# P1-T11b2 independent code review (2026-11-15)

Three reviewers who did not write the code (Opus 5.5, high effort, three lenses) read the ten route
swaps in `ihr_access/views.py`, `edit.py`, `branches.py`, the reviewed field lists and the tests.
The lenses were read leaks (R), write paths (W) and correctness (C). Each finding was then
challenged by another agent (Opus 5.5, medium; high for the high-severity ones) that tried to
refute it. Of 11 findings: 0 refuted, 5 confirmed in part (R1, W1, W4, C2, C3), 6 confirmed. A
fixer (Opus 5.5, high) fixed them. A verifier (Opus 5.5, medium) checked each fix against its
failure scenario: it closed 9 and found C2 and C3 only partly closed, with no new problems. A second
fixer closed C2 and C3.

| # | Finding | Verdict | Fix (verified) |
|---|---|---|---|
| R1 | Patient `update` and `partial_update` are opened to view-only holders (Cashier, TPA Desk, MRD), so CARE validates the body before it answers 403 | confirmed in part, medium to low (the "not unique" oracle for a programme number already exists upstream on patient create; b2 only added a second door) | `IhrPatientViewSet.handle_update` authorizes before CARE reads the body when our branch opened the patient or the user is restricted and cannot edit; test `WritesDecideBeforeTheBody.test_update_is_refused_before_the_body_is_read` (invalid gender and a held vs fresh Nikshay value give the same 403); mutation row `access-update-authorize-first` |
| R2 | The Store Keeper reads patient-linked dispense orders through CARE's dispense-order viewset, which reuses the supply-delivery read permission | confirmed, medium | new `IhrDispenseOrderViewSet` in `route_swaps.SWAPS` (10 swaps now): restricted users get an empty list and 403 on everything else; tests `PatientReturns.test_store_keeper_never_sees_a_dispense_order` and the static `test_every_supply_delivery_route_is_swapped`; mutation row `access-dispense-order` |
| R3 | The encounter `?patient` list and retrieve ignore the unit where the status permission is held, while `?facility` honours it | confirmed, low | the `?patient` list filters with `in_units(organizations_holding(...))`; `_status_retrievable` also needs `branches.holds_in(...)` on the encounter's and bed's organisation caches, as CARE does; test `StatusList.test_a_front_desk_placed_in_one_unit_sees_that_unit_only`; rows `access-encounter-unit-list`, `access-encounter-unit-scope` |
| W1 | Patient create, update and upsert let restricted roles test whether a programme number exists (CARE validates before it authorizes) | confirmed in part, medium (update is new in b2; create and upsert behave the same on b1 and upstream, but were hardened anyway) | update and upsert by id: the R1 fix; create: `clean_create_data` refuses a restricted user without `can_create_patient` with 403 before validation; test `test_create_is_refused_before_the_body_is_read` (POST and upsert, invalid body, held vs fresh value); row `access-create-authorize-first` |
| W2 | A user with two jobs (Receptionist and Nurse) can write or delete programme identifiers through `update_identifier` on the widened branch | confirmed, medium | `IhrPatientViewSet.authorize_update` answers 400 for an off-list config on our edit branch whether or not the user is restricted (`identifiers.off_list()`, shared with the edit rule); test `EditingDemographics.test_two_jobs_keep_to_the_identifier_list`; row `access-update-identifier-branch` |
| W3 | The district-move rule compares people, not what they can do, and skips patients with no district | confirmed, low | `edit._privileged_grants` compares (user, slug) pairs; a patient with no district is placed by PIN only and still gets the "gained" check; tests `test_a_district_move_compares_what_people_can_do` and `test_a_patient_with_no_district_moves_on_the_same_terms`; rows `access-geo-move-slugs`, `access-geo-move-no-district` |
| W4 | The Receptionist can set tags, extensions and programme identifiers at patient create | confirmed in part, low (tags part refuted: CARE's `can_apply_tag_config` already refuses them; extensions were a real gap not recorded) | `clean_create_data` drops off-list identifiers, `extensions` and `meta` unread; the tag refusal is pinned; test `test_registration_keeps_to_the_identifier_list` (POST and upsert); row `access-create-identifiers` |
| W5 | The Store Keeper can create a patient-linked delivery order, or a delivery against one, and the answer shows the patient | confirmed, low | `_NoPatientRows.clean_create_data` runs before CARE validates: a delivery order with a `patient` gets 403, a delivery against a patient-linked order gets 404; test `test_store_keeper_never_sees_a_patient_return` extended with both creates; rows `access-delivery-create-patient`, `access-delivery-create-order` |
| C1 | The web app's patient request always sends `?facility=`, and CARE answers 403 "Cannot view facility tags" for Cashier, TPA Desk and MRD | confirmed, medium | option (a): on our branch `get_serializer_retrieve_context` only checks that the facility exists (404 if not) and returns `facility: None`; no tag permission was granted; test `OpeningAPatient.test_the_web_apps_patient_request_with_the_facility`; row `access-patient-facility-context` |
| C2 | The swaps copy CARE code and nothing detects drift (create-body field list, retrieve, handle_update, get_queryset) | confirmed in part, medium (the `handle_update` and `_rows` copies hand back to CARE whenever CARE's right holds, so the risk was lower there) | `fields/encounter_create.txt` (allow, clinical, hidden) replaces the fixed deny-list and check E024 runs `create_problems()`; `care_copies.txt` pins the sha256 of five CARE methods (first fixer two, second fixer added `EncounterViewSet.get_queryset`, `EncounterAccess.get_filtered_encounters`, `EncounterAccess.can_view_encounter_obj`); `_rows` now starts from CARE's own base queryset; tests `CareCopies.test_copied_care_methods_are_unchanged`, `test_our_rows_are_cares_rows`, `test_the_create_list_classifies_cares_fields`; no mutation row (a drift check) |
| C3 | Repeated authorization queries for CARE's own roles | confirmed in part, low (the search-gate claim was overstated: the per-request state already holds it) | the encounter `?patient` branch checks our slug first; `_edits_demographics` is computed once per request; second fixer found all 5 extra statements on a Doctor's save were CARE's `can_write_patient_obj` run twice, added `RequestState.roles_granting` and `branches.may_hold`; budgets are now 1 extra statement for the encounter history and for a patient save; test `QueryBudget.test_a_doctors_encounter_history_and_patient_save`; no mutation row (`may_hold` only saves queries and controls no access) |

## Second round

The verifier closed R1, R2, R3, W1, W2, W3, W4 and C1, each by applying the fix's mutation by hand on
a scratch copy of `plugins/` and watching the named test FAIL. It found C2 and C3 only partly
closed and raised no new problems.

- **C2:** the copies of `EncounterViewSet.get_queryset` (the `select_related` list and the
  `?patient` / `?facility` hand-back) and of the two encounter authorization methods were not
  pinned. The second fixer pinned them (five digests in all) and made `_rows` build on CARE's own
  base queryset, with a test that compares our query with CARE's.
- **C3:** the Doctor's patient save still added 5 statements. All were CARE's own check run a
  second time. They are now 1 for the save and 1 for the encounter history. One statement (the roles
  that grant our three slugs) remains for CARE's roles; see "Known limits".

## Final state

- **Fixed:** 11 of 11 findings, all closed after the second fixer (C2 and C3 closed in the second
  pass).
- **No owner decision was needed** and no new Q-K was raised. Q-K37a stays open, option A.
- **Nothing under `apps/` was edited**, so there is no UPSTREAM_PATCHES.md entry. No migration and
  no new table. `batch_allowlist.txt` is unchanged.
- **Dev data left:** one synthetic patient and one visit in Alpha (from the by-hand check).

## Known limits (full text in design-b.md "P1-T11b2 as built")

- The Receptionist can still set the blood group (registration records it) and a date of death at
  patient create; the edit rule refuses changing the date of death later. Both are hidden from them
  afterwards.
- CARE's own validate-before-authorize order remains for CARE's roles (and for a clinician who also
  holds a front-desk role) on patient create and upsert. We decide first only for restricted users
  and on our branch.
- One extra query per request for CARE's roles on the patient and encounter swaps (the roles that
  grant our three slugs). It is not cached across requests (TENANCY_DESIGN §6).
- Digest pins catch only a change to the pinned methods' own source. A change elsewhere in CARE that
  alters what they mean is caught only if it changes their source.
- CARE gap found on the way (for P1-T11d with Q-K40): the delivery-order list without `origin` or
  `destination` returns every order of the hospital to any signed-in user.
- The front desk's patient list stays empty; patients are found by search (as designed).
- Q-K37a is still option A: the Cashier and TPA Desk see no bed on the billing account.

## Final results

- Plugin suites, `IHR_DB_ROLES=test`: Ran 874 tests, OK (skipped=45); Redis audit: 764 keys written,
  all tenant-prefixed.
- Plugin suites, roles off: Ran 839 tests, OK (skipped=49); Redis audit: 764 keys written, all
  tenant-prefixed.
- CARE modules in tenant mode (30 modules): Ran 1056 tests, FAILED (failures=1, errors=4). All five
  are `test_file_upload_api` storage tests: the test container cannot resolve the `minio` host. The
  same 5 fail on the committed b1 code. The other 1051 pass.
- `scripts/isolation-mutation.sh check`: exit 0, 132 rows (25 new in b2). Each new row was applied
  alone and its named test FAILED (an assertion, not an error).
- `python3 scripts/check-logs.py`: Log pipeline OK.
- `scripts/batch-allowlist.py --check`: 37 routes, list current.
- `pre-commit run --all-files`: 43 hooks passed.
