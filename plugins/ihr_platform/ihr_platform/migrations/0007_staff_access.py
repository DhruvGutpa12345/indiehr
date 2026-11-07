import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ihr_platform", "0006_staffinvitation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="staffinvitation",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("sent", "Sent"),
                    ("accepted", "Accepted"),
                    ("failed", "Failed"),
                    ("cancelled", "Cancelled"),
                    ("expired", "Expired"),
                ],
                default="queued",
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="StaffAccessRequest",
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
                (
                    "keycloak_sub",
                    models.UUIDField(blank=True, db_index=True, null=True),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("reset_sign_in", "Reset Sign In"),
                            ("resend_invitation", "Resend Invitation"),
                            ("cancel_invitation", "Cancel Invitation"),
                        ],
                        max_length=24,
                    ),
                ),
                ("requested_by", models.CharField(max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("done", "Done"),
                            ("failed", "Failed"),
                        ],
                        default="queued",
                        max_length=16,
                    ),
                ),
                ("error", models.CharField(blank=True, max_length=400)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("done_at", models.DateTimeField(blank=True, null=True)),
                (
                    "hospital",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="ihr_platform.hospital",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("status__in", ["queued", "running"])),
                        fields=("hospital", "care_user_id", "action"),
                        name="one_open_request_per_user_and_action",
                    )
                ],
            },
        ),
    ]
