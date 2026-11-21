"""The demo seed's hospital-wide admin (P1-T24a, G4, Q-K117 A).

``admin.priya`` signs in to alpha as CARE's fixture user ``care-admin``. The seed step
``make_hospital_admin`` makes sure that user holds an admin role on an active top-level
organisation (``IsHospitalAdmin``: the audit trail and pack updates) and a place at the demo
facility's root, because the admin pages live under /facility/:id/ (Q-K110 A). Synthetic data
only; it must be safe to run again.
"""

from unittest.mock import patch

from care.emr.models.organization import (
    FacilityOrganizationUser,
    Organization,
    OrganizationUser,
)
from care.security.authorization import AuthorizationController
from django.contrib.auth import get_user_model
from ihr_identity.staff_access import hospital_wide_admin
from ihr_platform import seeding
from ihr_platform.management.commands import ihr_keycloak_demo_users, ihr_seed_demo
from ihr_platform.seeding import (
    FACILITY_ADMIN,
    HOSPITAL_ADMIN,
    make_facility_admin,
    make_hospital_admin,
)

from ihr_access import state
from ihr_access.tests.base import SwapCase


class SeedHospitalAdmin(SwapCase):
    def fresh(self, name):
        return get_user_model().objects.create_user(
            username=name, password="x-Unused-1"
        )

    def test_the_login_and_the_care_user(self):
        self.assertEqual(HOSPITAL_ADMIN, "care-admin")
        self.assertIn(HOSPITAL_ADMIN, ihr_seed_demo.FIXTURE_USERS)
        (priya,) = [
            row for row in ihr_keycloak_demo_users.DEMO_STAFF if row[0] == "admin.priya"
        ]
        self.assertEqual(priya[3], {"alpha": HOSPITAL_ADMIN})
        self.assertTrue(priya[4])  # authenticator pre-set, like the other demo staff

    def test_makes_a_hospital_wide_admin_who_reaches_the_facility(self):
        w = self.world
        user = self.fresh("seed-admin-new")
        self.assertFalse(hospital_wide_admin(user))
        self.assertTrue(make_hospital_admin(user, w.facility))
        self.assertTrue(hospital_wide_admin(user))
        # the top-level membership holds the "Administrator" system role, chosen by name
        (top,) = OrganizationUser.objects.filter(user=user)
        self.assertIsNone(top.organization.parent_id)
        self.assertEqual(top.role.name, "Administrator")
        place = FacilityOrganizationUser.objects.get(user=user)
        self.assertEqual(place.organization, w.root)
        self.assertEqual(place.role.name, "Facility Admin")
        # the web app's flag and the audit route agree
        client = self.api(user)
        self.assertTrue(client.get("/api/ihr/my-access/").json()["hospital_admin"])
        state.end()
        self.assertEqual(client.get("/api/ihr/audit/status/").status_code, 200)
        state.end()
        # our roles are not given to her: never restricted
        self.assertFalse(state.restricted(user))

    def test_a_missing_admin_role_is_a_clear_error(self):
        w = self.world
        user = self.fresh("seed-admin-norole")
        with (
            patch.object(seeding, "ADMIN_ROLE_NAME", "No Such Role"),
            self.assertRaisesMessage(RuntimeError, "No Such Role"),
        ):
            make_hospital_admin(user, w.facility)
        self.assertFalse(OrganizationUser.objects.filter(user=user).exists())

    def test_runs_again_without_adding_anything(self):
        w = self.world
        user = self.fresh("seed-admin-again")
        make_hospital_admin(user, w.facility)
        counts = (
            OrganizationUser.objects.filter(user=user).count(),
            FacilityOrganizationUser.objects.filter(user=user).count(),
        )
        self.assertFalse(make_hospital_admin(user, w.facility))
        self.assertEqual(
            counts,
            (
                OrganizationUser.objects.filter(user=user).count(),
                FacilityOrganizationUser.objects.filter(user=user).count(),
            ),
        )

    def test_keeps_the_fixtures_own_top_level_role(self):
        """CARE's fixtures give care-admin an admin role on a top-level organisation already:
        that one is kept and no second one is added."""
        w = self.world
        user = self.fresh("seed-admin-fixture")
        top = Organization.objects.create(name="Seed Administrator", org_type="role")
        OrganizationUser.objects.create(
            organization=top, user=user, role=w.role("Facility Admin")
        )
        self.assertTrue(
            make_hospital_admin(user, w.facility)
        )  # the facility place only
        self.assertEqual(
            list(
                OrganizationUser.objects.filter(user=user).values_list(
                    "organization", flat=True
                )
            ),
            [top.pk],
        )


class SeedFacilityAdmin(SwapCase):
    """The facility-only admin login (P1-T24b, Q-K118 A): ``admin.anil`` signs in to alpha as
    ``care-facility-admin``, "Facility Admin" at the demo facility's root and nothing above it.
    The staff card's reset refusal (attack A1) is clicked with this login."""

    def test_the_login_and_the_care_user(self):
        self.assertEqual(FACILITY_ADMIN, "care-facility-admin")
        self.assertIn(FACILITY_ADMIN, ihr_seed_demo.SEED_USERS)
        (anil,) = [
            row for row in ihr_keycloak_demo_users.DEMO_STAFF if row[0] == "admin.anil"
        ]
        self.assertEqual(anil[3], {"alpha": FACILITY_ADMIN})
        self.assertTrue(anil[4])

    def test_adds_staff_but_is_not_hospital_wide_and_never_resets(self):
        w = self.world
        self.assertTrue(make_facility_admin(w.facility))
        user = get_user_model().objects.get(username=FACILITY_ADMIN)
        place = FacilityOrganizationUser.objects.get(user=user)
        self.assertEqual(
            (place.organization, place.role.name), (w.root, "Facility Admin")
        )
        self.assertFalse(OrganizationUser.objects.filter(user=user).exists())
        self.assertTrue(AuthorizationController.call("can_create_user", user))
        self.assertFalse(hospital_wide_admin(user))
        client = self.api(user)
        self.assertFalse(client.get("/api/ihr/my-access/").json()["hospital_admin"])
        state.end()
        doctor = self.user("Doctor")
        page = client.get(f"/api/ihr/staff/{doctor.username}/access/")
        self.assertEqual(page.status_code, 200, page.content[:300])
        self.assertNotIn("reset-sign-in", page.json()["offers"])
        state.end()
        refused = client.post(f"/api/ihr/staff/{doctor.username}/reset-sign-in/")
        self.assertEqual(refused.status_code, 403, refused.content[:300])
        state.end()
        self.assertFalse(make_facility_admin(w.facility))  # safe to run again

    def test_refuses_to_place_someone_hospital_wide(self):
        """The seed never turns a hospital-wide admin into the facility-only login."""
        w = self.world
        make_facility_admin(w.facility)
        user = get_user_model().objects.get(username=FACILITY_ADMIN)
        make_hospital_admin(user, w.facility)
        with self.assertRaisesMessage(RuntimeError, "hospital-wide"):
            make_facility_admin(w.facility)
