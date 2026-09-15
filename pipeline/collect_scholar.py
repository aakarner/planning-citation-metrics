"""Collect Google Scholar profile metrics.

Two backends:

  --via serpapi   (default when SERPAPI_KEY is set) SerpApi's google_scholar_author
                  endpoint: parsed JSON, one call per person, runs anywhere including
                  GitHub Actions. Free tier is 250 lookups a month; $25 buys 1,000,
                  enough for one full quarterly run, at a cap of 200 searches an hour
                  (so a full run takes about four hours). Key from serpapi.com in .env.

  --via scholarly direct scraping of profile pages from this machine. Kept for
                  spot checks. Not viable for full runs: Scholar allowed ~45 fetches
                  from one address on 2026-09-14 and then blocked it for 8+ hours.

    python -m pipeline.collect_scholar --limit 20                # test
    python -m pipeline.collect_scholar                           # full run, resumable

Scholar has no API. This scrapes public profile pages one request per person,
so run it from a residential or university connection, never from a cloud
runner. Scholar tolerates roughly 40 to 50 profile fetches in a burst before
it starts refusing for a while, so the defaults are slow and jittered
(--sleep 20 plus up to --jitter 20 seconds). When fetches start failing the
collector does not quit; it waits --cooldown minutes and tries again,
giving up only after --max-wait hours without a success. Output goes to
data/snapshots/google_scholar/<today>.csv; re-running the same day resumes,
skipping ids already written. Plan on an overnight run for all ~800 profiles.

Beyond totals and h-index, each profile yields citations received per
calendar year (cites_per_year), the verified email domain, and the
affiliation string; all are kept in raw_json for trend charts and
move detection.
"""

from __future__ import annotations

import argparse
import csv
import random
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from . import ROSTER_DIR, SNAPSHOT_DIR
from .openalex import _load_dotenv

_load_dotenv()

FIELDS = [
    "person_id", "source", "collected_at", "total_citations", "h_index", "i10_index",
    "citations_5yr", "h_index_5yr", "works_count", "raw_json",
]
FAILURES_BEFORE_COOLDOWN = 3


def load_people() -> tuple[list[str], list[dict]]:
    with (ROSTER_DIR / "person.csv").open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames), list(r)


def save_people(fields: list[str], rows: list[dict]) -> None:
    with (ROSTER_DIR / "person.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def already_done(path: Path, window_days: int = 7) -> set[str]:
    """People collected in this file or in any Scholar snapshot within the last
    window_days. A run that spans several days (rate limits, budgets) should
    not fetch the same person twice."""
    from datetime import timedelta
    run_date = date.fromisoformat(path.stem)
    done: set[str] = set()
    for other in path.parent.glob("*.csv"):
        try:
            d = date.fromisoformat(other.stem)
        except ValueError:
            continue
        if 0 <= (run_date - d).days <= window_days:
            with other.open(newline="", encoding="utf-8") as f:
                done |= {r["person_id"] for r in csv.DictReader(f) if r["total_citations"]}
    return done


def fetch_scholarly(scholarly, scholar_id: str) -> dict:
    author = scholarly.search_author_id(scholar_id)
    author = scholarly.fill(author, sections=["basics", "indices", "counts"])
    return author


def fetch_serpapi(scholar_id: str, key: str) -> dict:
    """Return the same dict shape scholarly produces, from SerpApi's JSON."""
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(
        {"engine": "google_scholar_author", "author_id": scholar_id, "hl": "en", "api_key": key})
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("error"):
        raise RuntimeError(data["error"])
    author = data.get("author") or {}
    if not author.get("name") and not (data.get("cited_by") or {}).get("table"):
        # Stale or renumbered id: SerpApi returns an empty profile instead of following the redirect.
        raise RuntimeError("empty profile from SerpApi; the Scholar id may have changed")
    table = (data.get("cited_by") or {}).get("table") or []
    stats = {}
    for entry in table:
        for k, v in entry.items():          # {'citations': {'all': 4555, 'since_2021': 3198}}
            stats[k] = v
    graph = (data.get("cited_by") or {}).get("graph") or []
    email = author.get("email") or ""      # 'Verified email at utexas.edu'
    domain = "@" + email.split(" at ", 1)[1].strip() if " at " in email else None
    return {
        "scholar_id": data.get("search_parameters", {}).get("author_id", scholar_id),
        "name": author.get("name"),
        "affiliation": author.get("affiliations"),
        "email_domain": domain,
        "interests": [i.get("title") for i in author.get("interests") or []],
        "homepage": author.get("website"),
        "citedby": (stats.get("citations") or {}).get("all"),
        "citedby5y": next((v for k, v in (stats.get("citations") or {}).items() if k.startswith("since")), None),
        "hindex": (stats.get("h_index") or {}).get("all"),
        "hindex5y": next((v for k, v in (stats.get("h_index") or {}).items() if k.startswith("since")), None),
        "i10index": (stats.get("i10_index") or {}).get("all"),
        "i10index5y": next((v for k, v in (stats.get("i10_index") or {}).items() if k.startswith("since")), None),
        "cites_per_year": {g["year"]: g.get("citations", 0) for g in graph if "year" in g},
    }


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sleep", type=float, default=20.0, help="base seconds between requests")
    ap.add_argument("--jitter", type=float, default=20.0, help="extra random seconds added to each pause")
    ap.add_argument("--cooldown", type=float, default=20.0, help="minutes to wait after repeated failures")
    ap.add_argument("--max-wait", type=float, default=3.0, help="hours without a success before giving up")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--ids", nargs="*", help="only these Scholar ids")
    ap.add_argument("--via", choices=["serpapi", "scholarly"],
                    default="serpapi" if os.environ.get("SERPAPI_KEY") else "scholarly")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # progress lines show up in logs as they happen

    if args.via == "serpapi":
        key = os.environ.get("SERPAPI_KEY")
        if not key:
            sys.exit("SERPAPI_KEY is not set (put it in .env)")
        fetch = lambda sid: fetch_serpapi(sid, key)
        if args.sleep == 20.0 and args.jitter == 20.0:
            # SerpApi's paid plans cap searches at 200 per hour (429 beyond that);
            # 18 s spacing keeps a run just under the cap with no failures.
            args.sleep, args.jitter = 18.0, 0.0
    else:
        try:
            from scholarly import scholarly
        except ImportError as e:
            sys.exit(f'cannot import scholarly ({e}); run  pip install -e ".[scholar]"')
        fetch = lambda sid: fetch_scholarly(scholarly, sid)
    print(f"backend: {args.via}")

    fields, everyone = load_people()
    people = [p for p in everyone if p["google_scholar_id"]]
    if args.ids:
        people = [p for p in people if p["google_scholar_id"] in set(args.ids)]
    out = SNAPSHOT_DIR / "google_scholar" / f"{args.date}.csv"
    done = already_done(out)
    todo = [p for p in people if p["person_id"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(people)} people with Scholar ids, {len(done)} collected in the last 7 days, {len(todo)} to fetch")

    out.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out.exists()
    ok = failed = consecutive = 0
    redirected: dict[str, str] = {}   # person_id -> new Scholar id
    retry: list[dict] = []            # people skipped during a cooldown; retried once at the end
    t0 = time.monotonic()
    last_success = t0
    with out.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        queue = list(todo)
        i = 0
        while i < len(queue):
            p = queue[i]
            i += 1
            if i == len(todo) + 1 and retry:
                queue.extend(retry)   # one more pass over the people skipped during cooldowns
                retry = []
            try:
                a = fetch(p["google_scholar_id"])
                seen = a.get("scholar_id")
                if seen and seen != p["google_scholar_id"]:
                    # Google merged or renumbered the profile; remember the new id.
                    redirected[p["person_id"]] = seen
                raw = {k: a.get(k) for k in ("scholar_id", "name", "affiliation", "email_domain", "interests", "homepage",
                                             "citedby", "citedby5y", "hindex", "hindex5y", "i10index",
                                             "i10index5y", "cites_per_year")}
                w.writerow({
                    "person_id": p["person_id"], "source": "google_scholar", "collected_at": args.date,
                    "total_citations": a.get("citedby"), "h_index": a.get("hindex"),
                    "i10_index": a.get("i10index"), "citations_5yr": a.get("citedby5y"),
                    "h_index_5yr": a.get("hindex5y"), "works_count": None,
                    "raw_json": json.dumps(raw, ensure_ascii=False),
                })
                f.flush()
                ok += 1
                consecutive = 0
                last_success = time.monotonic()
                print(f"  [{i}/{len(queue)}] {p['display_name']}: {a.get('citedby')} cites, h={a.get('hindex')}, "
                      f"{a.get('email_domain') or 'no verified email'}")
            except Exception as e:
                failed += 1
                consecutive += 1
                print(f"  [{i}/{len(queue)}] {p['display_name']}: FAILED ({type(e).__name__}: {e})", file=sys.stderr)
                if consecutive >= FAILURES_BEFORE_COOLDOWN:
                    waited = (time.monotonic() - last_success) / 3600
                    if waited > args.max_wait:
                        print(f"no successful fetch for {waited:.1f} h; giving up. Re-run to resume.", file=sys.stderr)
                        break
                    print(f"  {consecutive} failures in a row; Scholar is throttling. "
                          f"Cooling down {args.cooldown:.0f} min ({waited:.1f} h since last success).", file=sys.stderr)
                    time.sleep(args.cooldown * 60)
                    consecutive = 0
                    retry.append(p)   # come back to this person at the end
                    continue
            if i < len(queue):
                time.sleep(args.sleep + random.uniform(0, args.jitter))

    meta = {"trigger": "manual", "notes": f"{args.via} collector; {ok} ok, {failed} failed, "
                                          f"{(time.monotonic() - t0) / 60:.1f} min"}
    out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    if redirected:
        # Re-read the roster now: another process (the matcher) may have written
        # to it during this long run. Only the Scholar id column is ours to change.
        fields, everyone = load_people()
        taken = {q["google_scholar_id"] for q in everyone} - {p["google_scholar_id"] for p in everyone if p["person_id"] in redirected}
        for q in everyone:
            new = redirected.get(q["person_id"])
            if new and new not in taken:
                print(f"  roster: {q['display_name']} Scholar id {q['google_scholar_id']} -> {new} (profile redirected)")
                q["google_scholar_id"] = new
                taken.add(new)
            elif new:
                print(f"  roster: {q['display_name']} redirects to {new}, which another person already holds; left unchanged", file=sys.stderr)
        save_people(fields, everyone)
    print(f"\ndone: {ok} ok, {failed} failed -> {out}")


if __name__ == "__main__":
    main()
