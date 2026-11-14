"""Fingerprints of every hospital's audit chain, kept outside the database (P1-T6b, ADR-010).

Every hour one small JSON object is added to a write-once bucket (S3 Object Lock; SeaweedFS
in dev and staging, an India-region bucket in production). It lists, per hospital, the newest seal's number
and hash. Nobody can change or delete it until its retention ends, not even the platform's
database owner. A rewritten or shortened chain no longer matches the fingerprints written
before the change: ``ihr_audit_verify`` checks every hospital against every fingerprint.

Two credentials, read from the environment when used (never Django settings):
    IHR_AUDIT_ANCHOR_WRITE_KEY / _SECRET   may only add objects (the hourly job, Celery)
    IHR_AUDIT_ANCHOR_READ_KEY / _SECRET    may only read and list (verify, export)
plus IHR_AUDIT_ANCHOR_ENDPOINT (empty for AWS), _BUCKET and _REGION (default ap-south-1).

IHR_AUDIT_ANCHOR_LOCK_MODE (P1-T8c, ADR-011 §3): COMPLIANCE (default) requires a bucket whose
lock nobody can lift. GOVERNANCE accepts a Governance-mode bucket (E2E offers no other), which an
admin of the storage account could lift: allowed only when set explicitly, and always reported.
"""

import base64
import hashlib
import json
import logging
import os
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db import connection
from psycopg import sql

logger = logging.getLogger(__name__)

FORMAT = "ihr-audit-anchor/1"
STALE_AFTER = timedelta(hours=2)  # written hourly: one missed run is tolerated
# Production keeps fingerprints at least as long as the trail may be asked about (P1-T8 sets
# 10 years); dev uses 30 days so the bucket can be cleaned up.
MIN_RETENTION_DAYS = 365 * 7
CLOCK_SKEW = timedelta(minutes=10)  # writer's clock against the storage's
MAX_OBJECT_BYTES = 5 * 1024 * 1024
# What a fingerprint may say about a hospital (P1-T21 review A5): text from the bucket is never
# trusted as is, so a forged object cannot put a newline or another hospital's line into the
# watch's log
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{2,30}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
# Store problems by class (P1-T21 A5), each with its own alert in the watch's metrics
STORE_CLASSES = (
    "unreadable",
    "skew",
    "rewritten",
    "hidden",
    "lock",
    "unknown_hospital",
)


class NotConfiguredError(Exception):
    pass


LOCK_MODES = ("COMPLIANCE", "GOVERNANCE")


def required_lock_mode():
    """The weakest lock mode this platform accepts (IHR_AUDIT_ANCHOR_LOCK_MODE, default COMPLIANCE)."""
    value = (
        os.environ.get("IHR_AUDIT_ANCHOR_LOCK_MODE", "").strip().upper() or "COMPLIANCE"
    )
    if value not in LOCK_MODES:
        msg = f"IHR_AUDIT_ANCHOR_LOCK_MODE must be COMPLIANCE or GOVERNANCE, not {value!r}"
        raise NotConfiguredError(msg)
    return value


def prefix():
    """Objects of this platform (``IHR_TENANT_DOMAIN_SUFFIX``) only: staging and production
    may share a bucket without mixing their fingerprints."""
    suffix = getattr(settings, "IHR_TENANT_DOMAIN_SUFFIX", "") or "localhost"
    return f"anchors/{suffix}/"


class S3Store:
    def __init__(self, role, quick=False):
        env = os.environ
        key = env.get(f"IHR_AUDIT_ANCHOR_{role}_KEY", "")
        secret = env.get(f"IHR_AUDIT_ANCHOR_{role}_SECRET", "")
        self.bucket = env.get("IHR_AUDIT_ANCHOR_BUCKET", "")
        if not (key and secret and self.bucket):
            msg = f"IHR_AUDIT_ANCHOR_BUCKET and _{role}_KEY/_SECRET are not set"
            raise NotConfiguredError(msg)
        import boto3
        from botocore.config import Config

        self.client = boto3.client(
            "s3",
            endpoint_url=env.get("IHR_AUDIT_ANCHOR_ENDPOINT") or None,
            region_name=env.get("IHR_AUDIT_ANCHOR_REGION", "ap-south-1"),
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=3 if quick else 5,
                read_timeout=5 if quick else 20,
                retries={"max_attempts": 1 if quick else 3},
            ),
        )

    def put(self, key, body):
        md5 = base64.b64encode(hashlib.md5(body).digest()).decode()  # noqa: S324 (S3 integrity header)
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentMD5=md5,
            ContentType="application/json",
            IfNoneMatch="*",  # a new object only; a second version of a key is reported
        )

    def versions(self, under):
        """Every version of every object under the prefix: (key, version id, when the
        storage received it). The storage's time, not the writer's, orders them."""
        pages = self.client.get_paginator("list_object_versions").paginate(
            Bucket=self.bucket, Prefix=under
        )
        for page in pages:
            for item in page.get("Versions", []):
                yield item["Key"], item["VersionId"], item["LastModified"]
            for marker in page.get("DeleteMarkers", []):
                # a delete marker hides a version: reported
                yield marker["Key"], None, marker["LastModified"]

    def keys(self, under):
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=under
        )
        for page in pages:
            for item in page.get("Contents", []):
                yield item["Key"]

    def get(self, key, version=None):
        args = {"Bucket": self.bucket, "Key": key}
        if version:
            args["VersionId"] = version
        body = self.client.get_object(**args)["Body"]
        return body.read(MAX_OBJECT_BYTES + 1)

    def lock(self):
        """The bucket's Object Lock default retention and versioning, or None."""
        try:
            conf = self.client.get_object_lock_configuration(Bucket=self.bucket)
            versioning = self.client.get_bucket_versioning(Bucket=self.bucket)
        except self.client.exceptions.ClientError:
            return None
        lock = conf.get("ObjectLockConfiguration", {})
        rule = lock.get("Rule", {}).get("DefaultRetention", {})
        if lock.get("ObjectLockEnabled") != "Enabled" or not rule:
            return None
        return {
            "mode": rule.get("Mode"),
            "days": rule.get("Days") or 365 * (rule.get("Years") or 0),
            "versioning": versioning.get("Status"),
        }


def _schema_has_trail(cursor, schema):
    cursor.execute(
        "SELECT to_regclass(format('%%I.ihr_audit_seal', %s::text)) IS NOT NULL",
        [schema],
    )
    return cursor.fetchone()[0]


def snapshot():
    """Each hospital's newest seal: {hospital uuid: {slug, seq, hash, sealed_at}}."""
    from ihr_platform.models import Hospital

    heads = {}
    with connection.cursor() as cursor:
        live = Hospital.objects.exclude(
            schema_name="public"
        ).exclude(
            status=Hospital.Status.PROVISIONING  # a failed set-up is rolled back and deleted
        )
        for hospital in live.order_by("slug"):
            if not _schema_has_trail(cursor, hospital.schema_name):
                continue
            cursor.execute(
                sql.SQL(
                    "SELECT seq, encode(hash, 'hex'), sealed_at FROM {} "
                    "ORDER BY seq DESC LIMIT 1"
                ).format(sql.Identifier(hospital.schema_name, "ihr_audit_seal"))
            )
            row = cursor.fetchone()
            seq, digest, at = row if row else (0, bytes(32).hex(), None)
            heads[str(hospital.uuid)] = {
                "slug": hospital.slug,
                "seq": seq,
                "hash": digest,
                "sealed_at": at and at.isoformat(),
            }
    return heads


def write(store=None, now=None):
    """Add one fingerprint object. Returns its key."""
    store = store or S3Store("WRITE")
    now = now or datetime.now(UTC)
    body = json.dumps(
        {
            "format": FORMAT,
            "platform": prefix(),
            "written_at": now.isoformat(),
            "hospitals": snapshot(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    key = f"{prefix()}{now:%Y/%m/%d/%Y%m%dT%H%M%S}Z-{uuid.uuid4().hex[:8]}.json"
    store.put(key, body)
    return key


@dataclass
class Anchor:
    key: str
    version: str | None
    written_at: datetime
    hospitals: dict = field(default_factory=dict)  # uuid -> (seq, hash, slug)
    stored: datetime | None = None  # when the storage received it


def _parse(key, version, body):
    if len(body) > MAX_OBJECT_BYTES:
        msg = "too large"
        raise ValueError(msg)
    doc = json.loads(body)
    # Valid JSON that is not our shape (a list, a string, null, nested lists) is a bad object,
    # never an error that escapes the classification (P1-T21 review C7)
    if not isinstance(doc, dict):
        msg = "not a JSON object"
        raise ValueError(msg)
    if doc.get("format") != FORMAT or doc.get("platform") != prefix():
        msg = f"unknown format or platform {doc.get('format')!r}"
        raise ValueError(msg)
    if not isinstance(doc.get("written_at"), str) or not isinstance(
        doc.get("hospitals"), dict
    ):
        msg = "written_at or hospitals is not of our shape"
        raise ValueError(msg)
    written = datetime.fromisoformat(doc["written_at"])
    if written.tzinfo is None:
        msg = "written_at has no time zone"
        raise ValueError(msg)
    hospitals = {}
    for hospital_uuid, head in doc["hospitals"].items():
        if not isinstance(head, dict):
            msg = "a hospital entry that is not an object"
            raise ValueError(msg)
        seq, digest, slug = int(head["seq"]), str(head["hash"]), head.get("slug")
        if (
            not UUID_RE.match(str(hospital_uuid))
            or seq < 0
            or not HASH_RE.match(digest)
            or not (isinstance(slug, str) and SLUG_RE.match(slug))
        ):
            msg = "a hospital entry that is not ours (uuid, seq, hash or slug)"
            raise ValueError(msg)
        hospitals[hospital_uuid] = (seq, digest, slug)
    return Anchor(key, version, written, hospitals)


@dataclass
class Fingerprints:
    """What the fingerprints say, kept small: each claim is checked against the database as
    it is read (in batches) and only problems are kept, with when each hospital was last
    listed. Times are the storage's own (LastModified), not what the writer says."""

    count: int = 0
    newest: datetime | None = None
    last_listed: dict = field(default_factory=dict)  # uuid -> storage time
    slugs: dict = field(default_factory=dict)  # uuid -> slug in its newest listing
    hospital_problems: dict = field(default_factory=dict)  # uuid -> [problem]
    problems: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    pending: dict = field(default_factory=dict)  # uuid -> {(seq, hash): first stored}
    classes: Counter = field(default_factory=Counter)  # store problems by STORE_CLASSES
    # Newest claim per hospital, (seq, hash, stored): the watch seeds a lost checkpoint from it
    newest_claims: dict = field(default_factory=dict)
    excused: int = 0  # claims a restore event accounts for (P1-T21 A4)
    items: list = field(default_factory=list)  # (class, object key or None, text)

    def problem(self, cls, text, key=None):
        self.classes[cls] += 1
        self.items.append((cls, key, text))
        self.problems.append(text)

    def add(self, anchor, stored):
        self.count += 1
        self.newest = max(self.newest or stored, stored)
        if abs(anchor.written_at - stored) > CLOCK_SKEW:
            self.problem(
                "skew",
                f"fingerprint {anchor.key}: says it was written at "
                f"{anchor.written_at.isoformat(timespec='seconds')}, the storage received "
                f"it at {stored.isoformat(timespec='seconds')}",
                anchor.key,
            )
        for hospital_uuid, (seq, digest, slug) in anchor.hospitals.items():
            claims = self.pending.setdefault(hospital_uuid, {})
            claims[(seq, digest)] = min(claims.get((seq, digest), stored), stored)
            if stored >= self.last_listed.get(hospital_uuid, stored):
                self.last_listed[hospital_uuid] = stored
                self.slugs[hospital_uuid] = slug
            newest = self.newest_claims.get(hospital_uuid)
            if newest is None or (stored, seq) >= (newest[2], newest[0]):
                self.newest_claims[hospital_uuid] = (seq, digest, stored)

    def flush(self, stored_hashes, excuse=None):
        """Check the pending claims: ``stored_hashes(uuid, seqs)`` gives {seq: hash hex} of
        that hospital's seals (None for a hospital not in the database). ``excuse(uuid, seq,
        hash, first stored)``: True for a claim a restore event accounts for (its superseded
        segment holds that seal, events.py); such a claim is counted, not a problem."""
        for hospital_uuid, claims in self.pending.items():
            found = stored_hashes(hospital_uuid, sorted({seq for seq, _ in claims}))
            if found is None:
                continue  # reported by unknown_hospitals
            problems = self.hospital_problems.setdefault(hospital_uuid, [])
            for (seq, digest), first in sorted(claims.items()):
                when = first.isoformat(timespec="seconds")
                if seq == 0:
                    continue
                if (
                    found.get(seq) != digest
                    and excuse
                    and excuse(hospital_uuid, seq, digest, first)
                ):
                    self.excused += 1
                    continue
                if seq not in found:
                    problems.append(
                        f"seal {seq}, fingerprinted at {when}, is missing (the trail was cut)"
                    )
                elif found[seq] != digest:
                    problems.append(
                        f"seal {seq} differs from its fingerprint of {when} "
                        "(the trail was rewritten)"
                    )
        self.pending = {}


def database_hashes():
    """``stored_hashes`` for Fingerprints.flush, reading each hospital's seal table."""
    from ihr_platform.models import Hospital

    schemas = {
        str(u): schema
        for u, schema in Hospital.objects.exclude(schema_name="public").values_list(
            "uuid", "schema_name"
        )
    }

    def stored_hashes(hospital_uuid, seqs):
        schema = schemas.get(hospital_uuid)
        if schema is None:
            return None
        with connection.cursor() as cursor:
            if not _schema_has_trail(cursor, schema):
                return {}
            cursor.execute(
                sql.SQL(
                    "SELECT seq, encode(hash, 'hex') FROM {} WHERE seq = ANY(%s)"
                ).format(sql.Identifier(schema, "ihr_audit_seal")),
                [seqs],
            )
            return dict(cursor.fetchall())

    return stored_hashes


def _lock_problems(store, fps, lock=...):
    required = required_lock_mode()
    if lock is ...:
        lock = store.lock()
    if lock is None:
        fps.problem(
            "lock",
            f"fingerprint bucket {store.bucket!r} has no Object Lock default retention",
        )
        return
    weak = []
    if lock["mode"] == "GOVERNANCE" and required == "GOVERNANCE":
        # Accepted on purpose (ADR-011 §3), never silently
        fps.notes.append(
            f"fingerprint bucket {store.bucket!r}: GOVERNANCE retention, accepted by "
            "IHR_AUDIT_ANCHOR_LOCK_MODE=GOVERNANCE (an admin of the storage account can lift it)"
        )
    elif lock["mode"] != "COMPLIANCE":
        weak.append(f"{lock['mode']} retention can be lifted by the storage admin")
    if lock["versioning"] != "Enabled":
        weak.append("versioning is not enabled")
    if lock["days"] < MIN_RETENTION_DAYS:
        weak.append(f"objects are kept {lock['days']} days only")
    for item in weak:
        if settings.DEBUG:
            fps.notes.append(
                f"fingerprint bucket {store.bucket!r}: {item} (allowed in dev only)"
            )
        else:
            fps.problem("lock", f"fingerprint bucket {store.bucket!r}: {item}")


_LOCK_STATUS_CACHE = {}  # {"at": monotonic seconds, "value": dict}
LOCK_STATUS_SECONDS = 300


def lock_status(store=None):
    """For the status page: the bucket's lock and whether this platform accepts it. One
    storage call at most every 5 minutes per process (a page load never waits on it twice)."""
    import time

    cached = _LOCK_STATUS_CACHE.get("value")
    if (
        store is None
        and cached
        and time.monotonic() - _LOCK_STATUS_CACHE["at"] < LOCK_STATUS_SECONDS
    ):
        return cached
    value = _lock_status(store)
    if store is None:
        _LOCK_STATUS_CACHE.update(at=time.monotonic(), value=value)
    return value


def _lock_status(store):
    try:
        required = required_lock_mode()
        store = store or S3Store("READ", quick=True)
    except NotConfiguredError as exc:
        return {"configured": False, "detail": str(exc)}
    try:
        lock = store.lock()
    except Exception as exc:  # any storage error: shown on the page, never fatal
        return {
            "configured": True,
            "bucket": store.bucket,
            "readable": False,
            "detail": type(exc).__name__,
        }
    fps = Fingerprints()
    _lock_problems(store, fps, lock)
    return {
        "configured": True,
        "bucket": store.bucket,
        "required_mode": required,
        "mode": lock and lock["mode"],
        "retention_days": lock and lock["days"],
        "accepted": not fps.problems,
        "notes": fps.notes,
        "problems": fps.problems,
    }


def _listing(store, since):
    """Every version under this platform's prefix; with ``since``, only the day folders
    from that day to today (UTC), so a frequent check lists a day or two, never 10 years
    of objects (P1-T21)."""
    if since is None:
        yield from store.versions(prefix())
        return
    day = since if not isinstance(since, datetime) else since.astimezone(UTC).date()
    today = datetime.now(UTC).date()
    while day <= today:
        yield from store.versions(f"{prefix()}{day:%Y/%m/%d}/")
        day += timedelta(days=1)


def load_all(
    store=None, since=None, stored_hashes=None, batch=500, until=None, excuse=None
):
    """Read every fingerprint of this platform (from the day ``since`` if given), checking
    each claim against the database in batches of ``batch`` objects. ``until``: only those
    the storage received by then (a database restored to that moment, P1-T8c). ``excuse``:
    see Fingerprints.flush (restore events, P1-T21)."""
    store = store or S3Store("READ")
    stored_hashes = stored_hashes or database_hashes()
    fps = Fingerprints()
    _lock_problems(store, fps)
    first = since and f"{since:%Y/%m/%d}"
    seen = set()
    for key, version, stored in _listing(store, since):
        if key in seen:
            fps.problem("rewritten", f"fingerprint {key}: written more than once", key)
        seen.add(key)
        if first and key[len(prefix()) : len(prefix()) + 10] < first:
            continue
        if until and stored > until:
            continue
        if version is None:
            fps.problem("hidden", f"fingerprint {key}: a delete marker hides it", key)
            continue
        # A storage error makes the whole read unreadable (raised); anything wrong with one
        # object's body marks that object alone, whatever the parser raised (C7)
        body = store.get(key, version)
        try:
            fps.add(_parse(key, version, body), stored)
        except Exception as exc:  # any bad body is one bad object, classed (C7)
            fps.problem(
                "unreadable",
                f"fingerprint {key}: unreadable ({type(exc).__name__}: {str(exc)[:200]})",
                key,
            )
        if fps.count % batch == 0:
            fps.flush(stored_hashes, excuse)
    fps.flush(stored_hashes, excuse)
    return fps


def platform_problems(fps, now=None):
    now = now or datetime.now(UTC)
    if not fps.count:
        return ["no fingerprint has been written yet"]
    if now - fps.newest > STALE_AFTER:
        hours = (now - fps.newest).total_seconds() / 3600
        return [
            f"the newest fingerprint is {hours:.1f} hours old (is the job running?)"
        ]
    return []


def unknown_hospitals(fps, hospitals, closed=()):
    """Fingerprinted hospitals the database no longer has, or whose address now belongs to
    another hospital: deleting a hospital row must not hide a rewritten trail.
    ``hospitals`` is {uuid: slug} of every hospital in the database. ``closed``: uuids an
    offboarding event in the bucket closes (events.py, P1-T21 A4): gone on purpose."""
    problems = []
    by_slug = {slug: hospital_uuid for hospital_uuid, slug in hospitals.items()}
    for hospital_uuid, slug in sorted(fps.slugs.items()):
        if hospital_uuid in hospitals or hospital_uuid in closed:
            continue
        if slug in by_slug:
            problems.append(
                f"{slug}: fingerprinted as hospital {hospital_uuid}, but the database "
                f"now gives that address to {by_slug[slug]}"
            )
        else:
            problems.append(
                f"{slug}: hospital {hospital_uuid} is fingerprinted but no longer in the "
                "database"
            )
    return problems


def check(hospital_uuid, fps):
    """Problems for one hospital: fingerprints its seals no longer match, and being left
    out of the newest fingerprint although earlier ones list it."""
    problems = list(fps.hospital_problems.get(hospital_uuid, []))
    listed = fps.last_listed.get(hospital_uuid)
    if listed and listed < fps.newest:
        problems.append(
            "missing from the newest fingerprint although earlier ones list it"
        )
    return problems


def find_covering(hospital_uuid, low, high, start, store=None, max_reads=1000):
    """The earliest fingerprint written since ``start`` whose claim for this hospital is a
    seal in [low, high] (an export's proof), or None. Objects with more than one version,
    hidden by a delete marker, or whose stated time is far from the storage's are passed
    over. Reads day folders forward, at most ``max_reads`` objects."""
    store = store or S3Store("READ")
    reads = 0
    day = start.astimezone(UTC).date()
    today = datetime.now(UTC).date()
    while day <= today and reads < max_reads:
        listed = {}
        for key, version, stored in store.versions(f"{prefix()}{day:%Y/%m/%d}/"):
            listed.setdefault(key, []).append((version, stored))
        for key in sorted(listed):
            versions = listed[key]
            if len(versions) != 1 or versions[0][0] is None:
                continue  # overwritten or hidden: never used as proof
            reads += 1
            version, stored = versions[0]
            body = store.get(key, version)
            try:
                found = _parse(key, version, body)
            except Exception:  # noqa: S112 (a bad object is never used as proof)
                continue
            found.stored = stored
            claim = found.hospitals.get(hospital_uuid)
            if (
                abs(found.written_at - stored) <= CLOCK_SKEW
                and stored >= start
                and claim
                and low <= claim[0] <= high
            ):
                return found
            if reads >= max_reads:
                break
        day += timedelta(days=1)
    return None
