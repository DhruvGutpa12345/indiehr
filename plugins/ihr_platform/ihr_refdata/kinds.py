"""The kinds a reference pack can hold, and the contract every kind keeps (P1-T15b2a, b2b;
docs/reviews/p1-t15b/design.md §3.1 and "After the attack" items 2, 7-11 and 13).

A kind is a module; the engine, the manifest and the decision API reach it only through
``get(kind)``, which fills the optional names with their defaults. ``ihr_refdata.tests.
test_kinds`` checks every registered kind against this contract.

Required (no default):

    KIND                      the pack files' "kind"
    FIELDS                    the fields a pack sets (and the admin is shown)
    check_item(item)          shape only, no database (prepare runs in ``public``)
    item_hash(item), row_hash(row), retired_hash(row), decision_hash(row)
    rows(keys, *, lock=False, targets=None)
                              {key: row}, soft-deleted rows included. ``targets``: {key: the
                              record's target}; a kind whose row is not the key (forms) resolves
                              by it, and the others ignore it (item 2)
    all_present(keys, targets=None)
                              True when there is nothing to do without a pack change
    create(item), write(row, item, *, restore=False), retire(row), is_retired(row)
                              writes, always ``.save()`` (CARE's post_save signals fire);
                              create and write return the row that now holds the item
    label(row, item), effect(row, item), shown(row=, item=, *, retire=False)
                              what the admin is shown (P1-T15b1)

Optional (default):

    SCOPE                     "hospital" ("facility": P1-T15b3)
    RESTORE_DELETED           True: a soft-deleted row is restored; False: it is a local
                              edit, pending "deleted" only when the pack changes the item
    check_release(versions)   checks across every release of one pack (none)
    target(item)              the natural key of the row when it is not the item ("")
    record_target(item, row)  what PackItem.target records once ``row`` holds the item: the
                              engine moves it in the same transaction as the write (item 2; "")
    change(row)               what the change list adds beside a write of ``row`` ({})
    notes(row, item)          extra counts for the item detail ({}; forms: item 12)
    lock(key)                 serialises writers of one key (nothing)
    conflict(key, row, item)  "" or why nothing can be written (structural; blocks accept)
    missing_link(key, row, item)
                              "" or why a row it must link to is missing (pending
                              "missing-link", asked again at every apply; blocks accept; Q-K68)
    RECHECK_CONFLICTS         False; True: conflict() is asked again after the write commits
                              (writers outside the engine do not take ``lock``)
    data_checks(row, item)    the data checks this change needs (none)
    run_data_checks(row, item, names) -> {"counts", "patients", "complete"}
    watermark(row)            what the data checks read has moved since ([])
    admin_reason(row, item, leaving) -> (reason, checks): a change only the admin may take
                              ("privacy", "counter-restart"; "" for none)
    usable(row), make_usable(row)
                              a row CARE cannot read, mended without losing its content
    LINKS                     {} ({field: target kind}: P1-T15b3, per-facility kinds only)
    linkable(items)           True (P1-T15b3 item 11): a held "missing-link" item may now be
                              written, so the start-up short-circuit reconciles again (one
                              query; the default always does)
    stale_hash(row)           "" (P1-T15b3, review T1): the row's hash with every link to a
                              deleted or superseded target read as its key; when it equals
                              what the pack wrote, the link is re-pointed (or the item held
                              as "missing-link"), never counted as the hospital's edit

Per-facility kinds (``SCOPE = "facility"``, P1-T15b3, design.md §4): applied once per live
facility. The engine reaches them only through ``get(kind).at(facility)``, which passes
``facility=`` to each of FACILITY_BOUND the kind defines (``rows``, ``all_present`` and
``create`` must take it); a hospital-wide kind is its own ``at(None)``. A kind may be a module
or a class with the same names (ihr_refdata.catalogue). Test-only kinds
(``ihr_refdata.tests.kinds``) are registered only while ``IHR_REFDATA_TEST_KINDS`` is on: the
test settings alone; the deployment settings refuse it (settings/secure.py), so a pack of
theirs is an unknown kind on a server and ``prepare()`` refuses it for the fleet.
"""

import inspect
from functools import partial

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from ihr_refdata import forms, identifiers, valuesets

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
    "record_target": lambda item, row: "",
    "change": lambda row: {},
    "notes": lambda row, item: {},
    "lock": _none,
    "conflict": lambda key, row, item: "",
    "missing_link": lambda key, row, item: "",
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
    "LINKS": {},
    "linkable": lambda items: True,
    "stale_hash": lambda row: "",
}
CONTRACT = (*REQUIRED, *DEFAULTS)
# What a per-facility kind is asked inside one facility: ``facility=`` is passed to each it
# defines (P1-T15b3)
FACILITY_BOUND = (
    "rows",
    "all_present",
    "create",
    "record_target",
    "conflict",
    "missing_link",
    "linkable",
)
FACILITY_REQUIRED = ("rows", "all_present", "create")


class Kind:
    """One kind's module with the contract's defaults filled in."""

    def __init__(self, module):
        missing = [name for name in REQUIRED if not hasattr(module, name)]
        if missing:
            msg = f"pack kind {module.__name__} lacks {', '.join(missing)}"
            raise ImproperlyConfigured(msg)
        if not {"lock", "targets"} <= set(inspect.signature(module.rows).parameters):
            msg = f"pack kind {module.__name__}: rows() must take lock= and targets="
            raise ImproperlyConfigured(msg)
        if "targets" not in inspect.signature(module.all_present).parameters:
            msg = f"pack kind {module.__name__}: all_present() must take targets="
            raise ImproperlyConfigured(msg)
        scope = getattr(module, "SCOPE", DEFAULTS["SCOPE"])
        if scope not in SCOPES:
            msg = (
                f"pack kind {module.__name__}: SCOPE must be one of {', '.join(SCOPES)}"
            )
            raise ImproperlyConfigured(msg)
        if scope == "facility":
            for name in FACILITY_BOUND:
                if not hasattr(module, name) and name not in FACILITY_REQUIRED:
                    continue
                if (
                    "facility"
                    not in inspect.signature(getattr(module, name)).parameters
                ):
                    msg = f"pack kind {module.__name__}: {name}() must take facility="
                    raise ImproperlyConfigured(msg)
        elif getattr(module, "LINKS", None):
            msg = f"pack kind {module.__name__}: only a per-facility kind links (LINKS)"
            raise ImproperlyConfigured(msg)
        self.module = module

    def at(self, facility):
        """This kind inside ``facility`` (a per-facility kind) or hospital-wide (``None``)."""
        if self.SCOPE == "facility":
            if facility is None:
                msg = f"{self!r} is applied per facility: name the facility"
                raise ValueError(msg)
            return _AtFacility(self, facility)
        if facility is not None:
            msg = f"{self!r} is hospital-wide: it has no facility"
            raise ValueError(msg)
        return self

    def __getattr__(self, name):
        try:
            return getattr(self.module, name)
        except AttributeError:
            if name in DEFAULTS:
                return DEFAULTS[name]
            raise

    def __repr__(self):
        return f"<kind {self.module.KIND}>"


class _AtFacility:
    """A per-facility kind inside one facility: ``facility=`` passed to what it defines of
    FACILITY_BOUND; everything else is the kind's own."""

    def __init__(self, kind, facility):
        self.kind = kind
        self.facility = facility

    def __getattr__(self, name):
        value = getattr(self.kind, name)
        if name in FACILITY_BOUND and hasattr(self.kind.module, name):
            return partial(value, facility=self.facility)
        return value

    def __repr__(self):
        return f"<kind {self.kind.KIND} in facility {self.facility.pk}>"


_BUILT = {module.KIND: Kind(module) for module in (valuesets, identifiers, forms)}
# The test-only kinds, made once per process when first asked for (code, no hospital data)
_TEST_ONLY = {}


def _registry():
    if not getattr(settings, "IHR_REFDATA_TEST_KINDS", False):
        return _BUILT
    if not _TEST_ONLY:
        from ihr_refdata.tests import kinds as test_kinds

        _TEST_ONLY.update({k.KIND: Kind(k) for k in test_kinds.KINDS})
    return {**_BUILT, **_TEST_ONLY}


def names():
    """Every registered kind, sorted."""
    return tuple(sorted(_registry()))


def get(kind):
    """The kind ``kind`` (KeyError for one this build does not know)."""
    return _registry()[kind]
