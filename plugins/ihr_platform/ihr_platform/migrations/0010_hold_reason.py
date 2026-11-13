# P1-T18a (review C4/S2): why a hospital is held, so a run whose roles pass releases only a
# hold that a refused role sync set, never a release window's.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ihr_platform", "0009_release"),
    ]

    operations = [
        migrations.AddField(
            model_name="hospitalschemastate",
            name="hold_reason",
            field=models.CharField(blank=True, max_length=16),
        ),
    ]
