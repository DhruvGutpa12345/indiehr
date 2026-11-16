"""Apply reference packs to one hospital (P1-T15a; docs/reviews/p1-t15/design.md).

``apply_packs(hospital, operator=...)`` is lock-free: its caller holds the hospital's lock
(``ihr_pack_apply`` takes ``runner.exclusive``; provisioning already holds it for the whole
run; the demo seed takes it when the hospital is already live) or owns a hospital nobody else
can reach yet (``ihr_create_tenant`` applies the packs before the hospital becomes ACTIVE, and
``ihr_pack_apply`` only ever sees ACTIVE ones). It is idempotent: a second apply with nothing
new writes nothing at all, so the append-only audit trail stays quiet.

For each item of the newest version of each pack, in the hospital's schema:

    no row with the slug (deleted ones included)  create it from the pack     -> current
    row soft-deleted                              restore it with the pack    -> current
    row already equals the new content            nothing written to it       -> current
      ... but CARE cannot use its compose         the pack's compose written  -> current
      (no "include"/"exclude" list)               ("repaired")
    row equals what this pack last wrote          the pack's four fields set  -> current
    row edited, the pack has nothing new for it   left as it is (a local edit is the
                                                  hospital's own until the pack changes it)
    hospital kept its own version                 left; pending again only when the pack's
                                                  content for it changes again
    row differs, never written by this pack       left -> pending (unknown-origin)
    row differs from what this pack last wrote    left -> pending (edited)

An item that left the pack is retired (status "retired", never deleted) when its row is
unedited, otherwise pending.

Each kind keeps the contract in kinds.py (P1-T15b2a). Before any create or write the engine
asks the kind why it must wait, in this order: a structural ``conflict`` (blocks accept), a
failed data check (``data-check``, blocks accept; counts stored on the record and recomputed
only when the content or the data's watermark moved, or with ``recheck``), a change only the
admin may take (``privacy``, ``counter-restart``). A kind that does not restore deleted rows
treats a deletion as a local edit: pending ``deleted`` only when the pack changes the item. A pack older than the one applied here is skipped (an image
roll-back must still start); the same version with another sha256 is refused. A newer release
that writes nothing here is still recorded (outcome "reached"), so later starts skip it.

Per-facility kinds (P1-T15b3, design.md §4 and "After the attack" items 11, 13 and 14): the rule
table above runs once per live facility (``_reconcile(release, handler, facility)``), each with
its own records (``PackItem.facility``) and rows; one PackApply row per pack and run, whose
change list names the facility. A soft-deleted facility is skipped (its records stay, hidden
from the API) and caught up when restored. ``PackFacility`` records the release last reconciled
in each facility: the start-up short-circuit needs it for every live facility, and the
new-facility task and the 15-minute backstop (tasks.py) find a facility without it in one
query. Packs are applied in the order their manifest entries' ``after`` give (ties by name).
"""

import logging
from collections import Counter
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from django.db import connection, transaction
from django.db.models import BooleanField, Count, ExpressionWrapper, Max, Q
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context
from ihr_audit import context as audit

from ihr_refdata import kinds, manifest
from ihr_refdata.manifest import PackError
from ihr_refdata.models import PackApply, PackFacility, PackItem

log = logging.getLogger("ihr.refdata")
MAX_ERROR = 300
MAX_REASON = 300
State, Reason, Outcome = PackItem.State, PackItem.Reason, PackApply.Outcome
_CURRENT = {
    "state": State.CURRENT,
    "reason": Reason.NONE,
    "pending_version": None,
    "pending_sha256": "",
    "kept_row_sha256": "",
    "checks": {},
}

__all__ = [
    "PackError",
    "applied_ahead",
    "apply_packs",
    "decide",
    "lagging_facilities",
    "needs_filling",
    "normal_reason",
    "order",
    "pending",
    "prepare",
]
MAX_CHANGES = (
    500  # entries of PackApply.changes; the total is in counts["changes_total"]
)
# Reasons that refuse an accept until fixed (P1-T15b2/b3 set them; keep stays allowed)
MISSING_LINK = "missing-link"  # Q-K68 A: a row the item must link to is not there yet
BLOCKING = frozenset(("conflict", MISSING_LINK, "data-check"))
# Reasons the start-up short-circuit still reconciles for (item 11): the others wait only on
# the admin (edited, unknown-origin, deleted, privacy, counter-restart, a removal)
RERUN = BLOCKING
# Operators the command line may not use: staff decisions in the web app are "user-<pk>"
STAFF_OPERATOR_PREFIX = "user-"


class RefusedError(Exception):
    """This hospital cannot take this pack (the message is safe to show an operator)."""


class NothingWaitingError(RefusedError):
    """No such item, or nothing of it waits for a decision."""


class StaleError(RefusedError):
    """The item, the release or the hospital's row is not what the decision was made on."""


class BlockedError(RefusedError):
    """The item cannot be accepted until a problem is fixed (keep is still allowed).
    ``checks``: a data check's result found at the accept, stored on the item (Q9).
    ``held``: (reason, why, pending sha256) of a conflict or missing link found at the
    accept, stored on a pending item (review E6)."""

    def __init__(self, message, checks=None, held=None):
        super().__init__(message)
        self.checks = checks
        self.held = held


class NotReleasedError(StaleError):
    """The newest release is not in the fleet's record yet (the web app never records one)."""


class ApplyError(Exception):
    """Applying ``release`` to a hospital failed; nothing of that pack was written."""

    def __init__(self, release, cause):
        super().__init__(safe_error(cause))
        self.release = release


@dataclass
class Result:
    pack: str
    version: int
    result: str  # "applied" (something written), "unchanged", "older" (skipped)
    counts: dict = field(default_factory=dict)
    note: str = ""
    # items whose data check hit a cap, now or as stored (the command logs the alert line)
    unchecked: list = field(default_factory=list)


def safe_error(exc):
    """The exception's class and a short message (operators see it; no secrets in it)."""
    text = str(exc).replace("\n", " ")
    return f"{type(exc).__name__}: {text[:MAX_ERROR]}"


def normal_reason(text):
    """A decision's reason on one line, at most MAX_REASON characters (RefusedError if it is
    empty or longer: never cut silently). It names a role or a ticket, never personal details."""
    reason = " ".join((text or "").split())
    if not reason:
        msg = "a reason is required"
        raise RefusedError(msg)
    if len(reason) > MAX_REASON:
        msg = (
            f"the reason is {len(reason)} characters; at most {MAX_REASON} (name a role or "
            "a ticket, not personal details)"
        )
        raise RefusedError(msg)
    return reason


def check_items(release):
    """Every item of ``release`` has the fields its kind needs, of the right types (PackError
    otherwise: a malformed pack is refused for the whole fleet, never hospital by hospital)."""
    handler = kinds.get(release.kind)
    for item in release.items:
        slug = item.get("slug") if isinstance(item, dict) else None
        try:
            handler.check_item(item)
            if slug != slug.lower():  # item 13: keys are matched in lower case
                msg = "the key must be lower case"
                raise ValueError(msg)
        except (KeyError, TypeError, ValueError) as exc:
            msg = f"{release.file}: item {slug!r} is malformed: {safe_error(exc)}"
            raise PackError(msg) from None


def check_pack(versions):
    """Every release of one pack: one kind throughout, and the kind's own checks across them
    (PackError otherwise)."""
    kind = versions[-1].kind
    if any(r.kind != kind for r in versions):
        msg = f"{versions[-1].pack}: every release of a pack has the same kind"
        raise PackError(msg)
    try:
        kinds.get(kind).check_release(versions)
    except (KeyError, TypeError, ValueError) as exc:
        msg = f"{versions[-1].pack}: {safe_error(exc)}"
        raise PackError(msg) from None


_VERIFIED = {}  # (packs folder, manifest sha256) -> releases; repo files, no hospital data


def verified_releases(root=None, *, cached=False):
    """Every release, each file checked against the manifest and each pack's newest items
    checked: {pack: [Release, oldest first]}. ``cached`` (the decision API): kept per process
    for the manifest's sha256, so the files are read and hashed once per manifest."""
    global _VERIFIED  # noqa: PLW0603 (one manifest at a time, replaced in one assignment)
    root = Path(root or manifest.packs_dir())
    key = None
    if cached:
        key = (str(root.resolve()), manifest.manifest_sha256(root))
        found = _VERIFIED.get(key)  # one lookup: gthread workers share this dict
        if found is not None:
            return found
    found = manifest.releases(root)
    for versions in found.values():
        check_pack(versions)
        check_items(versions[-1])
    check_order({pack: versions[-1] for pack, versions in found.items()})
    if key is not None:
        _VERIFIED = {key: found}
    return found


def order(releases):
    """The packs of ``releases`` in the order they are applied: each after the packs its
    ``after`` names (those among ``releases``), otherwise by name (PackError on a cycle)."""
    left = sorted(releases)
    done, out = set(), []
    while left:
        ready = [
            pack
            for pack in left
            if all(a in done or a not in releases for a in releases[pack].after)
        ]
        if not ready:
            msg = f"the packs' after lists make a cycle: {', '.join(left)}"
            raise PackError(msg)
        done.add(ready[0])
        out.append(ready[0])
        left.remove(ready[0])
    return out


def check_order(latest):
    """Fleet-wide checks of the packs' ``after`` (P1-T15b3; PackError): every pack it names
    exists, there is no cycle, and a kind that links (``LINKS``) is applied after a pack of
    each kind it links to, so its targets are written first."""
    for pack in sorted(latest):
        release = latest[pack]
        for name in release.after:
            if name not in latest:
                msg = f"{pack}: after names {name!r}, which is not a pack of this build"
                raise PackError(msg)
        handler = kinds.get(release.kind)
        for link, target in sorted(handler.LINKS.items()):
            try:
                linked = kinds.get(target)
            except (
                KeyError
            ):  # review E7: refused for the fleet like any other pack fault
                msg = f"{pack}: its {link} links to {target!r}, which is not a kind of this build"
                raise PackError(msg) from None
            if handler.SCOPE != "facility" or linked.SCOPE != "facility":
                msg = f"{pack}: only per-facility kinds link ({link} -> {target})"
                raise PackError(msg)
            if not any(latest[a].kind == target for a in release.after):
                msg = (
                    f"{pack}: its {link} links to {target} items, but no pack of that "
                    "kind is in its after list"
                )
                raise PackError(msg)
    order(latest)


def recorded(release):
    """True when the fleet's record holds ``release`` (with its sha256: prepare checks that)."""
    from ihr_refdata_registry.models import PackRelease

    with schema_context(get_public_schema_name()):
        return PackRelease.objects.filter(
            pack=release.pack, version=release.version, sha256=release.sha256
        ).exists()


def prepare(operator, *, root=None, record=True, cached=False):
    """Check every pack file against the manifest, every item of each pack's newest release
    (the only one applied), and each release against the fleet's record of it
    (``PackRelease``, written the first time a release is seen). Returns the newest release of
    each pack. Any fault here is the same for every hospital: PackError. ``record=False`` only
    reads (the web app may only read ``PackRelease``); ``cached``: see verified_releases."""
    from ihr_refdata_registry.models import PackRelease

    found = verified_releases(root, cached=cached)
    with schema_context(get_public_schema_name()):
        for versions in found.values():
            for release in versions:
                if record:
                    known, _ = PackRelease.objects.get_or_create(
                        pack=release.pack,
                        version=release.version,
                        defaults={
                            "sha256": release.sha256,
                            "care_commit": release.care_commit,
                            "item_count": release.item_count,
                            "loaded_by": operator[:150],
                        },
                    )
                else:
                    known = PackRelease.objects.filter(
                        pack=release.pack, version=release.version
                    ).first()
                if known is not None and known.sha256 != release.sha256:
                    msg = (
                        f"{release.pack} v{release.version} was released before with "
                        f"another sha256 ({known.sha256[:12]}): versions are immutable, "
                        f"build v{versions[-1].version + 1} instead"
                    )
                    raise PackError(msg)
    return {pack: versions[-1] for pack, versions in found.items()}


def apply_packs(
    hospital,
    *,
    operator,
    releases=None,
    root=None,
    dry_run=False,
    recheck=False,
    facilities=None,
):
    """Apply the newest version of every pack to ``hospital`` (lock-free; see above), in
    ``order``. Returns a Result per pack; a pack that fails raises ApplyError (its writes
    rolled back) and the later packs are not applied. ``dry_run`` works out the same results
    and writes nothing. ``recheck`` (``ihr_pack_pending --recheck``): every data check is run
    again, whatever is stored. ``facilities`` (the new-facility task and the 15-minute
    backstop, item 14): only per-facility packs, and only in those facilities; a hospital-wide
    pack is never applied then."""
    if releases is None:
        releases = prepare(operator, root=root, record=not dry_run)
    results = []
    with schema_context(hospital.schema_name):
        for pack in order(releases):
            release = releases[pack]
            if facilities is not None and kinds.get(release.kind).SCOPE != "facility":
                continue
            try:
                results.append(
                    _apply_release(
                        release,
                        operator,
                        dry_run=dry_run,
                        recheck=recheck,
                        facilities=facilities,
                    )
                )
            except Exception as exc:
                raise ApplyError(release, exc) from exc
    return results


def _last_applied(pack):
    """The newest release this hospital took in full: applied, or reached with nothing to
    write (C5)."""
    return (
        PackApply.objects.filter(
            pack=pack, outcome__in=[Outcome.APPLIED, Outcome.REACHED]
        )
        .order_by("-version", "-id")
        .first()
    )


def applied_ahead(releases):
    """The packs of ``releases`` this hospital took a newer version of: after an image
    roll-back the apply skips them as older, so the task and the backstop leave them alone
    until the roll-forward (review T3). One query."""
    newest = dict(
        PackApply.objects.filter(
            pack__in=list(releases), outcome__in=[Outcome.APPLIED, Outcome.REACHED]
        )
        .order_by()
        .values("pack")
        .annotate(newest=Max("version"))
        .values_list("pack", "newest")
    )
    return {
        pack
        for pack, release in releases.items()
        if newest.get(pack, 0) > release.version
    }


def live_facilities():
    """The hospital's facilities that are not soft-deleted (Q-K67 A), oldest first."""
    from care.facility.models import Facility

    return Facility._base_manager.filter(deleted=False).order_by("id")  # noqa: SLF001


def lagging_facilities(releases, *, missing_links=False):
    """Live facilities that lack the current release of one of these per-facility packs (no
    PackFacility for its sha256, nor for a newer version: after an image roll-back the pack
    is skipped as older, review T3): one query (item 14). ``missing_links`` (the backstop):
    also those with an item held for a missing link, which ``_settled`` reconciles only once
    a target is there (item 11); each then carries ``ihr_behind`` (False: only the held
    item), for ``needs_filling``."""
    mine = [r for r in releases.values() if kinds.get(r.kind).SCOPE == "facility"]
    if not mine:
        return []
    current = Q()
    for release in mine:
        current |= Q(pack=release.pack, sha256=release.sha256)
        current |= Q(pack=release.pack, version__gt=release.version)
    complete = (
        PackFacility.objects.filter(current)
        .values("facility")
        .annotate(n=Count("id"))
        .filter(n=len(mine))
        .values("facility")
    )
    behind = ~Q(id__in=complete)
    found = live_facilities()
    if missing_links:
        found = found.annotate(
            ihr_behind=ExpressionWrapper(behind, output_field=BooleanField())
        )
        behind |= Q(
            id__in=PackItem.objects.filter(
                pack__in=[r.pack for r in mine],
                state=State.PENDING,
                reason=MISSING_LINK,
            ).values("facility")
        )
    return list(found.filter(behind))


def needs_filling(releases, facility):
    """True when ``facility`` (one ``lagging_facilities`` found) has work now: it lacks a
    current release, or a pending record of it can clear without the admin (an item held
    for a missing link only once all its targets are live: ``_needs_reconcile``). The
    backstop asks it before it takes the hospital's lock (review E2)."""
    if getattr(facility, "ihr_behind", True):
        return True
    return any(
        _needs_reconcile(release, kinds.get(release.kind), facility)
        for release in releases.values()
        if kinds.get(release.kind).SCOPE == "facility"
    )


def _settled(release, handler, facility):
    """True when ``facility`` has nothing to do for ``release``, the release applied here: it
    was reconciled with it, no pending record can clear without the admin, and every row
    expected is there (the start-up short-circuit, per facility)."""
    return (
        PackFacility.objects.filter(
            pack=release.pack, facility=facility, sha256=release.sha256
        ).exists()
        and not _needs_reconcile(release, handler, facility)
        and handler.at(facility).all_present(
            _expected_rows(release, handler, facility),
            targets=_targets(release.pack, facility=facility),
        )
    )


def _apply_release(release, operator, *, dry_run, recheck=False, facilities=None):
    handler = kinds.get(release.kind)
    with (
        audit.extend(pack=release.pack, version=release.version, sha256=release.sha256),
        transaction.atomic(),
    ):
        last = _last_applied(release.pack)
        if last is not None and release.version < last.version:
            note = (
                f"v{release.version} is older than the v{last.version} applied here: "
                "skipped (versions only go forward)"
            )
            return Result(release.pack, release.version, "older", note=note)
        if (
            last is not None
            and release.version == last.version
            and release.sha256 != last.sha256
        ):
            msg = (
                f"{release.pack} v{release.version} differs from the v{last.version} "
                f"applied here (sha256 {last.sha256[:12]}): versions are immutable"
            )
            raise RefusedError(msg)
        same = last is not None and last.sha256 == release.sha256 and not recheck
        if handler.SCOPE == "facility":
            places = live_facilities()
            if facilities is not None:  # the task and the backstop: only these, if live
                places = places.filter(pk__in=[f.pk for f in facilities])
            places = list(places)
            if same:
                places = [f for f in places if not _settled(release, handler, f)]
                if not places:
                    return Result(release.pack, release.version, "unchanged")
        else:
            if (
                same
                and not _needs_reconcile(release, handler)
                and handler.all_present(
                    _expected_rows(release, handler), targets=_targets(release.pack)
                )
            ):
                return Result(release.pack, release.version, "unchanged")
            places = [None]
        counts, writes, changes, unchecked = Counter(), 0, [], []
        for place in places:
            done = _reconcile(release, handler, place, dry_run=dry_run, recheck=recheck)
            counts.update(done.counts)
            writes += done.writes
            changes += done.changes
            unchecked += done.unchecked
            if done.written and handler.RECHECK_CONFLICTS and not dry_run:
                transaction.on_commit(
                    partial(
                        recheck_conflicts,
                        connection.schema_name,
                        release,
                        tuple(done.written),
                        place.pk if place is not None else None,
                    )
                )
        counts = dict(sorted(counts.items()))
        # A newer release with nothing to write here (e.g. only CARE's spelling of compose
        # changed) is still recorded once, so the sha256 short-circuit fires from then on (C5).
        # Not by the task or the backstop: they reach only some facilities
        reached = (last is None or last.sha256 != release.sha256) and facilities is None
        if (writes or reached) and not dry_run:
            # The visible change list (Q-K61): only with an applied row, at most MAX_CHANGES
            # entries and their total
            listed = changes[:MAX_CHANGES] if writes else []
            PackApply.objects.create(
                pack=release.pack,
                version=release.version,
                sha256=release.sha256,
                care_commit=release.care_commit,
                outcome=Outcome.APPLIED if writes else Outcome.REACHED,
                actor=operator[:150],
                counts={**counts, "changes_total": len(changes)} if writes else counts,
                changes=listed,
            )
    result = "applied" if writes else "unchanged"
    return Result(
        release.pack, release.version, result, counts=counts, unchecked=unchecked
    )


def _records(pack, facility=None):
    """This pack's records, hospital-wide (``facility`` None) or of one facility."""
    return PackItem.objects.filter(pack=pack, facility=facility)


def _needs_reconcile(release, handler, facility=None):
    """True when a pending record must be reconciled again although the release is the one
    applied here (item 11): a reason that can clear without the admin (RERUN), or a record
    that does not wait for exactly this release's content (an older release, or tampered
    with: put right, never trusted). Items held for a missing link are reconciled only when
    the kind finds a target of theirs there now (one query, ``linkable``)."""
    items = release.by_item()
    held = []
    for item, reason, version, sha256 in (
        _records(release.pack, facility)
        .filter(state=State.PENDING)
        .values_list("item", "reason", "pending_version", "pending_sha256")
    ):
        expected = handler.item_hash(items[item]) if item in items else ""
        if version != release.version or sha256 != expected:
            return True
        if reason == MISSING_LINK and item in items:
            held.append(items[item])
        elif reason in RERUN:
            return True
    return bool(held) and handler.at(facility).linkable(held)


def _expected_rows(release, handler, facility=None):
    """The items that should have a row now: not one waiting on the admin, or kept, for
    exactly this content (a create held for privacy or a counter-restart has none, and must
    not reconcile at every start: item 11, review Q6)."""
    waiting = set(
        _records(release.pack, facility)
        .filter(state__in=[State.PENDING, State.KEPT])
        .values_list("item", "pending_sha256")
    )
    return [
        item["slug"]
        for item in release.items
        if (item["slug"], handler.item_hash(item)) not in waiting
    ]


def _targets(pack, records=None, facility=None):
    """{item: target} of this pack's records that name their row (forms: item 2)."""
    if records is None:
        records = _records(pack, facility).exclude(target="")
        return dict(records.values_list("item", "target"))
    return {r.item: r.target for r in records if r.target}


def checks_result(handler, row, item, names, content_hash, mark, at):
    """A data check run now, as stored on the record (counts only, never a value)."""
    found = handler.run_data_checks(row, item, names)
    return {
        "names": list(names),
        "counts": found["counts"],
        "patients": found["patients"],
        "complete": found["complete"],
        "sha256": content_hash,
        "watermark": mark,
        "at": at.isoformat(),
    }


def reusable(stored, content_hash, mark, names):
    """True when ``stored`` checked this content against these data (item 8)."""
    stored = stored or {}
    return (
        stored.get("sha256") == content_hash
        and stored.get("watermark") == mark
        and stored.get("names") == list(names)
    )


def recheck_conflicts(schema, release, keys, facility_id=None):
    """After commit (item 9): writers that do not take the kind's lock (CARE's own) may have
    made a duplicate meanwhile. An item that now has a conflict is pending ``conflict``."""
    items = release.by_item()
    with schema_context(schema), transaction.atomic():
        facility = None
        if facility_id is not None:
            facility = live_facilities().filter(pk=facility_id).first()
            if facility is None:
                return
        handler = kinds.get(release.kind).at(facility)
        found = handler.rows(keys, targets=_targets(release.pack, facility=facility))
        for key in keys:
            item = items.get(key)
            why = handler.conflict(key, found.get(key), item) if item else ""
            if not why:
                continue
            record = (
                PackItem.objects.select_for_update()
                .filter(
                    pack=release.pack, kind=release.kind, item=key, facility=facility
                )
                .first()
            )
            if record is None:
                continue
            record.state, record.reason = State.PENDING, Reason.CONFLICT
            record.pending_version = release.version
            record.pending_sha256 = handler.item_hash(item)
            record.checks = {"conflict": why}
            record.changed_at = timezone.now()
            record.save()
            log.warning(
                "ihr-packs: %s:%s has a conflict found after commit: %s",
                release.pack,
                key,
                why,
            )


@dataclass
class _Reconciled:
    counts: dict
    writes: int
    changes: list
    written: list  # keys created or written (looked at again after commit)
    unchecked: list  # keys whose data check hit a cap (now, or as stored)


def _reconcile(release, handler, facility=None, *, dry_run, recheck=False):  # noqa: PLR0912, PLR0915 (the rule table)
    """The rule table for one release, hospital-wide (``facility`` None) or in one facility
    of a per-facility kind (its own records and rows; links resolve inside it)."""
    now = timezone.now()
    handler = handler.at(facility)
    where = str(facility.external_id) if facility is not None else None
    items = release.by_item()
    records = {
        r.item: r
        for r in PackItem.objects.filter(
            pack=release.pack, kind=release.kind, facility=facility
        )
    }
    targets = _targets(release.pack, records.values())
    rows = handler.rows(set(items) | set(records), targets=targets)
    # Rows this release takes out of the pack (a counter-restart looks at them, item 10)
    leaving = [
        rows[slug]
        for slug, record in records.items()
        if slug not in items
        and record.state != State.RETIRED
        and slug in rows
        and not rows[slug].deleted
    ]
    counts = Counter()
    writes = 0
    unchecked = []
    written = []
    changes = []  # [{item, facility, change}]: what this apply did, item by item

    def listed(slug, change, **extra):
        changes.append({"item": slug, "facility": where, "change": change, **extra})

    def note(record, slug, **values):
        """Write the record's new values; True when anything changed."""
        nonlocal writes
        if record is None:
            writes += 1
            if not dry_run:
                PackItem.objects.create(
                    pack=release.pack,
                    kind=release.kind,
                    facility=facility,
                    item=slug,
                    changed_at=now,
                    **values,
                )
            return True
        changed = [k for k, v in values.items() if getattr(record, k) != v]
        if changed:
            writes += 1
            if not dry_run:
                for k in changed:
                    setattr(record, k, values[k])
                record.changed_at = now
                record.save(update_fields=[*changed, "changed_at"])
        return bool(changed)

    def settled(record, slug, values):
        """A record-only write to current: "resolved" when a change waited or was kept (the
        pack withdrew it, or the hospital's row now holds it), else "adopted"."""
        was = record.state if record is not None else None
        if note(record, slug, **values):
            listed(
                slug, "resolved" if was in (State.PENDING, State.KEPT) else "adopted"
            )

    def hold(record, slug, reason, content_hash, checks=None):
        counts["pending"] += 1
        if note(
            record,
            slug,
            state=State.PENDING,
            reason=reason,
            pending_version=release.version,
            pending_sha256=content_hash,
            kept_row_sha256="",
            checks=checks or {},
        ):
            listed(slug, "pending")

    def data_checked(record, slug, row, item, content_hash, names):
        """The data checks' result: the stored one while neither the content nor the data
        moved (item 8), else computed now. One not fully checked counts every time, stored
        or not, so the alert line is written until a recheck finishes it (Q3)."""
        mark = handler.watermark(row)
        stored = record.checks if record is not None else None
        if not recheck and reusable(stored, content_hash, mark, names):
            found = stored
        else:
            found = checks_result(handler, row, item, names, content_hash, mark, now)
        if not found["complete"]:
            unchecked.append(slug)
            log.warning(
                "ihr-packs: %s:%s data check not fully checked (a cap was reached)",
                release.pack,
                slug,
            )
        return found

    def moved(slug, row):
        """True when the row changed since it was read (E3): read again under its lock, so
        a hospital edit committed meanwhile is never overwritten."""
        fresh = handler.rows(
            [slug], lock=not dry_run, targets={slug: targets.get(slug, "")}
        ).get(slug)
        if row is None or fresh is None:
            return fresh is not row
        return handler.decision_hash(fresh) != handler.decision_hash(row) or getattr(
            fresh, "ihr_live", 1
        ) != getattr(row, "ihr_live", 1)

    def gate(slug, record, row, item, content_hash):
        """(reason, checks): why writing ``item`` must wait ("" to write now). A data
        check that passed is kept with any hold, so it is not run again (E6)."""
        handler.lock(slug)
        if moved(slug, row):  # the next apply works from what is there now
            edited = record is not None and record.applied_sha256
            return (Reason.EDITED if edited else Reason.UNKNOWN_ORIGIN), {}
        if why := handler.conflict(slug, row, item):
            return Reason.CONFLICT, {"conflict": why}
        if why := handler.missing_link(slug, row, item):
            return MISSING_LINK, {"missing-link": why}
        found = {}
        if names := handler.data_checks(row, item):
            found = data_checked(record, slug, row, item, content_hash, names)
            if found["patients"] or not found["complete"]:
                return Reason.DATA_CHECK, found
        reason, checks = handler.admin_reason(row, item, leaving)
        return reason, {**found, **checks}

    def waiting(record, slug, content_hash, row=None, item=None):
        reason = (
            Reason.EDITED if record and record.applied_sha256 else Reason.UNKNOWN_ORIGIN
        )
        checks = {}
        if item is not None:
            held, checks = gate(slug, record, row, item, content_hash)
            reason = held or reason
        hold(record, slug, reason, content_hash, checks)

    def deleted_reason(row, item):
        """A deletion waits as "deleted", or as what only the admin may take (Q7)."""
        return handler.admin_reason(row, item, leaving)[0] or Reason.DELETED

    def wrote(slug, change, holder=None, done=None):
        """A write of ``slug``; ``holder``: the row that now holds the item (the kind's
        record_target and change list entry come from it, item 2)."""
        nonlocal writes
        counts[change] += 1
        writes += 1
        written.append(slug)
        extra = handler.change(holder) if holder is not None else {}
        if done is not None and holder is not None:
            done["target"] = handler.record_target(items[slug], holder)
        listed(slug, change, **extra)

    for slug, item in items.items():
        new = handler.item_hash(item)
        row, record = rows.get(slug), records.get(slug)
        done = dict(
            _CURRENT,
            applied_version=release.version,
            applied_sha256=new,
            target=handler.record_target(item, row),
        )
        if row is None and record is not None and record.pending_sha256 == new:
            # A create the admin kept, or one held for a counter-restart, stays so for this
            # content (E1: the old series it replaces is retired by the apply that held it,
            # so asking again would find nothing leaving and create it unasked)
            if record.state == State.KEPT:
                counts["kept"] += 1
                if note(record, slug, pending_version=release.version):
                    listed(slug, "kept")
                continue
            if record.reason == Reason.COUNTER_RESTART:
                hold(record, slug, Reason.COUNTER_RESTART, new, record.checks)
                continue
        if row is None:
            held, checks = gate(slug, record, row, item, new)
            if held:
                hold(record, slug, held, new, checks)
                continue
            holder = None if dry_run else handler.create(item)
            wrote(slug, "created", holder, done)
            note(record, slug, **done)
        elif row.deleted and handler.RESTORE_DELETED:
            # CARE binds these slugs in code: a deleted one breaks every write bound to it
            held, checks = gate(slug, record, row, item, new)
            if held:
                hold(record, slug, held, new, checks)
                continue
            holder = None if dry_run else handler.write(row, item, restore=True)
            wrote(slug, "restored", holder, done)
            note(record, slug, **done)
        elif row.deleted:
            # The hospital deleted it (item 11): its own choice, like an edit. Waits only
            # when the pack changes the item; accept restores it with the pack's content.
            if record is not None and record.state == State.KEPT:
                if record.pending_sha256 == new:
                    counts["kept"] += 1
                    if note(record, slug, pending_version=release.version):
                        listed(slug, "kept")
                else:
                    hold(record, slug, deleted_reason(row, item), new)
            elif record is not None and record.applied_sha256 == new:
                counts["local-edit"] += 1
                if record.state != State.CURRENT:
                    settled(record, slug, _CURRENT)
            else:
                hold(record, slug, deleted_reason(row, item), new)
        elif (
            (record is None or record.state != State.KEPT)
            and (
                (record is not None and record.reason == Reason.CONFLICT)
                or getattr(row, "ihr_live", 1) > 1
            )
            and (why := handler.conflict(slug, row, item))
        ):
            # Two live rows, or a conflict found before: asked again whatever the row holds
            # (E2), so a newer duplicate never reads as a local edit or adopted content
            hold(record, slug, Reason.CONFLICT, new, {"conflict": why})
        elif (have := handler.row_hash(row)) == new:
            if not handler.usable(row):
                # Same meaning, but a list CARE's lookup iterates over is missing (CARE's API
                # can store compose without "exclude"): the pack's canonical compose (C2)
                holder = None if dry_run else handler.write(row, item)
                wrote(slug, "repaired", holder, done)
                note(record, slug, **done)
            elif record is not None and (
                record.state == State.CURRENT and record.applied_sha256 == new
            ):
                counts["unchanged"] += 1
                continue
            else:
                # the row already holds it: nothing written to it
                counts["adopted"] += 1
                settled(record, slug, done)
        elif (
            record is not None
            and record.applied_sha256 == new
            and handler.stale_hash(row) == new
        ):
            # What the pack wrote, but a link now names a deleted target, or one a newer live
            # row of its key replaced (review T1, item 13): the target's deletion is the
            # hospital's, the link is not its edit. Re-pointed to the key's live row, or held
            # as missing-link until there is one (Q-K68 A)
            held, checks = gate(slug, record, row, item, new)
            if held:
                hold(record, slug, held, new, checks)
                continue
            holder = None if dry_run else handler.write(row, item)
            wrote(slug, "updated", holder, done)
            note(record, slug, **done)
        elif record is not None and record.applied_sha256 == new:
            # The hospital edited the row and the pack has nothing new for it since it last
            # applied it: the edit is the hospital's own, never offered for revert (C4). A
            # change that waited (or was kept) and that the pack has since withdrawn goes too.
            counts["local-edit"] += 1
            if record.state != State.CURRENT:
                settled(record, slug, _CURRENT)
            if not handler.usable(row):
                # their edit left out a list CARE's lookup iterates over (CARE's own API can
                # store compose without "exclude"): add it empty, keeping their content
                if not dry_run:
                    handler.make_usable(row)
                counts["repaired"] += 1
                writes += 1
                listed(slug, "repaired")
        elif record is not None and record.state == State.KEPT:
            if record.pending_sha256 == new:  # the very content the hospital declined
                counts["kept"] += 1
                # follows the newest release, still kept, so accept stays possible (C1)
                if note(record, slug, pending_version=release.version):
                    listed(slug, "kept")
            else:
                waiting(record, slug, new, row, item)
        elif record is not None and record.applied_sha256 == have:
            held, checks = gate(slug, record, row, item, new)
            if held:
                hold(record, slug, held, new, checks)
                continue
            holder = None if dry_run else handler.write(row, item)
            wrote(slug, "updated", holder, done)
            note(record, slug, **done)
        else:
            waiting(record, slug, new, row, item)

    for slug, record in records.items():
        if slug in items or record.state == State.RETIRED:
            continue
        row = rows.get(slug)
        retired = dict(_CURRENT, state=State.RETIRED)
        if row is None or row.deleted:
            counts["retired"] += 1
            if note(record, slug, **retired):
                listed(slug, "retired")
        elif handler.row_hash(row) == record.applied_sha256 or (
            record.applied_sha256 and handler.stale_hash(row) == record.applied_sha256
        ):
            # unedited (a link to a target the hospital deleted is not an edit, review T1)
            retiring = not handler.is_retired(row)
            if retiring:
                writes += 1
                if not dry_run:
                    handler.retire(row)
            counts["retired"] += 1
            # The retired row's hash: a later re-add finds it unedited (C9)
            if (
                note(
                    record,
                    slug,
                    **retired,
                    applied_version=release.version,
                    applied_sha256=handler.retired_hash(row),
                )
                or retiring
            ):
                listed(slug, "retired")
        elif record.state == State.KEPT and record.pending_sha256 == "":
            counts["kept"] += 1  # the hospital kept a set the pack dropped
            if note(
                record, slug, pending_version=release.version
            ):  # accept stays possible
                listed(slug, "kept")
        else:
            waiting(record, slug, "")  # accepting it retires the row
    if facility is not None and not dry_run:
        _mark(release, facility, now)
    return _Reconciled(
        dict(sorted(counts.items())), writes, changes, written, unchecked
    )


def _mark(release, facility, now):
    """``facility`` was reconciled with ``release`` (written only when that changes)."""
    marker = PackFacility.objects.filter(pack=release.pack, facility=facility).first()
    if marker is None:
        PackFacility.objects.create(
            pack=release.pack,
            facility=facility,
            version=release.version,
            sha256=release.sha256,
            reconciled_at=now,
        )
    elif (marker.version, marker.sha256) != (release.version, release.sha256):
        marker.version, marker.sha256, marker.reconciled_at = (
            release.version,
            release.sha256,
            now,
        )
        marker.save(update_fields=["version", "sha256", "reconciled_at"])


# --- what the hospital admin decides (CLI now; API and screen in P1-T15b and P1-T24) -------


def pending(hospital):
    with schema_context(hospital.schema_name):
        return list(
            PackItem.objects.filter(state__in=[State.PENDING, State.KEPT])
            .select_related("facility")
            .order_by("pack", "item", "facility_id")
        )


def row_sha256(handler, row):
    """The hospital row as the admin decides on it ("" for none): its pack fields and whether
    it is soft-deleted, so a delete between the look and the decision is stale too."""
    return handler.decision_hash(row) if row is not None else ""


def replayed(record, *, accept, version, sha256):
    """True when ``record`` already stands where this decision leads, for this version and
    content, and the newest decision on it is that one (the decision API's idempotent replay:
    answered unchanged, nothing written). An item an apply moved there, never decided, is not
    a replay."""
    if record is None:
        return False
    if not accept:
        there = (
            record.state == State.KEPT
            and record.pending_version == version
            and record.pending_sha256 == sha256
        )
    elif sha256 == "":  # accepting a removal retires the row
        there = record.state == State.RETIRED and record.applied_version == version
    else:
        there = (
            record.state == State.CURRENT
            and record.applied_version == version
            and record.applied_sha256 == sha256
        )
    if not there:
        return False
    decisions = PackApply.objects.filter(
        pack=record.pack,
        outcome__in=[Outcome.ACCEPTED, Outcome.KEPT],
        counts__item=record.item,
    )
    if record.facility_id is not None:
        decisions = decisions.filter(counts__facility=str(record.facility.external_id))
    last = decisions.order_by("-id").values_list("outcome", "version").first()
    return last == (Outcome.ACCEPTED if accept else Outcome.KEPT, version)


def decide(  # noqa: PLR0912, PLR0915 (one decision, every check of it)
    hospital,
    pack,
    slug,
    *,
    accept,
    operator,
    reason,
    root=None,
    expect=None,
    facility=None,
    record=True,
    releases=None,
):
    """Accept a pending item (the pack's content, rebuilt from the verified pack file, is
    written; a soft-deleted row is restored with it) or keep the hospital's own row. Only
    the newest release can be accepted or kept. The caller holds the hospital's lock and has
    put the reason in the audit context (``ihr_pack_pending``'s job, the API's): it is not
    added here, so the trail never holds more of it than the normalised, length-checked form
    (S7).

    ``expect`` (the decision API): ``(version, sha256, row_sha256)`` the admin was shown; a
    different pending version or content, or a hospital row edited since, is StaleError and
    nothing is written. A record already where the decision leads for that version is a
    replay: returned with ``replayed = True``, nothing written. ``record=False``: the release
    must already be in the fleet's record (NotReleasedError), never recorded here.
    ``facility``: the facility of a per-facility item (P1-T15b3; None for a hospital-wide
    one); a soft-deleted facility's items wait hidden and are never decided.
    ``releases``: what ``prepare`` returned (the API prepares once, before the lock)."""
    reason = normal_reason(reason)
    if facility is not None and facility.deleted:
        msg = f"{pack}:{slug} has nothing waiting to decide in that facility"
        raise NothingWaitingError(msg)
    if releases is None:
        releases = prepare(operator, root=root, record=record)
    release = releases.get(pack)
    if release is None:
        msg = f"no pack named {pack!r}"
        raise NothingWaitingError(msg)
    if not record and not recorded(release):
        msg = (
            "this update is not released here yet: try again after the next pack apply"
        )
        raise NotReleasedError(msg)
    handler = kinds.get(release.kind)
    if (handler.SCOPE == "facility") != (facility is not None):
        msg = (
            f"{pack}:{slug}: name the facility (a per-facility pack)"
            if facility is None
            else f"{pack} is hospital-wide: it has no facility's items"
        )
        raise NothingWaitingError(msg)
    where = {"facility": str(facility.external_id)} if facility is not None else {}
    action = "accept" if accept else "keep"
    try:
        with (
            schema_context(hospital.schema_name),
            audit.extend(
                pack=release.pack,
                version=release.version,
                sha256=release.sha256,
                action=action,
                item=slug,
                **where,
            ),
            transaction.atomic(),
        ):
            if facility is not None:
                # read again and locked (review E3): the caller's copy was read before the
                # hospital's lock, and a facility deleted since is never decided in
                from care.facility.models import Facility

                facility = (
                    Facility._base_manager.select_for_update(no_key=True)  # noqa: SLF001
                    .filter(pk=facility.pk, deleted=False)
                    .first()
                )
                if facility is None:
                    msg = (
                        f"{pack}:{slug} has nothing waiting to decide in that facility"
                    )
                    raise NothingWaitingError(msg)
            handler = kinds.get(release.kind).at(facility)
            found = (
                PackItem.objects.select_for_update()
                .filter(pack=pack, item=slug, facility=facility)
                .first()
            )
            if expect is not None and replayed(
                found, accept=accept, version=expect[0], sha256=expect[1]
            ):
                found.replayed = True
                return found
            allowed = (State.PENDING, State.KEPT) if accept else (State.PENDING,)
            if found is None or found.state not in allowed:
                msg = f"{pack}:{slug} has nothing waiting to {action}"
                raise NothingWaitingError(msg)
            if found.pending_version != release.version:
                msg = (
                    f"{pack}:{slug} waits for v{found.pending_version}, but the newest "
                    f"release is v{release.version}: run ihr_pack_apply first"
                )
                raise StaleError(msg)
            item = release.by_item().get(slug)
            expected = handler.item_hash(item) if item is not None else ""
            if expected != found.pending_sha256:
                msg = f"{pack}:{slug}: the waiting content is not the release's"
                raise StaleError(msg)
            if expect is not None and (expect[0], expect[1]) != (
                found.pending_version,
                found.pending_sha256,
            ):
                msg = f"{pack}:{slug} changed since it was shown: look at it again"
                raise StaleError(msg)
            if accept and found.state == State.PENDING and found.reason in BLOCKING:
                # a kept item is asked afresh below (E5): its stored reason may be fixed since
                msg = f"{pack}:{slug} cannot be accepted until its {found.reason} is fixed"
                raise BlockedError(msg)
            handler.lock(slug)
            row = handler.rows([slug], lock=True, targets={slug: found.target}).get(
                slug
            )
            if expect is not None and row_sha256(handler, row) != expect[2]:
                msg = (
                    f"the hospital's {slug} was changed since it was shown: look again"
                )
                raise StaleError(msg)
            if accept and item is not None:
                _accept_checks(handler, found, pack, slug, row, item)
            now = timezone.now()
            if not accept:
                found.state = State.KEPT
                found.kept_row_sha256 = handler.row_hash(row) if row is not None else ""
            elif item is None:  # the pack dropped it: retire the row
                if row is not None and not row.deleted:
                    handler.retire(row)
                    found.applied_sha256 = handler.retired_hash(row)
                found.state = State.RETIRED
                found.applied_version = release.version
            else:
                if row is None:
                    holder = handler.create(item)
                else:
                    holder = handler.write(row, item, restore=True)
                found.state = State.CURRENT
                found.applied_version = release.version
                found.applied_sha256 = expected
                # in the same transaction as the write (item 2)
                found.target = handler.record_target(item, holder)
                if handler.RECHECK_CONFLICTS:
                    transaction.on_commit(
                        partial(
                            recheck_conflicts,
                            hospital.schema_name,
                            release,
                            (slug,),
                            facility.pk if facility is not None else None,
                        )
                    )
            if found.state != State.KEPT:
                found.pending_version, found.pending_sha256 = None, ""
                found.kept_row_sha256 = ""
                found.checks = {}
            found.reason = Reason.NONE if accept else found.reason
            found.changed_at = now
            found.save()
            PackApply.objects.create(
                pack=release.pack,
                version=release.version,
                sha256=release.sha256,
                care_commit=release.care_commit,
                outcome=Outcome.ACCEPTED if accept else Outcome.KEPT,
                actor=operator[:150],
                counts={"item": slug, **where, "reason": reason},
            )
    except BlockedError as exc:
        if exc.checks is not None:
            _store_blocked(hospital, pack, slug, exc.checks, facility)
        if exc.held is not None:
            _store_held(hospital, pack, slug, *exc.held, facility)
        raise
    found.replayed = False
    return found


def _accept_checks(handler, record, pack, slug, row, item):
    """An accept is refused while a conflict or a failed data check stands. The conflict is
    asked now; a stored data check is reused only while it is complete and neither the
    content nor the data moved (E4: the row stays locked while a check runs), else run now.
    A failed one is stored on the item by ``decide`` (Q9)."""
    if why := handler.conflict(slug, row, item):
        msg = f"{pack}:{slug} cannot be accepted until its conflict is fixed: {why}"
        raise BlockedError(msg, held=(Reason.CONFLICT, why, record.pending_sha256))
    if why := handler.missing_link(slug, row, item):
        msg = f"{pack}:{slug} cannot be accepted until its missing link is there: {why}"
        raise BlockedError(msg, held=(MISSING_LINK, why, record.pending_sha256))
    if names := handler.data_checks(row, item):
        mark = handler.watermark(row)
        found = record.checks or {}
        if not (
            found.get("complete")
            and reusable(found, record.pending_sha256, mark, names)
        ):
            found = checks_result(
                handler, row, item, names, record.pending_sha256, mark, timezone.now()
            )
        if found["patients"] or not found["complete"]:
            what = (
                f"{found['patients']} patients' edits would fail until fixed"
                if found["complete"]
                else "the hospital's data could not be fully checked"
            )
            msg = f"{pack}:{slug} cannot be accepted: {what}"
            raise BlockedError(msg, checks=found)


def _store_held(hospital, pack, slug, reason, why, sha256, facility=None):
    """A conflict or missing link that refused an accept is kept on a pending item as its
    reason (review E6): the list shows it as blocked, and the next apply or the backstop
    asks again (RERUN; a missing link once its targets are live). A kept item is asked
    afresh at its accept (E5), so it is left as it is."""
    with schema_context(hospital.schema_name), transaction.atomic():
        PackItem.objects.filter(
            pack=pack,
            item=slug,
            facility=facility,
            state=State.PENDING,
            pending_sha256=sha256,
        ).update(reason=reason, checks={reason: why}, changed_at=timezone.now())


def _store_blocked(hospital, pack, slug, checks, facility=None):
    """A data check that refused an accept is kept on the item as its reason (Q9): the list
    shows it, and one not fully checked is alerted on by the next apply (Q3)."""
    with schema_context(hospital.schema_name), transaction.atomic():
        PackItem.objects.filter(
            pack=pack,
            item=slug,
            facility=facility,
            state__in=[State.PENDING, State.KEPT],
            pending_sha256=checks["sha256"],
        ).update(reason=Reason.DATA_CHECK, checks=checks, changed_at=timezone.now())
    if not checks["complete"]:
        log.warning(
            "ihr-packs: hospital=%s pack=%s item=%s data check not fully checked at an "
            "accept: ihr_pack_pending --slug %s --recheck",
            hospital.slug,
            pack,
            slug,
            hospital.slug,
        )
