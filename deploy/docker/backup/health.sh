#!/bin/sh
# Health of the backup service (P1-T8c), for Docker's healthcheck and `staging.sh smoke`:
# unhealthy when a WAL file has waited in the spool more than 10 minutes, the last hourly
# check failed (or none passed in 2 hours), or the last good backup is more than 26 hours old.
set -eu
STATE=/var/lib/pgbackrest
now="$(date +%s)"
age() { echo $(( now - $(stat -c %Y "$1") )); }
problem() { echo "backup unhealthy: $*"; exit 1; }
for f in /var/spool/ihr-wal/*; do
  [ -f "$f" ] || continue
  [ "$(age "$f")" -le 600 ] || problem "${f##*/} has waited $(( $(age "$f") / 60 )) min in the spool"
done
[ -f "$STATE/check-ok" ] || problem "no successful pgbackrest check yet (or the last one failed)"
[ "$(age "$STATE/check-ok")" -le 7200 ] || problem "no successful check for 2 hours"
[ -f "$STATE/backup-ok" ] || problem "no successful backup yet"
[ "$(age "$STATE/backup-ok")" -le 93600 ] || problem "the last good backup is more than 26 hours old"
echo "backup healthy"
