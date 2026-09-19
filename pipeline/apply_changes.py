"""Apply affiliation changes from the latest Scholar snapshot to the roster.

    python -m pipeline.apply_changes --dry-run     # print every decision, change nothing
    python -m pipeline.apply_changes               # write data/roster/affiliation.csv

Policy (2026-09-15): trust the Scholar profile. Corrections can come in later
through the site. For each person with a current appointment and a Scholar row
in the latest run:

  1. profile affiliation text names the current department  -> no change
     (a stale verified email alone never triggers anything)
  2. affiliation text names another tracked department      -> MOVE there
  3. verified email belongs to another tracked department,
     and the affiliation text does not contradict it        -> MOVE there
     (the text may name that department with one character wrong: a profile
      reading "University of Mew Mexico" with an @unm.edu address is a move,
      not a departure)
  4. affiliation text names an institution we don't track,
     and the email domain is not the current department's  -> DEPART
     (appointment closed, destination kept in the note)
  5. anything else                                          -> no change

A profile with fewer than THIN_CITES citations for someone THIN_YEARS or more
past the PhD is reported as a probable namesake and never acted on, unless it
names the current department.

Rank comes from the affiliation text when it says Assistant/Associate/
Professor, otherwise the existing rank carries over. Nothing is deleted:
the old appointment is closed with end_date = the snapshot date, and the
source column records the evidence.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from . import ROSTER_DIR
from .detect_changes import latest_two, registrable, rows
from .find_scholar_profiles import affiliation_match, affiliation_names_dept
from .match_openalex import norm

# Stems, not words, so the same concept is caught in more than one language:
# "polytechni" covers Polytechnic and Polytechnique, "institut" covers Institute
# and Institut, "universit" covers University, Universite and Universidad.
# Owen Waygood's "Professor, Polytechnique Montreal" passed as no institution
# at all under a list that only knew the English spellings.
INSTITUTION_WORDS = ("universit", "college", "institut", "school", "polytechni", "academy",
                     "ecole", "\u00e9cole", "hochschule", "facult", "escuela", "scuola")
# A profile that still describes a pre-faculty stage predates the appointment we track: leave it.
PRE_FACULTY = ("student", "candidate", "postdoc", "post-doc", "postdoctoral", "fellow", "research assistant")
# Fields no planning faculty member is appointed in: the profile belongs to a namesake.
FOREIGN_FIELDS = ("electrical engineering", "computer science", "biochem", "chemistry", "physics", "medicine",
                  "medical", "biology", "neuroscience", "mathematics", "dentistry", "nursing", "pharmac")
# Tracked-department names that are prefixes of campuses we do not track.
UNTRACKED_CAMPUS = ("tacoma", "bothell", "dearborn", "flint", "duluth", "omaha", "kearney")
RANK_ORDER = {"assistant": 0, "associate": 1, "full": 2}
# A profile with almost no citations for someone years past the PhD is more
# likely a namesake than a record of their career. Jenny Liu's inherited id
# pointed at a Guelph entomologist with 6 citations, and the rules moved her
# there. Such a profile is reported for a person to look at, never acted on.
THIN_CITES = 25
THIN_YEARS = 5


def email_home_check(email, home, a) -> bool:
    return email in home[a["department_id"]]


def rank_from_text(text: str, default: str) -> str:
    t = re.sub(r"\bassoc\.?\b", "associate", re.sub(r"\bass(is)?t\.?\b", "assistant", text.lower()))
    if "assistant professor" in t or "assistant prof" in t:
        return "assistant"
    if "associate professor" in t or "associate prof" in t:
        return "associate"
    if re.search(r"\bprofessor\b", t) and "assistant" not in t and "associate" not in t:
        return "full"
    return default


def decide(text, email, rank, cur, depts, home, domain_owner, cites=None, years_since_phd=None):
    """One profile against one appointment -> (action, destination).

    Returns (None, None) to leave the appointment alone. Kept separate from
    main() so the rules can be tested: three wrong departures reached the site
    in September 2026 because this logic only ran against live data.

    `rank` is the rank on the appointment, `cur` the department on file, `home`
    its set of email domains, and `domain_owner` maps a domain to the department
    that owns it. `cites` and `years_since_phd`, when known, let a thin profile
    be reported instead of trusted.
    """
    low = text.lower()
    if any(w in low for w in PRE_FACULTY):
        return None, None                                  # stale pre-appointment profile
    if (cites is not None and years_since_phd is not None
            and cites < THIN_CITES and years_since_phd >= THIN_YEARS
            and not (text and affiliation_names_dept(text, cur))):
        return "wrong", None                               # too thin to move anyone on; probably a namesake
    # Typo-tolerant here on purpose: a profile that misspells its own school
    # must not read as a departure from it. Initiating a move still needs an
    # exact match below.
    at_current = bool(text) and affiliation_names_dept(text, cur) and not any(c in low for c in UNTRACKED_CAMPUS)
    if not at_current and any(w in low for w in FOREIGN_FIELDS):
        return "wrong", None                               # namesake's profile: report, do not apply
    if at_current:
        promoted = RANK_ORDER.get(rank_from_text(text, rank), -1) > RANK_ORDER.get(rank, -1)
        return ("promote", cur) if promoted else (None, None)   # rule 1, plus promotions
    email_home = bool(email) and email in home
    if any(c in low for c in UNTRACKED_CAMPUS) and email and not email_home:
        return "depart", None
    target = None
    if text:
        hits = [d for did, d in depts.items() if did != cur["department_id"] and affiliation_match(text, d)]
        if len(hits) == 1:
            target = hits[0]                               # rule 2
    if target is None and email and not email_home:
        did = domain_owner.get(email)
        if did and did != cur["department_id"] and (not text or not any(w in low for w in INSTITUTION_WORDS)
                                                    or affiliation_names_dept(text, depts[did])):
            target = depts[did]                            # rule 3
    if target is not None:
        return "move", target
    if text and any(w in low for w in INSTITUTION_WORDS) and email and not email_home:
        return "depart", None                              # rule 4
    return None, None


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    people = {p["person_id"]: p for p in rows(ROSTER_DIR / "person.csv")}
    depts = {d["department_id"]: d for d in rows(ROSTER_DIR / "department.csv")}
    aff_path = ROSTER_DIR / "affiliation.csv"
    with aff_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        aff_fields = list(reader.fieldnames)
        affs = list(reader)
    current = {a["person_id"]: a for a in affs if not a["end_date"] and a["is_primary"] == "1"}
    next_id = max(int(a["affiliation_id"]) for a in affs) + 1

    gs_latest, _ = latest_two("google_scholar")
    # department home domains: site domain + majority verified-email domain
    dom_votes: dict[str, Counter] = defaultdict(Counter)
    for r in gs_latest:
        a = current.get(r["person_id"])
        dom = registrable(json.loads(r["raw_json"] or "{}").get("email_domain"))
        if a and dom:
            dom_votes[a["department_id"]][dom] += 1
    home: dict[str, set] = {}
    domain_owner: dict[str, str] = {}
    for did, d in depts.items():
        doms = {registrable(d.get("url"))} - {None}
        if dom_votes[did]:
            top, n = dom_votes[did].most_common(1)[0]
            if n >= 2 or not doms:
                doms.add(top)
        home[did] = doms
    for did, doms in home.items():
        for dom in doms:
            domain_owner.setdefault(dom, did)   # first wins; shared domains (uw.edu) stay ambiguous

    actions = []
    for r in gs_latest:
        pid = r["person_id"]
        a = current.get(pid)
        if not a:
            continue
        raw = json.loads(r["raw_json"] or "{}")
        text = raw.get("affiliation") or ""
        email = registrable(raw.get("email_domain"))
        phd = people.get(pid, {}).get("phd_year")
        years = (int(r["collected_at"][:4]) - int(phd)) if phd else None
        cites = int(r["total_citations"]) if r.get("total_citations") else None
        kind, target = decide(text, email, a["rank"], depts[a["department_id"]], depts,
                              home[a["department_id"]], domain_owner, cites=cites, years_since_phd=years)
        if kind:
            actions.append((kind, pid, a, target, text, email, r["collected_at"]))

    for kind, pid, a, target, text, email, when in actions:
        p = people[pid]
        cur = depts[a["department_id"]]
        if kind == "move":
            new_rank = rank_from_text(text, a["rank"])
            print(f"MOVE    {p['display_name']:<28} {cur['short_name'][:28]:<28} -> {target['short_name'][:28]:<28} "
                  f"{a['rank']}->{new_rank}  [{email or '-'}; \"{text[:60]}\"]")
        elif kind == "promote":
            print(f"PROMOTE {p['display_name']:<28} {cur['short_name'][:28]:<28} {a['rank']}->{rank_from_text(text, a['rank'])}  [\"{text[:60]}\"]")
        elif kind == "wrong":
            print(f"WRONG?  {p['display_name']:<28} {cur['short_name'][:28]:<28} profile looks like a namesake; not applied "
                  f"[{email or '-'}; \"{text[:60]}\"]")
        else:
            print(f"DEPART  {p['display_name']:<28} {cur['short_name'][:28]:<28} -> (untracked) "
                  f"[{email or '-'}; \"{text[:70]}\"]")
        if args.dry_run or kind == "wrong":
            continue
        evidence = f"change from Scholar profile {when}: email @{email or '?'}; affiliation \"{text[:120]}\""
        a["end_date"] = when
        a["source"] = (a.get("source") or "") + f" | closed {when}: {kind}; {evidence}"
        if kind in ("move", "promote"):
            affs.append({"affiliation_id": next_id, "person_id": pid, "department_id": target["department_id"],
                         "rank": rank_from_text(text, a["rank"]), "appointment_type": "regular", "is_primary": 1,
                         "start_date": when, "end_date": None, "source": evidence})
            next_id += 1

    c = Counter(x[0] for x in actions)
    print(f"\n{c['move']} moves, {c['promote']} promotions, {c['depart']} departures, {c['wrong']} suspected wrong profiles (not applied)"
          + (" (dry run, nothing written)" if args.dry_run else ""))
    if not args.dry_run and actions:
        with aff_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=aff_fields)
            w.writeheader()
            for a in affs:
                w.writerow({k: ("" if a.get(k) is None else a.get(k)) for k in aff_fields})
        print(f"wrote {aff_path}")


if __name__ == "__main__":
    main()
