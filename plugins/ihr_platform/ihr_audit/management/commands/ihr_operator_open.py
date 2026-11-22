"""``manage.py ihr_operator_open --session S --operator O --scope <slug|all> --reason R --tier data|job``

One ``operator`` entry in each hospital's trail for a recorded operator session (P1-T27b, Q-K136;
``ihr_audit/operator.py``). Run by ``scripts/staging.sh`` (``op_scope_open``, in the admin one-off)
at the start of a data-tier session, and of a job of every hospital, before anything else runs.

Prints exactly one line, ``opened=<slug,...|-> skipped=<slug:why,...|->`` (why: ``held``, ``absent``,
``old-schema``, ``failed``), and exits 0. An entry that could not be written in some hospital is
``failed`` in that line (the others are still tried, and entries written stay), and the command
exits 3 with the reasons on stderr: staging.sh records the line, then refuses the session (review
A7). A value staging.sh would have refused, or any other database error, exits 1 with the reason
on stderr and no answer: staging.sh then refuses the session.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from ihr_audit import operator


class Command(BaseCommand):
    help = (
        "Write the operator access entry of a session into each hospital in its scope."
    )
    # A release's new build runs this on the old schema, before its migrations (staging.sh
    # op_scope_open): the system checks must not read the database (review A2/O7)
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--session", required=True)
        parser.add_argument("--operator", required=True)
        parser.add_argument("--scope", required=True, help="a hospital's slug, or all")
        parser.add_argument("--reason", required=True)
        parser.add_argument("--tier", required=True, help="data or job")

    def handle(self, *args, **options):
        try:
            opened, skipped = operator.open_entries(
                options["session"],
                options["operator"],
                options["scope"],
                options["reason"],
                options["tier"],
                warn=lambda text: self.stderr.write(f"warning: {text}"),
            )
        except operator.OperatorError as exc:
            raise CommandError(str(exc)) from None
        except operator.EntryWriteError as exc:
            self.stdout.write(operator.answer(exc.opened, exc.skipped))
            raise CommandError(str(exc), returncode=3) from None
        except DatabaseError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(operator.answer(opened, skipped))
