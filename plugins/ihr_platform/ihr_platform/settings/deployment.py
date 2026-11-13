"""Staging and production settings in tenant mode (P1-T8a).

    DJANGO_SETTINGS_MODULE=ihr_platform.settings.deployment

Everything secret or site-specific comes from the environment (deploy/staging/env.example).
The process refuses to start while anything in ``secure.problems()`` is wrong, and names
every problem at once.
"""

import os

# CARE's settings start Sentry while they load, before our check below could stop it: take the
# address out first, so nothing (not even this refusal) is sent outside India.
_SENTRY_DSN_GIVEN = os.environ.pop("SENTRY_DSN", "")

from config.settings.deployment import *  # noqa: E402, F403
from config.settings.deployment import env  # noqa: E402
from django.core.exceptions import ImproperlyConfigured  # noqa: E402

from ihr_platform.settings import secure  # noqa: E402
from ihr_platform.settings.tenancy import apply_tenancy  # noqa: E402

SENTRY_DSN = _SENTRY_DSN_GIVEN  # only for the check: Sentry was never started

# CARE's deployment logging switches off every logger that exists before Django configures
# logging: gunicorn's (worker errors, the path-only access log of compose.yaml), django.request
# (500s and their tracebacks), Celery's task results. With Sentry refused (secure.py) a server
# error then left no trace at all (P1-T9b). They stay on; the log pipeline masks values.
LOGGING["disable_existing_loggers"] = False  # noqa: F405
# A label only. Every deployment runs CARE in production mode: outside it, CARE gives every
# patient the same sign-in code (45612) and publishes its API schema to anyone.
IHR_ENVIRONMENT = env("IHR_ENVIRONMENT", default="staging")
IS_PRODUCTION = True

# Hospital addresses <slug>.<suffix> and the other public hosts (TENANCY_DESIGN §9, Q-T11).
IHR_TENANT_DOMAIN_SUFFIX = env("IHR_TENANT_DOMAIN_SUFFIX", default="")
IHR_FRONTEND_URL_TEMPLATE = "https://{domain}"
IHR_KEYCLOAK_PUBLIC_URL = env("IHR_KEYCLOAK_PUBLIC_URL", default="")
IHR_KEYCLOAK_URL = env("IHR_KEYCLOAK_URL", default="")
IHR_CONSOLE_URL = env("IHR_CONSOLE_URL", default="")
IHR_BACKCHANNEL_LOGOUT_URL = env("IHR_BACKCHANNEL_LOGOUT_URL", default="")
IHR_SESSION_IDLE_SECONDS = env.int("IHR_SESSION_IDLE_SECONDS", default=1800)
# Client keys (P1-T8d2): the key this container holds, if any (backend, onboarding, staff).
IHR_CLIENT_KEY_PURPOSE = env("IHR_CLIENT_KEY_PURPOSE", default="")
IHR_CLIENT_KEY_DIR = env("IHR_CLIENT_KEY_DIR", default="/run/ihr-keys")
IHR_CLIENT_JWKS_DIR = env("IHR_CLIENT_JWKS_DIR", default="/run/ihr-jwks")
# Separate database roles (P1-T19): "off" until P1-T19b moves staging's roles, then
# "required"; the role this container declares it connects as (app or owner) is checked on
# its first database connection.
IHR_DB_ROLES = env("IHR_DB_ROLES", default="off")
IHR_DB_ROLE = env("IHR_DB_ROLE", default="")
# The operator console posts forms (Django CSRF): its https origin is trusted.
CSRF_TRUSTED_ORIGINS = [IHR_CONSOLE_URL] if IHR_CONSOLE_URL else []

# TLS ends at Traefik, which also redirects http to https and sends X-Forwarded-Proto.
# Django does not redirect again: Keycloak's back-channel logout and the health checks
# reach the backend over the internal network in plain http.
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=31536000)
# Preloading would bind the owner's whole domain to https in browsers for good: never by default.
SECURE_HSTS_PRELOAD = env.bool("DJANGO_SECURE_HSTS_PRELOAD", default=False)

# Keycloak's setup e-mails (P1-T5c): the realm's mail server, from ihr_keycloak_sync --realm.
if os.environ.get("IHR_KEYCLOAK_SMTP_HOST"):
    IHR_KEYCLOAK_SMTP = {
        "host": os.environ["IHR_KEYCLOAK_SMTP_HOST"],
        "port": os.environ.get("IHR_KEYCLOAK_SMTP_PORT", "587"),
        "from": os.environ.get("IHR_KEYCLOAK_SMTP_FROM", ""),
        "fromDisplayName": "IndieHR",
        "starttls": "true",
        "auth": "true",
        "user": os.environ.get("IHR_KEYCLOAK_SMTP_USER", ""),
    }

apply_tenancy(globals())

_problems = secure.problems(globals(), os.environ)
_problems += secure.client_key_problems(IHR_CLIENT_KEY_PURPOSE, IHR_CLIENT_KEY_DIR)
if _problems:
    raise ImproperlyConfigured(
        "Refusing to start with unsafe settings:\n- " + "\n- ".join(_problems)
    )
