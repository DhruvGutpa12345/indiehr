"""Who our widening branches serve, and the gates on CARE's own paths (P1-T11b2;
docs/reviews/p1-t11/design-b.md §5.3 and "After the attack" §5-§7).

Our roles hold permissions of ours (``ihr_*``) that CARE's checks never honour. The swapped
viewsets (views.py) add a narrow branch that honours them, only where CARE's own path gives the
user nothing, and every such branch calls ``widen()``: the rest of the request is projected by
the serializer guard whatever else the user holds (a Receptionist who is also a Doctor gets the
projection through our branch, and CARE's unprojected answer through CARE's own; Q-K35).

"Holds a slug" means through a facility organisation of this hospital (each hospital is its own
schema), exactly as CARE's ``check_permission_in_facility_organization`` counts it.
"""

import os

from care.emr.models.organization import FacilityOrganizationUser
from care.security.authorization.base import AuthorizationHandler
from care.security.models import RolePermission
from django.db import connection

from ihr_access import state

DEMOGRAPHICS = "ihr_view_patient_demographics"
EDIT = "ihr_edit_patient_demographics"
STATUS = "ihr_view_encounter_status"
# Who may search patients at all, once they hold one of our roles (Q-K40; design §5.3 item 1)
SEARCH_SLUGS = ("can_list_patients", "can_create_patient", DEMOGRAPHICS)
LIST_PATIENTS = "can_list_patients"
# Our slugs: holds() first asks the request's cached roles (may_hold), so a user of CARE's roles
# only pays one query per request for every check of ours (review C3)
OURS = (DEMOGRAPHICS, EDIT, STATUS)

CARE = "care"  # CARE's own path answers, unchanged
PROJECTED = "projected"  # CARE's path answers, projected (our slug let them in)
REFUSED = "refused"

_handler = AuthorizationHandler()


def _signed_in(user):
    return (
        user is not None
        and getattr(user, "is_authenticated", False)
        and getattr(user, "pk", None)
    )


def may_hold(user, slug):
    """A necessary condition for ``holds``, from this request's cached facts: one of the roles
    the user holds here (any organisation) grants ``slug``. False means ``holds`` is False
    everywhere; True decides nothing."""
    if not _signed_in(user):
        return False
    if getattr(user, "is_superuser", False):
        return True  # as CARE's check
    current = state.for_user(user)
    return bool(current.held_role_ids & current.roles_granting(slug, together=OURS))


def holds(user, slug, facility=None):
    """``slug`` through a facility organisation of this hospital (of ``facility`` if given)."""
    if not _signed_in(user):
        return False
    if slug in OURS and not may_hold(user, slug):
        return False
    return _handler.check_permission_in_facility_organization(
        [slug], user, facility=facility
    )


def holds_in(user, slug, organizations):
    """``slug`` through one of these facility organisations (CARE's own check on an encounter,
    ``can_view_encounter_obj``: the encounter's and its bed's organisation caches)."""
    if not _signed_in(user):
        return False
    if slug in OURS and not may_hold(user, slug):
        return False
    return _handler.check_permission_in_facility_organization(
        [slug], user, orgs=list(organizations)
    )


def organizations_holding(user, slug, facility=None):
    """Ids of the facility organisations where ``user`` holds ``slug``."""
    if not _signed_in(user):
        return []
    rows = FacilityOrganizationUser.objects.filter(
        user=user, role_id__in=_handler.get_role_from_permissions([slug])
    )
    if facility is not None:
        rows = rows.filter(organization__facility=facility)
    return list(rows.values_list("organization_id", flat=True))


def facilities_holding(user, slug):
    """Ids of the facilities where ``user`` holds ``slug`` in some organisation."""
    if not _signed_in(user):
        return []
    return list(
        FacilityOrganizationUser.objects.filter(
            user=user, role_id__in=_handler.get_role_from_permissions([slug])
        )
        .values_list("organization__facility_id", flat=True)
        .distinct()
    )


def restricted(user):
    """One of our restricted roles here and no clinical slug (state.restricted; never a
    superuser): the users the further swaps refuse (design "After the attack" §7)."""
    if not _signed_in(user) or getattr(user, "is_superuser", False):
        return False
    return state.for_user(user).restricted


def widen():
    """Our branch serves this request: every patient, encounter, bed, device, booking and
    token it shows is projected, whatever else the user holds (design §5.2)."""
    state.force_projection()


def search_gate(user):
    """CARE answers ``patient/search`` and ``search_retrieve`` to anyone signed in (Q-K40).
    A user who holds one of our roles must hold one of SEARCH_SLUGS (else REFUSED: the Owner,
    Lab Technician and Store Keeper); one who holds no ``can_list_patients`` is served
    PROJECTED. A user of CARE's roles only is unchanged (CARE)."""
    if not _signed_in(user) or getattr(user, "is_superuser", False):
        return CARE
    current = state.for_user(user)
    held = current.held_role_ids
    if not held & set(current.role_ids.values()):
        return CARE
    slugs = set(
        RolePermission.objects.filter(
            role_id__in=held,
            temp_deleted=False,
            permission__slug__in=SEARCH_SLUGS,
            permission__deleted=False,
            permission__temp_deleted=False,
        ).values_list("permission__slug", flat=True)
    )
    if not slugs:
        return REFUSED
    return CARE if LIST_PATIENTS in slugs else PROJECTED


# --- the log line -------------------------------------------------------------------------

REFUSAL_PREFIX = "ihr-access: refused"


def refusal_line(what):
    """A fixed line (our own words only, never a value from the request or a patient)."""
    slug = getattr(getattr(connection, "tenant", None), "slug", None) or "unknown"
    return f"{REFUSAL_PREFIX} {what} hospital={slug}"


def emit(line):
    # Straight to standard error as one whole line, as the batch refusals (ihr_platform/batch.py)
    os.write(2, f"{line}\n".encode())
