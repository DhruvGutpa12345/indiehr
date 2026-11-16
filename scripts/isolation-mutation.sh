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
#   scripts/isolation-mutation.sh env <name>     # NAME=value lines the test run needs (may be none)
#
# "apply" fails if the code it targets is not found, so a refactor cannot turn a mutation into
# a silent no-op. Never commit a mutated tree: CI applies these to a throwaway checkout.
set -euo pipefail
ROOT="${IHR_ROOT:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"  # IHR_ROOT: a scratch copy
CLS="ihr_platform.tests.isolation.test_isolation.CrossTenantIsolationTests"

# Isolation and security controls, one row each:
# name | file | exact text to find | replacement | tests that must all fail | leak signature | env
# The text to find must occur exactly once as a substring (not merely as a whole line).
# "leak signature": only where the leak already breaks building the two-tenant world (that is how
# the identifier-config leak first showed up), a regex for that leak's own error. A log line
# matching it counts in place of the test's FAIL; any other setUpClass error does not.
# "env" (optional): comma-separated NAME=value settings the test run needs, e.g. IHR_DB_ROLES=test
# for a control that only the tests run as the app role can see (P1-T19a).
# Retired rows (kept here, never in the table, so "list" stays one name per line):
# - identifier-lookup-cache (P1-T15b2a): CARE's get_config no longer reads its per-process dict
#   (patient.py _key); ihr_platform/identifier_cache.py swaps in CARE's Redis-backed
#   model_from_cache, whose keys carry the schema only through the cache key function. Row
#   cache-key-function now names test_identifier_config_lookup_cache_is_per_tenant, and rows
#   identifier-config-swap, -check-registered, -check-itself, -behaviour and -after-commit prove
#   the swap itself (review C5: each names tests that must FAIL, none rests on E025's error).
MUTATIONS=$(cat <<'TABLE'
cache-key-function|plugins/ihr_platform/ihr_platform/settings/tenancy.py|        cache["KEY_FUNCTION"] = "django_tenants.cache.make_key"\n        cache["REVERSE_KEY_FUNCTION"] = "django_tenants.cache.reverse_key"|        pass|test_zz_redis_keyspace_is_tenant_prefixed,test_crawl_never_shows_the_other_tenants_data,test_pack_decision_limits_are_per_hospital,test_identifier_config_lookup_cache_is_per_tenant,ihr_refdata.tests.test_api.DecisionTests.test_both_limits_are_kept_under_this_hospitals_schema||IHR_DB_ROLES=test
file-key-prefix|plugins/ihr_platform/ihr_platform/settings/tenancy.py|    settings["FILE_OBJECT_KEY_PREFIX_FUNCTION"] = (\n        "ihr_platform.storage.tenant_key_prefix"\n    )|    pass|test_file_object_keys_carry_the_tenant|
cover-image-prefix|apps/backend/care/utils/file_uploads/cover_image.py|    image_key = f"{key_prefix()}{folder}/|    image_key = f"{folder}/|test_file_object_keys_carry_the_tenant|
otp-tid-check|apps/backend/config/patient_otp_token.py|        if schema and self.payload.get("tid") != schema:|        if False:|test_patient_otp_token_from_a_is_rejected_on_b|
identifier-config-cache|apps/backend/care/emr/signals/patient/base.py|        cached_key = f"{schema}:{facility_key}:{cls.IDENTIFIER_SYSTEM}"|        cached_key = f"{facility_key}:{cls.IDENTIFIER_SYSTEM}"|test_patient_identifier_config_cache_is_per_tenant|emr_patientidentifie_config_id_[0-9a-f]+_fk
identifier-config-swap|plugins/ihr_platform/ihr_platform/apps.py|        identifier_cache.install()\n        checks.register(identifier_cache.system_check)|        pass|ihr_platform.tests.test_identifier_cache.SwapTests.test_a_save_is_seen_at_once_by_another_process,ihr_platform.tests.test_identifier_cache.SwapTests.test_the_swap_is_in_place_and_checked,ihr_platform.tests.test_identifier_cache.SwapTests.test_a_reader_before_the_commit_never_keeps_the_old_config||IHR_DB_ROLES=test
identifier-config-check-registered|plugins/ihr_platform/ihr_platform/apps.py|        checks.register(identifier_cache.system_check)|        pass|ihr_platform.tests.test_identifier_cache.SwapTests.test_the_check_is_registered||IHR_DB_ROLES=test
identifier-config-check-itself|plugins/ihr_platform/ihr_platform/identifier_cache.py|    if not getattr(getattr(current, "__func__", None), _SWAPPED, False):|    if False:|ihr_platform.tests.test_identifier_cache.SwapTests.test_the_swap_is_in_place_and_checked||IHR_DB_ROLES=test
identifier-config-behaviour|plugins/ihr_platform/ihr_platform/identifier_cache.py|    data = model_from_cache(PatientIdentifierListSpec, external_id=config_id)|    return cls.__dict__["get_config"].ihr_original.__func__(cls, config_id)\n    data = model_from_cache(PatientIdentifierListSpec, external_id=config_id)|ihr_platform.tests.test_identifier_cache.SwapTests.test_a_save_is_seen_at_once_by_another_process||IHR_DB_ROLES=test
identifier-config-after-commit|plugins/ihr_platform/ihr_platform/identifier_cache.py|    transaction.on_commit(partial(_forget, connection.schema_name, keys))|    pass|ihr_platform.tests.test_identifier_cache.SwapTests.test_a_reader_before_the_commit_never_keeps_the_old_config||IHR_DB_ROLES=test
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
audit-header-walk|plugins/ihr_platform/ihr_audit/incremental.py|           s.prev_hash <> coalesce(s.prev_stored, {genesis}) AS link,|           false AS link,|ihr_audit.tests.test_watch.HeaderWalkTests.test_a_rewritten_seal_below_the_checkpoint_is_found|
terminology-remote-refusal|plugins/ihr_platform/ihr_terminology/client.py|    care_client.FHIRClient.query = _refuse|    pass|ihr_terminology.tests.test_terminology.NothingLeaves.test_care_client_is_ours_and_the_remote_one_refuses|
terminology-install|plugins/ihr_platform/ihr_terminology/apps.py|        client.install()|        pass|ihr_terminology.tests.test_terminology.NothingLeaves.test_care_client_is_ours_and_the_remote_one_refuses,ihr_terminology.tests.test_terminology.NothingLeaves.test_no_http_during_terminology_calls|
role-sync-guard|plugins/ihr_platform/ihr_roles/management/commands/sync_permissions_roles.py|        if held:|        if False:|ihr_roles.tests.test_guarded_sync.GuardedSync.test_refuses_to_delete_a_held_role|
patient-global-edit-refused|plugins/ihr_platform/ihr_platform/settings/secure.py|    if settings.get("PATIENT_GLOBAL_EDIT_ACCESS_ENABLED"):|    if False:|ihr_platform.tests.test_secure_settings.SecureRulesTests.test_patient_global_edit_access_is_refused|
packs-edited-row-kept|plugins/ihr_platform/ihr_refdata/engine.py|        elif record is not None and record.applied_sha256 == have:|        elif record is not None:|ihr_refdata.tests.test_engine.RuleTests.test_an_edited_row_waits_for_the_admin|
packs-api-gate|plugins/ihr_platform/ihr_refdata/api.py|GATE = (IsHospitalAdmin,)|from rest_framework.permissions import AllowAny\nGATE = (AllowAny,)|ihr_refdata.tests.test_api.GateTests.test_only_a_hospital_wide_admin_may_call_it,ihr_access.tests.test_role_matrix.RoleMatrix.test_role_matrix||IHR_DB_ROLES=test
packs-api-atomic|plugins/ihr_platform/ihr_refdata/api.py|        return transaction.non_atomic_requests(super().as_view(**initkwargs))|        return super().as_view(**initkwargs)|ihr_refdata.tests.test_api.CommitInsideTheLock.test_the_decision_is_committed_before_the_lock_is_released||IHR_DB_ROLES=test
forms-guard|plugins/ihr_platform/ihr_refdata/forms_guard.py|        for table, trigger, *_rest in EXPECTED:\n            # Every time|        for table, trigger, *_rest in EXPECTED[1:]:\n            # Every time|ihr_refdata.tests.test_forms.GuardTests.test_an_answered_form_refuses_frozen_edits_by_the_orm_and_raw_sql,ihr_refdata.tests.test_forms.GuardTests.test_the_guard_is_installed_as_this_builds||IHR_DB_ROLES=test
forms-answer-lock|plugins/ihr_platform/ihr_refdata/forms_guard.py|            FOR SHARE;|            ;|ihr_refdata.tests.test_forms.AnswerRace.test_an_answer_being_given_holds_off_a_frozen_change||IHR_DB_ROLES=test
forms-in-place|plugins/ihr_platform/ihr_refdata/forms.py|    models = _models()\n    new = _new_row(item)|    for name in FIELDS[1:]:\n        setattr(row, name, item[name])\n    row.version = str(wanted)\n    row.save()\n    row.ihr_change = {"target": row.slug}\n    return row\n    models = _models()\n    new = _new_row(item)|ihr_refdata.tests.test_forms.NewVersionTests.test_a_new_version_never_writes_the_old_row||IHR_DB_ROLES=test
forms-answer-delete|plugins/ihr_platform/ihr_refdata/forms_guard.py|    IF TG_OP = 'DELETE' THEN\n        RAISE EXCEPTION 'ihr_forms: answers are never deleted|    IF TG_OP = 'DELETE' THEN\n        RETURN OLD;\n        RAISE EXCEPTION 'ihr_forms: answers are never deleted|ihr_refdata.tests.test_forms.GuardTests.test_hard_deletes_of_answers_are_refused||IHR_DB_ROLES=test
forms-answer-relink|plugins/ihr_platform/ihr_refdata/forms_guard.py|        IF NEW.questionnaire_id IS DISTINCT FROM OLD.questionnaire_id THEN|        IF false THEN|ihr_refdata.tests.test_forms.GuardTests.test_the_relink_detour_is_refused_at_its_first_step||IHR_DB_ROLES=test
forms-reserved-slug|plugins/ihr_platform/ihr_refdata/forms_guard.py|        IF left(lower(NEW.slug), 9) = 'ihr-pack-' AND NOT may_name THEN|        IF false THEN|ihr_refdata.tests.test_forms.GuardTests.test_the_pack_namespace_is_reserved||IHR_DB_ROLES=test
forms-answer-snapshot|plugins/ihr_platform/ihr_refdata/forms_guard.py|    committed_only boolean := current_setting('transaction_isolation') = 'read committed';|    committed_only boolean := true;|ihr_refdata.tests.test_forms.AnswerRace.test_a_repeatable_read_snapshot_cannot_edit_after_an_answer||IHR_DB_ROLES=test
forms-answer-truncate|plugins/ihr_platform/ihr_refdata/forms_guard.py|        IF coalesce(current_setting('ihr.answers_truncate', true), '') <> 'on' THEN|        IF false THEN|ihr_refdata.tests.test_forms.GuardOwnerTests.test_truncate_of_answers_is_refused||IHR_DB_ROLES=test
forms-pack-freed|plugins/ihr_platform/ihr_refdata/forms_guard.py|        IF left(lower(OLD.slug), 9) = 'ihr-pack-' AND NOT may_name THEN|        IF false THEN|ihr_refdata.tests.test_forms.GuardOwnerTests.test_a_pack_form_is_never_freed_without_the_setting||IHR_DB_ROLES=test
forms-trigger-elsewhere|plugins/ihr_platform/ihr_refdata/forms_guard.py|        if held_function != function:|        if False:|ihr_refdata.tests.test_forms.GuardInstallTests.test_owner_tampering_is_found_by_the_check_and_the_gate||IHR_DB_ROLES=test
forms-fleet-repair|plugins/ihr_platform/ihr_platform/fleet.py|            ("answered-form guard", forms_guard),\n        ):\n            try:|        ):\n            try:|ihr_platform.tests.test_release.FleetTests.test_check_reports_and_migrate_repairs_a_disabled_forms_guard|
release-gate|plugins/ihr_platform/ihr_platform/settings/tenancy.py|        "ihr_platform.release_gate.ReleaseGateMiddleware",\n||test_gated_hospital_shows_nothing|
release-gate-auth|plugins/ihr_platform/ihr_identity/authentication.py|    if not release.servable(tenant):  # held, or not on this build's schema (P1-T18a)|    if False:|ihr_platform.tests.test_release.DefenceInDepthTests.test_staff_and_service_sign_ins_refuse|
release-gate-beat|plugins/ihr_platform/ihr_platform/beat.py|        return active.filter(release.ready_q())|        return active|ihr_platform.tests.test_release.DefenceInDepthTests.test_beat_skips_a_held_hospital|
release-gate-staff-worker|plugins/ihr_platform/ihr_platform/invitations.py|            .filter(status=StaffInvitation.Status.QUEUED)\n            .filter(release.ready_q("hospital__"))|            .filter(status=StaffInvitation.Status.QUEUED)|ihr_platform.tests.test_release.DefenceInDepthTests.test_the_staff_worker_waits_and_never_fails_the_work|
release-gate-celery-task|plugins/ihr_platform/ihr_platform/apps.py|        celery_gate.install()|        pass|ihr_platform.tests.test_release.CeleryGateTests.test_a_queued_task_never_runs_in_a_held_hospital|
release-roles-hold-released|plugins/ihr_platform/ihr_platform/fleet.py|                release.release_hold(passed, reason=release.HOLD_ROLES)|                pass|ihr_platform.tests.test_release.FleetTests.test_a_role_refusal_hold_is_released_by_its_resume|
release-roles-never-lift-window|plugins/ihr_platform/ihr_platform/fleet.py|                release.release_hold(passed, reason=release.HOLD_ROLES)|                release.release_hold(passed)|ihr_platform.tests.test_release.FleetTests.test_a_run_whose_roles_pass_never_lifts_a_window_hold|
db-roles-audit-append-only|plugins/ihr_platform/ihr_platform/db_roles.py|    "append": Grant(frozenset({"SELECT"}), (("INSERT", AUDIT_DATA_COLUMNS),)),|    "append": Grant(DML, (("INSERT", AUDIT_DATA_COLUMNS),)),|ihr_platform.tests.test_db_roles.AppRoleTests.test_audit_entries_are_append_only_by_privilege||IHR_DB_ROLES=test
db-role-refusal-closes|plugins/ihr_platform/ihr_platform/db_roles.py|    connection.close()\n    msg = (\n        f"Refusing to use the database as|    msg = (\n        f"Refusing to use the database as|ihr_platform.tests.test_db_roles.RefusalTests.test_a_refused_role_stays_refused|
db-roles-queue-guard|plugins/ihr_platform/ihr_platform/queue_guard.py|    NEW.hospital_id := NULL;|    NULL;|ihr_platform.tests.test_db_roles.AppRoleTests.test_a_forged_operation_is_only_a_new_request||IHR_DB_ROLES=test
db-roles-provisioner-built-by|plugins/ihr_platform/ihr_platform/provisioning/runner.py|    if hospital is None or hospital.provisioning_operation != op.pk:\n        raise ProvisioningError("This operation did not build the hospital it names.")|    pass|ihr_platform.tests.test_db_roles.ProvisionerInputTests.test_only_the_building_operation_reaches_its_hospital|
db-roles-hospital-gated|plugins/ihr_platform/ihr_platform/release.py|    if hospital.schema_name in db_roles.gated_schemas():\n        return False|    pass|ihr_platform.tests.test_db_roles.GatedHospitalTests.test_a_gated_hospital_is_not_served|
db-roles-column-grants-kept|plugins/ihr_platform/ihr_platform/db_roles.py|            grants = [f.statement for f in after if f.statement is not None]|            pass|ihr_platform.tests.test_db_roles.GrantsTests.test_a_table_revoke_keeps_the_column_grants||IHR_DB_ROLES=test
db-roles-masking-helper|plugins/ihr_platform/ihr_platform/db_roles.py|    "ihr_audit_last4": {APP},\n||ihr_platform.tests.test_db_roles.GrantsTests.test_a_new_hospitals_masking_helper_is_the_apps||IHR_DB_ROLES=test
db-roles-controls-refuse-or-gate|plugins/ihr_platform/ihr_platform/db_roles.py|        elif item.kind == "problem" and item.control:|        elif False:|ihr_platform.tests.test_db_roles.GrantsTests.test_a_broken_control_refuses_or_gates||IHR_DB_ROLES=test
db-roles-fleet-converges-every-schema|plugins/ihr_platform/ihr_platform/fleet.py|        self._converge_rest()\n||ihr_platform.tests.test_release.FleetTests.test_a_fleet_wide_migrate_repairs_every_hospital_schema||IHR_DB_ROLES=test
public-doctor-fields|plugins/ihr_platform/ihr_platform/public_routes.py|    pydantic_read_model = PublicPractitionerSpec\n    pydantic_retrieve_model = PublicPractitionerSpec|    from care.emr.resources.user.spec import PublicUserReadSpec as pydantic_read_model|test_public_doctor_list_shows_only_name_title_and_photo|
public-doctor-public-schedule|plugins/ihr_platform/ihr_platform/public_routes.py|        schedule__is_public=True,\n||test_public_doctor_list_shows_only_name_title_and_photo|
public-otp-booking-trim|plugins/ihr_platform/ihr_platform/public_routes.py|        _pick(data.get("resource"), RESOURCE_KEYS.get(data.get("resource_type"), ()))|        data.get("resource")|ihr_platform.tests.test_public_routes.PublicBookingTests.test_each_resource_type_keeps_its_allow_list|
public-otp-booking-from-model|plugins/ihr_platform/ihr_platform/public_routes.py|    return public_booking(PublicTokenBookingSpec.serialize(booking).to_json())|    from care.emr.resources.scheduling.slot.spec import TokenBookingOTPReadSpec\n\n    return public_booking(TokenBookingOTPReadSpec.serialize(booking).to_json())|test_patient_otp_bookings_of_a_location_or_service_show_its_name_only|
public-doctor-last-day|plugins/ihr_platform/ihr_platform/public_routes.py|        schedule__valid_to__date__gte=timezone.localdate(),|        schedule__valid_to__gte=timezone.now(),|test_public_doctor_list_shows_only_name_title_and_photo|
public-govt-only|plugins/ihr_platform/ihr_platform/public_routes.py|        return super().get_queryset().filter(org_type="govt")|        return super().get_queryset()|test_public_organisations_are_government_only|
public-facility-raw-key-field|plugins/ihr_platform/ihr_platform/public_routes.py|        data.pop("cover_image_url", None)|        pass|test_public_facility_finder_drops_raw_key_field|
public-route-swap|plugins/ihr_platform/ihr_platform/apps.py|        route_swaps.install()|        pass|test_care_routes_reach_our_classes,ihr_platform.tests.test_public_routes.RouteSwapTests.test_care_router_serves_our_classes|CARE.s own views would be served
route-swap-system-check|plugins/ihr_platform/ihr_platform/apps.py|        checks.register(route_swaps.system_check)|        pass|ihr_platform.tests.test_public_routes.RouteSwapRefusalTests.test_django_runs_the_system_check|
restore-sequence-advance|plugins/ihr_platform/ihr_platform/restore/engine.py|            if (p_value, p_called) > (value, called):|            if False:|ihr_platform.tests.test_restore.RestoreEndToEnd.test_sequences_are_advanced_past_the_parked_copy|
restore-session-invalidation|plugins/ihr_platform/ihr_platform/restore/engine.py|"UPDATE {r}.ihr_identity_staffidentity SET sessions_valid_after = clock_timestamp()"|"UPDATE {r}.ihr_identity_staffidentity SET sessions_valid_after = sessions_valid_after"|ihr_platform.tests.test_restore.RestoreEndToEnd.test_every_staff_session_is_invalidated|
restore-final-seal|plugins/ihr_platform/ihr_platform/restore/engine.py|                final = self._final_seal(cursor, schema)|                final = 0|ihr_platform.tests.test_restore.RestoreEndToEnd.test_the_live_tail_is_sealed_before_the_park|
restore-sequences-before-migrate|plugins/ihr_platform/ihr_platform/restore/engine.py|            loaded["sequences"] = self._sequences(cursor)|            loaded["sequences"] = {}|ihr_platform.tests.test_restore.RestoreEndToEnd.test_ids_written_during_migrate_are_never_the_parked_copys|
restore-patient-grants|plugins/ihr_platform/ihr_platform/restore/engine.py|    "emr_patientuser",\n||ihr_platform.tests.test_restore.RestoreEndToEnd.test_patient_grants_and_shares_taken_back_stay_so|
restore-drain-jobs|plugins/ihr_platform/ihr_platform/restore/engine.py|                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])|                cursor.execute("SELECT 1")|ihr_platform.tests.test_restore.DrainTests.test_the_drain_waits_for_running_jobs|
restore-expressions-inspected|plugins/ihr_platform/ihr_platform/restore/package.py|    if problems := expression_problems(cur, schema):\n        raise PackageError("; ".join(problems[:10]))\n    rows = 0|    rows = 0|ihr_platform.tests.test_restore.PackageTests.test_a_forged_check_is_inspected_before_anything_evaluates_it|
restore-constraint-form|plugins/ihr_platform/ihr_platform/restore/package.py|    form = _CONSTRAINT_FORMS[kind].fullmatch("".join(shape))|    form = _CONSTRAINT_FORMS[kind].match("".join(shape))|ihr_platform.tests.test_restore.PackageTests.test_a_constraint_statement_carries_nothing_after_its_constraint|
restore-nextval-constant|plugins/ihr_platform/ihr_platform/restore/package.py|            for args in _nextval_arguments(tree, nextval)|            for args in []|ihr_platform.tests.test_restore.PackageTests.test_nextval_of_a_sequence_named_at_run_time_is_refused|
restore-chain-recomputed|plugins/ihr_platform/ihr_platform/restore/engine.py|            loaded["chain"] = self._restored_chain(cursor)|            loaded["chain"] = {}|ihr_platform.tests.test_restore.RestoreEndToEnd.test_w1_a_dump_whose_sealed_entries_do_not_verify_stops_before_the_commit|
restore-trail-defaults|plugins/ihr_platform/ihr_platform/restore/engine.py|        install.ensure_defaults(cursor, schema)\n||ihr_platform.tests.test_restore.PackageTests.test_the_restore_gives_the_trail_this_builds_defaults|
restore-job-lock-celery|plugins/ihr_platform/ihr_platform/celery_gate.py|    with release.job(schema) as taken:|    with release.job("public") as taken:|ihr_platform.tests.test_restore.JobLockTests.test_each_job_holds_its_hospitals_lock_while_it_runs|
restore-job-lock-staff-worker|plugins/ihr_platform/ihr_platform/invitations.py|            release.job(inv.hospital.schema_name) as taken,|            release.job("public") as taken,|ihr_platform.tests.test_restore.JobLockTests.test_each_job_holds_its_hospitals_lock_while_it_runs|
restore-job-lock-no-wait|plugins/ihr_platform/ihr_platform/release.py|        cursor.execute("SELECT pg_try_advisory_lock_shared(%s)", [key])|        cursor.execute("SELECT true FROM pg_advisory_lock_shared(%s)", [key])|ihr_platform.tests.test_restore.JobLockTests.test_no_job_queues_behind_a_waiting_drain|
restore-hold-served|plugins/ihr_platform/ihr_platform/release.py|        and row[2] is None  # the restore hold (P1-T22b)\n||ihr_platform.tests.test_restore.RestoreHoldTests.test_a_held_hospital_is_not_served_anywhere|
restore-hold-fleet|plugins/ihr_platform/ihr_platform/fleet.py|        self._leave_restores()\n||ihr_platform.tests.test_restore.RestoreHoldTests.test_the_fleet_runner_leaves_it_alone|
route-swap-required|plugins/ihr_platform/ihr_platform/urls_tenant.py|route_swaps.require()|route_swaps.check()|ihr_platform.tests.test_public_routes.RouteSwapRefusalTests.test_the_url_conf_refuses_to_load|
picture-link-signed|plugins/ihr_platform/ihr_platform/settings/tenancy.py|    settings["FACILITY_IMAGE_URL_FUNCTION"] = "ihr_platform.storage.facility_image_url"|    pass|test_picture_links_are_signed_for_this_hospital_only|
picture-link-own-hospital|plugins/ihr_platform/ihr_platform/storage.py|    if not match or not key.startswith(prefix) or f"t/{match['hospital']}/" != prefix:|    if not match:|test_picture_links_are_signed_for_this_hospital_only,ihr_platform.tests.test_storage.PictureLinkTests.test_only_keys_of_this_hospital_and_of_the_upload_shape|
picture-link-role-expiry|plugins/ihr_platform/ihr_platform/storage.py|    return max(0, min(CACHE_SECONDS, int(left)))|    return CACHE_SECONDS|ihr_platform.tests.test_storage.RoleBasedLinkTests.test_cached_no_longer_than_the_credentials_live|
files-own-key|apps/backend/care/utils/csp/config.py|    params = {"region_name": settings.FILE_UPLOAD_REGION}  # IndieHR: own key\n    if CSProvider.AWS_ROLE_BASED.value != settings.BUCKET_PROVIDER:\n        params["aws_access_key_id"] = settings.FILE_UPLOAD_KEY\n        params["aws_secret_access_key"] = settings.FILE_UPLOAD_SECRET\n        params["endpoint_url"] = (\n            settings.FILE_UPLOAD_BUCKET_EXTERNAL_ENDPOINT\n            if external\n            else settings.FILE_UPLOAD_BUCKET_ENDPOINT\n        )\n    return params, settings.FILE_UPLOAD_BUCKET\n\n\ndef get_report|    params = {"region_name": settings.FILE_UPLOAD_REGION}\n    if CSProvider.AWS_ROLE_BASED.value != settings.BUCKET_PROVIDER:\n        params["aws_access_key_id"] = settings.FACILITY_S3_KEY\n        params["aws_secret_access_key"] = settings.FACILITY_S3_SECRET\n        params["endpoint_url"] = (\n            settings.FILE_UPLOAD_BUCKET_EXTERNAL_ENDPOINT\n            if external\n            else settings.FILE_UPLOAD_BUCKET_ENDPOINT\n        )\n    return params, settings.FILE_UPLOAD_BUCKET\n\n\ndef get_report|ihr_platform.tests.test_storage.CredentialsPerBucketTests.test_each_bucket_signs_with_its_own_key|
separate-bucket-keys|plugins/ihr_platform/ihr_platform/settings/secure.py|    elif files[0] == pictures[0] or files[1] == pictures[1]:|    elif False:|ihr_platform.tests.test_secure_settings.SecureRulesTests.test_pictures_are_signed_and_each_bucket_has_its_own_key|
batch-allow-list|plugins/ihr_platform/ihr_platform/batch.py|        elif not listed(wsgi.method, wsgi.path_info, match):|        elif False:|test_batch_refuses_unreviewed_sub_requests_and_runs_nothing|
audit-omit-organisation-cache|plugins/ihr_platform/ihr_audit/install.py|    "emr_organization": ("cached_parent_json",),\n||test_anonymous_gets_write_no_audit_entry|
audit-triggers-ship-with-migration|plugins/ihr_platform/ihr_audit/install.py|        repr(sorted(OMIT.items())),\n||ihr_audit.tests.test_audit_trail.TriggerChangesShipWithAMigration.test_the_latest_migration_states_this_builds_trigger_configuration|
access-guard-install|plugins/ihr_platform/ihr_access/apps.py|        guard.install()|        pass|ihr_access.tests.test_guard.EncounterProjection.test_projection_keeps_only_listed_fields,ihr_access.tests.test_role_matrix.RoleMatrix.test_clinical_canary_never_reaches_restricted_roles|the access guard does not hold|
access-guard-applies|plugins/ihr_platform/ihr_access/guard.py|        if shape.applies(current, obj):|        if False:|ihr_access.tests.test_guard.EncounterProjection.test_projection_keeps_only_listed_fields,ihr_access.tests.test_guard.ThroughTheApi.test_cashier_and_tpa_see_the_projection_doctor_sees_everything|
access-guard-keeplist|plugins/ihr_platform/ihr_access/guard.py|        verb = rules.get(name)|        verb = "keep"|ihr_access.tests.test_guard.EncounterProjection.test_projection_keeps_only_listed_fields|
access-request-state|plugins/ihr_platform/ihr_access/apps.py|            state.begin(request.user)|            pass|ihr_access.tests.test_guard.ThroughTheApi.test_cashier_and_tpa_see_the_projection_doctor_sees_everything|
access-device-encounter|plugins/ihr_platform/ihr_access/fields/device.txt|current_encounter drop  # the patient on the device|current_encounter keep  # the patient on the device|ihr_access.tests.test_guard.BedAndDevice.test_the_owner_sees_no_one_on_a_device|
access-identifier-allowlist|plugins/ihr_platform/ihr_access/guard.py|        kept = [i for i in (value or []) if _system_of(i) in allowed]|        kept = list(value or [])|ihr_access.tests.test_guard.PatientProjection.test_programme_identifier_and_clinical_fields_are_hidden|
access-permissions-filter|plugins/ihr_platform/ihr_access/guard.py|    return isinstance(slug, str) and (\n        slug.startswith("ihr_") or slug not in NEVER_RESTRICTED\n    )|    return True|ihr_access.tests.test_guard.PatientProjection.test_permissions_hold_no_clinical_slug|
access-patient-user-roles|plugins/ihr_platform/ihr_access/authz.py|    if current.restricted:|    if False:|ihr_access.tests.test_patient_user.PatientUserRule.test_moved_nurse_loses_patient_user_read,ihr_access.tests.test_patient_user.PatientUserRule.test_a_moved_nurse_with_another_non_clinical_role_loses_the_row_too,ihr_access.tests.test_role_matrix.RoleMatrix.test_role_matrix,ihr_access.tests.test_role_matrix.RoleMatrix.test_clinical_canary_never_reaches_restricted_roles|
access-patient-user-gate|plugins/ihr_platform/ihr_access/authz.py|    if current.restricted:|    if current.restricted_only:|ihr_access.tests.test_patient_user.PatientUserRule.test_a_moved_nurse_with_another_non_clinical_role_loses_the_row_too|
access-patient-list|plugins/ihr_platform/ihr_access/authz.py|        if not getattr(user, "is_superuser", False) and state.for_user(user).restricted:|        if False:|ihr_access.tests.test_patient_user.PatientUserRule.test_patient_list_of_restricted_users,ihr_access.tests.test_patient_user.PatientUserRule.test_a_moved_nurse_with_another_non_clinical_role_loses_the_row_too|
records-read-time|plugins/ihr_platform/ihr_access/authz.py|    granted = hidden in roles and patient.pk in current.granted_patients|    granted = hidden in roles|ihr_access.tests.test_patient_user.PatientUserRule.test_hidden_role_counts_only_under_a_live_grant|
access-placement-install|plugins/ihr_platform/ihr_access/apps.py|        post_migrate.connect(_install, sender=self, dispatch_uid="ihr_access_placement")|        pass|ihr_access.tests.test_placement.OrganisationRules.test_the_trigger_is_installed,ihr_access.tests.test_placement.OrganisationRules.test_radiologist_refused_at_root|
access-root-placement|plugins/ihr_platform/ihr_access/placement.py|    IF role_name = ANY({department_only}) AND EXISTS (|    IF false AND EXISTS (|ihr_access.tests.test_placement.OrganisationRules.test_radiologist_refused_at_root|
records-trigger|plugins/ihr_platform/ihr_access/placement.py|                  AND g.ended_at IS NULL AND g.expires_at > now()\n            ) THEN|                  AND g.ended_at IS NULL AND g.expires_at > now()\n            ) AND false THEN|ihr_access.tests.test_placement.PatientUserRules.test_patient_user_with_records_role_needs_a_grant|
access-restricted-patient-user|plugins/ihr_platform/ihr_access/placement.py|        IF is_restricted THEN|        IF false THEN|ihr_access.tests.test_placement.PatientUserRules.test_patient_user_refused_for_a_restricted_only_user,ihr_access.tests.test_placement.PatientUserRules.test_patient_user_refused_for_a_restricted_person_with_another_non_clinical_role|
access-patient-user-check-gate|plugins/ihr_platform/ihr_access/management/commands/ihr_patient_user_check.py|            restricted[row.user_id] = state.restricted(row.user, role_ids)|            restricted[row.user_id] = state.restricted_only(row.user, role_ids)|ihr_access.tests.test_patient_user.PatientUserRule.test_a_moved_nurse_with_another_non_clinical_role_loses_the_row_too|
access-check-records-rows|plugins/ihr_platform/ihr_access/management/commands/ihr_patient_user_check.py|        if row.role_id == hidden:|        if False:|ihr_access.tests.test_patient_user.PatientUserRule.test_check_command_keeps_records_access_under_a_live_grant|
access-placement-trigger-shape|plugins/ihr_platform/ihr_access/placement.py|        if tgtype != TRIGGER_TYPE or columns != _expected_columns(table) or extra:|        if False:|ihr_access.tests.test_placement.OrganisationRules.test_a_trigger_re_created_with_fewer_columns_is_drift_and_repaired|
access-placement-fingerprint|plugins/ihr_platform/ihr_roles/registry.py|DEPARTMENT_ONLY = frozenset({"Lab Technician", "Radiologist", "Store Keeper"})|DEPARTMENT_ONLY = frozenset({"Lab Technician", "Radiologist", "Store Keeper", "Cashier"})|ihr_access.tests.test_placement.GuardChangesShipWithAMigration.test_the_latest_migration_states_this_builds_guard|
access-placement-fleet-repair|plugins/ihr_platform/ihr_platform/fleet.py|        self._repair_placement(entry)\n        if self._repair_grants(entry):|        if self._repair_grants(entry):|ihr_platform.tests.test_release.FleetTests.test_check_reports_and_migrate_repairs_a_disabled_placement_guard,ihr_platform.tests.test_release.FleetTests.test_a_failed_grant_repair_still_repairs_the_placement_guard|
access-placement-400|plugins/ihr_platform/ihr_access/exceptions.py|            exc = ValidationError(reason)|            pass|ihr_access.tests.test_patient_user.PlacementRefusalsOverTheApi.test_a_department_role_at_the_root_answers_400,ihr_access.tests.test_patient_user.PlacementRefusalsOverTheApi.test_add_user_with_the_hidden_role_and_no_grant_answers_400|
access-encounter-tags|plugins/ihr_platform/ihr_access/fields/encounter.txt|tags drop  # tags can carry clinical labels|tags keep  # tags can carry clinical labels\ntags.* keep  # mutated|ihr_access.tests.test_guard.EncounterProjection.test_projection_keeps_only_listed_fields|
access-billed-bed-roles|plugins/ihr_platform/ihr_roles/registry.py|BED_FOR_BILLING = frozenset({"Cashier", "TPA Desk"})|BED_FOR_BILLING = frozenset({"Cashier", "TPA Desk", "Receptionist", "MRD"})|ihr_access.tests.test_guard.ThroughTheApi.test_other_desk_roles_still_see_no_bed|
access-billed-bed-facility|plugins/ihr_platform/ihr_access/state.py|                    organization__facility_id=facility_id,\n||ihr_access.tests.test_guard.ThroughTheApi.test_the_bed_needs_the_billing_role_in_the_visits_facility|
access-billed-bed-ward|plugins/ihr_platform/ihr_access/fields/encounter_billing.txt|location_history.location.name keep  # the bed's name|location_history.location.name keep  # the bed's name\nlocation_history.location.parent keep  # mutated|ihr_access.tests.test_guard.ThroughTheApi.test_billing_roles_see_the_bed_for_room_rent|
access-billed-bed-tpa-add|plugins/ihr_platform/ihr_roles/registry.py|        "can_update_account",\n        "can_read_charge_item",\n        "can_read_invoice",|        "can_update_account",\n        "can_read_charge_item",\n        "can_create_charge_item",\n        "can_read_invoice",|ihr_access.tests.test_guard.ThroughTheApi.test_the_cashier_adds_room_rent_with_the_projected_stay|
access-patient-tags|plugins/ihr_platform/ihr_access/fields/patient.txt|instance_tags drop  # tags can carry clinical labels|instance_tags keep  # tags can carry clinical labels\ninstance_tags.* keep  # mutated|ihr_access.tests.test_guard.PatientProjection.test_patient_tags_are_hidden|
access-placement-ensure-replaces|plugins/ihr_platform/ihr_access/placement.py|            cursor.execute(\n                sql.SQL(TRIGGER_SQL).format(|            if table in _triggers(cursor, schema):\n                continue\n            cursor.execute(\n                sql.SQL(TRIGGER_SQL).format(|ihr_access.tests.test_placement.OrganisationRules.test_a_trigger_re_created_with_fewer_columns_is_drift_and_repaired|
access-placement-gate|plugins/ihr_platform/ihr_platform/db_roles.py|        guards = _placement_problems(cursor, schemas)|        guards = {}|ihr_platform.tests.test_db_roles.GrantsTests.test_a_broken_placement_guard_gates_its_hospital||IHR_DB_ROLES=test
access-placement-batch-handler|plugins/ihr_platform/ihr_access/exceptions.py|            module.emr_exception_handler = wrapper|            pass|ihr_access.tests.test_patient_user.PlacementRefusalsOverTheApi.test_a_handler_bound_by_name_before_the_wrap_is_rebound|
access-identifier-write-search|plugins/ihr_platform/ihr_access/identifiers.py|    return inner.get("system") not in allowed_identifier_systems()|    return False|ihr_access.tests.test_patient_user.IdentifierAllowListOnSearchAndWrites.test_receptionist_cannot_search_by_a_programme_identifier,ihr_access.tests.test_patient_user.IdentifierAllowListOnSearchAndWrites.test_receptionist_cannot_update_a_programme_identifier|
access-placement-temp-deleted|plugins/ihr_platform/ihr_access/placement.py| AND NOT rp.deleted AND NOT rp.temp_deleted| AND NOT rp.deleted|ihr_access.tests.test_placement.PatientUserRules.test_a_grant_being_deleted_by_the_role_sync_is_not_clinical|
access-restricted-temp-deleted|plugins/ihr_platform/ihr_access/state.py|        temp_deleted=False,\n        permission__slug__in=CLINICAL_SLUGS,|        permission__slug__in=CLINICAL_SLUGS,|ihr_access.tests.test_placement.PatientUserRules.test_a_grant_being_deleted_by_the_role_sync_is_not_clinical|
access-forced-projection|plugins/ihr_platform/ihr_access/branches.py|    state.force_projection()|    pass|ihr_access.tests.test_patient_swap.MixedRoles.test_mixed_roles_branch_is_still_projected|
access-search-gate|plugins/ihr_platform/ihr_access/branches.py|    if not slugs:\n        return REFUSED|    if False:\n        return REFUSED|ihr_access.tests.test_patient_swap.SearchGate.test_search_refused_to_roles_without_patient_access|
access-add-user-gate|plugins/ihr_platform/ihr_access/views.py|        if target is not None and state.restricted(target):|        if False:|ihr_access.tests.test_patient_swap.OpeningAPatient.test_add_user_refuses_restricted_target|
access-update-action-allowlist|plugins/ihr_platform/ihr_access/views.py|        return self.action in which|        return True|ihr_access.tests.test_patient_swap.OpeningAPatient.test_receptionist_cannot_add_user_or_tag|
access-edit-hidden-fields|plugins/ihr_platform/ihr_access/edit.py|WRITTEN_VERBS = frozenset({"edit"})|WRITTEN_VERBS = frozenset({"edit", "hidden"})|ihr_access.tests.test_patient_swap.EditingDemographics.test_receptionist_save_keeps_hidden_fields|
access-booking-note|plugins/ihr_platform/ihr_access/fields/booking.txt|note drop  # free text, possibly clinical (Q-K37)\nresource drop  # the booked doctor, room or service (Q-K37)|note keep  # free text, possibly clinical (Q-K37)\nresource keep  # the booked doctor, room or service (Q-K37)\nresource.* keep  # mutated|ihr_access.tests.test_patient_swap.BookingsAndTokens.test_receptionist_patient_appointments_are_projected,ihr_access.tests.test_role_matrix.RoleMatrix.test_clinical_canary_never_reaches_restricted_roles,ihr_access.tests.test_role_matrix.RoleMatrix.test_canary_exemptions_are_narrow|
access-encounter-filters|plugins/ihr_platform/ihr_access/views.py|        self._refuse_other_parameters()\n||ihr_access.tests.test_encounter_swap.StatusList.test_restricted_encounter_filters_refused|
access-retrieve-scope|plugins/ihr_platform/ihr_access/views.py|    def retrieve(self, request, *args, **kwargs):|    def authorize_retrieve(self, model_instance):\n        try:\n            return super().authorize_retrieve(model_instance)\n        except PermissionDenied:\n            if not self._status_retrievable(model_instance):\n                raise\n            branches.widen()\n            return True\n\n    def retrieve(self, request, *args, **kwargs):|ihr_access.tests.test_encounter_swap.OneEncounter.test_encounter_organizations_refused|
encounter-create-fields|plugins/ihr_platform/ihr_access/views.py|            refuse_clinical_fields(data)|            pass|ihr_access.tests.test_encounter_swap.CreatingAVisit.test_receptionist_cannot_write_discharge_advice|
access-bed-history|plugins/ihr_platform/ihr_access/views.py|            raise PermissionDenied(BED_HISTORY_REFUSED)|            pass|ihr_access.tests.test_further_swaps.BedHistoryAndResourceRequests.test_bed_history_refused_to_restricted,ihr_access.tests.test_role_matrix.RoleMatrix.test_role_matrix|
access-resource-request|plugins/ihr_platform/ihr_access/views.py|            raise PermissionDenied(RESOURCE_REQUESTS_REFUSED)|            pass|ihr_access.tests.test_further_swaps.BedHistoryAndResourceRequests.test_resource_requests_refused_to_restricted,ihr_access.tests.test_role_matrix.RoleMatrix.test_role_matrix|
access-account-report|plugins/ihr_platform/ihr_access/views.py|        return branches.restricted(self.request.user)|        return False|ihr_access.tests.test_further_swaps.AccountReports.test_account_report_refused_to_restricted|
access-delivery-patient|plugins/ihr_platform/ihr_access/views.py|    return queryset.filter(**{f"{path}__isnull": True})|    return queryset|ihr_access.tests.test_further_swaps.PatientReturns.test_store_keeper_never_sees_a_patient_return,ihr_access.tests.test_role_matrix.RoleMatrix.test_clinical_canary_never_reaches_restricted_roles,ihr_access.tests.test_role_matrix.RoleMatrix.test_role_matrix,ihr_access.tests.test_role_matrix.StoreRows.test_delivery_orders_of_a_request_order|
access-update-authorize-first|plugins/ihr_platform/ihr_access/views.py|                self.authorize_update(None, instance)|                pass|ihr_access.tests.test_patient_swap.WritesDecideBeforeTheBody.test_update_is_refused_before_the_body_is_read|
access-create-authorize-first|plugins/ihr_platform/ihr_access/views.py|            raise PermissionDenied(CREATE_REFUSED)|            pass|ihr_access.tests.test_patient_swap.WritesDecideBeforeTheBody.test_create_is_refused_before_the_body_is_read|
access-create-identifiers|plugins/ihr_platform/ihr_access/views.py|            data["identifiers"] = edit.allowed_identifiers(data["identifiers"])|            pass|ihr_access.tests.test_patient_swap.WritesDecideBeforeTheBody.test_registration_keeps_to_the_identifier_list|
access-update-identifier-branch|plugins/ihr_platform/ihr_access/views.py|            if self.action == "update_identifier" and identifiers.off_list(|            if False and identifiers.off_list(|ihr_access.tests.test_patient_swap.EditingDemographics.test_two_jobs_keep_to_the_identifier_list|
access-geo-move-slugs|plugins/ihr_platform/ihr_access/edit.py|    return {(user_id, slug) for user_id, role_id in rows for slug in slugs_of[role_id]}|    return {(user_id, None) for user_id, role_id in rows}|ihr_access.tests.test_patient_swap.EditingDemographics.test_a_district_move_compares_what_people_can_do|
access-geo-move-no-district|plugins/ihr_platform/ihr_access/edit.py|    if old is not None and new.id == old.id:\n        return None|    if old is None or new.id == old.id:\n        return None|ihr_access.tests.test_patient_swap.EditingDemographics.test_a_patient_with_no_district_moves_on_the_same_terms|
access-patient-facility-context|plugins/ihr_platform/ihr_access/views.py|        if self.ihr_opened:  # our branch: no facility tags to show|        if False:|ihr_access.tests.test_patient_swap.OpeningAPatient.test_the_web_apps_patient_request_with_the_facility|
access-encounter-unit-list|plugins/ihr_platform/ihr_access/views.py|            rows = self._rows().filter(patient=patient).filter(in_units(organizations))|            rows = self._rows().filter(patient=patient)|ihr_access.tests.test_encounter_swap.StatusList.test_a_front_desk_placed_in_one_unit_sees_that_unit_only|
access-encounter-unit-scope|plugins/ihr_platform/ihr_access/views.py|        return branches.holds_in(self.request.user, branches.STATUS, organizations)|        return True|ihr_access.tests.test_encounter_swap.StatusList.test_a_front_desk_placed_in_one_unit_sees_that_unit_only|
access-dispense-order|plugins/ihr_platform/ihr_access/views.py|        return branches.closed(user)|        return False|ihr_access.tests.test_further_swaps.PatientReturns.test_store_keeper_never_sees_a_dispense_order|
access-delivery-create-patient|plugins/ihr_platform/ihr_access/views.py|            raise PermissionDenied(PATIENT_LINK_REFUSED)|            pass|ihr_access.tests.test_further_swaps.PatientReturns.test_store_keeper_never_sees_a_patient_return|
access-delivery-create-order|plugins/ihr_platform/ihr_access/views.py|            if found.exists() and not without_patients(found, "patient").exists():|            if False:|ihr_access.tests.test_further_swaps.PatientReturns.test_store_keeper_never_sees_a_patient_return|
access-ours-only|plugins/ihr_platform/ihr_access/branches.py|    return restricted(user) or ours_only(user)|    return restricted(user)|ihr_access.tests.test_role_matrix.RoleMatrix.test_role_matrix,ihr_access.tests.test_role_matrix.RoleMatrix.test_clinical_canary_never_reaches_restricted_roles|
access-chart-rule|plugins/ihr_platform/ihr_access/guard.py|        if self.chart is None or not current.chart_limited:|        if True:|ihr_access.tests.test_role_matrix.RoleMatrix.test_clinical_canary_never_reaches_restricted_roles,ihr_access.tests.test_guard.EncounterProjection.test_the_radiologist_sees_a_chart_only_with_radiology|
access-encounter-base|plugins/ihr_platform/ihr_access/guard.py|        also=("care.emr.resources.encounter.spec.EncounterSpecBase",),|        also=(),|ihr_access.tests.test_role_matrix.SpecWalk.test_every_patient_and_encounter_spec_is_guarded_or_exempt|
access-visit-patient|plugins/ihr_platform/ihr_access/visits.py|            external_id=encounter_key, patient__external_id=patient_key|            external_id=encounter_key|ihr_access.tests.test_visits.VisitParam.test_another_patients_visit_opens_nothing,ihr_access.tests.test_role_matrix.RoleMatrix.test_role_matrix,ihr_access.tests.test_role_matrix.RoleMatrix.test_clinical_canary_never_reaches_restricted_roles,ihr_access.tests.test_role_matrix.RoleMatrix.test_post_reads|
access-visit-rows|plugins/ihr_platform/ihr_access/visits.py|    if rows is None or not all(_of_visit(m, e, encounter) for m, e in rows):|    if False:|ihr_access.tests.test_visits.VisitParam.test_a_finished_radiology_visit_opens_its_own_rows_only|
access-visit-install|plugins/ihr_platform/ihr_access/apps.py|        visits.install()|        pass|ihr_access.tests.test_visits.VisitParam.test_another_patients_visit_opens_nothing,ihr_access.tests.test_role_matrix.RoleMatrix.test_clinical_canary_never_reaches_restricted_roles|the access guard does not hold|
access-slot-day|plugins/ihr_platform/ihr_access/views.py|        if self.action == "get_slots_for_day" and branches.closed(request.user):|        if False:|ihr_access.tests.test_role_matrix.RoleMatrix.test_post_reads,ihr_access.tests.test_role_matrix.RoleMatrix.test_clinical_canary_never_reaches_restricted_roles|
access-list-params-pharmacist|plugins/ihr_platform/ihr_access/tests/list_params.txt|/api/v1/patient/{patient_external_id}/medication/request/ ?facility=facility  # CARE's pharmacist branch (authorize_for_pharmacist): no role here is a pharmacist of ours\n||ihr_access.tests.test_role_matrix.RoleMatrix.test_every_query_read_has_a_params_line|
access-list-params-patient-key|plugins/ihr_platform/ihr_access/tests/list_params.txt|/api/v1/patient/{patient_external_id}/symptom/ none:facility  # |/api/v1/patient/{patient_external_id}/symptom/ none  # |ihr_access.tests.test_role_matrix.ListParams.test_every_list_reading_the_query_has_a_row|
access-list-params-none-keys|plugins/ihr_platform/ihr_access/tests/list_params.txt|/api/v1/facility/{facility_external_id}/location/ none:mine,parent |/api/v1/facility/{facility_external_id}/location/ none |ihr_access.tests.test_role_matrix.RoleMatrix.test_every_query_read_has_a_params_line|
access-post-read-recorded|plugins/ihr_platform/ihr_access/tests/matrix.py|                    token = _route.set(f"POST {read.route}")  # review NEW-2|                    token = _route.set(None)  # review NEW-2|ihr_access.tests.test_role_matrix.RoleMatrix.test_every_query_read_has_a_params_line|
records-grant-scope|plugins/ihr_platform/ihr_access/authz.py|    return patient_id in state.for_user(user).granted_patients|    return bool(state.for_user(user).granted_patients)|ihr_access.tests.test_records.GrantedReads.test_grant_does_not_open_other_patient_reports,ihr_access.tests.test_role_matrix.RoleMatrix.test_role_matrix|
records-annotate|plugins/ihr_platform/ihr_audit/middleware.py|                    **context.patient_fields(everyone, ctx["purpose"]),|                    **{},|ihr_access.tests.test_records.RecordsAudit.test_records_request_entry_has_purpose,ihr_access.tests.test_records.RecordsAudit.test_only_reads_of_a_granted_patient_say_records|
records-annotate-scope|plugins/ihr_platform/ihr_audit/context.py|    ids = sorted({by_patient[p] for p in patients if p in by_patient})|    ids = sorted(set(by_patient.values()))|ihr_access.tests.test_records.RecordsAudit.test_only_reads_of_a_granted_patient_say_records|
records-expiry|plugins/ihr_platform/ihr_access/records.py|            end_grant(grant_id, "expired")|            pass|ihr_access.tests.test_records.GrantedReads.test_expired_grant_is_refused_before_the_beat,ihr_access.tests.test_records.GrantedReads.test_a_refused_request_still_ends_its_expired_grants|
records-expiry-after-view|plugins/ihr_platform/ihr_platform/settings/tenancy.py|        "ihr_access.middleware.RecordsExpiryMiddleware",\n||ihr_access.tests.test_records.GrantedReads.test_a_refused_request_still_ends_its_expired_grants|
records-lapsed-first|plugins/ihr_platform/ihr_access/records.py|            end_grant(lapsed_id, "expired", now=now)|            pass|ihr_access.tests.test_records.GrantedReads.test_a_new_grant_ends_the_lapsed_one_first|
records-row-still-needed|plugins/ihr_platform/ihr_access/records.py|        if not still_needed:|        if True:|ihr_access.tests.test_records.GrantedReads.test_ending_a_grant_keeps_the_row_another_live_grant_needs|
records-link-cap|plugins/ihr_platform/ihr_access/records.py|            duration = min(duration, cap)|            pass|ihr_access.tests.test_records.FileLinks.test_links_under_a_grant_are_short|
records-authorizer-install|plugins/ihr_platform/ihr_access/apps.py|        authz.install_grant_authorizer()|        pass|ihr_access.tests.test_records.GrantedReads.test_a_grant_opens_its_patients_requests_reports_and_prescriptions|records grants do not hold
owner-aggregates|plugins/ihr_platform/ihr_access/totals.py|        "inpatients_now": inpatients.count(),|        "inpatients_now": len(list(inpatients)),|ihr_access.tests.test_totals.OwnerTotals.test_owner_totals_load_no_patient|
my-access-restricted|plugins/ihr_platform/ihr_access/endpoints.py|                "restricted": False if superuser else bool(current.restricted),|                "restricted": False,|ihr_access.tests.test_my_access.MyAccess.test_restricted_is_the_backends_rule|
my-access-bills-facility|plugins/ihr_platform/ihr_access/endpoints.py|                else bool(current.bills_at(facility_pk)),|                else bool(current.held_role_ids & {current.role_ids.get(n) for n in ("Cashier", "TPA Desk")}),|ihr_access.tests.test_my_access.MyAccess.test_bills_here_only_where_they_bill|
my-access-superuser|plugins/ihr_platform/ihr_access/endpoints.py|                if superuser or facility_pk is None|                if facility_pk is None|ihr_access.tests.test_my_access.MyAccess.test_superuser_is_never_in_our_modes|
care-permissions-snapshot|plugins/ihr_platform/ihr_roles/care_permissions.txt|can_apply_tag_config |can_apply_tag_config_unreviewed |ihr_roles.tests.test_grants.CarePermissionsReviewed.test_care_permissions_match_the_reviewed_snapshot|
TABLE
)

row() {
  local line
  line=$(grep -E "^$1\|" <<<"$MUTATIONS") || { echo "unknown mutation: $1" >&2; exit 2; }
  IFS='|' read -r NAME FILE FIND REPLACE TESTS SIGNATURE ENVS <<<"$line"
  [[ -n "$TESTS" ]] || { echo "mutation $1 names no test" >&2; exit 2; }
  local e
  for e in ${ENVS//,/ }; do
    [[ "$e" =~ ^IHR_[A-Z_]+=[a-z]+$ ]] || { echo "mutation $1: bad env $e" >&2; exit 2; }
  done
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
  env)
    row "${2:?name}"
    for e in ${ENVS//,/ }; do echo "$e"; done
    ;;
  *) sed -n '5,11p' "$0"; exit 1 ;;
esac
