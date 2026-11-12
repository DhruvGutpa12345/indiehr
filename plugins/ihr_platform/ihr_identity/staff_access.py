"""What a hospital admin can do about a colleague's sign-in (P1-T5e2, owner's decisions).

    GET  /api/ihr/staff/<username>/access/                the invitation and recent requests
    POST /api/ihr/staff/<username>/reset-sign-in/         lost authenticator (also the password)
    POST /api/ihr/staff/<username>/resend-invitation/     a fresh invitation e-mail
    POST /api/ihr/staff/<username>/cancel-invitation/     withdraw an unaccepted invitation

Allowed for whoever may add staff at this hospital (CARE's ``can_create_user``), never on
oneself, never on a superuser, and on a hospital-wide admin only by another hospital-wide
admin (a facility's admin cannot reset the people above them). One reset per person per
day across all their hospitals: a reset wipes their sign-in everywhere. Requests are queued for the provisioner, the only holder of the Keycloak admin
credential, and kept as this hospital's record of who asked for what. A reset sends the
one-time link to the person's own verified e-mail only: the admin never sees it, and should
check who is asking (in person or by a known phone number) before pressing the button.
Buttons on CARE's user page come with the web-app patch.
"""

from datetime import timedelta

from care.emr.models.organization import OrganizationUser
from care.security.authorization import AuthorizationController
from care.security.models import RolePermission
from care.security.permissions.user import UserPermissions
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ihr_platform.models import StaffAccessRequest, StaffInvitation
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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
    roles = RolePermission.objects.filter(
        permission__slug=UserPermissions.can_create_user.name
    ).values_list("role_id", flat=True)
    return OrganizationUser.objects.filter(user=user, role_id__in=roles, deleted=False)


def organisation_admin(user):
    """An admin role on any of the hospital's organisations (not only a facility's)."""
    return _admin_memberships(user).exists()


def hospital_wide_admin(user):
    """An admin role on one of the hospital's top-level organisations, active and not deleted
    (not a team's or a district's inside it): who may read the audit trail (P1-T6b)."""
    return (
        _admin_memberships(user)
        .filter(
            organization__parent__isnull=True,
            organization__active=True,
            organization__deleted=False,
        )
        .exists()
    )


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
        recent = StaffAccessRequest.objects.filter(
            hospital=connection.tenant, care_user_id=user.pk
        ).order_by("-created_at")[:5]
        return Response(
            {
                "active": user.is_active,
                "invitation": invitation
                and {
                    "status": invitation.status,
                    "kind": invitation.kind,
                    "sent_at": invitation.sent_at,
                    "expires_at": invitation.expires_at,
                    "problem": invitation.error,
                },
                "requests": [
                    {
                        "action": r.action,
                        "status": r.status,
                        "requested_at": r.created_at,
                        "problem": r.error,
                    }
                    for r in recent
                ],
            }
        )

    def post(self, request, username, action=None):
        if action not in ACTIONS:
            raise ValidationError({"action": ["Unknown action."]})
        if username == request.user.username:
            raise ValidationError(["Ask another admin to do this for you."])
        user = self._target(request, username)
        kind = ACTIONS[action]
        sub = None
        if kind == StaffAccessRequest.Action.RESET_SIGN_IN:
            identity = StaffIdentity.objects.filter(user=user).first()
            if not user.is_active or identity is None or identity.awaiting_setup:
                raise ValidationError(
                    ["Not active staff yet: resend or cancel the invitation instead."]
                )
            sub = identity.keycloak_sub
            recent = StaffAccessRequest.objects.filter(
                keycloak_sub=sub,
                action=kind,
                created_at__gte=timezone.now()
                - timedelta(hours=settings.IHR_RESET_LINK_HOURS),
            ).exclude(status=StaffAccessRequest.Status.FAILED)
            if recent.exists():
                raise ValidationError(
                    [
                        "Their sign-in was reset in the last day (perhaps at another "
                        "hospital): ask them to use the link in that e-mail."
                    ]
                )
        else:
            if user.is_active:
                raise ValidationError(["Already active staff: there is no invitation."])
            invitation = self._invitation(user)
            allowed = (
                CANCELLABLE
                if kind == StaffAccessRequest.Action.CANCEL_INVITATION
                else RESENDABLE
            )
            if invitation is not None and invitation.status == "queued":
                raise ValidationError(["It is being sent now; try again in a minute."])
            if invitation is None or invitation.status not in allowed:
                raise ValidationError(["There is no invitation to do this with."])
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
            raise ValidationError(["Already asked; it is being done."]) from None
        return Response({"id": str(queued.pk), "status": queued.status}, status=202)
