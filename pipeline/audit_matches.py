"""Un-accept OpenAlex matches that the collected numbers contradict.

    python -m pipeline.audit_matches --dry-run
    python -m pipeline.audit_matches

The matcher decides from names, institutions and topics, before any OpenAlex
metrics exist. Once a snapshot is in, a much blunter test is available: an
author's OpenAlex citation count should be in the same world as the Scholar or
Publish or Perish count we already hold for them. OpenAlex is journal-centric
and normally lands near 0.7 of Scholar, so anything beyond MAX_RATIO or below
MIN_RATIO is a different person with the same name.

Offenders lose their openalex_author_id in person.csv and their review row is
marked 'rejected' with the evidence, so the matcher will not pick the same
record again. A reviewer who disagrees can flip that row back in the review
spreadsheet. People with a Scholar profile only lose a comparison figure;
fallback people revert to their earlier number until a better match is found.

Note on Publish or Perish baselines: those were hand-built and can themselves
undercount, so a rejection against a `pop` base is weaker evidence than one
against a Scholar base. Both are rejected here, and the note says which.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from datetime import date
from pathlib import Path

from . import BUILD_DIR, ROSTER_DIR
from .match_openalex import REVIEW_DIR, REVIEW_FIELDS, read_csv, write_csv

MAX_RATIO = 3.0     # OpenAlex claiming >3x Scholar is a bigger namesake
MIN_RATIO = 0.05    # OpenAlex claiming <5% of Scholar is an empty or wrong record
MIN_BASE = 25       # below this the ratio is noise, so don't judge


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=BUILD_DIR / "citations.sqlite")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT p.person_id, p.display_name, p.openalex_author_id,
               ours.source AS base_source, ours.total_citations AS base_cites,
               o.total_citations AS oa_cites, o.works_count
        FROM person p
        JOIN v_openalex_metrics o ON o.person_id = p.person_id
        JOIN (SELECT person_id, source, total_citations FROM v_latest_metrics
              WHERE source IN ('google_scholar', 'pop')) ours ON ours.person_id = p.person_id
        WHERE p.openalex_author_id IS NOT NULL AND p.openalex_author_id <> ''
    """).fetchall()
    con.close()

    bad = {}
    for r in rows:
        base, oa = r["base_cites"], r["oa_cites"]
        if not base or base < MIN_BASE or oa is None:
            continue
        ratio = oa / base
        if ratio > MAX_RATIO or ratio < MIN_RATIO:
            bad[str(r["person_id"])] = (
                f"un-accepted {date.today().isoformat()}: OpenAlex {oa:,} citations against "
                f"{base:,} from {r['base_source']} ({ratio:.1f}x); probable namesake")
            print(f"  {r['display_name']:<28} {r['base_source']:<15} ours {base:>7,}  "
                  f"openalex {oa:>7,}  {ratio:>6.1f}x  ({r['openalex_author_id']})")

    print(f"\n{len(bad)} of {len(rows)} matches contradicted by the numbers"
          + (" (dry run, nothing written)" if args.dry_run else ""))
    if args.dry_run or not bad:
        return

    ppath = ROSTER_DIR / "person.csv"
    pfields, people = read_csv(ppath)
    for p in people:
        if p["person_id"] in bad:
            p["notes"] = " ".join(filter(None, [p.get("notes"), bad[p["person_id"]]]))
            p["openalex_author_id"] = None
    write_csv(ppath, pfields, people)

    rpath = REVIEW_DIR / "identity_candidates.csv"
    _, review = read_csv(rpath)
    for r in review:
        if r["source"] == "openalex" and r["person_id"] in bad and r["status"] == "accepted":
            r["status"], r["reviewed_by"], r["reviewed_at"] = "rejected", "audit_matches", date.today().isoformat()
    write_csv(rpath, REVIEW_FIELDS, review)
    print(f"cleared {len(bad)} ids from {ppath} and rejected their review rows")


if __name__ == "__main__":
    main()
