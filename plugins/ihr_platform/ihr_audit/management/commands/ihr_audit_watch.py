"""The audit watch (P1-T21, ihr_audit/watch.py).

    ihr_audit_watch serve                       the service: every pass on its schedule, and the
                                                metrics on :9108 (the audit-watch container)
    ihr_audit_watch once --pass fast [--pass incremental ...] [--json]
                                                those passes now, then the metrics they set
    ihr_audit_watch measure [--json] [--days N] [--max-entries N] [--full]
                                                the ingest rate and what each pass costs on this
                                                database (the nightly budget is sized from it)

``--dir`` (default IHR_AUDIT_WATCH_DIR) is where its checkpoints and state live. ``once``
without ``--dir`` reads the serving watch's state but never writes it (P1-T21 review C12: the
serving watch's state is its own); its nightly and sweep findings that stand until the next
nightly are counted apart (``standing_findings``). Exit status of ``once``: 1 when a pass
failed or found a broken chain, a fingerprint mismatch, a trigger problem or a hospital it
could not check.

``measure`` is bounded (C12): per hospital the seals of the last ``--days`` (default 7) are
recomputed, at most ``--max-entries`` (default 200,000) entries, and the ingest counts come
from the seals' own counts. ``--full`` (every chain from seal 1 and a count of every entry)
runs only on a ``bench_*`` database or the restore drill's copy, never on the live one.
"""

import json
import resource
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from psycopg import sql

from ihr_audit import incremental, watch


class Command(BaseCommand):
    help = "The audit watch: integrity checks of every hospital's audit trail (P1-T21)"
    # The web app's system checks (Keycloak addresses, its client key, a shared cache for rate
    # limits) are about serving requests; the watch serves none and holds none of them. Its own
    # settings are checked by settings/watch.py (secure.watch_problems) and its database role on
    # its first connection (db_roles.watch_problems)
    requires_system_checks = []

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        sub.add_parser("serve")
        once = sub.add_parser("once")
        once.add_argument(
            "--pass", dest="passes", action="append", choices=watch.PASSES
        )
        once.add_argument("--json", action="store_true")
        once.add_argument(
            "--metrics", action="store_true", help="print the metrics text"
        )
        measure = sub.add_parser("measure")
        measure.add_argument("--json", action="store_true")
        measure.add_argument("--days", type=int, default=7)
        measure.add_argument("--max-entries", type=int, default=200_000)
        measure.add_argument(
            "--full",
            action="store_true",
            help="every chain from seal 1 (a bench_* or drill database only)",
        )
        for p in (sub.choices["serve"], once, measure):
            p.add_argument("--dir", type=Path)

    def _config(self, options):
        config = watch.Config()
        if options.get("dir"):
            config.root = options["dir"]
        return config

    def handle(self, *args, **options):
        action = options["action"]
        if action == "serve":
            watch.Watch(self._config(options)).serve()
            return
        if action == "measure":
            self._measure(options)
            return
        # Beside the serving watch (its own folder), its state is read, never written
        w = watch.Watch(self._config(options), save_state=bool(options.get("dir")))
        results = [w.run(name) for name in options["passes"] or ["fast", "incremental"]]
        if options["json"]:
            self.stdout.write(
                json.dumps(results, indent=2, sort_keys=True, default=str)
            )
        else:
            for r in results:
                self.stdout.write(json.dumps(r, sort_keys=True, default=str))
        if options["metrics"]:
            self.stdout.write(w.gauges.render())
        values = {name: v for (name, _labels), v in w.gauges.values.items()}
        bad = any(not r["ok"] for r in results) or any(
            values.get(name, 0) > 0
            for name in (
                "ihr_audit_chain_broken_hospitals",
                "ihr_audit_fingerprint_mismatch_hospitals",
                "ihr_audit_trigger_problem_hospitals",
                "ihr_audit_disabled_trigger_hospitals",
                "ihr_audit_hospitals_unchecked",
            )
        )
        if bad:
            raise SystemExit(1)

    def _measure(self, options):
        """What the passes cost here and how fast the trail grows: the numbers the nightly
        budget is sized from (docs/reviews/p1-t21/design.md, As built). Bounded unless
        ``--full`` (a bench_* or drill database only)."""
        full_run = options.get("full", False)
        name = connection.settings_dict.get("NAME") or ""
        if full_run and not (name.startswith("bench_") or name.endswith("_drill")):
            msg = (
                f"--full recomputes every chain from seal 1: only on a bench_* database or "
                f"the restore drill's copy, not {name!r}"
            )
            raise CommandError(msg)
        hospitals = watch._hospitals()  # noqa: SLF001 (the watch's own listing)
        now = datetime.now(UTC)
        out = {"hospitals": len(hospitals), "bounded": not full_run, "per_hospital": []}
        totals = {
            "entries": 0,
            "seals": 0,
            "entries_1d": 0,
            "entries_7d": 0,
            "entries_30d": 0,
            "walk_seconds": 0.0,
            "recompute_seconds": 0.0,
            "fast_seconds": 0.0,
        }
        with connection.cursor() as cursor:
            for h in hospitals:
                if not watch._has_trail(cursor, h.schema):  # noqa: SLF001
                    continue
                # From the seals' own counts (one index-free scan of the small seal table),
                # never a count of every entry on the live database
                cursor.execute(
                    sql.SQL(
                        "SELECT coalesce(sum(entries), 0), "
                        "coalesce(sum(entries) FILTER (WHERE sealed_at > %s), 0), "
                        "coalesce(sum(entries) FILTER (WHERE sealed_at > %s), 0), "
                        "coalesce(sum(entries) FILTER (WHERE sealed_at > %s), 0) FROM {}"
                    ).format(sql.Identifier(h.schema, "ihr_audit_seal")),
                    [
                        now - timedelta(days=1),
                        now - timedelta(days=7),
                        now - timedelta(days=30),
                    ],
                )
                entries, d1, d7, d30 = (int(v) for v in cursor.fetchone())
                began = time.monotonic()
                incremental.unsealed(cursor, h.schema)
                fast = time.monotonic() - began
                walk = incremental.header_walk(cursor, h.schema)
                if full_run:
                    first, limit = 1, None
                else:
                    first = incremental.horizon_start(
                        cursor, h.schema, now - timedelta(days=options.get("days", 7))
                    )
                    first = walk.head_seq + 1 if first is None else first
                    limit = options.get("max_entries", 200_000)
                full = incremental.recompute(
                    cursor, h.schema, first, walk.head_seq, max_entries=limit
                )
                row = {
                    "slug": h.slug,
                    "entries": entries,
                    "seals": walk.seals,
                    "walk_seconds": round(walk.seconds, 4),
                    "recompute_seconds": round(full.seconds, 4),
                    "recompute_entries": full.entries,
                    "recompute_problems": len(full.problems),
                }
                out["per_hospital"].append(row)
                for key, value in (
                    ("entries", entries),
                    ("seals", walk.seals),
                    ("entries_1d", d1),
                    ("entries_7d", d7),
                    ("entries_30d", d30),
                    ("walk_seconds", walk.seconds),
                    ("recompute_seconds", full.seconds),
                    ("fast_seconds", fast),
                ):
                    totals[key] += value
        recomputed = sum(row["recompute_entries"] for row in out["per_hospital"])
        rate = (
            recomputed / totals["recompute_seconds"]
            if totals["recompute_seconds"]
            else 0
        )
        walk_rate = (
            totals["seals"] / totals["walk_seconds"] if totals["walk_seconds"] else 0
        )
        daily = totals["entries_7d"] / 7
        out["totals"] = {
            k: round(v, 4) if isinstance(v, float) else v for k, v in totals.items()
        }
        out["recompute_entries_per_second"] = round(rate)
        out["walk_seals_per_second"] = round(walk_rate)
        out["ingest_entries_per_day_7d"] = round(daily)
        cfg = watch.Config()
        out["rehash_horizon_entries_now"] = round(daily * cfg.rehash_days)
        out["rehash_budget"] = cfg.rehash_budget
        out["rehash_budget_minutes_at_measured_rate"] = (
            round(cfg.rehash_budget / rate / 60, 1) if rate else None
        )
        out["max_rss_mb"] = round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1
        )
        if options["json"]:
            self.stdout.write(json.dumps(out, indent=2, sort_keys=True))
        else:
            for key, value in out.items():
                if key != "per_hospital":
                    self.stdout.write(f"{key}: {value}")
