"""The synthetic banner on the operator console (P1-T32 E5, design docs/reviews/p1-t32/design.md §7.4)."""

import re

from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import SimpleTestCase, override_settings

from ihr_platform import environment
from ihr_platform.console.views import STYLE


def _banner():
    return Template("{% load ihr_environment %}{% synthetic_banner %}").render(
        Context()
    )


class ConsoleBannerTests(SimpleTestCase):
    def test_both_languages_carry_the_class_when_synthetic(self):
        for name in ("", "staging", "proof"):
            with self.subTest(name=name), override_settings(IHR_ENVIRONMENT=name):
                html = _banner()
                self.assertIn(
                    f'<div class="ihr-synthetic" lang="en">{environment.BANNER_EN}</div>',
                    html,
                )
                # the Hindi div carries the class too, or it is unstyled under style-src 'self'
                self.assertIn(
                    f'<div class="ihr-synthetic" lang="hi">{environment.BANNER_HI}</div>',
                    html,
                )
                self.assertNotIn(
                    "style=", html
                )  # the console's CSP forbids inline styles

    def test_nothing_on_production(self):
        for name in ("production-1", "production-12", "prod", "Staging", "x"):
            with self.subTest(name=name), override_settings(IHR_ENVIRONMENT=name):
                self.assertEqual(_banner().strip(), "")

    def test_first_child_of_body_on_every_page(self):
        for template in ("home", "signed_out", "new_hospital", "operation"):
            with (
                self.subTest(page=template),
                override_settings(IHR_ENVIRONMENT="staging"),
            ):
                html = render_to_string(f"ihr_console/{template}.html", {})
                self.assertRegex(
                    html, r"<body>\s*<div class=\"ihr-synthetic\" lang=\"en\">"
                )
            with (
                self.subTest(page=template, env="production"),
                override_settings(IHR_ENVIRONMENT="production-1"),
            ):
                html = render_to_string(f"ihr_console/{template}.html", {})
                self.assertNotIn("ihr-synthetic", html)

    def test_the_stylesheet_styles_the_class(self):
        self.assertRegex(STYLE, r"\.ihr-synthetic\s*\{")
        rule = re.search(r"\.ihr-synthetic\s*\{[^}]*\}", STYLE).group(0)
        self.assertNotIn("<", STYLE)
        self.assertIn("background", rule)
