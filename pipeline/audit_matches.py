"""Un-accept OpenAlex matches that the collected numbers contradict.

    python -m pipeline.audit_matches --dry-run
    python -m pipeline.audit_matches

The matcher decides from names, institutions and topics, before any OpenAlex
metrics exist. Once a snapshot is in, a much blunter test is available: an
author's OpenAlex citation count should be in the same world as the Scholar or
Publish or Perish count we already hold for them. OpenAlex is journal-centric
and normally lands near 0.7 of Scholar, so anything beyond MAX_RATIO or below
MIN_RATIO is a different person with the same name.

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
from .match_openalex import REVIEW_DIR, REVIEW_FIELDS, read_csv, write_csv

MAX_RATIO = 3.0     # OpenAlex claiming >3x Scholar is a bigger namesake
SETTLE_NAME = 0.95      # a pending candidate this close on name, at the right institution...
SETTLE_RATIO = 1.5      # ...and at most this many times our own figure, is the person
SIBLING_SUM_RATIO = 1.2 # a further record for someone already matched may not push the OpenAlex sum past this
FRAGMENT_WORKS = 3      # a record this small settles on name and institution alone
MIN_RATIO = 0.05    # OpenAlex claiming <5% of Scholar is an empty or wrong record
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


def settles(name_sim, inst_match, oa_cites, works, base_cites, accepted_cites=0, has_record=False) -> bool:
    """Accept a pending candidate without a reviewer?

    Name and institution must both match. Then:

    * For a person who already has an accepted record (`has_record`), a
      fragment of FRAGMENT_WORKS or fewer is theirs on the match alone, and a
      larger record is theirs if the summed OpenAlex total stays under
      SIBLING_SUM_RATIO times our own figure -- OpenAlex normally lands below
      Scholar, and a sum well above it means two people.
    * For a person with no record yet, nothing settles without our own figure
      to compare against, and the candidate must be between MIN_RATIO and
      SETTLE_RATIO times it. The low bound matters as much as the high one: a
      fragment accepted as someone's *only* record becomes their whole OpenAlex
      figure, and for a Publish or Perish person it becomes their headline --
      Edmund Merem's 736 turned into 1 the day fragments were allowed to settle
      alone.

    Accepting deserves a stricter test than un-accepting: accepting adds a
    stranger's citations, un-accepting only removes a figure.
    """
    if (name_sim or 0) < SETTLE_NAME or (inst_match or 0) < 1.0:
        return False
    if has_record:
        if (works or 0) <= FRAGMENT_WORKS:
            return True
        if not base_cites or base_cites < MIN_BASE or oa_cites is None:
            return False
        return (accepted_cites + oa_cites) / base_cites <= SIBLING_SUM_RATIO
    if not base_cites or base_cites < MIN_BASE or oa_cites is None:
        return False
    return MIN_RATIO <= oa_cites / base_cites <= SETTLE_RATIO


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
        ratio = implausible(oa, base_cites)
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
    have: dict[str, float] = {}                     # OpenAlex citations already accepted per person
    for r in review:
        if r["source"] == "openalex" and r["status"] == "accepted" and r["external_id"]:
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
        if settles(ns, im, oa, works, b[1] if b else None, have.get(str(r["person_id"]), 0.0),
                   has_record=str(r["person_id"]) in have):
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
    if not bad and not flagged and not accepted_now:
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
        if r["person_id"] in bad and r["status"] == "accepted":
            r["status"], r["reviewed_by"], r["reviewed_at"] = "rejected", "audit_matches", today
            r["notes"] = " ".join(filter(None, [r.get("notes"), bad[r["person_id"]]]))
        elif id(r) in flagged:
            r["status"], r["reviewed_by"], r["reviewed_at"] = "rejected", "audit_matches", today
            r["notes"] = " ".join(filter(None, [r.get("notes"), flagged[id(r)]]))
    write_csv(rpath, REVIEW_FIELDS, review)
    print(f"rejected {len(bad) + len(flagged)} and accepted {len(accepted_now)} review rows in {rpath}")


if __name__ == "__main__":
    main()
