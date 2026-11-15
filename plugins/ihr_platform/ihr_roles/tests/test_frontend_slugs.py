"""The web app's slug sets mirror the registry (P1-T11c1; design-c.md §13.2 FF2).

``frontend_slugs.json`` must be byte-identical to what ``ihr_frontend_slugs --write`` would
write; ``scripts/check-ihr-frontend.py`` then byte-compares it with the web app's
``src/Utils/ihr/ihrSlugs.json`` on the host.
"""

import io
import json

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from ihr_roles import frontend, permissions, registry

FIX = (
    "run: scripts/care-tenancy.sh ihr_frontend_slugs > "
    "plugins/ihr_platform/ihr_roles/frontend_slugs.json, then copy it to "
    "apps/frontend/src/Utils/ihr/ihrSlugs.json"
)


class FrontendSlugs(SimpleTestCase):
    def test_the_file_is_what_the_command_writes(self):
        self.assertTrue(frontend.PATH.exists(), FIX)
        self.assertEqual(
            frontend.PATH.read_bytes().decode("utf-8"), frontend.render(), FIX
        )

    def test_the_sets_are_the_registry(self):
        data = json.loads(frontend.PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            set(data),
            {
                "bedForBilling",
                "clinical",
                "grants",
                "ours",
                "patientOpening",
                "restricted",
            },
        )
        self.assertEqual(set(data["clinical"]), set(registry.CLINICAL_SLUGS))
        self.assertEqual(set(data["patientOpening"]), set(registry.PATIENT_OPENING))
        self.assertEqual(set(data["restricted"]), set(registry.RESTRICTED))
        self.assertEqual(set(data["bedForBilling"]), set(registry.BED_FOR_BILLING))
        self.assertEqual(set(data["ours"]), set(permissions.SLUGS))
        self.assertEqual(
            {k: set(v) for k, v in data["grants"].items()},
            {k: set(v) for k, v in registry.GRANTS.items()},
        )

    def test_stable_and_sorted(self):
        text = frontend.render()
        self.assertEqual(text, frontend.render())
        self.assertTrue(text.endswith("}\n"))
        data = json.loads(text)
        for key, value in data.items():
            if isinstance(value, list):
                self.assertEqual(value, sorted(value), key)
        for role, slugs in data["grants"].items():
            self.assertEqual(slugs, sorted(set(slugs)), role)

    def test_command_prints_and_checks(self):
        out = io.StringIO()
        call_command("ihr_frontend_slugs", stdout=out, stderr=io.StringIO())
        self.assertEqual(out.getvalue(), frontend.render())
        call_command("ihr_frontend_slugs", "--check", stderr=io.StringIO())

    def test_check_fails_on_a_stale_file(self):
        real = frontend.render
        try:
            frontend.render = lambda: real().replace("Cashier", "Cashier ", 1)
            with self.assertRaises(CommandError):
                call_command("ihr_frontend_slugs", "--check", stderr=io.StringIO())
        finally:
            frontend.render = real
