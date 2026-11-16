"""Two synthetic demo hospitals for local testing (development only, task P1-T2b).

    TEST_PASSWORD=... manage.py ihr_seed_demo --slug alpha
    TEST_PASSWORD=... manage.py ihr_seed_demo --slug alpha --passwords-only

Creates the hospital if it is missing, loads CARE's synthetic demo data into it once, sets its
profile, adds a pharmacist and a billing user, adds one user for each of our eight roles placed as
design-b.md §4 says (P1-T11b1), puts one account's visit in a bed as an inpatient stay (P1-T11c2),
and sets every test user's password to $TEST_PASSWORD (CARE's fixture passwords are public).
Prints one JSON line.
Run through ``scripts/care-dev.sh seed``.
"""

import json
import os

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_tenants.utils import schema_context
from ihr_tenant_settings.models import HospitalProfile

from ihr_platform.models import Domain, Hospital
from ihr_platform.seeding import add_inpatient_stay, load_demo_data

# Synthetic hospitals: names, places and phone numbers are made up.
DEMO_HOSPITALS = {
    "alpha": {
        "display_name": "Alpha Multispeciality Hospital",
        "short_name": "Alpha Hospital",
        "primary_colour": "#0f766e",
        "city": "Pune",
        "district": "Pune",
        "state_code": "27",
        "pin_code": "411001",
        "phone": "+912000000001",
    },
    "beta": {
        "display_name": "Beta Children's Hospital",
        "short_name": "Beta Hospital",
        "primary_colour": "#7c3aed",
        "city": "Bengaluru",
        "district": "Bengaluru Urban",
        "state_code": "29",
        "pin_code": "560001",
        "phone": "+918000000001",
    },
}
FIXTURE_USERS = [
    "admin",
    "care-doctor",
    "care-staff",
    "care-nurse",
    "care-admin",
    "care-volunteer",
    "care-fac-admin",
    "care-role-admin",
    "care-role-manager",
    "care-role-member",
]
# username, CARE role, first name, last name, synthetic phone
EXTRA_USERS = [
    ("care-pharmacist", "Pharmacist", "Pooja", "Pharmacist", "+919999900011"),
    ("care-billing", "Staff", "Bharat", "Billing", "+919999900012"),
]
# Our eight roles (P1-T11b1): username, role, first name, last name, synthetic phone, department
# (None: the facility root). Placed explicitly, never by copying care-staff's memberships, which
# include a government organisation (the placement trigger refuses our roles there).
ROLE_USERS = [
    (
        "care-receptionist",
        "Receptionist",
        "Ravi",
        "Receptionist",
        "+919999900013",
        None,
    ),
    ("care-cashier", "Cashier", "Kavya", "Cashier", "+919999900014", None),
    (
        "care-labtech",
        "Lab Technician",
        "Imran",
        "Labtech",
        "+919999900015",
        "Laboratory",
    ),
    (
        "care-radiologist",
        "Radiologist",
        "Sunita",
        "Radiologist",
        "+919999900016",
        "Radiology",
    ),
    ("care-tpa", "TPA Desk", "Joseph", "Tpa", "+919999900017", None),
    ("care-mrd", "MRD", "Farah", "Records", "+919999900018", None),
    ("care-storekeeper", "Store Keeper", "Gopal", "Stores", "+919999900019", "Stores"),
    ("care-owner", "Owner", "Vikram", "Owner", "+919999900020", None),
]
# Department -> a location linked to it (lab queues, imaging rooms and stores are per location)
DEPARTMENT_LOCATIONS = {
    "Laboratory": "Sample Collection Room",
    "Radiology": "X-Ray Room",
    "Stores": "Main Store",
}


class Command(BaseCommand):
    help = "Create or refresh a synthetic demo hospital (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True, choices=sorted(DEMO_HOSPITALS))
        parser.add_argument("--passwords-only", action="store_true")

    def handle(self, *args, slug, passwords_only, **options):
        if not settings.DEBUG:
            raise CommandError("Demo hospitals are for local development only.")
        password = os.environ.get("TEST_PASSWORD")
        if not password:
            raise CommandError("Set TEST_PASSWORD.")
        profile = DEMO_HOSPITALS[slug]

        hospital = Hospital.objects.filter(slug=slug).first()
        if hospital is None:
            if passwords_only:
                msg = f"No hospital {slug!r}: run a full seed first."
                raise CommandError(msg)
            call_command("ihr_create_tenant", slug=slug, name=profile["display_name"])
            hospital = Hospital.objects.get(slug=slug)
        domain = Domain.objects.get(tenant=hospital, is_primary=True).domain

        with schema_context(hospital.schema_name):
            from care.users.models import User

            loaded = False
            stay = None
            if not passwords_only:
                if not User.objects.filter(username="care-doctor").exists():
                    load_demo_data(hospital.schema_name, domain)
                    loaded = True
                current = HospitalProfile.current()
                for field, value in profile.items():
                    setattr(current, field, value)
                current.save()
                # an inpatient with a bed stay and an account: the billing desks' screens
                stay = add_inpatient_stay()
            created = _add_extra_users()
            created += _add_role_users()
            reset = _set_passwords(password)

        self.stdout.write(
            json.dumps(
                {
                    "hospital": slug,
                    "address": domain,
                    "demo_data_loaded": loaded,
                    "inpatient_stay_added": stay is not None,
                    "users_created": created,
                    "passwords_reset": reset,
                }
            )
        )


@transaction.atomic
def _add_extra_users():
    from care.emr.models.organization import (
        FacilityOrganizationUser,
        OrganizationUser,
    )
    from care.security.models import RoleModel
    from care.users.models import User

    template = User.objects.get(username="care-staff")
    created = []
    for username, role_name, first, last, phone in EXTRA_USERS:
        role = RoleModel.objects.get(name=role_name)
        user, was_created = User.objects.get_or_create(
            username=username,
            defaults={
                "first_name": first,
                "last_name": last,
                "email": f"{username}@care.test",
                "phone_number": phone,
                "user_type": template.user_type,
                "gender": template.gender,
                "geo_organization": template.geo_organization,
            },
        )
        for membership in OrganizationUser.objects.filter(user=template):
            OrganizationUser.objects.get_or_create(
                organization=membership.organization, user=user, defaults={"role": role}
            )
        for membership in FacilityOrganizationUser.objects.filter(user=template):
            FacilityOrganizationUser.objects.get_or_create(
                organization=membership.organization, user=user, defaults={"role": role}
            )
        if was_created:
            created.append(username)
    return created


@transaction.atomic
def _set_passwords(password):
    from care.users.models import User

    names = FIXTURE_USERS + [u[0] for u in EXTRA_USERS] + [u[0] for u in ROLE_USERS]
    reset = 0
    for user in User.objects.filter(username__in=names):
        user.set_password(password)
        user.save(update_fields=["password"])
        reset += 1
    return reset


def _department(facility, name):
    """The facility's department ``name`` (CARE's demo makes Laboratory and Radiology; Stores is
    ours), with one location linked to it."""
    from care.emr.models.location import FacilityLocation, FacilityLocationOrganization
    from care.emr.models.organization import FacilityOrganization

    department = FacilityOrganization.objects.filter(
        facility=facility, name=name
    ).first()
    if department is None:
        department = FacilityOrganization.objects.create(
            facility=facility, name=name, org_type="team"
        )
    location_name = DEPARTMENT_LOCATIONS[name]
    location = FacilityLocation.objects.filter(
        facility=facility, name=location_name
    ).first()
    if location is None:
        location = FacilityLocation.objects.create(
            facility=facility,
            name=location_name,
            description=f"{name} (synthetic demo)",
            status="active",
            operational_status="O",
            mode="kind",
            form="ro",
        )
    FacilityLocationOrganization.objects.get_or_create(
        location=location, organization=department
    )
    return department


@transaction.atomic
def _add_role_users():
    from care.emr.models.organization import FacilityOrganizationUser
    from care.security.models import RoleModel
    from care.users.models import User

    template = User.objects.get(username="care-staff")
    # The facility the demo's staff work in (CARE's "FACILITY WITH PATIENTS")
    facility = (
        FacilityOrganizationUser.objects.filter(user=template)
        .select_related("organization__facility")
        .first()
        .organization.facility
    )
    created = []
    for username, role_name, first, last, phone, department in ROLE_USERS:
        role = RoleModel.objects.get(name=role_name, is_system=True)
        user, was_created = User.objects.get_or_create(
            username=username,
            defaults={
                "first_name": first,
                "last_name": last,
                "email": f"{username}@care.test",
                "phone_number": phone,
                "user_type": template.user_type,
                "gender": template.gender,
                "geo_organization": template.geo_organization,
            },
        )
        organization = (
            _department(facility, department)
            if department
            else facility.default_internal_organization
        )
        FacilityOrganizationUser.objects.get_or_create(
            organization=organization, user=user, defaults={"role": role}
        )
        if was_created:
            created.append(username)
    return created
