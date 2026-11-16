"""A hospital's pending reference pack items, and the decision on one (P1-T15a).

    manage.py ihr_pack_pending --slug alpha
    manage.py ihr_pack_pending --slug alpha --accept care-valuesets:<slug> --operator <name> --reason "<why>"
    manage.py ihr_pack_pending --slug alpha --keep care-valuesets:<slug> --operator <name> --reason "<why>"
    manage.py ihr_pack_pending --slug alpha --recheck --operator <name>
    manage.py ihr_pack_pending --slug alpha --accept <pack>:<key> --facility <external id> ...

A per-facility pack's item (P1-T15b3) is decided in one facility: name it with --facility (its
external id, as the list shows it). With --facility and no decision, only that facility's items
are listed; --recheck takes no --facility (it reconciles every live facility). A deleted
facility's items are listed with "(deleted)" and cannot be decided until it is restored.

"Accept" writes the newest release's content for the item (read again from the verified pack
file; a soft-deleted row is restored). "Keep" leaves the hospital's own row; the item comes
back only when a later release changes it again. Owner rule Q-T10: the hospital admin's OK is
needed, so the reason names it (the admin's API and screen come with P1-T15b and P1-T24).

"Recheck" (P1-T15b2a) applies the packs again with every data check run afresh, whatever
counts are stored (an apply runs one only when the pack's content or the hospital's data has
moved). It takes the same hospitals as ``ihr_pack_apply --slug`` (live, not held, on this
build's schema) and is recorded and alerted on exactly as an apply: a failure is a PackApply
row and the line "ihr-packs: hospital=<slug> outcome=failed", as is a check still over its cap.

The list shows what holds each item as the last apply stored it: the conflict, the data
check's counts (patients only, never a value), whether it was fully checked, and when.

The reason is kept for good in the append-only audit trail: it names a role or a ticket
("hospital admin OK, ticket 123"), never personal details such as names or phone numbers. It
is put on one line and refused when longer than 300 characters. The decision is recorded as
"packs:<operator>".
"""

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import get_public_schema_name
from ihr_audit import context as audit
from ihr_platform.models import Hospital
from ihr_platform.provisioning import runner

from ihr_refdata import engine
from ihr_refdata.api import held
from ihr_refdata.management.commands.ihr_pack_apply import Command as ApplyCommand
from ihr_refdata.management.commands.ihr_pack_apply import check_operator


class Command(BaseCommand):
    help = "List a hospital's pending reference pack items, or accept or keep one."

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True, help="The hospital")
        decision = parser.add_mutually_exclusive_group()
        decision.add_argument("--accept", metavar="PACK:ITEM")
        decision.add_argument("--keep", metavar="PACK:ITEM")
        decision.add_argument(
            "--recheck",
            action="store_true",
            help="Run every data check again (an apply with the stored counts ignored)",
        )
        parser.add_argument(
            "--facility",
            metavar="EXTERNAL_ID",
            help="The facility of a per-facility pack's item (its external id)",
        )
        parser.add_argument(
            "--operator",
            help="Who decides (recorded as packs:<operator>; 1-64 letters, digits or . _ @ -)",
        )
        parser.add_argument(
            "--reason",
            help="Why, and whose OK: name a role or a ticket (e.g. 'hospital admin OK, "
            "ticket 123'), never personal details such as names or phone numbers; at most "
            f"{engine.MAX_REASON} characters; kept for good in the audit trail",
        )

    def handle(
        self, *args, slug, accept, keep, recheck, operator, reason, facility, **options
    ):
        hospital = (
            Hospital.objects.exclude(schema_name=get_public_schema_name())
            .filter(slug=slug)
            .first()
        )
        if hospital is None:
            msg = f"No hospital with slug {slug!r}"
            raise CommandError(msg)
        if recheck:
            if facility:
                msg = "--recheck takes every live facility: leave out --facility"
                raise CommandError(msg)
            self._recheck(hospital, operator)
            return
        target = accept or keep
        place = _facility(hospital, facility) if facility else None
        if not target:
            for record in engine.pending(hospital):
                if place is not None and record.facility_id != place.pk:
                    continue
                waits = f"v{record.pending_version}" if record.pending_version else "-"
                where = ""
                if record.facility_id is not None:
                    gone = " (deleted)" if record.facility.deleted else ""
                    where = f"  facility={record.facility.external_id}{gone}"
                self.stdout.write(
                    f"{record.pack}:{record.item}{where}  {record.state}"
                    f"{f' ({record.reason})' if record.reason else ''}  {waits}"
                    f"{_held_text(held(record))}"
                )
            return
        if not operator or not reason or not reason.strip():
            raise CommandError("--operator and --reason are required to decide")
        check_operator(operator)
        try:
            reason = engine.normal_reason(reason)
        except engine.RefusedError as exc:
            raise CommandError(str(exc)) from None
        actor = f"packs:{operator}"
        pack, sep, item = target.partition(":")
        if not sep or not pack or not item:
            raise CommandError("Name the item as PACK:ITEM")
        action = "accept" if accept else "keep"
        with audit.job(
            {
                "job": "packs",
                "via": "operator",
                "action": action,
                "item": item,
                "reason": reason,
            },
            actor,
        ):
            try:
                with runner.exclusive(hospital.slug):
                    record = engine.decide(
                        hospital,
                        pack,
                        item,
                        accept=bool(accept),
                        operator=actor,
                        reason=reason,
                        facility=place,
                    )
            except (
                runner.ProvisioningError,
                engine.RefusedError,
                engine.PackError,
            ) as exc:
                raise CommandError(str(exc)) from None
        where = f" in facility {place.external_id}" if place is not None else ""
        self.stdout.write(
            f"{hospital.slug}: {pack}:{item}{where} is now {record.state}"
        )

    def _recheck(self, hospital, operator):
        if not operator:
            raise CommandError("--operator is required to recheck")
        check_operator(operator)
        actor = f"packs:{operator}"
        apply = ApplyCommand(stdout=self.stdout, stderr=self.stderr)
        # ihr_pack_apply's own selection (review Q2): a hospital not live, held for a restore
        # or a release, or behind this build is refused
        ready, _ = apply._selection(hospital.slug, check=False)  # noqa: SLF001
        if not ready:
            msg = f"{hospital.slug} is not ready for packs: ihr_fleet_run check"
            raise CommandError(msg)
        hospital = ready[0]
        with audit.job({"job": "packs", "via": "operator", "action": "recheck"}, actor):
            try:
                releases = engine.prepare(actor)
            except engine.PackError as exc:
                msg = f"ihr-packs: refused for every hospital: {exc}"
                raise CommandError(msg) from None
            outcome = apply._one(  # noqa: SLF001
                hospital, releases, actor, check=False, recheck=True
            )
        if outcome != "done":
            msg = f"{hospital.slug}: the recheck was {outcome} (see above)"
            raise CommandError(msg)


def _facility(hospital, external_id):
    """The hospital's facility with this external id (CommandError when there is none)."""
    from care.facility.models import Facility
    from django.core.exceptions import ValidationError
    from django_tenants.utils import schema_context

    with schema_context(hospital.schema_name):
        try:
            found = Facility._base_manager.filter(  # noqa: SLF001
                external_id=external_id
            ).first()
        except ValidationError:
            found = None
    if found is None:
        msg = f"{hospital.slug} has no facility {external_id!r}"
        raise CommandError(msg)
    return found


def _held_text(found):
    """What holds an item, on one line (counts only, never a value)."""
    parts = []
    if "conflict" in found:
        parts.append(f"conflict: {found['conflict']}")
    if counts := found.get("counts"):
        parts.append(", ".join(f"{what} {n}" for what, n in sorted(counts.items())))
    if "patients" in found:
        parts.append(f"{found['patients']} patients")
        parts.append("fully checked" if found["complete"] else "NOT fully checked")
    if "checked_at" in found:
        parts.append(f"checked {found['checked_at']}")
    return f"  [{'; '.join(parts)}]" if parts else ""
