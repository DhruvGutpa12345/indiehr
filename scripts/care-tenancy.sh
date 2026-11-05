#!/usr/bin/env bash
# Run a CARE manage.py command in tenant mode (ihr_platform settings) against the separate
# local database "care_tenancy", so the normal dev database is untouched. P1-T1a.
# Needs the local stack running (scripts/care-dev.sh up).
#
# Usage:
#   scripts/care-tenancy.sh init                      # (re)create care_tenancy + shared schema
#   scripts/care-tenancy.sh ihr_create_tenant --slug alpha --name "Alpha Hospital" --domain alpha.localhost
#   scripts/care-tenancy.sh test --settings=ihr_platform.settings.test ihr_platform
#   scripts/care-tenancy.sh <any manage.py command and arguments>
set -euo pipefail
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
NETWORK="$(docker inspect indiehr-care-db-1 --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')"

manage() {
  docker run --rm ${STDIN:+-i} --network "$NETWORK" \
    --env-file "$ROOT/apps/backend/docker/.local.env" \
    -e DATABASE_URL=postgres://postgres:postgres@db:5432/care_tenancy \
    -e DJANGO_SETTINGS_MODULE=ihr_platform.settings.local \
    -e PYTHONPATH=/ihr/plugins/ihr_platform \
    -e SNOWSTORM_DEPLOYMENT_URL=http://127.0.0.1:9/fhir \
    -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$ROOT/apps/backend:/app:ro" -v "$ROOT/plugins:/ihr/plugins:ro" -w /app \
    --user "$(id -u):$(id -g)" care_local python manage.py "$@"
}

if [[ "${1:-}" == "init" ]]; then
  docker exec indiehr-care-db-1 psql -U postgres -qc "DROP DATABASE IF EXISTS care_tenancy" -c "CREATE DATABASE care_tenancy"
  manage migrate_schemas --shared
  exit 0
fi
[[ $# -gt 0 ]] || { sed -n '6,10p' "$0"; exit 1; }
manage "$@"
