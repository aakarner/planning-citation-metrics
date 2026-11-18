"""Which roster Scholar ids still need their first fetch.

Discovery writes an id and stops; without this rule the person keeps their
Publish or Perish number until the quarterly collection while the homepage
announces the find. Arnab Chakraborty sat like that for a week.
"""

import json

from pipeline.collect_scholar import ids_missing_readings


def snap(pid, sid):
    return {"person_id": pid, "raw_json": json.dumps({"scholar_id": sid}) if sid != "legacy" else "{}"}


PEOPLE = [{"person_id": "1", "google_scholar_id": "NEW1"},     # found, never fetched
          {"person_id": "2", "google_scholar_id": "OLD2"},     # fetched under this id
          {"person_id": "3", "google_scholar_id": "ANY3"},     # only a legacy row with no id stored
          {"person_id": "4", "google_scholar_id": "REAL4"},    # id changed: rows exist for the namesake only
          {"person_id": "5", "google_scholar_id": ""}]         # no id at all


def test_only_ids_without_a_reading_are_selected():
    rows = [snap("2", "OLD2"), snap("3", "legacy"), snap("4", "WRONG4")]
    assert ids_missing_readings(PEOPLE, rows) == {"NEW1", "REAL4"}


def test_a_legacy_row_without_an_id_counts_for_whatever_id_is_held():
    assert ids_missing_readings([PEOPLE[2]], [snap("3", "legacy")]) == set()


def test_nothing_to_fetch_when_every_id_has_a_row():
    assert ids_missing_readings([PEOPLE[1]], [snap("2", "OLD2")]) == set()


def test_the_weekly_budget_goes_to_the_oldest_readings_of_current_people():
    from pipeline.collect_scholar import oldest_first
    people = [{"person_id": str(i), "google_scholar_id": f"id{i}"} for i in range(1, 6)]
    snaps = [{"person_id": "1", "collected_at": "2026-03-01", "total_citations": "10"},
             {"person_id": "1", "collected_at": "2026-09-15", "total_citations": "12"},
             {"person_id": "2", "collected_at": "2026-03-01", "total_citations": "10"},
             {"person_id": "3", "collected_at": "2026-10-10", "total_citations": "10"},
             {"person_id": "4", "collected_at": "2026-10-10", "total_citations": ""}]    # a failed fetch is no reading
    picked = oldest_first(people, snaps, current={"1", "2", "3", "4"}, budget=3)
    assert [p["person_id"] for p in picked] == ["4", "2", "1"]     # never read, then March, then September; 5 has left



def test_a_workbook_row_counts_only_for_the_profile_its_url_names():
    # Yan Song, 2026-10-12: the workbook row pointed at a namesake's id; once the roster held
    # her real profile, that row must not count as a reading of it.
    import json
    person = {"person_id": "516", "google_scholar_id": "VOpvlkkAAAAJ"}
    workbook = {"person_id": "516", "raw_json": json.dumps({"workbook_row": {"GoogleScholar": "https://scholar.google.com/citations?hl=en&user=ky5sX-0AAAAJ"}})}
    assert ids_missing_readings([person], [workbook]) == {"VOpvlkkAAAAJ"}
    read = {"person_id": "516", "raw_json": json.dumps({"scholar_id": "VOpvlkkAAAAJ"})}
    assert ids_missing_readings([person], [workbook, read]) == set()


def test_the_run_record_names_the_ids_that_stopped_resolving():
    """The stderr listing of dead ids is gone once the Actions log scrolls away;
    the meta file is what the discovery pass reads."""
    from pipeline.collect_scholar import run_meta
    meta = run_meta("serpapi", 49, 1, 67.04, [("130", "Yiyi He", "xoUOI-wAAAAJ")])
    assert meta["notes"] == "serpapi collector; 49 ok, 1 failed, 67.0 min"
    assert meta["stale_ids"] == [{"person_id": "130", "name": "Yiyi He", "scholar_id": "xoUOI-wAAAAJ"}]
    assert "stale_ids" not in run_meta("serpapi", 50, 0, 60.0, [])


# ------------------------------------------------- the collection loop itself

def people_and_run(monkeypatch, tmp_path, n, fetch, left=None):
    """Run the collector over n made-up people with `fetch` standing in for SerpApi."""
    from pipeline import collect_scholar as cs
    people = [{"person_id": str(i), "display_name": f"Person {i}", "google_scholar_id": f"id{i:02d}AAAAJ"}
              for i in range(1, n + 1)]
    naps = []
    monkeypatch.setenv("SERPAPI_KEY", "not-a-key")
    monkeypatch.setattr(cs, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(cs, "load_people", lambda: (list(people[0]), people))
    monkeypatch.setattr(cs, "current_ids", lambda: {p["person_id"] for p in people})
    monkeypatch.setattr(cs, "fetch_serpapi", fetch)
    monkeypatch.setattr(cs, "searches_left", lambda key: left)
    monkeypatch.setattr(cs, "id_is_dead", lambda sid: (_ for _ in ()).throw(AssertionError("asked Scholar directly")))
    monkeypatch.setattr(cs.time, "sleep", naps.append)
    return cs, naps


def reading(sid):
    return {"scholar_id": sid, "name": "x", "citedby": 100, "hindex": 5}


def test_people_whose_fetch_failed_are_tried_once_more_at_the_end(tmp_path, monkeypatch):
    """The retry pass was checked for one step after the loop had ended, so it
    never ran: anyone who failed during a rate limit waited months for the
    rolling run to come round again."""
    calls = []

    def fetch(sid, key, fresh=False):
        calls.append(sid)
        if sid in ("id03AAAAJ", "id04AAAAJ", "id12AAAAJ") and calls.count(sid) == 1:
            raise RuntimeError("HTTP Error 429: Too Many Requests")
        if sid == "id07AAAAJ":
            raise RuntimeError("HTTP Error 500")                # fails both times
        return reading(sid)

    cs, _ = people_and_run(monkeypatch, tmp_path, 12, fetch)
    cs.main(["--budget", "12", "--date", "2026-11-23"])
    assert calls[12:] == ["id03AAAAJ", "id04AAAAJ", "id07AAAAJ", "id12AAAAJ"]     # once more each, in order
    meta = json.loads((tmp_path / "google_scholar" / "2026-11-23.meta.json").read_text())
    assert (meta["ok"], meta["failed"], meta["attempted"]) == (11, 1, 12)        # a retry that worked is not a failure
    rows = (tmp_path / "google_scholar" / "2026-11-23.csv").read_text().splitlines()
    assert len(rows) == 12                                                        # header and eleven readings


def test_an_id_is_reported_dead_only_when_a_second_uncached_look_agrees(tmp_path, monkeypatch):
    """Discovery clears a dead id for good when it finds no replacement, so one
    empty answer from SerpApi must not be enough to call an id dead."""
    from pipeline.collect_scholar import PermanentFailure
    looks = []

    def fetch(sid, key, fresh=False):
        looks.append((sid, fresh))
        if sid == "id02AAAAJ" and not fresh:
            raise PermanentFailure("empty profile")             # a blip: the second look finds the profile
        if sid == "id03AAAAJ":
            raise PermanentFailure("empty profile")             # really gone
        return reading(sid)

    cs, _ = people_and_run(monkeypatch, tmp_path, 4, fetch)
    cs.main(["--budget", "4", "--date", "2026-11-23"])
    assert ("id02AAAAJ", True) in looks and ("id03AAAAJ", True) in looks          # both were asked again, uncached
    meta = json.loads((tmp_path / "google_scholar" / "2026-11-23.meta.json").read_text())
    assert [e["scholar_id"] for e in meta["stale_ids"]] == ["id03AAAAJ"]
    assert (meta["ok"], meta["failed"]) == (3, 1)
    assert looks.count(("id03AAAAJ", False)) == 1                                 # a dead id is not retried at the end


def test_the_weekly_run_is_cut_to_the_searches_the_account_has_left(tmp_path, monkeypatch):
    """Fifty searches on five Mondays is the whole free tier. The fifth run used to
    ask for more than was left, fail part-way and raise a short-run alarm."""
    from pipeline.collect_scholar import weekly_plan
    assert weekly_plan(50, 12, 250) == (50, 12)
    assert weekly_plan(50, 12, None) == (50, 12)          # SerpApi would not say: plan as asked
    assert weekly_plan(50, 30, 250) == (50, 30)           # a run started by hand to work down the queue gets what it asked for
    assert weekly_plan(20, 12, 250) == (20, 12)
    assert weekly_plan(50, 12, 30) == (30, 7)             # cut: discovery keeps its share
    assert weekly_plan(50, 12, 10) == (10, 2)
    assert weekly_plan(50, 12, 9) == (9, 0)               # too few to share: discovery can overshoot by four
    assert weekly_plan(50, 12, 0) == (0, 0)

    calls = []
    cs, _ = people_and_run(monkeypatch, tmp_path, 40, lambda sid, key, fresh=False: calls.append(sid) or reading(sid),
                           left=18)
    cs.main(["--budget", "38", "--date", "2026-11-30"])
    meta = json.loads((tmp_path / "google_scholar" / "2026-11-30.meta.json").read_text())
    # one search is held back: any profile may turn out to need a second look
    assert len(calls) == 17 and (meta["ok"], meta["attempted"], meta["rolling"], meta["budget_asked"]) == (17, 17, 18, 38)
    assert "stopped" not in meta                           # a smaller run, not a failed one

    import pytest
    cs, _ = people_and_run(monkeypatch, tmp_path, 40, lambda sid, key, fresh=False: calls.append(sid), left=0)
    with pytest.raises(SystemExit, match="no searches left"):
        cs.main(["--budget", "38", "--date", "2026-12-07"])
    assert len(calls) == 17                                # and it asked SerpApi for nothing


def test_a_cut_run_with_a_dead_id_in_it_still_ends_cleanly(tmp_path, monkeypatch):
    """The budget counts profiles; the account counts searches, and a dead id costs
    two. Five searches left and one dead id used to mean a refusal on the last
    profile, a run marked stopped, and a short-run alarm for a run that was only small."""
    from pipeline.collect_scholar import PermanentFailure
    from pipeline.health_check import check_scholar
    from datetime import date
    calls = []

    def fetch(sid, key, fresh=False):
        calls.append(sid)
        if len(calls) > 5:
            raise RuntimeError("HTTP Error 429: Too Many Requests")        # the account is empty
        if sid == "id02AAAAJ":
            raise PermanentFailure("empty profile")
        return reading(sid)

    cs, _ = people_and_run(monkeypatch, tmp_path, 12, fetch, left=5)
    cs.main(["--budget", "12", "--date", "2026-11-30"])
    assert len(calls) == 4                                 # 1, then 2 twice, then 3: one search left, not enough for a profile
    meta = json.loads((tmp_path / "google_scholar" / "2026-11-30.meta.json").read_text())
    assert (meta["ok"], meta["failed"], meta["attempted"]) == (2, 1, 3) and "stopped" not in meta
    assert [e["scholar_id"] for e in meta["stale_ids"]] == ["id02AAAAJ"]
    assert check_scholar({"2026-11-30": meta}, date(2026, 11, 30), expect=True) == []


def test_a_missing_key_never_falls_back_to_scraping(monkeypatch):
    # The default backend used to become direct scraping when SERPAPI_KEY was unset.
    import pytest
    from pipeline import collect_scholar as cs, find_scholar_profiles as fs
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    for main in (cs.main, fs.main):
        with pytest.raises(SystemExit, match="SERPAPI_KEY is not set"):
            main([])


def test_clearing_an_id_by_hand_checks_through_serpapi_and_needs_two_empty_answers():
    from pipeline.clear_dead_ids import profile_is_gone
    from pipeline.collect_scholar import PermanentFailure

    def gone(sid, key, fresh=False):
        raise PermanentFailure("empty profile")

    def blip(sid, key, fresh=False):
        if not fresh:
            raise PermanentFailure("empty profile")
        return reading(sid)

    def refused(sid, key, fresh=False):
        raise RuntimeError("HTTP Error 429")

    assert profile_is_gone("DEADDEADDEAD", "key", pause=0, fetch=gone)
    assert not profile_is_gone("LIVELIVELIVE", "key", pause=0, fetch=blip)
    assert not profile_is_gone("LIVELIVELIVE", "key", pause=0, fetch=refused)     # could not check: leave it alone
