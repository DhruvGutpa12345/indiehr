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
#   scripts/staging.sh fleet <args>              ihr_fleet_run (P1-T18a), logged for its alert
#   scripts/staging.sh release [--now [--minutes N] [--note T]] [--operator NAME] | --finish
#                                                the new images in the announced window (P1-T18b):
#                                                hold all, stop workers, drain, seal, restore point,
#                                                migrate, start, smoke every hospital, log it
#   scripts/staging.sh rollback [--restore] [--operator NAME]
#                                                back to the release before (--restore: to the
#                                                restore point of the one left; loses later writes)
#   scripts/staging.sh releases                  the logged releases in force, newest last
#   scripts/staging.sh ps | logs [service] | down
#   scripts/staging.sh db-split-secrets [--check] the split database roles' secrets (P1-T19b): a new
#                                                owner password (secrets/db-owner.env), an app one
#                                                (secrets/app.env) and the audit watch's (P1-T21,
#                                                secrets/audit-watch.env); on the deploying machine,
#                                                once (run again: makes only what is missing)
#   scripts/staging.sh watch-reader --check      whether the audit watch's fingerprint reader key is
#                                                set where monitoring is on (P1-T21; deploy.sh asks)
#   scripts/staging.sh db-roles --migrate [--operator NAME]
#                                                move a restored database of the single role ihr to
#                                                ihr_owner + ihr_app: stopped stack, build released
#                                                last (on a running server, release moves them)
#   scripts/staging.sh db-roles --serve-previous [--operator NAME]
#                                                a release stopped before the move: start the build
#                                                that ran again, lift the holds, clear the window
#   scripts/staging.sh start-existing <service>...
#                                                the existing (stopped) containers of app services
#                                                started again as they were (never recreated)
#   scripts/staging.sh db-roles --assert | --status
#                                                re-assert both roles' passwords and settings as
#                                                postgres (every up does) | where the roles stand
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
#   scripts/staging.sh s3-split-keys [--check]   CARE's one storage key split into a patient files
#                                                key and a pictures key (P1-T20b), on the deploying
#                                                machine, once (--check: only say whether it is)
#   scripts/staging.sh s3-check                  live checks of its keys, locks and public paths
#   scripts/staging.sh mail-init <from> [--host H] [--port P] [--user U] | --test
#                                                mail via Zoho ZeptoMail (P1-T8b4); token on stdin
#   scripts/staging.sh mail-check <to>           send a test e-mail through Django
#   scripts/staging.sh backup-init <https://endpoint> <bucket> <region> [--uri-style path|host] | --local
#                                                database backups (P1-T8c); bucket key on stdin
#   scripts/staging.sh backup-status | backup-now [full|diff]
#   scripts/staging.sh restore-drill [--target "YYYY-MM-DD HH:MM:SS+05:30"] [--keep | --clean]
#   scripts/staging.sh restore [--target ... | --target-name release-<tag> [--set <label>]]
#                                                a lost server: into this stack's empty volume
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
    # A proof's fingerprint bucket is this stack's own SeaweedFS: the audit watch reaches it
    # over its own way out (auditout), as it reaches the real bucket on a server (P1-T21 A1)
    [[ "$(env_get IHR_S3)" == local ]] && files+=(-f "$DIR/compose.audit-watch.local.yaml")
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

  local pg owner app watch kc redis_pw admin_pw admin_secret jwks
  local SECRETS_TARGET="$work/secrets"
  pg="$(rand 32)"; owner="$(rand 32)"; app="$(rand 32)"; kc="$(rand 32)"; redis_pw="$(rand 32)"
  watch="$(rand 32)"
  admin_pw="$(rand 24)"; admin_secret="$(rand 32)"
  # --log-driver none: the private key must not reach the host's journal
  jwks="$(docker run --rm --log-driver none --network none --entrypoint python "$backend_image" -c \
    'from care.utils.jwks.generate_jwk import generate_encoded_jwks; print(generate_encoded_jwks())')"
  # The database's two roles (P1-T19b): ihr_owner (schema changes: migrate, admin, the
  # provisioner) and ihr_app (everything else). Their passwords live only in these addresses;
  # staging.sh up gives them to Postgres (db_roles_apply), never through db.env
  write_secret db.env <<EOF
POSTGRES_PASSWORD=$pg
KC_DB_PASSWORD=$kc
EOF
  write_secret db-owner.env <<EOF
# The owner's address (P1-T19b): only migrate, admin and the provisioner read this file
DATABASE_URL=postgres://ihr_owner:$owner@db:5432/ihr
EOF
  audit_watch_env "$watch" | write_secret audit-watch.env
  write_secret redis.env <<EOF
REDIS_PASSWORD=$redis_pw
EOF
  write_secret app.env <<EOF
DJANGO_SECRET_KEY=$(rand 64)
JWKS_BASE64=$jwks
DATABASE_URL=postgres://ihr_app:$app@db:5432/ihr
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

# ---- Database roles (P1-T19b; docs/reviews/p1-t19/design.md, ihr_platform/db_roles.py) ---------
# Two roles serve the database ihr: ihr_owner owns it and everything in it (the migrate one-shot,
# admin one-offs, the provisioner: secrets/db-owner.env) and ihr_app serves every request and
# worker (secrets/app.env), with no right to change a schema, an audit trigger or a grant. The
# passwords live only in those two addresses, made on the deploying machine (db-split-secrets);
# every up hands them to Postgres as postgres (db_roles_apply). A database of the previous
# layout (one role, ihr, owning everything) is moved once, under a full stop: db-roles --migrate,
# or inside `release`.
DB_APP_URL_RE='^DATABASE_URL=postgres://ihr_app:([A-Za-z0-9_-]{32,})@db:5432/ihr$'
DB_OWNER_URL_RE='^DATABASE_URL=postgres://ihr_owner:([A-Za-z0-9_-]{32,})@db:5432/ihr$'
# The audit watch's own read-only role (P1-T21): its address alone, in its own file
DB_WATCH_URL_RE='^DATABASE_URL=postgres://ihr_audit_watch:([A-Za-z0-9_-]{32,})@db:5432/ihr$'
audit_watch_env() {  # audit_watch_env <password>: secrets/audit-watch.env's text (to write_secret)
  printf '# The audit watch'"'"'s address (P1-T21): ihr_audit_watch, read-only; the audit-watch container only\nDATABASE_URL=postgres://ihr_audit_watch:%s@db:5432/ihr\n' "$1"
}
DB_APP_SERVICES=(backend frontend celery-worker celery-beat provisioner staff-worker)
db_password() {  # db_password <app|owner|watch> [secrets dir]: that role's password from its address; nothing if absent
  local f re line
  case "$1" in
    app) f=app.env re="$DB_APP_URL_RE" ;;
    owner) f=db-owner.env re="$DB_OWNER_URL_RE" ;;
    watch) f=audit-watch.env re="$DB_WATCH_URL_RE" ;;
    *) die "db_password: app, owner or watch" ;;
  esac
  line="$(grep '^DATABASE_URL=' "${2:-$SECRETS}/$f" 2>/dev/null | tail -1 || true)"
  if [[ "$line" =~ $re ]]; then echo "${BASH_REMATCH[1]}"; fi
}
db_secrets_split() {  # db_secrets_split [secrets dir]: the three addresses are the split roles'
  [[ -n "$(db_password app "${1:-}")" && -n "$(db_password owner "${1:-}")" && -n "$(db_password watch "${1:-}")" ]]
}
db_need_split_secrets() {
  db_secrets_split || die "secrets/app.env, secrets/db-owner.env and secrets/audit-watch.env do not hold the split database roles' addresses (P1-T19b, P1-T21): run scripts/staging.sh db-split-secrets on the deploying machine and send them (scripts/deploy.sh); deploy/staging/README.md, Moving to split roles"
}
# The audit watch (P1-T21) starts with the monitoring and refuses to run without the fingerprint
# bucket's reader key: a stack with monitoring needs its fingerprint storage. Checked by up and
# release before anything changes, never found by `up`'s last step (review C4); and by deploy.sh
# on the deploying machine before it sends anything (watch-reader --check, IHR_STAGING_OUT)
watch_need_reader() {  # watch_need_reader [folder]: that folder's .env and secrets/ (default this stack's)
  local on sec="$SECRETS"
  if [[ -n "${1:-}" ]]; then
    on="$(sed -n 's/^IHR_MONITORING=//p' "$1/.env" 2>/dev/null | tail -1)"; sec="$1/secrets"
  else
    on="$(env_get IHR_MONITORING)"
  fi
  [[ -n "$on" ]] || return 0
  grep -q '^IHR_AUDIT_ANCHOR_READ_KEY=.' "$sec/anchor-read.env" 2>/dev/null \
    || die "monitoring is on, but secrets/anchor-read.env holds no fingerprint reader key: the audit watch would refuse to start and up fail at its last step. Set up the fingerprint storage first (scripts/staging.sh s3-init, or fill secrets/anchor-read.env for a provider bucket; deploy/staging/README.md, Audit watch). Nothing was changed"
}
scram() {  # scram < password: its SCRAM-SHA-256 verifier, made here: the password itself never
  # reaches Postgres, its logs or pg_stat_activity (password_encryption is scram-sha-256)
  # shellcheck disable=SC2016 # Python: its f-string, not the shell's
  python3 -c '
import base64, hashlib, hmac, os, sys
pw = sys.stdin.readline().rstrip("\n").encode()
salt, rounds = os.urandom(16), 4096
salted = hashlib.pbkdf2_hmac("sha256", pw, salt, rounds)
client = hmac.new(salted, b"Client Key", "sha256").digest()
server = hmac.new(salted, b"Server Key", "sha256").digest()
b64 = lambda b: base64.b64encode(b).decode()
print(f"SCRAM-SHA-256${rounds}:{b64(salt)}${b64(hashlib.sha256(client).digest())}:{b64(server)}")'
}
pg_su() {  # pg_su <psql args>: psql as postgres in this stack's database container, or in the
  # container PG_TARGET names (the restore drill's copy); SQL on stdin, never a command line
  if [[ -n "${PG_TARGET:-}" ]]; then
    docker exec -i "$PG_TARGET" psql -U postgres -v ON_ERROR_STOP=1 "$@"
  else
    compose exec -T db psql -U postgres -v ON_ERROR_STOP=1 "$@"
  fi
}
DB_STATE_SQL="SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ihr_owner') THEN 'split' WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ihr') THEN 'single' ELSE 'none' END"
db_roles_state() {  # split (ihr_owner exists), single (the previous layout: ihr), none; or fail
  local out
  out="$(pg_su -d postgres -Atc "$DB_STATE_SQL")" || return 1
  [[ "$out" =~ ^(split|single|none)$ ]] || return 1
  echo "$out"
}
# The move itself, as postgres: ihr becomes ihr_owner and keeps owning the database and every
# object in it (an object's owner is the role's id; SCRAM verifiers survive a rename, and both
# roles get new passwords below anyway). Anything still logged in as ihr is ended first.
# shellcheck disable=SC2016 # SQL: $$ is PL/pgSQL's quoting, never the shell's
DB_ROLES_MOVE_SQL='DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '"'ihr_owner'"') THEN
    RAISE EXCEPTION '"'role ihr_owner exists already: the roles were moved'"';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_database d JOIN pg_roles r ON r.oid = d.datdba
                 WHERE d.datname = '"'ihr'"' AND r.rolname = '"'ihr'"') THEN
    RAISE EXCEPTION '"'the database ihr is not owned by the role ihr: not the previous layout'"';
  END IF;
END $$;
SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename = '"'ihr'"';
ALTER ROLE ihr RENAME TO ihr_owner;'
# Every role of the model as ihr_platform/db_roles.py has it (ROLES), re-asserted at every up:
# made if missing; attributes (no SUPERUSER, CREATEDB, CREATEROLE, REPLICATION, BYPASSRLS; the app
# NOINHERIT); the passwords (verifiers in :vo and :va); no stored settings anywhere, in any
# database (a role may ALTER ROLE itself, also IN DATABASE another database: design.md, known
# limit 1); no membership but the dump role's
# pg_read_all_data, and nobody a member of ours; large objects closed to PUBLIC (known limit 3).
# The audit watch logs in (P1-T21; its verifier in :vw, at most 4 connections); the dump role has
# no login until its service exists (P1-T22). Grants are not here: ensure_grants (the migrate
# one-shot, as the owner) converges them.
# shellcheck disable=SC2016
DB_ROLES_SQL='DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '"'ihr_owner'"') THEN
    RAISE EXCEPTION '"'role ihr_owner does not exist: the database still has its single role (scripts/staging.sh db-roles --migrate)'"';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_database d JOIN pg_roles r ON r.oid = d.datdba
                 WHERE d.datname = '"'ihr'"' AND r.rolname = '"'ihr_owner'"') THEN
    RAISE EXCEPTION '"'the database ihr is not owned by ihr_owner'"';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '"'ihr_app'"') THEN CREATE ROLE ihr_app; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '"'ihr_audit_watch'"') THEN CREATE ROLE ihr_audit_watch; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '"'ihr_dump'"') THEN CREATE ROLE ihr_dump; END IF;
END $$;
ALTER ROLE ihr_owner WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'"'vo'"';
ALTER ROLE ihr_app WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'"'va'"';
ALTER ROLE ihr_audit_watch WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 4 PASSWORD :'"'vw'"';
ALTER ROLE ihr_dump WITH NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD NULL;
DO $$
DECLARE s record;
BEGIN
  -- Every stored setting of our roles, in every database: a role may ALTER ROLE itself IN
  -- DATABASE any database (postgres, template1, keycloak), even one it cannot connect to, and
  -- the run-time check refuses a row in any of them (review S1/C3)
  FOR s IN
    SELECT r.rolname, d.datname
    FROM pg_db_role_setting x
    JOIN pg_roles r ON r.oid = x.setrole
    LEFT JOIN pg_database d ON d.oid = x.setdatabase
    WHERE r.rolname IN ('"'ihr_owner', 'ihr_app', 'ihr_audit_watch', 'ihr_dump'"')
  LOOP
    IF s.datname IS NULL THEN
      EXECUTE format('"'ALTER ROLE %I RESET ALL'"', s.rolname);
    ELSE
      EXECUTE format('"'ALTER ROLE %I IN DATABASE %I RESET ALL'"', s.rolname, s.datname);
    END IF;
  END LOOP;
  IF EXISTS (SELECT 1 FROM pg_db_role_setting x JOIN pg_roles r ON r.oid = x.setrole
             WHERE r.rolname IN ('"'ihr_owner', 'ihr_app', 'ihr_audit_watch', 'ihr_dump'"')) THEN
    RAISE EXCEPTION '"'stored settings of the database roles are left after the reset'"';
  END IF;
END $$;
DO $$
DECLARE m record;
BEGIN
  FOR m IN
    SELECT g.rolname AS grp, r.rolname AS member, w.rolname AS grantor
    FROM pg_auth_members a
    JOIN pg_roles g ON g.oid = a.roleid
    JOIN pg_roles r ON r.oid = a.member
    JOIN pg_roles w ON w.oid = a.grantor
    WHERE (r.rolname IN ('"'ihr_owner', 'ihr_app', 'ihr_audit_watch', 'ihr_dump'"')
           AND NOT (r.rolname = '"'ihr_dump'"' AND g.rolname = '"'pg_read_all_data'"'))
       OR g.rolname IN ('"'ihr_owner', 'ihr_app', 'ihr_audit_watch', 'ihr_dump'"')
  LOOP
    EXECUTE format('"'REVOKE %I FROM %I GRANTED BY %I CASCADE'"', m.grp, m.member, m.grantor);
  END LOOP;
  IF NOT pg_has_role('"'ihr_dump', 'pg_read_all_data', 'MEMBER'"') THEN
    GRANT pg_read_all_data TO ihr_dump;
  END IF;
END $$;
REVOKE EXECUTE ON FUNCTION lo_create(oid), lo_creat(integer), lo_from_bytea(oid, bytea),
  lo_import(text), lo_import(text, oid) FROM PUBLIC;'
db_roles_apply() {  # db_roles_apply [--move]: (the move, then) the roles re-asserted, as postgres,
  # in one transaction; the passwords from the secrets as SCRAM verifiers, on stdin. In the
  # database ihr: roles are the cluster's, but a function's privileges are each database's own
  # (the large-object revoke must be ihr's)
  local owner app watch vo va vw
  owner="$(db_password owner)"; app="$(db_password app)"; watch="$(db_password watch)"
  [[ -n "$owner" && -n "$app" && -n "$watch" ]] || db_need_split_secrets
  vo="$(scram <<< "$owner")"; va="$(scram <<< "$app")"; vw="$(scram <<< "$watch")"
  [[ "$vo" =~ ^SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+$ && "$va" =~ ^SCRAM-SHA-256\$ && "$vw" =~ ^SCRAM-SHA-256\$ ]] \
    || die "could not make the password verifiers"
  { printf '\\set vo %s\n\\set va %s\n\\set vw %s\n' "'$vo'" "'$va'" "'$vw'"
    [[ "${1:-}" != --move ]] || printf '%s\n' "$DB_ROLES_MOVE_SQL"
    printf '%s\n' "$DB_ROLES_SQL"
  } | pg_su -1 -q -d ihr >/dev/null
}
db_split_secrets() {  # db-split-secrets: the split roles' secrets (P1-T19b), on the deploying
  # machine as s3-init (IHR_STAGING_OUT: elsewhere): a NEW owner password, never the old shared
  # one (secrets/db-owner.env: the owner's address, which only migrate, admin and the provisioner
  # read), an app password (secrets/app.env's DATABASE_URL), and the old IHR_DB_PASSWORD out of
  # secrets/db.env. Idempotent: a secret already made is kept, never replaced; after an
  # interruption run it again. Nothing is printed but file names.
  local sec="$OUT/secrets" owner app line tmp changed=()
  # --check: only whether the three addresses are the split roles' (what up and release need,
  # db_need_split_secrets; deploy.sh asks before it sends anything, review C13). File names only
  case "${1:-}" in
    "") ;;
    --check)
      if db_secrets_split "$sec"; then echo "The database roles' secrets are split (app.env, db-owner.env, audit-watch.env)."; return 0; fi
      echo "$sec: the database roles' secrets are not all split (app.env, db-owner.env, audit-watch.env)" >&2
      return 1 ;;
    *) die "usage: scripts/staging.sh db-split-secrets [--check]" ;;
  esac
  [[ -f "$OUT/.env" && -d "$sec" ]] || die "no $OUT/.env / secrets: run scripts/staging.sh init <suffix> first"
  local SECRETS_TARGET="$sec"
  owner="$(db_password owner "$sec")"; app="$(db_password app "$sec")"
  if [[ -z "$app" ]]; then
    line="$(grep '^DATABASE_URL=' "$sec/app.env" 2>/dev/null | tail -1 || true)"
    [[ "$line" =~ ^DATABASE_URL=postgres://ihr:[A-Za-z0-9_-]+@db:5432/ihr$ ]] \
      || die "secrets/app.env's DATABASE_URL is neither the single role's address nor the app role's: nothing changed (look at it)"
  fi
  if [[ -z "$owner" && -e "$sec/db-owner.env" ]]; then
    die "secrets/db-owner.env exists but holds no owner address: nothing changed (look at it; a secret is never replaced silently)"
  fi
  if [[ -z "$owner" ]]; then
    owner="$(rand 32)"
    printf '# The owner'"'"'s address (P1-T19b): only migrate, admin and the provisioner read this file\nDATABASE_URL=postgres://ihr_owner:%s@db:5432/ihr\n' "$owner" \
      | write_secret db-owner.env.new
    mv "$sec/db-owner.env.new" "$sec/db-owner.env"
    changed+=(secrets/db-owner.env)
  fi
  if [[ -z "$app" ]]; then
    app="$(rand 32)"
    tmp="$(grep -v '^DATABASE_URL=' "$sec/app.env"
      echo "DATABASE_URL=postgres://ihr_app:$app@db:5432/ihr")"
    printf '%s\n' "$tmp" | write_secret app.env.new
    mv "$sec/app.env.new" "$sec/app.env"
    changed+=(secrets/app.env)
  fi
  # The audit watch's read-only role (P1-T21): its own file, made once like the others
  if [[ -z "$(db_password watch "$sec")" ]]; then
    [[ ! -e "$sec/audit-watch.env" ]] || die "secrets/audit-watch.env exists but holds no audit watch address: nothing more changed (look at it; a secret is never replaced silently)"
    audit_watch_env "$(rand 32)" | write_secret audit-watch.env.new
    mv "$sec/audit-watch.env.new" "$sec/audit-watch.env"
    changed+=(secrets/audit-watch.env)
  fi
  if grep -q '^IHR_DB_PASSWORD=' "$sec/db.env" 2>/dev/null; then
    tmp="$(grep -v '^IHR_DB_PASSWORD=' "$sec/db.env")"
    printf '%s\n' "$tmp" | write_secret db.env.new
    mv "$sec/db.env.new" "$sec/db.env"
    changed+=("secrets/db.env (the old shared password removed)")
  fi
  if ((${#changed[@]} == 0)); then
    echo "The database roles' secrets are split already: nothing changed."
    return 0
  fi
  echo "Wrote ${changed[*]} in $OUT (mode 600)."
  echo "Put the owner's password into your password manager: it is the password in secrets/db-owner.env's DATABASE_URL (not printed here)."
  echo "Next: scripts/deploy.sh --host ... --release (the release moves the roles under a full stop)."
}
outgoing_database_url() {  # the running backend's own database address (its container's settings)
  local cid
  cid="$(compose ps -q backend 2>/dev/null || true)"
  [[ -n "$cid" ]] || return 1
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$cid" | sed -n 's/^DATABASE_URL=//p' | tail -1
}
backend_runs_as_app() {  # the running backend runs as ihr_app (a build after the move)
  local cid
  cid="$(compose ps -q backend 2>/dev/null || true)"
  [[ -n "$cid" ]] && docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$cid" | grep -qx 'IHR_DB_ROLE=app'
}
db_roles_move_stopped() {  # the move with every app container stopped (release, db-roles --migrate):
  # the database container recreated if its settings changed (db.env lost IHR_DB_PASSWORD), then
  # the rename and the re-assertion in one transaction. Prints how long it took.
  local t
  echo "== Stopping every app container: ${DB_APP_SERVICES[*]}"
  compose stop "${DB_APP_SERVICES[@]}"
  compose up -d --wait db redis
  t=$SECONDS
  echo "== Moving the database roles: ihr -> ihr_owner (new password), ihr_app, ihr_audit_watch, ihr_dump; settings reset; large objects closed"
  db_roles_apply --move
  echo "moved in $((SECONDS - t)) s"
}
# The EXIT trap of db-roles --migrate (globals). It runs only on a stopped stack (review C2):
# nothing is held by it, so before the move nothing is to undo; the move is one transaction
DB_MOVE_STAGE="" DB_MOVE_OP=""
db_move_exit() {
  local rc="$1"
  ((rc != 0)) || return 0
  case "$DB_MOVE_STAGE" in
    moved) echo "DB-ROLES --migrate STOPPED (exit $rc) AFTER THE MOVE: the roles are split; the app containers are stopped. Finish: scripts/staging.sh fleet migrate --operator $DB_MOVE_OP (the grants; it also lifts the holds of every hospital it finishes), then scripts/staging.sh up" >&2 ;;
    *) echo "DB-ROLES --migrate STOPPED (exit $rc) BEFORE THE MOVE: the roles are unchanged (the move is one transaction). Fix the cause (above) and run scripts/staging.sh db-roles --migrate again" >&2 ;;
  esac
}
rel_logged_backend() {  # rel_logged_backend <tag>: the backend image id of that tag's newest
  # logged release line (releases.log), or nothing
  [[ -f "$REL_LOG" ]] || return 0
  awk -v t="tag=$1" '{ k = 0; g = 0; b = ""
    for (i = 1; i <= NF; i++) { if ($i == "kind=release") k = 1; if ($i == t) g = 1; if ($i ~ /^backend=/) b = substr($i, 9) }
    if (k && g) last = b } END { if (last != "") print last }' "$REL_LOG"
}
existing_containers() {  # existing_containers <service>: the ids of that service's own containers,
  # running or stopped, one per line (never a one-off left by compose run); fails if Compose or
  # Docker cannot say
  local ids
  ids="$(compose ps -a -q "$1")" || return 1
  [[ -n "$ids" ]] || return 0
  # shellcheck disable=SC2086 # one container id per line
  docker inspect -f '{{.Id}} {{index .Config.Labels "com.docker.compose.oneoff"}}' $ids | awk '$2 == "False" { print $1 }'
}
start_existing() {  # start_existing <service>...: the existing containers of these services
  # started again as they were (their image, settings and secrets), with `docker start`, never
  # Compose. `docker compose start` also starts each service's dependencies (Compose v5.7.1), and
  # every app service depends on the one-shot migrate (service_completed_successfully), which
  # has no container on this stack (it runs with compose run --rm): it fails with "missing
  # dependency migrate" and starts nothing (proven on a throwaway project, P1-T19b). Never `up`,
  # which would recreate them with this stack's (new) images and secrets
  local s got ids=()
  (($#)) || die "usage: scripts/staging.sh start-existing <service>..."
  for s in "$@"; do
    [[ " ${DB_APP_SERVICES[*]} " == *" $s "* ]] || die "$s is not an app service (${DB_APP_SERVICES[*]}): nothing was started"
    got="$(existing_containers "$s")" || die "could not list the containers of $s (above): nothing was started"
    [[ -n "$got" ]] || die "no container of $s is left to start (scripts/staging.sh down removed it, or it never ran): nothing was started"
    mapfile -t -O "${#ids[@]}" ids <<< "$got"
  done
  docker start "${ids[@]}" >/dev/null
  echo "started $* (their own containers, as they were)"
}
db_serve_previous() {  # db-roles --serve-previous [--operator NAME]: a release (or the move) that
  # stopped before the move of the roles (review C1). The database still has its single role, so
  # neither `up` nor a rollback nor the owner's one-offs can start anything: the containers of the
  # build that ran are started again as they were (start_existing: never `up`, which would
  # recreate them with the new images and secrets, nor `compose start`, which fails on the
  # migrate dependency), then that build lifts the holds and clears the window (old_manage: in
  # its own backend, as ihr)
  local op="$1" state svcs=() rest=() s got
  rel_lock
  [[ -n "$(compose ps -q db 2>/dev/null || true)" ]] || die "the database is not running: start it first (docker compose start db), then run this again"
  state="$(db_roles_state)" || die "could not read the database's roles"
  [[ "$state" == single ]] || die "the database's roles are $state: this is only for a database that still has its single role. Start the stack: scripts/staging.sh up"
  # Every app service with a container of its own (the build that ran), read fail-closed
  for s in "${DB_APP_SERVICES[@]}"; do
    got="$(existing_containers "$s")" || die "could not list the containers of $s (above): nothing was started"
    [[ -z "$got" ]] || svcs+=("$s")
  done
  [[ " ${svcs[*]} " == *" backend "* ]] || die "no backend container of the build that ran is left: fix the cause and run scripts/staging.sh release again"
  for s in "${svcs[@]}"; do [[ "$s" == backend ]] || rest+=("$s"); done
  echo "== Starting the backend of the build that ran (its own container, its own address)"
  start_existing backend
  echo "== Lifting the holds and clearing the window (that build)"
  old_manage ihr_fleet_run release --operator "$op"
  old_manage ihr_fleet_run window --clear --operator "$op"
  if ((${#rest[@]})); then
    echo "== Starting its other containers: ${rest[*]}"
    start_existing "${rest[@]}"
  fi
  echo "Served by the build that ran; the database still has its single role. A release in progress stays unfinished (releases/attempt): fix the cause and run scripts/staging.sh release again (scripts/deploy.sh --release)."
}
running_app_services() {  # running_app_services <service>...: those of them that run, space
  # separated; fails (never "none run") if Compose cannot say
  local out
  # -a with --status running also lists a running one-off ("compose run"), which plain ps leaves out
  out="$(compose ps -a --status running --format '{{.Service}}' "$@")" || return 1
  echo "$out" | sort -u | tr '\n' ' '
}
db_roles_cmd() {  # db-roles --migrate [--operator NAME] | --serve-previous [--operator NAME] | --assert | --status
  local what="${1:-}" op="" state t0 t apps top logged
  local usage="usage: scripts/staging.sh db-roles --migrate [--operator NAME] | --serve-previous [--operator NAME] | --assert | --status"
  (($#)) && shift
  while (($#)); do
    case "$1" in
      --operator) op="${2:?--operator NAME}"; shift ;;
      *) die "$usage" ;;
    esac
    shift
  done
  [[ -f "$DIR/.env" ]] || die "run: scripts/staging.sh init <suffix>"
  case "$what" in
    --status)
      if db_secrets_split; then echo "secrets: split (secrets/app.env: ihr_app, secrets/db-owner.env: ihr_owner, secrets/audit-watch.env: ihr_audit_watch)"
      else echo "secrets: not split (scripts/staging.sh db-split-secrets)"; fi
      if [[ -n "$(compose ps -q db 2>/dev/null || true)" ]]; then
        echo "database: $(db_roles_state || echo unreadable)"
      else
        echo "database: not running"
      fi ;;
    --assert)
      # The runbook of DatabaseRoleLoginFailed and of a refusal for stored settings: both roles'
      # passwords set again from the secrets, every stored setting reset (as every up does)
      db_need_split_secrets
      [[ "$(db_roles_state)" == split ]] || die "the database's roles are not split: scripts/staging.sh db-roles --migrate"
      db_roles_apply
      echo "Re-asserted ihr_owner and ihr_app (passwords from the secrets, no stored settings in any database, no memberships) and the large-object revoke."
      echo "Restart the app containers so each checks its role again: scripts/staging.sh up" ;;
    --serve-previous) op="$(rel_operator "$op")"; db_serve_previous "$op" ;;
    --migrate)
      op="$(rel_operator "$op")"
      db_need_split_secrets
      s3_need_split_keys  # before the move: its migrate one-shots start the new build (S1)
      rel_lock
      [[ ! -f "$REL_ATTEMPT" ]] || die "a release is in progress (releases/attempt): finish it (or roll it back) first; a release moves the roles itself"
      # Only a stopped stack, on the build released last (review C2): a database restored to a
      # moment before the move. On a running server the move belongs to a release, which holds,
      # seals and takes a restore point first, and logs the build it starts; this would migrate
      # whatever build is tagged, outside the window, with neither
      # Read fail-closed: a Compose that cannot say which run is never taken for "none run"
      apps="$(running_app_services "${DB_APP_SERVICES[@]}")" \
        || die "could not tell whether any app container runs (docker compose ps, above): nothing was changed"
      [[ -z "${apps// /}" ]] || die "${apps% } still run(s): on a running server the roles move inside a release (scripts/deploy.sh --release, or scripts/staging.sh release), never with db-roles --migrate, which is for a stopped stack whose database was restored to a moment before the move. Nothing was changed"
      top="$(rlog state "$REL_LOG" | tail -1)" || die "could not read releases/releases.log"
      [[ -n "$top" ]] || die "no release is logged here: the roles move inside a release (scripts/deploy.sh --release). Nothing was changed"
      logged="$(rel_logged_backend "$top")"
      [[ -n "$logged" && "$(image_id "$(env_get IHR_BACKEND_IMAGE)" || true)" == "$logged" ]] \
        || die "$(env_get IHR_BACKEND_IMAGE) is not the build of the release logged last ($top): the move would migrate a build that was never released, outside the window and with no restore point. Release it instead (scripts/deploy.sh --release, or scripts/staging.sh release), which moves the roles itself. Nothing was changed"
      t0=$SECONDS
      DB_MOVE_OP="$op"
      trap 'db_move_exit $?' EXIT
      if [[ -z "$(compose ps -q db 2>/dev/null || true)" ]]; then compose up -d --wait db redis; fi
      state="$(db_roles_state)" || die "could not read the database's roles"
      case "$state" in
        split) echo "The roles are split already: nothing to move (scripts/staging.sh db-roles --assert re-asserts them)"; trap - EXIT; return 0 ;;
        none) die "the database has neither ihr nor ihr_owner: not a database of this stack" ;;
      esac
      # Under a full stop (design item 10): nothing of the app runs (checked above). A database
      # restored to a release's restore point has every hospital held (the point is taken after
      # the hold): the fleet-wide migrate below lifts the hold of each hospital it finishes
      echo "== [$((SECONDS - t0)) s] The move"
      db_roles_move_stopped
      DB_MOVE_STAGE=moved
      # The grants, by the owner: the check of the roles first (C6), the platform's schema (its
      # post_migrate converges public and the database), then every hospital schema
      echo "== [$((SECONDS - t0)) s] Converging the grants (migrate one-shot, as ihr_owner)"
      compose run --rm --no-deps -T migrate python manage.py ihr_db_role_check --for migrate
      compose run --rm --no-deps -T migrate python manage.py migrate_schemas --shared --noinput
      t=$SECONDS
      compose run --rm --no-deps -T migrate python manage.py ihr_fleet_run migrate --operator "$op"
      echo "grants of every hospital schema converged in $((SECONDS - t)) s"
      echo "== [$((SECONDS - t0)) s] Starting"
      up
      trap - EXIT
      t="$(rel_held_count)"
      [[ "${t:-0}" == 0 ]] || echo "${t} hospital(s) are still held (the fleet run above says why): fix it, then scripts/staging.sh fleet release --operator $op" >&2
      echo "== Roles moved in $((SECONDS - t0)) s. Next: scripts/staging.sh smoke; a window that came back with the restored database: scripts/staging.sh fleet window --clear --operator $op" ;;
    *) die "$usage" ;;
  esac
}

up() {  # up [--new-database]: the flag only where an empty database is really meant (db_guard)
  local new_db=0
  [[ "${1:-}" != --new-database ]] || new_db=1
  [[ $# -eq 0 || $new_db == 1 ]] || die "usage: scripts/staging.sh up [--new-database]"
  [[ -f "$DIR/.env" ]] || { echo "run: scripts/staging.sh init <suffix>" >&2; exit 1; }
  db_need_split_secrets
  watch_need_reader
  # CARE's storage keys (P1-T20b) before anything starts: a local proof splits its own (its
  # folder is where its secrets are made); a server refuses unsplit ones (S1/S2)
  if [[ "$(env_get IHR_S3)" == local && -f "$DIR/.local" ]]; then s3_split_care_key "$SECRETS"; fi
  s3_need_split_keys
  [[ -z "$(env_get IHR_BACKUP)" ]] || backup_config
  # Compose reads Fluent Bit's (and Grafana's) env files on every call: those secrets first
  if [[ -n "$(env_get IHR_MONITORING)" ]]; then logs_secrets; dashboards_secrets; fi
  db_guard "$new_db"
  # A running database of the single role is refused before anything is recreated (the db
  # container's settings changed with the split: recreating it would cut the old stack off)
  if [[ -n "$(compose ps -q db 2>/dev/null || true)" && "$(db_roles_state 2>/dev/null || true)" == single ]]; then
    die "the database still has its single role ihr: move it first, under a full stop (scripts/staging.sh db-roles --migrate, or scripts/staging.sh release; deploy/staging/README.md, Moving to split roles). Nothing was changed"
  fi
  compose up -d --wait db redis
  touch "$DIR/.db-created"
  # The database's two roles (P1-T19b), re-asserted as postgres at every start: passwords from
  # the secrets, no stored settings in any database, no memberships, large objects closed (a
  # role may ALTER ROLE itself: design.md, known limit 1). A database of the single role is
  # moved only under a full stop, never by a plain start
  case "$(db_roles_state || echo unreadable)" in
    split) db_roles_apply ;;
    single) die "the database still has its single role ihr: move it first, under a full stop (scripts/staging.sh db-roles --migrate, or scripts/staging.sh release; deploy/staging/README.md, Moving to split roles)" ;;
    *) die "could not read the database's roles (or it has neither ihr nor ihr_owner)" ;;
  esac
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
  # Terminology content that ships with the code (P1-T10a1, ADR-016): loaded once per version,
  # a no-op when nothing changed
  # (admin keeps no logs: what was newly loaded goes to the host's journal, hence the log store)
  compose --profile admin run --rm --no-deps -T admin python manage.py ihr_terminology_load --bundled \
    --operator "staging.sh up ($(id -un)@$(hostname -s))" | { grep '^loaded ' || true; } \
    | while IFS= read -r line; do echo "terminology $line"; logger -t ihr-terminology "$line" 2>/dev/null || true; done
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

repo_info() {  # repo_info [--keep-running]: the repository's info as JSON, read by a one-off
  # container (the stack may be down). --keep-running (a release, before its `up`): no existing
  # container is recreated, so a gateway that the outgoing backend still uses keeps the keys it
  # was started with (a recreated one would have only the split keys: the second fixer's review)
  local rc=0 norecreate=()
  [[ "${1:-}" != --keep-running ]] || norecreate=(--no-recreate)
  compose up --no-start ${norecreate[@]+"${norecreate[@]}"} db backup >/dev/null || die "could not create the stack's containers and volumes"
  [[ "$(env_get IHR_S3)" != local ]] || { s3_keys_current >/dev/null; compose up -d --wait ${norecreate[@]+"${norecreate[@]}"} s3 >/dev/null; }
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
  local curl_args=(-sk --max-time 20) slugs=() slug
  # Every active hospital (P1-T18b, O1): each must be served, never "503 upgrading"
  mapfile -t slugs < <(compose exec -T db psql -U postgres -d ihr -Atc \
    "SELECT slug FROM ihr_platform_hospital WHERE schema_name <> 'public' AND status = 'active' ORDER BY slug" 2>/dev/null \
    | grep -E '^[a-z][a-z0-9-]{2,30}$' || true)
  if [[ -n "$resolve" ]]; then
    for host in "$suffix" "alpha.$suffix" "console.$suffix" "auth.$suffix" "nope.$suffix" "files.$suffix" "status.$suffix" "ops.$suffix" \
        ${slugs[@]+"${slugs[@]/%/.$suffix}"}; do
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
    check "files: hospital pictures need a signature (P1-T20b)" 403 "$(code "https://files.$suffix:$port/$(env_get IHR_S3_FACILITY_BUCKET)/no-such-picture.png")"
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
  # Terminology answered here, from the content that ships with the code (P1-T10a1)
  check "terminology: bundled code systems active" \
    "$(compose exec -T backend python -c 'import importlib.util, json, pathlib; d = pathlib.Path(importlib.util.find_spec("ihr_terminology").origin).parent; print(len(json.loads((d / "content" / "MANIFEST.json").read_text())["files"]))' 2>/dev/null || echo unreadable)" \
    "$(compose exec -T backend python manage.py ihr_terminology_load --status 2>/dev/null | grep -c '^active ' || true)"
  check "unknown hospital address: no branding" 404 "$(code "https://nope.$suffix:$port/api/ihr/bootstrap/")"
  if ((${#slugs[@]} == 0)); then echo "skip  every hospital served: no active hospital yet"; fi
  for slug in ${slugs[@]+"${slugs[@]}"}; do
    check "hospital $slug served (not held: 503 upgrading)" 200 "$(code "https://$slug.$suffix:$port/api/ihr/bootstrap/")"
  done
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
  need="$(compose exec -T db psql -U postgres -Atc "SELECT (sum(pg_database_size(datname)) / 1048576)::bigint FROM pg_database" | tr -d '[:space:]')"
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
  # The roles in the copy as in the live database (P1-T19b): a copy from before the move is moved
  # here too; both roles get the secrets' passwords. The check runs as the owner (secrets/db-owner.env)
  echo "== The database roles in the restored copy"
  case "$(PG_TARGET="$DRILL" db_roles_state || echo unreadable)" in
    split) PG_TARGET="$DRILL" db_roles_apply; echo "split (re-asserted)" ;;
    single) PG_TARGET="$DRILL" db_roles_apply --move; echo "single role ihr: moved in the copy (ihr_owner, ihr_app)" ;;
    *) die "could not read the roles in the restored copy" ;;
  esac
  echo "== Audit trail and fingerprints, checked in the restored copy (as ihr_owner)"
  pw="$(db_password owner)"
  [[ -n "$pw" ]] || db_need_split_secrets
  dburl="postgres://ihr_owner:$pw@$DRILL:5432/ihr"
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

restore_live() {  # restore [--target T | --target-name NAME [--set LABEL]]: a lost server's
  # database, into this stack's own volume. --target-name: a restore point (pg_create_restore_point;
  # `staging.sh release` takes one per release), from the backup --set names (one taken before
  # the point). --replace-database: only for `staging.sh rollback --restore`, which has stopped
  # the stack and kept a copy where the disk allowed: the volume's database is replaced.
  local target="" name="" set="" replace=0 project="${IHR_PROJECT:-indiehr-staging}" vol i state
  local usage="usage: scripts/staging.sh restore [--target 'YYYY-MM-DD HH:MM:SS+05:30' | --target-name <restore point> [--set <backup label>]]"
  while (($#)); do
    case "$1" in
      --target) target="${2:?--target 'YYYY-MM-DD HH:MM:SS+05:30'}"; shift ;;
      --target-name) name="${2:?--target-name <restore point>}"; shift ;;
      --set) set="${2:?--set <backup label>}"; shift ;;
      --replace-database) replace=1 ;;
      *) die "$usage" ;;
    esac
    shift
  done
  [[ -z "$target" || -z "$name" ]] || die "$usage"
  [[ -z "$name" || "$name" =~ ^release-[0-9a-f]{12}(-dirty-[0-9a-f]{8})?(-[0-9]{10})?$ ]] || die "--target-name: a release's restore point (release-<tag>-<epoch>)"
  [[ -z "$set" || "$set" =~ ^[0-9]{8}-[0-9]{6}F(_[0-9]{8}-[0-9]{6}[DI])?$ ]] || die "--set: a backup label as pgbackrest info prints it"
  [[ -n "$(env_get IHR_BACKUP)" ]] || die "no backup settings here (backup-init, with --existing-passphrase on a new server)"
  [[ -z "$target" ]] || check_target "$target"
  local how=()
  if [[ -n "$target" ]]; then how=(--type=time "--target=$target" --target-action=promote)
  elif [[ -n "$name" ]]; then how=(--type=name "--target=$name" --target-action=promote ${set:+"--set=$set"})
  fi
  vol="${project}_pgdata"
  if [[ -n "$(compose ps -q db 2>/dev/null)" ]]; then die "the stack is running: scripts/staging.sh down first"; fi
  backup_config
  # Volumes made the Compose way (labels), then: never over an existing database
  compose up --no-start db backup >/dev/null || die "could not create the stack's containers and volumes"
  # The repository on this stack's own SeaweedFS (a local proof) must be running to read from
  [[ "$(env_get IHR_S3)" != local ]] || { s3_keys_current >/dev/null; compose up -d --wait s3 >/dev/null; }
  # Never over a database; an unfinished restore (its marker) is started again from scratch
  state="$(docker run --rm --user 70:70 --network none -v "$vol:/var/lib/postgresql" --entrypoint sh \
    "$(env_get IHR_POSTGRES_IMAGE)" -c "$PG_STATE")" || die "could not read $vol"
  case "$state" in
    db) ((replace)) || die "$vol already holds a database; restore only into an empty one. On a new server that was started by mistake: scripts/staging.sh down, then docker volume rm $vol (never down -v: it also deletes the log store, the signing keys and the WAL spool). To go back to a release's restore point: scripts/staging.sh rollback --restore" ;;
    restoring) echo "== An unfinished restore is in $vol: starting it again" ;;
  esac
  # The marker stays until recovery has finished: up refuses a half-restored database
  docker run --rm --user 70:70 --network none -v "$vol:/var/lib/postgresql" --entrypoint sh \
    "$(env_get IHR_POSTGRES_IMAGE)" -c 'touch /var/lib/postgresql/.ihr-restore-incomplete && find /var/lib/postgresql/18 -mindepth 1 -delete 2>/dev/null; mkdir -p /var/lib/postgresql/18/docker && chmod 700 /var/lib/postgresql/18/docker'
  echo "== Restore to ${target:-${name:-the newest moment in the backups}}${set:+ from backup $set} into $vol"
  # (inside staging.sh rollback, its own trap still says where the rollback stopped)
  trap 'rc=$?; docker rm -f ihr-restore ihr-restore-spool ihr-restore-recovery >/dev/null 2>&1; [[ -z "${REL_RB_STAGE:-}" ]] || rel_rollback_exit "$rc"' EXIT
  pg_container ihr-restore "$vol:/var/lib/postgresql" pgbackrest --stanza=main "${how[@]}" restore
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


# ---- Releases and rollbacks (P1-T18b; deploy/staging/README.md "Releases") --------------------
# releases/releases.log: one line per finished release and per rollback (scripts/release_log.py
# says what a line holds); releases/attempt: the release in progress; releases/stack-<tag>.tgz:
# each release's stack files as scripts/deploy.sh sent them (tracked files only, mode 600).
REL_DIR="$DIR/releases"
REL_LOG="$REL_DIR/releases.log"
REL_ATTEMPT="$REL_DIR/attempt"
RELEASES_KEPT=3  # the images of the last 3 logged releases are never removed
REL_TAG_RE='^[0-9a-f]{12}(-dirty-[0-9a-f]{8})?$'
FP_RE='^[0-9a-f]{64}$'
REL_WORKERS=(celery-worker celery-beat provisioner staff-worker)
REL_DRAIN_SECONDS="${IHR_RELEASE_DRAIN_SECONDS:-60}"
# The full audit check before the hold (hospitals still served) and the seal inside the window,
# each capped (O7)
REL_AUDIT_SECONDS="${IHR_RELEASE_AUDIT_SECONDS:-1800}"
REL_SEAL_SECONDS="${IHR_RELEASE_SEAL_SECONDS:-300}"

rlog() { python3 "$ROOT/scripts/release_log.py" "$@"; }
rel_sql() { compose exec -T db psql -U postgres -d ihr -v ON_ERROR_STOP=1 -Atc "$1"; }
rel_held_count() {  # rel_held_count: how many hospitals are held for a window; nothing if unreadable
  local n
  n="$(rel_sql "SELECT count(*) FROM ihr_platform_hospitalschemastate WHERE hold_reason = 'window' AND upgrading_since IS NOT NULL" 2>/dev/null || true)"
  [[ ! "$n" =~ ^[0-9]+$ ]] || echo "$n"
}
rel_lock() {  # one release or rollback at a time (fd 9 stays open until the script ends)
  (umask 077; mkdir -p "$REL_DIR")
  exec 9>"$REL_DIR/.lock"
  flock -n 9 || die "another release or rollback is running here"
}
rel_operator() {  # who runs it, as ihr_fleet_run records it (1-64 of letters, digits, . _ @ -)
  local who="${1:-$(id -un)}"
  [[ "$who" =~ ^[A-Za-z0-9._@-]{1,64}$ ]] || die "--operator: 1-64 letters, digits or . _ @ -"
  echo "$who"
}
rel_images() {  # "<name> <image>" for each of our images in this stack
  local g
  printf 'backend %s\nfrontend %s\npostgres %s\nkeycloak %s\n' "$(env_get IHR_BACKEND_IMAGE)" \
    "$(env_get IHR_FRONTEND_IMAGE)" "$(env_get IHR_POSTGRES_IMAGE)" "$(env_get IHR_KEYCLOAK_IMAGE)"
  [[ -z "$(env_get IHR_ACME)" ]] || printf 'acmedns %s\n' "$(env_get IHR_ACMEDNS_IMAGE)"
  if [[ -n "$(env_get IHR_MONITORING)" ]]; then
    g="$(env_get IHR_GRAFANA_IMAGE)"; printf 'grafana %s\n' "${g:-indiehr-grafana:staging}"
  fi
}
image_id() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null; }
image_label() {  # image_label <image> <label>: its value, or nothing
  local v; v="$(docker image inspect -f "{{index .Config.Labels \"$2\"}}" "$1" 2>/dev/null || true)"
  [[ "$v" == "<no value>" ]] || echo "$v"
}
repo_of() {  # repo_of <name:tag>: the name without its tag
  local r="${1%@*}"
  [[ "${r##*/}" != *:* ]] || r="${r%:*}"
  echo "$r"
}
release_tag_of() {  # release_tag_of <backend image or id>: its release tag (release_log.py tag)
  local id rev dirty
  id="$(image_id "$1")" || die "no image $1"
  rev="$(image_label "$1" org.opencontainers.image.revision)"
  dirty="$(image_label "$1" ihr.git-dirty)"
  rlog tag "${rev:-none}" "${dirty:-no}" "$id" 2>/dev/null \
    || die "image $1 carries no commit label: build it with scripts/deploy.sh"
}
running_backend_id() {  # the image the running backend was started from; else this stack's image
  local cid
  cid="$(compose ps -q backend 2>/dev/null || true)"
  if [[ -n "$cid" ]]; then docker inspect -f '{{.Image}}' "$cid"; else image_id "$(env_get IHR_BACKEND_IMAGE)"; fi
}
release_tag_images() {  # release_tag_images <tag>: each of our images also as <name>:<tag>, checked
  local tag="$1" name ref id
  [[ "$tag" =~ $REL_TAG_RE ]] || die "not a release tag: $tag"
  while read -r name ref; do
    id="$(image_id "$ref")" || die "image $ref ($name) is not here"
    docker tag "$id" "$(repo_of "$ref"):$tag"
    [[ "$(image_id "$(repo_of "$ref"):$tag")" == "$id" ]] || die "$(repo_of "$ref"):$tag is not $id"
  done < <(rel_images)
}
image_fingerprints() {  # image_fingerprints <backend image>: "<tenant> <public>" code fingerprints
  # (ihr_platform/release.py) read from the image itself: no database, no network, no secret
  local out
  out="$(docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges:true \
    --memory 512m --entrypoint python -e DJANGO_SETTINGS_MODULE=ihr_platform.settings.test "$1" -c \
    'import django; django.setup(); from ihr_platform import release; print(release.code_fingerprint(), release.code_fingerprint("public"))' \
    2>/dev/null | tail -1)"
  [[ "$out" =~ ^[0-9a-f]{64}\ [0-9a-f]{64}$ ]] || die "could not read the migration fingerprints of $1 (built before P1-T18a?)"
  echo "$out"
}
rel_fields() {  # rel_fields <tag> <backend image>: the image and fingerprint fields of a release line
  local name ref fps
  printf 'tag=%s\n' "$1"
  while read -r name ref; do printf '%s=%s\n' "$name" "$(image_id "$ref")"; done < <(rel_images)
  fps="$(image_fingerprints "$2")"
  read -r -a fps <<< "$fps"
  printf 'tenant_fp=%s\npublic_fp=%s\n' "${fps[0]}" "${fps[1]}"
}
attempt_get() {  # attempt_get <key>: a field of the release in progress
  [[ -f "$REL_ATTEMPT" ]] || return 0
  tr ' ' '\n' < "$REL_ATTEMPT" | sed -n "s/^$1=//p"
}
rel_unserved() {  # rel_unserved <tenant fingerprint>: live hospitals this build does not serve
  [[ "$1" =~ $FP_RE ]] || die "not a fingerprint: $1"
  rel_sql "SELECT h.slug FROM ihr_platform_hospital h
    LEFT JOIN ihr_platform_hospitalschemastate s ON s.hospital_id = h.id
    WHERE h.schema_name <> 'public' AND h.status IN ('active', 'suspended')
      AND (s.hospital_id IS NULL OR s.upgrading_since IS NOT NULL OR s.schema_fingerprint <> '$1')
    ORDER BY h.slug"
}
old_manage() {  # a manage.py command of the running (outgoing) build, never the new build on
  # the old schema (D10). Before the move of the roles (P1-T19b) the running backend is the owner
  # itself; after it, it runs as ihr_app, which may not write the release's records (the hold,
  # the window: the owner's tables), so the command runs in a one-off of the running backend's
  # image in the owner's (logged) migrate container
  local repo rc=0
  if backend_runs_as_app; then
    repo="$(repo_of "$(env_get IHR_BACKEND_IMAGE)"):ihr-outgoing"
    docker tag "$(running_backend_id)" "$repo"
    IHR_BACKEND_IMAGE="$repo" compose run --rm --no-deps -T migrate python manage.py "$@" || rc=$?
    docker rmi "$repo" >/dev/null 2>&1 || true
    return "$rc"
  fi
  compose exec -T backend python manage.py "$@"
}
window_open_now() {  # window_open_now "<ihr_fleet_run window's line>": the window holds this minute
  python3 -c '
import datetime as dt, re, sys
m = re.match(r"window: (\d\d-\d\d-\d{4} \d\d:\d\d) IST for (\d+) min", sys.argv[1])
if not m:
    sys.exit(1)
ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
start = dt.datetime.strptime(m[1], "%d-%m-%Y %H:%M").replace(tzinfo=ist)
sys.exit(0 if start <= dt.datetime.now(ist) < start + dt.timedelta(minutes=int(m[2])) else 1)' "$1"
}
window_end() {  # window_end "<ihr_fleet_run window's line>": the window's end in epoch seconds (or fail)
  python3 -c '
import datetime as dt, re, sys
m = re.match(r"window: (\d\d-\d\d-\d{4} \d\d:\d\d) IST for (\d+) min", sys.argv[1])
if not m:
    sys.exit(1)
ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
start = dt.datetime.strptime(m[1], "%d-%m-%Y %H:%M").replace(tzinfo=ist)
print(int((start + dt.timedelta(minutes=int(m[2]))).timestamp()))' "$1"
}
rel_workers_marker() {  # rel_workers_marker <until epoch>|clear: while a release has the workers
  # stopped, ContainerNotRunning does not page for them until then (the window's end; O6). A
  # textfile metric beside host-metrics.sh's (deploy/monitoring/prometheus/rules/availability.yml);
  # removed when the release ends, so a release that stops with the workers down is paged
  local dir f
  [[ -n "$(env_get IHR_MONITORING)" ]] || return 0
  dir="$(textfile_dir)"; f="$dir/ihr_release.prom"
  [[ -d "$dir" && -w "$dir" ]] || return 0
  if [[ "$1" == clear ]]; then rm -f "$f"; return 0; fi
  [[ "$1" =~ ^[0-9]+$ ]] || return 0
  printf '%s\n' "# HELP ihr_release_workers_stopped_until_seconds A release window has the workers stopped until then (staging.sh release)" \
    "# TYPE ihr_release_workers_stopped_until_seconds gauge" "ihr_release_workers_stopped_until_seconds $1" > "$f.tmp"
  chmod 644 "$f.tmp"
  mv -f "$f.tmp" "$f"
}
rel_drain() {  # rel_drain <hold time>: until no transaction of the app began before the hold
  local i=0 n=""
  while ((i <= REL_DRAIN_SECONDS)); do
    n="$(rel_sql "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()
      AND pid <> pg_backend_pid() AND usename <> 'postgres' AND xact_start < '$1'::timestamptz")"
    if [[ "$n" == 0 ]]; then echo "drained (${i} s)"; return 0; fi
    sleep 2; i=$((i + 2))
  done
  echo "WARNING: $n transaction(s) begun before the hold still open after $REL_DRAIN_SECONDS s; going on" \
    "(what they commit later is not in the restore point)" >&2
}
rel_audit_check() {  # rel_audit_check: the whole audit check (triggers, hash chains, every outside
  # fingerprint) by the outgoing build in the running backend (its read-only key), BEFORE the hold:
  # no hospital waits for it (O7). Capped (IHR_RELEASE_AUDIT_SECONDS). Prints ok, problems,
  # timeout or skipped.
  local rc=0
  if ! grep -q '^IHR_AUDIT_ANCHOR_WRITE_KEY=.' "$SECRETS/anchor-write.env" 2>/dev/null; then
    echo "audit: skipped, no fingerprint storage set up here (secrets/anchor-write.env)" >&2
    echo skipped; return 0
  fi
  compose exec -T backend timeout "$REL_AUDIT_SECONDS" python manage.py ihr_audit_verify --all >&2 || rc=$?
  case "$rc" in
    0) echo ok ;;
    124) echo "WARNING: the audit check did not end within $REL_AUDIT_SECONDS s (IHR_RELEASE_AUDIT_SECONDS); going on" >&2; echo timeout ;;
    *) echo "WARNING: the audit check with the outgoing build reported problems (above); going on" >&2; echo problems ;;
  esac
}
# Inside the window only what is final is sealed and one fingerprint written: no chain is
# recomputed and no stored fingerprint read while every hospital is held (O7; the full check ran
# before the hold). ihr_audit's own functions, as ihr_audit_verify --seal --anchor calls them.
REL_SEAL_PY='from django_tenants.utils import schema_context
from ihr_audit import anchor
from ihr_audit.seal import seal_once
from ihr_platform.models import Hospital
for h in Hospital.objects.exclude(schema_name="public").order_by("slug"):
    with schema_context(h.schema_name):
        seal_once(h.schema_name)
print("fingerprint written:", anchor.write())'
rel_seal() {  # rel_seal <outgoing backend image id>: seal and fingerprint the audit trail with the
  # old build and the worker's add-only key (seal and one fingerprint only, capped:
  # IHR_RELEASE_SEAL_SECONDS). Prints ok, problems, timeout or skipped.
  local repo rc=0 url="" envs=()
  if ! grep -q '^IHR_AUDIT_ANCHOR_WRITE_KEY=.' "$SECRETS/anchor-write.env" 2>/dev/null; then
    echo "audit: skipped, no fingerprint storage set up here (secrets/anchor-write.env)" >&2
    echo skipped; return 0
  fi
  if [[ "${REL_MOVE:-0}" == 1 ]]; then
    # Before the move of the roles (P1-T19b) the outgoing build runs as the single role: its
    # one-off takes the running backend's own address (the split roles do not exist yet)
    url="$(outgoing_database_url)" || true
    [[ -n "$url" ]] || { echo "WARNING: the running backend's database address could not be read; not sealed" >&2; echo problems; return 0; }
    envs=(-e DATABASE_URL -e IHR_DB_ROLES -e IHR_DB_ROLE)
  fi
  repo="$(repo_of "$(env_get IHR_BACKEND_IMAGE)"):ihr-outgoing"
  docker tag "$1" "$repo"
  DATABASE_URL="$url" IHR_DB_ROLES=off IHR_DB_ROLE="" \
    IHR_BACKEND_IMAGE="$repo" compose run --rm --no-deps -T ${envs[@]+"${envs[@]}"} celery-worker \
    timeout "$REL_SEAL_SECONDS" python manage.py shell -c "$REL_SEAL_PY" >&2 || rc=$?
  docker rmi "$repo" >/dev/null 2>&1 || true
  case "$rc" in
    0) echo ok ;;
    124) echo "WARNING: sealing did not end within $REL_SEAL_SECONDS s (IHR_RELEASE_SEAL_SECONDS); going on" >&2; echo timeout ;;
    *) echo "WARNING: sealing with the outgoing build failed (above); going on" >&2; echo problems ;;
  esac
}
rel_seal_new() {  # rel_seal_new: the same seal and one fingerprint with THIS build's worker (its
  # add-only key), after the migrations and before up (capped: IHR_RELEASE_SEAL_SECONDS). Prints
  # ok, problems, timeout or skipped; never stops the release (the watch alerts what stays)
  local rc=0
  if ! grep -q '^IHR_AUDIT_ANCHOR_WRITE_KEY=.' "$SECRETS/anchor-write.env" 2>/dev/null; then
    echo "audit: skipped, no fingerprint storage set up here (secrets/anchor-write.env)" >&2
    echo skipped; return 0
  fi
  compose run --rm --no-deps -T celery-worker \
    timeout "$REL_SEAL_SECONDS" python manage.py shell -c "$REL_SEAL_PY" >&2 || rc=$?
  case "$rc" in
    0) echo ok ;;
    124) echo "WARNING: sealing did not end within $REL_SEAL_SECONDS s (IHR_RELEASE_SEAL_SECONDS); going on" >&2; echo timeout ;;
    *) echo "WARNING: sealing with the new build failed (above); going on" >&2; echo problems ;;
  esac
}
rel_repo_info() {  # the repository's info as JSON: through the running backup service when it runs;
  # else a one-off that recreates nothing (a plain repo_info's `up` would re-create a running db
  # whose image tag deploy.sh moved, and the gateway with the split keys mid-release, which kills
  # the outgoing backend's storage key before `up`)
  if [[ -n "$(compose ps -q backup 2>/dev/null || true)" ]]; then
    compose exec -T backup pgbackrest --stanza=main --output=json --log-level-console=error info
  else
    repo_info --keep-running
  fi
}
rel_restore_point() {  # rel_restore_point <tag>: "<name> <lsn> <epoch>", or "- - -" without backups.
  # The name is unique, release-<tag>-<epoch>: recovery stops at the FIRST point of a name, so a
  # release of one build twice must never make two of them (D4)
  local name point
  name="release-$1-$(date +%s)"
  if [[ -z "$(env_get IHR_BACKUP)" ]]; then
    echo "no backups set up here: no restore point (a rollback across migrations is impossible)" >&2
    echo "- - -"; return 0
  fi
  rlog point-free "$REL_LOG" "$REL_ATTEMPT" "$name" >&2 || die "refused: restore point $name is already in the release log"
  # A restore point needs a backup from before it
  if ! rel_repo_info | grep -q '"label"'; then
    echo "no backup in the repository yet: a full one first" >&2
    compose exec -T backup pgbackrest --stanza=main --type=full backup >&2
  fi
  # Its WAL file is closed at once, so the spool pushes it within seconds
  point="$(rel_sql "SELECT pg_create_restore_point('$name')::text || ' ' || extract(epoch FROM now())::bigint")"
  rel_sql "SELECT pg_switch_wal()" >/dev/null
  echo "$name $point"
}
rel_backup_set() {  # rel_backup_set <epoch>: the newest backup label that ended before it
  rel_repo_info | python3 -c '
import json, sys
limit = int(sys.argv[1])
sets = [b for s in json.load(sys.stdin) for b in s.get("backup", []) if b["timestamp"]["stop"] < limit]
if not sets:
    sys.exit("no backup in the repository ended before the restore point")
print(max(sets, key=lambda b: b["timestamp"]["stop"])["label"])' "$1"
}
rel_wal_archived() {  # rel_wal_archived <lsn>: the repository already holds the WAL up to that
  # position, so a restore can reach the point: asked before anything is stopped or replaced (O10)
  local seg
  [[ "$1" =~ ^[0-9A-F]{1,8}/[0-9A-F]{1,8}$ ]] || return 1
  seg="$(rel_sql "SELECT pg_walfile_name('$1')")" || return 1
  [[ "$seg" =~ ^[0-9A-F]{24}$ ]] || return 1
  rel_repo_info | python3 -c '
import json, sys
seg = sys.argv[1]
tops = [a.get("max") or "" for s in json.load(sys.stdin) for a in s.get("archive", [])]
sys.exit(0 if any(len(t) == 24 and t[8:] >= seg[8:] for t in tops) else 1)' "$seg"
}
rel_db_used_mb() {  # the database volume's real size in MB, pg_wal included (a copy takes all of it)
  docker run --rm --network none --user 70:70 --cap-drop ALL --security-opt no-new-privileges:true \
    -v "${IHR_PROJECT:-indiehr-staging}_pgdata:/v:ro" --entrypoint du "$(env_get IHR_POSTGRES_IMAGE)" -sm /v \
    | awk '{print $1}'
}
release_prune() {  # untag release images beyond the kept ones (never one a container uses)
  local kept name ref repo t
  mapfile -t kept < <(rlog keep "$REL_LOG" "$REL_ATTEMPT" "$RELEASES_KEPT" "$(release_tag_of "$(running_backend_id)" 2>/dev/null || true)")
  while read -r name ref; do
    repo="$(repo_of "$ref")"
    while read -r t; do
      [[ "$t" =~ $REL_TAG_RE ]] || continue
      [[ " ${kept[*]} " != *" $t "* ]] || continue
      if docker rmi "$repo:$t" >/dev/null 2>&1; then echo "removed $repo:$t (beyond the last $RELEASES_KEPT releases)"; fi
    done < <(docker image ls --format '{{.Tag}}' "$repo")
  done < <(rel_images)
  # Untagged layers: only on a server (this machine's other images are not ours to remove)
  [[ -f "$DIR/.local" ]] || docker image prune -f >/dev/null
}
release_note() {  # release_note [--release] [key=value...]: the release in progress (releases/attempt),
  # from this stack's images. scripts/deploy.sh writes it before a plain start; `release` (with
  # --release) before its first change and again with its restore point. A plain start never
  # overwrites an unfinished release's record (its window, holds and restore point; D3/O4), and
  # nothing drops a restore point the record holds.
  local tag fields=() key given by_release=0 open_rp
  if [[ "${1:-}" == --release ]]; then by_release=1; shift; fi
  given=" $* "
  open_rp="$(attempt_get restore_point)"
  if ((by_release == 0)) && [[ "$(attempt_get via)" == release || "$open_rp" =~ ^release- ]]; then
    die "release $(attempt_get tag) is unfinished (releases/attempt: its window, holds and restore point). Finish it (scripts/staging.sh release --finish), roll back (scripts/staging.sh rollback [--restore]), or release this build in the window (scripts/staging.sh release: it keeps that restore point)"
  fi
  if [[ "$open_rp" =~ ^release- && "$given" != *" restore_point=$open_rp "* ]]; then
    die "releases/attempt holds restore point $open_rp: it is never dropped (scripts/staging.sh release carries it over)"
  fi
  tag="$(release_tag_of "$(env_get IHR_BACKEND_IMAGE)")"
  release_tag_images "$tag" >&2
  mapfile -t fields < <(rel_fields "$tag" "$(env_get IHR_BACKEND_IMAGE)")
  for key in restore_point lsn rp_time audit; do  # "-" unless given
    [[ "$given" == *" $key="* ]] || fields+=("$key=-")
  done
  [[ "$given" == *" via="* ]] || fields+=(via=plain)
  (umask 077; mkdir -p "$REL_DIR"
   rlog line kind=attempt "time=$(date -Iseconds)" "${fields[@]}" "$@" > "$REL_ATTEMPT.tmp")
  mv "$REL_ATTEMPT.tmp" "$REL_ATTEMPT"
  echo "$tag"
}
release_record() {  # release_record: the release in progress is logged (its smoke passed: the
  # caller ran it), if the running backend is its build and every live hospital is served
  local tag running fp unserved line top
  [[ -f "$REL_ATTEMPT" ]] || die "no release in progress (releases/attempt)"
  tag="$(attempt_get tag)"; fp="$(attempt_get tenant_fp)"
  running="$(running_backend_id)"
  [[ "$running" == "$(attempt_get backend)" ]] || die "the running backend is not release $tag's build"
  unserved="$(rel_unserved "$fp")"
  [[ -z "$unserved" ]] || die "not every live hospital is served: $(echo "$unserved" | tr '\n' ' ')"
  # The build logged last, deployed again (a changed setting, a proof): one release, logged once,
  # so a rollback never goes from it to itself (D1)
  top="$(rlog state "$REL_LOG" | tail -1)"
  if [[ "$top" == "$tag" ]]; then
    rm -f "$REL_ATTEMPT"
    echo "Release $tag is already the last logged release: not logged again"
    return 0
  fi
  line="$(sed "s/^kind=attempt /kind=release /; s/\$/ finished=$(date -Iseconds)/" "$REL_ATTEMPT")"
  # shellcheck disable=SC2086 # the line's words (checked again here)
  line="$(rlog line $line)"
  (umask 077; echo "$line" >> "$REL_LOG")
  rm -f "$REL_ATTEMPT"
  echo "Logged release $tag in $REL_LOG"
  release_prune
}
rel_unpack_stack() {  # rel_unpack_stack <tag>: that release's stack files (a server; never .env,
  # secrets/, generated/ or keys: the archive holds tracked files only). Files a later release
  # added stay (harmless: nothing reads them unless the compose files name them). Under umask 022
  # whatever the caller's: Prometheus and Fluent Bit (user 65534) read their settings (O2).
  local tag="$1" tmp
  if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    echo "== A checkout (a proof on this machine): its stack files are git's; check out $tag's commit if they changed"
    return 0
  fi
  [[ -f "$REL_DIR/stack-$tag.tgz" ]] || { echo "WARNING: no stack files kept for $tag: the current ones stay" >&2; return 0; }
  tmp="$(mktemp -d "$REL_DIR/.unpack.XXXXXX")"
  # New files (new inodes), so this script, still running, reads on undisturbed
  (umask 022
   tar -C "$tmp" --no-same-owner -xzf "$REL_DIR/stack-$tag.tgz"
   cd "$tmp" && find . -type f -print0 | while IFS= read -r -d '' f; do
     mkdir -p "$ROOT/$(dirname "$f")"; cp -p --remove-destination "$f" "$ROOT/$f"; done)
  rm -rf "$tmp"
  echo "== Stack files of $tag restored into $ROOT"
}
rel_check_images() {  # rel_check_images <names> <plan file>: every target image of those names is
  # here (checked before anything changes, D5)
  local names=" $1 " name ref id missing=()
  while read -r name ref; do
    [[ "$names" == *" $name "* ]] || continue
    id="$(sed -n "s/^target_$name=//p" "$2")"
    [[ -n "$id" ]] || continue
    docker image inspect "$id" >/dev/null 2>&1 || missing+=("$name=$id")
  done < <(rel_images)
  ((${#missing[@]} == 0)) || die "images of the target are no longer here: ${missing[*]} (nothing was changed)"
}
rel_point_images() {  # rel_point_images <names> <plan file>: this stack's tags at the target's images
  # (all checked first: never half re-pointed)
  local names=" $1 " name ref id
  rel_check_images "$1" "$2"
  while read -r name ref; do
    [[ "$names" == *" $name "* ]] || continue
    id="$(sed -n "s/^target_$name=//p" "$2")"
    [[ -n "$id" ]] || continue
    docker tag "$id" "$ref"
    echo "$ref -> $id"
  done < <(rel_images)
}

container_env() {  # container_env <container id> <name>: that variable as the container was created
  # with it (its env files then). Callers compare it, never print it
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$1" 2>/dev/null | sed -n "s/^$2=//p" | tail -1
}
rel_outgoing_storage() {  # whether the running backend still reaches object storage with the keys
  # it was started with: "yes" (this stack's gateway runs with the same key id), "no", or
  # "provider" (a provider's keys: this machine cannot tell)
  local b s key
  b="$(compose ps -q backend 2>/dev/null || true)"
  [[ -n "$b" ]] || { echo no; return 0; }
  [[ "$(env_get IHR_S3)" == local ]] || { echo provider; return 0; }
  s="$(compose ps -q s3 2>/dev/null || true)"
  [[ -n "$s" ]] || { echo no; return 0; }
  key="$(container_env "$b" BUCKET_KEY)"
  if [[ -n "$key" ]]; then
    [[ "$key" == "$(container_env "$s" IHR_S3_CARE_KEY)" ]] && echo yes || echo no
    return 0
  fi
  key="$(container_env "$b" FILE_UPLOAD_KEY)"
  [[ -n "$key" && "$key" == "$(container_env "$s" IHR_S3_FILES_KEY)" ]] && echo yes || echo no
}
rel_serve_outgoing_advice() {  # rel_serve_outgoing_advice: at stage 1 (held, workers stopped, the
  # database unchanged by this release) with no rollback accepted, the backend that ran still
  # serves its hospitals once the holds are lifted in it (the fleet dispatch sends fleet release
  # and hold to it while the release's migrations have not started). Its object-storage key must
  # still work: the gateway it was started with (rel_repo_info recreates nothing), or the provider's
  # old key. Prints "; or, ..." or nothing (the second fixer's review)
  local op="${REL_OP:-<you>}" storage
  ((${REL_STAGE:-0} == 1)) || return 0
  [[ -n "$(compose ps -q backend 2>/dev/null || true)" ]] || return 0
  storage="$(rel_outgoing_storage 2>/dev/null || echo no)"
  case "$storage" in
    no) echo "; its backend still runs, but its object-storage key is no longer the gateway's (s3 was recreated with the split keys): serving it again would break patient files and pictures" ;;
    *) echo "; or, as its backend still runs and the database is unchanged by this release, serve the hospitals in it: scripts/staging.sh fleet release --operator $op then scripts/staging.sh fleet window --clear --operator $op (a worker this release stopped stays stopped until scripts/staging.sh start-existing <it>, of ${REL_WORKERS[*]}; $([[ "$storage" == provider ]] && echo "only while its old storage key (BUCKET_KEY) still works at the provider" || echo "only while the gateway is not restarted: the deployed start.sh refuses the combined key"))" ;;
  esac
}
rel_rollback_advice() {  # rel_rollback_advice: the rollbacks `rollback` accepts now, found the way
  # it finds them (release_log.py plan with the running build, then rel_roles_guard), so advice
  # never names one it refuses (D3/O4; review C1)
  local running out ways=() presplit=0 pres3=0 state t
  # Before the move of the roles no rollback is accepted (rel_roles_guard): the build that ran
  # serves again on its own containers. The database's own answer first; unreadable, what this
  # release knows (REL_MOVE: the move was still to come)
  state="$(db_roles_state 2>/dev/null || echo unreadable)"
  if [[ "$state" == single || ( "$state" == unreadable && "${REL_MOVE:-0}" == 1 ) ]]; then
    out="$REL_SERVE_PREVIOUS ${REL_OP:-<you>} (the database still has its single role, so no rollback is accepted; this starts the build that ran on its own containers again, lifts the holds and clears the window)"
    # Its backend still runs (the database answered single): the fleet steps run in it before the
    # move (the fleet dispatch, through old_manage), so they lift the holds too
    if [[ "$state" == single && -n "$(compose ps -q backend 2>/dev/null || true)" ]]; then
      out+="; or, as its backend still runs, scripts/staging.sh fleet release --operator ${REL_OP:-<you>} then scripts/staging.sh fleet window --clear --operator ${REL_OP:-<you>} (in that backend; a worker this release stopped stays stopped until scripts/staging.sh start-existing <it>, of ${REL_WORKERS[*]})"
    fi
    echo "$out"
    return 0
  fi
  running="$( (release_tag_of "$(running_backend_id)") 2>/dev/null)" || running=""
  if [[ -z "$running" ]]; then
    echo "the running build could not be read: scripts/staging.sh rollback [--restore] says whether it can"
    return 0
  fi
  if out="$(rlog plan "$REL_LOG" "$REL_ATTEMPT" "$running" 2>/dev/null)"; then
    t="$(sed -n 's/^target=//p' <<< "$out")"
    if rel_target_presplit "$t"; then presplit=1
    elif rel_target_pre_s3split "$t"; then pres3=1
    else ways+=("scripts/staging.sh rollback (no --restore: it lifts the holds and clears the window; nothing is lost)"); fi
  fi
  if out="$(rlog plan "$REL_LOG" "$REL_ATTEMPT" "$running" --restore 2>/dev/null)"; then
    t="$(sed -n 's/^target=//p' <<< "$out")"
    if rel_target_presplit "$t"; then presplit=1
    elif rel_target_pre_s3split "$t"; then pres3=1
    else ways+=("scripts/staging.sh rollback --restore (back to the window's restore point $(sed -n 's/^restore_point=//p' <<< "$out"); what was written since is lost)"); fi
  fi
  if ((${#ways[@]} == 0 && presplit)); then
    echo "no rollback is accepted from here: the release before this one predates the split database roles (P1-T19b). Fix forward$(rel_serve_outgoing_advice)"
  elif ((${#ways[@]} == 0 && pres3)); then
    echo "no rollback is accepted from here: the release before this one predates the split object-storage keys (P1-T20b). Fix forward$(rel_serve_outgoing_advice)"
  elif ((${#ways[@]} == 0)); then
    echo "no rollback is accepted from here (scripts/staging.sh rollback says why): fix forward$(rel_serve_outgoing_advice)"
  elif ((${#ways[@]} == 1)); then
    echo "${ways[0]}"
  else
    echo "${ways[0]}; or ${ways[1]}"
  fi
}
# The EXIT trap of release (globals: a trap runs after the function's locals are gone)
REL_STAGE=0 REL_PREV="" REL_HAD_PREV=0 REL_ANNOUNCED=0 REL_OP="" REL_HELD_STATE=""
rel_release_exit() {  # rel_release_exit <exit status>: what the release changed, and the way on.
  # Stage 0: nothing held, stopped or migrated; 1: hospitals held (the hold succeeded, or the
  # database shows some held) and workers stopped, the database untouched by this release; 2: its
  # restore point taken and migrations may have begun (D2/D10). The rollback advice is what
  # `rollback` accepts (rel_rollback_advice)
  local rc="$1"
  rel_workers_marker clear
  if ((rc != 0 && REL_STAGE == 0)); then
    # Nothing changed: the record of a release in progress goes back to what it was
    if ((REL_HAD_PREV)); then mv -f "$REL_PREV" "$REL_ATTEMPT"; else rm -f "$REL_ATTEMPT"; fi
  fi
  [[ -z "$REL_PREV" ]] || rm -f "$REL_PREV"
  ((rc != 0)) || return 0
  case "$REL_STAGE" in
    0) echo "RELEASE DID NOT START (exit $rc): nothing was held, stopped or migrated; the running build serves as before.$( ((REL_ANNOUNCED == 0)) || echo " The window announced now stays: clear it with scripts/staging.sh fleet window --clear --operator $REL_OP, or release again inside it.")" >&2 ;;
    1) echo "RELEASE STOPPED BEFORE ANY MIGRATION (exit $rc): ${REL_HELD_STATE:-every hospital is held (503 upgrading), the workers are stopped}, and the window stays announced; the database is unchanged by this release. Serve them again: $(rel_rollback_advice); or fix the cause and run scripts/staging.sh release again (deploy/staging/README.md, Releases)" >&2 ;;
    *) echo "RELEASE DID NOT FINISH (exit $rc): migrations may have begun; hospitals held so far stay at 503 upgrading and the window stays announced. Fix forward and run scripts/staging.sh release again, with this build or a fixed one (the window's restore point is kept), or roll back: $(rel_rollback_advice) (deploy/staging/README.md, Releases)" >&2 ;;
  esac
}
release() {  # release [--now [--minutes N] [--note T]] [--operator NAME] | --finish [--operator NAME]
  local now=0 minutes=60 note="" op="" finish=0 new_ref tag fps=() cid="" old_id="" win hold_at="" apps
  local rp=() carry=() from="" migr=none audit=- seal=- seal_s=- quiet_until unserved t0 fleet_rc=0 smoke_rc=0 held
  while (($#)); do
    case "$1" in
      --now) now=1 ;;
      --minutes) minutes="${2:?--minutes N}"; shift ;;
      --note) note="${2:?--note TEXT}"; shift ;;
      --operator) op="${2:?--operator NAME}"; shift ;;
      --finish) finish=1 ;;
      *) die "usage: scripts/staging.sh release [--now [--minutes N] [--note TEXT]] [--operator NAME] | --finish" ;;
    esac
    shift
  done
  [[ "$minutes" =~ ^[0-9]+$ ]] || die "--minutes N"
  op="$(rel_operator "$op")"
  [[ -f "$DIR/.env" ]] || die "run: scripts/staging.sh init <suffix>"
  # This build runs the app as ihr_app and the owner's one-shots as ihr_owner (P1-T19b)
  db_need_split_secrets
  # ...and refuses one storage key for both buckets (P1-T20b): checked before anything is held,
  # never found by the migrate one-shot with every hospital held (S1)
  s3_need_split_keys
  # ...and the audit watch's reader key where the monitoring is on (P1-T21 C4): else step 8's
  # up would fail after the migrations
  watch_need_reader
  rel_lock
  t0=$SECONDS
  if ((finish)); then release_finish "$op"; return; fi
  new_ref="$(env_get IHR_BACKEND_IMAGE)"
  tag="$(release_tag_of "$new_ref")"
  win="$(image_fingerprints "$new_ref")"
  read -r -a fps <<< "$win"
  cid="$(compose ps -q backend 2>/dev/null || true)"
  [[ -z "$cid" ]] || old_id="$(docker inspect -f '{{.Image}}' "$cid")"
  if [[ -n "$old_id" && "$old_id" == "$(image_id "$new_ref")" ]]; then
    [[ "$(attempt_get tag)" != "$tag" ]] || die "release $tag already runs and is not finished: after the fix, scripts/staging.sh release --finish"
    die "release $tag already runs: nothing to release (scripts/deploy.sh sends a new build first)"
  fi
  if [[ -z "$cid" ]]; then
    # Without a backend nothing is held: then no other service of the app may run either (D8)
    apps="$(running_app_services "${REL_WORKERS[@]}" frontend)" \
      || die "could not tell whether any app container runs (docker compose ps, above): nothing changed"
    [[ -z "${apps// /}" ]] || die "the backend is not running, but ${apps% } still run(s) and would write during the migrations. Stop the stack first (scripts/staging.sh down), or start the old backend again (scripts/staging.sh start-existing backend: its own container, as it was) so the release holds every hospital: nothing changed"
  fi
  release_tag_images "$tag"
  echo "== Release $tag (hospitals' schema ${fps[0]:0:12}, platform's ${fps[1]:0:12}) by $op"
  # The restore point belongs to the open window, not to a build: an unfinished release's point is
  # carried over, whatever its build (the database has not been released since; D3/O4)
  if [[ "$(attempt_get restore_point)" =~ ^release- ]]; then
    from="$(attempt_get rp_from)"; from="${from:-$(attempt_get tag)}"
    carry=("restore_point=$(attempt_get restore_point)" "lsn=$(attempt_get lsn)" "rp_time=$(attempt_get rp_time)")
    [[ "$from" == "$tag" ]] || carry+=("rp_from=$from")
    migr=started
  elif [[ "$(attempt_get migrations)" == started ]]; then
    migr=started  # an unfinished release without backups: its migrations may have begun
  fi
  # This release's record before its first change (D2): a rollback finds it from here on. If it
  # stops before anything is held, the record goes back to what it was (rel_release_exit).
  REL_OP="$op" REL_STAGE=0 REL_HAD_PREV=0
  REL_PREV="$(mktemp "$REL_DIR/.attempt-prev.XXXXXX")"
  if [[ -f "$REL_ATTEMPT" ]]; then cp -p "$REL_ATTEMPT" "$REL_PREV"; REL_HAD_PREV=1; fi
  trap 'rel_release_exit $?' EXIT
  release_note --release via=release "migrations=$migr" ${carry[@]+"${carry[@]}"} >/dev/null
  if [[ -n "$cid" ]]; then
    # 1. The announcement (the web app shows it; ihr_fleet_run window), by the running build
    win="$(old_manage ihr_fleet_run window | tail -1)"
    if ! window_open_now "$win"; then
      ((now)) || die "no announced window is open now ($win). Announce one ahead (scripts/staging.sh fleet window runs it in the running backend): scripts/staging.sh fleet window --start 'DD-MM-YYYY HH:MM' --minutes 90 --note '...' --operator $op; or pass --now"
      old_manage ihr_fleet_run window --start "$(TZ=Asia/Kolkata date +'%d-%m-%Y %H:%M')" --minutes "$minutes" \
        --note "$note" --operator "$op"
      REL_ANNOUNCED=1
      win="$(old_manage ihr_fleet_run window | tail -1)"
    fi
    echo "$win"
    # 2a. The whole audit trail checked by the outgoing build while it still serves (capped)
    echo "== [$((SECONDS - t0)) s] Checking the audit trail (outgoing build, before the hold)"
    audit="$(rel_audit_check)"
    # 2. Every hospital held, in one transaction, by the outgoing build (503 upgrading)
    echo "== [$((SECONDS - t0)) s] Holding every hospital"
    if ! old_manage ihr_fleet_run hold --operator "$op"; then
      # One transaction: a failed hold normally held nobody (stage 0, the record goes back). Only
      # what the database shows is claimed (D2/O3): never "every hospital is held"
      held="$(rel_held_count)"
      [[ "$held" != 0 ]] || die "the hold failed (above): no hospital is held"
      REL_STAGE=1
      if [[ -n "$held" ]]; then
        REL_HELD_STATE="the hold failed, and $held hospital(s) are held for the window (503 upgrading); the workers were not stopped"
      else
        REL_HELD_STATE="the hold failed, and the database did not say whether any hospital is held; the workers were not stopped"
      fi
      die "the hold failed (above)"
    fi
    REL_STAGE=1
    hold_at="$(rel_sql "SELECT now()")"
    rel_move_pending
    # 3. No worker writes into a hospital from here (and none is paged as down until the window's end)
    echo "== [$((SECONDS - t0)) s] Stopping the workers: ${REL_WORKERS[*]}"
    compose stop "${REL_WORKERS[@]}"
    quiet_until="$(window_end "$win" 2>/dev/null || echo $(($(date +%s) + minutes * 60)))"
    rel_workers_marker "$quiet_until"
    # 4. Requests that began before the hold finish (capped)
    echo "== [$((SECONDS - t0)) s] Draining"
    rel_drain "$hold_at"
    # 5. The audit trail sealed and fingerprinted by the outgoing build (seal only, capped)
    echo "== [$((SECONDS - t0)) s] Sealing and fingerprinting the audit trail (outgoing build)"
    seal_s=$SECONDS
    seal="$(rel_seal "$old_id")"
    seal_s=$((SECONDS - seal_s))
  else
    echo "== The stack is not serving: no announcement, hold, drain or seal needed"
    REL_STAGE=2  # the database starts on this stack's (possibly new) Postgres image
    release_note --release via=release migrations=started ${carry[@]+"${carry[@]}"} >/dev/null
    compose up -d --wait db redis
    rel_move_pending
  fi
  # 6. The restore point: the window's own (carried from an unfinished release), or a new one
  if ((${#carry[@]})); then
    rp=("${carry[0]#restore_point=}" "${carry[1]#lsn=}" "${carry[2]#rp_time=}")
    echo "== [$((SECONDS - t0)) s] Keeping the window's restore point (taken for release $from): ${rp[0]} at ${rp[1]}"
  else
    echo "== [$((SECONDS - t0)) s] Restore point"
    win="$(rel_restore_point "$tag")"
    read -r -a rp <<< "$win"
    carry=("restore_point=${rp[0]}" "lsn=${rp[1]}" "rp_time=${rp[2]}")
    echo "${rp[0]} at ${rp[1]}"
  fi
  REL_STAGE=2
  release_note --release via=release migrations=started "${carry[@]}" "audit=$audit" "seal=$seal" "seal_s=$seal_s" >/dev/null
  # 6a. The move of the database roles (P1-T19b), once, under a full stop: every hospital is held,
  # the workers are stopped and drained, the trail sealed and the restore point taken (the
  # database as it was); then every app container stops, ihr becomes ihr_owner, ihr_app is made,
  # and the migrations below converge the grants as the owner
  if ((REL_MOVE)); then
    echo "== [$((SECONDS - t0)) s] Moving the database roles to ihr_owner and ihr_app (P1-T19b)"
    db_roles_move_stopped
    REL_MOVE=0
  else
    # The roles as the model has them before the check below, as every up does (also when a
    # release that moved them stopped before its migrations and runs again)
    db_roles_apply
  fi
  # 7. The new build: the roles checked (C6), the platform's schema, then each hospital (2 at a
  # time, logged for its alerts; the grants of every hospital schema converged as the owner)
  echo "== [$((SECONDS - t0)) s] Platform (public) migrations"
  compose run --rm --no-deps -T migrate python manage.py ihr_db_role_check --for migrate
  compose run --rm --no-deps -T migrate python manage.py migrate_schemas --shared --noinput
  echo "== [$((SECONDS - t0)) s] Hospital migrations (ihr_fleet_run, as scripts/staging.sh fleet)"
  compose run --rm --no-deps -T migrate python manage.py ihr_fleet_run migrate check roles packs --operator "$op" || fleet_rc=$?
  # 7a. What the migrations, roles and packs wrote into the trails (suspended and offboarding
  # hospitals too) sealed and fingerprinted by the new build before anything starts: the audit
  # watch, recreated by up, never finds it waiting for seal_inactive's next run (P1-T21 C3)
  echo "== [$((SECONDS - t0)) s] Sealing what the migrations wrote (new build)"
  rel_seal_new >/dev/null
  # 8. Start (a hospital this release left held stays at 503; the others start)
  echo "== [$((SECONDS - t0)) s] Starting release $tag"
  up
  rel_workers_marker clear
  # 9. Every hospital served, and the smoke checks
  echo "== [$((SECONDS - t0)) s] Smoke"
  smoke || smoke_rc=$?
  unserved="$(rel_unserved "${fps[0]}")"
  if [[ -n "$unserved" || $smoke_rc != 0 || $fleet_rc != 0 ]]; then
    trap - EXIT
    rel_release_exit 0
    echo "RELEASE $tag IS NOT FINISHED ($((SECONDS - t0)) s): not served: ${unserved//$'\n'/ }; fleet run exit $fleet_rc, smoke exit $smoke_rc." >&2
    echo "The others are served; the window stays announced. Each held hospital's newest upgrade record names the step and error: fix it, then scripts/staging.sh fleet --resume <the fleet run above> --operator $op, then scripts/staging.sh release --finish. Or roll back: $(rel_rollback_advice)." >&2
    exit 1
  fi
  release_finish "$op" --checked
  trap - EXIT
  rel_release_exit 0
  echo "== Release $tag finished in $((SECONDS - t0)) s"
}
REL_MOVE=0
rel_move_pending() {  # sets REL_MOVE: the database still has its single role, so this release
  # moves it (P1-T19b; the secrets are split already: release checked)
  REL_MOVE=0
  case "$(db_roles_state || echo unreadable)" in
    split) ;;
    single) REL_MOVE=1; echo "== The database still has its single role ihr: this release moves it to ihr_owner and ihr_app (P1-T19b)" ;;
    *) die "could not read the database's roles" ;;
  esac
}
release_finish() {  # release_finish <operator> [--checked]: close the window and log the release
  local op="$1" tag
  [[ -f "$REL_ATTEMPT" ]] || die "no release in progress (releases/attempt)"
  tag="$(attempt_get tag)"
  if [[ "${2:-}" != --checked ]]; then
    smoke || die "release $tag: the smoke checks fail (above): not finished"
  fi
  release_record
  compose run --rm --no-deps -T migrate python manage.py ihr_fleet_run window --clear --operator "$op"
}

# The EXIT trap of rollback (globals). Stage 0: nothing changed; 1: images re-pointed (no
# restore); 2: the stack stopped; 3: the database being replaced; 4: restored and logged (D5)
REL_RB_STAGE="" REL_RB_PLAN="" REL_RB_TARGET="" REL_RB_RP="" REL_RB_SET="" REL_RB_ASIDE=""
rel_rollback_exit() {  # rel_rollback_exit <exit status>: where a rollback stopped, and the way on
  local rc="$1"
  [[ -z "$REL_RB_PLAN" ]] || rm -f "$REL_RB_PLAN"
  ((rc != 0)) || return 0
  case "$REL_RB_STAGE" in
    1) echo "ROLLBACK DID NOT FINISH (exit $rc): this stack's images point at $REL_RB_TARGET; the database was not touched. Fix the cause (above) and run scripts/staging.sh rollback again: it does the same steps again" >&2 ;;
    2) echo "ROLLBACK STOPPED WITH THE STACK DOWN (exit $rc): the database and the images are unchanged. Start it again: scripts/staging.sh up (then fleet release and fleet window --clear if hospitals are held)" >&2 ;;
    3) echo "ROLLBACK STOPPED DURING THE RESTORE (exit $rc): the stack is down and its database volume is being replaced (Postgres's tag points at $REL_RB_TARGET's image). Finish the restore: scripts/staging.sh restore --target-name $REL_RB_RP --set $REL_RB_SET --replace-database; then the remaining steps by hand (deploy/staging/README.md, Releases).${REL_RB_ASIDE:+ The database as it was is in volume $REL_RB_ASIDE.}" >&2 ;;
    4) echo "ROLLBACK DID NOT FINISH (exit $rc): the database is restored to $REL_RB_RP and the rollback to $REL_RB_TARGET is logged; do not run rollback again. Finish by hand: scripts/staging.sh up, fleet release --operator <you>, fleet window --clear --operator <you>, smoke, backup-now full${REL_RB_ASIDE:+ (the database as it was: volume $REL_RB_ASIDE)}" >&2 ;;
    *) echo "ROLLBACK STOPPED (exit $rc): nothing was changed" >&2 ;;
  esac
}
rel_target_presplit() {  # rel_target_presplit <target tag>: its kept stack predates the split
  # database roles (P1-T19b: no secrets/db-owner.env in its compose.yaml). Never dies
  local text
  [[ -f "$REL_DIR/stack-$1.tgz" ]] || return 1
  text="$(tar -xzOf "$REL_DIR/stack-$1.tgz" deploy/staging/compose.yaml 2>/dev/null || true)"
  [[ "$text" != *"secrets/db-owner.env"* ]]
}
rel_s3_split_here() {  # this site's CARE storage keys are P1-T20b's two (never dies)
  grep -q '^FILE_UPLOAD_KEY=.' "$SECRETS/app.env" 2>/dev/null \
    || grep -q '^IHR_S3_FILES_KEY=' "$SECRETS/s3-gateway.env" 2>/dev/null
}
rel_target_pre_s3split() {  # rel_target_pre_s3split <target tag>: its kept stack predates CARE's
  # split storage keys (P1-T20b: its deploy/s3/start.sh knows only the combined ihr-care key) and
  # this site's keys are split. Its backend would sign patient files with the pictures key, its
  # gateway would refuse both new keys (and exit on its next start), and its setup would make the
  # pictures bucket public again (C1/S3). Never dies
  local text
  [[ -f "$REL_DIR/stack-$1.tgz" ]] || return 1
  rel_s3_split_here || return 1
  text="$(tar -xzOf "$REL_DIR/stack-$1.tgz" deploy/s3/start.sh 2>/dev/null || true)"
  [[ "$text" == *IHR_S3_CARE_KEY* || "$text" != *IHR_S3_FILES_KEY* ]]
}
rel_outgoing_unmigrated() {  # a release is in progress whose migrations have not started, and the
  # backend that runs is not this stack's build (the outgoing one, which held the hospitals): its
  # holds are lifted by that build, never by the new build on the old schema (D10)
  [[ -f "$REL_ATTEMPT" && "$(attempt_get migrations)" != started ]] || return 1
  [[ "$(running_backend_id 2>/dev/null)" != "$(image_id "$(env_get IHR_BACKEND_IMAGE)")" ]]
}
rel_db_single() {  # the running database still has its single role ihr (the move has not run)
  [[ -n "$(compose ps -q db 2>/dev/null || true)" && "$(db_roles_state 2>/dev/null || true)" == single ]]
}
REL_SERVE_PREVIOUS="scripts/staging.sh db-roles --serve-previous --operator"
rel_roles_guard() {  # rel_roles_guard <target tag>: never back to a release from before the split
  # database roles (P1-T19b). Its kept stack would start the app as the single role ihr, which the
  # move renamed (and a restore to a point before the move brings back a database whose roles the
  # current secrets do not match): refused before anything changes; fix forward. Nor any rollback
  # while the database still has its single role (review C1): every rollback ends with `up`,
  # which refuses that database, after the image tags were moved
  if rel_db_single; then
    die "refused: the database still has its single role ihr (the move of the roles has not run), and a rollback starts the stack with up, which needs the split roles. Serve the build that ran: $REL_SERVE_PREVIOUS ${REL_OP:-<you>}; or fix the cause and run scripts/staging.sh release again. Nothing was changed"
  fi
  if rel_target_presplit "$1"; then
    die "refused: release $1 predates the split database roles (P1-T19b): its stack would run the app as the single role ihr, which no longer exists. Fix forward (deploy/staging/README.md, Moving to split roles); nothing was changed"
  fi
  if rel_target_pre_s3split "$1"; then
    die "refused: release $1 predates the split object-storage keys (P1-T20b): its backend would sign patient files with the pictures key, its gateway knows only the retired combined key, and its set-up would make the pictures bucket public again. Fix forward (deploy/staging/README.md, Releases); nothing was changed"
  fi
}
rollback() {  # rollback [--restore] [--operator NAME]: back to the release before the running one
  local restore=0 op="" running plan answer project="${IHR_PROJECT:-indiehr-staging}" t0
  local need free margin floor aside="" set leave target rp rp_time rp_of lsn keep_copy=0 names held
  while (($#)); do
    case "$1" in
      --restore) restore=1 ;;
      --operator) op="${2:?--operator NAME}"; shift ;;
      *) die "usage: scripts/staging.sh rollback [--restore] [--operator NAME]" ;;
    esac
    shift
  done
  op="$(rel_operator "$op")"
  [[ -f "$DIR/.env" ]] || die "run: scripts/staging.sh init <suffix>"
  s3_need_split_keys  # every rollback ends with up, which refuses them (after the tags moved)
  rel_lock
  # The typed confirmation comes from our own stdin: no docker command below may read it
  exec 8<&0 0</dev/null
  t0=$SECONDS
  REL_RB_STAGE=0
  running="$(release_tag_of "$(running_backend_id)")"
  plan="$(mktemp "$REL_DIR/.plan.XXXXXX")"
  REL_RB_PLAN="$plan"
  trap 'rel_rollback_exit $?' EXIT
  # A window's holds with no release in progress to explain them: not a rollback's to guess (D2)
  if [[ ! -f "$REL_ATTEMPT" ]]; then
    held="$(rel_held_count)"
    [[ ! "$held" =~ ^[1-9][0-9]*$ ]] || die "$held hospital(s) are held for a window, but no release is in progress (releases/attempt): nothing here says what held them. Serve them with scripts/staging.sh fleet release --operator $op and clear the window (fleet window --clear --operator $op), or find the release that held them"
  fi
  if ((restore)); then rlog plan "$REL_LOG" "$REL_ATTEMPT" "$running" --restore > "$plan" || exit 1
  else rlog plan "$REL_LOG" "$REL_ATTEMPT" "$running" > "$plan" || exit 1; fi
  leave="$(sed -n 's/^leave=//p' "$plan")"; target="$(sed -n 's/^target=//p' "$plan")"
  REL_RB_TARGET="$target"
  [[ -n "$target" && "$leave" != "$target" ]] || die "refused: $leave would be rolled back to itself"
  rel_roles_guard "$target"
  echo "== Rolling back from $leave ($(sed -n 's/^leave_note=//p' "$plan")) to $target"
  # Every image of the target is here before anything changes, never found missing half-way (D5).
  # All of them, Postgres and Keycloak too, with or without a restore: deploy.sh already moved this
  # stack's :staging tags to the build being left, so a tag left alone would make `up` recreate the
  # database or Keycloak on that build's image (D2/O3/D7). Without a restore, release_log.py plan
  # allowed only a database that already runs on the target's Postgres and Keycloak (the left
  # build never ran on it: migrations=none, or the same images)
  names="backend frontend postgres keycloak acmedns grafana"
  ((restore == 0)) || [[ -n "$(env_get IHR_BACKUP)" ]] || die "no backups set up here"
  rel_check_images "$names" "$plan"
  if [[ ! -f "$REL_DIR/stack-$target.tgz" ]] && ! git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    echo "WARNING: no stack files kept for $target: the current ones stay" >&2
  fi
  if ((restore == 0)); then
    # Same schemas, Keycloak and Postgres, or a release that stopped before its restore point
    # (release_log.py plan checked): every tag back at the target's image, the database kept
    REL_RB_STAGE=1
    rel_point_images "$names" "$plan"
    rel_unpack_stack "$target"
    "$ROOT/scripts/staging.sh" up
    # The window's holds lifted (the schemas are the target's), any hospital whose role sync this
    # build refuses held again, and the window cleared (D7)
    "$ROOT/scripts/staging.sh" fleet release --operator "$op"
    "$ROOT/scripts/staging.sh" fleet roles --operator "$op" || true
    "$ROOT/scripts/staging.sh" fleet window --clear --operator "$op"
    "$ROOT/scripts/staging.sh" smoke || die "rolled back to $target, but the smoke checks fail (above)"
    (umask 077; rlog line kind=rollback "tag=$target" "from=$leave" restored=no "time=$(date -Iseconds)" >> "$REL_LOG")
    rm -f "$REL_ATTEMPT"
    echo "== Rolled back to $target in $((SECONDS - t0)) s (nothing lost)"
    return 0
  fi
  rp="$(sed -n 's/^restore_point=//p' "$plan")"; rp_time="$(sed -n 's/^rp_time=//p' "$plan")"
  lsn="$(sed -n 's/^lsn=//p' "$plan")"; rp_of="$(sed -n 's/^rp_of=//p' "$plan")"
  REL_RB_RP="$rp"
  [[ "$rp_time" =~ ^[0-9]+$ ]] || die "the restore point $rp has no time in the log"
  backup_config
  set="$(rel_backup_set "$rp_time")" || die "cannot roll back to $rp"
  REL_RB_SET="$set"
  # The copy beside it: the volume's real size (pg_wal too), kept only with room for it and the
  # 20 GB floor the restore, the journal and the log store still need (D6/O10)
  need="$(rel_db_used_mb)"
  free="$(df -Pm "$(docker info --format '{{.DockerRootDir}}')" | awk 'NR==2 {print $4}')"
  [[ "$need" =~ ^[0-9]+$ && "$free" =~ ^[0-9]+$ ]] || die "could not read the database volume's size or the free disk"
  floor=$(( ${IHR_DEPLOY_MIN_FREE_GB:-20} * 1024 ))
  margin=$(( need / 5 > floor ? need / 5 : floor ))
  if ((free > need + margin)); then
    keep_copy=1
    aside="${project}_pgdata-pre-rollback-$leave-$(date +%Y%m%d%H%M%S)"
    ! docker volume inspect "$aside" >/dev/null 2>&1 || die "volume $aside already exists: nothing changed"
  fi
  # The only database is never replaced before the restore is shown possible: a backup before the
  # point (above) and the WAL up to it in the repository (O10)
  if ! rel_wal_archived "$lsn"; then
    ((keep_copy)) || die "cannot show that the repository holds the WAL up to $rp (lsn $lsn), and there is no room to keep the current database beside it: refused, nothing changed (scripts/staging.sh backup-status)"
    echo "WARNING: cannot show that the repository holds the WAL up to $rp (lsn $lsn); the current database is kept beside it" >&2
  fi
  echo "== What this rollback does and loses:"
  echo "- every hospital is offline until it ends (the stack stops; the restore replays WAL from backup $set);"
  echo "- the database (every hospital, the platform and Keycloak) goes back to restore point $rp,"
  echo "  taken at $(date -d "@$rp_time" -Iseconds) for release $rp_of, with every hospital held at the start of its window:"
  echo "  everything written after it is lost (nothing, if no hospital was served since);"
  echo "- Keycloak's changes since then are lost too: invitations, password and authenticator resets,"
  echo "  accounts disabled or removed (re-do them; the realm sync runs at the start);"
  echo "- the audit trail goes back too: fingerprints stored after it no longer match it, and the audit watch"
  echo "  reports every hospital sealed since as cut or rewritten (AuditChainBroken, AuditFingerprintMismatch,"
  echo "  critical) until restore events for this rollback exist (P1-T22b; nothing writes them yet). Record it as"
  echo "  an incident, never a silence; a checkpoint is never reset by hand;"
  if ((keep_copy)); then echo "- the current database is kept beside it (volume $aside, about $need MB)."
  else echo "- the current database is REPLACED: not enough disk to keep a copy ($free MB free; $need MB plus a ${floor} MB floor needed)."; fi
  echo "Type the release tag to roll back to ($target) to go on:"
  read -r answer <&8 || true
  [[ "$answer" == "$target" ]] || die "not confirmed: nothing changed"
  echo "== [$((SECONDS - t0)) s] Stopping the stack (its volumes stay: never down -v)"
  REL_RB_STAGE=2
  compose down
  if ((keep_copy)); then
    echo "== [$((SECONDS - t0)) s] Keeping the current database in $aside"
    docker volume create --label "ihr.rollback-from=$leave" "$aside" >/dev/null
    if ! docker run --rm --network none --user 0 -v "${project}_pgdata:/from:ro" -v "$aside:/to" \
      --entrypoint sh "$(env_get IHR_POSTGRES_IMAGE)" -c 'cp -a /from/. /to/ && touch /to/.ihr-rollback-copy-complete'; then
      docker volume rm "$aside" >/dev/null 2>&1 || true
      die "the copy of the current database failed (the partial copy was removed); nothing was restored"
    fi
    REL_RB_ASIDE="$aside"
  fi
  echo "== [$((SECONDS - t0)) s] Restoring to $rp (with $target's Postgres image)"
  REL_RB_STAGE=3
  rel_point_images "postgres" "$plan"
  restore_live --target-name "$rp" --set "$set" --replace-database
  trap 'rel_rollback_exit $?' EXIT  # restore_live set its own
  # The database is the target's now: logged at once (a failure below is not a rollback to repeat)
  REL_RB_STAGE=4
  (umask 077; rlog line kind=rollback "tag=$target" "from=$leave" "restored=$rp" "time=$(date -Iseconds)" >> "$REL_LOG")
  rm -f "$REL_ATTEMPT"
  rel_point_images "backend frontend keycloak acmedns grafana" "$plan"
  rel_unpack_stack "$target"
  echo "== [$((SECONDS - t0)) s] Starting $target"
  "$ROOT/scripts/staging.sh" up
  # The restore point was taken with every hospital held for the window: serve them again, then
  # hold again any whose role sync this build refuses
  "$ROOT/scripts/staging.sh" fleet release --operator "$op"
  "$ROOT/scripts/staging.sh" fleet roles --operator "$op" || true
  "$ROOT/scripts/staging.sh" fleet window --clear --operator "$op"
  "$ROOT/scripts/staging.sh" smoke || die "restored and started $target, but the smoke checks fail (above)"
  echo "== Rolled back to $target with a restore in $((SECONDS - t0)) s"
  # A full backup of the new timeline at once: a later restore never has to reach back past this
  # one, or past an older point (D4/O9)
  echo "== Full backup of the restored database"
  "$ROOT/scripts/staging.sh" backup-now full || echo "WARNING: the full backup failed (above): run scripts/staging.sh backup-now full" >&2
  [[ -z "$aside" ]] || echo "The database as it was is in volume $aside: once you are sure, docker volume rm $aside"
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

# CARE's two identities (P1-T20b): ihr-care-files (patient files) and ihr-care-pictures (staff
# photos, facility covers), one key each; they replace the combined ihr-care
S3_CARE_IDS="FILES PICTURES"
S3_APP_VARS='BUCKET_KEY|BUCKET_SECRET|BUCKET_ENDPOINT|BUCKET_EXTERNAL_ENDPOINT|FILE_UPLOAD_KEY|FILE_UPLOAD_SECRET|FACILITY_S3_KEY|FACILITY_S3_SECRET'

s3_care_env() {  # s3_care_env <files key> <files secret> <pictures key> <pictures secret>
  echo "# Patient files and pictures, one key each (P1-T20b; secure.py refuses one shared key)"
  printf 'FILE_UPLOAD_KEY=%s\nFILE_UPLOAD_SECRET=%s\nFACILITY_S3_KEY=%s\nFACILITY_S3_SECRET=%s\n' "$@"
}

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
  for k in ADMIN $S3_CARE_IDS ANCHOR_WRITE ANCHOR_READ BACKUP LOGS_ARCHIVE; do key[${k}_KEY]="ihr$(hex 10)"; key[${k}_SECRET]="$(hex 20)"; done
  local SECRETS_TARGET="$sec"
  {
    echo "# SeaweedFS identities (P1-T8b3), written by scripts/staging.sh s3-init; the s3 container only."
    for k in ADMIN $S3_CARE_IDS ANCHOR_WRITE ANCHOR_READ BACKUP LOGS_ARCHIVE; do
      echo "IHR_S3_${k}_KEY=${key[${k}_KEY]}"
      echo "IHR_S3_${k}_SECRET=${key[${k}_SECRET]}"
    done
  } | write_secret s3-gateway.env
  # CARE: one key for patient files, one for pictures (P1-T20b), the internal address, and the
  # public one for signed links
  tmp="$(grep -Ev "^($S3_APP_VARS)=" "$sec/app.env"
    echo "# Object storage on this server (P1-T8b3, scripts/staging.sh s3-init)"
    echo "BUCKET_ENDPOINT=http://s3:8333"
    echo "BUCKET_EXTERNAL_ENDPOINT=https://files.$suffix"
    s3_care_env "${key[FILES_KEY]}" "${key[FILES_SECRET]}" "${key[PICTURES_KEY]}" "${key[PICTURES_SECRET]}")"
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
  for k in ADMIN $S3_CARE_IDS ANCHOR_WRITE ANCHOR_READ LOGS_ARCHIVE; do
    for v in KEY SECRET; do
      export "S3_${k}_${v}=$(sed -n "s/^IHR_S3_${k}_${v}=//p" "$SECRETS/s3-gateway.env")"
      envs+=(-e "S3_${k}_${v}")
    done
  done
  if [[ -f "$SECRETS/s3-retired.env" ]]; then  # the retired combined key: s3-check proves it dead
    for v in KEY SECRET; do
      export "S3_RETIRED_CARE_${v}=$(sed -n "s/^IHR_S3_RETIRED_CARE_${v}=//p" "$SECRETS/s3-retired.env")"
      envs+=(-e "S3_RETIRED_CARE_${v}")
    done
  fi
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

s3_split_care_key() {  # s3_split_care_key <secrets dir>: P1-T20b, once. A site set up before
  # it has one CARE key for both buckets (identity ihr-care, BUCKET_KEY in app.env). Two new keys
  # replace it; the old one leaves the gateway's file (the gateway forgets it when s3 is next
  # recreated) and app.env, and is kept in s3-retired.env only so s3-check can prove it dead.
  # Made where the secrets are kept: the deploying machine (s3-split-keys), or a local proof's
  # own folder (up), never a server (S1/S2: deploy.sh would send the unsplit copy over it again).
  # Idempotent, also after an interruption (each file is finished from the gateway's keys); prints
  # file names only
  local sec="$1" gw="$1/s3-gateway.env" app="$1/app.env" k fk pk changed=()
  local SECRETS_TARGET="$sec"
  [[ -f "$gw" && -f "$app" ]] || die "no $gw / $app: run scripts/staging.sh s3-init first"
  hex() { python3 -c "import secrets;print(secrets.token_hex($1))"; }
  if grep -q '^IHR_S3_CARE_KEY=' "$gw"; then
    { echo "# The combined CARE key retired by P1-T20b (dead: s3-check proves it). Never commit."
      sed -n 's/^IHR_S3_CARE_\(KEY\|SECRET\)=/IHR_S3_RETIRED_CARE_\1=/p' "$gw"; } | write_secret s3-retired.env.new
    mv "$sec/s3-retired.env.new" "$sec/s3-retired.env"
    changed+=(s3-retired.env)
  fi
  if grep -q '^IHR_S3_CARE_\(KEY\|SECRET\)=' "$gw" || ! grep -q '^IHR_S3_FILES_KEY=' "$gw" \
      || ! grep -q '^IHR_S3_PICTURES_KEY=' "$gw"; then
    { grep -v '^IHR_S3_CARE_\(KEY\|SECRET\)=' "$gw"
      for k in $S3_CARE_IDS; do
        grep -q "^IHR_S3_${k}_KEY=" "$gw" || printf 'IHR_S3_%s_KEY=ihr%s\nIHR_S3_%s_SECRET=%s\n' "$k" "$(hex 10)" "$k" "$(hex 20)"
      done
    } | write_secret s3-gateway.env.new
    mv "$sec/s3-gateway.env.new" "$gw"
    changed+=(s3-gateway.env)
  fi
  s3g() { sed -n "s/^IHR_S3_$1=//p" "$gw" | tail -1; }
  fk="$(s3g FILES_KEY)" pk="$(s3g PICTURES_KEY)"
  # app.env carries exactly the gateway's two keys (compared, never printed), and no BUCKET_KEY
  if grep -Eq '^BUCKET_(KEY|SECRET)=' "$app" || ! grep -qxF "FILE_UPLOAD_KEY=$fk" "$app" \
      || ! grep -qxF "FILE_UPLOAD_SECRET=$(s3g FILES_SECRET)" "$app" || ! grep -qxF "FACILITY_S3_KEY=$pk" "$app" \
      || ! grep -qxF "FACILITY_S3_SECRET=$(s3g PICTURES_SECRET)" "$app"; then
    { grep -Ev "^(BUCKET_KEY|BUCKET_SECRET|FILE_UPLOAD_KEY|FILE_UPLOAD_SECRET|FACILITY_S3_KEY|FACILITY_S3_SECRET)=" "$app" \
        | grep -v '^# Patient files and pictures, one key each (P1-T20b' || true
      s3_care_env "$fk" "$(s3g FILES_SECRET)" "$pk" "$(s3g PICTURES_SECRET)"
    } | write_secret app.env.new
    mv "$sec/app.env.new" "$app"
    changed+=(app.env)
  fi
  if ((${#changed[@]})); then
    echo "Object storage: CARE's key split into a patient files key and a pictures key (P1-T20b): wrote ${changed[*]} in $sec (mode 600)."
  fi
}

s3_keys_problem() {  # s3_keys_problem <folder with .env and secrets/>: why CARE's storage keys are not
  # P1-T20b's (one key for patient files, one for pictures; secure.py refuses anything else), or
  # nothing. Role-based access (no keys) and a site without object storage have nothing to check
  local d="$1" app="$1/secrets/app.env" gw="$1/secrets/s3-gateway.env"
  if [[ "$(sed -n 's/^IHR_S3=//p' "$d/.env" 2>/dev/null | tail -1)" == local ]]; then
    [[ -f "$gw" ]] || { echo "no secrets/s3-gateway.env (scripts/staging.sh s3-init)"; return 0; }
    if grep -q '^IHR_S3_CARE_\(KEY\|SECRET\)=' "$gw"; then
      echo "secrets/s3-gateway.env still holds the combined CARE key (IHR_S3_CARE_KEY)"; return 0
    fi
    if ! grep -q '^IHR_S3_FILES_KEY=' "$gw" || ! grep -q '^IHR_S3_PICTURES_KEY=' "$gw"; then
      echo "secrets/s3-gateway.env lacks IHR_S3_FILES_KEY or IHR_S3_PICTURES_KEY"; return 0
    fi
  elif ! grep -q '^BUCKET_ENDPOINT=' "$app" 2>/dev/null; then
    return 0
  fi
  grep -q '^BUCKET_PROVIDER=AWS_ROLE_BASED$' "$app" 2>/dev/null && return 0
  if grep -Eq '^BUCKET_(KEY|SECRET)=' "$app" 2>/dev/null; then
    echo "secrets/app.env still holds BUCKET_KEY / BUCKET_SECRET (one key for both buckets)"; return 0
  fi
  if ! grep -q '^FILE_UPLOAD_KEY=.' "$app" 2>/dev/null || ! grep -q '^FACILITY_S3_KEY=.' "$app" 2>/dev/null; then
    echo "secrets/app.env lacks FILE_UPLOAD_KEY or FACILITY_S3_KEY"
  fi
}

s3_need_split_keys() {  # s3_need_split_keys [folder]: refuses, before anything changes, CARE storage
  # keys that are not P1-T20b's (S1: else the new build's migrate one-shot stops a release with
  # every hospital held)
  local d="${1:-$DIR}" why
  why="$(s3_keys_problem "$d")"
  [[ -n "$why" ]] || return 0
  if [[ "$(sed -n 's/^IHR_S3=//p' "$d/.env" 2>/dev/null | tail -1)" == local ]]; then
    die "$why: CARE needs one key for patient files and one for pictures (P1-T20b). On the deploying machine run IHR_STAGING_OUT=<the folder scripts/deploy.sh sends> scripts/staging.sh s3-split-keys, then deploy (scripts/deploy.sh sends them); deploy/staging/README.md, Object storage. Nothing was changed"
  fi
  die "$why: CARE needs FILE_UPLOAD_KEY/_SECRET (patient files bucket only) and FACILITY_S3_KEY/_SECRET (pictures bucket only), never BUCKET_KEY (P1-T20b; deploy/staging/README.md, Moving to a provider). Nothing was changed"
}

s3_split_keys_cmd() {  # s3-split-keys [--check]: CARE's split storage keys (P1-T20b) in $OUT, on the
  # deploying machine like db-split-secrets (IHR_STAGING_OUT: elsewhere); --check only says
  # whether they are split (scripts/deploy.sh runs it before sending anything)
  [[ -f "$OUT/.env" && -d "$OUT/secrets" ]] || die "no $OUT/.env / secrets: run scripts/staging.sh init <suffix> first"
  case "${1:-}" in
    --check) s3_need_split_keys "$OUT"; return 0 ;;
    "") ;;
    *) die "usage: scripts/staging.sh s3-split-keys [--check]" ;;
  esac
  if [[ "$(sed -n 's/^IHR_S3=//p' "$OUT/.env" | tail -1)" != local ]]; then
    s3_need_split_keys "$OUT"
    echo "No SeaweedFS on this site (IHR_S3): nothing to split."
    return 0
  fi
  local before after
  before="$(cat "$OUT/secrets/s3-gateway.env" "$OUT/secrets/app.env" 2>/dev/null | sha256sum)"
  s3_split_care_key "$OUT/secrets"
  s3_need_split_keys "$OUT"
  after="$(cat "$OUT/secrets/s3-gateway.env" "$OUT/secrets/app.env" | sha256sum)"
  if [[ "$before" == "$after" ]]; then
    echo "CARE's storage keys are split already: nothing changed."
  else
    echo "Next: scripts/deploy.sh --host ... (it sends them; the server's gateway forgets the old key when s3 is recreated, and scripts/staging.sh s3-check proves it dead)."
  fi
}

s3_keys_current() {  # the gateway's key files as this start.sh needs them, before any start of s3
  s3_archive_key
  # A local proof's folder is where its secrets are made: split there. A server never makes
  # CARE's keys (S2): it refuses unsplit ones, which the deploying machine splits
  [[ ! -f "$DIR/.local" ]] || s3_split_care_key "$SECRETS"
  s3_need_split_keys
}

s3_up() {  # SeaweedFS, then its buckets (idempotent)
  s3_keys_current
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
MON_ONESHOTS='migrate|admin|keytool|s3-tool|traefik-data|alertmanager-data|logs-data|grafana-data|audit-watch-data'

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
  # The audit watch starts with the monitoring and needs the fingerprint bucket's reader key (C4)
  grep -q '^IHR_AUDIT_ANCHOR_READ_KEY=.' "$sec/anchor-read.env" 2>/dev/null \
    || echo "WARNING: no fingerprint storage yet (secrets/anchor-read.env is empty): up refuses to start the monitoring without it. Run scripts/staging.sh s3-init, or fill secrets/anchor-read.env for a provider bucket." >&2
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
  # The audit watch (P1-T21): always with the monitoring
  json_targets "$d/targets/audit-watch.json" "audit-watch:9108" "service=audit-watch"
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
             victorialogs fluent-bit vmalert logs-reader grafana audit-watch) out s
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
  # vmalert has every group of log alerts (one per "  - name:" in deploy/logs/alerts), and they run
  check "logs: vmalert runs the log alerts without errors" "$(cat "$LOGS_DIR"/alerts/*.yml | grep -c '^  - name:') groups, no errors" \
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
  # The fleet runner in the one-shot's (logged) container, never the unlogged admin one: its
  # "ihr-fleet:" lines must reach the HospitalNotServed alert (deploy/logs/alerts/fleet.yml, S3).
  # `fleet window` runs as the running backend's build when one runs: the build the database's
  # schema is (after deploy.sh moved the image tags, a one-off of the tag would be the new build on
  # the old schema; D10), as the owner (old_manage: after the move of the roles, P1-T19b, a
  # one-off of the running backend's image in this container)
  # Before the move of the roles (P1-T19b) the owner's one-off cannot log in (ihr_owner does not
  # exist yet): release and hold then run in the running backend too, as ihr (review C1)
  fleet)
    if [[ -n "$(compose ps -q backend 2>/dev/null || true)" ]] \
      && { [[ "${2:-}" == window ]] || { [[ "${2:-}" =~ ^(release|hold)$ ]] && { rel_db_single || rel_outgoing_unmigrated; }; }; }; then
      old_manage ihr_fleet_run "${@:2}"
    elif rel_db_single; then
      die "the database still has its single role ihr (the move has not run): only fleet window, release and hold run before it, in the running backend ($([[ -n "$(compose ps -q backend 2>/dev/null || true)" ]] && echo "it runs" || echo "none runs: $REL_SERVE_PREVIOUS <you> starts the build that ran"))"
    else
      compose run --rm --no-deps -T migrate python manage.py ihr_fleet_run "${@:2}"
    fi ;;
  release) release "${@:2}" ;;
  rollback) rollback "${@:2}" ;;
  releases) rlog state "$REL_LOG" "$REL_ATTEMPT" ;;
  # Internal steps of scripts/deploy.sh (a plain start of a release without migrations)
  release-note) rel_lock; release_note ;;
  release-record) rel_lock; release_record ;;
  db-split-secrets) db_split_secrets "${@:2}" ;;
  watch-reader)  # --check: the audit watch's reader key where monitoring is on ($OUT; deploy.sh, C4)
    [[ "${2:-}" == --check ]] || die "usage: scripts/staging.sh watch-reader --check"
    watch_need_reader "$OUT"
    echo "The audit watch can start: monitoring is off, or secrets/anchor-read.env holds its reader key." ;;
  db-roles) db_roles_cmd "${@:2}" ;;
  # The existing containers of app services started again as they were (docker start: Compose's
  # start would start the migrate dependency, which has no container here, and fail)
  start-existing) rel_lock; start_existing "${@:2}" ;;  # never during a release or rollback
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
  s3-split-keys) s3_split_keys_cmd "${@:2}" ;;
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
  *) sed -n '5,82p' "$0"; exit 1 ;;
esac
