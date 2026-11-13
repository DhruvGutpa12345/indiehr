"""ihr_keycloak_sync without a Keycloak (P1-T3): the live behaviour is in keycloak/tests."""

import os
import tempfile
import uuid
from io import StringIO
from pathlib import Path
from unittest import mock

import jwt
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from django_tenants.utils import schema_context

from ihr_platform.keycloak.hospital import HospitalIds
from ihr_platform.management.commands import ihr_keycloak_sync
from ihr_platform.models import Hospital
from ihr_platform.testing import owner_for_test

ENV = {
    "IHR_KEYCLOAK_ADMIN_CLIENT_ID": "test-admin",
    "IHR_KEYCLOAK_ADMIN_CLIENT_SECRET": "not-a-real-secret",
}
SYNC = "ihr_platform.management.commands.ihr_keycloak_sync"


@override_settings(IHR_KEYCLOAK_URL="http://keycloak.invalid", DEBUG=True)
class KeycloakSyncCommandTests(SimpleTestCase):
    databases = {"default"}

    def setUp(self):
        owner_for_test(
            self
        )  # ihr_keycloak_sync is an admin one-off: the owner (P1-T19a)
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
            # The workers' accounts (P1-T5f): checked live in keycloak/tests
            mock.patch(f"{SYNC}.check_service_accounts", return_value=[]),
            mock.patch(f"{SYNC}.check_admin_holders", return_value=[]),
            self.assertRaisesMessage(CommandError, "1 problem(s)"),
        ):
            self.run_sync("--slug", "testhosp", "--check")

    def test_sign_in_goes_to_the_backend_only_with_the_backends_keys(self):
        """No other redirect (the dev token viewer's :8082 is gone, P1-T8d2), and the client
        accepts exactly the backend's published keys."""
        with schema_context("public"):
            login = ihr_keycloak_sync.hospital_login(self.hospital)
        self.assertTrue(login.redirect_uris)
        self.assertTrue(
            all(u.endswith("/api/ihr/auth/callback/") for u in login.redirect_uris)
        )
        self.assertNotIn(":8082", " ".join(login.origins))
        self.assertEqual(login.jwks, ihr_keycloak_sync.public_jwks("backend"))

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


def key_dir(purpose):
    """The test run's key folder of ``purpose`` (settings/test.py makes all three)."""
    return str(Path(settings.IHR_CLIENT_KEY_DIR).parent / purpose)


@override_settings(
    IHR_KEYCLOAK_URL="http://keycloak.invalid", IHR_KEYCLOAK_REALM="indiehr"
)
class WorkerCredentialTests(SimpleTestCase):
    """P1-T5f: each worker uses only its own narrow account of the hospitals' realm, and
    (P1-T8d2) signs in to it with its own key."""

    def test_purpose_picks_the_workers_own_account_and_key(self):
        with (
            override_settings(IHR_CLIENT_KEY_DIR=key_dir("staff")),
            mock.patch.dict(os.environ, ENV, clear=True),
        ):
            staff = ihr_keycloak_sync.keycloak_admin(purpose="staff")
            operator = ihr_keycloak_sync.keycloak_admin()
            # The staff worker's key cannot stand in for the onboarding account.
            onboarding = ihr_keycloak_sync.keycloak_admin(purpose="onboarding")
            with self.assertRaisesMessage(CommandError, "works in indiehr only"):
                ihr_keycloak_sync.keycloak_admin("indiehr-ops", purpose="staff")
            fields = staff._client_auth()
        self.assertEqual(
            (staff.client_id, staff._account_realm, staff.realm),
            ("ihr-staff-worker", "indiehr", "indiehr"),
        )
        self.assertNotIn("client_secret", fields)
        claims = jwt.decode(
            fields["client_assertion"], options={"verify_signature": False}
        )
        self.assertEqual(
            (claims["sub"], claims["aud"]),
            ("ihr-staff-worker", f"{settings.IHR_KEYCLOAK_PUBLIC_URL}/realms/indiehr"),
        )
        self.assertEqual(operator._account_realm, "master")  # the bootstrap one
        self.assertEqual(onboarding.client_id, "test-admin")  # bootstrap, not a key
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            self.assertRaisesMessage(CommandError, "the onboarding key"),
        ):
            ihr_keycloak_sync.keycloak_admin(purpose="onboarding")

    def test_a_worker_refuses_to_run_with_the_wrong_credentials(self):
        staff, empty = key_dir("staff"), tempfile.mkdtemp()
        self.addCleanup(Path(empty).rmdir)
        for folder, env, message in (
            (empty, {}, "No client key"),
            (key_dir("onboarding"), {}, "holds the onboarding key"),
            (key_dir("backend"), {}, "holds the backend key"),
            (staff, ENV, "bootstrap admin credential"),
            (staff, {"IHR_KEYCLOAK_STAFF_SECRET": "s"}, "IHR_KEYCLOAK_STAFF_SECRET"),
            (
                staff,
                {"IHR_KEYCLOAK_ONBOARDING_SECRET": "o"},
                "IHR_KEYCLOAK_ONBOARDING_SECRET is in the staff worker",
            ),
        ):
            with (
                self.subTest(message),
                override_settings(IHR_CLIENT_KEY_DIR=folder),
                mock.patch.dict(os.environ, env, clear=True),
                self.assertRaisesMessage(CommandError, message),
            ):
                call_command("ihr_provision_worker", role="staff", once=True)

    def test_a_worker_is_ready_only_once_keycloak_accepts_its_key(self):
        from ihr_platform.keycloak.kc_http import KeycloakAdmin, KeycloakError

        with (
            override_settings(IHR_CLIENT_KEY_DIR=key_dir("staff")),
            mock.patch.dict(os.environ, {}, clear=True),
        ):
            with (
                mock.patch.object(
                    KeycloakAdmin,
                    "_access_token",
                    side_effect=KeycloakError("Keycloak admin login failed: HTTP 401"),
                ),
                self.assertRaisesMessage(CommandError, "Keycloak refused the staff"),
            ):
                call_command("ihr_provision_worker", role="staff", once=True)
            out = StringIO()
            with (
                mock.patch.object(KeycloakAdmin, "_access_token", return_value="t"),
                mock.patch(
                    "ihr_platform.management.commands.ihr_provision_worker.work_once"
                ) as work,
            ):
                call_command(
                    "ihr_provision_worker", role="staff", once=True, stdout=out
                )
            self.assertIn("provisioner ready (staff)", out.getvalue())
            work.assert_called_once_with("staff")
