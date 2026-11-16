"""States the answered-form guard of this build (P1-T15b2b; design.md §3.4, "After the attack"
items 3-5).

The guard (ihr_refdata/forms_guard.py) is installed and repaired only by post_migrate, which the
fleet runs only for a hospital with a migration new to it. ``FORMS_GUARD_SHA256`` is
``ihr_refdata.forms_guard.fingerprint()`` as of this migration: when its SQL, its trigger shape
or its frozen columns change, check ihr_refdata.W026 and the tests ask for a new ihr_refdata
migration (no operations) stating the new value, so every existing hospital migrates and gets
the new guard.
"""

from django.db import migrations

FORMS_GUARD_SHA256 = "09e84eb3fe8c13ce5af4e564c15b9cf6b96cd025b543ad419ca6d7413e02958a"


class Migration(migrations.Migration):
    dependencies = [("ihr_refdata", "0003_packitem_target_checks")]

    operations = []
