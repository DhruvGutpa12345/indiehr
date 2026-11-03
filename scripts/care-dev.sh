#!/usr/bin/env bash
# Run upstream CARE locally with the IndieHR low-memory override (P0-T5).
# Usage: scripts/care-dev.sh <up|down|ps|logs|seed|frontend|shell>
set -euo pipefail
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
BACKEND="$ROOT/apps/backend"
COMPOSE=(docker compose --project-directory "$BACKEND" -p indiehr-care
  -f "$BACKEND/docker-compose.yaml" -f "$BACKEND/docker-compose.local.yaml"
  -f "$ROOT/deploy/dev/care-local.override.yaml")
FE_IMAGE=indiehr-frontend:local

case "${1:-}" in
  up)
    docker image inspect care_local >/dev/null 2>&1 || \
      docker buildx build --load -f "$BACKEND/docker/dev.Dockerfile" -t care_local "$BACKEND"
    "${COMPOSE[@]}" up -d --wait
    echo "API: http://localhost:9000/api/v1/  (health: http://localhost:9000/ping/ if available)"
    ;;
  down) "${COMPOSE[@]}" down; docker rm -f indiehr-frontend >/dev/null 2>&1 || true ;;
  ps) "${COMPOSE[@]}" ps; docker ps --filter name=indiehr-frontend --format '{{.Names}} {{.Status}} {{.Ports}}' ;;
  logs) "${COMPOSE[@]}" logs --tail=100 "${@:2}" ;;
  seed) "${COMPOSE[@]}" exec backend python manage.py load_fixtures ;;
  shell) "${COMPOSE[@]}" exec backend bash ;;
  frontend)
    # Serve a pre-built bundle (from CI artifact) unpacked into deploy/dev/frontend-build
    BUILD="$ROOT/deploy/dev/frontend-build"
    test -f "$BUILD/index.html" || { echo "No bundle in $BUILD (see docs/LOCAL_DEV.md)"; exit 1; }
    docker buildx build -q --load -f "$ROOT/deploy/docker/frontend.Dockerfile" \
      --build-context config="$ROOT/deploy/docker" -t "$FE_IMAGE" "$BUILD" >/dev/null
    docker rm -f indiehr-frontend >/dev/null 2>&1 || true
    docker run -d --name indiehr-frontend -p 127.0.0.1:8080:8080 --memory 64m "$FE_IMAGE" >/dev/null
    echo "Web app: http://localhost:8080"
    ;;
  *) sed -n '3p' "$0"; exit 1 ;;
esac
