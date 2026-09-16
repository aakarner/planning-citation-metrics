"""Semiannual change report: what a person should look at before editing the roster.

    python -m pipeline.detect_changes                # latest snapshots -> build/change_report_<date>.md

Flags, per person:
  * Scholar verified-email domain does not match the department's web domain
    (a likely move; checked against the URL in department.csv);
  * Scholar affiliation text does not name the department's university;
  * OpenAlex last known institution differs from the department;
  * headline citation count fell more than 20% since the previous snapshot of
    the same source (almost always a wrong or merged profile, not a real drop);
  * profile fetched last time but missing from the latest run.

Nothing here edits the roster. The report is for the person doing the
semiannual review.
"""

from __future__ import annotations

import argparse
import csv
import json
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
    try:
        con = sqlite3.connect(BUILD_DIR / "citations.sqlite")
        for pid, name in con.execute(
                "SELECT p.person_id, p.display_name FROM person p "
                "LEFT JOIN v_headline_metrics h USING(person_id) WHERE h.person_id IS NULL"):
            flags[str(pid)].append("no current figure from any source, so they do not appear on "
                                   "the site; needs a Scholar profile or an OpenAlex match")
        con.close()
    except sqlite3.Error:
        pass

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
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
