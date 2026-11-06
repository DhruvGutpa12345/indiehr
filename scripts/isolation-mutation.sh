#!/usr/bin/env bash
# Mutation runs for the cross-tenant isolation suite (TENANCY_DESIGN.md §11.2, P1-T1b).
# Each mutation removes one isolation control from the working tree. The suite must then go red
# on the named test, which proves that test really guards that control.
#
# Usage:
#   scripts/isolation-mutation.sh list           # mutation names, one per line
#   scripts/isolation-mutation.sh check          # every target still exists (no edits)
#   scripts/isolation-mutation.sh apply <name>   # edit the working tree (CI checkout only)
#   scripts/isolation-mutation.sh test <name>    # the test labels to run for <name>
#   scripts/isolation-mutation.sh expect <name>  # regexes (one per line) the run's log must match
#
# "apply" fails if the code it targets is not found, so a refactor cannot turn a mutation into
# a silent no-op. Never commit a mutated tree: CI applies these to a throwaway checkout.
set -euo pipefail
ROOT="${IHR_ROOT:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"  # IHR_ROOT: a scratch copy
CLS="ihr_platform.tests.isolation.test_isolation.CrossTenantIsolationTests"

# name | file | exact text to find | replacement | tests that must all fail | leak signature
# "leak signature": only where the leak already breaks building the two-tenant world (that is how
# the identifier-config leak first showed up), a regex for that leak's own error. A log line
# matching it counts in place of the test's FAIL; any other setUpClass error does not.
MUTATIONS=$(cat <<'TABLE'
cache-key-function|plugins/ihr_platform/ihr_platform/settings/tenancy.py|        cache["KEY_FUNCTION"] = "django_tenants.cache.make_key"\n        cache["REVERSE_KEY_FUNCTION"] = "django_tenants.cache.reverse_key"|        pass|test_zz_redis_keyspace_is_tenant_prefixed,test_crawl_never_shows_the_other_tenants_data|
file-key-prefix|plugins/ihr_platform/ihr_platform/settings/tenancy.py|    settings["FILE_OBJECT_KEY_PREFIX_FUNCTION"] = (\n        "ihr_platform.storage.tenant_key_prefix"\n    )|    pass|test_file_object_keys_carry_the_tenant|
cover-image-prefix|apps/backend/care/utils/file_uploads/cover_image.py|    image_key = f"{key_prefix()}{folder}/|    image_key = f"{folder}/|test_file_object_keys_carry_the_tenant|
otp-tid-check|apps/backend/config/patient_otp_token.py|        if schema and self.payload.get("tid") != schema:|        if False:|test_patient_otp_token_from_a_is_rejected_on_b|
identifier-config-cache|apps/backend/care/emr/signals/patient/base.py|        cached_key = f"{schema}:{facility_key}:{cls.IDENTIFIER_SYSTEM}"|        cached_key = f"{facility_key}:{cls.IDENTIFIER_SYSTEM}"|test_patient_identifier_config_cache_is_per_tenant|emr_patientidentifie_config_id_[0-9a-f]+_fk
identifier-lookup-cache|apps/backend/care/emr/models/patient.py|        return f"{getattr(connection, 'schema_name', '')}:{config_id}"|        return config_id|test_identifier_config_lookup_cache_is_per_tenant|
recent-views-prefix|apps/backend/care/emr/models/valueset.py|        return f"{schema}:{cache_key}" if schema else cache_key|        return cache_key|test_raw_redis_recent_views_are_per_tenant|
celery-tenant-app|apps/backend/config/celery_app.py|    from tenant_schemas_celery.app import CeleryApp as Celery|    pass|test_celery_tasks_run_in_the_senders_tenant|
beat-active-only|plugins/ihr_platform/ihr_platform/beat.py|Hospital.objects.filter(status=Hospital.Status.ACTIVE).exclude(|Hospital.objects.exclude(|test_periodic_jobs_run_once_per_active_tenant|
anonymous-default-allow|plugins/ihr_platform/ihr_platform/settings/tenancy.py|    settings["CELERY_BEAT_SCHEDULER"] =|    settings["REST_FRAMEWORK"]["DEFAULT_PERMISSION_CLASSES"] = ["rest_framework.permissions.AllowAny"]\n    settings["CELERY_BEAT_SCHEDULER"] =|test_anonymous_access_matches_reviewed_allow_list|
TABLE
)

row() {
  local line
  line=$(grep -E "^$1\|" <<<"$MUTATIONS") || { echo "unknown mutation: $1" >&2; exit 2; }
  IFS='|' read -r NAME FILE FIND REPLACE TESTS SIGNATURE <<<"$line"
}

mutate() {
  python3 - "$1" "$ROOT/$FILE" "$FIND" "$REPLACE" "$NAME" <<'PY'
import sys
mode, path, find, replace, name = sys.argv[1:]
find = find.replace("\\n", "\n")
replace = replace.replace("\\n", "\n")
text = open(path).read()
if text.count(find) != 1:
    sys.exit(f"{name}: target not found exactly once in {path}")
if mode == "write":
    open(path, "w").write(text.replace(find, replace))
print(f"{name}: {'mutated' if mode == 'write' else 'target found in'} {path}")
PY
}

case "${1:-}" in
  list) cut -d'|' -f1 <<<"$MUTATIONS" ;;
  apply)
    row "${2:?name}"
    mutate write
    ;;
  check)
    while read -r name; do row "$name"; mutate check; done < <(cut -d'|' -f1 <<<"$MUTATIONS")
    ;;
  test) row "${2:?name}"; for t in ${TESTS//,/ }; do printf '%s.%s ' "$CLS" "$t"; done; echo ;;
  # One regex per line; the run's log must match every line.
  expect)
    row "${2:?name}"
    # An assertion FAIL, not an ERROR: an error could come from unrelated breakage.
    for t in ${TESTS//,/ }; do
      if [[ -n "$SIGNATURE" ]]; then
        echo "^FAIL: $t \(ihr_platform\.tests\.isolation|$SIGNATURE"
      else
        echo "^FAIL: $t \(ihr_platform\.tests\.isolation"
      fi
    done
    ;;
  *) sed -n '5,10p' "$0"; exit 1 ;;
esac
