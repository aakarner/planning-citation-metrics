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
