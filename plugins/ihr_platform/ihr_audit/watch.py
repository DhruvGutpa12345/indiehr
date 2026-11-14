"""The audit watch (P1-T21; docs/reviews/p1-t21/design.md, "After the attack" wins).

A resident service (``manage.py ihr_audit_watch serve``, the audit-watch container of
compose.monitoring.yaml) that checks every hospital's audit trail and serves what it found to
Prometheus. Its passes, each in its own thread so one stuck pass never freezes another (A9):

- **fast** (every 60 s): per hospital, the finished entries not sealed yet and the entries
  held back by a running transaction (one index range query each); and one catalogue query
  for any of the trail's triggers that is switched off, in every hospital schema (A10).
- **incremental** (every 15 min): the restore and offboarding events; the fingerprints since
  yesterday (day folders only); per hospital the header walk, the chain from its checkpoint,
  the fingerprints' claims, and trigger coverage for servable hospitals only (A7).
- **nightly** (04:00 IST): the rolling recompute of every seal of the last 30 days within a
  budget (A2), and once a week the full fingerprint sweep (**sweep**). Where the 30 days start
  is the watch's own record of each hospital's head (``heads`` in its state), never the
  database's own ``sealed_at`` alone, which the owner can back-date (review S3).

Fail closed (A9; review S1/C2): each hospital is checked on its own (its own transaction); one
that raises (a lock, a timeout, a revoked grant) is counted as **unchecked** for that pass
(``ihr_audit_hospitals_unchecked{pass}``, critical) and named by the fixed line with
``check=unchecked`` from its second failing pass on, and the others are still checked. The
switched-off-trigger count is published straight after its catalogue query. A pass's
``last_success`` time moves only when the pass ends without an error, and the staleness rules
read it.

Metrics carry no hospital label (privacy): counts and ages only, plus ``pass`` and ``class``.
A hospital is named only by one fixed line on standard error,

    ihr-audit: hospital=<slug> check=<chain|fingerprint|triggers|seal|unchecked> outcome=failed

which the log alerts AuditHospitalCheckFailed / AuditHospitalSealLate (deploy/logs/alerts/
audit.yml) accept from this service alone, and once per new restore or offboarding event

    ihr-audit: hospital=<slug> event=<restore|offboarding> outcome=recorded

(AuditEventRecorded: a human confirms each one). The slug comes from the database's own
hospital row, or the watch's own record of it (checked against its pattern; one that does not
match is named ``invalid-slug``, never dropped), never from text in the bucket (A5).

Seal lag (A6, A12): for each hospital the beat job or ``ihr_audit.seal_inactive`` seals, the
time since this process first saw finished entries waiting with the same newest seal.
Skipped (and counted): hospitals held for a restore, ACTIVE hospitals that are held or not on
this build's schema (beat skips them), and any hospital a release holds, for at most
HOLD_GRACE_SECONDS after the watch first saw the hold (the release has stopped the sealers and
its migrations may lock the trail: a check such a hospital cannot finish is deferred, not
unchecked; past the grace it is judged again). Suspended, offboarding and archived hospitals
are otherwise judged (``seal_inactive`` seals them whether held or not). A seal resets it, so a long
transaction that ends never reads as an hour of lag; that transaction shows as ``held back``
instead. At start, the first sighting is bounded by the database (the newer of the newest seal
and the oldest waiting entry), but never earlier than the sealers' one cycle before the watch
started (review C3: a release's migrations write entries the next seal_inactive run seals).

Everything it keeps (checkpoints, the rolling cursor, pass times) is in its own volume
(``IHR_AUDIT_WATCH_DIR``): the database owner cannot move it.
"""

import contextlib
import json
import logging
import os
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from django.db import connection, transaction

from ihr_audit import anchor, events, incremental
from ihr_audit.incremental import CheckpointStore

logger = logging.getLogger(__name__)

PASSES = ("fast", "incremental", "nightly", "sweep")
CHECKS = ("chain", "fingerprint", "triggers", "seal", "unchecked")
EVENT_KINDS = ("restore", "offboarding")
LINE_PREFIX = "ihr-audit:"
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{2,30}$")
# Named in a line instead of a slug that does not match SLUG_RE (S6), or of a hospital the
# watch has never seen (an event): both match the log alerts' slug pattern
INVALID_SLUG = "invalid-slug"
UNKNOWN_HOSPITAL = "unknown-hospital"
IST = ZoneInfo("Asia/Kolkata")
# A hospital is named for its seal lag from here (the warning's threshold, AuditSealLag)
SEAL_LINE_SECONDS = 900
# ihr_audit.seal_inactive's interval (tasks.py): after a start, a hospital's wait is dated no
# earlier than one such cycle (and a minute) before a line could come (review C3)
SEAL_INACTIVE_EVERY_SECONDS = 600
# A hospital failing this many passes in a row is named (check=unchecked)
UNCHECKED_LINE_AFTER = 2
ACKNOWLEDGED = Path(__file__).with_name("acknowledged_objects.txt")
# Statuses in which a hospital may be closed by an offboarding event (S4)
CLOSABLE = ("offboarding", "archived")
# A hospital held by a release (ihr_fleet_run: window, migrate, roles) is being migrated while
# the sealers are stopped: for this long after the watch first saw it held (or its hold began,
# if earlier) its seal lag is not judged and a check it cannot finish (a migration's lock) is
# deferred, not "unchecked". The window's own budget (ihr_platform.tasks
# WINDOW_BUDGET_MINUTES; a longer hold is ReleaseWindowOverrun's). Review C3 residual.
HOLD_GRACE_SECONDS = 120 * 60


def acknowledged(path=ACKNOWLEDGED):
    """Reviewed object keys whose store problems no longer count (A5): {key: reason}."""
    found = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, day, *reason = line.split()
        datetime.strptime(day, "%Y-%m-%d")  # noqa: DTZ007 (a review date, no time)
        if not reason or "*" in key or key.endswith("/"):
            msg = f"acknowledged_objects.txt: {line!r} needs a whole key, a date and a reason"
            raise ValueError(msg)
        found[key] = " ".join(reason)
    return found


def store_counts(fps, ack=None):
    """Store problems by class, without the acknowledged objects'."""
    ack = acknowledged() if ack is None else ack
    counts = dict.fromkeys(anchor.STORE_CLASSES, 0)
    for cls, key, _text in fps.items:
        if key is not None and key in ack:
            continue
        counts[cls] = counts.get(cls, 0) + 1
    return counts


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _default_root():
    return Path(os.environ.get("IHR_AUDIT_WATCH_DIR", "/var/lib/ihr-audit-watch"))


@dataclass
class Config:
    root: Path = field(default_factory=_default_root)
    port: int = _env_int("IHR_AUDIT_WATCH_PORT", 9108)
    fast_seconds: int = _env_int("IHR_AUDIT_WATCH_FAST_SECONDS", 60)
    incremental_seconds: int = _env_int("IHR_AUDIT_WATCH_INCREMENTAL_SECONDS", 900)
    nightly_at: str = os.environ.get("IHR_AUDIT_WATCH_NIGHTLY_AT", "04:00")  # IST
    sweep_days: int = _env_int("IHR_AUDIT_WATCH_SWEEP_DAYS", 7)
    # The rolling recompute (A2): every seal sealed in the last REHASH_DAYS, within a budget
    # of entries a night and a time limit. Sized in docs/reviews/p1-t21/design.md, As built.
    rehash_days: int = _env_int("IHR_AUDIT_WATCH_REHASH_DAYS", 30)
    # 160 million entries: 45 minutes at the measured 60,000 entries a second, the last 30
    # days of 100 busy hospitals (50,000 entries a day each); the time limit stops it first
    rehash_budget: int = _env_int("IHR_AUDIT_WATCH_REHASH_BUDGET", 160_000_000)
    rehash_max_seconds: int = _env_int("IHR_AUDIT_WATCH_REHASH_MAX_SECONDS", 2700)
    # Entries one hospital's check from its checkpoint recomputes in one 15-minute pass: a
    # first check of a long chain goes on over several passes (review C6), about 80 s at the
    # measured rate
    incremental_max_entries: int = _env_int(
        "IHR_AUDIT_WATCH_INCREMENTAL_MAX_ENTRIES", 5_000_000
    )
    # Who may read the metrics (S7): these names' addresses (re-resolved), and this container
    scrapers: str = os.environ.get("IHR_AUDIT_WATCH_SCRAPERS", "prometheus")


def log_line(slug, check):
    """The fixed line the log alerts match (deploy/logs/alerts/audit.yml)."""
    return f"ihr-audit: hospital={slug} check={check} outcome=failed"


def event_line(slug, kind):
    """The fixed line of a new restore or offboarding event (AuditEventRecorded)."""
    return f"ihr-audit: hospital={slug} event={kind} outcome=recorded"


def line_slug(slug):
    """A slug as a line may name it: the database's own when it matches its pattern, else
    the fixed INVALID_SLUG (never dropped: the hospital is still named, review S6)."""
    return slug if isinstance(slug, str) and SLUG_RE.match(slug) else INVALID_SLUG


def _emit(line):
    # One whole line straight to standard error: a logger would put a prefix in front
    os.write(2, f"{line}\n".encode())


def _now():
    return datetime.now(UTC)


def _ts(value):
    return value.timestamp() if value else None


# --- what the watch keeps between passes and restarts ----------------------------------------


class State:
    """Pass times, the rolling cursor and standing findings, in ``<root>/state.json``.
    ``save=False`` (``ihr_audit_watch once`` beside the serving watch): read, never written,
    so it can never overwrite the serving watch's state (review C12)."""

    def __init__(self, root, save=True):
        self.path = Path(root) / "state.json"
        self.lock = threading.Lock()
        self.saving = save
        self.data = {
            "passes": {},
            "fingerprint_newest": None,
            "rehash": None,  # the cycle in progress
            "rehash_complete": None,
            "rehash_problems": {},  # uuid -> [problem], until a cycle checks it again
            "sweep_problems": {},  # uuid -> [problem], until the next sweep
            "sweep_store": {},  # class -> count, from the last sweep
            # uuid -> [seq, hash, stored ISO]: the newest claim of every fingerprint ever read
            # (the sweep), to seed a checkpoint no recent fingerprint names (C6)
            "sweep_claims": {},
            # uuid -> {"generation": g, "days": {ISO date: head seq at its first pass that
            # day}}: the nightly horizon's own record, independent of sealed_at (S3)
            "heads": {},
            # uuid -> {"slug", "status"} as the database last said (S4: an offboarding event
            # closes only a hospital the watch saw offboarding or archived)
            "seen": {},
            "events_seen": [],  # event object keys already named (AuditEventRecorded)
            # event key -> [uuid, final seq, final hash]: offboarding events the watch accepted
            # while a recent fingerprint still named the hospital. A closed hospital drops out
            # of the hourly fingerprints a day or two later; the acceptance stands until a
            # check refuses it (review: no false AuditEventInvalid after a real offboarding)
            "offboarding_accepted": {},
            # uuid -> ISO time the watch first saw the hospital held (HOLD_GRACE_SECONDS: an
            # owner who re-dates upgrading_since cannot stretch the grace)
            "held_seen": {},
        }
        with contextlib.suppress(OSError, ValueError):
            self.data.update(json.loads(self.path.read_text()))

    def save(self):
        if not self.saving:
            return
        with self.lock:
            text = json.dumps(self.data, sort_keys=True)
        incremental._atomic(self.path, text)  # noqa: SLF001 (the module's own atomic write)


@dataclass
class Gauges:
    """The last values of every gauge, rendered for Prometheus."""

    values: dict = field(default_factory=dict)  # (name, labels tuple) -> value
    lock: threading.Lock = field(default_factory=threading.Lock)

    def set(self, name, value, **labels):
        if value is None:
            return
        with self.lock:
            self.values[(name, tuple(sorted(labels.items())))] = float(value)

    def render(self):
        with self.lock:
            items = sorted(self.values.items())
        lines, typed = [], set()
        for (name, labels), value in items:
            if name not in typed:
                lines.append(f"# TYPE {name} gauge")
                typed.add(name)
            label = ",".join(f'{k}="{v}"' for k, v in labels)
            text = str(int(value)) if value.is_integer() else repr(value)
            lines.append(f"{name}{{{label}}} {text}" if label else f"{name} {text}")
        return "\n".join(lines) + "\n"


# --- the watch ---------------------------------------------------------------------------------


@dataclass
class Hospital:
    uuid: str
    slug: str
    schema: str
    status: str
    ready: bool  # not held, and on this build's schema (release.schema_ready)
    hold: str = ""  # why it is held ("" when it is not)
    created_on: datetime | None = None
    held_since: datetime | None = (
        None  # when its hold began (owner-written: never alone)
    )


# A hospital being restored (P1-T22b's restore hold, HospitalSchemaState.restore_operation): its
# schema is parked and replaced, so nothing about it can be judged until the restore ends and
# writes its event
HOLD_RESTORE = "restore"


def _hospitals():
    """Every hospital but provisioning ones (a failed set-up is rolled back and deleted; never
    judged), with its schema state in the same query (release.schema_ready, for the fleet)."""
    from ihr_platform import db_roles, release
    from ihr_platform.models import Hospital as Model

    fingerprint, gated = release.code_fingerprint(), db_roles.gated_schemas()
    found = []
    rows = (
        Model.objects.exclude(schema_name="public")
        .exclude(status=Model.Status.PROVISIONING)
        .select_related("schema_state")
        .order_by("slug")
    )
    for h in rows:
        state = getattr(h, "schema_state", None)
        restoring = state is not None and state.restore_operation is not None
        ready = (
            state is not None
            and state.upgrading_since is None
            and not restoring
            and state.schema_fingerprint == fingerprint
            and h.schema_name not in gated
        )
        since = state.upgrading_since if state is not None else None
        hold = state.hold_reason if since else ""
        if restoring:  # the restore hold (P1-T22b): its own field, never a release's
            since, hold = state.restore_since, HOLD_RESTORE
        found.append(
            Hospital(
                str(h.uuid),
                h.slug,
                h.schema_name,
                h.status,
                ready,
                hold,
                h.created_on,
                since,
            )
        )
    return found


def _store():
    """The fingerprint reader (anchor-read.env), or None where none is set up (dev)."""
    try:
        return anchor.S3Store("READ", quick=False)
    except anchor.NotConfiguredError:
        return None


class Watch:
    def __init__(self, config=None, store=..., segments=None, save_state=True):
        self.config = config or Config()
        self.config.root.mkdir(parents=True, exist_ok=True)
        self.checkpoints = CheckpointStore(self.config.root).ensure()
        self.state = State(self.config.root, save=save_state)
        self.gauges = Gauges()
        self._store = store
        self.segments = segments
        self.seal_seen = {}  # uuid -> (head seq, first seen)
        self.unchecked = {}  # pass -> {uuid: passes failed in a row}
        self.lost_counted = set()  # uuids counted as lost by this process (C9)
        self.started = _now()
        self.stop = threading.Event()
        for name in PASSES:
            saved = self.state.data["passes"].get(name)
            if saved:
                self._pass_gauges(name, saved)
        newest = self.state.data.get("fingerprint_newest")
        self.gauges.set("ihr_audit_fingerprint_newest_timestamp_seconds", newest)
        self.gauges.set(
            "ihr_audit_rehash_cycle_complete_timestamp_seconds",
            self.state.data.get("rehash_complete"),
        )
        self.gauges.set(
            "ihr_audit_watch_checkpoint_store_created_timestamp_seconds",
            _ts(self.checkpoints.created()),
        )

    @property
    def store(self):
        if self._store is ...:
            self._store = _store()
        return self._store

    # --- bookkeeping ---------------------------------------------------------------------

    def _pass_gauges(self, name, saved):
        labels = {"pass": name}
        self.gauges.set(
            "ihr_audit_watch_last_run_timestamp_seconds", saved["at"], **labels
        )
        # Only a pass that ended without an error moves this one: the staleness and absent()
        # rules read it, so a pass that fails every time goes stale (A9; review C8)
        self.gauges.set(
            "ihr_audit_watch_last_success_timestamp_seconds",
            saved.get("ok_at"),
            **labels,
        )
        self.gauges.set("ihr_audit_watch_last_run_ok", saved["ok"], **labels)
        self.gauges.set(
            "ihr_audit_watch_last_run_duration_seconds", saved["seconds"], **labels
        )

    def run(self, name):
        """One pass, recorded whatever happens: its time, whether it ran without error and
        how long it took. Never raises."""
        began = time.monotonic()
        ok = 1
        try:
            summary = getattr(self, f"pass_{name}")()
        except Exception as exc:  # recorded, never fatal: AuditWatchFailing says it
            ok, summary = 0, {"error": type(exc).__name__}
            logger.exception("ihr-audit-watch: the %s pass failed", name)
        finally:
            with contextlib.suppress(Exception):
                connection.close()
        now = time.time()
        with self.state.lock:
            before = self.state.data["passes"].get(name) or {}
            saved = {
                "at": now,
                "ok": ok,
                "seconds": round(time.monotonic() - began, 3),
                # the last time it ended without error (an older state has "at" only)
                "ok_at": now if ok else before.get("ok_at"),
            }
            self.state.data["passes"][name] = saved
        self._pass_gauges(name, saved)
        with contextlib.suppress(OSError):
            self.state.save()
        logger.info(
            "ihr-audit-watch: %s pass %s in %.1f s %s",
            name,
            "ok" if ok else "FAILED",
            saved["seconds"],
            json.dumps(summary, sort_keys=True, default=str),
        )
        return {"pass": name, "ok": ok, **saved, **(summary or {})}

    def _lines(self, slugs, check):
        """One fixed line per hospital; a slug that does not match its pattern is named
        INVALID_SLUG, never dropped (S6)."""
        for slug in sorted({line_slug(s) for s in slugs}):
            _emit(log_line(slug, check))

    def _unchecked(self, name, hospitals, errors=None):
        """Hospitals this pass could not check (a lock, a timeout, a revoked grant): counted
        for the pass (critical), and named from their UNCHECKED_LINE_AFTER-th pass in a row
        (S1/C2: a hospital the watch cannot read is failing, never clean)."""
        before = self.unchecked.get(name, {})
        now = {h.uuid: before.get(h.uuid, 0) + 1 for h in hospitals}
        self.unchecked[name] = now
        self.gauges.set("ihr_audit_hospitals_unchecked", len(now), **{"pass": name})
        self._lines(
            [h.slug for h in hospitals if now[h.uuid] >= UNCHECKED_LINE_AFTER],
            "unchecked",
        )
        for h in hospitals:
            logger.error(
                "ihr-audit-watch: %r not checked by the %s pass (%s)",
                h.slug,
                name,
                (errors or {}).get(h.uuid, "error"),
            )

    def _held(self, hospitals, now):
        """uuids of hospitals held by a release within HOLD_GRACE_SECONDS (C3 residual): the
        sealers are stopped and a migration may lock their trail. The grace runs from the
        earlier of the hold's own start and the watch's first sighting of it (kept in its
        state, so a re-dated hold cannot stretch it); a restore hold is not one of these."""
        held = {}
        with self.state.lock:
            seen = self.state.data.setdefault("held_seen", {})
            for h in hospitals:
                if h.held_since is None or h.hold == HOLD_RESTORE:
                    seen.pop(h.uuid, None)
                    continue
                first = seen.setdefault(h.uuid, now.isoformat())
                try:
                    first = datetime.fromisoformat(first)
                except (TypeError, ValueError):
                    first = now
                    seen[h.uuid] = now.isoformat()
                began = min(h.held_since, first)
                if now - began < timedelta(seconds=HOLD_GRACE_SECONDS):
                    held[h.uuid] = began
        self.gauges.set("ihr_audit_hospitals_held", len(held))
        return held

    # --- fast --------------------------------------------------------------------------------

    def pass_fast(self):  # noqa: PLR0912, PLR0915 (one pass, read top to bottom)
        from ihr_platform.models import Hospital as Model

        now = _now()
        hospitals = _hospitals()
        g = self.gauges.set
        with connection.cursor() as cursor:
            disabled = incremental.disabled_triggers(cursor)
        # Published at once, before any per-hospital query can fail (A10; review S1)
        by_schema = {h.schema: h.slug for h in hospitals}
        g("ihr_audit_disabled_trigger_hospitals", len(disabled))
        self._lines([by_schema[s] for s in disabled if s in by_schema], "triggers")
        lag, held, skipped, late, failed, errors = 0.0, 0.0, 0, [], [], {}
        releasing = self._held(hospitals, now)
        # A start dates a wait no earlier than this (C3): the sealers get one cycle first
        floor = self.started - timedelta(
            seconds=SEAL_LINE_SECONDS - SEAL_INACTIVE_EVERY_SECONDS - 60
        )
        for h in hospitals:
            if h.hold == HOLD_RESTORE:
                skipped += 1
                self.seal_seen.pop(h.uuid, None)
                continue
            # Who seals it: beat for a live hospital on this build, not held;
            # ihr_audit.seal_inactive for suspended, offboarding and archived ones
            if h.status == Model.Status.ACTIVE and not h.ready:
                skipped += 1
                self.seal_seen.pop(h.uuid, None)
                continue
            # Held by a release (any status): its sealers are stopped and a migration may lock
            # its trail; judged again once the hold ends or outlasts its grace (C3 residual)
            if h.uuid in releasing:
                skipped += 1
                self.seal_seen.pop(h.uuid, None)
                continue
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    if not _has_trail(cursor, h.schema):
                        continue
                    u = incremental.unsealed(cursor, h.schema)
            except Exception as exc:  # this hospital is unchecked; the others go on
                failed.append(h)
                errors[h.uuid] = type(exc).__name__
                seen = self.seal_seen.get(h.uuid)
                if seen is not None:  # its wait keeps ageing: fail closed
                    age = (now - seen[1]).total_seconds()
                    lag = max(lag, age)
                    if age > SEAL_LINE_SECONDS:
                        late.append(h.slug)
                continue
            if u.sealable:
                seen = self.seal_seen.get(h.uuid)
                if seen is None or seen[0] != u.head_seq:
                    first = now
                    bounds = [d for d in (u.head_sealed_at, u.sealable_oldest) if d]
                    if (
                        seen is None
                        and bounds
                        and now - self.started < timedelta(minutes=2)
                    ):
                        # just started: the database bounds how long it has waited
                        first = max(*bounds, floor)
                    seen = self.seal_seen[h.uuid] = (u.head_seq, first)
                age = (now - seen[1]).total_seconds()
                lag = max(lag, age)
                if age > SEAL_LINE_SECONDS:
                    late.append(h.slug)
            else:
                self.seal_seen.pop(h.uuid, None)
            if u.held_back and u.held_back_oldest:
                held = max(held, (now - u.held_back_oldest).total_seconds())
        g("ihr_audit_seal_lag_seconds", round(lag, 1))
        g("ihr_audit_seal_held_back_seconds", round(held, 1))
        g("ihr_audit_hospitals_seal_skipped", skipped)
        self._unchecked("fast", failed, errors)
        self._lines(late, "seal")
        return {
            "hospitals": len(hospitals),
            "seal_lag_seconds": round(lag, 1),
            "held_back_seconds": round(held, 1),
            "seal_skipped": skipped,
            "disabled_trigger_hospitals": len(disabled),
            "unchecked": len(failed),
        }

    # --- incremental -------------------------------------------------------------------------

    def _events(self):
        store = self.store
        if store is None:
            return events.Events(read_ok=False), "not configured"
        try:
            return events.read(store, self.segments), ""
        except Exception as exc:  # unreadable: nothing is excused
            return events.Events(read_ok=False), type(exc).__name__

    def _fingerprints(self, found_events, since):
        """(fingerprints or None, why not)."""
        store = self.store
        if store is None:
            return None, "not configured"
        try:
            return (
                anchor.load_all(store, since=since, excuse=found_events.excuse),
                "",
            )
        except Exception as exc:  # the store is unreadable
            return None, type(exc).__name__

    def _claim(self, hospital_uuid, fps):
        """The newest fingerprint claim (seq, hash, stored) of a hospital: from the recent
        listing, else from the last sweep's (C6), else None."""
        if fps is not None and hospital_uuid in fps.newest_claims:
            return fps.newest_claims[hospital_uuid]
        kept = self.state.data.get("sweep_claims", {}).get(hospital_uuid)
        if kept:
            with contextlib.suppress(ValueError, TypeError):
                return int(kept[0]), str(kept[1]), datetime.fromisoformat(kept[2])
        return None

    def _event_claim(self, u, event, fps, accepted, listings):
        """(the claim to compare an offboarding event's ``final`` with, where it came from).

        - "recent": the newest claim of the fingerprints since yesterday;
        - "accepted": none recent, but the watch accepted this event before, while one was
          (a closed hospital drops out of the hourly fingerprints; review: no week of false
          AuditEventInvalid after a real offboarding);
        - "listed": none recent and never accepted (the watch was down while it was): the
          fingerprints since the day before the event, read once per pass, for an event
          younger than a sweep cycle;
        - "sweep": the last sweep's claim (C6), or None."""
        if fps is not None and u in fps.newest_claims:
            return fps.newest_claims[u], "recent"
        if event.key in accepted:
            return self._claim(u, None), "accepted"
        young = _now() - event.stored < timedelta(days=self.config.sweep_days + 1)
        if young and self.store is not None:
            since = (event.stored - timedelta(days=1)).date()
            if since not in listings:
                try:
                    listings[since] = anchor.load_all(self.store, since=since)
                except Exception:  # unreadable now: the sweep's claim decides
                    listings[since] = None
            listed = listings[since]
            if listed is not None and u in listed.newest_claims:
                return listed.newest_claims[u], "listed"
        return self._claim(u, None), "sweep"

    def judge_offboarding(self, found_events, fps, every):
        """(uuids an offboarding event may close, problems). An event closes its hospital
        only when (S4): the database no longer lists it as anything but offboarding or
        archived; the watch saw it offboarding or archived; its ``final`` is the newest
        fingerprint claim of it (or, once no recent fingerprint names the closed hospital,
        the watch accepted it while one did, and no newer claim is known); and it does not
        contradict the watch's checkpoint. Or its object key is in acknowledged_objects.txt
        (a human confirmed it). Any other event is a bad event (class events): the hospital
        stays unknown and keeps its checkpoint, and an earlier acceptance is withdrawn."""
        ack = acknowledged()
        seen = self.state.data.get("seen", {})
        with self.state.lock:
            accepted = dict(self.state.data.get("offboarding_accepted") or {})
        closed, problems, listings = set(), [], {}
        for u, event in sorted(found_events.offboarded.items()):
            if event.key in ack:
                closed.add(u)
                continue
            why = []
            status = every[u][1] if u in every else (seen.get(u) or {}).get("status")
            if status not in CLOSABLE:
                why.append(
                    "the watch never saw it offboarding or archived"
                    if u not in every
                    else f"it is still {status} in the database"
                )
            claim, source = self._event_claim(u, event, fps, accepted, listings)
            if source == "accepted":
                if claim is not None and claim[0] > event.final[0]:
                    why.append(
                        f"a fingerprint names seal {claim[0]}, after its final seal "
                        f"{event.final[0]}"
                    )
            elif claim is None:
                why.append("no fingerprint of it to compare its final seal with")
            elif (claim[0], claim[1]) != tuple(event.final):
                why.append(
                    f"its final seal {event.final[0]} is not the newest fingerprint's "
                    f"(seal {claim[0]})"
                )
            cp = self.checkpoints.get(u)
            if cp is not None and (
                cp.seq > event.final[0]
                or (cp.seq == event.final[0] and cp.hash != event.final[1])
            ):
                why.append(
                    f"its final seal {event.final[0]} contradicts the watch's checkpoint "
                    f"(seal {cp.seq})"
                )
            if why:
                accepted.pop(event.key, None)
                problems.append(
                    f"event {event.key}: offboarding refused: {'; '.join(why)}"
                )
            else:
                closed.add(u)
                accepted[event.key] = [u, event.final[0], event.final[1]]
        with self.state.lock:
            # only events still in the bucket (write-once: one that vanished is a store
            # problem of its own, never an acceptance kept for it)
            self.state.data["offboarding_accepted"] = {
                k: v
                for k, v in accepted.items()
                if k in {e.key for e in found_events.offboarded.values()}
                or not found_events.read_ok
            }
        return closed, problems

    def _new_events(self, found_events, slugs):
        """Name each restore or offboarding event once (AuditEventRecorded): a human confirms
        it was theirs (S4). The slug is the database's, or the watch's own record of it."""
        seen = self.state.data.get("seen", {})
        with self.state.lock:
            done = set(self.state.data.get("events_seen", []))
        new = []
        every = [e for evs in found_events.restores.values() for e in evs]
        every += list(found_events.offboarded.values())
        for event in sorted(every, key=lambda e: e.key):
            if event.key in done:
                continue
            slug = slugs.get(event.hospital) or (seen.get(event.hospital) or {}).get(
                "slug", UNKNOWN_HOSPITAL
            )
            _emit(event_line(line_slug(slug), event.kind))
            new.append(event.key)
        if new:
            with self.state.lock:
                self.state.data["events_seen"] = sorted(done | set(new))
        return len(new)

    def _record_head(self, hospital_uuid, generation, seq, today):
        """The nightly horizon's own record (S3): the head at the first pass of each day.
        Never reset: a new restore generation (or a forged restore event) keeps the days
        recorded before it. They only mark where each day began, and the nightly starts at
        the lowest seal any of them gives, so an older record can only widen it."""
        with self.state.lock:
            heads = self.state.data.setdefault("heads", {})
            mine = heads.get(hospital_uuid)
            if not mine or not isinstance(mine.get("days"), dict):
                mine = heads[hospital_uuid] = {"generation": generation, "days": {}}
            mine["generation"] = generation
            mine["days"].setdefault(today, seq)
            keep = (
                (
                    datetime.fromisoformat(today)
                    - timedelta(days=self.config.rehash_days + 3)
                )
                .date()
                .isoformat()
            )
            mine["days"] = {d: v for d, v in mine["days"].items() if d >= keep}

    def _trusted_first(self, hospital_uuid, since):
        """(the first seal sealed after ``since`` by the watch's own record, how far the
        record reaches: "full", "partial" or "none")."""
        mine = self.state.data.get("heads", {}).get(hospital_uuid) or {}
        days = mine.get("days") or {}
        if not days:
            return None, "none"
        before = [d for d in days if d <= since.date().isoformat()]
        if before:
            return int(days[max(before)]) + 1, "full"
        return int(days[min(days)]) + 1, "partial"

    def _should_have_checkpoint(self, h):
        """A hospital the watch's store should already know: made before the store (C9: a
        hospital activated since is seeded for the first time, not lost)."""
        created = self.checkpoints.created()
        if created is None or h.created_on is None:
            return True
        return h.created_on < created - timedelta(
            seconds=self.config.incremental_seconds
        )

    def pass_incremental(self):  # noqa: PLR0912, PLR0915 (one pass, read top to bottom)
        from ihr_platform.models import Hospital as Model

        from ihr_audit.install import coverage

        cfg = self.config
        found_events, events_error = self._events()
        since = (_now() - timedelta(days=1)).date()
        fps, fps_error = self._fingerprints(found_events, since)
        hospitals = _hospitals()
        every = _every_hospital()  # uuid -> (slug, status), provisioning ones included
        slugs = {u: slug for u, (slug, _status) in every.items()}
        closed, refused = self.judge_offboarding(found_events, fps, every)
        broken, mismatch, triggers, failed, errors = {}, {}, {}, [], {}
        not_comparable, lost, unseeded, checked, closed_n, restoring = 0, 0, 0, 0, 0, 0
        rehash = self.state.data.get("rehash_problems", {})
        sweep = self.state.data.get("sweep_problems", {})
        # A hospital with no checkpoint is seeded only when the fingerprints could be read,
        # or where none are set up (dev); never from the genesis because the store is down
        readable = fps is not None and found_events.read_ok
        seed_ok = readable or self.store is None
        today = _now().date().isoformat()
        releasing, deferred = self._held(hospitals, _now()), 0
        for h in hospitals:
            if h.hold == HOLD_RESTORE:
                # Its schema is being replaced: the chain is judged again when the hold ends.
                # What was found before the hold still stands, and the hold itself alerts
                # (AuditHospitalsRestoring, review S2)
                restoring += 1
                if problems := rehash.get(h.uuid, []):
                    broken[h.uuid] = problems
                if fp := sweep.get(h.uuid, []):
                    mismatch[h.uuid] = fp
                continue
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    if not _has_trail(cursor, h.schema):
                        if h.uuid in closed:
                            closed_n += (
                                1  # offboarded on purpose and its schema is gone
                            )
                        else:
                            broken[h.uuid] = ["no audit trail tables"]
                        continue
                    checked += 1
                    result = incremental.check_from_checkpoint(
                        cursor,
                        h.schema,
                        h.uuid,
                        self.checkpoints.get(h.uuid),
                        self._claim(h.uuid, fps),
                        found_events,
                        seed_ok=seed_ok,
                        max_entries=cfg.incremental_max_entries,
                    )
                    if result.checkpoint is not None:
                        self.checkpoints.put(result.checkpoint)
                    if result.walk is not None and (
                        found_events.read_ok or self.store is None
                    ):
                        # never from a failed events read: its generation 0 is not news
                        self._record_head(
                            h.uuid,
                            found_events.generation(h.uuid),
                            result.walk.head_seq,
                            today,
                        )
                    unseeded += result.unseeded
                    if (
                        result.lost
                        and h.uuid not in self.lost_counted
                        and self._should_have_checkpoint(h)
                    ):
                        lost += 1
                        self.lost_counted.add(h.uuid)
                    problems = result.problems + rehash.get(h.uuid, [])
                    if problems:
                        broken[h.uuid] = problems
                    fp = (anchor.check(h.uuid, fps) if fps else []) + sweep.get(
                        h.uuid, []
                    )
                    if fp:
                        mismatch[h.uuid] = fp
                    if h.status == Model.Status.ACTIVE and h.ready:
                        if trig := coverage(connection, h.schema):
                            triggers[h.uuid] = trig
                    elif h.status == Model.Status.ACTIVE:
                        not_comparable += 1
            except Exception as exc:  # this hospital is unchecked; the others go on
                if h.uuid in releasing:  # a release's migration holds it (C3 residual)
                    deferred += 1
                    logger.warning(
                        "ihr-audit-watch: %r held by a release, check deferred (%s)",
                        h.slug,
                        type(exc).__name__,
                    )
                    continue
                failed.append(h)
                errors[h.uuid] = type(exc).__name__
        # Hospitals the watch judged before whose row now says provisioning: a row the owner
        # can write must not take a hospital out of every check (S2)
        judged_before = set(self.checkpoints.hospitals()) | set(
            fps.slugs if fps else {}
        )
        judged_before |= set(self.state.data.get("sweep_claims", {}))
        for u, (_slug, status) in every.items():
            if status == Model.Status.PROVISIONING and u in judged_before - closed:
                broken[u] = [
                    "it was checked before, but its row now says provisioning: no longer "
                    "judged (status changed by hand?)"
                ]
        # What the database said of each hospital, kept for judging offboarding events
        with self.state.lock:
            seen = self.state.data.setdefault("seen", {})
            for u, (slug, status) in every.items():
                seen[u] = {"slug": slug, "status": status}
        # Checkpoints of hospitals the database no longer has: dropped only when an accepted
        # offboarding event closes them; otherwise they are unknown hospitals
        gone = set(self.checkpoints.hospitals()) - set(every)
        for u in gone & closed:
            self.checkpoints.drop(
                u
            )  # offboarded on purpose (an accepted event says so)
        unknown = set()
        store_classes = dict.fromkeys(anchor.STORE_CLASSES, 0)
        store_classes["events"] = len(found_events.problems) + len(refused)
        if fps is not None:
            for cls, n in store_counts(fps).items():
                store_classes[cls] = n
            unknown = {u for u in fps.slugs if u not in every and u not in closed}
            for cls, n in self.state.data.get("sweep_store", {}).items():
                store_classes[cls] = max(store_classes.get(cls, 0), n)
            if fps.newest:
                with self.state.lock:
                    old = self.state.data.get("fingerprint_newest") or 0
                    self.state.data["fingerprint_newest"] = max(
                        old, fps.newest.timestamp()
                    )
        # A checkpoint of a gone hospital no recent fingerprint names is unknown too
        unknown |= gone - closed
        store_classes["unknown_hospital"] = max(
            store_classes["unknown_hospital"], len(unknown)
        )
        new_events = self._new_events(found_events, slugs)
        g = self.gauges.set
        g("ihr_audit_hospitals_checked", checked)
        g("ihr_audit_hospitals_not_comparable", not_comparable)
        g("ihr_audit_hospitals_restoring", restoring)
        g("ihr_audit_hospitals_closed", closed_n)
        g("ihr_audit_chain_broken_hospitals", len(broken))
        g("ihr_audit_fingerprint_mismatch_hospitals", len(mismatch))
        g("ihr_audit_trigger_problem_hospitals", len(triggers))
        g("ihr_audit_checkpoints_reseeded_hospitals", lost)
        g("ihr_audit_checkpoints_unseeded_hospitals", unseeded)
        g("ihr_audit_fingerprints_readable", 1 if readable else 0)
        if fps is not None:
            g("ihr_audit_fingerprint_bucket_ok", 0 if fps.classes.get("lock") else 1)
            for cls, n in store_classes.items():
                g("ihr_audit_fingerprint_store_problems", n, **{"class": cls})
        g(
            "ihr_audit_fingerprint_newest_timestamp_seconds",
            self.state.data.get("fingerprint_newest"),
        )
        g(
            "ihr_audit_restore_events",
            sum(len(v) for v in found_events.restores.values()),
        )
        g("ihr_audit_offboarding_events", len(found_events.offboarded))
        self._unchecked("incremental", failed, errors)
        self._lines([slugs[u] for u in broken], "chain")
        self._lines([slugs[u] for u in mismatch], "fingerprint")
        self._lines([slugs[u] for u in triggers], "triggers")
        for what, found in (
            ("chain", broken),
            ("fingerprint", mismatch),
            ("triggers", triggers),
        ):
            for u, problems in sorted(found.items()):
                for text in problems[:5]:
                    logger.error("ihr-audit-watch: %r %s: %s", slugs[u], what, text)
        for text in refused:
            logger.error("ihr-audit-watch: %s", events.shown(text, 600))
        return {
            "hospitals": len(hospitals),
            "checked": checked,
            "unchecked": len(failed),
            "deferred": deferred,
            "chain_broken": len(broken),
            "fingerprint_mismatch": len(mismatch),
            "trigger_problems": len(triggers),
            "not_comparable": not_comparable,
            "restoring": restoring,
            "closed": closed_n,
            "reseeded": lost,
            "unseeded": unseeded,
            "new_events": new_events,
            # nightly and sweep findings this pass shows again (they stand until the next
            # nightly re-checks them; C12)
            "standing_findings": len(set(rehash) | set(sweep)),
            "fingerprints_read": fps.count if fps else 0,
            "fingerprints_error": fps_error,
            "events_error": events_error,
            "store_problems": {k: v for k, v in store_classes.items() if v},
        }

    # --- nightly -----------------------------------------------------------------------------

    def pass_nightly(self):
        summary = {"rehash": self._rehash()}
        if self._sweep_due():
            summary["sweep"] = self.run("sweep")
        return summary

    def _sweep_due(self):
        """Due when no sweep has ended without error for a week (C8: a failed sweep is tried
        again at the next nightly, not a week later)."""
        last = self.state.data["passes"].get("sweep") or {}
        ok_at = last.get("ok_at")
        return not ok_at or time.time() - ok_at > self.config.sweep_days * 86400 - 3600

    def _rehash(self):  # noqa: PLR0912, PLR0915 (one cycle, read top to bottom)
        """The rolling recompute (A2): a cycle covers every seal sealed in the last
        ``rehash_days`` of every hospital; a night does as much as the budget allows, and the
        next continues the same cycle. A cycle complete within a night is the promise:
        AuditRehashOverdue fires when one is not complete for two nights.

        Where a hospital's cycle starts (S3): the lower of the database's first seal sealed
        since then (``sealed_at``, which the owner could back-date) and the watch's own record
        of the head then (``heads``). A back-dated ``sealed_at`` can only widen it."""
        cfg = self.config
        began = time.monotonic()
        hospitals = {h.uuid: h for h in _hospitals()}
        releasing = self._held(hospitals.values(), _now())
        with self.state.lock:
            cycle = self.state.data.get("rehash")
        failed, errors = [], {}
        if not cycle:
            since = _now() - timedelta(days=cfg.rehash_days)
            cycle = {
                "started": time.time(),
                "since": since.isoformat(),
                "todo": {},
                "found": {},
                "trusted": {"full": 0, "partial": 0, "none": 0},
            }
            total = 0
            for u, h in hospitals.items():
                try:
                    with transaction.atomic(), connection.cursor() as cursor:
                        if not _has_trail(cursor, h.schema):
                            continue
                        by_db = incremental.horizon_start(cursor, h.schema, since)
                        by_watch, reach = self._trusted_first(u, since)
                        head = incremental.unsealed(cursor, h.schema).head_seq
                        firsts = [
                            f for f in (by_db, by_watch) if f is not None and f <= head
                        ]
                        cycle["trusted"][reach] += 1
                        if firsts:
                            first = min(firsts)
                            cycle["todo"][u] = [first, head]
                            total += incremental.entries_between(
                                cursor, h.schema, first, head
                            )
                except Exception as exc:  # counted; the others go on
                    failed.append(h)
                    errors[u] = type(exc).__name__
            cycle["entries"] = total
        budget, used, stopped = cfg.rehash_budget, 0, 0
        for u in sorted(cycle["todo"]):
            first, last = cycle["todo"][u]
            h = hospitals.get(u)
            if used >= budget:
                stopped = 1
                break
            if time.monotonic() - began > cfg.rehash_max_seconds:
                stopped = 2
                break
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    if h is None or not _has_trail(cursor, h.schema):
                        cycle["todo"].pop(u)
                        continue
                    done = incremental.recompute(
                        cursor,
                        h.schema,
                        first,
                        last,
                        budget - used,
                        deadline=began + cfg.rehash_max_seconds,
                    )
            except Exception as exc:  # kept in the cycle for the next night
                failed.append(h)
                errors[u] = type(exc).__name__
                continue
            used += done.entries
            if done.problems:
                cycle["found"].setdefault(u, []).extend(done.problems[:20])
                with self.state.lock:
                    self.state.data["rehash_problems"][u] = cycle["found"][u]
            if done.last_seq >= last:
                cycle["todo"].pop(u)
            else:
                cycle["todo"][u] = [done.last_seq + 1, last]
        complete = not cycle["todo"]
        if not complete and not stopped:
            stopped = 1 if used >= budget else 2
        if complete:
            stopped = 0
        with self.state.lock:
            if complete:
                self.state.data["rehash"] = None
                self.state.data["rehash_complete"] = time.time()
                self.state.data["rehash_problems"] = cycle["found"]
            else:
                self.state.data["rehash"] = cycle
        seconds = time.monotonic() - began
        rate = round(used / seconds) if seconds else None
        g = self.gauges.set
        g("ihr_audit_rehash_horizon_entries", cycle.get("entries", 0))
        g("ihr_audit_rehash_budget_entries", budget)
        g("ihr_audit_rehash_entries_last_night", used)
        # Which limit ended the night (C5): 0 the cycle completed, 1 the entry budget,
        # 2 the time limit (IHR_AUDIT_WATCH_REHASH_MAX_SECONDS)
        g("ihr_audit_rehash_stopped_by", stopped)
        g("ihr_audit_rehash_rate_entries_per_second", rate)
        g(
            "ihr_audit_rehash_cycle_complete_timestamp_seconds",
            self.state.data.get("rehash_complete"),
        )
        # A hospital a release holds is tried again the next night, not counted (C3 residual)
        self._unchecked(
            "nightly",
            [h for h in failed if h is not None and h.uuid not in releasing],
            errors,
        )
        return {
            "complete": complete,
            "entries": used,
            "horizon_entries": cycle.get("entries", 0),
            "seconds": round(seconds, 2),
            "rate_per_second": rate,
            "stopped_by": ("complete", "budget", "time limit")[stopped],
            "horizon_by_watch_record": cycle.get("trusted"),
            "hospitals_with_problems": len(cycle["found"]),
            "unchecked": len(failed),
        }

    def pass_sweep(self):
        """Every fingerprint ever written, once a week (anchor.load_all, all day folders)."""
        from ihr_platform.models import Hospital as Model

        found_events, _ = self._events()
        fps, error = self._fingerprints(found_events, None)
        if fps is None:
            msg = f"the fingerprints are unreadable ({error})"
            raise RuntimeError(msg)
        every = _every_hospital()
        closed, refused = self.judge_offboarding(found_events, fps, every)
        problems = {}
        with connection.cursor() as cursor:
            for h in _hospitals():
                if h.uuid in closed and not _has_trail(cursor, h.schema):
                    continue  # offboarded on purpose and its schema is gone (C14)
                if p := anchor.check(h.uuid, fps):
                    problems[h.uuid] = p
        for u, (_slug, status) in every.items():
            if (
                status == Model.Status.PROVISIONING
                and u in fps.slugs
                and u not in closed
            ):
                problems[u] = [
                    "fingerprinted before, but its row now says provisioning (S2)"
                ]
        with self.state.lock:
            self.state.data["sweep_problems"] = problems
            self.state.data["sweep_claims"] = {
                u: [seq, digest, stored.isoformat()]
                for u, (seq, digest, stored) in fps.newest_claims.items()
            }
            self.state.data["sweep_store"] = {
                **store_counts(fps),
                "unknown_hospital": len(
                    anchor.unknown_hospitals(fps, every_slugs(every), closed)
                ),
                "events": len(found_events.problems) + len(refused),
            }
        return {
            "fingerprints_read": fps.count,
            "hospitals_with_problems": len(problems),
        }

    # --- serving -----------------------------------------------------------------------------

    def _loop(self, name, every):
        while not self.stop.is_set():
            began = time.monotonic()
            self.run(name)
            self.stop.wait(max(1.0, every - (time.monotonic() - began)))

    def _seconds_to_nightly(self):
        hour, minute = (int(x) for x in self.config.nightly_at.split(":"))
        now = datetime.now(IST)
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at <= now:
            at += timedelta(days=1)
        return (at - now).total_seconds()

    def _nightly_wait(self):
        """Seconds until the next nightly: an hour after a nightly that failed (C8), soon
        after a start that missed a night, else the next 04:00 IST."""
        last = self.state.data["passes"].get("nightly") or {}
        if last and not last.get("ok"):
            return max(120.0, 3600 - (time.time() - last["at"]))
        ok_at = last.get("ok_at", last.get("at"))
        if not ok_at or time.time() - ok_at > 24 * 3600:
            return 120.0
        return self._seconds_to_nightly()

    def _nightly_loop(self):
        self.stop.wait(self._nightly_wait())
        while not self.stop.is_set():
            self.run("nightly")
            self.stop.wait(self._nightly_wait())

    def serve(self):
        server = metrics_server(
            self.gauges, self.config.port, scrapers=self.config.scrapers.split(",")
        )
        threads = [
            threading.Thread(target=server.serve_forever, name="metrics", daemon=True),
            threading.Thread(
                target=self._loop, args=("fast", self.config.fast_seconds), daemon=True
            ),
            threading.Thread(
                target=self._loop,
                args=("incremental", self.config.incremental_seconds),
                daemon=True,
            ),
            threading.Thread(target=self._nightly_loop, daemon=True),
        ]
        for t in threads:
            t.start()
        logger.info("ihr-audit-watch: serving metrics on :%s", self.config.port)
        try:
            while not self.stop.wait(5):
                if not all(t.is_alive() for t in threads):
                    logger.error("ihr-audit-watch: a pass thread died; exiting")
                    break
        finally:
            self.stop.set()
            server.shutdown()


def _has_trail(cursor, schema):
    from ihr_audit.install import has_trail

    return has_trail(cursor, schema)


def _every_hospital():
    """{uuid: (slug, status)} of every hospital row, provisioning ones included."""
    from ihr_platform.models import Hospital as Model

    return {
        str(u): (slug, status)
        for u, slug, status in Model.objects.exclude(schema_name="public").values_list(
            "uuid", "slug", "status"
        )
    }


def every_slugs(every):
    return {u: slug for u, (slug, _status) in every.items()}


def _all_hospitals():
    return every_slugs(_every_hospital())


# --- the metrics endpoint ------------------------------------------------------------------------

# At most this many requests are served at once; more are closed at once (S7)
MAX_REQUESTS = 4
# A whole request (its line, headers and answer) gets this long, however it trickles
REQUEST_SECONDS = 10
# How often the scrapers' names are resolved again (their addresses are Docker's to choose)
RESOLVE_SECONDS = 60
# A refused address is resolved again at most this often
RESOLVE_AGAIN_SECONDS = 5


class _Handler(BaseHTTPRequestHandler):
    timeout = 5  # a slow client is cut off; scrapes never queue behind it (A1)
    server_version = "ihr-audit-watch"
    sys_version = ""
    gauges = None

    def setup(self):
        super().setup()
        # The whole request is bounded, not only each read: a client that sends a byte every
        # few seconds is cut off after REQUEST_SECONDS (S7)
        self._deadline = threading.Timer(REQUEST_SECONDS, self._cut)
        self._deadline.daemon = True
        self._deadline.start()

    def _cut(self):
        with contextlib.suppress(OSError):
            self.connection.shutdown(socket.SHUT_RDWR)

    def finish(self):
        self._deadline.cancel()
        with contextlib.suppress(OSError):
            super().finish()

    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        body = self.gauges.render().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # no access log: Prometheus every 30 s
        pass


class _Server(ThreadingHTTPServer):
    """Threaded, but bounded (S7): only the scrapers' addresses and this container's own are
    answered (checked in the accept loop, before any thread), and at most MAX_REQUESTS at
    once; anything else is closed at once."""

    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, handler, scrapers=(), allow=None):
        super().__init__(address, handler)
        self.scrapers = [s.strip() for s in scrapers if s.strip()]
        self.allow_extra = set(allow or ())
        self.slots = threading.BoundedSemaphore(MAX_REQUESTS)
        self._allowed, self._resolved = set(), 0.0
        self._resolve_lock = threading.Lock()

    def _resolve(self, force=False):
        with self._resolve_lock:
            if not force and time.monotonic() - self._resolved < RESOLVE_SECONDS:
                return self._allowed
            found = set()
            for name in self.scrapers:
                with contextlib.suppress(OSError):
                    found |= {info[4][0] for info in socket.getaddrinfo(name, None)}
            self._allowed, self._resolved = found, time.monotonic()
            return found

    def allowed(self, address):
        if address in ("127.0.0.1", "::1") or address in self.allow_extra:
            return True
        if address in self._resolve():
            return True
        # Its address may have changed (a recreated Prometheus): once more, freshly resolved
        return (
            time.monotonic() - self._resolved > RESOLVE_AGAIN_SECONDS
            and address in self._resolve(force=True)
        )

    def verify_request(self, request, client_address):
        return self.allowed(client_address[0])

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)  # too many at once: closed, no thread
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def handle_error(self, request, client_address):
        # A client cut off at its deadline or gone mid-answer: expected, never a traceback
        logger.debug(
            "ihr-audit-watch: a metrics request from %s ended early", client_address[0]
        )

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def metrics_server(gauges, port, host="0.0.0.0", scrapers=(), allow=None):  # noqa: S104 (the container's own address)
    handler = type("Handler", (_Handler,), {"gauges": gauges})
    return _Server((host, port), handler, scrapers=scrapers, allow=allow)
