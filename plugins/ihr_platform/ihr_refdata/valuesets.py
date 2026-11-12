"""The "valueset" kind: CARE's system value sets (P1-T15a, design §3).

A pack sets exactly four fields of a value set: ``name``, ``status``, ``compose`` and
``is_system_defined``; never ``slug`` (a permanent API: about 73 coded fields, questionnaires
and the web app resolve sets by it) and never ``description``. Edits are found by comparing a
hash of those four fields, taken from the database row and from the pack item alike.
"""

import hashlib
import importlib
import json
import pkgutil

from django.conf import settings
from django.utils import timezone
from pydantic import ValidationError

KIND = "valueset"
FIELDS = ("name", "status", "compose", "is_system_defined")
RETIRED = "retired"  # CARE's own status value (resources/valueset/spec.py)
# The compose lists CARE's ValueSet.create_composition iterates over with no guard: a row
# without one (or with null) breaks every coded write bound to the set (C2)
ITERATED = ("include", "exclude")


def _model():
    from care.emr.models.valueset import ValueSet

    return ValueSet


def _drop_empty(value):
    """Nulls and empty lists dropped at every level; list order kept."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            v = _drop_empty(v)  # noqa: PLW2901
            if v is None or v == []:
                continue
            out[k] = v
        return out
    if isinstance(value, list):
        return [_drop_empty(v) for v in value]
    return value


def normal_compose(compose):
    """``compose`` by meaning, not by spelling: CARE's sync stores ``exclude: []`` and leaves
    nulls out, its API leaves out both; all of these hash alike. A compose CARE's own model
    refuses is hashed as it is (it can only differ from the pack's)."""
    from care.emr.resources.common.valueset import ValueSetCompose

    try:
        compose = ValueSetCompose.model_validate(compose).model_dump(exclude_none=True)
    except (ValidationError, TypeError, ValueError):
        pass
    return _drop_empty(compose)


def content_hash(fields):
    canonical = {
        "name": fields["name"],
        "status": fields["status"],
        "compose": normal_compose(fields["compose"]),
        "is_system_defined": bool(fields["is_system_defined"]),
    }
    text = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(text.encode()).hexdigest()


def item_fields(item):
    return {name: item[name] for name in FIELDS}


def check_item(item):
    """A pack item as ihr_pack_build writes it (ValueError/TypeError/KeyError otherwise): the
    slug and the four fields, of CARE's types, and a compose CARE's own model accepts with
    every list its lookup iterates over (C8)."""
    from care.emr.resources.common.valueset import ValueSetCompose

    if not isinstance(item, dict):
        msg = "not an object"
        raise TypeError(msg)
    missing = [name for name in ("slug", *FIELDS) if name not in item]
    if missing:
        msg = f"missing {', '.join(missing)}"
        raise KeyError(msg)
    for name in ("slug", "name", "status"):
        if not isinstance(item[name], str) or not item[name]:
            msg = f"{name} must be a non-empty string"
            raise TypeError(msg)
    if not isinstance(item["is_system_defined"], bool):
        msg = "is_system_defined must be true or false"
        raise TypeError(msg)
    if not usable_compose(item["compose"]):
        msg = f"compose must be an object with the lists {', '.join(ITERATED)}"
        raise TypeError(msg)
    ValueSetCompose.model_validate(
        item["compose"]
    )  # pydantic's ValidationError: ValueError


def usable_compose(compose):
    return isinstance(compose, dict) and all(
        isinstance(compose.get(name), list) for name in ITERATED
    )


def usable(row):
    """True when CARE's lookup can read the row's compose (see ITERATED)."""
    return usable_compose(row.compose)


def row_fields(row):
    return {name: getattr(row, name) for name in FIELDS}


def item_hash(item):
    return content_hash(item_fields(item))


def row_hash(row):
    return content_hash(row_fields(row))


def retired_hash(row):
    """The row's hash once retired (what a retire writes, or would write)."""
    return content_hash({**row_fields(row), "status": RETIRED})


# --- reading and writing the hospital's rows ----------------------------------------------


def rows(slugs):
    """{slug: row} for these slugs, soft-deleted rows included (they still hold the slug)."""
    model = _model()
    return {row.slug: row for row in model._base_manager.filter(slug__in=list(slugs))}  # noqa: SLF001


def all_present(slugs):
    """True when every slug has a row that is not soft-deleted and whose compose CARE's
    lookup can read (cheap: one count)."""
    model = _model()
    present = (
        model._base_manager.filter(  # noqa: SLF001
            slug__in=list(slugs), deleted=False, compose__has_keys=list(ITERATED)
        )
        .exclude(compose__include=None)
        .exclude(compose__exclude=None)
        .count()
    )
    return present == len(set(slugs))


def create(item):
    model = _model()
    row = model(slug=item["slug"], description="", **item_fields(item))
    row.save()
    return row


def write(row, item, *, restore=False):
    """Set the pack's four fields (and un-delete), saving only what changed."""
    changed = []
    for name, value in item_fields(item).items():
        if getattr(row, name) != value:
            setattr(row, name, value)
            changed.append(name)
    if restore and row.deleted:
        row.deleted = False
        changed.append("deleted")
    if changed:
        row.modified_date = timezone.now()
        row.save(update_fields=[*changed, "modified_date"])
    return row


def make_usable(row):
    """The hospital's own compose with the lists CARE's lookup iterates over added as empty
    where they are missing or null: the same meaning, nothing of theirs changed (review C2
    after C4). Returns True when it wrote."""
    compose = dict(row.compose) if isinstance(row.compose, dict) else {}
    missing = [name for name in ITERATED if not isinstance(compose.get(name), list)]
    if not missing:
        return False
    for name in missing:
        compose[name] = []
    row.compose = compose
    row.modified_date = timezone.now()
    row.save(update_fields=["compose", "modified_date"])
    return True


def retire(row):
    """Mark a set the pack no longer ships: never deleted (old records still resolve it).
    CARE's validation ignores status, so this only marks it."""
    if row.status != RETIRED:
        row.status = RETIRED
        row.modified_date = timezone.now()
        row.save(update_fields=["status", "modified_date"])
    return row


# --- building the pack from CARE's own registry -------------------------------------------


def care_registry():
    """CARE's system value sets as pack items, sorted by slug. The registry is filled only as
    modules are imported, so every module under ``care.emr.resources`` and the URLconf are
    imported first (``system-medication-not-given`` is registered by a module nothing else
    imports, so CARE's own ``sync_valueset`` never wrote it)."""
    import care.emr.resources
    from care.emr.registries.care_valueset.care_valueset import SystemValueSet

    for module in pkgutil.walk_packages(
        care.emr.resources.__path__, care.emr.resources.__name__ + "."
    ):
        importlib.import_module(module.name)
    importlib.import_module(settings.ROOT_URLCONF)
    items, seen = [], set()
    for valueset in SystemValueSet.get_all_valuesets():
        if valueset.slug in seen:
            msg = f"CARE registers the system value set {valueset.slug!r} twice"
            raise ValueError(msg)
        seen.add(valueset.slug)
        items.append(
            {
                "slug": valueset.slug,
                "name": valueset.name,
                "status": valueset.status,
                # exactly what CARE's sync_valueset stores (C3): exclude_defaults keeps
                # "exclude": [], which CARE's ValueSet.create_composition needs
                "compose": json.loads(
                    json.dumps(valueset.composition.model_dump(exclude_defaults=True))
                ),
                "is_system_defined": True,
            }
        )
    return sorted(items, key=lambda item: item["slug"])
