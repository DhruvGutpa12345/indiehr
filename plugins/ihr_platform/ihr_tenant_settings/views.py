"""Hospital branding for the web app, by host (TENANCY_DESIGN.md §9).

``bootstrap`` and ``bootstrap.js`` are public: the login page needs the hospital's name, colour
and logo before anyone signs in. They return branding only. The full profile (GSTIN, address,
registration numbers) is for signed-in staff.
"""

import json

from django.http import HttpResponse, JsonResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ihr_tenant_settings.branding import default_logo_svg
from ihr_tenant_settings.models import HospitalProfile

PLATFORM_NAME = "IndieHR"
PROFILE_FIELDS = (
    "display_name",
    "short_name",
    "legal_name",
    "gstin",
    "address_line1",
    "address_line2",
    "city",
    "district",
    "state_code",
    "pin_code",
    "phone",
    "email",
    "website",
    "clinical_establishment_reg_no",
    "nabh_accreditation_no",
    "hfr_id",
    "rohini_id",
    "letterhead_footer",
    "primary_colour",
    "updated_on",
)
LOGO_PATH = "/api/ihr/logo/"


def _branding(profile):
    return {
        "platform": PLATFORM_NAME,
        "name": profile.display_name,
        "short_name": profile.short_name or profile.display_name,
        "primary_colour": profile.primary_colour,
        # Without an upload, the same path serves a default mark (initials on the colour).
        "logo_url": f"{LOGO_PATH}?v={profile.logo_sha256[:16]}"
        if profile.logo_sha256
        else LOGO_PATH,
    }


def _private_no_cache(response):
    # The answer depends on the Host header: shared caches must never store it.
    response["Cache-Control"] = "private, no-cache"
    return response


def bootstrap(request):
    return _private_no_cache(JsonResponse(_branding(HospitalProfile.current())))


def bootstrap_js(request):
    """The same data as a script the web app loads before its own code (same origin, so CSP-safe)."""
    # JSON is valid JavaScript; also neutralise "<" so the value can never close a script tag.
    data = json.dumps(_branding(HospitalProfile.current())).replace("<", "\\u003c")
    body = f"window.__IHR_BRANDING__ = {data};\n"
    response = HttpResponse(body, content_type="text/javascript; charset=utf-8")
    response["X-Content-Type-Options"] = "nosniff"
    return _private_no_cache(response)


def logo(request):
    profile = HospitalProfile.current(with_logo=True)
    if profile.logo_sha256:
        response = HttpResponse(
            bytes(profile.logo), content_type=profile.logo_content_type
        )
    else:
        response = HttpResponse(
            default_logo_svg(profile), content_type="image/svg+xml; charset=utf-8"
        )
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'"
    if profile.logo_sha256 and request.GET.get("v") == profile.logo_sha256[:16]:
        # Versioned URL: the content for this exact address never changes. Shared caches must
        # still key on the Host (every hospital uses the same path).
        response["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        _private_no_cache(response)
    return response


class ProfileView(APIView):
    """The full hospital profile, for signed-in staff of this hospital."""

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        profile = HospitalProfile.current()
        data = {name: getattr(profile, name) for name in PROFILE_FIELDS}
        data["state_name"] = profile.state_name
        data["branding"] = _branding(profile)
        return Response(data)
