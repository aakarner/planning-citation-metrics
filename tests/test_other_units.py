"""Planning PhDs appointed outside planning programs (department kind 'other')
are listed beside the rankings, never in them. Built on a tiny synthetic roster;
no network."""

import csv
import json
import sqlite3

import pipeline.apply_changes as apply_changes
import pipeline.detect_changes as detect_changes
from pipeline.build_db import build
from pipeline.build_site import as_of_dates

DEPT = {"url": "", "openalex_institution_id": "", "ror_id": "", "acsp_member": "", "active": 1}
PERSON = {"middle_name": "", "phd_year": 2010, "phd_institution": "", "interests": "",
          "openalex_author_id": "", "orcid": "", "semantic_scholar_id": "", "researchgate_url": "",
          "linkedin_url": "", "personal_url": "", "notes": ""}
SNAP_FIELDS = ["person_id", "source", "collected_at", "total_citations", "h_index", "i10_index",
               "citations_5yr", "h_index_5yr", "works_count", "raw_json"]


def write(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def roster(tmp_path):
    """A planning program at Test U, and a non-planning unit at the same university."""
    r = tmp_path / "roster"
    depts = [
        {"department_id": 1, "short_name": "Test U", "name": "City Planning", "university": "Test U",
         "country": "US", **DEPT, "url": "https://planning.testu.edu", "kind": "planning"},
        {"department_id": 2, "short_name": "Test U (Geography)", "name": "Department of Geography",
         "university": "Test U", "country": "US", **DEPT, "kind": "other"},
    ]
    people = [
        {"person_id": 1, "first_name": "Ada", "last_name": "Planner", "display_name": "Ada Planner",
         "google_scholar_id": "AAAAAAAAAAAJ", **PERSON},
        {"person_id": 2, "first_name": "Bo", "last_name": "Geographer", "display_name": "Bo Geographer",
         "google_scholar_id": "BBBBBBBBBBBJ", **PERSON},
    ]
    affs = [{"affiliation_id": i, "person_id": i, "department_id": i, "rank": "full",
             "appointment_type": "regular", "is_primary": 1, "start_date": "", "end_date": "",
             "source": "test"} for i in (1, 2)]
    write(r / "department.csv", list(depts[0]), depts)
    write(r / "person.csv", list(people[0]), people)
    write(r / "affiliation.csv", list(affs[0]), affs)
    write(r / "person_alias.csv", ["person_id", "alias"], [])
    return r


def scholar(pid, cites, affiliation="", email=""):
    return {"person_id": pid, "source": "google_scholar", "collected_at": "2026-09-15",
            "total_citations": cites, "h_index": 10, "i10_index": "", "citations_5yr": "",
            "h_index_5yr": "", "works_count": "",
            "raw_json": json.dumps({"affiliation": affiliation, "email_domain": email})}


def test_someone_in_another_unit_is_listed_but_never_ranked(tmp_path):
    snaps = tmp_path / "snapshots"
    write(snaps / "google_scholar" / "2026-09-15.csv", SNAP_FIELDS, [scholar(1, 500), scholar(2, 9000)])
    db = tmp_path / "t.sqlite"
    build(db, roster(tmp_path), snaps, quiet=True)
    con = sqlite3.connect(db)
    ranked = [r[0] for r in con.execute("SELECT person_id FROM v_person_percentiles")]
    listed = [r[0] for r in con.execute("SELECT person_id FROM v_other_units")]
    depts = [r[0] for r in con.execute("SELECT department_id FROM v_department_summary")]
    bench = con.execute("SELECT n, median_citations FROM v_rank_benchmarks WHERE rank='full'").fetchone()
    pct = con.execute("SELECT pct_citations_all FROM v_person_percentiles WHERE person_id=1").fetchone()[0]
    con.close()
    assert ranked == [1] and listed == [2] and depts == [1]
    # Bo's 9,000 citations move nobody's percentile or the field's benchmarks.
    assert bench == (1, 500) and pct == 0.0


def test_a_department_file_without_the_kind_column_reads_as_planning(tmp_path):
    r = roster(tmp_path)
    rows = list(csv.DictReader((r / "department.csv").open()))
    for d in rows:
        d.pop("kind")
    write(r / "department.csv", list(rows[0]), rows)
    snaps = tmp_path / "snapshots"
    write(snaps / "google_scholar" / "2026-09-15.csv", SNAP_FIELDS, [scholar(1, 500), scholar(2, 900)])
    db = tmp_path / "t.sqlite"
    build(db, r, snaps, quiet=True)
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM v_person_percentiles").fetchone()[0] == 2
    con.close()


def test_a_profile_naming_the_university_does_not_move_them_into_its_planning_program(tmp_path, monkeypatch, capsys):
    # Bo's profile says "Test U" with a testu.edu address. For a planning-program
    # member that reads as staying put; for someone in another Test U unit it must
    # not read as a move into Test U's planning program.
    r = roster(tmp_path)
    snaps = tmp_path / "snapshots"
    write(snaps / "google_scholar" / "2026-09-15.csv", SNAP_FIELDS,
          [scholar(1, 500, "Professor, Test U", "testu.edu"),
           scholar(2, 900, "Professor of Geography, Test U", "testu.edu")])
    monkeypatch.setattr(apply_changes, "ROSTER_DIR", r)
    monkeypatch.setattr(detect_changes, "SNAPSHOT_DIR", snaps)
    apply_changes.main(["--dry-run"])
    out = capsys.readouterr().out
    assert "Bo Geographer" not in out
    assert "0 moves" in out


def test_a_targeted_fetch_does_not_move_the_as_of_date():
    counts = [{"source": "openalex", "d": "2026-10-09", "n": 959},
              {"source": "openalex", "d": "2026-10-10", "n": 50},
              {"source": "google_scholar", "d": "2026-09-15", "n": 718},
              {"source": "google_scholar", "d": "2026-10-12", "n": 50}]     # a weekly rolling batch
    # OpenAlex is collected in full runs, so the 50-row fetch does not move its date;
    # Scholar is rolling, so its date is the newest reading (the page says "last read")
    assert as_of_dates(counts) == {"openalex": "2026-10-09", "google_scholar": "2026-10-12"}
