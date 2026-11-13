#!/bin/bash
# One batch of Google Scholar profile discovery, for launchd to run daily.
#
# Scholar blocks an address after roughly 45 requests, so this does 30 people a
# day and no more. launchd fires it at more than one time of day, because a
# missed slot (laptop asleep, lid shut) is never made up; the first slot that
# actually searches someone stamps the date, and later slots that day see the
# stamp and exit. A slot that got nothing done (Scholar blocked, network down)
# does not stamp, so the next slot tries again. Each run appends to the log,
# commits anything it found, and pushes. When there is nobody left to search
# it says so and does nothing further. It stays installed even then: a Scholar
# id that stops resolving (recorded by the weekly collection in the snapshot's
# .meta.json) puts that person back on the list, and this is what looks for
# the replacement profile. Every slot ends by publishing a small status record
# (pipeline/discovery_status.py) to refs/agent/discovery-status on GitHub, so
# the daily health check can tell when this has gone quiet, has been blocked
# for days, or has read a batch of pages that were not results. See also:
#
#   launchctl bootout gui/$(id -u)/com.aakarner.planning-citations.discovery
#   rm ~/Library/LaunchAgents/com.aakarner.planning-citations.discovery.plist
#
# Installed by: scripts/install_discovery_agent.sh

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python"
LOG="$HOME/Library/Logs/planning-citations-discovery.log"
LOCK="$REPO/.discovery.lock"
STAMP="$REPO/.discovery.last-run"   # holds the date of the last slot that did work
BATCH="${DISCOVERY_BATCH:-30}"   # overridable so the plumbing can be tested cheaply
STATE="$REPO/.discovery.status.json"    # this agent's status record, as last published
BATCHREC="$REPO/.discovery.batch.json"  # what the finder saw this slot
STATUS_REF="refs/agent/discovery-status"

export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin"
mkdir -p "$(dirname "$LOG")"

say() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }

# One at a time. mkdir is atomic, so a crashed run leaves a stale lock we can
# age out rather than a half-held lock we cannot reason about.
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
    say "clearing a lock older than 2h"
    rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null || { say "could not take the lock; skipping"; exit 0; }
  else
    say "another run holds the lock; skipping"
    exit 0
  fi
fi
trap 'rm -rf "$LOCK"' EXIT

cd "$REPO" || { say "cannot cd to $REPO"; exit 1; }
[ -x "$PY" ] || { say "no interpreter at $PY"; exit 1; }

# Keep the log from growing without bound.
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 1000000 ]; then
  tail -c 300000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

remaining() {
  "$PY" - <<'PYEOF'
import csv
people = list(csv.DictReader(open("data/roster/person.csv")))
from pipeline.find_scholar_profiles import searched_ids, stale_ids_on_file, load_run_metas, people_to_search
searched = searched_ids(list(csv.DictReader(open("data/review/identity_candidates.csv"))))
print(len(people_to_search(people, searched, stale_ids_on_file(people, load_run_metas()))))
PYEOF
}

# Fold this slot into the status record and publish it: one file in one
# parentless commit, pushed over whatever the last one was. It never touches
# main, so it starts no build. A failure here is logged and nothing more; the
# health check treats an agent with work and no recent report as a problem.
#   report OUTCOME [QUEUED] [BATCH-REPORT]
report() {
  local queued="${2:-}" blob tree commit
  [ "$queued" = "unknown" ] && queued=""
  "$PY" -m pipeline.discovery_status --state "$STATE" --outcome "$1" \
      ${queued:+--queued "$queued"} ${3:+--batch "$3"} >> "$LOG" 2>&1 \
    || { say "could not write the status record"; return 0; }
  blob="$(git hash-object -w "$STATE")" \
    && tree="$(printf '100644 blob %s\tdiscovery.json\n' "$blob" | git mktree)" \
    && commit="$(git commit-tree "$tree" -m "discovery agent status")" \
    && git push --quiet --force origin "$commit:$STATUS_REF" >> "$LOG" 2>&1 \
    || say "could not publish the status record"
  return 0
}

TODAY="$(date '+%Y-%m-%d')"
if [ -f "$STAMP" ] && [ "$(cat "$STAMP" 2>/dev/null)" = "$TODAY" ]; then
  say "already searched a batch today; this slot has nothing to do"
  report rested
  exit 0
fi

LEFT="$(remaining 2>/dev/null || echo unknown)"
if [ "$LEFT" = "0" ]; then
  say "nothing to search: every person without a Scholar id has been checked and no recorded id is stale."
  report idle 0
  exit 0
fi

say "starting: $LEFT people left to search, doing up to $BATCH"

# launchd fires a slot the moment the Mac wakes, often before Wi-Fi is back.
# Both slots on 2026-09-18 failed five requests in two seconds for that reason.
# Wait for the network rather than spend the slot on a machine that is offline.
online() { curl -s --max-time 5 -o /dev/null "https://scholar.google.com/robots.txt"; }
tries=0
until online; do
  tries=$((tries + 1))
  if [ "$tries" -ge 8 ]; then
    say "no network after two minutes; leaving the day open for the next slot"
    report offline "$LEFT"    # recorded locally; it goes up with the next slot that has a network
    exit 0
  fi
  sleep 15
done
[ "$tries" -gt 0 ] && say "network came up after $((tries * 15))s"

git pull --rebase --autostash --quiet origin main >> "$LOG" 2>&1 || say "pull failed; continuing on the local copy"

rm -f "$BATCHREC"
OUT="$("$PY" -m pipeline.find_scholar_profiles --limit "$BATCH" --report "$BATCHREC" 2>&1)"
printf '%s\n' "$OUT" >> "$LOG"

SUMMARY="$(printf '%s' "$OUT" | grep -E "^summary:" | head -1)"
say "finished. ${SUMMARY:-no summary line; see above}"

# A found profile is only an id until its figures are fetched; without this the
# person stays on their Publish or Perish number until the quarterly collection
# while the homepage announces the find. One SerpApi request per new profile.
# Skips, and says so, when no key is configured, so cancelling the subscription
# later just defers the figures to the quarterly run.
if grep -qiE '^SERPAPI(_API)?_KEY=.+' "$REPO/.env" 2>/dev/null; then
  FETCH="$("$PY" -m pipeline.collect_scholar --missing 2>&1)"
  printf '%s\n' "$FETCH" >> "$LOG"
  say "fetched figures for newly found profiles: $(printf '%s' "$FETCH" | grep -E '^done:' | head -1 || echo 'nothing to fetch')"
else
  say "no SerpApi key in .env; figures for new profiles will arrive with the quarterly collection"
fi

if ! git diff --quiet -- data/roster/person.csv data/review/identity_candidates.csv data/snapshots/google_scholar \
   || [ -n "$(git ls-files --others --exclude-standard data/snapshots/google_scholar)" ]; then
  git add data/roster/person.csv data/review/identity_candidates.csv data/snapshots/google_scholar
  git commit -q -m "Scholar profile discovery, $(date '+%Y-%m-%d') (automated daily batch)" \
    >> "$LOG" 2>&1 && say "committed"
  if git push --quiet origin main >> "$LOG" 2>&1; then
    say "pushed"
  else
    say "push failed; the commit is local and will go up with the next push"
  fi
else
  say "no data changed, nothing to commit"
fi

AFTER="$(remaining 2>/dev/null || echo unknown)"
say "$AFTER people still to search"

# Stamp only if the count moved: a blocked or failed slot leaves the day open
# for the next one.
if [ "$LEFT" != "unknown" ] && [ "$AFTER" != "unknown" ] && [ "$AFTER" -lt "$LEFT" ]; then
  printf '%s\n' "$TODAY" > "$STAMP"
else
  say "no one was searched this slot; a later slot today will try again"
fi
report ran "$AFTER" "$BATCHREC"
