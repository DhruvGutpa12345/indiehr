"""CARE's test settings in tenant mode: the full CARE suite runs inside a tenant schema."""

from urllib.parse import urlsplit

from config.settings.test import *  # noqa: F403

from ihr_platform.settings.tenancy import apply_tenancy


def _test_client_keys():
    import atexit
    import shutil
    import tempfile
    from pathlib import Path

    from ihr_platform import client_keys

    root = Path(tempfile.mkdtemp(prefix="ihr-test-keys-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    (root / "jwks").mkdir()
    for purpose in client_keys.PURPOSES:
        store = client_keys.KeyStore(root / "keys" / purpose)
        store.ensure(purpose)
        (root / "jwks" / f"{purpose}.jwks.json").write_text(store.public_jwks())
    return str(root / "keys" / "backend"), str(root / "jwks")


# CARE's test settings drop the Swagger cache that config/urls.py uses; the route crawl needs it.
CACHES["swagger_cache"] = {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}  # noqa: F405

IHR_TENANT_DOMAIN_SUFFIX = "localhost"
# Tests never reach Keycloak: ihr_identity.testing stands in for it.
IHR_KEYCLOAK_PUBLIC_URL = "https://auth.test"
IHR_KEYCLOAK_URL = "http://keycloak.test"
IHR_CONSOLE_URL = "https://console.testserver"
IHR_BACKCHANNEL_LOGOUT_URL = "http://console.testserver/api/ihr/backchannel-logout/"
# The backend's client key (P1-T8d2), made fresh for each run in a temporary folder: a private
# key is never committed. ihr_identity.testing checks every assertion signed with it.
IHR_CLIENT_KEY_DIR, IHR_CLIENT_JWKS_DIR = _test_client_keys()
IHR_CLIENT_KEY_PURPOSE = "backend"

apply_tenancy(globals())

TEST_RUNNER = "ihr_platform.test_runner.TenantTestRunner"

# The isolation suite flushes and audits Redis: keep test cache data in its own database
# (index 15) so it never touches the running app's keys.
_test_redis = urlsplit(REDIS_URL)._replace(path="/15", query="")  # noqa: F405
CACHES["default"]["LOCATION"] = _test_redis.geturl()  # noqa: F405
