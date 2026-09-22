"""Build a review spreadsheet for undecided OpenAlex candidates.

    python -m pipeline.build_review_sheet                       # people on fallback figures
    python -m pipeline.build_review_sheet --scope all
    python -m pipeline.import_review data/review/openalex_review.xlsx

The default scope is the people whose published figure still comes from Publish
or Perish: a hand-built number from February 2026 that nothing refreshes.
Confirming a match moves them onto the monthly OpenAlex series, so this is the
subset where a decision actually changes what the site shows.

Candidates within a person are ordered by works then citations. OpenAlex
routinely splits one author across several records, and that is exactly what
holds these rows back: the matcher will not auto-accept a winner that a
substantive sibling record sits close behind. The reviewer accepts every
record that is the person; the collector adds them together.

Bands put the quick decisions first. Run audit_matches beforehand so the
namesakes the numbers already rule out never reach the sheet.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from . import BUILD_DIR
from .match_openalex import REVIEW_DIR

OPENALEX_URL = "https://openalex.org/{}"
HEADERS = ["Band", "Person", "Department", "Our figure", "Source", "Candidate", "Institution",
           "OA citations", "Works", "OA h-index", "Name", "Inst", "Ratio", "Suggested",
           "Decision", "Reviewed by", "Date", "Notes", "person_id", "external_id", "OpenAlex"]
BANDS = {
    "A": "Name and institution both match. Usually just confirming which record is the main one.",
    "B": "Institution matches, the name differs a little. Check the name is a form of theirs.",
    "C": "Name matches, the institution does not say our program. Check it is the right person.",
    "D": "Weaker on both. These need the most thought.",
}
HEAD_FILL = PatternFill("solid", fgColor="1F3864")
BAND_FILL = PatternFill("solid", fgColor="DCE6F1")
GROUP_FILL = PatternFill("solid", fgColor="F5F7FA")   # alternating, so a person's rows read as one block
ASK_FILL = PatternFill("solid", fgColor="FFF2CC")
THIN = Side(style="thin", color="8EA9DB")     # under the last row of a person
HAIR = Side(style="hair", color="D9D9D9")     # between candidates


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def band_of(best: dict) -> str:
    ns, im = num(best["name_sim"]) or 0, num(best["inst_match"]) or 0
    if ns >= 0.95 and im >= 1.0:
        return "A"
    if im >= 1.0:
        return "B"
    if ns >= 0.95:
        return "C"
    return "D"


def load(db_path: Path, scope: str):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    people = {str(r["person_id"]): r for r in con.execute("""
        SELECT h.person_id, p.display_name, d.short_name AS dept, h.source AS base_source,
               h.total_citations AS base_cites
        FROM v_headline_metrics h
        JOIN person p ON p.person_id = h.person_id
        JOIN v_current_affiliation ca ON ca.person_id = h.person_id
        JOIN department d ON d.department_id = ca.department_id
        WHERE p.openalex_author_id IS NULL OR p.openalex_author_id = ''""")}
    con.close()
    if scope == "pop-fallback":
        people = {k: v for k, v in people.items() if v["base_source"] == "pop"}

    rows = [r for r in csv.DictReader((REVIEW_DIR / "identity_candidates.csv").open())
            if r["source"] == "openalex"]
    by = defaultdict(list)
    for r in rows:
        by[r["person_id"]].append(r)

    out = []
    for pid, person in people.items():
        rs = by.get(pid, [])
        if any(r["status"] == "accepted" for r in rs):
            continue                            # already settled
        pending = [r for r in rs if r["status"] == "pending" and (r["external_id"] or "").strip()]
        if not pending:
            continue                            # nothing to decide
        best = max(pending, key=lambda r: num(r["score"]) or 0)
        pending.sort(key=lambda r: (-(num(r["works_count"]) or 0), -(num(r["cited_by_count"]) or 0)))
        out.append((band_of(best), person, pending))
    out.sort(key=lambda t: (t[0], t[1]["display_name"]))
    return out


def write_sheet(path: Path, groups) -> tuple[int, int]:
    wb = Workbook()
    ws = wb.active
    ws.title = "Review"
    ws.append(HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "C2"

    col = {h: i + 1 for i, h in enumerate(HEADERS)}
    dv = DataValidation(type="list", formula1='"accepted,rejected"', allow_blank=True,
                        showDropDown=False)
    dv.prompt = "accepted = this record is them. rejected = it is not."
    dv.promptTitle = "Decision"
    ws.add_data_validation(dv)

    row = 2
    n_cand = 0
    # Person, department and our figure repeat on every row, and there are no blank
    # separator rows: both would break filtering and sorting, which is how anyone
    # actually works a sheet this long. Groups stay legible through the shading.
    for group_no, (band, person, pending) in enumerate(groups):
        top = max(pending, key=lambda r: num(r["score"]) or 0)
        shade = GROUP_FILL if group_no % 2 else None
        for i, r in enumerate(pending):
            base = person["base_cites"]
            oa = num(r["cited_by_count"])
            ratio = round(oa / base, 2) if (oa is not None and base) else None
            ws.cell(row, col["Band"], band).fill = BAND_FILL
            ws.cell(row, col["Person"], person["display_name"])
            ws.cell(row, col["Department"], person["dept"])
            ws.cell(row, col["Our figure"], base)
            ws.cell(row, col["Source"], person["base_source"])
            ws.cell(row, col["Candidate"], r["candidate_name"])
            ws.cell(row, col["Institution"], r["last_known_institution"])
            ws.cell(row, col["OA citations"], int(oa) if oa is not None else None)
            ws.cell(row, col["Works"], int(num(r["works_count"]) or 0) or None)
            ws.cell(row, col["OA h-index"], int(num(r["h_index"]) or 0) or None)
            ws.cell(row, col["Name"], num(r["name_sim"]))
            ws.cell(row, col["Inst"], num(r["inst_match"]))
            ws.cell(row, col["Ratio"], ratio)
            ws.cell(row, col["Suggested"], "best match" if r is top else "")
            ws.cell(row, col["person_id"], r["person_id"])
            ws.cell(row, col["external_id"], r["external_id"])
            link = ws.cell(row, col["OpenAlex"], r["external_id"])
            link.hyperlink = OPENALEX_URL.format(r["external_id"])
            link.font = Font(color="0563C1", underline="single")
            dv.add(ws.cell(row, col["Decision"]))
            for c in ws[row]:
                c.border = Border(bottom=THIN if i == len(pending) - 1 else HAIR)
                if shade and c.column < col["Decision"]:
                    c.fill = shade
                if c.column in (col["Institution"], col["Notes"]):
                    c.alignment = Alignment(wrap_text=True, vertical="top")
            for h in ("Decision", "Reviewed by", "Date", "Notes"):
                ws.cell(row, col[h]).fill = ASK_FILL
            ws.cell(row, col["Band"]).fill = BAND_FILL
            row += 1
            n_cand += 1

    widths = {"Band": 6, "Person": 22, "Department": 24, "Our figure": 10, "Source": 8,
              "Candidate": 26, "Institution": 34, "OA citations": 12, "Works": 7,
              "OA h-index": 10, "Name": 7, "Inst": 6, "Ratio": 7, "Suggested": 11,
              "Decision": 13, "Reviewed by": 13, "Date": 11, "Notes": 30,
              "person_id": 10, "external_id": 14, "OpenAlex": 14}
    for h, w in widths.items():
        ws.column_dimensions[get_column_letter(col[h])].width = w
    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{row - 1}"  # row is one past the last

    doc = wb.create_sheet("How to use this")
    lines = [
        ("What this is", True),
        (f"{len(groups)} faculty whose published citation figure still comes from Publish or Perish, "
         "a hand-built number from February 2026 that nothing refreshes. Each has at least one "
         "OpenAlex record that may be theirs. Confirming one moves them onto the monthly series.", False),
        ("", False),
        ("What to do", True),
        ("Put 'accepted' or 'rejected' in the Decision column. Accept EVERY record that is this "
         "person, and reject the ones that are not. OpenAlex often splits one author across several "
         "records, and we add a person's records together, so the question is only 'is this them?', "
         "never 'which one is the main one?'. Leave a row blank to decide later. Put your name in "
         "'Reviewed by' if you like; Notes is free text and is kept.", False),
        ("", False),
        ("Why most of these are here", True),
        ("OpenAlex often splits one author across several records. The matcher will not accept on "
         "its own while a substantive sibling record scores close behind, so these wait for a person "
         "to say which records are theirs. Rows are ordered by Works, fullest first; 'best match' "
         "marks the matcher's pick, which is almost always one of theirs.", False),
        ("", False),
        ("Columns", True),
        ("Our figure / Source  the citation count the site shows now, and where it came from", False),
        ("Ratio                OpenAlex citations divided by our figure. OpenAlex is journal-centric "
         "and normally reports less, so well under 1 is normal and does not mean the wrong person.", False),
        ("Name / Inst          name similarity, and whether the record's institution is their program", False),
        ("OpenAlex             click to open the record", False),
        ("", False),
        ("Bands", True),
    ] + [(f"{k}  {v}", False) for k, v in BANDS.items()] + [
        ("", False),
        ("Namesakes are already gone", True),
        ("audit_matches removed candidates whose citation count contradicts our figure, so you "
         "should not meet a hepatologist here. If a row still looks like a different person, "
         "reject it and say so in Notes.", False),
        ("", False),
        ("When you are done", True),
        ("python -m pipeline.import_review data/review/openalex_review.xlsx", False),
        ("python -m pipeline.match_openalex --rescore", False),
    ]
    for i, (text, bold) in enumerate(lines, 1):
        c = doc.cell(i, 1, text)
        c.font = Font(bold=bold, size=12 if bold else 11)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    doc.column_dimensions["A"].width = 112

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".xlsx.tmp")
    wb.save(tmp)
    tmp.replace(path)
    return len(groups), n_cand


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=BUILD_DIR / "citations.sqlite")
    ap.add_argument("--scope", choices=("pop-fallback", "all"), default="pop-fallback")
    ap.add_argument("--out", type=Path, default=REVIEW_DIR / "openalex_review.xlsx")
    args = ap.parse_args(argv)

    groups = load(args.db, args.scope)
    if not groups:
        raise SystemExit("nothing to review in this scope")
    n_people, n_cand = write_sheet(args.out, groups)
    counts = {b: sum(1 for g in groups if g[0] == b) for b in sorted(BANDS)}
    print(f"wrote {args.out}")
    print(f"  {n_people} people, {n_cand} candidate rows")
    for b, n in counts.items():
        if n:
            print(f"    band {b}: {n:3}  {BANDS[b]}")


if __name__ == "__main__":
    main()
