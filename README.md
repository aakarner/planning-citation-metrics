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
.venv/bin/python -m pipeline.load_workbook data/legacy/UPDATED_Cites2026.xlsm --collected-at 2026-09-13
```

Writes the roster CSVs and one snapshot per person under `data/snapshots/google_scholar/`
(faculty with a Scholar profile) and `data/snapshots/pop/` (faculty Tom looked up in
Publish or Perish). Two people who appear at two schools in the workbook are merged, with
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
| `v_headline_metrics` | latest Scholar or Publish or Perish number per person |
| `v_openalex_metrics` | latest OpenAlex number per person, kept as a separate series |
| `v_person_timeseries` | every observation, for sparklines |

## Tests

```bash
.venv/bin/python -m pytest -q
```

The regression test rebuilds the database and checks that the views reproduce the
workbook's percentile columns and department summaries to within rounding. It documents
the known deviations: the two duplicate rows, the one-row-off macro range at the
associate/full boundary, and one stale percentile cell.
