"""Records grants: MRD's audited clinical read, one patient at a time (P1-T11b4; design-b.md §7,
"After the attack" §8; decisions Q-K36 A, auto-accepted, awaiting owner confirmation).

- ``create_grant``: a user holding ``ihr_request_records_access`` in a facility organisation of
  this hospital names a patient and a reason code (a reference for legal and insurer requests).
  One transaction writes the ``ihr_records_grant`` row and a ``PatientUser`` row with the hidden
  role ``MRD Records Access`` (the placement trigger refuses that row without the grant). The
  POST endpoint and the dev command ``ihr_records_access`` both call it.
- ``end_grant``: the one end routine. It sets ``ended_at``, deletes the hidden-role
  ``PatientUser`` row and rebuilds the patient's ``users_cache``, in one atomic block, under a
  row lock (so a grant ends once). The DELETE endpoint, the beat job (``expire_due``) and the
  per-request expiry (``on_request``) all use it.
- ``on_request``: at the start of every staff request (DRF's authentication hook, after the
  audit actor): one indexed query for the user's open grants, no lock. Live ones fill the
  request's state and note them for the request's audit entry, which says ``purpose: records``
  with the ids of the grants whose patients the request showed (and only those, review A3);
  expired ones are only noted (the read-time rule in authz.py and ``state.live_grants`` already
  refuse them) and ``RecordsExpiryMiddleware`` ends them after the view, each in its own short
  transaction, in id order (review C3: never under the request's ATOMIC_REQUESTS transaction,
  so a refused request cannot roll the end back and parallel requests do not queue on the
  grant and patient rows).
- A new grant first ends that user's expired-but-open grants on the patient (review G1), and
  ``end_grant`` removes the hidden-role row only when no other live grant of that user on that
  patient still needs it. The hidden-role row is inserted without CARE's ``PatientUser.save``
  (which re-saves the whole patient from the instance it holds): only the caches are written,
  from a fresh, locked read of the patient (review G2).
- File links: CARE signs read links for an hour; a request served under a live grant gets
  ``min(seconds left on the file's patient's grant, IHR_RECORDS_LINK_SECONDS)``.
- Log line (ADR-013; alerts in deploy/logs/alerts/records.yml), on standard error, one whole
  line of our own words and numbers only (no patient id beyond the grant id):
  ``ihr-records: hospital=<slug> grant=<id|none> user=<id|none> reason=<code|invalid|none>
  outcome=<granted|ended|expired|refused> hour=<00-23 IST>``. ``granted``, ``ended`` and
  ``expired`` are written on commit (only a committed change logs); ``refused`` at once.

Lengths and reasons are settings and constants (Q-K36: easy to change):
``IHR_RECORDS_GRANT_MINUTES`` (30), ``IHR_RECORDS_LINK_SECONDS`` (300),
``IHR_RECORDS_REFERENCE_REQUIRED`` (legal, insurer); the codes are ``RecordsGrant.Reason``.
"""

import contextvars
import datetime
import logging
import os
import re
import uuid
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from ihr_access import branches, state

REQUEST_SLUG = "ihr_request_records_access"
IST = ZoneInfo("Asia/Kolkata")
LOG_PREFIX = "ihr-records:"
OUTCOMES = ("granted", "ended", "expired", "refused")
MAX_REFERENCE = 100
# A reference is a case or claim number: letters, digits and a few separators, never free text
REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ./_:-]{0,99}$")
MARKER = "ihr_records"
# The audit context's key for the grant ids a request showed (``annotate``)
GRANTS_KEY = "grants"
logger = logging.getLogger(__name__)
# Grants a request found expired, ended after its view (RecordsExpiryMiddleware); None outside
# a request the middleware wraps (a command, a test calling a view directly): the beat ends them
_due = contextvars.ContextVar("ihr_records_due", default=None)


def grant_minutes():
    return int(getattr(settings, "IHR_RECORDS_GRANT_MINUTES", 30))


def link_seconds():
    return int(getattr(settings, "IHR_RECORDS_LINK_SECONDS", 300))


def reference_required():
    return frozenset(
        getattr(settings, "IHR_RECORDS_REFERENCE_REQUIRED", ("legal", "insurer"))
    )


def reasons():
    from ihr_access.models import RecordsGrant

    return frozenset(RecordsGrant.Reason.values)


class GrantRefusedError(Exception):
    """A grant request refused: ``status`` 400 (the request is wrong) or 403 (not theirs to
    ask); ``message`` is shown to the person."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# --- the log line -----------------------------------------------------------------------


def _slug():
    """This hospital's slug: the request's tenant, else (a command, a task in a schema) the
    hospital whose schema the connection is in."""
    slug = getattr(getattr(connection, "tenant", None), "slug", None)
    schema = getattr(connection, "schema_name", None)
    if not slug and schema and schema != "public":
        from ihr_platform.models import Hospital

        slug = (
            Hospital.objects.filter(schema_name=schema)
            .values_list("slug", flat=True)
            .first()
        )
    return slug or "unknown"


def _number(value):
    return str(value) if isinstance(value, int) and value > 0 else "none"


def log_line(outcome, grant_id=None, user_id=None, reason=None, at=None):
    """The fixed line: our own words and numbers only, never a value from the request."""
    if outcome not in OUTCOMES:
        msg = f"not a records outcome: {outcome!r}"
        raise ValueError(msg)
    if reason is None or reason == "":
        code = "none"
    else:
        code = reason if reason in reasons() else "invalid"
    hour = (at or timezone.now()).astimezone(IST).hour
    return (
        f"{LOG_PREFIX} hospital={_slug()} grant={_number(grant_id)} "
        f"user={_number(user_id)} reason={code} outcome={outcome} hour={hour:02d}"
    )


def emit(line):
    # Straight to standard error as one whole line, as the batch refusals (ihr_platform/batch.py)
    os.write(2, f"{line}\n".encode())


def _emit_on_commit(line):
    transaction.on_commit(lambda: emit(line))


# --- grants -----------------------------------------------------------------------------


def _hidden_role():
    from care.security.models import RoleModel
    from ihr_roles.registry import HIDDEN

    return RoleModel.objects.get(name=HIDDEN, is_system=True)


def _patient(value):
    from care.emr.models import Patient

    if isinstance(value, Patient):
        return value
    try:
        key = uuid.UUID(str(value))
    except ValueError:
        return None
    return Patient.objects.filter(external_id=key).first()


def _refuse(status, message, user, reason):
    emit(log_line("refused", user_id=getattr(user, "pk", None), reason=reason))
    raise GrantRefusedError(status, message)


def create_grant(user, patient, reason, reference="", now=None):
    """A live grant for ``user`` on ``patient`` (a Patient or its external id), or
    GrantRefusedError. The endpoint's and the dev command's one path."""
    from care.emr.models import Patient, PatientUser

    from ihr_access.models import RecordsGrant

    reason = reason if isinstance(reason, str) else None
    reference = (reference or "").strip() if isinstance(reference, str) else None
    if getattr(user, "is_superuser", False) or not branches.holds(user, REQUEST_SLUG):
        _refuse(403, "Your role cannot ask for records access.", user, reason)
    if not reason:
        _refuse(400, "Give a reason.", user, reason)
    if reason not in reasons():
        _refuse(
            400,
            f"The reason must be one of: {', '.join(sorted(reasons()))}.",
            user,
            reason,
        )
    if reference is None or len(reference) > MAX_REFERENCE:
        _refuse(400, "The reference is text of at most 100 characters.", user, reason)
    if reference and not REFERENCE_RE.match(reference):
        _refuse(
            400,
            "The reference holds a case or claim number only (letters, digits, . / _ : -).",
            user,
            reason,
        )
    if reason in reference_required() and not reference:
        _refuse(
            400, "This reason needs a reference (case or claim number).", user, reason
        )
    found = _patient(patient)
    if found is None:
        _refuse(400, "No such patient.", user, reason)
    now = now or timezone.now()
    with transaction.atomic():
        # A grant past its time but not yet ended (the beat runs each minute; the dev command
        # runs no request hook) is ended here first, with its own ``expired`` line, so its later
        # end can never take this grant's row (review G1)
        lapsed = (
            RecordsGrant.objects.select_for_update()
            .filter(
                user=user, patient=found, ended_at__isnull=True, expires_at__lte=now
            )
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        for lapsed_id in list(lapsed):
            end_grant(lapsed_id, "expired", now=now)
        live = RecordsGrant.objects.select_for_update().filter(
            user=user, patient=found, ended_at__isnull=True, expires_at__gt=now
        )
        if live.exists():
            # Ended first, never stacked: each grant has its own reason and time box
            _refuse(
                400,
                "You already have records access to this patient: end it first.",
                user,
                reason,
            )
        grant = RecordsGrant.objects.create(
            patient=found,
            user=user,
            reason=reason,
            reference=reference,
            created_at=now,
            expires_at=now + datetime.timedelta(minutes=grant_minutes()),
        )
        role = _hidden_role()
        # A row an earlier grant left behind (its end failed half-way) is replaced
        PatientUser.objects.filter(user=user, patient=found, role=role).delete()
        # Not CARE's PatientUser.save: it re-saves the whole patient from the instance it holds,
        # which was read before this block and could write back a demographics edit made since
        # (review G2). The row alone (the placement trigger still checks it), then only the
        # caches, from a fresh read locked against a concurrent edit.
        PatientUser.objects.bulk_create(
            [PatientUser(user=user, patient_id=found.pk, role=role)]
        )
        Patient.objects.select_for_update().get(pk=found.pk).save(
            update_fields=["users_cache"]
        )
        _emit_on_commit(log_line("granted", grant.pk, user.pk, reason, now))
    current = state.current()
    if current is not None and current.is_for(user):
        current.set_grants({**current.grants, found.pk: (grant.pk, grant.expires_at)})
    annotate()
    return grant


def end_grant(grant, outcome="ended", now=None):
    """End ``grant`` (a RecordsGrant or its id) once: True if this call ended it. ``outcome``:
    ``ended`` (early: the holder, an admin, the dev command) or ``expired`` (the beat, the
    per-request expiry)."""
    from care.emr.models import Patient, PatientUser

    from ihr_access.models import RecordsGrant

    if outcome not in ("ended", "expired"):
        msg = f"a grant ends as ended or expired, not {outcome!r}"
        raise ValueError(msg)
    pk = getattr(grant, "pk", grant)
    now = now or timezone.now()
    with transaction.atomic():
        row = (
            RecordsGrant.objects.select_for_update()
            .filter(pk=pk, ended_at__isnull=True)
            .first()
        )
        if row is None:
            return False  # already ended (another request, the beat): logged there
        row.ended_at = now
        row.save(update_fields=["ended_at"])
        # The row is shared by the user's grants on this patient: kept while another of them is
        # still live (never made by create_grant, which ends lapsed grants first; review G1)
        still_needed = (
            RecordsGrant.objects.filter(
                user_id=row.user_id,
                patient_id=row.patient_id,
                ended_at__isnull=True,
                expires_at__gt=now,
            )
            .exclude(pk=row.pk)
            .exists()
        )
        if not still_needed:
            PatientUser.objects.filter(
                user_id=row.user_id, patient_id=row.patient_id, role=_hidden_role()
            ).delete()
        # users_cache (and organization_cache) rebuilt by CARE's save; nothing else written
        patient = Patient.objects.get(pk=row.patient_id)
        patient.save(update_fields=["users_cache"])
        _emit_on_commit(log_line(outcome, row.pk, row.user_id, row.reason, now))
    current = state.current()
    if current is not None and getattr(current.user, "pk", None) == row.user_id:
        grants = {p: (g, e) for p, (g, e) in current.grants.items() if g != row.pk}
        current.set_grants(grants)
    return True


def expire_due(now=None):
    """The beat job's work: end every grant past its time in this hospital. Returns the
    number this run ended."""
    from ihr_access.models import RecordsGrant

    now = now or timezone.now()
    due = list(
        RecordsGrant.objects.filter(ended_at__isnull=True, expires_at__lte=now)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    return sum(end_grant(pk, "expired", now=now) for pk in due)


def on_request(user):
    """At the start of every DRF request (after authentication and the audit actor): one
    indexed query for the user's open grants, no row lock; note the expired ones for
    ``end_due`` (after the view), keep the live ones in the request state, and note them for
    the request's audit entry (``annotate``)."""
    from ihr_access.models import RecordsGrant

    current = state.current()
    if current is None or not state._signed_in(user) or not current.is_for(user):  # noqa: SLF001
        return
    if getattr(connection, "schema_name", "public") == "public":
        return  # the platform's own hosts: no hospital, no grants
    now = timezone.now()
    rows = list(
        RecordsGrant.objects.filter(user=user, ended_at__isnull=True)
        .order_by("pk")
        .values_list("pk", "patient_id", "expires_at")
    )
    due = _due.get()
    if due is not None:
        due.update(g for g, _p, expires_at in rows if expires_at <= now)
    current.set_grants(state.live_grants(user, rows=rows, now=now))
    annotate()


def start_due():
    """RecordsExpiryMiddleware, before the view: this request's list of grants to end."""
    return _due.set(set())


def end_due(token):
    """RecordsExpiryMiddleware, after the view and outside its transaction (ATOMIC_REQUESTS):
    end the expired grants this request found, each in its own short transaction, in id order.
    A failure is logged and left to the beat (the read-time rule refuses the grant meanwhile)."""
    from ihr_audit import context

    due = sorted(_due.get() or ())
    _due.reset(token)
    if not due:
        return
    context.resync()  # the view's transaction may have rolled the audit values back
    for grant_id in due:
        try:
            end_grant(grant_id, "expired")
        except DatabaseError:
            logger.exception("records grant %s: end after the request failed", grant_id)


def annotate():
    """This request's audit entry names a live grant, with ``purpose: records``, only if the
    request showed that grant's patient (review A3: a read of another patient, or the front-desk
    search, made while a grant is live is not marked with that grant's reason)."""
    from ihr_audit import context

    current = state.current()
    if current is None or not current.grants:
        return
    context.annotate_patients(
        GRANTS_KEY,
        {patient: grant for patient, (grant, _ends) in current.grants.items()},
        purpose="records",
    )


def seconds_left(patient_id=None, now=None):
    """Seconds left on this request's grant for ``patient_id`` (or, unknown patient, on the
    soonest of its grants); None when the request is served under no grant."""
    current = state.current()
    if current is None or not current.grants:
        return None
    now = now or timezone.now()
    if patient_id is not None and patient_id in current.grants:
        ends = current.grants[patient_id][1]
    else:
        ends = min(expires for _id, expires in current.grants.values())
    return max(1, int((ends - now).total_seconds()))


# --- signed file links ------------------------------------------------------------------

# file_type -> how its associating_id leads to the patient (CARE's file and report types)
_FILE_PATIENT = {
    "patient": ("emr.Patient", "id"),
    "encounter": ("emr.Encounter", "patient_id"),
    "consent": ("emr.Consent", "encounter__patient_id"),
    "diagnostic_report": ("emr.DiagnosticReport", "patient_id"),
    "service_request": ("emr.ServiceRequest", "patient_id"),
    "discharge_summary": ("emr.Encounter", "patient_id"),
    "encounter_report": ("emr.Encounter", "patient_id"),
    "patient_summary": ("emr.Patient", "id"),
}


def _patient_of_file(file_obj):
    from django.apps import apps

    kind = getattr(file_obj, "file_type", None) or getattr(
        file_obj, "report_type", None
    )
    target = _FILE_PATIENT.get(kind)
    if target is None:
        return None
    try:
        key = uuid.UUID(str(getattr(file_obj, "associating_id", "")))
    except ValueError:
        return None
    label, path = target
    return (
        apps.get_model(label)
        .objects.filter(external_id=key)
        .values_list(path, flat=True)
        .first()
    )


def link_cap(file_obj):
    """The longest a signed read link may live in this request: None (CARE's own) unless the
    request is served under a live grant, then min(seconds left, IHR_RECORDS_LINK_SECONDS)."""
    current = state.current()
    if current is None or not current.grants:
        return None
    return min(seconds_left(_patient_of_file(file_obj)), link_seconds())


def install():
    """Wrap CARE's signed read link (IhrAccessConfig.ready(); ihr_access.E023 checks it)."""
    from care.emr.utils.file_manager import S3FilesManager

    original = S3FilesManager.read_signed_url
    if getattr(original, MARKER, False):
        return

    def read_signed_url(self, file_obj, duration=60 * 60):
        cap = link_cap(file_obj)
        if cap is not None:
            duration = min(duration, cap)
        return original(self, file_obj, duration=duration)

    setattr(read_signed_url, MARKER, True)
    read_signed_url.ihr_original = original
    S3FilesManager.read_signed_url = read_signed_url


def problems():
    """Why the signed-link cap does not hold (ihr_access.E023)."""
    from care.emr.utils.file_manager import S3FilesManager

    if not getattr(S3FilesManager.read_signed_url, MARKER, False):
        return ["S3FilesManager.read_signed_url is not capped for records grants"]
    return []
