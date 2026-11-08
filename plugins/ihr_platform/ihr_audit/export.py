# ruff: noqa: S608 (SQL is built from constants; values are always parameters)
"""Audit trail exports for a date range, at most 31 days (P1-T6b, owner: CSV + JSON with proof).

CSV is for people: one row per entry, IST times, which fields changed but never the values.

JSON Lines is for an outside auditor, checked with ``verify_export.py`` (stdlib only):
  - a ``seal`` line for every seal whose window lies between the first and last exported
    entry, then a line for every entry of that window in sealing order: exported ones
    (``entry``) with their details but never their values, the others (``leaf``) with only
    their time, patient ids and a hash of their details. The auditor recomputes each window's
    hash, and checks that no ``leaf`` is in the range (or names the patient): no entry of
    those windows can be left out quietly;
  - ``seal`` headers on to the first outside fingerprint (anchor.py) that covers them, and
    that fingerprint (``anchor``), so the chain is tied to something the database owner
    cannot change;
  - exported entries not sealed yet, marked so.
What is not proven: an entry outside those windows whose time is in the range (a transaction
that stayed open across the range's edge). Seals made before P1-T6b's review (formats 1 and 2)
give only hashes. A patient's export carries every entry of the windows it spans as ``leaf``
lines (up to the whole range's trail), the price of proving nothing was left out.

The patients in the range are counted as shown by the export request before streaming starts.
"""

import csv
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from care.emr.models import Patient
from django.db import connection, transaction
from django.http import StreamingHttpResponse
from psycopg import sql

from ihr_audit import anchor, reads
from ihr_audit.context import mask_query
from ihr_audit.seal import CANONICAL, EXPORTABLE, INNER, leaf_sql
from ihr_audit.seal import FORMAT as FORMAT_NOW

MAX_DAYS = 31
CHUNK = 2000
FORMAT = "ihr-audit-export/1"
CSV_HEADER = (
    "entry_id",
    "time_ist",
    "kind",
    "actor",
    "purpose",
    "via",
    "ip",
    "method",
    "path",
    "query",
    "task",
    "device_tz",  # IndieHR P1-T7b: zone of a non-IST device
    "status",
    "table",
    "operation",
    "record_id",
    "fields_changed",
    "patients_total",
    "patient_ids",
    "patient_names",
)
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
IST = ZoneInfo("Asia/Kolkata")
# Operations are stored as one letter (install.py)
OP_NAMES = {"I": "INSERT", "U": "UPDATE", "D": "DELETE", "T": "TRUNCATE"}


def ist(moment):
    return moment.astimezone(IST).strftime("%d-%m-%Y %H:%M:%S")


def cell(value):
    """Text a spreadsheet will never run as a formula."""
    if value is None:
        return ""
    text = str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


class _Echo:
    def write(self, value):
        return value


class Export:
    def __init__(self, schema, hospital, start, end, patient=None):
        self.hospital = hospital
        self.entry = sql.Identifier(schema, "ihr_audit_entry")
        self.seal = sql.Identifier(schema, "ihr_audit_seal")
        self.start, self.end, self.patient = start, end, patient
        self.match = self.matching()
        self.args = [start, end] + ([patient] if patient else [])
        self.names = {}
        self.window = None  # (first seq, last seq, lower, upper) chosen when counting

    def matching(self, alias=""):
        """SQL for "this entry is exported" (the range, and the patient if one is given)."""
        clause = f"{alias}at >= %s AND {alias}at < %s"
        if self.patient:
            clause += f" AND {alias}patients @> ARRAY[%s]::bigint[]"
        return clause

    def _query(self, cursor, text, args=()):
        cursor.execute(sql.SQL(text).format(entry=self.entry, seal=self.seal), args)

    def note_patients(self, proof=False):
        """Count as shown every patient the export will carry, before it streams (the
        request entry is written when the view returns). The range ends now, so nothing
        written later is exported without having been counted. A JSON proof also carries the
        patient ids of the other entries in its windows."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            self.end = min(self.end, cursor.fetchone()[0])
            self.args[1] = self.end
            self._query(
                cursor,
                "SELECT DISTINCT unnest(patients) FROM {entry} WHERE " + self.match,
                self.args,
            )
            ids = {row[0] for row in cursor.fetchall() if row[0] is not None}
            carried = ids
            if proof:
                self.window = self._spanned(cursor)
                self._query(
                    cursor,
                    "SELECT DISTINCT unnest(patients) FROM {entry} "
                    "WHERE (at >= %s AND at < %s) OR (txid >= %s::xid8 AND txid < %s::xid8)",
                    [
                        self.start,
                        self.end,
                        *(self.window[2:] if self.window else ("0", "0")),
                    ],
                )
                carried = {row[0] for row in cursor.fetchall() if row[0] is not None}
        for pid, external, name in Patient.objects.filter(id__in=ids).values_list(
            "id", "external_id", "name"
        ):
            self.names[pid] = (str(external), name)
        reads.note_patients(ids | carried)

    def _spanned(self, cursor):
        """The seals holding the range's entries (by time, whatever the patient), and one
        more on each side: (first seq, last seq, their lower and upper txid), or None."""
        self._query(
            cursor,
            "SELECT min(txid)::text, max(txid)::text FROM {entry} "
            "WHERE at >= %s AND at < %s",
            [self.start, self.end],
        )
        low, high = cursor.fetchone()
        if low is None:
            return None
        self._query(
            cursor,
            "WITH spanned AS (SELECT min(seq) AS first, max(seq) AS last FROM {seal} "
            "  WHERE upper_txid > %s::xid8 AND lower_txid <= %s::xid8) "
            "SELECT min(seq), max(seq), min(lower_txid)::text, max(upper_txid)::text "
            "FROM {seal}, spanned "
            "WHERE seq >= spanned.first - 1 AND seq <= spanned.last + 1",
            [low, high],
        )
        found = cursor.fetchone()
        return found if found[0] is not None else None

    def response(self, kind, first_day, last_day):
        name = f"audit-{self.hospital.slug}-{first_day}-to-{last_day}"
        if kind == "csv":
            response = StreamingHttpResponse(self.csv(), content_type="text/csv")
            name += ".csv"
        else:
            response = StreamingHttpResponse(
                self.jsonl(), content_type="application/x-ndjson"
            )
            name += ".jsonl"
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        response["Cache-Control"] = "no-store"
        return response

    # CSV

    def csv(self):
        out = csv.writer(_Echo())
        yield "﻿" + out.writerow(CSV_HEADER)  # BOM: Excel reads UTF-8 (Hindi names)
        last = 0
        with _snapshot() as cursor:
            while True:
                self._query(
                    cursor,
                    "SELECT id, at, kind, actor, context, tbl, op, row_pk, patients, "
                    "CASE WHEN op = 'U' THEN (SELECT string_agg(k, ' ' ORDER BY k) "
                    "  FROM jsonb_object_keys(data) k) END, "
                    "CASE WHEN kind = 'request' THEN data->>'status' END, "
                    "CASE WHEN kind = 'request' THEN data->>'patients_total' END "
                    "FROM {entry} WHERE " + self.match + " AND id > %s "
                    "ORDER BY id LIMIT %s",
                    [*self.args, last, CHUNK],
                )
                rows = cursor.fetchall()
                if not rows:
                    return
                for (
                    pk,
                    at,
                    kind,
                    actor,
                    ctx,
                    tbl,
                    op,
                    row_pk,
                    patients,
                    fields,
                    status,
                    total,
                ) in rows:
                    context = json.loads(ctx) if isinstance(ctx, str) else (ctx or {})
                    known = [self.names.get(p, ("", "")) for p in patients or []]
                    yield out.writerow(
                        [
                            cell(v)
                            for v in (
                                pk,
                                ist(at),
                                kind,
                                actor,
                                context.get("purpose"),
                                context.get("via"),
                                context.get("ip"),
                                context.get("method"),
                                context.get("path"),
                                mask_query(context.get("query")),
                                context.get("task"),
                                context.get("tz"),
                                status,
                                tbl,
                                OP_NAMES.get(op),
                                row_pk,
                                fields,
                                total or len(patients or []),
                                "; ".join(k[0] for k in known),
                                "; ".join(k[1] for k in known),
                            )
                        ]
                    )
                last = rows[-1][0]

    # JSON Lines with proof

    def jsonl(self):
        with _snapshot() as cursor:
            yield from self._jsonl(cursor)

    def _jsonl(self, cursor):
        exported = 0
        yield _line(
            {
                "type": "export",
                "format": FORMAT,
                "hospital": {
                    "uuid": str(self.hospital.uuid),
                    "slug": self.hospital.slug,
                },
                "from": self.start.isoformat(),
                "to_before": self.end.isoformat(),
                "patient": self.patient,  # internal id; null for every patient
                "patient_id": self.patient and self.names.get(self.patient, (None,))[0],
                "generated_at": datetime.now(UTC).isoformat(),
            }
        )
        # The windows holding the range's entries, with one more on each side, all in full
        # (as counted before streaming): the auditor sees where the range starts and ends.
        window = self.window if self.window is not None else self._spanned(cursor)
        self._query(
            cursor,
            "SELECT coalesce(max(seq), 0), coalesce(max(upper_txid), '0'::xid8)::text "
            "FROM {seal}",
        )
        newest, sealed_to = cursor.fetchone()
        seals = []
        if window is not None:
            self._query(
                cursor,
                "SELECT " + SEAL_COLUMNS + " FROM {seal} "
                "WHERE seq >= %s AND seq <= %s ORDER BY seq",
                window[:2],
            )
            seals = cursor.fetchall()
        for header in seals:
            yield _line(_seal(header, leaves=True))
            for item in self._window(cursor, header):
                exported += item["type"] == "entry"
                yield _line(item)
        # Seal headers on to the first fingerprint written after the last full window
        proof = self._anchor(seals, newest)
        if seals and proof.get("seq", 0) > seals[-1][0]:
            self._query(
                cursor,
                "SELECT " + SEAL_COLUMNS + " FROM {seal} "
                "WHERE seq > %s AND seq <= %s ORDER BY seq",
                [seals[-1][0], proof["seq"]],
            )
            for header in cursor.fetchall():
                yield _line(_seal(header, leaves=False))
        # Exported entries not sealed yet
        for item in self._unsealed(cursor, sealed_to):
            exported += 1
            yield _line(item)
        yield _line(proof)
        yield _line({"type": "end", "entries": exported})

    def _patients(self, ids):
        return [self.names.get(p, (None,))[0] for p in ids or []]

    def _lines(self, cursor, query, args, seq, version):
        """Every entry the query returns, in sealing order, as export lines."""
        after = ("0", 0)
        while True:
            self._query(cursor, query, [*args, *after, CHUNK])
            rows = cursor.fetchall()
            if not rows:
                return
            for _txid, _pk, leaf, chosen, outer, inner, patients in rows:
                item = {
                    "type": "entry" if chosen else "leaf",
                    "seal": seq,
                    "leaf": leaf,
                }
                if version in EXPORTABLE:
                    item["canonical"] = outer  # time, patients, hash of the details
                if chosen:
                    item["inner"] = inner if version in EXPORTABLE else None
                    item["patients"] = self._patients(patients)
                yield item
            after = (rows[-1][0], rows[-1][1])

    def _select(self, version):
        exportable = version in EXPORTABLE
        return (
            "SELECT e.txid::text, e.id, encode(" + leaf_sql(version) + ", 'hex'), "
            "("
            + self.matching("e.")
            + "), "
            + (CANONICAL[version] if exportable else "NULL::text")
            + ", "
            + (INNER if exportable else "NULL::text")
            + ", e.patients FROM {entry} e "
        )

    def _window(self, cursor, header):
        seq, version, lower, upper = header[:4]
        return self._lines(
            cursor,
            self._select(version) + "WHERE e.txid >= %s::xid8 AND e.txid < %s::xid8 "
            "AND (e.txid, e.id) > (%s::xid8, %s) ORDER BY e.txid, e.id LIMIT %s",
            [*self.args, lower, upper],
            seq,
            version,
        )

    def _unsealed(self, cursor, sealed_to):
        return self._lines(
            cursor,
            self._select(FORMAT_NOW)
            + "WHERE "
            + self.matching("e.")
            + " AND e.txid >= %s::xid8 "
            "AND (e.txid, e.id) > (%s::xid8, %s) ORDER BY e.txid, e.id LIMIT %s",
            [*self.args, *self.args, sealed_to],
            None,
            FORMAT_NOW,
        )

    def _anchor(self, seals, newest):
        """The first fingerprint covering the last full window: the earliest one ties the
        chain soonest after sealing. Never one naming seals this snapshot cannot show."""
        if not seals:
            return {
                "type": "anchor",
                "missing": "no sealed entries in this range (that it is empty is not proven)",
            }
        try:
            found = anchor.find_covering(
                str(self.hospital.uuid), seals[-1][0], newest, seals[-1][8]
            )
        except anchor.NotConfiguredError:
            return {"type": "anchor", "missing": "fingerprints are not set up here"}
        except Exception as exc:  # the export still proves the chain itself
            return {"type": "anchor", "missing": f"fingerprints unreadable: {exc}"}
        if found is None:
            return {
                "type": "anchor",
                "missing": "not fingerprinted yet (hourly); export again later",
            }
        seq, digest, _slug = found.hospitals[str(self.hospital.uuid)]
        return {
            "type": "anchor",
            "key": found.key,
            "version": found.version,
            "stored_at": found.stored.isoformat() if found.stored else None,
            "written_at": found.written_at.isoformat(),
            "hospital": str(self.hospital.uuid),
            "seq": seq,
            "hash": digest,
        }


SEAL_COLUMNS = (
    "seq, version, lower_txid::text, upper_txid::text, entries, "
    "encode(entries_hash, 'hex'), encode(prev_hash, 'hex'), encode(hash, 'hex'), sealed_at"
)


@contextmanager
def _snapshot():
    """One consistent view of the trail for the whole export: a seal made while it streams
    must not show its entries both as sealed and as not sealed yet."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        yield cursor


def _seal(header, leaves):
    seq, version, lower, upper, count, entries_hash, prev_hash, digest, at = header
    return {
        "type": "seal",
        "seq": seq,
        "version": version,
        "lower": lower,
        "upper": upper,
        "entries": count,
        "entries_hash": entries_hash,
        "prev_hash": prev_hash,
        "hash": digest,
        "sealed_at": at.isoformat(),
        "leaves": leaves,
    }


def _line(item):
    return json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
