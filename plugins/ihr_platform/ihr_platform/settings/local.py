"""CARE's local settings in tenant mode (scripts/care-dev.sh)."""

import os

from config.settings.local import *  # noqa: F403

from ihr_platform.settings.tenancy import apply_tenancy

# Dev: hospitals at http://<slug>.localhost:8080 (browsers resolve *.localhost to this machine).
IHR_TENANT_DOMAIN_SUFFIX = "localhost"
IHR_FRONTEND_URL_TEMPLATE = "http://{domain}:8080"
# Keycloak (P1-T3): the browser address and the address inside the compose network.
IHR_KEYCLOAK_PUBLIC_URL = "http://auth.localhost:8081"
IHR_KEYCLOAK_URL = "http://keycloak:8080"
# Keycloak reaches the backend inside the compose network ("backend" is a platform address).
IHR_BACKCHANNEL_LOGOUT_URL = "http://backend:9000/api/ihr/backchannel-logout/"
# Dev runs on plain http://*.localhost: no __Host- prefix, no Secure flag (ADR-009).
IHR_SESSION_COOKIE = "ihr_session"
IHR_LOGIN_COOKIE = "ihr_login"
IHR_JOIN_COOKIE = "ihr_join"
IHR_JOIN_CONFIRM_COOKIE = "ihr_join_ok"
# Django's own e-mails (the "join this hospital" e-mail, P1-T5e1) also go to Mailpit.
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "mailpit"
EMAIL_PORT = 1025
EMAIL_USE_TLS = False
DEFAULT_FROM_EMAIL = "IndieHR <no-reply@indiehr.localhost>"
IHR_SESSION_COOKIE_SECURE = False
# Operator console (P1-T5b), straight to the backend on the published port.
IHR_CONSOLE_URL = "http://console.localhost:9000"
IHR_OPS_SESSION_COOKIE = "ihr_ops"
IHR_OPS_LOGIN_COOKIE = "ihr_ops_login"
# Setup e-mails go to Mailpit (inbox http://127.0.0.1:8025), never to a real mailbox.
IHR_KEYCLOAK_SMTP = {
    "host": "mailpit",
    "port": "1025",
    "from": "no-reply@indiehr.localhost",
    "fromDisplayName": "IndieHR",
}
IHR_SESSION_IDLE_SECONDS = int(os.environ.get("IHR_SESSION_IDLE_SECONDS", "1800"))

apply_tenancy(globals())

# Only the terminology benchmark sets this (scripts/terminology-scale-bench.sh), against its own
# bench_* database; the loader also checks the database name (P1-T10a2)
IHR_TERMINOLOGY_THROWAWAY_DB = os.environ.get("IHR_TERMINOLOGY_THROWAWAY_DB") == "1"
