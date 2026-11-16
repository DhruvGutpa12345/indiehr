# P1-T15b2b independent code review (2026-11-16)

An Opus builder (high) built pack forms (design §3.3: `ihr_refdata/forms.py`), the answered-form
guard in the database (§3.4: `ihr_refdata/forms_guard.py`, migration 0004 with no operations that
states the guard's fingerprint, check E026 for the wiring and W026 for the fingerprint), and
design items 1-5, 12 and 15. The restore package, the database-role gate and the fleet runner now
know both guards. The task is security-type (a database guard on patients' answers), so it got the
full review shape. Three reviewers who did not write it (Opus 5.5, high) read it through three
lenses: the guard and its tamper paths (G), the forms engine (E), and tests, operations and docs
(Q). One batched challenger per lens (Opus) tried to refute each finding: 17 findings, 0 refuted,
9 confirmed, 8 confirmed in part (G2, G3, E3, E6, Q2, Q3, Q5, Q7; severities below). An
Opus fixer (high) fixed or documented all 17, each code fix with a regression test that failed
with the fix reverted. An Opus verifier re-ran every suite, applied all 11 mutation rows, reverted
the fixes in two batches (each failure read for its own reason), and made no late fix.

## Hard-delete inventory

The answer-side trigger refuses hard deletes of form answers, so the builder and then the verifier
listed every hard-delete path independently. None that is legitimate breaks:

- CARE's model `.delete()` (`care/utils/models/base.py:30`) and viewset destroy
  (`care/emr/api/viewsets/base.py:250`) are soft deletes.
- `set_organizations` (`care/emr/api/viewsets/questionnaire.py:193`) hard-deletes link rows only;
  `sync_permissions_roles` deletes roles and permissions only.
- No CARE or plugin code hard-deletes a patient, encounter, facility, form, submission or response;
  encounter → facility is PROTECT; a user hard delete only nulls `created_by` / `updated_by`.
- Restore park and expiry, offboarding and test teardowns use DROP SCHEMA (no row triggers).
- Django's flush between CARE's TransactionTestCases TRUNCATEs every table: after G2 the test
  runner turns `ihr.answers_truncate` on for that flush only (`ihr_platform/test_runner.py`).

## Findings

| # | Finding | Severity (reviewer → challenger) | Fix (paths under `plugins/ihr_platform/`) |
|---|---|---|---|
| G1 | The answered check used the transaction's snapshot, so a REPEATABLE READ transaction could edit a form after another committed an answer | medium → medium | frozen-column changes and hard deletes of a form are refused unless the transaction is READ COMMITTED (`forms_guard.py`); `AnswerRace.test_a_repeatable_read_snapshot_cannot_edit_after_an_answer`; mutation `forms-answer-snapshot` |
| G2 | TRUNCATE was not refused on the answer tables | low → low (partly) | a BEFORE TRUNCATE statement trigger refuses it unless `ihr.answers_truncate = on`; wired into the expected list, the gate, `ensure()`, the fingerprint and the restore package; `test_truncate_of_answers_is_refused` (direct and through `TRUNCATE emr_patient CASCADE`); mutation `forms-answer-truncate` |
| G3 | The gate, the fleet check and `ensure()` missed three owner-level tamper methods: a trigger re-pointed to a same-named function in another schema, a re-stamped function comment, both functions dropped with CASCADE | low → low (partly) | functions from another schema are named as a problem; pg_proc body, settings, security and volatility are compared with this build's SQL; a schema whose migrations list `0004_forms_guard` but has no functions is gated; subtests repointed, restamped, dropped; mutation `forms-trigger-elsewhere`. **Limit:** a body re-stamped with an older build's version passes the gate; the fleet check names it and migrate repairs it |
| G4 = Q3 | The restore's tampered-guard refusal compared against the live schema (so a live guard edited by hand refused every good dump), needed every app's migrations equal, and ignored a guard missing from the dump | low → low | when the dump's ihr_refdata migrations are this build's, each guard function's md5 must equal this build's definition (`forms_guard.expected_md5s`); a missing guard counts as tampered; three new `test_restore` cases |
| E1 = Q1, E2 | **Q-K80 A linked v1 to nothing in every provisioned hospital** (it has only the government organisations), so pack forms would be invisible; provisioning makes v1 before any organisation exists | **high → high** (E2 medium) | `forms.missing_link` holds v1 as pending `missing-link` (Q-K68 A) until a non-government top-level organisation exists; re-checked at every apply; blocks accept; shown in the API; `create()` never writes a form with no links; `ProvisionedShapeTests`. The linking rule itself: **Q-K81** |
| E3 | Accepting a same-version item recorded the pack's hash but left the hospital's edited content | low → low (partly) | content that differs at the same form_version gets a new row (links copied, old row retired); `SameVersionTests` |
| E4 | Question ids were not checked as canonical, so CARE's submit could not match them | medium → medium | ids must be lower-case hyphenated UUID v4 or v5; `ShapeTests` (5 id subtests) |
| E5 | A title over 255 characters passed the fleet-wide checks and failed in each hospital | low → low | titles capped at 255; `ShapeTests` |
| E6 | A slug freed by a hard delete could be recorded by a second pack | low → low (partly) | hard-deleting a reserved-slug form or moving its slug out of `ihr-pack-` needs `ihr.pack_write`; `test_a_pack_form_is_never_freed_without_the_setting`; mutation `forms-pack-freed` |
| Q2 | DPDP: a future owner-only SECURITY DEFINER erasure function cannot get past the answer-delete trigger, and patient and visit hard deletes with answers are refused too | medium → low (partly) | documented in the `forms_guard` docstring; the erasure path is **Q-K82** |
| Q4 | No test covered the fleet's repair and check of the answered-form guard | low → low | `FleetTests.test_check_reports_and_migrate_repairs_a_disabled_forms_guard`; mutation `forms-fleet-repair` |
| Q5 | Every column was classified, but not every table pointing at a form, so a new upstream answer table would get past the guard | low → low (partly) | `forms_guard.REFERENCES` classifies all 7 foreign keys to a form or an answer; a test fails on an unclassified one |
| Q6 | CARE's spec check over the committed form packs could not fail when it found no forms | low → low | form items counted apart from the manifest; also run over a test packs folder with one form |
| Q7 | W026 required the newest ihr_refdata migration to restate the fingerprint, and its text misled | low → low (partly) | compared against the newest migration that states one, which the warning names |

## Verification

Verifier (`IHR_DB_ROLES=test`): ihr_refdata, ihr_refdata_registry, ihr_audit and ihr_identity 383
OK (194 Redis keys, all hospital-prefixed); ihr_access 202 OK; isolation 25 OK; test_restore,
test_db_roles and test_release 200 OK; as the owner, CARE's questionnaire, questionnaire_response,
form_submission, observation, diagnostic_report and inventory_item_lock tests 168 OK (the guard
does not break CARE's own form tests, and the flush path works). `pre-commit run --all-files` and
`isolation-mutation.sh check` pass. All 11 mutation rows FAIL their named tests (`forms-guard`,
`-answer-lock`, `-in-place`, `-answer-delete`, `-answer-relink`, `-reserved-slug`,
`-answer-snapshot`, `-answer-truncate`, `-pack-freed`, `-trigger-elsewhere`, `-fleet-repair`).
Every code fix failed its test when reverted. My own re-run before the commit: see TRACKER.

## Decisions

- **Q-K81** (E1/E2): pack forms wait as `missing-link` in a hospital with only government
  organisations, until the linking rule is settled with Q-K76 B.
- **Q-K82** (Q2): the erasure path past the answer guard is left to P4-T1 (DPDP).

Implementation choices kept without a question: W026 is a warning (an error would abort every
mutation run), E026 an error; a same-version write also sets status; `id` and `created_date` are
frozen, `created_by_id` and `updated_by_id` free; an answer is any response or submission row,
soft-deleted ones included; the reserved namespace is checked on `lower(slug)`; refusals carry the
`ihr_forms:` prefix and CARE's viewsets answer them 400.

## Open points

1. No form content ships before Phase 2: only test packs exercise the kind.
2. The dev stack's hospitals hold the guard's first fingerprint: run
   `scripts/care-dev.sh migrate-hospitals`.
3. An edited guard body re-stamped with an older build's version passes the per-request gate (the
   fleet check names it and migrate repairs it). Reaching it needs the database owner's login.
