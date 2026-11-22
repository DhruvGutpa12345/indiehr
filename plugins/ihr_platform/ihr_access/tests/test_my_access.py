"""The web app's mode for the caller: GET /api/ihr/my-access/ (P1-T11c1; design-c.md §13.2).

Parity with the backend's own rule: ``restricted`` is ``state.restricted`` for every login of
the role world, for a Receptionist who also holds a hospital's own role with
``can_list_patients`` only (not clinical: still restricted), and for a Doctor in one facility
who is a Receptionist in another (permissions add up: not restricted). ``bills_here`` is true
only for the Cashier and the TPA Desk, and only for a facility where they hold that role
(Q-K37a B). ``hospital_admin`` is the audit trail's own gate (``IsHospitalAdmin``, P1-T24a,
Q-K111): since P1-T25 (Q-K76 B) an admin role on the hospital's root (``ihr_identity.roots``)
only, never another top-level organisation, a facility's or a district's. The state where
provisioning put the first admin before P1-T25 no longer counts (Q-K120 superseded); provisioning
now places that admin on the root.
A superuser is never in our modes (``hospital_admin`` false too); a malformed facility is 400;
anonymous callers are refused; no patient data in the body. The query count is pinned.
"""

import uuid

from care.emr.models.organization import (
    FacilityOrganizationUser,
    Organization,
    OrganizationUser,
)
from care.facility.models import Facility
from care.security.models import PermissionModel, RoleModel, RolePermission
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from ihr_identity import roots
from ihr_identity import testing as identity
from ihr_identity.staff_access import IsHospitalAdmin
from ihr_roles import registry
from model_bakery import baker
from rest_framework.test import APIClient

from ihr_access import state
from ihr_access.tests.base import HOST, SwapCase

URL = "/api/ihr/my-access/"
KEYS = {"restricted", "bills_here", "hospital_admin"}
# SQL statements one GET makes as a non-superuser, the request's whole path under
# force_authenticate, not counting django-tenants' "SET search_path" before each (P1-T24a,
# design.md "T24a as decided"): before T24a 12 with a facility and 10 without; hospital_admin
# adds one EXISTS, and since P1-T25 one SELECT of the root before it (``roots.valid_root``, never
# cached across requests: review S10). A superuser skips both.
QUERIES_WITH_FACILITY = 14
QUERIES_WITHOUT_FACILITY = 12


class MyAccess(SwapCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        w = cls.world
        cls.other = baker.make(Facility, created_by=w.admin, name="Other Facility")
        other_root = cls.other.default_internal_organization
        lister = baker.make(
            RoleModel, name="Front desk lister (hospital's own)", is_system=False
        )
        RolePermission.objects.create(
            role=lister,
            permission=PermissionModel.objects.get(slug="can_list_patients"),
        )
        cls.rec_lister = w.user("acc-rec-lister", ("Receptionist", w.root))
        FacilityOrganizationUser.objects.create(
            organization=w.root, user=cls.rec_lister, role=lister
        )
        cls.doctor_rec = w.user(
            "acc-doctor-a-rec-b",
            ("Doctor", w.departments["Paediatrics"]),
            ("Receptionist", other_root),
        )
        cls.cashier_b = w.user("acc-cashier-b", ("Cashier", other_root))
        cls.tpa_dept = w.user("acc-tpa-dept", ("TPA Desk", w.departments["Stores"]))
        cls.super_cashier = get_user_model().objects.create_superuser(
            username="acc-super-cashier", email="sc@example.test", password="x-Unused-1"
        )
        FacilityOrganizationUser.objects.create(
            organization=w.root, user=cls.super_cashier, role=w.role("Cashier")
        )
        # Hospital-wide admin (P1-T25): an admin role (it holds can_create_user) on the
        # hospital's root. The others hold the same role where it does not count, an unmarked
        # top-level organisation included (CARE's fixture role organisations, Q-K118).
        root, _ = roots.ensure_root("test")
        top = Organization.objects.create(name="Acc Hospital Admins", org_type="role")
        district = Organization.objects.create(
            name="Acc District", org_type="govt", parent=top
        )
        closed = Organization.objects.create(
            name="Acc Closed Admins", org_type="role", active=False
        )
        # A state, as ihr_load_geography makes it (a top-level government organisation), where
        # provisioning made each hospital's first admin an Administrator before P1-T25.
        state_org = Organization.objects.create(name="Acc State", org_type="govt")
        admin_role = w.role("Facility Admin")
        cls.hospital_admin = w.user("acc-hospital-admin")
        cls.district_admin = w.user("acc-district-admin")
        cls.closed_admin = w.user("acc-closed-admin")
        cls.state_admin = w.user("acc-state-admin")
        cls.top_admin = w.user("acc-top-admin")
        cls.provisioned_admin = w.user("acc-root-administrator")
        cls.facility_admin = w.user("acc-facility-admin", ("Facility Admin", w.root))
        cls.super_admin = get_user_model().objects.create_superuser(
            username="acc-super-admin", email="sa@example.test", password="x-Unused-1"
        )
        for user, organization in (
            (cls.hospital_admin, root),
            (cls.top_admin, top),
            (cls.district_admin, district),
            (cls.closed_admin, closed),
            (cls.super_admin, root),
        ):
            OrganizationUser.objects.create(
                organization=organization, user=user, role=admin_role
            )
        administrator = RoleModel.objects.get(name="Administrator", is_system=True)
        OrganizationUser.objects.create(
            organization=state_org, user=cls.state_admin, role=administrator
        )
        # as provisioning makes the first admin now (steps.first_admin, P1-T25)
        OrganizationUser.objects.create(
            organization=root, user=cls.provisioned_admin, role=administrator
        )

    def ask(self, who, facility=None, client=None):
        client = client or self.api(who)
        params = {} if facility is None else {"facility": str(facility)}
        response = client.get(URL, params)
        state.end()
        return response

    def access(self, who, facility=None):
        response = self.ask(who, facility)
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        self.assertEqual(set(body), KEYS)
        self.assertTrue(all(isinstance(v, bool) for v in body.values()), body)
        return body

    def logins(self):
        """Every login of the role world, plus the three mixed people."""
        people = dict(self.world.users)
        people["Receptionist + can_list_patients role"] = self.rec_lister
        people["Doctor in A, Receptionist in B"] = self.doctor_rec
        people["Cashier in B"] = self.cashier_b
        people["TPA Desk in a department"] = self.tpa_dept
        people["Hospital-wide admin"] = self.hospital_admin
        people["Admin of a district"] = self.district_admin
        people["Admin of an inactive organisation"] = self.closed_admin
        people["Administrator of the state (pre-P1-T25 first admin)"] = self.state_admin
        people["Admin of another top-level organisation"] = self.top_admin
        people["Provisioned first admin (Administrator of the root)"] = (
            self.provisioned_admin
        )
        people["Facility admin only"] = self.facility_admin
        return people

    def test_restricted_is_the_backends_rule(self):
        facility = self.world.facility.external_id
        for label, user in self.logins().items():
            with self.subTest(login=label):
                expected = state.restricted(user)
                self.assertEqual(self.access(user, facility)["restricted"], expected)
                self.assertEqual(self.access(user)["restricted"], expected)
                self.assertEqual(
                    self.access(user, self.other.external_id)["restricted"], expected
                )
        # the rule itself, spelled out (Q-K49 A'): our restricted roles, no clinical slug
        for role, user in self.world.users.items():
            with self.subTest(role=role):
                self.assertEqual(state.restricted(user), role in registry.RESTRICTED)
        self.assertTrue(
            state.restricted(self.rec_lister)
        )  # can_list_patients is not clinical
        self.assertFalse(state.restricted(self.doctor_rec))  # Doctor anywhere adds up

    def test_bills_here_only_where_they_bill(self):
        w = self.world
        for role, user in w.users.items():
            with self.subTest(role=role):
                self.assertEqual(
                    self.access(user, w.facility.external_id)["bills_here"],
                    role in registry.BED_FOR_BILLING,
                )
                self.assertFalse(
                    self.access(user, self.other.external_id)["bills_here"]
                )
                self.assertFalse(self.access(user)["bills_here"])
        self.assertTrue(
            self.access(self.cashier_b, self.other.external_id)["bills_here"]
        )
        self.assertFalse(
            self.access(self.cashier_b, w.facility.external_id)["bills_here"]
        )
        self.assertTrue(
            self.access(self.tpa_dept, w.facility.external_id)["bills_here"]
        )
        self.assertFalse(
            self.access(self.doctor_rec, self.other.external_id)["bills_here"]
        )

    def test_an_unknown_facility_is_not_told_apart(self):
        cashier = self.world.users["Cashier"]
        unknown = uuid.uuid4()
        self.assertEqual(
            self.access(cashier, unknown),
            {"restricted": True, "bills_here": False, "hospital_admin": False},
        )
        self.assertEqual(
            self.access(self.world.users["Doctor"], unknown)["bills_here"], False
        )

    def test_superuser_is_never_in_our_modes(self):
        for user in (self.world.admin, self.super_cashier, self.super_admin):
            with self.subTest(user=user.username):
                self.assertEqual(
                    self.access(user, self.world.facility.external_id),
                    {"restricted": False, "bills_here": False, "hospital_admin": False},
                )

    def test_hospital_admin_is_the_audit_trails_gate(self):
        """Same rule as IsHospitalAdmin, for every login of the world and the admin cases;
        our roles and CARE's facility admin are never hospital-wide."""
        gate = IsHospitalAdmin()
        facility = self.world.facility.external_id
        for label, user in self.logins().items():
            with self.subTest(login=label):
                request = type("R", (), {"user": user})()
                expected = gate.has_permission(request, None)
                self.assertEqual(self.access(user)["hospital_admin"], expected)
                self.assertEqual(
                    self.access(user, facility)["hospital_admin"], expected
                )
        self.assertTrue(self.access(self.hospital_admin)["hospital_admin"])
        for user in (
            self.district_admin,
            self.closed_admin,
            self.facility_admin,
            self.top_admin,
            self.state_admin,
        ):
            with self.subTest(refused=user.username):
                self.assertFalse(self.access(user)["hospital_admin"])
        for role, user in self.world.users.items():
            if role in registry.PERMANENT_NAMES:
                with self.subTest(our_role=role):
                    self.assertFalse(self.access(user)["hospital_admin"])

    def test_hospital_admin_agrees_with_the_audit_route(self):
        """The flag never shows a menu item the backend refuses, nor hides one it allows."""
        for user in (
            self.hospital_admin,
            self.facility_admin,
            self.district_admin,
            self.top_admin,
            self.state_admin,
        ):
            with self.subTest(user=user.username):
                flag = self.access(user)["hospital_admin"]
                status = self.api(user).get("/api/ihr/audit/status/").status_code
                state.end()
                self.assertEqual(status, 200 if flag else 403)

    def test_the_provisioned_first_admin_is_a_hospital_admin(self):
        """Provisioning (``steps.first_admin``, P1-T25) makes a hospital's first admin an
        Administrator of its root: shown the audit trail and served it."""
        self.assertTrue(self.access(self.provisioned_admin)["hospital_admin"])
        status = (
            self.api(self.provisioned_admin).get("/api/ihr/audit/status/").status_code
        )
        state.end()
        self.assertEqual(status, 200)

    def test_a_state_only_administrator_is_not_a_hospital_admin(self):
        """Q-K76 B (P1-T25; Q-K120 superseded): an Administrator of a state organisation, where
        provisioning put the first admin before P1-T25, is no longer hospital-wide: anyone
        given an admin role on any of the 36 states would otherwise be."""
        self.assertFalse(self.access(self.state_admin)["hospital_admin"])
        status = self.api(self.state_admin).get("/api/ihr/audit/status/").status_code
        state.end()
        self.assertEqual(status, 403)

    def test_nobody_is_a_hospital_admin_without_a_valid_root(self):
        """Fail closed: the root unmarked (or a second one marked) closes the screens for
        everyone, the root's own admins included."""
        root = roots.valid_root()
        Organization.objects.filter(pk=root.pk).update(metadata={})
        self.assertFalse(self.access(self.hospital_admin)["hospital_admin"])
        Organization.objects.filter(pk=root.pk).update(metadata=dict(root.metadata))
        self.assertTrue(self.access(self.hospital_admin)["hospital_admin"])
        # system-made, as only owner code can make one (review A1: a marked organisation
        # from CARE's API is ignored)
        Organization.objects.create(
            name="Second root",
            org_type="role",
            system_generated=True,
            metadata=dict(root.metadata),
        )
        self.assertFalse(self.access(self.hospital_admin)["hospital_admin"])

    def test_query_count_is_pinned(self):
        """Two statements more than before T24a (the root, then one EXISTS); a superuser makes
        none for them."""
        facility = self.world.facility.external_id
        for user in (self.world.users["Doctor"], self.hospital_admin):
            for value, expected in (
                (facility, QUERIES_WITH_FACILITY),
                (None, QUERIES_WITHOUT_FACILITY),
            ):
                client = self.api(user)
                with self.subTest(user=user.username, facility=value):
                    with CaptureQueriesContext(connection) as queries:
                        self.ask(user, value, client=client)
                    statements = [
                        q["sql"]
                        for q in queries.captured_queries
                        if not q["sql"].startswith("SET search_path")
                    ]
                    self.assertEqual(
                        len(statements),
                        expected,
                        "\n".join(sql[:160] for sql in statements),
                    )

    def test_malformed_facility_is_400(self):
        for who in ("Cashier", "Doctor", "Owner"):
            for value in ("not-an-id", "123", "' OR 1=1 --"):
                with self.subTest(who=who, value=value):
                    response = self.ask(who, value)
                    self.assertEqual(response.status_code, 400, response.content[:300])
                    self.assertEqual(set(response.json()), {"facility"})

    def test_anonymous_is_refused(self):
        anonymous = APIClient(HTTP_HOST=HOST)
        for facility in (None, self.world.facility.external_id):
            with self.subTest(facility=facility):
                response = self.ask(None, facility, client=anonymous)
                self.assertIn(response.status_code, (401, 403))
                self.assertNotIn(b"bills_here", response.content)

    def test_writes_are_not_allowed(self):
        client = self.api("Cashier")
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                self.assertEqual(getattr(client, method)(URL, {}).status_code, 405)
                state.end()

    def test_through_the_real_sign_in(self):
        """The staff session cookie, the middleware and the request's state (P1-T4a)."""
        w = self.world
        with identity.fake_keycloak():
            client = self.signed_in("TPA Desk")
            response = self.ask(None, w.facility.external_id, client=client)
            self.assertEqual(response.status_code, 200, response.content[:300])
            self.assertEqual(
                response.json(),
                {"restricted": True, "bills_here": True, "hospital_admin": False},
            )
            client = self.signed_in("Doctor")
            response = self.ask(None, w.facility.external_id, client=client)
            self.assertEqual(
                response.json(),
                {"restricted": False, "bills_here": False, "hospital_admin": False},
            )
