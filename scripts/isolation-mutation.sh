#!/usr/bin/env bash
# Mutation runs for isolation and security controls (TENANCY_DESIGN.md §11.2, P1-T1b; P1-T16a).
# Each mutation removes one isolation or security control from the working tree. The tests must
# then go red on the named test, which proves that test really guards that control.
#
# Usage:
#   scripts/isolation-mutation.sh list           # mutation names, one per line
#   scripts/isolation-mutation.sh check          # every target and named test exists (no edits)
#   scripts/isolation-mutation.sh apply <name>   # edit the working tree (CI checkout only)
#   scripts/isolation-mutation.sh test <name>    # the test labels to run for <name>
#   scripts/isolation-mutation.sh expect <name>  # regexes (one per line) the run's log must match
#
# "apply" fails if the code it targets is not found, so a refactor cannot turn a mutation into
# a silent no-op. Never commit a mutated tree: CI applies these to a throwaway checkout.
set -euo pipefail
ROOT="${IHR_ROOT:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"  # IHR_ROOT: a scratch copy
CLS="ihr_platform.tests.isolation.test_isolation.CrossTenantIsolationTests"

# Isolation and security controls, one row each:
# name | file | exact text to find | replacement | tests that must all fail | leak signature
# The text to find must occur exactly once as a substring (not merely as a whole line).
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
reset-token-tid|apps/backend/care/emr/utils/reset_password.py|        if schema and payload.get("tid") != schema:|        if False:|ihr_tenant_settings.tests.test_hospital_profile.HospitalProfileTests.test_reset_token_from_one_hospital_is_refused_by_another|
celery-tenant-app|apps/backend/config/celery_app.py|    from tenant_schemas_celery.app import CeleryApp as Celery|    pass|test_celery_tasks_run_in_the_senders_tenant|
beat-active-only|plugins/ihr_platform/ihr_platform/beat.py|Hospital.objects.filter(status=Hospital.Status.ACTIVE).exclude(|Hospital.objects.exclude(|test_periodic_jobs_run_once_per_active_tenant|
anonymous-default-allow|plugins/ihr_platform/ihr_platform/settings/tenancy.py|    settings["CELERY_BEAT_SCHEDULER"] =|    settings["REST_FRAMEWORK"]["DEFAULT_PERMISSION_CLASSES"] = ["rest_framework.permissions.AllowAny"]\n    settings["CELERY_BEAT_SCHEDULER"] =|test_anonymous_access_matches_reviewed_allow_list|
oidc-azp|plugins/ihr_platform/ihr_identity/tokens.py|    if claims["azp"] != client_id:|    if False:|ihr_identity.tests.test_tokens.AccessTokenTests.test_wrong_azp_is_refused|
oidc-org-id|plugins/ihr_platform/ihr_identity/tokens.py|    if org.get("id") != str(org_id) or org.get("alias") != slug:|    if False:|test_staff_session_from_a_is_rejected_on_b|
oidc-acr|plugins/ihr_platform/ihr_identity/tokens.py|    if claims["acr"] != "mfa":|    if False:|ihr_identity.tests.test_tokens.AccessTokenTests.test_password_only_sign_in_is_refused|
session-tid|plugins/ihr_platform/ihr_identity/authentication.py|        if session.tid != connection.schema_name:  # second line behind the key prefix|        if False:|ihr_identity.tests.test_staff_sign_in.StaffSessionTests.test_session_from_another_schema_is_refused|
revocation-check|plugins/ihr_platform/ihr_identity/authentication.py|    if valid_after and session.created < valid_after.timestamp():|    if False:|ihr_identity.tests.test_staff_sign_in.StaffSessionTests.test_revoked_person_is_refused|
backchannel-sid|plugins/ihr_platform/ihr_identity/authentication.py|        if sessions.sign_in_ended(session):  # Keycloak said so (back-channel, P1-T5d)|        if False:|ihr_identity.tests.test_backchannel.BackChannelLogoutTests.test_a_signed_logout_token_ends_that_sign_in_here_at_once|
backchannel-aud|plugins/ihr_platform/ihr_identity/tokens.py|    if _audiences(claims) != [client_id]:\n        raise TokenRejectedError("logout token for another client")||ihr_identity.tests.test_backchannel.BackChannelLogoutTests.test_the_token_check_itself_demands_this_hospitals_client|
care-login-blocked|plugins/ihr_platform/ihr_platform/urls_tenant.py|    *blocked_urlpatterns(),  # CARE's own staff sign-in is off (ADR-009); must come first\n||ihr_identity.tests.test_staff_sign_in.StaffSessionTests.test_care_password_sign_in_routes_are_gone|
audit-trigger-every-table|plugins/ihr_platform/ihr_audit/install.py|            if table in EXEMPT:|            if table in EXEMPT or table == "emr_patient":|ihr_audit.tests.test_audit_trail.CoverageTests.test_every_table_is_covered_and_every_secret_looking_column_classified|
audit-coverage-missing|plugins/ihr_platform/ihr_audit/install.py|                    problems.append(f"{schema}.{table}: trigger {name} is missing")|                    pass|ihr_audit.tests.test_audit_trail.CoverageTests.test_a_new_table_or_secret_column_is_reported|
terminology-remote-refusal|plugins/ihr_platform/ihr_terminology/client.py|    care_client.FHIRClient.query = _refuse|    pass|ihr_terminology.tests.test_terminology.NothingLeaves.test_care_client_is_ours_and_the_remote_one_refuses|
terminology-install|plugins/ihr_platform/ihr_terminology/apps.py|        client.install()|        pass|ihr_terminology.tests.test_terminology.NothingLeaves.test_care_client_is_ours_and_the_remote_one_refuses,ihr_terminology.tests.test_terminology.NothingLeaves.test_no_http_during_terminology_calls|
role-sync-guard|plugins/ihr_platform/ihr_roles/management/commands/sync_permissions_roles.py|        if held:|        if False:|ihr_roles.tests.test_guarded_sync.GuardedSync.test_refuses_to_delete_a_held_role|
patient-global-edit-refused|plugins/ihr_platform/ihr_platform/settings/secure.py|    if settings.get("PATIENT_GLOBAL_EDIT_ACCESS_ENABLED"):|    if False:|ihr_platform.tests.test_secure_settings.SecureRulesTests.test_patient_global_edit_access_is_refused|
packs-edited-row-kept|plugins/ihr_platform/ihr_refdata/engine.py|        elif record is not None and record.applied_sha256 == have:|        elif record is not None:|ihr_refdata.tests.test_engine.RuleTests.test_an_edited_row_waits_for_the_admin|
release-gate|plugins/ihr_platform/ihr_platform/settings/tenancy.py|        "ihr_platform.release_gate.ReleaseGateMiddleware",\n||test_gated_hospital_shows_nothing|
release-gate-auth|plugins/ihr_platform/ihr_identity/authentication.py|    if not release.servable(tenant):  # held, or not on this build's schema (P1-T18a)|    if False:|ihr_platform.tests.test_release.DefenceInDepthTests.test_staff_and_service_sign_ins_refuse|
release-gate-beat|plugins/ihr_platform/ihr_platform/beat.py|        return active.filter(release.ready_q())|        return active|ihr_platform.tests.test_release.DefenceInDepthTests.test_beat_skips_a_held_hospital|
release-gate-staff-worker|plugins/ihr_platform/ihr_platform/invitations.py|            .filter(status=StaffInvitation.Status.QUEUED)\n            .filter(release.ready_q("hospital__"))|            .filter(status=StaffInvitation.Status.QUEUED)|ihr_platform.tests.test_release.DefenceInDepthTests.test_the_staff_worker_waits_and_never_fails_the_work|
release-gate-celery-task|plugins/ihr_platform/ihr_platform/apps.py|        celery_gate.install()|        pass|ihr_platform.tests.test_release.CeleryGateTests.test_a_queued_task_never_runs_in_a_held_hospital|
release-roles-hold-released|plugins/ihr_platform/ihr_platform/fleet.py|                release.release_hold(passed, reason=release.HOLD_ROLES)|                pass|ihr_platform.tests.test_release.FleetTests.test_a_role_refusal_hold_is_released_by_its_resume|
release-roles-never-lift-window|plugins/ihr_platform/ihr_platform/fleet.py|                release.release_hold(passed, reason=release.HOLD_ROLES)|                release.release_hold(passed)|ihr_platform.tests.test_release.FleetTests.test_a_run_whose_roles_pass_never_lifts_a_window_hold|
TABLE
)

row() {
  local line
  line=$(grep -E "^$1\|" <<<"$MUTATIONS") || { echo "unknown mutation: $1" >&2; exit 2; }
  IFS='|' read -r NAME FILE FIND REPLACE TESTS SIGNATURE <<<"$line"
  [[ -n "$TESTS" ]] || { echo "mutation $1 names no test" >&2; exit 2; }
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

# Every named test must exist as Class.method in its module (stdlib only: the CI list job has
# no Django). A renamed test then fails here, not later as an ERROR in its matrix job.
tests_exist() {
  local labels=()
  for t in ${TESTS//,/ }; do
    [[ "$t" == *.* ]] || t="$CLS.$t"
    labels+=("$t")
  done
  python3 -I - "$ROOT" "$NAME" "${labels[@]}" <<'PY'
import ast
import sys
from pathlib import Path

root, name, *labels = sys.argv[1:]
roots = [Path(root, "plugins", "ihr_platform"), Path(root, "apps", "backend")]
missing = []
for label in labels:
    parts = label.split(".")
    *module, cls, method = parts
    files = [r.joinpath(*module).with_suffix(".py") for r in roots]
    found = False
    for path in files:
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(), str(path))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == cls:
                found = any(
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == method
                    for item in node.body
                )
        break
    if not found:
        missing.append(label)
if missing:
    sys.exit(f"{name}: named test not found: {', '.join(missing)}")
print(f"{name}: {len(labels)} named test(s) found")
PY
}

case "${1:-}" in
  list) cut -d'|' -f1 <<<"$MUTATIONS" ;;
  apply)
    row "${2:?name}"
    mutate write
    ;;
  check)
    while read -r name; do row "$name"; mutate check; tests_exist; done < <(cut -d'|' -f1 <<<"$MUTATIONS")
    ;;
  # A test is a method of the isolation class, or a full dotted label for any other test.
  test)
    row "${2:?name}"
    for t in ${TESTS//,/ }; do
      if [[ "$t" == *.* ]]; then printf '%s ' "$t"; else printf '%s.%s ' "$CLS" "$t"; fi
    done
    echo
    ;;
  # One regex per line; the run's log must match every line.
  expect)
    row "${2:?name}"
    # An assertion FAIL, not an ERROR: an error could come from unrelated breakage.
    # Anchored on the full label (unittest prints "FAIL: method (module.Class.method)").
    for t in ${TESTS//,/ }; do
      [[ "$t" == *.* ]] || t="$CLS.$t"
      anchor="^FAIL: ${t##*.} \(${t//./\\.}\)"
      if [[ -n "$SIGNATURE" ]]; then echo "$anchor|$SIGNATURE"; else echo "$anchor"; fi
    done
    ;;
  *) sed -n '5,10p' "$0"; exit 1 ;;
esac
