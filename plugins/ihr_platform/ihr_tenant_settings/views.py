"""Hospital branding for the web app, by host (TENANCY_DESIGN.md §9).

``bootstrap`` and ``bootstrap.js`` are public: the login page needs the hospital's name, colour
and logo before anyone signs in. They return branding only. The full profile (GSTIN, address,
registration numbers) is for signed-in staff, as is the PIN code lookup (P1-T7a; also a patient
signed in by OTP on the appointment page, P1-T7b).
"""

import json

from care.emr.resources.organization.spec import OrganizationReadSpec
from config.patient_otp_authentication import JWTTokenPatientAuthentication
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from ihr_platform import pincodes
from ihr_platform.models import PostOffice
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from ihr_tenant_settings import geography
from ihr_tenant_settings.branding import default_logo_svg
from ihr_tenant_settings.device_time import WARNING_SCRIPT
from ihr_tenant_settings.india import GST_STATE_CODES
from ihr_tenant_settings.models import HospitalProfile
from ihr_tenant_settings.validators import PIN_RE

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
    # The tab shows the hospital's name from the first paint (pages then add their own titles).
    # Idle time-out for the web app's warning (P1-T4b, ADR-009); the backend enforces it too.
    auth = json.dumps(
        {
            "idle_seconds": settings.IHR_SESSION_IDLE_SECONDS,
            "warn_seconds": min(120, settings.IHR_SESSION_IDLE_SECONDS // 2),
        }
    )
    body = (
        f"window.__IHR_BRANDING__ = {data};\n"
        f"window.__IHR_AUTH__ = {auth};\n"
        "document.title = window.__IHR_BRANDING__.short_name;\n"
    )
    # A bar until the computer is set to India Standard Time (P1-T7b).
    body += WARNING_SCRIPT
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


class PinCodeView(APIView):
    """Where a PIN code is: its state and district(s), the matching geography of this
    hospital (to fill an address form) and its post offices. From our own copy of India
    Post's directory (P1-T7a).

    Open to signed-in staff and to a patient signed in by OTP on this hospital's public
    appointment page (P1-T7b), as CARE's own organisation list is. A patient token is bound
    to the hospital that issued it (``tid``, config/patient_otp_token.py), so another
    hospital's token is refused; anonymous callers are refused. Each place carries the
    full state and district organisations, so the address form needs no second request."""

    authentication_classes = [
        JWTTokenPatientAuthentication,
        *api_settings.DEFAULT_AUTHENTICATION_CLASSES,
    ]
    permission_classes = (IsAuthenticated,)

    def get(self, request, pincode):
        if not PIN_RE.match(pincode):
            return Response(
                {"detail": "PIN code must be 6 digits and cannot start with 0."},
                status=400,
            )
        loaded = pincodes.current()
        if loaded is None:
            return Response({"detail": "The PIN directory is not loaded."}, status=503)
        offices = list(
            PostOffice.objects.filter(pincode=pincode).order_by(
                "state_code", "district", "name"
            )
        )
        if not offices:
            return Response({"detail": "Unknown PIN code."}, status=404)
        places = []
        for code, district in dict.fromkeys(
            (o.state_code, o.district) for o in offices
        ):
            state_org, district_org = geography.find(code, district)
            places.append(
                {
                    "state_code": code,
                    "state": GST_STATE_CODES[code],
                    "district": district,
                    "state_organization": str(state_org.external_id)
                    if state_org
                    else None,
                    "geo_organization": str(district_org.external_id)
                    if district_org
                    else None,
                    "state_organization_object": _organization(state_org),
                    "geo_organization_object": _organization(district_org),
                }
            )
        return Response(
            {
                "pincode": pincode,
                "places": places,
                "post_offices": [
                    {
                        "name": o.name,
                        "type": o.office_type,
                        "delivery": o.delivery,
                        "district": o.district,
                        "state_code": o.state_code,
                    }
                    for o in offices
                ],
                "source": {
                    "name": "India Post, All India Pincode Directory (data.gov.in, GODL-India)",
                    "released_on": loaded.released_on,
                },
            }
        )


def _organization(org):
    """An organisation as CARE's organisation list returns it (with its parent chain)."""
    return OrganizationReadSpec.serialize(org).to_json() if org else None
