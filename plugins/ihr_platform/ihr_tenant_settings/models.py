"""Who the hospital is and how it looks (TENANCY_DESIGN.md §3, task P1-T2a).

One row per tenant schema. The registry row in ``public`` (``ihr_platform.Hospital``) holds only
what the platform needs to route and operate a tenant; everything the hospital itself owns lives
here, inside its own schema.
"""

import hashlib

from django.core.exceptions import ValidationError
from django.db import connection, models

from ihr_tenant_settings.india import GST_STATE_CODES
from ihr_tenant_settings.validators import (
    logo_content_type,
    validate_colour,
    validate_gstin,
    validate_pin_code,
)

DEFAULT_COLOUR = "#0f766e"


class HospitalProfile(models.Model):
    # Exactly one row per schema: the unique constant column makes a second row impossible.
    singleton = models.BooleanField(default=True, unique=True, editable=False)

    display_name = models.CharField(max_length=255)  # as long as the registry name
    short_name = models.CharField(max_length=40, blank=True)
    legal_name = models.CharField(max_length=200, blank=True)
    gstin = models.CharField(max_length=15, blank=True, validators=[validate_gstin])

    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    district = models.CharField(max_length=100, blank=True)
    state_code = models.CharField(
        max_length=2, blank=True, choices=sorted(GST_STATE_CODES.items())
    )
    pin_code = models.CharField(
        max_length=6, blank=True, validators=[validate_pin_code]
    )

    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)

    # Registration numbers printed on letterheads and claims.
    clinical_establishment_reg_no = models.CharField(max_length=64, blank=True)
    nabh_accreditation_no = models.CharField(max_length=64, blank=True)
    hfr_id = models.CharField("ABDM HFR id", max_length=32, blank=True)
    rohini_id = models.CharField("ROHINI id", max_length=32, blank=True)

    letterhead_footer = models.TextField(max_length=500, blank=True)
    primary_colour = models.CharField(
        max_length=7, default=DEFAULT_COLOUR, validators=[validate_colour]
    )
    logo = models.BinaryField(null=True, blank=True)
    logo_content_type = models.CharField(max_length=20, blank=True, editable=False)
    logo_sha256 = models.CharField(max_length=64, blank=True, editable=False)

    updated_on = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self):
        if self.gstin and self.state_code and self.gstin[:2] != self.state_code:
            raise ValidationError(
                {"gstin": "GSTIN state code does not match the address state."}
            )
        if self.logo:
            data = bytes(self.logo)
            self.logo_content_type = logo_content_type(data)
            self.logo_sha256 = hashlib.sha256(data).hexdigest()
        else:
            self.logo_content_type = ""
            self.logo_sha256 = ""

    @property
    def state_name(self):
        return GST_STATE_CODES.get(self.state_code, "")

    @classmethod
    def current(cls, *, with_logo=False):
        """This schema's profile. Never writes: provisioning (migration 0002) creates the row.

        If the row is missing, an unsaved profile named after the registry entry is returned, so
        a public page view can never race another one to insert it.
        """
        rows = cls.objects.filter(singleton=True)
        profile = (rows if with_logo else rows.defer("logo")).first()
        return profile or cls(display_name=_registry_name())


def _registry_name():
    from ihr_platform.models import Hospital

    tenant = getattr(connection, "tenant", None)
    if getattr(tenant, "name", ""):
        return tenant.name
    name = (
        Hospital.objects.filter(schema_name=getattr(connection, "schema_name", ""))
        .values_list("name", flat=True)
        .first()
    )
    return name or "Hospital"
