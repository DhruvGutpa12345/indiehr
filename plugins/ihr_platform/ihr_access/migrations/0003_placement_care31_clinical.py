"""States the role placement guard of this build (P2-T0a: CARE 3.1).

CARE 3.1's immunisation records and recommendations and medication returns joined
``ihr_roles.registry.CLINICAL_SLUGS``, which the guard (ihr_access/placement.py) is written
from; referrals did not (owner, Q-K163: ihr_access/referrals.py closes them to restricted users
instead). ``PLACEMENT_SHA256`` is ``ihr_access.placement.fingerprint()`` as of this migration, so
every existing hospital migrates and post_migrate installs the new guard (0002's docstring;
tests.test_placement.GuardChangesShipWithAMigration).
"""

from django.db import migrations

PLACEMENT_SHA256 = "827300a0c7e0a6dd1ae307f82d3ef947d35b72e7a1807f865229f377efbb3538"


class Migration(migrations.Migration):
    dependencies = [("ihr_access", "0002_placement_fingerprint")]

    operations = []
