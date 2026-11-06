"""CARE's test settings in tenant mode: the full CARE suite runs inside a tenant schema."""

from urllib.parse import urlsplit

from config.settings.test import *  # noqa: F403

from ihr_platform.settings.tenancy import apply_tenancy

# CARE's test settings drop the Swagger cache that config/urls.py uses; the route crawl needs it.
CACHES["swagger_cache"] = {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}  # noqa: F405

IHR_TENANT_DOMAIN_SUFFIX = "localhost"
# Tests never reach Keycloak: ihr_identity.testing stands in for it.
IHR_KEYCLOAK_PUBLIC_URL = "https://auth.test"
IHR_KEYCLOAK_URL = "http://keycloak.test"

apply_tenancy(globals())

TEST_RUNNER = "ihr_platform.test_runner.TenantTestRunner"

# The isolation suite flushes and audits Redis: keep test cache data in its own database
# (index 15) so it never touches the running app's keys.
_test_redis = urlsplit(REDIS_URL)._replace(path="/15", query="")  # noqa: F405
CACHES["default"]["LOCATION"] = _test_redis.geturl()  # noqa: F405
