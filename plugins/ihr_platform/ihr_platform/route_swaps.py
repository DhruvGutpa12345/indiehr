"""Our classes in place of CARE's in CARE's own router (P1-T20a, review A4).

CARE's ``config/api_router.py`` registers each viewset on a DRF router and builds its URLs when
it is imported. ``install()`` runs from ``IhrPlatformConfig.ready()``, before any URL
configuration is imported, and makes every registration of one of CARE's classes in ``SWAPS``
register ours instead. So every route of that viewset reaches our class: list and detail, the
format suffixes DEBUG adds (``users.json``), and batch sub-requests, which resolve through the
same URL configuration. Shadowing a route string in ``urls_tenant.py`` would miss the variants.

Classes are named, not imported, here: importing CARE's viewset modules from ``ready()`` hits an
import cycle in CARE (resources.permissions). Ours are imported when CARE registers its own,
which is after ``config/api_router.py`` has imported everything in CARE's order.

``check()`` confirms the swap on CARE's live routers. ``require()`` runs it at the end of
``urls_tenant.py``, so a process that would serve CARE's own class (an upstream sync moved or
renamed one) never builds its URLs: every web worker, batch sub-request and command that
resolves a URL refuses. ``system_check`` does the same for ``manage.py check`` and every
command that runs the checks (migrate, the start-up one-shot) (review C4).
"""

import sys

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from rest_framework.routers import BaseRouter

URLCONF_MODULES = ("config.api_router", "config.urls", "ihr_platform.urls_tenant")
# CARE's class -> ours (a subclass of it)
SWAPS = {
    "care.emr.api.viewsets.facility.FacilitySchedulableUsersViewSet": (
        "ihr_platform.public_routes.PublicSchedulableUsersViewSet"
    ),
    "care.emr.api.otp_viewsets.slot.OTPSlotViewSet": (
        "ihr_platform.public_routes.PublicOTPSlotViewSet"
    ),
    "care.emr.api.viewsets.organization.OrganizationPublicViewSet": (
        "ihr_platform.public_routes.GovtOrganizationPublicViewSet"
    ),
    "care.emr.api.viewsets.facility.AllFacilityViewSet": (
        "ihr_platform.public_routes.PublicFacilityViewSet"
    ),
    "care.emr.api.viewsets.batch_request.BatchRequestView": (
        "ihr_platform.batch.IhrBatchRequestView"
    ),
    # "Add user" makes an invitation (P1-T5e1); before P1-T20a only the list route was ours
    "care.emr.api.viewsets.user.UserViewSet": (
        "ihr_identity.invitations.InvitingUserViewSet"
    ),
}
# {CARE's class: ours}, filled as CARE registers them
swapped = {}
_state = {"installed": False}


def _dotted(cls):
    return f"{cls.__module__}.{cls.__qualname__}"


def install():
    if _state["installed"]:
        return
    if loaded := [m for m in URLCONF_MODULES if m in sys.modules]:
        msg = (
            f"ihr_platform: {', '.join(loaded)} imported before the route swap; CARE's own "
            "public views would be served (P1-T20a)"
        )
        raise ImproperlyConfigured(msg)
    register = BaseRouter.register

    def swapped_register(self, prefix, viewset, basename=None):
        target = SWAPS.get(_dotted(viewset)) if isinstance(viewset, type) else None
        if target is not None:
            ours = import_string(target)
            if not issubclass(ours, viewset):
                msg = f"ihr_platform: {target} must subclass {_dotted(viewset)}"
                raise ImproperlyConfigured(msg)
            swapped[viewset] = ours
            viewset = ours
        return register(self, prefix, viewset, basename)

    swapped_register.ihr_original = register
    BaseRouter.register = swapped_register
    _state["installed"] = True


def check():
    """Problems: a CARE class in SWAPS that was never registered, or a router entry that
    still names one."""
    from config import api_router

    problems = []
    if not _state["installed"]:
        problems.append("route swap not installed")
    if missing := sorted(set(SWAPS) - {_dotted(c) for c in swapped}):
        problems.append(f"never registered by CARE: {missing}")
    for name, router in vars(api_router).items():
        if not isinstance(router, BaseRouter):
            continue
        for prefix, viewset, _basename in router.registry:
            if _dotted(viewset) in SWAPS:
                problems.append(f"{name}: {prefix} still serves {viewset.__name__}")
    return problems


def require():
    """Refuse to go on when ``check()`` finds a problem (the end of urls_tenant.py)."""
    if problems := check():
        msg = "ihr_platform: CARE's own views would be served: " + "; ".join(problems)
        raise ImproperlyConfigured(msg)


def system_check(app_configs=None, **kwargs):
    from django.core.checks import Error

    return [
        Error(f"CARE's own views would be served: {problem}", id="ihr_platform.E020")
        for problem in check()
    ]
