#!/usr/bin/env bash
# IndieHR staging stack (P1-T8a): deploy/staging/compose.yaml, run on the staging VM or, for a
# proof run, on this machine. Secrets are generated here, kept in deploy/staging/secrets/
# (mode 600, git-ignored) and handed to each container only as far as it needs them.
#
# Usage:
#   scripts/staging.sh init <suffix> [--local|--vm]  write .env and secrets (once; no overwrite)
#   scripts/staging.sh up                        start everything, then the Keycloak realm + workers
#   scripts/staging.sh smoke                     check every public address through Traefik
#   scripts/staging.sh manage <args>             one-off manage.py command (no admin credentials)
#   scripts/staging.sh ps | logs [service] | down
#   scripts/staging.sh local-proof               init (if needed) + up + smoke on this machine
#
# <suffix> is where hospitals live: <slug>.<suffix>, e.g. app.example.in (Q-T11).
# --vm: a proof on a throwaway local VM (scripts/local-vm.sh): small memory, all addresses,
#   journald logs. IHR_STAGING_OUT=<dir> makes init write .env and secrets/ there instead
#   (scripts/deploy.sh ships them to the server). On the server this script runs from
#   /srv/indiehr/scripts (no git checkout there): the folder above it is the root.
set -euo pipefail
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/.." && pwd))"
DIR="$ROOT/deploy/staging"
SECRETS="$DIR/secrets"
OUT="${IHR_STAGING_OUT:-$DIR}"
compose() {  # the file list is worked out on each call: init may have just made .local
  local files=(-f "$DIR/compose.yaml")
  [[ -f "$DIR/.local" ]] && files+=(-f "$DIR/compose.local.yaml")
  docker compose --project-directory "$DIR" "${files[@]}" "$@"
}
LOCAL_SUFFIX="app.ihr-proof.in"  # local proof only; resolved to 127.0.0.1 by curl --resolve

rand() { python3 -c "import secrets,sys;print(secrets.token_urlsafe(int(sys.argv[1])))" "$1"; }
env_get() { sed -n "s/^$1=//p" "$DIR/.env" | tail -1; }
write_secret() {  # write_secret <file> <<EOF ... EOF   (mode 600 from the first byte)
  (umask 077; cat > "${SECRETS_TARGET:-$SECRETS}/$1")
}

digest() {  # digest <image:tag>: the multi-arch index digest, pinned in .env
  docker buildx imagetools inspect "$1" --format '{{json .Manifest}}' \
    | python3 -c "import json,sys;print(json.load(sys.stdin)['digest'].split(':')[1])"
}

init() {
  local suffix="${1:?suffix, e.g. app.example.in}" local_run="${2:-}"
  [[ "$suffix" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] \
    || { echo "not a domain: $suffix" >&2; exit 1; }
  if [[ -e "$OUT/.env" || -e "$OUT/secrets" ]]; then
    echo "$OUT/.env or secrets/ already exist; refusing to overwrite secrets." >&2
    exit 1
  fi
  # Everything is made in a scratch folder and moved into place only when complete, so a
  # failure half-way leaves nothing behind that a second init would refuse to replace.
  [[ -d "$OUT" ]] || (umask 077 && mkdir -p "$OUT")
  INIT_WORK="$(mktemp -d "$OUT/.init.XXXXXX")"
  trap 'rm -rf "${INIT_WORK:-}"' EXIT
  local work="$INIT_WORK"
  local backend_image="${IHR_BACKEND_IMAGE:-indiehr-backend:staging}"
  local frontend_image="${IHR_FRONTEND_IMAGE:-indiehr-frontend:staging}"
  echo "Resolving image digests..."
  local traefik postgres redis
  traefik="$(digest traefik:v3.7.17)"
  postgres="$(digest postgres:18.6-alpine)"
  redis="$(digest redis:8.10.3-alpine)"
  mkdir -m 700 "$work/secrets"
  {
    echo "# IndieHR staging: site settings for Compose (no secrets). Written by staging.sh init."
    echo "IHR_TENANT_DOMAIN_SUFFIX=$suffix"
    echo "IHR_SUFFIX_RE=${suffix//./\\.}"
    echo "IHR_BACKEND_IMAGE=$backend_image"
    echo "IHR_FRONTEND_IMAGE=$frontend_image"
    echo "IHR_TRAEFIK_DIGEST=$traefik"
    echo "IHR_POSTGRES_DIGEST=$postgres"
    echo "IHR_REDIS_DIGEST=$redis"
    if [[ "$local_run" == "--local" ]]; then
      echo "# Proof run on this machine: only 127.0.0.1, small memory"
      echo "IHR_BIND=127.0.0.1"
      echo "IHR_PG_SHARED_BUFFERS=128MB"
      echo "IHR_PG_MEMORY=512m"
      echo "IHR_GUNICORN_WORKERS=1"
      echo "IHR_CELERY_CONCURRENCY=1"
    elif [[ "$local_run" == "--vm" ]]; then
      echo "# Proof run on a throwaway local VM (scripts/local-vm.sh): small memory; smoke"
      echo "# checks reach Traefik on the VM itself"
      echo "IHR_PG_SHARED_BUFFERS=128MB"
      echo "IHR_PG_MEMORY=512m"
      echo "IHR_GUNICORN_WORKERS=1"
      echo "IHR_CELERY_CONCURRENCY=1"
      echo "IHR_KEYCLOAK_MEMORY=900m"
      echo "IHR_SMOKE_RESOLVE=127.0.0.1"
    fi
  } > "$work/.env"

  local pg ihr kc redis_pw admin_pw admin_secret jwks
  local SECRETS_TARGET="$work/secrets"
  pg="$(rand 32)"; ihr="$(rand 32)"; kc="$(rand 32)"; redis_pw="$(rand 32)"
  admin_pw="$(rand 24)"; admin_secret="$(rand 32)"
  # --log-driver none: the private key must not reach the host's journal
  jwks="$(docker run --rm --log-driver none --network none --entrypoint python "$backend_image" -c \
    'from care.utils.jwks.generate_jwk import generate_encoded_jwks; print(generate_encoded_jwks())')"
  write_secret db.env <<EOF
POSTGRES_PASSWORD=$pg
IHR_DB_PASSWORD=$ihr
KC_DB_PASSWORD=$kc
EOF
  write_secret redis.env <<EOF
REDIS_PASSWORD=$redis_pw
EOF
  write_secret app.env <<EOF
DJANGO_SECRET_KEY=$(rand 64)
JWKS_BASE64=$jwks
DATABASE_URL=postgres://ihr:$ihr@db:5432/ihr
REDIS_URL=redis://:$redis_pw@redis:6379/0
# Object storage (S3 in ap-south-1; P1-T8b fills these in on the VM)
FILE_UPLOAD_BUCKET=ihr-staging-files
FACILITY_S3_BUCKET=ihr-staging-facility
BUCKET_REGION=ap-south-1
EOF
  # Keycloak's first-start admin: used once to create the realms and the narrow accounts,
  # passed only to one-off commands (never to the web app or the workers).
  write_secret keycloak.env <<EOF
KC_DB_PASSWORD=$kc
KC_BOOTSTRAP_ADMIN_USERNAME=ihr-admin
KC_BOOTSTRAP_ADMIN_PASSWORD=$admin_pw
KC_BOOTSTRAP_ADMIN_CLIENT_ID=ihr-bootstrap
KC_BOOTSTRAP_ADMIN_CLIENT_SECRET=$admin_secret
EOF
  for f in provisioner.env staff-worker.env anchor-read.env anchor-write.env; do
    write_secret "$f" < /dev/null
  done
  mv "$work/secrets" "$OUT/secrets"
  mv "$work/.env" "$OUT/.env"
  [[ "$local_run" == "--local" ]] && touch "$OUT/.local"
  echo "Wrote $OUT/.env and secrets/ (mode 600)."
}

admin_manage() {  # a one-off manage.py run holding the Keycloak bootstrap client (P1-T5f)
  local id secret
  id="$(sed -n 's/^KC_BOOTSTRAP_ADMIN_CLIENT_ID=//p' "$SECRETS/keycloak.env")"
  secret="$(sed -n 's/^KC_BOOTSTRAP_ADMIN_CLIENT_SECRET=//p' "$SECRETS/keycloak.env")"
  IHR_KEYCLOAK_ADMIN_CLIENT_ID="$id" IHR_KEYCLOAK_ADMIN_CLIENT_SECRET="$secret" \
    compose --profile admin run --rm --no-deps -T -e IHR_KEYCLOAK_ADMIN_CLIENT_ID \
    -e IHR_KEYCLOAK_ADMIN_CLIENT_SECRET admin python manage.py "$@"
}

worker_secret() {  # worker_secret <role> <file> <variable>
  local secret
  secret="$(admin_manage ihr_keycloak_service_secret "$1" | tail -1)"
  [[ -n "$secret" ]] || { echo "no secret for the $1 worker" >&2; exit 1; }
  printf '%s=%s\n' "$3" "$secret" | write_secret "$2"
}

up() {
  [[ -f "$DIR/.env" ]] || { echo "run: scripts/staging.sh init <suffix>" >&2; exit 1; }
  compose up -d --wait db redis
  compose up -d --wait traefik keycloak backend celery-worker celery-beat frontend
  admin_manage ihr_keycloak_sync --realm --all
  worker_secret onboarding provisioner.env IHR_KEYCLOAK_ONBOARDING_SECRET
  worker_secret staff staff-worker.env IHR_KEYCLOAK_STAFF_SECRET
  compose up -d --force-recreate provisioner staff-worker
  compose ps --format 'table {{.Service}}\t{{.Status}}'
}

smoke() {  # every public address through Traefik, as a browser would reach it
  local suffix port bind resolve fail=0
  suffix="$(env_get IHR_TENANT_DOMAIN_SUFFIX)"
  port="$(env_get IHR_HTTPS_PORT)"; port="${port:-443}"
  bind="$(env_get IHR_BIND)"
  # Where the names point for the checks: the bind address of a local proof, or an address
  # given in .env / the environment (a VM proof, or a server before its DNS exists)
  resolve="${IHR_SMOKE_RESOLVE:-$(env_get IHR_SMOKE_RESOLVE)}"
  [[ -z "$resolve" && "$bind" == "127.0.0.1" ]] && resolve=127.0.0.1
  local curl_args=(-sk --max-time 20)
  if [[ -n "$resolve" ]]; then
    for host in "$suffix" "alpha.$suffix" "console.$suffix" "auth.$suffix" "nope.$suffix"; do
      curl_args+=(--resolve "$host:$port:$resolve" --resolve "$host:${IHR_HTTP_PORT:-80}:$resolve")
    done
  fi
  check() {  # check <description> <expected> <actual>
    if [[ "$3" == "$2" ]]; then echo "ok    $1 ($3)"; else echo "FAIL  $1: expected $2, got $3"; fail=1; fi
  }
  code() { curl "${curl_args[@]}" -o /dev/null -w '%{http_code}' "$@"; }
  header() { curl "${curl_args[@]}" -o /dev/null -D - "$1" | tr -d '\r' | sed -n "s/^$2: //Ip" | head -1; }

  check "http redirects to https" "301 https://console.$suffix/ops/" \
    "$(curl "${curl_args[@]}" -o /dev/null -w '%{http_code} %{redirect_url}' "http://console.$suffix:${IHR_HTTP_PORT:-80}/ops/")"
  check "operator console" 302 "$(code "https://console.$suffix:$port/ops/")"
  check "console: back-channel logout not public" 418 "$(code -X POST "https://console.$suffix:$port/api/ihr/backchannel-logout/")"
  check "console: API not public" 418 "$(code "https://console.$suffix:$port/api/v1/users/")"
  check "console: health not public" 418 "$(code "https://console.$suffix:$port/health/")"
  check "Keycloak hospitals' realm" 200 "$(code "https://auth.$suffix:$port/realms/indiehr/.well-known/openid-configuration")"
  check "Keycloak operators' realm" 200 "$(code "https://auth.$suffix:$port/realms/indiehr-ops/.well-known/openid-configuration")"
  check "Keycloak admin console not public" 418 "$(code "https://auth.$suffix:$port/admin/master/console/")"
  check "Keycloak master realm not public" 418 "$(code "https://auth.$suffix:$port/realms/master/.well-known/openid-configuration")"
  local issuer
  issuer="$(curl "${curl_args[@]}" "https://auth.$suffix:$port/realms/indiehr/.well-known/openid-configuration" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin).get("issuer",""))' 2>/dev/null || true)"
  check "token issuer is the public https address" "https://auth.$suffix/realms/indiehr" "$issuer"
  check "unknown hospital address: no branding" 404 "$(code "https://nope.$suffix:$port/api/ihr/bootstrap/")"
  check "bare suffix not served" 404 "$(code "https://$suffix:$port/")"
  check "HSTS on the console" "max-age=31536000" "$(header "https://console.$suffix:$port/ops/" strict-transport-security)"
  if [[ "$(code "https://alpha.$suffix:$port/api/ihr/bootstrap/")" == 200 ]]; then
    check "hospital web app" 200 "$(code "https://alpha.$suffix:$port/")"
    check "hospital branding (public)" 200 "$(code "https://alpha.$suffix:$port/api/ihr/bootstrap/")"
    check "hospital profile needs sign-in" 403 "$(code "https://alpha.$suffix:$port/api/ihr/profile/")"
    check "CARE's own staff login is off" 404 "$(code -X POST "https://alpha.$suffix:$port/api/v1/auth/login/")"
    check "HSTS on the web app" "max-age=31536000; includeSubDomains" "$(header "https://alpha.$suffix:$port/" strict-transport-security)"
    # CARE in production mode: no public API schema, no fixed patient sign-in code
    check "API schema not public" 404 "$(code "https://alpha.$suffix:$port/api/schema/")"
    # Searches and setup links travel in query strings: no log may keep them
    local probe="ihrprobe$RANDOM$RANDOM"
    code "https://alpha.$suffix:$port/api/ihr/bootstrap/?q=$probe" >/dev/null
    code "https://auth.$suffix:$port/realms/indiehr/login-actions/action-token?key=$probe" >/dev/null
    sleep 2
    check "no query strings in the logs" 0 "$(compose logs --no-color traefik frontend backend 2>/dev/null | grep -c "$probe" || true)"
  else
    echo "skip  hospital checks: no hospital 'alpha' yet (scripts/staging.sh manage ihr_create_tenant ...)"
  fi
  return "$fail"
}

case "${1:-}" in
  init) init "${@:2}" ;;
  up) up ;;
  smoke) smoke ;;
  manage) compose --profile admin run --rm --no-deps -T admin python manage.py "${@:2}" ;;
  ps) compose ps ;;
  logs) compose logs --tail=200 "${@:2}" ;;
  down) compose down ;;
  local-proof)
    [[ -f "$DIR/.env" ]] || IHR_FRONTEND_IMAGE="${IHR_FRONTEND_IMAGE:-indiehr-frontend:local}" \
      init "$LOCAL_SUFFIX" --local
    up
    # A synthetic hospital for the address checks (no Keycloak client: sign-in is not tested)
    compose --profile admin run --rm --no-deps -T admin python manage.py ihr_create_tenant \
      --slug alpha --name "Alpha Proof Hospital" --domain "alpha.$(env_get IHR_TENANT_DOMAIN_SUFFIX)" \
      >/dev/null 2>&1 || true
    smoke
    ;;
  *) sed -n '5,17p' "$0"; exit 1 ;;
esac
