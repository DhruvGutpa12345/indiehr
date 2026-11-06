"""URLs served on hospital hosts: our routes under /api/ihr/, then all of CARE's."""

from config.urls import urlpatterns as care_urlpatterns
from django.urls import include, path

urlpatterns = [
    path("api/ihr/", include("ihr_tenant_settings.urls")),
    *care_urlpatterns,
]
