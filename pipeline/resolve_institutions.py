"""Fill department.openalex_institution_id and ror_id via OpenAlex institution search.

    python -m pipeline.resolve_institutions            # fill blanks, print a review table
    python -m pipeline.resolve_institutions --redo     # re-resolve everything

The workbook labels are short ('UCLA', 'UNC', 'Cal Poly, Pomona'); QUERY_OVERRIDE
maps the ones OpenAlex search gets wrong to a better query. Review the printed
table and add overrides until every row looks right.
"""

from __future__ import annotations

import argparse
import csv
import sys

from . import ROSTER_DIR
from .openalex import search_institutions, short_id

QUERY_OVERRIDE = {
    "UCLA": "University of California, Los Angeles",
    "USC": "University of Southern California",
    "UNC": "University of North Carolina at Chapel Hill",
    "MIT": "Massachusetts Institute of Technology",
    "Cal Poly, Pomona": "California State Polytechnic University Pomona",
    "Cal Poly, San Luis Obispo": "California Polytechnic State University",
    "Eastern Washington": "Eastern Washington University",
    "University of Texas, Austin": "The University of Texas at Austin",
    "University of Texas, Arlington": "The University of Texas at Arlington",
    "University of Texas, San Antonio": "The University of Texas at San Antonio",
    "University of Illinois, Chicago": "University of Illinois Chicago",
    "University of Illinois, Urbana-Champaign": "University of Illinois Urbana-Champaign",
    "University of Colorado, Denver": "University of Colorado Denver",
    "University of Wisconsin, Madison": "University of Wisconsin–Madison",
    "University of Wisconsin, Milwaukee": "University of Wisconsin–Milwaukee",
    "University of Waterloo, Ontario": "University of Waterloo",
    "University of Quebec in Montreal": "Université du Québec à Montréal",
    "Universite de Montreal": "Université de Montréal",
    "Universite Laval": "Université Laval",
    "University at Buffalo, The State University of New York": "University at Buffalo, State University of New York",
    "Rutgers University, School of Environmental & Biological Sciences": "Rutgers, The State University of New Jersey",
    "Rutgers University": "Rutgers, The State University of New Jersey",
    "The New School": "The New School",
    "State University of New York at Albany": "University at Albany, State University of New York",
    "Georgia Tech": "Georgia Institute of Technology",
    "Virginia Tech": "Virginia Polytechnic Institute and State University",
    "Texas A&M University": "Texas A&M University",
}


def country_ok(hit: dict, country: str) -> bool:
    codes = {country} | ({"PR"} if country == "US" else set())
    return hit.get("country_code") in codes


def resolve(label: str, country: str) -> dict | None:
    query = QUERY_OVERRIDE.get(label, label)
    results = search_institutions(query) or (search_institutions(label) if query != label else [])
    # Keep OpenAlex relevance order; only demote non-universities and wrong countries.
    ranked = sorted(results, key=lambda r: (r.get("type") != "education", not country_ok(r, country)))
    return ranked[0] if ranked else None


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--redo", action="store_true")
    args = ap.parse_args(argv)

    path = ROSTER_DIR / "department.csv"
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)

    print(f"{'label':<58} {'openalex match':<52} {'cc':<3} {'type':<10} id")
    unresolved = []
    for r in rows:
        if r["openalex_institution_id"] and not args.redo:
            continue
        try:
            hit = resolve(r["short_name"], r["country"])
        except Exception as e:  # usually the daily OpenAlex budget
            print(f"\nstopping at {r['short_name']}: {e}; partial results are saved, re-run to continue")
            break
        if not hit:
            unresolved.append(r["short_name"])
            print(f"{r['short_name']:<58} {'-- no result --':<52}")
            continue
        r["openalex_institution_id"] = short_id(hit["id"])
        r["ror_id"] = hit.get("ror")
        flag = "" if country_ok(hit, r["country"]) and hit.get("type") == "education" else "  <-- check"
        print(f"{r['short_name']:<58} {hit['display_name'][:50]:<52} {hit.get('country_code',''):<3} "
              f"{hit.get('type',''):<10} {r['openalex_institution_id']}{flag}")

    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {path}; unresolved: {unresolved or 'none'}")


if __name__ == "__main__":
    main()
