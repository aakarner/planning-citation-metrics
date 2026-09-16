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
  4. affiliation text names an institution we don't track,
     and the email domain is not the current department's  -> DEPART
     (appointment closed, destination kept in the note)
  5. anything else                                          -> no change

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
from .find_scholar_profiles import affiliation_match
from .match_openalex import norm

INSTITUTION_WORDS = ("university", "universit", "college", "institute", "school", "polytechnic", "academy")
# A profile that still describes a pre-faculty stage predates the appointment we track: leave it.
PRE_FACULTY = ("student", "candidate", "postdoc", "post-doc", "postdoctoral", "fellow", "research assistant")
# Fields no planning faculty member is appointed in: the profile belongs to a namesake.
FOREIGN_FIELDS = ("electrical engineering", "computer science", "biochem", "chemistry", "physics", "medicine",
                  "medical", "biology", "neuroscience", "mathematics", "dentistry", "nursing", "pharmac")
# Tracked-department names that are prefixes of campuses we do not track.
UNTRACKED_CAMPUS = ("tacoma", "bothell", "dearborn", "flint", "duluth", "omaha", "kearney")
RANK_ORDER = {"assistant": 0, "associate": 1, "full": 2}


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
        cur = depts[a["department_id"]]
        low = text.lower()
        if any(w in low for w in PRE_FACULTY):
            continue                                       # stale pre-appointment profile
        at_current = bool(text) and affiliation_match(text, cur) and not any(c in low for c in UNTRACKED_CAMPUS)
        if not at_current and any(w in low for w in FOREIGN_FIELDS):
            actions.append(("wrong", pid, a, None, text, email, r["collected_at"]))
            continue                                       # namesake's profile elsewhere: report, do not apply
        if at_current:
            new_rank = rank_from_text(text, a["rank"])     # rule 1, plus promotions
            if RANK_ORDER.get(new_rank, -1) > RANK_ORDER.get(a["rank"], -1):
                actions.append(("promote", pid, a, cur, text, email, r["collected_at"]))
            continue
        if any(c in low for c in UNTRACKED_CAMPUS) and email and not email_home_check(email, home, a):
            actions.append(("depart", pid, a, None, text, email, r["collected_at"]))
            continue
        email_home = email in home[a["department_id"]] if email else False
        target = None
        if text:
            hits = [d for did, d in depts.items() if did != a["department_id"] and affiliation_match(text, d)]
            if len(hits) == 1:
                target = hits[0]                           # rule 2
        if target is None and email and not email_home:
            did = domain_owner.get(email)
            if did and did != a["department_id"] and (not text or not any(w in text.lower() for w in INSTITUTION_WORDS)
                                                       or affiliation_match(text, depts[did])):
                target = depts[did]                        # rule 3
        if target is not None:
            actions.append(("move", pid, a, target, text, email, r["collected_at"]))
            continue
        if text and any(w in text.lower() for w in INSTITUTION_WORDS) and email and not email_home:
            actions.append(("depart", pid, a, None, text, email, r["collected_at"]))   # rule 4

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
