from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ihr_identity", "0002_staffidentity_awaiting_setup"),
    ]

    operations = [
        migrations.AddField(
            model_name="staffidentity",
            name="join_confirmation_required",
            field=models.BooleanField(default=False),
        ),
    ]
