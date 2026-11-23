"""CARE's action engine, closed to the restricted roles and to users of our roles only (P2-T0a,
review F2 of docs/reviews/p2-t0a/review.md).

CARE 2026-11 added configured *actions*: an ``Action`` row (made by a superuser) holds conditions
and instructions (show a message, tag the patient or booking, set the visit's priority) that
``care.action_evaluator.base.ActionEvaluator`` runs as the person making the request. Three paths
reach it:

- ``EMRActionBaseViewSet.execute_action`` (POST ``.../execute_action/``) on every viewset that
  mixes it in (token bookings, slots): CARE checks only the booking right ("TODO: Authorize
  action permission"), which our Receptionist holds;
- ``EMRActionBaseViewSet.perform_actions``: run after a slot's ``create_appointment`` and after
  any create of such a viewset; its outputs come back raw in the answer's ``_actions``;
- the questionnaire submit (``QuestionnaireViewSet.submit``), whose outputs come back in
  ``_actions`` too (none of our roles may submit a form today: no ``can_submit_*``).

The instructions' writes never pass through a route ihr_access reviewed, and their outputs can
carry patient fields (blood group, birth date, phone) that the guard keeps from the front desk.
So, for ``branches.closed`` users (restricted, or holding our roles only: the Radiologist):

- ``execute_action`` answers 403 before CARE reads the body;
- ``perform_actions`` runs nothing and returns no output;
- ``ActionEvaluator.evaluate`` runs nothing and returns no output (every other caller,
  the questionnaire submit included);
- ``IhrSlotViewSet`` drops ``_actions`` from their ``create_appointment`` answer (views.py).

CARE's own roles are unchanged. Every viewset of the URL configuration that mixes in
``EMRActionBaseViewSet`` must be reviewed in ``fields/action_viewsets.txt`` and must not
override the two wrapped methods; ``problems()`` (ihr_access.E022) fails the start otherwise.
"""

import functools

from rest_framework.exceptions import PermissionDenied

from ihr_access import branches, guard

MARKER = "ihr_action_gate"
ACTIONS_REFUSED = "Your role cannot run configured actions."
VIEWSETS = guard.FIELDS / "action_viewsets.txt"
VIEWSET_VERBS = frozenset({"closed"})
WRAPPED = ("execute_action", "perform_actions")


def _base():
    from care.emr.api.viewsets.action_base import EMRActionBaseViewSet

    return EMRActionBaseViewSet


def _evaluator():
    from care.action_evaluator.base import ActionEvaluator

    return ActionEvaluator


def _user_of(view):
    request = getattr(view, "request", None)
    return getattr(request, "user", None)


def _mark(wrapper, original):
    setattr(wrapper, MARKER, True)
    wrapper.ihr_original = original
    return wrapper


def _wrap_execute_action(original):
    # functools.wraps copies the function's __dict__: DRF's @action attributes (mapping,
    # detail, url_path, url_name, kwargs) stay, so the router still finds the route
    @functools.wraps(original)
    def execute_action(self, request, *args, **kwargs):
        if branches.closed(request.user):
            raise PermissionDenied(ACTIONS_REFUSED)
        return original(self, request, *args, **kwargs)

    return _mark(execute_action, original)


def _wrap_perform_actions(original):
    @functools.wraps(original)
    def perform_actions(self, instance):
        if branches.closed(_user_of(self)):
            return []
        return original(self, instance)

    return _mark(perform_actions, original)


def _wrap_evaluate(original):
    @functools.wraps(original)
    def evaluate(self):
        if branches.closed(self.user):
            return []
        return original(self)

    return _mark(evaluate, original)


def install():
    """Wrap the engine's three entry points once (IhrAccessConfig.ready())."""
    base, evaluator = _base(), _evaluator()
    if not getattr(base.execute_action, MARKER, False):
        base.execute_action = _wrap_execute_action(base.execute_action)
    if not getattr(base.perform_actions, MARKER, False):
        base.perform_actions = _wrap_perform_actions(base.perform_actions)
    if not getattr(evaluator.evaluate, MARKER, False):
        evaluator.evaluate = _wrap_evaluate(evaluator.evaluate)


@functools.cache
def reviewed():
    """{CARE viewset dotted path: verb} from fields/action_viewsets.txt."""
    return {
        name: verb
        for name, (verb, _reason) in guard.read_list(
            VIEWSETS, verbs=VIEWSET_VERBS
        ).items()
    }


def _care_name(cls):
    """The dotted path of the CARE class a (possibly swapped) viewset stands for: the first
    class of its MRO from CARE's code that mixes in the engine."""
    base = _base()
    for klass in cls.__mro__:
        if klass.__module__.startswith("care.") and issubclass(klass, base):
            return f"{klass.__module__}.{klass.__qualname__}"
    return f"{cls.__module__}.{cls.__qualname__}"


def action_viewsets():
    """{CARE dotted path: {view classes}} of every view of the hospital's URL configuration
    that mixes in ``EMRActionBaseViewSet``."""
    from django.urls import URLPattern, URLResolver, get_resolver

    base = _base()

    def walk(patterns):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                yield from walk(pattern.url_patterns)
            elif isinstance(pattern, URLPattern):
                yield pattern.callback

    found = {}
    for callback in walk(get_resolver().url_patterns):
        cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        if isinstance(cls, type) and issubclass(cls, base):
            found.setdefault(_care_name(cls), set()).add(cls)
    return found


def problems():
    """Why the action engine is not closed to our roles (ihr_access.E022)."""
    found = []
    base, evaluator = _base(), _evaluator()
    for name in WRAPPED:
        if not getattr(getattr(base, name), MARKER, False):
            found.append(
                f"EMRActionBaseViewSet.{name} is not our gate (actions.install)"
            )
    if not getattr(evaluator.evaluate, MARKER, False):
        found.append("ActionEvaluator.evaluate is not our gate (actions.install)")
    listed = reviewed()
    mixers = action_viewsets()
    for name in sorted(set(mixers) - set(listed)):
        found.append(
            f"{name} mixes in CARE's action engine and is not reviewed in "
            "fields/action_viewsets.txt"
        )
    for name in sorted(set(listed) - set(mixers)):
        found.append(
            f"fields/action_viewsets.txt names {name}, which no route serves any more"
        )
    for _name, classes in sorted(mixers.items()):
        for cls in sorted(classes, key=lambda c: c.__qualname__):
            for method in WRAPPED:
                if not getattr(getattr(cls, method), MARKER, False):
                    found.append(
                        f"{cls.__module__}.{cls.__qualname__}.{method} overrides our "
                        "action gate"
                    )
    return found
