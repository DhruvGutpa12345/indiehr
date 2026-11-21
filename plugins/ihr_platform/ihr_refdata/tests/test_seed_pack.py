"""The demo seed's waiting pack item (P1-T24c, G5).

The dev stack has one pack release (``care-valuesets`` v1), so nothing waits for a decision and
the pack updates screen is empty in a click-through. ``seeding.make_waiting_pack_item`` edits
one system value set's name (synthetic) and has the engine re-read the pack, so that item is
held as pending ("unknown origin": the hospital's row differs and no earlier apply is recorded
for it). It must leave exactly one pending item and be safe to run again. Pack decisions
(accept, keep) are the screen's own click-through; running the seed after one makes a new one.
"""

from io import StringIO
from pathlib import Path
from unittest import mock

from care.emr.models.valueset import ValueSet
from django.test import TestCase, override_settings
from ihr_platform import seeding
from ihr_platform.management.commands import ihr_seed_demo
from ihr_platform.provisioning import runner
from ihr_platform.testing import as_owner

from ihr_refdata.models import PackItem
from ihr_refdata.testing import TEST_PACK, Packs, vs
from ihr_refdata.tests.test_api import apply

ITEM = "ihr-seed-a"


# Django's test runner turns DEBUG off; the seed is dev-only, so the class turns it on
@override_settings(DEBUG=True)
class SeedWaitingPackItem(TestCase):
    databases = {"default"}

    def setUp(self):
        self.packs = Packs(self)
        with as_owner():
            ValueSet._base_manager.all().delete()
            self.packs.add(1, *(vs(s, "1-8") for s in (ITEM, "ihr-seed-b")))
        apply()

    def waiting(self):
        return list(
            PackItem.objects.filter(state="pending").values_list("pack", "item")
        )

    def test_leaves_exactly_one_pending_item_and_is_safe_to_run_again(self):
        self.assertEqual(self.waiting(), [])
        self.assertTrue(
            seeding.make_waiting_pack_item(pack=TEST_PACK, item=ITEM, operator="seed")
        )
        self.assertEqual(self.waiting(), [(TEST_PACK, ITEM)])
        record = PackItem.objects.get(pack=TEST_PACK, item=ITEM)
        self.assertEqual(record.reason, "unknown-origin")
        self.assertEqual(record.pending_version, 1)
        self.assertEqual(
            ValueSet._base_manager.get(slug=ITEM).name, seeding.WAITING_PACK_NAME
        )
        # again: nothing added, nothing changed
        stamp = record.changed_at
        self.assertFalse(
            seeding.make_waiting_pack_item(pack=TEST_PACK, item=ITEM, operator="seed")
        )
        self.assertEqual(self.waiting(), [(TEST_PACK, ITEM)])
        self.assertEqual(PackItem.objects.get(pk=record.pk).changed_at, stamp)
        # the other item is untouched
        self.assertEqual(PackItem.objects.get(item="ihr-seed-b").state, "current")

    def test_a_decided_item_is_waiting_again_on_the_next_run(self):
        """After a click-through (kept or accepted) the seed makes a fresh one."""
        seeding.make_waiting_pack_item(pack=TEST_PACK, item=ITEM, operator="seed")
        PackItem.objects.filter(item=ITEM).update(state="kept")
        self.assertTrue(
            seeding.make_waiting_pack_item(pack=TEST_PACK, item=ITEM, operator="seed")
        )
        self.assertEqual(self.waiting(), [(TEST_PACK, ITEM)])

    def test_the_real_item_is_one_of_the_care_value_sets_the_pack_ships(self):
        shipped = Path(__file__).resolve().parents[1] / "packs" / seeding.WAITING_PACK
        slugs = {
            line.strip()
            for line in (shipped / "expected_slugs.txt").read_text().splitlines()
            if line.strip() and not line.startswith("#")
        }
        self.assertIn(seeding.WAITING_PACK_ITEM, slugs)
        self.assertEqual(seeding.WAITING_PACK, "care-valuesets")

    def test_it_never_touches_a_pack_that_is_not_there(self):
        with self.assertRaises(RuntimeError):
            seeding.make_waiting_pack_item(
                pack=TEST_PACK, item="no-such-item", operator="seed"
            )

    def test_it_refuses_outside_development(self):
        """Defence in depth (review P4): the function checks DEBUG itself, not only its caller."""
        with (
            override_settings(DEBUG=False),
            self.assertRaises(RuntimeError),
        ):
            seeding.make_waiting_pack_item(pack=TEST_PACK, item=ITEM, operator="seed")
        self.assertEqual(self.waiting(), [])
        self.assertNotEqual(
            ValueSet._base_manager.get(slug=ITEM).name, seeding.WAITING_PACK_NAME
        )


class SeedCommandSurvivesAFailedPackItem(TestCase):
    """Review P3: a failed pack step must not abort the seed before the passwords are set."""

    def waiting_item(self):
        return ihr_seed_demo._waiting_pack_item(StringIO())

    def test_a_runtime_error_is_reported_as_failed(self):
        err = StringIO()
        with mock.patch.object(
            ihr_seed_demo, "make_waiting_pack_item", side_effect=RuntimeError("x")
        ):
            self.assertEqual(ihr_seed_demo._waiting_pack_item(err), "failed")
        self.assertIn("pack", err.getvalue().lower())

    def test_a_busy_hospital_is_reported_as_failed(self):
        err = StringIO()
        with mock.patch.object(
            ihr_seed_demo,
            "make_waiting_pack_item",
            side_effect=runner.ProvisioningError("busy"),
        ):
            self.assertEqual(ihr_seed_demo._waiting_pack_item(err), "failed")

    def test_success_and_nothing_to_do_pass_through(self):
        for value in (True, False):
            with mock.patch.object(
                ihr_seed_demo, "make_waiting_pack_item", return_value=value
            ):
                self.assertIs(self.waiting_item(), value)
