"""Check a hospital's audit trail: triggers on every table, and the hash chain recomputed.

    ihr_audit_verify --slug alpha [--seal]      one hospital
    ihr_audit_verify --all [--seal]             every hospital that has a schema

``--seal`` seals what is final first (what the every-minute job does). Every hospital is also
checked against every outside fingerprint (anchor.py; reader credentials from the
environment; ``--since`` limits that to recent ones for frequent runs). ``--anchor`` writes a fingerprint first (needs the writer credentials). Exit
status 1 on any problem, so it can run from cron or CI.

P1-T21: restore and offboarding events in the bucket (events.py) are honoured: a fingerprint
claim a verified restore event accounts for is not a problem, and an offboarded hospital's
fingerprints are closed. ``--from-checkpoint <dir>`` checks each chain incrementally from the
audit watch's checkpoints in that folder (the header walk, the checkpoint, the entries of the
seals after it; ihr_audit/incremental.py) instead of recomputing it from seal 1; it never
changes the checkpoints. ``--json`` prints one JSON document instead of text.
"""

import json
import os
from datetime import date, datetime
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django_tenants.utils import schema_context
from ihr_platform.models import Hospital

from ihr_audit import anchor, events, incremental
from ihr_audit.install import coverage
from ihr_audit.seal import seal_once, verify


class Command(BaseCommand):
    help = "Verify the audit trail's triggers and hash chain"

    def add_arguments(self, parser):
        which = parser.add_mutually_exclusive_group(required=True)
        which.add_argument("--slug")
        which.add_argument("--all", action="store_true")
        parser.add_argument("--seal", action="store_true")
        parser.add_argument("--anchor", action="store_true")
        parser.add_argument(
            "--since",
            type=date.fromisoformat,
            help="read only fingerprints written from this day (YYYY-MM-DD); "
            "the default reads all of them",
        )
        parser.add_argument("--stale-minutes", type=int, default=10)
        parser.add_argument(
            "--as-of",
            type=datetime.fromisoformat,
            help="a database restored to this moment (ISO 8601 with offset; restore-drill, "
            "P1-T8c): only fingerprints stored by then count, and 'late' is measured then",
        )
        parser.add_argument(
            "--from-checkpoint",
            type=Path,
            metavar="DIR",
            help="check each chain from the audit watch's checkpoints in DIR (P1-T21)",
        )
        parser.add_argument("--json", action="store_true", help="one JSON document")

    def _say(self, text, style=None):
        if not self.as_json:
            self.stdout.write(style(text) if style else text)

    def _anchors(self, options):
        """(fingerprints or None when not checked, platform problems). A storage outage
        is a problem, but the local checks still run."""
        written = []
        if options["anchor"]:
            try:
                self._say(f"fingerprint written: {anchor.write()}")
            except (anchor.NotConfiguredError, BotoCoreError, ClientError) as exc:
                written = [
                    f"fingerprint not written: {exc} (run --anchor in the celery "
                    "container, which holds the writer's key)"
                ]
        if (
            options["anchor"]
            and not written
            and not os.environ.get("IHR_AUDIT_ANCHOR_READ_KEY")
        ):
            # The writer's container (Celery) holds no reader key, on purpose: the fingerprint
            # is written; the backend checks them all (P1-T8c)
            self._say(
                "outside fingerprints are checked where the reader's key is (the backend)"
            )
            return None, []
        events_problems = []
        try:
            self.events = events.read()
        except anchor.NotConfiguredError:
            pass  # reported with the fingerprints below
        except (BotoCoreError, ClientError) as exc:
            events_problems.append(f"restore and offboarding events unreadable: {exc}")
        try:
            fps = anchor.load_all(
                since=options["since"],
                until=options["as_of"],
                excuse=self.events.excuse,
            )
        except anchor.NotConfiguredError as exc:
            message = f"outside fingerprints not checked: {exc}"
            if settings.DEBUG and not written:
                self._say(message, self.style.WARNING)
                return None, []
            return None, [*written, message]
        except (BotoCoreError, ClientError) as exc:
            return None, [*written, f"outside fingerprints unreadable: {exc}"]
        for note in fps.notes:
            self._say(note, self.style.WARNING)
        self._say(f"{fps.count} outside fingerprints read")
        if fps.excused:
            self._say(
                f"{fps.excused} fingerprint claims accounted for by restore events"
            )
        return fps, (
            written
            + fps.problems
            + events_problems
            + self.events.problems
            + anchor.platform_problems(fps, now=options["as_of"])
        )

    def handle(self, *args, **options):  # noqa: PLR0912 (one flat report)
        self.as_json = options["json"]
        self.events = events.Events()
        as_of = options["as_of"]
        if as_of and (as_of.tzinfo is None or options["seal"] or options["anchor"]):
            msg = "--as-of needs a time zone offset, and reads only (no --seal or --anchor)"
            raise CommandError(msg)
        hospitals = Hospital.objects.exclude(schema_name="public").order_by("slug")
        if options["slug"]:
            hospitals = hospitals.filter(slug=options["slug"])
            if not hospitals:
                msg = f"no hospital with slug {options['slug']!r}"
                raise CommandError(msg)
        if options["seal"]:  # before a fingerprint is written and read
            for hospital in hospitals:
                with schema_context(hospital.schema_name):
                    seal_once(hospital.schema_name)
        anchors, platform = self._anchors(options)
        if anchors is not None and options["all"]:
            every = Hospital.objects.exclude(schema_name="public")
            platform += anchor.unknown_hospitals(
                anchors, {str(h.uuid): h.slug for h in every}, self.events.closed()
            )
        failed = bool(platform)
        report = {"platform": platform, "hospitals": {}}
        for problem in platform:
            self._say(problem, self.style.ERROR)
        checkpoints = (
            incremental.CheckpointStore(options["from_checkpoint"])
            if options["from_checkpoint"]
            else None
        )
        for hospital in hospitals:
            with schema_context(hospital.schema_name):
                problems = coverage(connection, hospital.schema_name)
                if checkpoints is not None:
                    chain, summary = self._from_checkpoint(
                        hospital, checkpoints, anchors
                    )
                else:
                    chain, summary = verify(
                        hospital.schema_name, options["stale_minutes"], as_of=as_of
                    )
            problems += chain
            if anchors is not None:
                problems += anchor.check(str(hospital.uuid), anchors)
            report["hospitals"][hospital.slug] = {"problems": problems, **summary}
            self._say(
                f"{hospital.slug}: {summary['seals']} seals, "
                f"{summary['sealed_entries']} entries sealed, "
                f"{summary['unsealed_entries']} not yet, head {summary['head'][:16]}"
            )
            if summary["held_back"]:
                self._say(
                    f"  {summary['held_back']} entries older than "
                    f"{options['stale_minutes']} minutes wait for a transaction "
                    "that is still running (check pg_stat_activity)",
                    self.style.WARNING,
                )
            for problem in problems:
                failed = True
                self._say(f"  {problem}", self.style.ERROR)
        if self.as_json:
            report["ok"] = not failed
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True, default=str))
        if failed:
            raise SystemExit(1)
        self._say("audit trail OK", self.style.SUCCESS)

    def _from_checkpoint(self, hospital, checkpoints, anchors):
        """The watch's incremental check of one chain, read-only (P1-T21)."""
        hospital_uuid = str(hospital.uuid)
        claim = anchors.newest_claims.get(hospital_uuid) if anchors else None
        with connection.cursor() as cursor:
            result = incremental.check_from_checkpoint(
                cursor,
                hospital.schema_name,
                hospital_uuid,
                checkpoints.get(hospital_uuid),
                claim,
                self.events,
            )
            waiting = incremental.unsealed(cursor, hospital.schema_name)
            walk = result.walk
            sealed = incremental.entries_between(
                cursor, hospital.schema_name, 1, walk.head_seq
            )
        done = result.recomputed
        summary = {
            "seals": walk.seals,
            "sealed_entries": sealed,
            "unsealed_entries": waiting.sealable + waiting.held_back,
            "oldest_unsealed": waiting.sealable_oldest or waiting.held_back_oldest,
            "held_back": waiting.held_back,
            "head": walk.head_hash,
            # the seals whose entries were recomputed now (the rest: the header walk)
            "entries_recomputed_from_seal": done.last_seq - done.seals + 1
            if done
            else None,
            "entries_recomputed": done.entries if done else 0,
            "seeded": result.seeded,
        }
        return result.problems, summary
