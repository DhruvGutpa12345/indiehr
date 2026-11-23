"""Fill CARE's new User.role_org_ids for people who were members before CARE 3.1 (P2-T0a).

CARE's users 0028 adds ``role_org_ids`` (the ids of the person's role organisations) with the
default ``[]`` and no data step; CARE keeps it current only when a role-organisation membership
is saved. Every member placed before the release, each hospital's admins on the hospital root
included, would keep ``[]``, and CARE's questionnaire-response role filter
(``created_by__role_org_ids__overlap``) would leave out what they wrote. This step derives it
exactly as ``User.populate_role_orgs_array`` does (live memberships of ``role`` organisations),
once per hospital as the fleet migrates it; running it again changes nothing.
"""

from collections import defaultdict

from django.db import migrations


def backfill(apps, schema_editor):
    user_model = apps.get_model("users", "User")
    membership_model = apps.get_model("emr", "OrganizationUser")
    wanted = defaultdict(list)
    rows = membership_model._base_manager.filter(
        deleted=False, organization__org_type="role"
    ).values_list("user_id", "organization_id")
    for user_id, organization_id in rows.order_by("id"):
        wanted[user_id].append(organization_id)
    for user_id, current in user_model._base_manager.filter(
        pk__in=list(wanted)
    ).values_list("pk", "role_org_ids"):
        if sorted(current) != sorted(wanted[user_id]):
            user_model._base_manager.filter(pk=user_id).update(
                role_org_ids=wanted[user_id]
            )


class Migration(migrations.Migration):
    dependencies = [
        ("ihr_identity", "0004_staff_directory"),
        ("users", "0028_multiple_changes"),
        ("emr", "0084_multiple_changes"),
    ]

    # Nothing to undo: users 0028's reverse drops the column
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
