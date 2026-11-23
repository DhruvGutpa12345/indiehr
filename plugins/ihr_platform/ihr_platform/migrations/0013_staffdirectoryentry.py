"""The staff directory behind the hospital switcher (P1-T31a; docs/reviews/p1-t31/design.md
§3.1, attack.md "Proposed orchestrator decision"): one row per listed (Keycloak person,
hospital) link, in public. Written only by each hospital schema's ``ihr_directory_touch()``
trigger (owner, SECURITY DEFINER; installed and first filled by ihr_identity 0004) and the
owner-run repair; ``select`` for the app (public_tables.txt)."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ihr_platform", "0012_restore")]

    operations = [
        migrations.CreateModel(
            name="StaffDirectoryEntry",
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
                ("keycloak_sub", models.UUIDField()),
                (
                    "hospital",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="ihr_platform.hospital",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("keycloak_sub", "hospital"),
                        name="ihr_directory_one_per_hospital",
                    )
                ],
            },
        ),
    ]
