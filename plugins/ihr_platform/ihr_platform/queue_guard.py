"""What the app role may put into the provisioner's queue (P1-T19a review S1).

The console (the web app, ``ihr_app``) records a provisioning operation and queues work on it;
the onboarding provisioner (the owner) acts on the queue. The app has INSERT on
``ihr_platform_tenantoperation`` (public_tables.txt, class ``queue``), so without this guard it
could insert a row the provisioner trusts: a "succeeded" operation naming a live hospital and
its first admin (resend_setup re-points that admin to an attacker's e-mail), or a "pending" one
pointing at a live hospital with most steps "succeeded".

``ihr_operation_guard``, a BEFORE INSERT trigger owned by the owner, makes every row inserted by
anyone who is not the table's owner a new provisioning request and nothing more:

- any kind but ``provision`` is refused (rollback and upgrade records are the owner's);
- ``status`` is forced to pending, ``result`` to ``{}``, ``hospital_id``, ``started_at``,
  ``finished_at`` and ``fleet_run_id`` to NULL, and every step to pending (its name kept);
- ``slug``, ``input``, ``requested_by`` and the queue columns stay as given (the console's).

The provisioner's own second line: rollback and resend_setup act only for the operation the
owner recorded as the one that built the hospital (``Hospital.provisioning_operation``, which
the app cannot write), never on what an operation row says (provisioning/runner.py).

Installed by migration 0011 (its SQL frozen there) and kept current by ``ensure`` on the public
schema's post_migrate (ihr_platform/apps.py; db_functions.py). ``problems`` reports a missing,
disabled or changed guard (ensure_grants' report: ihr_fleet_run check, ihr_db_role_check
--report); only the owner can drop or disable it.
"""

from django.db import transaction
from psycopg import sql

from ihr_platform import db_functions

TABLE = "ihr_platform_tenantoperation"
FUNCTION = "ihr_operation_guard"
TRIGGER = "ihr_operation_guard"

GUARD_SQL = """
CREATE OR REPLACE FUNCTION public.ihr_operation_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
BEGIN
    -- The table's owner (the provisioner, ihr_fleet_run, migrate) records anything
    IF pg_has_role(current_user,
                   (SELECT c.relowner FROM pg_class c WHERE c.oid = TG_RELID), 'USAGE') THEN
        RETURN NEW;
    END IF;
    IF NEW.kind IS DISTINCT FROM 'provision' THEN
        RAISE EXCEPTION 'only the owner records a % operation', NEW.kind
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    NEW.status := 'pending';
    NEW.result := '{}'::jsonb;
    NEW.hospital_id := NULL;
    NEW.started_at := NULL;
    NEW.finished_at := NULL;
    NEW.fleet_run_id := NULL;
    NEW.steps := coalesce((
        SELECT jsonb_agg(jsonb_build_object('name', s.value ->> 'name', 'status', 'pending')
                         ORDER BY s.ordinality)
        FROM jsonb_array_elements(CASE WHEN jsonb_typeof(NEW.steps) = 'array'
                                       THEN NEW.steps ELSE '[]'::jsonb END)
             WITH ORDINALITY AS s
        WHERE jsonb_typeof(s.value) = 'object'), '[]'::jsonb);
    RETURN NEW;
END $f$;
REVOKE ALL ON FUNCTION public.ihr_operation_guard() FROM PUBLIC;
"""

TRIGGER_SQL = (
    "CREATE OR REPLACE TRIGGER ihr_operation_guard BEFORE INSERT ON "
    "public.ihr_platform_tenantoperation FOR EACH ROW "
    "EXECUTE FUNCTION public.ihr_operation_guard()"
)
VERSION = db_functions.version(GUARD_SQL, TRIGGER_SQL)


def _table_exists(cursor):
    cursor.execute("SELECT to_regclass(%s)", [f"public.{TABLE}"])
    return cursor.fetchone()[0] is not None


def _trigger(cursor):
    """(enabled, function name) of the guard trigger, or None."""
    cursor.execute(
        "SELECT t.tgenabled, p.proname FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_proc p ON p.oid = t.tgfoid "
        "WHERE n.nspname = 'public' AND c.relname = %s AND t.tgname = %s",
        [TABLE, TRIGGER],
    )
    return cursor.fetchone()


def problems(cursor):
    """Why the guard does not hold now (empty when it does, or before the table exists)."""
    if not _table_exists(cursor):
        return []
    found = []
    functions = db_functions.held(cursor, "public", [FUNCTION])
    if not db_functions.intact(functions, [FUNCTION], FUNCTION, VERSION):
        found.append(
            f"public.{FUNCTION}(): missing or changed (the app could forge what the "
            "provisioner trusts; migrate puts it back)"
        )
    trigger = _trigger(cursor)
    if trigger is None or trigger[1] != FUNCTION:
        found.append(f"public.{TABLE}: trigger {TRIGGER} is missing (migrate)")
    elif trigger[0] not in ("O", "A"):
        found.append(f"public.{TABLE}: trigger {TRIGGER} is disabled (migrate)")
    return found


def ensure(connection):
    """Install or repair the guard (the public schema's post_migrate, as the owner). Changes
    nothing when it holds. True if it changed something."""
    with connection.cursor() as cursor:
        if not _table_exists(cursor) or not problems(cursor):
            return False
    with transaction.atomic(using=connection.alias), connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute(GUARD_SQL)
        trigger = _trigger(cursor)
        if trigger is None or trigger[1] != FUNCTION:
            cursor.execute(TRIGGER_SQL)
        cursor.execute(
            sql.SQL("ALTER TABLE public.{} ENABLE TRIGGER {}").format(
                sql.Identifier(TABLE), sql.Identifier(TRIGGER)
            )
        )
        db_functions.write_stamp(cursor, "public", FUNCTION, "", [FUNCTION], VERSION)
    return True
