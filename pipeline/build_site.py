"""Generate the static site from the built database.

    python -m pipeline.build_db && python -m pipeline.build_site
    python -m pipeline.build_site --out build/site --base /planning-citation-metrics/

Writes one page per current faculty member and per department, plus rankings,
methods, a search index, and the database for download. Plain Python and
SQLite, no Node toolchain: the site is ~1,200 read-only pages whose charts are
two-point sparklines, so a framework would add a dependency without adding
anything a reader would notice.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sqlite3
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path

from . import BUILD_DIR
from .site_assets import SEARCH_JS, STYLE, TABLE_JS

RANK_LABEL = {"assistant": "Assistant Professor", "associate": "Associate Professor",
              "full": "Professor", "other": "Faculty"}
SOURCE_LABEL = {"google_scholar": "Google Scholar", "openalex": "OpenAlex",
                "pop": "Publish or Perish", "manual": "entered by hand"}
REPO = "https://github.com/aakarner/planning-citation-metrics"
CONTACT = "planning-citations@austin.utexas.edu"
CONTACT_NAME = "Alex Karner"
SCHOLAR_HELP = "https://scholar.google.com/intl/en/scholar/citations.html"


# ------------------------------------------------------------------ helpers

def e(x) -> str:
    return html.escape(str(x), quote=True) if x is not None else ""


def num(x) -> str:
    return f"{x:,}" if isinstance(x, (int, float)) and x is not None else "&mdash;"


def pct(x) -> str:
    return f"{round(x * 100)}th" if x is not None else "&mdash;"


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "x"


def norm_words(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def uniq_slugs(items, key) -> dict:
    """Stable unique slugs; a collision falls back to appending the row id."""
    seen, out = defaultdict(list), {}
    for it in items:
        seen[slugify(key(it))].append(it)
    for base, group in seen.items():
        for it in group:
            out[id(it)] = base if len(group) == 1 else f"{base}-{it['person_id' if 'person_id' in it.keys() else 'department_id']}"
    return out


def sparkline(points, width=290, height=64, pad=22) -> str:
    """points: [(iso date, value), ...] ascending. Endpoint values are labelled,
    so the drawing never needs an axis."""
    pts = [(d, v) for d, v in points if v is not None]
    if not pts:
        return ""
    vals = [v for _, v in pts]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or max(1, hi * 0.08)
    x0, x1 = 4, width - 4
    inner_h = height - pad - 8

    def xy(i, v):
        x = x0 if len(pts) == 1 else x0 + (x1 - x0) * i / (len(pts) - 1)
        y = 8 + inner_h - (v - lo) / span * inner_h
        return x, y

    coords = [xy(i, v) for i, (_, v) in enumerate(pts)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = (f'<polygon points="{line} {coords[-1][0]:.1f},{8 + inner_h:.1f} '
            f'{coords[0][0]:.1f},{8 + inner_h:.1f}" fill="currentColor" fill-opacity=".08"/>'
            if len(pts) > 1 else "")
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{3.4 if i == len(coords) - 1 else 2.4}" '
                   f'fill="currentColor" fill-opacity="{1 if i == len(coords) - 1 else .5}"/>'
                   for i, (x, y) in enumerate(coords))
    first_lab = (f'<text x="{coords[0][0]:.1f}" y="{height - 6}" font-size="11" fill="currentColor" '
                 f'fill-opacity=".62">{pts[0][0][:7]} &middot; {pts[0][1]:,}</text>'
                 if len(pts) > 1 else "")
    last_lab = (f'<text x="{x1}" y="{height - 6}" font-size="11" text-anchor="end" fill="currentColor" '
                f'fill-opacity=".62">{pts[-1][0][:7]} &middot; {pts[-1][1]:,}</text>')
    return (f'<svg viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Total citations from {pts[0][0]} to {pts[-1][0]}: '
            f'{pts[0][1]:,} to {pts[-1][1]:,}.">{area}'
            f'<polyline points="{line}" fill="none" stroke="currentColor" stroke-width="1.6" '
            f'stroke-linejoin="round"/>{dots}{first_lab}{last_lab}</svg>')


def pct_row(label, value) -> str:
    if value is None:
        return ""
    w = max(1.5, round(value * 100, 1))
    hi = " hi" if value >= 0.75 else ""
    return (f'<div class="pct-row"><span class="lab">{label}</span>'
            f'<span class="pct-track"><span class="pct-fill{hi}" style="width:{w}%"></span></span>'
            f'<span class="v">{pct(value)}</span></div>')


# ----------------------------------------------------------------- template

def page(title, body, base, active="", description="", extra_js=()) -> str:
    nav = [("Rankings", "rankings.html"), ("Departments", "departments.html"),
           ("Methods", "methods.html")]
    links = "".join(
        f'<a href="{base}{href}"{" aria-current=page" if active == href else ""}>{label}</a>'
        for label, href in nav)
    scripts = "".join(f'<script src="{base}{s}" defer></script>' for s in ("search.js",) + tuple(extra_js))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 16 16%22><text y=%2213%22 font-size=%2213%22>&#128218;</text></svg>">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,500;8..60,600&family=Source+Sans+3:wght@400;600&family=IBM+Plex+Mono:wght@400&display=swap">
<link rel="stylesheet" href="{base}style.css">
</head>
<body>
<header class="bar">
  <div class="bar-in">
    <a class="wordmark" href="{base}index.html">Planning <span>Citation Metrics</span><span class="tag">Beta</span></a>
    <div class="search" data-base="{base}">
      <input type="search" placeholder="Search faculty or schools" aria-label="Search faculty or schools"
             autocomplete="off" spellcheck="false">
      <div class="results" hidden></div>
    </div>
    <nav>{links}</nav>
  </div>
</header>
{body}
<footer class="bar">
  <div class="bar-in">
    <p>Citation metrics for urban planning faculty in the United States and Canada, continuing the
       dataset Tom Sanchez maintained from 2017 to 2026.
       <a href="{base}methods.html">How these numbers are produced</a> &middot;
       <a href="{REPO}">Source and data on GitHub</a></p>
    <p>Counts come from public profile pages and change constantly; treat them as approximate.
       This site is in beta. Spotted something wrong, or want your entry changed or removed?
       Email <a href="mailto:{CONTACT}?subject=Planning%20Citation%20Metrics">{CONTACT}</a>
       or <a href="{REPO}/issues/new">open an issue</a>.</p>
  </div>
</footer>
{scripts}
</body>
</html>
"""


# --------------------------------------------------------------------- data

def load(con: sqlite3.Connection) -> dict:
    con.row_factory = sqlite3.Row
    q = lambda sql, *a: [dict(r) for r in con.execute(sql, a)]
    people = q("""
      SELECT pp.*, p.phd_year, p.phd_institution, p.interests, p.google_scholar_id,
             p.openalex_author_id, p.orcid, p.researchgate_url, p.linkedin_url, p.personal_url,
             d.university, d.country, d.url AS dept_url, d.acsp_member
      FROM v_person_percentiles pp
      JOIN person p     ON p.person_id = pp.person_id
      JOIN department d ON d.department_id = pp.department_id
      ORDER BY pp.display_name
    """)
    depts = q("SELECT * FROM v_department_summary ORDER BY short_name")
    oa = {r["person_id"]: r for r in q("SELECT * FROM v_openalex_metrics")}
    series = defaultdict(lambda: defaultdict(list))
    for r in q("SELECT * FROM v_person_timeseries"):
        series[r["person_id"]][r["source"]].append((r["collected_at"], r["total_citations"]))
    asof = {r["source"]: r["d"] for r in q(
        "SELECT source, MAX(collected_at) AS d FROM metric_snapshot GROUP BY source")}
    return {"people": people, "depts": depts, "oa": oa, "series": series, "asof": asof}


# -------------------------------------------------------------------- pages

def person_page(p, slugs, dslug, oa, series, base) -> str:
    fallback = bool(p["is_fallback"])
    src = SOURCE_LABEL.get(p["source"], p["source"])
    pill = (f'<span class="pill pill-oa">{e(src)}</span>' if fallback
            else '<span class="pill pill-gs">Google Scholar</span>')
    note = ""
    if fallback:
        note = (f'<div class="note"><p>This person has no Google Scholar profile we know of, so the '
                f'figures above come from <strong>{e(src)}</strong>. OpenAlex indexes fewer books, '
                f'reports and other non-journal work than Scholar does, so this is very likely an '
                f'undercount next to the Scholar numbers shown elsewhere on this site. '
                f'<a href="{SCHOLAR_HELP}">Creating a Scholar profile</a> takes a few minutes and '
                f'fixes it at the next update.</p></div>')

    head = [(d, v) for d, v in sorted(series.get(p["person_id"], {}).get(p["source"], []))]
    spark = sparkline(head)
    change = ""
    if len(head) > 1 and head[0][1]:
        change = f" Up {(head[-1][1] - head[0][1]) / head[0][1]:.0%} over that span."
    spark_block = (f'<figure class="spark">{spark}<figcaption>Total citations recorded by this '
                   f'project, {e(src)}.{change}</figcaption></figure>' if spark else "")

    o = oa.get(p["person_id"])
    compare = ""
    if o and not fallback and o["total_citations"] is not None:
        compare = f"""<div class="card" style="margin-top:18px">
  <h3>OpenAlex, for comparison</h3>
  <dl class="facts">
    <dt>Citations</dt><dd class="nums">{num(o['total_citations'])}</dd>
    <dt>h-index</dt><dd class="nums">{num(o['h_index'])}</dd>
    <dt>Works</dt><dd class="nums">{num(o['works_count'])}</dd>
  </dl>
  <p class="sub" style="margin:10px 0 0;font-size:13px">OpenAlex is journal-centric and normally
     counts fewer citations than Scholar. Shown for context, not used in rankings.</p>
</div>"""

    links = []
    if p["google_scholar_id"]:
        links.append(f'<li><a href="https://scholar.google.com/citations?user={e(p["google_scholar_id"])}&hl=en">Google Scholar profile</a></li>')
    if p["openalex_author_id"]:
        links.append(f'<li><a href="https://openalex.org/{e(p["openalex_author_id"])}">OpenAlex author record</a></li>')
    if p["orcid"]:
        links.append(f'<li><a href="https://orcid.org/{e(p["orcid"])}">ORCID</a></li>')
    for url, label in ((p["personal_url"], "Personal site"), (p["researchgate_url"], "ResearchGate"),
                       (p["linkedin_url"], "LinkedIn")):
        if url:
            links.append(f'<li><a href="{e(url)}">{label}</a></li>')
    links_block = f'<ul class="links">{"".join(links)}</ul>' if links else ""

    cpy = p["citations_per_year"]
    facts = [("Rank", RANK_LABEL.get(p["rank"], p["rank"]))]
    if p["phd_year"]:
        facts.append(("PhD", f'{p["phd_year"]}' + (f', {e(p["phd_institution"])}' if p["phd_institution"] else "")))
    if p["years_since_phd"]:
        facts.append(("Years since PhD", p["years_since_phd"]))
    if cpy:
        facts.append(("Citations per year", f"{cpy:,.0f}"))
    if p["interests"]:
        facts.append(("Interests", e(p["interests"])))
    facts_block = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts)

    body = f"""<main>
  <p class="eyebrow">Faculty</p>
  <h1>{e(p['display_name'])}</h1>
  <p class="sub">{RANK_LABEL.get(p['rank'], p['rank'])},
     <a href="{base}department/{dslug}.html">{e(p['department'])}</a></p>
  <div class="sheet">
    <div>
      <div class="metric">
        <span class="big nums">{num(p['total_citations'])}</span>
        <span class="side">citations {pill}</span>
        <span class="side">h-index <b class="nums">{num(p['h_index'])}</b></span>
      </div>
      <p class="sub" style="font-size:13.5px">As of {e(p['collected_at'])}.</p>
      {note}
      <div class="pct">
        {pct_row("Citations, all", p["pct_citations_all"])}
        {pct_row(f"Citations, {p['rank']}s", p["pct_citations_rank"])}
        {pct_row("h-index, all", p["pct_h_all"])}
        {pct_row(f"h-index, {p['rank']}s", p["pct_h_rank"])}
      </div>
      <p class="sub" style="font-size:13px;margin-top:10px">Percentile among the
         {RANK_LABEL.get(p['rank'], p['rank']).lower()}s in this dataset, and among all
         faculty in it.</p>
      {spark_block}
    </div>
    <div>
      <div class="card"><h3>Details</h3><dl class="facts">{facts_block}</dl>{links_block}</div>
      {compare}
    </div>
  </div>
</main>"""
    return page(f"{p['display_name']} — Planning Citation Metrics", body, base,
                description=f"Citation metrics for {p['display_name']}, "
                            f"{RANK_LABEL.get(p['rank'], p['rank'])} at {p['department']}.")


def department_page(d, roster, slugs, base) -> str:
    rows = []
    for p in roster:
        pill = ' <span class="pill pill-oa">OpenAlex</span>' if p["is_fallback"] else ""
        rows.append(f"""<tr data-rank="{e(p['rank'])}">
  <td class="name"><a href="{base}person/{slugs[id(p)]}.html">{e(p['display_name'])}</a>{pill}</td>
  <td>{RANK_LABEL.get(p['rank'], p['rank'])}</td>
  <td class="n">{num(p['total_citations'])}</td>
  <td class="n">{num(p['h_index'])}</td>
  <td class="n">{pct(p['pct_citations_rank'])}</td>
  <td class="n">{p['phd_year'] or '&mdash;'}</td>
</tr>""")
    member = {"full": "ACSP full member", "affiliate": "ACSP affiliate member",
              "none": "Not an ACSP member"}.get(d["acsp_member"], "")
    site = f' &middot; <a href="{e(d["url"])}">Program website</a>' if d["url"] else ""
    body = f"""<main>
  <p class="eyebrow">Department</p>
  <h1>{e(d['short_name'])}</h1>
  <p class="sub">{e(member)}{site}</p>
  <div class="tiles" style="margin-top:22px">
    <div class="tile"><span class="n nums">{num(d['n_faculty'])}</span><span class="l">faculty tracked</span></div>
    <div class="tile"><span class="n nums">{num(round(d['median_citations']))}</span><span class="l">median citations</span></div>
    <div class="tile"><span class="n nums">{num(round(d['mean_citations']))}</span><span class="l">mean citations</span></div>
    <div class="tile"><span class="n nums">{num(round(d['citations_per_faculty']))}</span><span class="l">citations per faculty</span></div>
    <div class="tile"><span class="n nums">{num(round(d['median_h_index']))}</span><span class="l">median h-index</span></div>
    <div class="tile"><span class="n nums">{round(d['share_with_scholar_profile'] * 100)}<small>%</small></span><span class="l">have a Scholar profile</span></div>
  </div>
  <h2>Faculty</h2>
  <div class="controls">
    <label>Rank
      <select data-filter-for="roster" data-filter-key="rank">
        <option value="">All</option><option value="full">Professor</option>
        <option value="associate">Associate</option><option value="assistant">Assistant</option>
      </select>
    </label>
    <span class="count"><span id="roster-count">{d['n_faculty']}</span> shown</span>
  </div>
  <div class="tw"><table id="roster" data-sortable>
    <thead><tr><th data-text>Name</th><th data-text>Rank</th><th class="n">Citations</th>
      <th class="n">h-index</th><th class="n">Pct. in rank</th><th class="n">PhD</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
  <p class="sub" style="margin-top:14px;font-size:13.5px">Rows marked
     <span class="pill pill-oa">OpenAlex</span> have no Google Scholar profile, so their figures
     come from OpenAlex and are likely undercounts. See <a href="{base}methods.html">Methods</a>.</p>
</main>"""
    return page(f"{d['short_name']} — Planning Citation Metrics", body, base,
                description=f"Citation metrics for {d['n_faculty']} planning faculty at {d['short_name']}.",
                extra_js=("table.js",))


def index_page(data, slugs, dslugs, base) -> str:
    people, depts = data["people"], data["depts"]
    top_p = sorted(people, key=lambda p: -(p["total_citations"] or 0))[:10]
    top_d = sorted([d for d in depts if d["n_faculty"] >= 3],
                   key=lambda d: -(d["median_citations"] or 0))[:10]
    prow = "".join(f'<tr><td class="rank">{i}</td><td class="name">'
                   f'<a href="{base}person/{slugs[id(p)]}.html">{e(p["display_name"])}</a></td>'
                   f'<td>{e(p["department"])}</td><td class="n">{num(p["total_citations"])}</td>'
                   f'<td class="n">{num(p["h_index"])}</td></tr>'
                   for i, p in enumerate(top_p, 1))
    drow = "".join(f'<tr><td class="rank">{i}</td><td class="name">'
                   f'<a href="{base}department/{dslugs[id(d)]}.html">{e(d["short_name"])}</a></td>'
                   f'<td class="n">{d["n_faculty"]}</td>'
                   f'<td class="n">{num(round(d["median_citations"]))}</td>'
                   f'<td class="n">{num(round(d["mean_citations"]))}</td></tr>'
                   for i, d in enumerate(top_d, 1))
    gs = data["asof"].get("google_scholar", "")
    body = f"""<main class="narrow">
  <section class="beta" aria-labelledby="beta-h">
    <h2 id="beta-h">Beta &middot; in active development</h2>
    <p>This site is new and still being built. Figures, rosters and rankings will change as
       collection improves, and some of what you see today is wrong: appointments are inferred
       from public profiles, a few people are matched to the wrong record, and anyone who has
       moved recently may still be listed at their old program.</p>
    <p>If you find an error in your own entry or anywhere else, or you would rather not be
       listed, email {CONTACT_NAME} at <a href="mailto:{CONTACT}?subject=Planning%20Citation%20Metrics">{CONTACT}</a>.
       Corrections are quick to apply and genuinely welcome. Technical problems can also go in
       the <a href="{REPO}/issues/new">issue tracker</a>.</p>
  </section>
  <h1>Citation metrics for urban planning faculty</h1>
  <p class="lede">Total citations and h-index for {len(people):,} faculty at
     {len(depts)} planning programs in the United States and Canada, refreshed on a schedule
     and kept as a running record rather than overwritten.</p>
  <div class="search hero-search" data-base="{base}">
    <input type="search" placeholder="Search a name or a school&hellip;" aria-label="Search faculty or schools"
           autocomplete="off" spellcheck="false">
    <div class="results" hidden></div>
  </div>
  <p class="sub" style="font-size:13.5px">Press <code>/</code> to jump to search.
     Google Scholar figures as of {e(gs)}.</p>

  <h2>Most cited faculty</h2>
  <div class="tw"><table>
    <thead><tr><th data-nosort></th><th>Name</th><th>School</th><th class="n">Citations</th><th class="n">h-index</th></tr></thead>
    <tbody>{prow}</tbody>
  </table></div>
  <p class="sub" style="margin-top:10px"><a href="{base}rankings.html">Full faculty rankings &rarr;</a></p>

  <h2>Programs by median citations</h2>
  <div class="tw"><table>
    <thead><tr><th data-nosort></th><th>School</th><th class="n">Faculty</th><th class="n">Median</th><th class="n">Mean</th></tr></thead>
    <tbody>{drow}</tbody>
  </table></div>
  <p class="sub" style="margin-top:10px">Programs with at least three faculty tracked.
     <a href="{base}departments.html">All programs &rarr;</a></p>

  <h2>What to know before citing these numbers</h2>
  <p>Citation counts measure attention, not quality, and they differ enormously by subfield,
     career stage, and publication venue. A number here is a rough indicator of reach at one
     moment, nothing more. The <a href="{base}methods.html">methods page</a> explains where each
     figure comes from and the ways it can be wrong.</p>
</main>"""
    return page("Planning Citation Metrics", body, base, active="index.html",
                description=f"Citation metrics for {len(people):,} urban planning faculty at "
                            f"{len(depts)} programs in the US and Canada.")


def rankings_page(data, slugs, base) -> str:
    people = sorted(data["people"], key=lambda p: -(p["total_citations"] or 0))
    rows = []
    for i, p in enumerate(people, 1):
        pill = ' <span class="pill pill-oa">OA</span>' if p["is_fallback"] else ""
        rows.append(f"""<tr data-rank="{e(p['rank'])}" data-country="{e(p['country'])}">
  <td class="rank">{i}</td>
  <td class="name"><a href="{base}person/{slugs[id(p)]}.html">{e(p['display_name'])}</a>{pill}</td>
  <td>{e(p['department'])}</td>
  <td>{RANK_LABEL.get(p['rank'], p['rank'])}</td>
  <td class="n">{num(p['total_citations'])}</td>
  <td class="n">{num(p['h_index'])}</td>
  <td class="n" data-v="{p['pct_citations_rank'] or 0}">{pct(p['pct_citations_rank'])}</td>
  <td class="n" data-v="{p['citations_per_year'] or 0}">{f"{p['citations_per_year']:,.0f}" if p['citations_per_year'] else '&mdash;'}</td>
</tr>""")
    body = f"""<main>
  <p class="eyebrow">Rankings</p>
  <h1>Faculty by total citations</h1>
  <p class="lede">Click any column to re-sort. Rows marked <span class="pill pill-oa">OA</span>
     use OpenAlex figures because the person has no Google Scholar profile; those are likely
     undercounts.</p>
  <div class="controls">
    <label>Rank
      <select data-filter-for="faculty" data-filter-key="rank">
        <option value="">All</option><option value="full">Professor</option>
        <option value="associate">Associate</option><option value="assistant">Assistant</option>
      </select>
    </label>
    <label>Country
      <select data-filter-for="faculty" data-filter-key="country">
        <option value="">All</option><option value="US">United States</option><option value="CA">Canada</option>
      </select>
    </label>
    <span class="count"><span id="faculty-count">{len(people):,}</span> shown</span>
  </div>
  <div class="tw"><table id="faculty" data-sortable>
    <thead><tr><th data-nosort></th><th data-text>Name</th><th data-text>School</th><th data-text>Rank</th>
      <th class="n">Citations</th><th class="n">h-index</th><th class="n">Pct. in rank</th>
      <th class="n">Per year</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
</main>"""
    return page("Faculty rankings — Planning Citation Metrics", body, base, active="rankings.html",
                description="Urban planning faculty ranked by total citations, sortable and filterable.",
                extra_js=("table.js",))


def departments_page(data, dslugs, base) -> str:
    depts = sorted(data["depts"], key=lambda d: -(d["median_citations"] or 0))
    rows = []
    for i, d in enumerate(depts, 1):
        rows.append(f"""<tr data-country="{e(d['country'])}" data-acsp="{e(d['acsp_member'])}">
  <td class="rank">{i}</td>
  <td class="name"><a href="{base}department/{dslugs[id(d)]}.html">{e(d['short_name'])}</a></td>
  <td class="n">{d['n_faculty']}</td>
  <td class="n">{num(round(d['median_citations']))}</td>
  <td class="n">{num(round(d['mean_citations']))}</td>
  <td class="n">{num(d['total_citations'])}</td>
  <td class="n">{num(round(d['median_h_index']))}</td>
  <td class="n" data-v="{d['share_with_scholar_profile']}">{round(d['share_with_scholar_profile'] * 100)}%</td>
</tr>""")
    body = f"""<main>
  <p class="eyebrow">Departments</p>
  <h1>Planning programs</h1>
  <p class="lede">{len(depts)} programs, ranked by the median citation count of the faculty tracked
     at each. Median rather than mean, because one very highly cited person moves a mean a long way.</p>
  <div class="controls">
    <label>Country
      <select data-filter-for="depts" data-filter-key="country">
        <option value="">All</option><option value="US">United States</option><option value="CA">Canada</option>
      </select>
    </label>
    <label>ACSP
      <select data-filter-for="depts" data-filter-key="acsp">
        <option value="">All</option><option value="full">Full member</option>
        <option value="affiliate">Affiliate</option><option value="none">Non-member</option>
      </select>
    </label>
    <span class="count"><span id="depts-count">{len(depts)}</span> shown</span>
  </div>
  <div class="tw"><table id="depts" data-sortable>
    <thead><tr><th data-nosort></th><th data-text>School</th><th class="n">Faculty</th>
      <th class="n">Median cites</th><th class="n">Mean cites</th><th class="n">Total cites</th>
      <th class="n">Median h</th><th class="n">Scholar profiles</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
</main>"""
    return page("Planning programs — Planning Citation Metrics", body, base, active="departments.html",
                description=f"{len(depts)} urban planning programs ranked by faculty citation metrics.",
                extra_js=("table.js",))


def methods_page(data, base, db_name) -> str:
    a = data["asof"]
    n_fb = sum(1 for p in data["people"] if p["is_fallback"])
    body = f"""<main class="narrow">
  <p class="eyebrow">Methods</p>
  <h1>Where these numbers come from</h1>
  <p class="lede">This project continues a dataset Tom Sanchez maintained by hand from 2017 to 2026.
     It is now collected on a schedule, stored as a running record, and published from a public
     repository.</p>

  <h2>Sources, in order of preference</h2>
  <p><strong>Google Scholar</strong> is the headline source. It counts books, reports and other
     non-journal work that matters in planning, and it is what the field is used to seeing. Of the
     {len(data['people']):,} faculty here, {len(data['people']) - n_fb:,} have a Scholar profile.
     Last refreshed {e(a.get('google_scholar', 'n/a'))}.</p>
  <p><strong>OpenAlex</strong> covers the remaining {n_fb} people, who have no Scholar profile we
     could find. Those figures are marked <span class="pill pill-oa">OpenAlex</span> everywhere they
     appear. OpenAlex is journal-centric and normally reports fewer citations than Scholar for the
     same person, so those numbers are very likely undercounts and are not comparable to the Scholar
     ones. We do not adjust or scale them; we label them.
     Last refreshed {e(a.get('openalex', 'n/a'))}.</p>
  <p>Anyone shown with an OpenAlex figure can move to the Scholar series by
     <a href="{SCHOLAR_HELP}">creating a Scholar profile</a>. We re-check for new profiles each
     semester.</p>

  <h2>How often it updates</h2>
  <p>OpenAlex monthly, Google Scholar quarterly, faculty rosters twice a year. Every run is
     appended, never overwritten, so the history builds up from the first snapshot in
     February and March 2026 rather than being reconstructed backwards.</p>

  <h2>Who is included</h2>
  <p>Regular full-time tenured and tenure-track faculty at planning programs in the United States
     and Canada: the population Tom tracked. Adjunct, visiting and clinical appointments are out.
     The school list began as the membership of the Association of Collegiate Schools of Planning
     and has a handful of programs added on request, mostly Canadian. Each program page says which
     it is.</p>

  <h2>Percentiles and per-year figures</h2>
  <p>Percentiles are computed across the faculty in this dataset, both overall and within academic
     rank, so a percentile means "relative to the people on this site", not to all of academia.
     Citations per year divides total citations by years since the PhD, which flatters nobody
     consistently: it favours people whose early work was widely cited and penalises anyone whose
     PhD year we have wrong.</p>

  <h2>Known ways this is wrong</h2>
  <ul>
    <li><strong>Profiles lag reality.</strong> Appointments are updated from what a Scholar profile
        says, so someone who moved but has not updated their profile stays put here.</li>
    <li><strong>Name collisions.</strong> A common name can pick up another researcher's record,
        particularly on the OpenAlex side. Matches were reviewed, but not all of them.</li>
    <li><strong>Scholar counts are generous.</strong> They include citations from preprints, theses
        and self-citations, and they change from week to week.</li>
    <li><strong>Missing people.</strong> New hires appear only after the next roster review, and
        anyone recorded as having left a tracked program drops out of the site entirely, so a
        search for them returns nothing even though their earlier figures remain in the
        downloadable data.</li>
  </ul>
  <p>The site is in beta, so expect all of the above to be more common now than it will be
     later. Corrections are welcome and quick to apply: email {CONTACT_NAME} at
     <a href="mailto:{CONTACT}?subject=Planning%20Citation%20Metrics%20correction">{CONTACT}</a>,
     or <a href="{REPO}/issues/new">open an issue</a>. If you would rather not have an individual
     page here at all, say so and it will be removed.</p>

  <h2>Get the data</h2>
  <p>Everything is public. The curated roster and every dated snapshot live as CSV in the
     <a href="{REPO}/tree/main/data">repository</a>; the built database, with the percentile and
     summary views already defined, is one file:</p>
  <p><a href="{base}{db_name}"><strong>citations.sqlite</strong></a> &middot;
     <a href="{REPO}">pipeline source</a></p>
  <p>Please cite it as: Karner, A. and T. W. Sanchez ({date.today().year}).
     <em>Planning Citation Metrics</em>. {REPO}</p>
</main>"""
    return page("Methods — Planning Citation Metrics", body, base, active="methods.html",
                description="Sources, update cadence, inclusion criteria, and known limitations.")


# --------------------------------------------------------------------- main

def build(db: Path, out: Path, base: str, quiet: bool = False) -> None:
    log = (lambda *a: None) if quiet else print
    con = sqlite3.connect(db)
    data = load(con)
    con.close()
    people, depts = data["people"], data["depts"]
    slugs = uniq_slugs(people, lambda p: p["display_name"])
    dslugs = uniq_slugs(depts, lambda d: d["short_name"])
    dslug_by_id = {d["department_id"]: dslugs[id(d)] for d in depts}

    if out.exists():
        shutil.rmtree(out)
    (out / "person").mkdir(parents=True)
    (out / "department").mkdir(parents=True)

    (out / "style.css").write_text(STYLE)
    (out / "search.js").write_text(SEARCH_JS)
    (out / "table.js").write_text(TABLE_JS)
    (out / ".nojekyll").write_text("")

    roster = defaultdict(list)
    for p in people:
        roster[p["department_id"]].append(p)

    (out / "index.html").write_text(index_page(data, slugs, dslugs, ""))
    (out / "rankings.html").write_text(rankings_page(data, slugs, ""))
    (out / "departments.html").write_text(departments_page(data, dslugs, ""))
    shutil.copy(db, out / "citations.sqlite")
    (out / "methods.html").write_text(methods_page(data, "", "citations.sqlite"))

    for p in people:
        (out / "person" / f"{slugs[id(p)]}.html").write_text(
            person_page(p, slugs, dslug_by_id[p["department_id"]], data["oa"], data["series"], "../"))
    for d in depts:
        members = sorted(roster[d["department_id"]],
                         key=lambda x: -(x["total_citations"] or 0))
        (out / "department" / f"{dslugs[id(d)]}.html").write_text(
            department_page(d, members, slugs, "../"))

    index = [{"t": p["display_name"], "n": norm_words(p["display_name"]),
              "w": norm_words(p["department"]), "d": p["department"],
              "u": f"person/{slugs[id(p)]}.html", "c": p["total_citations"] or 0} for p in people]
    index += [{"t": d["short_name"], "n": norm_words(d["short_name"]),
               "w": norm_words(f"{d['university']} planning program department"),
               "d": f"{d['n_faculty']} faculty", "u": f"department/{dslugs[id(d)]}.html",
               "c": d["total_citations"] or 0} for d in depts]
    (out / "search.json").write_text(json.dumps(index, separators=(",", ":"), ensure_ascii=False))

    n = sum(1 for _ in out.rglob("*") if _.is_file())
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6
    log(f"  {len(people):,} faculty pages, {len(depts)} department pages")
    log(f"  search index {(out / 'search.json').stat().st_size / 1e3:.0f} KB")
    log(f"  built {out}  ({n} files, {size:.1f} MB)")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=BUILD_DIR / "citations.sqlite")
    ap.add_argument("--out", type=Path, default=BUILD_DIR / "site")
    ap.add_argument("--base", default="", help="path prefix for absolute links (unused; links are relative)")
    args = ap.parse_args(argv)
    build(args.db, args.out, args.base)


if __name__ == "__main__":
    main()
