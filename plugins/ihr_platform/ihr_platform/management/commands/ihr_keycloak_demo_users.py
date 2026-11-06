"""Synthetic Keycloak staff for the two demo hospitals (development only, P1-T3).

    manage.py ihr_keycloak_demo_users      # run through `scripts/care-dev.sh keycloak seed`

Recreates each demo user with a fresh random password and, for most, a fresh TOTP secret, and
adds them to their hospitals. Prints one JSON line (passwords and secrets included) for
care-dev.sh to write into the git-ignored deploy/seed/TEST_USERS.md.
"""

import json
import secrets

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ihr_platform.keycloak import totp
from ihr_platform.keycloak.hospital import ensure_member, hospital_ids
from ihr_platform.keycloak.kc_http import KeycloakError
from ihr_platform.keycloak.users import create_user
from ihr_platform.management.commands.ihr_keycloak_sync import keycloak_admin
from ihr_platform.models import Hospital

# username, first name, last name, hospitals, TOTP pre-set (False: enrols at first login)
DEMO_STAFF = [
    ("dr.meera", "Meera", "Iyer", ["alpha", "beta"], True),
    ("nurse.asha", "Asha", "Nair", ["alpha"], True),
    ("admin.bhavesh", "Bhavesh", "Rao", ["beta"], True),
    ("new.neha", "Neha", "Kulkarni", ["alpha"], False),
]


def new_password():
    return f"Ihr-{secrets.token_urlsafe(12)}-7"


class Command(BaseCommand):
    help = "Create the synthetic Keycloak demo staff (development only)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Demo users are for local development only.")
        admin = keycloak_admin()
        hospitals = {
            h.slug: h
            for h in Hospital.objects.filter(slug__in=["alpha", "beta"])
            if h.keycloak_org_id
        }
        if set(hospitals) != {"alpha", "beta"}:
            raise CommandError("Run `care-dev.sh seed` and `keycloak up` first.")
        users = []
        try:
            ids = {
                slug: hospital_ids(admin, slug, str(h.keycloak_org_id))
                for slug, h in hospitals.items()
            }
            for username, first, last, member_of, has_otp in DEMO_STAFF:
                password = new_password()
                otp_secret = totp.new_secret() if has_otp else None
                user_id = create_user(
                    admin, username, first, last, password, otp_secret
                )
                for slug in member_of:
                    ensure_member(admin, ids[slug], user_id)
                users.append(
                    {
                        "username": username,
                        "name": f"{first} {last}",
                        "password": password,
                        "otp_secret": otp_secret,
                        "hospitals": member_of,
                    }
                )
        except KeycloakError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            json.dumps(
                {
                    "keycloak": settings.IHR_KEYCLOAK_PUBLIC_URL,
                    "realm": settings.IHR_KEYCLOAK_REALM,
                    "users": users,
                }
            )
        )
