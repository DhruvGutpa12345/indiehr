"""Link between a hospital's CARE user and a person's Keycloak account (TENANCY_DESIGN §4.2)."""

from django.conf import settings
from django.db import models


class StaffIdentity(models.Model):
    """One per CARE user in this hospital's schema. Written only by ihr_link_staff, the dev seed
    and the setup e-mail (P1-T5c; staff invitations P1-T5e); nothing ever looks a user up by e-mail."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ihr_identity"
    )
    keycloak_sub = models.UUIDField(unique=True)
    # Sessions that started before this moment are refused, and the next sign-in must be a
    # fresh one (password + code): set at sign-out, removal and deactivation. Kept in Postgres
    # so a Redis outage cannot undo it.
    sessions_valid_after = models.DateTimeField(null=True, blank=True)
    # Linked by a setup e-mail (P1-T5c) and not yet signed in: the user is inactive until the
    # person first signs in with the password and authenticator they chose, from a verified
    # e-mail address; that sign-in activates them and clears this, once.
    awaiting_setup = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user_id} <-> {self.keycloak_sub}"
