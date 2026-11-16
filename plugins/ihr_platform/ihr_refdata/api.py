"""The hospital admin's "updates available" API for reference packs (P1-T15b1;
docs/reviews/p1-t15b/design.md §2 and "After the attack" items 6, 15, 17).

    GET  /api/ihr/packs/updates/            pending and kept items, paged
    GET  /api/ihr/packs/updates/<pk>/       one item with its field difference
    POST /api/ihr/packs/updates/<pk>/       accept or keep it, with a reason
    GET  /api/ihr/packs/history/            applies and decisions, newest first

Only a hospital-wide admin (``IsHospitalAdmin``: signed-in staff, never a service account),
decided before any body is read. Nothing here shows patient data, a row's history or authors,
``PackApply.error`` or our operators' names. GETs show no patients, so they write no request
entry; every POST writes one (purpose ``admin``, ihr_audit.middleware).

A POST is checked before the hospital's lock is taken (body, the item, what the admin was
shown, blocked, the release recorded, the row's hash), then once more inside it by
``engine.decide``, which writes and commits in its own transaction while the lock is held: the
view is ``non_atomic_requests``, so CARE's ATOMIC_REQUESTS cannot commit after the lock is
released (a pack apply starting in between would read and overwrite uncommitted state). At
most one decision is in flight per hospital and 30 per user per minute (schema-prefixed cache
keys); beyond that 429. A busy hospital (a pack apply, a release, a dump, a restore) is 409
with ``Retry-After: 60`` and never names the other job.

Per-facility items (P1-T15b3): each shows its facility's external id (null for a hospital-wide
item) and the list takes ``facility=<external id>``. A soft-deleted facility's items are hidden
(listed, shown and decided as if absent) until it is restored.
"""

import re
import time
import uuid

from care.users.models import User
from django.core.cache import cache
from django.db import connection, transaction
from django.db.models import Q
from django.http import QueryDict
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from ihr_audit import context as audit
from ihr_identity.staff_access import IsHospitalAdmin
from ihr_platform.models import Hospital
from ihr_platform.provisioning import runner
from rest_framework.response import Response
from rest_framework.views import APIView

from ihr_refdata import diff, engine, kinds
from ihr_refdata.models import PackApply, PackItem

PAGE, MAX_PAGE = 50, 200
MAX_OFFSET = 10**6
MAX_ID = 2**63 - 1
MAX_VERSION = 2**31 - 1
RETRY_AFTER = "60"
PER_USER_PER_MINUTE = 30
# The in-flight mark expires on its own if a process dies; it outlasts the longest decision
# (an accept may run a data check: 30 s repeat query + 60 s regex scan, review E4)
IN_FLIGHT_SECONDS = 150
STATES = {"pending": PackItem.State.PENDING, "kept": PackItem.State.KEPT}
WAITING = tuple(STATES.values())
HEX = re.compile(r"^(?:[0-9a-f]{64})?$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
NUMBER = re.compile(r"^[0-9]{1,7}$")
PACK_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
STAFF_ACTOR = re.compile(r"^packs:user-([0-9]{1,19})$")
NOT_FOUND = "No such pack update waiting here."
BUSY = "This hospital is busy with other work; try again in a minute."
IN_FLIGHT = "Another pack decision is being saved here; try again in a minute."
TOO_MANY = "Too many pack decisions in a minute; try again in a minute."
NOT_LIVE = "This hospital is not live: pack updates wait until it is."
UNAVAILABLE = (
    "The reference packs cannot be read here right now; try again later, and tell your "
    "operator if it persists."
)
FAILED_NOTE = "failed; the details are kept for the operators"
# Every route here: hospital-wide admins only (mutation row packs-api-gate)
GATE = (IsHospitalAdmin,)


def _number(params, name, default, high):
    value = params.get(name)
    if value is None or value == "":
        return default
    if not NUMBER.match(value) or int(value) > high:
        return _bad(name, f"a whole number from 0 to {high}")
    return int(value)


class _BadQueryError(Exception):
    def __init__(self, field, message):
        super().__init__(message)
        self.body = {field: message}


def _bad(field, message):
    raise _BadQueryError(field, message)


def _page(params):
    limit = _number(params, "limit", PAGE, MAX_PAGE)
    if limit < 1:
        _bad("limit", f"a whole number from 1 to {MAX_PAGE}")
    return limit, _number(params, "offset", 0, MAX_OFFSET)


def _releases():
    """Every verified release, oldest first, per pack (cached per manifest sha256)."""
    return engine.verified_releases(cached=True)


def _release(releases, pack, version):
    return next((r for r in releases.get(pack, ()) if r.version == version), None)


def _summary(record, row, item):
    handler = kinds.get(record.kind)
    return {
        "id": record.pk,
        "pack": record.pack,
        "kind": record.kind,
        "item": record.item,
        "label": handler.label(row, item),
        "facility": (
            str(record.facility.external_id) if record.facility_id is not None else None
        ),
        "state": record.state,
        "reason": record.reason,
        "applied_version": record.applied_version,
        "pending_version": record.pending_version,
        "changed_at": record.changed_at.isoformat(),
    }


def _waiting_item(record, releases):
    """The pack's content waiting for this record (None for a removal or an unknown version)."""
    pending = _release(releases, record.pack, record.pending_version)
    if pending is None or not record.pending_sha256:
        return None
    return pending.by_item().get(record.item)


def detail(record, releases):
    """One item as the admin decides on it: the list fields, what accepting would do, and the
    three columns of every field the pack sets (never any other field of the row)."""
    handler = kinds.get(record.kind)
    row = _row(handler, record)
    item = _waiting_item(record, releases)
    waiting = record.state in WAITING
    effect = handler.effect(row, item) if waiting else ""
    hospital = handler.shown(row=row) if row is not None else None
    if not waiting:
        pack = None
    elif effect == "retire":
        pack = handler.shown(row=row, retire=True) if row is not None else None
    else:
        pack = handler.shown(item=item) if item is not None else None
    applied = _release(releases, record.pack, record.applied_version)
    before = applied.by_item().get(record.item) if applied is not None else None
    previous = handler.shown(item=before) if before is not None else None
    out = {
        **_summary(record, row, item),
        "pending_sha256": record.pending_sha256,
        "row_sha256": engine.row_sha256(handler, row),
        "effect": effect,
        "blocked": blocked(record),
        "conflicts": held(record),
        "fields": diff.fields(handler.FIELDS, hospital, pack, previous),
    }
    # A kind's own counts (forms: what a new version leaves on the current one, item 12)
    if waiting and (notes := handler.notes(row, item)):
        out["notes"] = notes
    return out


def _row(handler, record):
    """The hospital row the record's item writes (by the record's target where it has one;
    in the record's facility for a per-facility kind)."""
    return (
        handler.at(record.facility)
        .rows([record.item], targets={record.item: record.target})
        .get(record.item)
    )


def _shown():
    """Records an admin may see: hospital-wide ones, and those of facilities not deleted."""
    return PackItem.objects.select_related("facility").filter(
        Q(facility__isnull=True) | Q(facility__deleted=False)
    )


def blocked(record):
    """The reason that refuses an accept now: a pending item's stored one. A kept item is
    asked afresh at its accept (review E5), so a fixed problem never blocks it for good."""
    if record.state == PackItem.State.PENDING and record.reason in engine.BLOCKING:
        return record.reason
    return ""


def held(record):
    """What holds the item back, as stored by the last apply (counts only, never a value; a
    data check is never run in a GET): {} when nothing is stored."""
    checks = record.checks or {}
    if not checks:
        return {}
    out = {"counts": dict(checks.get("counts") or {})}
    if "conflict" in checks:
        out["conflict"] = checks["conflict"]
    if "missing-link" in checks:
        out["missing_link"] = checks["missing-link"]
    if "patients" in checks:
        out["patients"] = checks["patients"]
        out["complete"] = bool(checks.get("complete"))
        if not out["complete"]:
            out["note"] = (
                "not fully checked: the hospital's data took too long to check"
            )
        elif checks["patients"]:
            out["note"] = f"{checks['patients']} patients' edits would fail until fixed"
        else:
            out["note"] = "checked: no patient's edits would fail"
    if at := parse_datetime(checks.get("at") or ""):
        out["checked_at"] = at.isoformat()
        out["age_seconds"] = max(0, int((timezone.now() - at).total_seconds()))
    return out


def _refused(status, detail_text, **flags):
    response = Response({"detail": detail_text, **flags}, status=status)
    if flags.get("busy") or status == 429:  # noqa: PLR2004
        response["Retry-After"] = RETRY_AFTER
    return response


class UpdatesView(APIView):
    permission_classes = GATE

    def get(self, request):
        params = request.query_params
        try:
            limit, offset = _page(params)
            states = WAITING
            if state := params.get("state"):
                if state not in STATES:
                    _bad("state", "pending or kept")
                states = (STATES[state],)
            try:
                releases = _releases()
            except engine.PackError:
                return _refused(503, UNAVAILABLE)
            rows = _shown().filter(state__in=states)
            if pack := params.get("pack"):
                if pack not in releases:
                    _bad("pack", "not a pack of this build")
                rows = rows.filter(pack=pack)
            if kind := params.get("kind"):
                if kind not in kinds.names():
                    _bad("kind", f"one of {', '.join(kinds.names())}")
                rows = rows.filter(kind=kind)
            if facility := params.get("facility"):
                if not UUID.match(facility):
                    _bad("facility", "a facility's id")
                rows = rows.filter(facility__external_id=facility)
        except _BadQueryError as bad:
            return Response(bad.body, status=400)
        rows = rows.order_by("pack", "item", "facility_id", "id")
        count = rows.count()
        page = list(rows[offset : offset + limit])
        found = {}
        for kind, place in {(r.kind, r.facility_id) for r in page}:
            mine = [r for r in page if (r.kind, r.facility_id) == (kind, place)]
            found[kind, place] = (
                kinds.get(kind)
                .at(mine[0].facility)
                .rows([r.item for r in mine], targets={r.item: r.target for r in mine})
            )
        results = [
            _summary(
                r,
                found[r.kind, r.facility_id].get(r.item),
                _waiting_item(r, releases),
            )
            for r in page
        ]
        return Response({"count": count, "results": results})


class UpdateView(APIView):
    permission_classes = GATE

    @classmethod
    def as_view(cls, **initkwargs):
        # decide() commits inside the hospital's lock; ATOMIC_REQUESTS would commit after it
        return transaction.non_atomic_requests(super().as_view(**initkwargs))

    def _record(self, pk, *, waiting=True):
        if pk > MAX_ID:
            return None
        rows = _shown().filter(pk=pk)
        if waiting:
            rows = rows.filter(state__in=WAITING)
        return rows.first()

    def get(self, request, pk):
        record = self._record(pk)
        if record is None:
            return _refused(404, NOT_FOUND)
        try:
            releases = _releases()
        except engine.PackError:
            return _refused(503, UNAVAILABLE)
        return Response(detail(record, releases))

    def post(self, request, pk):  # noqa: PLR0911, PLR0912 (the answers of §2.5, in order)
        user = request.user
        if not _under_rate(user):
            return _refused(429, TOO_MANY)
        body, errors = _body(request.data)
        if errors:
            return Response(errors, status=400)
        accept = body["action"] == "accept"
        hospital = connection.tenant
        if hospital.status != Hospital.Status.ACTIVE:
            return _refused(409, NOT_LIVE)
        record = self._record(pk, waiting=False)
        if record is None:
            return _refused(404, NOT_FOUND)
        try:
            releases = engine.prepare("api", record=False, cached=True)
            everything = _releases()
        except engine.PackError:
            return _refused(503, UNAVAILABLE)
        expect = (body["version"], body["sha256"], body["row_sha256"])
        # Checks before the lock (item 6): the lock is never taken for a request that fails
        if engine.replayed(record, accept=accept, version=expect[0], sha256=expect[1]):
            return Response({**detail(record, everything), "unchanged": True})
        if record.state not in (WAITING if accept else (PackItem.State.PENDING,)):
            return _refused(404, NOT_FOUND)
        release = releases.get(record.pack)
        if release is None or record.pending_version != release.version:
            return _refused(
                409, "A newer pack release is not applied here yet.", stale=True
            )
        if (record.pending_version, record.pending_sha256) != expect[:2]:
            return _refused(409, "This update changed since it was shown.", stale=True)
        if accept and blocked(record):
            return _refused(
                409,
                f"It cannot be accepted until its {record.reason} is fixed.",
                blocked=True,
            )
        if not engine.recorded(release):
            return _refused(409, "This update is not released here yet.", stale=True)
        handler = kinds.get(record.kind)
        row = _row(handler, record)
        if engine.row_sha256(handler, row) != expect[2]:
            return _refused(
                409,
                "The hospital's own version changed since it was shown.",
                stale=True,
            )
        token = uuid.uuid4().hex
        if not cache.add(_in_flight_key(), token, IN_FLIGHT_SECONDS):
            return _refused(429, IN_FLIGHT)
        try:
            with (
                audit.extend(
                    job="packs",
                    via="api",
                    action=body["action"],
                    item=record.item,
                    reason=body["reason"],
                ),
                runner.exclusive(hospital.slug),
            ):
                decided = engine.decide(
                    hospital,
                    record.pack,
                    record.item,
                    accept=accept,
                    operator=f"packs:{engine.STAFF_OPERATOR_PREFIX}{user.pk}",
                    reason=body["reason"],
                    expect=expect,
                    record=False,
                    releases=releases,
                    facility=record.facility,
                )
        except runner.ProvisioningError:
            return _refused(409, BUSY, busy=True)
        except engine.NothingWaitingError:
            return _refused(404, NOT_FOUND)
        except engine.BlockedError as exc:
            return _refused(409, str(exc), blocked=True)
        except engine.StaleError as exc:
            return _refused(409, str(exc), stale=True)
        except engine.RefusedError as exc:
            return Response({"reason": str(exc)}, status=400)
        except engine.PackError:
            return _refused(503, UNAVAILABLE)
        finally:
            if cache.get(_in_flight_key()) == token:
                cache.delete(_in_flight_key())
        return Response(
            {**detail(decided, everything), "unchanged": bool(decided.replayed)}
        )


def _in_flight_key():
    return "ihr:packs:decision"  # the cache's key function adds this hospital's schema


def _under_rate(user):
    """At most PER_USER_PER_MINUTE decisions per user per clock minute (refused ones count)."""
    key = f"ihr:packs:rate:{user.pk}:{int(time.time() // 60)}"
    cache.add(key, 0, 120)
    try:
        return cache.incr(key) <= PER_USER_PER_MINUTE
    except ValueError:  # expired between add and incr: a new minute
        return True


def _body(data):
    """({action, version, sha256, row_sha256, reason}, {}) or (None, {field: message})."""
    if not isinstance(data, dict) or isinstance(data, QueryDict):
        return None, {"detail": "Send a JSON object."}
    errors = {}
    action = data.get("action")
    if action not in ("accept", "keep"):
        errors["action"] = "accept or keep"
    version = data.get("version")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or not 1 <= version <= MAX_VERSION
    ):
        errors["version"] = "the pending_version you were shown"
    for name in ("sha256", "row_sha256"):
        value = data.get(name)
        if not isinstance(value, str) or not HEX.match(value):
            errors[name] = (
                f"the {'pending_sha256' if name == 'sha256' else name} you were shown"
            )
    reason = data.get("reason")
    if not isinstance(reason, str):
        errors["reason"] = "a reason is required"
    else:
        try:
            reason = engine.normal_reason(reason)
        except engine.RefusedError as exc:
            errors["reason"] = str(exc)
    if errors:
        return None, errors
    return {
        "action": action,
        "version": version,
        "sha256": data["sha256"],
        "row_sha256": data["row_sha256"],
        "reason": reason,
    }, {}


def _by(actor, users):
    """Who: start-up, an operator (never named), or the deciding staff member."""
    if actor == "packs:startup":
        return "start-up"
    if match := STAFF_ACTOR.match(actor):
        user = users.get(int(match.group(1)))
        return {
            "user": str(user["external_id"]) if user else None,
            "username": user["username"] if user else None,
        }
    return "operator"


class HistoryView(APIView):
    permission_classes = GATE

    def get(self, request):
        params = request.query_params
        try:
            limit, offset = _page(params)
            pack = params.get("pack")
            if pack and not PACK_NAME.match(pack):
                _bad("pack", "not a pack name")
        except _BadQueryError as bad:
            return Response(bad.body, status=400)
        rows = PackApply.objects.all()
        if pack:
            rows = rows.filter(pack=pack)
        rows = rows.order_by("-applied_at", "-id")
        count = rows.count()
        page = list(rows[offset : offset + limit])
        pks = {int(m.group(1)) for r in page if (m := STAFF_ACTOR.match(r.actor))}
        users = {
            u["pk"]: u
            for u in User.objects.filter(pk__in=pks).values(
                "pk", "external_id", "username"
            )
        }
        results = []
        for r in page:
            entry = {
                "id": r.pk,
                "pack": r.pack,
                "version": r.version,
                "outcome": r.outcome,
                "at": r.applied_at.isoformat(),
                "by": _by(r.actor, users),
                "counts": r.counts,
                # null: an apply from before the change list was kept (no changes_total)
                "changes": (
                    None
                    if r.outcome == PackApply.Outcome.APPLIED
                    and "changes_total" not in (r.counts or {})
                    else r.changes
                ),
            }
            if r.outcome == PackApply.Outcome.FAILED:
                entry["note"] = FAILED_NOTE  # the error itself is for operators only
            results.append(entry)
        return Response({"count": count, "results": results})
