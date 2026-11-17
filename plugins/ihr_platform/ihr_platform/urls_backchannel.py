"""URLs of the back-channel service (P1-T23, Q-K95): Keycloak's logout post and nothing else.

A sign-out holds a backend thread while Keycloak posts the back-channel logout back to the
platform host (ADR-009 §78). When both land in the same eight threads, eight sign-outs at once
(a shift change, many idle time-outs) leave no thread for the posts: the logout tokens are lost
and the other hospitals notice only at the one-minute token refresh. The ``backchannel`` service
of deploy/staging/compose.yaml runs this URL set in its own small gunicorn (``IHR_SERVICE=
backchannel``, ihr_platform.settings.tenancy), reached only from Keycloak over the internal
network. Every other path answers 404, and a hospital's own address serves nothing at all
(urls_none.py).
"""

from django.http import JsonResponse
from django.urls import path
from ihr_identity import backchannel

from ihr_platform.urls_public import not_found, ping, server_error

handler404 = not_found
handler500 = server_error


def health(request):
    """A plain liveness answer; no database, so a slow database never marks it down."""
    return JsonResponse({"status": "OK", "service": "backchannel"})


urlpatterns = [
    path("ping/", ping),
    path("health/", health),
    path("api/ihr/backchannel-logout/", backchannel.logout, name="ihr-backchannel"),
]
