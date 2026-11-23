"""The staff directory's trigger, installed and first filled (P1-T31a; docs/reviews/p1-t31/
design.md §3.2, attack.md "Proposed orchestrator decision" items 3-4 and D2, D6, D7).

In this hospital's schema: the trigger function ``ihr_directory_touch()`` (SECURITY DEFINER,
the owner's, search_path pinned, EXECUTE from nobody) and its three triggers, then this
hospital's rows of ``public.ihr_platform_staffdirectoryentry`` made equal to its listed links,
in this migration's own transaction. Creating the triggers locks both tables against writes
until it commits, so no link changes between the fill and the triggers taking over. A schema no
hospital names (a restore's staging schema, ``public``) gets the triggers and no rows.

The SQL is frozen here as it was when this migration was written (like ihr_audit 0004, review
C5): it never imports the live code. From then on ``ihr_identity.directory.ensure_directory``
(every hospital schema's post_migrate) brings the function and triggers to the running build's
and puts back any that were dropped or altered. ``DIRECTORY_SHA256`` is
``ihr_identity.directory.fingerprint()`` as of this migration: when that SQL changes, check
ihr_identity.W027 and the tests ask for a new ihr_identity migration (no operations) stating
the new value, so every existing hospital migrates and gets it.
"""

from django.db import migrations
from psycopg import sql

DIRECTORY_SHA256 = "23c60a6fb59f601e3fcf0dfe6086d4b54f4644edf0d604e889e5d2e793d5a077"

# {schema}, {link}, {users} and {function} are filled in per schema; the rest is frozen.
FUNCTION = """
CREATE OR REPLACE FUNCTION {schema}.ihr_directory_touch() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $f$
DECLARE
    hid bigint;
    subs uuid[];
    one uuid;
    listed boolean;
BEGIN
    -- The hospital is the schema that fired the trigger, never a value of the row
    SELECT h.id INTO hid FROM public.ihr_platform_hospital h
        WHERE h.schema_name = TG_TABLE_SCHEMA AND h.schema_name <> 'public';
    IF hid IS NULL THEN
        RETURN NULL;  -- a restore's parked or staging schema: no hospital's
    END IF;
    IF TG_OP = 'TRUNCATE' THEN
        DELETE FROM public.ihr_platform_staffdirectoryentry d WHERE d.hospital_id = hid;
        RETURN NULL;
    END IF;
    IF TG_TABLE_NAME = 'ihr_identity_staffidentity' THEN
        IF TG_OP <> 'INSERT' THEN
            subs := array_append(subs, OLD.keycloak_sub);
        END IF;
        IF TG_OP <> 'DELETE' THEN
            subs := array_append(subs, NEW.keycloak_sub);
        END IF;
    ELSE
        EXECUTE format('SELECT array_agg(s.keycloak_sub) FROM %I.ihr_identity_staffidentity s '
                       'WHERE s.user_id IN ($1, $2)', TG_TABLE_SCHEMA)
            INTO subs USING OLD.id, NEW.id;
    END IF;
    FOR one IN SELECT DISTINCT x FROM unnest(subs) AS x WHERE x IS NOT NULL ORDER BY 1 LOOP
        PERFORM pg_advisory_xact_lock(hid::integer, hashtext(one::text));
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM %1$I.ihr_identity_staffidentity s '
                       'JOIN %1$I.users_user u ON u.id = s.user_id WHERE s.keycloak_sub = $1 '
                       'AND u.is_active AND NOT u.deleted AND NOT u.is_superuser '
                       'AND NOT u.is_service_account)', TG_TABLE_SCHEMA)
            INTO listed USING one;
        IF listed THEN
            INSERT INTO public.ihr_platform_staffdirectoryentry (keycloak_sub, hospital_id)
                VALUES (one, hid) ON CONFLICT (keycloak_sub, hospital_id) DO NOTHING;
        ELSE
            DELETE FROM public.ihr_platform_staffdirectoryentry d
                WHERE d.keycloak_sub = one AND d.hospital_id = hid;
        END IF;
    END LOOP;
    RETURN NULL;
END $f$;
REVOKE ALL ON FUNCTION {schema}.ihr_directory_touch() FROM PUBLIC;
"""
TRIGGERS = """
CREATE OR REPLACE TRIGGER ihr_directory_link AFTER INSERT OR DELETE OR UPDATE OF user_id, keycloak_sub ON {link} FOR EACH ROW EXECUTE FUNCTION {function}();
CREATE OR REPLACE TRIGGER ihr_directory_user AFTER UPDATE OF is_active, deleted, is_superuser, is_service_account ON {users} FOR EACH ROW WHEN (OLD.is_active IS DISTINCT FROM NEW.is_active OR OLD.deleted IS DISTINCT FROM NEW.deleted OR OLD.is_superuser IS DISTINCT FROM NEW.is_superuser OR OLD.is_service_account IS DISTINCT FROM NEW.is_service_account) EXECUTE FUNCTION {function}();
CREATE OR REPLACE TRIGGER ihr_directory_truncate AFTER TRUNCATE ON {link} FOR EACH STATEMENT EXECUTE FUNCTION {function}();
"""
# The listed links of this schema (a person, linked to an active, not deleted user who is
# neither a superuser nor a service account)
LISTED = (
    "SELECT s.keycloak_sub FROM {link} s JOIN {users} u ON u.id = s.user_id "
    "WHERE u.is_active AND NOT u.deleted AND NOT u.is_superuser AND NOT u.is_service_account"
)


def _names(schema):
    return {
        "schema": sql.Identifier(schema),
        "link": sql.Identifier(schema, "ihr_identity_staffidentity"),
        "users": sql.Identifier(schema, "users_user"),
        "function": sql.Identifier(schema, "ihr_directory_touch"),
    }


def fill(cursor, schema):
    """Make this hospital's directory rows equal to its listed links (nothing when no
    hospital has this schema, or when a single-hospital restore holds it: a restore's migrate
    step runs this under the hospital's own schema name before its reconcile step, which
    rebuilds the rows itself, and nothing outside the schema may change before then, P1-T31
    attack D6)."""
    cursor.execute(
        "SELECT h.id FROM public.ihr_platform_hospital h WHERE h.schema_name = %s "
        "AND h.schema_name <> 'public' AND NOT EXISTS (SELECT 1 FROM "
        "public.ihr_platform_hospitalschemastate s WHERE s.hospital_id = h.id "
        "AND s.restore_operation IS NOT NULL)",
        [schema],
    )
    row = cursor.fetchone()
    if row is None:
        return
    listed = sql.SQL(LISTED).format(**_names(schema))
    cursor.execute(
        sql.SQL(
            "DELETE FROM public.ihr_platform_staffdirectoryentry WHERE hospital_id = %s "
            "AND keycloak_sub NOT IN ({})"
        ).format(listed),
        [row[0]],
    )
    cursor.execute(
        sql.SQL(
            "INSERT INTO public.ihr_platform_staffdirectoryentry (keycloak_sub, hospital_id) "
            "SELECT l.keycloak_sub, %s FROM ({}) l "
            "ON CONFLICT (keycloak_sub, hospital_id) DO NOTHING"
        ).format(listed),
        [row[0]],
    )


def install_and_fill(apps, schema_editor):
    connection = schema_editor.connection
    schema = getattr(connection, "schema_name", "public")
    if schema == "public":
        return
    names = _names(schema)
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '10s'")
        cursor.execute(sql.SQL(FUNCTION).format(schema=names["schema"]))
        cursor.execute(sql.SQL(TRIGGERS).format(**names))
        fill(cursor, schema)


class Migration(migrations.Migration):
    dependencies = [
        ("ihr_identity", "0003_staffidentity_join_confirmation_required"),
        ("ihr_platform", "0013_staffdirectoryentry"),
        ("users", "0027_user_cached_role_orgs"),
    ]

    # No reverse: the directory is never dropped by a migration
    operations = [migrations.RunPython(install_and_fill, migrations.RunPython.noop)]
