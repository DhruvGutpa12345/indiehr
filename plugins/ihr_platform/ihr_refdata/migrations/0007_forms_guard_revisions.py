"""States the answered-form guard of this build after review P2-T0a D1 and D2.

The guard (ihr_refdata/forms_guard.py) now also refuses to move an answer to another form
revision than its form's current one (CARE 3.1 resolves an answer's questions by the pair
form and revision), and keeps the archive copy that holds the questions of a revision with
answers as it was (frozen columns, ``deleted`` and hard delete). It is installed and repaired
only by post_migrate, which the fleet runs only for a hospital with a migration new to it: this
migration (no operations) states the new ``ihr_refdata.forms_guard.fingerprint()``, so every
existing hospital migrates and gets it (check ihr_refdata.W026; 0004 and 0006 state earlier
guards and are never edited).
"""

from django.db import migrations

FORMS_GUARD_SHA256 = "e1900dcc9afdb9e2270d75979090ce07ea324e81c69fb15b213168e576b18c4f"


class Migration(migrations.Migration):
    dependencies = [("ihr_refdata", "0006_forms_guard_care31")]

    operations = []
