"""Turn CARE's settings into schema-per-tenant settings (TENANCY_DESIGN.md §2, §3, §6.1).

Call ``apply_tenancy(globals())`` at the end of a settings module that imported CARE's.
Applied last on purpose: CARE's test settings redefine CACHES, which would drop the key function.
"""

SHARED_APPS = [
    "django_tenants",
    "ihr_platform",
    "django.contrib.contenttypes",
]
PLATFORM_ONLY_APPS = {"django_tenants", "ihr_platform"}


def apply_tenancy(settings):
    care_apps = [a for a in settings["INSTALLED_APPS"] if a not in PLATFORM_ONLY_APPS]

    # Every CARE app (and Django auth/sessions/sites/admin/authtoken) lives in the tenant schema.
    settings["SHARED_APPS"] = list(SHARED_APPS)
    settings["TENANT_APPS"] = care_apps
    settings["INSTALLED_APPS"] = SHARED_APPS + [
        a for a in care_apps if a not in SHARED_APPS
    ]

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

    for cache in settings["CACHES"].values():
        cache["KEY_FUNCTION"] = "django_tenants.cache.make_key"
        cache["REVERSE_KEY_FUNCTION"] = "django_tenants.cache.reverse_key"
