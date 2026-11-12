"""A hospital's pending reference pack items, and the decision on one (P1-T15a).

    manage.py ihr_pack_pending --slug alpha
    manage.py ihr_pack_pending --slug alpha --accept care-valuesets:<slug> --operator <name> --reason "<why>"
    manage.py ihr_pack_pending --slug alpha --keep care-valuesets:<slug> --operator <name> --reason "<why>"

"Accept" writes the newest release's content for the item (read again from the verified pack
file; a soft-deleted row is restored). "Keep" leaves the hospital's own row; the item comes
back only when a later release changes it again. Owner rule Q-T10: the hospital admin's OK is
needed, so the reason names it (the admin's API and screen come with P1-T15b and P1-T24).

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
from ihr_refdata.management.commands.ihr_pack_apply import check_operator


class Command(BaseCommand):
    help = "List a hospital's pending reference pack items, or accept or keep one."

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True, help="The hospital")
        decision = parser.add_mutually_exclusive_group()
        decision.add_argument("--accept", metavar="PACK:ITEM")
        decision.add_argument("--keep", metavar="PACK:ITEM")
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

    def handle(self, *args, slug, accept, keep, operator, reason, **options):
        hospital = (
            Hospital.objects.exclude(schema_name=get_public_schema_name())
            .filter(slug=slug)
            .first()
        )
        if hospital is None:
            msg = f"No hospital with slug {slug!r}"
            raise CommandError(msg)
        target = accept or keep
        if not target:
            for record in engine.pending(hospital):
                waits = f"v{record.pending_version}" if record.pending_version else "-"
                self.stdout.write(
                    f"{record.pack}:{record.item}  {record.state}"
                    f"{f' ({record.reason})' if record.reason else ''}  {waits}"
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
                    )
            except (
                runner.ProvisioningError,
                engine.RefusedError,
                engine.PackError,
            ) as exc:
                raise CommandError(str(exc)) from None
        self.stdout.write(f"{hospital.slug}: {pack}:{item} is now {record.state}")
