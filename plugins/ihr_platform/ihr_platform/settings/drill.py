"""Settings of the restore drill's one-off (P1-T22b; design "After the attack" 5).

    DJANGO_SETTINGS_MODULE=ihr_platform.settings.drill

The drill restores a hospital inside the restore-drill copy of the database (scripts/staging.sh
hospital-restore-drill), never the live one. So the process that runs it gets nothing that could
reach the live system: no secrets/app.env (the Django secret key, Redis, storage keys), no
fingerprint-bucket keys, no Keycloak credential, and its container has no edge network. These
are CARE's base settings in tenant mode with a throwaway secret key, a local cache instead of
Redis and empty storage settings; ``IHR_RESTORE_DRILL`` makes every step that would reach
outside the database a reported dry run, and ihr_hospital_restore refuses --drill unless the
database itself is the drill copy (cluster_name ihr-restore-drill). Refuses to start if any
live setting reaches it through the environment.
"""

import os
import secrets

_SENTRY_DSN_GIVEN = os.environ.pop("SENTRY_DSN", "")

from config.settings.base import *  # noqa: E402, F403
from config.settings.base import env  # noqa: E402
from django.core.exceptions import ImproperlyConfigured  # noqa: E402

from ihr_platform.settings.tenancy import apply_tenancy  # noqa: E402

# What would reach the live system, if it came in through the environment
# (setting names only, as one string: a quoted list of *_SECRET names reads as keys to a scanner)
LIVE_SETTINGS = tuple(
    """
    REDIS_URL CELERY_BROKER_URL BUCKET_KEY BUCKET_SECRET
    FILE_UPLOAD_KEY FILE_UPLOAD_SECRET FACILITY_S3_KEY FACILITY_S3_SECRET
    IHR_RESTORE_S3_ACCESS_KEY IHR_AUDIT_ANCHOR_WRITE_KEY IHR_AUDIT_ANCHOR_READ_KEY IHR_KEYCLOAK_ADMIN_CLIENT_ID
    IHR_KEYCLOAK_ADMIN_CLIENT_SECRET DJANGO_SECRET_KEY SENTRY_DSN EMAIL_HOST_PASSWORD
    """.split()  # noqa: SIM905 (a list literal of *_SECRET names reads as keys to gitleaks)
)
_found = sorted(name for name in LIVE_SETTINGS if os.environ.get(name)) + (
    ["SENTRY_DSN"] if _SENTRY_DSN_GIVEN else []
)
if _found:
    raise ImproperlyConfigured(
        "Refusing to run the restore drill with live settings in its environment: "
        + ", ".join(_found)
    )

IHR_RESTORE_DRILL = True
IHR_ENVIRONMENT = env("IHR_ENVIRONMENT", default="staging")
IS_PRODUCTION = True
DEBUG = False
SECRET_KEY = secrets.token_urlsafe(64)  # signs nothing; shared with nobody
ALLOWED_HOSTS = []
IHR_TENANT_DOMAIN_SUFFIX = env("IHR_TENANT_DOMAIN_SUFFIX", default="drill.invalid")
IHR_DB_ROLES = env("IHR_DB_ROLES", default="required")
IHR_DB_ROLE = env("IHR_DB_ROLE", default="owner")
SNOWSTORM_DEPLOYMENT_URL = "http://127.0.0.1:9/fhir"
CACHES = {"default": {"BACKEND": "ihr_platform.restore.drill_cache.DrillCache"}}
CELERY_BROKER_URL = "memory://"
CELERY_TASK_ALWAYS_EAGER = False
for _name in ("FILE_UPLOAD", "FACILITY_S3"):
    globals()[f"{_name}_BUCKET"] = ""
    globals()[f"{_name}_KEY"] = ""
    globals()[f"{_name}_SECRET"] = ""
    globals()[f"{_name}_BUCKET_ENDPOINT"] = "http://127.0.0.1:9"
IHR_KEYCLOAK_URL = ""
EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"

DATABASES = {"default": env.db("DATABASE_URL", default="")}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"]["CONN_MAX_AGE"] = 0
DATABASES["default"].setdefault("OPTIONS", {}).update(
    {"application_name": "ihr-restore-drill"}
)

apply_tenancy(globals())
