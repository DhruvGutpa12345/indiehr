"""The kinds a reference pack can hold, and the contract every kind keeps (P1-T15b2a;
docs/reviews/p1-t15b/design.md §3.1 and "After the attack" items 7-11 and 13).

A kind is a module; the engine, the manifest and the decision API reach it only through
``get(kind)``, which fills the optional names with their defaults. ``ihr_refdata.tests.
test_kinds`` checks every registered kind against this contract.

Required (no default):

    KIND                      the pack files' "kind"
    FIELDS                    the fields a pack sets (and the admin is shown)
    check_item(item)          shape only, no database (prepare runs in ``public``)
    item_hash(item), row_hash(row), retired_hash(row), decision_hash(row)
    rows(keys, *, lock=False) {key: row}, soft-deleted rows included
    all_present(keys)         True when there is nothing to do without a pack change
    create(item), write(row, item, *, restore=False), retire(row), is_retired(row)
                              writes, always ``.save()`` (CARE's post_save signals fire)
    label(row, item), effect(row, item), shown(row=, item=, *, retire=False)
                              what the admin is shown (P1-T15b1)

Optional (default):

    SCOPE                     "hospital" ("facility": P1-T15b3)
    RESTORE_DELETED           True: a soft-deleted row is restored; False: it is a local
                              edit, pending "deleted" only when the pack changes the item
    check_release(versions)   checks across every release of one pack (none)
    target(item)              the natural key of the row when it is not the item ("")
    lock(key)                 serialises writers of one key (nothing)
    conflict(key, row, item)  "" or why nothing can be written (structural; blocks accept)
    RECHECK_CONFLICTS         False; True: conflict() is asked again after the write commits
                              (writers outside the engine do not take ``lock``)
    data_checks(row, item)    the data checks this change needs (none)
    run_data_checks(row, item, names) -> {"counts", "patients", "complete"}
    watermark(row)            what the data checks read has moved since ([])
    admin_reason(row, item, leaving) -> (reason, checks): a change only the admin may take
                              ("privacy", "counter-restart"; "" for none)
    usable(row), make_usable(row)
                              a row CARE cannot read, mended without losing its content
"""

import inspect

from django.core.exceptions import ImproperlyConfigured

from ihr_refdata import identifiers, valuesets

SCOPES = ("hospital", "facility")
REQUIRED = (
    "KIND",
    "FIELDS",
    "check_item",
    "item_hash",
    "row_hash",
    "retired_hash",
    "decision_hash",
    "rows",
    "all_present",
    "create",
    "write",
    "retire",
    "is_retired",
    "label",
    "effect",
    "shown",
)


def _none(*args, **kwargs):
    return None


DEFAULTS = {
    "SCOPE": "hospital",
    "RESTORE_DELETED": True,
    "RECHECK_CONFLICTS": False,
    "check_release": _none,
    "target": lambda item: "",
    "lock": _none,
    "conflict": lambda key, row, item: "",
    "data_checks": lambda row, item: (),
    "run_data_checks": lambda row, item, names: {
        "counts": {},
        "patients": 0,
        "complete": True,
    },
    "watermark": lambda row: [],
    "admin_reason": lambda row, item, leaving: ("", {}),
    "usable": lambda row: True,
    "make_usable": lambda row: False,
}
CONTRACT = (*REQUIRED, *DEFAULTS)


class Kind:
    """One kind's module with the contract's defaults filled in."""

    def __init__(self, module):
        missing = [name for name in REQUIRED if not hasattr(module, name)]
        if missing:
            msg = f"pack kind {module.__name__} lacks {', '.join(missing)}"
            raise ImproperlyConfigured(msg)
        if "lock" not in inspect.signature(module.rows).parameters:
            msg = f"pack kind {module.__name__}: rows() must take lock="
            raise ImproperlyConfigured(msg)
        self.module = module

    def __getattr__(self, name):
        try:
            return getattr(self.module, name)
        except AttributeError:
            if name in DEFAULTS:
                return DEFAULTS[name]
            raise

    def __repr__(self):
        return f"<kind {self.module.KIND}>"


_REGISTRY = {module.KIND: Kind(module) for module in (valuesets, identifiers)}


def names():
    """Every registered kind, sorted."""
    return tuple(sorted(_REGISTRY))


def get(kind):
    """The kind ``kind`` (KeyError for one this build does not know)."""
    return _REGISTRY[kind]
