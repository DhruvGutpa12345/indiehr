"""The guarded role sync and the role registry (P1-T11a, ADR-017)."""

from types import SimpleNamespace

from care.emr.models.organization import Organization, OrganizationUser
from care.security.models import RoleModel
from care.security.roles.role import RoleController
from care.users.models import User
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command, get_commands, load_command_class
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from ihr_roles import registry


class GuardedSync(TestCase):
    def setUp(self):
        call_command("sync_permissions_roles")  # CARE's roles as at every start

    def test_our_command_shadows_cares(self):
        self.assertEqual(get_commands()["sync_permissions_roles"], "ihr_roles")
        self.assertEqual(type(load_command_class("ihr_roles", "sync_permissions_roles")).__module__,
                         "ihr_roles.management.commands.sync_permissions_roles")  # fmt: skip

    def _held_ghost(self):
        """A system role no longer in code (as after a rollback or a rename), held by someone."""
        ghost = RoleModel.objects.create(
            name="Ghost role", is_system=True, contexts=["FACILITY"]
        )
        user = User.objects.create_user(username="ghost-holder", password="x-Unused-1")
        org = Organization.objects.create(name="Ghost team", org_type="team")
        OrganizationUser.objects.create(organization=org, user=user, role=ghost)
        return ghost, user

    def test_refuses_to_delete_a_held_role(self):
        ghost, user = self._held_ghost()
        with self.assertRaises(CommandError) as refused:
            call_command("sync_permissions_roles")
        self.assertIn("Ghost role", str(refused.exception))
        self.assertTrue(RoleModel.objects.filter(pk=ghost.pk).exists())
        self.assertTrue(OrganizationUser.objects.filter(user=user, role=ghost).exists())

    def test_refuses_also_for_a_soft_deleted_membership(self):
        _ghost, user = self._held_ghost()
        OrganizationUser.objects.filter(user=user).update(deleted=True)
        with self.assertRaises(CommandError):
            call_command("sync_permissions_roles")

    def test_an_unheld_stale_role_is_removed_as_care_does(self):
        RoleModel.objects.create(
            name="Unheld stale role", is_system=True, contexts=["FACILITY"]
        )
        call_command("sync_permissions_roles")
        self.assertFalse(RoleModel.objects.filter(name="Unheld stale role").exists())
        self.assertEqual(set(RoleModel.objects.filter(is_system=True).values_list("name", flat=True)),
                         {r.name for r in RoleController.get_roles()})  # fmt: skip

    def test_refuses_to_take_over_a_hospitals_own_role(self):
        name = RoleController.get_roles()[0].name
        RoleModel.objects.filter(name=name).update(is_system=False)
        with self.assertRaises(CommandError) as refused:
            call_command("sync_permissions_roles")
        self.assertIn(name, str(refused.exception))

    def test_check_mode_reports_and_changes_nothing(self):
        ghost, _user = self._held_ghost()
        with self.assertRaises(CommandError) as refused:
            call_command("sync_permissions_roles", check=True)
        self.assertIn("would be refused", str(refused.exception))
        self.assertTrue(RoleModel.objects.filter(pk=ghost.pk).exists())
        RoleModel.objects.filter(pk=ghost.pk).update(
            is_system=False, name="Ghost role, now a hospital role"
        )
        call_command(
            "sync_permissions_roles", check=True
        )  # nothing to refuse: returns quietly

    def test_a_soft_deleted_hospital_role_never_blocks(self):
        """CARE's sync never touches soft-deleted roles, so neither may the guard (else every
        start-up would stop for good on one deleted row)."""
        name = RoleController.get_roles()[0].name
        RoleModel.objects.create(
            name=name, is_system=False, contexts=["FACILITY"], deleted=True
        )
        call_command("sync_permissions_roles")

    def test_permission_removed_but_granted_to_a_hospitals_own_role(self):
        from care.security.models import (
            PermissionModel,
            RolePermission,
        )

        own = RoleModel.objects.create(
            name="Hospital's own role", is_system=False, contexts=["FACILITY"]
        )
        gone = PermissionModel.objects.create(
            slug="can_ghost_permission",
            name="Ghost",
            description="",
            context="FACILITY",
        )
        RolePermission.objects.create(role=own, permission=gone)
        with self.assertRaises(CommandError) as refused:
            call_command("sync_permissions_roles")
        self.assertIn("can_ghost_permission", str(refused.exception))
        self.assertTrue(
            RolePermission.objects.filter(role=own, permission=gone).exists()
        )

    def test_every_reverse_relation_of_roles_is_counted(self):
        from ihr_roles.management.commands.sync_permissions_roles import (
            holders,
        )

        counted = {rel.related_model.__name__ for rel in holders()}
        self.assertTrue(
            {"OrganizationUser", "FacilityOrganizationUser", "PatientUser"} <= counted
        )
        self.assertNotIn("RolePermission", counted)


class Registry(SimpleTestCase):
    def test_grant_never_changes_a_shared_list(self):
        shared = [SimpleNamespace(name="Doctor")]
        a, b = (
            SimpleNamespace(value=SimpleNamespace(roles=shared)),
            SimpleNamespace(value=SimpleNamespace(roles=shared)),
        )
        role = SimpleNamespace(name="Test role")
        original = registry._permission
        registry._permission = lambda slug: a
        try:
            registry.grant("anything", role)
            registry.grant("anything", role)  # twice: still once
        finally:
            registry._permission = original
        self.assertEqual([r.name for r in a.value.roles], ["Doctor", "Test role"])
        self.assertEqual(
            [r.name for r in b.value.roles], ["Doctor"]
        )  # the other permission is untouched
        self.assertIs(b.value.roles, shared)

    def test_unknown_permission_fails_at_start(self):
        with self.assertRaises(ImproperlyConfigured):
            registry.grant(
                "can_do_something_that_does_not_exist", SimpleNamespace(name="x")
            )

    def test_names_are_permanent_and_never_clash(self):
        care = RoleController.internal_roles[0]
        with self.assertRaises(ImproperlyConfigured):
            registry.install(roles=[care], grants={}, permanent=[care.name])
        role = SimpleNamespace(name="Unlisted role", contexts=[])
        with self.assertRaises(ImproperlyConfigured):
            registry.install(
                roles=[role], grants={}, permanent=[]
            )  # not in PERMANENT_NAMES
        with self.assertRaises(ImproperlyConfigured):
            registry.install(
                roles=[], grants={}, permanent=["Once registered"]
            )  # removed later

    def test_the_shipped_registry_is_consistent(self):
        self.assertEqual(
            [r.name for r in registry.ROLES], list(registry.PERMANENT_NAMES)
        )
        self.assertLessEqual(set(registry.GRANTS), {r.name for r in registry.ROLES})
