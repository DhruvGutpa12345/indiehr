"""The role x endpoint matrix and the clinical canary walk (P1-T11b3; design-b.md §6, "After the
attack" §9 and §10 items 12-20). Not collected (no ``test`` prefix): test_role_matrix.py runs it.

Every route of the hospital's URL configuration is enumerated by the isolation crawl's own
``routes.py`` (``get_route_views``, ``fill``), never a second enumerator. Each GET route is
requested as each matrix role, signed in through the real session cookie (P1-T4a): the
restricted roles of ours (registry.RESTRICTED), the Radiologist, and CARE's Doctor and Facility
Admin as the comparison. ``list_params.txt`` adds the query and path parameters CARE's lists
need, each line one more request of its route (its key is the route plus the line's
assignments). Each answer is classified:

- ``allow``: 200 with at least one row, and nothing in it was projected by the guard;
- ``project``: 200 with at least one row, and the guard projected a shape in it;
- ``empty``: 200 with no row;
- ``deny``: 401, 403, 404 or 405;
- ``invalid``: 400 (the row's reason says why);
- ``conflict`` (409) and ``unavailable`` (503): the two answers CARE and we give where that is
  the answer itself (a username already taken; the PIN directory not loaded);
- ``redirect``: 3xx (the sign-in flow);
- ``skip``: not requested (a route ``fill()`` cannot fill, or one that must not be called);
- anything else (a 5xx above all) never matches a row: a server error can hide a leak.

``role_matrix.txt`` holds the expected outcome of every (route key, role), compactly: role
groups (``restricted:deny``) and later per-role exceptions (``rec:project``). Every non-GET
method of a route ("W" rows; default ``ours:deny care:care``) is sent, as each of our roles
whose outcome is ``deny``, with an empty JSON body: it must not answer 2xx (or 5xx). An
``allow`` write row is not sent and must name its tests. There is no write crawl of CARE's own
roles (``care``).

Each request runs in a savepoint that is rolled back, so no answer changes the next one.

Review fixes (P1-T11b3): every GET route under a patient is also requested with
``?encounter=encounter_b`` (``CROSS``: another patient's visit, K1/K2); the query keys the
view's own code reads are recorded per route (``QueryReads``, M2); ``post_reads.txt`` sends the
reads CARE makes by POST, with bodies, as every role (K3); ``role_matrix_writes.txt`` snapshots
how each denied write answered (M3); a guarded shape embedded in an answer is collapsed in the
key snapshot only when the guard handled that instance (K4).
"""

import contextlib
import datetime
import fnmatch
import importlib
import json
import re
import sys
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlencode

from django.apps import apps
from django.db import connection, transaction
from django.http.request import QueryDict
from django.utils import timezone
from ihr_identity.blocked import Blocked
from ihr_platform.tests.isolation.routes import (
    PARAM_MODELS,
    PARAM_RE,
    _model,
    fill,
    get_route_views,
)

from ihr_access import guard
from ihr_access.testing import CANARY

HERE = Path(__file__).parent
MATRIX = HERE / "role_matrix.txt"
KEYS = HERE / "role_matrix_keys.txt"
PARAMS = HERE / "list_params.txt"
EXEMPTIONS = HERE / "canary_exemptions.txt"
WRITES = HERE / "role_matrix_writes.txt"
POST_READS = HERE / "post_reads.txt"
# Path parameters the matrix fills beyond the isolation crawl's PARAM_MODELS (review M5); kept
# here so the isolation crawl's own route set does not change
MATRIX_PARAMS = {"schedule_external_id": "emr.schedule"}
# Every GET route under a patient is also requested naming another patient's visit (review
# K1/K2): CARE authorizes some readers through ?encounter= without checking whose visit it is
PATIENT_PARAM = "{patient_external_id}"
CROSS = "?encounter=encounter_b"
# Query keys the runtime read check ignores: paging and ordering, and CARE's generic list
# narrowing mixins (tags, a person's favourites list), which filter rows and never authorize
PAGING_KEYS = frozenset(
    {
        "limit",
        "offset",
        "ordering",
        "page",
        "page_size",
        "cursor",
        "tags",
        "tags_behavior",
        "favorite_list",
    }
)

# short name -> the world's user (ihr_access.testing.PLACES and add_matrix_world)
ROLES = {
    "rec": "Receptionist",
    "cash": "Cashier",
    "tpa": "TPA Desk",
    "owner": "Owner",
    "mrd": "MRD",
    "lab": "Lab Technician",
    "store": "Store Keeper",
    "rad": "Radiologist",
    "doctor": "Doctor",
    "admin": "Facility Admin",
}
# crawled as their own user, expected exactly as the role named: the Nurse who moved to the
# front desk keeps a PatientUser row, which must count for nothing ("After the attack" §3)
ALIASES = {"moved": "rec"}
ALIAS_USERS = {"moved": "Moved Nurse"}
RESTRICTED = ("rec", "cash", "tpa", "owner", "mrd", "lab", "store")
OURS = (*RESTRICTED, "rad")
CARE = ("doctor", "admin")
GROUPS = {
    "all": (*OURS, *CARE),
    "restricted": RESTRICTED,
    "ours": OURS,
    "frontdesk": ("rec", "cash", "tpa", "mrd"),
    "billing": ("cash", "tpa"),
    "care": CARE,
    "clinical": ("rad", *CARE),
}
GET_OUTCOMES = frozenset(
    {
        "allow",
        "project",
        "empty",
        "deny",
        "invalid",
        "conflict",
        "unavailable",
        "redirect",
        "skip",
    }
)
WRITE_OUTCOMES = frozenset({"deny", "allow", "care", "skip"})
WRITE_DEFAULT = "ours:deny care:care"
METHOD_TOKENS = frozenset({"GET", "W", "POST", "PUT", "PATCH", "DELETE"})
# Which model a path parameter takes its id from (beyond the isolation crawl's PARAM_MODELS)
APPS = {"emr", "facility", "users", "security"}
MARKER_RE = re.compile(re.escape(CANARY) + r"(?:-([a-z0-9]+(?:-[a-z0-9]+)*))?")
WRITE_CLASSES = {
    401: "auth",
    403: "auth",
    404: "auth",
    405: "auth",
    400: "body",
    409: "conflict",
}
TESTS_RE = re.compile(r"tests:\s*([\w.,\s]+)")


def norm(route):
    """The route as the files name it: regex groups as ``{name}``, anchors dropped."""
    path = "/" + route
    path = PARAM_RE.sub(lambda m: "{" + m.group(1) + "}", path)
    return path.replace("^", "").replace("$", "")


def _lines(path):
    for number, raw in enumerate(Path(path).read_text().splitlines(), 1):
        body, _, reason = raw.partition("#")
        body = body.strip()
        if body:
            yield number, body.split(), reason.strip()


def expand(tokens, where):
    """{role: outcome} from ``group:outcome`` tokens (later ones win)."""
    out = {}
    for token in tokens:
        names, _, outcome = token.rpartition(":")
        if not names:
            msg = f"{where}: bad token {token!r}"
            raise ValueError(msg)
        for name in names.split(","):
            if name in GROUPS:
                roles = GROUPS[name]
            elif name in ROLES:
                roles = (name,)
            else:
                msg = f"{where}: unknown role or group {name!r}"
                raise ValueError(msg)
            for role in roles:
                out[role] = outcome
    return out


@dataclass
class Row:
    method: str  # GET or W
    key: str
    outcomes: dict
    reason: str
    tests: tuple = ()


def read_matrix(path=MATRIX):
    rows = {}
    for number, parts, reason in _lines(path):
        where = f"{path.name}:{number}"
        methods = parts[0].split(",") if parts else []
        if len(parts) < 3 or not methods or not set(methods) <= METHOD_TOKENS:
            msg = f"{where}: expected 'GET|W|POST,PUT,... <route> [?params] tokens  # why'"
            raise ValueError(msg)
        if "GET" in methods and len(methods) > 1:
            msg = f"{where}: GET stands alone"
            raise ValueError(msg)
        route = parts[1]
        rest = parts[2:]
        key = route
        if rest and rest[0].startswith("?"):
            key = f"{route} {rest[0]}"
            rest = rest[1:]
        if not reason:
            msg = f"{where}: no reason"
            raise ValueError(msg)
        outcomes = expand(rest, where)
        get = methods == ["GET"]
        allowed = GET_OUTCOMES if get else WRITE_OUTCOMES
        if bad := {o for o in outcomes.values() if o not in allowed}:
            msg = f"{where}: unknown outcome {sorted(bad)}"
            raise ValueError(msg)
        if missing := set(GROUPS["all"]) - set(outcomes):
            if get:
                msg = f"{where}: no outcome for {sorted(missing)}"
                raise ValueError(msg)
            outcomes = {**expand(WRITE_DEFAULT.split(), where), **outcomes}
        tests = ()
        if found := TESTS_RE.search(reason):
            tests = tuple(t.strip() for t in found.group(1).split(",") if t.strip())
        for method in methods:
            if (method, key) in rows:
                msg = f"{where}: {method} {key} listed twice"
                raise ValueError(msg)
            rows[(method, key)] = Row(method, key, outcomes, reason, tests)
    return rows


NONE_RE = re.compile(r"none(?::([a-z_]+(?:,[a-z_]+)*))?")


def read_params(path=PARAMS):
    """{route: [assignment string]}: ``?a=obj&b~text`` variants, or ``none[:k1,k2]`` (no extra
    request; the optional keys the route reads, each reviewed: review NEW-2, a key read on the
    route that no line names fails RoleMatrix.test_every_query_read_has_a_params_line)."""
    params = {}
    for number, parts, reason in _lines(path):
        where = f"{path.name}:{number}"
        if len(parts) != 2 or not reason:
            msg = f"{where}: expected '<route> ?a=obj&b=obj|none[:key,...]  # why'"
            raise ValueError(msg)
        route, assignment = parts
        if assignment.startswith("none"):
            if not NONE_RE.fullmatch(assignment):
                msg = f"{where}: expected 'none' or 'none:key1,key2'"
                raise ValueError(msg)
        elif not assignment.startswith("?"):
            msg = f"{where}: assignments start with '?'"
            raise ValueError(msg)
        params.setdefault(route, []).append(assignment)
    return params


def named_keys(lines):
    """The query keys a route's list_params.txt lines name: each variant's keys (``{param}``
    path assignments aside) and each ``none:`` line's reviewed keys."""
    found = set()
    for line in lines:
        if line.startswith("none"):
            keys = NONE_RE.fullmatch(line).group(1)
            found |= set(keys.split(",")) if keys else set()
            continue
        for part in line[1:].split("&"):
            name = re.split(r"[=~]", part, maxsplit=1)[0]
            if not name.startswith("{"):
                found.add(name)
    return found


def is_variant(line):
    return not line.startswith("none")


@dataclass
class Exemption:
    role: str
    marker: str
    pattern: str
    verb: str  # allow | never
    reason: str
    line: int = 0

    def matches(self, key):
        return fnmatch.fnmatchcase(key, self.pattern)


def read_exemptions(path=EXEMPTIONS):
    found = []
    for number, parts, reason in _lines(path):
        where = f"{path.name}:{number}"
        if len(parts) != 4 or parts[3] not in ("allow", "never") or not reason:
            msg = f"{where}: expected '<who> <marker> <key glob> allow|never  # why'"
            raise ValueError(msg)
        who, marker, pattern, verb = parts
        for role in expand([f"{who}:x"], where):
            found.append(Exemption(role, marker, pattern, verb, reason, number))
    return found


def read_keys(path=KEYS):
    """{(route key, key path): frozenset(roles)}. Sections: ``[<route key>] <roles>`` then one
    key path per line (``a.b``, ``a[].b``, ``[].a`` for a list body, ``a.b<shape>``)."""
    snapshot = {}
    section = None
    for number, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith(("[/", "[POST ")):  # a path of a list body starts "[]."
            key, sep, roles = line[1:].partition("] ")
            if not sep or not roles.strip():
                msg = f"{path.name}:{number}: expected '[<route key>] <roles>'"
                raise ValueError(msg)
            section = (key, frozenset(roles.strip().split(",")))
            continue
        if section is None or " " in line:
            msg = f"{path.name}:{number}: a key path outside a section"
            raise ValueError(msg)
        key, roles = section
        snapshot[(key, line)] = snapshot.get((key, line), frozenset()) | roles
    return snapshot


def resolve_test(label):
    """True if ``label`` (module.Class.method) names a test method."""
    module, cls, method = label.rsplit(".", 2)
    try:
        found = getattr(importlib.import_module(module), cls)
    except (ImportError, AttributeError):
        return False
    return callable(getattr(found, method, None))


def filling():
    """``fill()`` and ``variant()`` with the matrix's own path parameters as well."""
    return mock.patch.dict(PARAM_MODELS, MATRIX_PARAMS)


def read_writes(path=WRITES):
    """{(METHOD, route key): {viewer: class}}: how each denied write answered (review M3).
    One line per method and route: ``<METHOD> <route key>  <class>:<viewer>,...``; classes
    ``auth`` (401/403/404/405: refused by authorization), ``body`` (400: CARE validated the empty
    body first; whether the role holds the write is ihr_roles ExactGrants'), ``conflict``."""
    found = {}
    for number, parts, _reason in _lines(path):
        where = f"{path.name}:{number}"
        if len(parts) < 3 or parts[0] not in METHOD_TOKENS - {"GET", "W"}:
            msg = f"{where}: expected '<METHOD> <route key> <class>:<viewer>,...'"
            raise ValueError(msg)
        cells = {}
        for token in parts[2:]:
            kind, _, viewers = token.partition(":")
            if not viewers:
                msg = f"{where}: bad token {token!r}"
                raise ValueError(msg)
            for viewer in viewers.split(","):
                cells[viewer] = kind
        if (parts[0], parts[1]) in found:
            msg = f"{where}: listed twice"
            raise ValueError(msg)
        found[(parts[0], parts[1])] = cells
    return found


def write_class(code):
    return WRITE_CLASSES.get(code, str(code))


@dataclass
class PostRead:
    label: str
    route: str
    assignment: str  # list_params-style: query and path parameters
    body: object  # JSON, "@obj[.attr][:n]" and "@today[+n]" strings resolved per world
    outcomes: dict
    reason: str
    line: int = 0

    @property
    def key(self):
        return f"POST {self.route} {self.label}"


def read_post_reads(path=POST_READS):
    """post_reads.txt (review K3): ``<label> <route> [?assignments] <JSON body> <who>:<outcome>
    ...  # why`` (the body compact, no spaces)."""
    found = []
    for number, parts, reason in _lines(path):
        where = f"{path.name}:{number}"
        if len(parts) < 4 or not reason:
            msg = f"{where}: expected '<label> <route> [?a=obj] <body> <who>:<outcome>  # why'"
            raise ValueError(msg)
        label, route, rest = parts[0], parts[1], parts[2:]
        assignment = ""
        if rest[0].startswith("?"):
            assignment, rest = rest[0], rest[1:]
        try:
            body = json.loads(rest[0])
        except ValueError as e:
            msg = f"{where}: the body is not JSON ({e})"
            raise ValueError(msg) from e
        outcomes = expand(rest[1:], where)
        if missing := set(GROUPS["all"]) - set(outcomes):
            msg = f"{where}: no outcome for {sorted(missing)}"
            raise ValueError(msg)
        if bad := {o for o in outcomes.values() if o not in GET_OUTCOMES - {"skip"}}:
            msg = f"{where}: unknown outcome {sorted(bad)}"
            raise ValueError(msg)
        found.append(PostRead(label, route, assignment, body, outcomes, reason, number))
    if len({r.key for r in found}) != len(found):
        msg = f"{path.name}: a label is used twice on one route"
        raise ValueError(msg)
    return found


def resolve_body(world, value):
    """A post_reads.txt body with its ``@`` references taken from the world."""
    if isinstance(value, dict):
        return {k: resolve_body(world, v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_body(world, v) for v in value]
    if not (isinstance(value, str) and value.startswith("@")):
        return value
    ref, _, cut = value[1:].partition(":")
    if ref.startswith("today"):
        days = int(ref[len("today") :] or 0)
        today = timezone.localdate()
        return (today + datetime.timedelta(days=days)).isoformat()
    name, _, attr = ref.partition(".")
    if name not in world.objects:
        msg = f"post_reads.txt: no world object {name!r}"
        raise ValueError(msg)
    found = getattr(world.objects[name], attr or "external_id")
    if not isinstance(found, (int, str)):
        found = str(found)
    return found[: int(cut)] if cut else found


# --- the world as fill() sees it ---------------------------------------------------------


def _label(model):
    return model._meta.label_lower


def tenant_for(world):
    """What ``routes.fill()`` reads: one id (and slug) per model, the world's named row first
    (world.objects, the first named of a model is its default), else the first row by pk."""
    by_label, slugs = {}, {}
    for obj in world.objects.values():
        label = _label(type(obj))
        if getattr(obj, "external_id", None):
            by_label.setdefault(label, str(obj.external_id))
        if getattr(obj, "slug", None):
            slugs.setdefault(label, obj.slug)
    by_label["users.user"] = str(world.users["Doctor"].external_id)
    for model in apps.get_models():
        if model._meta.app_label not in APPS:
            continue
        names = {f.name for f in model._meta.get_fields()}
        manager = getattr(model, "all_objects", None) or model._default_manager
        label = _label(model)
        if "external_id" in names and label not in by_label:
            value = manager.order_by("pk").values_list("external_id", flat=True).first()
            if value:
                by_label[label] = str(value)
        if "slug" in names and label not in slugs:
            value = manager.order_by("pk").values_list("slug", flat=True).first()
            if value:
                slugs[label] = value
    with connection.cursor() as cursor:
        cursor.execute("SELECT max(id) FROM ihr_audit_entry")
        entry = cursor.fetchone()[0]
    return SimpleNamespace(
        first_id_by_model=by_label,
        first_slug_by_model=slugs,
        username=world.users["Doctor"].username,
        audit_entry_id=str(entry or ""),
        pincode="411001",
    )


def _value(world, ref, where):
    name, _, attr = ref.partition(".")
    if name not in world.objects:
        msg = f"{where}: no world object {name!r}"
        raise ValueError(msg)
    obj = world.objects[name]
    return str(getattr(obj, attr) if attr else obj.external_id)


def variant(world, tenant, route, cls, assignment):
    """(path, query) for one list_params line: ``?a=obj`` (query), ``?{param}=obj`` (the path
    parameter takes that object's id), ``?a~text`` (a literal)."""
    where = f"list_params.txt {route} {assignment}"
    query = {}
    ids = dict(tenant.first_id_by_model)
    slugs = dict(tenant.first_slug_by_model)
    for part in assignment[1:].split("&"):
        if "~" in part and "=" not in part:
            name, _, literal = part.partition("~")
            query[name] = literal
            continue
        name, _, ref = part.partition("=")
        if not ref:
            msg = f"{where}: bad assignment {part!r}"
            raise ValueError(msg)
        value = _value(world, ref, where)
        if name.startswith("{"):
            param = name.strip("{}")
            obj = world.objects[ref.partition(".")[0]]
            if param == "slug":
                slugs[_label(type(obj))] = value
            else:
                label = (
                    _label(_model(cls))
                    if param == "external_id"
                    else PARAM_MODELS[param]
                )
                if label != _label(type(obj)):
                    msg = f"{where}: {ref} is not a {label}"
                    raise ValueError(msg)
                ids[label] = value
        else:
            query[name] = value
    other = SimpleNamespace(
        **{**vars(tenant), "first_id_by_model": ids, "first_slug_by_model": slugs}
    )
    return fill(route, cls, other), query


# --- reading an answer --------------------------------------------------------------------


def body_of(response):
    if not hasattr(response, "ihr_text"):
        raw = (
            b"".join(response.streaming_content)
            if getattr(response, "streaming", False)
            else response.content
        )
        response.ihr_text = raw.decode(errors="replace")
    return response.ihr_text


def json_of(response):
    if "json" not in response.get("Content-Type", ""):
        return None
    try:
        return json.loads(body_of(response))
    except ValueError:
        return None


def has_rows(response):
    data = json_of(response)
    if data is None:
        return bool(body_of(response).strip())
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return bool(data["results"])
    return bool(data)


STATUS_OUTCOMES = {
    400: "invalid",
    401: "deny",
    403: "deny",
    404: "deny",
    405: "deny",
    409: "conflict",
    503: "unavailable",
}


def classify(response, projected):
    code = response.status_code
    if code in STATUS_OUTCOMES:
        return STATUS_OUTCOMES[code]
    if 300 <= code < 400:
        return "redirect"
    if 200 <= code < 300:
        if not has_rows(response):
            return "empty"
        return "project" if projected else "allow"
    return f"status {code}"


def markers_in(text):
    return frozenset(m.group(1) or "base" for m in MARKER_RE.finditer(text))


# An embedded body of a guarded shape (its fields are classified exactly by the guard's field
# lists, test_fields) or a staff card is recorded once as ``path<shape>``, not walked: the
# snapshot is for the bodies no field list covers (accounts, bills, payments, bookings,
# deliveries ...; design §10 item 15)
SIGNATURES = (
    ("patient", frozenset({"blood_group", "gender", "year_of_birth"})),
    ("encounter", frozenset({"encounter_class", "discharge_summary_advice"})),
    ("location", frozenset({"mode", "form", "operational_status"})),
    ("device", frozenset({"registered_name", "availability_status"})),
    ("booking", frozenset({"token_slot", "booked_on"})),
    ("user", frozenset({"username", "first_name", "last_name"})),
)


# Staff cards are not a guard shape: collapsed always (a known limit: a new field on CARE's user
# card is not flagged here)
UNGUARDED_SIGNATURES = frozenset({"user"})


def _shape_of(value, handled):
    """The shape this embedded body is recorded as, or None to walk it: a guarded shape only
    when the guard decided on this very instance in this answer (review K4), so a patient-like
    body built outside the guarded specs is walked and its keys show."""
    keys = set(value)
    for name, signature in SIGNATURES:
        if signature <= keys:
            if name in UNGUARDED_SIGNATURES or (name, str(value.get("id"))) in handled:
                return name
            return None
    return None


def key_paths(value, prefix="", handled=frozenset()):
    """Every key of a JSON body, nested dicts and lists of dicts walked (``a[].b``); a guarded
    shape the guard handled, or a staff card, below the top level is ``a.b<shape>``."""
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            shape = _shape_of(item, handled) if isinstance(item, dict) else None
            if shape is None and isinstance(item, list) and item:
                shapes = {
                    _shape_of(i, handled) if isinstance(i, dict) else None for i in item
                }
                shape = shapes.pop() if len(shapes) == 1 else None
            if shape is not None:
                found.add(f"{path}<{shape}>")
                continue
            found.add(path)
            found |= key_paths(item, path, handled)
    elif isinstance(value, list):
        for item in value:
            found |= key_paths(item, f"{prefix}[]", handled)
    return found


@dataclass
class Answer:
    key: str
    role: str
    status: int
    outcome: str
    markers: frozenset = frozenset()
    shapes: tuple = ()  # (shape, spec class) the guard projected in this answer
    handled: frozenset = (
        frozenset()
    )  # (shape, id) of every instance the guard decided on
    keys: frozenset = frozenset()
    identity: frozenset = (
        frozenset()
    )  # patient names, phones and ids found (Owner rule)


_route = ContextVar("ihr_matrix_route", default=None)


def _ours_or_cares(filename):
    return ("/care/" in filename or "/ihr_" in filename) and "/tests/" not in filename


class QueryReads:
    """Which query keys the code of CARE or ours reads on each route (review M2/K2/O1): every
    ``get``, ``[]``, ``in`` and ``getlist`` on a QueryDict made from a frame under ``care/`` or
    ``ihr_*`` while the crawl requests that route, helpers, mixins and parents included (a
    static scan of the view's own methods missed them). Django's, DRF's and django-filter's own
    reads (paging, filter forms) are not counted."""

    NAMES = ("get", "__getitem__", "__contains__", "getlist")

    def __init__(self):
        self.reads = {}  # base route key -> {query key}

    def _wrap(self, original):
        reads = self.reads

        def method(qd, key, *args, **kwargs):
            route = _route.get()
            if (
                route is not None
                and isinstance(key, str)
                and _ours_or_cares(sys._getframe(1).f_code.co_filename)
            ):
                reads.setdefault(route, set()).add(key)
            return original(qd, key, *args, **kwargs)

        return method

    @contextlib.contextmanager
    def recording(self):
        with contextlib.ExitStack() as stack:
            for name in self.NAMES:
                stack.enter_context(
                    mock.patch.object(
                        QueryDict, name, self._wrap(getattr(QueryDict, name))
                    )
                )
            yield self


@dataclass
class Crawl:
    """The GET crawl, run once per test class (each request rolled back)."""

    world: object
    answers: dict = field(default_factory=dict)  # (key, role) -> Answer
    posts: dict = field(default_factory=dict)  # (post read key, role) -> Answer
    unfilled: set = field(default_factory=set)  # keys fill() could not fill
    skipped: set = field(default_factory=set)  # unfilled keys their row says to skip
    reads: dict = field(default_factory=dict)  # base route key -> query keys read
    requests: int = 0

    def targets(self, rows):
        """(key, base key, path, query) of every GET route, list_params line and generated
        cross-patient variant, each key once (review O6: our invitation swap and CARE's router
        both give ``/api/v1/users/``; Django serves the first, so that one is kept)."""
        tenant = tenant_for(self.world)
        params = read_params()
        cross = "encounter_b" in self.world.objects
        out, seen = [], {}
        with filling():
            for route, cls, methods in get_route_views():
                if cls is Blocked or (methods is not None and "get" not in methods):
                    continue
                key = norm(route)
                entries = [(key, fill(route, cls, tenant), {})]
                assignments = [a for a in params.get(key, ()) if is_variant(a)]
                if cross and PATIENT_PARAM in key:
                    assignments.append(CROSS)
                for assignment in assignments:
                    path, query = variant(self.world, tenant, route, cls, assignment)
                    entries.append((f"{key} {assignment}", path, query))
                for entry_key, path, query in entries:
                    if entry_key in seen:
                        if seen[entry_key] != (path, query):
                            msg = f"two routes give the key {entry_key}: {seen[entry_key]} {path}"
                            raise AssertionError(msg)
                        continue
                    seen[entry_key] = (path, query)
                    if path is None:
                        self.unfilled.add(entry_key)
                        row = rows.get(("GET", entry_key))
                        if row is not None and set(row.outcomes.values()) == {"skip"}:
                            self.skipped.add(entry_key)
                        continue
                    # a filled route is always requested: a skip row there fails (review M5)
                    out.append((entry_key, key, path, query))
        return out

    def identity_words(self):
        words = set()
        for name in ("patient", "patient_b"):
            patient = self.world.objects.get(name)
            if patient is not None:
                words |= {
                    patient.name,
                    str(patient.external_id),
                    patient.phone_number[3:],
                }
        return words

    def post_targets(self):
        """(PostRead, path, query, body) of every post_reads.txt line."""
        tenant = tenant_for(self.world)
        views = {norm(route): (route, cls) for route, cls, _m in get_route_views()}
        out = []
        with filling():
            for read in read_post_reads():
                if read.route not in views:
                    msg = f"post_reads.txt:{read.line}: no route {read.route}"
                    raise ValueError(msg)
                route, cls = views[read.route]
                if read.assignment:
                    path, query = variant(
                        self.world, tenant, route, cls, read.assignment
                    )
                else:
                    path, query = fill(route, cls, tenant), {}
                if path is None:
                    msg = f"post_reads.txt:{read.line}: fill() cannot fill {read.route}"
                    raise ValueError(msg)
                out.append((read, path, query, resolve_body(self.world, read.body)))
        return out

    def _answer(self, key, role, response, projected, handled, words):
        text = body_of(response)
        data = json_of(response)
        outcome = classify(response, bool(projected))
        answer = Answer(key, role, response.status_code, outcome)
        answer.markers = markers_in(text)
        answer.identity = frozenset(w for w in words if w in text)
        answer.shapes = tuple(sorted(set(projected)))
        answer.handled = frozenset(handled)
        if outcome in ("allow", "project") and data is not None:
            answer.keys = frozenset(key_paths(data, handled=answer.handled))
        return answer

    def run(self, clients, rows):
        targets = self.targets(rows)
        posts = self.post_targets()
        words = self.identity_words()
        projected, handled = [], set()
        original = guard.project_instance
        applies = guard.Shape.applies

        def counting(cls, instance, shape):
            projected.append((shape.name, cls.__name__))
            return original(cls, instance, shape)

        def deciding(shape, current, obj):
            handled.add((shape.name, str(getattr(obj, "external_id", ""))))
            return applies(shape, current, obj)

        reads = QueryReads()
        with (
            mock.patch.object(guard, "project_instance", counting),
            mock.patch.object(guard.Shape, "applies", deciding),
            reads.recording(),
        ):
            for role, client in clients.items():
                for key, base, path, query in targets:
                    projected.clear()
                    handled.clear()
                    token = _route.set(base)
                    try:
                        with transaction.atomic():
                            response = client.get(path, query)
                            body_of(response)
                            transaction.set_rollback(True)
                    finally:
                        _route.reset(token)
                    self.requests += 1
                    self.answers[(key, role)] = self._answer(
                        key, role, response, projected, handled, words
                    )
                for read, path, query, body in posts:
                    projected.clear()
                    handled.clear()
                    url = f"{path}?{urlencode(query)}" if query else path
                    token = _route.set(f"POST {read.route}")  # review NEW-2
                    try:
                        with transaction.atomic():
                            response = client.post(
                                url, json.dumps(body), content_type="application/json"
                            )
                            body_of(response)
                            transaction.set_rollback(True)
                    finally:
                        _route.reset(token)
                    self.requests += 1
                    self.posts[(read.key, role)] = self._answer(
                        read.key, role, response, projected, handled, words
                    )
        self.reads = reads.reads
        return self

    def every_answer(self):
        """GET and POST-read answers together (the canary, the snapshot, world coverage)."""
        return {**self.answers, **self.posts}


def write_targets(world, rows):
    """(route key, method, path) of every non-GET method of a class view, as ``fill()``
    fills its path (None if it cannot); each (key, method) once (review O6)."""
    tenant = tenant_for(world)
    out, seen = [], {}
    with filling():
        for route, cls, methods in get_route_views():
            if cls is None or cls is Blocked or not methods:
                continue
            key = norm(route)
            path = fill(route, cls, tenant)
            for method in methods:
                if method == "get":
                    continue
                if (key, method) in seen:
                    if seen[(key, method)] != path:
                        msg = f"two routes give {method} {key}: {seen[(key, method)]} {path}"
                        raise AssertionError(msg)
                    continue
                seen[(key, method)] = path
                out.append((key, method, path))
    return out


def write_rows(rows, key, method="W"):
    """The row for one non-GET method of a route: its own line, else the route's W line, else
    the default (our roles deny, CARE's roles not sent)."""
    row = rows.get((method.upper(), key)) or rows.get(("W", key))
    if row is None:
        return Row("W", key, expand(WRITE_DEFAULT.split(), "default"), "default")
    return row
