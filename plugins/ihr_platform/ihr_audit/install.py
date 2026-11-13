# ruff: noqa: SLF001 (Django model metadata is read through _meta)
"""Row triggers on every table of a hospital schema (P1-T6a, ADR-010).

After each migration run of a hospital schema, ``ensure_triggers`` (re)creates the trigger
functions and puts two triggers on every table except the reviewed ``EXEMPT`` ones:

    ihr_audit           AFTER INSERT/UPDATE/DELETE, each row: one 'write' entry with the new row,
                        the old row, or the changed columns as [old, new]
    ihr_audit_truncate  AFTER TRUNCATE: one 'truncate' entry

Values are kept (owner's decision) except the columns below. ``coverage`` lists what is not
covered or not classified, and the tests fail on any of it, so a new table or a new
secret-looking column is reviewed before it ships.
"""

import hashlib
import importlib
import pkgutil
import re

from django.apps import apps
from django.db import transaction
from psycopg import sql

from ihr_audit import reads

# Never audited: the trail itself, migrations bookkeeping and Django's (unused) sessions.
EXEMPT = frozenset(
    ("ihr_audit_entry", "ihr_audit_seal", "django_migrations", "django_session")
)

# Recorded as "(hidden)": secrets that must never be copied anywhere.
SECRET = {
    "users_user": (
        "password",
        "totp_secret",
        "mfa_settings",
        "pf_endpoint",
        "pf_p256dh",
        "pf_auth",
    ),
    "authtoken_token": ("key",),
    "facility_mobileotp": ("otp",),
}
# Recorded as "***" plus the last four characters (owner: identifiers such as ABHA masked).
LAST4 = {"emr_patientidentifier": ("value",)}
# Never recorded: caches CARE derives from audited tables (the source rows are audited) and
# CARE's own per-row history (its old values are in this trail already).
OMIT_EVERYWHERE = ("history",)
# Never in an update's changes: the save time, and the mark-and-sweep flag CARE's permission
# and role sync flips on every row at each start (rows it really removes are still deleted).
UNCHANGED_IN_UPDATES = ("modified_date", "temp_deleted")
OMIT = {
    "emr_patient": (
        "facility_identifiers",
        "instance_identifiers",
        "organization_cache",
        "users_cache",
    ),
    "users_user": ("cached_role_orgs",),
}
# Columns whose names look secret but hold none (reviewed). Anything else that matches
# SUSPICIOUS must be in SECRET, LAST4 or OMIT.
NOT_SECRET = {
    ("emr_availability", "create_tokens"),
    ("emr_availability", "tokens_per_slot"),
    ("emr_questionnaireresponsetemplate", "available_keys"),
    ("emr_tokenbooking", "token_id"),
    ("emr_tokenbooking", "token_slot_id"),
    ("emr_tokensubqueue", "current_token_id"),
    ("ihr_identity_staffidentity", "keycloak_sub"),
    ("ihr_identity_staffidentity", "sessions_valid_after"),
}
SUSPICIOUS = re.compile(r"pass|secret|token|otp|key|salt|aadha|abha|session|pin\b|pf_")

ROW_FUNCTION = """
CREATE OR REPLACE FUNCTION {schema}.ihr_audit_row() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
DECLARE
    secret text[] := string_to_array(TG_ARGV[0], ',');
    last4 text[] := string_to_array(TG_ARGV[1], ',');
    omit text[] := string_to_array(TG_ARGV[2], ',') || {omit_everywhere};
    o jsonb;
    n jsonb;
    r jsonb := '{{}}';
    k text;
    v jsonb;
    pats bigint[] := '{{}}';
    found bigint[];
    pat bigint;
    lookup text := TG_ARGV[3];
    ctx jsonb := coalesce(nullif(current_setting('ihr.audit_ctx', true), '')::jsonb, '{{}}');
    who text := coalesce(
        nullif(current_setting('ihr.audit_actor', true), ''),
        nullif(ctx->>'actor', ''),
        'system:' || coalesce(nullif(current_setting('application_name'), ''), current_user)
    );
BEGIN
    IF TG_OP <> 'INSERT' THEN o := to_jsonb(OLD) - omit; END IF;
    IF TG_OP <> 'DELETE' THEN n := to_jsonb(NEW) - omit; END IF;
    IF TG_OP = 'UPDATE' THEN
        FOR k, v IN SELECT * FROM jsonb_each(n) LOOP
            CONTINUE WHEN k = ANY({unchanged}) OR v IS NOT DISTINCT FROM o->k;
            r := r || jsonb_build_object(k, CASE
                WHEN k = ANY(secret) THEN '"(hidden)"'::jsonb
                WHEN k = ANY(last4) THEN jsonb_build_array(
                    {schema}.ihr_audit_last4(o->k), {schema}.ihr_audit_last4(v))
                ELSE jsonb_build_array(o->k, v) END);
        END LOOP;
        IF r = '{{}}' THEN RETURN NULL; END IF;  -- a save that changed nothing that counts
    ELSE
        r := coalesce(n, o);
        FOREACH k IN ARRAY secret LOOP
            IF r ? k AND r->k <> 'null' THEN r := jsonb_set(r, ARRAY[k], '"(hidden)"'); END IF;
        END LOOP;
        FOREACH k IN ARRAY last4 LOOP
            IF r ? k THEN r := jsonb_set(r, ARRAY[k], {schema}.ihr_audit_last4(r->k)); END IF;
        END LOOP;
    END IF;
    -- The patient(s) the row belongs to, old and new (a record moved between patients).
    FOREACH v IN ARRAY ARRAY[o, n] LOOP
        CONTINUE WHEN v IS NULL;
        found := '{{}}';
        IF TG_TABLE_NAME = 'emr_patient' THEN
            found := ARRAY[(v->>'id')::bigint];
        ELSIF v ? 'patient_id' THEN
            found := ARRAY[(v->>'patient_id')::bigint];
        ELSIF v ? 'encounter_id' AND v->>'encounter_id' IS NOT NULL THEN
            found := ARRAY(SELECT e.patient_id FROM {schema}.emr_encounter e
                           WHERE e.id = (v->>'encounter_id')::bigint);
        ELSIF lookup <> '' THEN
            -- Linked through another record (patient_lookup in install.py, built by us).
            -- A table renamed since the trigger was made must not stop the write itself:
            -- the entry says the lookup failed, and coverage() reports the stale trigger.
            BEGIN
                EXECUTE 'SELECT ' || lookup INTO found USING v;
            EXCEPTION WHEN undefined_table OR undefined_column THEN
                found := '{{}}';
                ctx := ctx || '{{"patient_lookup": "failed"}}';
            END;
        END IF;
        FOREACH pat IN ARRAY coalesce(found, '{{}}') LOOP
            IF pat IS NOT NULL AND NOT pat = ANY(pats) THEN pats := pats || pat; END IF;
        END LOOP;
    END LOOP;
    INSERT INTO {schema}.ihr_audit_entry (kind, actor, context, tbl, op, row_pk, patients, data)
    VALUES ('write', who, ctx, TG_TABLE_NAME, left(TG_OP, 1),
            coalesce(n, o)->>'id', pats, r);
    RETURN NULL;
END $f$;

CREATE OR REPLACE FUNCTION {schema}.ihr_audit_truncate() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
DECLARE
    ctx jsonb := coalesce(nullif(current_setting('ihr.audit_ctx', true), '')::jsonb, '{{}}');
BEGIN
    INSERT INTO {schema}.ihr_audit_entry (kind, actor, context, tbl, op)
    VALUES ('truncate', coalesce(
        nullif(current_setting('ihr.audit_actor', true), ''),
        nullif(ctx->>'actor', ''),
        'system:' || coalesce(nullif(current_setting('application_name'), ''), current_user)
    ), ctx, TG_TABLE_NAME, 'T');
    RETURN NULL;
END $f$;

CREATE OR REPLACE FUNCTION {schema}.ihr_audit_last4(v jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $f$
    SELECT CASE WHEN v IS NULL OR v = 'null' THEN v
                ELSE to_jsonb('***' || right(v #>> '{{}}', 4)) END
$f$;
"""


def _csv(names):
    return ",".join(names)


def _array(names):
    """A text[] literal of our own constant column names (never user input)."""
    return "ARRAY[" + ",".join(f"'{n}'" for n in names) + "]::text[]"


def tables(cursor, schema):
    """Plain tables (partitioned ones are refused by ``coverage``)."""
    cursor.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace "
        "WHERE s.nspname = %s AND c.relkind = 'r' AND NOT c.relispartition ORDER BY 1",
        [schema],
    )
    return [row[0] for row in cursor.fetchall()]


def has_trail(cursor, schema):
    cursor.execute("SELECT to_regclass(%s)", [f'"{schema}".ihr_audit_entry'])
    return cursor.fetchone()[0] is not None


def _owner_patient(schema, label, key, value):
    """SQL for the patient id of the ``label`` row whose ``key`` is ``value``."""
    table = sql.Identifier(schema, apps.get_model(label)._meta.db_table)
    path = reads.path_to_patient(label)
    if path == "encounter__patient_id":
        return sql.SQL(
            "(SELECT e.patient_id FROM {} x JOIN {} e ON e.id = x.encounter_id "
            "WHERE x.{} = {} LIMIT 1)"
        ).format(
            table, sql.Identifier(schema, "emr_encounter"), sql.Identifier(key), value
        )
    return sql.SQL("(SELECT x.{} FROM {} x WHERE x.{} = {} LIMIT 1)").format(
        sql.Identifier(path), table, sql.Identifier(key), value
    )


def patient_lookup(schema, model):
    """For a table whose rows reach their patient only through another record (a payment's
    account, a note's thread, a file's owner): SQL giving bigint[] from the row ($1, jsonb),
    the same links reads.py follows. Empty when the trigger's own checks suffice."""
    if model is None:
        return ""
    parts = []
    for kind, field, target in reads._links_of(model):
        if kind == "patient":
            if field == "patient_id":
                return ""
            parts.append(sql.SQL("($1->>{})::bigint").format(sql.Literal(field)))
        elif kind == "via":
            if field == "encounter_id" and target == "emr.Encounter":
                return ""
            parts.append(
                _owner_patient(
                    schema,
                    target,
                    "id",
                    sql.SQL("($1->>{})::bigint").format(sql.Literal(field)),
                )
            )
        else:
            type_field, id_field, owners = reads.BY_REFERENCE[field]
            ref = sql.SQL("($1->>{})").format(sql.Literal(id_field))
            whens = sql.SQL(" ").join(
                sql.SQL("WHEN {} THEN {}").format(
                    sql.Literal(name),
                    _owner_patient(
                        schema, owner, "external_id", sql.SQL("{}::uuid").format(ref)
                    ),
                )
                for name, owner in sorted(owners.items())
            )
            parts.append(
                sql.SQL("CASE WHEN {} ~* {} THEN CASE ($1->>{}) {} END END").format(
                    ref, sql.Literal(UUID_PATTERN), sql.Literal(type_field), whens
                )
            )
    if not parts:
        return ""
    return sql.SQL("ARRAY[{}]::bigint[]").format(sql.SQL(", ").join(parts))


UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


def _load_all_models():
    """Import every models module: CARE imports some only with its API's URLs, and a table
    whose model is not loaded yet would get no patient lookup."""
    for config in apps.get_app_configs():
        package = getattr(config.models_module, "__path__", None)
        if package is None:
            continue
        for module in pkgutil.walk_packages(
            package, f"{config.models_module.__name__}."
        ):
            importlib.import_module(module.name)


def _models_by_table():
    _load_all_models()
    return {
        m._meta.db_table: m
        for m in apps.get_models()
        if not m._meta.proxy and m._meta.managed
    }


def trigger_args(connection, schema, table, models):
    lookup = patient_lookup(schema, models.get(table))
    if not isinstance(lookup, str):
        lookup = lookup.as_string(connection.connection)
    return [
        _csv(SECRET.get(table, ())),
        _csv(LAST4.get(table, ())),
        _csv(OMIT.get(table, ())),
        lookup,
    ]


def _function_sql():
    return ROW_FUNCTION.replace("{omit_everywhere}", _array(OMIT_EVERYWHERE)).replace(
        "{unchanged}", _array(UNCHANGED_IN_UPDATES)
    )


def _installed(cursor, schema):
    """{table: {trigger: (function oid, args bytes, enabled)}} and the function's version."""
    cursor.execute(
        "SELECT c.relname, t.tgname, t.tgfoid, t.tgargs, t.tgenabled FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace s ON s.oid = c.relnamespace "
        "WHERE s.nspname = %s AND t.tgname LIKE 'ihr\\_audit%%'",
        [schema],
    )
    found = {}
    for table, name, foid, args, enabled in cursor.fetchall():
        found.setdefault(table, {})[name] = (foid, bytes(args), enabled)
    cursor.execute(
        "SELECT p.proname, p.oid, obj_description(p.oid, 'pg_proc'), "
        "md5(p.prosrc || coalesce(p.proconfig::text, '')) FROM pg_proc p "
        "JOIN pg_namespace s ON s.oid = p.pronamespace "
        "WHERE s.nspname = %s AND p.proname = ANY(%s)",
        [schema, list(FUNCTIONS)],
    )
    functions = {
        row[0]: row[1:] for row in cursor.fetchall()
    }  # name: (oid, comment, md5)
    return found, functions


FUNCTIONS = ("ihr_audit_row", "ihr_audit_truncate", "ihr_audit_last4")


def _stamp(version, functions):
    """What ihr_audit_row's comment says once installed: our version, then a fingerprint
    of each function as the database holds it. CREATE OR REPLACE keeps a comment, so a
    body changed by hand no longer matches its fingerprint."""
    return " ".join([version, *(functions[name][2] for name in FUNCTIONS)])


def functions_intact(functions, version):
    return set(functions) == set(FUNCTIONS) and functions["ihr_audit_row"][1] == _stamp(
        version, functions
    )


def _version():
    return hashlib.sha256(_function_sql().encode()).hexdigest()


def _encoded(args):
    return b"".join(a.encode() + b"\0" for a in args)


def ensure_triggers(connection, schema):
    """Bring the functions and both triggers on every audited table of ``schema`` up to
    date. Changes only what differs: re-creating a trigger locks its table against writes,
    so a migration run with nothing new must not do it on every table of every hospital.
    Returns the number of tables changed."""
    version = _version()
    models = _models_by_table()
    with connection.cursor() as cursor:
        if not has_trail(cursor, schema):
            return 0
        ident = sql.Identifier(schema)
        installed, functions = _installed(cursor, schema)
        if not functions_intact(functions, version):
            cursor.execute(sql.SQL(_function_sql()).format(schema=ident))
            installed, functions = _installed(cursor, schema)
            cursor.execute(
                sql.SQL("COMMENT ON FUNCTION {}.ihr_audit_row() IS {}").format(
                    ident, sql.Literal(_stamp(version, functions))
                )
            )
            installed, functions = _installed(cursor, schema)
        row_oid, truncate_oid = (
            functions["ihr_audit_row"][0],
            functions["ihr_audit_truncate"][0],
        )
        changed = 0
        for table in tables(cursor, schema):
            if table in EXEMPT:
                continue
            args = trigger_args(connection, schema, table, models)
            have = installed.get(table, {})
            wanted = {
                "ihr_audit": (row_oid, _encoded(args), "O"),
                "ihr_audit_truncate": (truncate_oid, b"", "O"),
            }
            if all(have.get(name) == value for name, value in wanted.items()):
                continue
            target = sql.Identifier(schema, table)
            with transaction.atomic(using=connection.alias):
                # Wait a little for writers, never queue every later write behind us.
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute(
                    sql.SQL(
                        "CREATE OR REPLACE TRIGGER ihr_audit "
                        "AFTER INSERT OR UPDATE OR DELETE ON {} FOR EACH ROW "
                        "EXECUTE FUNCTION {}.ihr_audit_row({})"
                    ).format(target, ident, sql.SQL(", ").join(map(sql.Literal, args)))
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE OR REPLACE TRIGGER ihr_audit_truncate AFTER TRUNCATE "
                        "ON {} FOR EACH STATEMENT EXECUTE FUNCTION {}.ihr_audit_truncate()"
                    ).format(target, ident)
                )
                for name in wanted:  # a trigger left disabled is switched back on
                    cursor.execute(
                        sql.SQL("ALTER TABLE {} ENABLE TRIGGER {}").format(
                            target, sql.Identifier(name)
                        )
                    )
            changed += 1
        return changed


def coverage(connection, schema):
    """Problems: tables without both triggers (or with them disabled), and secret-looking
    columns nobody has classified. Empty when all is well."""
    problems = []
    models = _models_by_table()
    with connection.cursor() as cursor:
        if not has_trail(cursor, schema):
            return [f"{schema}: no audit trail tables"]
        cursor.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace "
            "WHERE s.nspname = %s AND (c.relkind = 'p' OR c.relispartition) ORDER BY 1",
            [schema],
        )
        problems += [
            f"{schema}.{table}: partitioned tables are not covered by the audit trail; "
            "decide how to audit it first"
            for (table,) in cursor.fetchall()
        ]
        installed, functions = _installed(cursor, schema)
        if not functions_intact(functions, _version()):
            problems.append(
                f"{schema}: audit functions are missing or were changed (migrate)"
            )
        problems += _sealing_problems(cursor, schema)  # the stamp trigger too
        row_oid = functions.get("ihr_audit_row", (None,))[0]
        for table in tables(cursor, schema):
            have = installed.get(table, {})
            if table in ("ihr_audit_entry", "ihr_audit_seal"):
                wanted = ("ihr_audit_append_only", "ihr_audit_no_truncate")
            elif table in EXEMPT:
                continue
            else:
                wanted = ("ihr_audit", "ihr_audit_truncate")
            for name in wanted:
                if name not in have:
                    problems.append(f"{schema}.{table}: trigger {name} is missing")
                elif have[name][2] != "O":
                    problems.append(f"{schema}.{table}: trigger {name} is not enabled")
            if "ihr_audit" in have:
                args = _encoded(trigger_args(connection, schema, table, models))
                if have["ihr_audit"][:2] != (row_oid, args):
                    problems.append(
                        f"{schema}.{table}: trigger ihr_audit is out of date (migrate)"
                    )
        problems += _unclassified(cursor, schema)
    return problems


def _sealing_problems(cursor, schema):
    """The sealing functions and the stamp trigger (P1-T19a, S2/S3): there, enabled and
    this build's (seal.ensure_sealing, every migrate, puts them back)."""
    from ihr_audit.seal import sealing_problems

    return sealing_problems(cursor, schema)


def _unclassified(cursor, schema):
    """Secret-looking columns nobody has classified. From the catalogue, never
    information_schema, which hides the tables the reading role has no rights on (S8)."""
    problems = []
    cursor.execute(
        "SELECT c.relname, a.attname FROM pg_attribute a "
        "JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace s ON s.oid = c.relnamespace "
        "WHERE s.nspname = %s AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
        "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY 1, 2",
        [schema],
    )
    for table, column in cursor.fetchall():
        if table in EXEMPT or not SUSPICIOUS.search(column):
            continue
        classified = (
            column in SECRET.get(table, ())
            or column in LAST4.get(table, ())
            or column in OMIT.get(table, ())
            or column in OMIT_EVERYWHERE
            or (table, column) in NOT_SECRET
        )
        if not classified:
            problems.append(
                f"{schema}.{table}.{column}: looks secret; classify it in install.py"
            )
    return problems
