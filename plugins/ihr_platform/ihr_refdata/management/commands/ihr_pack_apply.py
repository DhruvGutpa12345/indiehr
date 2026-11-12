"""Apply the reference packs to live hospitals (P1-T15a; replaces CARE's sync_valueset).

    manage.py ihr_pack_apply --all --startup               (the start-up one-shot)
    manage.py ihr_pack_apply --all --operator <name> [--check]
    manage.py ihr_pack_apply --slug alpha --operator <name> [--check]

Only live (ACTIVE) hospitals: one being provisioned gets its packs from the provisioning run
(value_sets and activate steps). Each hospital is applied in its own transaction under its
provisioning lock; a busy hospital is deferred (outcome "deferred"), never waited for. With
--startup a busy hospital is tried again after 5, 15 and 30 seconds (every lock holder on a
live hospital is short-lived) before it is given up.

Exit status: a fault in the pack files or the fleet's release record (the same for every
hospital) always exits 1. With --startup, a hospital that failed, or was still busy after the
last try, is recorded (a PackApply row and the line "ihr-packs: hospital=<slug>
outcome=failed", which a log alert watches) and the command still exits 0, so one hospital
never stops the fleet's start. Without --startup any such hospital exits 1. --check writes
nothing and reports what would change.

Recorded as "packs:<operator>" (PackApply.actor, PackRelease.loaded_by and the audit actor),
or "packs:startup" for the start-up one-shot, which takes no --operator.
"""

import re
from time import sleep

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_tenants.utils import get_public_schema_name, schema_context
from ihr_audit import context as audit
from ihr_platform.models import Hospital
from ihr_platform.provisioning import runner

from ihr_refdata import engine
from ihr_refdata.models import PackApply

RETRY_DELAYS = (5, 15, 30)  # seconds; --startup only (S4)
OPERATOR = re.compile(r"[A-Za-z0-9._@-]{1,64}")


def log_line(slug, outcome):
    """The fixed line the log alert matches (deploy/logs/alerts/packs.yml)."""
    return f"ihr-packs: hospital={slug} outcome={outcome}"


RESERVED_OPERATORS = {"startup", "check"}


def check_operator(operator):
    """Who runs it: a plain name (no ':' prefix that could pose as another source, S6)."""
    if not operator or not OPERATOR.fullmatch(operator):
        msg = "--operator must be 1-64 letters, digits or . _ @ -"
        raise CommandError(msg)
    if operator.lower() in RESERVED_OPERATORS:
        msg = f"--operator {operator!r} is reserved for the start-up and check runs"
        raise CommandError(msg)
    return operator


class Command(BaseCommand):
    help = "Apply the newest reference packs to live hospitals."

    def add_arguments(self, parser):
        which = parser.add_mutually_exclusive_group(required=True)
        which.add_argument("--slug", help="One live hospital")
        which.add_argument("--all", action="store_true", help="Every live hospital")
        parser.add_argument(
            "--startup",
            action="store_true",
            help="Start-up mode (with --all): a busy hospital is tried again 3 times; a "
            "hospital's failure is recorded and alerted on, and does not fail the command",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Report what would change; write nothing",
        )
        parser.add_argument(
            "--operator",
            help="Who runs it (recorded as packs:<operator> with every change; 1-64 "
            "letters, digits or . _ @ -; not with --startup)",
        )

    def handle(self, *args, slug, all, startup, check, operator, **options):  # noqa: A002
        if startup and (not all or check):
            raise CommandError("--startup goes with --all, and never with --check")
        if startup and operator:
            raise CommandError(
                "--startup records itself as packs:startup: no --operator"
            )
        if operator:
            check_operator(operator)
        elif not (startup or check):
            raise CommandError("--operator is required")
        else:
            operator = "startup" if startup else "check"
        actor = f"packs:{operator}"
        hospitals = Hospital.objects.exclude(
            schema_name=get_public_schema_name()
        ).order_by("slug")
        if slug:
            hospital = hospitals.filter(slug=slug).first()
            if hospital is None:
                msg = f"No hospital with slug {slug!r}"
                raise CommandError(msg)
            if hospital.status != Hospital.Status.ACTIVE:
                msg = f"{slug} is {hospital.status}: only live hospitals are changed here."
                raise CommandError(msg)
        hospitals = list(hospitals.filter(status=Hospital.Status.ACTIVE))
        if slug:
            hospitals = [h for h in hospitals if h.slug == slug]

        via = "startup" if startup else "operator"
        with audit.job({"job": "packs", "via": via}, actor):
            try:
                releases = engine.prepare(actor, record=not check)
            except engine.PackError as exc:
                msg = f"ihr-packs: refused for every hospital: {exc}"
                raise CommandError(msg) from None
            outcomes = [
                (
                    hospital,
                    self._one(hospital, releases, actor, check=check, retry=startup),
                )
                for hospital in hospitals
            ]
            problems = sum(outcome in ("failed", "deferred") for _, outcome in outcomes)
            busy = [h for h, outcome in outcomes if outcome == "busy"]  # --startup only
            problems += len(self._retry(busy, releases, actor))
        if problems and not startup:
            msg = f"{problems} hospital(s) not done (see above)"
            raise CommandError(msg)

    def _retry(self, busy, releases, actor):
        """Try busy hospitals again after each delay; the ones still busy are given up:
        recorded and alerted on like a failure. Returns those."""
        for delay in RETRY_DELAYS:
            if not busy:
                break
            for hospital in busy:
                self.stderr.write(
                    f"ihr-packs: hospital={hospital.slug} busy: trying again in {delay} s"
                )
            sleep(delay)
            busy = [
                h
                for h in busy
                if self._one(h, releases, actor, check=False, retry=True) == "busy"
            ]
        for hospital in busy:
            self._record(
                hospital,
                releases.values(),
                PackApply.Outcome.DEFERRED,
                f"another job held this hospital through {len(RETRY_DELAYS) + 1} tries; "
                "run ihr_pack_apply --slug for it",
                actor,
                check=False,
            )
            self.stderr.write(log_line(hospital.slug, "failed"))
        return busy

    def _one(self, hospital, releases, actor, *, check, retry=False):
        """ "done", "failed", "deferred" (recorded) or "busy" (with ``retry``: not recorded,
        the caller tries again)."""
        try:
            if check:
                results = engine.apply_packs(
                    hospital, operator=actor, releases=releases, dry_run=True
                )
            else:
                with runner.exclusive(hospital.slug):
                    results = engine.apply_packs(
                        hospital, operator=actor, releases=releases
                    )
        except runner.ProvisioningError:
            if retry:
                return "busy"
            self._record(
                hospital,
                releases.values(),
                PackApply.Outcome.DEFERRED,
                "another job held this hospital; the next run applies the packs",
                actor,
                check=check,
            )
            self.stderr.write(log_line(hospital.slug, "deferred"))
            return "deferred"
        except Exception as exc:
            failed = [exc.release] if isinstance(exc, engine.ApplyError) else None
            error = str(exc) if failed else engine.safe_error(exc)
            self._record(
                hospital,
                failed or releases.values(),
                PackApply.Outcome.FAILED,
                error,
                actor,
                check=check,
            )
            self.stderr.write(log_line(hospital.slug, "failed"))
            return "failed"
        for result in results:
            counts = ", ".join(f"{n} {what}" for what, n in result.counts.items())
            line = f"{hospital.slug}: {result.pack} v{result.version} {result.result}"
            if check and result.result == "applied":
                line = f"{hospital.slug}: {result.pack} v{result.version} would change"
            self.stdout.write(f"{line}{f' ({counts})' if counts else ''}")
            if result.note:
                self.stderr.write(
                    f"ihr-packs: hospital={hospital.slug} pack={result.pack} {result.note}"
                )
        return "done"

    def _record(self, hospital, releases, outcome, error, actor, *, check):
        """One PackApply row per pack, in the hospital's own schema (safe error text)."""
        if check:
            return
        try:
            with schema_context(hospital.schema_name), transaction.atomic():
                for release in releases:
                    PackApply.objects.create(
                        pack=release.pack,
                        version=release.version,
                        sha256=release.sha256,
                        care_commit=release.care_commit,
                        outcome=outcome,
                        actor=actor[:150],
                        error=error[:400],
                    )
        except Exception as exc:
            self.stderr.write(
                f"ihr-packs: hospital={hospital.slug} not recorded: {type(exc).__name__}"
            )
