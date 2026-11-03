# IndieHR local test users (P0-T11). Runs inside the CARE API container via
# `python manage.py shell` after `load_fixtures` (see scripts/care-dev.sh seed).
#
# - adds users CARE's fixtures lack: a pharmacist and a billing/cashier user
#   (cloned memberships from care-staff, with their own role)
# - replaces every fixture password (CARE's are public in its source) with
#   the one passed in TEST_PASSWORD
# Prints a JSON summary on the last line.
import json
import os

from django.db import transaction

from care.emr.models.organization import FacilityOrganizationUser, OrganizationUser
from care.security.models import RoleModel
from care.users.models import User

PASSWORD = os.environ["TEST_PASSWORD"]
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

with transaction.atomic():
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

    reset = 0
    for user in User.objects.filter(username__in=FIXTURE_USERS + [u[0] for u in EXTRA_USERS]):
        user.set_password(PASSWORD)
        user.save(update_fields=["password"])
        reset += 1

print(json.dumps({"created": created, "passwords_reset": reset}))
