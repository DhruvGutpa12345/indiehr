"""Terminology is read-only for everyone but the loader (P1-T10a1, ADR-016).

Hospitals' connections see public too (search_path "<hospital>, public") and use the same database
role as the loader, so a stray write from a hospital's context would change terminology for every
hospital, outside any hospital's audit trail. These triggers refuse every INSERT, UPDATE and DELETE
unless the transaction opened the tables with
``set_config('ihr.terminology_load', 'on', true)``, which only ihr_terminology.loader does, in
public. (TRUNCATE is left to the table owner: nothing in the application issues it, and the
test runner's flush needs it.)
"""

from django.db import migrations

TABLES = (
    "ihr_terminology_codesystemversion",
    "ihr_terminology_concept",
    "ihr_terminology_designation",
)

FUNCTION = """
CREATE OR REPLACE FUNCTION ihr_terminology_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('ihr.terminology_load', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION 'ihr_terminology is read-only: load content with manage.py ihr_terminology_load'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NULL;
END;
$$;
"""


def forward_sql():
    out = [FUNCTION]
    for t in TABLES:
        out.append(
            f"CREATE TRIGGER {t}_guard BEFORE INSERT OR UPDATE OR DELETE ON {t} "
            "FOR EACH STATEMENT EXECUTE FUNCTION ihr_terminology_guard();"
        )
    return out


def reverse_sql():
    return [f"DROP TRIGGER IF EXISTS {t}_guard ON {t};" for t in TABLES] + [
        "DROP FUNCTION IF EXISTS ihr_terminology_guard();"
    ]


class Migration(migrations.Migration):
    dependencies = [("ihr_terminology", "0001_initial")]

    operations = [migrations.RunSQL(forward_sql(), reverse_sql())]
