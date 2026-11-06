"""CARE's local settings in tenant mode (scripts/care-dev.sh)."""

from config.settings.local import *  # noqa: F403

from ihr_platform.settings.tenancy import apply_tenancy

# Dev: hospitals at http://<slug>.localhost:8080 (browsers resolve *.localhost to this machine).
IHR_TENANT_DOMAIN_SUFFIX = "localhost"
IHR_FRONTEND_URL_TEMPLATE = "http://{domain}:8080"

apply_tenancy(globals())
