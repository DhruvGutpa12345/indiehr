"""Link a hospital's CARE user to a person's Keycloak account (operators and dev; P1-T4).

    manage.py ihr_link_staff --slug alpha --username care-doctor --sub <keycloak user id> --yes

Never matches by e-mail. Refuses superusers (no in-hospital superuser, Q-T5). P1-T5 replaces
this with the invitation flow.
"""

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django_tenants.utils import schema_context
from ihr_platform import release
from ihr_platform.models import Hospital

from ihr_identity.models import StaffIdentity


class Command(BaseCommand):
    help = "Link a CARE user in one hospital to a Keycloak user id."

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True)
        parser.add_argument("--username", required=True)
        parser.add_argument("--sub", required=True, type=uuid.UUID)
        parser.add_argument("--yes", action="store_true", help="Confirm the link")

    def handle(self, *args, slug, username, sub, yes, **options):
        hospital = (
            Hospital.objects.filter(slug=slug).exclude(schema_name="public").first()
        )
        if hospital is None:
            msg = f"No hospital {slug!r}"
            raise CommandError(msg)
        release.require_ready(hospital)  # never into a held or older schema (S6)
        with schema_context(hospital.schema_name):
            from care.users.models import User

            user = User.objects.filter(username=username).first()
            if user is None:
                msg = f"No user {username!r} in {slug}"
                raise CommandError(msg)
            if user.is_superuser or user.is_service_account:
                raise CommandError(
                    "Superusers and machine accounts cannot sign in as staff."
                )
            self.stdout.write(f"{slug}: CARE user {username} <-> Keycloak user {sub}")
            if not yes:
                raise CommandError("Check the line above, then repeat with --yes.")
            StaffIdentity.objects.filter(keycloak_sub=sub).exclude(user=user).delete()
            # A (re)link never revives older sessions: the next sign-in must be a fresh one.
            StaffIdentity.objects.update_or_create(
                user=user,
                defaults={"keycloak_sub": sub, "sessions_valid_after": timezone.now()},
            )
        self.stdout.write(self.style.SUCCESS("Linked."))
