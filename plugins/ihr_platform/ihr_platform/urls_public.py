"""URLs served on the platform (public schema) hosts: no CARE routes (TENANCY_DESIGN.md §3)."""

from django.http import JsonResponse
from django.urls import path


def ping(request):
    return JsonResponse({"status": "OK", "tenant": None})


urlpatterns = [path("ping/", ping)]
