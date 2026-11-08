from django.db import migrations

# Format 3 (P1-T6b review): the hash of an entry's values is salted with 16 random bytes kept
# only in the database, so an export's hashes cannot be turned back into short values (a
# status, a blood group, the last 4 digits of an identifier) by trying every candidate.
# Entries written before this have no salt and are hashed as before.
ADD = """
SET LOCAL lock_timeout = '5s';  -- never queue every write behind a long transaction
ALTER TABLE ihr_audit_entry ADD COLUMN salt bytea;
ALTER TABLE ihr_audit_entry ALTER COLUMN salt SET DEFAULT uuid_send(gen_random_uuid());
ALTER TABLE ihr_audit_seal DROP CONSTRAINT ihr_audit_seal_version_check;
ALTER TABLE ihr_audit_seal ADD CONSTRAINT ihr_audit_seal_version_check
    CHECK (version IN (1, 2, 3));
"""


class Migration(migrations.Migration):
    dependencies = [("ihr_audit", "0002_seal_version")]
    operations = [migrations.RunSQL(ADD)]
