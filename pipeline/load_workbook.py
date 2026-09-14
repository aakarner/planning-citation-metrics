"""Migrate Tom Sanchez's handover workbook into the roster CSVs and a first snapshot.

Run once:

    python -m pipeline.load_workbook data/legacy/UPDATED_Cites2026.xlsm \
        --scholar-date 2026-03-01 --pop-date 2026-02-20

Dates per Tom Sanchez (email, 2026-09-14): Scholar totals refreshed 2026-03-01;
Publish or Perish lookups run 2026-02-15 to 2026-02-20.

Writes:
  data/roster/department.csv
  data/roster/person.csv
  data/roster/person_alias.csv
  data/roster/affiliation.csv
  data/snapshots/google_scholar/<date>.csv   faculty with a Scholar profile
  data/snapshots/pop/<date>.csv              faculty looked up in Publish or Perish
  data/snapshots/<source>/<date>.meta.json   run metadata
  data/private/person_private.csv            gender; gitignored, never published

The workbook has one row per (person, school). Two people appear twice because
they moved and the old row was never removed; those are merged into one person
with two affiliations, the older one closed.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import openpyxl

from . import PRIVATE_DIR, ROSTER_DIR, SNAPSHOT_DIR

RANK_MAP = {
    "Professor": "full",
    "Associate Professor": "associate",
    "Assistant Professor": "assistant",
}

# Workbook school labels that are Canadian. Everything else is US.
CANADA = {
    "Dalhousie University",
    "McGill University",
    "Queen's University",
    "Simon Fraser University",
    "Toronto Metropolitan University",
    "Universite Laval",
    "Universite de Montreal",
    "University of Alberta",
    "University of British Columbia",
    "University of Calgary",
    "University of Guelph",
    "University of Manitoba",
    "University of Northern British Columbia",
    "University of Quebec in Montreal",
    "University of Saskatchewan",
    "University of Toronto",
    "University of Waterloo, Ontario",
    "York University",
}

# People who appear at two schools in the workbook. Value = the school we
# treat as current; the other row becomes a closed affiliation. Both moves were
# verified on 2026-09-13 against the Scholar profile (verified email domain)
# and the department directory page.
CURRENT_SCHOOL_OVERRIDE = {
    "Guang Tian": "University of Utah",
    "Lisa Berglund": "Morgan State University",
}

PERSON_FIELDS = [
    "person_id", "first_name", "middle_name", "last_name", "display_name",
    "phd_year", "phd_institution", "interests", "google_scholar_id", "openalex_author_id",
    "orcid", "semantic_scholar_id", "researchgate_url", "linkedin_url", "personal_url", "notes",
]
DEPARTMENT_FIELDS = [
    "department_id", "short_name", "name", "university", "country", "url",
    "openalex_institution_id", "ror_id", "acsp_member", "active",
]
PRIVATE_FIELDS = ["person_id", "gender"]
AFFILIATION_FIELDS = [
    "affiliation_id", "person_id", "department_id", "rank", "appointment_type", "is_primary",
    "start_date", "end_date", "source",
]
SNAPSHOT_FIELDS = [
    "person_id", "source", "collected_at", "total_citations", "h_index", "i10_index",
    "citations_5yr", "h_index_5yr", "works_count", "raw_json",
]


def clean(value):
    """Strip strings, turn blanks and Excel errors into None."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "" or value.startswith("#"):
            return None
    return value


def scholar_id(url) -> str | None:
    url = clean(url)
    if not url or not str(url).startswith("http"):
        return None
    user = parse_qs(urlparse(str(url)).query).get("user", [None])[0]
    if user and re.fullmatch(r"[\w-]{12}", user):
        return user
    print(f"  warning: Scholar URL without a valid user id: {url}", file=sys.stderr)
    return None


def read_sheet(ws) -> list[dict]:
    rows = ws.iter_rows(values_only=True)
    header = [clean(h) for h in next(rows)]
    out = []
    for row in rows:
        if not any(c is not None for c in row):
            continue
        out.append({header[i]: clean(v) for i, v in enumerate(row) if i < len(header) and header[i]})
    return out


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})
    print(f"  wrote {len(rows):>5} rows  {path}")


def migrate(workbook: Path, dates: dict[str, str], roster_dir: Path, snapshot_dir: Path,
            private_dir: Path = PRIVATE_DIR, migration_date: str | None = None) -> None:
    migration_date = migration_date or date.today().isoformat()
    # Re-running must not destroy curation done since the first migration
    # (OpenAlex ids, ORCIDs, notes). Carry those over by display name.
    existing = {}
    prior = roster_dir / "person.csv"
    if prior.exists():
        with prior.open(newline="", encoding="utf-8") as f:
            existing = {r["display_name"]: r for r in csv.DictReader(f)}
    print(f"Reading {workbook}")
    wb = openpyxl.load_workbook(workbook, read_only=True, data_only=True)
    cites = [r for r in read_sheet(wb["Cites"]) if r.get("Name")]
    schools = [r for r in read_sheet(wb["Current Schools"]) if r.get("Planning School")]
    print(f"  {len(cites)} faculty rows, {len(schools)} school rows")

    # ---- departments -------------------------------------------------------
    school_url = {s["Planning School"]: s.get("URL") for s in schools}
    labels = sorted({r["CurrentSchool"] for r in cites})
    missing = [l for l in labels if l not in school_url]
    if missing:
        print(f"  warning: schools in Cites but not in Current Schools: {missing}", file=sys.stderr)
    departments, dept_id = [], {}
    for i, label in enumerate(labels, start=1):
        dept_id[label] = i
        departments.append({
            "department_id": i,
            "short_name": label,
            "name": label,          # unit name to be curated later
            "university": label,    # same; the workbook only has one label
            "country": "CA" if label in CANADA else "US",
            "url": school_url.get(label),
            "openalex_institution_id": None,
            "ror_id": None,
            "acsp_member": None,
            "active": 1,
        })

    # ---- people, merging duplicates ----------------------------------------
    # Group rows by (display name, PhD institution). Two rows in one group is a move.
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in cites:
        groups[(r["Name"], r.get("PhDSchool"))].append(r)

    persons, private, aliases, affiliations, snapshots = [], [], [], [], defaultdict(list)
    pid = aid = 0
    for key, rows in groups.items():
        name = key[0]
        if len(rows) > 1:
            current_label = CURRENT_SCHOOL_OVERRIDE.get(name)
            if current_label is None:
                sys.exit(f"Duplicate rows for {name!r} but no CURRENT_SCHOOL_OVERRIDE entry")
            rows.sort(key=lambda r: r["CurrentSchool"] != current_label)  # current first
            print(f"  merging {name}: current at {rows[0]['CurrentSchool']}, "
                  f"closing {[r['CurrentSchool'] for r in rows[1:]]}")
        cur = rows[0]
        pid += 1
        gs = scholar_id(cur.get("GoogleScholar"))
        persons.append({
            "person_id": pid,
            "first_name": cur.get("First") or name.split()[0],
            "middle_name": cur.get("Middle"),
            "last_name": cur.get("Last") or name.split()[-1],
            "display_name": name,
            "phd_year": cur.get("PhDYear") or cur.get("Year"),
            "phd_institution": cur.get("PhDSchool"),
            "interests": cur.get("Interests"),
            "google_scholar_id": gs,
            "openalex_author_id": (existing.get(name) or {}).get("openalex_author_id") or None,
            "orcid": (existing.get(name) or {}).get("orcid") or None,
            "semantic_scholar_id": (existing.get(name) or {}).get("semantic_scholar_id") or None,
            "researchgate_url": cur.get("ResearchGate"),
            "linkedin_url": cur.get("LinkedIn"),
            "personal_url": cur.get("Personal"),
            "notes": (existing.get(name) or {}).get("notes") or None,
        })
        private.append({"person_id": pid, "gender": cur.get("Gender")})
        # Alias: the raw workbook name if it differs from the cleaned display name.
        raw = rows[0].get("Name")
        if raw and raw != name:
            aliases.append({"person_id": pid, "alias": raw})

        for i, r in enumerate(rows):
            aid += 1
            rank = RANK_MAP.get(r.get("Position"))
            if rank is None:
                sys.exit(f"Unknown Position {r.get('Position')!r} for {name}")
            is_current = i == 0
            affiliations.append({
                "affiliation_id": aid,
                "person_id": pid,
                "department_id": dept_id[r["CurrentSchool"]],
                "rank": rank,
                "appointment_type": "regular",   # Tom tracked full-time tenure-track faculty only
                "is_primary": 1,
                "start_date": None,
                "end_date": None if is_current else migration_date,
                "source": (
                    "workbook-2026" if is_current and len(rows) == 1 else
                    "workbook-2026; move verified 2026-09-13 against Scholar profile (verified email domain) and department page" if is_current else
                    "workbook-2026; stale duplicate row, closed at migration, actual end date unknown"
                ),
            })

        # One snapshot per person from the current row's numbers.
        source = "google_scholar" if gs else "pop"
        snapshots[source].append({
            "person_id": pid,
            "source": source,
            "collected_at": dates[source],
            "total_citations": cur.get("Cites2017"),
            "h_index": cur.get("H-Index"),
            "i10_index": None,
            "citations_5yr": None,
            "h_index_5yr": None,
            "works_count": None,
            "raw_json": json.dumps({
                "workbook_row": {k: v for k, v in cur.items() if v is not None and k != "Gender"},
            }, ensure_ascii=False),
        })

    print(f"  {len(persons)} people, {len(affiliations)} affiliations, "
          f"{len(departments)} departments")

    # ---- write ----------------------------------------------------------------
    write_csv(roster_dir / "department.csv", DEPARTMENT_FIELDS, departments)
    write_csv(roster_dir / "person.csv", PERSON_FIELDS, persons)
    write_csv(roster_dir / "person_alias.csv", ["person_id", "alias"], aliases)
    write_csv(roster_dir / "affiliation.csv", AFFILIATION_FIELDS, affiliations)
    write_csv(private_dir / "person_private.csv", PRIVATE_FIELDS, private)
    notes = {
        "google_scholar": f"Migrated from {workbook.name}. Tom Sanchez refreshed Scholar totals and "
                          "h-indices on 2026-03-01 (email, 2026-09-14).",
        "pop": f"Migrated from {workbook.name}. Publish or Perish lookups run 2026-02-15 to 2026-02-20 "
               "by Tom Sanchez (email, 2026-09-14); queries not retained.",
    }
    for source, rows in snapshots.items():
        path = snapshot_dir / source / f"{dates[source]}.csv"
        write_csv(path, SNAPSHOT_FIELDS, rows)
        meta = {"trigger": "migration", "notes": notes[source]}
        path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2) + "\n")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workbook", type=Path)
    ap.add_argument("--scholar-date", default="2026-03-01", help="date Tom last refreshed Scholar numbers")
    ap.add_argument("--pop-date", default="2026-02-20", help="date of the Publish or Perish lookups")
    ap.add_argument("--migration-date", default=None, help="end_date for closed duplicate rows (default today)")
    ap.add_argument("--roster-dir", type=Path, default=ROSTER_DIR)
    ap.add_argument("--snapshot-dir", type=Path, default=SNAPSHOT_DIR)
    args = ap.parse_args(argv)
    migrate(args.workbook, {"google_scholar": args.scholar_date, "pop": args.pop_date},
            args.roster_dir, args.snapshot_dir, migration_date=args.migration_date)


if __name__ == "__main__":
    main()
