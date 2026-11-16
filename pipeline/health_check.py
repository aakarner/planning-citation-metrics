"""Sanity checks over what the scheduled jobs leave behind, raised as GitHub issues.

    python -m pipeline.health_check                  # print what is wrong; exit 1 if anything is
    python -m pipeline.health_check --sync-issues    # what .github/workflows/health-check.yml runs
    python -m pipeline.health_check --expect scholar # a weekly run should just have finished
    python -m pipeline.health_check --sync-issues --self-test   # raise a test alarm, to see it arrive

Every job here can go wrong while its workflow run stays green. The OpenAlex
run of 2026-11-01 lost 344 of 1,050 people to an exhausted budget and summed
136 merged records several times over; two discovery batches in September
recorded Scholar's "Verifying your request" page as "no profile" for 59 people;
the weekly Scholar run of 2026-10-12 committed its readings and the site never
rebuilt. Each left a trace in the repository's own records, and nothing read
them. This does, once a day and after each collection.

A rule that trips becomes a Finding: a key (which rule), an episode (which
occurrence of it), a title and the evidence. With --sync-issues each finding
is one open GitHub issue labelled `health-check`, assigned to the repository
owner so GitHub notifies them:

  * a new finding opens an issue, and the run exits 1 so it also shows red;
  * the same finding on later days only refreshes the issue text: one
    notification per problem, not one per day;
  * a new episode of a problem whose issue is still open is a comment on it;
  * when the rule passes again the issue is closed with a note;
  * an issue closed by hand stays closed for that episode, which is how to
    say "seen it".

A check that could not be made (no SerpApi key here) neither raises nor closes
anything. Notices (alarm=False) open an issue but do
not fail the run. What none of this can see is a plausible wrong answer, such
as a namesake's profile: that still takes a person writing in.

Profile discovery runs inside the weekly Scholar workflow and can come back
empty for good reasons and bad ones, so each run folds what it saw into
data/review/discovery_status.json (see pipeline/discovery_status.py); the
checks here read that.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from . import REPO_ROOT, ROSTER_DIR, SNAPSHOT_DIR
from .match_openalex import REVIEW_DIR

SITE = "https://scholarmetrics.com"
LABEL = "health-check"
DISCOVERY_STATUS = REVIEW_DIR / "discovery_status.json"   # written by find_scholar_profiles --status

WEEKLY_BUDGET = 50          # profiles the Monday Scholar run re-reads
ROLLING_MIN = 30            # an older run record with this many attempts was a collection run, not a repair
SHORT_RUN = 0.9             # a run that read under this share of what it set out to read is short
REPEAT_MIN_CITES = 20       # two records of one person with identical figures above this are one record
SHARED_MIN_CITES = 50       # two people with identical figures above this are one record
AUDIT_WINDOW_DAYS = 14      # how long an audit withdrawal stays raised
SILENT_DAYS = 8             # people are waiting and discovery (weekly) has not reported for this long
EMPTY_BATCH_MIN = 6         # a batch at least this large ...
EMPTY_BATCH_SHARE = 0.8     # ... in which this share of searches showed no linked author at all


@dataclass(frozen=True)
class Finding:
    key: str            # which rule
    episode: str        # which occurrence of it; no spaces
    title: str
    detail: str         # markdown
    alarm: bool = True  # False: a notice, raised but the run stays green


# ---------------------------------------------------------------- run records

COUNTS = re.compile(r"([\d,]+) ok, ([\d,]+) failed")


def run_counts(meta: dict) -> tuple[int, int] | None:
    """(ok, failed) from a run record: the fields, or the note older records carry."""
    if "ok" in meta and "failed" in meta:
        return int(meta["ok"]), int(meta["failed"])
    m = COUNTS.search(meta.get("notes") or "")
    return (int(m[1].replace(",", "")), int(m[2].replace(",", ""))) if m else None


def last_due(today: date, weekday: int = 0) -> date:
    """The most recent Monday, today included."""
    return today - timedelta(days=(today.weekday() - weekday) % 7)


def latest(runs: dict[str, dict]) -> tuple[str, dict] | tuple[None, None]:
    return max(runs.items()) if runs else (None, None)


def rolling_runs(metas: dict[str, dict]) -> dict[str, dict]:
    """The Scholar run records that were collection runs. New records say so
    (`rolling`); older ones are told from one-profile repairs by their size."""
    out = {}
    for d, m in metas.items():
        counts = run_counts(m)
        if m.get("rolling") or ("ok" not in m and counts and sum(counts) >= ROLLING_MIN):
            out[d] = m
    return out


def full_openalex_runs(metas: dict[str, dict]) -> dict[str, dict]:
    """OpenAlex run records for everyone, not a targeted fetch of people just matched."""
    out = {}
    for d, m in metas.items():
        targeted = m["targeted"] if "targeted" in m else "(targeted)" in (m.get("notes") or "")
        if not targeted and run_counts(m):
            out[d] = m
    return out


# --------------------------------------------------------------------- checks

SCHOLAR_KEYS = {"scholar-overdue", "scholar-short-run", "scholar-dead-ids"}


def check_scholar(metas: dict[str, dict], today: date, expect: bool = False) -> list[Finding]:
    """The weekly rolling collection: did it run, and did it read what it set out to."""
    out = []
    d, m = latest(rolling_runs(metas))
    due = last_due(today)
    if (d is None or d < due.isoformat()) and (today > due or expect):
        out.append(Finding(
            "scholar-overdue", d or "never",
            f"The weekly Scholar collection has not run since {d or 'records began'}",
            f"A run was due on Monday {due.isoformat()} and no snapshot from it is in "
            f"`data/snapshots/google_scholar/`. The last collection run on file is {d or 'none'}.\n\n"
            "Usual causes: the workflow was skipped or disabled, the `SERPAPI_KEY` secret is missing, "
            "or every request failed (a run that reads nothing leaves no snapshot). "
            "See the latest run of *Collect Google Scholar metrics (weekly, rolling)* under Actions."))
    if m is not None:
        ok, failed = run_counts(m)
        attempted = int(m.get("attempted") or ok + failed)
        dead = len(m.get("stale_ids") or [])
        errors = failed - dead
        if attempted and (ok < SHORT_RUN * attempted or errors >= 3):
            out.append(Finding(
                "scholar-short-run", d,
                f"The Scholar collection of {d} read {ok} of {attempted} profiles",
                f"{ok} read, {errors} failed for reasons other than a dead profile id"
                + (f", {dead} dead ids" if dead else "") + f". Run note: \"{m.get('notes', '')}\"."
                + (f" The run stopped early: {m['stopped']}." if m.get("stopped") else "") + "\n\n"
                "Usual causes: the SerpApi monthly quota ran out part-way, or SerpApi was rate-limiting. "
                "The people missed keep their previous reading and come round again as the oldest."))
    return out


def check_dead_ids(persons: list[dict], metas: dict[str, dict]) -> list[Finding]:
    """A notice, not an alarm: the next discovery run looks for the replacement itself."""
    reported: dict[str, str] = {}           # scholar id -> newest run that found it dead
    for d, m in sorted(metas.items()):
        for e in m.get("stale_ids") or []:
            if e.get("scholar_id"):
                reported[e["scholar_id"]] = d
    held = [(p["display_name"], p["google_scholar_id"], reported[p["google_scholar_id"]])
            for p in persons if p.get("google_scholar_id") in reported]
    if not held:
        return []
    lines = "\n".join(f"- {name}: `{sid}` (found dead {d})" for name, sid, d in held)
    return [Finding(
        "scholar-dead-ids", max(d for _, _, d in held),
        f"{len(held)} Scholar profile id{'s' if len(held) != 1 else ''} no longer resolve{'s' if len(held) == 1 else ''}",
        f"{lines}\n\nThe next weekly discovery run searches for the replacement profile first; this closes "
        "when the roster holds a new id or the old one is cleared. Nothing to do unless it lingers.",
        alarm=False)]


JOB_NAMES = {"scholar": "Collect Google Scholar metrics (weekly, rolling)",
             "openalex": "Collect OpenAlex metrics"}


def check_job(expect: str, result: str, today: date, explained: bool) -> list[Finding]:
    """How the collection job itself ended, which only the workflow that ran
    it knows. On 2026-11-16 the weekly Scholar job was cancelled at its time
    limit with nothing saved, and the records on file (a small run earlier the
    same day) looked complete. Left to the more specific rule when one of
    those already fires for this collection."""
    if not expect or result in ("", "success") or explained:
        return []
    return [Finding(
        f"{expect}-job", today.isoformat(),
        f"The {'Scholar' if expect == 'scholar' else 'OpenAlex'} collection job of {today.isoformat()} "
        f"ended as '{result}'",
        f"The job did not finish, and the records it left behind show nothing else wrong, so whatever it "
        f"collected may not have been saved. See the latest run of *{JOB_NAMES.get(expect, expect)}* under "
        "Actions for the step that stopped it.\n\n"
        "Usual causes: the job reached its time limit (a collector waiting on a service that was never going "
        "to answer), a step after the collection crashed, or the run was cancelled by hand. This closes after "
        "the next run that finishes.")]


OPENALEX_KEYS = {"openalex-overdue", "openalex-incomplete"}


def check_openalex(metas: dict[str, dict], today: date, expect: bool = False) -> list[Finding]:
    """The monthly collection: did it run, and did it reach everyone."""
    out = []
    d, m = latest(full_openalex_runs(metas))
    due = today.replace(day=1)
    if (d is None or d < due.isoformat()) and (today > due or expect):
        out.append(Finding(
            "openalex-overdue", d or "never",
            f"The monthly OpenAlex collection has not run since {d or 'records began'}",
            f"A run was due on {due.isoformat()} and no full snapshot from it is in "
            f"`data/snapshots/openalex/`. The last full run on file is {d or 'none'}.\n\n"
            "See the latest run of *Collect OpenAlex metrics* under Actions."))
    if m is not None:
        ok, failed = run_counts(m)
        notes = m.get("notes") or ""
        stopped = m.get("stopped") or ("stopped at" in notes)
        if "matched" in m and "collected" in m:
            size, missing = int(m["matched"]), int(m["matched"]) - int(m["collected"])
        else:
            size, missing = ok + failed, failed
        if stopped or missing >= max(5, 0.005 * size):
            out.append(Finding(
                "openalex-incomplete", d,
                f"The OpenAlex collection of {d} missed {missing:,} of {size:,} people",
                f"Run note: \"{notes}\".\n\n"
                "The people missed have no reading from this run, so the site shows their earlier figure. "
                "Usual cause: the API budget ran out (check the `OPENALEX_API_KEY` secret is set). "
                "Running the workflow again the same day picks up only the people missed."))
    return out


FIGURE_KEYS = {"openalex-repeated-records"}


def check_openalex_figures(snapshot_date: str, rows: list[dict], names: dict[str, str]) -> list[Finding]:
    """One OpenAlex record counted more than once: within a person (their ids
    merged into one record and each was summed), or under two people."""
    inside, holders, totals = [], defaultdict(set), defaultdict(set)
    for r in rows:
        who = names.get(r["person_id"], f"person {r['person_id']}")
        try:
            raw = json.loads(r.get("raw_json") or "{}")
        except ValueError:
            continue
        figures = Counter((x.get("cited_by_count"), x.get("works_count"), x.get("h_index"))
                          for x in raw.get("records") or []
                          if (x.get("cited_by_count") or 0) >= REPEAT_MIN_CITES)
        for (cites, works, _), n in figures.items():
            if n > 1:
                inside.append(f"- {who}: {n} records each with {cites:,} citations and {works:,} works; "
                              f"published total {int(r.get('total_citations') or 0):,}")
        for aid in raw.get("openalex_author_ids") or [raw.get("openalex_author_id")]:
            if aid:
                holders[aid].add(who)
        if int(r.get("total_citations") or 0) >= SHARED_MIN_CITES:
            totals[(r.get("total_citations"), r.get("h_index"), r.get("works_count"))].add(who)
    across = [f"- record `{aid}` is counted for {', '.join(sorted(w))}" for aid, w in sorted(holders.items()) if len(w) > 1]
    across += [f"- {', '.join(sorted(w))} have identical figures ({int(c):,} citations, h {h}, {wk} works)"
               for (c, h, wk), w in sorted(totals.items()) if len(w) > 1]
    if not inside and not across:
        return []
    shown = (inside + across)[:25]
    more = len(inside) + len(across) - len(shown)
    return [Finding(
        "openalex-repeated-records", snapshot_date,
        f"OpenAlex snapshot {snapshot_date}: {len(inside) + len(across)} figures count one record more than once",
        "\n".join(shown) + (f"\n- and {more} more" if more else "") + "\n\n"
        "Identical figures on two records are one record reached by two ids. Within a person the total is "
        "inflated by the repeats; across people, one of them holds someone else's record.")]


AUDIT_KEYS = {"openalex-audit-withdrawals"}
UNACCEPTED = re.compile(r"un-accepted (\d{4}-\d{2}-\d{2})")


def check_audit(persons: list[dict], review: list[dict], today: date) -> list[Finding]:
    """Matches the audit withdrew recently. Sometimes right (a namesake); on
    2026-11-01 it was seven settled matches thrown out by inflated figures."""
    since = (today - timedelta(days=AUDIT_WINDOW_DAYS)).isoformat()
    hits: dict[str, tuple[str, str]] = {}
    for row in list(persons) + list(review):
        for m in UNACCEPTED.finditer(row.get("notes") or ""):
            if since <= m[1] <= today.isoformat():
                hits[row["person_id"]] = (m[1], row.get("display_name") or f"person {row['person_id']}")
    if not hits:
        return []
    lines = "\n".join(f"- {name} ({d})" for d, name in sorted(hits.values()))
    return [Finding(
        "openalex-audit-withdrawals", max(d for d, _ in hits.values()),
        f"The audit withdrew {len(hits)} accepted OpenAlex match{'es' if len(hits) != 1 else ''}",
        f"{lines}\n\nEach lost their OpenAlex id because the collected citations were far above the figure "
        "already held. That is right for a namesake and wrong when the OpenAlex figure itself was inflated. "
        "The evidence is in each person's notes in `data/roster/person.csv`; a rejected row in "
        "`data/review/identity_candidates.csv` can be flipped back.")]


DISCOVERY_KEYS = {"discovery-silent", "discovery-stalled", "discovery-empty-batch"}


def check_discovery(status: dict | None, queued: int, today: date) -> list[Finding]:
    """Profile discovery, which runs in the weekly Scholar workflow. `status`
    is its record (data/review/discovery_status.json); `queued` is how many
    people the roster says are waiting to be searched, counted here rather
    than taken from the record, so a discovery that never runs is still seen."""
    out = []
    week = (today - timedelta(days=7)).isoformat()
    suspect = [b for b in (status or {}).get("batches") or []
               if int(b.get("searched") or 0) >= EMPTY_BATCH_MIN and b.get("date", "") >= week
               and int(b.get("no_links") or 0) >= EMPTY_BATCH_SHARE * int(b["searched"])]
    if suspect:
        batch = max(suspect, key=lambda b: b["date"])
        searched, empty = int(batch["searched"]), int(batch["no_links"])
        out.append(Finding(
            "discovery-empty-batch", batch["date"],
            f"Scholar discovery on {batch['date']}: {empty} of {searched} searches showed no linked author",
            "A search for someone with any publications nearly always shows at least a co-author with a "
            "profile. A batch that is nearly all empty suggests the searches were not returning real results "
            "(in September 2026 they were Scholar's verification page, and people were wrongly recorded as "
            "having no profile).\n\n"
            "The people in this batch are marked as searched. Check a few by hand, and if the batch was bad, "
            "delete their `google_scholar` rows with an empty `external_id` from "
            "`data/review/identity_candidates.csv` so they are searched again. This stays open for a week "
            "unless closed by hand."))
    if not queued:
        return out
    reported = (status or {}).get("reported", "")[:10]
    if not reported or reported < (today - timedelta(days=SILENT_DAYS)).isoformat():
        out.append(Finding(
            "discovery-silent", reported or "never",
            f"Scholar discovery has not run since {reported or 'records began'}; {queued} people are waiting",
            f"{queued} people are waiting for a Scholar profile search, and the discovery step of the weekly "
            f"Scholar workflow has recorded nothing for more than {SILENT_DAYS} days. See the latest run of "
            "*Collect Google Scholar metrics (weekly, rolling)* under Actions."))
        return out
    stalled = status.get("stalled_since")
    if stalled:
        out.append(Finding(
            "discovery-stalled", stalled,
            f"Scholar discovery has searched no one since {stalled}; {queued} people are waiting",
            f"The last run had people to search and searched none (outcome: {status.get('outcome', 'unknown')}). "
            f"Last successful search: {status.get('last_searched') or 'none on record'}.\n\n"
            "Usual causes: the SerpApi quota was already spent, or SerpApi was failing. New hires and anyone "
            "whose profile id died stay without a Scholar figure until a run gets through."))
    return out


SERPAPI_KEYS = {"serpapi-quota"}


def check_serpapi(account: dict, today: date, last_rolling: str | None, budget: int = WEEKLY_BUDGET) -> list[Finding]:
    """Asked only on the eve of a weekly run (Sunday, or Monday before the run
    has landed): the quota resets on a date SerpApi does not report, so a low
    balance earlier in the week says nothing about Monday."""
    left = account.get("total_searches_left")
    monday = today + timedelta(days=(7 - today.weekday()) % 7)
    soon = today.weekday() == 6 or (today.weekday() == 0 and (last_rolling or "") < today.isoformat())
    if not soon or left is None or int(left) >= budget:
        return []
    return [Finding(
        "serpapi-quota", monday.isoformat(),
        f"SerpApi has {int(left)} searches left; Monday's Scholar run needs {budget}",
        f"The account ({account.get('plan_name') or 'plan unknown'}, {account.get('searches_per_month', '?')} "
        f"searches a month) has used {account.get('this_month_usage', '?')} this period. The run on "
        f"{monday.isoformat()} will read only what the balance allows and fail the rest, unless the "
        "period resets first.\n\nTo skip or shrink the run, start the workflow by hand with a smaller "
        "budget, or disable it for the week under Actions.")]


SITE_KEYS = {"site-stale", "site-unreachable"}


def repo_snapshot_dates(root=SNAPSHOT_DIR) -> dict[str, str]:
    """source -> date of its newest snapshot file. The site build publishes the
    same thing as status.json, so the two can be compared."""
    out = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        dates = sorted(f.stem for f in d.glob("*.csv"))
        if dates:
            out[d.name] = dates[-1]
    return out


def check_site(live: dict, repo: dict[str, str], today: date) -> list[Finding]:
    """The live site was built without a snapshot the repository has held for
    at least a day (a same-day snapshot may simply still be deploying)."""
    behind = {src: (live.get(src), d) for src, d in repo.items()
              if d < today.isoformat() and (live.get(src) or "") < d}
    if not behind:
        return []
    lines = "\n".join(f"- {src}: the site was built with {have or 'nothing'}, the repository has {d}"
                      for src, (have, d) in sorted(behind.items()))
    return [Finding(
        "site-stale", max(d for _, d in behind.values()),
        f"The live site is missing the {', '.join(sorted(behind))} snapshot of {max(d for _, d in behind.values())}",
        f"{lines}\n\nThe data was committed and the site was not rebuilt, or the deploy failed. "
        "Run *Deploy site* by hand under Actions.")]


# -------------------------------------------------------------- GitHub issues

MARK = re.compile(r"<!-- health-check key=(\S+) episode=(\S+?)( resolved)? -->")
FOOTER = ("\n\n---\nRaised by `pipeline/health_check.py`. This issue closes itself when the check passes "
          "again. Closing it by hand silences this occurrence.\n")


def issue_body(f: Finding, resolved: bool = False) -> str:
    return f"{f.detail}{FOOTER}<!-- health-check key={f.key} episode={f.episode}{' resolved' if resolved else ''} -->"


def issue_title(f: Finding) -> str:
    return ("" if f.alarm else "Notice: ") + f.title


def plan_issue_actions(findings: list[Finding], issues: list[dict], evaluated: set[str], today: date) -> list[dict]:
    """What to do to the tracker so that it shows exactly the current findings.

    `issues` are the repository's health-check issues, open and closed:
    {number, state, title, body}. `evaluated` holds the keys of the rules that
    were actually checked this run; an open issue for a rule that was not is
    left alone. Each action is a dict with `do` and, where the owner should
    hear about it, `new: True`.
    """
    tracked = []
    for i in issues:
        m = MARK.search(i.get("body") or "")
        if m:
            tracked.append({**i, "key": m[1], "episode": m[2], "resolved": bool(m[3]),
                            "open": (i.get("state") or "").upper() == "OPEN"})
    actions = []
    for f in findings:
        title, body = issue_title(f), issue_body(f)
        mine = [i for i in tracked if i["key"] == f.key]
        current = next((i for i in mine if i["open"]), None)
        if current:
            if current["episode"] != f.episode:
                actions.append({"do": "comment", "number": current["number"], "new": True, "alarm": f.alarm,
                                "text": f"It has happened again ({today.isoformat()}): **{f.title}**\n\n{f.detail}"})
            if current["episode"] != f.episode or current.get("title") != title or current.get("body") != body:
                actions.append({"do": "edit", "number": current["number"], "title": title, "body": body})
            continue
        same = [i for i in mine if i["episode"] == f.episode]
        if any(not i["resolved"] for i in same):
            continue                                   # closed by hand: this occurrence has been seen
        if same:
            n = max(i["number"] for i in same)
            actions.append({"do": "reopen", "number": n, "new": True, "alarm": f.alarm,
                            "text": f"This is failing again as of {today.isoformat()}."})
            actions.append({"do": "edit", "number": n, "title": title, "body": body})
            continue
        actions.append({"do": "create", "title": title, "body": body, "new": True, "alarm": f.alarm})
    firing = {f.key for f in findings}
    for i in tracked:
        if i["open"] and i["key"] in evaluated and i["key"] not in firing:
            actions.append({"do": "edit", "number": i["number"], "title": i.get("title"),
                            "body": MARK.sub(f"<!-- health-check key={i['key']} episode={i['episode']} resolved -->",
                                             i.get("body") or "")})
            actions.append({"do": "close", "number": i["number"],
                            "text": f"The check passed on {today.isoformat()}; closing."})
    return actions


def gh(*args: str, stdin: str | None = None) -> str:
    r = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {r.stderr.strip()[:300]}")
    return r.stdout


def list_issues() -> list[dict]:
    return json.loads(gh("issue", "list", "--label", LABEL, "--state", "all", "--limit", "300",
                         "--json", "number,state,title,body"))


def apply_issue_actions(actions: list[dict]) -> None:
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    if any(a["do"] == "create" for a in actions):
        gh("label", "create", LABEL, "--force", "--color", "B60205",
           "--description", "Raised by the scheduled health check; closes itself when the check passes")
    for a in actions:
        n = str(a.get("number", ""))
        if a["do"] == "create":
            base = ["issue", "create", "--title", a["title"], "--label", LABEL, "--body-file", "-"]
            try:
                # Assigned, so the owner is notified whether or not they watch the repository.
                print("  opened", gh(*base, *(["--assignee", owner] if owner else []), stdin=a["body"]).strip())
            except RuntimeError:
                print("  opened", gh(*base, stdin=a["body"]).strip(), "(unassigned)")
        elif a["do"] == "edit":
            gh("issue", "edit", n, "--title", a["title"], "--body-file", "-", stdin=a["body"])
        elif a["do"] == "comment":
            gh("issue", "comment", n, "--body-file", "-", stdin=a["text"])
            print(f"  commented on #{n}")
        elif a["do"] == "reopen":
            gh("issue", "reopen", n, "--comment", a["text"])
            print(f"  reopened #{n}")
        elif a["do"] == "close":
            gh("issue", "close", n, "--comment", a["text"])
            print(f"  closed #{n}")


# ------------------------------------------------------------------ gathering

def read_rows(path) -> list[dict]:
    csv.field_size_limit(10 ** 9)
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_metas(source: str) -> dict[str, dict]:
    out = {}
    for f in sorted((SNAPSHOT_DIR / source).glob("*.meta.json")):
        try:
            out[f.name[: -len(".meta.json")]] = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
    return out


def fetch_json(url: str, tries: int = 3) -> dict:
    """GET a JSON document. Errors are re-raised without the URL, which can carry a key."""
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "planning-citation-metrics health check"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            err = type(e).__name__ + (f" {e.code}" if hasattr(e, "code") else "")
            if attempt + 1 < tries:
                time.sleep(10)
    raise RuntimeError(err)


def discovery_queue(persons: list[dict], review: list[dict]) -> int:
    from .find_scholar_profiles import load_run_metas, people_to_search, searched_ids, stale_ids_on_file
    return len(people_to_search(persons, searched_ids(review), stale_ids_on_file(persons, load_run_metas())))


def gather(today: date, expect: str = "", status_file: str | None = None, offline: bool = False,
           self_test: bool = False, job_result: str = "") -> tuple[list[Finding], set[str], list[str]]:
    """Run every check that can be run. Returns the findings, the keys of the
    rules that were evaluated, and a line for each check that was skipped."""
    findings, evaluated, skipped = [], set(), []
    persons = read_rows(ROSTER_DIR / "person.csv")
    review = read_rows(REVIEW_DIR / "identity_candidates.csv")
    names = {p["person_id"]: p["display_name"] for p in persons}

    gs = load_metas("google_scholar")
    findings += check_scholar(gs, today, expect == "scholar") + check_dead_ids(persons, gs)
    evaluated |= SCHOLAR_KEYS

    oa = load_metas("openalex")
    findings += check_openalex(oa, today, expect == "openalex")
    evaluated |= OPENALEX_KEYS
    last_full, _ = latest(full_openalex_runs(oa))
    recent = sorted(f for f in (SNAPSHOT_DIR / "openalex").glob("*.csv") if last_full and f.stem >= last_full)
    for f in recent:
        findings += check_openalex_figures(f.stem, read_rows(f), names)
    by_key = {}
    for f in findings:                      # one issue per rule: keep the newest snapshot's finding
        by_key[f.key] = f
    findings = list(by_key.values())
    evaluated |= FIGURE_KEYS

    if expect:
        own = {"scholar": {"scholar-overdue", "scholar-short-run"}, "openalex": OPENALEX_KEYS}[expect]
        findings += check_job(expect, job_result, today, any(f.key in own for f in findings))
        evaluated.add(f"{expect}-job")

    findings += check_audit(persons, review, today)
    evaluated |= AUDIT_KEYS

    try:
        status = json.loads(open(status_file or DISCOVERY_STATUS).read())
    except (OSError, ValueError):
        status = None                       # no record at all: discovery has never reported
    findings += check_discovery(status, discovery_queue(persons, review), today)
    evaluated |= DISCOVERY_KEYS

    key = os.environ.get("SERPAPI_KEY")
    if key and not offline:
        try:
            account = fetch_json("https://serpapi.com/account.json?" + urllib.parse.urlencode({"api_key": key}))
            findings += check_serpapi(account, today, latest(rolling_runs(gs))[0])
            evaluated |= SERPAPI_KEYS
        except RuntimeError as e:
            skipped.append(f"SerpApi quota: account lookup failed ({e})")
    else:
        skipped.append("SerpApi quota: no SERPAPI_KEY here")

    if not offline:
        try:
            live = fetch_json(f"{SITE}/status.json").get("snapshots") or {}
            findings += check_site(live, repo_snapshot_dates(), today)
        except RuntimeError as e:
            # Not skipped: a status file that cannot be read means nothing is watching the deploys.
            findings.append(Finding(
                "site-unreachable", today.strftime("%Y-%m"),
                f"The live site's status file could not be read ({e})",
                f"`{SITE}/status.json` did not load in three tries. Either the site is down, or the last "
                "deploy did not publish the file. Until it loads, nothing checks that new data reaches the site."))
        evaluated |= SITE_KEYS
    else:
        skipped.append("live site: offline")

    evaluated.add("self-test")
    if self_test:
        findings.append(Finding(
            "self-test", today.isoformat(), "Test: the health check can reach you",
            "Raised on request (`--self-test`) to show what a real alarm looks like and to confirm that GitHub "
            "notifies you about it. Nothing is wrong. The next health check closes this."))
    return findings, evaluated, skipped


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sync-issues", action="store_true",
                    help="open, refresh and close GitHub issues to match; exit 1 only when something new was raised")
    ap.add_argument("--expect", default="", choices=["", "scholar", "openalex"],
                    help="a scheduled run of this collection should just have finished")
    ap.add_argument("--job-result", default="", metavar="RESULT",
                    help="how the collection job ended (success, failure, cancelled), from the workflow that ran it")
    ap.add_argument("--discovery-status", metavar="FILE",
                    help="a discovery status record other than data/review/discovery_status.json")
    ap.add_argument("--offline", action="store_true", help="only the checks that need no network")
    ap.add_argument("--self-test", action="store_true",
                    help="raise a harmless test alarm, to see that the notification arrives")
    ap.add_argument("--today", default=date.today().isoformat(), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    today = date.fromisoformat(args.today)

    findings, evaluated, skipped = gather(today, args.expect, args.discovery_status, args.offline, args.self_test,
                                          args.job_result)
    for f in findings:
        print(f"{'ALARM ' if f.alarm else 'notice'}  {f.key}  [{f.episode}]\n        {f.title}")
    for s in skipped:
        print(f"skipped {s}")
    alarms = [f for f in findings if f.alarm]
    print(f"\n{len(alarms)} alarm(s), {len(findings) - len(alarms)} notice(s), "
          f"{len(evaluated)} rules checked, {len(skipped)} checks skipped")

    if not args.sync_issues:
        sys.exit(1 if alarms else 0)
    actions = plan_issue_actions(findings, list_issues(), evaluated, today)
    apply_issue_actions(actions)
    fresh = [a for a in actions if a.get("new") and a.get("alarm")]
    if fresh:
        print(f"::error::{len(fresh)} new problem(s) raised; see the open issues labelled {LABEL}")
        sys.exit(1)


if __name__ == "__main__":
    main()
