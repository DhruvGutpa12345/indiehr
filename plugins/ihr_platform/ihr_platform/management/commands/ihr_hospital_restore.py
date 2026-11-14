"""Restore one hospital from its dump (P1-T22b). Owner-run; scripts/staging.sh hospital-restore
drives it (it fetches the dump, runs the quarantine and restarts the app containers).

    ihr_hospital_restore --plan --slug S             what the quarantine needs (JSON)
    ihr_hospital_restore --slug S --package DIR --reason "..." --operator NAME
                         [--package-sha256 HEX] [--dry-run] [--drill] [--restart]
                         [--keep-restored-access "why"] [--free-bytes N]
    ihr_hospital_restore --resume OP [--restart] [--restarted] [--stop-after STEP]
                         [--operator NAME] [--unlogged]
    ihr_hospital_restore --abort OP --operator NAME
    ihr_hospital_restore --report-failure OP         the failure line of a step that failed in
                                                     the unlogged admin one-off (logged here)
    ihr_hospital_restore --status [--slug S]
    ihr_hospital_restore --parked | --expire-parked [--dry-run]

Exit status: 0 done; 1 refused or a step failed (the hospital stays held: --resume after the
fix, or --abort before reconcile); 3 (only with --restart) restart the app containers, then
--resume --restarted; 4 the Keycloak check needs the operator's credential (staging.sh runs
that step as admin).
Steps and refusals: ihr_platform/restore/engine.py.
"""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from ihr_platform import release
from ihr_platform.models import Hospital, TenantOperation
from ihr_platform.restore import engine

IST = timezone.get_fixed_timezone(330)


class Command(BaseCommand):
    help = "Restore one hospital from its dump, step by step (P1-T22b)"
    # The web app's system checks run below, except in the drill's one-off (settings.drill):
    # it serves nothing and holds no Keycloak address, client key or shared cache on purpose
    requires_system_checks = []

    def add_arguments(self, parser):
        what = parser.add_mutually_exclusive_group(required=True)
        what.add_argument("--slug")
        what.add_argument("--resume", metavar="OP")
        what.add_argument("--abort", metavar="OP")
        what.add_argument("--status", action="store_true")
        what.add_argument("--parked", action="store_true")
        what.add_argument("--expire-parked", action="store_true")
        what.add_argument("--report-failure", metavar="OP")
        parser.add_argument("--plan", action="store_true")
        parser.add_argument("--package", type=Path)
        parser.add_argument("--reason", default="")
        parser.add_argument("--operator", default="")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--drill", action="store_true")
        parser.add_argument("--keep-restored-access", default="", metavar="WHY")
        parser.add_argument("--free-bytes", type=int)
        parser.add_argument("--restarted", action="store_true")
        parser.add_argument("--restart", action="store_true")
        parser.add_argument("--package-sha256", default="", metavar="HEX")
        parser.add_argument("--unlogged", action="store_true")
        parser.add_argument("--stop-after", choices=engine.STEPS, default="")
        parser.add_argument("--status-slug", dest="status_slug")

    def _options(self, o):
        return engine.Options(
            slug=o["slug"] or "",
            package_dir=o["package"],
            reason=o["reason"],
            operator=o["operator"],
            drill=o["drill"],
            dry_run=o["dry_run"],
            keep_restored_access=o["keep_restored_access"],
            free_bytes=o["free_bytes"],
            restarted=o["restarted"],
            restart=o["restart"],
            package_sha256=o["package_sha256"],
            unlogged=o["unlogged"],
            stop_after=o["stop_after"],
            out=lambda line: self.stdout.write(line),
            err=lambda line: self.stderr.write(line),
        )

    def handle(self, *args, **o):  # noqa: PLR0912 (one flat dispatch)
        if not getattr(settings, "IHR_RESTORE_DRILL", False):
            self.check(display_num_errors=False)
        connection.set_schema_to_public()
        if (
            o["keep_restored_access"]
            and len(o["keep_restored_access"].strip()) < engine.MIN_REASON
        ):
            msg = "--keep-restored-access needs its reason (at least 10 characters)"
            raise CommandError(msg)
        if o["parked"] or o["expire_parked"]:
            return self._parked(o)
        if o["status"]:
            return self._status(o["status_slug"])
        if o["plan"]:
            return self._plan(o["slug"])
        run = engine.Restore(self._options(o))
        if o["report_failure"]:
            try:
                lines = run.report_failures(o["report_failure"])
            except engine.Refused as exc:
                raise CommandError(str(exc)) from None
            self.stdout.write(f"{lines} failure line(s) reported")
            return None
        try:
            if o["abort"]:
                if not engine.OPERATOR_RE.match(o["operator"] or ""):
                    msg = "--abort needs --operator"
                    raise CommandError(msg)
                code = run.abort(o["abort"])
            elif o["resume"]:
                code = run.resume(o["resume"])
            else:
                result = run.start()
                if o["dry_run"]:
                    self.stdout.write(
                        json.dumps(result, indent=1, sort_keys=True, default=str)
                    )
                    self.stdout.write("dry run: nothing changed")
                    return None
                code = result
        except engine.Refused as exc:
            msg = f"refused: {exc}"
            raise CommandError(msg) from None
        except engine.StepFailed as exc:
            msg = (
                f"{exc}\nThe hospital stays held: fix it and --resume {run.op.pk if run.op else ''}, "
                "or --abort (before reconcile)"
            )
            raise CommandError(msg) from None
        if code:
            raise SystemExit(code)
        self.stdout.write(self.style.SUCCESS(f"restore {run.op.pk}: done"))
        return None

    def _plan(self, slug):
        hospital = Hospital.objects.filter(slug=slug).first()
        if hospital is None:
            msg = f"no hospital {slug!r}"
            raise CommandError(msg)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT extname FROM pg_extension WHERE extname <> 'plpgsql' ORDER BY 1"
            )
            extensions = [r[0] for r in cursor.fetchall()]
        open_op = (
            TenantOperation.objects.filter(
                kind=TenantOperation.Kind.RESTORE,
                slug=slug,
                status__in=["pending", "running", "failed"],
            )
            .values_list("pk", flat=True)
            .first()
        )
        self.stdout.write(
            json.dumps(
                {
                    "slug": hospital.slug,
                    "uuid": str(hospital.uuid),
                    "schema": hospital.schema_name,
                    "status": hospital.status,
                    "extensions": extensions,
                    "open_restore": str(open_op) if open_op else "",
                    "held_for_restore": release.restore_holder(hospital),
                    "drill_database": engine.is_drill_database(),
                },
                sort_keys=True,
            )
        )

    def _status(self, slug):
        ops = TenantOperation.objects.filter(kind=TenantOperation.Kind.RESTORE)
        if slug:
            ops = ops.filter(slug=slug)
        for op in ops.order_by("-created_at")[:20]:
            steps = " ".join(f"{s['name']}={s['status']}" for s in op.steps)
            when = timezone.localtime(op.created_at, IST)
            self.stdout.write(
                f"{op.pk} {op.slug} {op.status} {when:%d-%m-%Y %H:%M} IST {steps}"
            )

    def _parked(self, o):
        if o["expire_parked"]:
            try:
                done = engine.expire_parked(dry_run=o["dry_run"])
            except engine.Refused as exc:
                raise CommandError(str(exc)) from None
            for name, dropped in done:
                self.stdout.write(
                    f"{name}: {'dropped' if dropped else 'would be dropped'}"
                )
            self.stdout.write(
                f"{len(done)} parked schema(s) past {engine.PARKED_DAYS} days"
            )
            return
        for name, schema, at, age in engine.parked_schemas():
            when = f"{at:%d-%m-%Y %H:%M} UTC, {age:.1f} days" if at else "unknown age"
            self.stdout.write(f"{name} (of {schema or '?'}; parked {when})")
