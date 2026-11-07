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
from ihr_platform.keycloak.kc_http import KeycloakAdmin, KeycloakError, request
from ihr_platform.keycloak.realm import (
    FLOW_TEMPLATE,
    HOSPITAL_SCOPE,
    check_realm,
    ensure_realm,
)
from ihr_platform.keycloak.users import create_user

URL = os.environ.get("IHR_KC_TEST_URL")
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
        ensure_realm(cls.admin)
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
            check_realm(self.admin),
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
            "flow ihr-core is not password + OTP as built", check_realm(self.admin)
        )
        ensure_realm(self.admin)
        self.assertEqual(check_realm(self.admin), [])

    def _drop_hospital(self, ids):
        self.admin.delete(f"clients/{ids.client_uuid}")
        flows = {f["alias"]: f["id"] for f in self.admin.get("authentication/flows")}
        self.admin.delete(f"authentication/flows/{flows[ids.flow_alias]}")
        self.admin.delete(f"organizations/{ids.org_id}")

    def test_baseline_is_clean_and_provisioning_repeatable(self):
        self.assertEqual(check_realm(self.admin), [])
        again = ensure_hospital(self.admin, self.h["alpha"], self.ids["alpha"].org_id)
        self.assertEqual(again, self.ids["alpha"])
        for slug, h in self.h.items():
            self.assertEqual(check_hospital(self.admin, h, self.ids[slug].org_id), [])
        self.assertEqual(ensure_realm(self.admin), [])
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
