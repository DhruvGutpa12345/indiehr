#!/usr/bin/env bash
# How far our CARE pins are behind upstream (P1-T17; PLAN §8 promises monthly syncs).
#
#   scripts/check-upstream-drift.sh
#
# Prints the dry-run sync report (commits behind, files, our patched files touched upstream) and
# each pin's age, and writes them to the GitHub job summary when there is one. Warns when a pin is
# WARN_DAYS old or more (default 30) and fails when it is more than FAIL_DAYS old (default 60).
# DRIFT_TODAY (YYYY-MM-DD) and DRIFT_SKIP_FETCH=1 exist for the tests.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
LOCK="${DRIFT_LOCK:-upstream.lock}"
WARN_DAYS="${WARN_DAYS:-30}"
FAIL_DAYS="${FAIL_DAYS:-60}"
TODAY="${DRIFT_TODAY:-$(date -u +%F)}"

report=""
if [[ "${DRIFT_SKIP_FETCH:-}" != 1 ]]; then
  report="$(scripts/sync-upstream.sh all --dry-run 2>&1)"
fi

status=0
ages=""
for app in backend frontend; do
  pinned="$(sed -n "s/^$app\.date=//p" "$LOCK")"
  if [[ ! "$pinned" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    echo "check-upstream-drift: no $app.date in $LOCK" >&2
    exit 2
  fi
  days=$(( ( $(date -u -d "$TODAY" +%s) - $(date -u -d "$pinned" +%s) ) / 86400 ))
  line="$app pin $pinned is $days day(s) old"
  if (( days > FAIL_DAYS )); then
    echo "::error::$line (more than $FAIL_DAYS): sync upstream (docs/UPSTREAM.md)"
    status=1
  elif (( days >= WARN_DAYS )); then
    echo "::warning::$line (monthly syncs, PLAN §8)"
  fi
  ages+="- $line"$'\n'
done

out="## Upstream drift ($TODAY)"$'\n\n'"$ages"
if [[ -n "$report" ]]; then
  out+=$'\n''```'$'\n'"$report"$'\n''```'$'\n'
fi
echo "$out"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  echo "$out" >> "$GITHUB_STEP_SUMMARY"
fi
exit "$status"
