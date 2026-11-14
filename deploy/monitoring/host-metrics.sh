#!/usr/bin/env bash
# shellcheck disable=SC2034  # the per-service arrays are read through family()'s name reference
# Host-level signals for Prometheus without giving any container the Docker socket (P1-T9a).
# Writes <out>/ihr_host.prom for node_exporter's textfile collector (compose.monitoring.yaml):
#   - per long-running service of the stack: present, running, unhealthy, restart count, memory
#     working set and its limit, OOM kills (docker inspect and cgroup v2 files, read here on the
#     host). One-shot set-up services (migrate, traefik-data, ...) are never reported: they exit
#     by design, and an exited one is not a service that is down;
#   - the journal's size and cap, and lines journald suppressed in the last 15 minutes;
#   - whether chrony follows one of the NIC/NPL time servers, and the clock's offset (CERT-In).
#     chronyd answers root only (bootstrap.sh: cmdport 0), so the timer's unit reads it with root
#     rights first and leaves the CSV output in a root-owned folder (--chrony-dir); a reading
#     that is missing or old writes no value at all, never a false 0;
#   - the backup service's own markers (the files deploy/docker/backup/health.sh checks).
# Run every minute by scripts/staging.sh monitoring-textfile (a systemd timer on the server,
# deploy/host/bootstrap.sh). Prints nothing but errors: no secret, no log line, no environment
# variable of any container is ever read or printed. Only states, counts, sizes and times.
#
#   host-metrics.sh --out DIR --project NAME --services "svc ..." [--backup-spool-max-mb N]
#                   [--chrony-dir DIR]
# IHR_CGROUP_ROOT (tests only) replaces /sys/fs/cgroup.
set -euo pipefail
export LC_ALL=C

OUT="" PROJECT="" SERVICES="" SPOOL_MAX_MB="" CHRONY_DIR="/run/indiehr-chrony"
CGROUP_ROOT="${IHR_CGROUP_ROOT:-/sys/fs/cgroup}"
while (($#)); do
  case "$1" in
    --out) OUT="${2:?}"; shift ;;
    --project) PROJECT="${2:?}"; shift ;;
    --services) SERVICES="${2:-}"; shift ;;
    --backup-spool-max-mb) SPOOL_MAX_MB="${2:?}"; shift ;;
    --chrony-dir) CHRONY_DIR="${2:?}"; shift ;;
    *) echo "host-metrics: unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
[[ -d "$OUT" && -n "$PROJECT" ]] || { echo "usage: host-metrics.sh --out DIR --project NAME --services \"...\"" >&2; exit 2; }
[[ "$PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || { echo "host-metrics: bad project name" >&2; exit 2; }
NAME_RE='^[a-z0-9][a-z0-9-]*$'
# Set-up services that run once and exit (restart: "no"); scripts/staging.sh leaves them out of
# --services too. Never reported, even when --services is empty (compose could not be read).
ONESHOT_RE='^(migrate|admin|keytool|s3-tool|traefik-data|alertmanager-data|logs-data|grafana-data|audit-watch-data)$'

LINES=()
metric() { LINES+=("$1"); }  # metric '<name>{labels} <value>'
describe() { LINES+=("# HELP $1 $2" "# TYPE $1 ${3:-gauge}"); }

# ---- Containers ---------------------------------------------------------------------------
declare -A present running unhealthy restarts memory limit ooms wanted=()
for s in $SERVICES; do
  [[ "$s" =~ $NAME_RE && ! "$s" =~ $ONESHOT_RE ]] || continue
  wanted[$s]=1; present[$s]=0; running[$s]=0; unhealthy[$s]=0
done
working_set() {  # working_set <cgroup dir>: memory.current minus inactive_file (as docker stats)
  local cur inactive
  [[ -r "$1/memory.current" ]] || return 1
  cur="$(< "$1/memory.current")"
  inactive="$(awk '$1 == "inactive_file" {print $2}' "$1/memory.stat" 2>/dev/null || true)"
  [[ "$cur" =~ ^[0-9]+$ ]] || return 1
  [[ "$inactive" =~ ^[0-9]+$ ]] || inactive=0
  echo $((cur > inactive ? cur - inactive : 0))
}
mapfile -t ids < <(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" \
  --filter "label=com.docker.compose.oneoff=False")
if ((${#ids[@]})); then
  # service|status|health|restarts|id|memory limit: states and numbers only
  while IFS='|' read -r svc status health count id mem; do
    [[ "$svc" =~ $NAME_RE && ! "$svc" =~ $ONESHOT_RE ]] || continue
    # Only the services the stack should have running, when that list is known
    ((${#wanted[@]} == 0)) || [[ -n "${wanted[$svc]:-}" ]] || continue
    present[$svc]=1
    # Several containers of one service (a recreate in progress): the running one counts
    if [[ "$status" == running ]]; then
      running[$svc]=1
      [[ "$health" == unhealthy ]] && unhealthy[$svc]=1 || unhealthy[$svc]=${unhealthy[$svc]:-0}
      restarts[$svc]=$count
      limit[$svc]=$mem
      for d in "$CGROUP_ROOT/system.slice/docker-$id.scope" "$CGROUP_ROOT/docker/$id"; do
        if ws="$(working_set "$d")"; then
          memory[$svc]=$ws
          ooms[$svc]="$(awk '$1 == "oom_kill" {print $2}' "$d/memory.events" 2>/dev/null || true)"
          break
        fi
      done
    else
      running[$svc]=${running[$svc]:-0}; unhealthy[$svc]=${unhealthy[$svc]:-0}
      [[ -n "${restarts[$svc]:-}" ]] || restarts[$svc]=$count
    fi
  done < <(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}|{{.State.Status}}|{{with index .State "Health"}}{{index . "Status"}}{{else}}none{{end}}|{{.RestartCount}}|{{.Id}}|{{.HostConfig.Memory}}' "${ids[@]}")
fi
family() {  # family <name> <array name> <help> [type]: one line per service, if any
  local -n values="$2"
  ((${#values[@]})) || return 0
  describe "$1" "$3" "${4:-gauge}"
  local k
  for k in "${!values[@]}"; do
    if [[ "${values[$k]}" =~ ^[0-9]+$ ]]; then metric "$1{service=\"$k\"} ${values[$k]}"; fi
  done
}
family ihr_container_present present "1 when the service has a container (running or not), 0 when it is missing"
family ihr_container_running running "1 when the service's container is running"
family ihr_container_unhealthy unhealthy "1 when Docker's health check reports unhealthy"
family ihr_container_restarts_total restarts "Docker's restart count of the service's container" counter
family ihr_container_memory_working_set_bytes memory "Memory in use without reclaimable page cache (cgroup v2 memory.current minus inactive_file, as docker stats)"
family ihr_container_memory_limit_bytes limit "The container's mem_limit (0: none)"
family ihr_container_oom_kills_total ooms "Processes the kernel killed in the container for lack of memory (cgroup v2 memory.events)" counter

# ---- Journal (container logs arrive through Docker's journald driver) ------------------------
to_bytes() {  # 20G / 512M / 1T / plain bytes -> bytes (journald.conf sizes are base 1024)
  local v="$1" n u
  n="${v%[KMGTkmgt]}"; u="${v#"$n"}"
  [[ "$n" =~ ^[0-9]+$ ]] || return 1
  case "${u^^}" in
    "") echo "$n" ;; K) echo $((n << 10)) ;; M) echo $((n << 20)) ;; G) echo $((n << 30)) ;; T) echo $((n << 40)) ;;
  esac
}
if [[ -d /var/log/journal ]]; then
  used="$(du -sb /var/log/journal 2>/dev/null | cut -f1 || true)"
  if [[ "$used" =~ ^[0-9]+$ ]]; then
    describe ihr_journal_disk_usage_bytes "Size of the persistent journal"
    metric "ihr_journal_disk_usage_bytes $used"
  fi
  cap="$(systemd-analyze cat-config systemd/journald.conf 2>/dev/null | sed -n 's/^SystemMaxUse=//p' | tail -1 || true)"
  if [[ -n "$cap" ]] && cap="$(to_bytes "$cap")"; then
    describe ihr_journal_max_use_bytes "journald's SystemMaxUse"
    metric "ihr_journal_max_use_bytes $cap"
  fi
fi
# Only with read access to the system journal (the systemd unit adds the systemd-journal group):
# without it journalctl shows this user's own entries only, and a 0 would be a false all-clear
if [[ $EUID -eq 0 || " $(id -Gn) " == *" systemd-journal "* || " $(id -Gn) " == *" adm "* ]]; then
  n="$(journalctl -q --no-pager --since -15min -t systemd-journald -o cat 2>/dev/null | grep -c '^Suppressed ' || true)"
  describe ihr_journal_suppressed_lines "journald's \"Suppressed N messages\" lines in the last 15 minutes"
  metric "ihr_journal_suppressed_lines ${n:-0}"
  # The journal is a 14-day buffer (P1-T9c): its oldest entry, and when this host was set up
  # (deploy/host/bootstrap.sh), so an alert can tell a short buffer from a young host
  # (the first line only: journalctl stops reading when head closes the pipe)
  oldest="$(journalctl -q --no-pager -o short-unix 2>/dev/null | head -n1 | cut -d. -f1 || true)"
  if [[ "$oldest" =~ ^[0-9]+$ ]]; then
    describe ihr_journal_oldest_entry_timestamp_seconds "Time of the oldest entry in the journal"
    metric "ihr_journal_oldest_entry_timestamp_seconds $oldest"
  fi
fi
installed="$(head -n1 "${IHR_INSTALLED_FILE:-/etc/indiehr/installed-at}" 2>/dev/null || true)"
if [[ "$installed" =~ ^[0-9]+$ ]]; then
  describe ihr_host_installed_timestamp_seconds "When deploy/host/bootstrap.sh first set this host up"
  metric "ihr_host_installed_timestamp_seconds $installed"
fi

# ---- Time source (CERT-In: NIC/NPL only; deploy/host/bootstrap.sh) ---------------------------
# Read by the timer's unit as root (`chronyc -N -c sources` and `chronyc -c tracking`, see
# deploy/host/bootstrap.sh) into $CHRONY_DIR; this script only parses those files. A host without
# chrony (a proof on a workstation) writes nothing at all here.
if command -v chronyc >/dev/null 2>&1 || [[ -d "$CHRONY_DIR" ]]; then
  src="$CHRONY_DIR/sources.csv" trk="$CHRONY_DIR/tracking.csv" reading=0
  # Fresh (the root step runs each minute and deletes both files when chronyc fails)
  if [[ -f "$src" && -f "$trk" && -n "$(find "$src" "$trk" -mmin -5 2>/dev/null | sed -n 2p)" ]]; then
    # tracking CSV: refid,name,stratum,reftime,system time,...,leap status (14 fields). System
    # time: chrony's own offset of the clock from NTP time (positive: the clock is behind).
    offset="$(awk -F, 'NR == 1 && NF >= 14 {print $5}' "$trk")"
    if [[ "$offset" =~ ^-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?$ ]]; then
      reading=1
      # sources CSV: mode,state,name,...; state "*" is the source the clock follows (-N: names
      # as configured). None selected (unsynchronised) is a real 0.
      selected="$(awk -F, '$2 == "*" {print $3; exit}' "$src")"
      ok=0
      case "$selected" in samay1.nic.in|samay2.nic.in|time.nplindia.org) ok=1 ;; esac
      describe ihr_ntp_indian_source_selected "1 when chrony follows a NIC or NPL time server"
      metric "ihr_ntp_indian_source_selected $ok"
      describe ihr_ntp_offset_seconds "chrony's offset of the system clock from NTP time (tracking: System time; positive: behind)"
      metric "ihr_ntp_offset_seconds $offset"
    fi
  fi
  describe ihr_ntp_reading_ok "1 when chrony's state could be read (0: the source and offset are unknown, not bad)"
  metric "ihr_ntp_reading_ok $reading"
fi

# ---- Backups (the backup service's own markers; compose.backup.yaml) -------------------------
backup_id="$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" \
  --filter "label=com.docker.compose.service=backup" --filter "label=com.docker.compose.oneoff=False" \
  --filter status=running | head -1)"
if [[ -n "$backup_id" ]]; then
  # Times of the marker files, the spool's files (WAL waiting to be pushed), and the health
  # check's verdict. Its message is dropped: only the exit code counts here.
  # shellcheck disable=SC2016  # runs in the container's shell
  out="$(docker exec "$backup_id" sh -c '
    S=/var/lib/pgbackrest; now=$(date +%s)
    for f in backup-ok check-ok; do [ -f "$S/$f" ] && echo "$f $(stat -c %Y "$S/$f")"; done
    n=0; oldest=0
    for f in /var/spool/ihr-wal/*; do
      [ -f "$f" ] || continue
      n=$((n + 1)); a=$((now - $(stat -c %Y "$f"))); [ "$a" -gt "$oldest" ] && oldest=$a
    done
    echo "spool-files $n"; echo "spool-oldest $oldest"
    echo "spool-kib $(du -sk /var/spool/ihr-wal | cut -f1)"
    if /usr/local/bin/ihr-backup-health >/dev/null 2>&1; then echo "healthy 1"; else echo "healthy 0"; fi
  ' 2>/dev/null || true)"
  declare -A b=()
  while read -r k v; do [[ "$k" =~ ^[a-z-]+$ && "$v" =~ ^[0-9]+$ ]] && b[$k]=$v; done <<< "$out"
  if [[ -n "${b[healthy]:-}" ]]; then
    describe ihr_backup_healthy "The backup service's own health check (deploy/docker/backup/health.sh)"
    metric "ihr_backup_healthy ${b[healthy]}"
    [[ -n "${b[backup-ok]:-}" ]] && { describe ihr_backup_last_success_timestamp_seconds "Last good backup"; metric "ihr_backup_last_success_timestamp_seconds ${b[backup-ok]}"; }
    [[ -n "${b[check-ok]:-}" ]] && { describe ihr_backup_last_check_timestamp_seconds "Last passing pgbackrest check"; metric "ihr_backup_last_check_timestamp_seconds ${b[check-ok]}"; }
    describe ihr_backup_spool_files "WAL files waiting in the spool"
    metric "ihr_backup_spool_files ${b[spool-files]:-0}"
    describe ihr_backup_spool_oldest_seconds "Age of the oldest WAL file in the spool"
    metric "ihr_backup_spool_oldest_seconds ${b[spool-oldest]:-0}"
    describe ihr_backup_spool_bytes "Size of the spool"
    metric "ihr_backup_spool_bytes $(( ${b[spool-kib]:-0} * 1024 ))"
    if [[ "$SPOOL_MAX_MB" =~ ^[0-9]+$ ]]; then
      describe ihr_backup_spool_max_bytes "The spool's cap (IHR_BACKUP_SPOOL_MAX_MB)"
      metric "ihr_backup_spool_max_bytes $((SPOOL_MAX_MB * 1048576))"
    fi
  fi
fi

describe ihr_host_metrics_last_run_timestamp_seconds "When this file was last written"
metric "ihr_host_metrics_last_run_timestamp_seconds $(date +%s)"

# Atomic: node_exporter never reads a half-written file
tmp="$(mktemp "$OUT/.ihr_host.XXXXXX")"
printf '%s\n' "${LINES[@]}" > "$tmp"
chmod 644 "$tmp"
mv "$tmp" "$OUT/ihr_host.prom"
