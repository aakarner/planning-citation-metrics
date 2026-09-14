-- Planning Citation Metrics: core schema.
-- Curated tables are loaded from data/roster/*.csv on every build.
-- Append-only tables are loaded from data/snapshots/<source>/<date>.csv.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- curated

CREATE TABLE department (
  department_id            INTEGER PRIMARY KEY,
  short_name               TEXT NOT NULL UNIQUE,   -- label used in the workbook, e.g. 'UCLA'
  name                     TEXT NOT NULL,          -- unit name, e.g. 'Luskin School of Public Affairs'
  university               TEXT NOT NULL,
  country                  TEXT NOT NULL CHECK (country IN ('US', 'CA')),
  url                      TEXT,
  openalex_institution_id  TEXT,                   -- 'I161318765', for author matching
  ror_id                   TEXT,
  acsp_member              INTEGER,                -- 1/0/NULL(unknown)
  active                   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE person (
  person_id            INTEGER PRIMARY KEY,
  first_name           TEXT NOT NULL,
  middle_name          TEXT,
  last_name            TEXT NOT NULL,
  display_name         TEXT NOT NULL,
  gender               TEXT,                       -- loaded from data/private/ when present; never committed or shown
  phd_year             INTEGER,
  phd_institution      TEXT,
  interests            TEXT,
  google_scholar_id    TEXT UNIQUE,                -- 12-character Scholar user id, not the URL
  openalex_author_id   TEXT UNIQUE,                -- 'A5023456789'
  orcid                TEXT UNIQUE,
  semantic_scholar_id  TEXT,
  researchgate_url     TEXT,
  linkedin_url         TEXT,
  personal_url         TEXT,
  notes                TEXT
);

CREATE TABLE person_alias (
  person_id  INTEGER NOT NULL REFERENCES person(person_id),
  alias      TEXT NOT NULL,
  PRIMARY KEY (person_id, alias)
);

-- One row per appointment. A person who moves gets a second row; the old
-- row is closed by setting end_date. end_date IS NULL means current.
CREATE TABLE affiliation (
  affiliation_id  INTEGER PRIMARY KEY,
  person_id       INTEGER NOT NULL REFERENCES person(person_id),
  department_id   INTEGER NOT NULL REFERENCES department(department_id),
  rank            TEXT NOT NULL CHECK (rank IN ('assistant', 'associate', 'full', 'other')),
  -- 'regular' = full-time tenure-track/tenured, the population Tom tracked.
  -- Anything else (adjunct, visiting, clinical, emeritus) is excluded from rankings.
  appointment_type TEXT NOT NULL DEFAULT 'regular'
                  CHECK (appointment_type IN ('regular', 'adjunct', 'visiting', 'clinical', 'emeritus', 'other')),
  is_primary      INTEGER NOT NULL DEFAULT 1,
  start_date      TEXT,                            -- ISO date; NULL = unknown
  end_date        TEXT,                            -- ISO date; NULL = current
  source          TEXT                             -- where this fact came from
);

CREATE INDEX affiliation_person_idx ON affiliation(person_id);
CREATE INDEX affiliation_department_idx ON affiliation(department_id);

-- ------------------------------------------------------------ append-only

CREATE TABLE collection_run (
  run_id       INTEGER PRIMARY KEY,
  source       TEXT NOT NULL,                      -- 'google_scholar' | 'pop' | 'openalex' | 'manual'
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  trigger      TEXT,                               -- 'schedule' | 'manual' | 'migration'
  n_attempted  INTEGER,
  n_succeeded  INTEGER,
  n_failed     INTEGER,
  notes        TEXT
);

CREATE TABLE metric_snapshot (
  snapshot_id      INTEGER PRIMARY KEY,
  run_id           INTEGER NOT NULL REFERENCES collection_run(run_id),
  person_id        INTEGER NOT NULL REFERENCES person(person_id),
  source           TEXT NOT NULL,
  collected_at     TEXT NOT NULL,                  -- ISO date or datetime
  total_citations  INTEGER,
  h_index          INTEGER,
  i10_index        INTEGER,
  citations_5yr    INTEGER,                        -- Scholar's "since 20XX" column
  h_index_5yr      INTEGER,
  works_count      INTEGER,
  raw_json         TEXT,                           -- full payload for audit
  UNIQUE (person_id, source, collected_at)
);

CREATE INDEX metric_snapshot_person_idx ON metric_snapshot(person_id, source, collected_at);

-- Disambiguation review queue. Written by the matcher, resolved by a person.
CREATE TABLE identity_candidate (
  candidate_id  INTEGER PRIMARY KEY,
  person_id     INTEGER NOT NULL REFERENCES person(person_id),
  source        TEXT NOT NULL,                     -- 'openalex' | 'google_scholar' | 'orcid'
  external_id   TEXT NOT NULL,
  score         REAL,
  evidence      TEXT,                              -- JSON
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'accepted', 'rejected')),
  reviewed_by   TEXT,
  reviewed_at   TEXT,
  UNIQUE (person_id, source, external_id)
);
