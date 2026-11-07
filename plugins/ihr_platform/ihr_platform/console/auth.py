"""Operator sign-in for the console (P1-T5b, TENANCY_DESIGN §4.9).

Like the hospitals' staff sign-in (ADR-009) but in the operator realm: the backend runs the
Keycloak code + PKCE sign-in, keeps the tokens, and the browser holds one HttpOnly cookie.
Every request re-checks: the session's limits, the operator's entry on our own list, and,
once a minute, a refreshed token with MFA and the ``ihr-operator`` role.
"""

import hashlib
import secrets
import time
from dataclasses import asdict, dataclass

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from ihr_identity import oidc
from ihr_identity.sessions import seal, unseal
from ihr_identity.tokens import TokenRejectedError, verify_operator_token

from ihr_platform.keycloak.ops import OPERATOR_ROLE
from ihr_platform.models import OperatorAccount

REFRESH_AFTER = 60


def console_url():
    url = getattr(settings, "IHR_CONSOLE_URL", "")
    if not url:
        msg = "Set IHR_CONSOLE_URL to serve the operator console."
        raise ImproperlyConfigured(msg)
    return url.rstrip("/")


def redirect_uri():
    return f"{console_url()}/ops/callback/"


def realm():
    return settings.IHR_OPS_REALM


def check_token(token, now=None):
    kid = oidc.jwt.get_unverified_header(token).get("kid")
    return verify_operator_token(
        token,
        keys=oidc.signing_keys(kid, realm=realm()),
        issuer=oidc.issuer(realm()),
        client_id=settings.IHR_CONSOLE_CLIENT,
        role=OPERATOR_ROLE,
        now=now or time.time(),
        leeway=settings.IHR_OIDC_LEEWAY_SECONDS,
    )


def operator_for(sub):
    return OperatorAccount.objects.filter(keycloak_sub=sub, active=True).first()


@dataclass
class OperatorSession:
    sub: str
    name: str
    created: float
    refreshed: float
    refresh: str
    cookie: str = ""
    # Kept in a key of its own (marking activity must never overwrite the tokens)
    last_activity: float = 0.0


class Unavailable:
    """Keycloak could not be asked: not a sign-out, try again shortly."""


UNAVAILABLE = Unavailable()


def _key(cookie):
    return f"ihr:ops:sess:{hashlib.sha256(cookie.encode()).hexdigest()}"


def _store(session):
    data = asdict(session)
    data.pop("cookie")
    data.pop("last_activity")
    cache.set(
        _key(session.cookie),
        seal(data, session.cookie.encode(), "ihr-ops-session"),
        timeout=settings.IHR_SESSION_MAX_SECONDS,
    )


def _mark_activity(session, when):
    cache.set(
        f"{_key(session.cookie)}:act", when, timeout=settings.IHR_SESSION_MAX_SECONDS
    )


def create_session(sub, name, refresh_token):
    now = time.time()
    session = OperatorSession(
        sub=sub,
        name=name,
        created=now,
        refreshed=now,
        refresh=refresh_token,
        cookie=secrets.token_urlsafe(32),
        last_activity=now,
    )
    _store(session)
    _mark_activity(session, now)
    return session


def load_session(cookie):
    if not cookie or len(cookie) > 100:  # noqa: PLR2004
        return None
    text = cache.get(_key(cookie))
    data = unseal(text, cookie.encode(), "ihr-ops-session") if text else None
    if not data:
        return None
    session = OperatorSession(cookie=cookie, **data)
    session.last_activity = cache.get(f"{_key(cookie)}:act") or session.created
    return session


def delete_session(cookie):
    cache.delete_many([_key(cookie), f"{_key(cookie)}:act", f"{_key(cookie)}:lock"])


def current_operator(request, *, active=True):
    """The signed-in operator's live session; None (sign in again); or UNAVAILABLE.

    ``active=False`` for the progress page's automatic refresh: it must not count as the
    operator being at the keyboard, or an unattended console would never time out."""
    session = load_session(request.COOKIES.get(settings.IHR_OPS_SESSION_COOKIE))
    if session is None:
        return None
    now = time.time()
    if (
        now - session.created > settings.IHR_SESSION_MAX_SECONDS
        or now - session.last_activity > settings.IHR_SESSION_IDLE_SECONDS
        or operator_for(session.sub) is None
    ):
        end_session(session)
        return None
    if now - session.refreshed > REFRESH_AFTER:
        outcome = _refresh(session, now)
        if outcome is not session:
            return outcome
    if active:
        _mark_activity(session, now)
    return session


def _refresh(session, now):  # noqa: PLR0911 (each exit is one outcome)
    """Re-ask Keycloak (role, MFA, account still there). One request at a time: refresh
    tokens work once, so the others carry on with the session as it is."""
    token = secrets.token_hex(8)
    lock = f"{_key(session.cookie)}:lock"
    if not cache.add(lock, token, timeout=10):
        return session
    try:
        latest = load_session(session.cookie)
        if latest is None:
            return None
        if latest.refreshed > session.refreshed:
            return latest  # another request has just refreshed it
        try:
            tokens = oidc.refresh(
                latest.refresh, realm=realm(), client=settings.IHR_CONSOLE_CLIENT
            )
            claims = check_token(tokens["access_token"], now)
        except (oidc.GrantRejectedError, TokenRejectedError, KeyError):
            delete_session(session.cookie)
            return None
        except oidc.KeycloakUnavailableError:
            return UNAVAILABLE
        if claims["sub"] != latest.sub:
            delete_session(session.cookie)
            return None
        latest.refresh = tokens["refresh_token"]
        latest.refreshed = now
        _store(latest)
        return latest
    finally:
        if cache.get(lock) == token:
            cache.delete(lock)


def end_session(session):
    """Delete the session and end the operator's Keycloak sign-in. True if confirmed."""
    delete_session(session.cookie)
    return oidc.end_keycloak_session(
        session.refresh, realm=realm(), client=settings.IHR_CONSOLE_CLIENT
    )
