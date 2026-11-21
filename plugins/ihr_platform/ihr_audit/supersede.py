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

**Disaster restores (P1-T22c2a, flow b; docs/reviews/p1-t22c/design-c2.md, "c2a as
decided").** ``staging.sh restore --target T`` writes a **stub** plan (``write_stub``: no
database, the events key only) just before the restore; the hospitals are served first; then
``export_disaster`` reads every hospital's tail from a copy restored from the backup repository
(the audit watch's role; T, the old timeline and the backup set come from the stub, never from
the copy), ``write_export`` writes the **export doc** (the events key only), and ``load`` takes
a disaster plan's hospitals from it: F = the restored head (the highest seal the restored
chain shares with the package), bound from below by entry times (``events.fork_floor_problem``,
audit S2). ``close`` writes the result of a restore that cannot be loaded (every hospital not
loaded ``refused``; the cuts stay). No export runs while another restore's load is pending.

Lines (journal, log store):
    ihr-supersede: hospital=<slug> outcome=<exported|absent|unchanged|loaded|refused|unreadable>
                   [step=<s>]
    ihr-supersede: restore=<id8> outcome=<planned|stub|exported|loaded|incomplete|closed>
"""

import gzip
import hashlib
import io
import json
import os
import tarfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from django.db import DatabaseError, connection, transaction
from psycopg import sql

from ihr_audit import anchor, archive, events, oneline, seal

PACKAGE_FORMAT = "ihr-superseded/1"
PARKED = (
    "whole-database"  # ihr_restore_record.parked_schema of a whole-database restore
)
BATCH = 500
PLAN_MEMBER = "plan.json"
EXPORT_MEMBER = "export.json"  # a disaster restore's export doc draft (P1-T22c2a)
# The objects this build writes and reads (P1-T22c2a, Q-K101): 1 = P1-T22c1 (plans, results,
# events of a planned rollback), 2 = disaster stubs, export docs, closed or "unreadable"
# results. A build without the constant is level 1 (level_of); the shell reads it from an image
LEVEL = 2
# An export later than this after its stub warns: the backup set and the old timeline's WAL
# must still be in the repository (retention 28 days; goldfish C2-8)
EXPORT_WARN_AFTER = timedelta(days=14)
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


def level_of(module):
    """The level of a build's supersede module: a build without ``LEVEL`` is P1-T22c1's (1)."""
    return getattr(module, "LEVEL", 1)


def bucket_level(found):
    """The highest level of the whole-database restore objects in the bucket (0: none): a
    rollback to a build below it is refused (Q-K101; it would read them as bad objects)."""
    level = 1 if (found.plans or found.results or found.exports) else 0
    if (
        found.exports
        or any(p.flow != events.FLOWS[0] for p in found.plans.values())
        or any(
            r.closed or any(h["outcome"] == "unreadable" for h in r.hospitals.values())
            for r in found.results.values()
        )
    ):
        level = 2
    return level


def _refuse_pending(found, timeline, what, ancestors_pass=False):
    """Another whole-database restore moved the database and has no result: nothing new may
    start (P1-T22c1 verification; it would leave one of them unanchored or pending for good).
    Plans sharing ``timeline`` as their old one are siblings (only one moved the database).
    ``ancestors_pass`` (a disaster restore, P1-T22c2a review E1): a pending plan from an older
    timeline than the repository's newest does not block: the repository is already past it, a
    lost server can neither load nor close it (close needs a database on another timeline), and
    it keeps alerting as pending until its own result."""
    waiting = found.unfinished(timeline)
    if ancestors_pass:
        waiting = [
            r
            for r in waiting
            if int(found.plans[r].old_timeline, 16) > int(timeline, 16)
        ]
    if waiting:
        steps = "; ".join(f"{r[:8]}: {found.next_step(r)}" for r in waiting)
        msg = (
            f"{what} refused: whole-database restore {', '.join(r[:8] for r in waiting)} "
            f"restored the database and has no result yet: finish it first ({steps}) or "
            "close it (scripts/staging.sh audit-supersede close <id> --reason ...)"
        )
        raise Refused(msg)


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
    _refuse_pending(found, timeline, "the export")
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
            ok = name in (PLAN_MEMBER, EXPORT_MEMBER) or (
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


def disaster_fork(cursor, schema, lower, tail):
    """Flow (b): the fork point F = the highest seal at or after L that the restored chain
    shares with the package (the restored head at T, normally). Seal L must be shared, else the
    restored database is not an ancestor of the exported chain. (seq, hash hex)."""
    top = tail[-1]["seq"] if tail else lower[0]
    restored = {
        s["seq"]: s["hash"] for s in live_seals(cursor, schema, max(lower[0], 1), top)
    }
    if lower[0] and restored.get(lower[0]) != lower[1]:
        msg = (
            f"the restored chain's seal {lower[0]} is not the export's lower bound: the "
            "restored database is not an ancestor of the exported chain"
        )
        raise Refused(msg)
    fork = tuple(lower)
    for s in tail:
        if restored.get(s["seq"]) != s["hash"]:
            break
        fork = (s["seq"], s["hash"])
    return fork


def _at(text):
    return datetime.fromisoformat(text)


def latest_entry_at(entries, seal_doc):
    """The newest ``at`` of a package's entries inside one seal's window, or None."""
    low, high = int(seal_doc["lower"]), int(seal_doc["upper"])
    times = [_at(e["at"]) for e in entries if low <= int(e["txid"]) < high]
    return max(times) if times else None


def _fork_floor(plan, fork, latest, restores):
    """The load's own check of the fork floor (every reader checks it again)."""
    return events.fork_floor_problem(plan.target_time, fork, latest, restores)


class _Hospital:
    def __init__(self, hospital_uuid, slug):
        self.uuid, self.slug = hospital_uuid, slug


def load_one(  # noqa: PLR0912, PLR0915 (one check per step)
    hospital_uuid, entry, schema, body, plan, found, operator, store
):
    """One hospital of the plan (steps 1-7). Returns an Outcome. A disaster plan's (flow b):
    F is computed (``disaster_fork``) and bound by the floor; only seals F+1..head are
    superseded."""
    disaster = plan.flow == "disaster"
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
        if rows and (
            rows[0].seq != lower[0] + 1
            or bytes(rows[0].prev_hash).hex() != lower[1]
            or (rows[-1].seq, bytes(rows[-1].hash).hex(), events.segment_digest(rows))
            != (entry["head"]["seq"], entry["head"]["hash"], entry["digest"])
        ):
            msg = "its package's tail is not the one the plan names"
            raise Refused(msg)
        if disaster:
            # Flow (b): F is the restored head at T, computed, never chosen; seals the
            # restored database added since (served first) start a new branch after it
            fork = disaster_fork(cursor, schema, lower, tail)
        else:
            # Step 3, F = L (S3): the restored chain ended exactly at the plan's lower bound,
            # the release's fingerprint of the sealed head. Never a lower fork point. Read at
            # L, not at the head: a load run again after `up` finds seals the restored
            # database added since (its own restore entry sealed, new traffic; review T2/E2).
            # Those start a new branch after L; a seal after L that is the package's own was
            # in the database at T (a stale L): refused
            fork_problem = restored_fork_problem(cursor, schema, lower, tail)
            if fork_problem:
                raise Refused(fork_problem)
            fork = lower
        sup_tail = [s for s in tail if s["seq"] > fork[0]]
        rows = [r for r in rows if r.seq > fork[0]]
        if not rows:
            return Outcome(slug, "unchanged", fork=fork[0])
        if disaster:
            problem = _fork_floor(
                plan,
                fork,
                latest_entry_at(pkg.entries, sup_tail[0]),
                found.restores.get(hospital_uuid, []),
            )
            if problem:
                raise Refused(problem)
        low = int(sup_tail[0]["lower"])
        sup_entries = [e for e in pkg.entries if int(e["txid"]) >= low]
        digest = events.segment_digest(rows)
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
                    fork[0],
                    fork[1],
                    rows[0].seq,
                    rows[-1].seq,
                    entry["head"]["hash"],
                    digest,
                    PARKED,
                    f"{plan.target_name} {plan.target_time.isoformat()}"[:200],
                    operator[:64],
                ],
            )
            _insert_json(cursor, _INSERT_SEALS, hospital_uuid, generation, sup_tail)
            _insert_json(
                cursor, _INSERT_ENTRIES, hospital_uuid, generation, sup_entries
            )
        _probe(hospital_uuid, slug, generation, fork, rows, operator)
    # Step 6: the event, once (a generation already in the bucket is never written again)
    key = ""
    for event in found.restores.get(hospital_uuid, []):
        if event.generation == generation:
            if event.superseded["digest"] != digest:
                msg = f"restore event {generation} in the bucket is of another tail"
                raise Refused(msg)
            key = event.key
    if not key:
        key = events.write(
            events.restore_document(
                _Hospital(hospital_uuid, slug),
                generation,
                fork,
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
                            "restored_to": fork[0],
                            "superseded": [rows[0].seq, rows[-1].seq],
                            "target": plan.target_name,
                            "target_time": plan.target_time.isoformat(),
                            "event": key,
                            "reason": (
                                "disaster restore (staging.sh restore --target)"
                                if disaster
                                else "whole-database restore (staging.sh rollback --restore)"
                            ),
                        }
                    ),
                ],
            )
    return Outcome(slug, "loaded", generation, key, fork[0])


def _restored_hospitals(cursor):
    """The restored database's hospitals, as the export counts them (_hospitals: provisioning
    ones and schemas without a trail aside; review E4)."""
    return _hospitals(cursor)


def load(members, restore_id, operator, store=None, reader=None):
    """Steps 1-8 for every hospital of the plan. Returns {uuid: Outcome}; raises Refused
    (after every hospital was tried) when any was refused: then no result is written, and
    ``load`` runs again after the cause is fixed."""
    reader = reader or anchor.S3Store("READ")
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
    disaster = plan.flow == "disaster"
    entries = plan.hospitals
    if disaster:
        # Its hospitals are the export doc's; its documents go with the events key only
        # (audit S5: never the writer's key, which Celery holds)
        if plan.no_supersede:
            msg = f"restore {restore_id} was made with --no-supersede: nothing to load (close it)"
            raise Refused(msg)
        exp = found.exports.get(str(restore_id))
        if exp is None:
            msg = (
                f"restore {restore_id} is not exported yet: scripts/staging.sh audit-supersede "
                "export <id> first"
            )
            raise Refused(msg)
        entries = exp.hospitals
        store = store or events.events_store()
    else:
        store = store or events.writer_store()
    outcomes, refused = {}, []
    for hospital_uuid, entry in sorted(
        entries.items(), key=lambda item: item[1]["slug"]
    ):
        slug = entry["slug"]
        there = hospitals.get(hospital_uuid)
        try:
            if (
                entry["outcome"] == "unreadable"
            ):  # flow (c): named in the result, cuts stay
                outcomes[hospital_uuid] = Outcome(slug, "unreadable")
            elif (
                disaster and there is None
            ):  # made after T: not in the restored database
                outcomes[hospital_uuid] = Outcome(slug, "absent")
            elif entry["outcome"] == "absent":
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
        _write_result(restore_id, doc, store, reader)
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


def _write_result(restore_id, doc, store, reader):
    events.write_restore_doc(
        events.result_key(restore_id), doc, store=store, reader=reader
    )


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
        "flow": plan and plan.flow,
        "exported": found.exports[rid].key if rid in found.exports else None,
        "closed": result.closed if result else {},
        "pending": rid in found.pending_loads(),
        "hospitals": {},
    }
    entries = plan.hospitals if plan else {}
    if rid in found.exports:
        entries = found.exports[rid].hospitals
    for hospital_uuid, entry in sorted(entries.items(), key=lambda i: i[1]["slug"]):
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


# --- disaster restores, flow (b) (P1-T22c2a) -----------------------------------------------------


def _reason(text, what="--reason"):
    if not events.REASON_RE.match(text or ""):
        msg = f"{what}: 1-200 characters on one line"
        raise Refused(msg)
    return text


def write_stub(
    restore_id,
    target_time,
    old_timeline,
    image,
    operator,
    backup_set,
    archive_max,
    no_supersede="",
    store=None,
    reader=None,
):
    """The stub plan, written just before ``restore_live`` replaces anything: restore id, T, the
    repository's old timeline and backup set, no hospitals. Touches no database (a disaster
    server has none yet); the events key only."""
    store = store or events.events_store()
    reader = reader or anchor.S3Store("READ")
    if not events.WAL_RE.match(archive_max or "") or archive_max[:8] != old_timeline:
        msg = (
            f"the old timeline ({old_timeline}) must be the one of the repository archive's "
            f"newest WAL ({str(archive_max)[:24]})"
        )
        raise Refused(msg)
    if not events.BACKUP_SET_RE.match(backup_set or ""):
        msg = "the backup set is not a pgBackRest label"
        raise Refused(msg)
    if no_supersede:
        _reason(no_supersede, "--no-supersede --reason")
    found = events.read(reader, segments=events.NO_SEGMENTS, timeline=None)
    if not found.read_ok:
        msg = "the fingerprint bucket's restore objects could not be read"
        raise Refused(msg)
    _refuse_pending(found, old_timeline, "the disaster restore", ancestors_pass=True)
    extra = {"source": {"backup_set": backup_set, "archive_max": archive_max}}
    if no_supersede:
        extra["no_supersede"] = {"reason": no_supersede}
    when = target_time.astimezone(UTC)
    doc = events.plan_document(
        restore_id,
        f"time-{when:%Y%m%dT%H%M%SZ}",
        when,
        old_timeline,
        image,
        {},
        operator,
        flow="disaster",
        extra=extra,
    )
    key = events.write_restore_doc(
        events.plan_key(restore_id), doc, store=store, reader=reader
    )
    _emit(run_line(restore_id, "stub"))
    return key


def _disaster_plan(found, restore_id):
    rid = str(restore_id)
    plan = found.plans.get(rid)
    if plan is None or plan.flow != "disaster":
        msg = f"no disaster stub of restore {rid} in the bucket"
        raise Refused(msg)
    if plan.no_supersede:
        msg = f"restore {rid} was made with --no-supersede: nothing to export"
        raise Refused(msg)
    if rid in found.results:
        msg = f"restore {rid} already has its result"
        raise Refused(msg)
    _refuse_pending(found, plan.old_timeline, "the export", ancestors_pass=True)
    return plan


def _last_commit(text):
    try:
        when = datetime.fromisoformat(str(text).strip())
    except ValueError:
        return ""
    return when.astimezone(UTC).isoformat() if when.tzinfo else ""


def export_disaster(
    out, restore_id, image, operator, source_end="", accept_unreadable="", store=None
):
    """Every hospital's package from the repository copy (this database: the audit watch's
    read-only role) and the export doc's draft, as one tar on ``out``. T, the old timeline and
    the backup set are the stub's. Each hospital in its own savepoint: one that cannot be read
    refuses the export (named), unless ``accept_unreadable`` (a reason) writes it
    ``unreadable``."""
    store = store or anchor.S3Store("READ")
    found = events.read(
        store, timeline=None
    )  # the copy's timeline is its own, never used
    if not found.read_ok:
        msg = "the fingerprint bucket's events could not be read"
        raise Refused(msg)
    rid = str(restore_id)
    if rid in found.exports:
        msg = (
            f"restore {rid} is already exported (its export doc is write-once): run "
            "scripts/staging.sh audit-supersede load <id>"
        )
        raise Refused(msg)
    plan = _disaster_plan(found, rid)
    if accept_unreadable:
        _reason(accept_unreadable, "--accept-unreadable --reason")
    if datetime.now(UTC) - plan.stored > EXPORT_WARN_AFTER:
        _emit(
            f"WARNING: restore {rid[:8]}'s stub is older than {EXPORT_WARN_AFTER.days} days: "
            "its backup set and WAL leave the repository after 28 days"
        )
    target = plan.target_time
    fp_key, fp_stored, claims = anchor.claims_at_full(store, target - anchor.CLOCK_SKEW)
    hospitals, packages, failed = {}, {}, {}
    outer = not connection.in_atomic_block
    with transaction.atomic(), connection.cursor() as cursor:
        if outer:  # one snapshot of every hospital
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        for hospital_uuid, (slug, schema) in _hospitals(cursor).items():
            claim = claims.get(hospital_uuid)
            lower = events.lower_of(
                claim,
                fp_stored if claim else None,
                found.restores.get(hospital_uuid, []),
                plan.stored,
            )
            fp = fp_key if claim and tuple(claim[:2]) == lower else ""
            before = _events_before(found, hospital_uuid, target)
            try:
                with (
                    transaction.atomic()
                ):  # a savepoint: one hospital never blocks the rest
                    in_db = archive.generations_in_db(cursor, hospital_uuid)
                    if in_db < before:
                        msg = (
                            f"the bucket has {before} restore events of it before T and the "
                            f"copy only {in_db} restore records: they must agree"
                        )
                        raise Refused(msg)
                    body, entry = export_one(
                        cursor,
                        hospital_uuid,
                        slug,
                        schema,
                        (lower[0], lower[1], fp),
                        list(range(before + 1, in_db + 1)),
                        rid,
                    )
            except (Refused, DatabaseError) as exc:
                failed[hospital_uuid] = (slug, lower, f"{type(exc).__name__}: {exc}")
                _emit(log_line(slug, "unreadable", "export"))
                continue
            hospitals[hospital_uuid], packages[hospital_uuid] = entry, body
            _emit(log_line(slug, entry["outcome"], "export"))
    if failed and not accept_unreadable:
        msg = "; ".join(f"{slug}: {why}"[:300] for slug, _l, why in failed.values())
        raise Refused(msg + " (run again, or --accept-unreadable --reason)")
    for hospital_uuid, (slug, lower, _why) in failed.items():
        hospitals[hospital_uuid] = {
            "slug": slug,
            "outcome": "unreadable",
            "reason": accept_unreadable,
            "lower": {"seq": lower[0], "hash": lower[1], "fingerprint": ""},
            "head": {"seq": lower[0], "hash": lower[1]},
            "digest": "",
            "entries": 0,
            "unsealed": 0,
            "generations": [],
            "package_sha256": "",
        }
    draft = events.export_document(
        rid,
        plan.old_timeline,
        image,
        hospitals,
        operator,
        {
            "last_commit": _last_commit(source_end),
            "backup_set": plan.source["backup_set"],
        },
    )
    with tarfile.open(fileobj=out, mode="w|") as tar:
        for hospital_uuid, body in sorted(packages.items()):
            _add(tar, f"{hospital_uuid}.jsonl.gz", body)
        _add(tar, EXPORT_MEMBER, json.dumps(draft, sort_keys=True, indent=1).encode())
    return draft


def write_export(members, restore_id, store=None, reader=None, recovery=None):
    """The export doc's draft checked (every package's sha256, each L read again from the
    bucket, the stub's old timeline) and written once, with the events key only.
    ``recovery``: {stop, replayed} as supersede_parent read them from the restore (review T3):
    the stub's T must lie inside them (events.recovery_problem); written into the doc, which
    every reader requires."""
    store = store or events.events_store()
    reader = reader or anchor.S3Store("READ")
    rid = str(restore_id)
    try:
        draft = json.loads(members[EXPORT_MEMBER])
    except (KeyError, ValueError):
        msg = "the export has no export doc"
        raise Refused(msg) from None
    if draft.get("restore_id") != rid:
        msg = "the export is of another restore"
        raise Refused(msg)
    found = events.read(reader, segments=events.NO_SEGMENTS, timeline=None)
    have = found.exports.get(rid)
    plan = _disaster_plan(found, rid) if have is None else found.plans[rid]
    text = events.recovery_problem(recovery, plan.target_time)
    if text:
        msg = f"the restore does not match its stub's T: {text}"
        raise Refused(msg)
    draft["recovery"] = {
        "stop": recovery["stop"],
        "replayed": recovery.get("replayed") or "",
    }
    doc = events.parse_restore_doc(
        events.export_key(rid),
        events._body(draft),  # noqa: SLF001 (the events' own canonical form)
        datetime.fromisoformat(draft["written_at"]),
    )
    if doc.old_timeline != plan.old_timeline:
        msg = "the export's old timeline is not its stub's"
        raise Refused(msg)
    _key, fp_stored, claims = anchor.claims_at_full(
        reader, plan.target_time - anchor.CLOCK_SKEW
    )
    for hospital_uuid, entry in doc.hospitals.items():
        if entry["outcome"] == "exported":
            body = members.get(f"{hospital_uuid}.jsonl.gz")
            if body is None or entry["package_sha256"] != sha256(body):
                msg = f"{entry['slug']}: its package is missing or not the one the export made"
                raise Refused(msg)
        claim = claims.get(hospital_uuid)
        want = events.lower_of(
            claim,
            fp_stored if claim else None,
            found.restores.get(hospital_uuid, []),
            plan.stored,
        )
        if want != (entry["lower"]["seq"], entry["lower"]["hash"]):
            msg = f"{entry['slug']}: its lower bound is not the one the bucket gives at T - skew"
            raise Refused(msg)
    key = events.export_key(rid)
    if (
        have is not None
    ):  # a resumed run: the same draft (its own time aside), or refused
        stored = json.loads(reader.get(key))
        if {k: v for k, v in stored.items() if k != "written_at"} != {
            k: v for k, v in draft.items() if k != "written_at"
        }:
            msg = f"restore {rid} is already exported, with another export doc"
            raise Refused(msg)
        _emit(run_line(rid, "exported"))
        return key
    draft["written_at"] = datetime.now(UTC).isoformat()
    key = events.write_restore_doc(key, draft, store=store, reader=reader)
    _emit(run_line(rid, "exported"))
    return key


def close(restore_id, reason, operator, how="close", store=None, reader=None):
    """The result of a whole-database restore that cannot be loaded (Q-K100), or of
    ``restore --no-supersede`` (``how="no-supersede"``): every hospital not loaded is
    ``refused``; the timeline change is explained, the cuts stay. Refused before the restore
    happened and once any event of it is in the bucket (load it instead)."""
    reader = reader or anchor.S3Store("READ")
    rid = str(restore_id)
    if how not in events.CLOSED_HOW:
        msg = f"how: one of {', '.join(events.CLOSED_HOW)}"
        raise Refused(msg)
    _reason(reason)
    found = events.read(reader)
    if not found.read_ok:
        msg = "the fingerprint bucket's events could not be read"
        raise Refused(msg)
    plan = found.plans.get(rid)
    if plan is None:
        msg = f"no plan of restore {rid} in the bucket"
        raise Refused(msg)
    store = store or (
        events.events_store() if plan.flow == "disaster" else events.writer_store()
    )
    if rid in found.results:
        msg = f"restore {rid} already has its result"
        raise Refused(msg)
    if found.timeline is None or found.timeline == plan.old_timeline:
        msg = (
            f"the database is still on the plan's timeline {plan.old_timeline}: it was not "
            "restored, nothing to close"
        )
        raise Refused(msg)
    later = found.later_attempts(plan)
    if later:
        msg = (
            f"a later attempt from the same timeline {plan.old_timeline} has no result "
            f"(restore {', '.join(r[:8] for r in later)}): it, not this one, may be the restore "
            "that moved the database. Load or close it first; its result then supersedes this "
            "one (review E3)"
        )
        raise Refused(msg)
    if (how == "no-supersede") != bool(plan.no_supersede):
        msg = "--no-supersede closes only a restore whose stub says so, and only it"
        raise Refused(msg)
    if any(e.restore_id == rid for evs in found.restores.values() for e in evs):
        msg = (
            f"events of restore {rid} are in the bucket: finish it with scripts/staging.sh "
            "audit-supersede load <id>"
        )
        raise Refused(msg)
    if how == "no-supersede":
        claims = anchor.claims_at(reader, plan.target_time)[1]
        named = {u: c[2] for u, c in claims.items()}
    elif rid in found.exports:
        named = {u: e["slug"] for u, e in found.exports[rid].hospitals.items()}
    elif plan.flow == "disaster":
        # A stub has no hospitals before its export: those of the fingerprint at T, as
        # --no-supersede names them (review E8)
        claims = anchor.claims_at(reader, plan.target_time)[1]
        named = {u: c[2] for u, c in claims.items()}
    else:
        named = {u: e["slug"] for u, e in plan.hospitals.items()}
    doc = events.result_document(
        rid,
        plan.old_timeline,
        found.timeline,
        {u: Outcome(slug, "refused").entry() for u, slug in named.items()},
        operator,
        closed={"reason": reason, "how": how},
    )
    _write_result(rid, doc, store, reader)
    _emit(run_line(rid, "closed"))
    return events.result_key(rid)
