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

Who writes what (P1-T19a, ADR-010 amendment): the app role may insert only an entry's data
columns, and a BEFORE INSERT trigger forces its ``txid``, ``at`` and ``salt`` (so it can
neither backdate an entry nor slip one into a sealed window). It never inserts a seal: seals are
made by ``ihr_audit_seal_next()`` in each hospital schema, a SECURITY DEFINER function owned by
the database owner, with no arguments and its search_path pinned, that computes every window,
hash and bound itself (``_FUNCTIONS`` below, built from the same canonical texts). Migration
0004 installed them once, from SQL frozen in it; ``ensure_sealing`` (every hospital schema's
post_migrate) keeps them, and the stamp trigger, this build's: a dropped, altered or older
function is replaced by any migrate (review C5). ``verify`` stays here and recomputes
everything independently, so a changed function is caught like any other tampering.

A sealing run that finds another one working on the schema returns at once (review S6:
``pg_try_advisory_xact_lock``): the app role may take the same advisory lock, and must never be
able to make every sealer wait. ``seal_once`` also bounds the call (``statement_timeout``).
"""

import hashlib

from django.db import connection, transaction
from ihr_platform import db_functions
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


# One sealing call never runs longer than this (S6): the beat job tries again next minute
SEAL_TIMEOUT = "30s"


def seal_once(schema, batch=None, version=None):
    """Seal everything that is final. Returns the number of entries sealed (0 also when
    another sealing run holds the schema).

    As the app does: ``ihr_audit_seal_next()`` (BATCH entries a seal, format FORMAT). With
    ``batch`` or ``version``, the owner-only ``ihr_audit_seal_run`` (tests: small batches,
    the older formats); the app role may not execute it."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f"SET LOCAL statement_timeout = '{SEAL_TIMEOUT}'")
        if batch is None and version is None:
            cursor.execute(
                sql.SQL("SELECT {}.ihr_audit_seal_next()").format(
                    sql.Identifier(schema)
                )
            )
        else:
            cursor.execute(
                sql.SQL("SELECT {}.ihr_audit_seal_run(%s, %s::smallint)").format(
                    sql.Identifier(schema)
                ),
                [
                    BATCH if batch is None else batch,
                    FORMAT if version is None else version,
                ],
            )
        return cursor.fetchone()[0]


# The sealing functions of one hospital schema (ensure_sealing; migration 0004 froze the first
# version). The windows are cut exactly as the Python sealer before them did: at most about
# BATCH entries, never inside a transaction, never an empty window, never above the horizon
# (the oldest transaction still running).
_FUNCTIONS = """
CREATE OR REPLACE FUNCTION {schema}.ihr_audit_seal_run(p_batch integer, p_version smallint)
RETURNS bigint LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
DECLARE
    v_seq bigint := 0;
    v_lower xid8 := '0';
    v_prev bytea := decode(repeat('00', 32), 'hex');
    v_horizon xid8;
    v_upper xid8;
    v_first xid8;
    v_nth xid8;
    v_count bigint;
    v_leaves bytea;
    v_hash bytea;
    v_sealed bigint := 0;
    v_last record;
BEGIN
    IF p_version IS NULL OR p_version NOT IN ({versions}) OR p_batch IS NULL OR p_batch < 1 THEN
        RAISE EXCEPTION 'ihr_audit_seal_run: unknown format or batch';
    END IF;
    -- Another run is sealing this schema (or someone holds its key): nothing to do now (S6)
    IF NOT pg_try_advisory_xact_lock(hashtext({lock})) THEN
        RETURN 0;
    END IF;
    SELECT s.seq, s.upper_txid, s.hash INTO v_last
        FROM {schema}.ihr_audit_seal s ORDER BY s.seq DESC LIMIT 1;
    IF FOUND THEN
        v_seq := v_last.seq;
        v_lower := v_last.upper_txid;
        v_prev := v_last.hash;
    END IF;
    v_horizon := pg_snapshot_xmin(pg_current_snapshot());
    WHILE v_lower < v_horizon LOOP
        SELECT min(e.txid) INTO v_first FROM {schema}.ihr_audit_entry e
            WHERE e.txid >= v_lower AND e.txid < v_horizon;
        IF v_first IS NULL THEN
            v_upper := v_horizon;
        ELSE
            SELECT e.txid INTO v_nth FROM {schema}.ihr_audit_entry e
                WHERE e.txid >= v_lower AND e.txid < v_horizon
                ORDER BY e.txid, e.id OFFSET p_batch LIMIT 1;
            IF NOT FOUND THEN
                v_upper := v_horizon;  -- fewer than a batch: all of them
            ELSIF v_nth = v_first THEN
                -- one transaction wrote more than a batch: take it whole
                v_upper := ((v_first::text)::numeric + 1)::text::xid8;
            ELSE
                v_upper := v_nth;
            END IF;
        END IF;
        IF p_version = 1 THEN
            SELECT count(*), sha256(coalesce(string_agg({leaf1}, ''::bytea
                ORDER BY e.txid, e.id), ''::bytea)) INTO v_count, v_leaves
                FROM {schema}.ihr_audit_entry e WHERE e.txid >= v_lower AND e.txid < v_upper;
        ELSIF p_version = 2 THEN
            SELECT count(*), sha256(coalesce(string_agg({leaf2}, ''::bytea
                ORDER BY e.txid, e.id), ''::bytea)) INTO v_count, v_leaves
                FROM {schema}.ihr_audit_entry e WHERE e.txid >= v_lower AND e.txid < v_upper;
        ELSE
            SELECT count(*), sha256(coalesce(string_agg({leaf3}, ''::bytea
                ORDER BY e.txid, e.id), ''::bytea)) INTO v_count, v_leaves
                FROM {schema}.ihr_audit_entry e WHERE e.txid >= v_lower AND e.txid < v_upper;
        END IF;
        EXIT WHEN v_count = 0;
        v_seq := v_seq + 1;
        v_hash := sha256(v_prev || convert_to(
            CASE p_version WHEN 1 THEN {tag1} WHEN 2 THEN {tag2} ELSE {tag3} END
            || v_seq::text || '|' || v_lower::text || '|' || v_upper::text || '|'
            || v_count::text, 'UTF8') || v_leaves);
        INSERT INTO {schema}.ihr_audit_seal (seq, lower_txid, upper_txid, entries,
            entries_hash, prev_hash, hash, version)
        VALUES (v_seq, v_lower, v_upper, v_count, v_leaves, v_prev, v_hash, p_version);
        v_prev := v_hash;
        v_sealed := v_sealed + v_count;
        v_lower := v_upper;
    END LOOP;
    RETURN v_sealed;
END $f$;

CREATE OR REPLACE FUNCTION {schema}.ihr_audit_seal_next() RETURNS bigint
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $f$ SELECT {schema}.ihr_audit_seal_run({batch}, {version}::smallint) $f$;

REVOKE ALL ON FUNCTION {schema}.ihr_audit_seal_run(integer, smallint) FROM PUBLIC;
REVOKE ALL ON FUNCTION {schema}.ihr_audit_seal_next() FROM PUBLIC;
"""


def functions_sql(schema):
    """The sealing functions of ``schema``, as SQL (ensure_sealing). EXECUTE on
    ihr_audit_seal_next goes to the app role only (ihr_platform/db_roles.py)."""
    return sql.SQL(_FUNCTIONS).format(
        schema=sql.Identifier(schema),
        lock=sql.Literal(f"ihr_audit_seal:{schema}"),
        versions=sql.SQL(", ").join(sql.Literal(v) for v in sorted(CANONICAL)),
        batch=sql.Literal(BATCH),
        version=sql.Literal(FORMAT),
        leaf1=sql.SQL(leaf_sql(1)),
        leaf2=sql.SQL(leaf_sql(2)),
        leaf3=sql.SQL(leaf_sql(3)),
        tag1=sql.Literal(_TAG[1]),
        tag2=sql.Literal(_TAG[2]),
        tag3=sql.Literal(_TAG[3]),
    )


# The trigger that forces an entry's txid, time and salt (S2)
STAMP_FUNCTION = """
CREATE OR REPLACE FUNCTION {schema}.ihr_audit_stamp() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $f$
BEGIN
    NEW.txid := pg_current_xact_id();
    NEW.at := clock_timestamp();
    NEW.salt := uuid_send(gen_random_uuid());
    RETURN NEW;
END $f$;
"""
STAMP_TRIGGER = (
    "CREATE OR REPLACE TRIGGER ihr_audit_stamp BEFORE INSERT ON {schema}.ihr_audit_entry "
    "FOR EACH ROW EXECUTE FUNCTION {schema}.ihr_audit_stamp()"
)
SEALING = ("ihr_audit_seal_next", "ihr_audit_seal_run", "ihr_audit_stamp")
# This build's: the SQL and every constant baked into it
SEALING_VERSION = db_functions.version(
    _FUNCTIONS,
    STAMP_FUNCTION,
    STAMP_TRIGGER,
    repr(sorted(CANONICAL.items())),
    repr(sorted(_TAG.items())),
    str(BATCH),
    str(FORMAT),
)
# Installed by ihr_audit 0004 first: before it, a schema has no sealing at all
SEALING_MIGRATION = ("ihr_audit", "0004_owner_writes_the_chain")


def _stamp_triggers(cursor, schemas):
    """{schema: (enabled, function name)} of the stamp trigger on the entries."""
    cursor.execute(
        "SELECT n.nspname, t.tgenabled, p.proname FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_proc p ON p.oid = t.tgfoid "
        "WHERE n.nspname = ANY(%s) AND c.relname = 'ihr_audit_entry' "
        "AND t.tgname = 'ihr_audit_stamp'",
        [list(schemas)],
    )
    return {schema: (enabled, name) for schema, enabled, name in cursor.fetchall()}


def _stamp_trigger(cursor, schema):
    """(enabled, function name) of the stamp trigger on the entries, or None."""
    return _stamp_triggers(cursor, [schema]).get(schema)


def sealing_problems(cursor, schema):
    """Why this schema's sealing functions or stamp trigger are not this build's."""
    return _judge(
        schema,
        db_functions.held(cursor, schema, SEALING),
        _stamp_trigger(cursor, schema),
    )


def sealing_problems_by_schema(cursor, schemas):
    """What the run-time check (ihr_platform/db_roles.py) gates a hospital for, for every
    schema in ``schemas`` whose sealing is installed (any of its functions is there), in two
    queries: {schema: [problem]}, only schemas with one. A sealing function missing or changed
    since it was installed (its fingerprint no longer matches the anchor's stamp), or the
    stamp trigger missing or off. An older build's functions, unchanged as installed, are not:
    the schema's next migrate brings them to this build's (``sealing_problems``), and a release
    that changes their SQL without a migration must not stop every hospital. A schema with none
    of them is older than ihr_audit 0004 (not served: the release gate) or lost them all (the
    app's sealing then fails, and SealerLate fires)."""
    schemas = list(schemas)
    held = db_functions.held_in(cursor, schemas, SEALING)
    triggers = _stamp_triggers(cursor, schemas)
    found = {}
    for schema in schemas:
        if schema not in held:
            continue
        functions = held[schema]
        problems = []
        if missing := sorted(set(SEALING) - set(functions)):
            problems.append(
                f"{schema}: sealing functions {', '.join(missing)} missing (migrate puts "
                "them back)"
            )
        elif not db_functions.unchanged(functions, SEALING, "ihr_audit_seal_next"):
            problems.append(
                f"{schema}: sealing functions changed since they were installed (migrate "
                "puts them back)"
            )
        problems += _trigger_problems(schema, triggers.get(schema))
        if problems:
            found[schema] = problems
    return found


def _judge(schema, functions, trigger):
    problems = []
    if not db_functions.intact(
        functions, SEALING, "ihr_audit_seal_next", SEALING_VERSION
    ):
        missing = sorted(set(SEALING) - set(functions))
        problems.append(
            f"{schema}: sealing functions "
            + (
                f"{', '.join(missing)} missing"
                if missing
                else "changed or not this build's"
            )
            + " (migrate puts them back)"
        )
    return problems + _trigger_problems(schema, trigger)


def _trigger_problems(schema, trigger):
    problems = []
    if trigger is None or trigger[1] != "ihr_audit_stamp":
        problems.append(f"{schema}.ihr_audit_entry: trigger ihr_audit_stamp is missing")
    elif trigger[0] not in ("O", "A"):
        problems.append(
            f"{schema}.ihr_audit_entry: trigger ihr_audit_stamp is not enabled"
        )
    return problems


def _sealing_installed(cursor, schema):
    cursor.execute(
        sql.SQL(
            "SELECT to_regclass({}) IS NOT NULL AND EXISTS (SELECT 1 FROM {} "
            "WHERE app = %s AND name = %s)"
        ).format(
            sql.Literal(f'"{schema}".ihr_audit_seal'),
            sql.Identifier(schema, "django_migrations"),
        ),
        list(SEALING_MIGRATION),
    )
    return cursor.fetchone()[0]


def ensure_sealing(conn, schema):
    """Bring the sealing functions and the stamp trigger of ``schema`` to this build's
    (post_migrate, as the owner, before the grants: db_roles gives EXECUTE back to the app).
    Changes nothing when they are current. True if it changed something."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", [f'"{schema}".django_migrations'])
        if cursor.fetchone()[0] is None or not _sealing_installed(cursor, schema):
            return False
        if not sealing_problems(cursor, schema):
            return False
    ident = sql.Identifier(schema)
    with transaction.atomic(using=conn.alias), conn.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute(functions_sql(schema))
        cursor.execute(sql.SQL(STAMP_FUNCTION).format(schema=ident))
        trigger = _stamp_trigger(cursor, schema)
        if trigger is None or trigger[1] != "ihr_audit_stamp":
            cursor.execute(sql.SQL(STAMP_TRIGGER).format(schema=ident))
        if trigger is None or trigger[0] not in ("O", "A"):
            cursor.execute(
                sql.SQL("ALTER TABLE {} ENABLE TRIGGER ihr_audit_stamp").format(
                    sql.Identifier(schema, "ihr_audit_entry")
                )
            )
        db_functions.write_stamp(
            cursor, schema, "ihr_audit_seal_next", "", SEALING, SEALING_VERSION
        )
    return True


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
