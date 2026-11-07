"""Joining another hospital with an account one already has (P1-T5e1, owner's decision:
"sign in, then confirm").

    GET  /api/ihr/join/?t=<token>   the e-mailed link: remembered, then on to the normal sign-in
    GET  /api/ihr/join/confirm/      "Join <hospital>?" (after signing in with password + code)
    POST /api/ihr/join/confirm/      joins: the CARE user becomes active; then a normal sign-in

The invitation already made the person a member in Keycloak (so they can sign in here), but
their CARE user stays inactive and no session is created until all three hold: the link from
the e-mail (proves the mailbox), a sign-in at this hospital (proves the account) and the click.
"""

import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods
from ihr_platform.links import hospital_origins
from ihr_platform.models import StaffInvitation

from ihr_identity.models import StaffIdentity
from ihr_identity.sessions import seal, unseal

LINK_SECONDS = 1800  # from opening the link to signing in
CONFIRM_SECONDS = 600  # from signing in to clicking Join
CONFIRM_PATH = "/api/ihr/join/confirm/"


def _secret():
    return settings.SECRET_KEY.encode()


def _cookie(response, name, value, max_age):
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path="/",
        secure=settings.IHR_SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",  # the sign-in comes back through Keycloak's top-level redirect
    )


def _to_login(error):
    response = HttpResponseRedirect(f"/login?{urlencode({'error': error})}")
    for name in (settings.IHR_JOIN_COOKIE, settings.IHR_JOIN_CONFIRM_COOKIE):
        response.delete_cookie(name, path="/")
    return response


def _open_invitation(**filters):
    return StaffInvitation.objects.filter(
        hospital=connection.tenant,
        kind=StaffInvitation.Kind.JOIN,
        status=StaffInvitation.Status.SENT,
        expires_at__gt=timezone.now(),
        **filters,
    ).first()


def _state(request, name, purpose, max_age):
    text = request.COOKIES.get(name)
    state = unseal(text, _secret(), purpose) if text else None
    if (
        not state
        or state.get("tid") != connection.schema_name
        or time.time() - state.get("iat", 0) > max_age
    ):
        return None
    return state


@never_cache
@require_GET
def start(request):
    token = request.GET.get("t", "")
    digest = hashlib.sha256(token.encode()).hexdigest() if token else None
    invitation = _open_invitation(join_token_hash=digest) if digest else None
    if invitation is None:
        return _to_login("join_link")
    response = HttpResponseRedirect(
        f"/api/ihr/auth/login/?{urlencode({'next': CONFIRM_PATH})}"
    )
    state = {
        "inv": str(invitation.pk),
        "tid": connection.schema_name,
        "iat": time.time(),
    }
    _cookie(
        response,
        settings.IHR_JOIN_COOKIE,
        seal(state, _secret(), "ihr-join"),
        LINK_SECONDS,
    )
    return response


def pending_join(request, sub):
    """Called by the sign-in callback once Keycloak's tokens are verified. None if ``sub``
    has no join waiting here; otherwise where to go next (never a session)."""
    identity = StaffIdentity.objects.filter(
        keycloak_sub=sub, join_confirmation_required=True
    ).first()
    if identity is None:
        return None
    state = _state(request, settings.IHR_JOIN_COOKIE, "ihr-join", LINK_SECONDS)
    invitation = (
        _open_invitation(
            pk=state["inv"], care_user_id=identity.user_id, keycloak_sub=sub
        )
        if state
        else None
    )
    if invitation is None:
        return _to_login("join_link")  # signed in, but not through this person's link
    response = HttpResponseRedirect(CONFIRM_PATH)
    confirm = {
        "inv": str(invitation.pk),
        "sub": str(sub),
        "user": identity.user_id,
        "tid": connection.schema_name,
        "iat": time.time(),
        "nonce": secrets.token_urlsafe(24),
    }
    _cookie(
        response,
        settings.IHR_JOIN_CONFIRM_COOKIE,
        seal(confirm, _secret(), "ihr-join-confirm"),
        CONFIRM_SECONDS,
    )
    response.delete_cookie(settings.IHR_JOIN_COOKIE, path="/")
    return response


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Join {hospital}</title>
<meta name="color-scheme" content="light">
<style>html{{background:#f9fafb}}body{{font:16px/1.5 system-ui,sans-serif;max-width:32rem;
margin:3rem auto;padding:0 1rem;color:#1f2937}}a{{color:#1d4ed8}}
button{{font:inherit;padding:.5rem 1.2rem;margin-right:1rem}}</style></head><body>
<h1>Join {hospital}?</h1>
<p>{hospital} has invited <strong>{name}</strong> to work with them. Your password and
authenticator stay the same; this hospital gets its own record of you.</p>
<form method="post" action="{action}"><input type="hidden" name="nonce" value="{nonce}">
<button type="submit">Join {hospital}</button><a href="/login">Not now</a></form>
</body></html>"""


@csrf_exempt  # our own checks: same origin and the nonce from the sealed cookie
@never_cache
@require_http_methods(["GET", "POST"])
def confirm(request):
    state = _state(
        request, settings.IHR_JOIN_CONFIRM_COOKIE, "ihr-join-confirm", CONFIRM_SECONDS
    )
    invitation = (
        _open_invitation(
            pk=state["inv"], care_user_id=state["user"], keycloak_sub=state["sub"]
        )
        if state
        else None
    )
    if invitation is None:
        return _to_login("join_link")
    if request.method == "GET":
        from ihr_tenant_settings.models import HospitalProfile

        hospital = HospitalProfile.current().display_name or connection.tenant.name
        name = f"{invitation.first_name} {invitation.last_name}".strip()
        return HttpResponse(
            format_html(
                PAGE,
                hospital=hospital,
                name=name,
                action=CONFIRM_PATH,
                nonce=state["nonce"],
            ),
            content_type="text/html; charset=utf-8",
            headers={
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "same-origin",
            },
        )
    if not _same_origin(request) or not hmac.compare_digest(
        str(request.POST.get("nonce", "")), state["nonce"]
    ):
        return HttpResponse("Refused.", status=403, content_type="text/plain")
    with transaction.atomic():
        joined = StaffIdentity.objects.filter(
            user_id=state["user"],
            keycloak_sub=state["sub"],
            join_confirmation_required=True,
        ).update(awaiting_setup=False, join_confirmation_required=False)
        if not joined:
            return _to_login("join_link")
        get_user_model().objects.filter(
            pk=state["user"],
            deleted=False,
            is_superuser=False,
            is_service_account=False,
        ).update(is_active=True)
        StaffInvitation.objects.filter(
            pk=invitation.pk, status=StaffInvitation.Status.SENT
        ).update(
            status=StaffInvitation.Status.ACCEPTED,
            accepted_at=timezone.now(),
            join_token_hash="",
        )
    # Single sign-on brings them straight in, now as active staff.
    response = HttpResponseRedirect(f"/api/ihr/auth/login/?{urlencode({'next': '/'})}")
    response.delete_cookie(settings.IHR_JOIN_CONFIRM_COOKIE, path="/")
    return response


def _same_origin(request):
    site = request.headers.get("Sec-Fetch-Site")
    if site is not None and site != "same-origin":
        return False
    origin = request.headers.get("Origin")
    return origin is None or origin in hospital_origins()
