from django.db import migrations

# Format 2 (P1-T6b): an entry's canonical text holds the sha256 of its values instead of the
# values, so an export can prove its entries without revealing them (seal.py). Seals made
# before this keep format 1 and are still verified with it.
ADD = """
SET LOCAL lock_timeout = '5s';  -- never queue every write behind a long transaction
ALTER TABLE ihr_audit_seal
    ADD COLUMN version smallint NOT NULL DEFAULT 1 CHECK (version IN (1, 2));
"""


class Migration(migrations.Migration):
    dependencies = [("ihr_audit", "0001_initial")]
    operations = [migrations.RunSQL(ADD)]
