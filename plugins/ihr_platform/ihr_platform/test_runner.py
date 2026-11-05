"""Run CARE's whole test suite inside a tenant schema (TENANCY_DESIGN.md §11.1).

The test database gets the shared apps in ``public`` plus one tenant, ``h_testhosp01``, reachable
from the test client as ``testserver``. Every new database connection (threads in CARE's
concurrency tests included) is pointed at that tenant.

Parallel runs are not supported yet: Django clones the test database before we can add the
tenant schema, so ``--parallel`` is forced to 1.
"""

from django.db import connection
from django.db.backends.signals import connection_created
from django.test.runner import DiscoverRunner

TEST_SCHEMA = "h_testhosp01"
TEST_DOMAINS = ("testserver", "localhost", "127.0.0.1")


def _point_new_connection_at_test_tenant(sender, connection, **kwargs):
    if connection.alias == "default" and connection.schema_name != TEST_SCHEMA:
        connection.set_schema(TEST_SCHEMA)


class TenantTestRunner(DiscoverRunner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.parallel > 1:
            self.log("Tenant mode: running tests serially (parallel=1).")
            self.parallel = 1

    def setup_databases(self, **kwargs):
        old_config = super().setup_databases(**kwargs)

        from ihr_platform.models import Domain, Hospital

        connection.set_schema_to_public()
        tenant = Hospital.objects.filter(schema_name=TEST_SCHEMA).first()
        if tenant is None:
            tenant = Hospital(
                schema_name=TEST_SCHEMA,
                slug="testhosp",
                name="Test Hospital",
                status=Hospital.Status.ACTIVE,
            )
            tenant.save()  # creates the schema and migrates every CARE app into it
        for domain in TEST_DOMAINS:
            Domain.objects.get_or_create(domain=domain, defaults={"tenant": tenant})

        connection.set_tenant(tenant)
        connection_created.connect(_point_new_connection_at_test_tenant)
        return old_config

    def teardown_databases(self, old_config, **kwargs):
        connection_created.disconnect(_point_new_connection_at_test_tenant)
        connection.set_schema_to_public()
        super().teardown_databases(old_config, **kwargs)
