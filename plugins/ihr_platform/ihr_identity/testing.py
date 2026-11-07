"""A stand-in for Keycloak in tests (never imported by the app).

``TestIdp`` signs real RS256 tokens in the shape ADR-008 fixes, and patches ``ihr_identity.oidc``
so nothing reaches the network. The live Keycloak job (keycloak/tests/test_live.py) checks that
real tokens have that shape, so the two cannot drift apart silently.
"""

import secrets
import time
import uuid
from contextlib import ExitStack, contextmanager
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.db import connection
from django_tenants.utils import schema_context
from ihr_platform.keycloak.hospital import client_id_for
from rest_framework.test import APIClient

from ihr_identity import oidc, sessions
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


@contextmanager
def fake_keycloak(refresh=None, logout_ok=True):
    """Patch Keycloak calls. ``refresh`` is a callable(refresh_token) -> tokens, or None to
    hand out a fresh token for the current hospital and the person the refresh token names."""
    calls = {"refresh": 0, "logout": 0}
    used = set()

    def _refresh(token, **kwargs):
        calls["refresh"] += 1
        if token in used:  # Keycloak rotates refresh tokens: each works once
            raise oidc.GrantRejectedError("invalid_grant")
        used.add(token)
        if refresh is not None:
            return refresh(token)
        return tokens_for(connection.tenant, token.split(".")[1])

    def _logout(token, **kwargs):
        calls["logout"] += 1
        return logout_ok

    with ExitStack() as stack:
        stack.enter_context(
            mock.patch.object(
                oidc, "signing_keys", lambda kid=None, realm=None: public_keys()
            )
        )
        stack.enter_context(mock.patch.object(oidc, "refresh", _refresh))
        stack.enter_context(mock.patch.object(oidc, "end_keycloak_session", _logout))
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
