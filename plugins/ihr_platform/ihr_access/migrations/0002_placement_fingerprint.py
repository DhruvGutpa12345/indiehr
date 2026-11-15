"""States the role placement guard of this build (P1-T11b1, review C1).

The guard (ihr_access/placement.py) is installed and repaired only by post_migrate, which the
fleet runs only for a hospital with a migration new to it. ``PLACEMENT_SHA256`` is
``ihr_access.placement.fingerprint()`` as of this migration: its SQL and the role lists of
ihr_roles.registry it is written from. When either changes, the tests ask for a new ihr_access
migration (no operations) stating the new value, so every existing hospital migrates and gets the
new guard.
"""

from django.db import migrations

PLACEMENT_SHA256 = "255a7d423438a5ddddae09391e1704943fdf7ef7d8b19e006e6486a27c2b8372"


class Migration(migrations.Migration):
    dependencies = [("ihr_access", "0001_initial")]

    operations = []
