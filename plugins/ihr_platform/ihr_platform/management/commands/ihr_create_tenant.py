"""Create a tenant: schema + every migration + CARE's roles and value sets (TENANCY_DESIGN.md §7.1).

v1 provisioning never clones a template schema. Must run as the database owner role.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context

from ihr_platform.models import Domain, Hospital


class Command(BaseCommand):
    help = "Create a tenant (legal entity) with its own schema and domain(s)."

    def add_arguments(self, parser):
        parser.add_argument("--slug", help="Subdomain label, e.g. sunrise")
        parser.add_argument("--name", help="Display name of the legal entity")
        parser.add_argument(
            "--domain", action="append", default=[], help="Host name; repeatable"
        )
        parser.add_argument(
            "--public",
            action="store_true",
            help="Create the platform (public schema) tenant instead",
        )

    def handle(self, *args, **options):
        if options["public"]:
            hospital, _ = Hospital.objects.get_or_create(
                schema_name="public",
                defaults={
                    "slug": "public",
                    "name": "IndieHR platform",
                    "status": Hospital.Status.ACTIVE,
                },
            )
            self._add_domains(hospital, options["domain"])
            self.stdout.write(self.style.SUCCESS(f"public tenant ready: {hospital}"))
            return

        if not options["slug"] or not options["name"] or not options["domain"]:
            raise CommandError("--slug, --name and at least one --domain are required")

        hospital = Hospital(slug=options["slug"], name=options["name"])
        hospital.save()  # creates the schema and runs every migration in it
        self._add_domains(hospital, options["domain"])

        with schema_context(hospital.schema_name):
            call_command("sync_permissions_roles", verbosity=0)
            call_command("sync_valueset", verbosity=0)

        hospital.status = Hospital.Status.ACTIVE
        hospital.save()
        self.stdout.write(self.style.SUCCESS(f"tenant ready: {hospital}"))

    def _add_domains(self, hospital, domains):
        for index, domain in enumerate(domains):
            Domain.objects.get_or_create(
                domain=domain, defaults={"tenant": hospital, "is_primary": index == 0}
            )
