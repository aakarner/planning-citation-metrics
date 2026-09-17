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
# it says so and does nothing further, so the agent can be removed:
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
searched = {r["person_id"] for r in csv.DictReader(open("data/review/identity_candidates.csv"))
            if r["source"] == "google_scholar"}
print(sum(1 for p in people if not p["google_scholar_id"] and p["person_id"] not in searched))
PYEOF
}

TODAY="$(date '+%Y-%m-%d')"
if [ -f "$STAMP" ] && [ "$(cat "$STAMP" 2>/dev/null)" = "$TODAY" ]; then
  say "already searched a batch today; this slot has nothing to do"
  exit 0
fi

LEFT="$(remaining 2>/dev/null || echo unknown)"
if [ "$LEFT" = "0" ]; then
  say "nothing left to search: every person without a Scholar id has been checked."
  say "the agent has no more work and can be removed (see the header of this script)."
  exit 0
fi

say "starting: $LEFT people left to search, doing up to $BATCH"
git pull --rebase --autostash --quiet origin main >> "$LOG" 2>&1 || say "pull failed; continuing on the local copy"

OUT="$("$PY" -m pipeline.find_scholar_profiles --limit "$BATCH" 2>&1)"
printf '%s\n' "$OUT" >> "$LOG"

SUMMARY="$(printf '%s' "$OUT" | grep -E "^summary:" | head -1)"
say "finished. ${SUMMARY:-no summary line; see above}"

if ! git diff --quiet -- data/roster/person.csv data/review/identity_candidates.csv; then
  git add data/roster/person.csv data/review/identity_candidates.csv
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
