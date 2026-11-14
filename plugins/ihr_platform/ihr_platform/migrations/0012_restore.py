"""Single-hospital restores (P1-T22b; docs/reviews/p1-t22/design.md, "After the attack" 4, 6, 7).

- ``TenantOperation`` kind ``restore``, at most one unfinished per hospital.
- The restore hold on ``HospitalSchemaState`` (``restore_operation``, ``restore_since``): set
  and lifted only by ihr_hospital_restore; a release never clears it.
- The superseded-segment archive, in ``public`` (outside every hospital schema, so a restore,
  a second restore and the audit watch all see it): one restore record per hospital and restore
  generation, the live seals a restore replaced and their entries. Append-only: UPDATE, DELETE
  and TRUNCATE are refused by a trigger, and the app role has no right on them at all
  (public_tables.txt: none); the audit watch reads them (db_roles.WATCH_TABLES).

The tables are plain SQL (xid8 has no Django field); ihr_audit/archive.py reads and writes them.
"""

from django.db import migrations, models

ARCHIVE = """
SET LOCAL lock_timeout = '10s';
CREATE OR REPLACE FUNCTION public.ihr_restore_refuse() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
BEGIN
    RAISE EXCEPTION 'the restore archive is append-only (% on %)', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END $f$;
REVOKE ALL ON FUNCTION public.ihr_restore_refuse() FROM PUBLIC;

CREATE TABLE public.ihr_restore_record (
    hospital uuid NOT NULL,
    generation integer NOT NULL CHECK (generation > 0),
    operation uuid NOT NULL,
    slug text NOT NULL,
    restored_seq bigint NOT NULL CHECK (restored_seq >= 0),
    restored_hash bytea NOT NULL,
    from_seq bigint NOT NULL,
    to_seq bigint NOT NULL,
    head_hash bytea NOT NULL,
    digest text NOT NULL,
    parked_schema text NOT NULL,
    dump text NOT NULL,
    operator text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (hospital, generation),
    CHECK (from_seq = restored_seq + 1 AND to_seq >= from_seq)
);
CREATE TABLE public.ihr_restore_superseded_seal (
    hospital uuid NOT NULL,
    generation integer NOT NULL,
    seq bigint NOT NULL,
    lower_txid xid8 NOT NULL,
    upper_txid xid8 NOT NULL,
    entries bigint NOT NULL,
    entries_hash bytea NOT NULL,
    prev_hash bytea NOT NULL,
    hash bytea NOT NULL,
    sealed_at timestamptz NOT NULL,
    version smallint NOT NULL,
    PRIMARY KEY (hospital, generation, seq),
    FOREIGN KEY (hospital, generation) REFERENCES public.ihr_restore_record (hospital, generation)
);
CREATE TABLE public.ihr_restore_superseded_entry (
    hospital uuid NOT NULL,
    generation integer NOT NULL,
    id bigint NOT NULL,
    txid xid8 NOT NULL,
    at timestamptz NOT NULL,
    kind text NOT NULL,
    actor text NOT NULL,
    context jsonb NOT NULL,
    tbl text NOT NULL,
    op text NOT NULL,
    row_pk text,
    patients bigint[] NOT NULL,
    data jsonb NOT NULL,
    salt bytea,
    PRIMARY KEY (hospital, generation, id),
    FOREIGN KEY (hospital, generation) REFERENCES public.ihr_restore_record (hospital, generation)
);
CREATE INDEX ihr_restore_superseded_entry_txid
    ON public.ihr_restore_superseded_entry (hospital, generation, txid, id);

CREATE TRIGGER ihr_restore_append_only BEFORE UPDATE OR DELETE ON public.ihr_restore_record
    FOR EACH ROW EXECUTE FUNCTION public.ihr_restore_refuse();
CREATE TRIGGER ihr_restore_no_truncate BEFORE TRUNCATE ON public.ihr_restore_record
    FOR EACH STATEMENT EXECUTE FUNCTION public.ihr_restore_refuse();
CREATE TRIGGER ihr_restore_append_only BEFORE UPDATE OR DELETE ON public.ihr_restore_superseded_seal
    FOR EACH ROW EXECUTE FUNCTION public.ihr_restore_refuse();
CREATE TRIGGER ihr_restore_no_truncate BEFORE TRUNCATE ON public.ihr_restore_superseded_seal
    FOR EACH STATEMENT EXECUTE FUNCTION public.ihr_restore_refuse();
CREATE TRIGGER ihr_restore_append_only BEFORE UPDATE OR DELETE ON public.ihr_restore_superseded_entry
    FOR EACH ROW EXECUTE FUNCTION public.ihr_restore_refuse();
CREATE TRIGGER ihr_restore_no_truncate BEFORE TRUNCATE ON public.ihr_restore_superseded_entry
    FOR EACH STATEMENT EXECUTE FUNCTION public.ihr_restore_refuse();
"""


class Migration(migrations.Migration):
    dependencies = [("ihr_platform", "0011_operation_guard")]

    # No reverse for the archive: it is never dropped by a migration.
    operations = [
        migrations.AlterField(
            model_name="tenantoperation",
            name="kind",
            field=models.CharField(
                choices=[
                    ("provision", "Provision"),
                    ("rollback", "Rollback"),
                    ("upgrade", "Upgrade"),
                    ("restore", "Restore"),
                ],
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="tenantoperation",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("kind", "restore"), ("status__in", ["pending", "running", "failed"])
                ),
                fields=("slug",),
                name="one_open_restore_per_slug",
            ),
        ),
        migrations.AddField(
            model_name="hospitalschemastate",
            name="restore_operation",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="hospitalschemastate",
            name="restore_since",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunSQL(ARCHIVE),
    ]
