"""Per-facility catalogue kinds (P1-T15b3; docs/reviews/p1-t15b/design.md §4 and "After the
attack" items 11, 13 and 14).

CARE's catalogue rows (SpecimenDefinition, ActivityDefinition, ObservationDefinition, ...) are a
facility's own: CARE stores the slug ``f-<facility external id>-<slug_value>`` and keeps links
between them as id arrays resolved inside one facility (activity_definition.py
``convert_external_id_to_internal_id``: ``slug=…, facility=instance.facility``, deleted rows
excluded). A kind built on ``Catalogue`` keeps the kinds contract (kinds.py) that way:

- the pack item's ``slug`` is the key (CARE's slug_value, lower case); the row is the
  facility's row whose ``lower(slug)`` is ``f-<facility>-<key>`` (item 13): the live row
  preferred (the newest id), a deleted one only when no live one exists, two or more live rows
  a ``conflict``. ``PackItem.target`` records the stored slug;
- a link (``LINKS = {field: target kind}``) is held in the pack as the target's key and
  resolved by the same rule to the live row of the *same facility*; nothing is written while a
  target is missing (pending ``missing-link``, Q-K68 A) or names two live rows (``conflict``).
  Hashes use the link keys (ids mapped back by the facility's slug prefix), never ids, so every
  facility hashes alike; list order is kept;
- a deleted row is the hospital's own choice (``RESTORE_DELETED = False``);
- an item carries exactly the kind's keys: never a price or anything else (Q-K69 A).

Only test kinds use it so far (ihr_refdata/tests/kinds.py, Q-K70 A); no catalogue content
ships in P1-T15b. **Known limit (Q-K75):** a hospital can rename a row's slug (CARE recomputes
it from slug_value), after which the engine would create a second row; a production kind
(P1-T15b4+) must first bind each record to the row's ``external_id``.
"""

import hashlib
import json
import re

from django.db.models.functions import Lower
from django.utils import timezone

# CARE's SlugType, in lower case only (item 13: keys are matched in lower case)
KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*[a-z0-9]$")
SLUG_MAX = 255  # CARE's slug column
PREFIX_LENGTH = len("f--") + 36  # f-<uuid>-
MAX_KEY = SLUG_MAX - PREFIX_LENGTH
MAX_TITLE = 1024
RETIRED = "retired"
STALE = "~stale"  # a link to a key's deleted or superseded row (never a key: KEY has no "~")
STATUSES = ("active", "draft")  # what a pack ships; CARE's "retired" is the removal


def prefix(facility):
    """CARE's stored slug prefix of ``facility``'s rows (SlugBaseModel.calculate_slug)."""
    return f"f-{facility.external_id}-"


def slug_of(facility, key):
    return prefix(facility) + key


def find(model, facility, keys, *, lock=False):
    """{key: row} of ``facility``'s rows of ``model`` (soft-deleted ones included) whose
    lower(slug) is the key's slug there. One rule for rows and links (item 13): the live row
    preferred (newest id), a deleted one only when no live one exists; ``row.ihr_live`` is how
    many live rows match. Never a row of another facility, whatever its slug says."""
    wanted = {slug_of(facility, key).lower(): key for key in keys}
    if not wanted:
        return {}
    found = (
        model._base_manager.filter(facility=facility)  # noqa: SLF001
        .annotate(ihr_slug=Lower("slug"))
        .filter(ihr_slug__in=list(wanted))
    )
    if lock:
        found = found.select_for_update(no_key=True, of=("self",))
    out = {}
    for row in found.order_by("deleted", "-id"):
        key = wanted[row.ihr_slug]
        if key not in out:
            row.ihr_live = 0 if row.deleted else 1
            row.ihr_prefix = prefix(facility).lower()
            out[key] = row
        elif not row.deleted:
            out[key].ihr_live += 1
    return out


def _hash(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


class Catalogue:
    """The kinds contract for one CARE catalogue model, per facility (subclass it: KIND,
    FIELDS, LINKS, ``model()``, ``DEFAULTS`` and ``check_fields``)."""

    KIND = ""
    SCOPE = "facility"
    RESTORE_DELETED = False  # a deleted catalogue row is the hospital's choice
    # CARE's slug column is not unique and CARE's writers never take our lock: a duplicate
    # live row made while the engine wrote is found after commit (item 9, review E5)
    RECHECK_CONFLICTS = True
    FIELDS = ()  # the fields a pack sets, link fields included
    LINKS = {}  # {field: target kind}: id arrays of the target kind's rows, same facility
    DEFAULTS = {}  # required columns a pack does not set

    @classmethod
    def model(cls):
        raise NotImplementedError

    @classmethod
    def check_fields(cls, item):
        """The kind's own shape checks (ValueError/TypeError)."""

    # --- shape (no database: prepare runs in public) ----------------------------------------

    @classmethod
    def check_item(cls, item):
        if not isinstance(item, dict):
            msg = "not an object"
            raise TypeError(msg)
        keys = set(item)
        wanted = {"slug", *cls.FIELDS}
        if keys != wanted:
            # Exactly the kind's keys: nothing else rides along (Q-K69 A: never a price)
            extra, missing = sorted(keys - wanted), sorted(wanted - keys)
            msg = f"keys must be exactly {sorted(wanted)} (extra {extra}, missing {missing})"
            raise KeyError(msg)
        key = item["slug"]
        if not isinstance(key, str) or not KEY.match(key) or len(key) > MAX_KEY:
            msg = f"slug must be CARE's slug pattern in lower case, at most {MAX_KEY}"
            raise ValueError(msg)
        title = item.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE:
            msg = f"title must be a non-empty string of at most {MAX_TITLE} characters"
            raise ValueError(msg)
        if item.get("status") not in STATUSES:
            msg = f"status must be one of {', '.join(STATUSES)}"
            raise ValueError(msg)
        for field in cls.LINKS:
            links = item[field]
            if not isinstance(links, list) or not all(
                isinstance(k, str) and KEY.match(k) and len(k) <= MAX_KEY for k in links
            ):
                msg = f"{field} must be a list of lower-case keys"
                raise ValueError(msg)
            if len(set(links)) != len(links):
                msg = f"{field} names a key twice"
                raise ValueError(msg)
        cls.check_fields(item)

    # --- hashes (link keys, never ids) ------------------------------------------------------

    @classmethod
    def item_fields(cls, item):
        return {name: item[name] for name in cls.FIELDS}

    @classmethod
    def links_of(cls, row):
        """{field: [target tokens]}: the row's id arrays as keys of its facility, order kept,
        by ``find``'s rule (item 13): an id that is the live row ``find`` picks for its key is
        that key; one of this facility's rows of a key that is deleted, or is not the live row
        picked (a newer live one exists), is ``<key>~stale`` (review T1); any other id (another
        facility's row, or gone) stays visible as ``#<id>``. Neither ever hashes like a pack
        key. One query per link field."""
        cached = getattr(row, "ihr_links", None)
        if cached is not None:
            return cached
        from django.db.models import Q

        from ihr_refdata import kinds

        mine = getattr(row, "ihr_prefix", None) or prefix(row.facility).lower()
        out = {}
        for field, target in cls.LINKS.items():
            ids = list(getattr(row, field) or [])
            if not ids:
                out[field] = []
                continue
            model = kinds.get(target).model()
            same = model._base_manager.filter(facility_id=row.facility_id)  # noqa: SLF001
            named = same.filter(id__in=ids).annotate(s=Lower("slug")).values("s")
            seen = {}  # id -> (lower slug, deleted)
            picked = {}  # lower slug -> the id find() picks for it
            for pk, slug, deleted in (
                same.annotate(ihr_slug=Lower("slug"))
                .filter(Q(id__in=ids) | Q(ihr_slug__in=named))
                .order_by("deleted", "-id")
                .values_list("id", "ihr_slug", "deleted")
            ):
                seen[pk] = (slug, deleted)
                picked.setdefault(slug, pk)
            keys = []
            for pk in ids:
                slug, deleted = seen.get(pk, ("", True))
                if not slug.startswith(mine):
                    keys.append(f"#{pk}")
                elif picked[slug] == pk and not deleted:
                    keys.append(slug[len(mine) :])
                else:
                    keys.append(f"{slug[len(mine) :]}{STALE}")
            out[field] = keys
        row.ihr_links = out
        return out

    @classmethod
    def row_fields(cls, row):
        links = cls.links_of(row) if cls.LINKS else {}
        return {
            name: links[name] if name in cls.LINKS else getattr(row, name)
            for name in cls.FIELDS
        }

    @classmethod
    def item_hash(cls, item):
        return _hash(cls.item_fields(item))

    @classmethod
    def row_hash(cls, row):
        return _hash(cls.row_fields(row))

    @classmethod
    def retired_hash(cls, row):
        return _hash({**cls.row_fields(row), "status": RETIRED})

    @classmethod
    def decision_hash(cls, row):
        return _hash({"fields": cls.row_fields(row), "deleted": bool(row.deleted)})

    @classmethod
    def stale_hash(cls, row):
        """The row's hash with each stale link read as its key: what the pack wrote before the
        hospital deleted (or replaced) a target. "" when no link is stale (review T1: the
        engine then re-points the link to the key's live row, or holds the item as
        ``missing-link`` until there is one)."""
        if not cls.LINKS:
            return ""
        links = cls.links_of(row)
        if not any(k.endswith(STALE) for keys in links.values() for k in keys):
            return ""
        fields = cls.row_fields(row)
        for name in cls.LINKS:
            fields[name] = [k.removesuffix(STALE) for k in links[name]]
        return _hash(fields)

    @classmethod
    def is_retired(cls, row):
        return row.status == RETIRED

    # --- the facility's rows ----------------------------------------------------------------

    @classmethod
    def rows(cls, keys, *, lock=False, targets=None, facility):
        """{key: row} in ``facility`` (item 13's rule, ``find``); ``targets`` is not needed:
        the key and the facility name the row."""
        return find(cls.model(), facility, keys, lock=lock)

    @classmethod
    def all_present(cls, keys, targets=None, *, facility):
        """True when every key has a row in ``facility`` in any state (item 11: a deleted one
        is the hospital's own until the pack changes the item)."""
        keys = set(keys)
        if not keys:
            return True
        return len(cls.rows(keys, facility=facility)) == len(keys)

    @classmethod
    def record_target(cls, item, row, *, facility):
        return row.slug if row is not None else slug_of(facility, item["slug"])

    @classmethod
    def resolve(cls, item, facility, *, lock=False):
        """{field: {key: row}}: the rows the item's links name in ``facility`` (``find``'s rule
        through the target kind's own ``rows``)."""
        from ihr_refdata import kinds

        out = {}
        for field, target in cls.LINKS.items():
            out[field] = (
                kinds.get(target).at(facility).rows(item[field], lock=lock)
                if item[field]
                else {}
            )
        return out

    @classmethod
    def conflict(cls, key, row, item, *, facility):
        if row is not None and getattr(row, "ihr_live", 0) > 1:
            return f"{row.ihr_live} live rows in this facility have this key"
        if item is None:
            return ""
        for field, found in cls.resolve(item, facility).items():
            twice = sorted(k for k, r in found.items() if r.ihr_live > 1)
            if twice:
                return f"{field}: {', '.join(twice)} name two or more live rows here"
        return ""

    @classmethod
    def missing_link(cls, key, row, item, *, facility):
        """Q-K68 A: a link target not live in this facility holds the item (nothing written).
        The targets are locked (review E4): the write in the same transaction sees them, and a
        deletion meanwhile waits for it."""
        if item is None:
            return ""
        gone = []
        for field, found in cls.resolve(item, facility, lock=True).items():
            gone += [
                f"{field}:{k}"
                for k in item[field]
                if k not in found or found[k].deleted
            ]
        if not gone:
            return ""
        return f"not in this facility (or deleted): {', '.join(gone)}"

    @classmethod
    def linkable(cls, items, *, facility):
        """Item 11: True when some held item could be written now: every key it links to has
        a live row in this facility (review T2: not merely one of them). One query per link
        field."""
        from ihr_refdata import kinds

        live = {}
        for field, target in cls.LINKS.items():
            wanted = {
                slug_of(facility, k).lower(): k
                for item in items
                for k in item.get(field, [])
            }
            if not wanted:
                live[field] = set()
                continue
            model = kinds.get(target).model()
            live[field] = {
                wanted[slug]
                for slug in model._base_manager.filter(  # noqa: SLF001
                    facility=facility, deleted=False
                )
                .annotate(ihr_slug=Lower("slug"))
                .filter(ihr_slug__in=list(wanted))
                .values_list("ihr_slug", flat=True)
            }
        return any(
            all(k in live[field] for field in cls.LINKS for k in item.get(field, []))
            for item in items
        )

    # --- writes (always .save()) ------------------------------------------------------------

    @classmethod
    def _values(cls, item, facility):
        values = {}
        resolved = cls.resolve(item, facility, lock=True)
        for name in cls.FIELDS:
            if name not in cls.LINKS:
                values[name] = item[name]
                continue
            found = resolved[name]
            missing = [k for k in item[name] if k not in found or found[k].deleted]
            if missing:  # the engine holds such an item (missing_link): never written
                msg = f"{name}: {', '.join(missing)} not in this facility"
                raise ValueError(msg)
            values[name] = [found[k].id for k in item[name]]
        return values

    @classmethod
    def create(cls, item, *, facility):
        model = cls.model()
        row = model(
            facility=facility,
            slug=slug_of(facility, item["slug"]),
            **cls.DEFAULTS,
            **cls._values(item, facility),
        )
        row.save()
        return row

    @classmethod
    def write(cls, row, item, *, restore=False):
        changed = []
        for name, value in cls._values(item, row.facility).items():
            if getattr(row, name) != value:
                setattr(row, name, value)
                changed.append(name)
        if restore and row.deleted:
            row.deleted = False
            changed.append("deleted")
        if changed:
            row.ihr_links = None
            row.modified_date = timezone.now()
            row.save(update_fields=[*changed, "modified_date"])
        return row

    @classmethod
    def retire(cls, row):
        if row.status != RETIRED:
            row.status = RETIRED
            row.modified_date = timezone.now()
            row.save(update_fields=["status", "modified_date"])
        return row

    # --- what the admin is shown ------------------------------------------------------------

    @classmethod
    def label(cls, row, item):
        if row is not None:
            return row.title
        return item["title"] if item is not None else ""

    @classmethod
    def effect(cls, row, item):
        if item is None:
            return "retire"
        if row is None:
            return "create"
        return "restore" if row.deleted else "update"

    @classmethod
    def shown(cls, row=None, item=None, *, retire=False):
        if row is None and item is None:
            return None
        fields = cls.row_fields(row) if item is None else cls.item_fields(item)
        if retire:
            fields["status"] = RETIRED
        return fields
