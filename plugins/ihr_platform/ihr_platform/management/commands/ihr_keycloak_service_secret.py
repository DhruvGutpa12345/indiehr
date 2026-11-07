"""Print one worker's narrow Keycloak secret (P1-T5f), for its own environment file.

    manage.py ihr_keycloak_service_secret onboarding|staff

Run by an operator with the bootstrap credential (scripts/care-dev.sh writes the dev files).
"""

from django.core.management.base import BaseCommand, CommandError

from ihr_platform.keycloak.accounts import PURPOSES, secret_of
from ihr_platform.keycloak.kc_http import KeycloakError
from ihr_platform.management.commands.ihr_keycloak_sync import keycloak_admin


class Command(BaseCommand):
    help = "Print a worker's narrow Keycloak secret."

    def add_arguments(self, parser):
        parser.add_argument("purpose", choices=sorted(PURPOSES))

    def handle(self, *args, purpose, **options):
        try:
            self.stdout.write(secret_of(keycloak_admin(), PURPOSES[purpose]))
        except KeycloakError as exc:
            raise CommandError(str(exc)) from exc
