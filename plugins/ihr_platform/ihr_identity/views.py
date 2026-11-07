"""Staff sign-in through Keycloak, run by the backend (ADR-009).

    GET  /api/ihr/auth/login/?next=/x   -> Keycloak (password + code + membership gate)
    GET  /api/ihr/auth/callback/        <- Keycloak; sets the session cookie, back to /x
    POST /api/ihr/auth/logout/          ends the session and single sign-on everywhere
    GET  /api/ihr/auth/session/         who is signed in (never tokens)

The login state (state, nonce, PKCE verifier, next) travels in an encrypted, short-lived cookie,
so an anonymous request writes nothing to Redis.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import time
from urllib.parse import urlencode

from django.conf import settings
from django.db import connection
from django.http import HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from ihr_audit import context as audit
from ihr_platform.links import frontend_base_url
from ihr_platform.models import Domain
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ihr_identity import oidc, sessions
from ihr_identity.authentication import (
    StaffSessionAuthentication,
    check_csrf,
    check_stored_token,
    end_session,
    identity_for,
)
from ihr_identity.tokens import TokenRejectedError, verify_id_token

logger = logging.getLogger(__name__)
LOGIN_STATE_SECONDS = 600


def _login_secret():
    return settings.SECRET_KEY.encode()


def _redirect_uri(request):
    """The callback on the address the person used, if it is one of this hospital's."""
    host = request.get_host().rsplit(":", 1)[0]
    if Domain.objects.filter(tenant=connection.tenant, domain=host).exists():
        origin = settings.IHR_FRONTEND_URL_TEMPLATE.format(domain=host)
    else:
        origin = frontend_base_url()
    return f"{origin.rstrip('/')}/api/ihr/auth/callback/"


def _safe_next(request, value):
    if (
        value
        and value.startswith("/")
        and not value.startswith(("//", "/\\"))
        and url_has_allowed_host_and_scheme(
            value, allowed_hosts={request.get_host()}, require_https=False
        )
    ):
        return value
    return "/"


def _cookie_args(max_age):
    return {
        "max_age": max_age,
        "path": "/",
        "secure": settings.IHR_SESSION_COOKIE_SECURE,
        "httponly": True,
    }


def _to_login(error):
    return HttpResponseRedirect(f"/login?{urlencode({'error': error})}")


@never_cache
@require_GET
def login(request):
    verifier = secrets.token_urlsafe(48)
    state = {
        "state": secrets.token_urlsafe(24),
        "nonce": secrets.token_urlsafe(24),
        "verifier": verifier,
        "next": _safe_next(request, request.GET.get("next")),
        "reauth": request.GET.get("reauth") == "1",
        "tid": connection.schema_name,
        "iat": time.time(),
        "redirect_uri": _redirect_uri(request),
    }
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    params = {
        "client_id": oidc.client_id(),
        "redirect_uri": state["redirect_uri"],
        "response_type": "code",
        "scope": "openid",
        "state": state["state"],
        "nonce": state["nonce"],
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if state["reauth"]:
        params["prompt"] = "login"
    response = HttpResponseRedirect(oidc.authorize_url(**params))
    response.set_cookie(
        settings.IHR_LOGIN_COOKIE,
        sessions.seal(state, _login_secret(), "ihr-login"),
        samesite="Lax",  # must come back on Keycloak's top-level redirect
        **_cookie_args(LOGIN_STATE_SECONDS),
    )
    return response


def _take_login_state(request):
    text = request.COOKIES.get(settings.IHR_LOGIN_COOKIE)
    state = sessions.unseal(text, _login_secret(), "ihr-login") if text else None
    if (
        not state
        or state.get("tid") != connection.schema_name
        or time.time() - state.get("iat", 0) > LOGIN_STATE_SECONDS
    ):
        return None
    if not hmac.compare_digest(str(request.GET.get("state", "")), state["state"]):
        return None
    return state


@never_cache
@require_GET
def callback(request):
    state = _take_login_state(request)
    response = _finish_sign_in(request, state)
    response.delete_cookie(settings.IHR_LOGIN_COOKIE, path="/")
    return response


def _finish_sign_in(request, state):  # noqa: PLR0911 (each exit is one outcome)
    if state is None and not request.GET:
        # "Back to application" after setting up an account from the e-mailed link
        # (P1-T5c): nothing to finish, so on to the sign-in page.
        return HttpResponseRedirect("/login")
    if state is None:
        return _to_login("expired")
    if "error" in request.GET or not request.GET.get("code"):
        return _to_login("cancelled")
    try:
        tokens = oidc.exchange_code(
            request.GET["code"], state["verifier"], state["redirect_uri"]
        )
        access = check_stored_token(tokens["access_token"])
        id_claims = verify_id_token(
            tokens["id_token"],
            keys=oidc.signing_keys(),
            issuer=oidc.issuer(),
            client_id=oidc.client_id(),
            nonce=state["nonce"],
            access=access,
            now=time.time(),
            leeway=settings.IHR_OIDC_LEEWAY_SECONDS,
        )
    except (oidc.GrantRejectedError, TokenRejectedError, KeyError) as exc:
        logger.info("staff sign-in refused: %s", exc)
        return _to_login("refused")
    except oidc.KeycloakUnavailableError:
        return _to_login("unavailable")

    candidate = sessions.StaffSession(
        tid=connection.schema_name,
        user_id=0,
        sub=access["sub"],
        sid=access["sid"],
        created=time.time(),
        auth_time=id_claims["auth_time"],
        access=tokens["access_token"],
        access_exp=access["exp"],
        access_iat=access["iat"],
        refresh=tokens["refresh_token"],
    )
    from ihr_identity.join import pending_join

    joining = pending_join(request, candidate.sub)
    if joining is not None:
        return joining  # an existing account joining this hospital (P1-T5e1): no session yet
    identity = _linked_identity(
        candidate, email_verified=id_claims.get("email_verified") is True
    )
    if identity is None:
        # Not staff here (or not any more): the tokens are dropped, but the person's sign-in
        # at their other hospitals is left alone.
        return _to_login("not_linked")
    valid_after = identity.sessions_valid_after
    if valid_after and id_claims["auth_time"] < valid_after.timestamp():
        # Single sign-on from before a sign-out here: ask for the password and code again.
        oidc.end_keycloak_session(candidate.refresh)
        if state["reauth"]:
            return _to_login("reauth")
        return HttpResponseRedirect(
            f"/api/ihr/auth/login/?{urlencode({'reauth': '1', 'next': state['next']})}"
        )
    fields = {
        k: v
        for k, v in vars(candidate).items()
        if k not in {"cookie", "created", "user_id"}
    }
    session = sessions.create(user_id=identity.user_id, **fields)
    response = HttpResponseRedirect(state["next"])
    response.set_cookie(
        settings.IHR_SESSION_COOKIE,
        session.cookie,
        samesite="Lax",  # arrives after Keycloak's cross-site redirect; CSRF is checked by header
        **_cookie_args(settings.IHR_SESSION_MAX_SECONDS),
    )
    return response


def _linked_identity(candidate, *, email_verified=False):
    from ihr_identity.models import StaffIdentity

    identity = (
        StaffIdentity.objects.select_related("user")
        .filter(keycloak_sub=candidate.sub)
        .first()
    )
    if identity is None:
        return None
    audit.bind_actor(identity.user)  # whatever this sign-in writes is theirs
    if identity.awaiting_setup and email_verified:
        _finish_setup(identity)
    candidate.user_id = identity.user_id
    # The same rules as every later request, apart from the "signed out since" rule, which
    # decides between a fresh sign-in and refusal above.
    probe = sessions.StaffSession(**{**vars(candidate), "created": time.time()})
    return identity if identity_for(probe) else None


def _finish_setup(identity):
    """First sign-in after the setup e-mail (P1-T5c): the person proved the address and
    chose their password and authenticator (this sign-in passed both), so their account
    here becomes active. Once only: the flag is cleared in the same update."""
    from django.contrib.auth import get_user_model

    from ihr_identity.models import StaffIdentity

    if StaffIdentity.objects.filter(
        pk=identity.pk, awaiting_setup=True, join_confirmation_required=False
    ).update(awaiting_setup=False):
        get_user_model().objects.filter(
            pk=identity.user_id,
            deleted=False,
            is_superuser=False,
            is_service_account=False,
        ).update(is_active=True)
        identity.user.refresh_from_db()
        from ihr_platform.models import StaffInvitation

        StaffInvitation.objects.filter(
            hospital=connection.tenant,
            care_user_id=identity.user_id,
            status=StaffInvitation.Status.SENT,
        ).update(status=StaffInvitation.Status.ACCEPTED, accepted_at=timezone.now())
        logger.info("staff account %s activated by first sign-in", identity.user_id)


@csrf_exempt  # our own header check below; Django's CSRF cookie is not used
@never_cache
@require_POST
def logout(request):
    try:
        check_csrf(request)
    except PermissionDenied as exc:
        return JsonResponse({"detail": str(exc.detail)}, status=403)
    everywhere = (
        False  # no session here: Keycloak was not told (the web app can fall back)
    )
    session = sessions.load(request.COOKIES.get(settings.IHR_SESSION_COOKIE, ""))
    if session is not None and session.tid == connection.schema_name:
        everywhere = end_session(request, session, everywhere=True)
    response = JsonResponse({"signed_out_everywhere": everywhere})
    response.delete_cookie(settings.IHR_SESSION_COOKIE, path="/")
    return response


class SessionView(APIView):
    """Who is signed in at this hospital. Never returns tokens."""

    authentication_classes = [StaffSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        session = sessions.load(request.COOKIES.get(settings.IHR_SESSION_COOKIE))
        if session is None:  # ended by this very request (e.g. at refresh)
            return Response(status=403)
        claims = check_stored_token(session.access)
        now = time.time()
        return Response(
            {
                "username": request.user.username,
                "hospital": connection.tenant.slug,
                "organisation": claims["ihr_org"]["id"],
                "second_factor": claims["acr"] == "mfa",
                "access_token_expires_in": int(session.access_exp - now),
                "idle_seconds_left": int(
                    settings.IHR_SESSION_IDLE_SECONDS
                    - (now - sessions.last_activity(session))
                ),
                "session_seconds_left": int(
                    settings.IHR_SESSION_MAX_SECONDS - (now - session.created)
                ),
            }
        )
