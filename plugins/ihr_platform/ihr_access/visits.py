"""A visit named in the query must be the path patient's (P1-T11b3 review fixes K1/M1).

CARE's patient-scoped clinical viewsets (condition, observation, consent, medication request,
prescription, statement and administration, note thread and note message) let a reader with no
clinical right on the patient in through ``?encounter=<visit>``: CARE's
``can_view_encounter_clinical_data`` on that visit (``EncounterBasedAuthorizationBase.
authorize_read_encounter``, and copies of it in notes.py). They never check that the visit is the
path patient's, and a retrieve is not narrowed by the encounter filter (``EMRBaseViewSet.
get_object`` skips ``filter_queryset``). So the Radiologist, holding Radiology on one patient's
visit, read any other patient's diagnoses, medicines, results, consents and notes by id, and
``observation/analyse`` gave every result of a code (Q-K44 A forbids it).

For the users CARE's membership-only paths are closed to (``branches.closed``: restricted, or
holding roles of ours only, the Radiologist included), a request on any route with
``patient_external_id`` whose query names an ``encounter`` is refused (403) unless:

- the visit is one of the path patient's (a malformed id is refused too); and
- the user may read that patient's chart (CARE's ``can_view_clinical_data``: then the visit adds
  nothing), or the request reads through that visit only: a list (CARE's filters narrow it to the
  visit), or rows named in the path (the object, the note thread) that are all of that visit. Any
  other action (``analyse``, a write with no object) is refused: it is not narrowed by the visit.

The check runs in ``APIView.initial`` after CARE's authentication and permissions, so every DRF
view under a patient (upstream's new ones included) and every batch sub-request passes it.
CARE's own roles are unchanged: their side of the gap is on Q-K40's list (docs/QUESTIONS.md).
``problems()`` (ihr_access.E022) fails if the hook is not installed or a patient-scoped view
overrides ``initial`` outside our code.
"""

import uuid

from rest_framework.exceptions import PermissionDenied
from rest_framework.views import APIView

from ihr_access import branches, state

PATIENT = "patient_external_id"
PARAM = "encounter"
MARKER = "ihr_visit_param"
REFUSED = "This visit does not open these records."
# Rows a path names besides the view's own object, whose visit must be the named one
PARENTS = (("thread_external_id", "emr.NoteThread"),)


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _model(label):
    from django.apps import apps

    return apps.get_model(label)


def _named_rows(view, kwargs):
    """(model, external id) of every row the path names, or None if the action reads beyond
    the visit (an objectless action other than list)."""
    rows = []
    lookup = getattr(view, "lookup_url_kwarg", None) or getattr(
        view, "lookup_field", None
    )
    if lookup and lookup in kwargs:
        model = getattr(view, "database_model", None) or getattr(
            getattr(view, "queryset", None), "model", None
        )
        if model is None:
            return None
        rows.append((model, kwargs[lookup]))
    elif getattr(view, "action", None) != "list":
        return None
    for kwarg, label in PARENTS:
        if kwarg in kwargs:
            rows.append((_model(label), kwargs[kwarg]))
    return rows


def _of_visit(model, external_id, encounter):
    """The row is of ``encounter`` (a row that does not exist is CARE's 404)."""
    if PARAM not in {f.name for f in model._meta.get_fields()}:  # noqa: SLF001
        return False  # no visit to compare: fail closed
    key = _uuid(external_id)
    if key is None:
        return True  # CARE answers a malformed id itself (404)
    found = list(
        model.objects.filter(external_id=key).values_list("encounter_id", flat=True)[:1]
    )
    return not found or found[0] == encounter.pk


def check(view, request):
    kwargs = getattr(view, "kwargs", None) or {}
    patient = kwargs.get(PATIENT)
    if patient is None:
        return
    named = request.GET.get(PARAM)
    if not named:
        return  # no visit named: CARE decides on the patient alone
    user = request.user
    if not branches.closed(user):
        return
    patient_key, encounter_key = _uuid(patient), _uuid(named)
    if patient_key is None:
        return  # CARE answers a malformed patient id itself (404)
    from care.emr.models import Encounter

    encounter = (
        Encounter.objects.filter(
            external_id=encounter_key, patient__external_id=patient_key
        )
        .select_related("patient")
        .first()
        if encounter_key is not None
        else None
    )
    if encounter is None:
        raise PermissionDenied(REFUSED)
    if state.for_user(user).may_read_chart(encounter.patient):
        return
    rows = _named_rows(view, kwargs)
    if rows is None or not all(_of_visit(m, e, encounter) for m, e in rows):
        raise PermissionDenied(REFUSED)


def install():
    """Wrap ``APIView.initial`` once (IhrAccessConfig.ready())."""
    if getattr(APIView.initial, MARKER, False):
        return
    original = APIView.initial

    def initial(self, request, *args, **kwargs):
        original(self, request, *args, **kwargs)
        check(self, request)

    setattr(initial, MARKER, True)
    initial.ihr_original = original
    APIView.initial = initial


def patient_routes():
    """(route, view class) of every route of the hospital's URL configuration under a patient."""
    from django.urls import URLPattern, URLResolver, get_resolver

    def walk(patterns, prefix=""):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                yield from walk(pattern.url_patterns, prefix + str(pattern.pattern))
            elif isinstance(pattern, URLPattern):
                yield prefix + str(pattern.pattern), pattern.callback

    found = []
    for route, callback in walk(get_resolver().url_patterns):
        if f"<{PATIENT}>" in route or f"<{PATIENT}:" in route or "{" + PATIENT in route:
            cls = getattr(callback, "cls", None) or getattr(
                callback, "view_class", None
            )
            found.append((route, cls))
    return found


def problems():
    """Why the visit check does not hold (ihr_access.E022)."""
    found = []
    if not getattr(APIView.initial, MARKER, False):
        found.append("APIView.initial is not the visit check (visits.install)")
    for route, cls in patient_routes():
        if cls is None or not issubclass(cls, APIView):
            found.append(f"{route}: not a DRF view, the visit check cannot see it")
            continue
        initial = cls.initial
        if initial is not APIView.initial and not initial.__module__.startswith("ihr_"):
            found.append(
                f"{route}: {cls.__qualname__}.initial is CARE's own, the visit check may "
                "not run"
            )
    return found
