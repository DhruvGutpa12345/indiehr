#!/usr/bin/env bash
# Size model for the nightly per-hospital dumps (P1-T22a; docs/reviews/p1-t22/design.md, "After
# the attack" item 3, finding A10). Results: docs/reviews/p1-t22/size-model.md.
#
# A throwaway Postgres 18.6 container (ihr-bench-dumps, no network) with one database, bench_dumps:
# one demo hospital's schema restored from a dump file, then N generated audit entries in a busy
# hospital's mix (requests, inserts, updates, deletes, shaped like the real trail's rows). It
# measures:
#   - bytes per audit entry on disk (table, TOAST and the four indexes) and in each dump format;
#   - pg_dump -Fc of the schema (gzip, the default; zstd; lz4): seconds and MB/s;
#   - pg_restore --list, a schema-only restore into a scratch database (the nightly check) and a
#     full single-transaction restore into an empty database, with the locks it held at most;
#   - sealing the dump (Fernet in 1 MiB chunks, deploy/lib/sealer.py) and SHA-256, on this host.
# Synthetic data only; nothing leaves this machine. Stop the dev stack first (memory, timing).
#
# Usage: scripts/dump-size-bench.sh <hospital.dump> [entries]   (default 3650000: 73 busy days)
# Env:   KEEP=1  keep the container afterwards       OUT=<file>  JSON lines (default bench-dumps.jsonl)
set -euo pipefail
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
SRC="${1:?a hospital dump (pg_dump -Fc -n <schema> of a demo hospital)}"
N="${2:-3650000}"
OUT="${OUT:-bench-dumps.jsonl}"
DB=ihr-bench-dumps
IMAGE="postgres:18.6-alpine"  # docs/VERSIONS.md
WORK="$(mktemp -d "${TMPDIR:-/var/tmp}/ihr-dump-bench.XXXXXX")"  # the sealed copy: not a small /tmp
trap 'rm -rf "$WORK"; [[ -n "${KEEP:-}" ]] || docker rm -f -v "$DB" >/dev/null 2>&1 || true' EXIT

psql_in() { docker exec -i -e PGOPTIONS='-c client_min_messages=warning' "$DB" psql -U postgres -v ON_ERROR_STOP=1 -qAt "$@"; }
now() { date +%s.%N; }
since() { awk -v a="$1" -v b="$(now)" 'BEGIN { printf "%.2f", b - a }'; }
emit() { echo "BENCH $1" | tee -a "$OUT"; }

docker rm -f -v "$DB" >/dev/null 2>&1 || true
docker run -d --name "$DB" --network none --memory 1536m --shm-size=256m -e POSTGRES_PASSWORD=bench \
  "$IMAGE" -c shared_buffers=256MB -c maintenance_work_mem=256MB -c max_wal_size=4GB \
  -c checkpoint_timeout=30min >/dev/null
until docker exec "$DB" pg_isready -U postgres -q; do sleep 1; done
sleep 2
for d in bench_dumps bench_scratch bench_restore; do psql_in -c "CREATE DATABASE $d"; done
psql_in -d bench_dumps -c "CREATE EXTENSION IF NOT EXISTS pg_trgm"
docker cp "$SRC" "$DB:/tmp/src.dump" >/dev/null
docker exec "$DB" pg_restore -U postgres -d bench_dumps --no-owner --no-acl /tmp/src.dump
SCHEMA="$(psql_in -d bench_dumps -c "SELECT nspname FROM pg_namespace WHERE nspname ~ '^h_[a-z0-9]{10}$'")"
[[ "$SCHEMA" =~ ^h_[a-z0-9]{10}$ ]] || { echo "no hospital schema in $SRC" >&2; exit 1; }
BASE="$(psql_in -d bench_dumps -c "SELECT count(*) FROM $SCHEMA.ihr_audit_entry")"
echo "Schema $SCHEMA restored ($BASE audit entries); generating $N more"

# A busy day's mix (P1-T21's 50,000 entries per busy hospital per day): 40 % request entries,
# 35 % inserts with the whole row, 20 % updates with the changed columns as [old, new], 5 %
# deletes with the whole row. Values vary like real ones: uuids, times, codes, numbers, some
# words. Loaded with triggers off (session_replication_role): the stamp trigger would put every
# entry at today's time and transaction.
GEN=$(cat <<'SQL'
SET session_replication_role = replica;
INSERT INTO __S__.ihr_audit_entry (txid, at, kind, actor, context, tbl, op, row_pk, patients, data, salt)
OVERRIDING SYSTEM VALUE
SELECT (1000 + g)::text::xid8,
       now() - make_interval(secs => (__HI__ - g) * 1.728),
       CASE WHEN r < 40 THEN 'request' ELSE 'write' END,
       'user:' || (g % 180) || ':' || (ARRAY['dr-sharma','nurse-iyer','dr-khan','front-desk-2','lab-tech-1','pharm-3'])[1 + g % 6],
       jsonb_build_object('ip', '10.20.' || (g % 7) || '.' || (g % 250), 'rid', md5(g::text || 'r'), 'via', 'web',
         'path', '/api/v1/' || (ARRAY['patient','encounter','observation','medication_request','questionnaire_response','consent'])[1 + g % 6]
                 || '/' || gen_random_uuid() || '/', 'method', (ARRAY['GET','POST','PUT','GET'])[1 + g % 4],
         'purpose', 'care'),
       CASE WHEN r < 40 THEN '' ELSE (ARRAY['emr_encounter','emr_observation','emr_medicationrequest','emr_questionnaireresponse','emr_patient','emr_condition'])[1 + g % 6] END,
       CASE WHEN r < 40 THEN '' WHEN r < 75 THEN 'I' WHEN r < 95 THEN 'U' ELSE 'D' END,
       CASE WHEN r < 40 THEN NULL ELSE (g / 3)::text END,
       ARRAY[(g % 40000)::bigint],
       CASE
         WHEN r < 40 THEN jsonb_build_object('status', (ARRAY[200,200,200,201,404])[1 + g % 5])
         WHEN r < 75 OR r >= 95 THEN jsonb_build_object(
           'id', g / 3, 'external_id', gen_random_uuid(), 'created_date', now() - make_interval(secs => g),
           'modified_date', now() - make_interval(secs => g / 2), 'deleted', false,
           'created_by_id', g % 180, 'updated_by_id', (g + 7) % 180, 'patient_id', g % 40000,
           'encounter_id', g % 90000, 'facility_id', 1 + g % 2, 'status', (ARRAY['active','completed','entered_in_error','in_progress'])[1 + g % 4],
           'category', (ARRAY['vital-signs','laboratory','exam','therapy'])[1 + g % 4],
           'code', jsonb_build_object('system', 'http://loinc.org', 'code', (1000 + g % 9000) || '-' || (g % 10),
                   'display', (ARRAY['Glucose [Mass/volume] in Blood','Hemoglobin [Mass/volume] in Blood','Creatinine [Mass/volume] in Serum','Sodium [Moles/volume] in Serum','Body temperature','Heart rate'])[1 + g % 6]),
           'value', jsonb_build_object('value', round((random() * 200)::numeric, 1), 'unit', (ARRAY['mg/dL','g/dL','mmol/L','Cel','/min'])[1 + g % 5]),
           'effective_datetime', now() - make_interval(secs => g * 2),
           'note', (ARRAY['Patient stable, review tomorrow.','Repeat test after 6 hours.','Started on IV fluids; monitor urine output.',
                         'Known diabetic, on metformin 500 mg twice daily.','Advised review in OPD after one week.',''])[1 + g % 6],
           'meta', jsonb_build_object('version', g % 4, 'source', 'care-fe', 'performer', 'user:' || (g % 180)))
         ELSE jsonb_build_object(
           'status', jsonb_build_array('in_progress', 'completed'),
           'modified_date', jsonb_build_array(now() - make_interval(secs => g), now() - make_interval(secs => g / 2)),
           'note', jsonb_build_array('', (ARRAY['Discharged in stable condition.','Dose reduced.','Shifted to ward 4.'])[1 + g % 3]))
       END,
       uuid_send(gen_random_uuid())
FROM (SELECT g, (g * 7919) % 100 AS r FROM generate_series(__LO__::bigint, __HI__::bigint) g) x;
SQL
)
t0="$(now)"
step=500000
lo=1
while (( lo <= N )); do
  hi=$(( lo + step - 1 )); (( hi <= N )) || hi=$N
  sql="${GEN//__S__/$SCHEMA}"; sql="${sql//__LO__/$lo}"; sql="${sql//__HI__/$N}"
  sql="${sql/generate_series($lo::bigint, $N::bigint)/generate_series($lo::bigint, $hi::bigint)}"
  psql_in -d bench_dumps <<<"$sql"
  echo "  $hi / $N entries ($(since "$t0") s)"
  lo=$(( hi + 1 ))
done
psql_in -d bench_dumps -c "VACUUM (ANALYZE) $SCHEMA.ihr_audit_entry"
gen_s="$(since "$t0")"

read -r entries heap toast idx total schema_bytes rows_b data_b <<<"$(psql_in -F ' ' -d bench_dumps -c "
SELECT count(*), pg_relation_size('$SCHEMA.ihr_audit_entry'),
       coalesce(pg_total_relation_size(nullif(c.reltoastrelid, 0)), 0),
       pg_indexes_size('$SCHEMA.ihr_audit_entry'), pg_total_relation_size('$SCHEMA.ihr_audit_entry'),
       (SELECT sum(pg_total_relation_size(k.oid)) FROM pg_class k JOIN pg_namespace n ON n.oid = k.relnamespace
         WHERE n.nspname = '$SCHEMA' AND k.relkind IN ('r', 'm')),
       (SELECT round(avg(pg_column_size(e.*))) FROM (SELECT * FROM $SCHEMA.ihr_audit_entry TABLESAMPLE SYSTEM (1)) e),
       (SELECT round(avg(pg_column_size(e.data))) FROM (SELECT * FROM $SCHEMA.ihr_audit_entry TABLESAMPLE SYSTEM (1)) e)
FROM $SCHEMA.ihr_audit_entry, pg_class c WHERE c.oid = '$SCHEMA.ihr_audit_entry'::regclass GROUP BY c.reltoastrelid")"
emit "{\"what\":\"table\",\"entries\":$entries,\"generate_s\":$gen_s,\"heap\":$heap,\"toast\":$toast,\"indexes\":$idx,\"total\":$total,\"schema_total\":$schema_bytes,\"row_bytes\":$rows_b,\"data_bytes\":$data_b}"

dump() {  # dump <label> <compress>
  local t; t="$(now)"
  docker exec "$DB" pg_dump -U postgres -d bench_dumps -Fc -Z "$2" -n "$SCHEMA" --no-owner --no-acl \
    --lock-wait-timeout=60s -f "/tmp/$1.dump"
  local s; s="$(since "$t")"
  local b; b="$(docker exec "$DB" stat -c %s "/tmp/$1.dump")"
  emit "{\"what\":\"dump\",\"format\":\"$1\",\"seconds\":$s,\"bytes\":$b}"
}
dump gzip gzip
dump zstd zstd
dump lz4 lz4

t="$(now)"; docker exec "$DB" pg_restore -l /tmp/gzip.dump >/dev/null
emit "{\"what\":\"list\",\"seconds\":$(since "$t")}"
psql_in -d bench_scratch -c "CREATE EXTENSION IF NOT EXISTS pg_trgm"
t="$(now)"; docker exec "$DB" pg_restore -U postgres -d bench_scratch --schema-only --no-owner --no-acl --exit-on-error /tmp/gzip.dump
emit "{\"what\":\"schema_only_restore\",\"seconds\":$(since "$t")}"
psql_in -d bench_restore -c "CREATE EXTENSION IF NOT EXISTS pg_trgm"
for fmt in gzip zstd; do
  psql_in -d bench_restore -c "DROP SCHEMA IF EXISTS $SCHEMA CASCADE"
  t="$(now)"
  docker exec "$DB" pg_restore -U postgres -d bench_restore --no-owner --no-acl --exit-on-error --single-transaction "/tmp/$fmt.dump"
  emit "{\"what\":\"restore\",\"format\":\"$fmt\",\"seconds\":$(since "$t")}"
done
locks="$(psql_in -d bench_restore -c "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = '$SCHEMA'")"
emit "{\"what\":\"relations\",\"count\":$locks}"

# Sealing and SHA-256 on this host (the dumper's Python does the same)
docker cp "$DB:/tmp/gzip.dump" "$WORK/gzip.dump" >/dev/null
python3 - "$WORK/gzip.dump" "$ROOT/deploy/lib" <<'PY'
import hashlib, json, os, sys, time
sys.path.insert(0, sys.argv[2])
from cryptography.fernet import Fernet
import sealer
path = sys.argv[1]
s = sealer.Sealer(Fernet.generate_key().decode(), "ihr-dump")
size = os.path.getsize(path)
t = time.monotonic()
h = hashlib.sha256()
with open(path, "rb") as f:
    for block in iter(lambda: f.read(sealer.CHUNK), b""):
        h.update(block)
sha_s = time.monotonic() - t
t = time.monotonic()
with open(path, "rb") as src, open(path + ".enc", "wb") as dst:
    s.encrypt_file(src, dst)
seal_s = time.monotonic() - t
sealed = os.path.getsize(path + ".enc")
t = time.monotonic()
with open(path + ".enc", "rb") as src:
    for _ in s.decrypt_lines(src):
        pass
open_s = time.monotonic() - t
print("BENCH " + json.dumps({"what": "seal", "plain": size, "sealed": sealed, "seal_s": round(seal_s, 2),
                               "open_s": round(open_s, 2), "sha256_s": round(sha_s, 2)}))
PY
echo "Done; results in $OUT"
