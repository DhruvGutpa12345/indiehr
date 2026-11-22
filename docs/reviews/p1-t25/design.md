# P1-T25 design: hospital-root admin gate (Q-K76 B)

Status: design, 22-11-2026 (unattended run). No code changed. Decisions start at Q-K126.
Sources: QUESTIONS Q-K76 (B confirmed "before the first pilot"), Q-K80, Q-K81, Q-K117, Q-K118, Q-K120 A,
Q-K121, Q-K122; docs/reviews/p1-t24/design.md; ADR-009, ADR-010 §7, ADR-017, ADR-018.

## 1. Problem and goal

`ihr_identity.staff_access.hospital_wide_admin` (plugins/ihr_platform/ihr_identity/staff_access.py:74)
accepts a role holding CARE's `can_create_user` on **any** active top-level organisation. Those include the
36 government state organisations (`ihr_load_geography`) and CARE's fixture role organisations. Provisioning
makes each first admin "Administrator" of the hospital's **state** organisation. So anyone given an admin
role on any state organisation can read the audit trail, decide pack updates, end records grants and reset
sign-ins.

**Goal:** each hospital records exactly one *hospital root* organisation. The gate accepts only an admin role
on that organisation, active and not deleted. With no root recorded, nobody passes (fail closed).
Provisioning, the seed, the tests and existing hospitals move their admins onto the root.

## 2. Facts from code

- **F1 Gate.** `_admin_memberships` (staff_access.py:62) returns `OrganizationUser` rows, not deleted, whose
  role holds `can_create_user` (Administrator, Admin or Facility Admin: care/security/permissions/user.py:17-21).
  `hospital_wide_admin` adds `parent IS NULL, active, not deleted` (:74-87). `IsHospitalAdmin` (:90-106) also
  requires a signed-in `User` that is not a service account.
- **F2 Callers.** *Asker side* (who may act): `IsHospitalAdmin` for the audit trail (`ihr_audit/views.py:210`),
  packs (`ihr_refdata/api.py:80`) and my-access `hospital_admin` (`ihr_access/endpoints.py:203`); the
  records-grant DELETE (`endpoints.py:120`); the asker checks in `reset_allowed` (staff_access.py:172) and
  `_reset_sub` (:248); `invitations._asker_may_reset` (invitations.py:541-561). *Target side* (who is
  protected from a reset): `reset_allowed` (:172), `_reset_sub` (:251), `invitations._admin_anywhere`
  (:564-608, Q-K121). *Seed and tests:* `seeding.make_hospital_admin` (seeding.py:176-215),
  `make_facility_admin` (:236-258), `world._make_admin_hospital_wide` (tests/isolation/world.py:144-170).
- **F3 Provisioning.** `steps.first_admin` (provisioning/steps.py:266-300) creates the inactive user and
  `OrganizationUser(state, Administrator)`. `steps.facility` (:333-339) passes `created_by_id`, so CARE's
  `Facility.save` makes that user "Facility Admin" at the facility root. `STEPS` is at :506-520. The runner
  adds a later-added step to every open operation as *pending* (provisioning/runner.py:180-184).
- **F4 Checks that ignore which organisation.** `can_create_user` accepts any `OrganizationUser` or
  `FacilityOrganizationUser` holding the permission (security/authorization/user.py:10-22).
  `can_create_facility` checks with no organisation filter (authorization/facility.py:28-31, base.py:32-39),
  and Administrator holds it (permissions/facility.py:16-20). Administrator on a top-level "role"
  organisation therefore gives both.
- **F5 Organisation management.** Administrator lacks `can_create_organization`, `can_delete_organization`
  and `can_manage_organization`; only "Admin" has them (permissions/organization.py:38-55). The first admin
  cannot manage organisations today, the state included. Administrator does hold
  `can_manage_organization_users` (:56-60). Adding a member to a role organisation needs that right on it,
  plus a role no wider than the adder's (authorization/organization.py:55-87). CARE's user create places
  users **only** in `role` organisations, with the same check (emr/api/viewsets/user.py:113-130). A
  provisioned hospital has no role organisation today.
- **F6 What else the state membership gives.** `can_update_facility` through the geo cache
  (authorization/facility.py:33-36; the facility root also gives it). **Patient listing:**
  `Patient.organization_cache` holds the patient's geo organisation and its parents
  (emr/models/patient.py:83-97), and `find_roles_on_patient` reads them (authorization/patient.py:38-41), so
  the state's Administrator lists every patient of the hospital with an address in that state, visit or not
  (as `ihr_access/placement.py:8-10` notes). Also user management over the state tree.
- **F7 Types.** `org_type` ∈ `team|govt|role|product_supplier` (emr/resources/organization/spec.py:10-14).
  `Organization` has `system_generated`, `metadata`, `managing_organizations` (emr/models/organization.py:10-26,
  106); the default manager hides deleted rows (care/utils/models/base.py:9-12).
- **F8 CARE's organisation API** (emr/api/viewsets/organization.py). Only a superuser creates a top-level
  organisation (:157-164); no child goes under a `role` one (:175-179). Non-superusers may not update or
  delete a `govt` or `role` organisation (:120-155); other types need `can_manage_organization`, and the
  update carries `active`, `name` and **`org_type`**, so an Admin could deactivate, rename or retype a
  `team` root. No re-parenting (spec.py:28-50). Delete needs no children and soft-deletes the organisation
  **and all its memberships** (:189-203). `managing_organization` edits role organisations (:317-357).
  Removing a member needs the users right (:426-437). A superuser passes every check.
- **F9 Fixture** (fixtures/scripts/default_fixtures.py:46-58,101-125). It has six top-level role
  organisations. `care-admin` is Administrator on "Administrator". `care-fac-admin` (beta, admin.bhavesh) is
  Facility Admin on "Facility Admin", so he passes the gate today (Q-K118).
- **F10 Forms.** `forms.hospital_organisations` (ihr_refdata/forms.py:454-462) returns the active top-level
  non-govt organisations. With none, `missing_link` (:425-450) holds v1 (Q-K81 A).
- **F11 Storage.** `HospitalProfile` (ihr_tenant_settings/models.py:24) has one row per schema, and `ihr_app`
  has DML on it by default (hospital_tables.txt:1-8; classes `dml|select|none|append`). `Restore._security`
  (restore/engine.py:1276) merges security state strictly. Our triggers are listed in
  `restore/package.py:50-75`.

## 3. Design

**3.1 Record (Q-K126 A).** New tenant model `ihr_identity.HospitalRoot`: table `ihr_identity_hospitalroot`, one
row (unique `singleton`); fields `organization` (FK `emr.Organization`, PROTECT), `set_at`, `set_by`,
`reason`, `previous_organization_id`. `hospital_tables.txt` class `select`: only the owner (provisioner,
migrate, admin one-off) writes it, the app reads it for the gate, so a compromised web app cannot move the
root. Audit triggers record every change (classify it as the ihr_audit tests demand); dumps carry it; the
release gate needs no change. `ihr_identity/roots.py` provides `root_id()` (one query; None when unset),
`ensure_root`, `place(user, role="Administrator")` and `set_root(org)`, each taking `operator` and `reason`;
each write sets `ihr.root_write=on` in its transaction and logs:
`ihr-root: hospital=<slug> action=<create|place|set|remove-state> outcome=<done|refused> by=<operator>`.

**3.2 Root organisation (Q-K127 A).** A new top-level `Organization` named "Hospital administration"
(seeding.py's name), with `org_type="role"`, `system_generated=True` and
`metadata={"ihr": "hospital-root"}`. Because it is a role organisation (F5, F8), CARE refuses non-superuser
edits, deactivation, retyping and deletion; it can have no children and cannot be re-parented. "Add user"
can place staff on it with roles no higher than the adder's. Public routes list govt organisations only.
`ensure_root` never adopts by name: if an unrecorded top-level organisation has that name, it refuses
("use `ihr_hospital_root set`").

**3.3 Protection.** A new trigger, `ihr_root_guard` (`ihr_identity/root_guard.py`), installed and kept
current by post_migrate like `ihr_access/placement.py` and `forms_guard.py`, added to
`package.OUR_FUNCTIONS`/`OUR_TRIGGERS`, with a system check `ihr_identity.E027` (as E026 does).
Unless `ihr.root_write` is on, it refuses (a) on the recorded root's `emr_organization` row, any UPDATE of
`parent_id`, `root_org_id`, `org_type`, `active`, `deleted`, `managing_organizations`, `name` or
`system_generated`, and DELETE; (b) every write to `ihr_identity_hospitalroot` (the only guard in dev, where
`IHR_DB_ROLES=off`). A refusal is SQLSTATE 23514 `ihr_root: …` → 400, as `ihr_forms:` is; it stops a
superuser's API calls too. Memberships on the root stay CARE's (F8); the placement guard keeps our eight
roles off it.

**3.4 Gate.** `hospital_wide_admin(user)` = `root_id()` is set, AND `_admin_memberships(user)` has a row on
it, AND that organisation is active, not deleted and parentless. Otherwise False: with no root, nobody
passes. Superusers are ignored, as today. The asker-side callers (F2) keep their code; their meaning
narrows. On the target side (Q-K130 A), a new `top_level_admin(user)` keeps today's rule (the root counts),
and `reset_allowed`, `_reset_sub` and `invitations._admin_anywhere` use it for the person being reset, so
protection never narrows. `organisation_admin` (:69) is unchanged.

**3.5 Provisioning.** A new step, `hospital_root`, after `first_admin`, run as the owner under the hospital
lock: (1) `ensure_root`; (2) `place(admin_user_id)`; (3) soft-delete the state row that `first_admin` made
(Q-K128 A). `first_admin` stops creating that row. The step is idempotent; resumed operations get it as
pending (F3). `activate` refuses a hospital with no root. The console lists the new step.

**3.6 Existing hospitals (Q-K129 A).** A new `ihr_fleet_run` step, `root`, after `roles`, in
`check … --startup` and in releases. A hospital with no root gets one, and the step places **only**
`result.admin_user_id` of its last succeeded onboarding `TenantOperation`, removing that admin's state row.
With no onboarding operation (dev, `ihr_create_tenant`) the root gets no admin (`outcome=no-admin`). A
recorded root that is inactive, deleted or parented is never replaced (`outcome=invalid`, alert
`HospitalRootInvalid`). The hospital stays served; only the admin screens close. Nobody who loses the gate
is re-added silently.

Operator command `manage.py ihr_hospital_root --slug S` (admin one-off; staging
`scripts/staging.sh hospital-root`): `show` (the root, its admins, and admin-role holders on other top-level
organisations, i.e. who lost the gate); `create`; `place <username>` (recovery when no admin is left, the
Q-K122 limit); `set <org id>` (top-level, active, `role` only; the old root goes to
`previous_organization_id`; alert `HospitalRootChanged`); `plan --all` (read-only). Every write needs
`--operator` and `--reason` and is audited. Only the operator changes the root.

**3.7 Restore.** `Restore._security` copies the parked copy's root when that organisation exists in the
restored copy, is active and is top-level. Otherwise it clears the row, reports `root: unset` and logs
`ihr-root: … outcome=missing`, and the operator uses `set` or `create`. A dump from before T25 takes the
parked root. Root memberships go through the existing strict merge.

**3.8 Forms (Q-K131 A).** `forms.hospital_organisations()` returns `[root]` when a valid root is recorded,
else `[]`. The `missing_link` text becomes "no hospital root (P1-T25)". Existing links stay.

**3.9 Seed and demo.**
- `ihr_create_tenant` calls `ensure_root` (dev, test and bench hospitals get a root). `make_hospital_admin`
  makes the user Administrator on the root (plus Facility Admin at the facility root as before) and drops
  the "keep fixture role" branch. care-admin goes on both roots; admin.priya (alpha) stays the only
  hospital-wide login. admin.bhavesh (beta) **loses** the admin screens: Q-K118's intent, no fixture
  change; update `roles-menus.spec.ts`. `make_facility_admin` is unchanged.
- **Greeting:** `ihr_keycloak_demo_users` sets each linked CARE user's name to its `DEMO_STAFF` name. This is
  safe: each CARE user maps to one login per hospital (ihr_keycloak_demo_users.py:26-56), the data is
  synthetic, it runs under DEBUG only, and the trail shows `user:<pk>:<username>`, never names. Doing it
  for every login stops the "Sachi" finding from coming back.

**3.10 Web.** No change: menus follow my-access `hospital_admin`. CARE shows the root to its members.

**3.11 Tests (each must fail under the old rule).**
- New `ihr_identity.tests.test_root`: Administrator, Admin and Facility Admin on the root pass. Refused: the
  same roles on a state, another top-level role or team organisation, or a removed membership; a role
  without `can_create_user`; anyone when no root is set; a service account. The guard refuses API PUT,
  DELETE, deactivate and `managing_organization` on the root, as superuser too (400 `ihr_root`), and ORM
  writes to `HospitalRoot` outside `roots`. `ensure_root` never adopts by name.
- Changed: `test_my_access` (the provisioned first admin passes; a state-only Administrator is refused,
  flipping the Q-K120 pin); `ihr_refdata test_api` (`state_admin` refused); `test_staff_offers`,
  `test_viewer`, `test_records`, `test_seed_admin` (use a new `ihr_identity.testing.make_root_admin`);
  `test_forms` (root only); `test_provisioning` (root recorded, admin on the root and not the state,
  resume); `test_restore` (parked root kept, pre-T25 dump too); `test_db_roles` (`ihr_app` cannot write the
  table); the fleet step (create, no-admin, invalid; never replaces a root); a state-only Administrator
  stays `protected`.
- Isolation: the world uses `make_root_admin`. New: A's root id grants nothing in B. The matrix should show
  no outcome change; any new key (e.g. `/organization/mine/`) is reviewed into the lists.
- Mutation rows (scripts/isolation-mutation.sh), each naming tests that must fail: `root-gate` (today's
  top-level filter instead of the root: `test_root`, `test_my_access`); `root-unset-closed` (no root falls
  back to today's rule); `root-guard-install` (`ensure` → `pass`); `root-target-wide` (the target check
  uses `hospital_wide_admin`).

**3.12 Docs.** QUESTIONS (Q-K126…131; Q-K76 B built; Q-K80/Q-K81 → C; Q-K120 superseded); addenda to ADR-010
§7 and ADR-009; TENANCY_DESIGN §3; CLAUDE.md; TRACKER; deploy/staging/README (command, log line,
`deploy/logs/alerts/root.yml`). No edit inside `apps/`.

## 4. Decisions (not yet in QUESTIONS.md)

- **Q-K126 Where is the root recorded?** **A ⭐** Its own one-row table `ihr_identity.HospitalRoot`, class
  `select` for `ihr_app`. B: a `HospitalProfile.root_organization` field (Q-K76 B's wording). *Why A:* the
  app has DML on the profile, so a web-app flaw could move the root. A makes "operator only" a database fact,
  for one table.
- **Q-K127 What is the root?** **A ⭐** A new top-level `role` organisation, "Hospital administration".
  B: a `team` organisation. C: adopt an existing one, such as the fixture's "Administrator". *Why A:* CARE
  already refuses non-superuser edits, deletes and children for `role` organisations, and "Add user" places
  staff only in role organisations (F5, F8). Under B any Admin could deactivate or retype the root. C is a
  guess.
- **Q-K128 The first admin's state membership.** **A ⭐** Remove it: new hospitals never get it; existing
  ones lose only the row provisioning made (soft delete, audited). B: keep it, ignored by the gate. *Why A:*
  B is not harmless. It lists every patient with an address in that state, visit or not, and lets the admin
  place others there (F6). The admin keeps user and facility creation (F4) and facility updates (as Facility
  Admin).
- **Q-K129 Existing hospitals.** **A ⭐** The fleet `root` step places only the provisioning record's first
  admin; others go through `show`, then `place`. B: also carry over everyone who passes today's gate. C: the
  operator only. *Why A:* it is deterministic from a stored fact. B carries the hole over. C locks real
  admins out after a release.
- **Q-K130 Who is protected from a reset?** **A ⭐** The target side keeps today's wider top-level rule.
  B: the root only, like the gate. *Why A:* protection must not narrow during the move. The cost: a state
  admin's lost authenticator goes to the operator (Q-K122).
- **Q-K131 Pack form v1 links.** **A ⭐** The root only (Q-K80 C, Q-K81 C). B: keep Q-K80 A. *Why A:* in a
  provisioned hospital the root is the only such organisation, so A and B agree there. A also stops dev and
  test results from depending on the fixture's organisations. No form content ships before Phase 2.

## 5. Out of scope

An operator-console button; refusing new admin memberships on govt organisations (a later placement-guard
rule); multi-facility roots; Keycloak-side roles; removing the fixture's role organisations; a "last admin"
lock on CARE's member removal (`place` is the recovery); P1-T22c2b.

## 6. Risks

- **R1** A root admin can remove every other root admin, themselves included (F8). Recovery: `place`.
- **R2** Upstream could change CARE's role-organisation rules (viewsets/organization.py:120-179). The
  `test_root` guard tests catch it at a sync. The `cached_role_orgs` reset in `OrganizationUser.save`
  (organization.py:123-130) is harmless.
- **R3** Admin screens are closed between the migration and the `root` step (fail closed; `plan --all`).
- **R4** A restore whose parked root is missing in the restored copy leaves it unset until the operator acts.
- **R5** Assumption to test: on a new hospital the first admin can use CARE's "Add user" to put staff on the
  root, with roles up to Administrator. Today that sheet offers no role organisation there (F5).
- **R6** One more (uncached) query per gate call; a root change applies at once.
