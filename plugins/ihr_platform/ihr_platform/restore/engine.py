# ruff: noqa: S608 (SQL here names schemas checked against SCHEMA_RE; values are parameters)

"""One hospital restored from its dump, step by step (P1-T22b; docs/reviews/p1-t22/design.md,
P1-T22b as amended by "After the attack" 4-11). ``ihr_hospital_restore`` drives it; the steps
are a TenantOperation of kind ``restore``, resumed from the database's own state.

    precheck   the package (from the quarantine) and the live hospital: refusals before anything
               is touched (``Refused``)
    hold       the restore hold (release.restore_hold, never lifted by a release)
    drain      until no transaction of the app that began before the hold is open
    seal       the live trail sealed and fingerprinted
    restore    ONE transaction: lock the live trail, seal ALL of it (a final seal of the
               restore's own, whoever holds the sealer's lock or the horizon; refused if any
               entry is left unsealed), park the live schema as ihr_parked_<schema>_<time> (no
               app or watch rights), load the package under the real name (package.load:
               statements one at a time, expressions inspected before anything evaluates them,
               shape and rows checked), the restored chain checked, sequences advanced past the
               parked ones, the trail's guard, defaults, triggers and sealing from this build's
               code, security state merged strictly (inactive, deleted and unlinked users stay
               so; role assignments, patient grants and shares, memberships and role permissions
               not stronger than the parked copy's unless --keep-restored-access), and every
               other schema's and public's catalogue unchanged
    migrate    this build's migrations, role sync and reference packs, in this process, under
               this operation's lock (never ihr_fleet_run); run again by any later step that
               finds the schema behind this build (a release ran meanwhile)
    reconcile  sequences checked again; every staff session invalidated; patients' access
               caches rebuilt; invitations and access requests made after the dump or for users
               who no longer exist cancelled (a reset or a withdrawal for a user who still
               exists is kept); the hospital's Redis keys deleted; the profile difference
               shown
    keycloak   the hospital's Keycloak members against the restored staff links (fail closed;
               needs the operator's credential: staging.sh runs this step as admin)
    files      every object of the hospital in both file buckets as it was at the dump's time,
               from the buckets' versions; newer uploads reported and kept
    audit      the superseded segment archived, the restore event written, a ``restore``
               entry, sealed, fingerprinted, then ihr_audit_verify --slug must pass
    restart    nothing by default (no per-process state depends on a hospital's rows); with
               --restart the app containers are restarted (staging.sh does it)
    smoke      the schema is this build's, triggers and grants as modelled; the hold lifted

``--drill`` (on the restore-drill copy only, settings ``ihr_platform.settings.drill``): every
step that reaches outside the database (Redis, Keycloak, files, the fingerprint bucket, the
restart) is a reported dry run, and every other schema and ``public`` must be unchanged.
"""

import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from django_tenants.utils import schema_context
from psycopg import sql

from ihr_platform import release
from ihr_platform.models import (
    Hospital,
    HospitalSchemaState,
    StaffAccessRequest,
    StaffInvitation,
    TenantOperation,
)
from ihr_platform.provisioning import runner
from ihr_platform.restore import package

logger = logging.getLogger(__name__)

STEPS = (
    "precheck", "hold", "drain", "seal", "restore", "migrate", "reconcile", "keycloak",
    "files", "audit", "restart", "smoke",
)  # fmt: skip
# Abort (un-park, lift the hold) only before anything outside this hospital's schema changed
ABORTABLE = frozenset({"precheck", "hold", "drain", "seal", "restore", "migrate"})
PARKED_PREFIX = "ihr_parked_"
PARKED_RE = re.compile(r"^ihr_parked_(h_[a-z0-9]{10})_(\d{8}T\d{6})$")
PARKED_DAYS = 7  # a parked schema holds personal data: dropped after a week (Q-K33)
# The P1-T4 erasure ledger (TENANCY §7.5 correction 2; design "After the attack" 11). It does not
# exist yet: while it does not, no erasure can have been recorded. Once P4-T1 creates it, every
# restore refuses until P4-T1 teaches this module to replay the erasures after the restore point
ERASURE_LEDGER = "ihr_privacy_erasure"
DRILL_CLUSTER = "ihr-restore-drill"
DRAIN_SECONDS = 60
LOCK_WAIT = "60s"
OPERATOR_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
RESTORE_STATUSES = (Hospital.Status.ACTIVE, Hospital.Status.SUSPENDED)
EXIT_RESTART, EXIT_KEYCLOAK = 3, 4
PACKAGE_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MIN_REASON = 10  # characters: a reason is recorded, so it must say something
PAGE = 500  # Keycloak members per request
NAMED = 5  # differences named in a refusal
# The tables whose rows give or take away access (CARE): soft-deleted rows, ids stable, each
# with user_id and role_id (a direct patient grant is one: review S4)
ASSIGNMENTS = (
    "emr_organizationuser",
    "emr_facilityorganizationuser",
    "security_roleassociation",
    "emr_patientuser",
)
# A patient shared with an organisation (no user or role of its own): merged the same way
SHARES = ("emr_patientorganization",)
# The columns the strict merge (``_security``) needs in both copies of a table it merges. A
# dump of an older build without one of them is refused by the precheck, before anything is
# done (review, after the second verification: it failed half-way through the restore step)
MERGE_COLUMNS = {
    "users_user": ("id", "is_active", "deleted"),
    "ihr_identity_staffidentity": ("user_id", "keycloak_sub"),
    **dict.fromkeys(ASSIGNMENTS, ("id", "deleted", "user_id", "role_id")),
    "emr_patientuser": ("id", "deleted", "user_id", "role_id", "patient_id"),
    "emr_patientorganization": ("id", "deleted", "patient_id"),
    "security_rolepermission": ("role_id", "permission_id", "deleted"),
}
# ...and those it merges only where the dump's build has them (a flag that build did not have
# cannot come back; a cache it did not have needs no clearing): skipped and named otherwise
MERGE_IF_THERE = {"users_user": ("is_superuser", "is_staff", "cached_role_orgs")}


class Refused(Exception):  # noqa: N818 (a refusal, not an error)
    """A pre-check refused the restore; nothing was changed."""


class StepFailed(Exception):  # noqa: N818
    """A step could not finish: the hospital stays held; --resume after fixing, or --abort."""


class NeedsRestart(Exception):  # noqa: N818 (a state)
    """The app containers must be restarted now (per-process caches), then --resume."""


class NeedsKeycloak(Exception):  # noqa: N818 (a state)
    """The Keycloak check needs the operator's credential (staging.sh runs it as admin)."""


def log_line(slug, outcome, step=""):
    """The fixed line a log alert watches (deploy/logs/alerts/restore.yml): a failed one always
    names its step (``unknown`` when it failed outside one)."""
    if outcome == "failed":
        return f"ihr-restore: hospital={slug} outcome=failed step={step or 'unknown'}"
    return f"ihr-restore: hospital={slug} outcome={outcome}"


def parked_name(schema, when):
    return f"{PARKED_PREFIX}{schema}_{when:%Y%m%dT%H%M%S}"


def _schema_exists(cursor, name):
    cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", [name])
    return cursor.fetchone() is not None


def _one(cursor, text, params=()):
    cursor.execute(text, params)
    row = cursor.fetchone()
    return row[0] if row else None


def is_drill_database():
    """The restore-drill copy (staging.sh starts it with cluster_name ihr-restore-drill), never
    the live database."""
    with connection.cursor() as cursor:
        return _one(cursor, "SELECT current_setting('cluster_name')") == DRILL_CLUSTER


@dataclass
class Options:
    slug: str
    package_dir: Path | None = None
    reason: str = ""
    operator: str = ""
    drill: bool = False
    dry_run: bool = False
    keep_restored_access: str = (
        ""  # the recorded reason; "" = strict merge (the default)
    )
    free_bytes: int | None = None
    restarted: bool = False
    no_restart: bool = False  # tests: no containers to restart
    drain_seconds: int = DRAIN_SECONDS
    stop_after: str = (
        ""  # a step after which this run stops (the Keycloak step in its own one-off)
    )
    restart: bool = (
        False  # --restart: ask for the app containers' restart (step restart)
    )
    package_sha256: str = (
        ""  # what the quarantine printed for package.json (staging.sh)
    )
    unlogged: bool = False  # the admin one-off: its lines reach no log (C9)
    out: object = None
    err: object = None


@dataclass
class Restore:
    options: Options
    hospital: Hospital = None
    op: TenantOperation = None
    doc: dict = field(default_factory=dict)

    # --- output -----------------------------------------------------------------------------

    def say(self, text):
        (self.options.out or (lambda line: sys.stdout.write(f"{line}\n")))(text)

    def alert(self, text):
        (self.options.err or (lambda line: sys.stderr.write(f"{line}\n")))(text)

    # --- the record -------------------------------------------------------------------------

    def _note(self, step, status, **details):
        steps = [s for s in self.op.steps if s.get("name") != step]
        record = {"name": step, "status": status, **details}
        order = {name: i for i, name in enumerate(STEPS)}
        steps.append(record)
        steps.sort(key=lambda s: order.get(s.get("name"), 99))
        self.op.steps = steps
        self.op.save(update_fields=["steps"])

    def _done(self, step):
        return any(
            s.get("name") == step and s.get("status") == "done" for s in self.op.steps
        )

    def _result(self, **values):
        self.op.result = {**self.op.result, **values}
        self.op.save(update_fields=["result"])

    @property
    def schema(self):
        return self.hospital.schema_name

    @property
    def parked(self):
        return self.op.result.get("parked", "")

    # --- the run -----------------------------------------------------------------------------

    def start(self):
        """A new restore: pre-checks first (refusals change nothing), then the steps."""
        o = self.options
        if not OPERATOR_RE.match(o.operator or ""):
            msg = "--operator: 1-64 of letters, digits, . _ @ -"
            raise Refused(msg)
        if len(o.reason.strip()) < MIN_REASON:
            msg = "--reason: say why (at least 10 characters); it is recorded"
            raise Refused(msg)
        self.hospital = Hospital.objects.filter(slug=o.slug).first()
        if self.hospital is None:
            msg = f"no hospital {o.slug!r}"
            raise Refused(msg)
        if o.package_sha256 and not PACKAGE_SHA_RE.match(o.package_sha256):
            msg = "--package-sha256: the 64 hex digits the quarantine printed"
            raise Refused(msg)
        self.doc = self._read_package(o.package_sha256)
        report = self.precheck()
        if o.dry_run:
            return report
        if runner.is_busy(o.slug):
            # Never an operation whose first act fails on the lock (review C2): the nightly
            # dump (03:30-06:00) or another whole-hospital job holds it now
            msg = (
                f"{o.slug} is busy (its lock is held: a nightly dump, a release step or a pack "
                "apply); try again when it has finished"
            )
            raise Refused(msg)
        open_op = TenantOperation.objects.filter(
            kind=TenantOperation.Kind.RESTORE,
            slug=o.slug,
            status__in=["pending", "running", "failed"],
        ).first()
        if open_op is not None:
            msg = f"restore {open_op.pk} of {o.slug} is unfinished: --resume or --abort it"
            raise Refused(msg)
        self.op = TenantOperation.objects.create(
            kind=TenantOperation.Kind.RESTORE,
            hospital=self.hospital,
            slug=o.slug,
            status=TenantOperation.Status.RUNNING,
            requested_by=o.operator,
            input={
                "package": str(o.package_dir),
                "reason": o.reason[:500],
                "drill": o.drill,
                "keep_restored_access": o.keep_restored_access[:500],
                "source": self.doc.get("source", {}),
                "package_sha256": self.doc.get("package_sha256", ""),
                "restart": o.restart,
            },
            steps=[{"name": s, "status": "pending"} for s in STEPS],
            result={"precheck": report},
            started_at=timezone.now(),
        )
        self._note("precheck", "done")
        self.say(f"restore {self.op.pk} of {o.slug}: pre-checks passed")
        return self.run()

    def resume(self, op_id):
        o = self.options
        self.op = TenantOperation.objects.filter(
            pk=op_id, kind=TenantOperation.Kind.RESTORE
        ).first()
        if self.op is None:
            msg = f"no restore operation {op_id}"
            raise Refused(msg)
        if self.op.status in (
            TenantOperation.Status.SUCCEEDED,
            TenantOperation.Status.ROLLED_BACK,
        ):
            msg = f"restore {op_id} is {self.op.status}: nothing to resume"
            raise Refused(msg)
        self.hospital = self.op.hospital
        o.slug = self.hospital.slug
        o.package_dir = (
            Path(self.op.input["package"]) if not o.package_dir else o.package_dir
        )
        o.drill = bool(self.op.input.get("drill"))
        o.keep_restored_access = self.op.input.get("keep_restored_access", "")
        o.restart = o.restart or bool(self.op.input.get("restart"))
        o.reason = self.op.input.get("reason", "")
        o.operator = o.operator or self.op.requested_by
        if not o.unlogged:
            self.report_failures()
        # The package only while its load is still to come (review C6): read and checked again
        # then, against the hash recorded at the start; not once the restore step is done
        self.doc = (
            {}
            if self._done("restore")
            else self._read_package(self.op.input.get("package_sha256", ""))
        )
        self.op.status = TenantOperation.Status.RUNNING
        self.op.save(update_fields=["status"])
        return self.run()

    def run(self):
        """The steps not done yet, in order, under this operation's lock (held once)."""
        try:
            lock = runner.exclusive_for(self.hospital.slug, self.op.pk)
            lock.__enter__()
        except runner.ProvisioningError as exc:
            # Not a step (review C2): nothing was done, the operation stays abortable
            self.op.status = TenantOperation.Status.FAILED
            self._result(last_error=f"lock busy: {str(exc)[:200]}")
            self.op.save(update_fields=["status"])
            self.alert(log_line(self.hospital.slug, "failed", "lock"))
            raise StepFailed(str(exc)) from exc
        try:
            for step in STEPS:
                if self._done(step):
                    continue
                if STEPS.index(step) > STEPS.index("migrate"):
                    self._current_build()
                began = time.monotonic()
                self._note(step, "running")
                details = getattr(self, f"_{step}")() or {}
                details["seconds"] = round(time.monotonic() - began, 1)
                self._note(step, "done", **details)
                if step == self.options.stop_after:
                    self.op.status = TenantOperation.Status.PENDING
                    self.op.save(update_fields=["status"])
                    self.say(
                        f"restore {self.op.pk}: stopped after {step} (--stop-after)"
                    )
                    return 0
                self.say(
                    f"{self.hospital.slug}: {step} done ({round(time.monotonic() - began, 1)} s)"
                )
        except NeedsRestart:
            self._note("restart", "waiting")
            self.op.status = TenantOperation.Status.FAILED
            self.op.save(update_fields=["status"])
            self.say(
                f"restore {self.op.pk}: restart the app containers now (per-process caches), "
                "then --resume --restarted"
            )
            return EXIT_RESTART
        except NeedsKeycloak as exc:
            self._note("keycloak", "waiting", note=str(exc)[:300])
            self.op.status = TenantOperation.Status.FAILED
            self.op.save(update_fields=["status"])
            self.say(f"restore {self.op.pk}: {exc}")
            return EXIT_KEYCLOAK
        except Exception as exc:
            step = next(
                (s["name"] for s in self.op.steps if s.get("status") == "running"), ""
            )
            self._fail(step, exc)
            raise
        finally:
            lock.__exit__(None, None, None)
            connection.set_schema_to_public()
        self.op.status = TenantOperation.Status.SUCCEEDED
        self.op.finished_at = timezone.now()
        self.op.save(update_fields=["status", "finished_at"])
        self.alert(log_line(self.hospital.slug, "done"))
        return 0

    def _fail(self, step, exc):
        connection.set_schema_to_public()
        error = release.safe_error(exc)
        if isinstance(exc, (Refused, StepFailed, package.PackageError)):
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
        if step:
            # ``logged``: whether this line reached the log alerts (the admin one-off's never
            # does: the next logged run reports it, ``report_failures``; review C9)
            self._note(step, "failed", error=error, logged=not self.options.unlogged)
        self.op.status = TenantOperation.Status.FAILED
        self.op.save(update_fields=["status"])
        self.alert(log_line(self.hospital.slug, "failed", step))

    def report_failures(self, op_id=None):
        """The failure line of every failed step whose line no log has seen (the Keycloak step
        fails in the unlogged admin one-off), written here once (a logged process: the
        migrate one-off) and marked. Returns how many."""
        if op_id is not None:
            self.op = TenantOperation.objects.filter(
                pk=op_id, kind=TenantOperation.Kind.RESTORE
            ).first()
            if self.op is None:
                msg = f"no restore operation {op_id}"
                raise Refused(msg)
            self.hospital = self.op.hospital
        lines = 0
        for record in list(self.op.steps):
            if record.get("status") == "failed" and record.get("logged") is False:
                self.alert(log_line(self.hospital.slug, "failed", record["name"]))
                self._note(
                    record["name"],
                    "failed",
                    **{
                        k: v
                        for k, v in record.items()
                        if k not in ("name", "status", "logged")
                    },
                    logged=True,
                )
                lines += 1
        return lines

    def _current_build(self):
        """Before every step after migrate: the restored schema must be this build's. A
        release that ran while this restore was unfinished (review C1) leaves it behind: it is
        migrated again here, under this operation's lock; ahead (a rollback ran) is a stop."""
        state = release.schema_state(self.schema)
        if state.current:
            return
        self._note("migrate", "running")
        if state.verdict != "behind":
            msg = (
                f"the restored schema is {state.verdict} of this build ({state.describe()}): "
                "resume with the build that migrated it"
            )
            raise StepFailed(msg)
        self.say(
            f"{self.hospital.slug}: the restored schema is behind this build "
            f"({state.describe()}): migrated again"
        )
        details = self._migrate()
        again = [*self.op.result.get("remigrated", []), timezone.now().isoformat()]
        self._result(remigrated=again)
        self._note("migrate", "done", **details, remigrated=len(again))

    # --- the package and the pre-checks ------------------------------------------------------

    def _read_package(self, want_sha=""):
        o = self.options
        if o.package_dir is None:
            msg = "--package: the checked package from the quarantine"
            raise Refused(msg)
        try:
            doc = package.read(o.package_dir)
        except package.PackageError as exc:
            msg = f"the package: {exc}"
            raise Refused(msg) from None
        if want_sha and doc["package_sha256"] != want_sha:
            msg = (
                "the package is not the one the quarantine wrote (its package.json does not "
                "hash to what the quarantine printed)"
            )
            raise Refused(msg)
        if doc["schema"] != self.hospital.schema_name:
            # Never under a side name (TENANCY §7.5 correction 1): the trigger functions name
            # their schema, so a copy under another name would write into the live trail
            msg = (
                f"the package is of schema {doc['schema']}, not {self.hospital.slug}'s "
                f"{self.hospital.schema_name}: a restore goes under the real name only"
            )
            raise Refused(msg)
        return doc

    def precheck(self):  # noqa: PLR0912, PLR0915 (one flat list of refusals)
        """Every refusal before anything is touched. Returns the report (recorded)."""
        h, doc, o = self.hospital, self.doc, self.options
        found = {}
        if o.drill and not (
            getattr(settings, "IHR_RESTORE_DRILL", False) and is_drill_database()
        ):
            msg = (
                "--drill runs only on the restore-drill copy (cluster_name "
                f"{DRILL_CLUSTER}) with the drill settings (ihr_platform.settings.drill)"
            )
            raise Refused(msg)
        if not o.drill and (
            getattr(settings, "IHR_RESTORE_DRILL", False) or is_drill_database()
        ):
            msg = "this is the restore-drill copy: only --drill restores here"
            raise Refused(msg)
        if h.status not in RESTORE_STATUSES:
            msg = f"{h.slug} is {h.status}: only active or suspended hospitals are restored"
            raise Refused(msg)
        report = doc.get("report") or {}
        if report.get("quarantine") != "passed" or report.get("problems"):
            msg = (
                "the package did not pass the quarantine (deploy/restore/quarantine.py)"
            )
            raise Refused(msg)
        source = doc.get("source") or {}
        dump_at = source.get("dump_at")
        try:
            when = datetime.fromisoformat(dump_at)
        except (TypeError, ValueError):
            msg = "the package does not say when its dump was taken"
            raise Refused(msg) from None
        if when.tzinfo is None or when > timezone.now():
            msg = "the dump's time has no offset, or is in the future"
            raise Refused(msg)
        found["dump_at"] = when.isoformat()
        found["source"] = {k: source[k] for k in sorted(source) if k != "manifest"}
        holder = release.restore_holder(h)
        if holder:
            msg = f"{h.slug} is held for restore {holder}: --resume or --abort it"
            raise Refused(msg)
        # (one transaction: package.shape reads the catalogue with an empty search_path)
        with transaction.atomic(), connection.cursor() as cursor:
            if not _schema_exists(cursor, h.schema_name):
                msg = f"{h.schema_name} is not there (a lost schema is a cut, never restored over)"
                raise Refused(msg)
            cursor.execute(
                "SELECT to_regclass(%s) IS NOT NULL", [f"public.{ERASURE_LEDGER}"]
            )
            if cursor.fetchone()[0]:
                msg = (
                    "an erasure ledger exists (P4-T1): this build cannot replay erasures after "
                    "the restore point, so it refuses every restore (DPDP)"
                )
                raise Refused(msg)
            # The transaction-id rule (TENANCY §7.5 correction 4)
            audit = doc["facts"]["audit"]
            highest = max(
                int(audit["seal_upper_txid"] or 0), int(audit["max_entry_txid"] or 0)
            )
            now_xid = int(_one(cursor, "SELECT pg_current_xact_id()::text"))
            if highest >= now_xid:
                msg = (
                    f"the dump holds transaction ids up to {highest}, beyond this cluster's "
                    f"{now_xid}: a dump of another cluster needs a rebase seal (not built)"
                )
                raise Refused(msg)
            found["txid"] = {"dump_highest": highest, "cluster_now": now_xid}
            # The restored chain must be a prefix of the live one
            seq, digest = int(audit["seal_seq"] or 0), audit["seal_hash"] or ""
            if seq:
                live = _one(
                    cursor,
                    sql.SQL("SELECT encode(hash, 'hex') FROM {} WHERE seq = %s").format(
                        sql.Identifier(h.schema_name, "ihr_audit_seal")
                    ),
                    [seq],
                )
                if live != digest:
                    msg = (
                        f"the dump's audit head (seal {seq}) is not the live chain's seal {seq}: "
                        "the dump is not of this hospital's history (or the chain was rewritten)"
                    )
                    raise Refused(msg)
            found["restored_to"] = [seq, digest or bytes(32).hex()]
            # Our functions, by definition hash: the package's against this build's (installed
            # in the live schema). Never loaded either way; a difference is reported
            live_functions = package.functions(cursor, h.schema_name)
            found["functions_differ"] = sorted(
                name
                for name, md5 in doc.get("functions", {}).items()
                if live_functions.get(name) != md5
            )
            found["shape"] = self._shape_reference(cursor, doc)
            live_tables = set(package.tables(cursor.cursor, h.schema_name))
            missing = sorted(
                f"{item['table']}.{column}"
                for item in doc["tables"]
                if item["table"] in MERGE_COLUMNS and item["table"] in live_tables
                for column in MERGE_COLUMNS[item["table"]]
                if column not in item["columns"]
            )
            if missing:
                msg = (
                    "the dump's build is older than the strict merge of security state can "
                    f"take: {', '.join(missing[:NAMED])} not in its tables (restore a newer "
                    "dump; nothing was changed)"
                )
                raise Refused(msg)
            # ABDM is not built: refuse if anything of it exists
            cursor.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s AND c.relname LIKE 'abdm%%'",
                [h.schema_name],
            )
            abdm = cursor.fetchone()[0]
            if (
                abdm
                or any(t["table"].startswith("abdm") for t in doc["tables"])
                or any("abdm" in app for app in settings.INSTALLED_APPS)
            ):
                msg = "ABDM data or routes exist: a restore cannot reconcile them yet"
                raise Refused(msg)
            # Locks: one transaction holds a lock on every relation of both schemas
            relations = _one(
                cursor,
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s",
                [h.schema_name],
            )
            capacity = _one(
                cursor,
                "SELECT current_setting('max_locks_per_transaction')::int * "
                "(current_setting('max_connections')::int + "
                "current_setting('max_prepared_transactions')::int)",
            )
            # Measured (P1-T22b proof): about 3.5 locks per relation of the loaded schema. Past
            # the lock table the load fails cleanly ("out of shared memory") and rolls back, so
            # this warns; P1-T23 sets max_locks_per_transaction from these numbers
            wanted = 4 * relations
            found["locks"] = {
                "estimate": wanted,
                "lock_table": capacity,
                "relations": relations,
            }
            if wanted > capacity:
                self.say(
                    f"warning: the load's one transaction takes about {wanted} locks and the "
                    f"server's lock table is sized for {capacity}; if it fails with 'out of "
                    "shared memory', raise max_locks_per_transaction and --resume"
                )
        # Not ahead of this build's code
        facts = release._facts()  # noqa: SLF001 (the release gate's own known names)
        labels = release.tenant_labels()
        unknown = sorted(
            f"{a}.{n}"
            for a, n in doc["facts"]["migrations"]
            if a in labels and (a, n) not in facts.known
        )
        if unknown:
            msg = (
                f"the dump is ahead of this build: {len(unknown)} migration(s) it does not know "
                f"({', '.join(unknown[:3])}); restore with the build that made it"
            )
            raise Refused(msg)
        if not doc["facts"]["migrations"]:
            msg = "the dump has no migrations table: not a hospital schema"
            raise Refused(msg)
        need = sum(t["bytes"] for t in doc["tables"])
        found["package_bytes"] = need
        if o.free_bytes is not None:
            want = 2 * need + (512 << 20)
            if o.free_bytes < want:
                msg = f"not enough disk: {o.free_bytes} bytes free, about {want} needed"
                raise Refused(msg)
        if o.drill:
            found["others_before"] = self._others()
        self.say(
            f"{h.slug}: the package of {found['dump_at']} ({len(doc['tables'])} tables, {need} "
            f"bytes) restores seal {found['restored_to'][0]}; "
            + (
                f"functions differ from this build's: {', '.join(found['functions_differ'])}"
                if found["functions_differ"]
                else "functions as this build's"
            )
        )
        return found

    def _shape_reference(self, cursor, doc):
        """The package's tables, columns, defaults, constraints, indexes and sequences against
        a reference the dump did not write (review S1): when the dump's migrations are the
        live schema's, its shape must be the live schema's exactly. Otherwise (an older dump)
        there is no reference here: its expressions are inspected (package.expression_problems,
        in the quarantine and again before the load evaluates any) and the audit tables'
        defaults are this build's (ihr_audit.install.ensure_defaults)."""
        cursor.execute(
            sql.SQL("SELECT app, name FROM {}").format(
                sql.Identifier(self.schema, "django_migrations")
            )
        )
        live = sorted([a, n] for a, n in cursor.fetchall())
        if live != sorted([a, n] for a, n in doc["facts"]["migrations"]):
            return "not compared (the dump's migrations are not the live schema's)"
        live_shape, detail = package.shape(cursor.cursor, self.schema)
        if live_shape == doc.get("shape"):
            return "the live schema's"
        mine, theirs = package.shape_tables(detail), doc.get("shape_tables") or {}
        differ = sorted(
            k for k in set(mine) | set(theirs) if mine.get(k) != theirs.get(k)
        )
        msg = (
            "the package's tables are not the live schema's, though their migrations are the "
            f"same: {', '.join(differ[:NAMED]) or 'its shape'}"
            f"{f' and {len(differ) - NAMED} more' if len(differ) > NAMED else ''} (a dump is untrusted "
            "SQL: nothing but this build's migrations may define a hospital's tables)"
        )
        raise Refused(msg)

    def _others(self):
        """Every schema but this hospital's, and public (but this hospital's rows): hashed
        (the drill: before and after). On the drill copy nothing else runs, so the other
        hospitals' rows are stood for by their tables' write counters, not read (review C7:
        the whole fleet twice a month); public's rows are read. Where the server does not count
        writes, every row is read."""
        with (
            connection.cursor() as cursor
        ):  # (outside a transaction: the flush needs idle)
            counted = package.flush_write_counters(cursor.cursor)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname ~ '^h_[a-z0-9]{10}$' "
                "AND nspname <> %s",
                [self.schema],
            )
            schemas = [r[0] for r in cursor.fetchall()]
            return package.fingerprint(
                cursor.cursor,
                schemas,
                (self.hospital.pk, self.hospital.uuid),
                rows="writes" if counted else True,
            )

    def _others_changed(self, cursor, before):
        """([names], how many compared): the schemas (and public) whose catalogue changed
        since ``before`` (package.catalogue_versions), leaving out what this load cannot have
        done, so that another hospital's work never stops it: a hospital schema that appeared
        meanwhile (one being onboarded) and one held by another restore (its own load replaces
        it). Every statement of this load names this schema only; a schema that went away, or
        changed, and is not another restore's, is named."""
        after = package.catalogue_versions(cursor.cursor, self.schema)
        restoring = set(
            Hospital.objects.filter(schema_state__restore_operation__isnull=False)
            .exclude(pk=self.hospital.pk)
            .values_list("schema_name", flat=True)
        )
        differ = sorted(
            name
            for name in before
            if name not in restoring and before[name] != after.get(name)
        )
        return differ, len(after)

    # --- steps --------------------------------------------------------------------------------

    def _precheck(self):
        return {}

    def _hold(self):
        try:
            release.restore_hold(self.hospital, self.op.pk)
        except release.RestoreHeldError as exc:
            raise StepFailed(str(exc)) from None
        since = (
            HospitalSchemaState.objects.filter(pk=self.hospital.pk)
            .values_list("restore_since", flat=True)
            .first()
        )
        self._result(held_since=since.isoformat() if since else None)
        return {}

    def _drain(self):
        """Until no app transaction that began before the hold is open (the gate refuses new
        requests from the hold on), and no background job of this hospital that began before
        it is still running (review C4: a Celery task or a staff-worker job runs between
        transactions, so it holds the hospital's job lock, shared, for its whole run:
        release.job). Long transactions are reported; a job still running when the wait ends
        stops the restore here (the hospital stays held: --resume once it has finished)."""
        since = self.op.result.get("held_since")
        waited, left = 0, 0
        while waited <= self.options.drain_seconds:
            with connection.cursor() as cursor:
                left = _one(
                    cursor,
                    # The app's role (with the roles off, the one login there is)
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() AND usename IN ('ihr_app', current_user) "
                    "AND xact_start < %s::timestamptz",
                    [since],
                )
            if not left:
                break
            time.sleep(2)
            waited += 2
        return {"waited": waited, "still_open": left, "jobs": self._wait_for_jobs()}

    def _wait_for_jobs(self):
        """The hospital's job lock taken once, exclusively, and let go: every job that took it
        before the hold has ended. A job that starts now sees the hold and defers."""
        from django.db import OperationalError

        key = release.job_key(self.schema)
        began = time.monotonic()
        seconds = max(1, self.options.drain_seconds)
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(f"SET LOCAL lock_timeout = '{int(seconds)}s'")
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])
        except OperationalError as exc:
            if getattr(exc.__cause__, "sqlstate", "") != "55P03":
                raise
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT a.pid, coalesce(nullif(a.application_name, ''), a.usename), "
                    "coalesce(a.state, '') FROM pg_locks l JOIN pg_stat_activity a "
                    "ON a.pid = l.pid WHERE l.locktype = 'advisory' AND l.granted "
                    "AND l.classid = %s AND l.objid = %s AND l.objsubid = 1 ORDER BY 1",
                    [(key >> 32) & 0xFFFFFFFF, key & 0xFFFFFFFF],
                )
                running = [f"pid {pid} ({who}, {state})" for pid, who, state in cursor]
            msg = (
                f"background jobs of {self.hospital.slug} still running after {seconds} s: "
                f"{', '.join(running[:5]) or 'gone since'}; --resume once they have finished"
            )
            raise StepFailed(msg) from None
        return round(time.monotonic() - began, 1)

    def _seal(self):
        from ihr_audit import anchor
        from ihr_audit.seal import seal_once

        sealed = 0
        with schema_context(self.schema):
            for _ in range(50):
                n = seal_once(self.schema)
                sealed += n
                if not n:
                    break
        written = ""
        if self.options.drill:
            written = "dry run (drill): no fingerprint written"
        else:
            try:
                written = anchor.write()
            except anchor.NotConfiguredError as exc:
                if not settings.DEBUG:
                    msg = f"the fingerprint could not be written: {exc}"
                    raise StepFailed(msg) from None
                written = f"not configured here ({exc})"
        return {"sealed": sealed, "fingerprint": written}

    def _restore(self):
        """Park and load in ONE transaction (crash-safe by construction); the database's own
        state says what happened when it is resumed. Everything that touches only this
        hospital's two schemas is done in it, before anything outside them can see the
        restored copy (reviews S5, S6, S7, C3)."""
        schema = self.schema
        parked = self.parked or parked_name(schema, timezone.now().astimezone(UTC))
        if not self.parked:
            self._result(parked=parked)
        with connection.cursor() as cursor:
            real, park = _schema_exists(cursor, schema), _schema_exists(cursor, parked)
        if real and park:
            return {"note": "already committed (both schemas there)"}
        if not real and not park:
            msg = f"neither {schema} nor {parked} exists: the hospital's schema is lost"
            raise StepFailed(msg)
        loaded, final = {}, 0
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL lock_timeout = '{LOCK_WAIT}'")
            if real:
                # No writer of this hospital is left once this lock is ours (every write writes
                # an audit entry)
                cursor.execute(
                    sql.SQL("LOCK TABLE {}, {} IN ACCESS EXCLUSIVE MODE").format(
                        sql.Identifier(schema, "ihr_audit_entry"),
                        sql.Identifier(schema, "ihr_audit_seal"),
                    )
                )
            # Every other schema's and public's catalogue, before (review S2): compared again
            # just before this transaction commits (the versions of their catalogue rows:
            # cheap, whatever the fleet's size)
            others = package.catalogue_versions(cursor.cursor, schema)
            if real:
                # The whole live trail sealed into the live chain now (review S5): by the
                # restore itself, so neither the sealer's lock nor a long transaction anywhere
                # can leave entries that would exist only in the parked schema
                final = self._final_seal(cursor, schema)
                cursor.execute(
                    sql.SQL(
                        "SELECT count(*) FROM {} WHERE txid >= coalesce((SELECT "
                        "max(upper_txid) FROM {}), '0'::xid8)"
                    ).format(
                        sql.Identifier(schema, "ihr_audit_entry"),
                        sql.Identifier(schema, "ihr_audit_seal"),
                    )
                )
                if unsealed := cursor.fetchone()[0]:
                    msg = (
                        f"{unsealed} entries of the live trail are still unsealed after the "
                        "final seal: nothing was parked"
                    )
                    raise StepFailed(msg)
                cursor.execute(
                    sql.SQL("ALTER SCHEMA {} RENAME TO {}").format(
                        sql.Identifier(schema), sql.Identifier(parked)
                    )
                )
            self._close_parked(cursor, parked)
            began = time.monotonic()
            loaded = package.load(
                connection.connection, self.options.package_dir, schema, self.doc
            )
            loaded["load_seconds"] = round(time.monotonic() - began, 1)
            loaded["chain"] = self._restored_chain(cursor)
            # No id the parked copy used is given again, from the first write on (review
            # S6): before the trail's triggers, migrate or the merge write anything
            loaded["sequences"] = self._sequences(cursor)
            self._install_trail(cursor, schema)
            access = self._merge(cursor)
            differ, compared = self._others_changed(cursor, others)
            if differ:
                msg = (
                    f"the load changed other schemas or public: {', '.join(differ[:5])}"
                )
                raise StepFailed(msg)
            loaded["others_unchanged"] = compared
            cursor.execute(
                sql.SQL("SELECT max(seq) FROM {}").format(
                    sql.Identifier(parked, "ihr_audit_seal")
                )
            )
            head = cursor.fetchone()[0]
        self._result(
            live_head=head or 0, parked_unsealed=0, final_sealed=final, access=access
        )
        return {**loaded, "parked": parked}

    def _final_seal(self, cursor, schema):
        """Seal every entry of the live trail (ihr_audit.seal.final_sql: the sealing function's
        own windows and hashes, up to every transaction that has ended, without the sealer's
        lock: the trail is locked ACCESS EXCLUSIVE by this transaction). Returns the entries
        sealed."""
        from ihr_audit import seal

        cursor.execute(seal.final_sql(schema))
        cursor.execute(
            "SELECT pg_temp.ihr_audit_seal_final(%s, %s::smallint)",
            [seal.BATCH, seal.FORMAT],
        )
        sealed = cursor.fetchone()[0]
        cursor.execute("DROP FUNCTION pg_temp.ihr_audit_seal_final(integer, smallint)")
        return sealed

    def _restored_chain(self, cursor):
        """Before the commit (review S7): the loaded trail's seals recomputed from its
        entries, and its entries past its last seal (all of them for a dump with no seal) the
        parked copy's own entries of the same id. A dump whose history does not hold refuses
        here, while the restore can still be aborted."""
        from ihr_audit.seal import verify

        problems, summary = verify(self.schema)
        chain = [p for p in problems if p.startswith("seal ")]
        if chain:
            msg = "the restored trail does not verify: " + "; ".join(chain)[:300]
            raise StepFailed(msg)
        cursor.execute(
            "SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'ihr_audit_entry' "
            "AND n.nspname = %s AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum",
            [self.schema],
        )
        restored = [r[0] for r in cursor.fetchall()]
        cursor.execute(
            "SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relname = 'ihr_audit_entry' "
            "AND n.nspname = %s AND a.attnum > 0 AND NOT a.attisdropped",
            [self.parked],
        )
        parked_columns = {r[0] for r in cursor.fetchall()}
        columns = [c for c in restored if c in parked_columns]
        same = sql.SQL(" AND ").join(
            sql.SQL("p.{c} IS NOT DISTINCT FROM r.{c}").format(c=sql.Identifier(c))
            for c in columns
        )
        cursor.execute(
            sql.SQL(
                "SELECT count(*) FROM {r} r WHERE r.txid >= coalesce((SELECT max(upper_txid) "
                "FROM {rs}), '0'::xid8) AND NOT EXISTS (SELECT 1 FROM {p} p WHERE p.id = r.id "
                "AND {same})"
            ).format(
                r=sql.Identifier(self.schema, "ihr_audit_entry"),
                rs=sql.Identifier(self.schema, "ihr_audit_seal"),
                p=sql.Identifier(self.parked, "ihr_audit_entry"),
                same=same,
            )
        )
        if foreign := cursor.fetchone()[0]:
            msg = (
                f"{foreign} entries of the dump past its last seal are not the live trail's "
                "own (the dump is not of this hospital's history)"
            )
            raise StepFailed(msg)
        return {
            "seals": summary["seals"],
            "unsealed_compared": summary["unsealed_entries"],
        }

    def _close_parked(self, cursor, parked):
        """Nobody but the owner reaches the parked schema: its trigger functions still name the
        real schema, so a write there would land in the restored trail (side names, §7.5)."""
        from ihr_platform import db_roles

        for role in (db_roles.APP, db_roles.WATCH, "PUBLIC"):
            cursor.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s OR %s = 'PUBLIC'",
                [role, role],
            )
            if cursor.fetchone() is None:
                continue
            who = sql.SQL("PUBLIC") if role == "PUBLIC" else sql.Identifier(role)
            cursor.execute(
                sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(
                    sql.Identifier(parked), who
                )
            )

    def _install_trail(self, cursor, schema):
        """The trail's guard, column defaults, triggers and sealing from this build's code,
        in the load's own transaction: the restored schema is never without them, and no
        default of the dump's runs on an audit entry (review S1)."""
        from ihr_audit import install, seal

        install.ensure_append_only(cursor, schema)
        install.ensure_defaults(cursor, schema)
        install.ensure_triggers(connection, schema)
        seal.ensure_sealing(connection, schema)

    def _migrate(self):
        from ihr_platform import fleet

        result = fleet.migrate_one(self.schema, self.hospital.slug)
        if result.get("outcome") != "done":
            raise StepFailed(
                f"migrate: {result.get('outcome')} {result.get('error', '')}"[:300]
            )
        state = release.record(self.hospital)
        if not state.current:
            msg = f"after migrate the schema is {state.verdict}: {state.describe()}"
            raise StepFailed(msg)
        try:
            fleet.roles_check(self.hospital)
            fleet.roles_sync(self.hospital)
        except Exception as exc:
            msg = f"roles: {release.safe_error(exc)}"
            raise StepFailed(msg) from None
        finally:
            connection.set_schema_to_public()
        packs = self._packs()
        return {"migrate": result, "packs": packs}

    def _packs(self):
        from ihr_audit import context as audit
        from ihr_refdata import engine

        actor = f"packs:restore-{self.options.operator}"[:150]
        with audit.job({"job": "packs", "via": "restore"}, actor):
            releases = engine.prepare(actor, record=True)
            results = engine.apply_packs(
                self.hospital, operator=actor, releases=releases
            )
        connection.set_schema_to_public()
        return [f"{r.pack} v{r.version} {r.result}" for r in results]

    def _reconcile(self):
        from ihr_audit import context as audit

        result = {}
        with audit.job(
            {"job": "restore", "operation": str(self.op.pk)},
            f"restore:{self.options.operator}",
        ):
            with transaction.atomic(), connection.cursor() as cursor:
                # Advanced in the restore step already: a re-check (0 advanced)
                result["sequences"] = self._sequences(cursor)
                result["sessions_invalidated"] = self._sessions(cursor)
                result["profile_differs"] = self._profile(cursor)
            result["patients_rebuilt"] = self._patients()
            result["public"] = self._public_rows()
        result["redis"] = self._redis()
        access = dict(self.op.result.get("access", {}))
        access["patients"] = len(access.get("patients", []))
        self._result(reconcile=result)
        self.say(
            f"{self.hospital.slug}: access difference {json.dumps(access, sort_keys=True)}"
        )
        if kept := result["public"].get("kept_queued"):
            self.say(
                f"{self.hospital.slug}: {len(kept)} sign-in reset(s) or invitation "
                "withdrawal(s) asked for after the dump stay queued (run once it is served): "
                + "; ".join(kept[:5])
            )
        if result["profile_differs"]:
            self.say(
                f"{self.hospital.slug}: the profile differs from the parked copy in "
                f"{', '.join(result['profile_differs'])}: check it with ihr_hospital_profile"
            )
        return {}

    def _sessions(self, cursor):
        """Every staff session ends: kept in Postgres, so no Redis state can undo it (after
        migrate: an older dump may not have the column yet)."""
        if "ihr_identity_staffidentity" not in set(
            package.tables(cursor.cursor, self.schema)
        ):
            return 0
        cursor.execute(
            self._q(
                "UPDATE {r}.ihr_identity_staffidentity SET sessions_valid_after = clock_timestamp()"
            )
        )
        return cursor.rowcount

    def _patients(self):
        """Patients whose grants or shares the merge took back (review S4): their access
        caches (users_cache, organization_cache: the patient list filters on them) built
        again by CARE's own save, after migrate (this build's model)."""
        ids = sorted(set(self.op.result.get("access", {}).get("patients", [])))
        if not ids:
            return 0
        from care.emr.models.patient import Patient

        with schema_context(self.schema):
            for patient in Patient.objects.filter(id__in=ids):
                patient.save()
        return len(ids)

    def _sequences(self, cursor):
        """Every sequence at least as far as the parked copy's: no id is used twice."""
        restored = package.sequences(cursor.cursor, self.schema)
        parked = package.sequences(cursor.cursor, self.parked)
        advanced = []
        for name, (value, called) in sorted(restored.items()):
            if name not in parked:
                continue
            p_value, p_called = parked[name]
            if (p_value, p_called) > (value, called):
                cursor.execute(
                    "SELECT pg_catalog.setval(%s::regclass, %s, %s)",
                    [f'"{self.schema}"."{name}"', p_value, p_called],
                )
                advanced.append(name)
        return {"advanced": len(advanced), "of": len(restored)}

    def _q(self, text):
        return sql.SQL(text).format(
            r=sql.Identifier(self.schema), p=sql.Identifier(self.parked)
        )

    def _merge(self, cursor):
        """Security state never goes back (TENANCY §7.5 correction 6, After the attack 10), in
        the restore's own transaction, before migrate, roles or packs write anything (review
        C3: the two copies' ids then mean the same rows). Its writes are audited (the trail's
        triggers are in place). Returns the access difference."""
        from ihr_audit import context as audit

        with audit.job(
            {"job": "restore", "operation": str(self.op.pk)},
            f"restore:{self.options.operator}",
        ):
            return self._security(cursor)

    def _columns(self, cursor, schema, table):
        cursor.execute(
            "SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s "
            "AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped",
            [schema, table],
        )
        return {r[0] for r in cursor.fetchall()}

    def _security(self, cursor):  # noqa: PLR0912, PLR0915 (one pass per kind of grant)
        out, has = (
            {},
            set(package.tables(cursor.cursor, self.parked))
            & set(package.tables(cursor.cursor, self.schema)),
        )
        # The columns merged must be in both copies (the precheck refused a dump without
        # them); the optional ones are merged only where both have them
        missing, there = [], {}
        for table in sorted(has & (set(MERGE_COLUMNS) | set(MERGE_IF_THERE))):
            both = self._columns(cursor, self.schema, table) & self._columns(
                cursor, self.parked, table
            )
            missing += [
                f"{table}.{c}" for c in MERGE_COLUMNS.get(table, ()) if c not in both
            ]
            there[table] = both
        if missing:
            msg = (
                "the strict merge needs columns one of the copies does not have: "
                + ", ".join(missing[:NAMED])
            )
            raise StepFailed(msg)
        skipped = [
            f"{table}.{column}"
            for table, columns in MERGE_IF_THERE.items()
            if table in has
            for column in columns
            if column not in there[table]
        ]
        if skipped:
            out["skipped"] = skipped

        def run(name, text):
            cursor.execute(self._q(text))
            out[name] = cursor.rowcount
            return out[name]

        if "users_user" in has:
            run(
                "users_deactivated",
                "UPDATE {r}.users_user r SET is_active = false WHERE r.is_active AND NOT EXISTS "
                "(SELECT 1 FROM {p}.users_user p WHERE p.id = r.id AND p.is_active AND NOT p.deleted)",
            )
            run(
                "users_deleted",
                "UPDATE {r}.users_user r SET deleted = true FROM {p}.users_user p "
                "WHERE p.id = r.id AND p.deleted AND NOT r.deleted",
            )
        if {"ihr_identity_staffidentity", "users_user"} <= has:
            run(
                "unlinked_deactivated",
                "UPDATE {r}.users_user r SET is_active = false WHERE r.is_active AND EXISTS "
                "(SELECT 1 FROM {r}.ihr_identity_staffidentity i WHERE i.user_id = r.id AND NOT "
                "EXISTS (SELECT 1 FROM {p}.ihr_identity_staffidentity p WHERE p.user_id = i.user_id "
                "AND p.keycloak_sub = i.keycloak_sub))",
            )
        keep = self.options.keep_restored_access
        if keep:
            out["strict_merge"] = f"skipped: --keep-restored-access ({keep[:200]})"
            return out
        if "users_user" in has:
            for flag in ("is_superuser", "is_staff"):
                if flag not in there["users_user"]:
                    continue
                run(
                    f"{flag}_removed",
                    f"UPDATE {{r}}.users_user r SET {flag} = false WHERE r.{flag} AND NOT EXISTS "
                    f"(SELECT 1 FROM {{p}}.users_user p WHERE p.id = r.id AND p.{flag})",
                )
        changed_users, patients = set(), set()
        for table in ASSIGNMENTS:
            if table not in has:
                continue
            patient = (
                ", r.patient_id" if table == "emr_patientuser" else ", NULL::bigint"
            )
            cursor.execute(
                self._q(
                    f"UPDATE {{r}}.{table} r SET deleted = true WHERE NOT r.deleted AND NOT "
                    f"EXISTS (SELECT 1 FROM {{p}}.{table} p WHERE p.id = r.id AND NOT p.deleted) "
                    f"RETURNING r.user_id{patient}"
                )
            )
            gone = cursor.fetchall()
            out[f"{table}_removed"] = len(gone)
            changed_users.update(row[0] for row in gone)
            patients.update(row[1] for row in gone if row[1] is not None)
            if "security_rolepermission" in has:
                cursor.execute(
                    self._q(
                        f"UPDATE {{r}}.{table} r SET role_id = p.role_id FROM {{p}}.{table} p "
                        "WHERE p.id = r.id AND NOT r.deleted AND NOT p.deleted "
                        "AND p.role_id <> r.role_id AND EXISTS ("
                        "  SELECT permission_id FROM {p}.security_rolepermission "
                        "  WHERE role_id = r.role_id AND NOT deleted EXCEPT "
                        "  SELECT permission_id FROM {p}.security_rolepermission "
                        f"  WHERE role_id = p.role_id AND NOT deleted) RETURNING r.user_id{patient}"
                    )
                )
                down = cursor.fetchall()
                out[f"{table}_downgraded"] = len(down)
                changed_users.update(row[0] for row in down)
                patients.update(row[1] for row in down if row[1] is not None)
            # Access granted after the dump is not in the restored copy: shown, not given
            cursor.execute(
                self._q(
                    f"SELECT count(*) FROM {{p}}.{table} p WHERE NOT p.deleted AND NOT EXISTS "
                    f"(SELECT 1 FROM {{r}}.{table} r WHERE r.id = p.id AND NOT r.deleted)"
                )
            )
            out[f"{table}_lost"] = cursor.fetchone()[0]
        for table in SHARES:
            if table not in has:
                continue
            cursor.execute(
                self._q(
                    f"UPDATE {{r}}.{table} r SET deleted = true WHERE NOT r.deleted AND NOT "
                    f"EXISTS (SELECT 1 FROM {{p}}.{table} p WHERE p.id = r.id AND NOT p.deleted) "
                    "RETURNING r.patient_id"
                )
            )
            gone = [row[0] for row in cursor.fetchall()]
            out[f"{table}_removed"] = len(gone)
            patients.update(gone)
            cursor.execute(
                self._q(
                    f"SELECT count(*) FROM {{p}}.{table} p WHERE NOT p.deleted AND NOT EXISTS "
                    f"(SELECT 1 FROM {{r}}.{table} r WHERE r.id = p.id AND NOT r.deleted)"
                )
            )
            out[f"{table}_lost"] = cursor.fetchone()[0]
        if "security_rolepermission" in has:
            run(
                "role_permissions_removed",
                "UPDATE {r}.security_rolepermission r SET deleted = true WHERE NOT r.deleted "
                "AND NOT EXISTS (SELECT 1 FROM {p}.security_rolepermission p WHERE "
                "p.role_id = r.role_id AND p.permission_id = r.permission_id AND NOT p.deleted)",
            )
        if (
            changed_users
            and "users_user" in has
            and "cached_role_orgs" in there["users_user"]
        ):
            cursor.execute(
                self._q(
                    "UPDATE {r}.users_user SET cached_role_orgs = NULL WHERE id = ANY(%s) "
                    "AND cached_role_orgs IS NOT NULL"
                ),
                [sorted(changed_users)],
            )
        # Their access caches are built again after migrate (reconcile, ``_patients``)
        out["patients"] = sorted(patients)
        return out

    def _profile(self, cursor):
        """The hospital profile's fields that differ from the parked copy (names only)."""
        table = "ihr_tenant_settings_hospitalprofile"
        if table not in set(package.tables(cursor.cursor, self.parked)):
            return []
        cursor.execute(
            self._q(
                "SELECT to_jsonb(r) - 'id' - 'updated_on' - 'logo', to_jsonb(p) - 'id' - "
                "'updated_on' - 'logo' FROM {r}."
                + table
                + " r FULL JOIN {p}."
                + table
                + " p ON p.singleton = r.singleton"
            )
        )
        differs = set()
        for row in cursor.fetchall():
            # Django's connection reads jsonb as text
            restored, parked = (
                json.loads(v) if isinstance(v, str) else (v or {}) for v in row
            )
            differs |= {
                k
                for k in set(restored) | set(parked)
                if restored.get(k) != parked.get(k)
            }
        return sorted(differs)

    def _public_rows(self):
        """Invitations and access requests made after the dump, or for users who no longer
        exist, are cancelled (TENANCY §7.5 correction 5). A request that takes access away
        (a sign-in reset after a lost authenticator, an invitation's withdrawal) for a user the
        restored copy still has is kept queued, and named (review S8): the staff worker runs
        it once the hospital is served again."""
        from django.db.models import Q

        dump_at = datetime.fromisoformat(self.op.result["precheck"]["dump_at"])
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("SELECT id FROM {}.users_user").format(
                    sql.Identifier(self.schema)
                )
            )
            users = [r[0] for r in cursor.fetchall()]
        stale = Q(created_at__gt=dump_at) | ~Q(care_user_id__in=users)
        note = f"cancelled by restore {self.op.pk}"
        invitations = (
            StaffInvitation.objects.filter(hospital=self.hospital)
            .exclude(
                status__in=[
                    StaffInvitation.Status.CANCELLED,
                    StaffInvitation.Status.EXPIRED,
                ]
            )
            .filter(stale)
            .update(status=StaffInvitation.Status.CANCELLED, error=note)
        )
        open_requests = StaffAccessRequest.objects.filter(
            hospital=self.hospital,
            status__in=[
                StaffAccessRequest.Status.QUEUED,
                StaffAccessRequest.Status.RUNNING,
            ],
        )
        protective = Q(
            action__in=[
                StaffAccessRequest.Action.RESET_SIGN_IN,
                StaffAccessRequest.Action.CANCEL_INVITATION,
            ],
            care_user_id__in=users,
        )
        kept = [
            f"{r.action} of user {r.care_user_id} (asked by {r.requested_by[:40]})"
            for r in open_requests.filter(stale)
            .filter(protective)
            .order_by("created_at")
        ]
        requests = (
            open_requests.filter(stale)
            .exclude(protective)
            .update(
                status=StaffAccessRequest.Status.FAILED,
                error=note,
                done_at=timezone.now(),
            )
        )
        return {
            "invitations_cancelled": invitations,
            "access_requests_cancelled": requests,
            "kept_queued": kept,
        }

    def _redis(self):
        """Every Redis key of this hospital (sessions, caches) deleted: each starts with the
        schema name (TENANCY_DESIGN §6; the test runner refuses any other key)."""
        if self.options.drill:
            return "dry run (drill): no Redis here"
        try:
            from django_redis import get_redis_connection

            client = get_redis_connection("default")
        except Exception as exc:
            msg = f"Redis is not reachable to delete the hospital's keys: {type(exc).__name__}"
            raise StepFailed(msg) from None
        deleted, batch = 0, []
        for key in client.scan_iter(match=f"{self.schema}:*", count=1000):
            batch.append(key)
            if len(batch) >= PAGE:
                deleted += client.unlink(*batch)
                batch = []
        if batch:
            deleted += client.unlink(*batch)
        return {"deleted": deleted}

    def _keycloak(self):
        """Every active restored staff link must still be a member of the hospital's Keycloak
        organisation; one that is not is switched off. Fail closed: without the check, the
        hospital stays held."""
        if self.options.drill:
            return {"note": "dry run (drill): Keycloak not asked"}
        from django.core.management.base import CommandError

        from ihr_platform.management.commands.ihr_keycloak_sync import keycloak_admin

        try:
            admin = keycloak_admin()
        except CommandError as exc:
            msg = (
                "the Keycloak membership check needs the operator's credential: "
                "scripts/staging.sh runs it with the admin one-off"
            )
            raise NeedsKeycloak(msg) from exc
        org = self.hospital.keycloak_org_id
        if not org:
            msg = f"{self.hospital.slug} has no Keycloak organisation recorded"
            raise StepFailed(msg)
        members, first = set(), 0
        while True:
            page = admin.get(
                f"organizations/{org}/members?first={first}&max={PAGE}&briefRepresentation=true"
            )
            members |= {str(m["id"]) for m in page}
            if len(page) < PAGE:
                break
            first += PAGE
        from ihr_audit import context as audit

        with (
            audit.job(
                {"job": "restore", "operation": str(self.op.pk)},
                f"restore:{self.options.operator}",
            ),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                sql.SQL(
                    "SELECT i.keycloak_sub::text, u.id, u.is_active FROM {}.ihr_identity_staffidentity i "
                    "JOIN {}.users_user u ON u.id = i.user_id"
                ).format(sql.Identifier(self.schema), sql.Identifier(self.schema))
            )
            links = cursor.fetchall()
            gone = [uid for sub, uid, active in links if active and sub not in members]
            if gone:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {}.users_user SET is_active = false WHERE id = ANY(%s)"
                    ).format(sql.Identifier(self.schema)),
                    [gone],
                )
        linked = {sub for sub, _, _ in links}
        unlinked = len(members - linked)
        if unlinked:
            self.say(
                f"{self.hospital.slug}: {unlinked} Keycloak member(s) have no staff link in the "
                "restored copy (added after the dump): remove or re-invite them in the console"
            )
        return {
            "members": len(members),
            "deactivated": len(gone),
            "members_without_link": unlinked,
        }

    def _files(self):
        """Each object under the hospital's prefix, in both file buckets, as it was at the
        dump's time (the buckets keep versions 35 days); newer uploads are kept and counted."""
        if self.options.drill:
            return {"note": "dry run (drill): no object storage here"}
        from ihr_platform.restore import files

        dump_at = datetime.fromisoformat(self.op.result["precheck"]["dump_at"])
        try:
            return files.restore(self.hospital, dump_at)
        except files.FilesError as exc:
            raise StepFailed(str(exc)) from None

    def _audit(self):  # noqa: PLR0912, PLR0915
        """The superseded segment, the restore record (event), a ``restore`` entry, a seal, a
        fingerprint, then the whole check of this hospital must pass."""
        from ihr_audit import anchor, archive, events
        from ihr_audit.seal import seal_once, verify

        seq, digest = self.op.result["precheck"]["restored_to"]
        uuid = str(self.hospital.uuid)
        operator = self.options.operator
        with connection.cursor() as cursor:
            # This operation's record if a run of it got that far, else the next generation
            generation = archive.generation_of(
                cursor, uuid, self.op.pk
            ) or archive.next_generation(cursor, uuid)
        dump = json.dumps(self.op.input.get("source", {}), sort_keys=True)[:200]
        with transaction.atomic(), connection.cursor() as cursor:
            existing = archive.recorded(cursor, uuid, generation)
            if existing is None:
                rows = archive.archive(
                    cursor,
                    hospital=self.hospital,
                    generation=generation,
                    operation=self.op.pk,
                    parked_schema=self.parked,
                    restored_to=(seq, digest),
                    dump=dump,
                    operator=operator,
                )
            else:
                rows = archive.live_rows(
                    cursor, self.parked, existing["from_seq"], existing["to_seq"]
                )
        self._result(generation=generation if rows else None, superseded=len(rows))
        event_key = ""
        if rows:
            # The segment checked here exactly as the watch and ihr_audit_verify will check it
            probe = events.Event(
                "",
                "restore",
                uuid,
                self.hospital.slug,
                timezone.now(),
                timezone.now(),
                operator,
                generation,
                (seq, digest),
            )
            probe.superseded = {
                "from_seq": rows[0].seq,
                "to_seq": rows[-1].seq,
                "head_hash": bytes(rows[-1].hash).hex(),
                "digest": events.segment_digest(rows),
            }
            events.check_restore(probe)
            if probe.problems:
                raise StepFailed(
                    "the superseded segment does not verify: "
                    + "; ".join(probe.problems)[:300]
                )
            if self.options.drill:
                event_key = "dry run (drill): no event written"
            else:
                event_key = self._write_event(generation, (seq, digest), rows)
        data = {
            "operation": str(self.op.pk),
            "generation": generation if rows else 0,
            "restored_to": seq,
            "superseded": [rows[0].seq, rows[-1].seq] if rows else [],
            "parked": self.parked,
            "source": self.op.input.get("source", {}),
            "reason": self.options.reason[:500],
            "event": event_key,
        }
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "SELECT count(*) FROM {} WHERE kind = 'restore' AND data->>'operation' = %s"
                ).format(sql.Identifier(self.schema, "ihr_audit_entry")),
                [str(self.op.pk)],
            )
            if not cursor.fetchone()[0]:
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {} (kind, actor, context, tbl, op, row_pk, patients, data) "
                        "VALUES ('restore', %s, %s::jsonb, '', '', NULL, '{{}}', %s::jsonb)"
                    ).format(sql.Identifier(self.schema, "ihr_audit_entry")),
                    [
                        f"restore:{operator}",
                        json.dumps({"via": "restore"}),
                        json.dumps(data, default=str),
                    ],
                )
        with schema_context(self.schema):
            for _ in range(50):
                if not seal_once(self.schema):
                    break
        if self.options.drill:
            with schema_context(self.schema):
                from ihr_audit.install import coverage

                problems = coverage(connection, self.schema)
                chain, summary = verify(self.schema)
            if problems or chain:
                raise StepFailed(
                    "the restored trail does not verify: "
                    + "; ".join(problems + chain)[:300]
                )
            return {
                "event": event_key,
                "verify": "chain and triggers (drill: no bucket)",
                "head": summary["head"][:16],
            }
        try:
            fingerprint = anchor.write()
        except anchor.NotConfiguredError as exc:
            if not settings.DEBUG:
                msg = f"the fingerprint could not be written: {exc}"
                raise StepFailed(msg) from None
            fingerprint = f"not configured here ({exc})"
        from django.core.management import call_command

        out = _Collect()
        try:
            call_command(
                "ihr_audit_verify", slug=self.hospital.slug, stdout=out, stderr=out
            )
        except SystemExit as exc:
            if exc.code:
                raise StepFailed(
                    "ihr_audit_verify --slug failed: " + out.text()[-400:]
                ) from None
        return {"event": event_key, "fingerprint": fingerprint, "verify": "passed"}

    def _write_event(self, generation, restored_to, rows):
        """The restore event, once: a generation already in the bucket is not written again."""
        from ihr_audit import anchor, events

        try:
            found = events.read()
        except anchor.NotConfiguredError:
            if settings.DEBUG:
                return "not configured here"
            msg = "the fingerprint bucket's reader key is not set (anchor-read.env)"
            raise StepFailed(msg) from None
        for event in found.restores.get(str(self.hospital.uuid), []):
            if event.generation == generation:
                return event.key
        try:
            return events.write_restore(
                self.hospital, generation, restored_to, rows, self.options.operator
            )
        except anchor.NotConfiguredError:
            msg = "the fingerprint bucket's writer key is not set (anchor-write.env)"
            raise StepFailed(msg) from None

    def _restart(self):
        """Nothing to restart by default (review C5): no app process keeps state that comes
        from a hospital's rows (its uuid and slug live in public and do not change; its
        settings and role caches were in Redis, deleted in reconcile). One process state can
        outlive a restore: a schema the process gated for rights beyond the model
        (db_roles.gated_schemas, DatabaseSchemaGated); --restart then restarts the app
        containers (staging.sh), all at once."""
        o = self.options
        if o.drill or o.no_restart or o.restarted or not o.restart:
            return {
                "note": "drill: nothing to restart"
                if o.drill
                else "restarted"
                if o.restarted
                else "nothing to restart"
            }
        raise NeedsRestart

    def _smoke(self):
        from ihr_audit.install import coverage

        from ihr_platform import db_roles

        self._current_build()  # (run() did; again here, just before the hold goes)
        state = release.record(self.hospital)
        if not state.current:
            msg = f"the schema is {state.verdict}: {state.describe()}"
            raise StepFailed(msg)
        with schema_context(self.schema):
            problems = coverage(connection, self.schema)
        findings = [
            f.text
            for f in db_roles.report(connection, [self.schema], database=False)
            if f.kind in ("problem", "missing", "extra")
        ]
        if problems or findings:
            raise StepFailed("smoke: " + "; ".join(problems + findings)[:400])
        result = {}
        if self.options.drill:
            before = self.op.result["precheck"].get("others_before")
            after = self._others()
            differ = sorted(
                k
                for k in set(before or {}) | set(after)
                if (before or {}).get(k) != after.get(k)
            )
            if differ:
                msg = f"the drill changed other schemas or public: {', '.join(differ)}"
                raise StepFailed(msg)
            result["others_unchanged"] = len(after)
        release.lift_restore_hold(self.hospital, self.op.pk)
        result["served"] = release.servable(Hospital.objects.get(pk=self.hospital.pk))
        return result

    # --- abort ---------------------------------------------------------------------------------

    def abort(self, op_id):
        """Un-park and lift the hold, at any step before reconcile (nothing outside the
        hospital's schema has changed yet). The restored copy is dropped."""
        self.op = TenantOperation.objects.filter(
            pk=op_id, kind=TenantOperation.Kind.RESTORE
        ).first()
        if self.op is None:
            msg = f"no restore operation {op_id}"
            raise Refused(msg)
        if self.op.status in (
            TenantOperation.Status.SUCCEEDED,
            TenantOperation.Status.ROLLED_BACK,
        ):
            msg = f"restore {op_id} is {self.op.status}"
            raise Refused(msg)
        self.hospital = self.op.hospital
        started = {
            s["name"]
            for s in self.op.steps
            if s.get("status") in ("done", "running", "failed", "waiting")
        } & set(STEPS)  # a record of something that is not a step never blocks it (C2)
        if started - ABORTABLE:
            msg = (
                f"restore {op_id} has passed the point of no return ({', '.join(sorted(started - ABORTABLE))}): "
                "--resume it"
            )
            raise Refused(msg)
        try:
            lock = runner.exclusive_for(self.hospital.slug, self.op.pk)
            lock.__enter__()
        except runner.ProvisioningError as exc:
            msg = f"{self.hospital.slug} is busy ({exc}): --abort again when it is free"
            raise Refused(msg) from None
        try:
            schema, parked = self.schema, self.parked
            with transaction.atomic(), connection.cursor() as cursor:
                real = _schema_exists(cursor, schema)
                park = bool(parked) and _schema_exists(cursor, parked)
                if park:
                    if real:
                        cursor.execute(
                            sql.SQL("DROP SCHEMA {} CASCADE").format(
                                sql.Identifier(schema)
                            )
                        )
                    cursor.execute(
                        sql.SQL("ALTER SCHEMA {} RENAME TO {}").format(
                            sql.Identifier(parked), sql.Identifier(schema)
                        )
                    )
            from ihr_platform import db_roles

            db_roles.ensure_grants(connection, [schema], database=False)
            release.record(self.hospital)
            release.lift_restore_hold(self.hospital, self.op.pk)
        finally:
            lock.__exit__(None, None, None)
        self.op.status = TenantOperation.Status.ROLLED_BACK
        self.op.finished_at = timezone.now()
        self.op.result = {**self.op.result, "aborted": True}
        self.op.save(update_fields=["status", "finished_at", "result"])
        self.alert(log_line(self.hospital.slug, "aborted"))
        return 0


class _Collect:
    def __init__(self):
        self.parts = []

    def write(self, text):
        self.parts.append(str(text))

    def flush(self):
        pass

    def text(self):
        return "".join(self.parts)


# --- parked schemas -----------------------------------------------------------------------------


def parked_schemas(now=None):
    """[(name, schema, parked at, days)] of every parked schema, oldest first."""
    now = now or timezone.now()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'ihr\\_parked\\_%%'"
        )
        names = [r[0] for r in cursor.fetchall()]
    found = []
    for name in names:
        m = PARKED_RE.match(name)
        if not m:
            found.append((name, "", None, None))
            continue
        at = datetime.strptime(m.group(2), "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        found.append((name, m.group(1), at, (now - at) / timedelta(days=1)))
    return sorted(found, key=lambda f: (f[2] is None, f[2] or now))


def expire_parked(days=PARKED_DAYS, dry_run=False, now=None):
    """Drop every parked schema older than ``days`` (owner-run: scripts/staging.sh
    hospital-restore-expire, daily). A parked schema of a restore that is still open is kept.
    Returns [(name, dropped?)]."""
    if days < PARKED_DAYS:
        msg = f"parked schemas are kept {PARKED_DAYS} days at least"
        raise Refused(msg)
    open_parks = {
        (op.result or {}).get("parked")
        for op in TenantOperation.objects.filter(
            kind=TenantOperation.Kind.RESTORE,
            status__in=["pending", "running", "failed"],
        )
    }
    done = []
    for name, _schema, _at, age in parked_schemas(now):
        if age is None or age < days or name in open_parks:
            continue
        if not dry_run:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '10s'")
                cursor.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name))
                )
        done.append((name, not dry_run))
    return done


def overdue_lines(now=None):
    """Fixed lines for parked schemas kept past their days (beat, deploy/logs/alerts/restore.yml)."""
    return [
        f"ihr-restore: parked={name} outcome=overdue"
        for name, _s, _at, age in parked_schemas(now)
        if age is not None and age >= PARKED_DAYS + 1 and PARKED_RE.match(name)
    ]
