"""Settings of the audit watch (P1-T21): the audit-watch container of compose.monitoring.yaml.

    DJANGO_SETTINGS_MODULE=ihr_platform.settings.watch

The watch reads the audit trail and the fingerprint bucket; it serves no web request and signs
nothing. So it never gets the web app's secrets (secrets/app.env: the Django secret key, Redis,
CARE's JWKS, storage keys): these are CARE's base settings in tenant mode, with a throwaway
secret key, no Redis (a local cache) and its own checks (``secure.watch_problems``), which name
every problem at once and refuse to start. Its database role is ``ihr_audit_watch``
(secrets/audit-watch.env), and every session it opens is read-only with a statement timeout.
"""

import copy
import os
import secrets

# As deployment.py: nothing may start Sentry, not even this refusal
_SENTRY_DSN_GIVEN = os.environ.pop("SENTRY_DSN", "")

from config.settings.base import *  # noqa: E402, F403
from config.settings.base import env  # noqa: E402
from django.core.exceptions import ImproperlyConfigured  # noqa: E402

from ihr_platform.settings import secure  # noqa: E402
from ihr_platform.settings.tenancy import apply_tenancy  # noqa: E402

SENTRY_DSN = _SENTRY_DSN_GIVEN
IHR_ENVIRONMENT = env("IHR_ENVIRONMENT", default="staging")
IS_PRODUCTION = True
DEBUG = False
# Signs nothing (no sessions, no tokens): a new random one each start, shared with nobody
SECRET_KEY = secrets.token_urlsafe(64)
ALLOWED_HOSTS = []  # it serves no Django request (its metrics are a plain HTTP server)
IHR_TENANT_DOMAIN_SUFFIX = env("IHR_TENANT_DOMAIN_SUFFIX", default="")
IHR_DB_ROLES = env("IHR_DB_ROLES", default="required")
IHR_DB_ROLE = env("IHR_DB_ROLE", default="")
SNOWSTORM_DEPLOYMENT_URL = "http://127.0.0.1:9/fhir"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

DATABASES = {"default": env.db("DATABASE_URL", default="")}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"]["CONN_MAX_AGE"] = 0
DATABASES["default"].setdefault("OPTIONS", {}).update(
    {
        "application_name": "ihr-audit-watch",
        # Read-only sessions, and no statement or idle transaction that can hold anything up
        "options": (
            "-c default_transaction_read_only=on -c statement_timeout=300000 "
            "-c idle_in_transaction_session_timeout=60000 -c lock_timeout=10000"
        ),
    }
)

apply_tenancy(globals())

# One record, one line (P1-T21 review): text from the fingerprint bucket (an object key, a
# body, an error) can never start a line of its own and forge the watch's fixed line, which
# the log alerts accept from this service (ihr_audit/oneline.py)
LOGGING = copy.deepcopy(LOGGING)  # noqa: F405
LOGGING["formatters"] = {
    name: {
        "()": "ihr_audit.oneline.OneLineFormatter",
        "fmt": spec["format"],
        **({"datefmt": spec["datefmt"]} if "datefmt" in spec else {}),
    }
    for name, spec in LOGGING["formatters"].items()
}

_problems = secure.watch_problems(globals(), os.environ)
if _problems:
    raise ImproperlyConfigured(
        "Refusing to start the audit watch with unsafe settings:\n- "
        + "\n- ".join(_problems)
    )
