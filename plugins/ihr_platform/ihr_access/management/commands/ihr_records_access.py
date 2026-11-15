"""Give or end a records grant from the command line (development only, P1-T11b4).

    manage.py ihr_records_access --slug alpha <user> <patient id> --reason <code> [--reference R]
    manage.py ihr_records_access --slug alpha <user> --end <grant id>
    scripts/care-dev.sh records-access mrd.farah <patient id> --reason insurer --reference TEST-1

``<user>`` is a demo Keycloak login (ihr_keycloak_demo_users.DEMO_STAFF, e.g. ``mrd.farah``) or a
CARE username in that hospital (``care-mrd``). It calls the same grant code as
``POST /api/ihr/records-access/`` (records.create_grant: the user's own right, the reason codes,
the reference rule, one live grant per patient), and ``--end`` the same end routine as the
DELETE endpoint (records.end_grant). The audit trail names the command as the writer
(``system:manage:ihr_records_access``). Refuses to run outside development.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context

from ihr_access import records


def care_username(slug, name):
    """A demo Keycloak login's CARE user in this hospital, else ``name`` itself."""
    from ihr_platform.management.commands.ihr_keycloak_demo_users import DEMO_STAFF

    for username, _first, _last, links, _totp in DEMO_STAFF:
        if username == name:
            if slug not in links:
                msg = f"{name} is not linked in {slug}."
                raise CommandError(msg)
            return links[slug]
    return name


class Command(BaseCommand):
    help = "Give or end an MRD records grant (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--slug", default="alpha")
        parser.add_argument("user")
        parser.add_argument("patient", nargs="?")
        parser.add_argument("--reason")
        parser.add_argument("--reference", default="")
        parser.add_argument("--end", type=int, metavar="GRANT_ID")

    def handle(self, *args, slug, user, patient, reason, reference, end, **options):
        if not settings.DEBUG or settings.IHR_TENANT_DOMAIN_SUFFIX != "localhost":
            raise CommandError("Development only.")
        from ihr_platform.models import Hospital

        hospital = Hospital.objects.filter(slug=slug).first()
        if hospital is None:
            msg = f"No hospital {slug!r}."
            raise CommandError(msg)
        with schema_context(hospital.schema_name):
            from care.users.models import User

            from ihr_access.models import RecordsGrant

            username = care_username(slug, user)
            person = User.objects.filter(username=username).first()
            if person is None:
                msg = f"No user {username!r} in {slug}."
                raise CommandError(msg)
            if end is not None:
                grant = RecordsGrant.objects.filter(pk=end, user=person).first()
                if grant is None:
                    msg = f"No records grant {end} of {username}."
                    raise CommandError(msg)
                if not records.end_grant(grant, "ended"):
                    msg = f"Records grant {end} had already ended."
                    raise CommandError(msg)
                self.stdout.write(f"Records grant {end} ended ({username}, {slug}).")
                return
            if not patient:
                msg = "Name the patient (its id, from the web app's address bar)."
                raise CommandError(msg)
            try:
                grant = records.create_grant(person, patient, reason, reference)
            except records.GrantRefusedError as refused:
                raise CommandError(refused.message) from None
            self.stdout.write(
                f"Records grant {grant.pk} for {username} on patient {patient} "
                f"({grant.reason}), until {grant.expires_at.astimezone(records.IST):%H:%M} "
                f"IST. End it: scripts/care-dev.sh records-access {user} --end {grant.pk} "
                f"--slug {slug}"
            )
