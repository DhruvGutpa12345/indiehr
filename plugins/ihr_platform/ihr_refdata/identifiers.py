"""The "identifier_config" kind: hospital-wide patient identifier configs (P1-T15b2a;
docs/reviews/p1-t15b/design.md §3.2 and "After the attack" items 7-11 and 13).

A pack item is ``{slug, status, config}``: CARE's ``PatientIdentifierConfig`` with ``facility``
null. The slug is the system itself, lower case; a system is never changed (it is the identity
of every identifier pointing at the row), so a new system is a new item plus a removal. A pack
sets ``status`` and the whole ``config``; the row's own spelling of the system is kept.

What the engine asks of this kind beyond value sets (kinds.py):

- ``conflict``: two live hospital-wide rows with the system, or a facility's own row with it
  (CARE's create refuses that too). Nothing is written; accept is refused until fixed.
- ``data_checks``: ``unique`` turning on while live values repeat across patients, or
  ``regex`` changing while live values no longer match (Python ``re.match``, as CARE checks
  them). CARE re-validates every identifier a patient edit sends, so taking such a change makes
  those patients' edits fail: reason "data-check", counts only. The repeat check is one query
  under a statement timeout; the regex check streams values in batches under a time cap; over
  a cap the item is "not fully checked" (still held).
- ``admin_reason``: a change that widens who finds a patient by identifier ("privacy"), and a
  new auto-numbered config replacing one with values in the same release ("counter-restart").
- A soft-deleted row is the hospital's choice (``RESTORE_DELETED`` False): pending "deleted"
  only when the pack changes the item; accept restores it.
"""

import hashlib
import json
import logging
import re
import time

from django.db import OperationalError, connection, transaction
from django.db.models import Count, Max
from django.db.models.fields.json import KT
from django.db.models.functions import Lower

log = logging.getLogger("ihr.refdata")

KIND = "identifier_config"
SCOPE = "hospital"
RESTORE_DELETED = False  # a deleted config is the hospital's choice (Q-K63)
RECHECK_CONFLICTS = True  # duplicates by CARE's own writers are looked for after commit
FIELDS = ("status", "config")
ITEM_KEYS = frozenset(("slug", "status", "config"))
STATUSES = ("draft", "active", "inactive")
RETIRED = "inactive"  # CARE has no "retired" status
MAX_KEY = 255
# CARE's own systems (its signals maintain them): never in a pack
CARE_HOST = "care.ohc.network"
SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://")
# Retrieve options (Q-K71). CARE's server checks only the year of birth before it shows a
# patient found by a value (care/emr/api/viewsets/patient.py search); the date of birth and the
# OTP are asked by its web form alone. So a config without YEAR is loose; any of CHECKS
# turning off still waits for the admin (a conservative extra trigger).
YEAR = "retrieve_with_year_of_birth"
CHECKS = ("retrieve_with_dob", YEAR, "retrieve_with_otp")
PARTIAL = "retrieve_partial_search"
# Data check caps (item 8)
REPEAT_TIMEOUT_MS = 30_000
REGEX_SECONDS = 60
REGEX_BATCH = 2000


def _models():
    from care.emr.models.patient import PatientIdentifier, PatientIdentifierConfig

    return PatientIdentifierConfig, PatientIdentifier


def _spec():
    from care.emr.resources.patient_identifier.spec import IdentifierConfig

    return IdentifierConfig


def stored_config(config, system=None):
    """The config as written: CARE's model with its defaults filled (``system`` kept as the
    row spells it when given)."""
    out = _spec().model_validate(config).model_dump(mode="json")
    if system is not None:
        out["system"] = system
    return out


def normal_config(config):
    """The config by meaning: defaults filled, the system in lower case. A config CARE's own
    model refuses is compared as it is (it can only differ from the pack's)."""
    try:
        out = _spec().model_validate(config).model_dump(mode="json")
    except Exception:  # any stored shape: hashed as it is
        return config
    out["system"] = out["system"].lower()
    return out


def canonical(fields):
    return {"status": fields["status"], "config": normal_config(fields["config"])}


def _hash(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def content_hash(fields):
    return _hash(canonical(fields))


def item_fields(item):
    return {"status": item["status"], "config": item["config"]}


def row_fields(row):
    return {"status": row.status, "config": row.config}


def care_system(system):
    host = SCHEME.sub("", system.lower()).split("/", 1)[0]
    return host == CARE_HOST or host.endswith("." + CARE_HOST)


def check_item(item):
    """A pack item (ValueError/TypeError/KeyError otherwise): the system as the slug, a status
    CARE knows, a config CARE's own model accepts (its default value through CARE's dummy
    evaluator, no database) with no field CARE would drop, a regex that compiles, and never one
    of CARE's own systems."""
    if not isinstance(item, dict):
        msg = "not an object"
        raise TypeError(msg)
    missing = sorted(ITEM_KEYS - set(item))
    if missing:
        msg = f"missing {', '.join(missing)}"
        raise KeyError(msg)
    if extra := sorted(set(item) - ITEM_KEYS):
        msg = f"unknown fields {', '.join(extra)}"
        raise ValueError(msg)
    slug = item["slug"]
    if not isinstance(slug, str) or not slug or len(slug) > MAX_KEY:
        msg = f"slug must be a string of 1-{MAX_KEY} characters"
        raise TypeError(msg)
    if item["status"] not in STATUSES:
        msg = f"status must be one of {', '.join(STATUSES)}"
        raise ValueError(msg)
    config = item["config"]
    if not isinstance(config, dict):
        msg = "config must be an object"
        raise TypeError(msg)
    if extra := sorted(set(config) - set(_spec().model_fields)):
        msg = f"config has fields CARE does not keep: {', '.join(extra)}"
        raise ValueError(msg)
    parsed = _spec().model_validate(
        config
    )  # pydantic's ValidationError is a ValueError
    if parsed.system != slug:
        msg = "the slug must be the config's system itself"
        raise ValueError(msg)
    if care_system(slug):
        msg = "CARE's own identifier systems are never in a pack"
        raise ValueError(msg)
    try:
        re.compile(parsed.regex)
    except re.error as exc:
        msg = f"regex does not compile: {exc}"
        raise ValueError(msg) from None


def item_hash(item):
    return content_hash(item_fields(item))


def row_hash(row):
    return content_hash(row_fields(row))


def retired_hash(row):
    return content_hash({**row_fields(row), "status": RETIRED})


def decision_hash(row):
    """What the admin decides on: the two fields and whether the row is soft-deleted."""
    return _hash({"fields": canonical(row_fields(row)), "deleted": bool(row.deleted)})


def is_retired(row):
    return row.status == RETIRED


# --- reading the hospital's rows (item 13) ------------------------------------------------


def _by_key(queryset):
    return queryset.annotate(ihr_key=Lower(KT("config__system")))


def rows(keys, *, lock=False, targets=None):
    """{key: row} for hospital-wide rows whose system is the key in any case. The live row is
    preferred (the newest); a deleted one only when no live one exists. ``row.ihr_live`` is
    how many live rows match (two or more is a conflict)."""
    model, _ = _models()
    found = _by_key(model._base_manager.filter(facility__isnull=True)).filter(  # noqa: SLF001
        ihr_key__in=list(keys)
    )
    if lock:
        # FOR NO KEY UPDATE: CARE's inserts of patient identifiers (their foreign key takes
        # FOR KEY SHARE on the config) never wait for it (review E4)
        found = found.select_for_update(no_key=True, of=("self",))
    out = {}
    for row in found.order_by("deleted", "-id"):
        if row.ihr_key not in out:
            row.ihr_live = 0 if row.deleted else 1
            out[row.ihr_key] = row
        elif not row.deleted:
            out[row.ihr_key].ihr_live += 1
    return out


def all_present(keys, targets=None):
    """True when every key has a hospital-wide row in any state (item 11: a deleted one is the
    hospital's own; nothing to do for it until the pack changes)."""
    model, _ = _models()
    present = (
        _by_key(model._base_manager.filter(facility__isnull=True))  # noqa: SLF001
        .filter(ihr_key__in=list(keys))
        .values("ihr_key")
        .distinct()
        .count()
    )
    return present == len(set(keys))


def lock(key):
    """Our writers of one system in this hospital wait for each other (item 9). CARE's own
    writers do not take it (no trigger on CARE's table): duplicates they make are found after
    commit."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s || %s))",
            [connection.schema_name, key],
        )


def conflict(key, row, item):
    """Why nothing can be written for ``key`` ("" when nothing blocks)."""
    if row is not None and getattr(row, "ihr_live", 0) > 1:
        return f"{row.ihr_live} live hospital-wide configs have this system"
    model, _ = _models()
    if (
        _by_key(model.objects.filter(facility__isnull=False))
        .filter(ihr_key=key)
        .exists()
    ):
        return "a facility's own config has this system"
    return ""


# --- what the admin is shown --------------------------------------------------------------


def label(row, item):
    for config in (getattr(row, "config", None), (item or {}).get("config")):
        if isinstance(config, dict) and isinstance(config.get("display"), str):
            return config["display"]
    return (item or {}).get("slug", "")


def effect(row, item):
    if item is None:
        return "retire"
    if row is None:
        return "create"
    return "restore" if row.deleted else "update"


def shown(row=None, item=None, *, retire=False):
    if row is None and item is None:
        return None
    fields = canonical(row_fields(row) if item is None else item_fields(item))
    if retire:
        fields["status"] = RETIRED
    return fields


# --- writes (always .save(): CARE's post_save clears its Redis copy) ----------------------


def create(item):
    model, _ = _models()
    row = model(
        facility=None, status=item["status"], config=stored_config(item["config"])
    )
    row.save()
    return row


def write(row, item, *, restore=False):
    """Set status and config (the row's own spelling of the system kept) and un-delete."""
    own = row.config.get("system") if isinstance(row.config, dict) else None
    keep = own if isinstance(own, str) and own.lower() == item["slug"] else None
    config = stored_config(item["config"], keep)
    changed = []
    if row.status != item["status"]:
        row.status = item["status"]
        changed.append("status")
    if row.config != config:
        row.config = config
        changed.append("config")
    if restore and row.deleted:
        row.deleted = False
        changed.append("deleted")
    if changed:
        row.save(update_fields=[*changed, "modified_date"])
    return row


def retire(row):
    """Old values keep resolving: the row is only made inactive."""
    if row.status != RETIRED:
        row.status = RETIRED
        row.save(update_fields=["status", "modified_date"])
    return row


# --- changes the admin must take, and data checks -----------------------------------------


def _retrieve(config):
    if not isinstance(config, dict):
        return {}
    value = config.get("retrieve_config")
    return value if isinstance(value, dict) else {}


def _loose(retrieve):
    """Partial search, or no check CARE's server makes before a patient is shown (review C1:
    only the year of birth is checked there)."""
    return bool(retrieve.get(PARTIAL)) or not retrieve.get(YEAR)


def _loosened(old, new):
    """``new`` retrieve options let more people find a patient than ``old``."""
    return bool(new.get(PARTIAL) and not old.get(PARTIAL)) or any(
        old.get(n) and not new.get(n) for n in CHECKS
    )


def _active(row):
    """CARE validates values against, and lists, live active hospital-wide configs only."""
    return row is not None and not row.deleted and row.status == "active"


def _values(row):
    _, identifier = _models()
    return identifier.objects.filter(config=row)


def admin_reason(row, item, leaving):
    """("privacy" | "counter-restart" | "", checks): a change only the admin may take, whatever
    the row's edit state (items 7 and 10)."""
    new = stored_config(item["config"])
    retrieve = _retrieve(new)
    # CARE's search resolves a config by its id whatever its status (patient.py: no status
    # filter), so loosening any existing row's options waits, inactive and draft ones too (C2)
    if row is not None and _loosened(_retrieve(normal_config(row.config)), retrieve):
        return "privacy", {}
    if item["status"] == "active" and not _active(row) and _loose(retrieve):
        return "privacy", {}  # a new or newly active config with loose retrieval
    if row is None and new.get("auto_maintained"):
        for old in leaving:
            config = old.config if isinstance(old.config, dict) else {}
            if config.get("auto_maintained") and not is_retired(old):
                values = _values(old).count()
                if values:
                    return "counter-restart", {"counts": {"old_values": values}}
    return "", {}


def data_checks(row, item):
    """The checks this change needs (none for a new row: it has no values yet). Only the rule
    in force counts (review E7): CARE validates a patient's values against active hospital-wide
    configs alone (``get_instance_config``), so a change to an inactive or draft config needs
    none, and one that makes a config active needs them for its rule as a whole."""
    if row is None:
        return ()
    old = row.config if isinstance(row.config, dict) else {}
    new = item["config"]
    was, will = _active(row), item["status"] == "active"
    names = []
    if will and new.get("unique") and not (was and old.get("unique")):
        names.append("repeated")
    if will and new.get("regex") and not (was and new["regex"] == old.get("regex")):
        names.append("not_matching")
    return tuple(names)


def watermark(row):
    """What the checks read: [newest id, newest change, how many] of the config's values
    (deleted ones included, so a soft delete moves it)."""
    _, identifier = _models()
    found = identifier._base_manager.filter(config=row).aggregate(  # noqa: SLF001
        top=Max("id"), at=Max("modified_date"), n=Count("id")
    )
    at = found["at"].isoformat() if found["at"] else ""
    return [found["top"] or 0, at, found["n"]]


REPEAT_SQL = """
SELECT DISTINCT i.patient_id FROM {table} i
JOIN (
    SELECT value FROM {table}
    WHERE config_id = %s AND NOT deleted AND value <> ''
    GROUP BY value HAVING count(DISTINCT patient_id) > 1
) r ON r.value = i.value
WHERE i.config_id = %s AND NOT i.deleted
"""


def _repeated(row):
    """The patients whose value another patient also has (None when over the time cap)."""
    _, identifier = _models()
    table = identifier._meta.db_table  # noqa: SLF001
    query = REPEAT_SQL.format(table=connection.ops.quote_name(table))
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('statement_timeout')")
            (before,) = cursor.fetchone()
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                [f"{int(REPEAT_TIMEOUT_MS)}ms"],
            )
            cursor.execute(query, [row.pk, row.pk])
            found = {patient for (patient,) in cursor.fetchall()}
            cursor.execute("SELECT set_config('statement_timeout', %s, true)", [before])
    except OperationalError:  # cancelled at the cap; the savepoint undid the setting
        return None
    return found


def _not_matching(row, regex):
    """(patients with a value the regex does not match, complete) within the time cap."""
    pattern = re.compile(regex)
    deadline = time.monotonic() + REGEX_SECONDS
    bad, last = set(), 0
    while True:
        if time.monotonic() >= deadline:
            return bad, False
        batch = list(
            _values(row)
            .filter(id__gt=last)
            .exclude(value="")
            .order_by("id")
            .values_list("id", "patient_id", "value")[:REGEX_BATCH]
        )
        if not batch:
            return bad, True
        for _, patient, value in batch:
            if not pattern.match(value):
                bad.add(patient)
        last = batch[-1][0]


def run_data_checks(row, item, names):
    """{"counts": {name: patients}, "patients": patients in all, "complete": bool}. Counts
    only: never a value."""
    counts, patients, complete = {}, set(), True
    if "repeated" in names:
        found = _repeated(row)
        if found is None:
            complete = False
        else:
            counts["repeated"] = len(found)
            patients |= found
    if "not_matching" in names:
        found, done = _not_matching(row, item["config"]["regex"])
        complete = complete and done
        counts["not_matching"] = len(found)
        patients |= found
    return {"counts": counts, "patients": len(patients), "complete": complete}
