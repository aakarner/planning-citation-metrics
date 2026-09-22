"""Collect OpenAlex author metrics for every matched person.

    python -m pipeline.collect_openalex                # -> data/snapshots/openalex/<today>.csv
    python -m pipeline.collect_openalex --date 2026-10-01

One free by-id lookup per author record (OpenAlex does not meter singleton
GETs), so the monthly run costs nothing. Resumable within a day. Intended to
run from GitHub Actions on a cron; see .github/workflows/.

OpenAlex often splits one author across several records. A person's records
are every OpenAlex id accepted for them in the review queue, plus the one on
the roster row. Citations, works and i10 are summed across them -- OpenAlex
assigns each authorship to exactly one author id, so a person's own records
do not overlap -- and the h-index is the largest of the records', a lower
bound that is exact whenever the extra records are the one- and two-work
fragments they usually are. The record with the most works is the primary:
its fields go in raw_json for the change report, and its id is the one the
roster row and the site link to.
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
from .match_openalex import REVIEW_DIR
from .openalex import get_author

FIELDS = [
    "person_id", "source", "collected_at", "total_citations", "h_index", "i10_index",
    "citations_5yr", "h_index_5yr", "works_count", "raw_json",
]


def accepted_ids(people: list[dict], review_rows: list[dict]) -> dict[str, list[str]]:
    """person_id -> every OpenAlex id that is theirs: the roster row's id first,
    then any other record accepted for them in the review queue."""
    out: dict[str, list[str]] = {}
    for p in people:
        if (p.get("openalex_author_id") or "").strip():
            out[p["person_id"]] = [p["openalex_author_id"].strip()]
    for r in review_rows:
        if r.get("source") == "openalex" and r.get("status") == "accepted" and (r.get("external_id") or "").strip():
            ids = out.setdefault(r["person_id"], [])
            if r["external_id"] not in ids:
                ids.append(r["external_id"])
    return out


def aggregate(records: list[dict], year: int) -> dict:
    """One person's figures from one or more OpenAlex author records.

    Sums are safe because a work sits under exactly one author id; the h-index
    is not additive, so the largest record's stands (a lower bound). The record
    with the most works is primary and supplies the descriptive fields.
    """
    records = sorted(records, key=lambda a: -(a.get("works_count") or 0))
    primary = records[0]
    by_year: dict[int, int] = {}
    for a in records:
        for y in a.get("counts_by_year") or []:
            by_year[y.get("year", 0)] = by_year.get(y.get("year", 0), 0) + (y.get("cited_by_count") or 0)
    recent = sum(v for k, v in by_year.items() if k > year - 5)
    stats = [a.get("summary_stats") or {} for a in records]
    raw = {k: primary.get(k) for k in ("display_name", "orcid", "works_count", "cited_by_count",
                                       "summary_stats", "last_known_institutions", "counts_by_year")}
    raw["openalex_author_id"] = primary.get("_id")
    raw["openalex_author_ids"] = [a.get("_id") for a in records]
    if len(records) > 1:
        raw["records"] = [{"id": a.get("_id"), "cited_by_count": a.get("cited_by_count"),
                           "works_count": a.get("works_count"),
                           "h_index": (a.get("summary_stats") or {}).get("h_index")} for a in records]
    return {
        "total_citations": sum(a.get("cited_by_count") or 0 for a in records),
        "h_index": max((st.get("h_index") or 0) for st in stats),
        "i10_index": sum(st.get("i10_index") or 0 for st in stats),
        "citations_5yr": recent, "h_index_5yr": None,
        "works_count": sum(a.get("works_count") or 0 for a in records),
        "raw_json": json.dumps(raw, ensure_ascii=False),
    }


def load_people(roster: Path = ROSTER_DIR / "person.csv",
                review: Path = REVIEW_DIR / "identity_candidates.csv") -> list[dict]:
    """People with at least one OpenAlex record, each carrying `ids` (primary first)."""
    with roster.open(newline="", encoding="utf-8") as f:
        people = list(csv.DictReader(f))
    rows = []
    if review.exists():
        with review.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    ids = accepted_ids(people, rows)
    out = []
    for p in people:
        if p["person_id"] in ids:
            p["ids"] = ids[p["person_id"]]
            out.append(p)
    return out


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
            records = []
            for aid in p["ids"]:
                try:
                    a = get_author(aid)
                    a["_id"] = aid
                    records.append(a)
                except Exception as e:
                    print(f"  [{i}/{len(todo)}] {p['display_name']}: record {aid} FAILED ({e})", file=sys.stderr)
            if not records:
                failed += 1
                continue
            if len(records) < len(p["ids"]):
                print(f"  [{i}/{len(todo)}] {p['display_name']}: {len(records)} of {len(p['ids'])} records; "
                      "figures are partial this run", file=sys.stderr)
            row = aggregate(records, date.fromisoformat(args.date).year)
            w.writerow({"person_id": p["person_id"], "source": "openalex", "collected_at": args.date, **row})
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
