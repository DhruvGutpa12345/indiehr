"""Answers CARE's terminology questions from our own tables (P1-T10a1, ADR-016).

Rules (auto-accepted by Claude, Q-F7, awaiting the owner):

- A value set compose may use only what CARE's own value sets use: concept lists, filters
  (concept is-a | descendent-of | = | in | not-in) and the ECL subset in ecl.py. Anything else is
  refused as a whole: a search then shows nothing and a coded write is refused, never widened.
- Licence-gated systems (SNOMED CT, LOINC, ICD) answer nothing, neither search nor validation,
  until their content is loaded (P1-T10b), even when a value set lists codes of them.
- Other systems without loaded content: a value set's own concept list is offered and accepted.
- A pinned include version that is not loaded uses the active version (logged once): CARE pins
  HL7 code systems at 2.0.0, and HL7 has moved on (THO 7.5.0 carries 1.x/3.x).
- A coding is valid only with the include's system, an active code in the value set, and, if it
  carries a display, a display that is one of the concept's terms.
- UCUM: the common units are offered; any unit valid by ucum.py is accepted.
- Limits per call, a budget of searches per web request (middleware.py), and a 2 s statement
  timeout keep one request from loading the database for every hospital. An exhausted budget or
  a timeout is an HTTP error (429 / 503), never an answer: CARE caches answers for 10 s, and a
  refusal cached as "not in the value set" would reach other users. Code checks and lookups are
  point queries and have no budget, so long forms with many coded fields still save.
"""

import contextvars
import logging
from dataclasses import dataclass, field

from django.db import OperationalError, connection, transaction
from django.db.models import Q
from rest_framework.exceptions import APIException

from ihr_terminology import ecl, ucum
from ihr_terminology.models import CodeSystemVersion, Concept, Designation
from ihr_terminology.text import normalise, same_display

log = logging.getLogger("ihr.terminology")

SNOMED = "http://snomed.info/sct"
UCUM = "http://unitsofmeasure.org"
LICENCE_GATED = {
    SNOMED,
    "http://loinc.org",
    "http://hl7.org/fhir/sid/icd-10",
    "http://hl7.org/fhir/sid/icd-10-cm",
    "http://id.who.int/icd/release/11/mms",
}
MAX_COUNT = 100
MAX_INCLUDES = 20  # includes + excludes in one call
MAX_CONCEPTS = 2000  # codes listed in one compose
MAX_FILTER_CHARS = 100
MAX_FILTER_WORDS = 12  # counted on the text as typed
MAX_CANDIDATES = 5000  # designations ranked for one search
MAX_SEARCHES_PER_REQUEST = 30
MAX_WARNINGS_KEPT = 256
STATEMENT_TIMEOUT_MS = 2000
ALLOWED_FILTERS = {("concept", "is-a"), ("concept", "descendent-of"), ("concept", "="),
                   ("concept", "in"), ("concept", "not-in"), ("constraint", "=")}  # fmt: skip

_calls = contextvars.ContextVar("ihr_terminology_calls", default=None)
_warned = set()  # bounded: system URLs and pins, logged once per process


class RefusedError(Exception):
    """Answered with an OperationOutcome: CARE then shows no results and refuses the write."""


class TooManySearches(APIException):
    status_code = 429
    default_detail = "Too many terminology searches in one request."
    default_code = "terminology_busy"


class TooSlow(APIException):
    status_code = 503
    default_detail = "The terminology search took too long; try more specific words."
    default_code = "terminology_timeout"


def start_request():
    return _calls.set(0)


def end_request(token):
    _calls.reset(token)


def _count_search():
    n = _calls.get()
    if n is None:  # outside a web request (tasks, commands, tests): no request budget
        return
    if n >= MAX_SEARCHES_PER_REQUEST:
        raise TooManySearches
    _calls.set(n + 1)


def _warn_once(key, message, *args):
    """Process-wide and bounded (reviewed process-wide state, TENANCY_DESIGN §6.3): systems and
    versions come from value sets staff can write, so once full it logs nothing more."""
    if key in _warned or len(_warned) >= MAX_WARNINGS_KEPT:
        return
    _warned.add(key)
    log.warning(message, *(str(a)[:120] for a in args))


@dataclass
class Include:
    system: str
    version: str | None
    concepts: list = field(default_factory=list)  # [(code, display)]
    filters: list = field(default_factory=list)  # [(property, op, value)]
    enumerated: bool = False


def parse_compose(compose):
    """{'include': [...], 'exclude': [...]} -> (includes, excludes); RefusedError if beyond the rules."""
    if not isinstance(compose, dict):
        raise RefusedError("no value set compose")
    includes = compose.get("include") or []
    excludes = compose.get("exclude") or []
    if not isinstance(includes, list) or not isinstance(excludes, list):
        raise RefusedError("include/exclude must be lists")
    if len(includes) + len(excludes) > MAX_INCLUDES:
        raise RefusedError("too many includes")
    total = 0

    def one(item):
        nonlocal total
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("system"), str)
            or not item["system"]
        ):
            raise RefusedError("every include needs a system")
        unknown = set(item) - {
            "system",
            "version",
            "concept",
            "filter",
            "id",
            "valueSet",
        }
        if unknown or item.get("valueSet"):
            msg = f"unsupported include parts: {sorted(unknown) or ['valueSet']}"
            raise RefusedError(msg)
        concepts = item.get("concept") or []
        filters = item.get("filter") or []
        if concepts and filters:
            raise RefusedError("an include has either concepts or filters")
        inc = Include(system=item["system"], version=item.get("version") or None)
        for c in concepts:
            code = (c or {}).get("code")
            if not isinstance(code, str) or not code:
                continue  # CARE allows a code-less row in the editor; it matches nothing
            inc.concepts.append((code, (c or {}).get("display") or None))
        inc.enumerated = bool(concepts)
        total += len(inc.concepts)
        for f in filters:
            prop, op, value = (
                (f or {}).get("property"),
                (f or {}).get("op"),
                (f or {}).get("value"),
            )
            if (prop, op) not in ALLOWED_FILTERS or not isinstance(value, str):
                msg = f"filter not supported: {prop} {op}"
                raise RefusedError(msg)
            if prop == "constraint":
                try:
                    ecl.parse(value)
                except ecl.EclError as e:
                    raise RefusedError(str(e)) from e
            inc.filters.append((prop, op, value))
        return inc

    inc = [one(i) for i in includes]
    exc = [one(i) for i in excludes]
    if total > MAX_CONCEPTS:
        raise RefusedError("too many listed codes")
    return inc, exc


def active_version(system, pinned=None):
    v = CodeSystemVersion.objects.filter(
        url=system, status=CodeSystemVersion.Status.ACTIVE
    ).first()
    if v and pinned and pinned != v.version:
        _warn_once(
            (system, pinned),
            "terminology: %s pinned at %s, answering from %s",
            system,
            pinned,
            v.version,
        )
    return v


def _concept_id(version, code):
    return (
        Concept.objects.filter(system=version, code=code)
        .values_list("id", flat=True)
        .first()
    )


def _filter_q(version, prop, op, value):  # noqa: PLR0911 (one exit per filter kind)
    """One filter as a condition on Concept rows of this version."""
    if prop == "constraint":
        op_ecl, code = ecl.parse(value)
        if code == "*":
            return Q()
        op = {
            "<<": "is-a",
            "<": "descendent-of",
            ">>": "generalizes",
            ">": "ancestor-of",
            "": "=",
        }[op_ecl]
        value = code
    if op in ("in", "not-in"):
        codes = [c.strip() for c in value.split(",") if c.strip()]
        return Q(code__in=codes) if op == "in" else ~Q(code__in=codes)
    if op == "=":
        return Q(code=value)
    focus = (
        Concept.objects.filter(system=version, code=value)
        .values("id", "ancestors")
        .first()
    )
    if not focus:
        return Q(pk__in=[])
    if op == "is-a":
        return Q(pk=focus["id"]) | Q(ancestors__contains=[focus["id"]])
    if op == "descendent-of":
        return Q(ancestors__contains=[focus["id"]])
    if op == "generalizes":
        return Q(pk=focus["id"]) | Q(pk__in=focus["ancestors"])
    if op == "ancestor-of":
        return Q(pk__in=focus["ancestors"])
    msg = f"filter not supported: {op}"
    raise RefusedError(msg)


def _include_q(version, inc):
    if inc.enumerated:
        return Q(code__in=[c for c, _ in inc.concepts])
    q = Q()
    for prop, op, value in inc.filters:
        q &= _filter_q(version, prop, op, value)
    return q


def _any_of(conditions):
    """OR of conditions, where None means "every concept" (Django drops an empty Q in an OR)."""
    if not conditions:
        return None
    if any(c is None or not c for c in conditions):
        return Q()
    out = conditions[0]
    for c in conditions[1:]:
        out |= c
    return out


@dataclass
class Plan:
    """What one compose means for each system: a query over loaded rows, or listed codes only."""

    loaded: dict = field(
        default_factory=dict
    )  # system -> (version, [include Q], [exclude Q])
    displays: dict = field(
        default_factory=dict
    )  # (system, code) -> the value set's own display
    listed: dict = field(
        default_factory=dict
    )  # system -> {code: display} (unloaded, not gated)
    excluded_listed: dict = field(default_factory=dict)  # system -> set(code)


def plan(includes, excludes):
    p = Plan()
    versions = {}
    for inc in includes:
        if inc.system not in versions:
            versions[inc.system] = active_version(inc.system, inc.version)
        v = versions[inc.system]
        for code, display in inc.concepts:
            if display:
                p.displays.setdefault((inc.system, code), display)
        if v is None:
            if inc.system in LICENCE_GATED:
                _warn_once(
                    ("gated", inc.system),
                    "terminology: no content for %s yet (licence, P1-T10b)",
                    inc.system,
                )
            elif inc.enumerated:
                p.listed.setdefault(inc.system, {}).update(dict(inc.concepts))
            else:
                _warn_once(
                    ("missing", inc.system),
                    "terminology: no content for %s",
                    inc.system,
                )
            continue
        p.loaded.setdefault(inc.system, (v, [], []))[1].append(_include_q(v, inc))
    for exc in excludes:
        v = versions.get(exc.system) or active_version(exc.system, exc.version)
        if exc.system in p.loaded:
            v = p.loaded[exc.system][0]
            p.loaded[exc.system][2].append(_include_q(v, exc))
        if exc.enumerated:
            p.excluded_listed.setdefault(exc.system, set()).update(
                c for c, _ in exc.concepts
            )
        elif (
            exc.system in p.listed
        ):  # a filter exclude on unloaded content: nothing can be shown
            p.listed.pop(exc.system)
    return p


def _concepts(version, includes, excludes):
    qs = Concept.objects.filter(system=version, active=True)
    q_inc = _any_of(includes)
    qs = qs.filter(q_inc) if q_inc is not None else qs.none()
    q_exc = _any_of(excludes)
    if q_exc is not None:
        qs = qs.exclude(q_exc) if q_exc else qs.none()
    return qs


class _Timeout:
    """Each terminology query under a statement timeout, restored afterwards (CARE may hold an
    outer transaction; SET LOCAL would outlive us)."""

    def __enter__(self):
        self.atomic = transaction.atomic()
        self.atomic.__enter__()
        with connection.cursor() as cur:
            cur.execute("SELECT current_setting('statement_timeout')")
            self.previous = cur.fetchone()[0]
            cur.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                [str(STATEMENT_TIMEOUT_MS)],
            )
        return self

    def __exit__(self, *exc):
        try:
            if exc[0] is None:
                with connection.cursor() as cur:
                    cur.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        [self.previous],
                    )
        finally:
            suppressed = self.atomic.__exit__(
                *exc
            )  # rolls back to the savepoint on an error
        if (
            isinstance(exc[1], OperationalError)
            and getattr(exc[1].__cause__, "sqlstate", None) == "57014"
        ):
            log.warning(
                "terminology: a query hit the %d ms limit", STATEMENT_TIMEOUT_MS
            )
            raise TooSlow from exc[1]
        return suppressed


def _designations(concept_ids):
    out = {}
    for d in Designation.objects.filter(concept_id__in=concept_ids).order_by(
        "concept_id", "-preferred", "id"
    ):
        out.setdefault(d.concept_id, []).append(d)
    return out


def _display_for(concept, designations, language):
    lang = (language or "").lower()
    if lang and not lang.startswith("en"):
        for d in designations:
            if d.preferred and d.language.lower() == lang:
                return d.value
    return concept.display


def _fhir_designations(designations):
    out = []
    for d in designations:
        item = {"language": d.language, "value": d.value}
        if d.use_code:
            item["use"] = {"system": d.use_system, "code": d.use_code}
        out.append(item)
    return out


def _rank(raw, filter_norm, concept, designation):
    """Lower is better: the code as typed; the exact term; the code in another case; a term
    starting with the words; a preferred term; a shorter term."""
    term = normalise(designation.value)
    return (
        0 if concept.code == raw else 1,
        0 if term == filter_norm else 1,
        0 if concept.code.lower() == raw.lower() else 1,
        0 if term.startswith(filter_norm) else 1,
        0 if designation.preferred else 1,
        len(term),
    )


def expand(  # noqa: PLR0912 (loaded and listed content, each with and without words)
    compose, search="", count=10, display_language=None, include_designations=False
):
    """-> list of {system, code, display, designation?}"""
    _count_search()
    includes, excludes = parse_compose(compose)
    count = max(1, min(int(count or 10), MAX_COUNT))
    search = str(search or "")
    if len(search) > MAX_FILTER_CHARS:
        raise RefusedError("search text too long")
    if len(search.split()) > MAX_FILTER_WORDS:
        raise RefusedError("too many search words")
    raw = search.strip()
    filter_norm = normalise(search)
    words = filter_norm.split()
    results = []
    with _Timeout():
        p = plan(includes, excludes)
        for system, (version, q_inc, q_exc) in p.loaded.items():
            base = _concepts(version, q_inc, q_exc)
            exact = (
                list(base.filter(code__iexact=raw).order_by("code")[:count])
                if raw
                else []
            )
            if not words:
                # nothing to match words against ("" or only symbols, e.g. "%"): the code, then A-Z
                picked = exact + [
                    c
                    for c in base.order_by("display", "code")[:count]
                    if c not in exact
                ]
                ranked = [(c, None) for c in picked[:count]]
            else:
                dq = Designation.objects.filter(concept__in=base)
                for w in words:
                    dq = dq.filter(
                        Q(search_text__startswith=w) | Q(search_text__contains=" " + w)
                    )
                best = {}
                for d in dq.select_related("concept").order_by("id")[:MAX_CANDIDATES]:
                    key = _rank(raw, filter_norm, d.concept, d)
                    if d.concept_id not in best or key < best[d.concept_id][0]:
                        best[d.concept_id] = (key, d.concept)
                for c in exact:
                    key = (0 if c.code == raw else 1, 1, 0, 1, 1, 0)
                    if c.id not in best or key < best[c.id][0]:
                        best[c.id] = (key, c)
                ranked = [
                    (c, k)
                    for k, c in sorted(
                        best.values(), key=lambda kc: (kc[0], kc[1].code)
                    )
                ][:count]
            des = _designations([c.id for c, _ in ranked])
            for c, _ in ranked:
                row = {
                    "system": system,
                    "code": c.code,
                    "display": p.displays.get((system, c.code))
                    or _display_for(c, des.get(c.id, []), display_language),
                }
                if include_designations:
                    row["designation"] = _fhir_designations(des.get(c.id, []))
                results.append(row)
        for system, listed in p.listed.items():
            skip = p.excluded_listed.get(system, set())
            shown_here = 0
            for code, display in listed.items():
                if code in skip or shown_here >= count:
                    continue
                shown = display or code
                text = normalise(f"{shown} {code}")
                if all(text.startswith(w) or f" {w}" in f" {text}" for w in words):
                    row = {"system": system, "code": code, "display": shown}
                    if include_designations:
                        row["designation"] = []
                    results.append(row)
                    shown_here += 1
    return results


def validate(compose, coding):  # noqa: PLR0911 (each exit is one verdict)
    """-> (result, message). A point query: no search budget."""
    includes, excludes = parse_compose(compose)
    if not isinstance(coding, dict):
        return False, "no coding"
    system, code, display = (
        coding.get("system"),
        coding.get("code"),
        coding.get("display"),
    )
    if not system or not isinstance(code, str) or not code:
        return False, "a coding needs its system and code"
    if not any(i.system == system for i in includes):
        return False, f"{system} is not in this value set"
    with _Timeout():
        p = plan(
            [i for i in includes if i.system == system],
            [e for e in excludes if e.system == system],
        )
        if system in p.loaded:
            version, q_inc, q_exc = p.loaded[system]
            concept = _concepts(version, q_inc, q_exc).filter(code=code).first()
            if (
                concept is None
                and system == UCUM
                and not any(e.system == UCUM for e in excludes)
            ):
                whole = any(
                    i.system == UCUM and not i.enumerated and not i.filters
                    for i in includes
                )
                atoms = set(
                    Concept.objects.filter(system=version).values_list(
                        "code", flat=True
                    )
                )
                if whole and ucum.is_valid(code, atoms):
                    if display and not same_display(display, code):
                        return False, f"display {display!r} is not {code}"
                    return True, "valid UCUM syntax"
            if concept is None:
                return False, f"{code} is not in the value set"
            own = p.displays.get((system, code))
            if display and not (
                same_display(display, concept.display)
                or (own and same_display(display, own))
                or any(
                    same_display(display, d.value) for d in concept.designations.all()
                )
            ):
                return False, f"display {display!r} is not a term of {code}"
            return True, ""
        listed = p.listed.get(system, {})
        if code in listed and code not in p.excluded_listed.get(system, set()):
            shown = listed[code] or code
            if display and not same_display(display, shown):
                return False, f"display {display!r} is not a term of {code}"
            return True, "listed in the value set (no content loaded for this system)"
    return (
        False,
        f"no content for {system}"
        if system in LICENCE_GATED
        else f"{code} is not in the value set",
    )


def lookup(system, code):
    """-> FHIR Parameters list for CodeSystem/$lookup, or RefusedError."""
    with _Timeout():
        v = active_version(system)
        concept = Concept.objects.filter(system=v, code=code).first() if v else None
        if concept is None:
            msg = f"{code} not found in {system}"
            raise RefusedError(msg)
        params = [
            {"name": "name", "valueString": v.name or v.title or system},
            {"name": "version", "valueString": v.version},
            {"name": "system", "valueString": system},
            {"name": "code", "valueString": concept.code},
            {"name": "display", "valueString": concept.display},
            {"name": "inactive", "valueBoolean": not concept.active},
        ]
        for d in concept.designations.all().order_by("-preferred", "id"):
            part = [
                {"name": "language", "valueCode": d.language},
                {"name": "value", "valueString": d.value},
            ]
            if d.use_code:
                part.append(
                    {
                        "name": "use",
                        "valueCoding": {"system": d.use_system, "code": d.use_code},
                    }
                )
            params.append({"name": "designation", "part": part})
        for parent in Concept.objects.filter(pk__in=concept.parents).values_list(
            "code", flat=True
        ):
            params.append(
                {
                    "name": "property",
                    "part": [
                        {"name": "code", "valueCode": "parent"},
                        {"name": "value", "valueCode": parent},
                    ],
                }
            )
        for child in Concept.objects.filter(parents__contains=[concept.id]).values_list(
            "code", flat=True
        )[:100]:
            params.append(
                {
                    "name": "property",
                    "part": [
                        {"name": "code", "valueCode": "child"},
                        {"name": "value", "valueCode": child},
                    ],
                }
            )
        return params


def code_systems(name=None, url=None):
    qs = CodeSystemVersion.objects.filter(status=CodeSystemVersion.Status.ACTIVE)
    if url:
        qs = qs.filter(url=url)
    if name:
        qs = qs.filter(name=name)
    return list(qs)
