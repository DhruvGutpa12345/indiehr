# P1-T11c design: web-app menus by permission, and the front-desk, records and Owner screens (2026-11-15)

> **Revised after the attack (2026-11-15): see "After the attack" at the end, which wins over earlier sections.**

**Task.** P1-T11c (Q-K45 A): CARE's web app shows our eight roles only what they can use. The
menus key on permissions; the patient tabs, the encounter history, the patient home's encounter
tab, the edit form and the redirect after "create encounter" work for the front desk; MRD gets a
records-access page and the Owner a totals page. It is an `apps/frontend` patch, built and tested
in CI only (the dev machine never builds the web app).

**Inputs.** docs/QUESTIONS.md Q-K34…Q-K46 (owner-confirmed 15-11; Q-K37a B: the Cashier and TPA
Desk see the bed, being built in the backend now as `ihr_access/fields/encounter_billing.txt`;
Q-K46 A), docs/reviews/p1-t11/design-b.md ("After the attack" §11, "P1-T11b4 as built", "Known
limits"), docs/adr/018 (its "Admin guide" consequence is P1-T11c's), docs/UPSTREAM_PATCHES.md,
docs/UPSTREAM.md, `.github/workflows/apps.yml`, `apps/frontend/tests-ihr/`, `deploy/locale/`.
Code read at upstream pin `57205b897804` (frontend) and this repo at `6451eb9eb`.

**Rules this design keeps.**
- **Hidden is not a control.** The backend already refuses (P1-T11b's swaps, guard, matrix). The
  web app only stops offering dead ends: a menu, tab, button or field is shown when the request
  behind it can succeed for this user.
- **Never show a projected field as if it were empty.** The guard resets dropped fields to their
  empty value (`blood_group: null`, `tags: []`, `extensions: {}`, `organizations: []` …). In the
  demographics view those rows are removed, never shown as "—" or "none".
- **CARE's roles are unchanged.** Doctor, Nurse, Facility Admin, Radiologist (it holds clinical
  slugs) and any user with a clinical or patient-opening slug see CARE's web app exactly as now
  (Q-K35: permissions add up). A Playwright test pins it.
- **Upstream-friendly.** Every edit inside an upstream file is a one-to-five-line hook marked
  `// IndieHR P1-T11c`, calling code of ours under `src/Utils/ihr/` or `src/components/ihr/`.

---

## 1. What the code does today (facts the design rests on)

| # | Fact | Where |
|---|---|---|
| F1 | `PermissionContext.hasPermission(p, objectPerms)` checks only `objectPerms` when given, else the user list; superusers get `true` for everything, **our `ihr_*` slugs included** | `src/context/PermissionContext.tsx:34-42` |
| F2 | The user list (`getcurrentuser.permissions`) holds only roles from **government** organisations (`OrganizationUser`). Our roles live in facility organisations (the placement trigger refuses government ones), so it is empty for them | `apps/backend/care/emr/resources/user/spec.py:192-199` |
| F3 | `facility.permissions` (facility retrieve) is the union of every slug of every role the user holds in that facility's organisations, root and departments: **our `ihr_*` slugs appear there** | `care/emr/resources/permissions.py:29-53` (`FacilityPermissionsMixin`) |
| F4 | `patient.permissions` comes from `find_roles_on_patient`: roles through the patient's **open** encounters' departments and bed, government orgs and `PatientUser`. A returning patient with no open visit here gives a Receptionist `[]`. The guard keeps only `ihr_*` and non-clinical slugs for restricted users | `care/security/authorization/patient.py:16-47`; `ihr_access/guard.py:211-216`, `fields/patient.txt` |
| F5 | `encounter.permissions` comes from `find_roles_on_encounter`: departments and bed only, **no `PatientUser`**. MRD under a grant gets no encounter slug from its hidden role; CARE's `EncounterProvider` reads clinical rights from the patient's list but `canReadPrimaryEncounter` from the encounter's | `care/security/authorization/encounter.py:17-26`; `src/pages/Encounters/utils/EncounterProvider.tsx:137-185` |
| F6 | Facility menu: Overview, Services, Resource, Users, Billing (+3), Settings (+16) are **ungated**; Appointments/Queues need `canViewAppointments`; Patients needs `canWriteAppointment \|\| canListEncounters \|\| canCreateEncounter`; links take `visibility` | `src/components/ui/sidebar/facility/facility-nav.tsx:22-215` |
| F7 | The location menu (beds, laboratory, pharmacy, inventory, schedule, appointments, queues) is **entirely ungated** | `src/components/ui/sidebar/facility/location/location-nav.tsx:19-100` |
| F8 | Patient tabs: `demography`, `resource_requests`, `users`, `notes`, `accounts` always show; `appointments`, `encounters`, `files`, `clinical_history`, `updates` key on CARE slugs of `patient.permissions` | `src/components/Patient/PatientDetailsTab/index.tsx:37-123`; `PatientProfile.tsx:54-62` |
| F9 | The appointments tab is rendered with `facilityId` **undefined**, so `get_appointments` goes without `?facility`, which restricted users must send (403 otherwise) | `PatientProfile.tsx:143-145`; `fields/patient_actions.txt` |
| F10 | Encounter history: `enabled: canViewPatients` (CARE's `can_list_patients`), else a toast "no permission" and a redirect back; "create encounter" keys on `facility.permissions` | `PatientDetailsTab/EncounterHistory.tsx:39-74` |
| F11 | Encounter cards: "View encounter" is `disabled={!(canReadEncounter \|\| canViewPatients)}` on a `Button asChild` around a `Link` (still clickable) and points to `/…/encounter/<id>/updates` | `src/components/Facility/EncounterCard.tsx:37-44,143-153` |
| F12 | Patient home: the encounter tab needs `canListEncounters` from `facility.permissions`; "View accounts" is ungated | `src/pages/Patient/PatientHome.tsx:66-77,218-229`; `home/PatientHomeEncounters.tsx:49-54` |
| F13 | Demography: edit buttons on `canWritePatient` (patient list); the blood group row, tags section and extension sections render whatever the body holds | `PatientDetailsTab/Demography.tsx:37-40,218-225,276-305` |
| F14 | The edit form (`/patient/:id/update`) shows a blood group field and plugin extension fields; it resends `extensions` and `tags` (the backend's edit rule ignores hidden fields) | `src/components/Patient/PatientRegistration.tsx:133-150,218,240-246` |
| F15 | After create, `navigate('/facility/:f/patient/:p/encounter/:id/updates')` unless `disableRedirectOnSuccess`; the tag picker is in the form | `src/components/Encounter/CreateEncounterForm.tsx:148-151,357-366` |
| F16 | Every encounter page goes through one route table entry per prefix: `/${facility\|organization}/:id/patient/:patientId/encounter/:encounterId/:tab` → `EncounterProvider` + `EncounterShow` | `src/Routers/routes/ConsultationRoutes.tsx:121-133` |
| F17 | The route table is one object in `AppRouter.tsx` (`Routes`, merged after plugin routes); a key defined later replaces an earlier value with the same key | `src/Routers/AppRouter.tsx:74-91,104-111` |
| F18 | The facility encounter list sends `organization`, `care_team_user`, `tags`, `tags_behavior` when those filters are set or restored from `sessionStorage` (`encounter_list_filters`): 400 for restricted users | `src/pages/Encounters/EncounterList.tsx:285-372,431-437`; `fields/encounter_filters.txt` |
| F19 | The account page's tabs: invoices, charge items, payments, **reports** (account reports, refused to restricted users, Q-K41), **bed charges** (built from `primary_encounter.location_history`) | `src/pages/Facility/billing/account/AccountShow.tsx:95-101,304-361`; `components/BedChargeItemsTable.tsx:204-223` |
| F20 | The services page needs `can_read_healthcare_service`; the Lab Technician and the Store Keeper lack it, so the only CARE path to their lab or store (Services → service → location) is a 403 | `care/security/authorization/healthcare_service.py:9-17`; `ihr_roles/registry.py:185-221` |
| F21 | CARE's plugin system (`REACT_ENABLED_APPS`, module federation, `PlugConfig` rows) loads manifests from a URL; the in-tree "local plugin" path exists only under `vite serve`, never in a build | `care.config.ts:223-245`; `vite.config.mts:251-257`; `src/PluginEngine.tsx:29-62` |
| F22 | Our unit tests run as `node --import tsx --test src/Utils/ihr/*.test.ts` (pure TypeScript, no `@/` alias, no DOM); upstream `test:unit` runs `plugins/**/*.test.ts` | `apps.yml` "IndieHR unit tests"; `apps/frontend/package.json:44` |
| F23 | `merge-locale.py` accepts only keys that exist in upstream `en.json`; our English `ihr_*` keys (27) were added to upstream `en.json` as logged patches (P1-T4b, P1-T7b) | `scripts/merge-locale.py:41-60`; `UPSTREAM_PATCHES.md` |
| F24 | The records API lists only the caller's live grants with the patient's **id** (no name), `seconds_left`, `expires_at`; POST takes `patient`, `reason`, `reference`; DELETE `…/<id>/` → 204. Totals answer a fixed key set; amounts are two-decimal strings | `ihr_access/endpoints.py:27-110`; `totals.py:253-277` |

F4 and F5 decide the mechanism: the web app must combine the **facility** list (which always
carries our slugs) with the **patient** list (which carries MRD's clinical slugs under a grant).

---

## 2. Mechanism

### 2.1 One pure rule, one hook

`src/Utils/ihr/access.ts` (pure, unit-tested) computes, from raw slug lists (never through
`hasPermission`, F1, so a superuser is never put in our modes and never shown our pages):

```ts
export type IhrMode = "care" | "restricted";
export interface IhrAccess {
  mode: IhrMode;                // "restricted": facility list has no clinical or patient-opening slug
  demographics: boolean;        // ihr_view_patient_demographics (Rec, Cash, TPA, MRD)
  editDemographics: boolean;    // ihr_edit_patient_demographics (Rec)
  encounterStatus: boolean;     // ihr_view_encounter_status (Rec, Cash, TPA, MRD)
  records: boolean;             // ihr_request_records_access (MRD)
  totals: boolean;              // ihr_view_hospital_totals (Owner)
  billingBed: boolean;          // restricted && can_read_charge_item (Cash, TPA; Q-K37a B)
  has: (slug: string) => boolean;
}
export function ihrAccess(facilityPerms: string[], isSuperuser: boolean): IhrAccess;
export function ihrPatientView(a: IhrAccess, patientPerms: string[]): "care" | "demographics" | "none";
```

- `mode = "restricted"` when the user is not a superuser and `facility.permissions` holds none of
  `CLINICAL_SLUGS ∪ PATIENT_OPENING` (`ihr_roles/registry.py:110-133`). This is the backend's
  `state.restricted` seen per facility (Q-K35: a doctor-owner or a Receptionist-Doctor is "care").
- `ihrPatientView`: `"care"` when the mode is care **or** the patient's list holds a clinical or
  patient-opening slug (MRD under a live grant, F4); `"demographics"` when `demographics`;
  `"none"` otherwise (Owner, Lab, Store: no patient screens).
- The slug sets are not typed by hand: `src/Utils/ihr/ihrSlugs.json` (`ours`, `clinical`,
  `patientOpening`, `grants` per role) is checked against `registry.py` and `permissions.py` by a
  new plugin test `ihr_roles/tests/test_frontend_slugs.py` (runs in CI's plugin step; the backend
  is the source, the JSON fails CI when they differ).

`src/Utils/ihr/useIhrAccess.ts` (hook): `useCurrentFacilitySilently()` (already cached by CARE,
5 min) plus `useAuthUser().is_superuser`, memoised `ihrAccess(...)`; `useIhrPatientView(patient)`.
Outside a facility route the mode is "care" (our roles always work inside one).

### 2.2 Menus: wrap, do not rewrite

`src/Utils/ihr/menu.ts` (pure, unit-tested): `ihrFacilityLinks(links, access, baseUrl, t)` and
`ihrLocationLinks(links, access)`. In care mode they return CARE's links plus our two items when
held (Records access, Hospital totals). In restricted mode they set `visibility` on CARE's links
by **URL suffix** (table in §3.2), drop parents whose children are all hidden, point a parent's
`url` at its first visible child, and add our items. A URL suffix CARE renames upstream is
caught by `scripts/check-ihr-frontend.py` (§5.3), so a menu never silently stays open or closed.

### 2.3 Routes: one table wrap

`src/components/ihr/IhrRoutes.tsx` exports `withIhrRoutes(routes: AppRoutes): AppRoutes`, called
once in `AppRouter.tsx` around the `Routes` object (F17). It:
1. adds our pages: `/facility/:facilityId/ihr/records`, `/facility/:facilityId/ihr/totals`,
   `/facility/:facilityId/ihr/places`;
2. wraps CARE's own route functions **by key, calling CARE's function** (no copy of its tree):
   - `/facility/:id/patient/:patientId/encounter/:encounterId/:tab` (F16) →
     `IhrEncounterGate`: when `ihrPatientView` is "demographics" it renders our
     `IhrEncounterStatus`, else CARE's function's element unchanged. This one wrap covers every
     "View encounter" link (encounter cards, encounter list, patient home) **and the redirect
     after create** (F15), which lands on `…/updates`;
   - `/facility/:facilityId/encounters/locations` and `…/locations/:locationId` (the bed board) →
     `IhrNotForRole` in restricted mode (Q-K37: occupants are not shown to them);
3. refuses at build time (a unit test over the exported key list) to wrap a key CARE no longer
   defines.

`IhrNotForRole` is a plain page: "This page is not part of your role" and a link to Overview; it
makes no API call (direct URLs typed by hand land there instead of a broken page).

---

## 3. Who sees what

### 3.1 Per role (alpha seed logins in brackets)

"CARE" = CARE's own gating, unchanged. Each row names the permission it keys on.

| Role (login) | Mode | Facility menu | Patient screens | Encounter link opens | Our pages |
|---|---|---|---|---|---|
| **Receptionist** (`reception.ravi`) | restricted | Overview; Appointments, Queues (CARE: `can_list_booking`/`can_list_schedule`); Patients → Search patients, All / OPD / IPD … encounters (`ihr_view_encounter_status`), *no* Locations; Users; Billing → Accounts (`can_read_account`) | demographics: tabs Demography (edit with `ihr_edit_patient_demographics`), Appointments (`can_list_booking`, sent with `?facility`), Encounters (`ihr_view_encounter_status`), Accounts (`can_read_account`); patient home with its encounter tab; create encounter (`can_create_encounter`, department picker via `can_view_facility_organization`, Q-K42) | `IhrEncounterStatus` (no bed) | none |
| **Cashier** (`cashier.kavya`) | restricted | Overview; Patients → Search patients, encounter lists; Users; Billing → Accounts, Invoices, Payments (`can_read_account`/`_invoice`/`_payment_reconciliation`) | demographics, read-only (no edit slug): Demography, Encounters, Accounts | `IhrEncounterStatus` **with the bed** (Q-K37a B) and the visit's account link | none |
| **TPA Desk** (`tpa.joseph`) | restricted | as Cashier | as Cashier | as Cashier | none |
| **MRD, no grant** (`mrd.farah`) | restricted | Overview; Patients → Search patients, encounter lists; Users; **Records access** | demographics, read-only: Demography, Encounters; **"Ask for records access"** on the patient page and patient home | `IhrEncounterStatus` | Records access |
| **MRD with a grant** | restricted (facility) | as above | **care** for that patient only (patient list carries the hidden role's slugs, F4): CARE's tabs; a banner "Records access: 23:41 left · End now" | CARE's `EncounterShow` (see risk R2) | Records access lists the grant |
| **Owner** (`owner.vikram`) | restricted | Overview; Users; **Hospital totals** | none (patient search hidden; a typed URL shows `IhrNotForRole` or CARE's own 403) | n/a | Hospital totals |
| **Lab Technician** (`lab.imran`) | restricted | Overview; Users; **My places** (Q-K48 A) → location menu: Laboratory → Service requests (`can_read_service_request`), Inventory → Items (`can_read_inventory_item`); no beds, pharmacy, schedule | none (its patient data arrives inside requests, as CARE shows them) | n/a | My places |
| **Store Keeper** (`store.gopal`) | restricted | Overview; Users; **My places** → Inventory → Items, To receive, To dispatch (`can_read_supply_delivery`/`can_write_supply_delivery`), Purchase orders and deliveries (`can_read_supply_request`, `can_write_external_supply_delivery`) | none | n/a | My places |
| **Radiologist** (`radiology.sunita`) | care (holds clinical slugs) | CARE | CARE (the chart only with Radiology on the visit, Q-K44 A; otherwise CARE's own 403 page, a known limit) | CARE | none |
| **Doctor** (`dr.meera`), **Nurse** (`nurse.asha`), **Facility Admin** (beta `admin.bhavesh`) | care | CARE, unchanged | CARE | CARE | Records access / totals only if they also hold our slug |

A person holding a clinical role and one of ours (Q-K35) is in care mode and sees CARE's screens,
plus "Records access" or "Hospital totals" when they hold those slugs (the backend allows it:
`branches.holds`). Superusers never see our items (F1; our endpoints refuse superusers).

### 3.2 Restricted-mode menu map (`menu.ts`)

| CARE item (URL suffix) | Shown in restricted mode when | Why |
|---|---|---|
| `/overview` | always | membership only |
| `/appointments`, `/queues` | CARE's own rule | CARE gates them already |
| `/patients` (parent) | any child visible | |
| ↳ `/patients` (search) | `demographics` | patient search is gated to our roles with this slug (Q-K40, b2) |
| ↳ `/encounters/patients/all`, `/encounters/patients/<class>` | `encounterStatus` | our encounter branch (b2) |
| ↳ `/encounters/locations` | never | bed board shows occupants (Q-K37) |
| `/services` | never | needs `can_read_healthcare_service`; its sub-pages are clinical work lists |
| `/resource` | never | resource requests closed to restricted users (`branches.closed`) |
| `/users` | always | `membership_only.txt` (facility users list) |
| `/billing/account` | `can_read_account` | CARE's account list check |
| `/billing/invoices` | `can_read_invoice` | |
| `/billing/payments` | `can_read_payment_reconciliation` | |
| `/settings/*` (all 16) | never | configuration is the Facility Admin's; the read rights our roles hold feed pickers inside their work screens, not browsing |
| plugin links | as CARE | none configured (`REACT_ENABLED_APPS=""`) |
| **ours** `/ihr/records` | `records` (also in care mode) | `ihr_request_records_access` |
| **ours** `/ihr/totals` | `totals` (also in care mode) | `ihr_view_hospital_totals` |
| **ours** `/ihr/places` | restricted and (`can_read_service_request` or `can_read_inventory_item`) | Lab, Store (F20, Q-K48) |

Location menu (`location-nav.tsx`, restricted mode only): `/beds` never (bed history refused);
`/laboratory`, `/service_requests` with `can_read_service_request`; `/pharmacy`,
`/medication_requests`, `/medication_dispense`, `/medication_return` never (dispense orders
closed, clinical); `/inventory/summary` with `can_read_inventory_item`;
`/inventory/internal/receive/`, `/inventory/internal/dispatch/` with `can_read_supply_delivery`;
`/inventory/external/*` with `can_read_supply_request`; `/schedule`, `/appointments`, `/queues`
as CARE's facility rule (`can_list_schedule`, `can_list_booking`).

The Overview page's "Services" shortcut (`src/pages/Facility/overview.tsx:83-89`, `visible: true`)
follows the `/services` row.

### 3.3 Fields the demographics view never shows (projected away)

| Screen | Removed in "demographics" view | Kept |
|---|---|---|
| Demography (`Demography.tsx`) | blood group row; tags section and its edit; extension (plugin) sections; `PatientDetailsTabDemographyGeneralInfo` plugin slot | identity, contact, address, identifiers on the allow-list, deceased date, created/updated by |
| Edit form (`PatientRegistration.tsx`, update only) | blood group field; plugin extension fields | the edit list (`fields/patient_edit.txt`); **create** keeps blood group (b2 known limit: registration records it) |
| Create encounter (`CreateEncounterForm.tsx`) | tag picker (`tags: []` sent) | status, class, priority, department, start date |
| Encounter cards and list | department, care team, location, tags (each hidden, not "—") | status, class, dates, priority, visit number |
| `IhrEncounterStatus` | everything not in `fields/encounter.txt` keep rows | status, class, period, priority, visit number, status history, booking time; **Cashier/TPA**: current bed and bed stays (`encounter_billing.txt`) and a link to the visit's account |
| Account page (`AccountShow.tsx`) | **Reports** tab (Q-K41); Invoices / Charge items / Payments / Bed charges tabs without their read slug (`can_read_invoice`, `can_read_charge_item`, `can_read_payment_reconciliation`, `can_read_charge_item` + `billingBed`) | the rest |

Playwright asserts the absence of the labels "Blood group", "Tags", "Department", "Care team",
"Location" on the front-desk screens (§6.2), and the presence of the bed name for the Cashier.

---

## 4. Subtasks (about 2–3 hours each; built in CI, reviewed one Opus lens at high per subtask)

| Part | What | Done when |
|---|---|---|
| **P1-T11c1 Menus and the foundation** (3 h) | `ihrSlugs.json` + `ihr_roles/tests/test_frontend_slugs.py`; `access.ts`, `menu.ts`, `useIhrAccess.ts` + unit tests; hooks in `facility-nav.tsx`, `location-nav.tsx`, `overview.tsx`, `AppRouter.tsx`; `IhrRoutes.tsx` (bed-board refusal, `IhrNotForRole`, empty shells for records/totals routes), `IhrMyPlaces.tsx`; our English strings mechanism (Q-K47 A: `deploy/locale/en.json` + `merge-locale.py`); `scripts/check-ihr-frontend.py` + test + pre-commit hook; `tests-ihr/roles-menus.spec.ts`; UPSTREAM_PATCHES rows | Apps CI green (typecheck, `tsc -p tests-ihr`, lint, unit, merge-locale, build, egress, Playwright). `roles-menus.spec.ts` signs in as each of the 11 logins of §3.1 and the sidebar's link names equal its expected list exactly; for CARE's three roles the list equals the one recorded **before** the patch (captured in c1's first CI run from the unpatched bundle and committed as `tests-ihr/menus-care.json`). Plugin test passes in tenant mode |
| **P1-T11c2 Front-desk and billing-desk screens** (3 h) | hooks in `PatientProfile.tsx` (tabs, appointments `facilityId`, actions slot), `Demography.tsx`, `PatientRegistration.tsx`, `EncounterHistory.tsx`, `EncounterCard.tsx`, `PatientHome.tsx`, `CreateEncounterForm.tsx`, `EncounterList.tsx`, `AccountShow.tsx`; `IhrEncounterStatus.tsx`, `IhrEncounterGate`; `src/Utils/ihr/frontDesk.ts` (tab, section and filter rules) + unit tests; `tests-ihr/front-desk.spec.ts`, `billing-desk.spec.ts`; seed check (an inpatient with a bed stay and an account in alpha; added to `ihr_seed_demo` if missing) | Needs Q-K37a B committed. Apps CI green. As `reception.ravi`: search by phone → patient → tabs exactly Demography, Appointments, Encounters, Accounts; edit phone → saved (200) and shown; create encounter → lands on the status page; no `/api/` answer 400/403/5xx on the path (dead-end listener, §6.2). As `cashier.kavya`: the inpatient's account → Bed charges lists the bed by name; no Reports tab |
| **P1-T11c3 Records access and Owner totals** (2.5 h) | `IhrRecordsAccess.tsx`, `IhrPatientActions.tsx` (button + live-grant banner), `src/Utils/ihr/records.ts` + tests; `IhrOwnerTotals.tsx`, `src/Utils/ihr/totals.ts` + `money.ts` `formatInrCompact` + tests; `src/Utils/ihr/ihrApi.ts`; Hindi drafts + review sheet; the admin guide `docs/ROLES_GUIDE.md` (ADR-018 consequence); `tests-ihr/records.spec.ts`, `owner-totals.spec.ts`; ADR-018 "Not yet usable day to day" updated | Apps CI green. As `mrd.farah`: patient → "Ask for records access", insurer without reference → field error, no request sent; with `TEST-1` → the clinical tabs appear and a clinical request answers 200; "End now" → the tabs are gone, the grants list is empty, and the page's DOM holds no text from the clinical tab (cache purged). As `owner.vikram`: totals for this month show ₹ in lakh/crore with exact figures on hover; 400-day range → message, no request; patient search absent from the menu |

c1 lands first (c2 and c3 use its hook and routes); c2 and c3 are independent of each other.

---

## 5. The patch

### 5.1 Upstream files changed (each logged in `UPSTREAM_PATCHES.md`, one row per file)

| File | Hook (marked `// IndieHR P1-T11c`) | Part |
|---|---|---|
| `src/Routers/AppRouter.tsx` | `const Routes: AppRoutes = withIhrRoutes({ … })` (one call around the existing object, one import) | c1 |
| `src/components/ui/sidebar/facility/facility-nav.tsx` | in `FacilityNav`: `links={ihrFacilityLinks(generateFacilityLinks(...), ihr, baseUrl, t)}` + `const ihr = useIhrAccess()` | c1 |
| `src/components/ui/sidebar/facility/location/location-nav.tsx` | `links={ihrLocationLinks([...], ihr)}` | c1 |
| `src/pages/Facility/overview.tsx` | Services shortcut `visible: ihrShows(ihr, "/services")` | c1 |
| `src/components/Patient/PatientProfile.tsx` | tabs: `ihrPatientTabs(getPatientTabs, view, ihr)`; appointments tab gets `facilityId` when view is "demographics" (F9); `<IhrPatientActions patient facilityId />` beside `PatientHomeActions` | c2 (actions: c3) |
| `src/components/Patient/PatientDetailsTab/Demography.tsx` | `canWritePatient \|\| (view==="demographics" && ihr.editDemographics)` for the identity/contact/address sections only; sections passed through `ihrDemographySections(...)` (§3.3) | c2 |
| `src/components/Patient/PatientRegistration.tsx` | on update in demographics view: hide blood group and plugin extension fields (two conditions) | c2 |
| `src/components/Patient/PatientDetailsTab/EncounterHistory.tsx` | `const canListHere = canViewPatients \|\| (view==="demographics" && ihr.encounterStatus)` replaces `canViewPatients` in `enabled` and the redirect effect | c2 |
| `src/components/Facility/EncounterCard.tsx` | `canAccess` also true for `ihr.encounterStatus`; tag block already hidden on `[]` | c2 |
| `src/pages/Patient/PatientHome.tsx` | `canListEncounters \|\| ihr.encounterStatus` passed to the tabs; "View accounts" only with `can_read_account` in restricted mode; `<IhrPatientActions>` beside `PatientHomeQuickActions` | c2 (actions: c3) |
| `src/components/Encounter/CreateEncounterForm.tsx` | tag picker rendered only in care mode | c2 |
| `src/pages/Encounters/EncounterList.tsx` | `filters` through `ihrEncounterFilters(filters, ihr)` (drops department, care team, tags) and the query params through `ihrEncounterParams(params, ihr)` (drops `organization`, `care_team_user`, `tags`, `tags_behavior` even when restored from `sessionStorage`, F18) | c2 |
| `src/pages/Facility/billing/account/AccountShow.tsx` | `const tabs = ihrAccountTabs({ … }, ihr)` (§3.3) | c2 |

13 upstream files, each a few lines. No edit to `public/locale/en.json` from now on (Q-K47 A),
none to `ConsultationRoutes.tsx` (wrapped by key, §2.3), none to `PermissionContext.tsx`.

### 5.2 Files of ours (new; one `UPSTREAM_PATCHES.md` row per part listing them, as P1-T4b did)

| File | What |
|---|---|
| `src/Utils/ihr/ihrSlugs.json` | slug sets mirrored from the backend (checked by the plugin test) |
| `src/Utils/ihr/access.ts`, `access.test.ts` | §2.1 rules; tests per role grant set from the JSON (each of §3.1's rows) |
| `src/Utils/ihr/useIhrAccess.ts` | the hook |
| `src/Utils/ihr/menu.ts`, `menu.test.ts` | §3.2 map; tests: each role's visible URL suffixes, care-mode identity (CARE's links returned unchanged, deep-equal), superuser |
| `src/Utils/ihr/frontDesk.ts`, `frontDesk.test.ts` | tab, demography-section, encounter-filter/param and account-tab rules |
| `src/Utils/ihr/ihrApi.ts` | route objects in CARE's `query()` shape: `records.list` GET, `records.create` POST, `records.end` DELETE `/api/ihr/records-access/{id}/`, `totals.get` GET `/api/ihr/owner/totals/` |
| `src/Utils/ihr/records.ts`, `records.test.ts` | reason codes, the reference rule (same regex as `records.REFERENCE_RE`, required for `legal`, `insurer`), countdown text |
| `src/Utils/ihr/totals.ts`, `totals.test.ts` | range rules (DD-MM-YYYY, from ≤ to, ≤ 366 days, ≥ 01-01-2000), default range, query building |
| `src/Utils/ihr/money.ts` (ours, extended), `money.test.ts` | `formatInrCompact(value, labels)` (§7) |
| `src/components/ihr/IhrRoutes.tsx` | `withIhrRoutes`, `IhrEncounterGate`, `IhrNotForRole` |
| `src/components/ihr/IhrEncounterStatus.tsx` | §3.3 |
| `src/components/ihr/IhrMyPlaces.tsx` | `GET /api/v1/facility/{f}/location/?mine=true` (CARE's own filter, `location.py:170`), links to `/facility/:f/locations/:id` |
| `src/components/ihr/IhrPatientActions.tsx` | records button and live-grant banner (§6) |
| `src/components/ihr/IhrRecordsAccess.tsx`, `IhrOwnerTotals.tsx` | the two pages |
| `tests-ihr/roles-menus.spec.ts`, `front-desk.spec.ts`, `billing-desk.spec.ts`, `records.spec.ts`, `owner-totals.spec.ts`, `lib/deadEnds.ts`, `menus-care.json` | §8 |
| `scripts/check-ihr-frontend.py`, `scripts/test_check_ihr_frontend.py` | §5.3 |
| `plugins/ihr_platform/ihr_roles/tests/test_frontend_slugs.py` | JSON vs registry |
| `deploy/locale/en.json` (Q-K47 A) | our English `ihr_*` strings |

Pure modules import nothing through `@/` and nothing from React, so the node test runner (F22)
runs them; components hold no rule of their own.

### 5.3 Surviving upstream syncs

- `scripts/check-ihr-frontend.py` (stdlib; pre-commit hook and the Apps CI frontend job): for
  every anchor our code names it checks the upstream source still has it: each URL suffix of
  `menu.ts` in `facility-nav.tsx` / `location-nav.tsx`, each wrapped route key of
  `IhrRoutes.tsx` in `ConsultationRoutes.tsx` / `PatientRoutes.tsx`, each of the 13 hooks
  (`IndieHR P1-T11c` marker present in each file of §5.1). A miss fails with the file and the
  anchor. Precedent: `scripts/check-date-formats.py`, `check-storage-keys.py`.
- The menu map fails toward CARE: an unknown upstream menu item is shown (a dead end at worst,
  never a leak; the backend refuses). `roles-menus.spec.ts` then fails on the extra name, which is
  the review signal.
- `docs/UPSTREAM.md` "After every sync", item 3, gains: "new or renamed menu items, patient
  tabs, account tabs or encounter-list filters → classify them in `src/Utils/ihr/menu.ts` /
  `frontDesk.ts` (`check-ihr-frontend.py` and `roles-menus.spec.ts` fail until you do)".
- If CARE later builds in-tree plugins into the bundle (F21), the hooks can move into a plugin
  manifest (`navItems`, `routes`, `overrides`); not now: today it needs a second build, hosting
  and a `PlugConfig` row per hospital.

---

## 6. Records-access page (`/facility/:facilityId/ihr/records`, c3)

Shown with `records` (MRD). Three parts, all through `ihrApi.ts`:

1. **Ask.** Patient: preset from `?patient=<id>` (the patient page's button), else a phone search
   with CARE's `patientApi.search` (the call `PatientIndex` makes; allowed for MRD, projected).
   The chosen patient is shown by name, year of birth and UHID only. Reason: a select of the six
   codes of Q-K36 (`patient_request`, `legal`, `insurer`, `internal_audit`, `nabh_review`,
   `continuity_of_care`), labels from our strings. Reference: shown and required for `legal` and
   `insurer`, checked client-side with the backend's rule (letters, digits, space, `. / _ : -`,
   ≤ 100), optional otherwise. A short notice above the button: "You will see this patient's
   whole record, read only, for 30 minutes. Every page you open is recorded." The duration
   comes from the answer (`expires_at`), never hard-coded in the count-down. Submit → `POST`;
   201 → navigate to the patient page; 400 (a live grant exists, bad reference) → the server's
   message beside the field; 403 → "Your role cannot ask for records access here".
2. **My live grants.** `GET` on mount, on focus and every 30 s; one row per grant: patient name
   (one `patientApi.get` per row, the list is small), reason, reference, started (India date and
   time), time left (`mm:ss`, counted down locally from `seconds_left`), **Open**, **End now**
   (confirm dialog → `DELETE` → 204 → refetch). Empty state: "No live records access".
3. **When a grant ends** (End now, count-down at zero, or a 403 on a granted patient's page):
   `queryClient.clear()` (CARE's query keys for clinical data vary by page; clearing is the only
   way to be sure no clinical answer stays on screen), then navigate to this page with a toast
   "Records access ended". The same handler runs from `IhrPatientActions`' banner.

`IhrPatientActions` (patient page and patient home, c3): MRD without a live grant on this patient
→ "Ask for records access" (link to the page with `?patient=`); with one → banner "Records
access · 23:41 left · End now". Nothing for other roles.

Out of scope: ending another clerk's grant (a hospital-wide admin may, through the API; the
audit viewer screens are a separate open item), and grant history (the audit trail has it).

## 7. Owner totals page (`/facility/:facilityId/ihr/totals`, c3)

Shown with `totals`. The facility is the route's (an Owner of two facilities switches with
CARE's facility switcher; the API is per facility, b4 known limit).

- **Controls:** From and To (DD-MM-YYYY, our India date boxes), "By day / By month"; default this
  month in IST (first day to today) by day. Client checks mirror `totals.parse_range` (≤ 366
  days, from ≤ to, not before 01-01-2000); the server stays the authority and its 400 messages
  are shown by field. State is kept in the URL (`?from=&to=&by=`), not in storage.
- **Request:** `GET /api/ihr/owner/totals/?facility=<id>&from=DD-MM-YYYY&to=DD-MM-YYYY&by=day|month`.
- **Tiles:** visits (sum of `encounters`), admissions, discharges, inpatients now, beds occupied
  of total, invoiced, **collected**, refunded, outstanding now.
- **Money:** `formatInrCompact`: ≥ ₹1 crore → "₹1.23 crore", ≥ ₹1 lakh → "₹4.56 lakh", else
  `formatInr` ("₹98,765.00"); two decimals, half-up (decimal.js, as `formatInr`); the words come
  from our strings (`ihr_amount_lakh`, `ihr_amount_crore`, Hindi "लाख", "करोड़"). Tiles show the
  compact form with the exact `formatInr` figure as `title` and in an "Exact figures" table;
  tables always show exact figures with Indian grouping.
- **Tables:** visits by class and status; bills by status (count, amount); collected by method;
  the series (period, admissions, discharges, invoiced, collected).
- **Help text (Q-K46 A):** "Collected is money received: completed payments and advances.
  Adjustments (write-offs, corrections) are not collected money, but they do lower Outstanding
  now. Amounts include tax. Days are India time (IST). Outstanding now is today's balance, not
  the balance at the end of the range."
- No chart library call is needed for P1; recharts is already in the bundle if the owner asks
  for charts later. No patient, bill or id is ever shown (the API returns none).

---

## 8. Strings and Hindi

- **Where English lives (Q-K47 A).** New file `deploy/locale/en.json` with our `ihr_*` keys
  only. `merge-locale.py` merges it into `apps/frontend/public/locale/en.json` at build (CI
  already runs the script before every build, both jobs) and refuses a key upstream also has
  or a key without the `ihr_` prefix; `--check` validates it (pre-commit). `hi.json` keys are
  then checked against upstream English **plus** ours. The 27 existing `ihr_*` keys move there in
  c1, and their two `UPSTREAM_PATCHES.md` rows are marked "moved to deploy/locale/en.json"
  (upstream `en.json` returns to upstream's text).
- **Keys** follow the existing style: `ihr_menu_records_access`, `ihr_menu_hospital_totals`,
  `ihr_menu_my_places`, `ihr_not_for_role`, `ihr_encounter_status_title`,
  `ihr_records_reason__<code>`, `ihr_records_reference_required`, `ihr_records_notice`,
  `ihr_records_time_left`, `ihr_records_end_now`, `ihr_records_ended`, `ihr_totals_*`,
  `ihr_amount_lakh`, `ihr_amount_crore` (about 60 strings in all).
- **Hindi** drafts go in `deploy/locale/hi.json` and a new sheet `docs/i18n/review/hi-batch-5.csv`
  (🟡, README table row added). Clinical column **yes** for the reason labels that name care
  ("continuity of care", "NABH review") and the encounter-status words if any are new; the
  owner reviews them (CLAUDE.md "clinical content"). Terms follow `docs/i18n/hi-glossary.md`.

---

## 9. Tests in CI, and the click-throughs

### 9.1 What CI runs (Apps CI, `apps.yml`)

| Job / step | What it proves for P1-T11c |
|---|---|
| Frontend: `npm run typecheck`, `npx tsc -p tests-ihr/tsconfig.json`, `npm run lint -- --quiet` | the hooks and our components type-check against CARE's types; specs compile |
| Frontend: "IndieHR unit tests" (`node --import tsx --test src/Utils/ihr/*.test.ts`) | `access`, `menu`, `frontDesk`, `records`, `totals`, `money` rules (pure) |
| Frontend: `python3 scripts/merge-locale.py` (+ `--check` in pre-commit) | our English and Hindi keys valid, placeholders match |
| Frontend: build, then `scripts/check-bundle-egress.sh` | no new external host (only same-origin `/api/ihr/…` and `/api/v1/…`) |
| Frontend: new step `python3 scripts/check-ihr-frontend.py` (+ its unittest) | anchors of §5.3 |
| Plugin suites (tenant mode): `ihr_roles.tests.test_frontend_slugs` | the JSON mirrors the registry |
| **Browser isolation (Playwright)**: dev stack, `seed`, `keycloak up`, `keycloak seed` (all eight role logins plus `dr.meera`, `nurse.asha`, beta `admin.bhavesh`), bundle served on alpha/beta | the role specs below |

Playwright specs (own config, one worker, no retries; every sign-in consumes a fresh TOTP step,
so the 11 role sign-ins add about 6–8 minutes; each spec signs a role in once and reuses the
page):
- `lib/deadEnds.ts`: a response listener that fails the test on any `/api/` answer 400, 403, 404
  or 5xx not declared by the step (the declared ones: the bad-reference POST is never sent; the
  MRD 403 after a grant ends is expected once).
- `roles-menus.spec.ts` (c1): §3.1 menu lists per role, exact; CARE's three roles equal
  `menus-care.json`; the location menu for `lab.imran` and `store.gopal` after "My places";
  typed URLs `/facility/<f>/encounters/locations` and `/facility/<f>/ihr/totals` as
  `reception.ravi` → `IhrNotForRole`, no API call.
- `front-desk.spec.ts`, `billing-desk.spec.ts` (c2), `records.spec.ts`, `owner-totals.spec.ts`
  (c3): the done-criteria of §4, with the dead-end listener on, plus label-absence checks (§3.3).
- Locale `en` and `Asia/Kolkata` as the config already sets; dates asserted as DD-MM-YYYY.

### 9.2 Claude's own click-through (dev stack, after CI is green)

1. `gh workflow run "Frontend bundle" -f api_url=`; download the artifact into
   `deploy/dev/frontend-build` (docs/LOCAL_DEV.md "Get a fresh web app bundle"); never `npm`.
2. Stop any running test containers first (RAM: about 600 MB stack + 540 MB Keycloak);
   `scripts/care-dev.sh up`, `keycloak up`, `frontend`.
3. In the Browser pane at http://alpha.localhost:8080, sign in as each of `reception.ravi`,
   `cashier.kavya`, `mrd.farah`, `owner.vikram`, `lab.imran` (codes:
   `scripts/care-dev.sh keycloak otp <login>`), walk §4's done-criteria by hand, and screenshot
   each role's sidebar and the two pages into the run file's evidence (no codes or passwords on
   screen).

### 9.3 The owner's click-through (written into TRACKER and the run file at c3)

One-click URL http://alpha.localhost:8080; logins `reception.ravi`, `cashier.kavya`,
`mrd.farah`, `owner.vikram` (passwords and keys only in the git-ignored
`deploy/seed/TEST_USERS.md`); steps: (1) Ravi searches a patient by phone, edits the phone,
creates an OPD visit and lands on its status page; (2) Kavya opens an inpatient's account and
sees the bed under Bed charges; (3) Farah asks for records access (insurer, `TEST-1`), reads the
record, presses End now; (4) Vikram opens Hospital totals for this month and reads the lakh/crore
figures.

---

## 10. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | **The patient-level permission list cannot drive the front desk** (F4: empty for a returning patient with no open visit) and the facility list alone would hide MRD's granted record (F5) | the two-list rule of §2.1 (`ihrPatientView`); unit tests for both cases; `records.spec.ts` opens a granted patient that has no open visit |
| R2 | **MRD under a grant may still meet CARE's encounter page gated on `encounter.permissions`** (F5: no hidden-role slugs there), e.g. `canReadPrimaryEncounter` false | c3 checks it in Playwright first; if CARE's page hides the visit, `IhrEncounterGate` passes the patient's clinical slugs into the encounter's list for display only (a UI union; the backend's grant authorizer already answers `can_view_encounter_obj`), logged as a hook |
| R3 | Menu map by URL suffix drifts on upstream sync | `check-ihr-frontend.py`, `roles-menus.spec.ts`, UPSTREAM.md step (§5.3) |
| R4 | A CARE role with no clinical slug (e.g. CARE's Staff or Volunteer, if they hold none) falls into restricted mode and loses ungated menus | intended: those menus answer 403 for them too (Q-K49 A); `menus-care.json` pins the CARE roles we seed |
| R5 | CI Playwright time and TOTP: 11 more sign-ins | one sign-in per role per spec file; specs ordered so one role's checks share a page |
| R6 | c2 depends on Q-K37a B being committed and on the alpha seed having an inpatient with a bed stay and an account | c2 starts after the Q-K37a B commit; c2 adds the seed rows if missing (synthetic only) |
| R7 | `queryClient.clear()` on a grant's end also drops non-clinical caches (the facility, the user) | they refetch on the next page; acceptable for a 30-minute grant |
| R8 | The Radiologist without Radiology on the visit opens a patient from search and meets CARE's 403 page (Q-K44 A) | known limit, logged; not a leak |

## 11. Open decisions (to log as Q-K47 onwards; each auto-accepted at ⭐ in an unattended run)

**Q-K47 — Where do our English strings live?**
- **A ⭐** `deploy/locale/en.json` (ours, `ihr_*` keys only), merged into the web app at build by
  `merge-locale.py`; upstream `en.json` stays upstream's; the 27 existing keys move there.
- B Keep adding `ihr_*` keys to upstream `en.json` as logged patches (P1-T4b, P1-T7b).
*Recommendation A:* about 60 new keys would otherwise be a patch in a file every sync touches;
CI already merges before every build.

**Q-K48 — How do the Lab Technician and Store Keeper reach their lab or store?**
- **A ⭐** A "My places" menu item of ours listing the places they belong to (CARE's
  `location/?mine=true`), opening CARE's location pages. No access change.
- B Give both roles `can_read_healthcare_service` so CARE's Services menu works (a registry
  change; matrix rows and key snapshots change).
*Recommendation A:* it changes no permission and keeps P1-T11b's matrix as reviewed.

**Q-K49 — Who gets the stricter menus?**
- **A ⭐** Anyone whose permissions in the facility include no clinical or patient-opening slug
  (our restricted roles, and any CARE role in the same position): only menus whose first request
  they may make are shown.
- B Only holders of an `ihr_*` slug (the Lab Technician and Store Keeper, who hold none, keep
  CARE's ungated menus and their 403s).
*Recommendation A:* it matches the backend's own test for "restricted" and removes dead ends for
every non-clinical user; CARE's clinical roles are unchanged either way.

**Q-K50 — What does the front desk see when it opens a visit, and after creating one?**
- **A ⭐** A status page of ours (status, class, dates, priority, visit number, status history;
  for the Cashier and TPA Desk also the bed and the account link) for every visit link and after
  "create encounter".
- B Return to the patient's encounter tab after create, and hide "View encounter" for them.
*Recommendation A:* one route wrap covers every link and the redirect, and the billing desks need
the bed and the account from the visit.

## 12. Not in this task

- CARE-role access gaps (Q-K40) → P1-T11d. Account reports for billing roles → P2-T15 (Q-K41).
- Ward name for visitor enquiries (Q-K37 B) → P2-T2. Clinical breakdowns in totals → P2-T16.
- Screens for invitations, recovery and the audit viewer (TRACKER "still open"); ending another
  clerk's grant from the web app.
- The shortcut command dialog (`ShortcutCommandDialog`) lists shortcuts by CARE's own checks;
  any it offers a restricted user land on `IhrNotForRole` or CARE's 403. Revisit if the
  click-through shows one.

---

## After the attack (2026-11-15)

Sixteen findings from two attack seats (SS = state and security, FF = feasibility), each checked
against the code at `6451eb9eb` (frontend pin `57205b897804`). **This section wins over every
earlier section where they differ.** SS2 and FF3 are the same defect and are fixed once.

### 13.1 Verdicts

| Id | Sev | Verdict | Checked against | What changed |
|---|---|---|---|---|
| SS1 | high | confirmed | `queryClient.ts:19-25` (`retry: false`, `refetchOnWindowFocus: false`); `errorHandler.ts:66` (a 403 is only a toast); `endpoints.py:69-82` (a hospital-wide admin may DELETE another's grant); §6 put the end handler in two places only | One app-level `IhrGrantWatcher` (§13.3); the banner and the records page read its state; a Playwright case for an admin-ended grant |
| SS2 | medium | confirmed (= FF3) | `patient.py:211-213,262-264` (a phone search is always `partial`); `spec.py:232-244` (`id = uuid4()`, `partial_id` only, no year of birth, no identifiers); `patient.py:278-289` (`search_retrieve` needs phone + year of birth + `partial_id`); `records.py:206-208` ("No such patient.") | The records page has no patient search (Q-K51 A, §13.4) |
| SS3 | medium | confirmed | `state.py:240-262` (`restricted` = one of `RESTRICTED` held and no `CLINICAL_SLUGS` through any membership; `PATIENT_OPENING` not counted); `placement.py:17-18`; `guard.py:236-241` | The mode is the backend's decision, published by a new `GET /api/ihr/my-access/` (§13.2); `ihrSlugs.json` only shows items; Q-K49 revised |
| SS4 | low | confirmed | §2.3 ("else CARE's function's element unchanged"); §3.1 Owner row ("`IhrNotForRole` or CARE's own 403") | The gate renders a loader while pending, `IhrNotForRole` for "none", CARE only for "care"; patient routes wrapped the same way (§13.2) |
| SS5 | low | confirmed | `endpoints.py:27-37` (`grant_body`: patient id only); `records.py:13-17` (a request that shows a granted patient is audited `purpose: records`); `guard.py:138-142` (a granted patient is not projected) | `grant_body` gains `patient_name` and `patient_year_of_birth` (the query already `select_related("patient")`); no per-row `patientApi.get` |
| SS6 | low | confirmed | `totals.py:16-17` (encounters by class and **every** status), `:220-229` (`invoiced` = `BILLED` by `issue_date`; `invoices_by_status` = all statuses by `Coalesce(issue_date, created_date)`); §7 named no source and no error state | Tile sources fixed in `totals.ts` (§13.5); skeleton or error, never 0 |
| SS7 | low | confirmed | `records.py:35-37,68-69` (`IHR_RECORDS_GRANT_MINUTES`); §6.1 notice text "30 minutes" | `grant_minutes` in the GET body; `ihr_records_notice` takes `{{minutes}}` |
| FF1 | high | confirmed | `PatientRoutes.tsx:47-58` (only `/facility/:facilityId/encounters/:tab` and `…/encounters/locations/:locationId`); raviger `useMatchRoute`: `Object.entries` in insertion order, `getMatchParams` takes the first `find` | Wrap `/facility/:facilityId/encounters/:tab` (refuse only when `tab === "locations"`) and `…/locations/:locationId` |
| FF2 | high | confirmed | `care-tenancy.sh:27` and `deploy/ci/tenancy.override.yaml` mount only `apps/backend` and `plugins`; `registry.py:24-25` imports CARE and Django | Authoritative copy in `plugins/…/ihr_roles/frontend_slugs.json` (command `ihr_frontend_slugs --write`, plugin test); host-side byte compare in `check-ihr-frontend.py` |
| FF3 | high | confirmed (= SS2) | as SS2; `PatientIndex.tsx:127-136,165-170` (search, then CARE's verify step) | as SS2 |
| FF4 | medium | confirmed | `apps.yml:613-621` (artifacts only on failure, only `*.png` and `failure.json`); `tests-ihr/tsconfig.json` has no `resolveJsonModule`; no local build allowed | No captured list: hand-derived CARE-role lists in the spec plus the care-mode deep-equal unit test (Q-K53 A); JSON read with `readFileSync` |
| FF5 | medium | confirmed | `useCurrentFacility.tsx:30-43` (`facility` undefined while loading) | Third mode `"pending"` (§13.2) |
| FF6 | medium | confirmed | `ConsultationRoutes.tsx:121-122` (template key in a `reduce`); `facility-nav.tsx:84` (template URL); F22 (no `@/` in the node runner) | Runtime assertion in `withIhrRoutes` + Playwright console check; regex anchors in `check-ihr-frontend.py` |
| FF7 | medium | confirmed | `LocationLayout.tsx:46-47` (routes start at `/beds`), `:436` (`"*"` → `ErrorPage`) | "My places" links to the first child the role may open (§13.5) |
| FF8 | low | confirmed | `facility-nav.tsx:199` (Templates is `${baseUrl}/template`) | `/template` is its own "never" row |
| FF9 | low | confirmed | `merge-locale.py:45-50` (`not an English key`), `:99` (English loaded once from upstream) | New subtask c0 for the locale move (§13.6) |

**Counts:** 16 findings, 16 confirmed (SS2 and FF3 one defect), 0 partly, 0 refuted.

### 13.2 Mechanism, corrected (replaces §2.1's mode rule, §2.3 steps 2-3, §3.2 additions)

- **Mode comes from the backend (SS3).** New `GET /api/ihr/my-access/?facility=<uuid>` in
  `ihr_access/endpoints.py` (signed-in staff; superuser → `restricted: false`):
  `{"restricted": state.restricted(user), "bills_here": current.bills_at(facility)}`. A
  malformed facility → 400, an unknown one → `bills_here: false`. It is a new route, so it gets
  a `role_matrix.txt` row and `list_params.txt` line (the matrix fails until it does).
  `IhrAccess.mode` keys on `restricted`; `billingBed` on `bills_here`. The JSON slug sets now
  only decide which items to show inside a mode, never the mode. Parity test
  (`ihr_access.tests.test_my_access`): for every login of the role world, plus a Receptionist
  holding an extra custom role with `can_list_patients` only, plus a Doctor-in-A/Receptionist-in-B
  user, `restricted` equals `state.restricted` and the Playwright menus match. Consequence: CARE
  roles without clinical slugs (Staff, Volunteer) are **not** restricted and keep CARE's menus
  (Q-K49 revised; R4 withdrawn).
- **Three modes (FF5).** `IhrMode = "pending" | "care" | "restricted"`; "pending" while the
  facility or `my-access` is loading (and, for `ihrPatientView`, while the patient is). Pure
  rules return "pending"; `menu.ts` returns CARE's links unchanged while pending (no flash of
  our items either); the gates render CARE's `Loading` and mount nothing else. Unit tests for
  every rule's pending case.
- **Gates (SS4).** `IhrEncounterGate` and a new `IhrPatientGate` (wrapping
  `/facility/:facilityId/patient/:patientId/*` keys in `PatientRoutes.tsx`): pending → loader;
  "none" → `IhrNotForRole`; "demographics" → ours (encounter) or CARE's element (patient pages,
  with c2's hooks); "care" → CARE's element. Playwright: `lab.imran` and `owner.vikram` open a
  typed encounter URL → `IhrNotForRole`, no `/api/v1/` call after the facility and
  `my-access`.
- **Bed board (FF1).** Wrap `/facility/:facilityId/encounters/:tab`: restricted and
  `tab === "locations"` → `IhrNotForRole`, else CARE's element. Also wrap
  `/facility/:facilityId/encounters/locations/:locationId`. No key is added.
- **Key checks (FF6).** §2.3 step 3's unit test is dropped. `withIhrRoutes` throws on a missing
  key when `import.meta.env.DEV`, else `console.error("ihr-routes: missing key …")`; every
  Playwright spec fails on that console line; `roles-menus.spec.ts` opens the app once as a
  smoke step. `check-ihr-frontend.py` anchors are regexes where upstream builds the text:
  ``/\$\{identifier\}/:id/patient/:patientId/encounter/:encounterId/:tab`` in
  `ConsultationRoutes.tsx`, ``/encounters/patients/\$\{encounterClass\}`` in `facility-nav.tsx`;
  each pattern has a positive and a negative case in `test_check_ihr_frontend.py`.
- **Slug JSON (FF2).** `plugins/ihr_platform/ihr_roles/frontend_slugs.json` is written by
  `manage.py ihr_frontend_slugs --write` and checked by `ihr_roles.tests.test_frontend_slugs`
  (in the container); `check-ihr-frontend.py` (host, stdlib, pre-commit and Apps CI) byte-compares
  it with `apps/frontend/src/Utils/ihr/ihrSlugs.json`.
- **Menu map (FF8).** §3.2 gains a row: `/template` → never (configuration). `/settings/*` is 15
  items. `menu.test.ts` asserts both.

### 13.3 Grant end, one watcher (replaces §6.3 and the banner's own timer, SS1)

`src/components/ihr/IhrGrantWatcher.tsx`, mounted once by `withIhrRoutes`' facility wrapper for
users with `records`: (a) `GET /api/ihr/records-access/` on mount, every 30 s, on `focus` and
`visibilitychange`; (b) a timer at the earliest `expires_at`; (c) subscribes to
`queryClient.getQueryCache()` and `getMutationCache()`: any 403 while a grant is held triggers an
immediate refetch (it does not try to decide which patient the 403 was about); (d)
`BroadcastChannel("ihr-grants")` (fallback: a `storage` event) tells other tabs. A grant that
disappears or expires → `queryClient.clear()`, toast "Records access ended", navigate to the
records page. The banner and the grants list read time left from the watcher's state (pure
`records.ts` countdown), never their own timer. Playwright (`records.spec.ts`): MRD on an
encounter page under a grant; the grant ended through the admin `DELETE` (beta admin's API
call); within 35 s the page holds none of the clinical marker text and Back does not show it.

### 13.4 Records page, corrected (replaces §6.1-6.2)

- **Patient (Q-K51 A, SS2/FF3).** Only from `?patient=<id>`, set by `IhrPatientActions` on the
  patient page or patient home, which MRD reaches through CARE's own search → year-of-birth
  verify (`search_retrieve`, allowed and projected for MRD: `post_reads.txt:15`). Without
  `?patient`: a link "Search patients". The page shows only the name and year of birth from the
  patient body CARE already loaded for that page. `records.test.ts`: the POST id is never taken
  from a `partial: true` result (the module refuses one).
- **List (SS5).** `grant_body` gains `patient_name`, `patient_year_of_birth`; no per-row patient
  GET. `test_records` asserts the list request's audit entry carries no `grants` (it reads no
  record).
- **Length (SS7).** The GET body gains `grant_minutes` (`records.grant_minutes()`); the notice is
  `ihr_records_notice` with `{{minutes}}`; a unit test feeds 45 and checks the text.

### 13.5 Totals and My places, corrected

- **Tiles (SS6, Q-K52 A).** `totals.ts`: invoiced = sum of `series[].invoiced`; visits = the
  `encounters` counts excluding `cancelled` and `entered_in_error`. The "Bills by status" table
  is captioned "all bills, drafts and cancelled included, dated by issue or creation". Loading →
  skeleton; 400 → the field message; 403 or 5xx → an error line; a missing value is never
  formatted (no "₹0.00" stand-in). Unit tests for both sums and for the missing-value case.
- **My places (FF7).** Each place links to `/facility/:f/locations/:id/service_requests` with
  `can_read_service_request`, else `/inventory/summary` with `can_read_inventory_item`; the
  choice is `menu.ts`'s location rule (one function, so link and menu agree). Unit tests for the
  Lab Technician and the Store Keeper.

### 13.6 Subtasks, final (replaces §4)

| Part | Done when |
|---|---|
| **c0 Locale move** (1 h) | `deploy/locale/en.json` holds the 27 `ihr_*` keys; `merge-locale.py` takes it as input, refuses overlap and non-`ihr_` keys, checks Hindi against merged English; upstream `en.json` reverted; two UPSTREAM_PATCHES rows marked moved; `python3 scripts/merge-locale.py --check` and its unittest pass locally; Apps CI green |
| **c1 Menus and foundation** (3 h) | `my-access` endpoint + parity and matrix rows pass in tenant mode; slug JSON command, plugin test and host compare pass; `access`/`menu` unit tests (pending, care identity deep-equal, superuser, `/template`) pass; gates and route wraps with the runtime key check; `roles-menus.spec.ts` exact lists for all 11 logins (CARE roles hand-derived, Q-K53 A), typed-URL refusals, no `ihr-routes` console error; Apps CI green |
| **c2 Front-desk and billing-desk** (3 h) | As §4 c2, plus: no CARE clinical request fires while pending; Apps CI green |
| **c3 Records and totals** (3 h) | `grant_body`/`grant_minutes` backend change with `test_records` green; watcher spec (admin-ended grant cleared within 35 s, Back clean); records page only from `?patient`; totals tiles per §13.5 with error states; §4 c3 checks; Apps CI green |

c0 → c1 → (c2, c3 independent).

### 13.7 Open decisions, final (replaces §11; ⭐ = auto-accepted in an unattended run)

- **Q-K47** English strings in `deploy/locale/en.json` — **A ⭐** (now its own subtask c0).
- **Q-K48** Lab and Store reach their places through "My places" — **A ⭐** (links to the first
  child they may open).
- **Q-K49 (revised)** Who gets stricter menus: **A' ⭐** exactly the backend's `state.restricted`
  (holders of our restricted roles with no clinical slug anywhere), published by `my-access`;
  CARE roles without clinical slugs keep CARE's menus. B (old A: anyone without clinical or
  patient-opening slugs in this facility) is withdrawn: it disagrees with the backend (SS3).
- **Q-K50** Front desk opens visits on our status page — **A ⭐**.
- **Q-K51 (new)** How MRD picks a patient: **A ⭐** only from the patient page (CARE's own
  verify flow); B CARE's two steps (phone, then year of birth and `search_retrieve`) inside our
  page. A: no second copy of the knowledge check.
- **Q-K52 (new)** "Visits" tile: **A ⭐** excludes cancelled and entered-in-error; B all
  statuses, labelled "visits recorded, all statuses".
- **Q-K53 (new)** Pinning CARE's three roles' menus: **A ⭐** hand-derived lists in the spec plus
  the care-mode deep-equal unit test; B a recording-mode commit before the hooks and an artifact
  download (one extra CI round and commit).

### 13.8 After the c1 review (2026-11-16; wins over §13.2 where they differ)

- **Every facility patient and encounter route is gated by key** (`src/Utils/ihr/routeKeys.ts`,
  pure, `routeKeys.test.ts`). F16 was incomplete: raviger matches CARE's
  `…/encounter/:encounterId/questionnaire`, `report/:reportId`, `consents/:consentId`,
  `questionnaire_response/:id` and the print pages before (or instead of) the `:tab` key. Every
  key under `/facility/:facilityId/patient/:patientId/encounter/:encounterId/` now goes through
  `IhrPatientGate` with the demographics view refused: pending → loader, "none" and
  "demographics" → `IhrNotForRole`, "care" → CARE's element. The `:tab` key keeps the status page.
- **The demographics view opens an allow-list only** (`DEMOGRAPHICS_KEYS`): patient search and
  home, create, the patient page, its Demography, Appointments, Encounters and Accounts tabs, the
  edit form and an appointment. Every other `/facility/:facilityId/patient/…` key (history,
  diagnostic reports, prescriptions, questionnaires, consultation, other tabs, and any key
  upstream adds) shows `IhrNotForRole` to it: a new upstream route fails closed. Replaces §13.2's
  "demographics → CARE's element (patient pages)".
- **Encounter lists** (`/encounters/patients/{all,:encounterClass}`) are gated like their menu
  items: restricted without `ihr_view_encounter_status` → `IhrNotForRole`.
- **A failed my-access fails closed.** A cached answer wins over a failed refetch
  (`myAccessFrom`); with nothing cached the mode stays "pending" (never "care", never guessed
  from slugs, Q-K49 A'), the query retries 3 times and then every 15 s.
- **Pending shows only Overview and Users** (and no location item, no Services shortcut, none
  of ours), so CARE's full menu never flashes for a restricted role and a failed my-access never
  opens it. Reverses §13.2's "CARE's links unchanged while pending": CARE's roles see the full list
  one my-access round trip later. *Decision taken in an unattended run: awaiting owner
  confirmation.*
- **Anchors.** `check-ihr-frontend.py` also requires every `${baseUrl}/…` url of the two nav files
  to be classified in `menu.ts`, pins facility-nav's `baseUrl` (ihrMenu.tsx rebuilds it), and
  checks the new route keys in their own tables. The `ihr-routes` console watcher moved into the
  shared `newContext` fixture, so every Playwright spec fails on it.
- **Known limits (R9, not a leak; the backend refuses, as R8):** routes outside a facility
  (`/patient/:id…`, `/organization/…/patient/…`, the `/organization/:id/…/encounter/…/:tab`
  variant) are not gated: with no facility in the address the mode is "care" and CARE's own
  page meets the backend's refusal (CARE's fallback links reach `/patient/:id` only without a
  facility in context).
