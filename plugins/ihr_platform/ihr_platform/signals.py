"""Keep a deleted hospital's slug and web addresses reserved forever (TENANCY_DESIGN.md §6.9).

The one exception (P1-T5a, owner's choice): rolling back a hospital that never went live
releases its slug and addresses, because they never served anyone. The roll-back marks the
very instances it deletes (``ihr_release_addresses``); nothing process-wide is switched off.
"""

from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django_tenants.signals import schema_migrated

from ihr_platform.models import Domain, Hospital, RetiredAddress


def _released(instance, hospital):
    return (
        getattr(instance, "ihr_release_addresses", False)
        and hospital.activated_at is None
    )


@receiver(pre_delete, sender=Hospital)
def retire_slug(sender, instance, **kwargs):
    if instance.schema_name != "public" and not _released(instance, instance):
        RetiredAddress.objects.get_or_create(kind="slug", value=instance.slug)


@receiver(pre_delete, sender=Domain)
def retire_domain(sender, instance, **kwargs):
    if not _released(instance, instance.tenant):
        RetiredAddress.objects.get_or_create(kind="domain", value=instance.domain)


# --- release state (P1-T18a) -------------------------------------------------------------


@receiver(schema_migrated)
def record_schema_after_migrate(sender, schema_name, **kwargs):
    """django-tenants sends this after a schema's migrations finished without error (never
    after a failure). Record which build that schema now matches (release.py): covers
    provisioning, ihr_create_tenant, the test runner and a manual migrate_schemas."""
    from ihr_platform import release

    if release.RECORD_ON_SIGNAL["on"]:
        release.record_after_migrate(schema_name)
