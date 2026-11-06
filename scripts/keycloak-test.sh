#!/usr/bin/env bash
# Live Keycloak tests (P1-T3): a throwaway realm per run, real login pages, deleted afterwards.
# Locally: scripts/care-dev.sh keycloak up, then scripts/keycloak-test.sh [unittest args].
# CI sets IHR_KC_TEST_URL / IHR_KC_TEST_ADMIN_ID / IHR_KC_TEST_ADMIN_SECRET itself.
set -euo pipefail
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
if [[ -z "${IHR_KC_TEST_ADMIN_ID:-}" ]]; then
  set -a
  # shellcheck disable=SC1090,SC1091
  source "$ROOT/deploy/dev/keycloak.env"
  set +a
  export IHR_KC_TEST_URL="${IHR_KC_TEST_URL:-http://auth.localhost:8081}"
  export IHR_KC_TEST_ADMIN_ID="$KC_BOOTSTRAP_ADMIN_CLIENT_ID"
  export IHR_KC_TEST_ADMIN_SECRET="$KC_BOOTSTRAP_ADMIN_CLIENT_SECRET"
fi
cd "$ROOT/plugins/ihr_platform"
exec python3 -m unittest -v "${@:-ihr_platform.keycloak.tests.test_live}"
