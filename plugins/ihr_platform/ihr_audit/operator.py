"""Operator access in each hospital's own trail (P1-T27b; Q-K136, docs/reviews/p1-t27/attack.md
"Orchestrator decision" -> "Per-hospital entries").

Two things, both for a process started by ``scripts/staging.sh`` inside a recorded operator
session (its ``op_need`` passes ``IHR_OPERATOR_SESSION``, ``IHR_OPERATOR``, ``IHR_OPERATOR_SCOPE``,
``IHR_OPERATOR_REASON`` and ``IHR_OPERATOR_TIER`` into every one-off):

- ``install()`` (``IhrAuditConfig.ready``): every write the one-off makes is attributed
  ``operator:<name>`` with the session and tier in its context (``context.adopt``, no database
  access; a bad value stops the process before it reaches the database). A write may land in
  any hospital's trail (there is no scope guard, Q-K136), so its context names no hospital and
  carries no free-text reason (review A5): its scope is ``all``, ``none`` or ``one`` and its
  reason a fixed text naming the session; the hospital's own ``operator`` entry has the given
  reason, joined by the session. Its database sessions are named ``op:<session>:<process>`` by
  ``settings/tenancy.py``.
- ``open_entries()`` (``manage.py ihr_operator_open``, run once per session by staging.sh's
  ``op_scope_open``): one ``operator`` entry in each hospital in scope, so the hospital's own
  trail says "an operator session had access to your hospital at T, for reason R". ``all`` is
  every hospital whose status is in the fleet's migrate statuses (provisioning, active,
  suspended, offboarding: every schema that holds its data); its entries carry the fixed reason
  ``operator access (all hospitals), session <id>`` (the full reason stays in the log store: it
  may name another hospital). A hospital held for a restore, without a trail, or whose kind check
  is an older build's (migration 0007 not applied) gets none and is named as skipped. An entry
  that cannot be written is named ``failed`` and the others are still tried (review A7: entries
  already written stay, and the answer accounts for each); the command then exits 3.

The entry is written as the restore entry is (``restore/engine.py``, ``supersede.py``): an INSERT
of the data columns only; the identity gives ``id`` and the stamp trigger (``ihr_audit_stamp``,
seal.py) forces ``txid``, ``at`` and ``salt``. It goes on a connection of its own in autocommit,
so nothing the caller rolls back can remove it, once per session and hospital in this process,
and the hospital is sealed right after (``seal.seal_once``; the beat job seals what is left).

What it is (attack.md, S1/S5): attribution of an honest or careless operator, by convention. A
deliberate insider with the deploy account can skip it; the host's records are the evidence.
"""

import json
import os
import re
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS, DatabaseError, connections
from psycopg import sql

from ihr_audit import context
from ihr_audit.seal import seal_once

# The same rules as scripts/staging.sh (OP_NAME_RE, OP_SLUG_RE, OP_REASON_RE, op_reason_ok)
NAME = re.compile(r"[a-z][a-z0-9.-]{1,31}")
SLUG = re.compile(r"[a-z][a-z0-9-]{2,30}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
REASON = re.compile(r"[A-Za-z0-9 ._:/#-]{3,120}")
LONG_NUMBER = re.compile(r"[0-9]{6}")
SESSION = re.compile(r"[0-9a-f]{12}")
SHARED_NAMES = frozenset({"ihr", "root", "admin", "postgres"})
ADMIN_USER_FILE = Path(
    "/etc/indiehr/admin-user"
)  # the bootstrap's admin account, on a host
TIERS = ("data", "job")
ALL = "all"
NONE = "none"
ALL_REASON = "operator access (all hospitals), session {session}"
# The one-off's own writes (write_context): never a slug or the given reason
ONE = "one"
ONE_REASON = "operator access (one hospital), session {session}"
JOB_REASON = "operator job, session {session}"
KIND = "operator"
INSERT = (
    "INSERT INTO {} (kind, actor, context, tbl, op, row_pk, patients, data) "
    "VALUES ('operator', %s, %s::jsonb, '', '', NULL, '{{}}', %s::jsonb)"
)
# The kind check of one hospital's trail, if the trail is there
KIND_CHECK = (
    "SELECT pg_get_constraintdef(c.oid) FROM pg_class t "
    "JOIN pg_namespace n ON n.oid = t.relnamespace "
    "LEFT JOIN pg_constraint c ON c.conrelid = t.oid "
    "AND c.conname = 'ihr_audit_entry_kind_check' "
    "WHERE n.nspname = %s AND t.relname = 'ihr_audit_entry'"
)
SEAL_ROUNDS = 50  # as the restore: BATCH entries a seal, until nothing final is left
# (session, schema) written by this process: one entry each, however often it is asked
_WRITTEN = set()


class OperatorError(ValueError):
    """A value staging.sh would have refused: ``<code>: <why>``."""


class EntryWriteError(DatabaseError):
    """Some hospitals' entries could not be written; ``opened`` and ``skipped`` (the failed ones
    as ``failed``) still name every hospital in scope."""

    def __init__(self, message, opened, skipped):
        super().__init__(message)
        self.opened = opened
        self.skipped = skipped


def _refuse(code, why):
    msg = f"{code}: {why}"
    raise OperatorError(msg)


def check_session(value):
    if not SESSION.fullmatch(value or ""):
        _refuse("bad-session", "a session id is 12 lowercase hex characters")


def _admin_user():
    try:
        return ADMIN_USER_FILE.read_text().strip()
    except OSError:
        return ""


def check_operator(value):
    if not NAME.fullmatch(value or ""):
        _refuse(
            "bad-operator",
            "an operator name is 2-32 of a-z, 0-9, . and -, starting with a letter",
        )
    if value in SHARED_NAMES or value == _admin_user():
        _refuse("shared-operator", f"{value} is an account, not a person")


def check_reason(value):
    if not REASON.fullmatch(value or "") or LONG_NUMBER.search(value):
        _refuse(
            "bad-reason",
            "a reason is 3-120 characters of letters, digits, space and . _ : / # -, "
            "with no number of 6 or more digits",
        )


def check_scope(value):
    """The command's scope: one hospital's slug, or all."""
    if value != ALL and (value == NONE or not SLUG.fullmatch(value or "")):
        _refuse("bad-scope", "a scope is a hospital's slug or all")


def check_tier(value):
    if value not in TIERS:
        _refuse("bad-tier", "a tier is data or job")


def entry_reason(scope, reason, session):
    """The reason a hospital's trail shows: the given one for a session of that one hospital,
    the fixed text otherwise (a reason may name another hospital)."""
    if scope in (ALL, NONE):
        return ALL_REASON.format(session=session)
    return reason


def entry_context(session, tier, scope, reason):
    return {
        "via": "operator",
        "session": session,
        "tier": tier,
        "scope": scope,
        "reason": entry_reason(scope, reason, session),
    }


def write_context(session, tier, scope):
    """The context of the one-off's own row writes: they may land in any hospital's trail, so
    no slug, uuid or given reason (review A5); the scope only as all, none or one."""
    if scope == NONE:
        kind, reason = NONE, JOB_REASON.format(session=session)
    elif scope == ALL:
        kind, reason = ALL, entry_reason(ALL, "", session)
    else:
        kind, reason = ONE, ONE_REASON.format(session=session)
    return {
        "via": "operator",
        "session": session,
        "tier": tier,
        "scope": kind,
        "reason": reason,
    }


# --- the one-off's own writes ---------------------------------------------------------------


def attribution(environ):
    """(context, actor) for a process of an operator session, or None outside one. A bad
    value raises ImproperlyConfigured: the process stops before any database access."""
    session = environ.get("IHR_OPERATOR_SESSION", "")
    if not session:
        return None
    scope = environ.get("IHR_OPERATOR_SCOPE", "")
    name = environ.get("IHR_OPERATOR", "")
    tier = environ.get("IHR_OPERATOR_TIER", "")
    reason = environ.get("IHR_OPERATOR_REASON", "")
    try:
        check_session(session)
        check_operator(name)
        check_tier(tier)
        check_reason(reason)
        # dumps-get passes the dump's hospital uuid; a job of no hospital passes none
        if scope != NONE and not UUID.fullmatch(scope):
            check_scope(scope)
    except OperatorError as exc:
        msg = f"operator session (IHR_OPERATOR_*): {exc}"
        raise ImproperlyConfigured(msg) from None
    return write_context(session, tier, scope), f"operator:{name}"


def install(environ=None):
    """``IhrAuditConfig.ready``: under an operator session, name the operator on every write
    of this process. Returns the (context, actor) installed, or None."""
    values = attribution(os.environ if environ is None else environ)
    if values is not None:
        context.adopt(*values)
    return values


# --- the hospital entries -------------------------------------------------------------------


def _hospitals(scope):
    from ihr_platform import fleet
    from ihr_platform.models import Hospital

    _refuse_reserved_slugs()
    # Only the columns every build has (review A2/O7): a release's new build runs this on the
    # old schema before its migrations, so a column a later migration adds is never selected.
    # Archived hospitals are listed too, to be named (open_entries), never silently left out.
    if scope == ALL:
        found = Hospital.objects.filter(status__in=fleet.MIGRATE_STATUSES)
        found = found | Hospital.objects.filter(status=Hospital.Status.ARCHIVED)
    else:
        found = Hospital.objects.filter(slug=scope)
    found = found.only("slug", "schema_name", "status")
    return list(found.exclude(schema_name="public").order_by("slug"))


def _refuse_reserved_slugs():
    """``all`` and ``none`` are scopes, never hospitals (models.RESERVED_SLUGS keeps new ones out);
    one that exists already would be read as the whole fleet or nothing (review A4)."""
    from ihr_platform.models import Hospital

    if Hospital.objects.filter(slug__in=(ALL, NONE)).exists():
        _refuse(
            "reserved-slug",
            "a hospital's slug is all or none, which are operator scopes: rename is impossible "
            "(slugs are never changed): look at it with the owner before any operator session",
        )


def _separate():
    """A connection of its own, in autocommit: what the caller rolls back stays written."""
    return connections.create_connection(DEFAULT_DB_ALIAS)


def _timed(conn):
    """The separate connection with short waits: a lock held elsewhere fails this hospital's
    entry (named ``failed``), never the session's whole start."""
    try:
        with conn.cursor() as cursor:
            cursor.execute("SET lock_timeout = '10s'")
            cursor.execute("SET statement_timeout = '30s'")
    except DatabaseError:
        conn.close()
        raise
    return conn


def _kind_check(cursor, schema):
    """None: no trail; "": a trail that does not take the kind yet; else the check's text."""
    cursor.execute(KIND_CHECK, [schema])
    row = cursor.fetchone()
    if row is None:
        return None
    return row[0] or ""


def _insert(cursor, schema, actor, ctx):
    cursor.execute(
        sql.SQL(INSERT).format(sql.Identifier(schema, "ihr_audit_entry")),
        [actor, json.dumps(ctx), json.dumps({"event": "opened"})],
    )


def _seal(schema, warn):
    try:
        for _ in range(SEAL_ROUNDS):
            if not seal_once(schema):
                break
    except DatabaseError as exc:  # the beat job seals it within the minute
        warn(f"{schema}: not sealed now ({type(exc).__name__}); the beat job seals it")


def open_entries(session, name, scope, reason, tier, warn=None):
    """One ``operator`` entry in each hospital in ``scope``. Returns (opened slugs, [(slug,
    why)]). Raises OperatorError on a bad value (nothing written), and EntryWriteError naming the
    hospitals whose entry could not be written, after trying every other one (entries already
    written stay; its ``opened`` and ``skipped`` answer for each hospital)."""
    from ihr_platform import release

    check_session(session)
    check_operator(name)
    check_scope(scope)
    check_reason(reason)
    check_tier(tier)
    warn = warn or (lambda text: None)
    ctx = entry_context(session, tier, scope, reason)
    actor = f"operator:{name}"
    hospitals = _hospitals(scope)
    opened, skipped, failures = [], [], []
    if scope != ALL and not hospitals:
        return opened, [(scope, "absent")]
    conn = None
    try:
        for hospital in hospitals:
            schema = hospital.schema_name
            if scope == ALL and hospital.status == hospital.Status.ARCHIVED:
                # still holds its data and trail (sealed every 10 minutes), but is not migrated:
                # named, so the line answers for it (review A3)
                skipped.append((hospital.slug, "archived"))
                continue
            if release.restore_holder(hospital):  # the restore's alone
                skipped.append((hospital.slug, "held"))
                continue
            if (session, schema) in _WRITTEN:
                opened.append(hospital.slug)
                continue
            try:
                if conn is None:
                    conn = _timed(_separate())
                with conn.cursor() as cursor:
                    check = _kind_check(cursor, schema)
                    if check is None:
                        skipped.append((hospital.slug, "absent"))
                        continue
                    if f"'{KIND}'" not in check:
                        skipped.append((hospital.slug, "old-schema"))
                        continue
                    _insert(cursor, schema, actor, ctx)
            except DatabaseError as exc:
                failures.append(
                    f"the operator entry could not be written in {hospital.slug} "
                    f"({type(exc).__name__}: {str(exc).strip()[:200]})"
                )
                skipped.append((hospital.slug, "failed"))
                continue
            _WRITTEN.add((session, schema))
            opened.append(hospital.slug)
            _seal(schema, warn)
    finally:
        if conn is not None:
            conn.close()
    if failures:
        raise EntryWriteError("; ".join(failures), opened, skipped)
    return opened, skipped


def answer(opened, skipped):
    """The one line staging.sh reads."""
    left = ",".join(opened) or "-"
    right = ",".join(f"{slug}:{why}" for slug, why in skipped) or "-"
    return f"opened={left} skipped={right}"
