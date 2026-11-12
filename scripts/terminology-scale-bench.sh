#!/usr/bin/env bash
# SNOMED-scale benchmark of the terminology engine (P1-T10a2; decides Q-F6, ADR-016).
#
# Starts a throwaway Postgres 18.6 container (ihr-bench-tx) shaped like staging's database (1.5 GB
# memory limit, shared_buffers 512 MB) on its own network, migrates the shared schema into a
# bench_terminology database, writes two synthetic RF2 packages (an "edition" and a "drug
# extension"; made-up ids and words, never SNOMED content) to disk, loads them together with the
# real loader and times the engine. The loader and the measuring run each get 1 GB, as a backend
# container would. Prints BENCH lines (JSON).
#
# The dev stack must be stopped first (scripts/care-dev.sh down): the machine has 6.7 GB, and a
# run where the host swapped is marked invalid. Removes the container, network and release
# afterwards (KEEP=1 keeps them).
#
# Usage: scripts/terminology-scale-bench.sh [active inactive drugs]   (default 370000 150000 100000)
# Env:   OUT=<file> results as JSON lines (default: terminology-bench.jsonl); RUNS=<n> (default 200)
#        KEEP=1 keep the container, network and release; REUSE=1 measure again on a kept container
set -euo pipefail
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
ACTIVE="${1:-370000}" INACTIVE="${2:-150000}" DRUGS="${3:-100000}"
OUT="${OUT:-terminology-bench.jsonl}"
PG_IMAGE="postgres:18.6-alpine" # docs/VERSIONS.md
DB=ihr-bench-tx
NETWORK=ihr-bench-tx-net
WORK="${HOME}/.cache/ihr-bench/terminology"

emit() { echo "BENCH $1" | tee -a "$OUT"; }
swaps() { awk '/^pswpin|^pswpout/ {s += $2} END {print s}' /proc/vmstat; }
sampler=""
cleanup() {
  [[ -n "$sampler" ]] && kill "$sampler" 2>/dev/null || true
  [[ "${KEEP:-}" == 1 ]] && { echo "Kept: container $DB, network $NETWORK, release $WORK"; return; }
  docker rm -fv "$DB" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}

# Statistics first (the loader also analyses; this makes a reused database certain), then the
# timings; the run is valid only with no host swap and a calibrated release
measure_step() {
  docker exec "$DB" psql -U postgres -d bench_terminology -qc "VACUUM (ANALYZE) ihr_terminology_concept, ihr_terminology_designation" >/dev/null
  emit "{\"step\": \"statistics\", \"tables\": $(psql_q "SELECT json_agg(json_build_object('table', relname, 'last_analyze', last_analyze, 'last_autoanalyze', last_autoanalyze)) FROM pg_stat_user_tables WHERE relname IN ('ihr_terminology_concept', 'ihr_terminology_designation')")}"
  local sw0; sw0="$(swaps)"
  manage ihr_terminology_bench measure --runs "${RUNS:-200}" --clients 4 | grep '^{' | while IFS= read -r line; do emit "$line"; done
  local swapped=$(( $(swaps) - sw0 ))
  local calibrated=true
  [[ -f "$WORK/.calibrated" ]] && calibrated="$(cat "$WORK/.calibrated")"
  local valid=false
  [[ "$swapped" == 0 && "$calibrated" == true ]] && valid=true
  emit "{\"step\": \"measured\", \"mode\": \"$1\", \"host_swapped_pages\": $swapped, \"calibrated\": $calibrated, \"valid\": $valid, \"db_oom_kills\": \"$(docker exec "$DB" sh -c "sed -n 's/^oom_kill //p' /sys/fs/cgroup/memory.events" 2>/dev/null || echo unknown)\"}"
}

if docker ps --format '{{.Names}}' | grep -q '^indiehr-care-'; then
  echo "Stop the dev stack first (scripts/care-dev.sh down): the benchmark needs the memory" >&2
  exit 1
fi
trap cleanup EXIT
if [[ "${REUSE:-}" != 1 ]]; then rm -rf "$WORK" && mkdir -p "$WORK" && chmod 755 "$WORK"; fi

manage() {
  docker run --rm --network "$NETWORK" --memory 1g --memory-swap 1g \
    --env-file "$ROOT/apps/backend/docker/.local.env" \
    -e DATABASE_URL="postgres://postgres:postgres@$DB:5432/bench_terminology" \
    -e DJANGO_SETTINGS_MODULE=ihr_platform.settings.local \
    -e PYTHONPATH=/ihr/plugins/ihr_platform \
    -e SNOWSTORM_DEPLOYMENT_URL=http://127.0.0.1:9/fhir -e IHR_TERMINOLOGY_THROWAWAY_DB=1 \
    -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$ROOT/apps/backend:/app:ro" -v "$ROOT/plugins:/ihr/plugins:ro" -v "$WORK:/release" -w /app \
    --user "$(id -u):$(id -g)" care_local python manage.py "$@"
}
psql_q() { docker exec "$DB" psql -U postgres -d bench_terminology -tAc "$1"; }

if [[ "${REUSE:-}" == 1 ]]; then
  docker ps --format '{{.Names}}' | grep -q "^$DB$" || { echo "REUSE=1: no running $DB (run once with KEEP=1)" >&2; exit 1; }
  measure_step reused
  exit 0
fi
docker network create "$NETWORK" >/dev/null 2>&1 || true
docker rm -fv "$DB" >/dev/null 2>&1 || true
# /dev/shm as on staging (compose.yaml shm_size 256m): Docker's 64 MB default made a parallel query
# fail under 4 clients in run 5
docker run -d --name "$DB" --network "$NETWORK" --memory 1536m --memory-swap 1536m --shm-size=256m \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=bench_terminology "$PG_IMAGE" \
  -c shared_buffers=512MB -c max_connections=100 >/dev/null
# The image initialises with a temporary server first: wait for the real one
for _ in $(seq 1 90); do
  docker logs "$DB" 2>&1 | grep -q "PostgreSQL init process complete" \
    && docker exec "$DB" pg_isready -q -U postgres -d bench_terminology && break
  sleep 1
done

t0=$(date +%s)
manage migrate_schemas --shared --noinput >/dev/null
emit "{\"step\": \"migrate\", \"seconds\": $(( $(date +%s) - t0 ))}"

t0=$(date +%s)
gen="$(manage ihr_terminology_bench generate --out /release --active "$ACTIVE" --inactive "$INACTIVE" --drugs "$DRUGS" | tail -1)"
emit "$gen"
emit "{\"step\": \"generate\", \"seconds\": $(( $(date +%s) - t0 )), \"release_mb\": $(du -sm "$WORK" | cut -f1)}"
if grep -q '"calibrated": true' <<< "$gen"; then echo true > "$WORK/.calibrated"; else
  echo false > "$WORK/.calibrated"
  echo "WARNING: the synthetic release is below its targets: no verdict from this run" >&2
fi

# The load: database memory (cgroup peak and OOM events), WAL and temporary bytes, data directory peak
wal0="$(psql_q "SELECT pg_current_wal_lsn()")"
tmp0="$(psql_q "SELECT temp_bytes FROM pg_stat_database WHERE datname = 'bench_terminology'")"
sw0="$(swaps)"
( peak=0; while sleep 2; do
    docker inspect "$DB" >/dev/null 2>&1 || break
    s="$(docker exec "$DB" du -sm /var/lib/postgresql 2>/dev/null | cut -f1 | tail -n 1)"
    [[ "$s" =~ ^[0-9]+$ ]] || continue  # a file vanished mid-count: skip that sample
    (( s > peak )) && peak=$s && echo "$peak" > "$WORK/.du_peak"
  done ) & sampler=$!
t0=$(date +%s)
loader_out="$(manage ihr_terminology_load --rf2 /release/edition --rf2 /release/extension --operator terminology-scale-bench)"
load_s=$(( $(date +%s) - t0 ))
kill "$sampler" 2>/dev/null || true
echo "$loader_out" | grep -E '^(loaded|summary|loader)' || true
loader_mb="$(sed -n 's/^loader peak memory \([0-9]*\) MB$/\1/p' <<< "$loader_out")"
db_peak="$(docker exec "$DB" cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo 0)"
ooms="$(docker exec "$DB" sh -c "sed -n 's/^oom_kill //p' /sys/fs/cgroup/memory.events" 2>/dev/null || echo unknown)"
wal_mb="$(psql_q "SELECT round(pg_wal_lsn_diff(pg_current_wal_lsn(), '$wal0') / 1048576)")"
tmp_mb="$(psql_q "SELECT round((temp_bytes - $tmp0) / 1048576.0) FROM pg_stat_database WHERE datname = 'bench_terminology'")"
emit "{\"step\": \"load\", \"seconds\": $load_s, \"loader_peak_mb\": ${loader_mb:-0}, \"db_peak_mb\": $(( db_peak / 1048576 )), \"db_limit_mb\": 1536, \"db_oom_kills\": \"$ooms\", \"wal_mb\": $wal_mb, \"temp_mb\": $tmp_mb, \"data_dir_peak_mb\": $(cat "$WORK/.du_peak" 2>/dev/null || echo 0), \"host_swapped_pages\": $(( $(swaps) - sw0 ))}"

measure_step fresh
