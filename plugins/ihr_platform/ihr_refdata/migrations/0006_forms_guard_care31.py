"""States the answered-form guard of this build on CARE 3.1 (P2-T0a; review F2 and F3).

The guard (ihr_refdata/forms_guard.py) now also covers CARE 3.1's facility-resource and
referral answers, freezes the new columns an answer depends on (``internal_revision``,
``latest_revision_id``, ``auth_context``, ``facility_id``, ``facility_organization_id``) and lets
CARE's archive copy of a live pack form take that form's slug. It is installed and repaired only
by post_migrate, which the fleet runs only for a hospital with a migration new to it: this
migration (no operations) states the new ``ihr_refdata.forms_guard.fingerprint()``, so every
existing hospital migrates and gets it (check ihr_refdata.W026; 0004 states the first guard and
is never edited).
"""

from django.db import migrations

FORMS_GUARD_SHA256 = "e0c24aaf6701d5822cd00c395999ec6b3928f5586f17d51ba3f68028f7fc7ce8"


class Migration(migrations.Migration):
    dependencies = [("ihr_refdata", "0005_packitem_facility")]

    operations = []
