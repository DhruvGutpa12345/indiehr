"""The web app's mode for the caller: GET /api/ihr/my-access/ (P1-T11c1; design-c.md §13.2).

Parity with the backend's own rule: ``restricted`` is ``state.restricted`` for every login of
the role world, for a Receptionist who also holds a hospital's own role with
``can_list_patients`` only (not clinical: still restricted), and for a Doctor in one facility
who is a Receptionist in another (permissions add up: not restricted). ``bills_here`` is true
only for the Cashier and the TPA Desk, and only for a facility where they hold that role
(Q-K37a B). A superuser is never in our modes; a malformed facility is 400; anonymous callers
are refused; no patient data in the body.
"""

import uuid

from care.emr.models.organization import FacilityOrganizationUser
from care.facility.models import Facility
from care.security.models import PermissionModel, RoleModel, RolePermission
from django.contrib.auth import get_user_model
from ihr_identity import testing as identity
from ihr_roles import registry
from model_bakery import baker
from rest_framework.test import APIClient

from ihr_access import state
from ihr_access.tests.base import HOST, SwapCase

URL = "/api/ihr/my-access/"
KEYS = {"restricted", "bills_here"}


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
            self.access(cashier, unknown), {"restricted": True, "bills_here": False}
        )
        self.assertEqual(
            self.access(self.world.users["Doctor"], unknown)["bills_here"], False
        )

    def test_superuser_is_never_in_our_modes(self):
        for user in (self.world.admin, self.super_cashier):
            with self.subTest(user=user.username):
                self.assertEqual(
                    self.access(user, self.world.facility.external_id),
                    {"restricted": False, "bills_here": False},
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
            self.assertEqual(response.json(), {"restricted": True, "bills_here": True})
            client = self.signed_in("Doctor")
            response = self.ask(None, w.facility.external_id, client=client)
            self.assertEqual(
                response.json(), {"restricted": False, "bills_here": False}
            )
