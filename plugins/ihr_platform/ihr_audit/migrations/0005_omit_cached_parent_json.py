"""No schema change: a new step so every hospital re-runs post_migrate (P1-T20a, review C2).

The audit's OMIT now leaves out the organisations' cached parent copy (review A6), and only
post_migrate (ihr_audit.apps._install: ensure_triggers, ensure_sealing) re-creates a hospital's
triggers. The fleet migrates a hospital only when a migration is new to it, so without this step
existing hospitals would keep the old triggers. Inside the release window the held migrate then
brings each hospital's triggers to this build.

``AUDIT_TRIGGERS_SHA256`` is ihr_audit.install.config_fingerprint() as of this migration. The
tests compare it with the live value: a later change to the trigger or sealing configuration
fails them until it ships with its own migration stating the new fingerprint.
"""

from django.db import migrations

AUDIT_TRIGGERS_SHA256 = (
    "100e4d3f3d38be723e26090e2ebf9204c6e5c1defdff4ee8dfb0d163836bfffd"
)


class Migration(migrations.Migration):
    dependencies = [("ihr_audit", "0004_owner_writes_the_chain")]

    operations = []
