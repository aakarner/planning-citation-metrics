"""The headline metric follows the source policy: Scholar, then OpenAlex, then
Publish or Perish or manual. Built on a tiny synthetic roster; no network."""

import csv
import sqlite3

from pipeline.build_db import build

ROSTER = {
    "department.csv": [
        {"department_id": 1, "short_name": "Test U", "name": "Test U", "university": "Test U",
         "country": "US", "url": "", "openalex_institution_id": "", "ror_id": "", "acsp_member": "", "active": 1},
    ],
    "person.csv": [
        {"person_id": 1, "first_name": "Ada", "middle_name": "", "last_name": "Scholar", "display_name": "Ada Scholar",
         "phd_year": 2010, "phd_institution": "", "interests": "", "google_scholar_id": "AAAAAAAAAAAJ",
         "openalex_author_id": "A1", "orcid": "", "semantic_scholar_id": "", "researchgate_url": "",
         "linkedin_url": "", "personal_url": "", "notes": ""},
        {"person_id": 2, "first_name": "Bo", "middle_name": "", "last_name": "Noprofile", "display_name": "Bo Noprofile",
         "phd_year": 2012, "phd_institution": "", "interests": "", "google_scholar_id": "",
         "openalex_author_id": "A2", "orcid": "", "semantic_scholar_id": "", "researchgate_url": "",
         "linkedin_url": "", "personal_url": "", "notes": ""},
        {"person_id": 3, "first_name": "Cy", "middle_name": "", "last_name": "Unmatched", "display_name": "Cy Unmatched",
         "phd_year": 2015, "phd_institution": "", "interests": "", "google_scholar_id": "",
         "openalex_author_id": "", "orcid": "", "semantic_scholar_id": "", "researchgate_url": "",
         "linkedin_url": "", "personal_url": "", "notes": ""},
    ],
    "person_alias.csv": [],
    "affiliation.csv": [
        {"affiliation_id": i, "person_id": i, "department_id": 1, "rank": "full", "appointment_type": "regular",
         "is_primary": 1, "start_date": "", "end_date": "", "source": "test"} for i in (1, 2, 3)
    ],
}
SNAP_FIELDS = ["person_id", "source", "collected_at", "total_citations", "h_index", "i10_index",
               "citations_5yr", "h_index_5yr", "works_count", "raw_json"]


def snap(pid, source, date, cites):
    return {"person_id": pid, "source": source, "collected_at": date, "total_citations": cites, "h_index": 1,
            "i10_index": "", "citations_5yr": "", "h_index_5yr": "", "works_count": "", "raw_json": ""}


def write(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def test_headline_prefers_scholar_then_openalex_then_pop(tmp_path):
    roster = tmp_path / "roster"
    snaps = tmp_path / "snapshots"
    for name, rows in ROSTER.items():
        fields = list(rows[0].keys()) if rows else ["person_id", "alias"]
        write(roster / name, fields, rows)
    # Everyone got a 2026-02 Publish or Perish number; Ada also has Scholar;
    # Ada and Bo have a newer OpenAlex number.
    write(snaps / "pop" / "2026-02-20.csv", SNAP_FIELDS,
          [snap(1, "pop", "2026-02-20", 900), snap(2, "pop", "2026-02-20", 300), snap(3, "pop", "2026-02-20", 50)])
    write(snaps / "google_scholar" / "2026-03-01.csv", SNAP_FIELDS, [snap(1, "google_scholar", "2026-03-01", 1000)])
    write(snaps / "openalex" / "2026-10-01.csv", SNAP_FIELDS,
          [snap(1, "openalex", "2026-10-01", 600), snap(2, "openalex", "2026-10-01", 200)])

    db = build(tmp_path / "t.sqlite", roster, snaps, quiet=True)
    con = sqlite3.connect(db)
    rows = {r[0]: r[1:] for r in con.execute(
        "SELECT person_id, source, total_citations, is_fallback FROM v_headline_metrics")}
    assert rows[1] == ("google_scholar", 1000, 0)   # Scholar wins even over newer OpenAlex
    assert rows[2] == ("openalex", 200, 1)          # no profile: OpenAlex, flagged
    assert rows[3] == ("pop", 50, 1)                # nothing newer yet: keep the migrated number
    flagged = con.execute("SELECT COUNT(*) FROM v_person_percentiles WHERE is_fallback = 1").fetchone()[0]
    assert flagged == 2
