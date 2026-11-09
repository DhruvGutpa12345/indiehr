#!/usr/bin/env bash
# IndieHR host bootstrap (P1-T8b1, ADR-011): turns a fresh Ubuntu 26.04 LTS (or 24.04 LTS)
# server into a staging/production host. Provider-neutral: run it as root over SSH from the
# operator's machine, or (installed copy) from a provider's every-boot start script.
#
#   sudo bash bootstrap.sh [--ssh-key "<public key>"]... [--ssh-key-file FILE] [--admin-user NAME]
#                          [--change-admin] [--no-upgrade] [--dry-run]
#   sudo bash bootstrap.sh --check        read-only: PASS/FAIL per control, exit 1 on any FAIL
#
# Idempotent: a second run changes nothing and says "No changes." It never locks the operator
# out: the deploy user's and the admin user's keys are in place, `sshd -t` passes and sshd's
# effective settings let both users in before password and root logins are switched off. The
# admin user chosen on the first run is saved and kept (--change-admin to add another).
# What each control is for: deploy/host/README.md.
set -euo pipefail
umask 022
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 LC_ALL=C.UTF-8
# A provider start script may run with a minimal PATH
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# ---- Pinned choices (docs/VERSIONS.md; tests: scripts/test_bootstrap_static.py) ----------
SUPPORTED_RELEASES=("26.04" "24.04")
# Docker's apt repository (download.docker.com); the release suffix is added per Ubuntu
# release, e.g. 5:29.9.3-1~ubuntu.26.04~resolute. Checked 2026-11-09.
DOCKER_CE_VERSION="5:29.9.3-1"
CONTAINERD_VERSION="2.4.2-3"
BUILDX_VERSION="0.38.1-1"
COMPOSE_VERSION="5.7.0-1"
DOCKER_KEY_URL="https://download.docker.com/linux/ubuntu/gpg"
DOCKER_KEY_FINGERPRINT="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
# CERT-In Directions 28-04-2022 (i) + FAQ Q43: NIC and NPL time servers only.
NTP_SERVERS=("samay1.nic.in" "samay2.nic.in" "time.nplindia.org")
TIMEZONE="Asia/Kolkata"
# CERT-In: 180 days rolling; DPDP Rules 2025: one year of processing logs. The size cap
# (not the time) decides how long logs really last: see deploy/host/README.md.
JOURNAL_MAX_RETENTION="1year"
JOURNAL_SYSTEM_MAX_USE="20G"
JOURNAL_SYSTEM_KEEP_FREE="15G"
DEPLOY_USER="ihr"
SSH_GROUP="ihr-ssh"
SRV_DIR="/srv/indiehr"
SWAPFILE="/swapfile"
# acme-dns (P1-T8b2) publishes port 53; systemd-resolved's local stub would hold it
RESOLVED_DROPIN="/etc/systemd/resolved.conf.d/60-indiehr.conf"
RESOLV_TARGET="/run/systemd/resolve/resolv.conf"
SWAP_SIZE="2G"
SELF_INSTALL="/usr/local/sbin/ihr-bootstrap"
STATE_DIR="/etc/indiehr"
ADMIN_FILE="$STATE_DIR/admin-user"        # the admin chosen on the first run (kept)
INSTALLED_FILE="$STATE_DIR/installed-at"  # first run's date: --check compares log age with it
USER_RE='^[a-z_][a-z0-9_.-]{0,31}$'
BASE_PACKAGES=(ca-certificates curl gnupg ufw fail2ban python3-systemd chrony
  unattended-upgrades rsync iptables openssh-server python3 tzdata)

# ---- Arguments -------------------------------------------------------------------------
MODE=apply DRY_RUN=0 UPGRADE=1 ADMIN_USER="" CHANGE_ADMIN=0 KEY_INPUT=()
usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }
while (($#)); do
  case "$1" in
    --check) MODE=check ;;
    --dry-run) DRY_RUN=1 ;;
    --no-upgrade) UPGRADE=0 ;;
    --admin-user) ADMIN_USER="${2:?--admin-user NAME}"; shift ;;
    --change-admin) CHANGE_ADMIN=1 ;;
    --ssh-key) KEY_INPUT+=("${2:?--ssh-key \"<public key>\"}"); shift ;;
    --ssh-key-file)
      [[ -r "${2:?--ssh-key-file FILE}" ]] || { echo "cannot read $2" >&2; exit 2; }
      mapfile -t -O "${#KEY_INPUT[@]}" KEY_INPUT < "$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done
if [[ -n "$ADMIN_USER" ]]; then
  [[ "$ADMIN_USER" =~ $USER_RE ]] || { echo "--admin-user: not a valid user name: $ADMIN_USER" >&2; exit 2; }
  [[ "$ADMIN_USER" != root && "$ADMIN_USER" != "$DEPLOY_USER" ]] || {
    echo "--admin-user must not be root or $DEPLOY_USER (root SSH is switched off; $DEPLOY_USER has no sudo)" >&2
    exit 2; }
fi
((CHANGE_ADMIN == 0)) || [[ -n "$ADMIN_USER" ]] || { echo "--change-admin needs --admin-user NAME" >&2; exit 2; }
if [[ -n "${IHR_SSH_PUBKEY:-}" ]]; then
  mapfile -t -O "${#KEY_INPUT[@]}" KEY_INPUT <<< "$IHR_SSH_PUBKEY"
fi

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
trap 'echo "ERROR: bootstrap stopped at line $LINENO (nothing after it ran)" >&2' ERR

[[ $EUID -eq 0 ]] || die "run as root (sudo bash $0 ...)"
# shellcheck source=/dev/null
. /etc/os-release
[[ "${ID:-}" == ubuntu ]] || die "Ubuntu only (found ${ID:-unknown})"
supported=0
for r in "${SUPPORTED_RELEASES[@]}"; do [[ "${VERSION_ID:-}" == "$r" ]] && supported=1; done
((supported)) || die "Ubuntu ${SUPPORTED_RELEASES[*]} only (found ${VERSION_ID:-unknown})"
CODENAME="${VERSION_CODENAME:?}"
PKG_SUFFIX="~ubuntu.${VERSION_ID}~${CODENAME}"
declare -A DOCKER_PINS=(
  [docker-ce]="${DOCKER_CE_VERSION}${PKG_SUFFIX}"
  [docker-ce-cli]="${DOCKER_CE_VERSION}${PKG_SUFFIX}"
  [containerd.io]="${CONTAINERD_VERSION}${PKG_SUFFIX}"
  [docker-buildx-plugin]="${BUILDX_VERSION}${PKG_SUFFIX}"
  [docker-compose-plugin]="${COMPOSE_VERSION}${PKG_SUFFIX}"
)

# ---- Desired file contents (shared by apply and --check) ---------------------------------
chrony_conf() {
  echo "# Managed by deploy/host/bootstrap.sh. Time from NIC and NPL only (CERT-In)."
  echo "# Ubuntu's own chrony.conf (pools, DHCP sources) is not read: see chrony.service.d."
  for s in "${NTP_SERVERS[@]}"; do echo "server $s iburst"; done
  cat <<'EOF'
keyfile /etc/chrony/chrony.keys
driftfile /var/lib/chrony/chrony.drift
ntsdumpdir /var/lib/chrony
logdir /var/log/chrony
maxupdateskew 100.0
rtcsync
makestep 1 3
# chronyc talks over the local socket only; no NTP server or UDP command port is opened.
cmdport 0
EOF
}
CHRONY_ENV='DAEMON_OPTS="-F 1 -f /etc/chrony/indiehr.conf"'
CHRONY_DROPIN=$'# Managed by deploy/host/bootstrap.sh: start chronyd with /etc/chrony/indiehr.conf\n# (a later EnvironmentFile overrides /etc/default/chrony).\n[Service]\nEnvironmentFile=/etc/chrony/indiehr.env'

journald_conf() {
  cat <<EOF
# Managed by deploy/host/bootstrap.sh. CERT-In: logs kept 180 days rolling, in India;
# DPDP Rules 2025: processing logs one year. Container logs arrive here via Docker's
# journald log driver. The size cap decides real retention: watch journalctl --disk-usage.
[Journal]
Storage=persistent
Compress=yes
MaxRetentionSec=$JOURNAL_MAX_RETENTION
MaxFileSec=1month
SystemMaxUse=$JOURNAL_SYSTEM_MAX_USE
SystemKeepFree=$JOURNAL_SYSTEM_KEEP_FREE
SystemMaxFileSize=256M
RateLimitIntervalSec=30s
RateLimitBurst=20000
EOF
}

sshd_conf() {
  cat <<EOF
# Managed by deploy/host/bootstrap.sh (Q-E9: key-only, rate-limited, open to the internet).
# The first value sshd reads wins, so this file (10-) beats cloud-init's 50-cloud-init.conf.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PermitRootLogin no
PubkeyAuthentication yes
AuthenticationMethods publickey
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
AllowAgentForwarding no
# local forwarding only: the Keycloak admin console is reached by an SSH tunnel
AllowTcpForwarding local
ClientAliveInterval 300
ClientAliveCountMax 2
AllowGroups $SSH_GROUP
EOF
}
SSHD_DROPIN="/etc/ssh/sshd_config.d/10-indiehr.conf"
# name -> value as `sshd -T` prints it (lower-case), checked by --check
declare -A SSHD_EXPECT=(
  [passwordauthentication]=no [kbdinteractiveauthentication]=no [permitrootlogin]=no
  [pubkeyauthentication]=yes [maxauthtries]=3 [logingracetime]=30 [x11forwarding]=no
  [permitemptypasswords]=no [allowgroups]="$SSH_GROUP" [authenticationmethods]=publickey
)

daemon_json() {
  cat <<'EOF'
{
  "log-driver": "journald",
  "log-opts": {"tag": "docker/{{.Name}}"},
  "live-restore": true,
  "userland-proxy": false,
  "no-new-privileges": true,
  "iptables": true
}
EOF
}

# Docker publishes ports with DNAT + FORWARD, so ufw's INPUT rules never see them. Docker
# consults DOCKER-USER before its own rules; ufw loads this block at boot (before Docker
# starts) and on every reload, so the chain is never open. From outside, only connections
# whose original destination port is 80 or 443 (Traefik), or 53 (acme-dns: the DNS check for the
# wildcard certificate, P1-T8b2) reach a container; containers' own traffic (from docker0 / br-*)
# and replies are untouched.
docker_user_block() {
  cat <<'EOF'
# BEGIN IHR DOCKER-USER (managed by deploy/host/bootstrap.sh; edits here are overwritten)
*filter
:DOCKER-USER - [0:0]
-A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
-A DOCKER-USER -i docker0 -j RETURN
-A DOCKER-USER -i br-+ -j RETURN
-A DOCKER-USER -p tcp -m conntrack --ctstate DNAT --ctorigdstport 80 --ctdir ORIGINAL -j RETURN
-A DOCKER-USER -p tcp -m conntrack --ctstate DNAT --ctorigdstport 443 --ctdir ORIGINAL -j RETURN
-A DOCKER-USER -p udp -m conntrack --ctstate DNAT --ctorigdstport 53 --ctdir ORIGINAL -j RETURN
-A DOCKER-USER -p tcp -m conntrack --ctstate DNAT --ctorigdstport 53 --ctdir ORIGINAL -j RETURN
-A DOCKER-USER -m limit --limit 6/min -j LOG --log-prefix "[IHR DOCKER-USER BLOCK] "
-A DOCKER-USER -j DROP
COMMIT
# END IHR DOCKER-USER
EOF
}

unattended_conf() {
  cat <<'EOF'
// Managed by deploy/host/bootstrap.sh. Security updates only (the origins in Ubuntu's
// 50unattended-upgrades); never an automatic reboot: a pending reboot shows in --check.
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::SyslogEnable "true";
EOF
}

fail2ban_conf() {  # fail2ban_conf "<ports, comma-separated>": the ports sshd really uses
  cat <<EOF
# Managed by deploy/host/bootstrap.sh: in addition to ufw's rate limit on SSH, ban
# addresses that fail to sign in (journal backend: no auth.log needed).
[sshd]
enabled = true
backend = systemd
port = $1
maxretry = 5
findtime = 10m
bantime = 1h
bantime.increment = true
bantime.maxtime = 1w
EOF
}

resolved_conf() {
  cat <<'EOF'
# Managed by deploy/host/bootstrap.sh (P1-T8b2). acme-dns answers on port 53 for the wildcard
# certificate's DNS check, so systemd-resolved keeps no local stub on 127.0.0.53/127.0.0.54:53;
# the host reads its upstream resolvers directly (/etc/resolv.conf -> /run/systemd/resolve/resolv.conf).
[Resolve]
DNSStubListener=no
EOF
}

sysctl_conf() {
  cat <<'EOF'
# Managed by deploy/host/bootstrap.sh. Leaves net.ipv4.ip_forward and the bridge settings
# to Docker; log_martians stays off (container NAT makes it noisy).
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv4.conf.all.secure_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.icmp_ignore_bogus_error_responses = 1
net.ipv4.tcp_syncookies = 1
kernel.kptr_restrict = 2
kernel.dmesg_restrict = 1
kernel.unprivileged_bpf_disabled = 1
net.core.bpf_jit_harden = 2
kernel.yama.ptrace_scope = 1
fs.suid_dumpable = 0
vm.swappiness = 10
EOF
}

# =========================================================================================
# --check: read-only verification
# =========================================================================================
FAILS=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
info() { printf 'INFO  %s\n' "$1"; }
expect() { if "${@:2}"; then pass "$1"; else fail "$1"; fi; }
same_content() { [[ -f "$1" ]] && [[ "$(cat "$1")" == "$2" ]]; }
in_group() { [[ " $(id -nG "$1" 2>/dev/null) " == *" $2 "* ]]; }
not_in_group() { ! in_group "$@"; }
not_active() { ! systemctl is-active --quiet "$1"; }
contains() { [[ "$1" == *"$2"* ]]; }
apport_off() { [[ -z "$1" || "$1" == "masked" || "$1" == "not-found" ]]; }
valid_keys() {  # valid_keys [quiet]: print the usable public-key lines of stdin
  # A line with options (command=, restrict, from=, no-pty...) is skipped, never copied or
  # counted: cloud-init puts a forced "Please login as ubuntu" command on root's key, and such
  # a key opens no shell. Private keys are refused.
  local line
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == *"PRIVATE KEY"* ]] && die "a private key was given where a public key belongs"
    if ! ssh-keygen -l -f <(printf '%s\n' "$line") >/dev/null 2>&1; then
      [[ "${1:-}" == quiet ]] || echo "    ignored a line that is not an SSH public key" >&2
    elif [[ "$line" =~ ^(ssh-|ecdsa-|sk-) ]]; then
      printf '%s\n' "$line"
    else
      [[ "${1:-}" == quiet ]] \
        || echo "    ignored a key with options (e.g. cloud-init's forced command): it opens no shell" >&2
    fi
  done
}
home_of() { getent passwd "$1" | cut -d: -f6; }
keys_of() {  # keys_of <user> [quiet]: usable keys in the user's authorized_keys
  local f; f="$(home_of "$1")/.ssh/authorized_keys"
  if [[ -r "$f" ]]; then valid_keys "${2:-quiet}" < "$f"; fi
}
key_count() { keys_of "$1" | grep -c . || true; }
saved_admin() {  # the admin user saved by the first run, if any
  local name=""
  [[ -s "$ADMIN_FILE" ]] && name="$(head -n1 "$ADMIN_FILE")"
  [[ -z "$name" || "$name" =~ $USER_RE ]] || die "$ADMIN_FILE holds an invalid user name"
  echo "$name"
}
pick_admin_user() {  # --admin-user, else the saved one, else who ran sudo, ubuntu, ihradmin
  local saved; saved="$(saved_admin)"
  if [[ -n "$ADMIN_USER" ]]; then echo "$ADMIN_USER"
  elif [[ -n "$saved" ]]; then echo "$saved"
  elif [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root && "$SUDO_USER" != "$DEPLOY_USER" \
          && "$SUDO_USER" =~ $USER_RE ]]; then echo "$SUDO_USER"
  elif id ubuntu >/dev/null 2>&1; then echo ubuntu
  else echo ihradmin; fi
}
sudoers_file() { echo "/etc/sudoers.d/90-indiehr-admin-${1//[^a-z0-9_-]/_}"; }
# The Docker key file must hold exactly one primary key, Docker's: apt trusts every key in a
# Signed-By file. Prints one fingerprint per primary key (subkeys are not listed).
key_fpr() { gpg --show-keys --with-colons "$1" 2>/dev/null | awk -F: '/^pub:/{p=1; next} p && /^fpr:/{print $10; p=0}'; }
sshd_ports() {  # every port sshd listens on (Port lines in sshd_config), one per line
  mkdir -p /run/sshd
  sshd -T 2>/dev/null | awk '$1=="port"{print $2}' | sort -un
}
# Addresses to evaluate Match blocks for: a documentation address, plus the operator's own
# when this run came over SSH (sudo may drop SSH_CONNECTION; then only the first is used).
CHECK_ADDRS=("192.0.2.1")
client_addr="${SSH_CONNECTION:-}"; client_addr="${client_addr%% *}"
[[ -n "$client_addr" ]] || { client_addr="${SSH_CLIENT:-}"; client_addr="${client_addr%% *}"; }
[[ -n "$client_addr" && "$client_addr" != 192.0.2.1 ]] && CHECK_ADDRS+=("$client_addr")
sshd_value() {  # sshd_value <key> <sshd -T output>: all values of the key, space-joined
  awk -v k="$1" '$1==k{$1=""; sub(/^ /,""); v = v (v == "" ? "" : " ") $0} END{print v}' <<< "$2"
}
sshd_login_problems() {  # sshd_login_problems <user> <addr>: why a key login could fail or be weak
  local eff k v
  eff="$(sshd -T -C "user=$1,host=check.invalid,addr=$2" 2>/dev/null)" \
    || { echo "sshd -T failed for $1"; return 0; }
  # Each of these is a filter of its own that our AllowGroups cannot override
  for k in allowusers denyusers denygroups; do
    v="$(sshd_value "$k" "$eff")"
    [[ -z "$v" ]] || echo "$k '$v' is set elsewhere (only AllowGroups $SSH_GROUP may decide)"
  done
  for k in "${!SSHD_EXPECT[@]}"; do
    v="$(sshd_value "$k" "$eff")"
    [[ "$v" == "${SSHD_EXPECT[$k]}" ]] || echo "$k is '${v:-unset}', expected '${SSHD_EXPECT[$k]}'"
  done
}

run_check() {
  local out v name admin ports p u a problems
  admin="$ADMIN_USER"
  pass "Ubuntu $VERSION_ID ($CODENAME) is supported"

  # packages and Docker
  local missing_pkgs=()
  for p in "${BASE_PACKAGES[@]}"; do
    [[ "$(dpkg-query -W -f='${db:Status-Abbrev}' "$p" 2>/dev/null)" == ?i* ]] || missing_pkgs+=("$p")
  done
  expect "base packages installed${missing_pkgs[*]:+ (missing: ${missing_pkgs[*]})}" [ "${#missing_pkgs[@]}" -eq 0 ]
  out="$(apt-mark showhold)"
  for p in "${!DOCKER_PINS[@]}"; do
    v="$(dpkg-query -W -f='${Version}' "$p" 2>/dev/null || true)"
    expect "$p pinned at ${DOCKER_PINS[$p]} (found ${v:-none})" [ "$v" == "${DOCKER_PINS[$p]}" ]
    expect "$p held (no surprise upgrades)" grep -qx "$p" <<< "$out"
  done
  v="$(key_fpr /etc/apt/keyrings/docker.asc | paste -sd' ' -)"
  expect "Docker apt keyring holds only key $DOCKER_KEY_FINGERPRINT (found: ${v:-none})" \
    [ "$v" == "$DOCKER_KEY_FINGERPRINT" ]
  expect "docker running" systemctl is-active --quiet docker
  expect "daemon.json as managed" same_content /etc/docker/daemon.json "$(daemon_json)"
  v="$(docker info --format '{{.LoggingDriver}} {{.LiveRestoreEnabled}}' 2>/dev/null || true)"
  expect "Docker logs to journald, live-restore on (found: $v)" [ "$v" == "journald true" ]

  # firewall
  out="$(ufw status verbose 2>/dev/null || true)"
  expect "ufw active" grep -q '^Status: active' <<< "$out"
  expect "ufw default deny incoming, allow outgoing" grep -q 'Default: deny (incoming), allow (outgoing)' <<< "$out"
  expect "ufw covers IPv6 too (IPV6=yes in /etc/default/ufw)" grep -qx 'IPV6=yes' /etc/default/ufw
  out="$(ufw show added 2>/dev/null || true)"
  ports="$(sshd_ports)"
  expect "sshd reports its port(s) (found: $(paste -sd' ' - <<< "$ports"))" [ -n "$ports" ]
  for p in $ports; do
    expect "ufw limit $p/tcp (SSH rate limit)" grep -Eq "^ufw limit $p/tcp( |$)" <<< "$out"
    if grep -Eq "$(ssh_bypass_re "$p")" <<< "$out"; then
      fail "a plain allow rule bypasses the SSH limit on $p: $(grep -E "$(ssh_bypass_re "$p")" <<< "$out" | paste -sd';' -)"
    else pass "no plain allow rule bypasses the SSH limit on $p"; fi
  done
  while IFS= read -r v; do
    [[ -n "$v" ]] && info "extra ufw rule (not managed here; review it): $v"
  done < <(grep '^ufw ' <<< "$out" | grep -Ev "^ufw (allow (80|443)/tcp|allow 53|allow out 123/udp|limit ($(paste -sd'|' - <<< "${ports:-22}"))/tcp)( |$)" || true)
  expect "ufw allow 80/tcp" grep -Eq '^ufw allow 80/tcp( |$)' <<< "$out"
  expect "ufw allow 443/tcp" grep -Eq '^ufw allow 443/tcp( |$)' <<< "$out"
  expect "ufw allow 53 (acme-dns, tcp+udp)" grep -Eq '^ufw allow 53( |$)' <<< "$out"
  expect "ufw allow out 123/udp (NTP)" grep -Eq '^ufw allow out 123/udp( |$)' <<< "$out"
  out="$(iptables -S DOCKER-USER 2>/dev/null || true)"
  # the first rule without conditions must be our DROP (Docker may append a RETURN after it)
  v="$(awk 'NF==4 && $1=="-A" {print $4; exit}' <<< "$out")"
  if [[ "$out" == *"--ctorigdstport 80 "* && "$out" == *"--ctorigdstport 443 "* \
        && "$out" == *"-p udp -m conntrack --ctstate DNAT --ctorigdstport 53 "* \
        && "$out" == *"-p tcp -m conntrack --ctstate DNAT --ctorigdstport 53 "* && "$v" == DROP ]]; then
    pass "DOCKER-USER: only 80, 443 and 53 reach containers from outside, then DROP"
  else fail "DOCKER-USER: only 80, 443 and 53 reach containers from outside, then DROP"; fi
  out="$(iptables -S FORWARD 2>/dev/null || true)"
  expect "FORWARD consults DOCKER-USER" grep -q -- '-j DOCKER-USER' <<< "$out"

  # SSH
  mkdir -p /run/sshd
  expect "sshd -t accepts the configuration" sshd -t
  out="$(sshd -T -C "user=$DEPLOY_USER,host=check.invalid,addr=192.0.2.1" 2>/dev/null || true)"
  for name in "${!SSHD_EXPECT[@]}"; do
    v="$(sshd_value "$name" "$out")"
    expect "sshd $name ${SSHD_EXPECT[$name]} (found: ${v:-unset})" [ "$v" == "${SSHD_EXPECT[$name]}" ]
  done
  # Effective settings (Match blocks included) for both users, and no other user filter
  for u in "$DEPLOY_USER" "$admin"; do
    for a in "${CHECK_ADDRS[@]}"; do
      problems="$(sshd_login_problems "$u" "$a")"
      if [[ -z "$problems" ]]; then pass "sshd lets $u in by key only (from $a; Match blocks applied)"
      else while IFS= read -r v; do fail "sshd for $u from $a: $v"; done <<< "$problems"; fi
    done
  done
  v="$(sshd_value permitrootlogin "$(sshd -T -C "user=root,host=check.invalid,addr=192.0.2.1" 2>/dev/null)")"
  expect "sshd refuses root, Match blocks included (found: ${v:-unset})" [ "$v" == no ]
  expect "deploy user $DEPLOY_USER in group docker" in_group "$DEPLOY_USER" docker
  expect "deploy user $DEPLOY_USER in group $SSH_GROUP" in_group "$DEPLOY_USER" "$SSH_GROUP"
  expect "deploy user $DEPLOY_USER is not a sudoer" not_in_group "$DEPLOY_USER" sudo
  v="$(key_count "$DEPLOY_USER")"; expect "deploy user has SSH keys ($v)" [ "$v" -ge 1 ]
  expect "admin user $admin saved in $ADMIN_FILE" [ "$(saved_admin)" == "$admin" ]
  expect "admin user $admin in group $SSH_GROUP" in_group "$admin" "$SSH_GROUP"
  expect "admin user $admin in group sudo" in_group "$admin" sudo
  expect "admin user $admin has passwordless sudo ($(sudoers_file "$admin"))" \
    same_content "$(sudoers_file "$admin")" "$admin ALL=(ALL) NOPASSWD:ALL"
  v="$(key_count "$admin")"; expect "admin user has SSH keys ($v)" [ "$v" -ge 1 ]
  v="$(passwd -S "$DEPLOY_USER" 2>/dev/null | awk '{print $2}')"
  expect "deploy user has no usable password (status $v)" [ "$v" == L ]
  expect "fail2ban running" systemctl is-active --quiet fail2ban
  expect "fail2ban sshd jail" fail2ban-client status sshd
  expect "fail2ban watches the SSH port(s) ${ports//$'\n'/,}" \
    same_content /etc/fail2ban/jail.d/60-indiehr-sshd.local "$(fail2ban_conf "${ports//$'\n'/,}")"

  # time
  expect "timezone $TIMEZONE" [ "$(timedatectl show -p Timezone --value 2>/dev/null)" == "$TIMEZONE" ]
  expect "chrony running" systemctl is-active --quiet chrony
  expect "systemd-timesyncd not running" not_active systemd-timesyncd
  v="$(systemctl show -p MainPID --value chrony 2>/dev/null || echo 0)"
  v="$( [[ "$v" != 0 && -r "/proc/$v/cmdline" ]] && tr '\0' ' ' < "/proc/$v/cmdline" || true)"
  expect "chronyd reads /etc/chrony/indiehr.conf" contains "$v" "-f /etc/chrony/indiehr.conf"
  expect "chrony config: NIC/NPL servers only" same_content /etc/chrony/indiehr.conf "$(chrony_conf)"
  chronyc waitsync 12 0 0 5 >/dev/null 2>&1 || true
  out="$(chronyc -N -c sources 2>/dev/null || true)"
  local reachable=0 n
  n="$(grep -c . <<< "$out" || true)"
  expect "chrony has exactly ${#NTP_SERVERS[@]} sources (found $n)" [ "$n" -eq "${#NTP_SERVERS[@]}" ]
  out="$(chronyc -N sources 2>/dev/null || true)"   # -N: names as configured
  for s in "${NTP_SERVERS[@]}"; do
    # columns: MS Name Stratum Poll Reach ...; Reach is octal, 0 = never answered
    v="$(awk -v s="$s" '$2==s{print $5}' <<< "$out")"
    if [[ -n "$v" && "$v" != 0 ]]; then pass "NTP $s reachable (reach $v)"; reachable=1
    else info "NTP $s not answering (reach ${v:-absent})"; fi
  done
  expect "at least one Indian time server reachable" [ "$reachable" -eq 1 ]
  out="$(chronyc tracking 2>/dev/null || true)"
  expect "clock synchronised (Leap status Normal)" grep -q '^Leap status *: Normal' <<< "$out"
  info "$(grep -E '^(Reference ID|System time)' <<< "$out" | tr -s ' ' | paste -sd ';' -)"

  # logs
  out="$(systemd-analyze cat-config systemd/journald.conf 2>/dev/null || true)"
  for kv in "Storage=persistent" "MaxRetentionSec=$JOURNAL_MAX_RETENTION" \
            "SystemMaxUse=$JOURNAL_SYSTEM_MAX_USE" "SystemKeepFree=$JOURNAL_SYSTEM_KEEP_FREE"; do
    v="$(grep -E "^${kv%%=*}=" <<< "$out" | tail -1)"
    expect "journald $kv (effective: ${v:-default})" [ "$v" == "$kv" ]
  done
  expect "persistent journal directory /var/log/journal" test -d /var/log/journal
  info "journal: $(journalctl --disk-usage 2>/dev/null | sed 's/^Archived and active journals take up //')"
  check_log_retention

  # updates, swap, kernel settings, directories
  out="$(apt-config dump 2>/dev/null)"
  expect "unattended security updates on" grep -q '^APT::Periodic::Unattended-Upgrade "1";' <<< "$out"
  expect "no automatic reboot" grep -q '^Unattended-Upgrade::Automatic-Reboot "false";' <<< "$out"
  if [[ -f /run/reboot-required ]]; then info "a reboot is pending (security update); plan one"; fi
  expect "swap active" [ -n "$(swapon --show --noheadings)" ]
  # host DNS: port 53 free for acme-dns (P1-T8b2), names still resolve
  if systemctl is-enabled --quiet systemd-resolved 2>/dev/null; then
    expect "systemd-resolved: no local stub on port 53" same_content "$RESOLVED_DROPIN" "$(resolved_conf)"
    expect "/etc/resolv.conf points at the upstream resolvers" [ "$(readlink -f /etc/resolv.conf)" == "$RESOLV_TARGET" ]
  fi
  # Docker itself holding it (dockerd or docker-proxy) is acme-dns's published port
  v="$(ss -Hlntup 'sport = :53' 2>/dev/null | grep -Ev '\(\("(dockerd|docker-proxy)"' || true)"
  expect "port 53 free on the host for acme-dns (other than Docker's; found: ${v:-nothing})" [ -z "$v" ]
  expect "the host still resolves names (download.docker.com)" getent ahosts download.docker.com
  local apport_state
  apport_state="$(systemctl is-enabled apport.service 2>/dev/null || true)"
  expect "apport crash reporter switched off (masked or absent; found: ${apport_state:-absent})" \
    apport_off "$apport_state"
  while read -r name _ v; do
    [[ -z "$name" || "$name" == \#* ]] && continue
    if [[ ! -e "/proc/sys/${name//.//}" ]]; then info "sysctl $name not on this kernel (skipped)"; continue; fi
    expect "sysctl $name = $v" [ "$(sysctl -n "$name" 2>/dev/null)" == "$v" ]
  done < <(sysctl_conf)
  expect "$SRV_DIR owned by $DEPLOY_USER" [ "$(stat -c '%U' "$SRV_DIR" 2>/dev/null)" == "$DEPLOY_USER" ]

  echo
  if ((FAILS)); then echo "$FAILS check(s) FAILED"; return 1; fi
  echo "All checks passed."
}

check_log_retention() {
  # journald's real cap is min(SystemMaxUse, used + free - SystemKeepFree): the journal shares
  # the disk with Docker, so it deletes old logs early as the disk fills. FAIL when entries
  # younger than 180 days are already gone (the oldest entry is younger than both 180 days and
  # this host's first run); a WARNING when free space already shrinks the cap.
  local now first oldest host_days log_days free_g used_g keep_g max_g
  now="$(date +%s)"
  first="$(head -n1 "$INSTALLED_FILE" 2>/dev/null || true)"
  oldest="$(journalctl -q -o short-unix --no-pager 2>/dev/null | head -n1 | cut -d. -f1)"
  if [[ "$first" =~ ^[0-9]+$ && "$oldest" =~ ^[0-9]+$ ]]; then
    host_days=$(((now - first) / 86400)); log_days=$(((now - oldest) / 86400))
    if ((log_days < 180 && host_days > log_days + 1)); then
      fail "journal keeps 180 days: oldest entry is $log_days days old, host set up $host_days days ago (logs were deleted early: free disk space or raise the cap)"
    else pass "journal keeps 180 days (oldest entry $log_days days old, host set up $host_days days ago)"; fi
  else fail "journal age can be checked ($INSTALLED_FILE or the oldest entry missing)"; fi
  keep_g="${JOURNAL_SYSTEM_KEEP_FREE%G}"; max_g="${JOURNAL_SYSTEM_MAX_USE%G}"
  free_g="$(df --output=avail -BG /var/log/journal 2>/dev/null | tail -n1 | tr -dc 0-9)"
  used_g="$(du -s -BG /var/log/journal 2>/dev/null | cut -f1 | tr -dc 0-9)"
  if [[ -n "$free_g" && -n "$used_g" ]] && ((used_g + free_g - keep_g < max_g)); then
    info "WARNING: journal cap is now about $((used_g + free_g - keep_g > 0 ? used_g + free_g - keep_g : 0))G, not ${max_g}G (only ${free_g}G free; journald keeps ${keep_g}G free): free disk space (docker image prune) or logs will be deleted early"
  fi
}
ssh_bypass_re() {  # ERE for a plain allow rule (as `ufw show added` prints it) covering SSH port $1
  local alt="$1(/tcp)?|OpenSSH"
  [[ "$1" == 22 ]] && alt+="|ssh(/tcp)?"
  echo "^ufw allow (in )?($alt)( |\$)"
}

ADMIN_USER_GIVEN="$ADMIN_USER"
ADMIN_USER="$(pick_admin_user)"
SAVED_ADMIN="$(saved_admin)"
if [[ -n "$SAVED_ADMIN" && "$ADMIN_USER" != "$SAVED_ADMIN" ]] && ((CHANGE_ADMIN == 0)); then
  die "the admin user of this host is $SAVED_ADMIN ($ADMIN_FILE), not $ADMIN_USER_GIVEN. To add $ADMIN_USER_GIVEN as the new admin (the old one keeps sudo), add --change-admin"
fi

if [[ "$MODE" == check ]]; then
  trap - ERR
  set +e
  run_check
  exit $?
fi

# =========================================================================================
# apply
# =========================================================================================
exec 9>/run/ihr-bootstrap.lock
flock -w 900 9 || die "another bootstrap run holds /run/ihr-bootstrap.lock"
CHANGES=()
changed() { CHANGES+=("$1"); printf '    changed: %s\n' "$1"; }
act() {  # act <description> <command...>: run (or, with --dry-run, report) one change
  local what="$1"; shift
  if ((DRY_RUN)); then CHANGES+=("$what"); printf '    would: %s\n' "$what"; return 0; fi
  "$@"
  changed "$what"
}
WROTE=0
write_file() {  # write_file <path> <mode> [owner:group] < content; sets WROTE=1 if changed
  local path="$1" mode="$2" owner="${3:-root:root}" tmp
  WROTE=0
  tmp="$(mktemp)"
  cat > "$tmp"
  if [[ -f "$path" ]] && cmp -s "$tmp" "$path" \
     && [[ "$(stat -c '%a %U:%G' "$path")" == "$mode $owner" ]]; then
    rm -f "$tmp"; return 0
  fi
  WROTE=1
  if ((DRY_RUN)); then
    CHANGES+=("$path"); printf '    would write: %s\n' "$path"
    diff -u "$path" "$tmp" 2>/dev/null | sed -n '3,40p' | sed 's/^/      /' || true
    rm -f "$tmp"; return 0
  fi
  install -D -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$tmp" "$path"
  rm -f "$tmp"
  changed "$path"
}
quiet() { "$@" >/dev/null; }
# Second letter of dpkg's status is the real state ("i" = installed); the first is the wish,
# which is "h" for the pinned Docker packages once they are held ("hi ").
installed() { [[ "$(dpkg-query -W -f='${db:Status-Abbrev}' "$1" 2>/dev/null)" == ?i* ]]; }
APT=(apt-get -y -q -o Dpkg::Lock::Timeout=600 -o Dpkg::Options::=--force-confdef
     -o Dpkg::Options::=--force-confold)
APT_UPDATED=0
apt_update() {
  ((APT_UPDATED)) && return 0
  if ((DRY_RUN)); then echo "    would: apt-get update"; APT_UPDATED=1; return 0; fi
  "${APT[@]}" update >/dev/null
  APT_UPDATED=1
}

# ---- 1. Packages ----------------------------------------------------------------------
log "Packages (Ubuntu $VERSION_ID $CODENAME)"
missing=()
for p in "${BASE_PACKAGES[@]}"; do installed "$p" || missing+=("$p"); done
if ((UPGRADE)); then
  apt_update
  before="$(dpkg-query -W -f='${Package} ${Version}\n')"
  if ((DRY_RUN)); then
    n="$(apt-get -s upgrade 2>/dev/null | grep -c '^Inst ' || true)"
    ((n == 0)) || { CHANGES+=("upgrade $n packages"); echo "    would: upgrade $n packages"; }
  else
    "${APT[@]}" upgrade >/dev/null
    after="$(dpkg-query -W -f='${Package} ${Version}\n')"
    n="$(diff <(echo "$before") <(echo "$after") | grep -c '^>' || true)"
    ((n == 0)) || changed "upgraded $n packages"
  fi
fi
if ((${#missing[@]})); then
  apt_update
  act "installed ${missing[*]}" "${APT[@]}" install --no-install-recommends "${missing[@]}"
fi
# chrony replaces systemd-timesyncd (they conflict); make sure the old one is gone
if installed systemd-timesyncd; then
  act "removed systemd-timesyncd (chrony keeps time)" "${APT[@]}" purge systemd-timesyncd
fi

# ---- 2. Time: chrony to NIC/NPL, IST ---------------------------------------------------
log "Time (chrony to ${NTP_SERVERS[*]}; $TIMEZONE)"
restart_chrony=0
write_file /etc/chrony/indiehr.conf 644 < <(chrony_conf); ((WROTE)) && restart_chrony=1
write_file /etc/chrony/indiehr.env 644 <<< "$CHRONY_ENV"; ((WROTE)) && restart_chrony=1
write_file /etc/systemd/system/chrony.service.d/10-indiehr.conf 644 <<< "$CHRONY_DROPIN"
if ((WROTE)); then restart_chrony=1; ((DRY_RUN)) || systemctl daemon-reload; fi
# Ubuntu's default pool (1.ntp.ubuntu.com, NTS): not read by our config; removed for clarity
if [[ -e /etc/chrony/sources.d/ubuntu-ntp-pools.sources ]]; then
  act "removed Ubuntu's default NTP pool file" rm -f /etc/chrony/sources.d/ubuntu-ntp-pools.sources
fi
if ! systemctl is-enabled --quiet chrony 2>/dev/null; then
  act "enabled chrony" systemctl enable chrony
fi
# restart only for a new config; otherwise start (a no-op while it is still starting at boot)
if ((restart_chrony)); then
  act "restarted chrony" systemctl restart chrony
elif ! systemctl is-active --quiet chrony; then
  if [[ "$(systemctl show -p ActiveState --value chrony)" == activating ]]; then
    ((DRY_RUN)) || systemctl start chrony   # waits for it; not a change
  else
    act "started chrony" systemctl start chrony
  fi
fi
if [[ "$(timedatectl show -p Timezone --value)" != "$TIMEZONE" ]]; then
  act "timezone $TIMEZONE" timedatectl set-timezone "$TIMEZONE"
fi

# ---- 3. Logs: persistent journal ---------------------------------------------------------
log "Logs (journald persistent, MaxRetentionSec=$JOURNAL_MAX_RETENTION, cap $JOURNAL_SYSTEM_MAX_USE)"
if [[ ! -d /var/log/journal ]]; then
  act "created /var/log/journal" install -d -m 2755 -g systemd-journal /var/log/journal
fi
# --check compares the oldest log entry with this date to see whether logs went early
if [[ ! -s "$INSTALLED_FILE" ]]; then
  write_file "$INSTALLED_FILE" 644 < <(date +%s)
fi
write_file /etc/systemd/journald.conf.d/60-indiehr.conf 644 < <(journald_conf)
if ((WROTE)) && ! ((DRY_RUN)); then
  systemd-tmpfiles --create --prefix /var/log/journal >/dev/null 2>&1 || true
  systemctl restart systemd-journald
  journalctl --flush >/dev/null 2>&1 || true
fi

# ---- 4. Automatic security updates ---------------------------------------------------------
log "Automatic security updates (no automatic reboot)"
write_file /etc/apt/apt.conf.d/52indiehr-unattended-upgrades 644 < <(unattended_conf)
if ! systemctl is-enabled --quiet unattended-upgrades 2>/dev/null; then
  act "enabled unattended-upgrades" systemctl enable --now unattended-upgrades
fi

# ---- 5. Swap and kernel settings -----------------------------------------------------------
log "Swap ($SWAP_SIZE if none) and kernel settings"
if [[ -z "$(swapon --show --noheadings)" ]]; then
  if [[ ! -f "$SWAPFILE" ]]; then
    act "created $SWAPFILE ($SWAP_SIZE)" bash -c \
      "fallocate -l '$SWAP_SIZE' '$SWAPFILE' && chmod 600 '$SWAPFILE' && mkswap '$SWAPFILE' >/dev/null"
  fi
  act "swap on $SWAPFILE" swapon "$SWAPFILE"
fi
if [[ -f "$SWAPFILE" ]] && ! grep -Eq "^${SWAPFILE}[[:space:]]" /etc/fstab; then
  act "$SWAPFILE in /etc/fstab" bash -c "echo '$SWAPFILE none swap sw 0 0' >> /etc/fstab"
fi
write_file /etc/sysctl.d/60-indiehr.conf 644 < <(sysctl_conf)
# -e: a key this kernel lacks (net.ipv6.* when booted with ipv6.disable=1) is skipped
if ((WROTE)) && ! ((DRY_RUN)); then sysctl -q -e -p /etc/sysctl.d/60-indiehr.conf; fi
# Ubuntu's crash reporter sets fs.suid_dumpable=2 every time it starts (so after each boot),
# undoing the setting above. A server needs no crash reports; switch it off for good.
if systemctl list-unit-files apport.service >/dev/null 2>&1 \
   && [[ "$(systemctl is-enabled apport.service 2>/dev/null)" != "masked" ]]; then
  act "switched off apport (it resets fs.suid_dumpable at boot)" \
    quiet systemctl mask --now apport.service
  if ! ((DRY_RUN)); then sysctl -q -w fs.suid_dumpable=0; fi
fi

# ---- 5b. Host DNS: port 53 for acme-dns (P1-T8b2) --------------------------------------------
# systemd-resolved stays (it still reads the provider's resolvers from DHCP); only its local stub
# goes, and /etc/resolv.conf names the upstream resolvers instead of 127.0.0.53.
if systemctl is-enabled --quiet systemd-resolved 2>/dev/null; then
  log "Host DNS (systemd-resolved without its local stub: port 53 is acme-dns's)"
  write_file "$RESOLVED_DROPIN" 644 < <(resolved_conf)
  if ((WROTE)) && ! ((DRY_RUN)); then systemctl restart systemd-resolved; fi
  if [[ "$(readlink -f /etc/resolv.conf)" != "$RESOLV_TARGET" ]]; then
    act "/etc/resolv.conf -> $RESOLV_TARGET" ln -sfn "$RESOLV_TARGET" /etc/resolv.conf
  fi
fi

# ---- 6. Docker Engine (pinned, Docker's own apt repository) --------------------------------
log "Docker Engine $DOCKER_CE_VERSION (pinned)"
for p in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
  if installed "$p"; then act "removed conflicting package $p" "${APT[@]}" remove "$p"; fi
done
if [[ "$(key_fpr /etc/apt/keyrings/docker.asc)" != "$DOCKER_KEY_FINGERPRINT" ]]; then
  tmpkey="$(mktemp)"
  curl -fsSL --retry 3 "$DOCKER_KEY_URL" -o "$tmpkey"
  # exactly one primary key, Docker's: an extra key in the file would be trusted by apt too
  [[ "$(key_fpr "$tmpkey")" == "$DOCKER_KEY_FINGERPRINT" ]] \
    || die "Docker's apt key file is not exactly the key $DOCKER_KEY_FINGERPRINT"
  write_file /etc/apt/keyrings/docker.asc 644 < "$tmpkey"
  rm -f "$tmpkey"
fi
write_file /etc/apt/sources.list.d/docker.sources 644 <<EOF
# Managed by deploy/host/bootstrap.sh (key fingerprint $DOCKER_KEY_FINGERPRINT)
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $CODENAME
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
((WROTE)) && APT_UPDATED=0
write_file /etc/docker/daemon.json 644 < <(daemon_json)
daemon_changed=$WROTE
want=()
for p in "${!DOCKER_PINS[@]}"; do
  [[ "$(dpkg-query -W -f='${Version}' "$p" 2>/dev/null || true)" == "${DOCKER_PINS[$p]}" ]] && installed "$p" \
    || want+=("$p=${DOCKER_PINS[$p]}")
done
if ((${#want[@]})); then
  apt_update
  act "installed ${want[*]}" "${APT[@]}" install --no-install-recommends --allow-downgrades \
    --allow-change-held-packages "${want[@]}"
fi
holds="$(apt-mark showhold)"
for p in "${!DOCKER_PINS[@]}"; do
  grep -qx "$p" <<< "$holds" || act "held $p at its pinned version" quiet apt-mark hold "$p"
done
if ! ((DRY_RUN)); then
  dockerd --validate --config-file=/etc/docker/daemon.json >/dev/null \
    || die "/etc/docker/daemon.json rejected by dockerd --validate"
fi
if ! systemctl is-enabled --quiet docker 2>/dev/null; then act "enabled docker" systemctl enable docker; fi
# live-restore keeps running containers up while the daemon restarts. Restart only for a new
# daemon.json; otherwise start, which is a no-op while Docker is still starting at boot.
if ((daemon_changed)); then
  act "restarted docker (daemon.json)" systemctl restart docker
elif ! systemctl is-active --quiet docker; then
  if [[ "$(systemctl show -p ActiveState --value docker)" == activating ]]; then
    ((DRY_RUN)) || systemctl start docker   # waits for it; not a change
  else
    act "started docker" systemctl start docker
  fi
fi

# ---- 7. Users and SSH keys -------------------------------------------------------------------
log "Users: deploy user $DEPLOY_USER (docker, no sudo); admin/break-glass user $ADMIN_USER (sudo)"
append_key() {  # append_key <user> <.ssh dir> <authorized_keys> <key line>
  install -d -m 700 -o "$1" -g "$1" "$2"
  printf '%s\n' "$4" >> "$3"
  chown "$1:$1" "$3"
  chmod 600 "$3"
}
add_keys() {  # add_keys <user> <<< "key lines": append keys not yet present
  local user="$1" home dir file line body
  home="$(home_of "$user")"; dir="$home/.ssh"; file="$dir/authorized_keys"
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    body="$(awk '{for(i=1;i<=NF;i++) if ($i ~ /^(ssh-|ecdsa-|sk-)/) {print $(i+1); exit}}' <<< "$line")"
    [[ -n "$body" && -f "$file" ]] && grep -qF "$body" "$file" && continue
    act "SSH key for $user: $(ssh-keygen -l -f <(printf '%s\n' "$line") | awk '{print $2}')" \
      append_key "$user" "$dir" "$file" "$line"
  done
}
given_keys="$(printf '%s\n' "${KEY_INPUT[@]+"${KEY_INPUT[@]}"}" | valid_keys)"
root_keys="$(keys_of root loud)"

if ! getent group "$SSH_GROUP" >/dev/null; then act "group $SSH_GROUP" groupadd --system "$SSH_GROUP"; fi
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  act "user $DEPLOY_USER" useradd --create-home --shell /bin/bash --user-group "$DEPLOY_USER"
fi
for g in docker "$SSH_GROUP"; do
  in_group "$DEPLOY_USER" "$g" || act "$DEPLOY_USER in group $g" usermod -aG "$g" "$DEPLOY_USER"
done
if ! id "$ADMIN_USER" >/dev/null 2>&1; then
  act "admin user $ADMIN_USER" useradd --create-home --shell /bin/bash --user-group "$ADMIN_USER"
fi
for g in sudo "$SSH_GROUP"; do
  in_group "$ADMIN_USER" "$g" || act "$ADMIN_USER in group $g" usermod -aG "$g" "$ADMIN_USER"
done
# Saved, so a later run (the every-boot start script has no SUDO_USER) keeps this admin
write_file "$ADMIN_FILE" 644 <<< "$ADMIN_USER"
# The admin has no password: sudo without one, as cloud images do for their default user.
# One file per admin, never removed here: a changed admin adds sudo, it never takes it away.
echo "$ADMIN_USER ALL=(ALL) NOPASSWD:ALL" > /run/ihr-sudoers.tmp
visudo -cqf /run/ihr-sudoers.tmp || die "sudoers line for $ADMIN_USER rejected by visudo"
write_file "$(sudoers_file "$ADMIN_USER")" 440 < /run/ihr-sudoers.tmp
# The single file of earlier versions: removed only when it names this same admin
if [[ -f /etc/sudoers.d/90-indiehr-admin ]] && cmp -s /run/ihr-sudoers.tmp /etc/sudoers.d/90-indiehr-admin \
   && [[ -f "$(sudoers_file "$ADMIN_USER")" ]]; then
  act "replaced /etc/sudoers.d/90-indiehr-admin by $(sudoers_file "$ADMIN_USER")" \
    rm -f /etc/sudoers.d/90-indiehr-admin
fi
rm -f /run/ihr-sudoers.tmp

# Keys: the deploy user gets the given keys (or, if it has none yet, the admin's or root's);
# the admin keeps its own (or gets root's, or the given ones). Keys are never removed here.
if [[ -n "$given_keys" ]]; then add_keys "$DEPLOY_USER" <<< "$given_keys"; fi
if [[ -z "$(keys_of "$DEPLOY_USER")" ]]; then
  add_keys "$DEPLOY_USER" <<< "$(keys_of "$ADMIN_USER")"
  [[ -n "$(keys_of "$DEPLOY_USER")" ]] || add_keys "$DEPLOY_USER" <<< "$root_keys"
fi
if [[ -z "$(keys_of "$ADMIN_USER")" ]]; then
  add_keys "$ADMIN_USER" <<< "$root_keys"
  [[ -n "$(keys_of "$ADMIN_USER")" ]] || add_keys "$ADMIN_USER" <<< "$given_keys"
fi

# ---- 8. Firewall ------------------------------------------------------------------------------
SSH_PORTS="$(sshd_ports)"
[[ -n "$SSH_PORTS" ]] || die "sshd -T reports no port (is the sshd configuration valid? sshd -t); firewall left unchanged"
log "Firewall (ufw: deny in, allow out, limit $(paste -sd, - <<< "$SSH_PORTS")/tcp, allow 80+443+53; DOCKER-USER)"
ufw_added="$(ufw show added 2>/dev/null || true)"
rules_changed=0
# With IPV6=no, ufw drops all IPv6 traffic (an IPv6 SSH session included) and writes no IPv6
# rules: switch it on, then re-issue our rules so each gets its IPv6 twin.
if ! grep -qx 'IPV6=yes' /etc/default/ufw 2>/dev/null; then
  if grep -q '^IPV6=' /etc/default/ufw 2>/dev/null; then
    act "ufw IPV6=yes" sed -i -E 's/^IPV6=.*/IPV6=yes/' /etc/default/ufw
  else
    act "ufw IPV6=yes" bash -c 'echo IPV6=yes >> /etc/default/ufw'
  fi
  ufw_added="" rules_changed=1
fi
ufw_rule() {  # ufw_rule "<rule as 'ufw show added' prints it, without 'ufw '>" <comment>
  grep -Eq "^ufw $1( |\$)" <<< "$ufw_added" && return 0
  # shellcheck disable=SC2086  # the rule is words on purpose
  act "ufw $1" quiet ufw $1 comment "$2"
}
# The SSH rules go in first, before ufw is switched on, one per port sshd listens on.
for p in $SSH_PORTS; do ufw_rule "limit $p/tcp" "SSH, rate-limited (Q-E9)"; done
ufw_rule "allow 80/tcp" "HTTP (redirects to HTTPS)"
ufw_rule "allow 443/tcp" "HTTPS"
ufw_rule "allow 53" "DNS for the certificate check (acme-dns, P1-T8b2)"
ufw_rule "allow out 123/udp" "NTP to NIC/NPL"
# A plain allow for the SSH port (e.g. `ufw allow OpenSSH`) matches before our limit rule, so
# the rate limit would never apply. Removed only now, after the limit rule is in place.
ufw_now="$(ufw show added 2>/dev/null || true)"
for p in $SSH_PORTS; do
  while IFS= read -r r; do
    [[ -n "$r" ]] || continue
    r="${r#ufw }"; r="${r%% comment *}"
    # shellcheck disable=SC2086  # the rule is words on purpose
    act "ufw delete $r (it bypassed the SSH rate limit)" quiet ufw delete $r
  done < <(grep -E "$(ssh_bypass_re "$p")" <<< "$ufw_now" || true)
done
grep -q '^DEFAULT_INPUT_POLICY="DROP"' /etc/default/ufw || act "ufw default deny incoming" quiet ufw default deny incoming
grep -q '^DEFAULT_OUTPUT_POLICY="ACCEPT"' /etc/default/ufw || act "ufw default allow outgoing" quiet ufw default allow outgoing
for f in /etc/ufw/after.rules /etc/ufw/after6.rules; do
  [[ -f "$f" ]] || continue
  write_file "$f" "$(stat -c '%a' "$f")" < <(sed '/^# BEGIN IHR DOCKER-USER/,/^# END IHR DOCKER-USER/d' "$f"; docker_user_block)
  ((WROTE)) && rules_changed=1
done
if [[ "$(ufw status 2>/dev/null || true)" != "Status: active"* ]]; then
  act "ufw enabled" quiet ufw --force enable
elif ((rules_changed)); then
  act "ufw reloaded (DOCKER-USER rules / IPv6)" quiet ufw reload
fi

# ---- 9. SSH hardening (only once both users can sign in with a key) -----------------------------
log "SSH (key-only, no root, AllowGroups $SSH_GROUP)"
if ! ((DRY_RUN)); then
  [[ -n "$(keys_of "$DEPLOY_USER")" ]] || die "no SSH key for $DEPLOY_USER: pass --ssh-key-file; SSH left unchanged"
  [[ -n "$(keys_of "$ADMIN_USER")" ]] || die "no SSH key for $ADMIN_USER: pass --ssh-key-file; SSH left unchanged"
  if ! in_group "$DEPLOY_USER" "$SSH_GROUP" || ! in_group "$ADMIN_USER" "$SSH_GROUP"; then
    die "users not in $SSH_GROUP; SSH left unchanged"
  fi
fi
mkdir -p /run/sshd
previous=""
[[ -f "$SSHD_DROPIN" ]] && previous="$(cat "$SSHD_DROPIN")"
restore_sshd() {
  if [[ -n "$previous" ]]; then printf '%s\n' "$previous" > "$SSHD_DROPIN"; else rm -f "$SSHD_DROPIN"; fi
}
write_file "$SSHD_DROPIN" 644 < <(sshd_conf)
if ((WROTE)) && ! ((DRY_RUN)); then
  if ! sshd -t; then
    restore_sshd
    die "sshd -t rejected the new settings; the previous ones are back in place"
  fi
  # sshd -t checks syntax only. Check what sshd would really apply to both users (Match
  # blocks, and AllowUsers/DenyUsers/DenyGroups set elsewhere, which our file cannot undo).
  problems=""
  for u in "$DEPLOY_USER" "$ADMIN_USER"; do
    for a in "${CHECK_ADDRS[@]}"; do
      while IFS= read -r line; do
        [[ -n "$line" ]] && problems+="    $u from $a: $line"$'\n'
      done < <(sshd_login_problems "$u" "$a")
    done
  done
  if [[ -n "$problems" ]]; then
    restore_sshd
    printf '%s' "$problems" >&2
    die "other sshd settings would lock out or weaken these logins (above); SSH left unchanged. Remove them from /etc/ssh/sshd_config(.d), then re-run"
  fi
  # Ubuntu 24.04+ may start sshd from ssh.socket; a running sshd reloads, else the next
  # connection starts one with the new settings. Open sessions are not affected.
  if systemctl is-active --quiet ssh.service; then systemctl reload ssh.service; fi
fi

# ---- 10. fail2ban ---------------------------------------------------------------------------------
log "fail2ban (sshd jail, journal backend)"
write_file /etc/fail2ban/jail.d/60-indiehr-sshd.local 644 < <(fail2ban_conf "${SSH_PORTS//$'\n'/,}")
if ((WROTE)) && ! ((DRY_RUN)); then
  fail2ban-client -t >/dev/null || die "fail2ban rejected its configuration"
  systemctl restart fail2ban
fi
if ! systemctl is-enabled --quiet fail2ban 2>/dev/null || ! systemctl is-active --quiet fail2ban; then
  act "fail2ban enabled and started" systemctl enable --now fail2ban
fi

# ---- 11. Directories and an installed copy for re-runs ---------------------------------------------
log "Directories ($SRV_DIR) and $SELF_INSTALL"
if [[ ! -d "$SRV_DIR" ]]; then act "created $SRV_DIR" install -d -m 750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$SRV_DIR"; fi
if [[ "$(stat -c '%U:%G %a' "$SRV_DIR" 2>/dev/null)" != "$DEPLOY_USER:$DEPLOY_USER 750" ]] && ! ((DRY_RUN)); then
  act "$SRV_DIR owned by $DEPLOY_USER, mode 750" bash -c "chown '$DEPLOY_USER:$DEPLOY_USER' '$SRV_DIR' && chmod 750 '$SRV_DIR'"
fi
if [[ -f "${BASH_SOURCE[0]}" && "$(realpath "${BASH_SOURCE[0]}")" != "$SELF_INSTALL" ]]; then
  write_file "$SELF_INSTALL" 755 < "${BASH_SOURCE[0]}"
fi

# ---- Summary ------------------------------------------------------------------------------------------
echo
if ((${#CHANGES[@]} == 0)); then
  echo "No changes."
else
  ((DRY_RUN)) && echo "Dry run: ${#CHANGES[@]} change(s) would be made." || echo "${#CHANGES[@]} change(s) made."
fi
[[ -f /run/reboot-required ]] && echo "A reboot is pending (package updates). Plan one: sudo systemctl reboot"
echo "Sign in as $DEPLOY_USER (deploys) or $ADMIN_USER (admin, sudo). Root and passwords are off."
echo "Verify: sudo $SELF_INSTALL --check"
