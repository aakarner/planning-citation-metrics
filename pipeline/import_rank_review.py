"""Apply reviewer decisions from rank_review.xlsx to the roster.

    python -m pipeline.import_rank_review data/review/rank_review.xlsx --dry-run
    python -m pipeline.import_rank_review data/review/rank_review.xlsx

For each row with an 'Actual rank' filled in:

  same rank as on file          the appointment row gets a 'rank verified <date>'
                                note, so the person is not flagged again for
                                RANK_REVIEW_YEARS
  assistant / associate / full  the row is closed and a new one opened at the
                                new rank, starting from 'Since (year)' if given,
                                else the review date
  emeritus, adjunct or lecturer the row is closed and a new one opened with that
                                appointment type, which takes them off the site
  left the program              the row is closed
  unclear, or blank             nothing

Nothing is deleted. Evidence ('Where you saw it', reviewer, notes) is written
into the source column of every row touched, like every other roster change.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from . import ROSTER_DIR
from .match_openalex import write_csv

RANKS = ("assistant", "associate", "full")
APPOINTMENT_TYPE = {"emeritus": "emeritus", "adjunct or lecturer": "adjunct"}


def since_date(value, fallback: str) -> str:
    """'2021' -> '2021-01-01'; a full date passes through; blank -> fallback."""
    if value is None or str(value).strip() == "":
        return fallback
    if hasattr(value, "date"):
        return value.date().isoformat()
    s = str(value).strip()
    if len(s) == 4 and s.isdigit():
        return f"{s}-01-01"
    return date.fromisoformat(s[:10]).isoformat()


def read_decisions(xlsx: Path) -> list[dict]:
    wb = load_workbook(xlsx, data_only=True)
    ws = wb["Review"]
    hdr = [c.value for c in ws[1]]
    col = {h: i for i, h in enumerate(hdr)}
    for needed in ("Actual rank", "Since (year)", "Where you saw it", "Reviewed by", "Date", "Notes",
                   "person_id", "affiliation_id", "Rank on file"):
        if needed not in col:
            sys.exit(f"column {needed!r} not found in the Review sheet")
    out = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        decision = (row[col["Actual rank"]] or "").strip().lower()
        if not decision or decision == "unclear":
            continue
        when = row[col["Date"]]
        out.append({"affiliation_id": str(row[col["affiliation_id"]]), "person_id": str(row[col["person_id"]]),
                    "on_file": row[col["Rank on file"]], "decision": decision,
                    "since": row[col["Since (year)"]], "url": (row[col["Where you saw it"]] or "").strip(),
                    "by": (row[col["Reviewed by"]] or "reviewer").strip(),
                    "date": when.date().isoformat() if hasattr(when, "date") else (str(when)[:10] if when else date.today().isoformat()),
                    "notes": (row[col["Notes"]] or "").strip()})
    return out


def apply(decisions: list[dict], affs: list[dict]) -> tuple[list[dict], list[str]]:
    """Return (updated rows, log lines). Pure, so it can be tested."""
    by_id = {a["affiliation_id"]: a for a in affs}
    next_id = max(int(a["affiliation_id"]) for a in affs) + 1
    log = []
    for d in decisions:
        a = by_id.get(d["affiliation_id"])
        if a is None:
            log.append(f"skip: affiliation {d['affiliation_id']} not found"); continue
        if a["end_date"]:
            log.append(f"skip: affiliation {d['affiliation_id']} already closed on {a['end_date']}"); continue
        # A rank a reviewer set today is a rank verified today: the new row carries
        # the note too, or a promotion dated years back is re-flagged at once.
        evidence = f"rank verified {d['date']}; rank review {d['date']} by {d['by']}" + \
                   (f": {d['url']}" if d["url"] else "") + (f" ({d['notes']})" if d["notes"] else "")
        dec = d["decision"]
        if dec == a["rank"]:
            a["source"] = f"{a['source']} | {evidence}"
            log.append(f"verified  {d['person_id']}: still {dec}")
            continue
        start = since_date(d["since"], d["date"])
        a["end_date"], a["source"] = start, f"{a['source']} | closed {start}: {dec}; {evidence}"
        if dec in RANKS or dec in APPOINTMENT_TYPE:
            affs.append({"affiliation_id": next_id, "person_id": a["person_id"], "department_id": a["department_id"],
                         "rank": dec if dec in RANKS else a["rank"],
                         "appointment_type": APPOINTMENT_TYPE.get(dec, "regular"), "is_primary": 1,
                         "start_date": start, "end_date": "", "source": evidence})
            next_id += 1
            log.append(f"{'rank' if dec in RANKS else 'type'}  {d['person_id']}: {a['rank']} -> {dec} from {start}")
        elif dec == "left the program":
            log.append(f"left      {d['person_id']}: closed {start}")
        else:
            log.append(f"skip: unknown decision {dec!r} for {d['person_id']}")
    return affs, log


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    decisions = read_decisions(args.xlsx)
    if not decisions:
        sys.exit("no decisions found in the sheet")
    path = ROSTER_DIR / "affiliation.csv"
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f); fields = list(reader.fieldnames); affs = list(reader)
    affs, log = apply(decisions, affs)
    print("\n".join(log))
    if args.dry_run:
        print(f"\n{len(decisions)} decisions (dry run, nothing written)"); return
    write_csv(path, fields, affs)
    print(f"\napplied {len(decisions)} decisions -> {path}; now rebuild: python -m pipeline.build_db && python -m pipeline.build_site")


if __name__ == "__main__":
    main()
