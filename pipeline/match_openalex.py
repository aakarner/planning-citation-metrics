"""Match each person to an OpenAlex author id, with a human review queue.

    OPENALEX_API_KEY=... python -m pipeline.match_openalex            # everyone unmatched
    OPENALEX_API_KEY=... python -m pipeline.match_openalex --limit 50  # trial run
    python -m pipeline.match_openalex --rescore                        # re-score from cache, no API calls

For each person without an openalex_author_id:
  1. look up candidate authors by name (cheap display_name filter; full-text
     search as a fallback when that returns nothing);
  2. score each candidate on name similarity, institution match against the
     department's OpenAlex institution id (or the PhD institution), topic
     overlap with planning-adjacent fields, and whether OpenAlex's citation
     count is plausible next to the Google Scholar number we already hold;
  3. auto-accept a clear winner; otherwise leave the top candidates 'pending'
     in data/review/identity_candidates.csv for a person to resolve.

Accepted ids are written back to data/roster/person.csv. Re-running is safe:
matched people are skipped and API responses are cached under build/cache/.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

from . import DATA_DIR, ROSTER_DIR, SNAPSHOT_DIR
from .openalex import filter_authors, search_authors, short_id

REVIEW_DIR = DATA_DIR / "review"
REVIEW_FIELDS = [
    "person_id", "display_name", "department", "source", "external_id", "candidate_name",
    "score", "name_sim", "inst_match", "topic_share", "cites_ratio", "works_count",
    "cited_by_count", "h_index", "last_known_institution", "orcid", "status", "reviewed_by",
    "reviewed_at", "notes",
]

# Topic subfield/field names that make a candidate look like a planning scholar.
PLANNING_WORDS = (
    "urban", "planning", "geography", "transport", "environmental", "sociolog", "political",
    "public administration", "architecture", "development", "economics", "demograph",
    "health", "social", "law", "history", "civil", "ecology", "energy", "policy", "housing",
)

# Decision thresholds. Tuned on the first full run; see docs/PLAN.md.
ACCEPT_SCORE = 0.75
ACCEPT_NAME = 0.90
ACCEPT_MARGIN = 0.15
KEEP_TOP = 5


# ------------------------------------------------------------------ names

def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z ]+", " ", s.replace("-", " "))
    return re.sub(r"\s+", " ", s).strip()


def jaro_winkler(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    win = max(len(a), len(b)) // 2 - 1
    ma = [False] * len(a)
    mb = [False] * len(b)
    m = 0
    for i, ca in enumerate(a):
        for j in range(max(0, i - win), min(len(b), i + win + 1)):
            if not mb[j] and b[j] == ca:
                ma[i] = mb[j] = True
                m += 1
                break
    if m == 0:
        return 0.0
    t = 0
    k = 0
    for i, matched in enumerate(ma):
        if matched:
            while not mb[k]:
                k += 1
            if a[i] != b[k]:
                t += 1
            k += 1
    jaro = (m / len(a) + m / len(b) + (m - t / 2) / m) / 3
    prefix = 0
    for ca, cb in zip(a, b):
        if ca != cb or prefix == 4:
            break
        prefix += 1
    return jaro + prefix * 0.1 * (1 - jaro)


def name_similarity(person: dict, candidate: dict) -> float:
    ours_full = norm(person["display_name"])
    ours_last = norm(person["last_name"])
    ours_first = norm(person["first_name"])
    variants = [candidate.get("display_name")] + list(candidate.get("display_name_alternatives") or [])
    best = 0.0
    any_last_ok = False
    for v in variants:
        v_norm = norm(v)
        if not v_norm:
            continue
        parts = v_norm.split()
        full = jaro_winkler(ours_full, v_norm)
        # Last name must really match; first name may be an initial.
        last_ok = bool(ours_last) and (ours_last in v_norm)
        any_last_ok = any_last_ok or last_ok
        first_ok = ours_first and (parts[0] == ours_first or parts[0][:1] == ours_first[:1])
        structured = 1.0 if (last_ok and parts and parts[0] == ours_first) else 0.92 if (last_ok and first_ok) else 0.0
        best = max(best, full, structured)
    if not any_last_ok:
        best = min(best, 0.6)  # 'Guang Li' must not look like 'Guang Tian'
    return round(best, 3)


# ------------------------------------------------------------------ scoring

def institution_match(candidate: dict, dept_inst_id: str | None, phd_inst: str | None) -> float:
    inst_ids = set()
    inst_names = []
    for a in candidate.get("affiliations") or []:
        inst = a.get("institution") or {}
        inst_ids.add(short_id(inst.get("id")))
        inst_names.append(norm(inst.get("display_name")))
    for inst in candidate.get("last_known_institutions") or []:
        inst_ids.add(short_id(inst.get("id")))
        inst_names.append(norm(inst.get("display_name")))
    if dept_inst_id and dept_inst_id in inst_ids:
        return 1.0
    if phd_inst:
        p = norm(phd_inst)
        for n in inst_names:
            if p and (p in n or n in p or jaro_winkler(p, n) > 0.9):
                return 0.5
    return 0.0


def topic_share(candidate: dict) -> float:
    topics = candidate.get("topics") or []
    if not topics:
        return 0.5  # unknown, neutral
    total = sum(t.get("count", 0) for t in topics) or 1
    hit = 0
    for t in topics:
        labels = " ".join(norm((t.get(k) or {}).get("display_name")) for k in ("subfield", "field")) + " " + norm(t.get("display_name"))
        if any(w in labels for w in PLANNING_WORDS):
            hit += t.get("count", 0)
    return round(hit / total, 3)


def cites_plausibility(candidate: dict, gs_total: int | None) -> tuple[float, float | None]:
    oa = candidate.get("cited_by_count") or 0
    if not gs_total:
        return 0.5, None
    ratio = oa / gs_total
    # OpenAlex usually reports fewer citations than Scholar, but not absurdly fewer,
    # and rarely more than ~1.5x.
    score = 1.0 if 0.1 <= ratio <= 1.5 else 0.5 if 0.03 <= ratio <= 3 else 0.0
    return score, round(ratio, 3)


def score_candidate(person: dict, candidate: dict, dept_inst_id, gs_total) -> dict:
    ns = name_similarity(person, candidate)
    im = institution_match(candidate, dept_inst_id, person.get("phd_institution"))
    ts = topic_share(candidate)
    cs, ratio = cites_plausibility(candidate, gs_total)
    score = 0.45 * ns + 0.30 * im + 0.15 * ts + 0.10 * cs
    lki = (candidate.get("last_known_institutions") or [{}])[0].get("display_name")
    return {
        "external_id": short_id(candidate["id"]),
        "candidate_name": candidate.get("display_name"),
        "score": round(score, 3), "name_sim": ns, "inst_match": im, "topic_share": ts,
        "cites_ratio": ratio, "works_count": candidate.get("works_count"),
        "cited_by_count": candidate.get("cited_by_count"),
        "h_index": (candidate.get("summary_stats") or {}).get("h_index"),
        "last_known_institution": lki, "orcid": candidate.get("orcid"),
    }


STUB_WORKS = 5
STUB_CITES = 50


def is_stub(c: dict) -> bool:
    """A near-empty OpenAlex author record: almost always a split of the real
    profile (same name, same institution, one or two works, no citations)."""
    return (c.get("works_count") or 0) <= STUB_WORKS and (c.get("cited_by_count") or 0) < STUB_CITES


def decide(scored: list[dict]) -> str:
    """'accepted' for a clear winner, else 'pending'.

    The winner must match on name and institution with a plausible citation
    count, and beat every *substantive* runner-up by a margin. Stub records are
    ignored when measuring the margin; a real second profile keeps the case
    pending for a person to look at."""
    if not scored:
        return "none"
    best = scored[0]
    if is_stub(best):
        return "pending"
    runner = max((c["score"] for c in scored[1:] if not is_stub(c)), default=0.0)
    if (best["score"] >= ACCEPT_SCORE and best["name_sim"] >= ACCEPT_NAME
            and best["inst_match"] >= 1.0 and best["score"] - runner >= ACCEPT_MARGIN):
        return "accepted"
    return "pending"


# ------------------------------------------------------------------ io

def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames), [{k: (v if v != "" else None) for k, v in row.items()} for row in r]


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    """Write via a temp file and replace, so a failure part-way through cannot
    leave a truncated roster or review queue behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})
    tmp.replace(path)


def load_context():
    pfields, persons = read_csv(ROSTER_DIR / "person.csv")
    _, depts = read_csv(ROSTER_DIR / "department.csv")
    _, affs = read_csv(ROSTER_DIR / "affiliation.csv")
    dept_by_id = {d["department_id"]: d for d in depts}
    current = {a["person_id"]: a for a in affs if a["end_date"] is None and a["is_primary"] == "1"}
    gs_total = {}
    for path in sorted(SNAPSHOT_DIR.glob("*/*.csv")):
        if path.parent.name in ("google_scholar", "pop"):
            for r in read_csv(path)[1]:
                if r["total_citations"]:
                    gs_total[r["person_id"]] = int(r["total_citations"])
    return pfields, persons, dept_by_id, current, gs_total


def fetch_candidates(person: dict) -> list[dict]:
    name = person["display_name"]
    results = filter_authors(name)
    if not results:
        results = search_authors(name)
    if not results and person.get("middle_name"):
        results = filter_authors(f"{person['first_name']} {person['last_name']}")
    return results


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="only process the first N unmatched people")
    ap.add_argument("--rescore", action="store_true", help="re-score everyone from cached responses only")
    ap.add_argument("--dry-run", action="store_true", help="do not write person.csv or the review file")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # progress lines show up in logs as they happen

    pfields, persons, dept_by_id, current, gs_total = load_context()
    review_path = REVIEW_DIR / "identity_candidates.csv"
    existing = read_csv(review_path)[1] if review_path.exists() else []
    # Keep human decisions from earlier runs.
    decided = {(r["person_id"], r["external_id"]): r for r in existing if r["status"] != "pending" and r.get("reviewed_by")}

    rejected: dict[str, set] = {}
    for r in existing:
        if r["source"] == "openalex" and r["status"] == "rejected" and r["external_id"]:
            rejected.setdefault(r["person_id"], set()).add(r["external_id"])

    review_rows: list[dict] = []
    tally = {"accepted": 0, "pending": 0, "none": 0, "already": 0, "failed": 0}
    todo = [p for p in persons if not p.get("openalex_author_id")]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(persons)} people, {len(todo)} to match")

    for i, person in enumerate(todo, 1):
        aff = current.get(person["person_id"])
        dept = dept_by_id.get(aff["department_id"]) if aff else None
        dept_inst = dept["openalex_institution_id"] if dept else None
        try:
            candidates = fetch_candidates(person)
        except Exception as e:  # rate limit exhausted, network, etc.
            print(f"  [{i}/{len(todo)}] {person['display_name']}: fetch failed ({e}); stopping", file=sys.stderr)
            tally["failed"] += 1
            break
        veto = rejected.get(person["person_id"], set())
        scored = sorted(
            (score_candidate(person, c, dept_inst, gs_total.get(person["person_id"]))
             for c in candidates if short_id(c["id"]) not in veto),
            key=lambda s: -s["score"],
        )[:KEEP_TOP]
        status = decide(scored)
        tally[status] += 1
        if status == "accepted":
            person["openalex_author_id"] = scored[0]["external_id"]
            if scored[0].get("orcid") and not person.get("orcid"):
                person["orcid"] = scored[0]["orcid"].rsplit("/", 1)[-1]
        for rank, s in enumerate(scored):
            row = {"person_id": person["person_id"], "display_name": person["display_name"],
                   "department": dept["short_name"] if dept else None, "source": "openalex",
                   "status": "accepted" if (status == "accepted" and rank == 0) else "pending",
                   "reviewed_by": "matcher" if (status == "accepted" and rank == 0) else None,
                   "reviewed_at": date.today().isoformat() if (status == "accepted" and rank == 0) else None,
                   **s}
            prior = decided.get((person["person_id"], s["external_id"]))
            if prior:
                row.update({k: prior[k] for k in ("status", "reviewed_by", "reviewed_at")})
                if prior["status"] == "accepted":
                    person["openalex_author_id"] = s["external_id"]
            review_rows.append(row)
        if status == "none":
            review_rows.append({"person_id": person["person_id"], "display_name": person["display_name"],
                                "department": dept["short_name"] if dept else None, "source": "openalex",
                                "external_id": None, "candidate_name": None, "score": 0, "status": "pending"})
        if i % 50 == 0 or i == len(todo):
            print(f"  [{i}/{len(todo)}] accepted {tally['accepted']}  pending {tally['pending']}  none {tally['none']}")

    # Carry forward rows for people not processed this run.
    processed = {p["person_id"] for p in todo}
    carried = [r for r in existing
               if r["person_id"] not in processed or r["source"] != "openalex"
               or r["status"] == "rejected"]
    all_rows = carried + review_rows

    print("\nsummary:", json.dumps(tally))
    if args.dry_run:
        print("dry run: nothing written")
        return
    write_csv(review_path, REVIEW_FIELDS, all_rows)
    write_csv(ROSTER_DIR / "person.csv", pfields, persons)
    print(f"wrote {review_path} ({len(all_rows)} rows) and updated person.csv")


if __name__ == "__main__":
    main()
