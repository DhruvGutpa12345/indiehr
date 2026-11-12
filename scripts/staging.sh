#!/usr/bin/env bash
# IndieHR staging stack (P1-T8a): deploy/staging/compose.yaml, run on the staging VM or, for a
# proof run, on this machine. Secrets are generated here, kept in deploy/staging/secrets/
# (mode 600, git-ignored) and handed to each container only as far as it needs them.
#
# Usage:
#   scripts/staging.sh init <suffix> [--local|--vm]  write .env and secrets (once; no overwrite)
#   scripts/staging.sh up [--new-database]       start everything, then the Keycloak realm + workers
#                                                (--new-database: an empty database where backups exist)
#   scripts/staging.sh smoke                     check every public address through Traefik
#   scripts/staging.sh manage <args>             one-off manage.py command (no admin credentials)
#   scripts/staging.sh ps | logs [service] | down
#   scripts/staging.sh keys ensure | status      the signing keys Keycloak knows the backend and
#                                                workers by (P1-T8d2; made here, never copied)
#   scripts/staging.sh keys rotate <backend|onboarding|staff|all> [--compromised]
#                                                every 180 days, no downtime (--compromised: at
#                                                once; for backend also ends every session)
#   scripts/staging.sh keys probe [--negative]   each key holder signs in to Keycloak
#   scripts/staging.sh local-proof               init (if needed) + up + smoke on this machine
#   scripts/staging.sh acme-init <ca> <acme-zone> <ns-name>   add the wildcard certificate (P1-T8b2)
#   scripts/staging.sh acme-register [--reset]   acme-dns account + the records for the registrar
#   scripts/staging.sh cert [--wait SECONDS]     the certificate Traefik serves now (from the right CA)
#   scripts/staging.sh acme-retry                ask for the certificate again (after DNS changes)
#   scripts/staging.sh acme-new-cert             drop the stored certificate (after changing the CA)
#   scripts/staging.sh acme-proof [--keep]       certificate proof with Pebble, alone, on this machine
#   scripts/staging.sh s3-init                   files on the server itself: SeaweedFS (P1-T8b3)
#   scripts/staging.sh s3-check                  live checks of its keys, locks and public paths
#   scripts/staging.sh mail-init <from> [--host H] [--port P] [--user U] | --test
#                                                mail via Zoho ZeptoMail (P1-T8b4); token on stdin
#   scripts/staging.sh mail-check <to>           send a test e-mail through Django
#   scripts/staging.sh backup-init <https://endpoint> <bucket> <region> [--uri-style path|host] | --local
#                                                database backups (P1-T8c); bucket key on stdin
#   scripts/staging.sh backup-status | backup-now [full|diff]
#   scripts/staging.sh restore-drill [--target "YYYY-MM-DD HH:MM:SS+05:30"] [--keep | --clean]
#   scripts/staging.sh restore [--target ...]   a lost server: into this stack's empty volume
#   scripts/staging.sh monitoring-init <alert-recipient> [--from A] | --test
#                                                metrics and e-mail alerts (P1-T9a); the alerts'
#                                                ZeptoMail token and dead-man ping URL on stdin
#   scripts/staging.sh monitoring-deadman        set the dead-man switch's ping URL later (stdin)
#   scripts/staging.sh alerts | alert-test       alerts held now | one test alert, mailed now
#   scripts/staging.sh ops-tunnel [ihr@<server>] Grafana, Prometheus and Alertmanager on 127.0.0.1 (SSH)
#   scripts/staging.sh grafana-reset-admin       a new Grafana admin password (P1-T9d), file and database together
#   scripts/staging.sh monitoring-textfile [--keys]   the host timer's run (deploy/host/bootstrap.sh)
#   scripts/staging.sh logs-volume               lines and MB a day in the log store; sizes for
#                                                180 days, the archive and the journal (P1-T9c)
#   scripts/staging.sh logs-archive-init <https://endpoint> <bucket> <region> [--uri-style path|host]
#                     [--existing-key] | --local the daily locked log archive (P1-T9c); bucket key
#                                                (and, rebuilding, the archive key) on stdin
#   scripts/staging.sh logs-archive-now | logs-archive-check [--deep] [--recent N] | logs-archive-get <day> [rev]
#   scripts/staging.sh logs-archive-probe       the bucket's lock and what the archive key may do (go-live)
#   scripts/staging.sh logs-archive-expire [--dry-run] [--older-than-days N]
#                                                erasure after 400 days, from the owner's machine
#                                                with the bucket's admin key (stdin)
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
  # Made by us, not by Docker: a bind mount of a missing folder (generated/client-keys in admin
  # and provisioner) would create it owned by root, and staging.sh could no longer write there
  [[ -d "$GEN/client-keys" ]] || mkdir -p "$GEN/client-keys"
  [[ -f "$DIR/.local" ]] && files+=(-f "$DIR/compose.local.yaml")
  acme="$(env_get IHR_ACME)"
  if [[ -n "$acme" ]]; then
    files+=(-f "$DIR/compose.acme.yaml")
    [[ "$acme" == pebble ]] && files+=(-f "$DIR/compose.pebble.yaml")
  fi
  [[ "$(env_get IHR_S3)" == local ]] && files+=(-f "$DIR/compose.s3.yaml")
  [[ "$(env_get IHR_MAIL)" == test ]] && files+=(-f "$DIR/compose.mailtest.yaml")
  [[ -n "$(env_get IHR_BACKUP)" ]] && files+=(-f "$DIR/compose.backup.yaml")
  local mon; mon="$(env_get IHR_MONITORING)"
  if [[ -n "$mon" ]]; then
    # Logs come with the metrics: their alerts go through the same Alertmanager (P1-T9b)
    files+=(-f "$DIR/compose.monitoring.yaml" -f "$DIR/compose.logs.yaml")
    # ...and the operator's dashboards on both (P1-T9d)
    files+=(-f "$DIR/compose.dashboards.yaml")
    [[ "$mon" == test ]] && files+=(-f "$DIR/compose.monitoring.test.yaml")
    # The daily locked log archive (P1-T9c), and in a proof its way to the local stand-in
    case "$(env_get IHR_LOGS_ARCHIVE)" in
      s3) files+=(-f "$DIR/compose.logs-archive.yaml") ;;
      local) files+=(-f "$DIR/compose.logs-archive.yaml" -f "$DIR/compose.logs-archive.local.yaml") ;;
    esac
  fi
  # A proof on this machine that writes to its journal like a server (IHR_LOG_DRIVER=journald in
  # .env): the stack's own services too, so Fluent Bit finds them there
  [[ -f "$DIR/.local" && "$(env_get IHR_LOG_DRIVER)" == journald ]] && files+=(-f "$DIR/compose.local-journal.yaml")
  # Alertmanager and VictoriaLogs run as the user that owns secrets/ (they read a token / key
  # from there, mode 600)
  export IHR_SECRETS_UID IHR_SECRETS_GID IHR_JOURNAL_GID
  IHR_SECRETS_UID="$(id -u)"; IHR_SECRETS_GID="$(id -g)"
  # Fluent Bit reads the journal as nobody with this group (P1-T9b)
  IHR_JOURNAL_GID="$(getent group systemd-journal | cut -d: -f3)"
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
  local traefik redis
  traefik="$(digest traefik:v3.7.17)"
  redis="$(digest redis:8.10.3-alpine)"
  mkdir -m 700 "$work/secrets"
  {
    echo "# IndieHR staging: site settings for Compose (no secrets). Written by staging.sh init."
    echo "IHR_TENANT_DOMAIN_SUFFIX=$suffix"
    echo "IHR_SUFFIX_RE=${suffix//./\\.}"
    echo "IHR_BACKEND_IMAGE=$backend_image"
    echo "IHR_FRONTEND_IMAGE=$frontend_image"
    echo "IHR_TRAEFIK_DIGEST=$traefik"
    # Our build of postgres:18.6-alpine with pgBackRest (deploy/docker/postgres.Dockerfile, P1-T8c)
    echo "IHR_POSTGRES_IMAGE=${IHR_POSTGRES_IMAGE:-indiehr-postgres:staging}"
    # Keycloak 26.8.2 pre-built with our build-time options (deploy/docker/keycloak.Dockerfile, P1-T8d1)
    echo "IHR_KEYCLOAK_IMAGE=${IHR_KEYCLOAK_IMAGE:-indiehr-keycloak:staging}"
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
  for f in anchor-read.env anchor-write.env; do
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
  local id secret mail_pw="" extra=()
  id="$(sed -n 's/^KC_BOOTSTRAP_ADMIN_CLIENT_ID=//p' "$SECRETS/keycloak.env")"
  secret="$(sed -n 's/^KC_BOOTSTRAP_ADMIN_CLIENT_SECRET=//p' "$SECRETS/keycloak.env")"
  # The mail password reaches only this one-off (ihr_keycloak_sync puts it on the realm), never
  # the web app's settings (P1-T8b4)
  [[ -f "$SECRETS/mail.env" ]] && mail_pw="$(sed -n 's/^IHR_KEYCLOAK_SMTP_PASSWORD=//p' "$SECRETS/mail.env")"
  [[ -n "$mail_pw" ]] && extra=(-e IHR_KEYCLOAK_SMTP_PASSWORD)
  IHR_KEYCLOAK_ADMIN_CLIENT_ID="$id" IHR_KEYCLOAK_ADMIN_CLIENT_SECRET="$secret" IHR_KEYCLOAK_SMTP_PASSWORD="$mail_pw" \
    compose --profile admin run --rm --no-deps -T -e IHR_KEYCLOAK_ADMIN_CLIENT_ID \
    -e IHR_KEYCLOAK_ADMIN_CLIENT_SECRET "${extra[@]}" admin python manage.py "$@"
}

# ---- Client keys (P1-T8d2): what scripts/client-keys.sh needs from this stack -------------------
# shellcheck source=scripts/client-keys.sh disable=SC1091 # followed only when checked together
source "$(dirname "$0")/client-keys.sh"
# shellcheck disable=SC2034 # read by client-keys.sh
KEY_JWKS_DIR="$GEN/client-keys"
kt() {  # kt <purpose> <command> [args]: the key tool, offline, unlogged, as the app user
  compose --profile keys run --rm --no-deps -T keytool "$2" --purpose "$1" --dir "/run/ihr-keytool/$1" "${@:3}"
}
key_publish_sync() { admin_manage ihr_keycloak_sync --realm --all; }
key_check_sync() { admin_manage ihr_keycloak_sync --all --check; }
key_probe_in() { compose exec -T "$1" python manage.py ihr_client_keys probe "${@:2}"; }
key_revoke_sessions() { admin_manage ihr_client_keys revoke-sessions; }
key_log() {  # kid and time only; kept in the log store 180 days and the archive 400 (CERT-In). A local proof keeps this
  # machine's journal clean (compose.local.yaml)
  echo "$*"
  [[ -f "$DIR/.local" ]] && return 0
  logger -t indiehr/keys -- "$* ($(date -Iseconds))" || echo "warning: could not write to the journal" >&2
}

up() {  # up [--new-database]: the flag only where an empty database is really meant (db_guard)
  local new_db=0
  [[ "${1:-}" != --new-database ]] || new_db=1
  [[ $# -eq 0 || $new_db == 1 ]] || die "usage: scripts/staging.sh up [--new-database]"
  [[ -f "$DIR/.env" ]] || { echo "run: scripts/staging.sh init <suffix>" >&2; exit 1; }
  [[ -z "$(env_get IHR_BACKUP)" ]] || backup_config
  # Compose reads Fluent Bit's (and Grafana's) env files on every call: those secrets first
  if [[ -n "$(env_get IHR_MONITORING)" ]]; then logs_secrets; dashboards_secrets; fi
  db_guard "$new_db"
  compose up -d --wait db redis
  touch "$DIR/.db-created"
  # Signing keys (P1-T8d2): made here where a volume is still empty (a new server, after
  # down -v), and their public halves written for the sync below
  keys_ensure
  [[ -z "$(env_get IHR_ACME)" ]] || acme_up
  [[ "$(env_get IHR_S3)" != local ]] || s3_up
  local extra=()
  if [[ "$(env_get IHR_MAIL)" == test ]]; then mail_test_files; extra=(smtp-test); fi
  if [[ -n "$(env_get IHR_BACKUP)" ]]; then backup_config; extra+=(backup); fi
  compose up -d --wait "${extra[@]}" traefik keycloak
  # Schema changes to the end first: the sync reads the hospitals from the database. Run here,
  # once; the services below start without their dependency chain, so it does not run again.
  compose run --rm --no-deps -T migrate
  # The new backend first: on a server from before P1-T8d2 its clients are still public and
  # ignore the assertion it now sends, so sign-ins go on while the old backend is replaced.
  # The old workers stop: after the sync below their secrets are dead.
  compose up -d --wait --no-deps backend
  compose stop provisioner staff-worker
  # Keycloak learns the current public keys (clients switch to keys; after a restore its
  # database may hold older ones: sign-ins wait only for this step)
  admin_manage ihr_keycloak_sync --realm --all
  compose up -d --wait --no-deps celery-worker celery-beat frontend
  compose up -d --no-deps provisioner staff-worker
  # The workers' Keycloak secrets of before P1-T8d2: replaced by the sync above, so dead;
  # their files go too
  rm -f "$SECRETS/provisioner.env" "$SECRETS/staff-worker.env"
  # Metrics and alerts last (P1-T9a): they watch what is already running
  [[ -z "$(env_get IHR_MONITORING)" ]] || monitoring_up
  compose ps --format 'table {{.Service}}\t{{.Status}}'
}

db_guard() {  # db_guard <new 0|1>: never an empty database where one is to be restored. In an
  # empty volume Postgres makes a fresh, empty cluster and the hospitals see an empty system:
  #  - a half-done restore (its marker in the volume) must be finished first;
  #  - a server that had a database here (.db-created) must restore it;
  #  - a new server whose backup repository already has backups is a restore, not a start.
  local new="$1" vol="${IHR_PROJECT:-indiehr-staging}_pgdata" state out
  state="empty"
  if docker volume inspect "$vol" >/dev/null 2>&1; then
    state="$(docker run --rm --user 70:70 --network none -v "$vol:/var/lib/postgresql" \
      --entrypoint sh "$(env_get IHR_POSTGRES_IMAGE)" -c "$PG_STATE")" || die "could not read $vol"
  fi
  case "$state" in
    db) return 0 ;;
    restoring) die "$vol holds an unfinished restore: run scripts/staging.sh restore again" ;;
  esac
  [[ "$new" == 1 ]] && return 0
  [[ ! -f "$DIR/.db-created" ]] || die "$vol is missing or empty, but this server had a database: restore it (scripts/staging.sh restore). To start an empty one on purpose: scripts/staging.sh up --new-database"
  [[ -n "$(env_get IHR_BACKUP)" ]] || return 0
  out="$(repo_info)" || die "cannot read the backup repository, so cannot tell whether this is a restore: fix its access (scripts/staging.sh backup-status), or start an empty database on purpose with scripts/staging.sh up --new-database"
  if grep -q '"type":"full"' <<< "$out"; then
    die "the backup repository already holds backups of a database: this is a restore (scripts/staging.sh restore, then up). To start an empty database anyway: scripts/staging.sh up --new-database"
  fi
}

# Restored copies log like the live database (compose.yaml, P1-T9a): no query values
PG_LOG_PRIVACY=(-c log_min_duration_statement=-1 -c log_min_error_statement=panic -c log_error_verbosity=terse)

# The database volume's state (PGDATA: compose.yaml): "restoring" while a real restore is unfinished
PG_STATE='if [ -e /var/lib/postgresql/.ihr-restore-incomplete ]; then echo restoring; elif [ -e /var/lib/postgresql/18/docker/PG_VERSION ]; then echo db; else echo empty; fi'

repo_info() {  # the repository's info as JSON, read by a one-off container (the stack may be down)
  local rc=0
  compose up --no-start db backup >/dev/null || die "could not create the stack's containers and volumes"
  [[ "$(env_get IHR_S3)" != local ]] || compose up -d --wait s3 >/dev/null
  docker rm -f ihr-repo-info >/dev/null 2>&1 || true
  pg_container ihr-repo-info "" pgbackrest --stanza=main --output=json --log-level-console=error info
  docker start -a ihr-repo-info || rc=$?
  docker rm -f ihr-repo-info >/dev/null 2>&1 || true
  return "$rc"
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
    for host in "$suffix" "alpha.$suffix" "console.$suffix" "auth.$suffix" "nope.$suffix" "files.$suffix" "status.$suffix" "ops.$suffix"; do
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
  # Per-IP limit on sign-in submissions (P1-T8d1): 20 quick POSTs from one address meet a 429
  local i limited=no
  for i in $(seq 1 20); do
    if [[ "$(code -X POST "https://auth.$suffix:$port/realms/indiehr/login-actions/authenticate")" == 429 ]]; then limited=yes; break; fi
  done
  check "sign-in submissions limited per address" yes "$limited"
  local issuer
  issuer="$(curl "${curl_args[@]}" "https://auth.$suffix:$port/realms/indiehr/.well-known/openid-configuration" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin).get("issuer",""))' 2>/dev/null || true)"
  check "token issuer is the public https address" "https://auth.$suffix/realms/indiehr" "$issuer"
  # Signing keys (P1-T8d2): their age (rotate every 180 days: WARN from 150, FAIL from 365),
  # Keycloak holding the current public keys of every client, and the web app's key accepted
  # (a junk refresh token gets invalid_grant, so client authentication itself passed)
  keys_age_check || fail=1
  local p
  for p in "${KEY_PURPOSES[@]}"; do kt_change "$p" public; done
  check "Keycloak holds each client's current public keys (sync --check)" 0 "$(key_check_sync >/dev/null; echo $?)"
  check "web app signs in to Keycloak with its key (probe)" 0 "$(key_probe_in backend >/dev/null; echo $?)"
  if [[ "$(env_get IHR_S3)" == local ]]; then
    check "files: nothing at the root" 418 "$(code "https://files.$suffix:$port/")"
    check "files: fingerprint bucket not public" 418 "$(code "https://files.$suffix:$port/$(env_get IHR_S3_ANCHOR_BUCKET)/anchors/x")"
    check "files: patient files need a signature" 403 "$(code "https://files.$suffix:$port/$(env_get IHR_S3_PATIENT_BUCKET)/x")"
    check "files: hospital pictures are public (none here yet)" 404 "$(code "https://files.$suffix:$port/$(env_get IHR_S3_FACILITY_BUCKET)/no-such-picture.png")"
  fi
  if [[ -n "$(env_get IHR_BACKUP)" ]]; then
    local bh
    bh="$(docker inspect -f '{{.State.Health.Status}}' "$(compose ps -q backup)" 2>/dev/null || echo missing)"
    if [[ "$bh" == starting ]]; then echo "skip  backups: the backup service is still starting"
    else check "backups healthy (WAL pushed, check passing, backup within 26 h)" healthy "$bh"; fi
  fi
  # The outside uptime check's address (P1-T9a): Traefik's ping there, nothing else; ops. closed
  check "status address for the uptime check" "200 OK" "$(curl "${curl_args[@]}" -w ' %{http_code}' -o /dev/null "https://status.$suffix:$port/healthz" | sed 's/^ //') $(curl "${curl_args[@]}" "https://status.$suffix:$port/healthz")"
  check "status host: nothing else served" 418 "$(code "https://status.$suffix:$port/")"
  check "ops host closed (reserved, SSH tunnel only)" 418 "$(code "https://ops.$suffix:$port/")"
  [[ -z "$(env_get IHR_MONITORING)" ]] || monitoring_smoke
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

# ---- Database backups (P1-T8c): pgBackRest, compose.backup.yaml ----------------------------------
backup_init() {  # backup_init <https://endpoint> <bucket> <region> [--uri-style path|host] | --local
  local envf="$OUT/.env" sec="$OUT/secrets" endpoint="" bucket="" region="" style="host" local_repo=0
  local key secret args=() existing=0 pass
  [[ -f "$envf" && -d "$sec" ]] || die "no $envf / secrets: run scripts/staging.sh init <suffix> first"
  grep -q '^IHR_BACKUP=' "$envf" && die "$envf already has backup settings (IHR_BACKUP=...)"
  while (($#)); do
    case "$1" in
      --local) local_repo=1 ;;
      # A new server reading an existing repository: its passphrase (from the password
      # manager), typed or as the last line on stdin, instead of a new one
      --existing-passphrase) existing=1 ;;
      --uri-style) style="${2:?--uri-style path|host}"; shift ;;
      -*) die "unknown option: $1" ;;
      *) args+=("$1") ;;
    esac
    shift
  done
  if ((local_repo)); then
    # Proof only: the backup bucket of this stack's own SeaweedFS (same disk, so not a real
    # backup); a real server uses a bucket in the other region
    grep -q '^IHR_S3=local' "$envf" || die "backup-init --local needs SeaweedFS on this stack (s3-init)"
    endpoint="http://s3:8333" bucket="$(sed -n 's/^IHR_S3_BACKUP_BUCKET=//p' "$envf" | tail -1)" region="ap-south-1" style="path"
    key="$(sed -n 's/^IHR_S3_BACKUP_KEY=//p' "$sec/s3-gateway.env")"
    secret="$(sed -n 's/^IHR_S3_BACKUP_SECRET=//p' "$sec/s3-gateway.env")"
  else
    ((${#args[@]} == 3)) || die "usage: scripts/staging.sh backup-init <https://endpoint> <bucket> <region> [--uri-style path|host]"
    endpoint="${args[0]}" bucket="${args[1]}" region="${args[2]}"
    [[ "$endpoint" =~ ^https://[a-z0-9.-]+(:[0-9]+)?$ ]] || die "the endpoint must be https://host (e.g. https://in-south1-objectstore.e2enetworks.net)"
    # The bucket's key, from the keyboard or stdin (two lines: key, then secret), never arguments
    if [[ -t 0 ]]; then
      read -rp "Backup bucket access key: " key; read -rsp "Backup bucket secret key: " secret; echo
    else
      IFS= read -r key || true; IFS= read -r secret || true
    fi
  fi
  if ((existing)); then
    if [[ -t 0 ]]; then read -rsp "The repository's encryption passphrase: " pass; echo
    else IFS= read -r pass || true; fi
    [[ ${#pass} -ge 20 ]] || die "that passphrase is too short to be ours"
    case "$pass" in *[[:space:]\"\'\\\$\`]*) die "the passphrase has characters an env file cannot hold" ;; esac
  else
    pass="$(rand 48)"
  fi
  [[ "$bucket" =~ $BUCKET_RE ]] || die "not a bucket name: $bucket"
  [[ "$region" =~ ^[A-Za-z0-9-]+$ ]] || die "not a region: $region"
  [[ "$style" =~ ^(path|host)$ ]] || die "--uri-style is path or host"
  for v in "$key" "$secret"; do
    [[ ${#v} -ge 8 ]] || die "the bucket key and secret are required"
    case "$v" in *[[:space:]\"\'\\\$\`]*) die "the key has characters an env file cannot hold" ;; esac
  done
  local SECRETS_TARGET="$sec"
  {
    echo "# Database backups (P1-T8c, scripts/staging.sh backup-init): the backup service only."
    echo "# PGBACKREST_REPO1_CIPHER_PASS encrypts every backup. Without it nothing can be restored:"
    echo "# keep a copy in the owner's password manager, never only on the server."
    echo "PGBACKREST_REPO1_S3_KEY=$key"
    echo "PGBACKREST_REPO1_S3_KEY_SECRET=$secret"
    echo "PGBACKREST_REPO1_CIPHER_PASS=$pass"
  } | write_secret backup.env
  {
    echo "# Database backups (P1-T8c; scripts/staging.sh backup-init): any moment of the last"
    echo "# 28 days (owner, 09-11-2026)"
    echo "IHR_BACKUP=s3"
    echo "IHR_BACKUP_ENDPOINT=$endpoint"
    echo "IHR_BACKUP_BUCKET=$bucket"
    echo "IHR_BACKUP_REGION=$region"
    echo "IHR_BACKUP_URI_STYLE=$style"
    echo "IHR_PG_ARCHIVE_MODE=on"
  } >> "$envf"
  echo "Backup settings added ($endpoint, bucket $bucket). Copy PGBACKREST_REPO1_CIPHER_PASS from"
  echo "$sec/backup.env into your password manager now: without it no backup can be restored."
}

backup_config() {  # generated/pgbackrest/pgbackrest.conf from .env (no secrets: those are env)
  local d="$GEN/pgbackrest" tmp
  mkdir -p "$d"
  tmp="$(mktemp "$d/.conf.XXXXXX")"
  cat > "$tmp" <<EOF
# Written by scripts/staging.sh from .env (P1-T8c); changes here are overwritten.
# Keys and the encryption passphrase come from secrets/backup.env (PGBACKREST_REPO1_*).
[global]
repo1-type=s3
repo1-s3-endpoint=$(env_get IHR_BACKUP_ENDPOINT)
repo1-s3-bucket=$(env_get IHR_BACKUP_BUCKET)
repo1-s3-region=$(env_get IHR_BACKUP_REGION)
repo1-s3-uri-style=$(env_get IHR_BACKUP_URI_STYLE)
# One folder per platform: staging and production may share a bucket
repo1-path=/pgbackrest/$(env_get IHR_TENANT_DOMAIN_SUFFIX)
repo1-cipher-type=aes-256-cbc
# Any moment of the last 28 days (owner, 09-11-2026): pgBackRest keeps the newest full that is
# older than 28 days and everything after it, however many extra fulls were taken
repo1-retention-full-type=time
repo1-retention-full=28
compress-type=zst
process-max=2
start-fast=y
archive-timeout=120
log-level-console=info
log-level-file=off
lock-path=/tmp/pgbackrest

[main]
pg1-path=/var/lib/postgresql/18/docker
pg1-socket-path=/var/run/postgresql
pg1-user=postgres
EOF
  chmod 644 "$tmp"
  mv "$tmp" "$d/pgbackrest.conf"
}

backup_status() {
  compose exec -T backup pgbackrest --stanza=main info
  echo "--- the backup service's own record (/var/lib/pgbackrest/status):"
  compose exec -T backup cat /var/lib/pgbackrest/status 2>/dev/null || echo "(none yet)"
  echo "--- WAL files waiting in the spool: $(compose exec -T backup sh -c 'ls /var/spool/ihr-wal | grep -vc "^\." || true')"
  compose exec -T backup sh -c 'ls /var/lib/pgbackrest/rejected-wal 2>/dev/null | sed "s/^/--- set aside by a restore (another database; remove once checked): /"' || true
}

DRILL="ihr-restore-drill"
TARGET_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}([+-][0-9]{2}:[0-9]{2}|Z)$'

check_target() {  # a PITR target must carry its offset: without one Postgres reads it as UTC
  [[ "$1" =~ $TARGET_RE ]] || die "--target must look like '2026-11-09 14:00:55+05:30' (with the offset)"
  echo "Target: $1 = $(date -u -d "$1" '+%Y-%m-%d %H:%M:%S') UTC"
}

pg_container() {  # pg_container <name> <volume:path,...> <command...>: our image as postgres, with
  # the repository's settings and keys (env file: never on a command line), on the stack's
  # internal (SeaweedFS) and edge (a provider's bucket) networks
  local name="$1" mounts="$2" project="${IHR_PROJECT:-indiehr-staging}" m args=()
  shift 2
  m=()
  [[ -z "$mounts" ]] || IFS=',' read -ra m <<< "$mounts"
  for v in "${m[@]}"; do args+=(-v "$v"); done
  docker create --name "$name" --user 70:70 --network "${project}_internal" --memory 768m \
    --env-file "$SECRETS/backup.env" --cap-drop ALL --security-opt no-new-privileges:true \
    "${args[@]}" -v "$GEN/pgbackrest/pgbackrest.conf:/etc/pgbackrest/pgbackrest.conf:ro" \
    --tmpfs /tmp:uid=70,gid=70,mode=0700 "$(env_get IHR_POSTGRES_IMAGE)" "$@" >/dev/null
  docker network connect "${project}_edge" "$name"
}

wait_promoted() {  # wait_promoted <container>: until recovery has ended (or fail)
  local out="" i
  for i in $(seq 1 450); do
    out="$(docker exec "$1" psql -U postgres -Atc "SELECT pg_is_in_recovery()" 2>/dev/null || true)"
    [[ "$out" == f ]] && return 0
    docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null | grep -q true || break
    sleep 2
  done
  docker logs --tail 30 "$1"
  die "recovery did not finish"
}

restore_drill() {  # a copy of the database from the backups, checked, then removed
  local target="" keep=0 pw dburl i free need as_of
  while (($#)); do
    case "$1" in
      --target) target="${2:?--target 'YYYY-MM-DD HH:MM:SS+05:30'}"; shift ;;
      --keep) keep=1 ;;
      --clean) docker rm -f "$DRILL" "$DRILL-restore" >/dev/null 2>&1; docker volume rm -f "$DRILL" >/dev/null; echo "Drill removed."; return 0 ;;
      *) die "usage: scripts/staging.sh restore-drill [--target 'YYYY-MM-DD HH:MM:SS+05:30'] [--keep | --clean]" ;;
    esac
    shift
  done
  [[ -n "$(env_get IHR_BACKUP)" ]] || die "no backups set up here (backup-init)"
  [[ -z "$target" ]] || check_target "$target"
  backup_config
  # Room for a second copy beside the live database (the same disk): twice its size
  need="$(compose exec -T db psql -U postgres -Atc "SELECT sum(pg_database_size(datname)) / 1048576 FROM pg_database" | tr -d '[:space:]')"
  free="$(df -Pm "$(docker info --format '{{.DockerRootDir}}')" 2>/dev/null | awk 'NR==2 {print $4}')"
  if [[ -n "$need" && -n "$free" ]] && ((free < 2 * need + 1024)); then
    die "not enough disk for a copy: ${free} MB free, about $((2 * need + 1024)) MB needed"
  fi
  # Whatever happens from here, the drill's containers go (and the volume, unless --keep)
  # (the trap runs after this function's locals are gone: keep's value is written into it)
  # shellcheck disable=SC2064
  trap "docker rm -f '$DRILL-restore' >/dev/null 2>&1; [[ $keep == 1 ]] || { docker rm -f '$DRILL' >/dev/null 2>&1; docker volume rm -f '$DRILL' >/dev/null 2>&1; }" EXIT
  docker rm -f "$DRILL" "$DRILL-restore" >/dev/null 2>&1 || true
  docker volume rm -f "$DRILL" >/dev/null 2>&1 || true
  docker volume create "$DRILL" >/dev/null
  echo "== Restore $([[ -n "$target" ]] && echo "to $target" || echo "to the newest moment in the backups")"
  pg_container "$DRILL-restore" "$DRILL:/var/lib/postgresql" pgbackrest --stanza=main \
    ${target:+--type=time "--target=$target" --target-action=promote} restore
  if docker start -a "$DRILL-restore"; then i=0; else i=$?; fi
  ((i == 0)) || die "restore failed"
  # The copy never archives into the repository (archive_mode=off); it fetches WAL to recover.
  # Recovery needs at least the original's max_connections (100, compose.yaml).
  pg_container "$DRILL" "$DRILL:/var/lib/postgresql" postgres -c archive_mode=off \
    -c shared_buffers=128MB -c max_connections=100 "${PG_LOG_PRIVACY[@]}"
  docker start "$DRILL" >/dev/null
  wait_promoted "$DRILL"
  # The copy's own moment: its last replayed transaction (what the fingerprints are checked at)
  as_of="$(docker logs "$DRILL" 2>&1 | sed -n 's/.*last completed transaction was at log time \([0-9-]* [0-9:.]*[+-][0-9]*\).*/\1/p' | tail -1)"
  [[ "$as_of" =~ [+-][0-9]{2}$ ]] && as_of="$as_of:00"
  echo "== The restored copy (its last transaction: ${as_of:-not found in its log})"
  docker exec "$DRILL" psql -U postgres -Atc "SELECT 'databases: ' || string_agg(datname || ' ' || pg_size_pretty(pg_database_size(datname)), ', ' ORDER BY datname) FROM pg_database WHERE NOT datistemplate"
  docker exec "$DRILL" psql -U postgres -d ihr -Atc "SELECT 'hospital schemas: ' || count(*) FROM pg_namespace WHERE nspname ~ '^h_[a-z0-9]{10}\$'"
  # The audit trail of every hospital, checked in the copy against the outside fingerprints
  # the storage had received by the copy's moment: a backup that restores a rewritten
  # history fails here
  echo "== Audit trail and fingerprints, checked in the restored copy"
  pw="$(sed -n 's#^DATABASE_URL=postgres://ihr:\([^@]*\)@db:5432/ihr$#\1#p' "$SECRETS/app.env")"
  [[ -n "$pw" ]] || die "DATABASE_URL in secrets/app.env is not in the expected form"
  dburl="postgres://ihr:$pw@$DRILL:5432/ihr"
  local envs=(-e DATABASE_URL) k v
  while IFS='=' read -r k v; do
    if [[ "$k" =~ ^IHR_AUDIT_ANCHOR_[A-Z_]+$ ]]; then export "$k=$v"; envs+=(-e "$k"); fi
  done < "$SECRETS/anchor-read.env"
  if DATABASE_URL="$dburl" compose --profile admin run --rm --no-deps -T "${envs[@]}" admin \
    python manage.py ihr_audit_verify --all ${as_of:+--as-of "$as_of"}; then i=0; else i=$?; fi
  ((keep)) && echo "Kept: container $DRILL (psql: docker exec -it $DRILL psql -U postgres). Remove: scripts/staging.sh restore-drill --clean"
  ((i == 0)) || die "the restored copy failed the audit check"
  echo "Restore drill passed."
}

restore_live() {  # restore [--target T]: a lost server's database, into this stack's own volume
  local target="" project="${IHR_PROJECT:-indiehr-staging}" vol i state
  while (($#)); do
    case "$1" in
      --target) target="${2:?--target 'YYYY-MM-DD HH:MM:SS+05:30'}"; shift ;;
      *) die "usage: scripts/staging.sh restore [--target 'YYYY-MM-DD HH:MM:SS+05:30']" ;;
    esac
    shift
  done
  [[ -n "$(env_get IHR_BACKUP)" ]] || die "no backup settings here (backup-init, with --existing-passphrase on a new server)"
  [[ -z "$target" ]] || check_target "$target"
  vol="${project}_pgdata"
  if [[ -n "$(compose ps -q db 2>/dev/null)" ]]; then die "the stack is running: scripts/staging.sh down first"; fi
  backup_config
  # Volumes made the Compose way (labels), then: never over an existing database
  compose up --no-start db backup >/dev/null || die "could not create the stack's containers and volumes"
  # The repository on this stack's own SeaweedFS (a local proof) must be running to read from
  [[ "$(env_get IHR_S3)" != local ]] || compose up -d --wait s3 >/dev/null
  # Never over a database; an unfinished restore (its marker) is started again from scratch
  state="$(docker run --rm --user 70:70 --network none -v "$vol:/var/lib/postgresql" --entrypoint sh \
    "$(env_get IHR_POSTGRES_IMAGE)" -c "$PG_STATE")" || die "could not read $vol"
  case "$state" in
    db) die "$vol already holds a database; restore only into an empty one (on a new server: scripts/staging.sh down -v first, if the stack was started)" ;;
    restoring) echo "== An unfinished restore is in $vol: starting it again" ;;
  esac
  # The marker stays until recovery has finished: up refuses a half-restored database
  docker run --rm --user 70:70 --network none -v "$vol:/var/lib/postgresql" --entrypoint sh \
    "$(env_get IHR_POSTGRES_IMAGE)" -c 'touch /var/lib/postgresql/.ihr-restore-incomplete && find /var/lib/postgresql/18 -mindepth 1 -delete 2>/dev/null; mkdir -p /var/lib/postgresql/18/docker && chmod 700 /var/lib/postgresql/18/docker'
  echo "== Restore $([[ -n "$target" ]] && echo "to $target" || echo "to the newest moment in the backups") into $vol"
  trap 'docker rm -f ihr-restore ihr-restore-spool ihr-restore-recovery >/dev/null 2>&1' EXIT
  pg_container ihr-restore "$vol:/var/lib/postgresql" pgbackrest --stanza=main \
    ${target:+--type=time "--target=$target" --target-action=promote} restore
  if docker start -a ihr-restore; then i=0; else i=$?; fi
  ((i == 0)) || die "restore failed"
  # The spool before recovery: our own surviving WAL pushed (replayed below), another
  # database's set aside (it would block every later push)
  pg_container ihr-restore-spool "$vol:/var/lib/postgresql:ro,${project}_walspool:/var/spool/ihr-wal,${project}_backupstate:/var/lib/pgbackrest" \
    ihr-backup-spool-sweep
  docker start -a ihr-restore-spool || die "the spool still holds WAL of this database that could not be pushed (above): nothing was recovered yet; fix the repository's access and run scripts/staging.sh restore again"
  # Recovery with the repository in reach, archiving ON into the stack's spool: the new
  # timeline's history file reaches the repository, so later restores can follow it
  pg_container ihr-restore-recovery "$vol:/var/lib/postgresql,${project}_walspool:/var/spool/ihr-wal" \
    postgres -c archive_mode=on -c "archive_command=/usr/local/bin/ihr-pgbackrest-spool %p %f" \
    -c max_connections=100 "${PG_LOG_PRIVACY[@]}"
  docker start ihr-restore-recovery >/dev/null
  wait_promoted ihr-restore-recovery
  docker exec ihr-restore-recovery psql -U postgres -Atc "CHECKPOINT" >/dev/null
  docker stop -t 60 ihr-restore-recovery >/dev/null
  docker run --rm --user 70:70 --network none -v "$vol:/var/lib/postgresql" --entrypoint sh \
    "$(env_get IHR_POSTGRES_IMAGE)" -c 'rm -f /var/lib/postgresql/.ihr-restore-incomplete'
  echo "Restored. Next: scripts/staging.sh up (it pushes the new timeline), then scripts/staging.sh backup-now full"
}


# ---- Mail (P1-T8b4): Zoho ZeptoMail, or a stand-in for proofs (compose.mailtest.yaml) ------------
EMAIL_RE='^[A-Za-z0-9._%+-]+@[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
MAIL_TEST_HOST="mail.ihr-proof.test"

mail_init() {  # mail_init <from-address> [--host H] [--port P] [--user U] | mail_init --test
  local envf="$OUT/.env" sec="$OUT/secrets" from="" host="smtp.zeptomail.in" port=587
  local user="emailapikey" test=0 token tmp
  [[ -f "$envf" && -d "$sec" ]] || die "no $envf / secrets: run scripts/staging.sh init <suffix> first"
  grep -q '^IHR_MAIL=' "$envf" && die "$envf already has mail settings (IHR_MAIL=...)"
  while (($#)); do
    case "$1" in
      --test) test=1 ;;
      --host) host="${2:?--host H}"; shift ;;
      --port) port="${2:?--port P}"; shift ;;
      --user) user="${2:?--user U}"; shift ;;
      -*) die "unknown option: $1" ;;
      *) from="$1" ;;
    esac
    shift
  done
  if ((test)); then
    # The stand-in: same shape as ZeptoMail (STARTTLS, user and password), nothing leaves
    host="$MAIL_TEST_HOST" port=1025 from="${from:-no-reply@ihr-proof.test}" token="$(rand 32)"
  else
    [[ -n "$from" ]] || die "usage: scripts/staging.sh mail-init <from-address> (the token is read from stdin)"
    # The token comes from the keyboard or stdin, never the command line (shell history, ps)
    if [[ -t 0 ]]; then
      read -rsp "ZeptoMail SMTP password (the agent's Send Mail token): " token; echo
    else
      IFS= read -r token || true
    fi
    [[ ${#token} -ge 16 ]] || die "that does not look like a ZeptoMail token (too short)"
  fi
  [[ "$from" =~ $EMAIL_RE ]] || die "not an e-mail address: $from"
  [[ "$host" =~ $DOMAIN_RE ]] || die "not a host name: $host"
  # 587 with STARTTLS only: deployment settings always use STARTTLS (EMAIL_USE_TLS), and so does
  # Keycloak's realm; port 465 (implicit TLS) would fail to connect. The stand-in uses 1025.
  if ((test)); then [[ "$port" == 1025 ]] || die "the stand-in listens on 1025"
  else [[ "$port" == 587 ]] || die "only port 587 (STARTTLS) is supported"; fi
  [[ "$user" =~ ^[A-Za-z0-9@._-]+$ ]] || die "not a user name: $user"
  # Values go into env files that Compose reads (and expands): no spaces, quotes or $
  case "$token" in *[[:space:]\"\'\\\$\`]*) die "the token has characters an env file cannot hold" ;; esac
  local SECRETS_TARGET="$sec"
  # Django (invitations, P1-T5e1): host, user, password, sender; TLS is always on in deployment
  tmp="$(grep -Ev '^(EMAIL_HOST|EMAIL_PORT|EMAIL_USER|EMAIL_PASSWORD|EMAIL_FROM|EMAIL_USE_TLS|IHR_KEYCLOAK_SMTP_[A-Z]+)=' "$sec/app.env"
    echo "# Mail (P1-T8b4, scripts/staging.sh mail-init): $([[ $test == 1 ]] && echo "proof stand-in" || echo "Zoho ZeptoMail")"
    echo "EMAIL_HOST=$host"
    echo "EMAIL_PORT=$port"
    echo "EMAIL_USE_TLS=True"
    echo "EMAIL_USER=$user"
    echo "EMAIL_PASSWORD=$token"
    echo "EMAIL_FROM=IndieHR <$from>"
    echo "# Keycloak's setup e-mails (its password only in secrets/mail.env, for ihr_keycloak_sync)"
    echo "IHR_KEYCLOAK_SMTP_HOST=$host"
    echo "IHR_KEYCLOAK_SMTP_PORT=$port"
    echo "IHR_KEYCLOAK_SMTP_FROM=$from"
    echo "IHR_KEYCLOAK_SMTP_USER=$user")"
  printf '%s\n' "$tmp" | write_secret app.env.new
  mv "$sec/app.env.new" "$sec/app.env"
  printf 'IHR_KEYCLOAK_SMTP_PASSWORD=%s\n' "$token" | write_secret mail.env
  {
    echo "# Mail (P1-T8b4; scripts/staging.sh mail-init)"
    echo "IHR_MAIL=$([[ $test == 1 ]] && echo test || echo smtp)"
  } >> "$envf"
  echo "Mail settings added ($host:$port, from $from); token in $sec (mode 600)."
}

mail_test_files() {  # the stand-in's throwaway CA, certificate and password file (IHR_MAIL=test)
  local d="$GEN/mailtest" token
  mkdir -p "$d"
  if [[ ! -s "$d/ca.pem" || ! -s "$d/smtp.crt" ]]; then
    # Shaped like a real CA and server certificate: Python 3.13 checks strictly (key usage)
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 30 \
      -subj "/CN=IndieHR proof mail CA (throwaway)" -keyout "$d/ca.key" -out "$d/ca.pem" \
      -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" \
      -addext "subjectKeyIdentifier=hash" 2>/dev/null
    openssl req -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -subj "/CN=$MAIL_TEST_HOST" \
      -keyout "$d/smtp.key" -out "$d/smtp.csr" 2>/dev/null
    openssl x509 -req -in "$d/smtp.csr" -CA "$d/ca.pem" -CAkey "$d/ca.key" -CAcreateserial -days 30 \
      -extfile <(printf 'subjectAltName=DNS:%s\nextendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature\nbasicConstraints=critical,CA:FALSE\nauthorityKeyIdentifier=keyid\nsubjectKeyIdentifier=hash\n' "$MAIL_TEST_HOST") \
      -out "$d/smtp.crt" 2>/dev/null
    rm -f "$d/smtp.csr" "$d/ca.key" "$d/ca.srl"  # nothing else is ever signed with it
  fi
  # Python checks certificates against SSL_CERT_FILE: the image's own bundle plus this CA
  docker run --rm --network none --log-driver none --entrypoint cat "$(env_get IHR_BACKEND_IMAGE)" \
    /etc/ssl/certs/ca-certificates.crt > "$d/ca-bundle.pem"
  cat "$d/ca.pem" >> "$d/ca-bundle.pem"
  # Mailpit (user nobody) reads these: a throwaway proof key and the proof's own token
  token="$(sed -n 's/^IHR_KEYCLOAK_SMTP_PASSWORD=//p' "$SECRETS/mail.env")"
  printf 'emailapikey:%s\n' "$token" > "$d/smtp-auth"
  chmod 644 "$d"/*
}

mail_check() {  # mail_check <to>: Django sends a test e-mail; with the stand-in, read it back
  local to="${1:?usage: scripts/staging.sh mail-check <to-address>}"
  [[ "$to" =~ $EMAIL_RE ]] || die "not an e-mail address: $to"
  compose --profile admin run --rm --no-deps -T admin python manage.py sendtestemail "$to"
  if [[ "$(env_get IHR_MAIL)" == test ]]; then
    compose exec -T smtp-test wget -q -O - "http://127.0.0.1:8025/api/v1/search?query=to:$to" \
      | python3 -c 'import json,sys; n=json.load(sys.stdin)["messages_count"]; print("stand-in received", n, "message(s) to that address"); sys.exit(0 if n else 1)'
  else
    echo "Sent. Check the inbox of $to, and ZeptoMail's logs (Mail Agents > Processed emails)."
  fi
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
  for k in ADMIN CARE ANCHOR_WRITE ANCHOR_READ BACKUP LOGS_ARCHIVE; do key[${k}_KEY]="ihr$(hex 10)"; key[${k}_SECRET]="$(hex 20)"; done
  local SECRETS_TARGET="$sec"
  {
    echo "# SeaweedFS identities (P1-T8b3), written by scripts/staging.sh s3-init; the s3 container only."
    for k in ADMIN CARE ANCHOR_WRITE ANCHOR_READ BACKUP LOGS_ARCHIVE; do
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
  printf 'IHR_AUDIT_ANCHOR_ENDPOINT=http://s3:8333\nIHR_AUDIT_ANCHOR_LOCK_MODE=COMPLIANCE\nIHR_AUDIT_ANCHOR_BUCKET=%s\nIHR_AUDIT_ANCHOR_WRITE_KEY=%s\nIHR_AUDIT_ANCHOR_WRITE_SECRET=%s\n' \
    "$S3_ANCHOR_BUCKET_DEFAULT" "${key[ANCHOR_WRITE_KEY]}" "${key[ANCHOR_WRITE_SECRET]}" | write_secret anchor-write.env
  printf 'IHR_AUDIT_ANCHOR_ENDPOINT=http://s3:8333\nIHR_AUDIT_ANCHOR_LOCK_MODE=COMPLIANCE\nIHR_AUDIT_ANCHOR_BUCKET=%s\nIHR_AUDIT_ANCHOR_READ_KEY=%s\nIHR_AUDIT_ANCHOR_READ_SECRET=%s\n' \
    "$S3_ANCHOR_BUCKET_DEFAULT" "${key[ANCHOR_READ_KEY]}" "${key[ANCHOR_READ_SECRET]}" | write_secret anchor-read.env
  {
    echo "# Object storage on this server: SeaweedFS (P1-T8b3; scripts/staging.sh s3-init)"
    echo "IHR_S3=local"
    echo "IHR_S3_PATIENT_BUCKET=$patient"
    echo "IHR_S3_FACILITY_BUCKET=$facility"
    echo "IHR_S3_ANCHOR_BUCKET=$S3_ANCHOR_BUCKET_DEFAULT"
    echo "IHR_S3_BACKUP_BUCKET=ihr-backups"
    echo "IHR_S3_LOGS_ARCHIVE_BUCKET=ihr-log-archive"
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
  for k in ADMIN CARE ANCHOR_WRITE ANCHOR_READ LOGS_ARCHIVE; do
    for v in KEY SECRET; do
      export "S3_${k}_${v}=$(sed -n "s/^IHR_S3_${k}_${v}=//p" "$SECRETS/s3-gateway.env")"
      envs+=(-e "S3_${k}_${v}")
    done
  done
  compose --profile admin run --rm --no-deps -T "${envs[@]}" s3-tool "/ihr-s3/$script" \
    --endpoint http://s3:8333 --patient "$(env_get IHR_S3_PATIENT_BUCKET)" \
    --facility "$(env_get IHR_S3_FACILITY_BUCKET)" --anchor "$(env_get IHR_S3_ANCHOR_BUCKET)" "$@"
}

s3_archive_key() {  # the log archive's SeaweedFS key, for a stand-in set up before P1-T9c
  local f="$SECRETS/s3-gateway.env"
  grep -q '^IHR_S3_LOGS_ARCHIVE_KEY=' "$f" && return 0
  { cat "$f"
    printf 'IHR_S3_LOGS_ARCHIVE_KEY=ihr%s\nIHR_S3_LOGS_ARCHIVE_SECRET=%s\n' \
      "$(python3 -c 'import secrets;print(secrets.token_hex(10))')" "$(python3 -c 'import secrets;print(secrets.token_hex(20))')"
  } | write_secret s3-gateway.env.new
  mv "$SECRETS/s3-gateway.env.new" "$f"
}

s3_up() {  # SeaweedFS, then its buckets (idempotent)
  s3_archive_key
  compose up -d --wait s3
  s3_tool setup_buckets.py --lock-mode "$(env_get IHR_S3_LOCK_MODE)" --lock-days "$(env_get IHR_S3_LOCK_DAYS)" \
    --cors-origin "https://*.$(env_get IHR_TENANT_DOMAIN_SUFFIX)" \
    --backups "$(env_get IHR_S3_BACKUP_BUCKET || true)" \
    --log-archive "$(v="$(env_get IHR_S3_LOGS_ARCHIVE_BUCKET)"; echo "${v:-ihr-log-archive}")"
}

s3_check() {  # every key and lock, and the public paths through Traefik (files.<suffix>)
  local suffix; suffix="$(env_get IHR_TENANT_DOMAIN_SUFFIX)"
  # files.<suffix> is Traefik on the files network (port 8443 inside); its certificate is
  # checked by `cert` and smoke, so these requests skip TLS verification
  s3_tool check_keys.py --public-endpoint "https://files.$suffix:8443" --origin "https://alpha.$suffix" --insecure --size-limit \
    --log-archive "$(v="$(env_get IHR_S3_LOGS_ARCHIVE_BUCKET)"; echo "${v:-ihr-log-archive}")"
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
IHR_POSTGRES_IMAGE=unused:proof
IHR_KEYCLOAK_IMAGE=unused:proof
IHR_REDIS_DIGEST=unused
# Its own Traefik-Keycloak range: never the same as a staging stack running beside it
IHR_PROXY_NET=172.30.66.0/28
IHR_LOG_DRIVER=json-file
IHR_BIND=127.0.0.1
IHR_HTTP_PORT=${IHR_PROOF_HTTP_PORT:-18080}
IHR_HTTPS_PORT=${IHR_PROOF_HTTPS_PORT:-18443}
IHR_DNS_BIND=127.0.0.1
IHR_DNS_PORT=${IHR_PROOF_DNS_PORT:-15353}
IHR_ACME_SUBNET=172.30.63.0/28
IHR_ACME_API_IP=172.30.63.5
IHR_MONITORING_NET=172.30.67.0/26
IHR_MONITORING_DYNAMIC=172.30.67.32/27
IHR_PROMETHEUS_IP=172.30.67.6
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

# ---- Metrics and e-mail alerts (P1-T9a, compose.monitoring.yaml) ---------------------------------
MON_TEST_TO="ops@ihr-proof.test"
MON_TEST_DEADMAN="http://heartbeat-stub:8080/ping"
# The dead-man switch's ping address (healthchecks.io gives https://hc-ping.com/<uuid>)
DEADMAN_RE='^https://[a-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~-]+)+$'

# Set-up services that run once and exit; never reported as "not running" (host-metrics.sh)
MON_ONESHOTS='migrate|admin|keytool|s3-tool|traefik-data|alertmanager-data|logs-data|grafana-data'

monitoring_init() {  # monitoring_init <alert-recipient> [--from A] [--host H] [--port P] [--user U] | --test
  local envf="$OUT/.env" sec="$OUT/secrets" to="" from="" host="smtp.zeptomail.in" port=587
  local user="emailapikey" test=0 token="" deadman="" suffix pg_pw redis_pw
  [[ -f "$envf" && -d "$sec" ]] || die "no $envf / secrets: run scripts/staging.sh init <suffix> first"
  grep -q '^IHR_MONITORING=' "$envf" && die "$envf already has monitoring settings (IHR_MONITORING=...)"
  while (($#)); do
    case "$1" in
      --test) test=1 ;;
      --from) from="${2:?--from address}"; shift ;;
      --host) host="${2:?--host H}"; shift ;;
      --port) port="${2:?--port P}"; shift ;;
      --user) user="${2:?--user U}"; shift ;;
      -*) die "unknown option: $1" ;;
      *) to="$1" ;;
    esac
    shift
  done
  suffix="$(sed -n 's/^IHR_TENANT_DOMAIN_SUFFIX=//p' "$envf" | tail -1)"
  if ((test)); then
    # The proof: mail to the Mailpit stand-in with its own password, pings to the local counter
    grep -q '^IHR_MAIL=test' "$envf" || die "monitoring-init --test needs the mail stand-in (mail-init --test) first"
    host="$MAIL_TEST_HOST" port=1025 to="${to:-$MON_TEST_TO}" from="${from:-alerts@ihr-proof.test}"
    token="$(sed -n 's/^IHR_KEYCLOAK_SMTP_PASSWORD=//p' "$sec/mail.env")"
    deadman="$MON_TEST_DEADMAN"
  else
    [[ -n "$to" ]] || die "usage: scripts/staging.sh monitoring-init <alert-recipient> [--from address] (token and ping URL on stdin)"
    # alerts@<the domain above the hospitals' suffix>, e.g. alerts@amhcloud.in for app.amhcloud.in
    from="${from:-alerts@${suffix#*.}}"
    # The alerts' own ZeptoMail token (a Mail Agent of its own, not the web app's), then the
    # dead-man switch's ping URL: from the keyboard or two lines on stdin, never the command line
    if [[ -t 0 ]]; then
      read -rsp "ZeptoMail SMTP password for alerts (its Mail Agent's Send Mail token): " token; echo
      read -rp "healthchecks.io ping URL (https://hc-ping.com/...; empty: none yet): " deadman
    else
      IFS= read -r token || true; IFS= read -r deadman || true
    fi
    [[ ${#token} -ge 16 ]] || die "that does not look like a ZeptoMail token (too short)"
    [[ -z "$deadman" || "$deadman" =~ $DEADMAN_RE ]] || die "the ping URL must look like https://hc-ping.com/<uuid>"
    [[ "$port" == 587 ]] || die "only port 587 (STARTTLS) is supported"
  fi
  [[ "$to" =~ $EMAIL_RE ]] || die "not an e-mail address: $to"
  [[ "$from" =~ $EMAIL_RE ]] || die "not an e-mail address: $from"
  [[ "$host" =~ $DOMAIN_RE ]] || die "not a host name: $host"
  [[ "$user" =~ ^[A-Za-z0-9@._-]+$ ]] || die "not a user name: $user"
  case "$token" in ""|*[[:space:]\"\'\\\$\`]*) die "the token is empty or has characters a secret file cannot hold" ;; esac
  local SECRETS_TARGET="$sec"
  # Alertmanager reads these three as files (no newline: the whole file is the value)
  printf '%s' "$token" | write_secret alertmanager-smtp-password
  printf '%s' "$deadman" | write_secret alertmanager-watchdog-url
  printf '%s' "${deadman:+$deadman/fail}" | write_secret alertmanager-watchdog-fail-url
  logs_secrets
  # The exporters' own database and Redis users (created by `up`; monitoring_db_role and
  # compose.yaml's redis command). Redis gets only the SHA-256 of its password.
  pg_pw="$(rand 32)"; redis_pw="$(rand 32)"
  printf 'DATA_SOURCE_URI=db:5432/postgres?sslmode=disable\nDATA_SOURCE_USER=ihr_monitor\nDATA_SOURCE_PASS=%s\n' "$pg_pw" \
    | write_secret postgres-exporter.env
  printf 'REDIS_PASSWORD=%s\n' "$redis_pw" | write_secret redis-exporter.env
  { grep -v '^IHR_REDIS_MONITOR_SHA256=' "$sec/redis.env" || true
    printf 'IHR_REDIS_MONITOR_SHA256=%s\n' "$(printf '%s' "$redis_pw" | sha256sum | cut -d' ' -f1)"
  } | write_secret redis.env.new
  mv "$sec/redis.env.new" "$sec/redis.env"
  {
    echo "# Metrics and e-mail alerts (P1-T9a; scripts/staging.sh monitoring-init)"
    echo "IHR_MONITORING=$([[ $test == 1 ]] && echo test || echo on)"
    echo "IHR_ALERT_TO=$to"
    echo "IHR_ALERT_FROM=$from"
    echo "IHR_ALERT_SMTP_HOST=$host"
    echo "IHR_ALERT_SMTP_PORT=$port"
    echo "IHR_ALERT_SMTP_USER=$user"
    echo "IHR_ALERT_DEADMAN=$([[ -n $deadman ]] && echo on || echo off)"
    # A proof on this machine: the host timer's files next to the stack, not in /var/lib; and
    # Fluent Bit starts at the end of this desktop's journal, not at its beginning
    echo "IHR_GRAFANA_IMAGE=${IHR_GRAFANA_IMAGE:-indiehr-grafana:staging}"
    if [[ -f "$OUT/.local" ]]; then
      echo "IHR_TEXTFILE_DIR=$OUT/generated/monitoring/textfile"
      echo "IHR_LOGS_FROM_TAIL=on"
      # No SSH server on this machine: the browser goes to Grafana's address itself
      echo "IHR_GRAFANA_DOMAIN=10.203.0.50"
    fi
  } >> "$envf"
  echo "Monitoring settings added (alerts to $to from $from via $host:$port); secrets in $sec (mode 600)."
  [[ -n "$deadman" ]] || echo "No dead-man switch yet: once it exists, add its URL with scripts/staging.sh monitoring-deadman."
}

monitoring_deadman() {  # monitoring_deadman: (re)set the dead-man switch's ping URL (stdin or keyboard)
  local envf="$OUT/.env" url=""
  [[ "$(sed -n 's/^IHR_MONITORING=//p' "$envf" | tail -1)" == on ]] || die "no monitoring here (monitoring-init), or a proof (its stand-in needs no URL)"
  if [[ -t 0 ]]; then read -rp "healthchecks.io ping URL: " url; else IFS= read -r url || true; fi
  [[ "$url" =~ $DEADMAN_RE ]] || die "the ping URL must look like https://hc-ping.com/<uuid>"
  local SECRETS_TARGET="$OUT/secrets"
  printf '%s' "$url" | write_secret alertmanager-watchdog-url
  printf '%s' "$url/fail" | write_secret alertmanager-watchdog-fail-url
  sed -i 's/^IHR_ALERT_DEADMAN=.*/IHR_ALERT_DEADMAN=on/' "$envf"
  echo "Dead-man switch set. Next: scripts/staging.sh up (Alertmanager starts pinging it)."
}

logs_secrets() {  # the log store's secrets (P1-T9b), each made once (mode 600, never printed):
  # logs-admin-key   deleting or rewriting logs: VictoriaLogs checks it; only the operator holds it
  # logs-http-password (+ logs-shipper.env for Fluent Bit): the store's password (user ihr-logs)
  # logs-metrics-key its /metrics only (counters); Prometheus's target file carries it
  local d="${SECRETS_TARGET:-$SECRETS}" pw
  [[ -s "$d/logs-admin-key" ]] || rand 32 | tr -d '\n' | write_secret logs-admin-key
  if [[ ! -s "$d/logs-http-password" || ! -s "$d/logs-shipper.env" ]]; then
    pw="$(rand 32)"
    printf '%s' "$pw" | write_secret logs-http-password
    printf 'IHR_LOGS_HTTP_PASSWORD=%s\n' "$pw" | write_secret logs-shipper.env
  fi
  [[ -s "$d/logs-metrics-key" ]] || rand 24 | tr -d '\n' | write_secret logs-metrics-key
}

dashboards_secrets() {  # Grafana's secrets (P1-T9d), each made once (mode 600, never printed):
  # grafana-admin.env      the admin's generated name and e-mail: a lock-out counts failed
  #                        sign-ins per name, so a guessable name could keep the operator out
  # grafana-admin-password the admin's password (Grafana stores it on its first start;
  #                        change it with grafana-reset-admin, never by editing the file)
  # grafana-secret-key     encrypts the datasource passwords in Grafana's database
  # logs-reader-password (+ logs-reader.env for vmauth): Grafana's read-only login to the logs
  # logs-reader-key        vmauth's own pages (metrics, flags, reload); not the store's admin key
  local d="${SECRETS_TARGET:-$SECRETS}" name up
  if [[ ! -s "$d/grafana-admin.env" || ! -s "$d/grafana-admin-password" || ! -s "$d/grafana-secret-key" ]]; then
    # Grafana holds these in its database from the first start: files made anew would not match it
    if [[ -z "${SECRETS_TARGET:-}" ]] && docker volume inspect "${IHR_PROJECT:-indiehr-staging}_grafanadata" >/dev/null 2>&1; then
      die "Grafana's data volume exists but secrets/grafana-admin.env, grafana-admin-password or grafana-secret-key is missing: restore them (owner's password manager), or remove the volume ${IHR_PROJECT:-indiehr-staging}_grafanadata (only preferences are lost: dashboards and datasources come from git) and run up again"
    fi
    name="ops-$(python3 -c 'import secrets;print(secrets.token_hex(6))')"
    printf 'GF_SECURITY_ADMIN_USER=%s\nGF_SECURITY_ADMIN_EMAIL=%s@admin.invalid\n' "$name" "$name" | write_secret grafana-admin.env
    rand 32 | tr -d '\n' | write_secret grafana-admin-password
    rand 32 | tr -d '\n' | write_secret grafana-secret-key
  fi
  if [[ ! -s "$d/logs-reader-password" || ! -s "$d/logs-reader.env" ]]; then
    [[ -s "$d/logs-http-password" ]] || die "no logs-http-password (logs_secrets first)"
    rand 32 | tr -d '\n' | write_secret logs-reader-password
    up="$(printf 'ihr-logs:%s' "$(cat "$d/logs-http-password")" | base64 -w0)"
    printf 'IHR_LOGS_READER_PASSWORD=%s\nIHR_LOGS_UPSTREAM_BASIC=%s\n' "$(cat "$d/logs-reader-password")" "$up" \
      | write_secret logs-reader.env
  fi
  [[ -s "$d/logs-reader-key" ]] || rand 32 | tr -d '\n' | write_secret logs-reader-key
}
grafana_ip() {  # Grafana's fixed address on `dashboards`, the only one it listens on
  local v; v="$(env_get IHR_GRAFANA_IP)"; v="${v:-10.203.0.50}"
  [[ "$v" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "IHR_GRAFANA_IP in .env is not an IPv4 address"
  echo "$v"
}
grafana_domain() {  # the one host name Grafana answers to: grafana.localhost through the tunnel;
  # a proof on this machine, with no SSH server, uses the fixed address itself (monitoring_init)
  local v; v="$(env_get IHR_GRAFANA_DOMAIN)"; v="${v:-grafana.localhost}"
  [[ "$v" == grafana.localhost || ( -f "$DIR/.local" && "$v" == "$(grafana_ip)" ) ]] \
    || die "IHR_GRAFANA_DOMAIN must be grafana.localhost (a proof on this machine: Grafana's address)"
  echo "$v"
}
grafana_image() {  # built by scripts/deploy.sh (a server) or here (a proof on this machine); a
  # proof rebuilds when the Dockerfile changed (its hash is the image's label, as deploy.sh sets)
  local img df sum have
  img="$(env_get IHR_GRAFANA_IMAGE)"; img="${img:-indiehr-grafana:staging}"
  df="$ROOT/deploy/docker/grafana.Dockerfile"
  have="$(docker image inspect -f '{{index .Config.Labels "ihr.dockerfile.sha256"}}' "$img" 2>/dev/null || true)"
  if [[ -f "$DIR/.local" && -f "$df" ]]; then
    sum="$(sha256sum "$df" | cut -d' ' -f1)"
    if [[ "$have" != "$sum" ]]; then
      echo "Building $img (deploy/docker/grafana.Dockerfile)"
      docker buildx build -q --load --label "ihr.dockerfile.sha256=$sum" -f "$df" -t "$img" "$ROOT/deploy/docker" >/dev/null
    fi
  elif [[ -z "$have" ]]; then
    die "image $img is not here (scripts/deploy.sh builds and sends it)"
  fi
}
grafana_reset_admin() {  # grafana-reset-admin: a new admin password, in Grafana and the file together
  [[ -n "$(compose ps -q grafana 2>/dev/null)" ]] || die "Grafana is not running (scripts/staging.sh up)"
  rand 32 | tr -d '\n' | write_secret grafana-admin-password.new
  # On stdin, never as an argument (ps would show it)
  if compose exec -T grafana grafana cli --homepath /usr/share/grafana admin reset-admin-password --password-from-stdin \
       < "$SECRETS/grafana-admin-password.new" >/dev/null; then
    mv "$SECRETS/grafana-admin-password.new" "$SECRETS/grafana-admin-password"
    echo "New Grafana admin password in $SECRETS/grafana-admin-password (name: $(sed -n 's/^GF_SECURITY_ADMIN_USER=//p' "$SECRETS/grafana-admin.env"))."
    echo "A locked-out name unlocks by itself 5 minutes after the last failed try."
  else
    rm -f "$SECRETS/grafana-admin-password.new"
    die "Grafana refused the reset; the old password stays"
  fi
}

textfile_dir() { local v; v="$(env_get IHR_TEXTFILE_DIR)"; echo "${v:-/var/lib/indiehr/textfile}"; }

json_targets() {  # json_targets <file> [<target> <labels as k=v,k=v>]...: a Prometheus file_sd file
  local f="$1"; shift
  python3 -c 'import json,sys
a = sys.argv[2:]
groups = [{"targets": [a[i]], "labels": dict(kv.split("=", 1) for kv in a[i + 1].split(",") if kv)} for i in range(0, len(a), 2)]
with open(sys.argv[1] + ".new", "w") as out:
    json.dump(groups, out, indent=1)' "$f" "$@"
  chmod 644 "$f.new"
  mv "$f.new" "$f"
}

monitoring_config() {  # generated/monitoring/: Prometheus's targets and alertmanager.yml, from .env
  local d="$GEN/monitoring" suffix to from host port user deadman hello tls module tmp
  suffix="$(env_get IHR_TENANT_DOMAIN_SUFFIX)"
  to="$(env_get IHR_ALERT_TO)"; from="$(env_get IHR_ALERT_FROM)"
  host="$(env_get IHR_ALERT_SMTP_HOST)"; port="$(env_get IHR_ALERT_SMTP_PORT)"; user="$(env_get IHR_ALERT_SMTP_USER)"
  deadman="$(env_get IHR_ALERT_DEADMAN)"
  # Checked again: .env may have been edited, and these go into YAML
  [[ "$suffix" =~ $DOMAIN_RE && "$host" =~ $DOMAIN_RE ]] || die "IHR_TENANT_DOMAIN_SUFFIX / IHR_ALERT_SMTP_HOST in .env are not domain names"
  [[ "$to" =~ $EMAIL_RE && "$from" =~ $EMAIL_RE ]] || die "IHR_ALERT_TO / IHR_ALERT_FROM in .env are not e-mail addresses"
  [[ "$port" =~ ^[0-9]+$ && "$user" =~ ^[A-Za-z0-9@._-]+$ ]] || die "IHR_ALERT_SMTP_PORT / IHR_ALERT_SMTP_USER in .env are not valid"
  mkdir -p "$d/targets"
  # Traefik's own certificate until acme-init (and in a proof): the probe reads its expiry
  # without checking who issued it
  module=https_2xx; [[ -n "$(env_get IHR_ACME)" ]] || module=https_2xx_selfsigned
  json_targets "$d/targets/probe-public.json" \
    "https://auth.$suffix:8443/realms/indiehr/.well-known/openid-configuration" "endpoint=auth,module=$module" \
    "https://status.$suffix:8443/healthz" "endpoint=status,module=$module"
  json_targets "$d/targets/probe-internal.json" \
    "http://backend:9000/health/" "service=backend" "http://keycloak:9000/health/ready" "service=keycloak"
  if [[ -n "$(env_get IHR_ACME)" ]]; then json_targets "$d/targets/probe-dns.json" "acme-dns:53" "service=acme-dns"
  else json_targets "$d/targets/probe-dns.json"; fi
  # VictoriaLogs's /metrics key as a URL parameter (it guards counters only)
  local mkey=""
  [[ ! -r "$SECRETS/logs-metrics-key" ]] || mkey="$(cat "$SECRETS/logs-metrics-key")"
  [[ -z "$mkey" || "$mkey" =~ ^[A-Za-z0-9_-]+$ ]] || die "secrets/logs-metrics-key is not a plain key"
  json_targets "$d/targets/victorialogs.json" "victorialogs:9428" "${mkey:+__param_authKey=$mkey}"
  # The log archive's own metrics (P1-T9c), when it runs here
  if [[ -n "$(env_get IHR_LOGS_ARCHIVE)" ]]; then json_targets "$d/targets/logs-archive.json" "logs-archive:9100" ""
  else json_targets "$d/targets/logs-archive.json"; fi
  if [[ "$(env_get IHR_S3)" == local ]]; then json_targets "$d/targets/s3.json" "s3:9327" "service=s3"
  else json_targets "$d/targets/s3.json"; fi
  hello="${suffix#*.}"
  if [[ "$(env_get IHR_MONITORING)" == test ]]; then
    tls="        tls_config: {min_version: TLS12, server_name: \"$host\", ca_file: /proof/ca.pem}"
  else
    tls="        tls_config: {min_version: TLS12, server_name: \"$host\"}"
  fi
  tmp="$(mktemp "$d/.alertmanager.XXXXXX")"
  cat > "$tmp" <<EOF
# Written by scripts/staging.sh from .env (P1-T9a); changes here are overwritten.
# Secrets are files Alertmanager reads (secrets/alertmanager-*), never written here.
global:
  smtp_smarthost: "$host:$port"
  smtp_from: "IndieHR alerts <$from>"
  smtp_hello: "$hello"
  smtp_auth_username: "$user"
  smtp_auth_password_file: /run/ihr-alerts/smtp-password
  # STARTTLS required (ZeptoMail on 587); never plain text
  smtp_require_tls: true

route:
  receiver: email
  group_by: [alertname, severity]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    # The outside dead-man switch: pinged about once a minute while all is well
    - receiver: $([[ "$deadman" == on ]] && echo deadman || echo blackhole)
      matchers: ['alertname="Watchdog"']
      group_wait: 0s
      group_interval: 1m
      repeat_interval: 1m
    # A mail every day at 09:00 IST (the mail path works)
    - receiver: email-heartbeat
      matchers: ['alertname="DailyHeartbeat"']
      group_wait: 0s
      repeat_interval: 24h
    # Mail is failing: tell the dead-man switch (its /fail), and try mail too
    - receiver: $([[ "$deadman" == on ]] && echo deadman-fail || echo blackhole)
      matchers: ['alertname="AlertmanagerNotificationsFailing"']
      continue: true
      repeat_interval: 1h
    - receiver: email
      matchers: ['severity="critical"']
      repeat_interval: 4h
    # Sign-in abuse naming an address (log alerts, notify="anytime"): mailed at once, day or
    # night, at most daily per address (owner, Q-O9); still a warning for the inhibit rule
    - receiver: email
      matchers: ['notify="anytime"']
      repeat_interval: 24h
    # Warnings wait for the day (08:00-22:00 IST) and repeat once a day
    - receiver: email
      matchers: ['severity="warning"']
      repeat_interval: 24h
      active_time_intervals: [daytime-ist]

time_intervals:
  - name: daytime-ist
    time_intervals:
      - times: [{start_time: "08:00", end_time: "22:00"}]
        location: Asia/Kolkata

inhibit_rules:
  # The critical alert says it already: no warning of the same name for the same thing beside
  # it. Every label that tells instances apart is compared (one missing on both counts as
  # equal), so a critical for db never mutes a warning for provisioner, nor /boot one for /.
  - source_matchers: ['severity="critical"']
    target_matchers: ['severity="warning"']
    equal: [alertname, service, job, instance, mountpoint, purpose, endpoint]
  # A service that is down as a whole: no separate container alerts for it
  - source_matchers: ['alertname=~"BackendHealth|KeycloakNotReady|BackupUnhealthy"']
    target_matchers: ['alertname=~"ContainerUnhealthy|ContainerNotRunning|ContainerRestarting|ContainerMemoryNearLimit"']
    equal: [service]

receivers:
  - name: email
    email_configs:
      - to: "$to"
        send_resolved: true
$tls
        headers:
          Subject: '[IndieHR {{ .Status | toUpper }}] {{ .CommonLabels.alertname }} ({{ .CommonLabels.severity }})'
  - name: email-heartbeat
    email_configs:
      - to: "$to"
        send_resolved: false
$tls
        headers:
          Subject: '[IndieHR] Daily alert check: mail works'
  - name: deadman
    webhook_configs:
      - url_file: /run/ihr-alerts/watchdog-url
        send_resolved: false
        max_alerts: 1
  - name: deadman-fail
    webhook_configs:
      - url_file: /run/ihr-alerts/watchdog-fail-url
        send_resolved: false
        max_alerts: 1
  - name: blackhole
EOF
  chmod 644 "$tmp"
  mv "$tmp" "$d/alertmanager.yml"
}

monitoring_db_role() {  # the exporter's login role: pg_monitor only, its password from the secret
  local pw
  pw="$(sed -n 's/^DATA_SOURCE_PASS=//p' "$SECRETS/postgres-exporter.env")"
  [[ "$pw" =~ ^[A-Za-z0-9_-]{20,}$ ]] || die "secrets/postgres-exporter.env has no usable DATA_SOURCE_PASS"
  # On stdin, never a command line; the server never logs the statement (compose.yaml)
  compose exec -T db psql -U postgres -d postgres -v ON_ERROR_STOP=1 -q >/dev/null <<SQL
SELECT 'CREATE ROLE ihr_monitor' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ihr_monitor')\gexec
ALTER ROLE ihr_monitor WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 3 PASSWORD '$pw';
GRANT pg_monitor TO ihr_monitor;
-- initdb/10-roles.sh revokes CONNECT from PUBLIC on every database: this one only
GRANT CONNECT ON DATABASE postgres TO ihr_monitor;
SQL
}

MON_DIR="$DIR/../monitoring"   # what compose.monitoring.yaml mounts as ../monitoring
LOGS_DIR="$DIR/../logs"        # what compose.logs.yaml mounts as ../logs
monitoring_settings() {  # monitoring_settings <service>: the files a service reads its settings from
  case "$1" in
    prometheus) printf '%s\n' "$MON_DIR/prometheus/prometheus.yml" "$MON_DIR"/prometheus/rules/*.yml ;;
    alertmanager) printf '%s\n' "$GEN/monitoring/alertmanager.yml" ;;
    blackbox-exporter) printf '%s\n' "$MON_DIR/blackbox/blackbox.yml" ;;
    fluent-bit) printf '%s\n' "$LOGS_DIR"/fluent-bit/* ;;
    vmalert) printf '%s\n' "$LOGS_DIR"/alerts/*.yml ;;
    # Read at start only (the dashboards themselves are rescanned every minute)
    grafana) printf '%s\n' "$DIR"/../grafana/provisioning/*/*.yaml ;;
    logs-reader) printf '%s\n' "$DIR/../grafana/logs-reader.yml" ;;
    logs-archive) printf '%s\n' "$LOGS_DIR"/archive/*.py ;;
  esac
}
monitoring_settings_sum() {  # one hash over a service's settings files (names and contents; no secret:
  # the token and the ping URL are files Alertmanager reads on each send)
  local f
  monitoring_settings "$1" | while IFS= read -r f; do echo "$f"; cat "$f"; done | sha256sum | cut -d' ' -f1
}

monitoring_up() {  # after the stack: settings, the database role, the host's files, then the services
  local svc=(prometheus alertmanager node-exporter blackbox-exporter postgres-exporter redis-exporter
             victorialogs fluent-bit vmalert logs-reader grafana) out s
  local stale=() stamp restamp=(prometheus alertmanager blackbox-exporter fluent-bit vmalert logs-reader grafana)
  # Monitoring set up before P1-T9b has no log-store secrets yet (the targets need one), and
  # before P1-T9d none of Grafana's
  logs_secrets
  dashboards_secrets
  grafana_image
  grafana_domain >/dev/null
  monitoring_config
  [[ -n "$IHR_JOURNAL_GID" ]] || IHR_JOURNAL_GID="$(getent group systemd-journal | cut -d: -f3)"
  [[ -n "$IHR_JOURNAL_GID" && -d /var/log/journal ]] || die "no persistent journal here (/var/log/journal, group systemd-journal): run deploy/host/bootstrap.sh"
  out="$(textfile_dir)"
  # node_exporter mounts it: Docker would create a missing folder as root
  if [[ ! -d "$out" ]]; then
    [[ -f "$DIR/.local" ]] || die "no $out: run deploy/host/bootstrap.sh on this server (it makes the folder and the timer)"
    mkdir -p "$out"
  fi
  monitoring_db_role
  monitoring_textfile --keys
  [[ "$(env_get IHR_MONITORING)" != test ]] || svc+=(heartbeat-stub)
  if [[ -n "$(env_get IHR_LOGS_ARCHIVE)" ]]; then svc+=(logs-archive); restamp+=(logs-archive); fi
  # Settings are single files mounted read-only, rewritten as new files (deploys, .env changes):
  # a running container keeps the old ones, and neither Prometheus nor Alertmanager rereads its
  # rules or routes by itself. A container whose settings changed since it started (or that
  # has no stamp yet) is recreated; Alertmanager's silences and notification log stay in amdata.
  for s in "${restamp[@]}"; do
    stamp="$GEN/monitoring/.settings-$s"
    if [[ -n "$(compose ps -aq "$s" 2>/dev/null)" && "$(cat "$stamp" 2>/dev/null)" != "$(monitoring_settings_sum "$s")" ]]; then
      stale+=("$s")
    fi
  done
  compose up -d --wait "${svc[@]}"
  if ((${#stale[@]})); then
    echo "Settings changed: recreating ${stale[*]}"
    compose up -d --wait --no-deps --force-recreate "${stale[@]}"
  fi
  for s in "${restamp[@]}"; do
    monitoring_settings_sum "$s" > "$GEN/monitoring/.settings-$s"
  done
}

monitoring_textfile() {  # monitoring_textfile [--keys]: the host timer's run (every minute)
  local out services spool keys=0
  [[ "${1:-}" != --keys ]] || keys=1
  [[ -n "$(env_get IHR_MONITORING)" ]] || return 0
  out="$(textfile_dir)"
  [[ -d "$out" ]] || die "no $out (deploy/host/bootstrap.sh makes it)"
  # The services this stack should have running: one-shot set-up services (restart "no") exit
  # by design and are left out (host-metrics.sh skips the same names on its own as well)
  services="$(compose config --services 2>/dev/null | grep -Evx "$MON_ONESHOTS" | tr '\n' ' ' || true)"
  spool="$(env_get IHR_BACKUP_SPOOL_MAX_MB)"
  "$ROOT/deploy/monitoring/host-metrics.sh" --out "$out" --project "${IHR_PROJECT:-indiehr-staging}" \
    --services "$services" --backup-spool-max-mb "${spool:-2048}"
  logs_cap_metric "$out"
  # The key ages hourly (three one-off key-tool containers)
  if ((keys)) || [[ ! -f "$out/ihr_client_keys.prom" || -n "$(find "$out/ihr_client_keys.prom" -mmin +55)" ]]; then
    monitoring_key_metrics "$out"
  fi
}

logs_cap_bytes() {  # IHR_LOGS_MAX_SIZE (as VictoriaLogs reads it: 20GiB, 500MB, ...) in bytes
  local v n u; v="$(env_get IHR_LOGS_MAX_SIZE)"; v="${v:-20GiB}"
  [[ "$v" =~ ^([0-9]+)(|KB|MB|GB|TB|KiB|MiB|GiB|TiB)$ ]] || die "IHR_LOGS_MAX_SIZE in .env is not a size (e.g. 20GiB): $v"
  n="${BASH_REMATCH[1]}"; u="${BASH_REMATCH[2]}"
  case "$u" in
    "") echo "$n" ;;
    KB) echo $((n * 1000)) ;; MB) echo $((n * 1000 ** 2)) ;; GB) echo $((n * 1000 ** 3)) ;; TB) echo $((n * 1000 ** 4)) ;;
    KiB) echo $((n * 1024)) ;; MiB) echo $((n * 1024 ** 2)) ;; GiB) echo $((n * 1024 ** 3)) ;; TiB) echo $((n * 1024 ** 4)) ;;
  esac
}

logs_cap_metric() {  # <out>/ihr_logs.prom: the log store's disk cap (LogStoreNearCap compares)
  local out="$1" tmp
  tmp="$(mktemp "$out/.ihr_logs.XXXXXX")"
  printf '# HELP ihr_logs_max_bytes The log store'"'"'s disk cap (IHR_LOGS_MAX_SIZE).\n# TYPE ihr_logs_max_bytes gauge\nihr_logs_max_bytes %s\n' "$(logs_cap_bytes)" > "$tmp"
  chmod 644 "$tmp"
  mv "$tmp" "$out/ihr_logs.prom"
}

monitoring_key_metrics() {  # <out>/ihr_client_keys.prom: each purpose's active key and its age
  local out="$1" p json lines=() tmp
  for p in "${KEY_PURPOSES[@]}"; do
    # A failed read writes nothing: the file goes stale and ClientKeyCheckStale says so
    json="$(kt "$p" status --json 2>/dev/null)" || return 0
    lines+=("$(python3 -c 'import json,sys
label = "{purpose=\"%s\"}" % sys.argv[1]
active = [k for k in json.load(sys.stdin)["keys"] if k["state"] == "active"]
print("ihr_client_key_active%s %d" % (label, 1 if active else 0))
if active:
    print("ihr_client_key_active_age_days%s %d" % (label, int(active[0]["age_days"])))' "$p" <<< "$json")") || return 0
  done
  tmp="$(mktemp "$out/.ihr_client_keys.XXXXXX")"
  {
    echo "# HELP ihr_client_key_active 1 when the purpose has an active signing key"
    echo "# TYPE ihr_client_key_active gauge"
    echo "# HELP ihr_client_key_active_age_days Age of the active signing key in days"
    echo "# TYPE ihr_client_key_active_age_days gauge"
    printf '%s\n' "${lines[@]}" | sort
    echo "# HELP ihr_client_keys_checked_timestamp_seconds When the keys were last read"
    echo "# TYPE ihr_client_keys_checked_timestamp_seconds gauge"
    echo "ihr_client_keys_checked_timestamp_seconds $(date +%s)"
  } > "$tmp"
  chmod 644 "$tmp"
  mv "$tmp" "$out/ihr_client_keys.prom"
}

prom_get() {  # prom_get <url>: a GET from inside the Prometheus container (monitoring network;
  # by address or 127.0.0.1 only: its wget cannot resolve service names, see monitoring_smoke)
  compose exec -T prometheus wget -q -T 10 -O - "$1"
}

vl_ip() {  # VictoriaLogs's fixed address on `logs` (the SSH tunnel and smoke use it)
  local v; v="$(env_get IHR_VICTORIALOGS_IP)"; v="${v:-10.203.0.36}"
  [[ "$v" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "IHR_VICTORIALOGS_IP in .env is not an IPv4 address"
  echo "$v"
}
vmalert_ip() {
  local v; v="$(env_get IHR_VMALERT_IP)"; v="${v:-10.203.0.37}"
  [[ "$v" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "IHR_VMALERT_IP in .env is not an IPv4 address"
  echo "$v"
}

am_ip() {  # Alertmanager's fixed address on `alerting`, the only one it listens on
  local v; v="$(env_get IHR_ALERTMANAGER_IP)"; v="${v:-10.203.0.20}"
  [[ "$v" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "IHR_ALERTMANAGER_IP in .env is not an IPv4 address"
  echo "$v"
}

am_tool() {  # am_tool <args>: Alertmanager's own amtool, against itself
  local ip; ip="$(am_ip)"
  compose exec -T alertmanager amtool --alertmanager.url="http://$ip:9093" "$@"
}

stub_count() {  # the dead-man switch stand-in's ping count, asked inside its own container
  compose exec -T heartbeat-stub python -I -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8080/count', timeout=5).read().decode())"
}

monitoring_smoke() {  # smoke's monitoring part: targets up, rules loaded, Alertmanager set up
  local i out bad
  # The host's files fresh now (a server's timer does this every minute; a proof on this
  # machine has no timer: run `scripts/staging.sh monitoring-textfile` to refresh them)
  monitoring_textfile
  check "monitoring: nothing published on the host (SSH tunnel only)" "" \
    "$(for s in prometheus alertmanager node-exporter blackbox-exporter postgres-exporter redis-exporter victorialogs fluent-bit vmalert logs-reader grafana; do docker port "$(compose ps -q "$s")" 2>/dev/null; done)"
  # Every target scraped and up (the first scrapes take up to 30 s after a start)
  for i in $(seq 1 12); do
    bad="$(prom_get 'http://127.0.0.1:9090/api/v1/targets?state=active' | python3 -c 'import json,sys
t = json.load(sys.stdin)["data"]["activeTargets"]
print(" ".join(sorted("%s(%s)" % (x["labels"]["job"], x["labels"].get("endpoint") or x["labels"].get("service") or "") for x in t if x["health"] != "up")) if t else "none")' 2>/dev/null || echo unreadable)"
    [[ -n "$bad" ]] || break
    sleep 10
  done
  check "monitoring: every Prometheus target up" "" "$bad"
  out="$(prom_get http://127.0.0.1:9090/api/v1/rules | python3 -c 'import json,sys
g = json.load(sys.stdin)["data"]["groups"]
rules = [r for x in g for r in x["rules"]]
err = [r["name"] for r in rules if r.get("health") == "err"]
print("%d groups, %d rules%s" % (len(g), len(rules), (", errors: " + " ".join(err)) if err else ""))' 2>/dev/null || echo unreadable)"
  check "monitoring: alert rules loaded without errors" "$(grep -c -- '- alert:' "$ROOT"/deploy/monitoring/prometheus/rules/*.yml | awk -F: '{n+=$2} END {print n}') rules" \
    "$(sed -n 's/^[0-9]* groups, \([0-9]* rules\)$/\1/p' <<< "$out")"
  # The exporters answer (target up) even when their login fails: their own verdict counts
  for s in pg_up redis_up; do
    check "monitoring: $s is 1 (the exporter reaches its server and logs in)" 1 \
      "$(prom_get "http://127.0.0.1:9090/api/v1/query?query=$s" | python3 -c 'import json,sys
r = json.load(sys.stdin)["data"]["result"]
print(" ".join(sorted(x["value"][1] for x in r)) or "absent")' 2>/dev/null || echo unreadable)"
  done
  check "monitoring: Prometheus hands alerts to Alertmanager" 1 \
    "$(prom_get http://127.0.0.1:9090/api/v1/alertmanagers | python3 -c 'import json,sys;print(len(json.load(sys.stdin)["data"]["activeAlertmanagers"]))' 2>/dev/null || echo 0)"
  # Configuration loaded, and no cluster: the gossip port stays closed
  # Asked through Alertmanager's own amtool: the Prometheus image's wget cannot resolve service
  # names on Docker's resolver (it reads the "search ." line wrongly), though Prometheus can
  check "monitoring: Alertmanager configured, gossip off" "loaded disabled" \
    "$( (am_tool config show | grep -q '^receivers:' && echo loaded || echo empty) 2>/dev/null) $(am_tool cluster show 2>/dev/null | sed -n 's/^Cluster Status: *//p')"
  # The always-firing Watchdog reaches Alertmanager (the first rule run is up to 30 s after a start)
  for i in $(seq 1 9); do
    out="$(am_tool alert query alertname=Watchdog -o simple 2>/dev/null | grep -q '^Watchdog ' && echo yes || echo no)"
    [[ "$out" == yes ]] && break
    sleep 10
  done
  check "monitoring: Watchdog alert reaches Alertmanager" yes "$out"
  if [[ "$(env_get IHR_MONITORING)" == test ]]; then
    for i in $(seq 1 9); do
      out="$(stub_count | python3 -c 'import json,sys;print("yes" if json.load(sys.stdin)["ping"] > 0 else "no")' 2>/dev/null || echo no)"
      [[ "$out" == yes ]] && break
      sleep 10
    done
    check "monitoring: the dead-man switch stand-in receives pings" yes "$out"
  elif [[ "$(env_get IHR_ALERT_DEADMAN)" != on ]]; then
    echo "WARN  monitoring: no dead-man switch yet (scripts/staging.sh monitoring-deadman)"
  fi
  # Traefik's metrics answer Prometheus's address only: the web app, on the same network as
  # Traefik, is refused
  check "Traefik metrics refused to other containers" 403 \
    "$(compose exec -T backend python -c 'import urllib.request, urllib.error
try:
    print(urllib.request.urlopen("http://traefik:8083/metrics", timeout=5).status)
except urllib.error.HTTPError as e:
    print(e.code)' 2>/dev/null || echo unreachable)"
  check "host metrics written in the last 5 minutes" yes \
    "$([[ -n "$(find "$(textfile_dir)/ihr_host.prom" -mmin -5 2>/dev/null)" ]] && echo yes || echo no)"
  logs_smoke
  dashboards_smoke
}

dashboards_smoke() {  # smoke's dashboards part (P1-T9d): Grafana itself, then where it can reach
  local cases=() out days net
  # Panels allowed to be empty here (deploy/grafana/allowed-empty.txt): what this site has on
  [[ -f "$DIR/.local" ]] && cases+=(--case proof)
  [[ -n "$(env_get IHR_BACKUP)" ]] || cases+=(--case backup-off)
  if [[ -z "$(env_get IHR_LOGS_ARCHIVE)" ]]; then
    cases+=(--case archive-off)
  else
    days="$(prom_get 'http://127.0.0.1:9090/api/v1/query?query=max(ihr_logs_archive_days)' 2>/dev/null | python3 -c 'import json,sys
r = json.load(sys.stdin)["data"]["result"]
print(int(float(r[0]["value"][1])) if r else 0)' 2>/dev/null || echo 0)"
    [[ "$days" != 0 ]] || cases+=(--case archive-new)
  fi
  # From this host to Grafana's fixed address, as the SSH tunnel does (and its host name)
  if python3 "$ROOT/scripts/check-dashboards.py" --url "http://$(grafana_ip):3000" --domain "$(grafana_domain)" \
      --admin-env "$SECRETS/grafana-admin.env" --password-file "$SECRETS/grafana-admin-password" \
      --dashboards "$DIR/../grafana/dashboards" --allowed-empty "$DIR/../grafana/allowed-empty.txt" "${cases[@]}"; then
    out=ok
  else
    out=failed
  fi
  check "grafana: every live check above" ok "$out"
  # The containers that take traffic from the internet cannot reach Grafana at all
  check "grafana: the web app cannot reach it" unreachable \
    "$(compose exec -T backend python -c 'import socket, sys
try:
    socket.create_connection((sys.argv[1], 3000), timeout=5); print("reachable")
except OSError:
    print("unreachable")' "$(grafana_ip)" 2>/dev/null || echo error)"
  # ...and Grafana reaches Prometheus (the control), but not the log store itself nor Alertmanager
  check "grafana: reaches Prometheus, not the log store or Alertmanager" "reachable unreachable unreachable" \
    "$(for t in "http://prometheus:9090/-/healthy" "http://$(vl_ip):9428/health" "http://$(am_ip):9093/-/healthy"; do
         compose exec -T grafana wget -q -T 5 -O /dev/null "$t" >/dev/null 2>&1 && echo -n "reachable " || echo -n "unreachable "
       done | sed 's/ $//')"
  # The log door, from a one-off container on `dashboards` (the backend image's Python; the
  # password on stdin, never a command line): grafana-ro reads and does nothing else ("missing
  # route", 400), and without a login nothing at all (401)
  net="$(stack_net logs-reader dashboards)"
  check "grafana: its log login reads only; no login, no access" "200 400 400 401 401" \
    "$( [[ -n "$net" ]] && docker run --rm -i --network "$net" --log-driver none --entrypoint python3 "$(env_get IHR_BACKEND_IMAGE)" -I -c 'import base64, sys, urllib.error, urllib.request
pw = sys.stdin.readline().strip()
auth = {"Authorization": "Basic " + base64.b64encode(("grafana-ro:" + pw).encode()).decode()}
out = []
for path, h in (("select/logsql/query?query=*&limit=1", auth), ("insert/jsonline", auth), ("internal/force_flush", auth),
                ("select/logsql/query?query=*&limit=1", {}), ("insert/jsonline", {})):
    req = urllib.request.Request("http://logs-reader:8427/" + path, data=b"{}", headers=h, method="POST")
    try:
        out.append(str(urllib.request.urlopen(req, timeout=15).status))
    except urllib.error.HTTPError as e:
        out.append(str(e.code))
    except OSError:
        out.append("unreachable")
print(" ".join(out))' < "$SECRETS/logs-reader-password" 2>/dev/null || echo error)"
  check "grafana: the log store's admin key is not in the log door" absent \
    "$(docker inspect -f '{{range .Mounts}}{{.Source}} {{end}}' "$(compose ps -q logs-reader)" | grep -q 'logs-admin-key' && echo present || echo absent)"
}

vl_get() {  # vl_get <path>: a GET to the log store with its password, from a one-off container
  # on its network (the backend image's Python); the password goes in on stdin, never a command line
  local net
  net="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$(compose ps -q victorialogs)" | tr ' ' '\n' | grep '_logs$' | head -1)"
  [[ -n "$net" ]] || return 1
  docker run --rm -i --network "$net" --log-driver none --entrypoint python3 "$(env_get IHR_BACKEND_IMAGE)" -I -c 'import base64, sys, urllib.error, urllib.request
pw = sys.stdin.readline().strip()
auth = base64.b64encode(("ihr-logs:" + pw).encode()).decode()
req = urllib.request.Request(sys.argv[1], headers={"Authorization": "Basic " + auth})
try:
    sys.stdout.write(urllib.request.urlopen(req, timeout=15).read().decode())
except urllib.error.HTTPError as e:
    sys.exit("HTTP %d" % e.code)' "http://$(vl_ip):9428$1" < "$SECRETS/logs-http-password"
}

logs_smoke() {  # smoke's log part (P1-T9b): a test line through the journal, redacted in the store
  local vl id line out i
  vl="http://$(vl_ip):9428"
  # A journal entry like any container's, with values the redaction must mask (synthetic: a
  # documentation address and numbers that belong to nobody)
  id="ihrcanary$(rand 9 | tr -dc 'a-z0-9')"
  line="$id mail canary.$id@example.in mobile 98765 43210 aadhaar 2345 6789 0123 token=secret$id search /api/v1/patient/?name=$id-secret"
  docker run --rm --network none --log-driver journald --log-opt "tag=indiehr/log-canary" \
    --log-opt labels=com.docker.compose.service --label com.docker.compose.service=log-canary \
    --entrypoint /bin/echo "$(sed -n 's/^ *image: \(prom\/alertmanager:[^ ]*\)$/\1/p' "$DIR/compose.logs.yaml" | head -1)" "$line" >/dev/null
  for i in $(seq 1 12); do
    out="$(vl_get "/select/logsql/query?query=$id&limit=5" 2>/dev/null || true)"
    [[ -n "$out" ]] && break
    sleep 5
  done
  check "logs: a test line reaches the log store through the journal" 1 "$(grep -c "$id" <<< "$out" || true)"
  check "logs: its e-mail, mobile, Aadhaar-like number, token and query string are masked" \
    "<email> <phone> <aadhaar> <redacted> ?<q>" \
    "$(python3 -c 'import json,sys
for l in sys.stdin:
    m = json.loads(l)["_msg"]
    print(" ".join(t for t in ("<email>", "<phone>", "<aadhaar>", "<redacted>", "?<q>") if t in m))' <<< "$out" 2>/dev/null)"
  check "logs: none of the test values is stored" 0 "$(grep -cE "canary\.|98765|2345 6789|secret$id|$id-secret" <<< "$out" || true)"
  check "logs: stored with its service and source" "log-canary container" \
    "$(python3 -c 'import json,sys
for l in sys.stdin:
    r = json.loads(l); print(r.get("service"), r.get("source"))' <<< "$out" 2>/dev/null)"
  # The server's own logs too (sshd, sudo, systemd): anything from the host in the last hour
  check "logs: the host's own services are stored" yes \
    "$(vl_get "/select/logsql/hits?query=source:host&start=1h&step=1h" 2>/dev/null | grep -q '"total":[1-9]' && echo yes || echo no)"
  # vmalert has both groups of log alerts, and they run
  check "logs: vmalert runs the log alerts without errors" "2 groups, no errors" \
    "$(prom_get "http://$(vmalert_ip):8880/api/v1/rules" 2>/dev/null | python3 -c 'import json,sys
g = json.load(sys.stdin)["data"]["groups"]
e = [r["name"] for x in g for r in x["rules"] if r.get("lastError")]
print("%d groups, %s" % (len(g), ("errors: " + " ".join(e)) if e else "no errors"))' 2>/dev/null || echo unreadable)"
  # Deleting logs needs the operator's key; the web app cannot reach the log store at all
  out="$(compose exec -T prometheus wget -q -T 10 -O /dev/null "$vl/select/logsql/query?query=$id" 2>&1 || true)"
  check "logs: reading the store without its password is refused" 401 \
    "$(grep -oE 'HTTP/[0-9.]+ [0-9]{3}' <<< "$out" | grep -oE '[0-9]{3}$' | head -1)"
  out="$(compose exec -T prometheus wget -q -T 10 -O /dev/null --post-data '' "$vl/delete/run_task?filter=$id" 2>&1 || true)"
  check "logs: deleting logs without the key is refused" 401 \
    "$(grep -oE 'HTTP/[0-9.]+ [0-9]{3}' <<< "$out" | grep -oE '[0-9]{3}$' | head -1)"
  if [[ -n "$(env_get IHR_LOGS_ARCHIVE)" ]]; then
    check "logs: the archiver's last pass succeeded" 1 \
      "$(prom_get 'http://127.0.0.1:9090/api/v1/query?query=ihr_logs_archive_last_run_ok' 2>/dev/null | python3 -c 'import json,sys
r = json.load(sys.stdin)["data"]["result"]
print(r[0]["value"][1] if r else "absent")' 2>/dev/null || echo unreadable)"
    # Every manifest and record checked; the last two days' files fetched, the others' sizes
    # (the archiver checks every file in full once a week)
    check "logs: every archived day authentic, chained and intact" "0 problem(s)" \
      "$(archive_exec check --recent 2 2>/dev/null | tail -1 | grep -oE '[0-9]+ problem\(s\)' || echo unreadable)"
    out="$(prom_get 'http://127.0.0.1:9090/api/v1/query?query=ihr_logs_archive_bucket_locked' 2>/dev/null | python3 -c 'import json,sys
r = json.load(sys.stdin)["data"]["result"]
print(r[0]["value"][1] if r else "absent")' 2>/dev/null || echo unreadable)"
    if [[ "$out" == -1 ]]; then
      echo "WARN  logs: the archive key cannot read the bucket's lock settings: check them in the provider's console (logs-archive-probe)"
    else
      check "logs: the archive bucket is locked for 400 days, with versioning" 1 "$out"
    fi
  fi
  check "logs: the web app cannot reach the log store" unreachable \
    "$(compose exec -T backend python -c 'import sys, urllib.request
try:
    urllib.request.urlopen(sys.argv[1] + "/health", timeout=5); print("reachable")
except Exception:
    print("unreachable")' "$vl" 2>/dev/null || echo unreachable)"
}

alerts() {  # alerts: what Alertmanager holds now (firing, silenced, inhibited)
  [[ -n "$(env_get IHR_MONITORING)" ]] || die "no monitoring here (monitoring-init)"
  am_tool alert query --active --silenced --inhibited -o extended
  if [[ "$(env_get IHR_MONITORING)" == test ]]; then
    echo "--- dead-man switch stand-in: $(stub_count)"
  fi
}

alert_test() {  # alert-test: one synthetic alert, mailed now; it resolves itself after 5 minutes
  local end tag
  [[ -n "$(env_get IHR_MONITORING)" ]] || die "no monitoring here (monitoring-init)"
  end="$(date -u -d '+5 min' +%Y-%m-%dT%H:%M:%SZ)"
  tag="test-$(date +%Y%m%d%H%M%S)"
  # severity info: routed at once at any hour (warnings wait for the day)
  am_tool alert add IhrAlertTest \
    severity=info "run=$tag" --annotation=summary="Test alert from scripts/staging.sh alert-test. No action needed." \
    --end="$end"
  echo "Sent IhrAlertTest ($tag) to Alertmanager; the mail goes out within about 30 s to $(env_get IHR_ALERT_TO)."
  if [[ "$(env_get IHR_MONITORING)" == test ]]; then
    local i n=0
    for i in $(seq 1 12); do
      n="$(compose exec -T smtp-test wget -q -O - "http://127.0.0.1:8025/api/v1/search?query=subject:IhrAlertTest" \
        | python3 -c 'import json,sys;print(json.load(sys.stdin)["messages_count"])' 2>/dev/null || echo 0)"
      ((n > 0)) && break
      sleep 10
    done
    ((n > 0)) || die "the stand-in received no alert mail within 2 minutes (scripts/staging.sh logs alertmanager)"
    echo "Stand-in received $n alert mail(s) for IhrAlertTest."
  fi
}

ops_tunnel() {  # ops-tunnel [user@server] [--port N]: Grafana, Prometheus, Alertmanager and the logs on this machine
  local dest="" port=22 prom am vl vma gf
  while (($#)); do
    case "$1" in
      --port) port="${2:?--port N}"; shift ;;
      -*) die "usage: scripts/staging.sh ops-tunnel [ihr@<server>] [--port N]" ;;
      *) dest="$1" ;;
    esac
    shift
  done
  prom="$(env_get IHR_PROMETHEUS_IP)"; prom="${prom:-10.203.0.70}"
  am="$(am_ip)"; vl="$(vl_ip)"; vma="$(vmalert_ip)"; gf="$(grafana_ip)"
  [[ "$port" =~ ^[0-9]+$ ]] || die "--port must be a number"
  # Their fixed addresses on the server's monitoring and alerting networks (nothing is published); bootstrap.sh
  # allows local forwarding only. Bound to this machine's 127.0.0.1 alone.
  local cmd=(ssh -N -p "$port" -L "127.0.0.1:9090:$prom:9090" -L "127.0.0.1:9093:$am:9093"
             -L "127.0.0.1:9428:$vl:9428" -L "127.0.0.1:8880:$vma:8880" -L "127.0.0.1:3000:$gf:3000"
             "${dest:-ihr@<server>}")
  echo "Dashboards:   http://grafana.localhost:3000   (Grafana: name in secrets/grafana-admin.env, password in secrets/grafana-admin-password, on the server)"
  echo "Prometheus:   http://127.0.0.1:9090   (alerts: /alerts, targets: /targets)"
  echo "Alertmanager: http://127.0.0.1:9093"
  echo "Logs:         http://127.0.0.1:9428/select/vmui/   (user ihr-logs, password: secrets/logs-http-password on the server)"
  echo "Log alerts:   http://127.0.0.1:8880"
  if [[ -z "$dest" ]]; then
    echo "Run on your own machine (Ctrl-C ends it):"
    printf '  %s\n' "${cmd[*]}"
  else
    [[ "$dest" =~ ^[a-z_][a-z0-9_.-]*@[A-Za-z0-9.:-]+$ ]] || die "not user@server: $dest"
    echo "Tunnel open until Ctrl-C."
    exec "${cmd[@]}"
  fi
}

# ---- The daily locked log archive (P1-T9c, ADR-014; compose.logs-archive.yaml) ----------------
logs_archive_init() {  # <https://endpoint> <bucket> <region> [--uri-style path|host] [--existing-key] | --local
  local envf="$OUT/.env" sec="$OUT/secrets" endpoint="" bucket="" region="" style="host" local_arc=0
  local existing=0 key="" secret="" enc args=()
  [[ -f "$envf" && -d "$sec" ]] || die "no $envf / secrets: run scripts/staging.sh init <suffix> first"
  grep -q '^IHR_LOGS_ARCHIVE=' "$envf" && die "$envf already has log archive settings (IHR_LOGS_ARCHIVE=...)"
  grep -q '^IHR_MONITORING=' "$envf" || die "the log archive needs the logs: run monitoring-init first"
  while (($#)); do
    case "$1" in
      --local) local_arc=1 ;;
      # A rebuilt server writing into an existing archive: its encryption key (from the
      # password manager), typed or as the last line on stdin, instead of a new one
      --existing-key) existing=1 ;;
      --uri-style) style="${2:?--uri-style path|host}"; shift ;;
      -*) die "unknown option: $1" ;;
      *) args+=("$1") ;;
    esac
    shift
  done
  if ((local_arc)); then
    # Proof only: this stack's own SeaweedFS (same disk, so not a real archive)
    grep -q '^IHR_S3=local' "$envf" || die "logs-archive-init --local needs SeaweedFS on this stack (s3-init)"
    SECRETS="$sec" s3_archive_key
    endpoint="http://s3:8333" region="ap-south-1" style="path"
    bucket="$(sed -n 's/^IHR_S3_LOGS_ARCHIVE_BUCKET=//p' "$envf" | tail -1)"; bucket="${bucket:-ihr-log-archive}"
    key="$(sed -n 's/^IHR_S3_LOGS_ARCHIVE_KEY=//p' "$sec/s3-gateway.env")"
    secret="$(sed -n 's/^IHR_S3_LOGS_ARCHIVE_SECRET=//p' "$sec/s3-gateway.env")"
  else
    ((${#args[@]} == 3)) || die "usage: scripts/staging.sh logs-archive-init <https://endpoint> <bucket> <region> [--uri-style path|host]"
    endpoint="${args[0]}" bucket="${args[1]}" region="${args[2]}"
    [[ "$endpoint" =~ ^https://[a-z0-9.-]+(:[0-9]+)?$ ]] || die "the endpoint must be https://host (e.g. https://in-south1-objectstore.e2enetworks.net)"
    # The bucket's writer key, from the keyboard or stdin (two lines), never arguments
    if [[ -t 0 ]]; then
      read -rp "Log archive bucket access key (its Writer key): " key; read -rsp "Secret key: " secret; echo
    else
      IFS= read -r key || true; IFS= read -r secret || true
    fi
  fi
  if ((existing)); then
    if [[ -t 0 ]]; then read -rsp "The archive's encryption key (IHR_LOGS_ARCHIVE_ENCRYPTION_KEY): " enc; echo
    else IFS= read -r enc || true; fi
  else
    enc="$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  fi
  [[ "$enc" =~ ^[A-Za-z0-9_-]{43}=$ ]] || die "that is not an archive key (44 characters, ending in =)"
  [[ "$bucket" =~ $BUCKET_RE ]] || die "not a bucket name: $bucket"
  [[ "$region" =~ ^[A-Za-z0-9-]+$ ]] || die "not a region: $region"
  [[ "$style" =~ ^(path|host)$ ]] || die "--uri-style is path or host"
  for v in "$key" "$secret"; do
    [[ ${#v} -ge 8 ]] || die "the bucket key and secret are required"
    case "$v" in *[[:space:]\"\'\\\$\`]*) die "the key has characters an env file cannot hold" ;; esac
  done
  local SECRETS_TARGET="$sec"
  {
    echo "# The daily locked log archive (P1-T9c, scripts/staging.sh logs-archive-init): the"
    echo "# archiver only. IHR_LOGS_ARCHIVE_ENCRYPTION_KEY encrypts every day: without it nothing in"
    echo "# the archive can be read. Keep a copy in the owner's password manager."
    echo "IHR_LOGS_ARCHIVE_ENDPOINT=$endpoint"
    echo "IHR_LOGS_ARCHIVE_REGION=$region"
    echo "IHR_LOGS_ARCHIVE_BUCKET=$bucket"
    echo "IHR_LOGS_ARCHIVE_URI_STYLE=$style"
    echo "IHR_LOGS_ARCHIVE_KEY=$key"
    echo "IHR_LOGS_ARCHIVE_SECRET=$secret"
    echo "IHR_LOGS_ARCHIVE_ENCRYPTION_KEY=$enc"
  } | write_secret logs-archive.env
  {
    echo "# The daily locked log archive (P1-T9c; scripts/staging.sh logs-archive-init): 400 days"
    echo "IHR_LOGS_ARCHIVE=$( ((local_arc)) && echo local || echo s3)"
  } >> "$envf"
  echo "Log archive settings added ($endpoint, bucket $bucket). Copy IHR_LOGS_ARCHIVE_ENCRYPTION_KEY"
  echo "from $sec/logs-archive.env into your password manager now: without it the archive cannot be read."
}

stack_net() {  # stack_net <service> <network>: the full name of one of the stack's networks, as
  # the service sees it (nothing when the service is not running)
  local id; id="$(compose ps -q "$1" 2>/dev/null)"
  [[ -n "$id" ]] || return 0
  # shellcheck disable=SC2016  # a Go template for docker, not a shell expansion
  docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$id" | tr ' ' '\n' | grep "_$2\$" | head -1
}

archive_exec() {  # archive_exec <archive.py args>: in the running archiver (its key, its network);
  # without one (a lost server, or the owner's machine) `check`, `get` and `probe` run in a
  # one-off container from secrets/logs-archive.env
  local arc net=()
  arc="$(env_get IHR_LOGS_ARCHIVE)"
  [[ -n "$arc" ]] || die "no log archive here (logs-archive-init)"
  if [[ -n "$(compose ps -q logs-archive 2>/dev/null)" ]]; then
    compose exec -T logs-archive python3 -I /opt/ihr-archive/archive.py "$@"
    return
  fi
  case "$1" in check|get|probe) ;; *) die "the archiver is not running here (scripts/staging.sh up)" ;; esac
  if [[ "$arc" == local ]]; then
    net=(--network "$(stack_net s3 internal)")
    [[ "${net[1]}" ]] || die "the proof's object storage is not running (scripts/staging.sh up)"
  fi
  docker run --rm -i "${net[@]}" --log-driver none --read-only --tmpfs /tmp -e HOME=/tmp -e TMPDIR=/tmp \
    --env-file "$SECRETS/logs-archive.env" -v "$ROOT/deploy/logs/archive:/opt/ihr-archive:ro" \
    --entrypoint python3 "$(env_get IHR_BACKEND_IMAGE)" -I /opt/ihr-archive/archive.py "$@"
}

logs_archive_expire() {  # [--dry-run] [--older-than-days N]: erasure, with the bucket's admin key
  local arc net=() admin=() mac extra=()
  arc="$(env_get IHR_LOGS_ARCHIVE)"
  [[ -n "$arc" ]] || die "no log archive here (logs-archive-init)"
  local f="$SECRETS/logs-archive.env" envs=() k v
  # The bucket's address only: never the writer's key or the encryption key
  while IFS='=' read -r k v; do
    case "$k" in IHR_LOGS_ARCHIVE_ENDPOINT|IHR_LOGS_ARCHIVE_REGION|IHR_LOGS_ARCHIVE_BUCKET|IHR_LOGS_ARCHIVE_URI_STYLE)
      envs+=(-e "$k=$v") ;; esac
  done < "$f"
  # The erasure records' key, derived from the archive key here: expire.py never sees the
  # archive key itself (it could not read the archive with what it gets)
  mac="$(sed -n 's/^IHR_LOGS_ARCHIVE_ENCRYPTION_KEY=//p' "$f" | python3 -c 'import base64,hashlib,hmac,sys
k = base64.urlsafe_b64decode(sys.stdin.read().strip())
print(hmac.new(k, b"ihr-log-archive erasure", hashlib.sha256).hexdigest())')"
  if [[ "$arc" == local ]]; then
    # A proof: the stand-in's admin key, and its network; shorter periods allowed
    extra=(--allow-short)
    net=(--network "$(stack_net s3 internal)")
    admin=("$(sed -n 's/^IHR_S3_ADMIN_KEY=//p' "$SECRETS/s3-gateway.env")" "$(sed -n 's/^IHR_S3_ADMIN_SECRET=//p' "$SECRETS/s3-gateway.env")")
  elif [[ -t 0 ]]; then
    read -rp "The archive bucket's admin access key: " k; read -rsp "Secret key: " v; echo
    admin=("$k" "$v")
  else
    IFS= read -r k || true; IFS= read -r v || true
    admin=("$k" "$v")
  fi
  printf '%s\n%s\n%s\n' "${admin[@]}" "$mac" | docker run --rm -i "${net[@]}" --log-driver none --read-only \
    --tmpfs /tmp -e HOME=/tmp "${envs[@]}" -v "$ROOT/deploy/logs/archive:/opt/ihr-archive:ro" \
    --entrypoint python3 "$(env_get IHR_BACKEND_IMAGE)" -I /opt/ihr-archive/expire.py "$@" "${extra[@]}"
}

logs_volume() {  # [hours]: lines and MB a day in the log store (rate over the last hours, default
  # 24); sizes for 180 days, the archive and the journal
  [[ -n "$(env_get IHR_MONITORING)" ]] || die "no logs here (monitoring-init)"
  local days w="${1:-24}" q="http://127.0.0.1:9090/api/v1/query?query="
  [[ "$w" =~ ^[0-9]+$ && "$w" -ge 1 ]] || die "usage: scripts/staging.sh logs-volume [hours]"
  days="$(vl_get "/select/logsql/hits?query=*&start=15d&step=1d&offset=5h30m" 2>/dev/null || true)"
  python3 - "$days" \
    "$(prom_get "${q}sum(rate(vl_bytes_ingested_total%5B${w}h%5D))*86400" 2>/dev/null || true)" \
    "$(prom_get "${q}sum(rate(vl_rows_ingested_total%5B${w}h%5D))*86400" 2>/dev/null || true)" \
    "$(prom_get "${q}sum(vl_uncompressed_data_size_bytes)/sum(vl_compressed_data_size_bytes)" 2>/dev/null || true)" \
    "$(prom_get "${q}sum(ihr_journal_disk_usage_bytes)/clamp_min((time()-min(ihr_journal_oldest_entry_timestamp_seconds))/86400,1)" 2>/dev/null || true)" "$w" <<'PY'
import json, sys

def val(raw):
    try:
        r = json.loads(raw)["data"]["result"]
        return float(r[0]["value"][1]) if r else None
    except (ValueError, KeyError, IndexError):
        return None

try:
    hits = json.loads(sys.argv[1]).get("hits") or []
except ValueError:
    hits = []
import datetime as dt
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
per_day = {}
for h in hits:
    for t, n in zip(h.get("timestamps", []), h.get("values", [])):
        # a bucket starts at India midnight (18:30 UTC the day before)
        day = dt.datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(IST).date().isoformat()
        per_day[day] = per_day.get(day, 0) + int(n)
raw_day, rows_day, ratio, journal_day = (val(a) for a in sys.argv[2:6])
print("Lines per India day in the store (last 15 days):")
for d in sorted(per_day):
    print(f"  {d}  {per_day[d]:>10,}")
if raw_day is None or not ratio:
    sys.exit("not enough data yet (Prometheus needs a few hours of the store's metrics)")
disk_day = raw_day / ratio
mb = 1e6
print(f"Rate over the last {sys.argv[6]} hours: {rows_day:,.0f} lines/day, {raw_day / mb:,.1f} MB/day as sent, "
      f"{disk_day / mb:,.1f} MB/day on disk (compression {ratio:.1f}x)")
print(f"180 days in the store: {180 * disk_day / 1e9:,.2f} GB -> IHR_LOGS_MAX_SIZE at least "
      f"{max(1, round(180 * disk_day * 1.3 / 2**30 + 0.5))}GiB (with 30 % room)")
print(f"400 days in the archive (encrypted gzip, about 1.2x the store): {400 * disk_day * 1.2 / 1e9:,.2f} GB")
if journal_day is not None and journal_day > 0:
    print(f"Journal: {journal_day / mb:,.1f} MB per day it holds -> 14 days need about "
          f"{16 * journal_day * 1.5 / 2**30:,.2f} GiB with room (SystemMaxUse; deploy/host/bootstrap.sh)")
else:
    print("Journal: not measurable here (the host timer reports no journal size or oldest entry)")
PY
}

case "${1:-}" in
  init) init "${@:2}" ;;
  up) up "${@:2}" ;;
  smoke) smoke ;;
  manage) compose --profile admin run --rm --no-deps -T admin python manage.py "${@:2}" ;;
  ps) compose ps ;;
  logs) compose logs --tail=200 "${@:2}" ;;
  keys) keys_cmd "${@:2}" ;;
  down)  # with -v the volumes go on purpose: the next up may start an empty database
    compose down "${@:2}"
    if [[ " ${*:2} " == *" -v "* || " ${*:2} " == *" --volumes "* ]]; then rm -f "$DIR/.db-created"; fi ;;
  acme-init) acme_init "${@:2}" ;;
  acme-register) acme_register "${@:2}" ;;
  acme-new-cert) acme_new_cert ;;
  cert) cert "${@:2}" ;;
  acme-retry) compose restart traefik && cert --wait 300 ;;
  acme-proof) acme_proof "${@:2}" ;;
  acme-proof-run) acme_proof_run ;;
  s3-init) s3_init ;;
  backup-init) backup_init "${@:2}" ;;
  backup-status) backup_status ;;
  backup-now) compose exec -T backup pgbackrest --stanza=main --type="${2:-diff}" backup ;;
  restore-drill) restore_drill "${@:2}" ;;
  restore) restore_live "${@:2}" ;;
  mail-init) mail_init "${@:2}" ;;
  mail-check) mail_check "${@:2}" ;;
  s3-check) s3_check ;;
  monitoring-init) monitoring_init "${@:2}" ;;
  monitoring-deadman) monitoring_deadman ;;
  monitoring-render) monitoring_config ;;
  monitoring-textfile) monitoring_textfile "${@:2}" ;;
  alerts) alerts ;;
  alert-test) alert_test ;;
  ops-tunnel) ops_tunnel "${@:2}" ;;
  grafana-reset-admin) grafana_reset_admin ;;
  logs-volume) logs_volume "${@:2}" ;;
  logs-archive-init) logs_archive_init "${@:2}" ;;
  logs-archive-now) archive_exec once ;;
  logs-archive-check) archive_exec check "${@:2}" ;;
  logs-archive-get) [[ -n "${2:-}" ]] || die "usage: scripts/staging.sh logs-archive-get <YYYY-MM-DD> [revision]"; archive_exec get "${@:2}" ;;
  logs-archive-expire) logs_archive_expire "${@:2}" ;;
  logs-archive-probe) archive_exec probe ;;
  local-proof)
    [[ -f "$DIR/.env" ]] || IHR_FRONTEND_IMAGE="${IHR_FRONTEND_IMAGE:-indiehr-frontend:local}" \
      init "$LOCAL_SUFFIX" --local
    # A proof keeps its files on SeaweedFS too (P1-T8b3)
    grep -q '^IHR_S3=' "$DIR/.env" || s3_init
    # ...and a stand-in for the mail service (P1-T8b4)
    grep -q '^IHR_MAIL=' "$DIR/.env" || mail_init --test
    # ...and backups into that SeaweedFS (P1-T8c; same disk: a proof, not a real backup)
    grep -q '^IHR_BACKUP=' "$DIR/.env" || backup_init --local
    # ...and metrics with alerts to the mail stand-in (P1-T9a)
    grep -q '^IHR_MONITORING=' "$DIR/.env" || monitoring_init --test
    # ...and the daily locked log archive into that SeaweedFS (P1-T9c)
    grep -q '^IHR_LOGS_ARCHIVE=' "$DIR/.env" || logs_archive_init --local
    up
    # A synthetic hospital for the address checks, then its Keycloak organisation and client
    # (smoke checks every hospital's client holds the web app's current key)
    compose --profile admin run --rm --no-deps -T admin python manage.py ihr_create_tenant \
      --slug alpha --name "Alpha Proof Hospital" --domain "alpha.$(env_get IHR_TENANT_DOMAIN_SUFFIX)" \
      >/dev/null 2>&1 || true
    admin_manage ihr_keycloak_sync --all >/dev/null
    smoke
    ;;
  *) sed -n '5,53p' "$0"; exit 1 ;;
esac
