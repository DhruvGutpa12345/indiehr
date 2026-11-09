#!/bin/sh
# The backup service (P1-T8c): runs beside the database, same image, as the postgres user.
#  - every 10 s: pushes WAL files from the spool to the repository (point-in-time recovery);
#  - once a day at IHR_BACKUP_TIME (IST, default 02:30): a full backup on IHR_BACKUP_FULL_DAY
#    (1 = Monday ... 7 = Sunday, default 7), a differential on the other days; pgBackRest then
#    expires what retention no longer needs (4 fulls = about 4 weeks, owner 09-11-2026);
#  - every hour: `pgbackrest check` (archiving really reaches the repository).
# A fresh server gets its first full backup at once. If either loop stops, the container stops
# and Docker restarts it. Status for monitoring: /var/lib/pgbackrest/status.
set -eu
STANZA=main
SPOOL=/var/spool/ihr-wal
STATE=/var/lib/pgbackrest
AT="${IHR_BACKUP_TIME:-02:30}"
FULL_DAY="${IHR_BACKUP_FULL_DAY:-7}"
case "$AT" in [0-2][0-9]:[0-5][0-9]) ;; *) echo "backup: IHR_BACKUP_TIME must be HH:MM" >&2; exit 1 ;; esac
case "$FULL_DAY" in [1-7]) ;; *) echo "backup: IHR_BACKUP_FULL_DAY must be 1-7" >&2; exit 1 ;; esac
export TZ="${TZ:-Asia/Kolkata}"

log() { echo "backup: $*"; }
status() {  # status <key> <value>: one line per key in the status file (both loops write it)
  tmp="$(mktemp "$STATE/.status.XXXXXX")"
  touch "$STATE/status"
  { grep -v "^$1=" "$STATE/status" || true; echo "$1=$2"; } > "$tmp"
  mv "$tmp" "$STATE/status"
}
mark() { touch "$STATE/$1"; }  # the health check reads these files' ages

until pg_isready -q -h /var/run/postgresql; do sleep 2; done

push_wal() {  # every WAL file in the spool, oldest first; stop at the first failure
  for path in "$SPOOL"/*; do  # in name order = WAL order; .tmp files (being written) are hidden
    [ -f "$path" ] || continue
    f="${path##*/}"
    if pgbackrest --stanza="$STANZA" --log-level-console=warn archive-push "$path"; then
      rm -f "$path"
      status last_wal_push "$(date -Iseconds) $f"
      mark wal-ok
    else
      log "WAL push FAILED for $f (kept in the spool, retried)"
      status last_wal_failure "$(date -Iseconds) $f"
      return 1
    fi
  done
}

# The repository's stanza first (idempotent): WAL can only be pushed once it exists
pgbackrest --stanza="$STANZA" --log-level-console=warn stanza-create

pusher() { while :; do push_wal || true; sleep 10; done; }
pusher &
PUSHER=$!
trap 'kill "$PUSHER" 2>/dev/null; exit 0' TERM INT

# A fresh server's first full backup, at once; it counts as today's backup
if ! pgbackrest --stanza="$STANZA" --output=json info | grep -q '"type":"full"'; then
  log "no full backup yet: taking one now"
  if pgbackrest --stanza="$STANZA" --type=full backup; then
    status last_backup "$(date -Iseconds) full"
    mark backup-ok
    date +%F > "$STATE/last-run"
  else
    status last_backup_failure "$(date -Iseconds) full"
  fi
fi

retry_after=0
backup_once() {
  today="$(date +%F)"
  [ "$(cat "$STATE/last-run" 2>/dev/null || true)" = "$today" ] && return 0
  [ "$(date +%s)" -lt "$retry_after" ] && return 0
  # "1" in front: 0830 and 0230 compare as decimals, never as octal
  [ "1$(date +%H%M)" -lt "1$(echo "$AT" | tr -d :)" ] && return 0
  type="diff"
  [ "$(date +%u)" = "$FULL_DAY" ] && type=full
  log "$type backup started"
  start="$(date +%s)"
  if pgbackrest --stanza="$STANZA" --type="$type" backup; then
    echo "$today" > "$STATE/last-run"
    mark backup-ok
    status last_backup "$(date -Iseconds) $type $(( $(date +%s) - start ))s"
    log "$type backup finished in $(( $(date +%s) - start )) s"
  else
    status last_backup_failure "$(date -Iseconds) $type"
    log "$type backup FAILED (retried in an hour)"
    retry_after=$(( $(date +%s) + 3600 ))  # no sleep: the hourly check and the pushes go on
  fi
}

last_check=0
while kill -0 "$PUSHER" 2>/dev/null; do
  backup_once
  now="$(date +%s)"
  if [ $((now - last_check)) -ge 3600 ]; then
    if pgbackrest --stanza="$STANZA" --log-level-console=warn check; then
      status last_check "$(date -Iseconds) ok"
      mark check-ok
    else
      status last_check "$(date -Iseconds) FAILED"
      rm -f "$STATE/check-ok"
      log "check FAILED: WAL archiving does not reach the repository"
    fi
    last_check="$now"
  fi
  sleep 30
done
log "WAL pusher stopped; stopping the container"
exit 1
