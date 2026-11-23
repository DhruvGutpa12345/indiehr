"""Our hospital roles, added to CARE's code-defined roles (P1-T11a, ADR-017 Proposed).

CARE defines roles in code (care/security/roles/role.py) and grants each permission to roles by
listing them on the permission (`Permission(..., roles=[...])`). `sync_permissions_roles` writes
both into every hospital's schema at each start, as system roles that hospitals cannot edit.

Rules here:
- **Names are permanent identifiers.** The sync matches roles by name, and a system role that is
  no longer registered is deleted, with every membership that holds it (OrganizationUser and
  FacilityOrganizationUser cascade). A role is therefore never renamed or removed: a retired role
  stays registered with no grants. `PERMANENT_NAMES` freezes the names; a test checks it.
- **Grants rebind, never append.** Several CARE permissions share one list object (all six
  encounter permissions use CLINICAL_DATA_ACCESS_ROLES), so `.append` would grant every one of
  them and change Doctor's and Nurse's grants too. `grant()` gives the permission a new list.
- **Fail at start-up** on an unknown permission slug (an upstream rename), or a name that clashes
  with one of CARE's roles.

The roles (P1-T11b, Q-K21 answered 2026-11-14; design: docs/reviews/p1-t11/design-b.md §4):
eight Indian hospital roles and one hidden role (``MRD Records Access``) that is only ever held on
a time-boxed ``PatientUser`` row under a records grant. The non-clinical ones are *restricted*
(``RESTRICTED``): what they may see of a patient is projected by ihr_access's guard.
"""

from care.security.roles.role import Role, RoleContext
from django.core.exceptions import ImproperlyConfigured

FACILITY = [RoleContext.FACILITY]


def _role(name, description, contexts=FACILITY):
    return Role(
        name=name, description=f"{description} (IndieHR)", contexts=list(contexts)
    )


RECEPTIONIST = _role(
    "Receptionist", "Front desk: registers patients and visits; demographics only"
)
CASHIER = _role("Cashier", "Billing counter: accounts, charges, invoices and payments")
LAB_TECHNICIAN = _role(
    "Lab Technician", "Laboratory: specimens and lab reports at the lab's locations"
)
RADIOLOGIST = _role(
    "Radiologist", "Imaging reports; the chart when Radiology is on the encounter"
)
TPA_DESK = _role("TPA Desk", "Insurance desk: reads bills and updates billing accounts")
MRD = _role(
    "MRD", "Medical records: demographics; clinical read only through an audited grant"
)
STORE_KEEPER = _role(
    "Store Keeper", "Stores: stock, supply requests and deliveries; nothing patient"
)
OWNER = _role("Owner", "Hospital owner: totals only; approves nothing")
# Never placed in an organisation (the placement trigger refuses it): held only on a PatientUser
# row while a live records grant exists (P1-T11b4). No context, so no role picker offers it.
RECORDS_ACCESS = _role(
    "MRD Records Access",
    "Hidden: one patient's clinical record under a records grant",
    [],
)

ROLES = [
    RECEPTIONIST,
    CASHIER,
    LAB_TECHNICIAN,
    RADIOLOGIST,
    TPA_DESK,
    MRD,
    STORE_KEEPER,
    OWNER,
    RECORDS_ACCESS,
]
# every role name we have ever registered, in order; never shortened (ADR-017 §2)
PERMANENT_NAMES = [
    "Receptionist",
    "Cashier",
    "Lab Technician",
    "Radiologist",
    "TPA Desk",
    "MRD",
    "Store Keeper",
    "Owner",
    "MRD Records Access",
]
HIDDEN = RECORDS_ACCESS.name
EIGHT = tuple(
    PERMANENT_NAMES[:8]
)  # the eight roles of P1-T11b (all but the hidden one)
# A user is restricted when they hold one of these in this hospital and no clinical slug through
# any organisation (ihr_access.state); their patients, encounters and beds are projected.
RESTRICTED = frozenset(
    {
        "Receptionist",
        "Cashier",
        "TPA Desk",
        "Owner",
        "Lab Technician",
        "Store Keeper",
        "MRD",
    }
)
# Q-K37a B (owner, 2026-11-15): of the restricted roles only these see the bed on a visit, in a
# facility where they hold one of them, for room rent (ihr_access/fields/encounter_billing.txt);
# the others keep Q-K37 A
BED_FOR_BILLING = frozenset({"Cashier", "TPA Desk"})
# Placed in a department, never at the facility root (the placement trigger): at the root they
# would read every patient with an active encounter (emr/models/encounter.py:39-54).
DEPARTMENT_ONLY = frozenset({"Lab Technician", "Radiologist", "Store Keeper"})

# Defined once; used by the grant tests, the guard and the restricted rule (design-b.md §4)
CLINICAL_SLUGS = frozenset(
    {
        "can_view_clinical_data",
        "can_read_encounter_clinical_data",
        "can_write_encounter_clinical_data",
        "can_view_questionnaire_responses",
        "can_submit_patient_questionnaire",
        "can_submit_encounter_questionnaire",
        "is_pharmacist",
        "read_medication_dispense",
        "write_medication_dispense",
        # CARE 3.1 (P2-T0a): a patient's vaccinations and recommendations, and medication
        # returns (which patient returned what). Every CARE role holding them already held a
        # clinical slug, so they make no one clinical who was not. Referrals are not here
        # (owner, Q-K163): CARE's role-organisation Member and Manager hold them, and a
        # restricted person who is also a Member must stay restricted; referral routes and
        # checks are closed to restricted users instead (ihr_access/referrals.py).
        "can_read_immunization",
        "can_write_immunization",
        "can_read_immunization_recommendation",
        "can_write_immunization_recommendation",
        "can_read_medication_return",
        "can_write_medication_return",
    }
)
# CARE slugs that open a patient or an encounter, or write one (and through
# can_write_patient_obj also allergies, notes and files)
PATIENT_OPENING = frozenset(
    {
        "can_list_patients",
        "can_read_encounter",
        "can_list_encounter",
        "can_write_encounter",
        "can_write_patient",
    }
)
NEVER_RESTRICTED = (
    CLINICAL_SLUGS | PATIENT_OPENING
)  # no restricted role holds any of these

DEMOGRAPHICS = ["ihr_view_patient_demographics", "ihr_view_encounter_status"]
# role name -> [permission slug, ...] (design-b.md §4; Q-K21, Q-K42 A for the departments)
GRANTS = {
    "Receptionist": [
        "can_create_patient",
        "can_create_encounter",
        "can_list_schedule",
        "can_list_booking",
        "can_write_booking",
        "can_reschedule_booking",
        "can_list_token",
        "can_write_token",
        "can_list_token_category",
        "can_read_patient_identifier_config",
        "can_read_healthcare_service",
        "can_read_charge_item_definition",
        "can_create_account",
        "can_read_account",
        "can_read_tag_config",
        "can_view_facility_organization",  # the department picker (Q-K42 A)
        "ihr_view_patient_demographics",
        "ihr_edit_patient_demographics",
        "ihr_view_encounter_status",
    ],
    "Cashier": [
        "can_read_account",
        "can_create_account",
        "can_update_account",
        "can_read_charge_item",
        "can_create_charge_item",
        "can_update_charge_item",
        "can_set_charge_item_definition",
        "can_read_charge_item_definition",
        "can_read_healthcare_service",
        "can_read_invoice",
        "can_write_invoice",
        "can_read_payment_reconciliation",
        "can_write_payment_reconciliation",
        *DEMOGRAPHICS,
    ],
    "TPA Desk": [
        "can_read_account",
        "can_update_account",
        "can_read_charge_item",
        "can_read_invoice",
        "can_read_payment_reconciliation",
        "can_read_charge_item_definition",
        *DEMOGRAPHICS,
    ],
    "Owner": ["ihr_view_hospital_totals"],
    "Lab Technician": [
        "can_read_service_request",
        "can_read_specimen",
        "can_write_specimen",
        "can_read_diagnostic_report",
        "can_write_diagnostic_report",
        "can_read_specimen_definition",
        "can_read_activity_definition",
        "can_read_observation_definition",
        "can_read_inventory_item",
        "can_list_facility_locations",
    ],
    "Radiologist": [
        "can_list_patients",
        "can_view_clinical_data",
        "can_view_questionnaire_responses",
        "can_list_encounter",
        "can_read_encounter",
        "can_read_encounter_clinical_data",
        "can_read_service_request",
        "can_read_diagnostic_report",
        "can_write_diagnostic_report",
        "can_read_activity_definition",
        "can_read_observation_definition",
        "can_list_facility_locations",
    ],
    "MRD": [*DEMOGRAPHICS, "ihr_request_records_access"],
    "Store Keeper": [
        "can_read_inventory_item",
        "can_read_supply_delivery",
        "can_write_supply_delivery",
        "can_write_external_supply_delivery",
        "can_read_supply_request",
        "can_write_supply_request",
        "can_read_product",
        "can_read_product_knowledge",
        "can_list_facility_locations",
    ],
    "MRD Records Access": [
        "can_list_patients",
        "can_view_clinical_data",
        "can_view_questionnaire_responses",
    ],
}
# Each permission's role names as CARE's code has them, before any grant of ours (recorded by
# the first install(); the grant tests compare against it)
ORIGINAL = {}


def _permission(slug):
    from care.security.permissions.base import PermissionController

    for handler in (
        PermissionController.internal_permission_handlers
        + PermissionController.override_permission_handlers
    ):
        for perm in handler:
            if perm.name == slug:
                return perm
    msg = f"ihr_roles: no CARE permission named {slug!r} (renamed upstream?)"
    raise ImproperlyConfigured(msg)


def grant(slug, role):
    """Give `role` the permission `slug`, without touching the list other permissions share."""
    perm = _permission(slug)
    if any(r.name == role.name for r in perm.value.roles):
        return
    perm.value.roles = [*perm.value.roles, role]


def install(roles=None, grants=None, permanent=None):
    """Register our roles and their grants (once per process)."""
    from care.security.roles.role import RoleController

    roles = ROLES if roles is None else roles
    grants = GRANTS if grants is None else grants
    permanent = PERMANENT_NAMES if permanent is None else permanent
    care_names = {r.name for r in RoleController.internal_roles}
    registered = {r.name for r in RoleController.override_roles}
    for role in roles:
        if role.name in care_names:
            msg = f"ihr_roles: {role.name!r} clashes with a CARE role"
            raise ImproperlyConfigured(msg)
        if role.name not in permanent:
            msg = f"ihr_roles: {role.name!r} is not in PERMANENT_NAMES (role names are permanent identifiers)"
            raise ImproperlyConfigured(msg)
        if role.name not in registered:
            RoleController.register_role(role)
            registered.add(role.name)
    missing = [n for n in permanent if n not in {r.name for r in roles}]
    if missing:
        msg = f"ihr_roles: {missing} were registered once and must stay registered (retire a role with no grants)"
        raise ImproperlyConfigured(msg)
    _record_original()
    by_name = {r.name: r for r in roles}
    for name, slugs in grants.items():
        for slug in slugs:
            grant(slug, by_name[name])


def _record_original():
    """Remember CARE's own role lists once, before the first grant of ours."""
    from care.security.permissions.base import PermissionController

    if ORIGINAL:
        return
    ours = set(PERMANENT_NAMES)
    for handler in PermissionController.internal_permission_handlers:
        for perm in handler:
            ORIGINAL[perm.name] = [
                r.name for r in perm.value.roles if r.name not in ours
            ]
