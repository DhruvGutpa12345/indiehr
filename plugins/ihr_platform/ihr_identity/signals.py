"""Staff who are deactivated or deleted lose their sessions at once (TENANCY §4.6). An unlinked
person (StaffIdentity row deleted) fails the link check on their next request."""

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from ihr_identity.models import StaffIdentity


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def end_sessions_of_disabled_users(sender, instance, **kwargs):
    if not instance.is_active or instance.deleted:
        StaffIdentity.objects.filter(user_id=instance.pk).update(
            sessions_valid_after=timezone.now()
        )
