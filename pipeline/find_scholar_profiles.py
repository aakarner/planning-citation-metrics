"""Look for Google Scholar profiles for people who do not have one on file.

    pip install -e ".[scholar]"
    python -m pipeline.find_scholar_profiles --limit 20 --sleep 10    # trial
    python -m pipeline.find_scholar_profiles                          # everyone without a profile

Policy (2026-09-14): we do not reconstruct citation counts for people without
a Scholar profile (that is what Publish or Perish did, by hand). We do check
whether a profile exists that we simply never recorded, and we re-check new
hires each semester. One Scholar author-search page per person; run it from a
university or home connection, never a cloud runner.

Candidates are scored like OpenAlex candidates (name, affiliation text,
citation plausibility) and land in data/review/identity_candidates.csv with
source=google_scholar. Clear winners are written to person.google_scholar_id.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date

from . import ROSTER_DIR
from .match_openalex import (REVIEW_DIR, REVIEW_FIELDS, load_context, name_similarity, norm,
                             read_csv, write_csv)

ACCEPT_SCORE = 0.85
ACCEPT_MARGIN = 0.15
MAX_CONSECUTIVE_FAILURES = 5

# Words too generic to prove an affiliation match on their own.
GENERIC = {"university", "of", "the", "state", "college", "at", "in", "and", "institute", "school",
           "department", "planning", "urban", "professor", "assistant", "associate"}


def affiliation_match(candidate_affiliation: str | None, dept: dict | None) -> float:
    """1.0 if the Scholar affiliation text names the department's university."""
    if not candidate_affiliation or not dept:
        return 0.0
    words = set(norm(candidate_affiliation).split())
    padded = f" {norm(candidate_affiliation)} "
    labels = [dept.get("university"), dept.get("short_name"), dept.get("name")]
    # Also try each label without a trailing qualifier: 'University of Waterloo, Ontario'.
    labels += [l.split(",")[0] for l in labels if l and "," in l]
    for label in labels:
        lab = norm(label)
        if lab and f" {lab} " in padded:          # whole label present as words ('ucla', 'mit')
            return 1.0
        toks = {t for t in lab.split() if t not in GENERIC and len(t) > 2}
        if len(toks) >= 2 and toks <= words:       # all distinctive words present ('arizona','state' is too generic; 'alabama','a&m'...)
            return 1.0
    return 0.0


def cites_plausibility(candidate_cites, ours) -> tuple[float, float | None]:
    if not ours or candidate_cites is None:
        return 0.5, None
    ratio = (candidate_cites or 0) / ours
    # Publish or Perish and a profile both count Scholar citations, so they
    # should be close; a profile with a tenth or ten times the number is
    # probably someone else.
    return (1.0 if 0.5 <= ratio <= 2.0 else 0.5 if 0.2 <= ratio <= 5 else 0.0), round(ratio, 3)


def score(person: dict, c: dict, dept: dict | None, ours: int | None) -> dict:
    ns = name_similarity(person, {"display_name": c.get("name")})
    am = affiliation_match(c.get("affiliation"), dept)
    cs, ratio = cites_plausibility(c.get("citedby"), ours)
    return {
        "external_id": c.get("scholar_id"),
        "candidate_name": c.get("name"),
        "score": round(0.5 * ns + 0.35 * am + 0.15 * cs, 3),
        "name_sim": ns, "inst_match": am, "topic_share": None, "cites_ratio": ratio,
        "works_count": None, "cited_by_count": c.get("citedby"), "h_index": None,
        "last_known_institution": c.get("affiliation"), "orcid": None,
    }


def decide(scored: list[dict]) -> str:
    if not scored:
        return "none"
    best = scored[0]
    runner = scored[1]["score"] if len(scored) > 1 else 0.0
    if (best["score"] >= ACCEPT_SCORE and best["name_sim"] >= 0.9 and best["inst_match"] >= 1.0
            and best["score"] - runner >= ACCEPT_MARGIN):
        return "accepted"
    return "pending"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sleep", type=float, default=10.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        from scholarly import scholarly
    except ImportError:
        sys.exit('scholarly is not installed: run  pip install -e ".[scholar]"')

    pfields, persons, dept_by_id, current, totals = load_context()
    review_path = REVIEW_DIR / "identity_candidates.csv"
    existing = read_csv(review_path)[1] if review_path.exists() else []
    searched = {r["person_id"] for r in existing if r["source"] == "google_scholar"}

    todo = [p for p in persons if not p.get("google_scholar_id") and p["person_id"] not in searched]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{sum(1 for p in persons if not p.get('google_scholar_id'))} people without a Scholar id, "
          f"{len(searched)} already searched, {len(todo)} to search")

    rows, tally, consecutive = [], {"accepted": 0, "pending": 0, "none": 0, "failed": 0}, 0
    for i, person in enumerate(todo, 1):
        aff = current.get(person["person_id"])
        dept = dept_by_id.get(aff["department_id"]) if aff else None
        try:
            found = list(scholarly.search_author(person["display_name"]))
            consecutive = 0
        except Exception as e:
            tally["failed"] += 1
            consecutive += 1
            print(f"  [{i}/{len(todo)}] {person['display_name']}: FAILED ({type(e).__name__}: {e})", file=sys.stderr)
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                print("Scholar appears to be blocking; stopping. Partial results are kept.", file=sys.stderr)
                break
            continue
        scored = sorted((score(person, c, dept, totals.get(person["person_id"])) for c in found),
                        key=lambda s: -s["score"])[:5]
        status = decide(scored)
        tally[status] += 1
        if status == "accepted":
            person["google_scholar_id"] = scored[0]["external_id"]
        for rank, s in enumerate(scored):
            acc = status == "accepted" and rank == 0
            rows.append({"person_id": person["person_id"], "display_name": person["display_name"],
                         "department": dept["short_name"] if dept else None, "source": "google_scholar",
                         "status": "accepted" if acc else "pending",
                         "reviewed_by": "matcher" if acc else None,
                         "reviewed_at": date.today().isoformat() if acc else None, **s})
        if not scored:
            rows.append({"person_id": person["person_id"], "display_name": person["display_name"],
                         "department": dept["short_name"] if dept else None, "source": "google_scholar",
                         "external_id": None, "candidate_name": None, "score": 0, "status": "pending"})
        print(f"  [{i}/{len(todo)}] {person['display_name']}: {len(found)} candidates -> {status}"
              + (f" ({scored[0]['candidate_name']}, {scored[0]['last_known_institution']})" if scored else ""))
        if i < len(todo):
            time.sleep(args.sleep)

    print("\nsummary:", tally)
    if args.dry_run:
        return
    write_csv(review_path, REVIEW_FIELDS, existing + rows)
    write_csv(ROSTER_DIR / "person.csv", pfields, persons)
    print(f"wrote {review_path} and updated person.csv")


if __name__ == "__main__":
    main()
