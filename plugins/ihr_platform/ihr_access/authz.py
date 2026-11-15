"""A PatientUser row follows the person's current job (P1-T11b1; "After the attack" §3).

CARE counts every ``PatientUser`` row of a user in ``PatientAccess.find_roles_on_patient``
(security/authorization/patient.py), and every patient-level check starts there: opening the
patient, clinical data, questionnaires, writes. ``install()`` wraps it (and the patient list
query) on CARE's class, with the ``ihr_guard`` marker E022 checks:

- for a restricted user (one of our restricted roles and no clinical slug through any
  membership, ``state.restricted``) a PatientUser row counts only with a restricted role, or the
  hidden role under a live grant: a care-team row added while they were a Nurse stops counting
  when they work at the front desk, also when they hold another non-clinical role there (CARE's
  role-organisation "Member", a hospital's own role; review S1). Roles they hold through a
  membership stay as CARE computed them: none of those holds a clinical slug;
- a ``MRD Records Access`` row counts only while a live records grant exists for that user and
  patient, at read time, whatever the clean-up jobs have done (P1-T11b4);
- the patient list of a restricted user is CARE's organisation-based list (empty unless a
  non-clinical role of theirs lists patients) plus patients under their own live grants (MRD),
  never CARE's ``users_cache`` branch, which PatientUser rows fill and which can be stale.
"""

from ihr_access import state

MARKER = "ihr_guard"
_installed = {}


def patient_roles(user, patient, roles):
    """CARE's role ids on ``patient`` for ``user``, less the PatientUser rows that must not count."""
    from care.emr.models import PatientUser
    from ihr_roles.registry import HIDDEN, RESTRICTED

    roles = set(roles)
    if user is None or getattr(user, "is_superuser", False):
        return roles
    current = state.for_user(user)
    ids = current.role_ids
    hidden = ids.get(HIDDEN)
    granted = hidden in roles and patient.pk in current.granted_patients
    if current.restricted:
        # A row counts only with a restricted role (or the hidden one under a live grant); a
        # role they also hold through a membership stays (it holds no clinical slug)
        allowed = {ids[n] for n in RESTRICTED if n in ids}
        if granted:
            allowed.add(hidden)
        rows = set(
            PatientUser.objects.filter(patient=patient, user=user).values_list(
                "role_id", flat=True
            )
        )
        roles -= rows - allowed - current.held_role_ids
    if hidden in roles and not granted:
        roles.discard(hidden)
    return roles


def filtered_patients(handler, queryset, user):
    """The patient list of a restricted user: CARE's organisation branch (patients of an
    organisation where a role of theirs lists patients) and their own live grants' patients,
    without CARE's ``users_cache`` branch."""
    from care.emr.models.organization import OrganizationUser
    from care.security.permissions.patient import PatientPermissions
    from django.db.models import Q

    current = state.for_user(user)
    roles = handler.get_role_from_permissions(
        [PatientPermissions.can_list_patients.name]
    )
    organizations = list(
        OrganizationUser.objects.filter(user=user, role_id__in=roles).values_list(
            "organization_id", flat=True
        )
    )
    return queryset.filter(
        Q(organization_cache__overlap=organizations)
        | Q(pk__in=current.granted_patients)
    )


def install():
    from care.security.authorization.patient import PatientAccess

    if _installed:
        return
    find = PatientAccess.find_roles_on_patient
    listing = PatientAccess.get_filtered_patients

    def find_roles_on_patient(self, user, patient):
        return patient_roles(user, patient, find(self, user, patient))

    def get_filtered_patients(self, qs, user):
        if not getattr(user, "is_superuser", False) and state.for_user(user).restricted:
            return filtered_patients(self, qs, user)
        return listing(self, qs, user)

    for wrapper, original in (
        (find_roles_on_patient, find),
        (get_filtered_patients, listing),
    ):
        setattr(wrapper, MARKER, True)
        wrapper.ihr_original = original
        setattr(PatientAccess, original.__name__, wrapper)
    _installed["PatientAccess"] = PatientAccess


def problems():
    """Why the PatientUser rule does not hold (E022)."""
    from care.security.authorization.base import AuthorizationController
    from care.security.authorization.patient import PatientAccess

    found = [
        f"PatientAccess.{name} is not wrapped"
        for name in ("find_roles_on_patient", "get_filtered_patients")
        if not getattr(getattr(PatientAccess, name, None), MARKER, False)
    ]
    # The class CARE's lazy map will resolve the query to (the last that defines it), found
    # without building that map (P1-T11b4 registers an override before it is built)
    handler = None
    for controller in (
        AuthorizationController.internal_authz_controllers
        + AuthorizationController.override_authz_controllers
    ):
        if hasattr(controller, "get_filtered_patients"):
            handler = controller
    if handler is None or not getattr(handler.get_filtered_patients, MARKER, False):
        found.append(
            "the patient list query does not resolve to the wrapped PatientAccess"
        )
    return found
