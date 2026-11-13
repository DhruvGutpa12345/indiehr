"""Only the database decides what the chain relies on (P1-T19a; ADR-010 amendment; S2, S3).

- ``ihr_audit_stamp``: a BEFORE INSERT trigger on the entries that forces ``txid``, ``at`` and
  ``salt`` to the database's own values, whoever inserts (the app role may insert only the
  data columns anyway: ihr_platform/db_roles.py). No entry can be backdated or slipped into a
  window that is already sealed.
- ``ihr_audit_seal_run(batch, format)`` and ``ihr_audit_seal_next()``: sealing moves into the
  database. The second is SECURITY DEFINER, owned by the owner (who runs migrations), takes no
  arguments and has its search_path pinned; only the app role may execute it (EXECUTE from
  PUBLIC is revoked here and granted to ihr_app by ensure_grants). The app role reads seals
  but never inserts one.

The SQL is frozen here as it was when this migration was written (review C5): it never imports
the live code. From then on ihr_audit.seal.ensure_sealing (every hospital schema's
post_migrate) brings the functions and the trigger to the running build's version, and puts
back any that were dropped or altered. The functions name this schema in their bodies (their
search_path is pinned), so they are made per schema.
"""

from django.db import migrations
from psycopg import sql

STAMP = """
SET LOCAL lock_timeout = '5s';  -- never queue every write behind a long transaction
CREATE FUNCTION ihr_audit_stamp() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
BEGIN
    NEW.txid := pg_current_xact_id();
    NEW.at := clock_timestamp();
    NEW.salt := uuid_send(gen_random_uuid());
    RETURN NEW;
END $f$;
CREATE TRIGGER ihr_audit_stamp BEFORE INSERT ON ihr_audit_entry
    FOR EACH ROW EXECUTE FUNCTION ihr_audit_stamp();
"""

# {schema} and {lock} are filled in per schema; everything else is frozen.
FUNCTIONS = """
CREATE OR REPLACE FUNCTION {schema}.ihr_audit_seal_run(p_batch integer, p_version smallint)
RETURNS bigint LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
DECLARE
    v_seq bigint := 0;
    v_lower xid8 := '0';
    v_prev bytea := decode(repeat('00', 32), 'hex');
    v_horizon xid8;
    v_upper xid8;
    v_first xid8;
    v_nth xid8;
    v_count bigint;
    v_leaves bytea;
    v_hash bytea;
    v_sealed bigint := 0;
    v_last record;
BEGIN
    IF p_version IS NULL OR p_version NOT IN (1, 2, 3) OR p_batch IS NULL OR p_batch < 1 THEN
        RAISE EXCEPTION 'ihr_audit_seal_run: unknown format or batch';
    END IF;
    -- Another run is sealing this schema (or someone holds its key): nothing to do now (S6)
    IF NOT pg_try_advisory_xact_lock(hashtext({lock})) THEN
        RETURN 0;
    END IF;
    SELECT s.seq, s.upper_txid, s.hash INTO v_last
        FROM {schema}.ihr_audit_seal s ORDER BY s.seq DESC LIMIT 1;
    IF FOUND THEN
        v_seq := v_last.seq;
        v_lower := v_last.upper_txid;
        v_prev := v_last.hash;
    END IF;
    v_horizon := pg_snapshot_xmin(pg_current_snapshot());
    WHILE v_lower < v_horizon LOOP
        SELECT min(e.txid) INTO v_first FROM {schema}.ihr_audit_entry e
            WHERE e.txid >= v_lower AND e.txid < v_horizon;
        IF v_first IS NULL THEN
            v_upper := v_horizon;
        ELSE
            SELECT e.txid INTO v_nth FROM {schema}.ihr_audit_entry e
                WHERE e.txid >= v_lower AND e.txid < v_horizon
                ORDER BY e.txid, e.id OFFSET p_batch LIMIT 1;
            IF NOT FOUND THEN
                v_upper := v_horizon;  -- fewer than a batch: all of them
            ELSIF v_nth = v_first THEN
                -- one transaction wrote more than a batch: take it whole
                v_upper := ((v_first::text)::numeric + 1)::text::xid8;
            ELSE
                v_upper := v_nth;
            END IF;
        END IF;
        IF p_version = 1 THEN
            SELECT count(*), sha256(coalesce(string_agg(sha256(convert_to(jsonb_build_array(e.id, e.txid::text, to_char(e.at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'), e.kind, e.actor, e.context, e.tbl, e.op, e.row_pk, to_jsonb(e.patients), e.data)::text, 'UTF8')), ''::bytea
                ORDER BY e.txid, e.id), ''::bytea)) INTO v_count, v_leaves
                FROM {schema}.ihr_audit_entry e WHERE e.txid >= v_lower AND e.txid < v_upper;
        ELSIF p_version = 2 THEN
            SELECT count(*), sha256(coalesce(string_agg(sha256(convert_to(jsonb_build_array(e.id, e.txid::text, to_char(e.at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'), e.kind, e.actor, e.context, e.tbl, e.op, e.row_pk, to_jsonb(e.patients), encode(sha256(convert_to(coalesce(e.data::text, 'null'), 'UTF8')), 'hex'))::text, 'UTF8')), ''::bytea
                ORDER BY e.txid, e.id), ''::bytea)) INTO v_count, v_leaves
                FROM {schema}.ihr_audit_entry e WHERE e.txid >= v_lower AND e.txid < v_upper;
        ELSE
            SELECT count(*), sha256(coalesce(string_agg(sha256(convert_to(jsonb_build_array(to_char(e.at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'), to_jsonb(e.patients), encode(sha256(convert_to(jsonb_build_array(e.id, e.txid::text, e.kind, e.actor, e.context, e.tbl, e.op, e.row_pk, encode(sha256(coalesce(e.salt, ''::bytea) || convert_to(coalesce(e.data::text, 'null'), 'UTF8')), 'hex'))::text, 'UTF8')), 'hex'))::text, 'UTF8')), ''::bytea
                ORDER BY e.txid, e.id), ''::bytea)) INTO v_count, v_leaves
                FROM {schema}.ihr_audit_entry e WHERE e.txid >= v_lower AND e.txid < v_upper;
        END IF;
        EXIT WHEN v_count = 0;
        v_seq := v_seq + 1;
        v_hash := sha256(v_prev || convert_to(
            CASE p_version WHEN 1 THEN '' WHEN 2 THEN 'v2|' ELSE 'v3|' END
            || v_seq::text || '|' || v_lower::text || '|' || v_upper::text || '|'
            || v_count::text, 'UTF8') || v_leaves);
        INSERT INTO {schema}.ihr_audit_seal (seq, lower_txid, upper_txid, entries,
            entries_hash, prev_hash, hash, version)
        VALUES (v_seq, v_lower, v_upper, v_count, v_leaves, v_prev, v_hash, p_version);
        v_prev := v_hash;
        v_sealed := v_sealed + v_count;
        v_lower := v_upper;
    END LOOP;
    RETURN v_sealed;
END $f$;

CREATE OR REPLACE FUNCTION {schema}.ihr_audit_seal_next() RETURNS bigint
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $f$ SELECT {schema}.ihr_audit_seal_run(5000, 3::smallint) $f$;

REVOKE ALL ON FUNCTION {schema}.ihr_audit_seal_run(integer, smallint) FROM PUBLIC;
REVOKE ALL ON FUNCTION {schema}.ihr_audit_seal_next() FROM PUBLIC;
"""


def make_functions(apps, schema_editor):
    connection = schema_editor.connection
    schema = connection.schema_name
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(FUNCTIONS).format(
                schema=sql.Identifier(schema),
                lock=sql.Literal(f"ihr_audit_seal:{schema}"),
            )
        )


class Migration(migrations.Migration):
    dependencies = [("ihr_audit", "0003_entry_salt")]
    # No reverse: the trail is never weakened by a migration.
    operations = [
        migrations.RunSQL(STAMP),
        migrations.RunPython(make_functions),
    ]
