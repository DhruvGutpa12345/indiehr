"""Check a hospital's audit trail: triggers on every table, and the hash chain recomputed.

    ihr_audit_verify --slug alpha [--seal]      one hospital
    ihr_audit_verify --all [--seal]             every hospital that has a schema

``--seal`` seals what is final first (what the every-minute job does). Every hospital is also
checked against every outside fingerprint (anchor.py; reader credentials from the
environment; ``--since`` limits that to recent ones for frequent runs). ``--anchor`` writes a fingerprint first (needs the writer credentials). Exit
status 1 on any problem, so it can run from cron or CI.
"""

import os
from datetime import date, datetime

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django_tenants.utils import schema_context
from ihr_platform.models import Hospital

from ihr_audit import anchor
from ihr_audit.install import coverage
from ihr_audit.seal import seal_once, verify


class Command(BaseCommand):
    help = "Verify the audit trail's triggers and hash chain"

    def add_arguments(self, parser):
        which = parser.add_mutually_exclusive_group(required=True)
        which.add_argument("--slug")
        which.add_argument("--all", action="store_true")
        parser.add_argument("--seal", action="store_true")
        parser.add_argument("--anchor", action="store_true")
        parser.add_argument(
            "--since",
            type=date.fromisoformat,
            help="read only fingerprints written from this day (YYYY-MM-DD); "
            "the default reads all of them",
        )
        parser.add_argument("--stale-minutes", type=int, default=10)
        parser.add_argument(
            "--as-of",
            type=datetime.fromisoformat,
            help="a database restored to this moment (ISO 8601 with offset; restore-drill, "
            "P1-T8c): only fingerprints stored by then count, and 'late' is measured then",
        )

    def _anchors(self, options):
        """(fingerprints or None when not checked, platform problems). A storage outage
        is a problem, but the local checks still run."""
        written = []
        if options["anchor"]:
            try:
                self.stdout.write(f"fingerprint written: {anchor.write()}")
            except (anchor.NotConfiguredError, BotoCoreError, ClientError) as exc:
                written = [
                    f"fingerprint not written: {exc} (run --anchor in the celery "
                    "container, which holds the writer's key)"
                ]
        if (
            options["anchor"]
            and not written
            and not os.environ.get("IHR_AUDIT_ANCHOR_READ_KEY")
        ):
            # The writer's container (Celery) holds no reader key, on purpose: the fingerprint
            # is written; the backend checks them all (P1-T8c)
            self.stdout.write(
                "outside fingerprints are checked where the reader's key is (the backend)"
            )
            return None, []
        try:
            fps = anchor.load_all(since=options["since"], until=options["as_of"])
        except anchor.NotConfiguredError as exc:
            message = f"outside fingerprints not checked: {exc}"
            if settings.DEBUG and not written:
                self.stdout.write(self.style.WARNING(message))
                return None, []
            return None, [*written, message]
        except (BotoCoreError, ClientError) as exc:
            return None, [*written, f"outside fingerprints unreadable: {exc}"]
        for note in fps.notes:
            self.stdout.write(self.style.WARNING(note))
        self.stdout.write(f"{fps.count} outside fingerprints read")
        return fps, (
            written + fps.problems + anchor.platform_problems(fps, now=options["as_of"])
        )

    def handle(self, *args, **options):
        as_of = options["as_of"]
        if as_of and (as_of.tzinfo is None or options["seal"] or options["anchor"]):
            msg = "--as-of needs a time zone offset, and reads only (no --seal or --anchor)"
            raise CommandError(msg)
        hospitals = Hospital.objects.exclude(schema_name="public").order_by("slug")
        if options["slug"]:
            hospitals = hospitals.filter(slug=options["slug"])
            if not hospitals:
                msg = f"no hospital with slug {options['slug']!r}"
                raise CommandError(msg)
        if options["seal"]:  # before a fingerprint is written and read
            for hospital in hospitals:
                with schema_context(hospital.schema_name):
                    seal_once(hospital.schema_name)
        anchors, platform = self._anchors(options)
        if anchors is not None and options["all"]:
            every = Hospital.objects.exclude(schema_name="public")
            platform += anchor.unknown_hospitals(
                anchors, {str(h.uuid): h.slug for h in every}
            )
        failed = bool(platform)
        for problem in platform:
            self.stdout.write(self.style.ERROR(problem))
        for hospital in hospitals:
            with schema_context(hospital.schema_name):
                problems = coverage(connection, hospital.schema_name)
                chain, summary = verify(
                    hospital.schema_name, options["stale_minutes"], as_of=as_of
                )
            problems += chain
            if anchors is not None:
                problems += anchor.check(str(hospital.uuid), anchors)
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
