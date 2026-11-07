from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ihr_identity", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="staffidentity",
            name="awaiting_setup",
            field=models.BooleanField(default=False),
        ),
    ]
