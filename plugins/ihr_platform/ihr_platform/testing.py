"""Tests run as the app role (P1-T19a; ihr_platform/db_roles.py, test mode).

With ``IHR_DB_ROLES=test`` (CI's tenant job runs the plugin suites and the isolation suite so),
``TenantTestRunner`` creates the roles in the test cluster, converges the grants and then:

- every test connection, thread connections included, runs ``SET ROLE ihr_app`` at session
  level, outside any transaction (never ``SET LOCAL``, which outside a transaction does
  nothing), so a rolled-back transaction never undoes it. That session role is the
  protection: whatever runs on the connection runs as ``ihr_app``, COPY (``cursor.copy``),
  ``callproc`` and raw psycopg calls included, and fails on a missing grant as in production;
- an execute wrapper on each connection catches a role change nobody asked for: before each
  query that goes through Django's ``execute`` it checks ``current_user`` is ``ihr_app``, and
  outside ``as_owner()`` it refuses any SQL that changes the role itself (``SET``/``RESET
  ROLE``, ``SESSION AUTHORIZATION`` and ``session_authorization``, ``DISCARD ALL``,
  ``set_config('role' | 'session_authorization', ...)``, the name quoted or passed as a
  parameter), wherever it is in the text (review C8: a string such as ``SELECT 1; RESET ROLE;
  INSERT ...`` would otherwise pass the check made before it ran). COPY, ``callproc`` and raw
  calls do not pass the wrapper (they still run under the session role), and SQL that builds
  such a statement at run time (``EXECUTE 'SET ' || ...`` in a DO block) is seen only by the
  next statement's check;
- ``as_owner()`` is the one explicit way to the superuser (who owns the test database): for
  what only owner-run processes do in production (making hospitals and their schemas,
  provisioning, ihr_fleet_run, the loaders, ihr_create_tenant...) and for fixtures and
  clean-ups that write tables the app may only read. Outside test mode it does nothing.

Usable as ``with as_owner():`` or as a decorator, ``@as_owner()``.
"""

import contextlib
import contextvars
import re

from django.db import connections
from django.db.backends.signals import connection_created
from psycopg.pq import TransactionStatus

from ihr_platform.db_roles import APP

_OWNER_DEPTH = contextvars.ContextVar("ihr_test_owner_depth", default=0)
_STATE = {"on": False}
_UID = "ihr_test_app_role"


def active():
    """True while the tests run as the app role."""
    return _STATE["on"]


def owner_now():
    return _OWNER_DEPTH.get() > 0


def _aborted(raw):
    return raw.info.transaction_status == TransactionStatus.INERROR


def _switch(connection, role):
    """SET ROLE (app) or RESET ROLE (owner) on the open connection, if any; remembers which
    physical connection it is so a reconnect is noticed. In an aborted transaction it waits
    for the rollback (which restores the role the transaction began with)."""
    raw = connection.connection
    if raw is None:
        return
    if _aborted(raw):
        connection.ihr_test_role = None
        return
    raw.execute(f"SET ROLE {APP}" if role == "app" else "RESET ROLE")
    connection.ihr_test_role = (raw, role)


def _assert_app(raw):
    user = raw.execute("SELECT current_user").fetchone()[0]
    if user != APP:
        msg = (
            f"A test query ran as {user}, not {APP} (IHR_DB_ROLES=test). Work only an "
            "owner-run process does goes inside ihr_platform.testing.as_owner() (P1-T19a)."
        )
        raise AssertionError(msg)


# SQL that changes the role (C8, and its second review), anywhere in a statement's text:
# - SET [LOCAL | SESSION] ROLE ... and SET SESSION AUTHORIZATION ... (keyword forms, never
#   followed by "=": an UPDATE's ``SET role = ...`` is a column);
# - SET/RESET of the parameters themselves (``SET session_authorization = ...``, ``SET "role"
#   TO ...``, ``RESET session_authorization``), at the start of a statement (inside an UPDATE
#   such a SET names a column);
# - RESET ROLE and RESET SESSION AUTHORIZATION anywhere, and DISCARD ALL (it runs SET SESSION
#   AUTHORIZATION DEFAULT);
# - set_config('role' | 'session_authorization', ...), the name quoted or (below) a parameter.
_ROLE_PARAMETERS = ("role", "session_authorization")
_ROLE_CHANGE = re.compile(
    r"\bSET\s+(?:(?:LOCAL|SESSION)\s+)?(?:ROLE|SESSION\s+AUTHORIZATION)\b(?!\s*=)"
    r"|(?:^|;)\s*(?:SET|RESET)\s+(?:(?:LOCAL|SESSION)\s+)?"
    r"\"?(?:role|session_authorization)\"?(?![\w$])"
    r"|\bRESET\s+(?:\"?(?:role|session_authorization)\"?(?![\w$])|SESSION\s+AUTHORIZATION\b)"
    r"|\bDISCARD\s+ALL\b"
    r"|\bset_config\s*\(\s*E?'\s*\"?(?:role|session_authorization)\"?\s*'",
    re.IGNORECASE,
)
# set_config whose parameter name is a query parameter (%s, %(name)s, $1)
_SET_CONFIG_BOUND = re.compile(
    r"\bset_config\s*\(\s*(?:%s|%\(\w+\)s|\$\d+)", re.IGNORECASE
)


def _values(params):
    """The parameter values of one execute or executemany call, flattened."""
    if params is None:
        return []
    if isinstance(params, dict):
        return list(params.values())
    flat = []
    for value in params:
        if isinstance(value, (list, tuple, dict)):
            flat += _values(value)
        else:
            flat.append(value)
    return flat


def _changes_role(statement, params=None):
    if not isinstance(statement, str):
        return False
    if _ROLE_CHANGE.search(statement):
        return True
    return bool(_SET_CONFIG_BOUND.search(statement)) and any(
        isinstance(value, str) and value.strip().strip('"').lower() in _ROLE_PARAMETERS
        for value in _values(params)
    )


def _head(statement):
    return statement.lstrip()[:16].upper() if isinstance(statement, str) else ""


def _must_be_first(statement):
    """SET TRANSACTION must come before any query of its transaction: checked after it."""
    return _head(statement).startswith("SET TRANSACTION")


def _setting(statement):
    """A session setting (django-tenants' SET search_path on every cursor, set_config is a
    query): it reads and writes no table, and checking first would put a query ahead of a
    SET TRANSACTION. The next statement that touches data is checked."""
    head = _head(statement)
    return head.startswith(("SET ", "RESET ", "SHOW ")) and not _must_be_first(
        statement
    )


def _check_role(execute, sql, params, many, context):
    """Every query, while the tests run as the app role and outside ``as_owner()``: the
    connection's current_user must be ihr_app."""
    connection = context["connection"]
    raw = connection.connection
    if not _STATE["on"] or owner_now() or raw is None or _aborted(raw):
        return execute(sql, params, many, context)
    if getattr(connection, "ihr_test_role", None) != (raw, "app"):
        _switch(
            connection, "app"
        )  # a new connection, or the first query after owner work
    if _changes_role(sql, params):
        msg = (
            "A test query changes the database role (IHR_DB_ROLES=test): only "
            "ihr_platform.testing.as_owner() may (P1-T19a)."
        )
        raise AssertionError(msg)
    if _setting(sql):
        return execute(sql, params, many, context)
    if _must_be_first(sql):
        result = execute(sql, params, many, context)
        _assert_app(raw)
        return result
    _assert_app(raw)
    return execute(sql, params, many, context)


def _install(connection):
    if _check_role not in connection.execute_wrappers:
        connection.execute_wrappers.append(_check_role)


def _on_new_connection(sender=None, connection=None, **kwargs):
    if not _STATE["on"] or connection is None or connection.alias != "default":
        return
    _install(connection)
    _switch(connection, "owner" if owner_now() else "app")


def start(connection):
    """From now on, every test connection runs as the app role (the runner, after set-up)."""
    _STATE["on"] = True
    connection_created.connect(_on_new_connection, weak=False, dispatch_uid=_UID)
    _install(connection)
    connection.ensure_connection()
    _switch(connection, "app")


def stop(connection):
    """Back to the superuser (the runner, before tearing the test database down)."""
    _STATE["on"] = False
    connection_created.disconnect(dispatch_uid=_UID)
    with contextlib.suppress(ValueError):
        connection.execute_wrappers.remove(_check_role)
    if connection.connection is not None and not _aborted(connection.connection):
        connection.connection.execute("RESET ROLE")
    connection.ihr_test_role = None


@contextlib.contextmanager
def as_owner(using="default"):
    """The test database's owner (its superuser), on purpose: owner-run work and fixtures.
    Nests; outside test mode it does nothing."""
    token = _OWNER_DEPTH.set(_OWNER_DEPTH.get() + 1)
    if _STATE["on"]:
        _switch(connections[using], "owner")
    try:
        yield
    finally:
        _OWNER_DEPTH.reset(token)
        if _STATE["on"] and not owner_now():
            _switch(connections[using], "app")


def owner_for_test(testcase):
    """For a test class whose subject only owner-run processes run (ihr_fleet_run,
    provisioning, the loaders): its setUp calls this first, so the whole test and its
    clean-ups registered afterwards run as the owner (they run first: last in, first out)."""
    owner = as_owner()
    owner.__enter__()
    testcase.addCleanup(owner.__exit__, None, None, None)


def owner_call(function, *args, **kwargs):
    """``function(*args, **kwargs)`` as the owner: for clean-ups (``addCleanup(owner_call,
    ...)``) of rows only owner-run processes write."""
    with as_owner():
        return function(*args, **kwargs)
