"""Tests for the nightly per-hospital dumps (P1-T22a): deploy/dumps/dumper.py and expire.py, with the
shared deploy/lib (sealer, bucket, chain), against a fake database, fake pg_dump and a fake
versioned, locked bucket (no network, no Docker).

Run: python -m unittest scripts/test_dumps.py (pre-commit hook "dumps-tests"; needs the
`cryptography` package).

The class Live runs only where Postgres's programs and psycopg are (the dumper image):
    scripts/test-dumps-live.sh
It starts a throwaway server inside that container and proves, with real pg_dump and
pg_restore: one snapshot for the manifest and the dump while writers keep writing, a held
hospital skipped, a locked one reported, the role check, the night's schema-only restore."""

import base64
import collections
import contextlib
import datetime as dt
import gzip
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet

ROOT = Path(os.environ.get("IHR_DUMPS_ROOT", Path(__file__).resolve().parent.parent))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


D = load("dumper", ROOT / "deploy" / "dumps" / "dumper.py")
X = load("dump_expire", ROOT / "deploy" / "dumps" / "expire.py")
A = load("archive_for_dumps", ROOT / "deploy" / "logs" / "archive" / "archive.py")
import sealer  # noqa: E402 (on the path the modules above put there)

IST = D.IST
UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"
UUID_C = "33333333-3333-4333-8333-333333333333"


class S3Error(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    """A versioned bucket with Object Lock (Governance, 30 days). writer=True: the dumper's key
    (adds, reads and lists versions under dumps/, reads erasure/, never deletes or unlocks). Like
    a store that does not enforce conditional writes, it takes a write without If-None-Match as
    a new version (review S1). lost_reply: names whose next write is stored and then answered
    with 412, as a client's retry after a lost reply meets If-None-Match (review S4/C6)."""

    def __init__(self, writer=False, locked=True, days=30):
        self.versions = collections.defaultdict(list)  # key -> [(vid, data or None (a delete marker), retain_until, modified)]
        self.n = 0
        self.writer = writer
        self.locked = locked
        self.days = days
        self.corrupt_reads = []
        self.uploads = {}
        self.clock = None  # LastModified of the next writes (tests of the expiry)
        self.lost_reply = set()
        self.events = None  # a list to append ("put", key) to (ordering tests)

    @property
    def objects(self):
        return {k: v[-1][1] for k, v in self.versions.items() if v and v[-1][1] is not None}

    def _store(self, key, data):
        self.n += 1
        now = (self.clock or dt.datetime.now(dt.UTC)) + dt.timedelta(microseconds=self.n)
        self.versions[key].append((f"v{self.n}", data, now + dt.timedelta(days=self.days), now))
        if self.events is not None:
            self.events.append(("put", key))
        return f"v{self.n}"

    def hide(self, key):
        """A delete marker (what a plain delete leaves on a versioned bucket)."""
        self.n += 1
        now = dt.datetime.now(dt.UTC) + dt.timedelta(microseconds=self.n)
        self.versions[key].append((f"m{self.n}", None, None, now))

    def _taken(self, Key):
        return bool(self.versions.get(Key)) and self.versions[Key][-1][1] is not None

    def put_object(self, Bucket, Key, Body, ContentMD5=None, IfNoneMatch=None, **kw):
        if self.writer and not Key.startswith("dumps/"):
            raise S3Error("AccessDenied")
        if self.writer and kw.get("ObjectLockRetainUntilDate"):
            raise S3Error("AccessDenied")
        if IfNoneMatch == "*" and self._taken(Key):
            raise S3Error("PreconditionFailed")
        data = Body.read() if hasattr(Body, "read") else Body
        if ContentMD5 is None:
            raise S3Error("InvalidRequest")  # a locked bucket wants an integrity header
        assert ContentMD5 == base64.b64encode(hashlib.md5(data).digest()).decode()  # noqa: S324
        vid = self._store(Key, data)
        if Key in self.lost_reply:
            self.lost_reply.discard(Key)
            raise S3Error("PreconditionFailed")  # stored; the reply lost; the retry meets If-None-Match
        return {"VersionId": vid}

    def create_multipart_upload(self, Bucket, Key, ContentType=None):
        if self.writer and not Key.startswith("dumps/"):
            raise S3Error("AccessDenied")
        uid = f"u{len(self.uploads) + 1}"
        self.uploads[uid] = {}
        return {"UploadId": uid}

    def upload_part(self, Bucket, Key, UploadId, PartNumber, Body, ContentMD5):
        assert ContentMD5 == base64.b64encode(hashlib.md5(Body).digest()).decode()  # noqa: S324
        self.uploads[UploadId][PartNumber] = Body
        return {"ETag": f'"{hashlib.md5(Body).hexdigest()}"'}  # noqa: S324

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload, IfNoneMatch=None):
        if IfNoneMatch == "*" and self._taken(Key):
            raise S3Error("PreconditionFailed")
        parts = self.uploads.pop(UploadId)
        vid = self._store(Key, b"".join(parts[p["PartNumber"]] for p in MultipartUpload["Parts"]))
        if Key in self.lost_reply:
            self.lost_reply.discard(Key)
            raise S3Error("PreconditionFailed")
        return {"VersionId": vid}

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self.uploads.pop(UploadId, None)

    def _find(self, Key, VersionId=None):
        vs = self.versions.get(Key) or []
        if not vs:
            raise S3Error("NoSuchKey")
        if VersionId is None:
            if vs[-1][1] is None:
                raise S3Error("NoSuchKey")
            return vs[-1]
        for v in vs:
            if v[0] == VersionId and v[1] is not None:
                return v
        raise S3Error("NoSuchVersion")

    def get_object(self, Bucket, Key, VersionId=None):
        if self.writer and not (Key.startswith("dumps/") or Key.startswith("erasure/")):
            raise S3Error("AccessDenied")
        _, data, _, _ = self._find(Key, VersionId)
        if Key in self.corrupt_reads:
            self.corrupt_reads.remove(Key)
            data = data[:-2] + bytes([data[-2] ^ 1]) + b"\n"  # always a different byte
        return {"Body": io.BytesIO(data)}

    def head_object(self, Bucket, Key, VersionId=None):
        vid, data, until, _ = self._find(Key, VersionId)
        return {"ContentLength": len(data), "VersionId": vid, "ObjectLockRetainUntilDate": until}

    def delete_object(self, Bucket, Key, VersionId=None, BypassGovernanceRetention=False):
        if self.writer or VersionId is None or not BypassGovernanceRetention:
            raise S3Error("AccessDenied")
        self.versions[Key] = [v for v in self.versions[Key] if v[0] != VersionId]
        if not self.versions[Key]:
            del self.versions[Key]

    def put_object_retention(self, **kw):
        raise S3Error("AccessDenied")

    def put_object_lock_configuration(self, **kw):
        raise S3Error("AccessDenied")

    def get_object_lock_configuration(self, Bucket):
        rule = {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": self.days}} if self.locked else {}
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
                    # As S3 lists them: each name's versions newest first; markers apart
                    keys = [k for k in sorted(s3.versions) if k.startswith(Prefix)]
                    yield {"Versions": [{"Key": k, "VersionId": v[0], "Size": len(v[1]), "LastModified": v[3]}
                                        for k in keys for v in reversed(s3.versions[k]) if v[1] is not None],
                           "DeleteMarkers": [{"Key": k, "VersionId": v[0], "LastModified": v[3]}
                                             for k in keys for v in reversed(s3.versions[k]) if v[1] is None]}

        return P()


# ---- a fake database and pg_dump --------------------------------------------------------------


class FakeConn:
    def __init__(self, log, name):
        self.log, self.name = log, name
        self.closed = False

    def close(self):
        if not self.closed:  # as psycopg: closing a closed session does nothing
            self.closed = True
            self.log.append(("close", self.name))


class FakeProc:
    def __init__(self, data, rc=0, err=b""):
        self.stdout = io.BytesIO(data)
        self.rc = rc
        self.err = err

    def wait(self):
        return self.rc


class FakeSource:
    def __init__(self, hospitals):
        self.list = hospitals
        self.busy = set()
        self.held_in_snapshot = set()
        self.problems = []
        self.log = []
        self.snaps = 0
        self.sessions = 0

    def role_problems(self):
        return list(self.problems)

    def hospitals(self):
        return list(self.list)

    def lock(self, slug, wait):
        if slug in self.busy:
            return None
        self.log.append(("lock", slug))
        self.sessions += 1
        return FakeConn(self.log, "lock:" + slug)

    def snapshot(self, h, conn):
        assert isinstance(conn, FakeConn) and conn.name == "lock:" + h.slug and not conn.closed, \
            "the snapshot is taken on the lock's own session"
        self.snaps += 1
        snap = f"00000003-{self.snaps:08X}-1"
        facts = {"status": h.status, "held": h.slug in self.held_in_snapshot,
                 "snapshot": {"at": "2027-01-04T03:30:00+05:30", "xmin": "900", "xmax": "905"},
                 "server_version": "18.6", "schema_fingerprint": "f" * 64, "migrations": 712,
                 "migrations_sha256": "a" * 64,
                 "audit": {"seal_seq": 41, "seal_hash": "b" * 64, "seal_upper_txid": "880",
                           "max_entry_id": 1912, "max_entry_txid": "899"},
                 "sequences": {"emr_patient_id_seq": 10, "ihr_audit_entry_id_seq": 1912},
                 "relations": 7, "schema_bytes": 1000, "extensions": [["pg_trgm", "1.6"], ["plpgsql", "1.0"]]}
        rows = {"ihr_platform_hospital": [{"id": 1, "slug": h.slug, "name": "Alpha Hospital"}],
                "ihr_platform_staffinvitation": [{"id": 5, "email": "priya.sharma@example.in", "first_name": "Priya"}]}
        self.log.append(("snapshot", h.slug, snap))
        return snap, facts, rows


class FakeTools:
    """pg_dump writes a recognisable archive; pg_restore --list reads it back as a TOC, and like
    the real one stops after its first chunk (the table of contents; review C3)."""

    def __init__(self):
        self.calls = []
        self.fail = set()
        self.foreign = set()
        self.delay = 0
        self.read_all_calls = 0

    def dump(self, schema, snapshot, compress, stderr):
        self.calls.append((schema, snapshot, compress))
        if self.delay:
            time.sleep(self.delay)
        if schema in self.fail:
            stderr.write(b"pg_dump: error: query failed\n")
            return FakeProc(b"", rc=1)
        body = json.dumps({"schema": schema, "snapshot": snapshot, "pad": "x" * 3000}).encode()
        return FakeProc(b"PGDMP" + body * 400)  # about 1.2 MB: two sealed chunks

    def read_all(self, chunks):
        self.read_all_calls += 1
        for _ in chunks:
            pass

    def toc(self, chunks):
        data = next(iter(chunks))  # the table of contents only, as pg_restore --list
        assert data.startswith(b"PGDMP")
        schema = json.loads(data[5:data.index(b"}") + 1])["schema"]
        lines = [";", "; Archive created", f"5; 2615 16390 SCHEMA - {schema} postgres",
                 f"233; 1259 16391 TABLE {schema} emr_patient postgres",
                 f"4521; 0 16391 TABLE DATA {schema} emr_patient postgres",
                 f"240; 1259 16400 SEQUENCE {schema} emr_patient_id_seq postgres",
                 f"4600; 0 0 SEQUENCE SET {schema} emr_patient_id_seq postgres",
                 f"300; 2606 16500 FK CONSTRAINT {schema} emr_patient emr_patient_fk postgres"]
        if schema in self.foreign:
            lines.append("301; 1259 16600 TABLE public evil postgres")
        return lines


class FakeScratch:
    def __init__(self, relations=7):
        self.relations = relations
        self.restored = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def restore_schema(self, schema, extensions, chunks):
        next(iter(chunks))  # schema-only: the table of contents, as pg_restore --schema-only
        self.restored.append(schema)
        return self.relations


def re_search(pattern, text):
    import re

    m = re.search(pattern, text)
    assert m, pattern
    return m.group(1)


def hosp(slug, uuid, schema, status="active", held=False, since=0, schema_bytes=0):
    return D.Hospital(uuid, slug, schema, status, held, "window" if held else "", since, schema_bytes)


def ist(y, m, d, h=0, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=IST)


WALL = ist(2027, 6, 1, 12)


class Base(unittest.TestCase):
    def setUp(self):
        self.key = Fernet.generate_key().decode()
        self.sealer = sealer.Sealer(self.key, D.PURPOSE)
        self.s3 = FakeS3(writer=True)
        self.bucket = D.locked_bucket.Bucket(self.s3, "ihr-dumps")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = D.Config({"IHR_DUMP_WORKDIR": self.tmp.name, "IHR_DUMP_LOCK_WAIT": "0"})
        self.source = FakeSource([hosp("alpha", UUID_A, "h_alpha00001"), hosp("beta", UUID_B, "h_beta000001")])
        self.tools = FakeTools()
        self.status = D.Status()
        self.scratch = FakeScratch()
        self.out = io.StringIO()
        patcher = mock.patch("sys.stdout", self.out)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The wall clock outside a run: after every date these tests use (a run sets its own)
        clock = mock.patch.object(D, "now_ist", return_value=WALL)
        clock.start()
        self.addCleanup(clock.stop)

    def run_at(self, when, **kw):
        with mock.patch.object(D, "now_ist", return_value=when):
            return D.run(self.cfg, self.source, self.tools, self.bucket, self.sealer, self.status, now=when,
                         scratch_factory=lambda: self.scratch, **kw)

    def lines(self):
        return [ln for ln in self.out.getvalue().splitlines() if ln.startswith("ihr-dump:")]

    def held(self):
        return D.Holdings(self.bucket, self.sealer)

    def manifests(self, uuid=UUID_A):
        h = self.held()
        self.assertEqual(h.bad, [])
        return [h.by_hospital[uuid][sha] for sha in h.order.get(uuid, [])]

    def check(self, **kw):
        out = io.StringIO()
        n, failed = D.check(self.bucket, self.sealer, self.tools, out=out, **kw)
        return n, failed, out.getvalue()


MON = ist(2027, 1, 4, 3, 30)  # a Monday
SUN = ist(2027, 1, 10, 3, 30)


class Dumps(Base):
    def test_every_due_hospital_dumped_encrypted_with_its_own_key(self):
        r = self.run_at(MON)
        self.assertEqual(r, {"alpha": "ok", "beta": "ok"})
        self.assertEqual(self.lines(), [])
        m = self.manifests()[0]
        self.assertEqual((m["kind"], m["date"], m["revision"]), ("daily", "2027-01-04", 1))
        blob = self.s3.objects[m["dump"]["key"]]
        self.assertNotIn(b"PGDMP", blob)
        self.assertNotIn(b"h_alpha00001", blob)
        plain = b"".join(sealer.Sealer(self.key, D.PURPOSE).decrypt_lines(io.BytesIO(blob)))
        self.assertTrue(plain.startswith(b"PGDMP"))
        self.assertEqual(hashlib.sha256(plain).hexdigest(), m["dump"]["plain_sha256"])
        self.assertGreater(blob.count(b"\n"), 1, "one MiB a token: several tokens")
        other = sealer.Sealer(Fernet.generate_key().decode(), D.PURPOSE)
        with self.assertRaises(Exception):
            list(other.decrypt_lines(io.BytesIO(blob)))

    def test_manifests_hold_no_personal_data(self):
        self.run_at(MON)
        for k, data in self.s3.objects.items():
            # Manifests: no part of a name or address in any case. The sealed objects are random
            # base64, where any five letters turn up now and then: there the plaintext itself,
            # which holds characters base64 never has, must not appear
            if k.endswith(".manifest.json"):
                self.assertNotIn(b"priya", data.lower(), k)
            self.assertNotIn(b"priya.sharma@example.in", data.lower(), k)
            self.assertNotIn(b"Alpha Hospital", data, k)
        m = self.manifests()[0]
        self.assertEqual(m["public"]["tables"], {"ihr_platform_hospital": 1, "ihr_platform_staffinvitation": 1})
        sealed = io.BytesIO(self.s3.objects[m["public"]["key"]])
        rows = json.loads(gzip.decompress(b"".join(self.sealer.decrypt_lines(sealed))))
        self.assertEqual(rows["ihr_platform_staffinvitation"][0]["email"], "priya.sharma@example.in")

    def test_the_dump_is_of_the_snapshot_the_manifest_was_read_in(self):
        self.run_at(MON)
        snaps = {s[1]: s[2] for s in self.source.log if s[0] == "snapshot"}
        for schema, snapshot, compress in self.tools.calls:
            slug = "alpha" if schema == "h_alpha00001" else "beta"
            self.assertEqual(snapshot, snaps[slug])
            self.assertEqual(compress, "zstd")
        # The snapshot is taken on the lock's own session, which stays open until pg_dump is
        # done, then closes (review C7: two sessions a dump, not three)
        events = [e for e in self.source.log if e[1] in ("alpha", "lock:alpha")]
        self.assertEqual([e[0] for e in events], ["lock", "snapshot", "close"])
        self.assertEqual(self.source.sessions, 2)  # one session of ours per hospital

    def test_the_lock_is_released_before_the_upload(self):
        """Review C1: the hospital's lock (the fleet runner's and provisioning's) ends with
        pg_dump; the upload, the read-back and the manifest run without it."""
        self.s3.events = self.source.log
        self.run_at(MON)
        log = self.source.log
        released = log.index(("close", "lock:alpha"))
        first_put = min(i for i, e in enumerate(log) if e[0] == "put" and UUID_A in e[1])
        self.assertLess(released, first_put, log)

    def test_pg_dump_arguments(self):
        with mock.patch.object(D.subprocess, "Popen") as popen:
            D.Tools({"PGUSER": "ihr_dump", "PGPASSWORD": "pw"}).dump("h_alpha00001", "snap", "zstd", None)
        args, kw = popen.call_args
        self.assertEqual(args[0], ["pg_dump", "-Fc", "--compress=zstd", "-n", "h_alpha00001", "--strict-names",
                                   "--snapshot=snap", "--no-owner", "--no-acl", "--lock-wait-timeout=60s"])
        self.assertEqual(kw["env"]["PGPASSWORD"], "pw")
        self.assertNotIn("pw", " ".join(args[0]))
        self.assertFalse(any(k.startswith("IHR_") for k in kw["env"]))

    def test_chained_per_hospital_and_weekly_on_sunday(self):
        self.run_at(MON)
        self.run_at(ist(2027, 1, 5, 3, 30))
        self.run_at(SUN)
        ms = self.manifests()
        self.assertEqual([m["kind"] for m in ms], ["daily", "daily", "weekly"])
        self.assertIsNone(ms[0]["previous"])
        raws = [D.record_bytes(m) for m in ms]
        self.assertEqual(ms[1]["previous"], hashlib.sha256(raws[0]).hexdigest())
        self.assertEqual(ms[2]["previous"], hashlib.sha256(raws[1]).hexdigest())
        self.assertTrue(ms[2]["dump"]["key"].startswith(f"dumps/weekly/{UUID_A}/2027-01-10.r1"))
        self.assertEqual(len(self.manifests(UUID_B)), 3)
        n, failed, text = self.check(deep=True, every=True)
        self.assertEqual(n, 0, text)

    def test_a_second_run_the_same_night_dumps_nothing_again(self):
        self.run_at(MON)
        self.assertEqual(self.run_at(ist(2027, 1, 4, 5, 0)), {})
        self.assertEqual(len(self.tools.calls), 2)
        r = self.run_at(ist(2027, 1, 4, 6, 0), only=["alpha"])
        self.assertEqual(r, {"alpha": "ok"})
        self.assertEqual([m["revision"] for m in self.manifests()], [1, 2])

    def test_held_hospitals_are_skipped_and_reported(self):
        self.source.list.append(hosp("gamma", UUID_C, "h_gamma00001", held=True))
        r = self.run_at(MON)
        self.assertEqual(r["gamma"], "held")
        self.assertIn("ihr-dump: hospital=gamma outcome=held", self.lines())
        self.assertNotIn("h_gamma00001", [c[0] for c in self.tools.calls])
        self.assertFalse(any(UUID_C in k for k in self.s3.objects))
        self.assertEqual(self.status.get("hospitals_held"), 1)

    def test_held_when_its_snapshot_is_taken_is_skipped_too(self):
        self.source.held_in_snapshot.add("beta")
        r = self.run_at(MON)
        self.assertEqual(r, {"alpha": "ok", "beta": "held"})
        self.assertNotIn("h_beta000001", [c[0] for c in self.tools.calls])
        self.assertIn("ihr-dump: hospital=beta outcome=held", self.lines())

    def test_provisioning_and_archived_are_not_dumped(self):
        self.source.list += [hosp("delta", UUID_C, "h_delta00001", status="provisioning"),
                             hosp("eps", "44444444-4444-4444-8444-444444444444", "h_eps0000001", status="archived")]
        self.source.list[1].status = "offboarding"
        r = self.run_at(MON)
        self.assertEqual(r, {"alpha": "ok", "beta": "ok"})
        self.assertEqual(self.status.get("hospitals"), 2)

    def test_a_locked_hospital_is_reported_the_others_go_on(self):
        self.source.busy.add("alpha")
        r = self.run_at(MON)
        self.assertEqual(r, {"alpha": "lock", "beta": "ok"})
        self.assertIn("ihr-dump: hospital=alpha outcome=failed reason=lock", self.lines())
        self.assertEqual(self.status.get("hospitals_failed"), 1)

    def test_a_failed_pg_dump_writes_nothing(self):
        self.tools.fail.add("h_alpha00001")
        r = self.run_at(MON)
        self.assertEqual(r["alpha"], "dump")
        self.assertFalse(any(UUID_A in k for k in self.s3.objects))
        self.assertIn("ihr-dump: hospital=alpha outcome=failed reason=dump", self.lines())

    def test_a_dump_with_another_schema_in_it_is_refused_before_its_manifest(self):
        self.tools.foreign.add("h_alpha00001")
        r = self.run_at(MON)
        self.assertEqual(r["alpha"], "verify")
        self.assertFalse(any(UUID_A in k and k.endswith(".manifest.json") for k in self.s3.objects))

    def test_a_corrupted_read_back_stops_before_the_manifest(self):
        key = D.object_keys("daily", UUID_A, MON.date(), 1)[0]
        self.s3.corrupt_reads.append(key)
        r = self.run_at(MON)
        self.assertEqual(r["alpha"], "verify")
        self.assertFalse(any(k.endswith(".manifest.json") and UUID_A in k for k in self.s3.objects))
        # The next run uses the next revision (the name is taken)
        r = self.run_at(ist(2027, 1, 4, 6, 0), only=["alpha"])
        self.assertEqual(r, {"alpha": "ok"})
        self.assertEqual(self.manifests()[0]["revision"], 2)

    def test_a_name_taken_by_someone_else_moves_to_the_next_revision(self):
        key = D.object_keys("daily", UUID_A, MON.date(), 1)[0]
        self.s3.put_object(Bucket="b", Key=key, Body=b"x", ContentMD5=base64.b64encode(hashlib.md5(b"x").digest()).decode())  # noqa: S324
        self.run_at(MON)
        self.assertEqual(self.manifests()[0]["revision"], 2)
        n, _, text = self.check()
        self.assertEqual(n, 0, text)

    def test_the_night_check_restores_each_new_dump_schema_only(self):
        self.run_at(MON)
        self.assertEqual(sorted(self.scratch.restored), ["h_alpha00001", "h_beta000001"])
        self.scratch.relations = 6
        self.scratch.restored.clear()
        r = self.run_at(ist(2027, 1, 5, 3, 30))
        self.assertEqual(r, {"alpha": "check", "beta": "check"})
        self.assertIn("ihr-dump: hospital=alpha outcome=failed reason=check", self.lines())
        self.assertEqual(self.status.get("check_problems"), 2)

    def test_the_quiet_window_and_the_disk(self):
        r = self.run_at(ist(2027, 1, 4, 2, 0))
        self.assertEqual(r, {"alpha": "window", "beta": "window"})
        with mock.patch.object(D.Reserve, "free", return_value=100 * 2**20):
            r = self.run_at(MON)
        self.assertEqual(r, {"alpha": "disk", "beta": "disk"})
        self.assertIn("ihr-dump: hospital=beta outcome=failed reason=disk", self.lines())

    def run_with_clock(self, start, **patches):
        """A run whose wall clock is start until a patched step moves it (clock["t"])."""
        clock = {"t": start}
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(D, "now_ist", side_effect=lambda: clock["t"]))
            for target, attr, make in patches.values():
                stack.enter_context(mock.patch.object(target, attr, make(clock)))
            return D.run(self.cfg, self.source, self.tools, self.bucket, self.sealer, self.status, now=start,
                         scratch_factory=lambda: self.scratch)

    def test_a_wait_for_room_into_the_quiet_window_dumps_nothing(self):
        """Review (second fix): the window was checked only before reserve.take(), which can
        wait for another dump's room; a dump that started waiting at 01:40 ran inside the
        release window and the backup. Checked again after the wait, before the lock."""
        real_take = D.Reserve.take

        def make(clock):
            def take(reserve, n):
                ok = real_take(reserve, n)
                clock["t"] = ist(2027, 1, 4, 1, 50)  # the room came free inside the window
                return ok
            return take

        r = self.run_with_clock(ist(2027, 1, 4, 1, 40), take=(D.Reserve, "take", make))
        self.assertEqual(r, {"alpha": "window", "beta": "window"})
        self.assertEqual(self.tools.calls, [])
        self.assertFalse([e for e in self.source.log if e[0] == "lock"], self.source.log)  # not even locked
        self.assertIn("ihr-dump: hospital=alpha outcome=failed reason=window", self.lines())

    def test_a_wait_for_the_lock_into_the_quiet_window_dumps_nothing(self):
        """Its lock (up to IHR_DUMP_LOCK_WAIT) can run into the window too: checked after it,
        and the lock's session closed."""
        real_lock = FakeSource.lock
        sessions = []

        def make(clock):
            def lock(source, slug, wait):
                conn = real_lock(source, slug, wait)
                sessions.append(conn)
                clock["t"] = ist(2027, 1, 4, 1, 50)
                return conn
            return lock

        r = self.run_with_clock(ist(2027, 1, 4, 1, 40), lock=(FakeSource, "lock", make))
        self.assertEqual(r, {"alpha": "window", "beta": "window"})
        self.assertEqual(self.tools.calls, [])
        self.assertTrue(sessions)  # the first locked; the other may meet the window before its lock
        self.assertTrue(all(c.closed for c in sessions))
        self.assertEqual(self.source.snaps, 0)

    def test_the_disk_check_follows_the_size_model_and_the_last_dump(self):
        cfg = D.Config({})
        big = hosp("alpha", UUID_A, "h_alpha00001", schema_bytes=10 * 2**30)
        # a first dump: 0.30 of the schema's bytes on disk, plus the margin
        self.assertEqual(D.disk_need(cfg, big, None), int(10 * 2**30 * 0.30) + D.DISK_MARGIN)
        # a last dump larger than the model's share wins, with a fifth more
        last = {"dump": {"bytes": 5 * 2**30}, "public": {"bytes": 0}}
        self.assertEqual(D.disk_need(cfg, big, last), int(5 * 2**30 * 1.2) + D.DISK_MARGIN)
        small = hosp("beta", UUID_B, "h_beta000001")
        self.assertEqual(D.disk_need(cfg, small, None), D.DISK_MARGIN)

    def test_a_refused_role_dumps_nothing(self):
        self.source.problems = ["connected as postgres (postgres), not ihr_dump"]
        self.assertIsNone(self.run_at(MON))
        self.assertEqual(self.tools.calls, [])
        self.assertEqual(self.status.get("role_ok"), 0)
        self.assertIn("ihr-dump: refused role", self.out.getvalue())

    def test_role_problems(self):
        good = ("ihr_dump", "ihr_dump", False, False, True, ["pg_read_all_data"], False, False, False, 0, 0, False)
        self.assertEqual(D.role_problems(good), [])
        for i, bad in ((0, "postgres"), (2, True), (3, True), (5, ["pg_read_all_data", "ihr_owner"]), (6, True),
                       (8, True), (9, 1), (10, 3), (11, True)):
            row = list(good)
            row[i] = bad
            self.assertTrue(D.role_problems(tuple(row)), (i, bad))

    def test_a_lost_reply_to_the_manifest_does_not_fork_the_chain(self):
        """Review S4/C6: the manifest is stored, its reply lost, and the client's retry meets
        If-None-Match. The same bytes are there: done, not a taken name (no second revision, no
        fork, no DumpNotAuthentic)."""
        self.s3.lost_reply.add(D.object_keys("daily", UUID_A, MON.date(), 1)[2])
        self.s3.lost_reply.add(D.object_keys("daily", UUID_B, MON.date(), 1)[0])  # and a dump object's
        r = self.run_at(MON)
        self.assertEqual(r, {"alpha": "ok", "beta": "ok"})
        self.assertFalse(any(".r2." in k for k in self.s3.versions), sorted(self.s3.versions))
        h = self.held()
        self.assertEqual((h.bad, h.problems), ([], []))
        self.run_at(ist(2027, 1, 5, 3, 30))
        n, _, text = self.check(deep=True, every=True)
        self.assertEqual(n, 0, text)

    def test_two_large_dumps_wait_for_each_other_instead_of_failing(self):
        """Review C2: the volume fits one large dump at a time. The second waits for the first
        (it is refused only when it would not fit alone)."""
        self.cfg.parallel = 2
        self.tools.delay = 0.3  # both workers start before the first is done
        self.source.list = [hosp("alpha", UUID_A, "h_alpha00001", schema_bytes=10 * 2**30),
                            hosp("beta", UUID_B, "h_beta000001", schema_bytes=10 * 2**30)]
        one = D.disk_need(self.cfg, self.source.list[0], None)
        with mock.patch.object(D.Reserve, "free", return_value=int(one * 1.5)):
            r = self.run_at(MON)
        self.assertEqual(r, {"alpha": "ok", "beta": "ok"})
        with mock.patch.object(D.Reserve, "free", return_value=one - 1):
            r = self.run_at(ist(2027, 1, 4, 6, 0), only=["alpha"])
        self.assertEqual(r, {"alpha": "disk"})

    def test_the_largest_dump_and_the_need_follow_the_bucket_not_the_last_run(self):
        """Review C2: a catch-up run that dumps nothing never lowers them (DumpDiskLow)."""
        self.run_at(MON)
        largest = self.status.get("largest_bytes")
        m = self.manifests()[0]
        self.assertEqual(largest, m["dump"]["bytes"] + m["public"]["bytes"])
        self.assertEqual(self.status.get("work_need_bytes"), self.cfg.parallel * (int(largest * 1.2) + D.DISK_MARGIN))
        self.assertEqual(self.run_at(ist(2027, 1, 4, 5, 0), catch_up=True), {})
        self.assertEqual(self.status.get("largest_bytes"), largest)

    def test_the_oldest_good_dump_moves_on_during_the_run(self):
        """Review C4: by the night's check (after every dump, before the run ends) the gauge
        already shows tonight's dumps, not last night's."""
        self.run_at(MON)
        tue = ist(2027, 1, 5, 3, 30)
        seen = []
        status = self.status

        class Watching(FakeScratch):
            def restore_schema(self, schema, extensions, chunks):
                seen.append(status.get("oldest_good_timestamp_seconds"))
                return super().restore_schema(schema, extensions, chunks)

        self.scratch = Watching()
        self.run_at(tue)
        self.assertEqual(set(seen), {int(tue.timestamp())})

    def test_two_sessions_a_dump_and_the_role_limit(self):
        """Review C7: the role's connection limit (scripts/staging.sh) holds every dump the
        dumper may run at once, two sessions each, and two more; a larger setting is refused."""
        self.assertEqual((D.SESSIONS_PER_DUMP, D.MAX_PARALLEL), (2, 4))
        with self.assertRaises(SystemExit):
            D.Config({"IHR_DUMP_PARALLEL": str(D.MAX_PARALLEL + 1)})
        self.assertEqual(D.Config({"IHR_DUMP_PARALLEL": str(D.MAX_PARALLEL)}).parallel, D.MAX_PARALLEL)
        staging = ROOT / "scripts" / "staging.sh"
        if not staging.exists():
            self.skipTest("scripts/staging.sh is not here (the dumper image's run)")
        limit = int(re_search(r"ALTER ROLE ihr_dump WITH LOGIN .*CONNECTION LIMIT (\d+) ", staging.read_text()))
        self.assertEqual(limit, D.SESSIONS_PER_DUMP * D.MAX_PARALLEL + 2)

    def test_role_says_when_it_cannot_connect(self):
        class NoSession:
            def role_problems(self):
                raise D.CannotConnect('FATAL:  too many connections for role "ihr_dump"')

        class Wrong:
            def role_problems(self):
                return ["a superuser"]

        out = io.StringIO()
        self.assertEqual(D.role_cmd(NoSession(), out), 2)
        self.assertIn("cannot connect as ihr_dump", out.getvalue())
        self.assertEqual(D.role_cmd(Wrong(), io.StringIO()), 1)
        self.assertEqual(D.role_cmd(self.source, io.StringIO()), 0)

    def test_the_lock_key_is_the_provisioning_runners(self):
        """Review C10: ihr_platform/provisioning/runner.py's lock_key has the same pinned vector
        (ihr_platform.tests.test_provisioning)."""
        self.assertEqual(D.lock_key("alpha"), -8154903275597228642)

    def test_parallel_runs_never_share_a_revision(self):
        self.cfg.parallel = 2
        self.source.list = [hosp(f"h{i:02d}x", f"{i:08x}-1111-4111-8111-111111111111", f"h_{i:010d}") for i in range(6)]
        r = self.run_at(MON)
        self.assertEqual(set(r.values()), {"ok"})
        n, _, text = self.check(deep=True)
        self.assertEqual(n, 0, text)


class Authenticity(Base):
    def setUp(self):
        super().setUp()
        self.run_at(MON)
        self.run_at(ist(2027, 1, 5, 3, 30))

    def mkey(self, m):
        return D.object_keys(m["kind"], m["hospital"], dt.date.fromisoformat(m["date"]), m["revision"])[2]

    def put_plain(self, key, data):
        """A write with the dumper's key and no If-None-Match (what someone else holding it can send)."""
        self.s3.put_object(Bucket="ihr-dumps", Key=key, Body=data,
                           ContentMD5=base64.b64encode(hashlib.md5(data).digest()).decode())  # noqa: S324

    def test_a_changed_manifest_is_not_trusted(self):
        m = self.manifests()[1]
        key = self.mkey(m)
        vid, _, until, when = self.s3.versions[key][0]
        self.s3.versions[key][0] = (vid, D.record_bytes({**m, "relations": 1}), until, when)  # its only version changed
        h = self.held()
        self.assertIn(key, h.bad)
        n, _, text = self.check()
        self.assertGreater(n, 0, text)

    def test_a_manifest_copied_under_another_name_is_not_trusted(self):
        m = self.manifests()[0]
        key = D.object_keys("daily", UUID_A, dt.date(2027, 1, 6), 1)[2]
        self.s3.versions[key].append(("vY", D.record_bytes(m), None, dt.datetime.now(dt.UTC)))
        self.assertIn(key, self.held().bad)

    def test_a_log_archive_manifest_key_cannot_sign_a_dump(self):
        m = self.manifests()[0]
        logs = sealer.Sealer(self.key)  # the same key, the log archive's purpose
        self.assertNotEqual(logs.mac(m), m["mac"])
        self.assertNotEqual(logs.mac_key, self.sealer.mac_key)

    def test_the_manifest_records_the_stored_versions(self):
        m = self.manifests()[0]
        for part in ("dump", "public"):
            self.assertEqual(m[part]["version"], self.s3.head_object(Bucket="b", Key=m[part]["key"])["VersionId"])

    def test_a_server_holding_both_keys_cannot_replace_the_newest_dump_unseen(self):
        """Review S1: with the dumper's key and IHR_DUMP_KEY (a compromised server), the newest
        manifest re-written without If-None-Match and re-authenticated, its dump replaced with
        garbage of the same size. The store takes both as new versions; the check finds them
        (deep or not), the metrics raise DumpNotAuthentic, and every reader still takes the
        versions the dumper wrote."""
        m = self.manifests()[1]
        genuine = self.s3.objects[m["dump"]["key"]]
        plain = b"".join(self.sealer.decrypt_lines(io.BytesIO(genuine)))
        self.put_plain(m["dump"]["key"], os.urandom(len(genuine)))
        forged = {k: v for k, v in m.items() if k != "mac"}
        forged["relations"] = 1
        forged["mac"] = self.sealer.mac(forged)
        self.put_plain(self.mkey(m), D.record_bytes(forged))
        h = self.held()
        self.assertTrue(any(m["dump"]["key"] in p for p in h.problems), h.problems)
        self.assertTrue(any(self.mkey(m) in p for p in h.problems), h.problems)
        self.assertEqual(h.newest(UUID_A)["relations"], m["relations"])  # the first version is read
        for deep in (False, True):
            n, _, text = self.check(deep=deep)
            self.assertGreater(n, 0, text)
            self.assertIn("the dumper writes a name once", text)
        D.refresh(self.bucket, self.sealer, self.status, self.source.hospitals())
        self.assertGreater(self.status.get("bad_manifests"), 0)
        out = io.BytesIO()
        D.get(self.bucket, self.sealer, UUID_A, "latest", "dump", out)
        self.assertEqual(out.getvalue(), plain)

    def test_the_bucket_key_alone_cannot_replace_an_older_dump_unseen(self):
        """Review S1: the bucket key alone (a leak from tofu state): an older dump overwritten
        with garbage of the same size. The routine (non-deep) check now finds it."""
        m = self.manifests()[0]
        self.put_plain(m["public"]["key"], os.urandom(len(self.s3.objects[m["public"]["key"]])))
        n, _, text = self.check()
        self.assertGreater(n, 0, text)
        self.assertIn(m["public"]["key"], text)

    def test_a_dump_hidden_by_a_delete_marker_is_found(self):
        m = self.manifests()[1]
        self.s3.hide(self.mkey(m))
        n, _, text = self.check()
        self.assertGreater(n, 0, text)
        self.assertIn("delete marker", text)

    def test_a_replaced_object_is_found(self):
        m = self.manifests()[0]
        self.s3.versions[m["dump"]["key"]].append(("vZ", b"short", None, dt.datetime.now(dt.UTC)))
        n, _, text = self.check()
        self.assertGreater(n, 0, text)

    def test_a_same_size_change_in_place_is_found_by_the_deep_check(self):
        """A store that changed an object's only version (no new version): the sizes still
        match; the deep check reads it back."""
        m = self.manifests()[1]
        vid, data, until, when = self.s3.versions[m["dump"]["key"]][0]
        blob = bytearray(data)
        blob[10] ^= 1
        self.s3.versions[m["dump"]["key"]][0] = (vid, bytes(blob), until, when)
        n, _, _ = self.check()
        self.assertEqual(n, 0)
        n, failed, text = self.check(deep=True)
        self.assertGreater(n, 0, text)
        self.assertIn("SHA-256", text)

    def test_a_deleted_manifest_breaks_the_chain(self):
        m = self.manifests()[0]
        del self.s3.versions[D.object_keys("daily", UUID_A, MON.date(), 1)[2]]
        self.assertTrue(any("missing" in p for p in self.held().problems), m)

    def test_a_forked_chain_is_found(self):
        m = self.manifests()[1]
        fork = {**m, "revision": 2, "dump": {**m["dump"], "key": D.object_keys("daily", UUID_A, dt.date(2027, 1, 5), 2)[0]},
                "public": {**m["public"], "key": D.object_keys("daily", UUID_A, dt.date(2027, 1, 5), 2)[1]}}
        fork["mac"] = self.sealer.mac(fork)
        key = D.object_keys("daily", UUID_A, dt.date(2027, 1, 5), 2)[2]
        self.s3.versions[key].append(("vF", D.record_bytes(fork), None, dt.datetime.now(dt.UTC)))
        self.assertTrue(any("forks" in p for p in self.held().problems))

    def test_unknown_files_under_dumps_are_reported(self):
        self.s3.versions["dumps/daily/notes.txt"].append(("vN", b"x", None, dt.datetime.now(dt.UTC)))
        self.assertIn("dumps/daily/notes.txt", self.held().bad)


class Verify(Base):
    """What the read-back and the checks read (review C3, C2)."""

    def setUp(self):
        super().setUp()
        self.run_at(MON)
        self.m = self.manifests()[0]

    def replace_dump(self, blob, **fields):
        """The manifest's dump object replaced by blob, under a fresh name, with its sealed size
        and SHA-256 recorded as they are (a dump whose stored hash matches)."""
        key = D.object_keys("daily", UUID_A, MON.date(), 9)[0]
        vid = self.s3.put_object(Bucket="b", Key=key, Body=blob, IfNoneMatch="*",
                                 ContentMD5=base64.b64encode(hashlib.md5(blob).digest()).decode())["VersionId"]  # noqa: S324
        return {**self.m, "dump": {**self.m["dump"], "key": key, "version": vid, "bytes": len(blob),
                                   "sha256": hashlib.sha256(blob).hexdigest(), **fields}}

    def test_a_later_token_that_does_not_decrypt_fails(self):
        lines = self.s3.objects[self.m["dump"]["key"]].splitlines(keepends=True)
        self.assertGreater(len(lines), 1)
        lines[-1] = base64.urlsafe_b64encode(os.urandom(len(lines[-1]) * 3 // 4))[:len(lines[-1]) - 1] + b"\n"
        m = self.replace_dump(b"".join(lines))
        for scratch in (None, self.scratch):
            with self.subTest(scratch=scratch), self.assertRaises(Exception):
                D.verify_dump(self.bucket, self.sealer, self.tools, m, scratch)

    def test_a_plaintext_that_differs_from_its_manifest_fails(self):
        m = {**self.m, "dump": {**self.m["dump"], "plain_sha256": "0" * 64}}
        for scratch in (None, self.scratch):
            with self.subTest(scratch=scratch), self.assertRaisesRegex(RuntimeError, "decrypted object differs"):
                D.verify_dump(self.bucket, self.sealer, self.tools, m, scratch)

    def test_the_read_back_keeps_no_copy_on_disk(self):
        """Review C2: the nightly read-back streams; only the night's check and check --deep copy."""
        with mock.patch.object(D.tempfile, "TemporaryFile", side_effect=AssertionError("a copy on disk")):
            D.verify_dump(self.bucket, self.sealer, self.tools, self.m)

    def test_the_deep_check_reads_every_data_block(self):
        n, _, text = self.check(deep=True)
        self.assertEqual(n, 0, text)
        self.assertEqual(self.tools.read_all_calls, 2)  # alpha's and beta's newest

    def test_a_program_that_stops_reading_early(self):
        chunks = [b"x" * D.CHUNK] * 8
        with self.assertRaisesRegex(RuntimeError, "stopped reading"):
            D._feed(["head", "-c", "1"], iter(chunks))
        self.assertEqual(D._feed(["head", "-c", "1"], iter(chunks), early_exit=True), b"x")

    def test_get_fails_early_without_room_for_its_copy(self):
        """Review C8: get and the copying checks say there is no room before they fetch."""
        tiny = os.statvfs_result((4096, 4096, 100, 1, 1, 100, 1, 1, 0, 255))
        with mock.patch.object(D.os, "statvfs", return_value=tiny), self.assertRaisesRegex(RuntimeError, "MiB free"):
            D.get(self.bucket, self.sealer, UUID_A, "latest", "dump", io.BytesIO())


class Metrics(Base):
    def test_no_hospital_label_and_the_oldest_good_dump(self):
        self.source.list.append(hosp("gamma", UUID_C, "h_gamma00001", held=True, since=int(ist(2027, 1, 1).timestamp())))
        self.run_at(MON)
        text = self.status.render().decode()
        self.assertNotIn("hospital=", text)
        self.assertNotIn("alpha", text)
        self.assertNotIn(UUID_A, text)
        self.assertNotIn("{", text)  # no labels at all
        # gamma (held, never dumped) counts from when it went live
        self.assertEqual(self.status.get("oldest_good_timestamp_seconds"), int(ist(2027, 1, 1).timestamp()))
        D.refresh(self.bucket, self.sealer, self.status, self.source.hospitals(), now=ist(2027, 1, 5, 6, 0))
        self.assertEqual(self.status.get("stale_hospitals"), 3)
        D.refresh(self.bucket, self.sealer, self.status, self.source.hospitals(), now=ist(2027, 1, 4, 6, 0))
        self.assertEqual(self.status.get("stale_hospitals"), 1)
        self.assertEqual(self.status.get("bucket_locked"), 1)

    def test_the_fixed_line(self):
        D.emit("alpha", "failed", "dump")
        D.emit("Evil\nihr-dump: hospital=x", "failed", "dump")
        D.emit("beta", "failed", "made-up")
        D.say("text\nihr-dump: hospital=forged outcome=failed reason=dump")
        self.assertEqual(self.lines(), ["ihr-dump: hospital=alpha outcome=failed reason=dump",
                                        "ihr-dump: hospital=invalid-slug outcome=failed reason=dump",
                                        "ihr-dump: hospital=beta outcome=failed reason=error"])

    def test_status_survives_a_restart(self):
        path = os.path.join(self.tmp.name, "status.json")
        s = D.Status(path)
        s.set(last_run_end_timestamp_seconds=123, running=1)
        one_off = D.Status(path)  # dumps-now while the service runs: its run stays "running"
        self.assertEqual(one_off.get("running"), 1)
        again = D.Status(path, fresh=True)  # the service restarted: the killed run is cleared
        self.assertEqual(again.get("last_run_end_timestamp_seconds"), 123)
        self.assertEqual(again.get("running"), 0)

    def test_scrapers_resolved_off_the_request_path(self):
        calls = []

        def slow(name, port):
            calls.append(name)
            time.sleep(0.5)
            raise OSError("no such name")

        allowed = D.Scrapers(["prometheus"], resolve=slow)
        started = time.monotonic()
        self.assertTrue(allowed.allows("127.0.0.1"))  # the health check never waits on a name
        self.assertFalse(allowed.allows("172.18.0.9"))
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertEqual(calls, [])
        allowed.resolve = lambda name, port: [(None, None, None, None, ("172.18.0.9", 0))]
        allowed.refresh()
        self.assertTrue(allowed.allows("172.18.0.9"))
        self.assertFalse(allowed.allows("not an address"))

    def test_a_one_off_run_shows_in_the_services_metrics(self):
        """dumps-now and dumps-check run in their own process: the service's scrape reads the
        status file they wrote."""
        path = os.path.join(self.tmp.name, "status.json")
        service = D.Status(path)
        service.set(hospitals_failed=1, check_problems=1)
        one_off = D.Status(path)
        one_off.set(hospitals_failed=0, hospitals_held=1, check_problems=0)
        text = service.render().decode()
        self.assertIn("ihr_dump_hospitals_failed 0\n", text)
        self.assertIn("ihr_dump_hospitals_held 1\n", text)
        self.assertIn("ihr_dump_check_problems 0\n", text)

    def test_drill_mark(self):
        D.drill_mark(self.tmp.name, UUID_A, "ok", self.status)
        self.assertGreater(self.status.get("last_drill_timestamp_seconds"), 0)
        with self.assertRaises(SystemExit):
            D.drill_mark(self.tmp.name, "x", "ok", self.status)

    def test_schedule(self):
        cfg = D.Config({})
        self.assertFalse(D.scheduled_due(cfg, ist(2027, 1, 4, 3, 0), ""))
        self.assertTrue(D.scheduled_due(cfg, ist(2027, 1, 4, 3, 30), "2027-01-03"))
        self.assertFalse(D.scheduled_due(cfg, ist(2027, 1, 4, 3, 30), "2027-01-04"))
        self.assertTrue(cfg.in_quiet(dt.time(2, 0)))
        self.assertFalse(cfg.in_quiet(dt.time(3, 30)))
        with self.assertRaises(SystemExit):
            D.Config({"IHR_DUMP_TIME": "3:30"})


class Toc(unittest.TestCase):
    S = "h_alpha00001"

    def lines(self, *extra):
        return [";", f"5; 2615 16390 SCHEMA - {self.S} postgres", f"6; 1259 1 TABLE {self.S} t postgres",
                f"7; 0 1 TABLE DATA {self.S} t postgres", f"8; 0 0 SEQUENCE SET {self.S} s postgres",
                f"9; 0 0 SEQUENCE OWNED BY {self.S} s postgres", f"10; 0 0 COMMENT {self.S} FUNCTION f() postgres", *extra]

    def test_ours(self):
        self.assertEqual(D.toc_problems(self.lines(), self.S), [])

    def test_refused(self):
        for extra in ("11; 3079 1 EXTENSION - pg_trgm ", f"11; 1259 2 TABLE public x postgres",
                      "11; 2615 3 SCHEMA - h_other00001 postgres", "11; 0 0 EVENT TRIGGER - et postgres",
                      f"11; 0 0 NEW KIND {self.S} x postgres", "garbage"):
            self.assertTrue(D.toc_problems(self.lines(extra), self.S), extra)
        self.assertTrue(D.toc_problems([f"6; 1259 1 TABLE {self.S} t postgres"], self.S))  # no SCHEMA entry
        self.assertTrue(D.toc_problems([], self.S))


class Shared(unittest.TestCase):
    def test_multipart_upload_with_its_own_if_none_match(self):
        s3 = FakeS3(writer=True)
        b = D.locked_bucket.Bucket(s3, "b", multipart_bytes=10)
        with tempfile.NamedTemporaryFile() as f:
            f.write(os.urandom(150 * 1024))
            f.flush()
            with mock.patch.object(D.locked_bucket, "PART", 64 * 1024):
                b.put_file("dumps/x", f.name, "application/octet-stream")
                self.assertEqual(s3.objects["dumps/x"], Path(f.name).read_bytes())
                with self.assertRaises(Exception) as e:
                    b.put_file("dumps/x", f.name, "application/octet-stream")
            self.assertTrue(D.taken_error(e.exception))
        self.assertEqual(s3.uploads, {})

    def test_the_log_archive_still_uses_the_same_sealer(self):
        self.assertIs(A.Sealer, sealer.Sealer)
        k = Fernet.generate_key().decode()
        self.assertEqual(A.Sealer(k).mac_key, sealer.derived_key(k, "ihr-log-archive manifest"))
        self.assertEqual(A.erasure_mac_key(k), sealer.derived_key(k, "ihr-log-archive erasure"))

    def test_a_pipe_still_seals_one_mib_a_token(self):
        k = Fernet.generate_key().decode()
        s = sealer.Sealer(k, D.PURPOSE)

        class Trickle(io.RawIOBase):
            def __init__(self, data):
                self.data = data

            def read(self, n=-1):
                n = min(n, 1000)
                out, self.data = self.data[:n], self.data[n:]
                return out

        data = os.urandom(3 * sealer.CHUNK + 5)
        dst = io.BytesIO()
        info = s.seal(Trickle(data), dst)
        self.assertEqual(dst.getvalue().count(b"\n"), 4)
        self.assertEqual(info["plain_bytes"], len(data))
        self.assertEqual(b"".join(s.decrypt_lines(io.BytesIO(dst.getvalue()))), data)


class EnforcingS3(FakeS3):
    """A store that refuses the dumper's writes without If-None-Match (AWS with infra/aws's policy)."""

    def put_object(self, Bucket, Key, Body, ContentMD5=None, IfNoneMatch=None, **kw):
        if self.writer and IfNoneMatch != "*":
            raise S3Error("AccessDenied")
        return super().put_object(Bucket, Key, Body, ContentMD5=ContentMD5, IfNoneMatch=IfNoneMatch, **kw)


class Probe(Base):
    def test_the_writer_key_passes_the_probe(self):
        out = io.StringIO()
        self.assertEqual(D.probe(self.bucket, out=out), 0, out.getvalue())
        # Review S1: a rewrite without If-None-Match, and which of the two holds against it
        self.assertIn("PASS  list the versions under dumps/", out.getvalue())
        self.assertIn("the store took it as a second version, which dumps-check reports (2 versions)", out.getvalue())
        out = io.StringIO()
        self.assertEqual(D.probe(D.locked_bucket.Bucket(EnforcingS3(writer=True), "b"), out=out), 0, out.getvalue())
        self.assertIn("PASS  replace it without If-None-Match: refused by the store", out.getvalue())

    def test_an_unlocked_bucket_or_a_deleting_key_fails_it(self):
        self.assertGreater(D.probe(D.locked_bucket.Bucket(FakeS3(writer=True, locked=False), "b"), out=io.StringIO()), 0)
        self.assertGreater(D.probe(D.locked_bucket.Bucket(FakeS3(writer=True, days=7), "b"), out=io.StringIO()), 0)
        admin = FakeS3(writer=False)  # may delete with the bypass: not a dumper key
        self.assertGreater(D.probe(D.locked_bucket.Bucket(admin, "b"), out=io.StringIO()), 0)


class Expire(Base):
    def setUp(self):
        super().setUp()
        self.admin = self.s3
        self.admin.writer = False
        self.mac = sealer.erasure_mac_key(self.key, D.PURPOSE)
        for day in range(1, 31):
            when = ist(2027, 1, 1, 3, 30) + dt.timedelta(days=day)
            self.admin.clock = when.astimezone(dt.UTC)
            self.run_at(when)
        self.admin.clock = None
        self.now = ist(2027, 2, 1, 12).astimezone(dt.UTC)

    def erase(self, **kw):
        out = io.StringIO()
        days = {"daily": 14, "weekly": 28, "probe": 30, **kw.pop("days", {})}
        return X.erase(self.admin, "ihr-dumps", self.mac, self.now, days, out=out, **kw), out.getvalue()

    def test_never_under_the_retention(self):
        with self.assertRaises(SystemExit):
            self.erase(days={"daily": 13})
        with self.assertRaises(SystemExit):
            self.erase(days={"weekly": 27})

    def test_record_first_then_the_chain_still_holds(self):
        (doomed, failed), text = self.erase()
        self.assertEqual(failed, [])
        kept = {m["date"] for m in self.manifests()}
        self.assertTrue(all(d >= "2027-01-18" or d in ("2027-01-03", "2027-01-10", "2027-01-17") for d in kept), kept)
        self.assertIn("2027-01-10", kept)  # a Sunday: four weeks
        self.assertNotIn("2027-01-05", kept)  # a night: two weeks
        self.assertNotIn("2027-01-02", kept)
        records = sorted(k for k in self.admin.objects if k.startswith("erasure/"))
        self.assertEqual(len(records), 2)
        h = self.held()
        self.assertEqual(h.bad, [])
        self.assertEqual(h.problems, [])
        n, _, out = self.check()
        self.assertEqual(n, 0, out)

    def test_a_name_older_than_its_object_is_not_erased_early(self):
        # Written today under a name dated weeks ago (by whoever held the writer key)
        key = f"dumps/daily/{UUID_C}/2026-12-01.r1.dump.enc"
        self.admin.clock = self.now
        self.admin.put_object(Bucket="b", Key=key, Body=b"x", ContentMD5=base64.b64encode(hashlib.md5(b"x").digest()).decode())  # noqa: S324
        (doomed, _), _ = self.erase(dry_run=True)
        self.assertNotIn(key, {d[0] for d in doomed})

    def test_an_erasure_record_that_is_not_ours_is_not_trusted(self):
        self.erase()
        fake = {"format": X.FORMAT, "erased": [], "mac": "0" * 64}
        self.admin.put_object(Bucket="b", Key="erasure/forged.json", Body=json.dumps(fake).encode(),
                              ContentMD5=base64.b64encode(hashlib.md5(json.dumps(fake).encode()).digest()).decode())  # noqa: S324
        self.assertIn("erasure/forged.json", self.held().bad)

    def test_files_not_ours_are_left_alone(self):
        other = "dumps/other/" + "thing"  # an object name, built so no scanner reads it as a key
        self.admin.put_object(Bucket="b", Key=other, Body=b"x",
                              ContentMD5=base64.b64encode(hashlib.md5(b"x").digest()).decode())  # noqa: S324
        (doomed, _), text = self.erase()
        self.assertIn("NOT OURS (left alone) dumps/other/thing", text)
        self.assertIn("dumps/other/thing", self.admin.objects)

    def test_a_dry_run_deletes_nothing(self):
        before = dict(self.admin.versions)
        (doomed, _), text = self.erase(dry_run=True)
        self.assertTrue(doomed)
        self.assertEqual(set(before), set(self.admin.versions))
        self.assertIn("dry run", text)

    def test_a_name_written_twice_is_left_alone_and_named(self):
        """Review S1: erasing by age would remove the original first and keep the replacement."""
        m = self.manifests()[0]
        self.admin.clock = self.now
        self.admin.put_object(Bucket="b", Key=m["dump"]["key"], Body=b"x" * 10,
                              ContentMD5=base64.b64encode(hashlib.md5(b"x" * 10).digest()).decode())  # noqa: S324
        self.admin.clock = None
        (doomed, _), text = self.erase()
        self.assertIn(f"SUSPECT {m['dump']['key']}", text)
        self.assertNotIn(m["dump"]["key"], {d[0] for d in doomed})
        self.assertEqual(len(self.admin.versions[m["dump"]["key"]]), 2)
        (doomed, _), text = self.erase(erase_suspect=True)
        self.assertIn(m["dump"]["key"], {d[0] for d in doomed})  # the original version, past its retention

    def sign(self, rec):
        body = sealer.record_bytes(sealer.signed(self.mac, rec))
        self.admin.put_object(Bucket="b", Key=f"erasure/{rec['erased_at'][:10]}-x.json", Body=body,
                              ContentMD5=base64.b64encode(hashlib.md5(body).digest()).decode())  # noqa: S324

    def test_an_authentic_record_erasing_inside_the_retention_is_a_problem(self):
        """Review S2: the erasure-record key can be derived on the server; a record that bridges
        the chain over dumps still inside their retention is never trusted silently."""
        m = self.manifests()[-1]  # the newest night's
        key = D.object_keys(m["kind"], UUID_A, dt.date.fromisoformat(m["date"]), m["revision"])[2]
        raw = self.admin.objects[key]
        self.sign({"format": X.FORMAT, "status": "started", "erased_at": ist(2027, 2, 1, 12).isoformat(),
                   "allow_short": False, "days": {"daily": 14, "weekly": 28}, "objects": [key],
                   "erased": [{"sha256": hashlib.sha256(raw).hexdigest(), "previous": m["previous"],
                               "hospital": UUID_A, "key": key}]})
        for vid, *_ in list(self.admin.versions[key]):
            self.admin.delete_object(Bucket="b", Key=key, VersionId=vid, BypassGovernanceRetention=True)
        h = self.held()
        self.assertTrue(any("inside its retention" in p for p in h.problems), h.problems)
        self.sign({"format": X.FORMAT, "status": "started", "erased_at": ist(2028, 1, 1).isoformat(),
                   "allow_short": False, "erased": []})
        self.assertTrue(any("in the future" in p for p in self.held().problems))

    def test_an_early_erasure_is_a_metric_of_its_own(self):
        """Review S3: --allow-short (a proof's) shows in monitoring, not only as a check's note.
        On a proof's stand-in it is only that: its two records are one erasure, and erasing
        inside the retention is what a proof does."""
        with mock.patch.dict(os.environ, {"IHR_DUMP_ENDPOINT": "http://s3-dumpsout:8333"}):
            self.erase(days={"daily": 1, "weekly": 1, "probe": 1}, allow_short=True)
            self.assertEqual(len([k for k in self.admin.objects if k.startswith("erasure/")]), 2)
            D.refresh(self.bucket, self.sealer, self.status, self.source.hospitals())
            self.assertEqual(self.status.get("early_erasures"), 1)
            self.assertEqual(self.status.get("bad_manifests"), 0)
            n, _, text = self.check()
        self.assertEqual(n, 0, text)
        self.assertIn("WARN  erasure/", text)

    def test_a_proofs_erasure_off_the_stand_in_is_a_problem(self):
        """Review S2: a record with allow_short is a proof's only on a proof's stand-in;
        anywhere else (a server's bucket) it is checked against the retention like any other."""
        self.erase(days={"daily": 1, "weekly": 1, "probe": 1}, allow_short=True)
        h = D.Holdings(self.bucket, self.sealer, proof=False)
        self.assertTrue(any("with --allow-short, off a proof's stand-in" in p for p in h.problems), h.problems)
        # One erasure (its record and its closing record): each manifest named once
        self.assertEqual(len(h.problems), len(set(h.problems)))
        self.assertEqual(len([p for p in h.problems if "2027-01-30.r1" in p and UUID_A in p]), 1, h.problems)
        self.assertEqual(len(h.early_erasures), 1)
        self.assertEqual(D.Holdings(self.bucket, self.sealer, proof=True).problems, [])
        self.assertTrue(any("inside its retention" in p for p in self.held().problems))  # here: no stand-in

    def test_one_completed_allow_short_record_is_still_checked_and_counted(self):
        """Review S2 bypass (second fix): one record with allow_short and status "completed",
        signed with the key a server can derive, erasing the newest night. It was neither a
        problem nor an early erasure (problems=[], early=[]); now it is both."""
        m = self.manifests()[-1]  # the newest night's
        key = D.object_keys(m["kind"], UUID_A, dt.date.fromisoformat(m["date"]), m["revision"])[2]
        raw = self.admin.objects[key]
        self.sign({"format": X.FORMAT, "status": "completed", "failed": [], "erased_at": ist(2027, 2, 1, 12).isoformat(),
                   "allow_short": True, "days": {"daily": 0, "weekly": 0}, "objects": [key],
                   "erased": [{"sha256": hashlib.sha256(raw).hexdigest(), "previous": m["previous"],
                               "hospital": UUID_A, "key": key}]})
        for vid, *_ in list(self.admin.versions[key]):
            self.admin.delete_object(Bucket="b", Key=key, VersionId=vid, BypassGovernanceRetention=True)
        h = D.Holdings(self.bucket, self.sealer, proof=False)
        self.assertTrue(any("inside its retention" in p for p in h.problems), h.problems)
        self.assertEqual(h.early_erasures, ["erasure/2027-02-01-x.json"])
        D.refresh(self.bucket, self.sealer, self.status, self.source.hospitals(), held=h)
        self.assertGreater(self.status.get("bad_manifests"), 0)  # DumpNotAuthentic
        self.assertEqual(self.status.get("early_erasures"), 1)  # DumpErasedEarly
        n, _, text = self.check()
        self.assertGreater(n, 0, text)

    def test_an_erasure_that_names_no_manifest_never_bridges_the_chain(self):
        """Review S2: the retention check reads the erased manifest's date from its name; an
        entry without one (or with a name that is not a manifest's) is a problem, and the chain
        is not followed through it."""
        m = self.manifests()[-1]
        key = D.object_keys(m["kind"], UUID_A, dt.date.fromisoformat(m["date"]), m["revision"])[2]
        sha = hashlib.sha256(self.admin.objects[key]).hexdigest()
        for name in (None, key.replace(".manifest.json", ".dump.enc"), "dumps/daily/x/2020-01-01.r1.manifest.json"):
            with self.subTest(name=name):
                for k in [k for k in self.admin.objects if k.startswith("erasure/")]:
                    del self.admin.versions[k]
                entry = {"sha256": sha, "previous": m["previous"], "hospital": UUID_A, **({"key": name} if name else {})}
                self.sign({"format": X.FORMAT, "status": "started", "erased_at": ist(2027, 2, 1, 12).isoformat(),
                           "allow_short": False, "erased": [entry]})
                h = self.held()
                self.assertTrue(any("without naming its manifest" in p for p in h.problems), h.problems)
                self.assertNotIn(sha, h.erased)

    def test_the_stand_ins(self):
        for endpoint, proof in (("http://s3:8333", True), ("http://s3-dumpsout:8333", True),
                                ("https://objects.example.in", False), ("http://s3:8334", False), (None, False)):
            self.assertEqual(D.on_stand_in({"IHR_DUMP_ENDPOINT": endpoint} if endpoint else {}), proof, endpoint)
        self.assertIn(X.STAND_IN, D.STAND_INS)
        self.assertEqual(D.erasure_name("erasure/2027-02-01T120000-done.json"), "erasure/2027-02-01T120000.json")
        self.assertEqual(D.erasure_name("erasure/2027-02-01T120000.json"), "erasure/2027-02-01T120000.json")

    def test_names_the_dumper_cannot_date_are_never_erased(self):
        """Review S2: expire.py takes only the dumper's own names (anything else is NOT OURS),
        so no record names a manifest the dumper could not date."""
        self.assertIsNotNone(X.kind_and_day(f"dumps/daily/{UUID_A}/2027-01-01.r1.manifest.json"))
        for odd in (f"dumps/daily/{UUID_A}/2027-01-01.r0.manifest.json", f"dumps/daily/{UUID_A}/2027-01-01.r01.dump.enc",
                    "dumps/daily/" + "-" * 36 + "/2027-01-01.r1.manifest.json", f"dumps/daily/{UUID_A}/2027-01-01.r12345.dump.enc"):
            self.assertIsNone(X.kind_and_day(odd), odd)
            self.assertIsNone(D.parse_key(odd), odd)

    def test_allow_short_is_refused_off_the_stand_in(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("IHR_")}
        script = str(ROOT / "deploy" / "dumps" / "expire.py")
        for endpoint, refused in (("https://objects.example.in", True), (None, True), (X.STAND_IN, False)):
            e = dict(env, **({"IHR_DUMP_ENDPOINT": endpoint} if endpoint else {}))
            out = subprocess.run([sys.executable, "-I", script, "--allow-short",
                                  "--daily-days", "0"], input="", capture_output=True, text=True, env=e)
            self.assertNotEqual(out.returncode, 0)
            self.assertEqual("refused: --allow-short" in out.stderr, refused, (endpoint, out.stderr))
        # Never abbreviated (review S3, second fix): argparse took `--allow` for --allow-short
        for flag in ("--allow", "--allow-s", "--a"):
            for endpoint in ("https://objects.example.in", X.STAND_IN):
                with self.subTest(flag=flag, endpoint=endpoint):
                    out = subprocess.run([sys.executable, "-I", script, flag, "--daily-days", "0"], input="",
                                         capture_output=True, text=True, env=dict(env, IHR_DUMP_ENDPOINT=endpoint))
                    self.assertEqual(out.returncode, 2, out.stderr)
                    self.assertIn("unrecognized arguments", out.stderr)

    def test_the_drill_clock_survives_expiry(self):
        """Review C5: after the owner's expiry the oldest dump held is at most about 35 days
        old; the drill's clock is the first dump ever, from the erasure records too."""
        first = self.manifests()[0]
        self.erase()
        D.refresh(self.bucket, self.sealer, self.status, self.source.hospitals())
        self.assertGreater(self.status.get("first_good_timestamp_seconds"), int(ist(2027, 1, 10).timestamp()))
        self.assertEqual(self.status.get("first_dump_ever_timestamp_seconds"),
                         int(ist(*map(int, first["date"].split("-"))).timestamp()))
        self.status.set(first_dump_ever_timestamp_seconds=100)  # never raised
        D.refresh(self.bucket, self.sealer, self.status, self.source.hospitals())
        self.assertEqual(self.status.get("first_dump_ever_timestamp_seconds"), 100)


STAGING = ROOT / "scripts" / "staging.sh"


@unittest.skipUnless(STAGING.exists(), "scripts/staging.sh is not here (the dumper image's run)")
class StagingScript(unittest.TestCase):
    """scripts/staging.sh's dump commands (read, and run with stubs; no Docker)."""

    @classmethod
    def setUpClass(cls):
        cls.text = STAGING.read_text()

    def func(self, name):
        import re

        m = re.search(r"^%s\(\) \{(.*?)^\}" % re.escape(name), self.text, re.S | re.M)
        self.assertTrue(m, name)
        return m.group(1)

    def test_up_and_a_release_stop_the_dumper_before_any_migration(self):
        """Review C1: a dump in flight holds its hospital's lock, which the start-up roles step
        and the fleet migration never wait for."""
        up = self.func("up")
        stop = '[[ -z "$(env_get IHR_DUMPS)" ]] || compose stop dumper'
        self.assertLess(up.index(stop), up.index("compose run --rm --no-deps -T migrate\n"))
        self.assertLess(up.index("compose run --rm --no-deps -T migrate\n"), up.index("|| dumps_up"))
        rel = self.func("release")
        at = rel.index('compose stop "${REL_WORKERS[@]}"')
        self.assertLess(at, rel.index(stop, at))
        self.assertLess(rel.index(stop, at), rel.index("ihr_fleet_run migrate check roles packs"))

    def test_one_off_commands_keep_their_copies_on_disk(self):
        """Review C8: get and check --deep copy a whole sealed dump; never into a tmpfs (RAM)."""
        run = self.func("dumps_exec")
        self.assertNotIn("--tmpfs /state", run)
        self.assertIn("-v /state ", run)
        self.assertIn("-e TMPDIR=/state", run)

    def test_allow_short_only_in_a_proof(self):
        """Review S3: the wrappers refuse a typed --allow-short (a proof adds it by itself), in
        any spelling argparse could take for it (second fix: `--allow` passed)."""
        for fn, var in (("dumps_expire", "IHR_DUMPS"), ("logs_archive_expire", "IHR_LOGS_ARCHIVE")):
            for mode in ("s3", "local"):
                for flag in ("--allow-short", "--allow", "--allow-s", "--a"):
                    with self.subTest(fn=fn, mode=mode, flag=flag):
                        script = (f'set -euo pipefail\ndie() {{ echo "DIE: $*" >&2; exit 3; }}\n'
                                  f'env_get() {{ [[ "$1" == {var} ]] && echo {mode}; }}\n'
                                  f'{fn}() {{{self.func(fn)}}}\n{fn} {flag} --daily-days 0\n')
                        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                             env={"PATH": "/usr/bin:/bin", "SECRETS": "/nonexistent"})
                        self.assertNotEqual(out.returncode, 0)
                        self.assertIn("DIE: --allow-short is for a proof on the local stand-in only", out.stderr)

    def fleet(self, *args, dumps="s3", running=True, rc=0):
        """`scripts/staging.sh fleet <args>` (its dispatch, as written) with stubs: (exit code,
        the calls in order)."""
        import re

        [case] = re.findall(r"^  fleet\)(.*?);;$", self.text, re.M | re.S)
        log = tempfile.NamedTemporaryFile("r", delete=False)
        self.addCleanup(os.unlink, log.name)
        script = ('set -euo pipefail\ndie() { echo "DIE: $*" >&2; exit 3; }\n'
                  f'env_get() {{ [[ "$1" == IHR_DUMPS ]] && echo "{dumps}"; return 0; }}\n'
                  'compose() { echo "compose $*" >> "$LOG"\n'
                  '  case "$*" in "ps -q --status running dumper") [[ -z "$RUNNING" ]] || echo cid-dumper ;;\n'
                  '    "run "*) return "$RC" ;; esac; }\n'
                  'rel_db_single() { return 1; }\nrel_outgoing_unmigrated() { return 1; }\n'
                  'old_manage() { echo "old_manage $*" >> "$LOG"; }\n'
                  'start_existing() { echo "start_existing $*" >> "$LOG"; }\n'
                  f'fleet_locks() {{{self.func("fleet_locks")}}}\nFLEET_PAUSED=0\n'
                  f'fleet_pause_dumper() {{{self.func("fleet_pause_dumper")}}}\n'
                  f'case "${{1:-}}" in\n  fleet){case};;\nesac\n')
        out = subprocess.run(["bash", "-c", script, "staging.sh", "fleet", *args], capture_output=True, text=True,
                             env={"PATH": "/usr/bin:/bin", "LOG": log.name, "RUNNING": "1" if running else "",
                                  "RC": str(rc)})
        calls = [ln for ln in Path(log.name).read_text().splitlines() if not ln.startswith("compose ps")]
        return out.returncode, calls

    def test_a_fleet_run_that_takes_locks_stops_the_dumper_and_starts_it_again(self):
        """Review C1 (second fix): `scripts/staging.sh fleet migrate|roles|packs` (and a --resume)
        met a dump in flight on its hospital's lock; the dumper stops first and its container is
        started again after, whatever the fleet's outcome (which stays the exit code)."""
        run = "compose run --rm --no-deps -T migrate python manage.py ihr_fleet_run"
        for args in (("migrate", "--operator", "x"), ("roles", "--operator", "x"), ("packs", "--operator", "x"),
                     ("--resume", "r1", "--operator", "x")):
            for rc in (0, 1):
                with self.subTest(args=args, rc=rc):
                    code, calls = self.fleet(*args, rc=rc)
                    self.assertEqual(code, rc)
                    self.assertEqual(calls, ["compose stop dumper", f"{run} {' '.join(args)}", "start_existing dumper"])
        # No dumps here, a dumper a release stopped, or a step that takes no lock: left alone
        for kw, args in (({"dumps": ""}, ("migrate",)), ({"running": False}, ("migrate",)), ({}, ("window", "--clear")),
                         ({}, ("check",))):
            with self.subTest(kw=kw, args=args):
                code, calls = self.fleet(*args, **kw)
                self.assertEqual((code, calls), (0, [f"{run} {' '.join(args)}"]))
        # The move of the roles stops the dumper before its fleet migration
        move = self.func("db_roles_move_stopped")
        self.assertLess(move.index('compose stop "${DB_APP_SERVICES[@]}"'),
                        move.index('[[ -z "$(env_get IHR_DUMPS)" ]] || compose stop dumper'))
        self.assertLess(move.index("compose stop dumper"), move.index("db_roles_apply --move"))

    def test_the_stand_in_lets_the_dumper_list_versions(self):
        """Review S1: the version-aware readers need ListBucketVersions on the stand-in too."""
        tpl = json.loads((ROOT / "deploy" / "s3" / "s3.template.json").read_text())
        pol = json.loads(next(p["content"] for p in tpl["policies"] if p["name"] == "ihr-dumps"))
        listing = next(st for st in pol["Statement"] if "s3:ListBucket" in st["Action"])
        self.assertIn("s3:ListBucketVersions", listing["Action"])
        self.assertFalse(any(a.startswith("s3:Delete") for st in pol["Statement"] for a in st["Action"]))

    def test_a_connection_failure_is_not_a_wrong_role(self):
        """Review C7: `dumper.py role` exit 2 (could not connect) is retried and named as such."""
        up = self.func("dumps_up")
        self.assertIn("[[ $rc == 2 && $i -lt 6 ]] || break", up)
        self.assertIn('2) die "could not connect as ihr_dump', up)
        self.assertIn('if [[ "$rc" == 2 ]]; then', self.func("dumps_smoke"))


# ---- with a real Postgres (the dumper image) ---------------------------------------------------

LIVE = bool(shutil.which("initdb") and shutil.which("pg_dump") and importlib.util.find_spec("psycopg"))
if os.environ.get("IHR_DUMPS_LIVE_REQUIRED") and not LIVE:
    raise SystemExit("IHR_DUMPS_LIVE_REQUIRED: the live tests cannot run here (no Postgres programs or psycopg)")


@unittest.skipUnless(LIVE, "needs Postgres's programs and psycopg: scripts/test-dumps-live.sh")
class Live(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp()
        cls.data = os.path.join(cls.work, "data")
        cls.port = 55432
        env = {**os.environ, "PGHOST": cls.work, "PGUSER": "postgres", "PGPORT": str(cls.port)}
        cls.env = env
        subprocess.run(["initdb", "-D", cls.data, "-U", "postgres", "--auth=trust", "--no-sync"], check=True,
                       capture_output=True, env=env)
        subprocess.run(["pg_ctl", "-D", cls.data, "-w", "-l", os.path.join(cls.work, "log"), "-o",
                        f"-c listen_addresses=127.0.0.1 -c port={cls.port} -c unix_socket_directories={cls.work} -c fsync=off",
                        "start"], check=True, capture_output=True, env=env)
        cls.su(SETUP, "postgres")

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["pg_ctl", "-D", cls.data, "-m", "immediate", "stop"], capture_output=True, env=cls.env)
        shutil.rmtree(cls.work, ignore_errors=True)

    @classmethod
    def su(cls, text, db="ihr"):
        r = subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-qAt", "-d", db], input=text.encode(),
                           capture_output=True, env=cls.env)
        if r.returncode:
            raise AssertionError(f"psql: {r.stderr.decode()[-500:]}")
        return r.stdout.decode().strip()

    def setUp(self):
        self.key = Fernet.generate_key().decode()
        self.sealer = sealer.Sealer(self.key, D.PURPOSE)
        self.s3 = FakeS3(writer=True)
        self.bucket = D.locked_bucket.Bucket(self.s3, "ihr-dumps")
        self.tmp = tempfile.mkdtemp(dir=self.work)
        self.cfg = D.Config({"IHR_DUMP_WORKDIR": self.tmp, "IHR_DUMP_LOCK_WAIT": "2"})
        self.url = f"postgresql://ihr_dump:dump-test@127.0.0.1:{self.port}/ihr"
        self.source = D.Source(self.url)
        self.tools = D.Tools(D.pg_env(self.url))
        self.status = D.Status()
        self.su("UPDATE public.ihr_platform_hospitalschemastate SET upgrading_since = NULL")

    def run_now(self, **kw):
        when = ist(2027, 1, 4, 4, 0)
        with mock.patch.object(D, "now_ist", return_value=when), contextlib.redirect_stdout(io.StringIO()) as out:
            r = D.run(self.cfg, self.source, self.tools, self.bucket, self.sealer, self.status, now=when,
                      scratch_factory=lambda: D.Scratch(self.tmp), **kw)
        return r, out.getvalue()

    def test_the_role(self):
        self.assertEqual(self.source.role_problems(), [])
        su = D.Source(f"postgresql://postgres@127.0.0.1:{self.port}/ihr")
        self.assertTrue(any("not ihr_dump" in p or "superuser" in p for p in su.role_problems()))
        self.su("GRANT INSERT ON h_live000001.t TO ihr_dump")
        try:
            self.assertTrue(any("write privilege" in p for p in self.source.role_problems()))
        finally:
            self.su("REVOKE INSERT ON h_live000001.t FROM ihr_dump")

    def test_one_snapshot_while_writers_write(self):
        stop = threading.Event()
        import psycopg

        def writer():
            with psycopg.connect(f"postgresql://postgres@127.0.0.1:{self.port}/ihr", autocommit=True) as c:
                while not stop.is_set():
                    c.execute("INSERT INTO h_live000001.ihr_audit_entry (txid, kind) "
                              "SELECT pg_current_xact_id(), 'write' FROM generate_series(1, 50)")
                    c.execute("INSERT INTO h_live000001.t (v) SELECT md5(random()::text) FROM generate_series(1, 50)")

        threads = [threading.Thread(target=writer) for _ in range(2)]
        for t in threads:
            t.start()
        time.sleep(0.5)
        try:
            r, out = self.run_now(only=["live"])
        finally:
            stop.set()
            for t in threads:
                t.join()
        self.assertEqual(r, {"live": "ok"}, out)
        held = D.Holdings(self.bucket, self.sealer)
        m = held.newest(LIVE_UUID)
        # Restore the dump into a fresh database and compare with what the manifest read
        with tempfile.TemporaryFile() as sealed:
            D.fetch_checked(self.bucket, m["dump"], sealed)
            plain = b"".join(D.opened(self.sealer, sealed, m["dump"]))
        self.su("DROP DATABASE IF EXISTS back", "postgres")
        self.su("CREATE DATABASE back", "postgres")
        self.su("CREATE EXTENSION pg_trgm", "back")
        subprocess.run(["pg_restore", "--no-owner", "--no-acl", "--exit-on-error", "-d", "back"], input=plain,
                       check=True, capture_output=True, env=self.env)
        max_id = int(self.su("SELECT coalesce(max(id), 0) FROM h_live000001.ihr_audit_entry", "back"))
        self.assertEqual(max_id, m["audit"]["max_entry_id"], "the dump and the manifest saw one snapshot")
        live_now = int(self.su("SELECT max(id) FROM h_live000001.ihr_audit_entry"))
        self.assertGreater(live_now, max_id, "writers went on after the snapshot")
        restored_seq = int(self.su("SELECT last_value FROM h_live000001.t_id_seq", "back"))
        self.assertGreaterEqual(m["sequences"]["t_id_seq"], int(self.su("SELECT max(id) FROM h_live000001.t", "back")))
        self.assertGreaterEqual(restored_seq, int(self.su("SELECT max(id) FROM h_live000001.t", "back")))
        rels = int(self.su("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                           "WHERE n.nspname = 'h_live000001'", "back"))
        self.assertEqual(rels, m["relations"])
        self.assertNotIn(b"live-admin@example.in", D.record_bytes(m))
        sealed = io.BytesIO(self.s3.objects[m["public"]["key"]])
        rows = json.loads(gzip.decompress(b"".join(self.sealer.decrypt_lines(sealed))))
        self.assertEqual(rows["ihr_platform_staffinvitation"][0]["email"], "live-admin@example.in")
        self.assertEqual(rows["ihr_platform_domain"][0]["domain"], "live.localhost")
        self.assertEqual(self.status.get("check_problems"), 0)

    def test_held_hospitals_are_skipped(self):
        self.su("UPDATE public.ihr_platform_hospitalschemastate SET upgrading_since = now()")
        r, out = self.run_now(only=["live"])
        self.assertEqual(r, {"live": "held"})
        self.assertIn("ihr-dump: hospital=live outcome=held", out)

    def test_the_hospital_lock(self):
        import psycopg

        # The key as provisioning/runner.py's lock_key has it, written out (review C10: not
        # D.lock_key, which would agree with itself)
        key = int.from_bytes(hashlib.sha256(b"live").digest()[:8], "big", signed=True)
        with psycopg.connect(f"postgresql://postgres@127.0.0.1:{self.port}/ihr", autocommit=True) as c:
            c.execute("SELECT pg_advisory_lock(%s)", [key])
            r, out = self.run_now(only=["live"])
        self.assertEqual(r, {"live": "lock"})
        self.assertIn("ihr-dump: hospital=live outcome=failed reason=lock", out)

    def test_two_sessions_while_pg_dump_runs_and_the_lock_gone_before_the_upload(self):
        """Reviews C7 and C1, with a real server: while pg_dump runs, ihr_dump has two sessions
        (the lock's, which exported the snapshot, and pg_dump's); by the upload the hospital's
        lock is free again."""
        import psycopg

        key = int.from_bytes(hashlib.sha256(b"live").digest()[:8], "big", signed=True)
        su = f"postgresql://postgres@127.0.0.1:{self.port}/ihr"
        seen = {}
        dump, put = self.tools.dump, self.bucket.put_file

        def watched_dump(*a, **kw):
            proc = dump(*a, **kw)
            with psycopg.connect(su, autocommit=True) as c:
                for _ in range(50):
                    n = c.execute("SELECT count(*) FROM pg_stat_activity WHERE usename = 'ihr_dump'").fetchone()[0]
                    seen["sessions"] = max(seen.get("sessions", 0), n)
                    if n >= 2:
                        break
                    time.sleep(0.01)
            return proc

        def watched_put(key_, *a, **kw):
            if "lock_free" not in seen:
                with psycopg.connect(su, autocommit=True) as c:
                    seen["lock_free"] = c.execute("SELECT pg_try_advisory_lock(%s)", [key]).fetchone()[0]
            return put(key_, *a, **kw)

        with mock.patch.object(self.tools, "dump", watched_dump), mock.patch.object(self.bucket, "put_file", watched_put):
            r, out = self.run_now(only=["live"])
        self.assertEqual(r, {"live": "ok"}, out)
        self.assertEqual(seen, {"sessions": 2, "lock_free": True})

    def test_pg_restore_reads_whole_or_stops_at_the_toc(self):
        """Review C3, with the real pg_restore: --list stops after the table of contents (so the
        dumper reads the rest itself), and reading every data block (check --deep) fails on half
        an archive that --list accepts."""
        r, out = self.run_now(only=["live"])
        self.assertEqual(r, {"live": "ok"}, out)
        m = D.Holdings(self.bucket, self.sealer).newest(LIVE_UUID)
        with tempfile.TemporaryFile() as sealed:
            D.fetch_checked(self.bucket, m["dump"], sealed)
            plain = b"".join(D.opened(self.sealer, sealed, m["dump"]))
        chunks = [plain[i:i + 65536] for i in range(0, len(plain), 65536)]
        self.assertGreater(len(chunks), 4)
        read = []

        def counted(cs):
            for c in cs:
                read.append(len(c))
                yield c

        lines = D.Tools().toc(counted(chunks))
        self.assertEqual(D.toc_problems(lines, "h_live000001"), [])
        self.assertLess(len(read), len(chunks), "pg_restore --list read the whole archive")
        D.Tools().read_all(iter(chunks))
        half = chunks[:len(chunks) // 2]
        D.Tools().toc(iter(half))  # the table of contents is all there
        with self.assertRaises(RuntimeError):
            D.Tools().read_all(iter(half))


LIVE_UUID = "55555555-5555-4555-8555-555555555555"
SETUP = f"""
CREATE DATABASE ihr;
\\c ihr
CREATE ROLE ihr_dump LOGIN PASSWORD 'dump-test';
GRANT pg_read_all_data TO ihr_dump;
REVOKE CREATE, TEMPORARY ON DATABASE ihr FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE EXTENSION pg_trgm;
CREATE TABLE public.ihr_platform_hospital (id serial PRIMARY KEY, uuid uuid UNIQUE, slug text, name text,
  schema_name text, status text, activated_at timestamptz, created_on timestamptz DEFAULT now());
CREATE TABLE public.ihr_platform_hospitalschemastate (hospital_id int PRIMARY KEY REFERENCES public.ihr_platform_hospital,
  schema_fingerprint text DEFAULT '', upgrading_since timestamptz, hold_reason text DEFAULT '');
CREATE TABLE public.ihr_platform_domain (id serial PRIMARY KEY, domain text, tenant_id int REFERENCES public.ihr_platform_hospital);
CREATE TABLE public.ihr_platform_staffinvitation (id serial PRIMARY KEY, email text, hospital_id int REFERENCES public.ihr_platform_hospital);
INSERT INTO public.ihr_platform_hospital (uuid, slug, name, schema_name, status, activated_at)
  VALUES ('{LIVE_UUID}', 'live', 'Live Test Hospital', 'h_live000001', 'active', now());
INSERT INTO public.ihr_platform_hospitalschemastate (hospital_id, schema_fingerprint) VALUES (1, 'abc');
INSERT INTO public.ihr_platform_domain (domain, tenant_id) VALUES ('live.localhost', 1);
INSERT INTO public.ihr_platform_staffinvitation (email, hospital_id) VALUES ('live-admin@example.in', 1);
CREATE SCHEMA h_live000001;
CREATE TABLE h_live000001.django_migrations (id serial PRIMARY KEY, app text, name text);
INSERT INTO h_live000001.django_migrations (app, name) VALUES ('emr', '0001_initial'), ('ihr_audit', '0001_initial');
CREATE TABLE h_live000001.ihr_audit_entry (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, txid xid8 NOT NULL,
  kind text NOT NULL, at timestamptz DEFAULT clock_timestamp());
CREATE TABLE h_live000001.ihr_audit_seal (seq bigint PRIMARY KEY, hash bytea, upper_txid xid8);
INSERT INTO h_live000001.ihr_audit_seal VALUES (1, '\\x00ff', '5');
CREATE TABLE h_live000001.t (id serial PRIMARY KEY, v text);
CREATE INDEX t_v ON h_live000001.t USING gin (v gin_trgm_ops);
CREATE FUNCTION h_live000001.f() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;
CREATE TRIGGER t_f BEFORE INSERT ON h_live000001.t FOR EACH ROW EXECUTE FUNCTION h_live000001.f();
INSERT INTO h_live000001.t (v) SELECT md5(g::text) FROM generate_series(1, 20000) g;
"""


if __name__ == "__main__":
    unittest.main()
