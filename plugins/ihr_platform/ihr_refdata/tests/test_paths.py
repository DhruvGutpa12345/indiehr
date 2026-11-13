"""Every path that once ran CARE's sync_valueset now applies the packs (P1-T15a, S10).

CARE's command is made to fail here, so a path that still reached it would fail the test.
Provisioning is covered the same way in ihr_platform.tests.test_provisioning (runner.run end
to end). One new hospital (about a minute of migrations) serves both checks below.
"""

from unittest import mock

from care.emr.models.valueset import ValueSet
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase
from django_tenants.utils import schema_context
from ihr_platform.models import Domain, Hospital, RetiredAddress
from ihr_platform.provisioning import runner
from ihr_platform.seeding import load_demo_data
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_platform.testing import owner_for_test

from ihr_refdata import engine, manifest
from ihr_refdata.models import PackApply, PackItem

SLUG = "packs-cli"


def refuse_sync_valueset():
    return mock.patch(
        "care.emr.management.commands.sync_valueset.Command.handle",
        side_effect=AssertionError("CARE's sync_valueset must not run"),
    )


class PathTests(SimpleTestCase):
    databases = {"default"}

    def tearDown(self):
        connection.set_schema(TEST_SCHEMA)

    def _drop(self):
        connection.set_schema_to_public()
        for hospital in Hospital.objects.filter(slug=SLUG):
            Domain.objects.filter(tenant=hospital).delete()
            hospital.delete(force_drop=True)
        RetiredAddress.objects.filter(value__startswith=SLUG).delete()
        connection.set_schema(TEST_SCHEMA)

    def test_create_tenant_and_the_demo_seed_use_the_packs(self):
        owner_for_test(self)  # ihr_create_tenant and the demo seed's packs: the owner
        release = manifest.latest()["care-valuesets"]
        self.addCleanup(self._drop)
        connection.set_schema_to_public()
        with refuse_sync_valueset():
            call_command("ihr_create_tenant", slug=SLUG, name="Packs CLI", verbosity=0)
        hospital = Hospital.objects.get(slug=SLUG)
        self.assertEqual(hospital.status, Hospital.Status.ACTIVE)
        with schema_context(hospital.schema_name):
            self.assertEqual(
                ValueSet.objects.filter(is_system_defined=True).count(),
                release.item_count,
            )
            self.assertEqual(
                PackItem.objects.filter(pack="care-valuesets", state="current").count(),
                release.item_count,
            )
            self.assertEqual(PackApply.objects.get().actor, "ihr_create_tenant")

        # The demo seed (CARE's fixture context runs sync_valueset first): routed to the
        # packs, which find nothing to do; CARE's demo data itself is left out here.
        with (
            refuse_sync_valueset(),
            mock.patch(
                "care.fixtures.scripts.default_fixtures.load_fixtures"
            ) as fixtures,
            mock.patch.object(
                engine, "apply_packs", wraps=engine.apply_packs
            ) as applied,
        ):
            load_demo_data(hospital.schema_name, f"{SLUG}.localhost")
        fixtures.assert_called_once()
        [(args, kwargs)] = applied.call_args_list
        self.assertEqual((args[0].slug, kwargs["operator"]), (SLUG, "demo-seed"))
        with schema_context(hospital.schema_name):
            self.assertEqual(PackApply.objects.count(), 1)  # nothing new to apply

        # The hospital is live: the seed takes its lock, and a busy one is a clear refusal
        # (C9, S5), never a race with the start-up apply
        with (
            runner.exclusive(SLUG),
            mock.patch("care.fixtures.scripts.default_fixtures.load_fixtures"),
            mock.patch.object(engine, "apply_packs") as applied,
            self.assertRaisesMessage(CommandError, f"Another job holds {SLUG}"),
        ):
            load_demo_data(hospital.schema_name, f"{SLUG}.localhost")
        applied.assert_not_called()
