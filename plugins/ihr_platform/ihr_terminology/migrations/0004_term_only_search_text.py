"""Main designations of FHIR-loaded code systems carried their code in search_text (P1-T10a1);
from P1-T10a2 the search text is the term alone, so the exact-term ranking key can compare it, and
codes are matched by the search's exact-code path. This recomputes the old rows (a few thousand:
SNOMED, loaded by RF2, never had the code appended). The read-only guard is opened for this
transaction only, as the loader does."""

from django.db import migrations


def forward(apps, schema_editor):
    from ihr_terminology.text import normalise  # noqa: PLC0415 (the same normalise the search uses)

    Designation = apps.get_model("ihr_terminology", "Designation")
    rows = Designation.objects.filter(use_code="", preferred=True).exclude(concept__system__url="http://snomed.info/sct")
    with schema_editor.connection.cursor() as cur:
        cur.execute("SELECT set_config('ihr.terminology_load', 'on', true)")
        for d in rows.only("id", "value", "search_text").iterator(chunk_size=2000):
            text = normalise(d.value)
            if text != d.search_text:
                cur.execute("UPDATE ihr_terminology_designation SET search_text = %s WHERE id = %s", [text, d.id])
        cur.execute("SELECT set_config('ihr.terminology_load', 'off', true)")


class Migration(migrations.Migration):
    dependencies = [("ihr_terminology", "0003_search_vector")]

    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
