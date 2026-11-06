"""Every hospital schema gets its profile row when it is migrated (new and existing hospitals).

The name comes from the hospital's registry row in ``public``. django-tenants inserts that row
before it creates and migrates the schema, so it is there for new hospitals too.
"""

from django.db import migrations

DEFAULT_COLOUR = "#0f766e"


def create_profile(apps, schema_editor):
    profile_model = apps.get_model("ihr_tenant_settings", "HospitalProfile")
    if profile_model.objects.exists():
        return
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT name FROM public.ihr_platform_hospital WHERE schema_name = %s",
            [connection.schema_name],
        )
        row = cursor.fetchone()
    profile_model.objects.create(
        display_name=(row[0] if row else "Hospital")[:255],
        primary_colour=DEFAULT_COLOUR,
    )


class Migration(migrations.Migration):
    dependencies = [("ihr_tenant_settings", "0001_initial")]

    operations = [migrations.RunPython(create_profile, migrations.RunPython.noop)]
