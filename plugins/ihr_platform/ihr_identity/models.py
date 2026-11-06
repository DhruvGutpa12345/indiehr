"""Link between a hospital's CARE user and a person's Keycloak account (TENANCY_DESIGN §4.2)."""

from django.conf import settings
from django.db import models


class StaffIdentity(models.Model):
    """One per CARE user in this hospital's schema. Written only by ihr_link_staff, the dev seed
    and (P1-T5) the invitation flow; nothing ever looks a user up by e-mail."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ihr_identity"
    )
    keycloak_sub = models.UUIDField(unique=True)
    # Sessions that started before this moment are refused, and the next sign-in must be a
    # fresh one (password + code): set at sign-out, removal and deactivation. Kept in Postgres
    # so a Redis outage cannot undo it.
    sessions_valid_after = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user_id} <-> {self.keycloak_sub}"
