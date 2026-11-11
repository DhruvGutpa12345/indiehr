"""URLs served on the platform (public schema) hosts: no CARE routes (TENANCY_DESIGN.md §3);
the operator console lives under /ops/ (P1-T5b), Keycloak's back-channel logout at
/api/ihr/backchannel-logout/ (P1-T5d).

Errors are plain JSON: CARE's HTML error pages link to CARE routes that do not exist here, so
rendering them would itself fail (a 404 became a 500).
"""

from django.http import JsonResponse
from django.urls import include, path
from ihr_identity import backchannel


def ping(request):
    return JsonResponse({"status": "OK", "tenant": None})


def not_found(request, exception=None):
    return JsonResponse({"detail": "Not found"}, status=404)


def server_error(request):
    return JsonResponse({"detail": "Server error"}, status=500)


handler404 = not_found
handler500 = server_error

urlpatterns = [
    path("ping/", ping),
    # CARE's own health check (database, cache, Celery queue), for the monitoring probe on the
    # internal address backend:9000 (P1-T9a); Traefik never routes /health from outside
    path("health/", include("healthy_django.urls", namespace="healthy_django")),
    path("ops/", include("ihr_platform.console.urls")),  # operator console (P1-T5b)
    # From Keycloak only: a sign-in ended (P1-T5d)
    path("api/ihr/backchannel-logout/", backchannel.logout, name="ihr-backchannel"),
]
