"""Look for Google Scholar profiles for people who do not have one on file.

    pip install -e ".[scholar]"
    python -m pipeline.find_scholar_profiles --limit 20 --sleep 10    # trial
    python -m pipeline.find_scholar_profiles                          # everyone without a profile

Policy (2026-09-14): we do not reconstruct citation counts for people without
a Scholar profile (that is what Publish or Perish did, by hand). We do check
whether a profile exists that we simply never recorded, and we re-check new
hires each semester.

How it works (2026-09-15): Scholar's author search redirects to a Google
sign-in page when reached directly, and SerpApi has discontinued its author
search endpoint; SerpApi's regular Scholar search returns author names as
plain text with no profile links. The only route that still exposes profile
links is a direct publication search restricted to the author's name, fetched
from a residential or university address. Linked author names that match ours
are fetched and scored: one search page plus one profile page per plausible
candidate. Scholar blocks an address after roughly 45 requests for many hours,
so run this in batches of about 30 (--limit 30) on different days. It is
resumable, and the yield is low: on the first 20 of the 243 it found no
missing profiles, only namesakes.

  --via scrape    the working route (default).
  --via serpapi   kept for the record; exits with an explanation.

    --stale        also re-check people whose recorded Scholar id no longer resolves
                   (the collector reports these as 'empty profile'), to find the
                   renumbered profile.

Candidates are scored like OpenAlex candidates (name, affiliation text,
citation plausibility) and land in data/review/identity_candidates.csv with
source=google_scholar. Clear winners are written to person.google_scholar_id.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date

from . import ROSTER_DIR
from .openalex import _load_dotenv
from .match_openalex import (REVIEW_DIR, REVIEW_FIELDS, load_context, name_similarity, norm,
                             read_csv, write_csv)

SEARCH_URL = "https://scholar.google.com/scholar?hl=en&q=author%3A%22{name}%22"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
PROFILE_LINK = re.compile(r'<a href="/citations\?user=([\w-]{12})[^"]*"[^>]*>([^<]+)</a>')


class ScholarBlocked(RuntimeError):
    pass


def search_profile_links(name: str) -> dict[str, str]:
    """Publication search by author name -> {scholar_id: linked author text}."""
    url = SEARCH_URL.format(name=urllib.parse.quote_plus(name))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        final = resp.geturl()
        page = resp.read().decode("utf-8", "replace")
    if "accounts.google.com" in final or "/sorry/" in final or "unusual traffic" in page:
        raise ScholarBlocked(f"redirected/blocked: {final[:80]}")
    links: dict[str, str] = {}
    for sid, text in PROFILE_LINK.findall(page):
        links.setdefault(sid, html.unescape(text).strip())
    return links


_load_dotenv()


def serpapi_profiles(name: str, key: str) -> list[dict]:
    """Scholar author search via SerpApi -> candidate dicts in the scholarly shape."""
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(
        {"engine": "google_scholar_profiles", "mauthors": name, "hl": "en", "api_key": key})
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("error") and "hasn't returned any results" not in data["error"]:
        raise RuntimeError(data["error"])
    out = []
    for prof in data.get("profiles") or []:
        email = prof.get("email") or ""
        out.append({
            "scholar_id": prof.get("author_id"),
            "name": prof.get("name"),
            "affiliation": prof.get("affiliations"),
            "citedby": prof.get("cited_by"),
            "email_domain": ("@" + email.split(" at ", 1)[1].strip()) if " at " in email else None,
        })
    return out


ACCEPT_SCORE = 0.85
ACCEPT_MARGIN = 0.15
MAX_CONSECUTIVE_FAILURES = 5

# Words too generic to prove an affiliation match on their own.
GENERIC = {"university", "of", "the", "state", "college", "at", "in", "and", "institute", "school",
           "department", "planning", "urban", "professor", "assistant", "associate"}
# An affiliation line that names no institution cannot tell us the profile
# belongs to someone else, however unhelpful it is.
INSTITUTION_WORDS = ("universit", "college", "institut", "school", "polytechnic", "academy",
                     "hochschule", "universidad", "universidade", "universita", "uniwersytet",
                     "laborator", "hospital", "clinic", "centre", "center", "museum",
                     "faculty", "fakultesi", "faculte", "facultad", "department of")
# Affiliation lines that are placeholders, not information.
PLACEHOLDERS = ("unknown affiliation", "unknown", "none", "n a", "independent researcher",
                "independent scholar", "retired", "self employed")
# Fields no planning academic is appointed in. A profile naming one is someone
# else, whether or not it names an institution we recognise.
FOREIGN_FIELDS = ("pediatric", "paediatric", "oncolog", "radiolog", "cardiolog", "neurolog",
                  "psychiatr", "surgery", "surgeon", "dentist", "nursing", "pharmac",
                  "immunolog", "microbiolog", "biochem", "molecular biolog", "genetic",
                  "electrical engineering", "mechanical engineering", "chemical engineering",
                  "computer science", "astronom", "astrophys", "particle physic", "veterinar",
                  "arztin", "arzt", "medizin", "klinik")

# A profile still describing a pre-faculty stage is not the faculty member we track.
PRE_FACULTY = ("student", "estudiante", "etudiant", "doctoral candidate", "phd candidate",
               "doctorando", "doktorand", "postdoc", "post doc", "postdoctoral")

# Names a profile may use that no rule derives from our label. Grows as cases
# appear; keep each one specific enough that it cannot match another campus.
ALIASES = {
    "University at Buffalo, The State University of New York": ["SUNY Buffalo", "University at Buffalo"],
    "State University of New York at Albany": ["SUNY Albany", "University at Albany"],
    "Toronto Metropolitan University": ["Ryerson University"],          # renamed in 2022
    "University of Quebec in Montreal": ["UQAM", "Universite du Quebec a Montreal"],
}

# Label suffixes a profile can legitimately omit. Campus names are deliberately absent.
DROPPABLE_SUFFIXES = {
    "ontario",                                          # University of Waterloo, Ontario
    "the state university of new york",                 # University at Buffalo, ...
    "school of environmental biological sciences",      # Rutgers University, ...
}


def affiliation_match(candidate_affiliation: str | None, dept: dict | None) -> float:
    """1.0 if the Scholar affiliation text names the department's university."""
    if not candidate_affiliation or not dept:
        return 0.0
    words = set(norm(candidate_affiliation).split())
    padded = f" {norm(candidate_affiliation)} "
    labels = [dept.get("university"), dept.get("short_name"), dept.get("name")]
    labels += ALIASES.get(dept.get("short_name"), [])
    # Drop only suffixes that add nothing a profile would repeat: a province, a
    # system restatement, a sub-unit. Never drop a campus name, or
    # 'University of Colorado, Denver' would match a Boulder profile.
    for l in list(labels):
        if l and "," in l:
            head, _, tail = l.partition(",")
            if norm(tail) in DROPPABLE_SUFFIXES:
                labels.append(head)
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


def classify(cand: dict, depts: dict) -> str:
    """accepted / pending / rejected for one candidate profile.

    Discovery searches by name alone, so most hits are strangers who share it.
    A profile whose affiliation names an institution is self-describing: if it
    is not this person's department, it is not this person, and there is nothing
    for a reviewer to weigh. Those are rejected outright with the reason kept.
    What stays pending is the genuinely undecidable: a blank affiliation, or one
    naming a different department we do track, which would be a real find plus
    a move."""
    if cand["inst_match"] >= 1.0:
        return "accepted"
    text = norm(cand.get("last_known_institution") or "")
    if not text or text in PLACEHOLDERS:
        return "pending"                     # nothing to judge on
    if any(w in text for w in PRE_FACULTY):
        return "rejected"                    # our population is faculty
    if any(w in text for w in FOREIGN_FIELDS):
        return "rejected"                    # a field none of them works in
    for d in depts.values():
        if affiliation_match(cand["last_known_institution"], d):
            return "pending"                 # a tracked department, just not the one on file
    if not any(w in text for w in INSTITUTION_WORDS):
        # Names a role or a field but no institution, e.g. "Professor of urban
        # history, public policy & planning". Unhelpful, but it does not say
        # this is somebody else.
        return "pending"
    return "rejected"


def decide(scored: list[dict], depts: dict) -> tuple[str, list[str]]:
    """Overall outcome plus a per-candidate status."""
    if not scored:
        return "none", []
    statuses = [classify(c, depts) for c in scored]
    accepted = [i for i, st in enumerate(statuses) if st == "accepted"]
    if len(accepted) == 1:
        best = scored[accepted[0]]
        others = [c["score"] for i, c in enumerate(scored) if i != accepted[0] and statuses[i] != "rejected"]
        if (best["score"] >= ACCEPT_SCORE and best["name_sim"] >= 0.9
                and best["score"] - max(others, default=0.0) >= ACCEPT_MARGIN):
            return "accepted", statuses
    for i in accepted:
        statuses[i] = "pending"              # more than one plausible: a person decides
    if all(st == "rejected" for st in statuses):
        return "rejected", statuses
    return "pending", statuses


def reclassify() -> None:
    """Apply the current rules to Scholar rows already in the review queue."""
    pfields, persons, dept_by_id, current, totals = load_context()
    path = REVIEW_DIR / "identity_candidates.csv"
    fields, rows = read_csv(path)
    changed = 0
    for r in rows:
        if r["source"] != "google_scholar":
            continue
        if not r["external_id"]:
            if r["status"] == "pending":       # searched, nothing found
                r["status"], r["reviewed_by"] = "rejected", "matcher"
                r["reviewed_at"] = date.today().isoformat()
                changed += 1
            continue
        if r["reviewed_by"] and r["reviewed_by"] != "matcher":
            continue                                   # a person decided this; leave it
        cand = {"inst_match": float(r["inst_match"] or 0),
                "last_known_institution": r["last_known_institution"]}
        st = classify(cand, dept_by_id)
        if st != r["status"]:
            print(f"  {r['display_name'][:26]:<27}{str(r['candidate_name'])[:24]:<25}"
                  f"{r['status']} -> {st}   ({str(r['last_known_institution'])[:40]})")
            r["status"] = st
            r["reviewed_by"] = "matcher" if st in ("accepted", "rejected") else None
            r["reviewed_at"] = date.today().isoformat() if st in ("accepted", "rejected") else None
            changed += 1
    write_csv(path, REVIEW_FIELDS, rows)
    print(f"\n{changed} rows reclassified in {path}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sleep", type=float, default=10.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reclassify", action="store_true",
                    help="re-apply the accept/reject rules to rows already in the review queue, "
                         "from their stored evidence; makes no network calls")
    ap.add_argument("--via", choices=["serpapi", "scrape"], default="scrape")
    ap.add_argument("--stale", metavar="ID,ID,...", default="",
                    help="also re-check the people holding these Scholar ids (no longer resolving); "
                         "comma-separated, written as --stale=... because ids can start with '-'")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # progress lines show up in logs as they happen
    if args.reclassify:
        reclassify()
        return

    scholarly = None
    key = os.environ.get("SERPAPI_KEY")
    if args.via == "serpapi":
        sys.exit("SerpApi discontinued its Scholar author-search endpoint, and its regular Scholar "
                 "search omits profile links (checked 2026-09-15). Use --via scrape from a home or "
                 "campus address, about 30 people per day.")
    else:
        try:
            from scholarly import scholarly
        except ImportError as e:
            sys.exit(f'cannot import scholarly ({e}); run  pip install -e ".[scholar]"')
    print(f"backend: {args.via}")

    pfields, persons, dept_by_id, current, totals = load_context()
    stale = {x.strip() for x in args.stale.split(",") if x.strip()}
    review_path = REVIEW_DIR / "identity_candidates.csv"
    existing = read_csv(review_path)[1] if review_path.exists() else []
    searched = {r["person_id"] for r in existing if r["source"] == "google_scholar"}

    todo = [p for p in persons
            if (not p.get("google_scholar_id") or p.get("google_scholar_id") in stale)
            and p["person_id"] not in searched]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{sum(1 for p in persons if not p.get('google_scholar_id'))} people without a Scholar id, "
          f"{len(searched)} already searched, {len(todo)} to search")

    rows, tally, consecutive = [], {"accepted": 0, "pending": 0, "rejected": 0, "none": 0, "failed": 0}, 0
    for i, person in enumerate(todo, 1):
        aff = current.get(person["person_id"])
        dept = dept_by_id.get(aff["department_id"]) if aff else None
        try:
            if args.via == "serpapi":
                found = serpapi_profiles(person["display_name"], key)
                links = {c["scholar_id"]: c["name"] for c in found}
            else:
                links = search_profile_links(person["display_name"])
                # Only fetch profiles whose linked name could be this person ("N Brooks" counts).
                plausible = [sid for sid, text in links.items()
                             if name_similarity(person, {"display_name": text}) >= 0.9]
                found = []
                for sid in plausible[:4]:
                    time.sleep(args.sleep / 2)
                    a = scholarly.search_author_id(sid)
                    found.append({"scholar_id": sid, "name": a.get("name"), "affiliation": a.get("affiliation"),
                                  "citedby": a.get("citedby"), "email_domain": a.get("email_domain")})
            # A stale id must never be re-accepted.
            found = [c for c in found if c.get("scholar_id") and c["scholar_id"] not in stale]
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
        status, statuses = decide(scored, dept_by_id)
        tally[status] = tally.get(status, 0) + 1
        if status == "accepted":
            person["google_scholar_id"] = scored[0]["external_id"]
        elif person.get("google_scholar_id") in stale and not args.dry_run:
            person["google_scholar_id"] = None      # the old id is dead either way
            person["notes"] = ((person.get("notes") or "") + f" Scholar id {list(stale & {person['google_scholar_id']}) or ''} stopped resolving {date.today().isoformat()}.").strip()
        for rank, s in enumerate(scored):
            st = statuses[rank]
            settled = st in ("accepted", "rejected")
            rows.append({"person_id": person["person_id"], "display_name": person["display_name"],
                         "department": dept["short_name"] if dept else None, "source": "google_scholar",
                         "status": st,
                         "reviewed_by": "matcher" if settled else None,
                         "reviewed_at": date.today().isoformat() if settled else None, **s})
        if not scored:
            # Searched, nothing found. A settled outcome, not review work: the row
            # exists so the next run skips this person, not so someone reads it.
            rows.append({"person_id": person["person_id"], "display_name": person["display_name"],
                         "department": dept["short_name"] if dept else None, "source": "google_scholar",
                         "external_id": None, "candidate_name": None, "score": 0,
                         "status": "rejected", "reviewed_by": "matcher",
                         "reviewed_at": date.today().isoformat()})
        print(f"  [{i}/{len(todo)}] {person['display_name']}: {len(links)} linked authors, "
              f"{len(found)} plausible -> {status}"
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
