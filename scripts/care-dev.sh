#!/usr/bin/env bash
# Run upstream CARE locally with the IndieHR low-memory override (P0-T5), in tenant mode with
# two synthetic demo hospitals at http://alpha.localhost:8080 and http://beta.localhost:8080
# (P1-T2b). Usage: scripts/care-dev.sh <up|down|ps|logs|seed|reset-passwords|frontend|shell>
set -euo pipefail
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
BACKEND="$ROOT/apps/backend"
COMPOSE=(docker compose --project-directory "$BACKEND" -p indiehr-care
  -f "$BACKEND/docker-compose.yaml" -f "$BACKEND/docker-compose.local.yaml"
  -f "$ROOT/deploy/dev/care-local.override.yaml")
FE_IMAGE=indiehr-frontend:local
DB=ihr_dev
HOSPITALS=(alpha beta)

new_password() { echo "Ihr-$(python3 -c 'import secrets;print(secrets.token_urlsafe(9))')-7"; }

# seed_hospital <slug> [--passwords-only]: prints "<slug> <password>"
seed_hospital() {
  local pw; pw="$(new_password)"
  "${COMPOSE[@]}" exec -T -e TEST_PASSWORD="$pw" backend \
    python manage.py ihr_seed_demo --slug "$1" "${@:2}" | tail -1 >&2
  echo "$1 $pw"
}

# write_test_users "alpha <pw>" "beta <pw>"
write_test_users() {
  mkdir -p "$ROOT/deploy/seed"
  {
    cat <<EOT
# Local test users — synthetic, localhost only, NOT committed

Generated $(date '+%d-%m-%Y %H:%M') by \`scripts/care-dev.sh\`. CARE's public fixture passwords were replaced.
Each demo hospital is a separate tenant with its own users: the same username exists in both,
with a **different password** in each.

EOT
    for entry in "$@"; do
      read -r slug pw <<<"$entry"
      cat <<EOT
## ${slug^} — http://$slug.localhost:8080

**Password for every user below:** \`$pw\`

EOT
    done
    cat <<'EOT'
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
| admin | Superuser | Not for the web app |
EOT
  } > "$ROOT/deploy/seed/TEST_USERS.md"
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
    # The tenant-mode database is created once; migrations run when the celery container starts.
    "${COMPOSE[@]}" up -d --wait db
    if [[ -z "$("${COMPOSE[@]}" exec -T db psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DB'")" ]]; then
      "${COMPOSE[@]}" exec -T db psql -U postgres -qc "CREATE DATABASE $DB"
    fi
    "${COMPOSE[@]}" up -d --wait
    echo "API is up. Next: scripts/care-dev.sh seed (first time), then scripts/care-dev.sh frontend"
    ;;
  down) "${COMPOSE[@]}" down; docker rm -f indiehr-frontend >/dev/null 2>&1 || true ;;
  ps) "${COMPOSE[@]}" ps; docker ps --filter name=indiehr-frontend --format '{{.Names}} {{.Status}} {{.Ports}}' ;;
  logs) "${COMPOSE[@]}" logs --tail=100 "${@:2}" ;;
  seed)
    # Two synthetic hospitals with CARE's demo data, and fresh passwords per hospital (P0-T11)
    entries=()
    for slug in "${HOSPITALS[@]}"; do entries+=("$(seed_hospital "$slug")"); done
    write_test_users "${entries[@]}"
    echo "Test logins written to deploy/seed/TEST_USERS.md"
    ;;
  reset-passwords)
    entries=()
    for slug in "${HOSPITALS[@]}"; do entries+=("$(seed_hospital "$slug" --passwords-only)"); done
    write_test_users "${entries[@]}"
    echo "New passwords written to deploy/seed/TEST_USERS.md"
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
    # Same-origin API: nginx passes each hospital's host through to the backend.
    docker run -d --name indiehr-frontend -p 127.0.0.1:8080:8080 --memory 64m \
      --network care -e API_UPSTREAM=http://backend:9000 \
      -e CSP_STORAGE_ORIGIN=http://localhost:9100 \
      "$FE_IMAGE" >/dev/null
    for slug in "${HOSPITALS[@]}"; do echo "Web app: http://$slug.localhost:8080"; done
    ;;
  *) sed -n '3p' "$0"; exit 1 ;;
esac
