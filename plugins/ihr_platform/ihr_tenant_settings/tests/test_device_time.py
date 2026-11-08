"""The warning bar for computers not on India Standard Time (P1-T7b)."""

import re

from django.test import SimpleTestCase

from ihr_tenant_settings.device_time import WARNING_SCRIPT


class WarningScriptTests(SimpleTestCase):
    def test_checks_both_the_offset_and_the_zone_name(self):
        self.assertIn("getTimezoneOffset()", WARNING_SCRIPT)
        self.assertIn("var IST_OFFSET = -330;", WARNING_SCRIPT)
        self.assertIn("resolvedOptions().timeZone", WARNING_SCRIPT)
        self.assertIn('["Asia/Kolkata", "Asia/Calcutta"]', WARNING_SCRIPT)

    def test_bar_is_bilingual_and_says_how_to_fix_it(self):
        self.assertIn("India Standard Time (IST)", WARNING_SCRIPT)
        self.assertIn("This computer's time zone is ", WARNING_SCRIPT)
        self.assertIn("इस कंप्यूटर का टाइम ज़ोन ", WARNING_SCRIPT)
        self.assertIn('setAttribute("role", "alert")', WARNING_SCRIPT)

    def test_rechecks_and_waits_for_the_page(self):
        self.assertIn("setInterval(check, 60000)", WARNING_SCRIPT)
        self.assertIn('"visibilitychange"', WARNING_SCRIPT)
        self.assertIn('"DOMContentLoaded"', WARNING_SCRIPT)
        self.assertIn("if (!document.body) return;", WARNING_SCRIPT)

    def test_device_text_is_never_parsed_as_html(self):
        self.assertNotIn("innerHTML", WARNING_SCRIPT)
        self.assertNotIn("insertAdjacentHTML", WARNING_SCRIPT)
        self.assertNotIn("document.write", WARNING_SCRIPT)
        self.assertNotIn("eval(", WARNING_SCRIPT)
        self.assertIn(".textContent =", WARNING_SCRIPT)

    def test_can_never_close_a_script_tag(self):
        self.assertNotIn("<", WARNING_SCRIPT)

    def test_is_one_balanced_self_contained_function(self):
        script = WARNING_SCRIPT.strip()
        self.assertTrue(script.startswith("(function () {"))
        self.assertTrue(script.endswith("})();"))
        # Brackets balance outside strings and comments (a cheap structure check; the
        # script itself was run in Node under several zones while it was written).
        code = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', script)
        code = re.sub(r"'(?:[^'\\\n]|\\.)*'", "''", code)
        code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
        for opening, closing in ("()", "[]", "{}"):
            with self.subTest(pair=opening + closing):
                self.assertEqual(code.count(opening), code.count(closing))
