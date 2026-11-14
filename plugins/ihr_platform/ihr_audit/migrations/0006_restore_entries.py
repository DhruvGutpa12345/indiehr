"""A ``restore`` entry in the trail (P1-T22b; docs/reviews/p1-t22/design.md, "Corrections" 3.4).

A single-hospital restore adds one entry of kind ``restore`` to the restored trail: who restored
it, from which dump, and which live seals it superseded (their archive and the restore record are
outside the schema). The kind check gains that value; nothing else changes. The constraint is
added NOT VALID and then validated, so the table is never rewritten (the trail is append-only).

``AUDIT_TRIGGERS_SHA256`` is unchanged (ihr_audit.install.config_fingerprint(): no trigger or
sealing configuration changed), stated again because the tests read it from the latest migration.
"""

from django.db import migrations

AUDIT_TRIGGERS_SHA256 = (
    "100e4d3f3d38be723e26090e2ebf9204c6e5c1defdff4ee8dfb0d163836bfffd"
)

KINDS = """
SET LOCAL lock_timeout = '10s';
ALTER TABLE ihr_audit_entry DROP CONSTRAINT IF EXISTS ihr_audit_entry_kind_check;
ALTER TABLE ihr_audit_entry ADD CONSTRAINT ihr_audit_entry_kind_check
    CHECK (kind IN ('write', 'truncate', 'request', 'restore')) NOT VALID;
ALTER TABLE ihr_audit_entry VALIDATE CONSTRAINT ihr_audit_entry_kind_check;
"""


class Migration(migrations.Migration):
    dependencies = [("ihr_audit", "0005_omit_cached_parent_json")]

    # No reverse: a restore entry, once written, must stay valid
    operations = [migrations.RunSQL(KINDS)]
