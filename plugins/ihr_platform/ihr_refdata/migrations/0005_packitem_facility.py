# P1-T15b3: per-facility kinds (docs/reviews/p1-t15b/design.md §4 and "After the attack" items
# 11, 13 and 14). A record names its facility (null for a hospital-wide kind); one record per
# (pack, kind, facility, item), two hospital-wide ones refused too (NULLS NOT DISTINCT); the
# release of each per-facility pack last reconciled in each facility (PackFacility).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("facility", "0001_initial_squashed"),
        ("ihr_refdata", "0004_forms_guard"),
    ]

    operations = [
        migrations.AddField(
            model_name="packitem",
            name="facility",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="facility.facility",
            ),
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
                    ("missing-link", "Missing Link"),
                ],
                default="",
                max_length=32,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="packitem",
            name="ihr_packitem_one_per_item",
        ),
        migrations.AddConstraint(
            model_name="packitem",
            constraint=models.UniqueConstraint(
                fields=("pack", "kind", "facility", "item"),
                name="ihr_packitem_one_per_item_facility",
                nulls_distinct=False,
            ),
        ),
        migrations.CreateModel(
            name="PackFacility",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("pack", models.CharField(max_length=64)),
                ("version", models.PositiveIntegerField()),
                ("sha256", models.CharField(max_length=64)),
                ("reconciled_at", models.DateTimeField()),
                (
                    "facility",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="facility.facility",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("pack", "facility"),
                        name="ihr_packfacility_one_per_facility",
                    )
                ],
            },
        ),
    ]
