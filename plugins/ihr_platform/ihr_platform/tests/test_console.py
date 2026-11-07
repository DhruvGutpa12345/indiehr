"""The operator console (P1-T5b): sign-in, who gets in, pages, queueing, the provisioner."""

import time
import uuid
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import jwt
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test import Client, SimpleTestCase
from django_tenants.utils import schema_context
from ihr_identity import oidc, testing
from ihr_identity.sessions import unseal

from ihr_platform.console import auth
from ihr_platform.management.commands import ihr_provision_worker as worker
from ihr_platform.models import Hospital, OperatorAccount, TenantOperation
from ihr_platform.test_runner import TEST_SCHEMA

HOST = "console.testserver"
OPS_ISSUER = "https://auth.test/realms/indiehr-ops"


def operator_token(sub, **overrides):
    now = int(time.time())
    claims = {
        "iss": OPS_ISSUER,
        "sub": str(sub),
        "aud": "ihr-console",
        "azp": "ihr-console",
        "typ": "Bearer",
        "iat": now,
        "exp": now + 300,
        "sid": "ops-sid",
        "acr": "mfa",
        "realm_access": {"roles": ["ihr-operator", "default-roles-indiehr-ops"]},
    } | overrides
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(
        claims, testing._KEY, algorithm="RS256", headers={"kid": testing.KID}
    )


def tokens(sub, nonce, **overrides):
    now = int(time.time())
    id_token = jwt.encode(
        {
            "iss": OPS_ISSUER,
            "sub": str(sub),
            "aud": "ihr-console",
            "azp": "ihr-console",
            "typ": "ID",
            "iat": now,
            "exp": now + 300,
            "sid": "ops-sid",
            "nonce": nonce,
            "auth_time": now,
        },
        testing._KEY,
        algorithm="RS256",
        headers={"kid": testing.KID},
    )
    return {
        "access_token": operator_token(sub, **overrides),
        "refresh_token": f"refresh.{sub}.{uuid.uuid4().hex}",
        "id_token": id_token,
    }


class ConsoleTests(SimpleTestCase):
    databases = {"default"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        connection.set_schema_to_public()
        call_command("ihr_create_tenant", public=True, domain=[HOST], verbosity=0)
        connection.set_schema(TEST_SCHEMA)

    def setUp(self):
        connection.set_schema_to_public()
        self.sub = uuid.uuid4()
        self.account = OperatorAccount.objects.create(
            keycloak_sub=self.sub, name="Test Operator", added_by="test"
        )
        self.keycloak = testing.fake_keycloak(
            refresh=lambda token: tokens(token.split(".")[1], "n")
        )
        self.calls = self.keycloak.__enter__()
        for target, value in (
            ("ihr_platform.console.auth.oidc.issuer", lambda realm=None: OPS_ISSUER),
        ):
            patch = mock.patch(target, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.browser = Client(HTTP_HOST=HOST)

    def tearDown(self):
        self.keycloak.__exit__(None, None, None)
        connection.set_schema_to_public()
        OperatorAccount.objects.filter(pk=self.account.pk).delete()
        TenantOperation.objects.filter(slug__startswith="cons-").delete()
        connection.set_schema(TEST_SCHEMA)

    def sign_in(self, sub=None, **overrides):
        start = self.browser.get("/ops/login/")
        params = parse_qs(urlsplit(start["Location"]).query)
        state = unseal(
            self.browser.cookies[settings.IHR_OPS_LOGIN_COOKIE].value,
            settings.SECRET_KEY.encode(),
            "ihr-ops-login",
        )
        with mock.patch.object(
            oidc,
            "exchange_code",
            return_value=tokens(sub or self.sub, state["nonce"], **overrides),
        ):
            return self.browser.get(
                "/ops/callback/", {"code": "c", "state": params["state"][0]}
            ), params

    # --- sign-in ------------------------------------------------------------------------

    def test_anonymous_visitors_are_sent_to_the_operator_realm(self):
        response = self.browser.get("/ops/")
        self.assertEqual(response["Location"], "/ops/login/")
        start = self.browser.get("/ops/login/")
        target = urlsplit(start["Location"])
        params = parse_qs(target.query)
        self.assertTrue(
            start["Location"].startswith(f"{OPS_ISSUER}/protocol/openid-connect/auth")
        )
        self.assertEqual(params["client_id"], ["ihr-console"])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(
            params["redirect_uri"], [f"{settings.IHR_CONSOLE_URL}/ops/callback/"]
        )

    def test_an_operator_signs_in_and_sees_the_hospitals(self):
        response, _ = self.sign_in()
        self.assertEqual(response["Location"], "/ops/")
        cookie = response.cookies[settings.IHR_OPS_SESSION_COOKIE]
        self.assertTrue(cookie["httponly"])
        page = self.browser.get("/ops/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "testhosp")
        self.assertContains(page, "Test Operator")
        self.assertIn("default-src 'none'", page["Content-Security-Policy"])
        self.assertEqual(page["X-Frame-Options"], "DENY")
        self.assertEqual(page["Cache-Control"], "no-store")
        # Not "no-referrer": browsers then send "Origin: null" and every form fails CSRF.
        self.assertEqual(page["Referrer-Policy"], "same-origin")

    def test_only_listed_operators_with_role_and_mfa_get_in(self):
        stranger, _ = self.sign_in(sub=uuid.uuid4())
        self.assertIn("not_operator", stranger["Location"])
        self.assertEqual(self.calls["logout"], 1)
        for overrides in (
            {
                "realm_access": {"roles": ["default-roles-indiehr-ops"]}
            },  # no operator role
            {"acr": "pwd"},  # no second factor
            {"azp": "ihr-h-testhosp", "aud": "ihr-h-testhosp"},  # a hospital's token
            {"iss": "https://auth.test/realms/indiehr"},  # the hospitals' realm
        ):
            with self.subTest(overrides=overrides):
                self.browser = Client(HTTP_HOST=HOST)
                response, _ = self.sign_in(**overrides)
                self.assertIn("refused", response["Location"])
                self.assertNotIn(settings.IHR_OPS_SESSION_COOKIE, response.cookies)

    def test_removal_from_the_list_ends_the_session(self):
        self.sign_in()
        self.assertEqual(self.browser.get("/ops/").status_code, 200)
        OperatorAccount.objects.filter(pk=self.account.pk).update(active=False)
        self.assertEqual(self.browser.get("/ops/")["Location"], "/ops/login/")

    def test_session_limits(self):
        self.sign_in()
        cookie = self.browser.cookies[settings.IHR_OPS_SESSION_COOKIE].value
        with schema_context("public"):
            session = auth.load_session(cookie)
            auth._mark_activity(
                session, time.time() - settings.IHR_SESSION_IDLE_SECONDS - 1
            )
        self.assertEqual(self.browser.get("/ops/")["Location"], "/ops/login/")
        self.assertEqual(self.calls["logout"], 1)  # idle ends the Keycloak sign-in too

    def test_the_progress_pages_own_refresh_is_not_activity(self):
        self.sign_in()
        op = TenantOperation.objects.create(
            kind="provision", slug="cons-auto", requested_by="test", status="running"
        )
        cookie = self.browser.cookies[settings.IHR_OPS_SESSION_COOKIE].value
        old = time.time() - 600
        with schema_context("public"):
            auth._mark_activity(auth.load_session(cookie), old)
        self.assertEqual(
            self.browser.get(f"/ops/operations/{op.id}/?auto=1").status_code, 200
        )
        with schema_context("public"):
            self.assertEqual(auth.load_session(cookie).last_activity, old)
        self.browser.get("/ops/")  # a real visit counts
        with schema_context("public"):
            self.assertGreater(auth.load_session(cookie).last_activity, old)

    def test_a_keycloak_outage_is_not_a_sign_out(self):
        self.sign_in()
        cookie = self.browser.cookies[settings.IHR_OPS_SESSION_COOKIE].value
        with schema_context("public"):
            session = auth.load_session(cookie)
            session.refreshed -= auth.REFRESH_AFTER + 1
            auth._store(session)

        def down(token):
            raise oidc.KeycloakUnavailableError("timeout")

        with testing.fake_keycloak(refresh=down):
            self.assertEqual(self.browser.get("/ops/").status_code, 503)
        with schema_context("public"):
            self.assertIsNotNone(auth.load_session(cookie))
        self.assertEqual(self.browser.get("/ops/").status_code, 200)

    def test_a_run_left_behind_by_a_stopped_provisioner_can_be_resumed(self):
        self.sign_in()
        op = TenantOperation.objects.create(
            kind="provision", slug="cons-stalled", requested_by="test", status="running"
        )
        page = self.browser.get(f"/ops/operations/{op.id}/")
        self.assertContains(page, "stopped part-way")
        self.assertContains(page, "Retry from where it stopped")
        self.assertContains(page, "Roll back")
        self.assertNotContains(page, 'http-equiv="refresh"')
        self.browser.post(f"/ops/operations/{op.id}/retry/")
        op.refresh_from_db()
        self.assertEqual(op.queued_action, "run")

    def test_a_refused_run_goes_back_on_the_queue(self):
        op = TenantOperation.objects.create(
            kind="provision",
            slug="cons-busy",
            requested_by="test",
            status="failed",
            queued_action="run",
            queued_by="x",
            queued_at=time.time() and None,
        )
        from django.utils import timezone

        TenantOperation.objects.filter(pk=op.pk).update(queued_at=timezone.now())
        from ihr_platform.provisioning import runner

        with runner._exclusive("cons-busy"):  # e.g. the command line is running it
            # Put back, and not counted as work: the loop sleeps instead of spinning on it.
            self.assertFalse(worker.work_once())
        op.refresh_from_db()
        self.assertEqual(op.queued_action, "run")
        self.assertIsNotNone(op.queued_at)

    def test_role_removed_in_keycloak_ends_the_session_at_refresh(self):
        self.sign_in()
        cookie = self.browser.cookies[settings.IHR_OPS_SESSION_COOKIE].value
        with schema_context("public"):
            session = auth.load_session(cookie)
            session.refreshed -= auth.REFRESH_AFTER + 1
            auth._store(session)
        no_role = lambda token: tokens(  # noqa: E731
            self.sub, "n", realm_access={"roles": []}
        )
        with testing.fake_keycloak(refresh=no_role):
            self.assertEqual(self.browser.get("/ops/")["Location"], "/ops/login/")

    def test_sign_out(self):
        self.sign_in()
        client = Client(HTTP_HOST=HOST, enforce_csrf_checks=True)
        client.cookies = self.browser.cookies
        self.assertEqual(client.post("/ops/logout/").status_code, 403)  # no CSRF token
        page = self.browser.get("/ops/")
        token = page.context["csrf_token"]
        response = client.post("/ops/logout/", {"csrfmiddlewaretoken": str(token)})
        self.assertIn("signed_out", response["Location"])
        self.assertEqual(self.calls["logout"], 1)
        self.assertEqual(self.browser.get("/ops/")["Location"], "/ops/login/")

    def test_hospital_cookies_and_hosts_never_reach_the_console(self):
        with schema_context(TEST_SCHEMA):
            hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
        from care.users.models import User

        with schema_context(TEST_SCHEMA):
            user = User.objects.create_user(
                username=f"cons{time.time_ns()}"[:16], password="x-Unused-1"
            )
        staff = testing.staff_client(hospital, "testserver", user)
        at_console = Client(HTTP_HOST=HOST)
        at_console.cookies = staff.cookies
        self.assertEqual(at_console.get("/ops/")["Location"], "/ops/login/")
        self.assertEqual(
            staff.get("/ops/").status_code, 404
        )  # no console on hospital hosts
        stray = Client(HTTP_HOST=HOST)
        stray.cookies["sessionid"] = "stray"
        self.assertNotEqual(stray.post("/ops/logout/").status_code, 500)

    # --- pages and queueing -----------------------------------------------------------

    def _form(self, slug, **changes):
        return {
            "slug": slug,
            "name": "Console Health Pvt Ltd",
            "display_name": "Console Hospital",
            "state_code": "27",
            "district": "Pune",
            "city": "Pune",
            "pin_code": "411001",
            "phone": "+912000000099",
            "address": "1 Test Road",
            "facility_name": "Console Hospital",
            "facility_type": "Private Hospital",
            "admin_email": "admin@cons.example.test",
            "admin_first_name": "Con",
            "admin_last_name": "Sole",
        } | changes

    def test_new_hospital_is_queued_for_the_provisioner(self):
        self.sign_in()
        page = self.browser.get("/ops/hospitals/new/")
        self.assertContains(page, "Maharashtra")
        bad = self.browser.post("/ops/hospitals/new/", self._form("auth"))
        self.assertContains(bad, "reserved")
        self.assertFalse(TenantOperation.objects.filter(slug="auth").exists())
        response = self.browser.post("/ops/hospitals/new/", self._form("cons-one"))
        op = TenantOperation.objects.get(slug="cons-one")
        self.assertEqual(response["Location"], f"/ops/operations/{op.id}/")
        self.assertEqual(op.queued_action, "run")
        self.assertIn(str(self.sub), op.queued_by)
        self.assertIn(str(self.sub), op.requested_by)
        detail = self.browser.get(f"/ops/operations/{op.id}/")
        self.assertContains(detail, 'http-equiv="refresh"')  # refreshes while queued

        claimed = worker.claim_next()
        self.assertEqual((claimed[0].pk, claimed[1]), (op.pk, "run"))
        self.assertIsNone(worker.claim_next())  # taken off the queue once

        TenantOperation.objects.filter(pk=op.pk).update(status="failed")
        self.browser.post(f"/ops/operations/{op.id}/rollback/")
        op.refresh_from_db()
        self.assertEqual(op.queued_action, "rollback")
        with mock.patch.object(worker.runner, "rollback") as rollback:
            self.assertTrue(worker.work_once())
        rollback.assert_called_once()
        self.assertIn(str(self.sub), rollback.call_args.args[1])

        TenantOperation.objects.filter(pk=op.pk).update(status="failed")
        self.browser.post(f"/ops/operations/{op.id}/retry/")
        with mock.patch.object(worker.runner, "run") as run:
            self.assertTrue(worker.work_once())
        run.assert_called_once()
        self.assertFalse(worker.work_once())

    def test_first_admin_setup_email_can_be_sent_again(self):
        from care.users.models import User

        from ihr_platform.management.commands import ihr_provision_worker as worker
        from ihr_platform.models import Hospital

        self.sign_in()
        hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
        with schema_context(TEST_SCHEMA):
            admin = User.objects.create_user(
                username=f"cons-{time.time_ns()}"[:16], password=None, is_active=False
            )
        self.addCleanup(lambda: self._delete_user(admin))
        op = TenantOperation.objects.create(
            kind="provision",
            slug="cons-setup",
            hospital=hospital,
            status="succeeded",
            input={"admin_email": "first.admin@cons.example.test"},
            result={
                "admin_user_id": admin.pk,
                "setup_email_sent_at": "2026-11-07T10:00:00+05:30",
                "setup_link_hours": 72,
            },
        )
        page = self.browser.get(f"/ops/operations/{op.id}/").content.decode()
        self.assertIn(
            "Setup e-mail sent to first.admin@cons.example.test on 07-11-2026", page
        )
        self.assertIn("Send the setup e-mail again", page)
        self.browser.post(f"/ops/operations/{op.id}/resend-setup/")
        op.refresh_from_db()
        self.assertEqual(op.queued_action, "resend_setup")
        with mock.patch.object(worker.runner, "resend_setup") as resend:
            self.assertTrue(worker.work_once())
        resend.assert_called_once()

        with schema_context(TEST_SCHEMA):
            User.objects.filter(pk=admin.pk).update(is_active=True)
        page = self.browser.get(f"/ops/operations/{op.id}/").content.decode()
        self.assertIn("has set up their account", page)
        self.assertNotIn("Send the setup e-mail again", page)

    def _delete_user(self, user):
        with schema_context(TEST_SCHEMA):
            user.delete()

    def test_forms_need_the_csrf_token(self):
        self.sign_in()
        client = Client(HTTP_HOST=HOST, enforce_csrf_checks=True)
        client.cookies = self.browser.cookies
        response = client.post("/ops/hospitals/new/", self._form("cons-two"))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(TenantOperation.objects.filter(slug="cons-two").exists())
