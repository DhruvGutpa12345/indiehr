"""The serializer guard: what restricted users see of a patient, encounter, bed or device
(P1-T11b1; docs/reviews/p1-t11/design-b.md §5.2 and "After the attack" §2).

CARE builds every response body with ``<Spec>.serialize(obj)`` (``EMRResource.serialize``,
emr/resources/base.py), and embeds patients, encounters and beds by calling the same classmethod
of the embedded spec. ``install()`` wraps that classmethod on one class per shape; subclasses
inherit the wrapper, so every list, retrieve and embedding of the shape goes through it, also in
specs upstream adds later. The wrapper calls CARE's ``serialize`` and, when the shape applies to
this request (state.py), returns a projection of the result:

- every model field stays present, so the web app's types hold;
- a field marked ``keep`` keeps its value; a container (dict, list of dicts, model) keeps only
  the nested keys its list names (``a.b keep``; ``a.* keep`` keeps a reviewed staff, facility or
  configuration card whole); unnamed nested keys are removed;
- ``drop``, and any field the list does not name (a new upstream field), is reset to the field's
  default: fail closed;
- ``patient`` / ``encounter`` / ``location`` re-project an embedded body with that shape's list;
- ``identifiers`` keeps only identifiers whose config system is on identifier_configs.txt (a
  programme number such as an ART registration reveals the illness);
- ``permissions`` keeps only our ``ihr_*`` slugs and CARE slugs that open nothing clinical.

When a shape applies:
- patient, encounter, location (bed occupant), device: the user is restricted, or our widening
  branch forced projection (P1-T11b2). A patient under one of the user's live records grants is
  not projected, nor are that patient's encounters (MRD, P1-T11b4);
- patient, encounter (the chart rule, P1-T11b3): the user holds only roles of ours, one of them
  clinical (the Radiologist, ``state.chart_limited``), and CARE gives them no clinical data on
  that patient: an imaging request or report embeds the whole patient and visit (Q-K44 A);
- booking, token: only under forced projection, so the Receptionist's own CARE-granted booking
  and token screens keep their notes and resources.

The billing overlay (Q-K37a B, owner 2026-11-15): for a user who holds the Cashier or TPA Desk
role (``registry.BED_FOR_BILLING``) in an organisation of the encounter's own facility,
``fields/encounter_billing.txt`` is laid over ``fields/encounter.txt``: the bed's id and name, the
bed stays' ids and times, for CARE's room-rent table. Everyone else, and an encounter body
re-projected inside another shape (``project_body``), keeps Q-K37 A.

Field lists: ``fields/<shape>.txt``, one ``<path> <verb>  # reason`` per line (tests check they
classify exactly the shape's fields and name the nested keys of every kept container).
"""

import copy
import functools
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path

from django.utils.module_loading import import_string
from pydantic import BaseModel

from ihr_access import state

FIELDS = Path(__file__).with_name("fields")
VERBS = frozenset(
    {"keep", "drop", "patient", "encounter", "location", "identifiers", "permissions"}
)
SHAPE_VERBS = frozenset({"patient", "encounter", "location"})
MARKER = "ihr_guard"
_MISSING = object()


def read_list(path, verbs=VERBS):
    """{dotted path: (verb, reason)} from a field list; ValueError on a malformed line."""
    rules = {}
    for number, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        body, _, reason = line.partition("#")
        parts = body.split()
        if len(parts) != 2 or parts[1] not in verbs or not reason.strip():  # noqa: PLR2004
            msg = f"{path}:{number}: expected '<path> <verb>  # reason', got {raw!r}"
            raise ValueError(msg)
        if parts[0] in rules:
            msg = f"{path}:{number}: {parts[0]} is listed twice"
            raise ValueError(msg)
        rules[parts[0]] = (parts[1], reason.strip())
    return rules


def read_names(path):
    """One name per line (``name  # reason``): identifier_configs.txt."""
    names = set()
    for raw in Path(path).read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            names.add(line)
    return frozenset(names)


@dataclass
class Shape:
    name: str
    base: str  # dotted name of the class the wrapper sits on
    forced_only: bool = False
    exempt_by: str = (
        ""  # attribute of the CARE object naming its patient, for grant exemption
    )
    # how to reach the object's patient for the chart rule ("" for the object itself), or None
    # where the rule does not apply (P1-T11b3)
    chart: str | None = None
    # base classes CARE also serializes directly, wrapped with this shape too (P1-T11b3's spec
    # walk: a medication dispense embeds its visit through EncounterSpecBase)
    also: tuple = ()
    # a field list laid over this one for users who hold a billing role in the object's
    # facility (state.bills_at; Q-K37a B: the encounter's bed for room rent), or ""
    billing: str = ""
    rules: dict = field(default_factory=dict)  # path -> verb
    billing_rules: dict = field(default_factory=dict)  # rules with the overlay laid on
    fields: dict = field(
        default_factory=dict
    )  # name -> pydantic FieldInfo, all classes

    def load(self):
        self.rules = {
            p: v for p, (v, _r) in read_list(FIELDS / f"{self.name}.txt").items()
        }
        self.billing_rules = {}
        if self.billing:
            overlay = read_list(FIELDS / f"{self.billing}.txt")
            self.billing_rules = {
                **self.rules,
                **{p: v for p, (v, _r) in overlay.items()},
            }
        self.fields = {}
        for cls in classes_of(import_string(self.base)):
            for name, info in cls.model_fields.items():
                self.fields.setdefault(name, info)

    def applies(self, current, obj):
        if current is None:
            return False
        if self.forced_only:
            return current.forced
        if not (current.forced or current.restricted):
            return self._chart_withheld(current, obj)
        if self.exempt_by:
            patient = getattr(obj, self.exempt_by, None)
            if patient is not None and patient in current.granted_patients:
                return False
        return True

    def rules_for(self, current, obj):
        """The rules for this object in this request: the billing overlay only for a holder of
        a billing role in the object's own facility (Q-K37a B), else the shape's list."""
        if self.billing_rules and current.bills_at(getattr(obj, "facility_id", None)):
            return self.billing_rules
        return self.rules

    def _chart_withheld(self, current, obj):
        """The chart rule: a user of our roles only who holds a clinical one (the Radiologist)
        sees a patient or visit unprojected only where CARE gives them that patient's clinical
        data; an imaging request or report of another patient embeds both (Q-K44 A)."""
        if self.chart is None or not current.chart_limited:
            return False
        patient = getattr(obj, self.chart) if self.chart else obj
        return patient is not None and not current.may_read_chart(patient)

    def reset(self, name):
        info = self.fields.get(name)
        if info is None:
            return None
        if not info.is_required():
            return copy.deepcopy(info.get_default(call_default_factory=True))
        return _empty(info.annotation)


SHAPES = {
    "patient": Shape(
        "patient",
        "care.emr.resources.patient.spec.PatientListSpec",
        exempt_by="pk",
        chart="",
    ),
    "encounter": Shape(
        "encounter",
        "care.emr.resources.encounter.spec.EncounterListSpec",
        exempt_by="patient_id",
        chart="patient",
        also=("care.emr.resources.encounter.spec.EncounterSpecBase",),
        billing="encounter_billing",
    ),
    "location": Shape(
        "location", "care.emr.resources.location.spec.FacilityLocationListSpec"
    ),
    "device": Shape("device", "care.emr.resources.device.spec.DeviceRetrieveSpec"),
    "booking": Shape(
        "booking",
        "care.emr.resources.scheduling.slot.spec.TokenBookingBaseReadSpec",
        forced_only=True,
    ),
    "token": Shape(
        "token",
        "care.emr.resources.scheduling.token.spec.TokenRetrieveSpec",
        forced_only=True,
    ),
}
_installed = {}  # shape name -> the class the wrapper was put on


def classes_of(base):
    """``base`` and every subclass of it, as they are now."""
    found, todo = [], [base]
    while todo:
        cls = todo.pop()
        if cls not in found:
            found.append(cls)
            todo.extend(cls.__subclasses__())
    return found


def _empty(annotation):
    """A value of the field's own kind for a required field that has no default."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        if type(None) in typing.get_args(annotation):
            return None
        annotation = typing.get_args(annotation)[0]
        origin = typing.get_origin(annotation)
    kind = origin or annotation
    if kind is dict or (isinstance(kind, type) and issubclass(kind, BaseModel)):
        return {}
    if kind is list:
        return []
    if kind is str:
        return ""
    return None


@functools.cache
def allowed_identifier_systems():
    return read_names(FIELDS / "identifier_configs.txt")


def allowed_permission(slug):
    from ihr_roles.registry import NEVER_RESTRICTED

    return isinstance(slug, str) and (
        slug.startswith("ihr_") or slug not in NEVER_RESTRICTED
    )


def _as_plain(value):
    return value.model_dump(mode="python") if isinstance(value, BaseModel) else value


def _system_of(identifier):
    identifier = _as_plain(identifier)
    if not isinstance(identifier, dict):
        return None
    config = _as_plain(identifier.get("config"))
    if not isinstance(config, dict):
        return None
    inner = _as_plain(config.get("config"))
    return inner.get("system") if isinstance(inner, dict) else None


def _children(rules, path):
    """{key: verb} of the direct children ``path`` names (a key named only through deeper
    paths is kept as a container)."""
    prefix = path + "."
    children = {}
    for rule in rules:
        if rule.startswith(prefix):
            key = rule[len(prefix) :].split(".", 1)[0]
            children.setdefault(key, rules.get(prefix + key, "keep"))
    return children


def _nested(value, path, rules):
    children = _children(rules, path)
    if not children or "*" in children:
        return value
    value = _as_plain(value)
    if isinstance(value, list):
        return [_nested(item, path, rules) for item in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, item in value.items():
        verb = children.get(key)
        if verb is None:
            continue  # not named: removed (fail closed)
        out[key] = (
            None if verb == "drop" else _apply(verb, item, f"{path}.{key}", rules)
        )
    return out


def _apply(verb, value, path, rules):
    if verb == "keep":
        return _nested(value, path, rules)
    if verb in SHAPE_VERBS:
        return project_body(SHAPES[verb], value)
    if verb == "identifiers":
        allowed = allowed_identifier_systems()
        kept = [i for i in (value or []) if _system_of(i) in allowed]
        return _nested(kept, path, rules)
    if verb == "permissions":
        return [slug for slug in (value or []) if allowed_permission(slug)]
    return None  # drop (handled by the callers) or an unknown verb: nothing


def project_body(shape, value):
    """Project an embedded body (a dict as ``to_json`` made it, a list of them, or a model)."""
    value = _as_plain(value)
    if isinstance(value, list):
        return [project_body(shape, item) for item in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, item in value.items():
        verb = shape.rules.get(key)
        if verb is None:
            continue
        out[key] = (
            shape.reset(key) if verb == "drop" else _apply(verb, item, key, shape.rules)
        )
    return out


def project_instance(cls, instance, shape, rules=None):
    """The projected copy of a constructed spec: every field present, dropped ones reset.
    ``rules``: the shape's rules for this object (``Shape.rules_for``), by default its list."""
    rules = shape.rules if rules is None else rules
    values = {}
    held = instance.__dict__
    for name in cls.model_fields:
        verb = rules.get(name)
        value = held.get(name, _MISSING)
        if verb is None or verb == "drop" or value is _MISSING:
            values[name] = shape.reset(name)
        else:
            values[name] = _apply(verb, value, name, rules)
    return cls.model_construct(**values)


def _wrapper(shape, original):
    def serialize(cls, obj, user=None, *args, **kwargs):
        result = original(cls, obj, user, *args, **kwargs)
        current = state.current()
        if shape.applies(current, obj):
            return project_instance(cls, result, shape, shape.rules_for(current, obj))
        return result

    setattr(serialize, MARKER, shape.name)
    serialize.ihr_original = original
    return classmethod(serialize)


def install():
    """Wrap ``serialize`` on each shape's class (IhrAccessConfig.ready(), once)."""
    for shape in SHAPES.values():
        if shape.name in _installed:
            continue
        shape.load()
        base = import_string(shape.base)
        original = base.serialize.__func__
        base.serialize = _wrapper(shape, original)
        _installed[shape.name] = base
        for dotted in shape.also:
            # its own serialize only: the shape's class above already has its own wrapper
            extra = import_string(dotted)
            extra.serialize = _wrapper(shape, extra.serialize.__func__)


def problems():
    """Why the guard does not hold (ihr_access.E022): a shape class CARE uses whose
    ``serialize`` is not our wrapper (a subclass that defines its own, a class moved upstream)."""
    found = []
    for shape in SHAPES.values():
        try:
            base = import_string(shape.base)
        except ImportError:
            found.append(f"{shape.base} no longer exists (moved upstream?)")
            continue
        classes = classes_of(base)
        for dotted in shape.also:
            try:
                classes = [*classes, *classes_of(import_string(dotted))]
            except ImportError:
                found.append(f"{dotted} no longer exists (moved upstream?)")
        for cls in dict.fromkeys(classes):
            if getattr(cls.serialize, MARKER, None) != shape.name:
                found.append(
                    f"{cls.__module__}.{cls.__qualname__}.serialize is not guarded"
                )
    return found
