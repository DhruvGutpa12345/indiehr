"""CARE's test settings in tenant mode: the full CARE suite runs inside a tenant schema."""

from config.settings.test import *  # noqa: F403

from ihr_platform.settings.tenancy import apply_tenancy

apply_tenancy(globals())

TEST_RUNNER = "ihr_platform.test_runner.TenantTestRunner"
