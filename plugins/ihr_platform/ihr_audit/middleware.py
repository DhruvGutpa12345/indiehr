"""One 'request' entry per hospital request that showed patients or tried to change anything.

Runs outside CARE's per-request transaction (ATOMIC_REQUESTS), so the entry is kept when the
view fails or is refused: refused attempts are part of the trail. If the entry cannot be
written the request fails (no trail, no access). Row changes themselves are recorded by the
database triggers (install.py), in the view's own transaction.
"""

import json
import re

from django.db import connection
from psycopg import sql

from ihr_audit import context, reads

SAFE_METHODS = frozenset(("GET", "HEAD", "OPTIONS"))
# Purpose is inferred from the screen's API until staff are asked (owner: inferred now).
PURPOSES = (
    (re.compile(r"/(invoice|charge_item|account|payment_reconciliation)"), "billing"),
    (
        re.compile(r"^/api/(v1/(users|role|organization|facility_organization)|ihr/)"),
        "admin",
    ),
)


def purpose_of(path):
    for pattern, purpose in PURPOSES:
        if pattern.search(path):
            return purpose
    return "care"


class AuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if connection.schema_name == "public":
            return self.get_response(request)
        ctx = context.request_context(request, purpose_of(request.path))
        context.begin_request(ctx)
        token = reads.start()
        status = 500
        try:
            response = self.get_response(request)
            status = response.status_code
        finally:
            try:
                context.resync()  # the view's rollback may have undone the actor
                patients, total = reads.finish(token)
                if patients or request.method not in SAFE_METHODS:
                    data = {"status": status}
                    if total > len(patients):
                        data["patients_total"] = total
                    _record(
                        context.current_actor() or "anonymous",
                        ctx,
                        patients,
                        data,
                    )
            finally:
                context.end()
        return response


def _record(actor, ctx, patients, data):
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "INSERT INTO {} (kind, actor, context, patients, data) "
                "VALUES ('request', %s, %s, %s, %s)"
            ).format(sql.Identifier(connection.schema_name, "ihr_audit_entry")),
            [
                actor,
                json.dumps(ctx, default=str),
                patients,
                json.dumps(data),
            ],
        )
