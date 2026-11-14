"""Restore and offboarding events in the fingerprint bucket (P1-T21 review A4, ADR-010).

A restore rolls a hospital's chain back below seals that hourly fingerprints already name, and
an offboarding deletes a hospital whose fingerprints stay in the bucket for years. Without a
record, both read as tampering for good ("the trail was rewritten", "no longer in the
database"), and operators learn to silence the critical alerts. So each is written, by the
owner-run step that does it, as one write-once **event** object next to the fingerprints:

    anchors/ihr-events/<platform suffix>/<YYYY>/<MM>/<DD>/<YYYYmmddTHHMMSS>Z-<kind>-<uuid>-<rand>.json

(under ``anchors/``: the existing writer key may add there and the reader key read, and the
fingerprint check, which lists ``anchors/<suffix>/`` only, never sees an event). One document:

    {"format": "ihr-audit-event/1", "platform": "anchors/<suffix>/", "kind": "restore",
     "hospital": "<uuid>", "slug": "<slug>", "written_at": "<ISO 8601, UTC>",
     "operator": "<who ran it>", "generation": <n, 1 for a hospital's first restore>,
     "restored_to": {"seq": <s>, "hash": "<hex>"},          # the restored chain's head
     "superseded": {"from_seq": <s+1>, "to_seq": <t>,       # the live tail it replaced
                    "head_hash": "<hex of seal t>", "digest": "<segment_digest>"}}

    {"format": ..., "kind": "offboarding", "hospital", "slug", "written_at", "operator",
     "final": {"seq": <n>, "hash": "<hex>"},                # the chain's head when it closed
     "certificate": "<the deletion certificate's id>"}

Hashes, numbers, the hospital's id and address only: no personal data.

**What an event may excuse** (``Events.excuse``, used by ihr_audit_verify and the watch):

- A restore event accounts for a fingerprint claim (seq, hash) of its hospital that the
  database no longer matches only when (1) the claim was stored before the event, (2) seq is
  in the superseded range, and (3) the **superseded segment** holds that seal with that hash
  and the segment itself verifies: contiguous seals from ``restored_to.seq + 1``, the first
  linked to ``restored_to.hash``, every link and seal hash recomputed, the last one
  ``head_hash``, its ``digest`` the event's, and its entries recomputed (the segment's own
  ``verify_entries``). A restore event whose segment cannot be read, or does not verify,
  excuses nothing: it reads as the cut it would hide (P1-T22 design, After the attack 6).
- An offboarding event closes its hospital: fingerprints of a hospital the database no longer
  has are then gone on purpose, not "missing" (``anchor.unknown_hospitals``).

**Superseded segments** are P1-T22's (an append-only archive outside the hospital schema,
keyed by hospital and restore generation). Until it exists, ``SEGMENTS`` reads none, so every
restore event fails closed. P1-T22 sets ``SEGMENTS.source`` to a callable
``(hospital uuid, generation) -> Segment | None``. A ``Segment`` (below) is its seals in this
module's row format, plus a ``verify_entries()`` that recomputes the archived entries of each
seal and returns problems.

Writers (owner-run; the add-only key, anchor-write.env): ``write_restore`` and
``write_offboarding``. Readers: ``read`` (the reader key, anchor-read.env).
"""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ihr_audit import anchor, oneline, seal

FORMAT = "ihr-audit-event/1"
KINDS = ("restore", "offboarding")
OPERATOR_RE = re.compile(
    r"^[A-Za-z0-9_.@-]{1,64}$"
)  # as ihr_fleet_run records operators
CERTIFICATE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
# The name the writer gives an event (``_key``), after this platform's prefix: an object named
# otherwise is not one of ours, and its key is never put into a log line as it is (review:
# a key with a line break would forge the watch's fixed line, S6's vector through the bucket)
KEY_TAIL_RE = re.compile(
    r"^\d{4}/\d{2}/\d{2}/\d{8}T\d{6}Z-(?P<kind>restore|offboarding)-"
    r"(?P<hospital>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-[0-9a-f]{8}\.json$"
)


def shown(text, limit=300):
    """Text from the bucket as a log or problem may show it: one line, every control or
    line-breaking character escaped, cut to ``limit``."""
    return oneline.one_line(str(text)[:limit])


def prefix():
    """Where this platform's events live: beside, never under, its fingerprints."""
    suffix = anchor.prefix()[len("anchors/") :].rstrip("/")
    return f"anchors/ihr-events/{suffix}/"


@dataclass(frozen=True)
class SealRow:
    """A seal as the chain holds it (also a superseded segment's rows)."""

    seq: int
    lower: str
    upper: str
    entries: int
    entries_hash: bytes
    prev_hash: bytes
    hash: bytes
    version: int


def header_problems(rows, first_seq, first_prev, first_lower=None):
    """Links and seal hashes of consecutive ``rows`` (SealRow), recomputed from their stored
    fields: the header walk, in Python (a segment; the database's own walk is in SQL,
    incremental.py)."""
    problems = []
    expected_seq, prev, lower = first_seq, bytes(first_prev), first_lower
    for row in rows:
        where = f"seal {row.seq}"
        if row.seq != expected_seq:
            problems.append(f"{where}: expected seal {expected_seq}")
        if lower is not None and row.lower != lower:
            problems.append(f"{where}: starts at {row.lower}, expected {lower}")
        if bytes(row.prev_hash) != prev:
            problems.append(f"{where}: does not follow the previous seal")
        if row.version not in seal.CANONICAL:
            problems.append(f"{where}: unknown format {row.version}")
        elif bytes(row.hash) != seal.seal_hash(
            row.prev_hash,
            row.seq,
            row.lower,
            row.upper,
            row.entries,
            row.entries_hash,
            row.version,
        ):
            problems.append(f"{where}: hash does not match")
        expected_seq, prev, lower = row.seq + 1, bytes(row.hash), row.upper
    return problems


def segment_digest(rows):
    """What a restore event says about its superseded segment: sha256 over each seal's
    number and hash, in order."""
    text = "".join(f"{row.seq}|{bytes(row.hash).hex()}\n" for row in rows)
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class Segment:
    """A superseded segment (P1-T22 stores them): its seals, and its entries' own check."""

    seals: list
    verify_entries: object = None  # callable() -> [problem]; None: entries not kept


class _Segments:
    """Where superseded segments are read from. P1-T22 sets ``source``; until then none is
    found and every restore event excuses nothing (fails closed)."""

    source = None

    def get(self, hospital_uuid, generation):
        if self.source is None:
            return None
        return self.source(hospital_uuid, generation)


SEGMENTS = _Segments()


@dataclass
class Event:
    key: str
    kind: str
    hospital: str
    slug: str
    written_at: datetime
    stored: datetime
    operator: str
    generation: int = 0
    restored_to: tuple | None = None  # (seq, hash hex)
    superseded: dict | None = None  # from_seq, to_seq, head_hash, digest
    final: tuple | None = None  # (seq, hash hex)
    certificate: str = ""
    problems: list = field(default_factory=list)  # why it excuses nothing
    segment_hashes: dict = field(default_factory=dict)  # seq -> hash hex, once verified


def _seq_hash(doc, name):
    part = doc[name]
    seq, digest = int(part["seq"]), str(part["hash"])
    if seq < 0 or not anchor.HASH_RE.match(digest):
        msg = f"{name}: not a seal number and hash"
        raise ValueError(msg)
    return seq, digest


def parse(key, body, stored):  # noqa: PLR0912, PLR0915 (one check per field)
    """One event object, checked field by field (text from the bucket is never trusted)."""
    if len(body) > anchor.MAX_OBJECT_BYTES:
        msg = "too large"
        raise ValueError(msg)
    doc = json.loads(body)
    if not isinstance(doc, dict):  # valid JSON of another shape is a bad object (C7)
        msg = "not a JSON object"
        raise ValueError(msg)
    if doc.get("format") != FORMAT or doc.get("platform") != anchor.prefix():
        msg = "unknown format or platform"
        raise ValueError(msg)
    kind = doc.get("kind")
    hospital, slug = str(doc.get("hospital", "")), doc.get("slug")
    operator = doc.get("operator")
    if (
        kind not in KINDS
        or not anchor.UUID_RE.match(hospital)
        or not (isinstance(slug, str) and anchor.SLUG_RE.match(slug))
        or not (isinstance(operator, str) and OPERATOR_RE.match(operator))
    ):
        msg = "kind, hospital, slug or operator is not ours"
        raise ValueError(msg)
    if not isinstance(doc.get("written_at"), str):
        msg = "written_at is not a time"
        raise ValueError(msg)
    written = datetime.fromisoformat(doc["written_at"])
    if written.tzinfo is None:
        msg = "written_at has no time zone"
        raise ValueError(msg)
    for part in ("restored_to", "superseded") if kind == "restore" else ("final",):
        if not isinstance(doc.get(part), dict):
            msg = f"{part} is not an object"
            raise ValueError(msg)
    event = Event(key, kind, hospital, slug, written, stored, operator)
    if kind == "restore":
        event.generation = int(doc["generation"])
        if event.generation < 1:
            msg = "generation must be 1 or more"
            raise ValueError(msg)
        event.restored_to = _seq_hash(doc, "restored_to")
        sup = doc["superseded"]
        event.superseded = {
            "from_seq": int(sup["from_seq"]),
            "to_seq": int(sup["to_seq"]),
            "head_hash": str(sup["head_hash"]),
            "digest": str(sup["digest"]),
        }
        if (
            event.superseded["from_seq"] != event.restored_to[0] + 1
            or event.superseded["to_seq"] < event.superseded["from_seq"]
            or not anchor.HASH_RE.match(event.superseded["head_hash"])
            or not anchor.HASH_RE.match(event.superseded["digest"])
        ):
            msg = "superseded: not the range after restored_to, or not hashes"
            raise ValueError(msg)
    else:
        event.final = _seq_hash(doc, "final")
        event.certificate = str(doc.get("certificate", ""))
        if not CERTIFICATE_RE.match(event.certificate):
            msg = "certificate is not an id"
            raise ValueError(msg)
    if abs(written - stored) > anchor.CLOCK_SKEW:
        msg = "its stated time is far from the storage's"
        raise ValueError(msg)
    named = (
        KEY_TAIL_RE.match(key[len(prefix()) :]) if key.startswith(prefix()) else None
    )
    if not named or (named["kind"], named["hospital"]) != (kind, hospital):
        msg = "its object key is not the name the writer gives this event"
        raise ValueError(msg)
    return event


@dataclass
class Events:
    """Every event of this platform, by hospital; ``problems``: objects that are not
    events of ours (each one counts as a store problem of the watch, class "events")."""

    restores: dict = field(default_factory=dict)  # uuid -> [Event] by generation
    offboarded: dict = field(default_factory=dict)  # uuid -> Event
    problems: list = field(default_factory=list)
    read_ok: bool = True

    def generation(self, hospital_uuid):
        """The hospital's current restore generation (0: never restored)."""
        return len(self.restores.get(hospital_uuid, []))

    def closed(self):
        return set(self.offboarded)

    def excuse(self, hospital_uuid, seq, digest, first_stored):
        """For anchor.Fingerprints.flush: does a verified restore event account for this
        claim? Only a claim stored before the event, in its superseded range, whose hash its
        verified segment holds."""
        for event in self.restores.get(hospital_uuid, []):
            sup = event.superseded
            if (
                not event.problems
                and first_stored < event.stored
                and sup["from_seq"] <= seq <= sup["to_seq"]
                and event.segment_hashes.get(seq) == digest
            ):
                return True
        return False


def check_restore(event, segments=None):
    """Verify a restore event's superseded segment (fills ``event.problems`` and, when it
    holds, ``event.segment_hashes``)."""
    segments = segments or SEGMENTS
    try:
        segment = segments.get(event.hospital, event.generation)
    except Exception as exc:  # a broken archive excuses nothing
        segment = None
        event.problems.append(
            f"its superseded segment is unreadable ({type(exc).__name__})"
        )
    if segment is None:
        if not event.problems:
            event.problems.append(
                f"restore {event.generation} has no superseded segment to check it "
                "against (P1-T22 keeps them): it excuses nothing"
            )
        return event
    rows = sorted(segment.seals, key=lambda r: r.seq)
    sup = event.superseded
    _, head = event.restored_to
    problems = header_problems(
        rows, sup["from_seq"], bytes.fromhex(head), first_lower=None
    )
    if not rows or rows[-1].seq != sup["to_seq"]:
        problems.append("the segment does not end at the superseded range's end")
    elif bytes(rows[-1].hash).hex() != sup["head_hash"]:
        problems.append("the segment's last seal is not the event's head")
    if segment_digest(rows) != sup["digest"]:
        problems.append("the segment is not the one the event names (digest)")
    if segment.verify_entries is None:
        problems.append(
            "the segment's entries are not kept: its windows cannot be recomputed"
        )
    else:
        problems += list(segment.verify_entries())
    event.problems += problems
    if not event.problems:
        event.segment_hashes = {row.seq: bytes(row.hash).hex() for row in rows}
    return event


def read(store=None, segments=None):
    """Every event under this platform's prefix (the reader key). A storage error leaves
    ``read_ok`` False: nothing is excused, and the caller reports the store unreadable."""
    store = store or anchor.S3Store("READ")
    found = Events()
    seen = set()
    restores = {}
    for key, version, stored in store.versions(prefix()):
        if key in seen:
            found.problems.append(f"event {shown(key)}: written more than once")
            continue
        seen.add(key)
        if version is None:
            found.problems.append(f"event {shown(key)}: a delete marker hides it")
            continue
        body = store.get(key, version)  # a storage error: the whole read is unreadable
        try:
            event = parse(key, body, stored)
        except Exception as exc:  # any bad body is one bad object (C7)
            found.problems.append(
                f"event {shown(key)}: unreadable ({type(exc).__name__}: {shown(exc, 200)})"
            )
            continue
        if event.kind == "offboarding":
            found.offboarded.setdefault(event.hospital, event)
        else:
            restores.setdefault(event.hospital, []).append(event)
    for hospital_uuid, events in restores.items():
        events.sort(key=lambda e: (e.generation, e.stored))
        expected = 1
        for event in events:
            if event.generation != expected:
                text = (
                    f"restore generation {event.generation}, expected {expected} "
                    "(one is missing or written twice)"
                )
                event.problems.append(text)
                # A gap or a double is a bad event object too, not only an excuse refused
                # (P1-T21 review S4); a missing segment is not (P1-T22 keeps them)
                found.problems.append(f"event {event.key}: {text}")
            expected = event.generation + 1
            check_restore(event, segments)
        found.restores[hospital_uuid] = events
    return found


def _body(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()


def _key(kind, hospital_uuid, now):
    return (
        f"{prefix()}{now:%Y/%m/%d/%Y%m%dT%H%M%S}Z-{kind}-{hospital_uuid}-"
        f"{uuid.uuid4().hex[:8]}.json"
    )


def restore_document(
    hospital, generation, restored_to, superseded_rows, operator, now=None
):
    """The restore event of ``hospital`` (P1-T22 calls write_restore with it): ``restored_to``
    (seq, hash hex) is the restored chain's head, ``superseded_rows`` the live tail it
    replaced (SealRow, from restored_to.seq + 1), as kept in the superseded segment."""
    now = now or datetime.now(UTC)
    rows = sorted(superseded_rows, key=lambda r: r.seq)
    if not rows or rows[0].seq != restored_to[0] + 1:
        msg = "the superseded seals must start right after restored_to"
        raise ValueError(msg)
    if not OPERATOR_RE.match(operator or ""):
        msg = "operator: 1-64 of letters, digits, . _ @ -"
        raise ValueError(msg)
    return {
        "format": FORMAT,
        "platform": anchor.prefix(),
        "kind": "restore",
        "hospital": str(hospital.uuid),
        "slug": hospital.slug,
        "written_at": now.isoformat(),
        "operator": operator,
        "generation": int(generation),
        "restored_to": {"seq": int(restored_to[0]), "hash": str(restored_to[1])},
        "superseded": {
            "from_seq": rows[0].seq,
            "to_seq": rows[-1].seq,
            "head_hash": bytes(rows[-1].hash).hex(),
            "digest": segment_digest(rows),
        },
    }


def offboarding_document(hospital, final, operator, certificate, now=None):
    now = now or datetime.now(UTC)
    if not OPERATOR_RE.match(operator or "") or not CERTIFICATE_RE.match(
        certificate or ""
    ):
        msg = "operator or certificate is not an id"
        raise ValueError(msg)
    return {
        "format": FORMAT,
        "platform": anchor.prefix(),
        "kind": "offboarding",
        "hospital": str(hospital.uuid),
        "slug": hospital.slug,
        "written_at": now.isoformat(),
        "operator": operator,
        "final": {"seq": int(final[0]), "hash": str(final[1])},
        "certificate": certificate,
    }


def write(doc, store=None, now=None):
    """Add one event object (the add-only writer key; never overwrites). Returns its key."""
    store = store or anchor.S3Store("WRITE")
    when = datetime.fromisoformat(doc["written_at"]) if now is None else now
    key = _key(doc["kind"], doc["hospital"], when.astimezone(UTC))
    store.put(key, _body(doc))
    return key


def write_restore(
    hospital, generation, restored_to, superseded_rows, operator, store=None
):
    """For P1-T22's restore, after the superseded segment is archived."""
    return write(
        restore_document(hospital, generation, restored_to, superseded_rows, operator),
        store,
    )


def write_offboarding(hospital, final, operator, certificate, store=None):
    """For offboarding (TENANCY §7.7), before the hospital's schema is deleted."""
    return write(offboarding_document(hospital, final, operator, certificate), store)
