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
from .openalex import get as oa_get
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


STALE_WORKS_RATIO = 1.25   # the works index holding this many more works than the author record says
STALE_WORKS_MIN = 5        # ...and at least this many more, before the record's totals are distrusted


def works_index_count(author_id: str) -> int:
    """How many works the works index files under this author right now."""
    return (oa_get("/works", {"filter": f"author.id:{author_id}", "per-page": 1, "select": "id"},
                   use_cache=False).get("meta") or {}).get("count") or 0


def fetch_works(author_id: str) -> list[dict]:
    out, cursor = [], "*"
    while cursor:
        page = oa_get("/works", {"filter": f"author.id:{author_id}", "per-page": 200,
                                 "select": "id,cited_by_count,publication_year,counts_by_year", "cursor": cursor}, use_cache=False)
        out.extend(page.get("results") or [])
        cursor = (page.get("meta") or {}).get("next_cursor") if page.get("results") else None
    return out


def figures_from_works(works: list[dict]) -> dict:
    """cited_by_count, works_count, summary_stats and counts_by_year recomputed from the works themselves."""
    cites = sorted((w.get("cited_by_count") or 0 for w in works), reverse=True)
    h = sum(1 for i, c in enumerate(cites, 1) if c >= i)
    by_year: dict[int, int] = {}
    for w in works:
        for y in w.get("counts_by_year") or []:
            by_year[y["year"]] = by_year.get(y["year"], 0) + (y.get("cited_by_count") or 0)
    return {"cited_by_count": sum(cites), "works_count": len(works),
            "summary_stats": {"h_index": h, "i10_index": sum(1 for c in cites if c >= 10)},
            "counts_by_year": [{"year": y, "cited_by_count": c} for y, c in sorted(by_year.items(), reverse=True)]}


def reconcile_with_works(record: dict, index_count: int | None = None, works: list[dict] | None = None) -> dict:
    """Replace an author record's totals with figures summed from its works when
    the works index disagrees with the record.

    OpenAlex's author-level statistics can lag its works index: one record
    reported 12 works and 51 citations while 31 works, with 660 citations
    between them, were filed under it (2026-10-15). In a sample of 30 of our
    authors the two agreed for 29, so this costs one extra call per record and
    the second lookup only when it is needed. The record is marked so the
    stored payload says where the figures came from.
    """
    aid = record.get("_id") or (record.get("id") or "").rsplit("/", 1)[-1]
    if index_count is None:
        index_count = works_index_count(aid)
    said = record.get("works_count") or 0
    if index_count < STALE_WORKS_MIN or index_count < max(said * STALE_WORKS_RATIO, said + STALE_WORKS_MIN):
        return record
    if works is None:
        works = fetch_works(aid)
    fixed = dict(record)
    fixed.update(figures_from_works(works))
    fixed["_reconciled"] = {"author_record_said": {"works_count": said, "cited_by_count": record.get("cited_by_count")},
                            "works_index": index_count}
    return fixed


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
    if any(a.get("_reconciled") for a in records):
        raw["reconciled_from_works"] = {a.get("_id"): a["_reconciled"] for a in records if a.get("_reconciled")}
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
    ap.add_argument("--ids", help="comma-separated person_ids: a targeted fetch for people just matched")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # progress lines show up in logs as they happen

    people = load_people()
    if args.ids:
        want = set(args.ids.split(","))
        people = [p for p in people if p["person_id"] in want]
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
                    a = reconcile_with_works(a)      # author totals can lag the works index
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

    meta = {"trigger": "manual" if args.ids else "schedule",
            "notes": f"OpenAlex collector{' (targeted)' if args.ids else ''}; {ok} ok, {failed} failed, "
                                            f"{(time.monotonic() - t0) / 60:.1f} min"}
    out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"\ndone: {ok} ok, {failed} failed -> {out}")
    if failed and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
