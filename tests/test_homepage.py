"""The homepage's computed blocks: medians by rank, and what changed since the
last collection. Both are shown to every visitor first, so they are pinned."""

import sqlite3
from pathlib import Path

from pipeline.build_site import recent_changes

ROOT = Path(__file__).resolve().parent.parent


def aff(aid, pid, dept, rank, start="", end="", dept_name=None, source=""):
    return {"affiliation_id": aid, "person_id": pid, "department_id": dept, "dept": dept_name or f"D{dept}",
            "rank": rank, "appointment_type": "regular", "is_primary": 1, "start_date": start, "end_date": end,
            "source": source}


def test_a_move_a_promotion_and_a_departure_are_told_apart():
    affs = [
        aff(1, "a", 1, "assistant", end="2026-09-15"), aff(2, "a", 2, "assistant", start="2026-09-15"),   # move
        aff(3, "b", 1, "associate", end="2026-09-15"), aff(4, "b", 1, "full", start="2026-09-15"),        # promotion
        aff(5, "c", 1, "full", end="2026-09-15"),                                                          # departed
        aff(6, "d", 1, "full"),                                                                            # unchanged
        aff(7, "e", 1, "assistant", end="2026-03-01"), aff(8, "e", 2, "assistant", start="2026-03-01"),   # too old
    ]
    ch = recent_changes(affs, "2026-09-14")
    assert ch["moves"] == [("a", "D1", "D2")]
    assert ch["promos"] == [("b", "D1", "associate", "full", "profile")]
    assert ch["departed"] == 1


def test_nothing_is_narrated_without_a_window():
    assert recent_changes([aff(1, "a", 1, "full", end="2026-09-15")], None) == {"moves": [], "promos": [], "departed": 0}


def test_a_corrected_row_is_not_double_counted():
    # A row closed on one day with the replacement dated another (a hand
    # correction) is not a move or a promotion we can narrate.
    affs = [aff(1, "a", 1, "assistant", end="2026-09-15"), aff(2, "a", 2, "assistant", start="2026-09-16")]
    ch = recent_changes(affs, "2026-09-14")
    assert ch == {"moves": [], "promos": [], "departed": 0}


def test_undoing_a_mistaken_move_is_not_narrated_as_a_move():
    # Moved to D2 on a stale profile, put back in D1 a month later: the second
    # closing is a correction, not a move D2 -> D1. The departure count and a
    # genuine onward move are untouched.
    affs = [aff(1, "a", 1, "full", end="2026-09-15"),
            aff(2, "a", 2, "full", start="2026-09-15", end="2026-10-15"),
            aff(3, "a", 1, "full", start="2026-10-15"),
            aff(4, "b", 1, "full", end="2026-09-15"),
            aff(5, "b", 2, "full", start="2026-09-15", end="2026-10-15"),
            aff(6, "b", 3, "full", start="2026-10-15")]
    ch = recent_changes(affs, "2026-10-01")
    assert ch == {"moves": [("b", "D2", "D3")], "promos": [], "departed": 0}


def test_rank_benchmarks_view_reports_median_and_75th_percentile():
    con = sqlite3.connect(":memory:")
    con.executescript((ROOT / "sql" / "schema.sql").read_text())
    con.executescript((ROOT / "sql" / "views.sql").read_text())
    con.execute("INSERT INTO department (department_id, short_name, name, university, country) VALUES (1,'D','D','D','US')")
    con.execute("INSERT INTO collection_run (run_id, source, started_at) VALUES (1,'google_scholar','2026-09-15')")
    cites = [100, 200, 300, 400, 500]                       # odd n: median 300, p75 400
    for i, c in enumerate(cites, 1):
        con.execute("INSERT INTO person (person_id, first_name, last_name, display_name, google_scholar_id) VALUES (?,?,?,?,?)",
                    (i, "p", str(i), f"p{i}", f"id{i}"))
        con.execute("INSERT INTO affiliation (person_id, department_id, rank, appointment_type, is_primary) VALUES (?,1,'assistant','regular',1)", (i,))
        con.execute("INSERT INTO metric_snapshot (run_id, person_id, source, collected_at, total_citations, h_index) VALUES (1,?,'google_scholar','2026-09-15',?,?)",
                    (i, c, c // 50))
    row = con.execute("SELECT * FROM v_rank_benchmarks WHERE rank='assistant'").fetchone()
    assert row[1] == 5 and row[2] == 300 and row[3] == 400 and row[4] == 6


def test_the_change_lists_render_their_rows_and_badges_agree(monkeypatch):
    """The live page shipped with badges reading 6 / 13 / 47 over empty lists:
    the people index was keyed by int and looked up by str. Render the page
    with a few synthetic people and check every badge equals the number of
    rows under it, and that the rows name the right people."""
    import re
    from pipeline import build_site
    from pipeline.build_site import index_page, uniq_slugs

    def person(pid, name, dept, rank="assistant", cites=100):
        return {"person_id": pid, "display_name": name, "department": dept, "department_id": 1,
                "rank": rank, "total_citations": cites, "h_index": 5}
    people = [person(1, "Ada Lovelace", "D2"), person(2, "Grace Hopper", "D1", "full"), person(3, "Mary Somerville", "D1")]
    depts = [{"department_id": 1, "short_name": "D1", "n_faculty": 3, "median_citations": 100, "mean_citations": 100,
              "university": "D1", "total_citations": 300},
             {"department_id": 2, "short_name": "D2", "n_faculty": 3, "median_citations": 90, "mean_citations": 90,
              "university": "D2", "total_citations": 270}]
    affs = [aff(1, 1, 1, "assistant", end="2026-09-15"), aff(2, 1, 2, "assistant", start="2026-09-15"),   # Ada moved
            aff(3, 2, 1, "associate", end="2026-09-15"), aff(4, 2, 1, "full", start="2026-09-15"),       # Grace promoted
            aff(5, 3, 1, "assistant")]
    data = {"people": people, "depts": depts, "asof": {"google_scholar": "2026-09-16", "openalex": "2026-09-15",
            "pop": "2026-02-20"}, "mix": {"google_scholar": 2, "openalex": 1, "pop": 0},
            "bench": {"assistant": {"n": 2, "median_citations": 100, "p75_citations": 100, "median_h_index": 5}},
            "affs": affs, "today": "2026-09-16"}
    # discovery finds normally come from the review CSV; give the page one for Mary
    monkeypatch.setattr(build_site, "discovery_finds",
                        lambda since: [{"person_id": "3", "department": "D1", "reviewed_at": "2026-09-16"}])
    monkeypatch.setattr(build_site, "SHOW_PROMOTIONS", True)
    html = index_page(data, uniq_slugs(people, lambda p: p["display_name"]),
                      uniq_slugs(depts, lambda d: d["short_name"]), "")

    badges = [int(n) for n in re.findall(r'class="count">(\d+)<', html)]
    lists = re.findall(r'<ul class="plain[^"]*">(.*?)</ul>', html, flags=re.S)
    assert badges == [1, 1, 1]
    assert [l.count("<li>") for l in lists] == [1, 1, 1]
    assert "Mary Somerville" in lists[0] and "Ada Lovelace" in lists[1] and "Grace Hopper" in lists[2]
    assert "D1 &rarr; <b>D2</b>" in lists[1] and "associate &rarr; <b>full</b>" in lists[2]


def test_each_person_is_compared_with_their_own_previous_reading(tmp_path, monkeypatch):
    """Collection is rolling (about fifty profiles a week), so there is no run
    to compare with the one before; each person's latest reading is set against
    their own previous one, whichever files they sit in."""
    import csv as _csv
    from pipeline import detect_changes
    d = tmp_path / "google_scholar"; d.mkdir()
    for day, readings in [("2026-03-01", {"1": 100, "2": 50}), ("2026-09-15", {"1": 120}),
                          ("2026-10-10", {"1": 130, "3": ""})]:                       # 3's fetch failed
        with (d / f"{day}.csv").open("w", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=["person_id", "collected_at", "total_citations", "raw_json"]); w.writeheader()
            for pid, c in readings.items():
                w.writerow({"person_id": pid, "collected_at": day, "total_citations": c, "raw_json": "{}"})
    monkeypatch.setattr(detect_changes, "SNAPSHOT_DIR", tmp_path)
    latest, prev = detect_changes.latest_two("google_scholar")
    assert {r["person_id"]: r["total_citations"] for r in latest} == {"1": "130", "2": "50"}
    assert {r["person_id"]: r["total_citations"] for r in prev} == {"1": "120"}       # 2 has only ever been read once
def test_promotions_say_where_the_rank_was_read():
    affs = [aff(1, "a", 1, "assistant", end="2026-09-27"),
            aff(2, "a", 1, "associate", start="2026-09-27", source="rank verified 2026-09-27; department page https://x/faculty"),
            aff(3, "b", 1, "associate", end="2026-09-15"),
            aff(4, "b", 1, "full", start="2026-09-15", source="change from Scholar profile 2026-09-15: ...")]
    ch = recent_changes(affs, "2026-09-14")
    assert sorted(p[4] for p in ch["promos"]) == ["profile", "website"]


# --- Rising: citation gains since the baseline, within rank -------------------


def test_rising_estimates_the_twelve_months_before_each_reading():
    import json
    from pipeline.build_site import rising_from_readings, rising

    def reading(pid, rank, total, cpy, when="2026-09-15"):
        return {"person_id": pid, "display_name": f"p{pid}", "rank": rank, "department": "D",
                "total_citations": total, "collected_at": when, "raw_json": json.dumps({"cites_per_year": cpy})}
    # 15 September: 257 of 365 days gone, so 2026 to date plus 29.6% of 2025 (Yan Song's real profile)
    rows = rising_from_readings([
        reading(1, "full", 13175, {"2024": 1321, "2025": 1381, "2026": 1029}),
        reading(2, "full", 900, {"2025": 400, "2026": 300}),
        reading(3, "assistant", 80, {"2025": 40, "2026": 30}),              # too small a profile
        reading(4, "assistant", 500, {}),                                   # no per-year graph: left out
        reading(5, "assistant", 500, {"2026": 120, "2027": 10}, when="2027-01-10"),   # early January: mostly last year
    ])
    by = {r["person_id"]: r for r in rows}
    assert by[1]["gain"] == 1029 + round(1381 * (1 - 257 / 365)) == 1438
    assert by[2]["gain"] == 300 + round(400 * (1 - 257 / 365))
    assert 4 not in by
    assert by[5]["gain"] == 10 + round(120 * (1 - 9 / 365)) == 127
    out = rising(rows)
    assert [r["person_id"] for r in out["full"]] == [1, 2]
    assert [r["person_id"] for r in out["assistant"]] == [5]               # 3 is under the 100-citation floor


def test_the_rising_block_renders_its_columns():
    import json
    from pipeline.build_site import index_page, uniq_slugs

    def person(pid, name, rank="assistant"):
        return {"person_id": pid, "display_name": name, "department": "D1", "department_id": 1,
                "rank": rank, "total_citations": 500, "h_index": 5}
    people = [person(1, "Ada Lovelace"), person(2, "Grace Hopper", "full")]
    depts = [{"department_id": 1, "short_name": "D1", "n_faculty": 3, "median_citations": 100, "mean_citations": 100,
              "university": "D1", "total_citations": 300}]
    rising_rows = [{"person_id": 1, "display_name": "Ada Lovelace", "rank": "assistant", "department": "D1",
                    "total_citations": 400, "gain": 100, "window_end": "2026-09-15"},
                   {"person_id": 2, "display_name": "Grace Hopper", "rank": "full", "department": "D1",
                    "total_citations": 50, "gain": 40, "window_end": "2026-09-15"}]          # under the floor
    data = {"people": people, "depts": depts, "asof": {"google_scholar": "2026-09-16", "openalex": "2026-09-15",
            "pop": "2026-02-20"}, "mix": {"google_scholar": 2}, "bench": {}, "affs": [aff(1, 1, 1, "assistant")],
            "today": "2026-09-16", "rising": rising_rows}
    html = index_page(data, uniq_slugs(people, lambda p: p["display_name"]),
                      uniq_slugs(depts, lambda d: d["short_name"]), "")
    assert "Most cited in the past year" in html
    block = html.split("Most cited in the past year", 1)[1].split("<h2>", 1)[0]
    assert "Ada Lovelace" in block and "+100" in block and "25%" in block
    assert "Grace Hopper" not in block and "Full professors" not in block

    data["rising"] = []
    assert "Most cited in the past year" not in index_page(data, uniq_slugs(people, lambda p: p["display_name"]),
                                                uniq_slugs(depts, lambda d: d["short_name"]), "")



def test_a_namesakes_readings_leave_the_series_once_the_profile_is_replaced():
    import json
    from pipeline.build_site import reading_is_theirs
    workbook = json.dumps({"workbook_row": {"GoogleScholar": "https://scholar.google.com/citations?hl=en&user=ky5sX-0AAAAJ"}})
    read = json.dumps({"scholar_id": "ky5sX-0AAAAJ", "citedby": 23933})
    assert not reading_is_theirs("google_scholar", workbook, "VOpvlkkAAAAJ")     # Tom's row came from the namesake
    assert not reading_is_theirs("google_scholar", read, "VOpvlkkAAAAJ")
    assert reading_is_theirs("google_scholar", read, "ky5sX-0AAAAJ")             # while that profile was the one held
    assert reading_is_theirs("google_scholar", json.dumps({"citedby": 5}), "VOpvlkkAAAAJ")   # no id stored: shown
    assert reading_is_theirs("google_scholar", read, None)                       # no profile held: history still shown
    assert reading_is_theirs("openalex", json.dumps({"records": []}), "VOpvlkkAAAAJ")


def test_the_promotions_fold_is_hidden_while_the_record_catches_up(monkeypatch):
    """2026-10-12: 136 'promotions' in the window, nearly all ranks Tom's sheet had
    missed; Alex: hide it for now. The flag keeps the code and the data."""
    from pipeline import build_site
    from pipeline.build_site import index_page, uniq_slugs
    people = [{"person_id": 2, "display_name": "Grace Hopper", "department": "D1", "department_id": 1,
               "rank": "full", "total_citations": 100, "h_index": 5}]
    depts = [{"department_id": 1, "short_name": "D1", "n_faculty": 3, "median_citations": 100, "mean_citations": 100,
              "university": "D1", "total_citations": 300}]
    affs = [aff(3, 2, 1, "associate", end="2026-09-15"), aff(4, 2, 1, "full", start="2026-09-15")]
    data = {"people": people, "depts": depts, "asof": {"google_scholar": "2026-09-16", "openalex": "2026-09-15",
            "pop": "2026-02-20"}, "mix": {"google_scholar": 1}, "bench": {}, "affs": affs, "today": "2026-09-16"}
    monkeypatch.setattr(build_site, "discovery_finds", lambda since: [])
    monkeypatch.setattr(build_site, "SHOW_PROMOTIONS", False)
    html = index_page(data, uniq_slugs(people, lambda p: p["display_name"]), uniq_slugs(depts, lambda d: d["short_name"]), "")
    assert "Promotions recorded" not in html and "Grace Hopper" not in html.split("New to the record", 1)[1].split("<h2>", 1)[0]
    monkeypatch.setattr(build_site, "SHOW_PROMOTIONS", True)
    html = index_page(data, uniq_slugs(people, lambda p: p["display_name"]), uniq_slugs(depts, lambda d: d["short_name"]), "")
    assert "Promotions recorded" in html and "associate &rarr; <b>full</b>" in html



def test_the_openalex_card_puts_a_persons_share_beside_the_typical_one():
    from pipeline.build_site import oa_share, person_page, uniq_slugs
    assert oa_share(136, 640) == 136 / 640 and oa_share(0, 640) is None and oa_share(50, None) is None
    p = {"person_id": 1, "display_name": "AJ Kim", "department": "D1", "department_id": 1, "rank": "associate",
         "total_citations": 640, "h_index": 14, "source": "google_scholar", "is_fallback": 0, "collected_at": "2026-09-15",
         "google_scholar_id": "x", "openalex_author_id": "A1", "orcid": None, "phd_year": None, "phd_institution": None,
         "interests": None, "researchgate_url": None, "linkedin_url": None, "personal_url": None, "university": "SDSU",
         "country": "US", "dept_url": None, "acsp_member": "full", "pct_citations_all": 0.5, "pct_citations_rank": 0.5,
         "pct_h_all": 0.5, "pct_h_rank": 0.5, "years_since_phd": None, "citations_per_year": None}
    oa = {1: {"total_citations": 136, "h_index": 3, "works_count": 14}}
    html = person_page(p, uniq_slugs([p], lambda x: x["display_name"]), "d1", oa, {}, "../", {"n": 729, "typical": 0.587})
    assert "Here OpenAlex counts 21% of the Scholar total" in html and "typical share is 59%" in html and "729 faculty" in html
    html = person_page(p, uniq_slugs([p], lambda x: x["display_name"]), "d1", oa, {}, "../")      # no site-wide figure: no sentence
    assert "typical share" not in html
