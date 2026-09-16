"""Read reviewer decisions from the review spreadsheet back into the review queue.

    python -m pipeline.import_review data/review/openalex_review.xlsx
    python -m pipeline.match_openalex --rescore      # then apply accepted ids to the roster

Rows with a Decision of 'accepted' or 'rejected' update the matching
(person_id, external_id) row in data/review/identity_candidates.csv, with
reviewed_by and reviewed_at. Blank decisions are left pending. Nothing else
in the CSV changes.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from .match_openalex import REVIEW_DIR, REVIEW_FIELDS, read_csv, write_csv


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--source", default="openalex")
    args = ap.parse_args(argv)

    wb = load_workbook(args.xlsx, data_only=True)
    ws = wb["Review"]
    hdr = [c.value for c in ws[1]]
    col = {h: i for i, h in enumerate(hdr)}
    for needed in ("Decision", "Reviewed by", "Date", "Notes", "person_id", "external_id"):
        if needed not in col:
            sys.exit(f"column {needed!r} not found in the Review sheet")

    decisions = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        decision = (row[col["Decision"]] or "").strip().lower()
        pid, ext = row[col["person_id"]], row[col["external_id"]]
        if decision in ("accepted", "rejected") and pid and ext:
            when = row[col["Date"]]
            decisions[(str(pid), str(ext))] = {
                "status": decision,
                "reviewed_by": (row[col["Reviewed by"]] or "reviewer"),
                "reviewed_at": when.date().isoformat() if hasattr(when, "date") else (str(when) if when else date.today().isoformat()),
                "notes": row[col["Notes"]],
            }
    if not decisions:
        sys.exit("no decisions found in the sheet")

    path = REVIEW_DIR / "identity_candidates.csv"
    fields, rows = read_csv(path)
    applied = 0
    accepted_people = set()
    for r in rows:
        d = decisions.get((r["person_id"], r["external_id"] or ""))
        if d and r["source"] == args.source:
            r["status"], r["reviewed_by"], r["reviewed_at"] = d["status"], d["reviewed_by"], d["reviewed_at"]
            if d.get("notes"):
                r["notes"] = d["notes"]        # the reviewer's reason, previously dropped
            applied += 1
            if d["status"] == "accepted":
                accepted_people.add(r["person_id"])
    # Two accepted candidates for one person is a mistake worth stopping on.
    per_person = {}
    for r in rows:
        if r["source"] == args.source and r["status"] == "accepted":
            per_person.setdefault(r["person_id"], []).append(r["external_id"])
    dupes = {p: e for p, e in per_person.items() if len(e) > 1}
    if dupes:
        sys.exit(f"more than one accepted candidate for: {dupes}; fix the sheet and re-run")
    write_csv(path, REVIEW_FIELDS, rows)
    print(f"applied {applied} decisions ({len(accepted_people)} people accepted); "
          f"now run: python -m pipeline.match_openalex --rescore")


if __name__ == "__main__":
    main()
