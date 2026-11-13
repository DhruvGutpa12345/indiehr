"""What the app role may put into the provisioner's queue (P1-T19a review S1).

- ``Hospital.provisioning_operation``: the operation that built the hospital, written by the
  owner-run provisioner (the app may only read hospitals). Filled here for hospitals provisioned
  before it existed: the provisioning operation that recorded the hospital's uuid.
- ``ihr_operation_guard``: a BEFORE INSERT trigger on the operations that turns every row
  inserted by anyone but the table's owner into a new, pending provisioning request
  (ihr_platform/queue_guard.py, which keeps it current from post_migrate afterwards).

The guard's SQL is frozen here as it was when this migration was written: the code that
installs the current version is queue_guard.ensure, never imported by this migration.
"""

from django.db import migrations, models

GUARD = """
SET LOCAL lock_timeout = '5s';
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
CREATE OR REPLACE TRIGGER ihr_operation_guard BEFORE INSERT ON public.ihr_platform_tenantoperation
    FOR EACH ROW EXECUTE FUNCTION public.ihr_operation_guard();
"""


def builders(apps, schema_editor):
    hospital_model = apps.get_model("ihr_platform", "Hospital")
    operation_model = apps.get_model("ihr_platform", "TenantOperation")
    for hospital in hospital_model.objects.filter(provisioning_operation__isnull=True):
        built = (
            operation_model.objects.filter(
                kind="provision",
                hospital_id=hospital.pk,
                result__hospital_uuid=str(hospital.uuid),
            )
            .order_by("created_at")
            .values_list("pk", flat=True)
            .first()
        )
        if built is not None:
            hospital_model.objects.filter(pk=hospital.pk).update(
                provisioning_operation=built
            )


class Migration(migrations.Migration):
    dependencies = [("ihr_platform", "0010_hold_reason")]

    # No reverse for the guard: a control is never weakened by a migration.
    operations = [
        migrations.AddField(
            model_name="hospital",
            name="provisioning_operation",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(builders, migrations.RunPython.noop),
        migrations.RunSQL(GUARD),
    ]
