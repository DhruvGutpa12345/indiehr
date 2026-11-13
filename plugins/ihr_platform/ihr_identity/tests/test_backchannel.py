"""Keycloak's back-channel logout (P1-T5d): a sign-in ended in Keycloak ends here at once.

Tokens are minted in the shape Keycloak 26.8 sends (captured from a real sign-out); the live
check is the dev click-through (Keycloak reaches the backend inside the compose network).
"""

import time
import uuid

from care.users.models import User
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase
from django_tenants.utils import schema_context
from ihr_platform.models import Hospital
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_platform.testing import as_owner
from rest_framework.test import APIClient

from ihr_identity import sessions, testing
from ihr_identity.models import StaffIdentity

ME = "/api/v1/users/getcurrentuser/"
HOST = "testserver"
PLATFORM = "console.testserver"  # the platform host the console tests use too
URL = "/api/ihr/backchannel-logout/"


class BackChannelLogoutTests(SimpleTestCase):
    databases = {"default"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        connection.set_schema_to_public()
        try:
            with as_owner():  # the migrate one-shot's ihr_create_tenant --public
                call_command(
                    "ihr_create_tenant", public=True, domain=[PLATFORM], verbosity=0
                )
        finally:
            connection.set_schema(TEST_SCHEMA)  # where every other test expects to be

    def setUp(self):
        with schema_context("public"):
            self.hospital = testing.ensure_org_id(
                Hospital.objects.get(schema_name=TEST_SCHEMA)
            )
        self.user = User.objects.create_user(
            username=f"bc-{time.time_ns()}"[:16], password="x-Unused-1"
        )
        self.keycloak = testing.fake_keycloak()
        self.keycloak.__enter__()
        self.platform = APIClient(HTTP_HOST=PLATFORM)

    def tearDown(self):
        self.keycloak.__exit__(None, None, None)
        connection.set_schema(TEST_SCHEMA)
        StaffIdentity.objects.filter(user=self.user).delete()
        self.user.delete()

    def signed_in(self, sid, user=None):
        # One user per sign-in: the helper links the user to a fresh Keycloak id each time.
        client = testing.staff_client(self.hospital, HOST, user or self.user, sid=sid)
        self.assertEqual(client.get(ME).status_code, 200)
        return client

    def post(self, token, **kwargs):
        response = self.platform.post(URL, {"logout_token": token}, **kwargs)
        connection.set_schema(TEST_SCHEMA)  # the request ran on the platform host
        return response

    def test_a_signed_logout_token_ends_that_sign_in_here_at_once(self):
        ended = self.signed_in("sid-ended")
        colleague = User.objects.create_user(
            username=f"bc2-{time.time_ns()}"[:16], password="x-Unused-1"
        )
        self.addCleanup(colleague.delete)
        self.addCleanup(StaffIdentity.objects.filter(user=colleague).delete)
        other = self.signed_in("sid-other", colleague)  # a sign-in of its own
        response = self.post(testing.mint_logout(self.hospital, "sid-ended"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Cache-Control"],
            "max-age=0, no-cache, no-store, must-revalidate, private",
        )
        self.assertEqual(ended.get(ME).status_code, 403)
        self.assertEqual(ended.get(ME).status_code, 403)  # the session itself is gone
        self.assertEqual(other.get(ME).status_code, 200)
        # Replayed: still fine, nothing more happens.
        self.assertEqual(
            self.post(testing.mint_logout(self.hospital, "sid-ended")).status_code, 200
        )
        self.assertEqual(other.get(ME).status_code, 200)

    def test_the_marker_is_kept_per_hospital(self):
        self.post(testing.mint_logout(self.hospital, "sid-marked"))
        probe = sessions.StaffSession(
            tid="x",
            user_id=0,
            sub="s",
            sid="sid-marked",
            created=0,
            auth_time=0,
            access="",
            access_exp=0,
            access_iat=0,
            refresh="",
        )
        with schema_context(TEST_SCHEMA):
            self.assertTrue(sessions.sign_in_ended(probe))
        with schema_context("public"):  # another schema's prefix: not marked there
            self.assertFalse(sessions.sign_in_ended(probe))

    def test_anything_else_is_refused_and_ends_nothing(self):
        client = self.signed_in("sid-kept")
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        old = int(time.time()) - 400
        events = "http://schemas.openid.net/event/backchannel-logout"
        for name, token in (
            ("empty", ""),
            ("not a JWT", "not-a-jwt"),
            ("an access token", testing.mint(self.hospital, uuid.uuid4())),
            ("wrong type", testing.mint_logout(self.hospital, "sid-kept", typ="JWT")),
            (
                "another key",
                testing.mint_logout(self.hospital, "sid-kept", key=other_key),
            ),
            (
                "another issuer",
                testing.mint_logout(
                    self.hospital, "sid-kept", iss="https://evil.test/realms/indiehr"
                ),
            ),
            (
                "unknown hospital",
                testing.mint_logout(self.hospital, "sid-kept", aud="ihr-h-nosuch"),
            ),
            (
                "not a hospital client",
                testing.mint_logout(self.hospital, "sid-kept", aud="ihr-console"),
            ),
            (
                "two audiences",
                testing.mint_logout(
                    self.hospital, "sid-kept", aud=["ihr-h-testhosp", "ihr-h-other"]
                ),
            ),
            ("no event", testing.mint_logout(self.hospital, "sid-kept", events={})),
            (
                "wrong event",
                testing.mint_logout(
                    self.hospital, "sid-kept", events={events + "x": {}}
                ),
            ),
            ("a nonce", testing.mint_logout(self.hospital, "sid-kept", nonce="n")),
            ("no sid", testing.mint_logout(self.hospital, None)),
            ("empty sid", testing.mint_logout(self.hospital, "")),
            (
                "expired",
                testing.mint_logout(self.hospital, "sid-kept", iat=old, exp=old + 120),
            ),
            (
                "too old",
                testing.mint_logout(self.hospital, "sid-kept", iat=old, exp=old + 3600),
            ),
            (
                "from the future",
                testing.mint_logout(
                    self.hospital, "sid-kept", iat=int(time.time()) + 600
                ),
            ),
            ("no jti", testing.mint_logout(self.hospital, "sid-kept", jti=None)),
        ):
            with self.subTest(name):
                self.assertEqual(self.post(token).status_code, 400)
        self.assertEqual(self.platform.get(URL).status_code, 405)
        connection.set_schema(TEST_SCHEMA)
        self.assertEqual(client.get(ME).status_code, 200)

    def test_the_token_check_itself_demands_this_hospitals_client(self):
        from ihr_identity.tokens import TokenRejectedError, verify_logout_token

        token = testing.mint_logout(self.hospital, "sid-y")
        check = {
            "keys": testing.public_keys(),
            "issuer": testing.oidc.issuer(),
            "now": time.time(),
        }
        self.assertEqual(
            verify_logout_token(token, client_id="ihr-h-testhosp", **check)["sid"],
            "sid-y",
        )
        with self.assertRaisesMessage(TokenRejectedError, "another client"):
            verify_logout_token(token, client_id="ihr-h-other", **check)

    def test_only_on_the_platform_host(self):
        token = testing.mint_logout(self.hospital, "sid-x")
        response = APIClient(HTTP_HOST=HOST).post(URL, {"logout_token": token})
        self.assertEqual(response.status_code, 404)
