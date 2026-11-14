"""Platform-wide periodic jobs of ihr_platform (sent once, in ``public``: beat.PLATFORM_ONCE).

``report_unserved`` (P1-T18a review S3): every minute, the fixed line
``ihr-fleet: hospital=<slug> outcome=held`` for each live (active or suspended) hospital that
is not served: held for a release, or its schema not this build's. A fleet run writes that
line once, at its end; this keeps the HospitalNotServed alert (deploy/logs/alerts/fleet.yml)
firing for as long as the hospital stays down, so it never resolves while it still answers
503. Written straight to the worker's standard error (file descriptor 2), as one whole line:
the worker's log formatting would otherwise put a prefix in front, and the alert matches only
the whole line. A hospital held for a release window is left out while it waits, but not once
its step in the window failed (its newest upgrade record since the hold).

It also writes again, every minute, this worker's ``ihr-db-role: gated schema=<schema>
reason=<why>`` lines (P1-T19a, ihr_platform/db_roles.py): each hospital schema the worker found
a right beyond the model or a broken control in at its first connection, and so does not serve.
The DatabaseSchemaGated alert (deploy/logs/alerts/db-roles.yml) then lasts as long as the
gating, for an archived hospital's schema too, which HospitalNotServed never names.

The same job writes ``ihr-fleet: hospital=<slug> outcome=overrun`` every minute for each live
hospital held for a release window (``ihr_fleet_run hold``, P1-T18b) for longer than that
window's budget: the end of the announced window its hold began in, or
``WINDOW_BUDGET_MINUTES`` after the hold when no announced window covers it. The
ReleaseWindowOverrun alert (deploy/logs/alerts/fleet.yml) fires on it.
"""

import os

from celery import Celery, current_app, shared_task

from ihr_platform.beat import PLATFORM_ONCE

REPORT_EVERY_SECONDS = 60
# A window hold with no announced window around its start (a hold by hand): its budget
WINDOW_BUDGET_MINUTES = 120


def _emit(line):
    os.write(2, f"{line}\n".encode())


# The outcomes of a hospital's upgrade record (fleet.py Fleet._close and _close_interrupted) that
# mean its step went wrong: a step failed, was put off, or held it ("held": its role sync refused,
# or not served at the end), the run stopped before it ("stopped"), or the run was killed
# ("interrupted"). "crashed" is a child process's result, never a record's outcome.
FAILED_OUTCOMES = ("failed", "deferred", "held", "stopped", "interrupted")
# The only outcomes of a record since the hold that do not count as failed (P1-T18b review O6):
# finished, or still being worked on by the run in progress. Any other (one of the above, or an
# outcome added later, or none) counts as failed, so a hospital down in the window is never
# taken for one still waiting. A run killed half-way leaves "running": the window's budget
# alert (overrun_slugs) names it once the window is over.
NOT_FAILED_OUTCOMES = ("done", "running")


def failed_in_window():
    """Live hospitals held for a release window whose newest upgrade record since the hold
    did not finish and is not still running (P1-T18b review O6: FAILED_OUTCOMES, or any other
    outcome but NOT_FAILED_OUTCOMES): their window hold stays, but they are down, not waiting."""
    from ihr_platform import fleet, release
    from ihr_platform.models import Hospital, TenantOperation

    held = dict(
        Hospital.objects.exclude(schema_name="public")
        .filter(
            status__in=fleet.LIVE_STATUSES,
            schema_state__hold_reason=release.HOLD_WINDOW,
            schema_state__upgrading_since__isnull=False,
        )
        .values_list("pk", "schema_state__upgrading_since")
    )
    if not held:
        return set()
    newest = {}
    ops = (
        TenantOperation.objects.filter(
            kind=TenantOperation.Kind.UPGRADE,
            hospital_id__in=held,
            created_at__gte=min(held.values()),
        )
        .order_by("hospital_id", "-created_at")
        .values_list("hospital_id", "created_at", "result")
    )
    for pk, created, result in ops:
        if pk not in newest and created >= held[pk]:
            newest[pk] = (result or {}).get("outcome")
    return {pk for pk, outcome in newest.items() if outcome not in NOT_FAILED_OUTCOMES}


def unserved_slugs():
    """Live hospitals that are not served now, by slug."""
    from ihr_platform import fleet, release
    from ihr_platform.models import Hospital

    # A hospital held on purpose for a release window is not "down": the window's own budget
    # alert (P1-T18b) covers a window that overruns. Unless its step in the window failed: then
    # it is named every minute like any other unserved live hospital (O6), so HospitalNotServed
    # does not resolve while it is down.
    live = Hospital.objects.exclude(schema_name="public").filter(
        status__in=fleet.LIVE_STATUSES
    )
    failed = failed_in_window()
    waiting = live.filter(schema_state__hold_reason=release.HOLD_WINDOW).exclude(
        pk__in=failed
    )
    candidates = live.exclude(pk__in=waiting.values("pk"))
    ready = set(candidates.filter(release.ready_q()).values_list("pk", flat=True))
    return [
        slug
        for pk, slug in candidates.order_by("slug").values_list("pk", "slug")
        if pk not in ready
    ]


def _deadline(since, windows):
    """When a window hold that began at ``since`` outlasts its budget."""
    from datetime import timedelta

    for item in windows:
        if item.start <= since < item.end:
            return item.end
    return since + timedelta(minutes=WINDOW_BUDGET_MINUTES)


def overrun_slugs(now=None):
    """Live hospitals held for a release window past its budget, by slug (P1-T18b)."""
    from django.utils import timezone

    from ihr_platform import fleet, release
    from ihr_platform.models import Hospital, MaintenanceWindow

    now = now or timezone.now()
    windows = list(MaintenanceWindow.objects.filter(start__lte=now))
    held = (
        Hospital.objects.exclude(schema_name="public")
        .filter(
            status__in=fleet.LIVE_STATUSES,
            schema_state__hold_reason=release.HOLD_WINDOW,
            schema_state__upgrading_since__isnull=False,
        )
        .order_by("slug")
        .values_list("slug", "schema_state__upgrading_since")
    )
    return [slug for slug, since in held if now >= _deadline(since, windows)]


@shared_task(name="ihr_platform.report_unserved", ignore_result=True)
def report_unserved():
    from ihr_platform import db_roles, fleet

    for slug in unserved_slugs():
        if fleet.SLUG_RE.match(slug):
            _emit(fleet.log_line(slug, "held"))
    for line in db_roles.gated_lines():
        _emit(line)
    for slug in overrun_slugs():
        if fleet.SLUG_RE.match(slug):
            _emit(fleet.log_line(slug, "overrun"))
    # A parked schema (P1-T22b) holds personal data: kept past its 7 days it is named every
    # run until the owner's hospital-restore-expire drops it (ParkedSchemaOverdue)
    from ihr_platform.restore import engine

    for line in engine.overdue_lines():
        _emit(line)


@current_app.on_after_finalize.connect
def setup_periodic_tasks(sender: Celery, **kwargs):
    sender.add_periodic_task(
        REPORT_EVERY_SECONDS,
        report_unserved.s(),
        name="ihr_fleet_unserved",
        headers={PLATFORM_ONCE: True},
    )
