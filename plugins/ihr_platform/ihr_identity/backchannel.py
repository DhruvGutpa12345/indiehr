"""Keycloak's back-channel logout (P1-T5d, OIDC Back-Channel Logout 1.0).

    POST <IHR_BACKCHANNEL_LOGOUT_URL>   logout_token=<JWT>   (platform host, from Keycloak)

When a sign-in ends in Keycloak (sign-out or idle time-out at any hospital, or an admin ending
it), Keycloak posts a signed logout token for every hospital client that sign-in reached. The
token's audience names the hospital; its ``sid`` names the sign-in, whose sessions at that
hospital are refused from their next request (``sessions.end_sign_in``). Before this, other
hospitals noticed only at their next token refresh, up to a minute later (ADR-009).

The address is one internal URL on the platform host for every hospital (Keycloak cannot reach
the hospitals' public addresses from inside the network). The token is the only credential:
it must be signed by the realm, for exactly one hospital client, recent and for a sign-in.
"""

import logging
import time

import jwt
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_tenants.utils import schema_context
from ihr_platform.keycloak.hospital import client_id_for
from ihr_platform.models import Hospital

from ihr_identity import oidc, sessions
from ihr_identity.tokens import TokenRejectedError, verify_logout_token

logger = logging.getLogger(__name__)
MAX_TOKEN = 8192
PREFIX = client_id_for("")


def _refused():
    return JsonResponse({"error": "invalid_request"}, status=400)


def _hospital_named(token):
    """The hospital whose client the token is addressed to (checked again when verified)."""
    try:
        aud = jwt.decode(token, options={"verify_signature": False}).get("aud")
    except jwt.PyJWTError:
        return None
    if isinstance(aud, list) and len(aud) == 1:
        aud = aud[0]
    if not isinstance(aud, str) or not aud.startswith(PREFIX):
        return None
    return (
        Hospital.objects.exclude(schema_name="public")
        .filter(slug=aud.removeprefix(PREFIX))
        .first()
    )


@csrf_exempt  # a server-to-server call; the signed token is its only credential
@never_cache
@require_POST
def logout(request):
    token = request.POST.get("logout_token", "")
    if not token or len(token) > MAX_TOKEN:
        return _refused()
    hospital = _hospital_named(token)
    if hospital is None:
        return _refused()
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        claims = verify_logout_token(
            token,
            keys=oidc.signing_keys(kid),
            issuer=oidc.issuer(),
            client_id=client_id_for(hospital.slug),
            now=time.time(),
            leeway=settings.IHR_OIDC_LEEWAY_SECONDS,
        )
    except (TokenRejectedError, jwt.PyJWTError) as exc:
        logger.info("back-channel logout refused: %s", exc)
        return _refused()
    except oidc.KeycloakUnavailableError:
        return JsonResponse({"error": "temporarily_unavailable"}, status=503)
    with schema_context(hospital.schema_name):
        sessions.end_sign_in(claims["sid"])
    logger.info("back-channel logout: a sign-in ended at %s", hospital.slug)
    return HttpResponse(status=200)
