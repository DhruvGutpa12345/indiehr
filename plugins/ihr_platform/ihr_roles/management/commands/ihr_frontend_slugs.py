"""Print, write or check the web app's slug sets (P1-T11c1; ihr_roles/frontend.py).

    manage.py ihr_frontend_slugs            print the JSON (stdout)
    manage.py ihr_frontend_slugs --write    write ihr_roles/frontend_slugs.json
    manage.py ihr_frontend_slugs --check    exit 1 when the file differs

The dev containers mount plugins/ read-only, so on this machine regenerate with
``scripts/care-tenancy.sh ihr_frontend_slugs > plugins/ihr_platform/ihr_roles/frontend_slugs.json``
and copy the file to ``apps/frontend/src/Utils/ihr/ihrSlugs.json`` (byte-identical).
"""

from django.core.management.base import BaseCommand, CommandError

from ihr_roles import frontend


class Command(BaseCommand):
    help = "Print, write (--write) or check (--check) ihr_roles/frontend_slugs.json."
    requires_system_checks = []  # no database: the sets come from code only

    def add_arguments(self, parser):
        what = parser.add_mutually_exclusive_group()
        what.add_argument("--write", action="store_true", help="write the file")
        what.add_argument(
            "--check", action="store_true", help="exit 1 when the file differs"
        )

    def handle(self, *args, write=False, check=False, **options):
        text = frontend.render()
        if write:
            frontend.PATH.write_text(text, encoding="utf-8")
            self.stderr.write(f"wrote {frontend.PATH}")
        elif check:
            current = (
                frontend.PATH.read_text(encoding="utf-8")
                if frontend.PATH.exists()
                else None
            )
            if current != text:
                msg = f"{frontend.PATH.name} is out of date: run ihr_frontend_slugs --write"
                raise CommandError(msg)
            self.stderr.write(f"{frontend.PATH.name} is current")
        else:
            self.stdout.write(text, ending="")
