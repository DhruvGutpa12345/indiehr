"""Incremental checks of the audit chain (P1-T21; docs/reviews/p1-t21/design.md, "After the
attack"). Shared by the audit watch (ihr_audit/watch.py) and ``ihr_audit_verify
--from-checkpoint``.

``seal.verify`` recomputes a whole chain from seal 1, one query per seal: hours at fleet scale,
so it runs on the monthly restore-drill copy. Live, the chain is checked in three cheaper ways:

- **Header walk** (``header_walk``, every 15 minutes): every seal row from seal 1, in one query
  per hospital, entries untouched. Each seal's number follows the previous one, its window
  starts where the previous one ended, its ``prev_hash`` is the previous seal's stored hash,
  and its hash is recomputed from its stored fields. A seal rewritten consistently in itself
  (its entries hash and hash recomputed) breaks the next seal's link, wherever it is (A3).
- **From the checkpoint** (``check_from_checkpoint``, every 15 minutes): the watch remembers
  each hospital's last verified seal (number, hash, window end) in its own volume, outside
  the database. That seal must still be there with that hash (a moved checkpoint is refused),
  and every newer seal's entries are recomputed, many windows per query (``recompute``).
- **Rolling recompute** (``recompute`` over the horizon, nightly): the entries of every seal
  sealed in the last ``horizon`` days, within a budget (watch.py).

A checkpoint is keyed by hospital uuid and restore generation (events.py). A missing one is
seeded from the newest **fingerprint** of that hospital (the write-once bucket), never from the
database's head (A8); a hospital no fingerprint names yet is checked from seal 1.
"""

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from psycopg import sql

from ihr_audit import seal

GENESIS_HEX = seal.GENESIS.hex()
# One recompute query covers at most this many entries (or seals): a bounded statement
CHUNK_ENTRIES = 20000
CHUNK_SEALS = 2000
# Problem rows the header walk names (it counts them all)
WALK_NAMED = 20
# The triggers the trail rests on: none of them is ever off legitimately (A10)
AUDIT_TRIGGERS = (
    "ihr_audit",
    "ihr_audit_truncate",
    "ihr_audit_append_only",
    "ihr_audit_no_truncate",
    "ihr_audit_stamp",
)


def _names(schema):
    return {
        "entry": sql.Identifier(schema, "ihr_audit_entry"),
        "seal": sql.Identifier(schema, "ihr_audit_seal"),
    }


def _tag_case():
    """The seal hash's format tag by version, as SQL (seal._TAG); NULL for an unknown one."""
    return sql.SQL("CASE s.version {} END").format(
        sql.SQL(" ").join(
            sql.SQL("WHEN {} THEN {}").format(sql.Literal(v), sql.Literal(tag))
            for v, tag in sorted(seal._TAG.items())  # noqa: SLF001 (the seal format's own table)
        )
    )


# --- the header walk -------------------------------------------------------------------------

_WALK = """
WITH s AS (
    SELECT s.seq, s.lower_txid::text AS lower, s.upper_txid::text AS upper, s.entries,
           s.entries_hash, s.prev_hash, s.hash, s.version,
           lag(s.seq) OVER w AS prev_seq,
           lag(s.upper_txid::text) OVER w AS prev_upper,
           lag(s.hash) OVER w AS prev_stored
    FROM {seal} s WINDOW w AS (ORDER BY s.seq)
), judged AS (
    SELECT s.seq,
           s.seq <> coalesce(s.prev_seq, 0) + 1 AS gap,
           s.lower <> coalesce(s.prev_upper, '0') AS bounds,
           s.prev_hash <> coalesce(s.prev_stored, {genesis}) AS link,
           {tag} IS NULL AS format,
           s.hash IS DISTINCT FROM sha256(s.prev_hash || convert_to({tag} || s.seq::text
               || '|' || s.lower || '|' || s.upper || '|' || s.entries::text, 'UTF8')
               || s.entries_hash) AS hash
    FROM s
)
SELECT count(*) FILTER (WHERE gap OR bounds OR link OR format OR hash),
       (array_agg(ARRAY[seq::text, gap::text, bounds::text, link::text, format::text,
                        hash::text] ORDER BY seq)
            FILTER (WHERE gap OR bounds OR link OR format OR hash))[1:{named}],
       count(*)
FROM judged
"""
_HEAD = (
    "SELECT seq, encode(hash, 'hex'), upper_txid::text, sealed_at FROM {seal} "
    "ORDER BY seq DESC LIMIT 1"
)
_KINDS = (
    ("gap", "a seal is missing before it"),
    ("bounds", "does not start where the previous seal ended"),
    ("link", "does not follow the previous seal (its prev_hash)"),
    ("format", "unknown format"),
    ("hash", "hash does not match its stored fields"),
)


@dataclass
class Walk:
    seals: int = 0
    head_seq: int = 0
    head_hash: str = GENESIS_HEX
    head_upper: str = "0"
    head_sealed_at: datetime | None = None
    bad: int = 0
    problems: list = field(default_factory=list)
    seconds: float = 0.0


def header_walk(cursor, schema):
    """Every seal of ``schema`` checked from its stored fields (no entry is read)."""
    began = time.monotonic()
    names = _names(schema)
    cursor.execute(
        sql.SQL(_WALK).format(
            seal=names["seal"],
            genesis=sql.SQL("'\\x{}'::bytea").format(sql.SQL(GENESIS_HEX)),
            tag=_tag_case(),
            named=sql.Literal(WALK_NAMED),
        )
    )
    bad, named, count = cursor.fetchone()
    walk = Walk(seals=count, bad=bad)
    for row in named or []:
        seq, flags = row[0], [flag == "true" for flag in row[1:]]
        why = [text for (_, text), on in zip(_KINDS, flags, strict=True) if on]
        walk.problems.append(f"seal {seq}: {', '.join(why)}")
    if bad > len(named or []):
        walk.problems.append(f"... {bad} seals in all fail the header walk")
    cursor.execute(sql.SQL(_HEAD).format(**names))
    head = cursor.fetchone()
    if head:
        walk.head_seq, walk.head_hash, walk.head_upper, walk.head_sealed_at = head
    walk.seconds = time.monotonic() - began
    return walk


def seal_at(cursor, schema, seq):
    """(hash hex, upper) of seal ``seq``, or None. Seal 0 is the genesis."""
    if seq == 0:
        return GENESIS_HEX, "0"
    cursor.execute(
        sql.SQL(
            "SELECT encode(hash, 'hex'), upper_txid::text FROM {seal} WHERE seq = %s"
        ).format(**_names(schema)),
        [seq],
    )
    return cursor.fetchone()


# --- entries recomputed, many windows per query ---------------------------------------------

_RECOMPUTE = """
SELECT s.seq, s.lower_txid::text, s.upper_txid::text, s.entries, s.entries_hash, s.prev_hash,
       s.hash, s.version, count(e.id),
       sha256(coalesce(string_agg({leaf}, ''::bytea ORDER BY e.txid, e.id), ''::bytea))
FROM {seal} s
LEFT JOIN {entry} e ON e.txid >= s.lower_txid AND e.txid < s.upper_txid
WHERE s.seq >= %s AND s.seq <= %s AND s.version = %s
GROUP BY s.seq ORDER BY s.seq
"""


# Seal rows read per query by _chunks: a year of one seal a minute is 525,600 rows, never held
# in memory at once (P1-T21 review C6; the watch's limit is 256 MB)
SEAL_BATCH = 10000


def _seal_rows(cursor, schema, first, last):
    """(seq, entries, version) of seals ``first``..``last``, in keyset batches. Each batch is
    fetched whole before it is yielded, so the caller may use the cursor between rows."""
    query = sql.SQL(
        "SELECT seq, entries, version FROM {seal} WHERE seq >= %s AND seq <= %s "
        "ORDER BY seq LIMIT %s"
    ).format(**_names(schema))
    low = first
    while low <= last:
        cursor.execute(query, [low, last, SEAL_BATCH])
        rows = cursor.fetchall()
        yield from rows
        if len(rows) < SEAL_BATCH:
            return
        low = rows[-1][0] + 1


def _chunks(cursor, schema, first, last, max_entries):
    """Seal ranges [a, b] from ``first`` to ``last`` of at most CHUNK_ENTRIES entries each
    (a bigger seal alone), stopping once ``max_entries`` would be passed. Also the versions
    in each range."""
    chunk, size, used = [], 0, 0
    for seq, entries, version in _seal_rows(cursor, schema, first, last):
        if chunk and (size + entries > CHUNK_ENTRIES or len(chunk) >= CHUNK_SEALS):
            yield chunk[0][0], chunk[-1][0], {v for _, v in chunk}, size
            used += size
            chunk, size = [], 0
        if (
            max_entries is not None
            and used + size + entries > max_entries
            and (used + size)
        ):
            break
        chunk.append((seq, version))
        size += entries
    if chunk:
        yield chunk[0][0], chunk[-1][0], {v for _, v in chunk}, size


@dataclass
class Recompute:
    problems: list = field(default_factory=list)
    last_seq: int = 0  # the newest seal recomputed
    entries: int = 0
    seals: int = 0
    seconds: float = 0.0


def recompute(cursor, schema, first, last, max_entries=None, deadline=None):
    """Recompute the entries of seals ``first``..``last`` against each seal: its count, its
    entries hash, and its hash from the recomputed entries hash and its stored prev_hash
    (A3). Stops early once ``max_entries`` entries were read (the rolling budget), or after
    the chunk in which ``deadline`` (time.monotonic()) passed."""
    began = time.monotonic()
    result = Recompute(last_seq=first - 1)
    if last < first:
        return result
    names = _names(schema)
    for low, high, versions, size in _chunks(cursor, schema, first, last, max_entries):
        rows = []
        for version in sorted(versions):
            if version not in seal.CANONICAL:
                result.problems.append(f"seals {low}-{high}: unknown format {version}")
                continue
            cursor.execute(
                sql.SQL(_RECOMPUTE).format(
                    leaf=sql.SQL(seal.leaf_sql(version)), **names
                ),
                [low, high, version],
            )
            rows += cursor.fetchall()
        for (
            seq,
            lower,
            upper,
            count,
            entries_hash,
            prev_hash,
            stored,
            version,
            now_count,
            now_hash,
        ) in sorted(rows):
            where = f"seal {seq}"
            if now_count != count:
                result.problems.append(
                    f"{where}: {count} entries sealed, {now_count} now"
                )
            if bytes(now_hash) != bytes(entries_hash):
                result.problems.append(f"{where}: entries changed since sealing")
            if bytes(stored) != seal.seal_hash(
                prev_hash, seq, lower, upper, now_count, now_hash, version
            ):
                result.problems.append(f"{where}: hash does not match its entries")
        result.entries += size
        result.seals += high - low + 1
        result.last_seq = high
        if deadline is not None and time.monotonic() > deadline:
            break
    result.seconds = time.monotonic() - began
    return result


def horizon_start(cursor, schema, since):
    """The first seal sealed at or after ``since`` (None: none)."""
    cursor.execute(
        sql.SQL("SELECT min(seq) FROM {seal} WHERE sealed_at >= %s").format(
            **_names(schema)
        ),
        [since],
    )
    return cursor.fetchone()[0]


def entries_between(cursor, schema, first, last):
    cursor.execute(
        sql.SQL(
            "SELECT coalesce(sum(entries), 0) FROM {seal} WHERE seq >= %s AND seq <= %s"
        ).format(**_names(schema)),
        [first, last],
    )
    return int(cursor.fetchone()[0])


# --- what is not sealed yet (the fast pass) -------------------------------------------------

_HEAD_SEAL = (
    "SELECT seq, sealed_at, upper_txid::text FROM {seal} ORDER BY seq DESC LIMIT 1"
)
# The entries after the newest seal's window, split at the horizon: an index range scan
# (ihr_audit_entry_txid) from a constant bound. A bound from a subquery is not used for the
# index (measured: a full scan of 500,000 entries, 0.34 s, P1-T21 As built)
_UNSEALED = """
SELECT count(*) FILTER (WHERE e.txid < h.horizon),
       min(e.at) FILTER (WHERE e.txid < h.horizon),
       count(*) FILTER (WHERE e.txid >= h.horizon),
       min(e.at) FILTER (WHERE e.txid >= h.horizon)
FROM (SELECT pg_snapshot_xmin(pg_current_snapshot()) AS horizon) h
LEFT JOIN {entry} e ON e.txid >= %s::xid8
GROUP BY h.horizon
"""


@dataclass
class Unsealed:
    head_seq: int = 0
    head_sealed_at: datetime | None = None
    sealable: int = 0  # finished entries not sealed yet
    sealable_oldest: datetime | None = None
    held_back: int = 0  # entries above the horizon (a transaction older than them runs)
    held_back_oldest: datetime | None = None


def unsealed(cursor, schema):
    """The newest seal (by its primary key), then one index range query: the entries after
    its window, split at the horizon."""
    names = _names(schema)
    cursor.execute(sql.SQL(_HEAD_SEAL).format(**names))
    head = cursor.fetchone()
    head_seq, sealed_at, upper = head if head else (0, None, "0")
    cursor.execute(sql.SQL(_UNSEALED).format(**names), [upper])
    sealable, oldest, held, held_oldest = cursor.fetchone()
    return Unsealed(head_seq, sealed_at, sealable, oldest, held, held_oldest)


def disabled_triggers(cursor):
    """{schema: number of the trail's triggers that are not enabled}, every hospital schema
    in one catalogue query (A10). The stamp trigger may also be ALWAYS (seal.py)."""
    cursor.execute(
        "SELECT n.nspname, count(*) FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname ~ '^h_[a-z0-9]{10}$' AND NOT t.tgisinternal "
        "AND t.tgname = ANY(%s) "
        "AND NOT (t.tgenabled = 'O' OR (t.tgname = 'ihr_audit_stamp' AND t.tgenabled = 'A')) "
        "GROUP BY 1",
        [list(AUDIT_TRIGGERS)],
    )
    return dict(cursor.fetchall())


# --- checkpoints ----------------------------------------------------------------------------


@dataclass
class Checkpoint:
    hospital: str  # uuid
    generation: int  # restore generation (events.py)
    seq: int
    hash: str
    upper: str
    at: str  # when it was verified (ISO 8601)
    seeded: str = (
        ""  # "fingerprint" or "genesis" when it was made without a predecessor
    )


class CheckpointStore:
    """One small JSON file per hospital in the watch's own volume (never the database's):
    ``<root>/checkpoints/<uuid>.json``, replaced atomically. ``<root>/created`` dates the
    store itself (a wiped volume shows as a new store, A8)."""

    def __init__(self, root):
        self.root = Path(root)
        self.dir = self.root / "checkpoints"

    def ensure(self):
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        created = self.root / "created"
        if not created.exists():
            _atomic(created, datetime.now(UTC).isoformat())
        return self

    def created(self):
        try:
            return datetime.fromisoformat((self.root / "created").read_text().strip())
        except (OSError, ValueError):
            return None

    def _path(self, hospital_uuid):
        if not _UUID(hospital_uuid):
            msg = f"not a hospital uuid: {hospital_uuid!r}"
            raise ValueError(msg)
        return self.dir / f"{hospital_uuid}.json"

    def get(self, hospital_uuid):
        try:
            data = json.loads(self._path(hospital_uuid).read_text())
            return Checkpoint(**data)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError):
            return None  # unreadable: reseeded from the fingerprint, and counted

    def put(self, checkpoint):
        _atomic(
            self._path(checkpoint.hospital),
            json.dumps(asdict(checkpoint), sort_keys=True),
        )

    def hospitals(self):
        if not self.dir.exists():
            return []
        return sorted(p.stem for p in self.dir.glob("*.json"))

    def drop(self, hospital_uuid):
        self._path(hospital_uuid).unlink(missing_ok=True)


def _UUID(value):  # noqa: N802 (a predicate on the checkpoint's file name)
    from ihr_audit.anchor import UUID_RE

    return bool(UUID_RE.match(str(value)))


def _atomic(path, text):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- one hospital from its checkpoint -------------------------------------------------------


@dataclass
class Result:
    problems: list = field(default_factory=list)
    checkpoint: Checkpoint | None = None  # the new one (None: not advanced)
    seeded: str = ""  # "fingerprint", "genesis" or "" (it had one)
    lost: bool = (
        False  # it had none, or an unreadable one, though a fingerprint names it
    )
    rebased: bool = False  # moved to a restore event's restored_to
    # No checkpoint, and none could be seeded because the fingerprints are unreadable: nothing
    # is stored, and the next pass that can read them seeds it (A8; P1-T21 review S5/C6)
    unseeded: bool = False
    partial: bool = (
        False  # the recompute stopped at its budget; the checkpoint is where it got
    )
    walk: Walk | None = None
    recomputed: Recompute | None = None


def _rebase(cp, events, hospital_uuid, problems):
    """A checkpoint of an older restore generation, moved through each newer restore event:
    kept if it is at or below the restored head, else it must be in that restore's verified
    superseded segment, and becomes the restored head."""
    for event in events.restores.get(hospital_uuid, [])[cp.generation :]:
        seq, digest = event.restored_to
        if cp.seq > seq:
            if event.problems or event.segment_hashes.get(cp.seq) != cp.hash:
                problems.append(
                    f"the watch's checkpoint (seal {cp.seq}) is not in restore "
                    f"{event.generation}'s verified superseded segment"
                    + (f": {event.problems[0]}" if event.problems else "")
                )
                return None
            cp = Checkpoint(
                cp.hospital, event.generation, seq, digest, "", cp.at, cp.seeded
            )
        else:
            cp = Checkpoint(
                cp.hospital,
                event.generation,
                cp.seq,
                cp.hash,
                cp.upper,
                cp.at,
                cp.seeded,
            )
    return cp


def _seed(cursor, schema, hospital_uuid, claim, events, problems):
    """A checkpoint for a hospital that has none: from its newest fingerprint (seq, hash,
    stored), never from the database's head; the genesis when no fingerprint names it."""
    generation = events.generation(hospital_uuid)
    now = datetime.now(UTC).isoformat()
    if not claim or claim[0] == 0:
        return Checkpoint(
            hospital_uuid, generation, 0, GENESIS_HEX, "0", now, "genesis"
        )
    seq, digest, stored = claim
    restores = events.restores.get(hospital_uuid, [])
    if restores and stored < restores[-1].stored and seq > restores[-1].restored_to[0]:
        # The newest fingerprint is of the chain a restore replaced: its seal must be in that
        # restore's verified segment, and the restored head is the seed
        event = restores[-1]
        if event.problems or event.segment_hashes.get(seq) != digest:
            problems.append(
                f"seal {seq} of the newest fingerprint is not in restore "
                f"{event.generation}'s verified superseded segment"
            )
            return None
        seq, digest = event.restored_to
    found = seal_at(cursor, schema, seq)
    if found is None:
        problems.append(
            f"seal {seq}, named by the newest fingerprint, is missing (cut)"
        )
        return None
    if found[0] != digest:
        problems.append(
            f"seal {seq} differs from the newest fingerprint (the trail was rewritten)"
        )
        return None
    return Checkpoint(
        hospital_uuid, generation, seq, digest, found[1], now, "fingerprint"
    )


def check_from_checkpoint(  # noqa: PLR0911, PLR0912 (one return per verdict)
    cursor,
    schema,
    hospital_uuid,
    checkpoint,
    claim,
    events,
    seed_ok=True,
    max_entries=None,
    deadline=None,
):
    """The 15-minute check of one hospital: the header walk, its checkpoint, and the entries
    of every seal after it. ``claim``: its newest fingerprint (seq, hash, stored) or None.
    Returns a Result whose ``checkpoint`` is the one to store (None: keep the old one; the
    problem stays until it is resolved, never reset silently).

    ``seed_ok`` False (the fingerprints exist but cannot be read now): a hospital with no
    checkpoint is not seeded at all, never from the genesis blindly; the header walk still
    runs. ``max_entries`` / ``deadline``: the entries recomputed in this call are bounded (a
    first check of a long chain); the checkpoint then moves only as far as was recomputed."""
    result = Result()
    result.walk = walk = header_walk(cursor, schema)
    result.problems += walk.problems
    cp = checkpoint
    # A failed events read knows no restore (generation 0): the checkpoint's own generation is
    # then not compared, and the seal it names is checked as it is (a restore while the events
    # cannot be read reads as a rewrite: fail closed). Never "one was lost" from an outage
    known = events.generation(hospital_uuid) if events.read_ok else None
    if cp is not None and known is not None and cp.generation > known:
        result.problems.append(
            f"the watch's checkpoint is of restore generation {cp.generation}, but the "
            f"bucket has {known} restore events (one was lost?)"
        )
        return result
    if cp is not None and known is not None and cp.generation < known:
        cp = _rebase(cp, events, hospital_uuid, result.problems)
        if cp is None:
            return result
        result.rebased = True
    if cp is None and not seed_ok:
        result.unseeded = True
        return result
    if cp is None:
        cp = _seed(cursor, schema, hospital_uuid, claim, events, result.problems)
        if cp is None:
            return result
        result.seeded = cp.seeded
        # A checkpoint made again for a chain that already has seals: from a fingerprint, or
        # from seal 1 because none names it (the watch decides whether it should have had one)
        result.lost = cp.seeded == "fingerprint" or (
            cp.seeded == "genesis" and walk.head_seq > 0
        )
    else:
        found = seal_at(cursor, schema, cp.seq)
        if found is None:
            result.problems.append(
                f"seal {cp.seq}, the watch's checkpoint of {cp.at[:19]}, is missing "
                "(the trail was cut)"
            )
            return result
        if found[0] != cp.hash:
            result.problems.append(
                f"seal {cp.seq} differs from the watch's checkpoint of {cp.at[:19]} "
                "(the trail was rewritten)"
            )
            return result
        cp = Checkpoint(
            cp.hospital, cp.generation, cp.seq, cp.hash, found[1], cp.at, cp.seeded
        )
    if walk.head_seq < cp.seq:
        result.problems.append(
            f"the chain ends at seal {walk.head_seq}, before seal {cp.seq}"
        )
        return result
    result.recomputed = done = recompute(
        cursor, schema, cp.seq + 1, walk.head_seq, max_entries, deadline
    )
    result.problems += done.problems
    if result.problems:
        return result
    if done.last_seq < walk.head_seq:
        # Bounded: the checkpoint moves to the last seal recomputed (still marked seeded), and
        # the next pass goes on from there
        result.partial = True
        if done.last_seq <= cp.seq:
            return result
        found = seal_at(cursor, schema, done.last_seq)
        if found is None:
            result.problems.append(
                f"seal {done.last_seq} is missing (the trail was cut)"
            )
            return result
        result.checkpoint = Checkpoint(
            hospital_uuid,
            cp.generation,
            done.last_seq,
            found[0],
            found[1],
            datetime.now(UTC).isoformat(),
            cp.seeded,
        )
        return result
    result.checkpoint = Checkpoint(
        hospital_uuid,
        cp.generation,
        walk.head_seq,
        walk.head_hash,
        walk.head_upper,
        datetime.now(UTC).isoformat(),
        cp.seeded if cp.seq == walk.head_seq else "",
    )
    return result
