"""Keep a deleted hospital's slug and web addresses reserved forever (TENANCY_DESIGN.md §6.9)."""

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from ihr_platform.models import Domain, Hospital, RetiredAddress


@receiver(pre_delete, sender=Hospital)
def retire_slug(sender, instance, **kwargs):
    if instance.schema_name != "public":
        RetiredAddress.objects.get_or_create(kind="slug", value=instance.slug)


@receiver(pre_delete, sender=Domain)
def retire_domain(sender, instance, **kwargs):
    RetiredAddress.objects.get_or_create(kind="domain", value=instance.domain)
