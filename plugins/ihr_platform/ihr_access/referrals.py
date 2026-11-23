"""CARE's referrals, closed to ``branches.closed`` users: the restricted roles and users of our
roles only (P2-T0a; owner, Q-K163).

A referral names the patient and the visit, and carries the clinical reason (title and note),
a note thread, form answers, attached records and files. CARE opens it with
``can_read_referral`` / ``can_write_referral`` / ``can_create_referral``, which CARE's own
role-organisation roles "Member" and "Manager" hold as well. Those slugs are not clinical
(registry.CLINICAL_SLUGS, Q-K163: a Receptionist who is also a Member stays restricted), so the
restricted rule alone would leave such a person CARE's referral answers. Three gates close them:

- ``AssignedReferralViewSet`` (GET /api/v1/referral_assigned/, care/emr/api/viewsets/referral.py)
  lists every referral whose ``assignee_users`` holds the caller, with no permission check at
  all: the list is empty for ``branches.closed`` users (as CARE's device list for them,
  views.py), and for anyone none of whose roles here grants ``can_read_referral`` (review A1:
  CARE lets any active user be put on a referral's care team, so a person whose memberships
  were removed, or CARE's Pharmacist or Volunteer, would read the referrals assigned to them).
  ``install()`` wraps the class's own ``get_queryset``.
- Every other referral viewset served (``GATED``: the referral itself with its scoped lists,
  detail, ``set_assignees``, ``referenced_by``, ``questionnaires`` and ``thread``; its notes,
  form answers and attached resources with ``resolve``) answers 403 to them in ``initial``,
  after authentication and before CARE reads a body.
- CARE's referral checks (``CHECKS``) answer False to them through ``ReferralGateAuthorizer``,
  an override controller: CARE's file links (``file_authorizer``, file_type ``referral``) and
  resource references (care/emr/utils/resource_reference.py) ask them too.

CARE's own roles keep CARE's answers (each check delegates to CARE's handler). Referral
availability (who accepts referrals: a setting with no patient) keeps CARE's answers.
``problems()`` (ihr_access.E022) fails the start if a wrapper is gone, a referral view served
anywhere in the hospital's URL configuration (any router, nested or not, or a plugin's urlconf:
review A2) is not gated or not reviewed here, or a check no longer resolves to our authorizer.
"""

import functools

from ihr_access import branches

MARKER = "ihr_assigned_referrals"
GATE = "ihr_referral_gate"
REFUSED = "Your role cannot open or change referrals."
MODULE = "care.emr.api.viewsets.referral"
# CARE's referral models: a view of one of these is a referral view wherever it is defined
MODELS_MODULE = "care.emr.models.referral"
READ = "can_read_referral"
# CARE's referral viewsets refused in ``initial`` (config/api_router.py: referral, and the
# nested notes, questionnaire_responses and resources)
GATED = (
    "ReferralViewSet",
    "ReferralNoteMessageViewSet",
    "ReferralQuestionnaireResponseViewSet",
    "ReferralResourceViewSet",
)
# Served from CARE's referral module and reviewed as not gated here, each only at its own
# top-level route: the assigned list (emptied above) and referral availability (a setting
# with no patient)
NOT_GATED = {
    "AssignedReferralViewSet": "referral_assigned",
    "ReferralAvailabilityViewSet": "referral_availability",
    "ReferralAvailabilityConfigViewSet": "referral_availability_config",
}
# CARE's referral checks (care/security/authorization/referral.py), False for closed users
CHECKS = (
    "can_create_referral",
    "can_read_referral",
    "can_write_referral",
    "can_list_entity_referrals",
    "can_list_encounter_referrals",
    "can_list_patient_referrals",
)


def _viewset():
    from care.emr.api.viewsets.referral import AssignedReferralViewSet

    return AssignedReferralViewSet


def install():
    """Install the three gates once (IhrAccessConfig.ready())."""
    _install_assigned()
    _install_gates()
    _install_authorizer()


def _install_assigned():
    """Wrap ``AssignedReferralViewSet.get_queryset``."""
    cls = _viewset()
    original = cls.__dict__.get("get_queryset")
    if original is None or getattr(original, MARKER, False):
        return

    @functools.wraps(original)
    def get_queryset(self):
        queryset = original(self)
        user = self.request.user
        if branches.closed(user) or not branches.may_hold(user, READ):
            return queryset.none()
        return queryset

    setattr(get_queryset, MARKER, True)
    get_queryset.ihr_original = original
    cls.get_queryset = get_queryset


def _gated(cls):
    """``cls.initial`` that refuses closed users after CARE's own steps (authentication, the
    visit check: the next ``initial`` in the class's MRO, looked up per call)."""
    own = cls.__dict__.get("initial")

    def initial(self, request, *args, **kwargs):
        if own is not None:
            own(self, request, *args, **kwargs)
        else:
            super(cls, self).initial(request, *args, **kwargs)
        if branches.closed(request.user):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(REFUSED)

    initial.__qualname__ = f"{cls.__qualname__}.initial"
    setattr(initial, GATE, True)
    initial.ihr_original = own
    return initial


def _install_gates():
    """Refuse every action of each ``GATED`` viewset to closed users, in ``initial``."""
    import importlib

    module = importlib.import_module(MODULE)
    for name in GATED:
        cls = getattr(module, name)
        if getattr(cls.__dict__.get("initial"), GATE, False):
            continue
        cls.initial = _gated(cls)


def _closed_check(name):
    def check(self, user, *args, **kwargs):
        if branches.closed(user):
            return False
        return getattr(self.delegates[name](), name)(user, *args, **kwargs)

    check.__name__ = name
    check.__qualname__ = f"ReferralGateAuthorizer.{name}"
    setattr(check, GATE, True)
    return check


ReferralGateAuthorizer = None


def _install_authorizer():
    """Register ``ReferralGateAuthorizer`` (each check delegates to the class CARE resolved it
    to before us). CARE's controller builds its name map lazily and ignores a later
    registration, so a map already built is rebuilt (as authz.install_grant_authorizer)."""
    from care.security.authorization.base import (
        AuthorizationController,
        AuthorizationHandler,
    )

    from ihr_access.authz import care_handler

    global ReferralGateAuthorizer  # noqa: PLW0603 (one class per process, built in ready())
    if ReferralGateAuthorizer is not None:
        return
    namespace = {
        "__doc__": "CARE's referral answer, or False for a branches.closed user.",
        "__module__": __name__,
        "delegates": {name: care_handler(name) for name in CHECKS},
        GATE: True,
    }
    for name in CHECKS:
        namespace[name] = _closed_check(name)
    authorizer = type("ReferralGateAuthorizer", (AuthorizationHandler,), namespace)
    AuthorizationController.register_override_controller(authorizer)
    ReferralGateAuthorizer = authorizer
    if AuthorizationController.cache["actions"]:
        AuthorizationController.build_cache()


def _views():
    """(route, view class) of every view of the hospital's URL configuration (as
    actions.action_viewsets walks it)."""
    from django.urls import URLPattern, URLResolver, get_resolver

    def walk(patterns, prefix):
        for pattern in patterns:
            route = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                yield from walk(pattern.url_patterns, route)
            elif isinstance(pattern, URLPattern):
                callback = pattern.callback
                cls = getattr(callback, "cls", None) or getattr(
                    callback, "view_class", None
                )
                if isinstance(cls, type):
                    yield route, cls

    yield from walk(get_resolver().url_patterns, "")


def _is_referral(route, cls):
    """A class from CARE's referral module (or built on one), a view of a referral model, or
    any view on a referral route."""
    model = getattr(cls, "database_model", None)
    return (
        any(base.__module__ == MODULE for base in cls.__mro__)
        or getattr(model, "__module__", None) == MODELS_MODULE
        or "referral" in route
    )


def _top_level(route, name):
    """``route`` is under ``api/v1/<NOT_GATED[name]>`` (the router's list, detail and format
    patterns), not nested under another route or mounted elsewhere."""
    import re

    head = "api/v1/"
    if not route.lstrip("^").startswith(head):
        return False
    tail = route.lstrip("^")[len(head) :]
    prefix = re.escape(NOT_GATED[name])
    return re.match(rf"\^?{prefix}(?![A-Za-z0-9_])", tail) is not None


def _served():
    """(route, view class) of every referral view served (``_is_referral``), from any router
    or urlconf."""
    seen = set()
    for route, cls in _views():
        if _is_referral(route, cls) and (route, cls) not in seen:
            seen.add((route, cls))
            yield route, cls


def problems():
    """Why the referral gates do not hold (ihr_access.E022)."""
    from care.security.authorization.base import AuthorizationController

    from ihr_access.authz import care_handler

    found = []
    method = _viewset().__dict__.get("get_queryset")
    if method is None or not getattr(method, MARKER, False):
        found.append(
            "AssignedReferralViewSet.get_queryset is not our gate (referrals.install)"
        )
    served = set()
    for route, viewset in _served():
        name = viewset.__name__
        care = viewset.__module__ == MODULE
        served.add(name)
        if care and name in NOT_GATED:
            if not _top_level(route, name):
                found.append(
                    f"{name} is reviewed as not gated only at {NOT_GATED[name]!r}, and "
                    f"route {route!r} serves it"
                )
            continue
        if not care or name not in GATED:
            found.append(
                f"referral route {route!r} serves {viewset.__module__}.{name}, which "
                "referrals.py has not reviewed (GATED or NOT_GATED)"
            )
        if not getattr(getattr(viewset, "initial", None), GATE, False):
            found.append(f"{name}.initial is not our referral gate (referrals.install)")
    if missing := sorted(set(GATED) - served):
        found.append(f"referral viewsets no longer served: {missing}")
    authorizer = ReferralGateAuthorizer
    if authorizer is None:
        found.append(
            "the referral gate authorizer is not registered (referrals.install)"
        )
        return found
    built = AuthorizationController.cache["actions"]
    for name in CHECKS:
        resolved = built.get(name) if built else care_handler(name)
        if resolved is not authorizer:
            found.append(
                f"{name} resolves to {getattr(resolved, '__name__', resolved)}, not the "
                "referral gate authorizer"
            )
        delegate = authorizer.delegates.get(name)
        if delegate is None or delegate is authorizer:
            found.append(f"{name}: CARE has no handler of its own for it any more")
    return found
