# ruff: noqa: S608 (SQL built only from constant fragments; values are parameters)
"""Separate database roles (P1-T19a; TENANCY_DESIGN §6.10; docs/reviews/p1-t19/design.md).

Four roles, one model (``ROLES``):

- ``ihr_owner`` owns the database and every object in it: the migrate one-shot, the admin
  one-offs and the onboarding provisioner (DDL: CREATE/DROP SCHEMA, migrations). CONNECT and
  TEMPORARY on the database.
- ``ihr_app`` serves every request and runs every worker (backend, celery-worker, celery-beat,
  staff-worker): data only. CONNECT, never CREATE or TEMPORARY, no memberships, NOINHERIT.
- ``ihr_audit_watch`` (P1-T21 item 10): the audit watch's own login (compose.monitoring.yaml,
  secrets/audit-watch.env). SELECT on the two audit tables of every hospital schema and on the
  hospital list and its schema state in public, USAGE on the schemas, CONNECT; nothing else (it
  writes nothing, executes no sealing function). Its process declares ``IHR_DB_ROLE=watch``
  and is checked like the app's (``watch_problems``).
- ``ihr_dump`` (P1-T22 item 2): the nightly dumper, a member of ``pg_read_all_data`` only.

What ``ihr_app`` (and the watch role) may do on each table comes from two reviewed lists next
to this file: ``public_tables.txt`` (every table in ``public``) and ``hospital_tables.txt``
(the exceptions to "data rights on every table" in a hospital schema). An unlisted public
table, or an audit-exempt hospital table left at ``dml``, is a finding: the public one gets no
rights at all, and the tests fail on both.

``ensure_grants`` converges from the catalogue: it reads the actual ACLs (tables, columns,
sequences, schemas, the sealing functions, the database; PUBLIC and unknown grantees
included), grants what is missing and revokes anything beyond the model. It changes only what
differs, so a migration run with nothing new changes nothing. It repairs on ``post_migrate``
(ihr_audit/apps.py, beside the audit triggers), in the provisioning schema step and in
``ihr_fleet_run migrate``; ``ihr_fleet_run check`` and the run-time check only report. There
are no global table defaults: the owner's only default privilege is USAGE on new schemas.

The mode is ``settings.IHR_DB_ROLES``:

- ``off`` (the default: dev, the plain upstream CI job, staging until P1-T19b): nothing is
  granted or checked (one log line).
- ``test``: the test runner creates the roles but the owner (its own superuser owns the test
  database), converges the grants, and every test connection runs as ``ihr_app``
  (ihr_platform/testing.py). Ownership and default privileges are not checked there.
- ``required`` (deployment; switched on by P1-T19b with the move of the roles): every role must
  exist with exactly its attributes and memberships and no stored settings, ``ihr_owner`` must
  own everything, and every process checks its database role on its first connection
  (``install_check``). A process that is not what it declares (``IHR_DB_ROLE``) writes one
  fixed line, closes the connection and refuses every later connection. Every later new
  connection of an app process re-checks the role's stored settings, memberships and
  attributes (S2: a role can ALTER ROLE itself). A right beyond the model for the app or
  PUBLIC, or a broken control (``Finding.control``: the queue guard, the sealing functions,
  large objects), refuses the process when it is in public or the database; in one hospital's
  schema it does not stop the process: that hospital alone is not served by it (C2,
  ``gated_schemas``), and the alert
  DatabaseSchemaGated names it every minute. Gating hides the hospital; it does not take the
  right away from the shared app role, so a fleet-wide ``ihr_fleet_run migrate`` converges
  every hospital schema in the catalogue, archived ones too (fleet.py). What reaches an
  owner's rights refuses the process wherever it is: CREATE on any schema, or a definer
  function the app may execute (``Finding.escalates``).

In ``off`` mode a process that connects as ``ihr_app`` or ``ihr_owner`` is refused (S10: once
the roles are moved, a container that lost ``IHR_DB_ROLES`` must not run unchecked).
"""

import logging
import os
import re
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.db import transaction
from psycopg import sql

logger = logging.getLogger(__name__)

OWNER, APP, WATCH, DUMP = "ihr_owner", "ihr_app", "ihr_audit_watch", "ihr_dump"
PUBLIC = "PUBLIC"
MODES = ("off", "test", "required")
DECLARED = ("app", "owner", "watch")
HOSPITAL_SCHEMA_RE = re.compile(r"^h_[a-z0-9]{10}$")
HERE = Path(__file__).resolve().parent
PUBLIC_LIST = HERE / "public_tables.txt"
HOSPITAL_LIST = HERE / "hospital_tables.txt"


@dataclass(frozen=True)
class Role:
    name: str
    inherit: bool
    memberships: tuple = ()
    database: frozenset = frozenset({"CONNECT"})
    # False until a service logs in as it (watch: P1-T21, dump: P1-T22): a missing LOGIN is
    # then reported, never raised (C6); its attributes, memberships and settings still are
    in_use: bool = True


ROLES = {
    OWNER: Role(OWNER, inherit=True, database=frozenset({"CONNECT", "TEMPORARY"})),
    APP: Role(APP, inherit=False),
    WATCH: Role(WATCH, inherit=False),
    DUMP: Role(DUMP, inherit=True, memberships=("pg_read_all_data",), in_use=False),
}
# The roles the test runner makes (the owner there is the tests' own superuser)
TEST_ROLES = (APP, WATCH, DUMP)

DML = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})
SEQUENCE = frozenset({"USAGE", "SELECT"})
# TenantOperation: the console queues work and the owner-run provisioner trusts the queue, so
# the app may add an operation and set only who queued what, and when (S1)
QUEUE_COLUMNS = ("queued_action", "queued_by", "queued_at")
# ihr_audit_entry: what the row triggers and the request middleware write. id, txid, at and
# salt are always the database's own (a BEFORE INSERT trigger forces txid, at and salt; S2)
AUDIT_DATA_COLUMNS = (
    "kind",
    "actor",
    "context",
    "tbl",
    "op",
    "row_pk",
    "patients",
    "data",
)


@dataclass(frozen=True)
class Grant:
    """What one class of table gives ihr_app: table privileges and column privileges."""

    table: frozenset = frozenset()
    columns: tuple = ()  # ((privilege, (column, ...)), ...)


CLASSES = {
    "dml": Grant(DML),
    "select": Grant(frozenset({"SELECT"})),
    "none": Grant(),
    "queue": Grant(frozenset({"SELECT", "INSERT"}), (("UPDATE", QUEUE_COLUMNS),)),
    "append": Grant(frozenset({"SELECT"}), (("INSERT", AUDIT_DATA_COLUMNS),)),
}
# The read-only watch role (P1-T21): the audit trail, the hospital list it walks and each
# hospital's schema state (whether it is servable: trigger coverage is judged only then, A7)
WATCH_TABLES = {
    "hospital": {"ihr_audit_entry", "ihr_audit_seal"},
    "public": {"ihr_platform_hospital", "ihr_platform_hospitalschemastate"},
}
# Hospital-schema functions whose ACL is ours: who may EXECUTE each. Sealing runs as the owner
# through the first (SECURITY DEFINER, no arguments); the second is the owner's own (tests,
# repairs). The third is the audit trail's masking helper: the row trigger runs as whoever
# writes (the app) and calls it directly. A trigger function fires without EXECUTE, a function
# it calls does not, and in required mode the owner's new functions are nobody's
# (default_findings), so without this a new hospital's helper would refuse every app write of a
# masked identifier (second review of S3). Any other function that is not a trigger function
# is a finding until it is listed here.
FUNCTIONS = {
    "ihr_audit_seal_next": {APP},
    "ihr_audit_seal_run": set(),
    "ihr_audit_last4": {APP},
}
SECURITY_DEFINER = {"ihr_audit_seal_next"}
# Never the app's on any table (TRIGGER would let it attach a refusing trigger, MAINTAIN lock)
NEVER = frozenset({"TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN"})
RELKINDS = ("r", "p", "v", "m", "f")
# Only tables take the hospital default (dml). A view, materialized view or foreign table must
# be listed (S4): a view runs with its owner's rights, so one over the trail or
# django_migrations would hand the app what the lists deny
TABLE_KINDS = ("r", "p")
# A table the audit trail does not record, or one shaped like an append-only one, must have a
# class other than dml (S5)
APPEND_ONLY_SHAPE = re.compile(r"^ihr_audit_|_ledger$|_seal$")


class RolesError(Exception):
    """The database roles differ from the model in a way only an operator can fix."""


class RoleRefused(RuntimeError):  # noqa: N818 (a refusal, named as one in the logs)
    """This process's database role is wrong: it serves nothing."""


@dataclass
class Finding:
    # "extra" (a right beyond the model), "missing", "problem" (only an operator can fix it;
    # raised in required mode) or "notice" (reported only)
    kind: str
    text: str
    statement: object = None  # what repairs it (sql.Composed), when anything can
    grantee: str = ""
    schema: str = ""  # the schema it is in; "" for the database and the roles
    # A problem with a control the app must not run without (the provisioner's queue guard,
    # the sealing functions, a definer function the app may execute, large objects): the
    # run-time check refuses the process for it, or gates the hospital whose schema it is in
    # (second review: a problem is otherwise only counted at run time)
    control: bool = False
    # A way to an owner's rights (a definer function the app may execute): it refuses the
    # process wherever it is, like CREATE on any schema, because the shared app role reaches
    # it from every hospital the process serves
    escalates: bool = False

    def __str__(self):
        return f"{self.kind}: {self.text}"


@dataclass
class Findings:
    items: list = field(default_factory=list)

    def add(
        self,
        kind,
        text,
        statement=None,
        grantee="",
        schema="",
        control=False,
        escalates=False,
    ):
        self.items.append(
            Finding(kind, text, statement, grantee, schema, control, escalates)
        )

    def of(self, kind):
        return [f for f in self.items if f.kind == kind]

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)


# --- mode and the reviewed lists ----------------------------------------------------------


def mode():
    value = getattr(settings, "IHR_DB_ROLES", "off") or "off"
    if value not in MODES:
        msg = f"IHR_DB_ROLES must be one of {', '.join(MODES)}, not {value!r}"
        raise RolesError(msg)
    return value


_OFF_LOGGED = {"done": False}


def _off():
    if mode() != "off":
        return False
    if not _OFF_LOGGED["done"]:
        _OFF_LOGGED["done"] = True
        logger.info("IHR_DB_ROLES is off: database grants are not managed (P1-T19)")
    return True


def read_list(path):
    """{table: class} from a reviewed list: "<table> <class> <reason>" per line."""
    entries = {}
    for number, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 2)
        where = f"{Path(path).name}:{number}"
        if len(parts) < 3:  # noqa: PLR2004
            msg = f"{where}: '<table> <class> <reason>' (a reason is required)"
            raise RolesError(msg)
        table, cls, _reason = parts
        if cls not in CLASSES:
            msg = f"{where}: class {cls!r} is not one of {', '.join(CLASSES)}"
            raise RolesError(msg)
        if table in entries:
            msg = f"{where}: {table} is listed twice"
            raise RolesError(msg)
        entries[table] = cls
    return entries


def public_classes():
    return read_list(PUBLIC_LIST)


def hospital_classes():
    return read_list(HOSPITAL_LIST)


def schema_kind(schema):
    if schema == "public":
        return "public"
    if HOSPITAL_SCHEMA_RE.match(schema or ""):
        return "hospital"
    msg = f"{schema!r} is neither public nor a hospital schema: no grants are managed there"
    raise RolesError(msg)


def classification_gaps(kind, present, listed=None):
    """Relations (``present``: {name: relkind}, in a schema of ``kind``) the lists do not
    classify well enough, and listed ones that do not exist. ``listed``: that kind's list,
    when the caller has read it already (C4: once per run, never once per schema)."""
    from ihr_audit.install import EXEMPT

    if not isinstance(present, dict):
        present = dict.fromkeys(present, "r")
    gaps = []
    if kind == "public":
        listed = public_classes() if listed is None else listed
        gaps += [
            f"public.{t}: not in public_tables.txt (classify it: dml, select, none...)"
            for t in sorted(set(present) - set(listed))
        ]
        gaps += [
            f"public_tables.txt lists {t}, which does not exist"
            for t in sorted(set(listed) - set(present))
        ]
        return gaps
    listed = hospital_classes() if listed is None else listed
    for table, relkind in sorted(present.items()):
        if relkind not in TABLE_KINDS and table not in listed:
            gaps.append(
                f"{table}: a view, materialized view or foreign table is never data by "
                "default (it reads with its owner's rights): list it in hospital_tables.txt"
            )
            continue
        shaped = table in EXEMPT or APPEND_ONLY_SHAPE.search(table)
        if shaped and listed.get(table, "dml") == "dml":
            gaps.append(
                f"{table}: the audit trail does not record it (or it looks append-only), "
                "so hospital_tables.txt must give it a class other than dml"
            )
    gaps += [
        f"hospital_tables.txt lists {t}, which does not exist"
        for t in sorted(set(listed) - set(present))
    ]
    return gaps


def classification_problems(connection, schema):
    """``classification_gaps`` for one schema of this database (the tests, any mode)."""
    kind = schema_kind(schema)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT c.relname, c.relkind FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relkind = ANY(%s)",
            [schema, list(RELKINDS)],
        )
        present = dict(cursor.fetchall())
    return classification_gaps(kind, present)


def expected_table(kind, table, classes, relkind="r"):
    """{grantee: (table privileges, {privilege: columns})}; None: an unclassified public
    relation, or an unlisted hospital view (no rights at all until it is reviewed)."""
    if kind == "public" or relkind not in TABLE_KINDS:
        cls = classes.get(table)
    else:
        cls = classes.get(table, "dml")
    if cls is None:
        return None
    grant = CLASSES[cls]
    wanted = {}
    if grant.table or grant.columns:
        wanted[APP] = (set(grant.table), {p: set(cols) for p, cols in grant.columns})
    if table in WATCH_TABLES[kind]:
        wanted[WATCH] = ({"SELECT"}, {})
    return wanted


def _managed_roles(current_mode):
    return set(TEST_ROLES) if current_mode == "test" else set(ROLES)


# --- reading the catalogue ------------------------------------------------------------------

_GRANTEE = "CASE a.grantee WHEN 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END"


def _nested():
    return defaultdict(lambda: defaultdict(set))


def read_state(cursor, schemas):
    """Everything the model governs in ``schemas``, in seven catalogue queries."""
    schemas = list(schemas)
    state = {
        "relations": {},  # (schema, table) -> (relkind, owner)
        "acl": _nested(),  # (schema, table) -> grantee -> privileges
        "columns": defaultdict(
            _nested
        ),  # (schema, table) -> grantee -> priv -> columns
        "attnames": defaultdict(
            set
        ),  # (schema, table) -> its columns (the classed ones)
        "sequences": {},  # (schema, sequence) -> (owning table or None, owner)
        "seq_acl": _nested(),
        "schemas": {},  # schema -> owner
        "schema_acl": defaultdict(lambda: defaultdict(set)),
        "functions": {},  # (schema, name) -> (arguments, owner, security definer, config)
        "function_acl": _nested(),
        # SECURITY DEFINER functions the model does not name (S3): schema -> [("name(args)",
        # whether the app may execute it)]
        "definers": defaultdict(list),
        # Other functions the model does not name, trigger functions aside (second review of
        # S3): schema -> ["name(args)"]
        "unlisted": defaultdict(list),
        # Grant options held by anyone but an object's owner (S3): schema -> [(name,
        # privilege, privilege SQL, target SQL, grantee)]
        "options": defaultdict(list),
    }
    _read_relations(cursor, schemas, state)
    _read_columns(cursor, schemas, state)
    _read_schemas(cursor, schemas, state)
    _read_functions(cursor, schemas, state)
    # Each schema's objects, sorted once (C4: per-schema work stays linear in the fleet)
    index = defaultdict(lambda: {"relations": [], "sequences": []})
    for key in sorted(state["relations"]):
        index[key[0]]["relations"].append(key)
    for key in sorted(state["sequences"]):
        index[key[0]]["sequences"].append(key)
    state["index"] = index
    return state


def _option(state, schema, name, privilege, target, privilege_sql=None):
    """Record a grant option (``target``: the object, as SQL)."""

    def add(grantee):
        state["options"][schema].append(
            (name, privilege, privilege_sql or sql.SQL(privilege), target, grantee)
        )

    return add


def _read_relations(cursor, schemas, state):
    cursor.execute(
        "SELECT n.nspname, c.relname, c.relkind, pg_get_userbyid(c.relowner), "
        f"{_GRANTEE}, a.privilege_type, a.grantee = c.relowner, a.is_grantable "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "LEFT JOIN LATERAL aclexplode(c.relacl) a ON true "
        "WHERE n.nspname = ANY(%s) AND c.relkind = ANY(%s)",
        [schemas, [*RELKINDS, "S"]],
    )
    for row in cursor.fetchall():
        schema, name, relkind, owner, grantee, privilege, own, grantable = row
        key = (schema, name)
        if relkind == "S":
            state["sequences"].setdefault(key, (None, owner))
            target, what = state["seq_acl"], "SEQUENCE"
        else:
            state["relations"][key] = (relkind, owner)
            target, what = state["acl"], "TABLE"
        if grantee is None or own:
            continue
        target[key][grantee].add(privilege)
        if grantable:
            object_sql = sql.SQL(what + " {}").format(sql.Identifier(*key))
            _option(state, schema, f"{schema}.{name}", privilege, object_sql)(grantee)
    cursor.execute(
        "SELECT n.nspname, s.relname, t.relname, pg_get_userbyid(s.relowner) "
        "FROM pg_depend d JOIN pg_class s ON s.oid = d.objid AND s.relkind = 'S' "
        "JOIN pg_class t ON t.oid = d.refobjid "
        "JOIN pg_namespace n ON n.oid = s.relnamespace "
        "WHERE d.classid = 'pg_class'::regclass AND d.refclassid = 'pg_class'::regclass "
        "AND d.deptype IN ('a', 'i') AND n.nspname = ANY(%s)",
        [schemas],
    )
    for schema, seq, table, owner in cursor.fetchall():
        state["sequences"][(schema, seq)] = (table, owner)


def _read_columns(cursor, schemas, state):
    classed = [
        t
        for t, cls in {**public_classes(), **hospital_classes()}.items()
        if CLASSES[cls].columns
    ]
    cursor.execute(
        "SELECT n.nspname, c.relname, att.attname, "
        f"{_GRANTEE}, a.privilege_type, a.grantee = c.relowner, a.is_grantable "
        "FROM pg_attribute att JOIN pg_class c ON c.oid = att.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "LEFT JOIN LATERAL aclexplode(att.attacl) a ON true "
        "WHERE n.nspname = ANY(%s) AND c.relkind = ANY(%s) AND att.attnum > 0 "
        "AND NOT att.attisdropped AND (att.attacl IS NOT NULL OR c.relname = ANY(%s))",
        [schemas, list(RELKINDS), classed],
    )
    for row in cursor.fetchall():
        schema, table, column, grantee, privilege, own, grantable = row
        state["attnames"][(schema, table)].add(column)
        if grantee is None or own:
            continue
        state["columns"][(schema, table)][grantee][privilege].add(column)
        if grantable:
            _option(
                state,
                schema,
                f"{schema}.{table}.{column}",
                privilege,
                sql.SQL("TABLE {}").format(sql.Identifier(schema, table)),
                sql.SQL("{} ({})").format(sql.SQL(privilege), sql.Identifier(column)),
            )(grantee)


def _read_schemas(cursor, schemas, state):
    cursor.execute(
        "SELECT n.nspname, pg_get_userbyid(n.nspowner), "
        f"{_GRANTEE}, a.privilege_type, a.grantee = n.nspowner, a.is_grantable "
        "FROM pg_namespace n LEFT JOIN LATERAL aclexplode(n.nspacl) a ON true "
        "WHERE n.nspname = ANY(%s)",
        [schemas],
    )
    for schema, owner, grantee, privilege, own, grantable in cursor.fetchall():
        state["schemas"][schema] = owner
        if grantee is None or own:
            continue
        state["schema_acl"][schema][grantee].add(privilege)
        if grantable:
            object_sql = sql.SQL("SCHEMA {}").format(sql.Identifier(schema))
            _option(state, schema, f"schema {schema}", privilege, object_sql)(grantee)


def _read_functions(cursor, schemas, state):
    cursor.execute(
        "SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid), "
        "pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig, "
        f"{_GRANTEE}, a.privilege_type, a.grantee = p.proowner, a.is_grantable "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "LEFT JOIN LATERAL aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
        "ON true WHERE n.nspname = ANY(%s) AND p.proname = ANY(%s)",
        [schemas, list(FUNCTIONS)],
    )
    for row in cursor.fetchall():
        schema, name, arguments, owner, secdef, config = row[:6]
        grantee, privilege, own, grantable = row[6:]
        state["functions"][(schema, name)] = (arguments, owner, secdef, config or [])
        if grantee is None or own:
            continue
        state["function_acl"][(schema, name)][grantee].add(privilege)
        if grantable:
            object_sql = sql.SQL("FUNCTION {}.{}({})").format(
                sql.Identifier(schema), sql.Identifier(name), sql.SQL(arguments)
            )
            _option(state, schema, f"{schema}.{name}()", privilege, object_sql)(grantee)
    cursor.execute(
        "SELECT n.nspname, p.proname || '(' || "
        "pg_get_function_identity_arguments(p.oid) || ')', p.prosecdef, "
        # A trigger function fires without EXECUTE; any other function is called by name
        "p.prorettype IN ('trigger'::regtype, 'event_trigger'::regtype), "
        "has_function_privilege(%s, p.oid, 'EXECUTE') "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = ANY(%s) "
        "AND NOT (n.nspname <> 'public' AND p.proname = ANY(%s)) "
        # An extension's own functions are its upstream's (CREATE EXTENSION, reviewed)
        "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass "
        "AND d.objid = p.oid AND d.deptype = 'e') "
        "ORDER BY 1, 2",
        [APP, schemas, list(FUNCTIONS)],
    )
    for schema, signature, secdef, trigger, app_runs in cursor.fetchall():
        if secdef:
            state["definers"][schema].append((signature, app_runs))
        elif not trigger:
            state["unlisted"][schema].append(signature)


# --- the difference -------------------------------------------------------------------------


def _who(grantee):
    return sql.SQL("PUBLIC") if grantee == PUBLIC else sql.Identifier(grantee)


def _privileges(privileges):
    return sql.SQL(", ").join(sql.SQL(p) for p in sorted(privileges))


def _diff(findings, name, target, actual, wanted):
    """Grants and revokes for one object (``target``: "TABLE x", "SCHEMA x"...) against
    ``wanted`` {grantee: privileges}. A grantee the model does not name (PUBLIC, another
    role) loses everything."""
    for grantee in sorted(set(actual) | set(wanted)):
        if grantee.startswith("unknown (OID="):
            findings.add("problem", f"{name}: a grant to a dropped role {grantee}")
            continue
        have, want = actual.get(grantee, set()), wanted.get(grantee, set())
        if extra := have - want:
            findings.add(
                "extra",
                f"{name}: {grantee} has {', '.join(sorted(extra))}",
                sql.SQL("REVOKE {} ON {} FROM {}").format(
                    _privileges(extra), target, _who(grantee)
                ),
                grantee,
            )
        if missing := want - have:
            findings.add(
                "missing",
                f"{name}: {grantee} lacks {', '.join(sorted(missing))}",
                sql.SQL("GRANT {} ON {} TO {}").format(
                    _privileges(missing), target, _who(grantee)
                ),
                grantee,
            )


def _column_diff(findings, key, actual, wanted, columns):
    """Column privileges against ``wanted`` {grantee: {privilege: columns}}."""
    target = sql.Identifier(*key)
    name = f"{key[0]}.{key[1]}"
    for grantee in sorted(set(actual) | set(wanted)):
        have, want = actual.get(grantee, {}), wanted.get(grantee, {})
        for privilege in sorted(set(have) | set(want)):
            got, need = have.get(privilege, set()), want.get(privilege, set())
            if absent := sorted(need - columns):
                findings.add(
                    "problem",
                    f"{name}: no column {', '.join(absent)} (its {privilege} class)",
                )
                need = need & columns
            for action, cols, verb in (
                ("extra", sorted(got - need), "REVOKE {} ({}) ON TABLE {} FROM {}"),
                ("missing", sorted(need - got), "GRANT {} ({}) ON TABLE {} TO {}"),
            ):
                if cols:
                    findings.add(
                        action,
                        f"{name}: {grantee} {'has' if action == 'extra' else 'lacks'} "
                        f"{privilege} on column(s) {', '.join(cols)}",
                        sql.SQL(verb).format(
                            sql.SQL(privilege),
                            sql.SQL(", ").join(map(sql.Identifier, cols)),
                            target,
                            _who(grantee),
                        ),
                        grantee,
                    )


def _schema_acl(findings, schema, kind, actual):
    target = sql.SQL("SCHEMA {}").format(sql.Identifier(schema))
    if kind == "hospital":
        _diff(
            findings,
            f"schema {schema}",
            target,
            actual,
            {APP: {"USAGE"}, WATCH: {"USAGE"}},
        )
        return
    # public: since Postgres 15 PUBLIC has USAGE only, and nobody but its owner may create
    for grantee, have in sorted(actual.items()):
        if "CREATE" in have:
            findings.add(
                "extra",
                f"schema public: {grantee} has CREATE",
                sql.SQL("REVOKE CREATE ON {} FROM {}").format(target, _who(grantee)),
                grantee,
            )
    for role in (APP, WATCH):
        if "USAGE" not in actual.get(PUBLIC, set()) | actual.get(role, set()):
            findings.add(
                "missing",
                f"schema public: {role} lacks USAGE",
                sql.SQL("GRANT USAGE ON {} TO {}").format(target, sql.Identifier(role)),
                role,
            )


def _function_acl(findings, state, schema, current_mode):
    for name, grantees in FUNCTIONS.items():
        key = (schema, name)
        if key not in state["functions"]:
            continue  # ihr_audit's coverage() reports a missing sealing function
        arguments, owner, secdef, config = state["functions"][key]
        definer = name in SECURITY_DEFINER
        if definer != bool(secdef) or "search_path=pg_catalog, pg_temp" not in config:
            findings.add(
                "problem",
                f"{schema}.{name}: must be "
                f"{'SECURITY DEFINER' if definer else 'SECURITY INVOKER'} with its "
                "search_path pinned to pg_catalog, pg_temp (migrate puts it back)",
                control=True,
            )
        if current_mode == "required" and owner != OWNER:
            # A definer runs with its owner's rights (S3)
            findings.add(
                "problem", f"{schema}.{name}() is owned by {owner}", control=True
            )
        target = sql.SQL("FUNCTION {}.{}({})").format(
            sql.Identifier(schema), sql.Identifier(name), sql.SQL(arguments)
        )
        _diff(
            findings,
            f"{schema}.{name}()",
            target,
            state["function_acl"][key],
            {g: {"EXECUTE"} for g in grantees},
        )


def _grant_options(findings, state, schema):
    """A grant option lets its holder pass the right on (S3): nobody but an object's owner
    holds one. The owner's REVOKE ... CASCADE takes what was passed on as well."""
    for name, label, privilege, target, grantee in state["options"][schema]:
        findings.add(
            "extra",
            f"{name}: {grantee} may grant {label} to others",
            sql.SQL("REVOKE GRANT OPTION FOR {} ON {} FROM {} CASCADE").format(
                privilege, target, _who(grantee)
            ),
            grantee,
        )


def schema_findings(state, schema, current_mode, classes):
    """Every difference between ``schema`` (read into ``state``) and the model."""
    findings = Findings()
    kind = schema_kind(schema)
    if schema not in state["schemas"]:
        findings.add("problem", f"schema {schema} does not exist")
        return _in(findings, schema)
    owner = state["schemas"][schema]
    if current_mode == "required" and owner not in (OWNER, "pg_database_owner"):
        findings.add("problem", f"schema {schema} is owned by {owner}, not {OWNER}")
    _schema_acl(findings, schema, kind, state["schema_acl"][schema])
    index = state["index"].get(schema, {"relations": [], "sequences": []})
    present = {key[1]: state["relations"][key][0] for key in index["relations"]}
    for gap in classification_gaps(kind, present, classes):
        findings.add("problem", gap)
    inserting = set()
    for key in index["relations"]:
        relkind, owner = state["relations"][key]
        wanted = expected_table(kind, key[1], classes, relkind) or {}
        if "INSERT" in wanted.get(APP, (set(), {}))[0]:
            inserting.add(key[1])
        _diff(
            findings,
            f"{key[0]}.{key[1]}",
            sql.SQL("TABLE {}").format(sql.Identifier(*key)),
            state["acl"][key],
            {g: privs for g, (privs, _cols) in wanted.items() if privs},
        )
        _column_diff(
            findings,
            key,
            state["columns"][key],
            {g: cols for g, (_privs, cols) in wanted.items() if cols},
            state["attnames"][key],
        )
        if current_mode == "required" and owner != OWNER:
            findings.add("problem", f"{key[0]}.{key[1]} is owned by {owner}")
    for key in index["sequences"]:
        table, owner = state["sequences"][key]
        # A table's own sequence: only where the app inserts; a free-standing one in a
        # hospital schema: as any hospital data
        uses = table in inserting if table else kind == "hospital"
        _diff(
            findings,
            f"{key[0]}.{key[1]}",
            sql.SQL("SEQUENCE {}").format(sql.Identifier(*key)),
            state["seq_acl"][key],
            {APP: set(SEQUENCE)} if uses else {},
        )
        if current_mode == "required" and owner != OWNER:
            findings.add("problem", f"{key[0]}.{key[1]} is owned by {owner}")
    if kind == "hospital":
        _function_acl(findings, state, schema, current_mode)
    for signature, app_runs in state["definers"][schema]:
        # One the app may execute is a way to its owner's rights from any hospital the app
        # serves: it refuses the process, wherever it is
        findings.add(
            "problem",
            f"{schema}.{signature}: a SECURITY DEFINER function the model does not know "
            f"(it runs with its owner's rights{'; ihr_app may execute it' if app_runs else ''}"
            ": add it to db_roles.FUNCTIONS, with who may execute it, or make it SECURITY "
            "INVOKER)",
            control=bool(app_runs),
            escalates=bool(app_runs),
        )
    for signature in state["unlisted"][schema]:
        findings.add(
            "problem",
            f"{schema}.{signature}: a function the model does not list (who may execute "
            "it? A helper a trigger calls needs EXECUTE for the app: add it to "
            "db_roles.FUNCTIONS)",
        )
    _grant_options(findings, state, schema)
    return _in(findings, schema)


def _in(findings, schema):
    for finding in findings:
        finding.schema = schema
    return findings


# --- the database, the roles and their defaults ---------------------------------------------


def database_findings(cursor, current_mode):
    """The database's ACL (PUBLIC loses its default CONNECT and TEMPORARY, C8) and owner."""
    findings = Findings()
    cursor.execute(
        "SELECT current_database(), pg_get_userbyid(d.datdba), "
        f"{_GRANTEE}, a.privilege_type, a.grantee = d.datdba, a.is_grantable "
        "FROM pg_database d LEFT JOIN LATERAL "
        "aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) a ON true "
        "WHERE d.datname = current_database()"
    )
    actual, name, owner, options = defaultdict(set), None, None, []
    for db, db_owner, grantee, privilege, own, grantable in cursor.fetchall():
        name, owner = db, db_owner
        if grantee is not None and not own:
            actual[grantee].add(privilege)
            if grantable:
                options.append((grantee, privilege))
    if current_mode == "required" and owner != OWNER:
        findings.add("problem", f"database {name} is owned by {owner}, not {OWNER}")
    wanted = {
        role: set(ROLES[role].database)
        for role in _managed_roles(current_mode)
        if role != OWNER or owner != OWNER
    }
    target = sql.SQL("DATABASE {}").format(sql.Identifier(name))
    _diff(findings, f"database {name}", target, actual, wanted)
    for grantee, privilege in options:  # S3
        findings.add(
            "extra",
            f"database {name}: {grantee} may grant {privilege} to others",
            sql.SQL("REVOKE GRANT OPTION FOR {} ON {} FROM {} CASCADE").format(
                sql.SQL(privilege), target, _who(grantee)
            ),
            grantee,
        )
    if current_mode == "required":
        for text in large_object_problems(cursor):
            findings.add("problem", text, control=True)
    return findings


# Postgres lets every role make large objects (S8): a write channel the audit triggers do not
# see. Closing it needs the superuser (they are pg_catalog's), so the move of the roles
# (P1-T19b) revokes them from PUBLIC as postgres, and required mode checks that it did.
# (lo_import is superuser-only already, since Postgres 11.)
LARGE_OBJECT_CREATORS = (
    "lo_create(oid)",
    "lo_creat(integer)",
    "lo_from_bytea(oid, bytea)",
    "lo_import(text)",
    "lo_import(text, oid)",
)


def large_object_problems(cursor):
    """The large-object creators the app may still execute (required mode: a problem)."""
    cursor.execute(
        "SELECT f FROM unnest(%s::text[]) f "
        "WHERE has_function_privilege(%s, f::regprocedure, 'EXECUTE') ORDER BY 1",
        [list(LARGE_OBJECT_CREATORS), APP],
    )
    return [
        f"large objects: {APP} may execute {name} (as postgres: REVOKE EXECUTE ON "
        f"FUNCTION {name} FROM PUBLIC; P1-T19b)"
        for (name,) in cursor.fetchall()
    ]


# The databases in which a role (``r``) has stored settings: "every database" for ALTER ROLE
# ... SET, else the database's name. A role may store them for itself IN DATABASE any
# database, also one it cannot connect to; every one counts (review S1/C3)
_STORED_IN = (
    "ARRAY(SELECT COALESCE(d.datname::text, 'every database') "
    "FROM pg_db_role_setting s LEFT JOIN pg_database d ON d.oid = s.setdatabase "
    "WHERE s.setrole = r.oid ORDER BY 1)"
)


def _stored_text(where):
    return f"stored settings in {', '.join(where)}"


def role_problems(cursor, current_mode):
    """Each role of the model: it exists, with exactly its attributes and memberships, and
    no stored settings (S9: a role may set its own defaults, e.g. a forged audit actor).
    [(kind, text)]: "problem", or "notice" for a missing LOGIN on a role no service uses
    yet (C6)."""
    problems = []
    expected = _managed_roles(current_mode)
    cursor.execute(
        "SELECT r.rolname, r.rolsuper, r.rolinherit, r.rolcreaterole, r.rolcreatedb, "
        "r.rolcanlogin, r.rolreplication, r.rolbypassrls, "
        "ARRAY(SELECT g.rolname::text FROM pg_auth_members m "
        "      JOIN pg_roles g ON g.oid = m.roleid WHERE m.member = r.oid ORDER BY 1), "
        f"{_STORED_IN} "
        "FROM pg_roles r WHERE r.rolname = ANY(%s)",
        [sorted(expected)],
    )
    found = {row[0]: row[1:] for row in cursor.fetchall()}
    for name in sorted(expected):
        role = ROLES[name]
        if name not in found:
            problems.append(("problem", f"role {name} does not exist"))
            continue
        su, inherit, createrole, createdb, login, repl, bypass, groups, stored = found[
            name
        ]
        attributes = [
            label
            for label, on in (
                ("SUPERUSER", su),
                ("CREATEROLE", createrole),
                ("CREATEDB", createdb),
                ("REPLICATION", repl),
                ("BYPASSRLS", bypass),
            )
            if on
        ]
        if attributes:
            problems.append(("problem", f"role {name} has {', '.join(attributes)}"))
        if bool(inherit) != role.inherit:
            problems.append(
                (
                    "problem",
                    f"role {name} must be {'INHERIT' if role.inherit else 'NOINHERIT'}",
                )
            )
        if not login:
            problems.append(
                (
                    "problem" if role.in_use else "notice",
                    f"role {name} must be able to log in (LOGIN)",
                )
            )
        if sorted(groups) != sorted(role.memberships):
            problems.append(
                (
                    "problem",
                    f"role {name} is a member of {', '.join(groups) or 'nothing'}; "
                    f"the model: {', '.join(role.memberships) or 'nothing'}",
                )
            )
        if stored:
            problems.append(
                (
                    "problem",
                    f"role {name} has {_stored_text(stored)} (ALTER ROLE ... RESET ALL, "
                    "in each: scripts/staging.sh db-roles --assert)",
                )
            )
    return problems


_OBJECTS = {
    "r": "TABLES",
    "S": "SEQUENCES",
    "f": "FUNCTIONS",
    "T": "TYPES",
    "n": "SCHEMAS",
}


def default_findings(cursor):
    """Required mode: the owner's only default privilege is USAGE on new schemas for the
    app and the watch role. No table defaults, global or per schema (S7). Postgres's own
    default EXECUTE to PUBLIC on new functions is revoked too (S3): a SECURITY DEFINER
    function a later migration adds is then nobody's until it is in the model."""
    findings = Findings()
    cursor.execute(
        "SELECT EXISTS (SELECT 1 FROM aclexplode(coalesce((SELECT d.defaclacl "
        "  FROM pg_default_acl d WHERE d.defaclrole = r.oid AND d.defaclobjtype = 'f' "
        "  AND d.defaclnamespace = 0), acldefault('f', r.oid))) a WHERE a.grantee = 0) "
        "FROM pg_roles r WHERE r.rolname = %s",
        [OWNER],
    )
    row = cursor.fetchone()
    if row is not None and row[0]:
        findings.add(
            "extra",
            f"default privileges of {OWNER}: PUBLIC gets EXECUTE on new functions",
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} REVOKE EXECUTE ON FUNCTIONS "
                "FROM PUBLIC"
            ).format(sql.Identifier(OWNER)),
            PUBLIC,
        )
    cursor.execute(
        "SELECT d.defaclobjtype, coalesce(n.nspname, ''), "
        f"{_GRANTEE}, a.privilege_type FROM pg_default_acl d "
        "LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace "
        "CROSS JOIN LATERAL aclexplode(d.defaclacl) a "
        "WHERE d.defaclrole = (SELECT oid FROM pg_roles WHERE rolname = %s) "
        "AND a.grantee <> d.defaclrole",
        [OWNER],
    )
    have = defaultdict(set)
    for objtype, schema, grantee, privilege in cursor.fetchall():
        if grantee == PUBLIC and (objtype == "T" or (objtype == "f" and not schema)):
            # Types: Postgres's own USAGE to PUBLIC; functions: checked above
            continue
        have[(objtype, schema, grantee)].add(privilege)
    wanted = {("n", "", APP): {"USAGE"}, ("n", "", WATCH): {"USAGE"}}
    for key in sorted(set(have) | set(wanted)):
        objtype, schema, grantee = key
        what = _OBJECTS.get(objtype, objtype)
        where = (
            sql.SQL(" IN SCHEMA {}").format(sql.Identifier(schema))
            if schema
            else sql.SQL("")
        )
        if extra := have.get(key, set()) - wanted.get(key, set()):
            findings.add(
                "extra",
                f"default privileges of {OWNER}{' in ' + schema if schema else ''}: "
                f"{grantee} gets {', '.join(sorted(extra))} on new {what.lower()}",
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {}{} REVOKE {} ON {} FROM {}"
                ).format(
                    sql.Identifier(OWNER),
                    where,
                    _privileges(extra),
                    sql.SQL(what),
                    _who(grantee),
                ),
                grantee,
            )
        if wanted.get(key, set()) - have.get(key, set()):
            findings.add(
                "missing",
                f"default privileges of {OWNER}: {grantee} lacks USAGE on new schemas",
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} GRANT USAGE ON SCHEMAS TO {}"
                ).format(sql.Identifier(OWNER), _who(grantee)),
                grantee,
            )
    return findings


# --- the entry points -----------------------------------------------------------------------


def hospital_schemas(cursor):
    """From the catalogue, never from Hospital rows (S7)."""
    cursor.execute(
        "SELECT nspname FROM pg_namespace WHERE nspname ~ %s ORDER BY 1",
        [HOSPITAL_SCHEMA_RE.pattern],
    )
    return [row[0] for row in cursor.fetchall()]


def _check_roles_exist(cursor, current_mode):
    cursor.execute(
        "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
        [sorted(_managed_roles(current_mode))],
    )
    missing = sorted(
        _managed_roles(current_mode) - {row[0] for row in cursor.fetchall()}
    )
    if missing:
        msg = (
            f"IHR_DB_ROLES={current_mode}, but role(s) {', '.join(missing)} do not exist "
            "(test: the test runner makes them; required: they are made with the move of "
            "the roles, P1-T19b)"
        )
        raise RolesError(msg)


def report(connection, schemas=None, *, database=True):
    """Every difference from the model; changes nothing. ``schemas``: None for public and
    every hospital schema. Off: nothing."""
    return _run(connection, schemas, repair=False, database=database)


def ensure_grants(connection, schemas=None, *, database=None):
    """Converge ``schemas`` (a name, a list, or None for public and every hospital schema)
    to the model: grant what is missing and revoke what goes beyond it. Returns the findings
    (what it changed, and problems only an operator can fix, which raise RolesError in
    required mode). Off: does nothing."""
    if isinstance(schemas, str):
        schemas = [schemas]
    if database is None:
        database = schemas is None or "public" in schemas
    return _run(connection, schemas, repair=True, database=database)


def _run(connection, schemas, *, repair, database):
    if _off():
        return Findings()
    current_mode = mode()
    with connection.cursor() as cursor:
        _check_roles_exist(cursor, current_mode)
        if schemas is None:
            schemas = ["public", *hospital_schemas(cursor)]
    schemas = list(schemas)
    findings = _findings(connection, schemas, current_mode, database)
    if repair:
        _apply(
            connection,
            findings,
            lambda: _findings(connection, schemas, current_mode, database),
        )
        problems = findings.of("problem")
        if problems and current_mode == "required":
            msg = "database roles: " + "; ".join(p.text for p in problems[:20])
            raise RolesError(msg)
    return findings


def _findings(connection, schemas, current_mode, database):
    from ihr_platform import queue_guard

    findings = Findings()
    with connection.cursor() as cursor:
        kinds = {schema: schema_kind(schema) for schema in schemas}
        state = read_state(cursor, schemas)
        classes = {"public": public_classes(), "hospital": hospital_classes()}
        if database:
            findings.items += database_findings(cursor, current_mode).items
            findings.items += [
                Finding(kind, text)
                for kind, text in role_problems(cursor, current_mode)
            ]
            if current_mode == "required":
                findings.items += default_findings(cursor).items
        for schema in schemas:
            findings.items += schema_findings(
                state, schema, current_mode, classes[kinds[schema]]
            ).items
        if "public" in schemas:
            # What the app may put into the provisioner's queue (S1)
            findings.items += [
                Finding("problem", text, schema="public", control=True)
                for text in queue_guard.problems(cursor)
            ]
    return findings


def _apply(connection, findings, again):
    """Revokes first, then grants, in one transaction. A table's REVOKE also takes the
    privilege off each of its columns (Postgres, REVOKE), so after any revoke the state is
    read again and every grant then missing is made (C1). Adds what it found missing only
    then to ``findings``."""
    revokes = [f.statement for f in findings.of("extra") if f.statement is not None]
    grants = [f.statement for f in findings.of("missing") if f.statement is not None]
    if not revokes and not grants:
        return
    with transaction.atomic(using=connection.alias), connection.cursor() as cursor:
        # Wait a little for a table's readers, never queue every later query behind us
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        for statement in revokes:
            cursor.execute(statement)
        if revokes:
            after = again().of("missing")
            known = {str(f) for f in findings}
            findings.items += [f for f in after if str(f) not in known]
            grants = [f.statement for f in after if f.statement is not None]
        for statement in grants:
            cursor.execute(statement)
    logger.info("database grants: %s revoked, %s granted", len(revokes), len(grants))


def applied(findings):
    """How many differences a repair changed (never the problems it could not)."""
    return len(findings.of("extra")) + len(findings.of("missing"))


# A required-mode refusal found on post_migrate is raised at the end of the signal (C6), so the
# other apps' receivers (content types, permissions) still run first
_DEFERRED = []


def repair_after_migrate(connection, schema):
    """post_migrate (ihr_audit/apps.py, after the audit triggers): the schema just migrated,
    and the database itself after the public schema's run. A RolesError waits for
    ``raise_deferred``, the last post_migrate receiver."""
    if schema == "public" or HOSPITAL_SCHEMA_RE.match(schema or ""):
        try:
            ensure_grants(connection, [schema], database=schema == "public")
        except RolesError as exc:
            _DEFERRED.append(str(exc))


def raise_deferred(**kwargs):
    """The last post_migrate receiver: raise what repair_after_migrate found (required)."""
    if _DEFERRED:
        found = list(_DEFERRED)
        _DEFERRED.clear()
        raise RolesError("; ".join(found))


def clear_deferred(**kwargs):
    """pre_migrate: each migrate run starts with nothing deferred, so a refusal left by a
    post_migrate emission that stopped before its last receiver is never raised by a later,
    unrelated migrate of the same process (second review of C6)."""
    _DEFERRED.clear()


# --- the test cluster's roles ---------------------------------------------------------------


def create_test_roles(cursor):
    """Test mode: make (or reset) the roles in the test cluster, as its superuser. Roles are
    cluster-wide, so this is idempotent and every other database is left alone. They have no
    password: nobody logs in as them, the tests switch to them with SET ROLE."""
    for name in TEST_ROLES:
        role = ROLES[name]
        ident = sql.Identifier(name)
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [name])
        if cursor.fetchone() is None:
            cursor.execute(sql.SQL("CREATE ROLE {}").format(ident))
        cursor.execute(
            sql.SQL(
                "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                "NOBYPASSRLS {} PASSWORD NULL"
            ).format(ident, sql.SQL("INHERIT" if role.inherit else "NOINHERIT"))
        )
        # Its stored settings in every database, not only role-wide (review S1/C3)
        cursor.execute(
            "SELECT d.datname FROM pg_db_role_setting s "
            "LEFT JOIN pg_database d ON d.oid = s.setdatabase "
            "JOIN pg_roles r ON r.oid = s.setrole WHERE r.rolname = %s",
            [name],
        )
        for (database,) in cursor.fetchall():
            if database is None:
                cursor.execute(sql.SQL("ALTER ROLE {} RESET ALL").format(ident))
            else:
                cursor.execute(
                    sql.SQL("ALTER ROLE {} IN DATABASE {} RESET ALL").format(
                        ident, sql.Identifier(database)
                    )
                )
        cursor.execute(
            "SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
            "JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = %s",
            [name],
        )
        for (group,) in cursor.fetchall():
            if group not in role.memberships:
                cursor.execute(
                    sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(group), ident)
                )
        for group in role.memberships:
            cursor.execute(
                sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), ident)
            )


# --- the run-time check (required mode) -----------------------------------------------------

REFUSAL_PREFIX = "ihr-db-role: refused"
GATED_PREFIX = "ihr-db-role: gated"
REASONS = (
    "wrong_role",
    "superuser",
    "attributes",
    "membership",
    "role_settings",
    "database_create",
    "database_temporary",
    "schema_create",
    "replication_role",
    "owns_objects",
    "extra_privileges",
    "not_owner",
    "process",
    "undeclared",
    "check_failed",
    "roles_off",
    "controls",
)
# Why a hospital schema is gated (C2): a right beyond the model for the app or PUBLIC there, or
# a broken control there (its sealing functions: security, search path, owner, missing or
# changed by hand, the stamp trigger off). The worse one first
GATED_REASONS = ("controls", "extra_privileges")
# What a container's pre-start check (ihr_db_role_check --for) may guard (S5). Only these may
# run as the owner: the migrate one-shot, the admin one-offs and the onboarding provisioner
OWNER_PROCESSES = ("migrate", "admin", "provisioner")
PROCESSES = (
    "backend",
    "celery-worker",
    "celery-beat",
    "staff-worker",
    *OWNER_PROCESSES,
)
# refused: the reason, once refused. checked: the first connection passed. declared: what it
# declared then. gated: hospital schemas this process does not serve, each with why (a tuple of
# GATED_REASONS; C2). off_checked: off mode's check ran (S10)
_STATE = {
    "refused": None,
    "checked": False,
    "declared": "",
    "gated": {},
    "off_checked": False,
}
_LOCK = threading.Lock()


def declared():
    return getattr(settings, "IHR_DB_ROLE", "") or ""


def refusal_line(declared_role, reason):
    """The fixed line the log alert matches (deploy/logs/alerts/db-roles.yml)."""
    return f"{REFUSAL_PREFIX} role={declared_role} reason={reason}"


def gated_line(schema, reason="extra_privileges"):
    """One hospital schema this process does not serve, and why (C2). The alert
    DatabaseSchemaGated (deploy/logs/alerts/db-roles.yml) fires on it: the worker writes it
    again every minute (tasks.report_unserved), so the alert lasts as long as the gating, for
    an archived hospital's schema too; HospitalNotServed also names a live gated hospital."""
    return f"{GATED_PREFIX} schema={schema} reason={reason}"


def gated_lines():
    """This process's gated lines, one per schema and reason (tasks.report_unserved)."""
    return [
        gated_line(schema, reason)
        for schema, reasons in sorted(_STATE["gated"].items())
        for reason in reasons
        if HOSPITAL_SCHEMA_RE.match(schema) and reason in GATED_REASONS
    ]


def _emit(line):
    # Straight to standard error as one whole line: a logger would put a prefix in front
    os.write(2, f"{line}\n".encode())


def gated_schemas():
    """Hospital schemas this process found rights beyond the model in (the app's or
    PUBLIC's) on its first connection: it serves none of them (release.schema_ready,
    release.ready_q). Kept until the process restarts: repair the grants (ihr_fleet_run
    migrate), then restart the app containers."""
    return frozenset(_STATE["gated"])


def _command_option(command, args, dest):
    """What ``manage.py <command>`` would parse ``dest`` as, with the command's own parser
    (abbreviations such as --rol included, S5); None if it would not parse."""
    from django.core.management import load_command_class

    try:
        parser = load_command_class("ihr_platform", command).create_parser(
            "manage.py", command
        )
        return getattr(parser.parse_args(list(args)), dest, None)
    except (Exception, SystemExit):
        return None


def owner_process_allowed(argv=None, process=None):
    """Only the migrate one-shot, the admin one-offs and the onboarding provisioner may run
    as the owner: gunicorn, celery and the staff worker always run as the app role, whatever
    the environment says (S6). ``process``: what a pre-start check guards (S5); otherwise
    this process's own command line, read with the command's own parser."""
    if process is not None:
        return process in OWNER_PROCESSES
    argv = list(sys.argv if argv is None else argv)
    if not argv or Path(argv[0]).name != "manage.py":
        return False
    command = argv[1] if len(argv) > 1 else ""
    if command == "ihr_db_role_check":
        # The check judges the container it guards, never its own command line (S5)
        return _command_option(command, argv[2:], "guards") in OWNER_PROCESSES
    if command == "ihr_provision_worker":
        return _command_option(command, argv[2:], "role") == "onboarding"
    return True


_IDENTITY = (
    "SELECT current_user::text, session_user::text, r.rolsuper, "
    "r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls, "
    "EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = r.oid), " + _STORED_IN
)


def _identity_problems(user, session, superuser, attributes, member, stored, role=APP):
    found = []
    if user != role or session != role:
        found.append(("wrong_role", f"connected as {session} ({user}), not {role}"))
    if superuser:
        found.append(("superuser", f"{user} is a superuser"))
    if attributes:
        found.append(
            ("attributes", f"{user} has CREATEROLE, CREATEDB, REPLICATION or BYPASSRLS")
        )
    if member:
        found.append(("membership", f"{user} is a member of another role"))
    if stored:
        found.append(
            ("role_settings", f"{user} has {_stored_text(stored)} (ALTER ROLE ... SET)")
        )
    return found


def reconnect_problems(cursor, role=APP):
    """Every new connection after the first (S2): an ordinary role can ALTER ROLE itself (SET
    a default, a password), so its identity, attributes, memberships and stored settings are
    read again at each login. One small catalogue query."""
    cursor.execute(_IDENTITY + " FROM pg_roles r WHERE r.rolname = current_user")
    return _identity_problems(*cursor.fetchone(), role=role)


# What the watch role may never hold anywhere (P1-T21 A11): it only reads
WATCH_NEVER = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


def watch_problems(cursor):
    """Why this connection must not serve as the audit watch's role: its identity (as the
    app's), no CREATE or TEMPORARY anywhere, nothing owned, and no right to change any table
    or to execute a sealing function, whatever its source (has_*_privilege)."""
    cursor.execute(
        _IDENTITY + ", "
        "has_database_privilege(current_database(), 'CREATE'), "
        "has_database_privilege(current_database(), 'TEMPORARY'), "
        "(SELECT count(*) FROM pg_namespace n WHERE has_schema_privilege(n.oid, 'CREATE') "
        " AND n.nspname !~ '^pg_(toast_)?temp_'), "
        "EXISTS (SELECT 1 FROM pg_class c WHERE c.relowner = r.oid) "
        "OR EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspowner = r.oid) "
        "OR EXISTS (SELECT 1 FROM pg_proc p WHERE p.proowner = r.oid), "
        "(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        " CROSS JOIN unnest(%s::text[]) w(privilege) "
        " WHERE (n.nspname = 'public' OR n.nspname ~ %s) AND c.relkind = ANY(%s) "
        " AND has_table_privilege(c.oid, w.privilege)), "
        "(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        " WHERE n.nspname ~ %s AND p.proname IN ('ihr_audit_seal_next', 'ihr_audit_seal_run') "
        " AND has_function_privilege(p.oid, 'EXECUTE')) "
        "FROM pg_roles r WHERE r.rolname = current_user",
        [
            list(WATCH_NEVER),
            HOSPITAL_SCHEMA_RE.pattern,
            list(RELKINDS),
            HOSPITAL_SCHEMA_RE.pattern,
        ],
    )
    row = cursor.fetchone()
    found = _identity_problems(*row[:6], role=WATCH)
    db_create, db_temp, schema_create, owns, writes, seals = row[6:]
    if db_create:
        found.append(("database_create", "CREATE on the database"))
    if db_temp:
        found.append(("database_temporary", "TEMPORARY on the database"))
    if schema_create:
        found.append(("schema_create", f"CREATE on {schema_create} schema(s)"))
    if owns:
        found.append(("owns_objects", f"{row[0]} owns objects in the database"))
    if writes or seals:
        found.append(
            (
                "extra_privileges",
                f"{row[0]} may change {writes} table(s) or seal {seals} schema(s): it only "
                "reads (ihr_fleet_run migrate repairs the grants)",
            )
        )
    return found


def app_problems(cursor):
    """Why this connection must not serve as the app role: [(reason, text)]."""
    cursor.execute(
        _IDENTITY + ", "
        "has_database_privilege(current_database(), 'CREATE'), "
        "has_database_privilege(current_database(), 'TEMPORARY'), "
        "has_parameter_privilege('session_replication_role', 'SET'), "
        "pg_has_role((SELECT datdba FROM pg_database WHERE datname = current_database()), "
        "'MEMBER'), "
        "(SELECT count(*) FROM pg_namespace n WHERE has_schema_privilege(n.oid, 'CREATE') "
        " AND n.nspname !~ '^pg_(toast_)?temp_'), "
        "EXISTS (SELECT 1 FROM pg_class c WHERE c.relowner = r.oid) "
        "OR EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspowner = r.oid) "
        "OR EXISTS (SELECT 1 FROM pg_proc p WHERE p.proowner = r.oid) "
        # Large objects (S8): any role may make one and owns it; a leftover is a sign
        "OR EXISTS (SELECT 1 FROM pg_largeobject_metadata l WHERE l.lomowner = r.oid) "
        "FROM pg_roles r WHERE r.rolname = current_user"
    )
    row = cursor.fetchone()
    found = _identity_problems(*row[:6])
    db_create, db_temp, replication, db_owner, schema_create, owns = row[6:]
    user = row[0]
    if db_create:
        found.append(("database_create", "CREATE on the database"))
    if db_temp:
        found.append(
            (
                "database_temporary",
                "TEMPORARY on the database (PUBLIC's default grant? ensure_grants "
                "revokes it)",
            )
        )
    if replication:
        found.append(("replication_role", "may SET session_replication_role"))
    if db_owner or owns:
        found.append(("owns_objects", f"{user} owns the database or objects in it"))
    if schema_create:
        found.append(("schema_create", f"CREATE on {schema_create} schema(s)"))
    return found


def owner_problems(cursor, argv=None, process=None):
    cursor.execute(
        "SELECT current_user::text, session_user::text, r.rolsuper, "
        "pg_has_role((SELECT datdba FROM pg_database WHERE datname = current_database()), "
        "'MEMBER') FROM pg_roles r WHERE r.rolname = current_user"
    )
    user, session, superuser, db_owner = cursor.fetchone()
    found = []
    if user != OWNER or session != OWNER:
        found.append(("wrong_role", f"connected as {session} ({user}), not {OWNER}"))
    if superuser:
        found.append(("superuser", f"{user} is a superuser"))
    if not db_owner:
        found.append(("not_owner", f"{user} does not own the database"))
    if not owner_process_allowed(argv, process):
        found.append(("process", "this process always runs as the app role"))
    return found


def off_problems(cursor):
    """Off mode (S10): a process that connects as one of the split roles belongs to a moved
    database, which needs IHR_DB_ROLES=required. On a server (the deployment settings, which
    name IHR_ENVIRONMENT) a superuser login is refused too: no deployed process logs in as
    one, so a container that lost both variables and connects as postgres never runs
    unchecked (second review of S10)."""
    cursor.execute(
        "SELECT current_user::text, session_user::text, "
        "(SELECT r.rolsuper FROM pg_roles r WHERE r.rolname = session_user)"
    )
    user, session, superuser = cursor.fetchone()
    found = []
    if {user, session} & {OWNER, APP, WATCH}:
        found.append(
            (
                "roles_off",
                f"connected as {session} ({user}) with IHR_DB_ROLES=off: a database whose "
                "roles were moved needs IHR_DB_ROLES=required",
            )
        )
    if superuser and getattr(settings, "IHR_ENVIRONMENT", ""):
        found.append(
            (
                "superuser",
                f"logged in as {session}, a superuser, on a server: no deployed process "
                "does",
            )
        )
    return found


def effective_public_problems(cursor):
    """has_table_privilege over every public table: the role's effective table rights,
    whatever their source, beyond the table's class."""
    classes = public_classes()
    cursor.execute(
        "SELECT c.relname, p.privilege FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public' "
        "CROSS JOIN unnest(%s::text[]) p(privilege) "
        "WHERE c.relkind = ANY(%s) AND has_table_privilege(c.oid, p.privilege)",
        [sorted(DML | NEVER), list(RELKINDS)],
    )
    held = defaultdict(set)
    for table, privilege in cursor.fetchall():
        held[table].add(privilege)
    return [
        ("extra_privileges", f"public.{table}: {', '.join(sorted(extra))}")
        for table, privileges in sorted(held.items())
        if (extra := privileges - CLASSES[classes.get(table, "none")].table)
    ]


def check_connection(  # noqa: PLR0912 (one flat list of verdicts)
    connection, declared_role=None, argv=None, gated=None, process=None
):
    """[(reason, text)] for this connection and the role it declares; empty when fine.

    For the app role: its attributes, memberships, stored settings, CREATE and TEMPORARY
    anywhere, ownership, its effective rights on public tables, ensure_grants' report and
    the sealing functions of every hospital schema. A right beyond the model for the app or
    PUBLIC, or a control problem (the queue guard, the sealing functions, large objects:
    ``Finding.control``), refuses the process when it is the database's or the public
    schema's; when it is in one hospital's schema, that hospital alone is gated (C2): it goes
    into ``gated`` {schema: [(reason, text)]}, and the process still serves the others. A
    definer function the app may execute refuses it wherever it is (``Finding.escalates``).
    ``process``: what a pre-start check guards (S5)."""
    from ihr_audit.seal import sealing_problems_by_schema

    declared_role = declared_role or declared()
    if declared_role not in DECLARED:
        return [("undeclared", "IHR_DB_ROLE must be app, owner or watch")]
    with connection.cursor() as cursor:
        if declared_role == "owner":
            return owner_problems(cursor, argv, process)
        if declared_role == "watch":
            return watch_problems(cursor)
        found = app_problems(cursor)
        if found:
            return found
        found += effective_public_problems(cursor)
    findings = report(connection)
    with connection.cursor() as cursor:
        # A sealing function missing or changed by hand, or the stamp trigger off: what the
        # hospital's chain rests on (second review, S1/S3 at run time; an older build's,
        # unchanged as installed, is not: its next migrate brings it up to date)
        sealing = sealing_problems_by_schema(cursor, hospital_schemas(cursor))
    findings.items += [
        Finding("problem", text, schema=schema, control=True)
        for schema, texts in sorted(sealing.items())
        for text in texts
    ]
    other = 0
    for item in findings:
        if item.kind == "notice":
            # Reported only (a missing LOGIN on a role no service uses yet: P1-T19b makes
            # the watch and dump roles without one); nothing for migrate to repair
            continue
        if item.kind == "extra" and item.grantee in (APP, PUBLIC):
            reason = "extra_privileges"
        elif item.kind == "problem" and item.control:
            reason = "controls"
        else:
            # a missing right fails that table, not the process; drift elsewhere is the
            # owner's to repair (ihr_fleet_run migrate)
            other += 1
            continue
        if HOSPITAL_SCHEMA_RE.match(item.schema) and not item.escalates:
            if gated is not None:
                gated.setdefault(item.schema, []).append((reason, item.text))
        else:
            found.append((reason, item.text))
    if other:
        # One line, not one per difference (C4): ihr_db_role_check --report names them
        logger.warning(
            "ihr-db-role: %s difference(s) from the model the app gains nothing from "
            "(ihr_fleet_run migrate repairs them; ihr_db_role_check --report names them)",
            other,
        )
    return found


def _refuse(connection, declared_role, found):
    reason = found[0][0] if found[0][0] in REASONS else "check_failed"
    _STATE["refused"] = reason
    for _code, text in found[:20]:
        logger.error("ihr-db-role: %s", text)
    _emit(refusal_line(declared_role if declared_role in DECLARED else "none", reason))
    # Never left open (C1): Django keeps a connection whose connection_created receiver
    # raised, and the next request would use it unchecked
    connection.close()
    msg = (
        f"Refusing to use the database as {declared_role or 'an undeclared role'}: "
        f"{found[0][1]} (manage.py ihr_db_role_check names everything; P1-T19)"
    )
    raise RoleRefused(msg)


def _gate(gated):
    _STATE["gated"] = {
        schema: tuple(r for r in GATED_REASONS if any(r == x for x, _ in items))
        for schema, items in gated.items()
    }
    for _schema, items in sorted(gated.items()):
        for _reason, text in items[:5]:
            logger.error("ihr-db-role: %s", text)
    for line in gated_lines():
        _emit(line)


def on_connection_created(sender=None, connection=None, **kwargs):
    """Required mode: the first connection of a process checks its role; each later one of
    an app process re-reads the role's identity and stored settings (S2). A refusal is
    sticky: every later connection of the process is closed and refused at once."""
    if connection is None or connection.alias != "default":
        return
    if _STATE["refused"] is not None:
        connection.close()
        msg = (
            f"Refusing to use the database: this process's role was refused "
            f"({_STATE['refused']}); fix it and restart the container (P1-T19)"
        )
        raise RoleRefused(msg)
    if _STATE["checked"]:
        if _STATE["declared"] in ("app", "watch"):
            role = APP if _STATE["declared"] == "app" else WATCH
            try:
                with connection.cursor() as cursor:
                    found = reconnect_problems(cursor, role)
            except Exception as exc:
                found = [
                    ("check_failed", f"the role check failed: {type(exc).__name__}")
                ]
            if found:
                _refuse(connection, _STATE["declared"], found)
        return
    with _LOCK:
        if _STATE["checked"]:
            return
        declared_role = declared()
        gated = {}
        began = time.monotonic()
        try:
            found = check_connection(connection, declared_role, gated=gated)
        except Exception as exc:
            found = [("check_failed", f"the role check failed: {type(exc).__name__}")]
        if found:
            _refuse(connection, declared_role, found)
        _gate(gated)
        _STATE["declared"] = declared_role
        _STATE["checked"] = True
        logger.info(
            "ihr-db-role: %s role checked in %.1f s",
            declared_role,
            time.monotonic() - began,
        )


def on_connection_created_off(sender=None, connection=None, **kwargs):
    """Off mode: the first connection of a process must not be one of the split roles."""
    if connection is None or connection.alias != "default":
        return
    if _STATE["refused"] is not None:
        connection.close()
        msg = f"Refusing to use the database ({_STATE['refused']}; P1-T19)"
        raise RoleRefused(msg)
    if _STATE["off_checked"]:
        return
    with _LOCK:
        if _STATE["off_checked"]:
            return
        try:
            with connection.cursor() as cursor:
                found = off_problems(cursor)
        except Exception as exc:
            found = [("check_failed", f"the role check failed: {type(exc).__name__}")]
        if found:
            _refuse(connection, declared(), found)
        _STATE["off_checked"] = True


def install_check():
    """Connect the run-time check (AppConfig.ready): the full one in required mode, the
    off-mode one in off mode (S10); nothing in test mode (the tests switch roles)."""
    from django.db.backends.signals import connection_created

    current = mode()
    if current == "required":
        connection_created.connect(
            on_connection_created, weak=False, dispatch_uid="ihr_db_role_check"
        )
    elif current == "off":
        connection_created.connect(
            on_connection_created_off, weak=False, dispatch_uid="ihr_db_role_check"
        )
