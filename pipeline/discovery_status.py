"""The discovery run's status record: what the last run did.

Profile discovery (pipeline/find_scholar_profiles.py) can come back empty for
a good reason, most people searched have no profile, and for a bad one: in
September 2026 thirty verification pages were read as thirty people without
profiles, and in November it was blocked for days with ninety people waiting.
Neither failed anything. So each run folds what it saw into
data/review/discovery_status.json (--status), the weekly workflow commits it,
and pipeline/health_check.py reads it. Counts and dates only.

    reported       when discovery last ran (UTC)
    outcome        idle     nobody to search
                   searched at least one person was searched
                   blocked  five searches in a row failed
                   failed   there were people to search and none was, for another reason
                   skipped  the week's run was cut so short that discovery was given no searches
    queued         people still waiting after the run
    last_searched  the last day anyone was searched
    stalled_since  the first day of the current run of attempts that had work
                   and searched nobody; cleared by a search or an empty queue
    batches        the last ten batches that searched anyone: date, searched,
                   no_links (results with no linked author), found, failed

    python -m pipeline.discovery_status --state data/review/discovery_status.json --failed
        records a run that crashed before it could report (the workflow does this)
    python -m pipeline.discovery_status --state data/review/discovery_status.json --skipped
        records a week in which discovery was deliberately not run
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

KEEP_BATCHES = 10


def next_status(prev: dict | None, *, now: datetime, batch: dict | None, skipped: bool = False) -> dict:
    """Fold one run into the record. `batch` is the finder's report
    (find_scholar_profiles.batch_report), or None for a run that never got
    as far as writing one. `skipped` records a week in which discovery was
    left out on purpose: the record shows it reported, so the health check
    does not call it silent, and nothing else about it changes."""
    prev = prev or {}
    if skipped:
        return {"reported": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "outcome": "skipped",
                "queued": prev.get("queued"), "last_searched": prev.get("last_searched"),
                "stalled_since": prev.get("stalled_since"), "batches": list(prev.get("batches") or [])}
    today = now.date().isoformat()
    batch = batch or {}
    searched = int(batch.get("searched") or 0)
    waiting = int(batch["waiting"]) if "waiting" in batch else None
    status = {"reported": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "queued": prev.get("queued") if waiting is None else waiting - searched,
              "last_searched": prev.get("last_searched"), "stalled_since": prev.get("stalled_since"),
              "batches": list(prev.get("batches") or [])}
    if searched:
        status["outcome"] = "searched"
        status["last_searched"] = today
        status["batches"] = (status["batches"] + [{
            "date": today, "searched": searched, "no_links": int(batch.get("no_links") or 0),
            "found": int(batch.get("found") or 0), "failed": int(batch.get("failed") or 0)}])[-KEEP_BATCHES:]
    elif waiting == 0:
        status["outcome"] = "idle"
    else:
        status["outcome"] = "blocked" if batch.get("blocked") else "failed"
    if status["outcome"] in ("searched", "idle"):
        status["stalled_since"] = None
    else:
        status["stalled_since"] = status["stalled_since"] or today
    return {"reported": status["reported"], "outcome": status["outcome"], "queued": status["queued"],
            "last_searched": status["last_searched"], "stalled_since": status["stalled_since"],
            "batches": status["batches"]}


def read_json(path) -> dict | None:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def record_run(path, batch: dict | None, skipped: bool = False) -> dict:
    status = next_status(read_json(path), now=datetime.now(timezone.utc), batch=batch, skipped=skipped)
    Path(path).write_text(json.dumps(status, indent=2) + "\n")
    print(f"discovery status: {status['outcome']}, {status['queued']} waiting"
          + (f", no one searched since {status['stalled_since']}" if status["stalled_since"] else ""))
    return status


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", required=True, help="the record to update in place")
    how = ap.add_mutually_exclusive_group(required=True)
    how.add_argument("--failed", action="store_true", help="record a run that did not get as far as reporting")
    how.add_argument("--skipped", action="store_true",
                     help="record a week in which discovery was given no searches and not run")
    args = ap.parse_args(argv)
    record_run(args.state, None, skipped=args.skipped)


if __name__ == "__main__":
    main()
