"""The eight roles, the hidden role and their exact grants (P1-T11b1; design-b.md §9 tests 1-4)."""

from care.security.models import PermissionModel, RoleModel, RolePermission
from care.security.permissions.base import PermissionController
from care.security.roles.role import RoleContext, RoleController
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from ihr_roles import care_permissions, registry
from ihr_roles.checks import permissions_registered
from ihr_roles.permissions import SLUGS

OURS = set(registry.PERMANENT_NAMES)


def held(role_name):
    return set(
        RolePermission.objects.filter(
            role__name=role_name, role__is_system=True
        ).values_list("permission__slug", flat=True)
    )


class ExactGrants(TestCase):
    def setUp(self):
        call_command("sync_permissions_roles")  # as at every start

    def test_each_of_our_roles_holds_exactly_its_grants(self):
        for name in registry.PERMANENT_NAMES:
            with self.subTest(role=name):
                self.assertEqual(held(name), set(registry.GRANTS.get(name, [])))

    def test_no_care_permission_lists_our_roles_beyond_the_grants(self):
        """A permission that newly names one of our roles (upstream, or an append here) fails."""
        for slug, permission in PermissionController.get_permissions().items():
            for role in permission.roles:
                if role.name in OURS:
                    with self.subTest(slug=slug, role=role.name):
                        self.assertIn(slug, registry.GRANTS[role.name])

    def test_care_roles_are_unchanged(self):
        self.assertTrue(registry.ORIGINAL)  # recorded before the first grant
        for handler in PermissionController.internal_permission_handlers:
            for perm in handler:
                with self.subTest(slug=perm.name):
                    now = [r.name for r in perm.value.roles if r.name not in OURS]
                    self.assertEqual(now, registry.ORIGINAL[perm.name])
        care_names = {r.name for r in RoleController.internal_roles}
        for name in care_names:
            with self.subTest(role=name):
                expected = {
                    s for s, names in registry.ORIGINAL.items() if name in names
                }
                self.assertEqual(held(name), expected)

    def test_our_permissions_are_registered_and_synced(self):
        self.assertEqual(permissions_registered(), [])
        known = PermissionController.get_permissions()
        self.assertTrue(set(SLUGS) <= set(known))
        rows = set(
            PermissionModel.objects.filter(slug__in=SLUGS).values_list(
                "slug", flat=True
            )
        )
        self.assertEqual(rows, set(SLUGS))

    def test_the_hidden_role_is_offered_in_no_context(self):
        self.assertEqual(RoleModel.objects.get(name=registry.HIDDEN).contexts, [])
        for name in registry.EIGHT:
            self.assertEqual(
                RoleModel.objects.get(name=name, is_system=True).contexts,
                [RoleContext.FACILITY.value],
            )


class GrantGuards(SimpleTestCase):
    def test_restricted_roles_never_open_a_patient_or_clinical_data(self):
        for name in [*registry.RESTRICTED, "MRD"]:
            with self.subTest(role=name):
                self.assertEqual(
                    set(registry.GRANTS[name]) & registry.NEVER_RESTRICTED, set()
                )

    def test_the_owner_sees_totals_only(self):
        self.assertEqual(registry.GRANTS["Owner"], ["ihr_view_hospital_totals"])

    def test_none_of_ours_is_an_admin(self):
        for name, slugs in registry.GRANTS.items():
            with self.subTest(role=name):
                self.assertNotIn("can_create_user", slugs)

    def test_billing_approvals_stay_with_facility_admin(self):
        approvals = {
            "can_create_negative_charge_item",
            "can_cancel_charge_item",
            "can_destroy_invoice",
            "can_manage_locked_invoice",
            "can_destroy_payment_reconciliation",
        }
        for name, slugs in registry.GRANTS.items():
            with self.subTest(role=name):
                self.assertEqual(set(slugs) & approvals, set())

    def test_the_hidden_role_reads_and_never_writes(self):
        self.assertEqual(
            set(registry.GRANTS[registry.HIDDEN]),
            {
                "can_list_patients",
                "can_view_clinical_data",
                "can_view_questionnaire_responses",
            },
        )

    def test_clinical_slugs_exist(self):
        known = PermissionController.get_permissions()
        self.assertEqual(registry.NEVER_RESTRICTED - set(known), set())


class CarePermissionsReviewed(SimpleTestCase):
    """Upstream drift in CARE's permissions fails here until reviewed (review C6):
    ``registry.ORIGINAL`` is rebuilt from the new code at every start, so only this pinned
    snapshot sees a new slug or a changed role list."""

    def test_care_permissions_match_the_reviewed_snapshot(self):
        reviewed = care_permissions.read()
        live = care_permissions.live()
        changed = []
        for slug, value in sorted(live.items()):
            if value is None:
                changed.append(f"{slug}: registered twice with different values")
                continue
            context, roles = value
            have = reviewed.get(slug)
            if have is None or have[:2] != (context, roles):
                cls = have[2] if have else care_permissions.classify(slug)
                changed.append(
                    ("new: " if have is None else "changed: ")
                    + care_permissions.line(slug, context, roles, cls)
                )
        changed += [f"removed: {slug}" for slug in sorted(set(reviewed) - set(live))]
        self.assertEqual(
            changed,
            [],
            "CARE's permissions differ from ihr_roles/care_permissions.txt: review each "
            "line (class: clinical, opening or other), then update the file",
        )

    def test_the_classes_are_the_registrys(self):
        reviewed = care_permissions.read()
        by_class = {
            cls: {s for s, (_c, _r, k) in reviewed.items() if k == cls}
            for cls in care_permissions.CLASSES
        }
        self.assertEqual(by_class["clinical"], set(registry.CLINICAL_SLUGS))
        self.assertEqual(by_class["opening"], set(registry.PATIENT_OPENING))
