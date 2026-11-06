"""Staff sessions, sign-in, sign-out and the switched-off CARE routes (P1-T4a, ADR-009)."""

import base64
import hashlib
import time
from io import StringIO
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from care.users.models import User
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from django_tenants.utils import schema_context
from ihr_platform.models import Hospital
from ihr_platform.test_runner import TEST_SCHEMA
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from ihr_identity import checks, oidc, sessions, testing
from ihr_identity.models import StaffIdentity

ME = "/api/v1/users/getcurrentuser/"
HOST = "testserver"


def hospital():
    with schema_context("public"):
        return testing.ensure_org_id(Hospital.objects.get(schema_name=TEST_SCHEMA))


class StaffSessionTests(SimpleTestCase):
    databases = {"default"}

    def setUp(self):
        self.hospital = hospital()
        self.user = User.objects.create_user(
            username=f"staff-{time.time_ns()}", password="x-Unused-1"
        )
        self.keycloak = testing.fake_keycloak()
        self.calls = self.keycloak.__enter__()

    def tearDown(self):
        self.keycloak.__exit__(None, None, None)
        StaffIdentity.objects.filter(user=self.user).delete()
        self.user.delete()

    def client_for(self, user=None, **claims):
        return testing.staff_client(self.hospital, HOST, user or self.user, **claims)

    def session_of(self, client):
        return sessions.load(client.cookies[settings.IHR_SESSION_COOKIE].value)

    def assert_refused(self, client, path=ME):
        response = client.get(path)
        self.assertEqual(response.status_code, 403, response.content[:200])
        return response

    # --- signed in -------------------------------------------------------------------------

    def test_signed_in_staff_reach_the_api(self):
        response = self.client_for().get(ME)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], self.user.username)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("Cookie", response["Vary"])

    def test_session_view_never_returns_tokens(self):
        client = self.client_for()
        body = client.get("/api/ihr/auth/session/").json()
        self.assertEqual(body["hospital"], "testhosp")
        self.assertTrue(body["second_factor"])
        session = self.session_of(client)
        text = str(body)
        self.assertNotIn(session.access, text)
        self.assertNotIn(session.refresh, text)

    def test_no_cookie_and_unknown_cookie_are_not_signed_in(self):
        self.assert_refused(APIClient(HTTP_HOST=HOST))
        stranger = APIClient(HTTP_HOST=HOST)
        stranger.cookies[settings.IHR_SESSION_COOKIE] = "made-up"
        response = self.assert_refused(stranger)
        self.assertEqual(response.cookies[settings.IHR_SESSION_COOKIE].value, "")

    def test_redis_dump_reveals_neither_cookie_nor_tokens(self):
        from django_redis import get_redis_connection

        client = self.client_for()
        session = self.session_of(client)
        redis = get_redis_connection("default")
        keys = list(redis.scan_iter("*ihr:sess:*", count=1000))
        self.assertTrue(any(sessions._base(session.cookie).encode() in k for k in keys))
        dump = b"".join(redis.dump(key) or b"" for key in keys)
        for secret in (session.cookie, session.access, session.refresh, session.sub):
            self.assertNotIn(secret.encode(), dump)

    # --- who may hold a session ---------------------------------------------------------

    def test_unlinked_person_is_refused(self):
        client = self.client_for()
        StaffIdentity.objects.filter(user=self.user).delete()
        self.assert_refused(client)

    def test_deactivated_or_deleted_user_is_refused(self):
        for change in ({"is_active": False}, {"deleted": True}):
            with self.subTest(change=change):
                client = self.client_for()
                User.objects.filter(pk=self.user.pk).update(**change)
                self.assert_refused(client)
                User.objects.filter(pk=self.user.pk).update(
                    is_active=True, deleted=False
                )

    def test_deactivation_ends_sessions_through_the_signal(self):
        client = self.client_for()
        self.user.is_active = False
        self.user.save()
        self.user.is_active = True
        self.user.save()
        self.assert_refused(client)  # session predates the deactivation

    def test_superusers_and_machine_accounts_cannot_sign_in(self):
        for change in ({"is_superuser": True}, {"is_service_account": True}):
            with self.subTest(change=change):
                client = self.client_for()
                User.objects.filter(pk=self.user.pk).update(**change)
                self.assert_refused(client)
                User.objects.filter(pk=self.user.pk).update(
                    is_superuser=False, is_service_account=False
                )

    def test_session_follows_its_own_care_user(self):
        """Re-linking the person to another CARE user does not carry live sessions over."""
        client = self.client_for()
        other = User.objects.create_user(
            username=f"other-{time.time_ns()}", password="x-Unused-1"
        )
        try:
            identity = StaffIdentity.objects.get(user=self.user)
            identity.user = other
            identity.save()
            self.assert_refused(client)
        finally:
            StaffIdentity.objects.filter(user=other).delete()
            other.delete()

    def test_revoked_person_is_refused(self):
        client = self.client_for()
        StaffIdentity.objects.filter(user=self.user).update(
            sessions_valid_after=timezone.now()
        )
        self.assert_refused(client)

    def test_session_from_another_schema_is_refused(self):
        """Stored under this hospital's key prefix but created for another hospital."""
        client = self.client_for()
        session = self.session_of(client)
        session.tid = "h_otherhosp1"
        sessions._store(session)
        self.assert_refused(client)

    def test_suspended_hospital_refuses_everyone(self):
        client = self.client_for()
        with schema_context("public"):
            Hospital.objects.filter(pk=self.hospital.pk).update(status="suspended")
        try:
            from django.db import connection

            connection.tenant.status = "suspended"
            self.assert_refused(client)
        finally:
            with schema_context("public"):
                Hospital.objects.filter(pk=self.hospital.pk).update(status="active")
            connection.tenant.status = "active"

    def test_token_for_another_client_or_without_mfa_is_refused(self):
        for claims in (
            {"azp": "ihr-h-beta"},
            {"acr": "pwd"},
            {"resource_access": {"ihr-h-testhosp": {"roles": []}}},
            {
                "ihr_org": {
                    "alias": "testhosp",
                    "id": "00000000-0000-0000-0000-000000000000",
                }
            },
        ):
            with self.subTest(claims=claims):
                self.assert_refused(self.client_for(**claims))

    # --- limits ----------------------------------------------------------------------------

    def test_idle_session_signs_out_everywhere(self):
        client = self.client_for()
        session = self.session_of(client)
        idle = settings.IHR_SESSION_IDLE_SECONDS + 5
        sessions.mark_activity(session, time.time() - idle)
        # As in production: each request in one transaction, rolled back when refused.
        settings_dict = connection.settings_dict
        settings_dict["ATOMIC_REQUESTS"] = True
        try:
            self.assertEqual(client.get(ME, HTTP_X_IHR_IDLE=str(idle)).status_code, 403)
        finally:
            settings_dict["ATOMIC_REQUESTS"] = False
        self.assertEqual(self.calls["logout"], 1)
        self.assertIsNotNone(
            StaffIdentity.objects.get(user=self.user).sessions_valid_after
        )
        self.assertIsNone(sessions.load(session.cookie))

    def test_polling_does_not_keep_an_idle_session_alive(self):
        """Requests carry the web app's idle time; only real input moves activity forward."""
        limit = settings.IHR_SESSION_IDLE_SECONDS
        client = self.client_for()
        session = self.session_of(client)
        sessions.mark_activity(session, time.time() - limit + 60)
        self.assertEqual(
            client.get(ME, HTTP_X_IHR_IDLE=str(limit - 60)).status_code, 200
        )
        self.assertLess(sessions.last_activity(session), time.time() - limit + 70)
        sessions.mark_activity(session, time.time() - limit - 1)
        self.assertEqual(
            client.get(ME, HTTP_X_IHR_IDLE=str(limit + 1)).status_code, 403
        )
        # Recent input reported by the web app keeps the session going.
        other = self.client_for()
        sessions.mark_activity(self.session_of(other), time.time() - limit - 1)
        self.assertEqual(other.get(ME, HTTP_X_IHR_IDLE="5").status_code, 200)

    def test_session_ends_after_twelve_hours(self):
        client = self.client_for()
        session = self.session_of(client)
        session.created -= settings.IHR_SESSION_MAX_SECONDS + 1
        sessions._store(session)
        self.assert_refused(client)

    # --- refresh ----------------------------------------------------------------------------

    def test_tokens_refresh_once_a_minute_old(self):
        client = self.client_for()
        session = self.session_of(client)
        session.access_iat -= 61
        sessions._store(session)
        self.assertEqual(client.get(ME).status_code, 200)
        self.assertEqual(self.calls["refresh"], 1)
        self.assertNotEqual(self.session_of(client).refresh, session.refresh)

    def test_refused_refresh_ends_the_session(self):
        def refused(token):
            raise oidc.GrantRejectedError("invalid_grant")

        for refresh in (
            refused,
            lambda t: testing.tokens_for(self.hospital, "x", acr="pwd"),
        ):
            with self.subTest(refresh=refresh), testing.fake_keycloak(refresh=refresh):
                client = self.client_for()
                session = self.session_of(client)
                session.access_iat -= 61
                sessions._store(session)
                self.assert_refused(client)
                self.assertIsNone(sessions.load(session.cookie))

    def test_a_request_with_an_old_copy_never_reuses_a_rotated_refresh_token(self):
        """Two requests load the session; the first refreshes; the second must not refresh
        again with the now-used refresh token (Keycloak would refuse it and end the session)."""
        from ihr_identity.authentication import StaffSessionAuthentication

        client = self.client_for()
        session = self.session_of(client)
        session.access_iat -= 61
        sessions._store(session)
        stale = sessions.load(session.cookie)
        self.assertEqual(client.get(ME).status_code, 200)  # refreshes
        request = mock.Mock(_request=mock.Mock())
        fresh = StaffSessionAuthentication()._refreshed(request, stale, time.time())
        self.assertIsNotNone(fresh)
        self.assertEqual(self.calls["refresh"], 1)
        self.assertEqual(client.get(ME).status_code, 200)

    def test_an_expired_token_while_another_request_refreshes_keeps_the_session(self):
        client = self.client_for()
        session = self.session_of(client)
        old = int(time.time()) - 400
        session.access = testing.mint(
            self.hospital, session.sub, iat=old, exp=old + 300
        )
        session.access_iat = old
        sessions._store(session)
        token = sessions.lock(session)  # another request is refreshing
        with mock.patch("ihr_identity.authentication.WAIT_STEPS", 1):
            self.assert_refused(client)
        sessions.unlock(session, token)
        self.assertIsNotNone(sessions.load(session.cookie))
        self.assertEqual(client.get(ME).status_code, 200)  # now it refreshes

    def test_keycloak_outage_keeps_a_still_valid_session(self):
        def down(token):
            raise oidc.KeycloakUnavailableError("timeout")

        with testing.fake_keycloak(refresh=down):
            client = self.client_for()
            session = self.session_of(client)
            session.access_iat -= 61
            sessions._store(session)
            self.assertEqual(client.get(ME).status_code, 200)

    # --- CSRF -------------------------------------------------------------------------------

    def _batch(self, client, **headers):
        body = {"requests": [{"url": ME, "method": "GET", "reference_id": "me"}]}
        return client.post("/api/v1/batch_requests/", body, format="json", **headers)

    def test_unsafe_requests_need_the_header_and_this_origin(self):
        client = self.client_for()
        self.assertEqual(self._batch(client).status_code, 200)
        bare = APIClient(HTTP_HOST=HOST)
        bare.cookies[settings.IHR_SESSION_COOKIE] = client.cookies[
            settings.IHR_SESSION_COOKIE
        ].value
        self.assertEqual(self._batch(bare).status_code, 403)
        self.assertEqual(
            self._batch(client, HTTP_SEC_FETCH_SITE="same-site").status_code, 403
        )
        self.assertEqual(
            self._batch(client, HTTP_ORIGIN="https://evil.test").status_code, 403
        )
        self.assertEqual(
            self._batch(client, HTTP_ORIGIN="https://testserver").status_code, 200
        )

    def test_batch_sub_requests_use_the_session(self):
        response = self._batch(self.client_for())
        self.assertEqual(response.json()["results"][0]["status_code"], 200)

    # --- CARE's own sign-in is off ----------------------------------------------------------

    def test_care_password_sign_in_routes_are_gone(self):
        client = self.client_for()
        routes = [
            "/api/v1/auth/login/",
            "/api/v1/auth/logout/",
            "/api/v1/auth/token/refresh/",
            "/api/v1/auth/token/verify/",
            "/api/v1/mfa/login/",
            "/api/v1/mfa/totp/setup/",
            "/api/v1/password_reset/",
            "/api/v1/password_reset/confirm/",
            "/api/v1/password_change/",
            "/api/v1/otp/password_reset/send/",
            f"/{settings.ADMIN_URL.strip('/')}/",
            f"/api/v1/users/{self.user.username}/generate_service_account_token/",
        ]
        for route in routes:
            with self.subTest(route=route):
                self.assertEqual(client.get(route).status_code, 404)
                self.assertEqual(client.post(route, {}).status_code, 404)
        from django.urls import resolve

        from ihr_identity.blocked import Blocked

        # DRF's format-suffix routes (DEBUG) are caught too.
        for suffixed in (
            "/api/v1/users/x/generate_service_account_token.json",
            "/api/v1/otp/password_reset.json",
            "/api/v1/mfa.json",
        ):
            self.assertIs(resolve(suffixed).func.view_class, Blocked, suffixed)
        body = {"requests": [{"url": routes[0], "method": "POST", "reference_id": "x"}]}
        batch = client.post("/api/v1/batch_requests/", body, format="json")
        self.assertEqual(batch.json()["results"][0]["status_code"], 404)

    def test_care_jwt_is_no_longer_accepted(self):
        from rest_framework_simplejwt.tokens import RefreshToken

        client = APIClient(HTTP_HOST=HOST)
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(self.user).access_token}"
        )
        self.assert_refused(client)

    def test_machine_tokens_only_for_machine_accounts(self):
        token = Token.objects.create(user=self.user)
        client = APIClient(HTTP_HOST=HOST)
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        self.assertIn(client.get(ME).status_code, (401, 403))
        User.objects.filter(pk=self.user.pk).update(is_service_account=True)
        self.assertEqual(client.get(ME).status_code, 200)
        token.delete()

    def test_settings_checks(self):
        self.assertEqual(checks.staff_sign_in_settings(None), [])
        rest = {**settings.REST_FRAMEWORK, "DEFAULT_AUTHENTICATION_CLASSES": ["x.Y"]}
        with override_settings(REST_FRAMEWORK=rest, CORS_ALLOW_CREDENTIALS=True):
            ids = {e.id for e in checks.staff_sign_in_settings(None)}
        self.assertEqual(ids, {"ihr_identity.E001", "ihr_identity.E002"})
        with override_settings(DEBUG=False, IHR_SESSION_COOKIE="ihr_session"):
            ids = {e.id for e in checks.staff_sign_in_settings(None)}
        self.assertIn("ihr_identity.E003", ids)


class SignInFlowTests(SimpleTestCase):
    databases = {"default"}

    def setUp(self):
        self.hospital = hospital()
        self.user = User.objects.create_user(
            username=f"flow-{time.time_ns()}", password="x-Unused-1"
        )
        self.identity = testing.link(self.user)
        self.keycloak = testing.fake_keycloak()
        self.calls = self.keycloak.__enter__()
        self.browser = APIClient(HTTP_HOST=HOST)

    def tearDown(self):
        self.keycloak.__exit__(None, None, None)
        StaffIdentity.objects.filter(user=self.user).delete()
        self.user.delete()

    def start(self, next_path="/patients"):
        response = self.browser.get("/api/ihr/auth/login/", {"next": next_path})
        self.assertEqual(response.status_code, 302)
        return parse_qs(urlsplit(response["Location"]).query)

    def finish(self, params, sub=None, auth_time=None, **id_claims):
        sub = str(sub or self.identity.keycloak_sub)
        state = sessions.unseal(
            self.browser.cookies[settings.IHR_LOGIN_COOKIE].value,
            settings.SECRET_KEY.encode(),
            "ihr-login",
        ) or {"nonce": "n"}
        tokens = testing.tokens_for(self.hospital, sub)
        with mock.patch(
            "ihr_identity.testing.time.time", return_value=auth_time or time.time()
        ):
            id_token = testing.mint(
                self.hospital, sub, kind="ID", **{"nonce": state["nonce"], **id_claims}
            )
        tokens["id_token"] = id_token
        with mock.patch.object(oidc, "exchange_code", return_value=tokens) as exchange:
            response = self.browser.get(
                "/api/ihr/auth/callback/", {"code": "c0de", "state": params["state"][0]}
            )
        return response, exchange

    def test_sign_in_goes_to_keycloak_with_pkce(self):
        params = self.start()
        self.assertEqual(params["client_id"], ["ihr-h-testhosp"])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(
            params["redirect_uri"], ["https://testserver/api/ihr/auth/callback/"]
        )
        self.assertNotIn("prompt", params)
        cookie = self.browser.cookies[settings.IHR_LOGIN_COOKIE]
        self.assertTrue(cookie["httponly"])
        self.assertNotIn(params["state"][0], cookie.value)  # sealed, not readable

    def test_full_sign_in_sets_a_session_and_goes_back(self):
        params = self.start("/patients?x=1")
        response, exchange = self.finish(params)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/patients?x=1")
        code, verifier, redirect_uri = exchange.call_args.args
        self.assertEqual(code, "c0de")
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        self.assertEqual(challenge, params["code_challenge"][0])  # PKCE ties them
        self.assertEqual(redirect_uri, params["redirect_uri"][0])
        cookie = response.cookies[settings.IHR_SESSION_COOKIE]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(self.browser.get(ME, HTTP_X_IHR_CSRF="1").status_code, 200)

    def test_unsafe_next_falls_back_to_home(self):
        for target in (
            "//evil.test/x",
            "/\\evil.test",
            "https://evil.test/",
            "/\t/evil.test",
        ):
            with self.subTest(target=target):
                response, _ = self.finish(self.start(target))
                self.assertEqual(response["Location"], "/")

    def test_state_must_match_and_is_single_use(self):
        params = self.start()
        response, _ = self.finish({"state": ["forged"]})
        self.assertEqual(response["Location"], "/login?error=expired")
        response, _ = self.finish(params)  # cookie was cleared by the failed attempt
        self.assertEqual(response["Location"], "/login?error=expired")

    def test_wrong_nonce_is_refused(self):
        response, _ = self.finish(self.start(), nonce="other")
        self.assertEqual(response["Location"], "/login?error=refused")

    def test_person_without_a_link_is_refused(self):
        StaffIdentity.objects.filter(user=self.user).delete()
        response, _ = self.finish(self.start())
        self.assertEqual(response["Location"], "/login?error=not_linked")
        self.assertNotIn(settings.IHR_SESSION_COOKIE, response.cookies)
        # Their sign-in at other hospitals is left alone (this is not a sign-out).
        self.assertEqual(self.calls["logout"], 0)

    def test_login_state_is_bound_to_this_hospital_and_short_lived(self):
        params = self.start()
        cookie = self.browser.cookies[settings.IHR_LOGIN_COOKIE].value
        secret = settings.SECRET_KEY.encode()
        state = sessions.unseal(cookie, secret, "ihr-login")
        for change in ({"tid": "h_otherhosp1"}, {"iat": time.time() - 601}):
            with self.subTest(change=change):
                forged = sessions.seal({**state, **change}, secret, "ihr-login")
                self.browser.cookies[settings.IHR_LOGIN_COOKIE] = forged
                response = self.browser.get(
                    "/api/ihr/auth/callback/",
                    {"code": "c", "state": params["state"][0]},
                )
                self.assertEqual(response["Location"], "/login?error=expired")

    def test_sign_out_without_a_session_says_keycloak_was_not_told(self):
        self.browser.cookies[settings.IHR_SESSION_COOKIE] = "unknown"
        response = self.browser.post("/api/ihr/auth/logout/", HTTP_X_IHR_CSRF="1")
        self.assertEqual(response.json(), {"signed_out_everywhere": False})

    def test_after_sign_out_the_next_sign_in_must_be_fresh(self):
        StaffIdentity.objects.filter(user=self.user).update(
            sessions_valid_after=timezone.now()
        )
        old = time.time() - 120
        response, _ = self.finish(self.start(), auth_time=old)
        self.assertTrue(
            response["Location"].startswith("/api/ihr/auth/login/?reauth=1")
        )
        params = parse_qs(
            urlsplit(self.browser.get(response["Location"])["Location"]).query
        )
        self.assertEqual(params["prompt"], ["login"])
        response, _ = self.finish(params, auth_time=old)
        self.assertEqual(response["Location"], "/login?error=reauth")
        response, _ = self.finish(self.start(), auth_time=time.time() + 1)
        self.assertEqual(response["Location"], "/patients")

    def test_sign_out_ends_the_session_and_single_sign_on(self):
        self.finish(self.start())
        cookie = self.browser.cookies[settings.IHR_SESSION_COOKIE].value
        self.assertEqual(
            self.browser.post("/api/ihr/auth/logout/").status_code, 403
        )  # no header
        response = self.browser.post("/api/ihr/auth/logout/", HTTP_X_IHR_CSRF="1")
        self.assertEqual(response.json(), {"signed_out_everywhere": True})
        self.assertEqual(self.calls["logout"], 1)
        self.assertIsNone(sessions.load(cookie))
        replay = APIClient(HTTP_HOST=HOST)
        replay.cookies[settings.IHR_SESSION_COOKIE] = cookie
        self.assertEqual(replay.get(ME).status_code, 403)

    def test_sign_out_reports_when_keycloak_could_not_be_told(self):
        self.finish(self.start())
        with testing.fake_keycloak(logout_ok=False):
            response = self.browser.post("/api/ihr/auth/logout/", HTTP_X_IHR_CSRF="1")
        self.assertEqual(response.json(), {"signed_out_everywhere": False})
        # Even then, the next sign-in here must ask for the password and code again.
        self.assertIsNotNone(
            StaffIdentity.objects.get(user=self.user).sessions_valid_after
        )


class LinkStaffCommandTests(SimpleTestCase):
    databases = {"default"}

    def test_link_needs_confirmation_and_refuses_superusers(self):
        user = User.objects.create_user(
            username=f"link-{time.time_ns()}", password="x-Unused-1"
        )
        sub = "22222222-2222-2222-2222-222222222222"
        args = ["--slug", "testhosp", "--username", user.username, "--sub", sub]
        try:
            with (
                schema_context("public"),
                self.assertRaisesMessage(CommandError, "--yes"),
            ):
                call_command("ihr_link_staff", *args, stdout=StringIO())
            with schema_context("public"):
                call_command("ihr_link_staff", *args, "--yes", stdout=StringIO())
            link = StaffIdentity.objects.get(user=user)
            self.assertEqual(str(link.keycloak_sub), sub)
            self.assertIsNotNone(
                link.sessions_valid_after
            )  # a re-link never revives sessions
            User.objects.filter(pk=user.pk).update(is_superuser=True)
            with (
                schema_context("public"),
                self.assertRaisesMessage(CommandError, "Superusers"),
            ):
                call_command("ihr_link_staff", *args, "--yes", stdout=StringIO())
        finally:
            StaffIdentity.objects.filter(user=user).delete()
            user.delete()
