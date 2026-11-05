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

    def save(self, *args, **kwargs):
        if not self.schema_name:
            self.schema_name = generate_schema_name()
        self.full_clean(exclude=["uuid"])
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.slug} / {self.schema_name})"


class Domain(DomainMixin):
    pass
