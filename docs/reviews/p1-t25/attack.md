# P1-T25 design attack (2026-11-22)

Lenses: S = Opus security/tenancy (high); D = egm-design, Z = egm-zoom-out, R = egm-implementation-readiness (Sonnet high). Design: `design.md`. Decision: at the end.

## attack:Z

Z1 (high) – §3.1, Q-K126 A, §3.7. The HospitalRoot table is over-built, and its stated reason does not hold.
- The design says the `select` class stops a compromised web app from moving the root. But `ihr_app` also writes OrganizationUser, so the same flaw can add itself as Administrator on the root. A hardened pointer protects nothing.
- Cost of the table: a new model, a migration, a `hospital_tables.txt` entry, audit classification, `root_id()`, `set_at/set_by/reason/previous_organization_id` fields, a dump/restore branch (§3.7) and a `test_db_roles` case.
- Smallest fix: the root is the organisation itself, marked by `metadata={"ihr":"hospital-root"}` with `org_type="role"`, `system_generated=True`, parentless. Role-org rules already stop non-superuser edits (F8). The gate becomes "an admin role on an active, undeleted org carrying the flag". Restore and dumps then need no new code, because the flag travels with the organisation.

Z2 (high) – §3.3, E027, the `root-guard-install` mutation row. The `ihr_root_guard` trigger is not needed.
- It adds a function, a trigger, a post_migrate installer, a system check, a package.OUR_FUNCTIONS/OUR_TRIGGERS entry and a mutation row.
- Its only extra reach beyond CARE's role-org rules is stopping a superuser. Superusers are already trusted everywhere in this design (the gate ignores them, F8 says they pass every check).
- Fix: drop the trigger and E027, and rely on the `role` type. Keep the `test_root` tests that pin CARE's behaviour (R2).

Z3 (high) – §3.6, §3.9, Q-K129, and the `HospitalRootChanged`/`HospitalRootInvalid` alerts. The existing-hospital migration looks built for fleet scale.
- Q-K76 B is "before the first pilot", so only dev and staging hospitals exist.
- The design adds a fleet step, an `onboarding TenantOperation` lookup, `outcome=no-admin|invalid`, `plan --all`, two alert rules and `root.yml`.
- Fix: one idempotent `ensure_root_with_admin(admin)` function used by provisioning, `ihr_create_tenant` and one fleet step. Staging admins are fixed by hand with `place`. No alerts, no `plan --all`.

Z4 (medium) – §3.6 operator command. `show`, `create`, `place`, `set` and `plan` are five subcommands where two would do.
- `set` and `previous_organization_id` exist only to recover a deleted, deactivated or retyped root. Z2's `role` type already prevents that.
- `create` duplicates the fleet step.
- Fix: keep `show` (it lists who lost the gate) and `place` (the Q-K122 recovery). Cut the rest.

Z5 (medium) – §3.5. The separate `hospital_root` provisioning step creates a state membership and then soft-deletes it, which is churn.
- Fix: change `first_admin` itself to call `ensure_root` and `place`, with no state row. This removes a STEPS entry, the console change and the soft-delete branch.
- Operations already past `first_admin` are picked up by the fleet step, and `activate` still refuses without a root.
- Soft-deleting the state row is needed only for already-provisioned dev hospitals (Q-K128 A). That belongs in the fleet step, not in new-hospital provisioning.

Z6 (medium) – §3.8, Q-K131. The forms change is not needed.
- `hospital_organisations()` (F10) already returns active, top-level, non-govt organisations. The root is a role organisation, so it is included.
- The state row is gone after Z5, so in a provisioned hospital the root is the only such organisation. The design itself says "A and B agree there".
- "No form content ships before Phase 2" confirms nothing consumes it.
- Fix: cut §3.8 and leave Q-K80 and Q-K81 as they are.

Z7 (medium) – §3.2 and R5. The design's central assumption is untested.
- R5 says the first admin may not be able to put staff on the root, because "Add user" offers no role organisation today (F5). If that fails, the design's own usability story fails.
- Fix: make R5 a spike before the design is accepted, not a late risk. If it fails, `place` is the only way to add staff.

Z8 (low) – §3.4 and Q-K130, the new `top_level_admin`.
- This adds a second admin-wide rule that lives forever, plus a `root-target-wide` mutation row, to avoid narrowing protection.
- Fix: rename the old `hospital_wide_admin` body to `top_level_admin` as a plain move, with no new tests beyond the existing `protected` case. Alternatively reuse `organisation_admin` if the protection change is acceptable. Drop the mutation row.

Z9 (low) – §3.9 Greeting. The `ihr_keycloak_demo_users` rename is unrelated to the gate (it is the T24 "Sachi" finding). Move it to its own task. It does not belong in a security-gate design.

Z10 (low) – §3.11. Four mutation rows are too many. Keep `root-gate` and `root-unset-closed`, and drop the guard and target rows with Z2 and Z8. The `test_restore` and `test_db_roles` additions go with Z1.

Simpler rule overall: root = one flagged parentless `role` organisation. Gate = an admin role on it, with fail-closed when none exists. The first admin goes straight onto it. There is no table, no trigger, no restore logic and no alerts. The operator tool is `show` plus `place`.

tier-fit: over-built for a pre-pilot task. The gate narrowing itself (§1, §3.4) is right and small. Z1 to Z5 would remove roughly half the new surface without weakening it. The design fits a heavier tier only if real hospitals already exist, and Q-K76's "before the first pilot" says they don't.

## attack:D

Design review of P1-T25 (docs/reviews/p1-t25/design.md). I read the design, staff_access.py L55-284, steps.py L255-340, invitations.py L540-614 and seeding.py L170-255.

D1 [high] §3.7, §3.6, §3.4. Nothing checks that the recorded root is the right kind of organisation. The gate checks only parentless, active and not deleted. Restore checks only "exists, active, top-level". `set` is "role only" at the command, but restore and the fleet step don't re-check it. Organisation ids come from a per-schema sequence. A restored copy can hold a different organisation at the parked root's id, such as a state, a team or the fixture "Administrator" role organisation. The gate would then accept admins of that organisation, which is the hole this task closes.
Fix: one `valid_root(org)` predicate used by the gate, `set`, restore, the fleet step and `activate`. It requires top-level, active, not deleted, `org_type=="role"` and `metadata.ihr=="hospital-root"`. Also store the root's `created_at` or uuid in `HospitalRoot` and compare it on restore.

D2 [high] §3.5 step 3, §3.6, Q-K129 A. The fleet step places `result.admin_user_id` from the last succeeded onboarding operation without checking that person today.
- That user may have been deactivated, deleted or demoted since onboarding. The step would silently make them hospital-wide admin again, and "nobody is re-added silently" is not true.
- Removing "that admin's state row" is not tied to what provisioning made. It could delete an Administrator row someone granted deliberately.
Fix: place only if the user is not deleted and passes today's rule (`top_level_admin`). Otherwise log `outcome=no-admin`. Soft-delete only a state row with role Administrator for that user, and only if the user was placed. The same applies to the §3.5 new-hospital path on resume.

D3 [medium] Q-K126 rationale, §3.1, §3.3. "A compromised web app cannot move the root" is overstated. `ihr_app` has DML on `emr_organizationuser`, so it can add itself as Administrator on the root. Guard (a) on `emr_organization` is switched off by `SET ihr.root_write=on`, which any session can run. Only the `ihr_identity_hospitalroot` table is actually protected, and that is the least useful thing to protect.
Fix: restate the threat model as protection against CARE API and admin misuse, not app compromise. Also say that membership on the root is the real privilege and is outside the guard. Alternatively use a `HospitalProfile` field behind the same trigger and drop the table, restore branch and extra decision.

D4 [medium] R5, Q-K127 A, §3.9. The design's own R5 says the first admin may have no way to add a second admin to the root. R5 says CARE's "Add user" sheet offers no role organisation on a new hospital today. Q-K127 A rests on that path working. The first admin also loses user management over the state tree (F6).
Fix: make R5 a pre-build check with a stated fallback, and don't treat it as a post-build risk. If the sheet cannot offer the root, the supported path to a second admin is the operator's `place`. Say so, and say whether pilot hospitals accept that.

D5 [medium] §3.6, §3.1. The fleet step runs in `check --startup` and in releases. The table is `select` for `ihr_app`, and only the owner writes. The design doesn't say which DB role runs the step, so the startup path may be unable to write. With several replicas, two could race `ensure_root`, since the singleton constraint stops only the second record write. `ensure_root` creates an organisation and then the record.
Fix: name the owner role and the hospital job lock (as provisioning uses). Run create and record in one transaction. Let `check --startup` only report, and let releases write.

D6 [medium] §3.2. "`ensure_root` never adopts by name" will refuse on hospitals the seed has already touched. `make_hospital_admin` (seeding.py L203-211) creates an unrecorded top-level "Hospital administration" role organisation when none exists. For those hospitals the fleet step ends in `refused`, so the admin screens stay closed on them. The seeded one also lacks `system_generated` and the metadata marker.
Fix: let `ensure_root` adopt a top-level, active `role` organisation with exactly that name, no children and no marker. It adopts only when run by the operator, the seed or the fleet step. It then sets the marker and `system_generated`.

D7 [medium] §3.3. "Any UPDATE of `parent_id`, …" is ambiguous. Django `save()` rewrites every column. A trigger on `UPDATE OF cols` would fire on CARE's own saves of the root. CARE's creation path also does INSERT then UPDATE (`root_org_id`, parent json).
Fix: fire only on `OLD.col IS DISTINCT FROM NEW.col`. State that the create sequence runs under `ihr.root_write`. Add a test that a no-change `Organization.save()` on the root passes.

D8 [low] Q-K130 A, §3.4. Protection is wider than the gate. A state-only Administrator can't be reset by a root admin or anyone else. The refusal text `ADMIN_TARGET` still says "hospital-wide admin", which would be wrong for them.
Fix: give the protected-but-not-gate case its own message code, such as "ask IndieHR support". Add a test that an offer is not shown for such a target.

D9 [low] §3.1, R6. Each gate call makes two queries (`root_id()` and the membership). `IsHospitalAdmin` and my-access run on every page.
Fix: do it as one join, `filter(organization__hospitalroot__isnull=False, …)`, or cache per request.

D10 [low] §3.11. The mutation row `root-target-wide` and the D1 failure aren't covered. There is no test that a root pointing at a non-role or govt organisation yields a closed gate, or that a restore with a colliding id does.
Fix: add both tests, each failing under today's rule.

tier-fit: over-built for a pre-pilot, single-gate change. The table, trigger, restore branch, fleet step and operator command add up to a lot of new surface. It is justified only if D1 to D3 are fixed. Otherwise a profile field plus the `valid_root` predicate would give most of the safety.

## attack:R

Implementation-readiness review of docs/reviews/p1-t25/design.md. Verdict: not buildable from the design alone. The core rule (gate, target-side split, provisioning step) is clear. The model, migration, fleet step, restore and guard details are not. I read the design, staff_access.py, provisioning/steps.py (first_admin), runner.py, invitations.py, seeding.py and fleet.py. I did not read the other files the design cites.

R1 (high) 3.3 and 3.7. The guard and Restore conflict. The guard refuses any write to the root's `emr_organization` row and to `ihr_identity_hospitalroot` unless `ihr.root_write` is on, and only `roots.py` sets that flag. 3.7 has `Restore._security` copy or clear the root row, and Restore also replaces `emr_organization` rows through the strict merge. Nothing says Restore sets the flag or calls `roots`. As written, a restore either trips the guard or needs a bypass the builder has to invent. The same goes for pg_restore and dump loading with the new trigger. Fix: say Restore writes the row only through a `roots.restore_root(...)` that sets the flag, and say how the trigger behaves during a restore load. Add a `test_restore` case for it.

R2 (high) 3.1 and 3.6. The model is under-specified and there is no migration step. Missing:
- the field types for `singleton`, `set_by` and `reason`;
- how "unique singleton" is enforced;
- the migration file (`ihr_identity/migrations/0004_hospitalroot.py`) and its dependency;
- the `hospital_tables.txt` line;
- how the table is "classified" for ihr_audit ("as the ihr_audit tests demand" names no file or class);
- the `roots.py` signatures. Only `root_id()` is clear. `ensure_root`, `place`, `set_root` and the "remove-state" action have no parameters or return types.
Fix: give a field table, the migration name, the exact `hospital_tables.txt` and audit-classification entries, and one signature per function.

R3 (high) 3.6. The fleet `root` step is unplaced. Steps live in `ihr_platform/fleet.py` (`STEPS = ("hold","migrate","check","roles","packs","release")`, `ROLES_STATUSES = (ACTIVE, SUSPENDED)`, `_roles` at about L1127). The design names none of this, and it leaves open:
- which hospital statuses the step covers;
- which DB role it writes as (the table is `select` for `ihr_app`, so the startup one-shot must run as the owner);
- how `outcome=invalid` and `no-admin` map onto fleet's step statuses and holds. Today a failed `roles` step holds the hospital through `release.HOLD_ROLES`, but 3.6 says the hospital stays served;
- how the `ihr_fleet_run` argparse, docs and scripts change.
Fix: name `fleet.py`, the new `_root` method, its statuses, the DB role, and say that no hold is set.

R4 (high) 3.5 and rollout. There is no rollback or ordering story. Moving the first admin off the state organisation is irreversible. A rollback to the old build leaves that admin locked out, because the old rule needs a top-level admin membership and the admin's only such row is on the role-type root, which the old rule ignores. Fix: state "roll-forward only", or have the fleet step keep the state row until a later task.

R5 (medium) R5 and 3.5. The design removes the first admin's state membership but only assumes that "Add user" can place staff on the root. If that assumption is false, a new hospital's admin cannot add staff, and there is no fallback. Fix: make the R5 test a named test in `test_provisioning` or `test_root`, and say what to do if it fails.

R6 (medium) 3.9 and 3.12. They contradict each other. 3.9 says to update `roles-menus.spec.ts`, which is at `apps/frontend/tests-ihr/roles-menus.spec.ts`. 3.12 says "No edit inside `apps/`". The beta e2e login also loses the admin screens, and the design does not say which assertions change. Fix: allow that file and list the assertions.

R7 (medium) 3.11. The test names and mutation targets are incomplete.
- Only module names are given, with no method names.
- `root-unset-closed`, `root-guard-install` and `root-target-wide` name no failing tests (only `root-gate` does).
- The sentence "each must fail under the old rule" is false for the root-admin-passes cases, which also pass under the old rule.
- The test list omits `ihr_identity/tests/test_invitations.py` and `ihr_platform/tests/test_provisioning.py`'s step-count assertions. The first has 6 hits for `hospital_wide_admin`/`can_create_user`/`OrganizationUser`. Fix: add a method-name list and a mutation-to-tests map.

R8 (medium) 3.3. The guard's SQL contract is unspecified: how it identifies "the recorded root" (a subselect on `hospitalroot` per row, so every `emr_organization` UPDATE pays a query), and what message and SQLSTATE the HospitalRoot-table refusal uses (the 400 mapping says "as `ihr_forms:` is" but gives no file). The check ID `ihr_identity.E027` is called "as E026 does", but E026 is `ihr_refdata.E026` and `ihr_identity` has no E0xx yet. Fix: give the trigger body outline, the file where the 400 mapping is registered, and the check numbering rule.

R9 (low) 3.4, 3.7 and 3.8. "Valid root" (active, not deleted, top-level) is restated in the gate, forms, restore and fleet. Fix: one `roots.valid_root()` helper that all four call.

R10 (low) 3.6 and 3.12. The alerts are not defined. `HospitalRootInvalid`, `HospitalRootChanged` and `deploy/logs/alerts/root.yml` have no expression, threshold or log-parse rule, and `scripts/test_staging_logs.py` validates alert files. The "Greeting" block in 3.9 is unrelated scope with no tests named. Fix: add the alert rules and test, or drop the Greeting block.

tier-fit: The design suits a medium-risk security change. It needs one more pass (R1 to R4) before a builder can start without making design decisions.

## attack:S

P1-T25 design attack (security and tenancy). Graphify calls: 9 (7 explain, 2 affected).

**S1 high, §3.6 / Q-K129 A.** The fleet `root` step places `result.admin_user_id` from the last succeeded onboarding operation. It does not check whether that person is still an admin.
- Failure: the first admin left the hospital. A colleague removed their state membership or deactivated them. The release re-adds them as Administrator on the root, giving them the gate again.
- Fix: place them only if the user is active, not deleted, and still holds an undeleted admin-role row on the state organisation (that is, passes today's gate). Otherwise report `outcome=no-admin`.

**S2 high, §3.7.** A restore can leave the hospital with no admin.
- The root organisation and its memberships were created after any pre-T25 dump. The strict merge (engine.py:1346-1384) counts the root memberships as "lost, shown not given". It also soft-deletes the first admin's state row, because that row is deleted in the parked copy. So "a pre-T25 dump takes the parked root" cannot happen: the organisation is not in the restored copy.
- With `--keep-restored-access` the merge is skipped, so root admins removed after the dump come back.
- Fix:
  - After `_security`, run the same `ensure_root` and checked placement as the fleet step, and report the result.
  - Match the root by `external_id` plus `org_type=role` plus the `hospital-root` marker, not by primary key.
  - Merge root memberships strictly even when `--keep-restored-access` is given.

**S3 high, §3.6 `set <org id>`.** `set` accepts any top-level, active `role` organisation.
- Failure: the fixture's "Administrator" organisation already has members. Any role organisation may also have non-empty `managing_organizations`. Holders of `can_manage_connected_role_organizations` on a managing organisation can add users to the root (security/authorization/organization.py:61-82). Those users become hospital-wide admins.
- Fix: `set` accepts only an organisation with `metadata.ihr=hospital-root`, `system_generated` and empty `managing_organizations`. It shows the current members and asks before switching.

**S4 medium, §3.2 / F8.** The rationale is incomplete. The `managing_organization` action (viewsets/organization.py:316-354) lets a non-superuser with `can_manage_organization` on the root change `managing_organizations`. Those are Admin and Role Organization Admin.
- The guard's column list covers this, so it is the main way to escalate.
- Fix:
  - Make the gate also require the root's `managing_organizations = '{}'`.
  - Add a test: a non-superuser Admin on the root calls `managing_organization` add and gets 400 `ihr_root`.
  - Add a mutation that drops this column from the guard.

**S5 medium, §3.6 / Q-K128.** Before T25 the state organisation was the only one where admins could place staff (F5). Other people on it with an admin role keep F6 patient listing across the whole state. They can keep adding Administrators there, and they stay reset-protected (Q-K130 A). Once the first admin's state row is removed, no hospital admin can manage them.
- Fix:
  - The fleet step logs `ihr-root: … outcome=state-admins n=<k>` and raises an alert.
  - Add an operator command `ihr_hospital_root remove-state <username>`. The log action `remove-state` exists, but no command is defined.

**S6 medium, §3.3.** `ihr.root_write` is a custom setting that any role can SET, `ihr_app` included. For the root's `emr_organization` row, this setting is the only protection.
- Also, the trigger must look up the root in `TG_TABLE_SCHEMA`, not through `search_path`. Restore works with parked and quarantine schemas.
- Fix: when `IHR_DB_ROLES=required`, also require `current_user` to be the owner role, and qualify the lookup by the trigger's schema.

**S7 medium, §3.11 mutations.** The four proposed rows would not catch these regressions:
- the gate dropping root `active`, `deleted` or `parent` (add a deactivated-root test plus a row);
- the fleet step placing every top-level admin, i.e. Q-K129 B (`root-fleet-wide`);
- the `HospitalRoot` table changed from `select` to `dml` (`root-table-class` → `test_db_roles`);
- the `managing_organizations` guard (S4).

Also, `root-target-wide` covers one of three target-side call sites (`reset_allowed`, `_reset_sub`, `invitations._admin_anywhere`). Use one shared `top_level_admin` with a row per site.

**S8 low, §3.5 / §3.6.** The startup `root` step goes through every `MIGRATE_STATUSES` hospital, PROVISIONING ones included, while provisioning may be running its own `hospital_root` step. Two concurrent `ensure_root` calls can create two organisations or refuse each other by name.
- Fix:
  - The fleet step skips PROVISIONING hospitals.
  - Both run under the same `release.job` lock.
  - `activate` also requires at least one active admin on the root.

**S9 low, §3.1 audit.** The owner-side writes are not tied to a named person in the trail. These are the root row, root placement and the state-row soft delete, made by commands, fleet and provisioning. Make the trail record `operator:<name>` as the actor for each one, not "system". Make `set_by` the operator text, not a user foreign key.

**S10 low, §3.6 / R6.** The design does not say which database role runs `root` inside `check --startup`. It must be the owner, since `ihr_app` only has SELECT on the table. Also add: `root_id()` must never be cached in a process, so that `set` and restore take effect at once.

tier-fit: OK: a multi-surface security design attack needed Opus high; the findings were checked against CARE's authorization and restore code.

## Decision (orchestrator, 2026-11-22)

Binding for the build; wins over design.md where they differ. Z's simpler shape is taken, and S/D's checks are kept on
what remains. R4 is refuted: the old rule accepts an admin role on *any* top-level organisation, the role-type root
included, so a rollback to the old build keeps the moved first admin in (no roll-forward-only rule is needed).

1. **The root is the organisation itself, no table, no profile field, no trigger (Z1, Z2; Q-K126 A, Q-K131 A).** A
   hospital root is a top-level CARE organisation with `org_type="role"`, `system_generated=True` and
   `metadata["ihr"] == "hospital-root"`, named "Hospital administration" (the name means nothing; the marker identifies
   it). `ihr_identity/roots.py` (new): `valid_root()` returns the one organisation that has the marker AND is active, not
   deleted, parentless, `org_type="role"` and has `managing_organizations == []` (S4), or None when there is none, when
   the marked one fails any of those, or when more than one is marked (fail closed). Never cached across requests (S10).
   This removes design §3.1, §3.3, §3.7's copy/clear branch, E027, `HospitalRoot`, `hospital_tables.txt`, dumps and the
   D1/D3/D5/D7/R1/R2/R8/S6/S9 issues. Protection rests on CARE's role-organisation rules (F8: non-superusers cannot
   update, deactivate, retype, delete or add children); `test_root` pins each of them (R2), plus `managing_organization`
   (S4: a change makes `valid_root()` None, i.e. the screens close, never widen).
2. **Gate (§3.4, kept).** `hospital_wide_admin(user)` = `valid_root()` is not None AND `_admin_memberships(user)` has a
   row on it. Superusers ignored as today. No root → nobody passes. `top_level_admin(user)` = today's rule, moved
   unchanged; the target side (`reset_allowed`, `_reset_sub`, `invitations._admin_anywhere`) uses it (Q-K130 A, Z8: a
   plain move). D8: keep the existing `admin-target` code; no new phrase.
3. **`ensure_root(operator)`** creates the marked organisation when no organisation carries the marker, and never
   adopts one by name (D6 moot: names do not matter). When a marked organisation exists but is not valid, it does
   nothing and returns `invalid` (never a second root). Runs under the hospital's job lock (S8, D5).
4. **Provisioning (Z5).** `steps.first_admin` calls `ensure_root` and makes the first admin "Administrator" on the root;
   it no longer creates the state-organisation row (Q-K128 A). No new STEPS entry. `activate` refuses a hospital
   without a valid root holding at least one admin membership (S8). Operations already past `first_admin` are covered by
   the fleet step.
5. **Fleet step `root` (Q-K129 A with S1/D2).** New `ihr_fleet_run` step `root` in `fleet.py` after `roles`, statuses
   ACTIVE and SUSPENDED (skips PROVISIONING), under the job lock, in `check … --startup` and releases (owner role, as
   `roles`). Per hospital: `ensure_root`; if the root has no admin membership, take `result.admin_user_id` of the last
   succeeded onboarding `TenantOperation` and place that user as Administrator **only if** the user exists, is active
   or awaiting setup (not deleted), and passes `top_level_admin` today; then soft-delete only that user's
   Administrator row on a `govt` top-level organisation (D2), audited. No hold, the hospital stays served. Log line
   `ihr-root: hospital=<slug> action=<ensure|place|remove-state> outcome=<done|exists|no-admin|invalid|state-admins> by=<operator>`
   (`state-admins n=<k>` counts other admin rows left on govt organisations, S5). One log alert
   `HospitalRootProblem` (outcome `no-admin` or `invalid`) in `deploy/logs/alerts/root.yml`, validated by
   `scripts/test_staging_logs.py` (Z3 keeps it to one alert).
6. **Operator command `manage.py ihr_hospital_root --slug S`** (Z4 + S5): `show` (the root, its admins, admin-role
   holders on other top-level organisations), `place <username> --operator --reason` (recovery, the Q-K122 limit),
   `remove-state <username> --operator --reason` (S5). No `set`, `create` or `plan` (S3 moot). Staging wrapper
   `scripts/staging.sh hospital-root`.
7. **Restore (S2).** After `Restore._security`, the engine runs the same per-hospital root step (ensure + checked
   placement) and reports `root:` in its result; a pre-T25 dump therefore comes back with a root and the provisioned
   admin when that person is still entitled. Root memberships go through the existing strict merge;
   `--keep-restored-access` stays the operator's explicit choice (known limit, not changed).
8. **Forms (Z6).** No change: Q-K80 A / Q-K81 A stand (the root is a non-govt top-level organisation, so new forms link
   to it). §3.8 dropped.
9. **Seed.** `ihr_create_tenant` calls `ensure_root`. `make_hospital_admin(user, facility)` places the user as
   Administrator on the root (plus Facility Admin at the facility root) and drops the fixture branch; care-admin on
   alpha and beta. admin.bhavesh loses the admin screens: update `apps/frontend/tests-ihr/roles-menus.spec.ts`
   (our file; list the changed assertions in build.md) (R6). Greeting: `ihr_keycloak_demo_users` sets each linked
   CARE user's first and last name to its `DEMO_STAFF` name (DEBUG only; a test).
10. **R5 / Z7 / D4 first.** Before the rest, a test in `test_root` proves that a provisioned first admin (Administrator
    on the root only) can create a user placed on the root through CARE's user-create API with a role up to
    Administrator, and cannot place one with a higher role. If it fails, stop and report: `place` becomes the only
    path and that is a new decision.
11. **Tests and mutations.** As design §3.11 minus the dropped parts, with method names in build.md, each new negative
    test shown failing under today's rule. Mutation rows: `root-gate` (today's top-level filter), `root-unset-closed`
    (no root falls back), `root-valid` (drop the active/deleted/parent/managing checks), `root-fleet-checked` (place
    without `top_level_admin`), `root-target-wide` (`_admin_anywhere` uses the gate). my-access query count re-pinned
    with the stated number.
12. **Decisions** written to QUESTIONS.md (style of Q-K105, "auto-accepted by Claude, 2026-11-22, awaiting owner
    confirmation"): Q-K126 A the root is a marked organisation (B a table, C a profile field: Q-K76 B's wording);
    Q-K127 A a new `role` organisation; Q-K128 A the first admin's state row removed; Q-K129 A the fleet step places
    only the onboarding admin and only if still entitled; Q-K130 A reset protection keeps the wider rule; Q-K131 A no
    guard trigger, CARE's role-organisation rules pinned by tests. Q-K76 B marked built; Q-K120 superseded.
