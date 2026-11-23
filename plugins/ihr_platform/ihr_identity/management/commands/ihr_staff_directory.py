"""Check or repair the staff directory behind the hospital switcher (P1-T31b; docs/reviews/
p1-t31/design.md §3.3, attack.md "Orchestrator decision" item 4 and D2, S8, S9).

    manage.py ihr_staff_directory --check  [--slug S]
    manage.py ihr_staff_directory --repair [--slug S]

``--check`` (read-only; any role that reads the hospitals): for every hospital in
``fleet.MIGRATE_STATUSES`` the rows of ``public.ihr_platform_staffdirectoryentry`` against the
links its schema lists, and its trigger function and triggers against this build's
(``directory.problems``); the rows of any other hospital (archived, ``public``) are findings too,
since the switcher never lists them. Each hospital with a finding prints counts and writes the
line ``ihr-directory: hospital=<slug> outcome=drift`` on stderr; exit 1 if any. No person's sub
is ever printed.

``--repair`` (the database owner: staging ``scripts/staging.sh manage --operator <you> --reason
"<ticket>" ihr_staff_directory --repair``): per hospital, under its provisioning lock
(``runner.exclusive``, as every job that changes a hospital), ``ensure_directory`` and
``rebuild``; the other hospitals' rows deleted; then the check again (exit 1 if anything is
left). A hospital held by a restore, or held for a release or on another build's schema
(``release.schema_ready``, S6), is skipped and not checked: the restore rebuilds it in its
reconcile step (or its abort), a release's migrate installs this build's SQL. Each hospital
catches its own failure (a busy lock, ``links kept changing``): it prints the safe error, writes
``ihr-directory: hospital=<slug> outcome=failed`` on stderr and the rest go on; exit 1 at the end.
``--check`` skips a hospital held by a restore the same way (its rows are mid-restore).

The weekly ``--check`` is the backstop for two recomputes of one person racing (attack S9); it
is a row of ``scripts/staging.sh due`` (scripts/ops_due.py).
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from ihr_platform import fleet, release
from ihr_platform.models import Hospital
from ihr_platform.provisioning import runner

from ihr_identity import directory


class Command(BaseCommand):
    help = "Check (--check) or repair (--repair) the staff directory (P1-T31)."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--check", action="store_true")
        mode.add_argument("--repair", action="store_true")
        parser.add_argument("--slug", help="one hospital (default: every hospital)")

    def handle(self, *args, check, repair, slug, **options):
        connection.set_schema_to_public()
        hospitals = Hospital.objects.order_by("slug")
        if slug:
            hospitals = hospitals.filter(slug=slug)
            if not hospitals.exists():
                msg = f"No hospital {slug!r}"
                raise CommandError(msg)
        found = 0
        for hospital in hospitals:
            listed = (
                hospital.status in fleet.MIGRATE_STATUSES
                and hospital.schema_name != "public"
            )
            if repair:
                outcome = self._repair(hospital, listed)
                if outcome == "failed":
                    found += 1
                if outcome != "done":
                    continue  # skipped (nothing to compare yet) or failed (already told)
            found += self._check(hospital, listed)
        if found:
            msg = f"{found} hospital(s) with directory drift" + (
                "" if repair else ": ihr_staff_directory --repair puts it right"
            )
            raise CommandError(msg, returncode=1)
        self.stdout.write(
            "Staff directory: every hospital's rows and triggers are current."
        )

    def _skipped(self, hospital):
        """Why ``--repair`` leaves this hospital alone now ("" if it may be written to)."""
        if release.restore_holder(hospital):
            return "held by a restore, skipped (its reconcile rebuilds it)"
        if not release.schema_ready(hospital):
            return (
                "held for a release or not on this build's schema, skipped "
                "(ihr_fleet_run check, then repair)"
            )
        return ""

    def _repair(self, hospital, listed):
        """ "done", "skipped" or "failed" (told, with its ``failed`` line)."""
        if not listed:
            removed = directory.clear(hospital)
            if removed:
                self.stdout.write(f"{hospital.slug}: {removed} row(s) removed")
            return "done"
        if why := self._skipped(hospital):
            self.stdout.write(f"{hospital.slug}: {why}")
            return "skipped"
        try:
            with runner.exclusive(hospital.slug):
                if why := self._skipped(hospital):  # a hold set since the first look
                    self.stdout.write(f"{hospital.slug}: {why}")
                    return "skipped"
                changed = directory.ensure_directory(connection, hospital.schema_name)
                report = directory.rebuild(hospital)
        except Exception as exc:  # each hospital catches its own failure
            self.stdout.write(
                f"{hospital.slug}: not repaired ({release.safe_error(exc)})"
            )
            self.stderr.write(directory.log_line(hospital.slug, "failed"))
            return "failed"
        if changed or any(report.values()):
            self.stdout.write(
                f"{hospital.slug}: {report['added']} row(s) added, {report['removed']} "
                f"removed{', function and triggers put back' if changed else ''}"
            )
        return "done"

    def _check(self, hospital, listed):
        """1 if this hospital has a finding (printed, and its drift line), else 0."""
        lines = []
        if listed and release.restore_holder(hospital):
            self.stdout.write(
                f"{hospital.slug}: held by a restore, skipped (its reconcile rebuilds it)"
            )
            return 0
        with connection.cursor() as cursor:
            if not listed:
                if rows := len(directory.row_subs(cursor, hospital.pk)):
                    lines.append(
                        f"{hospital.slug}: {rows} row(s) of a hospital the switcher never "
                        f"lists ({hospital.status})"
                    )
            else:
                missing, extra = directory.differences(cursor, hospital)
                if missing or extra:
                    lines.append(
                        f"{hospital.slug}: {len(missing)} row(s) missing, {len(extra)} extra"
                    )
                lines += [
                    f"{hospital.slug}: {problem}"
                    for problem in directory.problems(cursor, hospital.schema_name)
                ]
        for line in lines:
            self.stdout.write(line)
        if lines:
            self.stderr.write(directory.log_line(hospital.slug, "drift"))
        return 1 if lines else 0
