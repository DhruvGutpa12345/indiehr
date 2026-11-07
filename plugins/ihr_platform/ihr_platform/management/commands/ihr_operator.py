"""Who may use the operator console (P1-T5b). The only way to change the list.

    manage.py ihr_operator add --sub <operator realm user id> --name "Asha Rao"
    manage.py ihr_operator list
    manage.py ihr_operator remove --sub <id>

An operator also needs the ``ihr-operator`` role in the operator realm: both locks must open.
"""

import getpass
import uuid

from django.core.management.base import BaseCommand, CommandError

from ihr_platform.models import OperatorAccount


class Command(BaseCommand):
    help = "Add, list or remove platform operators."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        add = sub.add_parser("add")
        add.add_argument("--sub", required=True, type=uuid.UUID)
        add.add_argument("--name", required=True)
        sub.add_parser("list")
        remove = sub.add_parser("remove")
        remove.add_argument("--sub", required=True, type=uuid.UUID)

    def handle(self, *args, action, **options):
        who = f"cli:{getpass.getuser()}"
        if action == "add":
            account, _ = OperatorAccount.objects.update_or_create(
                keycloak_sub=options["sub"],
                defaults={"name": options["name"], "active": True, "added_by": who},
            )
            self.stdout.write(f"operator: {account}")
        elif action == "remove":
            changed = OperatorAccount.objects.filter(
                keycloak_sub=options["sub"]
            ).update(active=False)
            if not changed:
                raise CommandError("No such operator.")
            self.stdout.write("removed (sessions end on their next request)")
        else:
            for account in OperatorAccount.objects.order_by("name"):
                self.stdout.write(f"{account.keycloak_sub}  {account}")
