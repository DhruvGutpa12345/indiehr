# P1-T20a independent code review (2026-11-13)

An independent Opus reviewer who did not write the code looked at correctness, tests, security and
operations of the anonymous-route trim and the batch allow-list, then a second agent tried to
refute each finding. Of 6 findings: 0 refuted (1 confirmed only in part). A fixer agent fixed them,
and a verifier checked each fix against its failure scenario.

| # | Finding | Verdict | Fix (verified) |
|---|---|---|---|
| S1 | Patient-OTP booking routes pass a location resource through untrimmed: the location's current encounter (another patient's name, phone, address; the care team as staff records) and its parent's copy reach an OTP caller, and a cold parent copy makes the GET write | confirmed, high | the booking is built from the model (`PublicTokenBookingSpec`): a location is id, name, description, type, form, status, mode; a healthcare service four fields; any other type `{}`; the outer allow-list applies to every resource type; test with a theatre and parent holding another patient's encounter (exact keys, no row write, copy still cold); mutation rows `public-otp-booking-from-model` and `public-otp-booking-trim` |
| C1 | The doctor list and detail hide a doctor for the whole last day of a schedule (`valid_to` is 00:00 IST of that day, compared with `now()`) | confirmed, medium | compared by IST calendar day (`valid_to__date >= localdate()`, as CARE's slot generator); cases "last day, 15:00 IST" (listed) and "ended yesterday" (hidden) on list and detail; mutation row `public-doctor-last-day` |
| C2 | The audit OMIT change never reaches existing hospitals: no new migration means the fleet treats every schema as current and never re-creates the triggers | confirmed, medium | empty migration `ihr_audit/0005_omit_cached_parent_json` states the trigger fingerprint; a test fails whenever the live fingerprint differs from the latest migration's, so a later trigger change cannot ship without one; mutation row `audit-triggers-ship-with-migration`; applied in all 8 dev schemas |
| C3 | The reviewed DYNAMIC entry (LinkDepartmentsSheet) stays green when the file starts batching the remove routes | confirmed, low | the entry pins a sha256 of the file's code (comments and spacing ignored) and one use of the variable; any edit fails `--check` until re-reviewed; script tests 11 OK |
| C4 | The route swap is verified only by tests: a swap that missed at run time would silently serve CARE's leaking views | confirmed, low | `route_swaps.require()` at the end of `urls_tenant.py` refuses to load the URL configuration, and system check `ihr_platform.E020` fails `manage.py check` and the start-up one-shot; mutation row `route-swap-required` |
| C5 | The facility finder still sends the raw storage key inside `read_cover_image_url`; the doc and test claimed it did not | partly, low | the overclaim corrected in TENANCY_DESIGN §6.6 and §6.8, design.md and GAP_ANALYSIS; the test and mutation row renamed (`public-facility-raw-key-field`); the key stays visible until P1-T20b signs the link |

Design attack: design.md ("After the attack": 10 findings, 0 refuted, none fatal).

## Verifier follow-up

The verifier closed all 6 findings and raised two new problems, both closed by a second fixer:

- **System-check registration untested.** Deleting `checks.register(route_swaps.system_check)` kept
  every test green. New test `RouteSwapRefusalTests.test_django_runs_the_system_check` goes through
  Django's check registry (`run_checks()`): no `ihr_platform.E020` with the real check, exactly one
  with the check patched to fail. New mutation row `route-swap-system-check`.
- **The verifier could not run the Django suites** (the fixer's run held the stack), so closure
  rested on code reading. The second fixer ran both suites one after the other. That run exposed
  one regression from this change: `MigrationNameTests.test_a_real_failing_migration_is_named`
  un-recorded the newest `ihr_audit` migration and expected re-applying it to fail, but migration
  0005 has no operations and succeeds. The test is re-pinned to `0004_owner_writes_the_chain`
  (whose `CREATE FUNCTION` fails when run again), un-recording it and every later `ihr_audit`
  step. It is no weaker: it still needs a real SQLSTATE failure naming that migration.

Final results: app-role mode **Ran 615 OK** (45 skipped); off mode **Ran 582 OK** (48 skipped);
Redis audit 562 keys, all tenant-prefixed; `check-logs.py` Log pipeline OK; `isolation-mutation.sh
check` OK (every row's target and named test found); pre-commit OK.

## Environment note

The CARE labels run in this environment (`test_booking_api`, `test_facility_api`,
`test_organization_api`, `test_user_api`, `test_questionnaire_response_api`) gave Ran 171 with 5
errors, all `UserProfilePictureTestCase`. The throwaway test container cannot resolve the S3 host
`minio`, so these are an environment problem, not this change. No CARE test asserts a trimmed
field. CI runs the full CARE suite.
