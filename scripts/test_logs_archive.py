"""Tests for the daily log archive (P1-T9c, ADR-014): deploy/logs/archive/archive.py and expire.py,
against a fake log store and a fake versioned, locked bucket (no network, no Docker).

Run: python -m unittest scripts/test_logs_archive.py (pre-commit hook "logs-archive-tests";
needs the `cryptography` package, pinned to the backend image's version)."""

import collections
import datetime as dt
import gzip
import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "deploy" / "logs" / "archive" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


A = load("archive")
E = load("expire")
A.time.sleep = lambda s: None  # retries without waiting
IST = A.IST


class FakeStore:
    """Lines at times; counts and exports like VictoriaLogs ([start, end))."""

    def __init__(self):
        self.lines = []  # (aware datetime, bytes)
        self.truncate = 0  # export this many lines fewer (a query that hit its time limit)
        self.arrive_during_export = []  # lines that appear after the first export of their hour

    def add(self, when, text):
        self.lines.append((when, json.dumps({"_time": when.isoformat(), "_msg": text}).encode() + b"\n"))

    def _in(self, start, end):
        return [b for t, b in self.lines if start <= t < end]

    def count(self, start, end):
        return len(self._in(start, end))

    def export(self, start, end, sink):
        got = self._in(start, end)
        if self.truncate and got:
            got = got[: max(0, len(got) - self.truncate)]
        for b in got:
            sink.write(b)
        late = [x for x in self.arrive_during_export if start <= x[0] < end]
        for x in late:
            self.arrive_during_export.remove(x)
            self.add(*x)
        return len(got)

    def day_counts(self, first, last):
        out = {}
        d = first
        while d <= last:
            s, e = A.day_bounds(d)
            n = self.count(s, e)
            if n:
                out[d] = n
            d += dt.timedelta(days=1)
        return out


class S3Error(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    """A versioned bucket with Object Lock (Governance, 400 days). writer=True: the archive's key
    (no deletes, no lock changes, nothing outside logs/)."""

    def __init__(self, writer=False, locked=True):
        self.versions = collections.defaultdict(list)  # key -> [(vid, data, retain_until)]
        self.n = 0
        self.writer = writer
        self.locked = locked
        self.fail_puts = []  # keys whose next put fails once (a dropped connection)
        self.corrupt_reads = []  # keys whose next read returns a changed copy
        self.calls = []

    @property
    def objects(self):
        return {k: v[-1][1] for k, v in self.versions.items() if v}

    def put_object(self, Bucket, Key, Body, ContentMD5=None, IfNoneMatch=None, **kw):
        self.calls.append(("put", Key))
        if self.writer and not Key.startswith("logs/"):
            raise S3Error("AccessDenied")
        if Key in self.fail_puts:
            self.fail_puts.remove(Key)
            raise ConnectionError("dropped")
        if IfNoneMatch == "*" and self.versions.get(Key):
            raise S3Error("PreconditionFailed")
        data = Body.read() if hasattr(Body, "read") else Body
        if ContentMD5 is not None:
            import base64

            assert ContentMD5 == base64.b64encode(hashlib.md5(data).digest()).decode()  # noqa: S324
        self.n += 1
        until = dt.datetime.now(dt.UTC) + dt.timedelta(days=400)
        self.versions[Key].append((f"v{self.n}", data, until))

    def _find(self, Key, VersionId=None):
        vs = self.versions.get(Key) or []
        if not vs:
            raise S3Error("NoSuchKey")
        if VersionId is None:
            return vs[-1]
        for v in vs:
            if v[0] == VersionId:
                return v
        raise S3Error("NoSuchVersion")

    def get_object(self, Bucket, Key, VersionId=None):
        vid, data, _ = self._find(Key, VersionId)
        if Key in self.corrupt_reads:
            self.corrupt_reads.remove(Key)
            data = data[:-2] + b"x\n"
        return {"Body": io.BytesIO(data)}

    def head_object(self, Bucket, Key, VersionId=None):
        vid, data, until = self._find(Key, VersionId)
        return {"ContentLength": len(data), "VersionId": vid, "ObjectLockRetainUntilDate": until}

    def delete_object(self, Bucket, Key, VersionId=None, BypassGovernanceRetention=False):
        self.calls.append(("delete", Key))
        if self.writer or VersionId is None or not BypassGovernanceRetention:
            raise S3Error("AccessDenied")
        vs = self.versions[Key]
        self.versions[Key] = [v for v in vs if v[0] != VersionId]

    def put_object_retention(self, **kw):
        raise S3Error("AccessDenied")

    def put_object_lock_configuration(self, **kw):
        raise S3Error("AccessDenied")

    def get_object_lock_configuration(self, Bucket):
        rule = {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 400}} if self.locked else {}
        return {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled" if self.locked else "", "Rule": rule}}

    def get_bucket_versioning(self, Bucket):
        return {"Status": "Enabled"}

    def get_paginator(self, name):
        s3 = self

        class P:
            def paginate(self, Bucket, Prefix):
                if name == "list_objects_v2":
                    yield {"Contents": [{"Key": k} for k in sorted(s3.objects) if k.startswith(Prefix)]}
                else:
                    yield {"Versions": [{"Key": k, "VersionId": v[0], "Size": len(v[1]), "LastModified": 0}
                                        for k, vs in sorted(s3.versions.items()) if k.startswith(Prefix) for v in vs]}

        return P()


def ist(y, m, d, h=0, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=IST)


D = dt.date


class Base(unittest.TestCase):
    def setUp(self):
        self.key = Fernet.generate_key().decode()
        self.sealer = A.Sealer(self.key)
        self.store = FakeStore()
        self.s3 = FakeS3()
        self.bucket = A.Bucket(self.s3, "ihr-log-archive")
        self.status = A.Status()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_pass(self, now, grace_h=1, recheck=180):
        return A.run_pass(self.store, self.bucket, self.sealer, self.status, self.tmp.name,
                          dt.timedelta(hours=grace_h), recheck, 180, now=now)

    def held(self):
        return A.Holdings(self.bucket, self.sealer)

    def manifests(self):
        h = self.held()
        self.assertEqual(h.bad, [])
        return h.manifests

    def problems(self, **kw):
        out = io.StringIO()
        n = A.check(self.bucket, self.sealer, out=out, **kw)
        return n, out.getvalue()

    def forge(self, day, rev, **change):
        """A manifest with a valid MAC (someone holding the server's archive key)."""
        latest = self.held().latest()[day][1]
        m = {**latest, "revision": rev, "object": A.object_key(day, rev), **change}
        m["mac"] = self.sealer.mac(m)
        return m

    def put_revision(self, day, rev, lines, previous):
        """A whole revision written with the archive key: object and authentic manifest."""
        raw = b"".join(lines)
        sealed = io.BytesIO()
        self.sealer.encrypt_file(io.BytesIO(gzip.compress(raw)), sealed)
        blob = sealed.getvalue()
        hours = [0] * 24
        for ln in lines:
            t = dt.datetime.fromisoformat(json.loads(ln)["_time"])
            hours[t.hour] += 1
        m = {"format": A.FORMAT, "day": day.isoformat(), "revision": rev, "timezone": "+05:30",
             "lines": len(lines), "hours": hours, "object": A.object_key(day, rev), "bytes": len(blob),
             "sha256": hashlib.sha256(blob).hexdigest(), "encryption": "x", "previous": previous,
             "created": "2027-01-01T00:00:00+05:30"}
        m["mac"] = self.sealer.mac(m)
        self.s3.put_object(Bucket="b", Key=A.object_key(day, rev), Body=blob)
        self.s3.put_object(Bucket="b", Key=A.manifest_key(day, rev), Body=A.manifest_bytes(m))
        return hashlib.sha256(A.manifest_bytes(m)).hexdigest()


class Days(Base):
    def test_india_day_edges(self):
        s, e = A.day_bounds(D(2026, 11, 10))
        self.assertEqual(s.astimezone(dt.UTC).isoformat(), "2026-11-09T18:30:00+00:00")
        self.assertEqual(e - s, dt.timedelta(days=1))
        self.store.add(ist(2026, 11, 10, 23, 59), "last")
        self.store.add(ist(2026, 11, 11, 0, 0), "next")
        self.store.add(ist(2026, 11, 10, 0, 0), "first")
        done, err = self.run_pass(ist(2026, 11, 12, 2))
        self.assertIsNone(err)
        by_day = {d: m for d, _, m, _ in self.manifests()}
        self.assertEqual(by_day[D(2026, 11, 10)]["lines"], 2)
        self.assertEqual(by_day[D(2026, 11, 11)]["lines"], 1)
        self.assertEqual((by_day[D(2026, 11, 10)]["hours"][0], by_day[D(2026, 11, 10)]["hours"][23]), (1, 1))

    def test_waits_for_the_grace_period(self):
        self.store.add(ist(2026, 11, 11, 12), "x")
        self.assertEqual(self.run_pass(ist(2026, 11, 12, 0, 30))[0], [])
        self.assertEqual(self.run_pass(ist(2026, 11, 12, 1, 5))[0], [(D(2026, 11, 11), 1)])

    def test_never_today(self):
        self.store.add(ist(2026, 11, 12, 1), "today")
        self.assertEqual(self.run_pass(ist(2026, 11, 12, 23))[0], [])


class Archive(Base):
    def test_encrypted_with_our_key_and_reads_back(self):
        for h in range(5):
            self.store.add(ist(2026, 11, 10, h), f"line {h} asha")
        self.run_pass(ist(2026, 11, 11, 3))
        ((_, _, m, _),) = self.manifests()
        blob = self.s3.objects[m["object"]]
        self.assertNotIn(b"asha", blob)
        self.assertEqual(hashlib.sha256(blob).hexdigest(), m["sha256"])
        # Independent of archive.py: each line a Fernet token of our key, a gzip inside
        tokens = [t for t in blob.split(b"\n") if t]
        plain = b"".join(Fernet(self.key).decrypt(t) for t in tokens)
        self.assertEqual(plain[:2], b"\x1f\x8b")
        lines = gzip.decompress(plain).splitlines()
        self.assertEqual(len(lines), 5)
        self.assertIn(b"line 4 asha", lines[4])
        with self.assertRaises(InvalidToken):
            Fernet(Fernet.generate_key()).decrypt(tokens[0])
        out = io.BytesIO()
        A.get_day(self.bucket, self.sealer, D(2026, 11, 10), out=out)
        self.assertEqual(out.getvalue().count(b"\n"), 5)
        self.assertEqual(self.problems(deep=True)[0], 0)

    def test_manifests_are_chained_by_their_links(self):
        for d in (9, 10):
            self.store.add(ist(2026, 11, d, 10), f"d{d}")
        self.run_pass(ist(2026, 11, 11, 3))
        ms = {d: (m, sha) for d, _, m, sha in self.manifests()}
        self.assertIsNone(ms[D(2026, 11, 9)][0]["previous"])
        self.assertEqual(ms[D(2026, 11, 10)][0]["previous"], ms[D(2026, 11, 9)][1])
        self.assertEqual(self.held().head, ms[D(2026, 11, 10)][1])

    def test_a_changed_manifest_is_not_trusted(self):
        self.store.add(ist(2026, 11, 9, 10), "d9")
        self.run_pass(ist(2026, 11, 10, 3))
        key = A.manifest_key(D(2026, 11, 9), 1)
        forged = json.loads(self.s3.objects[key])
        forged["lines"] = 0
        self.s3.versions[key].append(("vx", A.manifest_bytes(forged), None))
        self.assertEqual(self.held().bad, [key])

    def test_malformed_manifests_are_reported_and_archiving_goes_on(self):
        self.store.add(ist(2026, 11, 9, 10), "d9")
        self.store.add(ist(2026, 11, 10, 10), "d10")
        junk = {A.manifest_key(D(2026, 1, 1), 1): b"[1]",
                A.manifest_key(D(2026, 1, 2), 1): b'{"format": "ihr-log-archive/1", "mac": 7}',
                A.manifest_key(D(2026, 1, 3), 1): b'{"format": "ihr-log-archive/1", "mac": "\\u00e9"}',
                A.manifest_key(D(2026, 1, 4), 1): b"\xff\xfe"}
        for k, v in junk.items():
            self.s3.put_object(Bucket="b", Key=k, Body=v)
        done, err = self.run_pass(ist(2026, 11, 11, 3))
        self.assertIsNone(err)
        self.assertEqual([d for d, _ in done], [D(2026, 11, 9), D(2026, 11, 10)])
        self.assertIn(f"ihr_logs_archive_bad_manifests {len(junk)}", self.status.render().decode())
        n, text = self.problems()
        self.assertEqual(n, len(junk))
        self.assertEqual(text.count("not authentic"), len(junk))

    def test_a_manifest_copied_under_another_name_is_not_trusted(self):
        self.store.add(ist(2026, 11, 9, 10), "d9")
        self.run_pass(ist(2026, 11, 10, 3))
        raw = self.s3.objects[A.manifest_key(D(2026, 11, 9), 1)]
        other = A.manifest_key(D(2026, 11, 9), 7)
        self.s3.put_object(Bucket="b", Key=other, Body=raw)
        self.assertEqual(self.held().bad, [other])

    def test_a_replaced_object_or_a_missing_manifest_is_found(self):
        for d in (8, 9, 10):
            self.store.add(ist(2026, 11, d, 10), f"d{d}")
        self.run_pass(ist(2026, 11, 11, 3))
        self.assertEqual(self.problems()[0], 0)
        okey = A.object_key(D(2026, 11, 9), 1)
        self.s3.versions[okey].append(("vx", self.s3.objects[okey] + b"x", None))
        n, text = self.problems()
        self.assertGreater(n, 0)
        self.assertIn("SHA-256 differs", text)
        self.s3.versions[okey].pop()
        del self.s3.versions[A.manifest_key(D(2026, 11, 9), 1)]
        n, text = self.problems()
        self.assertIn("missing", text)

    def test_a_forked_chain_is_found(self):
        self.store.add(ist(2026, 11, 9, 10), "d9")
        self.run_pass(ist(2026, 11, 10, 3))
        head = self.held().head
        line = json.dumps({"_time": ist(2026, 11, 10, 10).isoformat(), "_msg": "x"}).encode() + b"\n"
        self.put_revision(D(2026, 11, 10), 1, [line], head)
        self.put_revision(D(2026, 11, 11), 1, [line], head)
        self.assertIn("forks", self.problems()[1])

    def test_late_lines_make_a_new_revision_the_old_one_stays(self):
        self.store.add(ist(2026, 11, 10, 10), "on time")
        self.run_pass(ist(2026, 11, 11, 3))
        self.store.add(ist(2026, 11, 10, 11), "late")
        done, err = self.run_pass(ist(2026, 11, 11, 4))
        self.assertIsNone(err)
        self.assertEqual(done, [(D(2026, 11, 10), 2)])
        self.assertIn(A.object_key(D(2026, 11, 10), 1), self.s3.objects)
        self.assertEqual(self.problems(deep=True)[0], 0)
        self.assertEqual(self.run_pass(ist(2026, 11, 11, 5))[0], [])
        err = io.StringIO()
        A.get_day(self.bucket, self.sealer, D(2026, 11, 10), out=io.BytesIO(), err=err)
        self.assertIn("2 revisions", err.getvalue())

    def test_late_lines_weeks_later_still_make_a_revision(self):
        self.store.add(ist(2026, 10, 1, 10), "on time")
        self.run_pass(ist(2026, 10, 2, 3))
        self.store.add(ist(2026, 10, 1, 11), "after a 3-week outage")
        done, _ = self.run_pass(ist(2026, 10, 23, 3))
        self.assertIn((D(2026, 10, 1), 2), done)

    def test_fewer_lines_than_archived_is_reported_not_rearchived(self):
        self.store.add(ist(2026, 11, 10, 10), "a")
        self.store.add(ist(2026, 11, 10, 11), "b")
        self.run_pass(ist(2026, 11, 11, 3))
        self.store.lines.pop()
        self.assertEqual(self.run_pass(ist(2026, 11, 11, 4))[0], [])
        self.assertIn("ihr_logs_archive_shrunk_days 1", self.status.render().decode())

    def test_a_truncated_export_is_refused(self):
        self.store.add(ist(2026, 11, 10, 10), "a")
        self.store.add(ist(2026, 11, 10, 10, 30), "b")
        self.store.truncate = 1
        done, err = self.run_pass(ist(2026, 11, 11, 3))
        self.assertEqual(done, [])
        self.assertIn("export and count disagree", err)
        self.assertEqual(self.s3.objects, {})
        self.assertIn("ihr_logs_archive_last_run_ok 0", self.status.render().decode())

    def test_lines_arriving_during_the_export_are_caught(self):
        self.store.add(ist(2026, 11, 10, 10), "a")
        self.store.arrive_during_export = [(ist(2026, 11, 10, 10, 5), "arrived while exporting")]
        done, err = self.run_pass(ist(2026, 11, 11, 3))
        self.assertIsNone(err)
        ((_, _, m, _),) = self.manifests()
        self.assertEqual(m["lines"], 2)  # the hour was tried again with both lines

    def test_a_corrupted_read_back_stops_before_the_manifest(self):
        self.store.add(ist(2026, 11, 10, 10), "a")
        self.s3.corrupt_reads.append(A.object_key(D(2026, 11, 10), 1))
        done, err = self.run_pass(ist(2026, 11, 11, 3))
        self.assertIn("SHA-256 differs", err)
        self.assertNotIn(A.manifest_key(D(2026, 11, 10), 1), self.s3.objects)

    def test_an_interrupted_upload_does_not_block_the_archive(self):
        """The object is stored, the manifest upload fails: the name r1 is taken for good (lock,
        If-None-Match). The next pass archives the day as r2 and goes on."""
        for d in (9, 10, 11):
            self.store.add(ist(2026, 11, d, 10), f"d{d}")
        self.s3.fail_puts.append(A.manifest_key(D(2026, 11, 10), 1))
        done, err = self.run_pass(ist(2026, 11, 12, 3))
        self.assertIsNotNone(err)
        self.assertEqual(done, [(D(2026, 11, 9), 1)])
        done, err = self.run_pass(ist(2026, 11, 12, 4))
        self.assertIsNone(err)
        self.assertEqual(done, [(D(2026, 11, 10), 2), (D(2026, 11, 11), 1)])
        self.assertEqual(self.problems(deep=True)[0], 0)

    def test_a_name_taken_by_someone_else_moves_to_the_next_revision(self):
        self.store.add(ist(2026, 11, 10, 10), "a")
        self.s3.put_object(Bucket="b", Key=A.object_key(D(2026, 11, 10), 1), Body=b"squatted")
        self.s3.put_object(Bucket="b", Key=A.object_key(D(2026, 11, 10), 2), Body=b"squatted")
        done, err = self.run_pass(ist(2026, 11, 11, 3))
        self.assertIsNone(err)
        self.assertEqual(done, [(D(2026, 11, 10), 3)])

    def test_an_empty_day_after_archiving_began_is_recorded(self):
        self.store.add(ist(2026, 11, 9, 10), "a")
        self.store.add(ist(2026, 11, 11, 10), "c")
        self.run_pass(ist(2026, 11, 12, 3))
        days = {d: m["lines"] for d, _, m, _ in self.manifests()}
        self.assertEqual(days, {D(2026, 11, 9): 1, D(2026, 11, 10): 0, D(2026, 11, 11): 1})

    def test_metrics_and_lock_state(self):
        self.store.add(ist(2026, 11, 10, 10), "a")
        self.run_pass(ist(2026, 11, 11, 3))
        text = self.status.render().decode()
        end = int(A.day_bounds(D(2026, 11, 10))[1].timestamp())
        self.assertIn(f"ihr_logs_archive_last_day_end_timestamp_seconds {end}", text)
        self.assertIn("ihr_logs_archive_last_run_ok 1", text)
        self.assertIn("ihr_logs_archive_bucket_locked 1", text)
        self.s3.locked = False
        self.assertEqual(A.lock_state(self.bucket), 0)


class Revisions(Base):
    """A later revision must hold every line of the one before (someone with the server's keys
    could otherwise replace a day by a cleaned copy)."""

    def setUp(self):
        super().setUp()
        for i in range(3):
            self.store.add(ist(2026, 11, 10, 10, i), f"line {i}")
        self.run_pass(ist(2026, 11, 11, 3))
        self.r1 = sorted(b for t, b in self.store.lines)

    def test_a_revision_with_fewer_lines_is_found_cheaply(self):
        self.put_revision(D(2026, 11, 10), 2, self.r1[:2], self.held().head)
        n, text = self.problems()
        self.assertIn("fewer lines in some hour than r1", text)

    def test_a_revision_with_a_replaced_line_is_found_by_the_deep_check(self):
        swapped = self.r1[:2] + [json.dumps({"_time": ist(2026, 11, 10, 10, 2).isoformat(), "_msg": "innocent"}).encode() + b"\n"]
        self.put_revision(D(2026, 11, 10), 2, swapped, self.held().head)
        self.assertEqual(self.problems()[0], 0)  # same counts: only the content shows it
        n, text = self.problems(deep=True)
        self.assertIn("drops 1 line(s) of r1", text)

    def test_a_genuine_superset_passes(self):
        more = self.r1 + [json.dumps({"_time": ist(2026, 11, 10, 11).isoformat(), "_msg": "late"}).encode() + b"\n"]
        self.put_revision(D(2026, 11, 10), 2, more, self.held().head)
        self.assertEqual(self.problems(deep=True)[0], 0)


class Erasure(Base):
    def erase(self, now, days, **kw):
        mac = A.erasure_mac_key(self.key)
        return E.erase(self.s3, "ihr-log-archive", mac, now, days, out=io.StringIO(), **kw)

    def test_never_fewer_than_400_days(self):
        with self.assertRaises(SystemExit):
            self.erase(dt.datetime.now(dt.UTC), 399)

    def test_cutoff_record_first_and_chain_still_valid(self):
        for d in (1, 2, 3):
            self.store.add(ist(2026, 11, d, 10), f"d{d}")
        self.run_pass(ist(2026, 11, 4, 3))
        # day 1 gains a line after days 2-3 were archived: r2 sits later in the chain
        self.store.add(ist(2026, 11, 1, 11), "late")
        self.store.add(ist(2026, 11, 4, 10), "d4")
        self.run_pass(ist(2026, 11, 5, 3))
        self.assertEqual(self.problems(deep=True)[0], 0)
        now = ist(2026, 11, 5, 12).astimezone(dt.UTC)
        days, failed = self.erase(now, 3, allow_short=True)  # cut-off 2 Nov: day 1 (both revisions) goes
        self.assertEqual((days, failed), ([D(2026, 11, 1)], []))
        self.assertFalse([k for k in self.s3.objects if "2026-11-01" in k])
        first_delete = next(i for i, c in enumerate(self.s3.calls) if c[0] == "delete")
        first_record = next(i for i, c in enumerate(self.s3.calls) if c == ("put", c[1]) and c[1].startswith("erasure/"))
        self.assertLess(first_record, first_delete)
        n, text = self.problems(deep=True)
        self.assertEqual(n, 0, text)
        self.assertIn("NOTE", text)  # an erasure inside 400 days (a proof)

    def test_the_cutoff_day_itself_stays(self):
        self.store.add(ist(2026, 11, 2, 10), "d2")
        self.run_pass(ist(2026, 11, 3, 3))
        days, _ = self.erase(ist(2026, 11, 5, 12).astimezone(dt.UTC), 3, allow_short=True)
        self.assertEqual(days, [])

    def test_a_version_still_locked_is_not_touched(self):
        self.store.add(ist(2026, 11, 2, 10), "d2")
        self.run_pass(ist(2026, 11, 3, 3))
        # 401 days later by the name, but the store says its lock runs for months yet
        days, _ = self.erase(ist(2027, 12, 8, 12).astimezone(dt.UTC), 400)
        self.assertIn(A.object_key(D(2026, 11, 2), 1), self.s3.objects)

    def test_an_erasure_record_that_is_not_ours_is_not_trusted(self):
        for d in (1, 2):
            self.store.add(ist(2026, 11, d, 10), f"d{d}")
        self.run_pass(ist(2026, 11, 3, 3))
        first = A.manifest_key(D(2026, 11, 1), 1)
        sha = hashlib.sha256(self.s3.objects[first]).hexdigest()
        del self.s3.versions[first]
        record = {"format": A.ERASURE_FORMAT, "erased": [{"sha256": sha, "previous": None}],
                  "older_than_days": 400, "mac": "0" * 64}
        self.s3.put_object(Bucket="b", Key="erasure/x.json", Body=json.dumps(record).encode())
        n, text = self.problems()
        self.assertIn("erasure/x.json", text)
        self.assertIn("missing", text)

    def test_day_of(self):
        self.assertEqual(E.day_of("logs/2026/11/2026-11-10.r1.manifest.json"), D(2026, 11, 10))
        self.assertEqual(E.day_of("logs/ihr-key-check/2026-11-10T101010.probe"), D(2026, 11, 10))
        self.assertIsNone(E.day_of("erasure/2027-12-14T000000.json"))
        self.assertIsNone(E.day_of("logs/x/notaday.json"))


class Probe(Base):
    def test_the_writer_key_passes_the_probe(self):
        writer = A.Bucket(FakeS3(writer=True), "ihr-log-archive")
        out = io.StringIO()
        self.assertEqual(A.probe(writer, out=out), 0, out.getvalue())

    def test_an_unlocked_bucket_fails_the_probe(self):
        writer = A.Bucket(FakeS3(writer=True, locked=False), "ihr-log-archive")
        out = io.StringIO()
        self.assertGreater(A.probe(writer, out=out), 0)
        self.assertIn("FAIL  the bucket is locked", out.getvalue())


class Format(unittest.TestCase):
    def test_many_chunks(self):
        key = Fernet.generate_key().decode()
        s = A.Sealer(key)
        src = io.BytesIO(gzip.compress(b"".join(b'{"a":"%s"}\n' % os.urandom(16).hex().encode() for _ in range(100000))))
        dst = io.BytesIO()
        s.encrypt_file(src, dst)
        dst.seek(0)
        self.assertGreater(dst.getvalue().count(b"\n"), 1)
        with tempfile.TemporaryFile() as work, A.open_plain(s, dst, work) as gz:
            self.assertEqual(sum(1 for _ in gz), 100000)

    def test_keys(self):
        self.assertEqual(A.parse_key("logs/2026/11/2026-11-10.r2.jsonl.gz.enc"), (D(2026, 11, 10), 2, "object"))
        self.assertIsNone(A.parse_key("logs/2026/12/2026-11-10.r2.manifest.json"))  # wrong folder
        self.assertIsNone(A.parse_key("logs/2026/11/2026-11-10.r0.manifest.json"))
        self.assertIsNone(A.parse_key("other/2026/11/2026-11-10.r1.manifest.json"))


if __name__ == "__main__":
    unittest.main()
