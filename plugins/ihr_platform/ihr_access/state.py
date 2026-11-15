"""Per-request facts the guard and the PatientUser rule need, computed lazily, once.

``begin(user)`` runs in DRF's ``perform_authentication`` (after the audit actor is bound,
ihr_audit/apps.py), so every DRF view, batch sub-requests included, starts with a fresh state
for the person it authenticated. Django's request_started and request_finished clear it, so
nothing carries into the next request on the same thread. Outside a request (Celery, commands)
there is no state and the guard is off: no restricted user acts there (design-b.md §5.2).

Nothing here is cached across requests: role ids and memberships differ per hospital schema
and change between requests (TENANCY_DESIGN §6).
"""

from contextvars import ContextVar

from django.utils import timezone

_state = ContextVar("ihr_access_state", default=None)


class RequestState:
    __slots__ = (
        "_chart",
        "_granting",
        "_grants",
        "_held",
        "_ours_only",
        "_restricted",
        "_restricted_only",
        "_role_ids",
        "forced",
        "user",
    )

    def __init__(self, user):
        self.user = user
        self.forced = (
            False  # set by our widening branches (P1-T11b2): project whatever else
        )
        self._restricted = None
        self._restricted_only = None
        self._grants = None
        self._granting = None
        self._held = None
        self._role_ids = None
        self._ours_only = None
        self._chart = {}

    def is_for(self, user):
        return user is not None and getattr(self.user, "pk", None) == getattr(
            user, "pk", 0
        )

    @property
    def restricted(self):
        if self._restricted is None:
            self._restricted = restricted(self.user, self.role_ids, self.held_role_ids)
        return self._restricted

    @property
    def restricted_only(self):
        if self._restricted_only is None:
            self._restricted_only = restricted_only(self.user, self.role_ids)
        return self._restricted_only

    @property
    def ours_only(self):
        """Every role held here (any organisation) is one of ours: no CARE role adds to them
        (Q-K35)."""
        if self._ours_only is None:
            held = self.held_role_ids
            self._ours_only = (
                bool(held)
                and not getattr(self.user, "is_superuser", False)
                and held <= set(self.role_ids.values())
            )
        return self._ours_only

    @property
    def chart_limited(self):
        """Holds only roles of ours, one of them clinical: the Radiologist. They read a chart
        only where CARE's own patient rule gives them clinical data (Radiology on the visit,
        Q-K44 A), so a patient or visit embedded in an imaging request or report is projected
        for them elsewhere (P1-T11b3's matrix; guard.Shape.applies)."""
        return self.ours_only and not self.restricted

    def may_read_chart(self, patient):
        """CARE's ``can_view_clinical_data`` on this patient (through our PatientUser rule),
        once per patient and request."""
        if patient.pk not in self._chart:
            from care.security.authorization import AuthorizationController

            self._chart[patient.pk] = bool(
                AuthorizationController.call(
                    "can_view_clinical_data", self.user, patient
                )
            )
        return self._chart[patient.pk]

    @property
    def granted_patients(self):
        """Patient pks under this user's live records grants."""
        if self._grants is None:
            self._grants = granted_patients(self.user)
        return self._grants

    @property
    def held_role_ids(self):
        """Role ids this user holds through any facility or other organisation here."""
        if self._held is None:
            self._held = (
                frozenset(_held_role_ids(self.user))
                if _signed_in(self.user)
                else frozenset()
            )
        return self._held

    def roles_granting(self, slug, together=()):
        """Role ids that grant ``slug``, read as CARE's ``get_role_from_permissions`` reads them
        (so never fewer); ``together``: slugs read in the same query (review C3)."""
        if self._granting is None:
            self._granting = {}
        if slug not in self._granting:
            from care.security.models import RolePermission

            wanted = {slug, *together} - set(self._granting)
            found = {name: set() for name in wanted}
            for name, role_id in RolePermission.objects.filter(
                permission__slug__in=wanted
            ).values_list("permission__slug", "role_id"):
                found[name].add(role_id)
            self._granting.update({k: frozenset(v) for k, v in found.items()})
        return self._granting[slug]

    @property
    def role_ids(self):
        if self._role_ids is None:
            self._role_ids = our_role_ids()
        return self._role_ids


def begin(user):
    _state.set(RequestState(user))


def end(**kwargs):
    _state.set(None)


def current():
    return _state.get()


def force_projection():
    """Our widening branches call this: the rest of this request is projected (§5.2)."""
    state = _state.get()
    if state is not None:
        state.forced = True


def for_user(user):
    """This request's state when it is about ``user``, else a fresh one (not stored)."""
    state = _state.get()
    if state is not None and state.is_for(user):
        return state
    return RequestState(user)


# --- the rules ----------------------------------------------------------------------------


def our_role_ids():
    """{role name: id} of our system roles in this hospital."""
    from care.security.models import RoleModel
    from ihr_roles.registry import PERMANENT_NAMES

    return dict(
        RoleModel.objects.filter(is_system=True, name__in=PERMANENT_NAMES).values_list(
            "name", "id"
        )
    )


def _held_role_ids(user):
    from care.emr.models.organization import FacilityOrganizationUser, OrganizationUser

    held = set(
        FacilityOrganizationUser.objects.filter(user=user).values_list(
            "role_id", flat=True
        )
    )
    held |= set(
        OrganizationUser.objects.filter(user=user).values_list("role_id", flat=True)
    )
    return held


def _signed_in(user):
    # Staff only: CARE's OTP sign-in gives a PatientOtpObject, which is authenticated but
    # has no pk and holds no role; such a caller is never restricted (CI mutation run of
    # public-otp-booking-from-model, 15-11: AttributeError instead of a projection check)
    return (
        user is not None
        and getattr(user, "is_authenticated", False)
        and getattr(user, "pk", None) is not None
    )


def restricted(user, role_ids=None, held=None):
    """Holds one of our restricted roles here, and no clinical slug through any organisation
    (design-b.md §4; permissions add up, Q-K35). ``held``: this request's held role ids."""
    from care.security.models import RolePermission
    from ihr_roles.registry import CLINICAL_SLUGS, RESTRICTED

    if not _signed_in(user) or user.is_superuser:
        return False
    role_ids = our_role_ids() if role_ids is None else role_ids
    held = _held_role_ids(user) if held is None else held
    if not held & {role_ids[n] for n in RESTRICTED if n in role_ids}:
        return False
    # As CARE's own permission lookups (RoleModel.get_permission_sk_for_role) and the
    # placement trigger: a grant sync_permissions_roles is about to delete is not counted
    # (second review of S1)
    return not RolePermission.objects.filter(
        role_id__in=held,
        temp_deleted=False,
        permission__slug__in=CLINICAL_SLUGS,
        permission__deleted=False,
        permission__temp_deleted=False,
    ).exists()


def restricted_only(user, role_ids=None):
    """Every role this user holds here (in any organisation) is one of our restricted ones.
    No rule keys on it any more (review S1: the PatientUser rule uses ``restricted``); it stays
    for tests and the mutation ``access-patient-user-gate``."""
    from ihr_roles.registry import RESTRICTED

    if not _signed_in(user) or user.is_superuser:
        return False
    role_ids = our_role_ids() if role_ids is None else role_ids
    held = _held_role_ids(user)
    return bool(held) and held <= {role_ids[n] for n in RESTRICTED if n in role_ids}


def granted_patients(user):
    from ihr_access.models import RecordsGrant

    if not _signed_in(user):
        return frozenset()
    return frozenset(
        RecordsGrant.objects.filter(
            user=user, ended_at__isnull=True, expires_at__gt=timezone.now()
        ).values_list("patient_id", flat=True)
    )
