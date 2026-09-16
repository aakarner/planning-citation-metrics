"""Remove Scholar ids whose profile page no longer exists.

    python -m pipeline.clear_dead_ids --ids=ID,ID,...      # verify, then clear
    python -m pipeline.clear_dead_ids --ids=ID --dry-run

Each id is checked directly: only a profile page returning 404 is cleared, so a
throttled run can never strip a live id. The person keeps their history and
falls back to OpenAlex (or their last Publish or Perish figure) until the
discovery pass finds a new profile for them.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from . import ROSTER_DIR
from .collect_scholar import id_is_dead
from .match_openalex import read_csv, write_csv


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", metavar="ID,ID,...", default="",
                    help="comma-separated Scholar ids; written as --ids=... because one can start with '-'")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
    if not wanted:
        sys.exit("give at least one id, as --ids=...")

    fields, people = read_csv(ROSTER_DIR / "person.csv")
    cleared = 0
    for p in people:
        sid = p.get("google_scholar_id")
        if sid not in wanted:
            continue
        if not id_is_dead(sid):
            print(f"  {p['display_name']:<28} {sid}  still resolves; left alone")
            continue
        print(f"  {p['display_name']:<28} {sid}  404, clearing")
        cleared += 1
        if not args.dry_run:
            p["google_scholar_id"] = None
            p["notes"] = " ".join(filter(None, [
                p.get("notes"),
                f"Scholar profile {sid} returned 404 on {date.today().isoformat()}; id cleared, "
                "falls back to OpenAlex until a new profile is found."]))
    missing = wanted - {p.get("google_scholar_id") for p in people}
    print(f"\n{cleared} cleared" + (" (dry run, nothing written)" if args.dry_run else ""))
    if not args.dry_run and cleared:
        write_csv(ROSTER_DIR / "person.csv", fields, people)
        print(f"wrote {ROSTER_DIR / 'person.csv'}")


if __name__ == "__main__":
    main()
