"""The token checks of ADR-008/009, one refusing case per check (no database)."""

import time
import uuid

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import SimpleTestCase

from ihr_identity import testing
from ihr_identity.tokens import TokenRejectedError, verify_access_token, verify_id_token

ISSUER = "https://auth.test/realms/indiehr"
ORG = str(uuid.uuid4())


class _Hospital:
    slug = "alpha"
    keycloak_org_id = ORG


def token(**overrides):
    with _Issuer():
        return testing.mint(
            _Hospital, "11111111-1111-1111-1111-111111111111", **overrides
        )


class _Issuer:
    def __enter__(self):
        from unittest import mock

        from ihr_identity import oidc

        self.patch = mock.patch.object(oidc, "issuer", lambda: ISSUER)
        self.patch.start()

    def __exit__(self, *exc):
        self.patch.stop()


def check(tok, **kw):
    args = {
        "keys": testing.public_keys(),
        "issuer": ISSUER,
        "client_id": "ihr-h-alpha",
        "org_id": ORG,
        "slug": "alpha",
        "now": time.time(),
    } | kw
    return verify_access_token(tok, **args)


class AccessTokenTests(SimpleTestCase):
    def test_a_well_formed_token_passes(self):
        claims = check(token())
        self.assertEqual(claims["azp"], "ihr-h-alpha")

    def refused(self, tok, reason, **kw):
        with self.assertRaisesRegex(TokenRejectedError, reason):
            check(tok, **kw)

    def test_unsigned_and_hs256_tokens_are_refused(self):
        claims = jwt.decode(token(), options={"verify_signature": False})
        unsigned = jwt.encode(claims, None, algorithm="none")
        self.refused(unsigned, "algorithm")
        hs = jwt.encode(claims, "secret-but-public-key-like", algorithm="HS256")
        self.refused(hs, "algorithm")

    def test_unknown_key_and_forged_signature_are_refused(self):
        self.refused(token(kid="other"), "unknown signing key")
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.refused(token(key=other), "invalid token")

    def test_token_from_the_future_is_refused(self):
        now = int(time.time())
        self.refused(token(iat=now + 120, exp=now + 400), "future")

    def test_wrong_issuer_is_refused(self):
        self.refused(token(iss="https://evil.test/realms/indiehr"), "wrong issuer")

    def test_id_token_is_not_an_access_token(self):
        self.refused(token(typ="ID"), "not an access token")

    def test_expired_and_long_lived_tokens_are_refused(self):
        old = int(time.time()) - 1000
        self.refused(token(iat=old, exp=old + 300), "expired")
        now = int(time.time())
        self.refused(token(iat=now, exp=now + 3600), "lifetime")

    def test_missing_claims_are_refused(self):
        for claim in ("acr", "sid", "azp", "aud"):
            with self.subTest(claim=claim):
                self.refused(token(**{claim: None}), "invalid token")

    def test_wrong_azp_is_refused(self):
        self.refused(token(azp="ihr-h-beta"), "another hospital")

    def test_extra_audience_is_refused(self):
        self.refused(token(aud=["ihr-h-alpha", "ihr-h-beta"]), "audience")
        self.refused(token(aud=["ihr-h-alpha", "account"]), "audience")

    def test_roles_of_another_client_are_refused(self):
        roles = {
            "ihr-h-alpha": {"roles": ["member"]},
            "ihr-h-beta": {"roles": ["member"]},
        }
        self.refused(token(resource_access=roles), "other clients")

    def test_missing_member_is_refused(self):
        self.refused(token(resource_access={"ihr-h-alpha": {"roles": []}}), "member")

    def test_other_organisation_is_refused(self):
        self.refused(
            token(ihr_org={"alias": "alpha", "id": str(uuid.uuid4())}), "organisation"
        )
        self.refused(token(ihr_org={"alias": "beta", "id": ORG}), "organisation")

    def test_password_only_sign_in_is_refused(self):
        self.refused(token(acr="pwd"), "second factor")
        self.refused(token(acr="1"), "second factor")


class IdTokenTests(SimpleTestCase):
    def test_id_token_must_match_the_sign_in(self):
        access = check(token())
        with _Issuer():
            id_token = testing.mint(
                _Hospital, access["sub"], kind="ID", sid=access["sid"]
            )
        args = {
            "keys": testing.public_keys(),
            "issuer": ISSUER,
            "client_id": "ihr-h-alpha",
            "access": access,
            "now": time.time(),
        }
        self.assertEqual(verify_id_token(id_token, nonce="n", **args)["typ"], "ID")
        with self.assertRaisesRegex(TokenRejectedError, "nonce"):
            verify_id_token(id_token, nonce="other", **args)
        with self.assertRaisesRegex(TokenRejectedError, "different sign-ins"):
            verify_id_token(
                id_token, nonce="n", **(args | {"access": {**access, "sub": "x"}})
            )
        with _Issuer():
            foreign = testing.mint(
                _Hospital,
                access["sub"],
                kind="ID",
                sid=access["sid"],
                aud="ihr-h-beta",
                azp="ihr-h-beta",
            )
            other_sid = testing.mint(_Hospital, access["sub"], kind="ID", sid="other")
        with self.assertRaisesRegex(TokenRejectedError, "another client"):
            verify_id_token(foreign, nonce="n", **args)
        with self.assertRaisesRegex(TokenRejectedError, "different sign-ins"):
            verify_id_token(other_sid, nonce="n", **args)
        with self.assertRaisesRegex(TokenRejectedError, "not an ID token"):
            verify_id_token(token(nonce="n", auth_time=1), nonce="n", **args)
