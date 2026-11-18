#!/usr/bin/env bash
# Commit what is staged and push it to main, or fail the step.
#
#   git add <paths>
#   bash .github/scripts/commit_and_push.sh "commit message"
#
# main moves while a long run collects, so the commit is rebased onto it and
# the push is tried three times. Until 2026-11 each collector workflow carried
# its own copy of this loop, and the loop ended quietly after the third
# rejection: the step passed, the run went green, the site was redeployed
# without the new data, and the snapshot was gone with the runner. Now a push
# that cannot be made fails the job, which the health check reports.
set -euo pipefail

message="${1:?usage: commit_and_push.sh \"commit message\"}"
pause="${PUSH_RETRY_SLEEP:-15}"

git config user.name "citation-metrics-bot"
git config user.email "actions@users.noreply.github.com"

if git diff --cached --quiet; then
  echo "nothing new"
  exit 0
fi
git commit -m "$message"

for attempt in 1 2 3; do
  # --autostash: a tracked file this run touched but did not stage must not block the rebase.
  if git pull --rebase --autostash origin main && git push; then
    exit 0
  fi
  git rebase --abort 2>/dev/null || true
  echo "push rejected, retrying ($attempt)"
  sleep "$pause"
done

echo "::error::could not push the commit to main after three attempts; nothing from this run was saved"
exit 1
