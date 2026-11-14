"""The audit watch (P1-T21): incremental checks from a checkpoint, the header walk, the rolling
recompute, bootstrap from a fingerprint, restore and offboarding events, seal lag, the sealing of
inactive hospitals, and what its metrics and log lines may say.

The trail is append-only and tests never clean it up (test_audit_trail.py): every tamper below
runs as the database owner inside a transaction that is rolled back.
"""

import contextlib
import json
import logging
import re
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest import SkipTest
from unittest.mock import patch

from care.emr.models import Patient
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, OperationalError, connection, transaction
from django.test import SimpleTestCase
from ihr_platform.models import Hospital
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_platform.testing import as_owner
from model_bakery import baker

from ihr_audit import anchor, events, incremental, seal, tasks, watch
from ihr_audit.events import SealRow
from ihr_audit.incremental import Checkpoint, CheckpointStore

from .test_anchor import FakeStore
from .test_audit_trail import AuditCase


def _sealed(n=1):
    for _ in range(n):
        baker.make(Patient)
        seal.seal_once(TEST_SCHEMA)


@contextlib.contextmanager
def tampering():
    """What only the database owner can do: the trail's guards off, then everything rolled
    back. Yields a cursor."""
    with as_owner(), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE ihr_audit_entry DISABLE TRIGGER ihr_audit_append_only"
        )
        cursor.execute(
            "ALTER TABLE ihr_audit_seal DISABLE TRIGGER ihr_audit_append_only"
        )
        try:
            yield cursor
        finally:
            transaction.set_rollback(True)


def _rows(cursor, low, high):
    cursor.execute(
        "SELECT seq, lower_txid::text, upper_txid::text, entries, entries_hash, prev_hash, "
        "hash, version FROM ihr_audit_seal WHERE seq BETWEEN %s AND %s ORDER BY seq",
        [low, high],
    )
    return [
        SealRow(r[0], r[1], r[2], r[3], bytes(r[4]), bytes(r[5]), bytes(r[6]), r[7])
        for r in cursor.fetchall()
    ]


class WatchCase(AuditCase):
    def setUp(self):
        super().setUp()
        self.hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
        self.uuid = str(self.hospital.uuid)
        _sealed(3)
        self.events = events.Events()

    def check(self, cursor, checkpoint, claim=None, found_events=None):
        return incremental.check_from_checkpoint(
            cursor,
            TEST_SCHEMA,
            self.uuid,
            checkpoint,
            claim,
            found_events or self.events,
        )

    def head(self, cursor):
        walk = incremental.header_walk(cursor, TEST_SCHEMA)
        return walk.head_seq, walk.head_hash, walk.head_upper


class IncrementalTests(WatchCase):
    def test_a_clean_chain_passes_and_the_checkpoint_follows_the_head(self):
        with connection.cursor() as cursor:
            first = self.check(cursor, None)
            self.assertEqual(first.problems, [])
            self.assertEqual(first.seeded, "genesis")  # no fingerprint names it: seal 1
            # a chain with seals and no checkpoint: lost (the watch decides whether it should
            # have had one, P1-T21 review S5)
            self.assertTrue(first.lost)
            full, _ = seal.verify(TEST_SCHEMA)
            self.assertEqual(full, [])  # the same verdict as the whole recompute
            cp = first.checkpoint
            self.assertEqual((cp.seq, cp.hash), self.head(cursor)[:2])
        _sealed()
        with connection.cursor() as cursor:
            again = self.check(cursor, cp)
            self.assertEqual(again.problems, [])
            self.assertGreater(again.checkpoint.seq, cp.seq)
            self.assertEqual(again.recomputed.last_seq, again.checkpoint.seq)

    def test_an_entry_changed_after_the_checkpoint_is_found(self):
        with connection.cursor() as cursor:
            cp = self.check(cursor, None).checkpoint
        _sealed()
        with tampering() as cursor:
            cursor.execute(
                "UPDATE ihr_audit_entry SET actor = 'someone-else' "
                "WHERE id = (SELECT max(id) FROM ihr_audit_entry "
                "WHERE txid < (SELECT max(upper_txid) FROM ihr_audit_seal))"
            )
            result = self.check(cursor, cp)
        self.assertTrue(
            any("entries changed" in p for p in result.problems), result.problems
        )
        self.assertIsNone(
            result.checkpoint, "a failing chain never moves its checkpoint"
        )

    def test_a_moved_checkpoint_is_refused(self):
        with connection.cursor() as cursor:
            cp = self.check(cursor, None).checkpoint
        moved = Checkpoint(cp.hospital, 0, cp.seq, "ab" * 32, cp.upper, cp.at)
        with connection.cursor() as cursor:
            result = self.check(cursor, moved)
        self.assertTrue(
            any("differs from the watch's checkpoint" in p for p in result.problems)
        )
        self.assertIsNone(result.checkpoint)
        with tampering() as cursor:  # the newest seals cut below the checkpoint
            cursor.execute("DELETE FROM ihr_audit_seal WHERE seq >= %s", [cp.seq])
            result = self.check(cursor, cp)
        self.assertTrue(
            any("is missing (the trail was cut)" in p for p in result.problems)
        )

    def test_bootstrap_from_the_fingerprint_never_the_database_head(self):
        """A8: no checkpoint, a fingerprint names seal k: seal k must still have that hash, and
        the entries after it are recomputed."""
        with connection.cursor() as cursor:
            seq, digest, _ = self.head(cursor)
        stored = datetime.now(UTC)
        _sealed()
        with connection.cursor() as cursor:
            result = self.check(cursor, None, (seq, digest, stored))
            self.assertEqual(result.problems, [])
            self.assertEqual((result.seeded, result.lost), ("fingerprint", True))
            self.assertEqual(result.recomputed.last_seq - result.recomputed.seals, seq)
            wrong = self.check(cursor, None, (seq, "cd" * 32, stored))
        self.assertTrue(
            any("differs from the newest fingerprint" in p for p in wrong.problems)
        )
        self.assertIsNone(wrong.checkpoint)
        # the whole chain rewritten consistently by the owner: the database head would agree
        # with itself, the fingerprint does not
        with tampering() as cursor:
            cursor.execute(
                "UPDATE ihr_audit_seal SET hash = sha256(hash) WHERE seq = %s", [seq]
            )
            result = self.check(cursor, None, (seq, digest, stored))
        self.assertTrue(
            any("differs from the newest fingerprint" in p for p in result.problems)
        )

    def test_a_checkpoint_from_a_future_generation_is_refused(self):
        with connection.cursor() as cursor:
            cp = self.check(cursor, None).checkpoint
            ahead = Checkpoint(cp.hospital, 2, cp.seq, cp.hash, cp.upper, cp.at)
            result = self.check(cursor, ahead)
        self.assertTrue(any("restore generation 2" in p for p in result.problems))


class HeaderWalkTests(WatchCase):
    def test_a_rewritten_seal_below_the_checkpoint_is_found(self):
        """A3: seal j's entries hash and hash rewritten consistently with its own prev_hash, the
        next seal left alone. Only the next seal's link shows it, and the walk reads them all."""
        with connection.cursor() as cursor:
            cp = self.check(cursor, None).checkpoint
        self.assertGreaterEqual(cp.seq, 3)
        j = cp.seq - 1
        with tampering() as cursor:
            [row] = _rows(cursor, j, j)
            forged = bytes.fromhex("11" * 32)
            new_hash = seal.seal_hash(
                row.prev_hash,
                row.seq,
                row.lower,
                row.upper,
                row.entries,
                forged,
                row.version,
            )
            cursor.execute(
                "UPDATE ihr_audit_seal SET entries_hash = %s, hash = %s WHERE seq = %s",
                [forged, new_hash, j],
            )
            walk = incremental.header_walk(cursor, TEST_SCHEMA)
            result = self.check(cursor, cp)
        self.assertEqual(walk.bad, 1, walk.problems)
        self.assertIn(
            f"seal {j + 1}: does not follow the previous seal", walk.problems[0]
        )
        # the checkpoint itself still matches: only the walk catches it
        self.assertTrue(result.problems)
        self.assertIsNone(result.checkpoint)

    def test_every_kind_of_header_problem_is_named(self):
        with tampering() as cursor:
            cursor.execute(
                "UPDATE ihr_audit_seal SET hash = sha256(hash) WHERE seq = 2"
            )
            cursor.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'ihr_audit_seal'::regclass "
                "AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%%version%%'"
            )
            for (name,) in cursor.fetchall():
                cursor.execute(f'ALTER TABLE ihr_audit_seal DROP CONSTRAINT "{name}"')
            cursor.execute("UPDATE ihr_audit_seal SET version = 9 WHERE seq = 3")
            cursor.execute("DELETE FROM ihr_audit_seal WHERE seq = 1")
            walk = incremental.header_walk(cursor, TEST_SCHEMA)
        text = " ".join(walk.problems)
        for want in (
            "a seal is missing",
            "does not start",
            "does not follow",
            "unknown format",
            "hash does not match",
        ):
            self.assertIn(want, text)


class RollingRecomputeTests(WatchCase):
    def watch(self, d, **cfg):
        config = watch.Config(root=Path(d), **cfg)
        return watch.Watch(config, store=None)

    def test_a_changed_old_entry_is_found_by_the_nightly_recompute(self):
        """An entry below the checkpoint changed: the 15-minute check (header walk, seals
        after the checkpoint) cannot see it; the nightly recompute of the horizon does."""
        with tempfile.TemporaryDirectory() as d:
            w = self.watch(d)
            with connection.cursor() as cursor:
                cp = self.check(cursor, None).checkpoint
            with tampering() as cursor:
                cursor.execute(
                    "UPDATE ihr_audit_entry SET actor = 'someone-else' WHERE id = "
                    "(SELECT max(e.id) FROM ihr_audit_entry e JOIN ihr_audit_seal s "
                    " ON e.txid >= s.lower_txid AND e.txid < s.upper_txid WHERE s.seq = %s)",
                    [cp.seq - 1],
                )
                quick = self.check(cursor, cp)
                summary = w._rehash()
                problems = w.state.data["rehash_problems"].get(self.uuid, [])
            self.assertEqual(
                quick.problems, [], "the old edit is not in the 15-minute check"
            )
            self.assertTrue(summary["complete"])
            self.assertTrue(
                any(f"seal {cp.seq - 1}: entries changed" in p for p in problems),
                problems,
            )
            self.assertTrue(w.state.data["rehash_complete"])

    def test_a_small_budget_spreads_a_cycle_over_nights(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as e:
            horizon = self.watch(d)._rehash()["horizon_entries"]
            w = self.watch(e, rehash_budget=horizon // 3 + 1)
            nights = 1
            self.assertFalse(w._rehash()["complete"])
            self.assertIsNotNone(w.state.data["rehash"])
            while nights < 20 and not w._rehash()["complete"]:
                nights += 1
            self.assertGreaterEqual(nights, 2)
            self.assertIsNone(w.state.data["rehash"])
            self.assertEqual(w.state.data["rehash_problems"], {})


class EventTests(WatchCase):
    def _segment(self, after_seq, after_hash, n=2):
        """A stand-in superseded segment (P1-T22 keeps real ones): n seals chained after
        (after_seq, after_hash), as the live tail a restore replaced."""
        rows, prev, lower = [], bytes.fromhex(after_hash), "999000"
        for seq in range(after_seq + 1, after_seq + 1 + n):
            upper = str(int(lower) + 10)
            entries_hash = bytes.fromhex(f"{seq:02x}" * 32)
            digest = seal.seal_hash(
                prev, seq, lower, upper, 5, entries_hash, seal.FORMAT
            )
            rows.append(
                SealRow(seq, lower, upper, 5, entries_hash, prev, digest, seal.FORMAT)
            )
            prev, lower = digest, upper
        return rows

    def _restore(self, store, rows, restored_to, generation=1):
        doc = events.restore_document(
            self.hospital, generation, restored_to, rows, "tester"
        )
        return events.write(doc, store)

    def test_a_restore_event_excuses_only_claims_its_verified_segment_holds(self):
        store = FakeStore()
        with connection.cursor() as cursor:
            seq, digest, _ = self.head(cursor)
        rows = self._segment(seq, digest)
        self._restore(store, rows, (seq, digest))
        before = datetime.now(UTC) - timedelta(hours=1)
        claim = (rows[0].seq, rows[0].hash.hex())
        good = events._Segments()
        good.source = lambda h, g: events.Segment(rows, verify_entries=list)
        found = events.read(store, good)
        [event] = found.restores[self.uuid]
        self.assertEqual(event.problems, [])
        self.assertTrue(found.excuse(self.uuid, *claim, before))
        # a claim stored after the restore, or for a seal outside the range, is not excused
        self.assertFalse(
            found.excuse(self.uuid, *claim, datetime.now(UTC) + timedelta(hours=1))
        )
        self.assertFalse(found.excuse(self.uuid, seq, "ef" * 32, before))
        # no segment (P1-T22 not built yet), or one that does not match: nothing is excused
        for source in (
            None,
            lambda h, g: events.Segment(rows[:1], verify_entries=list),
            lambda h, g: events.Segment(rows, verify_entries=None),
            lambda h, g: events.Segment(
                rows, verify_entries=lambda: ["entries changed"]
            ),
        ):
            segments = events._Segments()
            segments.source = source
            bad = events.read(store, segments)
            [event] = bad.restores[self.uuid]
            self.assertTrue(event.problems, source)
            self.assertFalse(bad.excuse(self.uuid, *claim, before))

    def test_a_checkpoint_in_the_superseded_segment_is_rebased(self):
        """A4: the watch checkpointed the live tail a restore then replaced: with a verified
        restore event it moves to the restored head; without one it is refused."""
        store = FakeStore()
        with connection.cursor() as cursor:
            seq, digest, _upper = self.head(cursor)
        rows = self._segment(seq, digest)
        self._restore(store, rows, (seq, digest))
        old = Checkpoint(
            self.uuid, 0, rows[-1].seq, rows[-1].hash.hex(), "x", "2026-11-14T00:00"
        )
        segments = events._Segments()
        segments.source = lambda h, g: events.Segment(rows, verify_entries=list)
        with connection.cursor() as cursor:
            result = self.check(cursor, old, found_events=events.read(store, segments))
            self.assertEqual(result.problems, [])
            self.assertTrue(result.rebased)
            self.assertEqual(result.checkpoint.generation, 1)
            refused = self.check(cursor, old, found_events=events.read(store))
        self.assertTrue(
            any("superseded segment" in p for p in refused.problems), refused.problems
        )

    def test_fingerprints_check_honours_restore_and_offboarding_events(self):
        store = FakeStore()
        anchor.write(store)
        with connection.cursor() as cursor:
            seq, digest, _ = self.head(cursor)
        # a fingerprint of a later, replaced seal: a "rewritten" claim
        rows = self._segment(seq, digest)
        fake = json.dumps(
            {
                "format": anchor.FORMAT,
                "platform": anchor.prefix(),
                "written_at": datetime.now(UTC).isoformat(),
                "hospitals": {
                    self.uuid: {
                        "slug": self.hospital.slug,
                        "seq": rows[0].seq,
                        "hash": rows[0].hash.hex(),
                        "sealed_at": None,
                    }
                },
            }
        ).encode()
        store.put(f"{anchor.prefix()}{datetime.now(UTC):%Y/%m/%d}/claim.json", fake)
        fps = anchor.load_all(store)
        self.assertTrue(anchor.check(self.uuid, fps))
        store.clock = datetime.now(UTC) + timedelta(seconds=5)  # the event comes after
        self._restore(store, rows, (seq, digest))
        store.clock = None
        segments = events._Segments()
        segments.source = lambda h, g: events.Segment(rows, verify_entries=list)
        found = events.read(store, segments)
        fps = anchor.load_all(store, excuse=found.excuse)
        self.assertEqual(anchor.check(self.uuid, fps), [])
        self.assertEqual(fps.excused, 1)
        # an offboarding event closes a hospital the database no longer has
        gone = "0f0f0f0f-0000-4000-8000-000000000001"
        fps.slugs[gone] = "goneaway"
        self.assertTrue(anchor.unknown_hospitals(fps, {self.uuid: self.hospital.slug}))
        closing = type("H", (), {"uuid": gone, "slug": "goneaway"})()
        events.write(
            events.offboarding_document(closing, (3, "aa" * 32), "tester", "cert-1"),
            store,
        )
        found = events.read(store)
        self.assertEqual(found.closed(), {gone})
        self.assertEqual(
            anchor.unknown_hospitals(
                fps, {self.uuid: self.hospital.slug}, found.closed()
            ),
            [],
        )

    def test_forged_or_doubled_event_objects_are_store_problems(self):
        store = FakeStore()
        with connection.cursor() as cursor:
            seq, digest, _ = self.head(cursor)
        key = self._restore(store, self._segment(seq, digest), (seq, digest))
        store.put(key, store.get(key))  # written twice
        doc = events.offboarding_document(self.hospital, (1, "aa" * 32), "tester", "c1")
        doc["slug"] = "evil\nihr-audit: hospital=alpha check=chain outcome=failed"
        store.put(f"{events.prefix()}2026/11/14/forged.json", json.dumps(doc).encode())
        found = events.read(store)
        text = " ".join(found.problems)
        self.assertIn("written more than once", text)
        self.assertIn("unreadable", text)
        self.assertEqual(found.offboarded, {})
        self.assertFalse(
            events.prefix().startswith(anchor.prefix())
        )  # never read as a fingerprint


class SealInactiveTests(WatchCase):
    def test_a_suspended_hospitals_last_entries_are_sealed(self):
        baker.make(Patient)  # written while active, not sealed yet
        with connection.cursor() as cursor:
            self.assertGreater(incremental.unsealed(cursor, TEST_SCHEMA).sealable, 0)
        with as_owner():
            Hospital.objects.filter(pk=self.hospital.pk).update(
                status=Hospital.Status.SUSPENDED
            )
        try:
            done = tasks.seal_inactive_hospitals()
        finally:
            with as_owner():
                Hospital.objects.filter(pk=self.hospital.pk).update(
                    status=Hospital.Status.ACTIVE
                )
        self.assertGreaterEqual(done["sealed"], 1)
        self.assertGreater(done["entries"], 0)
        with connection.cursor() as cursor:
            self.assertEqual(incremental.unsealed(cursor, TEST_SCHEMA).sealable, 0)
        # an active hospital is beat's: the task leaves it alone
        baker.make(Patient)
        self.assertEqual(tasks.seal_inactive_hospitals()["entries"], 0)


class WatchPassTests(WatchCase):
    def test_seal_lag_and_its_line_and_no_hospital_in_the_metrics(self):
        lines = []
        with (
            tempfile.TemporaryDirectory() as d,
            patch.object(watch, "_emit", lines.append),
        ):
            w = watch.Watch(watch.Config(root=Path(d)), store=None)
            baker.make(Patient)  # a finished entry, not sealed
            w.started -= timedelta(
                hours=1
            )  # not a fresh start: the first sighting is now
            w.run("fast")
            values = {name: v for (name, _), v in w.gauges.values.items()}
            self.assertLess(values["ihr_audit_seal_lag_seconds"], 60)
            # seen waiting 20 minutes with the same newest seal: late, and named
            head, _ = w.seal_seen[self.uuid]
            w.seal_seen[self.uuid] = (head, datetime.now(UTC) - timedelta(minutes=20))
            w.run("fast")
            values = {name: v for (name, _), v in w.gauges.values.items()}
            self.assertGreater(values["ihr_audit_seal_lag_seconds"], 1100)
            self.assertIn(
                f"ihr-audit: hospital={self.hospital.slug} check=seal outcome=failed",
                lines,
            )
            seal.seal_once(TEST_SCHEMA)  # a seal resets it
            w.run("fast")
            values = {name: v for (name, _), v in w.gauges.values.items()}
            self.assertEqual(values["ihr_audit_seal_lag_seconds"], 0)
            w.run("incremental")
            text = w.gauges.render()
        for name in watch.PASSES[:2]:
            self.assertIn(f'ihr_audit_watch_last_run_ok{{pass="{name}"}} 1', text)
        for secret in (self.hospital.slug, self.uuid, TEST_SCHEMA):
            self.assertNotIn(secret, text)
        self.assertFalse(re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text))
        self.assertIn(
            "ihr_audit_fingerprints_readable 0", text
        )  # no reader here: unreadable
        self.assertIn("ihr_audit_chain_broken_hospitals 0", text)

    def test_a_switched_off_trigger_is_seen_by_the_fast_pass(self):
        with as_owner(), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("ALTER TABLE emr_patient DISABLE TRIGGER ihr_audit")
            found = incremental.disabled_triggers(cursor)
            transaction.set_rollback(True)
        self.assertEqual(found.get(TEST_SCHEMA), 1)
        with connection.cursor() as cursor:
            self.assertNotIn(TEST_SCHEMA, incremental.disabled_triggers(cursor))

    def test_a_broken_chain_is_counted_and_named_once_per_pass(self):
        lines = []
        with (
            tempfile.TemporaryDirectory() as d,
            patch.object(watch, "_emit", lines.append),
        ):
            w = watch.Watch(watch.Config(root=Path(d)), store=None)
            w.run("incremental")
            cp = w.checkpoints.get(self.uuid)
            self.assertIsNotNone(cp)
            w.checkpoints.put(
                Checkpoint(cp.hospital, 0, cp.seq, "ab" * 32, cp.upper, cp.at)
            )
            w.run("incremental")
            values = {name: v for (name, _), v in w.gauges.values.items()}
        self.assertGreaterEqual(values["ihr_audit_chain_broken_hospitals"], 1)
        mine = f"ihr-audit: hospital={self.hospital.slug} check=chain outcome=failed"
        self.assertEqual(lines.count(mine), 1)

    def test_the_metrics_endpoint_answers_get_metrics_only(self):
        import threading
        import urllib.error
        import urllib.request

        gauges = watch.Gauges()
        gauges.set("ihr_audit_chain_broken_hospitals", 0)
        server = watch.metrics_server(gauges, 0, host="127.0.0.1")
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            body = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/metrics", timeout=5
            ).read()
            self.assertIn(b"ihr_audit_chain_broken_hospitals 0", body)
            for path, method in (("/", "GET"), ("/metrics", "POST")):
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}{path}",
                    method=method,
                    data=b"" if method == "POST" else None,
                )
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(req, timeout=5)  # noqa: S310 (http to itself)
        finally:
            server.shutdown()
            server.server_close()

    def test_acknowledged_objects_are_reviewed_lines(self):
        fps = anchor.Fingerprints()
        fps.problem("unreadable", "fingerprint k1: unreadable", "anchors/x/k1.json")
        fps.problem("skew", "fingerprint k2: skew", "anchors/x/k2.json")
        counts = watch.store_counts(fps, {"anchors/x/k1.json": "reviewed"})
        self.assertEqual((counts["unreadable"], counts["skew"]), (0, 1))
        with tempfile.NamedTemporaryFile("w", suffix=".txt") as f:
            f.write("anchors/x/* 2026-11-14 a pattern\n")
            f.flush()
            with self.assertRaises(ValueError):
                watch.acknowledged(Path(f.name))
        self.assertEqual(watch.acknowledged(), {})  # the reviewed list starts empty


class VerifyCommandTests(WatchCase):
    def test_json_and_from_checkpoint(self):
        store = FakeStore()
        anchor.write(store)
        with tempfile.TemporaryDirectory() as d:
            CheckpointStore(d).ensure()
            with connection.cursor() as cursor:
                CheckpointStore(d).put(self.check(cursor, None).checkpoint)
            out = StringIO()
            with patch.object(anchor, "S3Store", lambda role, quick=False: store):
                call_command(
                    "ihr_audit_verify",
                    slug=self.hospital.slug,
                    json=True,
                    from_checkpoint=Path(d),
                    stdout=out,
                )
            report = json.loads(out.getvalue())
            self.assertTrue(report["ok"], report)
            row = report["hospitals"][self.hospital.slug]
            self.assertEqual(row["problems"], [])
            self.assertEqual(row["seeded"], "")
            # a moved checkpoint fails the command, and it never changes the checkpoints
            cp = CheckpointStore(d).get(self.uuid)
            CheckpointStore(d).put(
                Checkpoint(cp.hospital, 0, cp.seq, "ab" * 32, cp.upper, cp.at)
            )
            out = StringIO()
            with (
                patch.object(anchor, "S3Store", lambda role, quick=False: store),
                self.assertRaises(SystemExit),
            ):
                call_command(
                    "ihr_audit_verify",
                    slug=self.hospital.slug,
                    json=True,
                    from_checkpoint=Path(d),
                    stdout=out,
                )
            self.assertFalse(json.loads(out.getvalue())["ok"])
            self.assertEqual(CheckpointStore(d).get(self.uuid).hash, "ab" * 32)


class WatchRoleTests(WatchCase):
    @classmethod
    def setUpClass(cls):
        from ihr_platform import testing

        if not testing.active():
            raise SkipTest("needs IHR_DB_ROLES=test (the roles and their grants)")
        super().setUpClass()

    def test_the_watch_role_reads_the_trail_and_changes_nothing(self):
        """A11: SELECT on the trail and the hospital list; no write, no sealing function."""
        from ihr_platform import db_roles

        with as_owner(), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE ihr_audit_watch")
            walk = incremental.header_walk(cursor, TEST_SCHEMA)
            incremental.unsealed(cursor, TEST_SCHEMA)
            cursor.execute(
                "SELECT count(*) FROM public.ihr_platform_hospitalschemastate"
            )
            found = db_roles.watch_problems(cursor)
            # only the session's own login differs here (SET ROLE): no right beyond reading
            self.assertEqual([r for r, _ in found], ["wrong_role"], found)
            for statement in (
                "INSERT INTO ihr_audit_entry (kind, actor) VALUES ('write', 'x')",
                "UPDATE emr_patient SET name = name WHERE false",
                f'SELECT "{TEST_SCHEMA}".ihr_audit_seal_next()',
            ):
                with self.assertRaises(DatabaseError), transaction.atomic():
                    cursor.execute(statement)
            transaction.set_rollback(True)
        self.assertGreater(walk.seals, 0)


class WatchSettingsTests(SimpleTestCase):
    """settings/watch.py's own checks (secure.watch_problems): the watch's role and reader key,
    and never the web app's or the workers' secrets."""

    GOOD = {
        "DATABASE_URL": "postgres://ihr_audit_watch:" + "w" * 43 + "@db:5432/ihr",
        "IHR_AUDIT_ANCHOR_READ_KEY": "reader",
        "IHR_AUDIT_ANCHOR_READ_SECRET": "reader-secret",
        "GPG_KEY": "the python image's own",
    }
    SETTINGS = {
        "DEBUG": False,
        "IHR_TENANT_DOMAIN_SUFFIX": "app.example.in",
        "IHR_DB_ROLES": "required",
        "IHR_DB_ROLE": "watch",
    }

    def test_the_watch_settings_pass(self):
        from ihr_platform.settings import secure

        self.assertEqual(secure.watch_problems(self.SETTINGS, self.GOOD), [])

    def test_anything_else_is_refused(self):
        from ihr_platform.settings import secure

        for settings, env, want in (
            ({"IHR_DB_ROLE": "app"}, {}, "IHR_DB_ROLE watch"),
            (
                {},
                {"DATABASE_URL": "postgres://ihr_app:x@db:5432/ihr"},
                "ihr_audit_watch",
            ),
            ({}, {"IHR_AUDIT_ANCHOR_READ_KEY": ""}, "IHR_AUDIT_ANCHOR_READ_KEY"),
            ({}, {"IHR_AUDIT_ANCHOR_WRITE_KEY": "w"}, "IHR_AUDIT_ANCHOR_WRITE_KEY"),
            ({}, {"REDIS_URL": "redis://:p@redis:6379/0"}, "REDIS_URL"),
            ({}, {"DJANGO_SECRET_KEY": "k"}, "DJANGO_SECRET_KEY"),
            ({}, {"FILE_UPLOAD_SECRET": "s"}, "FILE_UPLOAD_SECRET"),
            ({"DEBUG": True}, {}, "DEBUG"),
            ({"IHR_TENANT_DOMAIN_SUFFIX": "localhost"}, {}, "real domain"),
        ):
            found = secure.watch_problems(
                {**self.SETTINGS, **settings}, {**self.GOOD, **env}
            )
            self.assertTrue(any(want in p for p in found), (want, found))


# --- P1-T21 review fixes (each test fails without its fix) ------------------------------------


def _gauge(w, name, **labels):
    return w.gauges.values.get((name, tuple(sorted(labels.items()))))


def _only(schema, error, original):
    """A side effect that raises ``error`` for one schema and runs ``original`` otherwise."""

    def run(cursor, s, *args, **kwargs):
        if s == schema:
            raise error
        return original(cursor, s, *args, **kwargs)

    return run


class _BrokenStore(FakeStore):
    """A configured fingerprint store that cannot be read (an outage, a wrong reader key)."""

    def versions(self, under):
        msg = "the storage is down"
        raise OSError(msg)


class ReviewWatchTests(WatchCase):
    def watch(self, d, store=None, **cfg):
        return watch.Watch(watch.Config(root=Path(d), **cfg), store=store)

    def lines(self):
        found = []
        return found, patch.object(watch, "_emit", found.append)

    def test_s1_one_unreadable_hospital_never_hides_a_switched_off_trigger(self):
        """S1/C2: the fast pass publishes the disabled-trigger count before any per-hospital
        query, and a hospital that raises is counted unchecked (and named from its second
        pass) while the pass goes on."""
        found, emit = self.lines()
        locked = OperationalError("canceling statement due to lock timeout")
        with tempfile.TemporaryDirectory() as d, emit:
            w = self.watch(d)
            with (
                patch.object(
                    incremental, "disabled_triggers", return_value={TEST_SCHEMA: 1}
                ),
                patch.object(
                    incremental,
                    "unsealed",
                    side_effect=_only(TEST_SCHEMA, locked, incremental.unsealed),
                ),
            ):
                first = w.run("fast")
                second = w.run("fast")
        self.assertEqual((first["ok"], second["ok"]), (1, 1))
        self.assertEqual(_gauge(w, "ihr_audit_disabled_trigger_hospitals"), 1)
        self.assertEqual(
            _gauge(w, "ihr_audit_hospitals_unchecked", **{"pass": "fast"}), 1
        )
        slug = self.hospital.slug
        self.assertIn(
            f"ihr-audit: hospital={slug} check=triggers outcome=failed", found
        )
        self.assertEqual(
            found.count(f"ihr-audit: hospital={slug} check=unchecked outcome=failed"), 1
        )

    def test_s1_an_incremental_pass_goes_on_past_a_hospital_that_raises(self):
        _found, emit = self.lines()
        original = incremental.check_from_checkpoint

        def check(cursor, schema, *args, **kwargs):
            if schema == TEST_SCHEMA:
                msg = "permission denied for table ihr_audit_seal"
                raise DatabaseError(msg)
            return original(cursor, schema, *args, **kwargs)

        with tempfile.TemporaryDirectory() as d, emit:
            w = self.watch(d)
            with patch.object(incremental, "check_from_checkpoint", side_effect=check):
                result = w.run("incremental")
        self.assertEqual(result["ok"], 1)
        self.assertEqual(result["unchecked"], 1)
        self.assertEqual(
            _gauge(w, "ihr_audit_hospitals_unchecked", **{"pass": "incremental"}), 1
        )
        self.assertIsNotNone(_gauge(w, "ihr_audit_chain_broken_hospitals"))

    def test_c8_only_a_pass_that_ends_without_error_is_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            w = self.watch(d)
            w.run("fast")
            ok_at = _gauge(
                w, "ihr_audit_watch_last_success_timestamp_seconds", **{"pass": "fast"}
            )
            self.assertIsNotNone(ok_at)
            with patch.object(watch, "_hospitals", side_effect=DatabaseError("gone")):
                self.assertEqual(w.run("fast")["ok"], 0)
            self.assertEqual(
                _gauge(
                    w,
                    "ihr_audit_watch_last_success_timestamp_seconds",
                    **{"pass": "fast"},
                ),
                ok_at,
            )
            self.assertGreaterEqual(
                _gauge(
                    w, "ihr_audit_watch_last_run_timestamp_seconds", **{"pass": "fast"}
                ),
                ok_at,
            )
            # a sweep that failed is tried again at the next nightly, not a week later
            self.assertEqual(w.run("sweep")["ok"], 0)  # no reader here: it fails
            self.assertTrue(w._sweep_due())
            # and a failed nightly is tried again within the hour
            w.state.data["passes"]["nightly"] = {
                "at": time.time(),
                "ok": 0,
                "seconds": 1,
            }
            self.assertLessEqual(w._nightly_wait(), 3600)

    def test_s2_a_hospital_set_back_to_provisioning_is_still_judged(self):
        found, emit = self.lines()
        with tempfile.TemporaryDirectory() as d, emit:
            w = self.watch(d)
            w.run("incremental")
            self.assertIsNotNone(w.checkpoints.get(self.uuid))
            with as_owner():
                Hospital.objects.filter(pk=self.hospital.pk).update(
                    status=Hospital.Status.PROVISIONING
                )
            try:
                w.run("incremental")
            finally:
                with as_owner():
                    Hospital.objects.filter(pk=self.hospital.pk).update(
                        status=Hospital.Status.ACTIVE
                    )
        self.assertGreaterEqual(_gauge(w, "ihr_audit_chain_broken_hospitals"), 1)
        self.assertIn(
            f"ihr-audit: hospital={self.hospital.slug} check=chain outcome=failed",
            found,
        )

    def test_s2_a_restore_hold_keeps_standing_findings_and_is_counted(self):
        from ihr_platform.models import HospitalSchemaState

        with tempfile.TemporaryDirectory() as d:
            w = self.watch(d)
            w.state.data["rehash_problems"][self.uuid] = ["seal 1: entries changed"]
            state = HospitalSchemaState.objects.filter(hospital=self.hospital)
            before = list(state.values_list("upgrading_since", "hold_reason"))
            with as_owner():
                state.update(upgrading_since=datetime.now(UTC), hold_reason="restore")
            try:
                w.run("incremental")
            finally:
                with as_owner():
                    state.update(upgrading_since=before[0][0], hold_reason=before[0][1])
        self.assertEqual(_gauge(w, "ihr_audit_hospitals_restoring"), 1)
        self.assertGreaterEqual(_gauge(w, "ihr_audit_chain_broken_hospitals"), 1)

    def test_s3_a_back_dated_sealed_at_cannot_shrink_the_nightly_horizon(self):
        """S3: the owner back-dates sealed_at of every seal up to an edited one; the nightly
        horizon comes from the watch's own record of the head 30 days ago."""
        with tempfile.TemporaryDirectory() as d:
            w = self.watch(d)
            w.run("incremental")  # records today's head
            heads = w.state.data["heads"][self.uuid]["days"]
            self.assertTrue(heads)
            old = (datetime.now(UTC) - timedelta(days=40)).date().isoformat()
            heads[old] = 0  # the watch saw no seal 40 days ago
            with connection.cursor() as cursor:
                head = self.head(cursor)[0]
            with tampering() as cursor:
                cursor.execute(
                    "UPDATE ihr_audit_seal SET sealed_at = now() - interval '60 days'"
                )
                cursor.execute(
                    "UPDATE ihr_audit_entry SET actor = 'someone-else' WHERE id = "
                    "(SELECT max(e.id) FROM ihr_audit_entry e JOIN ihr_audit_seal s "
                    " ON e.txid >= s.lower_txid AND e.txid < s.upper_txid WHERE s.seq = %s)",
                    [head - 1],
                )
                summary = w._rehash()
                problems = w.state.data["rehash_problems"].get(self.uuid, [])
        self.assertTrue(summary["complete"])
        self.assertTrue(
            any(f"seal {head - 1}: entries changed" in p for p in problems), problems
        )

    def test_s4_restore_generation_errors_are_bad_events(self):
        store = FakeStore()
        with connection.cursor() as cursor:
            seq, digest, _ = EventTests.head(self, cursor)
        rows = EventTests._segment(self, seq, digest)
        for _ in range(2):  # two events both claiming generation 1
            events.write(
                events.restore_document(self.hospital, 1, (seq, digest), rows, "t"),
                store,
            )
        found = events.read(store)
        self.assertTrue(any("expected 2" in p for p in found.problems), found.problems)

    def test_s4_an_offboarding_event_closes_only_what_the_watch_saw_closing(self):
        gone = "0f0f0f0f-0000-4000-8000-000000000002"
        closing = type("H", (), {"uuid": gone, "slug": "goneaway"})()
        final = (7, "ab" * 32)
        store = FakeStore()
        events.write(
            events.offboarding_document(closing, final, "tester", "cert-2"), store
        )
        events.write(
            events.offboarding_document(self.hospital, final, "tester", "cert-3"), store
        )
        found = events.read(store)
        every = watch._every_hospital()
        with tempfile.TemporaryDirectory() as d:
            w = self.watch(d)
            closed, refused = w.judge_offboarding(found, None, every)
            self.assertEqual(closed, set())
            text = " ".join(refused)
            self.assertIn("still active", text)  # the live hospital
            self.assertIn("never saw it offboarding", text)  # the gone one
            # seen archived, and its final seal is the newest fingerprint's: accepted
            w.state.data["seen"][gone] = {"slug": "goneaway", "status": "archived"}
            w.state.data["sweep_claims"][gone] = [*final, datetime.now(UTC).isoformat()]
            closed, refused = w.judge_offboarding(found, None, every)
            self.assertEqual(closed, {gone})
            self.assertEqual(len(refused), 1)
            # a final seal other than the fingerprints': refused
            w.state.data["sweep_claims"][gone] = [
                8,
                "cd" * 32,
                datetime.now(UTC).isoformat(),
            ]
            self.assertEqual(w.judge_offboarding(found, None, every)[0], set())

    def test_s4_each_new_event_is_named_once(self):
        found, emit = self.lines()
        store = FakeStore()
        anchor.write(store)
        events.write(
            events.offboarding_document(self.hospital, (1, "aa" * 32), "tester", "c9"),
            store,
        )
        with tempfile.TemporaryDirectory() as d, emit:
            w = self.watch(d, store=store)
            w.run("incremental")
            w.run("incremental")
        line = watch.event_line(self.hospital.slug, "offboarding")
        self.assertEqual(found.count(line), 1)
        self.assertGreaterEqual(
            _gauge(w, "ihr_audit_fingerprint_store_problems", **{"class": "events"}), 1
        )

    def test_s5_no_checkpoint_is_seeded_while_the_fingerprints_are_unreadable(self):
        with tempfile.TemporaryDirectory() as d:
            w = self.watch(d, store=_BrokenStore())
            w.run("incremental")
            self.assertIsNone(w.checkpoints.get(self.uuid))
            self.assertGreaterEqual(
                _gauge(w, "ihr_audit_checkpoints_unseeded_hospitals"), 1
            )
            self.assertEqual(_gauge(w, "ihr_audit_fingerprints_readable"), 0)

    def test_c6_a_claim_from_the_last_sweep_seeds_and_a_long_catch_up_is_bounded(self):
        with connection.cursor() as cursor:
            seq, digest, _ = self.head(cursor)
        _sealed(2)
        with tempfile.TemporaryDirectory() as d:
            w = self.watch(d, store=FakeStore())  # readable, but no recent fingerprint
            w.state.data["sweep_claims"][self.uuid] = [
                seq,
                digest,
                datetime.now(UTC).isoformat(),
            ]
            self.assertEqual(w._claim(self.uuid, None)[:2], (seq, digest))
        with connection.cursor() as cursor:
            result = self.check(cursor, None, (seq, digest, datetime.now(UTC)))
            self.assertEqual(result.seeded, "fingerprint")
            first = self.check(cursor, None, None)
            bounded = incremental.check_from_checkpoint(
                cursor, TEST_SCHEMA, self.uuid, None, None, self.events, max_entries=1
            )
            self.assertTrue(bounded.partial)
            self.assertLess(bounded.checkpoint.seq, first.checkpoint.seq)
            cp = bounded.checkpoint
            for _ in range(200):
                step = incremental.check_from_checkpoint(
                    cursor, TEST_SCHEMA, self.uuid, cp, None, self.events, max_entries=1
                )
                self.assertEqual(step.problems, [])
                cp = step.checkpoint or cp
                if not step.partial:
                    break
            self.assertEqual(cp.seq, first.checkpoint.seq)
        with patch.object(incremental, "SEAL_BATCH", 2):  # rows read in keyset batches
            with connection.cursor() as cursor:
                again = self.check(cursor, None)
            self.assertEqual(again.problems, [])
            self.assertEqual(again.checkpoint.seq, first.checkpoint.seq)

    def test_s6_a_slug_off_its_pattern_is_still_named(self):
        found, emit = self.lines()
        with tempfile.TemporaryDirectory() as d, emit:
            self.watch(d)._lines(["Bad_Slug", "x\nihr-audit: hospital=alpha"], "chain")
        self.assertEqual(
            found, ["ihr-audit: hospital=invalid-slug check=chain outcome=failed"]
        )

    def test_s7_the_metrics_server_answers_only_its_scrapers_and_a_few_at_once(self):
        import socket
        import threading
        import urllib.request

        gauges = watch.Gauges()
        gauges.set("ihr_audit_chain_broken_hospitals", 0)
        server = watch.metrics_server(gauges, 0, host="127.0.0.1")
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.assertFalse(server.verify_request(None, ("10.9.9.9", 1)))
        self.assertTrue(server.verify_request(None, ("127.0.0.1", 1)))
        held = []
        try:
            base = threading.active_count()
            for _ in range(20):  # idle connections from an address not allowed
                s = socket.socket()
                s.bind(("127.0.0.2", 0))
                s.connect(("127.0.0.1", port))
                held.append(s)
            time.sleep(0.5)
            self.assertLessEqual(threading.active_count(), base + 1)
            for _ in range(12):  # idle connections from an allowed one: a few at a time
                s = socket.create_connection(("127.0.0.1", port))
                held.append(s)
            time.sleep(0.5)
            # each request: its handler and its deadline timer
            self.assertLessEqual(
                threading.active_count(), base + 2 * watch.MAX_REQUESTS + 1
            )
            for s in held:
                s.close()
            held = []
            time.sleep(0.3)
            body = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/metrics", timeout=5
            ).read()
            self.assertIn(b"ihr_audit_chain_broken_hospitals 0", body)
            # a request that trickles is cut off after REQUEST_SECONDS in all
            with patch.object(watch, "REQUEST_SECONDS", 1):
                s = socket.create_connection(("127.0.0.1", port))
                s.settimeout(4)
                s.sendall(b"GET /met")
                began = time.monotonic()
                self.assertEqual(s.recv(100), b"")
                self.assertLess(time.monotonic() - began, 3)
                s.close()
        finally:
            for s in held:
                s.close()
            server.shutdown()
            server.server_close()

    def test_c3_a_start_dates_a_wait_no_earlier_than_one_sealing_cycle(self):
        found, emit = self.lines()
        now = datetime.now(UTC)
        waiting = incremental.Unsealed(
            head_seq=3,
            head_sealed_at=now - timedelta(minutes=30),
            sealable=4,
            sealable_oldest=now - timedelta(minutes=20),
        )
        original = incremental.unsealed

        def unsealed(cursor, schema):
            return waiting if schema == TEST_SCHEMA else original(cursor, schema)

        with tempfile.TemporaryDirectory() as d, emit:
            w = self.watch(d)  # just started
            with patch.object(incremental, "unsealed", side_effect=unsealed):
                w.run("fast")
        slug = self.hospital.slug
        self.assertNotIn(f"ihr-audit: hospital={slug} check=seal outcome=failed", found)
        self.assertLess(
            _gauge(w, "ihr_audit_seal_lag_seconds"), watch.SEAL_LINE_SECONDS
        )

    def test_c5_which_limit_ended_the_night_is_exported(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as e:
            budget = self.watch(d, rehash_budget=1)
            budget._rehash()
            self.assertEqual(_gauge(budget, "ihr_audit_rehash_stopped_by"), 1)
            timed = self.watch(e, rehash_max_seconds=-1)
            timed._rehash()
            self.assertEqual(_gauge(timed, "ihr_audit_rehash_stopped_by"), 2)
            self.assertIsNotNone(
                _gauge(budget, "ihr_audit_rehash_rate_entries_per_second")
            )

    def test_c7_valid_json_of_another_shape_is_one_bad_object(self):
        now = datetime.now(UTC)
        fp = json.dumps(
            {
                "format": anchor.FORMAT,
                "platform": anchor.prefix(),
                "written_at": now.isoformat(),
                "hospitals": [],
            }
        ).encode()
        bodies = [b"[]", b'"x"', b"null", fp, b"[" * 100000]
        for prefix_, read in (
            (f"{anchor.prefix()}{now:%Y/%m/%d}/", lambda st: anchor.load_all(st)),
            (f"{events.prefix()}{now:%Y/%m/%d}/", lambda st: events.read(st)),
        ):
            store = FakeStore()
            for i, body in enumerate(bodies):
                key = f"{prefix_}bad-{i}.json"
                store.objects[key] = [("v0", body)]
                store.received[key] = now
            found = read(store)  # never raises
            if isinstance(found, anchor.Fingerprints):
                self.assertEqual(found.classes["unreadable"], len(bodies))
                keys = {key for _cls, key, _text in found.items}
                counts = watch.store_counts(found, dict.fromkeys(keys, "reviewed"))
                self.assertEqual(counts["unreadable"], 0)  # each can be acknowledged
            else:
                self.assertTrue(found.read_ok)
                self.assertEqual(len(found.problems), len(bodies))

    def test_c9_a_hospital_newer_than_the_store_is_not_lost_and_a_reseed_counts_once(
        self,
    ):
        store = FakeStore()
        anchor.write(store)
        with tempfile.TemporaryDirectory() as d:
            w = self.watch(d, store=store)
            created = self.hospital.created_on - timedelta(days=1)
            (Path(d) / "created").write_text(created.isoformat())
            w.run(
                "incremental"
            )  # seeded from its fingerprint, but newer than the store
            self.assertEqual(w.checkpoints.get(self.uuid).seeded, "fingerprint")
            self.assertNotIn(self.uuid, w.lost_counted)
        with tempfile.TemporaryDirectory() as d:
            w = self.watch(d, store=store)
            # a store made after the hospital: its missing checkpoint was lost
            created = self.hospital.created_on + timedelta(days=1)
            (Path(d) / "created").write_text(created.isoformat())
            failing = incremental.Result(problems=["seal 9: missing"], lost=True)
            with patch.object(
                incremental, "check_from_checkpoint", return_value=failing
            ):
                w.run("incremental")
                first = _gauge(w, "ihr_audit_checkpoints_reseeded_hospitals")
                w.run("incremental")
                second = _gauge(w, "ihr_audit_checkpoints_reseeded_hospitals")
        self.assertGreaterEqual(first, 1)
        self.assertEqual(second, 0)

    def test_c12_once_never_writes_the_serving_watchs_state_and_measure_is_bounded(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            state.write_text('{"passes": {}, "marker": "the serving watch"}')
            with (
                patch.dict("os.environ", {"IHR_AUDIT_WATCH_DIR": d}),
                contextlib.suppress(SystemExit),
            ):
                call_command(
                    "ihr_audit_watch", "once", "--pass", "fast", stdout=StringIO()
                )
            self.assertEqual(
                json.loads(state.read_text())["marker"], "the serving watch"
            )
            self.assertEqual(json.loads(state.read_text())["passes"], {})
        seen = []
        original = incremental.recompute

        def recompute(cursor, schema, first, last, max_entries=None, deadline=None):
            seen.append(max_entries)
            return original(cursor, schema, first, last, max_entries, deadline)

        with patch.object(incremental, "recompute", side_effect=recompute):
            call_command("ihr_audit_watch", "measure", "--json", stdout=StringIO())
        self.assertTrue(seen)
        self.assertTrue(all(m == 200_000 for m in seen), seen)
        with self.assertRaises(CommandError):
            call_command("ihr_audit_watch", "measure", "--full", stdout=StringIO())

    def test_c14_an_accepted_offboarding_excuses_only_a_schema_that_is_gone(self):
        found, emit = self.lines()
        store = FakeStore()
        key = events.write(
            events.offboarding_document(self.hospital, (1, "aa" * 32), "t", "c1"), store
        )
        trail = watch._has_trail

        def no_trail(cursor, schema):
            return False if schema == TEST_SCHEMA else trail(cursor, schema)

        with (
            tempfile.TemporaryDirectory() as d,
            emit,
            patch.object(watch, "acknowledged", return_value={key: "reviewed"}),
        ):
            w = self.watch(d, store=store)
            with patch.object(watch, "_has_trail", side_effect=no_trail):
                w.run("incremental")
            self.assertEqual(_gauge(w, "ihr_audit_hospitals_closed"), 1)
            self.assertNotIn(
                f"ihr-audit: hospital={self.hospital.slug} check=chain outcome=failed",
                found,
            )
            # closed, but its trail is still there: every check still runs
            w.run("incremental")
            cp = w.checkpoints.get(self.uuid)
            w.checkpoints.put(
                Checkpoint(cp.hospital, 0, cp.seq, "ab" * 32, cp.upper, cp.at)
            )
            w.run("incremental")
        self.assertIn(
            f"ihr-audit: hospital={self.hospital.slug} check=chain outcome=failed",
            found,
        )


# --- P1-T21 second review fixes (each test fails without its fix) -----------------------------


class SecondReviewTests(WatchCase):
    GONE = "0f0f0f0f-0000-4000-8000-000000000003"
    FINAL = (7, "ab" * 32)
    watch = ReviewWatchTests.watch
    lines = ReviewWatchTests.lines

    def _gone_event(self, store, when=None):
        closing = type("H", (), {"uuid": self.GONE, "slug": "goneaway"})()
        doc = events.offboarding_document(closing, self.FINAL, "tester", "cert-4", when)
        return events.write(doc, store)

    def test_an_accepted_offboarding_stays_accepted_once_its_fingerprints_stop(self):
        """A real offboarding: accepted while a recent fingerprint names the hospital; a day
        or two later the hourly fingerprints no longer list it, and the last sweep's claim is
        older. The acceptance stands (no false AuditEventInvalid for up to a week) until a
        newer claim contradicts it, which withdraws it."""
        store = FakeStore()
        key = self._gone_event(store)
        found = events.read(store)
        every = watch._every_hospital()
        with tempfile.TemporaryDirectory() as d:
            w = self.watch(d)
            w.state.data["seen"][self.GONE] = {"slug": "goneaway", "status": "archived"}
            recent = anchor.Fingerprints()
            recent.newest_claims[self.GONE] = (*self.FINAL, datetime.now(UTC))
            self.assertEqual(w.judge_offboarding(found, recent, every)[0], {self.GONE})
            # days later: no recent fingerprint names it; the last sweep's claim is older
            w.state.data["sweep_claims"][self.GONE] = [
                5,
                "cd" * 32,
                (datetime.now(UTC) - timedelta(days=3)).isoformat(),
            ]
            closed, refused = w.judge_offboarding(found, anchor.Fingerprints(), every)
            self.assertEqual(closed, {self.GONE}, refused)
            self.assertEqual(
                [r for r in refused if self.GONE in r or key in r], [], refused
            )
            # a claim after its final seal: refused, and the acceptance is withdrawn
            w.state.data["sweep_claims"][self.GONE] = [
                8,
                "ef" * 32,
                datetime.now(UTC).isoformat(),
            ]
            self.assertEqual(
                w.judge_offboarding(found, anchor.Fingerprints(), every)[0], set()
            )
            self.assertNotIn(key, w.state.data["offboarding_accepted"])
            w.state.data["sweep_claims"][self.GONE] = [
                5,
                "cd" * 32,
                datetime.now(UTC).isoformat(),
            ]
            self.assertEqual(
                w.judge_offboarding(found, anchor.Fingerprints(), every)[0], set()
            )

    def test_an_offboarding_the_watch_missed_is_judged_from_the_days_around_it(self):
        """The watch was down while the closed hospital was still fingerprinted: the
        fingerprints since the day before the event decide, not a week-old sweep claim."""
        store = FakeStore()
        when = datetime.now(UTC)
        fake = json.dumps(
            {
                "format": anchor.FORMAT,
                "platform": anchor.prefix(),
                "written_at": (when - timedelta(minutes=5)).isoformat(),
                "hospitals": {
                    self.GONE: {
                        "slug": "goneaway",
                        "seq": self.FINAL[0],
                        "hash": self.FINAL[1],
                        "sealed_at": None,
                    }
                },
            }
        ).encode()
        store.put(f"{anchor.prefix()}{when:%Y/%m/%d}/final.json", fake)
        self._gone_event(store, when)
        found = events.read(store)
        every = watch._every_hospital()
        with tempfile.TemporaryDirectory() as d:
            w = self.watch(d, store=store)
            w.state.data["seen"][self.GONE] = {"slug": "goneaway", "status": "archived"}
            w.state.data["sweep_claims"][self.GONE] = [
                5,
                "cd" * 32,
                (when - timedelta(days=6)).isoformat(),
            ]
            closed, refused = w.judge_offboarding(found, anchor.Fingerprints(), every)
        self.assertEqual(closed, {self.GONE}, refused)

    def test_an_event_key_never_forges_a_line_and_every_record_is_one_line(self):
        """A writer-key holder names an event object with a line break and the watch's fixed
        line: the object is not an event of ours, and nothing the watch logs or emits holds
        a line break or that line."""
        store = FakeStore()
        forged = "ihr-audit: hospital=alpha check=chain outcome=failed"
        doc = events.offboarding_document(self.hospital, (1, "aa" * 32), "tester", "c5")
        tail = (
            f"{datetime.now(UTC):%Y/%m/%d/%Y%m%dT%H%M%S}Z-offboarding-{self.uuid}"
            "-abcd1234.json"
        )
        store.put(f"{events.prefix()}{tail}\n{forged}\n", json.dumps(doc).encode())
        store.put(  # a good body under a name the writer never gives
            f"{events.prefix()}2026/11/14/offboarding-{self.uuid}.json",
            json.dumps(doc).encode(),
        )
        found = events.read(store)
        self.assertEqual(found.offboarded, {})
        self.assertEqual(len(found.problems), 2, found.problems)
        self.assertTrue(all("\n" not in p for p in found.problems), found.problems)
        lines, emit = self.lines()
        with (
            tempfile.TemporaryDirectory() as d,
            emit,
            self.assertLogs("ihr_audit.watch", "INFO") as logs,
        ):
            self.watch(d, store=store).run("incremental")
        self.assertNotIn(forged, lines)
        self.assertTrue(all("\n" not in m for m in logs.output), logs.output)
        # and whatever reaches a record, the watch's formatter keeps it on one line
        from ihr_audit.oneline import OneLineFormatter

        record = logging.LogRecord(
            "ihr_audit.watch",
            logging.ERROR,
            __file__,
            1,
            "x %s",
            (f"\n{forged}",),
            None,
        )
        try:
            msg = chr(0x2028) + forged + "\r\n"
            raise ValueError(msg)
        except ValueError:
            record.exc_info = sys.exc_info()
        text = OneLineFormatter("%(levelname)s %(message)s").format(record)
        self.assertEqual(len(text.splitlines()), 1, text)

    def test_the_watch_settings_log_one_record_per_line(self):
        source = (
            Path(watch.__file__).parent.parent
            / "ihr_platform"
            / "settings"
            / "watch.py"
        ).read_text()
        self.assertIn('"()": "ihr_audit.oneline.OneLineFormatter"', source)
        self.assertIn("for name, spec in LOGGING", source)

    def _held(self, status, since):
        """Hold the test hospital as a release does, with ``status``; undone afterwards."""
        from ihr_platform.models import HospitalSchemaState

        state = HospitalSchemaState.objects.filter(hospital=self.hospital)
        before = next(iter(state.values_list("upgrading_since", "hold_reason")))

        @contextlib.contextmanager
        def held():
            with as_owner():
                state.update(upgrading_since=since, hold_reason="migrate")
                Hospital.objects.filter(pk=self.hospital.pk).update(status=status)
            try:
                yield
            finally:
                with as_owner():
                    state.update(upgrading_since=before[0], hold_reason=before[1])
                    Hospital.objects.filter(pk=self.hospital.pk).update(
                        status=Hospital.Status.ACTIVE
                    )

        return held()

    def test_a_release_hold_defers_a_locked_hospital_within_its_grace_only(self):
        """C3 residual: a suspended hospital a release holds and migrates (its sealers are
        stopped, a migration locks its trail) is neither unchecked nor late while the hold
        is within its grace; past the grace (by the watch's own first sighting, whatever the
        hold's re-dated start) it is judged again."""
        lines, emit = self.lines()
        locked = OperationalError("canceling statement due to lock timeout")
        slug = self.hospital.slug
        now = datetime.now(UTC)
        with (
            tempfile.TemporaryDirectory() as d,
            emit,
            self._held(Hospital.Status.SUSPENDED, now),
            patch.object(
                incremental,
                "unsealed",
                side_effect=_only(TEST_SCHEMA, locked, incremental.unsealed),
            ),
            patch.object(
                incremental,
                "check_from_checkpoint",
                side_effect=_only(
                    TEST_SCHEMA, locked, incremental.check_from_checkpoint
                ),
            ),
        ):
            w = self.watch(d)
            for _ in range(2):
                w.run("fast")
            inc = w.run("incremental")
            self.assertEqual(
                _gauge(w, "ihr_audit_hospitals_unchecked", **{"pass": "fast"}), 0
            )
            self.assertEqual(inc["unchecked"], 0)
            self.assertEqual(inc["deferred"], 1)
            self.assertGreaterEqual(_gauge(w, "ihr_audit_hospitals_held"), 1)
            self.assertNotIn(
                f"ihr-audit: hospital={slug} check=unchecked outcome=failed", lines
            )
            # the watch saw it held three hours ago: a hold re-dated to now changes nothing
            w.state.data["held_seen"][self.uuid] = (
                now - timedelta(hours=3)
            ).isoformat()
            for _ in range(2):
                w.run("fast")
            self.assertEqual(w.run("incremental")["unchecked"], 1)
        self.assertEqual(
            _gauge(w, "ihr_audit_hospitals_unchecked", **{"pass": "fast"}), 1
        )
        self.assertIn(
            f"ihr-audit: hospital={slug} check=unchecked outcome=failed", lines
        )

    def test_a_failed_events_read_never_resets_the_head_record_or_breaks_a_chain(self):
        """S3 follow-up: an events outage (generation 0) or an extra restore event must not
        wipe the watch's 30-day record, nor read as a lost restore event."""
        with connection.cursor() as cursor:
            seq, digest, upper = self.head(cursor)
        old = (datetime.now(UTC) - timedelta(days=20)).date().isoformat()
        with tempfile.TemporaryDirectory() as d:
            w = self.watch(d, store=_BrokenStore())
            w.state.data["heads"][self.uuid] = {"generation": 1, "days": {old: 1}}
            w.checkpoints.put(
                Checkpoint(
                    self.uuid, 1, seq, digest, upper, datetime.now(UTC).isoformat()
                )
            )
            w.run("incremental")
            self.assertEqual(w.state.data["heads"][self.uuid]["days"], {old: 1})
            self.assertEqual(_gauge(w, "ihr_audit_chain_broken_hospitals"), 0)
            # a newer generation (a real or forged restore event) keeps the days too
            w._record_head(self.uuid, 2, seq, datetime.now(UTC).date().isoformat())
            self.assertEqual(w.state.data["heads"][self.uuid]["days"][old], 1)
        with connection.cursor() as cursor:
            result = self.check(
                cursor,
                Checkpoint(self.uuid, 1, seq, digest, upper, "2026-11-14T00:00"),
                found_events=events.Events(read_ok=False),
            )
        self.assertEqual(result.problems, [])
