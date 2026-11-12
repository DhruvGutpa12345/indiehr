"""Load terminology content into the shared tables (P1-T10a1, ADR-016). Run in public:

manage.py ihr_terminology_load --bundled --operator <name>
manage.py ihr_terminology_load --fhir <CodeSystem.json> [--sha256 <hex>] --operator <name>
manage.py ihr_terminology_load --rf2 <edition.zip> --sha256 <hex> [--rf2 <extension.zip> --sha256 <hex> ...]
                               --licence <reference> [--language-refset en:<id> ...] [--release-version <v>]
                               --operator <name>                                  (P1-T10a2)
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
        what.add_argument(
            "--rf2",
            action="append",
            help="a SNOMED CT RF2 release zip; repeat for extensions",
        )
        what.add_argument("--status", action="store_true")
        what.add_argument("--prune", action="store_true")
        parser.add_argument(
            "--sha256",
            action="append",
            default=[],
            help="one per --rf2 (or for --fhir)",
        )
        parser.add_argument("--operator", help="who runs the load (recorded with it)")
        parser.add_argument("--licence", default="")
        parser.add_argument(
            "--language-refset",
            action="append",
            default=[],
            help="<language>:<refset id>, in priority order (default en:900000000000508004, GB English)",
        )
        parser.add_argument(
            "--release-version", help="defaults to the date in the file names"
        )

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
            if o["rf2"]:
                from ihr_terminology import rf2

                before = CodeSystemVersion.objects.count()
                v, summary = rf2.load_release(
                    o["rf2"],
                    operator=o["operator"],
                    sha256=o["sha256"],
                    language_refsets=o["language_refset"],
                    version=o["release_version"],
                    licence=o["licence"],
                )
                versions = [(v, CodeSystemVersion.objects.count() > before)]
                self.stdout.write(f"summary {summary}")
                import resource

                peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
                self.stdout.write(f"loader peak memory {peak_mb} MB")
            elif o["bundled"]:
                versions = loader.load_bundled(operator=o["operator"])
            else:
                before = CodeSystemVersion.objects.count()
                v = loader.load_file(
                    o["fhir"],
                    sha256=(o["sha256"] or [None])[0],
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
