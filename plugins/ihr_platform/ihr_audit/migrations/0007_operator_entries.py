"""An ``operator`` entry in the trail (P1-T27b; Q-K136, docs/reviews/p1-t27/attack.md
"Orchestrator decision" -> "Per-hospital entries").

A recorded operator session (scripts/staging.sh) puts one entry of kind ``operator`` in each
hospital it had access to (``ihr_audit/operator.py``). The kind check gains that value, exactly as
0006 added ``restore``: expand-only, added NOT VALID and then validated, so the table is never
rewritten (the trail is append-only). A build without this migration skips the hospital
(``old-schema``); older code reading ``operator`` rows treats them as any other entry.

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
    CHECK (kind IN ('write', 'truncate', 'request', 'restore', 'operator')) NOT VALID;
ALTER TABLE ihr_audit_entry VALIDATE CONSTRAINT ihr_audit_entry_kind_check;
"""


class Migration(migrations.Migration):
    dependencies = [("ihr_audit", "0006_restore_entries")]

    # No reverse: an operator entry, once written, must stay valid
    operations = [migrations.RunSQL(KINDS)]
