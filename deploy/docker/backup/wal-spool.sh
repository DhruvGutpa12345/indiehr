#!/bin/sh
# Postgres's archive_command (P1-T8c): copy one finished WAL file into the spool, where the backup
# service picks it up and pushes it to the repository (encrypted, off the server). The database
# itself needs no backup key and no way out to the internet.
#   archive_command = '/usr/local/bin/ihr-pgbackrest-spool %p %f'  (pgBackRest's checks want its name in it)
# Atomic (copy to a temporary name, flush, rename) and safe to repeat: Postgres retries after a
# crash, so a file already in the spool with the same content counts as done.
set -eu
src="$1"
name="$2"
dir=/var/spool/ihr-wal
case "$name" in
  */*|.*|"") echo "ihr-pgbackrest-spool: bad file name '$name'" >&2; exit 1 ;;
esac
# Never more than IHR_BACKUP_SPOOL_MAX_MB (default 2048) waiting: past that, refuse (exit 1).
# Postgres then keeps the file and retries, and its own archiver statistics show the failure,
# instead of the spool silently filling the disk the database lives on.
max="${IHR_BACKUP_SPOOL_MAX_MB:-2048}"
used="$(du -sm "$dir" | cut -f1)"
if [ "$used" -ge "$max" ]; then
  echo "ihr-pgbackrest-spool: the spool holds ${used} MB (limit ${max} MB): is the backup service pushing?" >&2
  exit 1
fi
if [ -f "$dir/$name" ]; then
  if cmp -s "$src" "$dir/$name"; then exit 0; fi
  echo "ihr-pgbackrest-spool: $name is already spooled with different content" >&2
  exit 1
fi
cp "$src" "$dir/.$name.tmp"
sync "$dir/.$name.tmp"   # the data, before the name appears
mv "$dir/.$name.tmp" "$dir/$name"
sync "$dir"              # the rename itself: Postgres forgets the file once we return 0
