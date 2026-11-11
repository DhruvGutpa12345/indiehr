"""A stand-in for Keycloak in tests (never imported by the app).

``TestIdp`` signs real RS256 tokens in the shape ADR-008 fixes, and patches ``ihr_identity.oidc``
so nothing reaches the network. The live Keycloak job (keycloak/tests/test_live.py) checks that
real tokens have that shape, so the two cannot drift apart silently.

``fake_keycloak`` answers the backend's own token and logout requests, and checks the client
assertion on every one of them (P1-T8d2), as Keycloak does: a request that fell back to a bare
client_id, or signed for the wrong client or audience, fails the test.
"""

import json
import secrets
import time
import uuid
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlsplit

import jwt
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.db import connection
from django_tenants.utils import schema_context
from ihr_platform import client_keys
from ihr_platform.keycloak.hospital import client_id_for
from rest_framework.test import APIClient

from ihr_identity import client_auth, oidc, sessions
from ihr_identity.models import StaffIdentity

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "test-key"


def public_keys():
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_KEY.public_key(), as_dict=True)
    return {KID: jwt.PyJWK({**jwk, "kid": KID, "use": "sig", "alg": "RS256"})}


def ensure_org_id(hospital):
    """Give a test hospital a Keycloak organisation id if it has none."""
    if hospital.keycloak_org_id is None:
        with schema_context("public"):
            type(hospital).objects.filter(pk=hospital.pk).update(
                keycloak_org_id=uuid.uuid4()
            )
            hospital.refresh_from_db()
    return hospital


def mint(hospital, sub, *, kind="Bearer", key=None, kid=KID, **overrides):
    """A signed token for ``hospital``; an override of None removes that claim."""
    now = int(time.time())
    cid = client_id_for(hospital.slug)
    claims = {
        "iss": oidc.issuer(),
        "sub": str(sub),
        "aud": cid,
        "azp": cid,
        "typ": kind,
        "iat": now,
        "exp": now + 300,
        "sid": "sid-" + str(sub)[:8],
        "acr": "mfa",
        "resource_access": {cid: {"roles": ["member"]}},
        "ihr_org": {"alias": hospital.slug, "id": str(hospital.keycloak_org_id)},
    }
    if kind == "ID":
        claims.update(nonce="n", auth_time=now)
        claims.pop("resource_access")
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key or _KEY, algorithm="RS256", headers={"kid": kid})


def mint_logout(hospital, sid, *, sub=None, key=None, typ="logout+jwt", **overrides):
    """A back-channel logout token as Keycloak 26.8 sends it (P1-T5d); None removes a claim."""
    now = int(time.time())
    claims = {
        "iss": oidc.issuer(),
        "aud": client_id_for(hospital.slug),
        "sub": str(sub or uuid.uuid4()),
        "typ": "Logout",
        "iat": now,
        "exp": now + 120,
        "jti": str(uuid.uuid4()),
        "sid": sid,
        "events": {"http://schemas.openid.net/event/backchannel-logout": {}},
    } | overrides
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(
        claims, key or _KEY, algorithm="RS256", headers={"kid": KID, "typ": typ}
    )


def _refresh_token(sub):
    return f"refresh.{sub}.{secrets.token_hex(8)}"


def tokens_for(hospital, sub, **overrides):
    return {
        "access_token": mint(hospital, sub, **overrides),
        "refresh_token": _refresh_token(sub),
        "id_token": mint(hospital, sub, kind="ID"),
    }


class _Response:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self.content = json.dumps(body).encode() if body is not None else b""

    def json(self):
        return json.loads(self.content)


def check_assertion(data, realm, jtis=None):
    """Check a token or logout request's client authentication as Keycloak would: the backend's
    active key, ES256, iss = sub = client_id, the realm's public issuer as audience, a 30 s
    lifetime and a jti never seen before. Raises AssertionError (a failing test) otherwise."""
    if "client_secret" in data:
        msg = "the backend sent a client secret"
        raise AssertionError(msg)
    if data.get("client_assertion_type") != client_keys.ASSERTION_TYPE:
        raise AssertionError("the request carries no client assertion")
    assertion = data.get("client_assertion", "")
    header = jwt.get_unverified_header(assertion)
    store = client_auth.store()
    if (header.get("alg"), header.get("kid")) != ("ES256", store.active_kid()):
        msg = f"assertion header {header} is not the active ES256 key"
        raise AssertionError(msg)
    public = store.private_key(store.active_kid()).public_key()
    # Time is not checked here (tests may move the clock); the live tests check it for real.
    claims = jwt.decode(
        assertion,
        public,
        algorithms=["ES256"],
        audience=oidc.issuer(realm),
        options={"verify_exp": False, "verify_iat": False},
    )
    if not claims["iss"] == claims["sub"] == data.get("client_id"):
        msg = f"assertion for {claims['sub']}, sent as {data.get('client_id')}"
        raise AssertionError(msg)
    if not isinstance(claims["aud"], str):
        msg = "Keycloak takes one audience only"
        raise AssertionError(msg)
    if claims["exp"] - claims["iat"] != client_keys.LIFETIME:
        raise AssertionError("assertion lifetime is not 30 s")
    if jtis is not None:
        if claims["jti"] in jtis:
            raise AssertionError("assertion jti used twice")
        jtis.add(claims["jti"])
    return claims


@contextmanager
def fake_keycloak(refresh=None, logout_ok=True):
    """Answer the backend's calls to Keycloak. ``refresh`` is a callable(refresh_token) ->
    tokens (it may raise oidc.GrantRejectedError or KeycloakUnavailableError), or None to hand
    out a fresh token for the current hospital and the person the refresh token names. Every
    call's client assertion is checked (``check_assertion``)."""
    calls = {"refresh": 0, "logout": 0}
    used = set()
    jtis = set()

    def _refresh(token):
        calls["refresh"] += 1
        if token in used:  # Keycloak rotates refresh tokens: each works once
            raise oidc.GrantRejectedError("invalid_grant")
        used.add(token)
        if refresh is not None:
            return refresh(token)
        return tokens_for(connection.tenant, token.split(".")[1])

    def _post(url, data=None, timeout=None):
        base = f"{settings.IHR_KEYCLOAK_URL}/realms/"
        if not url.startswith(base):
            msg = f"not Keycloak's back channel: {url}"
            raise AssertionError(msg)
        realm, _, path = urlsplit(url).path.removeprefix("/realms/").partition("/")
        check_assertion(data, realm, jtis)
        try:
            if path == "protocol/openid-connect/logout":
                calls["logout"] += 1
                if not logout_ok:
                    raise oidc.KeycloakUnavailableError("down")
                return _Response(204)
            if data.get("grant_type") != "refresh_token":
                msg = f"unexpected Keycloak call {path} {data.get('grant_type')}"
                raise AssertionError(msg)
            return _Response(200, _refresh(data["refresh_token"]))
        except oidc.GrantRejectedError as exc:
            return _Response(400, {"error": str(exc)})
        except oidc.KeycloakUnavailableError:
            return _Response(503)

    def _get(*args, **kwargs):
        raise AssertionError("signing_keys is patched: nothing else may GET Keycloak")

    fake = SimpleNamespace(
        post=_post, get=_get, RequestException=requests.RequestException
    )
    with ExitStack() as stack:
        stack.enter_context(
            mock.patch.object(
                oidc, "signing_keys", lambda kid=None, realm=None: public_keys()
            )
        )
        stack.enter_context(mock.patch.object(oidc, "requests", fake))
        yield calls


def link(user, sub=None):
    identity, _ = StaffIdentity.objects.update_or_create(
        user=user, defaults={"keycloak_sub": sub or uuid.uuid4()}
    )
    return identity


def staff_session(hospital, user, sid="sid-test", **claim_overrides):
    """A session for ``user`` at ``hospital`` (call inside that hospital's schema)."""
    hospital = ensure_org_id(hospital)
    identity = link(user)
    access = mint(hospital, identity.keycloak_sub, **claim_overrides)
    now = time.time()
    return sessions.create(
        tid=hospital.schema_name,
        user_id=user.pk,
        sub=str(identity.keycloak_sub),
        sid=sid,
        auth_time=now,
        access=access,
        access_exp=now + 300,
        access_iat=now,
        refresh=_refresh_token(identity.keycloak_sub),
    )


def staff_client(hospital, domain, user, sid="sid-test", **claim_overrides):
    """An API client signed in as ``user`` at ``hospital``, as the web app would be."""
    with schema_context(hospital.schema_name):
        session = staff_session(hospital, user, sid, **claim_overrides)
    client = APIClient(
        HTTP_HOST=domain, HTTP_X_IHR_CSRF="1", raise_request_exception=False
    )
    client.cookies[settings.IHR_SESSION_COOKIE] = session.cookie
    return client
