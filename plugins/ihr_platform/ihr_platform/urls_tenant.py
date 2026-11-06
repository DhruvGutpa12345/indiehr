"""URLs served on hospital hosts: blocked CARE routes, our routes under /api/ihr/, then CARE's."""

from config.urls import urlpatterns as care_urlpatterns
from django.urls import include, path
from ihr_identity.blocked import blocked_urlpatterns

urlpatterns = [
    *blocked_urlpatterns(),  # CARE's own staff sign-in is off (ADR-009); must come first
    path("api/ihr/auth/", include("ihr_identity.urls")),
    path("api/ihr/", include("ihr_tenant_settings.urls")),
    *care_urlpatterns,
]
