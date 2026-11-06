"""Tenant registry, stored in the shared ``public`` schema (TENANCY_DESIGN.md §3).

A tenant is one legal entity (GSTIN holder) that may run several facilities (Q-T0).
"""

import re
import secrets
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django_tenants.models import DomainMixin, TenantMixin

SCHEMA_NAME_RE = re.compile(r"^h_[a-z0-9]{10}$")
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{2,30}$")
RESERVED_SLUGS = {
    "www",
    "api",
    "app",
    "auth",
    "abdm",
    "console",
    "admin",
    "static",
    "mail",
    "status",
    "public",
}
_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def generate_schema_name():
    return "h_" + "".join(secrets.choice(_ALPHABET) for _ in range(10))


class Hospital(TenantMixin):
    class Status(models.TextChoices):
        PROVISIONING = "provisioning"
        ACTIVE = "active"
        SUSPENDED = "suspended"
        OFFBOARDING = "offboarding"
        ARCHIVED = "archived"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    slug = models.CharField(max_length=31, unique=True)
    name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PROVISIONING
    )
    created_on = models.DateTimeField(auto_now_add=True)
    # Keycloak organisation of this hospital (TENANCY_DESIGN §4, P1-T3). Set once by
    # ihr_keycloak_sync and never changed: tokens are checked against it (P1-T4).
    keycloak_org_id = models.UUIDField(
        null=True, blank=True, unique=True, editable=False
    )

    # v1 provisioning: create the schema and run every migration in it (no cloning).
    auto_create_schema = True
    auto_drop_schema = False

    def clean(self):
        if self.schema_name != "public" and not SCHEMA_NAME_RE.match(
            self.schema_name or ""
        ):
            raise ValidationError(
                {"schema_name": "Must match h_ + 10 lowercase letters/digits."}
            )
        if self.schema_name != "public" and (
            not SLUG_RE.match(self.slug or "") or self.slug in RESERVED_SLUGS
        ):
            raise ValidationError({"slug": "Invalid or reserved slug."})
        if (
            not self.pk
            and RetiredAddress.objects.filter(kind="slug", value=self.slug).exists()
        ):
            raise ValidationError({"slug": "This slug belonged to another hospital."})

    def save(self, *args, **kwargs):
        if not self.schema_name:
            self.schema_name = generate_schema_name()
        if self.pk:
            # Web addresses are never renamed or reused (TENANCY_DESIGN.md §6.9): bookmarks,
            # printed QR codes and old links must never reach a different hospital.
            old_slug = (
                Hospital.objects.filter(pk=self.pk)
                .values_list("slug", flat=True)
                .first()
            )
            if old_slug is not None and old_slug != self.slug:
                raise ValidationError({"slug": "A hospital's slug can never change."})
        self.full_clean(exclude=["uuid"])
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.slug} / {self.schema_name})"


class Domain(DomainMixin):
    def save(self, *args, **kwargs):
        if self.pk:
            # An address never moves to another hospital and is never renamed (§6.9).
            old = (
                Domain.objects.filter(pk=self.pk).values("domain", "tenant_id").first()
            )
            if old and (old["domain"], old["tenant_id"]) != (
                self.domain,
                self.tenant_id,
            ):
                raise ValidationError("A hospital's web address can never be changed.")
        elif RetiredAddress.objects.filter(kind="domain", value=self.domain).exists():
            raise ValidationError("This web address belonged to another hospital.")
        super().save(*args, **kwargs)


class RetiredAddress(models.Model):
    """Slugs and web addresses of deleted hospitals, never given to another one (§6.9).

    Filled by the delete signals in ``signals.py``, which also fire for queryset deletes.
    """

    KINDS = (("slug", "slug"), ("domain", "domain"))

    kind = models.CharField(max_length=6, choices=KINDS)
    value = models.CharField(max_length=253)
    retired_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "value"], name="unique_retired_address"
            )
        ]

    def __str__(self):
        return f"{self.kind}:{self.value}"
