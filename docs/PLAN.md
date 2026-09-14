# Planning Citation Metrics: Automation Plan

Drafted 2026-09-13 after profiling `data/UPDATED_Cites2026.xlsm`, the workbook Tom Sanchez handed over.

## 1. What we are starting from

The workbook is a single **snapshot**, not a time series. Four sheets:

| Sheet | Rows | Role in the new system |
|---|---|---|
| `Cites` | 1,051 faculty | Becomes `person`, `affiliation`, and one `metric_snapshot` per person |
| `Current Schools` | 120 schools | Becomes `department` plus a derived summary view |
| `Top Faculty` | 1,051 | Derived view (sorted by citations, then by h-index) |
| `Sorting` | notes | Documents the seven VBA macros; replaced by SQL |

Coverage and quality facts that shape the design:

- **808 of 1,051 faculty (77%) have a Google Scholar profile URL.** All 808 parse to a valid 12-character Scholar user id, 806 distinct. The other 243 were looked up by hand in Publish or Perish.
- **Three ranks only**: Professor 406, Associate 364, Assistant 281. `RegFaculty` and `NonPhD` are empty in every row; Tom says they were meant to restrict the list to regular full-time tenure-track faculty and to flag faculty without PhDs, such as landscape architects. The schema keeps the first idea as `affiliation.appointment_type`. `Academia.edu`, `Twitter`, `Personal` are empty or nearly so.
- **120 schools across the US and Canada**, from 32 faculty (MIT) down to 1.
- **Metrics stored are total citations and h-index only.** The column is still named `Cites2017`, but it holds current totals. `CY2017` (citations per year since PhD) is `#REF!` in 805 rows.
- **Percentiles are computed by macros with hard-coded row ranges** (rows 2 to 1052 overall; rank boundaries at rows 282 and 647). The sheet has 1,051 data rows, so the ranges are already slightly off, and any roster change silently breaks them.
- **Identity problems the spreadsheet cannot express**: Guang Tian (Utah to New Orleans) and Lisa Berglund (Dalhousie to Morgan State, with a promotion) each appear twice because they moved and the old row was never removed. 15 rows have no `Years` value, mostly 2025 PhDs where years since PhD is zero.
- **Gender** is recorded (M/F/T). It is useful for aggregate analysis but should not be shown on individual public pages.

**No history survives in this file.** Tom updated in place each summer and winter, and the site he published only ever showed the current snapshot. We will not try to reconstruct the past. The time series starts with the workbook: Tom refreshed the Scholar numbers on 2026-03-01 and ran the Publish or Perish lookups between 2026-02-15 and 2026-02-20, so the first snapshot carries those dates. Every collection run after it adds a point. Within a year the site has a real trend to show; within three it has more history than the spreadsheet ever held.

## 2. Design principles

1. **Separate the person from the appointment from the measurement.** Faculty move and get promoted. A person has many affiliations over time and many metric snapshots over time. This is what the spreadsheet fundamentally cannot model and why duplicates crept in.
2. **Append, never overwrite.** Every collection run writes new snapshot rows. Nothing is edited in place, so any past ranking can be reproduced.
3. **Key on stable identifiers, disambiguate once.** Google Scholar id, OpenAlex author id, and ORCID are resolved one time per person with human review, then the pipeline runs unattended on ids.
4. **Curated data lives as plain text in git.** The roster (people, departments, appointments) is CSV, edited via pull request, with full history. The database is built from these files plus the snapshot log.
5. **Compute, don't store, derived numbers.** Percentiles, years since PhD, citations per year, department medians, and ranks are SQL views, recomputed on every build.

## 3. Database schema

SQLite for the built database (single file, zero ops, queryable with DuckDB or Python, small enough to ship to the browser). The schema is standard SQL and moves to Postgres unchanged if the project ever needs a server.

```sql
-- Curated tables (maintained as CSV in data/roster/, loaded on build)

CREATE TABLE department (
  department_id   INTEGER PRIMARY KEY,
  name            TEXT NOT NULL,         -- 'Luskin School of Public Affairs'
  university      TEXT NOT NULL,         -- 'UCLA'
  short_name      TEXT NOT NULL UNIQUE,  -- 'UCLA' as used in the workbook
  country         TEXT NOT NULL,         -- 'US' | 'CA'
  url             TEXT,
  openalex_institution_id TEXT,          -- 'I161318765', for author matching
  ror_id          TEXT,
  acsp_member     INTEGER,               -- 1/0, if inclusion follows ACSP
  active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE person (
  person_id       INTEGER PRIMARY KEY,
  first_name      TEXT NOT NULL,
  middle_name     TEXT,
  last_name       TEXT NOT NULL,
  display_name    TEXT NOT NULL,
  gender          TEXT,                  -- internal / aggregate use only
  phd_year        INTEGER,
  phd_institution TEXT,
  interests       TEXT,
  google_scholar_id TEXT UNIQUE,         -- 12-char user id, not the URL
  openalex_author_id TEXT UNIQUE,        -- 'A5023456789'
  orcid           TEXT UNIQUE,
  semantic_scholar_id TEXT,
  researchgate_url TEXT,
  linkedin_url    TEXT,
  personal_url    TEXT,
  notes           TEXT
);

CREATE TABLE person_alias (               -- name variants for matching
  person_id       INTEGER REFERENCES person,
  alias           TEXT NOT NULL,
  PRIMARY KEY (person_id, alias)
);

CREATE TABLE affiliation (                -- one row per appointment
  affiliation_id  INTEGER PRIMARY KEY,
  person_id       INTEGER NOT NULL REFERENCES person,
  department_id   INTEGER NOT NULL REFERENCES department,
  rank            TEXT NOT NULL CHECK (rank IN
                    ('assistant','associate','full','other')),
  is_primary      INTEGER NOT NULL DEFAULT 1,
  start_date      DATE,                  -- NULL = unknown (inherited from workbook)
  end_date        DATE,                  -- NULL = current
  source          TEXT                   -- 'workbook-2026', 'dept website', 'GS profile'
);

-- Append-only tables (written by the collector)

CREATE TABLE collection_run (
  run_id          INTEGER PRIMARY KEY,
  started_at      TIMESTAMP NOT NULL,
  finished_at     TIMESTAMP,
  trigger         TEXT,                  -- 'schedule' | 'manual'
  source          TEXT NOT NULL,         -- 'google_scholar' | 'openalex' | 'manual'
  n_attempted     INTEGER, n_succeeded INTEGER, n_failed INTEGER,
  notes           TEXT
);

CREATE TABLE metric_snapshot (
  snapshot_id     INTEGER PRIMARY KEY,
  run_id          INTEGER NOT NULL REFERENCES collection_run,
  person_id       INTEGER NOT NULL REFERENCES person,
  source          TEXT NOT NULL,         -- 'google_scholar' | 'openalex' | 'pop' | 'manual'
  collected_at    TIMESTAMP NOT NULL,
  total_citations INTEGER,
  h_index         INTEGER,
  i10_index       INTEGER,
  citations_5yr   INTEGER,               -- GS "since 20XX" column
  h_index_5yr     INTEGER,
  works_count     INTEGER,
  raw_json        TEXT,                  -- full API/scrape payload for audit
  UNIQUE (person_id, source, collected_at)
);

CREATE TABLE identity_candidate (         -- disambiguation review queue
  candidate_id    INTEGER PRIMARY KEY,
  person_id       INTEGER NOT NULL REFERENCES person,
  source          TEXT NOT NULL,         -- 'openalex' | 'google_scholar' | 'orcid'
  external_id     TEXT NOT NULL,
  score           REAL,                  -- 0..1 from matcher
  evidence        TEXT,                  -- JSON: name sim, inst match, topic overlap
  status          TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','accepted','rejected')),
  reviewed_by     TEXT, reviewed_at TIMESTAMP
);
```

Derived views, recomputed on every build:

- `v_current_affiliation`: primary appointment with `end_date IS NULL`.
- `v_latest_metrics`: most recent snapshot per person per source.
- `v_person_percentiles`: `PERCENT_RANK() OVER (ORDER BY total_citations)` overall and `PARTITION BY rank`, for both citations and h-index. This reproduces Excel's `PERCENTRANK` and replaces the seven macros. Also `years_since_phd` and `citations_per_year`, fixing the broken `CY2017` column.
- `v_department_summary`: faculty count, total, median, mean, per-capita citations, median h-index, share with Scholar profiles. Reproduces `Current Schools`.
- `v_top_faculty`: ranked list, reproduces `Top Faculty`.
- `v_person_timeseries`: one row per person per collection date, for sparklines.

Acceptance test for phase 1: load the workbook, run the views, and match Tom's percentile and department columns to within rounding.

## 4. Data sources and the disambiguation problem

Google Scholar has no API and blocks datacenter IPs, so automation has to be pragmatic. Use two sources with different jobs:

| | Google Scholar | OpenAlex |
|---|---|---|
| Access | Scrape profile pages (`scholarly` library, throttled) or a paid Scholar API such as SerpApi | Metered REST API. A free account's key gives about $1 of usage a day; lookups by author id are free, name searches cost about a tenth of a cent each. Our one-time matching costs a dollar or two; monthly collection costs nothing |
| Covers | 808 faculty with profiles, plus any the discovery pass turns up | Everyone, once matched |
| Numbers | Higher; includes books, reports, gray literature that matter in planning | Lower and systematically different; journal-centric |
| Role | **Headline metric** for everyone with a profile. Continuity with Tom's series and with what the field expects | **Fallback headline** for faculty without a Scholar profile, flagged as OpenAlex and a likely undercount. Also collected for everyone as a comparison series |

**Source policy (decided 2026-09-14).** Scholar is the default. For faculty without a Scholar profile the headline number comes from OpenAlex, and every place it appears carries the source and a note that OpenAlex counts fewer citations than Scholar. Rankings include everyone, with fallback rows marked. This is deliberate: it keeps the list complete while giving faculty a clear reason to set up a Scholar profile. Sources stay separate in storage; the `source` column travels with every number so the mixing is visible, never silent.

**Where the Scholar collector runs.** GitHub Actions runners are blocked by Scholar. Two options, decide in phase 2 after a test:

- Self-hosted runner or `launchd` job on a UT machine, using `scholarly` with a few seconds between requests. 808 profiles at one request every 10 to 15 seconds is about three hours. This is what Tom did by hand. Scraping is against Google's terms, which is a risk to note, not a blocker; the data is public profile data.
- A commercial Scholar API (SerpApi's `google_scholar_author` engine returns exactly the citation table). Runs fine from GitHub Actions. Costs money only in the months a run happens. Check current pricing before committing.

**Disambiguation, done once.** The 808 people with a Scholar id need no disambiguation on the Scholar side. The real work Tom described is matching everyone to OpenAlex, and checking whether any of the 243 without a recorded profile actually have one.

1. Query OpenAlex `/authors?search=<name>` and filter by the department's `openalex_institution_id`.
2. Score each candidate: name similarity (Jaro-Winkler on full name and on last name plus first initial), current or past institution match, topic overlap with planning concepts, first publication year consistent with PhD year, coauthor overlap with already-matched faculty in the same department.
3. Auto-accept when one candidate scores above threshold and the runner-up is far below. Everything else lands in `identity_candidate` as `pending`.
4. Review pending rows in a simple table (a CSV in the repo or a small review page) and mark accepted or rejected. Expect a few hours of review for the initial pass, then a handful of new hires per semester.

Once a person has an OpenAlex id, monthly collection is a single API call per person with no ambiguity.

**Scholar profile discovery.** For each person without a Scholar id, one Scholar publication search restricted to their name, fetched from a UT connection, links author names to profiles where they exist (Scholar's dedicated author search now requires a Google sign-in). Linked names that match ours are fetched for affiliation text and citation totals. They are scored the same way (name, affiliation, plausibility against the number we already hold) and land in the same review queue. Run once over the 243, then each semester for new hires.

**Publish or Perish is retired.** It is a desktop program that runs the same Scholar name search and relies on a person unticking the wrong papers. Automating the fetch without that judgment produces confident wrong numbers; automating the judgment is the unsolved problem. Policy (2026-09-14): we do not reconstruct counts for people without a profile. They get the flagged OpenAlex number, and the fix is in their hands. The migrated Publish or Perish figures stay in the database as the first data point for those 243 and are superseded by the first OpenAlex snapshot.

**Detecting roster changes automatically.** Twice a year, produce a diff report for human review rather than editing blind: Scholar profile affiliation text or verified-email domain changed; OpenAlex `last_known_institutions` changed; citation count fell by more than 20% (almost always a wrong profile, not a real drop); profile disappeared. Rank changes still need a person to check department websites, as before.

## 5. Update cadence and scheduling

| What | When | How |
|---|---|---|
| OpenAlex snapshot | Monthly, 1st of month | GitHub Actions cron; about 1,051 free id lookups, a few minutes |
| Google Scholar snapshot | Quarterly (Jan, Apr, Jul, Oct) | Self-hosted runner or Scholar API; results committed as a snapshot file |
| Roster review | Semiannual (Jan, Jul), matching Tom's rhythm | Pipeline emails the change-detection report; a person edits the roster CSVs by pull request |
| Scholar profile discovery | Semiannual, with roster review | One publication-search page per person without a profile, plus one profile page per plausible candidate, from a UT connection; candidates go to the review queue |
| Site rebuild and deploy | On every push to `main` and after every collection | GitHub Actions builds SQLite from CSV and snapshots, builds the site, deploys to GitHub Pages |

Each run appends a snapshot file under `data/snapshots/<source>/<YYYY-MM-DD>.csv` and commits it. The CSVs are the canonical, diffable record; the SQLite file is a build product that is also published for download. Failures open a GitHub issue automatically so a silent stall cannot go unnoticed for a semester.

## 6. Website

A **static site** is the right shape: about 1,200 entities, read-only for visitors, updated a few times a month. No server, no database to keep alive, free hosting on GitHub Pages under a UT or project domain.

Recommended stack: **Observable Framework**. Its Python data loaders query the SQLite file at build time, it supports parameterized routes for one page per person and per department, and Observable Plot handles sparklines and distributions well. Astro is a fine alternative if the team prefers a general-purpose site builder. Search is client-side over a prebuilt JSON index (roughly 200 KB for all people and departments) using MiniSearch, so typing a name or school returns instantly.

Pages:

- **Home**: search box front and center; current top faculty and top departments; "data as of" date.
- **Person page**: current appointment and rank, PhD year and school, the headline metric with a source badge (a visible note when it is OpenAlex, with a link to how to create a Scholar profile), the OpenAlex comparison series where available, sparklines, percentile within rank and overall, links to Scholar and OpenAlex profiles. No gender shown.
- **Department page**: roster with rank, latest metrics and within-rank percentiles per faculty member, summary tiles (count, median, mean, per-capita), department trend line, link to the program.
- **Rankings**: faculty and department tables, filterable by rank, source, and country, with column sort.
- **Methods and caveats**: sources, cadence, how percentiles are computed, why Scholar and OpenAlex differ, inclusion criteria for schools, known limitations.
- **Corrections**: a GitHub issue template so faculty can request a fix or opt out of the individual page.
- **Download**: the SQLite file and CSVs, with a citation and license.

Optional companion: publish the SQLite file with Datasette for people who want arbitrary queries and a JSON API without us building it.

## 7. Repository layout

```
planning-citation-metrics/
  data/
    roster/            department.csv, person.csv, affiliation.csv, person_alias.csv
    snapshots/
      google_scholar/  2026-01-15.csv, ...
      openalex/        2026-10-01.csv, ...
    review/            identity_candidates.csv
    legacy/            UPDATED_Cites2026.xlsm, the handover workbook
  pipeline/            Python package
    load_workbook.py   one-time migration of the xlsm into roster CSVs
    build_db.py        CSV + snapshots -> citations.sqlite, runs views and tests
    collect_openalex.py
    collect_scholar.py
    match_identities.py
    detect_changes.py  semiannual roster diff report
  sql/                 schema.sql, views.sql
  site/                Observable Framework project
  tests/               regression test against Tom's percentile columns
  .github/workflows/   monthly.yml, quarterly.yml, deploy.yml
  docs/PLAN.md         this document
```

## 8. Phased delivery

**Phase 0. Settle scope** (this month)
- Done 2026-09-14: inclusion rule per Tom is ACSP member schools plus a handful added on request, mostly Canadian. `department.acsp_member` records which is which; the methods page will say so.
- Done 2026-09-13: the two moves (Guang Tian to Utah, Lisa Berglund to Morgan State) were verified against Scholar profiles and department pages, and the stale rows are closed as ended affiliations.

**Phase 1. Schema and migration** (2 to 3 weeks)
- Write `schema.sql` and `views.sql`. Migrate the workbook into roster CSVs and one 2026 snapshot per person (source `google_scholar` or `pop`).
- Regression test: views reproduce Tom's percentiles and department summaries.
- Done: the migrated snapshot is dated 2026-03-01 (Scholar) and 2026-02-20 (Publish or Perish), per Tom.

**Phase 2. Collectors and identity matching** (3 to 4 weeks)
- OpenAlex collector and matcher; run the initial match, review the pending queue.
- Scholar collector; test `scholarly` from a UT IP versus a commercial API and pick one.
- Scholar profile discovery pass over the 243 people without a recorded profile.
- `collection_run` logging, retries, and the drop-detection check.

**Phase 3. Scheduling** (1 week)
- GitHub Actions workflows for monthly OpenAlex, quarterly Scholar, and deploy. Failure alerts as issues.

**Phase 4. Website** (3 to 4 weeks)
- Person, department, rankings, methods, and download pages. Client-side search. Deploy to GitHub Pages.

**Phase 5. Roster maintenance workflow** (1 week, then ongoing)
- Change-detection report, semiannual review checklist, correction issue template.

Rough total: about three months of part-time effort, front-loaded in phases 1 and 2. After that the recurring cost is a few hours twice a year for roster review plus responding to correction requests.

## 9. Questions for Tom, answered 2026-09-14

1. **Last refresh:** Scholar totals and h-indices on 2026-03-01. Publish or Perish lookups 2026-02-15 to 2026-02-20. The first snapshot is dated accordingly.
2. **Inclusion rule:** ACSP member schools originally, plus several added on request, mostly Canadian. Recorded per department in `acsp_member`.
3. **Publish or Perish queries:** not retained. Those 243 faculty move to OpenAlex going forward, labelled as such.
4. **`RegFaculty` / `NonPhD`:** meant to keep the list to regular full-time tenure-track appointments and to flag non-PhD faculty. Kept as `affiliation.appointment_type`; rankings include only `regular`.
5. **Duplicates:** confirmed as moves he forgot to clean up. Closed as ended affiliations.

Decided by Alex, 2026-09-14, rather than left to Tom: Scholar by default, OpenAlex as the flagged fallback. See the source policy in section 4.
