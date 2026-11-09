"""The hash chain over a hospital's audit trail (P1-T6a, ADR-010).

Entries are sealed in batches. Seal ``n`` covers every entry whose writing transaction id
(``txid``) is in ``[lower, upper)``, in (txid, id) order:

    entries_hash = sha256(sha256(entry_1) || ... || sha256(entry_k))
    hash         = sha256(prev_hash || "v2|n|lower|upper|k" || entries_hash)

``entry_i`` is the entry's canonical text. Format 3 (every seal since P1-T6b) is two levels:

    canonical = [at, patients, sha256(inner)]
    inner     = [id, txid, kind, actor, context, table, op, row, sha256(salt || values)]

so an export can show any entry's time and patients (to prove it is not one of the exported
ones) without its details, and an exported entry's details without its values. The salt is 16
random bytes per entry that never leave the database, so a value hash cannot be reversed by
trying candidate values. Older seals keep their format and are verified as they were made:
format 1 held the values, format 2 an unsalted hash of them (both before P1-T6b's review).

``upper`` is never above the oldest transaction still running, so no entry can appear in a
window after it is sealed: entries are final once sealed, however long a transaction ran.
Windows are contiguous from txid 0, so every entry belongs to exactly one seal. Removing,
changing or adding an entry, or a seal, breaks a recomputed hash. A database owner could
recompute the whole chain: the hourly fingerprints kept outside the database (anchor.py)
catch that. Entries written in the last moments are unsealed until the next run (every minute).
"""

import hashlib

from django.db import connection, transaction
from psycopg import sql

GENESIS = bytes(32)
# The beat job runs every minute: three missed runs before "the sealer is late"
SEALER_GRACE_SECONDS = 180
BATCH = 5000
# One canonical text per entry, built by the database (fixed formats, UTC). Kept here, not in
# a database function, so a changed function cannot change what is verified.
_AT = "to_char(e.at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')"
_HEAD = (
    "jsonb_build_array(e.id, e.txid::text, " + _AT + ", "
    "e.kind, e.actor, e.context, e.tbl, e.op, e.row_pk, to_jsonb(e.patients), "
)
_DATA = "convert_to(coalesce(e.data::text, 'null'), 'UTF8')"
INNER = (
    "jsonb_build_array(e.id, e.txid::text, e.kind, e.actor, e.context, e.tbl, e.op, "
    "e.row_pk, encode(sha256(coalesce(e.salt, ''::bytea) || "
    + _DATA
    + "), 'hex'))::text"
)
CANONICAL = {
    1: _HEAD + "e.data)::text",
    2: _HEAD + "encode(sha256(" + _DATA + "), 'hex'))::text",
    3: "jsonb_build_array(" + _AT + ", to_jsonb(e.patients), "
    "encode(sha256(convert_to(" + INNER + ", 'UTF8')), 'hex'))::text",
}
FORMAT = 3  # what new seals use
EXPORTABLE = frozenset({3})  # formats whose canonical text can leave the database
_TAG = {
    1: "",
    2: "v2|",
    3: "v3|",
}  # in the seal hash: a seal cannot be re-read as another


def leaf_sql(version):
    return "sha256(convert_to(" + CANONICAL[version] + ", 'UTF8'))"


def _window_hash(version):
    # Constant SQL; the table name goes in through sql.Identifier.
    return (
        "SELECT count(*), sha256(coalesce(string_agg("
        + leaf_sql(version)
        + ", ''::bytea ORDER BY e.txid, e.id), ''::bytea)) "
        "FROM {entry} e WHERE e.txid >= %s::xid8 AND e.txid < %s::xid8"
    )


def _names(schema):
    return {
        "entry": sql.Identifier(schema, "ihr_audit_entry"),
        "seal": sql.Identifier(schema, "ihr_audit_seal"),
    }


def seal_hash(prev, seq, lower, upper, count, entries_hash, version=FORMAT):
    head = f"{_TAG[version]}{seq}|{lower}|{upper}|{count}".encode()
    return hashlib.sha256(bytes(prev) + head + bytes(entries_hash)).digest()


def _upper_bound(cursor, names, lower, horizon):
    """The window's end: the horizon, or earlier so a seal covers about BATCH entries.
    Never a window with no entries: the loop below would stop there for good."""
    window = "FROM {entry} WHERE txid >= %s::xid8 AND txid < %s::xid8"
    cursor.execute(
        sql.SQL("SELECT min(txid)::text " + window).format(**names),
        [lower, horizon],
    )
    first = cursor.fetchone()[0]
    if first is None:
        return horizon
    cursor.execute(
        sql.SQL(
            "SELECT txid::text " + window + " ORDER BY txid, id OFFSET %s LIMIT 1"
        ).format(**names),
        [lower, horizon, BATCH],
    )
    row = cursor.fetchone()
    if row is None:
        return horizon  # fewer than BATCH entries: all of them
    if row[0] == first:
        return str(
            int(first) + 1
        )  # one transaction wrote more than BATCH: take it whole
    return row[0]


def seal_once(schema):
    """Seal everything that is final. Returns the number of entries sealed."""
    names = _names(schema)
    sealed = 0
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))", [f"ihr_audit_seal:{schema}"]
        )
        cursor.execute(
            sql.SQL(
                "SELECT seq, upper_txid::text, hash FROM {seal} ORDER BY seq DESC LIMIT 1"
            ).format(**names)
        )
        last = cursor.fetchone()
        seq, lower, prev = (
            (last[0], last[1], bytes(last[2])) if last else (0, "0", GENESIS)
        )
        cursor.execute("SELECT pg_snapshot_xmin(pg_current_snapshot())::text")
        horizon = cursor.fetchone()[0]
        while int(lower) < int(horizon):
            upper = _upper_bound(cursor, names, lower, horizon)
            cursor.execute(
                sql.SQL(_window_hash(FORMAT)).format(**names), [lower, upper]
            )
            count, entries_hash = cursor.fetchone()
            if count == 0:
                break
            seq += 1
            new = seal_hash(prev, seq, lower, upper, count, entries_hash, FORMAT)
            cursor.execute(
                sql.SQL(
                    "INSERT INTO {seal} (seq, lower_txid, upper_txid, entries, "
                    "entries_hash, prev_hash, hash, version) "
                    "VALUES (%s, %s::xid8, %s::xid8, %s, %s, %s, %s, %s)"
                ).format(**names),
                [seq, lower, upper, count, bytes(entries_hash), prev, new, FORMAT],
            )
            prev = new
            sealed += count
            lower = upper
    return sealed


def verify(schema, stale_minutes=10, as_of=None):
    """Recompute the whole chain. Returns (problems, summary)."""
    names = _names(schema)
    problems = []
    prev, expected_lower, seals, entries = GENESIS, "0", 0, 0
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT seq, lower_txid::text, upper_txid::text, entries, entries_hash, "
                "prev_hash, hash, version FROM {seal} ORDER BY seq"
            ).format(**names)
        )
        rows = cursor.fetchall()
        for seq, lower, upper, count, entries_hash, prev_hash, stored, made in rows:
            seals += 1
            where = f"seal {seq}"
            if seq != seals:
                problems.append(f"{where}: expected seal {seals} (a seal is missing)")
            if lower != expected_lower:
                problems.append(
                    f"{where}: starts at {lower}, expected {expected_lower}"
                )
            if bytes(prev_hash) != prev:
                problems.append(f"{where}: does not follow the previous seal")
            version = made if made in CANONICAL else FORMAT
            if made not in CANONICAL:
                problems.append(f"{where}: unknown format {made}")
            cursor.execute(
                sql.SQL(_window_hash(version)).format(**names), [lower, upper]
            )
            now_count, now_hash = cursor.fetchone()
            if now_count != count:
                problems.append(f"{where}: {count} entries sealed, {now_count} now")
            if bytes(now_hash) != bytes(entries_hash):
                problems.append(f"{where}: entries changed since sealing")
            recomputed = seal_hash(
                prev, seq, lower, upper, now_count, now_hash, version
            )
            if bytes(stored) != recomputed:
                problems.append(f"{where}: hash does not match")
            prev, expected_lower = bytes(stored), upper
            entries += count
        # The sealer is late if an entry it could seal (its transaction is over) has waited
        # stale_minutes and nothing was sealed in the last few runs. Entries held back by a
        # transaction still running somewhere in the cluster are reported, not a problem.
        # ``as_of`` (a restored copy, P1-T8c): "now" is the copy's last moment, not today.
        cursor.execute(
            sql.SQL(
                "SELECT count(*), min(at), "
                "count(*) FILTER (WHERE txid < pg_snapshot_xmin(pg_current_snapshot())), "
                "count(*) FILTER (WHERE txid >= pg_snapshot_xmin(pg_current_snapshot()) "
                "  AND at < coalesce(%s::timestamptz, now()) - make_interval(mins => %s)), "
                "min(at) FILTER (WHERE txid < pg_snapshot_xmin(pg_current_snapshot())) "
                "  < coalesce(%s::timestamptz, now()) - make_interval(mins => %s) "
                "  AND coalesce((SELECT max(sealed_at) FROM {seal}) "
                "      < coalesce(%s::timestamptz, now()) - make_interval(secs => %s), true) "
                "FROM {entry} WHERE txid >= %s::xid8"
            ).format(**names),
            [
                as_of,
                stale_minutes,
                as_of,
                stale_minutes,
                as_of,
                SEALER_GRACE_SECONDS,
                expected_lower,
            ],
        )
        unsealed, oldest, sealable, held_back, sealer_idle = cursor.fetchone()
    if sealable and sealer_idle:
        problems.append(
            f"{sealable} finished entries are not sealed and nothing was sealed for "
            f"{stale_minutes} minutes (is the sealer running?)"
        )
    summary = {
        "seals": seals,
        "sealed_entries": entries,
        "unsealed_entries": unsealed,
        "oldest_unsealed": oldest,
        "held_back": held_back,
        "head": prev.hex(),
    }
    return problems, summary
