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


# --- records grants open a patient's requests, specimens, reports and prescriptions -------
#
# A records grant (MRD, P1-T11b4) adds a ``PatientUser`` row with the hidden role, and that row
# counts only in patient-level checks (``find_roles_on_patient``). CARE checks service
# requests, specimens and diagnostic reports by department and place, and prescription retrieve
# and the encounter report by ``can_view_encounter_obj``, so a grant alone did not open them
# (design-b.md "After the attack" §8.1, B5). ``RecordsGrantAuthorizer`` answers these read
# checks only: CARE's answer, or True when the user holds a live grant for the object's patient.
# Never a write, never a place-wide list (``can_list_location_service_request``), never
# ``can_view_clinical_data`` (the chart rule's question, P1-T11b3 D3). Registered with CARE's
# ``register_override_controller``; ``ihr_access.E023`` checks every name resolves to it.

# CARE's read checks it answers; each object (a service request, or an encounter) has a
# ``patient_id``
GRANT_READS = (
    "can_read_service_request",  # a service request: retrieve, its files, its list by id
    "can_read_specimen",  # a specimen's service request
    "can_read_diagnostic_report",  # a report's service request; its files
    "can_read_diagnostic_report_in_encounter",  # an encounter: reports listed by visit
    "can_view_service_request_for_encounter",  # an encounter: requests listed by visit
    "can_view_encounter_obj",  # an encounter: prescription retrieve, encounter report
)
# Names it must never answer (E023): the chart rule's question and every write
NEVER_WRAPPED = frozenset({"can_view_clinical_data"})
WRITE_WORDS = ("write", "update", "create", "delete", "submit", "generate", "manage")


def granted(user, obj):
    """``obj`` (a service request or an encounter: both carry ``patient_id``) belongs to a
    patient under one of ``user``'s live records grants, as this request read them."""
    patient_id = getattr(obj, "patient_id", None)
    if patient_id is None or user is None:
        return False
    return patient_id in state.for_user(user).granted_patients


def care_handler(name, exclude=None):
    """The handler class CARE's controller would answer ``name`` with, leaving ``exclude``
    out: the last registered class that defines it (``AuthorizationController.build_cache``)."""
    from care.security.authorization.base import AuthorizationController

    found = None
    for controller in (
        AuthorizationController.internal_authz_controllers
        + AuthorizationController.override_authz_controllers
    ):
        if controller is exclude:
            continue
        if callable(getattr(controller, name, None)):
            found = controller
    return found


def _grant_read(name):
    def check(self, user, obj, *args, **kwargs):
        delegate = self.delegates.get(name)
        if delegate is not None and getattr(delegate(), name)(
            user, obj, *args, **kwargs
        ):
            return True
        return granted(user, obj)

    check.__name__ = name
    check.__qualname__ = f"RecordsGrantAuthorizer.{name}"
    setattr(check, MARKER, True)
    return check


def _authorizer_class():
    from care.security.authorization.base import AuthorizationHandler

    namespace = {
        "__doc__": "CARE's read answer, or a live records grant on the object's patient.",
        "__module__": __name__,
        "delegates": {},
        MARKER: True,
    }
    for name in GRANT_READS:
        namespace[name] = _grant_read(name)
    return type("RecordsGrantAuthorizer", (AuthorizationHandler,), namespace)


RecordsGrantAuthorizer = None


def install_grant_authorizer():
    """Register the authorizer once (IhrAccessConfig.ready()). Each name delegates to the class
    CARE resolved it to before us. CARE's controller builds its name map lazily on the first
    call and ignores a later registration (§2 fact 4): a map already built is rebuilt here."""
    from care.security.authorization.base import AuthorizationController

    global RecordsGrantAuthorizer  # noqa: PLW0603 (one class per process, built in ready())
    if RecordsGrantAuthorizer is not None:
        return
    authorizer = _authorizer_class()
    authorizer.delegates = {name: care_handler(name) for name in GRANT_READS}
    AuthorizationController.register_override_controller(authorizer)
    RecordsGrantAuthorizer = authorizer
    if AuthorizationController.cache["actions"]:
        AuthorizationController.build_cache()


def grant_problems():
    """Why the records grant authorizer does not hold (ihr_access.E023)."""
    import inspect

    from care.security.authorization.base import AuthorizationController

    authorizer = RecordsGrantAuthorizer
    if authorizer is None:
        return [
            "the records grant authorizer is not registered (authz.install_grant_authorizer)"
        ]
    found = []
    built = AuthorizationController.cache["actions"]
    for name in GRANT_READS:
        resolved = built.get(name) if built else care_handler(name)
        if resolved is not authorizer:
            found.append(
                f"{name} resolves to {getattr(resolved, '__name__', resolved)}, not the "
                "records grant authorizer"
            )
        delegate = authorizer.delegates.get(name)
        if delegate is None or delegate is authorizer:
            found.append(f"{name}: CARE has no handler of its own for it any more")
    # What it adds to CARE's base handler (whose helpers every handler inherits)
    own = {
        n
        for n, _m in inspect.getmembers(authorizer, predicate=inspect.isfunction)
        if n.startswith(("can_", "get_")) and n in vars(authorizer)
    }
    if extra := sorted(own - set(GRANT_READS)):
        found.append(f"the records grant authorizer answers more than reads: {extra}")
    for name in sorted(set(GRANT_READS) | own):
        if name in NEVER_WRAPPED or any(w in name for w in WRITE_WORDS):
            found.append(f"the records grant authorizer must never answer {name}")
    return found
