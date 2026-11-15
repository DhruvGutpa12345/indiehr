"""Where our roles may be held: a trigger on the three membership tables (P1-T11b1; design-b.md
§5.1 and "After the attack" §4).

``ihr_role_placement_guard()`` lives in each hospital schema and fires BEFORE INSERT or UPDATE
of the role, the person, the organisation or patient, and ``deleted``, on
``emr_facilityorganizationuser``, ``emr_organizationuser`` and ``emr_patientuser``. Rows being
soft-deleted (``deleted`` true) always pass, and so do hard deletes (no trigger). It refuses:

- ``MRD Records Access`` in any organisation (it is held only on a PatientUser row);
- any of our eight roles in an ``OrganizationUser`` (government or role organisations: a
  membership there holding ``can_list_patients`` lists every patient of a state);
- Lab Technician, Radiologist or Store Keeper at the facility's root organisation
  (``default_internal_organization``, compared by column): at the root they would read every
  patient with an active encounter;
- a ``PatientUser`` row with ``MRD Records Access`` unless a live ``ihr_records_grant`` exists for
  that person and patient;
- any other ``PatientUser`` row for a restricted person: one who holds one of our restricted
  roles and no clinical slug through any membership (``ihr_access.state.restricted``; review
  S1: a front-desk worker who also holds a non-clinical role such as CARE's "Member").

It is a trigger, not a signal, because CARE also writes these rows through ``get_or_create``,
bulk paths and fixtures. A refusal is SQLSTATE 23514 (Django's IntegrityError). Installed and
kept current by ``ensure`` from ihr_access's post_migrate in every hospital schema
(ihr_platform/db_functions.py: the code is the authority). A trigger function runs as whoever
writes (the app role), with that role's SELECT on the tables it reads, and needs no EXECUTE.
"""

from django.db import transaction
from ihr_platform import db_functions
from psycopg import sql

FUNCTION = "ihr_role_placement_guard"
TRIGGER = "ihr_role_placement_guard"
# table -> the column naming where the role is held
TABLES = {
    "emr_facilityorganizationuser": "organization_id",
    "emr_organizationuser": "organization_id",
    "emr_patientuser": "patient_id",
}
# The other tables the guard function reads
READS = (
    "ihr_records_grant",
    "security_rolemodel",
    "security_rolepermission",
    "security_permissionmodel",
    "facility_facility",
)


def _names():
    from ihr_roles.registry import (
        CLINICAL_SLUGS,
        DEPARTMENT_ONLY,
        EIGHT,
        HIDDEN,
        RESTRICTED,
    )

    return {
        "clinical": sorted(CLINICAL_SLUGS),
        "hidden": HIDDEN,
        "eight": sorted(EIGHT),
        "department_only": sorted(DEPARTMENT_ONLY),
        "restricted": sorted(RESTRICTED),
    }


def _array(names):
    return sql.SQL("ARRAY[{}]::text[]").format(
        sql.SQL(", ").join(sql.Literal(n) for n in names)
    )


FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION {schema}.ihr_role_placement_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
DECLARE
    role_name text;
    is_restricted boolean;
BEGIN
    IF NEW.deleted THEN
        RETURN NEW;
    END IF;
    SELECT r.name INTO role_name FROM {schema}.security_rolemodel r
        WHERE r.id = NEW.role_id AND r.is_system;
    IF TG_TABLE_NAME = 'emr_patientuser' THEN
        IF role_name = {hidden} THEN
            IF NOT EXISTS (
                SELECT 1 FROM {schema}.ihr_records_grant g
                WHERE g.user_id = NEW.user_id AND g.patient_id = NEW.patient_id
                  AND g.ended_at IS NULL AND g.expires_at > now()
            ) THEN
                RAISE EXCEPTION 'ihr_role_placement: % needs a live records grant for this patient',
                    role_name USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END IF;
        WITH held AS (
            SELECT m.role_id FROM {schema}.emr_facilityorganizationuser m
                WHERE m.user_id = NEW.user_id AND NOT m.deleted
            UNION
            SELECT m.role_id FROM {schema}.emr_organizationuser m
                WHERE m.user_id = NEW.user_id AND NOT m.deleted
        )
        SELECT EXISTS (
                SELECT 1 FROM held JOIN {schema}.security_rolemodel r ON r.id = held.role_id
                WHERE r.is_system AND r.name = ANY({restricted})
            ) AND NOT EXISTS (
                SELECT 1 FROM held
                JOIN {schema}.security_rolepermission rp
                    ON rp.role_id = held.role_id AND NOT rp.deleted AND NOT rp.temp_deleted
                JOIN {schema}.security_permissionmodel p
                    ON p.id = rp.permission_id AND NOT p.deleted AND NOT p.temp_deleted
                WHERE p.slug = ANY({clinical})
            )
            INTO is_restricted;
        IF is_restricted THEN
            RAISE EXCEPTION 'ihr_role_placement: this person holds restricted roles and no clinical permission, and cannot be on a patient''s care team'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF role_name = {hidden} THEN
        RAISE EXCEPTION 'ihr_role_placement: % is never held in an organisation', role_name
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_TABLE_NAME = 'emr_organizationuser' THEN
        IF role_name = ANY({eight}) THEN
            RAISE EXCEPTION 'ihr_role_placement: % is held in a facility organisation only',
                role_name USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF role_name = ANY({department_only}) AND EXISTS (
        SELECT 1 FROM {schema}.facility_facility f
        WHERE f.default_internal_organization_id = NEW.organization_id
    ) THEN
        RAISE EXCEPTION 'ihr_role_placement: % is held in a department, never at the facility root',
            role_name USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $f$;
REVOKE ALL ON FUNCTION {schema}.ihr_role_placement_guard() FROM PUBLIC;
"""
# The trigger's shape: BEFORE, FOR EACH ROW, on INSERT and on UPDATE OF these columns
# ("{column}" is the table's TABLES entry). problems() compares the installed trigger with it,
# so a trigger re-created by hand with fewer columns, or as AFTER, is drift (review C2).
TRIGGER_COLUMNS = ("role_id", "user_id", "{column}", "deleted")
# pg_trigger.tgtype bits: ROW 1 | BEFORE 2 | INSERT 4 | UPDATE 16
TRIGGER_TYPE = 1 | 2 | 4 | 16
TRIGGER_SQL = (
    "CREATE OR REPLACE TRIGGER ihr_role_placement_guard BEFORE INSERT OR UPDATE OF "
    + ", ".join(TRIGGER_COLUMNS)
    + " ON {table} FOR EACH ROW EXECUTE FUNCTION {schema}.ihr_role_placement_guard()"
)
VERSION = db_functions.version(FUNCTION_SQL, TRIGGER_SQL, repr(sorted(TABLES.items())))


def _version():
    return db_functions.version(VERSION, repr(sorted(_names().items())))


def fingerprint():
    """What the guard is in this build: its SQL and the role lists it is written from.

    Existing hospitals get a changed guard only from post_migrate, which the fleet runs only for
    a hospital with a migration new to it (fleet.needs_migrate): a change here must come with an
    ihr_access migration (no operations) whose ``PLACEMENT_SHA256`` states it (review C1;
    tests.test_placement.GuardChangesShipWithAMigration)."""
    return _version()


def function_sql(schema):
    names = _names()
    return sql.SQL(FUNCTION_SQL).format(
        schema=sql.Identifier(schema),
        hidden=sql.Literal(names["hidden"]),
        eight=_array(names["eight"]),
        department_only=_array(names["department_only"]),
        restricted=_array(names["restricted"]),
        clinical=_array(names["clinical"]),
    )


def _triggers(cursor, schema):
    """{table: (enabled, function name, tgtype, sorted column names, has WHEN or arguments)} of
    our trigger on the three tables."""
    return _triggers_in(cursor, [schema]).get(schema, {})


def _triggers_in(cursor, schemas):
    """``_triggers`` for many schemas in one query: {schema: {table: (...)}}."""
    cursor.execute(
        "SELECT n.nspname, c.relname, t.tgenabled, p.proname, t.tgtype, "
        "ARRAY(SELECT a.attname::text FROM unnest(t.tgattr::int2[]) k "
        "JOIN pg_attribute a ON a.attrelid = t.tgrelid AND a.attnum = k ORDER BY 1), "
        "t.tgqual IS NOT NULL OR t.tgnargs > 0 "
        "FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_proc p ON p.oid = t.tgfoid "
        "WHERE n.nspname = ANY(%s) AND c.relname = ANY(%s) AND t.tgname = %s",
        [list(schemas), list(TABLES), TRIGGER],
    )
    found = {}
    for schema, table, enabled, name, tgtype, columns, extra in cursor.fetchall():
        found.setdefault(schema, {})[table] = (
            enabled,
            name,
            tgtype,
            list(columns),
            extra,
        )
    return found


def _expected_columns(table):
    return sorted(c.format(column=TABLES[table]) for c in TRIGGER_COLUMNS)


def _ready(cursor, schema):
    """The three tables and every table the guard reads exist in ``schema``."""
    cursor.execute(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = ANY(%s)",
        [schema, [*TABLES, *READS]],
    )
    return cursor.fetchone()[0] == len(TABLES) + len(READS)


def problems(cursor, schema):
    """Why the guard does not hold in ``schema`` (empty when it does, or before its tables)."""
    if not _ready(cursor, schema):
        return []
    found = []
    functions = db_functions.held(cursor, schema, [FUNCTION])
    if not db_functions.intact(functions, [FUNCTION], FUNCTION, _version()):
        found.append(
            f"{schema}.{FUNCTION}(): missing or not this build's (migrate puts it back)"
        )
    triggers = _triggers(cursor, schema)
    for table in TABLES:
        held = triggers.get(table)
        if held is None or held[1] != FUNCTION:
            found.append(f"{schema}.{table}: trigger {TRIGGER} is missing (migrate)")
            continue
        enabled, _name, tgtype, columns, extra = held
        if enabled not in ("O", "A"):
            found.append(f"{schema}.{table}: trigger {TRIGGER} is disabled (migrate)")
        if tgtype != TRIGGER_TYPE or columns != _expected_columns(table) or extra:
            found.append(
                f"{schema}.{table}: trigger {TRIGGER} is not this build's (fires on "
                f"{', '.join(columns) or 'every column'}; migrate)"
            )
    return found


def runtime_problems_by_schema(cursor, schemas):
    """What an app process gates a hospital for (ihr_platform/db_roles.py, reason
    ``controls``; second review of C1), for every schema in ``schemas`` whose guard is
    installed, in two queries: {schema: [problem]}, only schemas with one. The function missing
    from a schema that has the triggers, or changed since it was installed (its fingerprint no
    longer matches its stamp); a trigger missing, disabled, pointing elsewhere, not BEFORE ROW
    on INSERT and UPDATE, or with a WHEN or arguments; and, when the installed guard is this
    build's, a trigger whose columns are not this build's. An older build's guard, unchanged as
    installed, is not a gate (as ihr_audit.seal.sealing_problems_by_schema): a guard change
    comes with an ihr_access migration, so that hospital is behind and not served anyway, and an
    archived hospital's schema is never migrated and must not raise an alert for it. A schema
    with neither the function nor a trigger has no guard yet (ihr_access not migrated there:
    the release gate does not serve it) or lost both by ``DROP FUNCTION ... CASCADE`` (known
    limit: ``problems()``, the fleet check, names it and migrate puts it back)."""
    schemas = list(schemas)
    held = db_functions.held_in(cursor, schemas, [FUNCTION])
    triggers = _triggers_in(cursor, schemas)
    found = {}
    for schema in schemas:
        if schema not in held and schema not in triggers:
            continue
        functions = held.get(schema, {})
        problems_here = []
        if FUNCTION not in functions:
            problems_here.append(
                f"{schema}.{FUNCTION}(): missing (migrate puts it back)"
            )
        elif not db_functions.unchanged(functions, [FUNCTION], FUNCTION):
            problems_here.append(
                f"{schema}.{FUNCTION}(): changed since it was installed (migrate puts it "
                "back)"
            )
        this_build = FUNCTION in functions and db_functions.intact(
            functions, [FUNCTION], FUNCTION, _version()
        )
        here = triggers.get(schema, {})
        for table in TABLES:
            trigger = here.get(table)
            if trigger is None or trigger[1] != FUNCTION:
                problems_here.append(
                    f"{schema}.{table}: trigger {TRIGGER} is missing (migrate)"
                )
                continue
            enabled, _name, tgtype, columns, extra = trigger
            if enabled not in ("O", "A"):
                problems_here.append(
                    f"{schema}.{table}: trigger {TRIGGER} is disabled (migrate)"
                )
            if (
                tgtype != TRIGGER_TYPE
                or extra
                or (this_build and columns != _expected_columns(table))
            ):
                problems_here.append(
                    f"{schema}.{table}: trigger {TRIGGER} is not as installed (migrate)"
                )
        if problems_here:
            found[schema] = problems_here
    return found


def ensure(connection, schema):
    """Install or repair the guard in a hospital schema (post_migrate, as the owner). True if
    it changed something."""
    with connection.cursor() as cursor:
        if not _ready(cursor, schema) or not problems(cursor, schema):
            return False
    with transaction.atomic(using=connection.alias), connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute(function_sql(schema))
        for table, column in TABLES.items():
            # Every time (CREATE OR REPLACE): an existing trigger of an earlier build, or one
            # re-created by hand, takes this build's definition (review C2)
            cursor.execute(
                sql.SQL(TRIGGER_SQL).format(
                    column=sql.Identifier(column),
                    table=sql.Identifier(schema, table),
                    schema=sql.Identifier(schema),
                )
            )
            cursor.execute(
                sql.SQL("ALTER TABLE {} ENABLE TRIGGER {}").format(
                    sql.Identifier(schema, table), sql.Identifier(TRIGGER)
                )
            )
        db_functions.write_stamp(cursor, schema, FUNCTION, "", [FUNCTION], _version())
    return True
