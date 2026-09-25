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
import csv
import json
import re
import shutil
import sqlite3
import tomllib
import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from . import BUILD_DIR, REPO_ROOT
from .site_assets import SEARCH_JS, STYLE, TABLE_JS

COPY_PATH = REPO_ROOT / "site" / "copy.toml"


def load_copy(path: Path = COPY_PATH) -> dict:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise SystemExit(f"no copy file at {path}")
    except tomllib.TOMLDecodeError as err:
        raise SystemExit(f"{path} is not valid TOML: {err}\n"
                         "A long passage must be wrapped in triple quotes, and a quote "
                         "inside one is fine as-is.")


COPY = load_copy()


def t(section: str, key: str, **values) -> str:
    """One passage from site/copy.toml, with its live values filled in.

    Anything wrong in the content file stops the build here with a message
    naming the passage, rather than shipping a page with a hole in it."""
    try:
        template = COPY[section][key]
    except KeyError:
        raise SystemExit(f"{COPY_PATH}: [{section}] has no '{key}'. "
                         f"It has: {', '.join(sorted(COPY.get(section, {})))}")
    try:
        # The content file says line breaks inside a passage are cosmetic, so
        # make that true: collapse them, and the page HTML stops depending on
        # how someone happened to wrap a sentence.
        return re.sub(r"\s+", " ", template.format(**values)).strip()
    except KeyError as err:
        raise SystemExit(
            f"{COPY_PATH}: [{section}] {key} uses {{{err.args[0]}}}, which this page does not "
            f"provide.\nAvailable here: {', '.join(sorted(values)) or '(none)'}")
    except (IndexError, ValueError) as err:
        raise SystemExit(f"{COPY_PATH}: [{section}] {key} has a malformed placeholder ({err}). "
                         "To write a literal brace, double it: {{ }}")

RANK_LABEL = {"assistant": "Assistant Professor", "associate": "Associate Professor",
              "full": "Professor", "other": "Faculty"}
SOURCE_LABEL = {"google_scholar": "Google Scholar", "openalex": "OpenAlex",
                "pop": "Publish or Perish", "manual": "entered by hand"}
PILL_LABEL = {"openalex": "OpenAlex", "pop": "PoP", "manual": "manual"}     # on a fallback row
PILL_SHORT = {"openalex": "OA", "pop": "PoP", "manual": "manual"}           # in the dense rankings table


def fallback_note(source: str, **values) -> str:
    """The wording under a non-Scholar figure, by source. A Publish or Perish
    page used to describe OpenAlex, which it had nothing to do with."""
    key = "fallback_note_pop" if source == "pop" else "fallback_note_openalex"
    return t("person", key, **values)
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

def donate_block() -> str:
    """The footer's donate line, or nothing at all while no destination is set."""
    url = (COPY["site"].get("donate_url") or "").strip()
    if not url:
        return ""
    return (f'    <p class="donate">{t("site", "donate_text")} '
            f'<a class="btn" href="{e(url)}" rel="noopener">{t("site", "donate_button")}</a></p>\n')


def page(title, body, base, active="", description="", extra_js=()) -> str:
    nav = [(t("site", "nav_rankings"), "rankings.html"),
           (t("site", "nav_departments"), "departments.html"),
           (t("site", "nav_methods"), "methods.html")]
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
    <a class="wordmark" href="{base}index.html">{t("site", "wordmark_lead")} <span>{t("site", "wordmark_rest")}</span><span class="tag">{t("site", "beta_tag")}</span></a>
    <div class="search" data-base="{base}">
      <input type="search" placeholder="{t("site", "search_placeholder")}" aria-label="{t("site", "search_placeholder")}"
             autocomplete="off" spellcheck="false">
      <div class="results" hidden></div>
    </div>
    <nav>{links}</nav>
  </div>
</header>
{body}
<footer class="bar">
  <div class="bar-in">
    <p>{t("site", "footer_about", methods_href=f"{base}methods.html", repo=REPO, contact=CONTACT)}</p>
    <p>{t("site", "footer_corrections", methods_href=f"{base}methods.html", repo=REPO, contact=CONTACT)}</p>
{donate_block()}  </div>
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
    bench = {r["rank"]: r for r in q("SELECT * FROM v_rank_benchmarks")}
    affs = q("SELECT a.*, d.short_name AS dept FROM affiliation a JOIN department d USING(department_id)")
    gs_dates = [r["d"] for r in q("SELECT DISTINCT collected_at AS d FROM metric_snapshot "
                                  "WHERE source='google_scholar' ORDER BY 1")]
    mix = {r["source"]: r["n"] for r in q("SELECT h.source, COUNT(*) AS n FROM v_headline_metrics h "
                                         "JOIN v_current_affiliation ca ON ca.person_id = h.person_id GROUP BY 1")}
    return {"people": people, "depts": depts, "oa": oa, "series": series, "asof": asof,
            "bench": bench, "affs": affs, "gs_dates": gs_dates, "mix": mix}


# -------------------------------------------------------------------- pages

def person_page(p, slugs, dslug, oa, series, base) -> str:
    fallback = bool(p["is_fallback"])
    src = SOURCE_LABEL.get(p["source"], p["source"])
    pill = (f'<span class="pill pill-oa">{e(src)}</span>' if fallback
            else '<span class="pill pill-gs">Google Scholar</span>')
    note = ""
    if fallback:
        note = ('<div class="note"><p>'
                + fallback_note(p["source"], scholar_help=SCHOLAR_HELP, collected_at=e(p["collected_at"] or ""))
                + '</p></div>')

    head = [(d, v) for d, v in sorted(series.get(p["person_id"], {}).get(p["source"], []))]
    spark = sparkline(head)
    change = ""
    if len(head) > 1 and head[0][1]:
        change = t("person", "spark_change",
                   percent=f"{(head[-1][1] - head[0][1]) / head[0][1]:.0%}")
    spark_block = (f'<figure class="spark">{spark}<figcaption>'
                   + t("person", "spark_caption", source=e(src), change=change)
                   + '</figcaption></figure>' if spark else "")

    o = oa.get(p["person_id"])
    compare = ""
    if o and not fallback and o["total_citations"] is not None:
        compare = f"""<div class="card" style="margin-top:18px">
  <h3>{t("person", "compare_heading")}</h3>
  <dl class="facts">
    <dt>Citations</dt><dd class="nums">{num(o['total_citations'])}</dd>
    <dt>h-index</dt><dd class="nums">{num(o['h_index'])}</dd>
    <dt>Works</dt><dd class="nums">{num(o['works_count'])}</dd>
  </dl>
  <p class="sub" style="margin:10px 0 0;font-size:13px">{t("person", "compare_note")}</p>
</div>"""

    links = []
    if p["google_scholar_id"]:
        links.append(f'<li><a href="https://scholar.google.com/citations?user={e(p["google_scholar_id"])}&hl=en">{t("person", "link_scholar")}</a></li>')
    if p["openalex_author_id"]:
        links.append(f'<li><a href="https://openalex.org/{e(p["openalex_author_id"])}">{t("person", "link_openalex")}</a></li>')
    if p["orcid"]:
        links.append(f'<li><a href="https://orcid.org/{e(p["orcid"])}">{t("person", "link_orcid")}</a></li>')
    for url, label in ((p["personal_url"], t("person", "link_personal")),
                       (p["researchgate_url"], t("person", "link_researchgate")),
                       (p["linkedin_url"], t("person", "link_linkedin"))):
        if url:
            links.append(f'<li><a href="{e(url)}">{label}</a></li>')
    links_block = f'<ul class="links">{"".join(links)}</ul>' if links else ""

    cpy = p["citations_per_year"]
    facts = [(t("person", "label_rank"), RANK_LABEL.get(p["rank"], p["rank"]))]
    if p["phd_year"]:
        facts.append((t("person", "label_phd"),
                      f'{p["phd_year"]}' + (f', {e(p["phd_institution"])}' if p["phd_institution"] else "")))
    if p["years_since_phd"]:
        facts.append((t("person", "label_years"), p["years_since_phd"]))
    if cpy:
        facts.append((t("person", "label_per_year"), f"{cpy:,.0f}"))
    if p["interests"]:
        facts.append((t("person", "label_interests"), e(p["interests"])))
    facts_block = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts)

    body = f"""<main>
  <p class="eyebrow">{t("person", "eyebrow")}</p>
  <h1>{e(p['display_name'])}</h1>
  <p class="sub">{RANK_LABEL.get(p['rank'], p['rank'])},
     <a href="{base}department/{dslug}.html">{e(p['department'])}</a></p>
  <div class="sheet">
    <div>
      <div class="metric">
        <span class="big nums">{num(p['total_citations'])}</span>
        <span class="side">{t("person", "citations_label")} {pill}</span>
        <span class="side">{t("person", "h_index_label")} <b class="nums">{num(p['h_index'])}</b></span>
      </div>
      <p class="sub" style="font-size:13.5px">{t("person", "as_of", collected_at=e(p['collected_at']))}</p>
      {note}
      <div class="pct">
        {pct_row(t("person", "pct_citations_all"), p["pct_citations_all"])}
        {pct_row(t("person", "pct_citations_rank", rank=p['rank']), p["pct_citations_rank"])}
        {pct_row(t("person", "pct_h_all"), p["pct_h_all"])}
        {pct_row(t("person", "pct_h_rank", rank=p['rank']), p["pct_h_rank"])}
      </div>
      <p class="sub" style="font-size:13px;margin-top:10px">{t("person", "percentile_note", rank_label=RANK_LABEL.get(p['rank'], p['rank']).lower())}</p>
      {spark_block}
    </div>
    <div>
      <div class="card"><h3>{t("person", "details_heading")}</h3><dl class="facts">{facts_block}</dl>{links_block}</div>
      {compare}
    </div>
  </div>
</main>"""
    return page(f"{p['display_name']} — {t('site', 'title_suffix')}", body, base,
                description=t("person", "meta_description", name=p["display_name"],
                              rank_label=RANK_LABEL.get(p["rank"], p["rank"]),
                              department=p["department"]))


def department_page(d, roster, slugs, base) -> str:
    rows = []
    for p in roster:
        pill = f' <span class="pill pill-oa">{PILL_LABEL.get(p["source"], e(p["source"]))}</span>' if p["is_fallback"] else ""
        rows.append(f"""<tr data-rank="{e(p['rank'])}">
  <td class="name"><a href="{base}person/{slugs[id(p)]}.html">{e(p['display_name'])}</a>{pill}</td>
  <td>{RANK_LABEL.get(p['rank'], p['rank'])}</td>
  <td class="n">{num(p['total_citations'])}</td>
  <td class="n">{num(p['h_index'])}</td>
  <td class="n">{pct(p['pct_citations_rank'])}</td>
  <td class="n">{p['phd_year'] or '&mdash;'}</td>
</tr>""")
    member = {"full": t("department", "member_full"),
              "affiliate": t("department", "member_affiliate"),
              "none": t("department", "member_none")}.get(d["acsp_member"], "")
    site = f' &middot; <a href="{e(d["url"])}">{t("department", "program_link")}</a>' if d["url"] else ""
    body = f"""<main>
  <p class="eyebrow">{t("department", "eyebrow")}</p>
  <h1>{e(d['short_name'])}</h1>
  <p class="sub">{e(member)}{site}</p>
  <div class="tiles" style="margin-top:22px">
    <div class="tile"><span class="n nums">{num(d['n_faculty'])}</span><span class="l">{t("department", "tile_faculty")}</span></div>
    <div class="tile"><span class="n nums">{num(round(d['median_citations']))}</span><span class="l">{t("department", "tile_median")}</span></div>
    <div class="tile"><span class="n nums">{num(round(d['mean_citations']))}</span><span class="l">{t("department", "tile_mean")}</span></div>
    <div class="tile"><span class="n nums">{num(round(d['citations_per_faculty']))}</span><span class="l">{t("department", "tile_per_faculty")}</span></div>
    <div class="tile"><span class="n nums">{num(round(d['median_h_index']))}</span><span class="l">{t("department", "tile_median_h")}</span></div>
    <div class="tile"><span class="n nums">{round(d['share_with_scholar_profile'] * 100)}<small>%</small></span><span class="l">{t("department", "tile_scholar_share")}</span></div>
  </div>
  <h2>{t("department", "faculty_heading")}</h2>
  <div class="controls">
    <label>{t("department", "filter_rank")}
      <select data-filter-for="roster" data-filter-key="rank">
        <option value="">All</option><option value="full">Professor</option>
        <option value="associate">Associate</option><option value="assistant">Assistant</option>
      </select>
    </label>
    <span class="count"><span id="roster-count">{d['n_faculty']}</span> {t("department", "shown")}</span>
  </div>
  <div class="tw"><table id="roster" data-sortable>
    <thead><tr><th data-text>Name</th><th data-text>Rank</th><th class="n">Citations</th>
      <th class="n">h-index</th><th class="n">Pct. in rank</th><th class="n">PhD</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
  <p class="sub" style="margin-top:14px;font-size:13.5px">{t("department", "fallback_footnote", methods_href=f"{base}methods.html")}</p>
</main>"""
    return page(f"{d['short_name']} — {t('site', 'title_suffix')}", body, base,
                description=t("department", "meta_description", n_faculty=d["n_faculty"],
                              short_name=d["short_name"]),
                extra_js=("table.js",))


WINDOW_DAYS = 7      # snapshot files this close together are one collection run
RANK_ORDER = {"assistant": 0, "associate": 1, "full": 2}


def longdate(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B %Y')}"


def collection_window(dates: list[str]) -> str | None:
    """First day of the latest collection run.

    Files chain into a run when each is within WINDOW_DAYS of the one before
    it -- not of the newest. A two-person fetch a week after a quarterly run
    belongs to that run; anchoring on the newest file split it, and the
    homepage reported zero promotions the moment a targeted fetch landed.
    Quarterly runs are ninety days apart, so a chain never bridges two.
    """
    if not dates:
        return None
    dates = sorted(dates)
    start = dates[-1]
    for d in reversed(dates[:-1]):
        if (date.fromisoformat(start) - date.fromisoformat(d)).days <= WINDOW_DAYS:
            start = d
        else:
            break
    return start


def recent_changes(affs: list[dict], since: str | None) -> dict:
    """Moves, promotions and departures dated on or after `since`.

    A change is a row closed on day X and, for moves and promotions, a new row
    for the same person opened on day X: same department with a higher rank is
    a promotion, a different department is a move. A person whose row closed
    and who has no open row left the tracked programs. Pure, so it is tested.
    """
    out = {"moves": [], "promos": [], "departed": 0}
    if not since:
        return out
    open_by = {a["person_id"]: a for a in affs if not a["end_date"] and str(a["is_primary"]) == "1"}
    for old in affs:
        if not old["end_date"] or old["end_date"] < since or str(old["is_primary"]) != "1":
            continue
        new = open_by.get(old["person_id"])
        if new is None:
            out["departed"] += 1
        elif new["start_date"] != old["end_date"]:
            continue                       # closed for some other reason; not a change we narrate
        elif new["department_id"] != old["department_id"]:
            out["moves"].append((old["person_id"], old["dept"], new["dept"]))
        elif RANK_ORDER.get(new["rank"], -1) > RANK_ORDER.get(old["rank"], -1):
            out["promos"].append((old["person_id"], new["dept"], old["rank"], new["rank"]))
    return out


def discovery_finds(since: str | None, review_csv: Path = REPO_ROOT / "data" / "review" / "identity_candidates.csv") -> list[dict]:
    """Scholar profiles the discovery search accepted on or after `since`."""
    if not since or not review_csv.exists():
        return []
    with review_csv.open(newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f)
                if r["source"] == "google_scholar" and r["status"] == "accepted"
                and (r.get("reviewed_at") or "") >= since]


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
    oa_date = data["asof"].get("openalex", "")
    pop_date = data["asof"].get("pop", "")
    mix = data["mix"]
    # keyed by string: the affiliation rows and the review CSV disagree on the type
    by_pid = {str(p["person_id"]): p for p in people}
    plink = lambda pid: (f'<a href="{base}person/{slugs[id(by_pid[pid])]}.html">{e(by_pid[pid]["display_name"])}</a>'
                         if pid in by_pid else "")
    rank_label = {"assistant": t("home", "rank_assistant"), "associate": t("home", "rank_associate"),
                  "full": t("home", "rank_full")}

    brow = "".join(f'<tr><td class="name">{rank_label[rk]}</td><td class="n">{b["n"]}</td>'
                   f'<td class="n">{num(b["median_citations"])}</td><td class="n">{num(b["p75_citations"])}</td>'
                   f'<td class="n">{b["median_h_index"]}</td></tr>'
                   for rk in ("assistant", "associate", "full") if (b := data["bench"].get(rk)))
    a_med = data["bench"].get("assistant", {}).get("median_citations") or 0

    since = collection_window(data["gs_dates"])
    ch = recent_changes(data["affs"], since)
    found = discovery_finds(since)
    # Build the items first and count those, so a badge can never disagree
    # with the list under it (someone found and since departed is in neither).
    found_items = [f'<li>{plink(str(r["person_id"]))}<span class="d">{e(r["department"] or "")}</span>'
                   f'<span class="when">{e(longdate(r["reviewed_at"]))}</span></li>'
                   for r in found if str(r["person_id"]) in by_pid]
    move_items = [f'<li>{plink(str(pid))}<span class="d">{e(o)} &rarr; <b>{e(n)}</b></span></li>'
                  for pid, o, n in ch["moves"] if str(pid) in by_pid]
    promo_items = [f'<li>{plink(str(pid))}<span class="d">{e(d)} &middot; {o} &rarr; <b>{n}</b></span></li>'
                   for pid, d, o, n in ch["promos"] if str(pid) in by_pid]
    found_li, moves_li, promos_li = "".join(found_items), "".join(move_items), "".join(promo_items)
    update_month = date.fromisoformat(since).strftime("%B") if since else ""
    pop_month = date.fromisoformat(pop_date).strftime("%B") if pop_date else ""
    # one swatch per source, in the order the sentence names them
    mix_key = t("home", "mix_key", gs_n=mix.get("google_scholar", 0), oa_n=mix.get("openalex", 0),
                pop_n=mix.get("pop", 0), pop_month=pop_month, scholar_help=SCHOLAR_HELP)
    mix_key = ('<i class="k gs"></i>' + mix_key.replace(" &middot; ", ' &middot; <i class="k oa"></i>', 1))
    head, sep, tail = mix_key.partition(' &middot; <i class="k oa"></i>')
    tail = tail.replace(" &middot; ", ' &middot; <i class="k pop"></i>', 1)
    mix_key = head + sep + tail
    if found_li or moves_li or promos_li or ch["departed"]:
        new_block = f"""
  <h2>{t("home", "new_heading")}</h2>
  <p class="sub" style="margin:-6px 0 14px">{t("home", "new_lede", update_month=update_month)}</p>
  <div class="cols2">
    <div class="col"><h3>{t("home", "new_found_heading")} <span class="count">{len(found_items)}</span></h3>
      <p class="sub">{t("home", "new_found_note")}</p>
      <ul class="plain">{found_li}</ul></div>
    <div class="col"><h3>{t("home", "new_moves_heading")} <span class="count">{len(move_items)}</span></h3>
      <ul class="plain">{moves_li}</ul></div>
  </div>
  <details class="fold" style="margin-top:14px"><summary>{t("home", "new_promos_heading")} <span class="count">{len(promo_items)}</span></summary>
    <ul class="plain two">{promos_li}</ul></details>
  <p class="sub" style="margin-top:12px">{t("home", "new_departed", n=ch["departed"], repo=REPO)}</p>"""
    else:
        new_block = f"""
  <h2>{t("home", "new_heading")}</h2>
  <p class="sub">{t("home", "new_empty", update_month=update_month)}</p>"""

    body = f"""<main class="narrow">
  <section class="beta" aria-labelledby="beta-h">
    <h2 id="beta-h">{t("home", "beta_title")}</h2>
    <p>{t("home", "beta_body_1")}</p>
    <p>{t("home", "beta_body_2", contact=CONTACT, repo=REPO)}</p>
  </section>
  <h1>{t("home", "heading")}</h1>
  <p class="lede">{t("home", "lede", people_count=f"{len(people):,}", dept_count=len(depts))}</p>
  <div class="search hero-search" data-base="{base}">
    <input type="search" placeholder="{t("home", "search_placeholder")}" aria-label="{t("site", "search_placeholder")}"
           autocomplete="off" spellcheck="false">
    <div class="results" hidden></div>
  </div>
  <p class="sub" style="font-size:13.5px">{t("home", "search_hint", scholar_date=e(gs))}</p>

  <section class="status" aria-label="Dataset status">
    <div class="tiles">
      <div class="tile"><div class="n">{len(people):,}</div><div class="l">{t("home", "status_faculty")}</div></div>
      <div class="tile"><div class="n">{len(depts)}</div><div class="l">{t("home", "status_programs")}</div></div>
      <div class="tile"><div class="n" style="font-size:19px">{e(longdate(gs)) if gs else "&mdash;"}</div><div class="l">{t("home", "status_scholar")}</div></div>
      <div class="tile"><div class="n" style="font-size:19px">{e(longdate(oa_date)) if oa_date else "&mdash;"}</div><div class="l">{t("home", "status_openalex")}</div></div>
    </div>
    <div class="mix" role="img" aria-label="Source of each headline figure">
      <span class="seg gs" style="flex:{mix.get('google_scholar', 0)}"></span><span class="seg oa" style="flex:{mix.get('openalex', 0)}"></span><span class="seg pop" style="flex:{mix.get('pop', 0)}"></span>
    </div>
    <p class="sub mixkey">{mix_key}</p>
  </section>

  <h2>{t("home", "field_heading")}</h2>
  <div class="tw"><table>
    <thead><tr><th>{t("home", "th_rank")}</th><th class="n">{t("home", "th_faculty")}</th><th class="n">{t("home", "th_median")}</th><th class="n">{t("home", "th_p75")}</th><th class="n">{t("home", "th_median_h")}</th></tr></thead>
    <tbody>{brow}</tbody>
  </table></div>
  <p class="sub" style="margin-top:10px">{t("home", "field_note", scholar_date=e(longdate(gs)) if gs else "", assistant_median=num(a_med))}</p>
{new_block}

  <h2>{t("home", "boards_heading")}</h2>
  <p class="sub" style="margin:-6px 0 12px">{t("home", "boards_lede", rankings_href=f"{base}rankings.html", departments_href=f"{base}departments.html")}</p>
  <details class="fold"><summary>{t("home", "top_faculty_heading")}</summary>
    <div class="tw"><table>
      <thead><tr><th data-nosort></th><th>Name</th><th>School</th><th class="n">Citations</th><th class="n">h-index</th></tr></thead>
      <tbody>{prow}</tbody>
    </table></div>
    <p class="sub" style="margin-top:10px"><a href="{base}rankings.html">{t("home", "top_faculty_link")}</a></p></details>
  <details class="fold"><summary>{t("home", "top_departments_heading")}</summary>
    <div class="tw"><table>
      <thead><tr><th data-nosort></th><th>School</th><th class="n">Faculty</th><th class="n">Median</th><th class="n">Mean</th></tr></thead>
      <tbody>{drow}</tbody>
    </table></div>
    <p class="sub" style="margin-top:10px">{t("home", "top_departments_note", departments_href=f"{base}departments.html")}</p></details>

  <h2>{t("home", "caveats_heading")}</h2>
  <p>{t("home", "caveats_body", methods_href=f"{base}methods.html")}</p>
</main>"""
    return page(t("site", "title_suffix"), body, base, active="index.html",
                description=t("home", "meta_description", people_count=f"{len(people):,}",
                              dept_count=len(depts)))


def rankings_page(data, slugs, base) -> str:
    people = sorted(data["people"], key=lambda p: -(p["total_citations"] or 0))
    rows = []
    for i, p in enumerate(people, 1):
        pill = f' <span class="pill pill-oa">{PILL_SHORT.get(p["source"], e(p["source"]))}</span>' if p["is_fallback"] else ""
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
  <p class="eyebrow">{t("rankings", "eyebrow")}</p>
  <h1>{t("rankings", "heading")}</h1>
  <p class="lede">{t("rankings", "lede")}</p>
  <div class="controls">
    <label>{t("rankings", "filter_rank")}
      <select data-filter-for="faculty" data-filter-key="rank">
        <option value="">All</option><option value="full">Professor</option>
        <option value="associate">Associate</option><option value="assistant">Assistant</option>
      </select>
    </label>
    <label>{t("rankings", "filter_country")}
      <select data-filter-for="faculty" data-filter-key="country">
        <option value="">All</option><option value="US">United States</option><option value="CA">Canada</option>
      </select>
    </label>
    <span class="count"><span id="faculty-count">{len(people):,}</span> {t("rankings", "shown")}</span>
  </div>
  <div class="tw"><table id="faculty" data-sortable>
    <thead><tr><th data-nosort></th><th data-text>Name</th><th data-text>School</th><th data-text>Rank</th>
      <th class="n">Citations</th><th class="n">h-index</th><th class="n">Pct. in rank</th>
      <th class="n">Per year</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
</main>"""
    return page(f"{t('rankings', 'browser_title')} — {t('site', 'title_suffix')}", body, base,
                active="rankings.html", description=t("rankings", "meta_description"),
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
  <p class="eyebrow">{t("departments", "eyebrow")}</p>
  <h1>{t("departments", "heading")}</h1>
  <p class="lede">{t("departments", "lede", dept_count=len(depts))}</p>
  <div class="controls">
    <label>{t("departments", "filter_country")}
      <select data-filter-for="depts" data-filter-key="country">
        <option value="">All</option><option value="US">United States</option><option value="CA">Canada</option>
      </select>
    </label>
    <label>{t("departments", "filter_acsp")}
      <select data-filter-for="depts" data-filter-key="acsp">
        <option value="">All</option><option value="full">Full member</option>
        <option value="affiliate">Affiliate</option><option value="none">Non-member</option>
      </select>
    </label>
    <span class="count"><span id="depts-count">{len(depts)}</span> {t("departments", "shown")}</span>
  </div>
  <div class="tw"><table id="depts" data-sortable>
    <thead><tr><th data-nosort></th><th data-text>School</th><th class="n">Faculty</th>
      <th class="n">Median cites</th><th class="n">Mean cites</th><th class="n">Total cites</th>
      <th class="n">Median h</th><th class="n">Scholar profiles</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
</main>"""
    return page(f"{t('departments', 'heading')} — {t('site', 'title_suffix')}", body, base,
                active="departments.html",
                description=t("departments", "meta_description", dept_count=len(depts)),
                extra_js=("table.js",))


def methods_page(data, base, db_name) -> str:
    a = data["asof"]
    n_fb = sum(1 for p in data["people"] if p["is_fallback"])
    m = lambda key, **kw: t("methods", key, **kw)
    vals = dict(people_count=f"{len(data['people']):,}",
                scholar_count=f"{len(data['people']) - n_fb:,}",
                fallback_count=n_fb,
                scholar_date=e(a.get("google_scholar", "n/a")),
                openalex_date=e(a.get("openalex", "n/a")),
                scholar_help=SCHOLAR_HELP, repo=REPO, contact=CONTACT,
                db_href=f"{base}{db_name}", year=date.today().year)
    body = f"""<main class="narrow">
  <p class="eyebrow">{m("eyebrow")}</p>
  <h1>{m("heading")}</h1>
  <p class="lede">{m("lede")}</p>

  <h2>{m("sources_heading")}</h2>
  <p>{m("sources_scholar", **vals)}</p>
  <p>{m("sources_openalex", **vals)}</p>
  <p>{m("sources_invitation", **vals)}</p>

  <h2>{m("cadence_heading")}</h2>
  <p>{m("cadence_body")}</p>

  <h2>{m("inclusion_heading")}</h2>
  <p>{m("inclusion_body")}</p>

  <h2>{m("percentiles_heading")}</h2>
  <p>{m("percentiles_body")}</p>

  <h2>{m("limitations_heading")}</h2>
  <ul>
    <li>{m("limitation_lag")}</li>
    <li>{m("limitation_names")}</li>
    <li>{m("limitation_scholar")}</li>
    <li>{m("limitation_missing")}</li>
  </ul>
  <p>{m("corrections_body", **vals)}</p>

  <h2>{m("data_heading")}</h2>
  <p>{m("data_body", **vals)}</p>
  <p>{m("data_links", **vals)}</p>
  <p>{m("citation_body", **vals)}</p>
</main>"""
    return page(f"{m('eyebrow')} — {t('site', 'title_suffix')}", body, base,
                active="methods.html", description=m("meta_description"))


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
