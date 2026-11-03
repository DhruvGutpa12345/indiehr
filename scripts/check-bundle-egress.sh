#!/usr/bin/env bash
# Fail if a built web app bundle references an unreviewed external host (P0-T10).
# Usage: scripts/check-bundle-egress.sh <build-dir> [allowlist]
set -euo pipefail
BUILD="${1:?usage: $0 <build-dir> [allowlist]}"
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
ALLOW="${2:-$ROOT/deploy/frontend-egress-allow.txt}"
# Never acceptable, even if someone adds them to the allowlist.
DENY_RE='^sentry\.io$|(^|\.)ingest\.([a-z]+\.)?sentry\.io$|^cdn\.ohc\.network$|^careapi\.ohc\.network$|^sbom\.ohc\.network$|\.s3\.amazonaws\.com$'

hosts="$(grep -rhoE 'https?://[A-Za-z0-9._@%-]+' "$BUILD" \
  | sed -E 's#^https?://##; s#^.*@##; s#:[0-9]+$##' \
  | grep -E '\.[A-Za-z]{2,}$' | grep -vE '^(localhost|127\.0\.0\.1)$' | sort -u)"
allowed="$(sed -E 's/#.*//; s/[[:space:]]+//g' "$ALLOW" | grep -v '^$' | sort -u)"

fail=0
while IFS= read -r h; do
  [[ -z "$h" ]] && continue
  if [[ "$h" =~ $DENY_RE ]]; then
    echo "DENIED host in bundle: $h"; fail=1
  elif ! grep -qxF "$h" <<<"$allowed"; then
    echo "UNREVIEWED host in bundle: $h (review it, then add to $(basename "$ALLOW") with a reason)"; fail=1
  fi
done <<<"$hosts"

echo "$(wc -l <<<"$hosts") distinct hosts checked in $BUILD"
[[ $fail -eq 0 ]] && echo "egress check: OK"
exit $fail
