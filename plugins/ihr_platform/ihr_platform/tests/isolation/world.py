"""Two richly seeded tenants for the isolation suite (TENANCY_DESIGN.md §11.2).

Each tenant gets CARE's full demo data (facilities, staff, patients, encounters, billing,
inventory, scheduling) by running CARE's own fixture script against that tenant's host.
Record ids (external_id UUIDs) are random per tenant because tenants are never cloned. After
seeding, facility, patient and staff names get a per-tenant canary word, so a leak is visible even
where a response carries names but no ids.

Integer primary keys are deliberately the same in both tenants (same fixture script, same order):
a cache keyed by pk without the tenant then hands one tenant the other's object, which shows up
as a foreign UUID or canary in the crawl.

The PIN directory's test sample is loaded into ``public``, and each tenant gets the states and
districts it names, so the PIN lookup answers with that tenant's own organisations (P1-T16a).

The models the fixtures leave empty get one row each from ``extras.py`` (P1-T16b), so the crawl
reaches their detail routes too.

Every write to ``public`` (the PIN sample, each hospital) registers its own removal right away
through ``cleanup`` (the test class's ``addClassCleanup``), so nothing outlives a failed set-up.

With ``IHR_DB_ROLES=test`` the crawl runs as the app role (P1-T19a); what only owner-run
processes do in production (making a hospital and its schema, loading the PIN directory,
dropping it all again) runs inside ``as_owner()``. The seeding itself drives the API, as the
app role.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import ihr_tenant_settings
from django.apps import apps
from django.db import connection
from django_tenants.utils import schema_context

from ihr_platform.models import Domain, Hospital
from ihr_platform.seeding import load_demo_data
from ihr_platform.testing import as_owner
from ihr_platform.tests.isolation.extras import seed_extras

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
PIN_SAMPLE = (
    Path(ihr_tenant_settings.__file__).parent / "tests" / "data" / "pincodes_sample.csv"
)
PINCODE = "411001"  # Pune, Maharashtra: in the sample


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
    # path converter <pk>: the audit entry of the canary step's last Patient UPDATE, so its
    # values hold this tenant's canary and its patient its own id (_collect_ids)
    audit_entry_id: str = ""
    pincode: str = PINCODE  # path converter <pincode>
    # detail routes of rows seeded for the crawl (P1-T16b), each checked to answer its owner
    seeded_routes: list = field(default_factory=list)

    @property
    def schema(self):
        return self.hospital.schema_name


def _create(slug, name, domain, canary, cleanup):
    with schema_context("public"), as_owner():
        hospital = Hospital.objects.filter(slug=slug).first()
        if hospital is None:
            hospital = Hospital(slug=slug, name=name, status=Hospital.Status.ACTIVE)
            hospital.save()
        tenant = Tenant(hospital=hospital, domain=domain, canary=canary)
        cleanup(drop_world, tenant)
        Domain.objects.get_or_create(domain=domain, defaults={"tenant": hospital})
    return tenant


def _load_pin_sample(cleanup):
    from ihr_platform import pincodes
    from ihr_platform.models import PinDirectoryLoad, PostOffice

    def remove():
        with schema_context("public"), as_owner():
            PostOffice.objects.all().delete()
            PinDirectoryLoad.objects.all().delete()

    with schema_context("public"), as_owner():  # ihr_load_pincodes: an admin one-off
        cleanup(remove)
        pincodes.load(PIN_SAMPLE.read_bytes(), PIN_SAMPLE.name, date(2026, 10, 1))


def _seed(tenant):
    load_demo_data(tenant.schema, tenant.domain)


def _make_first_facility_public(tenant):
    """CARE's demo fixtures make the first facility public at random (an unseeded Faker
    boolean), and fill() puts that facility into /api/v1/getallfacilities/<id>/, which shows only
    public facilities. Fixing it public means that route answers every run: anonymously, to its
    own admin, and refuses the other tenant's id in the cross-id probe."""
    from care.facility.models import Facility

    with schema_context(tenant.schema):
        first = Facility.objects.order_by("pk").values("pk")[:1]
        Facility.objects.filter(pk__in=first).update(is_public=True)


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


def _add_geography(tenant):
    """This tenant's own state and district organisations for the PIN sample."""
    from ihr_tenant_settings import geography

    with schema_context(tenant.schema):
        geography.load_all()


def _make_admin_hospital_wide(tenant):
    """The crawl's admin (a CARE superuser) also needs an admin role on a top-level
    organisation: the audit viewer has no superuser bypass (ihr_audit IsHospitalAdmin)."""
    from care.emr.models.organization import Organization, OrganizationUser
    from care.security.models import RolePermission
    from care.security.permissions.user import UserPermissions
    from care.users.models import User
    from ihr_identity.staff_access import hospital_wide_admin

    with schema_context(tenant.schema):
        admin = User.objects.get(username="admin")
        if hospital_wide_admin(admin):
            return
        role_id = (
            RolePermission.objects.filter(
                permission__slug=UserPermissions.can_create_user.name
            )
            .order_by("role_id")
            .values_list("role_id", flat=True)
            .first()
        )
        organization = (
            Organization.objects.filter(parent__isnull=True, active=True, deleted=False)
            .order_by("id")
            .first()
        )
        OrganizationUser.objects.create(
            organization=organization, user=admin, role_id=role_id
        )


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
        # An entry the canary step wrote (a Patient name UPDATE): its values carry the canary
        # and its patient this tenant's own id, so the owner test can tell its own entry from
        # the other tenant's entry with the same integer id.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT max(id) FROM ihr_audit_entry WHERE tbl = 'emr_patient' AND op = 'U'"
            )
            entry = cursor.fetchone()[0]
        tenant.audit_entry_id = str(entry) if entry is not None else ""


def build_world(cleanup):
    """Both tenants, seeded. ``cleanup(fn, *args)`` registers the removal of each write to
    ``public`` as soon as it is made (the test class's ``addClassCleanup``)."""
    _load_pin_sample(cleanup)
    a = _create(
        "iso-alpha",
        "Isolation Alpha Pvt Ltd",
        "alpha.iso.test",
        "Ihrcanaryalpha",
        cleanup,
    )
    b = _create(
        "iso-beta", "Isolation Beta LLP", "beta.iso.test", "Ihrcanarybeta", cleanup
    )
    for tenant in (a, b):
        _seed(tenant)
        _make_first_facility_public(tenant)  # before the canaries' cache flush
        seed_extras(tenant)  # before the canaries' cache flush and before _collect_ids
        _add_canaries(tenant)
        _add_geography(tenant)
        _make_admin_hospital_wide(tenant)
        _collect_ids(tenant)
    return a, b


def drop_world(*tenants):
    with schema_context("public"), as_owner(), connection.cursor() as cursor:
        for tenant in tenants:
            Domain.objects.filter(tenant=tenant.hospital).delete()
            Hospital.objects.filter(pk=tenant.hospital.pk).delete()
            cursor.execute(f'DROP SCHEMA IF EXISTS "{tenant.schema}" CASCADE')
