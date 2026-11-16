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
unedited, otherwise pending. A pack older than the one applied here is skipped (an image
roll-back must still start); the same version with another sha256 is refused. A newer release
that writes nothing here is still recorded (outcome "reached"), so later starts skip it.
"""

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from django.db import transaction
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context
from ihr_audit import context as audit

from ihr_refdata import manifest, valuesets
from ihr_refdata.manifest import PackError
from ihr_refdata.models import PackApply, PackItem

log = logging.getLogger("ihr.refdata")
HANDLERS = {valuesets.KIND: valuesets}
MAX_ERROR = 300
MAX_REASON = 300
State, Reason, Outcome = PackItem.State, PackItem.Reason, PackApply.Outcome
_CURRENT = {
    "state": State.CURRENT,
    "reason": Reason.NONE,
    "pending_version": None,
    "pending_sha256": "",
    "kept_row_sha256": "",
}

__all__ = ["PackError", "apply_packs", "decide", "normal_reason", "pending", "prepare"]
MAX_CHANGES = (
    500  # entries of PackApply.changes; the total is in counts["changes_total"]
)
# Reasons that refuse an accept until fixed (P1-T15b2/b3 set them; keep stays allowed)
BLOCKING = frozenset(("conflict", "missing-link", "data-check"))
# Operators the command line may not use: staff decisions in the web app are "user-<pk>"
STAFF_OPERATOR_PREFIX = "user-"


class RefusedError(Exception):
    """This hospital cannot take this pack (the message is safe to show an operator)."""


class NothingWaitingError(RefusedError):
    """No such item, or nothing of it waits for a decision."""


class StaleError(RefusedError):
    """The item, the release or the hospital's row is not what the decision was made on."""


class BlockedError(RefusedError):
    """The item cannot be accepted until a problem is fixed (keep is still allowed)."""


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
    handler = HANDLERS[release.kind]
    for item in release.items:
        try:
            handler.check_item(item)
        except (KeyError, TypeError, ValueError) as exc:
            slug = item.get("slug") if isinstance(item, dict) else None
            msg = f"{release.file}: item {slug!r} is malformed: {safe_error(exc)}"
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
        check_items(versions[-1])
    if key is not None:
        _VERIFIED = {key: found}
    return found


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


def apply_packs(hospital, *, operator, releases=None, root=None, dry_run=False):
    """Apply the newest version of every pack to ``hospital`` (lock-free; see above).
    Returns a Result per pack; a pack that fails raises ApplyError (its writes rolled back).
    ``dry_run`` works out the same results and writes nothing."""
    if releases is None:
        releases = prepare(operator, root=root, record=not dry_run)
    results = []
    with schema_context(hospital.schema_name):
        for pack in sorted(releases):
            release = releases[pack]
            try:
                results.append(_apply_release(release, operator, dry_run=dry_run))
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


def _apply_release(release, operator, *, dry_run):
    handler = HANDLERS[release.kind]
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
        if last is not None and last.sha256 == release.sha256:
            slugs = [item["slug"] for item in release.items]
            waiting = PackItem.objects.filter(
                pack=release.pack, state=State.PENDING
            ).exists()
            if not waiting and handler.all_present(slugs):
                return Result(release.pack, release.version, "unchanged")
        counts, writes, changes = _reconcile(release, handler, dry_run=dry_run)
        # A newer release with nothing to write here (e.g. only CARE's spelling of compose
        # changed) is still recorded once, so the sha256 short-circuit fires from then on (C5)
        reached = last is None or last.sha256 != release.sha256
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
    return Result(release.pack, release.version, result, counts=counts)


def _reconcile(release, handler, *, dry_run):  # noqa: PLR0912, PLR0915 (the rule table)
    now = timezone.now()
    items = release.by_item()
    records = {
        r.item: r for r in PackItem.objects.filter(pack=release.pack, kind=release.kind)
    }
    rows = handler.rows(set(items) | set(records))
    counts = Counter()
    writes = 0
    changes = []  # [{item, facility, change}]: what this apply did, item by item

    def listed(slug, change):
        changes.append({"item": slug, "facility": None, "change": change})

    def note(record, slug, **values):
        """Write the record's new values; True when anything changed."""
        nonlocal writes
        if record is None:
            writes += 1
            if not dry_run:
                PackItem.objects.create(
                    pack=release.pack,
                    kind=release.kind,
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

    def waiting(record, slug, content_hash):
        reason = (
            Reason.EDITED if record and record.applied_sha256 else Reason.UNKNOWN_ORIGIN
        )
        counts["pending"] += 1
        if note(
            record,
            slug,
            state=State.PENDING,
            reason=reason,
            pending_version=release.version,
            pending_sha256=content_hash,
            kept_row_sha256="",
        ):
            listed(slug, "pending")

    for slug, item in items.items():
        new = handler.item_hash(item)
        row, record = rows.get(slug), records.get(slug)
        done = dict(_CURRENT, applied_version=release.version, applied_sha256=new)
        if row is None:
            if not dry_run:
                handler.create(item)
            counts["created"] += 1
            writes += 1
            listed(slug, "created")
            note(record, slug, **done)
        elif row.deleted:
            # CARE binds these slugs in code: a deleted one breaks every write bound to it
            if not dry_run:
                handler.write(row, item, restore=True)
            counts["restored"] += 1
            writes += 1
            listed(slug, "restored")
            note(record, slug, **done)
        elif (have := handler.row_hash(row)) == new:
            if not handler.usable(row):
                # Same meaning, but a list CARE's lookup iterates over is missing (CARE's API
                # can store compose without "exclude"): the pack's canonical compose (C2)
                if not dry_run:
                    handler.write(row, item)
                counts["repaired"] += 1
                writes += 1
                listed(slug, "repaired")
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
                waiting(record, slug, new)
        elif record is not None and record.applied_sha256 == have:
            if not dry_run:
                handler.write(row, item)
            counts["updated"] += 1
            writes += 1
            listed(slug, "updated")
            note(record, slug, **done)
        else:
            waiting(record, slug, new)

    for slug, record in records.items():
        if slug in items or record.state == State.RETIRED:
            continue
        row = rows.get(slug)
        retired = dict(_CURRENT, state=State.RETIRED)
        if row is None or row.deleted:
            counts["retired"] += 1
            if note(record, slug, **retired):
                listed(slug, "retired")
        elif handler.row_hash(row) == record.applied_sha256:
            wrote = row.status != valuesets.RETIRED
            if wrote:
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
                or wrote
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
    return dict(sorted(counts.items())), writes, changes


# --- what the hospital admin decides (CLI now; API and screen in P1-T15b and P1-T24) -------


def pending(hospital):
    with schema_context(hospital.schema_name):
        return list(
            PackItem.objects.filter(state__in=[State.PENDING, State.KEPT]).order_by(
                "pack", "item"
            )
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
    last = (
        PackApply.objects.filter(
            pack=record.pack,
            outcome__in=[Outcome.ACCEPTED, Outcome.KEPT],
            counts__item=record.item,
        )
        .order_by("-id")
        .values_list("outcome", "version")
        .first()
    )
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
    ``facility``: per-facility items come with P1-T15b3; only hospital-wide ones here.
    ``releases``: what ``prepare`` returned (the API prepares once, before the lock)."""
    reason = normal_reason(reason)
    if facility is not None:
        msg = "per-facility pack items are not decided here yet"
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
    handler = HANDLERS[release.kind]
    action = "accept" if accept else "keep"
    with (
        schema_context(hospital.schema_name),
        audit.extend(
            pack=release.pack,
            version=release.version,
            sha256=release.sha256,
            action=action,
            item=slug,
        ),
        transaction.atomic(),
    ):
        found = (
            PackItem.objects.select_for_update().filter(pack=pack, item=slug).first()
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
        if accept and found.reason in BLOCKING:
            msg = f"{pack}:{slug} cannot be accepted until its {found.reason} is fixed"
            raise BlockedError(msg)
        row = handler.rows([slug], lock=True).get(slug)
        if expect is not None and row_sha256(handler, row) != expect[2]:
            msg = f"the hospital's {slug} was changed since it was shown: look again"
            raise StaleError(msg)
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
                handler.create(item)
            else:
                handler.write(row, item, restore=True)
            found.state = State.CURRENT
            found.applied_version = release.version
            found.applied_sha256 = expected
        if found.state != State.KEPT:
            found.pending_version, found.pending_sha256 = None, ""
            found.kept_row_sha256 = ""
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
            counts={"item": slug, "reason": reason},
        )
    found.replayed = False
    return found
