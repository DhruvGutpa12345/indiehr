# P1-T11b1 independent code review (2026-11-15)

Two reviewers who did not write the code (Opus 5.5, high effort, two lenses) read the nine roles
and their grants, the serializer guard, the placement trigger, the PatientUser rule, the check
command, the seeded users and the tests. One lens looked at security, the other at correctness and
completeness. Each finding was then challenged by a third agent (Opus 5.5, medium) that tried to
refute it. Of 9 findings: 0 refuted, 3 confirmed in part (C3, C6, S2), 6 confirmed. A fixer
(Opus 5.5, high) fixed them. A verifier (Opus 5.5, medium) checked each fix against its failure
scenario: it closed 7, found 2 only partly closed (S2, C4) and 5 new problems. A second fixer
closed what could be closed.

| # | Finding | Verdict | Fix (verified) |
|---|---|---|---|
| S1 | The PatientUser rule, the trigger and the check command switch off once a person holds any extra role that is neither restricted nor clinical, so a stale Nurse row keeps its force | confirmed, medium | the read-time rule, the patient list, the trigger and `ihr_patient_user_check` all key on "restricted" (a restricted role and no clinical permission), not on "only restricted roles"; recorded as design-b deviation 11; round two: the trigger also ignores soft-deleted (`temp_deleted`) role permissions, as CARE does; mutation rows `access-patient-user-gate`, `-patient-list`, `-patient-user-roles`, `-restricted-patient-user`, `-patient-user-check-gate`, `-placement-temp-deleted`, `-restricted-temp-deleted` |
| S2 | The identifier allow-list does not limit `search`, `search_retrieve` or `update_identifier`, and the deferral was not logged | confirmed in part, low | first round: recorded as deviation 10 and moved to b2 (verifier: only the paperwork was fixed); round two: fixed in b1 by `ihr_access/identifiers.py` (see below); mutation row `access-identifier-write-search` |
| C1 | A change to the placement trigger or the role lists never reaches existing hospitals, and nothing reports it | confirmed, medium | `placement.fingerprint()` and migration `0002_placement_fingerprint` (no operations) carry `PLACEMENT_SHA256`, and a test fails when it differs from this build's guard; `ihr_fleet_run check` reports a placement problem per hospital; `ihr_fleet_run migrate` repairs the guard of a hospital already on this build; mutation rows `access-placement-fingerprint`, `-placement-fleet-repair` |
| C2 | `ensure()` never re-creates an existing trigger when the trigger SQL changes, yet stamps the schema as current | confirmed, low | `ensure()` re-runs `CREATE OR REPLACE TRIGGER` and `ENABLE` on all three tables each time; `problems()` compares the trigger type bits, the `UPDATE OF` column set, and any WHEN clause or arguments; mutation rows `access-placement-trigger-shape`, `-placement-ensure-replaces` |
| C3 | A placement refusal reaches admins and clinicians as an HTTP 500 with no message | confirmed in part, low (the add_user half mostly belongs to b2; nothing leaks) | `ihr_access/exceptions.py` wraps CARE's `emr_exception_handler` (no `apps/` edit): only an IntegrityError with SQLSTATE 23514 and the `ihr_role_placement:` prefix becomes a 400 with the reason; system check E022 checks the wrap; mutation row `access-placement-400` |
| C4 | The Cashier and TPA Desk lose the bed (room rent) on the billing account, and the bill's ward and treating doctor | confirmed, medium | not changed on purpose: it follows Q-K37. Raised with the owner as **Q-K37a**; pinned by a test; see "Left open" |
| C5 | `ihr_patient_user_check` flags legitimate MRD records-access rows as stale and tells the admin to remove them | confirmed, low | a hidden-role row under a live grant is skipped; a row whose grant has ended is listed apart as `grant-ended` and does not make the command exit 1; mutation row `access-check-records-rows` |
| C6 | The "CARE roles unchanged" test is circular, and the new UPSTREAM.md step claims it catches upstream changes | confirmed in part, low (it fails closed, and nothing opens) | the stronger fix: `ihr_roles/care_permissions.txt` is a reviewed snapshot of all 115 CARE permissions (slug, context, class, CARE role names), compared with the live controller by `CarePermissionsReviewed`; UPSTREAM.md wording corrected; mutation row `care-permissions-snapshot` |
| C7 | Tag hiding is never exercised: the test world has no tags | confirmed, low | `build_world()` adds tag configs carrying the canary word on the encounter, the patient (instance and facility tags) and the booking; the Doctor must see them and a restricted role must get `[]`; mutation rows `access-encounter-tags`, `-patient-tags` |

## Second round

The verifier closed S1, C1, C2, C3, C5, C6 and C7, found S2 and C4 only partly closed, and raised
five new problems. The second fixer dealt with them as follows.

- **S2 (fixed in b1, not deferred):** `ihr_access/identifiers.py` wraps CARE's patient `search` and
  `update_identifier` on the class, so b2's swapped subclass inherits them. For a restricted user
  who names an identifier config whose system is not on `fields/identifier_configs.txt`, `search`
  answers 403 and searches nothing (the same answer whether or not the number matches) and
  `update_identifier` answers 400 before the patient is read. A missing or malformed config (a
  phone search) is left to CARE; `search_retrieve` takes no config and goes through the guard.
  Check E022 covers both actions on every subclass. Four tests replace the old deferral pin.
  Deviation 10 is rewritten as built. The general search gate (Q-K40) stays with b2.
- **C4 (unchanged on purpose):** the Cashier and TPA Desk still get no bed history, current bed
  or care team. Fixing it means deciding what a billing role may see, which is the owner's call.
  It is raised as **Q-K37a** (option A keep, option B the two billing roles see the bed, B
  recommended) and pinned by `test_guard.ThroughTheApi.test_billing_roles_see_no_bed_on_the_account_for_now`.
- **New, C1 (no alert):** `ihr_fleet_run check` only printed a placement problem. A broken guard
  (function missing or changed, trigger missing, disabled, pointing elsewhere or on the wrong
  event) now counts as a hospital control in the run-time role check
  (`placement.runtime_problems_by_schema()`, two queries). An app process that starts while a
  hospital's guard is broken does not serve it and writes `ihr-db-role: gated schema=<schema>
  reason=controls`, so the existing alerts fire. An older build's guard that is unchanged as
  installed is not gated. Mutation row `access-placement-gate`.
- **New, C1 (repair order):** the guard repair ran only after the grant repair succeeded. In
  `fleet.py` it now runs first and regardless; a failing grant repair still leaves the guard
  repaired (the hospital ends held).
- **New, C3 (batch handler):** the batch and password views bind the exception handler by name at
  import. `exceptions.install()` rebinds them too, and E022 fails if any other `care.*` module
  holds the unwrapped handler. At this pin both modules already import after the wrap, so this is
  defence in depth. Mutation row `access-placement-batch-handler`.
- **New, S1 (`temp_deleted`):** fixed as in the table. The trigger SQL changed, so the fingerprint
  in `0002_placement_fingerprint.py` was updated in place (the migration is uncommitted).
- **New, housekeeping:** four new files were untracked. They are now staged with the rest
  (`exceptions.py`, `identifiers.py`, `0002_placement_fingerprint.py`, `care_permissions.py` and
  `.txt`). The whitespace hook stripped a trailing space from `care_permissions.txt`, so the
  reader strips it too.

## Final state

- **Fixed:** 8 of 9 findings, S2 fixed in b1 by `identifiers.py`.
- **Left open on purpose:** C4, as owner question **Q-K37a**. The build is fail-closed: the Cashier
  and TPA Desk see no bed until the owner answers. A billing screen in P1-T11c or option B closes it.
- **Known limit (new, design-b.md):** the placement guard is judged only when a process starts, as
  the sealing functions are, so a guard broken at run time is gated only after the app containers
  restart. Nothing gates with `IHR_DB_ROLES=off`. A `DROP FUNCTION ... CASCADE` looks like a schema
  that `ihr_access` has not reached yet, so it is not gated either; `ihr_fleet_run check` still
  names it and `migrate` repairs it.
- **Nothing under `apps/` was edited**, so there is no UPSTREAM_PATCHES.md entry.
- **Dev state:** the dev hospitals were migrated to `ihr_access` 0002 (backend and celery
  restarted, `care-dev.sh migrate-hospitals`, 8 done), and the demo passwords were regenerated into
  the git-ignored `deploy/seed/TEST_USERS.md`.

## Final results

- Plugin suites, `IHR_DB_ROLES=test` (nine labels including `ihr_access`): Ran 827 tests, OK
  (skipped=45); Redis audit: 694 keys written, all tenant-prefixed.
- Plugin suites, roles off: Ran 792 tests, OK (skipped=49); Redis audit: 694 keys written, all
  tenant-prefixed.
- CARE modules in tenant mode (16 modules): Ran 590 tests, OK.
- `scripts/isolation-mutation.sh check`: exit 0, 107 rows, every target and named test found. Each
  new or changed row was applied alone and its named test failed.
- `python3 scripts/check-logs.py`: Log pipeline OK.
- `pre-commit run --all-files`: 43 hooks passed.
- Keycloak live tests: 45 OK (from the build; the seed has not changed since).
