"""P1-T1a structural and isolation smoke tests. The full isolation suite is P1-T1b."""

from care.users.models import User
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase
from django_tenants.utils import schema_context

from ihr_platform.models import Domain, Hospital
from ihr_platform.test_runner import TEST_SCHEMA

PUBLIC_TABLE_ALLOW_LIST = {
    "django_migrations",
    "django_content_type",
    "ihr_platform_hospital",
    "ihr_platform_domain",
    "ihr_platform_retiredaddress",
    "ihr_platform_tenantoperation",  # P1-T5a: provisioning runs (platform data, no patients)
    "ihr_platform_operatoraccount",  # P1-T5b: who may use the operator console
}
SECOND_DOMAIN = "second.testserver"


def _tables_in(schema):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            [schema],
        )
        return {row[0] for row in cursor.fetchall()}


class TenancyBasicsTests(SimpleTestCase):
    databases = {"default"}
    second = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with schema_context("public"):
            cls.second = Hospital.objects.filter(slug="second-test").first()
            if cls.second is None:
                cls.second = Hospital(slug="second-test", name="Second Test Hospital")
                cls.second.save()
            Domain.objects.get_or_create(
                domain=SECOND_DOMAIN, defaults={"tenant": cls.second}
            )

    @classmethod
    def tearDownClass(cls):
        with schema_context("public"), connection.cursor() as cursor:
            schema = cls.second.schema_name
            Domain.objects.filter(tenant=cls.second).delete()
            Hospital.objects.filter(pk=cls.second.pk).delete()
            cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        connection.set_schema(TEST_SCHEMA)
        super().tearDownClass()

    def tearDown(self):
        # Requests switch the connection to the host's tenant; always hand back the test tenant.
        connection.set_schema(TEST_SCHEMA)
        super().tearDown()

    def test_public_schema_holds_no_care_tables(self):
        unexpected = _tables_in("public") - PUBLIC_TABLE_ALLOW_LIST
        self.assertFalse(
            unexpected, f"Unexpected tables in public: {sorted(unexpected)}"
        )

    def test_tenant_schema_holds_care_tables(self):
        tables = _tables_in(TEST_SCHEMA)
        for table in (
            "users_user",
            "emr_patient",
            "facility_facility",
            "auth_permission",
        ):
            self.assertIn(table, tables)

    def test_no_app_is_both_shared_and_tenant_except_contenttypes(self):
        overlap = set(settings.SHARED_APPS) & set(settings.TENANT_APPS)
        self.assertEqual(overlap, {"django.contrib.contenttypes"})

    def test_pg_trgm_extension_lives_in_public(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
                "WHERE e.extname = 'pg_trgm'"
            )
            self.assertEqual(cursor.fetchone(), ("public",))

    def test_rows_do_not_cross_tenants(self):
        with schema_context(self.second.schema_name):
            User.objects.create(username="only-in-second", email="b@example.test")
            self.assertTrue(User.objects.filter(username="only-in-second").exists())
        with schema_context(TEST_SCHEMA):
            self.assertFalse(User.objects.filter(username="only-in-second").exists())

    def test_cache_keys_are_tenant_scoped(self):
        with schema_context(TEST_SCHEMA):
            cache.set("ihr-isolation-probe", "first", 60)
        with schema_context(self.second.schema_name):
            self.assertIsNone(cache.get("ihr-isolation-probe"))
            cache.set("ihr-isolation-probe", "second", 60)
        with schema_context(TEST_SCHEMA):
            self.assertEqual(cache.get("ihr-isolation-probe"), "first")
            cache.delete("ihr-isolation-probe")
        with schema_context(self.second.schema_name):
            cache.delete("ihr-isolation-probe")

    def test_host_selects_tenant(self):
        response = self.client.get("/ping/", HTTP_HOST=SECOND_DOMAIN)
        self.assertEqual(response.status_code, 200)
        # the middleware switched this connection to the second tenant, not the default one
        self.assertEqual(connection.schema_name, self.second.schema_name)

    def test_care_audit_log_is_off_in_tenant_mode(self):
        """CARE's audit log can mix up users across tenants (thread-local); off until P1-T6."""
        self.assertFalse(settings.AUDIT_LOG_ENABLED)

    def test_unknown_host_is_refused(self):
        response = self.client.get("/ping/", HTTP_HOST="unknown.example.test")
        self.assertEqual(response.status_code, 404)
