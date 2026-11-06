"""URLs served on the platform (public schema) hosts: no CARE routes (TENANCY_DESIGN.md §3).

Errors are plain JSON: CARE's HTML error pages link to CARE routes that do not exist here, so
rendering them would itself fail (a 404 became a 500).
"""

from django.http import JsonResponse
from django.urls import path


def ping(request):
    return JsonResponse({"status": "OK", "tenant": None})


def not_found(request, exception=None):
    return JsonResponse({"detail": "Not found"}, status=404)


def server_error(request):
    return JsonResponse({"detail": "Server error"}, status=500)


handler404 = not_found
handler500 = server_error

urlpatterns = [path("ping/", ping)]
