"""A disaster point-in-time restore and the audit chain (P1-T22c2a; docs/reviews/p1-t22c/
design-c2.md, "c2a as decided" wins).

Flow (b): ``staging.sh restore --target T`` writes a **stub plan** before the restore, serves
first, then exports every hospital's tail from a copy restored from the backup repository (here:
the live test schema plays the copy, holding everything), writes the **export doc**, and loads.

The world of test_supersede: seal A fingerprinted at ``base`` (the lower bound L at T - skew),
seal B fingerprinted at ``base`` + 5 min (in the database at T), then seal C after T,
fingerprinted at ``base`` + 20 min. T = ``base`` + 11 min.
"""

import io
import json
import os
import types
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.db import connection
from ihr_platform.test_runner import TEST_SCHEMA

from ihr_audit import anchor, events, supersede, watch

from .test_supersede import IMAGE, SupersedeCase, _next
from .test_watch import _sealed

SET = "20261121-100000F"


class DisasterCase(SupersedeCase):
    def setUp(self):
        super().setUp()
        self.b = self.top  # sealed before T
        _sealed(1)
        anchor.write(self.store, now=self.base + timedelta(minutes=20))
        with connection.cursor() as cursor:
            self.top = supersede.head(cursor, TEST_SCHEMA)
        self.T = self.base + timedelta(minutes=11)
        self.archive_max = f"{self.old}{'0' * 15}9"

    def stub(self, rid=None, old=None, **kw):
        return supersede.write_stub(
            rid or self.rid,
            self.T,
            old or self.old,
            IMAGE,
            "tester",
            SET,
            f"{old or self.old}{'0' * 15}9",
            store=kw.pop("store", self.store),
            reader=self.store,
            **kw,
        )

    def export_b(self, **kw):
        out = io.BytesIO()
        draft = supersede.export_disaster(
            out,
            self.rid,
            IMAGE,
            "tester",
            source_end="2026-11-21T10:00:00+00:00",
            store=self.store,
            **kw,
        )
        out.seek(0)
        self.members = supersede.read_tar(out)
        return draft

    def recovery(self):
        """What supersede_parent reads from the restore (review T3): the history file's stop
        (the first commit after T, not applied) and the last commit it applied (at or before T)."""
        return {
            "stop": (self.T + timedelta(seconds=1, microseconds=5)).isoformat(),
            "replayed": (self.T - timedelta(seconds=30)).isoformat(),
        }

    def export_doc(self, recovery=...):
        return supersede.write_export(
            self.members,
            self.rid,
            store=self.store,
            reader=self.store,
            recovery=self.recovery() if recovery is ... else recovery,
        )

    def flow_b(self, to_seq=None):
        """Stub, the copy's export, restore to T (B), export doc. Not loaded. (The copy is
        this test schema before the restore: it holds the whole chain, as the repository's
        copy does; the export never reads the live database's timeline.)"""
        self.stub()
        self.export_b()
        self.restore(self.b[0] if to_seq is None else to_seq)
        with patch.object(events, "current_timeline", return_value=self.new):
            self.export_doc()

    def entry_times(self, seq):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT min(e.at), max(e.at) FROM ihr_audit_entry e, ihr_audit_seal s "
                "WHERE s.seq = %s AND e.txid >= s.lower_txid AND e.txid < s.upper_txid",
                [seq],
            )
            return cursor.fetchone()

    def cutoff_between_b_and_c(self):
        """A floor cutoff after every entry of seal B and before every entry of seal C."""
        _low, b_max = self.entry_times(self.b[0])
        c_min, _high = self.entry_times(self.top[0])
        self.assertLess(b_max, c_min)
        return b_max + (c_min - b_max) / 2

    def forge(self, restored_to, when):
        """Review T1: what the events key's holder can write: one single-hospital restore event
        (no cause) of the next generation, naming ``restored_to``, with no superseded segment
        anywhere (so it excuses nothing itself, and nothing reports it)."""
        with connection.cursor() as cursor:
            rows = [
                supersede._seal_row(s)
                for s in supersede.live_seals(
                    cursor, TEST_SCHEMA, restored_to[0] + 1, self.top[0]
                )
            ]
        doc = events.restore_document(
            self.hospital, 1, restored_to, rows, "forger", now=when
        )
        return events.write(doc, self.store)

    def forge_record(self, restored_to):
        """What only the database owner can add to the restored database for it: the forged
        event's restore record (so the generations agree), with no archived seal."""
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.ihr_restore_record (hospital, generation, operation, slug, "
                "restored_seq, restored_hash, from_seq, to_seq, head_hash, digest, "
                "parked_schema, dump, operator) VALUES (%s, 1, %s, 's', %s, decode(%s, 'hex'), "
                "%s, %s, decode(%s, 'hex'), %s, '', '', 'forger')",
                [
                    self.uuid,
                    supersede.new_id(),
                    restored_to[0],
                    restored_to[1],
                    restored_to[0] + 1,
                    self.top[0],
                    self.top[1],
                    "00" * 32,
                ],
            )

    def below_lower(self):
        """A real seal of the chain below L (seal A), or genesis."""
        if self.lower[0] < 2:
            return (0, events.seal.GENESIS.hex())
        with connection.cursor() as cursor:
            [s] = supersede.live_seals(
                cursor, TEST_SCHEMA, self.lower[0] - 1, self.lower[0] - 1
            )
        return (s["seq"], s["hash"])

    def rewrite_restored_b(self):
        """What only the database owner can do to the restored database before the load: its
        seal B rewritten (a new hash), so the computed fork point falls to A."""
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE ihr_audit_seal DISABLE TRIGGER ihr_audit_append_only"
            )
            cursor.execute(
                "UPDATE ihr_audit_seal SET hash = sha256(hash) WHERE seq = %s",
                [self.b[0]],
            )
            cursor.execute(
                "ALTER TABLE ihr_audit_seal ENABLE TRIGGER ihr_audit_append_only"
            )


class Stub(DisasterCase):
    def test_the_stub_is_written_with_no_database(self):
        def no_database(*_a, **_k):
            msg = "no database here"
            raise RuntimeError(msg)

        with patch.object(connection, "cursor", side_effect=no_database):
            key = self.stub()
        self.assertEqual(key, events.plan_key(self.rid))
        found = events.read(self.store, timeline=self.old, segments=events.NO_SEGMENTS)
        plan = found.plans[self.rid]
        self.assertEqual(plan.flow, "disaster")
        self.assertEqual(plan.hospitals, {})
        self.assertEqual(plan.old_timeline, self.old)
        self.assertEqual(plan.target_time, self.T)
        self.assertEqual(plan.source["backup_set"], SET)
        self.assertEqual(found.problems, [])
        self.assertEqual(supersede.bucket_level(found), 2)

    def test_the_old_timeline_must_be_the_repositorys(self):
        with self.assertRaisesRegex(supersede.Refused, "archive"):
            supersede.write_stub(
                self.rid,
                self.T,
                self.old,
                IMAGE,
                "tester",
                SET,
                f"{self.new}{'0' * 16}",
                store=self.store,
                reader=self.store,
            )


class DisasterLoad(DisasterCase):
    def test_serve_first_then_export_load_and_the_cut_is_excused(self):
        with self.world():
            self.stub()
            draft = self.export_b()  # the copy (see flow_b)
            self.restore(self.b[0])
            found = self.read()
            self.assertEqual(found.pending_loads(), [self.rid])  # served, not loaded
            self.assertTrue(
                any("cut" in p for p in self.verdict(found)), "a cut before"
            )
            with patch.object(events, "current_timeline", return_value=self.new):
                self.export_doc()
            mine = draft["hospitals"][self.uuid]
            self.assertEqual(
                (mine["lower"]["seq"], mine["lower"]["hash"]), self.lower[:2]
            )
            self.assertEqual(mine["head"]["seq"], self.top[0])
            outcomes = self.load()
            self.assertEqual(outcomes[self.uuid].outcome, "loaded")
            # F is the restored head at T (B), not the lower bound (A): flow (b)
            self.assertEqual(outcomes[self.uuid].fork, self.b[0])
            found = self.read()
            self.assertEqual(found.pending_loads(), [])
            self.assertEqual(self.verdict(found), [])
            self.assertGreater(found.excused_by[self.rid], 0)
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            self.assertEqual(event.problems, [])
            self.assertEqual(event.restored_to, self.b[:2])
            result = found.results[self.rid]
            self.assertEqual(result.plan, events.plan_key(self.rid))

    def test_disaster_documents_are_written_with_the_events_key_only(self):
        """Audit S5: a stub, an export doc, a disaster load's events and result never fall back
        to the fingerprint writer's key (Celery holds it)."""
        env = {
            "IHR_AUDIT_ANCHOR_WRITE_KEY": "k" * 20,
            "IHR_AUDIT_ANCHOR_WRITE_SECRET": "s" * 40,
            "IHR_AUDIT_ANCHOR_BUCKET": "fake",
            "IHR_AUDIT_ANCHOR_ENDPOINT": "http://127.0.0.1:9",
        }
        kept = {
            k: v for k, v in os.environ.items() if not k.startswith("IHR_AUDIT_ANCHOR_")
        }
        with patch.dict("os.environ", {**kept, **env}, clear=True):
            with self.assertRaises(anchor.NotConfiguredError):
                events.events_store()
            self.assertEqual(events.writer_store().role, "WRITE")  # flow (a) as c1
            with self.assertRaises(anchor.NotConfiguredError):
                self.stub(store=None)
            self.assertEqual(self.store.keys(events.restores_prefix()), [])
            with self.world():
                self.stub()
                self.export_b()
                self.restore(self.b[0])
                with patch.object(events, "current_timeline", return_value=self.new):
                    with self.assertRaises(anchor.NotConfiguredError):
                        supersede.write_export(
                            self.members, self.rid, reader=self.store
                        )
                    self.export_doc()
                    with self.assertRaises(anchor.NotConfiguredError):
                        supersede.load(
                            self.members, self.rid, "tester", reader=self.store
                        )
        self.assertEqual(self.store.keys(events.prefix()), [])


class ForkFloor(DisasterCase):
    def test_a_rewritten_restored_seal_cannot_lower_the_fork_point(self):
        """Audit S2: the database owner rewrites seal B of the restored database; the computed
        fork point would fall to A and B's original would be excused. B's entries are all
        before the floor's cutoff: refused."""
        cutoff = self.cutoff_between_b_and_c()
        with self.world():
            self.flow_b()
            self.rewrite_restored_b()
            with (
                patch.object(events, "floor_cutoff", return_value=cutoff),
                self.assertRaisesRegex(supersede.Refused, "floor"),
            ):
                self.load()
        self.assertEqual(self.store.keys(events.prefix()), [])

    def test_every_reader_checks_the_floor_again(self):
        cutoff = self.cutoff_between_b_and_c()
        with self.world():
            self.flow_b()
            self.rewrite_restored_b()
            with (
                patch.object(events, "floor_cutoff", return_value=cutoff),
                patch.object(supersede, "_fork_floor", return_value=""),
            ):
                with self.assertRaisesRegex(supersede.Refused, "do not verify"):
                    self.load()  # its own final check, as the watch will do it
                found = self.read()
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            self.assertEqual(event.restored_to[0], self.lower[0])
            self.assertTrue(any("floor" in p for p in event.problems), event.problems)

    def test_a_forged_single_hospital_event_excuses_no_rewrite(self):
        """Review T1: the events key's holder writes a single-hospital event with no segment,
        stored after T, naming seal A; the database owner rewrites the restored seal B, so the
        computed fork point is A. The load reads no segments (a restore after T has its segment
        only in the package) and lets it pass; every reader counts only a verified event of an
        earlier generation, stored before this one: the floor refuses it."""
        cutoff = self.cutoff_between_b_and_c()
        with self.world():
            self.forge(self.lower[:2], self.T + timedelta(minutes=1))
            self.flow_b()
            self.rewrite_restored_b()
            self.forge_record(self.lower[:2])
            with patch.object(events, "floor_cutoff", return_value=cutoff):
                with self.assertRaisesRegex(supersede.Refused, "do not verify"):
                    self.load()  # its own final check, as every reader does it
                found = self.read()
            [forged] = [e for e in found.restores[self.uuid] if not e.cause]
            self.assertTrue(forged.problems, "the forged event excuses nothing itself")
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            self.assertEqual(event.restored_to[0], self.lower[0])
            self.assertTrue(any("floor" in p for p in event.problems), event.problems)

    def test_the_floor_exception_counts_only_a_verified_earlier_event(self):
        t, fork = self.T, self.lower[:2]
        late = events.floor_cutoff(t) - timedelta(minutes=1)
        mine = events.Event(
            "d", "restore", self.uuid, "s", t, t + timedelta(minutes=5), "o", 3, fork
        )

        def single(generation=2, stored=t + timedelta(minutes=2), problems=()):
            e = events.Event(
                "k", "restore", self.uuid, "s", t, stored, "o", generation, fork
            )
            e.problems = list(problems)
            return e

        self.assertEqual(events.fork_floor_problem(t, fork, late, [single()], mine), "")
        for other in (
            single(problems=["no superseded segment"]),
            single(generation=3),
            single(generation=4),
            single(stored=mine.stored + timedelta(seconds=1)),
        ):
            self.assertIn(
                "floor", events.fork_floor_problem(t, fork, late, [other], mine)
            )
        # the load's own check (no segments yet): generation and time only
        self.assertEqual(
            events.fork_floor_problem(
                t, fork, late, [single(problems=["no superseded segment"])]
            ),
            "",
        )

    def test_the_real_margin_accepts_a_fork_after_t(self):
        with self.world():
            self.flow_b()
            outcomes = self.load()
        self.assertEqual(outcomes[self.uuid].fork, self.b[0])

    def test_a_single_hospital_restore_after_t_may_name_a_lower_fork(self):
        t = self.T
        cutoff = t - events.FLOOR_MARGIN
        late = cutoff - timedelta(minutes=1)
        single = events.Event(
            "k",
            "restore",
            self.uuid,
            "s",
            t,
            t + timedelta(minutes=2),
            "o",
            1,
            (self.lower[0], self.lower[1]),
        )
        self.assertEqual(cutoff, events.floor_cutoff(t))
        self.assertEqual(
            events.fork_floor_problem(
                t, (self.lower[0], self.lower[1]), late, [single]
            ),
            "",
        )
        self.assertIn(
            "floor",
            events.fork_floor_problem(t, (self.lower[0], self.lower[1]), late, []),
        )
        # an event stored before t - skew, or a whole-database one, does not count
        single.stored = t - anchor.CLOCK_SKEW - timedelta(seconds=1)
        self.assertIn(
            "floor", events.fork_floor_problem(t, self.lower[:2], late, [single])
        )
        single.stored, single.cause = t, "whole-database"
        self.assertIn(
            "floor", events.fork_floor_problem(t, self.lower[:2], late, [single])
        )
        self.assertEqual(
            events.fork_floor_problem(
                t, self.lower[:2], cutoff + timedelta(seconds=1), []
            ),
            "",
        )
        self.assertIn("floor", events.fork_floor_problem(t, self.lower[:2], None, []))


class Lower(DisasterCase):
    def test_the_lower_bound_falls_to_a_restore_event_after_the_fingerprint(self):
        fp_stored = self.base
        later = events.Event(
            "k",
            "restore",
            self.uuid,
            "s",
            self.T,
            fp_stored + timedelta(minutes=1),
            "o",
            1,
            (3, "ab" * 32),
        )
        earlier = events.Event(
            "k",
            "restore",
            self.uuid,
            "s",
            self.T,
            fp_stored - timedelta(minutes=1),
            "o",
            1,
            (2, "cd" * 32),
        )
        claim = (7, "ef" * 32)
        stub = fp_stored + timedelta(minutes=30)
        self.assertEqual(
            events.lower_of(claim, fp_stored, [later, earlier], stub), (3, "ab" * 32)
        )
        self.assertEqual(events.lower_of(claim, fp_stored, [earlier], stub), claim)
        genesis = (0, events.seal.GENESIS.hex())
        # live proof: no fingerprint at T - skew is genesis, whatever events say (the load's
        # own event once named the fork point as L)
        self.assertEqual(events.lower_of(None, None, [earlier, later], stub), genesis)
        self.assertEqual(events.lower_of(None, None, [], stub), genesis)
        # an event stored after the stub (this restore's own, a later restore on the new
        # timeline) never moves L: the copy holds nothing of it
        later.stored = stub
        self.assertEqual(events.lower_of(claim, fp_stored, [later], stub), claim)

    def test_the_readers_bound_counts_only_a_verified_earlier_event(self):
        """Review T1: with ``generation`` (every reader: the disaster event's), an event with
        problems (no verified segment) or of that generation or later never lowers L; the
        exporters (None) go by time only."""
        fp_stored, stub = self.base, self.base + timedelta(minutes=30)
        claim, low = (7, "ef" * 32), (3, "ab" * 32)

        def single(generation=1, problems=()):
            e = events.Event(
                "k",
                "restore",
                self.uuid,
                "s",
                self.T,
                fp_stored + timedelta(minutes=1),
                "o",
                generation,
                low,
            )
            e.problems = list(problems)
            return e

        self.assertEqual(events.lower_of(claim, fp_stored, [single()], stub, 2), low)
        for other in (single(problems=["no superseded segment"]), single(2), single(3)):
            self.assertEqual(events.lower_of(claim, fp_stored, [other], stub, 2), claim)
        self.assertEqual(
            events.lower_of(claim, fp_stored, [single(problems=["x"])], stub), low
        )

    def test_a_forged_event_never_lowers_the_readers_bound(self):
        """Review T1: a single-hospital event with no segment, stored after the fingerprint at
        T - skew and before the stub, naming a seal below L. The export (no segments) takes it;
        every reader re-derives L from verified events only: the event excuses nothing."""
        with self.world():
            below = self.below_lower()
            self.forge(below, self.T + timedelta(minutes=1))
            self.flow_b()
            exp = self.read().exports[self.rid]
            self.assertEqual(exp.hospitals[self.uuid]["lower"]["seq"], below[0])
            self.forge_record(below)
            with self.assertRaisesRegex(supersede.Refused, "do not verify"):
                self.load()
            found = self.read()
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            self.assertTrue(
                any("lower bound" in p for p in event.problems), event.problems
            )
            self.assertTrue(any("cut" in p for p in self.verdict(found)), "cuts stay")

    def test_with_no_fingerprint_at_t_minus_skew_the_bound_is_genesis_and_holds(self):
        """Live proof finding: T within 10 minutes of the first fingerprint; L = genesis at the
        export and in every reader after the load wrote its own event."""
        self.T = self.base + timedelta(
            minutes=5
        )  # T - skew is before every fingerprint
        with self.world():
            self.flow_b()
            outcomes = self.load()
            self.assertEqual(outcomes[self.uuid].outcome, "loaded")
            found = self.read()
            exp = found.exports[self.rid]
            self.assertEqual(exp.hospitals[self.uuid]["lower"]["seq"], 0)
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            self.assertEqual(event.problems, [])
            self.assertEqual(self.verdict(found), [])

    def test_the_readers_lower_bound_is_read_again(self):
        with self.world():
            self.flow_b()
            self.load()
            found = self.read()
            self.assertEqual(found.problems, [])
            exp = found.exports[self.rid]
            self.assertEqual(
                (exp.hospitals[self.uuid]["lower"]["seq"],), (self.lower[0],)
            )
            # the bucket says otherwise (the export doc's L is not re-derived): excuses nothing
            with patch.object(events, "disaster_lower", return_value=(0, "00" * 32)):
                found = self.read()
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            self.assertTrue(
                any("lower bound" in p for p in event.problems), event.problems
            )


class ExportDoc(DisasterCase):
    def test_the_export_doc_binds_t_to_the_restores_own_stop(self):
        """Review T3: the export doc carries the restore's own stop (the history file's "before"
        time: the first commit after the target, not applied) and the last commit it applied.
        T must lie between them (the restored database is then the state at T, whatever target
        pgBackRest was given): else refused, and written nowhere."""
        t, s = self.T, timedelta(seconds=1)
        with self.world():
            self.stub()
            self.export_b()
            self.restore(self.b[0])
            with patch.object(events, "current_timeline", return_value=self.new):
                for bad in (
                    None,
                    {},
                    {"stop": "", "replayed": ""},
                    {"stop": t.isoformat(), "replayed": ""},  # stop is after T, always
                    {"stop": (t - s).isoformat(), "replayed": ""},  # an earlier target
                    # a commit after T was applied: the real target was later than the stub's
                    {"stop": (t + 2 * s).isoformat(), "replayed": (t + s).isoformat()},
                    {"stop": "yesterday", "replayed": ""},
                ):
                    with self.assertRaisesRegex(supersede.Refused, "recovery"):
                        self.export_doc(recovery=bad)
                self.assertEqual(self.store.keys(events.export_key(self.rid)), [])
                # a quiet database: no commit for minutes after T, none applied since the backup
                quiet = {"stop": (t + 300 * s).isoformat(), "replayed": ""}
                self.export_doc(recovery=quiet)
            found = self.read()
            self.assertEqual(found.exports[self.rid].recovery, quiet)

    def test_every_reader_requires_the_restores_stop(self):
        """Review T3: an export doc with no recovery field (or one T is not inside) excuses
        nothing, whoever wrote it."""
        with self.world():
            self.stub()
            self.export_b()
            self.restore(self.b[0])
            draft = json.loads(self.members[supersede.EXPORT_MEMBER])
            draft["written_at"] = datetime.now(UTC).isoformat()
            events.write_restore_doc(
                events.export_key(self.rid), draft, store=self.store, reader=self.store
            )
            with self.assertRaisesRegex(supersede.Refused, "do not verify"):
                self.load()
            found = self.read()
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            self.assertTrue(
                any("recovery" in p for p in event.problems), event.problems
            )

    def test_an_export_doc_stored_after_the_event_excuses_nothing(self):
        with self.world():
            self.flow_b()
            self.load()
            key = events.export_key(self.rid)
            found = self.read()
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            self.store.received[key] = event.stored + timedelta(minutes=1)
            found = self.read()
            [event] = [e for e in found.restores[self.uuid] if e.restore_id == self.rid]
            self.assertTrue(any("export" in p for p in event.problems), event.problems)

    def test_an_export_doc_must_continue_its_stub(self):
        with self.world():
            self.flow_b()
            doc = events.parse_restore_doc(
                events.export_key(self.rid),
                self.store.get(events.export_key(self.rid)),
                datetime.now(UTC),
            )
            self.assertEqual(doc.old_timeline, self.old)
        # a stray export doc of a planned rollback's plan is a bad object
        self.rid = supersede.new_id()
        with self.world():
            self.export()
            self.plan()
            body = (
                self.store.get(events.export_key(self.rid))
                if self.store.keys(events.export_key(self.rid))
                else None
            )
            self.assertIsNone(body)
            stray = events.export_document(
                self.rid,
                self.old,
                IMAGE,
                {},
                "tester",
                source_end={"last_commit": "", "backup_set": SET},
            )
            self.store.put(events.export_key(self.rid), events._body(stray))
            found = self.read()
            self.assertNotIn(self.rid, found.exports)
            self.assertTrue(any("export" in p for p in found.problems), found.problems)


class Pending(DisasterCase):
    def test_a_second_export_is_refused_while_a_load_is_pending(self):
        with self.world():
            self.flow_b()
            # a planned rollback's export (flow a) on the new timeline
            with (
                patch.object(events, "current_timeline", return_value=self.new),
                self.assertRaisesRegex(supersede.Refused, self.rid[:8]),
            ):
                supersede.export(
                    io.BytesIO(),
                    supersede.new_id(),
                    "p",
                    self.T,
                    IMAGE,
                    "t",
                    store=self.store,
                )
            # the same restore exported again
            with (
                patch.object(events, "current_timeline", return_value=self.new),
                self.assertRaisesRegex(supersede.Refused, "already exported"),
            ):
                self.export_b()
            # a sibling stub from the same old timeline (a restore that failed and was run
            # again) never blocks: only one of them moved the database
            sibling = supersede.new_id()
            self.stub(rid=sibling)
            self.load()
            found = self.read()
            self.assertEqual(found.pending_loads(), [])  # the sibling is superseded

    def test_a_stub_from_a_later_timeline_passes_a_pending_ancestor(self):
        """Review E1: restore A (tl1 to tl2) is served and not loaded; the server is lost again
        and the repository's newest timeline is tl2. The new stub, and its export, are not
        refused for A (nothing can load or close A on a lost server); A keeps alerting."""
        with self.world():
            self.flow_b()
            later = supersede.new_id()
            try:
                self.stub(rid=later, old=self.new)
            except supersede.Refused as exc:
                self.fail(f"the stub was refused for the pending ancestor: {exc}")
            found = self.read()
            self.assertIn(later, found.plans)
            try:
                supersede._disaster_plan(
                    found, later
                )  # its export is not refused either
            except supersede.Refused as exc:
                self.fail(f"the export was refused for the pending ancestor: {exc}")
            self.assertEqual(found.pending_loads(), [self.rid])

    def test_the_refusal_names_the_next_step_of_each_flow(self):
        """Review Q5: a disaster restore not yet exported is exported, then loaded; an exported
        one is loaded (``load`` refuses an unexported one)."""
        with self.world():
            self.stub()
            self.restore(self.b[0])

            def refusal():
                with (
                    patch.object(events, "current_timeline", return_value=self.new),
                    self.assertRaises(supersede.Refused) as caught,
                ):
                    supersede.export(
                        io.BytesIO(),
                        supersede.new_id(),
                        "p",
                        self.T,
                        IMAGE,
                        "t",
                        store=self.store,
                    )
                return str(caught.exception)

            self.assertIn(
                f"audit-supersede export {self.rid}, then load {self.rid}", refusal()
            )
            self.export_b()
            with patch.object(events, "current_timeline", return_value=self.new):
                self.export_doc()
            text = refusal()
            self.assertIn(f"audit-supersede load {self.rid}", text)
            self.assertNotIn(f"export {self.rid}", text)
            found = self.read()
            self.assertEqual(
                found.next_step(self.rid),
                f"scripts/staging.sh audit-supersede load {self.rid}",
            )

    def test_an_orphan_stub_explains_no_unrelated_timeline_change(self):
        """Review E7: an attempt failed after its stub on a server that came back on tl1 (the
        watch kept seeing tl1 after it). A later, unrelated change away from tl1 is unexplained,
        not pending; the newest stub written between the watch's last sight of the old
        timeline and its first of the new one is the restore that explains it."""
        import tempfile
        from pathlib import Path

        now = datetime.now(UTC)
        history = [
            {
                "timeline": self.old,
                "first": (now - timedelta(hours=5)).isoformat(),
                "last": (now - timedelta(minutes=5)).isoformat(),
            },
            {
                "timeline": self.new,
                "first": (now + timedelta(minutes=1)).isoformat(),
                "last": (now + timedelta(minutes=1)).isoformat(),
            },
        ]

        def unexplained():
            found = events.read(
                self.store, timeline=self.new, segments=events.NO_SEGMENTS
            )
            with tempfile.TemporaryDirectory() as d, patch.object(watch, "_emit"):
                w = watch.Watch(watch.Config(root=Path(d)), store=self.store)
                w.state.data["timelines"] = history
                w._whole_restores(found)
                self.assertEqual(
                    w.gauges.values[("ihr_audit_whole_restores_pending", ())],
                    len(found.pending_loads()),
                )
                return w.gauges.values[("ihr_audit_timeline_unexplained", ())]

        # stored 8 minutes ago (inside the store's clock allowance), before the watch last saw
        # tl1: an orphan of an attempt that went nowhere
        self.store.clock = now - timedelta(minutes=8)
        self.stub(rid=supersede.new_id())
        self.store.clock = None
        self.assertEqual(unexplained(), 1)
        # a second attempt, stored after the watch last saw tl1 and before it saw tl2: the
        # restore itself (the newest stub of that old timeline; the orphan beside it is no matter)
        self.stub(rid=supersede.new_id())
        self.assertEqual(unexplained(), 0)

    def test_pending_comes_from_plans_alone(self):
        """Goldfish C2-1: a watch on a new server (no timeline history) still reports the
        pending load, and its age."""
        import tempfile
        from pathlib import Path

        self.stub()
        found = events.read(self.store, timeline=self.new, segments=events.NO_SEGMENTS)
        with tempfile.TemporaryDirectory() as d, patch.object(watch, "_emit"):
            w = watch.Watch(watch.Config(root=Path(d)), store=self.store)
            w._whole_restores(found)
            g = w.gauges.values
            self.assertEqual(g[("ihr_audit_whole_restores_pending", ())], 1)
            self.assertGreaterEqual(
                g[("ihr_audit_whole_restore_pending_oldest_seconds", ())], 0
            )
            self.assertEqual(g[("ihr_audit_timeline_unexplained", ())], 0)
            # with history: the change from the stub's old timeline is pending, not unexplained
            now = datetime.now(UTC)
            w.state.data["timelines"] = [
                {
                    "timeline": self.old,
                    "first": (now - timedelta(hours=2)).isoformat(),
                    "last": (now - timedelta(hours=1)).isoformat(),
                },
                {
                    "timeline": self.new,
                    "first": (now + timedelta(minutes=1)).isoformat(),
                    "last": (now + timedelta(minutes=1)).isoformat(),
                },
            ]
            w._whole_restores(found)
            self.assertEqual(g[("ihr_audit_timeline_unexplained", ())], 0)
            with patch.object(watch, "_now", return_value=now + timedelta(hours=49)):
                w._whole_restores(found)
            self.assertEqual(g[("ihr_audit_timeline_unexplained", ())], 1)
            self.assertGreater(
                g[("ihr_audit_whole_restore_pending_oldest_seconds", ())], 48 * 3600
            )

    def test_a_stub_written_after_the_restore_explains_nothing(self):
        """The stub is written before the restore: one stored after the watch first saw the
        new timeline is neither pending nor a corroborated plan (the change stays unexplained,
        critical)."""
        import tempfile
        from pathlib import Path

        now = datetime.now(UTC)
        self.store.clock = now + timedelta(
            minutes=5
        )  # stored after the watch saw the change
        self.stub()
        self.store.clock = None
        found = events.read(self.store, timeline=self.new, segments=events.NO_SEGMENTS)
        history = [
            {
                "timeline": self.old,
                "first": (now - timedelta(hours=2)).isoformat(),
                "last": (now - timedelta(hours=1)).isoformat(),
            },
            {"timeline": self.new, "first": now.isoformat(), "last": now.isoformat()},
        ]
        with tempfile.TemporaryDirectory() as d, patch.object(watch, "_emit"):
            w = watch.Watch(watch.Config(root=Path(d)), store=self.store)
            w.state.data["timelines"] = history
            w._whole_restores(found)
            self.assertEqual(w.gauges.values[("ihr_audit_timeline_unexplained", ())], 1)
            self.assertEqual(
                w.gauges.values[("ihr_audit_whole_restores_pending", ())], 1
            )
        result = events.Result(
            "k",
            self.rid,
            now + timedelta(minutes=9),
            now,
            "o",
            events.plan_key(self.rid),
            self.old,
            self.new,
            {},
        )
        self.assertIn(
            "stored after the watch already saw",
            events.corroborated(found.plans[self.rid], result, history),
        )


class ExportWarning(DisasterCase):
    def test_the_export_warns_when_its_stub_is_older_than_fourteen_days(self):
        """Review Q8: the backup set and the old timeline's WAL leave the repository after 28
        days; the README promises the warning. (The store accepts a plan only within its clock
        allowance of its writer's time, so it is the export that is 15 days later.)"""

        class Later(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(days=15)

        with self.world():
            self.stub()
            with (
                patch.object(supersede, "datetime", Later),
                patch.object(supersede, "_emit") as emit,
            ):
                self.export_b()
            lines = [c.args[0] for c in emit.call_args_list]
            self.assertTrue(
                any("WARNING" in line and "14 days" in line for line in lines), lines
            )

    def test_a_fresh_stub_gets_no_warning(self):
        with self.world():
            self.stub()
            with patch.object(supersede, "_emit") as emit:
                self.export_b()
            self.assertFalse(any("WARNING" in c.args[0] for c in emit.call_args_list))


class Close(DisasterCase):
    def test_close_writes_the_result_and_frees_the_next_restore(self):
        with self.world():
            self.stub()
            self.restore(self.b[0])
            with patch.object(events, "current_timeline", return_value=self.new):
                supersede.close(
                    self.rid,
                    "repository lost",
                    "tester",
                    store=self.store,
                    reader=self.store,
                )
            found = self.read()
            self.assertEqual(found.pending_loads(), [])
            self.assertIn(self.rid, found.anchored)
            self.assertEqual(found.results[self.rid].closed["how"], "close")
            self.assertTrue(any("cut" in p for p in self.verdict(found)), "cuts stay")
            self.stub(rid=supersede.new_id(), old=self.new)  # not refused any more

    def test_no_supersede_names_every_hospital_and_no_lost_range(self):
        with self.world():
            self.stub(no_supersede="the repository is gone")
            self.restore(self.b[0])
            with patch.object(events, "current_timeline", return_value=self.new):
                supersede.close(
                    self.rid,
                    "the repository is gone",
                    "tester",
                    how="no-supersede",
                    store=self.store,
                    reader=self.store,
                )
            found = self.read()
            result = found.results[self.rid]
            self.assertEqual(result.hospitals[self.uuid]["outcome"], "refused")
            self.assertNotIn("lost", json_of(self.store, events.result_key(self.rid)))
            self.assertTrue(any("cut" in p for p in self.verdict(found)), "cuts stay")

    def test_closing_a_stub_with_no_export_names_the_hospitals_at_t(self):
        """Review E8: a stub has no hospitals before its export; its closed result names those
        of the fingerprint at T as --no-supersede does."""
        with self.world():
            self.stub()
            self.restore(self.b[0])
            self.close(self.rid)
            result = self.read().results[self.rid]
            self.assertIn(self.uuid, result.hospitals)
            self.assertEqual(result.hospitals[self.uuid]["outcome"], "refused")

    def test_closed_results_alert_until_acknowledged(self):
        """Review T2 (Q-K105): a closed or --no-supersede result explains the timeline change
        but can lose data after the last fingerprint with no trace: a gauge, and a line in
        ihr_audit_verify, until the restore id is acknowledged."""
        import tempfile
        from pathlib import Path

        with self.world():
            self.stub()
            self.restore(self.b[0])
            self.close(self.rid)
            found = self.read()
            self.assertEqual(list(found.closed_results()), [self.rid])
            self.assertEqual(found.closed_results({self.rid: "reviewed"}), {})
            [line] = found.closed_problems()
            self.assertIn(self.rid, line)
            self.assertIn("acknowledged_closed.txt", line)
            self.assertEqual(found.closed_problems({self.rid: "reviewed"}), [])
            key = "ihr_audit_whole_restores_closed"
            with tempfile.TemporaryDirectory() as d, patch.object(watch, "_emit"):
                w = watch.Watch(watch.Config(root=Path(d)), store=self.store)
                w._whole_restores(found)
                g = w.gauges.values
                self.assertEqual(g[(key, (("how", "close"),))], 1)
                self.assertEqual(g[(key, (("how", "no-supersede"),))], 0)
                with patch.object(
                    events,
                    "acknowledged_closed",
                    return_value={self.rid: "reviewed 2026-11-21"},
                ):
                    w._whole_restores(found)
                self.assertEqual(g[(key, (("how", "close"),))], 0)

    def test_the_acknowledged_closed_list_is_reviewed_lines(self):
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile("w", suffix=".txt") as f:
            f.write("not-an-id 2026-11-21 the incident was reviewed\n")
            f.flush()
            with self.assertRaises(ValueError):
                events.acknowledged_closed(Path(f.name))
        with tempfile.NamedTemporaryFile("w", suffix=".txt") as f:
            f.write(
                f"{self.rid} 2026-11-21 nothing was written after the fingerprint\n"
            )
            f.flush()
            self.assertEqual(list(events.acknowledged_closed(Path(f.name))), [self.rid])
        self.assertEqual(
            events.acknowledged_closed(), {}
        )  # the reviewed list starts empty

    def two_stubs(self):
        """An attempt (stored now) and a later one from the same old timeline (a minute on)."""
        first = self.rid
        self.stub()
        self.rid = supersede.new_id()
        self.store.clock = datetime.now(UTC).replace(microsecond=0) + timedelta(
            minutes=1
        )
        self.stub()
        self.store.clock = None
        return first, self.rid

    def close(self, rid, timeline=None):
        with patch.object(
            events, "current_timeline", return_value=timeline or self.new
        ):
            return supersede.close(
                rid, "it failed", "tester", store=self.store, reader=self.store
            )

    def test_an_orphan_stub_is_not_closed_while_a_later_attempt_waits(self):
        """Review E3: attempt 1 died after its stub (never promoted), attempt 2 restored. Closing
        the orphan would mark attempt 2 superseded: refused; and a closed result of an earlier
        attempt (written before this check) never supersedes a later one."""
        with self.world():
            orphan, real = self.two_stubs()
            self.restore(self.b[0])
            with self.assertRaisesRegex(supersede.Refused, "later attempt"):
                self.close(orphan)
            self.assertEqual(self.read().pending_loads(), sorted([orphan, real]))
            doc = events.result_document(
                orphan,
                self.old,
                self.new,
                {},
                "tester",
                closed={"reason": "it failed", "how": "close"},
            )
            events.write_restore_doc(
                events.result_key(orphan), doc, store=self.store, reader=self.store
            )
            self.assertEqual(self.read().pending_loads(), [real])
            # the later attempt's result (loaded or closed) supersedes the orphan
            self.store.clock = datetime.now(UTC).replace(microsecond=0) + timedelta(
                minutes=2
            )  # after its plan (stored a minute on)
            self.close(real)
            self.store.clock = None
            self.assertEqual(self.read().pending_loads(), [])

    def test_a_retry_after_a_closed_attempt_stays_pending(self):
        """Review Q1: attempt 1 promoted but its new timeline never reached the repository; it is
        closed; the retry's stub has the same old timeline. The retry is pending from its stub
        on (its alerts, and the next export or stub waits for it)."""
        with self.world():
            self.stub()
            self.restore(self.b[0])
            first = self.rid
            self.close(first)
            self.rid = supersede.new_id()
            self.store.clock = datetime.now(UTC).replace(microsecond=0) + timedelta(
                minutes=1
            )
            self.stub()
            self.store.clock = None
            found = self.read()
            self.assertEqual(found.pending_loads(), [self.rid])
            self.assertFalse(found.superseded_plan(found.plans[self.rid]))
            # a planned rollback's export waits for it (a disaster stub from the repository's
            # newer timeline does not: review E1)
            with (
                patch.object(events, "current_timeline", return_value=self.new),
                self.assertRaisesRegex(supersede.Refused, self.rid[:8]),
            ):
                supersede.export(
                    io.BytesIO(),
                    supersede.new_id(),
                    "p",
                    self.T,
                    IMAGE,
                    "t",
                    store=self.store,
                )

    def test_close_is_refused_once_an_event_of_it_exists_and_before_the_restore(self):
        with self.world():
            self.stub()
            with (
                patch.object(events, "current_timeline", return_value=self.old),
                self.assertRaisesRegex(supersede.Refused, "still on"),
            ):
                supersede.close(self.rid, "r", "t", store=self.store, reader=self.store)
            self.export_b()
            self.restore(self.b[0])
            with patch.object(events, "current_timeline", return_value=self.new):
                self.export_doc()
            with (
                patch.object(supersede, "_write_result", side_effect=RuntimeError("x")),
                self.assertRaises(RuntimeError),
            ):
                self.load()  # the event is written, the result is not
            with (
                patch.object(events, "current_timeline", return_value=self.new),
                self.assertRaisesRegex(supersede.Refused, "load"),
            ):
                supersede.close(self.rid, "r", "t", store=self.store, reader=self.store)


class Partial(DisasterCase):
    def test_an_unreadable_hospital_is_refused_unless_accepted(self):
        def broken(*_a, **_k):
            msg = "relation ihr_audit_seal is damaged"
            raise supersede.Refused(msg)

        with self.world():
            self.stub()
            with patch.object(supersede, "export_one", side_effect=broken):
                with self.assertRaisesRegex(supersede.Refused, "damaged"):
                    self.export_b()
                draft = self.export_b(accept_unreadable="schema damaged")
            self.restore(self.b[0])
            mine = draft["hospitals"][self.uuid]
            self.assertEqual(mine["outcome"], "unreadable")
            self.assertEqual(mine["reason"], "schema damaged")
            with patch.object(events, "current_timeline", return_value=self.new):
                self.export_doc()
            outcomes = self.load()
            self.assertEqual(outcomes[self.uuid].outcome, "unreadable")
            found = self.read()
            self.assertEqual(
                found.results[self.rid].hospitals[self.uuid]["outcome"], "unreadable"
            )
            self.assertTrue(any("cut" in p for p in self.verdict(found)), "cuts stay")

    def test_a_hospital_not_in_the_restored_database_is_absent(self):
        with self.world():
            self.flow_b()
            with patch.object(supersede, "_restored_hospitals", return_value={}):
                outcomes = self.load()
            self.assertEqual(outcomes[self.uuid].outcome, "absent")


class Level(DisasterCase):
    def test_a_build_without_the_constant_is_level_1(self):
        self.assertEqual(supersede.LEVEL, 2)
        self.assertEqual(supersede.level_of(types.ModuleType("old")), 1)
        self.assertEqual(supersede.level_of(supersede), 2)

    def test_the_buckets_level(self):
        self.assertEqual(supersede.bucket_level(events.Events()), 0)
        with self.world():
            self.export()
            self.plan()
            found = events.read(self.store, timeline=self.old)
            self.assertEqual(supersede.bucket_level(found), 1)


def json_of(store, key):
    return store.get(key).decode()


_ = _next
