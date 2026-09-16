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
same review queue with `source=google_scholar`. Because searching by name alone mostly turns
up strangers, a candidate is rejected outright, reason kept, when its affiliation gives a
decisive reason: it names an institution that is not this person's department, or a field no
planning academic works in, or a pre-faculty stage. Everything else stays pending for a
person, including an affiliation that names no institution at all (a profile reading
"Professor of urban history and planning" is unhelpful but does not say it is someone else)
and one naming a different department we do track, which would be a real find plus a move. A candidate is also rejected when its citation count is
more than four times or less than a fifth of the figure we already hold, since both measure
Scholar citations for the same claimed person. Only the reject direction is automated: a wrong
rejection leaves someone where they already were, while a wrong acceptance publishes a
stranger's count as theirs.

`--reclassify` re-applies these rules to rows already in the queue without any network calls.
`--apply` writes accepted ids from the queue into `person.csv`, which is how a person's own
decisions take effect.

People whose recorded Scholar id stops resolving (the collector reports "empty profile") are
re-found by a direct fetch that follows Google's redirect, from an unblocked address:

```bash
.venv/bin/python -m pipeline.collect_scholar --via scholarly --ids <old id> ...
```

We do not reconstruct citation counts for people without a profile (that was Publish or
Perish, by hand); they get the flagged OpenAlex number instead.

## Scheduled discovery (local, macOS)

Discovery has to run from a residential or campus address, so it cannot live in GitHub
Actions. A launchd agent runs one batch of 30 a day:

```bash
bash scripts/install_discovery_agent.sh          # daily at 10:15
bash scripts/install_discovery_agent.sh 21 30    # or a time you pick
```

Each run pulls, searches 30 people, commits anything it found, and pushes. It takes a lock so
runs cannot overlap, logs to `~/Library/Logs/planning-citations-discovery.log`, and when
nobody is left to search it says so and stops doing work. Remove it with:

```bash
launchctl bootout gui/$(id -u)/com.aakarner.planning-citations.discovery
rm ~/Library/LaunchAgents/com.aakarner.planning-citations.discovery.plist
```

## Applying roster changes

```bash
.venv/bin/python -m pipeline.apply_changes --dry-run   # print every decision
.venv/bin/python -m pipeline.apply_changes             # write data/roster/affiliation.csv
```

Policy: trust the Scholar profile, publish, and take corrections through the site. When the
profile's affiliation text names another tracked department the person is moved there; when
it names an institution we don't track and the verified email agrees, the appointment is
closed and the destination kept in the note; when it names the current department at a
higher rank, the person is promoted. A stale verified email alone never triggers anything,
profiles that still read "PhD student" or "postdoc" are ignored, and profiles in fields no
planning faculty hold (electrical engineering, biochemistry) are reported as probable
namesakes and left alone. Nothing is deleted: old appointments are closed with the snapshot
date and the evidence in `source`.

## Build the site

```bash
.venv/bin/python -m pipeline.build_db && .venv/bin/python -m pipeline.build_site
```

Writes `build/site`: a page per current faculty member and per department, sortable rankings,
a methods page, a client-side search index, and a copy of the database for download.

**Every word on the site is in [site/copy.toml](site/copy.toml)**, not in the Python. Edit the
prose there, rebuild, and push; the deploy runs itself. Values in curly braces like
`{people_count}` are filled in at build time, and a name the page does not provide stops the
build with a message naming the passage and listing what is available, so a typo cannot reach
the site. `tests/test_copy.py` fills every passage in the file, which catches a malformed brace
anywhere in it. Plain
Python and SQLite, no Node toolchain. Deployed to GitHub Pages by `.github/workflows/deploy.yml`
on every push that touches `data/`, `sql/`, or `pipeline/`:

<https://aakarner.github.io/planning-citation-metrics/>

## Audit OpenAlex matches

```bash
.venv/bin/python -m pipeline.audit_matches --dry-run
.venv/bin/python -m pipeline.audit_matches
```

Run after an OpenAlex collection. The matcher decides before any metrics exist; once a snapshot
is in, a match whose OpenAlex citation count is more than 3x or less than 5% of the Scholar or
Publish or Perish count we hold is almost certainly a namesake. Offenders lose their
`openalex_author_id` and their review row is marked `rejected`, which the matcher then respects.
`v_openalex_metrics` publishes only matches currently held, so a disavowed snapshot stays in the
database for audit without reaching the site.

The same test then runs over candidates still awaiting review, where it matters most: working
down the review sheet by name and institution would otherwise accept the hepatologist Guadalupe
Garcia-Tsao, 52,281 citations against a Publish or Perish base of 63, for a planning academic of
the same name. Pending rows are audited on the high side only. A candidate with far *fewer*
citations than our figure is usually OpenAlex splitting one real person across sparse author
records, so rejecting those would label some 1,600 rows as namesakes to no purpose.

## Change report

```bash
.venv/bin/python -m pipeline.detect_changes      # -> build/change_report_<date>.md
```

Run after `apply_changes`: lists what the automatic rules could not settle, for a person to
look at. Scholar email or affiliation text that does not match the department and was not
strong enough to act on, OpenAlex last known institution elsewhere, a citation count that
fell more than 20% (usually a wrong or merged profile), and profiles that vanished. It edits
nothing.

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
