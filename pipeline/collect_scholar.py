"""Collect Google Scholar profile metrics.

    python -m pipeline.collect_scholar --budget 50      # what the weekly workflow runs
    python -m pipeline.collect_scholar --missing        # ids on the roster with no reading yet
    python -m pipeline.collect_scholar --ids=ID,ID      # these profiles only

Two backends:

  --via serpapi   (the default) SerpApi's google_scholar_author endpoint: parsed
                  JSON, one search per person, runs anywhere including GitHub
                  Actions. The free tier is 250 searches a month and 50 an hour,
                  which is what the weekly rolling run is sized for: --budget N
                  re-reads the N current people whose reading is oldest, and is
                  cut to what the account has left, so a short month ends in a
                  smaller run, not a failed one. Key from serpapi.com in .env.

  --via scholarly direct scraping of profile pages from this machine. Never the
                  default, and not to be run from a home address: Scholar
                  allowed about 45 fetches from one address on 2026-09-14 and
                  then blocked it for 8+ hours, and in November 2026 it flagged
                  the address outright. Kept for the record.

When fetches start failing the collector does not quit; it waits --cooldown
minutes and tries again, giving up only after --max-wait hours without a
success, and at once when the account has no searches left. Everyone who
failed for a passing reason is tried once more at the end. Output goes to
data/snapshots/google_scholar/<today>.csv; re-running the same day resumes,
skipping people already read in the last week.

Beyond totals and h-index, each profile yields citations received per
calendar year (cites_per_year), the verified email domain, and the
affiliation string; all are kept in raw_json for trend charts and
move detection.

A Scholar id that stops resolving (the profile was deleted or re-created under
a new id; Yiyi He's was, 2026-11-05) is reported and written into the run's
.meta.json as stale_ids, but only after a second, uncached look agrees: the
discovery pass clears a dead id for good when it finds no replacement, so one
empty answer is not enough. The person keeps their last reading; discovery
(find_scholar_profiles, the first step of the weekly workflow) reads those
metas and looks for the new profile on its own. Nothing here clears the id.
"""

from __future__ import annotations

import argparse
import csv
import random
import json
import re
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
DISCOVERY_FLOOR = 10      # a weekly run cut to fewer searches than this skips discovery (see weekly_plan)
SEARCHES_PER_PROFILE = 2  # the most one profile can cost: the reading, and the second look at an id that came back dead


class PermanentFailure(RuntimeError):
    """This person cannot be collected however long we wait: the recorded
    Scholar id no longer resolves. Skip them, do not back off."""


PROFILE_URL = "https://scholar.google.com/citations?user={id}&hl=en"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def id_is_dead(scholar_id: str) -> bool:
    """One cheap request to tell a deleted profile from a throttled one.

    A fetch failure means either 'this profile is gone' or 'Google is refusing
    us right now', and the two call for opposite responses: skip, or wait. The
    profile page answers directly: 404 for a profile that no longer exists, a
    redirect to /sorry/ when the address is being refused.

    This asks Scholar itself, from this machine, so it is used only by the
    scholarly backend, which is already doing that. A SerpApi run never calls
    it: a SerpApi error says nothing about the profile, and the run may be on
    an address Google must not see (2026-11)."""
    req = urllib.request.Request(PROFILE_URL.format(id=scholar_id),
                                 headers={"User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return "/sorry/" not in resp.geturl() and resp.status == 404
    except urllib.error.HTTPError as e:
        return e.code == 404
    except Exception:
        return False        # cannot tell, so treat it as transient


def run_meta(via: str, ok: int, failed: int, minutes: float, stale: list[tuple],
             attempted: int | None = None, rolling: int | None = None, stopped: str | None = None) -> dict:
    """The run's record. Dead ids go in by name so the discovery run can find
    them without anyone reading the log: on GitHub the stderr listing is gone
    once the run page scrolls away. The counts are fields, not just prose, and
    a rolling run carries its budget: pipeline/health_check.py reads them to
    tell a short run from a full one."""
    meta = {"trigger": "schedule" if rolling else "manual",
            "notes": f"{via} collector; {ok} ok, {failed} failed, {minutes:.1f} min",
            "ok": ok, "failed": failed, "attempted": ok + failed if attempted is None else attempted}
    if rolling:
        meta["rolling"] = rolling
    if stopped:
        meta["stopped"] = stopped       # why the run ended before its list did
    if stale:
        meta["stale_ids"] = [{"person_id": pid, "name": name, "scholar_id": sid} for pid, name, sid in stale]
    return meta


def merged_meta(old: dict | None, new: dict) -> dict:
    """Two runs can land on one date: the Monday collection, then a one-profile
    fetch for someone whose profile was found that afternoon. The second used
    to replace the first's record (2026-10-12 reads "1 ok" for a day that
    collected 47), taking its dead ids with it. A rolling run's record is kept
    unless another rolling run replaces it, and dead ids accumulate."""
    if not old:
        return new
    keep = dict(old) if old.get("rolling") and not new.get("rolling") else dict(new)
    if keep is not new and keep.get("notes") != new.get("notes"):
        keep["notes"] = f"{old.get('notes', '')}; later the same day: {new.get('notes', '')}"
    dead = {e["scholar_id"]: e for e in (old.get("stale_ids") or []) + (new.get("stale_ids") or [])}
    if dead:
        keep["stale_ids"] = list(dead.values())
    return keep


def load_people() -> tuple[list[str], list[dict]]:
    with (ROSTER_DIR / "person.csv").open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames), list(r)


def save_people(fields: list[str], rows: list[dict]) -> None:
    with (ROSTER_DIR / "person.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def all_snapshot_rows() -> list[dict]:
    rows = []
    for f in sorted((SNAPSHOT_DIR / "google_scholar").glob("*.csv")):
        with f.open(newline="", encoding="utf-8") as fh:
            rows.extend(csv.DictReader(fh))
    return rows


def ids_missing_readings(people: list[dict], snapshot_rows: list[dict]) -> set[str]:
    """Roster Scholar ids with no snapshot row behind them.

    A row counts for an id if its raw_json names that id, or names none: one
    early collection day stored no id, and those people are not missing
    anything. The migrated workbook rows name theirs in the GoogleScholar URL,
    and that counts too: on 2026-10-12 Yan Song's replacement profile, and
    three profiles found after their ids died, were skipped as "already read"
    because the workbook row for the old id had no scholar_id key. A person
    whose id changed -- a namesake's cleared, the real profile found -- has
    rows for the old id only, so the new one is fetched. This is what turns a
    discovery find into a figure.
    """
    seen: dict[str, set] = {}
    for r in snapshot_rows:
        try:
            raw = json.loads(r.get("raw_json") or "{}")
        except ValueError:
            raw = {}
        sid = raw.get("scholar_id")
        if not sid:
            m = re.search(r"user=([\w-]+)", ((raw.get("workbook_row") or {}).get("GoogleScholar") or ""))
            sid = m.group(1) if m else None
        seen.setdefault(r["person_id"], set()).add(sid)
    out = set()
    for p in people:
        gid = (p.get("google_scholar_id") or "").strip()
        if gid and not ({gid, None} & seen.get(p["person_id"], set())):
            out.add(gid)
    return out


def current_ids() -> set[str]:
    """person_ids with an open primary appointment (any program kind)."""
    with (ROSTER_DIR / "affiliation.csv").open(newline="", encoding="utf-8") as f:
        return {a["person_id"] for a in csv.DictReader(f) if not a["end_date"] and a["is_primary"] == "1"}


def oldest_first(people: list[dict], snapshot_rows: list[dict], current: set[str], budget: int) -> list[dict]:
    """The `budget` current people whose latest Scholar reading is oldest, never-read first.

    Collection is rolling (from 2026-10): SerpApi's free tier allows 250 searches
    a month, so up to fifty a week are spent, oldest reading first, after
    discovery has taken its share. With some 840 profiles and about forty
    re-reads a week, every profile comes round in four to five months. People
    who have left are not re-read; their last figures stay in the data.
    """
    last: dict[str, str] = {}
    for r in snapshot_rows:
        if r.get("total_citations"):
            last[r["person_id"]] = max(last.get(r["person_id"], ""), r["collected_at"])
    pool = [p for p in people if p["person_id"] in current]
    pool.sort(key=lambda p: (last.get(p["person_id"], ""), int(p["person_id"])))
    return pool[:budget]


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


def searches_left(key: str) -> int | None:
    """How many searches the SerpApi account has left, or None if it will not
    say. The lookup is free. Errors carry no URL: the key is in it."""
    try:
        url = "https://serpapi.com/account.json?" + urllib.parse.urlencode({"api_key": key})
        with urllib.request.urlopen(url, timeout=30) as resp:
            left = json.loads(resp.read().decode("utf-8")).get("total_searches_left")
        return None if left is None else int(left)
    except Exception:
        return None


def weekly_plan(total: int, discovery: int, left: int | None) -> tuple[int, int]:
    """(searches the whole weekly run may spend, of which discovery may spend).

    Fifty searches on each of five Mondays is the entire free tier, so any
    month with five Mondays, or one lookup made by hand, ended in a run that
    asked for more than the account had: it failed part-way and raised an
    alarm (2026-11-16). The run is cut to what is left instead, and discovery
    keeps the share it was asked for. A run cut below DISCOVERY_FLOOR gives
    discovery nothing: it finishes the person it has started, so it can go
    over its cap by four searches, and on a small balance that would leave the
    re-read none. With enough left, or when SerpApi will not say (`left` is
    None), the plan stands exactly as asked."""
    if left is None or left >= total:
        return total, min(discovery, total)
    cut = max(0, left)
    return cut, (cut * discovery // total if total and cut >= DISCOVERY_FLOOR else 0)


def read_confirmed(fetch, scholar_id: str, pause: float) -> dict:
    """One profile's reading. PermanentFailure leaves here only when two looks
    agree that the id is dead.

    SerpApi answers a dead id with an empty profile, and discovery acts on
    that for good: it searches for a replacement and, finding none, clears the
    id and never accepts it again. One empty answer is too little for that, so
    the profile is asked for once more, uncached (SerpApi serves a repeat of
    the same search from its cache for an hour). A second empty answer raises
    PermanentFailure; anything else that goes wrong on the second look is an
    ordinary failure, and the person is tried again later."""
    try:
        return fetch(scholar_id)
    except PermanentFailure:
        time.sleep(pause)
        return fetch(scholar_id, fresh=True)


def fetch_serpapi(scholar_id: str, key: str, fresh: bool = False) -> dict:
    """Return the same dict shape scholarly produces, from SerpApi's JSON.
    `fresh` asks SerpApi to look again instead of answering from its cache."""
    params = {"engine": "google_scholar_author", "author_id": scholar_id, "hl": "en", "api_key": key}
    if fresh:
        params["no_cache"] = "true"
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("error"):
        raise RuntimeError(data["error"])
    author = data.get("author") or {}
    if not author.get("name") and not (data.get("cited_by") or {}).get("table"):
        # Stale or renumbered id: SerpApi returns an empty profile instead of following the redirect.
        raise PermanentFailure("empty profile; this Scholar id no longer resolves")
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
    ap.add_argument("--cooldown", type=float, default=20.0,
                    help="minutes to wait after repeated failures; 0 to fail fast instead. "
                         "Forced to 0 for a short or id-targeted run, where waiting out a rate "
                         "limit cannot be worth it")
    ap.add_argument("--max-wait", type=float, default=3.0, help="hours without a success before giving up")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--ids", metavar="ID,ID,...", default="",
                    help="only these Scholar ids; comma-separated, and written as --ids=... "
                         "because a Scholar id can start with '-'")
    ap.add_argument("--budget", type=int, metavar="N",
                    help="re-read the N current people whose latest reading is oldest (the weekly rolling run)")
    ap.add_argument("--missing", action="store_true",
                    help="only people whose roster id has never been fetched: a profile discovery "
                         "accepted, or one set by hand")
    ap.add_argument("--via", choices=["serpapi", "scholarly"], default="serpapi",
                    help="scholarly fetches Scholar directly from this machine and has to be asked for: "
                         "a missing key must not turn a run into scraping from a home address")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # progress lines show up in logs as they happen

    if args.via == "serpapi":
        key = os.environ.get("SERPAPI_KEY")
        if not key:
            sys.exit("SERPAPI_KEY is not set (put it in .env)")
        spent = [0]     # SerpApi searches this run has made, successful or not

        def fetch(sid, fresh=False):
            # The first look is the plain call; only the confirming look of a dead id passes `fresh`.
            spent[0] += 1
            return fetch_serpapi(sid, key, fresh=True) if fresh else fetch_serpapi(sid, key)
        left = lambda: searches_left(key)
        if args.sleep == 20.0 and args.jitter == 20.0:
            # SerpApi's paid plans cap searches at 200 per hour (429 beyond that);
            # 18 s spacing keeps a run just under the cap with no failures.
            args.sleep, args.jitter = 18.0, 0.0
    else:
        try:
            from scholarly import scholarly
        except ImportError as e:
            sys.exit(f'cannot import scholarly ({e}); run  pip install -e ".[scholar]"')
        print("note: --via scholarly requests Scholar pages from this machine's address", file=sys.stderr)
        spent = [0]
        fetch = lambda sid, fresh=False: fetch_scholarly(scholarly, sid)
        left = lambda: None
    print(f"backend: {args.via}")

    fields, everyone = load_people()
    people = [p for p in everyone if p["google_scholar_id"]]
    wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
    if args.missing:
        wanted |= ids_missing_readings(people, all_snapshot_rows())
        if not wanted:
            print("every roster id has a reading; nothing to fetch")
            return
        print(f"{len(wanted)} roster id(s) with no reading yet")
    if wanted:
        people = [p for p in people if p["google_scholar_id"] in wanted]
        missing = wanted - {p["google_scholar_id"] for p in people}
        if missing:
            print(f"  note: no roster entry holds {', '.join(sorted(missing))}", file=sys.stderr)
    asked = args.budget
    allowance = None      # searches the account had when a rolling run began; None when unknown or not rolling
    if args.budget:
        have = allowance = left()
        if have is not None and have < args.budget:
            # Read what the account can pay for and finish cleanly, rather than fail part-way.
            print(f"SerpApi has {have} searches left; this run reads {have} profiles, not {args.budget}",
                  file=sys.stderr)
            if have == 0:
                sys.exit("the rolling run read nothing: SerpApi has no searches left this period")
            args.budget = have
        people = oldest_first(people, all_snapshot_rows(), current_ids(), args.budget)
        print(f"rolling run: {len(people)} people, oldest reading first")
    out = SNAPSHOT_DIR / "google_scholar" / f"{args.date}.csv"
    done = set() if wanted else already_done(out)
    todo = [p for p in people if p["person_id"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    if wanted or len(todo) <= 10:
        # A handful of people is a targeted repair, not a collection run: report
        # failures and exit rather than sitting in rate-limit cooldowns.
        args.cooldown = 0
    print(f"{len(people)} people with Scholar ids, {len(done)} collected in the last 7 days, "
          f"{len(todo)} to fetch" + ("  (failing fast, no cooldowns)" if args.cooldown == 0 else ""))

    out.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out.exists()
    ok = failed = consecutive = 0
    redirected: dict[str, str] = {}   # person_id -> new Scholar id
    retry: list[dict] = []            # people whose fetch failed for a passing reason; tried once more at the end
    second_pass = False
    stale: list[tuple] = []           # (person_id, name, id) whose Scholar id no longer resolves
    stopped = None                    # set when the run ends before its list does
    tried = 0                         # people the first pass got to
    ran_out = False                   # the run ended because the account's searches were spent
    t0 = time.monotonic()
    last_success = t0
    with out.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        queue = list(todo)
        i = 0
        while True:
            if i >= len(queue):
                # The end of the list. Until 2026-11 the retry pass was tested for one
                # step too late, after the loop had already ended, so nobody was retried.
                if second_pass or not retry:
                    break
                print(f"  trying {len(retry)} failed profile(s) once more")
                time.sleep(args.sleep)
                queue.extend(retry)
                retry, second_pass = [], True
            if allowance is not None and allowance - spent[0] < SEARCHES_PER_PROFILE:
                # The budget counts profiles and the account counts searches: a dead id costs
                # two, and so does a failure SerpApi charges for. Stop while the next profile
                # can still be paid for, instead of ending the run on a refusal and an alarm.
                print(f"SerpApi's searches are spent ({spent[0]} used of {allowance}); "
                      f"{len(todo) - tried} profile(s) wait for next week", file=sys.stderr)
                ran_out = True
                break
            p = queue[i]
            i += 1
            tried += not second_pass
            try:
                a = read_confirmed(fetch, p["google_scholar_id"], args.sleep)
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
                failed -= second_pass       # counted as failed on the first pass; it is not any more
                consecutive = 0
                last_success = time.monotonic()
                print(f"  [{i}/{len(queue)}] {p['display_name']}: {a.get('citedby')} cites, h={a.get('hindex')}, "
                      f"{a.get('email_domain') or 'no verified email'}")
            except PermanentFailure as e:
                # A dead id is not a rate limit: record it and move on.
                failed += not second_pass
                stale.append((p["person_id"], p["display_name"], p["google_scholar_id"]))
                print(f"  [{i}/{len(queue)}] {p['display_name']}: STALE ID "
                      f"{p['google_scholar_id']} ({e}; a second look agreed)", file=sys.stderr)
                if i < len(queue) or retry:
                    time.sleep(args.sleep)
                continue
            except Exception as e:
                failed += not second_pass
                # Only the scholarly backend asks Scholar directly, so only it may ask this.
                if args.via == "scholarly" and id_is_dead(p["google_scholar_id"]):
                    stale.append((p["person_id"], p["display_name"], p["google_scholar_id"]))
                    print(f"  [{i}/{len(queue)}] {p['display_name']}: DEAD PROFILE "
                          f"{p['google_scholar_id']} (page returns 404)", file=sys.stderr)
                    if i < len(queue) or retry:
                        time.sleep(args.sleep)
                    continue
                consecutive += 1
                if not second_pass:
                    retry.append(p)   # come back to this person at the end
                print(f"  [{i}/{len(queue)}] {p['display_name']}: FAILED ({type(e).__name__}: {e})", file=sys.stderr)
                if left() == 0:
                    # An empty account answers 429 just as the hourly limit does, and no
                    # cooldown brings searches back: on 2026-11-16 a run waited out its
                    # whole time limit this way and its eight readings were never saved.
                    stopped = "SerpApi has no searches left this period"
                    print(f"{stopped}; stopping with {len(todo) - ok} of {len(todo)} profiles unread. "
                          "They keep their previous reading and come round again as the oldest.", file=sys.stderr)
                    break
                if consecutive >= FAILURES_BEFORE_COOLDOWN and args.cooldown > 0:
                    waited = (time.monotonic() - last_success) / 3600
                    if waited > args.max_wait:
                        print(f"no successful fetch for {waited:.1f} h; giving up. Re-run to resume.", file=sys.stderr)
                        break
                    who = ("SerpApi is refusing requests (its hourly limit, most likely)" if args.via == "serpapi"
                           else "Scholar is blocking this address")
                    print(f"  {consecutive} failures in a row; {who}. "
                          f"Cooling down {args.cooldown:.0f} min ({waited:.1f} h since last success).", file=sys.stderr)
                    time.sleep(args.cooldown * 60)
                    consecutive = 0
                    continue
            if i < len(queue):
                time.sleep(args.sleep + random.uniform(0, args.jitter))

    if ok == 0 and new_file:
        # Nothing was collected, so leave no empty snapshot behind: an
        # header-only file would become a collection_run of zero rows.
        out.unlink(missing_ok=True)
        print("no profiles collected; removed the empty snapshot file", file=sys.stderr)
    else:
        # A run that ended because the searches were spent attempted the people it reached, and
        # is a smaller run, not a short one. Any other early end set out to read everyone.
        meta = run_meta(args.via, ok, failed, (time.monotonic() - t0) / 60, stale,
                        attempted=tried if ran_out else len(todo), rolling=args.budget, stopped=stopped)
        if asked and asked != args.budget:
            meta["budget_asked"] = asked    # the run was cut to what the SerpApi account had left
        meta_path = out.with_suffix(".meta.json")
        try:
            earlier = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            earlier = None
        meta_path.write_text(json.dumps(merged_meta(earlier, meta), indent=2) + "\n")
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
    if stale:
        print(f"\n{len(stale)} Scholar ids no longer resolve; recorded in {out.with_suffix('.meta.json').name}. "
              f"Next week's discovery run will look for the new profiles first; to search now:\n"
              f"  python -m pipeline.find_scholar_profiles --stale={','.join(s[2] for s in stale)}",
              file=sys.stderr)
        for _, name, sid in stale:
            print(f"    {name}  {sid}", file=sys.stderr)
    print(f"\ndone: {ok} ok, {failed} failed, {len(stale)} stale ids -> {out}")
    if args.budget and todo and ok == 0:
        # A collection run that read nobody leaves no snapshot to judge it by, so say so here.
        sys.exit(f"the rolling run read none of {len(todo)} profiles" + (f": {stopped}" if stopped else ""))


if __name__ == "__main__":
    main()
