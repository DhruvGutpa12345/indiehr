"""A placement refusal answers 400 with its reason, not 500 (P1-T11b1, review C3).

The placement trigger (placement.py) refuses a membership or care-team row with SQLSTATE 23514
and a message starting ``ihr_role_placement:``; Django raises that as an IntegrityError, which
CARE's handlers leave to a bare 500. Every view that writes these rows (facility and other
organisation users, a patient's ``add_user``) is one of CARE's EMR viewsets, whose handler is
``care.emr.api.viewsets.base.emr_exception_handler`` (``EMRBaseViewSet.get_exception_handler``
and the upsert path look it up there at call time; the batch view and the password view bind it
by name, and ``install()`` rebinds those too). ``install()`` wraps it: exactly that refusal
becomes a DRF ValidationError carrying the trigger's reason (``{"errors": [{"type":
"validation_error", "msg": ...}]}``, as CARE answers other validation errors). Every other
IntegrityError still raises (a 500). Checked by ihr_access.E022.

The handler runs inside the request's atomic block (ATOMIC_REQUESTS, on in every deployed
setting); it marks that block for rollback (DRF's ``set_rollback()``), so the failed transaction
is rolled back, never committed or reused.
"""

from django.db import IntegrityError
from rest_framework.exceptions import ValidationError
from rest_framework.views import set_rollback

PREFIX = "ihr_role_placement:"
CHECK_VIOLATION = "23514"
MARKER = "ihr_guard"


def placement_refusal(exc):
    """The trigger's reason when ``exc`` is a placement refusal, else None."""
    if not isinstance(exc, IntegrityError):
        return None
    cause = exc.__cause__
    if getattr(cause, "sqlstate", None) != CHECK_VIOLATION:
        return None
    diag = getattr(cause, "diag", None)
    message = getattr(diag, "message_primary", None) or ""
    if not message.startswith(PREFIX):
        return None
    return message[len(PREFIX) :].strip()


# CARE modules that bind ``emr_exception_handler`` by name at import (``from ... import``) and
# return it from a view's ``get_exception_handler`` (second review of C3): each name is
# rebound to the wrapper, so a refusal that reaches the batch view's own handler is a 400 too.
# A batch sub-request is handled by its own EMR view first (base's name, looked up at call
# time), so this is defence in depth; ``problems()`` checks every module listed here and that
# no other CARE module holds the unwrapped handler.
BOUND_BY_NAME = (
    "care.emr.api.viewsets.base",
    "care.emr.api.viewsets.batch_request",
    "care.users.api.viewsets.change_password",
)


def _modules():
    import importlib

    return [importlib.import_module(name) for name in BOUND_BY_NAME]


def install():
    from care.emr.api.viewsets import base

    original = base.emr_exception_handler
    if getattr(original, MARKER, False):
        original = original.ihr_original
    else:

        def emr_exception_handler(exc, context):
            reason = placement_refusal(exc)
            if reason is not None:
                # CARE's handler answers a ValidationError itself, without DRF's rollback
                # mark: the request's transaction is broken by the refusal and must not be
                # committed
                set_rollback()
                exc = ValidationError(reason)
            return original(exc, context)

        emr_exception_handler.ihr_original = original
        setattr(emr_exception_handler, MARKER, True)
        base.emr_exception_handler = emr_exception_handler
    wrapper = base.emr_exception_handler
    for module in _modules():
        if getattr(module, "emr_exception_handler", None) is original:
            module.emr_exception_handler = wrapper


def problems():
    """Why a placement refusal would not answer 400 (E022)."""
    import sys

    from care.emr.api.viewsets import base

    if not getattr(base.emr_exception_handler, MARKER, False):
        return ["care.emr.api.viewsets.base.emr_exception_handler is not wrapped"]
    original = base.emr_exception_handler.ihr_original
    found = [
        f"{module.__name__}.emr_exception_handler is not wrapped"
        for module in _modules()
        if not getattr(getattr(module, "emr_exception_handler", None), MARKER, False)
    ]
    # A CARE module added upstream that binds the unwrapped handler by name
    found += [
        f"{name}.emr_exception_handler is not wrapped (add it to BOUND_BY_NAME)"
        for name, module in sorted(sys.modules.items())
        if name.startswith("care.")
        and name not in BOUND_BY_NAME
        and getattr(module, "emr_exception_handler", None) is original
    ]
    return found
