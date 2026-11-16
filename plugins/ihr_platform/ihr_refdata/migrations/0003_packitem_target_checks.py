# P1-T15b2a: the kinds contract (PackItem.target, checks and the new reasons)

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ihr_refdata", "0002_packapply_changes"),
    ]

    operations = [
        migrations.AddField(
            model_name="packitem",
            name="target",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="packitem",
            name="checks",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name="packitem",
            name="reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "None"),
                    ("edited", "Edited"),
                    ("unknown-origin", "Unknown Origin"),
                    ("deleted", "Deleted"),
                    ("conflict", "Conflict"),
                    ("data-check", "Data Check"),
                    ("privacy", "Privacy"),
                    ("counter-restart", "Counter Restart"),
                ],
                default="",
                max_length=32,
            ),
        ),
    ]
