"""Static checks of the mail settings (P1-T8b4): staging.sh mail-init, the proof stand-in
(compose.mailtest.yaml) and where the password may go. The live proof is local-proof plus
`scripts/staging.sh mail-check`. Run: python3 -m unittest scripts/test_staging_mail.py
"""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STAGING_SH = (ROOT / "scripts" / "staging.sh").read_text()
MAILTEST = yaml.safe_load((ROOT / "deploy" / "staging" / "compose.mailtest.yaml").read_text())
SECURE = (ROOT / "plugins" / "ihr_platform" / "ihr_platform" / "settings" / "secure.py").read_text()


def function(name):
    return STAGING_SH.split(f"{name}() {{", 1)[1].split("\n}\n", 1)[0]


class MailInit(unittest.TestCase):
    def test_token_never_from_the_command_line(self):
        body = function("mail_init")
        self.assertIn("read -rsp", body)  # keyboard, not echoed
        self.assertIn("IFS= read -r token", body)  # or stdin
        self.assertNotIn('token="$2"', body)
        self.assertNotIn("--token", body)

    def test_defaults_are_zeptomail_india_with_starttls(self):
        body = function("mail_init")
        self.assertIn('host="smtp.zeptomail.in" port=587', body)
        self.assertIn('user="emailapikey"', body)
        self.assertIn('echo "EMAIL_USE_TLS=True"', body)

    def test_keycloak_password_only_in_the_one_off(self):
        self.assertIn("write_secret mail.env", function("mail_init"))
        admin = function("admin_manage")
        self.assertIn("extra=(-e IHR_KEYCLOAK_SMTP_PASSWORD)", admin)
        # never written into app.env's Keycloak block
        self.assertNotIn('echo "IHR_KEYCLOAK_SMTP_PASSWORD', function("mail_init"))

    def test_only_starttls_port(self):
        self.assertIn('die "only port 587 (STARTTLS) is supported"', function("mail_init"))

    def test_unsafe_token_characters_refused(self):
        self.assertIn("the token has characters an env file cannot hold", function("mail_init"))


class StandIn(unittest.TestCase):
    def test_behaves_like_the_real_service(self):
        cmd = MAILTEST["services"]["smtp-test"]["command"]
        self.assertIn("--smtp-require-starttls", cmd)
        self.assertTrue(any(c.startswith("--smtp-auth-file=") for c in cmd))
        self.assertNotIn("--smtp-auth-accept-any", cmd)
        self.assertNotIn("--smtp-auth-allow-insecure", cmd)

    def test_hardened(self):
        svc = MAILTEST["services"]["smtp-test"]
        self.assertRegex(svc["image"], r"@sha256:[0-9a-f]{64}$")
        self.assertEqual(svc["user"], "65534:65534")
        self.assertEqual(svc["cap_drop"], ["ALL"])
        self.assertTrue(svc["read_only"])
        self.assertNotIn("ports", svc)  # nothing published

    def test_only_used_when_asked(self):
        self.assertIn('[[ "$(env_get IHR_MAIL)" == test ]] && files+=(-f "$DIR/compose.mailtest.yaml")', STAGING_SH)


class RefusesToStartWithoutMail(unittest.TestCase):
    def test_rules(self):
        for text in ("EMAIL_HOST is not set", "EMAIL_USE_TLS (or EMAIL_USE_SSL) must be on",
                     "EMAIL_FROM must be our own sender", "IHR_KEYCLOAK_SMTP_HOST and _FROM must be set",
                     "Keycloak's mail server must use TLS"):
            self.assertIn(text, SECURE)


if __name__ == "__main__":
    unittest.main()
