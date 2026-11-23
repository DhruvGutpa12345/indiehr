"""The "synthetic data only" bar (P1-T32 E5, ADR-020 §3; design docs/reviews/p1-t32/design.md §7)."""

import re

from django.test import SimpleTestCase
from ihr_platform import environment

from ihr_tenant_settings.device_time import WARNING_SCRIPT
from ihr_tenant_settings.synthetic_bar import SYNTHETIC_SCRIPT


class BannerTextTests(SimpleTestCase):
    def test_the_texts_are_fixed(self):
        self.assertEqual(
            environment.BANNER_EN, "Synthetic data only: do not enter real patients."
        )
        self.assertEqual(
            environment.BANNER_HI,
            "केवल कृत्रिम (सिंथेटिक) डेटा: असली मरीज़ों की जानकारी दर्ज न करें।",
        )


class SyntheticScriptTests(SimpleTestCase):
    def test_carries_both_texts(self):
        self.assertIn(environment.BANNER_EN, SYNTHETIC_SCRIPT)
        self.assertIn(environment.BANNER_HI, SYNTHETIC_SCRIPT)

    def test_its_own_permanent_bar(self):
        self.assertIn('var BAR_ID = "ihr-synthetic";', SYNTHETIC_SCRIPT)
        self.assertIn('setAttribute("role", "note")', SYNTHETIC_SCRIPT)
        self.assertNotIn('"alert"', SYNTHETIC_SCRIPT)
        # one z-index below the release gate's overlay (2147483647)
        self.assertIn("z-index:2147483646", SYNTHETIC_SCRIPT)
        self.assertIn("position:fixed;top:0;left:0;right:0", SYNTHETIC_SCRIPT)
        # permanent: nothing removes it and it has no close button
        self.assertNotIn("removeChild", SYNTHETIC_SCRIPT)
        self.assertNotIn("button", SYNTHETIC_SCRIPT)

    def test_publishes_its_height_for_the_ist_bar(self):
        self.assertIn('"--ihr-synthetic-h"', SYNTHETIC_SCRIPT)
        self.assertIn("setProperty", SYNTHETIC_SCRIPT)

    def test_reserves_its_own_height_without_touching_the_body_padding(self):
        # The IST bar owns `body{padding-top}`; this one pushes the page with `html` so the two
        # stack in any order. The app's full-height fixed panels start below it.
        self.assertIn("html{padding-top:var(--ihr-synthetic-h)", SYNTHETIC_SCRIPT)
        self.assertNotIn("body{", SYNTHETIC_SCRIPT)
        self.assertIn(".fixed.inset-y-0{top:var(--ihr-synthetic-h)", SYNTHETIC_SCRIPT)

    def test_rechecks_and_waits_for_the_page(self):
        self.assertIn('"DOMContentLoaded"', SYNTHETIC_SCRIPT)
        self.assertIn("if (!document.body) return;", SYNTHETIC_SCRIPT)
        self.assertIn('"resize"', SYNTHETIC_SCRIPT)
        self.assertIn("setInterval(", SYNTHETIC_SCRIPT)

    def test_text_is_never_parsed_as_html(self):
        for banned in ("innerHTML", "insertAdjacentHTML", "document.write", "eval("):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, SYNTHETIC_SCRIPT)
        self.assertIn(".textContent =", SYNTHETIC_SCRIPT)

    def test_can_never_close_a_script_tag(self):
        self.assertNotIn("<", SYNTHETIC_SCRIPT)

    def test_is_one_balanced_self_contained_function_inside_a_try(self):
        script = SYNTHETIC_SCRIPT.strip()
        self.assertTrue(script.startswith("(function () {"))
        self.assertTrue(script.endswith("})();"))
        self.assertIn("try {", script)  # a banner must never break the page
        self.assertEqual(SYNTHETIC_SCRIPT.count("(function () {"), 1)
        code = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', script)
        code = re.sub(r"'(?:[^'\\\n]|\\.)*'", "''", code)
        code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
        for opening, closing in ("()", "[]", "{}"):
            with self.subTest(pair=opening + closing):
                self.assertEqual(code.count(opening), code.count(closing))

    def test_does_not_share_ids_with_the_ist_bar(self):
        self.assertNotIn("ihr-tz-warning", SYNTHETIC_SCRIPT)
        self.assertNotIn(
            "ihr-synthetic", WARNING_SCRIPT.replace("--ihr-synthetic-h", "")
        )
