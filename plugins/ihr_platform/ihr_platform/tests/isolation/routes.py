"""Enumerate the URL routes and fill their path parameters for one tenant."""

import re

from django.urls import URLPattern, URLResolver, get_resolver

PARAM_RE = re.compile(r"\(\?P<(\w+)>[^)]*\)")
SKIP_PREFIXES = ("admin/", "static/", "media/", "__debug__/")

# path parameter name -> model label used to find an id for it
PARAM_MODELS = {
    "facility_external_id": "facility.facility",
    "facility_id": "facility.facility",
    "patient_external_id": "emr.patient",
    "patient_id": "emr.patient",
    "encounter_external_id": "emr.encounter",
    "encounter_id": "emr.encounter",
    "organization_external_id": "emr.organization",
    "facility_organization_external_id": "emr.facilityorganization",
    "facility_organizations_external_id": "emr.facilityorganization",
    "location_external_id": "emr.facilitylocation",
    "account_external_id": "emr.account",
    "user_external_id": "users.user",
    "device_external_id": "emr.device",
    "token_queue_external_id": "emr.tokenqueue",
    "thread_external_id": "emr.notethread",
    "resource_external_id": "emr.resourcerequest",
}
# Parameters whose values are the same in every tenant (fixtures), so they can't carry a
# cross-tenant id: the cross-tenant probe ignores routes that use them.
SHARED_VALUE_PARAMS = {"slug", "username", "path"}


def _walk(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern), pattern.callback


def get_routes():
    """(regex route, view class or None, allows GET) for every route except format suffixes."""
    routes = []
    for route, callback in _walk(get_resolver().url_patterns):
        if "(?P<format>" in route or route.startswith(SKIP_PREFIXES):
            continue
        cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        actions = getattr(callback, "actions", None)
        if actions is not None:
            allows_get = "get" in actions
        else:
            allows_get = cls is None or hasattr(cls, "get")
        routes.append((route, cls, allows_get))
    return routes


def fill(route, cls, tenant, *, ids_from=None, external_id_from=None):
    """Turn a route regex into a concrete path for ``tenant``, or None if it can't be filled.

    ``ids_from`` takes every id parameter from another tenant; ``external_id_from`` only the
    route's own ``external_id`` (the parent ids stay ``tenant``'s).
    """
    ids_by_model = (ids_from or tenant).first_id_by_model
    path = route.lstrip("^").replace("^", "").replace("$", "").replace("\\.", ".")
    model = getattr(cls, "database_model", None)
    label = model._meta.label_lower if model is not None else None
    for name in PARAM_RE.findall(path):
        if name == "external_id":
            source = (
                external_id_from.first_id_by_model if external_id_from else ids_by_model
            )
            value = source.get(label) if label else None
        elif name == "slug":
            value = tenant.first_slug_by_model.get(label) if label else None
        elif name == "username":
            value = tenant.username
        else:
            value = ids_by_model.get(PARAM_MODELS.get(name, ""))
        if not value:
            return None
        path = PARAM_RE.sub(value, path, count=1)
    if "(" in path or "?" in path:
        return None
    return "/" + path
