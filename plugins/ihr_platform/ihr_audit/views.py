# ruff: noqa: S608 (SQL is built from constants; values are always parameters)
"""The audit trail viewer for hospital-wide admins (P1-T6b, ADR-010).

    GET /api/ihr/audit/entries/          newest first; who, when, what record, which patients,
                                         which fields changed (never the values)
    GET /api/ihr/audit/entries/<id>/     one entry with its old and new values
    GET /api/ihr/audit/export/           ?from=&to=[&patient=][&file=json]: CSV, or JSON Lines with the hash-chain proof (export.py)
    GET /api/ihr/audit/status/           chain head and trigger coverage
    GET /api/ihr/audit/verifier.py       the auditor's checking script for JSON exports

Only staff with an admin role on the whole hospital may use these (owner, 2026-11-08). Every
call is itself recorded as a ``request`` entry with purpose "audit" (middleware.py), with the
patients it showed: patient names are loaded as model instances so they count as shown.
"""

import json
import re
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path

from care.emr.models import Patient
from care.users.models import User
from django.db import connection
from django.http import FileResponse
from ihr_identity.staff_access import hospital_wide_admin
from psycopg import sql
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from ihr_audit import export, reads
from ihr_audit.context import mask_query
from ihr_audit.export import IST, OP_NAMES, ist
from ihr_audit.install import coverage

PAGE, MAX_PAGE = 50, 200
PATIENTS_PER_ROW = 20  # in the list; the entry itself shows all it holds
KINDS = frozenset(("write", "truncate", "request"))
OPS = {name: letter for letter, name in OP_NAMES.items()}
TABLE = re.compile(r"^[a-z0-9_]{1,63}$")
COLUMNS = "id, at, kind, actor, context, tbl, op, row_pk, patients, data"


class IsHospitalAdmin(BasePermission):
    message = "Only a hospital-wide admin can open the audit trail."

    def has_permission(self, request, view):
        user = request.user
        return (
            isinstance(user, User)
            and user.is_authenticated
            and not user.is_service_account
            and hospital_wide_admin(user)
        )


MAX_ID = 2**63 - 1


def _day(value, name):
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()  # noqa: DTZ007 (a calendar day)
    except ValueError:
        raise ValidationError({name: "Use YYYY-MM-DD."}) from None
    if not 2000 <= day.year <= 9000:  # noqa: PLR2004
        raise ValidationError({name: "Not a sensible date."})
    return day


def day_range(params, required=False):
    """``from``/``to`` as IST calendar days, both included: (start, end) datetimes, end
    exclusive. Missing ones are None unless required."""
    start = end = None
    if params.get("from"):
        start = datetime.combine(_day(params["from"], "from"), time(), IST)
    if params.get("to"):
        end = datetime.combine(
            _day(params["to"], "to") + timedelta(days=1), time(), IST
        )
    if required and not (start and end):
        raise ValidationError("Both from and to are required (YYYY-MM-DD).")
    if start and end and end <= start:
        raise ValidationError({"to": "Must not be before from."})
    return start, end


def patient_id(params):
    """The internal id of the ``patient`` filter (an external id), None if not given,
    0 if no such patient."""
    value = params.get("patient")
    if not value:
        return None
    try:
        external = uuid.UUID(value)
    except ValueError:
        raise ValidationError({"patient": "Not a patient id."}) from None
    found = Patient.objects.filter(external_id=external).values_list("id", flat=True)
    pid = found.first() or 0
    if pid:
        reads.note_patients(
            [pid]
        )  # looking up a patient's trail counts as viewing them
    return pid


def _where(params):
    clauses, args = [], []
    start, end = day_range(params)
    if start:
        clauses.append("at >= %s")
        args.append(start)
    if end:
        clauses.append("at < %s")
        args.append(end)
    pid = patient_id(params)
    if pid is not None:
        clauses.append("patients @> ARRAY[%s]::bigint[]")
        args.append(pid)
    if kind := params.get("kind"):
        if kind not in KINDS:
            raise ValidationError({"kind": f"One of {', '.join(sorted(KINDS))}."})
        clauses.append("kind = %s")
        args.append(kind)
    if op := params.get("op"):
        if op not in OPS:
            raise ValidationError({"op": f"One of {', '.join(sorted(OPS))}."})
        clauses.append("op = %s")
        args.append(OPS[op])
    if table := params.get("table"):
        if not TABLE.match(table):
            raise ValidationError({"table": "Not a table name."})
        clauses.append("tbl = %s")
        args.append(table)
    if actor := params.get("actor"):
        escaped = (
            actor[:200].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        clauses.append("actor LIKE %s")
        args.append(escaped + "%")
    if before := params.get("before"):
        if not before.isdigit() or int(before) > MAX_ID:
            raise ValidationError({"before": "An entry id."})
        clauses.append("id < %s")
        args.append(int(before))
    return " AND ".join(clauses) or "true", args


def _rows(query, args):
    with connection.cursor() as cursor:
        cursor.execute(query, args)
        names = [c.name for c in cursor.description]
        rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    for row in rows:  # Django's cursor hands jsonb back as text
        for column in ("context", "data"):
            if isinstance(row[column], str):
                row[column] = json.loads(row[column])
    return rows


def _patients(ids):
    """{id: {"id": external id, "name": name}}; loading them counts them as shown."""
    found = Patient.objects.filter(id__in=set(ids)).only("id", "external_id", "name")
    return {p.id: {"id": str(p.external_id), "name": p.name} for p in found}


def summary(row, patients, limit=PATIENTS_PER_ROW):
    ctx, data = row["context"] or {}, row["data"] or {}
    item = {
        "id": row["id"],
        "at": row["at"].isoformat(),
        "at_ist": ist(row["at"]),
        "kind": row["kind"],
        "actor": row["actor"],
        "purpose": ctx.get("purpose"),
        "via": ctx.get("via"),
        "ip": ctx.get("ip"),
        "method": ctx.get("method"),
        "path": ctx.get("path"),
        "query": mask_query(ctx.get("query")),
        "task": ctx.get("task"),
        "request_id": ctx.get("rid"),
        "device_tz": ctx.get("tz"),  # set only when the device was not on IST (P1-T7b)
        "table": row["tbl"],
        "op": OP_NAMES.get(row["op"]),
        "record": row["row_pk"],
        "patients": [
            patients.get(p, {"id": None, "name": None})
            for p in (row["patients"] or [])[:limit]
        ],
        "patients_total": data.get("patients_total", len(row["patients"] or []))
        if row["kind"] == "request"
        else len(row["patients"] or []),
    }
    if row["kind"] == "request":
        item["status"] = data.get("status")
    elif row["op"] == "U":
        item["fields"] = sorted(data)
    return item


class EntriesView(APIView):
    permission_classes = (IsHospitalAdmin,)

    def get(self, request):
        where, args = _where(request.query_params)
        try:
            size = min(max(int(request.query_params.get("size", PAGE)), 1), MAX_PAGE)
        except ValueError:
            raise ValidationError({"size": "A number."}) from None
        rows = _rows(
            sql.SQL(
                "SELECT "
                + COLUMNS
                + " FROM {} WHERE "
                + where
                + " ORDER BY id DESC LIMIT %s"
            ).format(sql.Identifier(connection.schema_name, "ihr_audit_entry")),
            [*args, size + 1],
        )
        more = len(rows) > size
        rows = rows[:size]
        patients = _patients(
            p for row in rows for p in (row["patients"] or [])[:PATIENTS_PER_ROW]
        )
        return Response(
            {
                "results": [summary(row, patients) for row in rows],
                "next_before": rows[-1]["id"] if more else None,
            }
        )


class EntryView(APIView):
    permission_classes = (IsHospitalAdmin,)

    def get(self, request, pk):
        if pk > MAX_ID:
            return Response({"detail": "No such entry."}, status=404)
        rows = _rows(
            sql.SQL("SELECT " + COLUMNS + " FROM {} WHERE id = %s").format(
                sql.Identifier(connection.schema_name, "ihr_audit_entry")
            ),
            [pk],
        )
        if not rows:
            return Response({"detail": "No such entry."}, status=404)
        row = rows[0]
        patients = _patients(row["patients"] or [])
        item = summary(row, patients, limit=None)
        item["context"] = {
            **(row["context"] or {}),
            "query": mask_query((row["context"] or {}).get("query")),
        }
        item["values"] = row["data"]  # old and new values; identifiers already masked
        return Response(item)


class StatusView(APIView):
    permission_classes = (IsHospitalAdmin,)

    def get(self, request):
        schema = connection.schema_name
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "SELECT seq, encode(hash, 'hex'), sealed_at, upper_txid FROM {} "
                    "ORDER BY seq DESC LIMIT 1"
                ).format(sql.Identifier(schema, "ihr_audit_seal"))
            )
            last = cursor.fetchone()
            cursor.execute(
                sql.SQL(
                    "SELECT count(*), min(at) FROM {} WHERE txid >= %s::xid8"
                ).format(sql.Identifier(schema, "ihr_audit_entry")),
                [last[3] if last else "0"],
            )
            unsealed, oldest = cursor.fetchone()
        problems = coverage(connection, schema)
        return Response(
            {
                "seals": last[0] if last else 0,
                "head": last[1] if last else None,
                "last_sealed_at": last[2] and ist(last[2]) if last else None,
                "unsealed_entries": unsealed,
                "oldest_unsealed_at": oldest and ist(oldest),
                "triggers_ok": not problems,
                "trigger_problems": problems[:20],
            }
        )


class ExportView(APIView):
    permission_classes = (IsHospitalAdmin,)

    def get(self, request):
        params = request.query_params
        start, end = day_range(params, required=True)
        if end - start > timedelta(days=export.MAX_DAYS):
            msg = f"At most {export.MAX_DAYS} days per export."
            raise ValidationError(msg)
        # Not "format": DRF reads that one to choose a renderer
        kind = params.get("file", "csv")
        if kind not in ("csv", "json"):
            raise ValidationError({"file": "csv or json."})
        pid = patient_id(params)
        if pid == 0:
            raise ValidationError({"patient": "No such patient."})
        job = export.Export(connection.schema_name, connection.tenant, start, end, pid)
        job.note_patients(proof=kind == "json")  # before the response is streamed
        return job.response(kind, params.get("from"), params.get("to"))


class VerifierView(APIView):
    """The stdlib-only script an outside auditor runs on a JSON export."""

    permission_classes = (IsHospitalAdmin,)

    def get(self, request):
        return FileResponse(
            Path(__file__).with_name("verify_export.py").open("rb"),
            as_attachment=True,
            filename="verify_export.py",
            content_type="text/x-python",
        )
