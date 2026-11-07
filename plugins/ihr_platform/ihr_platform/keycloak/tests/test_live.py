"""Live tests against a real Keycloak (P1-T3). Run with scripts/keycloak-test.sh.

Each run builds a throwaway realm ``ihrtest_<8 hex>`` with the production baseline code, two
hospitals and fresh users, drives the real login pages with a scripted browser, and deletes the
realm at the end. Skipped unless IHR_KC_TEST_URL is set (so the Django suite skips it).
"""

import os
import re
import secrets
import time
import unittest
import uuid
from urllib.parse import urlencode

from ihr_platform.keycloak.browser import Browser, decode_jwt
from ihr_platform.keycloak.hospital import (
    HospitalLogin,
    check_hospital,
    client_id_for,
    ensure_hospital,
    ensure_member,
    rebuild_flow,
    remove_member,
)
from ihr_platform.keycloak.invite import (
    ExistingAccountError,
    account_state,
    delete_pending,
    ensure_invited_user,
    find_invited_by_email,
    remove_invited_user,
    reset_sign_in,
    send_setup_email,
)
from ihr_platform.keycloak.kc_http import KeycloakAdmin, KeycloakError, request
from ihr_platform.keycloak.realm import (
    FLOW_TEMPLATE,
    HOSPITAL_SCOPE,
    INVITED_FOR,
    check_realm,
    ensure_realm,
)
from ihr_platform.keycloak.users import create_user

URL = os.environ.get("IHR_KC_TEST_URL")
# Setup e-mails (P1-T5c): Keycloak sends to Mailpit, the test reads them from its API.
SMTP = {
    "host": os.environ.get("IHR_KC_TEST_SMTP_HOST", "mailpit"),
    "port": "1025",
    "from": "no-reply@indiehr.test",
    "fromDisplayName": "IndieHR",
}
MAILPIT = os.environ.get("IHR_KC_TEST_MAILPIT", "http://127.0.0.1:8025")
REALM_RE = re.compile(r"^ihrtest_[0-9a-f]{8}$")
NOT_MEMBER = "You are not a member of this hospital."


def hospital(slug):
    origin = f"http://{slug}.localhost:8099"
    return HospitalLogin(
        slug, f"{slug.title()} Test Hospital", (origin,), (f"{origin}/cb",)
    )


@unittest.skipUnless(URL, "set IHR_KC_TEST_URL to run the live Keycloak tests")
class KeycloakLiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.realm = f"ihrtest_{secrets.token_hex(4)}"
        cls.admin = KeycloakAdmin(
            URL,
            os.environ["IHR_KC_TEST_ADMIN_ID"],
            os.environ["IHR_KC_TEST_ADMIN_SECRET"],
            cls.realm,
        )
        ensure_realm(cls.admin, smtp=SMTP)
        cls.h = {slug: hospital(slug) for slug in ("alpha", "beta")}
        cls.ids = {slug: ensure_hospital(cls.admin, h) for slug, h in cls.h.items()}

    @classmethod
    def tearDownClass(cls):
        if not REALM_RE.match(cls.realm):  # never delete anything else
            raise RuntimeError(cls.realm)
        cls.admin.delete(f"/{cls.realm}")

    # --- helpers --------------------------------------------------------------------------

    def user(self, hospitals, otp=True):
        """A fresh user who belongs to ``hospitals``: (id, username, password, otp secret)."""
        username = f"u{secrets.token_hex(4)}"
        password = f"Pw-{secrets.token_urlsafe(16)}"
        secret = secrets.token_hex(10) if otp else None
        user_id = create_user(self.admin, username, "Test", "User", password, secret)
        for slug in hospitals:
            ensure_member(self.admin, self.ids[slug], user_id)
        return user_id, username, password, secret

    def browser(self):
        return Browser(URL, self.realm)

    def login(self, browser, slug, username, password, secret, **kw):
        return browser.login(
            client_id_for(slug),
            self.h[slug].redirect_uris[0],
            username,
            password,
            secret,
            **kw,
        )

    def assert_hospital_token(self, result, slug):
        self.assertIsNotNone(result.tokens, f"no token: {result.pages} {result.error}")
        claims = result.access
        cid = client_id_for(slug)
        self.assertEqual(claims["iss"], f"{URL}/realms/{self.realm}")
        self.assertEqual(claims["typ"], "Bearer")
        self.assertEqual(claims["azp"], cid)
        self.assertEqual(
            claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]], [cid]
        )
        self.assertEqual(
            claims["ihr_org"], {"alias": slug, "id": self.ids[slug].org_id}
        )
        self.assertEqual(claims["resource_access"], {cid: {"roles": ["member"]}})
        self.assertNotIn("realm_access", claims)
        self.assertNotIn("organization", claims)
        self.assertEqual(claims["acr"], "mfa")
        self.assertLessEqual(claims["exp"] - claims["iat"], 300)
        return claims

    # --- signing in -----------------------------------------------------------------------

    def test_member_gets_a_token_for_their_hospital_only(self):
        _, name, pw, secret = self.user(["alpha"])
        result = self.login(self.browser(), "alpha", name, pw, secret)
        self.assertEqual(result.pages, ["username", "otp"])
        self.assert_hospital_token(result, "alpha")
        self.assertEqual(decode_jwt(result.tokens["id_token"])["acr"], "mfa")

    def test_second_hospital_signs_in_silently_with_its_own_token(self):
        _, name, pw, secret = self.user(["alpha", "beta"])
        browser = self.browser()
        self.assert_hospital_token(
            self.login(browser, "alpha", name, pw, secret), "alpha"
        )
        result = self.login(browser, "beta", name, pw, secret)
        self.assertEqual(result.pages, [], "single sign-on should not ask again")
        self.assert_hospital_token(result, "beta")

    def test_non_member_is_refused_after_signing_in(self):
        _, name, pw, secret = self.user(["alpha"])
        result = self.login(self.browser(), "beta", name, pw, secret)
        self.assertIsNone(result.code)
        self.assertIn(NOT_MEMBER, result.page or "")

    def test_non_member_is_refused_through_single_sign_on(self):
        _, name, pw, secret = self.user(["alpha"])
        browser = self.browser()
        self.assert_hospital_token(
            self.login(browser, "alpha", name, pw, secret), "alpha"
        )
        result = self.login(browser, "beta", None, None, None)
        self.assertIsNone(result.code)
        self.assertIn(NOT_MEMBER, result.page or "")
        silent = self.login(browser, "beta", None, None, None, extra={"prompt": "none"})
        self.assertIsNone(silent.code)
        self.assertIsNone(silent.tokens)

    def test_password_alone_never_gets_a_code(self):
        _, name, pw, _ = self.user(["alpha"])
        result = self.login(self.browser(), "alpha", name, pw, None)
        self.assertIsNone(result.code)
        self.assertEqual(result.pages, ["username", "stopped:200"])
        self.assertIn("kc-otp-login-form", result.page)
        # Asking for the password-only level is overruled by the client's minimum.
        result = self.login(
            self.browser(), "alpha", name, pw, None, extra={"acr_values": "pwd"}
        )
        self.assertIsNone(result.code)

    def test_first_sign_in_sets_up_the_authenticator(self):
        _, name, pw, _ = self.user(["alpha"], otp=False)
        result = self.login(self.browser(), "alpha", name, pw, None)
        self.assertEqual(result.pages, ["username", "configure-otp"])
        self.assert_hospital_token(result, "alpha")
        again = self.login(self.browser(), "alpha", name, pw, result.enrolled_secret)
        self.assertEqual(again.pages, ["username", "otp"])

    def test_account_console_also_demands_the_second_factor(self):
        _, name, pw, _ = self.user(["alpha"])
        result = self.browser().login(
            "account-console", f"{URL}/realms/{self.realm}/account/", name, pw, None
        )
        self.assertIsNone(result.code)
        self.assertIn("kc-otp-login-form", result.page or "")

    # --- tokens ----------------------------------------------------------------------------

    def test_refresh_tokens_rotate_and_cannot_be_reused(self):
        _, name, pw, secret = self.user(["alpha"])
        browser = self.browser()
        first = self.login(browser, "alpha", name, pw, secret).tokens["refresh_token"]
        resp = browser.refresh(client_id_for("alpha"), first)
        self.assertEqual(resp.status, 200, resp.text)
        self.assertNotEqual(resp.json()["refresh_token"], first)
        self.assertEqual(decode_jwt(resp.json()["access_token"])["acr"], "mfa")
        self.assertEqual(browser.refresh(client_id_for("alpha"), first).status, 400)

    def test_removed_member_loses_refresh_and_sign_in_at_once(self):
        user_id, name, pw, secret = self.user(["alpha"])
        browser = self.browser()
        refresh = self.login(browser, "alpha", name, pw, secret).tokens["refresh_token"]
        remove_member(self.admin, self.ids["alpha"], user_id)
        self.assertEqual(browser.refresh(client_id_for("alpha"), refresh).status, 400)
        result = self.login(self.browser(), "alpha", name, pw, secret)
        self.assertIsNone(result.code)
        self.assertIn(NOT_MEMBER, result.page or "")

    def test_requested_scopes_cannot_add_other_hospitals(self):
        _, name, pw, secret = self.user(["alpha", "beta"])
        browser = self.browser()
        self.assert_hospital_token(
            self.login(browser, "alpha", name, pw, secret), "alpha"
        )
        for scope in (
            "openid organization:*",
            "openid organization:beta",
            "openid organization",
        ):
            # Hospital clients have no optional scopes, so Keycloak refuses the request.
            result = self.login(browser, "alpha", name, pw, secret, scope=scope)
            self.assertEqual(
                (result.error, result.code), ("invalid_scope", None), scope
            )

    # --- what the backend accepts (P1-T4, ADR-009) -----------------------------------------

    def _realm_keys(self):
        import jwt

        certs = request(
            "GET", f"{URL}/realms/{self.realm}/protocol/openid-connect/certs"
        )
        return {
            k["kid"]: jwt.PyJWK(k, algorithm="RS256")
            for k in certs.json()["keys"]
            if k.get("use") == "sig" and k.get("alg") == "RS256"
        }

    def test_real_tokens_pass_the_backend_checks_for_their_hospital_only(self):
        """The backend's own checks (ihr_identity.tokens) on tokens Keycloak really issued:
        the unit tests' minted tokens cannot drift from this shape unnoticed."""
        from ihr_identity.tokens import (
            TokenRejectedError,
            verify_access_token,
            verify_id_token,
        )

        _, name, pw, secret = self.user(["alpha"])
        result = self.login(self.browser(), "alpha", name, pw, secret)
        keys = self._realm_keys()
        expected = {
            "keys": keys,
            "issuer": f"{URL}/realms/{self.realm}",
            "now": time.time(),
        }
        alpha = {
            "client_id": "ihr-h-alpha",
            "org_id": self.ids["alpha"].org_id,
            "slug": "alpha",
        }
        beta = {
            "client_id": "ihr-h-beta",
            "org_id": self.ids["beta"].org_id,
            "slug": "beta",
        }
        access = verify_access_token(result.tokens["access_token"], **expected, **alpha)
        verify_id_token(
            result.tokens["id_token"],
            keys=keys,
            issuer=expected["issuer"],
            client_id="ihr-h-alpha",
            nonce=result.nonce,
            access=access,
            now=time.time(),
        )
        with self.assertRaises(TokenRejectedError):
            verify_access_token(result.tokens["access_token"], **expected, **beta)

    def test_back_channel_logout_ends_single_sign_on_everywhere(self):
        """What the backend does at sign-out and idle time-out (owner's decision)."""
        _, name, pw, secret = self.user(["alpha", "beta"])
        browser = self.browser()
        alpha = self.login(browser, "alpha", name, pw, secret).tokens
        beta = self.login(browser, "beta", None, None, None).tokens
        resp = request(
            "POST",
            f"{URL}/realms/{self.realm}/protocol/openid-connect/logout",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=urlencode(
                {"client_id": "ihr-h-alpha", "refresh_token": alpha["refresh_token"]}
            ),
        )
        self.assertEqual(resp.status, 204, resp.text)
        self.assertEqual(
            browser.refresh("ihr-h-beta", beta["refresh_token"]).status, 400
        )
        again = self.login(browser, "beta", None, None, None)
        self.assertIsNone(again.code)
        self.assertIn("kc-form-login", again.page or "")

    # --- provisioning safety (P1-T5a) ---------------------------------------------------

    def test_leftovers_of_another_hospital_are_never_adopted(self):
        from dataclasses import replace

        from ihr_platform.keycloak.hospital import remove_hospital

        mine = replace(hospital("zeta"), uuid=str(uuid.uuid4()))
        ids = ensure_hospital(self.admin, mine)
        org = self.admin.get(f"organizations/{ids.org_id}")
        self.assertEqual(org["attributes"]["ihr.hospital_uuid"], [mine.uuid])
        client = self.admin.get(f"clients/{ids.client_uuid}")
        self.assertEqual(client["attributes"]["ihr.hospital_uuid"], mine.uuid)
        # A resumed provisioning (no id stored yet) may reuse what it created itself...
        self.assertEqual(ensure_hospital(self.admin, mine).org_id, ids.org_id)
        # ...but a new hospital under the same slug never takes over these objects.
        other = replace(mine, uuid=str(uuid.uuid4()))
        with self.assertRaisesRegex(KeycloakError, "belongs to another hospital"):
            ensure_hospital(self.admin, other)
        with self.assertRaisesRegex(KeycloakError, "belongs to another hospital"):
            remove_hospital(self.admin, other)
        remove_hospital(self.admin, mine)
        self.assertEqual(self.admin.get("clients?clientId=ihr-h-zeta"), [])
        aliases = {
            o["alias"] for o in self.admin.get("organizations?briefRepresentation=true")
        }
        self.assertNotIn("zeta", aliases)
        flows = {f["alias"] for f in self.admin.get("authentication/flows")}
        self.assertNotIn("ihr-hospital-zeta", flows)

    # --- operator realm (P1-T5b) -----------------------------------------------------

    def test_operator_realm_console_and_operator_tokens(self):
        from ihr_identity.tokens import TokenRejectedError, verify_operator_token

        from ihr_platform.keycloak.ops import (
            OPERATOR_ROLE,
            check_ops_realm,
            ensure_ops_realm,
        )

        ops_realm = f"ihrtest_{secrets.token_hex(4)}"
        ops = KeycloakAdmin(
            URL,
            os.environ["IHR_KC_TEST_ADMIN_ID"],
            os.environ["IHR_KC_TEST_ADMIN_SECRET"],
            ops_realm,
        )
        self.addCleanup(ops.delete, f"/{ops_realm}")
        redirect, origin = (
            "http://console.localhost:8099/ops/callback/",
            "http://console.localhost:8099",
        )
        ensure_ops_realm(ops, redirect, origin)
        ensure_ops_realm(ops, redirect, origin)  # idempotent
        self.assertEqual(check_ops_realm(ops, redirect, origin), [])

        def operator(with_role):
            name, pw, secret = (
                f"op{secrets.token_hex(3)}",
                f"Pw-{secrets.token_urlsafe(16)}",
                secrets.token_hex(10),
            )
            sub = create_user(ops, name, "Op", "Erator", pw, secret)
            if with_role:
                ops.post(
                    f"users/{sub}/role-mappings/realm",
                    [ops.get(f"roles/{OPERATOR_ROLE}")],
                )
            return Browser(URL, ops_realm).login(
                "ihr-console", redirect, name, pw, secret
            )

        import jwt

        certs = request(
            "GET", f"{URL}/realms/{ops_realm}/protocol/openid-connect/certs"
        ).json()
        keys = {
            k["kid"]: jwt.PyJWK(k, algorithm="RS256")
            for k in certs["keys"]
            if k.get("use") == "sig" and k.get("alg") == "RS256"
        }
        args = {
            "keys": keys,
            "issuer": f"{URL}/realms/{ops_realm}",
            "client_id": "ihr-console",
            "role": OPERATOR_ROLE,
            "now": time.time(),
        }
        good = operator(with_role=True)
        self.assertEqual(good.pages, ["username", "otp"])
        verify_operator_token(good.tokens["access_token"], **args)
        plain = operator(with_role=False)
        with self.assertRaisesRegex(TokenRejectedError, "not an operator"):
            verify_operator_token(plain.tokens["access_token"], **args)

        # Drift: an extra scope mapping is reported, and the next sync removes it.
        client = ops.get("clients?clientId=ihr-console")[0]
        extra = ops.get("roles/offline_access")
        ops.post(f"clients/{client['id']}/scope-mappings/realm", [extra])
        self.assertIn(
            "operator realm: console client scope mappings changed",
            check_ops_realm(ops, redirect, origin),
        )
        ensure_ops_realm(ops, redirect, origin)
        self.assertEqual(check_ops_realm(ops, redirect, origin), [])

    # --- back-channel logout (P1-T5d) ---------------------------------------------------

    def test_hospital_clients_send_sign_outs_server_to_server(self):
        from dataclasses import replace

        url = "http://backend.internal/api/ihr/backchannel-logout/"
        h = replace(hospital("bclog"), backchannel_url=url)
        ids = ensure_hospital(self.admin, h)
        client = self.admin.get(f"clients/{ids.client_uuid}")
        self.assertFalse(client["frontchannelLogout"])
        attrs = client["attributes"]
        self.assertEqual(attrs["backchannel.logout.url"], url)
        self.assertEqual(attrs["backchannel.logout.session.required"], "true")
        self.assertEqual(check_hospital(self.admin, h, ids.org_id), [])
        # Drift: the back-channel address changed or the browser used instead.
        client["frontchannelLogout"] = True
        client["attributes"]["backchannel.logout.url"] = "http://evil.test/"
        self.admin.put(f"clients/{ids.client_uuid}", client)
        issues = check_hospital(self.admin, h, ids.org_id)
        self.assertIn("bclog: client frontchannelLogout is True", issues)
        self.assertIn(
            f"bclog: client attribute backchannel.logout.url is not {url!r}", issues
        )
        ensure_hospital(self.admin, h, ids.org_id)
        self.assertEqual(check_hospital(self.admin, h, ids.org_id), [])
        self._drop_hospital(ids)

    # --- the workers' narrow accounts (P1-T5f) -------------------------------------------

    def narrow(self, client_id):
        from ihr_platform.keycloak.accounts import secret_of

        secret = secret_of(self.admin, client_id)
        return KeycloakAdmin(
            URL, client_id, secret, self.realm, account_realm=self.realm
        )

    def test_each_worker_account_does_its_job_and_nothing_else(self):  # noqa: PLR0915 (one story)
        from ihr_platform.keycloak.accounts import (
            check_service_accounts,
            ensure_service_accounts,
        )

        ensure_service_accounts(self.admin)
        ensure_service_accounts(self.admin)  # repeatable
        self.assertEqual(check_service_accounts(self.admin), [])
        self.assertEqual(check_realm(self.admin, smtp=SMTP), [])
        onboarding = self.narrow("ihr-onboarding")
        staff = self.narrow("ihr-staff-worker")

        # Onboarding builds a hospital.
        h = hospital("svc")
        ids = ensure_hospital(onboarding, h)
        self.assertEqual(check_hospital(self.admin, h, ids.org_id), [])
        # The staff worker does the staff work...
        tag = str(uuid.uuid4())
        address = f"svc.{secrets.token_hex(4)}@example.test"
        sub = ensure_invited_user(staff, address, "S", "W", tag)
        ensure_member(staff, ids, sub)
        self.assertTrue(
            send_setup_email(staff, sub, tag, ids.client_id, h.redirect_uris[0], 600)
        )
        remove_member(staff, ids, sub)
        self.assertTrue(delete_pending(staff, sub, tag))
        user_id, *_ = self.user([])
        reset_sign_in(staff, user_id, ids.client_id, h.redirect_uris[0], 600)
        # ...and nothing more: no realm settings, no clients, no own roles, no other realm.
        rm = self.admin.get("clients?clientId=realm-management")[0]["id"]
        own = self.admin.get(
            f"clients/{self.admin.get('clients?clientId=ihr-staff-worker')[0]['id']}"
            "/service-account-user"
        )["id"]
        realm_admin = [
            r
            for r in self.admin.get(f"clients/{rm}/roles")
            if r["name"] == "realm-admin"
        ]
        client = self.admin.get(f"clients/{ids.client_uuid}")
        for label, call in (
            (
                "realm settings",
                lambda: staff.put(f"/{self.realm}", {"resetPasswordAllowed": False}),
            ),
            (
                "a hospital client",
                lambda: staff.put(
                    f"clients/{ids.client_uuid}",
                    dict(client, redirectUris=["https://evil.test/*"]),
                ),
            ),
            (
                "its own roles",
                lambda: staff.post(
                    f"users/{own}/role-mappings/clients/{rm}", realm_admin, ok=(204,)
                ),
            ),
            (
                "flows",
                lambda: staff.post(
                    "authentication/flows/ihr-core/copy", {"newName": "x"}
                ),
            ),
            ("another realm", lambda: staff.get("/master/users")),
        ):
            with (
                self.subTest(staff=label),
                self.assertRaisesRegex(KeycloakError, "40[13]"),
            ):
                call()
        onboarding_sa = self.admin.get(
            f"clients/{self.admin.get('clients?clientId=ihr-onboarding')[0]['id']}"
            "/service-account-user"
        )["id"]
        for label, call in (
            ("another realm", lambda: onboarding.get("/master/users")),
            (
                "its own roles",
                lambda: onboarding.post(
                    f"users/{onboarding_sa}/role-mappings/clients/{rm}",
                    realm_admin,
                    ok=(204,),
                ),
            ),
        ):
            with (
                self.subTest(onboarding=label),
                self.assertRaisesRegex(KeycloakError, "40[13]"),
            ):
                call()
        # A person made an admin with a worker's secret is reported (never unnoticed).
        from ihr_platform.keycloak.accounts import check_admin_holders

        self.assertEqual(check_admin_holders(self.admin), [])
        human, *_ = self.user([])
        manage_users = [
            r
            for r in self.admin.get(f"clients/{rm}/roles")
            if r["name"] == "manage-users"
        ]
        staff.post(f"users/{human}/role-mappings/clients/{rm}", manage_users, ok=(204,))
        held = check_admin_holders(self.admin)
        self.assertTrue(any("realm-management manage-users" in i for i in held), held)
        self.admin.call(
            "DELETE",
            f"users/{human}/role-mappings/clients/{rm}",
            body=manage_users,
            ok=(204,),
        )

        # Widening either account is drift, and the sync takes it back.
        staff_client = self.admin.get("clients?clientId=ihr-staff-worker")[0]
        self.admin.post(
            f"users/{own}/role-mappings/clients/{rm}", realm_admin, ok=(204,)
        )
        self.admin.put(
            f"clients/{staff_client['id']}", dict(staff_client, fullScopeAllowed=True)
        )
        scopes = {x["name"]: x["id"] for x in self.admin.get("client-scopes")}
        self.admin.put(
            f"clients/{staff_client['id']}/default-client-scopes/{scopes['roles']}"
        )
        issues = check_service_accounts(self.admin)
        self.assertTrue(any("holds" in i for i in issues), issues)
        self.assertTrue(any("fullScopeAllowed" in i for i in issues), issues)
        self.assertTrue(any("client scopes" in i for i in issues), issues)
        member = self.admin.get(f"clients/{ids.client_uuid}/roles/member")
        self.admin.post(
            f"users/{own}/role-mappings/clients/{ids.client_uuid}", [member], ok=(204,)
        )
        current = self.admin.get(f"clients/{staff_client['id']}")
        self.admin.put(
            f"clients/{staff_client['id']}",
            dict(current, clientAuthenticatorType="client-jwt"),
        )
        issues = check_service_accounts(self.admin)
        self.assertTrue(any("holds roles of" in i for i in issues), issues)
        self.assertTrue(any("clientAuthenticatorType" in i for i in issues), issues)
        ensure_service_accounts(self.admin)
        self.assertEqual(check_service_accounts(self.admin), [])
        self._drop_hospital(ids)

    # --- first-admin setup e-mail (P1-T5c) ----------------------------------------------

    def setup_email(self, address):
        """The newest e-mail Mailpit holds for ``address``, and the setup link in it."""
        query = urlencode({"query": f"to:{address}"})
        for _ in range(40):
            found = request("GET", f"{MAILPIT}/api/v1/search?{query}").json()
            if found["messages"]:
                break
            time.sleep(0.25)
        else:
            self.fail(f"no e-mail reached Mailpit for {address}")
        message = request(
            "GET", f"{MAILPIT}/api/v1/message/{found['messages'][0]['ID']}"
        ).json()
        links = re.findall(
            r"https?://\S+/login-actions/action-token\?\S+", message["Text"]
        )
        self.assertEqual(len(links), 1, message["Text"])
        return message, links[0]

    def test_setup_email_lets_a_new_person_set_up_and_sign_in(self):
        hospital_uuid = str(uuid.uuid4())
        address = f"first.{secrets.token_hex(4)}@alpha.example.test"
        sub = ensure_invited_user(
            self.admin, address.upper(), "First", "Admin", hospital_uuid
        )
        # Found again, never doubled (a resumed provisioning step).
        self.assertEqual(
            ensure_invited_user(self.admin, address, "First", "Admin", hospital_uuid),
            sub,
        )
        user = self.admin.get(f"users/{sub}")
        self.assertEqual((user["username"], user["email"]), (address, address))
        self.assertFalse(user["emailVerified"])
        self.assertEqual(user["attributes"][INVITED_FOR], [hospital_uuid])
        self.assertEqual(self.admin.get(f"users/{sub}/credentials"), [])
        ensure_member(self.admin, self.ids["alpha"], sub)
        # No password yet: nobody can sign in with this account.
        refused = self.login(self.browser(), "alpha", address, "", None)
        self.assertIsNone(refused.tokens)

        redirect = self.h["alpha"].redirect_uris[0]
        self.assertTrue(
            send_setup_email(
                self.admin, sub, hospital_uuid, client_id_for("alpha"), redirect, 3600
            )
        )
        message, link = self.setup_email(address)
        self.assertEqual([t["Address"] for t in message["To"]], [address])
        self.assertTrue(link.startswith(f"{URL}/realms/{self.realm}/"), link)
        self.assertNotIn("Keycloak", message["Text"])  # realm display name: IndieHR

        password = f"Pw-{secrets.token_urlsafe(16)}"
        done = self.browser().setup_account(link, password)
        self.assertEqual(
            done.pages, ["proceed", "configure-otp", "new-password", "stopped:200"]
        )
        self.assertIn("Your account has been updated", done.page or "")
        user = self.admin.get(f"users/{sub}")
        self.assertTrue(user["emailVerified"], "following the link proves the address")
        self.assertEqual(user.get("requiredActions"), [])
        # The link works once.
        again = self.browser().setup_account(link, f"Pw-{secrets.token_urlsafe(16)}")
        self.assertNotIn("new-password", again.pages)
        self.assertNotIn("configure-otp", again.pages)

        result = self.login(
            self.browser(), "alpha", address, password, done.enrolled_secret
        )
        self.assert_hospital_token(result, "alpha")
        self.assertTrue(decode_jwt(result.tokens["id_token"])["email_verified"])
        # Set up: no second e-mail, and the account is never removed as a leftover.
        self.assertFalse(
            send_setup_email(
                self.admin, sub, hospital_uuid, client_id_for("alpha"), redirect, 3600
            )
        )
        self.assertFalse(remove_invited_user(self.admin, sub, hospital_uuid))
        self.assertEqual(self.admin.get(f"users/{sub}")["id"], sub)

    # --- forgotten password (P1-T5e2) -----------------------------------------------------

    def test_forgot_password_needs_the_link_and_the_code(self):
        _, name, old, secret = self.user(["alpha"])
        address = self.admin.get(f"users?username={name}&exact=true")[0]["email"]
        redirect = self.h["alpha"].redirect_uris[0]
        page = self.browser().forgot_password(client_id_for("alpha"), redirect, name)
        self.assertIn("You should receive an email", page)
        _, link = self.setup_email(address)
        # The link alone: a wrong code changes nothing.
        stuck = self.browser().follow_reset(
            link, "0" * 20, "Pw-never-set-123", redirect
        )
        self.assertNotIn("new-password", stuck.pages)
        new = f"Pw-{secrets.token_urlsafe(16)}"
        done = self.browser().follow_reset(link, secret, new, redirect)
        # Opened in another browser than the one that asked: Keycloak ends on its own
        # "account updated" page (in the same browser it returns to the app, password-level
        # only, which hospitals refuse: their tokens need acr=mfa).
        self.assertEqual(done.pages[:2], ["otp", "new-password"])
        self.assertTrue(
            (done.landed or "").startswith(redirect)
            or "Your account has been updated" in (done.page or "")
        )
        result = self.login(self.browser(), "alpha", name, new, secret)
        self.assert_hospital_token(result, "alpha")
        stale = self.login(self.browser(), "alpha", name, old, secret)
        self.assertIsNone(stale.tokens)

    def test_forgot_password_is_refused_without_an_authenticator(self):
        """An account with no authenticator (an admin's reset in progress, an invitation
        never set up) cannot be taken over with the mailbox alone through "Forgot
        password?"; only the admin's own time-limited link sets it up."""
        user_id, name, _, _ = self.user(["alpha"], otp=False)
        address = self.admin.get(f"users/{user_id}")["email"]
        redirect = self.h["alpha"].redirect_uris[0]
        self.browser().forgot_password(client_id_for("alpha"), redirect, name)
        _, link = self.setup_email(address)
        tried = self.browser().follow_reset(
            link, None, f"Pw-{secrets.token_urlsafe(16)}", redirect
        )
        self.assertNotIn("new-password", tried.pages)
        self.assertNotIn("configure-otp", tried.pages)
        self.assertEqual(
            [c["type"] for c in self.admin.get(f"users/{user_id}/credentials")],
            ["password"],
        )

    # --- an admin's reset of a lost authenticator (P1-T5e2) -------------------------------

    def test_reset_clears_both_factors_ends_sign_ins_and_mails_a_link(self):
        user_id, name, old, secret = self.user(["alpha", "beta"])
        address = self.admin.get(f"users/{user_id}")["email"]
        signed = self.login(self.browser(), "alpha", name, old, secret)
        self.assert_hospital_token(signed, "alpha")
        redirect = self.h["alpha"].redirect_uris[0]

        stamped = self.admin.get(f"users/{user_id}")
        stamped["attributes"] = {
            **(stamped.get("attributes") or {}),
            INVITED_FOR: ["h"],
        }
        self.admin.put(f"users/{user_id}", stamped)
        reset_sign_in(self.admin, user_id, client_id_for("alpha"), redirect, 3600)
        self.assertEqual(self.admin.get(f"users/{user_id}/credentials"), [])
        after = self.admin.get(f"users/{user_id}")
        self.assertNotIn(
            INVITED_FOR, after.get("attributes") or {}
        )  # no hospital's now
        self.assertTrue(after["emailVerified"])
        refreshed = self.browser().refresh(
            client_id_for("alpha"), signed.tokens["refresh_token"]
        )
        self.assertEqual(refreshed.status, 400)  # every sign-in ended
        # The old password (and code) no longer get anyone in, at any hospital.
        self.assertIsNone(self.login(self.browser(), "beta", name, old, secret).tokens)

        _, link = self.setup_email(address)
        new = f"Pw-{secrets.token_urlsafe(16)}"
        done = self.browser().setup_account(link, new)
        self.assertIn("configure-otp", done.pages)
        self.assertIn("new-password", done.pages)
        self.assertNotEqual(done.enrolled_secret, secret)
        self.assert_hospital_token(
            self.login(self.browser(), "beta", name, new, done.enrolled_secret), "beta"
        )
        self.assertIsNone(self.login(self.browser(), "alpha", name, new, secret).tokens)

    def test_reset_refuses_an_unverified_address(self):
        user_id, *_ = self.user([])
        user = self.admin.get(f"users/{user_id}")
        user["emailVerified"] = False
        self.admin.put(f"users/{user_id}", user)
        with self.assertRaisesRegex(KeycloakError, "not verified"):
            reset_sign_in(self.admin, user_id, client_id_for("alpha"), "x", 60)
        self.assertNotEqual(self.admin.get(f"users/{user_id}/credentials"), [])

    def test_sending_again_replaces_the_account_so_older_links_die(self):
        """Keycloak keeps a sent link valid whatever happens to the account's sessions,
        so a resend replaces the unused account: the old link names an account that no
        longer exists."""
        tag = str(uuid.uuid4())
        address = f"again.{secrets.token_hex(4)}@alpha.example.test"
        redirect = self.h["alpha"].redirect_uris[0]
        old = ensure_invited_user(self.admin, address, "A", "B", tag)
        send_setup_email(self.admin, old, tag, client_id_for("alpha"), redirect, 3600)
        _, old_link = self.setup_email(address)
        self.assertEqual(account_state(self.admin, old, tag), "pending")
        self.assertEqual(find_invited_by_email(self.admin, address, tag), old)

        self.assertTrue(delete_pending(self.admin, old, tag))
        self.assertEqual(account_state(self.admin, old, tag), "missing")
        new = ensure_invited_user(self.admin, address, "A", "B", tag)
        self.assertNotEqual(new, old)
        ensure_member(self.admin, self.ids["alpha"], new)
        time.sleep(1)  # Mailpit orders by arrival
        send_setup_email(self.admin, new, tag, client_id_for("alpha"), redirect, 3600)
        _, new_link = self.setup_email(address)
        self.assertNotEqual(new_link, old_link)

        stale = self.browser().setup_account(
            old_link, f"Pw-{secrets.token_urlsafe(16)}"
        )
        self.assertNotIn("new-password", stale.pages)
        self.assertNotIn("configure-otp", stale.pages)
        self.assertEqual(account_state(self.admin, new, tag), "pending")
        password = f"Pw-{secrets.token_urlsafe(16)}"
        done = self.browser().setup_account(new_link, password)
        self.assertIn("new-password", done.pages)
        self.assertEqual(account_state(self.admin, new, tag), "set_up")
        # A set-up account is never replaced.
        self.assertFalse(delete_pending(self.admin, new, tag))
        self.assert_hospital_token(
            self.login(
                self.browser(), "alpha", address, password, done.enrolled_secret
            ),
            "alpha",
        )

    def test_an_account_the_stamp_did_not_stick_to_is_never_left_behind(self):
        profile = self.admin.get("users/profile")
        profile["attributes"] = [
            a for a in profile["attributes"] if a["name"] != INVITED_FOR
        ]
        self.admin.put("users/profile", profile, ok=(200,))
        try:
            address = f"nostamp.{secrets.token_hex(4)}@example.test"
            with self.assertRaisesRegex(KeycloakError, "did not keep"):
                ensure_invited_user(self.admin, address, "N", "S", str(uuid.uuid4()))
            self.assertEqual(self.admin.get(f"users?email={address}&exact=true"), [])
        finally:
            ensure_realm(self.admin, smtp=SMTP)

    def test_existing_accounts_are_never_adopted_or_removed(self):
        user_id, _, _, _ = self.user(["beta"])
        existing = self.admin.get(f"users/{user_id}")["email"]
        for address in (existing, existing.upper()):
            with self.assertRaises(ExistingAccountError):
                ensure_invited_user(self.admin, address, "X", "Y", str(uuid.uuid4()))
        # An account made for one hospital is neither adopted nor removed by another.
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        address = f"twice.{secrets.token_hex(4)}@example.test"
        sub = ensure_invited_user(self.admin, address, "T", "W", first)
        with self.assertRaises(ExistingAccountError):
            ensure_invited_user(self.admin, address, "T", "W", second)
        with self.assertRaises(ExistingAccountError):
            ensure_invited_user(self.admin, address, "T", "W", second, sub)
        self.assertFalse(remove_invited_user(self.admin, sub, second))
        self.assertFalse(
            send_setup_email(self.admin, sub, second, client_id_for("alpha"), "x", 3600)
        )
        self.assertFalse(remove_invited_user(self.admin, user_id, first))
        # Its own hospital removes it while it has never been set up.
        self.assertTrue(remove_invited_user(self.admin, sub, first))
        self.assertTrue(remove_invited_user(self.admin, sub, first))

    def test_mail_server_and_invitation_stamp_are_drift_checked(self):
        self.assertEqual(check_realm(self.admin, smtp=SMTP), [])
        realm = self.admin.get(f"/{self.realm}")
        realm["smtpServer"] = {**realm["smtpServer"], "host": "mail.elsewhere.test"}
        self.admin.put(f"/{self.realm}", realm)
        profile = self.admin.get("users/profile")
        stamp = next(a for a in profile["attributes"] if a["name"] == INVITED_FOR)
        stamp["permissions"] = {"view": ["admin", "user"], "edit": ["admin", "user"]}
        self.admin.put("users/profile", profile, ok=(200,))
        issues = check_realm(self.admin, smtp=SMTP)
        self.assertIn(f"realm mail server host is not {SMTP['host']!r}", issues)
        self.assertIn(f"user attribute {INVITED_FOR} is not admin-only", issues)
        ensure_realm(self.admin, smtp=SMTP)
        self.assertEqual(check_realm(self.admin, smtp=SMTP), [])

    # --- the gates fail closed -------------------------------------------------------------

    def _scratch_client(self, **overrides):
        client_id = f"scratch-{secrets.token_hex(3)}"
        rep = {
            "clientId": client_id,
            "publicClient": True,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": False,
            "redirectUris": ["http://scratch.localhost:8099/cb"],
            "attributes": {"pkce.code.challenge.method": "S256"},
            **overrides,
        }
        uuid = self.admin.created_id(self.admin.post("clients", rep))
        self.addCleanup(self.admin.delete, f"clients/{uuid}")
        return client_id, uuid

    def test_unconfigured_template_refuses_everyone(self):
        flows = {f["alias"]: f["id"] for f in self.admin.get("authentication/flows")}
        client_id, _ = self._scratch_client(
            authenticationFlowBindingOverrides={"browser": flows[FLOW_TEMPLATE]}
        )
        _, name, pw, secret = self.user(["alpha"])
        result = self.browser().login(
            client_id, "http://scratch.localhost:8099/cb", name, pw, secret
        )
        self.assertIsNone(result.code)
        self.assertIn(NOT_MEMBER, result.page or "")

    def test_hospital_client_that_lost_its_flow_refuses_everyone(self):
        client_id, uuid = self._scratch_client(
            defaultClientScopes=["basic", HOSPITAL_SCOPE]
        )
        scopes = {
            s["name"] for s in self.admin.get(f"clients/{uuid}/default-client-scopes")
        }
        self.assertIn(HOSPITAL_SCOPE, scopes)
        self.assertIn(
            f"client {client_id} is neither built in nor a hospital client",
            check_realm(self.admin, smtp=SMTP),
        )
        _, name, pw, secret = self.user(["alpha"])
        result = self.browser().login(
            client_id, "http://scratch.localhost:8099/cb", name, pw, secret
        )
        self.assertIsNone(result.code)
        self.assertIn("sign-in is not set up", result.page or "")

    def test_drift_check_catches_a_gate_that_would_fail_open(self):
        h = hospital("gamma")
        ids = ensure_hospital(self.admin, h)
        self.addCleanup(self._drop_hospital, ids)
        self.assertEqual(check_hospital(self.admin, h, ids.org_id), [])
        # Without the role, Keycloak's role condition stops matching and the deny is skipped.
        self.admin.delete(f"clients/{ids.client_uuid}/roles/member")
        issues = check_hospital(self.admin, h, ids.org_id)
        self.assertTrue(any("member is missing" in i for i in issues), issues)
        ensure_hospital(self.admin, h, ids.org_id)
        self.assertEqual(check_hospital(self.admin, h, ids.org_id), [])

    def test_sync_detects_and_removes_quiet_widening(self):
        h = hospital("delta")
        ids = ensure_hospital(self.admin, h)
        self.addCleanup(self._drop_hospital, ids)
        client = self.admin.get(f"clients/{ids.client_uuid}")
        client["attributes"]["access.token.lifespan"] = "86400"
        self.admin.put(f"clients/{ids.client_uuid}", client)
        beta_member = self.admin.get(
            f"clients/{self.ids['beta'].client_uuid}/roles/member"
        )
        self.admin.post(
            f"clients/{ids.client_uuid}/scope-mappings/clients/{self.ids['beta'].client_uuid}",
            [beta_member],
        )
        group = self.admin.created_id(
            self.admin.post("groups", {"name": f"g{secrets.token_hex(3)}"})
        )
        self.addCleanup(self.admin.delete, f"groups/{group}")
        member = self.admin.get(f"clients/{ids.client_uuid}/roles/member")
        self.admin.post(
            f"groups/{group}/role-mappings/clients/{ids.client_uuid}", [member]
        )
        issues = " | ".join(check_hospital(self.admin, h, ids.org_id))
        for text in ("access.token.lifespan", "scope mappings", "a group grants"):
            self.assertIn(text, issues)
        # A person who gets `member` through the group is not removed silently.
        user_id, *_ = self.user([])
        ensure_member(self.admin, ids, user_id)
        self.admin.put(f"users/{user_id}/groups/{group}")
        with self.assertRaisesRegex(KeycloakError, "through a group"):
            remove_member(self.admin, ids, user_id)
        self.admin.call(
            "DELETE",
            f"groups/{group}/role-mappings/clients/{ids.client_uuid}",
            body=[member],
        )
        ensure_hospital(self.admin, h, ids.org_id)
        self.assertEqual(check_hospital(self.admin, h, ids.org_id), [])

    def test_rebuild_flow_replaces_the_flow_and_keeps_the_gate(self):
        h = hospital("epsilon")
        ids = ensure_hospital(self.admin, h)
        self.addCleanup(self._drop_hospital, ids)
        old = {f["alias"]: f["id"] for f in self.admin.get("authentication/flows")}
        self.assertEqual(rebuild_flow(self.admin, h, ids.org_id), ids)
        new = {f["alias"]: f["id"] for f in self.admin.get("authentication/flows")}
        self.assertNotEqual(old[ids.flow_alias], new[ids.flow_alias])
        self.assertEqual(check_hospital(self.admin, h, ids.org_id), [])
        _, name, pw, secret = self.user([])
        result = self.browser().login(
            ids.client_id, h.redirect_uris[0], name, pw, secret
        )
        self.assertIn(NOT_MEMBER, result.page or "")

    def test_half_built_core_flow_is_reported_and_rebuilt(self):
        execs = self.admin.get("authentication/flows/ihr-core/executions")
        otp = next(e for e in execs if e.get("providerId") == "auth-otp-form")
        self.admin.delete(f"authentication/executions/{otp['id']}")
        self.assertIn(
            "flow ihr-core is not password + OTP as built",
            check_realm(self.admin, smtp=SMTP),
        )
        ensure_realm(self.admin, smtp=SMTP)
        self.assertEqual(check_realm(self.admin, smtp=SMTP), [])

    def _drop_hospital(self, ids):
        self.admin.delete(f"clients/{ids.client_uuid}")
        flows = {f["alias"]: f["id"] for f in self.admin.get("authentication/flows")}
        self.admin.delete(f"authentication/flows/{flows[ids.flow_alias]}")
        self.admin.delete(f"organizations/{ids.org_id}")

    def test_baseline_is_clean_and_provisioning_repeatable(self):
        self.assertEqual(check_realm(self.admin, smtp=SMTP), [])
        again = ensure_hospital(self.admin, self.h["alpha"], self.ids["alpha"].org_id)
        self.assertEqual(again, self.ids["alpha"])
        for slug, h in self.h.items():
            self.assertEqual(check_hospital(self.admin, h, self.ids[slug].org_id), [])
        self.assertEqual(ensure_realm(self.admin, smtp=SMTP), [])
        flows = [f["alias"] for f in self.admin.get("authentication/flows")]
        self.assertEqual(flows.count("ihr-hospital-alpha"), 1)

    # --- passwords and lock-out ------------------------------------------------------------

    def test_password_policy(self):
        user_id, name, _, _ = self.user([])

        def set_password(value):
            return self.admin.call(
                "PUT",
                f"users/{user_id}/reset-password",
                body={"type": "password", "value": value, "temporary": False},
                ok=(204, 400),
            )

        for weak in (
            "Short-pw-1",
            "qwertyuiopasdfgh",
            "QwertyUiopAsdfgh",
            f"x{name}x-Long-123",
        ):
            self.assertEqual(set_password(weak).status, 400, weak)
        self.assertEqual(set_password(f"Fine-{secrets.token_urlsafe(12)}").status, 204)

    def test_repeated_failures_lock_the_account(self):
        user_id, name, pw, secret = self.user(["alpha"])
        status = f"attack-detection/brute-force/users/{user_id}"
        for attempt in range(1, 6):
            # More than 1 s apart, so the quick-retry rule is not what locks it.
            time.sleep(1.2)
            self.login(self.browser(), "alpha", name, "wrong-password-123", secret)
            if attempt == 4:
                self.assertFalse(self.admin.get(status)["disabled"], "locked too early")
        locked = self.admin.get(status)
        self.assertTrue(locked["disabled"], locked)
        result = self.login(self.browser(), "alpha", name, pw, secret)
        self.assertIsNone(result.code)

    def test_sign_in_errors_do_not_reveal_accounts(self):
        _, name, _, secret = self.user(["alpha"])
        wrong = self.login(self.browser(), "alpha", name, "wrong-password-123", secret)
        unknown = self.login(
            self.browser(), "alpha", f"nobody{secrets.token_hex(3)}", "x" * 14, None
        )
        message = re.compile(r'kc-feedback-text">\s*([^<]+?)\s*<', re.DOTALL)
        self.assertEqual(message.findall(wrong.page), message.findall(unknown.page))
        self.assertTrue(message.findall(wrong.page))

    # --- other doors are shut --------------------------------------------------------------

    def test_password_grants_are_refused(self):
        # No authenticator: only the realm's ihr-deny-all flow can refuse admin-cli's grant.
        _, name, pw, _ = self.user(["alpha"], otp=False)
        for client in (client_id_for("alpha"), "admin-cli"):
            resp = request(
                "POST",
                f"{URL}/realms/{self.realm}/protocol/openid-connect/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=urlencode(
                    {
                        "grant_type": "password",
                        "client_id": client,
                        "username": name,
                        "password": pw,
                    }
                ),
            )
            self.assertIn(resp.status, (400, 401), client)
            self.assertNotIn("access_token", resp.text, client)

    def test_self_registration_and_client_registration_are_off(self):
        page = request(
            "GET",
            f"{URL}/realms/{self.realm}/protocol/openid-connect/registrations?"
            + urlencode(
                {
                    "client_id": client_id_for("alpha"),
                    "response_type": "code",
                    "scope": "openid",
                    "redirect_uri": self.h["alpha"].redirect_uris[0],
                }
            ),
        )
        self.assertNotIn("kc-register-form", page.text)
        reg = request(
            "POST",
            f"{URL}/realms/{self.realm}/clients-registrations/openid-connect",
            headers={"Content-Type": "application/json"},
            body='{"redirect_uris": ["http://evil.localhost/cb"]}',
        )
        self.assertIn(reg.status, (401, 403), reg.text)

    def test_admin_events_hold_no_secrets(self):
        _, _, pw, secret = self.user(["alpha"])
        time.sleep(1)
        events = self.admin.get("admin-events?first=0&max=200")
        self.assertTrue(events, "admin events should be recorded")
        dump = repr(events)
        self.assertNotIn(pw, dump)
        self.assertNotIn(secret, dump)


if __name__ == "__main__":
    unittest.main()
