"""Turn CARE's settings into schema-per-tenant settings (TENANCY_DESIGN.md §2, §3, §6.1).

Call ``apply_tenancy(globals())`` at the end of a settings module that imported CARE's.
Applied last on purpose: CARE's test settings redefine CACHES, which would drop the key function.
"""

from django.core.exceptions import ImproperlyConfigured

SHARED_APPS = [
    "django_tenants",
    "ihr_platform",
    "django.contrib.contenttypes",
]
PLATFORM_ONLY_APPS = {"django_tenants", "ihr_platform"}
# Our apps that live in every hospital's schema (TENANCY_DESIGN.md §3).
IHR_TENANT_APPS = ["ihr_tenant_settings"]


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

    settings["FILE_OBJECT_KEY_PREFIX_FUNCTION"] = (
        "ihr_platform.storage.tenant_key_prefix"
    )

    for cache in settings["CACHES"].values():
        cache["KEY_FUNCTION"] = "django_tenants.cache.make_key"
        cache["REVERSE_KEY_FUNCTION"] = "django_tenants.cache.reverse_key"
