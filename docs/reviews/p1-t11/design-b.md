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

## P1-T11b1 as built (2026-11-14)

**What exists now.** The eight roles and the hidden `MRD Records Access` are registered with
their exact grants; our five `ihr_*` permissions are registered with CARE; the serializer guard
projects six shapes for restricted users; `PatientUser` rows follow the person's current job;
the placement trigger guards the three membership tables; the demo seed has one user per role.
Nothing widens access yet: until P1-T11b2 the front-desk roles cannot open a patient by id
(CARE answers 404), and their patient list is empty.

### Build risk: the classmethod wrap (proven first)

Setting `serialize = classmethod(wrapper)` on a pydantic 2.12.5 model class works: the
attribute is accepted, resolves through every subclass (also one defined later), carries the
marker through the bound method, and leaves `model_fields` alone. Neither fallback was needed.
One finding shaped the projection: `model_dump` silently omits a required field that
`model_construct` was not given, so a dropped required field (`extensions` on the retrieve
specs, a booking's `note`) is reset to an empty value of its own type (`{}`, `[]`, `""`) rather
than left out. Keys therefore always stay present.

### Files

| Where | What |
|---|---|
| `ihr_roles/permissions.py` | `IhrPermissions`: the five slugs (contexts PATIENT, PATIENT, ENCOUNTER, FACILITY, FACILITY), no roles of their own |
| `ihr_roles/registry.py` | `ROLES` (9), `PERMANENT_NAMES`, `GRANTS` (§4 plus Q-K42's `can_view_facility_organization` for the Receptionist), `RESTRICTED`, `DEPARTMENT_ONLY`, `HIDDEN`, `CLINICAL_SLUGS`, `PATIENT_OPENING`, `NEVER_RESTRICTED`, and `ORIGINAL` (CARE's role lists, recorded before the first grant) |
| `ihr_roles/apps.py`, `checks.py` | handler registered and CARE's permission cache cleared before `install()`; check `ihr_roles.E021` (registration only: a system check does not read the database, so "has a row after the sync" is a test) |
| `ihr_access/` (new tenant app, last of ours) | `state.py` (per-request state), `guard.py` + `fields/*.txt`, `authz.py` (PatientUser rule, patient list), `placement.py` (trigger), `models.py` + migration `0001` (`ihr_records_grant`), `checks.py` (`ihr_access.E022`), command `ihr_patient_user_check`, `testing.py` (the test world; its encounter, patient and booking carry canary tags, review C7) |
| `ihr_platform/settings/tenancy.py` | `ihr_access` added to the tenant apps |
| `ihr_platform/seeding.py` | CARE's demo loader asks for every role (`limit=200`; refuses if there were more) |
| `ihr_seed_demo.py`, `ihr_keycloak_demo_users.py`, `scripts/care-dev.sh` | the eight seeded users, their Keycloak people and the TEST_USERS.md role table |
| `ihr_platform/restore/package.py` | the placement guard's function and trigger added to `OUR_FUNCTIONS` / `OUR_TRIGGERS`: a hospital dump may hold them and they are never exported; the restore's migrate reinstalls them after the data, so restored memberships are not re-judged (the first full run failed the three restore classes on this) |
| `scripts/isolation-mutation.sh` | 28 new rows (14 at first, 9 from the review fixes, 5 from the second review) |
| `ihr_access/exceptions.py` | the placement refusal as a 400: wraps CARE's `emr_exception_handler`, and rebinds the name in the CARE modules that import it (batch, password; second review of C3) |
| `ihr_access/identifiers.py` | the identifier allow-list on `patient/search` (403) and `update_identifier` (400) for restricted users (second review of S2; deviation 10) |
| `ihr_access/migrations/0002_placement_fingerprint.py` | `PLACEMENT_SHA256` (review C1) |
| `ihr_roles/care_permissions.py`, `care_permissions.txt` | CARE's permissions as reviewed (slug, context, class, CARE's roles), checked against the code by `CarePermissionsReviewed` (review C6; mutation `care-permissions-snapshot`) |
| `ihr_platform/fleet.py` | the placement guard reported by `check`, repaired by `migrate` for a current hospital, also when its grant repair fails (review C1 and its second review) |
| `ihr_platform/db_roles.py` | a broken placement guard is a hospital "control": an app process gates that hospital (second review of C1) |
| `.github/workflows/apps.yml` | `ihr_access` in the plugin test labels |
| docs | `docs/UPSTREAM.md` after-sync step, `TENANCY_DESIGN.md` §6.3 note, `CLAUDE.md`, TRACKER test credentials (usernames only) |

No file under `apps/` changed, so there is no `UPSTREAM_PATCHES.md` entry.

### Deviations from the design

1. **`ihr_records_grant` is built in b1, not b4.** The trigger and the read-time rule both read
   it (§5.1, "After the attack" §3.1), so the table (patient, user, reason, reference,
   created/expires/ended) ships now with no endpoint, beat or end routine; b4 adds those and any
   further columns by migration. Its foreign keys are PROTECT (a grant is part of the record).
2. **`hospital_tables.txt` is unchanged.** The table takes the hospital default (`dml`), which is
   what §12 asks for. Listing it explicitly would make every hospital not yet migrated a
   classification gap during a release. b4 should decide whether the app may DELETE grants (it
   should only insert and end them); that needs a new class in `db_roles.CLASSES`.
3. **Field-list grammar.** Lines are `<path> <verb>  # reason` with verbs `keep`, `drop`,
   `patient`, `encounter`, `location` (re-project an embedded body with that shape's list),
   `identifiers` and `permissions`. A kept container must name its nested keys; `a.* keep` keeps
   a reviewed **staff, place or configuration card** whole (user cards, the facility card, the
   identifier configuration, a device's vendor contacts). No patient or clinical container uses
   a wildcard. Nested keys a list does not name are removed; nested `drop` keys are kept as
   `null`.
4. **Fields §5.2 names that CARE's specs lack.** `class_history`, `version` and `age` named in §5.2 are
   not fields of CARE's specs at this pin (`version` is set in the mapping and discarded by
   `model_construct`), so they are not in the lists; the tests hold the lists to CARE's actual
   `model_fields` (union over the shape's class and all its subclasses).
5. **Location parent.** A location's `parent` body is re-projected (`parent location`): CARE
   caches it in the database for 15 days (`FacilityLocation.get_parent_json`), so a parent
   cached from a doctor's request would otherwise carry its occupant to a restricted user.
6. **Identifier allow-list.** `fields/identifier_configs.txt` holds CARE's two systems in use
   (`…/patient-phone-number`, `…/patient-name`). UHID/MRN and ABHA number and address join when
   their configurations ship (core-in pack, P2 registration, ABDM M1) with their exact system
   strings; until then a hospital-made UHID config is hidden from the restricted roles (fail
   closed).
7. **Patient edit list** (`fields/patient_edit.txt`, verbs `edit|hidden|visible`) ships with its
   drift test now; P1-T11b2 uses it. Every field the patient list drops is `hidden` there (a test
   checks it).
8. **Mutation rows.** b1 adds the rows whose tests exist in b1. `access-guard-install` is caught
   by `ihr_access.E022` before any test runs, so its row carries that check's message as its
   signature (as `public-route-swap` does). The canary-based rows of §9/§10 (and `records-*`
   beyond the two here) come with b2–b4.
9. **Keycloak.** `DEMO_STAFF` has the eight new people (TOTP pre-set, linked in both hospitals),
   seeded live (see "Checks"). Their passwords and keys are only in the git-ignored
   `deploy/seed/TEST_USERS.md`.
10. **The identifier allow-list on search and writes is built in b1** (review S2, second
    review). "After the attack" §2.4 puts `update_identifier`, `search` and `search_retrieve`
    under the allow-list. `ihr_access/identifiers.py` wraps CARE's `PatientViewSet.search` and
    `update_identifier` (on the class, so b2's swap subclass inherits them; E022 checks every
    subclass): a restricted user naming a configuration whose system is not on
    `fields/identifier_configs.txt` gets 403 from search (nothing searched, the same answer
    whether or not the number matches) and 400 from `update_identifier` (before the patient is
    read). A missing, malformed or absent configuration (`phone_number` search) is left to CARE.
    `search_retrieve` names no configuration (phone number, year of birth, start of the id); its
    answer is a patient body, which the guard projects. Tests
    `test_patient_user.IdentifierAllowListOnSearchAndWrites`, mutation row
    `access-identifier-write-search`. The general search gate (who may search at all, Q-K40)
    stays with b2.
11. **The PatientUser rule keys on "restricted", not "restricted only"** (review S1). "After the
    attack" §3 says "holds only restricted roles"; taken literally, a front-desk worker who also
    holds any other non-clinical role (CARE's role-organisation "Member", a hospital's own role)
    kept every stale care-team row, so a former Nurse could still read diagnoses. The read-time
    rule, the patient list, the trigger and `ihr_patient_user_check` now all use
    `state.restricted` (one of our restricted roles and no clinical slug through any
    membership). Q-K35 holds: a clinician is never restricted.

### The rules as built

- **Restricted** (guard): the user holds one of `RESTRICTED` in this hospital (facility or
  government/role organisation) and no `CLINICAL_SLUGS` through any membership; a superuser
  never is. The PatientUser rule, the patient list, the trigger and `ihr_patient_user_check`
  key on it too (deviation 11). **Restricted-only** (every role held is in `RESTRICTED`) is kept
  in `state` for tests and the mutation `access-patient-user-gate` only. Both are computed once
  per request (`state.RequestState`), never cached across requests.
- **When a shape applies**: patient, encounter, location, device when restricted or forced;
  booking and token only when forced (`state.force_projection()`, for b2's branches). A patient
  under the user's own live grant is not projected, nor its encounters.
- **Request state** is set in DRF's `perform_authentication` (after the audit actor), so batch
  sub-requests each start afresh; Django's `request_started`/`request_finished` clear it; outside
  a request the guard is off.
- **PatientUser rule** (deviation 11): for a restricted user, a role that reached
  `find_roles_on_patient` only through a `PatientUser` row survives only if it is a restricted
  role (a role they also hold through a membership stays: it holds no clinical slug); the
  hidden role survives only under a live grant (for anyone). The patient list of a restricted
  user is CARE's organisation branch (patients of an organisation where a role of theirs holds
  `can_list_patients`: none of ours can be there) plus the patients of their own live grants,
  never CARE's `users_cache` branch.
- **Trigger** (`SQLSTATE 23514`, Django `IntegrityError`): on INSERT and on UPDATE OF role,
  person, organisation/patient and `deleted`; soft-deleted rows and hard deletes pass. A
  `PatientUser` row other than the hidden role is refused for a restricted person (the same
  test as `state.restricted`, in SQL over the memberships and their role permissions; a role
  permission or permission that is `deleted` or `temp_deleted` is not counted, as CARE's own
  lookups skip `temp_deleted`: second review of S1). Because
  Django's `save()` names every column, any full save of a stale row of a restricted person
  (other than marking it deleted) is refused, which is intended. Over the API a refusal answers
  **400 with the trigger's reason** (review C3): `ihr_access/exceptions.py` wraps CARE's
  `emr_exception_handler`, which every view that writes these rows uses (organisation users,
  `add_user`; E022 checks the wrap); any other `IntegrityError` is still a 500, and so would be
  a refusal through a non-EMR view (none writes these rows at this pin). CARE's batch and
  password views import the handler by name; `install()` rebinds those names too, and E022
  fails if any other `care.*` module holds the unwrapped handler (second review of C3; at this
  pin both modules are first imported after the wrap, so the rebinding is for a module imported
  earlier: `test_a_handler_bound_by_name_before_the_wrap_is_rebound`, mutation
  `access-placement-batch-handler`). A
  refusal inside a batch is the sub-request's own 400 (its EMR view handles it), and the whole
  batch is rolled back (`PlacementRefusalsOverTheApi.test_a_refusal_inside_a_batch_answers_400_not_500`,
  with a membership route listed for the test only; none is on `batch_allowlist.txt`).
- **Keeping the trigger current** (reviews C1, C2): `placement.fingerprint()` (its SQL and the
  registry lists it is written from) must equal `PLACEMENT_SHA256` in the latest ihr_access
  migration (`0002_placement_fingerprint`, no operations); a test fails otherwise, so a change
  migrates every hospital and post_migrate reinstalls the guard. `problems()` also compares the
  installed trigger's events and columns with this build's, and `ensure()` re-creates all three
  triggers every time it runs (mutations `access-placement-trigger-shape`,
  `access-placement-ensure-replaces`, `access-placement-fingerprint`,
  `access-placement-fleet-repair`). `ihr_fleet_run check` names a hospital whose guard is missing,
  disabled or changed (`<slug>: role placement guard: …`, report only, detail
  `placement_problems`); `ihr_fleet_run migrate` puts it back for a hospital already current
  (`<slug>: role placement guard repaired`), before and whatever the outcome of its grant
  repair (a hospital whose grants cannot be repaired is held and still gets its guard back).
  **Alert (second review of C1):** the guard is a hospital control of the run-time role check
  (`db_roles.check_connection`, `placement.runtime_problems_by_schema`): an app process
  (`IHR_DB_ROLES=required` or `test`) that starts while a hospital's guard function is missing
  or changed since it was installed, or one of its triggers is missing, disabled, pointing
  elsewhere or not BEFORE ROW on INSERT and UPDATE (or, for this build's guard, on other
  columns), does not serve that hospital and writes `ihr-db-role: gated schema=<schema>
  reason=controls` (the worker every minute), so DatabaseSchemaGated and HospitalNotServed
  fire. An older build's guard, unchanged as installed, is not gated (as the sealing
  functions): the hospital is behind and not served anyway, and an archived one must not
  alert. Mutation rows `access-placement-gate`, `access-placement-fleet-repair`.
- **`ihr_patient_user_check`** (reviews S1, C5): lists `stale` rows (any row but the hidden role
  of a restricted person; exit 1) and, apart, `grant-ended` hidden-role rows (exit 0; the grant
  end routine of b4 removes them). A hidden-role row under a live grant is never listed.

### Checks

| Run | Result |
|---|---|
| b1 suites only (`ihr_roles ihr_access`, `IHR_DB_ROLES=test`) | 64 tests OK (37.8 s; 2 m 39 s with the test database) |
| Plugin suites, `IHR_DB_ROLES=test` (the nine labels, `ihr_access` added) | `Ran 805 tests in 773.904s` `OK (skipped=45)`; Redis audit 671 keys, all tenant-prefixed. The first run, before the restore allow-list change, ran 742 with 3 `setUpClass` errors in `test_restore` |
| Plugin suites, roles off | `Ran 771 tests in 755.072s` `OK (skipped=49)`; Redis audit 671 keys, all tenant-prefixed (first run: 708, the same 3 errors) |
| CARE modules in tenant mode, roles off as in CI's tenant job (patient, encounter, location, device, booking, token, account, roles, identifier configs, schedule, managing organisations) | `Ran 419 tests in 22.538s` `OK` |
| `scripts/isolation-mutation.sh check` | 102 rows, every target and named test found |
| The 14 new mutations, applied locally on a scratch copy (5 runs) | each named test FAILED (`access-guard-install`: E022 stopped the run with its message) |
| `python3 scripts/check-logs.py` | `Log pipeline OK.` (92 checks; b1 adds no log line) |
| `pre-commit run --all-files` | 43 hooks passed |
| Keycloak: `care-dev.sh keycloak up`, `keycloak seed`, `scripts/keycloak-test.sh` | the eight people created and linked in Alpha and Beta (8 staff links each); live tests `Ran 45 tests in 211.149s` `OK`; Keycloak stopped again afterwards |
| After the review fixes (S1, C1–C7; 2026-11-15): plugin suites, `IHR_DB_ROLES=test` | `Ran 819 tests in 789.022s` `OK (skipped=45)`; Redis audit 691 keys, all tenant-prefixed |
| After the review fixes: plugin suites, roles off | `Ran 785 tests in 772.324s` `OK (skipped=49)`; Redis audit 691 keys, all tenant-prefixed |
| After the review fixes: CARE modules in tenant mode (the eleven above plus facility organisations and organisations) | `Ran 468 tests in 24.672s` `OK` |
| After the review fixes: the 11 new mutation rows (S1, C1, C2, C3, C5, C6, C7) and the 3 changed ones (`access-patient-user-roles`, `access-patient-list`, `access-restricted-patient-user`), each applied alone on a scratch copy | each named test FAILED |
| After the review fixes: `check-logs.py`, `pre-commit run --all-files` | `Log pipeline OK.`; 43 hooks passed |
| After the second review (S2, C1 alert and repair order, C3 batch, S1 temp_deleted; 2026-11-15): plugin suites, `IHR_DB_ROLES=test` | `Ran 827 tests in 834.835s` `OK (skipped=45)`; Redis audit 694 keys, all tenant-prefixed |
| After the second review: plugin suites, roles off | `Ran 792 tests in 775.751s` `OK (skipped=49)`; Redis audit 694 keys, all tenant-prefixed |
| After the second review: CARE modules in tenant mode (the thirteen above plus token category, queue and sub-queue) | `Ran 590 tests in 31.234s` `OK` |
| After the second review: the 5 new mutation rows (`access-placement-gate`, `access-placement-batch-handler`, `access-identifier-write-search`, `access-placement-temp-deleted`, `access-restricted-temp-deleted`) and the changed `access-placement-fleet-repair`, each applied alone | each named test FAILED; `isolation-mutation.sh check`: 107 rows |
| After the second review: `check-logs.py`, `pre-commit run --all-files` | `Log pipeline OK.`; 43 hooks passed |
| Manual, Alpha (dev stack, `APIClient` signed in as each seeded user) | see below |

**Manual check (Alpha, after `care-dev.sh migrate-hospitals` and `seed`).** Account
`/api/v1/facility/<id>/account/<id>/` (it embeds the full patient and primary encounter):
- `care-doctor`: blood group `AB_negative`, `extensions {"core": {}}`, the encounter's
  department listed: CARE's body unchanged;
- `care-receptionist`, `care-cashier`, `care-tpa`: same patient name and phone, blood group
  `null`, `extensions {}`, encounter departments `[]`, the phone-based identifier only; 23 patient
  keys, as for the doctor;
- `care-owner`, `care-labtech`: 403 (no account rights).

`/api/v1/patient/<id>/`: Receptionist 404, Doctor 200; patient list: Receptionist empty. Placing
`care-labtech` at the facility root: refused by the trigger ("Lab Technician is held in a
department, never at the facility root").

### Known limits (for b2–b4)

- No widening yet: the front desk cannot open a patient or list encounters (b2's swaps).
  Search (`patient/search`) is still open to every signed-in user as in CARE (Q-K40; b2's gate),
  except that a restricted user cannot search by an identifier off the allow-list (deviation 10).
- **Bed and room-rent billing for the Cashier and TPA Desk** (review C4; Q-K37a, awaiting the
  owner). Q-K37 A drops `current_location`, `location_history` and `care_team` from every
  encounter these roles see, including the account's `primary_encounter`. CARE's account screen
  builds its bed-charge table only from `location_history`, so these two roles see "no
  locations" there and cannot add a room-rent charge from it, and the printed bill shows no
  ward, bed or treating doctor. Pinned by
  `test_guard.ThroughTheApi.test_billing_roles_see_no_bed_on_the_account_for_now`. The owner's
  answer decides: a per-role encounter list that keeps the bed (`id`, `name`) and its dates for
  these two roles (b2), or a P1-T11c billing screen that groups bed charges from the
  charge-item list.
- The guard is off outside requests (Celery, commands), as designed; the account-report PDF is
  the one such path and b2 closes it at the report swap (Q-K41).
- A location's parent cached by a restricted request is stored projected (only its occupant
  differs, and a parent is a "kind" location that holds no encounter in CARE).
- `RecordsGrant` rows may be deleted by the app (default class), see deviation 2.
- `ihr_patient_user_check` runs per hospital schema (`tenant_command`); no fleet-wide wrapper
  or alert yet.
- The placement guard is judged when an app process starts (its first connection), as the
  sealing functions are: a guard broken while the processes run is named by the next
  `ihr_fleet_run check` (report only) and gates the hospital only after the app containers
  restart; the audit watch does not probe it. With `IHR_DB_ROLES=off` (dev) nothing gates.
  A guard removed whole (`DROP FUNCTION ... CASCADE` takes the triggers with it) looks like a
  schema ihr_access has not reached yet, so it is not gated either; `ihr_fleet_run check`
  names it and migrate puts it back. Only the owner can do either.
- There is no admin guide yet: where each role is placed (§4), that a stale `PatientUser` row
  of a restricted person is to be removed, and that the trigger refuses the wrong places.
  It belongs with ADR-018 (b4) or the P1-T11c screens.

### Open items handed to b2–b4

- b2: (review C4) the bed projection for the billing roles if the owner answers Q-K37a with B.
  When b2 widens `update_identifier` for the front desk, the allow-list check of
  `ihr_access/identifiers.py` already runs first (deviation 10).
- b2: the four swaps and `force_projection()` calls; the search gate; `add_user` gate; patient
  edit rule using `fields/patient_edit.txt`; encounter filter allow-list
  (`fields/encounter_filters.txt`); device, report-upload and delivery swaps; the named write
  tests; the remaining mutation rows (`access-forced-projection`, `access-search-gate`,
  `access-add-user-gate`, `access-bed-history`, `access-resource-request`,
  `encounter-create-fields`, `access-encounter-filters`, `access-retrieve-scope`,
  `access-update-action-allowlist`, `access-edit-hidden-fields`, `access-account-report`,
  `access-delivery-patient`, `access-booking-note`).
- b3: the matrix reuses `ihr_access.testing.build_world()` (extend it: lab and ward copies of
  requests and reports, a resource request, a return order, an account report, a world-coverage
  check) and the canary word `testing.CANARY`; the spec walk's exemption list
  (`PatientOTPReadSpec`, `PatientPartialSpec`).
- b4: the grant endpoint, `records.end_grant`, the beat and per-request expiry, audit
  annotation, the signed-link cap, the grant authorizer, Owner totals, ADR-018, the
  click-through; decide the grant table's privilege class.

## P1-T11b2 as built (2026-11-15)

**What exists now.** Ten CARE viewsets are swapped for ours (`route_swaps.SWAPS`, guarded by
`ihr_platform.E020`). The front desk (Receptionist, Cashier, TPA Desk, MRD) opens any patient of
the hospital and the encounter status view through our `ihr_*` slugs, always projected; the
Receptionist edits demographics under the edit rule and starts visits with a department. Owner,
Lab Technician and Store Keeper cannot search or open patients. Bed history, resource requests,
devices, account reports and dispense orders are refused to restricted users, and patient-linked
delivery orders and supply deliveries are absent for them (and cannot be made by them). Patient
writes by restricted users are decided before CARE reads the body. The review of the uncommitted
b2 change (11 findings, "Review fixes" below) is folded into this section. CARE's own roles get CARE's answers (CARE's own test
modules below pass, apart from five file-storage round trips that fail the same way without b2).

### The swaps and gates (each confirmed in `apps/backend/care` at this pin)

| CARE class (route) | Ours (`ihr_access/views.py`) | What changes, for whom |
|---|---|---|
| `emr/api/viewsets/patient.py` `PatientViewSet` (`patient/`) | `IhrPatientViewSet` | `get_object`: CARE's queryset first; on 404, a holder of `ihr_view_patient_demographics` gets the patient for `retrieve`, `update`, `partial_update`, `update_identifier` (`fields/patient_actions.txt` "widened"), projected; any other detail action answers 403 to them. `authorize_update`/`handle_update`: without CARE's `can_write_patient_obj`, `ihr_edit_patient_demographics` edits under `edit.py` (on `update_identifier`, only identifier-list systems, also for a user who is not restricted); anyone else served by our branch, and every restricted user (CARE's `upsert` too), is authorized before CARE validates the body. `clean_create_data`: a restricted user without `can_create_patient` gets 403 before validation; the Receptionist's identifiers off the list and `extensions` are dropped unread. `get_serializer_retrieve_context`: on our branch `?facility` is only checked to exist (CARE's facility-tag check is skipped; the projection drops facility tags). `initial()`: the search gate on `search`/`search_retrieve`; `add_user` refuses a restricted target (403); `get_appointments`/`get_tokens` need `?facility` for restricted users (else 403, unless the patient is under their own live grant), projected |
| `emr/api/viewsets/encounter.py` `EncounterViewSet` (`encounter/`) | `IhrEncounterViewSet` | `list` with `?patient`: CARE's path when `can_view_patient_obj`; else, with both our slugs, that patient's encounters in facilities where the user holds `ihr_view_encounter_status`. With `?facility`: CARE's path when the user holds `can_list_encounter` there; else the encounters whose departments (or bed's) overlap the organisations where they hold the status slug; the `?patient` branch uses the same unit filter. Our branch: parameters off `fields/encounter_filters.txt` → 400, `IhrProjectedEncounterFilters` (CARE's allowed filters, no tag filter), projected. `retrieve` only: CARE's `authorize_retrieve` first, then both slugs in the encounter's facility and the status slug in one of its units (its departments' or bed's organisations, as CARE's `can_view_encounter_obj`), projected. `create`: CARE's check; for restricted users every field of CARE's create spec is classified in `fields/encounter_create.txt`: `clinical` (`discharge_summary_advice`, `hospitalization.discharge_disposition`, `diet_preference`) or an unclassified field with a value → 400, `hidden` (`extensions`, `meta`) dropped |
| `emr/api/viewsets/location.py` `FacilityLocationEncounterViewSet` (`facility/{id}/location/{id}/association/`) | `IhrLocationEncounterViewSet` | every action 403 to restricted users (each row embeds the whole encounter; CARE lists a place's history to anyone holding `can_list_facility_locations` there, e.g. the Lab Technician at a lab place) |
| `emr/api/viewsets/resource_request.py` `ResourceRequestViewSet`, `ResourceRequestCommentViewSet` (`resource/`, `resource/{id}/comment/`) | `IhrResourceRequestViewSet`, `IhrResourceRequestCommentViewSet` | every action 403 to restricted users |
| `emr/api/viewsets/device.py` `DeviceViewSet` (`facility/{id}/device/`) | `IhrDeviceViewSet` | restricted users: list empty, every other action 403 |
| `emr/api/viewsets/report/report_upload.py` `ReportUploadViewSet` (`template_reports/`) | `IhrReportUploadViewSet` | restricted users: account-report list, retrieve and archive 403; `generate` 403 for any template (Q-K41) |
| `emr/api/viewsets/inventory/delivery_order.py` `DeliveryOrderViewSet`, `inventory/supply_delivery.py` `SupplyDeliveryViewSet` | `IhrDeliveryOrderViewSet`, `IhrSupplyDeliveryViewSet` | restricted users: rows with `patient` / `order__patient` set are filtered out of every queryset, and CARE's `upsert` (which finds rows by id) answers 404 for them; a create (POST or upsert) of an order with a `patient` is 403, of a delivery against a patient-linked order 404, before CARE validates (Q-K43) |
| `emr/api/viewsets/inventory/dispense_order.py` `DispenseOrderViewSet` (`facility/{id}/order/dispense/`) | `IhrDispenseOrderViewSet` | restricted users: list empty, every other action 403 (each order names its patient, and CARE opens it to holders of the store's supply-delivery permissions at the location, which the Store Keeper holds; Q-K43 A) |

### Files

| Where | What |
|---|---|
| `ihr_access/views.py` | the ten swapped classes above, the action, filter and create lists' readers, `filter_problems()`, `create_problems()` |
| `ihr_access/branches.py` | `holds()` (for our three slugs it first asks `may_hold()`: this request's cached roles against the roles granting our slugs, one query per request), `organizations_holding()`, `facilities_holding()` (CARE's own membership counting), `restricted()`, `widen()` (the one place our branches force the projection), `search_gate()`, the refusal line |
| `ihr_access/edit.py` | the edit rule (`reduce`, `DemographicsSpec`) and the district-move rule |
| `ihr_access/fields/encounter_filters.txt`, `encounter_create.txt`, `patient_actions.txt`, `encounter_actions.txt` | reviewed lists (each line with its reason) |
| `ihr_access/care_copies.txt` | sha256 of the CARE code our swaps copy (`EMRRetrieveMixin.retrieve`, `EMRUpdateMixin.handle_update`, `EncounterViewSet.get_queryset`, `EncounterAccess.get_filtered_encounters` and `can_view_encounter_obj`) |
| `ihr_access/identifiers.py` | `off_list()` (shared by search, `update_identifier`, the edit rule and the patient create) |
| `ihr_access/membership_only.txt` | the 14 CARE viewsets that read organisation memberships, each classified `membership`, `permission` or `swapped` ("After the attack" §7.4) |
| `ihr_access/checks.py`, `apps.py` | new system check `ihr_access.E024`: every filter of CARE's `EncounterFilters` and every field of `EncounterCreateSpec` is classified (E023 stays reserved for b4's grant authorizer) |
| `ihr_platform/route_swaps.py` | ten entries added to `SWAPS` |
| `ihr_access/tests/base.py`, `test_patient_swap.py`, `test_encounter_swap.py`, `test_further_swaps.py`, `test_writes.py` | 31 new tests (below) |
| `ihr_access/tests/test_guard.py`, `test_patient_user.py` | two b1 assertions changed with the intended behaviour: the Owner now gets 403 on a device (the guard is still checked on the device spec, so `access-device-encounter` still fails that test), and the Receptionist's `update_identifier` with an allowed identifier now writes (200) |
| `scripts/isolation-mutation.sh` | 25 rows (below) |
| `docs/UPSTREAM.md`, `CLAUDE.md` | the after-sync step for the swaps; the swaps in the project notes |

No file under `apps/` changed (no `UPSTREAM_PATCHES.md` entry); no migration, no new table;
`batch_allowlist.txt` unchanged (the swaps change classes, not routes).

### Deviations from the design

1. **Named tests for the matrix and canary rows.** `access-bed-history` and
   `access-resource-request` name b2 tests (`test_bed_history_refused_to_restricted`,
   `test_resource_requests_refused_to_restricted`), and `access-delivery-patient` /
   `access-booking-note` name `test_store_keeper_never_sees_a_patient_return` /
   `test_receptionist_patient_appointments_are_projected`, because the role matrix and the canary
   walk are b3's. b3 may add its tests to these rows.
2. **Encounter create answers 200**, CARE's status (§10 test 9 says 201).
3. **`patient_filter` is allowed** on our encounter branch. The design's list names `patient`
   (the parameter `get_queryset` reads, also allowed); CARE's `patient_filter` filter narrows to
   one patient the user can open anyway and the web app's encounter history sends it. The range
   filter `created_date` is accepted as `created_date_after` / `created_date_before` (derived
   from the filter's widget).
4. **Both slugs on every encounter branch.** The `?facility` branch and retrieve also require
   `ihr_view_patient_demographics` (each row embeds the patient); every role that holds the
   status slug holds both (fail closed).
5. **403, not 404, on non-widened patient actions** for holders of
   `ihr_view_patient_demographics` (test 4 asks 403; they can open the patient anyway). Owner,
   Lab Technician and Store Keeper keep CARE's 404 on a patient.
6. **Report generation refused wholesale** to restricted users (every template type): the
   patient, encounter and discharge-summary reports are closed by CARE's authorizers anyway and
   an instance template has no facility check (§7.1's two cases in one rule).
7. **Delivery `upsert` guarded too**: CARE's upsert finds a row by id without `get_queryset`;
   for restricted users a patient-linked row answers 404 inside it.
8. **The search gate also projects**: a user of ours who passes only through
   `can_create_patient` or `ihr_view_patient_demographics` (no `can_list_patients`) is served
   CARE's search projected (§5.3's "results go through the guard", extended to mixed users).
9. **Identifiers in an update body** whose configuration is off the allow-list are dropped
   unread (as hidden fields are), not refused; an empty value for one (which CARE reads as
   "delete") is dropped too. `update_identifier` keeps b1's 400.
10. **Devices**: every non-list action is refused to restricted users, not only retrieve.
11. **"Restricted"** (one of our restricted roles and no clinical slug, b1 deviation 11) keys
    every gate here, not "holds only restricted roles".
12. **Billing denials as CARE has them**: invoices and payments have no destroy route; their
    "destroy" is a cancel after CARE's free period (`can_destroy_*`), which is what the test
    sends. A negative charge is not refused by CARE (it computes the charge without the negative
    part unless the user holds `can_create_negative_charge_item`), so the test checks that
    authorizer for each role.
13. **Moving a district**: "the old one's state" is the top of the organisation chain; the PIN's
    district is looked up in India Post's directory (`PostOffice`) and the hospital's district
    organisation (`ihr_tenant_settings.geography.find`). The refusal line has no alert.

### The rules as built

- **Our branch** (patient retrieve and edits, encounter list and retrieve, a patient's bookings
  and tokens with `?facility`, a projected search) runs only where CARE's own path gives the user
  nothing, never adds rows to CARE's, and calls `branches.widen()`: everything shown in the
  request is projected, also for a user who is not restricted (Q-K35).
- **Patient scope** (Q-K34 A): the hospital's whole register, for holders of
  `ihr_view_patient_demographics` in any department or the root.
- **Edit rule**: the body is reduced to the `edit` fields of `fields/patient_edit.txt` (written
  after CARE's validators, by `DemographicsSpec.de_serialize`, which writes nothing else);
  `hidden` fields are ignored; a changed `visible` field (the date of death) is 400, an unchanged
  one ignored; a district move passes only within the old state or to the PIN's district and when
  the new chain gives nobody, through a government organisation, one of `can_list_patients`,
  `can_view_clinical_data` or `can_view_questionnaire_responses` that the old chain did not
  already give them (compared per person and slug); a patient with no district moves only to
  the PIN's district, on the same terms (else 403 and `ihr-access: refused geo_move
  hospital=<slug>` on standard error).
- **Writes are decided before CARE reads the body** for restricted users and on our branch:
  CARE validates a patient body (a programme number's "not unique" included) before it
  authorizes, so update, partial update and upsert by id are authorized first, and a create by
  a restricted user without `can_create_patient` is refused first; a 400 never says whether
  someone holds a programme number. A registration keeps to the identifier list (others
  dropped unread, as in an edit) and drops `extensions`; tags stay with CARE's
  `can_apply_tag_config` (403, nothing made).
- **Encounter filters on our branch**: only parameters marked `allow` (and their range suffixes);
  `location`, `organization`, `care_team_user`, `tags`, `tags_behavior` and anything unlisted
  answer 400. CARE's path (CARE's list rights; MRD's grant, b4) keeps CARE's filters.
- **Search gate** (Q-K40): CARE's roles only, unchanged; a user of ours needs
  `can_list_patients`, `can_create_patient` or `ihr_view_patient_demographics` (403 otherwise).
- **`add_user`**: a restricted target is refused (403) before CARE writes; the placement
  trigger still refuses the row (400) if the gate is ever bypassed.
- **Q-K37a stays at option A** (no bed for the Cashier or TPA Desk), pinned by
  `test_guard.ThroughTheApi.test_billing_roles_see_no_bed_on_the_account_for_now` (unchanged).

### Tests added (31, plus 13 from the review fixes below)

`test_patient_swap`: opening any patient projected (front desk 200, Owner/Lab/Store 404,
Doctor full); the whole register (Q-K34); `test_receptionist_cannot_add_user_or_tag` (test 4);
`test_add_user_refuses_restricted_target`; create and update by role (§9 test 9);
`test_actions_are_classified`; `test_receptionist_save_keeps_hidden_fields` (test 5, the
web app's exact body plus a hostile one); `test_the_date_of_death_is_clinical` (test 5);
`test_receptionist_updates_an_allowed_identifier`; `test_moving_a_patients_district` (test 6);
`test_search_refused_to_roles_without_patient_access` (§9 test 12);
`test_mixed_roles_branch_is_still_projected` (§9 test 13);
`test_receptionist_patient_appointments_are_projected` ("After the attack" §5.2).
`test_encounter_swap`: the status view by patient and facility; `test_restricted_encounter_filters_refused`
(test 7); `test_mixed_roles_on_the_facility_list_get_cares_rows_only` (test 8); actions and
filters classified; `test_encounter_organizations_refused`; restricted users cannot change an
encounter; `test_receptionist_cannot_write_discharge_advice` (§9 test 9);
`test_receptionist_starts_a_visit_with_a_department` (test 9, signed in through the real
session cookie; the `EncounterOrganization` insert is in `ihr_audit_entry`).
`test_further_swaps`: `test_bed_history_refused_to_restricted`,
`test_resource_requests_refused_to_restricted`, `test_devices_are_closed_to_restricted`,
`test_account_report_refused_to_restricted` (test 10), `test_store_keeper_never_sees_a_patient_return`
(test 11, with a batch PUT and an upsert), `test_membership_only_lists_are_reviewed` (§7.4).
`test_writes`: clinical writes (allergy, note thread, patient file) 403 for every restricted
role; charge create (Cashier 200, TPA Desk 403); `test_billing_denials`.

### Mutation rows (13, plus 12 from the review fixes)

`access-forced-projection`, `access-search-gate`, `access-add-user-gate`,
`access-update-action-allowlist`, `access-edit-hidden-fields`, `access-booking-note`,
`access-encounter-filters`, `access-retrieve-scope`, `encounter-create-fields`,
`access-bed-history`, `access-resource-request`, `access-account-report`,
`access-delivery-patient`. Review fixes: `access-update-authorize-first`,
`access-create-authorize-first`, `access-create-identifiers`, `access-update-identifier-branch`,
`access-geo-move-slugs`, `access-geo-move-no-district`, `access-patient-facility-context`,
`access-encounter-unit-list`, `access-encounter-unit-scope`, `access-dispense-order`,
`access-delivery-create-patient`, `access-delivery-create-order`.

### Checks

| Run | Result |
|---|---|
| Plugin suites, `IHR_DB_ROLES=test` (the nine labels) | `Ran 859 tests in 791.878s` `OK (skipped=45)`; Redis audit 752 keys, all tenant-prefixed |
| Plugin suites, roles off | `Ran 824 tests in 781.300s` `OK (skipped=49)`; Redis audit 752 keys, all tenant-prefixed |
| CARE modules in tenant mode, roles off (account, booking, device, encounter, facility organisation, location, managing organisations, organisation, patient, patient identifier, roles, schedule, token, token category, queue and sub-queue; and for the swapped or tested viewsets: resource request, delivery order and its concurrency test, supply delivery, supply request, allergy, notes, file upload, charge item, invoice, payment reconciliation) | `Ran 979 tests in 81.444s` `FAILED (failures=1, errors=4)`: the five are `test_file_upload_api`'s storage round trips, which cannot resolve the `minio` host from the one-off test container; the same module on the committed b1 code (`git archive HEAD`) gives the same `FAILED (failures=1, errors=4)`, so not caused by b2. The other 974 pass. An earlier run of the nine swapped modules (patient, encounter, device, location, resource request, delivery order and concurrency, supply delivery, identifiers) alone: `Ran 348 tests` `OK` |
| `scripts/isolation-mutation.sh check` | 120 rows (107 + 13), every target and named test found |
| The 13 new mutation rows, each applied alone to a scratch copy and run as CI runs them (roles off) | each named test FAILED. First pass: 11 of 13; `access-add-user-gate` ended in an ERROR (the trigger's refusal broke the test's transaction) and `access-bed-history` survived (CARE itself refuses every restricted role at the world's bed). Fixed in the tests: the add-user requests run in their own transaction as deployed (as b1's placement tests do), and a lab place the Lab Technician can list was added; both then FAILED as required |
| `python3 scripts/check-logs.py` | `Log pipeline OK.` (92 checks; the new `ihr-access: refused geo_move` line has no alert and leaks nothing) |
| `scripts/batch-allowlist.py --check` | `batch-allowlist: 37 routes, list current` |
| `pre-commit run --all-files` (new files added with `git add -N` for the run, then reset) | 43 hooks passed (ruff check and format, logs-check, batch-allowlist, gitleaks, shellcheck …) |
| `manage.py check` in the dev backend | `System check identified no issues` (E020 with the nine new swaps, E022, E024) |

**Manual check (Alpha dev hospital, 2026-11-15, after restarting the backend and Celery; no new
migration, no re-seed; `APIClient` signed in as each seeded user through `manage.py shell`):**
- `care-receptionist`: phone search 200 (1 result); the patient opens 200 with blood group
  `null`, `extensions {}` and the same 23 keys as the Doctor's body; patient list 200, count 0;
  registering "B2 Manual Check" (synthetic) 200 with a projected answer (stored blood group
  `B_positive`, as entered at registration); the web app's save replayed from the projected
  GET 200 (address changed, blood group kept), and again with `blood_group: O_negative` 200
  (blood group still kept); a visit started under "General Medicine" 200 (answer
  `organizations: []`, stored with that department); encounter list `?patient` 200 with
  status-only rows, `?facility` 200 (11 rows, no care team), `?organization=` 400.
- `care-doctor`: the same patient 200 with blood group `AB_negative` (CARE's body).
- `care-owner` and `care-labtech`: search 403, the patient 404, encounters `?patient` 403.

### Review fixes (2026-11-15)

An independent review of the uncommitted b2 change raised 11 findings; each was challenged
against the code (verdicts below) and fixed with a regression test that fails without the fix,
and a mutation row where it is an access control. None needed an owner decision (no new Q-K).

| Finding | Verdict | Fix | Test (mutation row) |
|---|---|---|---|
| R1 (low after challenge): patient update opened to view-only holders (Cashier, TPA Desk, MRD) ran CARE's validators before the 403, a programme-number oracle | partly | `handle_update`: on our branch, and for every restricted user, `authorize_update` runs before CARE reads the body | `test_patient_swap.WritesDecideBeforeTheBody.test_update_is_refused_before_the_body_is_read` (`access-update-authorize-first`) |
| W1 (medium): the same oracle through create and CARE's upsert, for every restricted role | partly | the same line covers upsert by id; `clean_create_data` refuses a restricted user without `can_create_patient` before validation | `test_update_is_refused_before_the_body_is_read`, `test_create_is_refused_before_the_body_is_read` (`access-create-authorize-first`) |
| W4 (low): registration set programme identifiers and extensions | partly (tags refuted: CARE's `can_apply_tag_config` refuses them) | `clean_create_data` drops identifiers off the list and `extensions` (and `meta`) unread, for POST and upsert; tags pinned at 403 | `test_registration_keeps_to_the_identifier_list` (`access-create-identifiers`) |
| W2 (medium): a two-job user (Receptionist + Nurse) wrote or deleted programme identifiers through `update_identifier` on our branch | confirmed | `authorize_update`: on our edit branch, `update_identifier` with a configuration off the list is 400 whatever `restricted` says (`identifiers.off_list`, shared with the edit rule) | `EditingDemographics.test_two_jobs_keep_to_the_identifier_list` (`access-update-identifier-branch`) |
| W3 (low): the district-move rule compared people, not slugs, and skipped patients with no district | confirmed | `edit._privileged_grants` compares (person, slug) pairs; a patient with no district is placed only by the PIN and still gets the gained check | `test_a_district_move_compares_what_people_can_do` (`access-geo-move-slugs`), `test_a_patient_with_no_district_moves_on_the_same_terms` (`access-geo-move-no-district`) |
| C1 (medium): the web app's patient request (`?facility=`) answered 403 to Cashier, TPA Desk and MRD (CARE's facility-tag check) | confirmed | `get_serializer_retrieve_context`: on our branch the facility is only checked to exist (404 if unknown); no tag permission granted | `OpeningAPatient.test_the_web_apps_patient_request_with_the_facility` (`access-patient-facility-context`) |
| R2 (medium): dispense orders (each names its patient) open to the Store Keeper through the store's supply-delivery permissions | confirmed | `IhrDispenseOrderViewSet` (SWAPS): restricted users get an empty list and 403 otherwise; a static test requires every CARE viewset authorizing through `can_(list|write)_facility_supply_delivery` to be swapped | `PatientReturns.test_store_keeper_never_sees_a_dispense_order` (`access-dispense-order`), `test_every_supply_delivery_route_is_swapped` |
| W5 (low): the Store Keeper could create a patient-linked return order, or a delivery against one, and read the patient in the answer | confirmed | `_NoPatientRows.clean_create_data` (before CARE validates): an order with `patient` 403, a delivery against a patient-linked order 404 | `test_store_keeper_never_sees_a_patient_return` (`access-delivery-create-patient`, `access-delivery-create-order`) |
| R3 (low): encounter `?patient` list and retrieve ignored the unit where the status slug is held | confirmed | both now filter by the units (departments' or bed's organisations) where the user holds `ihr_view_encounter_status`, as CARE's `can_view_encounter_obj`; the "Scope asymmetry" limit is gone | `StatusList.test_a_front_desk_placed_in_one_unit_sees_that_unit_only` (`access-encounter-unit-list`, `access-encounter-unit-scope`) |
| C2 (medium): copies of CARE code with no drift check | confirmed; fixed in two passes | `fields/encounter_create.txt` classifies every create-spec field (E024; an unclassified field with a value is 400 to restricted users); `care_copies.txt` pins `EMRRetrieveMixin.retrieve` and `EMRUpdateMixin.handle_update`, and CareCopies asserts CARE's classes still inherit them (no override of their own). Second pass: also pinned `EncounterViewSet.get_queryset` (the select_related list `_rows` copies, and the `?patient` / `?facility` hand-over to CARE), `EncounterAccess.get_filtered_encounters` (`in_units`, the `?facility` branch) and `EncounterAccess.can_view_encounter_obj` (`_status_retrievable`); `_rows` now starts from CARE's own base queryset (`super(EncounterViewSet, self)`), and its SQL is compared with CARE's `get_queryset` for a retrieve | `test_the_create_list_classifies_cares_fields`, `CreatingAVisit.test_unclassified_and_hidden_fields_at_create`, `CareCopies.test_copied_care_methods_are_unchanged` (5 pins), `CareCopies.test_our_rows_are_cares_rows` |
| C3 (low): repeated authorization queries for CARE's roles | confirmed (the search claim refuted); fixed in two passes | the encounter `?patient` branch checks our slug first; the edit-branch decision is computed once per patient and request. Second pass (the 5 extra statements of a Doctor's save were CARE's `can_write_patient_obj`, run once by us and again by CARE): `branches.may_hold()` answers from this request's cached held roles (now shared with `state.restricted`) and one query for the roles granting our three slugs; `holds()` and `holds_in()` ask it first for our slugs, and the edit decision asks it before CARE's write check, so a user of CARE's roles only never reaches our membership queries or a second write check | `QueryBudget.test_a_doctors_encounter_history_and_patient_save` (warm, against CARE's own class: at most 1 extra statement on each request, measured 1) |

**Checks after the review fixes (2026-11-15, after restarting the backend and Celery; no
migration):**

| Run | Result |
|---|---|
| Plugin suites, `IHR_DB_ROLES=test` (the nine labels) | `Ran 873 tests in 810.568s` `OK (skipped=45)`; Redis audit 764 keys, all tenant-prefixed |
| Plugin suites, roles off | `Ran 838 tests in 785.855s` `OK (skipped=49)`; Redis audit 764 keys, all tenant-prefixed |
| CARE modules in tenant mode, roles off (the list above plus dispense order, its concurrency test and medication dispense) | `Ran 1056 tests in 87.781s` `FAILED (failures=1, errors=4)`: the same five `test_file_upload_api` storage round trips (`Failed to resolve 'minio'` from the one-off container) as on b1; the other 1051 pass |
| The 12 new mutation rows, each applied alone to a scratch copy of `plugins/` and run as CI runs them (roles off) | each named test FAILED (an assertion, not an error) on the first pass |
| `scripts/isolation-mutation.sh check` | 132 rows, every target and named test found |
| `python3 scripts/check-logs.py` | `Log pipeline OK.` |
| `scripts/batch-allowlist.py --check` | `batch-allowlist: 37 routes, list current` |
| `pre-commit run --all-files` (new files added with `git add -N` for the run, then reset) | 43 hooks passed |
| `manage.py check` in the dev backend | `System check identified no issues` (E020 with ten swaps, E022, E024) |

**Checks after the second fixer (C2, C3; 2026-11-15, backend and Celery restarted; no
migration):** plugin suites `IHR_DB_ROLES=test`: `Ran 874 tests in 809.052s` `OK (skipped=45)`;
roles off: `Ran 839 tests in 786.119s` `OK (skipped=49)` (Redis audit 764 keys, all
tenant-prefixed, both); CARE modules (the same 30): `Ran 1056 tests in 89.098s`
`FAILED (failures=1, errors=4)`, the same five `test_file_upload_api` storage round trips
(`Failed to resolve 'minio'`), the other 1051 pass; `isolation-mutation.sh check` 132 rows found;
`check-logs.py` `Log pipeline OK.`; `batch-allowlist.py --check` `37 routes, list current`;
`pre-commit run --all-files` passed.

### Known limits (for b3–b4)

- **One query of ours per request for CARE's roles** on the patient and encounter swaps (the
  roles that grant our three slugs, `branches.may_hold`; review C3). It is not cached across
  requests: role grants differ per hospital schema and change between requests
  (TENANCY_DESIGN §6).
- **Pinned CARE copies are checked by digest, not merged**: an upstream sync that changes one of
  the five methods in `care_copies.txt` fails `CareCopies` until our copy is re-read and the
  digest re-pinned (after-sync step in docs/UPSTREAM.md); a change elsewhere in CARE that alters
  what those methods mean (e.g. a new permission check called from them) is caught only if it
  changes their source.
- **Writes the front desk cannot read back**: none left on encounter create (extensions are
  dropped, tags refused by CARE's `can_apply_tag_config`). At patient create the Receptionist
  may still set the blood group (registration records it; pinned by
  `test_patient_create_and_update_by_role`) and a date of death (CARE's create spec has it; the
  edit rule refuses changing it later). Both are hidden from them afterwards.
- **CARE's own oracle for CARE's roles**: CARE's patient create and upsert validate before they
  authorize for every user; we decide first only for restricted users and on our branch. A user
  of CARE's roles (or a clinician who also holds a front-desk role) keeps CARE's order there.
- **CARE gap found on the way** (CARE's own roles; for P1-T11d with Q-K40): the delivery-order
  list without `origin`/`destination` returns every order of the hospital to any signed-in user
  (no check). Restricted users lose the patient-linked rows there through the swap above. (The
  bed-history list does check `can_list_facility_locations` on the place, which is why the Lab
  Technician and the Store Keeper reach their own department's places; the test seeds such a
  place so the swap's refusal is what the test sees.)
- The front desk's patient **list** stays empty; patients are found by search (as designed).
- Device history routes (location, encounter, service) are not swapped: CARE closes them with
  `can_list_devices` (b3 matrix rows).
- A cross-state district move needs the PIN directory loaded; without it only moves within the
  old state pass. The `geo_move` refusal line has no alert.
- The web-app screens (patient tabs, encounter history, edit form) are still P1-T11c's (Q-K45).

### Open items handed to b3–b4

- b3: the role × endpoint matrix and the canary walk should cover the ten swapped viewsets
  (bed history, resource requests, devices, account reports, delivery orders and supply
  deliveries rows already have b2 tests and mutation rows; b3 may add its own tests to those
  rows). `list_params.txt` needs `encounter` with `facility` and `patient`. The spec walk's
  exemption list should add `ihr_access.edit.DemographicsSpec` (a write spec with
  `__model__ = Patient`, never serialized). b2's fixtures (`SwapCase.other_patient`, the store,
  return order and pharmacist, the account report and template, the resource request, the bed
  association) can move into `build_world()`.
- b3 batch cases (test 20): the edit rule and the encounter refusals apply to batch
  sub-requests through the swap; only the supply-delivery batch PUT is tested in b2.
- b4: MRD with a grant: `get_appointments`/`get_tokens` without `?facility` are left to CARE for
  a granted patient, and the encounter list `?patient` takes CARE's path (`can_view_patient_obj`
  through the hidden role); the grant authorizer's check takes `ihr_access.E023`.

## P1-T11b3 as built (2026-11-15)

**What exists now.** Every route of a hospital's URL configuration is requested as each of our
eight roles (MRD without a records grant), the Nurse who moved to the front desk and still holds
a `PatientUser` row, and CARE's Doctor and Facility Admin, on a full test world, and every answer
is compared with the reviewed `ihr_access/tests/role_matrix.txt`; every non-GET method is sent,
with an empty body, as each of our roles whose write row says `deny`. A named marker in every
clinical field, and a reviewed exemption list, prove that no answer to one of our roles carries a
field it must not see. The matrix found **three leaks to the Radiologist and one latent guard
gap**, all fixed in `ihr_access` (below). No `apps/` file changed (no `UPSTREAM_PATCHES.md`
entry), no migration, no new table, `batch_allowlist.txt` unchanged.

### How the matrix enumerates, fills and classifies

- **Routes.** The isolation crawl's own `routes.py`: a new `get_route_views()` walks the same
  resolver with the same skips as `get_routes()` and also returns each route's methods (a
  viewset's actions, a class view's handlers; `None` for a function view). CARE's switched-off
  views (`Blocked`) are left out, as the isolation crawl does (ihr_identity tests them).
- **Path parameters.** `routes.fill()` unchanged, given a tenant-like namespace
  (`matrix.tenant_for`): for each model the world's first *named* row (`world.objects`, insertion
  order: the first patient, the inpatient visit, the bed, the ward copy of the request, the
  stock delivery order ...), else the first row by pk; the Doctor for `{username}` and users;
  the latest audit entry for `<int:pk>`; PIN 411001 (not loaded: `unavailable` for everyone).
- **Query and path variants** (`tests/list_params.txt`, item 12): one line per extra request of
  a route, `?name=object`, `?name~literal`, or `?{param}=object` (that path parameter takes
  another named row: the lab or imaging copy, the store, the Radiology patient, the return
  order ...). The bare route is always requested too. The row's key in `role_matrix.txt` is the
  route plus the line's assignments. 56 variant lines; 23 `none` lines. The static check
  (`ListParams`) requires a line for every list whose `get_queryset` or `list` reads
  `request.GET`/`query_params` (45 such lists at this pin) and refuses a line for no GET route.
- **Viewers.** Real staff sessions (`identity.staff_client` under `fake_keycloak`), so every
  view's own authentication runs; the write crawl uses the same sessions (a forced user made
  CARE's patient-OTP permission class raise 500s that no staff session can reach).
- **Each request runs in a savepoint that is rolled back**, so no answer (a token deleted, a
  favourite added) changes the next. The guard's `project_instance` is wrapped for the crawl:
  an answer in which it ran is `project`, else `allow`.
- **Outcomes** (item 13): `allow` / `project` (200 with rows: a non-empty `results`, list or
  body), `empty` (200, none), `deny` (401/403/404/405), `invalid` (400), `conflict` (409, a
  username already taken), `unavailable` (503, the PIN directory), `redirect` (3xx, the
  sign-in flow), `skip` (not requested). A 5xx matches no row. Rows are compact (item 17):
  `all`, `ours`, `restricted`, `frontdesk`, `billing`, `clinical`, `care` and per-role
  exceptions, later tokens winning; the moved Nurse is expected exactly as `rec`.
- **Every method** (item 14): `W` (every non-GET method), or `POST`, `PUT`, `PATCH`, `DELETE`
  rows; default `ours:deny care:care`. `deny` is sent with `{}` and must not answer 2xx or 5xx
  (403/404 preferred, 400 accepted); `allow` is not sent and must name tests that exist
  (`tests: a.b.C.test_x`, checked by `MatrixFiles`); `care` (CARE's roles) is never sent.
- **Key snapshot** (item 15, `role_matrix_keys.txt`): every key of every `allow`/`project`
  answer to a restricted role, nested dicts and lists of dicts walked (`a.b`, `a[].b`, `[].a`).
  A nested body of a guarded shape or a staff card (recognised by its keys) is one entry
  `a.b<patient>` (`<encounter>`, `<location>`, `<device>`, `<booking>`, `<user>`): the guard's
  field lists classify those exactly (`test_fields`). Sections `[<route key>] <roles>`: 1,874
  key paths in 94 sections. A new key, or one no longer seen, fails.
- **Canary** (item 18): `testing.marker(name)` is `CANARY-<name>`; the unnamed `CANARY`
  (`base`) is left only in the patient's extension and the visit's discharge advice. 46 named
  markers (notes, problem list, allergy, consent, prescription, dosage, request, statement,
  administration, answer, file, drawing, the bed, device service, resource request, account
  report, dispense, return, charge, booking note and tag, schedule, token note, tags, the
  programme number and its configuration, three copies of request, specimen, report and result,
  and the Radiology patient's). `canary_exemptions.txt` (`<who> <marker> <key glob>
  allow|never`): 24 `allow` lines, 31 `never` lines. Each `allow` line must be used, and have
  a negative row (the same marker for any role, or the same role and routes); each `never` line
  must be requested as its role, its marker must reach a care role on those routes, and no
  `allow` line may cover it. A positive control requires every marker to reach the Doctor or the
  Facility Admin somewhere. For the Owner no answer (any status) may hold either patient's name,
  phone number or id.
- **Spec walk** (item 16): every `EMRResource` subclass whose `__model__` is Patient or
  Encounter (our `edit.DemographicsSpec` included) carries the guard marker or is in
  `tests/spec_exemptions.txt` (`write`, `base`: never a viewset's read model and never
  `.serialize(`d in CARE's or our source, both checked; `exempt`: `PatientPartialSpec`,
  `PatientOTPReadSpec`, each with why).
- **World coverage** (item 19): every class the guard sits on (with its subclasses) is projected
  for a restricted role somewhere in the crawl, or is listed with why (`UNREACHED_SPECS`: the
  device spec, since devices are closed to our roles; the booking base, retrieve and OTP specs).
- **Batch** (item 20): Receptionist `PUT patient/{id}/` with a blood group (200, blood group
  unchanged, the address written), Receptionist and Cashier `PUT encounter/{id}/` (403 inside),
  Cashier `GET medication/dispense/{id}/` (403 inside, no marker or name); each asserts no
  `ihr-batch: refused` line; a labelled case (an encounter GET off the list is refused,
  `reason=not_listed`); the meta-test fails when `batch_allowlist.txt` gains a GET with no case
  in `BATCH_GET_CASES`.

### Files

| Where | What |
|---|---|
| `ihr_access/testing.py` | `build_world(full=False)`; `full` adds `add_matrix_world` (places at Laboratory, Radiology and Stores; one row of each clinical kind; ward, lab and imaging copies of request, specimen, report and result; a second patient with a Radiology visit and its problem list; a finished visit; account, charge, bill and payment; queue, category and token; the store with stock, a dispense, a stock order and delivery, the patient's return and its delivery; bed history (bed and a lab place), device histories, a resource request and comment, an account report and its template; a Pharmacist; the moved Nurse). `marker()`, `MARKERS`; schedule, booking note, tags and the programme configuration and number got markers of their own |
| `ihr_access/tests/matrix.py` | the crawl, the file readers, `tenant_for`, `classify`, `key_paths` |
| `ihr_access/tests/test_role_matrix.py` | `RoleMatrix` (7 tests), `AllowedWrites` (2), `MatrixFiles`, `ListParams`, `SpecWalk`, `BatchCases` (5) |
| `ihr_access/tests/role_matrix.txt`, `role_matrix_keys.txt`, `list_params.txt`, `canary_exemptions.txt`, `spec_exemptions.txt` | the reviewed files (263 GET rows, 105 write rows of which 31 `skip`) |
| `ihr_platform/tests/isolation/routes.py` | `get_route_views()` (the isolation crawl itself is unchanged) |
| `ihr_access/branches.py` | `ours_only()`, `closed()` |
| `ihr_access/state.py` | `ours_only`, `chart_limited`, `may_read_chart()` |
| `ihr_access/guard.py` | the chart rule (`Shape.chart`, `_chart_withheld`); `Shape.also` (the encounter shape also wraps `EncounterSpecBase`'s own `serialize`; E022 checks it) |
| `ihr_access/views.py` | resource requests, devices, dispense orders and patient-linked delivery rows gated on `branches.closed` |
| `ihr_access/tests/test_guard.py` | the Radiologist moved out of "CARE's roles see everything" into `test_the_radiologist_sees_a_chart_only_with_radiology`; expected marker strings |
| `scripts/isolation-mutation.sh` | 3 new rows, 7 re-pointed (below) |
| `docs/UPSTREAM.md`, `docs/QUESTIONS.md` (Q-K40), `CLAUDE.md` | the after-sync step, the CARE gaps, the project note |

### Defects found by the matrix and fixed (fail closed)

| # | What the matrix showed | Fix | Test (mutation row) |
|---|---|---|---|
| D1 | The **Radiologist** (not restricted: it holds clinical slugs) read every resource request and its comments, each embedding the first patient's unprojected body (blood group, extension; markers `base`, `resource`): CARE checks no right there and b2 gated restricted users only | `branches.closed(user)` = restricted, or every role held here is one of ours (`state.ours_only`; a CARE role held as well gives CARE's answer, Q-K35); the resource-request swap refuses `closed` users | `test_role_matrix`, the canary (`access-ours-only`) |
| D2 | The Radiologist saw the patient's **medication return** (named after the patient, the patient's body embedded; markers `return`, `base`) in the delivery-order list without origin or destination | `_NoPatientRows` (list, update by id, create) and the dispense-order and device swaps use `closed` | as D1 |
| D3 | The Radiologist reading an **imaging request or report** at the Radiology room for a patient whose visit has no Radiology got the whole patient (blood group, extension, programme number, tags) and visit (discharge advice, bed, tags) embedded: Q-K44 A says imaging requests and reports only, not the chart | **The chart rule** (guard): for a user of our roles only who holds a clinical one (`state.chart_limited`: the Radiologist), a patient or visit is projected unless CARE's `can_view_clinical_data` holds for that patient (`may_read_chart`, once per patient and request). The request's and report's own fields stay (exempted markers `request-xray`, `report-xray`, `result-xray`) | the canary, `test_guard.EncounterProjection.test_the_radiologist_sees_a_chart_only_with_radiology` (`access-chart-rule`) |
| D4 (latent, spec walk) | CARE's medication-dispense spec embeds the visit through `EncounterSpecBase.serialize`, which the guard did not wrap. No role of ours reaches a dispense today (403/empty), so nothing leaked | the encounter shape also wraps `EncounterSpecBase`'s own `serialize` (`Shape.also`); E022 checks it | `SpecWalk` (`access-encounter-base`) |

Every other cell was read before it was classified; the restricted roles' clinical routes all
answer `deny`, their lists of patient data `empty` or `project`.

### CARE-role gaps added to Q-K40's list (docs/QUESTIONS.md)

- the delivery-order list with neither origin nor destination answers every order of the
  hospital to any member (b2 had noted it as a limit);
- a service request or diagnostic report read through its place embeds the patient's whole
  record and visit, also for a department with no clinical right on that patient (D3 for CARE's
  own roles);
- `add_favorite` writes a favourite for an object the caller may not read (only the caller's own
  list changes; nothing is shown).

### Deviations from the design

1. **Write rows** prove only that an empty write is not accepted: CARE validates most bodies
   before it authorizes, so with `{}` every role gets 400 on those routes. The rows classify by
   the design's grants: `allow` for each write one of our roles holds (named tests: b2's write
   tests, `ihr_roles` `ExactGrants`, and the new `AllowedWrites` tests, which make the
   empty-body writes CARE grants our roles and check a role without the grant is refused);
   everything else is the default `deny`. A write that a role holds but no row names is still
   sent and, answering 400, passes: whether a role holds a write is ExactGrants'.
2. **Keys** are route plus `list_params.txt` assignments, and that file also overrides path
   parameters (the design's table has query parameters only): the lab, ward and imaging copies,
   the Radiology patient and the return order need their own path ids.
3. **b2's fixtures stay in b2's tests.** `build_world(full=True)` builds their equivalents for
   the matrix; moving them into the shared world would change the rows b2's exact-list
   assertions see (`test_store_keeper_never_sees_a_patient_return` lists the store's orders).
4. **Markers.** The world's tags, programme configuration and number, schedule, booking note and
   tag got named markers (b1's expected strings in `test_guard` updated); the catalogues that
   name them (tag and identifier settings, the facility's identifier configuration) are
   exempted for our roles, each with a negative row on the patient routes.
5. **Mutation rows.** `access-device-encounter` keeps b1's spec-level test only: devices are
   closed to our roles, so no route shows the canary a device (the device spec is in
   `UNREACHED_SPECS`). `access-guard-install` names the canary too, but E022 still stops the run
   first (its signature row stands, as in b1).
6. **The Radiologist is in the canary** (the task's list of our roles), with exemptions for
   its own imaging copies and the Radiology patient.
7. **Doctor and Facility Admin** answer as classified: where the world has no row of a settings
   kind they get `empty`, and 15 GET and 31 write routes `fill()` cannot fill (settings with no
   row in the access world: activity, specimen and observation definitions, valuesets, request
   orders, sub-queues, schedule exceptions ...) are `skip` with that reason (the isolation
   crawl seeds and crawls them).
8. **Function views** (the sign-in flow, bootstrap, logo) are requested by GET only; their
   methods cannot be read from the resolver (ihr_identity tests them).
9. The key snapshot covers the restricted roles; the Radiologist's bodies are CARE's, under the
   chart rule.
10. `test_guard.EncounterProjection.test_care_roles_see_everything` no longer lists the
    Radiologist: under the chart rule it sees the first patient's visit projected (intended).

### The rules as built

- `branches.closed(user)`: restricted, or holding only roles of ours (never a superuser);
  resource requests, devices, dispense orders and patient-linked delivery rows are closed to
  them. Bed history and account reports keep b2's `restricted` gate (the Radiologist reads a
  Radiology place's history by CARE's location rule, Q-K44 A; it holds no account right).
- The chart rule applies to the patient and encounter shapes only; a booking, token or account
  embedding a patient is projected through the patient shape. It costs CARE's
  `find_roles_on_patient` once per patient and request, for the Radiologist only; for CARE's
  roles `chart_limited` reads the request's cached roles (no new query).
- `Shape.also`: a base class CARE serializes directly is wrapped with the same shape and
  field list, its own `serialize` only.
- Q-K37a stays at option A (no bed for the Cashier and TPA Desk): the matrix's account rows are
  `project`, and `test_guard.ThroughTheApi.test_billing_roles_see_no_bed_on_the_account_for_now`
  is unchanged.

### Mutation rows

New: `access-ours-only` (closed() back to restricted only: `test_role_matrix` and the canary),
`access-chart-rule` (the chart rule off: the canary and the guard test), `access-encounter-base`
(no `also`: `SpecWalk`). Re-pointed (b3's tests added to b2's): `access-booking-note` (+ the
canary and `test_canary_exemptions_are_narrow`), `access-delivery-patient` (+ the canary and
`test_role_matrix`), `access-bed-history` and `access-resource-request` (+ `test_role_matrix`),
`access-patient-user-roles` (+ `test_role_matrix` and the canary: the moved Nurse),
`access-guard-install` (+ the canary; E022's signature), `access-dispense-order` (target now
`branches.closed`).

### Checks

| Run | Result |
|---|---|
| Plugin suites, `IHR_DB_ROLES=test` (the nine labels) | `Ran 892 tests in 896.991s` `OK (skipped=45)`; Redis audit 947 keys, all tenant-prefixed (17 min 2 s with the test database) |
| Plugin suites, roles off | `Ran 857 tests in 861.831s` `OK (skipped=49)`; Redis audit 947 keys, all tenant-prefixed (16 min 26 s) |
| CARE modules in tenant mode, roles off, for every viewset or spec b3 touched (delivery order and its concurrency test, device, diagnostic report, dispense order and its concurrency test, encounter, location, medication dispense, patient, resource request, service request, specimen, supply delivery, supply request) | `Ran 506 tests in 45.001s` `OK` |
| `scripts/isolation-mutation.sh check` | 135 rows (132 + 3 new), every target and named test found |
| The 3 new and 7 re-pointed rows, each applied alone to a scratch copy of `plugins/` and run as CI runs them (roles off) | each named test FAILED (an assertion, never an ERROR): `access-ours-only` 12 failures, `access-chart-rule` 4, `access-encounter-base` 1, `access-booking-note` 5, `access-delivery-patient` 17, `access-bed-history` 2, `access-resource-request` 64, `access-patient-user-roles` 64 (the moved Nurse in the matrix and canary), `access-dispense-order` 7; `access-guard-install` stopped at E022 with its signature (the canary, now named too, cannot run before it) |
| `python3 scripts/check-logs.py` | `Log pipeline OK.` (b3 adds no log line) |
| `scripts/batch-allowlist.py --check` | `batch-allowlist: 37 routes, list current` |
| `pre-commit run --all-files` (new files added with `git add -N` for the run, then reset) | 43 hooks passed |
| `manage.py check` in the dev backend (restarted with Celery) | `System check identified no issues` (E022 with `Shape.also`) |
| Manual, Alpha (dev, `APIClient` as the seeded users) | `care-radiologist`: resource requests 403 (was CARE's 200), delivery orders 200 with no patient-linked row; `care-receptionist` 403 and 200; `care-doctor` 200 and 200 (CARE's answers unchanged) |

### Runtime

The matrix module (`ihr_access.tests.test_role_matrix`, 17 tests) runs in about **62 s**
alone (`Ran 17 tests in 61.7s`): the GET crawl, 2,739 requests (249 targets × 11 viewers: 248
route keys, `/api/v1/users/` enumerated twice, by our invitation swap in `urls_tenant.py` and by
CARE's router; review O6 removed the duplicate, see "Review fixes" for the numbers after the
fixes), each in a rolled-back savepoint, **30-39 s**; the write crawl, 2,989 requests (the same
duplicate), **25-34 s**; building
the full world three times (one per database test class) a few seconds. It adds about 1.2 min to
CI's plugin step (the plugin suites went from 810 s to 897 s in roles test mode, 786 s to 862 s
roles off, with the other new test). With `IHR_MATRIX_DUMP=1` it prints every answer.

### Known limits (for b4)

- The write crawl's `deny` is weak where CARE validates first (deviation 1).
- The matrix runs on one world: a cell holds for these rows (one facility, the departments named
  above). Settings routes with no row here are skipped (the isolation crawl covers their
  tenancy).
- Staff cards (`<user>`) are not walked in the key snapshot: a new field on CARE's user card is
  not flagged there.
- The chart rule asks CARE's `can_view_clinical_data`, which counts open visits only: the
  Radiologist sees a finished Radiology visit of a patient projected, like any other patient with
  no open Radiology visit.
- Booking notes, tags and the booked doctor stay visible on the Receptionist's own CARE booking
  screens ("After the attack" §2.2; exempted with negative rows); the Owner's totals and MRD's
  grants are b4's.
- No write crawl with valid bodies (design item 14; review M3): `role_matrix_writes.txt` pins how
  each denied write answered an empty body, and a `body` cell (400: CARE validated first) proves
  only that nothing was written by that request, not that a valid body would be refused. Valid
  bodies are sent, as every role of ours, only on the desk routes (`DeskWrites`: bookings, slots,
  queues and tokens, accounts, invoices, payments) and the department routes (requests, specimens,
  reports: `AllowedWrites.test_department_writes_keep_to_their_own_copy`); there each denied role
  must be refused by authorization (401/403/404, or CARE's upsert 400 whose every datapoint is a
  permission refusal). Elsewhere a `body` cell still leans on the grants (`ExactGrants`) and CARE's
  own authorization after validation.
- Charge-item writes (`charge_item/...`, Cashier) are checked by status in `test_writes.BillingWrites`
  only, without a body canary (the answer names its service, which billing may see, Q-K38 A).
- The health rows read CARE's queue check against an empty queue on the test Redis (review O3); a
  Redis outage turns them into 500, which matches no row and fails the matrix. That is deliberate
  (fail loud): the runner's own Redis audit and every cache need the same server, so the run could
  not pass without it anyway.
- The static `list_params` walk reads keys named literally (`.get("k")`, `["k"]`, `"k" in`); a
  read of no literal key on a patient route only demands a line, not its keys (the runtime check
  names them when the crawl executes that read).
- The visit check refuses every objectless non-list action that names `?encounter=` for a closed
  user without the chart (review NEW-5), creates included, although CARE's creates authorize on
  the body's visit alone. A possible over-refusal, kept fail-closed: no web-app call sends a visit
  in the query of a write (the note, questionnaire and report forms send it in the body); pinned by
  `test_visits.VisitParam.test_a_create_naming_a_visit_is_refused`.

### Open items handed to b4

- MRD+g columns: add a records-grant viewer to the matrix (MRD with a live grant on the first
  patient: `allow` on that patient's clinical reads through the grant authorizer, `deny` on the
  Radiology patient) and its canary exemptions (each with a negative row on the other patient).
- The Owner totals endpoint gets its matrix row (`owner:allow`, everyone else `deny`) and the
  Owner identity rule already in the canary covers its body.
- `ihr_access.E023` (grant authorizer) stays reserved. The chart rule applies only to users of
  our roles who hold a clinical slug (the Radiologist); MRD stays restricted, so b4's grant
  exemption (`state.granted_patients`) is untouched by it, and the grant authorizer must not
  wrap `can_view_clinical_data` (the chart rule's question) without re-reading D3.
- `branches.closed` also closes resource requests, devices and dispense orders to MRD with a
  grant (they are not the patient's record); b4 decides if a grant should open any of them.
- The visit check (`visits.py`) asks `may_read_chart` (CARE's `can_view_clinical_data`): MRD with
  a records grant stays `closed`, so b4's grant authorizer decides whether a named visit opens
  rows beyond that visit; re-read `visits.check` with the grant authorizer.

### Review fixes (2026-11-15)

An independent review of the uncommitted b3 change raised 18 findings (M1-M8, K1-K4, O1-O6; M1
and K1 are the same hole, as are M2/K2/O1 in part and M7/O2); each was challenged against the
code and fixed with a regression test that fails without the fix, and a mutation row where it is
an access control. None needed an owner decision (no new Q-K): the CARE-role side of K1, the
slots-for-day gap and the request-order list went to Q-K40's list. No `apps/` file changed.

| Finding | Verdict | Fix | Test (mutation row) |
|---|---|---|---|
| K1 / M1 (high): the Radiologist read any patient's diagnoses, medicines, results, consents and notes, and `observation/analyse`, by naming another patient's Radiology visit in `?encounter=` (CARE's `authorize_read_encounter` and its copies in notes.py never check whose visit it is; a retrieve is not narrowed by the encounter filter) | confirmed | `ihr_access/visits.py`, in `APIView.initial` (every DRF view under a patient, batch sub-requests included): for `branches.closed` users a named visit must be the path patient's (else 403, a malformed or unknown id too); without the chart (`may_read_chart`) only that visit's rows open (the object and the note thread must be of it; a list is narrowed by CARE; any other action, `analyse`, is refused). E022 fails if the hook is missing or a patient view overrides `initial` outside our code. CARE's roles unchanged (Q-K40) | `test_visits.VisitParam` (6 tests: another patient's visit, restricted roles, the patient's own open Radiology visit, a finished Radiology visit opens its own rows only, CARE's roles unchanged, the check holds); the matrix's generated `?encounter=encounter_b` row on every patient route (27 rows) and `post_reads.txt` `analyse-cross`, with 13 canary `never` rows (`access-visit-patient`, `access-visit-rows`, `access-visit-install`) |
| K2 / M2 / O1 (medium): the list_params static check read only the view's own `get_queryset`/`list` and skipped detail routes, so helper reads (`authorize_read_encounter`, `authorize_for_pharmacist`), parents behind our swaps (device, delivery orders) and retrieves were never required a row; thread and note were marked "an optional filter" | confirmed | (1) runtime: the crawl records every `QueryDict` read made from CARE's or our code per route (`matrix.QueryReads`), and every key read (paging, tags and favourites aside) must be named by a `list_params.txt` line or the route have a `none` line; (2) the static check walks the whole MRO under `care.`/`ihr_` and includes detail routes; (3) every patient route gets the generated cross-patient variant; new lines: `?encounter=encounter` on consent, symptom, diagnosis, medication request/statement/administration/prescription, thread and note; `?facility=facility` (the pharmacist branch) on medication request and prescription, list and retrieve; device `?location=ward`; delivery-order `?origin`; supply delivery `?order` and `?request_order`; patient `?facility`; 52 more reviewed `none` lines (75 in all); thread and note reasons corrected | `RoleMatrix.test_every_query_read_has_a_params_line`, `ListParams.test_every_list_reading_the_query_has_a_row`, `ListParams.test_the_walk_reaches_parents_and_helpers` (`access-list-params-pharmacist`) |
| K3 (low): reads CARE makes by POST (search, search_retrieve, accession lookup, slots, `analyse`) were never crawled by the canary | confirmed | `tests/post_reads.txt`: 11 reviewed POST reads with bodies from the world, sent as every viewer, through the same outcome, canary, exemption, Owner and key-snapshot checks; each `role_matrix.txt` POST row whose reason says "a read by POST" must have a line. **It found a leak:** `slots/get_slots_for_day` checks no right (CARE's TODO), so every role of ours read any doctor's day and schedule; `IhrSlotViewSet` (SWAPS) asks `closed` users for the booking read right first, as `availability_stats` does (Q-K40 for CARE's roles) | `RoleMatrix.test_post_reads`, the canary (`access-slot-day`) |
| M3 (low after challenge): write rows defaulted to `deny` and passed on 400, so a new upstream write route, or one turning from authorize-first to validate-first, needed no review | partly | `tests/role_matrix_writes.txt`: a snapshot of every denied write (355 method-route lines) as `auth` (401/403/404/405) or `body` (400) per viewer; a new or gone write route, or a moved cell, fails. No write crawl with valid bodies (design item 14) | `RoleMatrix.test_every_method_of_denied_routes` |
| M4 (low): allowed writes on requests and reports named only ExactGrants, never sent to their endpoint | partly | `AllowedWrites.test_department_writes_keep_to_their_own_copy`: the Lab Technician's `create_specimen`, `create_specimen_from_definition`, report create, update and `upsert_observations`, and the Radiologist's report writes, answer 200 on the department's own copy (no marker of another copy or the chart in the answer) and 403/404 on the others; `MatrixFiles` refuses ExactGrants as the only named test on an allowed write whose route takes a request, report or patient id | that test, `MatrixFiles.test_the_files_parse_and_allowed_writes_name_their_tests` |
| M5 (low): an `all:skip` row hid a route fill() can fill; the availability writes' skip reason was wrong | confirmed | `skip` means "fill() cannot fill" only: a skip row on a filled GET route is requested and fails, on a filled write route fails; `schedule_external_id` filled by the matrix's own overlay (`matrix.MATRIX_PARAMS`, the isolation crawl unchanged); the world's new request order and supply request fill four more routes; their skip rows became reviewed rows (the availability writes default `deny`; the store's indent writes `store:allow`) | `RoleMatrix.test_targets_are_unique_and_skip_means_unfilled`, `test_role_matrix`, `test_every_method_of_denied_routes` |
| M6 (low): the matrix's roles were literals never checked against the registry | confirmed | `MatrixFiles.test_the_roles_are_the_registrys`: restricted group = `registry.RESTRICTED`, our roles = `registry.EIGHT`, the hidden role absent (b4), the comparison roles not ours | that test |
| M7 / O2 (low): SpecWalk ignored CARE's `pydantic_model` fallback for reads and walked `api_router` only | confirmed | read models collected as CARE resolves them (`get_read_pydantic_model` / `get_retrieve_pydantic_model`, falling back to `pydantic_model`) for every view of every URL configuration and router | `SpecWalk.test_a_view_reading_with_a_write_spec_is_caught` (a viewset with only `pydantic_model = PatientCreateSpec`) |
| M8 (low): the delivery-order list (it needs `?request_order`) answered 400 to everyone, so whether the Store Keeper sees the return's order there was never exercised | partly (the supply-request rows are settings kinds, deviation 7) | the world seeds a request order at the store and a supply request that both deliveries answer; row `delivery_orders/ ?request_order=request_order` is `all:deny admin:allow store:project` | `StoreRows.test_delivery_orders_of_a_request_order` (added to `access-delivery-patient`) |
| K4 (low): the key snapshot left out the Radiologist and collapsed any patient-like dict whether the guard handled it or not | confirmed | the Radiologist in its own column; the crawl records every instance the guard decided on (`Shape.applies`), and `key_paths` collapses a guarded shape only for those, walking any other (staff cards still collapse: a known limit) | `MatrixFiles.test_an_unhandled_patient_like_body_is_walked`, `RoleMatrix.test_response_keys_match_the_snapshot` |
| O3 (low): the health rows read the dev stack's live Celery queue | partly | the matrix classes point CARE's queue check at an empty queue on the test Redis (rows kept: they prove the probes show no hospital data) | `RoleMatrix.test_the_health_probe_reads_an_empty_test_queue` |
| O4 (low): `IHR_MATRIX_DUMP=1` never reached the container | confirmed | `scripts/care-tenancy.sh` forwards it; docs/UPSTREAM.md gives the exact command and what each `@@` line holds | used for every rebuild of the reviewed files in this fix |
| O5 (low): the batch meta-test was an undocumented after-sync failure | confirmed | kept strict; docs/UPSTREAM.md's batch step says each new GET needs a `BATCH_GET_CASES` entry and a `BatchCases` test; the assertion names the difference and the step | `BatchCases.test_every_listed_get_has_a_batch_case` |
| O6 (low): `/api/v1/users/` was crawled (and written) twice under one key; the runtime figures did not add up | confirmed | targets and write targets keep one entry per key (Django's first); two routes giving one key with different paths raise; the Runtime paragraph above is corrected | `RoleMatrix.test_targets_are_unique_and_skip_means_unfilled` |

**Matrix after the fixes:** the GET crawl sends 3,355 requests ((294 route keys + 11 POST reads) ×
11 viewers), the write crawl 3,046; the matrix module (27 tests) with `test_visits` (6) runs in about **73-90 s** alone (`Ran 33 tests in 73.423s`); in the full suites the GET crawl takes 44-52 s and the write crawl 34-38 s. The plugin suites went from 897 s to 920 s (roles test mode) and from 862 s to 896 s (roles off): about half a minute more in CI's plugin step.

**Checks after the review fixes (2026-11-15, backend and Celery restarted; no migration):**

| Run | Result |
|---|---|
| Plugin suites, `IHR_DB_ROLES=test` (the nine labels) | `Ran 908 tests in 919.811s` `OK (skipped=45)`; Redis audit 952 keys, all tenant-prefixed (17 min 26 s with the test database) |
| Plugin suites, roles off | `Ran 873 tests in 896.280s` `OK (skipped=49)`; Redis audit 952 keys, all tenant-prefixed (17 min 2 s) |
| The 5 new rows and the re-pointed `access-delivery-patient`, each applied alone to a scratch copy of `plugins/` and run as CI runs them (roles off) | each named test FAILED (an assertion, never an ERROR): `access-visit-patient` 46 failures, `access-visit-rows` 11, `access-slot-day` 14, `access-list-params-pharmacist` 1, `access-delivery-patient` 20; `access-visit-install` stopped at E022 with its signature (`APIView.initial is not the visit check`) |
| `scripts/isolation-mutation.sh check` | 140 rows (135 + 5 new), every target and named test found |
| `python3 scripts/check-logs.py` | `Log pipeline OK.` (no new log line) |
| `scripts/batch-allowlist.py --check` | `batch-allowlist: 37 routes, list current` |
| `pre-commit run --all-files` (new files added with `git add -N` for the run, then reset) | 43 hooks passed (a first run fixed one trailing newline in this file) |
| `manage.py check` in the dev backend (restarted with Celery) | `System check identified no issues (0 silenced).` (E020 with eleven swaps, E022 with the visit check) |

### Second fix pass: the verifier's open items (2026-11-15)

A verifier re-checked the review fixes and left four items partly open (M3, M4, M8, O3) and five
new problems. Each was fixed with a regression test where one fits; what stays open is under
"Known limits (for b4)" above. No `apps/` file, runtime code path, migration or log line changed
(tests, reviewed files and one matrix row only); no new Q-K.

| Item | Fix | Test (mutation row) |
|---|---|---|
| M4, specimen: `W specimen/{external_id}/ lab:allow` named only ExactGrants, and `MatrixFiles.SCOPED` missed it | `SCOPED` covers `/specimen/{external_id}/`; the department test sends PUT and PATCH as the Lab Technician to the lab copy's specimen (200, no marker of another copy) and the ward copy's (403/404); the report update is sent as PATCH too | `AllowedWrites.test_department_writes_keep_to_their_own_copy`, which now also asserts it sent every allowed cell of the rows naming it; `MatrixFiles` (cutting the row back to ExactGrants fails) |
| M4, desk writes: invoice, payment, booking and token writes were status checks only | `DeskWrites.test_desk_writes_with_bodies`: 39 writes with valid bodies (bookings and slots, queues and tokens, accounts, invoices, payments) sent as every role of ours and the moved Nurse; each allowed role gets a 2xx whose answer holds no marker beyond its exemptions (plus `WRITE_EXEMPTIONS`: an invoice names its charges, Q-K38 A); `MatrixFiles` makes every allowed write on these routes name it, and the test asserts it sent every such cell. It found one misclassified row: `account/default_account/` creates nothing and checks `can_read_account` only, so the TPA Desk gets it as every reader of accounts does (`rec,billing:allow`; `role_matrix_writes.txt` drops `tpa` there) | that test (dropping `WRITE_EXEMPTIONS` fails it on the invoice attaches) |
| M3: `body` write cells never proved a valid body is refused | on the desk and department routes every denied role of ours now sends the same valid body and must be refused by authorization (401/403/404, or CARE's upsert 400 whose every datapoint is a permission refusal: `refused()`); elsewhere a known limit (no valid-body crawl, item 14) | `DeskWrites`, `AllowedWrites.test_department_writes_keep_to_their_own_copy` |
| M8: a stale reason; the mutation row not re-run | the `delivery_orders/` reason corrected; the Doctor's `deny` on the store rows is CARE's correct answer (no inventory right at the store) | `access-delivery-patient` re-run: 20 failures (`StoreRows` 1, canary 15, `test_role_matrix` 3, `PatientReturns` 1) |
| O3: a Redis error would turn the health rows into 500 | the probe test also asserts the check answers `(200, {"queue_length": 0})` from the empty test queue; an outage stays a loud failure (known limit) | `RoleMatrix.test_the_health_probe_reads_an_empty_test_queue` |
| NEW-1: the static `list_params` check skipped every patient route | for a route under a patient, every key its view's classes read by name besides `encounter` (and paging) must be named by a line, and a read of no literal key needs a line; 13 reviewed `none:` lines added (EncounterBasedAuthorizationBase's pharmacist `facility` on consent, diagnosis, symptom, observation, statement and administration; `service_request` on a report retrieve) and the questionnaire-response lines name their keys | `ListParams.test_every_list_reading_the_query_has_a_row`, `test_a_patient_route_reading_another_key_needs_a_line` (`access-list-params-patient-key`) |
| NEW-2: a `none` line accepted every key; the POST reads recorded nothing | `none:key1,key2` names each reviewed optional key (a bare `none` names none) and the runtime check requires every read key to be named; the POST-read loop records under `POST <route>`, named by post_reads.txt assignments or a `none:` line (it surfaced `facility` on `search_retrieve`: CARE's tag context, dropped by the projection); 38 `none` lines now carry their keys | `RoleMatrix.test_every_query_read_has_a_params_line`, `MatrixFiles.test_a_new_optional_key_is_caught` (`access-list-params-none-keys`, `access-post-read-recorded`) |
| NEW-3: nothing proved the Lab Technician cannot update the ward specimen | as M4, specimen | as M4 |
| NEW-4: stale reason at role_matrix.txt:308 | corrected | — |
| NEW-5: the visit check refuses a create that names `?encounter=` | kept fail-closed (no web-app call sends it); a known limit, pinned | `test_visits.VisitParam.test_a_create_naming_a_visit_is_refused` |

**Checks after the second fix pass (2026-11-15; no runtime code changed, so no restart):**

| Run | Result |
|---|---|
| Plugin suites, `IHR_DB_ROLES=test` (the nine labels) | `Ran 912 tests in 924.262s` `OK (skipped=45)`; Redis audit 970 keys, all tenant-prefixed (17 min 28 s with the test database); GET crawl 3,355 requests 45.7 s, write crawl 3,045 requests 38.6 s |
| Plugin suites, roles off | `Ran 877 tests in 879.565s` `OK (skipped=49)`; Redis audit 970 keys, all tenant-prefixed (16 min 44 s); GET crawl 37.9 s, write crawl 26.6 s |
| The matrix module alone (`ihr_access.tests.test_role_matrix`, 30 tests, `--keepdb`) | `Ran 30 tests in 86.090s` `OK` (DeskWrites and the department test add about 10 s) |
| The 3 new rows and the re-run `access-delivery-patient` and `access-list-params-pharmacist`, each applied alone to a scratch copy of `plugins/` | each named test FAILED (an assertion, never an ERROR): `access-list-params-patient-key` 1, `access-list-params-none-keys` 1, `access-post-read-recorded` 1 ("the POST reads were not recorded"), `access-delivery-patient` 20, `access-list-params-pharmacist` 1 |
| `scripts/isolation-mutation.sh check` | 143 rows (140 + 3 new), every target and named test found |
| `python3 scripts/check-logs.py` | `Log pipeline OK.` |
| `scripts/batch-allowlist.py --check` | `batch-allowlist: 37 routes, list current` |
| `pre-commit run --all-files` | 43 hooks passed |
