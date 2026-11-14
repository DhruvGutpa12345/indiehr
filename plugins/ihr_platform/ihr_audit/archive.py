"""The superseded-segment archive (P1-T22b; design "After the attack" 6 and 7).

A single-hospital restore replaces the live audit chain's tail (the seals sealed after the dump)
with the restored chain. That tail is kept here, outside every hospital schema, in ``public``:

    ihr_restore_record             one row per hospital and restore generation (1, 2, ...)
    ihr_restore_superseded_seal    the live seals the restore replaced, as they were
    ihr_restore_superseded_entry   every entry of those seals, with its salt and values

Append-only (a trigger refuses UPDATE, DELETE and TRUNCATE; migration ihr_platform 0012); the
app role has no right on them, the audit watch's role reads them. Written only by the owner-run
restore (``archive``), in the same transaction as nothing else: the rows are copied from the
parked schema, which is the live chain as it was.

``segment(hospital uuid, generation)`` is ``events.SEGMENTS.source`` (set in apps.py): a restore
event excuses a fingerprint claim only when its segment is here and verifies, its seals' entries
recomputed from the archived entries (``verify_entries``), with the same canonical texts the
chain itself uses (seal.py). A restore event with no segment here, or one that does not verify,
excuses nothing: it reads as the cut it would hide.
"""

from django.db import connection
from psycopg import sql

from ihr_audit import events, seal

RECORD = "ihr_restore_record"
SEALS = "ihr_restore_superseded_seal"
ENTRIES = "ihr_restore_superseded_entry"
_COLUMNS = "id, txid, at, kind, actor, context, tbl, op, row_pk, patients, data, salt"


def next_generation(cursor, hospital_uuid):
    """The generation the hospital's next restore takes: one more than its records."""
    cursor.execute(
        "SELECT coalesce(max(generation), 0) FROM public.ihr_restore_record WHERE hospital = %s",
        [str(hospital_uuid)],
    )
    return cursor.fetchone()[0] + 1


def generation_of(cursor, hospital_uuid, operation):
    """The generation a restore operation recorded, or None."""
    cursor.execute(
        "SELECT generation FROM public.ihr_restore_record WHERE hospital = %s AND operation = %s",
        [str(hospital_uuid), str(operation)],
    )
    row = cursor.fetchone()
    return row[0] if row else None


def recorded(cursor, hospital_uuid, generation):
    """The record of that restore as a dict, or None."""
    cursor.execute(
        "SELECT operation::text, restored_seq, encode(restored_hash, 'hex'), from_seq, to_seq, "
        "encode(head_hash, 'hex'), digest, parked_schema, dump, operator "
        "FROM public.ihr_restore_record WHERE hospital = %s AND generation = %s",
        [str(hospital_uuid), generation],
    )
    row = cursor.fetchone()
    if row is None:
        return None
    keys = (
        "operation",
        "restored_seq",
        "restored_hash",
        "from_seq",
        "to_seq",
        "head_hash",
        "digest",
        "parked_schema",
        "dump",
        "operator",
    )
    return dict(zip(keys, row, strict=True))


def live_rows(cursor, schema, from_seq, to_seq=None):
    """SealRow of ``schema``'s seals from ``from_seq`` (to ``to_seq``), in order."""
    cursor.execute(
        sql.SQL(
            "SELECT seq, lower_txid::text, upper_txid::text, entries, entries_hash, prev_hash, "
            "hash, version FROM {} WHERE seq >= %s AND (%s::bigint IS NULL OR seq <= %s) "
            "ORDER BY seq"
        ).format(sql.Identifier(schema, "ihr_audit_seal")),
        [from_seq, to_seq, to_seq],
    )
    return [
        events.SealRow(*row[:4], bytes(row[4]), bytes(row[5]), bytes(row[6]), row[7])
        for row in cursor.fetchall()
    ]


def archive(
    cursor,
    *,
    hospital,
    generation,
    operation,
    parked_schema,
    restored_to,
    dump,
    operator,
):
    """Copy the parked schema's seals after ``restored_to`` (seq, hash hex) and their entries
    into the archive as ``generation``. Returns the SealRow list (empty: nothing was sealed
    after the dump, so nothing is superseded and nothing is written)."""
    seq, head = restored_to
    rows = live_rows(cursor, parked_schema, seq + 1)
    if not rows:
        return []
    if rows[0].seq != seq + 1 or bytes(rows[0].prev_hash).hex() != head:
        msg = (
            f"the parked chain does not continue the restored head (seal {seq}): the dump is "
            "not of this hospital's chain"
        )
        raise ValueError(msg)
    uuid = str(hospital.uuid)
    cursor.execute(
        "INSERT INTO public.ihr_restore_record (hospital, generation, operation, slug, "
        "restored_seq, restored_hash, from_seq, to_seq, head_hash, digest, parked_schema, dump, "
        "operator) VALUES (%s, %s, %s, %s, %s, decode(%s, 'hex'), %s, %s, %s, %s, %s, %s, %s)",
        [
            uuid,
            generation,
            str(operation),
            hospital.slug,
            seq,
            head,
            rows[0].seq,
            rows[-1].seq,
            bytes(rows[-1].hash),
            events.segment_digest(rows),
            parked_schema,
            dump[:200],
            operator[:64],
        ],
    )
    cursor.execute(
        sql.SQL(
            "INSERT INTO public.ihr_restore_superseded_seal (hospital, generation, seq, "
            "lower_txid, upper_txid, entries, entries_hash, prev_hash, hash, sealed_at, version) "
            "SELECT %s, %s, seq, lower_txid, upper_txid, entries, entries_hash, prev_hash, hash, "
            "sealed_at, version FROM {} WHERE seq BETWEEN %s AND %s"
        ).format(sql.Identifier(parked_schema, "ihr_audit_seal")),
        [uuid, generation, rows[0].seq, rows[-1].seq],
    )
    cursor.execute(
        sql.SQL(
            "INSERT INTO public.ihr_restore_superseded_entry (hospital, generation, {cols}) "
            "SELECT %s, %s, {cols} FROM {entry} WHERE txid >= %s::xid8 AND txid < %s::xid8"
        ).format(
            cols=sql.SQL(_COLUMNS),
            entry=sql.Identifier(parked_schema, "ihr_audit_entry"),
        ),
        [uuid, generation, rows[0].lower, rows[-1].upper],
    )
    return rows


def _seal_rows(cursor, hospital_uuid, generation):
    cursor.execute(
        "SELECT seq, lower_txid::text, upper_txid::text, entries, entries_hash, prev_hash, hash, "
        "version FROM public.ihr_restore_superseded_seal WHERE hospital = %s AND generation = %s "
        "ORDER BY seq",
        [str(hospital_uuid), generation],
    )
    return [
        events.SealRow(*row[:4], bytes(row[4]), bytes(row[5]), bytes(row[6]), row[7])
        for row in cursor.fetchall()
    ]


def verify_entries(cursor, hospital_uuid, generation, rows):
    """Each archived seal's window recomputed from the archived entries (count and
    entries_hash, the chain's own canonical texts); and no archived entry outside the seals'
    windows. Problems, as text."""
    problems = []
    for row in rows:
        version = row.version if row.version in seal.CANONICAL else seal.FORMAT
        cursor.execute(
            "SELECT count(*), sha256(coalesce(string_agg("
            + seal.leaf_sql(version)
            + ", ''::bytea ORDER BY e.txid, e.id), ''::bytea)) "
            "FROM public.ihr_restore_superseded_entry e WHERE e.hospital = %s "
            "AND e.generation = %s AND e.txid >= %s::xid8 AND e.txid < %s::xid8",
            [str(hospital_uuid), generation, row.lower, row.upper],
        )
        count, digest = cursor.fetchone()
        if count != row.entries:
            problems.append(
                f"superseded seal {row.seq}: {row.entries} entries sealed, {count} kept"
            )
        if bytes(digest) != bytes(row.entries_hash):
            problems.append(
                f"superseded seal {row.seq}: its kept entries do not hash to it"
            )
    if rows:
        cursor.execute(
            "SELECT count(*) FROM public.ihr_restore_superseded_entry WHERE hospital = %s "
            "AND generation = %s AND (txid < %s::xid8 OR txid >= %s::xid8)",
            [str(hospital_uuid), generation, rows[0].lower, rows[-1].upper],
        )
        if outside := cursor.fetchone()[0]:
            problems.append(f"{outside} kept entries lie outside the superseded seals")
    return problems


class ArchiveUnreadableError(PermissionError):
    """This process's role may not read the archive (the app role never may: run the check as
    the owner or the audit watch's role)."""


def readable(cursor):
    """True when this session may read all three archive tables (False also when they do not
    exist yet). Asked first, so a refusal never aborts the caller's transaction."""
    cursor.execute(
        "SELECT bool_and(CASE WHEN to_regclass(t) IS NULL THEN false "
        "ELSE has_table_privilege(t, 'SELECT') END) "
        "FROM unnest(%s::text[]) t",
        [[f"public.{RECORD}", f"public.{SEALS}", f"public.{ENTRIES}"]],
    )
    return bool(cursor.fetchone()[0])


def segment(hospital_uuid, generation):
    """events.SEGMENTS.source: the archived segment of that restore, or None. Raises
    ArchiveUnreadableError for a role without the right (check_restore then names it: the event
    excuses nothing)."""
    with connection.cursor() as cursor:
        if not readable(cursor):
            msg = (
                "the restore archive is not readable by this database role (ihr_audit_verify "
                "after a restore runs as the owner or the audit watch's role)"
            )
            raise ArchiveUnreadableError(msg)
        if recorded(cursor, hospital_uuid, generation) is None:
            return None
        rows = _seal_rows(cursor, hospital_uuid, generation)

    def check():
        with connection.cursor() as cursor:
            return verify_entries(cursor, hospital_uuid, generation, rows)

    return events.Segment(seals=rows, verify_entries=check)


def install():
    """Superseded segments are read from here from now on (events.SEGMENTS)."""
    events.SEGMENTS.source = segment
