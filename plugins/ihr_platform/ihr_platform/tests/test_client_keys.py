"""Client keys and signed client authentication without a Keycloak (P1-T8d2, ADR-012).

The live behaviour (Keycloak accepting and refusing assertions, rotation, the cut-over) is in
keycloak/tests/test_live.py. Every key here is made at run time in a temporary folder: a private
key is never committed.
"""

import io
import json
import shutil
import stat
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import jwt
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from django_tenants.utils import schema_context
from ihr_identity import client_auth, oidc

from ihr_platform import client_keys
from ihr_platform.client_keys import ClientKeyError, KeyStore
from ihr_platform.keycloak import accounts, hospital, ops, realm
from ihr_platform.keycloak.kc_http import KeycloakAdmin
from ihr_platform.settings import secure

PROBE = "ihr_platform.management.commands.ihr_client_keys"


class TempKeys:
    def make_dir(self):
        root = tempfile.mkdtemp(prefix="ihr-keys-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return root

    def store(self, purpose="backend"):
        store = KeyStore(Path(self.make_dir()) / purpose)
        store.ensure(purpose)
        return store


class KeyStoreTests(TempKeys, SimpleTestCase):
    def test_a_key_is_made_once_with_a_readable_kid_and_private_files(self):
        store = KeyStore(Path(self.make_dir()) / "keys")
        self.assertIsNone(store.purpose())
        kid, made = store.ensure("backend")
        self.assertTrue(made)
        self.assertEqual(store.ensure("backend"), (kid, False))
        self.assertRegex(kid, r"^backend-[A-Za-z0-9_-]{16}$")
        jwk = json.loads(store.public_jwks())["keys"][0]
        self.assertEqual(kid, f"backend-{client_keys.thumbprint(jwk)[:16]}")
        for path in store.dir.iterdir():
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600, path.name)
        self.assertEqual(stat.S_IMODE(store.dir.stat().st_mode), 0o700)
        with self.assertRaisesMessage(ClientKeyError, "holds the backend key"):
            store.ensure("staff")

    def test_the_rfc_7638_thumbprint(self):
        # RFC 7638 has no EC example; this is the members-in-order rule on a fixed key.
        jwk = {"crv": "P-256", "kty": "EC", "x": "AAAA", "y": "BBBB", "kid": "x"}
        import base64
        import hashlib

        want = base64.urlsafe_b64encode(
            hashlib.sha256(b'{"crv":"P-256","kty":"EC","x":"AAAA","y":"BBBB"}').digest()
        ).rstrip(b"=")
        self.assertEqual(client_keys.thumbprint(jwk), want.decode())

    def test_files_others_could_read_are_refused(self):
        store = self.store()
        kid = store.active_kid()
        pem = store.dir / f"{kid}.pem"
        pem.chmod(0o640)
        fresh = KeyStore(store.dir)
        with self.assertRaisesMessage(ClientKeyError, "must be 600"):
            fresh.check()
        pem.chmod(0o600)
        (store.dir / "state.json").chmod(0o644)
        with self.assertRaisesMessage(ClientKeyError, "must be 600"):
            KeyStore(store.dir).purpose()

    def test_a_swapped_key_file_is_refused(self):
        store = self.store()
        other = self.store()
        kid = store.active_kid()
        shutil.copy(
            other.dir / f"{other.active_kid()}.pem",
            store.dir / f"{kid}.pem",
        )
        with self.assertRaisesMessage(ClientKeyError, f"does not hold key {kid}"):
            KeyStore(store.dir).check()

    def test_a_compromise_never_reuses_a_staged_key(self):
        # A staged key left by a stopped rotation sat beside the stolen one: --fresh drops it
        store = self.store()
        left = store.stage()
        fresh = store.stage(fresh=True)
        self.assertNotEqual(fresh, left)
        self.assertNotIn(
            left, {k["kid"] for k in json.loads(store.public_jwks())["keys"]}
        )
        self.assertFalse((store.dir / f"{left}.pem").exists())

    def test_rotation_publishes_both_then_only_the_new_key(self):
        store = self.store()
        old = store.active_kid()
        new = store.stage()
        self.assertNotEqual(new, old)
        self.assertEqual(store.stage(), new)  # a stopped rotation can be run again

        def published():
            return {k["kid"] for k in json.loads(store.public_jwks())["keys"]}

        self.assertEqual(published(), {old, new})
        self.assertEqual(store.active_kid(), old)  # staged keys never sign
        with self.assertRaisesMessage(ClientKeyError, "is the active key"):
            store.retire(old)
        self.assertEqual(store.activate(), new)
        self.assertEqual(store.active_kid(), new)
        self.assertEqual(published(), {old, new})  # the old one until it is retired
        self.assertEqual(store.retire(), [old])
        self.assertEqual(published(), {new})
        self.assertTrue((store.dir / f"{old}.pem").exists())
        self.assertEqual(store.delete(), [old])
        self.assertFalse((store.dir / f"{old}.pem").exists())
        self.assertEqual([k["kid"] for k in store.state()["keys"]], [new])
        # Every rotation makes a new key with a new kid (Keycloak caches keys by kid).
        third = store.stage()
        self.assertNotIn(third, {old, new})

    def test_a_suspected_leak_stops_publishing_the_old_key_at_once(self):
        store = self.store()
        old = store.active_kid()
        new = store.stage()
        store.activate(new, at_once=True)
        self.assertEqual(store.kids(client_keys.RETIRED), [old])
        self.assertEqual(
            [k["kid"] for k in json.loads(store.public_jwks())["keys"]], [new]
        )

    def test_a_signer_follows_a_switch_made_by_another_process(self):
        store = self.store()
        signer = KeyStore(store.dir)  # e.g. the backend, while the key tool rotates
        first = jwt.get_unverified_header(signer.sign("ihr-console", "https://a/r"))
        new = store.stage()
        store.activate(new)
        second = jwt.get_unverified_header(signer.sign("ihr-console", "https://a/r"))
        self.assertNotEqual(first["kid"], new)
        self.assertEqual(second["kid"], new)

    def test_public_output_never_holds_a_private_part(self):
        store = self.store()
        store.stage()
        for key in json.loads(store.public_jwks())["keys"]:
            self.assertEqual(set(key), {"kty", "crv", "x", "y", "kid", "use", "alg"})
            self.assertEqual((key["use"], key["alg"]), ("sig", "ES256"))
        private = json.loads(store.public_jwks())
        private["keys"][0]["d"] = "AAAA"
        with self.assertRaisesMessage(ClientKeyError, "private parts are refused"):
            client_keys.parse_jwks(json.dumps(private))

    def test_published_sets_compare_by_their_keys(self):
        store = self.store()
        store.stage()
        published = store.public_jwks()
        keys = json.loads(published)["keys"]
        shuffled = json.dumps({"keys": list(reversed(keys))}, indent=2)
        self.assertTrue(client_keys.same_keys(shuffled, published))
        self.assertFalse(
            client_keys.same_keys(json.dumps({"keys": keys[:1]}), published)
        )
        self.assertFalse(client_keys.same_keys("", published))
        no_use = [dict(keys[0]), keys[1]]
        no_use[0].pop("use")  # Keycloak would silently skip it
        self.assertFalse(client_keys.same_keys(json.dumps({"keys": no_use}), published))

    def test_age_verdicts(self):
        store = self.store()
        day = 86400
        made = time.time()
        for days, verdict in ((0, "ok"), (149, "ok"), (150, "WARN"), (365, "FAIL")):
            with self.subTest(days=days):
                status = store.status(now=made + days * day + 60)
                self.assertEqual(status["verdict"], verdict, status["message"])

    def test_the_key_tool_prints_kids_and_public_keys_only(self):
        root = self.make_dir()
        folder = str(Path(root) / "staff")

        said = []

        def tool(*args):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = client_keys.main(
                    [args[0], "--purpose", "staff", "--dir", folder, *args[1:]]
                )
            said.extend((out.getvalue(), err.getvalue()))
            return code, out.getvalue(), err.getvalue()

        code, out, _ = tool("ensure")
        kid = out.strip()
        self.assertEqual((code, kid), (0, KeyStore(folder).active_kid()))
        code, out, _ = tool("stage")
        new = out.strip()
        code, out, _ = tool("status")
        lines = sorted(line.split() for line in out.splitlines())
        self.assertEqual(
            [(x[0], x[1], x[3]) for x in lines],
            sorted([(kid, "active", "0"), (new, "staged", "0")]),
        )
        self.assertTrue(lines[0][2].endswith("+05:30"))  # shown in IST
        _, out, _ = tool("public")
        client_keys.parse_jwks(out)
        self.assertEqual(tool("activate", "--kid", new)[1].strip(), new)
        self.assertEqual(tool("retire", "--kid", kid)[1].strip(), kid)
        self.assertEqual(tool("delete", "--kid", kid)[1].strip(), kid)
        code, _, err = tool("activate", "--kid", kid)
        self.assertEqual(code, 2)
        self.assertIn("not a staged key", err)
        # Nothing the tool printed holds any part of a private key.
        printed = "".join(said)
        for pem in Path(folder).glob("*.pem"):
            for line in pem.read_text().splitlines()[1:-1]:
                self.assertNotIn(line, printed)


class AssertionTests(TempKeys, SimpleTestCase):
    def test_assertion_claims(self):
        store = self.store()
        kid = store.active_kid()
        token = store.sign("ihr-h-alpha", "https://auth.test/realms/indiehr")
        self.assertEqual(
            jwt.get_unverified_header(token), {"alg": "ES256", "kid": kid, "typ": "JWT"}
        )
        claims = jwt.decode(
            token,
            store.private_key(kid).public_key(),
            algorithms=["ES256"],
            audience="https://auth.test/realms/indiehr",
        )
        self.assertEqual(claims["iss"], "ihr-h-alpha")
        self.assertEqual(claims["sub"], "ihr-h-alpha")
        self.assertEqual(claims["exp"] - claims["iat"], 30)
        self.assertLessEqual(abs(claims["iat"] - time.time()), 2)
        self.assertGreaterEqual(len(claims["jti"]), 32)
        jtis = {
            jwt.decode(store.sign("c", "a"), options={"verify_signature": False})["jti"]
            for _ in range(50)
        }
        self.assertEqual(len(jtis), 50)

    def test_the_backend_signs_for_the_public_issuer_not_the_back_channel(self):
        fields = client_auth.assertion_fields("ihr-h-testhosp")
        self.assertEqual(fields["client_assertion_type"], client_keys.ASSERTION_TYPE)
        claims = jwt.decode(
            fields["client_assertion"], options={"verify_signature": False}
        )
        self.assertEqual(
            claims["aud"], f"{settings.IHR_KEYCLOAK_PUBLIC_URL}/realms/indiehr"
        )
        self.assertNotIn(settings.IHR_KEYCLOAK_URL, claims["aud"])
        ops_claims = jwt.decode(
            client_auth.assertion_fields("ihr-console", "indiehr-ops")[
                "client_assertion"
            ],
            options={"verify_signature": False},
        )
        self.assertEqual(ops_claims["aud"], oidc.issuer("indiehr-ops"))


class BackChannelTests(SimpleTestCase):
    """Every token and logout call of the backend carries its signed assertion."""

    def _post(self, call, *args, **kwargs):
        response = mock.Mock(status_code=200, content=b"{}")
        response.json.return_value = {}
        with mock.patch.object(oidc.requests, "post", return_value=response) as post:
            call(*args, **kwargs)
        return post.call_args

    def test_hospital_and_console_calls_are_signed(self):
        for label, call, args, kwargs, client, realm_name in (
            ("exchange", oidc.exchange_code, ("c", "v", "https://x/cb"), {"client": "ihr-h-alpha"}, "ihr-h-alpha", "indiehr"),
            ("refresh", oidc.refresh, ("r",), {"client": "ihr-h-alpha"}, "ihr-h-alpha", "indiehr"),
            ("logout", oidc.end_keycloak_session, ("r",), {"client": "ihr-h-alpha"}, "ihr-h-alpha", "indiehr"),
            ("console refresh", oidc.refresh, ("r",), {"realm": "indiehr-ops", "client": "ihr-console"}, "ihr-console", "indiehr-ops"),
        ):  # fmt: skip
            with self.subTest(label):
                sent = self._post(call, *args, **kwargs)
                data = sent.kwargs["data"]
                self.assertTrue(
                    sent.args[0].startswith(
                        f"{settings.IHR_KEYCLOAK_URL}/realms/{realm_name}/"
                    )
                )
                self.assertEqual(data["client_id"], client)
                self.assertNotIn("client_secret", data)
                from ihr_identity.testing import check_assertion

                claims = check_assertion(data, realm_name)
                self.assertEqual(claims["aud"], oidc.issuer(realm_name))

    def test_no_key_means_keycloak_is_unavailable_not_an_unsigned_call(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty)
        with (
            override_settings(IHR_CLIENT_KEY_DIR=empty),
            mock.patch.object(oidc.requests, "post") as post,
            self.assertRaises(oidc.KeycloakUnavailableError),
            self.assertLogs("ihr_identity.oidc", "ERROR"),
        ):
            oidc.refresh("r", client="ihr-h-alpha")
        post.assert_not_called()


class KeycloakAdminTests(TempKeys, SimpleTestCase):
    def test_service_accounts_send_their_assertion_and_no_secret(self):
        store = self.store("staff")
        admin = KeycloakAdmin(
            "http://kc.test",
            "ihr-staff-worker",
            None,
            "indiehr",
            account_realm="indiehr",
            client_auth=lambda: store.fields(
                "ihr-staff-worker", "https://a/realms/indiehr"
            ),
        )
        reply = mock.Mock(status=200)
        reply.json.return_value = {"access_token": "t", "expires_in": 60}
        with mock.patch(
            "ihr_platform.keycloak.kc_http.request", return_value=reply
        ) as req:
            admin._access_token()
        body = dict(
            pair.split("=", 1) for pair in req.call_args.kwargs["body"].split("&")
        )
        self.assertEqual(body["client_id"], "ihr-staff-worker")
        self.assertIn("client_assertion", body)
        self.assertNotIn("client_secret", body)

    def test_either_a_secret_or_client_auth(self):
        for secret, auth in ((None, None), ("s", dict)):
            with self.subTest(secret=secret), self.assertRaises(ValueError):
                KeycloakAdmin("http://kc.test", "c", secret, "r", client_auth=auth)


class ClientRepresentationTests(TempKeys, SimpleTestCase):
    """What the sync writes, and what its drift check flags (hospital, console, accounts)."""

    def setUp(self):
        self.backend = self.store("backend")
        self.jwks = self.backend.public_jwks()
        self.h = hospital.HospitalLogin(
            "alpha",
            "Alpha",
            ("https://alpha.x",),
            ("https://alpha.x/cb",),
            jwks=self.jwks,
        )

    def test_every_client_is_confidential_with_pinned_keys(self):
        reps = {
            "hospital": hospital._client_rep(self.h, "org", None, enabled=True),
            "console": ops.console_rep("https://c/cb", "https://c", self.jwks),
            "account": accounts._client_rep("ihr-staff-worker", self.jwks),
        }
        for label, rep in reps.items():
            with self.subTest(label):
                self.assertFalse(rep["publicClient"])
                self.assertEqual(rep["clientAuthenticatorType"], "client-jwt")
                attrs = rep["attributes"]
                self.assertEqual(attrs["use.jwks.string"], "true")
                self.assertEqual(attrs["jwks.string"], self.jwks)
                self.assertEqual(
                    (attrs["use.jwks.url"], attrs["jwks.url"]), ("false", "")
                )
                self.assertEqual(attrs["token.endpoint.auth.signing.alg"], "ES256")
                self.assertEqual(attrs["token.endpoint.auth.signing.max.exp"], "60")
        self.assertEqual(
            reps["hospital"]["attributes"]["pkce.code.challenge.method"], "S256"
        )
        self.assertEqual(
            reps["console"]["attributes"]["pkce.code.challenge.method"], "S256"
        )

    def _changes(self):
        other = self.store("backend").public_jwks()
        extra = json.dumps(
            {"keys": json.loads(self.jwks)["keys"] + json.loads(other)["keys"]}
        )
        return {
            "publicClient": {"publicClient": True},
            "clientAuthenticatorType": {"clientAuthenticatorType": "client-secret"},
            "keys differ": {"attributes": {"jwks.string": other}},
            "keys differ ": {"attributes": {"jwks.string": extra}},
            "use.jwks.url": {"attributes": {"use.jwks.url": "true"}},
            "token.endpoint.auth.signing.alg": {
                "attributes": {"token.endpoint.auth.signing.alg": ""}
            },
        }

    @staticmethod
    def _apply(rep, change):
        rep = json.loads(json.dumps(rep))
        attrs = change.get("attributes", {})
        rep.update({k: v for k, v in change.items() if k != "attributes"})
        rep["attributes"].update(attrs)
        return rep

    def test_hospital_drift(self):
        good = hospital._client_rep(self.h, "org", None, enabled=True)
        self.assertEqual(hospital._check_client_settings(self.h, good, "org"), [])
        for expected, change in self._changes().items():
            with self.subTest(expected):
                issues = hospital._check_client_settings(
                    self.h, self._apply(good, change), "org"
                )
                self.assertTrue(any(expected.strip() in i for i in issues), issues)

    def test_account_drift(self):
        good = accounts._client_rep("ihr-onboarding", self.jwks)
        self.assertEqual(
            accounts._check_settings("ihr-onboarding", good, self.jwks), []
        )
        for expected, change in self._changes().items():
            with self.subTest(expected):
                issues = accounts._check_settings(
                    "ihr-onboarding", self._apply(good, change), self.jwks
                )
                want = "keys differ" if "keys" in expected else expected
                self.assertTrue(any(want in i for i in issues), issues)

    def test_console_drift(self):
        good = ops.console_rep("https://c/cb", "https://c", self.jwks)
        for expected, change in {"": {}, **self._changes()}.items():
            with self.subTest(expected or "clean"):
                admin = mock.Mock()
                client = self._apply(good, change) | {"id": "cid"}
                admin.get.side_effect = lambda path, client=client: (
                    [client]
                    if path.startswith("clients?")
                    else {"realmMappings": [{"name": ops.OPERATOR_ROLE}]}
                )
                with mock.patch.object(realm, "check_realm", return_value=[]):
                    issues = ops.check_ops_realm(
                        admin, "https://c/cb", "https://c", self.jwks
                    )
                if not expected:
                    self.assertEqual(issues, [])
                else:
                    self.assertTrue(any(expected.strip() in i for i in issues), issues)

    def test_no_key_set_means_no_change(self):
        empty = hospital.HospitalLogin(
            "alpha", "Alpha", ("https://a",), ("https://a/cb",)
        )
        admin = mock.Mock()
        with self.assertRaisesMessage(Exception, "no public key set for the backend"):
            hospital.ensure_hospital(admin, empty)
        with self.assertRaisesMessage(
            Exception, "no public key set for ihr-onboarding"
        ):
            accounts.ensure_service_accounts(admin, {})
        with self.assertRaisesMessage(Exception, "no public key set for the backend"):
            ops.ensure_ops_realm(admin, "https://c/cb", "https://c", "")
        admin.put.assert_not_called()
        admin.post.assert_not_called()

    def test_a_client_taking_keys_from_a_url_is_reported(self):
        admin = mock.Mock()
        admin.get.return_value = [
            {"clientId": "account", "attributes": {"use.jwks.url": "true"}}
        ]
        issues = realm._check_clients(admin, None)
        self.assertIn("client account takes its keys from a URL", issues)


class DeploymentKeyCheckTests(TempKeys, SimpleTestCase):
    def test_the_backend_refuses_to_start_without_its_own_key(self):
        empty = self.make_dir()
        self.assertIn(
            "No backend client key", secure.client_key_problems("backend", empty)[0]
        )
        staff = self.store("staff")
        self.assertIn(
            "holds the staff client key",
            secure.client_key_problems("backend", staff.dir)[0],
        )
        backend = self.store("backend")
        self.assertEqual(secure.client_key_problems("backend", backend.dir), [])
        (backend.dir / f"{backend.active_kid()}.pem").chmod(0o644)
        self.assertIn(
            "must be 600", secure.client_key_problems("backend", backend.dir)[0]
        )
        self.assertEqual(secure.client_key_problems("", empty), [])  # e.g. Celery
        self.assertIn("must be one of", secure.client_key_problems("web", empty)[0])

    def test_the_check_framework_reports_it_too(self):
        from ihr_identity.checks import staff_sign_in_settings

        with override_settings(DEBUG=False, IHR_CLIENT_KEY_DIR=self.make_dir()):
            ids = {e.id for e in staff_sign_in_settings(None)}
        self.assertIn("ihr_identity.E007", ids)


class SyncKeyTests(SimpleTestCase):
    def test_public_sets_come_from_the_jwks_folder(self):
        from ihr_platform.management.commands import ihr_keycloak_sync

        keys = ihr_keycloak_sync.worker_jwks()
        self.assertEqual(set(keys), {"ihr-onboarding", "ihr-staff-worker"})
        self.assertTrue(
            json.loads(keys["ihr-onboarding"])["keys"][0]["kid"].startswith(
                "onboarding-"
            )
        )
        with (
            override_settings(IHR_CLIENT_JWKS_DIR=tempfile.gettempdir() + "/ihr-none"),
            self.assertRaisesMessage(CommandError, "no public key set for backend"),
        ):
            ihr_keycloak_sync.public_jwks("backend")


class ProbeCommandTests(SimpleTestCase):
    """ihr_client_keys probe: its verdicts and exit codes, with Keycloak's answers faked."""

    databases = {"default"}

    @staticmethod
    def answer(status, error):
        response = mock.Mock(status_code=status, text=json.dumps({"error": error}))
        response.json.return_value = {"error": error}
        return response

    def run_probe(self, *args, answers):
        out = io.StringIO()
        sent = []

        def post(url, data=None, timeout=None):
            sent.append(data)
            return answers(data, len(sent))

        with (
            schema_context("public"),
            mock.patch(f"{PROBE}.requests.post", side_effect=post),
        ):
            try:
                call_command("ihr_client_keys", "probe", *args, stdout=out)
                code = 0
            except CommandError as exc:
                code = exc.returncode
        return code, out.getvalue(), sent

    def test_exit_codes(self):
        def grant(data, n):
            return self.answer(400, "invalid_grant")

        def refused(data, n):
            return self.answer(401, "invalid_client")

        def mixed(data, n):
            return grant(data, n) if n == 1 else refused(data, n)

        def down(data, n):
            return self.answer(502, "")

        self.assertEqual(self.run_probe(answers=grant)[0], 0)
        self.assertEqual(self.run_probe(answers=refused)[0], 1)
        self.assertEqual(self.run_probe(answers=down)[0], 2)
        with mock.patch(
            f"{PROBE}.Command.targets",
            return_value=[("ihr-console", "indiehr-ops"), ("ihr-h-x", "indiehr")],
        ):
            self.assertEqual(self.run_probe(answers=mixed)[0], 3)
        _, out, sent = self.run_probe(answers=grant)
        for data in sent:
            self.assertIn("client_assertion", data)
            self.assertNotIn("client_assertion", out)
            self.assertNotIn(data["client_assertion"], out)

    def test_negative_wants_every_bad_assertion_refused(self):
        def keycloak(data, n):
            # Accept only a good assertion, once (as Keycloak's jti store does).
            from ihr_identity.testing import check_assertion

            try:
                claims = check_assertion(data, "indiehr-ops", seen)
            except Exception:  # any flaw: refused
                return self.answer(401, "invalid_client")
            now = time.time()
            if claims["exp"] <= now or now - claims["iat"] > 60:
                return self.answer(401, "invalid_client")
            return self.answer(400, "invalid_grant")

        seen = set()
        code, out, sent = self.run_probe("--negative", answers=keycloak)
        self.assertEqual(code, 0, out)
        self.assertIn("every bad assertion refused", out)
        self.assertGreaterEqual(len(sent), 11)
        lax = lambda data, n: self.answer(400, "invalid_grant")  # noqa: E731
        code, out, _ = self.run_probe("--negative", answers=lax)
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)
