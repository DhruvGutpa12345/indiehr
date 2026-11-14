# ruff: noqa: S608 (identifiers quoted by _ident from the catalogue; values are parameters)
"""A hospital's restore package (P1-T22b; design "After the attack" 8): what an untrusted dump
becomes after the quarantine, and how it is loaded into the live cluster.

A dump is untrusted SQL: it is never fed to the live cluster. The quarantine
(deploy/restore/quarantine.py, a throwaway Postgres with no network) restores it as a role that
owns only an empty schema, checks what came out (``schema_problems``, ``database_problems``) and
then writes a **package** from that catalogue with this module (``export``):

    package.json       what it is: the schema, the facts (migrations, the audit head, the
                       highest transaction id, sequence values, row counts), the shape hash,
                       the sha256 of every other file, the checks' report
    ddl.json           the statements, in load order, each built here from the catalogue
                       (quote_ident, format_type, pg_get_expr/constraintdef/indexdef): schema,
                       sequences, tables, then (after the data) sequence values, keys and
                       checks, indexes, foreign keys, sequence ownership
    data/NNNN.copy     each table's rows, COPY text format

No function, trigger, view, rule, policy, grant, comment, extension or role is ever exported:
the live schema gets its functions and triggers from this build's code (ihr_audit.install,
ihr_audit.seal) inside the load's own transaction. ``load`` checks every file's sha256 and
every statement's form (``_check_statement``: one whole statement of a form ``export`` writes,
nothing after it), sends each alone through the extended protocol (the server refuses a second
command in it), and never lets the live cluster evaluate an expression of the dump before it
has inspected it there (``expression_problems``: only pure built-ins and a short list of
others, only this schema's relations): tables, CHECKs (NOT VALID), exclusion constraints and
expression or partial indexes first, on empty tables; then the inspection; then the rows;
then keys, plain indexes, foreign keys and the CHECKs' validation. Afterwards the result's
``shape``, sequences and row counts must be the package's: any difference rolls the whole load
back. The restore also compares the package's shape with the live schema's when their
migrations are the same (engine precheck).

Plain psycopg (no Django): the quarantine runs it in the dumper image; the restore engine runs it
on Django's own connection. Every catalogue query is read with ``search_path`` empty, so every
name in an expression comes out schema-qualified.
"""

import hashlib
import json
import re
from pathlib import Path

FORMAT = "ihr-restore-package/1"
SCHEMA_RE = re.compile(r"^h_[a-z0-9]{10}$")
# Our functions and triggers in a hospital schema (ihr_audit 0001, install.py, seal.py): the
# only ones a dump may hold. None of them is ever exported (this build's code installs them).
OUR_FUNCTIONS = frozenset(
    {
        "ihr_audit_row",
        "ihr_audit_truncate",
        "ihr_audit_last4",
        "ihr_audit_refuse",
        "ihr_audit_seal_run",
        "ihr_audit_seal_next",
        "ihr_audit_stamp",
    }
)
OUR_TRIGGERS = {
    "ihr_audit": "ihr_audit_row",
    "ihr_audit_truncate": "ihr_audit_truncate",
    "ihr_audit_append_only": "ihr_audit_refuse",
    "ihr_audit_no_truncate": "ihr_audit_refuse",
    "ihr_audit_stamp": "ihr_audit_stamp",
}
TABLE_KINDS = ("r",)
CHUNK = 1 << 20
_IDENT = r'(?:"(?:[^"]|"")+"|[a-z_][a-z0-9_$]*)'
_SCHEMA = r'(?P<s>"?h_[a-z0-9]{10}"?)'
_STATEMENT = {
    # phase: the statement forms ``load`` accepts (built by ``export``; anything else is refused).
    # Each is matched whole (``fullmatch``); ``_one_statement`` first makes sure the text is one
    # statement (no ";" or comment outside a literal, a quoted name or a dollar quote)
    "schema": re.compile(r"CREATE SCHEMA " + _SCHEMA),
    "sequence": re.compile(
        rf"CREATE SEQUENCE {_SCHEMA}\.{_IDENT} AS (?:smallint|integer|bigint) START WITH "
        r"-?\d+ INCREMENT BY -?\d+ MINVALUE -?\d+ MAXVALUE -?\d+ CACHE \d+ (?:NO )?CYCLE"
    ),
    # (its head: the column list must then be the first parenthesis, and only the options'
    # may follow it; ``_check_statement``)
    "table": re.compile(rf"CREATE (?:UNLOGGED )?TABLE {_SCHEMA}\.{_IDENT} (?=\()"),
    "setval": re.compile(r"SELECT pg_catalog\.setval\(%s::regclass, %s, %s\)"),
    # (its head: what follows the kind must then be ONE constraint of that kind, nothing
    # after it but its own options: _CONSTRAINT_FORMS, ``_check_constraint``)
    "constraint": re.compile(
        rf"ALTER TABLE ONLY {_SCHEMA}\.{_IDENT} ADD CONSTRAINT {_IDENT} "
        r"(?P<kind>PRIMARY KEY|UNIQUE|CHECK|EXCLUDE|FOREIGN KEY)(?= )"
    ),
    "index": re.compile(
        rf"CREATE (?:UNIQUE )?INDEX {_IDENT} ON (?:ONLY )?{_SCHEMA}\.{_IDENT} USING [a-z]+ "
        r"\(.+",
        re.DOTALL,
    ),
    "owned": re.compile(
        rf"ALTER SEQUENCE {_SCHEMA}\.{_IDENT} OWNED BY "
        rf"(?P<t>\"?h_[a-z0-9]{{10}}\"?)\.{_IDENT}\.{_IDENT}"
    ),
}
_TABLE_TAIL = re.compile(
    r"(?: WITH \([a-z_]+=[A-Za-z0-9_.]+(?:, [a-z_]+=[A-Za-z0-9_.]+)*\))?"
)
# A constraint after its kind, with every top-level parenthesis shown as "()" (the contents
# are checked apart): what pg_get_constraintdef writes, and nothing after it (review S2 after
# the second verification). ALTER TABLE takes several subcommands separated by commas, and
# one of a late statement (", ALTER COLUMN x TYPE text USING (...)", ", OWNER TO ...",
# ", DISABLE TRIGGER ALL") would run as the owner after the rows and the inspection, and a
# USING clause is never stored, so no later check could see it. No form below has a comma
# outside its parentheses.
_DEFERRAL = r"(?: DEFERRABLE)?(?: INITIALLY DEFERRED)?(?: NOT ENFORCED)?(?: NOT VALID)?"
_KEY_TAIL = rf"(?: INCLUDE \(\))?(?: WITH \(\))?(?: USING INDEX TABLESPACE {_IDENT})?"
_ACTION = r"(?:NO ACTION|RESTRICT|CASCADE|SET NULL|SET DEFAULT)"
_CONSTRAINT_FORMS = {
    "PRIMARY KEY": re.compile(rf"PRIMARY KEY \(\){_KEY_TAIL}{_DEFERRAL}"),
    "UNIQUE": re.compile(rf"UNIQUE(?: NULLS NOT DISTINCT)? \(\){_KEY_TAIL}{_DEFERRAL}"),
    "CHECK": re.compile(rf"CHECK \(\)(?: NO INHERIT)?{_DEFERRAL}"),
    "EXCLUDE": re.compile(
        rf"EXCLUDE USING [a-z]+ \(\){_KEY_TAIL}(?: WHERE \(\))?{_DEFERRAL}"
    ),
    "FOREIGN KEY": re.compile(
        rf"FOREIGN KEY \(\) REFERENCES (?P<rs>\"?[a-z_][a-z0-9_]*\"?)\.{_IDENT}\(\)"
        rf"(?: MATCH FULL)?(?: ON UPDATE {_ACTION})?(?: ON DELETE {_ACTION}(?: \(\))?)?"
        rf"{_DEFERRAL}"
    ),
}
# Inside a key's or a foreign key's parentheses: column names (or storage options) only. A
# CHECK's or an exclusion's hold expressions: those are stored and inspected
# (``expression_problems``) before any row meets them
_KEY_LIST = re.compile(
    rf"{_IDENT}(?:, {_IDENT})*(?:, PERIOD {_IDENT})?(?: WITHOUT OVERLAPS)?"
    r"|[a-z_]+=[A-Za-z0-9_.]+(?:, [a-z_]+=[A-Za-z0-9_.]+)*"
)
# An index on columns only (no expression, no predicate, no operator class named: the
# default one of a built-in or an extension's type): built after the rows, as pg_restore does.
# Anything else is built before them, on the empty table, and inspected first (an operator
# class's functions run for every row, and a name could resolve anywhere on the search path)
_QIDENT = rf"{_IDENT}(?:\.{_IDENT})?"
_COLUMN = rf"{_IDENT}(?: COLLATE {_QIDENT})?(?: DESC)?(?: NULLS (?:FIRST|LAST))?"
_PLAIN_INDEX = re.compile(
    rf"CREATE (?:UNIQUE )?INDEX {_IDENT} ON (?:ONLY )?\"?h_[a-z0-9]{{10}}\"?\.{_IDENT} "
    rf"USING [a-z]+ \({_COLUMN}(?:, {_COLUMN})*\)(?: INCLUDE \({_IDENT}(?:, {_IDENT})*\))?"
    r"(?: NULLS NOT DISTINCT)?(?: WITH \([a-z_]+=[A-Za-z0-9_.]+(?:, [a-z_]+=[A-Za-z0-9_.]+)*\))?"
)
# What an expression in a hospital schema (a default, a CHECK, an index expression or
# predicate, a generated column) may call: IMMUTABLE functions of pg_catalog or of an
# extension, and these few of pg_catalog (our audit tables' defaults, CARE's identity columns).
# Never one that reads another table (query_to_xml, table_to_xml), changes state outside the
# row (setval, set_config, pg_advisory_lock, lo_*) or reaches another session
# (pg_terminate_backend): S1 of the P1-T22b review.
ALLOWED_VOLATILE = frozenset(
    {
        "clock_timestamp",
        "gen_random_uuid",
        "nextval",
        "now",
        "pg_current_xact_id",
        "statement_timestamp",
        "transaction_timestamp",
    }
)
_FUNCTION_REFS = re.compile(
    r":(?:funcid|opfuncid|hashfuncid|negfuncid|aggfnoid|winfnoid) (\d+)"
)
_OPERATOR_REFS = re.compile(r":opno (\d+)")
_OPERATOR_LISTS = re.compile(r":opnos \(o((?: \d+)+)\)")
# nextval's one argument as the catalogue stores a sequence named in the expression itself:
# a regclass constant (pg_depend then records the sequence, so the reference check below sees
# it). A sequence named at run time (``('h_x.s'::name)::regclass`` is a CoerceViaIO,
# ``(16384::oid)::regclass`` a RelabelType, a column's value) leaves no dependency behind and
# could be another hospital's (review S1, after the second verification)
_REGCLASS_CONST = re.compile(r"\{CONST :consttype 2205 [^{}()]*\}")


class PackageError(RuntimeError):
    """The package is not one this module wrote, or it does not load as it says."""


def _rows(cur, text, params=()):
    cur.execute(text, params)
    return cur.fetchall()


class _QuietPath:
    """Every name in an expression schema-qualified while it is open; the search_path the
    session had (Django's, django-tenants') is put back afterwards."""

    def __init__(self, cur):
        self.cur = cur

    def __enter__(self):
        self.before = _rows(
            self.cur, "SELECT pg_catalog.current_setting('search_path')"
        )[0][0]
        self.cur.execute("SELECT pg_catalog.set_config('search_path', '', true)")
        return self

    def __exit__(self, kind, *rest):
        if (
            kind is None
        ):  # after an error the transaction is aborted: it rolls back anyway
            self.cur.execute(
                "SELECT pg_catalog.set_config('search_path', %s, true)", [self.before]
            )
        return False


def _ident(name):
    return '"' + name.replace('"', '""') + '"'


def _qualified(schema, name):
    return f"{_ident(schema)}.{_ident(name)}"


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


# --- what may be in a dump ---------------------------------------------------------------------


def schema_problems(cur, schema):  # noqa: PLR0912 (one check per kind of object)
    """What in ``schema`` is not a hospital schema's own (anywhere: the quarantine, the tests):
    functions or triggers other than ours (by name, kind and wiring), relations other than
    tables, indexes and sequences, types other than the tables' own, rules, policies, row
    security, statistics objects, and expressions (defaults, checks, indexes, generated
    columns) that call a function outside pg_catalog that no extension owns."""
    if not SCHEMA_RE.match(schema):
        return [f"not a hospital schema name: {schema!r}"]
    problems = []
    nsp = _rows(
        cur, "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = %s", [schema]
    )
    if not nsp:
        return [f"schema {schema} is not there"]
    oid = nsp[0][0]
    for name, kind, rettype in _rows(
        cur,
        "SELECT p.proname, p.prokind, p.prorettype::regtype::text FROM pg_catalog.pg_proc p "
        "WHERE p.pronamespace = %s ORDER BY 1",
        [oid],
    ):
        if name not in OUR_FUNCTIONS or kind != "f":
            problems.append(f"a function that is not ours: {name} ({kind}, {rettype})")
    for table, trigger, function, fschema, internal in _rows(
        cur,
        "SELECT c.relname, t.tgname, p.proname, pn.nspname, t.tgisinternal "
        "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid "
        "JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid "
        "JOIN pg_catalog.pg_namespace pn ON pn.oid = p.pronamespace "
        "WHERE c.relnamespace = %s ORDER BY 1, 2",
        [oid],
    ):
        if internal:  # a foreign key's own triggers (RI_FKey_*), made by the constraint
            continue
        if OUR_TRIGGERS.get(trigger) != function or fschema != schema:
            problems.append(
                f"a trigger that is not ours: {table}.{trigger} -> {fschema}.{function}"
            )
    for name, kind in _rows(
        cur,
        "SELECT relname, relkind FROM pg_catalog.pg_class WHERE relnamespace = %s "
        "AND relkind NOT IN ('r', 'i', 'S') ORDER BY 1",
        [oid],
    ):
        problems.append(
            f"a relation of kind {kind!r}: {name} (tables, indexes, sequences only)"
        )
    for name, persistence, rls, partition, inherits in _rows(
        cur,
        "SELECT c.relname, c.relpersistence, c.relrowsecurity OR c.relforcerowsecurity, "
        "c.relispartition, EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i "
        "WHERE i.inhrelid = c.oid OR i.inhparent = c.oid) "
        "FROM pg_catalog.pg_class c WHERE c.relnamespace = %s AND c.relkind = 'r' ORDER BY 1",
        [oid],
    ):
        if persistence not in ("p", "u"):
            problems.append(f"{name}: a temporary table")
        if rls:
            problems.append(f"{name}: row security")
        if partition or inherits:
            problems.append(f"{name}: inheritance or partitions")
    for name, kind in _rows(
        cur,
        "SELECT t.typname, t.typtype FROM pg_catalog.pg_type t WHERE t.typnamespace = %s "
        "AND NOT (t.typtype = 'c' AND EXISTS (SELECT 1 FROM pg_catalog.pg_class c "
        "  WHERE c.oid = t.typrelid AND c.relkind IN ('r', 'S'))) "
        "AND NOT (t.typtype = 'b' AND t.typcategory = 'A' AND EXISTS ("
        "  SELECT 1 FROM pg_catalog.pg_type e JOIN pg_catalog.pg_class c ON c.oid = e.typrelid "
        "  WHERE e.oid = t.typelem AND c.relkind IN ('r', 'S'))) ORDER BY 1",
        [oid],
    ):
        problems.append(f"a type of its own: {name} ({kind})")
    checks = (
        (
            "a rule",
            "SELECT c.relname || '.' || r.rulename FROM pg_catalog.pg_rewrite r "
            "JOIN pg_catalog.pg_class c ON c.oid = r.ev_class WHERE c.relnamespace = %s",
        ),
        (
            "a policy",
            "SELECT c.relname || '.' || p.polname FROM pg_catalog.pg_policy p "
            "JOIN pg_catalog.pg_class c ON c.oid = p.polrelid WHERE c.relnamespace = %s",
        ),
        (
            "a statistics object",
            "SELECT stxname FROM pg_catalog.pg_statistic_ext WHERE stxnamespace = %s",
        ),
        (
            "an operator",
            "SELECT oprname FROM pg_catalog.pg_operator WHERE oprnamespace = %s",
        ),
        (
            "a collation",
            "SELECT collname FROM pg_catalog.pg_collation WHERE collnamespace = %s",
        ),
        (
            "a conversion",
            "SELECT conname FROM pg_catalog.pg_conversion WHERE connamespace = %s",
        ),
        (
            "a text search configuration",
            "SELECT cfgname FROM pg_catalog.pg_ts_config WHERE cfgnamespace = %s",
        ),
        (
            "an operator class",
            "SELECT opcname FROM pg_catalog.pg_opclass WHERE opcnamespace = %s",
        ),
        (
            "a constraint trigger or not-null-only kind",
            "SELECT conname FROM "
            "pg_catalog.pg_constraint WHERE connamespace = %s AND contype NOT IN "
            "('p', 'u', 'c', 'f', 'x', 'n')",
        ),
    )
    for what, query in checks:
        for (name,) in _rows(cur, query + " ORDER BY 1", [oid]):
            problems.append(f"{what}: {name}")
    problems += expression_problems(cur, schema)
    # Expressions that call functions outside pg_catalog that no extension owns (pg_depend)
    for name, fschema in _rows(
        cur,
        "SELECT DISTINCT p.proname, n.nspname FROM pg_catalog.pg_depend d "
        "JOIN pg_catalog.pg_proc p ON d.refclassid = 'pg_catalog.pg_proc'::regclass "
        "  AND p.oid = d.refobjid "
        "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
        "WHERE d.classid IN ('pg_catalog.pg_attrdef'::regclass, "
        "  'pg_catalog.pg_constraint'::regclass, 'pg_catalog.pg_class'::regclass) "
        "AND (d.objid IN (SELECT oid FROM pg_catalog.pg_attrdef a WHERE a.adrelid IN "
        "      (SELECT oid FROM pg_catalog.pg_class WHERE relnamespace = %s)) "
        "  OR d.objid IN (SELECT oid FROM pg_catalog.pg_constraint WHERE connamespace = %s) "
        "  OR d.objid IN (SELECT oid FROM pg_catalog.pg_class WHERE relnamespace = %s)) "
        "AND n.nspname <> 'pg_catalog' "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_depend e WHERE "
        "  e.classid = 'pg_catalog.pg_proc'::regclass AND e.objid = p.oid AND e.deptype = 'e') "
        "ORDER BY 1",
        [oid, oid, oid],
    ):
        problems.append(
            f"an expression calls {fschema}.{name}: only built-in functions"
        )
    return problems


def _expression_trees(cur, oid):
    """(where, node tree text) of every stored expression of the schema: column defaults and
    generated columns, CHECKs, index expressions and predicates (exclusion constraints keep
    theirs in their index)."""
    return _rows(
        cur,
        "SELECT 'the default of ' || c.relname || '.' || a.attname, d.adbin::text "
        "FROM pg_catalog.pg_attrdef d JOIN pg_catalog.pg_class c ON c.oid = d.adrelid "
        "JOIN pg_catalog.pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum "
        "WHERE c.relnamespace = %s "
        "UNION ALL SELECT 'constraint ' || k.conname, k.conbin::text "
        "FROM pg_catalog.pg_constraint k WHERE k.connamespace = %s AND k.conbin IS NOT NULL "
        "UNION ALL SELECT 'index ' || c.relname, "
        "coalesce(x.indexprs::text, '') || ' ' || coalesce(x.indpred::text, '') "
        "FROM pg_catalog.pg_index x JOIN pg_catalog.pg_class c ON c.oid = x.indexrelid "
        "WHERE c.relnamespace = %s AND (x.indexprs IS NOT NULL OR x.indpred IS NOT NULL) "
        "ORDER BY 1",
        [oid, oid, oid],
    )


def _node_list(tree, at):
    """The text of the parenthesised node list that opens at ``tree[at]`` (a "(" ), without
    its parentheses. Node text escapes a parenthesis or a brace inside a token with a
    backslash."""
    if tree[at : at + 1] != "(":
        return None
    depth, i = 0, at
    while i < len(tree):
        ch = tree[i]
        if ch == "\\":
            i += 2
            continue
        if ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
            if depth == 0:
                return tree[at + 1 : i]
        i += 1
    return None


def _nextval_arguments(tree, nextval):
    """The argument list of every call of nextval (function ``nextval``) in ``tree`` that is
    not a regclass constant (``_REGCLASS_CONST``)."""
    bad, marker = [], f"{{FUNCEXPR :funcid {nextval} "
    start = tree.find(marker)
    while start >= 0:
        args = tree.find(":args ", start)
        listed = _node_list(tree, args + len(":args ")) if args >= 0 else None
        if listed is None or not _REGCLASS_CONST.fullmatch(listed):
            bad.append((listed or "")[:80])
        start = tree.find(marker, start + len(marker))
    return bad


def expression_problems(cur, schema):  # noqa: PLR0912 (one pass per kind of reference)
    """What a stored expression of ``schema`` may not do (review S1). The node trees are read
    from the catalogue (pg_depend never records a pinned built-in, so it cannot say this):
    every function an expression calls, directly or through an operator, must be IMMUTABLE
    (pg_catalog's or an extension's) or one of ALLOWED_VOLATILE; nextval only of a sequence
    the expression names as a constant; every relation an expression or constraint refers to
    (a sequence of nextval, a foreign key's table) must be in this schema. What a constraint
    or an index evaluates for each row without an expression of its own counts too: the
    operators of exclusion constraints and foreign keys (their functions, as above) and the
    indexes' operator classes (pg_catalog's or an extension's). Run by the quarantine on the
    dump and by ``load`` in the live cluster before any of these can be evaluated there."""
    nsp = _rows(
        cur, "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = %s", [schema]
    )
    if not nsp:
        return [f"schema {schema} is not there"]
    oid = nsp[0][0]
    nextval = _rows(
        cur,
        "SELECT 'pg_catalog.nextval(pg_catalog.regclass)'::pg_catalog.regprocedure::oid",
    )[0][0]
    calls, operators, problems = {}, {}, []
    for where, tree in _expression_trees(cur, oid):
        for m in _FUNCTION_REFS.finditer(tree):
            if int(m.group(1)):
                calls.setdefault(int(m.group(1)), where)
        for m in _OPERATOR_REFS.finditer(tree):
            operators.setdefault(int(m.group(1)), where)
        for m in _OPERATOR_LISTS.finditer(tree):
            for op in m.group(1).split():
                operators.setdefault(int(op), where)
        problems += [
            f"{where} calls nextval on a sequence named at run time ({args}): only one "
            "of this schema, named as a constant"
            for args in _nextval_arguments(tree, nextval)
        ]
    # Operators a constraint applies to every row without a stored expression
    for where, op in _rows(
        cur,
        "SELECT 'constraint ' || k.conname, o.op::bigint FROM pg_catalog.pg_constraint k, "
        "unnest(coalesce(k.conexclop, '{}') || coalesce(k.conpfeqop, '{}') || "
        "coalesce(k.conppeqop, '{}') || coalesce(k.conffeqop, '{}')) AS o(op) "
        "WHERE k.connamespace = %s ORDER BY 1, 2",
        [oid],
    ):
        operators.setdefault(int(op), where)
    for where, opclass, oschema in _rows(
        cur,
        "SELECT 'index ' || c.relname, oc.opcname, n.nspname FROM pg_catalog.pg_index x "
        "JOIN pg_catalog.pg_class c ON c.oid = x.indexrelid, "
        "unnest(x.indclass::pg_catalog.oid[]) AS k(opclass) "
        "JOIN pg_catalog.pg_opclass oc ON oc.oid = k.opclass "
        "JOIN pg_catalog.pg_namespace n ON n.oid = oc.opcnamespace "
        "WHERE c.relnamespace = %s AND n.nspname <> 'pg_catalog' AND NOT EXISTS ("
        "  SELECT 1 FROM pg_catalog.pg_depend e WHERE "
        "  e.classid = 'pg_catalog.pg_opclass'::regclass AND e.objid = oc.oid "
        "  AND e.deptype = 'e') ORDER BY 1, 2",
        [oid],
    ):
        problems.append(
            f"{where} uses operator class {oschema}.{opclass}: only built-in or extension ones"
        )
    if operators:
        for op, code in _rows(
            cur,
            "SELECT oid::bigint, oprcode::oid::bigint FROM pg_catalog.pg_operator "
            "WHERE oid = ANY(%s::oid[])",
            [sorted(operators)],
        ):
            if code:
                calls.setdefault(int(code), operators[int(op)])
    found = {}
    if calls:
        found = {
            int(r[0]): r[1:]
            for r in _rows(
                cur,
                "SELECT p.oid::bigint, n.nspname, p.proname, p.provolatile, EXISTS ("
                "  SELECT 1 FROM pg_catalog.pg_depend e WHERE "
                "  e.classid = 'pg_catalog.pg_proc'::regclass AND e.objid = p.oid "
                "  AND e.deptype = 'e') "
                "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n "
                "ON n.oid = p.pronamespace WHERE p.oid = ANY(%s::oid[])",
                [sorted(calls)],
            )
        }
    for function, where in sorted(calls.items(), key=lambda kv: (kv[1], kv[0])):
        if function not in found:
            problems.append(f"{where} calls function {function}, which is not there")
            continue
        fschema, name, volatility, extension = found[function]
        built_in = fschema == "pg_catalog"
        if not (built_in or extension):
            problems.append(
                f"{where} calls {fschema}.{name}: only built-in or extension functions"
            )
            continue
        if volatility == "i" or (built_in and name in ALLOWED_VOLATILE):
            continue
        kind = {"s": "stable", "v": "volatile"}.get(volatility, volatility)
        problems.append(
            f"{where} calls {fschema}.{name} ({kind}): an expression may call only "
            "immutable functions and " + ", ".join(sorted(ALLOWED_VOLATILE))
        )
    for where, relation, other in _rows(
        cur,
        "SELECT DISTINCT CASE d.classid WHEN 'pg_catalog.pg_attrdef'::regclass THEN 'a default' "
        "  WHEN 'pg_catalog.pg_constraint'::regclass THEN 'a constraint' ELSE 'an index' END, "
        "  r.relname, rn.nspname "
        "FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_class r "
        "  ON d.refclassid = 'pg_catalog.pg_class'::regclass AND r.oid = d.refobjid "
        "JOIN pg_catalog.pg_namespace rn ON rn.oid = r.relnamespace "
        "WHERE rn.oid <> %s AND ("
        "  (d.classid = 'pg_catalog.pg_attrdef'::regclass AND d.objid IN (SELECT a.oid "
        "     FROM pg_catalog.pg_attrdef a JOIN pg_catalog.pg_class c ON c.oid = a.adrelid "
        "     WHERE c.relnamespace = %s)) "
        "  OR (d.classid = 'pg_catalog.pg_constraint'::regclass AND d.objid IN (SELECT k.oid "
        "     FROM pg_catalog.pg_constraint k WHERE k.connamespace = %s)) "
        "  OR (d.classid = 'pg_catalog.pg_class'::regclass AND d.objid IN (SELECT c.oid "
        "     FROM pg_catalog.pg_class c WHERE c.relnamespace = %s AND c.relkind = 'i'))) "
        "ORDER BY 1, 2, 3",
        [oid, oid, oid, oid],
    ):
        problems.append(f"{where} refers to {other}.{relation}: only this schema's")
    return problems


BASELINE_ROLES = ("quarantine", "ihr_quarantine")


def database_problems(cur, schema, extensions, roles=BASELINE_ROLES):
    """The quarantine's whole database (it holds nothing else): no schema but this one, nothing
    in ``public`` but the extensions it was given, no extension it was not given, no role but
    its own, no grant, no event trigger, publication or large object."""
    problems = []
    for (name,) in _rows(
        cur,
        "SELECT nspname FROM pg_catalog.pg_namespace WHERE nspname NOT IN "
        "('pg_catalog', 'information_schema', 'pg_toast', 'public', %s) "
        "AND nspname !~ '^pg_(toast_)?temp_' ORDER BY 1",
        [schema],
    ):
        problems.append(f"another schema: {name}")
    for (name,) in _rows(cur, "SELECT extname FROM pg_catalog.pg_extension ORDER BY 1"):
        if name not in extensions and name != "plpgsql":
            problems.append(f"an extension it was not given: {name}")
    for name, kind in _rows(
        cur,
        "SELECT c.relname, c.relkind FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_depend d WHERE "
        "  d.classid = 'pg_catalog.pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e') "
        "ORDER BY 1",
    ):
        problems.append(f"an object in public: {name} ({kind})")
    for (name,) in _rows(
        cur,
        "SELECT p.proname FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public' "
        "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_depend d WHERE "
        "  d.classid = 'pg_catalog.pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e') "
        "ORDER BY 1",
    ):
        problems.append(f"a function in public: {name}")
    for (name,) in _rows(
        cur,
        "SELECT rolname FROM pg_catalog.pg_roles WHERE rolname !~ '^pg_' "
        "AND NOT rolsuper AND rolname <> ALL(%s) ORDER BY 1",
        [list(roles)],
    ):
        problems.append(f"a role: {name}")
    for name, acl in _rows(
        cur,
        "SELECT c.relname, c.relacl::text FROM pg_catalog.pg_class c "
        "WHERE c.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = %s) "
        "AND c.relacl IS NOT NULL ORDER BY 1",
        [schema],
    ):
        problems.append(f"a grant on {name}: {acl}")
    for name, acl in _rows(
        cur,
        "SELECT c.relname, a.attname || ' ' || a.attacl::text FROM pg_catalog.pg_attribute a "
        "JOIN pg_catalog.pg_class c ON c.oid = a.attrelid "
        "WHERE c.relnamespace = (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = %s) "
        "AND a.attacl IS NOT NULL ORDER BY 1",
        [schema],
    ):
        problems.append(f"a column grant on {name}: {acl}")
    for what, query in (
        ("an event trigger", "SELECT evtname FROM pg_catalog.pg_event_trigger"),
        ("a publication", "SELECT pubname FROM pg_catalog.pg_publication"),
        ("a large object", "SELECT oid::text FROM pg_catalog.pg_largeobject_metadata"),
        (
            "a default privilege",
            "SELECT defaclrole::regrole::text FROM pg_catalog.pg_default_acl",
        ),
        ("a foreign server", "SELECT srvname FROM pg_catalog.pg_foreign_server"),
    ):
        for (name,) in _rows(cur, query):
            problems.append(f"{what}: {name}")
    return problems


def functions(cur, schema):
    """{name: md5 of the body and settings} of the functions in ``schema`` (the restore
    compares them with this build's, installed in the live schema: by definition hash)."""
    return dict(
        _rows(
            cur,
            "SELECT p.proname, md5(p.prosrc || '|' || coalesce(p.proconfig::text, '') || '|' "
            "|| p.prosecdef::text) FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n "
            "ON n.oid = p.pronamespace WHERE n.nspname = %s ORDER BY 1",
            [schema],
        )
    )


# --- facts and shape ---------------------------------------------------------------------------


def tables(cur, schema):
    return [
        name
        for (name,) in _rows(
            cur,
            "SELECT c.relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
            "ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'r' ORDER BY 1",
            [schema],
        )
    ]


def sequences(cur, schema):
    """{sequence: [last_value, is_called]}, read from each sequence itself."""
    found = {}
    for (name,) in _rows(
        cur,
        "SELECT c.relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'S' ORDER BY 1",
        [schema],
    ):
        value, called = _rows(
            cur,
            f"SELECT last_value, is_called FROM {_qualified(schema, name)}",
        )[0]
        found[name] = [int(value), bool(called)]
    return found


def facts(cur, schema):
    """What the restore checks before it touches anything: the migrations recorded, the audit
    chain's head, the highest transaction id anywhere in the trail, the newest entry's time."""
    has = set(tables(cur, schema))
    found = {
        "migrations": [],
        "audit": {
            "seal_seq": 0,
            "seal_hash": "",
            "seal_upper_txid": "0",
            "max_entry_txid": "0",
            "max_entry_id": 0,
            "newest_entry_at": None,
        },
    }
    if "django_migrations" in has:
        found["migrations"] = [
            [a, n]
            for a, n in _rows(
                cur,
                f"SELECT app, name FROM {_qualified(schema, 'django_migrations')} "
                "ORDER BY 1, 2",
            )
        ]
    if {"ihr_audit_seal", "ihr_audit_entry"} <= has:
        head = _rows(
            cur,
            f"SELECT seq, encode(hash, 'hex'), upper_txid::text FROM "
            f"{_qualified(schema, 'ihr_audit_seal')} ORDER BY seq DESC LIMIT 1",
        )
        if head:
            found["audit"].update(
                seal_seq=head[0][0], seal_hash=head[0][1], seal_upper_txid=head[0][2]
            )
        txid, entry, at = _rows(
            cur,
            "SELECT coalesce(max(txid), '0')::text, coalesce(max(id), 0), max(at) FROM "
            f"{_qualified(schema, 'ihr_audit_entry')}",
        )[0]
        found["audit"].update(
            max_entry_txid=txid,
            max_entry_id=entry,
            newest_entry_at=at.isoformat() if at else None,
        )
    return found


def shape(cur, schema):
    """A canonical description of the schema's tables, columns, sequences, constraints and
    indexes (what ``export`` writes and ``load`` makes), and its sha256. Read with an empty
    search_path; the same queries on both sides of a load."""
    with _QuietPath(cur):
        return _shape(cur, schema)


def _shape(cur, schema):
    out = {"tables": {}, "sequences": {}, "constraints": [], "indexes": []}
    for table, persistence, options in _rows(
        cur,
        "SELECT c.relname, c.relpersistence, coalesce(c.reloptions::text, '') "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relkind = 'r' ORDER BY 1",
        [schema],
    ):
        out["tables"][table] = {
            "persistence": persistence,
            "options": options,
            "columns": [],
        }
    for row in _rows(
        cur,
        "SELECT c.relname, a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod), "
        "a.attnotnull, coalesce(pg_catalog.pg_get_expr(d.adbin, d.adrelid), ''), "
        "a.attidentity, a.attgenerated, coalesce(co.collname, '') "
        "FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid = a.attrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "LEFT JOIN pg_catalog.pg_type t ON t.oid = a.atttypid "
        "LEFT JOIN pg_catalog.pg_collation co ON co.oid = a.attcollation "
        "  AND a.attcollation <> t.typcollation "
        "WHERE n.nspname = %s AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped "
        "ORDER BY c.relname, a.attnum",
        [schema],
    ):
        out["tables"][row[0]]["columns"].append(list(row[1:]))
    for row in _rows(
        cur,
        "SELECT s.relname, pg_catalog.format_type(q.seqtypid, NULL), q.seqstart, q.seqincrement, "
        "q.seqmin, q.seqmax, q.seqcache, q.seqcycle, "
        "coalesce((SELECT t.relname || '.' || a.attname || ':' || d.deptype::text "
        "  FROM pg_catalog.pg_depend d JOIN pg_catalog.pg_class t ON t.oid = d.refobjid "
        "  JOIN pg_catalog.pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid "
        "  WHERE d.classid = 'pg_catalog.pg_class'::regclass AND d.objid = s.oid "
        "  AND d.refclassid = 'pg_catalog.pg_class'::regclass AND d.deptype IN ('a', 'i')), '') "
        "FROM pg_catalog.pg_class s JOIN pg_catalog.pg_namespace n ON n.oid = s.relnamespace "
        "JOIN pg_catalog.pg_sequence q ON q.seqrelid = s.oid "
        "WHERE n.nspname = %s AND s.relkind = 'S' ORDER BY 1",
        [schema],
    ):
        out["sequences"][row[0]] = [str(v) for v in row[1:]]
    out["constraints"] = [
        list(r)
        for r in _rows(
            cur,
            "SELECT c.relname, k.conname, k.contype, pg_catalog.pg_get_constraintdef(k.oid) "
            "FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid = k.conrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid = k.connamespace "
            "WHERE n.nspname = %s AND k.contype IN ('p', 'u', 'c', 'f', 'x') ORDER BY 1, 2",
            [schema],
        )
    ]
    out["indexes"] = [
        list(r)
        for r in _rows(
            cur,
            "SELECT t.relname, i.relname, pg_catalog.pg_get_indexdef(i.oid) "
            "FROM pg_catalog.pg_index x JOIN pg_catalog.pg_class i ON i.oid = x.indexrelid "
            "JOIN pg_catalog.pg_class t ON t.oid = x.indrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid = i.relnamespace "
            "WHERE n.nspname = %s ORDER BY 1, 2",
            [schema],
        )
    ]
    text = json.dumps(out, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest(), out


def shape_tables(detail):
    """{table: short hash} of a ``shape`` detail (its columns, constraints and indexes), and
    ``sequences``: what the restore names when a package's shape is not the live schema's."""

    def digest(value):
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    out = {
        table: digest(
            [
                body,
                [c for c in detail["constraints"] if c[0] == table],
                [i for i in detail["indexes"] if i[0] == table],
            ]
        )
        for table, body in detail["tables"].items()
    }
    out["sequences"] = digest(detail["sequences"])
    return out


# --- export ------------------------------------------------------------------------------------


def _identity(schema, seq, row):
    """GENERATED ... AS IDENTITY (...) for an identity column's own sequence."""
    kind, _, start, inc, low, high, cache, cycle = row
    return (
        f"GENERATED {'ALWAYS' if kind == 'a' else 'BY DEFAULT'} AS IDENTITY "
        f"(SEQUENCE NAME {_qualified(schema, seq)} START WITH {int(start)} INCREMENT BY "
        f"{int(inc)} MINVALUE {int(low)} MAXVALUE {int(high)} CACHE {int(cache)}"
        f"{' CYCLE' if cycle else ' NO CYCLE'})"
    )


def _ddl(cur, schema):
    """The statements that make ``schema`` again, in load order, and the tables' columns
    (those COPY carries: generated columns are computed again)."""
    with _QuietPath(cur):
        return _ddl_quiet(cur, schema)


def _ddl_quiet(cur, schema):  # noqa: PLR0912 (one pass per kind of object)
    ddl = {"pre": [f"CREATE SCHEMA {_ident(schema)}"], "post": [], "setval": []}
    identity = {}  # (table, column) -> sequence
    owned = []
    for seq, table, column, deptype in _rows(
        cur,
        "SELECT s.relname, t.relname, a.attname, d.deptype FROM pg_catalog.pg_class s "
        "JOIN pg_catalog.pg_namespace n ON n.oid = s.relnamespace "
        "JOIN pg_catalog.pg_depend d ON d.classid = 'pg_catalog.pg_class'::regclass "
        "  AND d.objid = s.oid AND d.refclassid = 'pg_catalog.pg_class'::regclass "
        "  AND d.deptype IN ('a', 'i') "
        "JOIN pg_catalog.pg_class t ON t.oid = d.refobjid "
        "JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid "
        "WHERE n.nspname = %s AND s.relkind = 'S'",
        [schema],
    ):
        if deptype == "i":
            identity[(table, column)] = seq
        else:
            owned.append((seq, table, column))
    seq_rows = {
        r[0]: r[1:]
        for r in _rows(
            cur,
            "SELECT s.relname, pg_catalog.format_type(q.seqtypid, NULL), q.seqstart, "
            "q.seqincrement, q.seqmin, q.seqmax, q.seqcache, q.seqcycle "
            "FROM pg_catalog.pg_class s JOIN pg_catalog.pg_namespace n ON n.oid = s.relnamespace "
            "JOIN pg_catalog.pg_sequence q ON q.seqrelid = s.oid "
            "WHERE n.nspname = %s AND s.relkind = 'S' ORDER BY 1",
            [schema],
        )
    }
    in_identity = set(identity.values())
    for seq, (typ, start, inc, low, high, cache, cycle) in seq_rows.items():
        if seq in in_identity:
            continue
        ddl["pre"].append(
            f"CREATE SEQUENCE {_qualified(schema, seq)} AS {typ} START WITH {int(start)} "
            f"INCREMENT BY {int(inc)} MINVALUE {int(low)} MAXVALUE {int(high)} "
            f"CACHE {int(cache)}{' CYCLE' if cycle else ' NO CYCLE'}"
        )
    columns = {}
    rel = {
        r[0]: r[1:]
        for r in _rows(
            cur,
            "SELECT c.relname, c.relpersistence, c.reloptions FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relkind = 'r' ORDER BY 1",
            [schema],
        )
    }
    cols = {}
    for table, name, typ, notnull, default, ident, generated, collation in _rows(
        cur,
        "SELECT c.relname, a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod), "
        "a.attnotnull, pg_catalog.pg_get_expr(d.adbin, d.adrelid), a.attidentity, "
        "a.attgenerated, CASE WHEN a.attcollation <> t.typcollation AND a.attcollation <> 0 "
        "  THEN quote_ident(cn.nspname) || '.' || quote_ident(co.collname) END "
        "FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid = a.attrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_catalog.pg_type t ON t.oid = a.atttypid "
        "LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "LEFT JOIN pg_catalog.pg_collation co ON co.oid = a.attcollation "
        "LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid = co.collnamespace "
        "WHERE n.nspname = %s AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped "
        "ORDER BY c.relname, a.attnum",
        [schema],
    ):
        parts = [_ident(name), typ]
        if collation:
            parts.append(f"COLLATE {collation}")
        if ident:
            seq = identity.get((table, name))
            if seq is None:
                msg = f"{table}.{name}: an identity column without its sequence"
                raise PackageError(msg)
            parts.append(_identity(schema, seq, (ident, *seq_rows[seq])))
        elif generated == "s":
            parts.append(f"GENERATED ALWAYS AS ({default}) STORED")
        elif generated:
            msg = f"{table}.{name}: a generated column of kind {generated!r}"
            raise PackageError(msg)
        elif default is not None:
            parts.append(f"DEFAULT {default}")
        if notnull:
            parts.append("NOT NULL")
        cols.setdefault(table, []).append(" ".join(parts))
        if not generated:
            columns.setdefault(table, []).append(name)
    for table, (persistence, options) in rel.items():
        body = ", ".join(cols.get(table, []))
        unlogged = "UNLOGGED " if persistence == "u" else ""
        with_ = f" WITH ({', '.join(options)})" if options else ""
        ddl["pre"].append(
            f"CREATE {unlogged}TABLE {_qualified(schema, table)} ({body}){with_}"
        )
    for seq in seq_rows:
        ddl["setval"].append(seq)
    keys, foreign = [], []
    for table, name, kind, definition in _rows(
        cur,
        "SELECT c.relname, k.conname, k.contype, pg_catalog.pg_get_constraintdef(k.oid) "
        "FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid = k.conrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid = k.connamespace "
        "WHERE n.nspname = %s AND k.contype IN ('p', 'u', 'c', 'f', 'x') "
        "ORDER BY k.contype = 'f', 1, 2",
        [schema],
    ):
        statement = (
            f"ALTER TABLE ONLY {_qualified(schema, table)} ADD CONSTRAINT {_ident(name)} "
            f"{definition}"
        )
        (foreign if kind == "f" else keys).append(statement)
    indexes = [
        definition
        for (definition,) in _rows(
            cur,
            "SELECT pg_catalog.pg_get_indexdef(x.indexrelid) FROM pg_catalog.pg_index x "
            "JOIN pg_catalog.pg_class i ON i.oid = x.indexrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid = i.relnamespace "
            "WHERE n.nspname = %s AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint k "
            "  WHERE k.conindid = x.indexrelid AND k.contype IN ('p', 'u', 'x')) ORDER BY 1",
            [schema],
        )
    ]
    ddl["post"] = (
        keys
        + indexes
        + foreign
        + [
            f"ALTER SEQUENCE {_qualified(schema, seq)} OWNED BY "
            f"{_qualified(schema, table)}.{_ident(column)}"
            for seq, table, column in sorted(owned)
        ]
    )
    return ddl, columns


def export(
    conn, schema, out_dir, *, source=None, report=None, snapshot=True, digests=None
):
    """Write the package of ``schema`` from ``conn`` (psycopg, any role that reads it) into
    ``out_dir`` (new or empty), in one REPEATABLE READ snapshot (``snapshot``; False: in the
    caller's transaction, as the tests do). Returns package.json's dict. Refuses (PackageError)
    a schema with anything ``schema_problems`` names. ``digests`` (a dict) gets the SHA-256 of
    ddl.json and package.json as written, from this process's memory: the quarantine checks
    the files against them once its server is stopped (``recheck``)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        msg = f"{out} is not empty"
        raise PackageError(msg)
    (out / "data").mkdir()
    with conn.transaction() if snapshot else _Nothing():
        cur = conn.cursor()
        if snapshot:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        if problems := schema_problems(cur, schema):
            raise PackageError("; ".join(problems[:20]))
        ddl, columns = _ddl(cur, schema)
        shape_hash, detail = shape(cur, schema)
        found_facts = facts(cur, schema)
        seqs = sequences(cur, schema)
        their_functions = functions(cur, schema)
        listing = []
        for n, table in enumerate(sorted(columns)):
            path = out / "data" / f"{n:04d}.copy"
            names = ", ".join(_ident(c) for c in columns[table])
            rows = 0
            with (
                Path(path).open("wb") as f,
                cur.copy(
                    f"COPY {_qualified(schema, table)} ({names}) TO STDOUT"
                ) as copy,
            ):
                for block in copy:
                    f.write(block)
                    rows += bytes(block).count(b"\n")
            listing.append(
                {
                    "table": table,
                    "columns": columns[table],
                    "rows": rows,
                    "file": f"data/{n:04d}.copy",
                    "sha256": _sha(path),
                    "bytes": Path(path).stat().st_size,
                }
            )
    ddl_text = json.dumps(ddl, indent=1)
    (out / "ddl.json").write_text(ddl_text, encoding="utf-8")
    doc = {
        "format": FORMAT,
        "schema": schema,
        "shape": shape_hash,
        "shape_tables": shape_tables(detail),
        "ddl_sha256": hashlib.sha256(ddl_text.encode()).hexdigest(),
        "tables": listing,
        "sequences": seqs,
        "facts": found_facts,
        "functions": their_functions,
        "source": source or {},
        "report": report or {},
    }
    body = json.dumps(doc, indent=1, sort_keys=True)
    (out / "package.json").write_text(body, encoding="utf-8")
    if digests is not None:
        digests.update(
            {
                "ddl.json": doc["ddl_sha256"],
                "package.json": hashlib.sha256(body.encode()).hexdigest(),
            }
        )
    return doc


def recheck(out_dir, digests):
    """The package's files against what ``export`` wrote (``digests``) and each other: run by
    the quarantine after its server is stopped, so nothing it ran can have changed them
    unseen. Problems, as text."""
    out = Path(out_dir)
    problems = [
        f"{name} changed after it was written"
        for name, want in sorted(digests.items())
        if not (out / name).is_file() or _sha(out / name) != want
    ]
    if not problems:
        try:
            read(out)
        except PackageError as exc:
            problems.append(str(exc))
    return problems


class _Nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --- load --------------------------------------------------------------------------------------


def read(pkg_dir):
    """package.json, after every file it names is checked (sha256)."""
    pkg = Path(pkg_dir)
    try:
        raw = (pkg / "package.json").read_bytes()
        doc = json.loads(raw)
    except (OSError, ValueError) as exc:
        msg = f"no readable package.json in {pkg}: {exc}"
        raise PackageError(msg) from None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        msg = "not a restore package of this format"
        raise PackageError(msg)
    # What the quarantine printed for it (the restore compares, ``--package-sha256``)
    doc["package_sha256"] = hashlib.sha256(raw).hexdigest()
    if not SCHEMA_RE.match(str(doc.get("schema", ""))):
        msg = "the package's schema is not a hospital schema name"
        raise PackageError(msg)
    if _sha(pkg / "ddl.json") != doc.get("ddl_sha256"):
        msg = "ddl.json is not the one package.json names"
        raise PackageError(msg)
    for item in doc.get("tables", []):
        name = str(item.get("file", ""))
        if not re.fullmatch(r"data/\d{4}\.copy", name):
            msg = f"a data file of another name: {name[:40]!r}"
            raise PackageError(msg)
        if _sha(pkg / name) != item.get("sha256"):
            msg = f"{name} is not the one package.json names"
            raise PackageError(msg)
    return doc


_DOLLAR = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def _one_statement(text):  # noqa: PLR0912, PLR0915 (a small lexer)
    """Refuse (PackageError) a text that is not exactly one statement: a ";" or a comment
    outside a string literal, a quoted name or a dollar quote, or one of those left open, or
    unbalanced parentheses. Returns the top-level parenthesised spans [(open, close)]."""
    i, n, depth, start, spans = 0, len(text), 0, 0, []
    while i < n:
        ch = text[i]
        if ch == "'":
            # E'...' (backslash escapes): an E right before, not ending a longer name
            escapes = text[i - 1 : i] in ("e", "E") and not re.fullmatch(
                r'[\w$"]', text[max(i - 2, 0) : max(i - 1, 0)]
            )
            i += 1
            while True:
                if i >= n:
                    msg = "a string literal left open"
                    raise PackageError(msg)
                if escapes and text[i] == "\\":
                    i += 2
                    continue
                if text[i] == "'":
                    if text[i + 1 : i + 2] == "'":
                        i += 2
                        continue
                    break
                i += 1
        elif ch == '"':
            i += 1
            while True:
                if i >= n:
                    msg = "a quoted name left open"
                    raise PackageError(msg)
                if text[i] == '"':
                    if text[i + 1 : i + 2] == '"':
                        i += 2
                        continue
                    break
                i += 1
        elif ch == "$" and not (i and (text[i - 1].isalnum() or text[i - 1] == "_")):
            m = _DOLLAR.match(text, i)
            if m:
                close = text.find(m.group(0), m.end())
                if close < 0:
                    msg = "a dollar quote left open"
                    raise PackageError(msg)
                i = close + len(m.group(0))
                continue
        elif ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                msg = "unbalanced parentheses"
                raise PackageError(msg)
            if depth == 0:
                spans.append((start, i))
        elif ch == ";":
            msg = "more than one statement"
            raise PackageError(msg)
        elif text.startswith(("--", "/*"), i):
            msg = "a comment"
            raise PackageError(msg)
        i += 1
    if depth:
        msg = "unbalanced parentheses"
        raise PackageError(msg)
    return spans


def _check_statement(phase, text, schema):
    """One whole statement of a form ``export`` writes for ``phase``, naming only ``schema``
    (review S2: matched whole, never only its start). Returns the match."""
    try:
        spans = _one_statement(text)
    except PackageError as exc:
        msg = f"a {phase} statement that is not one statement ({exc})"
        raise PackageError(msg) from None
    pattern = _STATEMENT[phase]
    found = (pattern.match if phase in ("table", "constraint") else pattern.fullmatch)(
        text
    )
    if not found:
        msg = f"a {phase} statement of a form this module never writes"
        raise PackageError(msg)
    for group in ("s", "t"):
        if group in pattern.groupindex and found.group(group).strip('"') != schema:
            msg = f"a {phase} statement names another schema"
            raise PackageError(msg)
    if phase == "table" and not (
        # The column list is the first parenthesis, and only the options may follow it (no
        # INHERITS, PARTITION OF or anything else)
        spans
        and spans[0][0] == found.end()
        and _TABLE_TAIL.fullmatch(text, spans[0][1] + 1)
    ):
        msg = "a table statement with more than its columns"
        raise PackageError(msg)
    if phase == "constraint":
        _check_constraint(text, found, spans, schema)
    return found


def _check_constraint(text, found, spans, schema):
    """What follows ``ADD CONSTRAINT <name>`` is one constraint of its kind, as
    pg_get_constraintdef writes it, and nothing more (``_CONSTRAINT_FORMS``): its shape with
    each top-level parenthesis emptied must be the form's whole, a key's or a foreign key's
    parentheses hold column names only, and a foreign key points into this schema."""
    kind, start = found.group("kind"), found.start("kind")
    shape, last = [], start
    for open_, close in spans:
        if open_ < start:
            continue
        shape.append(text[last:open_] + "()")
        last = close + 1
    shape.append(text[last:])
    form = _CONSTRAINT_FORMS[kind].fullmatch("".join(shape))
    if not form:
        msg = (
            f"a {kind} constraint not of the form this module writes, or with something "
            "after it (another ALTER TABLE subcommand)"
        )
        raise PackageError(msg)
    if kind in ("PRIMARY KEY", "UNIQUE", "FOREIGN KEY"):
        for open_, close in spans:
            if open_ > start and not _KEY_LIST.fullmatch(text, open_ + 1, close):
                msg = f"a {kind} constraint with more than column names in its parentheses"
                raise PackageError(msg)
    if kind == "FOREIGN KEY" and form.group("rs").strip('"') != schema:
        msg = "a foreign key into another schema"
        raise PackageError(msg)


def _execute(conn, text):
    """One statement through the extended protocol (psycopg sends a query with a binary result
    format as Parse/Bind/Execute): the server refuses a text holding a second command, whatever
    the checks above let through."""
    from psycopg import Cursor

    Cursor(conn).execute(text, binary=True)


def _post(text, schema):
    """(when, statement, validate) of a post statement: ``early`` (on empty tables, before the
    inspection and the rows: CHECKs as NOT VALID, exclusion constraints, indexes with an
    expression or a predicate) or ``late`` (after the rows: keys, plain indexes, foreign keys,
    sequence ownership). ``validate``: the CHECK to validate once the rows are in."""
    if text.startswith("ALTER TABLE"):
        kind = _check_statement("constraint", text, schema).group("kind")
        if kind == "CHECK":
            if text.endswith(" NOT VALID"):
                return "early", text, None
            name = re.match(
                rf"ALTER TABLE ONLY ({_SCHEMA}\.{_IDENT}) ADD CONSTRAINT ({_IDENT}) ",
                text,
            )
            return "early", f"{text} NOT VALID", (name.group(1), name.group(3))
        return ("early" if kind == "EXCLUDE" else "late"), text, None
    if text.startswith("ALTER SEQUENCE"):
        _check_statement("owned", text, schema)
        return "late", text, None
    _check_statement("index", text, schema)
    return ("late" if _PLAIN_INDEX.fullmatch(text) else "early"), text, None


def load(conn, pkg_dir, schema, doc=None):  # noqa: PLR0912, PLR0915 (one pass per phase)
    """Make ``schema`` from the package in ``conn``'s current transaction (the caller's: the
    park is in the same one). Every statement is checked (``_check_statement``) before any
    runs, and each is sent alone (``_execute``). No expression of the package is evaluated
    before ``expression_problems`` has passed it here (module docstring). Afterwards the shape,
    sequences and each table's row count must be the package's, and the schema hold nothing
    ``schema_problems`` names, or PackageError (the caller's transaction then rolls everything
    back). Returns {"tables", "rows", "statements", "locks"}."""
    pkg = Path(pkg_dir)
    doc = doc or read(pkg)
    if doc["schema"] != schema:
        msg = f"the package is of {doc['schema']}, not {schema}"
        raise PackageError(msg)
    ddl = json.loads((pkg / "ddl.json").read_text(encoding="utf-8"))
    pre = []
    for text in ddl["pre"]:
        phase = (
            "schema"
            if text.startswith("CREATE SCHEMA")
            else "sequence"
            if text.startswith("CREATE SEQUENCE")
            else "table"
        )
        _check_statement(phase, text, schema)
        pre.append(text)
    early, late, validate = [], [], []
    for text in ddl["post"]:
        when, statement, check = _post(text, schema)
        (early if when == "early" else late).append(statement)
        if check:
            validate.append(check)
    for seq in ddl["setval"]:
        if seq not in doc["sequences"] or not re.fullmatch(r"[A-Za-z0-9_]{1,63}", seq):
            msg = f"sequence {seq[:63]!r} has no value in the package"
            raise PackageError(msg)
    for item in doc["tables"]:
        if not re.fullmatch(r"[A-Za-z0-9_]{1,63}", item["table"]) or not all(
            re.fullmatch(r"[A-Za-z0-9_]{1,63}", c) for c in item["columns"]
        ):
            msg = "a table or column name this module would not write"
            raise PackageError(msg)
    cur = conn.cursor()
    count = 0
    for text in pre + early:
        _execute(conn, text)
        count += 1
    # Nothing of the package has been evaluated yet: the tables are empty
    if problems := expression_problems(cur, schema):
        raise PackageError("; ".join(problems[:10]))
    rows = 0
    for item in doc["tables"]:
        names = ", ".join(_ident(c) for c in item["columns"])
        with (
            (pkg / item["file"]).open("rb") as f,
            cur.copy(
                f"COPY {_qualified(schema, item['table'])} ({names}) FROM STDIN"
            ) as copy,
        ):
            for block in iter(lambda f=f: f.read(CHUNK), b""):
                copy.write(block)
        got = _rows(cur, f"SELECT count(*) FROM {_qualified(schema, item['table'])}")[
            0
        ][0]
        if got != item["rows"]:
            msg = f"{item['table']}: {got} rows loaded, the package has {item['rows']}"
            raise PackageError(msg)
        rows += got
    for seq in ddl["setval"]:
        value, called = doc["sequences"][seq]
        cur.execute(
            "SELECT pg_catalog.setval(%s::regclass, %s, %s)",
            [_qualified(schema, seq), int(value), bool(called)],
        )
        count += 1
    for text in late:
        _execute(conn, text)
        count += 1
    for table, name in validate:
        _execute(conn, f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")
    got_shape, _ = shape(cur, schema)
    if got_shape != doc["shape"]:
        msg = "the loaded schema's shape is not the package's"
        raise PackageError(msg)
    if sequences(cur, schema) != doc["sequences"]:
        msg = "the loaded sequences' values are not the package's"
        raise PackageError(msg)
    if problems := schema_problems(cur, schema):
        raise PackageError("; ".join(problems[:10]))
    locks = _rows(
        cur, "SELECT count(*) FROM pg_catalog.pg_locks WHERE pid = pg_backend_pid()"
    )[0][0]
    return {
        "tables": len(doc["tables"]),
        "rows": rows,
        "statements": count,
        "locks": locks,
    }


# --- other schemas, unchanged (the drill, the tests) -------------------------------------------

# Every catalogue row that belongs to a schema's objects, by the column that names the schema
# (directly, or through the relation the row describes). Any DDL writes a new version of the
# rows it changes (a new xmin): ALTER, GRANT, a trigger enabled or disabled, an owner, a
# TRUNCATE (relfilenode), a default, a constraint. Autovacuum's and ANALYZE's statistics are
# written in place (no new version), and pg_statistic is not listed.
_VERSIONED = (
    ("pg_namespace", "x.oid", "x.oid::text"),
    ("pg_class", "x.relnamespace", "x.oid::text"),
    ("pg_type", "x.typnamespace", "x.oid::text"),
    ("pg_proc", "x.pronamespace", "x.oid::text"),
    ("pg_constraint", "x.connamespace", "x.oid::text"),
    ("pg_attribute", "c.relnamespace", "x.attrelid::text || '.' || x.attnum::text"),
    ("pg_attrdef", "c.relnamespace", "x.oid::text"),
    ("pg_index", "c.relnamespace", "x.indexrelid::text"),
    ("pg_trigger", "c.relnamespace", "x.oid::text"),
    ("pg_sequence", "c.relnamespace", "x.seqrelid::text"),
    ("pg_rewrite", "c.relnamespace", "x.oid::text"),
    ("pg_policy", "c.relnamespace", "x.oid::text"),
)
_RELATION_OF = {
    "pg_attribute": "x.attrelid",
    "pg_attrdef": "x.adrelid",
    "pg_index": "x.indexrelid",
    "pg_trigger": "x.tgrelid",
    "pg_sequence": "x.seqrelid",
    "pg_rewrite": "x.ev_class",
    "pg_policy": "x.polrelid",
}


def flush_write_counters(cur):
    """Make this session's write counters reach the statistics, and drop any cached snapshot
    of them, before ``fingerprint(rows="writes")`` reads them. False where the server does not
    count writes (track_counts off): the caller then reads the rows. Call it outside a
    transaction block (the flush happens when the session goes idle after it)."""
    if _rows(cur, "SELECT current_setting('track_counts')")[0][0] != "on":
        return False
    _rows(cur, "SELECT pg_catalog.pg_stat_force_next_flush()")
    return True


def catalogue_versions(cur, except_schema):
    """{schema: sha256} over every hospital schema but ``except_schema``, and public: of each
    catalogue's rows of that schema (``_VERSIONED``), their number and the versions they are
    at (key and xmin). Two reads differ exactly when some DDL changed a catalogue row of that
    schema between them (or inside this transaction). Set-based, without deparsing anything:
    a live restore compares it inside its own transaction, before the commit (review C7, and
    after the second verification: the deparsed catalogue of every schema twice made the
    trail's lock window grow with the fleet)."""
    parts = []
    for catalogue, namespace, key in _VERSIONED:
        join = (
            f"JOIN pg_catalog.pg_class c ON c.oid = {_RELATION_OF[catalogue]} "
            if catalogue in _RELATION_OF
            else ""
        )
        parts.append(
            f"SELECT n.nspname, '{catalogue}', count(*), coalesce(sum(hashtextextended("
            f"{key} || ':' || x.xmin::text, 0)::numeric), 0)::text "
            f"FROM pg_catalog.{catalogue} x {join}"
            f"JOIN pg_catalog.pg_namespace n ON n.oid = {namespace} "
            "WHERE (n.nspname ~ '^h_[a-z0-9]{10}$' OR n.nspname = 'public') "
            "AND n.nspname <> %s GROUP BY 1"
        )
    rows = _rows(
        cur,
        " UNION ALL ".join(parts) + " ORDER BY 1, 2",
        [except_schema] * len(parts),
    )
    out = {}
    for schema, catalogue, count, total in rows:
        out.setdefault(schema, []).append((catalogue, count, total))
    return {
        schema: hashlib.sha256(repr(items).encode()).hexdigest()
        for schema, items in out.items()
    }


def _catalogue(cur, oid, schema):
    """A schema's own objects, as rows: its grants, triggers, functions (body, settings,
    rights), relations and their grants, and its shape (columns, defaults, constraints,
    indexes, sequences' definitions)."""
    parts = _rows(
        cur, "SELECT nspacl::text FROM pg_catalog.pg_namespace WHERE oid = %s", [oid]
    )
    parts += _rows(
        cur,
        "SELECT c.relname, t.tgname, t.tgenabled, pg_catalog.pg_get_triggerdef(t.oid) "
        "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid "
        "WHERE c.relnamespace = %s ORDER BY 1, 2",
        [oid],
    )
    parts += _rows(
        cur,
        "SELECT proname, pg_catalog.pg_get_function_identity_arguments(oid), md5(prosrc), "
        "coalesce(proconfig::text, ''), prosecdef, coalesce(proacl::text, ''), "
        "coalesce(obj_description(oid, 'pg_proc'), '') FROM pg_catalog.pg_proc "
        "WHERE pronamespace = %s ORDER BY 1, 2",
        [oid],
    )
    parts += _rows(
        cur,
        "SELECT c.relname, c.relkind, coalesce(c.relacl::text, ''), "
        "coalesce((SELECT string_agg(a.attname || '=' || a.attacl::text, ',' ORDER BY a.attnum) "
        "  FROM pg_catalog.pg_attribute a WHERE a.attrelid = c.oid AND a.attacl IS NOT NULL), '') "
        "FROM pg_catalog.pg_class c WHERE c.relnamespace = %s ORDER BY 1",
        [oid],
    )
    parts.append(("shape", shape(cur, schema)[0]))
    return parts


def fingerprint(cur, schemas, public_except_hospital=None, rows=True):
    """{name: sha256} of each schema's catalogue (``_catalogue``: grants, triggers, function
    bodies and settings, shape) and, with ``rows``, its sequence values and rows (order-free:
    count and sum of row hashes per table); and of ``public`` (its catalogue too, and with
    ``rows`` every table's rows but those that point at ``public_except_hospital``, by
    ``hospital_id`` or ``hospital``: the restore writes those). What a restore of one hospital
    must leave exactly as it was (design "After the attack" 5). Without ``rows`` only what no
    app process changes. With ``rows="writes"`` (the drill, on a copy where nothing else runs)
    a hospital schema's rows are not read: each table's write counters (rows inserted, updated
    and deleted, from the statistics, which count a rolled-back write too) and its relfilenode
    (a TRUNCATE) stand for them, so the drill does not read the whole fleet twice (review C7);
    the caller makes its own counters reach the statistics first (``flush_write_counters``)."""
    out = {}
    for schema in sorted(schemas):
        oid = _rows(
            cur, "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = %s", [schema]
        )
        if not oid:
            out[schema] = "absent"
            continue
        parts = _catalogue(cur, oid[0][0], schema)
        if rows == "writes":
            parts += sorted(sequences(cur, schema).items())
            parts += _rows(
                cur,
                "SELECT c.relname, c.relfilenode::bigint, "
                "pg_catalog.pg_stat_get_tuples_inserted(c.oid), "
                "pg_catalog.pg_stat_get_tuples_updated(c.oid), "
                "pg_catalog.pg_stat_get_tuples_deleted(c.oid) "
                "FROM pg_catalog.pg_class c WHERE c.relnamespace = %s AND c.relkind = 'r' "
                "ORDER BY 1",
                [oid[0][0]],
            )
        elif rows:
            parts += sorted(sequences(cur, schema).items())
            for table in tables(cur, schema):
                parts += [
                    (
                        table,
                        *_rows(
                            cur,
                            f"SELECT count(*), coalesce(sum(hashtextextended(t::text, 0)), 0)::text "
                            f"FROM {_qualified(schema, table)} t",
                        )[0],
                    )
                ]
        out[schema] = hashlib.sha256(repr(parts).encode()).hexdigest()
    if public_except_hospital is not None:
        hospital_id, hospital_uuid = public_except_hospital
        oid = _rows(
            cur, "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public'"
        )[0][0]
        parts = _catalogue(cur, oid, "public")
        for table, column in (
            _rows(
                cur,
                "SELECT c.relname, (SELECT a.attname FROM pg_catalog.pg_attribute a WHERE "
                "  a.attrelid = c.oid AND a.attname IN ('hospital_id', 'hospital') "
                "  AND NOT a.attisdropped LIMIT 1) "
                "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1",
            )
            if rows
            else ()
        ):
            where = ""
            params = []
            if column == "hospital_id":
                where, params = (
                    " WHERE t.hospital_id IS DISTINCT FROM %s",
                    [hospital_id],
                )
            elif column == "hospital":
                where, params = (
                    " WHERE t.hospital::text IS DISTINCT FROM %s",
                    [str(hospital_uuid)],
                )
            parts.append(
                (
                    table,
                    *_rows(
                        cur,
                        f"SELECT count(*), coalesce(sum(hashtextextended(t::text, 0)), 0)::text "
                        f"FROM public.{_ident(table)} t{where}",
                        params,
                    )[0],
                )
            )
        out["public"] = hashlib.sha256(repr(parts).encode()).hexdigest()
    return out
