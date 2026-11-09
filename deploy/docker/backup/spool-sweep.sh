#!/bin/sh
# Spool sweep before a real restore's recovery (P1-T8c, `staging.sh restore`). Runs with the
# restored data directory mounted read-only, so pgBackRest knows which database is ours.
#  - WAL of OUR database still in the spool (the volume survived the loss) is pushed first, so
#    the recovery can replay it: less is lost;
#  - WAL of ANOTHER database (pgBackRest error 44: e.g. an empty database started by mistake
#    on the lost volume) is moved to /var/lib/pgbackrest/rejected-wal/: left in the spool it
#    would block every later push;
#  - any other failure (the repository out of reach) STOPS the sweep, exit 3, before recovery:
#    recovering without that WAL would promote early and leave it unreplayable for ever. The
#    file stays in the spool; `staging.sh restore` can simply be run again.
set -u
SPOOL=/var/spool/ihr-wal
REJECTED=/var/lib/pgbackrest/rejected-wal
pushed=0 rejected=0
for path in "$SPOOL"/*; do  # name order = WAL order; .tmp files (being written) are hidden
  [ -f "$path" ] || continue
  f="${path##*/}"
  if pgbackrest --stanza=main --log-level-console=error archive-push "$path"; then
    rm -f "$path"; pushed=$((pushed + 1))
  else
    rc=$?
    if [ "$rc" = 44 ]; then
      mkdir -p "$REJECTED" && mv "$path" "$REJECTED/$f" || exit 1
      echo "spool: $f belongs to another database: moved to $REJECTED"
      rejected=$((rejected + 1))
    else
      echo "spool: $f could not be pushed (pgBackRest error $rc); stopping: $pushed pushed, $rejected set aside"
      exit 3
    fi
  fi
done
echo "spool: $pushed pushed, $rejected set aside"
