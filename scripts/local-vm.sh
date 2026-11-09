#!/usr/bin/env bash
# A throwaway Ubuntu 26.04 VM on this machine (KVM, no sudo) to prove the host kit:
# deploy/host/bootstrap.sh and scripts/deploy.sh (P1-T8b1). Nothing in it is real: synthetic
# data only, its own SSH key, files under ~/.cache/indiehr/vm (never the repository).
#
#   scripts/local-vm.sh fetch       download the pinned cloud image, check signature + SHA-256
#   scripts/local-vm.sh up          boot it (overlay disk, cloud-init seed), wait for SSH
#   scripts/local-vm.sh bootstrap   run bootstrap.sh twice (second run must change nothing), --check
#   scripts/local-vm.sh reboot      reboot, then --check again (settings survive a reboot)
#   scripts/local-vm.sh verify      SSH rules, firewall (ufw + DOCKER-USER), HTTPS from outside
#   scripts/local-vm.sh ssh [user] [command...]   shell (default user ubuntu; ihr = deploy user)
#   scripts/local-vm.sh status | down | destroy   destroy keeps the base image and the key
#
# Deploy into it: scripts/deploy.sh --local-proof. Settings: IHR_VM_MEM (MiB, 3584),
# IHR_VM_DISK (20G), IHR_VM_SSH_PORT (2222), IHR_VM_HTTP_PORT (8080), IHR_VM_HTTPS_PORT (8443),
# IHR_VM_PROBE_PORTS ("2998 2999": host ports for the firewall probes on 9998 and 9999),
# IHR_VM_DNS_PORT (5354: the VM's port 53, tcp + udp, for acme-dns; P1-T8b2).
set -euo pipefail
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
CACHE="${IHR_VM_CACHE:-$HOME/.cache/indiehr/vm}"
# Pinned cloud image (docs/VERSIONS.md): dated release directory + SHA-256, and the key
# that signs SHA256SUMS (Canonical's "UEC Image Automatic Signing Key").
IMAGE_SERIAL="20261016"
IMAGE_NAME="ubuntu-26.04-server-cloudimg-amd64.img"
IMAGE_URL_BASE="https://cloud-images.ubuntu.com/releases/resolute/release-$IMAGE_SERIAL"
IMAGE_SHA256="5645d0318ad21f643ebb1ac4567514620e9893380b07b0201fda163baa5b7eb5"
SIGNING_KEY_FPR="D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81"
SYSTEM_KEYRING="/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg"

MEM="${IHR_VM_MEM:-3584}"
DISK="${IHR_VM_DISK:-20G}"
SSH_PORT="${IHR_VM_SSH_PORT:-2222}"
HTTP_PORT="${IHR_VM_HTTP_PORT:-8080}"
HTTPS_PORT="${IHR_VM_HTTPS_PORT:-8443}"
DNS_PORT="${IHR_VM_DNS_PORT:-5354}"
read -r PROBE_HOST_PORT PROBE_DOCKER_PORT <<< "${IHR_VM_PROBE_PORTS:-2998 2999}"
BASE="$CACHE/$IMAGE_NAME"
OVERLAY="$CACHE/disk.qcow2"
SEED="$CACHE/seed.iso"
PIDFILE="$CACHE/qemu.pid"
SERIAL_LOG="$CACHE/serial.log"
KEY="$CACHE/id_ed25519"
KNOWN_HOSTS="$CACHE/known_hosts"
PROOF_SUFFIX="app.ihr-proof.in"

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
FAILS=0
check() {  # check <description> <expected> <actual>
  if [[ "$3" == "$2" ]]; then echo "PASS  $1 ($3)"; else echo "FAIL  $1: expected $2, got $3"; FAILS=$((FAILS + 1)); fi
}

# One shared connection per user: after bootstrap, ufw allows 6 new SSH connections per 30 s.
ssh_opts() {
  printf '%s\n' -p "$SSH_PORT" -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes \
    -o "UserKnownHostsFile=$KNOWN_HOSTS" -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=10 -o LogLevel=ERROR -o ControlMaster=auto \
    -o "ControlPath=$CACHE/cm-%r" -o ControlPersist=120
}
vm() {  # vm <user> <command...>
  local user="$1"; shift
  local opts; mapfile -t opts < <(ssh_opts)
  # shellcheck disable=SC2029  # the commands are meant to run in the VM as given
  ssh "${opts[@]}" "$user@127.0.0.1" "$@"
}
close_ssh() {
  local opts; mapfile -t opts < <(ssh_opts)
  for user in ubuntu ihr; do ssh "${opts[@]}" -O exit "$user@127.0.0.1" >/dev/null 2>&1 || true; done
}
running() { [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

fetch() {
  mkdir -p "$CACHE"; chmod 700 "$CACHE"
  if [[ -f "$BASE" ]] && [[ "$(sha256sum "$BASE" | cut -d' ' -f1)" == "$IMAGE_SHA256" ]]; then
    log "Image already here and verified: $BASE"; return
  fi
  local work; work="$(mktemp -d "$CACHE/.fetch.XXXXXX")"
  trap 'rm -rf "${work:-}"; trap - RETURN' RETURN
  log "Downloading SHA256SUMS + signature ($IMAGE_URL_BASE)"
  curl -fsSL --retry 3 -o "$work/SHA256SUMS" "$IMAGE_URL_BASE/SHA256SUMS"
  curl -fsSL --retry 3 -o "$work/SHA256SUMS.gpg" "$IMAGE_URL_BASE/SHA256SUMS.gpg"
  local keyring="$SYSTEM_KEYRING"
  if [[ ! -r "$keyring" ]]; then
    log "No $SYSTEM_KEYRING here: fetching key $SIGNING_KEY_FPR from keyserver.ubuntu.com"
    curl -fsSL --retry 3 -o "$work/key.asc" \
      "https://keyserver.ubuntu.com/pks/lookup?op=get&options=mr&search=0x$SIGNING_KEY_FPR"
    gpg --homedir "$work" --batch --quiet --import "$work/key.asc" 2>/dev/null
    gpg --homedir "$work" --batch --export "$SIGNING_KEY_FPR" > "$work/key.gpg"
    keyring="$work/key.gpg"
  fi
  local fpr
  fpr="$(gpg --batch --show-keys --with-colons "$keyring" 2>/dev/null | awk -F: '/^fpr:/{print $10}' | grep -x "$SIGNING_KEY_FPR" || true)"
  [[ "$fpr" == "$SIGNING_KEY_FPR" ]] || die "keyring $keyring lacks the image signing key $SIGNING_KEY_FPR"
  gpgv --keyring "$keyring" "$work/SHA256SUMS.gpg" "$work/SHA256SUMS" 2>&1 | sed 's/^/    /' \
    || die "SHA256SUMS signature did not verify"
  grep -qx "$IMAGE_SHA256 \*$IMAGE_NAME" "$work/SHA256SUMS" \
    || die "the signed SHA256SUMS does not list our pinned hash for $IMAGE_NAME"
  log "Downloading $IMAGE_NAME (about 820 MiB)"
  curl -fL --retry 3 -o "$work/$IMAGE_NAME" "$IMAGE_URL_BASE/$IMAGE_NAME"
  [[ "$(sha256sum "$work/$IMAGE_NAME" | cut -d' ' -f1)" == "$IMAGE_SHA256" ]] || die "SHA-256 mismatch"
  mv "$work/$IMAGE_NAME" "$BASE"
  log "Verified (signature + SHA-256) and saved: $BASE"
}

up() {
  [[ -f "$BASE" ]] || die "no image: scripts/local-vm.sh fetch"
  [[ -r /dev/kvm && -w /dev/kvm ]] || die "/dev/kvm is not usable by $(id -un) (the user must run: sudo usermod -aG kvm $(id -un), then sign in again)"
  if running; then log "VM already running (pid $(cat "$PIDFILE"))"; return; fi
  [[ -f "$KEY" ]] || ssh-keygen -q -t ed25519 -N '' -C "indiehr-local-vm" -f "$KEY"
  if [[ ! -f "$OVERLAY" ]]; then
    qemu-img create -q -f qcow2 -F qcow2 -b "$BASE" "$OVERLAY" "$DISK"
    rm -f "$KNOWN_HOSTS"  # a new disk has new host keys
  fi
  if [[ ! -f "$SEED" ]]; then
    local seed; seed="$(mktemp -d "$CACHE/.seed.XXXXXX")"
    # The default user (ubuntu, passwordless sudo as on cloud servers) with this VM's own key
    cat > "$seed/user-data" <<EOF
#cloud-config
hostname: ihr-vm
users:
  - default
ssh_pwauth: false
ssh_authorized_keys:
  - $(cat "$KEY.pub")
EOF
    printf 'instance-id: ihr-vm-%s\nlocal-hostname: ihr-vm\n' "$(date +%s)" > "$seed/meta-data"
    xorriso -as mkisofs -quiet -output "$SEED" -volid CIDATA -joliet -rock \
      "$seed/user-data" "$seed/meta-data" 2>/dev/null
    rm -rf "$seed"
  fi
  log "Booting ($MEM MiB, 2 CPUs); SSH 127.0.0.1:$SSH_PORT, HTTP :$HTTP_PORT, HTTPS :$HTTPS_PORT"
  local fwd="hostfwd=tcp:127.0.0.1:$SSH_PORT-:22,hostfwd=tcp:127.0.0.1:$HTTP_PORT-:80"
  fwd+=",hostfwd=tcp:127.0.0.1:$HTTPS_PORT-:443"
  fwd+=",hostfwd=tcp:127.0.0.1:$PROBE_HOST_PORT-:9998,hostfwd=tcp:127.0.0.1:$PROBE_DOCKER_PORT-:9999"
  fwd+=",hostfwd=udp:127.0.0.1:$DNS_PORT-:53,hostfwd=tcp:127.0.0.1:$DNS_PORT-:53"
  qemu-system-x86_64 -enable-kvm -cpu host -m "$MEM" -smp 2 -name ihr-vm \
    -drive "file=$OVERLAY,if=virtio,format=qcow2" \
    -drive "file=$SEED,if=virtio,format=raw,readonly=on" \
    -netdev "user,id=n0,$fwd" -device virtio-net-pci,netdev=n0 \
    -display none -serial "file:$SERIAL_LOG" -daemonize -pidfile "$PIDFILE"
  log "Waiting for SSH (serial console log: $SERIAL_LOG)"
  local i
  for i in $(seq 1 60); do
    if vm ubuntu true 2>/dev/null; then break; fi
    ((i == 60)) && die "no SSH after 5 minutes; see $SERIAL_LOG"
    sleep 5
  done
  vm ubuntu "cloud-init status --wait >/dev/null; cloud-init status" || true
  log "VM up: scripts/local-vm.sh ssh"
}

bootstrap() {
  running || die "VM not running: scripts/local-vm.sh up"
  log "Copying deploy/host/bootstrap.sh and the deploy key into the VM"
  vm ubuntu "cat > /tmp/ihr-bootstrap.sh" < "$ROOT/deploy/host/bootstrap.sh"
  vm ubuntu "cat > /tmp/ihr-deploy.pub" < "$KEY.pub"
  log "Run 1"
  vm ubuntu "sudo bash /tmp/ihr-bootstrap.sh --ssh-key-file /tmp/ihr-deploy.pub" | tee "$CACHE/bootstrap-1.log"
  log "Run 2 (must change nothing)"
  vm ubuntu "sudo bash /tmp/ihr-bootstrap.sh --ssh-key-file /tmp/ihr-deploy.pub" | tee "$CACHE/bootstrap-2.log"
  grep -qx 'No changes.' "$CACHE/bootstrap-2.log" || die "second run changed something (see $CACHE/bootstrap-2.log)"
  log "Fresh SSH connections under the new rules, then --check"
  close_ssh
  vm ihr "id"
  vm ubuntu "sudo /usr/local/sbin/ihr-bootstrap --check" | tee "$CACHE/check.log"
}

reboot_vm() {
  running || die "VM not running"
  vm ubuntu "sudo systemctl reboot" || true
  close_ssh
  sleep 15
  local i
  for i in $(seq 1 40); do
    if vm ubuntu true 2>/dev/null; then break; fi
    ((i == 40)) && die "no SSH after the reboot; see $SERIAL_LOG"
    sleep 5
  done
  vm ubuntu "sudo /usr/local/sbin/ihr-bootstrap --check" | tee "$CACHE/check-after-reboot.log"
}

verify() {
  running || die "VM not running"
  local opts; mapfile -t opts < <(ssh_opts)
  log "SSH: keys only, no root, no passwords"
  check "deploy user ihr signs in with its key" ok "$(vm ihr 'echo ok' 2>/dev/null || echo refused)"
  check "admin user ubuntu signs in with its key" ok "$(vm ubuntu 'echo ok' 2>/dev/null || echo refused)"
  check "root cannot sign in" refused \
    "$(ssh "${opts[@]}" root@127.0.0.1 'echo ok' 2>/dev/null || echo refused)"
  local methods
  methods="$(ssh -p "$SSH_PORT" -o BatchMode=yes -o "UserKnownHostsFile=$KNOWN_HOSTS" \
    -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive \
    -o ControlMaster=no ubuntu@127.0.0.1 true 2>&1 | grep -o 'Permission denied ([^)]*)' || true)"
  check "the server offers public keys only" "Permission denied (publickey)" "$methods"
  sleep 31  # let ufw's SSH rate-limit window pass before the next checks

  log "Firewall: a host port (ufw) and a published container port (DOCKER-USER), from outside"
  local image
  image="$(vm ihr "sed -n 's/^IHR_BACKEND_IMAGE=//p' /srv/indiehr/deploy/staging/.env" 2>/dev/null || true)"
  [[ -n "$image" ]] || die "deploy first (scripts/deploy.sh --local-proof): the probe uses the backend image"
  vm ubuntu "sudo install -d /run/ihr-probe && sudo systemd-run --quiet --unit ihr-probe-host python3 -m http.server 9998 --bind 0.0.0.0 --directory /run/ihr-probe 2>/dev/null || true"
  vm ihr "docker rm -f ihr-probe >/dev/null 2>&1; docker run -d --rm --name ihr-probe -p 9999:9999 --entrypoint python $image -m http.server 9999 >/dev/null"
  sleep 3
  local ip
  ip="$(vm ubuntu "ip -4 route get 1.1.1.1 | sed -n 's/.* src \([0-9.]*\).*/\1/p'")"
  outside() { curl -s -o /dev/null -w '%{http_code}' --max-time 6 "http://127.0.0.1:$1/" || true; }
  check "probe on the host listens (seen inside the VM)" 200 "$(vm ubuntu "curl -s -o /dev/null -w '%{http_code}' http://$ip:9998/")"
  check "probe in a container listens (seen inside the VM)" 200 "$(vm ubuntu "curl -s -o /dev/null -w '%{http_code}' http://$ip:9999/")"
  check "host port 9998 blocked from outside (ufw)" 000 "$(outside "$PROBE_HOST_PORT")"
  check "published container port 9999 blocked from outside (DOCKER-USER)" 000 "$(outside "$PROBE_DOCKER_PORT")"
  # Mutation: open each hole on purpose; the same probe must then get through, which shows
  # the rule (not something else) was doing the blocking.
  vm ubuntu "sudo iptables -I DOCKER-USER 1 -p tcp -m conntrack --ctstate DNAT --ctorigdstport 9999 -j RETURN"
  check "mutation: without the DOCKER-USER block the container port opens" 200 "$(outside "$PROBE_DOCKER_PORT")"
  vm ubuntu "sudo iptables -D DOCKER-USER -p tcp -m conntrack --ctstate DNAT --ctorigdstport 9999 -j RETURN"
  vm ubuntu "sudo ufw allow 9998/tcp >/dev/null"
  check "mutation: with a ufw rule the host port opens" 200 "$(outside "$PROBE_HOST_PORT")"
  vm ubuntu "sudo ufw delete allow 9998/tcp >/dev/null"
  check "after the mutations: container port blocked again" 000 "$(outside "$PROBE_DOCKER_PORT")"
  vm ihr "docker rm -f ihr-probe >/dev/null"
  vm ubuntu "sudo systemctl stop ihr-probe-host 2>/dev/null || true"

  log "HTTPS from outside through the forwarded ports (Traefik, self-signed certificate)"
  local r=(-sk -o /dev/null -w '%{http_code}' --max-time 15)
  check "http redirects to https" 301 "$(curl "${r[@]}" --resolve "console.$PROOF_SUFFIX:$HTTP_PORT:127.0.0.1" "http://console.$PROOF_SUFFIX:$HTTP_PORT/ops/" || true)"
  check "operator console over https" 302 "$(curl "${r[@]}" --resolve "console.$PROOF_SUFFIX:$HTTPS_PORT:127.0.0.1" "https://console.$PROOF_SUFFIX:$HTTPS_PORT/ops/" || true)"
  check "hospital web app over https" 200 "$(curl "${r[@]}" --resolve "alpha.$PROOF_SUFFIX:$HTTPS_PORT:127.0.0.1" "https://alpha.$PROOF_SUFFIX:$HTTPS_PORT/" || true)"

  if [[ -n "$(vm ihr "sed -n 's/^IHR_ACME=//p' /srv/indiehr/deploy/staging/.env" 2>/dev/null || true)" ]]; then
    verify_acme
  fi

  log "Logs: containers write to the persistent journal"
  check "Traefik's log is in journald" yes \
    "$(vm ubuntu "sudo journalctl -q -n 1 -o cat CONTAINER_TAG=indiehr/indiehr-staging-traefik-1 | grep -q . && echo yes || echo no")"
  check "deployed commit recorded" yes "$(vm ihr 'grep -q ^git_sha= /srv/indiehr/DEPLOYED && echo yes || echo no')"
  check "no unfinished deploy (DEPLOYING removed)" no "$(vm ihr 'test -e /srv/indiehr/DEPLOYING && echo yes || echo no')"
  check "backend image holds no git-ignored dev key (jwks.b64.txt)" absent \
    "$(vm ihr "docker run --rm --entrypoint sh $image -c 'test -e /app/jwks.b64.txt && echo present || echo absent'" 2>/dev/null || echo error)"

  log "Re-run as the every-boot start script does (root, no SUDO_USER): same admin, no changes"
  check "admin user saved" ubuntu "$(vm ubuntu 'cat /etc/indiehr/admin-user' 2>/dev/null || echo missing)"
  check "boot-style re-run changes nothing" yes \
    "$(vm ubuntu 'sudo env -u SUDO_USER /usr/local/sbin/ihr-bootstrap --no-upgrade' 2>&1 | grep -qx 'No changes.' && echo yes || echo no)"
  echo
  ((FAILS == 0)) || { echo "$FAILS check(s) FAILED"; exit 1; }
  echo "All checks passed."
}

dns_reply() {  # dns_reply <server> <port> [dig args]: "<status> <answers>", or "noreply"
  # (dig prints its own errors on stdout, so only a real reply's header counts)
  local out st an
  out="$(dig +time=3 +tries=1 @"$1" -p "$2" "${@:3}" 2>/dev/null || true)"
  st="$(sed -n 's/.*->>HEADER<<-.* status: \([A-Z]*\),.*/\1/p' <<< "$out")"
  an="$(sed -n 's/.* ANSWER: \([0-9]*\),.*/\1/p' <<< "$out")"
  if [[ -n "$st" ]]; then echo "$st ${an:-0}"; else echo noreply; fi
}

verify_acme() {  # the wildcard certificate and acme-dns, from outside (P1-T8b2; IHR_ACME=pebble)
  log "Certificate: acme-dns on port 53 from outside, the wildcard certificate over HTTPS"
  local full root
  dns() { dns_reply 127.0.0.1 "$DNS_PORT" "$@"; }
  full="$(vm ihr 'cd /srv/indiehr && scripts/staging.sh acme-register' 2>/dev/null \
    | sed -n 's/^acme-dns account: \([^ ]*\).*/\1/p')"
  check "acme-dns account registered" yes "$([[ -n "$full" ]] && echo yes || echo no)"
  check "DNS over UDP from outside answers the check values" "NOERROR 2" "$(dns TXT "$full")"
  check "DNS over TCP from outside answers the check values" "NOERROR 2" "$(dns +tcp TXT "$full")"
  check "not a resolver for other names (answers: no such name)" "NXDOMAIN 0" "$(dns A example.com)"
  # Mutation: without the DOCKER-USER rule for udp/53 the same question must go unanswered
  local rule="-p udp -m conntrack --ctstate DNAT --ctorigdstport 53 --ctdir ORIGINAL -j RETURN"
  vm ubuntu "sudo iptables -D DOCKER-USER $rule"  # each dig uses a new source port: no old conntrack entry helps
  check "mutation: without its DOCKER-USER rule, port 53/udp is closed" noreply "$(dns TXT "$full")"
  check "mutation: tcp still open (its own rule)" "NOERROR 2" "$(dns +tcp TXT "$full")"
  vm ubuntu "sudo ufw reload >/dev/null"
  check "after ufw reload the udp rule is back" "NOERROR 2" "$(dns TXT "$full")"
  root="$CACHE/pebble-root.pem"
  vm ihr "docker exec indiehr-staging-traefik-1 wget -q -O - --no-check-certificate https://pebble:15000/roots/0" > "$root" 2>/dev/null || true
  local h
  for h in "alpha.$PROOF_SUFFIX" "console.$PROOF_SUFFIX" "auth.$PROOF_SUFFIX"; do
    check "trusted wildcard certificate for $h (Pebble's root only)" 0 \
      "$(curl -s -o /dev/null --max-time 15 --cacert "$root" --resolve "$h:$HTTPS_PORT:127.0.0.1" "https://$h:$HTTPS_PORT/"; echo $?)"
  done
}

down() {
  if ! running; then log "VM not running"; rm -f "$PIDFILE"; return; fi
  log "Shutting down"
  vm ubuntu "sudo systemctl poweroff" >/dev/null 2>&1 || true
  close_ssh
  local i pid; pid="$(cat "$PIDFILE")"
  for i in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 2; done
  if kill -0 "$pid" 2>/dev/null; then kill "$pid"; fi
  rm -f "$PIDFILE"
}

case "${1:-}" in
  fetch) fetch ;;
  up) up ;;
  bootstrap) bootstrap ;;
  reboot) reboot_vm ;;
  verify) verify ;;
  ssh)
    user="${2:-ubuntu}"
    if (($# > 2)); then vm "$user" "${@:3}"; else
      mapfile -t opts < <(ssh_opts); exec ssh -t "${opts[@]}" "$user@127.0.0.1"
    fi ;;
  status)
    if running; then echo "running (pid $(cat "$PIDFILE")), SSH 127.0.0.1:$SSH_PORT"; else echo "stopped"; fi
    ls -lh "$CACHE" 2>/dev/null || true ;;
  down) down ;;
  destroy)
    down
    rm -f "$OVERLAY" "$SEED" "$KNOWN_HOSTS" "$SERIAL_LOG" "$CACHE"/*.log
    rm -rf "$CACHE/staging"  # the VM's own .env and secrets (its database is gone too)
    log "Removed the VM's disk, seed and settings; kept $BASE and $KEY" ;;
  *) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
