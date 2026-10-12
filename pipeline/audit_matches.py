"""Un-accept OpenAlex matches that the collected numbers contradict.

    python -m pipeline.audit_matches --dry-run
    python -m pipeline.audit_matches

The matcher decides from names, institutions and topics, before any OpenAlex
metrics exist. Once a snapshot is in, a much blunter test is available: an
author's OpenAlex citation count should be in the same world as the Scholar or
Publish or Perish count we already hold for them. OpenAlex is journal-centric
and normally lands near 0.7 of Scholar, so anything beyond MAX_RATIO is a
different person with the same name. Too few citations proves nothing: a real
record can be tiny, and since 2026-09-26 a small OpenAlex record is simply the
person's figure -- Publish or Perish is a deprecated source, and a Scholar
profile is the way to a larger number.

Offenders lose their openalex_author_id in person.csv and their review row is
marked 'rejected' with the evidence, so the matcher will not pick the same
record again. A reviewer who disagrees can flip that row back in the review
spreadsheet. People with a Scholar profile only lose a comparison figure;
fallback people revert to their earlier number until a better match is found.

The same test then runs over candidates still *awaiting* review, which the
audit never used to see: a reviewer working down the spreadsheet by name and
institution would otherwise accept the hepatologist Guadalupe Garcia-Tsao
(52,281 citations against a Publish or Perish base of 63). Those rows are
rejected with the evidence, so they neither reach a reviewer nor get proposed
again.

Only the high side is audited on pending rows. A candidate with far *fewer*
citations than the base is usually OpenAlex splitting one real person across
several sparse author records -- Reid Ewing and Carlo Ratti each have records
holding a single work -- and rejecting those would mislabel some 1,600 rows as
namesakes to no benefit, since the matcher already treats a near-empty record
as a stub. Publishing a stranger's inflated count is the failure that matters.

Note on Publish or Perish baselines: those were hand-built and can themselves
undercount, so a rejection against a `pop` base is weaker evidence than one
against a Scholar base. Both are rejected here, and the note says which.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from datetime import date
from pathlib import Path

from . import BUILD_DIR, ROSTER_DIR
from .find_scholar_profiles import affiliation_match
from .match_openalex import REVIEW_DIR, REVIEW_FIELDS, read_csv, write_csv

MAX_RATIO = 3.0     # OpenAlex claiming >3x Scholar is a bigger namesake
SETTLE_NAME = 0.95      # a pending candidate this close on name, at the right institution...
SETTLE_RATIO = 1.5      # ...and at most this many times our own figure, is the person
SIBLING_SUM_RATIO = 1.2 # a further record for someone already matched may not push the OpenAlex sum past this
FRAGMENT_WORKS = 3      # a record this small settles on name and institution alone
SIBLING_TOPIC = 0.5     # an extra record at another institution needs this share of planning topics
MEDICAL = ("hospital", "medical", "clinic", "health", "medicine", "cancer")
MIN_RATIO = 0.05    # kept for implausible()'s two-sided mode; policy no longer un-accepts on the low side
MIN_BASE = 25       # below this the ratio is noise, so don't judge


def implausible(oa_cites, base_cites, *, high_only: bool = False) -> float | None:
    """The OpenAlex/base ratio when it rules a candidate out, else None.

    `high_only` audits the namesake direction alone, which is what pending
    candidates get: a record with far fewer citations than the base is usually
    OpenAlex holding a fragment of the right person, not a stranger.
    """
    if oa_cites is None or not base_cites or base_cites < MIN_BASE:
        return None                      # below MIN_BASE the ratio is noise
    ratio = oa_cites / base_cites
    if ratio > MAX_RATIO or (not high_only and ratio < MIN_RATIO):
        return ratio
    return None


def settles(name_sim, inst_match, oa_cites, works, base_cites, accepted_cites=0, has_record=False,
            last_inst_ok=None, topic_share=None) -> bool:
    """Accept a pending candidate without a reviewer?

    Name and institution decide identity. Size can disprove it in one direction
    only: far more citations than our own figure means a namesake, far fewer
    means nothing -- a real record can be tiny, and policy (2026-09-26) is
    that a small OpenAlex record is the person's figure and a Scholar profile
    is the way to a larger one. So:

    * A fragment (FRAGMENT_WORKS or fewer) settles on the match alone.
    * A larger record needs our own figure to compare against. For a person
      with no record yet it must be at most SETTLE_RATIO times it; for someone
      already matched, the summed OpenAlex total must stay under
      SIBLING_SUM_RATIO times it, since a sum well above Scholar means two
      people. Jenny Liu's two same-campus records (no base) and Robert Brown's
      16,190-citation namesake beside his 9,517 both stay with a reviewer.
    """
    if (name_sim or 0) < SETTLE_NAME or (inst_match or 0) < 1.0:
        return False
    if (works or 0) <= FRAGMENT_WORKS:
        return True
    if not base_cites or base_cites < MIN_BASE or oa_cites is None:
        return False
    if has_record:
        if not sibling_agrees(last_inst_ok, topic_share):
            return False
        return (accepted_cites + oa_cites) / base_cites <= SIBLING_SUM_RATIO
    return oa_cites / base_cites <= SETTLE_RATIO


def sibling_agrees(last_inst_ok, topic_share) -> bool:
    """May an extra, non-fragment record join someone already matched?

    inst_match says the department's institution appears somewhere in the
    record's affiliation history, which for a common name is weak: OpenAlex
    conflates authors, and on 2026-09-25 the sum rule let five 'Yan Song'
    records through, three at Chinese universities, because together they
    stayed under 1.2x her Scholar total. An extra record must therefore also
    sit at the person's institution now (`last_inst_ok`: True, or None when
    the record names none) or carry a planning share of topics; a record at a
    hospital or company with no planning topics is left to a reviewer.
    """
    if last_inst_ok is False and (topic_share is None or topic_share < SIBLING_TOPIC):
        return False
    return True


def last_institution_ok(text, dept, primary_text=None):
    """None when the record names no institution; else whether it is the
    person's department (by name or alias) or the same place as their primary record."""
    text = (text or "").strip()
    if not text:
        return None
    if any(w in text.lower() for w in MEDICAL):
        return False
    if primary_text and text == primary_text.strip():
        return True
    return bool(dept) and affiliation_match(text, dept) > 0


def base_counts(con) -> dict[str, tuple[str, int]]:
    """person_id -> (source, citations) for comparison, Scholar over PoP.

    A person can hold both, and Scholar is the stronger baseline, so pick rather
    than letting a join return two rows and leave the outcome to row order.
    """
    out: dict[str, tuple[str, int]] = {}
    for r in con.execute("""
            SELECT person_id, source, total_citations FROM v_latest_metrics
            WHERE source IN ('google_scholar', 'pop') AND total_citations IS NOT NULL"""):
        pid, better = str(r["person_id"]), r["source"] == "google_scholar"
        if pid not in out or (better and out[pid][0] != "google_scholar"):
            out[pid] = (r["source"], r["total_citations"])
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=BUILD_DIR / "citations.sqlite")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    base = base_counts(con)
    rows = con.execute("""
        SELECT p.person_id, p.display_name, p.openalex_author_id,
               o.total_citations AS oa_cites, o.works_count
        FROM person p
        JOIN v_openalex_metrics o ON o.person_id = p.person_id
        WHERE p.openalex_author_id IS NOT NULL AND p.openalex_author_id <> ''
    """).fetchall()
    con.close()

    bad = {}
    for r in rows:
        b = base.get(str(r["person_id"]))
        if not b:
            continue
        base_source, base_cites = b
        oa = r["oa_cites"]
        ratio = implausible(oa, base_cites, high_only=True)   # too few citations never proves a wrong record
        if ratio is not None:
            bad[str(r["person_id"])] = (
                f"un-accepted {date.today().isoformat()}: OpenAlex {oa:,} citations against "
                f"{base_cites:,} from {base_source} ({ratio:.1f}x); probable namesake")
            print(f"  {r['display_name']:<28} {base_source:<15} ours {base_cites:>7,}  "
                  f"openalex {oa:>7,}  {ratio:>6.1f}x  ({r['openalex_author_id']})")

    print(f"\n{len(bad)} of {len(rows)} accepted matches contradicted by the numbers")

    # ---- candidates still awaiting review -----------------------------------
    rpath = REVIEW_DIR / "identity_candidates.csv"
    _, review = read_csv(rpath)
    today = date.today().isoformat()
    flagged: dict[int, str] = {}                  # id(row) -> note
    per_person: dict[str, list] = {}
    for r in review:
        if r["source"] != "openalex" or r["status"] != "pending" or not r["external_id"]:
            continue
        per_person.setdefault(str(r["person_id"]), []).append(r)
        b = base.get(str(r["person_id"]))
        if not b:
            continue
        base_source, base_cites = b
        try:
            oa = float(r["cited_by_count"])
        except (TypeError, ValueError):
            continue
        ratio = implausible(oa, base_cites, high_only=True)
        if ratio is not None:
            flagged[id(r)] = (f"rejected {today}: OpenAlex {int(oa):,} citations against "
                              f"{base_cites:,} from {base_source} ({ratio:.1f}x); probable namesake")
            print(f"  {r['display_name'][:26]:26} <- {(r['candidate_name'] or '-')[:26]:26} "
                  f"ours {base_cites:>7,} ({base_source})  openalex {int(oa):>7,}  {ratio:>7.1f}x")

    # ---- pending candidates the evidence already settles --------------------
    # Name and institution both match and the numbers do not object: the person.
    # OpenAlex splits authors, so several such rows for one person are all theirs.
    accepted_now: dict[int, str] = {}
    newly_matched: dict[str, list[dict]] = {}
    _, depts_rows = read_csv(ROSTER_DIR / "department.csv")
    depts = {d["department_id"]: d for d in depts_rows}
    _, affs = read_csv(ROSTER_DIR / "affiliation.csv")
    dept_of = {a["person_id"]: depts.get(a["department_id"]) for a in affs
               if not a["end_date"] and a["is_primary"] == "1"}
    accepted_by: dict[str, list[dict]] = {}
    for r in review:
        if r["source"] == "openalex" and r["status"] == "accepted" and r["external_id"]:
            accepted_by.setdefault(str(r["person_id"]), []).append(r)
    primary_text = {pid: (max(rs, key=lambda r: float(r["works_count"] or 0))["last_known_institution"] or "")
                    for pid, rs in accepted_by.items()}

    # ---- extra records the audit itself accepted that no longer pass --------
    # Back to pending, not rejected: a reviewer decides. Only rows this audit
    # accepted; a person's decision stands.
    unsettled: dict[int, str] = {}
    for pid, rs in accepted_by.items():
        if len(rs) < 2:
            continue
        primary = max(rs, key=lambda r: float(r["works_count"] or 0))
        for r in rs:
            if r is primary or r.get("reviewed_by") != "audit_matches" or float(r["works_count"] or 0) <= FRAGMENT_WORKS:
                continue
            ok = last_institution_ok(r["last_known_institution"], dept_of.get(str(pid)), primary["last_known_institution"])
            ts = float(r["topic_share"]) if r["topic_share"] not in (None, "") else None
            if not sibling_agrees(ok, ts):
                shown = "no" if not ts else f"{ts:.0%}"
                unsettled[id(r)] = (f"unsettled {today}: extra record at {r['last_known_institution'] or '?'} with "
                                    f"{shown} planning topics; a reviewer decides")
                print(f"  {r['display_name'][:26]:26} extra record {(r['last_known_institution'] or '?')[:34]:34} "
                      f"{int(float(r['cited_by_count'] or 0)):>7,} cites  topics {ts if ts is not None else '-'}")
    if unsettled:
        print(f"\n{len(unsettled)} extra records sent back to review")

    have: dict[str, float] = {}                     # OpenAlex citations already accepted per person
    for r in review:
        if r["source"] == "openalex" and r["status"] == "accepted" and r["external_id"] and id(r) not in unsettled:
            have[str(r["person_id"])] = have.get(str(r["person_id"]), 0.0) + float(r["cited_by_count"] or 0)
    for r in review:
        if r["source"] != "openalex" or r["status"] != "pending" or not r["external_id"] or id(r) in flagged:
            continue
        b = base.get(str(r["person_id"]))
        try:
            oa = float(r["cited_by_count"]) if r["cited_by_count"] not in (None, "") else None
            works = float(r["works_count"]) if r["works_count"] not in (None, "") else None
            ns, im = float(r["name_sim"] or 0), float(r["inst_match"] or 0)
        except (TypeError, ValueError):
            continue
        pid_s = str(r["person_id"])
        ts = float(r["topic_share"]) if r["topic_share"] not in (None, "") else None
        ok = last_institution_ok(r["last_known_institution"], dept_of.get(pid_s), primary_text.get(pid_s))
        if settles(ns, im, oa, works, b[1] if b else None, have.get(pid_s, 0.0),
                   has_record=pid_s in have, last_inst_ok=ok, topic_share=ts):
            if oa is not None:
                have[str(r["person_id"])] = have.get(str(r["person_id"]), 0.0) + oa   # later siblings see the running sum
            accepted_now[id(r)] = (f"accepted {today}: name and institution match, "
                                   + (f"{int(oa):,} against {b[1]:,} from {b[0]}" if b and oa is not None else f"{int(works or 0)}-work fragment"))
            newly_matched.setdefault(str(r["person_id"]), []).append(r)
    if accepted_now:
        print(f"\n{len(accepted_now)} pending candidates settled as the person, across {len(newly_matched)} people")

    settled = sorted(name for pid, rs in per_person.items()
                     if rs and all(id(r) in flagged for r in rs)
                     for name in [rs[0]["display_name"]])
    print(f"\n{len(flagged)} of {sum(len(v) for v in per_person.values())} pending candidates "
          f"are namesakes by the numbers")
    if settled:
        print(f"{len(settled)} people have no plausible candidate left, so no review is needed: "
              + ", ".join(settled))
    if args.dry_run:
        print("\n(dry run, nothing written)")
        return
    if not bad and not flagged and not accepted_now and not unsettled:
        return

    if bad:
        ppath = ROSTER_DIR / "person.csv"
        pfields, people = read_csv(ppath)
        for p in people:
            if p["person_id"] in bad:
                p["notes"] = " ".join(filter(None, [p.get("notes"), bad[p["person_id"]]]))
                p["openalex_author_id"] = None
                # The matcher copied the record's ORCID onto the roster on acceptance;
                # it is the namesake's too, and renders as a public link.
                p["orcid"] = None
        write_csv(ppath, pfields, people)
        print(f"cleared {len(bad)} ids from {ppath}")

    if accepted_now:
        # a newly matched person with no roster id gets the accepted record with the most works
        ppath = ROSTER_DIR / "person.csv"
        pfields, people = read_csv(ppath)
        for p in people:
            rs = newly_matched.get(p["person_id"])
            if rs and not (p.get("openalex_author_id") or "").strip():
                best = max(rs, key=lambda r: (float(r["works_count"] or 0), float(r["cited_by_count"] or 0)))
                p["openalex_author_id"] = best["external_id"]
        write_csv(ppath, pfields, people)
    for r in review:
        if r["source"] != "openalex":
            continue
        if id(r) in accepted_now:
            r["status"], r["reviewed_by"], r["reviewed_at"] = "accepted", "audit_matches", today
            r["notes"] = " ".join(filter(None, [r.get("notes"), accepted_now[id(r)]]))
            continue
        if id(r) in unsettled:
            r["status"], r["reviewed_by"], r["reviewed_at"] = "pending", "", ""
            r["notes"] = " ".join(filter(None, [r.get("notes"), unsettled[id(r)]]))
            continue
        if r["person_id"] in bad and r["status"] == "accepted":
            r["status"], r["reviewed_by"], r["reviewed_at"] = "rejected", "audit_matches", today
            r["notes"] = " ".join(filter(None, [r.get("notes"), bad[r["person_id"]]]))
        elif id(r) in flagged:
            r["status"], r["reviewed_by"], r["reviewed_at"] = "rejected", "audit_matches", today
            r["notes"] = " ".join(filter(None, [r.get("notes"), flagged[id(r)]]))
    write_csv(rpath, REVIEW_FIELDS, review)
    print(f"rejected {len(bad) + len(flagged)}, accepted {len(accepted_now)} and sent back {len(unsettled)} review rows in {rpath}")


if __name__ == "__main__":
    main()
