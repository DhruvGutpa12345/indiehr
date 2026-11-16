"""The field difference an admin is shown before accepting or keeping a pack item (P1-T15b1).

Three columns per field the pack sets: the hospital's row now (``hospital``), what accepting
writes (``pack``) and what the pack last applied here (``previous``; None when unknown). Large
JSON fields are shown as their changed paths only: at most MAX_CHANGES, each value cut to
MAX_VALUE characters, with ``truncated`` saying whether anything was left out or cut. Only the
fields given are ever shown (never a row's history, meta, authors or other ids).
"""

import json

LARGE = frozenset(("compose", "questions", "config"))
MAX_CHANGES = 100
MAX_VALUE = 200
_ABSENT = object()


def flatten(value, path="$"):
    """{path: leaf} in document order; an empty object or list is a leaf of its own."""
    if isinstance(value, dict) and value:
        out = {}
        for key, inner in value.items():
            out.update(flatten(inner, f"{path}.{key}"))
        return out
    if isinstance(value, list) and value:
        out = {}
        for index, inner in enumerate(value):
            out.update(flatten(inner, f"{path}[{index}]"))
        return out
    return {path: value}


def _cut(value):
    """(value as shown, whether it was cut). Strings longer than MAX_VALUE are cut; an empty
    object or list stays as it is."""
    if value is _ABSENT:
        return None, False
    if isinstance(value, str) and len(value) > MAX_VALUE:
        return value[:MAX_VALUE], True
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
        if len(text) > MAX_VALUE:
            return text[:MAX_VALUE], True
    return value, False


def json_changes(hospital, pack, previous):
    """The changed paths of one large field: ([{path, hospital, pack, previous}], truncated).
    A side that is None (no row, nothing to write, previous unknown) has no paths."""
    sides = [flatten(v) if v is not None else {} for v in (hospital, pack, previous)]
    paths = list(dict.fromkeys(p for side in sides for p in side))
    changed = []
    for path in paths:
        values = [side.get(path, _ABSENT) for side in sides]
        here, new, before = values
        if here == new and (previous is None or before == here):
            continue
        changed.append((path, values))
    truncated = len(changed) > MAX_CHANGES
    rows = []
    for path, values in changed[:MAX_CHANGES]:
        shown = {}
        for name, value in zip(("hospital", "pack", "previous"), values, strict=True):
            shown[name], cut = _cut(value)
            truncated = truncated or cut
        rows.append({"path": path, **shown})
    return rows, truncated


def fields(names, hospital, pack, previous):
    """One entry per field the pack sets. ``hospital``, ``pack`` and ``previous`` are
    {field: value} (canonical, as compared) or None."""
    out = []
    for name in names:
        values = [
            side.get(name) if side is not None else None
            for side in (hospital, pack, previous)
        ]
        if name in LARGE:
            changes, truncated = json_changes(*values)
            out.append({"field": name, "changes": changes, "truncated": truncated})
            continue
        shown = {}
        truncated = False
        for column, value in zip(("hospital", "pack", "previous"), values, strict=True):
            shown[column], cut = _cut(value)
            truncated = truncated or cut
        entry = {"field": name, **shown}
        if truncated:
            entry["truncated"] = True
        out.append(entry)
    return out
