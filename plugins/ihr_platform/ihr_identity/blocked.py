"""CARE routes switched off on hospital addresses (ADR-009, owner's decision of 2026-11-07).

Staff sign in only through Keycloak, so CARE's own password sign-in, tokens, two-factor and
password reset are gone, and so is the Django admin (a password login). Machine-account tokens
are created by a platform command only (TENANCY_DESIGN §4.8), never through the API. Patient
OTP sign-in is untouched. Batch sub-requests resolve through the same URLconf, so they are
blocked too.
"""

from django.conf import settings
from django.urls import re_path
from rest_framework.response import Response
from rest_framework.views import APIView


class Blocked(APIView):
    """404 for every method, also inside batch sub-requests (which read ``response.data``)."""

    authentication_classes = []
    permission_classes = []

    def _not_found(self, request, *args, **kwargs):
        return Response({"detail": "Not found."}, status=404)

    get = post = put = patch = delete = head = options = _not_found


# Each ends in (?:[/.]|$) so DRF's format-suffix routes (".json", DEBUG only) are caught too.
END = r"(?:[/.]|$)"
BLOCKED_PREFIXES = [
    rf"^api/v1/auth/(?:login|logout|token){END}",
    rf"^api/v1/mfa{END}",
    rf"^api/v1/password_reset{END}",
    rf"^api/v1/password_change{END}",
    rf"^api/v1/otp/password_reset{END}",
    rf"^api/v1/users/[^/]+/generate_service_account_token{END}",
    # The format-suffix twin of "Add user" (DEBUG only) would skip the invitation (P1-T5e1)
    r"^api/v1/users\.[^/]+/?$",
    # CARE's bulk "upsert" creates users too, outside the invitation (review of P1-T5e1)
    rf"^api/v1/users/upsert{END}",
]


def blocked_urlpatterns():
    admin = settings.ADMIN_URL.strip("/")
    patterns = [*BLOCKED_PREFIXES, rf"^{admin}{END}"]
    return [re_path(pattern, Blocked.as_view()) for pattern in patterns]
