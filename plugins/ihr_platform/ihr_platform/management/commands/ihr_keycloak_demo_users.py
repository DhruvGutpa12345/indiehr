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
from django_tenants.utils import schema_context

from ihr_platform.keycloak import totp
from ihr_platform.keycloak.hospital import ensure_member, hospital_ids
from ihr_platform.keycloak.kc_http import KeycloakError
from ihr_platform.keycloak.users import create_user
from ihr_platform.management.commands.ihr_keycloak_sync import keycloak_admin
from ihr_platform.models import Hospital

# username, first name, last name, {hospital: CARE user it signs in as}, TOTP pre-set
# (False: enrols at first sign-in). CARE's `admin` is a superuser and is never linked (Q-T5).
DEMO_STAFF = [
    (
        "dr.meera",
        "Meera",
        "Iyer",
        {"alpha": "care-doctor", "beta": "care-doctor"},
        True,
    ),
    ("nurse.asha", "Asha", "Nair", {"alpha": "care-nurse"}, True),
    ("admin.bhavesh", "Bhavesh", "Rao", {"beta": "care-fac-admin"}, True),
    ("new.neha", "Neha", "Kulkarni", {"alpha": "care-staff"}, False),
]


def new_password():
    return f"Ihr-{secrets.token_urlsafe(12)}-7"


def _link(hospital, username, sub):
    """The Keycloak person signs in to this hospital as its CARE user ``username``."""
    from care.users.models import User
    from ihr_identity.models import StaffIdentity

    with schema_context(hospital.schema_name):
        user = User.objects.get(username=username)
        StaffIdentity.objects.filter(user=user).delete()
        StaffIdentity.objects.create(user=user, keycloak_sub=sub)


def _demo_operator():
    """A synthetic platform operator for the console (P1-T5b): operator realm, role and
    our own operator list."""
    from ihr_platform.keycloak.kc_http import q
    from ihr_platform.keycloak.ops import OPERATOR_ROLE
    from ihr_platform.models import OperatorAccount

    ops = keycloak_admin(settings.IHR_OPS_REALM)
    password, otp_secret = new_password(), totp.new_secret()
    sub = create_user(ops, "op.owner", "Owner", "Operator", password, otp_secret)
    role = ops.get(f"roles/{q(OPERATOR_ROLE)}")
    ops.post(f"users/{sub}/role-mappings/realm", [role])
    OperatorAccount.objects.filter(name="Owner Operator (demo)").delete()
    OperatorAccount.objects.create(
        keycloak_sub=sub, name="Owner Operator (demo)", added_by="dev seed"
    )
    return {
        "username": "op.owner",
        "name": "Owner Operator",
        "password": password,
        "otp_secret": otp_secret,
        "hospitals": ["console"],
        "care_users": {"console": "operator (no CARE user)"},
    }


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
                for slug, care_user in member_of.items():
                    ensure_member(admin, ids[slug], user_id)
                    _link(hospitals[slug], care_user, user_id)
                users.append(
                    {
                        "username": username,
                        "name": f"{first} {last}",
                        "password": password,
                        "otp_secret": otp_secret,
                        "hospitals": list(member_of),
                        "care_users": member_of,
                    }
                )
            if getattr(settings, "IHR_CONSOLE_URL", ""):
                users.append(_demo_operator())
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
