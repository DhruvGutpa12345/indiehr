"""The hospital root (P1-T25, Q-K76 B; docs/reviews/p1-t25/attack.md "Decision").

A hospital's root is one top-level CARE organisation of type ``role``, marked
``metadata["ihr"] == "hospital-root"``. Only an admin role on it makes someone hospital-wide
(``staff_access.hospital_wide_admin``); with no valid root nobody is. The person protected from
a reset keeps the wider rule (``top_level_admin``, Q-K130 A).
"""

import uuid
from unittest import mock

from care.emr.models.organization import Organization, OrganizationUser
from care.security.models import RoleModel
from care.users.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from ihr_access import state
from ihr_access.tests.base import SwapCase
from ihr_platform.models import Hospital, StaffInvitation, TenantOperation
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_platform.testing import as_owner

from ihr_identity import roots
from ihr_identity.models import StaffIdentity
from ihr_identity.staff_access import (
    IsHospitalAdmin,
    hospital_wide_admin,
    top_level_admin,
)
from ihr_identity.tests.test_invitations import USERS, new_user_payload

MARK = {"ihr": "hospital-root"}


def system_role(name):
    return RoleModel.objects.filter(name=name, is_system=True).order_by("id").first()


class RootCase(SwapCase):
    """The access world (CARE's system roles synced, one facility), inside one transaction."""

    def setUp(self):
        super().setUp()
        self.lines = []
        patch = mock.patch.object(roots, "_write", side_effect=self.lines.append)
        patch.start()
        self.addCleanup(patch.stop)

    def make_root(self):
        root, outcome = roots.ensure_root("test")
        self.assertIn(outcome, ("done", "exists"))
        return root

    def fresh(self, name):
        return User.objects.create_user(username=name, password="x-Unused-1")

    def member(self, user, organization, role_name="Administrator"):
        return OrganizationUser.objects.create(
            organization=organization, user=user, role=system_role(role_name)
        )


class AddStaffToTheRoot(RootCase):
    """Decision item 10 (R5, Z7, D4), run before anything else was built: a first admin who is
    Administrator on the root only can put new staff on the root through CARE's own "Add user"
    (``POST /api/v1/users/`` with ``role_orgs``), with a role up to Administrator and never a
    wider one. If this failed, ``ihr_hospital_root place`` would be the only path."""

    def add(self, client, role):
        payload = new_user_payload(
            role_orgs=[
                {
                    "organization": str(self.root.external_id),
                    "role": str(role.external_id),
                }
            ]
        )
        return client.post(USERS, payload, format="json"), payload["username"]

    def test_r5_a_root_admin_adds_staff_to_the_root_up_to_administrator(self):
        self.root = self.make_root()
        first = self.fresh("r5-first-admin")
        self.member(first, self.root)
        self.assertFalse(first.is_superuser)
        client = self.api(first)
        response, username = self.add(client, system_role("Administrator"))
        self.assertEqual(response.status_code, 200, response.content[:300])
        made = User.objects.get(username=username)
        (placed,) = OrganizationUser.objects.filter(user=made)
        self.assertEqual(
            (placed.organization_id, placed.role.name), (self.root.pk, "Administrator")
        )
        # still our invitation path: inactive, invited (P1-T5e1)
        self.assertFalse(made.is_active)
        self.assertTrue(StaffInvitation.objects.filter(care_user_id=made.pk).exists())
        # A role holding anything hers lacks is refused (CARE's ``check_role_subset``):
        # "Admin" also manages organisations, "Staff" holds clinical rights. Clinical staff
        # are placed in the facility's own organisations, as before.
        for name in ("Admin", "Staff"):
            with self.subTest(role=name):
                response, username = self.add(client, system_role(name))
                self.assertEqual(response.status_code, 403, response.content[:300])
                self.assertFalse(User.objects.filter(username=username).exists())


class Gate(RootCase):
    """Decision items 1 and 2: only an admin role on the valid root makes someone hospital-wide.
    Under the rule before P1-T25 (an admin role on any top-level organisation) the refusals for
    a state, another role organisation or a team, and every "no valid root" case, passed; the
    removed membership, the Doctor, the service account and the superuser were refused by it
    too, and are kept here because the new rule must not let them in."""

    def admin_on(self, organization, role_name="Administrator", name=None):
        user = self.fresh(
            name or f"gate-{organization.pk}-{role_name.lower().replace(' ', '')}"
        )
        self.member(user, organization, role_name)
        return user

    def test_admin_roles_on_the_root_pass(self):
        root = self.make_root()
        for name in ("Administrator", "Admin", "Facility Admin"):
            with self.subTest(role=name):
                user = self.admin_on(root, name)
                self.assertTrue(hospital_wide_admin(user))
                self.assertTrue(top_level_admin(user))

    def test_the_same_roles_elsewhere_are_refused(self):
        self.make_root()
        places = {
            "state": Organization.objects.create(name="Gate State", org_type="govt"),
            "other role": Organization.objects.create(
                name="Administrator", org_type="role"
            ),
            "team": Organization.objects.create(name="Gate Team", org_type="team"),
        }
        places["district"] = Organization.objects.create(
            name="Gate District", org_type="govt", parent=places["state"]
        )
        for label, organization in places.items():
            for name in ("Administrator", "Admin", "Facility Admin"):
                with self.subTest(place=label, role=name):
                    user = self.admin_on(organization, name)
                    self.assertFalse(hospital_wide_admin(user))
                    # the target side keeps the wider rule (Q-K130 A)
                    self.assertEqual(top_level_admin(user), label != "district")

    def test_a_removed_membership_or_a_role_without_can_create_user_is_refused(self):
        root = self.make_root()
        removed = self.admin_on(root, name="gate-removed")
        OrganizationUser.objects.filter(user=removed).update(deleted=True)
        self.assertFalse(hospital_wide_admin(removed))
        doctor = self.admin_on(root, "Doctor", name="gate-doctor")
        self.assertFalse(hospital_wide_admin(doctor))

    def test_nobody_passes_without_a_valid_root(self):
        """Fail closed (decision item 1): no marker, two marked, or the marked one inactive,
        deleted, parented, of another type or with a managing organisation."""
        root = self.make_root()
        admin = self.admin_on(root, name="gate-closed")
        self.assertTrue(hospital_wide_admin(admin))
        other = Organization.objects.create(name="Gate Other", org_type="role")
        changes = {
            "unmarked": {"metadata": {}},
            "inactive": {"active": False},
            "deleted": {"deleted": True},
            "parented": {"parent": other},
            "a team": {"org_type": "team"},
            "managed": {"managing_organizations": [other.pk]},
            # only owner code makes one (review A1): CARE's API cannot set this
            "not system-made": {"system_generated": False},
        }
        before = {f: getattr(root, f) for c in changes.values() for f in c}
        for label, change in changes.items():
            with self.subTest(root=label):
                Organization._base_manager.filter(pk=root.pk).update(**change)
                self.assertIsNone(roots.valid_root())
                self.assertFalse(hospital_wide_admin(admin))
                Organization._base_manager.filter(pk=root.pk).update(**before)
                self.assertTrue(hospital_wide_admin(admin))
        second = Organization.objects.create(
            name="Second", org_type="role", system_generated=True, metadata=dict(MARK)
        )
        self.assertIsNone(roots.valid_root())
        self.assertFalse(hospital_wide_admin(admin))
        second.delete()  # a soft delete: still marked, still two
        self.assertIsNone(roots.valid_root())
        Organization._base_manager.filter(pk=second.pk).delete()
        self.assertTrue(hospital_wide_admin(admin))
        # and with no root at all
        Organization._base_manager.filter(pk=root.pk).delete()
        self.assertFalse(hospital_wide_admin(admin))

    def test_a_service_account_or_a_superuser_alone_is_refused(self):
        root = self.make_root()
        service = self.admin_on(root, name="gate-service")
        User.objects.filter(pk=service.pk).update(is_service_account=True)
        service.refresh_from_db()
        gate = IsHospitalAdmin()
        self.assertFalse(gate.has_permission(type("R", (), {"user": service})(), None))
        self.assertFalse(
            hospital_wide_admin(self.world.admin)
        )  # a superuser, no role here

    def test_the_audit_route_follows_the_root(self):
        root = self.make_root()
        admin = self.admin_on(root, name="gate-route")
        state_admin = self.admin_on(
            Organization.objects.create(name="Route State", org_type="govt"),
            name="gate-state",
        )
        for user, expected in ((admin, 200), (state_admin, 403)):
            with self.subTest(user=user.username):
                status = self.api(user).get("/api/ihr/audit/status/").status_code
                state.end()
                self.assertEqual(status, expected)


class CareRoleOrganisationRules(RootCase):
    """Decision item 1 / review R2 and S4: no trigger guards the root; CARE's own rules for
    role organisations do, for everyone but a superuser. These pin them, so an upstream sync
    that changes them fails here. The root's own "Admin" (who manages organisations) tries."""

    def setUp(self):
        super().setUp()
        self.root = self.make_root()
        self.admin = self.fresh("rules-admin")
        self.member(self.admin, self.root, "Admin")
        self.client_ = self.api(self.admin)
        self.url = f"/api/v1/organization/{self.root.external_id}/"

    def tearDown(self):
        self.root.refresh_from_db()
        super().tearDown()

    def unchanged(self):
        root = Organization._base_manager.get(pk=self.root.pk)
        self.assertEqual(
            (root.name, root.org_type, root.active, root.deleted, root.parent_id),
            (roots.NAME, "role", True, False, None),
        )
        self.assertEqual(root.metadata, MARK)
        self.assertEqual(root.managing_organizations, [])
        self.assertEqual(roots.valid_root(), root)

    def test_it_cannot_be_updated_deactivated_or_retyped(self):
        for change in (
            {"name": "Renamed"},
            {"active": False},
            {"org_type": "team"},
        ):
            with self.subTest(change=change):
                body = {
                    "name": roots.NAME,
                    "org_type": "role",
                    "active": True,
                    "description": "",
                    **change,
                }
                response = self.client_.put(self.url, body, format="json")
                state.end()
                self.assertEqual(response.status_code, 403, response.content[:300])
                self.unchanged()

    def test_it_cannot_be_deleted(self):
        response = self.client_.delete(self.url)
        state.end()
        self.assertEqual(response.status_code, 403, response.content[:300])
        self.unchanged()
        self.assertTrue(hospital_wide_admin(self.admin))

    def test_it_can_have_no_child(self):
        response = self.client_.post(
            "/api/v1/organization/",
            {
                "name": "Under the root",
                "org_type": "team",
                "parent": str(self.root.external_id),
            },
            format="json",
        )
        state.end()
        self.assertIn(response.status_code, (400, 403), response.content[:300])
        self.assertFalse(Organization.objects.filter(parent=self.root).exists())
        self.unchanged()

    def test_a_managing_organisation_closes_the_screens_never_widens_them(self):
        """Review S4: CARE lets an Admin of the root and of another role organisation make that
        one a managing organisation of the root, whose admins could then add root members.
        ``valid_root`` refuses a managed root: the screens close for everyone."""
        other = Organization.objects.create(name="Managing", org_type="role")
        self.member(self.admin, other, "Admin")
        response = self.client_.post(
            f"{self.url}managing_organization/",
            {"organization": str(other.external_id), "action": "add"},
            format="json",
        )
        state.end()
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertIsNone(roots.valid_root())
        self.assertFalse(hospital_wide_admin(self.admin))


class AnAdminElsewhereCannotJoinTheRoot(RootCase):
    """Review Q1: the gate trusts CARE's own check (``can_manage_organization_users_obj``) to keep
    people out of a role organisation, and the tests above are all made by the root's own admin.
    An admin who holds an admin role only elsewhere (a state's Administrator, a facility's
    Facility Admin) must not be able to put anyone on the root, by CARE's "Add user"
    (``POST /api/v1/users/`` with ``role_orgs``) or by adding a member to the organisation. If an
    upstream sync loosened that check, this fails before anyone is made hospital-wide."""

    def test_an_admin_elsewhere_cannot_add_anyone_to_the_root(self):
        root = self.make_root()
        state_org = Organization.objects.create(name="Elsewhere State", org_type="govt")
        state_admin = self.fresh("elsewhere-state-admin")
        self.member(state_admin, state_org)
        outsiders = {
            "state Administrator": state_admin,
            "Facility Admin": self.world.users["Facility Admin"],
        }
        target = self.fresh("elsewhere-target")
        before = OrganizationUser.objects.filter(organization=root).count()
        for label, outsider in outsiders.items():
            client = self.api(outsider)
            for role_name in ("Administrator", "Doctor"):
                role = system_role(role_name)
                with self.subTest(who=label, role=role_name):
                    payload = new_user_payload(
                        role_orgs=[
                            {
                                "organization": str(root.external_id),
                                "role": str(role.external_id),
                            }
                        ]
                    )
                    created = client.post(USERS, payload, format="json")
                    state.end()
                    self.assertEqual(created.status_code, 403, created.content[:300])
                    self.assertFalse(
                        User.objects.filter(username=payload["username"]).exists()
                    )
                    added = client.post(
                        f"/api/v1/organization/{root.external_id}/users/",
                        {
                            "user": str(target.external_id),
                            "role": str(role.external_id),
                        },
                        format="json",
                    )
                    state.end()
                    self.assertEqual(added.status_code, 403, added.content[:300])
        self.assertEqual(
            OrganizationUser.objects.filter(organization=root).count(), before
        )
        self.assertFalse(hospital_wide_admin(target))
        self.assertFalse(hospital_wide_admin(state_admin))


class MarkerFromTheApi(RootCase):
    """Review A1: CARE's organisation API takes ``metadata``, so an "Admin" of a team can make
    or edit a team organisation carrying the marker. Only a system-made organisation (owner
    code; CARE's write specs have no ``system_generated``) counts, so the root and its admins
    are unchanged: nobody below hospital-wide admin can switch the screens off."""

    def test_a_marked_organisation_made_or_edited_through_the_api_is_ignored(self):
        root = self.make_root()
        hospital_admin = self.fresh("api-mark-root")
        self.member(hospital_admin, root)
        team = Organization.objects.create(name="Api Team", org_type="team")
        team_admin = self.fresh("api-mark-team")
        self.member(team_admin, team, "Admin")
        client = self.api(team_admin)
        created = client.post(
            "/api/v1/organization/",
            {
                "name": "Api Marked",
                "org_type": "team",
                "parent": str(team.external_id),
                "metadata": dict(MARK),
            },
            format="json",
        )
        state.end()
        self.assertEqual(created.status_code, 200, created.content[:300])
        made = Organization._base_manager.get(external_id=created.json()["id"])
        self.assertEqual((made.metadata, made.system_generated), (MARK, False))
        edited = client.put(
            f"/api/v1/organization/{team.external_id}/",
            {
                "name": "Api Team",
                "org_type": "team",
                "active": True,
                "description": "",
                "metadata": dict(MARK),
            },
            format="json",
        )
        state.end()
        self.assertEqual(edited.status_code, 200, edited.content[:300])
        team.refresh_from_db()
        self.assertEqual(team.metadata, MARK)
        self.assertEqual(roots.valid_root(), root)
        self.assertEqual(list(roots.marked()), [root])
        self.assertTrue(hospital_wide_admin(hospital_admin))
        self.assertFalse(hospital_wide_admin(team_admin))
        self.assertEqual(roots.ensure_root("test"), (root, "exists"))


class EnsureAndPlace(RootCase):
    def test_one_root_and_never_a_second(self):
        root, made = roots.ensure_root("test")
        self.assertEqual(made, "done")
        self.assertEqual(
            (
                root.name,
                root.org_type,
                root.system_generated,
                root.parent_id,
                root.metadata,
            ),
            (roots.NAME, "role", True, None, MARK),
        )
        again, outcome = roots.ensure_root("test")
        self.assertEqual((again, outcome), (root, "exists"))
        self.assertEqual(roots.marked().count(), 1)
        self.assertIn(
            "ihr-root: hospital=testhosp action=ensure outcome=done by=test", self.lines
        )

    def test_it_never_adopts_by_name(self):
        named = Organization.objects.create(name=roots.NAME, org_type="role")
        root, made = roots.ensure_root("test")
        self.assertEqual(made, "done")
        self.assertNotEqual(root.pk, named.pk)
        named.refresh_from_db()
        self.assertEqual(named.metadata, {})

    def test_an_invalid_root_is_left_alone(self):
        root = self.make_root()
        Organization.objects.filter(pk=root.pk).update(active=False)
        self.assertEqual(roots.ensure_root("op.x"), (None, "invalid"))
        self.assertEqual(roots.marked().count(), 1)
        self.assertIn(
            "ihr-root: hospital=testhosp action=ensure outcome=invalid by=op.x",
            self.lines,
        )
        with self.assertRaisesMessage(roots.RootError, "no valid root"):
            roots.place(self.fresh("place-none"), "test")

    def test_place_refuses_superusers_and_service_accounts(self):
        self.make_root()
        service = self.fresh("place-svc")
        User.objects.filter(pk=service.pk).update(is_service_account=True)
        service.refresh_from_db()
        for user in (self.world.admin, service):
            with self.subTest(user=user.username), self.assertRaises(roots.RootError):
                roots.place(user, "test")
        self.assertFalse(roots.admins(roots.valid_root()).exists())

    def test_the_line_never_carries_an_address(self):
        self.assertEqual(
            roots.log_line("alpha", "place", "done", "first.admin@example.test"),
            "ihr-root: hospital=alpha action=place outcome=done by=first.admin-example.test",
        )
        self.assertEqual(roots.operator_token(""), "unknown")


class Converge(RootCase):
    """The fleet step ``root`` and the restore (decision items 5 and 7, review S1/D2): a root
    with no admin gets the provisioning record's first admin, only if still entitled, and
    only that person's Administrator row on a state is dropped."""

    def setUp(self):
        super().setUp()
        self.hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
        self.state_org = Organization.objects.create(name="Conv State", org_type="govt")
        self.first = self.fresh("conv-first")
        self.member(self.first, self.state_org)
        self.colleague = self.fresh("conv-colleague")
        self.member(self.colleague, self.state_org)

    def onboarding(self, user_id):
        with as_owner():
            TenantOperation.objects.create(
                kind=TenantOperation.Kind.PROVISION,
                hospital=self.hospital,
                slug=self.hospital.slug,
                status=TenantOperation.Status.SUCCEEDED,
                requested_by="test",
                result={"admin_user_id": user_id},
            )

    def test_the_onboarding_admin_is_moved_onto_the_root(self):
        self.onboarding(self.first.pk)
        report = roots.converge(self.hospital, "fleet-test")
        self.assertEqual(
            (report["outcome"], report["created"], report["placed"], report["removed"]),
            ("done", True, self.first.pk, 1),
        )
        self.assertTrue(hospital_wide_admin(self.first))
        self.assertFalse(
            OrganizationUser.objects.filter(
                user=self.first, organization=self.state_org
            ).exists()
        )
        # nobody else is moved or removed: the colleague keeps the state row (S5)
        self.assertFalse(hospital_wide_admin(self.colleague))
        self.assertTrue(
            OrganizationUser.objects.filter(
                user=self.colleague, organization=self.state_org
            ).exists()
        )
        self.assertEqual(report["state_admins"], 1)
        self.assertIn(
            "ihr-root: hospital=testhosp action=ensure outcome=state-admins n=1 "
            "by=fleet-test",
            self.lines,
        )
        # again: the root has an admin, nothing changes
        again = roots.converge(self.hospital, "fleet-test")
        self.assertEqual((again["outcome"], again["placed"]), ("exists", None))
        self.assertEqual(roots.marked().count(), 1)

    def test_an_admin_no_longer_entitled_is_not_placed(self):
        """Review S1/D2: gone, deactivated, or no longer an admin anywhere at the top."""
        cases = {
            "deleted": lambda u: User.objects.filter(pk=u.pk).update(deleted=True),
            "deactivated": lambda u: User.objects.filter(pk=u.pk).update(
                is_active=False
            ),
            "lost the role": lambda u: OrganizationUser.objects.filter(user=u).update(
                deleted=True
            ),
        }
        for label, change in cases.items():
            with self.subTest(case=label):
                user = self.fresh(f"conv-{label[:4]}")
                self.member(user, self.state_org)
                change(user)
                with as_owner():
                    TenantOperation.objects.filter(hospital=self.hospital).delete()
                self.onboarding(user.pk)
                report = roots.converge(self.hospital, "fleet-test")
                self.assertEqual(
                    (report["outcome"], report["placed"]), ("no-admin", None)
                )
                self.assertFalse(roots.admins(roots.valid_root()).exists())
        self.assertIn(
            "ihr-root: hospital=testhosp action=place outcome=no-admin by=fleet-test",
            self.lines,
        )

    def test_awaiting_setup_counts_as_entitled(self):
        """The first admin is inactive until their first sign-in (P1-T5c)."""
        User.objects.filter(pk=self.first.pk).update(is_active=False)
        StaffIdentity.objects.create(
            user=self.first,
            keycloak_sub=uuid.uuid4(),
            awaiting_setup=True,
        )
        self.onboarding(self.first.pk)
        self.assertEqual(roots.converge(self.hospital, "t")["placed"], self.first.pk)

    def test_placing_and_dropping_the_state_row_are_one_change(self):
        """Review T2: a failure between the two never leaves the admin on the root with their
        state row (a run that sees an admin on the root would never drop it)."""
        self.onboarding(self.first.pk)
        with (
            mock.patch.object(roots, "remove_state", side_effect=RuntimeError("cut")),
            self.assertRaisesMessage(RuntimeError, "cut"),
        ):
            roots.converge(self.hospital, "fleet-test")
        root = roots.valid_root()
        self.assertIsNotNone(root)  # the root itself stays made
        self.assertFalse(roots.admins(root).exists())
        report = roots.converge(self.hospital, "fleet-test")
        self.assertEqual((report["placed"], report["removed"]), (self.first.pk, 1))
        self.assertFalse(
            OrganizationUser.objects.filter(
                user=self.first, organization=self.state_org, deleted=False
            ).exists()
        )

    def test_a_root_whose_only_admin_is_deactivated_has_no_admin(self):
        """Review A3: an admin who can no longer sign in (deactivated, not awaiting setup)
        does not count, so the step says ``no-admin`` (and the alert fires)."""
        root = self.make_root()
        gone = self.fresh("conv-gone")
        self.member(gone, root)
        User.objects.filter(pk=gone.pk).update(is_active=False)
        self.assertFalse(roots.live_admins(root).exists())
        report = roots.converge(self.hospital, "fleet-test")
        self.assertEqual((report["outcome"], report["placed"]), ("no-admin", None))
        self.assertIn(
            "ihr-root: hospital=testhosp action=place outcome=no-admin by=fleet-test",
            self.lines,
        )
        # awaiting setup counts (P1-T5c), as does an active admin
        StaffIdentity.objects.create(
            user=gone, keycloak_sub=uuid.uuid4(), awaiting_setup=True
        )
        self.assertTrue(roots.live_admins(root).exists())
        self.assertEqual(roots.converge(self.hospital, "t")["outcome"], "exists")

    def test_no_onboarding_record_or_an_invalid_root(self):
        report = roots.converge(self.hospital, "t")
        self.assertEqual((report["outcome"], report["created"]), ("no-admin", True))
        Organization.objects.filter(pk=roots.valid_root().pk).update(
            parent=self.state_org
        )
        self.onboarding(self.first.pk)
        report = roots.converge(self.hospital, "t")
        self.assertEqual((report["outcome"], report["placed"]), ("invalid", None))
        self.assertEqual(roots.marked().count(), 1)  # never replaced
        self.assertFalse(hospital_wide_admin(self.first))


class OperatorCommand(RootCase):
    def run_command(self, *args, **options):
        import io

        out = io.StringIO()
        with as_owner():
            call_command(
                "ihr_hospital_root", "--slug", "testhosp", *args, stdout=out, **options
            )
        return out.getvalue()

    def test_an_operator_cannot_pose_as_an_automatic_source(self):
        """Review T4: the audit actor is ``root:<operator>``, so a name that an automatic source
        writes (the fleet runner, a restore, provisioning, the seed, ``ihr_create_tenant``)
        would make a manual write look automatic."""
        self.make_root()
        self.fresh("cmd-poser")
        for name in (
            "fleet-startup",
            "Fleet-x",
            "restore-op.x",
            "provisioning-12",
            "demo-seed",
            "ihr_create_tenant",
            "test",
        ):
            for action in ("place", "remove-state"):
                with (
                    self.subTest(operator=name, action=action),
                    self.assertRaisesMessage(CommandError, "--operator"),
                ):
                    self.run_command(
                        action, "cmd-poser", operator=name, reason="ticket 1"
                    )
        self.assertFalse(
            OrganizationUser.objects.filter(user__username="cmd-poser").exists()
        )
        self.assertIn(
            "placed on the root",
            self.run_command("place", "cmd-poser", operator="op.x", reason="ticket 1"),
        )

    def test_show_place_and_remove_state(self):
        self.assertIn("no valid root (none is marked)", self.run_command("show"))
        root = self.make_root()
        state_org = Organization.objects.create(name="Cmd State", org_type="govt")
        user = self.fresh("cmd-admin")
        self.member(user, state_org)
        shown = self.run_command("show")
        self.assertIn("admins on the root (0): none", shown)
        self.assertIn("cmd-admin: Administrator on 'Cmd State' (govt)", shown)
        with self.assertRaisesMessage(CommandError, "--operator"):
            self.run_command("place", "cmd-admin", reason="ticket 1")
        with self.assertRaisesMessage(CommandError, "--reason"):
            self.run_command("place", "cmd-admin", operator="op.x")
        self.assertIn(
            "placed on the root",
            self.run_command("place", "cmd-admin", operator="op.x", reason="ticket 1"),
        )
        self.assertTrue(roots.admins(root).filter(user=user).exists())
        self.assertIn(
            "1 state Administrator row(s) removed",
            self.run_command(
                "remove-state", "cmd-admin", operator="op.x", reason="ticket 1"
            ),
        )
        shown = self.run_command("show")
        self.assertIn("admins on the root (1): cmd-admin", shown)
        self.assertNotIn("Cmd State", shown)
