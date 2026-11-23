"""Which instance is this? (P1-T32 E1, ADR-020; design docs/reviews/p1-t32/design.md §2)

``ihr_platform.environment`` is the one place that knows the environment names and the one
canonical regex. Every other copy of the regex (shell, scripts) is checked against it by
``scripts/test_environment_regex.py`` on the host; the copies a tenant-test container can see
(everything under ``plugins/``) are checked here as well.
"""

import re
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from ihr_platform import environment

PLUGINS = Path(__file__).resolve().parents[2]
# Any text that looks like a copy of the name regex (the three server shapes joined by |)
COPY_RE = re.compile(r"\^\(production-[^\n]*?\)\$")
CANONICAL = r"^(production-[1-9][0-9]{0,2}|staging|proof)$"


class NameTests(SimpleTestCase):
    def test_unset_is_dev(self):
        with override_settings(IHR_ENVIRONMENT=""):
            self.assertEqual(environment.name(), "dev")
        with override_settings():
            from django.conf import settings

            if hasattr(settings, "IHR_ENVIRONMENT"):
                del settings.IHR_ENVIRONMENT
            self.assertEqual(environment.name(), "dev")

    def test_the_setting_is_the_name(self):
        for value in ("production-1", "production-12", "staging", "proof"):
            with self.subTest(value=value), override_settings(IHR_ENVIRONMENT=value):
                self.assertEqual(environment.name(), value)

    def test_dev_and_test_settings_leave_it_unset(self):
        """db_roles.off_problems treats any non-empty value as "on a server" (P1-T19b)."""
        from django.conf import settings

        self.assertFalse(getattr(settings, "IHR_ENVIRONMENT", ""))
        self.assertEqual(environment.name(), "dev")

    def test_canonical_pattern_and_valid(self):
        self.assertEqual(environment.SERVER_PATTERN, CANONICAL)
        for good in (
            "production-1",
            "production-12",
            "production-999",
            "staging",
            "proof",
        ):
            self.assertTrue(environment.valid(good), good)
        for bad in (
            "",
            "dev",
            "prod",
            "production",
            "production-0",
            "production-01",
            "production-1000",
            "Production-1",
            "staging ",
            " staging",
            "staging\n",
            "staging\r",
            "'staging'",
            "production-1\n",
            "proof-1",
            None,
            1,
        ):
            self.assertFalse(environment.valid(bad), repr(bad))

    def test_production_is_the_special_case(self):
        """Anything that is not exactly dev, staging or proof counts as production (fail closed)."""
        for value in ("production-1", "production-12"):
            self.assertTrue(environment.is_production(value))
            self.assertFalse(environment.synthetic(value))
        for value in ("dev", "staging", "proof"):
            self.assertFalse(environment.is_production(value))
            self.assertTrue(environment.synthetic(value))
        # a typo in a name must never relax a production rule
        for typo in (
            "Production-1",
            "production-01",
            "prod",
            "staging ",
            "STAGING",
            "",
        ):
            with self.subTest(typo=typo):
                self.assertTrue(environment.is_production(typo))
                self.assertFalse(environment.synthetic(typo))

    def test_defaults_to_the_current_environment(self):
        with override_settings(IHR_ENVIRONMENT="production-1"):
            self.assertTrue(environment.is_production())
            self.assertFalse(environment.synthetic())
        with override_settings(IHR_ENVIRONMENT="staging"):
            self.assertFalse(environment.is_production())
            self.assertTrue(environment.synthetic())
        with override_settings(IHR_ENVIRONMENT=""):
            self.assertFalse(environment.is_production())
            self.assertTrue(environment.synthetic())  # dev

    def test_importing_it_needs_no_django_settings(self):
        """Settings modules import it, so it cannot import django.conf at import time."""
        code = (
            f"import sys; sys.path.insert(0, {str(PLUGINS)!r}); "
            "import ihr_platform.environment as e; "
            "assert 'django.conf' not in sys.modules, 'imported django.conf'; "
            "print(e.valid('staging'))"
        )
        out = subprocess.run(  # noqa: S603 (our interpreter, fixed code)
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(out.returncode, 0, out.stderr[-1500:])
        self.assertEqual(out.stdout.strip(), "True")


class RegexCopiesInPluginsTests(SimpleTestCase):
    """Every copy of the name regex under plugins/ equals the canonical one (design §2, L4)."""

    def test_every_copy_is_the_canonical_regex(self):
        copies = {}
        for path in PLUGINS.rglob("*"):
            if (
                not path.is_file()
                or path.suffix not in {".py", ".sh", ".yaml", ".yml", ".txt", ".lua"}
                or path.name == "test_environment.py"
                or "__pycache__" in path.parts
            ):
                continue
            for found in COPY_RE.findall(path.read_text(errors="ignore")):
                copies.setdefault(found, []).append(str(path.relative_to(PLUGINS)))
        self.assertTrue(copies, "environment.py itself must hold the canonical copy")
        self.assertEqual(set(copies), {CANONICAL}, copies)
