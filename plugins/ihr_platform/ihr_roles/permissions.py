"""Our five permissions (P1-T11b, design-b.md §3).

CARE's own checks never honour them: a user who holds only these gets nothing from CARE's
endpoints. Our route swaps (P1-T11b2) and our own endpoints (P1-T11b4) honour them, and our
serializer guard projects what they then show (ihr_access/guard.py).

Registered with ``PermissionController.register_permission_handler`` in
``IhrRolesConfig.ready()`` before ``registry.install()``. Each permission starts with no roles;
``registry.GRANTS`` gives them out through ``registry.grant()``.
"""

import enum

from care.security.permissions.constants import Permission, PermissionContext


class IhrPermissions(enum.Enum):
    ihr_view_patient_demographics = Permission(
        "Can open a patient's demographic record (IndieHR)",
        "Identity, contact and address only; never clinical data",
        PermissionContext.PATIENT,
        [],
    )
    ihr_edit_patient_demographics = Permission(
        "Can edit a patient's identity, contact and address (IndieHR)",
        "Only the reviewed edit list (ihr_access/fields/patient_edit.txt)",
        PermissionContext.PATIENT,
        [],
    )
    ihr_view_encounter_status = Permission(
        "Can see that an encounter exists, and its status (IndieHR)",
        "Status, class, period and priority only",
        PermissionContext.ENCOUNTER,
        [],
    )
    ihr_view_hospital_totals = Permission(
        "Can see the hospital's totals (IndieHR)",
        "Census counts and revenue, aggregated; never a patient or a bill",
        PermissionContext.FACILITY,
        [],
    )
    ihr_request_records_access = Permission(
        "Can ask for a time-boxed clinical read of one patient (IndieHR)",
        "Medical records: one patient, a stated reason, 30 minutes, audited",
        PermissionContext.FACILITY,
        [],
    )


SLUGS = tuple(p.name for p in IhrPermissions)
