# Planning Citation Metrics: how the pipeline and site work

This document describes the system as it runs. It continues the dataset Tom Sanchez
maintained by hand from 2017 to 2026 and publishes it at
[scholarmetrics.com](https://scholarmetrics.com). For the commands, see the README; for
why individual records look the way they do, the `source` and `notes` columns in the
roster files carry the evidence.

## 1. Scope

**Who is tracked.** Regular full-time tenured and tenure-track faculty at planning
programs in the United States and Canada: ACSP member schools, plus a handful of programs
added on request, mostly Canadian. Adjunct, visiting, clinical and emeritus appointments
are recorded but not listed. Each program page says which category it is in.

**A second, unranked list.** Planning scholars appointed outside planning programs,
people with a planning doctorate, a tenure-line post at a US or Canadian university, and
research that sits in planning, are listed on a separate page beside the rankings. They
never enter a ranking, a percentile or a program figure. Their units are recorded like
programs but with `kind = 'other'`.

**What is measured.** Total citations and h-index per person, from Google Scholar where
the person has a profile and from OpenAlex otherwise, with every figure carrying its
source. Percentiles, medians and per-year rates are computed from these, never stored.

## 2. Principles

1. **Person, appointment and measurement are separate records.** A person has many
   appointments over time and many readings over time. Moves and promotions close one
   appointment row and open another; nothing is edited in place.
2. **Append, never overwrite.** Every collection writes new snapshot rows. Any past state
   can be rebuilt.
3. **Identify once, then run on ids.** A person's Scholar id, OpenAlex record set and
   ORCID are resolved with evidence and, where needed, a reviewer; collection then runs
   unattended on those ids.
4. **The curated data is plain text in git.** People, programs, appointments and the
   review queue are CSV files. The database and the site are build products.
5. **Compute derived numbers in SQL views.** Percentiles, years since PhD, citations per
   year, program medians, rank benchmarks.
6. **Policy lives in code with the reason beside it.** Thresholds and rules carry a
   comment naming the case that set them, so a future change is made knowingly.

## 3. Data model

SQLite, built from the CSV files on every run. Standard SQL throughout.

**Curated tables** (`data/roster/`):

- `department`: a program or other unit. Name, university, country, website, OpenAlex
  institution id, ROR id, ACSP membership (`full`, `affiliate`, `none`), and `kind`
  (`planning` or `other`).
- `person`: names, PhD year and institution, interests, and the identifiers: Scholar id,
  OpenAlex author id, ORCID, plus optional profile links. A `notes` column records the
  provenance of anything entered or changed by hand. Gender is loaded from a private,
  uncommitted file for aggregate use only and never appears on the site.
- `person_alias`: name variants used in matching.
- `affiliation`: one row per appointment: person, unit, rank (`assistant`, `associate`,
  `full`, `other`), appointment type (`regular`, `adjunct`, `visiting`, `clinical`,
  `emeritus`, `other`), start and end dates, and a `source` string saying where the fact
  was read. A current appointment has no end date. A `rank verified <date>` note in the
  source marks a rank confirmed from a program page or by a reviewer.

**Append-only tables** (`data/snapshots/<source>/<date>.csv`, plus a `.meta.json` per
run):

- `collection_run`: one row per run, with counts and notes.
- `metric_snapshot`: one reading per person per source per date: total citations,
  h-index, i10, five-year figures, works count, and the full payload as JSON. For Scholar
  that payload includes the profile's affiliation line, verified email domain, interests
  and per-year citation counts, which several rules below depend on.

**Review queue** (`data/review/identity_candidates.csv`):

- `identity_candidate`: one row per candidate record per person: source, external id,
  match scores (name similarity, institution match, topic share, citation ratio), the
  record's own figures, and a status: `pending`, `accepted`, `rejected`, or `none` for a
  search that found nothing. Every decision carries who made it and why.

**Views** recomputed on every build: current appointments (planning programs only, and a
second view covering other units), latest reading per person and source, the headline
metric per person, Scholar and OpenAlex series restricted to ids the roster still holds,
percentiles overall and within rank, per-year rates, program summaries, rank benchmarks,
the ranked faculty list, the time series for sparklines, and the other-units list.

## 4. Sources and the headline figure

| | Google Scholar | OpenAlex |
|---|---|---|
| Access | SerpApi's Scholar author endpoint, one call per profile; direct scraping only for spot checks | Open REST API, free by-id lookups |
| Covers | Everyone with a profile, about three quarters of the roster | Everyone matched to a record |
| Counts | Higher; includes books, reports and grey literature | Lower and journal-centric, typically half to three quarters of Scholar |
| Role | Headline figure wherever a profile exists | Headline figure for the rest, marked as OpenAlex with a note; comparison figure on everyone's page |

**Precedence.** Scholar, then OpenAlex, then the Publish or Perish figures inherited from
the workbook, which are frozen and shown only until an OpenAlex record is found. A source
publishes only while the roster holds its identifier: clear a Scholar id and the person
falls back to OpenAlex at once, with the old readings kept for audit but hidden from the
person's series.

**Small records count.** An accepted OpenAlex record is the person's figure however small.
A citation count can prove a record is somebody else's when it is far larger than the
figure we already hold; it never disproves identity by being small. A Scholar profile is
the route to a fuller number, and the site says so wherever a fallback figure appears.

**Split records are summed.** OpenAlex often holds one author as several records. All of a
person's accepted records are collected together: citations, works and i10 are summed, the
h-index is the largest, and the record with the most works is the primary id.

## 5. Identity

**Scholar profiles.** Most Scholar ids came from the workbook. For people without one, a
daily search runs from a residential address (Scholar blocks datacentre addresses): a
publication search by name, the linked author profiles fetched and scored on name,
affiliation text and plausibility against the figure already held. A profile whose
affiliation names the person's institution is accepted; one naming an institution we do
not track, a student stage, or a field no planning scholar works in is rejected with the
reason; the undecidable go to the queue. Rejected profiles are never proposed again. A
profile whose id stops resolving is cleared, and the person is searched again.

**Namesakes.** Name, institution and even a verified email domain can all agree and the
profile still belong to someone else at the same university. The one cheap tell is the
profile's interests line. Any held profile whose interests or affiliation name a field no
planning scholar works in, and which does not place itself at the person's department, is
cleared and vetoed automatically; the person falls back to OpenAlex until the daily search
finds the right profile.

**OpenAlex records.** The matcher searches by name, filters by the program's institution,
and scores each candidate on name similarity, institution match (any affiliation in the
record's history), planning topic share, and citation plausibility against the Scholar or
inherited figure. Candidates settle without a reviewer when name and institution match
and the numbers do not object: a fragment of a few works settles on the match alone; a
larger record must stay within a bound of the figure held; an extra record for someone
already matched must also sit at the person's institution now, or carry a planning share
of topics, since the institution-history match is weak for common names. Everything else
waits for a reviewer. An audit after every collection un-accepts records whose citation
count has grown far beyond the person's Scholar figure, and retires ids that OpenAlex has
deleted or merged.

**Review sheets.** Pending candidates for people without a Scholar headline are written to
an Excel workbook with the evidence, dropdowns and a how-to tab, banded by how much
judgement each needs. A reviewer marks each row accepted or rejected; the import writes
the decisions and reasons back to the queue and the roster takes the accepted ids. The
builders refuse to overwrite a workbook that already holds reviewer entries.

**ORCID.** Copied from an accepted OpenAlex record, or read off a faculty page by a
reviewer; a disagreement between the two is flagged, since it usually means the OpenAlex
match is wrong. ORCID's education section fills a missing PhD year and institution where
the record names a doctorate.

## 6. Collection and automation

| What | When | Where |
|---|---|---|
| Google Scholar readings | Rolling: about fifty profiles every Monday, oldest reading first, so every profile is re-read roughly every fifteen weeks | GitHub Actions, SerpApi free tier (250 searches a month, paced to its hourly cap) |
| OpenAlex readings | Monthly, 1st of the month, everyone matched | GitHub Actions, free by-id lookups; followed by the match audit and the ORCID PhD fill |
| Scholar profile discovery | Daily, one batch; fetches figures for anything it finds | A launchd agent on the maintainer's Mac, with a retry slot if the machine was asleep |
| Roster rules | After every Scholar run | Same job as the readings |
| Site build and deploy | On every push to `main`, and after every collector commits | GitHub Actions to GitHub Pages at scholarmetrics.com |

A push made by a workflow does not trigger other workflows, so each collector calls the
deploy workflow itself after committing. Each run appends a snapshot file and commits it;
the CSV files are the canonical, diffable record and the SQLite file is a build product,
also published for download.

Because Scholar collection is rolling, there is no "latest run" to compare with: the
change report sets each person against their own previous reading, flags any held profile
not re-read within twenty weeks, and the homepage quotes the oldest and newest reading
dates rather than one refresh date.

## 7. Keeping the roster right

**Program websites are the better source.** A scan of every program's faculty listing
reads ranks for people we hold and adds tenure-line new hires, who go straight onto the
roster. Ranks apply in both directions. Names on a school-wide page that cannot be tied to
the program, and names with no rank shown, are held in a side file rather than added.
Pages that need a browser, or are in French or Spanish, are read by hand and saved as
text for the scanner to merge. Dead program links are fixed as they are found.

**Scholar profiles drive routine changes.** After each Scholar reading, the roster rules
read the profile's affiliation text and verified email: text naming another tracked
program is a move; text naming an institution we do not track, with the email agreeing,
is a departure with the destination noted; a stated higher rank at the current program is
a promotion. A stale email alone changes nothing. A profile too thin to be the person
years past their PhD is reported, not acted on. Any move, promotion or departure against
an appointment whose rank was verified from a program page or by a reviewer within the
last six months is held, since the page is the fresher source and a profile often lags a
move by months.

**Rank review.** Anyone held as assistant or associate for seven years or more, or whose
PhD year implies that for an assistant, is listed for a reviewer with their profile, their
program's page and their percentile within rank, which is a second hint when a rank is
stale. A reviewer's decision confirms the rank, changes it from a stated year, retires the
person to emeritus, or closes the appointment.

**Write-ins.** Faculty and colleagues report moves, retirements and new hires by email.
These are applied directly with the reporter and date in the row's source note, and are
the fastest signal the system has.

## 8. The site

Static HTML generated by Python from the SQLite file: about 1,250 pages, a client-side
search over a small JSON index, no server. Deployed on GitHub Pages under a custom domain.
All copy lives in one TOML file so wording can change without touching code.

**Home.** Search; counts of faculty and programs; the dates of the oldest and newest
Scholar readings and the latest OpenAlex refresh; a bar showing how many headline figures
come from each source. Medians and 75th percentiles by rank. "Most cited in the past
year": the five people in each rank who received the most citations in the twelve months
before their profile was last read, estimated from the profile's own per-year counts.
"New to the record": Scholar profiles found and moves between programs in the last ninety
days, and how many people left the tracked programs. A fold listing rank changes exists
but is switched off while the record catches up with ranks the workbook had missed.
Collapsed leaderboards of most-cited faculty and programs.

**Person.** Current appointment and rank, PhD, the headline figure with its source badge
and, for a fallback figure, a note on how to set up a Scholar profile. Sparkline of the
person's own readings, with readings from any profile other than the one now held left
out. OpenAlex comparison where the headline is Scholar, with the person's OpenAlex share of
their Scholar total beside the typical share across the site. Percentiles overall and within
rank. Links to the profiles on record.

**Program.** Roster with rank, figures and within-rank percentiles; summary tiles; link to
the program's site. People with no figure from any source are on the roster but not yet on
the page.

**Rankings and Programs.** Sortable tables, filterable by rank, country and source.

**Beyond planning.** The unranked list of planning scholars in other departments, with the
four criteria for inclusion and how to ask to be added.

**Methods.** Sources and their precedence, cadence, inclusion, how percentiles are computed,
known ways the numbers are wrong, and the download of the database and CSV files.

**Footer.** A contact address for corrections and a donate link to GitHub Sponsors; the site
is paid for out of pocket and its one fixed cost is the domain.

## 9. Repository layout

```
data/
  roster/            department.csv, person.csv, affiliation.csv, person_alias.csv,
                     new_hire_hold.csv
  snapshots/         google_scholar/, openalex/, pop/  (<date>.csv + <date>.meta.json)
  review/            identity_candidates.csv, the review workbooks, INSTRUCTIONS.md
  legacy/            the handover workbook (not committed)
  private/           gender (not committed)
pipeline/
  load_workbook.py          one-time migration
  build_db.py, build_site.py, site_assets.py
  collect_scholar.py, collect_openalex.py, clear_dead_ids.py
  find_scholar_profiles.py  daily discovery
  match_openalex.py, resolve_institutions.py, audit_matches.py
  apply_changes.py          roster rules from profiles
  detect_changes.py         change report and rank review
  scan_departments.py       program-page scan
  build_review_sheet.py, build_rank_sheet.py, import_review.py, import_rank_review.py
  fill_phd_from_orcid.py
scripts/             daily_discovery.sh, install_discovery_agent.sh
sql/                 schema.sql, views.sql
site/                copy.toml
tests/               rules, views, homepage blocks, regression against the workbook
.github/workflows/   collect-scholar-weekly.yml, collect-openalex.yml, deploy.yml, build.yml
docs/                this document; the program-scan hand-off brief
```

## 10. What still needs a person

- Reviewing the candidate workbooks when they come back, a few hours a semester.
- Acting on write-ins and on the change report's flags: suspected namesakes, profiles not
  re-read in twenty weeks, stale ranks.
- A program-page scan twice a year, with a browser pass for the pages the crawler cannot
  read, and entering PhD details for new hires whose ORCID does not carry them.
- Deciding who belongs on the unranked list when someone asks.

## 11. Known limitations

- Scholar readings are up to a quarter old for any given person, by design of the rolling
  schedule; the homepage shows the range.
- A namesake at the same university in a neighbouring field passes every automatic check.
  The interests test catches foreign fields only; the rest depends on someone noticing.
- The inherited figures were not all read on one date, so no comparison treats the first
  snapshot as a single baseline.
- OpenAlex merges and deletes author ids without notice; the audit retires dead ids after
  the fact, so a person can lose a figure for a month.
- People with no figure from any source have a roster entry but no page.
- Promotions can only be dated when a page or reviewer states the year; most rank changes
  record when we learned of them, not when they happened.
