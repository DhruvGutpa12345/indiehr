# ADR-018: Demographics-and-billing access for the non-clinical roles

- **Status:** Proposed (written by Claude at the end of P1-T11b4, 2026-11-15; for the owner's
  review with Q-K34 to Q-K45, which are auto-accepted and await confirmation, and Q-K37a, which
  the owner answered B, built 2026-11-15)
- **Date:** 2026-11-15
- **Decided by:** Claude, from the owner's answer to Q-K21 (2026-11-14). Evidence: CARE's
  authorization code at the pinned upstream (docs/reviews/p1-t11/design-b.md §2), a design attack
  by two reviewers (30 findings, 1 refuted), three build reviews (b1 to b3) and the role ×
  endpoint matrix, which found and closed four leaks to the Radiologist (design-b.md, "P1-T11b3
  as built").
- **Relates to:** ADR-017 (roles in code; its §6 points here), ADR-010 (audit trail), ADR-013
  (log redaction), ADR-009 (staff sessions), TENANCY_DESIGN §6.

## Context

Q-K21 (owner, 2026-11-14): Receptionist, Cashier, TPA Desk and Owner get a demographics-and-
billing view (identity, contact, encounter status), never notes, diagnoses, reports, discharge
advice, prescriptions or bed occupants. MRD and Radiologist get clinical read, MRD only through an
audited path. The Owner sees totals only and approves nothing.

Four facts of CARE's code decide the mechanism (design-b.md §2):

1. Opening a patient by id needs clinical rights (`can_view_clinical_data`).
2. "Read encounter" is clinical: the encounter shapes carry discharge advice, the care team and
   the bed, and `can_view_encounter_obj` alone also opens prescriptions and reports.
3. Patients and encounters are embedded in accounts, beds, bookings, tokens, payments, devices,
   requests and reports.
4. CARE's authorizer override is all or nothing per method name and cannot see its call site,
   so widening `can_view_clinical_data` would open 15+ clinical paths at once.

## Decision

**Fail closed.** The non-clinical roles hold none of CARE's patient or encounter read
permissions; their access comes from permissions of ours that CARE never honours, read only by
narrow branches of ours, and everything they see of a patient or a visit is projected.

1. **Permissions of ours** (`ihr_roles/permissions.py`): `ihr_view_patient_demographics`,
   `ihr_edit_patient_demographics`, `ihr_view_encounter_status`, `ihr_view_hospital_totals`,
   `ihr_request_records_access`; the eight roles and the hidden `MRD Records Access` with exact
   grants (`ihr_roles/registry.py`, ExactGrants; system check `ihr_roles.E021`).
2. **Route swaps** (`ihr_access/views.py`, `ihr_platform.route_swaps.SWAPS`, check
   `ihr_platform.E020`): eleven of CARE's viewsets are subclasses of ours. A branch of ours
   answers only where CARE's own path gives the user nothing, never adds rows to CARE's, and
   forces the projection for the rest of the request (Q-K35 A: permissions add up; a clinician
   who is also front desk gets CARE's answer through CARE's path). Patient writes by restricted
   users are decided before CARE reads the body; edits keep to the edit list; encounter filters
   keep to an allow-list (400 otherwise, Q-K37 A); bed history, resource requests, devices,
   dispense orders, account reports (Q-K41 A) and patient-linked store rows (Q-K43 A) are closed.
3. **Serializer guard** (`ihr_access/guard.py`, `fields/*.txt`, check `ihr_access.E022`): the
   `serialize` of every patient, encounter, location, device, booking and token shape is wrapped;
   for a restricted user, or under forced projection, every field not kept is reset (keys stay).
   Identifiers keep to a reviewed list; a programme number is never shown. **The billing roles
   and the bed (Q-K37a B, owner 2026-11-15):** for a user who holds the Cashier or TPA Desk role
   (`registry.BED_FOR_BILLING`) in an organisation of the visit's own facility
   (`state.bills_at`), `fields/encounter_billing.txt` is laid over the encounter list: the
   current bed's id and name, and each bed stay's id, start, end and status with its place's id
   and name (no ward, no occupant). That is what CARE's room-rent table reads from the
   account's visit. The treating doctor, departments and tags stay hidden (care_team until
   P2-T15); a place's own bed history stays closed; the Receptionist, MRD and the other
   restricted roles keep Q-K37 A.
4. **Placement trigger** (`ihr_access/placement.py`, `ihr_role_placement_guard`): refuses the
   hidden role in any organisation, our roles in government organisations, department-only
   roles at the facility root, a `PatientUser` row for a restricted person, and a hidden-role
   `PatientUser` row without a live records grant. Its fingerprint ships with a migration; a
   broken guard gates its hospital (P1-T19a).
5. **The PatientUser rule** (`ihr_access/authz.py`): a care-team row of a person who is now
   restricted counts for nothing; the hidden role counts only under a live grant, at read time.
6. **The chart rule** (guard): a user of our roles who holds a clinical one (the Radiologist)
   sees a patient or visit embedded elsewhere projected unless CARE gives them that patient's
   clinical data (Q-K44 A: Radiology on the visit).
7. **The visit check** (`ihr_access/visits.py`, in `APIView.initial`): for users of our roles, a
   visit named in `?encounter=` must be the path patient's, and without the chart only that
   visit's rows open (CARE never checks whose visit it is; Q-K40).
8. **MRD records grants** (`ihr_access/records.py`, Q-K36 A): MRD asks for one patient at a time
   (`POST /api/ihr/records-access/`, a reason code, a reference for legal and insurer requests);
   one transaction writes the grant and a hidden-role `PatientUser` row. A grant lasts 30 minutes
   (`IHR_RECORDS_GRANT_MINUTES`), read only. `authz.RecordsGrantAuthorizer` (CARE's
   `register_override_controller`, check `ihr_access.E023`) answers six of CARE's **read**
   checks (service request, specimen, diagnostic report, reports and requests by visit,
   `can_view_encounter_obj`) with CARE's answer or a live grant on the object's patient; never a
   write, never a place-wide list, never `can_view_clinical_data`. One end routine
   (`records.end_grant`) serves the DELETE endpoint, the per-minute beat job, a new grant on the
   same patient (which first ends a lapsed one) and the first request after the grant's time; that
   request only notes the grant, and `ihr_access.middleware.RecordsExpiryMiddleware` ends it after
   the view in its own short transaction (never under CARE's per-request transaction, so a refused
   request cannot undo it and no grant or patient row lock is held while a view runs). The
   hidden-role row is inserted without CARE's `PatientUser.save` and only the patient's caches are
   written, from a fresh locked read; it is removed only when no other live grant of that user on
   that patient needs it. A request that shows a granted patient is marked `purpose: records`
   in its audit entry, with the ids of the grants whose patients it showed and no others
   (`ihr_audit.context.annotate_patients`); a read of another patient made while a grant is live
   keeps its own purpose and names no grant. The CSV export carries the ids in its last column, `grants`;
   a grant's reason and reference are in its own `ihr_records_grant` entry. Signed file links
   under a grant live at most min(seconds left, 5 minutes). Each grant event writes one fixed log line,
   alerted on in `deploy/logs/alerts/records.yml` (refusals, volume, night grants).
9. **Owner totals** (`ihr_access/totals.py`, `GET /api/ihr/owner/totals/`, Q-K39 A): aggregates
   only, computed in the database (no patient row is loaded, so the trail records none), by
   encounter class and status, beds and money, per IST day or month, at most 366 days, from
   01-01-2000 to a year after today; never an id, a name, a row or a clinical breakdown (MIS,
   P2-T16). "Collected" is money received: active, completed payments and advances (a queued,
   partial or failed payment is not counted, as in CARE's own totals); adjustments are left out
   of it but are in CARE's balance behind "outstanding now" (Q-K46, open).
10. **The role × endpoint matrix and the clinical canary** (`ihr_access/tests/`): every route is
    requested as each of our roles, MRD with a grant, the moved Nurse, Doctor and Facility Admin,
    and compared with the reviewed `role_matrix.txt`; every non-GET method is sent as each
    denied role and snapshotted; a named marker in every clinical field must never reach a role
    of ours except where `canary_exemptions.txt` allows it, each exemption with a negative row; a
    new upstream route, field, query key or patient-shaped spec fails until it is reviewed.

**Decisions this rests on:** Q-K21 (owner, 2026-11-14); Q-K34 (the whole register for the front
desk), Q-K35 (permissions add up), Q-K36 (records grants), Q-K37 (no location for the front desk),
Q-K38 (billing sees service names), Q-K39 (no small-number suppression), Q-K40 (CARE's own gaps
logged for P1-T11d), Q-K41 (no account reports for restricted roles until P2-T15), Q-K42 (the
Receptionist reads the department list), Q-K43 (store rows linked to a patient hidden), Q-K44
(the Radiologist reads a chart with Radiology on the visit), Q-K45 (the web-app screens are
P1-T11c's), all auto-accepted and awaiting the owner; **Q-K37a (the billing roles and the bed):
owner B, 2026-11-15, built the same day** (item 3). **Q-K46 (adjustments in "collected") is open**; the build leaves them out.

## Alternatives rejected

| Option | Why not |
|---|---|
| Widen CARE's authorizers (`register_override_controller` on `can_view_clinical_data`) | Cannot see the call site; one widening opens every clinical path. Used only for the six read checks of item 8, keyed on one patient under a live grant |
| Patch `apps/` so a list permission opens a patient | A patch that conflicts on every sync, and it leaves the embedded shapes open |
| Separate `/api/ihr/frontdesk/...` endpoints | Safest, but CARE's web app calls CARE's routes: a large web-app patch. Kept as the fallback if the guard proves fragile |
| Standing clinical read for MRD (Q-K36 C) | Every patient's record at all times for a records clerk |
| A second approver per grant (Q-K36 B) | Stalls frequent records work; the reason, the trail and the alerts were judged enough |

## Consequences

- **Upstream syncs.** `ihr_platform.E020` (a swapped class moved), `ihr_roles.E021` (our
  permissions), `ihr_access.E022` (guard, PatientUser rule, visit check installed on the classes
  CARE uses), `ihr_access.E023` (the grant authorizer answers its six names, CARE still defines
  them, and the link cap is installed) and `ihr_access.E024` (encounter filters and create fields
  classified) stop a start that would serve unguarded; the matrix, the field lists, the pinned
  CARE copies (`care_copies.txt`) and the spec walk fail the tests until a change is reviewed
  (docs/UPSTREAM.md, after-sync steps). A CARE read check renamed upstream drops out of the grant
  authorizer: the grant then opens less, never more (fail closed), and E023 names it.
- **`ihr_access` stays a tenant app**, loaded after CARE's apps (design-b.md B15); moving it
  earlier fails at start-up.
- **Known residual exposures.** Billing roles see service names (Q-K38) and the names and times
  of the beds and other places a visit stayed in (Q-K37a B; a bed's name may name its ward); MRD with a grant sees
  the whole of that patient's record, its care-team list and its booking notes, for the grant's
  time (Q-K36); a signed file link issued under a grant may outlive the grant by up to five
  minutes; a two-job person (clinician and front desk) keeps CARE's own reach through CARE's
  path (Q-K35). CARE's own roles keep CARE's gaps until P1-T11d (Q-K40).
- **Cost.** One indexed query per staff request (the user's open records grants), one query of
  ours per request on the swapped patient and encounter routes for CARE's roles (design-b.md,
  P1-T11b2 review C3), and about 2.5 minutes of matrix crawl in CI's plugin step.
- **Usable day to day in the web app (P1-T11c, Q-K45; design-c.md).** c1: menus and routes by
  the backend's own rule (`/api/ihr/my-access/`), "My places" for the Lab Technician and Store
  Keeper. c2: the front-desk and billing-desk screens (patient tabs, the visit status page with
  the bed for billing, the edit form). c3: MRD's records-access page (asked for from the patient
  page only, the live grants with the patient's name and year of birth from the grant list
  itself, one watcher that clears every clinical page when a grant ends, however it ends) and
  the Owner's totals page (₹ in lakh and crore, exact figures on hover). The grant list
  (`GET /api/ihr/records-access/`) carries `grant_minutes` (the setting); each grant is its id,
  the patient's external id and its times, which the watcher polls every 30 s without loading a
  patient (no audit entry). Only `?names=1` (the records page, on opening it) adds
  `patient_name`, `patient_year_of_birth`, the reason, reference and start; that request lists
  the patients as shown and names no grant id in its audit entry (it reads no record under
  them; `ihr_audit.context.withdraw_patients`). Still API-only: a
  hospital-wide admin ending another clerk's grant (`DELETE /api/ihr/records-access/<id>/`) and
  grant history (the audit viewer).
- **Admin guide:** docs/ROLES_GUIDE.md (P1-T11c3): where each role is placed (design-b.md §4),
  what the placement trigger refuses, `ihr_patient_user_check` for stale `PatientUser` rows, what
  each role sees, records access, ending a grant as an admin, and the totals page.
