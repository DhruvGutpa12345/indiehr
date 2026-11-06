#!/usr/bin/env bash
# Scale and operations benchmark for one cell (TENANCY_DESIGN.md §11.3, task P1-T1c).
#
# Starts a throwaway Postgres 18.6 container (ihr-bench-db) on the dev stack's network, grows a
# bench database to each level of hospitals and measures it (ihr_bench_scale), then times a
# per-hospital dump and two restore round trips, checked by a hash of every table's rows:
#   1. restore one hospital into an empty database;
#   2. restore it in place under a side name, and check the other hospitals are unchanged.
# Synthetic data only. Needs the dev stack (scripts/care-dev.sh up) for Redis.
#
# Usage: scripts/tenancy-scale-bench.sh [levels...]     (default: 1 25 50 100 200)
# Env:   OUT=<file>  results as JSON lines (default: bench-results.jsonl)
#        KEEP=1      keep the bench container and its data afterwards
set -euo pipefail
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
OUT="${OUT:-bench-results.jsonl}"
LEVELS=("${@:-1 25 50 100 200}")
read -r -a LEVELS <<<"${LEVELS[*]}"
PG_IMAGE="postgres:18.6-alpine" # docs/VERSIONS.md
DB=ihr-bench-db
NETWORK="$(docker inspect indiehr-care-db-1 --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')"

psql_in() { docker exec -i -e PGOPTIONS='-c client_min_messages=warning' "$DB" psql -U postgres -v ON_ERROR_STOP=1 -qAt "$@"; }
now() { date +%s.%N; }
since() { awk -v a="$1" -v b="$(now)" 'BEGIN { printf "%.1f", b - a }'; }
emit() { echo "BENCH $1" | tee -a "$OUT"; }

manage() {
  docker run --rm --network "$NETWORK" \
    --env-file "$ROOT/apps/backend/docker/.local.env" \
    -e DATABASE_URL="postgres://postgres:postgres@$DB:5432/bench_cell" \
    -e DJANGO_SETTINGS_MODULE=ihr_platform.settings.local \
    -e PYTHONPATH=/ihr/plugins/ihr_platform \
    -e SNOWSTORM_DEPLOYMENT_URL=http://127.0.0.1:9/fhir \
    -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$ROOT/apps/backend:/app:ro" -v "$ROOT/plugins:/ihr/plugins:ro" -w /app \
    --user "$(id -u):$(id -g)" care_local python manage.py "$@"
}

# Order-independent hash of a schema: every row of every table, every sequence position, and the
# definitions of its indexes and constraints (schema prefix removed, so a renamed copy matches).
HASH_FUNCTION=$(cat <<'SQL'
CREATE OR REPLACE FUNCTION public.bench_schema_hash(s text) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE t record; h text := ''; r text;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = s ORDER BY tablename LOOP
    EXECUTE format(
      'SELECT %L || '':'' || count(*) || '':'' || coalesce(md5(string_agg(x::text, ''|'' ORDER BY x::text)), '''') FROM %I.%I x',
      t.tablename, s, t.tablename) INTO r;
    h := md5(h || r);
  END LOOP;
  FOR t IN SELECT sequencename FROM pg_sequences WHERE schemaname = s ORDER BY sequencename LOOP
    EXECUTE format('SELECT %L || '':'' || last_value || '':'' || is_called FROM %I.%I',
      t.sequencename, s, t.sequencename) INTO r;
    h := md5(h || r);
  END LOOP;
  SELECT md5(coalesce(string_agg(d, '|' ORDER BY d), '')) INTO r FROM (
    SELECT replace(indexdef, quote_ident(s) || '.', '') AS d FROM pg_indexes WHERE schemaname = s
    UNION ALL
    SELECT c.conname || ':' || replace(pg_get_constraintdef(c.oid), quote_ident(s) || '.', '')
      FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE n.nspname = s
  ) q;
  RETURN md5(h || r);
END $$;
SQL
)

start_db() {
  if docker inspect "$DB" >/dev/null 2>&1; then
    echo "Reusing running $DB"
    return
  fi
  # Docker's default 64 MB /dev/shm is too small for parallel catalogue queries at ~150+
  # hospitals ("could not resize shared memory segment"); production needs the same headroom.
  docker run -d --name "$DB" --network "$NETWORK" --shm-size=1g -e POSTGRES_PASSWORD=postgres "$PG_IMAGE" >/dev/null
  until docker exec "$DB" pg_isready -U postgres -q; do sleep 1; done
  sleep 2
  psql_in -c "CREATE DATABASE bench_cell"
  psql_in -d bench_cell -c "$HASH_FUNCTION"
  manage migrate_schemas --shared -v 0
}

dump_restore() {
  local n=$1 t0 t1 t0r started dump_s dump_kb fresh_s inplace_s h0 h_fresh h1_before h1_after h_side
  t0=$(psql_in -d bench_cell -c "SELECT schema_name FROM ihr_platform_hospital WHERE slug = 'bench-template'")
  # the neighbour checked is a seeded copy of the template, not an empty new hospital
  t1=$(psql_in -d bench_cell -c "SELECT schema_name FROM ihr_platform_hospital WHERE slug LIKE 'bench-0%' ORDER BY created_on LIMIT 1")
  t0r="${t0}_r"
  h0=$(psql_in -d bench_cell -c "SELECT bench_schema_hash('$t0')")

  started=$(now)
  docker exec "$DB" pg_dump -U postgres -Fc -n "$t0" -f /tmp/t0.dump bench_cell
  dump_s=$(since "$started")
  dump_kb=$(docker exec "$DB" stat -c %s /tmp/t0.dump | awk '{ printf "%d", $1 / 1024 }')

  # 1. into an empty database
  psql_in -c "DROP DATABASE IF EXISTS bench_restore" -c "CREATE DATABASE bench_restore"
  psql_in -d bench_restore -c "CREATE EXTENSION pg_trgm" -c "$HASH_FUNCTION"
  started=$(now)
  docker exec "$DB" pg_restore -U postgres --exit-on-error -d bench_restore /tmp/t0.dump
  fresh_s=$(since "$started")
  h_fresh=$(psql_in -d bench_restore -c "SELECT bench_schema_hash('$t0')")

  # 2. in place, under a side name: rename in the scratch database, dump, restore. Timed from the
  #    rename; step 1's restore into the scratch database comes on top.
  h1_before=""
  [[ -n "$t1" ]] && h1_before=$(psql_in -d bench_cell -c "SELECT bench_schema_hash('$t1')")
  started=$(now)
  psql_in -d bench_restore -c "ALTER SCHEMA \"$t0\" RENAME TO \"$t0r\""
  docker exec "$DB" pg_dump -U postgres -Fc -n "$t0r" -f /tmp/t0r.dump bench_restore
  docker exec "$DB" pg_restore -U postgres --exit-on-error -d bench_cell /tmp/t0r.dump
  inplace_s=$(since "$started")
  h_side=$(psql_in -d bench_cell -c "SELECT bench_schema_hash('$t0r')")
  h1_after=""
  [[ -n "$t1" ]] && h1_after=$(psql_in -d bench_cell -c "SELECT bench_schema_hash('$t1')")
  local h0_after
  h0_after=$(psql_in -d bench_cell -c "SELECT bench_schema_hash('$t0')")
  psql_in -d bench_cell -c "DROP SCHEMA \"$t0r\" CASCADE"
  psql_in -c "DROP DATABASE bench_restore"
  docker exec "$DB" rm -f /tmp/t0.dump /tmp/t0r.dump

  emit "$(printf '{"step": "backup_restore", "n": %s, "dump_seconds": %s, "dump_kb": %s, "restore_empty_db_seconds": %s, "restore_side_name_seconds": %s, "empty_db_identical": %s, "side_name_identical": %s, "source_unchanged": %s, "other_tenant_unchanged": %s, "neighbour_checked": %s}' \
    "$n" "$dump_s" "$dump_kb" "$fresh_s" "$inplace_s" \
    "$([[ "$h_fresh" == "$h0" ]] && echo true || echo false)" \
    "$([[ "$h_side" == "$h0" ]] && echo true || echo false)" \
    "$([[ "$h0_after" == "$h0" ]] && echo true || echo false)" \
    "$([[ -z "$t1" || "$h1_before" == "$h1_after" ]] && echo true || echo false)" \
    "$([[ -n "$t1" ]] && echo true || echo false)")"
}

: >"$OUT"
start_db
for n in "${LEVELS[@]}"; do
  started=$(now)
  manage ihr_bench_scale grow --to "$n" | grep '^BENCH' | tee -a "$OUT"
  emit "{\"step\": \"grow_total\", \"n\": $n, \"seconds\": $(since "$started")}"
  manage ihr_bench_scale measure | grep '^BENCH' | tee -a "$OUT"
  dump_restore "$n"
done
[[ "${KEEP:-}" == 1 ]] || docker rm -fv "$DB" >/dev/null
echo "Results: $OUT"
