"""Check this container's database role before it starts its process (P1-T19a).

    manage.py ihr_db_role_check --for <process>            (exit 0: fine, or IHR_DB_ROLES is off)
    manage.py ihr_db_role_check --for <process> --report   (also list every difference)

``--for`` names the process the container runs next: backend, celery-worker, celery-beat or
staff-worker (the app role), or migrate, admin or provisioner (the only ones that may run as
the owner). The check judges that process, never its own command line (review S5): an
owner-declared celery container is refused here, exits non-zero and restart-loops visibly.

Run before ``exec`` by every runtime container, and by the migrate one-shot before
``migrate_schemas`` (wired with the move of the roles, P1-T19b). It makes the process's first
connection, which runs the same check as every process does with ``IHR_DB_ROLES=required``
(ihr_platform/db_roles.py): the role must be what ``IHR_DB_ROLE`` declares (app or owner), and
the app role must have no attribute, membership, stored setting, CREATE or TEMPORARY anywhere,
owned object, right beyond the reviewed lists or broken control (the provisioner's queue guard,
large objects, a definer function it may execute) outside a hospital's schema. A refusal also
writes the fixed line ``ihr-db-role: refused role=<app|owner> reason=<code>`` that the log alert
DatabaseRoleRefused watches. Such a right or control inside one hospital's schema gates that
hospital instead (``ihr-db-role: gated schema=<schema> reason=<code>``, DatabaseSchemaGated). ``--for migrate`` also checks the roles themselves (attributes, memberships, stored
settings), the database's grants and the owner's default privileges before anything is
migrated (review C6), so such a problem stops the start before its post_migrate.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from ihr_platform import db_roles


class Command(BaseCommand):
    help = "Check this process's database role (IHR_DB_ROLES=required)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--for",
            dest="guards",
            choices=db_roles.PROCESSES,
            help="the process this container runs next (only migrate, admin and "
            "provisioner may run as the owner)",
        )
        parser.add_argument(
            "--report",
            action="store_true",
            help="also list how the grants differ from the model (changes nothing)",
        )

    def handle(self, *args, guards=None, report=False, **options):
        current = db_roles.mode()
        if current == "off":
            self.stdout.write("IHR_DB_ROLES is off: nothing to check (P1-T19)")
            return
        if current != "required":
            msg = (
                f"IHR_DB_ROLES={current}: the role check is for deployments (required)"
            )
            raise CommandError(msg)
        try:
            connection.ensure_connection()  # the first connection runs the check
        except db_roles.RoleRefused as exc:
            raise CommandError(str(exc)) from None
        role = db_roles.declared()
        if role == "owner" and guards == "migrate":
            problems = [
                f for f in db_roles.report(connection, []) if f.kind == "problem"
            ]
            for finding in problems:
                self.stderr.write(str(finding))
            if problems:
                msg = (
                    f"{len(problems)} problem(s) with the database roles: fix them as "
                    "postgres before migrating (P1-T19)"
                )
                raise CommandError(msg)
        self.stdout.write(f"database role ok: {role} ({guards or 'not named'})")
        if report:
            for finding in db_roles.report(connection):
                self.stdout.write(str(finding))
