# P1-T25 build: hospital-root admin gate (Q-K76 B)

Builder: Opus, high (access boundary), 2026-11-22, unattended run. Built to `attack.md` "Decision
(orchestrator, 2026-11-22)", which wins over `design.md`. Nothing committed.

## R5 first (decision item 10)

`ihr_identity.tests.test_root.AddStaffToTheRoot.test_r5_a_root_admin_adds_staff_to_the_root_up_to_administrator`
was written and run before any other code. A first admin who is Administrator on a marked root only
(not a superuser) creates a user on the root through `POST /api/v1/users/` with `role_orgs`
(Administrator: 200, placed on the root, still inactive and invited). The run showed one finding:
"Staff" is refused (403) as well as "Admin". CARE's `check_role_subset` allows only roles whose
permissions are a subset of the adder's own, and Staff holds clinical rights that Administrator lacks.
So "up to Administrator" means Administrator, or a role that is a subset of it. That is not a failure
of R5: clinical staff go into the facility's own organisations, as before. The test pins both refusals.
Result: `Ran 1 test … OK`.

## Files

New:
- `plugins/ihr_platform/ihr_identity/roots.py`
- `plugins/ihr_platform/ihr_identity/management/commands/ihr_hospital_root.py`
- `plugins/ihr_platform/ihr_identity/tests/test_root.py`
- `deploy/logs/alerts/root.yml`

Changed (code):
- `ihr_identity/staff_access.py`, `ihr_identity/testing.py` (`make_root_admin`)
- `ihr_platform/invitations.py`, `ihr_platform/provisioning/steps.py`, `ihr_platform/fleet.py`
- `ihr_platform/restore/engine.py`, `ihr_platform/seeding.py`
- the commands `ihr_create_tenant`, `ihr_fleet_run` (help text) and `ihr_keycloak_demo_users`

Changed (tests): `test_my_access`, `test_seed_admin`, `test_viewer`, `test_invitations`,
`test_staff_offers`, `ihr_refdata` `test_api`, `test_forms`, `test_facility`, `test_provisioning`,
`test_release`, `test_restore`, the isolation `world.py` and `test_isolation.py`, and
`apps/frontend/tests-ihr/roles-menus.spec.ts` (ours).

Changed (ops and docs):
- `scripts/isolation-mutation.sh` (5 rows), `scripts/staging.sh` (`hospital-root`, the step list),
  `scripts/care-dev.sh`
- `deploy/staging/compose.yaml`, `deploy/dev/care-local.override.yaml` (start-up
  `check roles root packs`)
- `scripts/test_staging_logs.py`, `scripts/check-logs.py` (the key label),
  `deploy/logs/tests/alert-cases.json`
- the strings in `scripts/test_{dumps,release_scripts,staging_db_roles,staging_monitoring}.py`
- `deploy/grafana/dashboards/ihr-server.json` (regenerated: the log-rule count is now 26)
- `deploy/staging/README.md` (new section "Hospital root"), `docs/LOCAL_DEV.md`,
  `docs/QUESTIONS.md`, `CLAUDE.md`

## Decision items

1. **The root is a marked organisation; there is no table, field or trigger.**
   - `roots.py:62` `marked()` includes deleted rows.
   - `roots.py:67` `is_valid`: not deleted, active, parentless, `org_type == "role"`, no
     `managing_organizations`, and the marker set.
   - `roots.py:79` `valid_root()`: exactly one marked organisation, and it is valid; otherwise None.
     One query, no cache.
   - Protection comes from CARE's rules, pinned by `CareRoleOrganisationRules` (4 tests).
2. **The gate.**
   - `staff_access.py:72` `hospital_wide_admin` passes only with a valid root and an admin
     membership on it.
   - `:84` `top_level_admin` keeps the old rule, unchanged.
   - Targets use it: `:180` (`reset_allowed`), `:259` (`_reset_sub`, keeping the `admin-target`
     code) and `invitations.py:576/601` (`_admin_anywhere`). The asker's side is unchanged
     (`_asker_may_reset` still uses the gate).
3. **`ensure_root`** (`roots.py:182`).
   - Creates the root only when no organisation is marked. Never adopts one by name. If the marked
     one is not valid, returns `(None, "invalid")`.
   - A transaction advisory lock per schema (`:163`). Its callers hold `runner.exclusive`.
   - Audited as `root:<operator>` (`:173`).
4. **Provisioning.**
   - `steps.py:297-305` `first_admin` calls `ensure_root` and `place`; there is no state row any
     more, and an invalid root raises StepError.
   - `steps.py:388-393` `activate` refuses without a valid root that has an admin.
5. **The fleet step `root`.**
   - `fleet.py:87` `STEPS`; `:91` `ROOT_STATUSES` is ACTIVE and SUSPENDED.
   - `:1185` `_root` and `:1205` `_root_one` run under `runner.exclusive` with retries, write their
     own step record and never hold. `:549` `finished()` ignores `root`.
   - The per-hospital logic is `roots.converge` (`:294`): it uses `onboarding_admin` (`:275`), places
     only an `entitled` user (`:262`; exists, not deleted, active or awaiting setup, passes
     `top_level_admin`), then `remove_state` (`:246`, that user's Administrator rows on top-level
     govt organisations only).
   - `state-admins n=<k>` is logged for the rest.
   - The log line is built at `:136`, and the alert is `root.yml` `HospitalRootProblem`.
   - Wired into the start-up (`compose.yaml`, the dev override), releases (`staging.sh`) and dev
     `migrate-hospitals`.
6. **`ihr_hospital_root --slug S show|place|remove-state`**.
   - `place` and `remove-state` need `--operator` and `--reason`, run under the hospital lock, and
     carry the reason in the audit context.
   - Staging wrapper: `staging.sh hospital-root`, the admin (owner) one-off.
7. **The restore.** `engine.py:1154-1172` `_root()` runs `converge` after migrate, roles and packs,
   writes `root` into the result and says `<slug>: root <outcome>`. `--keep-restored-access` is
   unchanged.
8. **Forms:** no change. `test_forms` now places its pack-deciding admin on the root.
9. **Seed.**
   - `ihr_create_tenant.py:65` creates an empty root.
   - `seeding.py:175` `make_hospital_admin` places the user on the root and drops the fixture branch.
   - Greeting: `ihr_keycloak_demo_users.py:64` `_link(…, first, last)` sets the `DEMO_STAFF` name.
10. **R5:** see above.
11. **Tests and mutations:** below.
12. **QUESTIONS.md:** Q-K126…Q-K131 added (auto-accepted, awaiting owner confirmation). Q-K76 is
    marked "Built", and Q-K120 is marked "Superseded".

## Tests

Command: `IHR_DB_ROLES=test scripts/care-tenancy.sh test --settings=ihr_platform.settings.test --noinput <labels>`.

- **R5 alone:** 1 test, OK.
- **`ihr_identity.tests.test_root`:** 21 tests, OK. New methods:
  - `test_r5_a_root_admin_adds_staff_to_the_root_up_to_administrator`
  - Gate: `test_admin_roles_on_the_root_pass`, `test_the_same_roles_elsewhere_are_refused`,
    `test_a_removed_membership_or_a_role_without_can_create_user_is_refused`,
    `test_nobody_passes_without_a_valid_root`,
    `test_a_service_account_or_a_superuser_alone_is_refused`, `test_the_audit_route_follows_the_root`
  - CARE's rules: `test_it_cannot_be_updated_deactivated_or_retyped`, `test_it_cannot_be_deleted`,
    `test_it_can_have_no_child`, `test_a_managing_organisation_closes_the_screens_never_widens_them`
  - `ensure_root` and `place`: `test_one_root_and_never_a_second`, `test_it_never_adopts_by_name`,
    `test_an_invalid_root_is_left_alone`, `test_place_refuses_superusers_and_service_accounts`,
    `test_the_line_never_carries_an_address`
  - `converge`: `test_the_onboarding_admin_is_moved_onto_the_root`,
    `test_an_admin_no_longer_entitled_is_not_placed`, `test_awaiting_setup_counts_as_entitled`,
    `test_no_onboarding_record_or_an_invalid_root`
  - The command: `test_show_place_and_remove_state`
- **New elsewhere:**
  - `test_my_access`: `test_a_state_only_administrator_is_not_a_hospital_admin` (flips the Q-K120
    pin) and `test_nobody_is_a_hospital_admin_without_a_valid_root`. The provisioned-admin test is
    rewritten for the root. The query count is re-pinned 13/11 → **14/12** (one SELECT of the root).
  - `ihr_refdata test_api`: `test_a_state_or_other_top_level_admin_does_not_decide` (replaces the
    Q-K120 test). `test_facility` re-pinned 24/22 → **26/24** (that count includes the
    `SET search_path` statements, measured).
  - `test_seed_admin`: `test_a_fixture_top_level_role_is_no_longer_the_way_in`,
    `test_refuses_when_the_root_is_invalid` and `DemoGreeting.test_the_demo_login_names_its_care_user`.
  - `test_staff_offers`: `test_a_state_only_administrator_here_stays_protected` and
    `ProtectedElsewhere.test_a_state_administrator_elsewhere_stays_protected`.
  - `test_provisioning`: `test_activate_refuses_a_hospital_without_a_root_admin`, plus root
    assertions in the main story.
  - `test_release` (fleet): `test_the_root_step_makes_a_root_never_replaces_one_and_never_holds` and
    `test_the_root_step_skips_provisioning_hospitals`.
  - `test_restore`: `test_a_dump_from_before_the_root_comes_back_with_one`.
  - Isolation: `test_roots_grant_nothing_across_tenants`.
  - `test_staging_logs`: `test_root_line_is_ours_alone`.
- **Focused run** (identity, my_access, seed_admin, records, refdata api, viewer, provisioning,
  release): `Ran 342 tests in 493.607s OK`.
- **Full run** (the required labels plus `ihr_tenant_settings` and `test_console`, the isolation
  suite and the matrix included): **`Ran 1128 tests in 1239.690s OK`** on a fresh test database (Redis audit: 926 keys, all tenant-prefixed). The first full run (with `--keepdb`) had 16 failures and 1 error, all fixed or explained:
  - the forms admin needed the root;
  - the facility count needed re-pinning;
  - the isolation crawl saw my new test's probe of the other root id in the audit trail (the test was renamed to run after the crawl);
  - the role matrix's organisation-users route was filled by `test_viewer`'s leftover memberships from the previous `--keepdb` run (an older leak, see Known limits).
- **Script tests (`python3 -m unittest`):**

  | Suite | Tests | Result |
  |---|---|---|
  | `test_staging_logs` | 50 | OK |
  | `test_staging_db_roles` | 26 | OK |
  | `test_staging_monitoring` | 56 | OK |
  | `test_release_scripts` | 104 | OK |
  | `test_dumps` | 91 | OK (7 skipped) |
  | `test_staging_dashboards` | 28 | OK |

- `scripts/check-logs.py`: "Log pipeline OK" (Docker).
- `scripts/isolation-mutation.sh check`: exit 0.
- `pre-commit run --all-files`: exit 0, with the new files staged.

## Mutation results

Each row was applied to the working tree, its named tests were run, and the file was restored
byte-for-byte (sha256 checked). "expect" means every `expect` regex matched.

| Row | apply | test | expect |
|---|---|---|---|
| root-gate (the gate becomes `top_level_admin`) | mutated | FAILED (failures=14) | 1 |
| root-unset-closed (no root falls back to the old rule) | mutated | FAILED (failures=5) | 1 |
| root-valid (the active/deleted/parent/type/managing checks dropped) | mutated | FAILED (failures=6) | 1 |
| root-fleet-checked (`entitled` becomes `user is not None`) | mutated | FAILED (failures=3, errors=1) | 1 |
| root-target-wide (`_admin_anywhere` uses the gate) | mutated | FAILED (failures=2) | 1 |

- The error in `root-fleet-checked` is the deleted-user subtest: `place` refuses that user itself
  (defence in depth). The other subtests FAIL as expected.
- `root-gate` shows that each new negative test fails under the old rule.

## Live proof (dev stack)

1. `docker restart indiehr-care-backend-1`.
2. `scripts/care-dev.sh migrate-hospitals` ran fleet run f5740c92, "migrate, check, roles, root,
   packs for 8 hospital(s)", which finished with 8 done.
   - alpha and beta: `ensure outcome=done`, then `place outcome=no-admin` (no onboarding record).
   - epsilon, gamma, iota and theta (provisioned): `place outcome=done` and
     `remove-state outcome=done n=1`.
   - eta and zeta: `no-admin` plus `state-admins n=1` (their first admins are not entitled).
3. The seed's admin step (`ihr_seed_demo._hospital_admin`/`_facility_admin` under each schema)
   returned "alpha hospital_admin added: True" and "beta … True".
4. `scripts/care-dev.sh keycloak seed` exited 0. Its output was not printed (it holds passwords).
5. Gate answers (`manage.py shell`):
   - `alpha care-admin hospital_wide= True top_level= True greeting= Priya linked= True` (admin.priya)
   - `beta care-fac-admin hospital_wide= False top_level= True greeting= Bhavesh linked= True`
     (admin.bhavesh)
   - `alpha care-facility-admin hospital_wide= False` (admin.anil)
6. `ihr_hospital_root --slug alpha show`:
   ```
   alpha: root 49c11829-3a59-4542-adff-44de04d2a51e 'Hospital administration'
     admins on the root (1): care-admin
     admin roles on other top-level organisations (5; not hospital-wide since P1-T25):
       care-admin: Administrator on 'Administrator' (role)
       care-fac-admin: Facility Admin on 'Facility Admin' (role)
       admin: Admin on 'Health Department' (role)
       care-admin: Admin on 'Health Department' (role)
       care-role-admin: Admin on 'Health Department' (role)
   ```

## roles-menus.spec.ts assertions changed

- `admin.bhavesh` (beta): menu `[...CARE_FULL, AUDIT, PACKS]` → `CARE_FULL`.
- `HOSPITAL_ADMINS`: `{"admin.bhavesh", "admin.priya"}` → `{"admin.priya"}`. Bhavesh's typed
  `/audit` and `/packs` addresses are now checked as "not part of your role", with no admin API calls.
- The comment says why (Q-K118's intent). Not run locally (no frontend build on this machine). CI's
  Playwright job runs it.

## Deviations

- R5: "up to Administrator" holds for Administrator and roles that are a subset of it. Staff is
  refused (CARE's subset rule). This is pinned and noted in Q-K127.
- Root lines are written to stderr with `os.write(2)`, as `ihr-records` lines are. The fleet's own
  `err` callback does not see them (tests patch `roots._write`).
- No ADR-009/ADR-010 addenda or TENANCY_DESIGN edit. The decision list did not name them. CLAUDE.md,
  the staging README and QUESTIONS were updated.
- The isolation test is named `test_roots_…`, so it runs after the crawl. Its probe of B's root id is
  itself recorded in A's audit trail, and the crawl would report that as a leak if the probe ran first.
- The facility query re-pin (+2) was measured, not assumed: the root's SELECT plus its
  `SET search_path`.

## Known limits

- A superuser can unmark or edit the root (trusted, Q-K131). A root admin can remove every root admin
  (`place` recovers them).
- In dev, eta and zeta now have no hospital-wide admin (`no-admin`; their first admins are not
  entitled). Fix them by hand with `place` if they are needed.
- Admin rows left on state organisations are reported (`state-admins`) and never removed
  automatically.
- `ihr-root` lines from `ihr_hospital_root` run in the unlogged admin one-off. Only the fleet and
  restore lines reach the alert.
- `test_viewer` leaves `aud-*-team`, `-off` and `-d` memberships in the test schema (this was already
  so before this task). With `--keepdb` they fill the matrix's organisation-users route on a later
  run. A fresh database is clean.

Graphify calls: 14 (12 answered, 2 run from the wrong folder). Grep was used for exact text after the
graph named the files.

## Review

Fixes by the Opus fixer for the security-boundary findings (2026-11-22). Each regression test was
run red before its fix (6 red: 4 FAIL, 2 ERROR on the first run). Each fix also has a mutation row
in `scripts/isolation-mutation.sh` that reverts it, and its test goes red again under that row (see
"Mutation runs" below).

| id | finding | reviewer → challenger → recheck | fix | test |
|---|---|---|---|---|
| T1 | An operation past `first_admin` before P1-T25 but not yet live is stuck. `runner.run` skips the succeeded `first_admin`, `activate` raises without a valid root with an admin, the fleet step skips PROVISIONING, and `ihr_hospital_root` cannot ensure a root. | high → medium → medium (fails closed; needs an operation in flight across the upgrade) | `steps._root_for(op, user=None)`: ensure the root, then place the operation's `admin_user_id` and `remove_state` for that person in one transaction. `first_admin` calls it. `activate` calls it when the root is missing or has no admin who can act, then checks again. Recovery for such an operation is to resume it. | `test_provisioning…test_an_operation_past_first_admin_before_the_root_goes_live_with_one` deletes the root, restores the state Administrator row and resumes. It expects ACTIVE, the admin as the root's only admin, and no state row. Row `root-activate-heals`. |
| A1 | `marked()` counted any organisation with the marker. A team "Admin" can make or edit a team organisation with `metadata={"ihr":"hospital-root"}` through CARE's API, which makes two marked, `valid_root()` None and switches off every hospital-wide screen. | medium → low-medium → medium | `marked()` adds `system_generated=True`; `is_valid()` checks it too. CARE's write specs have no such field, so only owner code (`ensure_root`) makes a root. | `test_root.MarkerFromTheApi…made_or_edited_through_the_api_is_ignored`: a team Admin POSTs a marked child team and PUTs the marker onto its team (both 200, `system_generated` False). The root, `marked()`, the gate and `ensure_root` are unchanged. `Gate.test_nobody_passes_without_a_valid_root` gains "not system-made", and its second marked organisation is now made `system_generated=True`, since a non-system one no longer counts. Row `root-system-made`. |
| T2 | `converge` ran `place` and `remove_state` in separate transactions. A crash between them leaves the admin on the root still holding the state row, and no later run removes it. | medium → low-medium → medium | `place` and `remove_state` run in one `transaction.atomic()` in `converge`, and likewise in `_root_for`. | `test_root.Converge.test_placing_and_dropping_the_state_row_are_one_change`: `remove_state` raises. Nobody is left on the root, and the next run places the admin and removes the row. Row `root-converge-one-change`. |
| A3 | `admins(root)` ignores `is_active`. A root whose only admin is deactivated counted as `exists` in `converge` and passed `activate`, with no `no-admin` line. | low → low → low | `roots.live_admins(root, pending=None)` counts admins who are active or awaiting setup, plus `pending` (at `activate`, the operation's own first admin, who is not yet invited). `converge`'s has-admin test and `activate` use it. The gate is unchanged, since a deactivated user cannot sign in. | `test_root.Converge.test_a_root_whose_only_admin_is_deactivated_has_no_admin` expects `no-admin` and the line; awaiting setup counts. `test_provisioning…test_activate_refuses_a_hospital_without_a_root_admin` is reworked: the root's only admin is deactivated and the operation's admin is deleted, so activate refuses. After un-deleting, a resume goes live with the admin placed (this also covers T1's heal). Rows `root-live-admins` and `root-activate-live`. |

`test_activate_refuses_a_hospital_without_a_root_admin` changed, with this reason. It used to delete
the root's admin membership and expect a refusal. Since T1, `activate` restores the operation's own
admin, so the refusal case now needs an admin who cannot be placed (a deleted account) and a root
whose only admin is deactivated (A3).

### Mutation runs (review fixes)

These runs applied each row to the working tree, ran its named tests (`IHR_DB_ROLES=test`), then
put the file back from a copy and checked it with `md5sum -c`.

| row | result | why it fails |
|---|---|---|
| root-system-made (A1) | FAILED (failures=1) | `valid_root()` None != root: the API-made organisation counts as a second root |
| root-live-admins (A3) | FAILED (failures=1) | `('exists', None) != ('no-admin', None)` |
| root-converge-one-change (T2) | FAILED (failures=1) | the admin is left on the root after `remove_state` fails |
| root-activate-live (A3) | FAILED (failures=1) | `StepError not raised`: a deactivated admin passes `activate` |
| root-activate-heals (T1) | FAILED (errors=1) | `StepError: the hospital has no valid root with an admin` (the stuck operation) |
| root-valid (re-run) | FAILED (failures=6) | unchanged after the `is_valid` edit |
| root-fleet-checked (re-run) | FAILED (failures=3, errors=1) | unchanged after the `converge` edit |
| root-unset-closed (re-run) | FAILED (failures=6) | still red after the `test_my_access` edit below |
| root-gate (re-run) | FAILED (failures=14) | unchanged |

`ihr_access.tests.test_my_access.MyAccess.test_nobody_is_a_hospital_admin_without_a_valid_root`
changed for this reason: its second marked organisation is now made `system_generated=True`
(A1), because a non-system marked organisation no longer counts.

Suites after the fixes (`IHR_DB_ROLES=test`, tenant mode):
- `ihr_identity ihr_access ihr_audit ihr_refdata ihr_roles` plus `test_provisioning`,
  `test_release` (fleet), `test_restore`, `test_db_roles`, `test_console` and the isolation suite:
  1086 tests. The only failure was the my-access test above, which was then fixed. A re-run of
  `test_my_access` and `test_root` passed 38 of 38.
- The role matrix is in `ihr_access`.
- `scripts/test_staging_logs.py`: 50 passed.
- `isolation-mutation.sh check` passed with 10 root rows.
- `pre-commit run --all-files` passed.

### Review: Sonnet fixer (medium and low findings, 2026-11-22)

Built on the Opus fixer's tree. Each test below was written first and run red, or its fix was
reverted and shown red (column "red before / with the fix reverted"). Nothing was committed.

| id | finding | reviewer → challenger (no recheck was run for these) | fix | test | red before / with the fix reverted |
|---|---|---|---|---|---|
| T3 | `_root_one`'s `except` and the deferred branch wrote no `ihr-root` line; `finished()` ignores `root` and the hospital stays served, so a root never made or never checked was unalerted. | medium → medium | `roots.say("ensure", "failed", …)` in both branches (`fleet.py`; `_root_operator()` shared); `failed` added to `roots.OUTCOMES`, the `root.yml` regex, `ROOT_LINE` in `scripts/test_staging_logs.py` and one `alert-cases.json` case (`eta`). | `test_release.FleetTests.test_a_failed_or_deferred_root_step_writes_its_line_and_never_holds` (a raising `converge`, then a lock held on every try; the hospital stays served). `test_staging_logs.test_root_line_is_ours_alone` takes the `failed` line. Rows `root-failed-line`, `root-deferred-line`. | FAIL before the fix (`…outcome=failed… not found in []`); each row FAILs (failures=1). |
| Q9 | Duplicate of T3. | medium → low-medium | As T3, with `outcome=failed` (not `invalid`, which would mislabel it). | As T3. | As T3. |
| Q1 | No test pins that an admin outside the root cannot add anyone to it; the gate relies on CARE's `can_manage_organization_users_obj` staying strict. | medium → medium | Test only: `test_root.AnAdminElsewhereCannotJoinTheRoot.test_an_admin_elsewhere_cannot_add_anyone_to_the_root`: a state Administrator and the world's Facility Admin each try `POST /api/v1/users/` with `role_orgs=[root, role]` and `POST /api/v1/organization/<root>/users/`, for Administrator and Doctor; 403 each, no new root membership, nobody hospital-wide. | as named | Passes today. With `can_manage_organization_users_obj` made to answer True (a patched `AuthorizationController.call`, proof only, not kept): FAIL, `200 != 403`, 5 failures. (No row: the control is in `apps/`.) |
| A2 | `HospitalRootProblem` reads only fleet, release and restore lines, so a root made invalid at run time stays silent until the next of them. | low → low | **Recorded as a known limit, not built:** after A1 only a superuser can make a root invalid at run time (trusted, Q-K131), and a beat check would add a per-hospital job, a service to the alert and a false-alert path for a root not yet made by the fleet step. `ihr_hospital_root show` checks it at any time. Written in `deploy/staging/README.md` ("Hospital root") and the `root.yml` header. | none | none (a known limit) |
| A4 | Only `_admin_anywhere` had a target-side mutation row; `reset_allowed` and `_reset_sub` were test-covered but not mutation-pinned. | low → low | Rows `root-reset-offer-target` (`not top_level_admin(target)` → `not hospital_wide_admin(target)`) and `root-reset-sub-target` (`top_level_admin(user)` → `hospital_wide_admin(user)`), both expecting `TargetRule.test_a_state_only_administrator_here_stays_protected`. | existing test, now pinned | Both rows FAIL (failures=1). |
| T4 | `ihr_hospital_root` accepted operators like `fleet-startup`, `restore-x`, `provisioning-12`, so a manual write's audit actor `root:<operator>` could pose as an automatic source. | low → low (nit) | `_operator()` refuses the prefixes `fleet-`, `restore-`, `provisioning-` and the names `demo-seed`, `ihr_create_tenant`, `test`, `startup` (case-insensitive). `check_operator` of the packs command was not reused: it blocks none of these. | `OperatorCommand.test_an_operator_cannot_pose_as_an_automatic_source` (7 names x place and remove-state; the person is never placed). Row `root-operator-not-automatic`. | FAIL before the fix (15 failures); the row FAILs (failures=15). |
| T5 | `staging.sh hospital-root` runs in the unlogged admin one-off, so operator `place`/`remove-state` lines never reach the log store, while the README claimed every root write is logged. | low → low | README (and the `root.yml` header, the `roots.py` docstring) corrected: operator writes are recorded in the audit trail (`root:<operator>` with the reason) only. | none (documentation) | none |
| T6 | `TRACKER.md` still showed P1-T25 as not started, with "a hospital-profile field" wording and a stale header. | low → nit | Row now 🟡 with the marked-organisation wording, Q-K126…Q-K131 and a link to `docs/reviews/p1-t25/`; "Current phase" and "Next task" updated. | none | none |
| T7 | build.md and the README "Existing hospitals" omitted provisioning operations in flight across the upgrade and the old unmarked "Hospital administration" organisation on dev hospitals. | low → low | README "Hospital root" gets two bullets: an in-flight operation is resumed (T1's `activate` heal), and the old unmarked organisation stays beside the marked root, is never adopted by name and shows in `show`. See "Known limits" below. | none | none |
| Q2 | `TRACKER.md` sent the owner to `admin.bhavesh` (a facility admin, 403 on audit and packs) for the audit viewer and the MRD audit step. | medium → low-medium | Both rows use `admin.priya` at Alpha. | none | none |
| Q3 | `test_roots_grant_nothing_across_tenants` relied on sorting after the crawl test; its HTTP probe of B's root id is itself recorded in A's audit trail and would show up as a crawl leak. | low-medium → low | The HTTP probe moved to the end of the crawl test (`test_crawl_never_shows_the_other_tenants_data`, after its three crawls); the other test keeps the schema-level checks only. | the isolation pair run in reverse order (`--reverse`) | The old arrangement run in reverse: the crawl FAILs for both viewers (`/api/ihr/audit/entries/ -> [<B's root id>]`); the new arrangement passes in reverse (2 tests OK). |
| Q4 | `test_a_dump_from_before_the_root_comes_back_with_one` did not assert that the restore created the root. | low → nit | `assertIs(migrate["root"]["created"], True)`, and `marked_before_dump == 0` read in `setUpClass` before the dump. | as named | With a root made before the dump (proof only, not kept): FAIL, `1 != 0`. |
| Q5 | `DemoGreeting` subclassed `SeedHospitalAdmin` (six tests run twice) and called `_link` directly, so `handle()` dropping the names failed nothing. | low → low | `DemoGreeting(SwapCase)`; a new test runs `handle()` with Keycloak mocked and asserts each `_link` call carries first and last names (priya: `Priya`, `Deshpande`) and that every `DEMO_STAFF` link was made. | `test_seed_admin.DemoGreeting.test_handle_passes_each_logins_names_to_link` | With `handle()` calling `_link(hospital, user, sub)` (proof only): ERROR. Passes with the fix. |
| Q6 | The `Gate` docstring overclaimed ("every refusal passed under the old rule") and the `root-gate` row omitted two tests. | low → low | Docstring says which cases the old rule let in and which it refused too. `root-gate` now also names `Gate.test_the_audit_route_follows_the_root` and `test_seed_admin.SeedHospitalAdmin.test_a_fixture_top_level_role_is_no_longer_the_way_in`. | as named | Under `root-gate` all five tests FAIL (`…audit_route…` 1, `…fixture_top_level…` 1, `…same_roles_elsewhere…` 9, `my_access` 1, `GateTests` 4). |
| Q7 | `root-fleet-checked` replaced all of `entitled()`; no mutation on `staff_access.py:180`/`:259`. | low → nit | Part 1: the row now drops only `top_level_admin` (`return user.is_active or waiting`). Part 2: rows as A4. | `Converge.test_an_admin_no_longer_entitled_is_not_placed` | The narrowed row FAILs for the right reason (`case='lost the role'`, failures=1). |
| Q8 | The `staff_access.py` docstring and `ADMIN_TARGET` still said a reset is refused for "a hospital-wide admin at any hospital". | low → low | Module docstring now says an admin role on any top-level organisation (`top_level_admin`, Q-K130 A); a comment on `ADMIN_TARGET` says the fixed text means any admin at the top. The text itself is unchanged (the web app maps it). | none | none |

Known limits added:
- A root made invalid while the hospital runs is seen at the next start-up, release or restore
  (A2). `hospital-root show` checks it at any time.
- An operator's own `place`/`remove-state` is in the audit trail only, not the log store (T5).
- An old unmarked "Hospital administration" role organisation on a dev hospital stays beside the
  marked root and opens nothing (T7).
- A provisioning operation in flight across the upgrade is recovered by resuming it (T1; the
  fleet step skips hospitals still being provisioned).

Suites after the Sonnet fixes (`IHR_DB_ROLES=test`, tenant mode, fresh test database):
- `ihr_identity ihr_access ihr_audit ihr_refdata ihr_roles` plus `test_provisioning`, `test_release`
  (fleet), `test_restore`, `test_db_roles`, `test_console` and the isolation suite: 1084 tests OK
  (this includes the role matrix, which is in `ihr_access`). Three tests files were then tidied for
  ruff (`SIM117`, `FBT003`) and re-run: `test_root`, `test_seed_admin`, `test_restore`, `test_release`
  and the isolation suite, 206 tests OK.
- `scripts/test_staging_logs.py`: 50 passed. `isolation-mutation.sh check` passed with 15 root rows
  (10 before, plus `root-reset-offer-target`, `root-reset-sub-target`, `root-failed-line`,
  `root-deferred-line`, `root-operator-not-automatic`). `pre-commit run --all-files` passed.
- Not run: `scripts/check-logs.py` (Docker, builds a VictoriaLogs stack; the dev stack is up). Its new
  case (`eta`, `outcome=failed`) is covered only by `test_staging_logs.py`, which checks the rule's
  regex against the same line.

## Verification

Verifier: Opus, high, 2026-11-22 (wrote none of the build). sha256 of all 49 changed files was
recorded first; at the end only `ihr_platform/tests/test_provisioning.py` (the late fix below) and
this file differ. Mutation rows were applied to scratch copies of `plugins/` (`IHR_ROOT=<copy>`),
never to the working tree.

| Check | Result |
|---|---|
| Full run, fresh test DB, `IHR_DB_ROLES=test`: `ihr_identity ihr_access ihr_audit ihr_refdata ihr_roles` + `test_provisioning test_release test_restore test_db_roles test_console` + `ihr_platform.tests.isolation` (role matrix inside `ihr_access`) | PASS: `Ran 1084 tests in 1307.3s OK`; Redis 926 keys, unprefixed 0; crawl A/B 213 requests each, gated B 426 (503: 424) |
| `python3 -m unittest scripts/test_staging_logs.py` | PASS: 50 OK |
| `isolation-mutation.sh check` | PASS: exit 0, 15 root rows |
| 15 root rows apply/test/expect | 14 PASS on first run; **`root-activate-heals` FAILED its contract**: the test went red as an ERROR (`StepError` escaping `runner.run`), and its `expect` (like CI's "Isolation mutation" job, `apps.yml:332`) demands `^FAIL:`. After the late fix: 15/15 red as FAIL with every expect matched |
| Each regression test fails for its own reason | PASS: gate `200 != 403`/`True is not false`; system-made `None != <root>`; live-admins `('exists',…) != ('no-admin',…)`; one-change: admin left on the root; activate-live `StepError not raised`; heals "did not go live: … no valid root with an admin"; failed/deferred line not written; operator `CommandError not raised` ×14; reset targets `['reset-sign-in']` / `202 != 403`; fleet-checked `('done', id) != ('no-admin', None)` |
| Q5 revert (names dropped from `_link` in `handle()`) | red, as an ERROR (`IndexError`), as the fixer said; not a mutation row, so accepted |
| Q3, Q4 proofs | not re-run (Q3 needs a reversed isolation run, Q4 a hand-made pre-dump root) |
| Dev stack (`ihr_dev`) | PASS: alpha `care-admin` (admin.priya) hospital_wide=True; beta `care-fac-admin` (admin.bhavesh) hospital_wide=False, top_level=True; alpha `care-facility-admin` False; greeting names set |
| `pre-commit run --all-files` | PASS, before and after the late fix |
| Spot-checks of file:line claims (12) | `invitations.py:576/601`, `fleet.py:87/91/549/1185`, `ihr_create_tenant.py:65`, `seeding.py:175`, `ihr_keycloak_demo_users.py:64`, `engine.py:1154-1172`, `staff_access.py:180` exact; `roots.py` 62/67/79/182 → 65/75/88/207, `staff_access.py` 72/84/259 → 74/86/263, `fleet.py:1205` → 1217, `steps.py:388` → 389 (drift from the review fixes, content as claimed) |

**LATE FIX (verifier):** `test_provisioning.ProvisioningTests.test_an_operation_past_first_admin_before_the_root_goes_live_with_one`
now turns a `StepError` from the resumed `runner.run(op)` into `self.fail(...)`, so the
`root-activate-heals` mutation shows as an assertion FAIL, as its row and CI require. Unmutated:
`test_provisioning` 12 OK. Mutated: `FAILED (failures=1)`, expect matched. No product code changed.

Graphify calls: 10 (explain ×7, affected ×2, update ×1).
