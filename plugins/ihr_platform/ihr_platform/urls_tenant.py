"""URLs served on hospital hosts: blocked CARE routes, our routes under /api/ihr/, then CARE's."""

from config.urls import urlpatterns as care_urlpatterns
from django.urls import include, path, re_path
from ihr_identity.blocked import blocked_urlpatterns
from ihr_identity.invitations import InvitingUserViewSet

from ihr_platform import route_swaps

urlpatterns = [
    *blocked_urlpatterns(),  # CARE's own staff sign-in is off (ADR-009); must come first
    # CARE's "Add user" creates an invitation instead (P1-T5e1); same list and create API
    re_path(
        r"^api/v1/users/$",
        InvitingUserViewSet.as_view({"get": "list", "post": "create"}),
        name="users-list",
    ),
    path("api/ihr/auth/", include("ihr_identity.urls")),
    path("api/ihr/join/", include("ihr_identity.join_urls")),
    # Hospital admins: reset a lost authenticator, resend or cancel an invitation (P1-T5e2)
    path("api/ihr/staff/", include("ihr_identity.staff_urls")),
    path("api/ihr/audit/", include("ihr_audit.urls")),
    path("api/ihr/", include("ihr_tenant_settings.urls")),
    *care_urlpatterns,
]

# CARE's router (imported above by config.urls) must serve our classes, never CARE's own: a
# swap missed after an upstream sync stops here, in every process (P1-T20a, review C4)
route_swaps.require()
