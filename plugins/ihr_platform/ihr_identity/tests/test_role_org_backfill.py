"""CARE users 0028 added User.role_org_ids with no backfill (P2-T0a, data review F6): our
migration fills it for every user who already had a role-organisation membership."""

import importlib
import uuid

from care.emr.models.organization import Organization, OrganizationUser
from care.security.models import RoleModel
from care.users.models import User
from django.apps import apps
from django.core.management import call_command
from django.test import TestCase

from ihr_identity import roots
from ihr_identity.testing import make_root_admin

backfill = importlib.import_module(
    "ihr_identity.migrations.0005_backfill_role_org_ids"
).backfill


def _user():
    return User.objects.create_user(
        username=f"rob-{uuid.uuid4().hex[:10]}", password="x-Unused-1"
    )


class RoleOrgBackfill(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("sync_permissions_roles")  # CARE's roles, as at every start

    def test_existing_members_get_their_role_organisations(self):
        admin, other, gone = _user(), _user(), _user()
        membership = make_root_admin(self, admin)
        role = RoleModel.objects.get(name=roots.ADMIN_ROLE, is_system=True)
        district = Organization.objects.create(
            name="Backfill district", org_type="govt"
        )
        OrganizationUser.objects.create(organization=district, user=other, role=role)
        OrganizationUser.objects.create(
            organization=membership.organization, user=gone, role=role
        ).delete()  # CARE's soft delete: no longer a member
        # As each was before the release: CARE's AddField gave every row []
        User.objects.filter(pk__in=[admin.pk, other.pk, gone.pk]).update(
            role_org_ids=[]
        )

        backfill(apps, None)

        found = dict(
            User.objects.filter(pk__in=[admin.pk, other.pk, gone.pk]).values_list(
                "pk", "role_org_ids"
            )
        )
        self.assertEqual(found[admin.pk], [membership.organization_id])
        self.assertEqual(
            found[other.pk], []
        )  # a government organisation is not a role one
        self.assertEqual(found[gone.pk], [])
        # The same as CARE's own derivation
        admin.populate_role_orgs_array()
        self.assertEqual(admin.role_org_ids, found[admin.pk])

    def test_a_second_run_changes_nothing(self):
        admin = _user()
        make_root_admin(self, admin)
        backfill(apps, None)
        before = User.objects.get(pk=admin.pk).role_org_ids
        backfill(apps, None)
        self.assertEqual(User.objects.get(pk=admin.pk).role_org_ids, before)
