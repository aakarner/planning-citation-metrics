"""The discovery agent's status record: what its last slot did.

    python -m pipeline.discovery_status --state .discovery.status.json --outcome ran --queued 88 --batch .discovery.batch.json

The agent (scripts/daily_discovery.sh) runs under launchd on a Mac. Nothing on
GitHub can see it, so when Scholar blocked it for days, or a batch read thirty
verification pages as thirty people without profiles, nobody was told. After
every slot the script calls this to fold the slot into a small JSON record,
then pushes the record to the ref `refs/agent/discovery-status`, where
pipeline/health_check.py reads it. The record holds counts and dates only.

    reported       when the agent last ran a slot at all (UTC)
    outcome        idle     nobody to search
                   rested   a batch was already searched today
                   searched this slot searched at least one person
                   blocked  Scholar refused five searches running
                   failed   the slot had work and searched nobody for another reason
                   offline  no network
    queued         people waiting to be searched after the slot
    last_searched  the last day anyone was searched
    stalled_since  the first day of the current run of slots that had work and
                   searched nobody; cleared by a search or an empty queue
    batches        the last ten batches that searched anyone: date, searched,
                   no_links (results pages with no linked author), found, failed
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

OUTCOMES = ("idle", "rested", "offline", "ran")
KEEP_BATCHES = 10


def next_status(prev: dict | None, *, now: datetime, outcome: str,
                queued: int | None = None, batch: dict | None = None) -> dict:
    """Fold one slot into the record. `outcome` is what the script knows (one of
    OUTCOMES); "ran" is resolved here from the finder's batch report."""
    prev = prev or {}
    today = now.date().isoformat()
    status = {"reported": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "outcome": outcome,
              "queued": prev.get("queued") if queued is None else queued,
              "last_searched": prev.get("last_searched"), "stalled_since": prev.get("stalled_since"),
              "batches": list(prev.get("batches") or [])}
    if outcome == "ran":
        searched = int((batch or {}).get("searched") or 0)
        if searched:
            status["outcome"] = "searched"
            status["last_searched"] = today
            status["batches"] = (status["batches"] + [{
                "date": today, "searched": searched, "no_links": int(batch.get("no_links") or 0),
                "found": int(batch.get("found") or 0), "failed": int(batch.get("failed") or 0)}])[-KEEP_BATCHES:]
        else:
            status["outcome"] = "blocked" if (batch or {}).get("blocked") else "failed"
    if status["outcome"] in ("searched", "rested", "idle"):
        status["stalled_since"] = None
    else:
        status["stalled_since"] = status["stalled_since"] or today
    if status["outcome"] == "idle":
        status["queued"] = 0
    return status


def read_json(path: str | None) -> dict | None:
    try:
        return json.loads(Path(path).read_text()) if path else None
    except (OSError, ValueError):
        return None


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", required=True, help="the record to update in place")
    ap.add_argument("--outcome", required=True, choices=OUTCOMES)
    ap.add_argument("--queued", type=int)
    ap.add_argument("--batch", help="the finder's --report file for this slot")
    args = ap.parse_args(argv)
    status = next_status(read_json(args.state), now=datetime.now(timezone.utc), outcome=args.outcome,
                         queued=args.queued, batch=read_json(args.batch))
    Path(args.state).write_text(json.dumps(status, indent=2) + "\n")
    print(f"status: {status['outcome']}, {status['queued']} queued"
          + (f", no one searched since {status['stalled_since']}" if status["stalled_since"] else ""))


if __name__ == "__main__":
    main()
