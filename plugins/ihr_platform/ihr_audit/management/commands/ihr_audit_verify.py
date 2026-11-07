"""Check a hospital's audit trail: triggers on every table, and the hash chain recomputed.

    ihr_audit_verify --slug alpha [--seal]      one hospital
    ihr_audit_verify --all [--seal]             every hospital that has a schema

``--seal`` seals what is final first (what the every-minute job does). Exit status 1 on any
problem, so it can run from cron or CI.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django_tenants.utils import schema_context
from ihr_platform.models import Hospital

from ihr_audit.install import coverage
from ihr_audit.seal import seal_once, verify


class Command(BaseCommand):
    help = "Verify the audit trail's triggers and hash chain"

    def add_arguments(self, parser):
        which = parser.add_mutually_exclusive_group(required=True)
        which.add_argument("--slug")
        which.add_argument("--all", action="store_true")
        parser.add_argument("--seal", action="store_true")
        parser.add_argument("--stale-minutes", type=int, default=10)

    def handle(self, *args, **options):
        hospitals = Hospital.objects.exclude(schema_name="public").order_by("slug")
        if options["slug"]:
            hospitals = hospitals.filter(slug=options["slug"])
            if not hospitals:
                msg = f"no hospital with slug {options['slug']!r}"
                raise CommandError(msg)
        failed = False
        for hospital in hospitals:
            with schema_context(hospital.schema_name):
                if options["seal"]:
                    seal_once(hospital.schema_name)
                problems = coverage(connection, hospital.schema_name)
                chain, summary = verify(hospital.schema_name, options["stale_minutes"])
            problems += chain
            self.stdout.write(
                f"{hospital.slug}: {summary['seals']} seals, "
                f"{summary['sealed_entries']} entries sealed, "
                f"{summary['unsealed_entries']} not yet, head {summary['head'][:16]}"
            )
            if summary["held_back"]:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {summary['held_back']} entries older than "
                        f"{options['stale_minutes']} minutes wait for a transaction "
                        "that is still running (check pg_stat_activity)"
                    )
                )
            for problem in problems:
                failed = True
                self.stdout.write(self.style.ERROR(f"  {problem}"))
        if failed:
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("audit trail OK"))
