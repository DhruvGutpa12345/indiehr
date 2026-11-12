"""The code as typed, in any case, found by index (P1-T10a2, benchmark run 3).

expand() looks up the code typed with ``code__iexact`` (``UPPER(code) = UPPER(%s)``) before the
word search. Without an index that read every concept of the value set: 171 ms of a 177 ms search
at SNOMED scale. Indexes are DDL, so the read-only guard's statement triggers do not fire."""

import django.db.models.functions.text
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ihr_terminology", "0004_term_only_search_text")]

    operations = [
        migrations.AddIndex(
            model_name="concept",
            index=models.Index(
                models.F("system"), django.db.models.functions.text.Upper("code"), name="ihr_term_code_upper"
            ),
        ),
    ]
