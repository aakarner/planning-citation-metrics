"""The built site, from a small synthetic roster: who is ranked, who is named
without a figure, what the chart on a person's page draws, and the files a
visitor or a search engine asks for besides pages. No network.
"""

import csv
import json
import re
import sqlite3

import pytest

from pipeline import build_site
from pipeline.build_db import build as build_db
from pipeline.build_site import MIN_FACULTY_TO_RANK, YEARS_SHOWN, axis_top, t, year_chart, yearly_counts

# These tests run before every deploy, so they take the site's wording from site/copy.toml
# (through t) and never spell it out: an edit to the copy must not be able to fail them.

DEPT = {"url": "", "openalex_institution_id": "", "ror_id": "", "acsp_member": "full", "active": 1}
PERSON = {"middle_name": "", "phd_year": 2010, "phd_institution": "", "interests": "", "google_scholar_id": "",
          "openalex_author_id": "", "orcid": "", "semantic_scholar_id": "", "researchgate_url": "",
          "linkedin_url": "", "personal_url": "", "notes": ""}
SNAP_FIELDS = ["person_id", "source", "collected_at", "total_citations", "h_index", "i10_index",
               "citations_5yr", "h_index_5yr", "works_count", "raw_json"]
YEARS = {"2022": 40, "2023": 55, "2024": 0, "2025": 70, "2026": 30}


def write(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def reading(pid, cites, collected="2026-11-09", **raw):
    return {"person_id": pid, "source": "google_scholar", "collected_at": collected, "total_citations": cites,
            "h_index": 10, "i10_index": "", "citations_5yr": "", "h_index_5yr": "", "works_count": "",
            "raw_json": json.dumps({"scholar_id": f"ID{pid:010d}J", **raw})}


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """Big U (three faculty with figures, one without), Tiny U (two, both highly
    cited), and a geography department holding one planning PhD with a figure
    and one without."""
    tmp = tmp_path_factory.mktemp("site")
    depts = [{"department_id": 1, "short_name": "Big U", "name": "City Planning", "university": "Big U",
              "country": "US", **DEPT, "kind": "planning"},
             {"department_id": 2, "short_name": "Tiny U", "name": "Planning", "university": "Tiny U",
              "country": "US", **DEPT, "kind": "planning"},
             {"department_id": 3, "short_name": "Big U (Geography)", "name": "Department of Geography",
              "university": "Big U", "country": "US", **DEPT, "kind": "other"}]
    names = {1: "Ada Planner", 2: "Bo Mapper", 3: "Cy Zoner", 4: "Dee Newhire", 5: "Eve Star", 6: "Flo Star",
             7: "Gus Geographer", 8: "Hal Unfound"}
    where = {1: 1, 2: 1, 3: 1, 4: 1, 5: 2, 6: 2, 7: 3, 8: 3}
    no_figure = {4, 8}
    people = [{"person_id": i, "first_name": n.split()[0], "last_name": n.split()[1], "display_name": n, **PERSON,
               "google_scholar_id": "" if i in no_figure else f"ID{i:010d}J"} for i, n in names.items()]
    affs = [{"affiliation_id": i, "person_id": i, "department_id": d, "rank": "associate",
             "appointment_type": "regular", "is_primary": 1, "start_date": "", "end_date": "", "source": "test"}
            for i, d in where.items()]
    roster, snaps = tmp / "roster", tmp / "snapshots"
    write(roster / "department.csv", list(depts[0]), depts)
    write(roster / "person.csv", list(people[0]), people)
    write(roster / "affiliation.csv", list(affs[0]), affs)
    write(roster / "person_alias.csv", ["person_id", "alias"], [])
    cites = {1: 500, 2: 300, 3: 100, 5: 9000, 6: 8000, 7: 700}
    write(snaps / "google_scholar" / "2026-07-06.csv", SNAP_FIELDS,
          [reading(1, 450, "2026-07-06", cites_per_year=YEARS)])
    write(snaps / "google_scholar" / "2026-11-09.csv", SNAP_FIELDS,
          [reading(i, c, cites_per_year=YEARS) for i, c in cites.items()])
    db = build_db(tmp / "citations.sqlite", roster, snaps, quiet=True)
    out = tmp / "out"
    build_site.build(db, out, "", quiet=True)
    return db, out


def page(out, path):
    return (out / path).read_text()


def test_a_program_too_small_to_rank_is_listed_last_with_no_rank(site):
    """University of Nebraska, two faculty tracked, sat second of 121 on the
    departments page while the homepage list left such programs out."""
    assert MIN_FACULTY_TO_RANK == 3
    rows = re.findall(r"<tr data-country[^>]*>.*?</tr>", page(site[1], "departments.html"), re.S)
    assert ["Big U" in rows[0], "Tiny U" in rows[1]] == [True, True]        # the highest median comes last
    assert 'class="rank">1</td>' in rows[0] and "data-unranked" not in rows[0]
    assert "data-unranked" in rows[1] and 'class="rank">&mdash;</td>' in rows[1]
    fold = re.search(re.escape(t("home", "top_departments_heading")) + r".*?</details>", page(site[1], "index.html"), re.S)
    assert "Big U" in fold.group(0) and "Tiny U" not in fold.group(0)


def test_someone_with_no_figure_is_named_on_their_program_page_and_found_by_search(site):
    """Nine current people had no figure from any source in 2026-11 and appeared
    nowhere: not on their program's page, not in search."""
    _, out = site
    big = page(out, "department/big-u.html")
    assert 'id="no-figure"' in big and "Dee Newhire (Associate Professor)" in big
    assert "Dee Newhire" not in page(out, "rankings.html")                 # named, never ranked
    assert '<div class="tile"><span class="n nums">3</span>' in big        # and not counted: three faculty tracked
    assert 'id="no-figure"' not in page(out, "department/tiny-u.html")
    assert "Hal Unfound (Associate Professor)" in page(out, build_site.OTHERS_PAGE)
    index = {r["t"]: r for r in json.loads(page(out, "search.json"))}
    assert index["Dee Newhire"]["u"] == "department/big-u.html#no-figure"
    assert index["Hal Unfound"]["u"] == f"{build_site.OTHERS_PAGE}#no-figure"
    assert not (out / "person" / "dee-newhire.html").exists()


def test_a_person_page_charts_the_profiles_own_years_not_this_projects_two_readings(site):
    """The sparkline it replaces drew the March 2026 workbook figure at a date it
    was not read on, and printed 'Up 16% over that span'."""
    html = page(site[1], "person/ada-planner.html")
    assert '<figure class="years">' in html and "Up " not in html.split('<figure class="years">')[1].split("</figure>")[0]
    assert html.count('<g class="yr">') == 5                                # one column slot a year, 2022 to 2026
    assert html.count('class="col') == 4                                    # 2024 had none: no column, still a slot
    assert html.count('class="col part"') == 1 and t("person", "years_partial", year=2026) in html
    assert t("person", "years_openalex") not in html                       # a Scholar chart is the whole series
    assert "The most in one year was 70, in 2025." in html
    assert "<tr><td>2025</td><td class=\"n\">70</td></tr>" in html           # the same figures, as a table


def test_the_years_behind_a_figure_by_source():
    scholar = json.dumps({"cites_per_year": {"2019": 0, "2020": 3, "2021": 0, "2022": 8}})
    assert yearly_counts("google_scholar", scholar) == [(2020, 3), (2021, 0), (2022, 8)]   # leading empty years dropped
    one = json.dumps({"counts_by_year": [{"year": 2026, "cited_by_count": 4}, {"year": 2025, "cited_by_count": 9}]})
    assert yearly_counts("openalex", one) == [(2025, 9), (2026, 4)]
    # several records summed: the reading holds only the first record's years, so no chart
    assert yearly_counts("openalex", json.dumps({"records": [{}, {}], "counts_by_year": [{"year": 2025, "cited_by_count": 9}]})) == []
    assert yearly_counts("pop", json.dumps({"workbook_row": {}})) == []
    assert yearly_counts("google_scholar", json.dumps({"cites_per_year": {"2026": 5}})) == []     # one year is not a series
    assert yearly_counts("google_scholar", "not json") == [] and yearly_counts("google_scholar", None) == []


def test_the_chart_shows_the_last_twenty_years_on_a_round_axis():
    points = [(y, (y - 1980) * 10) for y in range(1985, 2027)]
    svg = year_chart(points, read_year=2026)
    assert svg.count('<g class="yr">') == YEARS_SHOWN and "2007 to 2026" in svg and ">1985<" not in svg
    assert ">500</text>" in svg and ">250</text>" in svg                    # 460 at most, so the axis ends at 500
    assert [axis_top(v) for v in (1, 3, 7, 42, 460, 4261, 5000)] == [1, 5, 10, 50, 500, 5000, 5000]
    assert year_chart([(2026, 5)]) == ""


def test_every_page_says_where_it_lives_and_the_site_lists_its_pages(site):
    """No page had a canonical address or a preview for a shared link, and
    /sitemap.xml, /robots.txt and any mistyped address were GitHub's own 404."""
    _, out = site
    home = page(out, "index.html")
    assert f'<link rel="canonical" href="{build_site.SITE_URL}/">' in home and 'property="og:title"' in home
    assert f'<link rel="canonical" href="{build_site.SITE_URL}/person/ada-planner.html">' in page(out, "person/ada-planner.html")
    assert f'href="{build_site.SITE_URL}/department/big-u.html"' in page(out, "department/big-u.html")
    urls = re.findall(r"<loc>(.*?)</loc>", page(out, "sitemap.xml"))
    assert urls[0] == f"{build_site.SITE_URL}/" and len(urls) == len(set(urls))
    built = {str(f.relative_to(out)) for f in out.rglob("*.html")} - {"404.html", "index.html"}
    assert {u.removeprefix(build_site.SITE_URL + "/") for u in urls[1:]} == built
    assert f"Sitemap: {build_site.SITE_URL}/sitemap.xml" in page(out, "robots.txt")
    missing = page(out, "404.html")
    assert '<meta name="robots" content="noindex">' in missing and "canonical" not in missing
    # served at any depth, so nothing in it may be a relative link
    assert not re.findall(r'(?:href|src)="(?!/|https?:|mailto:|data:|#)', missing)


def test_the_download_keeps_every_figure_and_only_the_newest_source_record(site):
    """The published database was 39 MB, four fifths of the site, and 27 MB of
    it was every monthly copy of every source record."""
    db, out = site
    full, public = sqlite3.connect(db), sqlite3.connect(out / "citations.sqlite")
    figures = "SELECT person_id, source, collected_at, total_citations, h_index FROM metric_snapshot ORDER BY 1, 2, 3"
    assert public.execute(figures).fetchall() == full.execute(figures).fetchall()
    kept = dict(public.execute("SELECT collected_at, raw_json IS NOT NULL FROM metric_snapshot WHERE person_id = 1"))
    assert kept == {"2026-07-06": 0, "2026-11-09": 1}
    assert full.execute("SELECT COUNT(*) FROM metric_snapshot WHERE raw_json IS NULL").fetchone() == (0,)   # the build's own copy is whole
    assert public.execute("SELECT COUNT(*) FROM v_person_percentiles").fetchone() == (5,)                   # the views still answer


def test_the_caption_claims_only_what_the_chart_shows():
    """An OpenAlex chart starts at 2012 whatever came before, and a reading year
    with no citations yet has no paler column to point at."""
    from pipeline.build_site import person_page, uniq_slugs
    p = {"person_id": 9, "display_name": "Ina Fallback", "department": "Big U", "department_id": 1, "rank": "full",
         "source": "openalex", "is_fallback": 1, "collected_at": "2026-11-01", "total_citations": 859, "h_index": 12,
         "pct_citations_all": 0.5, "pct_citations_rank": 0.5, "pct_h_all": 0.5, "pct_h_rank": 0.5,
         "years_since_phd": 30, "citations_per_year": 28.6, "phd_year": 1996, "phd_institution": "", "interests": "",
         "google_scholar_id": "", "openalex_author_id": "A1", "orcid": "", "researchgate_url": "",
         "linkedin_url": "", "personal_url": ""}
    slugs = uniq_slugs([p], lambda x: x["display_name"])
    html = person_page(p, slugs, "big-u", {}, {9: [(2012, 20), (2013, 30), (2025, 40), (2026, 0)]}, "../")
    assert t("person", "years_openalex") in html
    assert t("person", "years_partial", year=2026) not in html and 'class="col part"' not in html
