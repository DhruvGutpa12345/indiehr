"""ihr_keycloak_sync without a Keycloak (P1-T3): the live behaviour is in keycloak/tests."""

import os
import uuid
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from django_tenants.utils import schema_context

from ihr_platform.keycloak.hospital import HospitalIds
from ihr_platform.management.commands import ihr_keycloak_sync
from ihr_platform.models import Hospital

ENV = {
    "IHR_KEYCLOAK_ADMIN_CLIENT_ID": "test-admin",
    "IHR_KEYCLOAK_ADMIN_CLIENT_SECRET": "not-a-real-secret",
}
SYNC = "ihr_platform.management.commands.ihr_keycloak_sync"


@override_settings(IHR_KEYCLOAK_URL="http://keycloak.invalid", DEBUG=True)
class KeycloakSyncCommandTests(SimpleTestCase):
    databases = {"default"}

    def setUp(self):
        with schema_context("public"):
            self.hospital = Hospital.objects.get(slug="testhosp")
            self.original = self.hospital.keycloak_org_id
            Hospital.objects.filter(pk=self.hospital.pk).update(keycloak_org_id=None)

    def tearDown(self):
        with schema_context("public"):
            Hospital.objects.filter(pk=self.hospital.pk).update(
                keycloak_org_id=self.original
            )

    def run_sync(self, *args):
        out, err = StringIO(), StringIO()
        with schema_context("public"):
            call_command("ihr_keycloak_sync", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_admin_credential_must_come_from_the_environment(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            self.assertRaisesMessage(CommandError, "IHR_KEYCLOAK_ADMIN_CLIENT_ID"),
        ):
            self.run_sync("--slug", "testhosp")

    @mock.patch.dict(os.environ, ENV)
    def test_sync_links_the_hospital_once(self):
        org_id = str(uuid.uuid4())
        ids = HospitalIds(
            org_id, "client-uuid", "ihr-h-testhosp", "ihr-hospital-testhosp"
        )
        with (
            mock.patch(f"{SYNC}.ensure_hospital", return_value=ids) as ensure,
            mock.patch(f"{SYNC}.check_hospital", return_value=[]),
        ):
            out, _ = self.run_sync("--slug", "testhosp")
            login, stored = ensure.call_args.args[1:]
            self.assertIsNone(stored)
            self.assertEqual(login.slug, "testhosp")
            self.assertTrue(
                all(u.endswith("/api/ihr/auth/callback/") for u in login.redirect_uris)
            )
            self.assertIn("ihr-h-testhosp -> organisation", out)
            with schema_context("public"):
                self.hospital.refresh_from_db()
            self.assertEqual(str(self.hospital.keycloak_org_id), org_id)
            # The second run passes the stored id, so Keycloak cannot swap organisations.
            self.run_sync("--slug", "testhosp")
            self.assertEqual(ensure.call_args.args[2], org_id)

    @mock.patch.dict(os.environ, ENV)
    def test_check_reports_drift_and_fails(self):
        with (
            mock.patch(f"{SYNC}.check_realm", return_value=[]),
            # The operator realm (P1-T5b, IHR_CONSOLE_URL is set in test settings)
            mock.patch("ihr_platform.keycloak.ops.check_ops_realm", return_value=[]),
            self.assertRaisesMessage(CommandError, "1 problem(s)"),
        ):
            self.run_sync("--slug", "testhosp", "--check")

    @override_settings(IHR_KEYCLOAK_DEV_REDIRECTS=True, DEBUG=False)
    def test_dev_redirects_are_refused_outside_development(self):
        with (
            schema_context("public"),
            self.assertRaisesMessage(CommandError, "local development only"),
        ):
            ihr_keycloak_sync.hospital_login(self.hospital)

    @override_settings(IHR_KEYCLOAK_DEV_REDIRECTS=False)
    def test_disabled_hospitals_get_a_disabled_login(self):
        with schema_context("public"):
            self.hospital.status = Hospital.Status.SUSPENDED
            self.assertFalse(ihr_keycloak_sync.hospital_login(self.hospital).enabled)
            self.hospital.status = Hospital.Status.ACTIVE
            self.assertTrue(ihr_keycloak_sync.hospital_login(self.hospital).enabled)


class MailServerTests(SimpleTestCase):
    """The realm's mail server for setup e-mails (P1-T5c)."""

    SMTP = {"host": "smtp.example.test", "port": 587, "from": "no-reply@example.test"}

    def test_values_reach_keycloak_as_its_own_strings(self):
        from ihr_platform.keycloak.realm import realm_settings

        smtp = realm_settings("r", {**self.SMTP, "starttls": True, "ssl": False})
        self.assertEqual(
            smtp["smtpServer"],
            {
                "host": "smtp.example.test",
                "port": "587",
                "from": "no-reply@example.test",
                "starttls": "true",
                "ssl": "false",
            },
        )
        self.assertNotIn("smtpServer", realm_settings("r"))

    def test_tls_outside_development_and_password_only_from_the_environment(self):
        with (
            override_settings(DEBUG=False, IHR_KEYCLOAK_SMTP=self.SMTP),
            self.assertRaisesMessage(CommandError, "must use TLS"),
        ):
            ihr_keycloak_sync.mail_server()
        tls = {**self.SMTP, "starttls": True}
        with override_settings(DEBUG=False, IHR_KEYCLOAK_SMTP=tls):
            self.assertNotIn("password", ihr_keycloak_sync.mail_server())
            with mock.patch.dict(os.environ, {"IHR_KEYCLOAK_SMTP_PASSWORD": "pw-x"}):
                smtp = ihr_keycloak_sync.mail_server()
            self.assertEqual((smtp["auth"], smtp["password"]), ("true", "pw-x"))
        with (
            override_settings(IHR_KEYCLOAK_SMTP={**tls, "password": "in-settings"}),
            self.assertRaisesMessage(CommandError, "IHR_KEYCLOAK_SMTP_PASSWORD"),
        ):
            ihr_keycloak_sync.mail_server()
        with override_settings(IHR_KEYCLOAK_SMTP=None):
            self.assertIsNone(ihr_keycloak_sync.mail_server())
