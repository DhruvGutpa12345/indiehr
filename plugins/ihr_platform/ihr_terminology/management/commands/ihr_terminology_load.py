"""Load terminology content into the shared tables (P1-T10a1, ADR-016). Run in public:

manage.py ihr_terminology_load --bundled --operator <name>
manage.py ihr_terminology_load --fhir <CodeSystem.json> [--sha256 <hex>] --operator <name>
manage.py ihr_terminology_load --status
manage.py ihr_terminology_load --prune --operator <name>
"""

import logging

from django.core.management.base import BaseCommand, CommandError

from ihr_terminology import loader
from ihr_terminology.models import CodeSystemVersion

log = logging.getLogger("ihr.terminology")


class Command(BaseCommand):
    help = "Load terminology content (FHIR CodeSystem/ValueSet JSON, or the bundled free content)"

    def add_arguments(self, parser):
        what = parser.add_mutually_exclusive_group(required=True)
        what.add_argument("--bundled", action="store_true")
        what.add_argument("--fhir")
        what.add_argument("--status", action="store_true")
        what.add_argument("--prune", action="store_true")
        parser.add_argument("--sha256")
        parser.add_argument("--operator", help="who runs the load (recorded with it)")
        parser.add_argument("--licence", default="")

    def handle(self, *args, **o):
        try:
            if o["status"]:
                for v in CodeSystemVersion.objects.all():
                    self.stdout.write(
                        f"{v.status:8} {v.url} {v.version} ({v.concepts} codes; {v.loaded_at:%d-%m-%Y %H:%M} by {v.loaded_by}; sha256 {v.sha256[:12]})"
                    )
                return
            if o["prune"]:
                n = loader.prune(operator=o["operator"])
                self.stdout.write(f"pruned {n} rows of retired versions")
                return
            if o["bundled"]:
                versions = loader.load_bundled(operator=o["operator"])
            else:
                before = CodeSystemVersion.objects.count()
                v = loader.load_file(
                    o["fhir"],
                    sha256=o["sha256"],
                    operator=o["operator"],
                    licence=o["licence"],
                )
                versions = [(v, CodeSystemVersion.objects.count() > before)]
        except loader.LoadError as e:
            raise CommandError(str(e)) from e
        for v, new in versions:
            if new:
                # One line per new version into the log (staging.sh also hands it to the host's journal)
                log.warning(
                    "terminology loaded: %s %s, %d codes, sha256 %s, by %s",
                    v.url,
                    v.version,
                    v.concepts,
                    v.sha256,
                    v.loaded_by,
                )
            self.stdout.write(
                f"{'loaded' if new else 'unchanged':9} {v.status} {v.url} {v.version} ({v.concepts} codes, sha256 {v.sha256[:12]})"
            )
