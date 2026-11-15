# Records grants (P1-T11b1: the table the placement trigger and the PatientUser rule read;
# the flow comes in P1-T11b4). Depends on emr's first migration only (Patient), so an upstream
# squash of later emr migrations never breaks this one.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("emr", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RecordsGrant",
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
                (
                    "reason",
                    models.CharField(
                        choices=[
                            ("patient_request", "Patient Request"),
                            ("legal", "Legal"),
                            ("insurer", "Insurer"),
                            ("internal_audit", "Internal Audit"),
                            ("nabh_review", "Nabh Review"),
                            ("continuity_of_care", "Continuity Of Care"),
                        ],
                        max_length=32,
                    ),
                ),
                ("reference", models.CharField(blank=True, default="", max_length=100)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField()),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                (
                    "patient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="emr.patient",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "ihr_records_grant",
                "indexes": [
                    models.Index(
                        condition=models.Q(("ended_at__isnull", True)),
                        fields=["user", "patient", "expires_at"],
                        name="ihr_records_grant_live",
                    )
                ],
            },
        ),
    ]
