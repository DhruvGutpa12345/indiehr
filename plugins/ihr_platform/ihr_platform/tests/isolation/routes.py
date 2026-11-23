"""Enumerate the URL routes and fill their path parameters for one tenant."""

import re

from django.urls import URLPattern, URLResolver, get_resolver
from django.urls.converters import get_converters

PARAM_RE = re.compile(r"\(\?P<(\w+)>[^)]*\)")
# path() converters such as <int:pk> or <username>. The look-behind keeps it off the <name> of a
# regex group "(?P<name>...)"; fill() also runs it only after the regex groups are filled.
CONVERTER_RE = re.compile(r"(?<!\?P)<(?:(?P<conv>\w+):)?(?P<name>\w+)>")
# DRF's format-suffix variants, in regex and in converter form: the same views as their routes,
# because a router builds both from one registry, where our replacements of CARE's views are
# swapped in (ihr_platform/route_swaps.py, P1-T20a). A route shadowed by its string in
# urls_tenant.py would not cover its variant: blocked.py blocks those explicitly.
FORMAT_RE = re.compile(r"\(\?P<format>|<(?:\w+:)?format>")
# Regex metacharacters that must not survive fill() (see the end of fill()).
LEFTOVER_RE = re.compile(r"[()?<>\[\]\\*+{}|]")
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
    "referral_external_id": "emr.referral",  # CARE 3.1 (P2-T0a)
}
# Parameters whose values are the same in every tenant (fixtures), so they can't carry a
# cross-tenant id: the cross-tenant probe ignores routes that use them. Converter routes
# (<int:pk>, <str:pincode>, ...) never enter that probe at all: it takes only routes with a regex
# "(?P<external_id>" group. Their cross-tenant checks are their own tests.
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
        if FORMAT_RE.search(route) or route.startswith(SKIP_PREFIXES):
            continue
        cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        actions = getattr(callback, "actions", None)
        # A route's own as_view(http_method_names=...): Django views keep it in view_initkwargs,
        # DRF viewsets in initkwargs, function views have neither.
        initkwargs = (
            getattr(callback, "view_initkwargs", None)
            or getattr(callback, "initkwargs", None)
            or {}
        )
        methods = initkwargs.get("http_method_names")
        if actions is not None:
            allows_get = "get" in actions
        else:
            allows_get = (cls is None or hasattr(cls, "get")) and (
                methods is None or "get" in methods
            )
        routes.append((route, cls, allows_get))
    return routes


WRITE_METHODS = ("post", "put", "patch", "delete")


def get_route_views():
    """(regex route, view class or None, methods) for the same routes as ``get_routes()``:
    ``methods`` are the HTTP methods the route answers (lower case), from a viewset's actions
    or a class view's handlers; None for a function view, whose methods cannot be read
    (P1-T11b3's role matrix sends every method of a class view)."""
    found = []
    for route, callback in _walk(get_resolver().url_patterns):
        if FORMAT_RE.search(route) or route.startswith(SKIP_PREFIXES):
            continue
        cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        actions = getattr(callback, "actions", None)
        initkwargs = (
            getattr(callback, "view_initkwargs", None)
            or getattr(callback, "initkwargs", None)
            or {}
        )
        allowed = initkwargs.get("http_method_names") or ("get", *WRITE_METHODS)
        if actions is not None:
            methods = tuple(m for m in ("get", *WRITE_METHODS) if m in actions)
        elif cls is not None:
            methods = tuple(
                m for m in ("get", *WRITE_METHODS) if hasattr(cls, m) and m in allowed
            )
        else:
            methods = None
        found.append((route, cls, methods))
    return found


def _model(cls):
    """The view's model: CARE's ``database_model``, else a plain DRF ``queryset``'s."""
    model = getattr(cls, "database_model", None)
    if model is None:
        model = getattr(getattr(cls, "queryset", None), "model", None)
    return model


# Converters whose value depends on the route: (route, converter name) -> the tenant's
# attribute holding it. The reference pack item world.py seeds waiting (P1-T15b1); a tenant
# object without that attribute (the role matrix's) gets the converter's general value.
ROUTE_CONVERTER_VALUES = {
    ("api/ihr/packs/updates/<int:pk>/", "pk"): "pack_item_id",
    # The staff screen's action (P1-T24b, attack A1): the role matrix sends "reset-sign-in" so
    # its write crawl proves a facility-only admin is refused; the isolation world has no such
    # attribute and leaves the route unfilled, as before.
    ("api/ihr/staff/<str:username>/<slug:action>/", "action"): "staff_action",
}


def _converter_value(name, tenant, route=""):
    attribute = ROUTE_CONVERTER_VALUES.get((route.lstrip("^/"), name))
    if attribute and getattr(tenant, attribute, ""):
        return getattr(tenant, attribute)
    return {
        "username": tenant.username,
        "pk": tenant.audit_entry_id,
        "pincode": tenant.pincode,
    }.get(name)


def fill(route, cls, tenant, *, ids_from=None, external_id_from=None):
    """Turn a route regex into a concrete path for ``tenant``, or None if it can't be filled.

    ``ids_from`` takes every id parameter from another tenant; ``external_id_from`` only the
    route's own ``external_id`` (the parent ids stay ``tenant``'s).
    """
    ids_by_model = (ids_from or tenant).first_id_by_model
    path = route.lstrip("^").replace("^", "").replace("$", "").replace("\\.", ".")
    model = _model(cls)
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
    while match := CONVERTER_RE.search(path):
        value = _converter_value(match["name"], tenant, route)
        converter = get_converters().get(match["conv"] or "str")
        if not value or converter is None:
            return None
        if not re.fullmatch(converter.regex, value):
            return None  # a value its own converter would refuse: never request it
        path = path[: match.start()] + value + path[match.end() :]
    # Fail closed: a placeholder or any other regex syntax left in the path ([0-9]+, \d, *, {n},
    # |, ...) is a route not filled, so it is counted as skipped (and must be listed in
    # crawl_skips.txt), never requested as a literal. "." is left out: "\." became a literal dot
    # above, and no filled value (UUID, PIN, username, audit id) holds any of these characters.
    if LEFTOVER_RE.search(path):
        return None
    return "/" + path
