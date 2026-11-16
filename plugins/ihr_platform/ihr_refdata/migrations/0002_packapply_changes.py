# P1-T15b1: the visible change list of an apply (Q-K61)

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ihr_refdata", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="packapply",
            name="changes",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
