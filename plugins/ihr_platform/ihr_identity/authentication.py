"""How CARE's API recognises staff in tenant mode (ADR-009).

``StaffSessionAuthentication`` reads the backend session cookie and, on every request,
re-checks the stored Keycloak token for *this* hospital, the person's link to a CARE user and
the session limits. A dead or foreign session is simply not a sign-in (``None``): staff routes
then answer 403 and the cookie is cleared on the way out (``middleware.py``).
``ServiceTokenAuthentication`` keeps DRF tokens for machine accounts only (TENANCY_DESIGN §4.8).
"""

import logging
import time

from django.conf import settings
from django.db import connection
from django.utils import timezone
from ihr_platform.links import hospital_origins
from ihr_platform.models import Hospital
from rest_framework.authentication import BaseAuthentication, TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

from ihr_identity import oidc, sessions
from ihr_identity.models import StaffIdentity
from ihr_identity.tokens import (
    TokenExpiredError,
    TokenRejectedError,
    verify_access_token,
)

logger = logging.getLogger(__name__)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
REFRESH_AFTER = 60  # seconds: refresh (and so re-ask Keycloak) once a token is this old
LAST_LOGIN_EVERY = 900
WAIT_STEP, WAIT_STEPS = 0.1, 30  # wait up to 3 s for another request's refresh


def check_stored_token(token, now=None):
    """Claims of a stored access token for the current hospital, or TokenRejectedError."""
    tenant = connection.tenant
    if tenant.status != Hospital.Status.ACTIVE:
        raise TokenRejectedError("hospital is not active")
    if tenant.keycloak_org_id is None:
        raise TokenRejectedError("hospital is not linked to Keycloak")
    try:
        kid = oidc.jwt.get_unverified_header(token).get("kid")
    except oidc.jwt.PyJWTError as exc:
        raise TokenRejectedError("malformed token") from exc
    return verify_access_token(
        token,
        keys=oidc.signing_keys(kid),
        issuer=oidc.issuer(),
        client_id=oidc.client_id(),
        org_id=tenant.keycloak_org_id,
        slug=tenant.slug,
        now=time.time(),
        leeway=settings.IHR_OIDC_LEEWAY_SECONDS,
    )


def check_csrf(request):
    """Unsafe methods need our header (forces a CORS preflight, which never allows
    credentials) and, where the browser says, a same-origin request from this hospital."""
    if request.method in SAFE_METHODS:
        return
    if request.headers.get("X-IHR-CSRF") != "1":
        raise PermissionDenied("Missing request header.")
    site = request.headers.get("Sec-Fetch-Site")
    if site is not None and site != "same-origin":
        raise PermissionDenied("Cross-site request refused.")
    origin = request.headers.get("Origin")
    if origin is not None and origin not in hospital_origins():
        raise PermissionDenied("Request from another site refused.")


def identity_for(session):
    """The active, non-machine CARE user this session belongs to, or None."""
    identity = (
        StaffIdentity.objects.select_related("user")
        .filter(keycloak_sub=session.sub)
        .first()
    )
    if identity is None or identity.user_id != session.user_id:
        return None
    user = identity.user
    if not user.is_active or user.deleted or user.is_service_account:
        return None
    if user.is_superuser and settings.IHR_STAFF_REFUSE_SUPERUSERS:
        return None  # no in-hospital superuser (Q-T5)
    valid_after = identity.sessions_valid_after
    if valid_after and session.created < valid_after.timestamp():
        return None
    return identity


def end_session(request, session, *, everywhere):
    """Delete the session. For sign-out and idle time-out (owner's decision) also end the
    person's Keycloak single sign-on and refuse their other sessions at this hospital; the
    next sign-in then needs the password and code again. True if Keycloak confirmed."""
    sessions.delete(session.cookie)
    _clear_cookie_later(request)
    if not everywhere:
        return True
    # Written by the middleware after the view: a refused request rolls back the view's
    # transaction (ATOMIC_REQUESTS), which would undo the write.
    _django_request(request).ihr_revoke_sub = session.sub
    return oidc.end_keycloak_session(session.refresh)


def revoke(sub):
    """Refuse every session of ``sub`` here that started before now."""
    StaffIdentity.objects.filter(keycloak_sub=sub).update(
        sessions_valid_after=timezone.now()
    )


def _django_request(request):
    return getattr(request, "_request", request)


def _clear_cookie_later(request):
    _django_request(request).ihr_clear_session = True


class StaffSessionAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return ""  # 403 like CARE's own classes (its tests and the web app expect that)

    def authenticate(self, request):  # noqa: PLR0911 (each exit is one refusal reason)
        cookie = request.COOKIES.get(settings.IHR_SESSION_COOKIE)
        if not cookie:
            return None
        session = sessions.load(cookie)
        if session is None:
            _clear_cookie_later(request)
            return None
        if session.tid != connection.schema_name:  # second line behind the key prefix
            _clear_cookie_later(request)
            return None
        check_csrf(request)
        now = time.time()
        if not self._within_limits(request, session, now):
            return None
        if not self._fresh_token(request, session, now):
            if sessions.load(cookie) is None:
                _clear_cookie_later(request)
            return None
        identity = identity_for(session)
        if identity is None:
            sessions.delete(session.cookie)
            _clear_cookie_later(request)
            return None
        user = identity.user
        if (
            user.last_login is None
            or now - user.last_login.timestamp() > LAST_LOGIN_EVERY
        ):
            type(user).objects.filter(pk=user.pk).update(last_login=timezone.now())
        return (user, None)

    def _within_limits(self, request, session, now):
        if now - session.created > settings.IHR_SESSION_MAX_SECONDS:
            sessions.delete(session.cookie)
            _clear_cookie_later(request)
            return False
        idle_limit = settings.IHR_SESSION_IDLE_SECONDS
        claimed = now
        if (header := request.headers.get("X-IHR-Idle")) is not None:
            try:
                claimed = now - min(max(float(header), 0.0), idle_limit + 1)
            except ValueError:
                claimed = now - idle_limit - 1
        activity = max(sessions.last_activity(session), claimed)
        if now - activity > idle_limit:
            end_session(request, session, everywhere=True)
            return False
        if activity - sessions.last_activity(session) > REFRESH_AFTER:
            sessions.mark_activity(session, activity)
        return True

    def _fresh_token(self, request, session, now):
        """Re-check the stored token; refresh it once it is a minute old, so a sign-out at
        another hospital (Keycloak session gone) ends this one within about a minute."""
        if now - session.access_iat > REFRESH_AFTER:
            session = self._refreshed(request, session, now)
            if session is None:
                return False
        try:
            check_stored_token(session.access, now)
        except TokenExpiredError:
            return False  # not signed in for now; the session itself is kept
        except TokenRejectedError as exc:
            logger.info("staff session refused: %s", exc)
            sessions.delete(session.cookie)
            _clear_cookie_later(request)
            return False
        except oidc.KeycloakUnavailableError:
            return False
        return True

    def _refreshed(self, request, session, now):
        """The session with fresh tokens, or None if it has ended. Only one request per
        session refreshes (Keycloak refresh tokens work once); the others wait for it."""
        token = sessions.lock(session)
        if token is None:
            for _ in range(WAIT_STEPS):
                time.sleep(WAIT_STEP)
                latest = sessions.load(session.cookie)
                if latest is None or latest.access_iat > session.access_iat:
                    return latest
            return session
        try:
            latest = sessions.load(session.cookie)
            if latest is None or latest.access_iat > session.access_iat:
                return latest  # gone, or already refreshed by another request
            tokens = oidc.refresh(latest.refresh)
            claims = check_stored_token(tokens["access_token"], now)
            sessions.save_tokens(latest, tokens, claims)
        except (oidc.GrantRejectedError, TokenRejectedError, KeyError) as exc:
            logger.info("staff session ended at refresh: %s", type(exc).__name__)
            sessions.delete(session.cookie)
            _clear_cookie_later(request)
            return None
        except oidc.KeycloakUnavailableError:
            return latest  # keep the current token while it is still valid
        finally:
            sessions.unlock(session, token)
        return latest


class ServiceTokenAuthentication(TokenAuthentication):
    """DRF tokens, for machine accounts of an active hospital only."""

    def authenticate_header(self, request):
        return ""

    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)
        if not user.is_service_account:
            raise AuthenticationFailed("Tokens are for machine accounts only.")
        if connection.tenant.status != Hospital.Status.ACTIVE:
            raise AuthenticationFailed("Hospital is not active.")
        return user, token
