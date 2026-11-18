"""Remove Scholar ids whose profile page no longer exists.

    python -m pipeline.clear_dead_ids --ids=ID,ID,...      # verify, then clear
    python -m pipeline.clear_dead_ids --ids=ID --dry-run

Each id is checked first, through SerpApi (two searches an id): only a profile
that comes back empty twice, the second time uncached, is cleared, so a
throttled or failing lookup can never strip a live id. Nothing here contacts
Scholar from this machine. The person keeps their history and falls back to
OpenAlex (or their last Publish or Perish figure) until the discovery pass
finds a new profile for them.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date

from . import ROSTER_DIR
from .collect_scholar import PermanentFailure, fetch_serpapi, read_confirmed
from .match_openalex import read_csv, write_csv


def profile_is_gone(scholar_id: str, key: str, pause: float = 5.0, fetch=fetch_serpapi) -> bool:
    """True only when SerpApi returns an empty profile twice. A reading, or any
    error, leaves the id alone: this must never clear an id it could not check."""
    try:
        read_confirmed(lambda sid, fresh=False: fetch(sid, key, fresh=fresh), scholar_id, pause)
    except PermanentFailure:
        return True
    except Exception as e:
        print(f"  could not check {scholar_id} ({type(e).__name__}); left alone", file=sys.stderr)
    return False


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", metavar="ID,ID,...", default="",
                    help="comma-separated Scholar ids; written as --ids=... because one can start with '-'")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
    if not wanted:
        sys.exit("give at least one id, as --ids=...")
    key = os.environ.get("SERPAPI_KEY")
    if not key:
        sys.exit("SERPAPI_KEY is not set (put it in .env)")

    fields, people = read_csv(ROSTER_DIR / "person.csv")
    cleared = 0
    for p in people:
        sid = p.get("google_scholar_id")
        if sid not in wanted:
            continue
        if not profile_is_gone(sid, key):
            print(f"  {p['display_name']:<28} {sid}  still resolves, or could not be checked; left alone")
            continue
        print(f"  {p['display_name']:<28} {sid}  empty twice, clearing")
        cleared += 1
        if not args.dry_run:
            p["google_scholar_id"] = None
            p["notes"] = " ".join(filter(None, [
                p.get("notes"),
                f"Scholar profile {sid} no longer resolved on {date.today().isoformat()}; id cleared, "
                "falls back to OpenAlex until a new profile is found."]))
    print(f"\n{cleared} cleared" + (" (dry run, nothing written)" if args.dry_run else ""))
    if not args.dry_run and cleared:
        write_csv(ROSTER_DIR / "person.csv", fields, people)
        print(f"wrote {ROSTER_DIR / 'person.csv'}")


if __name__ == "__main__":
    main()
