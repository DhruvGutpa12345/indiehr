#!/usr/bin/env bash
# Deploy the staging stack to a bootstrapped server over SSH (P1-T8b1, ADR-011).
# Provider-neutral and registry-free: our images (and acme-dns when .env has IHR_ACME) travel as `docker save | ssh docker load`,
# the pinned third-party images are pulled by the server itself.
#
#   scripts/deploy.sh --host ihr@<address> [--port 22] [--env-dir deploy/staging] [--allow-dirty]
#                     [--identity <key>] [--no-build | --build-only] [--no-start]
#   --no-start: everything but starting the stack (a new server that is to be restored first:
#               scripts/staging.sh restore, then up; deploy/staging/README.md)
#   scripts/deploy.sh --local-proof [...]   the throwaway VM of scripts/local-vm.sh:
#                     ihr@127.0.0.1:2222, its own key and .env/secrets under ~/.cache/indiehr/vm
#
# The server must have been set up by deploy/host/bootstrap.sh (user ihr, Docker, /srv/indiehr).
# Layout on the server mirrors the repository, because compose.yaml uses ../keycloak and
# ../docker: /srv/indiehr/{deploy/staging,deploy/keycloak,deploy/docker,deploy/monitoring,
# deploy/logs,scripts/{staging,client-keys}.sh}. The monitoring and log images (P1-T9a, P1-T9b)
# are pulled by the server.
# Secrets (.env and secrets/ from `scripts/staging.sh init`) go over SSH as a tar stream, mode
# 600, never in arguments or logs. The backend image is built from the committed files only
# (git archive; with --allow-dirty, tracked + new files), never from git-ignored ones (local
# keys, .env files). Images carry their commit as labels; /srv/indiehr/DEPLOYED records the
# commit read from the images, and /srv/indiehr/DEPLOYING marks a deploy that did not finish.
set -euo pipefail
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$ROOT"
REMOTE_ROOT=/srv/indiehr
VM_CACHE="${IHR_VM_CACHE:-$HOME/.cache/indiehr/vm}"
PROOF_SUFFIX="app.ihr-proof.in"

HOST="" PORT=22 ENV_DIR="" ALLOW_DIRTY=0 IDENTITY="" BUILD=1 BUILD_ONLY=0 LOCAL_PROOF=0 NO_START=0 KNOWN_HOSTS=""
usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }
while (($#)); do
  case "$1" in
    --host) HOST="${2:?--host user@address}"; shift ;;
    --port) PORT="${2:?--port N}"; shift ;;
    --env|--env-dir) ENV_DIR="${2:?--env-dir DIR}"; shift ;;
    --identity) IDENTITY="${2:?--identity FILE}"; shift ;;
    --allow-dirty) ALLOW_DIRTY=1 ;;
    --no-build) BUILD=0 ;;
    --build-only) BUILD_ONLY=1 ;;
    --local-proof) LOCAL_PROOF=1 ;;
    --no-start) NO_START=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done
if ((LOCAL_PROOF)); then
  HOST="${HOST:-ihr@127.0.0.1}"
  PORT="${IHR_VM_SSH_PORT:-$([[ "$PORT" == 22 ]] && echo 2222 || echo "$PORT")}"
  ENV_DIR="${ENV_DIR:-$VM_CACHE/staging}"
  IDENTITY="${IDENTITY:-$VM_CACHE/id_ed25519}"
  KNOWN_HOSTS="$VM_CACHE/known_hosts"
fi
ENV_DIR="${ENV_DIR:-deploy/staging}"
((BUILD_ONLY)) || [[ -n "$HOST" ]] || { usage >&2; exit 2; }
[[ "$PORT" =~ ^[0-9]+$ ]] || { echo "--port must be a number" >&2; exit 2; }

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
CTX="" CM_DIR="" SSH=() HOST_CONNECTED=0
cleanup() {
  if ((HOST_CONNECTED)); then "${SSH[@]}" -O exit "$HOST" >/dev/null 2>&1 || true; fi
  [[ -z "$CM_DIR" ]] || rm -rf "$CM_DIR"
  [[ -z "$CTX" ]] || rm -rf "$CTX"
}
trap cleanup EXIT

# ---- The commit being deployed ---------------------------------------------------------
SHA="$(git rev-parse HEAD)"
DIRTY=no
if [[ -n "$(git status --porcelain)" ]]; then
  ((ALLOW_DIRTY)) || die "the git tree has uncommitted changes; commit them or pass --allow-dirty"
  DIRTY=yes
fi

# ---- Site settings and secrets ---------------------------------------------------------
env_get() { sed -n "s/^$1=//p" "$ENV_DIR/.env" | tail -1; }

# ---- 1. Images (built here; the web app from the CI bundle, never built locally) -----------
backend_image() { [[ -f "$ENV_DIR/.env" ]] && env_get IHR_BACKEND_IMAGE || echo "indiehr-backend:staging"; }
frontend_image() { [[ -f "$ENV_DIR/.env" ]] && env_get IHR_FRONTEND_IMAGE || echo "indiehr-frontend:staging"; }
postgres_image() { local v=""; [[ -f "$ENV_DIR/.env" ]] && v="$(env_get IHR_POSTGRES_IMAGE)"; echo "${v:-indiehr-postgres:staging}"; }
keycloak_image() { local v=""; [[ -f "$ENV_DIR/.env" ]] && v="$(env_get IHR_KEYCLOAK_IMAGE)"; echo "${v:-indiehr-keycloak:staging}"; }
BACKEND="$(backend_image)"
FRONTEND="$(frontend_image)"
POSTGRES="$(postgres_image)"
KEYCLOAK="$(keycloak_image)"
REV_LABEL="org.opencontainers.image.revision"
if ((BUILD)); then
  # Build context: a fresh copy of the committed files, so git-ignored files in the working
  # tree (apps/backend/jwks.b64.txt, .envrc, .env files, dumps) never reach the image.
  CTX="$(mktemp -d "${TMPDIR:-/tmp}/ihr-build.XXXXXX")"
  chmod 700 "$CTX"
  if [[ "$DIRTY" == no ]]; then
    git archive --format=tar HEAD apps/backend plugins | tar -x -C "$CTX"
  else  # tracked and new (not ignored) files as they are in the working tree
    git ls-files -z --cached --others --exclude-standard -- apps/backend plugins \
      | while IFS= read -r -d '' f; do if [[ -e "$f" || -L "$f" ]]; then printf '%s\0' "$f"; fi; done \
      | tar --null -T - -cf - | tar -x -C "$CTX"
  fi
  log "Building $BACKEND (backend + plugins/, commit ${SHA:0:12}, dirty: $DIRTY)"
  docker buildx build --load --build-context plugins="$CTX/plugins" \
    --label "$REV_LABEL=$SHA" --label "ihr.git-dirty=$DIRTY" \
    -f deploy/docker/backend.Dockerfile -t "$BACKEND" "$CTX/apps/backend"
  BUNDLE="deploy/dev/frontend-build"
  [[ -f "$BUNDLE/index.html" ]] || die "no web app bundle in $BUNDLE (docs/LOCAL_DEV.md: Get a fresh web app bundle)"
  scripts/check-bundle-egress.sh "$BUNDLE"
  bundle_id="$(sed -n 's/.*"version":"\([^"]*\)".*"built_at":"\([^"]*\)".*/\1 \2/p' "$BUNDLE/build-meta.json" 2>/dev/null || true)"
  log "Packing $FRONTEND from the CI bundle in $BUNDLE (${bundle_id:-no build-meta.json})"
  docker buildx build -q --load -f deploy/docker/frontend.Dockerfile \
    --label "$REV_LABEL=$SHA" --label "ihr.git-dirty=$DIRTY" --label "ihr.bundle=${bundle_id:-unknown}" \
    --build-context config=deploy/docker -t "$FRONTEND" "$BUNDLE" >/dev/null
  # Postgres with pgBackRest (P1-T8c): pinned base image and pgBackRest source
  log "Building $POSTGRES (deploy/docker/postgres.Dockerfile: postgres 18.6 + pgBackRest)"
  docker buildx build -q --load -f deploy/docker/postgres.Dockerfile \
    --label "$REV_LABEL=$SHA" --label "ihr.git-dirty=$DIRTY" -t "$POSTGRES" deploy/docker >/dev/null
  # Keycloak pre-built for `start --optimized` (P1-T8d1): pinned upstream image, our build options
  log "Building $KEYCLOAK (deploy/docker/keycloak.Dockerfile: Keycloak 26.8.2, optimized)"
  docker buildx build -q --load -f deploy/docker/keycloak.Dockerfile \
    --label "$REV_LABEL=$SHA" --label "ihr.git-dirty=$DIRTY" -t "$KEYCLOAK" deploy/docker >/dev/null
fi
label_of() {  # label_of <image> <label>: the label's value, or "unknown"
  local v; v="$(docker image inspect -f "{{index .Config.Labels \"$2\"}}" "$1" 2>/dev/null || true)"
  [[ -n "$v" && "$v" != "<no value>" ]] && echo "$v" || echo unknown
}
if ((LOCAL_PROOF)) && [[ ! -f "$ENV_DIR/.env" ]]; then
  # The VM's own settings and secrets, kept outside the repository (init needs the backend
  # image here: it makes the token-signing keys with it)
  log "No settings for the VM yet: scripts/staging.sh init $PROOF_SUFFIX --vm (into $ENV_DIR)"
  IHR_STAGING_OUT="$ENV_DIR" IHR_BACKEND_IMAGE="$BACKEND" IHR_FRONTEND_IMAGE="$FRONTEND" \
    scripts/staging.sh init "$PROOF_SUFFIX" --vm
fi
# acme-dns (P1-T8b2), only when the site uses the wildcard certificate (staging.sh acme-init)
ACMEDNS=""
[[ -f "$ENV_DIR/.env" && -n "$(env_get IHR_ACME)" ]] && ACMEDNS="$(env_get IHR_ACMEDNS_IMAGE)"
if ((BUILD)) && [[ -n "$ACMEDNS" ]]; then
  log "Building $ACMEDNS (deploy/docker/acme-dns.Dockerfile: pinned source, Go and base image)"
  docker buildx build -q --load -f deploy/docker/acme-dns.Dockerfile \
    --label "$REV_LABEL=$SHA" --label "ihr.git-dirty=$DIRTY" -t "$ACMEDNS" deploy/docker >/dev/null
fi
((BUILD_ONLY)) && { log "Images built: $BACKEND, $FRONTEND, $POSTGRES, $KEYCLOAK${ACMEDNS:+, $ACMEDNS}"; exit 0; }

[[ -f "$ENV_DIR/.env" && -d "$ENV_DIR/secrets" ]] \
  || die "no $ENV_DIR/.env and secrets/: run scripts/staging.sh init <suffix> first"
[[ -f "$ENV_DIR/.local" ]] && die "$ENV_DIR was made for a proof on this machine (init --local); use init without --local, or --local-proof"
for img in "$BACKEND" "$FRONTEND" "$POSTGRES" "$KEYCLOAK" ${ACMEDNS:+"$ACMEDNS"}; do
  docker image inspect "$img" >/dev/null 2>&1 || die "image $img not found here (drop --no-build)"
  # With --no-build the images may come from another commit: DEPLOYED must not claim HEAD
  rev="$(label_of "$img" "$REV_LABEL")"
  if [[ "$rev" != "$SHA" ]] && ! ((ALLOW_DIRTY)); then
    die "image $img was built from ${rev:0:12}, HEAD is ${SHA:0:12}: rebuild (drop --no-build) or pass --allow-dirty"
  fi
done

# ---- SSH: one shared connection (ufw allows 6 new SSH connections per 30 s per address) ----
CM_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ihr-deploy.XXXXXX")"
SSH=(ssh -p "$PORT" -o BatchMode=yes -o ConnectTimeout=15 -o ControlMaster=auto
     -o "ControlPath=$CM_DIR/cm" -o ControlPersist=300 -o ServerAliveInterval=30)
[[ -n "$IDENTITY" ]] && SSH+=(-i "$IDENTITY" -o IdentitiesOnly=yes)
# A real server's host key must already be verified and in known_hosts (README step 1): set
# here because the command line beats a permissive "StrictHostKeyChecking accept-new|no" in
# ~/.ssh/config. Secrets go over this connection. Only the throwaway VM accepts a new key.
if [[ -n "$KNOWN_HOSTS" ]]; then
  SSH+=(-o "UserKnownHostsFile=$KNOWN_HOSTS" -o StrictHostKeyChecking=accept-new)
else
  SSH+=(-o StrictHostKeyChecking=yes)
fi
HOST_CONNECTED=1
remote() { "${SSH[@]}" "$HOST" "$@"; }

log "Checking $HOST (port $PORT)"
remote "test -w $REMOTE_ROOT && docker compose version --short >/dev/null" \
  || die "$HOST: $REMOTE_ROOT not writable or Docker missing (run deploy/host/bootstrap.sh first)"

# Marks the attempt on the server first: if anything below fails, DEPLOYING stays and says so
# (the server may then run some new files or images while DEPLOYED names the previous deploy).
remote "printf 'git_sha=%s\nstarted_at=%s\nstatus=in progress; if this file remains, the deploy failed after images or files were replaced (DEPLOYED is the last finished deploy)\n' \
  '$(label_of "$BACKEND" "$REV_LABEL")' '$(date -Iseconds)' > $REMOTE_ROOT/DEPLOYING"

# ---- 2. Our images, only when the server does not have this exact build yet -------------
ship_image() {  # ship_image <image>
  local img="$1" id marker
  id="$(docker image inspect -f '{{.Id}}' "$img")"
  marker="$REMOTE_ROOT/images/$(printf '%s' "$img" | tr -c 'a-zA-Z0-9._-' '_')"
  if [[ "$(remote "cat '$marker' 2>/dev/null && docker image inspect -f ok '$img' >/dev/null 2>&1 && echo present" || true)" \
        == "$id"$'\n'"present" ]]; then
    log "$img already on the server ($id)"
    return
  fi
  log "Sending $img ($id)"
  docker save "$img" | gzip -1 | remote "docker load -q"
  remote "mkdir -p $REMOTE_ROOT/images && printf '%s\n' '$id' > '$marker'"
}
ship_image "$BACKEND"
ship_image "$FRONTEND"
ship_image "$POSTGRES"
ship_image "$KEYCLOAK"
[[ -z "$ACMEDNS" ]] || ship_image "$ACMEDNS"

# ---- 3. Stack files (tracked files only: never a stray .env or secret) ----------------------
log "Sending the stack files to $REMOTE_ROOT"
git ls-files -z deploy/staging deploy/s3 deploy/monitoring deploy/logs deploy/keycloak/password-blocklist \
    deploy/docker/frontend-nginx.conf.template scripts/staging.sh scripts/client-keys.sh \
  | tar --null -T - -czf - \
  | remote "tar -C $REMOTE_ROOT --no-same-owner -xzf -"

# ---- 4. Site settings and secrets, mode 600, through the SSH connection only -----------------
log "Sending .env and secrets/ from $ENV_DIR (mode 600)"
# Not the workers' Keycloak secrets of before P1-T8d2: they sign with keys made on the server
# now, and an old copy left here must not travel again (delete it: deploy/staging/README.md)
tar -C "$ENV_DIR" --exclude=secrets/provisioner.env --exclude=secrets/staff-worker.env -cf - .env secrets \
  | remote "umask 077 && mkdir -p $REMOTE_ROOT/deploy/staging \
      && tar -C $REMOTE_ROOT/deploy/staging --no-same-owner -xf - \
      && chmod 700 $REMOTE_ROOT/deploy/staging/secrets \
      && chmod 600 $REMOTE_ROOT/deploy/staging/.env $REMOTE_ROOT/deploy/staging/secrets/*"

# ---- 5. Start and check, with the same commands as a run on this machine --------------------
if ((NO_START)); then
  log "Not starting the stack (--no-start): on the server, scripts/staging.sh restore, then up and smoke"
elif ((LOCAL_PROOF)); then
  log "Starting the stack on the VM, a synthetic hospital 'alpha', then the smoke checks"
  remote "cd $REMOTE_ROOT && scripts/staging.sh local-proof"
else
  log "Starting the stack, then the smoke checks"
  remote "cd $REMOTE_ROOT && scripts/staging.sh up && scripts/staging.sh smoke"
fi

# Images replaced by this deploy are now untagged ("dangling"): remove them, or they fill the
# disk that the journal (a 14-day buffer) and the log store share (journald keeps SystemKeepFree free by deleting logs).
log "Removing images no longer tagged on the server"
remote "docker image prune -f >/dev/null"

# ---- 6. Record what is deployed (the commit as labelled on the images, not this tree's HEAD) ----
{
  echo "git_sha=$(label_of "$BACKEND" "$REV_LABEL")"
  echo "git_dirty=$(label_of "$BACKEND" ihr.git-dirty)"
  echo "deployed_at=$(date -Iseconds)"
  echo "deployed_from_head=$SHA"
  echo "suffix=$(env_get IHR_TENANT_DOMAIN_SUFFIX)"
  echo "backend_image=$BACKEND $(docker image inspect -f '{{.Id}}' "$BACKEND")"
  echo "frontend_image=$FRONTEND $(docker image inspect -f '{{.Id}}' "$FRONTEND")"
  echo "frontend_revision=$(label_of "$FRONTEND" "$REV_LABEL") bundle=$(label_of "$FRONTEND" ihr.bundle)"
  echo "postgres_image=$POSTGRES $(docker image inspect -f '{{.Id}}' "$POSTGRES") pgbackrest=$(label_of "$POSTGRES" ihr.pgbackrest.version)"
  echo "keycloak_image=$KEYCLOAK $(docker image inspect -f '{{.Id}}' "$KEYCLOAK")"
  [[ -z "$ACMEDNS" ]] || echo "acmedns_image=$ACMEDNS $(docker image inspect -f '{{.Id}}' "$ACMEDNS") source=$(label_of "$ACMEDNS" ihr.acme-dns.commit)"
} | remote "cat > $REMOTE_ROOT/DEPLOYED.tmp && mv $REMOTE_ROOT/DEPLOYED.tmp $REMOTE_ROOT/DEPLOYED && rm -f $REMOTE_ROOT/DEPLOYING"
log "Deployed $(label_of "$BACKEND" "$REV_LABEL") (dirty: $(label_of "$BACKEND" ihr.git-dirty)) to $HOST; recorded in $REMOTE_ROOT/DEPLOYED"
