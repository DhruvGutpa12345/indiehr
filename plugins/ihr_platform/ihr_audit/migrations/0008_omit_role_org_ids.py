"""No schema change: a new step so every hospital re-runs post_migrate (P2-T0a, data review F6).

CARE 3.1 (users 0028) added ``users_user.role_org_ids``, a copy of the person's role
organisations that CARE rewrites on every role-organisation membership save, like
``cached_role_orgs``. The audit's OMIT now leaves it out (the membership rows themselves are
audited), and only post_migrate re-creates a hospital's triggers, as in 0005. The same step
leaves out ``emr_facilitylocation.cached_parent_json``, the parent chain CARE copies into a
location on read, which emr 0084 empties on every location (review TPA-2).

``AUDIT_TRIGGERS_SHA256`` is ihr_audit.install.config_fingerprint() as of this migration.
"""

from django.db import migrations

AUDIT_TRIGGERS_SHA256 = (
    "954dad56a525fac7c7eda757a316ba2d61ebcf62c097fbba6fb993a1cdce4b79"
)


class Migration(migrations.Migration):
    dependencies = [("ihr_audit", "0007_operator_entries")]

    operations = []
