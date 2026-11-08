"""Who is acting, handed to the row triggers through two database session settings.

    ihr.audit_ctx    JSON: request id, client address, method, path, purpose, how (web, task),
                     and the device's time zone when it is not IST (``tz``, P1-T7b)
    ihr.audit_actor  the signed-in actor, set as soon as the person is known

Both are session-level and are overwritten at the start of every request and task and cleared
at its end, so a pooled connection never carries one request's actor into the next. (A pooler
in transaction mode would break this: P1-T8 must use session pooling or none.) The values are
also kept per thread here and put again on any connection opened later in the same request or
task (Celery closes connections right after a task starts). Writes made outside any request or
task are recorded as ``system:<application_name>`` (the process name, settings/tenancy.py).
"""

import json
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import parse_qsl, urlencode

from django.db import connection

MAX_PATH = 300
MAX_QUERY = 500
_EMPTY = ("", "")
_current = ContextVar("ihr_audit_current", default=_EMPTY)  # (ctx JSON, actor)
# Values saved while a task runs inside a request (eager tasks), restored after it
_outer = ContextVar("ihr_audit_outer", default=())


def _apply(conn, values):
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('ihr.audit_ctx', %s, false), "
            "set_config('ihr.audit_actor', %s, false)",
            list(values),
        )


def _set(ctx, actor):
    values = (json.dumps(ctx, separators=(",", ":"), default=str) if ctx else "", actor)
    _current.set(values)
    _apply(connection, values)


def reapply(sender=None, connection=None, **kwargs):
    """``connection_created``: a new connection gets this thread's current values."""
    values = _current.get()
    if values != _EMPTY and connection is not None:
        _apply(connection, values)


def actor_of(user):
    """A short, stable name for whoever made the request."""
    if user is None or not getattr(user, "is_authenticated", False):
        return "anonymous"
    if getattr(user, "is_alternative_login", False):  # a patient signed in by OTP
        return f"patient:***{str(getattr(user, 'phone_number', ''))[-4:]}"
    kind = "service" if getattr(user, "is_service_account", False) else "user"
    return f"{kind}:{user.pk}:{user.username}"


def current_actor():
    return _current.get()[1]


def resync():
    """Put this thread's values on the connection again. A rolled-back transaction (a
    refused view under ATOMIC_REQUESTS) also rolls back settings made inside it; writes
    after it (the sign-out revoke, the request entry) call this first."""
    values = _current.get()
    if values != _EMPTY:
        _apply(connection, values)


# Query parameters whose values are kept as they are; any other value (a searched name or
# phone number) is recorded as "***", a phone number with its last 4 digits.
PLAIN_PARAMS = frozenset(
    (
        "limit",
        "offset",
        "ordering",
        "page",
        "size",
        "status",
        "mode",
        "format",
        "facility",
        "encounter",
        "patient",
        "organization",
        "category",
        "kind",
        "op",
        "table",
        "before",
        "from",
        "to",
        "file",
        "active",
        "type",
        "file_type",
    )
)
MAX_PLAIN = 40
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_KEY = re.compile(r"^[A-Za-z0-9_.\[\]-]{1,40}$")
_PHONE = re.compile(r"^\+?[\d -]{10,}$")  # phone-length: last 4 kept, like stored ones

# The device's time zone, sent by our web app as X-IHR-TZ (P1-T7b): an IANA name, or
# "UTC+hh:mm" when the browser cannot name it. Staff computers must be on IST; a change made
# from any other zone is noted in the trail (the times typed in may be off). Only recorded,
# never trusted for anything else: anything that does not look like a zone is "invalid".
IST_ZONES = frozenset(("Asia/Kolkata", "Asia/Calcutta", "UTC+05:30"))
MAX_ZONE = 64
_ZONE = re.compile(r"^[A-Za-z0-9_+\-/:]{1,64}$")


def device_zone(request):
    """The zone to note for this request: None when IST or not sent (scripts, old pages)."""
    value = request.META.get("HTTP_X_IHR_TZ")
    if value is None:
        return None
    value = value.strip()
    if len(value) > MAX_ZONE or not _ZONE.match(value):
        return "invalid"
    return None if value in IST_ZONES else value


def _mask(name, value):
    """Kept: values of known paging/filter parameters, and record ids (UUIDs) anywhere.
    Masked: everything else, an OTP or a short number included."""
    if name in PLAIN_PARAMS and len(value) <= MAX_PLAIN:
        return value
    if _UUID.match(value):
        return value
    if _PHONE.match(value) and sum(c.isdigit() for c in value) >= 10:  # noqa: PLR2004
        return "***" + value.replace(" ", "").replace("-", "")[-4:]
    return "***" if value else ""


def mask_query(query):
    """A query string with search values masked (also used to show entries recorded before
    masking began)."""
    if not query:
        return query
    pairs = parse_qsl(query, keep_blank_values=True)
    return urlencode(
        [(k if _KEY.match(k) else "***", _mask(k, v)) for k, v in pairs], safe="*[]"
    )


def request_context(request, purpose):
    query = mask_query(request.META.get("QUERY_STRING", "")[:2000])
    ctx = {
        "rid": uuid.uuid4().hex,
        "via": "web",
        "ip": request.META.get("REMOTE_ADDR", ""),
        "method": request.method,
        "path": request.path[:MAX_PATH],
        "purpose": purpose,
    }
    if forwarded := request.META.get("HTTP_X_FORWARDED_FOR"):
        ctx["fwd"] = forwarded[:200]
    if query:
        ctx["query"] = query[:MAX_QUERY]
    if zone := device_zone(request):
        ctx["tz"] = zone  # also on every row this request changes
    return ctx


def begin_request(ctx):
    _set(ctx, "")


def bind_actor(user):
    """Name the person acting in this request: after DRF authentication (apps.py), and in
    our own non-DRF views as soon as they know who it is (sign-in, join, sign-out). An
    anonymous result never replaces a person already named (an idle session ended by the
    authentication class is still that person's sign-out)."""
    ctx, actor = _current.get()
    if not ctx:
        return
    name = actor_of(user)
    if name == "anonymous" and actor:
        return
    _set(json.loads(ctx), name)


def end():
    _set(None, "")


def begin_task(task):
    """A task names itself; a task run inside a request (eagerly) keeps the request's actor
    and adds its own name, and ``end_task`` puts the request's values back."""
    outer = _current.get()
    _outer.set((*_outer.get(), outer))
    info = {"task": task.name, "tid": str(task.request.id or "")}
    if outer[0]:
        _set({**json.loads(outer[0]), **info}, outer[1])
    else:
        _set({"via": "task", **info}, f"task:{task.name}")


@contextmanager
def job(ctx, actor):
    """A background job (the staff and onboarding workers): its writes name the job and
    whom it was done for; the previous values come back afterwards."""
    previous = _current.get()
    _set({"via": "worker", **ctx}, actor)
    try:
        yield
    finally:
        _current.set(previous)
        _apply(connection, previous)


def end_task():
    saved = _outer.get()
    if not saved:
        end()
        return
    _outer.set(saved[:-1])
    values = saved[-1]
    _current.set(values)
    _apply(connection, values)
