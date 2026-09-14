"""Collect Google Scholar profile metrics with the `scholarly` library.

    pip install -e ".[scholar]"
    python -m pipeline.collect_scholar --limit 20 --sleep 10     # access test
    python -m pipeline.collect_scholar                           # full run, resumable

Scholar has no API. This scrapes public profile pages one request per person,
so run it from a residential or university connection, never from a cloud
runner, and keep --sleep at 8 seconds or more. Output goes to
data/snapshots/google_scholar/<today>.csv; re-running the same day resumes,
skipping ids already written. The run stops after several consecutive
failures, which almost always means Scholar has started blocking.

Beyond totals and h-index, each profile yields citations received per
calendar year (cites_per_year), the verified email domain, and the
affiliation string; all are kept in raw_json for trend charts and
move detection.
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

FIELDS = [
    "person_id", "source", "collected_at", "total_citations", "h_index", "i10_index",
    "citations_5yr", "h_index_5yr", "works_count", "raw_json",
]
MAX_CONSECUTIVE_FAILURES = 5


def load_people() -> tuple[list[str], list[dict]]:
    with (ROSTER_DIR / "person.csv").open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames), list(r)


def save_people(fields: list[str], rows: list[dict]) -> None:
    with (ROSTER_DIR / "person.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def already_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        return {r["person_id"] for r in csv.DictReader(f)}


def fetch(scholarly, scholar_id: str) -> dict:
    author = scholarly.search_author_id(scholar_id)
    author = scholarly.fill(author, sections=["basics", "indices", "counts"])
    return author


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sleep", type=float, default=10.0, help="seconds between requests")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--ids", nargs="*", help="only these Scholar ids")
    args = ap.parse_args(argv)

    try:
        from scholarly import scholarly
    except ImportError as e:
        sys.exit(f'cannot import scholarly ({e}); run  pip install -e ".[scholar]"')

    fields, everyone = load_people()
    people = [p for p in everyone if p["google_scholar_id"]]
    if args.ids:
        people = [p for p in people if p["google_scholar_id"] in set(args.ids)]
    out = SNAPSHOT_DIR / "google_scholar" / f"{args.date}.csv"
    done = already_done(out)
    todo = [p for p in people if p["person_id"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(people)} people with Scholar ids, {len(done)} already collected today, {len(todo)} to fetch")

    out.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out.exists()
    ok = failed = consecutive = 0
    redirected: dict[str, str] = {}   # person_id -> new Scholar id
    t0 = time.monotonic()
    with out.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        for i, p in enumerate(todo, 1):
            try:
                a = fetch(scholarly, p["google_scholar_id"])
                seen = a.get("scholar_id")
                if seen and seen != p["google_scholar_id"]:
                    # Google merged or renumbered the profile; remember the new id.
                    redirected[p["person_id"]] = seen
                raw = {k: a.get(k) for k in ("scholar_id", "name", "affiliation", "email_domain", "interests", "homepage",
                                             "citedby", "citedby5y", "hindex", "hindex5y", "i10index",
                                             "i10index5y", "cites_per_year")}
                w.writerow({
                    "person_id": p["person_id"], "source": "google_scholar", "collected_at": args.date,
                    "total_citations": a.get("citedby"), "h_index": a.get("hindex"),
                    "i10_index": a.get("i10index"), "citations_5yr": a.get("citedby5y"),
                    "h_index_5yr": a.get("hindex5y"), "works_count": None,
                    "raw_json": json.dumps(raw, ensure_ascii=False),
                })
                f.flush()
                ok += 1
                consecutive = 0
                print(f"  [{i}/{len(todo)}] {p['display_name']}: {a.get('citedby')} cites, h={a.get('hindex')}, "
                      f"{a.get('email_domain') or 'no verified email'}")
            except Exception as e:
                failed += 1
                consecutive += 1
                print(f"  [{i}/{len(todo)}] {p['display_name']}: FAILED ({type(e).__name__}: {e})", file=sys.stderr)
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    print(f"{consecutive} consecutive failures; Scholar is probably blocking. Stopping.", file=sys.stderr)
                    break
            if i < len(todo):
                time.sleep(args.sleep)

    meta = {"trigger": "manual", "notes": f"scholarly collector; {ok} ok, {failed} failed, "
                                          f"{(time.monotonic() - t0) / 60:.1f} min"}
    out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    if redirected:
        taken = {q["google_scholar_id"] for q in everyone} - {p["google_scholar_id"] for p in everyone if p["person_id"] in redirected}
        for q in everyone:
            new = redirected.get(q["person_id"])
            if new and new not in taken:
                print(f"  roster: {q['display_name']} Scholar id {q['google_scholar_id']} -> {new} (profile redirected)")
                q["google_scholar_id"] = new
                taken.add(new)
            elif new:
                print(f"  roster: {q['display_name']} redirects to {new}, which another person already holds; left unchanged", file=sys.stderr)
        save_people(fields, everyone)
    print(f"\ndone: {ok} ok, {failed} failed -> {out}")


if __name__ == "__main__":
    main()
