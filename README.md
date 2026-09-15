# planning-citation-metrics

Tracking citation metrics for urban planning faculty across the United States and Canada.

The project continues the dataset Tom Sanchez maintained by hand from 2017 to 2026.
See [docs/PLAN.md](docs/PLAN.md) for the design: schema, collection cadence,
identity disambiguation, and the public site.

## Layout

```
data/
  roster/            curated: department.csv, person.csv, affiliation.csv, person_alias.csv
  snapshots/         append-only: <source>/<YYYY-MM-DD>.csv plus a .meta.json per run
  legacy/            the handover workbook
pipeline/            Python package (migration, build, collectors)
sql/                 schema.sql, views.sql
tests/               regression test against the workbook's own numbers
build/               build products, not committed (citations.sqlite)
```

The CSV files are the canonical record. The SQLite database is rebuilt from them.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

## One-time migration from the workbook

```bash
.venv/bin/python -m pipeline.load_workbook data/legacy/UPDATED_Cites2026.xlsm
```

Writes the roster CSVs and one snapshot per person under `data/snapshots/google_scholar/`
(dated 2026-03-01, when Tom last refreshed Scholar) and `data/snapshots/pop/` (dated
2026-02-20, the last day of his Publish or Perish lookups). Re-running preserves OpenAlex
ids, ORCIDs, and notes already in `person.csv`. Two people who appear at two schools in the workbook are merged, with
the older appointment closed; see `CURRENT_SCHOOL_OVERRIDE` in the script.

## Build the database

```bash
.venv/bin/python -m pipeline.build_db
```

Produces `build/citations.sqlite` with the tables from `sql/schema.sql` and the derived
views from `sql/views.sql`:

| View | Replaces |
|---|---|
| `v_person_percentiles` | the seven percentile macros; adds within-rank h-index percentile, years since PhD, citations per year |
| `v_top_faculty` | the `Top Faculty` sheet |
| `v_department_summary` | the `Current Schools` sheet; adds per-capita citations, median h-index, Scholar coverage |
| `v_headline_metrics` | the number shown for a person: Scholar if they have a profile, otherwise OpenAlex flagged as a fallback and likely undercount, otherwise the migrated Publish or Perish figure |
| `v_openalex_metrics` | latest OpenAlex number for everyone matched, as a comparison series |
| `v_person_timeseries` | every observation, for sparklines |

## Identity matching (OpenAlex)

OpenAlex meters API usage. Create a free account at openalex.org to get an API key;
without one an IP gets roughly 100 searches a day, with one about 1,000. Lookups by id
are free, so only the one-time matching spends budget.

```bash
export OPENALEX_API_KEY=...
.venv/bin/python -m pipeline.resolve_institutions      # department -> OpenAlex institution id
.venv/bin/python -m pipeline.match_openalex --limit 50  # trial; drop --limit for everyone
```

The matcher scores candidate authors on name similarity, institution match, topic
overlap, and citation plausibility against the Scholar number we already hold. Clear
winners are written to `person.csv`; everything else lands in
`data/review/identity_candidates.csv` with `status=pending`. Re-running keeps decisions.
API responses are cached under `build/cache/`.

Pending cases are reviewed in `data/review/openalex_review.xlsx` (one block per person,
grouped by why the matcher held it, yellow Decision cells, links to each OpenAlex page;
instructions on the first tab). To apply the decisions:

```bash
.venv/bin/python -m pipeline.import_review data/review/openalex_review.xlsx
.venv/bin/python -m pipeline.match_openalex --rescore
```

## Google Scholar collector

```bash
# SERPAPI_KEY=... in .env (serpapi.com; 250 lookups/month free, $25 for 1,000)
.venv/bin/python -m pipeline.collect_scholar --limit 20     # test
.venv/bin/python -m pipeline.collect_scholar                # full run, resumable within a day
```

Default backend is SerpApi's Scholar author endpoint: one JSON call per person, runs from
GitHub Actions, paced at 18 s to stay under SerpApi's cap of 200 searches an hour (a full run
of ~800 takes about four hours). `--via scholarly` scrapes profile pages directly and is kept
for spot checks only; a single address gets about 45 fetches before Scholar blocks it for
many hours.

## Scholar profile discovery

```bash
.venv/bin/python -m pipeline.find_scholar_profiles --limit 20 --sleep 10   # trial
.venv/bin/python -m pipeline.find_scholar_profiles                         # everyone without a profile
```

Runs a Scholar publication search restricted to the author's name from this machine and
follows the profile links on the results page. This is the only route that still exposes
those links: Scholar's own author search demands a sign-in, and SerpApi has discontinued its
author-search endpoint and strips profile links from regular search results. Scholar blocks an
address after ~45 requests for many hours, so run about 30 people per day (`--limit 30`); it
resumes where it left off. Candidates are scored like OpenAlex candidates and written to the
same review queue with `source=google_scholar`.

People whose recorded Scholar id stops resolving (the collector reports "empty profile") are
re-found by a direct fetch that follows Google's redirect, from an unblocked address:

```bash
.venv/bin/python -m pipeline.collect_scholar --via scholarly --ids <old id> ...
``` Clear winners
go into `person.csv`. Run it from a university or home connection. We do not reconstruct
citation counts for people without a profile (that was Publish or Perish, by hand); they get
the flagged OpenAlex number instead.

## Change report

```bash
.venv/bin/python -m pipeline.detect_changes      # -> build/change_report_<date>.md
```

Compares the latest snapshots with the roster and the previous snapshots, and lists the
people a reviewer should look at: Scholar verified-email domain or affiliation text that
does not match the department, OpenAlex last known institution elsewhere, a citation count
that fell more than 20% (usually a wrong or merged profile), and profiles that vanished.
It edits nothing; roster changes are made by hand in `data/roster/`.

## Private attributes

`data/private/person_private.csv` holds gender and is gitignored. The build merges it in
when present, so local analysis can use it while nothing published contains it.

## Tests

```bash
.venv/bin/python -m pytest -q
```

The regression test rebuilds the database and checks that the views reproduce the
workbook's percentile columns and department summaries to within rounding. It documents
the known deviations: the two duplicate rows, the one-row-off macro range at the
associate/full boundary, and one stale percentile cell.
