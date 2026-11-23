"""The hospital switcher's endpoint, GET /api/ihr/my-hospitals/ (P1-T31c; docs/reviews/p1-t31/
design.md §2 Q-K146, Q-K147, Q-K149, §3.4 and §5 tests 7-10, as changed by attack.md
"Orchestrator decision").

The other hospitals here are registry rows only (no schema): the endpoint reads only ``public``
(the directory, ``Hospital``, ``Domain``, ``HospitalSchemaState``), never another hospital's
schema. Their directory rows are written as the owner, as the trigger would (test_directory
proves the trigger writes them).

- ``Answer``: test 7 (who sees what, the order, the url, the key set, every refused credential).
- ``Statuses``: test 8 (which hospitals are listed, and when one is unavailable).
- ``CrossSite``: test 9.
- ``NoSideEffects``: test 10 (no audit entry, no Redis key, a constant query count).
- ``ServableMany`` and ``OriginFor``: ``release.servable_many`` and ``links.origin_for``.
"""

import secrets
import time
import uuid
from types import SimpleNamespace

from care.users.models import User
from config.patient_otp_token import PatientToken
from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_tenants.utils import schema_context
from ihr_audit.tests.test_audit_trail import entries_since, mark
from ihr_platform import links, release
from ihr_platform.models import (
    Domain,
    Hospital,
    HospitalSchemaState,
    RetiredAddress,
    StaffDirectoryEntry,
)
from ihr_platform.test_runner import TEST_SCHEMA, WRITTEN_REDIS_KEYS
from ihr_platform.testing import as_owner
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from ihr_identity import directory, testing
from ihr_identity.models import StaffIdentity

URL = "/api/ihr/my-hospitals/"
LOGIN = "/api/ihr/auth/login/"
HOST = "testserver"
ENTRY_KEYS = {"name", "url", "current", "available"}
CROSS_SITE = "Cross-site request refused."
NOT_SIGNED_IN = "Authentication credentials were not provided."


def here():
    with schema_context("public"):
        return testing.ensure_org_id(Hospital.objects.get(schema_name=TEST_SCHEMA))


def first_primary(hospital):
    with schema_context("public"):
        return (
            Domain.objects.filter(tenant=hospital, is_primary=True)
            .order_by("id")
            .values_list("domain", flat=True)
            .first()
        )


def statements(captured):
    """The queries run, without django-tenants' ``SET search_path`` (one per cursor)."""
    return [
        q["sql"]
        for q in captured.captured_queries
        if not q["sql"].startswith("SET search_path")
    ]


def login_url(domain):
    return settings.IHR_FRONTEND_URL_TEMPLATE.format(domain=domain) + LOGIN


class SwitcherCase(SimpleTestCase):
    """A signed-in person at the test hospital, and registry-only other hospitals."""

    databases = {"default"}

    def setUp(self):
        connection.set_schema(TEST_SCHEMA)
        self.hospital = here()
        self.tag = "sw" + secrets.token_hex(
            3
        )  # slugs are never reused: one set per test
        self.made = []
        self.user = self.person()
        self.keycloak = testing.fake_keycloak()
        self.keycloak.__enter__()
        self.client = testing.staff_client(self.hospital, HOST, self.user)
        self.sub = StaffIdentity.objects.get(user=self.user).keycloak_sub

    def tearDown(self):
        self.keycloak.__exit__(None, None, None)
        connection.set_schema(TEST_SCHEMA)
        made = User._base_manager.filter(username__startswith=self.tag)
        StaffIdentity.objects.filter(user__in=made).delete()
        made.delete()
        with schema_context("public"), as_owner():
            StaffDirectoryEntry.objects.filter(hospital__in=self.made).delete()
            Domain.objects.filter(tenant__in=self.made).delete()
            HospitalSchemaState.objects.filter(hospital__in=self.made).delete()
            for hospital in self.made:
                Hospital.objects.filter(pk=hospital.pk).delete()
            RetiredAddress.objects.filter(value__contains=self.tag).delete()
        connection.set_schema(TEST_SCHEMA)
        super().tearDown()

    def person(self, **fields):
        name = f"{self.tag}-{time.time_ns()}"
        return User.objects.create_user(username=name, password="x-Unused-1", **fields)

    def other(
        self,
        name,
        *,
        status=Hospital.Status.ACTIVE,
        org=True,
        domain="primary",
        state="current",
        sub=None,
    ):
        """A hospital in the registry only, listed for ``sub`` (the signed-in person's by
        default). ``domain``: primary, secondary (not primary) or None; ``state``: current,
        release (held for a release), restore (held for a restore), stale (another build's
        schema) or None (no state row)."""
        connection.set_schema_to_public()
        try:
            with as_owner():
                hospital = Hospital(
                    slug=f"{self.tag}-{len(self.made)}",
                    name=name,
                    status=status,
                    keycloak_org_id=uuid.uuid4() if org else None,
                )
                hospital.auto_create_schema = False
                hospital.save()
                self.made.append(hospital)
                if domain is not None:
                    hospital.address = f"{secrets.token_hex(4)}-{self.tag}.switch.test"
                    Domain.objects.create(domain=hospital.address, tenant=hospital)
                    if (
                        domain != "primary"
                    ):  # (django-tenants makes a tenant's first one
                        # primary on save)
                        Domain.objects.filter(domain=hospital.address).update(
                            is_primary=False
                        )
                if state is not None:
                    HospitalSchemaState.objects.create(
                        hospital=hospital,
                        schema_fingerprint=""
                        if state == "stale"
                        else release.code_fingerprint(),
                        upgrading_since=timezone.now() if state == "release" else None,
                        hold_reason="window" if state == "release" else "",
                        restore_operation=uuid.uuid4() if state == "restore" else None,
                        restore_since=timezone.now() if state == "restore" else None,
                    )
                StaffDirectoryEntry.objects.create(
                    keycloak_sub=sub or self.sub, hospital=hospital
                )
        finally:
            connection.set_schema(TEST_SCHEMA)
        return hospital

    def answer(self, client=None, **headers):
        response = (client or self.client).get(URL, **headers)
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response.json()["hospitals"]

    def names(self, client=None, **headers):
        return [h["name"] for h in self.answer(client, **headers)]


class Answer(SwitcherCase):
    """Test 7."""

    def test_a_person_at_two_hospitals_sees_both_the_current_first(self):
        beta = self.other("Beta Care Hospital")
        self.other("Someone Else's Hospital", sub=uuid.uuid4())  # another person's row
        self.assertEqual(
            self.answer(),
            [
                {
                    "name": self.hospital.name,
                    "url": login_url(first_primary(self.hospital)),
                    "current": True,
                    "available": True,
                },
                {
                    "name": "Beta Care Hospital",
                    "url": login_url(beta.address),
                    "current": False,
                    "available": True,
                },
            ],
        )

    def test_the_others_follow_by_name_whatever_its_case(self):
        self.other("Zeta Hospital")
        self.other("alpha clinic")
        self.other("Mid Town Hospital")
        self.assertEqual(
            self.names(),
            [self.hospital.name, "alpha clinic", "Mid Town Hospital", "Zeta Hospital"],
        )

    def test_a_person_at_one_hospital_sees_only_it(self):
        self.assertEqual(
            self.answer(),
            [
                {
                    "name": self.hospital.name,
                    "url": login_url(first_primary(self.hospital)),
                    "current": True,
                    "available": True,
                }
            ],
        )

    def test_the_current_hospital_is_listed_even_without_its_row(self):
        """Self-healing (Q-K146): the row is the trigger's; the current one comes from the
        request's own hospital."""
        with as_owner():
            StaffDirectoryEntry.objects.filter(keycloak_sub=self.sub).delete()
        self.other("Beta Care Hospital")
        self.assertEqual(self.names(), [self.hospital.name, "Beta Care Hospital"])

    def test_query_parameters_are_ignored(self):
        beta = self.other("Beta Care Hospital")
        someone = uuid.uuid4()
        self.other("Someone Else's Hospital", sub=someone)
        plain = self.answer()
        for params in (
            f"?sub={someone}",
            f"?keycloak_sub={someone}&hospital={beta.pk}",
            "?all=1&limit=0&format=json",
            f"?slug={beta.slug}&schema={TEST_SCHEMA}",
        ):
            with self.subTest(params):
                response = self.client.get(URL + params)
                self.assertEqual(response.status_code, 200, response.content[:300])
                self.assertEqual(response.json()["hospitals"], plain)

    def test_the_answer_holds_names_and_addresses_only(self):
        beta = self.other("Beta Care Hospital")
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        body = response.json()
        self.assertEqual(set(body), {"hospitals"})
        for entry in body["hospitals"]:
            self.assertEqual(set(entry), ENTRY_KEYS)
        text = response.content.decode()
        for secret in (
            str(self.sub),
            self.sub.hex,
            TEST_SCHEMA,
            beta.schema_name,
            beta.slug,
            str(beta.keycloak_org_id),
            str(beta.uuid),
            self.user.username,
            "active",
        ):
            with self.subTest(secret):
                self.assertNotIn(secret, text)

    def test_only_get_is_served(self):
        self.assertEqual(self.client.head(URL).status_code, 200)
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method):
                response = getattr(self.client, method)(URL, {}, format="json")
                self.assertEqual(response.status_code, 405)

    def test_every_other_credential_is_refused_with_403(self):
        """Staff session cookie only (attack S6, D14): 403 everywhere, never a list."""
        from rest_framework_simplejwt.tokens import RefreshToken

        self.other("Beta Care Hospital")
        refused = {}
        refused["anonymous"] = APIClient(HTTP_HOST=HOST).get(URL)
        refused["cross-site"] = self.client.get(URL, HTTP_SEC_FETCH_SITE="cross-site")

        jwt = APIClient(HTTP_HOST=HOST)
        jwt.credentials(
            HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(self.user).access_token}"
        )
        refused["CARE JWT"] = jwt.get(URL)

        patient = PatientToken()
        patient["phone_number"] = "+919800000001"
        otp = APIClient(HTTP_HOST=HOST)
        otp.credentials(HTTP_AUTHORIZATION=f"Bearer {patient}")
        refused["patient OTP token"] = otp.get(URL)

        # A machine account gets nothing, even one linked to a Keycloak person (so only the
        # view's own classes refuse it: attack S6, mutation directory-service-token)
        machine = self.person()
        testing.link(machine)
        User._base_manager.filter(pk=machine.pk).update(is_service_account=True)
        token = Token.objects.create(user=machine)
        service = APIClient(HTTP_HOST=HOST)
        service.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        self.assertEqual(  # positive control: the token is good elsewhere
            service.get("/api/v1/users/getcurrentuser/").status_code, 200
        )
        refused["service-account token"] = service.get(URL)
        token.delete()

        unlinked = APIClient(HTTP_HOST=HOST)
        unlinked.force_authenticate(self.person())
        refused["user with no identity"] = unlinked.get(URL)

        flagged = self.person()
        flagged_client = testing.staff_client(self.hospital, HOST, flagged, sid="sid-f")
        User._base_manager.filter(pk=flagged.pk).update(is_service_account=True)
        refused["signed-in user flagged as a machine account"] = flagged_client.get(URL)

        for credential, response in refused.items():
            with self.subTest(credential):
                self.assertEqual(response.status_code, 403, response.content[:300])
                self.assertNotIn(b"hospitals", response.content)
        self.assertEqual(refused["anonymous"].json()["detail"], NOT_SIGNED_IN)
        self.assertEqual(refused["cross-site"].json()["detail"], CROSS_SITE)

    def test_the_view_is_staff_only_by_its_own_classes(self):
        from ihr_identity.authentication import StaffSessionAuthentication

        view = directory.MyHospitalsView
        self.assertEqual(view.authentication_classes, [StaffSessionAuthentication])
        self.assertEqual(view.permission_classes[0].__name__, "NotCrossSite")


class Statuses(SwitcherCase):
    """Test 8 (Q-K146 A): only ACTIVE hospitals with a Keycloak organisation and a primary
    address, never ``public``; a held or other-build one is listed as unavailable."""

    def test_hospitals_that_cannot_be_switched_to_are_not_listed(self):
        for status in (
            Hospital.Status.SUSPENDED,
            Hospital.Status.OFFBOARDING,
            Hospital.Status.ARCHIVED,
            Hospital.Status.PROVISIONING,
        ):
            self.other(f"Not Listed {status}", status=status)
        self.other("Not Listed no organisation", org=False)
        self.other("Not Listed secondary address only", domain="secondary")
        self.other("Not Listed no address", domain=None)
        self.assertEqual(self.names(), [self.hospital.name])

    def test_the_platform_tenant_is_never_listed(self):
        """Attack S8: ``ihr_create_tenant --public`` makes an ACTIVE Hospital(public)."""
        connection.set_schema_to_public()
        try:
            with as_owner():
                public = Hospital.objects.filter(schema_name="public").first()
                if public is None:
                    public = Hospital(
                        schema_name="public",
                        slug="public",
                        name="IndieHR platform",
                        status=Hospital.Status.ACTIVE,
                    )
                    public.auto_create_schema = False
                    public.save()
                    self.made.append(public)
                Hospital.objects.filter(
                    pk=public.pk, keycloak_org_id__isnull=True
                ).update(keycloak_org_id=uuid.uuid4())
                address = f"pub-{self.tag}.switch.test"
                Domain.objects.create(domain=address, tenant=public, is_primary=True)
                self.addCleanup(self._drop_domain, address)
                StaffDirectoryEntry.objects.create(
                    keycloak_sub=self.sub, hospital=public
                )
                self.addCleanup(self._drop_row, public)
        finally:
            connection.set_schema(TEST_SCHEMA)
        self.assertEqual(self.names(), [self.hospital.name])

    def _drop_domain(self, address):
        with schema_context("public"), as_owner():
            Domain.objects.filter(domain=address).delete()
            RetiredAddress.objects.filter(value=address).delete()

    def _drop_row(self, hospital):
        with schema_context("public"), as_owner():
            StaffDirectoryEntry.objects.filter(
                keycloak_sub=self.sub, hospital=hospital
            ).delete()

    def test_a_held_or_other_build_hospital_is_listed_as_unavailable(self):
        self.other("Held For A Release", state="release")
        self.other("Held For A Restore", state="restore")
        self.other("On Another Build", state="stale")
        self.other("Never Checked", state=None)
        self.other("Open Hospital")
        self.assertEqual(
            [(h["name"], h["available"]) for h in self.answer()],
            [
                (self.hospital.name, True),
                ("Held For A Release", False),
                ("Held For A Restore", False),
                ("Never Checked", False),
                ("On Another Build", False),
                ("Open Hospital", True),
            ],
        )


class CrossSite(SwitcherCase):
    """Test 9: ``NotCrossSite`` first (Q-K147)."""

    REFUSED = ("cross-site", "same-site")
    ALLOWED = ("same-origin", "none", None)

    def ask(self, site, client=None):
        headers = {} if site is None else {"HTTP_SEC_FETCH_SITE": site}
        return (client or self.client).get(URL, **headers)

    def test_cross_site_and_same_site_are_refused(self):
        for site in self.REFUSED:
            with self.subTest(site=site):
                response = self.ask(site)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["detail"], CROSS_SITE)

    def test_own_pages_typed_addresses_and_older_browsers_are_served(self):
        for site in self.ALLOWED:
            with self.subTest(site=site):
                self.assertEqual(self.ask(site).status_code, 200)

    def test_the_guard_comes_first(self):
        """Refused before anything else is decided: 403 signed in or not, and nothing about
        the person (DRF names a missing sign-in first, as on the audit routes)."""
        from ihr_identity.staff_access import NotCrossSite

        self.assertIs(directory.MyHospitalsView.permission_classes[0], NotCrossSite)
        self.assertEqual(self.ask("cross-site").json()["detail"], CROSS_SITE)
        anonymous = self.ask("cross-site", APIClient(HTTP_HOST=HOST))
        self.assertEqual(anonymous.status_code, 403)
        self.assertEqual(anonymous.json()["detail"], NOT_SIGNED_IN)


class NoSideEffects(SwitcherCase):
    """Test 10 (Q-K147, Q-K149)."""

    def test_reading_it_writes_no_audit_entry(self):
        self.other("Beta Care Hospital")
        self.client.get(
            "/api/ihr/auth/session/"
        )  # the sign-in's own writes (last login)
        since = mark()
        self.assertEqual(len(self.answer()), 2)
        self.assertEqual(entries_since(since), [])

    def test_reading_it_writes_no_redis_key(self):
        self.other("Beta Care Hospital")
        self.client.get("/api/ihr/auth/session/")  # the session's own keys
        before = set(WRITTEN_REDIS_KEYS)
        self.assertEqual(len(self.answer()), 2)
        self.assertEqual(set(WRITTEN_REDIS_KEYS) - before, set())

    def queries(self):
        user = User.objects.select_related("ihr_identity").get(pk=self.user.pk)
        request = SimpleNamespace(user=user)
        release.code_fingerprint()  # this build's, worked out once per process
        connection.set_tenant(self.hospital)  # as the tenant middleware does
        try:
            with CaptureQueriesContext(connection) as captured:
                found = directory.hospitals_for(request)
        finally:
            connection.set_schema(TEST_SCHEMA)
        self.sql = statements(captured)
        return len(self.sql), len(found)

    def test_the_query_count_does_not_grow_with_the_hospitals(self):
        """Attack D3: one hospital query (directory, addresses) and one state query."""
        self.assertEqual(self.queries(), (2, 1), self.sql)
        self.other("Beta Care Hospital")
        self.other("Gamma Hospital", state="release")
        self.assertEqual(self.queries(), (2, 3), self.sql)


class ServableMany(SwitcherCase):
    """``release.servable_many``: ``servable()``'s rule for many hospitals at once."""

    def test_it_agrees_with_servable_for_each_hospital(self):
        hospitals = [
            self.hospital,
            self.other("Open"),
            self.other("Release", state="release"),
            self.other("Restore", state="restore"),
            self.other("Stale", state="stale"),
            self.other("Unchecked", state=None),
            self.other("Suspended", status=Hospital.Status.SUSPENDED),
        ]
        with schema_context("public"):
            hospitals = list(Hospital.objects.filter(pk__in=[h.pk for h in hospitals]))
            answer = release.servable_many(hospitals)
            expected = {h.pk: release.servable(h) for h in hospitals}
        self.assertEqual(answer, expected)
        self.assertEqual(sum(answer.values()), 2)  # the test hospital and "Open"

    def test_one_query_whatever_the_number(self):
        first = self.other("One")
        release.code_fingerprint()
        with CaptureQueriesContext(connection) as one:
            release.servable_many([first])
        many = [first, *(self.other(f"Many {n}") for n in range(4))]
        with CaptureQueriesContext(connection) as five:
            answer = release.servable_many(many)
        self.assertEqual(
            (len(statements(one)), len(statements(five))), (1, 1), statements(five)
        )
        self.assertTrue(all(answer.values()))

    def test_a_gated_schema_is_never_served(self):
        open_one = self.other("Open")
        from ihr_platform import db_roles

        with override_gated(db_roles, {open_one.schema_name}):
            self.assertEqual(release.servable_many([open_one]), {open_one.pk: False})
        self.assertEqual(release.servable_many([open_one]), {open_one.pk: True})


def override_gated(db_roles, schemas):
    from unittest import mock

    return mock.patch.object(db_roles, "gated_schemas", lambda: frozenset(schemas))


class OriginFor(SimpleTestCase):
    """``links.origin_for``: the one place a hospital address becomes a browser origin
    (attack Z6); ``frontend_base_url`` and ``hospital_origins`` use it."""

    databases = {"default"}

    def test_the_template_without_a_trailing_slash(self):
        with override_settings(IHR_FRONTEND_URL_TEMPLATE="https://{domain}"):
            self.assertEqual(
                links.origin_for("a.example.test"), "https://a.example.test"
            )
        with override_settings(IHR_FRONTEND_URL_TEMPLATE="http://{domain}:8080/"):
            self.assertEqual(links.origin_for("a.localhost"), "http://a.localhost:8080")

    def test_the_hospitals_own_links_use_it(self):
        connection.set_schema(TEST_SCHEMA)
        domain = first_primary(here())
        self.assertEqual(links.frontend_base_url(), links.origin_for(domain))
        self.assertIn(links.origin_for(domain), links.hospital_origins())
