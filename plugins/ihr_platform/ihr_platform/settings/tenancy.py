"""Turn CARE's settings into schema-per-tenant settings (TENANCY_DESIGN.md §2, §3, §6.1).

Call ``apply_tenancy(globals())`` at the end of a settings module that imported CARE's.
Applied last on purpose: CARE's test settings redefine CACHES, which would drop the key function.
"""

import os

from django.core.exceptions import ImproperlyConfigured

SHARED_APPS = [
    "django_tenants",
    "ihr_platform",
    "django.contrib.contenttypes",
]
PLATFORM_ONLY_APPS = {"django_tenants", "ihr_platform"}
# Our apps that live in every hospital's schema (TENANCY_DESIGN.md §3).
IHR_TENANT_APPS = ["ihr_tenant_settings", "ihr_identity"]


def apply_tenancy(settings):
    care_apps = [a for a in settings["INSTALLED_APPS"] if a not in PLATFORM_ONLY_APPS]

    # Every CARE app (and Django auth/sessions/sites/admin/authtoken) lives in the tenant schema.
    settings["SHARED_APPS"] = list(SHARED_APPS)
    settings["TENANT_APPS"] = care_apps + IHR_TENANT_APPS
    settings["INSTALLED_APPS"] = SHARED_APPS + [
        a for a in settings["TENANT_APPS"] if a not in SHARED_APPS
    ]
    settings["ROOT_URLCONF"] = "ihr_platform.urls_tenant"

    settings["DATABASES"]["default"]["ENGINE"] = "django_tenants.postgresql_backend"
    settings["DATABASE_ROUTERS"] = ("django_tenants.routers.TenantSyncRouter",)

    settings["TENANT_MODEL"] = "ihr_platform.Hospital"
    settings["TENANT_DOMAIN_MODEL"] = "ihr_platform.Domain"
    settings["PUBLIC_SCHEMA_URLCONF"] = "ihr_platform.urls_public"
    settings["SHOW_PUBLIC_IF_NO_TENANT_FOUND"] = False

    middleware = [m for m in settings["MIDDLEWARE"] if "TenantMainMiddleware" not in m]
    settings["MIDDLEWARE"] = [
        "django_tenants.middleware.main.TenantMainMiddleware",
        *middleware,
    ]

    # Periodic jobs run once per active hospital, in its schema (ihr_platform/beat.py).
    settings["CELERY_BEAT_SCHEDULER"] = "ihr_platform.beat.ActiveTenantScheduler"
    # CARE's own audit log keeps the last request in a thread-local that GET requests never reset,
    # so it could attribute one hospital's change to another hospital's user. Off until P1-T6.
    settings["AUDIT_LOG_ENABLED"] = False

    # Hospital web addresses: <slug>.<suffix>, e.g. alpha.app.example.in (dev: alpha.localhost).
    if "IHR_TENANT_DOMAIN_SUFFIX" not in settings:
        if not settings.get("DEBUG"):
            msg = "Set IHR_TENANT_DOMAIN_SUFFIX (e.g. app.example.in) before apply_tenancy()."
            raise ImproperlyConfigured(msg)
        settings["IHR_TENANT_DOMAIN_SUFFIX"] = "localhost"
    settings.setdefault("IHR_FRONTEND_URL_TEMPLATE", "https://{domain}")
    # CARE's password set-up/reset links point at the hospital's own address (links.py).
    settings["FRONTEND_BASE_URL_FUNCTION"] = "ihr_platform.links.frontend_base_url"

    # Our product name, never CARE's or OHC's (P1-T2b). Emails speak for the hospital.
    settings["SITE_NAME_FUNCTION"] = "ihr_tenant_settings.branding.hospital_name"
    for name in (
        "user_create_password",
        "user_reset_password",
        "totp_enabled",
        "totp_disabled",
    ):
        settings[f"{name.upper()}_EMAIL_TEMPLATE_PATH"] = f"ihr_email/{name}.html"
    if "EMAIL_FROM" not in os.environ:
        settings["DEFAULT_FROM_EMAIL"] = "IndieHR <no-reply@localhost>"
    settings["EMAIL_SUBJECT_PREFIX"] = "[IndieHR] "
    settings["OTP_SMS_LOGIN_CONTENT"] = (
        "Your login OTP is {otp}. Please do not share this with anyone."
    )
    settings["OTP_SMS_RESET_PASSWORD_CONTENT"] = (
        "Your password reset OTP is {otp}. Please do not share this with anyone."
    )
    settings["SPECTACULAR_SETTINGS"] = {
        **settings["SPECTACULAR_SETTINGS"],
        "TITLE": "IndieHR API",
        "DESCRIPTION": "IndieHR hospital API",
    }

    # Staff sign in only through Keycloak, with tokens kept by the backend (P1-T4, ADR-009).
    # CARE's own JWT, Basic and Django-session classes are gone; patient OTP views keep theirs.
    settings["REST_FRAMEWORK"] = {
        **settings["REST_FRAMEWORK"],
        "DEFAULT_AUTHENTICATION_CLASSES": [
            "ihr_identity.authentication.StaffSessionAuthentication",
            "ihr_identity.authentication.ServiceTokenAuthentication",
        ],
    }
    settings["MIDDLEWARE"] = [
        *settings["MIDDLEWARE"],
        "ihr_identity.middleware.StaffSessionMiddleware",
    ]
    settings["CORS_ALLOW_CREDENTIALS"] = False
    settings.setdefault("IHR_SESSION_IDLE_SECONDS", 1800)
    settings.setdefault("IHR_SESSION_MAX_SECONDS", 43200)
    settings.setdefault("IHR_OIDC_LEEWAY_SECONDS", 30)
    settings.setdefault("IHR_SESSION_COOKIE", "__Host-ihr_session")
    settings.setdefault("IHR_LOGIN_COOKIE", "__Host-ihr_login")
    settings.setdefault("IHR_SESSION_COOKIE_SECURE", True)
    settings.setdefault("IHR_STAFF_REFUSE_SUPERUSERS", True)

    # Keycloak (P1-T3, ADR-008): one realm for all hospitals. Admin credentials are never
    # settings: ihr_keycloak_sync reads them from its own environment.
    settings.setdefault("IHR_KEYCLOAK_REALM", "indiehr")
    settings.setdefault("IHR_KEYCLOAK_DEV_REDIRECTS", False)

    settings["FILE_OBJECT_KEY_PREFIX_FUNCTION"] = (
        "ihr_platform.storage.tenant_key_prefix"
    )

    for cache in settings["CACHES"].values():
        cache["KEY_FUNCTION"] = "django_tenants.cache.make_key"
        cache["REVERSE_KEY_FUNCTION"] = "django_tenants.cache.reverse_key"
