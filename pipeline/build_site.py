"""Generate the static site from the built database.

    python -m pipeline.build_db && python -m pipeline.build_site
    python -m pipeline.build_site --out build/site

Writes one page per current faculty member and per department, plus rankings,
methods, a search index, a sitemap, a not-found page, and the database for
download. Plain Python and SQLite, no Node toolchain: the site is ~1,200
read-only pages whose one chart is a column per year, drawn here as SVG, so a
framework would add a dependency without adding anything a reader would notice.
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
from .health_check import SITE as SITE_URL, repo_snapshot_dates
from .site_assets import NOT_FOUND_JS, SEARCH_JS, STYLE, TABLE_JS

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


def ranks(rank: str, lower: bool = False) -> str:
    """A rank as a group of people ("Full professors"), where RANK_LABEL is one
    person's title ("Professor"). Bare codes used to be pluralised in place,
    which gave "Citations, fulls"."""
    label = t("site", f"rank_{rank if rank in ('assistant', 'associate', 'full') else 'other'}")
    return label.lower() if lower else label


SOURCE_LABEL = {"google_scholar": "Google Scholar", "openalex": "OpenAlex",
                "pop": "Publish or Perish", "manual": "entered by hand"}
PILL_LABEL = {"openalex": "OpenAlex", "pop": "PoP", "manual": "manual"}     # on a fallback row
PILL_SHORT = {"openalex": "OA", "pop": "PoP", "manual": "manual"}           # in the dense rankings table
OTHERS_PAGE = "elsewhere.html"   # planning PhDs whose appointment is outside a planning program


def fallback_note(source: str, **values) -> str:
    """The wording under a non-Scholar figure, by source. A Publish or Perish
    page used to describe OpenAlex, which it had nothing to do with."""
    key = "fallback_note_pop" if source == "pop" else "fallback_note_openalex"
    values.setdefault("contact", CONTACT)
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
    if x is None:
        return "&mdash;"
    n = round(x * 100)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


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


YEARS_SHOWN = 20     # a profile can run to forty years; the chart shows the most recent twenty


def yearly_counts(source: str, raw_json: str | None) -> list[tuple[int, int]]:
    """Citations received in each calendar year, oldest first, from the reading
    behind a headline figure: the profile's own graph for Scholar, and
    counts_by_year for a single OpenAlex record.

    Empty when the source has no such series (Publish or Perish), when fewer
    than two years have any citations, or when several OpenAlex records were
    summed: the stored reading keeps only the first record's years, and a
    chart of part of someone's record would understate them."""
    try:
        raw = json.loads(raw_json or "{}")
    except ValueError:
        return []
    if source == "google_scholar":
        pairs = list((raw.get("cites_per_year") or {}).items())
    elif source == "openalex" and not raw.get("records"):
        pairs = [(y.get("year"), y.get("cited_by_count")) for y in raw.get("counts_by_year") or []]
    else:
        return []
    try:
        by = {int(y): int(n or 0) for y, n in pairs if y is not None}
    except (TypeError, ValueError):
        return []
    cited = [y for y, n in by.items() if n]
    if len(cited) < 2:
        return []
    return [(y, by.get(y, 0)) for y in range(min(cited), max(by) + 1)]


def axis_top(v: int) -> int:
    """The smallest of 1, 2 or 5 times a power of ten that is at least v: where the value axis ends."""
    mag = 10 ** (len(str(max(v, 1))) - 1)
    return next(m * mag for m in (1, 2, 5, 10) if m * mag >= v)


def year_chart(points: list[tuple[int, int]], read_year: int | None = None,
               width: int = 380, height: int = 170) -> str:
    """Columns of citations received per year, as inline SVG.

    Replaces a two-point sparkline of this project's own readings, whose first
    point was the March 2026 workbook figure drawn at a date it was not read
    on. The profile's own per-year counts are the same measure for everyone
    and are what a visitor sees on the profile itself.

    One series, so no legend: the caption says what is plotted. Columns are
    capped at 24 units wide with a rounded top and a square foot; the reading
    year, which is not over, is drawn paler. Each column shows its value on
    hover (styles under .years in site_assets), and the page carries the same
    figures as a table, so nothing depends on the hover."""
    pts = points[-YEARS_SHOWN:]
    if len(pts) < 2:
        return ""
    top = axis_top(max(n for _, n in pts))
    x0, x1, y0, y1 = 38, width - 4, 20, height - 22
    band = (x1 - x0) / len(pts)
    col = min(24.0, band - 2)
    out = [f'<line class="grid" x1="{x0}" x2="{x1}" y1="{y0}" y2="{y0}"/>',
           f'<text x="{x0 - 6}" y="{y0 + 4}" text-anchor="end">{top:,}</text>']
    if top % 2 == 0:
        mid = (y0 + y1) / 2
        out += [f'<line class="grid" x1="{x0}" x2="{x1}" y1="{mid:.1f}" y2="{mid:.1f}"/>',
                f'<text x="{x0 - 6}" y="{mid + 4:.1f}" text-anchor="end">{top // 2:,}</text>']
    first, last = pts[0][0], pts[-1][0]
    for i, (year, n) in enumerate(pts):
        x = x0 + i * band + (band - col) / 2
        h = (y1 - y0) * n / top
        y = y1 - h
        r = min(4.0, col / 2, h)
        cx = x + col / 2
        g = [f'<g class="yr"><rect x="{x0 + i * band:.1f}" y="{y0 - 14}" width="{band:.1f}" '
             f'height="{y1 - y0 + 14}" fill="transparent"/>']
        if n:
            g.append(f'<path class="col{" part" if year == read_year else ""}" d="M{x:.1f},{y1} V{y + r:.1f} '
                     f'Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} H{x + col - r:.1f} '
                     f'Q{x + col:.1f},{y:.1f} {x + col:.1f},{y + r:.1f} V{y1} Z"/>')
        # The value, shown on hover: above the plot, where no later column can be drawn over it,
        # and kept inside the drawing at either edge.
        anchor, tx = ("start", x0) if cx < x0 + 44 else ("end", x1) if cx > x1 - 44 else ("middle", cx)
        g.append(f'<text class="tip" x="{tx:.1f}" y="{y0 - 7}" text-anchor="{anchor}">{year}: {n:,}</text></g>')
        out.append("".join(g))
        if year in (first, last) or (year % 5 == 0 and min(year - first, last - year) > 2):
            out.append(f'<text x="{cx:.1f}" y="{height - 6}" text-anchor="middle">{year}</text>')
    out.append(f'<line class="base" x1="{x0}" x2="{x1}" y1="{y1}" y2="{y1}"/>')
    peak_year, peak = max(pts, key=lambda t: t[1])
    return (f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Citations received each year, '
            f'{first} to {last}. The most in one year was {peak:,}, in {peak_year}.">{"".join(out)}</svg>')


def pct_row(label, value) -> str:
    if value is None:
        return ""
    w = max(1.5, round(value * 100, 1))
    hi = " hi" if value >= 0.75 else ""
    return (f'<div class="pct-row"><span class="lab">{label}</span>'
            f'<span class="pct-track"><span class="pct-fill{hi}" style="width:{w}%"></span></span>'
            f'<span class="v">{pct(value)}</span></div>')


# ----------------------------------------------------------------- template

def reading_is_theirs(source: str, raw_json: str | None, held_scholar_id: str | None) -> bool:
    """Should a stored reading appear in the person's series on the site?

    Snapshots are never deleted, so a Scholar reading taken from a profile we
    later found to be a namesake stays in the data with the person's id on it.
    Yan Song's series (2026-10-12) ran 18,662 -> 23,933 -> 13,175: the first two
    were another Yan Song at UNC, inherited from the workbook. A Scholar reading
    is shown only if the profile it came from is the one the roster holds now;
    the workbook rows name theirs in the GoogleScholar URL. OpenAlex readings
    are sums over accepted records and are not tied to one id, so they stay.
    """
    if source != "google_scholar":
        return True
    try:
        raw = json.loads(raw_json or "{}")
    except ValueError:
        return True
    sid = raw.get("scholar_id")
    if not sid:
        m = re.search(r"user=([\w-]+)", ((raw.get("workbook_row") or {}).get("GoogleScholar") or ""))
        sid = m.group(1) if m else None
    if not sid or not held_scholar_id:
        return True                     # nothing to compare; show it
    return sid == held_scholar_id


def donate_block() -> str:
    """The footer's donate line, or nothing at all while no destination is set."""
    url = (COPY["site"].get("donate_url") or "").strip()
    if not url:
        return ""
    return (f'    <p class="donate">{t("site", "donate_text")} '
            f'<a class="btn" href="{e(url)}" rel="noopener">{t("site", "donate_button")}</a></p>\n')


def page(title, body, base, active="", description="", extra_js=(), path="", body_class="",
         noindex=False) -> str:
    """One page of the site. `path` is the page's own address under the site
    root ("rankings.html", "person/susan-handy.html"); it becomes the canonical
    link and the address a shared link previews, so every page has one."""
    url = f"{SITE_URL}/{'' if path == 'index.html' else path}"
    if noindex:
        meta = '<meta name="robots" content="noindex">'
    else:
        meta = (f'<link rel="canonical" href="{e(url)}">\n'
                f'<meta property="og:type" content="website">\n'
                f'<meta property="og:site_name" content="{e(t("site", "title_suffix"))}">\n'
                f'<meta property="og:title" content="{e(title)}">\n'
                f'<meta property="og:description" content="{e(description)}">\n'
                f'<meta property="og:url" content="{e(url)}">\n'
                f'<meta name="twitter:card" content="summary">')
    nav = [(t("site", "nav_rankings"), "rankings.html"),
           (t("site", "nav_departments"), "departments.html"),
           (t("site", "nav_others"), OTHERS_PAGE),
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
{meta}
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 16 16%22><text y=%2213%22 font-size=%2213%22>&#128218;</text></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,500;8..60,600&family=Source+Sans+3:wght@400;600&family=IBM+Plex+Mono:wght@400&display=swap">
<link rel="stylesheet" href="{base}style.css">
</head>
<body{f' class="{body_class}"' if body_class else ""}>
<header class="bar">
  <div class="bar-in">
    <a class="wordmark" href="{base}index.html">{t("site", "wordmark_lead")} <span>{t("site", "wordmark_rest")}</span><span class="tag">{t("site", "beta_tag")}</span></a>
    <div class="search" data-base="{base}" data-none="{t("site", "search_none")}">
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

ROLLING_SOURCES = ("google_scholar",)   # collected a few dozen profiles at a time, so the newest reading is the date


def as_of_dates(counts: list[dict]) -> dict:
    """source -> the date the site quotes for that source.

    For a source collected in full runs (OpenAlex monthly), the newest run that
    covered most people: a targeted fetch of a handful must not make the site
    claim every figure is as recent as that day. Google Scholar has been
    collected on a rolling schedule since 2026-10-11 (about fifty profiles a
    week), so no run covers most people; its date is simply the newest reading,
    and the homepage labels it "last read" and shows the oldest reading beside it.
    The old rule froze the Scholar date at 15 September after the first weekly
    run (2026-10-12)."""
    by_src: dict[str, list] = defaultdict(list)
    for r in counts:
        by_src[r["source"]].append((r["d"], r["n"]))
    out = {}
    for src, rows in by_src.items():
        if src in ROLLING_SOURCES:
            out[src] = max(d for d, _ in rows)
            continue
        biggest = max(n for _, n in rows)
        out[src] = max(d for d, n in rows if n >= biggest / 2)
    return out


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
    # Citations per year behind each headline figure, for the chart on a person's page.
    yearly = {}
    for r in q("""SELECT h.person_id, h.source, m.raw_json, p.google_scholar_id
                  FROM v_headline_metrics h
                  JOIN metric_snapshot m ON m.snapshot_id = h.snapshot_id
                  JOIN person p ON p.person_id = h.person_id"""):
        if reading_is_theirs(r["source"], r["raw_json"], r["google_scholar_id"]):
            yearly[r["person_id"]] = yearly_counts(r["source"], r["raw_json"])
    # Current people with no figure from any source. They cannot be ranked, but they
    # are on the roster, and until 2026-11 nothing on the site said so: nine people
    # could not be found by a search for their own name.
    no_figure = q("""
      SELECT p.person_id, p.display_name, ca.department_id, ca.rank, ca.kind,
             d.short_name AS department, d.name AS unit, d.university
      FROM person p
      JOIN v_current_appointment ca ON ca.person_id = p.person_id
      JOIN department d             ON d.department_id = ca.department_id
      LEFT JOIN v_headline_metrics h ON h.person_id = p.person_id
      WHERE h.person_id IS NULL
      ORDER BY p.display_name""")
    asof = as_of_dates(q("SELECT source, collected_at AS d, COUNT(*) AS n FROM metric_snapshot "
                         "GROUP BY source, collected_at"))
    bench = {r["rank"]: r for r in q("SELECT * FROM v_rank_benchmarks")}
    affs = q("SELECT a.*, d.short_name AS dept FROM affiliation a JOIN department d USING(department_id)")
    gs_oldest = q("SELECT MIN(collected_at) AS d FROM v_person_percentiles WHERE source='google_scholar'")[0]["d"]
    mix = {r["source"]: r["n"] for r in q("SELECT h.source, COUNT(*) AS n FROM v_headline_metrics h "
                                         "JOIN v_current_affiliation ca ON ca.person_id = h.person_id GROUP BY 1")}
    shares = sorted(r["k"] for r in q("""
      SELECT o.total_citations * 1.0 / s.total_citations AS k
      FROM v_scholar_metrics s JOIN v_openalex_metrics o USING (person_id)
      JOIN v_current_appointment ca USING (person_id)
      WHERE s.total_citations >= 100 AND o.total_citations IS NOT NULL"""))
    oa_share_typical = {"n": len(shares), "typical": shares[len(shares) // 2]} if shares else {"n": 0, "typical": None}
    rising_rows = rising_from_readings(q("""
      SELECT p.person_id, p.display_name, ca.rank, d.short_name AS department,
             m.total_citations, m.collected_at, m.raw_json
      FROM v_scholar_metrics m
      JOIN person p ON p.person_id = m.person_id
      JOIN v_current_affiliation ca ON ca.person_id = p.person_id
      JOIN department d ON d.department_id = ca.department_id"""))
    others = q("""
      SELECT o.*, p.phd_year, p.phd_institution, p.interests, p.google_scholar_id,
             p.openalex_author_id, p.orcid, p.researchgate_url, p.linkedin_url, p.personal_url,
             d.country
      FROM v_other_units o
      JOIN person p     ON p.person_id = o.person_id
      JOIN department d ON d.department_id = o.department_id
      ORDER BY o.display_name
    """)
    return {"people": people, "depts": depts, "oa": oa, "yearly": yearly, "no_figure": no_figure, "asof": asof,
            "bench": bench, "affs": affs, "gs_oldest": gs_oldest, "mix": mix, "rising": rising_rows, "oa_share": oa_share_typical,
            "others": others}


# -------------------------------------------------------------------- pages

def oa_share(oa_total, gs_total) -> float | None:
    """OpenAlex citations as a share of the person's Scholar total, or None when
    there is nothing to compare. Shown beside the site-wide typical share so a
    low OpenAlex figure reads as the norm it is, not as an error (a 2026-10
    request to remove the card from a page whose share was a fifth)."""
    if not oa_total or not gs_total:
        return None
    return oa_total / gs_total


def person_page(p, slugs, dslug, oa, yearly, base, share=None) -> str:
    # Someone in v_other_units: a planning PhD outside a planning program. Same
    # page, but no department page to link to and no percentiles to show.
    other = "unit" in p
    fallback = bool(p["is_fallback"])
    src = SOURCE_LABEL.get(p["source"], p["source"])
    pill = (f'<span class="pill pill-oa">{e(src)}</span>' if fallback
            else '<span class="pill pill-gs">Google Scholar</span>')
    note = ""
    if fallback:
        note = ('<div class="note"><p>'
                + fallback_note(p["source"], scholar_help=SCHOLAR_HELP, collected_at=e(p["collected_at"] or ""))
                + '</p></div>')

    points = yearly.get(p["person_id"]) or []
    read_year = int(p["collected_at"][:4]) if p["collected_at"] else None
    chart = year_chart(points, read_year)
    spark_block = ""
    if chart:
        shown = points[-YEARS_SHOWN:]
        notes = []
        if shown[-1][0] == read_year and shown[-1][1]:      # only when that column is actually drawn
            notes.append(t("person", "years_partial", year=read_year))
        if len(points) > len(shown):
            notes.append(t("person", "years_earlier", first_year=shown[0][0]))
        if p["source"] == "openalex":
            # OpenAlex gives no yearly counts before 2012, so for anyone cited earlier the
            # columns add up to less than the figure above, and the page has to say so.
            notes.append(t("person", "years_openalex"))
        table = "".join(f'<tr><td>{y}</td><td class="n">{n:,}</td></tr>' for y, n in reversed(shown))
        spark_block = (f'<figure class="years">{chart}<figcaption>'
                       + t("person", "years_caption", source=e(src), collected_at=e(p["collected_at"] or ""),
                           notes=" ".join(notes))
                       + f'</figcaption><details><summary>{t("person", "years_table")}</summary>'
                       f'<table><thead><tr><th>{t("person", "years_th_year")}</th>'
                       f'<th class="n">{t("person", "years_th_citations")}</th></tr></thead>'
                       f'<tbody>{table}</tbody></table></details></figure>')

    o = oa.get(p["person_id"])
    compare = ""
    if o and not fallback and o["total_citations"] is not None:
        own = oa_share(o["total_citations"], p["total_citations"])
        context = (" " + t("person", "compare_context", own=f"{own:.0%}", typical=f"{share['typical']:.0%}", n=num(share["n"]))
                   if own is not None and share and share.get("n") else "")
        compare = f"""<div class="card" style="margin-top:18px">
  <h3>{t("person", "compare_heading")}</h3>
  <dl class="facts">
    <dt>Citations</dt><dd class="nums">{num(o['total_citations'])}</dd>
    <dt>h-index</dt><dd class="nums">{num(o['h_index'])}</dd>
    <dt>Works</dt><dd class="nums">{num(o['works_count'])}</dd>
  </dl>
  <p class="sub" style="margin:10px 0 0;font-size:13px">{t("person", "compare_note")}{context}</p>
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

    if other:
        where = f"{e(p['unit'])}, {e(p['university'])}"
        standing = (f'<div class="note"><p>'
                    + t("person", "other_note", name=e(p["display_name"]), others_href=f"{base}{OTHERS_PAGE}")
                    + '</p></div>')
    else:
        where = f'<a href="{base}department/{dslug}.html">{e(p["department"])}</a>'
        standing = f"""<div class="pct">
        {pct_row(t("person", "pct_citations_all"), p["pct_citations_all"])}
        {pct_row(t("person", "pct_citations_rank", ranks=ranks(p['rank'], lower=True)), p["pct_citations_rank"])}
        {pct_row(t("person", "pct_h_all"), p["pct_h_all"])}
        {pct_row(t("person", "pct_h_rank", ranks=ranks(p['rank'], lower=True)), p["pct_h_rank"])}
      </div>
      <p class="sub" style="font-size:13px;margin-top:10px">{t("person", "percentile_note", ranks=ranks(p['rank'], lower=True))}</p>"""

    body = f"""<main>
  <p class="eyebrow">{t("person", "eyebrow")}</p>
  <h1>{e(p['display_name'])}</h1>
  <p class="sub">{RANK_LABEL.get(p['rank'], p['rank'])},
     {where}</p>
  <div class="sheet">
    <div>
      <div class="metric">
        <span class="big nums">{num(p['total_citations'])}</span>
        <span class="side">{t("person", "citations_label")} {pill}</span>
        <span class="side">{t("person", "h_index_label")} <b class="nums">{num(p['h_index'])}</b></span>
      </div>
      <p class="sub" style="font-size:13.5px">{t("person", "as_of", collected_at=e(p['collected_at']))}</p>
      {note}
      {standing}
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
                              department=(f"{p['unit']}, {p['university']}" if other else p["department"])),
                path=f"person/{slugs[id(p)]}.html")


def no_figure_note(section: str, people: list[dict], **values) -> str:
    """The sentence naming current people who have no figure from any source,
    or nothing when there are none. They are on the roster and cannot be
    ranked; saying so is what lets a search for their name find something."""
    if not people:
        return ""
    names = ", ".join(f"{e(p['display_name'])} ({RANK_LABEL.get(p['rank'], p['rank'])})" for p in people)
    key = "no_figure_one" if len(people) == 1 else "no_figure_many"
    return f'<p class="sub" id="no-figure" style="margin-top:14px;font-size:13.5px">{t(section, key, n=len(people), names=names, **values)}</p>'


def department_page(d, roster, slugs, base, dslug="", unlisted=()) -> str:
    rows = []
    for p in roster:
        pill = f' <span class="pill pill-oa">{PILL_LABEL.get(p["source"], e(p["source"]))}</span>' if p["is_fallback"] else ""
        rank = RANK_LABEL.get(p['rank'], p['rank'])
        rows.append(f"""<tr data-rank="{e(p['rank'])}">
  <td class="name" data-v="{e(p['display_name'])}"><a href="{base}person/{slugs[id(p)]}.html">{e(p['display_name'])}</a>{pill}<span class="under">{rank}</span></td>
  <td class="wide">{rank}</td>
  <td class="n">{num(p['total_citations'])}</td>
  <td class="n">{num(p['h_index'])}</td>
  <td class="n wide">{pct(p['pct_citations_rank'])}</td>
  <td class="n wide">{p['phd_year'] or '&mdash;'}</td>
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
        <option value="">All</option><option value="full">{ranks("full")}</option>
        <option value="associate">{ranks("associate")}</option><option value="assistant">{ranks("assistant")}</option>
      </select>
    </label>
    <span class="count"><span id="roster-count">{d['n_faculty']}</span> {t("department", "shown")}</span>
  </div>
  <div class="tw"><table id="roster" data-sortable>
    <thead><tr><th data-text>Name</th><th data-text class="wide">Rank</th><th class="n">Citations</th>
      <th class="n nb">h-index</th><th class="n wide">Pct. in rank</th><th class="n wide">PhD</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
  <p class="sub" style="margin-top:14px;font-size:13.5px">{t("department", "fallback_footnote", methods_href=f"{base}methods.html")}</p>
  {no_figure_note("department", list(unlisted), methods_href=f"{base}methods.html", scholar_help=SCHOLAR_HELP)}
</main>"""
    return page(f"{d['short_name']} — {t('site', 'title_suffix')}", body, base,
                description=t("department", "meta_description", n_faculty=d["n_faculty"],
                              short_name=d["short_name"]),
                extra_js=("table.js",), path=f"department/{dslug}.html")


# A median of one or two people is not a program's standing. The homepage list
# always left such programs out; the departments page ranked them all, so a
# program with two faculty tracked sat second of 121.
MIN_FACULTY_TO_RANK = 3
RECENT_DAYS = 90     # the homepage narrates roster changes from this many days back
# The "Promotions recorded" fold is hidden (Alex, 2026-10-12): 136 entries, nearly all
# our record catching up with ranks Tom's sheet had missed, not promotions this term.
# Flip to True once the record has settled and the entries are read in near real time.
SHOW_PROMOTIONS = False
RANK_ORDER = {"assistant": 0, "associate": 1, "full": 2}


def longdate(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B %Y')}"


RISING_PER_RANK = 5
RISING_MIN_TOTAL = 100   # a profile this small is noise, whichever way it moves


def rising_from_readings(rows: list[dict]) -> list[dict]:
    """Citations each person received in the twelve months before their profile
    was last read, estimated from the per-year counts on the profile.

    Scholar gives whole calendar years, so the estimate is this year's count so
    far plus the share of last year's that falls inside the window: a profile
    read on 15 September takes all of 2026 to date and the last 29% of 2025.
    The first version (2026-10-09) measured gains against Tom's March 2026
    figures, which were not all read in March (Yan Song's was two years old);
    the second (2026-10-12) summed last year and this, a window of up to two
    years that Alex found too loose. A profile's own graph is the same measure
    for everyone and matches what a visitor sees when they click through.
    """
    out = []
    for r in rows:
        try:
            cpy = {int(k): int(v or 0) for k, v in (json.loads(r.get("raw_json") or "{}").get("cites_per_year") or {}).items()}
        except (ValueError, TypeError):
            cpy = {}
        if not cpy or not r.get("collected_at"):
            continue
        read = date.fromisoformat(r["collected_at"][:10])
        elapsed = (read.timetuple().tm_yday - 1) / 365          # share of the reading year already gone
        gain = cpy.get(read.year, 0) + round(cpy.get(read.year - 1, 0) * (1 - elapsed))
        out.append({**r, "gain": gain, "window_end": r["collected_at"][:10]})
    return out


def rising(rows: list[dict], per_rank=RISING_PER_RANK, min_total=RISING_MIN_TOTAL) -> dict[str, list[dict]]:
    """The most-cited-recently people within each rank: top `per_rank` by
    citations received in the past twelve months (see rising_from_readings).
    Compares people with their own rank, so it was held back (2026-09-19)
    until the ranks had been reviewed; shipped 2026-10-09."""
    out = {}
    for rk in ("assistant", "associate", "full"):
        ok = [r for r in rows if r["rank"] == rk and (r.get("total_citations") or 0) >= min_total and r.get("gain", 0) > 0]
        out[rk] = sorted(ok, key=lambda r: (-r["gain"], r["display_name"]))[:per_rank]
    return out


def reversal(old: dict, new: dict, closed_on: dict) -> bool:
    """A closed row whose replacement returns the person to the department they
    were in just before it is a correction being undone, not a move: the stint
    in `old` was read from a stale profile and the roster has been put back.
    Narrating it as a move would show the mistake a second time (Rachel Weber,
    2026-10). The row `old` replaced is the one closed on the day `old` opened."""
    before = closed_on.get((old["person_id"], old["start_date"]))
    return before is not None and before["department_id"] == new["department_id"]


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
    primary = [a for a in affs if str(a["is_primary"]) == "1"]
    open_by = {a["person_id"]: a for a in primary if not a["end_date"]}
    closed_on = {(a["person_id"], a["end_date"]): a for a in primary if a["end_date"]}
    for old in primary:
        if not old["end_date"] or old["end_date"] < since:
            continue
        new = open_by.get(old["person_id"])
        if new is None:
            out["departed"] += 1
        elif new["start_date"] != old["end_date"]:
            continue                       # closed for some other reason; not a change we narrate
        elif new["department_id"] != old["department_id"]:
            if reversal(old, new, closed_on):
                continue
            out["moves"].append((old["person_id"], old["dept"], new["dept"]))
        elif RANK_ORDER.get(new["rank"], -1) > RANK_ORDER.get(old["rank"], -1):
            # where the new rank was read: a program website during a roster
            # review (mostly records catching up) or a change on a Scholar profile
            origin = "website" if "department page" in (new.get("source") or "") else "profile"
            out["promos"].append((old["person_id"], new["dept"], old["rank"], new["rank"], origin))
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
    top_d = sorted([d for d in depts if d["n_faculty"] >= MIN_FACULTY_TO_RANK],
                   key=lambda d: -(d["median_citations"] or 0))[:10]
    prow = "".join(f'<tr><td class="rank">{i}</td><td class="name">'
                   f'<a href="{base}person/{slugs[id(p)]}.html">{e(p["display_name"])}</a>'
                   f'<span class="under">{e(p["department"])}</span></td>'
                   f'<td class="school wide">{e(p["department"])}</td><td class="n">{num(p["total_citations"])}</td>'
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
    rank_label = {rk: ranks(rk) for rk in ("assistant", "associate", "full")}

    brow = "".join(f'<tr><td class="name">{rank_label[rk]}</td><td class="n">{b["n"]}</td>'
                   f'<td class="n">{num(b["median_citations"])}</td><td class="n">{num(b["p75_citations"])}</td>'
                   f'<td class="n">{b["median_h_index"]}</td></tr>'
                   for rk in ("assistant", "associate", "full") if (b := data["bench"].get(rk)))
    a_med = data["bench"].get("assistant", {}).get("median_citations") or 0

    rise = rising([r for r in data.get("rising", []) if str(r["person_id"]) in by_pid])
    rising_block = ""
    if any(rise.values()):
        cols = "".join(
            f'<div class="col"><h3>{rank_label[rk]}</h3><ul class="plain">'
            + "".join(f'<li>{plink(str(r["person_id"]))}<span class="gain">+{num(r["gain"])}'
                      f'<small>{r["gain"] / r["total_citations"]:.0%}</small></span>'
                      f'<span class="d">{e(r["department"])}</span></li>' for r in rise[rk])
            + "</ul></div>"
            for rk in ("assistant", "associate", "full") if rise[rk])
        rising_block = f"""
  <h2>{t("home", "rising_heading")}</h2>
  <p class="sub" style="margin:-6px 0 14px">{t("home", "rising_lede", min_total=num(RISING_MIN_TOTAL))}</p>
  <div class="cols3">{cols}</div>
  <p class="sub" style="margin-top:12px">{t("home", "rising_note", min_total=num(RISING_MIN_TOTAL))}</p>"""

    today = date.fromisoformat(data["today"]) if data.get("today") else date.today()
    since = (today - timedelta(days=RECENT_DAYS)).isoformat()
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
                   for pid, d, o, n, _ in ch["promos"] if str(pid) in by_pid]
    n_web = sum(1 for pid, *_, origin in ch["promos"] if str(pid) in by_pid and origin == "website")
    n_prof = len(promo_items) - n_web
    promo_note = (f'<p class="sub" style="margin:8px 0 6px">{t("home", "new_promos_note", n_pages=n_web, n_profiles=n_prof)}</p>'
                  if promo_items else "")
    found_li, moves_li, promos_li = "".join(found_items), "".join(move_items), "".join(promo_items)
    promo_fold = (f"""
  <details class="fold" style="margin-top:14px"><summary>{t("home", "new_promos_heading")} <span class="count">{len(promo_items)}</span></summary>
    {promo_note}<ul class="plain two">{promos_li}</ul></details>""" if SHOW_PROMOTIONS and promo_items else "")
    pop_month = date.fromisoformat(pop_date).strftime("%B") if pop_date else ""
    # one swatch per source, in the order the sentence names them
    mix_key = t("home", "mix_key", gs_n=mix.get("google_scholar", 0), oa_n=mix.get("openalex", 0),
                pop_n=mix.get("pop", 0), pop_month=pop_month, scholar_help=SCHOLAR_HELP)
    mix_key = ('<i class="k gs"></i>' + mix_key.replace(" &middot; ", ' &middot; <i class="k oa"></i>', 1))
    head, sep, tail = mix_key.partition(' &middot; <i class="k oa"></i>')
    tail = tail.replace(" &middot; ", ' &middot; <i class="k pop"></i>', 1)
    mix_key = head + sep + tail
    if found_li or moves_li or promo_fold or ch["departed"]:
        new_block = f"""
  <h2>{t("home", "new_heading")}</h2>
  <p class="sub" style="margin:-6px 0 14px">{t("home", "new_lede")}</p>
  <div class="cols2">
    <div class="col"><h3>{t("home", "new_found_heading")} <span class="count">{len(found_items)}</span></h3>
      <p class="sub">{t("home", "new_found_note")}</p>
      <ul class="plain">{found_li}</ul></div>
    <div class="col"><h3>{t("home", "new_moves_heading")} <span class="count">{len(move_items)}</span></h3>
      <ul class="plain">{moves_li}</ul></div>
  </div>
{promo_fold}
  <p class="sub" style="margin-top:12px">{t("home", "new_departed", n=ch["departed"], repo=REPO)}</p>"""
    else:
        new_block = f"""
  <h2>{t("home", "new_heading")}</h2>
  <p class="sub">{t("home", "new_empty")}</p>"""

    body = f"""<main class="narrow home">
  <section class="beta" aria-labelledby="beta-h">
    <h2 id="beta-h">{t("home", "beta_title")}</h2>
    <p>{t("home", "beta_body_1")}</p>
    <p>{t("home", "beta_body_2", contact=CONTACT, repo=REPO)}</p>
  </section>
  <h1>{t("home", "heading")}</h1>
  <p class="lede">{t("home", "lede", people_count=f"{len(people):,}", dept_count=len(depts))}</p>
  <div class="search hero-search" data-base="{base}" data-none="{t("site", "search_none")}">
    <input type="search" placeholder="{t("home", "search_placeholder")}" aria-label="{t("site", "search_placeholder")}"
           autocomplete="off" spellcheck="false">
    <div class="results" hidden></div>
  </div>
  <p class="sub hint" style="font-size:13.5px">{t("home", "search_hint", scholar_oldest=e(longdate(data.get("gs_oldest") or gs)) if (data.get("gs_oldest") or gs) else "", scholar_date=e(longdate(gs)) if gs else "")}</p>

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
  <p class="sub" style="margin-top:10px">{t("home", "field_note", scholar_oldest=e(longdate(data.get("gs_oldest") or gs)) if (data.get("gs_oldest") or gs) else "", scholar_date=e(longdate(gs)) if gs else "", assistant_median=num(a_med))}</p>
{rising_block}
{new_block}

  <h2>{t("home", "boards_heading")}</h2>
  <p class="sub" style="margin:-6px 0 12px">{t("home", "boards_lede", rankings_href=f"{base}rankings.html", departments_href=f"{base}departments.html")}</p>
  <details class="fold"><summary>{t("home", "top_faculty_heading")}</summary>
    <div class="tw"><table>
      <thead><tr><th data-nosort></th><th>Name</th><th class="wide">School</th><th class="n">Citations</th><th class="n nb">h-index</th></tr></thead>
      <tbody>{prow}</tbody>
    </table></div>
    <p class="sub" style="margin-top:10px"><a href="{base}rankings.html">{t("home", "top_faculty_link")}</a></p></details>
  <details class="fold"><summary>{t("home", "top_departments_heading")}</summary>
    <div class="tw"><table>
      <thead><tr><th data-nosort></th><th>School</th><th class="n">Faculty</th><th class="n">Median</th><th class="n">Mean</th></tr></thead>
      <tbody>{drow}</tbody>
    </table></div>
    <p class="sub" style="margin-top:10px">{t("home", "top_departments_note", departments_href=f"{base}departments.html", min_faculty=MIN_FACULTY_TO_RANK)}</p></details>

  <h2>{t("home", "caveats_heading")}</h2>
  <p>{t("home", "caveats_body", methods_href=f"{base}methods.html")}</p>
</main>"""
    return page(t("site", "title_suffix"), body, base, active="index.html",
                description=t("home", "meta_description", people_count=f"{len(people):,}",
                              dept_count=len(depts)), path="index.html", body_class="home")


def rankings_page(data, slugs, base) -> str:
    people = sorted(data["people"], key=lambda p: -(p["total_citations"] or 0))
    rows = []
    for i, p in enumerate(people, 1):
        pill = f' <span class="pill pill-oa">{PILL_SHORT.get(p["source"], e(p["source"]))}</span>' if p["is_fallback"] else ""
        rank = RANK_LABEL.get(p['rank'], p['rank'])
        rows.append(f"""<tr data-rank="{e(p['rank'])}" data-country="{e(p['country'])}">
  <td class="rank">{i}</td>
  <td class="name" data-v="{e(p['display_name'])}"><a href="{base}person/{slugs[id(p)]}.html">{e(p['display_name'])}</a>{pill}<span class="under">{e(p['department'])} &middot; {rank}</span></td>
  <td class="school wide">{e(p['department'])}</td>
  <td class="wide">{rank}</td>
  <td class="n">{num(p['total_citations'])}</td>
  <td class="n">{num(p['h_index'])}</td>
  <td class="n wide" data-v="{p['pct_citations_rank'] or 0}">{pct(p['pct_citations_rank'])}</td>
  <td class="n wide" data-v="{p['citations_per_year'] or ''}">{f"{p['citations_per_year']:,.0f}" if p['citations_per_year'] else '&mdash;'}</td>
</tr>""")
    body = f"""<main>
  <p class="eyebrow">{t("rankings", "eyebrow")}</p>
  <h1>{t("rankings", "heading")}</h1>
  <p class="lede">{t("rankings", "lede")}</p>
  <div class="controls">
    <label>{t("rankings", "filter_rank")}
      <select data-filter-for="faculty" data-filter-key="rank">
        <option value="">All</option><option value="full">{ranks("full")}</option>
        <option value="associate">{ranks("associate")}</option><option value="assistant">{ranks("assistant")}</option>
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
    <thead><tr><th data-nosort></th><th data-text>Name</th><th data-text class="wide">School</th><th data-text class="wide">Rank</th>
      <th class="n">Citations</th><th class="n nb">h-index</th><th class="n wide">Pct. in rank</th>
      <th class="n wide">Per year</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
</main>"""
    return page(f"{t('rankings', 'browser_title')} — {t('site', 'title_suffix')}", body, base,
                active="rankings.html", description=t("rankings", "meta_description"),
                extra_js=("table.js",), path="rankings.html")


def departments_page(data, dslugs, base) -> str:
    # Programs too small to rank keep their figures but carry no rank and come last.
    depts = sorted(data["depts"], key=lambda d: (d["n_faculty"] < MIN_FACULTY_TO_RANK, -(d["median_citations"] or 0)))
    rows, i = [], 0
    for d in depts:
        small = d["n_faculty"] < MIN_FACULTY_TO_RANK
        i += not small
        rows.append(f"""<tr data-country="{e(d['country'])}" data-acsp="{e(d['acsp_member'])}"{' data-unranked' if small else ''}>
  <td class="rank">{'&mdash;' if small else i}</td>
  <td class="name" data-v="{e(d['short_name'])}"><a href="{base}department/{dslugs[id(d)]}.html">{e(d['short_name'])}</a></td>
  <td class="n">{d['n_faculty']}</td>
  <td class="n">{num(round(d['median_citations']))}</td>
  <td class="n wide">{num(round(d['mean_citations']))}</td>
  <td class="n wide">{num(d['total_citations'])}</td>
  <td class="n wide">{num(round(d['median_h_index']))}</td>
  <td class="n wide" data-v="{d['share_with_scholar_profile']}">{round(d['share_with_scholar_profile'] * 100)}%</td>
</tr>""")
    body = f"""<main>
  <p class="eyebrow">{t("departments", "eyebrow")}</p>
  <h1>{t("departments", "heading")}</h1>
  <p class="lede">{t("departments", "lede", dept_count=len(depts), min_faculty=MIN_FACULTY_TO_RANK)}</p>
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
      <th class="n">Median cites</th><th class="n wide">Mean cites</th><th class="n wide">Total cites</th>
      <th class="n wide">Median h</th><th class="n wide">Scholar profiles</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
</main>"""
    return page(f"{t('departments', 'heading')} — {t('site', 'title_suffix')}", body, base,
                active="departments.html",
                description=t("departments", "meta_description", dept_count=len(depts)),
                extra_js=("table.js",), path="departments.html")


def others_page(data, slugs, base) -> str:
    """Planning PhDs whose appointment is outside a planning program. Listed
    beside the rankings, never in them: no rank number, no percentile."""
    people = sorted(data["others"], key=lambda p: -(p["total_citations"] or 0))
    rows = []
    for p in people:
        pill = f' <span class="pill pill-oa">{PILL_SHORT.get(p["source"], e(p["source"]))}</span>' if p["is_fallback"] else ""
        phd = (f'{p["phd_institution"] or ""}{", " if p["phd_institution"] and p["phd_year"] else ""}'
               f'{p["phd_year"] or ""}') or "&mdash;"
        rows.append(f"""<tr data-rank="{e(p['rank'])}" data-country="{e(p['country'])}">
  <td class="name" data-v="{e(p['display_name'])}"><a href="{base}person/{slugs[id(p)]}.html">{e(p['display_name'])}</a>{pill}<span class="under">{e(p['unit'])}, {e(p['university'])}</span></td>
  <td class="school wide">{e(p['unit'])}</td>
  <td class="school wide">{e(p['university'])}</td>
  <td class="wide">{RANK_LABEL.get(p['rank'], p['rank'])}</td>
  <td class="n">{num(p['total_citations'])}</td>
  <td class="n">{num(p['h_index'])}</td>
  <td class="school wide">{e(phd) if phd != "&mdash;" else phd}</td>
</tr>""")
    body = f"""<main>
  <p class="eyebrow">{t("others", "eyebrow")}</p>
  <h1>{t("others", "heading")}</h1>
  <p class="lede">{t("others", "lede", n=len(people), methods_href=f"{base}methods.html")}</p>
  <div class="controls">
    <label>{t("others", "filter_rank")}
      <select data-filter-for="others" data-filter-key="rank">
        <option value="">All</option><option value="full">{ranks("full")}</option>
        <option value="associate">{ranks("associate")}</option><option value="assistant">{ranks("assistant")}</option>
      </select>
    </label>
    <label>{t("others", "filter_country")}
      <select data-filter-for="others" data-filter-key="country">
        <option value="">All</option><option value="US">United States</option><option value="CA">Canada</option>
      </select>
    </label>
    <span class="count"><span id="others-count">{len(people):,}</span> {t("others", "shown")}</span>
  </div>
  <div class="tw"><table id="others" data-sortable>
    <thead><tr><th data-text>Name</th><th data-text class="wide">Department or school</th><th data-text class="wide">University</th>
      <th data-text class="wide">Rank</th><th class="n">Citations</th><th class="n nb">h-index</th><th data-text class="wide">PhD</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
  <p class="sub" style="margin-top:14px;font-size:13.5px">{t("others", "footnote", methods_href=f"{base}methods.html")}</p>
  {no_figure_note("others", [p for p in data.get("no_figure", []) if p["kind"] == "other"], scholar_help=SCHOLAR_HELP)}

  <section id="criteria" style="margin-top:36px;max-width:720px">
    <h2>{t("others", "criteria_heading")}</h2>
    <p>{t("others", "criteria_intro")}</p>
    <ol>
      <li>{t("others", "criteria_1")}</li>
      <li>{t("others", "criteria_2")}</li>
      <li>{t("others", "criteria_3")}</li>
      <li>{t("others", "criteria_4")}</li>
    </ol>
    <p>{t("others", "criteria_request", contact=CONTACT, contact_name=CONTACT_NAME)}</p>
  </section>
</main>"""
    return page(f"{t('others', 'browser_title')} — {t('site', 'title_suffix')}", body, base,
                active=OTHERS_PAGE, description=t("others", "meta_description", n=len(people)),
                extra_js=("table.js",), path=OTHERS_PAGE)


def methods_page(data, base, db_name) -> str:
    a = data["asof"]
    n_fb = sum(1 for p in data["people"] if p["is_fallback"])
    n_pop = sum(1 for p in data["people"] if p["source"] == "pop")
    pop_date = a.get("pop", "")
    m = lambda key, **kw: t("methods", key, **kw)
    vals = dict(people_count=f"{len(data['people']):,}",
                scholar_count=f"{len(data['people']) - n_fb:,}",
                fallback_count=n_fb,
                openalex_count=n_fb - n_pop,
                pop_count=n_pop,
                pop_month=date.fromisoformat(pop_date).strftime("%B %Y") if pop_date else "early 2026",
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
  <p>{m("sources_pop", **vals)}</p>
  <p>{m("sources_invitation", **vals)}</p>

  <h2>{m("cadence_heading")}</h2>
  <p>{m("cadence_body")}</p>

  <h2>{m("inclusion_heading")}</h2>
  <p>{m("inclusion_body")}</p>
  <p>{m("inclusion_others", others_href=f"{base}{OTHERS_PAGE}")}</p>

  <h2>{m("percentiles_heading")}</h2>
  <p>{m("percentiles_body")}</p>

  <h2>{m("limitations_heading")}</h2>
  <ul>
    <li>{m("limitation_lag")}</li>
    <li>{m("limitation_names")}</li>
    <li>{m("limitation_scholar")}</li>
    <li>{m("limitation_fields")}</li>
    <li>{m("limitation_missing")}</li>
  </ul>
  <p>{m("corrections_body", **vals)}</p>

  <h2>{m("data_heading")}</h2>
  <p>{m("data_body", **vals)}</p>
  <p>{m("data_links", **vals)}</p>
  <p>{m("citation_body", **vals)}</p>
</main>"""
    return page(f"{m('eyebrow')} — {t('site', 'title_suffix')}", body, base,
                active="methods.html", description=m("meta_description"), path="methods.html")


def not_found_page() -> str:
    """What GitHub Pages serves for any address that is not a page. People who
    leave the tracked programs lose their page, and an address changes when a
    program is renamed; the link someone saved used to end at GitHub's own
    error page. This one is served at every depth, so its links start at the
    site root, and it searches for the words in the address it was reached by."""
    body = f"""<main class="narrow">
  <p class="eyebrow">{t("notfound", "eyebrow")}</p>
  <h1>{t("notfound", "heading")}</h1>
  <p class="lede">{t("notfound", "lede")}</p>
  <div class="search hero-search" data-base="/" data-none="{t("site", "search_none")}">
    <input type="search" placeholder="{t("home", "search_placeholder")}" aria-label="{t("site", "search_placeholder")}"
           autocomplete="off" spellcheck="false">
    <div class="results" hidden></div>
  </div>
  <p>{t("notfound", "links", home_href="/", rankings_href="/rankings.html", departments_href="/departments.html",
        others_href=f"/{OTHERS_PAGE}", contact=CONTACT)}</p>
</main>
<script>{NOT_FOUND_JS}</script>"""
    return page(f"{t('notfound', 'heading')} — {t('site', 'title_suffix')}", body, "/",
                description=t("notfound", "lede"), path="404.html", noindex=True)


def sitemap(paths: list[str]) -> str:
    urls = "".join(f"<url><loc>{e(SITE_URL)}/{e('' if p == 'index.html' else p)}</loc></url>\n" for p in paths)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n')


def publish_database(db: Path, dest: Path) -> None:
    """The database as published: every figure from every snapshot, and the
    source's full record (raw_json) for each person's newest reading from each
    source only.

    The built database keeps the full record on every row for audit. By
    2026-11 that was 27 MB of the file's 39, nearly all of it seventeen
    monthly copies of each person's OpenAlex record, and the file was four
    fifths of the whole site. Nothing is lost by leaving the older copies out
    of the download: they are in data/snapshots/ in the repository, row for row."""
    shutil.copy(db, dest)
    con = sqlite3.connect(dest)
    con.execute("UPDATE metric_snapshot SET raw_json = NULL "
                "WHERE snapshot_id NOT IN (SELECT snapshot_id FROM v_latest_metrics)")
    con.commit()
    con.execute("VACUUM")
    con.close()


# --------------------------------------------------------------------- main

def build(db: Path, out: Path, base: str, quiet: bool = False) -> None:
    log = (lambda *a: None) if quiet else print
    con = sqlite3.connect(db)
    data = load(con)
    con.close()
    people, depts, others = data["people"], data["depts"], data["others"]
    slugs = uniq_slugs(people + others, lambda p: p["display_name"])
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
    unlisted = defaultdict(list)          # current people with no figure, by planning program
    for p in data["no_figure"]:
        if p["kind"] != "other":
            unlisted[p["department_id"]].append(p)

    (out / "index.html").write_text(index_page(data, slugs, dslugs, ""))
    (out / "rankings.html").write_text(rankings_page(data, slugs, ""))
    (out / "departments.html").write_text(departments_page(data, dslugs, ""))
    (out / OTHERS_PAGE).write_text(others_page(data, slugs, ""))
    publish_database(db, out / "citations.sqlite")
    (out / "methods.html").write_text(methods_page(data, "", "citations.sqlite"))
    (out / "404.html").write_text(not_found_page())

    for p in people:
        (out / "person" / f"{slugs[id(p)]}.html").write_text(
            person_page(p, slugs, dslug_by_id[p["department_id"]], data["oa"], data["yearly"], "../", data.get("oa_share")))
    for p in others:
        (out / "person" / f"{slugs[id(p)]}.html").write_text(
            person_page(p, slugs, None, data["oa"], data["yearly"], "../", data.get("oa_share")))
    for d in depts:
        members = sorted(roster[d["department_id"]],
                         key=lambda x: -(x["total_citations"] or 0))
        (out / "department" / f"{dslugs[id(d)]}.html").write_text(
            department_page(d, members, slugs, "../", dslugs[id(d)], unlisted[d["department_id"]]))

    index = [{"t": p["display_name"], "n": norm_words(p["display_name"]),
              "w": norm_words(p["department"]), "d": p["department"],
              "u": f"person/{slugs[id(p)]}.html", "c": p["total_citations"] or 0} for p in people]
    index += [{"t": p["display_name"], "n": norm_words(p["display_name"]),
               "w": norm_words(f"{p['unit']} {p['university']}"), "d": f"{p['unit']}, {p['university']}",
               "u": f"person/{slugs[id(p)]}.html", "c": p["total_citations"] or 0} for p in others]
    index += [{"t": d["short_name"], "n": norm_words(d["short_name"]),
               "w": norm_words(f"{d['university']} planning program department"),
               "d": f"{d['n_faculty']} faculty", "u": f"department/{dslugs[id(d)]}.html",
               "c": d["total_citations"] or 0} for d in depts]
    # People with no figure have no page of their own; a search for them lands on the
    # note that names them, on their program's page or the other-units page.
    for p in data["no_figure"]:
        if p["kind"] == "other":
            where, target = f"{p['unit']}, {p['university']}", f"{OTHERS_PAGE}#no-figure"
        elif p["department_id"] in dslug_by_id:
            where, target = p["department"], f"department/{dslug_by_id[p['department_id']]}.html#no-figure"
        else:
            continue                      # a program with no one ranked has no page to land on
        index.append({"t": p["display_name"], "n": norm_words(p["display_name"]), "w": norm_words(where),
                      "d": f"{where} · {t('site', 'search_no_figure')}", "u": target, "c": 0})
    (out / "search.json").write_text(json.dumps(index, separators=(",", ":"), ensure_ascii=False))
    pages = (["index.html", "rankings.html", "departments.html", OTHERS_PAGE, "methods.html"]
             + sorted(f"department/{dslugs[id(d)]}.html" for d in depts)
             + sorted(f"person/{slugs[id(p)]}.html" for p in people + others))
    (out / "sitemap.xml").write_text(sitemap(pages))
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n")
    # Which snapshots this build was made from. pipeline/health_check.py compares
    # it with the repository to notice data that was committed and never deployed.
    (out / "status.json").write_text(json.dumps({"snapshots": repo_snapshot_dates()}) + "\n")

    n = sum(1 for _ in out.rglob("*") if _.is_file())
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6
    log(f"  {len(people):,} faculty pages, {len(depts)} department pages, "
        f"{len(others)} planning PhDs in other units")
    log(f"  search index {(out / 'search.json').stat().st_size / 1e3:.0f} KB")
    log(f"  built {out}  ({n} files, {size:.1f} MB)")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=BUILD_DIR / "citations.sqlite")
    ap.add_argument("--out", type=Path, default=BUILD_DIR / "site")
    args = ap.parse_args(argv)
    build(args.db, args.out, "")


if __name__ == "__main__":
    main()
