"""The health check, replayed against the failures that prompted it.

Each of these went unnoticed at the time because the workflow run was green.
"""

import json
from datetime import date, datetime, timezone

from pipeline.collect_scholar import merged_meta, run_meta
from pipeline.discovery_status import next_status
from pipeline.find_scholar_profiles import batch_report, linked_authors, serpapi_candidates
from pipeline.health_check import (Finding, check_audit, check_job, check_dead_ids, check_discovery, check_openalex,
                                   check_openalex_figures, check_scholar, check_serpapi, check_site,
                                   issue_body, issue_title, plan_issue_actions, rolling_runs, run_counts)


def keys(findings):
    return [f.key for f in findings]


# ------------------------------------------------------------ Scholar, weekly

LEGACY = {  # the run records as they stood on 2026-11-13, before the counts became fields
    "2026-10-15": {"trigger": "manual", "notes": "serpapi collector; 774 ok, 1 failed, 264.2 min"},
    "2026-10-19": {"trigger": "manual", "notes": "serpapi collector; 37 ok, 0 failed, 49.1 min"},
    "2026-10-26": {"trigger": "manual", "notes": "serpapi collector; 50 ok, 0 failed, 66.8 min"},
    "2026-10-29": {"trigger": "manual", "notes": "serpapi collector; 1 ok, 0 failed, 0.0 min"},
    "2026-11-02": {"trigger": "manual", "notes": "serpapi collector; 49 ok, 1 failed, 67.0 min",
                   "stale_ids": [{"person_id": "130", "name": "Yiyi He", "scholar_id": "xoUOI-wAAAAJ"}]},
    "2026-11-05": {"trigger": "manual", "notes": "serpapi collector; 1 ok, 0 failed, 0.1 min"},
    "2026-11-09": {"trigger": "manual", "notes": "serpapi collector; 50 ok, 0 failed, 67.2 min"},
    "2026-11-13": {"trigger": "manual", "notes": "serpapi collector; 1 ok, 0 failed, 0.0 min"},
}


def test_old_run_records_are_read_from_their_notes():
    assert run_counts(LEGACY["2026-10-15"]) == (774, 1)
    assert run_counts({"notes": "OpenAlex collector; 1,050 ok, 0 failed, 3.1 min"}) == (1050, 0)
    assert run_counts({"notes": "Migrated from a workbook"}) is None
    # one-profile repairs are not collection runs
    assert sorted(rolling_runs(LEGACY)) == ["2026-10-15", "2026-10-19", "2026-10-26", "2026-11-02", "2026-11-09"]


def test_the_records_on_file_today_raise_nothing():
    assert check_scholar(LEGACY, date(2026, 11, 13)) == []


def test_a_dead_profile_id_does_not_make_a_run_short():
    upto = {d: m for d, m in LEGACY.items() if d <= "2026-11-02"}
    assert check_scholar(upto, date(2026, 11, 2), expect=True) == []


def test_a_run_cut_off_by_the_quota_is_raised():
    metas = {**LEGACY, "2026-11-16": run_meta("serpapi", 32, 18, 70.0, [], attempted=50, rolling=50)}
    [f] = check_scholar(metas, date(2026, 11, 16), expect=True)
    assert f.key == "scholar-short-run" and f.episode == "2026-11-16"
    assert "read 32 of 50" in f.title


def test_a_few_failures_in_an_otherwise_full_run_are_raised():
    metas = {"2026-11-16": run_meta("serpapi", 47, 3, 70.0, [], attempted=50, rolling=50)}
    assert keys(check_scholar(metas, date(2026, 11, 16))) == ["scholar-short-run"]
    metas = {"2026-11-16": run_meta("serpapi", 48, 2, 70.0, [], attempted=50, rolling=50)}
    assert check_scholar(metas, date(2026, 11, 16)) == []


def test_a_week_with_no_run_is_raised_the_day_after_it_was_due():
    # Monday 16 November: the daily check runs before the collection has, and waits.
    assert check_scholar(LEGACY, date(2026, 11, 16)) == []
    # Straight after the workflow, the run is expected to be there.
    [f] = check_scholar(LEGACY, date(2026, 11, 16), expect=True)
    assert f.key == "scholar-overdue" and f.episode == "2026-11-09"
    # And from Tuesday the daily check raises it by itself.
    assert keys(check_scholar(LEGACY, date(2026, 11, 17))) == ["scholar-overdue"]


def test_a_later_fetch_the_same_day_does_not_erase_the_collection_run():
    # 2026-10-12: 47 profiles read, then one more fetched that afternoon; the record said "1 ok".
    monday = run_meta("serpapi", 47, 3, 66.0, [("130", "Yiyi He", "xoUOI-wAAAAJ")], attempted=50, rolling=50)
    one = run_meta("serpapi", 1, 0, 0.0, [])
    kept = merged_meta(monday, one)
    assert kept["rolling"] == 50 and kept["ok"] == 47 and kept["stale_ids"] == monday["stale_ids"]
    assert "later the same day" in kept["notes"]
    # a second collection run that day replaces the first, and keeps its dead ids
    again = merged_meta(monday, run_meta("serpapi", 50, 0, 66.0, [], attempted=50, rolling=50))
    assert again["ok"] == 50 and again["stale_ids"] == monday["stale_ids"]
    assert merged_meta(None, one) == one


def test_dead_ids_are_a_notice_until_the_roster_moves_on():
    persons = [{"person_id": "130", "display_name": "Yiyi He", "google_scholar_id": "xoUOI-wAAAAJ"},
               {"person_id": "7", "display_name": "Someone Else", "google_scholar_id": "AAAAAAAAAAAJ"}]
    [f] = check_dead_ids(persons, LEGACY)
    assert f.key == "scholar-dead-ids" and not f.alarm and "Yiyi He" in f.detail and f.episode == "2026-11-02"
    persons[0]["google_scholar_id"] = "NEWNEWNEWNEW"        # the agent found the new profile
    assert check_dead_ids(persons, LEGACY) == []


# ---------------------------------------------------------- OpenAlex, monthly

def test_the_november_run_that_lost_a_third_of_the_roster_is_raised():
    # 2026-11-01: the anonymous budget ran out; the workflow run was green.
    metas = {"2026-10-01": {"trigger": "schedule", "notes": "OpenAlex collector; 1,043 ok, 0 failed, 2.9 min"},
             "2026-11-01": {"trigger": "schedule", "notes": "OpenAlex collector; 706 ok, 344 failed, 5.6 min"}}
    [f] = check_openalex(metas, date(2026, 11, 1), expect=True)
    assert f.key == "openalex-incomplete" and "344 of 1,050" in f.title


def test_a_run_that_stopped_early_is_raised_and_a_resume_clears_it():
    stopped = {"notes": "OpenAlex collector; 706 ok, 0 failed, 5.6 min; stopped at 706/1050: budget exhausted",
               "ok": 706, "failed": 0, "attempted": 1050, "matched": 1050, "collected": 706,
               "stopped": True, "targeted": False}
    assert keys(check_openalex({"2026-12-01": stopped}, date(2026, 12, 1))) == ["openalex-incomplete"]
    resumed = {"notes": "OpenAlex collector; 344 ok, 0 failed, 2.0 min", "ok": 344, "failed": 0, "attempted": 344,
               "matched": 1050, "collected": 1050, "stopped": False, "targeted": False}
    assert check_openalex({"2026-12-01": resumed}, date(2026, 12, 1)) == []


def test_a_clean_run_and_a_targeted_fetch_raise_nothing():
    metas = {"2026-11-02": {"trigger": "schedule", "notes": "OpenAlex collector; 1,050 ok, 0 failed, 12.4 min"},
             "2026-11-10": {"trigger": "manual", "notes": "OpenAlex collector (targeted); 0 ok, 2 failed, 0.1 min"}}
    assert check_openalex(metas, date(2026, 11, 13)) == []


def test_a_month_with_no_run_is_raised():
    metas = {"2026-11-02": {"trigger": "schedule", "notes": "OpenAlex collector; 1,050 ok, 0 failed, 12.4 min"}}
    assert check_openalex(metas, date(2026, 12, 1)) == []
    assert keys(check_openalex(metas, date(2026, 12, 1), expect=True)) == ["openalex-overdue"]
    assert keys(check_openalex(metas, date(2026, 12, 2))) == ["openalex-overdue"]


def oa_row(pid, total, h, works, ids, records=None):
    raw = {"openalex_author_id": ids[0], "openalex_author_ids": ids}
    if records:
        raw["records"] = records
    return {"person_id": pid, "total_citations": str(total), "h_index": str(h), "works_count": str(works),
            "raw_json": json.dumps(raw)}


def test_five_ids_that_are_one_record_are_raised():
    # 2026-11-01: OpenAlex had merged Ahmed El-Geneidy's five records; each id
    # returned the survivor's 14,964 and the site published the sum.
    same = {"cited_by_count": 14964, "works_count": 311, "h_index": 62}
    row = oa_row("1", 74820, 62, 1555, ["A1", "A2", "A3", "A4", "A5"],
                 [{"id": f"A{i}", **same} for i in range(1, 6)])
    [f] = check_openalex_figures("2026-11-01", [row], {"1": "Ahmed El-Geneidy"})
    assert f.key == "openalex-repeated-records"
    assert "Ahmed El-Geneidy: 5 records each with 14,964 citations" in f.detail and "74,820" in f.detail


def test_distinct_records_and_small_coincidences_are_not_raised():
    rows = [oa_row("1", 5200, 30, 140, ["A1", "A2"], [{"id": "A1", "cited_by_count": 5000, "works_count": 120, "h_index": 30},
                                                      {"id": "A2", "cited_by_count": 200, "works_count": 20, "h_index": 6}]),
            # two stray fragments with three citations each are not evidence of anything
            oa_row("2", 906, 12, 42, ["B1", "B2", "B3"], [{"id": "B1", "cited_by_count": 900, "works_count": 40, "h_index": 12},
                                                         {"id": "B2", "cited_by_count": 3, "works_count": 1, "h_index": 1},
                                                         {"id": "B3", "cited_by_count": 3, "works_count": 1, "h_index": 1}]),
            # two junior people who happen to share small figures
            oa_row("3", 12, 2, 3, ["C1"]), oa_row("4", 12, 2, 3, ["D1"])]
    assert check_openalex_figures("2026-11-02", rows, {}) == []


def test_one_record_held_by_two_people_is_raised():
    rows = [oa_row("1", 4100, 28, 90, ["A1"]), oa_row("2", 4100, 28, 90, ["A1"])]
    [f] = check_openalex_figures("2026-11-02", rows, {"1": "First Person", "2": "Second Person"})
    assert "record `A1` is counted for First Person, Second Person" in f.detail
    assert "identical figures" in f.detail


def test_matches_the_audit_withdrew_are_raised_for_a_fortnight():
    # 2026-11-01: seven settled matches were thrown out on the strength of inflated figures.
    persons = [{"person_id": "9", "display_name": "Evelyn Blumenberg",
                "notes": "un-accepted 2026-11-01: OpenAlex 41,200 citations against 9,800 held"},
               {"person_id": "10", "display_name": "Long Ago", "notes": "un-accepted 2026-09-15: OpenAlex ..."}]
    review = [{"person_id": "9", "display_name": "Evelyn Blumenberg", "notes": "un-accepted 2026-11-01: ..."}]
    [f] = check_audit(persons, review, date(2026, 11, 1))
    assert f.key == "openalex-audit-withdrawals" and f.episode == "2026-11-01"
    assert "withdrew 1 accepted OpenAlex match" in f.title and "Long Ago" not in f.detail
    assert check_audit(persons, review, date(2026, 11, 16)) == []


# ------------------------------------------------- discovery, in the weekly run

def at(day, hour=7):
    return datetime(2026, int(day[5:7]), int(day[8:10]), hour, 15, tzinfo=timezone.utc)


def test_a_batch_of_verification_pages_is_raised():
    # 2026-09-16: thirty searches, thirty pages with no linked author, thirty people marked "no profile".
    tally = {"accepted": 0, "pending": 0, "rejected": 0, "none": 30, "failed": 0}
    batch = batch_report(tally, no_links=30, consecutive_failures=0, searches=30, waiting=600)
    assert batch == {"searched": 30, "no_links": 30, "failed": 0, "found": 0, "blocked": False,
                     "searches": 30, "waiting": 600}
    status = next_status(None, now=at("2026-09-16"), batch=batch)
    assert status["outcome"] == "searched" and status["stalled_since"] is None and status["queued"] == 570
    [f] = check_discovery(status, 570, date(2026, 9, 16))
    assert f.key == "discovery-empty-batch" and "30 of 30" in f.title
    # a normal batch afterwards does not bury it
    ordinary = {"searched": 9, "no_links": 2, "failed": 0, "found": 3, "blocked": False, "waiting": 570}
    status = next_status(status, now=at("2026-09-21"), batch=ordinary)
    assert keys(check_discovery(status, 561, date(2026, 9, 21))) == ["discovery-empty-batch"]
    assert check_discovery(status, 561, date(2026, 9, 25)) == []          # a week on


def test_an_ordinary_batch_is_not_raised():
    # the first batch through SerpApi, 2026-11-16: four people, one page with no linked author
    batch = {"searched": 4, "no_links": 1, "failed": 0, "found": 1, "blocked": False, "searches": 8, "waiting": 90}
    status = next_status(None, now=at("2026-11-16"), batch=batch)
    assert status["queued"] == 86 and status["last_searched"] == "2026-11-16"
    assert check_discovery(status, 86, date(2026, 11, 16)) == []
    # nor a tiny one: one dead id, searched, no linked author
    one = {"searched": 1, "no_links": 1, "failed": 0, "found": 0, "blocked": False, "waiting": 1}
    status = next_status(None, now=at("2026-11-05"), batch=one)
    assert status["queued"] == 0 and check_discovery(status, 0, date(2026, 11, 5)) == []


def test_a_run_that_had_people_to_search_and_searched_none_is_raised_at_once():
    # 2026-11-13 to 15: six slots in a row, ninety people waiting, every search refused.
    blocked = {"searched": 0, "no_links": 0, "failed": 5, "found": 0, "blocked": True, "searches": 5, "waiting": 90}
    status = next_status(None, now=at("2026-11-16"), batch=blocked)
    assert status["outcome"] == "blocked" and status["stalled_since"] == "2026-11-16" and status["queued"] == 90
    [f] = check_discovery(status, 90, date(2026, 11, 16))
    assert f.key == "discovery-stalled" and f.episode == "2026-11-16" and "90 people" in f.title
    # a second week of it is the same occurrence, not a new alarm
    status = next_status(status, now=at("2026-11-23"), batch=blocked)
    [f] = check_discovery(status, 90, date(2026, 11, 23))
    assert f.episode == "2026-11-16"
    # one run that gets through clears it
    good = {"searched": 9, "no_links": 2, "failed": 0, "found": 3, "blocked": False, "searches": 12, "waiting": 90}
    status = next_status(status, now=at("2026-11-30"), batch=good)
    assert status["stalled_since"] is None and status["last_searched"] == "2026-11-30"
    assert check_discovery(status, 81, date(2026, 11, 30)) == []


def test_a_run_that_crashed_before_reporting_counts_as_stalled():
    status = next_status({"queued": 86, "stalled_since": None}, now=at("2026-11-23"), batch=None)
    assert status["outcome"] == "failed" and status["stalled_since"] == "2026-11-23" and status["queued"] == 86
    assert keys(check_discovery(status, 86, date(2026, 11, 23))) == ["discovery-stalled"]


def test_discovery_that_stops_running_is_raised_only_when_there_is_work():
    assert keys(check_discovery(None, 90, date(2026, 11, 13))) == ["discovery-silent"]
    assert check_discovery(None, 0, date(2026, 11, 13)) == []
    idle = next_status(None, now=at("2026-11-16"), batch={"searched": 0, "waiting": 0})
    assert idle["outcome"] == "idle" and idle["stalled_since"] is None
    assert check_discovery(idle, 0, date(2026, 12, 30)) == []            # nothing to do, nothing to say
    assert check_discovery(idle, 3, date(2026, 11, 23)) == []            # new hires wait for next Monday's run
    [f] = check_discovery(idle, 3, date(2026, 11, 25))                   # and it never came
    assert f.key == "discovery-silent" and f.episode == "2026-11-16"


def test_linked_authors_are_read_from_a_serpapi_response():
    response = {"organic_results": [
        {"publication_info": {"summary": "B Wali, AJ Khattak - 2020", "authors": [
            {"name": "B Wali", "author_id": "rhiTassAAAAJ"}, {"name": "AJ Khattak", "author_id": "h1zbRDIAAAAJ"}]}},
        {"publication_info": {"authors": [{"name": "B Wali", "author_id": "rhiTassAAAAJ"},
                                          {"name": "H Bozdogan"}]}},      # no profile, so no id
        {"publication_info": {"summary": "no linked authors at all"}}, {}]}
    assert linked_authors(response) == {"rhiTassAAAAJ": "B Wali", "h1zbRDIAAAAJ": "AJ Khattak"}
    assert linked_authors({"error": "Google hasn't returned any results for this query."}) == {}


def test_every_search_and_profile_read_is_counted_against_the_quota():
    from pipeline.collect_scholar import PermanentFailure
    person = {"display_name": "Behram Wali", "first_name": "Behram", "last_name": "Wali"}
    links = {"rhiTassAAAAJ": "B Wali", "h1zbRDIAAAAJ": "AJ Khattak", "DEADDEADDEAD": "B Wali"}

    def read(sid, key):
        if sid == "DEADDEADDEAD":
            raise PermanentFailure("empty profile")
        return {"name": "Behram Wali", "affiliation": "UC San Diego", "citedby": 2678, "email_domain": "@ucsd.edu"}

    meter = {"spent": 0}
    got, found = serpapi_candidates(person, "key", meter, 0, search=lambda name, key: links, read=read)
    assert got == links and [c["scholar_id"] for c in found] == ["rhiTassAAAAJ"]
    assert meter["spent"] == 3            # the search, and a read of each of the two profiles named "B Wali"


# ------------------------------------------------------------------- SerpApi

ACCOUNT = {"plan_name": "Free Plan", "searches_per_month": 250, "this_month_usage": 218, "total_searches_left": 32}


def test_too_few_searches_is_raised_on_the_eve_of_the_run_only():
    # Friday: the period may reset before Monday, so a low balance says nothing yet.
    assert check_serpapi(ACCOUNT, date(2026, 11, 13), "2026-11-09") == []
    [f] = check_serpapi(ACCOUNT, date(2026, 11, 15), "2026-11-09")       # Sunday
    assert f.key == "serpapi-quota" and f.episode == "2026-11-16" and "32 searches left" in f.title
    assert keys(check_serpapi(ACCOUNT, date(2026, 11, 16), "2026-11-09")) == ["serpapi-quota"]   # Monday, not yet run
    assert check_serpapi(ACCOUNT, date(2026, 11, 16), "2026-11-16") == []                         # Monday, after the run
    assert check_serpapi({**ACCOUNT, "total_searches_left": 250}, date(2026, 11, 15), "2026-11-09") == []


# ----------------------------------------------------------------- live site

def test_readings_committed_and_never_deployed_are_raised():
    # 2026-10-12: the weekly run committed 47 readings; a push by the workflow
    # token starts no other workflow, so the site stayed on the previous build.
    repo = {"google_scholar": "2026-10-12", "openalex": "2026-10-01", "pop": "2026-09-14"}
    live = {"google_scholar": "2026-10-11", "openalex": "2026-10-01", "pop": "2026-09-14"}
    assert check_site(live, repo, date(2026, 10, 12)) == []              # the deploy may still be running
    [f] = check_site(live, repo, date(2026, 10, 13))
    assert f.key == "site-stale" and f.episode == "2026-10-12" and "google_scholar" in f.title
    assert check_site(repo, repo, date(2026, 10, 13)) == []
    assert keys(check_site({}, repo, date(2026, 10, 13))) == ["site-stale"]   # a site with no status file at all


# ------------------------------------------- the weekly run of 2026-11-16

def test_a_job_that_was_cancelled_is_raised_when_the_records_look_complete():
    # The scheduled run was cancelled at its time limit and saved nothing. The
    # only record for the day was a three-profile trial that had gone well, so
    # every other rule passed.
    metas = {**LEGACY, "2026-11-16": run_meta("serpapi", 3, 0, 4.1, [], attempted=3, rolling=3)}
    today = date(2026, 11, 16)
    explained = bool(check_scholar(metas, today, expect=True))
    [f] = check_job("scholar", "cancelled", today, explained)
    assert f.key == "scholar-job" and f.episode == "2026-11-16" and "'cancelled'" in f.title
    assert check_job("scholar", "success", today, explained) == []
    assert check_job("", "cancelled", today, explained) == []        # the daily check ran no job
    assert check_job("scholar", "failure", today, explained=True) == []   # the short-run issue already says it


def test_the_collector_stops_when_the_account_is_out_of_searches(tmp_path, monkeypatch):
    # That run read eight profiles, then SerpApi answered 429 because the month's
    # searches were gone. It took that for the hourly limit and sat in cooldowns
    # for two hours, until the job was cancelled.
    from pipeline import collect_scholar as cs
    people = [{"person_id": str(n), "display_name": f"Person {n}", "google_scholar_id": f"id{n:02d}AAAAJ"}
              for n in range(1, 38)]
    calls, naps = [], []

    def fetch(sid, key):
        calls.append(sid)
        if len(calls) > 8:
            raise RuntimeError("HTTP Error 429: Too Many Requests")
        return {"scholar_id": sid, "name": "x", "citedby": 100, "hindex": 5}

    monkeypatch.setenv("SERPAPI_KEY", "not-a-key")
    monkeypatch.setattr(cs, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(cs, "load_people", lambda: (list(people[0]), people))
    monkeypatch.setattr(cs, "current_ids", lambda: {p["person_id"] for p in people})
    monkeypatch.setattr(cs, "fetch_serpapi", fetch)
    monkeypatch.setattr(cs, "searches_left", lambda key: 0)
    monkeypatch.setattr(cs, "id_is_dead", lambda sid: False)
    monkeypatch.setattr(cs.time, "sleep", naps.append)
    cs.main(["--via", "serpapi", "--budget", "37", "--date", "2026-11-16"])

    assert len(calls) == 9                           # one refusal, not eighteen
    assert max(naps) < 600                           # and no cooldown
    meta = json.loads((tmp_path / "google_scholar" / "2026-11-16.meta.json").read_text())
    assert (meta["ok"], meta["attempted"], meta["rolling"]) == (8, 37, 37)
    assert meta["stopped"] == "SerpApi has no searches left this period"
    rows = (tmp_path / "google_scholar" / "2026-11-16.csv").read_text().splitlines()
    assert len(rows) == 9                            # the eight readings are on file
    [f] = check_scholar({**LEGACY, "2026-11-16": meta}, date(2026, 11, 16), expect=True)
    assert f.key == "scholar-short-run" and "read 8 of 37" in f.title and "no searches left" in f.detail


# -------------------------------------------------------------------- issues

F = Finding("openalex-incomplete", "2026-11-01", "The OpenAlex collection of 2026-11-01 missed 344 of 1,050 people", "detail")
TODAY = date(2026, 11, 1)
EVALUATED = {"openalex-incomplete", "scholar-short-run", "scholar-dead-ids"}


def issue(n, f, state="OPEN", resolved=False):
    return {"number": n, "state": state, "title": issue_title(f), "body": issue_body(f, resolved)}


def does(actions):
    return [a["do"] for a in actions]


def test_a_new_problem_opens_one_issue_and_counts_as_new():
    [a] = plan_issue_actions([F], [], EVALUATED, TODAY)
    assert a["do"] == "create" and a["new"] and a["alarm"] and "key=openalex-incomplete episode=2026-11-01" in a["body"]


def test_the_same_problem_tomorrow_is_not_raised_again():
    assert plan_issue_actions([F], [issue(12, F)], EVALUATED, TODAY) == []
    # the evidence changed: the text is refreshed, quietly
    changed = Finding(F.key, F.episode, F.title, "more detail")
    [a] = plan_issue_actions([changed], [issue(12, F)], EVALUATED, TODAY)
    assert a["do"] == "edit" and not a.get("new")


def test_a_new_occurrence_while_the_issue_is_open_is_a_comment():
    december = Finding(F.key, "2026-12-01", "The OpenAlex collection of 2026-12-01 missed 80 of 1,060 people", "d")
    actions = plan_issue_actions([december], [issue(12, F)], EVALUATED, date(2026, 12, 1))
    assert does(actions) == ["comment", "edit"] and actions[0]["new"] and actions[0]["number"] == 12


def test_an_issue_closed_by_hand_stays_closed_for_that_occurrence():
    assert plan_issue_actions([F], [issue(12, F, "CLOSED")], EVALUATED, TODAY) == []
    december = Finding(F.key, "2026-12-01", "again", "d")
    assert does(plan_issue_actions([december], [issue(12, F, "CLOSED")], EVALUATED, TODAY)) == ["create"]


def test_a_problem_that_clears_closes_its_issue_and_one_that_returns_reopens_it():
    actions = plan_issue_actions([], [issue(12, F)], EVALUATED, TODAY)
    assert does(actions) == ["edit", "close"] and actions[0]["body"] == issue_body(F, resolved=True)
    actions = plan_issue_actions([F], [issue(12, F, "CLOSED", resolved=True)], EVALUATED, TODAY)
    assert does(actions) == ["reopen", "edit"] and actions[0]["new"]


def test_a_check_that_could_not_be_made_closes_nothing():
    quota = Finding("serpapi-quota", "2026-11-16", "SerpApi has 32 searches left", "d")
    assert plan_issue_actions([], [issue(13, quota)], EVALUATED, TODAY) == []
    assert does(plan_issue_actions([], [issue(13, quota)], EVALUATED | {"serpapi-quota"}, TODAY)) == ["edit", "close"]


def test_issues_that_are_not_ours_are_left_alone():
    stranger = {"number": 5, "state": "OPEN", "title": "Typo on the methods page", "body": "see line 3"}
    assert plan_issue_actions([], [stranger], EVALUATED, TODAY) == []


def test_a_notice_opens_an_issue_without_being_an_alarm():
    notice = Finding("scholar-dead-ids", "2026-11-02", "1 Scholar profile id no longer resolves", "d", alarm=False)
    [a] = plan_issue_actions([notice], [], EVALUATED, TODAY)
    assert a["do"] == "create" and a["title"].startswith("Notice: ") and not a["alarm"]
