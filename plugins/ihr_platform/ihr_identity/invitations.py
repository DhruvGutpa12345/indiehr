"""Adding staff turns into an invitation (P1-T5e1, owner's decision of 2026-11-07).

Hospital admins keep CARE's own "Add user" (``POST /api/v1/users/``, same roles and checks).
Here that creates the CARE user switched off, with no usable password, sends none of CARE's
own e-mails, and queues a ``StaffInvitation`` for the provisioner, which alone can reach
Keycloak's admin API. The answer is CARE's usual one whether or not the address already has an
account, so the screen cannot be used to find out who has one (TENANCY_DESIGN §4.5).
"""

import re
import secrets
from datetime import timedelta

from care.emr.api.viewsets.user import UserViewSet
from django.db import connection, transaction
from django.utils import timezone
from ihr_platform.models import StaffInvitation
from rest_framework.exceptions import Throttled, ValidationError

NO_PASSWORDS = (
    "Staff choose their own password from the invitation e-mail: choose "
    "'send an e-mail' instead of setting a password."
)


# Names go into the invitation e-mail and page: plain single-line text, no links.
NAME_REFUSED = re.compile(r"[\x00-\x1f\x7f]|://|(?i:www\.)")
# Each invitation creates a Keycloak account and sends an e-mail: a hospital cannot flood
# the one provisioner every hospital shares.
INVITES_PER_DAY = 50
# Set only in-process by ihr_platform.seeding while CARE's demo fixtures create synthetic
# users with passwords (dev seed, tests); no request can set it.
DEMO_SEEDING = False


class InvitingUserViewSet(UserViewSet):
    def clean_create_data(self, request_data):
        if DEMO_SEEDING:
            return super().clean_create_data(request_data)
        if request_data.get("password"):
            raise ValidationError({"password": [NO_PASSWORDS]})
        for field in ("first_name", "last_name"):
            if NAME_REFUSED.search(str(request_data.get(field) or "")):
                raise ValidationError(
                    {field: ["Plain text only: no line breaks or web addresses."]}
                )
        return super().clean_create_data(request_data)

    def perform_create(self, instance):
        if instance.is_service_account or DEMO_SEEDING:
            # Machine accounts never sign in; their tokens come from a platform command.
            super().perform_create(instance)
            return
        recent = StaffInvitation.objects.filter(
            hospital=connection.tenant,
            created_at__gte=timezone.now() - timedelta(days=1),
        ).count()
        if recent >= INVITES_PER_DAY:
            raise Throttled(detail="Too many invitations today; try again tomorrow.")
        instance.is_active = False
        # Usable for a moment so CARE sends none of its own e-mails (they would link to CARE's
        # password pages, which are switched off); nobody ever learns it, and it is replaced
        # by an unusable one in the same transaction.
        instance.set_password(secrets.token_urlsafe(48))
        with transaction.atomic():
            super().perform_create(instance)
            instance.set_unusable_password()
            type(instance).objects.filter(pk=instance.pk).update(
                password=instance.password, is_active=False
            )
            StaffInvitation.objects.create(
                hospital=connection.tenant,
                care_user_id=instance.pk,
                email=instance.email.strip().lower(),
                first_name=instance.first_name,
                last_name=instance.last_name,
                invited_by=f"user:{self.request.user.external_id}",
            )
