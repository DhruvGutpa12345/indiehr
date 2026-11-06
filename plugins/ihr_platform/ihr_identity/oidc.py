"""Calls from the backend to Keycloak, and the realm's public signing keys (ADR-009).

Tokens and codes travel only over the back channel (``IHR_KEYCLOAK_URL``); the issuer and the
browser address are ``IHR_KEYCLOAK_PUBLIC_URL``. Response bodies are never logged: they hold
tokens.
"""

import threading
import time
from urllib.parse import urlencode

import jwt
import requests
from django.conf import settings
from django.db import connection
from ihr_platform.keycloak.hospital import client_id_for

TIMEOUT = 5
JWKS_TTL = 600
JWKS_RETRY = 30


class KeycloakUnavailableError(Exception):
    """Keycloak could not be reached or answered with a server error."""


class GrantRejectedError(Exception):
    """Keycloak refused the code or refresh token (e.g. ``invalid_grant``)."""


def issuer():
    return f"{settings.IHR_KEYCLOAK_PUBLIC_URL}/realms/{settings.IHR_KEYCLOAK_REALM}"


def _backchannel(path):
    realm = settings.IHR_KEYCLOAK_REALM
    return f"{settings.IHR_KEYCLOAK_URL}/realms/{realm}/protocol/openid-connect/{path}"


def client_id():
    return client_id_for(connection.tenant.slug)


def authorize_url(**params):
    return f"{issuer()}/protocol/openid-connect/auth?{urlencode(params)}"


def _post(path, data):
    try:
        resp = requests.post(_backchannel(path), data=data, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise KeycloakUnavailableError(type(exc).__name__) from exc
    if resp.status_code >= 500:  # noqa: PLR2004
        msg = f"HTTP {resp.status_code}"
        raise KeycloakUnavailableError(msg)
    if resp.status_code >= 400:  # noqa: PLR2004
        try:
            error = resp.json().get("error", "error")
        except ValueError:
            error = "error"
        raise GrantRejectedError(error)
    return resp.json() if resp.content else {}


def exchange_code(code, verifier, redirect_uri):
    return _post(
        "token",
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "client_id": client_id(),
        },
    )


def refresh(refresh_token):
    return _post(
        "token",
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id(),
        },
    )


def end_keycloak_session(refresh_token):
    """End the person's Keycloak session, i.e. single sign-on at every hospital (owner's
    decision for sign-out and idle time-out). True if Keycloak confirmed it."""
    try:
        _post("logout", {"refresh_token": refresh_token, "client_id": client_id()})
    except GrantRejectedError as exc:
        # invalid_grant: already ended or expired. Anything else (e.g. a client problem)
        # means Keycloak did not end it.
        return str(exc) == "invalid_grant"
    except KeycloakUnavailableError:
        return False
    return True


# --- signing keys -----------------------------------------------------------------------

_jwks_lock = threading.Lock()
_jwks = {"keys": {}, "fetched": 0.0, "failed": 0.0, "issuer": None}


def _fetch_keys():
    try:
        resp = requests.get(_backchannel("certs"), timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise KeycloakUnavailableError(type(exc).__name__) from exc
    keys = {}
    for jwk in data.get("keys", []):
        if jwk.get("kty") == "RSA" and jwk.get("use") == "sig" and jwk.get("kid"):
            keys[jwk["kid"]] = jwt.PyJWK(jwk, algorithm="RS256")
    return keys


def signing_keys(kid=None):
    """The realm's public keys by key id. Process-wide on purpose: they are the realm's, not
    a hospital's (TENANCY_DESIGN §6.3). An unknown ``kid`` refetches at most every 30 s."""
    now = time.monotonic()
    with _jwks_lock:
        same_realm = _jwks["issuer"] == issuer()
        keys = dict(_jwks["keys"]) if same_realm else {}
        stale = not same_realm or now - _jwks["fetched"] > JWKS_TTL
        unknown = kid is not None and kid not in keys
        due = stale or (unknown and now - _jwks["fetched"] > JWKS_RETRY)
        backing_off = now - _jwks["failed"] < JWKS_RETRY
    if due and (not backing_off or not keys):
        try:
            fresh = (
                _fetch_keys()
            )  # outside the lock: a slow Keycloak must not queue requests
        except KeycloakUnavailableError:
            with _jwks_lock:
                _jwks["failed"] = now
            if not keys:
                raise
            return keys  # keep the last good keys during an outage
        with _jwks_lock:
            _jwks.update(keys=fresh, fetched=now, failed=0.0, issuer=issuer())
        return dict(fresh)
    return keys
