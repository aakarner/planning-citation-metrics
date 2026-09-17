"""Semiannual change report: what a person should look at before editing the roster.

    python -m pipeline.detect_changes                # latest snapshots -> build/change_report_<date>.md

Flags, per person:
  * Scholar verified-email domain does not match the department's web domain
    (a likely move; checked against the URL in department.csv);
  * Scholar affiliation text does not name the department's university;
  * OpenAlex last known institution differs from the department;
  * headline citation count fell more than 20% since the previous snapshot of
    the same source (almost always a wrong or merged profile, not a real drop);
  * profile fetched last time but missing from the latest run;
  * rank review: assistant or associate for RANK_REVIEW_YEARS or more, so a
    promotion may have been missed. Promotions are only applied when the
    profile text states a rank, and many profiles never do (Geoff Boeing's
    reads no rank at all). Tom's rows carry no rank start date, so for the
    inherited roster years since the PhD stands in -- for assistants only,
    where the tenure clock makes it a fair proxy. Associates are judged only
    by a start date we recorded ourselves, so that list fills over time
    rather than flagging 250 people who are simply long past their PhD.
    Written as a table in the report and as build/rank_review_<date>.csv.

Nothing here edits the roster. The report is for the person doing the
semiannual review.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from . import BUILD_DIR, ROSTER_DIR, SNAPSHOT_DIR
from .find_scholar_profiles import affiliation_match
from .openalex import short_id

DROP = 0.20


def rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def registrable(host: str | None) -> str | None:
    """'luskin.ucla.edu' -> 'ucla.edu'; '@utexas.edu' -> 'utexas.edu'."""
    if not host:
        return None
    host = host.lower().lstrip("@").strip()
    if "://" in host or "/" in host:
        host = urlparse(host if "://" in host else "http://" + host).hostname or host
    parts = host.split(".")
    # inu.ac.kr, ox.ac.uk, unsw.edu.au: keep three labels under a two-letter country code
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"ac", "edu", "co", "com", "org", "gov", "net"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


WINDOW_DAYS = 7
RANK_REVIEW_YEARS = 7            # this long as assistant or associate: check for a promotion
PHD_PROXY_RANKS = ("assistant",)  # where years since the PhD stands in for an unknown rank start


VERIFIED_RE = re.compile(r"rank verified (\d{4}-\d{2}-\d{2})")


def verified_on(source: str | None):
    """The most recent 'rank verified YYYY-MM-DD' note on an appointment, or None."""
    dates = VERIFIED_RE.findall(source or "")
    return date.fromisoformat(max(dates)) if dates else None


def rank_review(rank, start_date, phd_year, today, verified=None) -> tuple[int, str] | None:
    """(years, reason) if this appointment is due a rank check, else None.

    A start date we recorded ourselves is used when present, for either rank.
    Without one, only an assistant is judged, from years since the PhD: the
    tenure clock makes seven years a fair line there and a meaningless one for
    associates, most of whom are decades past the PhD. A rank a reviewer has
    confirmed within the window is not raised again.
    """
    if rank not in ("assistant", "associate"):
        return None
    if verified and (today - verified).days < RANK_REVIEW_YEARS * 365:
        return None
    if start_date:
        yrs = (today - date.fromisoformat(start_date)).days // 365
        if yrs >= RANK_REVIEW_YEARS:
            return yrs, f"{rank} since {start_date} by our record ({yrs} years)"
        return None
    if rank in PHD_PROXY_RANKS and phd_year:
        yrs = today.year - int(phd_year)
        if yrs >= RANK_REVIEW_YEARS:
            return yrs, f"assistant {yrs} years after the {phd_year} PhD; rank start not on record"
    return None


def latest_two(source: str) -> tuple[list[dict], list[dict]]:
    """The latest collection run and the one before it.

    A run can span several files (rate limits, budgets), so files within
    WINDOW_DAYS of the newest are merged into one run, keeping the newest row
    per person; the previous run is the newest file older than that window."""
    files = sorted((SNAPSHOT_DIR / source).glob("*.csv"))
    if not files:
        return [], []
    newest = date.fromisoformat(files[-1].stem)
    latest_files = [f for f in files if (newest - date.fromisoformat(f.stem)).days <= WINDOW_DAYS]
    older = [f for f in files if f not in latest_files]
    latest: dict[str, dict] = {}
    for f in latest_files:                      # ascending, so later files win
        for r in rows(f):
            if r["total_citations"]:
                latest[r["person_id"]] = r
    prev = rows(older[-1]) if older else []
    return list(latest.values()), prev


def collect_rank_review(people, depts, current, gs_text, metrics, today) -> list[dict]:
    """Every current appointment due a rank check, longest-held first.

    Shared by the change report and build_rank_sheet so both show the same rows.
    `gs_text` is person_id -> latest Scholar affiliation text; `metrics` is
    person_id -> (percentile within rank, total citations).
    """
    review = []
    for pid, aff in current.items():
        p = people.get(pid)
        if not p:
            continue
        hit = rank_review(aff["rank"], aff.get("start_date") or None, p.get("phd_year") or None, today,
                          verified=verified_on(aff.get("source")))
        if not hit:
            continue
        yrs, why = hit
        d = depts[aff["department_id"]]
        pct, cites = metrics.get(pid, (None, None))
        review.append({"person_id": pid, "affiliation_id": aff["affiliation_id"], "display_name": p["display_name"],
                       "department": d["short_name"], "department_url": d.get("url") or "", "rank": aff["rank"],
                       "years": yrs, "basis": why, "phd_year": p.get("phd_year") or "",
                       "rank_start": aff.get("start_date") or "", "profile_affiliation": gs_text.get(pid, ""),
                       "pct_citations_rank": "" if pct is None else pct, "total_citations": "" if cites is None else cites,
                       "google_scholar_id": p.get("google_scholar_id") or ""})
    review.sort(key=lambda r: (-r["years"], r["display_name"]))
    return review


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=BUILD_DIR / f"change_report_{date.today().isoformat()}.md")
    args = ap.parse_args(argv)

    people = {p["person_id"]: p for p in rows(ROSTER_DIR / "person.csv")}
    depts = {d["department_id"]: d for d in rows(ROSTER_DIR / "department.csv")}
    current = {a["person_id"]: a for a in rows(ROSTER_DIR / "affiliation.csv")
               if not a["end_date"] and a["is_primary"] == "1"}

    flags: dict[str, list[str]] = defaultdict(list)

    gs_latest, gs_prev = latest_two("google_scholar")
    gs_prev_by = {r["person_id"]: r for r in gs_prev}

    # A department's "home" email domains: its website's domain plus whatever
    # domain most of its faculty verify with (program sites like hunterurban.org
    # or uprrp.edu would otherwise flag everyone).
    by_dept_domains: dict[str, Counter] = defaultdict(Counter)
    for r in gs_latest:
        aff = current.get(r["person_id"])
        dom = registrable(json.loads(r["raw_json"] or "{}").get("email_domain"))
        if aff and dom:
            by_dept_domains[aff["department_id"]][dom] += 1
    home_domains: dict[str, set] = {}
    for did, d in depts.items():
        doms = {registrable(d.get("url"))} - {None}
        if by_dept_domains[did]:
            top, n = by_dept_domains[did].most_common(1)[0]
            if n >= 2 or not doms:
                doms.add(top)
        home_domains[did] = doms

    for r in gs_latest:
        pid = r["person_id"]
        aff = current.get(pid)
        dept = depts.get(aff["department_id"]) if aff else None
        raw = json.loads(r["raw_json"] or "{}")
        email_domain = registrable(raw.get("email_domain"))
        homes = home_domains.get(aff["department_id"], set()) if aff else set()
        email_ok = email_domain in homes if email_domain else None
        text_ok = bool(raw.get("affiliation")) and dept is not None and affiliation_match(raw["affiliation"], dept)
        if email_domain and homes and not email_ok and not text_ok:   # a stale email alone is not a signal
            flags[pid].append(f"Scholar verified email is @{email_domain}; department uses {', '.join(sorted(homes))}")
        # Affiliation text is only evidence when the email does not already settle it.
        if (email_ok is not True and raw.get("affiliation") and dept
                and not affiliation_match(raw["affiliation"], dept)):
            flags[pid].append(f"Scholar affiliation reads \"{raw['affiliation'][:80]}\"")
        prev = gs_prev_by.get(pid)
        if prev and prev["total_citations"] and r["total_citations"]:
            a, b = int(prev["total_citations"]), int(r["total_citations"])
            if a and (a - b) / a > DROP:
                flags[pid].append(f"Scholar citations fell {a} -> {b} ({(b - a) / a:+.0%}); check the profile")
    partial_note = ""
    if gs_latest and gs_prev and len(gs_latest) >= 0.9 * len(gs_prev):
        got = {r["person_id"] for r in gs_latest}
        for pid in gs_prev_by:
            if pid not in got and people.get(pid, {}).get("google_scholar_id"):
                flags[pid].append("Scholar profile fetched last time but missing from the latest run")
    elif gs_latest and gs_prev:
        partial_note = (f"Latest Scholar snapshot has {len(gs_latest)} rows against {len(gs_prev)} previously, "
                        "so it is treated as partial and missing profiles are not flagged.")

    oa_latest, oa_prev = latest_two("openalex")
    oa_prev_by = {r["person_id"]: r for r in oa_prev}
    have_scholar = {r["person_id"] for r in gs_latest}
    for r in oa_latest:
        pid = r["person_id"]
        aff = current.get(pid)
        dept = depts.get(aff["department_id"]) if aff else None
        raw = json.loads(r["raw_json"] or "{}")
        lki = raw.get("last_known_institutions") or []
        ids = {short_id(i.get("id")) for i in lki}
        # OpenAlex derives this from recent paper affiliations, so it is stale or
        # shows a coauthor's institution often enough to be noise. Only worth
        # raising for people with no Scholar profile, where it is all we have.
        if (pid not in have_scholar and dept and dept.get("openalex_institution_id")
                and ids and dept["openalex_institution_id"] not in ids):
            names = ", ".join(i.get("display_name", "?") for i in lki[:2])
            flags[pid].append(f"OpenAlex last known institution: {names} (no Scholar profile to check against)")
        prev = oa_prev_by.get(pid)
        if prev and prev["total_citations"] and r["total_citations"]:
            a, b = int(prev["total_citations"]), int(r["total_citations"])
            if a and (a - b) / a > DROP:
                flags[pid].append(f"OpenAlex citations fell {a} -> {b} ({(b - a) / a:+.0%})")

    # Someone with no current figure vanishes from the site entirely, so say so
    # here rather than letting them disappear quietly.
    import sqlite3
    metrics: dict[str, tuple] = {}
    try:
        con = sqlite3.connect(BUILD_DIR / "citations.sqlite")
        for pid, name in con.execute(
                "SELECT p.person_id, p.display_name FROM person p "
                "LEFT JOIN v_headline_metrics h USING(person_id) WHERE h.person_id IS NULL"):
            flags[str(pid)].append("no current figure from any source, so they do not appear on "
                                   "the site; needs a Scholar profile or an OpenAlex match")
        metrics = {str(r[0]): (r[1], r[2]) for r in con.execute(
            "SELECT person_id, pct_citations_rank, total_citations FROM v_person_percentiles")}
        con.close()
    except sqlite3.Error:
        pass

    # ---- rank review: promotions the profile text never announced ----------
    today = date.today()
    gs_text = {r["person_id"]: (json.loads(r["raw_json"] or "{}").get("affiliation") or "") for r in gs_latest}
    review = collect_rank_review(people, depts, current, gs_text, metrics, today)
    review_csv = args.out.parent / f"rank_review_{today.isoformat()}.csv"

    lines = [f"# Change report, {date.today().isoformat()}", "",
             f"{len(flags)} people flagged out of {len(gs_latest)} Scholar and {len(oa_latest)} OpenAlex rows in the latest snapshots.", ""]
    if partial_note:
        lines += [partial_note, ""]
    for pid in sorted(flags, key=lambda p: people[p]["last_name"]):
        p = people[pid]
        aff = current.get(pid)
        dept = depts[aff["department_id"]]["short_name"] if aff else "no current appointment"
        lines.append(f"## {p['display_name']} ({dept}, {aff['rank'] if aff else '-'})")
        lines += [f"- {f}" for f in flags[pid]]
        lines.append("")
    if review:
        lines += ["## Rank review", "",
                  f"{len(review)} appointments have been assistant or associate for {RANK_REVIEW_YEARS}+ years, "
                  "or look that way, so a promotion may have gone unrecorded. A promotion is applied only when the "
                  "Scholar profile text states a rank, and many profiles never do. 'Profile says' is the text as it "
                  "stands; 'pct in rank' is the person's citation percentile among people we hold at that rank, "
                  "so 0.99 for an assistant professor is a second reason to look. "
                  f"Also written to {review_csv.name}.", "",
                  "| Name | Program | Rank | Years | Basis | Profile says | Pct in rank |", "|---|---|---|---:|---|---|---:|"]
        for r in review:
            pct = f"{float(r['pct_citations_rank']):.2f}" if r["pct_citations_rank"] != "" else ""
            lines.append(f"| {r['display_name']} | {r['department']} | {r['rank']} | {r['years']} | {r['basis']} | "
                         f"{r['profile_affiliation'][:60]} | {pct} |")
        lines.append("")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines))
    if review:
        with review_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(review[0].keys()))
            w.writeheader(); w.writerows(review)
        print(f"-> {review_csv}  ({len(review)} rows)")
    print("\n".join(lines))
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
