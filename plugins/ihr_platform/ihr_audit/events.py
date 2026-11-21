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

Writers (owner-run): ``write_restore`` and ``write_offboarding``, with the events key
(anchor-events.env, Q-K91; until a site has one, the fingerprint writer's key, which is then
no security boundary between Celery and these objects). Readers: ``read`` (the reader key,
anchor-read.env).

**Whole-database restores (P1-T22c; docs/reviews/p1-t22c/design.md, "After the attack"
wins).** A restore of the whole database (``staging.sh rollback --restore``) brings every
hospital back to one moment T. Its owner-run tool (supersede.py) writes, beside the events, a
write-once **plan** before the destructive step and a **result** after the load:

    anchors/ihr-restores/<platform suffix>/<restore id>/plan.json     (PLAN_FORMAT)
    anchors/ihr-restores/<platform suffix>/<restore id>/result.json   (RESULT_FORMAT)

and one ordinary restore event per hospital whose tail it superseded, with two more fields,
``"cause": "whole-database"`` and ``"restore_id"``. Builds before P1-T22c ignore both fields and
the plans and results: they excuse on the segment rule alone. This build also requires
(``check_whole``), else the event excuses nothing:

- a plan stored before the event, naming the hospital as exported, whose lower bound L (the
  claim of the newest fingerprint stored at or before T, re-read here from the fingerprints, S3)
  is the event's ``restored_to`` (F = L), and whose head and digest are the event's;
- a result stored after the plan and after the event, naming that event key and generation,
  whose new timeline is later than the plan's old one (S2);
- the result is **anchored** (F4): the newest result's new timeline is the database's now, and
  each older result's new timeline is the next plan's old one;
- with the watch's own timeline history (``history``), the change from the plan's old timeline
  to the result's new one was seen between the plan's and the result's stored times (S2).

**Disaster restores (P1-T22c2a; docs/reviews/p1-t22c/design-c2.md, "c2a as decided").**
``staging.sh restore --target T`` writes a **stub plan** (``flow: "disaster"``, no hospitals)
before the restore, serves first, then exports each hospital's tail from a copy restored from
the backup repository and writes the **export doc** beside it:

    anchors/ihr-restores/<platform suffix>/<restore id>/export.json   (EXPORT_FORMAT)

A disaster event's hospital entry comes from the export doc. Its fork point F is the restored
head, so it is bound from below by entry times (audit S2): the first superseded seal must hold an
entry after ``floor_cutoff(T)``, unless a single-hospital restore event stored after T - skew
names F (``fork_floor_problem``). Stubs, export docs and a disaster's events and result are
written with the events key only (``events_store``, audit S5).
"""

import hashlib
import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ihr_platform import db_limits

from ihr_audit import anchor, oneline, seal

FORMAT = "ihr-audit-event/1"
PLAN_FORMAT = "ihr-restore-plan/1"
RESULT_FORMAT = "ihr-restore-result/1"
EXPORT_FORMAT = "ihr-restore-export/1"  # P1-T22c2a
FLOWS = ("planned-rollback", "disaster")
CLOSED_HOW = ("close", "no-supersede")
ACKNOWLEDGED_CLOSED = Path(__file__).with_name("acknowledged_closed.txt")


def acknowledged_closed(path=ACKNOWLEDGED_CLOSED):
    """Reviewed closed results that no longer alert (P1-T22c2a review T2, Q-K105):
    {restore id: reason}. A line is ``<restore id> <YYYY-MM-DD> <reason>``."""
    found = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        rid, day, *reason = line.split()
        datetime.strptime(day, "%Y-%m-%d")  # noqa: DTZ007 (a review date, no time)
        try:
            uuid.UUID(rid)
        except ValueError:
            reason = []
        if not reason:
            msg = f"acknowledged_closed.txt: {line!r} needs a restore id, a date and a reason"
            raise ValueError(msg)
        found[rid] = " ".join(reason)
    return found


KINDS = ("restore", "offboarding")
CAUSES = ("whole-database",)
TIMELINE_RE = re.compile(
    r"^(?!0{8})[0-9A-F]{8}$"
)  # Postgres's timelines start at 1 (review T5)
IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
POINT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
PLAN_OUTCOMES = ("exported", "absent")
EXPORT_OUTCOMES = ("exported", "unreadable")
RESULT_OUTCOMES = ("loaded", "unchanged", "absent", "refused", "unreadable")
RESTORE_KEY_RE = re.compile(
    r"^(?P<rid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/"
    r"(?P<what>plan|export|result)\.json$"
)
BACKUP_SET_RE = re.compile(r"^[0-9]{8}-[0-9]{6}F(_[0-9]{8}-[0-9]{6}[DI])?$")
WAL_RE = re.compile(r"^(?!0{8})[0-9A-F]{24}$")
LAST_COMMIT_MAX = 40  # an ISO 8601 time with its offset
REASON_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,200}$")  # one line, no control character
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


def restores_prefix():
    """Where this platform's whole-database restore plans and results live (P1-T22c)."""
    suffix = anchor.prefix()[len("anchors/") :].rstrip("/")
    return f"anchors/ihr-restores/{suffix}/"


def plan_key(restore_id):
    return f"{restores_prefix()}{restore_id}/plan.json"


def result_key(restore_id):
    return f"{restores_prefix()}{restore_id}/result.json"


def export_key(restore_id):
    return f"{restores_prefix()}{restore_id}/export.json"


def writer_store():
    """The store events, plans and results are added with: the events key (Q-K91), else,
    until the site has one, the fingerprint writer's key scoped to the same prefixes (no
    security boundary then: Celery holds that key; the runbook says so)."""
    try:
        return anchor.S3Store("EVENTS")
    except anchor.NotConfiguredError:
        return anchor.S3Store("WRITE", scope_as="EVENTS")


def events_store():
    """The events key alone (Q-K91), for a disaster restore's documents: a stub, an export
    doc, its events and result. Never the fingerprint writer's key, which Celery holds (audit
    S5): without the events key it raises NotConfiguredError (fails closed)."""
    return anchor.S3Store("EVENTS")  # no fallback to the writer's key (audit S5)


TIMELINE_SQL = "SELECT substr(pg_walfile_name(pg_current_wal_lsn()), 1, 8)"


def current_timeline():
    """The database's timeline now (8 hex digits): a whole-database restore starts a new one.
    Every role may ask (no grant needed); None when it cannot be read."""
    from django.db import connection

    try:
        with connection.cursor() as cursor:
            cursor.execute(TIMELINE_SQL)
            value = cursor.fetchone()[0]
    except Exception:
        return None
    return value if TIMELINE_RE.match(value or "") else None


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
    # callable(seq) -> the newest ``at`` of that archived seal's entries (None: none kept);
    # the disaster fork floor reads it (P1-T22c2a). None: cannot be read
    latest_at: object = None


class _Segments:
    """Where superseded segments are read from. P1-T22 sets ``source``; until then none is
    found and every restore event excuses nothing (fails closed)."""

    source = None

    def get(self, hospital_uuid, generation):
        if self.source is None:
            return None
        return self.source(hospital_uuid, generation)


SEGMENTS = _Segments()
NO_SEGMENTS = (
    _Segments()
)  # reads none: for a step that has no database (a stub, an export doc)


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
    cause: str = ""  # "whole-database" (P1-T22c), else ""
    restore_id: str = ""  # the whole-database restore's id
    problems: list = field(default_factory=list)  # why it excuses nothing
    segment_hashes: dict = field(default_factory=dict)  # seq -> hash hex, once verified
    segment: object = field(default=None, repr=False, compare=False)  # once verified


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
        if (
            "cause" in doc or "restore_id" in doc
        ):  # a whole-database restore's (P1-T22c)
            event.cause, event.restore_id = doc.get("cause"), doc.get("restore_id")
            if event.cause not in CAUSES or not anchor.UUID_RE.match(
                str(event.restore_id)
            ):
                msg = "cause or restore_id is not ours"
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
    # Whole-database restores (P1-T22c): restore id -> Plan / Result, the database's timeline
    # when read, the results the timeline chain anchors, and why the others are not
    plans: dict = field(default_factory=dict)
    results: dict = field(default_factory=dict)
    timeline: str | None = None
    anchored: set = field(default_factory=set)
    unanchored: dict = field(default_factory=dict)
    excused_by: Counter = field(
        default_factory=Counter
    )  # restore id ("" single) -> claims
    # Hospitals with an event object the reader could not count (unreadable, or a key
    # written more than once): their next generation is unknown (review T6)
    unsure: dict = field(default_factory=dict)  # uuid -> why
    # Restore ids whose S2 change the watch's history could not judge (review T5)
    uncorroborated: set = field(default_factory=set)
    # Disaster restores' export docs (P1-T22c2a): restore id -> ExportDoc
    exports: dict = field(default_factory=dict)

    def generation(self, hospital_uuid):
        """The hospital's current restore generation (0: never restored): the highest one in
        the bucket, never a count (a gap or a dropped object must not make a new restore take
        a generation the bucket already has; review T6)."""
        return max(
            (e.generation for e in self.restores.get(hospital_uuid, [])), default=0
        )

    def generation_unsure(self, hospital_uuid):
        """Why the hospital's next generation cannot be taken from the bucket, or ""."""
        why = self.unsure.get(str(hospital_uuid), "")
        return (
            f"an event object of it in the bucket cannot be counted ({why}): its next "
            "restore generation is unknown; refused until the object is looked at"
            if why
            else ""
        )

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
                self.excused_by[event.restore_id] += 1
                return True
        return False

    def pending_loads(self):
        """Whole-database restores planned, with no result, whose old timeline is not the
        database's any more: the restore happened and its load has not finished. A
        single-hospital restore waits for them (its generation would be taken twice)."""
        return self.unfinished(self.timeline)

    def unfinished(self, timeline):
        """The plans with no result, not superseded, whose old timeline is not ``timeline``:
        from the plans alone, no watch history needed (P1-T22c2a, goldfish C2-1). ``timeline``:
        the database's (flow a, the watch) or the repository's newest (a disaster stub)."""
        if timeline is None:
            return []
        return sorted(
            rid
            for rid, plan in self.plans.items()
            if rid not in self.results
            and plan.old_timeline != timeline
            and not self.superseded_plan(plan)
        )

    def superseded_plan(self, plan):
        """A plan that never led to a restore (review E1): another plan from the same old
        timeline has a result. A plan's old timeline is the database's at its export, and only
        one restore can move the database off a timeline (the next one gets a new number), so
        that other run, not this one, moved it on. A closed result (nothing loaded) supersedes
        only the plans stored before it, never a later attempt (P1-T22c2a review E3/Q1: a
        closed failed attempt, or one whose new timeline never reached the repository, would
        silence the retry that really moved the database)."""
        return any(
            other.restore_id != plan.restore_id
            and other.restore_id in self.results
            and other.old_timeline == plan.old_timeline
            and (
                not self.results[other.restore_id].closed or other.stored > plan.stored
            )
            for other in self.plans.values()
        )

    def closed_results(self, acknowledged=()):
        """Results of whole-database restores whose audit tails were never loaded (``close``,
        ``restore --no-supersede``), not acknowledged: {restore id: Result}. The timeline change
        is explained, the cuts stay, but data after the last fingerprint can be lost with no
        trace (P1-T22c2a review T2, Q-K105): they alert until a reviewer acknowledges them."""
        return {
            rid: result
            for rid, result in sorted(self.results.items())
            if result.closed and rid not in acknowledged
        }

    def closed_problems(self, acknowledged=()):
        """``closed_results`` as lines for ihr_audit_verify."""
        return [
            f"whole-database restore {rid}: closed without loading its audit tails "
            f"({r.closed.get('how')}: {r.closed.get('reason')}); data after the last "
            "fingerprint may be lost with no trace. Review it, then list it in "
            "ihr_audit/acknowledged_closed.txt"
            for rid, r in self.closed_results(acknowledged).items()
        ]

    def next_step(self, restore_id):
        """What finishes a pending whole-database restore, in words that depend on its flow: a
        disaster restore is exported before it is loaded (review Q5)."""
        rid = str(restore_id)
        plan = self.plans.get(rid)
        if plan is not None and plan.flow == FLOWS[1] and rid not in self.exports:
            return f"scripts/staging.sh audit-supersede export {rid}, then load {rid}"
        return f"scripts/staging.sh audit-supersede load {rid}"

    def later_attempts(self, plan):
        """Plans from the same old timeline, stored after ``plan``, with no result: one of them,
        not ``plan``, may be the restore that moved the database (review E3)."""
        return sorted(
            other.restore_id
            for other in self.plans.values()
            if other.restore_id != plan.restore_id
            and other.old_timeline == plan.old_timeline
            and other.stored > plan.stored
            and other.restore_id not in self.results
        )


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
        event.segment = segment
    return event


# --- whole-database restores: plans and results (P1-T22c) -------------------------------------


@dataclass
class Plan:
    key: str
    restore_id: str
    stored: datetime
    written_at: datetime
    operator: str
    target_name: str
    target_time: datetime
    old_timeline: str
    image: str
    hospitals: dict  # uuid -> {slug, outcome, lower, head, digest, entries, ...}
    flow: str = "planned-rollback"
    source: dict = field(default_factory=dict)  # disaster: backup_set, archive_max
    no_supersede: str = ""  # disaster: the reason, when nothing is exported


@dataclass
class ExportDoc:
    """A disaster restore's per-hospital half of its plan, written after the copy's export."""

    key: str
    restore_id: str
    stored: datetime
    written_at: datetime
    operator: str
    plan: str
    old_timeline: str
    image: str
    source_end: dict
    hospitals: dict  # uuid -> {slug, outcome, lower, head, digest, ..., reason}
    recovery: dict = field(
        default_factory=dict
    )  # {stop, replayed}: the restore's own (T3)


def recovery_problem(recovery, target_time):
    """Review T3: why the restore's own record does not put the stub's T inside it, or "".
    ``stop``: the new timeline's history file's "before" time (the first commit after the
    target, not applied); ``replayed``: the last commit the recovery applied ("" if none).
    T must satisfy replayed <= T < stop: the restored database is then the state at T, whatever
    target pgBackRest was given (a quiet database may stop minutes after T: still the state at
    T). The c2b ceiling (S6) completes it."""
    if not isinstance(recovery, dict) or not recovery.get("stop"):
        return "no recovery stop time (the restore's history file) is recorded"
    try:
        stop = _time(recovery, "stop")
        replayed = _time(recovery, "replayed") if recovery.get("replayed") else None
    except (ValueError, TypeError):
        return "the recovery's stop or last applied commit is not a time"
    if stop <= target_time:
        return (
            f"the recovery stopped before a commit at {stop.isoformat()}, not after T "
            f"({target_time.isoformat()}): its target was earlier than the stub's"
        )
    if replayed is not None and replayed > target_time:
        return (
            f"the recovery applied a commit at {replayed.isoformat()}, after T "
            f"({target_time.isoformat()}): its target was later than the stub's"
        )
    return ""


@dataclass
class Result:
    key: str
    restore_id: str
    stored: datetime
    written_at: datetime
    operator: str
    plan: str
    old_timeline: str
    new_timeline: str
    hospitals: dict  # uuid -> {slug, outcome, generation, event, fork}
    closed: dict = field(default_factory=dict)  # {reason, how}: closed, nothing loaded


def _int(value, low=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        msg = "not a whole number in range"
        raise ValueError(msg)
    return value


def _hash(value, empty=False):
    if not isinstance(value, str) or not (
        anchor.HASH_RE.match(value) or (empty and value == "")
    ):
        msg = "not a sha256 in hex"
        raise ValueError(msg)
    return value


def _point(part):
    if not isinstance(part, dict):
        msg = "not a seal number and hash"
        raise ValueError(msg)
    return {"seq": _int(part.get("seq")), "hash": _hash(part.get("hash"))}


def _plan_hospital(entry, outcomes=PLAN_OUTCOMES):
    if not isinstance(entry, dict):
        msg = "a hospital entry that is not an object"
        raise ValueError(msg)
    lower, head = _point(entry.get("lower")), _point(entry.get("head"))
    fingerprint = entry.get("lower", {}).get("fingerprint", "")
    if not isinstance(fingerprint, str) or (
        fingerprint and not fingerprint.startswith(anchor.prefix())
    ):
        msg = "lower.fingerprint is not one of this platform's fingerprints"
        raise ValueError(msg)
    lower["fingerprint"] = fingerprint[:300]
    generations = entry.get("generations", [])
    if not isinstance(generations, list):
        msg = "generations is not a list"
        raise ValueError(msg)
    out = {
        "slug": entry.get("slug"),
        "outcome": entry.get("outcome"),
        "lower": lower,
        "head": head,
        "digest": _hash(entry.get("digest", ""), empty=True),
        "entries": _int(entry.get("entries", 0)),
        "unsealed": _int(entry.get("unsealed", 0)),
        "generations": [_int(g, 1) for g in generations],
        "package_sha256": _hash(entry.get("package_sha256", ""), empty=True),
    }
    if (
        not (isinstance(out["slug"], str) and anchor.SLUG_RE.match(out["slug"]))
        or out["outcome"] not in outcomes
        or head["seq"] < lower["seq"]
        or (head["seq"] > lower["seq"]) != bool(out["digest"])
    ):
        msg = "slug, outcome, head or digest is not ours"
        raise ValueError(msg)
    if out["outcome"] == "unreadable":  # an export doc's: why, and nothing exported
        out["reason"] = entry.get("reason")
        if (
            not (isinstance(out["reason"], str) and REASON_RE.match(out["reason"]))
            or (head["seq"], head["hash"]) != (lower["seq"], lower["hash"])
            or out["package_sha256"]
        ):
            msg = "an unreadable hospital names a package, or no reason"
            raise ValueError(msg)
    return out


def _result_hospital(entry):
    if not isinstance(entry, dict):
        msg = "a hospital entry that is not an object"
        raise ValueError(msg)
    out = {
        "slug": entry.get("slug"),
        "outcome": entry.get("outcome"),
        "generation": _int(entry.get("generation", 0)),
        "event": entry.get("event", ""),
        "fork": _int(entry.get("fork", 0)),
    }
    named = out["event"]
    if (
        not (isinstance(out["slug"], str) and anchor.SLUG_RE.match(out["slug"]))
        or out["outcome"] not in RESULT_OUTCOMES
        or not isinstance(named, str)
        or (named and not KEY_TAIL_RE.match(named[len(prefix()) :]))
        or (named and not named.startswith(prefix()))
        or (out["outcome"] == "loaded") != bool(named)
    ):
        msg = "slug, outcome or event key is not ours"
        raise ValueError(msg)
    return out


def _time(doc, name):
    value = doc.get(name)
    if not isinstance(value, str):
        msg = f"{name} is not a time"
        raise ValueError(msg)
    when = datetime.fromisoformat(value)
    if when.tzinfo is None:
        msg = f"{name} has no time zone"
        raise ValueError(msg)
    return when


def parse_restore_doc(key, body, stored):
    """A plan or result object, checked field by field (text from the bucket is never
    trusted, never shown unescaped)."""
    if len(body) > anchor.MAX_OBJECT_BYTES:
        msg = "too large"
        raise ValueError(msg)
    doc = json.loads(body)
    if not isinstance(doc, dict):
        msg = "not a JSON object"
        raise ValueError(msg)
    named = (
        RESTORE_KEY_RE.match(key[len(restores_prefix()) :])
        if key.startswith(restores_prefix())
        else None
    )
    fmt = {"plan": PLAN_FORMAT, "result": RESULT_FORMAT, "export": EXPORT_FORMAT}.get(
        named["what"] if named else ""
    )
    if not named or doc.get("format") != fmt or doc.get("platform") != anchor.prefix():
        msg = "unknown format, platform or object key"
        raise ValueError(msg)
    rid, operator = doc.get("restore_id"), doc.get("operator")
    if rid != named["rid"] or not (
        isinstance(operator, str) and OPERATOR_RE.match(operator)
    ):
        msg = "restore_id or operator is not ours"
        raise ValueError(msg)
    written = _time(doc, "written_at")
    if abs(written - stored) > anchor.CLOCK_SKEW:
        msg = "its stated time is far from the storage's"
        raise ValueError(msg)
    if not isinstance(doc.get("hospitals"), dict) or not all(
        anchor.UUID_RE.match(str(u)) for u in doc["hospitals"]
    ):
        msg = "hospitals is not of our shape"
        raise ValueError(msg)
    old = doc.get("old_timeline")
    if not (isinstance(old, str) and TIMELINE_RE.match(old)):
        msg = "old_timeline is not a timeline"
        raise ValueError(msg)
    if named["what"] == "plan":
        target = doc.get("target")
        if (
            not isinstance(target, dict)
            or not isinstance(target.get("name"), str)
            or not POINT_RE.match(target["name"])
            or not (isinstance(doc.get("image"), str) and IMAGE_RE.match(doc["image"]))
        ):
            msg = "target or image is not ours"
            raise ValueError(msg)
        return _plan(doc, key, rid, stored, written, operator, target, old)
    if named["what"] == "export":
        return _export(doc, key, rid, stored, written, operator, old)
    new, plan = doc.get("new_timeline"), doc.get("plan")
    if not (isinstance(new, str) and TIMELINE_RE.match(new)) or plan != plan_key(rid):
        msg = "new_timeline or plan is not ours"
        raise ValueError(msg)
    closed = doc.get("closed", {})
    if closed and not (
        isinstance(closed, dict)
        and closed.get("how") in CLOSED_HOW
        and isinstance(closed.get("reason"), str)
        and REASON_RE.match(closed["reason"])
    ):
        msg = "closed is not ours"
        raise ValueError(msg)
    return Result(
        key,
        rid,
        stored,
        written,
        operator,
        plan,
        old,
        new,
        {u: _result_hospital(e) for u, e in doc["hospitals"].items()},
        dict(closed),
    )


def _plan(doc, key, rid, stored, written, operator, target, old):
    flow = doc.get("flow", FLOWS[0])
    if flow not in FLOWS:
        msg = "flow is not ours"
        raise ValueError(msg)
    source, reason = {}, ""
    if flow == "disaster":  # a stub: no hospitals, the repository it was restored from
        part = doc.get("source")
        if (
            doc["hospitals"]
            or not isinstance(part, dict)
            or not BACKUP_SET_RE.match(str(part.get("backup_set", "")))
            or not WAL_RE.match(str(part.get("archive_max", "")))
            or part["archive_max"][:8] != old
        ):
            msg = "a disaster stub's hospitals or source is not ours"
            raise ValueError(msg)
        source = {"backup_set": part["backup_set"], "archive_max": part["archive_max"]}
        if "no_supersede" in doc:
            reason = (
                (doc["no_supersede"] or {}).get("reason")
                if isinstance(doc["no_supersede"], dict)
                else None
            )
            if not (isinstance(reason, str) and REASON_RE.match(reason)):
                msg = "no_supersede is not ours"
                raise ValueError(msg)
    return Plan(
        key,
        rid,
        stored,
        written,
        operator,
        target["name"],
        _time(target, "time"),
        old,
        doc["image"],
        {u: _plan_hospital(e) for u, e in doc["hospitals"].items()},
        flow,
        source,
        reason,
    )


def _export(doc, key, rid, stored, written, operator, old):
    end = doc.get("source_end")
    if (
        doc.get("plan") != plan_key(rid)
        or not (isinstance(doc.get("image"), str) and IMAGE_RE.match(doc["image"]))
        or not isinstance(end, dict)
        or not BACKUP_SET_RE.match(str(end.get("backup_set", "")))
        or not isinstance(end.get("last_commit", ""), str)
        or len(end.get("last_commit", "")) > LAST_COMMIT_MAX
    ):
        msg = "plan, image or source_end is not ours"
        raise ValueError(msg)
    if end.get("last_commit"):
        _time(end, "last_commit")
    recovery = doc.get("recovery", {})
    if recovery and (
        not isinstance(recovery, dict)
        or set(recovery) - {"stop", "replayed"}
        or not all(
            isinstance(recovery.get(k, ""), str)
            and len(recovery.get(k, "")) <= LAST_COMMIT_MAX
            for k in ("stop", "replayed")
        )
    ):
        msg = "recovery is not ours"
        raise ValueError(msg)
    return ExportDoc(
        key,
        rid,
        stored,
        written,
        operator,
        doc["plan"],
        old,
        doc["image"],
        {"last_commit": end.get("last_commit", ""), "backup_set": end["backup_set"]},
        {u: _plan_hospital(e, EXPORT_OUTCOMES) for u, e in doc["hospitals"].items()},
        {k: recovery.get(k, "") for k in ("stop", "replayed")} if recovery else {},
    )


def _read_restore_docs(store, found):
    seen = set()
    for key, version, stored in store.versions(restores_prefix()):
        if key in seen:
            found.problems.append(
                f"restore object {shown(key)}: written more than once"
            )
            found.plans.pop(key.rsplit("/", 2)[-2], None)
            found.results.pop(key.rsplit("/", 2)[-2], None)
            found.exports.pop(key.rsplit("/", 2)[-2], None)
            continue
        seen.add(key)
        if version is None:
            found.problems.append(
                f"restore object {shown(key)}: a delete marker hides it"
            )
            continue
        body = store.get(key, version)
        try:
            doc = parse_restore_doc(key, body, stored)
        except Exception as exc:  # any bad body is one bad object (C7)
            found.problems.append(
                f"restore object {shown(key)}: unreadable "
                f"({type(exc).__name__}: {shown(exc, 200)})"
            )
            continue
        where = (
            found.plans
            if isinstance(doc, Plan)
            else found.exports
            if isinstance(doc, ExportDoc)
            else found.results
        )
        where[doc.restore_id] = doc
    _check_exports(found)
    _check_results(found)


def _check_exports(found):
    """An export doc continues a disaster stub, stored after it, from the same old timeline."""
    for rid, exp in list(found.exports.items()):
        plan = found.plans.get(rid)
        why = ""
        if plan is None or plan.flow != "disaster":
            why = "no disaster plan before it"
        elif exp.stored < plan.stored:
            why = "stored before its plan"
        elif exp.old_timeline != plan.old_timeline:
            why = "its old timeline is not its plan's"
        if why:
            found.problems.append(f"restore object {shown(exp.key)}: export doc, {why}")
            found.exports.pop(rid)


def _check_results(found):
    """A result whose plan is missing, or that names its plan's old timeline as its new one, is
    a bad object too (S2): it anchors nothing."""
    for rid, result in list(found.results.items()):
        plan = found.plans.get(rid)
        why = ""
        if plan is None:
            why = "no plan"
        elif result.stored < plan.stored:  # whole seconds: the same second is after
            why = "stored before its plan"
        elif result.old_timeline != plan.old_timeline or int(
            result.new_timeline, 16
        ) <= int(plan.old_timeline, 16):
            why = "its timelines do not move on from its plan's"
        if why:
            found.problems.append(f"restore object {shown(result.key)}: {why}")
            found.results.pop(rid)


def anchor_results(found):
    """F4: only the newest result's new timeline must be the database's now; each older one
    is accepted through the chain (the next plan's old timeline is its new one)."""
    found.anchored, found.unanchored = set(), {}
    results = sorted(found.results.values(), key=lambda r: (r.stored, r.key))
    if not results:
        return
    if found.timeline is None:
        for result in results:
            found.unanchored[result.restore_id] = (
                "the database's timeline could not be read"
            )
        return
    newer = None
    for result in reversed(results):
        if newer is None:
            ok = result.new_timeline == found.timeline
            why = (
                f"its new timeline {result.new_timeline} is not the database's "
                f"({found.timeline})"
            )
        else:
            nxt = found.plans.get(newer.restore_id)
            ok = (
                newer.restore_id in found.anchored
                and nxt is not None
                and nxt.old_timeline == result.new_timeline
            )
            why = (
                f"the next whole-database restore ({newer.restore_id[:8]}) does not "
                f"continue from its timeline {result.new_timeline}"
            )
        if ok:
            found.anchored.add(result.restore_id)
        else:
            found.unanchored[result.restore_id] = why
        newer = result


CANNOT_SAY = (
    "cannot say"  # corroborated(): the history does not show the change either way
)


def corroborated(plan, result, history):
    """S2: the watch's own timeline history ([{"timeline", "first", "last"}], oldest first)
    shows the change from the plan's old timeline to the result's new one between the plan's
    and the result's stored times. Returns why not, "" when it does, or CANNOT_SAY when the
    watch has not seen the new timeline yet, or began on it (never a silent pass: the watch
    counts those in ihr_audit_whole_restores_uncorroborated; review T5)."""
    index = next(
        (i for i, h in enumerate(history) if h.get("timeline") == result.new_timeline),
        None,
    )
    if not index:
        return CANNOT_SAY
    before, after = history[index - 1], history[index]
    if before.get("timeline") != plan.old_timeline:
        return (
            f"the watch saw the database move to timeline {result.new_timeline} from "
            f"{before.get('timeline')}, not from the plan's {plan.old_timeline}"
        )
    if not plan.stored < datetime.fromisoformat(after["first"]):
        return "its plan was stored after the watch already saw the new timeline"
    if not result.stored > datetime.fromisoformat(before["last"]):
        return "its result was stored while the watch still saw the old timeline"
    return ""


def _lower_claim(plan, hospital_uuid, store, cache):
    """(seq, hash) the newest fingerprint stored at or before the plan's restore point claims
    for the hospital, (0, genesis) if it names none; None if the fingerprints are unreadable."""
    if plan.key not in cache:
        try:
            cache[plan.key] = anchor.claims_at(store, plan.target_time)[1]
        except Exception:
            cache[plan.key] = None
    claims = cache[plan.key]
    if claims is None:
        return None
    seq, digest, _slug = claims.get(hospital_uuid, (0, seal.GENESIS.hex(), ""))
    return seq, digest


# --- disaster restores: the lower bound and the fork floor (P1-T22c2a) ------------------------

# How far before T the newest entry of the first superseded seal may lie: the writer's clock
# skew plus the worker profile's idle-in-transaction limit (db_limits; Q-K99). That limit bounds
# only the idle gaps of a transaction, not its total time (the worker's statement limit is 300 s
# and the owner profile has none; no transaction_timeout is set, Q-K96): a transaction that
# stays open longer than this and commits just after T makes the first seal after T hold only
# older entries, a false refusal that fails closed (the load is refused: close it; review T4,
# known limit, Q-K99). An entry committed after T was inserted (``at`` = clock_timestamp()) at
# most that long before its commit in every other case
FLOOR_MARGIN = anchor.CLOCK_SKEW + timedelta(
    milliseconds=db_limits.PROFILES["worker"].idle_in_transaction
)


def floor_cutoff(target_time):
    return target_time - FLOOR_MARGIN


def verified_before(other, event):
    """Review T1: does ``other`` count for a reader's lower bound or fork floor of the disaster
    event ``event``? Only a verified event (no problems: its superseded segment checked) of an
    earlier generation, stored no later than it. A forged event (no segment) moves nothing."""
    return (
        not other.problems
        and other.generation < event.generation
        and other.stored <= event.stored
    )


def fork_floor_problem(target_time, fork, latest_at, restores, event=None):
    """Audit S2: why a disaster restore's fork point F is below its floor, or "". The first
    superseded seal (F + 1) was sealed after T, so it must hold an entry newer than
    ``floor_cutoff(T)`` (``latest_at``: its newest entry's ``at``, which the seal hash covers).
    Else the restored chain was cut below its head at T (a rewritten restored seal): refused,
    unless a single-hospital restore event (no ``cause``) stored at or after T - skew names F
    as its ``restored_to`` (a restore from an older dump after T; S3). ``event``: the disaster
    event (every reader): only an event ``verified_before`` it counts (review T1). The load's
    own check (None) reads no segments: a restore after T has its segment only in the
    package; the readers' stricter check then refuses a forged one."""
    cutoff = floor_cutoff(target_time)
    if latest_at is not None and latest_at > cutoff:
        return ""
    for other in restores:
        if event is not None and not verified_before(other, event):
            continue
        if (
            not other.cause
            and other.stored >= target_time - anchor.CLOCK_SKEW
            and tuple(other.restored_to or ()) == tuple(fork)
        ):
            return ""
    newest = "none" if latest_at is None else latest_at.isoformat()
    return (
        f"its fork point (seal {fork[0]}) is below the floor: the first superseded seal holds "
        f"no entry after {cutoff.isoformat()} (T - {FLOOR_MARGIN}; its newest: {newest}), so "
        "it was in the database at T (audit S2)"
    )


def lower_of(claim, claim_stored, restores, before, generation=None):
    """A disaster restore's lower bound L (design.md §3): the claim (seq, hash) of the newest
    fingerprint stored at or before T - skew, lowered to the ``restored_to`` of any restore
    event of the hospital stored after that fingerprint and before ``before`` (the stub's
    stored time: an event after it, this restore's own or a later one on the new timeline, is
    nothing the repository copy holds). No claim: (0, genesis) (live proof: an event never
    raises L above genesis). ``generation`` (every reader: the disaster event's): only a
    verified event (no problems) of an earlier generation lowers L (review T1). The exporters
    (None) read no segments, so they go by time only: a forged event they took makes the
    export's L differ from the readers', and the disaster event excuses nothing."""
    if not claim:
        return (0, seal.GENESIS.hex())
    best = tuple(claim[:2])
    for event in restores:
        if event.stored <= claim_stored or event.stored >= before:
            continue
        if generation is not None and (
            event.problems or event.generation >= generation
        ):
            continue
        here = tuple(event.restored_to or ())
        if here and here[0] < best[0]:
            best = here
    return best


def disaster_lower(plan, hospital_uuid, store, cache, restores, generation):
    """L of one hospital for a disaster plan, re-read from the bucket; None if unreadable.
    ``generation``: the disaster event's (only verified earlier events count; review T1)."""
    ckey = ("disaster", plan.key)
    if ckey not in cache:
        try:
            _key, stored, claims = anchor.claims_at_full(
                store, plan.target_time - anchor.CLOCK_SKEW
            )
            cache[ckey] = (stored, claims)
        except Exception:
            cache[ckey] = None
    if cache[ckey] is None:
        return None
    stored, claims = cache[ckey]
    claim = claims.get(hospital_uuid)
    return lower_of(claim, stored if claim else None, restores, plan.stored, generation)


def _check_disaster(event, found, store, cache, why):
    """check_whole's half for a disaster plan: the hospital entry is the export doc's."""
    plan, exp = found.plans[event.restore_id], found.exports.get(event.restore_id)
    sup = event.superseded
    if exp is None or exp.stored > event.stored:
        why.append("no export doc stored before it")
        return
    text = recovery_problem(exp.recovery, plan.target_time)
    if text:  # every reader requires it (review T3)
        why.append(f"its export doc: {text}")
    mine = exp.hospitals.get(event.hospital)
    if mine is None or mine["outcome"] != "exported":
        why.append("its export doc did not export this hospital")
        return
    lower = (mine["lower"]["seq"], mine["lower"]["hash"])
    if not lower[0] <= event.restored_to[0] < mine["head"]["seq"]:
        why.append(
            f"its fork point (seal {event.restored_to[0]}) is not between its export's lower "
            f"bound (seal {lower[0]}) and head (seal {mine['head']['seq']})"
        )
    if (mine["head"]["seq"], mine["head"]["hash"]) != (sup["to_seq"], sup["head_hash"]):
        why.append("its superseded tail does not end at its export's head")
    claim = disaster_lower(
        plan,
        event.hospital,
        store,
        cache,
        found.restores.get(event.hospital, []),
        event.generation,
    )
    if claim is None:
        why.append("the fingerprints at its restore point are unreadable")
    elif claim != lower:
        why.append(
            f"its export's lower bound (seal {lower[0]}) is not the one the bucket gives at "
            f"T - skew (seal {claim[0]})"
        )


def check_floor(event, found):
    """After check_restore: a disaster event's fork floor, from the verified archived segment
    (every reader; the load checks it too). Adds a problem."""
    plan = found.plans.get(event.restore_id)
    if event.problems or plan is None or plan.flow != "disaster":
        return event
    segment, first = event.segment, event.superseded["from_seq"]
    try:
        latest = segment.latest_at(first) if segment and segment.latest_at else None
    except Exception:  # unreadable: no floor, so it excuses nothing
        latest = None
    text = fork_floor_problem(
        plan.target_time,
        event.restored_to,
        latest,
        found.restores.get(event.hospital, []),
        event,
    )
    if text:
        event.problems.append(f"whole-database restore {event.restore_id[:8]}: {text}")
        event.segment_hashes = {}
    return event


def check_whole(event, found, store, history=None, cache=None):  # noqa: PLR0912
    """The whole-database checks of one restore event (see the module text). Adds problems:
    an event with any excuses nothing."""
    rid, why = event.restore_id, []
    plan, result = found.plans.get(rid), found.results.get(rid)
    cache = {} if cache is None else cache
    # Storage times are whole seconds (S3 LastModified): "before" and "after" below allow the
    # same second, never an earlier one
    if plan is None or plan.stored > event.stored:
        why.append("no plan stored before it")
    if result is None:
        why.append("no result names it")
    else:
        entry = result.hospitals.get(event.hospital) or {}
        if (
            entry.get("event") != event.key
            or entry.get("generation") != event.generation
            or entry.get("outcome") != "loaded"
        ):
            why.append("its result does not name it")
        if result.stored < event.stored:
            why.append("its result was stored before it")
    if plan is not None and plan.flow == "disaster":
        _check_disaster(event, found, store, cache, why)
    elif plan is not None:
        mine = plan.hospitals.get(event.hospital)
        sup = event.superseded
        if mine is None or mine["outcome"] != "exported":
            why.append("its plan did not export this hospital")
        else:
            lower = (mine["lower"]["seq"], mine["lower"]["hash"])
            if lower != tuple(event.restored_to):
                why.append(
                    f"its fork point (seal {event.restored_to[0]}) is not the plan's lower "
                    f"bound (seal {lower[0]}): F = L"
                )
            if (mine["head"]["seq"], mine["head"]["hash"], mine["digest"]) != (
                sup["to_seq"],
                sup["head_hash"],
                sup["digest"],
            ):
                why.append("its superseded tail is not the one its plan exported")
            claim = _lower_claim(plan, event.hospital, store, cache)
            if claim is None:
                why.append("the fingerprints at its restore point are unreadable")
            elif claim != lower:
                why.append(
                    f"its plan's lower bound (seal {lower[0]}) is not the newest "
                    f"fingerprint's at the restore point (seal {claim[0]})"
                )
    if result is not None:
        if rid in found.unanchored:
            why.append(found.unanchored[rid])
        text = (
            corroborated(plan, result, history)
            if history is not None and plan is not None
            else ""
        )
        if text == CANNOT_SAY:
            found.uncorroborated.add(rid)
        elif text:
            why.append(text)
    event.problems += [f"whole-database restore {rid[:8]}: {text}" for text in why]
    return event


def read(store=None, segments=None, timeline=..., history=None):  # noqa: PLR0912
    """Every event under this platform's prefix (the reader key). A storage error leaves
    ``read_ok`` False: nothing is excused, and the caller reports the store unreadable.

    Also the whole-database restores' plans and results (P1-T22c). ``timeline``: the
    database's timeline (default: asked now); ``history``: the watch's own record of the
    timelines it saw (S2; None where there is none, as in ihr_audit_verify)."""
    store = store or anchor.S3Store("READ")
    found = Events()
    _read_restore_docs(store, found)
    found.timeline = current_timeline() if timeline is ... else timeline
    anchor_results(found)
    lowers = {}
    seen = set()
    restores = {}
    for key, version, stored in store.versions(prefix()):
        named = (
            KEY_TAIL_RE.match(key[len(prefix()) :])
            if key.startswith(prefix())
            else None
        )
        if key in seen:
            found.problems.append(f"event {shown(key)}: written more than once")
            if named:
                found.unsure.setdefault(named["hospital"], "written more than once")
            continue
        seen.add(key)
        if version is None:
            found.problems.append(f"event {shown(key)}: a delete marker hides it")
            if named:
                found.unsure.setdefault(named["hospital"], "a delete marker hides one")
            continue
        body = store.get(key, version)  # a storage error: the whole read is unreadable
        try:
            event = parse(key, body, stored)
        except Exception as exc:  # any bad body is one bad object (C7)
            found.problems.append(
                f"event {shown(key)}: unreadable ({type(exc).__name__}: {shown(exc, 200)})"
            )
            if named:
                found.unsure.setdefault(named["hospital"], "unreadable")
            continue
        if event.kind == "offboarding":
            found.offboarded.setdefault(event.hospital, event)
        else:
            restores.setdefault(event.hospital, []).append(event)
    # Every event is read before any is checked: a disaster event's lower bound and floor read
    # the hospital's other events (P1-T22c2a)
    for events in restores.values():
        events.sort(key=lambda e: (e.generation, e.stored))
    found.restores = restores
    for events in restores.values():
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
            if event.cause:
                check_whole(event, found, store, history, lowers)
            check_restore(event, segments)
            if event.cause:
                check_floor(event, found)
    return found


def _body(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()


def _key(kind, hospital_uuid, now):
    return (
        f"{prefix()}{now:%Y/%m/%d/%Y%m%dT%H%M%S}Z-{kind}-{hospital_uuid}-"
        f"{uuid.uuid4().hex[:8]}.json"
    )


def restore_document(
    hospital,
    generation,
    restored_to,
    superseded_rows,
    operator,
    now=None,
    restore_id=None,
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
        # A whole-database restore's (P1-T22c): fields older readers ignore
        **(
            {"cause": "whole-database", "restore_id": str(restore_id)}
            if restore_id
            else {}
        ),
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
    """Add one event object (the events key, Q-K91; never overwrites). Returns its key."""
    store = store or writer_store()
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


class WriteOnceError(ValueError):
    """A plan or result of that restore id is already in the bucket, and differs."""


def write_restore_doc(key, doc, store=None, reader=None):
    """Add a plan or result once: the same bytes again are accepted (a resumed run), anything
    else is refused (write-once; S2)."""
    store = store or writer_store()
    body = _body(doc)
    reader = reader or anchor.S3Store("READ")
    have = [(v, s) for k, v, s in reader.versions(key) if k == key]
    if have:
        if len(have) == 1 and have[0][0] and reader.get(key, have[0][0]) == body:
            return key
        msg = f"{shown(key)} is already in the bucket, with other contents"
        raise WriteOnceError(msg)
    store.put(key, body)
    return key


def plan_document(
    restore_id,
    target_name,
    target_time,
    old_timeline,
    image,
    hospitals,
    operator,
    now=None,
    flow=FLOWS[0],
    extra=None,
):
    """A whole-database restore's plan (supersede.py export): written before the destructive
    step. ``hospitals``: {uuid: entry} as _plan_hospital reads it. A disaster restore's stub:
    ``flow="disaster"``, no hospitals, ``extra`` = {"source": ..., ["no_supersede": ...]}."""
    now = now or datetime.now(UTC)
    doc = {
        **(extra or {}),
        "format": PLAN_FORMAT,
        "platform": anchor.prefix(),
        "restore_id": str(restore_id),
        "kind": "whole-database",
        "flow": flow,
        "written_at": now.isoformat(),
        "operator": operator,
        "target": {"name": target_name, "time": target_time.isoformat()},
        "old_timeline": old_timeline,
        "image": image,
        "hospitals": hospitals,
    }
    parse_restore_doc(
        plan_key(restore_id), _body(doc), now
    )  # never writes what it refuses
    return doc


def export_document(
    restore_id, old_timeline, image, hospitals, operator, source_end, now=None
):
    """A disaster restore's export doc (supersede.py export_disaster): ``hospitals`` as
    _plan_hospital reads them (outcome exported or unreadable)."""
    now = now or datetime.now(UTC)
    doc = {
        "format": EXPORT_FORMAT,
        "platform": anchor.prefix(),
        "restore_id": str(restore_id),
        "plan": plan_key(restore_id),
        "written_at": now.isoformat(),
        "operator": operator,
        "old_timeline": old_timeline,
        "image": image,
        "source": "repository",
        "source_end": source_end,
        "hospitals": hospitals,
    }
    parse_restore_doc(export_key(restore_id), _body(doc), now)
    return doc


def result_document(
    restore_id, old_timeline, new_timeline, hospitals, operator, now=None, closed=None
):
    """A whole-database restore's result (supersede.py load): after every hospital.
    ``closed``: {reason, how} when it was closed with nothing loaded (supersede.close)."""
    now = now or datetime.now(UTC)
    doc = {
        **({"closed": closed} if closed else {}),
        "format": RESULT_FORMAT,
        "platform": anchor.prefix(),
        "restore_id": str(restore_id),
        "written_at": now.isoformat(),
        "operator": operator,
        "plan": plan_key(restore_id),
        "old_timeline": old_timeline,
        "new_timeline": new_timeline,
        "hospitals": hospitals,
    }
    parse_restore_doc(result_key(restore_id), _body(doc), now)
    return doc
