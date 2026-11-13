"""The fleet runner behind ``ihr_fleet_run`` (P1-T18a; docs/reviews/p1-t18/design.md).

Steps, always run in this order whatever order they are named in:

- ``hold``: hold the selected hospitals (one transaction); the gate answers 503 for them.
- ``migrate``: each hospital whose schema is not this build's is held first ("mark, then
  migrate"), then migrated in a forked child of its own (at most ``parallel`` at a time, also
  for 1; one task per child; connections closed before forking). The child takes the
  hospital's provisioning lock, runs ``migrate_schemas`` with ``lock_timeout`` 10 s and
  retries lock errors with back-off. It tells the parent its database session first: the
  parent kills a child that passes the wall-clock limit *and ends that session*, so the
  migration's statement and its locks never outlive the limit (C6/S9). A child that dies
  without an answer is tried once more in a ``manage.py`` subprocess. Only the parent checks
  the schema and records it (release.py).
- ``check``: verify every selected schema against this build; ``--record`` writes the
  fingerprint of each schema found current (never "" over a recorded one: the previous build
  may still be serving it, S4). Without ``migrate`` in the same run, a live (active or
  suspended) hospital that is not this build's is a fault for the whole fleet: the start-up
  one-shot then refuses to start, pointing to the release (design item 9). The exception is a
  hospital this build's release already tried and left held (C2): it stays gated and is
  reported, and the others start.
- ``roles``: one read-only ``sync_permissions_roles --check`` pass over every selected active
  or suspended hospital, then the sync for those that passed. A refusing hospital is held
  (reason "roles") and reported (design item 6); a later run whose roles pass releases that
  hold, never a window's (C4/S2).
- ``packs``: the reference packs, exactly as ``ihr_pack_apply`` applies them (live hospitals
  only; its records and its ``ihr-packs:`` lines).
- ``release``: clear the hold of the selected hospitals.

Before any step, fleet-wide faults are looked for (the public schema behind, a bad pack), so
such a fault stops the run before a hospital is held or migrated (C1/S5). One run at a time:
a fleet-wide advisory lock is held for the whole run (C8); a run killed half-way is closed as
interrupted when it is resumed (C3).

A run that migrates clears, at its end, the hold of every hospital that finished each step
(unless ``keep_hold``); a hospital that failed, was deferred or was refused stays held. Each
live hospital that is not served after the run (and each hospital a step of the run failed
for) is written as one fixed line, ``ihr-fleet: hospital=<slug> outcome=failed|deferred|held``,
which a log alert watches (deploy/logs/alerts/fleet.yml); a provisioning or offboarding
hospital is not served by design and gets a plain note instead (C7/S8). Records: one FleetRun
per run, one TenantOperation of kind ``upgrade`` per hospital, with safe errors only
(release.safe_error).
"""

import contextlib
import hashlib
import multiprocessing
import multiprocessing.connection
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.db.backends.signals import connection_created
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Q
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context, schema_exists

from ihr_platform import release
from ihr_platform.models import FleetRun, Hospital, TenantOperation
from ihr_platform.provisioning import runner

STEPS = ("hold", "migrate", "check", "roles", "packs", "release")
S = Hospital.Status
MIGRATE_STATUSES = (S.PROVISIONING, S.ACTIVE, S.SUSPENDED, S.OFFBOARDING)
ROLES_STATUSES = (S.ACTIVE, S.SUSPENDED)
PACKS_STATUSES = (S.ACTIVE,)
LIVE_STATUSES = (
    S.ACTIVE,
    S.SUSPENDED,
)  # "check" without "migrate": a mismatch is a fault
RETRY_DELAYS = (
    5,
    15,
    30,
)  # a hospital whose lock another job holds is tried again after
LOCK_TIMEOUT = "10s"
LOCK_PAUSES = (
    2,
    5,
    10,
)  # between tries after a lock time-out or deadlock, in one child
LOCK_STATES = {"55P03", "40P01"}  # lock_not_available, deadlock_detected
# A migrating session checks every few seconds that its client is still there, so one whose
# process died stops at once instead of finishing its statement (S9; Postgres 14 and later)
CONNECTION_CHECK = "5s"
DEFAULT_TIMEOUT = 1800  # seconds per hospital and step (the wall-clock limit)
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{2,30}$")
# The fleet-wide run lock (C8): a key no hospital's lock can have (":" is never in a slug)
FLEET_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"ihr:fleet-run").digest()[:8], "big", signed=True
)
# Tests: run the per-hospital work in this process (design item 10). Never set otherwise:
# outside a test transaction the work always runs in forked children, with the limit (C6)
INLINE = False


class FleetFault(Exception):  # noqa: N818 (the name the command and tests use)
    """The same for every hospital (a public schema behind, live hospitals not migrated, a
    bad pack): the run stops and the command exits non-zero. ``keeps_holds``: False when the
    hospitals' own steps are not in question (a bad pack), so the run still releases the ones
    that finished them (C1)."""

    def __init__(self, message, keeps_holds=True):
        super().__init__(message)
        self.keeps_holds = keeps_holds


def _not_done():
    """Operations whose outcome is not "done", including those that never wrote one (C3)."""
    return ~Q(result__outcome="done") | Q(result__outcome__isnull=True)


def log_line(slug, outcome):
    """The fixed line the log alert matches (deploy/logs/alerts/fleet.yml)."""
    return f"ihr-fleet: hospital={slug} outcome={outcome}"


# --- one hospital's migration (in a forked child) ----------------------------------------


class _Sink:
    def write(self, text):
        return len(text)

    def flush(self):
        pass


class _Lines:
    """A stream that hands each written line to ``emit`` (for a borrowed command's output)."""

    def __init__(self, emit):
        self.emit = emit

    def write(self, text):
        for line in text.splitlines():
            self.emit(line)
        return len(text)

    def flush(self):
        pass


class _Applying:
    """The migration being applied (design item 8)."""

    def __init__(self):
        self.current = None


@contextlib.contextmanager
def _tracking(tracker):
    """Name each migration as Django applies it, from ``MigrationExecutor.apply_migration``
    itself (C5): django-tenants' per-line message is never sent when stdout is not a
    terminal, which it never is here. Cleared when the migration finishes, so a failure after
    the last one (a post_migrate handler) is not pinned on it."""
    original = MigrationExecutor.apply_migration

    def apply_migration(executor, state, migration, *args, **kwargs):
        tracker.current = f"{migration.app_label}.{migration.name}"
        done = original(executor, state, migration, *args, **kwargs)
        tracker.current = None
        return done

    MigrationExecutor.apply_migration = apply_migration
    try:
        yield
    finally:
        MigrationExecutor.apply_migration = original


@contextlib.contextmanager
def bounded_locks():
    """Every connection opened meanwhile waits at most LOCK_TIMEOUT for a lock: a migration
    never queues behind a long request holding a lock, which would stall every query behind
    it (design item 7). It also checks for a vanished client every CONNECTION_CHECK (S9)."""

    def apply(sender, connection, **kwargs):
        with connection.cursor() as cursor:
            cursor.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
            cursor.execute(
                f"SET client_connection_check_interval = '{CONNECTION_CHECK}'"
            )

    uid = "ihr-fleet-lock-timeout"
    connection_created.connect(apply, weak=False, dispatch_uid=uid)
    try:
        if connection.connection is not None:
            apply(None, connection)
        yield
    finally:
        connection_created.disconnect(dispatch_uid=uid)
        if connection.connection is not None:
            with contextlib.suppress(Exception), connection.cursor() as cursor:
                cursor.execute("RESET lock_timeout")
                cursor.execute("RESET client_connection_check_interval")


# In a forked child: hands the parent the database session that runs the migration (C6)
_REPORT = {"to": None}


def _report_backend():
    """Tell the parent which server session migrates (its pid and start time), so that it can
    end that session if it has to stop this child."""
    if _REPORT["to"] is None:
        return
    connection.ensure_connection()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pid, backend_start FROM pg_stat_activity WHERE pid = pg_backend_pid()"
        )
        _REPORT["to"](cursor.fetchone())


def migrate_one(schema_name, slug):
    """Migrate one hospital's schema, under its provisioning lock. Returns a plain dict:
    outcome done | failed | deferred (lock time-outs) | busy (another job holds it)."""
    began, tracker = time.monotonic(), _Applying()
    result = {"attempts": 0}
    try:
        with runner.exclusive(slug):
            for pause in (*LOCK_PAUSES, None):
                result["attempts"] += 1
                tracker.current = None
                try:
                    with (
                        bounded_locks(),
                        _tracking(tracker),
                        contextlib.redirect_stdout(_Sink()),
                    ):
                        _report_backend()
                        call_command(
                            "migrate_schemas",
                            tenant=True,
                            schema_name=schema_name,
                            interactive=False,
                            verbosity=1,
                        )
                    result["outcome"] = "done"
                    break
                except Exception as exc:  # each hospital catches its own failure
                    locked = release.sqlstate(exc) in LOCK_STATES
                    if locked and pause is not None:
                        connection.close()
                        time.sleep(pause)
                        continue
                    result["outcome"] = "deferred" if locked else "failed"
                    result["error"] = release.safe_error(exc, tracker.current)
                    break
    except runner.ProvisioningError:
        result["outcome"] = "busy"
    finally:
        connection.set_schema_to_public()
    result["seconds"] = round(time.monotonic() - began, 1)
    return result


def needs_migrate(hospital):
    """True unless the schema is already this build's (then it is only recorded)."""
    return not release.schema_state(hospital.schema_name).current


def settle(hospital):
    """After a migration: check the schema and record what was found (parent only)."""
    return release.record(hospital)


# --- the pool -----------------------------------------------------------------------------


def _child(send, work, args):
    # A child stopped by the parent dies at once (the parent then ends its database session)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    release.RECORD_ON_SIGNAL["on"] = False  # the parent checks and records (C11)
    _REPORT["to"] = lambda session: send.send(("backend", session))
    try:
        result = work(*args)
    except BaseException as exc:  # reported to the parent, never raised
        result = {"outcome": "failed", "error": release.safe_error(exc)}
    try:
        send.send(("result", result))
    finally:
        send.close()
        connections.close_all()


def _stop(process):
    process.terminate()
    process.join(5)
    if process.is_alive():
        process.kill()
        process.join()


def end_backend(session, wait=30):
    """End a stopped child's database session (pid and start time, so a reused pid is never
    hit) and wait until it is gone: a killed client does not stop the statement it sent, nor
    free that statement's locks (C6/S9). True when the session is gone."""
    if not session:
        return True
    pid, started = session
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE pid = %s AND backend_start = %s",
            [pid, started],
        )
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            cursor.execute(
                "SELECT 1 FROM pg_stat_activity WHERE pid = %s AND backend_start = %s",
                [pid, started],
            )
            if cursor.fetchone() is None:
                return True
            time.sleep(0.2)
    return False


def _take(child):
    """Every message the child has sent: its database session, then its result (None while
    it has not answered). A child that died without answering gives outcome "crashed"."""
    while child["receive"].poll():
        try:
            kind, value = child["receive"].recv()
        except EOFError:  # it died without answering
            child["process"].join(5)
            return {"outcome": "crashed", "exit_code": child["process"].exitcode}
        if kind == "backend":
            child["backend"] = value
        else:
            return value
    return None


def run_pool(work, items, parallel, timeout):
    """Run ``work(*args)`` for each ``(key, args)`` of ``items``: in forked children, one
    task per child, at most ``parallel`` at a time, each killed after ``timeout`` seconds
    (its database session ended with it). In-process only inside a transaction or with
    INLINE (tests). Returns {key: result}; a child that died without answering gives outcome
    "crashed"."""
    results = {}
    if INLINE or connection.in_atomic_block:
        for key, args in items:
            try:
                results[key] = work(*args)
            except Exception as exc:  # each item catches its own failure
                results[key] = {"outcome": "failed", "error": release.safe_error(exc)}
        return results
    context = multiprocessing.get_context(
        "fork"
    )  # explicitly: 3.14 changes the default
    pending, running = list(items), {}
    try:
        while pending or running:
            while pending and len(running) < max(parallel, 1):
                key, args = pending.pop(0)
                connections.close_all()  # never share a database socket with a child
                receive, send = context.Pipe(duplex=False)
                process = context.Process(target=_child, args=(send, work, args))
                process.start()
                send.close()
                running[key] = {
                    "process": process,
                    "receive": receive,
                    "started": time.monotonic(),
                    "backend": None,
                }
            waits = [c["receive"] for c in running.values()]
            waits += [c["process"].sentinel for c in running.values()]
            multiprocessing.connection.wait(waits, timeout=1)
            for key, child in list(running.items()):
                process = child["process"]
                result = _take(child)
                if result is None and not process.is_alive():
                    result = _take(child) or {
                        "outcome": "crashed",
                        "exit_code": process.exitcode,
                    }
                killed = False
                if result is None:
                    if time.monotonic() - child["started"] <= timeout:
                        continue
                    _stop(process)
                    killed = True
                    result = {
                        "outcome": "deferred",
                        "error": f"stopped after the {timeout} s limit",
                        "seconds": round(time.monotonic() - child["started"], 1),
                    }
                if killed or result.get("outcome") == "crashed":
                    # Before the outcome is recorded or the fallback starts (C6)
                    result["backend_stopped"] = end_backend(child["backend"])
                process.join(5)
                child["receive"].close()
                del running[key]
                results[key] = result
    finally:
        for child in running.values():  # interrupted: nothing outlives the run (C3, C6)
            _stop(child["process"])
            with contextlib.suppress(Exception):
                end_backend(child["backend"], wait=5)
    return results


def _database_url():
    """The child's DATABASE_URL: the one this process was started with, whole (TLS options,
    socket hosts), unless the test runner has renamed the database; then only its name is
    swapped (C9)."""
    db = connection.settings_dict
    original = os.environ.get("DATABASE_URL", "")
    if original:
        parts = urlsplit(original)
        if unquote(parts.path.lstrip("/")) == db["NAME"]:
            return original
        return parts._replace(path="/" + quote(db["NAME"], safe="")).geturl()
    user = quote(db.get("USER") or "", safe="")
    password = quote(db.get("PASSWORD") or "", safe="")
    auth = f"{user}:{password}@" if user else ""
    host = quote(db.get("HOST") or "", safe="")
    port = f":{db['PORT']}" if db.get("PORT") else ""
    return f"postgres://{auth}{host}{port}/{quote(db['NAME'], safe='')}"


def migrate_in_subprocess(schema_name, timeout):
    """The fallback for a child that crashed (design item 7): ``manage.py migrate_schemas``
    in a process of its own, against this process's database. Only its exit code is kept
    (never its output, which can hold row values: O10)."""
    manage = str(
        Path(settings.BASE_DIR) / "manage.py"
    )  # never from the working directory
    env = dict(os.environ, DATABASE_URL=_database_url())
    env["PGOPTIONS"] = (
        f"-c lock_timeout={LOCK_TIMEOUT} "
        f"-c client_connection_check_interval={CONNECTION_CHECK}"
    )
    command = [sys.executable, manage, "migrate_schemas", "--tenant"]
    command += [f"--schema={schema_name}", "--noinput", "-v", "0"]
    began = time.monotonic()
    try:
        done = subprocess.run(  # noqa: S603 (our own interpreter and manage.py)
            command,
            env=env,
            timeout=timeout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "outcome": "deferred",
            "error": f"subprocess stopped after the {timeout} s limit",
        }
    seconds = round(time.monotonic() - began, 1)
    if done.returncode == 0:
        return {"outcome": "done", "seconds": seconds, "via": "subprocess"}
    return {
        "outcome": "failed",
        "seconds": seconds,
        "error": f"the migration process exited with code {done.returncode}",
        "via": "subprocess",
    }


# --- roles --------------------------------------------------------------------------------


def roles_check(hospital):
    """The guarded sync's read-only check; RoleSyncRefused names what it would refuse."""
    with schema_context(hospital.schema_name):
        call_command("sync_permissions_roles", check=True, verbosity=0)


def roles_sync(hospital):
    with schema_context(hospital.schema_name):
        call_command("sync_permissions_roles", verbosity=0)


# --- the run ------------------------------------------------------------------------------


@contextlib.contextmanager
def fleet_lock():
    """One fleet run at a time (C8), held on a connection of its own for the whole run (as
    runner.exclusive). A second run refuses at once, naming the run that holds it."""
    holder = connections.create_connection("default")
    try:
        with holder.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [FLEET_LOCK_KEY])
            taken = cursor.fetchone()[0]
        if not taken:
            running = (
                FleetRun.objects.filter(status=FleetRun.Status.RUNNING)
                .order_by("-started_at")
                .values_list("pk", flat=True)
                .first()
            )
            msg = (
                f"another ihr_fleet_run is running (fleet run {running or 'unknown'}): "
                "wait for it to finish"
            )
            raise CommandError(msg)
        yield
    finally:
        holder.close()  # ends that session, which releases the lock


@dataclass
class _Entry:
    hospital: Hospital
    op: TenantOperation
    steps: dict = field(default_factory=dict)  # step -> record
    stopped: bool = False  # an earlier step did not finish: later ones leave it alone

    def outcome(self, ignore=()):
        found = {r["status"] for n, r in self.steps.items() if n not in ignore}
        for worst in ("failed", "held", "deferred"):
            if worst in found:
                return worst
        return "done"

    def finished(self, steps, ignore=("packs", "release")):
        """Every step of the run (but ``ignore``) recorded for it, and none went wrong."""
        complete = all(s in self.steps for s in steps if s not in ignore)
        return complete and self.outcome(ignore=ignore) == "done"


class Fleet:
    def __init__(
        self,
        steps,
        *,
        operator,
        statuses=None,
        slugs=None,
        parallel=2,
        timeout=DEFAULT_TIMEOUT,
        startup=False,
        record=False,
        keep_hold=False,
        resume=None,
        out=None,
        err=None,
    ):
        unknown = set(steps) - set(STEPS)
        if unknown or not (steps or resume):
            msg = f"steps are {', '.join(STEPS)}; got {', '.join(sorted(unknown)) or 'none'}"
            raise CommandError(msg)
        self.steps = [s for s in STEPS if s in steps]
        self.operator = operator
        self.statuses = list(statuses) if statuses else None
        self.slugs = list(slugs or [])
        self.parallel = parallel
        self.timeout = timeout
        self.startup = startup
        self.record_found = record
        self.keep_hold = keep_hold
        self.resume = resume
        self.out = out or (lambda line: sys.stdout.write(f"{line}\n"))
        self.err = err or (lambda line: sys.stderr.write(f"{line}\n"))
        self.entries = []
        self.run_row = None

    # selection ----------------------------------------------------------------------------

    def _hospitals(self):
        hospitals = Hospital.objects.exclude(
            schema_name=get_public_schema_name()
        ).exclude(status=S.ARCHIVED)
        if self.slugs:
            found = set(
                hospitals.filter(slug__in=self.slugs).values_list("slug", flat=True)
            )
            if missing := sorted(set(self.slugs) - found):
                msg = f"no such hospital (or archived): {', '.join(missing)}"
                raise CommandError(msg)
            hospitals = hospitals.filter(slug__in=self.slugs)
        hospitals = hospitals.filter(status__in=self.statuses or MIGRATE_STATUSES)
        chosen = []
        for hospital in hospitals.order_by("slug"):
            if schema_exists(hospital.schema_name):
                chosen.append(hospital)
            else:
                self.out(f"{hospital.slug}: left out (it has no schema)")
        return chosen

    def _resumed(self):
        previous = FleetRun.objects.filter(pk=self.resume).first()
        if previous is None:
            msg = f"no fleet run {self.resume}"
            raise CommandError(msg)
        if previous.status == FleetRun.Status.RUNNING:
            # This run holds the fleet lock, so the process that wrote that row is gone
            # (killed, or its host restarted): close it before taking up its hospitals (C3)
            self._close_interrupted(previous)
        if not self.steps:
            self.steps = list(previous.steps)
        self.statuses = self.statuses or previous.selection.get("statuses")
        unfinished = previous.operations.filter(_not_done())
        self.slugs = sorted(set(unfinished.values_list("slug", flat=True)))
        return previous

    def _close_interrupted(self, previous):
        now = timezone.now()
        for op in previous.operations.filter(status=TenantOperation.Status.RUNNING):
            op.result = {**op.result, "outcome": "interrupted"}
            op.status = TenantOperation.Status.FAILED
            op.finished_at = now
            op.save(update_fields=["result", "status", "finished_at"])
        previous.status = FleetRun.Status.FAULT
        previous.error = (
            "interrupted: it stopped without finishing (killed or restarted)"
        )
        previous.finished_at = now
        previous.save(update_fields=["status", "error", "finished_at"])
        self.out(f"fleet run {previous.pk} was interrupted: closed as a fault")

    # the run ------------------------------------------------------------------------------

    def run(self):
        with fleet_lock():
            return self._run()

    def _run(self):
        previous = self._resumed() if self.resume else None
        if previous is not None and not self.slugs:
            self.out(
                f"fleet run {previous.pk}: every hospital finished; nothing to resume"
            )
            return None
        hospitals = self._hospitals()
        self.run_row = FleetRun.objects.create(
            steps=self.steps,
            selection={
                "statuses": self.statuses or list(MIGRATE_STATUSES),
                "slugs": self.slugs,
            },
            parallel=self.parallel,
            operator=self.operator[:150],
            startup=self.startup,
            tenant_fingerprint=release.code_fingerprint(),
            public_fingerprint=release.code_fingerprint(release.PUBLIC),
            app_version=getattr(settings, "APP_VERSION", "")[:64],
            resumed_from=previous,
        )
        self.out(
            f"fleet run {self.run_row.pk}: {', '.join(self.steps)} for "
            f"{len(hospitals)} hospital(s), {self.parallel} at a time"
        )
        now = timezone.now()
        for hospital in hospitals:
            before = release.recorded(hospital)
            op = TenantOperation.objects.create(
                kind=TenantOperation.Kind.UPGRADE,
                hospital=hospital,
                slug=hospital.slug,
                status=TenantOperation.Status.RUNNING,
                requested_by=self.operator[:255],
                input={"fleet_run": str(self.run_row.pk), "steps": self.steps},
                steps=[{"name": s, "status": "pending"} for s in self.steps],
                # "running" until _close: a killed run's hospitals are resumed (C3)
                result={
                    "fingerprint_before": before[0] if before else None,
                    "outcome": "running",
                },
                started_at=now,
                fleet_run=self.run_row,
            )
            self.entries.append(_Entry(hospital, op))
        try:
            self._preflight()
            for step in self.steps:
                getattr(self, f"_{step}")()
        except FleetFault as exc:
            self._finish(fault=str(exc), keeps_holds=exc.keeps_holds)
            raise
        except Exception as exc:
            self._finish(fault=release.safe_error(exc))
            raise
        except (
            BaseException
        ) as exc:  # Ctrl-C, or SIGTERM (the command makes it SystemExit)
            self._finish(fault=f"interrupted ({type(exc).__name__})")
            raise
        self._finish()
        return self.run_row

    def _preflight(self):
        """Faults that are the same for every hospital, found before any hospital is held or
        migrated (C1/S5): the public schema behind this build, and a bad pack."""
        if {"migrate", "check"} & set(self.steps):
            public = release.schema_state(release.PUBLIC)
            if not public.current:
                msg = (
                    f"the public schema is {public.verdict} ({public.describe()}): run "
                    "migrate_schemas --shared first"
                )
                raise FleetFault(msg)
        if "packs" in self.steps and any(
            e.hospital.status in PACKS_STATUSES for e in self.entries
        ):
            from ihr_refdata import engine

            try:
                engine.prepare(self._pack_actor(), record=False)
            except engine.PackError as exc:
                msg = f"ihr-packs: refused for every hospital: {exc}"
                raise FleetFault(msg) from None

    def _pack_actor(self):
        return f"packs:{'startup' if self.startup else self.operator}"[:150]

    def _note(self, entry, step, status, **details):
        entry.steps[step] = {"name": step, "status": status, **details}
        if status not in ("done", "skipped"):
            entry.stopped = True

    def _alive(self, statuses=None):
        return [
            e
            for e in self.entries
            if not e.stopped and (statuses is None or e.hospital.status in statuses)
        ]

    def _skip_others(self, step, statuses):
        for entry in self.entries:
            if not entry.stopped and entry.hospital.status not in statuses:
                self._note(entry, step, "skipped", note=f"{entry.hospital.status}")

    # steps --------------------------------------------------------------------------------

    def _hold(self):
        alive = self._alive()
        release.hold([e.hospital.pk for e in alive], reason=release.HOLD_WINDOW)
        for entry in alive:
            self._note(entry, "hold", "done")

    def _migrate(self):
        todo = []
        for entry in self._alive():
            if needs_migrate(entry.hospital):
                todo.append(entry)
            else:
                settle(entry.hospital)
                self._note(entry, "migrate", "done", note="already this build's")
        if not todo:
            return
        # Mark, then migrate (design item 3): the gate answers 503 for them from now on
        release.hold([e.hospital.pk for e in todo], reason=release.HOLD_MIGRATE)
        self.out(f"migrating {len(todo)} hospital(s)")
        waiting = todo
        for delay in (*RETRY_DELAYS, None):
            items = [
                (e.op.pk, (e.hospital.schema_name, e.hospital.slug)) for e in waiting
            ]
            results = run_pool(migrate_one, items, self.parallel, self.timeout)
            busy = []
            for entry in waiting:
                result = results[entry.op.pk]
                if result.get("outcome") == "crashed":
                    result = self._fallback(entry, result)
                if result.get("outcome") == "busy":
                    busy.append(entry)
                    continue
                self._migrated(entry, result)
            if not busy:
                return
            if delay is None:
                for entry in busy:
                    self._note(
                        entry,
                        "migrate",
                        "deferred",
                        error=f"another job held this hospital through "
                        f"{len(RETRY_DELAYS) + 1} tries",
                    )
                return
            for entry in busy:
                self.err(
                    f"ihr-fleet: hospital={entry.hospital.slug} busy: "
                    f"trying again in {delay} s"
                )
            time.sleep(delay)
            waiting = busy

    def _fallback(self, entry, result):
        code = result.get("exit_code")
        self.err(
            f"ihr-fleet: hospital={entry.hospital.slug} migration process died "
            f"(exit code {code}); trying once more in a subprocess"
        )
        try:
            with runner.exclusive(entry.hospital.slug):
                again = migrate_in_subprocess(entry.hospital.schema_name, self.timeout)
        except runner.ProvisioningError:
            return {"outcome": "busy"}
        again["first_try"] = f"the forked process died (exit code {code})"
        if "backend_stopped" in result:
            again["backend_stopped"] = result["backend_stopped"]
        return again

    def _migrated(self, entry, result):
        details = {
            k: result[k]
            for k in (
                "seconds",
                "attempts",
                "error",
                "via",
                "first_try",
                "backend_stopped",
            )
            if k in result
        }
        if result.get("outcome") != "done":
            self._note(entry, "migrate", result.get("outcome", "failed"), **details)
            return
        state = settle(entry.hospital)
        if state.current:
            self._note(entry, "migrate", "done", **details)
        else:
            details["error"] = f"the schema is {state.verdict} after migrating"
            self._note(entry, "migrate", "failed", **details)

    def _failed_in_this_release(self, hospital, row):
        """Held, and a run of this build that migrates already tried it and did not finish
        it (C2): a known failure of this release, not a release nobody ran."""
        if row is None or row[1] is None:
            return False
        earlier = TenantOperation.objects.filter(
            kind=TenantOperation.Kind.UPGRADE,
            hospital=hospital,
            fleet_run__tenant_fingerprint=release.code_fingerprint(),
            fleet_run__steps__contains=["migrate"],
        ).filter(_not_done())
        if self.run_row is not None:
            earlier = earlier.exclude(fleet_run=self.run_row)
        return earlier.exists()

    def _check(self):
        # The public schema was checked before any step (_preflight)
        if self.record_found:
            platform = Hospital.objects.filter(schema_name=release.PUBLIC).first()
            if platform is not None:
                release.record(platform)
        code, mismatched = release.code_fingerprint(), []
        for entry in self._alive():
            hospital = entry.hospital
            if self.record_found:
                # Only a current schema is written: never "" while the previous build may
                # still serve this hospital (S4)
                state = release.record(hospital, only_current=True)
            else:
                state = release.schema_state(hospital.schema_name)
            row = release.recorded(hospital)
            fingerprint = row[0] if row else ""
            if state.current and fingerprint == code:
                self._note(entry, "check", "done")
                continue
            problem = (
                state.describe() if not state.current else "not recorded (use --record)"
            )
            if hospital.status not in LIVE_STATUSES:
                # Provisioning runs migrate themselves; offboarding is never served
                self._note(entry, "check", "skipped", note=problem[:300])
            elif "migrate" not in self.steps and self._failed_in_this_release(
                hospital, row
            ):
                # It stays gated (held) and is reported; the others start (C2)
                self._note(
                    entry,
                    "check",
                    "failed",
                    error=f"{problem[:200]}; this release's migration of it did not "
                    "finish (held until it is resumed)",
                )
            else:
                mismatched.append(hospital.slug)
                self._note(entry, "check", "failed", error=problem[:300])
            self.out(f"{hospital.slug}: {problem}")
        if mismatched and "migrate" not in self.steps:
            msg = (
                f"{len(mismatched)} live hospital(s) are not on this build's schema "
                f"({', '.join(mismatched[:10])}). The start migrates no hospital: run the "
                "release (scripts/staging.sh release; until P1-T18b: scripts/staging.sh "
                "fleet migrate check roles packs --operator <you>; dev: "
                "scripts/care-dev.sh migrate-hospitals)"
            )
            raise FleetFault(msg)

    def _roles(self):
        self._skip_others("roles", ROLES_STATUSES)
        candidates = self._ready(self._alive(ROLES_STATUSES), "roles")
        passed = []
        for entry in candidates:  # read-only first, for every hospital (design item 6)
            try:
                roles_check(entry.hospital)
            except CommandError as exc:  # the guarded sync's refusal (value-free, S7)
                self._note(entry, "roles", "held", error=release.safe_error(exc))
            except Exception as exc:
                self._note(entry, "roles", "failed", error=release.safe_error(exc))
            else:
                passed.append(entry)
        refused = [e for e in candidates if e.steps.get("roles", {}).get("status")]
        if refused:
            release.hold([e.hospital.pk for e in refused], reason=release.HOLD_ROLES)
        waiting = passed
        for delay in (*RETRY_DELAYS, None):
            busy = [entry for entry in waiting if self._sync_roles(entry)]
            if not busy:
                return
            if delay is None:
                release.hold([e.hospital.pk for e in busy], reason=release.HOLD_ROLES)
                for entry in busy:
                    self._note(entry, "roles", "deferred", error="another job held it")
                return
            time.sleep(delay)
            waiting = busy

    def _sync_roles(self, entry):
        """One hospital's role sync, under its lock. True if another job held the lock."""
        began = time.monotonic()
        try:
            with runner.exclusive(entry.hospital.slug):
                roles_sync(entry.hospital)
        except runner.ProvisioningError:
            return True
        except (
            CommandError
        ) as exc:  # refused under the lock (a holder appeared meanwhile)
            release.hold([entry.hospital.pk], reason=release.HOLD_ROLES)
            self._note(entry, "roles", "held", error=release.safe_error(exc))
        except Exception as exc:
            release.hold([entry.hospital.pk], reason=release.HOLD_ROLES)
            self._note(entry, "roles", "failed", error=release.safe_error(exc))
        else:
            seconds = round(time.monotonic() - began, 1)
            self._note(entry, "roles", "done", seconds=seconds)
        finally:
            connection.set_schema_to_public()
        return False

    def _ready(self, entries, step):
        code, ready = release.code_fingerprint(), []
        for entry in entries:
            row = release.recorded(entry.hospital)
            if row is not None and row[0] == code:
                ready.append(entry)
            else:
                self._note(
                    entry, step, "failed", error="its schema is not this build's"
                )
        return ready

    def _packs(self):
        from ihr_audit import context as audit
        from ihr_refdata import engine
        from ihr_refdata.management.commands.ihr_pack_apply import (
            Command as PackCommand,
        )

        self._skip_others("packs", PACKS_STATUSES)
        candidates = self._ready(self._alive(PACKS_STATUSES), "packs")
        if not candidates:
            return
        packer = PackCommand(stdout=_Lines(self.out), stderr=_Lines(self.err))
        actor = self._pack_actor()
        with audit.job({"job": "packs", "via": "fleet"}, actor):
            try:
                releases = engine.prepare(actor, record=True)
            except engine.PackError as exc:
                # Checked before any step (_preflight); if it still happens here, the
                # hospitals' own steps finished: they are released all the same (C1)
                msg = f"ihr-packs: refused for every hospital: {exc}"
                raise FleetFault(msg, keeps_holds=False) from None
            outcomes = {}
            for entry in candidates:
                began = time.monotonic()
                outcomes[entry.op.pk] = packer._one(  # noqa: SLF001 (the same apply)
                    entry.hospital, releases, actor, check=False, retry=True
                )
                entry.steps["packs"] = {"seconds": round(time.monotonic() - began, 1)}
            busy = [e for e in candidates if outcomes[e.op.pk] == "busy"]
            given_up = packer._retry(  # noqa: SLF001 (the same retries)
                [e.hospital for e in busy], releases, actor
            )
            still = {h.pk for h in given_up}
        for entry in candidates:
            outcome = outcomes[entry.op.pk]
            if outcome == "busy":
                outcome = "deferred" if entry.hospital.pk in still else "done"
            # A pack failure is recorded and alerted by ihr_pack_apply's own line; the
            # hospital is still served (its packs are retried by the next run).
            details = {"seconds": entry.steps["packs"]["seconds"]}
            entry.steps["packs"] = {"name": "packs", "status": outcome, **details}

    def _release(self):
        # A hospital that did not finish an earlier step of this run stays held
        alive = self._alive()
        release.release_hold([e.hospital.pk for e in alive])
        for entry in alive:
            if not release.schema_ready(entry.hospital):
                self.out(
                    f"{entry.hospital.slug}: released, but its schema is not this "
                    "build's: it stays at 503 until migrated"
                )
            self._note(entry, "release", "done")

    # the end ------------------------------------------------------------------------------

    def _intended_hold(self):
        """The operator asked for the hospitals to stay held (a window, --keep-hold)."""
        return "hold" in self.steps or self.keep_hold

    def _finish(self, fault="", keeps_holds=True):
        if (not fault or not keeps_holds) and not self.keep_hold:
            # A pack failure leaves the hospital served (its own line and alert, packs.yml)
            if "migrate" in self.steps:
                release.release_hold(
                    [e.hospital.pk for e in self.entries if e.finished(self.steps)]
                )
            if "roles" in self.steps and not self._intended_hold():
                # A hold a refused role sync set, now that its roles pass (C4/S2); never a
                # window's or a migration's
                passed = [
                    e.hospital.pk
                    for e in self.entries
                    if e.steps.get("roles", {}).get("status") == "done"
                    and e.finished(self.steps)
                ]
                release.release_hold(passed, reason=release.HOLD_ROLES)
        counts, end = {}, timezone.now()
        for entry in self.entries:
            outcome = self._close(entry, end, fault)
            counts[outcome] = counts.get(outcome, 0) + 1
        row = self.run_row
        if row is None:
            return
        row.counts = counts
        row.error = fault[:400]
        if fault:
            row.status = FleetRun.Status.FAULT
        elif set(counts) <= {"done"}:
            row.status = FleetRun.Status.FINISHED
        else:
            row.status = FleetRun.Status.INCOMPLETE
        row.finished_at = end
        row.save(update_fields=["counts", "error", "status", "finished_at"])
        self.out(
            f"fleet run {row.pk}: {row.status}"
            + "".join(f", {n} {outcome}" for outcome, n in sorted(counts.items()))
        )

    def _close(self, entry, end, fault):
        """Write one hospital's record and, if it is not served, its line. Its outcome."""
        outcome = entry.outcome()
        if fault and outcome == "done" and len(entry.steps) < len(self.steps):
            outcome = "stopped"  # the run stopped before it got to this hospital
        served = release.schema_ready(entry.hospital)
        live = entry.hospital.status in LIVE_STATUSES
        alert = None
        if not served:
            if outcome in ("failed", "deferred", "held"):
                alert = outcome  # a step of this run went wrong for it
            elif live and not self._intended_hold():
                alert = (
                    "held"  # a live hospital not served: never reported as done (C4)
                )
        if outcome == "done" and alert == "held":
            outcome = "held"
        op = entry.op
        op.steps = [
            entry.steps.get(s, {"name": s, "status": "pending"}) for s in self.steps
        ]
        after = release.recorded(entry.hospital)
        op.result.update(
            outcome=outcome,
            served=served,
            fingerprint_after=after[0] if after else None,
            held=bool(after and after[1]),
        )
        op.status = (
            TenantOperation.Status.SUCCEEDED
            if outcome == "done"
            else TenantOperation.Status.FAILED
        )
        op.finished_at = end
        op.save(update_fields=["steps", "result", "status", "finished_at"])
        line = f"{entry.hospital.slug}: {outcome}"
        for record in op.steps:
            if record.get("error"):
                line += f" ({record['name']}: {record['error']})"
        self.out(line)
        if alert and SLUG_RE.match(entry.hospital.slug):
            self.err(log_line(entry.hospital.slug, alert))
        elif not served and not live:
            # Provisioning or offboarding: not served by design, nothing to alert on (C7/S8)
            self.out(
                f"{entry.hospital.slug}: not served ({entry.hospital.status}; not on "
                "this build's schema)"
            )
        elif not served:
            self.out(f"{entry.hospital.slug}: held on purpose (ihr_fleet_run release)")
        return outcome
