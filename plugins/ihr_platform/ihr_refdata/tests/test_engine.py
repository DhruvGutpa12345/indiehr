"""Reference packs in one hospital (P1-T15a; docs/reviews/p1-t15/design.md, the rules as built).

Every test runs in the shared test hospital inside a transaction that is rolled back, so the
hospital keeps no value sets for CARE's own tests (C12). Rules with a second pack version use
a packs folder of their own (ihr_refdata.testing.Packs).
"""

import json
from contextlib import ExitStack
from io import StringIO
from unittest import mock

from care.emr.models.valueset import ValueSet
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django_tenants.utils import get_public_schema_name, schema_context
from ihr_audit import context as audit
from ihr_platform.models import Hospital
from ihr_platform.provisioning import runner
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_refdata_registry.models import PackRelease

from ihr_refdata import engine, manifest, valuesets
from ihr_refdata.management.commands import ihr_pack_apply
from ihr_refdata.models import PackApply, PackItem
from ihr_refdata.testing import TEST_PACK, Packs, audit_mark, audit_since, vs

SLUG = "testhosp"
NEVER_SYNCED = "system-medication-not-given"  # CARE's sync_valueset never wrote it
REFDATA_TABLES = {"ihr_refdata_packitem", "ihr_refdata_packapply"}


def _data(entry):
    data = entry["data"]
    return json.loads(data) if isinstance(data, str) else data


def rows():
    return {row.slug: row for row in ValueSet._base_manager.all()}


def items(pack=TEST_PACK):
    return {i.item: i for i in PackItem.objects.filter(pack=pack)}


class PackCase(TestCase):
    databases = {"default"}

    def setUp(self):
        # Only the test hospital is live here (rolled back with the test)
        Hospital.objects.exclude(
            schema_name__in=[get_public_schema_name(), TEST_SCHEMA]
        ).update(status=Hospital.Status.SUSPENDED)
        ValueSet._base_manager.all().delete()
        self.hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)

    def apply(self, *args, operator="tester"):
        out, err = StringIO(), StringIO()
        call_command(
            "ihr_pack_apply",
            "--slug",
            SLUG,
            "--operator",
            operator,
            *args,
            stdout=out,
            stderr=err,
        )
        return out.getvalue(), err.getvalue()

    def startup(self):
        out, err = StringIO(), StringIO()
        call_command("ihr_pack_apply", "--all", "--startup", stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def pending_cmd(self, *args):
        out = StringIO()
        call_command("ihr_pack_pending", "--slug", SLUG, *args, stdout=out)
        return out.getvalue()

    def decide(self, flag, item, pack=TEST_PACK):
        return self.pending_cmd(
            flag, f"{pack}:{item}", "--operator", "tester", "--reason", "admin said so"
        )

    def edit(self, slug, **fields):
        row = ValueSet._base_manager.get(slug=slug)
        for name, value in fields.items():
            setattr(row, name, value)
        row.save()
        return row


class RealPackTests(PackCase):
    """The committed care-valuesets pack, built from CARE's own registry."""

    def test_the_committed_pack_matches_cares_registry(self):
        """--check drift: an upstream sync that changes a system value set fails here until a
        new pack version is built and committed (scripts/pack-build.sh)."""
        out = StringIO()
        call_command("ihr_pack_build", "care-valuesets", "--check", stdout=out)
        self.assertIn("matches CARE's registry", out.getvalue())

    def test_a_fresh_hospital_gets_every_set_and_a_second_apply_writes_nothing(self):
        release = manifest.latest()["care-valuesets"]
        self.apply()
        found = rows()
        self.assertEqual(sorted(found), [i["slug"] for i in release.items])
        self.assertTrue(
            all(r.is_system_defined and not r.deleted for r in found.values())
        )
        records = items("care-valuesets")
        self.assertEqual(len(records), release.item_count)
        self.assertEqual({r.state for r in records.values()}, {"current"})
        self.assertEqual(
            list(PackApply.objects.values_list("outcome", "version")),
            [("applied", release.version)],
        )
        self.assertTrue(
            PackRelease.objects.filter(
                pack="care-valuesets", version=release.version, sha256=release.sha256
            ).exists()
        )
        dates = {s: r.modified_date for s, r in found.items()}

        since = audit_mark()
        out, _ = self.apply()
        self.assertIn("unchanged", out)
        self.assertEqual(audit_since(since), [])
        self.assertEqual(PackApply.objects.count(), 1)
        self.assertEqual({s: r.modified_date for s, r in rows().items()}, dates)

        # An edit made now is left alone: the hospital's own until the pack changes that set
        # (C4: the same whether or not a full comparison runs, see RuleTests)
        self.edit("system-route", name="Our own routes")
        since = audit_mark()
        self.apply()
        self.assertEqual(audit_since(since), [])
        self.assertEqual(items("care-valuesets")["system-route"].state, "current")

    def test_rows_written_by_cares_sync_valueset_are_adopted_without_a_write(self):
        """Every hospital made before P1-T15a holds the 30 sets CARE's sync_valueset wrote
        (it never imported the module registering system-medication-not-given): the pack
        adopts those unchanged and creates only the one CARE never wrote (C3, C6)."""
        # every module imported first, so the row count never depends on test order (the
        # registry is global to the process); then the state of a real hospital
        valuesets.care_registry()
        call_command("sync_valueset")
        ValueSet._base_manager.filter(slug=NEVER_SYNCED).delete()
        dates = {s: r.modified_date for s, r in rows().items()}
        total = manifest.latest()["care-valuesets"].item_count
        self.assertEqual(len(dates), total - 1)
        since = audit_mark()
        out, _ = self.apply()
        self.assertIn(f"{total - 1} adopted", out)
        self.assertIn("1 created", out)
        self.assertEqual(
            {r.state for r in items("care-valuesets").values()}, {"current"}
        )
        written = audit_since(since)
        sets = [e for e in written if e["tbl"] == "emr_valueset"]
        self.assertEqual(
            [(e["op"], _data(e)["slug"]) for e in sets], [("I", NEVER_SYNCED)]
        )
        # otherwise only the pack's own bookkeeping is written (and audited)
        self.assertEqual({e["tbl"] for e in written}, {"emr_valueset", *REFDATA_TABLES})
        after = {s: r.modified_date for s, r in rows().items() if s != NEVER_SYNCED}
        self.assertEqual(after, dates)


class RuleTests(PackCase):
    def setUp(self):
        super().setUp()
        self.packs = Packs(self)

    def test_an_unedited_row_is_updated_and_an_unchanged_one_left_alone(self):
        self.packs.add(1, vs("ihr-a", "1"), vs("ihr-b", "2"))
        self.apply()
        b_date = rows()["ihr-b"].modified_date
        self.packs.add(2, vs("ihr-a", "1", "3", name="Renamed"), vs("ihr-b", "2"))
        since = audit_mark()
        out, _ = self.apply()
        self.assertIn("1 updated", out)
        a = rows()["ihr-a"]
        self.assertEqual(a.name, "Renamed")
        self.assertEqual(
            [c["code"] for c in a.compose["include"][0]["concept"]], ["1", "3"]
        )
        record = items()["ihr-a"]
        self.assertEqual((record.state, record.applied_version), ("current", 2))
        self.assertEqual(items()["ihr-b"].applied_version, 1)  # nothing to write for it
        self.assertEqual(rows()["ihr-b"].modified_date, b_date)
        written = {(e["tbl"], e["op"]) for e in audit_since(since)}
        self.assertEqual(
            written,
            {
                ("emr_valueset", "U"),
                ("ihr_refdata_packitem", "U"),
                ("ihr_refdata_packapply", "I"),
            },
        )

    def test_an_edited_row_waits_for_the_admin(self):
        self.packs.add(1, vs("ihr-a", "1"))
        self.apply()
        self.edit("ihr-a", name="Hospital's own name")
        self.packs.add(2, vs("ihr-a", "1", "2"))
        self.apply()
        self.assertEqual(rows()["ihr-a"].name, "Hospital's own name")
        record = items()["ihr-a"]
        self.assertEqual(
            (record.state, record.reason, record.pending_version),
            ("pending", "edited", 2),
        )
        self.assertIn("test-pack:ihr-a  pending (edited)  v2", self.pending_cmd())
        # With an item waiting, every apply compares the whole pack again: still silent
        since = audit_mark()
        self.apply()
        self.assertEqual(audit_since(since), [])

    def test_a_row_this_pack_never_wrote_waits_as_unknown_origin(self):
        ValueSet.objects.create(
            slug="ihr-a", name="By hand", status="active", compose={"include": []}
        )
        self.packs.add(1, vs("ihr-a", "1"))
        self.apply()
        self.assertEqual(rows()["ihr-a"].name, "By hand")
        record = items()["ihr-a"]
        self.assertEqual((record.state, record.reason), ("pending", "unknown-origin"))

    def test_the_same_meaning_spelt_differently_is_not_an_edit(self):
        """The hash compares meaning (C3): nulls and empty lists spelt or left out alike. A
        row without "exclude", which CARE's lookup cannot read, is repaired (C2)."""
        item = vs("ihr-a", "1")
        b = vs("ihr-b", "2")
        spelt = {**b["compose"], "id": None, "property": []}  # usable: adopted as it is
        for slug, compose in (
            ("ihr-a", {"include": item["compose"]["include"], "id": None}),
            ("ihr-b", spelt),
        ):
            ValueSet.objects.create(
                slug=slug,
                name=slug.replace("-", " ").title(),
                status="active",
                compose=compose,
                is_system_defined=True,
            )
        self.packs.add(1, item, b)
        since = audit_mark()
        out, _ = self.apply()
        self.assertIn("1 adopted", out)
        self.assertIn("1 repaired", out)
        self.assertEqual({r.state for r in items().values()}, {"current"})
        self.assertEqual(rows()["ihr-a"].compose, item["compose"])  # exclude: [] again
        self.assertEqual(rows()["ihr-b"].compose, spelt)  # adopted, not rewritten
        # one audited write: ihr-a's compose
        sets = audit_since(since, tbl="emr_valueset")
        self.assertEqual([e["op"] for e in sets], ["U"])
        # CARE's own lookup can read it again
        self.assertEqual(
            set(rows()["ihr-a"].create_composition()), {"http://loinc.org"}
        )

        # Broken again later, with nothing new in the pack: the next start still repairs it
        broken = {"include": item["compose"]["include"], "exclude": None}
        ValueSet.objects.filter(slug="ihr-a").update(compose=broken)
        out, _ = self.apply()
        self.assertIn("1 repaired", out)
        self.assertEqual(rows()["ihr-a"].compose, item["compose"])

    def test_a_deleted_set_is_restored_even_when_the_pack_is_unchanged(self):
        self.packs.add(1, vs("ihr-a", "1"), vs("ihr-b", "2"))
        self.apply()
        self.edit("ihr-a", deleted=True, name="Edited, then deleted")
        ValueSet._base_manager.filter(slug="ihr-b").delete()
        out, _ = self.apply()  # same sha256: the existence check still finds them
        self.assertIn("1 restored", out)
        self.assertIn("1 created", out)
        a = rows()["ihr-a"]
        self.assertEqual((a.deleted, a.name), (False, "Ihr A"))
        self.assertEqual({r.state for r in items().values()}, {"current"})
        self.assertEqual(ValueSet.objects.count(), 2)

    def test_a_set_that_left_the_pack_is_retired_then_comes_back_when_re_added(self):
        self.packs.add(1, vs("ihr-a", "1"), vs("ihr-b", "2"))
        self.apply()
        self.packs.add(2, vs("ihr-a", "1"))
        out, _ = self.apply()
        self.assertIn("1 retired", out)
        b = rows()["ihr-b"]
        self.assertEqual((b.status, b.deleted), ("retired", False))  # never deleted
        self.assertEqual(items()["ihr-b"].state, "retired")
        self.packs.add(3, vs("ihr-a", "1"), vs("ihr-b", "2"))
        self.apply()
        self.assertEqual(rows()["ihr-b"].status, "active")
        self.assertEqual(items()["ihr-b"].state, "current")

    def test_an_edited_set_that_left_the_pack_waits(self):
        self.packs.add(1, vs("ihr-a", "1"), vs("ihr-b", "2"))
        self.apply()
        self.edit("ihr-b", name="Ours")
        self.packs.add(2, vs("ihr-a", "1"))
        self.apply()
        self.assertEqual(rows()["ihr-b"].status, "active")
        record = items()["ihr-b"]
        self.assertEqual((record.state, record.pending_sha256), ("pending", ""))
        self.decide("--accept", "ihr-b")  # accepting a removal retires the row
        self.assertEqual(rows()["ihr-b"].status, "retired")
        self.assertEqual(items()["ihr-b"].state, "retired")

    def test_a_kept_row_waits_again_only_when_the_pack_changes_it_again(self):
        self.packs.add(1, vs("ihr-a", "1"), vs("ihr-b", "2"))
        self.apply()
        self.edit("ihr-a", name="Ours")
        self.packs.add(2, vs("ihr-a", "1", "2"), vs("ihr-b", "2"))
        self.apply()
        self.decide("--keep", "ihr-a")
        record = items()["ihr-a"]
        self.assertEqual(record.state, "kept")
        self.assertTrue(record.kept_row_sha256)
        self.assertEqual(
            PackApply.objects.filter(outcome="kept").values_list("counts", flat=True)[
                0
            ],
            {"item": "ihr-a", "reason": "admin said so"},
        )

        since = audit_mark()
        self.apply()  # unchanged: nothing written, still kept
        self.assertEqual(audit_since(since), [])
        self.packs.add(3, vs("ihr-a", "1", "2"), vs("ihr-b", "2", "3"))
        self.apply()  # the pack changed another set: still kept, following v3 (C1)
        record = items()["ihr-a"]
        self.assertEqual((record.state, record.pending_version), ("kept", 3))
        self.packs.add(4, vs("ihr-a", "1", "2", "4"), vs("ihr-b", "2", "3"))
        self.apply()
        record = items()["ihr-a"]
        self.assertEqual((record.state, record.pending_version), ("pending", 4))
        self.assertEqual(rows()["ihr-a"].name, "Ours")

    def test_a_kept_item_can_still_be_accepted_after_a_release_that_left_it_alone(self):
        """C1: keep at v2, v3 changes only another set; accepting then writes v3's content."""
        self.packs.add(1, vs("ihr-a", "1"), vs("ihr-b", "2"))
        self.apply()
        self.edit("ihr-a", name="Ours")
        self.packs.add(2, vs("ihr-a", "1", "2", name="V2"), vs("ihr-b", "2"))
        self.apply()
        self.decide("--keep", "ihr-a")
        self.packs.add(3, vs("ihr-a", "1", "2", name="V2"), vs("ihr-b", "2", "3"))
        self.apply()
        record = items()["ihr-a"]
        self.assertEqual((record.state, record.pending_version), ("kept", 3))
        self.assertEqual(rows()["ihr-a"].name, "Ours")
        since = audit_mark()
        self.apply()  # nothing new: silent
        self.assertEqual(audit_since(since), [])
        self.decide("--accept", "ihr-a")
        a = rows()["ihr-a"]
        self.assertEqual(
            (a.name, [c["code"] for c in a.compose["include"][0]["concept"]]),
            ("V2", ["1", "2"]),
        )
        record = items()["ihr-a"]
        self.assertEqual(
            (record.state, record.applied_version, record.pending_version),
            ("current", 3, None),
        )

    def test_a_kept_removal_can_still_be_accepted_after_a_later_release(self):
        """C1 for a set the pack dropped: kept at v2, v3 changes another set, accept retires."""
        self.packs.add(1, vs("ihr-a", "1"), vs("ihr-b", "2"))
        self.apply()
        self.edit("ihr-b", name="Ours")
        self.packs.add(2, vs("ihr-a", "1"))
        self.apply()
        self.decide("--keep", "ihr-b")
        self.packs.add(3, vs("ihr-a", "1", "3"))
        self.apply()
        record = items()["ihr-b"]
        self.assertEqual((record.state, record.pending_version), ("kept", 3))
        self.assertEqual(rows()["ihr-b"].status, "active")
        self.decide("--accept", "ihr-b")
        self.assertEqual(rows()["ihr-b"].status, "retired")
        self.assertEqual(items()["ihr-b"].state, "retired")

    def test_a_local_edit_the_pack_has_nothing_new_for_is_never_pending(self):
        """C4: an edited set stays as it is while the pack has nothing new for it, whether or
        not a full comparison runs (another set waiting, or a release changing another set)."""
        self.packs.add(1, vs("ihr-a", "1"), vs("ihr-b", "2"), vs("ihr-c", "3"))
        self.apply()
        self.edit("ihr-a", name="Ours")
        self.edit("ihr-b", name="Ours too")
        self.packs.add(2, vs("ihr-a", "1"), vs("ihr-b", "2", "4"), vs("ihr-c", "3"))
        out, _ = self.apply()  # ihr-b waits; ihr-a has nothing new
        self.assertIn("1 local-edit", out)
        self.assertEqual(items()["ihr-b"].state, "pending")
        self.assertEqual(items()["ihr-a"].state, "current")
        since = audit_mark()
        self.apply()  # a full comparison again (ihr-b waits): still nothing for ihr-a
        self.assertEqual(audit_since(since), [])
        self.packs.add(
            3, vs("ihr-a", "1"), vs("ihr-b", "2", "4"), vs("ihr-c", "3", "5")
        )
        self.apply()
        record = items()["ihr-a"]
        self.assertEqual((record.state, record.pending_version), ("current", None))
        self.assertEqual(rows()["ihr-a"].name, "Ours")
        self.assertNotIn("test-pack:ihr-a", self.pending_cmd())
        # a change that waited and that the pack withdrew stops waiting
        self.packs.add(4, vs("ihr-a", "1"), vs("ihr-b", "2"), vs("ihr-c", "3", "5"))
        self.apply()
        record = items()["ihr-b"]
        self.assertEqual((record.state, record.pending_version), ("current", None))
        self.assertEqual(rows()["ihr-b"].name, "Ours too")

    def test_a_local_edit_that_broke_the_compose_is_made_usable_not_reverted(self):
        """C2 after C4: CARE's own API can store compose without "exclude" (then every lookup
        on that slug fails). With nothing new from the pack, the hospital's edit stays, but the
        missing list is added empty; their content is kept."""
        self.packs.add(1, vs("ihr-a", "1"), vs("ihr-b", "2"))
        self.apply()
        row = rows()["ihr-a"]
        own = {
            "include": [
                {"system": "http://example.org/ours", "concept": [{"code": "x"}]}
            ]
        }
        self.edit("ihr-a", name="Ours", compose=own)
        out, _ = self.apply()
        self.assertIn("1 repaired", out)
        row = rows()["ihr-a"]
        self.assertEqual(row.name, "Ours")
        self.assertEqual(row.compose, {**own, "exclude": []})
        self.assertEqual(items()["ihr-a"].state, "current")
        row.create_composition()  # CARE's lookup can read it again
        since = audit_mark()
        self.apply()
        self.assertEqual(audit_since(since), [])  # once repaired, nothing more

    def test_the_reason_is_one_line_of_at_most_300_characters(self):
        """S7: refused when longer (never cut silently); put on one line; recorded once, in
        the command's context and the PackApply row, the same normalised text."""
        self.packs.add(1, vs("ihr-a", "1"))
        self.apply()
        self.edit("ihr-a", name="Ours")
        self.packs.add(2, vs("ihr-a", "1", "2"))
        self.apply()
        for reason in ("x" * 301, " \n "):
            with self.assertRaises(CommandError):
                self.pending_cmd(
                    "--keep",
                    f"{TEST_PACK}:ihr-a",
                    "--operator",
                    "t",
                    "--reason",
                    reason,
                )
        since = audit_mark()
        self.pending_cmd(
            "--keep",
            f"{TEST_PACK}:ihr-a",
            "--operator",
            "t",
            "--reason",
            "hospital admin OK,\n  ticket 123  ",
        )
        normal = "hospital admin OK, ticket 123"
        kept = PackApply.objects.get(outcome="kept")
        self.assertEqual(
            (kept.counts, kept.actor), ({"item": "ihr-a", "reason": normal}, "packs:t")
        )
        entries = audit_since(since)
        self.assertTrue(entries)
        self.assertEqual({e["context"]["reason"] for e in entries}, {normal})

    def test_accept_writes_the_verified_packs_content_and_refuses_a_stale_one(self):
        self.packs.add(1, vs("ihr-a", "1"))
        self.apply()
        self.edit("ihr-a", name="Ours")
        self.packs.add(2, vs("ihr-a", "1", "2", name="V2"))
        self.apply()
        with self.assertRaisesMessage(CommandError, "--operator and --reason"):
            self.pending_cmd("--accept", "test-pack:ihr-a")
        # A newer release, not yet applied: the waiting v2 can no longer be accepted
        self.packs.add(3, vs("ihr-a", "1", "2", "3", name="V3"))
        with self.assertRaisesMessage(CommandError, "run ihr_pack_apply first"):
            self.decide("--accept", "ihr-a")
        self.apply()
        # A pending record tampered with in the database is refused, not trusted
        PackItem.objects.filter(item="ihr-a").update(pending_sha256="0" * 64)
        with self.assertRaisesMessage(CommandError, "not the release's"):
            self.decide("--accept", "ihr-a")
        self.apply()  # the record is put right again
        self.decide("--accept", "ihr-a")
        a = rows()["ihr-a"]
        self.assertEqual(a.name, "V3")
        self.assertEqual(
            [c["code"] for c in a.compose["include"][0]["concept"]], ["1", "2", "3"]
        )
        record = items()["ihr-a"]
        self.assertEqual((record.state, record.applied_version), ("current", 3))
        self.assertTrue(PackApply.objects.filter(outcome="accepted").exists())


class VersionTests(PackCase):
    def setUp(self):
        super().setUp()
        self.packs = Packs(self)

    def test_an_older_pack_is_skipped_with_a_warning(self):
        self.packs.add(1, vs("ihr-a", "1"), vs("ihr-b", "2"))
        self.apply()
        self.packs.add(2, vs("ihr-a", "1", "2"))
        self.apply()
        self.packs.only(1)  # an image roll-back: its manifest ends at v1
        since = audit_mark()
        _, err = self.startup()
        self.assertIn("older than the v2 applied here", err)
        self.assertNotIn("outcome=failed", err)
        self.assertEqual(audit_since(since), [])
        self.assertEqual(rows()["ihr-b"].status, "retired")  # v2's retirement stands

    def test_a_newer_release_with_nothing_to_write_is_recorded_once(self):
        """C5: a v2 that only changes how compose is spelt writes no value set, but is
        recorded (outcome "reached"), so later starts take the sha256 short-circuit."""
        self.packs.add(1, vs("ihr-a", "1"))
        self.apply()
        item = vs("ihr-a", "1")
        item["compose"]["property"] = []  # the same meaning
        self.packs.add(2, item)
        since = audit_mark()
        out, _ = self.apply()
        self.assertIn("test-pack v2 unchanged", out)
        self.assertEqual(
            {(e["tbl"], e["op"]) for e in audit_since(since)},
            {("ihr_refdata_packapply", "I")},
        )
        self.assertEqual(
            list(PackApply.objects.values_list("outcome", "version").order_by("id")),
            [("applied", 1), ("reached", 2)],
        )
        with mock.patch.object(engine, "_reconcile", wraps=engine._reconcile) as full:
            since = audit_mark()
            self.apply()
        full.assert_not_called()  # the short-circuit
        self.assertEqual(audit_since(since), [])
        self.assertEqual(PackApply.objects.count(), 2)

    def test_a_malformed_item_is_refused_for_every_hospital(self):
        """C8: the newest release's items are checked before any hospital is touched."""
        bad = vs("ihr-b", "2")
        del bad["compose"]
        self.packs.add(1, vs("ihr-a", "1"), bad)
        with self.assertRaisesMessage(CommandError, "'ihr-b' is malformed"):
            self.startup()
        no_exclude = vs("ihr-b", "2")
        del no_exclude["compose"]["exclude"]  # CARE's lookup could not read it
        self.packs.add(1, vs("ihr-a", "1"), no_exclude)
        with self.assertRaisesMessage(CommandError, "refused for every hospital"):
            self.startup()
        wrong = vs("ihr-b", "2")
        wrong["compose"]["include"] = [{"system": "x", "concept": "not a list"}]
        self.packs.add(1, vs("ihr-a", "1"), wrong)
        with self.assertRaisesMessage(CommandError, "'ihr-b' is malformed"):
            self.startup()
        self.assertFalse(PackApply.objects.exists())
        self.assertFalse(ValueSet.objects.exists())

    def test_the_same_version_with_another_sha256_is_refused_for_the_hospital(self):
        self.packs.add(1, vs("ihr-a", "1"))
        self.apply()
        self.packs.add(1, vs("ihr-a", "1", "2"))  # "v1" rebuilt
        PackRelease.objects.all().delete()  # (the fleet record would refuse it first)
        with self.assertRaisesMessage(CommandError, "1 hospital(s) not done"):
            self.apply()
        failed = PackApply.objects.get(outcome="failed")
        self.assertIn("versions are immutable", failed.error)
        self.assertEqual(len(rows()["ihr-a"].compose["include"][0]["concept"]), 1)

    def test_a_changed_pack_file_is_refused_for_every_hospital(self):
        self.packs.add(1, vs("ihr-a", "1"))
        (self.packs.root / TEST_PACK / "1.json").write_text("{}\n")
        with self.assertRaisesMessage(CommandError, "does not match its sha256"):
            self.startup()
        self.assertFalse(PackApply.objects.exists())


class FleetTests(PackCase):
    def setUp(self):
        super().setUp()
        self.packs = Packs(self)
        self.packs.add(1, vs("ihr-a", "1"))

    def _hospital(self, slug, status):
        with schema_context(get_public_schema_name()):
            hospital = Hospital(slug=slug, name=slug.title(), status=status)
            hospital.auto_create_schema = False  # never migrated: its apply fails
            hospital.save()
        return hospital

    def test_startup_records_a_failed_hospital_alerts_and_carries_on(self):
        self._hospital("packs-gone", Hospital.Status.ACTIVE)  # no schema at all
        self._hospital("packs-new", Hospital.Status.PROVISIONING)
        out, err = self.startup()  # exits 0
        self.assertIn("ihr-packs: hospital=packs-gone outcome=failed", err)
        self.assertNotIn("packs-new", out + err)  # live hospitals only
        self.assertIn("testhosp: test-pack v1 applied", out)
        self.assertEqual(items()["ihr-a"].state, "current")
        with self.assertRaisesMessage(CommandError, "1 hospital(s) not done"):
            call_command(
                "ihr_pack_apply",
                "--all",
                "--operator",
                "tester",
                stdout=StringIO(),
                stderr=StringIO(),
            )

    def test_a_failed_hospital_keeps_a_record_with_the_error(self):
        with mock.patch.object(
            engine, "_reconcile", side_effect=RuntimeError("injected\nfailure")
        ):
            _, err = self.startup()
        self.assertIn("ihr-packs: hospital=testhosp outcome=failed", err)
        failed = PackApply.objects.get()
        self.assertEqual(
            (failed.outcome, failed.pack, failed.version, failed.actor),
            ("failed", TEST_PACK, 1, "packs:startup"),
        )
        self.assertEqual(failed.error, "RuntimeError: injected failure")
        self.assertFalse(PackItem.objects.exists())  # its writes rolled back

    def test_a_busy_hospital_is_retried_then_alerted_at_startup(self):
        """S4: every lock holder on a live hospital is short-lived, so start-up tries a busy
        one again after 5, 15 and 30 s; still busy after that, it is alerted on."""
        with (
            runner.exclusive(SLUG),  # e.g. a provisioning run or a setup e-mail resend
            mock.patch.object(ihr_pack_apply, "sleep") as slept,
        ):
            _, err = self.startup()  # still exits 0
        self.assertEqual([c.args for c in slept.call_args_list], [(5,), (15,), (30,)])
        self.assertEqual(err.count("ihr-packs: hospital=testhosp busy"), 3)
        self.assertIn("ihr-packs: hospital=testhosp outcome=failed", err)
        [given_up] = PackApply.objects.all()
        self.assertEqual(given_up.outcome, "deferred")
        self.assertIn("through 4 tries", given_up.error)
        self.assertFalse(ValueSet.objects.exists())

        # Outside start-up a busy hospital is deferred at once (exit 1)
        with runner.exclusive(SLUG), self.assertRaises(CommandError):
            _, err = self.apply()
        self.assertEqual(PackApply.objects.filter(outcome="deferred").count(), 2)
        self.startup()  # the next start applies it
        self.assertTrue(ValueSet.objects.filter(slug="ihr-a").exists())

    def test_a_hospital_free_again_on_a_retry_is_applied(self):
        holder = ExitStack()
        self.addCleanup(holder.close)
        holder.enter_context(runner.exclusive(SLUG))
        with mock.patch.object(
            ihr_pack_apply, "sleep", side_effect=lambda seconds: holder.close()
        ) as slept:
            out, err = self.startup()
        slept.assert_called_once_with(5)
        self.assertIn("testhosp: test-pack v1 applied", out)
        self.assertNotIn("outcome=", err)
        self.assertEqual(
            list(PackApply.objects.values_list("outcome", flat=True)), ["applied"]
        )

    def test_one_hospital_is_changed_only_when_live(self):
        self._hospital("packs-new", Hospital.Status.PROVISIONING)
        with self.assertRaisesMessage(CommandError, "only live hospitals"):
            call_command("ihr_pack_apply", "--slug", "packs-new", "--operator", "t")
        with self.assertRaisesMessage(CommandError, "--operator is required"):
            call_command("ihr_pack_apply", "--slug", SLUG)

    def test_the_operator_is_a_plain_name_recorded_with_the_packs_prefix(self):
        """S6: no free text posing as another source; start-up names itself."""
        for operator in ("provisioning:ops", "a b", "x" * 65, "asha;rm"):
            with self.assertRaisesMessage(CommandError, "--operator must be"):
                self.apply(operator=operator)
        with self.assertRaisesMessage(CommandError, "no --operator"):
            call_command("ihr_pack_apply", "--all", "--startup", "--operator", "asha")
        for operator in (
            "startup",
            "Check",
        ):  # reserved: only the start-up and check runs
            with self.assertRaisesMessage(CommandError, "is reserved"):
                self.apply(operator=operator)
        self.assertFalse(PackApply.objects.exists())
        self.apply(operator="asha.k@ops")
        self.assertEqual(PackApply.objects.get().actor, "packs:asha.k@ops")
        self.assertEqual(
            PackRelease.objects.get(pack=TEST_PACK).loaded_by, "packs:asha.k@ops"
        )
        with self.assertRaisesMessage(CommandError, "--operator must be"):
            self.pending_cmd(
                "--keep", f"{TEST_PACK}:ihr-a", "--operator", "x:y", "--reason", "r"
            )

    def test_check_reports_and_writes_nothing(self):
        out, _ = self.apply("--check")
        self.assertIn("testhosp: test-pack v1 would change (1 created)", out)
        self.assertFalse(ValueSet.objects.exists())
        self.assertFalse(PackItem.objects.exists())
        self.assertFalse(PackApply.objects.exists())
        self.assertFalse(PackRelease.objects.filter(pack=TEST_PACK).exists())


class AuditTests(PackCase):
    def setUp(self):
        super().setUp()
        self.packs = Packs(self)
        self.packs.add(1, vs("ihr-a", "1"))
        self.release = manifest.latest()[TEST_PACK]

    def test_the_command_names_the_pack_version_and_operator(self):
        since = audit_mark()
        self.apply(operator="asha")
        [entry] = audit_since(since, tbl="emr_valueset")
        self.assertEqual(entry["actor"], "packs:asha")
        context = entry["context"]
        self.assertEqual(
            {k: context.get(k) for k in ("job", "via", "pack", "version", "sha256")},
            {
                "job": "packs",
                "via": "operator",
                "pack": TEST_PACK,
                "version": 1,
                "sha256": self.release.sha256,
            },
        )

    def test_provisioning_writes_keep_their_operation_and_operator(self):
        since = audit_mark()
        job = {"job": "provisioning", "operation": "op-7", "action": "provision"}
        with audit.job(job, "worker:for op:asha"):
            outer = json.loads(audit._current.get()[0])  # the job's context, as set
            engine.apply_packs(self.hospital, operator="provisioning:op:asha")
            # inside the job, once the engine is done: the job's values, none of the pack's
            # (C7), in Python and on the connection the triggers read
            self.assertEqual(json.loads(audit._current.get()[0]), outer)
            self.assertEqual(audit._current.get()[1], "worker:for op:asha")
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('ihr.audit_ctx', true)")
                on_connection = json.loads(cursor.fetchone()[0])
            self.assertEqual(
                {
                    k: on_connection.get(k)
                    for k in ("operation", "pack", "version", "sha256")
                },
                {"operation": "op-7", "pack": None, "version": None, "sha256": None},
            )
        self.assertEqual({k: outer.get(k) for k in job}, job)
        self.assertFalse({"pack", "version", "sha256"} & set(outer))
        entries = audit_since(since)
        self.assertEqual({e["tbl"] for e in entries}, {"emr_valueset", *REFDATA_TABLES})
        for entry in entries:
            self.assertEqual(entry["actor"], "worker:for op:asha")
            context = entry["context"]
            self.assertEqual(
                (context["operation"], context["pack"], context["version"]),
                ("op-7", TEST_PACK, 1),
            )
        # afterwards the outer values are back, without the pack's
        current = json.loads(audit._current.get()[0] or "{}")
        self.assertEqual(current, {})
