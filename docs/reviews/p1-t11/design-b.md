# P1-T11b design: the eight Indian roles and the demographics-and-billing view (2026-11-14)

Design only; no code yet. It builds on ADR-017 (accepted), the earlier design and its attack
(design.md, review.md in this folder) and the owner's answer to Q-K21 (docs/QUESTIONS.md:185):

- Receptionist, Cashier, TPA Desk and Owner get a demographics-and-billing view: patient
  identity, contact and encounter status. Never notes, diagnoses, reports, discharge advice,
  prescriptions or bed occupants. Every clinical endpoint stays closed to them, with a test per
  endpoint.
- MRD and Radiologist get clinical read. MRD gets it only through an audited path.
- The Owner sees totals only (census counts, revenue). No identified patient or billing list
  until MIS (P2-T16). The Owner approves nothing: discounts, cancellations and locked invoices
  stay with Facility Admin.

Paths are relative to `apps/backend/care/` unless they start with `plugins/`, `docs/` or
`scripts/`. **No `apps/backend` edit is needed** (the web-app screens are P1-T11c's; see the last
section).

> **This design was attacked on 2026-11-14.** The section "After the attack (2026-11-14): the
> design as it is built" at the end changes the split, the guard, the swaps, the MRD path, the
> matrix and the tests. Where it and §1–§12 disagree, it wins. §13 is updated in place.

## 1. Split

P1-T11b is too big for one sitting. Three parts, in this order:

| Part | What | Size |
|---|---|---|
| **P1-T11b1** Roles, grants and the guard | Our five permissions; the eight roles and one hidden role with exact grants; the placement trigger; the serializer guard and its field lists; seeded users and Keycloak logins; grant tests and field-list tests | 2–3 h |
| **P1-T11b2** Swaps and the role × endpoint matrix | Four route swaps (patient, encounter, bed history, resource requests) with the widening branches and gates; the role × endpoint crawl, the clinical canary, batch cases, billing-denial tests; mutation rows | 2–3 h |
| **P1-T11b3** MRD records access and Owner totals | Records grants (table, endpoint, trigger, expiry, audit context, log alerts); the Owner totals endpoint; ADR-018; the click-through | 2–3 h |

Between b1 and b2 the front-desk roles exist in the seed but cannot open a patient. That is
acceptable in dev, and nothing ships between parts.

## 2. Why the mechanism is what it is

Four facts from CARE's code decide it:

1. **Opening a patient by id needs clinical rights.** `PatientViewSet.get_queryset`
   (`emr/api/viewsets/patient.py:93-118`) serves one patient only when `can_view_clinical_data`
   passes (l.105-108). Otherwise it falls back to `get_filtered_patients`, which counts only
   geographic `OrganizationUser` memberships and `users_cache`
   (`security/authorization/patient.py:114-128`).
2. **"Read encounter" is clinical.** `EncounterSpecBase` carries `hospitalization` and
   `discharge_summary_advice` (`emr/resources/encounter/spec.py:66,69`). `EncounterListSpec`
   (l.111) and `EncounterRetrieveSpec` (l.148) both have them, and the retrieve spec adds
   `care_team`, `location_history` and `extensions`. `can_view_encounter_obj`
   (`security/authorization/encounter.py:36-47`) alone also opens prescriptions
   (`emr/api/viewsets/medication_request_prescription.py:100-109`) and the discharge-summary and
   encounter reports (`emr/reports/authorizers/encounter.py:8-12`).
3. **Encounters and patients are embedded in other resources.** Examples:
   - an account's `primary_encounter` and `patient` (`emr/resources/account/spec.py:110-117`);
   - a bed's `current_encounter` (`emr/resources/location/spec.py:157-166`);
   - bed history (`location/spec.py:244`);
   - tokens (`scheduling/token/spec.py:121`) and slots (`scheduling/slot/spec.py:174`);
   - a payment's `account.patient` (`payment_reconciliation/spec.py:148-150`).

   `PatientBaseSpec` includes `blood_group` (`emr/resources/patient/spec.py:70`).
4. **CARE's authorizer override is all-or-nothing by method name.** `AuthorizationController`
   (`security/authorization/base.py:72-117`) maps each `can_*` name to the last registered class.
   It builds the map lazily on the first call (l.98-99), so a later registration is silently
   ignored. An override of `can_view_clinical_data` cannot tell which of its 15+ call sites is
   asking, so widening it opens notes, conditions, prescriptions and reports along with the
   patient.

**Decision: fail closed.**
- The non-clinical roles hold **none** of CARE's patient or encounter read permissions.
- Their access comes from **permissions of ours** that CARE's checks never honour.
- **Route swaps** (the P1-T20a mechanism) add a narrow branch that honours them.
- A **serializer guard** projects every patient, encounter and bed shape these users can see,
  wherever CARE embeds it.

Result: every CARE endpoint we did not swap stays closed to these roles, including any endpoint
upstream adds later.

Alternatives rejected:

| Option | Why not |
|---|---|
| Override CARE authorizers (`register_override_controller`) | Cannot see the call site; widening one opens 15+ clinical paths; a renamed method upstream silently drops a narrowing wrapper |
| Patch `apps/` so `can_list_patients` opens a patient | A logged patch that conflicts on every sync, and it still leaves the embedded encounter shapes open |
| Separate `/api/ihr/frontdesk/…` endpoints | Safest, but CARE's web app calls `/api/v1/patient/<id>/` and the encounter APIs; it would need a large `apps/frontend` patch. **Kept as the fallback** if the guard proves fragile |

## 3. Permissions of ours

These are registered as a CARE `PermissionHandler` with
`PermissionController.register_permission_handler` (`security/permissions/base.py`, the
`register_permission_handler` classmethod). The code lives in
`plugins/ihr_platform/ihr_roles/permissions.py`.

| Slug | Honoured by | Meaning |
|---|---|---|
| `ihr_view_patient_demographics` | swapped PatientViewSet (retrieve, search), swapped EncounterViewSet (`?patient`) | open a patient's demographic record |
| `ihr_edit_patient_demographics` | swapped PatientViewSet (update, partial_update, update_identifier) | edit identity, contact and address only |
| `ihr_view_encounter_status` | swapped EncounterViewSet (list, retrieve) | see that an encounter exists, and its status, class, period and priority |
| `ihr_view_hospital_totals` | `/api/ihr/owner/totals/` | aggregate census and revenue |
| `ihr_request_records_access` | `/api/ihr/records-access/` | ask for a time-boxed clinical read of one patient (MRD) |

**Permission cache trap.** `PermissionController.cache` is built lazily, once (`build_cache`,
`get_permissions`, `get_enum`). The handler is registered in `IhrRolesConfig.ready()` before
`registry.install()`, and a cache built earlier is cleared there.
- New system check `ihr_roles.E021`: every `ihr_*` slug is in `get_permissions()` and has a
  `Permission` row after the sync.
- `registry._permission()` already searches the override handlers
  (`plugins/ihr_platform/ihr_roles/registry.py:29-40`), so `grant()` works for our slugs too.

## 4. Roles, placement and grants

Role names go into `registry.PERMANENT_NAMES`, in this order: `Receptionist`, `Cashier`,
`Lab Technician`, `Radiologist`, `TPA Desk`, `MRD`, `Store Keeper`, `Owner`,
`MRD Records Access`.
- They are permanent identifiers (ADR-017 §2).
- None clashes with CARE's names (`security/roles/role.py:22-74`).
- "Lab Technician" is spelled in full because the name shows in role pickers.

**Placement** (seeded users, and the admin guide for real hospitals):

| Role | Org | Why |
|---|---|---|
| Receptionist, Cashier, TPA Desk, Owner, MRD | facility root org | front office is facility-wide |
| Lab Technician | "Laboratory" department org, linked to the lab locations | lab queues are per location org |
| Radiologist | "Radiology" department org, linked to the imaging locations | clinical read only where Radiology is involved |
| Store Keeper | "Stores" department org, linked to the store locations | |
| MRD Records Access | **never in any org**; only on a time-boxed `PatientUser` row (§7) | |

MRD no longer gets the state government org of the earlier design (design.md:69). A geo
membership holding `can_list_patients` would list every patient in the state.

**Grants** (by slug; "ours" are the `ihr_*` slugs of §3):

| Role | CARE permissions | Ours | Deliberately not |
|---|---|---|---|
| **Receptionist** | `can_create_patient`, `can_create_encounter`, `can_list_schedule`, `can_list_booking`, `can_write_booking`, `can_reschedule_booking`, `can_list_token`, `can_write_token`, `can_list_token_category`, `can_read_patient_identifier_config`, `can_read_healthcare_service`, `can_read_charge_item_definition`, `can_create_account`, `can_read_account`, `can_read_tag_config` | `ihr_view_patient_demographics`, `ihr_edit_patient_demographics`, `ihr_view_encounter_status` | `can_write_patient` (it also gates allergy create, patient notes, patient files, meta artifacts and the patient-summary report through `can_write_patient_obj`, `security/authorization/patient.py:58`); `can_list_patients`; `can_read_encounter`, `can_list_encounter`, `can_write_encounter`; every clinical slug |
| **Cashier** | `can_read_account`, `can_create_account`, `can_update_account`, `can_read_charge_item`, `can_create_charge_item`, `can_update_charge_item`, `can_set_charge_item_definition`, `can_read_charge_item_definition`, `can_read_healthcare_service`, `can_read_invoice`, `can_write_invoice`, `can_read_payment_reconciliation`, `can_write_payment_reconciliation` | `ihr_view_patient_demographics`, `ihr_view_encounter_status` | `can_create_negative_charge_item`, `can_cancel_charge_item`, `can_destroy_invoice`, `can_manage_locked_invoice`, `can_destroy_payment_reconciliation` (Facility Admin keeps them, Q-K21) |
| **TPA Desk** | `can_read_account`, `can_update_account`, `can_read_charge_item`, `can_read_invoice`, `can_read_payment_reconciliation`, `can_read_charge_item_definition` | `ihr_view_patient_demographics`, `ihr_view_encounter_status` | invoice and payment writes (the TPA screens are P2-T15) |
| **Owner** | none | `ihr_view_hospital_totals` | every CARE slug that lists a patient or a bill: `can_read_account`, `can_read_invoice`, `can_read_charge_item`, `can_read_payment_reconciliation`, `can_list_encounter`, `can_list_token`, `can_list_booking`, `can_list_facility_locations`; all approvals |
| **Lab Technician** | `can_read_service_request`, `can_read_specimen`, `can_write_specimen`, `can_read_diagnostic_report`, `can_write_diagnostic_report`, `can_read_specimen_definition`, `can_read_activity_definition`, `can_read_observation_definition`, `can_read_inventory_item`, `can_list_facility_locations` | none | `can_write_service_request` (ordering); clinical data; billing |
| **Radiologist** | `can_list_patients`, `can_view_clinical_data`, `can_view_questionnaire_responses`, `can_list_encounter`, `can_read_encounter`, `can_read_encounter_clinical_data`, `can_read_service_request`, `can_read_diagnostic_report`, `can_write_diagnostic_report`, `can_read_activity_definition`, `can_read_observation_definition`, `can_list_facility_locations` | none | clinical writes other than reports; ordering; billing |
| **MRD** | none | `ihr_view_patient_demographics`, `ihr_view_encounter_status`, `ihr_request_records_access` | any clinical slug held standing |
| **Store Keeper** | `can_read_inventory_item`, `can_read_supply_delivery`, `can_write_supply_delivery`, `can_write_external_supply_delivery`, `can_read_supply_request`, `can_write_supply_request`, `can_read_product`, `can_read_product_knowledge`, `can_list_facility_locations` | none | `is_pharmacist`, `read_medication_dispense`, `write_medication_dispense`; anything patient |
| **MRD Records Access** (hidden) | `can_list_patients`, `can_view_clinical_data`, `can_view_questionnaire_responses` | none | every write |

Notes:
- Facility retrieve and the facility users list need only membership
  (`emr/api/viewsets/facility.py:87-102, 213-218`). The Owner needs no CARE slug to sign in and
  see the hospital.
- None of the eight roles gets `can_create_user`, which our code treats as admin (design.md:31).
- The Radiologist reads clinical context only for encounters where the Radiology org or a
  Radiology location is involved. That is CARE's org model (`find_roles_on_patient`,
  `security/authorization/patient.py:16-47`). Report requests reach them by location.
- **"Clinical slugs"**, a set defined once in `ihr_roles/registry.py` and used by the tests and
  the guard: `can_view_clinical_data`, `can_read_encounter_clinical_data`,
  `can_write_encounter_clinical_data`, `can_view_questionnaire_responses`,
  `can_submit_patient_questionnaire`, `can_submit_encounter_questionnaire`, `is_pharmacist`,
  `read_medication_dispense`, `write_medication_dispense`.

**Restricted roles** (`registry.RESTRICTED`): Receptionist, Cashier, TPA Desk, Owner, Lab
Technician, Store Keeper and MRD. A user is *restricted* when:
- they hold at least one restricted role in this hospital;
- they hold no clinical slug through any facility org or org membership.

Permissions add up across roles (Q-K35). A doctor who is also the Owner is treated as a doctor.

## 5. The override, piece by piece

### 5.1 Placement trigger (database)

`ihr_role_placement_guard` is a trigger function kept current by post_migrate, like
`db_functions.py`. It sits on `emr_facilityorganizationuser`, `emr_organizationuser` and
`emr_patientuser`, and refuses:
- `MRD Records Access` in any org membership;
- any of our eight roles in an `OrganizationUser` (geo or role orgs);
- a `PatientUser` with `MRD Records Access` unless a live `ihr_records_grant` row exists for the
  same user and patient (§7).

It is a trigger, not a signal, because CARE also writes these rows through `get_or_create`,
bulk paths and fixtures.

### 5.2 Serializer guard (`ihr_access/guard.py`)

- **Where it is installed.** In `IhrAccessConfig.ready()` it wraps the `serialize` classmethod
  (`emr/resources/base.py:51-74`) of `EncounterListSpec` (and through inheritance
  `EncounterRetrieveSpec`), `PatientListSpec`, `PatientRetrieveSpec` and
  `FacilityLocationListSpec` (and through inheritance `FacilityLocationRetrieveSpec`). Each
  wrapper carries an `ihr_guard` marker.
- **What it does.** It calls CARE's `serialize`. If the current request's user is restricted, it
  resets every field not marked `keep` to the field's default. Keys stay present, so the web
  app's types still hold. A nested key such as `hospitalization.admit_source` can be kept on its
  own.
- **Exemption.** A patient under one of the user's live records grants (MRD, §7) is not
  projected, and neither are that patient's encounters.
- **When the flag is set.** It is computed lazily, once per request, from a ContextVar. That
  ContextVar is filled in the same `APIView.perform_authentication` hook that binds the audit
  actor (`plugins/ihr_platform/ihr_audit/apps.py:41-46`). Outside a request (Celery, commands)
  the guard is off. No restricted user acts there.
- **Forced projection.** Our widening branches (§5.3) set a per-request flag that forces the
  projection whatever the user's other roles are. Example: a Receptionist who is also a
  Radiologist and opens a patient through the Receptionist branch still gets the projection.
- **Check.** System check `ihr_access.E022`: the classes that
  `care.emr.resources.encounter.spec.EncounterListSpec`, the patient specs and the location
  specs resolve to all carry the marker.
- **Build risk.** Setting a classmethod on a pydantic v2 model class must be proven in the first
  hour of b1. If it fails, wrap `perform_extra_serialization` instead. If that fails too, use
  the separate-endpoint fallback (§2).

The field lists are `ihr_access/fields/{patient,encounter,location}.txt`. Each line is
`field keep|drop  # reason`. Starting content:

| Shape | Keep | Drop (reset) |
|---|---|---|
| Patient | `id`, `name`, `gender`, `phone_number`, `emergency_phone_number`, `address`, `permanent_address`, `pincode`, `date_of_birth`, `year_of_birth`, `age`, `geo_organization`, `instance_identifiers`, `facility_identifiers`, `deceased_datetime`, `created_date`, `modified_date`, `created_by`, `updated_by`, `version` | `blood_group`, `extensions`, `instance_tags`, `facility_tags` (tags can carry clinical labels; registration tags such as MLC come back in P2-T2), anything new |
| Encounter | `id`, `status`, `status_history`, `encounter_class`, `class_history`, `period`, `priority`, `external_identifier`, `patient` (projected), `facility`, `appointment`, `hospitalization.admit_source`, `hospitalization.re_admission`, `created_date`, `modified_date`, `created_by`, `updated_by`, `permissions`, `version` | `discharge_summary_advice`, `hospitalization.discharge_disposition`, `hospitalization.diet_preference`, `care_team`, `organizations` (department names reveal the specialty), `current_location` and `location_history` (Q-K37), `extensions`, `tags`, anything new |
| Location | every field except the one dropped | `current_encounter` (bed occupant) |

### 5.3 Route swaps (added to `route_swaps.SWAPS`, `plugins/ihr_platform/ihr_platform/route_swaps.py:29-49`)

Each swap is a subclass of CARE's class (the `issubclass` check is at l.73-76) and is guarded by
`ihr_platform.E020`. "Only our restricted roles" means that every role the user holds in this
hospital is in `RESTRICTED`.

1. **`PatientViewSet` → `ihr_access.views.IhrPatientViewSet`**
   - **Retrieve (and the detail actions that call `get_object`).** CARE's queryset is tried
     first. If it does not contain the patient and the user holds
     `ihr_view_patient_demographics` in any facility org of this hospital, the patient is
     served, with forced projection. Scope: the whole hospital register (Q-K34).
   - **List.** Unchanged. Our roles get CARE's geo-based list, which is empty for them.
     Patients are found by search.
   - **`search` and `search_retrieve`** (l.204-264, 278-290). Today these need **no
     permission** for a phone search or a config with no facility. Our gate: refuse with 403
     when the user holds one of our roles and none of `can_list_patients`, `can_create_patient`
     or `ihr_view_patient_demographics`. That closes them for Owner, Lab Technician and Store
     Keeper. Users of CARE's roles only are unchanged. Results go through the guard.
   - **`update`, `partial_update`, `update_identifier`** (l.403). For a user without CARE's
     `can_write_patient_obj` on the patient, allowed with `ihr_edit_patient_demographics`. The
     body is limited to the patient keep-list. `blood_group`, `extensions` and tags are refused
     with 400.
   - **`add_user`** (l.308-317). Today any patient writer can attach any user with any role. A
     Doctor could make a Receptionist a "Doctor" on a patient and so give them that patient's
     clinical record. Ours refuses when the target user holds only our restricted roles. The
     hidden role is refused by the trigger. Other cases stay as in CARE (Q-K40).
   - **`get_users`, `add_user`, `delete_user`** are never reachable through our branch.
2. **`EncounterViewSet` → `IhrEncounterViewSet`**
   - **List** (`get_queryset`, `emr/api/viewsets/encounter.py:231-274`):
     - With `?patient`: CARE's `can_view_patient_obj` (l.255) is tried first. Otherwise
       `ihr_view_patient_demographics` plus `ihr_view_encounter_status` serve that patient's
       encounters in facilities where the user holds the slug.
     - With `?facility`: CARE's `get_filtered_encounters` is tried first. If the user holds
       `ihr_view_encounter_status` there, the queryset is the same filter on our slug
       (`security/authorization/encounter.py:181-198`).
     - Rows from our branch are forced-projected.
   - **Retrieve** (`authorize_retrieve`, l.153-163). CARE's check is tried first. Otherwise our
     slug in a facility org of the encounter's facility allows it, with forced projection.
   - **Create.** CARE's `can_create_encounter` is unchanged. For a restricted user, a body that
     sets `discharge_summary_advice`, `hospitalization.discharge_disposition` or
     `diet_preference` is refused with 400. The response goes through the guard.
   - **Update and actions** stay CARE's (`can_write_encounter`), which no restricted role holds.
     The Receptionist cannot change an encounter's status in P1-T11b. That is P2-T2.
3. **`FacilityLocationEncounterViewSet` → `IhrLocationEncounterViewSet`**
   (`emr/api/viewsets/location.py:286`). Bed history answers 403 to users who hold only our
   restricted roles: each row embeds a full `EncounterRetrieveSpec` (`location/spec.py:244`).
4. **`ResourceRequestViewSet` and `ResourceRequestCommentViewSet` → ours**
   (`emr/api/viewsets/resource_request.py:38, 98`). Today they have no permission check beyond
   org membership (`build_queryset`, l.47-78) and they show `related_patient`. Ours answer 403
   to users who hold only our restricted roles.

**Not swapped, because our roles cannot reach them** (they need `can_read_encounter`,
`can_write_patient` or clinical slugs, which no restricted role holds):
- prescription retrieve (`medication_request_prescription.py:100-109`);
- the encounter and discharge-summary report read (`emr/reports/authorizers/encounter.py:8-12`);
- allergy create, patient notes, patient file writes, meta-artifact writes.

These are CARE-role gaps. They are logged in Q-K40 and checked by the matrix (§9) for our roles.

### 5.4 Batch

Batch sub-requests resolve through the same URL configuration
(`plugins/ihr_platform/ihr_platform/route_swaps.py:3-8`). They therefore reach our swapped
classes and the guard. `batch_allowlist.txt` is unchanged.

## 6. Per-endpoint matrix

Key:
- **A** allowed as CARE does it.
- **D** demographics only (allowed, projected).
- **E** allowed, returns empty.
- **F** forbidden (403/404; for a list, 403 or an empty list).
- **–** not applicable.

"MRD+g" is MRD with a live records grant on that patient (read only).

| Endpoint (under `/api/v1/` unless noted) | Rec | Cash | TPA | Own | Lab | Rad | MRD | MRD+g | Store |
|---|---|---|---|---|---|---|---|---|---|
| `patient/` list | E | E | E | E | E | A | E | A (granted only) | E |
| `patient/{id}/` retrieve | D | D | D | F | F | A | D | A | F |
| `patient/` create | A (D response) | F | F | F | F | F | F | F | F |
| `patient/{id}/` update, `update_identifier` | D (keep-list fields) | F | F | F | F | F | F | F | F |
| `patient/search`, `search_retrieve` | D | D | D | F | F | A | D | A | F |
| `patient/{id}/get_users`, `add_user`, `delete_user` | F | F | F | F | F | F | F | F | F |
| `patient/{id}/get_appointments`, `get_tokens` | D | D | D | F | F | A | D | A | F |
| `encounter/?facility=`, `?patient=` | D | D | D | F | F | A (Radiology scope) | D | A | F |
| `encounter/{id}/` retrieve | D | D | D | F | F | A | D | A | F |
| `encounter/` create | A (no clinical fields; D response) | F | F | F | F | F | F | F | F |
| `encounter/{id}/` update and actions | F | F | F | F | F | F | F | F | F |
| observation, symptom, diagnosis, allergy (GET, POST), medication request, statement, administration, prescription (list, retrieve), dispense, questionnaire response, notes (thread, message), consent, files (patient, encounter), meta artifact | F | F | F | F | F | A (read) | F | A (read; writes F) | F |
| `service_request`, `specimen`, `diagnostic_report` | F | F | F | F | A (lab location orgs) | A (SR, DR; specimen F) | F | A (read) | F |
| report templates: `discharge_summary`, `encounter_report` read; generate | F | F | F | F | F | A read, generate F | F | A read, generate F | F |
| `facility/{id}/location/` list, retrieve | F | F | F | F | D (no occupant) | A | F | F | D (no occupant) |
| location encounters (bed history) | F | F | F | F | F | A | F | F | F |
| `account/` list, retrieve | A (D embeds) | A (D embeds) | A (D embeds) | F | F | F | F | F | F |
| `account/` create, update | A create | A | A update | F | F | F | F | F | F |
| `charge_item/` read; create, update | F | A; A | A; F | F | F | F | F | F | F |
| charge item negative, cancel | F | F | F | F | F | F | F | F | F |
| `invoice/` read; write | F | A; A | A; F | F | F | F | F | F | F |
| invoice cancel, lock, unlock, destroy | F | F | F | F | F | F | F | F | F |
| `payment_reconciliation/` read; write | F | A; A | A; F | F | F | F | F | F | F |
| payment cancel, destroy | F | F | F | F | F | F | F | F | F |
| schedules, bookings, tokens | A (D embeds) | F | F | F | F | F | F | F | F |
| resource requests and comments | F | F | F | F | F | A | F | F | F |
| inventory, supply delivery, supply request, product | F | F | F | F | A (inventory read) | F | F | F | A |
| facility retrieve, facility users list, own organisations | A | A | A | A | A | A | A | A | A |
| `/api/ihr/owner/totals/` | F | F | F | A | F | F | F | F | F |
| `/api/ihr/records-access/` | F | F | F | F | F | F | A | A | F |
| batch: each sub-request | as its own row | | | | | | | | |

Doctor and Facility Admin are crawled too. Their expected answers are CARE's own, except
`add_user` with a target user who holds only restricted roles.

## 7. MRD audited path

The MRD role holds no clinical permission. Clinical read is one patient at a time, for a stated
reason, for a limited time.

**Flow**
1. `POST /api/ihr/records-access/` with `{patient, reason, reference}`. The user needs
   `ihr_request_records_access` in a facility org.
   - Reason codes: `patient_request`, `legal`, `insurer`, `internal_audit`, `nabh_review`,
     `continuity_of_care`.
   - `reference` is required for `legal` and `insurer`.
2. In one transaction:
   - insert `ihr_records_grant` (patient, user, reason, reference, `expires_at = now + 30 min`,
     `ended_at`) in the new tenant app `ihr_access`;
   - create `PatientUser(user, patient, role=MRD Records Access)`. CARE's `PatientUser.save`
     re-saves the patient, which rebuilds `users_cache` (`emr/models/patient.py:99-106,
     148-158`).

   The trigger (§5.1) refuses the `PatientUser` without the grant row.
3. How access then works: `find_roles_on_patient` counts `PatientUser`
   (`security/authorization/patient.py:43-47`). So every check that starts with
   `can_view_clinical_data` passes for that patient only: encounter sub-resources
   (`encounter_authz_base.py:56-58`), files, reports. Encounter list and retrieve pass through
   `can_view_patient_obj` (`can_list_patients`). Writes stay closed: the hidden role holds no
   write slug, and clinical writes check facility orgs, not `PatientUser`.
4. **Ending a grant.** `DELETE /api/ihr/records-access/<id>/` ends it early. Expired grants are
   ended in two places:
   - by a beat job every minute;
   - at the start of every request by an MRD user, in the authentication hook. That is one
     indexed query, so expiry does not wait for the beat.

   Ending a grant removes the `PatientUser` and re-saves the patient, so `users_cache` is
   rebuilt.
5. **Audit.**
   - The grant and `PatientUser` row changes are in the trail (ADR-010 triggers).
   - Every request by a user with a live grant has `purpose: "records"` and `grants: [ids]`
     added to its request context, after authentication. The middleware builds the context
     before the view (`plugins/ihr_platform/ihr_audit/middleware.py:39-42`,
     `context.py:156-172`). A small `context.annotate_request()` helper sets the extra keys.
   - The audit viewer can then join every patient shown to its reason.
6. **Logs and alerts** (`deploy/logs/alerts/records.yml`, in the style of the other log
   alerts):
   - log line `ihr-records: hospital=<slug> grant=<id> reason=<code> outcome=granted|ended|expired|refused`;
   - `MrdRecordsRefused`: 3 or more refusals in 10 minutes;
   - `MrdRecordsVolume`: more than 40 grants a day by one user;
   - `MrdRecordsOffHours`: a grant between 22:00 and 07:00 IST (info).

   Thresholds are in Q-K36. A redaction test case comes first (ADR-013): the log line carries
   no patient id beyond the grant id.

The Radiologist needs no special path: ordinary clinical read through the Radiology org, with
every view in the audit trail as for any clinician.

## 8. Owner totals

`GET /api/ihr/owner/totals/?facility=<id>&from=DD-MM-YYYY&to=DD-MM-YYYY`
- **Access.** `ihr_view_hospital_totals` in a facility org of that facility. Range at most 366
  days; IST days.
- **Returns aggregates only, through `aggregate()` and `values(...).annotate(Count|Sum)`:**
  - encounters by class and status (period start in range);
  - admissions and discharges per day (inpatient class);
  - current inpatients;
  - beds: total and occupied (count of bed locations with a current encounter);
  - invoiced, collected and outstanding amounts in INR per day or month (invoice totals by
    status; payments by method).

  Amounts are strings with two decimals. Lakh and crore formatting is the screen's job.
- **Never** returns ids, names or rows, and never breaks totals down by department, service,
  doctor or diagnosis (a breakdown is MIS, P2-T16).
- **Approvals.** The Owner holds none of the approval slugs (§4).
- Small numbers are not suppressed (Q-K39).

## 9. Tests

Everything runs as `ihr_app` (`IHR_DB_ROLES=test`). World-building happens inside
`ihr_platform.testing.as_owner()` / `owner_for_test(self)`. Sign-in uses
`ihr_identity.testing.fake_keycloak()` / `staff_client()`.

Labels: add `ihr_access` to the tenant test labels and the CI plugin step.

Command:
`IHR_DB_ROLES=test scripts/care-tenancy.sh test --settings=ihr_platform.settings.test ihr_roles ihr_access`

**b1 (`ihr_roles`, `ihr_access`)**
1. **Exact grants.** After `sync_permissions_roles`, each of the nine roles' RolePermission set
   equals `GRANTS` exactly. A CARE permission that newly lists one of our roles fails the test.
2. **CARE roles unchanged.** Before `install()`, `registry` records each permission's role list
   (`ORIGINAL[slug]`). For every permission, the current list minus our roles equals
   `ORIGINAL`, and each CARE role's RolePermission set equals the code's.
3. **Grant guards.**
   - No restricted role, and not MRD, holds a clinical slug, `can_list_patients`,
     `can_read_encounter`, `can_list_encounter`, `can_write_encounter` or `can_write_patient`.
   - The Owner holds exactly `ihr_view_hospital_totals`.
   - None of the nine holds `can_create_user`.
4. **Permission registration.** Our five slugs are in `get_permissions()` and exist as rows after
   the sync (E021).
5. **Placement trigger.** It refuses:
   - the hidden role in a facility org;
   - any of our roles in an `OrganizationUser`;
   - a `PatientUser` with the hidden role and no live grant.
6. **Field lists.**
   - The union of `model_fields` of each guarded spec equals the field list's names. A new
     CARE field fails until it is classified; a field CARE dropped fails too.
   - Projected output keys are a subset of the spec's keys, and dropped fields hold defaults.
7. **Guard installed.** E022 passes. A unit test serializes an encounter with every field set,
   as a restricted user, and checks the keep-list.

**b2 (swaps and matrix)**

8. **Role × endpoint matrix** (`ihr_access.tests.test_role_matrix`). It reuses the isolation
   crawl's route enumeration and parameter filling (`ihr_platform/tests/isolation/routes.py`,
   `world.py`).
   - The world: one patient with an active inpatient encounter in a bed, one completed
     encounter, an account, an invoice and a payment, a prescription, a note, a diagnostic
     report, a specimen, a service request, a token and a resource request.
   - It calls every GET route as each of the nine roles plus Doctor and Facility Admin.
   - Each answer is compared with the reviewed `ihr_access/tests/role_matrix.txt`
     (`route-pattern method role allow|project|empty|deny  # reason`). There is one subTest per
     (route, role), so a failure names the endpoint.
   - **Upstream-sync safety:** a route not in the file fails the test until it is classified, in
     the same pattern as `anonymous_allowlist.txt`. A route classified `deny` that answers 200 to
     that role also fails.
9. **Writes** (named tests, one per row):
   - patient create and update (Receptionist A; with `blood_group`, 400; others 403);
   - encounter create with discharge advice (400);
   - `add_user` with a restricted target (403);
   - allergy POST, notes POST, file upload (403 for all restricted roles);
   - charge item create (Cashier 201, TPA 403);
   - **billing denials:** invoice cancel, lock, unlock and destroy; payment cancel and destroy;
     negative and cancelled charge items. Each is 403 for Cashier, TPA Desk and Owner, and 200
     for Facility Admin.
10. **Clinical canary.**
    - A unique marker is seeded into every clinical field: note text, condition code display,
      `discharge_summary_advice`, `hospitalization.discharge_disposition`, prescription
      dosage text, diagnostic-report conclusion, questionnaire answer, care-team member, the
      bed's location name and the patient's `blood_group`.
    - No response body for any restricted role (MRD without a grant included) contains a
      marker, across all GET routes and the batch cases.
    - For the Owner, no response anywhere contains the patient's name, phone number, UHID or
      external id either.
11. **Batch.** As Receptionist:
    - a batch with an observation-list sub-request gets 403 inside;
    - a batch with an encounter list gets a projected body.
12. **Search gate.** Store Keeper, Lab Technician and Owner get 403 on `search` and
    `search_retrieve`. The Receptionist gets projected results. Doctor is unchanged.
13. **Mixed roles.** Receptionist plus Doctor sees what a Doctor sees through CARE's path. The
    same user's access through our branch is still projected.

**b3 (MRD, Owner)**

14. MRD:
    - no grant: clinical routes 403, patient D;
    - grant: clinical reads 200 for that patient only, another patient still 403;
    - writes 403 even with a grant;
    - expiry (time moved forward): 403 at the next request, before the beat runs;
    - early end: 403;
    - no reason or a bad code: 400;
    - the request entries carry `purpose: records` and the grant id;
    - grant and `PatientUser` changes are in the trail.
15. Owner totals:
    - correct counts and sums on the seeded world;
    - `reads.start()`/`reads.finish()` around the call records zero patients
      (`plugins/ihr_platform/ihr_audit/reads.py:1-18`; aggregates build no model instances);
    - no UUID or name in the body;
    - 403 for every other role;
    - a range over 366 days gives 400.

**Mutation rows** (`scripts/isolation-mutation.sh`; each must turn its named test red, with
`IHR_DB_ROLES=test`):

| Name | Mutation | Test that must fail |
|---|---|---|
| `access-guard-install` | the guard's `install()` returns early | `test_clinical_canary_never_reaches_restricted_roles` |
| `access-guard-keeplist` | the guard keeps every field | `test_projection_keeps_only_listed_fields` |
| `access-forced-projection` | our patient and encounter branches stop forcing projection | `test_mixed_roles_branch_is_still_projected` |
| `access-search-gate` | the search gate passes everyone | `test_search_refused_to_roles_without_patient_access` |
| `access-add-user-gate` | the `add_user` gate passes everyone | `test_add_user_refuses_restricted_target` |
| `access-bed-history` | the location-encounter swap passes restricted users | `test_role_matrix` (bed history row) |
| `access-resource-request` | the resource-request gate passes restricted users | `test_role_matrix` (resource request row) |
| `records-trigger` | the trigger's grant check is removed | `test_patient_user_with_records_role_needs_a_grant` |
| `records-expiry` | the per-request expiry is removed | `test_expired_grant_is_refused_before_the_beat` |
| `owner-aggregates` | the totals view returns a row list | `test_owner_totals_load_no_patient` |
| `encounter-create-fields` | the create-body check is removed | `test_receptionist_cannot_write_discharge_advice` |

## 10. Upstream-sync safety (summary)

- `ihr_platform.E020`: a swapped CARE class that is moved or renamed stops the start.
- `ihr_roles` (existing): an unknown granted slug or a dropped role name stops the start.
- `ihr_roles.E021` and `ihr_access.E022`: our permissions are registered and the guard is
  installed on the classes CARE actually uses.
- **Role matrix:** a new upstream route fails the test until it is classified for every role.
- **Field lists:** a new field on a guarded spec fails the test until it is classified.
  Meanwhile it is dropped for restricted users (fail closed).
- **A new upstream spec that embeds an encounter or patient** is covered by the guard
  automatically, because the wrapper sits on the embedded class. The canary proves it.
- **Docs:** docs/UPSTREAM.md's after-sync checklist gets a step: run `ihr_access` and the role
  matrix, then classify new routes and fields.

## 11. Seeded users and logins

- **`ihr_seed_demo`** (`plugins/ihr_platform/ihr_platform/management/commands/ihr_seed_demo.py`):
  - creates the "Laboratory", "Radiology" and "Stores" department orgs and one location linked
    to each, in both demo hospitals;
  - adds `care-receptionist`, `care-cashier`, `care-labtech`, `care-radiologist`, `care-tpa`,
    `care-mrd`, `care-storekeeper` and `care-owner`, placed as in §4. They are placed
    explicitly, **not** by copying `care-staff`'s memberships as `_add_extra_users` does
    (l.120-155), because that also copies geo memberships.
  - `care-billing` stays a Staff user, so the existing click-throughs still hold.
  - The seed's role lookup asks for every role (ADR-017, "Role count and CARE's demo loader").
- **Keycloak logins** (`ihr_keycloak_demo_users.DEMO_STAFF`, l.26-37): eight new synthetic
  people, TOTP pre-set, each linked in both hospitals:
  - `reception.ravi` → care-receptionist
  - `cashier.kavya` → care-cashier
  - `lab.imran` → care-labtech
  - `radiology.sunita` → care-radiologist
  - `tpa.joseph` → care-tpa
  - `mrd.farah` → care-mrd
  - `store.gopal` → care-storekeeper
  - `owner.vikram` → care-owner
- `scripts/care-dev.sh keycloak seed` writes their passwords and TOTP secrets to the git-ignored
  `deploy/seed/TEST_USERS.md`. The role table in `scripts/care-dev.sh` (l.~165-175) and the
  TRACKER "Test credentials" section get the usernames (never passwords).
- **Click-through (b3):**
  1. Open http://alpha.localhost:8080 and sign in as `reception.ravi`.
  2. Search a patient by phone and open them: identity and contact only.
  3. Open their encounter: status, no discharge advice.
  4. Sign in as `owner.vikram`: the totals only; the patient search is refused.
  5. Sign in as `mrd.farah`: request access with a reason, read the record, and see the grant
     in the audit viewer.

## 12. Docs to change in the build

- **ADR-018** "Demographics-and-billing access" (b3): this mechanism, the restricted-role rule,
  the MRD grants and the Owner totals. ADR-017 §6 points to it.
- **docs/GAP_ANALYSIS.md:** the CARE-role gaps (open patient search, `add_user`, prescription
  and report read through `can_read_encounter`, resource requests without a check,
  `can_write_patient` gating clinical writes).
- **TENANCY_DESIGN §6:** no new cache keys; the guard's ContextVar is per request.
- **`hospital_tables.txt`** (P1-T19a): add `ihr_records_grant` (app: read and write).
- **docs/UPSTREAM.md:** the after-sync step (§10). **No `UPSTREAM_PATCHES.md` entry**, because
  nothing in `apps/` changes.

## 13. Decisions to log (draft, for docs/QUESTIONS.md)

### Q-K34 — Which patients may the front desk open? (P1-T11b)

- **A ⭐. The whole hospital register.** Receptionist, Cashier, TPA Desk and MRD may open any
  patient of this hospital (demographics only).
- B. Only patients with an encounter in a facility where the user works.

*Recommendation A.* Each hospital is its own schema, so "the register" is that hospital's. The
patient search already finds any patient by phone. A returning patient has no open encounter
until the front desk creates one, so B would block the main front-desk job.

### Q-K35 — One person, two jobs (P1-T11b)

- **A ⭐. Permissions add up.** A person who holds a clinical role (Doctor, Nurse, Radiologist)
  and one of our non-clinical roles is treated as a clinician. Example: an owner who is also a
  doctor.
- B. Refuse mixed assignments. Such a person needs two accounts.
- C. The narrowest role wins: a doctor-owner sees projected records everywhere.

*Recommendation A.* It is how CARE's roles already combine. Doctor-owners are common in small
Indian hospitals, and B or C would break their daily work. Our widening branches still project
whatever else the user holds, so a non-clinical role never widens clinical access.

*After the attack (A4):* our encounter branch is used only where CARE's own path gives the user
nothing, and it never adds its rows to CARE's: a Receptionist who is also a Paediatrics doctor
gets CARE's Paediatrics encounters unprojected and nothing else through `?facility`. The attack's
main case (a department doctor reading every account, booking, token and payment) is CARE's own
Doctor behaviour, not caused by adding a role; it is logged in Q-K40.

### Q-K36 — MRD records grants: length, reasons, alerts (P1-T11b4)

- **A ⭐.**
  - 30 minutes, read only.
  - Reason codes: patient request, legal, insurer, internal audit, NABH review, continuity of
    care.
  - Legal and insurer need a reference.
  - No second approver.
  - Alerts: 3 refusals in 10 minutes, more than 40 grants a day per user, grants between
    22:00 and 07:00 IST.
- B. As A, but a Facility Admin approves each grant.
- C. Standing clinical read for MRD, with the audit trail only.

*Recommendation A.* Records work is frequent and B would stall it. C would give a records clerk
every patient's record at all times. A stated reason plus the trail and the alerts meets NABH
access-control expectations at small-hospital scale.

*After the attack (B5, A14):* under A, a grant also opens that patient's lab and imaging requests,
specimens, reports and prescriptions (read only), which CARE checks by department, not by
`PatientUser`. File links issued during a grant last at most 5 minutes (CARE's are 60), and
until a records screen exists (Q-K45) MRD uses the grant through the API only.

### Q-K37 — Does the front desk see where an inpatient is? (P1-T11b)

- **A ⭐. No.** Encounters shown to Receptionist, Cashier, TPA Desk and MRD carry no location
  and no department. Visitors' "which ward?" questions go to the nursing station.
- B. The ward name only (not the bed), for visitor enquiries.

*Recommendation A.* Q-K21 excludes bed occupants. A ward or department name (oncology,
psychiatry) reveals the kind of illness. B can come with P2-T2 registration screens if wanted.

*After the attack (A2, A3/B4, A5, A6/B7):* A also means these roles cannot **find** patients by
bed, department, doctor or tag (those encounter filters answer 400), cannot read an encounter's
departments, do not see who is on a device, and see no booking notes or booked doctor.

### Q-K38 — Charge-item names seen by Cashier and TPA Desk (P1-T11b)

- **A ⭐. Accept.** Billing lines name the service ("HIV test", "chemotherapy cycle"), which
  billing needs.
- B. Hide service names from the Cashier and show codes only.

*Recommendation A.* A bill without service names cannot be explained to the patient or the
insurer. The view is still limited to people whose job is billing. ADR-018 records this as a
known residual exposure.

### Q-K39 — Small numbers in the Owner's totals (P1-T11b4)

- **A ⭐. No suppression, and no clinical breakdown.** Totals are by encounter class and status,
  beds and money only. There are no department, doctor, service or diagnosis splits until MIS
  (P2-T16).
- B. Hide any count under 5 in per-day figures.

*Recommendation A.* Without a clinical dimension, a small count does not point to a patient's
condition. Suppression would make daily figures wrong for small hospitals. The question comes
back with MIS breakdowns.

### Q-K40 — CARE's own access gaps found on the way (P1-T11b)

These CARE behaviours affect **CARE's own roles**:
- patient search open to any signed-in staff user;
- `add_user` giving any role on a patient;
- prescriptions and the discharge summary readable with `can_read_encounter` alone;
- resource requests with no permission check;
- `can_write_patient` also gating allergy, note and file writes;
- *(added after the attack)* Doctor, Nurse, Staff, Volunteer and Pharmacist read every account,
  booking, token and payment of the hospital, each embedding the full encounter, wherever
  their department is (`security/permissions/account.py:29-42`, `schedule.py:34-46`,
  `token.py:47-58`, `payment_reconciliation.py:30-43`; A4);
- the device list and retrieve need only facility membership and show the patient on each
  device (`emr/api/viewsets/device.py:119-173`; A2);
- a `PatientUser` care-team row keeps giving clinical read after the person's job changes (A9);
- an account-report template can print the whole clinical encounter, and anyone who reads the
  account can download it (A1).

Our roles are protected from all of them by the design above (after the attack).

- **A ⭐. Protect our roles now, and log the rest as a follow-up task** ("P1-T11d: close CARE-role
  access gaps", after P1-T11c). The `add_user` gate for restricted targets ships now.
- B. Close all of them now for every role. That changes Doctor, Nurse and Staff behaviour inside
  this task.

*Recommendation A.* P1-T11b's tests assert that CARE's roles are unchanged. The gaps are real,
but fixing them for CARE's roles needs their own design and checks against the web app's flows.

### Q-K41 — Account reports for the Cashier and TPA Desk (P1-T11b; added after the attack, A1)

An account report is a PDF built from a template. CARE's account template can print the whole
clinical encounter (diagnoses, prescriptions, discharge advice), and anyone who may read the
account may download any account report (`emr/reports/authorizers/account.py`,
`emr/api/viewsets/report/report_upload.py:67-88`). A TPA "final bill" that shows the diagnosis is
the normal Indian claim layout, so a hospital will make one.

- **A ⭐. Refuse account reports to the restricted roles until P2-T15.** Receptionist, Cashier,
  TPA Desk, Owner, Lab Technician, Store Keeper and MRD cannot list, download, generate or
  archive account reports. Bills and receipts still print from the invoice screens. P2-T15 (TPA) designs a claim layout and a reviewed list of billing-only
  templates, and comes back to you if TPA claims need diagnosis codes.
- B. Allow templates now that a check proves print no clinical field (a field-graph walker,
  re-checked on every template change).

*Recommendation A.* It closes the leak with a small swap and a test. B is a sizeable piece of
work whose real need (the claim layout) is P2-T15's.

### Q-K42 — May the Receptionist see the list of departments? (P1-T11b; added after the attack, B2)

CARE's "create encounter" form needs a department. With no department rights, the Receptionist's
picker offers only the hospital as a whole, so every front-desk encounter is filed under the
whole hospital and a department's doctors may not see it.

- **A ⭐. Yes.** The Receptionist gets CARE's `can_view_facility_organization` at the hospital
  level: department names and the department tree, which are hospital settings, not patient
  data. They still never see which department an existing patient's encounter is in.
- B. No. Front-desk encounters land on the hospital as a whole and clinical staff move them to a
  department.

*Recommendation A.* Sending a patient to the right department is the front desk's job. The
department list holds no patient data, and the filters that could use department ids to find
patients answer 400 (Q-K37).

### Q-K43 — Store Keeper and patient medication returns (P1-T11b; added after the attack, A10)

CARE records a medication return as a delivery order to the store or pharmacy named
"Medication return - <patient name>", with the patient attached
(`apps/frontend/src/pages/Facility/services/pharmacy/CreateMedicationReturnSheet.tsx:111-132`).
Where the store and the pharmacy share a location, the Store Keeper would see a named patient
next to the returned drugs (for example antiretrovirals).

- **A ⭐. Hide them.** Delivery orders and supply deliveries linked to a patient are invisible to
  the Store Keeper (and every other restricted role). Pharmacists handle returns.
- B. The Store Keeper handles returns and sees the patient's name on them.

*Recommendation A.* It keeps §4's "nothing patient" for the store, and Q-K21 lists prescriptions
among what non-clinical staff never see.

### Q-K44 — When does the Radiologist see a patient's clinical record? (P1-T11b; added after the attack, B6)

In CARE a department sees a patient's record only when the department is on the encounter (or
the patient's bed belongs to it). An X-ray request at the Radiology room does **not** count
(`security/authorization/patient.py:16-47`, `service_request.py:16-28`). So the earlier design's
"wherever Radiology is involved" was wrong.

- **A ⭐. CARE's rule, in P1.** The Radiologist sees the full record when the Radiology
  department is on the encounter (the front desk adds it at registration, or a clinician adds it
  later). Otherwise the Radiologist sees the imaging requests and reports only, not the chart.
- B. A plugin rule: while a patient has an open imaging request at a Radiology room, the
  Radiologist may read the chart (time-boxed and audited like MRD's grants). It widens clinical
  access, so it would come with the imaging work (Orthanc/OHIF, Phase 2).

*Recommendation A.* It needs no new access rule. B can be decided with the imaging workflow,
when ordering a scan can add Radiology to the encounter by itself.

### Q-K45 — Front-desk, records and owner screens in the web app (P1-T11b → P1-T11c; added after the attack, B1, B13)

CARE's web app opens patient tabs, the encounter list and the edit form only for CARE's own
permissions, which these roles deliberately lack. After creating an encounter it opens a
clinical page. There is no page for MRD's records access or the Owner's totals.

- **A ⭐. Widen P1-T11c** (the web-app patch, built in CI) to cover: the patient tabs, the
  encounter history, the patient home's encounter tab, the edit form, the redirect after
  creating an encounter, a records-access page (patient, reason, reference, active grants, end
  now) and an Owner totals page (lakh/crore). P1-T11b's own click-through is checked partly
  through the API.
- B. Keep P1-T11c to menus and add a new task for the screens.

*Recommendation A.* The screens and the menus are one patch to the same web app and one CI build.
Until then the eight roles can be seeded and tested, but the front desk, MRD and the Owner
cannot use them day to day.

## After the attack (2026-11-14): the design as it is built

Two Opus attackers (A: security and tenancy; B: CARE's behaviour, the web app and the tests)
raised findings, and a second agent tried to refute each against the code. **Result: 30 raised,
1 refuted (B15), 19 confirmed, 10 partly.** Three are the same finding found twice (A3 = B4,
A6 = B7; A13 contains B9 and B10), so 26 are distinct. Severity after the challenge: 6 high (A1,
A2, A3, B4, B5, B8), 13 medium, 10 low; none fatal to the mechanism (fail closed, swaps, guard).

Five would have made the built design miss Q-K21 or prove nothing, and are fixed in the design
here: **A1** (an account report carries the clinical encounter in a downloadable file), **A2**
(devices show who is on them, to the Owner too), **A3/B4** (encounter filters find a bed's,
department's or doctor's patients), **B5** (an MRD grant does not open lab and imaging reports)
and **B8** (the matrix never sends the query parameters CARE's lists need, so the new branches
go untested).

Where this section and the sections above disagree, **this section wins**. Inside it, "item 8.1"
means subsection 8, item 1.

| Verdict | Findings |
|---|---|
| Confirmed (19) | A2, A3, A5, A6, A7, A10, A13, A15, B1, B3, B4, B5, B6, B7, B9, B10, B12, B13, B14 |
| Partly (10) | A1, A4, A8, A9, A11, A12, A14, B2, B8, B11 |
| Refuted (1) | B15 |

### 1. New split (replaces §1)

| Part | What | Size |
|---|---|---|
| **P1-T11b1** Roles, grants and the guard | §3–§5.2 as above plus subsections 2–4 below: placement trigger (root rule, `PatientUser` firing), guard on patient, encounter, location, device, booking and token shapes (nested keys, identifier allow-list), the `PatientUser` read-time wrapper; seeded users and the larger test world; b1 tests | 3 h |
| **P1-T11b2** Swaps | subsections 5–7 and tests 4–11: patient (action allow-list, list, edit rule, geo rule, appointments and tokens), encounter (retrieve, filters, branch rule), bed history, resource requests, device, report upload, delivery orders and supply deliveries; named write tests; mutation rows | 3 h |
| **P1-T11b3** Matrix and canary | tests 12–20: query-parameter table, outcomes, every method, key-set snapshot, spec walk, per-role canary, batch cases | 2–3 h |
| **P1-T11b4** MRD and Owner | subsection 8, §7–§8 above and tests 21–23: grants, the grant authorizer, one end routine, audit context, signed-link cap, Owner totals, ADR-018, the click-through | 3 h |

The web-app screens go to P1-T11c (Q-K45). Q-K36 and Q-K39 now name P1-T11b4.

### 2. Guard: more shapes, nested keys (b1; A2, A6/B7, A12, A15, B1)

1. **Device** (`emr/resources/device/spec.py:86-104`): `DeviceRetrieveSpec` is guarded with
   `fields/device.txt`; `current_encounter` is dropped for every restricted user (as a bed's
   occupant is), the rest kept. E022 covers it.
2. **Bookings and tokens** (`scheduling/slot/spec.py:105-162`, `scheduling/token/spec.py:101-125`):
   `TokenBookingBaseReadSpec` and `TokenRetrieveSpec` are guarded, dropping `note`, `resource`,
   `tags`, `sub_queue` and `charge_item`, **only under forced projection**, so the
   Receptionist's own CARE-granted booking screens keep them.
3. **Encounter keep-list:** `appointment` is no longer kept whole. It keeps only `id`, `status`,
   `booked_on`, `token_slot.start_datetime` and `token_slot.end_datetime` (named nested keys).
4. **Patient keep-list:**
   - `permissions` is kept, filtered to our `ihr_*` slugs and non-clinical CARE slugs (gives
     P1-T11c a per-patient signal without inventing `can_list_patients`). A b1 test checks it
     holds no clinical slug.
   - `instance_identifiers` and `facility_identifiers` keep only identifiers whose config
     `system` is on the reviewed `fields/identifier_configs.txt` (UHID/MRN, ABHA number and
     address, phone-based). A programme identifier (ART registration, Nikshay) is dropped:
     having one reveals the illness (`emr/resources/patient/spec.py:274-290`). The same list
     limits `update_identifier`, `search` and `search_retrieve` for these users.
5. **Nested containers:** every kept field of type dict or list of dicts must name its nested
   keys in the field list; an unnamed container fails test 6.

### 3. `PatientUser` follows the person's current job (b1; A9, B11)

1. A wrapper on `PatientAccess.find_roles_on_patient` (`security/authorization/patient.py:15-47`),
   installed in `IhrAccessConfig.ready()` with an `ihr_guard` marker and checked by E022:
   - ignores every `PatientUser` role when the user holds only restricted roles (a care-team row
     added while they were a Nurse stops counting when they become a Receptionist);
   - counts a hidden-role (`MRD Records Access`) row only while a live `ihr_records_grant`
     exists for that user and patient (`ended_at IS NULL AND expires_at > now()`), at read time.
2. The patient **list** for restricted-only users is empty, except MRD, who sees only patients
   under a live grant. (CARE's list reads `users_cache` directly,
   `security/authorization/patient.py:114-128`, which can be stale.)
3. A check command lists `PatientUser` rows held by restricted-only users; the admin guide says
   to remove them.

### 4. Placement trigger (b1; A11, A9, B11)

Added to §5.1:
- Lab Technician, Radiologist and Store Keeper are refused in a `FacilityOrganizationUser` whose
  organization is the facility's `default_internal_organization` (compared by column, not
  `org_type`). At the root they would read every patient with an active encounter
  (`emr/models/encounter.py:39-54`).
- A `PatientUser` (any role) is refused for a user who holds only restricted roles; role ids are
  looked up by our permanent names.
- On `emr_patientuser` it fires `BEFORE INSERT OR UPDATE OF role_id, user_id, patient_id,
  deleted` and checks only rows with `NEW.deleted = false`. CARE's soft delete
  (`utils/models/base.py:30-32`) and `delete_user`'s hard `QuerySet.delete()`
  (`emr/api/viewsets/patient.py:330`) always pass; an update that sets the hidden role or
  un-deletes a row without a live grant is refused.

The front-office roles are **not** forced to the root: a multi-unit hospital may need them per
unit, and their projection already limits them.

### 5. Patient swap (b2; A7, A6/B7, B3, B14, A8)

1. **Per-action widening.** Our `get_object` widening applies only to `retrieve`, `update`,
   `partial_update` and `update_identifier`; our `authorize_update` widening only to the last
   three. `get_users`, `add_user`, `delete_user`, `get_appointments`, `get_tokens` and the four
   tag actions (`patient.py:446-503`) keep CARE's checks. A drift test compares the router's
   detail actions on `PatientViewSet` with a reviewed list (widened or CARE-only), so an
   upstream action fails until classified.
2. **Appointments and tokens.** Restricted users must pass `?facility=`, so CARE's
   `can_list_booking_on_facility` / `can_list_token_on_facility` runs (`patient.py:344-348`).
   Without it they get 403.
3. **The edit rule** (replaces "body limited to the keep-list; 400 on blood_group, extensions,
   tags"). The web app's save always resends `extensions` and `tags: []`, filled from the
   projected GET (`apps/frontend/src/components/Patient/PatientRegistration.tsx:237-246,
   318-371`); refusing them breaks every save, and accepting them would wipe real data.
   - **Edit list** (`fields/patient_edit.txt`, own drift test): `name`, `gender`,
     `phone_number`, `emergency_phone_number`, `address`, `permanent_address`, `pincode`,
     `date_of_birth`, `year_of_birth`, `age`, `geo_organization` (item 4); identifiers through
     `update_identifier`, limited to the identifier list of item 2.4.
   - **Hidden fields** (the patient drop list): always ignored, never written.
   - **Visible fields not on the edit list** (`deceased_datetime`, `created_by`, `version` …):
     ignored when equal to the stored value, 400 when changed. Recording a death stays with
     CARE's write path.
   - Validation goes through a narrow spec with no `extensions`, `blood_group` or tag fields,
     so `ExtensionValidator`'s default `{}` can never be written
     (`emr/extensions/validator.py:22-34`).
4. **Moving a patient's district** (low). A new `geo_organization` is accepted only when it lies
   in the same state as the old one or matches the PIN's district, and when the new org chain
   adds no `OrganizationUser` holding `can_list_patients`, `can_view_clinical_data` or
   `can_view_questionnaire_responses` that the old chain lacked. Otherwise 403 and the log line
   `ihr-access: refused geo_move`.

### 6. Encounter swap (b2; A5, A3/B4, A4, B2)

1. **Retrieve.** CARE's `authorize_retrieve` is left unchanged. Only `retrieve()` is overridden:
   CARE's check, then our slug, then forced projection. `organizations`
   (`encounter.py:300-316`) and every other detail action keep CARE's check. A drift test lists
   the viewset's `@action` names against a reviewed list.
2. **Filters.** On our branch the filter set is `IhrProjectedEncounterFilters`: `facility`,
   `patient`, `status`, `live`, `encounter_class`, `priority`, `external_identifier`, `name`,
   `phone_number`, `created_date`, with no `SingleFacilityTagFilter`. Any query parameter
   outside these plus `ordering`, `limit` and `offset` answers **400** (`location`,
   `organization`, `care_team_user`, `tags`, `tags_behavior`, and anything upstream adds). A
   system check fails when CARE's `EncounterFilters` (`encounter.py:89-108`) gains a filter not
   classified in `fields/encounter_filters.txt`. MRD's grant path uses CARE's full filters.
3. **Branch rule** (replaces §5.3 item 2's "tried first"). For `?facility` and `?patient`, if
   the user holds CARE's list right there, CARE's path answers, unprojected (Q-K35). Otherwise
   our branch answers with only rows our slug allows, never added to CARE's rows, and forced
   projection covers the whole response.
4. **Create.** `organizations` stays allowed: sending a patient to a department is the front
   desk's job, and it is what gives Radiology its read (Q-K44). With Q-K42 A the Receptionist
   gets `can_view_facility_organization` at the root so the picker lists departments
   (`FacilityOrganizationSelector.tsx`, `emr/api/viewsets/facility_organization.py:185-245`).

### 7. Further swaps (b2; A1, A2, A10)

1. **`ReportUploadViewSet`** (`emr/api/viewsets/report/report_upload.py:58`). For restricted-only
   users (MRD included; a grant does not open billing): `account_report` list, retrieve, generate
   and archive answer 403 (empty for list), Q-K41. Encounter, discharge-summary and
   patient-summary reports are already closed by their clinical authorizers. Generating from an
   instance template (superuser-made, `emr/api/viewsets/report/template.py:90,100`) is refused
   to them too.
2. **`DeviceViewSet`** (`emr/api/viewsets/device.py:58`): empty list and 403 retrieve for
   restricted-only users (none has a device job), on top of the guard. Device encounter history
   is already closed (`can_list_devices`, `device.py:391-398`); it gets matrix rows only.
3. **`DeliveryOrderViewSet`** and **`SupplyDeliveryViewSet`**
   (`emr/api/viewsets/inventory/delivery_order.py:57`, `supply_delivery.py:69`): for
   restricted-only users, `patient__isnull=True` and `order__patient__isnull=True` on list and
   retrieve (Q-K43). Rows are filtered, not blanked: the order's name carries the patient's
   name.
4. **Membership-only lists.** A static test lists every registered CARE viewset whose
   `get_queryset` filters by `FacilityOrganizationUser` membership with no permission slug,
   against a reviewed `membership_only.txt` (facility, facility users, own organisations,
   device…). §4's "facility retrieve and the facility users list need only membership" becomes
   that file.

### 8. MRD path (b4; B5, B11, B12, A14)

1. **What a grant opens.** `PatientUser` counts only in patient-level checks
   (`security/authorization/patient.py:43-56, 94-101`). Service requests, specimens and
   diagnostic reports are checked by department and location
   (`security/authorization/service_request.py:10-39`), prescription retrieve by
   `can_view_encounter_obj` (`emr/api/viewsets/medication_request_prescription.py:100-109`). So:
   - `ihr_access.authz.RecordsGrantAuthorizer` is registered with
     `AuthorizationController.register_override_controller` (`security/authorization/base.py:116-117`)
     in `ready()`, before the controller's lazy map is built. It subclasses CARE's handlers and
     wraps **read methods only**: `can_read_service_request`, `can_read_specimen`,
     `can_read_diagnostic_report`, `can_read_diagnostic_report_in_encounter`,
     `can_view_service_request_for_encounter`, `can_view_encounter_obj`. Each returns CARE's
     answer, or True when the user holds a live grant (checked on the grant table) for the
     object's patient.
   - Never a write method; never a location-wide list (`can_list_location_service_request`).
   - `can_view_encounter_obj`'s callers are all reads or behind a write check: prescription
     retrieve, the encounter report read (`emr/reports/authorizers/encounter.py:12`), encounter
     retrieve (`encounter.py:161`), care-team update after `authorize_update`
     (`encounter.py:425-435`).
   - System check `ihr_access.E023`: each wrapped name resolves to our class.
   - §2 fact 4 is not contradicted: the predicate is one patient under a live grant, which
     already has `can_view_clinical_data` on that patient.
   - The matrix's MRD+g cells are re-derived from the code, route by route.
2. **One end routine.** `records.end_grant(grant, outcome)` sets `ended_at`, deletes the
   `PatientUser` and re-saves the patient in one atomic block; the DELETE endpoint, the beat and
   the per-request expiry all use it. The per-request expiry stays inside the request
   transaction (ATOMIC_REQUESTS, `config/settings/base.py:73`); if a 403 rolls it back, the
   read-time wrapper (item 3.1) still refuses. The `ihr-records … outcome=expired` line is
   emitted with `transaction.on_commit`, so only the committed end logs.
3. **Audit context.** The middleware writes the request entry from its local dict
   (`plugins/ihr_platform/ihr_audit/middleware.py:43-90`), which a later `_set` cannot reach
   (`context.py:41-44`). So: `context.current_context()` returns the ContextVar's dict; the
   middleware's `finally` block uses it after `resync()` before `_record`, as it already does
   for the actor. `annotate_request()` is a non-restoring `_set` like `bind_actor`
   (`context.py:184-190`), not `extend()` (which restores on exit, l.231-236). The audit-purpose
   decision stays on the original path.
4. **File links.** CARE signs read links for 60 minutes (`emr/utils/file_manager.py:65`). In the
   plugin, `S3FilesManager.read_signed_url` is wrapped (the class the isolation suite already
   patches, `plugins/ihr_platform/ihr_platform/tests/isolation/test_isolation.py:634-668`): a
   request served under a live grant gets `min(seconds left, 300)`. If the wrap proves fragile,
   ADR-018 states the 60-minute limit instead. The issuing request is already audited with the
   path and the grant.
5. **Radiologist** (B6, Q-K44 A). §4's "or a Radiology location is involved" and §7's last
   paragraph are wrong: clinical context comes only from the encounter's departments (with
   parents and the root) and the current bed's department. A Radiologist reads the chart when
   the Radiology department is on the encounter; otherwise imaging requests and reports only.

### 9. Matrix changes (replaces the §6 cells named here)

| Endpoint | Rec | Cash | TPA | Own | Lab | Rad | MRD | MRD+g | Store |
|---|---|---|---|---|---|---|---|---|---|
| `patient/` list | E | E | E | E | E | A | E | A (granted only) | E |
| `patient/{id}/get_appointments`, `get_tokens` | A with `?facility` (projected bookings); F without | F | F | F | F | A | F | A | F |
| patient tag actions | F | F | F | F | F | CARE | F | F | F |
| `encounter/{id}/organizations/` | F | F | F | F | F | A | F | A | F |
| `encounter/?…` with `location`, `organization`, `care_team_user`, `tags` | 400 | 400 | 400 | F | F | A | 400 | A | F |
| `service_request`, `specimen`, `diagnostic_report`, prescription retrieve | F | F | F | F | A (lab orgs) | A where Radiology is on the encounter or the request's location; else SR, DR only | F | A (read, via §8.1) | F |
| Radiologist: patient, encounter, clinical rows | | | | | | A only with Radiology on the encounter; else F | | | |
| `facility/{id}/organizations/` list | A (Q-K42) | as CARE | as CARE | as CARE | as CARE | as CARE | as CARE | as CARE | as CARE |
| device list, retrieve | E / F | E / F | E / F | E / F | E / F | as CARE | E / F | E / F | E / F |
| device encounter history | F | F | F | F | F | as CARE | F | F | F |
| account report: list, retrieve, generate, archive | F | F | F | F | F | as CARE | F | F | F |
| delivery orders, supply deliveries | F | F | F | F | inventory only | F | F | F | A, patient-linked rows absent |

### 10. Tests (replaces or extends §9)

**b1**
1. Test 6 also covers the device, booking and token field lists and the patient edit list;
   unnamed nested containers fail; the identifier list is checked against a seeded programme
   identifier config ("ART registration"), absent from every restricted view and refused in
   writes and search.
2. Trigger: Radiologist, Lab Technician or Store Keeper at the root is refused; `PatientUser` for
   a restricted-only user is refused; a soft delete and a hard delete pass; an UPDATE that sets
   the hidden role or `deleted=false` without a live grant is refused.
3. `PatientUser` wrapper: a Nurse added with `add_user` and then moved to Receptionist gets 403
   on every clinical sub-resource; a hidden-role row with an expired grant and the cleanup jobs
   stubbed out gives no clinical read.

**b2** (named tests)
4. Receptionist on `add_user` with a non-restricted target, `delete_user` and the four tag
   actions: 403.
5. **Replay save:** GET the patient as Receptionist, rebuild `PatientRegistration`'s exact body
   (`tags: []`, default `extensions`), PUT it: 200; `blood_group`, `extensions` and both tag
   sets unchanged, an edited address changed. `deceased_datetime` changed: 400; resent
   unchanged: 200; a Doctor can still set it.
6. Geo move into an org whose member holds a Doctor role: 403 (and that member still gets 403 on
   the patient's clinical data); a PIN-consistent district: 200.
7. Encounter filters: `location`, `organization`, `care_team_user` and `tags` with real seeded
   values give 400 for Receptionist, Cashier, TPA Desk and MRD on both branches; allowed
   filters return the same rows as the projection.
8. Mixed roles (extends 13): Receptionist + Paediatrics Doctor on `encounter/?facility=` gets
   exactly CARE's Paediatrics rows, unprojected; Receptionist-only gets projected rows for the
   whole hospital.
9. Receptionist makes the web app's calls: `facility/{id}/organizations/mine/`, the department
   list, `POST encounter` with a department: 201, department on the encounter. With Radiology
   attached, the Radiologist can read the patient and the `EncounterOrganization` row is in the
   trail.
10. Account report generated by Facility Admin from a template that prints canary diagnoses and
    prescriptions: 403 on list, retrieve and generate for every restricted role; no signed URL.
11. Store Keeper: the canary return order is absent from every delivery-order and
    supply-delivery list, retrieve and batch answer; a pharmacist sees it.

**b3** (matrix and canary)
12. **Query parameters.** `ihr_access/tests/list_params.txt` (route, parameter, world object):
    `encounter` with `facility` and `patient`; `service_request` with `location` and
    `encounter`; `diagnostic_report` and `observation` with `encounter` and `service_request`;
    every list whose `get_queryset` reads `request.GET`. A static check fails on such a list
    with no row. The isolation crawl fills path parameters only
    (`plugins/ihr_platform/ihr_platform/tests/isolation/routes.py:97-136`).
13. **Outcomes.** `allow`, `project` and `empty` must answer 200; `allow` and `project` must
    return at least one seeded row; a 400 fails unless the row says `invalid  # reason`;
    Doctor and Facility Admin rows must answer 200 with rows.
14. **Every method.** Non-GET routes classified `deny` get an empty body and must not answer 2xx
    (403/404 preferred, 400 accepted); non-GET `allow` rows need a named test. No write crawl.
15. **Key-set snapshot** `role_matrix_keys.txt`: for each allowed or projected (route, role),
    the response keys with nested dicts walked. A new key fails until classified. This covers
    account, charge item, invoice, payment, booking, token, device and delivery bodies without a
    hand list per spec (A12).
16. **Spec walk.** Every `EMRResource` subclass whose `__model__` is Patient or Encounter carries
    the guard marker or is in a reviewed exemption list with a reason (`PatientOTPReadSpec`:
    patient OTP flow only; `PatientPartialSpec`: demographics only). §10's "covered
    automatically" holds only for embeddings through the wrapped classes; this walk catches the
    rest.
17. **Compact file.** `role_matrix.txt` rows are per route with role groups
    (`restricted:deny`, `clinical:allow`) and per-role exceptions, expanded by the test.
18. **Canary per role and marker.** `canary_exemptions.txt` (role, marker, route pattern,
    reason): Lab Technician may see the report and request markers on lab routes only;
    Radiologist and MRD+g on their granted routes. Each exemption has a negative row. Two copies
    of the request and report: one at a lab location, one at a ward; the ward copy's markers
    never reach Lab Technician.
19. **World additions:** a device on the active encounter with only the root in its cache; a
    booking with a canary note and a resource; a patient-linked return order at the store; an
    account report as in 10; an encounter with a department and one with Radiology; a non-empty
    patient extension; the programme identifier; the moved-Nurse `PatientUser` row. A
    world-coverage check: every guarded spec has a seeded instance.
20. **Batch** (replaces 11; the allow-list has one GET, `batch_allowlist.txt:30`, so the old
    cases were refused as `not_listed` before any view ran, `batch.py:130-160`): Receptionist
    `PUT patient/{id}/` with `blood_group` (200, unchanged); Receptionist and Cashier
    `PUT encounter/{id}/` (403 inside); Cashier `GET medication/dispense/{id}/` (403 inside,
    or no canary or patient fields). Each asserts the batch was not refused and no
    `ihr-batch: refused` line was written. One labelled case checks that a non-listed GET is
    refused. A meta-test fails when a newly allow-listed GET serving a guarded spec has no
    projected-batch case.

**b4**
21. MRD+g, per route of item 8.1: 200 with a grant, 403 without, 403 for another patient, 403
    after expiry. One expired log line per grant; the beat leaves no live `PatientUser`.
22. A grant-served file retrieve with 2 minutes left gives a link valid for at most 120 s.
23. An MRD GET that shows a patient under a grant and changes no rows gives a `request` entry
    with `purpose: records` and the grant id.

**Mutation rows added**

| Name | Mutation | Test that must fail |
|---|---|---|
| `access-device-encounter` | the guard keeps the device's `current_encounter` | `test_clinical_canary_never_reaches_restricted_roles` (Owner) |
| `access-booking-note` | the booking guard keeps `note` and `resource` | the canary (booking note) |
| `access-encounter-filters` | the filter allow-list is removed | `test_restricted_encounter_filters_refused` |
| `access-retrieve-scope` | the widening moves into `authorize_retrieve` | `test_encounter_organizations_refused` |
| `access-update-action-allowlist` | widening applies to every patient action | `test_receptionist_cannot_add_user_or_tag` |
| `access-edit-hidden-fields` | the update writes hidden fields | `test_receptionist_save_keeps_hidden_fields` |
| `access-patient-user-roles` | the wrapper counts `PatientUser` for restricted users | `test_moved_nurse_loses_patient_user_read` |
| `access-root-placement` | the trigger's root rule is removed | `test_radiologist_refused_at_root` |
| `access-account-report` | the report swap passes restricted users | `test_account_report_refused_to_restricted` |
| `access-delivery-patient` | the delivery filter is removed | the canary (Store Keeper) |
| `records-grant-scope` | the grant authorizer ignores which patient the grant is for | `test_grant_does_not_open_other_patient_reports` |
| `records-annotate` | the middleware stops reading the context back | `test_records_request_entry_has_purpose` |

### 11. Web app and click-through (B1, B2, B13)

- CARE's web app opens patient tabs, the encounter history and the edit form only for CARE
  slugs in the patient's `permissions` (`apps/frontend/src/context/PermissionContext.tsx:39-41`,
  `components/Patient/PatientDetailsTab/index.tsx:93-94`, `EncounterHistory.tsx:39-41,63`,
  `Demography.tsx:38-40,276`, `pages/Patient/PatientHome.tsx:70-77,236`) and after creating an
  encounter it opens `/encounter/<id>/updates` (`CreateEncounterForm.tsx:148-151`). These are
  P1-T11c's (Q-K45), keyed on our `ihr_*` slugs, logged in `UPSTREAM_PATCHES.md`.
- The picker was not a lock-out (B2, partly): with only the root, "My organizations"
  auto-selects it; Q-K42 adds the departments.
- **Click-through (b4)**, replacing §11's:
  1. Open http://alpha.localhost:8080 and sign in as `reception.ravi`. Search a patient by
     phone: the demographics show; the clinical tabs are hidden.
  2. In the same tab open `http://alpha.localhost:8080/api/v1/encounter/?patient=<id from the
     address bar>`: status-only rows; add `&location=<any id>`: 400.
  3. Sign in as `owner.vikram` and open
     `http://alpha.localhost:8080/api/ihr/owner/totals/?facility=<id>&from=01-11-2026&to=14-11-2026`:
     totals only. The patient search is refused.
  4. Run `scripts/care-dev.sh records-access mrd.farah <patient id> --reason insurer --reference
     TEST-1` (a dev-only command calling the same grant code as the endpoint). Sign in as
     `mrd.farah` and open the patient: the clinical record and its lab reports open. 30 minutes
     later (or after `--end`), they are closed again.
  5. As `care-admin`, `/api/ihr/audit/entries/?…` shows the grant and the reads with
     `purpose: records`.

### 12. Not acted on, and why

- **A4 main case** (refuted in part): a department Doctor already reads every account, booking,
  token and payment in CARE; adding a Receptionist role widens nothing there. Logged in Q-K40;
  the list ambiguity it found is fixed (item 6.3).
- **A11, encounter departments:** the front desk attaching a department is routing, not a leak;
  kept and tested (test 9).
- **A1, generate half:** hospital templates need `can_generate_report_from_template`
  (`security/permissions/template.py:50-63`), which no restricted role holds.
- **A14:** a link that outlives the grant gives no data MRD could not already have downloaded;
  the cap (item 8.4) makes the time box hard where cheap.
- **B15 (refuted):** tenant apps load after `care.emr` (`plugins/ihr_platform/ihr_platform/settings/tenancy.py:27-30, 47-54`),
  so `IhrAccessConfig.ready()` does not hit the import cycle `route_swaps` met. ADR-018 records
  that `ihr_access` must stay a tenant app; moving it earlier fails loudly at start-up.
- **§5.2 "the guard is off in Celery":** stays. The one Celery path that rendered clinical data
  for a restricted user (account reports, A1) is closed at the report swap.
