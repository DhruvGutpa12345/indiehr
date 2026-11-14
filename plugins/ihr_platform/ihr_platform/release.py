"""Which build a hospital's schema belongs to, and whether it may be served (P1-T18a).

Design: docs/reviews/p1-t18/design.md, "P1-T18a" as amended by "After the attack".

- **Code fingerprint** (``code_fingerprint``): the sha256 of the sorted leaf migrations of the
  tenant apps (or, for ``"public"``, the shared apps), from Django's ``MigrationLoader``. Built
  once per process, on first use (measured on the dev image: 80-120 ms and +7 MB RSS per process,
  then about 2 microseconds a call; P1-T18a).
- **Schema state** (``schema_state``): a schema is *current* when every tenant-app migration of
  this build is applied in it and its ``django_migrations`` holds no tenant-app row this build
  does not know. *Known* is: on disk, named in any loaded migration's ``replaces`` (CARE's
  squashed migrations leave about 450 such rows) or listed in ``retired_migrations.txt``. Rows
  of shared-only apps (django-tenants records them in every schema) are ignored. A row unknown
  to this build means the schema is *ahead*: an older image on a newer schema is refused too.
- **Recorded state** (``HospitalSchemaState``, one public row per hospital): the fingerprint
  the schema was last verified against, and the hold of a release window. Written only here,
  with ``get_or_create`` and ``update()``; recorded after django-tenants' ``schema_migrated``
  signal (sent on success only: ``signals.py``) and by ``ihr_fleet_run``.
- ``servable(hospital)``: ACTIVE, not held (for a release or a restore, P1-T22b), and its
  recorded fingerprint is this build's. The
  gate (``release_gate.py``) answers 503 for a hospital whose schema is not ready; staff and
  service sign-ins, beat, the staff worker, the provisioner, every queued Celery task
  (``celery_gate.py``) and the operator commands that write into a hospital
  (``require_ready``) use the same checks.
"""

import functools
import hashlib
import re
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

PUBLIC = "public"
RETIRED_MIGRATIONS = Path(__file__).with_name("retired_migrations.txt")
_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
# Fleet children leave recording to the parent, which checks the schema itself (C11).
RECORD_ON_SIGNAL = {"on": True}


# --- the code ---------------------------------------------------------------------------


def _labels(app_list):
    from django_tenants.utils import app_labels

    return frozenset(app_labels(app_list))


def tenant_labels():
    return _labels(settings.TENANT_APPS)


def public_labels():
    return _labels(settings.SHARED_APPS)


def labels_for(kind):
    return public_labels() if kind == PUBLIC else tenant_labels()


def retired_migrations():
    """(app, name) pairs upstream deleted or stopped listing in ``replaces``, kept known."""
    pairs = set()
    for line in RETIRED_MIGRATIONS.read_text().splitlines():
        entry = line.split("#")[0].strip()
        if entry:
            app, _, name = entry.partition(".")
            pairs.add((app, name))
    return frozenset(pairs)


@dataclass(frozen=True)
class _Facts:
    fingerprints: dict
    nodes: dict  # (app, name) -> Migration, after squashed migrations replaced theirs
    known: frozenset


@functools.cache
def _facts():
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(None, ignore_no_migrations=True)
    known = set(loader.disk_migrations)
    for migration in loader.disk_migrations.values():
        known.update(tuple(pair) for pair in (migration.replaces or ()))
    known |= retired_migrations()
    leaves = loader.graph.leaf_nodes()
    fingerprints = {}
    for kind in ("tenant", PUBLIC):
        labels = labels_for(kind)
        names = sorted(f"{app}.{name}" for app, name in leaves if app in labels)
        fingerprints[kind] = hashlib.sha256("\n".join(names).encode()).hexdigest()
    return _Facts(fingerprints, dict(loader.graph.nodes), frozenset(known))


def code_fingerprint(kind="tenant"):
    """This build's fingerprint of the tenant (default) or the public schema."""
    return _facts().fingerprints[kind]


# --- a schema ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SchemaState:
    schema: str
    kind: str
    pending: tuple = ()  # this build's migrations not applied there
    unknown: tuple = ()  # rows there that this build does not know
    missing: bool = False  # no schema, or no django_migrations in it

    @property
    def current(self):
        return not (self.missing or self.pending or self.unknown)

    @property
    def verdict(self):
        if self.missing:
            return "missing"
        if self.unknown:
            return "ahead"
        return "behind" if self.pending else "current"

    @property
    def fingerprint(self):
        """What may be recorded for it: this build's fingerprint, or "" (not this build's)."""
        return code_fingerprint(self.kind) if self.current else ""

    def describe(self, limit=3):
        if self.missing:
            return "no migrations table"
        parts = []
        if self.pending:
            names = ", ".join(f"{a}.{n}" for a, n in self.pending[:limit])
            parts.append(f"{len(self.pending)} migration(s) not applied ({names}…)")
        if self.unknown:
            names = ", ".join(f"{a}.{n}" for a, n in self.unknown[:limit])
            parts.append(f"{len(self.unknown)} unknown to this build ({names}…)")
        return "; ".join(parts) or "current"


def _recorded(schema_name):
    if not _SCHEMA_RE.match(schema_name):
        msg = f"not a schema name: {schema_name!r}"
        raise ValueError(msg)
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", [f'"{schema_name}".django_migrations'])
        if cursor.fetchone()[0] is None:
            return None
        # the schema name is checked above and quoted; nothing else is interpolated
        cursor.execute(f'SELECT app, name FROM "{schema_name}".django_migrations')  # noqa: S608
        return {(app, name) for app, name in cursor.fetchall()}


def _applied(key, migration, rows):
    # As Django's loader decides it: a squashed migration counts once all it replaces are.
    return key in rows or bool(
        migration.replaces and all(tuple(r) in rows for r in migration.replaces)
    )


def schema_state(schema_name):
    """The state of ``schema_name`` against this build (one query; nothing written)."""
    kind = PUBLIC if schema_name == PUBLIC else "tenant"
    rows = _recorded(schema_name)
    if rows is None:
        return SchemaState(schema_name, kind, missing=True)
    labels, facts = labels_for(kind), _facts()
    pending = sorted(
        key
        for key, migration in facts.nodes.items()
        if key[0] in labels and not _applied(key, migration, rows)
    )
    unknown = sorted(row for row in rows if row[0] in labels and row not in facts.known)
    return SchemaState(schema_name, kind, tuple(pending), tuple(unknown))


# --- the recorded state (HospitalSchemaState) -------------------------------------------


def _state_model():
    from ihr_platform.models import HospitalSchemaState

    return HospitalSchemaState


def _write(hospital_id, **fields):
    model = _state_model()
    _, created = model.objects.get_or_create(hospital_id=hospital_id, defaults=fields)
    if not created:
        model.objects.filter(pk=hospital_id).update(**fields)


def record(hospital, only_current=False):
    """Check ``hospital``'s schema now and record what was found: this build's fingerprint
    if it is current, else "" (so the gate refuses it). Returns the SchemaState.

    ``only_current``: write only a current schema's fingerprint and leave the row as it is
    otherwise. The start-up check uses it: while the previous build still serves, writing ""
    for a schema that is behind the *new* build would take that hospital down at once (S4);
    only ``ihr_fleet_run migrate``, which holds a hospital first, may leave it gated."""
    state = schema_state(hospital.schema_name)
    if state.current or not only_current:
        _write(
            hospital.pk, schema_fingerprint=state.fingerprint, checked_at=timezone.now()
        )
    return state


def record_after_migrate(schema_name):
    """For the ``schema_migrated`` signal: record the hospital whose schema this is, if any
    (``public`` before ``ihr_create_tenant --public``, or a bench schema, has none)."""
    from ihr_platform.models import Hospital

    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.ihr_platform_hospitalschemastate')")
        if cursor.fetchone()[0] is None:  # this very migrate is creating the table
            return None
    hospital = (
        Hospital.objects.filter(schema_name=schema_name)
        .only("pk", "schema_name")
        .first()
    )
    return record(hospital) if hospital is not None else None


def recorded(hospital):
    """(schema_fingerprint, upgrading_since) as recorded, or None (never checked)."""
    return (
        _state_model()
        .objects.filter(pk=hospital.pk)
        .values_list("schema_fingerprint", "upgrading_since")
        .first()
    )


HOLD_WINDOW = (
    "window"  # ihr_fleet_run hold: a release window, released by migrate or release
)
HOLD_MIGRATE = "migrate"  # mark, then migrate (design item 3)
HOLD_ROLES = "roles"  # a refused or failed role sync (design item 6)


def hold(hospital_ids, reason=HOLD_MIGRATE):
    """Hold these hospitals (the gate answers 503 for them), in one transaction. Returns how
    many were newly held. A hospital already held keeps its reason, except that a window's
    hold is the strongest: a later roles-only run must never lift it."""
    model, now, ids = _state_model(), timezone.now(), list(hospital_ids)
    with transaction.atomic():
        for pk in ids:
            model.objects.get_or_create(hospital_id=pk)
        held = model.objects.filter(pk__in=ids, upgrading_since__isnull=True).update(
            upgrading_since=now, hold_reason=reason
        )
        if reason == HOLD_WINDOW:
            model.objects.filter(pk__in=ids).update(hold_reason=reason)
        return held


def release_hold(hospital_ids, reason=None):
    """Clear the hold of these hospitals; with ``reason``, only a hold set for that reason."""
    rows = _state_model().objects.filter(
        pk__in=list(hospital_ids), upgrading_since__isnull=False
    )
    if reason is not None:
        rows = rows.filter(hold_reason=reason)
    return rows.update(upgrading_since=None, hold_reason="")


# --- the restore hold (P1-T22b) ---------------------------------------------------------------
# Set and lifted only by ihr_hospital_restore, for its own operation: a release window, the fleet
# runner, pack apply and the dumper never lift it (release_hold above does not touch it). Every
# check that serves a hospital refuses it (schema_ready, ready_q), so beat, the staff worker,
# queued Celery tasks, sign-ins and the gate all leave a hospital being restored alone.
HOLD_RESTORE = "restore"


class RestoreHeldError(RuntimeError):
    """Another restore operation holds this hospital."""


def restore_hold(hospital, operation_id):
    """Hold ``hospital`` for restore ``operation_id`` (idempotent for the same operation).
    Refuses (RestoreHeldError) when another operation holds it."""
    model, now = _state_model(), timezone.now()
    with transaction.atomic():
        model.objects.get_or_create(hospital_id=hospital.pk)
        row = model.objects.select_for_update().get(pk=hospital.pk)
        if row.restore_operation and str(row.restore_operation) != str(operation_id):
            msg = f"{hospital.slug} is held for restore {row.restore_operation}"
            raise RestoreHeldError(msg)
        if not row.restore_operation:
            model.objects.filter(pk=hospital.pk).update(
                restore_operation=operation_id, restore_since=now
            )
    return True


def restore_holder(hospital):
    """The restore operation (uuid string) holding ``hospital``, or ""."""
    value = (
        _state_model()
        .objects.filter(pk=hospital.pk)
        .values_list("restore_operation", flat=True)
        .first()
    )
    return str(value) if value else ""


def lift_restore_hold(hospital, operation_id):
    """Lift the restore hold of ``operation_id`` (never another operation's). Returns how many
    rows changed (0 or 1)."""
    return (
        _state_model()
        .objects.filter(pk=hospital.pk, restore_operation=operation_id)
        .update(restore_operation=None, restore_since=None)
    )


# --- the job lock (P1-T22b review C4) ------------------------------------------------------
# Every background job that works in one hospital (a CARE Celery task, a staff-worker job)
# holds that hospital's job lock, shared, for its whole run: it runs between transactions, so a
# restore's drain cannot see it in pg_stat_activity. The restore takes the lock exclusively once,
# after its hold: that waits for every job that began before the hold, and a job that begins
# after it sees the hold (it checks only once it holds the lock) and defers. A job never waits
# for the lock: while the drain holds it, or waits for it (Postgres queues a new shared request
# behind a waiting exclusive one), the job is told so at once and defers as for a held
# hospital, so no worker slot (shared by every hospital) is kept for the drain's length.


def job_key(schema_name):
    """The advisory lock key of ``schema_name``'s background jobs (not runner.lock_key's)."""
    digest = hashlib.sha256(f"ihr_job:{schema_name}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


@contextmanager
def job(schema_name):
    """Hold ``schema_name``'s job lock, shared, on this process's connection while the job
    runs (nothing for public). Yields True when held; False, at once, when a restore's drain
    holds it or waits for it: the caller then defers the job (it must not run). A job whose
    connection went away lost its lock with it."""
    if not schema_name or schema_name == PUBLIC:
        yield True
        return
    key = job_key(schema_name)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock_shared(%s)", [key])
        taken = bool(cursor.fetchone()[0])
    if not taken:
        yield False
        return
    try:
        yield True
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock_shared(%s)", [key])
        except Exception:  # a broken connection: closing it frees the lock
            connection.close()


def hold_reason(hospital):
    """Why ``hospital`` is held ("" when it is not)."""
    row = (
        _state_model()
        .objects.filter(pk=hospital.pk, upgrading_since__isnull=False)
        .values_list("hold_reason", flat=True)
        .first()
    )
    return row or ""


# --- may it be served? -------------------------------------------------------------------


def schema_ready(hospital):
    """Not held, and its schema recorded as this build's (one indexed query). The platform
    (public schema) is never held here. Nor is a schema in which this process found rights
    beyond the model for the app role (P1-T19a review C2, db_roles.gated_schemas)."""
    from ihr_platform import db_roles

    if hospital.schema_name == PUBLIC:
        return True
    if hospital.schema_name in db_roles.gated_schemas():
        return False
    row = (
        _state_model()
        .objects.filter(pk=hospital.pk)
        .values_list("schema_fingerprint", "upgrading_since", "restore_operation")
        .first()
    )
    return (
        row is not None
        and row[1] is None
        and row[2] is None  # the restore hold (P1-T22b)
        and row[0] == code_fingerprint()
    )


def servable(hospital):
    """ACTIVE, not held, and on this build's schema."""
    from ihr_platform.models import Hospital

    return hospital.status == Hospital.Status.ACTIVE and schema_ready(hospital)


def require_ready(hospital):
    """For operator commands that write into a hospital's schema (S6): refuse one that is
    held for a release or whose schema is not this build's (CommandError)."""
    from django.core.management.base import CommandError

    if restore_holder(hospital):
        msg = (
            f"{hospital.slug} is held for a restore (operation {restore_holder(hospital)}): "
            "finish it (ihr_hospital_restore --resume) or abort it first"
        )
        raise CommandError(msg)
    if not schema_ready(hospital):
        msg = (
            f"{hospital.slug} is held for a release or not on this build's schema: run "
            "ihr_fleet_run check (and --resume the release) first"
        )
        raise CommandError(msg)


def ready_q(prefix=""):
    """A filter for rows (of Hospital, or through ``prefix``) whose hospital's schema is
    ready: recorded as this build's, not held, and not gated by this process's database
    role check (P1-T19a review C2)."""
    from ihr_platform import db_roles

    ready = Q(
        **{
            f"{prefix}schema_state__schema_fingerprint": code_fingerprint(),
            f"{prefix}schema_state__upgrading_since__isnull": True,
            # never a hospital held for a restore (P1-T22b)
            f"{prefix}schema_state__restore_operation__isnull": True,
        }
    )
    if gated := db_roles.gated_schemas():
        ready &= ~Q(**{f"{prefix}schema_name__in": sorted(gated)})
    return ready


def window(now=None):
    """The announced window that is open now or comes next, or None."""
    from ihr_platform.models import MaintenanceWindow

    now = now or timezone.now()
    for item in MaintenanceWindow.objects.order_by("start"):
        if item.end > now:
            return item
    return None


# --- error records -----------------------------------------------------------------------

MAX_ERROR = 300
_VALUE_PATTERNS = (
    # Values Postgres or Django echo back (as redact.lua masks them in logs, P1-T9b)
    (re.compile(r"(Key \([^)]*\))=\(.*"), r"\1=(<x>)"),
    (re.compile(r"(Failing row contains )\(.*"), r"\1(<x>)"),
    (re.compile(r"(invalid input (?:syntax|value) for [^:\"]+: )\".*"), r'\1"<x>"'),
    (re.compile(r"(out of range: )\".*"), r'\1"<x>"'),
    (re.compile(r"[\w.%+-]+@[\w-][\w.-]*\.[A-Za-z]{2,}"), "<email>"),
    (re.compile(r"\+?\d[\d -]{4,}\d"), "<n>"),
)


def _database_error(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if getattr(exc, "sqlstate", None):
            return exc
        exc = exc.__cause__ or exc.__context__
    return None


def sqlstate(exc):
    found = _database_error(exc)
    return found.sqlstate if found is not None else None


def _value_free(exc):
    """Our own exception classes whose messages we write ourselves and never fill with row
    values: their first line is kept (masked all the same). Anything else keeps no text."""
    from ihr_platform.provisioning import runner, steps

    kinds = [runner.ProvisioningError, steps.StepError]
    try:
        from ihr_platform.fleet import FleetFault

        kinds.append(FleetFault)
    except ImportError:  # pragma: no cover (fleet imports this module)
        pass
    try:
        from ihr_roles.management.commands.sync_permissions_roles import RoleSyncRefused

        kinds.append(RoleSyncRefused)
    except ImportError:  # pragma: no cover (ihr_roles is always installed with us)
        pass
    return isinstance(exc, tuple(kinds))


def _where_raised(exc):
    """The innermost frame's file name and line: a pointer that holds no value."""
    frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    if not frames:
        return ""
    return f"; at {Path(frames[-1].filename).name}:{frames[-1].lineno}"


def safe_error(exc, migration=None):
    """An error record that never holds row values (O10, S7): for a database error its
    class, SQLSTATE, constraint, table and column, never Postgres' message or DETAIL; for
    one of our own value-free exceptions (provisioning, fleet, the guarded role sync) its
    class and first line, with values that look like data masked; for anything else (a
    ValueError or KeyError in a data migration can carry a patient's name) only its class and
    where it was raised. ``migration``: the one being applied, if known. Shared by
    provisioning and ihr_fleet_run."""
    found = _database_error(exc)
    where = f"; migration {migration}" if migration else ""
    if found is not None:
        parts = [type(exc).__name__, f"SQLSTATE {found.sqlstate}"]
        diag = getattr(found, "diag", None)
        for label, attribute in (
            ("constraint", "constraint_name"),
            ("table", "table_name"),
            ("column", "column_name"),
        ):
            if value := getattr(diag, attribute, None):
                parts.append(f"{label} {value}")
        return ("; ".join(parts) + where)[:MAX_ERROR]
    if not _value_free(exc):
        return f"{type(exc).__name__}{_where_raised(exc)}{where}"[:MAX_ERROR]
    lines = str(exc).strip().splitlines()
    text = lines[0] if lines else ""
    for pattern, replacement in _VALUE_PATTERNS:
        text = pattern.sub(replacement, text)
    return f"{type(exc).__name__}: {text[:MAX_ERROR]}{where}"
