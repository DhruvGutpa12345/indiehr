# P1-T11 design: Indian hospital roles (draft for attack, 2026-11-12)

PLAN row: "Seed Indian hospital roles: Receptionist, Cashier, Lab Tech, Radiologist, TPA Desk, MRD,
Store Keeper, Owner. Each role sees only its screens (tests)."

Research is in the run's scratchpad (`t11/care-roles.md`, with file:line evidence) and is
summarised here.

## Split (by Claude, 2026-11-12, awaiting owner confirmation)

- **P1-T11a, backend (this design).** Eight code-defined roles, their grants, seeded test users
  for each, and tests that each role's permissions and endpoint answers are exactly as intended.
- **P1-T11b, web app.** The facility and location menus show only what the user's permissions
  allow. This is a small patch in `apps/frontend` (a pure `navVisibility.ts` with its own tests,
  CI only), logged in UPSTREAM_PATCHES.md. It also fixes the frontend's locked-invoice slug,
  which never matches the backend's.

## How CARE does it (summary)

- **Roles** are defined in code (`care/security/roles/role.py`) and registered with
  `RoleController.register_role`, which has no duplicate check.
- **Grants** live on each permission: `Permission(..., roles=[...])`. A grant is a role in a
  permission's list.
- **Sync.** `sync_permissions_roles` writes system roles and their RolePermission rows in each
  hospital's schema. It runs at every start and at provisioning. Hospitals cannot edit system
  roles, and only superusers create roles.
- **Assignment.** A user gets a role through `FacilityOrganizationUser` or `OrganizationUser`
  rows.
- **Where a membership counts.**
  - Billing and products: anywhere in the facility.
  - Pharmacy, inventory and lab queues: only the org linked to that location.
  - Root-org members see every encounter.
  - Patient access ignores completed encounters, so seeing discharged patients needs a
    government-org membership.
- **The web app** gets permissions per facility from the facility API and gates only a few
  menus today.

## Design

1. **A code-only plugin app, `ihr_roles`** (shared nowhere: no tables, so nothing for the audit
   classifier).
   - It defines eight `Role` constants and a `GRANTS = {role: [permission slug, …]}` table.
   - `AppConfig.ready()`:
     - registers each role once;
     - **rebinds** each granted permission's role list (`perm.value.roles = [*perm.value.roles,
       ROLE]`). It never appends: CARE's encounter permissions share one list object, and an
       append would grant all six of them, and change Doctor's and Nurse's too.
   - It fails at start-up if a slug is unknown (an upstream rename) or a role name clashes with
     one of CARE's.
2. **The grants (Q-K21, auto-accepted, awaiting the owner).** Least privilege: non-clinical
   roles get no clinical-data permission, and none of the eight gets `can_create_user`. Our code
   treats that permission as admin (audit viewer, colleagues' sign-in resets).

   | Role | Gets | Deliberately not |
   |---|---|---|
   | Receptionist | register and edit patients; create, edit and read encounters (no clinical data); bookings and schedules; tokens; identifier config; services; locations; users list; questionnaires (read); billing accounts (create and read) | clinical data, questionnaire responses, billing writes beyond opening an account |
   | Cashier | patients list; encounters (read); accounts; charge items (create and read); invoices and payments (read and write); price definitions and services (read) | delete invoices, locked invoices, negative or cancelled charge items, delete payments (they stay with Facility Admin); clinical data |
   | Lab Tech | service requests (read); specimens and diagnostic reports (read and write); lab definitions (read); patients list; encounters and charge items (read); locations; inventory (read) | ordering tests; clinical data; billing writes |
   | Radiologist | as Lab Tech, plus clinical data (read), since radiologists read the clinical context | billing writes; ordering |
   | TPA Desk | patients list; encounters (read); accounts (read and update); charge items, invoices, payments and price definitions (read) | invoice and payment writes; clinical data. The TPA screens themselves are Phase 2 (P2-T15) |
   | MRD | patients list; encounters with clinical data, questionnaire responses, diagnostic reports, specimens and service requests (all read); report templates | every write: the records custodian is read-only. Every view is in the audit trail |
   | Store Keeper | inventory (read); supply deliveries and requests (read and write); goods receipt (external delivery); products and product knowledge (read); locations | pharmacy dispense; patients |
   | Owner | read-only finance and operations: accounts, charge items, invoices, payments, price definitions, inventory, supply, products, users, organisations, encounters (census) | clinical data and questionnaire responses (DPDP purpose limitation); destructive billing; creating users |

3. **Where each role sits** (seeded users; the same for real hospitals, in the admin guide).
   - Receptionist, Cashier, TPA Desk and Owner: the facility's root org.
   - Lab Tech: a "Laboratory" department org linked to the lab locations.
   - Radiologist: a "Radiology" department org.
   - Store Keeper: a "Stores" department org linked to the store locations.
   - MRD: the root org plus the hospital's state government org (for discharged patients).
4. **Seed.**
   - `ihr_seed_demo` adds one synthetic user per role (`care-receptionist` … `care-owner`) in
     each demo hospital, placed as above, with passwords in `deploy/seed/TEST_USERS.md`.
   - `care-billing` moves from Staff to Cashier.
   - The Keycloak seed gets one Keycloak login for each, so the web app can be tried.
5. **Tests** (tenant mode, `ihr_roles` label).
   - After `sync_permissions_roles`, each role's RolePermission set equals `GRANTS` exactly. An
     upstream permission that starts granting to our role fails this test.
   - CARE's own roles' permission sets are unchanged (the shared-list trap).
   - No non-clinical role holds a clinical-data permission. None holds `can_create_user`.
   - Endpoints, signed in with `staff_client` as each role, against a facility with root,
     Laboratory and Stores orgs, locations, a patient, an encounter, a service request and an
     invoice:
     - the facility API's `permissions` equal the expected set;
     - allowed calls answer 200/201;
     - forbidden calls answer 403.
6. **TENANCY_DESIGN §5** said roles come as a "pack" of rows. Amended: they are code-defined
   system roles, synced into every hospital like CARE's own (ADR-017, Proposed).

## Decisions to log (auto-accepted, awaiting the owner)

- **Q-K21:** the eight roles' grants, as in the table.
- **Q-K22:** the Owner is read-only. Discounts, cancellations and locked invoices stay with
  Facility Admin, and so does creating users.
- **Q-K23:** split into P1-T11a (backend) and P1-T11b (web-app menus).
