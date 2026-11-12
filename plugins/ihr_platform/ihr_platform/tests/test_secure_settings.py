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
}
GOOD_SETTINGS = {
    "DEBUG": False,
    "IS_PRODUCTION": True,
    "SECRET_KEY": GOOD_KEY,
    "IHR_TENANT_DOMAIN_SUFFIX": "app.hospital-test.in",
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
}


class SecureRulesTests(SimpleTestCase):
    def test_safe_settings_pass(self):
        self.assertEqual(secure.problems(GOOD_SETTINGS, GOOD_ENV), [])

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


class DeploymentSettingsImportTests(SimpleTestCase):
    """The real settings module, imported in a fresh process as a server would."""

    def _import(self, env):
        base = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("DJANGO_", "IHR_", "JWKS", "SENTRY", "SNOWSTORM"))
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
