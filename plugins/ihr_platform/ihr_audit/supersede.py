"""Whole-database restores and the audit chain (P1-T22c1; docs/reviews/p1-t22c/design.md, its
"After the attack" section wins over §1-§6).

``staging.sh rollback --restore`` puts every hospital schema and ``public`` back to the window's
restore point T. The fingerprint bucket is not rolled back: its claims for seals sealed after T
would read as a cut or a rewrite for good. So, while the old database is still alive (flow a):

1. **export** (``ihr_audit_supersede export``, the audit watch's read-only role, the reader key):
   per hospital its lower bound L = the claim of the newest fingerprint the storage received at
   or before T (the release's own fingerprint of the sealed head; S3), and a **package** of its
   tail after L: the seals L..head, their entries, any entry not sealed yet (kept, never
   loaded), and every restore-archive generation whose event the bucket received after T (those
   are rolled back too). One tar on standard output: ``<uuid>.jsonl.gz`` per hospital and
   ``plan.json``, the plan's draft. Nothing is written anywhere else.
2. **plan** (``ihr_audit_supersede plan``, the owner's one-off, the events key): the tar read
   back, each package's sha256 checked against the draft and L read again from the bucket; then
   the plan written, write-once, before the destructive step.
3. the database restored to T (``restore_live``), then **load** (``ihr_audit_supersede load``,
   the owner's one-off, the events key; before ``up``): per hospital, in its own transaction,
   each step checked before it is done (a crashed load is run again):
   the package's sha256 is the plan's; the archive generations re-loaded (one already there is
   compared row by row, never written again); the restored head must be L exactly (F = L,
   S3); a hospital with nothing after L is ``unchanged``; else generation g = the bucket's
   restore events of it + 1 (the database must agree), the tail archived as g (``operation`` =
   the restore id, ``parked_schema`` = ``whole-database``), checked as the watch checks it,
   and an ordinary restore event written with ``cause`` and ``restore_id``; and one ``restore``
   entry in the hospital's trail. When every hospital is done, the **result** (new timeline,
   outcome, generation, event key and fork point per hospital), then the whole check of every
   event of this restore (events.check_whole).

Raw SQL only for the database (no ORM model of ours is read or written): the load runs from the
leaving build against the target build's schema, which must have the restore archive (migration
ihr_platform 0012, P1-T22b; Q-K86: else refused). Packages hold personal data (audit entries):
on the host they live in ``deploy/staging/supersede/<id>/`` (mode 600) beside the copy of the
whole database the rollback keeps; their encrypted copy in the dumps bucket is P1-T22c2.

Lines (journal, log store):
    ihr-supersede: hospital=<slug> outcome=<exported|absent|unchanged|loaded|refused> [step=<s>]
    ihr-supersede: restore=<id8> outcome=<planned|loaded|incomplete>
"""

import gzip
import hashlib
import io
import json
import os
import tarfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from django.db import connection, transaction
from psycopg import sql

from ihr_audit import anchor, archive, events, oneline, seal

PACKAGE_FORMAT = "ihr-superseded/1"
PARKED = (
    "whole-database"  # ihr_restore_record.parked_schema of a whole-database restore
)
BATCH = 500
PLAN_MEMBER = "plan.json"
# pg_advisory_lock key of the load (one at a time, any database of this cluster): a constant
# (int8 of sha256("ihr_audit_supersede.load")[:8])
LOAD_LOCK = int.from_bytes(
    hashlib.sha256(b"ihr_audit_supersede.load").digest()[:8], "big", signed=True
)


class Refused(Exception):  # noqa: N818 (a refusal, not an error)
    pass


def log_line(slug, outcome, step=""):
    slug = slug if anchor.SLUG_RE.match(slug or "") else "invalid-slug"
    return f"ihr-supersede: hospital={slug} outcome={outcome}" + (
        f" step={step}" if step else ""
    )


def run_line(restore_id, outcome):
    return f"ihr-supersede: restore={str(restore_id)[:8]} outcome={outcome}"


def _emit(line):
    os.write(2, f"{oneline.one_line(line)}\n".encode())


# --- what is exported, in the same text for the live chain and the archive -------------------


def _utc(column):
    return f"to_char({column} AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')"


SEAL_JSON = (
    "json_build_object('seq', seq, 'lower', lower_txid::text, 'upper', upper_txid::text, "
    "'entries', entries, 'entries_hash', encode(entries_hash, 'hex'), "
    "'prev_hash', encode(prev_hash, 'hex'), 'hash', encode(hash, 'hex'), "
    f"'sealed_at', {_utc('sealed_at')}, 'version', version)::text"
)
ENTRY_JSON = (
    "json_build_object('id', id, 'txid', txid::text, "
    f"'at', {_utc('at')}, 'kind', kind, 'actor', actor, 'context', context, 'tbl', tbl, "
    "'op', op, 'row_pk', row_pk, 'patients', patients, 'data', data, "
    "'salt', encode(salt, 'hex'))::text"
)
RECORD_JSON = (
    "json_build_object('generation', generation, 'operation', operation::text, 'slug', slug, "
    "'restored_seq', restored_seq, 'restored_hash', encode(restored_hash, 'hex'), "
    "'from_seq', from_seq, 'to_seq', to_seq, 'head_hash', encode(head_hash, 'hex'), "
    "'digest', digest, 'parked_schema', parked_schema, 'dump', dump, 'operator', operator, "
    f"'recorded_at', {_utc('recorded_at')})::text"
)
_INSERT_SEALS = (
    "INSERT INTO public.ihr_restore_superseded_seal (hospital, generation, seq, lower_txid, "
    "upper_txid, entries, entries_hash, prev_hash, hash, sealed_at, version) "
    "SELECT %s, %s, (j->>'seq')::bigint, (j->>'lower')::xid8, (j->>'upper')::xid8, "
    "(j->>'entries')::bigint, decode(j->>'entries_hash', 'hex'), decode(j->>'prev_hash', 'hex'), "
    "decode(j->>'hash', 'hex'), (j->>'sealed_at')::timestamptz, (j->>'version')::smallint "
    "FROM jsonb_array_elements(%s::jsonb) j"
)
_INSERT_ENTRIES = (
    "INSERT INTO public.ihr_restore_superseded_entry (hospital, generation, id, txid, at, kind, "
    "actor, context, tbl, op, row_pk, patients, data, salt) "
    "SELECT %s, %s, (j->>'id')::bigint, (j->>'txid')::xid8, (j->>'at')::timestamptz, "
    "j->>'kind', j->>'actor', j->'context', j->>'tbl', j->>'op', j->>'row_pk', "
    "ARRAY(SELECT jsonb_array_elements_text(j->'patients'))::bigint[], j->'data', "
    "decode(j->>'salt', 'hex') FROM jsonb_array_elements(%s::jsonb) j"
)


def _rows(cursor, query, params):
    cursor.execute(query, params)
    return [json.loads(row[0]) for row in cursor.fetchall()]


def _seal_row(doc):
    return events.SealRow(
        doc["seq"],
        doc["lower"],
        doc["upper"],
        doc["entries"],
        bytes.fromhex(doc["entries_hash"]),
        bytes.fromhex(doc["prev_hash"]),
        bytes.fromhex(doc["hash"]),
        doc["version"],
    )


def live_seals(cursor, schema, low, high):
    return _rows(
        cursor,
        sql.SQL(
            "SELECT " + SEAL_JSON + " FROM {} WHERE seq BETWEEN %s AND %s ORDER BY seq"  # noqa: S608 (our own constant columns; values are parameters)
        ).format(sql.Identifier(schema, "ihr_audit_seal")),
        [low, high],
    )


def live_entries(cursor, schema, lower, upper=None):
    """Entries with lower <= txid (< upper): a seal range's, or (upper None) all after it."""
    return _rows(
        cursor,
        sql.SQL(
            "SELECT " + ENTRY_JSON + " FROM {} WHERE txid >= %s::xid8 "  # noqa: S608 (our own constant columns; values are parameters)
            "AND (%s::xid8 IS NULL OR txid < %s::xid8) ORDER BY txid, id"
        ).format(sql.Identifier(schema, "ihr_audit_entry")),
        [lower, upper, upper],
    )


def head(cursor, schema):
    """(seq, hash hex, upper) of the schema's newest seal; (0, genesis, "0") without one."""
    cursor.execute(
        sql.SQL(
            "SELECT seq, encode(hash, 'hex'), upper_txid::text FROM {} "
            "ORDER BY seq DESC LIMIT 1"
        ).format(sql.Identifier(schema, "ihr_audit_seal"))
    )
    row = cursor.fetchone()
    return tuple(row) if row else (0, seal.GENESIS.hex(), "0")


def archived(cursor, hospital_uuid, generation):
    """One archive generation, as the package carries it: (record, seals, entries)."""
    params = [str(hospital_uuid), generation]
    records = _rows(
        cursor,
        "SELECT " + RECORD_JSON + " FROM public.ihr_restore_record "  # noqa: S608 (our own constant columns; values are parameters)
        "WHERE hospital = %s AND generation = %s",
        params,
    )
    seals = _rows(
        cursor,
        "SELECT " + SEAL_JSON + " FROM public.ihr_restore_superseded_seal "  # noqa: S608 (our own constant columns; values are parameters)
        "WHERE hospital = %s AND generation = %s ORDER BY seq",
        params,
    )
    entries = _rows(
        cursor,
        "SELECT " + ENTRY_JSON + " FROM public.ihr_restore_superseded_entry "  # noqa: S608 (our own constant columns; values are parameters)
        "WHERE hospital = %s AND generation = %s ORDER BY txid, id",
        params,
    )
    return (records[0] if records else None), seals, entries


# --- packages ----------------------------------------------------------------------------------


@dataclass
class Package:
    header: dict
    seals: list = field(default_factory=list)  # seals L..head (L itself when L > 0)
    entries: list = field(default_factory=list)  # entries of seals L+1..head
    unsealed: list = field(
        default_factory=list
    )  # entries after head (kept, never loaded)
    generations: dict = field(
        default_factory=dict
    )  # g -> {"record", "seals", "entries"}

    def tail(self):
        low = self.header["lower"]["seq"]
        return [s for s in self.seals if s["seq"] > low]

    def to_bytes(self):
        lines = [json.dumps(self.header, sort_keys=True)]
        lines += [json.dumps({"seal": s}, sort_keys=True) for s in self.seals]
        lines += [json.dumps({"entry": e}, sort_keys=True) for e in self.entries]
        lines += [json.dumps({"unsealed": e}, sort_keys=True) for e in self.unsealed]
        for g, part in sorted(self.generations.items()):
            lines.append(
                json.dumps({"generation": g, "record": part["record"]}, sort_keys=True)
            )
            lines += [
                json.dumps({"generation": g, "archived_seal": s}, sort_keys=True)
                for s in part["seals"]
            ]
            lines += [
                json.dumps({"generation": g, "archived_entry": e}, sort_keys=True)
                for e in part["entries"]
            ]
        raw = ("\n".join(lines) + "\n").encode()
        out = io.BytesIO()
        with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
            gz.write(raw)
        return out.getvalue()

    @classmethod
    def from_bytes(cls, body):
        lines = gzip.decompress(body).decode().splitlines()
        header = json.loads(lines[0])
        if header.get("format") != PACKAGE_FORMAT:
            msg = "not a superseded-tail package"
            raise ValueError(msg)
        pkg = cls(header)
        for line in lines[1:]:
            doc = json.loads(line)
            if "seal" in doc:
                pkg.seals.append(doc["seal"])
            elif "entry" in doc:
                pkg.entries.append(doc["entry"])
            elif "unsealed" in doc:
                pkg.unsealed.append(doc["unsealed"])
            else:
                part = pkg.generations.setdefault(
                    int(doc["generation"]), {"record": None, "seals": [], "entries": []}
                )
                if "record" in doc:
                    part["record"] = doc["record"]
                elif "archived_seal" in doc:
                    part["seals"].append(doc["archived_seal"])
                else:
                    part["entries"].append(doc["archived_entry"])
        return pkg


def sha256(body):
    return hashlib.sha256(body).hexdigest()


# --- export (the old database, read-only) -------------------------------------------------------


def _hospitals(cursor):
    """{uuid: (slug, schema)} of every hospital with an audit trail, provisioning ones aside
    (as the fingerprints: anchor.snapshot)."""
    cursor.execute(
        "SELECT uuid::text, slug, schema_name FROM public.ihr_platform_hospital "
        "WHERE schema_name <> 'public' AND status <> 'provisioning' ORDER BY slug"
    )
    found = {}
    for hospital_uuid, slug, schema in cursor.fetchall():
        if anchor._schema_has_trail(cursor, schema):  # noqa: SLF001 (the anchors' own test)
            found[hospital_uuid] = (slug, schema)
    return found


def _events_before(found, hospital_uuid, when):
    return sum(1 for e in found.restores.get(hospital_uuid, []) if e.stored <= when)


def export_one(
    cursor, hospital_uuid, slug, schema, claim, carried, restore_id, target_time=None
):
    """One hospital's package and plan entry. ``claim``: (seq, hash, fingerprint key) of the
    newest fingerprint at or before T, or None (no fingerprint at T names it: it was made
    after T, ``absent``). ``carried``: the archive generations to carry. ``target_time``
    (T): the first seal after L must be sealed after it, else the fingerprint is stale (the
    release's own seal and fingerprint did not run: review E3) and the load would refuse
    the hospital only after the database is replaced."""
    top = head(cursor, schema)
    if claim is None:
        lower, outcome, fp_key = (0, seal.GENESIS.hex()), "absent", ""
    else:
        lower, outcome, fp_key = (claim[0], claim[1]), "exported", claim[2]
    if lower[0] > top[0]:
        msg = (
            f"its chain ends at seal {top[0]}, before seal {lower[0]} the fingerprint at the "
            "restore point names (cut?)"
        )
        raise Refused(msg)
    seals = live_seals(cursor, schema, max(lower[0], 1), top[0])
    if lower[0] and (
        not seals or seals[0]["seq"] != lower[0] or seals[0]["hash"] != lower[1]
    ):
        msg = (
            f"its seal {lower[0]} is not the one the fingerprint at the restore point names: "
            "its chain parted from it after the restore point (a single-hospital restore from "
            "an older dump?); this rollback cannot carry it (F = L, S3)"
        )
        raise Refused(msg)
    pkg = Package(
        {
            "format": PACKAGE_FORMAT,
            "hospital": hospital_uuid,
            "slug": slug,
            "restore_id": str(restore_id),
            "lower": {"seq": lower[0], "hash": lower[1]},
            "head": {"seq": top[0], "hash": top[1]},
        },
        seals,
    )
    tail = pkg.tail()
    # Only for a hospital a fingerprint names: one no fingerprint names (absent) may have been
    # provisioning at T (anchor.snapshot leaves those out); the load counts it the same way
    if (
        claim is not None
        and tail
        and target_time is not None
        and datetime.fromisoformat(tail[0]["sealed_at"]) <= target_time
    ):
        msg = (
            f"its seal {tail[0]['seq']} was sealed at {tail[0]['sealed_at']}, before the "
            f"restore point, but the fingerprint at the restore point names seal {lower[0]}: "
            "that fingerprint is stale (did the release's seal run?); the restored chain "
            "would not end at L (F = L, S3); nothing changed"
        )
        raise Refused(msg)
    if tail:
        pkg.entries = live_entries(cursor, schema, tail[0]["lower"], tail[-1]["upper"])
    pkg.unsealed = live_entries(cursor, schema, top[2])
    for g in carried:
        record, a_seals, a_entries = archived(cursor, hospital_uuid, g)
        if record is None:
            msg = f"restore generation {g} is in the bucket but not in the archive"
            raise Refused(msg)
        pkg.generations[g] = {"record": record, "seals": a_seals, "entries": a_entries}
    pkg.header.update(
        seals=len(tail),
        entries=len(pkg.entries),
        unsealed=len(pkg.unsealed),
        generations=sorted(carried),
    )
    body = pkg.to_bytes()
    rows = [_seal_row(s) for s in tail]
    entry = {
        "slug": slug,
        "outcome": outcome,
        "lower": {"seq": lower[0], "hash": lower[1], "fingerprint": fp_key},
        "head": {"seq": top[0], "hash": top[1]},
        "digest": events.segment_digest(rows) if rows else "",
        "entries": len(pkg.entries),
        "unsealed": len(pkg.unsealed),
        "generations": sorted(carried),
        "package_sha256": sha256(body),
    }
    return body, entry


def export(out, restore_id, target_name, target_time, image, operator, store=None):
    """Every hospital's package and the plan's draft, as one tar on ``out`` (binary). Returns
    the draft. Reads only (the watch's role in a staging one-off)."""
    store = store or anchor.S3Store("READ")
    found = events.read(store)
    if not found.read_ok:
        msg = "the fingerprint bucket's events could not be read"
        raise Refused(msg)
    timeline = events.current_timeline()
    if timeline is None:
        msg = "the database's timeline could not be read"
        raise Refused(msg)
    fp_key, claims = anchor.claims_at(store, target_time)
    hospitals, packages = {}, {}
    outer = not connection.in_atomic_block
    with transaction.atomic(), connection.cursor() as cursor:
        if outer:  # one snapshot of every hospital
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        for hospital_uuid, (slug, schema) in _hospitals(cursor).items():
            claim = claims.get(hospital_uuid)
            before = _events_before(found, hospital_uuid, target_time)
            in_db = archive.generations_in_db(cursor, hospital_uuid)
            carried = list(range(before + 1, in_db + 1))
            try:
                if in_db < before:
                    # The bucket already has restore events of it the database does not
                    # record (an older whole-database restore, before P1-T22c): the load
                    # could never make them agree; refused now, before anything changes
                    msg = (
                        f"the bucket has {before} restore events of it before the point and "
                        f"the database only {in_db} restore records: they must agree"
                    )
                    raise Refused(msg)
                body, entry = export_one(
                    cursor,
                    hospital_uuid,
                    slug,
                    schema,
                    claim and (claim[0], claim[1], fp_key),
                    carried,
                    restore_id,
                    target_time,
                )
            except Refused as exc:
                _emit(log_line(slug, "refused", "export"))
                msg = f"{slug}: {exc}"
                raise Refused(msg) from None
            hospitals[hospital_uuid], packages[hospital_uuid] = entry, body
            _emit(log_line(slug, entry["outcome"]))
    draft = events.plan_document(
        restore_id,
        target_name,
        target_time,
        timeline,
        image,
        hospitals,
        operator,
    )
    with tarfile.open(fileobj=out, mode="w|") as tar:
        for hospital_uuid, body in sorted(packages.items()):
            _add(tar, f"{hospital_uuid}.jsonl.gz", body)
        _add(tar, PLAN_MEMBER, json.dumps(draft, sort_keys=True, indent=1).encode())
    return draft


def _add(tar, name, body):
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = len(body), 0o600, 0
    tar.addfile(info, io.BytesIO(body))


def read_tar(stream):
    """{member name: bytes} of an export's tar (names checked: nothing but ours)."""
    found = {}
    with tarfile.open(fileobj=stream, mode="r|") as tar:
        for member in tar:
            name = member.name
            ok = name == PLAN_MEMBER or (
                name.endswith(".jsonl.gz")
                and anchor.UUID_RE.match(name[: -len(".jsonl.gz")])
            )
            if not member.isfile() or not ok or name in found:
                msg = f"the export holds an object that is not ours: {name[:80]!r}"
                raise Refused(msg)
            found[name] = tar.extractfile(member).read()
    return found


# --- the plan (the owner's one-off, before the destructive step) ------------------------------


def write_plan(members, restore_id, store=None, reader=None):
    """The draft checked (every package's sha256, L read again from the bucket) and written
    once. Returns its key."""
    reader = reader or anchor.S3Store("READ")
    try:
        draft = json.loads(members[PLAN_MEMBER])
    except (KeyError, ValueError):
        msg = "the export has no plan"
        raise Refused(msg) from None
    if draft.get("restore_id") != str(restore_id):
        msg = "the export is of another restore"
        raise Refused(msg)
    plan = events.parse_restore_doc(
        events.plan_key(restore_id),
        events._body(draft),  # noqa: SLF001 (the events' own canonical form)
        datetime.fromisoformat(draft["written_at"]),
    )
    _key, claims = anchor.claims_at(reader, plan.target_time)
    for hospital_uuid, entry in plan.hospitals.items():
        body = members.get(f"{hospital_uuid}.jsonl.gz")
        if body is None or sha256(body) != entry["package_sha256"]:
            msg = f"{entry['slug']}: its package is missing or not the one the export made"
            raise Refused(msg)
        claim = claims.get(hospital_uuid)
        want = (claim[0], claim[1]) if claim else None
        have = (entry["lower"]["seq"], entry["lower"]["hash"])
        if (want is None) != (entry["outcome"] == "absent") or (
            want is not None and want != have
        ):
            msg = f"{entry['slug']}: its lower bound is not the fingerprint's at the restore point"
            raise Refused(msg)
    # A resumed run: the plan already stored is accepted when it is this draft (its own
    # time aside); any other plan of this id is refused (write-once)
    key = events.plan_key(restore_id)
    have = [(v, s) for k, v, s in reader.versions(key) if k == key]
    if have:
        stored = json.loads(reader.get(key, have[-1][0])) if have[-1][0] else {}
        same = len(have) == 1 and {
            k: v for k, v in stored.items() if k != "written_at"
        } == {k: v for k, v in draft.items() if k != "written_at"}
        if not same:
            msg = f"another plan of restore {restore_id} is already in the bucket"
            raise Refused(msg)
        _emit(run_line(restore_id, "planned"))
        return key
    # Written as the export drafted it, except its own time (the plan is stored now)
    draft["written_at"] = datetime.now(UTC).isoformat()
    key = events.write_restore_doc(key, draft, store=store, reader=reader)
    _emit(run_line(restore_id, "planned"))
    return key


# --- the load (the restored database, the owner, before up) -------------------------------------


def _insert_json(cursor, query, hospital_uuid, generation, docs):
    for i in range(0, len(docs), BATCH):
        cursor.execute(
            query, [str(hospital_uuid), generation, json.dumps(docs[i : i + BATCH])]
        )


def _carry_generation(cursor, hospital_uuid, g, part):
    """Step 2: an archive generation the restore removed, re-loaded as it was; one already
    here is compared row by row and never written again."""
    record, seals, entries = archived(cursor, hospital_uuid, g)
    if record is not None:
        if (record, seals, entries) != (part["record"], part["seals"], part["entries"]):
            msg = f"restore generation {g} is here, but not as it was exported"
            raise Refused(msg)
        return False
    r = part["record"]
    cursor.execute(
        "INSERT INTO public.ihr_restore_record (hospital, generation, operation, slug, "
        "restored_seq, restored_hash, from_seq, to_seq, head_hash, digest, parked_schema, dump, "
        "operator, recorded_at) VALUES (%s, %s, %s, %s, %s, decode(%s, 'hex'), %s, %s, "
        "decode(%s, 'hex'), %s, %s, %s, %s, %s::timestamptz)",
        [
            str(hospital_uuid),
            g,
            r["operation"],
            r["slug"],
            r["restored_seq"],
            r["restored_hash"],
            r["from_seq"],
            r["to_seq"],
            r["head_hash"],
            r["digest"],
            r["parked_schema"],
            r["dump"],
            r["operator"],
            r["recorded_at"],
        ],
    )
    _insert_json(cursor, _INSERT_SEALS, hospital_uuid, g, part["seals"])
    _insert_json(cursor, _INSERT_ENTRIES, hospital_uuid, g, part["entries"])
    return True


@dataclass
class Outcome:
    slug: str
    outcome: str
    generation: int = 0
    event: str = ""
    fork: int = 0
    why: str = ""

    def entry(self):
        return {
            "slug": self.slug,
            "outcome": self.outcome,
            "generation": self.generation,
            "event": self.event,
            "fork": self.fork,
        }


def _probe(hospital_uuid, slug, generation, lower, rows, operator):
    """The archived segment checked exactly as the watch and ihr_audit_verify will."""
    now = datetime.now(UTC)
    probe = events.Event(
        "", "restore", hospital_uuid, slug, now, now, operator, generation, lower
    )
    probe.superseded = {
        "from_seq": rows[0].seq,
        "to_seq": rows[-1].seq,
        "head_hash": bytes(rows[-1].hash).hex(),
        "digest": events.segment_digest(rows),
    }
    events.check_restore(probe)
    if probe.problems:
        msg = "the archived tail does not verify: " + "; ".join(probe.problems)[:300]
        raise Refused(msg)


def restored_fork_problem(cursor, schema, lower, tail):
    """Why the restored chain did not end at ``lower`` (F = L), or "": its seal L is the plan's
    and its next seal, if any, is not the package's next one (that one was sealed before T)."""
    seq, digest = lower
    restored = head(cursor, schema)
    at_lower = live_seals(cursor, schema, seq, seq) if seq else []
    if restored[0] < seq or (seq and (not at_lower or at_lower[0]["hash"] != digest)):
        return (
            f"the restored chain ends at seal {restored[0]}, not at the plan's lower bound "
            f"(seal {seq}): F = L"
        )
    nxt = live_seals(cursor, schema, seq + 1, seq + 1)
    if nxt and tail and nxt[0]["hash"] == tail[0]["hash"]:
        return (
            f"the restored chain goes on past the plan's lower bound (seal {seq}) with the "
            "package's own next seal, sealed before the restore point: F = L"
        )
    return ""


class _Hospital:
    def __init__(self, hospital_uuid, slug):
        self.uuid, self.slug = hospital_uuid, slug


def load_one(  # noqa: PLR0912 (one check per step)
    hospital_uuid, entry, schema, body, plan, found, operator, store
):
    """One hospital of the plan (steps 1-7). Returns an Outcome."""
    slug = entry["slug"]
    if sha256(body or b"") != entry["package_sha256"]:
        msg = "its package is missing or not the one the plan names"
        raise Refused(msg)
    pkg = Package.from_bytes(body)
    lower = (entry["lower"]["seq"], entry["lower"]["hash"])
    if (pkg.header["lower"]["seq"], pkg.header["lower"]["hash"]) != lower:
        msg = "its package does not start at the plan's lower bound"
        raise Refused(msg)
    tail = pkg.tail()
    rows = [_seal_row(s) for s in tail]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '10s'")
        for g, part in sorted(pkg.generations.items()):
            _carry_generation(cursor, hospital_uuid, g, part)
        # Step 3, F = L (S3): the restored chain ended exactly at the plan's lower bound, the
        # release's fingerprint of the sealed head. Never a lower fork point. Read at L, not
        # at the head: a load run again after `up` finds seals the restored database added
        # since (its own restore entry sealed, new traffic; review T2/E2). Those start a new
        # branch after L; a seal after L that is the package's own was in the database at T
        # (a stale L): refused
        fork_problem = restored_fork_problem(cursor, schema, lower, tail)
        if fork_problem:
            raise Refused(fork_problem)
        if not rows:
            return Outcome(slug, "unchanged", fork=lower[0])
        if (
            rows[0].seq != lower[0] + 1
            or bytes(rows[0].prev_hash).hex() != lower[1]
            or (rows[-1].seq, bytes(rows[-1].hash).hex(), events.segment_digest(rows))
            != (entry["head"]["seq"], entry["head"]["hash"], entry["digest"])
        ):
            msg = "its package's tail is not the one the plan names"
            raise Refused(msg)
        generation = archive.generation_of(cursor, hospital_uuid, plan.restore_id)
        if generation is None:
            unsure = found.generation_unsure(hospital_uuid)
            if unsure:
                raise Refused(unsure)
            generation = found.generation(hospital_uuid) + 1
            if archive.generations_in_db(cursor, hospital_uuid) != generation - 1:
                msg = (
                    f"the bucket has {generation - 1} restore events of it and the database "
                    f"{archive.generations_in_db(cursor, hospital_uuid)} restore records: they "
                    "must agree"
                )
                raise Refused(msg)
            cursor.execute(
                "INSERT INTO public.ihr_restore_record (hospital, generation, operation, slug, "
                "restored_seq, restored_hash, from_seq, to_seq, head_hash, digest, "
                "parked_schema, dump, operator) VALUES (%s, %s, %s, %s, %s, decode(%s, 'hex'), "
                "%s, %s, decode(%s, 'hex'), %s, %s, %s, %s)",
                [
                    hospital_uuid,
                    generation,
                    plan.restore_id,
                    slug,
                    lower[0],
                    lower[1],
                    rows[0].seq,
                    rows[-1].seq,
                    entry["head"]["hash"],
                    entry["digest"],
                    PARKED,
                    f"{plan.target_name} {plan.target_time.isoformat()}"[:200],
                    operator[:64],
                ],
            )
            _insert_json(cursor, _INSERT_SEALS, hospital_uuid, generation, tail)
            _insert_json(
                cursor, _INSERT_ENTRIES, hospital_uuid, generation, pkg.entries
            )
        _probe(hospital_uuid, slug, generation, lower, rows, operator)
    # Step 6: the event, once (a generation already in the bucket is never written again)
    key = ""
    for event in found.restores.get(hospital_uuid, []):
        if event.generation == generation:
            if event.superseded["digest"] != entry["digest"]:
                msg = f"restore event {generation} in the bucket is of another tail"
                raise Refused(msg)
            key = event.key
    if not key:
        key = events.write(
            events.restore_document(
                _Hospital(hospital_uuid, slug),
                generation,
                lower,
                rows,
                operator,
                restore_id=plan.restore_id,
            ),
            store,
        )
    # Step 7: one restore entry in the hospital's trail (beat seals it, the hourly job
    # fingerprints it)
    with transaction.atomic(), connection.cursor() as cursor:
        trail = sql.Identifier(schema, "ihr_audit_entry")
        cursor.execute(
            sql.SQL(
                "SELECT count(*) FROM {} WHERE kind = 'restore' AND data->>'restore_id' = %s"
            ).format(trail),
            [plan.restore_id],
        )
        if not cursor.fetchone()[0]:
            cursor.execute(
                sql.SQL(
                    "INSERT INTO {} (kind, actor, context, tbl, op, row_pk, patients, data) "
                    "VALUES ('restore', %s, %s::jsonb, '', '', NULL, '{{}}', %s::jsonb)"
                ).format(trail),
                [
                    f"restore:{operator}",
                    json.dumps({"via": "whole-database restore"}),
                    json.dumps(
                        {
                            "restore_id": plan.restore_id,
                            "generation": generation,
                            "restored_to": lower[0],
                            "superseded": [rows[0].seq, rows[-1].seq],
                            "target": plan.target_name,
                            "target_time": plan.target_time.isoformat(),
                            "event": key,
                            "reason": "whole-database restore (staging.sh rollback --restore)",
                        }
                    ),
                ],
            )
    return Outcome(slug, "loaded", generation, key, lower[0])


def _restored_hospitals(cursor):
    """The restored database's hospitals, as the export counts them (_hospitals: provisioning
    ones and schemas without a trail aside; review E4)."""
    return _hospitals(cursor)


def load(members, restore_id, operator, store=None, reader=None):
    """Steps 1-8 for every hospital of the plan. Returns {uuid: Outcome}; raises Refused
    (after every hospital was tried) when any was refused: then no result is written, and
    ``load`` runs again after the cause is fixed."""
    reader = reader or anchor.S3Store("READ")
    store = store or events.writer_store()
    # One load at a time (review E6): two at once would both read the bucket before either
    # wrote, and write two events of one generation. The bucket is read after the lock
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOAD_LOCK])
        if not cursor.fetchone()[0]:
            msg = "another load of a whole-database restore is running: wait for it"
            raise Refused(msg)
    try:
        return _load(members, restore_id, operator, store, reader)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [LOAD_LOCK])


def _load(members, restore_id, operator, store, reader):  # noqa: PLR0912, PLR0915
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.ihr_restore_record') IS NOT NULL")
        if not cursor.fetchone()[0]:
            msg = (
                "the restored database has no restore archive (a build before P1-T22b): its "
                "events could not be checked; refused (Q-K86, fix forward)"
            )
            raise Refused(msg)
        hospitals = _restored_hospitals(cursor)
    found = events.read(reader)
    if not found.read_ok:
        msg = "the fingerprint bucket's events could not be read"
        raise Refused(msg)
    plan = found.plans.get(str(restore_id))
    if plan is None:
        msg = f"no plan of restore {restore_id} in the bucket"
        raise Refused(msg)
    if found.timeline is None or found.timeline == plan.old_timeline:
        msg = (
            f"the database is still on the plan's timeline {plan.old_timeline}: it was not "
            "restored (load runs after restore_live)"
        )
        raise Refused(msg)
    done = found.results.get(str(restore_id))
    outcomes, refused = {}, []
    for hospital_uuid, entry in sorted(
        plan.hospitals.items(), key=lambda item: item[1]["slug"]
    ):
        slug = entry["slug"]
        there = hospitals.get(hospital_uuid)
        try:
            if entry["outcome"] == "absent":
                if there is not None:
                    msg = "it is in the restored database, but no fingerprint at T named it"
                    raise Refused(msg)
                outcomes[hospital_uuid] = Outcome(slug, "absent")
            elif there is None:
                msg = "it is not in the restored database"
                raise Refused(msg)
            elif there[0] != slug:
                msg = f"the restored database calls it {there[0]}"
                raise Refused(msg)
            else:
                outcomes[hospital_uuid] = load_one(
                    hospital_uuid,
                    entry,
                    there[1],
                    members.get(f"{hospital_uuid}.jsonl.gz"),
                    plan,
                    found,
                    operator,
                    store,
                )
        except Refused as exc:
            outcomes[hospital_uuid] = Outcome(slug, "refused", why=str(exc))
            refused.append(f"{slug}: {exc}")
        _emit(log_line(slug, outcomes[hospital_uuid].outcome, "load"))
    if refused:
        _emit(run_line(restore_id, "incomplete"))
        msg = "; ".join(refused)
        raise Refused(msg)
    doc = events.result_document(
        restore_id,
        plan.old_timeline,
        found.timeline,
        {u: o.entry() for u, o in outcomes.items()},
        operator,
    )
    if done is None:
        events.write_restore_doc(
            events.result_key(restore_id), doc, store=store, reader=reader
        )
    # The whole check of every event of this restore, as this build's watch will do it
    after = events.read(reader)
    bad = [
        f"{e.slug}: {e.problems[0]}"
        for evs in after.restores.values()
        for e in evs
        if e.restore_id == str(restore_id) and e.problems
    ]
    if bad:
        _emit(run_line(restore_id, "incomplete"))
        msg = "the written events do not verify: " + "; ".join(bad)[:600]
        raise Refused(msg)
    _emit(run_line(restore_id, "loaded"))
    return outcomes


def status(restore_id, reader=None):
    """What the bucket says of a whole-database restore: {"plan", "result", "hospitals"}."""
    found = events.read(reader or anchor.S3Store("READ"))
    rid = str(restore_id)
    plan, result = found.plans.get(rid), found.results.get(rid)
    report = {
        "restore_id": rid,
        "plan": plan and plan.key,
        "result": result and result.key,
        "anchored": rid in found.anchored,
        "why_not": found.unanchored.get(rid, ""),
        "target": plan and f"{plan.target_name} {plan.target_time.isoformat()}",
        "timelines": plan and [plan.old_timeline, result and result.new_timeline],
        "hospitals": {},
    }
    for hospital_uuid, entry in sorted(
        (plan.hospitals if plan else {}).items(), key=lambda i: i[1]["slug"]
    ):
        got = (result.hospitals if result else {}).get(hospital_uuid, {})
        event = next(
            (e for e in found.restores.get(hospital_uuid, []) if e.restore_id == rid),
            None,
        )
        report["hospitals"][entry["slug"]] = {
            "planned": entry["outcome"],
            "lower": entry["lower"]["seq"],
            "head": entry["head"]["seq"],
            "outcome": got.get("outcome", "pending"),
            "generation": got.get("generation", 0),
            "event": got.get("event", ""),
            "event_problems": event.problems if event else [],
        }
    return report


def new_id():
    return str(uuid.uuid4())
