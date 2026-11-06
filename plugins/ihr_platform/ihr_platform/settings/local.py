"""CARE's local settings in tenant mode (scripts/care-dev.sh)."""

from config.settings.local import *  # noqa: F403

from ihr_platform.settings.tenancy import apply_tenancy

# Dev: hospitals at http://<slug>.localhost:8080 (browsers resolve *.localhost to this machine).
IHR_TENANT_DOMAIN_SUFFIX = "localhost"
IHR_FRONTEND_URL_TEMPLATE = "http://{domain}:8080"
# Keycloak (P1-T3): the browser address and the address inside the compose network.
IHR_KEYCLOAK_PUBLIC_URL = "http://auth.localhost:8081"
IHR_KEYCLOAK_URL = "http://keycloak:8080"
# Also allow the dev token viewer (http://<slug>.localhost:8082/) as a login redirect.
IHR_KEYCLOAK_DEV_REDIRECTS = True

apply_tenancy(globals())
