"""Calls from the backend to Keycloak, and the realm's public signing keys (ADR-009).

Tokens and codes travel only over the back channel (``IHR_KEYCLOAK_URL``); the issuer and the
browser address are ``IHR_KEYCLOAK_PUBLIC_URL``. Response bodies are never logged: they hold
tokens. Every call to the token and logout endpoints carries a client assertion signed with the
backend's own key (P1-T8d2, ADR-012): the hospital and console clients are confidential.
"""

import logging
import threading
import time
from urllib.parse import urlencode

import jwt
import requests
from django.conf import settings
from django.db import connection
from ihr_platform.keycloak.hospital import client_id_for

from ihr_identity import client_auth

logger = logging.getLogger(__name__)

TIMEOUT = 5
JWKS_TTL = 600
JWKS_RETRY = 30


class KeycloakUnavailableError(Exception):
    """Keycloak could not be reached or answered with a server error."""


class GrantRejectedError(Exception):
    """Keycloak refused the code or refresh token (e.g. ``invalid_grant``)."""


def issuer(realm=None):
    realm = realm or settings.IHR_KEYCLOAK_REALM
    return f"{settings.IHR_KEYCLOAK_PUBLIC_URL}/realms/{realm}"


def _backchannel(path, realm=None):
    realm = realm or settings.IHR_KEYCLOAK_REALM
    return f"{settings.IHR_KEYCLOAK_URL}/realms/{realm}/protocol/openid-connect/{path}"


def client_id():
    return client_id_for(connection.tenant.slug)


def authorize_url(realm=None, **params):
    return f"{issuer(realm)}/protocol/openid-connect/auth?{urlencode(params)}"


def _post(path, data, realm=None):
    # client_id stays in the form: a client not yet switched to keys (public) accepts the
    # request on client_id alone and ignores the assertion, so the backend can sign first.
    try:
        auth = client_auth.assertion_fields(data["client_id"], realm)
    except client_auth.ClientKeyError as exc:
        # The message names the key folder only, never key material.
        logger.error(
            "cannot authenticate to Keycloak as %s: %s", data["client_id"], exc
        )
        msg = "client key"
        raise KeycloakUnavailableError(msg) from exc
    try:
        resp = requests.post(
            _backchannel(path, realm), data={**data, **auth}, timeout=TIMEOUT
        )
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


def exchange_code(code, verifier, redirect_uri, *, realm=None, client=None):
    return _post(
        "token",
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "client_id": client or client_id(),
        },
        realm,
    )


def refresh(refresh_token, *, realm=None, client=None):
    return _post(
        "token",
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client or client_id(),
        },
        realm,
    )


def end_keycloak_session(refresh_token, *, realm=None, client=None):
    """End the person's Keycloak session, i.e. single sign-on at every hospital (owner's
    decision for sign-out and idle time-out). True if Keycloak confirmed it."""
    try:
        _post(
            "logout",
            {"refresh_token": refresh_token, "client_id": client or client_id()},
            realm,
        )
    except GrantRejectedError as exc:
        # invalid_grant: already ended or expired. Anything else (e.g. a client problem)
        # means Keycloak did not end it.
        return str(exc) == "invalid_grant"
    except KeycloakUnavailableError:
        return False
    return True


# --- signing keys -----------------------------------------------------------------------

_jwks_lock = threading.Lock()
_jwks = {}  # issuer -> {"keys", "fetched", "failed"}


def _fetch_keys(realm):
    try:
        resp = requests.get(_backchannel("certs", realm), timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise KeycloakUnavailableError(type(exc).__name__) from exc
    keys = {}
    for jwk in data.get("keys", []):
        if jwk.get("kty") == "RSA" and jwk.get("use") == "sig" and jwk.get("kid"):
            keys[jwk["kid"]] = jwt.PyJWK(jwk, algorithm="RS256")
    return keys


def signing_keys(kid=None, realm=None):
    """The realm's public keys by key id. Process-wide on purpose: they are the realm's, not
    a hospital's (TENANCY_DESIGN §6.3). An unknown ``kid`` refetches at most every 30 s."""
    now = time.monotonic()
    name = issuer(realm)
    with _jwks_lock:
        entry = _jwks.setdefault(name, {"keys": {}, "fetched": 0.0, "failed": 0.0})
        keys = dict(entry["keys"])
        stale = now - entry["fetched"] > JWKS_TTL
        unknown = kid is not None and kid not in keys
        due = stale or (unknown and now - entry["fetched"] > JWKS_RETRY)
        backing_off = now - entry["failed"] < JWKS_RETRY
    if due and (not backing_off or not keys):
        try:
            # Outside the lock: a slow Keycloak must not queue requests.
            fresh = _fetch_keys(realm)
        except KeycloakUnavailableError:
            with _jwks_lock:
                entry["failed"] = now
            if not keys:
                raise
            return keys  # keep the last good keys during an outage
        with _jwks_lock:
            entry.update(keys=fresh, fetched=now, failed=0.0)
        return dict(fresh)
    return keys
