"""Fill a person's PhD year and institution from their ORCID record.

    python -m pipeline.fill_phd_from_orcid --dry-run
    python -m pipeline.fill_phd_from_orcid

Tom's workbook carried a PhD year and institution for everyone; nothing since
has added them, so new hires arrive without either (46 people by 2026-10-12).
The year feeds the rankings' citations-per-year column and the rank review's
years-since-PhD proxy. ORCID's public record has an education section with a
role title, organization and end date; a doctoral entry there fills the gap.

Only current people with an ORCID and a blank year or institution are touched,
only blanks are written, and the note says where the values came from. A
record with no doctoral entry is left alone and listed, so a reviewer knows
who still needs a hand.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.request
from datetime import date

from . import ROSTER_DIR

ORCID_API = "https://pub.orcid.org/v3.0/{orcid}/educations"
DOCTORAL = re.compile(r"\b(ph\.?\s?d\.?|d\.?phil|doctor(ate|al)?\b|dr\.?\s*phil|sc\.?d\.?|dsc)\b", re.I)
PAUSE = 0.5


def doctorate(educations: dict) -> tuple[str, str, str] | None:
    """(end year, organization, department) of the most recent doctoral entry, else None.

    An entry counts when its role title reads as a doctorate; one with no
    title or no end year is ignored, since a blank is better than a guess.
    """
    found = []
    for g in educations.get("affiliation-group", []) or []:
        for s in g.get("summaries", []) or []:
            e = s.get("education-summary") or {}
            title = e.get("role-title") or ""
            year = ((e.get("end-date") or {}).get("year") or {}).get("value")
            org = (e.get("organization") or {}).get("name")
            if title and DOCTORAL.search(title) and year and org:
                found.append((str(year), org, e.get("department-name") or ""))
    return max(found) if found else None


def fetch_educations(orcid: str) -> dict:
    req = urllib.request.Request(ORCID_API.format(orcid=orcid), headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def current_people_missing_phd() -> list[dict]:
    with (ROSTER_DIR / "affiliation.csv").open(newline="", encoding="utf-8") as f:
        current = {a["person_id"] for a in csv.DictReader(f) if not a["end_date"] and a["is_primary"] == "1"}
    with (ROSTER_DIR / "person.csv").open(newline="", encoding="utf-8") as f:
        people = list(csv.DictReader(f))
    return [p for p in people if p["person_id"] in current and (p.get("orcid") or "").strip()
            and (not (p.get("phd_year") or "").strip() or not (p.get("phd_institution") or "").strip())]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    todo = current_people_missing_phd()
    print(f"{len(todo)} current people with an ORCID and no PhD year or institution")
    filled: dict[str, tuple[str, str, str]] = {}
    none: list[str] = []
    for p in todo:
        try:
            d = doctorate(fetch_educations(p["orcid"]))
        except Exception as e:  # noqa: BLE001 - one bad record must not stop the rest
            print(f"  {p['display_name']}: ORCID lookup failed ({e})", file=sys.stderr)
            continue
        if d:
            filled[p["person_id"]] = d
            print(f"  {p['display_name']:<28} PhD {d[0]}  {d[1]}" + (f"  ({d[2]})" if d[2] else ""))
        else:
            none.append(p["display_name"])
        time.sleep(PAUSE)
    if none:
        print(f"\n{len(none)} records list no doctorate: " + ", ".join(none))
    if args.dry_run or not filled:
        print(f"\n{len(filled)} to fill" + (" (dry run, nothing written)" if args.dry_run else ""))
        return
    path = ROSTER_DIR / "person.csv"
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields, rows = list(reader.fieldnames), list(reader)
    today = date.today().isoformat()
    for r in rows:
        d = filled.get(r["person_id"])
        if not d:
            continue
        year, org, _ = d
        if not (r.get("phd_year") or "").strip():
            r["phd_year"] = year
        if not (r.get("phd_institution") or "").strip():
            r["phd_institution"] = org
        r["notes"] = " | ".join(filter(None, [r.get("notes"), f"PhD {year} {org} from ORCID {r['orcid']} ({today})"]))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\nfilled {len(filled)} people in {path}")


if __name__ == "__main__":
    main()
