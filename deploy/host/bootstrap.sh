#!/usr/bin/env bash
# IndieHR host bootstrap (P1-T8b1, ADR-011): turns a fresh Ubuntu 26.04 LTS (or 24.04 LTS)
# server into a staging/production host. Provider-neutral: run it as root over SSH from the
# operator's machine, or (installed copy) from a provider's every-boot start script.
#
#   sudo bash bootstrap.sh [--ssh-key "<public key>"]... [--ssh-key-file FILE] [--admin-user NAME]
#                          [--change-admin] [--no-upgrade] [--dry-run]
#                          [--instance <environment> --suffix <suffix>]
#   --instance: which instance this server is (P1-T32 E3, ADR-020 §3): production-<n>, staging or
#               proof, with its hospitals' domain suffix, written to /srv/indiehr/INSTANCE (root's,
#               mode 644). deploy.sh refuses a server without it or that names another instance.
#               Set once: a different value is refused (a server changes instance by being rebuilt)
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
# The journal is a 14-day buffer (owner, Q-O3; P1-T9c): Fluent Bit ships every line, redacted, to
# the log store (180 days, CERT-In) and the daily locked archive (400 days, DPDP Rules 2025 8(3)).
# 15 days by time with one file a day, so at least 14 full days stay; the size cap is room for
# about three times the measured volume (scripts/staging.sh logs-volume): see deploy/host/README.md.
JOURNAL_MAX_RETENTION="15day"
JOURNAL_MAX_FILE="1day"
JOURNAL_SYSTEM_MAX_USE="8G"
JOURNAL_SYSTEM_KEEP_FREE="15G"
DEPLOY_USER="ihr"
SSH_GROUP="ihr-ssh"
SRV_DIR="/srv/indiehr"
# Which instance this server is (P1-T32 E3, ADR-020 §3): read by scripts/deploy.sh before it sends
# anything (scripts/instance_check.py). Root's, so the deploy user can never write one deploy.sh
# accepts (it owns $SRV_DIR, so it can remove it: then deploy.sh refuses the server)
INSTANCE_FILE="$SRV_DIR/INSTANCE"
# One regex, equal to environment.py's (scripts/test_environment_regex.py); the suffix as staging.sh's DOMAIN_RE
ENV_RE='^(production-[1-9][0-9]{0,2}|staging|proof)$'
DOMAIN_RE='^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
SWAPFILE="/swapfile"
# acme-dns (P1-T8b2) publishes port 53; systemd-resolved's local stub would hold it
RESOLVED_DROPIN="/etc/systemd/resolved.conf.d/60-indiehr.conf"
RESOLV_TARGET="/run/systemd/resolve/resolv.conf"
SWAP_SIZE="2G"
SELF_INSTALL="/usr/local/sbin/ihr-bootstrap"
# Host signals for Prometheus (P1-T9a): a timer writes files node_exporter reads, so no container
# needs the Docker socket (deploy/monitoring/host-metrics.sh, via scripts/staging.sh)
TEXTFILE_DIR="/var/lib/indiehr/textfile"
METRICS_UNIT="ihr-host-metrics"
# chronyd answers root only (cmdport 0 below): the timer's unit reads it as root into this
# root-owned folder (never the deploy user's: a planted link there would be written through as
# root), and host-metrics.sh parses the files. The deploy user never joins _chrony (that socket
# takes every control command).
CHRONY_READ_DIR="/run/indiehr-chrony"
# Host audit (P1-T27c, Q-K139): auditd from the Ubuntu archive, at least this version per release
# (launchpad.net/ubuntu/+source/audit, checked 2026-11-22; docs/VERSIONS.md). Installed at exactly
# this version when missing or older, never held: Ubuntu's security updates still apply. When the
# archive no longer offers it (a newer update replaced it), raise the pin.
declare -A AUDITD_PINS=([26.04]="1:4.1.2-1ubuntu0.1" [24.04]="1:3.1.2-2.1ubuntu0.1")
AUDIT_RULES_FILE="/etc/audit/rules.d/60-indiehr.rules"
AUDIT_RULE_COUNT="10"   # rule lines in audit_rules; the alert HostAuditRulesMissing uses the same number
# The kernel's audit state and loaded rules, read each minute by the timer's root step (like chrony)
AUDIT_READ_DIR="/run/indiehr-audit"
STATE_DIR="/etc/indiehr"
ADMIN_FILE="$STATE_DIR/admin-user"        # the admin chosen on the first run (kept)
INSTALLED_FILE="$STATE_DIR/installed-at"  # first run's date: --check compares log age with it
USER_RE='^[a-z_][a-z0-9_.-]{0,31}$'
BASE_PACKAGES=(ca-certificates curl gnupg ufw fail2ban python3-systemd chrony
  unattended-upgrades rsync iptables openssh-server python3 tzdata)

# ---- Arguments -------------------------------------------------------------------------
MODE=apply DRY_RUN=0 UPGRADE=1 ADMIN_USER="" CHANGE_ADMIN=0 KEY_INPUT=() INSTANCE_ENV="" INSTANCE_SUFFIX=""
usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; }
while (($#)); do
  case "$1" in
    --check) MODE=check ;;
    --dry-run) DRY_RUN=1 ;;
    --no-upgrade) UPGRADE=0 ;;
    --admin-user) ADMIN_USER="${2:?--admin-user NAME}"; shift ;;
    --change-admin) CHANGE_ADMIN=1 ;;
    --instance) INSTANCE_ENV="${2:?--instance production-<n>|staging|proof}"; shift ;;
    --suffix) INSTANCE_SUFFIX="${2:?--suffix <domain suffix of the hospitals>}"; shift ;;
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
if [[ -n "$INSTANCE_ENV" || -n "$INSTANCE_SUFFIX" ]]; then
  [[ -n "$INSTANCE_ENV" && -n "$INSTANCE_SUFFIX" ]] || { echo "--instance and --suffix go together" >&2; exit 2; }
  [[ "$INSTANCE_ENV" =~ $ENV_RE ]] || { echo "--instance: production-<n> (1-999), staging or proof, not $(printf '%q' "$INSTANCE_ENV")" >&2; exit 2; }
  [[ "$INSTANCE_SUFFIX" =~ $DOMAIN_RE ]] || { echo "--suffix: a lower-case domain name, not $(printf '%q' "$INSTANCE_SUFFIX")" >&2; exit 2; }
fi
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
AUDITD_PIN="${AUDITD_PINS[$VERSION_ID]:?no auditd version pinned for Ubuntu $VERSION_ID}"
declare -A DOCKER_PINS=(
  [docker-ce]="${DOCKER_CE_VERSION}${PKG_SUFFIX}"
  [docker-ce-cli]="${DOCKER_CE_VERSION}${PKG_SUFFIX}"
  [containerd.io]="${CONTAINERD_VERSION}${PKG_SUFFIX}"
  [docker-buildx-plugin]="${BUILDX_VERSION}${PKG_SUFFIX}"
  [docker-compose-plugin]="${COMPOSE_VERSION}${PKG_SUFFIX}"
)

# ---- Desired file contents (shared by apply and --check) ---------------------------------
instance_conf() {  # $INSTANCE_FILE: what scripts/instance_check.py reads (comment lines, environment=, suffix=)
  echo "# Managed by deploy/host/bootstrap.sh --instance (P1-T32 E3, ADR-020 §3): which instance this"
  echo "# server is. scripts/deploy.sh refuses any other; a server changes instance by being rebuilt."
  echo "environment=$INSTANCE_ENV"
  echo "suffix=$INSTANCE_SUFFIX"
}
instance_named() {  # "<environment> at <suffix>" as $INSTANCE_FILE names them (empty when it does not)
  echo "$(sed -n 's/^environment=//p' "$INSTANCE_FILE" 2>/dev/null | tail -1) at $(sed -n 's/^suffix=//p' "$INSTANCE_FILE" 2>/dev/null | tail -1)"
}
instance_valid() {  # does $INSTANCE_FILE name a valid environment and suffix?
  local env suffix
  env="$(sed -n 's/^environment=//p' "$INSTANCE_FILE" 2>/dev/null | tail -1)"
  suffix="$(sed -n 's/^suffix=//p' "$INSTANCE_FILE" 2>/dev/null | tail -1)"
  [[ "$env" =~ $ENV_RE && "$suffix" =~ $DOMAIN_RE ]]
}
instance_owner_ok() {  # instance_owner_ok <stat '%F %U:%G %a'> <environment>: root's, mode 644; or a proof's
  # written by deploy.sh --local-proof as the deploy user (mode 644), the rule scripts/instance_check.py applies
  [[ "$1" == "regular file root:root 644" ]] && return 0
  [[ "$2" == proof && "$1" =~ ^regular\ file\ ${DEPLOY_USER}:[a-z_][a-z0-9_-]*\ 644$ ]]
}
instance_conflict() {  # why --instance cannot be written (nothing when it can): never over another instance
  local f="$INSTANCE_FILE" env suffix
  [[ -e "$f" || -L "$f" ]] || return 0
  if [[ -L "$f" || ! -f "$f" ]]; then
    echo "$f is not a regular file: remove it (it was not written by this script), then re-run"
    return 0
  fi
  env="$(sed -n 's/^environment=//p' "$f" | tail -1)"
  suffix="$(sed -n 's/^suffix=//p' "$f" | tail -1)"
  [[ "$env" == "$INSTANCE_ENV" && "$suffix" == "$INSTANCE_SUFFIX" ]] && return 0
  echo "this server is already $(printf '%q' "${env:-unknown}") at $(printf '%q' "${suffix:-unknown}") ($f); it does not become $INSTANCE_ENV at $INSTANCE_SUFFIX: a server changes instance only by being rebuilt (ADR-020 §3 E3)"
}
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
# Managed by deploy/host/bootstrap.sh. A 14-day buffer: every line is shipped, redacted, to the
# log store (180 days, CERT-In) and the daily locked archive (400 days); deploy/staging/README.md.
# Container logs arrive here via Docker's journald log driver. The size cap must not cut the 14
# days short: watch journalctl --disk-usage and scripts/staging.sh logs-volume.
[Journal]
Storage=persistent
Compress=yes
MaxRetentionSec=$JOURNAL_MAX_RETENTION
MaxFileSec=$JOURNAL_MAX_FILE
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
  "log-opts": {"tag": "docker/{{.Name}}", "labels": "com.docker.compose.service"},
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

# An executable's real path, symlinks resolved: an audit path rule binds to one inode, and execve
# records the target (on Debian and Ubuntu /usr/bin/psql is a link to postgresql-common's pg_wrapper),
# so a rule on the link would load, be counted and never match (P1-T27 review S3). The path itself
# when it does not resolve (the tool is not installed yet).
audit_path() {
  local resolved
  resolved="$(readlink -f "$1" 2>/dev/null || true)"
  echo "${resolved:-$1}"
}
audit_rules() {
  local docker ctr runc nsenter psql
  docker="$(audit_path /usr/bin/docker)"; ctr="$(audit_path /usr/bin/ctr)"; runc="$(audit_path /usr/bin/runc)"
  nsenter="$(audit_path /usr/bin/nsenter)"; psql="$(audit_path /usr/bin/psql)"
  cat <<EOF
## Managed by deploy/host/bootstrap.sh (P1-T27c, Q-K139); deploy/host/README.md "Host audit".
## Who ran a root-equivalent tool from a login session: execve(at) of docker, ctr, runc, nsenter
## and psql. auid is the login uid that pam_loginuid sets at SSH login; it survives sudo and is
## unset for everything Docker and systemd start, so the stack's own processes are not recorded.
## b64 and b32: a 32-bit call would otherwise go past the 64-bit rule. Keys ihr-*: Fluent Bit
## ships only these events (SYSCALL and EXECVE records, arguments shaped). No "-e 2": a removed
## rule is alerted (HostAuditRulesMissing) instead of locking the rules until a reboot.
-a always,exit -F arch=b64 -S execve,execveat -F path=$docker -F auid>=1000 -F auid!=unset -k ihr-docker
-a always,exit -F arch=b32 -S execve,execveat -F path=$docker -F auid>=1000 -F auid!=unset -k ihr-docker
-a always,exit -F arch=b64 -S execve,execveat -F path=$ctr -F auid>=1000 -F auid!=unset -k ihr-docker
-a always,exit -F arch=b32 -S execve,execveat -F path=$ctr -F auid>=1000 -F auid!=unset -k ihr-docker
-a always,exit -F arch=b64 -S execve,execveat -F path=$runc -F auid>=1000 -F auid!=unset -k ihr-docker
-a always,exit -F arch=b32 -S execve,execveat -F path=$runc -F auid>=1000 -F auid!=unset -k ihr-docker
-a always,exit -F arch=b64 -S execve,execveat -F path=$nsenter -F auid>=1000 -F auid!=unset -k ihr-nsenter
-a always,exit -F arch=b32 -S execve,execveat -F path=$nsenter -F auid>=1000 -F auid!=unset -k ihr-nsenter
-a always,exit -F arch=b64 -S execve,execveat -F path=$psql -F auid>=1000 -F auid!=unset -k ihr-psql
-a always,exit -F arch=b32 -S execve,execveat -F path=$psql -F auid>=1000 -F auid!=unset -k ihr-psql
EOF
}

metrics_service() {
  cat <<EOF
# Managed by deploy/host/bootstrap.sh (P1-T9a). Writes $TEXTFILE_DIR/*.prom for node_exporter:
# container states, journal size, NTP source, backups, client-key age. Does nothing until the
# stack has monitoring (IHR_MONITORING in $SRV_DIR/deploy/staging/.env).
[Unit]
Description=IndieHR host metrics for Prometheus
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
User=$DEPLOY_USER
Group=$DEPLOY_USER
# Reads the system journal's own "Suppressed" lines (the deploy user is not in this group)
SupplementaryGroups=systemd-journal
WorkingDirectory=$SRV_DIR
# Skipped (not failed) until the first deploy has put the stack's scripts here
ExecCondition=/usr/bin/test -x $SRV_DIR/scripts/staging.sh
# "+": as root, only this fixed line (never a file the deploy user can change). chrony's sources
# and tracking as CSV into $CHRONY_READ_DIR; when chronyc fails both files go, so the metrics
# show "unknown" (ihr_ntp_reading_ok 0) instead of a false "not an Indian source".
ExecStartPre=+/bin/sh -c 'install -d -m 755 -o root -g root $CHRONY_READ_DIR && cd $CHRONY_READ_DIR && if /usr/bin/chronyc -N -c sources > sources.new 2>/dev/null && /usr/bin/chronyc -c tracking > tracking.new 2>/dev/null; then chmod 644 sources.new tracking.new && mv -f sources.new sources.csv && mv -f tracking.new tracking.csv; else rm -f sources.new tracking.new sources.csv tracking.csv; fi'
# The same for the host audit rules (P1-T27c): the kernel's audit state and its loaded rules into
# $AUDIT_READ_DIR; host-metrics.sh counts the ihr-* rules (alert HostAuditRulesMissing). When
# auditctl fails (auditd removed) both files go, and the metrics say "not read".
ExecStartPre=+/bin/sh -c 'install -d -m 755 -o root -g root $AUDIT_READ_DIR && cd $AUDIT_READ_DIR && if /usr/sbin/auditctl -s > status.new 2>/dev/null && /usr/sbin/auditctl -l > rules.new 2>/dev/null; then chmod 644 status.new rules.new && mv -f status.new status.txt && mv -f rules.new rules.txt; else rm -f status.new rules.new status.txt rules.txt; fi'
ExecStart=$SRV_DIR/scripts/staging.sh monitoring-textfile
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
TimeoutStartSec=5min
EOF
}

metrics_timer() {
  cat <<EOF
# Managed by deploy/host/bootstrap.sh (P1-T9a): every minute
[Unit]
Description=IndieHR host metrics for Prometheus, every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=10s

[Install]
WantedBy=timers.target
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
version_ge() { [[ -n "$1" ]] && dpkg --compare-versions "$1" ge "$2"; }
# ihr-* rules the kernel holds now (auditctl -l prints "-F key=ihr-…" or, for a watch, "-k ihr-…")
audit_loaded() { auditctl -l 2>/dev/null | grep -cE -- '(-k |-F key=)ihr-[a-z0-9-]+$' || true; }
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
  for kv in "Storage=persistent" "MaxRetentionSec=$JOURNAL_MAX_RETENTION" "MaxFileSec=$JOURNAL_MAX_FILE" \
            "SystemMaxUse=$JOURNAL_SYSTEM_MAX_USE" "SystemKeepFree=$JOURNAL_SYSTEM_KEEP_FREE"; do
    v="$(grep -E "^${kv%%=*}=" <<< "$out" | tail -1)"
    expect "journald $kv (effective: ${v:-default})" [ "$v" == "$kv" ]
  done
  expect "persistent journal directory /var/log/journal" test -d /var/log/journal
  info "journal: $(journalctl --disk-usage 2>/dev/null | sed 's/^Archived and active journals take up //')"
  check_log_retention

  # host audit (P1-T27c): auditd's rules for root-equivalent tools, collected by journald
  v="$(dpkg-query -W -f='${Version}' auditd 2>/dev/null || true)"
  expect "auditd $AUDITD_PIN or later (found ${v:-none})" version_ge "$v" "$AUDITD_PIN"
  expect "auditd running" systemctl is-active --quiet auditd
  expect "audit rules as managed ($AUDIT_RULES_FILE)" same_content "$AUDIT_RULES_FILE" "$(audit_rules)"
  v="$(audit_loaded)"
  expect "kernel holds the $AUDIT_RULE_COUNT ihr-* audit rules (auditctl -l; found ${v:-0})" [ "${v:-0}" == "$AUDIT_RULE_COUNT" ]
  v="$(auditctl -s 2>/dev/null | awk '$1 == "enabled" {print $2}')"
  expect "kernel auditing on and not locked (auditctl -s: enabled 1; found ${v:-unknown})" [ "$v" == 1 ]
  expect "journald collects audit records (systemd-journald-audit.socket active)" \
    systemctl is-active --quiet systemd-journald-audit.socket
  # A canary through the whole local path: a user audit message must show in the journal
  v="ihr-audit-canary-$(od -An -N6 -tx1 /dev/urandom | tr -d ' \n')"
  auditctl -m "$v" >/dev/null 2>&1 || true
  sleep 1
  expect "an audit message reaches the journal (_TRANSPORT=audit)" \
    grep -q -- "$v" <<< "$(journalctl -q --no-pager _TRANSPORT=audit --since -2min -o cat 2>/dev/null || true)"

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
  # which instance this server is (P1-T32 E3): deploy.sh refuses a server without it
  if [[ -e "$INSTANCE_FILE" || -L "$INSTANCE_FILE" ]]; then
    expect "$INSTANCE_FILE a regular file, root:root 644 (a proof's may be $DEPLOY_USER's)" instance_owner_ok \
      "$(stat -c '%F %U:%G %a' "$INSTANCE_FILE" 2>/dev/null)" "$(sed -n 's/^environment=//p' "$INSTANCE_FILE" 2>/dev/null | tail -1)"
    v="$(instance_named)"
    expect "$INSTANCE_FILE names an environment and a suffix ($(printf '%q' "$v"))" instance_valid
    if [[ -n "$INSTANCE_ENV" ]]; then
      expect "$INSTANCE_FILE is $INSTANCE_ENV at $INSTANCE_SUFFIX" same_content "$INSTANCE_FILE" "$(instance_conf)"
    fi
  elif [[ -n "$INSTANCE_ENV" ]]; then
    fail "$INSTANCE_FILE is $INSTANCE_ENV at $INSTANCE_SUFFIX (missing)"
  else
    info "no $INSTANCE_FILE yet: deploy.sh refuses this server until bootstrap.sh --instance <environment> --suffix <suffix>"
  fi

  # host metrics for Prometheus (P1-T9a)
  expect "$TEXTFILE_DIR owned by $DEPLOY_USER, mode 755" \
    [ "$(stat -c '%U %a' "$TEXTFILE_DIR" 2>/dev/null)" == "$DEPLOY_USER 755" ]
  expect "$METRICS_UNIT.service as managed" same_content "/etc/systemd/system/$METRICS_UNIT.service" "$(metrics_service)"
  expect "$METRICS_UNIT.timer as managed" same_content "/etc/systemd/system/$METRICS_UNIT.timer" "$(metrics_timer)"
  expect "$METRICS_UNIT.timer enabled and active" systemctl is-active --quiet "$METRICS_UNIT.timer"
  if [[ -f "$SRV_DIR/deploy/staging/.env" ]] && grep -q '^IHR_MONITORING=' "$SRV_DIR/deploy/staging/.env"; then
    v="$(find "$TEXTFILE_DIR/ihr_host.prom" -mmin -5 2>/dev/null)"
    expect "host metrics written in the last 5 minutes ($TEXTFILE_DIR/ihr_host.prom)" [ -n "$v" ]
    # The root step of the unit read chrony (the deploy user cannot)
    v="$(find "$CHRONY_READ_DIR/sources.csv" "$CHRONY_READ_DIR/tracking.csv" -mmin -5 2>/dev/null | grep -c . || true)"
    expect "chrony read for the metrics in the last 5 minutes ($CHRONY_READ_DIR)" [ "$v" -eq 2 ]
    expect "the timer reports chrony's state (ihr_ntp_reading_ok 1)" grep -qx 'ihr_ntp_reading_ok 1' "$TEXTFILE_DIR/ihr_host.prom"
    expect "$CHRONY_READ_DIR owned by root, mode 755" [ "$(stat -c '%U %a' "$CHRONY_READ_DIR" 2>/dev/null)" == "root 755" ]
    # ...and the audit rules (P1-T27c, HostAuditRulesMissing)
    v="$(find "$AUDIT_READ_DIR/status.txt" "$AUDIT_READ_DIR/rules.txt" -mmin -5 2>/dev/null | grep -c . || true)"
    expect "audit rules read for the metrics in the last 5 minutes ($AUDIT_READ_DIR)" [ "$v" -eq 2 ]
    expect "the timer reports the audit rules (ihr_host_audit_rule_keys $AUDIT_RULE_COUNT)" \
      grep -qx "ihr_host_audit_rule_keys $AUDIT_RULE_COUNT" "$TEXTFILE_DIR/ihr_host.prom"
  else
    info "monitoring not set up in $SRV_DIR/deploy/staging/.env yet: the timer writes nothing"
  fi

  echo
  if ((FAILS)); then echo "$FAILS check(s) FAILED"; return 1; fi
  echo "All checks passed."
}

check_log_retention() {
  # journald's real cap is min(SystemMaxUse, used + free - SystemKeepFree): the journal shares
  # the disk with Docker, so it deletes old logs early as the disk fills. FAIL when entries
  # younger than 14 days are already gone (the oldest entry is younger than both 14 days and
  # this host's first run): the buffer the log shipper relies on is too short. A WARNING when
  # free space already shrinks the cap.
  local now first oldest host_days log_days free_g used_g keep_g max_g
  now="$(date +%s)"
  first="$(head -n1 "$INSTALLED_FILE" 2>/dev/null || true)"
  oldest="$(journalctl -q -o short-unix --no-pager 2>/dev/null | head -n1 | cut -d. -f1)"
  if [[ "$first" =~ ^[0-9]+$ && "$oldest" =~ ^[0-9]+$ ]]; then
    host_days=$(((now - first) / 86400)); log_days=$(((now - oldest) / 86400))
    if ((log_days < 14 && host_days > log_days + 1)); then
      fail "journal keeps 14 days: oldest entry is $log_days days old, host set up $host_days days ago (logs were deleted early: free disk space or raise the cap)"
    else pass "journal keeps 14 days (oldest entry $log_days days old, host set up $host_days days ago)"; fi
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
# A server changes instance only by being rebuilt (P1-T32 E3): refused before anything changes
if [[ -n "$INSTANCE_ENV" ]]; then
  why="$(instance_conflict)"
  [[ -z "$why" ]] || die "$why"
fi
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
log "Logs (journald persistent, a 14-day buffer: MaxRetentionSec=$JOURNAL_MAX_RETENTION, cap $JOURNAL_SYSTEM_MAX_USE)"
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
  # journald applies a shorter MaxRetentionSec only when it rotates: now, not within a month.
  # Lines older than 15 days are expected to be in the log store already (Fluent Bit reads the
  # whole journal on its first start): deploy the log stack before shortening an old journal.
  journalctl --rotate >/dev/null 2>&1 || true
fi

# ---- 3b. Host audit: auditd rules, collected by journald (P1-T27c, Q-K139) ---------------------
log "Host audit (auditd $AUDITD_PIN or later; $AUDIT_RULE_COUNT rules in $AUDIT_RULES_FILE; journald collects)"
v="$(dpkg-query -W -f='${Version}' auditd 2>/dev/null || true)"
if ! installed auditd || ! version_ge "$v" "$AUDITD_PIN"; then
  apt_update
  act "installed auditd=$AUDITD_PIN" "${APT[@]}" install --no-install-recommends "auditd=$AUDITD_PIN"
fi
write_file "$AUDIT_RULES_FILE" 640 < <(audit_rules); audit_changed=$WROTE
if ! systemctl is-enabled --quiet auditd 2>/dev/null || ! systemctl is-active --quiet auditd; then
  act "auditd enabled and started" systemctl enable --now auditd
fi
# Loaded when the file changed or the kernel lacks one of ours (auditd's own start loads them
# too, at every boot). The marker line says a reload was ours (root, uid 0).
loaded="$(audit_loaded)"
if ((audit_changed)) || [[ "$loaded" != "$AUDIT_RULE_COUNT" ]]; then
  if ((DRY_RUN)); then
    CHANGES+=("load the audit rules"); echo "    would: augenrules --load"
  else
    augenrules --load >/dev/null || die "augenrules --load failed: the audit rules are not loaded"
    loaded="$(audit_loaded)"
    [[ "$loaded" == "$AUDIT_RULE_COUNT" ]] \
      || die "the kernel holds $loaded ihr-* audit rules after augenrules --load, not $AUDIT_RULE_COUNT (auditctl -l; another rules file with -e 2?)"
    logger -t ihr-bootstrap "audit-rules=loaded keys=$loaded"
    changed "audit rules loaded ($loaded ihr-* rules)"
  fi
fi
# journald reads the kernel's audit records only through this socket, which Ubuntu 26.04
# (systemd 259) ships disabled. journald takes a new socket only when it starts: restart it.
if ! systemctl is-enabled --quiet systemd-journald-audit.socket 2>/dev/null \
   || ! systemctl is-active --quiet systemd-journald-audit.socket; then
  act "journald collects audit records (systemd-journald-audit.socket)" \
    systemctl enable --now systemd-journald-audit.socket
  ((DRY_RUN)) || systemctl restart systemd-journald
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

# Which instance this server is (P1-T32 E3): root's, mode 644 (the deploy user reads it). Without
# --instance an INSTANCE already there is kept as it is
if [[ -n "$INSTANCE_ENV" ]]; then
  log "Instance ($INSTANCE_FILE: $INSTANCE_ENV at $INSTANCE_SUFFIX; ADR-020 §3 E3)"
  write_file "$INSTANCE_FILE" 644 < <(instance_conf)
fi

if [[ -f "${BASH_SOURCE[0]}" && "$(realpath "${BASH_SOURCE[0]}")" != "$SELF_INSTALL" ]]; then
  write_file "$SELF_INSTALL" 755 < "${BASH_SOURCE[0]}"
fi

# ---- 12. Host metrics for Prometheus (P1-T9a) -------------------------------------------------------
log "Host metrics timer ($METRICS_UNIT.timer -> $TEXTFILE_DIR)"
if [[ "$(stat -c '%U:%G %a' "$TEXTFILE_DIR" 2>/dev/null)" != "$DEPLOY_USER:$DEPLOY_USER 755" ]]; then
  act "$TEXTFILE_DIR owned by $DEPLOY_USER, mode 755" \
    install -d -m 755 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$TEXTFILE_DIR"
fi
write_file "/etc/systemd/system/$METRICS_UNIT.service" 644 < <(metrics_service); units_changed=$WROTE
write_file "/etc/systemd/system/$METRICS_UNIT.timer" 644 < <(metrics_timer); ((WROTE)) && units_changed=1
if ((units_changed)) && ! ((DRY_RUN)); then systemctl daemon-reload; fi
if ! systemctl is-enabled --quiet "$METRICS_UNIT.timer" 2>/dev/null || ! systemctl is-active --quiet "$METRICS_UNIT.timer"; then
  act "$METRICS_UNIT.timer enabled and started" systemctl enable --now "$METRICS_UNIT.timer"
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
