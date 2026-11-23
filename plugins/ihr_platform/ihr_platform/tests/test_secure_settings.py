"""Staging and production refuse unsafe settings (P1-T8a, review R0-09)."""

import os
import subprocess
import sys

from care.utils.jwks.generate_jwk import generate_encoded_jwks
from django.test import SimpleTestCase

from ihr_platform.settings import secure

GOOD_KEY = "k" + "".join(chr(33 + (i * 7) % 90) for i in range(70))
GOOD_ENV = {
    "DJANGO_SECRET_KEY": GOOD_KEY,
    "DJANGO_ALLOWED_HOSTS": '[".app.hospital-test.in", "backend"]',
    "DATABASE_URL": "postgres://u:p@db:5432/ihr",
    "REDIS_URL": "redis://redis:6379/0",
    "JWKS_BASE64": "e30=",
    "IHR_TENANT_DOMAIN_SUFFIX": "app.hospital-test.in",
    "IHR_ENVIRONMENT": "production-1",
    "IHR_KEYCLOAK_PUBLIC_URL": "https://auth.app.hospital-test.in",
    "IHR_KEYCLOAK_URL": "http://keycloak:8080",
    "IHR_CONSOLE_URL": "https://console.app.hospital-test.in",
    "IHR_BACKCHANNEL_LOGOUT_URL": "http://backend:9000/api/ihr/backchannel-logout/",
    "FILE_UPLOAD_BUCKET": "files",
    "FACILITY_S3_BUCKET": "facility",
    "SNOWSTORM_DEPLOYMENT_URL": "http://127.0.0.1:9/fhir",
    # Mail (P1-T8b4)
    "EMAIL_HOST": "smtp.mail-test.in",
    "EMAIL_FROM": "IndieHR <no-reply@hospital-test.in>",
    "IHR_KEYCLOAK_SMTP_HOST": "smtp.mail-test.in",
    "IHR_KEYCLOAK_SMTP_FROM": "no-reply@hospital-test.in",
    # Database roles (P1-T19b): IHR_DB_ROLES defaults to required in the deployment settings
    "IHR_DB_ROLE": "app",
    # Each bucket its own key (P1-T20b)
    "FILE_UPLOAD_KEY": "files-key",
    "FILE_UPLOAD_SECRET": "files-pass",
    "FACILITY_S3_KEY": "pics-key",
    "FACILITY_S3_SECRET": "pics-pass",
}
GOOD_SETTINGS = {
    "DEBUG": False,
    "IS_PRODUCTION": True,
    "SECRET_KEY": GOOD_KEY,
    "IHR_TENANT_DOMAIN_SUFFIX": "app.hospital-test.in",
    "IHR_ENVIRONMENT": "production-1",
    "ALLOWED_HOSTS": [".app.hospital-test.in", "backend"],
    "IHR_KEYCLOAK_PUBLIC_URL": "https://auth.app.hospital-test.in",
    "IHR_CONSOLE_URL": "https://console.app.hospital-test.in",
    "IHR_FRONTEND_URL_TEMPLATE": "https://{domain}",
    "SESSION_COOKIE_SECURE": True,
    "CSRF_COOKIE_SECURE": True,
    "IHR_SESSION_COOKIE_SECURE": True,
    "IHR_SESSION_COOKIE": "__Host-ihr_session",
    "IHR_OPS_SESSION_COOKIE": "__Host-ihr_ops",
    "SECURE_HSTS_SECONDS": 31536000,
    "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https"),
    "SNOWSTORM_DEPLOYMENT_URL": "http://127.0.0.1:9/fhir",
    "SENTRY_DSN": "",
    "TIME_ZONE": "Asia/Kolkata",
    "USE_TZ": True,
    "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
    "EMAIL_HOST": "smtp.mail-test.in",
    "EMAIL_USE_TLS": True,
    "DEFAULT_FROM_EMAIL": "IndieHR <no-reply@hospital-test.in>",
    "IHR_KEYCLOAK_SMTP": {
        "host": "smtp.mail-test.in",
        "from": "no-reply@hospital-test.in",
        "starttls": "true",
    },
    "IHR_DB_ROLES": "required",
    "IHR_DB_ROLE": "app",
    "IHR_DB_LIMITS": "web",
    "FACILITY_IMAGE_URL_FUNCTION": "ihr_platform.storage.facility_image_url",
    "FACILITY_CDN": None,
    "BUCKET_HAS_FINE_ACL": False,
    "BUCKET_PROVIDER": "AWS",
    "BUCKET_KEY": "",
    "BUCKET_SECRET": "",
    "FILE_UPLOAD_KEY": "files-key",
    "FILE_UPLOAD_SECRET": "files-pass",
    "FACILITY_S3_KEY": "pics-key",
    "FACILITY_S3_SECRET": "pics-pass",
}


class SecureRulesTests(SimpleTestCase):
    def test_safe_settings_pass(self):
        self.assertEqual(secure.problems(GOOD_SETTINGS, GOOD_ENV), [])

    def test_database_limits_are_never_off_on_a_server(self):
        """P1-T23: IHR_DB_LIMITS=off (or nothing) is refused; any profile is accepted."""
        for value in ("off", ""):
            with self.subTest(value=value):
                found = secure.problems(
                    GOOD_SETTINGS | {"IHR_DB_LIMITS": value}, GOOD_ENV
                )
                self.assertEqual(len(found), 1, found)
                self.assertIn("IHR_DB_LIMITS", found[0])
        for value in ("web", "worker", "owner"):
            self.assertEqual(
                secure.problems(GOOD_SETTINGS | {"IHR_DB_LIMITS": value}, GOOD_ENV), []
            )

    def test_each_unsafe_setting_is_named(self):
        cases = {
            "DEBUG must be off": {"DEBUG": True},
            "IS_PRODUCTION must be on": {"IS_PRODUCTION": False},
            "CARE's published default": {"SECRET_KEY": secure.CARE_DEFAULT_SECRET_KEY},
            "at least 50 characters": {"SECRET_KEY": "short"},
            "looks like a placeholder": {"SECRET_KEY": "django-insecure-" + GOOD_KEY},
            "'*' is not allowed": {"ALLOWED_HOSTS": ["*"]},
            "must include .app.hospital-test.in": {"ALLOWED_HOSTS": ["backend"]},
            "must be a real domain": {"IHR_TENANT_DOMAIN_SUFFIX": "localhost"},
            "IHR_KEYCLOAK_PUBLIC_URL must be https": {
                "IHR_KEYCLOAK_PUBLIC_URL": "http://auth.app.hospital-test.in"
            },
            "IHR_CONSOLE_URL must be https": {"IHR_CONSOLE_URL": ""},
            "IHR_FRONTEND_URL_TEMPLATE": {
                "IHR_FRONTEND_URL_TEMPLATE": "http://{domain}"
            },
            "SESSION_COOKIE_SECURE must be on": {"SESSION_COOKIE_SECURE": False},
            "IHR_SESSION_COOKIE_SECURE must be on": {
                "IHR_SESSION_COOKIE_SECURE": False
            },
            "IHR_SESSION_COOKIE must start with __Host-": {
                "IHR_SESSION_COOKIE": "ihr_session"
            },
            "IHR_OPS_SESSION_COOKIE must start": {"IHR_OPS_SESSION_COOKIE": "ihr_ops"},
            "SECURE_HSTS_SECONDS": {"SECURE_HSTS_SECONDS": 60},
            "X-Forwarded-Proto": {"SECURE_PROXY_SSL_HEADER": None},
            "SNOWSTORM_DEPLOYMENT_URL": {
                "SNOWSTORM_DEPLOYMENT_URL": "http://165.22.211.144/fhir"
            },
            # Not even our own https:// server: terminology is in-process (P1-T10a1)
            "SNOWSTORM_DEPLOYMENT_URL must be the black hole": {
                "SNOWSTORM_DEPLOYMENT_URL": "https://terminology.example.in/fhir"
            },
            "SENTRY_DSN must stay unset": {"SENTRY_DSN": "https://x@sentry.io/1"},
            "TIME_ZONE must be Asia/Kolkata": {"TIME_ZONE": "UTC"},
            # The terminology loader relaxes its rules on throwaway databases (P1-T10a2)
            "test_* or bench_* database": {
                "DATABASES": {"default": {"NAME": "bench_terminology"}}
            },
            "nor marked throwaway": {"IHR_TERMINOLOGY_THROWAWAY_DB": True},
            "USE_TZ must be on": {"USE_TZ": False},
            "EMAIL_HOST is not set": {"EMAIL_HOST": "localhost"},
            "EMAIL_BACKEND must be": {
                "EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend"
            },
            "EMAIL_USE_TLS": {"EMAIL_USE_TLS": False},
            "EMAIL_FROM must be our own sender": {
                "DEFAULT_FROM_EMAIL": "IndieHR <no-reply@localhost>"
            },
            "EMAIL_FROM must be our own": {
                "DEFAULT_FROM_EMAIL": "Open Healthcare Network <ops@care.ohc.network>"
            },
            "IHR_KEYCLOAK_SMTP_HOST and _FROM": {"IHR_KEYCLOAK_SMTP": None},
            # P1-T19b: the split database roles are always checked on a server
            "IHR_DB_ROLES must be required on a server": {"IHR_DB_ROLES": "test"},
            "IHR_DB_ROLE must be app or owner": {"IHR_DB_ROLE": ""},
            "Keycloak's mail server must use TLS": {
                "IHR_KEYCLOAK_SMTP": {
                    "host": "smtp.mail-test.in",
                    "from": "x@hospital-test.in",
                    "starttls": "false",
                }
            },
        }
        for expected, change in cases.items():
            with self.subTest(expected=expected):
                found = secure.problems(GOOD_SETTINGS | change, GOOD_ENV)
                self.assertEqual(len(found), 1, found)
                self.assertIn(expected, found[0])

    def test_test_only_pack_kinds_are_refused(self):
        """P1-T15b3 (Q-K70 A): the test-only reference pack kinds are never on a server. Its
        own method, so mutation row facility-test-kinds-guard fails here."""
        found = secure.problems(
            GOOD_SETTINGS | {"IHR_REFDATA_TEST_KINDS": True}, GOOD_ENV
        )
        self.assertEqual(
            found,
            ["IHR_REFDATA_TEST_KINDS must stay off (test packs only, P1-T15b3)."],
        )

    def test_patient_global_edit_access_is_refused(self):
        """CARE's switch opens every patient to every signed-in user (P1-T11a). Its own
        method, so its mutation row fails here and nowhere it could hide (P1-T16a)."""
        found = secure.problems(
            GOOD_SETTINGS | {"PATIENT_GLOBAL_EDIT_ACCESS_ENABLED": True}, GOOD_ENV
        )
        self.assertEqual(
            found, ["PATIENT_GLOBAL_EDIT_ACCESS_ENABLED must stay off (P1-T11a)."]
        )

    def test_pictures_are_signed_and_each_bucket_has_its_own_key(self):
        """P1-T20b (Q-T3): no unsigned picture address, no public-read upload, and the patient
        files' key never doubles as the pictures key."""
        cases = (
            (
                "FACILITY_CDN must stay unset",
                {"FACILITY_CDN": "https://cdn.example.in"},
            ),
            ("BUCKET_HAS_FINE_ACL must stay off", {"BUCKET_HAS_FINE_ACL": True}),
            (
                "FACILITY_IMAGE_URL_FUNCTION must be",
                {"FACILITY_IMAGE_URL_FUNCTION": None},
            ),
            ("must differ", {"FACILITY_S3_KEY": "files-key"}),
            ("must differ", {"FACILITY_S3_SECRET": "files-pass"}),
            ("must all be set", {"FILE_UPLOAD_KEY": ""}),
            ("must all be set", {"FACILITY_S3_SECRET": None}),
            ("BUCKET_KEY / BUCKET_SECRET must be unset", {"BUCKET_KEY": "old-key"}),
        )
        for expected, change in cases:
            with self.subTest(change=change):
                found = secure.problems(GOOD_SETTINGS | change, GOOD_ENV)
                self.assertEqual(len(found), 1, found)
                self.assertIn(expected, found[0])
        # One key for both (CARE's default when only BUCKET_KEY is set) is refused
        same = {"FILE_UPLOAD_KEY": "k1", "FACILITY_S3_KEY": "k1"}
        self.assertTrue(secure.problems(GOOD_SETTINGS | same, GOOD_ENV))
        # Role-based access: one role per process, separation by bucket and IAM policy
        role = {
            "BUCKET_PROVIDER": "AWS_ROLE_BASED",
            "FILE_UPLOAD_KEY": "",
            "FACILITY_S3_KEY": "",
        }
        self.assertEqual(secure.problems(GOOD_SETTINGS | role, GOOD_ENV), [])
        found = secure.problems(GOOD_SETTINGS | role | {"FACILITY_CDN": "x"}, GOOD_ENV)
        self.assertEqual(len(found), 1, found)

    def test_cares_metrics_endpoint_stays_off(self):
        """P2-T0a (review F1/F8): CARE's anonymous /metrics/ counts each hospital's patients,
        visits and staff; our monitoring never scrapes it."""
        found = secure.problems(
            GOOD_SETTINGS | {"ENABLE_PROMETHEUS_METRICS": True}, GOOD_ENV
        )
        self.assertEqual(len(found), 1, found)
        self.assertIn("ENABLE_PROMETHEUS_METRICS must stay off", found[0])
        self.assertEqual(
            secure.problems(
                GOOD_SETTINGS | {"ENABLE_PROMETHEUS_METRICS": False}, GOOD_ENV
            ),
            [],
        )

    def test_reports_fetch_nothing_remote_and_keep_cares_closed_policy(self):
        """P2-T0a (review F4): a report origin would make the backend fetch from outside at
        render time; a custom report CSP could loosen saved and served reports."""
        cases = (
            (
                "REPORT_ALLOWED_RESOURCE_ORIGINS must stay empty",
                {"REPORT_ALLOWED_RESOURCE_ORIGINS": ["https://cdn.example.in"]},
            ),
            (
                "REPORT_CONTENT_SECURITY_POLICY must be CARE's closed default",
                {"REPORT_CONTENT_SECURITY_POLICY": "default-src *"},
            ),
        )
        for expected, change in cases:
            with self.subTest(change=change):
                found = secure.problems(GOOD_SETTINGS | change, GOOD_ENV)
                self.assertEqual(len(found), 1, found)
                self.assertIn(expected, found[0])
        # The legacy variable is only an environment fallback (base.py), never a setting
        for name in ("REPORT_ALLOWED_IMAGE_ORIGINS", "REPORT_ALLOWED_RESOURCE_ORIGINS"):
            with self.subTest(env=name):
                env = GOOD_ENV | {name: "https://cdn.example.in"}
                found = secure.problems(GOOD_SETTINGS, env)
                self.assertEqual(len(found), 1, found)
                self.assertIn(f"{name} must stay empty", found[0])
        closed = {
            "REPORT_ALLOWED_RESOURCE_ORIGINS": [],
            "REPORT_CONTENT_SECURITY_POLICY": secure.REPORT_CLOSED_CSP,
        }
        env = GOOD_ENV | {"REPORT_ALLOWED_IMAGE_ORIGINS": " "}
        self.assertEqual(secure.problems(GOOD_SETTINGS | closed, env), [])

    def test_the_closed_report_policy_is_cares_default(self):
        """If upstream changes its default report CSP, this fails until secure.py follows."""
        from django.conf import settings

        self.assertEqual(settings.REPORT_ALLOWED_RESOURCE_ORIGINS, [])
        self.assertEqual(
            settings.REPORT_CONTENT_SECURITY_POLICY, secure.REPORT_CLOSED_CSP
        )

    def test_random_keys_are_never_called_placeholders(self):
        # Random keys contain words like "test" or "secret" by chance; only a key written
        # like a placeholder is refused.
        for key in (GOOD_KEY + "TeStsecretChange", "abcSECRET" + GOOD_KEY):
            found = secure.problems(GOOD_SETTINGS | {"SECRET_KEY": key}, GOOD_ENV)
            self.assertEqual(found, [])

    def test_every_required_variable_must_be_set(self):
        for name in secure.REQUIRED_ENV:
            with self.subTest(name=name):
                env = {k: v for k, v in GOOD_ENV.items() if k != name}
                self.assertIn(
                    f"{name} is not set.", secure.problems(GOOD_SETTINGS, env)
                )


class EnvironmentRulesTests(SimpleTestCase):
    """P1-T32 E1: a server names its instance, and only by a known name."""

    def test_environment_must_be_a_known_name(self):
        for bad in (
            "",
            "prod",
            "production-0",
            "production-01",
            "Production-1",
            "staging ",
            "dev",
            "production-1000",
            "'staging'",
        ):
            with self.subTest(value=bad):
                # an empty setting means an empty variable (REQUIRED_ENV says "not set")
                env = GOOD_ENV | {"IHR_ENVIRONMENT": bad}
                found = secure.problems(GOOD_SETTINGS | {"IHR_ENVIRONMENT": bad}, env)
                self.assertTrue(any("IHR_ENVIRONMENT" in p for p in found), found)
        for good in ("production-1", "production-12", "staging", "proof"):
            with self.subTest(value=good):
                self.assertEqual(
                    secure.problems(
                        GOOD_SETTINGS | {"IHR_ENVIRONMENT": good}, GOOD_ENV
                    ),
                    [],
                )

    def test_an_unnamed_environment_is_one_problem_not_two(self):
        env = {k: v for k, v in GOOD_ENV.items() if k != "IHR_ENVIRONMENT"}
        found = secure.problems(GOOD_SETTINGS | {"IHR_ENVIRONMENT": ""}, env)
        self.assertEqual(
            [p for p in found if "IHR_ENVIRONMENT" in p],
            ["IHR_ENVIRONMENT is not set."],
        )

    def test_production_never_runs_on_the_proof_suffix(self):
        """The proof suffix (ihr-proof.in) belongs to throwaway stands (ADR-020 E2)."""
        for suffix in ("app.ihr-proof.in", "ihr-proof.in", "x.app.ihr-proof.in."):
            settings = GOOD_SETTINGS | {
                "IHR_TENANT_DOMAIN_SUFFIX": suffix,
                "ALLOWED_HOSTS": [f".{suffix.strip('.')}", "backend"],
            }
            with self.subTest(suffix=suffix):
                found = secure.problems(settings, GOOD_ENV)
                self.assertTrue(
                    any("proof" in p and "production" in p for p in found), found
                )
                for synthetic in ("staging", "proof"):
                    self.assertEqual(
                        secure.problems(
                            settings | {"IHR_ENVIRONMENT": synthetic}, GOOD_ENV
                        ),
                        [],
                    )
        # a different domain that merely ends the same way is not the proof suffix
        near = GOOD_SETTINGS | {
            "IHR_TENANT_DOMAIN_SUFFIX": "app.notihr-proof.in",
            "ALLOWED_HOSTS": [".app.notihr-proof.in", "backend"],
        }
        self.assertEqual(secure.problems(near, GOOD_ENV), [])

    def test_the_watch_needs_a_known_name_and_never_production_on_the_proof_suffix(
        self,
    ):
        settings = {
            "DEBUG": False,
            "IHR_TENANT_DOMAIN_SUFFIX": "app.hospital-test.in",
            "IHR_ENVIRONMENT": "production-1",
            "IHR_DB_ROLES": "required",
            "IHR_DB_ROLE": "watch",
            "IHR_DB_LIMITS": "watch",
        }
        env = {
            "DATABASE_URL": "postgres://ihr_audit_watch:" + "w" * 43 + "@db:5432/ihr",
            "IHR_AUDIT_ANCHOR_READ_KEY": "reader",
        }
        self.assertEqual(secure.watch_problems(settings, env), [])
        for bad in ("", "prod", "Production-1", "staging "):
            with self.subTest(value=bad):
                found = secure.watch_problems(settings | {"IHR_ENVIRONMENT": bad}, env)
                self.assertTrue(any("IHR_ENVIRONMENT" in p for p in found), found)
        proof = settings | {"IHR_TENANT_DOMAIN_SUFFIX": "app.ihr-proof.in"}
        self.assertTrue(
            any("proof" in p for p in secure.watch_problems(proof, env)),
            secure.watch_problems(proof, env),
        )
        self.assertEqual(
            secure.watch_problems(proof | {"IHR_ENVIRONMENT": "proof"}, env), []
        )


class DeploymentSettingsImportTests(SimpleTestCase):
    """The real settings module, imported in a fresh process as a server would."""

    def _import(self, env):
        base = {
            k: v
            for k, v in os.environ.items()
            # storage settings come from GOOD_ENV only: CI's test container sets CARE's own
            # BUCKET_KEY, which a server must never have (P1-T20b)
            if not k.startswith(
                (
                    "DJANGO_",
                    "IHR_",
                    "JWKS",
                    "SENTRY",
                    "SNOWSTORM",
                    "BUCKET_",
                    "FILE_UPLOAD_",
                    "FACILITY_S3_",
                )
            )
        }
        return subprocess.run(  # noqa: S603 (our own interpreter and a fixed module)
            [
                sys.executable,
                "-c",
                "import django, os; os.environ['DJANGO_SETTINGS_MODULE']="
                "'ihr_platform.settings.deployment'; django.setup(); "
                "from django.conf import settings; print(settings.ALLOWED_HOSTS)",
            ],
            env=base | env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def test_starts_with_safe_environment(self):
        result = self._import(GOOD_ENV | {"JWKS_BASE64": generate_encoded_jwks()})
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn(".app.hospital-test.in", result.stdout)

    def test_the_real_settings_refuse_a_missing_or_unknown_environment(self):
        """P1-T32 E1: no default stands in for IHR_ENVIRONMENT any more (it was "staging")."""
        base = GOOD_ENV | {"JWKS_BASE64": generate_encoded_jwks()}
        missing = self._import(
            {k: v for k, v in base.items() if k != "IHR_ENVIRONMENT"}
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("IHR_ENVIRONMENT is not set.", missing.stderr)
        typo = self._import(base | {"IHR_ENVIRONMENT": "prod"})
        self.assertNotEqual(typo.returncode, 0)
        self.assertIn("IHR_ENVIRONMENT must be", typo.stderr)

    def test_the_restore_drill_refuses_a_missing_or_unknown_environment(self):
        """P1-T32 E1: settings.drill names the instance whose dump it restores, never guesses."""

        live = {  # what the drill refuses (CI's test container sets some of them)
            "REDIS_URL", "CELERY_BROKER_URL", "BUCKET_KEY", "BUCKET_SECRET",
            "FILE_UPLOAD_KEY", "FILE_UPLOAD_SECRET", "FACILITY_S3_KEY",
            "FACILITY_S3_SECRET", "EMAIL_HOST_PASSWORD",
        }  # fmt: skip

        def drill(**extra):
            env = (
                {
                    k: v
                    for k, v in os.environ.items()
                    if not k.startswith(("DJANGO_", "IHR_", "SENTRY")) and k not in live
                }
                | {"DATABASE_URL": GOOD_ENV["DATABASE_URL"]}
                | extra
            )
            return subprocess.run(  # noqa: S603 (our own interpreter and a fixed module)
                [
                    sys.executable,
                    "-c",
                    "import django, os; os.environ['DJANGO_SETTINGS_MODULE']="
                    "'ihr_platform.settings.drill'; django.setup(); "
                    "from django.conf import settings; print(settings.IHR_ENVIRONMENT)",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )

        for bad in ({}, {"IHR_ENVIRONMENT": "prod"}, {"IHR_ENVIRONMENT": "staging "}):
            with self.subTest(env=bad):
                result = drill(**bad)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("IHR_ENVIRONMENT must be", result.stderr)
        for good in ("production-1", "staging", "proof"):
            with self.subTest(env=good):
                result = drill(IHR_ENVIRONMENT=good)
                self.assertEqual(result.returncode, 0, result.stderr[-1500:])
                self.assertEqual(result.stdout.strip(), good)

    def test_sentry_is_refused_before_it_could_send_anything(self):
        dsn = "https://k@o1.ingest.sentry.io/1"
        env = GOOD_ENV | {"JWKS_BASE64": generate_encoded_jwks(), "SENTRY_DSN": dsn}
        result = self._import(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SENTRY_DSN must stay unset", result.stderr)
        self.assertNotIn("sentry_sdk", result.stderr)

    def test_the_backend_refuses_to_start_without_its_client_key(self):
        """P1-T8d2: a process that signs (IHR_CLIENT_KEY_PURPOSE) must hold its own key."""
        import tempfile

        from ihr_platform import client_keys

        root = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, root)
        env = GOOD_ENV | {
            "JWKS_BASE64": generate_encoded_jwks(),
            "IHR_CLIENT_KEY_PURPOSE": "backend",
            "IHR_CLIENT_KEY_DIR": root,
        }
        result = self._import(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No backend client key", result.stderr)
        client_keys.KeyStore(root).ensure("backend")
        result = self._import(env)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_refuses_care_defaults_and_names_every_problem(self):
        result = self._import({"DATABASE_URL": GOOD_ENV["DATABASE_URL"]})
        self.assertNotEqual(result.returncode, 0)
        for expected in (
            "Refusing to start with unsafe settings",
            "DJANGO_SECRET_KEY is not set.",
            "JWKS_BASE64 is not set.",
            "CARE's published default",
            "'*' is not allowed",
            "IHR_TENANT_DOMAIN_SUFFIX",
            "EMAIL_HOST is not set",
            "IHR_KEYCLOAK_SMTP_HOST and _FROM must be set",
        ):
            self.assertIn(expected, result.stderr)
