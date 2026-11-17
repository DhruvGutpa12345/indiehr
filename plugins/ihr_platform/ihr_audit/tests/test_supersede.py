"""A whole-database restore and the audit chain (P1-T22c1; docs/reviews/p1-t22c/design.md,
"After the attack" wins).

The trail is append-only and tests never clean it up: the live tail is sealed for real (it
stays), and the restore itself (the hospital's trail cut back to T, as restore_live leaves it)
and everything the load writes run as the owner inside a transaction that is rolled back.
"""

import contextlib
import io
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from django.db import connection, transaction
from ihr_platform.models import Hospital
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_platform.testing import as_owner

from ihr_audit import anchor, archive, events, supersede, watch

from .test_anchor import FakeStore
from .test_audit_trail import AuditCase
from .test_watch import _sealed

POINT = "release-0123456789ab-1700000000"
IMAGE = "sha256:" + "ab" * 32


def _next(timeline, step=1):
    return f"{int(timeline, 16) + step:08X}"


class SupersedeCase(AuditCase):
    """A hospital whose release fingerprinted its sealed head at T (the lower bound L), then
    served on: one more seal, fingerprinted after T. (Few seals: the trail is append-only and
    every test's seals stay in the test schema's chain.)"""

    def setUp(self):
        super().setUp()
        self.hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
        self.uuid = str(self.hospital.uuid)
        self.store = FakeStore()
        self.rid = supersede.new_id()
        self.base = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=30)
        self.old = events.current_timeline()
        self.new = _next(self.old)
        _sealed(1)
        anchor.write(self.store, now=self.base)  # the release's fingerprint of the head
        with connection.cursor() as cursor:
            self.lower = supersede.head(cursor, TEST_SCHEMA)
        self.point = self.base + timedelta(minutes=1)  # the restore point T
        _sealed(1)
        anchor.write(self.store, now=self.base + timedelta(minutes=5))
        with connection.cursor() as cursor:
            self.top = supersede.head(cursor, TEST_SCHEMA)

    @contextlib.contextmanager
    def world(self):
        """Everything after the live phase, as the owner, rolled back at the end."""
        with as_owner(), transaction.atomic():
            try:
                yield
            finally:
                transaction.set_rollback(True)

    def export(self):
        out = io.BytesIO()
        draft = supersede.export(
            out, self.rid, POINT, self.point, IMAGE, "tester", store=self.store
        )
        out.seek(0)
        self.members = supersede.read_tar(out)
        return draft

    def plan(self):
        return supersede.write_plan(
            self.members, self.rid, store=self.store, reader=self.store
        )

    def restore(self, to_seq=None):
        """What restore_live leaves of this hospital's trail: seals up to T's head (or
        ``to_seq``), and no entry after it."""
        to_seq = self.lower[0] if to_seq is None else to_seq
        with connection.cursor() as cursor:
            for table in ("ihr_audit_entry", "ihr_audit_seal"):
                cursor.execute(
                    f"ALTER TABLE {table} DISABLE TRIGGER ihr_audit_append_only"
                )
            cursor.execute(
                "SELECT coalesce((SELECT upper_txid FROM ihr_audit_seal WHERE seq = %s), "
                "'0'::xid8)::text",
                [to_seq],
            )
            upper = cursor.fetchone()[0]
            cursor.execute("DELETE FROM ihr_audit_seal WHERE seq > %s", [to_seq])
            cursor.execute(
                "DELETE FROM ihr_audit_entry WHERE txid >= %s::xid8", [upper]
            )
            for table in ("ihr_audit_entry", "ihr_audit_seal"):
                cursor.execute(
                    f"ALTER TABLE {table} ENABLE TRIGGER ihr_audit_append_only"
                )

    def load(self):
        with patch.object(events, "current_timeline", return_value=self.new):
            return supersede.load(
                self.members, self.rid, "tester", store=self.store, reader=self.store
            )

    def read(self, timeline=None, history=None):
        return events.read(self.store, timeline=timeline or self.new, history=history)

    def verdict(self, found):
        fps = anchor.load_all(self.store, excuse=found.excuse)
        return anchor.check(self.uuid, fps)

    def full(self):
        """Export, plan, restore, load."""
        self.export()
        self.plan()
        self.restore()
        return self.load()


class LowerBound(SupersedeCase):
    def test_the_lower_bound_is_the_newest_fingerprint_at_the_restore_point(self):
        with self.world():
            draft = self.export()
        mine = draft["hospitals"][self.uuid]
        self.assertEqual(mine["outcome"], "exported")
        self.assertEqual((mine["lower"]["seq"], mine["lower"]["hash"]), self.lower[:2])
        self.assertEqual((mine["head"]["seq"], mine["head"]["hash"]), self.top[:2])
        self.assertTrue(mine["lower"]["fingerprint"].startswith(anchor.prefix()))
        # one fingerprint later than T would have been the wrong one: it is never taken
        _key, claims = anchor.claims_at(self.store, self.point)
        self.assertEqual(claims[self.uuid][:2], self.lower[:2])
        self.assertIsNone(
            anchor.claims_at(self.store, self.base - timedelta(days=4))[0]
        )

    def test_a_hospital_no_fingerprint_names_at_t_is_absent_and_refused_if_still_there(
        self,
    ):
        empty = FakeStore()
        doc = {
            "format": anchor.FORMAT,
            "platform": anchor.prefix(),
            "written_at": self.base.isoformat(),
            "hospitals": {},
        }
        empty.put(
            f"{anchor.prefix()}{self.base:%Y/%m/%d}/x.json", json.dumps(doc).encode()
        )
        self.store = empty
        with self.world():
            draft = self.export()
            self.assertEqual(draft["hospitals"][self.uuid]["outcome"], "absent")
            self.plan()
            with self.assertRaisesRegex(supersede.Refused, "no fingerprint at T"):
                self.load()


class Packages(SupersedeCase):
    def test_round_trip(self):
        with self.world():
            self.export()
        body = self.members[f"{self.uuid}.jsonl.gz"]
        pkg = supersede.Package.from_bytes(body)
        self.assertEqual(pkg.to_bytes(), body)
        self.assertEqual(
            [s["seq"] for s in pkg.tail()],
            list(range(self.lower[0] + 1, self.top[0] + 1)),
        )
        self.assertTrue(pkg.entries)

    def test_a_package_not_the_plans_is_refused(self):
        with self.world():
            self.export()
            name = f"{self.uuid}.jsonl.gz"
            body = bytearray(self.members[name])
            body[-1] ^= 1
            self.members[name] = bytes(body)
            with self.assertRaisesRegex(
                supersede.Refused, "not the one the export made"
            ):
                self.plan()
            self.assertEqual(self.store.keys(events.restores_prefix()), [])

    def test_a_changed_package_byte_after_the_plan_is_refused_by_the_load(self):
        with self.world():
            self.export()
            self.plan()
            self.restore()
            name = f"{self.uuid}.jsonl.gz"
            body = bytearray(self.members[name])
            body[-1] ^= 1
            self.members[name] = bytes(body)
            with self.assertRaisesRegex(
                supersede.Refused, "not the one the plan names"
            ):
                self.load()
        self.assertEqual(self.store.keys(events.prefix()), [])

    def test_a_plan_is_written_once(self):
        with self.world():
            self.export()
            key = self.plan()
            self.assertEqual(self.plan(), key)  # the same again is accepted
            draft = json.loads(self.members[supersede.PLAN_MEMBER])
            draft["operator"] = "someone-else"
            with self.assertRaises(events.WriteOnceError):
                events.write_restore_doc(
                    key, draft, store=self.store, reader=self.store
                )
            self.members[supersede.PLAN_MEMBER] = json.dumps(draft).encode()
            with self.assertRaisesRegex(supersede.Refused, "another plan"):
                self.plan()


class Load(SupersedeCase):
    def test_before_the_load_a_cut_after_it_excused_and_a_deleted_archive_row_fails_again(
        self,
    ):
        with self.world():
            self.export()
            self.plan()
            self.restore()
            self.assertTrue(
                any("cut" in p for p in self.verdict(self.read())), "a cut before"
            )
            outcomes = self.load()
            mine = outcomes[self.uuid]
            self.assertEqual(mine.outcome, "loaded")
            self.assertEqual(mine.fork, self.lower[0])
            found = self.read()
            self.assertEqual(self.verdict(found), [])
            self.assertGreater(found.excused_by[self.rid], 0)
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            self.assertEqual(event.problems, [])
            self.assertEqual(event.restored_to, self.lower[:2])
            # the archived original is the evidence: gone, the event excuses nothing
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE public.ihr_restore_superseded_entry "
                    "DISABLE TRIGGER ihr_restore_append_only"
                )
                cursor.execute(
                    "DELETE FROM public.ihr_restore_superseded_entry WHERE ctid = ("
                    "SELECT ctid FROM public.ihr_restore_superseded_entry "
                    "WHERE hospital = %s AND generation = %s LIMIT 1)",
                    [self.uuid, mine.generation],
                )
            self.assertTrue(self.verdict(self.read()))

    def test_fork_below_lower_is_refused(self):
        """S3: the restored chain must end exactly at L; a lower fork point would excuse seals
        the restored database should still hold."""
        with self.world():
            self.export()
            self.plan()
            self.restore(to_seq=self.lower[0] - 1)
            with self.assertRaisesRegex(supersede.Refused, "F = L"):
                self.load()
        self.assertEqual(self.store.keys(events.prefix()), [])

    def test_load_is_idempotent_and_a_crash_before_the_result_resumes(self):
        with self.world():
            self.export()
            self.plan()
            self.restore()
            with (
                patch.object(
                    events, "result_document", side_effect=RuntimeError("crash")
                ),
                self.assertRaises(RuntimeError),
            ):
                self.load()
            self.load()
            self.load()  # once more after the result: nothing new
            found = self.read()
            mine = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            self.assertEqual(len(mine), 1)
            self.assertEqual(len(self.store.keys(events.restores_prefix())), 2)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM ihr_audit_entry WHERE kind = 'restore' "
                    "AND data->>'restore_id' = %s",
                    [self.rid],
                )
                self.assertEqual(cursor.fetchone()[0], 1)
                cursor.execute(
                    "SELECT count(*) FROM public.ihr_restore_record WHERE operation = %s",
                    [self.rid],
                )
                self.assertEqual(cursor.fetchone()[0], 1)
            self.assertEqual(self.verdict(found), [])

    def test_storage_times_in_whole_seconds_accept_the_same_second(self):
        """Live proof finding: S3 stores whole seconds, and the load writes the event and the
        result within one: the same second counts as after, an earlier one never."""
        with self.world():
            self.full()
            found = self.read()
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            result = found.results[self.rid]
            for when, ok in (
                (event.stored, True),
                (event.stored - timedelta(seconds=1), False),
            ):
                result.stored = when
                event.problems = []
                events.check_whole(event, found, self.store)
                self.assertEqual(not event.problems, ok, event.problems)

    def test_the_database_must_have_been_restored(self):
        with self.world():
            self.export()
            self.plan()
            with (
                patch.object(events, "current_timeline", return_value=self.old),
                self.assertRaisesRegex(supersede.Refused, "was not restored"),
            ):
                supersede.load(
                    self.members,
                    self.rid,
                    "tester",
                    store=self.store,
                    reader=self.store,
                )


class Generations(SupersedeCase):
    def test_bucket_ahead_of_database(self):
        """After a whole-database restore the database can be behind the bucket: the next
        single-hospital restore takes the bucket's next generation, never one it has."""
        with self.world(), connection.cursor() as cursor:
            in_db = archive.generations_in_db(cursor, self.uuid)
            self.assertEqual(
                archive.next_generation(cursor, self.uuid, in_db + 2), in_db + 3
            )
            self.assertEqual(
                archive.next_generation(cursor, self.uuid, None), in_db + 1
            )

    def test_database_ahead_is_refused(self):
        with self.world():
            self.full()
            with connection.cursor() as cursor:
                in_db = archive.generations_in_db(cursor, self.uuid)
                with self.assertRaises(archive.GenerationError):
                    archive.next_generation(cursor, self.uuid, in_db - 1)

    def test_a_second_whole_restore_carries_the_first_ones_generation(self):
        """Flow e: a later whole-database restore back to a point before the first one's
        load brings that generation back from its package, and the first restore's event
        still excuses its claims, anchored through the chain of results (F4)."""
        newer = _next(self.new)
        with self.world():
            first = self.full()[self.uuid].generation
            first_rid, self.rid = self.rid, supersede.new_id()
            with patch.object(events, "current_timeline", return_value=self.new):
                draft = self.export()
                self.plan()
            self.assertEqual(draft["hospitals"][self.uuid]["generations"], [first])
            # the second restore: the database again at T, without the first one's archive
            with connection.cursor() as cursor:
                for table in (
                    "ihr_restore_superseded_entry",
                    "ihr_restore_superseded_seal",
                    "ihr_restore_record",
                ):
                    cursor.execute(
                        f"ALTER TABLE public.{table} DISABLE TRIGGER ihr_restore_append_only"
                    )
                    cursor.execute(
                        f"DELETE FROM public.{table} WHERE hospital = %s AND generation = %s",  # noqa: S608
                        [self.uuid, first],
                    )
            self.restore()
            self.new = newer
            outcomes = self.load()
            self.assertEqual(outcomes[self.uuid].outcome, "unchanged")
            with connection.cursor() as cursor:
                self.assertEqual(archive.generations_in_db(cursor, self.uuid), first)
                # the next single-hospital restore takes a generation the bucket never had
                found = self.read()
                self.assertEqual(
                    archive.next_generation(
                        cursor, self.uuid, found.generation(self.uuid)
                    ),
                    first + 1,
                )
            self.assertEqual(found.anchored, {first_rid, self.rid})
            self.assertEqual(self.verdict(found), [])
            self.assertGreater(found.excused_by[first_rid], 0)


class Rules(SupersedeCase):
    def test_event_without_its_timeline_excuses_nothing(self):
        """F4: the newest result's new timeline must be the database's now."""
        with self.world():
            self.full()
            elsewhere = _next(self.new, 5)
            found = self.read(timeline=elsewhere)
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            self.assertTrue(event.problems)
            self.assertIn("not the database's", event.problems[0])
            self.assertTrue(self.verdict(found))

    def test_an_event_whose_fork_is_not_the_plans_lower_bound_excuses_nothing(self):
        with self.world():
            self.full()
            plan = self.read().plans[self.rid]
            plan.hospitals[self.uuid]["lower"]["seq"] -= 1
            found = events.Events(plans={self.rid: plan})
            found.results = self.read().results
            found.timeline = self.new
            events.anchor_results(found)
            [event] = [
                e for e in self.read().restores[self.uuid] if e.restore_id == self.rid
            ]
            event.problems = []
            events.check_whole(event, found, self.store)
            self.assertTrue(any("F = L" in p for p in event.problems), event.problems)

    def test_a_history_that_saw_another_change_excuses_nothing(self):
        """S2: the watch saw the database move to the new timeline from another one."""
        with self.world():
            self.full()
            now = datetime.now(UTC)
            history = [
                {
                    "timeline": _next(self.old, 7),
                    "first": now.isoformat(),
                    "last": now.isoformat(),
                },
                {
                    "timeline": self.new,
                    "first": now.isoformat(),
                    "last": now.isoformat(),
                },
            ]
            found = self.read(history=history)
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            self.assertTrue(any("not from the plan's" in p for p in event.problems))
            good = [
                {
                    "timeline": self.old,
                    "first": (now - timedelta(hours=2)).isoformat(),
                    "last": (now - timedelta(hours=2)).isoformat(),
                },
                {
                    "timeline": self.new,
                    "first": (now + timedelta(minutes=1)).isoformat(),
                    "last": (now + timedelta(minutes=1)).isoformat(),
                },
            ]
            found = self.read(history=good)
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            self.assertEqual(event.problems, [])

    def test_compatibility_old_readers_see_an_ordinary_restore_event(self):
        """Builds before P1-T22c read only the fields they know: without cause and
        restore_id the document is a P1-T22b restore event, and its key the same shape."""
        with self.world():
            self.full()
            found = self.read()
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            doc = json.loads(self.store.get(event.key))
            self.assertEqual(doc["cause"], "whole-database")
            old = {k: v for k, v in doc.items() if k not in ("cause", "restore_id")}
            parsed = events.parse(event.key, events._body(old), event.stored)
            self.assertEqual(
                (parsed.generation, parsed.restored_to, parsed.superseded),
                (event.generation, event.restored_to, event.superseded),
            )
            self.assertEqual(parsed.cause, "")


class Chain(SupersedeCase):
    def _restore_docs(self, rid, old, new, at):
        plan = events.plan_document(rid, POINT, at, old, IMAGE, {}, "tester", now=at)
        events.write_restore_doc(
            events.plan_key(rid), plan, store=self.store, reader=self.store
        )
        result = events.result_document(
            rid, old, new, {}, "tester", now=at + timedelta(minutes=2)
        )
        events.write_restore_doc(
            events.result_key(rid), result, store=self.store, reader=self.store
        )

    def test_an_older_restore_is_accepted_only_through_the_chain(self):
        a, b, c = self.old, _next(self.old, 10), _next(self.old, 20)
        first, second = supersede.new_id(), supersede.new_id()
        self._restore_docs(first, a, b, self.base)
        self._restore_docs(second, b, c, self.base + timedelta(minutes=10))
        found = events.read(self.store, timeline=c)
        self.assertEqual(found.anchored, {first, second})
        # the second restore says it started from another timeline: the first is no longer
        # anchored through it (and the second, still the database's, is)
        broken = FakeStore()
        self.store = broken
        self._restore_docs(first, a, b, self.base)
        self._restore_docs(
            second, _next(self.old, 5), c, self.base + timedelta(minutes=10)
        )
        found = events.read(self.store, timeline=c)
        self.assertEqual(found.anchored, {second})
        self.assertIn("does not continue", found.unanchored[first])
        # and a timeline the database does not have anchors nothing
        found = events.read(self.store, timeline=_next(self.old, 30))
        self.assertEqual(found.anchored, set())

    def test_a_result_naming_its_plans_old_timeline_is_refused(self):
        rid = supersede.new_id()
        plan = events.plan_document(
            rid, POINT, self.base, self.old, IMAGE, {}, "tester", now=self.base
        )
        events.write_restore_doc(
            events.plan_key(rid), plan, store=self.store, reader=self.store
        )
        result = events.result_document(
            rid, self.old, self.old, {}, "tester", now=self.base + timedelta(minutes=1)
        )
        self.store.put(events.result_key(rid), events._body(result))
        found = events.read(self.store, timeline=self.old)
        self.assertNotIn(rid, found.results)
        self.assertTrue(any("do not move on" in p for p in found.problems))


class WatchTimeline(SupersedeCase):
    def test_timeline_change_without_a_result_is_unexplained_and_with_one_it_is_not(
        self,
    ):
        lines = []
        import tempfile
        from pathlib import Path

        with (
            tempfile.TemporaryDirectory() as d,
            patch.object(watch, "_emit", lines.append),
        ):
            w = watch.Watch(watch.Config(root=Path(d)), store=self.store)
            with patch.object(events, "current_timeline", return_value=self.old):
                w.run("fast")
            with patch.object(events, "current_timeline", return_value=self.new):
                w.run("incremental")
            self.assertEqual(w.gauges.values[("ihr_audit_timeline_unexplained", ())], 1)
            seen_old = datetime.fromisoformat(w.state.data["timelines"][0]["last"])
            rid = supersede.new_id()
            plan = events.plan_document(
                rid,
                POINT,
                self.base,
                self.old,
                IMAGE,
                {},
                "tester",
                now=seen_old - timedelta(minutes=5),
            )
            events.write_restore_doc(
                events.plan_key(rid), plan, store=self.store, reader=self.store
            )
            result = events.result_document(
                rid,
                self.old,
                self.new,
                {},
                "tester",
                now=seen_old + timedelta(seconds=1),
            )
            events.write_restore_doc(
                events.result_key(rid), result, store=self.store, reader=self.store
            )
            with patch.object(events, "current_timeline", return_value=self.new):
                w.run("incremental")
                w.run("incremental")
            self.assertEqual(w.gauges.values[("ihr_audit_timeline_unexplained", ())], 0)
        line = f"ihr-audit: restore={rid[:8]} event=whole-database hospitals=0 outcome=recorded"
        self.assertEqual(lines.count(line), 1)


class EventsKey(SupersedeCase):
    def _store(self, role, **kw):
        env = {
            f"IHR_AUDIT_ANCHOR_{role}_KEY": "k" * 20,
            f"IHR_AUDIT_ANCHOR_{role}_SECRET": "s" * 40,
            "IHR_AUDIT_ANCHOR_BUCKET": "fake",
            "IHR_AUDIT_ANCHOR_ENDPOINT": "http://127.0.0.1:9",
        }
        with patch.dict("os.environ", env):
            store = anchor.S3Store(role, **kw)
        store.client = MagicMock()
        return store

    def test_each_writer_key_adds_only_under_its_own_prefix(self):
        """Q-K91: Celery's fingerprint key never adds an event, plan or result; the events
        key never adds a fingerprint."""
        fingerprint = f"{anchor.prefix()}2026/11/17/x.json"
        event = f"{events.prefix()}2026/11/17/x.json"
        plan = events.plan_key(self.rid)
        writer, owner = self._store("WRITE"), self._store("EVENTS")
        for store, key in ((writer, event), (writer, plan), (owner, fingerprint)):
            with self.assertRaises(anchor.ScopeError):
                store.put(key, b"{}")
        writer.put(fingerprint, b"{}")
        owner.put(event, b"{}")
        owner.put(plan, b"{}")
        self.assertEqual(writer.client.put_object.call_count, 1)
        self.assertEqual(owner.client.put_object.call_count, 2)
        # until a site has the events key, events go with the writer's key, scoped to them
        fallback = self._store("WRITE", scope_as="EVENTS")
        with self.assertRaises(anchor.ScopeError):
            fallback.put(fingerprint, b"{}")

    def test_events_use_the_events_key_when_there_is_one(self):
        with patch.dict(
            "os.environ",
            {
                "IHR_AUDIT_ANCHOR_EVENTS_KEY": "k" * 20,
                "IHR_AUDIT_ANCHOR_EVENTS_SECRET": "s" * 40,
                "IHR_AUDIT_ANCHOR_BUCKET": "fake",
                "IHR_AUDIT_ANCHOR_ENDPOINT": "http://127.0.0.1:9",
            },
        ):
            self.assertEqual(events.writer_store().role, "EVENTS")


class Pending(SupersedeCase):
    def test_a_single_hospital_restore_waits_for_an_unfinished_load(self):
        from ihr_platform.restore import engine

        with self.world():
            self.export()
            self.plan()
            found = events.read(self.store, timeline=self.new)
            self.assertEqual(found.pending_loads(), [self.rid])
            run = engine.Restore(engine.Options(slug=self.hospital.slug, reason="t"))
            with (
                patch.object(events, "read", return_value=found),
                self.assertRaisesRegex(engine.Refused, "not loaded yet"),
            ):
                run._no_pending_whole_restore()
            # before the restore (old timeline) the plan blocks nothing
            self.assertEqual(
                events.read(self.store, timeline=self.old).pending_loads(), []
            )


# --- P1-T22c1 review fixes (docs/reviews/p1-t22c/build-c1.md, "Review") -----------------------


class ReviewLoad(SupersedeCase):
    def _seal_restored(self):
        """What `up` and beat do to the restored database before a load is run again: a new
        seal after L (new traffic, or the load's own restore entry, sealed). Written as a row:
        inside the test's transaction the sealer's horizon stops before its own entries."""
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO ihr_audit_seal (seq, lower_txid, upper_txid, entries, "
                "entries_hash, prev_hash, hash, version) SELECT seq + 1, upper_txid, "
                "((upper_txid::text)::numeric + 1)::text::xid8, 1, sha256(''::bytea), hash, "
                "sha256(hash || 'after up'::bytea), "
                "version FROM ihr_audit_seal ORDER BY seq DESC LIMIT 1 RETURNING seq"
            )
            self.assertEqual(cursor.fetchone()[0], self.lower[0] + 1)

    def test_a_load_run_again_after_up_accepts_seals_added_since(self):
        """Review T2/E2: a load that stopped (no result) and is run again after `up` finds
        the restored chain grown past L; F = L is read at L, so it still finishes."""
        with self.world():
            self.export()
            self.plan()
            self.restore()
            with (
                patch.object(
                    events, "result_document", side_effect=RuntimeError("crash")
                ),
                self.assertRaises(RuntimeError),
            ):
                self.load()
            self._seal_restored()
            outcomes = self.load()
            self.assertEqual(outcomes[self.uuid].outcome, "loaded")
            found = self.read()
            self.assertIn(self.rid, found.results)
            self.assertEqual(self.verdict(found), [])

    def test_a_first_load_after_up_accepts_a_new_branch_after_lower(self):
        with self.world():
            self.export()
            self.plan()
            self.restore()
            self._seal_restored()
            self.assertEqual(self.load()[self.uuid].outcome, "loaded")

    def test_the_packages_own_next_seal_in_the_restored_chain_is_refused(self):
        """F = L still holds: a restored chain that goes on past L with the package's own next
        seal (sealed before T: the fingerprint was stale) is refused."""
        with self.world():
            self.export()
            self.plan()
            self.restore(to_seq=self.top[0])
            with self.assertRaisesRegex(supersede.Refused, "F = L"):
                self.load()
        self.assertEqual(self.store.keys(events.prefix()), [])

    def test_a_changed_but_well_formed_package_is_refused_by_its_sha256(self):
        """Review Q6: a re-gzipped package whose entries were changed after the plan."""
        import gzip

        with self.world():
            self.export()
            self.plan()
            self.restore()
            name = f"{self.uuid}.jsonl.gz"
            lines = gzip.decompress(self.members[name]).decode().splitlines()
            for i, line in enumerate(lines):
                doc = json.loads(line)
                if "entry" in doc:
                    doc["entry"]["actor"] = "someone-else"
                    lines[i] = json.dumps(doc, sort_keys=True)
                    break
            self.members[name] = gzip.compress(
                ("\n".join(lines) + "\n").encode(), mtime=0
            )
            supersede.Package.from_bytes(self.members[name])  # well-formed
            with self.assertRaisesRegex(
                supersede.Refused, "not the one the plan names"
            ):
                self.load()
        self.assertEqual(self.store.keys(events.prefix()), [])

    def test_only_one_load_runs_at_a_time(self):
        """Review E6: a second load while one holds the lock is refused before it reads."""
        import threading

        from django.db import connection as conn

        held, done = threading.Event(), threading.Event()

        def other():
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_lock(%s)", [supersede.LOAD_LOCK])
                    held.set()
                    done.wait(30)
                    cursor.execute(
                        "SELECT pg_advisory_unlock(%s)", [supersede.LOAD_LOCK]
                    )
            finally:
                held.set()
                conn.close()

        thread = threading.Thread(target=other)
        thread.start()
        try:
            self.assertTrue(held.wait(30))
            with (
                patch.object(events, "read") as read,
                self.assertRaisesRegex(supersede.Refused, "another load"),
            ):
                supersede.load(
                    {}, self.rid, "tester", store=self.store, reader=self.store
                )
            read.assert_not_called()
        finally:
            done.set()
            thread.join(30)

    def test_the_restored_hospitals_are_counted_as_the_export_counts_them(self):
        """Review E4: a hospital still provisioning in the restored database is not "there"
        (no fingerprint at T could name it), as at the export."""
        with self.world(), connection.cursor() as cursor:
            self.assertIn(self.uuid, supersede._restored_hospitals(cursor))
            cursor.execute(
                "UPDATE public.ihr_platform_hospital SET status = 'provisioning' "
                "WHERE uuid = %s",
                [self.uuid],
            )
            self.assertNotIn(self.uuid, supersede._restored_hospitals(cursor))


class ReviewExport(SupersedeCase):
    def test_a_stale_fingerprint_at_the_restore_point_is_refused_at_the_export(self):
        """Review E3: when the release's own fingerprint is missing, the newest one at T is an
        older one, and the seal after it was sealed before T; the load would refuse the
        hospital only after the database is replaced. Refused at the export instead."""
        stale = FakeStore()
        stale.put(
            *next(
                (k, self.store.get(k))
                for k in self.store.keys(anchor.prefix())
                if self.store.received[k] == self.base
            )
        )
        stale.received = dict.fromkeys(stale.objects, self.base)
        self.store = stale
        self.point = datetime.now(UTC) + timedelta(minutes=1)
        with self.world(), self.assertRaisesRegex(supersede.Refused, "stale"):
            self.export()

    def test_a_database_behind_the_bucket_before_the_point_is_refused_at_the_export(
        self,
    ):
        """Review E5: the bucket holds more restore events of it (stored before T) than the
        database records; the load could never make them agree."""
        with self.world(), connection.cursor() as cursor:
            in_db = archive.generations_in_db(cursor, self.uuid)
            fake = [
                MagicMock(stored=self.base, generation=g) for g in range(1, in_db + 2)
            ]
            found = events.Events(restores={self.uuid: fake})
            with (
                patch.object(events, "read", return_value=found),
                self.assertRaisesRegex(supersede.Refused, "must agree"),
            ):
                self.export()


class ReviewGenerations(SupersedeCase):
    def test_the_generation_is_the_highest_not_a_count(self):
        """Review T6: a gap (a dropped object) must not make the next restore take a
        generation the bucket already has."""
        found = events.Events(
            restores={self.uuid: [MagicMock(generation=1), MagicMock(generation=3)]}
        )
        self.assertEqual(found.generation(self.uuid), 3)

    def test_an_event_object_that_cannot_be_counted_refuses_the_next_generation(self):
        from ihr_platform.restore import engine

        with self.world():
            self.full()
            found = self.read()
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            self.store.put(event.key, b"not an event")  # a second version: garbage
            found = self.read()
            self.assertTrue(found.generation_unsure(self.uuid))
            run = engine.Restore(engine.Options(slug=self.hospital.slug, reason="t"))
            run.op = MagicMock(pk=supersede.new_id())
            with (
                patch.object(run, "_bucket_events", return_value=found),
                connection.cursor() as cursor,
                self.assertRaisesRegex(engine.StepFailed, "cannot be counted"),
            ):
                run._generation(cursor, self.uuid)

    def test_the_engine_takes_the_next_generation_from_the_bucket(self):
        """Review Q5: the engine half of the generation fix (P1-T22c): with the bucket ahead
        of the database, a single-hospital restore takes the bucket's next generation."""
        from ihr_platform.restore import engine

        with self.world(), connection.cursor() as cursor:
            in_db = archive.generations_in_db(cursor, self.uuid)
            found = events.Events(
                restores={
                    self.uuid: [MagicMock(generation=g) for g in range(1, in_db + 3)]
                }
            )
            run = engine.Restore(engine.Options(slug=self.hospital.slug, reason="t"))
            run.op = MagicMock(pk=supersede.new_id())
            with patch.object(run, "_bucket_events", return_value=found):
                self.assertEqual(run._generation(cursor, self.uuid), in_db + 3)


class ReviewRules(SupersedeCase):
    def test_a_plan_superseded_by_a_later_run_from_the_same_timeline_is_not_pending(
        self,
    ):
        """Review E1/Q2: a rollback that stopped after its plan and was run again: the first
        plan never led to a restore, and must not block single-hospital restores for good."""
        with self.world():
            self.export()
            self.plan()
            abandoned, self.rid = self.rid, supersede.new_id()
            self.full()
            found = self.read()
            self.assertIn(abandoned, found.plans)
            self.assertNotIn(abandoned, found.results)
            self.assertEqual(found.pending_loads(), [])
            self.assertTrue(found.superseded_plan(found.plans[abandoned]))

    def test_timeline_zero_is_no_timeline(self):
        """Review T5: Postgres's timelines start at 1."""
        with self.assertRaises(ValueError):
            events.plan_document(
                self.rid, POINT, self.base, "00000000", IMAGE, {}, "tester"
            )

    def test_a_history_that_never_saw_the_change_cannot_say_and_is_counted(self):
        """Review T5: a watch that began on the new timeline cannot judge S2: no silent pass,
        the restore is counted as uncorroborated (its events still excuse on the rest)."""
        with self.world():
            self.full()
            now = datetime.now(UTC).isoformat()
            history = [{"timeline": self.new, "first": now, "last": now}]
            found = self.read(history=history)
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            self.assertEqual(event.problems, [])
            self.assertEqual(found.uncorroborated, {self.rid})

    def test_the_lower_bound_is_read_again_from_the_fingerprints(self):
        """Review T8: check_whole re-derives L from the fingerprints at T (S3)."""
        with self.world():
            self.full()
            found = self.read()
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            event.problems = []
            other = {self.uuid: (self.lower[0] - 1, "00" * 32, "x")}
            with patch.object(anchor, "claims_at", return_value=("k", other)):
                events.check_whole(event, found, self.store)
            self.assertTrue(
                any("not the newest fingerprint's" in p for p in event.problems),
                event.problems,
            )

    def test_an_event_stored_before_its_plan_excuses_nothing(self):
        """Review T8: the plan must be stored before the event."""
        with self.world():
            self.full()
            found = self.read()
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            event.problems = []
            found.plans[self.rid].stored = event.stored + timedelta(seconds=1)
            events.check_whole(event, found, self.store)
            self.assertTrue(
                any("no plan stored before it" in p for p in event.problems),
                event.problems,
            )


class ReviewWatch(SupersedeCase):
    def test_an_unannounced_change_stays_unexplained_after_an_explained_one(self):
        """Review T7: every change the watch keeps is judged, not only the latest."""
        import tempfile
        from pathlib import Path

        with self.world():
            self.full()
            now = datetime.now(UTC)

            def seen(timeline, at):
                return {
                    "timeline": timeline,
                    "first": at.isoformat(),
                    "last": at.isoformat(),
                }

            history = [
                seen(_next(self.old, -1), now - timedelta(hours=3)),
                seen(self.old, now - timedelta(hours=2)),  # nobody announced this one
                seen(self.new, now + timedelta(minutes=1)),  # this restore's
            ]
            found = self.read(history=history)
            with tempfile.TemporaryDirectory() as d, patch.object(watch, "_emit"):
                w = watch.Watch(watch.Config(root=Path(d)), store=self.store)
                w.state.data["timelines"] = history
                w._whole_restores(found)
                self.assertEqual(
                    w.gauges.values[("ihr_audit_timeline_unexplained", ())], 1
                )
                w.state.data["timelines"] = history[1:]
                w._whole_restores(found)
                self.assertEqual(
                    w.gauges.values[("ihr_audit_timeline_unexplained", ())], 0
                )
