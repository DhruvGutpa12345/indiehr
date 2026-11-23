"""The demo seed still creates CARE's demo forms on CARE 3.1 (P2-T0a)."""

import json
import uuid
from pathlib import Path

from care.emr.models.organization import Organization
from care.emr.models.questionnaire import Questionnaire
from care.fixtures.base import CareFixtureBase
from care.users.models import User
from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from ihr_platform import seeding

FIXTURES = Path(settings.BASE_DIR) / "data" / "questionnaire_fixtures.json"


class DemoForms(TestCase):
    def test_cares_demo_forms_are_created_at_instance_level(self):
        """CARE 3.1 made auth_context required and its own fixture file has none, so every
        demo form failed quietly (the loader drops FixtureError) and the seed had no forms."""
        superuser = User.objects.create_superuser(
            username=f"seed-{uuid.uuid4().hex[:8]}",
            email="seed@example.test",
            password="x-Unused-1",
        )
        client = APIClient(HTTP_HOST="testserver")
        client.force_authenticate(superuser)
        base = CareFixtureBase(client)
        district = Organization.objects.create(name="Seed district", org_type="govt")
        forms = json.loads(FIXTURES.read_text())
        self.assertTrue(forms)
        self.assertTrue(all("auth_context" not in form for form in forms))

        create = seeding.questionnaire_with_auth_context(
            CareFixtureBase.create_questionnaire
        )
        create(base, [str(district.external_id)], forms[0])

        made = Questionnaire.objects.get(slug=forms[0]["slug"])
        self.assertEqual(made.auth_context, "instance")

    def test_a_given_auth_context_is_kept(self):
        seen = []
        create = seeding.questionnaire_with_auth_context(
            lambda base, organizations, data: seen.append(data)
        )
        create(None, [], {"slug": "x", "auth_context": "facility"})
        create(None, [], {"slug": "y"})
        self.assertEqual([d["auth_context"] for d in seen], ["facility", "instance"])
