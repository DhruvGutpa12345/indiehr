"""What a hospital admin can do about a colleague's sign-in (P1-T5e2, owner's decisions).

    GET  /api/ihr/staff/<username>/access/                the invitation and recent requests
    POST /api/ihr/staff/<username>/reset-sign-in/         lost authenticator (also the password)
    POST /api/ihr/staff/<username>/resend-invitation/     a fresh invitation e-mail
    POST /api/ihr/staff/<username>/cancel-invitation/     withdraw an unaccepted invitation

Reading, resending and cancelling: whoever may add staff at this hospital (CARE's
``can_create_user``), never on oneself, never on a superuser, and on an organisation admin only
by another organisation admin. Resetting sign-in (P1-T24b, attack A1): only a hospital-wide
admin (``hospital_wide_admin``, an admin role on the hospital's root) may ask, and never for
someone who holds an admin role on any top-level organisation at any hospital
(``top_level_admin``, the wider rule kept by Q-K130 A, so a state's admin from before P1-T25 is
still protected): this hospital is checked here, every other one by the provisioner before it
acts (``ihr_platform.invitations``), so a facility's admin cannot wipe the sign-in of the people
above them, here or elsewhere. One reset per person per day across all their hospitals: a
reset wipes their sign-in everywhere. Requests are queued for the provisioner, the only holder
of the Keycloak admin credential, and kept as this hospital's record of who asked for what. A
reset sends the one-time link to the person's own verified e-mail only: the admin never sees
it, and should check who is asking (in person or by a known phone number) before pressing the
button.

The GET answers ``state`` and ``offers`` (the actions this viewer may take on this person now,
P1-T24b G2; none on oneself), and every ``problem`` as a fixed code
(``invitations.problem_code``, attack A4): the provisioner's own text, which can hold Keycloak
paths and answers, stays in the record and the logs. The web app's "Sign-in" card on CARE's
user page shows buttons only from ``offers``.
"""

from datetime import timedelta

from care.emr.models.organization import OrganizationUser
from care.security.authorization import AuthorizationController
from care.users.models import User
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ihr_platform.invitations import PROTECTED_PREFIX, log_outcome, problem_code
from ihr_platform.models import StaffAccessRequest, StaffInvitation
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ihr_identity import roots
from ihr_identity.models import StaffIdentity

ACTIONS = {
    "reset-sign-in": StaffAccessRequest.Action.RESET_SIGN_IN,
    "resend-invitation": StaffAccessRequest.Action.RESEND_INVITATION,
    "cancel-invitation": StaffAccessRequest.Action.CANCEL_INVITATION,
}
# A queued invitation is being sent right now: neither is offered until it has been.
CANCELLABLE = {StaffInvitation.Status.SENT, StaffInvitation.Status.FAILED}
RESENDABLE = CANCELLABLE | {
    StaffInvitation.Status.CANCELLED,
    StaffInvitation.Status.EXPIRED,
}


def _admin_memberships(user):
    return OrganizationUser.objects.filter(
        user=user, role_id__in=roots.admin_role_ids(), deleted=False
    )


def organisation_admin(user):
    """An admin role on any of the hospital's organisations (not only a facility's)."""
    return _admin_memberships(user).exists()


def hospital_wide_admin(user):
    """An admin role (a role holding CARE's ``can_create_user``) on the hospital's root (P1-T25,
    Q-K76 B; ``roots.valid_root``): who may read the audit trail (P1-T6b), decide pack updates,
    end a records grant and ask for a sign-in reset. With no valid root nobody is (fail closed).
    A superuser is not, unless they hold such a role there too. An admin role on any other
    organisation, a government state included, no longer counts (Q-K120 superseded)."""
    root = roots.valid_root()
    return (
        root is not None and _admin_memberships(user).filter(organization=root).exists()
    )


def top_level_admin(user):
    """The rule before P1-T25, kept for the person protected from a reset (Q-K130 A): an admin
    role on any active, undeleted top-level organisation of the hospital (the root, a state, a
    fixture role organisation), so protection never narrowed with the gate."""
    return (
        _admin_memberships(user)
        .filter(
            organization__parent__isnull=True,
            organization__active=True,
            organization__deleted=False,
        )
        .exists()
    )


class IsHospitalAdmin(BasePermission):
    """DRF gate: signed-in staff (never a service account) with an admin role on the whole
    hospital. The audit viewer (P1-T6b) and the reference pack decisions (P1-T15b1) use it;
    being a permission class, a refused write is decided before its body is read."""

    message = "Only a hospital-wide admin can do this."

    def has_permission(self, request, view):
        user = request.user
        return (
            isinstance(user, User)
            and user.is_authenticated
            and not user.is_service_account
            and hospital_wide_admin(user)
        )


class NotCrossSite(BasePermission):
    """DRF gate for GET routes whose answer is itself an act (P1-T24 attack A2): reading the
    audit trail records the reader as having viewed every patient it names. The session cookie
    is SameSite=Lax and a GET needs no CSRF header (``authentication.check_csrf``), so another
    site could send a signed-in admin's browser to an export. Refused: a request whose
    ``Sec-Fetch-Site`` is present and is neither ``same-origin`` (our own page's link or fetch)
    nor ``none`` (an address typed, bookmarked or opened by the user). A client that sends no
    such header (an older browser, a script) is let through: the admin gate still applies.
    Put it first, so a cross-site request learns nothing about who is an admin."""

    message = "Cross-site request refused."
    ALLOWED = frozenset({"same-origin", "none"})

    def has_permission(self, request, view):
        site = request.headers.get("Sec-Fetch-Site")
        return site is None or site in self.ALLOWED


RESET = "reset-sign-in"
RESEND = "resend-invitation"
CANCEL = "cancel-invitation"
# What a viewer may be offered per state (design "T24b as decided"); ``offers_for`` narrows
# "active" to hospital-wide admins asking about someone who is not one here.
OFFERS = {
    "active": [RESET],
    StaffInvitation.Status.SENT: [RESEND, CANCEL],
    StaffInvitation.Status.FAILED: [RESEND, CANCEL],
    StaffInvitation.Status.CANCELLED: [RESEND],
    StaffInvitation.Status.EXPIRED: [RESEND],
}
RESET_REFUSED = "Only a hospital-wide admin can reset someone's sign-in."
# Fixed text the web app shows: "hospital-wide" here means any admin at the top of the hospital
# (``top_level_admin``), the root's admins and a state's admin alike
ADMIN_TARGET = (
    "A hospital-wide admin's sign-in is not reset from here: they can use "
    '"Forgot password", or ask IndieHR support.'
)


class RefusalError(ValidationError):
    """A 400 with a stable ``code`` beside its English ``detail`` (review W3): the web app
    chooses a phrase in the viewer's language from the code, never showing the English."""

    def __init__(self, code, detail):
        super().__init__({"code": code, "detail": detail})


def access_state(user, invitation, identity):
    """One word for where this person's sign-in stands at this hospital: ``active`` (signs
    in), an open invitation's status (``queued``, ``sent``, ``failed``, ``cancelled``,
    ``expired``), ``awaiting-setup`` (linked by a setup e-mail, never signed in, nothing open:
    the first admin's path), or ``none`` (not linked, or no longer active)."""
    if user.is_active:
        linked = identity is not None and not identity.awaiting_setup
        return "active" if linked else "none"
    if invitation is not None and invitation.status != StaffInvitation.Status.ACCEPTED:
        return str(invitation.status)
    if identity is not None and identity.awaiting_setup:
        return "awaiting-setup"
    return "none"


def reset_allowed(asker, target):
    """Attack A1, this hospital's part: a hospital-wide admin asks, about someone who is not a
    top-level admin here (the wider rule, Q-K130 A). (Other hospitals:
    ``invitations._admin_anywhere``, in the provisioner.)"""
    return hospital_wide_admin(asker) and not top_level_admin(target)


def offers_for(asker, target, state):
    """The actions ``asker`` may take on ``target`` now: what ``post`` accepts, except the two
    timing answers (one reset a day; a request already open), which stay 400."""
    if asker.pk == target.pk:
        return []
    offers = list(OFFERS.get(state, []))
    if RESET in offers and not reset_allowed(asker, target):
        offers.remove(RESET)
    return offers


class StaffAccessView(APIView):
    permission_classes = [IsAuthenticated]

    def _target(self, request, username):
        if not AuthorizationController.call("can_create_user", request.user):
            raise PermissionDenied("Only staff who may add users can do this.")
        target = get_object_or_404(
            get_user_model().objects.filter(deleted=False, is_service_account=False),
            username=username,
        )
        if target.is_superuser:
            raise PermissionDenied("Not for superusers.")
        if organisation_admin(target) and not organisation_admin(request.user):
            raise PermissionDenied(
                "Only a hospital-wide admin can do this for an admin."
            )
        return target

    def _invitation(self, user):
        return StaffInvitation.objects.filter(
            hospital=connection.tenant, care_user_id=user.pk
        ).first()

    def get(self, request, username, action=None):
        if action is not None:  # staff_urls.py routes only POST to an action
            raise ValidationError({"action": ["Ask for an action with POST."]})
        user = self._target(request, username)
        invitation = self._invitation(user)
        identity = StaffIdentity.objects.filter(user=user).first()
        state = access_state(user, invitation, identity)
        recent = StaffAccessRequest.objects.filter(
            hospital=connection.tenant, care_user_id=user.pk
        ).order_by("-created_at")[:5]
        return Response(
            {
                "active": user.is_active,
                "state": state,
                "offers": offers_for(request.user, user, state),
                "invitation": invitation
                and {
                    "status": invitation.status,
                    "kind": invitation.kind,
                    "sent_at": invitation.sent_at,
                    "expires_at": invitation.expires_at,
                    "problem": problem_code(invitation.error),
                },
                "requests": [
                    {
                        "action": r.action,
                        "status": r.status,
                        "requested_at": r.created_at,
                        "problem": problem_code(r.error),
                    }
                    for r in recent
                ],
            }
        )

    def _reset_sub(self, request, user, kind):
        """The checks of a reset (attack A1, review A4/K5/K10); returns the person's Keycloak
        ``sub``."""
        # Attack A1: decided before anything about the person is said (403, not 400)
        if not hospital_wide_admin(request.user):
            log_outcome(connection.tenant, None, kind, "reset-refused")
            raise PermissionDenied(RESET_REFUSED)
        if top_level_admin(user):  # the wider rule (Q-K130 A)
            log_outcome(connection.tenant, None, kind, "admin-target")
            raise PermissionDenied(ADMIN_TARGET)
        identity = StaffIdentity.objects.filter(user=user).first()
        if not user.is_active or identity is None or identity.awaiting_setup:
            raise RefusalError(
                "not-active-yet",
                "Not active staff yet: resend or cancel the invitation instead.",
            )
        sub = identity.keycloak_sub
        since = timezone.now() - timedelta(hours=settings.IHR_RESET_LINK_HOURS)
        asked = StaffAccessRequest.objects.filter(
            keycloak_sub=sub, action=kind, created_at__gte=since
        )
        # Across all hospitals, and without naming one (review K10): the person was reset
        # in the last day, or a refused reset for them is not to be repeated (review A4).
        # Only the "protected" refusal counts, not "not allowed" (that says something
        # about the asker, and must not block another admin) or "try later".
        if asked.exclude(status=StaffAccessRequest.Status.FAILED).exists():
            raise RefusalError(
                "reset-recent",
                "Their sign-in was reset in the last day: ask them to use the link "
                "in that e-mail.",
            )
        if asked.filter(
            status=StaffAccessRequest.Status.FAILED,
            error__startswith=PROTECTED_PREFIX,
        ).exists():
            raise RefusalError(
                "protected-recent",
                "This sign-in could not be reset from here. Try again tomorrow.",
            )
        return identity.keycloak_sub

    def post(self, request, username, action=None):
        if action not in ACTIONS:
            raise RefusalError("unknown-action", "Unknown action.")
        if username == request.user.username:
            raise RefusalError("own", "Ask another admin to do this for you.")
        user = self._target(request, username)
        kind = ACTIONS[action]
        sub = None
        if kind == StaffAccessRequest.Action.RESET_SIGN_IN:
            sub = self._reset_sub(request, user, kind)
        else:
            if user.is_active:
                raise RefusalError(
                    "already-active", "Already active staff: there is no invitation."
                )
            invitation = self._invitation(user)
            allowed = (
                CANCELLABLE
                if kind == StaffAccessRequest.Action.CANCEL_INVITATION
                else RESENDABLE
            )
            if invitation is not None and invitation.status == "queued":
                raise RefusalError(
                    "being-sent", "It is being sent now; try again in a minute."
                )
            if invitation is None or invitation.status not in allowed:
                raise RefusalError(
                    "no-invitation", "There is no invitation to do this with."
                )
        try:
            with transaction.atomic():
                queued = StaffAccessRequest.objects.create(
                    hospital=connection.tenant,
                    care_user_id=user.pk,
                    action=kind,
                    keycloak_sub=sub,
                    requested_by=f"user:{request.user.external_id}",
                )
        except IntegrityError:
            raise RefusalError(
                "already-asked", "Already asked; it is being done."
            ) from None
        return Response({"id": str(queued.pk), "status": queued.status}, status=202)
