import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ihr_platform", "0005_console"),
    ]

    operations = [
        migrations.CreateModel(
            name="StaffInvitation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("care_user_id", models.IntegerField()),
                ("email", models.EmailField(max_length=254)),
                ("first_name", models.CharField(max_length=150)),
                ("last_name", models.CharField(blank=True, max_length=150)),
                ("invited_by", models.CharField(max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("sent", "Sent"),
                            ("accepted", "Accepted"),
                            ("failed", "Failed"),
                        ],
                        default="queued",
                        max_length=16,
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        blank=True,
                        choices=[("new", "New"), ("join", "Join")],
                        max_length=8,
                    ),
                ),
                ("keycloak_sub", models.UUIDField(blank=True, null=True)),
                (
                    "join_token_hash",
                    models.CharField(blank=True, db_index=True, max_length=64),
                ),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("not_before", models.DateTimeField(blank=True, null=True)),
                ("error", models.CharField(blank=True, max_length=400)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "hospital",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="ihr_platform.hospital",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["status", "created_at"],
                        name="ihr_platfor_status_3035ef_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("hospital", "care_user_id"),
                        name="one_invitation_per_user",
                    )
                ],
            },
        ),
    ]
