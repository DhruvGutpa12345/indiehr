"""Per-facility pack kinds (P1-T15b3; docs/reviews/p1-t15b/design.md §4 and "After the attack"
items 11, 13 and 14).

Test-only kinds on real CARE models (Q-K70 A, ihr_refdata/tests/kinds.py): ``t_specimen``
(SpecimenDefinition) and ``t_activity`` (ActivityDefinition, ``specimen_requirements`` linking
t_specimen items of the same facility). No catalogue content ships. Every test runs in the shared
test hospital inside a transaction that is rolled back; the facilities that were there are
soft-deleted for the test (rolled back too), so the live ones are exactly the test's own. Pack
applies and decisions are owner-run; the new-facility task and the backstop run as the app role
(the Celery workers' role, ``IHR_DB_ROLES=test``).
"""

import time
from io import StringIO
from unittest import mock

from care.emr.models.activity_definition import ActivityDefinition
from care.emr.models.specimen_definition import SpecimenDefinition
from care.emr.models.valueset import ValueSet
from care.facility.models import Facility
from care.users.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_tenants.utils import get_public_schema_name
from ihr_platform.models import Hospital
from ihr_platform.provisioning import runner
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_platform.testing import as_owner
from model_bakery import baker
from rest_framework.test import APIClient

from ihr_refdata import catalogue, engine, manifest, tasks
from ihr_refdata.models import PackApply, PackFacility, PackItem
from ihr_refdata.testing import Packs, vs
from ihr_refdata.tests.kinds import activity, specimen
from ihr_refdata.tests.test_api import hospital_admin

SLUG = "testhosp"
SPEC = "t-spec"  # the specimen pack
ACT = "a-act"  # the activity pack: sorts before t-spec, so only "after" puts it second
VS = "v-vs"  # a hospital-wide pack (value sets): never applied by the task or the backstop
URL = "/api/ihr/packs/updates/"
REASON = "hospital admin OK, ticket 31"


def _tag():
    return f"{time.time_ns() % 10**12:x}"


def spec_row(facility, key):
    return SpecimenDefinition._base_manager.get(
        facility=facility, slug=catalogue.slug_of(facility, key)
    )


def act_row(facility, key):
    return ActivityDefinition._base_manager.get(
        facility=facility, slug=catalogue.slug_of(facility, key)
    )


class FacilityCase(TestCase):
    databases = {"default"}

    def setUp(self):
        self.packs = Packs(self)
        self.key = _tag()
        with as_owner():
            # Only the test hospital is live, and only this test's facilities in it
            Hospital.objects.exclude(
                schema_name__in=[get_public_schema_name(), TEST_SCHEMA]
            ).update(status=Hospital.Status.SUSPENDED)
            Facility._base_manager.filter(deleted=False).update(deleted=True)
            ValueSet._base_manager.all().delete()
            self.user = User.objects.create_user(
                username=f"fac-{self.key}", password="x-Unused-1"
            )
            self.f1 = self.facility("One")
            self.f2 = self.facility("Two")
        self.hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)

    def facility(self, name):
        with as_owner():
            return baker.make(Facility, name=f"{name} {self.key}", created_by=self.user)

    def add_spec(self, version, *items):
        self.packs.add(version, *items, pack=SPEC, kind="t_specimen")

    def add_act(self, version, *items, after=(SPEC,)):
        self.packs.add(version, *items, pack=ACT, kind="t_activity", after=after)

    def apply(self):
        out, err = StringIO(), StringIO()
        with as_owner():
            call_command(
                "ihr_pack_apply", "--slug", SLUG, "--operator", "tester",
                stdout=out, stderr=err,
            )  # fmt: skip
        return out.getvalue(), err.getvalue()

    def pending_cmd(self, *args):
        out = StringIO()
        with as_owner():
            call_command("ihr_pack_pending", "--slug", SLUG, *args, stdout=out)
        return out.getvalue()

    def decide(self, flag, pack, key, facility):
        return self.pending_cmd(
            flag, f"{pack}:{key}", "--facility", str(facility.external_id),
            "--operator", "tester", "--reason", REASON,
        )  # fmt: skip

    def record(self, pack, key, facility):
        return PackItem.objects.get(pack=pack, item=key, facility=facility)

    def record_releases(self):
        """What the start-up one-shot does first: the fleet's record of every release."""
        with as_owner():
            engine.prepare("packs:tester", record=True)

    def both(self):
        """Specimens s1, s2 and activities a1 (s1, s2) and a2 (s2)."""
        self.add_spec(1, specimen("s1"), specimen("s2", code="SER"))
        self.add_act(1, activity("a1", "s1", "s2"), activity("a2", "s2"))


class ApplyTests(FacilityCase):
    def test_two_facilities_get_both_packs_with_their_own_ids_and_a_reapply_is_silent(
        self,
    ):
        self.both()
        out, _ = self.apply()
        # "after" puts the specimen pack first though a-act sorts before it
        self.assertLess(out.index(f"{SPEC} v1 applied"), out.index(f"{ACT} v1 applied"))
        for place in (self.f1, self.f2):
            s1, s2 = spec_row(place, "s1"), spec_row(place, "s2")
            self.assertEqual(act_row(place, "a1").specimen_requirements, [s1.pk, s2.pk])
            self.assertEqual(act_row(place, "a2").specimen_requirements, [s2.pk])
            self.assertEqual(
                self.record(ACT, "a1", place).target,
                f"f-{place.external_id}-a1",
            )
            self.assertEqual(self.record(ACT, "a1", place).state, "current")
        # links never cross: the same keys, different ids
        self.assertNotEqual(
            act_row(self.f1, "a1").specimen_requirements,
            act_row(self.f2, "a1").specimen_requirements,
        )
        # one PackApply per pack, its change list naming each facility
        applied = PackApply.objects.get(pack=ACT, outcome="applied")
        self.assertEqual(applied.counts["created"], 4)
        self.assertEqual(
            {c["facility"] for c in applied.changes},
            {str(self.f1.external_id), str(self.f2.external_id)},
        )
        self.assertEqual(
            set(PackFacility.objects.values_list("pack", "facility_id")),
            {(p, f.pk) for p in (SPEC, ACT) for f in (self.f1, self.f2)},
        )
        applies = PackApply.objects.count()
        with mock.patch.object(engine, "_reconcile", wraps=engine._reconcile) as run:
            out, _ = self.apply()
        run.assert_not_called()  # the short-circuit, per facility
        self.assertIn(f"{ACT} v1 unchanged", out)
        self.assertEqual(PackApply.objects.count(), applies)

    def test_an_edit_in_one_facility_leaves_the_other_automatic(self):
        self.add_spec(1, specimen("s1"))
        self.apply()
        row = spec_row(self.f1, "s1")
        row.title = "Their own title"
        row.save()
        self.add_spec(2, specimen("s1", title="Pack v2 title"))
        self.apply()
        self.assertEqual(spec_row(self.f1, "s1").title, "Their own title")
        self.assertEqual(
            (
                self.record(SPEC, "s1", self.f1).state,
                self.record(SPEC, "s1", self.f1).reason,
            ),
            ("pending", "edited"),
        )
        self.assertEqual(spec_row(self.f2, "s1").title, "Pack v2 title")
        self.assertEqual(self.record(SPEC, "s1", self.f2).state, "current")
        # decided in that facility only
        with self.assertRaisesRegex(CommandError, "name the facility"):
            self.pending_cmd(
                "--accept", f"{SPEC}:s1", "--operator", "tester", "--reason", REASON
            )
        self.decide("--accept", SPEC, "s1", self.f1)
        self.assertEqual(spec_row(self.f1, "s1").title, "Pack v2 title")
        last = PackApply.objects.filter(outcome="accepted").latest("id")
        self.assertEqual(last.counts["facility"], str(self.f1.external_id))

    def test_removal_retires_per_facility_and_links_to_it_stay(self):
        self.both()
        self.apply()
        self.add_spec(2, specimen("s1"))  # s2 leaves the pack
        self.add_act(2, activity("a1", "s1", "s2"), activity("a2", "s2"))
        self.apply()
        for place in (self.f1, self.f2):
            self.assertEqual(spec_row(place, "s2").status, "retired")
            self.assertEqual(self.record(SPEC, "s2", place).state, "retired")
            self.assertEqual(
                act_row(place, "a2").specimen_requirements, [spec_row(place, "s2").pk]
            )

    def test_the_unique_constraint_with_and_without_a_facility(self):
        now = timezone.now()
        common = {
            "pack": "u-pack",
            "kind": "valueset",
            "state": "current",
            "changed_at": now,
        }
        with as_owner():
            PackItem.objects.create(item="x", **common)
            with self.assertRaises(IntegrityError), transaction.atomic():
                PackItem.objects.create(item="x", **common)  # NULLS NOT DISTINCT
            PackItem.objects.create(item="x", facility=self.f1, **common)
            PackItem.objects.create(item="x", facility=self.f2, **common)
            with self.assertRaises(IntegrityError), transaction.atomic():
                PackItem.objects.create(item="x", facility=self.f1, **common)


class LinkTests(FacilityCase):
    def test_links_never_cross_facilities(self):
        """Mutation facility-links-cross: a row of facility One whose slug says facility Two
        is never Two's row nor Two's link target, whatever its slug."""
        with as_owner():
            planted = SpecimenDefinition.objects.create(
                facility=self.f1,
                slug=catalogue.slug_of(self.f2, "s9"),
                title="Planted in One",
                status="active",
                description="",
            )
        self.add_spec(1, specimen("s1"))
        self.add_act(1, activity("a9", "s9"))
        self.apply()
        for place in (self.f1, self.f2):
            record = self.record(ACT, "a9", place)
            self.assertEqual((record.state, record.reason), ("pending", "missing-link"))
            self.assertIn("specimen_requirements:s9", record.checks["missing-link"])
        self.assertFalse(
            ActivityDefinition._base_manager.filter(
                specimen_requirements__contains=[planted.pk]
            ).exists()
        )
        found = engine.kinds.get("t_specimen").at(self.f2).rows(["s9"])
        self.assertEqual(found, {})

    def test_a_deleted_target_waits_as_missing_link_until_restored(self):
        self.add_spec(1, specimen("s1"))
        self.add_act(1, activity("a1", "s1"))
        self.apply()
        gone = spec_row(self.f2, "s1")
        gone.deleted = True
        gone.save()
        self.add_act(2, activity("a1", "s1"), activity("a2", "s1"))
        self.apply()
        self.assertEqual(
            act_row(self.f1, "a2").specimen_requirements, [spec_row(self.f1, "s1").pk]
        )
        record = self.record(ACT, "a2", self.f2)
        self.assertEqual((record.state, record.reason), ("pending", "missing-link"))
        self.assertFalse(
            ActivityDefinition._base_manager.filter(
                facility=self.f2, slug=catalogue.slug_of(self.f2, "a2")
            ).exists()
        )
        # Review T1: the a1 already written there links to the deleted row: not in sync
        record = self.record(ACT, "a1", self.f2)
        self.assertEqual((record.state, record.reason), ("pending", "missing-link"))
        self.assertEqual(self.record(ACT, "a1", self.f1).state, "current")
        # Review E2: the backstop leaves it alone (no target yet) without the lock
        self.record_releases()
        with mock.patch.object(runner, "exclusive") as locked:
            self.assertEqual(
                tasks.fill(TEST_SCHEMA, actor=tasks.BEAT_ACTOR, missing_links=True),
                ("none", []),
            )
        locked.assert_not_called()
        # the hospital's deletion is its own: the specimen pack does not restore it
        self.assertEqual(self.record(SPEC, "s1", self.f2).state, "current")
        with self.assertRaisesRegex(CommandError, "missing-link"):
            self.decide("--accept", ACT, "a2", self.f2)
        # item 11: no target yet, so the start-up short-circuit does not reconcile
        with mock.patch.object(engine, "_reconcile", wraps=engine._reconcile) as run:
            self.apply()
        run.assert_not_called()
        gone.deleted = False
        gone.save()
        with mock.patch.object(engine, "_reconcile", wraps=engine._reconcile) as run:
            self.apply()
        self.assertEqual([c.args[2] for c in run.call_args_list], [self.f2])
        self.assertEqual(act_row(self.f2, "a2").specimen_requirements, [gone.pk])
        self.assertEqual(self.record(ACT, "a2", self.f2).state, "current")
        self.assertEqual(self.record(ACT, "a1", self.f2).state, "current")

    def test_a_link_to_a_replaced_target_is_re_pointed_to_the_live_row(self):
        """Review T1 / item 13: the hospital deleted s1 and made a new live s1; a1 follows the
        live row (find's rule), never the deleted one."""
        self.add_spec(1, specimen("s1"))
        self.add_act(1, activity("a1", "s1"))
        self.apply()
        old = spec_row(self.f2, "s1")
        with as_owner():
            old.deleted = True
            old.save()
            new = SpecimenDefinition.objects.create(
                facility=self.f2,
                slug=catalogue.slug_of(self.f2, "s1"),
                title="Test specimen s1",
                status="active",
                description="",
                type_collected=specimen("s1")["type_collected"],
            )
        self.add_act(2, activity("a1", "s1"), activity("a2", "s1"))
        self.apply()
        self.assertEqual(act_row(self.f2, "a1").specimen_requirements, [new.pk])
        self.assertEqual(act_row(self.f2, "a2").specimen_requirements, [new.pk])
        self.assertEqual(self.record(ACT, "a1", self.f2).state, "current")
        self.assertEqual(
            act_row(self.f1, "a1").specimen_requirements, [spec_row(self.f1, "s1").pk]
        )

    def test_one_live_and_one_missing_target_is_not_reconciled_again(self):
        """Review T2 / E1 / Q1: a held item is linkable only when every target is live."""
        self.both()
        self.apply()
        gone = spec_row(self.f2, "s2")
        with as_owner():
            gone.deleted = True
            gone.save()
        self.add_act(
            2, activity("a1", "s1", "s2"), activity("a2", "s2"), activity("a3", "s1")
        )
        self.apply()
        record = self.record(ACT, "a1", self.f2)
        self.assertEqual((record.state, record.reason), ("pending", "missing-link"))
        self.assertEqual(self.record(ACT, "a3", self.f2).state, "current")
        with mock.patch.object(engine, "_reconcile", wraps=engine._reconcile) as run:
            self.apply()
        run.assert_not_called()
        self.record_releases()
        with mock.patch.object(engine, "_reconcile", wraps=engine._reconcile) as run:
            outcome = tasks.fill(
                TEST_SCHEMA, actor=tasks.BEAT_ACTOR, missing_links=True
            )
        self.assertEqual(outcome, ("none", []))
        run.assert_not_called()

    def test_link_targets_are_locked_when_checked(self):
        """Review E4: the check and the write in one transaction see the same targets."""
        self.add_spec(1, specimen("s1"))
        self.apply()
        handler = engine.kinds.get("t_activity").at(self.f1)
        with CaptureQueriesContext(connection) as seen:
            self.assertEqual(handler.missing_link("a1", None, activity("a1", "s1")), "")
        self.assertTrue(
            any("FOR NO KEY UPDATE" in q["sql"] for q in seen), [q["sql"] for q in seen]
        )

    def test_a_duplicate_made_during_the_apply_is_found_after_commit(self):
        """Review E5: CARE's writers never take our lock; the recheck after commit holds the
        item as a conflict."""
        self.add_spec(1, specimen("s1"))
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.apply()
        with as_owner():
            SpecimenDefinition.objects.create(
                facility=self.f1,
                slug=catalogue.slug_of(self.f1, "s1"),
                title="Made by staff meanwhile",
                status="active",
                description="",
            )
            for callback in callbacks:
                callback()
        record = self.record(SPEC, "s1", self.f1)
        self.assertEqual((record.state, record.reason), ("pending", "conflict"))
        self.assertEqual(self.record(SPEC, "s1", self.f2).state, "current")

    def test_an_accept_refused_for_a_missing_link_is_stored_as_blocked(self):
        """Review E6: the list then shows the item as blocked."""
        self.add_spec(1, specimen("s1"))
        self.add_act(1, activity("a1", "s1"))
        self.apply()
        row = act_row(self.f1, "a1")
        with as_owner():
            row.title = "Their own title"
            row.save()
        self.add_act(2, activity("a1", "s1", title="Pack v2 title"))
        self.apply()
        self.assertEqual(self.record(ACT, "a1", self.f1).reason, "edited")
        gone = spec_row(self.f1, "s1")
        with as_owner():
            gone.deleted = True
            gone.save()
        with self.assertRaisesRegex(CommandError, "missing link"):
            self.decide("--accept", ACT, "a1", self.f1)
        record = self.record(ACT, "a1", self.f1)
        self.assertEqual((record.state, record.reason), ("pending", "missing-link"))
        self.assertIn("specimen_requirements:s1", record.checks["missing-link"])
        self.assertEqual(act_row(self.f1, "a1").title, "Their own title")

    def test_a_link_to_a_kind_this_build_lacks_is_a_pack_error(self):
        """Review E7: refused for the fleet (PackError), never a KeyError."""
        from ihr_refdata.tests.kinds import TActivity

        self.add_spec(1, specimen("s1"))
        self.add_act(1, activity("a1", "s1"))
        with (
            mock.patch.object(
                TActivity, "LINKS", {"specimen_requirements": "t_nowhere"}
            ),
            self.assertRaisesRegex(manifest.PackError, "not a kind of this build"),
        ):
            engine.prepare("packs:tester", record=False)

    def test_after_order_and_its_refusals(self):
        self.add_spec(1, specimen("s1"))
        with self.subTest("a link with no pack of its kind in after"):
            self.add_act(1, activity("a1", "s1"), after=())
            with self.assertRaisesRegex(
                CommandError, "no pack of that kind is in its after"
            ):
                self.apply()
        with self.subTest("an unknown pack"):
            self.add_act(1, activity("a1", "s1"), after=(SPEC, "nowhere"))
            with self.assertRaisesRegex(CommandError, "not a pack of this build"):
                self.apply()
        with self.subTest("a cycle"):
            self.add_act(1, activity("a1", "s1"), after=(SPEC,))
            self.packs.add(
                1, specimen("s1"), pack=SPEC, kind="t_specimen", after=(ACT,)
            )
            with self.assertRaisesRegex(CommandError, "make a cycle"):
                self.apply()
        self.assertFalse(
            SpecimenDefinition._base_manager.filter(facility=self.f1).exists()
        )

    def test_one_pack_failing_stops_the_later_packs(self):
        self.both()
        real = engine._reconcile

        def broken(release, handler, facility=None, **kw):
            if release.pack == SPEC:
                raise RuntimeError("injected")
            return real(release, handler, facility, **kw)

        with (
            mock.patch.object(engine, "_reconcile", side_effect=broken),
            self.assertRaises(CommandError),
        ):
            self.apply()
        self.assertFalse(PackItem.objects.filter(pack=ACT).exists())

    def test_a_server_refuses_a_test_kind_pack_for_the_fleet(self):
        """Q-K70 A: without the test flag (as on every server) the test kinds are unknown and
        prepare() refuses their packs before any hospital is touched."""
        self.add_spec(1, specimen("s1"))
        with (
            override_settings(IHR_REFDATA_TEST_KINDS=False),
            self.assertRaisesRegex(manifest.PackError, "unknown kind 't_specimen'"),
        ):
            engine.prepare("packs:tester", record=False)


class RowLookupTests(FacilityCase):
    """Item 13: lower-case keys, lower(slug) inside the facility, the live row preferred."""

    def test_keys_must_be_lower_case(self):
        self.add_spec(1, specimen("S1"))
        with self.assertRaisesRegex(CommandError, "malformed"):
            self.apply()

    def test_a_case_variant_row_is_the_items_row(self):
        with as_owner():
            theirs = SpecimenDefinition.objects.create(
                facility=self.f1,
                slug=f"f-{str(self.f1.external_id).upper()}-S1",
                title="Test specimen s1",
                status="active",
                description="",
                type_collected=specimen("s1")["type_collected"],
            )
        self.add_spec(1, specimen("s1"))
        self.apply()
        self.assertEqual(
            SpecimenDefinition._base_manager.filter(facility=self.f1).count(),
            1,
        )
        record = self.record(SPEC, "s1", self.f1)
        self.assertEqual((record.state, record.target), ("current", theirs.slug))

    def test_a_deleted_and_a_live_row_the_live_one_counts(self):
        with as_owner():
            for deleted, title in ((False, "Live one"), (True, "Deleted one")):
                SpecimenDefinition.objects.create(
                    facility=self.f1,
                    slug=catalogue.slug_of(self.f1, "s1"),
                    title=title,
                    status="active",
                    description="",
                    deleted=deleted,
                )
        found = engine.kinds.get("t_specimen").at(self.f1).rows(["s1"])
        self.assertEqual((found["s1"].title, found["s1"].ihr_live), ("Live one", 1))
        self.add_spec(1, specimen("s1"))
        self.add_act(1, activity("a1", "s1"))
        self.apply()
        # the live row is the specimen's row (unknown origin) and the link's target
        self.assertEqual(self.record(SPEC, "s1", self.f1).reason, "unknown-origin")
        self.assertEqual(act_row(self.f1, "a1").specimen_requirements, [found["s1"].pk])

    def test_two_live_rows_are_a_conflict_for_the_row_and_the_link(self):
        with as_owner():
            for title in ("First", "Second"):
                SpecimenDefinition.objects.create(
                    facility=self.f1,
                    slug=catalogue.slug_of(self.f1, "s1"),
                    title=title,
                    status="active",
                    description="",
                )
        self.add_spec(1, specimen("s1"))
        self.add_act(1, activity("a1", "s1"))
        self.apply()
        self.assertEqual(self.record(SPEC, "s1", self.f1).reason, "conflict")
        self.assertEqual(self.record(ACT, "a1", self.f1).reason, "conflict")
        self.assertEqual(self.record(ACT, "a1", self.f2).state, "current")


class DeletedFacilityTests(FacilityCase):
    def test_a_deleted_facility_is_skipped_hidden_and_caught_up_when_restored(self):
        self.add_spec(1, specimen("s1"))
        self.apply()
        row = spec_row(self.f2, "s1")
        row.title = "Edited in Two"
        row.save()
        with as_owner():
            self.f2.deleted = True
            self.f2.save()
        self.add_spec(2, specimen("s1", title="v2"), specimen("s2"))
        self.apply()
        self.assertEqual(spec_row(self.f1, "s2").title, "Test specimen s2")
        self.assertFalse(
            SpecimenDefinition._base_manager.filter(
                facility=self.f2, title__startswith="Test specimen s2"
            ).exists()
        )
        record = self.record(SPEC, "s1", self.f2)  # kept as it was, hidden
        self.assertEqual(record.state, "current")
        with as_owner():
            record.state, record.reason = "pending", "edited"
            record.save()
        admin = hospital_admin(f"fa-{self.key}")
        client = APIClient(HTTP_HOST="testserver")
        client.force_authenticate(admin)
        listed = client.get(URL).json()
        self.assertNotIn(record.pk, [r["id"] for r in listed["results"]])
        self.assertEqual(client.get(f"{URL}{record.pk}/").status_code, 404)
        with self.assertRaisesRegex(CommandError, "nothing waiting"):
            self.decide("--keep", SPEC, "s1", self.f2)
        self.assertIn(" (deleted)", self.pending_cmd())
        # restored: the backstop brings it up to date (no PackFacility for v2 there)
        with as_owner():
            record.state, record.reason = "current", ""
            record.save()
            self.f2.deleted = False
            self.f2.save()
        self.record_releases()
        outcome, places = tasks.fill(TEST_SCHEMA, actor=tasks.BEAT_ACTOR)
        self.assertEqual((outcome, places), ("done", [self.f2]))
        self.assertEqual(spec_row(self.f2, "s2").title, "Test specimen s2")
        self.assertEqual(self.record(SPEC, "s1", self.f2).reason, "edited")


class DecideTests(FacilityCase):
    def _edited(self):
        self.add_spec(1, specimen("s1"))
        self.apply()
        for place in (self.f1, self.f2):
            row = spec_row(place, "s1")
            row.title = "Their own title"
            row.save()
        self.add_spec(2, specimen("s1", title="v2"))
        self.apply()

    def test_a_facility_deleted_after_it_was_read_is_never_decided_in(self):
        """Review E3: decide reads the facility again, locked, inside its transaction."""
        self._edited()
        stale = Facility._base_manager.get(pk=self.f1.pk)
        with as_owner():
            Facility._base_manager.filter(pk=self.f1.pk).update(deleted=True)
            with self.assertRaises(engine.NothingWaitingError):
                engine.decide(
                    self.hospital, SPEC, "s1", accept=False, operator="packs:tester",
                    reason=REASON, facility=stale,
                )  # fmt: skip
        self.assertEqual(self.record(SPEC, "s1", self.f1).state, "pending")

    def test_the_command_names_and_filters_by_the_facility(self):
        """Review Q9: --facility filters the list, is refused with --recheck, and the
        decision names it."""
        self._edited()
        one = str(self.f1.external_id)
        listed = self.pending_cmd("--facility", one)
        self.assertIn(f"facility={one}", listed)
        self.assertNotIn(str(self.f2.external_id), listed)
        with self.assertRaisesRegex(CommandError, "leave out --facility"):
            self.pending_cmd("--recheck", "--facility", one, "--operator", "tester")
        out = self.decide("--keep", SPEC, "s1", self.f1)
        self.assertIn(f"in facility {one} is now kept", out)


# SQL statements of the list GET through the whole middleware path (as T24a counted my-access),
# the same with and without facility_name: two facilities in the list, one facility filtered.
# Counted with django-tenants' "SET search_path" before each statement. P1-T25 added two: the
# gate's SELECT of the hospital's root (``roots.valid_root``, never cached across requests) and
# the SET before it; the gate's EXISTS on the membership was already counted.
QUERIES_TWO, QUERIES_ONE = 26, 24


class ApiTests(FacilityCase):
    def test_the_list_shows_and_filters_by_facility(self):
        self.add_spec(1, specimen("s1"))
        self.apply()
        for place in (self.f1, self.f2):
            row = spec_row(place, "s1")
            row.title = f"Edited {place.pk}"
            row.save()
        self.add_spec(2, specimen("s1", title="v2"))
        self.apply()
        client = APIClient(HTTP_HOST="testserver")
        client.force_authenticate(hospital_admin(f"fb-{self.key}"))
        found = client.get(URL, {"pack": SPEC}).json()["results"]
        self.assertEqual(
            sorted(r["facility"] for r in found),
            sorted(str(f.external_id) for f in (self.f1, self.f2)),
        )
        one = client.get(URL, {"facility": str(self.f1.external_id)}).json()
        self.assertEqual(
            [r["facility"] for r in one["results"]], [str(self.f1.external_id)]
        )
        self.assertEqual(client.get(URL, {"facility": "not-a-uuid"}).status_code, 400)
        record = self.record(SPEC, "s1", self.f1)
        shown = client.get(f"{URL}{record.pk}/").json()
        self.assertEqual(shown["label"], f"Edited {self.f1.pk}")
        answer = client.post(
            f"{URL}{record.pk}/",
            {
                "action": "accept",
                "version": shown["pending_version"],
                "sha256": shown["pending_sha256"],
                "row_sha256": shown["row_sha256"],
                "reason": REASON,
            },
            format="json",
        )
        self.assertEqual(answer.status_code, 200, answer.content[:300])
        self.assertEqual(spec_row(self.f1, "s1").title, "v2")
        self.assertEqual(spec_row(self.f2, "s1").title, f"Edited {self.f2.pk}")

    def test_the_list_and_detail_name_the_facility_with_no_extra_query(self):
        """P1-T24c G6: ``facility`` stays the external id (the filter value) and
        ``facility_name`` is the facility's name, from the join the list already makes."""
        self.add_spec(1, specimen("s1"))
        self.apply()
        for place in (self.f1, self.f2):
            row = spec_row(place, "s1")
            row.title = f"Edited {place.pk}"
            row.save()
        self.add_spec(2, specimen("s1", title="v2"))
        self.apply()
        client = APIClient(HTTP_HOST="testserver")
        client.force_authenticate(hospital_admin(f"fn-{self.key}"))
        with CaptureQueriesContext(connection) as both:
            found = client.get(URL, {"pack": SPEC}).json()["results"]
        self.assertEqual(
            {r["facility"]: r["facility_name"] for r in found},
            {str(f.external_id): f.name for f in (self.f1, self.f2)},
        )
        with CaptureQueriesContext(connection) as one:
            only = client.get(
                URL, {"pack": SPEC, "facility": str(self.f1.external_id)}
            ).json()["results"]
        self.assertEqual([r["facility_name"] for r in only], [self.f1.name])
        # the name costs no query (the join is _shown's): pinned, so a lookup per row shows
        self.assertEqual((len(both), len(one)), (QUERIES_TWO, QUERIES_ONE))
        record = self.record(SPEC, "s1", self.f2)
        shown = client.get(f"{URL}{record.pk}/").json()
        self.assertEqual(shown["facility_name"], self.f2.name)
        self.assertEqual(shown["facility"], str(self.f2.external_id))


class NewFacilityTests(FacilityCase):
    """Item 14 (Q-K67 A): the on-commit task and the 15-minute backstop."""

    def setUp(self):
        super().setUp()
        self.add_spec(1, specimen("s1"))
        self.add_act(1, activity("a1", "s1"))
        self.packs.add(1, vs("ihr-vsx", "1-8"), pack=VS, kind="valueset")
        self.apply()
        # a hospital-wide item waits: neither the task nor the backstop may touch it
        with as_owner():
            ValueSet._base_manager.filter(slug="ihr-vsx").update(name="Theirs")
        self.packs.add(2, vs("ihr-vsx", "2-8"), pack=VS, kind="valueset")
        self.record_releases()
        self.addCleanup(tasks._redis().delete, tasks.queue_key(TEST_SCHEMA))

    def assert_untouched_hospital_wide(self):
        self.assertFalse(PackItem.objects.filter(pack=VS, pending_version=2).exists())
        self.assertFalse(PackApply.objects.filter(pack=VS, version=2).exists())

    def test_a_new_facility_is_filled_by_the_task_on_commit(self):
        with self.captureOnCommitCallbacks(execute=True) as sent:
            f3 = self.facility("Three")
        self.assertTrue(sent)
        self.assertEqual(
            act_row(f3, "a1").specimen_requirements, [spec_row(f3, "s1").pk]
        )
        self.assertEqual(self.record(ACT, "a1", f3).state, "current")
        applied = PackApply.objects.filter(pack=ACT).latest("id")
        self.assertEqual(applied.actor, tasks.TASK_ACTOR)
        self.assertEqual(
            {c["facility"] for c in applied.changes}, {str(f3.external_id)}
        )
        self.assert_untouched_hospital_wide()
        # the key was cleared when the task started
        self.assertIsNone(tasks._redis().get(tasks.queue_key(TEST_SCHEMA)))

    def test_the_task_is_queued_once_per_hospital_by_one_schema_prefixed_key(self):
        with mock.patch.object(tasks.fill_new_facilities, "apply_async") as send:
            self.assertTrue(tasks.queue(TEST_SCHEMA, self.f1.pk))
            self.assertFalse(tasks.queue(TEST_SCHEMA, self.f2.pk))
        self.assertEqual(send.call_count, 1)
        self.assertEqual(
            tasks.queue_key(TEST_SCHEMA), f"{TEST_SCHEMA}:ihr:packs:facility-task"
        )
        self.assertIsNotNone(
            tasks._redis().get(f"{TEST_SCHEMA}:ihr:packs:facility-task")
        )

    def test_a_busy_hospital_is_tried_three_times_then_left_to_the_backstop(self):
        with as_owner():
            f3 = baker.make(Facility, name=f"Three {self.key}", created_by=self.user)
        lines = []
        with (
            mock.patch.object(tasks, "_emit", side_effect=lines.append),
            mock.patch.object(tasks, "fill", wraps=tasks.fill) as tried,
            runner.exclusive(SLUG),
        ):
            tasks.fill_new_facilities.apply(args=(f3.pk, 0))
        self.assertEqual(tried.call_count, 4)
        self.assertEqual(
            lines,
            [f"ihr-packs: hospital={SLUG} outcome=deferred facility={f3.external_id}"],
        )
        self.assertFalse(PackItem.objects.filter(facility=f3).exists())
        # the backstop fills it, and only it, never a hospital-wide pack
        with mock.patch.object(tasks, "_emit", side_effect=lines.append):
            tasks.fill_facilities.apply()
        self.assertEqual(self.record(ACT, "a1", f3).state, "current")
        self.assertEqual(PackFacility.objects.filter(facility=f3).count(), 2)
        self.assert_untouched_hospital_wide()
        self.assertEqual(len(lines), 1)

    def test_the_backstop_never_applies_a_hospital_wide_pack(self):
        """Mutation facility-beat-scope."""
        with as_owner():
            f3 = baker.make(Facility, name=f"Three {self.key}", created_by=self.user)
        self.assertEqual(
            tasks.fill(TEST_SCHEMA, actor=tasks.BEAT_ACTOR), ("done", [f3])
        )
        self.assertTrue(PackItem.objects.filter(facility=f3).exists())
        self.assert_untouched_hospital_wide()
        self.assertEqual(ValueSet._base_manager.get(slug="ihr-vsx").name, "Theirs")

    def test_the_backstop_never_touches_a_hospital_that_is_not_served(self):
        """Mutation facility-beat-hold: suspended, or held for a restore."""
        with as_owner():
            f3 = baker.make(Facility, name=f"Three {self.key}", created_by=self.user)
            Hospital.objects.filter(pk=self.hospital.pk).update(
                status=Hospital.Status.SUSPENDED
            )
        self.assertEqual(tasks.fill(TEST_SCHEMA, actor=tasks.BEAT_ACTOR), ("held", []))
        self.assertFalse(PackItem.objects.filter(facility=f3).exists())

    def test_the_backstop_is_one_query_when_nothing_lags(self):
        found = engine.verified_releases(cached=True)
        mine = {pack: found[pack][-1] for pack in (SPEC, ACT, VS)}
        with mock.patch.object(engine, "apply_packs") as applied:
            self.assertEqual(
                tasks.fill(TEST_SCHEMA, actor=tasks.BEAT_ACTOR), ("none", [])
            )
            # after whether the hospital is served: one query finds nothing to do
            with CaptureQueriesContext(connection) as seen:
                self.assertEqual(
                    engine.lagging_facilities(mine, missing_links=True), []
                )
        applied.assert_not_called()
        asked = [q["sql"] for q in seen if not q["sql"].startswith("SET search_path")]
        self.assertEqual(len(asked), 1, asked)

    def test_after_an_image_roll_back_nothing_lags_and_no_lock_is_taken(self):
        """Review T3 / Q2: v2 applied, the build ships only v1 (skipped as older): neither
        the facilities that took v2 nor a new one lag, and the hospital is never locked."""
        self.add_spec(2, specimen("s1", title="v2"))
        self.add_act(2, activity("a1", "s1", title="v2"))
        self.apply()
        self.packs._write_manifest(  # the older image: v1 of both per-facility packs
            [e for e in self.packs.entries if e["version"] == 1 or e["pack"] == VS]
        )
        found = engine.verified_releases(cached=True)
        mine = {pack: found[pack][-1] for pack in (SPEC, ACT)}
        self.assertEqual({r.version for r in mine.values()}, {1})
        self.assertEqual(engine.lagging_facilities(mine, missing_links=True), [])
        with as_owner():
            baker.make(Facility, name=f"Three {self.key}", created_by=self.user)
        with mock.patch.object(runner, "exclusive") as locked:
            self.assertEqual(
                tasks.fill(TEST_SCHEMA, actor=tasks.BEAT_ACTOR, missing_links=True),
                ("none", []),
            )
        locked.assert_not_called()

    def test_a_failure_names_the_facilities_or_unknown(self):
        """Review Q7: the backstop's line is written whatever failed, so the alert's hold is
        never broken; refused pack files name ``unknown``."""
        with as_owner():
            f3 = baker.make(Facility, name=f"Three {self.key}", created_by=self.user)
        lines = []
        with (
            mock.patch.object(tasks, "_emit", side_effect=lines.append),
            mock.patch.object(
                engine, "verified_releases", side_effect=manifest.PackError("refused")
            ),
        ):
            tasks.fill_facilities.apply()
        self.assertEqual(
            lines, [f"ihr-packs: hospital={SLUG} outcome=deferred facility=unknown"]
        )
        lines.clear()
        with (
            mock.patch.object(tasks, "_emit", side_effect=lines.append),
            mock.patch.object(engine, "prepare", side_effect=RuntimeError("injected")),
        ):
            tasks.fill_facilities.apply()
        self.assertEqual(
            lines,
            [f"ihr-packs: hospital={SLUG} outcome=deferred facility={f3.external_id}"],
        )

    def test_an_unrecorded_release_is_never_applied_by_the_task(self):
        """record=False: the workers only read the fleet's record; a new release is left to
        the next start-up, which records it."""
        self.add_spec(2, specimen("s1"), specimen("s2"))
        with as_owner():
            f3 = baker.make(Facility, name=f"Three {self.key}", created_by=self.user)
        outcome, places = tasks.fill(TEST_SCHEMA, actor=tasks.TASK_ACTOR)
        self.assertEqual((outcome, places), ("unreleased", [self.f1, self.f2, f3]))
        self.assertFalse(PackItem.objects.filter(facility=f3).exists())
        self.assertFalse(
            SpecimenDefinition._base_manager.filter(slug__endswith="-s2").exists()
        )

    def test_the_operators_of_the_task_and_backstop_are_reserved(self):
        for name in ("facility-task", "facility-beat"):
            with self.subTest(name), self.assertRaisesRegex(CommandError, "reserved"):
                call_command(
                    "ihr_pack_apply", "--slug", SLUG, "--operator", name,
                    stdout=StringIO(), stderr=StringIO(),
                )  # fmt: skip


class ScheduleTests(TestCase):
    databases = {"default"}

    def test_the_backstop_runs_every_15_minutes_per_hospital(self):
        from config.celery_app import app
        from ihr_platform.beat import PLATFORM_ONCE

        tasks.setup_periodic_tasks(app)
        entry = app.conf.beat_schedule["ihr_refdata_fill_facilities"]
        self.assertEqual(entry["schedule"], 15 * 60)
        self.assertEqual(entry["task"], "ihr_refdata.fill_facilities")
        self.assertFalse(entry.get("options", {}).get("headers", {}).get(PLATFORM_ONCE))
        self.assertEqual(connection.schema_name, TEST_SCHEMA)
