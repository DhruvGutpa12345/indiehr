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


class RefusedError(Exception):
    """This hospital cannot take this pack (the message is safe to show an operator)."""


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


def prepare(operator, *, root=None, record=True):
    """Check every pack file against the manifest, every item of each pack's newest release
    (the only one applied), and each release against the fleet's record of it
    (``PackRelease``, written the first time a release is seen). Returns the newest release of
    each pack. Any fault here is the same for every hospital: PackError."""
    from ihr_refdata_registry.models import PackRelease

    found = manifest.releases(root)
    for versions in found.values():
        check_items(versions[-1])
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
        counts, writes = _reconcile(release, handler, dry_run=dry_run)
        # A newer release with nothing to write here (e.g. only CARE's spelling of compose
        # changed) is still recorded once, so the sha256 short-circuit fires from then on (C5)
        reached = last is None or last.sha256 != release.sha256
        if (writes or reached) and not dry_run:
            PackApply.objects.create(
                pack=release.pack,
                version=release.version,
                sha256=release.sha256,
                care_commit=release.care_commit,
                outcome=Outcome.APPLIED if writes else Outcome.REACHED,
                actor=operator[:150],
                counts=counts,
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

    def note(record, slug, **values):
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
            return
        changed = [k for k, v in values.items() if getattr(record, k) != v]
        if changed:
            writes += 1
            if not dry_run:
                for k in changed:
                    setattr(record, k, values[k])
                record.changed_at = now
                record.save(update_fields=[*changed, "changed_at"])

    def waiting(record, slug, content_hash):
        reason = (
            Reason.EDITED if record and record.applied_sha256 else Reason.UNKNOWN_ORIGIN
        )
        counts["pending"] += 1
        note(
            record,
            slug,
            state=State.PENDING,
            reason=reason,
            pending_version=release.version,
            pending_sha256=content_hash,
            kept_row_sha256="",
        )

    for slug, item in items.items():
        new = handler.item_hash(item)
        row, record = rows.get(slug), records.get(slug)
        done = dict(_CURRENT, applied_version=release.version, applied_sha256=new)
        if row is None:
            if not dry_run:
                handler.create(item)
            counts["created"] += 1
            writes += 1
            note(record, slug, **done)
        elif row.deleted:
            # CARE binds these slugs in code: a deleted one breaks every write bound to it
            if not dry_run:
                handler.write(row, item, restore=True)
            counts["restored"] += 1
            writes += 1
            note(record, slug, **done)
        elif (have := handler.row_hash(row)) == new:
            if not handler.usable(row):
                # Same meaning, but a list CARE's lookup iterates over is missing (CARE's API
                # can store compose without "exclude"): the pack's canonical compose (C2)
                if not dry_run:
                    handler.write(row, item)
                counts["repaired"] += 1
                writes += 1
                note(record, slug, **done)
            elif record is not None and (
                record.state == State.CURRENT and record.applied_sha256 == new
            ):
                counts["unchanged"] += 1
                continue
            else:
                # the row already holds it: nothing written to it
                counts["adopted"] += 1
                note(record, slug, **done)
        elif record is not None and record.applied_sha256 == new:
            # The hospital edited the row and the pack has nothing new for it since it last
            # applied it: the edit is the hospital's own, never offered for revert (C4). A
            # change that waited (or was kept) and that the pack has since withdrawn goes too.
            counts["local-edit"] += 1
            if record.state != State.CURRENT:
                note(record, slug, **_CURRENT)
            if not handler.usable(row):
                # their edit left out a list CARE's lookup iterates over (CARE's own API can
                # store compose without "exclude"): add it empty, keeping their content
                if not dry_run:
                    handler.make_usable(row)
                counts["repaired"] += 1
                writes += 1
        elif record is not None and record.state == State.KEPT:
            if record.pending_sha256 == new:  # the very content the hospital declined
                counts["kept"] += 1
                # follows the newest release, still kept, so accept stays possible (C1)
                note(record, slug, pending_version=release.version)
            else:
                waiting(record, slug, new)
        elif record is not None and record.applied_sha256 == have:
            if not dry_run:
                handler.write(row, item)
            counts["updated"] += 1
            writes += 1
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
            note(record, slug, **retired)
        elif handler.row_hash(row) == record.applied_sha256:
            if row.status != valuesets.RETIRED:
                writes += 1
                if not dry_run:
                    handler.retire(row)
            counts["retired"] += 1
            # The retired row's hash: a later re-add finds it unedited (C9)
            note(
                record,
                slug,
                **retired,
                applied_version=release.version,
                applied_sha256=handler.retired_hash(row),
            )
        elif record.state == State.KEPT and record.pending_sha256 == "":
            counts["kept"] += 1  # the hospital kept a set the pack dropped
            note(record, slug, pending_version=release.version)  # accept stays possible
        else:
            waiting(record, slug, "")  # accepting it retires the row
    return dict(sorted(counts.items())), writes


# --- what the hospital admin decides (CLI now; API and screen in P1-T15b and P1-T24) -------


def pending(hospital):
    with schema_context(hospital.schema_name):
        return list(
            PackItem.objects.filter(state__in=[State.PENDING, State.KEPT]).order_by(
                "pack", "item"
            )
        )


def decide(hospital, pack, slug, *, accept, operator, reason, root=None):
    """Accept a pending item (the pack's content, rebuilt from the verified pack file, is
    written; a soft-deleted row is restored with it) or keep the hospital's own row. Only
    the newest release can be accepted or kept. The caller holds the hospital's lock and has
    put the reason in the audit context (``ihr_pack_pending``'s job): it is not added here, so
    the trail never holds more of it than the normalised, length-checked form (S7)."""
    reason = normal_reason(reason)
    release = prepare(operator, root=root).get(pack)
    if release is None:
        msg = f"no pack named {pack!r}"
        raise RefusedError(msg)
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
        record = (
            PackItem.objects.select_for_update().filter(pack=pack, item=slug).first()
        )
        allowed = (State.PENDING, State.KEPT) if accept else (State.PENDING,)
        if record is None or record.state not in allowed:
            msg = f"{pack}:{slug} has nothing waiting to {action}"
            raise RefusedError(msg)
        if record.pending_version != release.version:
            msg = (
                f"{pack}:{slug} waits for v{record.pending_version}, but the newest "
                f"release is v{release.version}: run ihr_pack_apply first"
            )
            raise RefusedError(msg)
        item = release.by_item().get(slug)
        expected = handler.item_hash(item) if item is not None else ""
        if expected != record.pending_sha256:
            msg = f"{pack}:{slug}: the waiting content is not the release's"
            raise RefusedError(msg)
        row = handler.rows([slug]).get(slug)
        now = timezone.now()
        if not accept:
            record.state = State.KEPT
            record.kept_row_sha256 = handler.row_hash(row) if row is not None else ""
        elif item is None:  # the pack dropped it: retire the row
            if row is not None and not row.deleted:
                handler.retire(row)
                record.applied_sha256 = handler.retired_hash(row)
            record.state = State.RETIRED
            record.applied_version = release.version
        else:
            if row is None:
                handler.create(item)
            else:
                handler.write(row, item, restore=True)
            record.state = State.CURRENT
            record.applied_version = release.version
            record.applied_sha256 = expected
        if record.state != State.KEPT:
            record.pending_version, record.pending_sha256 = None, ""
            record.kept_row_sha256 = ""
        record.reason = Reason.NONE if accept else record.reason
        record.changed_at = now
        record.save()
        PackApply.objects.create(
            pack=release.pack,
            version=release.version,
            sha256=release.sha256,
            care_commit=release.care_commit,
            outcome=Outcome.ACCEPTED if accept else Outcome.KEPT,
            actor=operator[:150],
            counts={"item": slug, "reason": reason},
        )
    return record
