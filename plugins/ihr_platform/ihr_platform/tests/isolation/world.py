"""Two richly seeded tenants for the isolation suite (TENANCY_DESIGN.md §11.2).

Each tenant gets CARE's full demo data (facilities, staff, patients, encounters, billing,
inventory, scheduling) by running CARE's own fixture script against that tenant's host.
Record ids (external_id UUIDs) are random per tenant because tenants are never cloned. After
seeding, facility, patient and staff names get a per-tenant canary word, so a leak is visible even
where a response carries names but no ids.

Integer primary keys are deliberately the same in both tenants (same fixture script, same order):
a cache keyed by pk without the tenant then hands one tenant the other's object, which shows up
as a foreign UUID or canary in the crawl.
"""

import re
from dataclasses import dataclass, field

from django.apps import apps
from django.db import connection
from django_tenants.utils import schema_context

from ihr_platform.models import Domain, Hospital
from ihr_platform.seeding import load_demo_data

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@dataclass
class Tenant:
    hospital: Hospital
    domain: str
    canary: str = ""  # word added to this tenant's facility, patient and staff names
    ids: set = field(default_factory=set)  # every external_id in this tenant
    first_id_by_model: dict = field(
        default_factory=dict
    )  # model label -> one external_id
    first_slug_by_model: dict = field(default_factory=dict)  # model label -> one slug
    username: str = ""  # a non-admin staff username
    admin: object = None

    @property
    def schema(self):
        return self.hospital.schema_name


def _create(slug, name, domain, canary):
    with schema_context("public"):
        hospital = Hospital.objects.filter(slug=slug).first()
        if hospital is None:
            hospital = Hospital(slug=slug, name=name, status=Hospital.Status.ACTIVE)
            hospital.save()
        Domain.objects.get_or_create(domain=domain, defaults={"tenant": hospital})
    return Tenant(hospital=hospital, domain=domain, canary=canary)


def _seed(tenant):
    load_demo_data(tenant.schema, tenant.domain)


def _add_canaries(tenant):
    from care.emr.models import Patient
    from care.facility.models import Facility
    from care.users.models import User
    from django.core.cache import cache
    from django.db.models import F, Value
    from django.db.models.functions import Concat

    with schema_context(tenant.schema):
        prefix = Value(f"{tenant.canary} ")
        Facility.objects.update(name=Concat(prefix, F("name")))
        Patient.objects.update(name=Concat(prefix, F("name")))
        User.objects.update(first_name=Concat(prefix, F("first_name")))
        # serialized copies cached during seeding predate the canaries (this tenant's keys only)
        cache.delete_pattern("*")


def _collect_ids(tenant):
    from care.users.models import User

    with schema_context(tenant.schema):
        for model in apps.get_models():
            if model._meta.app_label not in {"emr", "facility", "users", "security"}:
                continue
            fields = {f.name for f in model._meta.get_fields()}
            manager = getattr(model, "all_objects", None) or model._default_manager
            label = model._meta.label_lower
            if "external_id" in fields:
                values = [
                    str(v)
                    for v in manager.order_by("pk").values_list(
                        "external_id", flat=True
                    )
                ]
                tenant.ids.update(values)
                if values:
                    tenant.first_id_by_model[label] = values[0]
            if "slug" in fields:
                slug = manager.order_by("pk").values_list("slug", flat=True).first()
                if slug:
                    tenant.first_slug_by_model[label] = slug
        tenant.admin = User.objects.get(username="admin")
        tenant.username = (
            User.objects.exclude(username="admin")
            .order_by("pk")
            .values_list("username", flat=True)
            .first()
        )


def build_world():
    a = _create(
        "iso-alpha", "Isolation Alpha Pvt Ltd", "alpha.iso.test", "Ihrcanaryalpha"
    )
    b = _create("iso-beta", "Isolation Beta LLP", "beta.iso.test", "Ihrcanarybeta")
    for tenant in (a, b):
        _seed(tenant)
        _add_canaries(tenant)
        _collect_ids(tenant)
    return a, b


def drop_world(*tenants):
    with schema_context("public"), connection.cursor() as cursor:
        for tenant in tenants:
            Domain.objects.filter(tenant=tenant.hospital).delete()
            Hospital.objects.filter(pk=tenant.hospital.pk).delete()
            cursor.execute(f'DROP SCHEMA IF EXISTS "{tenant.schema}" CASCADE')
