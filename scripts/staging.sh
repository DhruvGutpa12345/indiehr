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
#   scripts/staging.sh acme-init <ca> <acme-zone> <ns-name>   add the wildcard certificate (P1-T8b2)
#   scripts/staging.sh acme-register [--reset]   acme-dns account + the records for the registrar
#   scripts/staging.sh cert [--wait SECONDS]     the certificate Traefik serves now (from the right CA)
#   scripts/staging.sh acme-retry                ask for the certificate again (after DNS changes)
#   scripts/staging.sh acme-new-cert             drop the stored certificate (after changing the CA)
#   scripts/staging.sh acme-proof [--keep]       certificate proof with Pebble, alone, on this machine
#   scripts/staging.sh s3-init                   files on the server itself: SeaweedFS (P1-T8b3)
#   scripts/staging.sh s3-check                  live checks of its keys, locks and public paths
#
# <suffix> is where hospitals live: <slug>.<suffix>, e.g. app.example.in (Q-T11).
# --vm: a proof on a throwaway local VM (scripts/local-vm.sh): small memory, all addresses,
#   journald logs. IHR_STAGING_OUT=<dir> makes init write .env and secrets/ there instead
#   (scripts/deploy.sh ships them to the server). On the server this script runs from
#   /srv/indiehr/scripts (no git checkout there): the folder above it is the root.
#
# Certificates (P1-T8b2, compose.acme.yaml): acme-init <ca> picks letsencrypt,
# letsencrypt-staging or pebble (a proof with Let's Encrypt's test CA). <acme-zone> is the small
# zone acme-dns answers for, <ns-name> the name the registrar's NS record points at, e.g.
#   scripts/staging.sh acme-init letsencrypt acme.amhcloud.in acme-ns.amhcloud.in
set -euo pipefail
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/.." && pwd))"
# IHR_STAGING_DIR: a copy of this folder (acme-proof runs from a throwaway one)
DIR="${IHR_STAGING_DIR:-$ROOT/deploy/staging}"
SECRETS="$DIR/secrets"
OUT="${IHR_STAGING_OUT:-$DIR}"
GEN="$DIR/generated"  # files made from .env on each start (names only, no secrets)
compose() {  # the file list is worked out on each call: init may have just made .local
  local files=(-f "$DIR/compose.yaml") acme
  [[ -f "$DIR/.local" ]] && files+=(-f "$DIR/compose.local.yaml")
  acme="$(env_get IHR_ACME)"
  if [[ -n "$acme" ]]; then
    files+=(-f "$DIR/compose.acme.yaml")
    [[ "$acme" == pebble ]] && files+=(-f "$DIR/compose.pebble.yaml")
  fi
  [[ "$(env_get IHR_S3)" == local ]] && files+=(-f "$DIR/compose.s3.yaml")
  docker compose --project-directory "$DIR" ${IHR_PROJECT:+-p "$IHR_PROJECT"} "${files[@]}" "$@"
}
LOCAL_SUFFIX="app.ihr-proof.in"  # local proof only; resolved to 127.0.0.1 by curl --resolve

rand() { python3 -c "import secrets,sys;print(secrets.token_urlsafe(int(sys.argv[1])))" "$1"; }
env_get() { [[ -f "$DIR/.env" ]] || return 0; sed -n "s/^$1=//p" "$DIR/.env" | tail -1; }
die() { echo "$*" >&2; exit 1; }
DOMAIN_RE='^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
write_secret() {  # write_secret <file> <<EOF ... EOF   (mode 600 from the first byte)
  (umask 077; cat > "${SECRETS_TARGET:-$SECRETS}/$1")
}

digest() {  # digest <image:tag>: the multi-arch index digest, pinned in .env
  docker buildx imagetools inspect "$1" --format '{{json .Manifest}}' \
    | python3 -c "import json,sys;print(json.load(sys.stdin)['digest'].split(':')[1])"
}

init() {
  local suffix="${1:?suffix, e.g. app.example.in}" local_run="${2:-}"
  [[ "$suffix" =~ $DOMAIN_RE ]] || die "not a domain: $suffix"
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
      echo "# Proof run on this machine: only 127.0.0.1, small memory, Docker's own log files"
      echo "# (removed with the stack) instead of this machine's journal"
      echo "IHR_BIND=127.0.0.1"
      echo "IHR_LOG_DRIVER=json-file"
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
  # Removed here too, not only by the EXIT trap: a later step in the same run may replace that
  # trap (acme_register sets its own)
  rm -rf "$work"; INIT_WORK=""
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
  [[ -z "$(env_get IHR_ACME)" ]] || acme_up
  [[ "$(env_get IHR_S3)" != local ]] || s3_up
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
    for host in "$suffix" "alpha.$suffix" "console.$suffix" "auth.$suffix" "nope.$suffix" "files.$suffix"; do
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
  if [[ "$(env_get IHR_S3)" == local ]]; then
    check "files: nothing at the root" 418 "$(code "https://files.$suffix:$port/")"
    check "files: fingerprint bucket not public" 418 "$(code "https://files.$suffix:$port/$(env_get IHR_S3_ANCHOR_BUCKET)/anchors/x")"
    check "files: patient files need a signature" 403 "$(code "https://files.$suffix:$port/$(env_get IHR_S3_PATIENT_BUCKET)/x")"
    check "files: hospital pictures are public (none here yet)" 404 "$(code "https://files.$suffix:$port/$(env_get IHR_S3_FACILITY_BUCKET)/no-such-picture.png")"
  fi
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

# ---- Object storage on the server: SeaweedFS (P1-T8b3, compose.s3.yaml) ------------------------
S3_ANCHOR_BUCKET_DEFAULT="ihr-audit-anchors"
BUCKET_RE='^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$'

s3_init() {  # SeaweedFS keys into secrets/, CARE's and the fingerprint keys into their env files
  local envf="$OUT/.env" sec="$OUT/secrets" suffix patient facility k tmp
  [[ -f "$envf" && -d "$sec" ]] || die "no $envf / secrets: run scripts/staging.sh init <suffix> first"
  grep -q '^IHR_S3=' "$envf" && die "$envf already has object storage settings (IHR_S3=...)"
  suffix="$(sed -n 's/^IHR_TENANT_DOMAIN_SUFFIX=//p' "$envf" | tail -1)"
  patient="$(sed -n 's/^FILE_UPLOAD_BUCKET=//p' "$sec/app.env" | tail -1)"
  facility="$(sed -n 's/^FACILITY_S3_BUCKET=//p' "$sec/app.env" | tail -1)"
  [[ "$patient" =~ $BUCKET_RE && "$facility" =~ $BUCKET_RE && "$patient" != "$facility" ]] \
    || die "FILE_UPLOAD_BUCKET / FACILITY_S3_BUCKET in secrets/app.env are not two bucket names"
  hex() { python3 -c "import secrets;print(secrets.token_hex($1))"; }
  declare -A key
  for k in ADMIN CARE ANCHOR_WRITE ANCHOR_READ; do key[${k}_KEY]="ihr$(hex 10)"; key[${k}_SECRET]="$(hex 20)"; done
  local SECRETS_TARGET="$sec"
  {
    echo "# SeaweedFS identities (P1-T8b3), written by scripts/staging.sh s3-init; the s3 container only."
    for k in ADMIN CARE ANCHOR_WRITE ANCHOR_READ; do
      echo "IHR_S3_${k}_KEY=${key[${k}_KEY]}"
      echo "IHR_S3_${k}_SECRET=${key[${k}_SECRET]}"
    done
  } | write_secret s3-gateway.env
  # CARE: its own key, the internal address, and the public one for presigned URLs
  tmp="$(grep -Ev '^(BUCKET_KEY|BUCKET_SECRET|BUCKET_ENDPOINT|BUCKET_EXTERNAL_ENDPOINT)=' "$sec/app.env"
    echo "# Object storage on this server (P1-T8b3, scripts/staging.sh s3-init)"
    echo "BUCKET_ENDPOINT=http://s3:8333"
    echo "BUCKET_EXTERNAL_ENDPOINT=https://files.$suffix"
    echo "BUCKET_KEY=${key[CARE_KEY]}"
    echo "BUCKET_SECRET=${key[CARE_SECRET]}")"
  printf '%s\n' "$tmp" | write_secret app.env.new
  mv "$sec/app.env.new" "$sec/app.env"
  printf 'IHR_AUDIT_ANCHOR_ENDPOINT=http://s3:8333\nIHR_AUDIT_ANCHOR_BUCKET=%s\nIHR_AUDIT_ANCHOR_WRITE_KEY=%s\nIHR_AUDIT_ANCHOR_WRITE_SECRET=%s\n' \
    "$S3_ANCHOR_BUCKET_DEFAULT" "${key[ANCHOR_WRITE_KEY]}" "${key[ANCHOR_WRITE_SECRET]}" | write_secret anchor-write.env
  printf 'IHR_AUDIT_ANCHOR_ENDPOINT=http://s3:8333\nIHR_AUDIT_ANCHOR_BUCKET=%s\nIHR_AUDIT_ANCHOR_READ_KEY=%s\nIHR_AUDIT_ANCHOR_READ_SECRET=%s\n' \
    "$S3_ANCHOR_BUCKET_DEFAULT" "${key[ANCHOR_READ_KEY]}" "${key[ANCHOR_READ_SECRET]}" | write_secret anchor-read.env
  {
    echo "# Object storage on this server: SeaweedFS (P1-T8b3; scripts/staging.sh s3-init)"
    echo "IHR_S3=local"
    echo "IHR_S3_PATIENT_BUCKET=$patient"
    echo "IHR_S3_FACILITY_BUCKET=$facility"
    echo "IHR_S3_ANCHOR_BUCKET=$S3_ANCHOR_BUCKET_DEFAULT"
    echo "# Audit fingerprints: kept 10 years, removable by nobody through S3 (ADR-010 §9)"
    echo "IHR_S3_LOCK_MODE=COMPLIANCE"
    echo "IHR_S3_LOCK_DAYS=3650"
    echo "IHR_STORAGE_ORIGIN=https://files.$suffix"
  } >> "$envf"
  echo "Object storage settings added to $envf; keys in $sec (mode 600)."
}

s3_tool() {  # s3_tool <script> <args>: a deploy/s3 script with the S3_* keys through the environment
  local script="$1" k v envs=()
  shift
  for k in ADMIN CARE ANCHOR_WRITE ANCHOR_READ; do
    for v in KEY SECRET; do
      export "S3_${k}_${v}=$(sed -n "s/^IHR_S3_${k}_${v}=//p" "$SECRETS/s3-gateway.env")"
      envs+=(-e "S3_${k}_${v}")
    done
  done
  compose --profile admin run --rm --no-deps -T "${envs[@]}" s3-tool "/ihr-s3/$script" \
    --endpoint http://s3:8333 --patient "$(env_get IHR_S3_PATIENT_BUCKET)" \
    --facility "$(env_get IHR_S3_FACILITY_BUCKET)" --anchor "$(env_get IHR_S3_ANCHOR_BUCKET)" "$@"
}

s3_up() {  # SeaweedFS, then its buckets (idempotent)
  compose up -d --wait s3
  s3_tool setup_buckets.py --lock-mode "$(env_get IHR_S3_LOCK_MODE)" --lock-days "$(env_get IHR_S3_LOCK_DAYS)" \
    --cors-origin "https://*.$(env_get IHR_TENANT_DOMAIN_SUFFIX)"
}

s3_check() {  # every key and lock, and the public paths through Traefik (files.<suffix>)
  local suffix; suffix="$(env_get IHR_TENANT_DOMAIN_SUFFIX)"
  # files.<suffix> is Traefik on the files network (port 8443 inside); its certificate is
  # checked by `cert` and smoke, so these requests skip TLS verification
  s3_tool check_keys.py --public-endpoint "https://files.$suffix:8443" --origin "https://alpha.$suffix" --insecure --size-limit
}

# ---- Wildcard certificate (P1-T8b2, compose.acme.yaml) ---------------------------------------
dns_reply() {  # dns_reply <server> <port> [dig args]: "<status> <answers>", or "noreply"
  # (dig prints its own errors on stdout, so only a real reply's header counts)
  local out st an
  out="$(dig +time=3 +tries=1 @"$1" -p "$2" "${@:3}" 2>/dev/null || true)"
  st="$(sed -n 's/.*->>HEADER<<-.* status: \([A-Z]*\),.*/\1/p' <<< "$out")"
  an="$(sed -n 's/.* ANSWER: \([0-9]*\),.*/\1/p' <<< "$out")"
  if [[ -n "$st" ]]; then echo "$st ${an:-0}"; else echo noreply; fi
}

ACME_API="http://acme-dns-api:8053"  # acme-dns's API, only on the internal network it shares with Traefik

acme_init() {  # acme_init <letsencrypt|letsencrypt-staging|pebble> <acme-dns zone> <its NS name>
  local ca="${1:-}" zone="${2:-}" ns="${3:-}" suffix server net envf="$OUT/.env"
  [[ -f "$envf" ]] || die "no $envf: run scripts/staging.sh init <suffix> first"
  grep -q '^IHR_ACME=' "$envf" && die "$envf already has certificate settings (IHR_ACME=...)"
  suffix="$(sed -n 's/^IHR_TENANT_DOMAIN_SUFFIX=//p' "$envf" | tail -1)"
  case "$ca" in
    letsencrypt) server=https://acme-v02.api.letsencrypt.org/directory ;;
    letsencrypt-staging) server=https://acme-staging-v02.api.letsencrypt.org/directory ;;
    pebble) server=https://pebble:14000/dir ;;
    *) die "usage: scripts/staging.sh acme-init <letsencrypt|letsencrypt-staging|pebble> <acme-zone> <ns-name>" ;;
  esac
  [[ "$zone" =~ $DOMAIN_RE && "$ns" =~ $DOMAIN_RE ]] || die "not domain names: '$zone' '$ns'"
  # The NS name lies outside the zone it serves, so the registrar needs a plain A record for
  # it (no glue); neither lies under the hospitals' suffix (its wildcard A record covers those).
  [[ "$ns" == "$zone" || "$ns" == *".$zone" ]] && die "$ns must not be inside $zone"
  local n
  for n in "$zone" "$ns"; do
    [[ "$n" == "$suffix" || "$n" == *".$suffix" ]] && die "$n must not be under the hospitals' suffix $suffix"
  done
  if [[ "$ca" == pebble && ( "${ns#*.}" == "$suffix" || "${ns#*.}" == "$zone" ) ]]; then
    die "proof: $ns's parent zone must differ from $suffix and $zone"
  fi
  net="${IHR_PROOF_NET:-172.30.54}"
  {
    echo "# Wildcard certificate for <suffix> and *.<suffix> (P1-T8b2; scripts/staging.sh acme-init)"
    echo "IHR_ACME=$ca"
    echo "IHR_ACME_CA_SERVER=$server"
    echo "IHR_ACME_DNS_ZONE=$zone"
    echo "IHR_ACME_DNS_NS=$ns"
    echo "IHR_ACMEDNS_IMAGE=${IHR_ACMEDNS_IMAGE:-indiehr-acme-dns:staging}"
    if [[ "$ca" == pebble ]]; then
      echo "# Proof only: Pebble's test root, CoreDNS in place of the public DNS, and 23-minute"
      echo "# certificates with a 1-hour setting, so Traefik renews about every 3 minutes"
      echo "IHR_ACME_CA_CERTS=/proof/pebble.minica.pem"
      echo "IHR_ACME_CERT_HOURS=1"
      echo "IHR_PROOF_CERT_SECONDS=1380"
      echo "IHR_PROOF_SUBNET=$net.0/24"
      echo "IHR_PROOF_COREDNS_IP=$net.10"
      echo "IHR_PROOF_ACMEDNS_IP=$net.53"
      echo "IHR_PROOF_DNS_VIA=${IHR_PROOF_DNS_VIA:-container}"
    fi
  } >> "$envf"
  echo "Added certificate settings ($ca) to $envf."
}

acme_dns_config() {  # acme_dns_config <open|closed>: acme-dns's settings, registration open or closed
  local zone ns tmp
  zone="$(env_get IHR_ACME_DNS_ZONE)"; ns="$(env_get IHR_ACME_DNS_NS)"
  [[ "$zone" =~ $DOMAIN_RE && "$ns" =~ $DOMAIN_RE ]] || die "IHR_ACME_DNS_ZONE / IHR_ACME_DNS_NS missing in .env"
  mkdir -p "$GEN/acme-dns"
  tmp="$(mktemp "$GEN/acme-dns/.config.XXXXXX")"
  cat > "$tmp" <<EOF
# Written by scripts/staging.sh from .env (P1-T8b2); changes here are overwritten.
[general]
# Authoritative for $zone only: it never looks up other names for anyone
listen = "0.0.0.0:53"
protocol = "both"
domain = "$zone"
nsname = "$ns"
nsadmin = "hostmaster.$zone"
records = ["$zone. NS $ns."]
debug = false

[database]
engine = "sqlite"
connection = "/var/lib/acme-dns/acme-dns.db"

[api]
# Plain HTTP, only on acme-dns's fixed address on the internal network (no published port)
ip = "$(acme_api_ip)"
port = "8053"
tls = "none"
disable_registration = $([[ "$1" == open ]] && echo false || echo true)
corsorigins = []
use_header = false
header_name = "X-Forwarded-For"

[logconfig]
loglevel = "info"
logtype = "stdout"
logformat = "json"
EOF
  chmod 644 "$tmp"
  mv "$tmp" "$GEN/acme-dns/config.cfg"
}

traefik_sh() {  # traefik_sh <script>: a one-off shell in Traefik's image, as Traefik's user, with
  # its data volume and networks. Its output reaches the log driver: never print a secret.
  compose run --rm --no-deps -T --entrypoint sh traefik -c "$1"
}

acme_api_ip() { local v; v="$(env_get IHR_ACME_API_IP)"; echo "${v:-172.30.53.5}"; }

acme_fulldomain() {  # the registered acme-dns name; nothing when there is no account yet
  local out
  # shellcheck disable=SC2016  # expanded by the container's shell
  out="$(traefik_sh 'if [ ! -e /data/acme-dns-accounts.json ]; then echo NONE; exit 0; fi
    v=$(sed -n "s/.*\"fulldomain\": *\"\([^\"]*\)\".*/\1/p" /data/acme-dns-accounts.json)
    echo "${v:-UNREADABLE}"')" || die "could not read Traefik's acme-dns account file"
  case "$out" in
    NONE) ;;
    *[!a-z0-9.-]*|"") die "Traefik's acme-dns account file is unreadable ($out); start over with: scripts/staging.sh acme-register --reset" ;;
    *) echo "$out" ;;
  esac
}

# Sends one update with the account's own key (read inside the container, never printed) to
# the acme-dns API at $1; prints "accepted" or "refused". The value is a dummy of the right length.
# shellcheck disable=SC2016  # expanded by the container's shell, not here
ACME_UPDATE_SH='u=$(sed -n "s/.*\"username\":\"\([^\"]*\)\".*/\1/p" /data/acme-dns-accounts.json)
  k=$(sed -n "s/.*\"password\":\"\([^\"]*\)\".*/\1/p" /data/acme-dns-accounts.json)
  s=$(sed -n "s/.*\"subdomain\":\"\([^\"]*\)\".*/\1/p" /data/acme-dns-accounts.json)
  wget -q -T 10 -O /dev/null --header "X-Api-User: $u" --header "X-Api-Key: $k" \
    --post-data "{\"subdomain\":\"$s\",\"txt\":\"ihrcheckihrcheckihrcheckihrcheckihrcheckihr\"}" "$1/update" 2>/dev/null \
    && echo accepted || echo refused'

acme_close_registration() {  # back to the closed settings (also when acme_register is interrupted)
  acme_dns_config closed
  compose up -d --no-deps --force-recreate acme-dns
}

acme_register() {  # acme_register [--quiet|--reset]: one acme-dns account for <suffix> and *.<suffix>
  local suffix zone ns subnet full rc=0 fresh=0 zre sre
  suffix="$(env_get IHR_TENANT_DOMAIN_SUFFIX)"; zone="$(env_get IHR_ACME_DNS_ZONE)"
  ns="$(env_get IHR_ACME_DNS_NS)"; subnet="$(env_get IHR_ACME_SUBNET)"; subnet="${subnet:-172.30.53.0/28}"
  zre="${zone//./\\.}"; sre="${subnet//./\\.}"  # as regular expressions
  compose run --rm --no-deps -T traefik-data >/dev/null
  if [[ "${1:-}" == --reset ]]; then
    # The old account stays in the volume under another name (never deleted here)
    # shellcheck disable=SC2016  # the container's shell runs date
    traefik_sh 'test ! -e /data/acme-dns-accounts.json || mv /data/acme-dns-accounts.json "/data/acme-dns-accounts.json.replaced-$(date +%Y%m%d%H%M%S)"'
    echo "Old acme-dns account set aside; registering a new one (the CNAME at the registrar must change)."
  fi
  full="$(acme_fulldomain)"
  if [[ -z "$full" ]]; then
    fresh=1
    echo "Registering an acme-dns account for $suffix (registration is open only meanwhile)"
    acme_dns_config open
    # Closed again however this ends (error, Ctrl-C, lost SSH session)
    trap acme_close_registration EXIT
    trap 'exit 130' INT TERM HUP
    compose up -d --no-deps --force-recreate acme-dns
    # The account (with its key) goes straight into Traefik's volume, mode 600, never printed.
    # Updates are accepted only from the internal network (allowfrom), i.e. from Traefik.
    traefik_sh "set -e
      trap 'rm -f /data/.acme-register.json' EXIT
      i=0; until wget -q -O /dev/null $ACME_API/health; do i=\$((i+1)); [ \$i -lt 30 ]; sleep 1; done
      umask 077
      wget -q -O /data/.acme-register.json --header 'Content-Type: application/json' \
        --post-data '{\"allowfrom\":[\"$subnet\"]}' $ACME_API/register
      grep -Eq '^\{\"username\":\"[0-9a-f-]{36}\",\"password\":\"[A-Za-z0-9_-]{40}\",\"fulldomain\":\"[0-9a-f-]{36}\.$zre\",\"subdomain\":\"[0-9a-f-]{36}\",\"allowfrom\":\[\"$sre\"\]\}\$' /data/.acme-register.json
      sed -e 's|^{|{\"$suffix\":{\"server_url\":\"$ACME_API\",|' -e 's|\$|}|' /data/.acme-register.json \
        > /data/acme-dns-accounts.json.new
      mv /data/acme-dns-accounts.json.new /data/acme-dns-accounts.json" || rc=$?
    acme_close_registration
    trap - EXIT INT TERM HUP
    ((rc == 0)) || die "acme-dns registration failed (registration is closed again)"
    full="$(acme_fulldomain)"
  fi
  [[ "$full" =~ ^[0-9a-f-]{36}\.${zone//./\\.}$ ]] || die "unexpected acme-dns account name: '$full'"
  ACME_FULLDOMAIN="$full"
  # acme-dns must still know the key Traefik holds (its volume could have been lost or restored
  # from another date): one update with that key, from Traefik's network
  local i=0 known
  until known="$(traefik_sh "set -- $ACME_API; $ACME_UPDATE_SH")" && [[ "$known" == accepted ]]; do
    i=$((i + 1))
    ((i < 5)) || die "acme-dns refuses Traefik's key for $full (acme-dns's volume lost?). Start over: scripts/staging.sh acme-register --reset, then change the CNAME at the registrar"
    sleep 2
  done
  if [[ "$(env_get IHR_ACME)" == pebble ]]; then
    echo "acme-dns account: $full (proof: the registrar's records are written into CoreDNS)"
  elif ((fresh)) || [[ "${1:-}" != --quiet ]]; then
    cat <<EOF

DNS records at the registrar (once; <IP> is this server's public IPv4 address):
  $ns.                  A      <IP>
  $zone.                  NS     $ns.
  _acme-challenge.$suffix.   CNAME  $full.
  *.$suffix.                 A      <IP>      (every hospital; if not there yet)
When they resolve, run: scripts/staging.sh acme-retry
(If it still fails: this server's resolver may remember "no such record" from before the CNAME
existed, for up to the zone's negative-cache time, often an hour. Wait, then acme-retry again.)
EOF
  else
    echo "acme-dns account: $full"
  fi
}

acme_proof_files() {  # acme_proof_files [<acme-dns name>]: CoreDNS's zones and Pebble's settings
  local suffix zone ns parent target full="${1:-}" d="$GEN/acme-proof" serial cid
  suffix="$(env_get IHR_TENANT_DOMAIN_SUFFIX)"; zone="$(env_get IHR_ACME_DNS_ZONE)"
  ns="$(env_get IHR_ACME_DNS_NS)"; parent="${ns#*.}"
  if [[ "$(env_get IHR_PROOF_DNS_VIA)" == host ]]; then
    # The server's own address: questions to acme-dns go through the published port and the
    # firewall (DOCKER-USER), as Let's Encrypt's would
    target="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')"
    [[ -n "$target" ]] || die "proof: no IPv4 address found on this host"
  else
    target="$(env_get IHR_PROOF_ACMEDNS_IP)"
  fi
  mkdir -p "$d"
  if [[ ! -s "$d/pebble.minica.pem" ]]; then  # Pebble's public test root for its own API
    cid="$(docker create "$(compose config --format json pebble \
      | python3 -c 'import json,sys;print(json.load(sys.stdin)["services"]["pebble"]["image"])')")"
    docker cp "$cid:/test/certs/pebble.minica.pem" "$d/pebble.minica.pem" >/dev/null
    docker rm "$cid" >/dev/null
    chmod 644 "$d/pebble.minica.pem"
  fi
  serial="$(date +%s)"
  cat > "$d/Corefile" <<EOF
# Proof DNS (P1-T8b2), written by scripts/staging.sh. Most specific zone wins.
$suffix:53 {
  file /proof/db.suffix
  errors
}
$parent:53 {
  file /proof/db.parent
  errors
}
$zone:53 {
  forward . $target:53
  errors
}
EOF
  cat > "$d/db.suffix" <<EOF
\$ORIGIN $suffix.
\$TTL 60
@ IN SOA ns.$parent. hostmaster.$parent. $serial 3600 600 86400 60
@ IN NS ns.$parent.
@ IN A 127.0.0.1
* IN A 127.0.0.1
$([[ -n "$full" ]] && echo "_acme-challenge IN CNAME $full.")
EOF
  cat > "$d/db.parent" <<EOF
\$ORIGIN $parent.
\$TTL 60
@ IN SOA ns.$parent. hostmaster.$parent. $serial 3600 600 86400 60
@ IN NS ns.$parent.
ns IN A 127.0.0.1
${ns%."$parent"} IN A $target
${zone%."$parent"} IN NS $ns.
EOF
  cat > "$d/pebble-config.json" <<EOF
{"pebble": {
  "listenAddress": "0.0.0.0:14000", "managementListenAddress": "0.0.0.0:15000",
  "certificate": "test/certs/localhost/cert.pem", "privateKey": "test/certs/localhost/key.pem",
  "httpPort": 5002, "tlsPort": 5001, "ocspResponderURL": "", "externalAccountBindingRequired": false,
  "retryAfter": {"authz": 3, "order": 5}, "keyAlgorithm": "ecdsa",
  "profiles": {"default": {"description": "IndieHR proof", "validityPeriod": $(env_get IHR_PROOF_CERT_SECONDS)}}
}}
EOF
  chmod 644 "$d"/Corefile "$d"/db.suffix "$d"/db.parent "$d"/pebble-config.json
}

acme_up() {  # acme-dns (and in a proof CoreDNS and Pebble) before Traefik starts
  local img
  img="$(env_get IHR_ACMEDNS_IMAGE)"
  docker image inspect "$img" >/dev/null 2>&1 \
    || die "image $img is not here (scripts/deploy.sh builds and sends it; locally: docker buildx build -f deploy/docker/acme-dns.Dockerfile -t $img deploy/docker)"
  acme_dns_config closed
  [[ "$(env_get IHR_ACME)" == pebble ]] && acme_proof_files ""
  compose up -d --no-deps --force-recreate acme-dns
  acme_register --quiet
  if [[ "$(env_get IHR_ACME)" == pebble ]]; then
    acme_proof_files "$ACME_FULLDOMAIN"
    # Proof only: Pebble keeps its CA and accounts in memory, so a new Pebble means a new root.
    # Traefik then starts over too (stored certificate and account dropped), or it would keep
    # a certificate nobody can check and an account the new Pebble does not know.
    compose stop traefik >/dev/null 2>&1 || true
    compose up -d --no-deps --force-recreate coredns pebble
    traefik_sh 'rm -f /data/acme.json'
  fi
}

acme_expected_issuer() {  # what the issuer line must contain for the CA in .env
  case "$(env_get IHR_ACME)" in
    letsencrypt) echo "O=Let's Encrypt" ;;
    letsencrypt-staging) echo "(STAGING)" ;;
    pebble) echo "Pebble" ;;
  esac
}

acme_new_cert() {  # drop the stored certificate and Let's Encrypt account, keep the acme-dns account
  [[ -n "$(env_get IHR_ACME)" ]] || die "no certificate settings in .env (acme-init)"
  compose stop traefik
  # shellcheck disable=SC2016  # the container's shell runs date
  traefik_sh 'test ! -e /data/acme.json || mv /data/acme.json "/data/acme.json.replaced-$(date +%Y%m%d%H%M%S)"'
  compose up -d --no-deps --wait traefik
  cert --wait 300
}

cert() {  # cert [--wait SECONDS]: the certificate Traefik now serves for alpha.<suffix>
  local wait=0 suffix port addr out waited=0
  [[ "${1:-}" == --wait ]] && wait="${2:?--wait SECONDS}"
  suffix="$(env_get IHR_TENANT_DOMAIN_SUFFIX)"
  port="$(env_get IHR_HTTPS_PORT)"; port="${port:-443}"
  addr="${IHR_SMOKE_RESOLVE:-$(env_get IHR_SMOKE_RESOLVE)}"; addr="${addr:-127.0.0.1}"
  local want issuer
  want="$(acme_expected_issuer)"
  while :; do
    out="$(openssl s_client -connect "$addr:$port" -servername "alpha.$suffix" </dev/null 2>/dev/null \
      | openssl x509 -noout -subject -issuer -serial -enddate -ext subjectAltName 2>/dev/null || true)"
    issuer="$(sed -n 's/^issuer=//p' <<< "$out")"
    if [[ -n "$out" && "$out" != *"TRAEFIK DEFAULT CERT"* && ( -z "$want" || "$issuer" == *"$want"* ) ]]; then
      # Let's Encrypt's own issuer line never says STAGING
      if [[ "$want" != "O=Let's Encrypt" || "$issuer" != *"(STAGING)"* ]]; then echo "$out"; return 0; fi
    fi
    ((waited >= wait)) && break
    sleep 5; waited=$((waited + 5))
  done
  echo "${out:-no answer on $addr:$port}"
  if [[ "$out" == *"TRAEFIK DEFAULT CERT"* || -z "$out" ]]; then
    echo "Not a certificate from the CA yet (Traefik's placeholder). See: scripts/staging.sh logs traefik" >&2
  else
    echo "Served by '$issuer', but .env says $(env_get IHR_ACME). After changing the CA: scripts/staging.sh acme-new-cert" >&2
  fi
  return 1
}

acme_proof() {  # acme_proof [--keep]: the certificate proof alone, in a throwaway copy of this folder
  local keep="${1:-}" t p img rc=0 f
  t="$(mktemp -d "${TMPDIR:-/tmp}/ihr-acme-proof.XXXXXX")"
  p="$t/deploy/staging"
  mkdir -p "$p/traefik" "$p/secrets"
  cp "$DIR/compose.yaml" "$DIR/compose.acme.yaml" "$DIR/compose.pebble.yaml" "$p/"
  cp "$DIR/traefik/routes.yml" "$p/traefik/"
  # Compose wants every service's env_file, even for services that are not started
  grep -o 'secrets/[a-z0-9-]*\.env' "$DIR/compose.yaml" | sort -u | while read -r f; do : > "$p/$f"; done
  img="${IHR_ACMEDNS_IMAGE:-}"
  if [[ -z "$img" ]]; then
    img=indiehr-acme-dns:proof
    echo "Building $img (deploy/docker/acme-dns.Dockerfile)"
    docker buildx build -q --load -f "$ROOT/deploy/docker/acme-dns.Dockerfile" -t "$img" "$ROOT/deploy/docker" >/dev/null
  fi
  cat > "$p/.env" <<EOF
# Certificate proof (scripts/staging.sh acme-proof): only Traefik, acme-dns, CoreDNS, Pebble
IHR_TENANT_DOMAIN_SUFFIX=$LOCAL_SUFFIX
IHR_SUFFIX_RE=${LOCAL_SUFFIX//./\\.}
IHR_BACKEND_IMAGE=unused:proof
IHR_FRONTEND_IMAGE=unused:proof
IHR_TRAEFIK_DIGEST=$(digest traefik:v3.7.17)
IHR_POSTGRES_DIGEST=unused
IHR_REDIS_DIGEST=unused
IHR_LOG_DRIVER=json-file
IHR_BIND=127.0.0.1
IHR_HTTP_PORT=${IHR_PROOF_HTTP_PORT:-18080}
IHR_HTTPS_PORT=${IHR_PROOF_HTTPS_PORT:-18443}
IHR_DNS_BIND=127.0.0.1
IHR_DNS_PORT=${IHR_PROOF_DNS_PORT:-15353}
IHR_ACME_SUBNET=172.30.63.0/28
IHR_ACME_API_IP=172.30.63.5
EOF
  IHR_STAGING_DIR="$p" IHR_STAGING_OUT="$p" IHR_ACMEDNS_IMAGE="$img" IHR_PROOF_NET=172.30.64 \
    "$0" acme-init pebble "acme.${LOCAL_SUFFIX#*.}" "acme-ns.${LOCAL_SUFFIX#*.}" >/dev/null
  IHR_STAGING_DIR="$p" IHR_PROJECT=indiehr-acmeproof "$0" acme-proof-run || rc=$?
  if [[ "$keep" == --keep ]]; then
    echo "Kept: IHR_STAGING_DIR=$p IHR_PROJECT=indiehr-acmeproof scripts/staging.sh ps|logs|cert|down"
  else
    IHR_STAGING_DIR="$p" IHR_PROJECT=indiehr-acmeproof "$0" down -v >/dev/null 2>&1 || true
    rm -rf "$t"
  fi
  return "$rc"
}

acme_proof_run() {  # the checks of acme-proof (run inside its throwaway folder)
  local suffix port dns_port fail=0 root full serial1 serial2 h out
  # Proof stacks only: one check below reads the (throwaway) account key back to search the logs
  [[ "$(env_get IHR_ACME)" == pebble && "$(env_get IHR_LOG_DRIVER)" == json-file ]] \
    || die "acme-proof-run is for scripts/staging.sh acme-proof only"
  suffix="$(env_get IHR_TENANT_DOMAIN_SUFFIX)"; port="$(env_get IHR_HTTPS_PORT)"
  dns_port="$(env_get IHR_DNS_PORT)"
  check() {  # check <description> <expected> <actual>
    if [[ "$3" == "$2" ]]; then echo "ok    $1 ($3)"; else echo "FAIL  $1: expected $2, got $3"; fail=1; fi
  }
  serial_now() { cert | sed -n 's/^serial=//p' || true; }
  dns() { dns_reply 127.0.0.1 "$dns_port" "$@"; }
  verify() {  # verify <host>: curl's TLS check with only Pebble's root trusted ("0" = good)
    curl -s -o /dev/null --max-time 10 --cacert "$root" --resolve "$1:$port:127.0.0.1" \
      "https://$1:$port/"; echo $?
  }
  echo "== Start: acme-dns, CoreDNS (as the public DNS), Pebble (as Let's Encrypt), Traefik"
  acme_up
  compose up -d --wait traefik
  full="$ACME_FULLDOMAIN"
  echo "== Certificate"
  if ! out="$(cert --wait 120)"; then
    echo "FAIL  no certificate from Pebble within 120 s"; compose logs --tail=40 traefik pebble acme-dns coredns
    return 1
  fi
  printf '      %s\n' "${out//$'\n'/$'\n'      }"
  root="$GEN/acme-proof/root.pem"
  traefik_sh 'wget -q -O - --no-check-certificate https://pebble:15000/roots/0' > "$root"
  for h in "$suffix" "alpha.$suffix" "console.$suffix" "auth.$suffix"; do
    check "trusted certificate for $h" 0 "$(verify "$h")"
  done
  check "a name two levels down is not covered (curl: name mismatch)" 60 "$(verify "x.alpha.$suffix")"
  check "names on the certificate" "DNS:*.$suffix, DNS:$suffix" \
    "$(echo "$out" | sed -n '/Subject Alternative Name/{n;s/^ *//;p}' | tr ',' '\n' | sed 's/^ *//' | sort | paste -sd, - | sed 's/,/, /')"
  check "key type EC P-256" yes "$(openssl s_client -connect "127.0.0.1:$port" -servername "alpha.$suffix" </dev/null 2>/dev/null \
    | openssl x509 -noout -text 2>/dev/null | grep -q 'ASN1 OID: prime256v1' && echo yes || echo no)"
  echo "== acme-dns"
  check "published DNS port answers the TXT check values" "NOERROR 2" "$(dns TXT "$full")"
  check "not a resolver for other names (answers: no such name)" "NXDOMAIN 0" "$(dns A example.com)"
  check "registration closed" closed "$(traefik_sh "wget -q -O /dev/null --post-data '{}' $ACME_API/register 2>/dev/null && echo open || echo closed")"
  # The same update with the account's own key (read inside the container, never printed):
  # accepted on the API's own address, while acme-dns's other addresses have no API at all
  check "update from Traefik's network" accepted "$(traefik_sh "set -- $ACME_API; $ACME_UPDATE_SH")"
  check "no API on acme-dns's other networks" refused \
    "$(traefik_sh "set -- http://$(env_get IHR_PROOF_ACMEDNS_IP):8053; $ACME_UPDATE_SH")"
  check "certificate store mode and owner" "600 65534 600 65534" \
    "$(traefik_sh 'stat -c "%a %u" /data/acme.json /data/acme-dns-accounts.json' | paste -sd' ' -)"
  local key
  key="$(traefik_sh 'sed -n "s/.*\"password\":\"\([^\"]*\)\".*/\1/p" /data/acme-dns-accounts.json')"
  check "acme-dns key in no service's log" 0 \
    "$(compose logs --no-color traefik acme-dns coredns pebble 2>/dev/null | grep -cF "$key" || true)"
  echo "== Restart and renewal"
  serial1="$(serial_now)"
  compose restart traefik >/dev/null 2>&1
  cert --wait 30 >/dev/null || true
  check "restart keeps the stored certificate" "$serial1" "$(serial_now)"
  echo "      waiting for Traefik's renewal (certificates last $(env_get IHR_PROOF_CERT_SECONDS) s here; up to 6 min)"
  local waited=0
  serial2="$serial1"
  while [[ "$serial2" == "$serial1" && $waited -lt 360 ]]; do sleep 15; waited=$((waited + 15)); serial2="$(serial_now)"; done
  check "renewed (new serial, DNS check done again)" yes "$([[ -n "$serial2" && "$serial2" != "$serial1" ]] && echo yes || echo no)"
  check "renewed certificate trusted" 0 "$(verify "alpha.$suffix")"
  return "$fail"
}

case "${1:-}" in
  init) init "${@:2}" ;;
  up) up ;;
  smoke) smoke ;;
  manage) compose --profile admin run --rm --no-deps -T admin python manage.py "${@:2}" ;;
  ps) compose ps ;;
  logs) compose logs --tail=200 "${@:2}" ;;
  down) compose down "${@:2}" ;;
  acme-init) acme_init "${@:2}" ;;
  acme-register) acme_register "${@:2}" ;;
  acme-new-cert) acme_new_cert ;;
  cert) cert "${@:2}" ;;
  acme-retry) compose restart traefik && cert --wait 300 ;;
  acme-proof) acme_proof "${@:2}" ;;
  acme-proof-run) acme_proof_run ;;
  s3-init) s3_init ;;
  s3-check) s3_check ;;
  local-proof)
    [[ -f "$DIR/.env" ]] || IHR_FRONTEND_IMAGE="${IHR_FRONTEND_IMAGE:-indiehr-frontend:local}" \
      init "$LOCAL_SUFFIX" --local
    # A proof keeps its files on SeaweedFS too (P1-T8b3)
    grep -q '^IHR_S3=' "$DIR/.env" || s3_init
    up
    # A synthetic hospital for the address checks (no Keycloak client: sign-in is not tested)
    compose --profile admin run --rm --no-deps -T admin python manage.py ihr_create_tenant \
      --slug alpha --name "Alpha Proof Hospital" --domain "alpha.$(env_get IHR_TENANT_DOMAIN_SUFFIX)" \
      >/dev/null 2>&1 || true
    smoke
    ;;
  *) sed -n '5,24p' "$0"; exit 1 ;;
esac
