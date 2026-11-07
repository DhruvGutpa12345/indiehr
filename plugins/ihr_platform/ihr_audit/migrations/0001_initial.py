"""The audit trail's two tables in every hospital schema (P1-T6a, ADR-010).

Plain SQL: Django has no type for ``xid8`` and must never update or delete these rows.
Both tables refuse UPDATE, DELETE and TRUNCATE (a guard against application mistakes; a
database owner can still drop the guard, which the hash chain and its outside anchor catch).
The row triggers on the other tables are installed by ``ihr_audit.install`` after every
migration run, so tables added by later migrations are covered too.
"""

from django.db import migrations

CREATE = """
CREATE TABLE ihr_audit_entry (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    txid xid8 NOT NULL DEFAULT pg_current_xact_id(),
    at timestamptz NOT NULL DEFAULT clock_timestamp(),
    kind text NOT NULL CHECK (kind IN ('write', 'truncate', 'request')),
    actor text NOT NULL,
    context jsonb NOT NULL DEFAULT '{}',
    tbl text NOT NULL DEFAULT '',
    op text NOT NULL DEFAULT '' CHECK (op IN ('', 'I', 'U', 'D', 'T')),
    row_pk text,
    patients bigint[] NOT NULL DEFAULT '{}',
    data jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX ihr_audit_entry_txid ON ihr_audit_entry (txid, id);
CREATE INDEX ihr_audit_entry_at ON ihr_audit_entry (at);
CREATE INDEX ihr_audit_entry_patients ON ihr_audit_entry USING gin (patients);

CREATE TABLE ihr_audit_seal (
    seq bigint PRIMARY KEY CHECK (seq > 0),
    lower_txid xid8 NOT NULL,
    upper_txid xid8 NOT NULL,
    entries bigint NOT NULL CHECK (entries > 0),
    entries_hash bytea NOT NULL,
    prev_hash bytea NOT NULL,
    hash bytea NOT NULL,
    sealed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (lower_txid < upper_txid)
);

CREATE FUNCTION ihr_audit_refuse() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'the audit trail is append-only (% on %)', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER ihr_audit_append_only BEFORE UPDATE OR DELETE ON ihr_audit_entry
    FOR EACH ROW EXECUTE FUNCTION ihr_audit_refuse();
CREATE TRIGGER ihr_audit_no_truncate BEFORE TRUNCATE ON ihr_audit_entry
    FOR EACH STATEMENT EXECUTE FUNCTION ihr_audit_refuse();
CREATE TRIGGER ihr_audit_append_only BEFORE UPDATE OR DELETE ON ihr_audit_seal
    FOR EACH ROW EXECUTE FUNCTION ihr_audit_refuse();
CREATE TRIGGER ihr_audit_no_truncate BEFORE TRUNCATE ON ihr_audit_seal
    FOR EACH STATEMENT EXECUTE FUNCTION ihr_audit_refuse();
"""


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    # No reverse: the trail is never dropped by a migration.
    operations = [migrations.RunSQL(CREATE)]
