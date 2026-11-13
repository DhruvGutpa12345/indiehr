"""Run release steps across the fleet of hospitals (P1-T18a; ihr_platform/fleet.py).

    manage.py ihr_fleet_run check roles packs --record --startup       (the start-up one-shot)
    manage.py ihr_fleet_run hold --operator <you> [--slug x ...]       (open a release window)
    manage.py ihr_fleet_run migrate check roles packs --operator <you> [--parallel 2]
    manage.py ihr_fleet_run --resume <run id> --operator <you>         (after a fix)
    manage.py ihr_fleet_run release --operator <you> [--slug x ...]    (clear holds)
    manage.py ihr_fleet_run window --start "DD-MM-YYYY HH:MM" --minutes 90 [--note ...] --operator <you>
    manage.py ihr_fleet_run window [--clear --operator <you>]          (show, or remove)

Steps run in the order hold, migrate, check, roles, packs, release, whatever order they are
named in. Which hospitals: every one with a schema that is provisioning, active, suspended or
offboarding (never archived), or ``--status`` / ``--slug``; roles take active and suspended
ones, packs active ones.

Exit status: a fault that is the same for every hospital (the public schema behind, live
hospitals not on this build's schema when the run does not migrate, a bad pack) exits 1.
With --startup a hospital that failed, was deferred or is held is recorded (its
TenantOperation of kind "upgrade", and the line "ihr-fleet: hospital=<slug> outcome=...",
which a log alert watches) and the command still exits 0; without it, any such hospital
exits 1. Records are written as "<operator>"; the start-up one-shot takes no --operator.
One run at a time (a second one refuses). On staging run it with `scripts/staging.sh fleet`,
whose lines reach the log store and the alert (never `staging.sh manage`, which logs nothing).
"""

import signal
import threading
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from ihr_refdata.management.commands.ihr_pack_apply import check_operator

from ihr_platform import fleet, release
from ihr_platform.models import Hospital, MaintenanceWindow

IST = timezone.get_fixed_timezone(330)
STATUSES = {s.value for s in Hospital.Status} - {Hospital.Status.ARCHIVED}


def _statuses(text):
    chosen = [s.strip() for s in text.split(",") if s.strip()]
    if bad := sorted(set(chosen) - STATUSES):
        msg = (
            f"--status takes {', '.join(sorted(STATUSES))} (never archived); not {bad}"
        )
        raise CommandError(msg)
    return chosen


class Command(BaseCommand):
    help = (
        "Release steps across the fleet: hold, migrate, check, roles, packs, release."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "steps", nargs="*", help="hold migrate check roles packs release, or window"
        )
        parser.add_argument("--slug", action="append", default=[], help="repeatable")
        parser.add_argument("--status", help="comma-separated hospital statuses")
        parser.add_argument("--parallel", type=int, default=2)
        parser.add_argument(
            "--timeout",
            type=int,
            default=fleet.DEFAULT_TIMEOUT,
            help="seconds one hospital's migration may take before it is stopped",
        )
        parser.add_argument(
            "--resume", help="continue this fleet run's unfinished hospitals"
        )
        parser.add_argument(
            "--record", action="store_true", help="check: record findings"
        )
        parser.add_argument(
            "--keep-hold",
            action="store_true",
            help="migrate: leave every hospital held at the end (release them later)",
        )
        parser.add_argument(
            "--startup",
            action="store_true",
            help="start-up mode: one hospital's problem is recorded and alerted on, and "
            "does not fail the command",
        )
        parser.add_argument("--operator", help="who runs it (not with --startup)")
        parser.add_argument("--start", help='window: "DD-MM-YYYY HH:MM" (IST)')
        parser.add_argument("--minutes", type=int, help="window: its length")
        parser.add_argument("--note", default="", help="window: a short public note")
        parser.add_argument("--clear", action="store_true", help="window: remove it")

    def handle(self, *args, steps, **options):
        operator = self._operator(options)
        if steps == ["window"]:
            return self._window(options, operator)
        if "window" in steps:
            raise CommandError("window is a command of its own")
        if not 1 <= options["parallel"] <= 8:  # noqa: PLR2004
            raise CommandError("--parallel is 1 to 8")
        if options["startup"] and (options["resume"] or "migrate" in steps):
            raise CommandError("--startup never migrates hospitals or resumes a run")
        if not operator:
            raise CommandError("--operator is required")
        runner = fleet.Fleet(
            steps,
            operator=operator,
            statuses=_statuses(options["status"]) if options["status"] else None,
            slugs=options["slug"],
            parallel=options["parallel"],
            timeout=options["timeout"],
            startup=options["startup"],
            record=options["record"],
            keep_hold=options["keep_hold"],
            resume=options["resume"],
            out=self.stdout.write,
            err=self.stderr.write,
        )
        previous = _stop_on_sigterm()
        try:
            run = runner.run()
        except fleet.FleetFault as exc:
            row = runner.run_row
            msg = f"ihr-fleet: stopped for every hospital: {exc}"
            if row is not None:
                msg += f" (fleet run {row.pk}"
                msg += (
                    ")"
                    if options["startup"]
                    else f"; after the fix: --resume {row.pk})"
                )
            raise CommandError(msg) from None
        finally:
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)
        if run is None or options["startup"]:
            return None
        if problems := sum(n for k, n in run.counts.items() if k != "done"):
            msg = f"{problems} hospital(s) not done (see above; --resume {run.pk})"
            raise CommandError(msg)
        return None

    def _operator(self, options):
        if options["startup"]:
            if options["operator"]:
                raise CommandError("--startup records itself as startup: no --operator")
            return "startup"
        if options["operator"]:
            return check_operator(options["operator"])
        return None

    def _window(self, options, operator):
        if (options["clear"] or options["start"]) and not operator:
            raise CommandError("--operator is required")
        if options["clear"]:
            self.stdout.write(f"removed {_remove_open_windows()} window(s)")
            return
        if options["start"]:
            try:
                start = datetime.strptime(options["start"], "%d-%m-%Y %H:%M").replace(
                    tzinfo=IST
                )
            except ValueError:
                raise CommandError('--start is "DD-MM-YYYY HH:MM" (IST)') from None
            minutes = options["minutes"]
            if not minutes or not 1 <= minutes <= 24 * 60:
                raise CommandError("--minutes is 1 to 1440")
            note = " ".join(options["note"].split())
            if len(note) > 200:  # noqa: PLR2004
                raise CommandError("--note is at most 200 characters")
            _remove_open_windows()  # one announced window at a time
            MaintenanceWindow.objects.create(
                start=start, minutes=minutes, note=note, created_by=operator
            )
        item = release.window()
        if item is None:
            self.stdout.write("no window announced")
            return
        local = item.start.astimezone(IST)
        self.stdout.write(
            f"window: {local:%d-%m-%Y %H:%M} IST for {item.minutes} min"
            + (f" ({item.note})" if item.note else "")
        )


def _raise_exit(signum, frame):
    raise SystemExit(128 + signum)


def _stop_on_sigterm():
    """``docker stop`` sends SIGTERM: make it unwind the run like Ctrl-C, so the run and its
    hospitals are closed (interrupted), not left "running" (C3). Returns the old handler."""
    if threading.current_thread() is not threading.main_thread():
        return None
    return signal.signal(signal.SIGTERM, _raise_exit)


def _remove_open_windows():
    """Remove every window not over yet; past ones stay as the record. Returns how many."""
    now, gone = timezone.now(), 0
    for item in MaintenanceWindow.objects.all():
        if item.end > now:
            item.delete()
            gone += 1
    return gone
