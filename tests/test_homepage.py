"""The homepage's computed blocks: medians by rank, and what changed since the
last collection. Both are shown to every visitor first, so they are pinned."""

import sqlite3
from pathlib import Path

from pipeline.build_site import collection_window, recent_changes

ROOT = Path(__file__).resolve().parent.parent


def aff(aid, pid, dept, rank, start="", end="", dept_name=None):
    return {"affiliation_id": aid, "person_id": pid, "department_id": dept, "dept": dept_name or f"D{dept}",
            "rank": rank, "appointment_type": "regular", "is_primary": 1, "start_date": start, "end_date": end}


def test_the_collection_window_merges_a_run_spread_over_days():
    assert collection_window(["2026-03-01", "2026-09-14", "2026-09-15", "2026-09-16"]) == "2026-09-14"
    assert collection_window(["2026-03-01"]) == "2026-03-01"
    assert collection_window([]) is None


def test_targeted_fetches_a_week_later_join_the_run_instead_of_splitting_it():
    # The homepage showed zero promotions once fetches on the 22nd and 23rd made
    # the 14th and 15th fall more than seven days behind the newest file.
    dates = ["2026-03-01", "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-22", "2026-09-23"]
    assert collection_window(dates) == "2026-09-14"
    assert collection_window(["2026-03-01", "2026-06-10", "2026-09-14"]) == "2026-09-14"   # a real gap still separates runs


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
    assert ch["promos"] == [("b", "D1", "associate", "full")]
    assert ch["departed"] == 1


def test_nothing_is_narrated_without_a_window():
    assert recent_changes([aff(1, "a", 1, "full", end="2026-09-15")], None) == {"moves": [], "promos": [], "departed": 0}


def test_a_corrected_row_is_not_double_counted():
    # A row closed on one day with the replacement dated another (a hand
    # correction) is not a move or a promotion we can narrate.
    affs = [aff(1, "a", 1, "assistant", end="2026-09-15"), aff(2, "a", 2, "assistant", start="2026-09-16")]
    ch = recent_changes(affs, "2026-09-14")
    assert ch == {"moves": [], "promos": [], "departed": 0}


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
            "affs": affs, "gs_dates": ["2026-03-01", "2026-09-15", "2026-09-16"]}
    # discovery finds normally come from the review CSV; give the page one for Mary
    monkeypatch.setattr(build_site, "discovery_finds",
                        lambda since: [{"person_id": "3", "department": "D1", "reviewed_at": "2026-09-16"}])
    html = index_page(data, uniq_slugs(people, lambda p: p["display_name"]),
                      uniq_slugs(depts, lambda d: d["short_name"]), "")

    badges = [int(n) for n in re.findall(r'class="count">(\d+)<', html)]
    lists = re.findall(r'<ul class="plain[^"]*">(.*?)</ul>', html, flags=re.S)
    assert badges == [1, 1, 1]
    assert [l.count("<li>") for l in lists] == [1, 1, 1]
    assert "Mary Somerville" in lists[0] and "Ada Lovelace" in lists[1] and "Grace Hopper" in lists[2]
    assert "D1 &rarr; <b>D2</b>" in lists[1] and "associate &rarr; <b>full</b>" in lists[2]


def test_snapshot_files_chain_into_one_run(tmp_path, monkeypatch):
    """A two-person fetch eight days after a quarterly run belongs to that run.
    Anchoring the window on the newest file split it and reported the first
    day's 89 people as 'missing from the latest run'."""
    import csv as _csv
    from pipeline import detect_changes
    d = tmp_path / "google_scholar"; d.mkdir()
    for day, pids in [("2026-03-01", ["1", "2"]), ("2026-09-14", ["1"]), ("2026-09-15", ["2"]), ("2026-09-22", ["3"])]:
        with (d / f"{day}.csv").open("w", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=["person_id", "total_citations", "raw_json"]); w.writeheader()
            for pid in pids: w.writerow({"person_id": pid, "total_citations": 10, "raw_json": "{}"})
    monkeypatch.setattr(detect_changes, "SNAPSHOT_DIR", tmp_path)
    latest, prev = detect_changes.latest_two("google_scholar")
    assert {r["person_id"] for r in latest} == {"1", "2", "3"}     # 14th, 15th and 22nd are one run
    assert {r["person_id"] for r in prev} == {"1", "2"}             # March is the previous one
