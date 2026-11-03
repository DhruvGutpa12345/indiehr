#!/usr/bin/env bash
# Run upstream CARE locally with the IndieHR low-memory override (P0-T5).
# Usage: scripts/care-dev.sh <up|down|ps|logs|seed|reset-passwords|frontend|shell>
set -euo pipefail
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
BACKEND="$ROOT/apps/backend"
COMPOSE=(docker compose --project-directory "$BACKEND" -p indiehr-care
  -f "$BACKEND/docker-compose.yaml" -f "$BACKEND/docker-compose.local.yaml"
  -f "$ROOT/deploy/dev/care-local.override.yaml")
FE_IMAGE=indiehr-frontend:local

write_test_users() {
  mkdir -p "$ROOT/deploy/seed"
  cat > "$ROOT/deploy/seed/TEST_USERS.md" <<EOT
# Local test users — synthetic, localhost only, NOT committed

Generated $(date '+%d-%m-%Y %H:%M') by \`scripts/care-dev.sh\`. CARE's public fixture passwords were replaced.
Web app: http://localhost:8080 · API: http://localhost:9000 · Django admin: http://localhost:9000/admin/

**Password for every user below:** \`$1\`

| Username | CARE role | Use it for |
|---|---|---|
| care-doctor | Doctor | OPD/IPD consultations, prescriptions, orders |
| care-nurse | Nurse | Vitals, medication administration, nursing notes |
| care-pharmacist | Pharmacist | Dispensing, stock |
| care-billing | Staff | Charges, invoices, payments (cannot create billing accounts; see GAP_ANALYSIS) |
| care-staff | Staff | Front desk / registration |
| care-fac-admin | Facility Admin | Facility settings, billing accounts, tariffs, users |
| care-admin | Administrator | Organisation-level admin |
| care-volunteer | Volunteer | Read-mostly |
| admin | Superuser | Django admin only |
EOT
}

case "${1:-}" in
  up)
    # Rebuild the dev image whenever its inputs (lock file, Dockerfile) change
    WANT="$(cat "$BACKEND/Pipfile.lock" "$BACKEND/docker/dev.Dockerfile" | sha256sum | cut -c1-16)"
    HAVE="$(docker image inspect care_local --format '{{ index .Config.Labels "indiehr.deps" }}' 2>/dev/null || true)"
    if [[ "$WANT" != "$HAVE" ]]; then
      echo "Building care_local (dependency hash $WANT, image has '${HAVE:-none}')"
      docker buildx build --load --label "indiehr.deps=$WANT" \
        -f "$BACKEND/docker/dev.Dockerfile" -t care_local "$BACKEND"
    fi
    "${COMPOSE[@]}" up -d --wait
    echo "API: http://localhost:9000/api/v1/  (health: http://localhost:9000/ping/ if available)"
    ;;
  down) "${COMPOSE[@]}" down; docker rm -f indiehr-frontend >/dev/null 2>&1 || true ;;
  ps) "${COMPOSE[@]}" ps; docker ps --filter name=indiehr-frontend --format '{{.Names}} {{.Status}} {{.Ports}}' ;;
  logs) "${COMPOSE[@]}" logs --tail=100 "${@:2}" ;;
  seed)
    # CARE demo data, then our test users with a fresh generated password (P0-T11)
    "${COMPOSE[@]}" exec -T backend python manage.py load_fixtures
    PW="Ihr-$(python3 -c 'import secrets;print(secrets.token_urlsafe(9))')-7"
    "${COMPOSE[@]}" exec -T -e TEST_PASSWORD="$PW" backend python manage.py shell -v 0 \
      < "$ROOT/deploy/dev/seed_test_users.py" | tail -1
    write_test_users "$PW"
    echo "Test logins written to deploy/seed/TEST_USERS.md"
    ;;
  reset-passwords)
    PW="Ihr-$(python3 -c 'import secrets;print(secrets.token_urlsafe(9))')-7"
    "${COMPOSE[@]}" exec -T -e TEST_PASSWORD="$PW" backend python manage.py shell -v 0 \
      < "$ROOT/deploy/dev/seed_test_users.py" | tail -1
    write_test_users "$PW"
    echo "New password written to deploy/seed/TEST_USERS.md"
    ;;
  shell) "${COMPOSE[@]}" exec backend bash ;;
  frontend)
    # Serve a pre-built bundle (from CI artifact) unpacked into deploy/dev/frontend-build
    BUILD="$ROOT/deploy/dev/frontend-build"
    test -f "$BUILD/index.html" || { echo "No bundle in $BUILD (see docs/LOCAL_DEV.md)"; exit 1; }
    "$ROOT/scripts/check-bundle-egress.sh" "$BUILD"
    docker buildx build -q --load -f "$ROOT/deploy/docker/frontend.Dockerfile" \
      --build-context config="$ROOT/deploy/docker" -t "$FE_IMAGE" "$BUILD" >/dev/null
    docker rm -f indiehr-frontend >/dev/null 2>&1 || true
    docker run -d --name indiehr-frontend -p 127.0.0.1:8080:8080 --memory 64m \
      -e CSP_API_ORIGIN=http://localhost:9000 -e CSP_STORAGE_ORIGIN=http://localhost:9100 \
      "$FE_IMAGE" >/dev/null
    echo "Web app: http://localhost:8080"
    ;;
  *) sed -n '3p' "$0"; exit 1 ;;
esac
