"""Collect OpenAlex author metrics for every matched person.

    python -m pipeline.collect_openalex                # -> data/snapshots/openalex/<today>.csv
    python -m pipeline.collect_openalex --date 2026-10-01

One free by-id lookup per person (OpenAlex does not meter singleton GETs), so
the monthly run costs nothing. Resumable within a day. Intended to run from
GitHub Actions on a cron; see .github/workflows/.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date
from pathlib import Path

from . import ROSTER_DIR, SNAPSHOT_DIR
from .openalex import get_author

FIELDS = [
    "person_id", "source", "collected_at", "total_citations", "h_index", "i10_index",
    "citations_5yr", "h_index_5yr", "works_count", "raw_json",
]


def load_people() -> list[dict]:
    with (ROSTER_DIR / "person.csv").open(newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["openalex_author_id"]]


def already_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        return {r["person_id"] for r in csv.DictReader(f)}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # progress lines show up in logs as they happen

    people = load_people()
    out = SNAPSHOT_DIR / "openalex" / f"{args.date}.csv"
    done = already_done(out)
    todo = [p for p in people if p["person_id"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(people)} matched people, {len(done)} already collected for {args.date}, {len(todo)} to fetch")

    out.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out.exists()
    ok = failed = 0
    t0 = time.monotonic()
    with out.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        for i, p in enumerate(todo, 1):
            try:
                a = get_author(p["openalex_author_id"])
            except Exception as e:
                failed += 1
                print(f"  [{i}/{len(todo)}] {p['display_name']}: FAILED ({e})", file=sys.stderr)
                continue
            stats = a.get("summary_stats") or {}
            by_year = a.get("counts_by_year") or []
            this_year = date.fromisoformat(args.date).year
            recent = sum(y.get("cited_by_count", 0) for y in by_year if y.get("year", 0) > this_year - 5)
            raw = {k: a.get(k) for k in ("display_name", "orcid", "works_count", "cited_by_count",
                                         "summary_stats", "last_known_institutions", "counts_by_year")}
            raw["openalex_author_id"] = p["openalex_author_id"]
            w.writerow({
                "person_id": p["person_id"], "source": "openalex", "collected_at": args.date,
                "total_citations": a.get("cited_by_count"), "h_index": stats.get("h_index"),
                "i10_index": stats.get("i10_index"), "citations_5yr": recent, "h_index_5yr": None,
                "works_count": a.get("works_count"), "raw_json": json.dumps(raw, ensure_ascii=False),
            })
            f.flush()
            ok += 1
            if i % 100 == 0 or i == len(todo):
                print(f"  [{i}/{len(todo)}] ok {ok} failed {failed}")

    meta = {"trigger": "schedule", "notes": f"OpenAlex collector; {ok} ok, {failed} failed, "
                                            f"{(time.monotonic() - t0) / 60:.1f} min"}
    out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"\ndone: {ok} ok, {failed} failed -> {out}")
    if failed and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
