"""Staff who are deactivated or deleted lose their sessions at once (TENANCY §4.6). An unlinked
person (StaffIdentity row deleted) fails the link check on their next request. A setup e-mail
not yet used (P1-T5c) is cancelled too: signing in afterwards must not reactivate them."""

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from ihr_identity.models import StaffIdentity


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def end_sessions_of_disabled_users(sender, instance, update_fields=None, **kwargs):
    if not instance.is_active or instance.deleted:
        changes = {"sessions_valid_after": timezone.now()}
        # CARE re-saves a user's role cache whenever their memberships change
        # (update_fields=["cached_role_orgs"]): that is not a deactivation.
        if update_fields is None or {"is_active", "deleted"} & set(update_fields):
            changes["awaiting_setup"] = False
        StaffIdentity.objects.filter(user_id=instance.pk).update(**changes)
