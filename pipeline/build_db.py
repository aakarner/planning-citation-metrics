"""Build the SQLite database from roster CSVs and snapshot files.

    python -m pipeline.build_db                 # -> build/citations.sqlite
    python -m pipeline.build_db --out /tmp/x.db

The database is a build product. The CSVs in data/ are the canonical record.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

from . import BUILD_DIR, PRIVATE_DIR, ROSTER_DIR, SNAPSHOT_DIR, SQL_DIR

ROSTER_TABLES = ["department", "person", "person_alias", "affiliation"]


def _rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return [{k: (None if v == "" else v) for k, v in r.items()} for r in csv.DictReader(f)]


def _insert(con: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
    con.executemany(sql, [[r[c] for c in cols] for r in rows])
    return len(rows)


def build(out: Path, roster_dir: Path = ROSTER_DIR, snapshot_dir: Path = SNAPSHOT_DIR,
          sql_dir: Path = SQL_DIR, quiet: bool = False) -> Path:
    log = (lambda *a: None) if quiet else print
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    con = sqlite3.connect(out)
    con.executescript((sql_dir / "schema.sql").read_text())

    for table in ROSTER_TABLES:
        path = roster_dir / f"{table}.csv"
        if not path.exists():
            sys.exit(f"missing roster file: {path}")
        n = _insert(con, table, _rows(path))
        log(f"  {table:<16} {n:>6} rows")

    # Private attributes live outside the public roster and are merged in only
    # when the file is present (it is gitignored, so CI builds never have it).
    private = PRIVATE_DIR / "person_private.csv"
    if private.exists():
        rows = _rows(private)
        con.executemany("UPDATE person SET gender = ? WHERE person_id = ?",
                        [(r["gender"], r["person_id"]) for r in rows])
        log(f"  {'person_private':<16} {len(rows):>6} rows (gender, local only)")

    # One collection_run per snapshot file; source is the directory name.
    n_snap = 0
    for path in sorted(snapshot_dir.glob("*/*.csv")):
        source = path.parent.name
        date = path.stem
        meta_path = path.with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        rows = _rows(path)
        bad = [r for r in rows if r["source"] != source]
        if bad:
            sys.exit(f"{path}: {len(bad)} rows whose source column != directory name {source!r}")
        cur = con.execute(
            "INSERT INTO collection_run (source, started_at, finished_at, trigger, "
            "n_attempted, n_succeeded, n_failed, notes) VALUES (?,?,?,?,?,?,?,?)",
            (source, date, date, meta.get("trigger", "schedule"),
             len(rows), len(rows), 0, meta.get("notes")),
        )
        run_id = cur.lastrowid
        for r in rows:
            r["run_id"] = run_id
        n_snap += _insert(con, "metric_snapshot", rows)
        log(f"  snapshot {source}/{date}: {len(rows)} rows")

    con.executescript((sql_dir / "views.sql").read_text())

    problems = con.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        sys.exit(f"foreign key violations: {problems[:5]}")
    n_current = con.execute("SELECT COUNT(*) FROM v_current_affiliation").fetchone()[0]
    n_people = con.execute("SELECT COUNT(*) FROM person").fetchone()[0]
    multi = con.execute(
        "SELECT person_id, COUNT(*) FROM v_current_affiliation GROUP BY person_id HAVING COUNT(*) > 1"
    ).fetchall()
    if multi:
        sys.exit(f"people with more than one current primary affiliation: {multi[:5]}")
    if n_current != n_people:
        log(f"  note: {n_people - n_current} people have no current affiliation")
    con.commit()
    con.close()
    log(f"  built {out}  ({n_people} people, {n_snap} snapshot rows)")
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=BUILD_DIR / "citations.sqlite")
    ap.add_argument("--roster-dir", type=Path, default=ROSTER_DIR)
    ap.add_argument("--snapshot-dir", type=Path, default=SNAPSHOT_DIR)
    args = ap.parse_args(argv)
    build(args.out, args.roster_dir, args.snapshot_dir)


if __name__ == "__main__":
    main()
