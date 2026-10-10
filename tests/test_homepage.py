"""The homepage's computed blocks: medians by rank, and what changed since the
last collection. Both are shown to every visitor first, so they are pinned."""

import sqlite3
from pathlib import Path

from pipeline.build_site import collection_window, recent_changes

ROOT = Path(__file__).resolve().parent.parent


def aff(aid, pid, dept, rank, start="", end="", dept_name=None, source=""):
    return {"affiliation_id": aid, "person_id": pid, "department_id": dept, "dept": dept_name or f"D{dept}",
            "rank": rank, "appointment_type": "regular", "is_primary": 1, "start_date": start, "end_date": end,
            "source": source}


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


def test_promotions_say_where_the_rank_was_read():
    affs = [aff(1, "a", 1, "assistant", end="2026-09-27"),
            aff(2, "a", 1, "associate", start="2026-09-27", source="rank verified 2026-09-27; department page https://x/faculty"),
            aff(3, "b", 1, "associate", end="2026-09-15"),
            aff(4, "b", 1, "full", start="2026-09-15", source="change from Scholar profile 2026-09-15: ...")]
    ch = recent_changes(affs, "2026-09-14")
    assert sorted(p[4] for p in ch["promos"]) == ["profile", "website"]


# --- Rising: citation gains since the baseline, within rank -------------------

def test_rising_view_measures_each_person_against_the_first_scholar_collection():
    con = sqlite3.connect(":memory:")
    con.executescript((ROOT / "sql" / "schema.sql").read_text())
    con.executescript((ROOT / "sql" / "views.sql").read_text())
    con.execute("INSERT INTO department (department_id, short_name, name, university, country) VALUES (1,'D','D','D','US')")
    con.execute("INSERT INTO collection_run (run_id, source, started_at) VALUES (1,'google_scholar','2026-03-01')")
    con.execute("INSERT INTO collection_run (run_id, source, started_at) VALUES (2,'google_scholar','2026-09-15')")
    for pid, gsid in ((1, "a"), (2, "b"), (3, None)):          # 3 has had the profile vetoed since
        con.execute("INSERT INTO person (person_id, first_name, last_name, display_name, google_scholar_id) VALUES (?,?,?,?,?)",
                    (pid, "p", str(pid), f"p{pid}", gsid))
        con.execute("INSERT INTO affiliation (person_id, department_id, rank, appointment_type, is_primary) VALUES (?,1,'assistant','regular',1)", (pid,))
    snap = "INSERT INTO metric_snapshot (run_id, person_id, source, collected_at, total_citations) VALUES (?,?,?,?,?)"
    con.execute(snap, (1, 1, "google_scholar", "2026-03-01", 100))
    con.execute(snap, (2, 1, "google_scholar", "2026-09-15", 130))
    con.execute(snap, (2, 2, "google_scholar", "2026-09-15", 900))     # found after the baseline: no row
    con.execute(snap, (1, 3, "google_scholar", "2026-03-01", 100))
    con.execute(snap, (2, 3, "google_scholar", "2026-09-15", 5000))    # vetoed id: no row
    rows = con.execute("SELECT person_id, base_citations, total_citations, gain, base_date FROM v_rising").fetchall()
    assert rows == [(1, 100, 130, 30, "2026-03-01")]


def test_rising_keeps_the_biggest_plausible_gains_within_each_rank():
    from pipeline.build_site import rising

    def row(pid, rank, base, now):
        return {"person_id": pid, "display_name": f"p{pid}", "rank": rank, "department": "D",
                "base_citations": base, "total_citations": now, "gain": now - base, "base_date": "2026-03-01"}
    rows = [row(1, "assistant", 100, 140),     # +40%: in
            row(2, "assistant", 50, 90),       # too small a base: out
            row(3, "assistant", 200, 400),     # +100%: a merge or a namesake, held back
            row(4, "assistant", 1000, 1010),   # +1%: in, but behind 1
            row(5, "assistant", 300, 290),     # fell: out
            row(6, "full", 10000, 11000)]      # +10%: in, in its own column
    rows += [row(10 + i, "associate", 1000, 1000 + 10 * i) for i in range(1, 9)]   # eight associates
    out = rising(rows)
    assert [r["person_id"] for r in out["assistant"]] == [1, 4]
    assert [r["person_id"] for r in out["full"]] == [6]
    assert [r["person_id"] for r in out["associate"]] == [18, 17, 16, 15, 14]     # five, biggest first


def test_the_rising_block_renders_its_columns_and_says_what_it_leaves_out():
    from pipeline.build_site import index_page, uniq_slugs

    def person(pid, name, rank="assistant"):
        return {"person_id": pid, "display_name": name, "department": "D1", "department_id": 1,
                "rank": rank, "total_citations": 500, "h_index": 5}
    people = [person(1, "Ada Lovelace"), person(2, "Grace Hopper", "full")]
    depts = [{"department_id": 1, "short_name": "D1", "n_faculty": 3, "median_citations": 100, "mean_citations": 100,
              "university": "D1", "total_citations": 300}]
    rising_rows = [{"person_id": 1, "display_name": "Ada Lovelace", "rank": "assistant", "department": "D1",
                    "base_citations": 400, "total_citations": 500, "gain": 100, "base_date": "2026-03-01"},
                   {"person_id": 2, "display_name": "Grace Hopper", "rank": "full", "department": "D1",
                    "base_citations": 100, "total_citations": 500, "gain": 400, "base_date": "2026-03-01"}]  # +400%: held
    data = {"people": people, "depts": depts, "asof": {"google_scholar": "2026-09-16", "openalex": "2026-09-15",
            "pop": "2026-02-20"}, "mix": {"google_scholar": 2}, "bench": {}, "affs": [aff(1, 1, 1, "assistant")],
            "gs_dates": ["2026-03-01", "2026-09-15"], "rising": rising_rows}
    html = index_page(data, uniq_slugs(people, lambda p: p["display_name"]),
                      uniq_slugs(depts, lambda d: d["short_name"]), "")
    assert "Rising since March 2026" in html
    block = html.split("Rising since March 2026", 1)[1].split("<h2>", 1)[0]
    assert "Ada Lovelace" in block and "+100" in block and "+25%" in block
    assert "Grace Hopper" not in block and "Full professors" not in block
    assert "more than 50%" in block

    data["rising"] = []
    assert "Rising since" not in index_page(data, uniq_slugs(people, lambda p: p["display_name"]),
                                            uniq_slugs(depts, lambda d: d["short_name"]), "")
