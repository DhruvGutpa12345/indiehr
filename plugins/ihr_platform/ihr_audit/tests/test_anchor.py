"""Outside fingerprints of the audit chain (P1-T6b): written, read back, and catching a
database owner who rewrites or cuts the trail."""

import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from care.emr.models import Patient
from django.db import connection, transaction
from django.test import override_settings
from ihr_platform.models import Hospital
from model_bakery import baker

from ihr_audit import anchor, seal, tasks

from .test_audit_trail import AuditCase


class FakeStore:
    """An in-memory write-once bucket: every put is a new version, nothing is deleted."""

    bucket = "fake"

    def __init__(self, locked=True, mode="COMPLIANCE"):
        self.objects = {}  # key -> [(version, body)]
        self.is_locked = locked
        self.mode = mode
        self.markers = set()
        self.clock = None  # the storage's time; by default what the writer says
        self.received = {}

    def put(self, key, body):
        versions = self.objects.setdefault(key, [])
        versions.append((f"v{len(versions)}", body))
        try:
            written = datetime.fromisoformat(json.loads(body)["written_at"])
        except (ValueError, KeyError):
            written = datetime.now(UTC)
        self.received[key] = self.clock or written

    def versions(self, under):
        for key in sorted(self.objects):
            if key.startswith(under):
                stored = self.received.get(key, datetime.now(UTC))
                for version, _ in self.objects[key]:
                    yield key, version, stored
                if key in self.markers:
                    yield key, None, stored

    def keys(self, under):
        return [k for k in sorted(self.objects) if k.startswith(under)]

    def get(self, key, version=None):
        versions = self.objects[key]
        return dict(versions)[version] if version else versions[-1][1]

    def lock(self):
        if not self.is_locked:
            return None
        return {"mode": self.mode, "days": 3650, "versioning": "Enabled"}


class AnchorCase(AuditCase):
    def setUp(self):
        super().setUp()
        self.hospital = Hospital.objects.get(schema_name=connection.schema_name)
        self.uuid = str(self.hospital.uuid)
        baker.make(Patient, name="anchored")
        seal.seal_once(connection.schema_name)
        self.store = FakeStore()

    def problems(self, store=None):
        fps = anchor.load_all(store or self.store)
        chain, _summary = seal.verify(connection.schema_name)
        return fps.problems, chain, anchor.check(self.uuid, fps)


class WriteAndCheckTests(AnchorCase):
    def test_a_written_fingerprint_matches_and_lists_the_hospital(self):
        key = anchor.write(self.store)
        self.assertTrue(key.startswith(anchor.prefix()))
        doc = json.loads(self.store.get(key))
        head = doc["hospitals"][self.uuid]
        _, summary = seal.verify(connection.schema_name)
        self.assertEqual(head["seq"], summary["seals"])
        self.assertEqual(head["hash"], summary["head"])
        self.assertEqual(self.problems(), ([], [], []))

    def test_a_database_owner_rewriting_the_whole_chain_is_caught(self):
        anchor.write(self.store)
        with transaction.atomic():
            with connection.cursor() as cursor:
                # What only the database owner can do: switch the guards off, change an
                # entry, throw the seals away and seal everything again consistently.
                cursor.execute("ALTER TABLE ihr_audit_entry DISABLE TRIGGER USER")
                cursor.execute("ALTER TABLE ihr_audit_seal DISABLE TRIGGER USER")
                cursor.execute(
                    "UPDATE ihr_audit_entry SET actor = 'user:0:nobody' "
                    "WHERE id = (SELECT min(id) FROM ihr_audit_entry)"
                )
                cursor.execute("DELETE FROM ihr_audit_seal")
            seal.seal_once(connection.schema_name)
            _platform, chain, problems = self.problems()
            self.assertEqual(chain, [], "the rewritten chain is consistent in itself")
            self.assertTrue(
                any("rewritten" in p or "cut" in p for p in problems), problems
            )
            transaction.set_rollback(True)

    def test_cutting_the_newest_seals_is_caught(self):
        anchor.write(self.store)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE ihr_audit_seal DISABLE TRIGGER USER")
                cursor.execute(
                    "DELETE FROM ihr_audit_seal WHERE seq = (SELECT max(seq) FROM ihr_audit_seal)"
                )
            _, _, problems = self.problems()
            self.assertTrue(
                any("missing (the trail was cut)" in p for p in problems), problems
            )
            transaction.set_rollback(True)

    def test_a_hospital_dropped_from_later_fingerprints_is_reported(self):
        anchor.write(self.store)
        with patch.object(anchor, "snapshot", return_value={}):
            anchor.write(self.store, now=datetime.now(UTC) + timedelta(minutes=1))
        _, _, problems = self.problems()
        self.assertIn(
            "missing from the newest fingerprint although earlier ones list it",
            problems,
        )


class BucketTests(AnchorCase):
    def test_unlocked_bucket_hidden_or_unreadable_objects_are_problems(self):
        store = FakeStore(locked=False)
        key = anchor.write(store)
        store.markers.add(key)
        store.put(anchor.prefix() + "junk.json", b"not json")
        platform, _, _ = self.problems(store)
        self.assertTrue(any("no Object Lock" in p for p in platform), platform)
        self.assertTrue(any("delete marker" in p for p in platform), platform)
        self.assertTrue(any("unreadable" in p for p in platform), platform)

    def test_another_platforms_objects_are_refused(self):
        body = json.dumps(
            {
                "format": anchor.FORMAT,
                "platform": "anchors/elsewhere/",
                "written_at": datetime.now(UTC).isoformat(),
                "hospitals": {},
            }
        ).encode()
        self.store.put(anchor.prefix() + "x.json", body)
        platform, _, _ = self.problems()
        self.assertTrue(any("unreadable" in p for p in platform), platform)

    def test_a_late_fingerprint_job_is_reported(self):
        now = datetime.now(UTC)
        anchor.write(self.store, now=now - timedelta(hours=3))
        fps = anchor.load_all(self.store)
        self.assertTrue(anchor.platform_problems(fps, now=now))
        self.assertEqual(
            anchor.platform_problems(anchor.Fingerprints()),
            ["no fingerprint has been written yet"],
        )
        anchor.write(self.store, now=now)
        fps = anchor.load_all(self.store)
        self.assertEqual(anchor.platform_problems(fps, now=now), [])
        # --since skips older day folders
        later = anchor.load_all(self.store, since=(now + timedelta(days=2)).date())
        self.assertEqual(later.count, 0)

    def test_the_export_finds_the_first_fingerprint_covering_a_seal(self):
        _, summary = seal.verify(connection.schema_name)
        seals = summary["seals"]
        start = datetime.now(UTC) - timedelta(minutes=5)
        self.assertIsNone(anchor.find_covering(self.uuid, 1, seals, start, self.store))
        anchor.write(self.store)
        baker.make(Patient, name="later")
        seal.seal_once(connection.schema_name)
        anchor.write(self.store, now=datetime.now(UTC) + timedelta(seconds=1))
        found = anchor.find_covering(self.uuid, 1, seals, start, self.store)
        self.assertEqual(found.hospitals[self.uuid][0], seals)  # not the later one
        newer = anchor.find_covering(
            self.uuid, seals + 1, seals + 99, start, self.store
        )
        self.assertGreater(newer.hospitals[self.uuid][0], seals)


class JobTests(AnchorCase):
    def test_the_job_without_credentials(self):
        with patch.dict(os.environ, {}, clear=False):
            for name in ("IHR_AUDIT_ANCHOR_BUCKET", "IHR_AUDIT_ANCHOR_WRITE_KEY"):
                os.environ.pop(name, None)
            with override_settings(DEBUG=True):
                tasks.write_anchor()  # dev without the bucket: a warning only
            with (
                override_settings(DEBUG=False),
                self.assertRaises(anchor.NotConfiguredError),
            ):
                tasks.write_anchor()

    def test_the_job_writes_through_the_writer_store(self):
        with patch.object(
            anchor, "S3Store", lambda role: self.store if role == "WRITE" else None
        ):
            tasks.write_anchor()
        self.assertEqual(len(self.store.keys(anchor.prefix())), 1)


class ReviewRegressionTests(AnchorCase):
    def test_a_hospital_deleted_or_replaced_in_the_database_is_reported(self):
        anchor.write(self.store)
        fps = anchor.load_all(self.store)
        slug = self.hospital.slug
        self.assertEqual(anchor.unknown_hospitals(fps, {self.uuid: slug}), [])
        (gone,) = anchor.unknown_hospitals(fps, {})
        self.assertIn("no longer in the database", gone)
        (swapped,) = anchor.unknown_hospitals(fps, {"another-uuid": slug})
        self.assertIn("now gives that address to another-uuid", swapped)

    def test_retention_the_storage_admin_can_lift_is_refused_outside_dev(self):
        store = FakeStore(mode="GOVERNANCE")
        anchor.write(store)
        with override_settings(DEBUG=False):
            fps = anchor.load_all(store)
            self.assertTrue(any("GOVERNANCE" in p for p in fps.problems), fps.problems)
        with override_settings(DEBUG=True):
            fps = anchor.load_all(store)
            self.assertEqual(fps.problems, [])
            self.assertTrue(fps.notes)

    def test_storage_outage_still_runs_the_local_checks(self):
        from io import StringIO

        from botocore.exceptions import EndpointConnectionError
        from django.core.management import call_command

        out = StringIO()
        with (
            patch.object(
                anchor,
                "load_all",
                side_effect=EndpointConnectionError(endpoint_url="x"),
            ),
            self.assertRaises(SystemExit),
        ):
            call_command("ihr_audit_verify", slug=self.hospital.slug, stdout=out)
        text = out.getvalue()
        self.assertIn("outside fingerprints unreadable", text)
        self.assertIn(f"{self.hospital.slug}: ", text)  # the chain was still checked

    def test_one_failed_send_never_stops_beat(self):
        from celery.beat import ScheduleEntry
        from config.celery_app import app
        from ihr_platform.beat import PLATFORM_ONCE, ActiveTenantScheduler

        scheduler = ActiveTenantScheduler(app=app, lazy=True)
        entry = ScheduleEntry(
            name="x",
            task="ihr_audit.anchor",
            app=app,
            options={"headers": {PLATFORM_ONCE: True}},
        )
        with patch.object(scheduler, "apply_async", side_effect=OSError("broker down")):
            scheduler.apply_entry(entry)  # logged, not raised


class SecondReviewTests(AnchorCase):
    def test_storage_time_not_the_writers_clock_decides(self):
        now = datetime.now(UTC)
        self.store.clock = now - timedelta(hours=3)
        anchor.write(self.store, now=now)  # claims "now", received 3 hours ago
        fps = anchor.load_all(self.store)
        self.assertTrue(any("the storage received" in p for p in fps.problems))
        self.assertTrue(anchor.platform_problems(fps, now=now))

    def test_claims_are_checked_in_batches(self):
        for minute in range(5):
            anchor.write(self.store, now=datetime.now(UTC) + timedelta(minutes=minute))
        calls = []
        lookup = anchor.database_hashes()

        def counting(hospital_uuid, seqs):
            calls.append(hospital_uuid)
            return lookup(hospital_uuid, seqs)

        fps = anchor.load_all(self.store, stored_hashes=counting, batch=2)
        self.assertEqual(fps.count, 5)
        self.assertEqual(fps.pending, {})
        self.assertGreaterEqual(calls.count(self.uuid), 3)
        self.assertEqual(anchor.check(self.uuid, fps), [])

    def test_a_failed_write_still_reads_and_fails_the_run(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        with (
            patch.object(
                anchor, "write", side_effect=anchor.NotConfiguredError("no key")
            ),
            patch.object(anchor, "load_all", return_value=anchor.load_all(self.store)),
            self.assertRaises(SystemExit),
        ):
            call_command(
                "ihr_audit_verify", slug=self.hospital.slug, anchor=True, stdout=out
            )
        self.assertIn("fingerprint not written", out.getvalue())


class ThirdReviewTests(AnchorCase):
    def test_a_fingerprint_written_twice_is_reported_and_never_used_as_proof(self):
        key = anchor.write(self.store)
        _, summary = seal.verify(connection.schema_name)
        body = self.store.get(key)
        self.store.put(key, body)  # a second version under the same name
        fps = anchor.load_all(self.store)
        self.assertTrue(any("written more than once" in p for p in fps.problems))
        start = datetime.now(UTC) - timedelta(minutes=5)
        self.assertIsNone(
            anchor.find_covering(self.uuid, 1, summary["seals"], start, self.store)
        )
