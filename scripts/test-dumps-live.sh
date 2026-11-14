#!/usr/bin/env bash
# The dumper's tests with a real Postgres (P1-T22a; scripts/test_dumps.py, class Live and all the
# others): inside the dumper image, which has pg_dump, pg_restore, initdb, Python, psycopg and
# cryptography. No network; the throwaway server lives in the container's /tmp and goes with it.
# Usage: scripts/test-dumps-live.sh [image]   (default: indiehr-dumper:staging, built if missing)
set -euo pipefail
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
IMG="${1:-indiehr-dumper:staging}"
DF="$ROOT/deploy/docker/dumper.Dockerfile"
sum="$(sha256sum "$DF" | cut -d' ' -f1)"
if [[ "$(docker image inspect -f '{{index .Config.Labels "ihr.dockerfile.sha256"}}' "$IMG" 2>/dev/null || true)" != "$sum" ]]; then
  echo "Building $IMG (deploy/docker/dumper.Dockerfile)"
  docker buildx build -q --load --label "ihr.dockerfile.sha256=$sum" -f "$DF" -t "$IMG" "$ROOT/deploy/docker" >/dev/null
fi
docker run --rm --network none --read-only --tmpfs /tmp:size=4g --tmpfs /state:size=64m,uid=70,gid=70 \
  --cap-drop ALL --security-opt no-new-privileges:true -e HOME=/tmp -e TMPDIR=/tmp \
  -e IHR_DUMPS_ROOT=/repo -e IHR_DUMPS_LIVE_REQUIRED=1 -v "$ROOT/deploy/dumps:/repo/deploy/dumps:ro" -v "$ROOT/deploy/lib:/repo/deploy/lib:ro" \
  -v "$ROOT/deploy/logs/archive:/repo/deploy/logs/archive:ro" -v "$ROOT/scripts/test_dumps.py:/repo/test_dumps.py:ro" \
  --entrypoint python3 "$IMG" -I /repo/test_dumps.py -v 2>&1 | tail -n "${TAIL:-25}"
