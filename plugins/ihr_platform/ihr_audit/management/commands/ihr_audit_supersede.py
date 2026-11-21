"""A whole-database restore and the audit chain (P1-T22c1, ihr_audit/supersede.py).

    ihr_audit_supersede export --restore-id <uuid> --point <name> --point-time <epoch>
                               --image <sha256:...> --operator <you>  > export.tar
        the old database, read-only (the audit watch's role): every hospital's superseded tail
        and the plan's draft, as one tar on standard output
    ihr_audit_supersede plan <restore id> --operator <you>  < export.tar
        the owner's one-off, the events key: the draft checked and the plan written (once)
    ihr_audit_supersede load <restore id> --operator <you>  < export.tar
        the restored database, the owner, before up: the tails archived back, restore events,
        a restore entry per hospital, then the result
    ihr_audit_supersede status <restore id> [--json]
        what the bucket says of it (plan, result, each hospital)

A disaster restore (P1-T22c2a, flow b; ``staging.sh restore --target`` and
``staging.sh audit-supersede export|load|close``):

    ihr_audit_supersede stub <restore id> --target-time <ISO> --old-timeline <8 hex>
                             --archive-max <WAL> --backup-set <label> --image <sha256:...>
                             --operator <you> [--no-supersede <reason>]
        the owner's one-off, no database, the events key only: the stub plan, just before the
        restore replaces anything
    ihr_audit_supersede export-copy <restore id> --image <sha256:...> --operator <you>
                             [--source-end <ISO>] [--accept-unreadable <reason>]  > export.tar
        the repository copy, read-only (the audit watch's role): every hospital's tail and the
        export doc's draft
    ihr_audit_supersede export-doc <restore id> --operator <you> --recovery-stop <time>
            [--recovery-replayed <time>]  < export.tar
        the owner's one-off, the events key only: the draft checked and the export doc written
    ihr_audit_supersede close <restore id> --reason <why> --operator <you> [--no-supersede]
        the result of a restore that cannot be loaded (every hospital not loaded: refused)
    ihr_audit_supersede level
        this build's level and the bucket's (Q-K101)

Exit status 1 on a refusal (named); ``load`` is run again after the cause is fixed: every step
is checked before it is done. Staging runs these through ``scripts/staging.sh audit-supersede``
and ``rollback --restore``.
"""

import json
import sys
import uuid
from datetime import UTC, datetime

from botocore.exceptions import ClientError
from django.core.management.base import BaseCommand, CommandError

from ihr_audit import anchor, events, supersede


def _uuid(value):
    try:
        return str(uuid.UUID(value))
    except ValueError:
        msg = f"not a restore id: {value[:40]!r}"
        raise CommandError(msg) from None


def _operator(value):
    if not events.OPERATOR_RE.match(value or ""):
        msg = "--operator: 1-64 of letters, digits, . _ @ -"
        raise CommandError(msg)
    return value


class Command(BaseCommand):
    help = (
        "A whole-database restore's superseded audit tails: export, plan, load, status"
    )
    # Runs from the leaving build against the target build's database (load) or as the audit
    # watch's role (export): the web app's checks are about serving requests, and the database
    # role is checked on its first connection (db_roles)
    requires_system_checks = []

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        exp = sub.add_parser("export")
        exp.add_argument("--restore-id", required=True)
        exp.add_argument("--point", required=True, help="the restore point's name")
        exp.add_argument(
            "--point-time", required=True, type=int, help="its time, epoch seconds"
        )
        exp.add_argument("--image", required=True, help="the image this tool runs from")
        exp.add_argument("--operator", required=True)
        for name in ("plan", "load", "export-doc"):
            one = sub.add_parser(name)
            one.add_argument("restore_id")
            one.add_argument("--operator", required=True)
            if (
                name == "export-doc"
            ):  # the restore's own stop and last commit (review T3)
                one.add_argument("--recovery-stop", required=True)
                one.add_argument("--recovery-replayed", default="")
        st = sub.add_parser("status")
        st.add_argument("restore_id")
        st.add_argument("--json", action="store_true")
        stub = sub.add_parser("stub")
        stub.add_argument("restore_id")
        stub.add_argument(
            "--target-time", required=True, help="T, ISO 8601 with offset"
        )
        stub.add_argument("--old-timeline", required=True)
        stub.add_argument(
            "--archive-max", required=True, help="the archive's newest WAL"
        )
        stub.add_argument("--backup-set", required=True)
        stub.add_argument("--image", required=True)
        stub.add_argument("--operator", required=True)
        stub.add_argument("--no-supersede", default="", help="the reason")
        copy = sub.add_parser("export-copy")
        copy.add_argument("restore_id")
        copy.add_argument("--image", required=True)
        copy.add_argument("--operator", required=True)
        copy.add_argument(
            "--source-end", default="", help="the copy's last transaction"
        )
        copy.add_argument("--accept-unreadable", default="", help="the reason")
        close = sub.add_parser("close")
        close.add_argument("restore_id")
        close.add_argument("--reason", required=True)
        close.add_argument("--operator", required=True)
        close.add_argument("--no-supersede", action="store_true")
        sub.add_parser("level")

    def handle(self, *args, **options):
        action = options["action"]
        try:
            if action == "export":
                self._export(options)
            elif action == "plan":
                members = supersede.read_tar(sys.stdin.buffer)
                key = supersede.write_plan(members, _uuid(options["restore_id"]))
                self.stderr.write(f"plan written: {key}")
            elif action == "load":
                self._load(options)
            elif action == "status":
                self._status(options)
            else:
                getattr(self, "_" + action.replace("-", "_"))(options)
        except (supersede.Refused, events.WriteOnceError, anchor.ScopeError) as exc:
            msg = f"refused: {exc}"
            raise CommandError(msg) from None
        except anchor.NotConfiguredError as exc:
            msg = f"the fingerprint bucket's keys: {exc}"
            raise CommandError(msg) from None
        except (
            ClientError
        ) as exc:  # e.g. AccessDenied: the writer key is not the events key
            msg = (
                f"the fingerprint bucket refused: {exc.response.get('Error', {}).get('Code')} "
                "(the events key: secrets/anchor-events.env or IHR_AUDIT_ANCHOR_EVENTS_KEY); "
                "what was done stays, run it again with the key"
            )
            raise CommandError(msg) from None

    def _export(self, options):
        rid = _uuid(options["restore_id"])
        if not events.POINT_RE.match(options["point"]) or not events.IMAGE_RE.match(
            options["image"]
        ):
            msg = "--point or --image is not ours"
            raise CommandError(msg)
        draft = supersede.export(
            sys.stdout.buffer,
            rid,
            options["point"],
            datetime.fromtimestamp(options["point_time"], UTC),
            options["image"],
            _operator(options["operator"]),
        )
        sys.stdout.buffer.flush()
        counts = {}
        for entry in draft["hospitals"].values():
            counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
        self.stderr.write(
            f"exported {rid}: "
            + ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
            + f"; old timeline {draft['old_timeline']}"
        )

    def _stub(self, options):
        if not events.IMAGE_RE.match(options["image"]):
            msg = "--image is not ours"
            raise CommandError(msg)
        try:
            when = datetime.fromisoformat(options["target_time"])
        except ValueError:
            when = None
        if when is None or when.tzinfo is None:
            msg = "--target-time: ISO 8601 with its offset"
            raise CommandError(msg)
        key = supersede.write_stub(
            _uuid(options["restore_id"]),
            when,
            options["old_timeline"],
            options["image"],
            _operator(options["operator"]),
            options["backup_set"],
            options["archive_max"],
            no_supersede=options["no_supersede"],
        )
        self.stderr.write(f"stub written: {key}")

    def _export_copy(self, options):
        if not events.IMAGE_RE.match(options["image"]):
            msg = "--image is not ours"
            raise CommandError(msg)
        rid = _uuid(options["restore_id"])
        draft = supersede.export_disaster(
            sys.stdout.buffer,
            rid,
            options["image"],
            _operator(options["operator"]),
            source_end=options["source_end"],
            accept_unreadable=options["accept_unreadable"],
        )
        sys.stdout.buffer.flush()
        counts = {}
        for entry in draft["hospitals"].values():
            counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
        self.stderr.write(
            f"exported {rid} from the copy: "
            + (
                ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
                or "no hospital"
            )
            + f"; old timeline {draft['old_timeline']}"
        )

    def _export_doc(self, options):
        rid = _uuid(options["restore_id"])
        _operator(options["operator"])
        members = supersede.read_tar(sys.stdin.buffer)
        key = supersede.write_export(
            members,
            rid,
            recovery={
                "stop": options["recovery_stop"],
                "replayed": options["recovery_replayed"],
            },
        )
        self.stderr.write(f"export doc written: {key}")

    def _close(self, options):
        key = supersede.close(
            _uuid(options["restore_id"]),
            options["reason"],
            _operator(options["operator"]),
            how="no-supersede" if options["no_supersede"] else "close",
        )
        self.stderr.write(f"result written: {key}")

    def _level(self, _options):
        found = events.read(
            anchor.S3Store("READ"), segments=events.NO_SEGMENTS, timeline=None
        )
        if not found.read_ok:
            msg = "the fingerprint bucket could not be read"
            raise CommandError(msg)
        self.stdout.write(
            f"tool={supersede.LEVEL} bucket={supersede.bucket_level(found)}"
        )

    def _load(self, options):
        rid = _uuid(options["restore_id"])
        members = supersede.read_tar(sys.stdin.buffer)
        outcomes = supersede.load(members, rid, _operator(options["operator"]))
        for o in outcomes.values():
            self.stderr.write(
                f"{o.slug}: {o.outcome}"
                + (
                    f" (generation {o.generation}, fork at seal {o.fork})"
                    if o.generation
                    else ""
                )
            )

    def _status(self, options):
        report = supersede.status(_uuid(options["restore_id"]))
        if options["json"]:
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True, default=str))
            return
        self.stdout.write(
            f"restore {report['restore_id']}: plan {report['plan'] or 'none'}, result "
            f"{report['result'] or 'none'}"
            + ("" if report["anchored"] else f" (not anchored: {report['why_not']})")
        )
        for slug, h in report["hospitals"].items():
            self.stdout.write(
                f"  {slug}: {h['planned']} -> {h['outcome']}; seals {h['lower']}..{h['head']}"
                + (f"; generation {h['generation']}" if h["generation"] else "")
                + (
                    f"; PROBLEM: {h['event_problems'][0]}"
                    if h["event_problems"]
                    else ""
                )
            )
