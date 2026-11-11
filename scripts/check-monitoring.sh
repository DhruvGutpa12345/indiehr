#!/usr/bin/env bash
# Checks the monitoring settings (P1-T9a) with the very images that run them, pinned by digest
# in deploy/staging/compose.monitoring.yaml: no network, no stack, a few seconds.
#   - promtool check config: deploy/monitoring/prometheus/prometheus.yml (with sample targets)
#   - promtool check rules + promtool test rules: the alert rules and their unit tests
#   - amtool check-config: alertmanager.yml as scripts/staging.sh writes it, for a real server
#     and for a proof, from sample settings (no secrets: the secret files are empty here)
#   - blackbox_exporter --config.check: the probe modules
# Run by pre-commit (hook "monitoring-promtool") and so by CI. Usage: scripts/check-monitoring.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="$ROOT/deploy/staging/compose.monitoring.yaml"
image() { grep -o "image: $1:[^ ]*@sha256:[0-9a-f]\{64\}" "$COMPOSE" | head -1 | sed 's/^image: //'; }
PROM="$(image prom/prometheus)"
AM="$(image prom/alertmanager)"
BB="$(image prom/blackbox-exporter)"
[[ -n "$PROM" && -n "$AM" && -n "$BB" ]] || { echo "pinned images not found in $COMPOSE" >&2; exit 1; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/ihr-monitoring-check.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
run() {  # run <image> <entrypoint> [docker args --] <args>: offline, as this user, no logs kept
  local img="$1" ep="$2"; shift 2
  local extra=()
  while (($#)) && [[ "$1" != -- ]]; do extra+=("$1"); shift; done
  shift
  docker run --rm --network none --log-driver none -u "$(id -u):$(id -g)" \
    -v "$ROOT/deploy/monitoring:/m:ro" "${extra[@]}" --entrypoint "$ep" "$img" "$@"
}

echo "== Prometheus settings, rules and rule tests ($PROM)"
render() {  # render <on|test> <acme yes|no>: staging.sh's own files from sample settings
  local d="$WORK/$1-$2"
  mkdir -p "$d/secrets"
  {
    echo "IHR_TENANT_DOMAIN_SUFFIX=app.example.in"
    echo "IHR_MONITORING=$1"
    echo "IHR_ALERT_TO=ops@example.in"
    echo "IHR_ALERT_FROM=alerts@example.in"
    echo "IHR_ALERT_SMTP_HOST=$([[ $1 == test ]] && echo mail.ihr-proof.test || echo smtp.zeptomail.in)"
    echo "IHR_ALERT_SMTP_PORT=$([[ $1 == test ]] && echo 1025 || echo 587)"
    echo "IHR_ALERT_SMTP_USER=emailapikey"
    echo "IHR_ALERT_DEADMAN=$([[ $1 == on && $2 == no ]] && echo off || echo on)"
    [[ "$2" == no ]] || echo "IHR_ACME=letsencrypt"
    [[ "$2" == no ]] || echo "IHR_S3=local"
  } > "$d/.env"
  IHR_STAGING_DIR="$d" "$ROOT/scripts/staging.sh" monitoring-render >&2
  # Empty stand-ins for the secret files the configuration names (the real ones: mode 600)
  mkdir -p "$d/run"
  : > "$d/run/smtp-password"; : > "$d/run/watchdog-url"; : > "$d/run/watchdog-fail-url"; : > "$d/run/ca.pem"
  echo "$d"
}
for v in "on yes" "on no" "test no"; do
  # shellcheck disable=SC2086  # two words on purpose
  d="$(render $v)"
  run "$PROM" promtool -v "$d/generated/monitoring/targets:/etc/prometheus/targets:ro" \
    -v "$ROOT/deploy/monitoring/prometheus/rules:/etc/prometheus/rules:ro" -- \
    check config /m/prometheus/prometheus.yml
done
rules=() tests=()
for f in "$ROOT"/deploy/monitoring/prometheus/rules/*.yml; do rules+=("/m/prometheus/rules/${f##*/}"); done
for f in "$ROOT"/deploy/monitoring/prometheus/tests/*.yml; do tests+=("${f##*/}"); done
run "$PROM" promtool -- check rules "${rules[@]}"
run "$PROM" promtool -w /m/prometheus/tests -- test rules "${tests[@]}"

echo "== Alertmanager settings as staging.sh writes them ($AM)"
for v in "on yes" "on no" "test no"; do
  d="$WORK/${v/ /-}"
  run "$AM" amtool -v "$d/run:/run/ihr-alerts:ro" -v "$d/run/ca.pem:/proof/ca.pem:ro" \
    -v "$d/generated/monitoring:/c:ro" -- check-config /c/alertmanager.yml
done
echo "== blackbox_exporter's probes ($BB)"
run "$BB" /bin/blackbox_exporter -- --config.file=/m/blackbox/blackbox.yml --config.check
echo "Monitoring settings OK."
