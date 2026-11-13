"""Platform-wide periodic jobs of ihr_platform (sent once, in ``public``: beat.PLATFORM_ONCE).

``report_unserved`` (P1-T18a review S3): every minute, the fixed line
``ihr-fleet: hospital=<slug> outcome=held`` for each live (active or suspended) hospital that
is not served: held for a release, or its schema not this build's. A fleet run writes that
line once, at its end; this keeps the HospitalNotServed alert (deploy/logs/alerts/fleet.yml)
firing for as long as the hospital stays down, so it never resolves while it still answers
503. Written straight to the worker's standard error (file descriptor 2), as one whole line:
the worker's log formatting would otherwise put a prefix in front, and the alert matches only
the whole line.
"""

import os

from celery import Celery, current_app, shared_task

from ihr_platform.beat import PLATFORM_ONCE

REPORT_EVERY_SECONDS = 60


def _emit(line):
    os.write(2, f"{line}\n".encode())


def unserved_slugs():
    """Live hospitals that are not served now, by slug."""
    from ihr_platform import fleet, release
    from ihr_platform.models import Hospital

    # A hospital held on purpose for a release window is not "down": the window's own budget
    # alert (P1-T18b) covers a window that overruns. Every other unserved live hospital is
    # named every minute.
    live = (
        Hospital.objects.exclude(schema_name="public")
        .filter(status__in=fleet.LIVE_STATUSES)
        .exclude(schema_state__hold_reason=release.HOLD_WINDOW)
    )
    ready = set(live.filter(release.ready_q()).values_list("pk", flat=True))
    return [
        slug
        for pk, slug in live.order_by("slug").values_list("pk", "slug")
        if pk not in ready
    ]


@shared_task(name="ihr_platform.report_unserved", ignore_result=True)
def report_unserved():
    from ihr_platform import fleet

    for slug in unserved_slugs():
        if fleet.SLUG_RE.match(slug):
            _emit(fleet.log_line(slug, "held"))


@current_app.on_after_finalize.connect
def setup_periodic_tasks(sender: Celery, **kwargs):
    sender.add_periodic_task(
        REPORT_EVERY_SECONDS,
        report_unserved.s(),
        name="ihr_fleet_unserved",
        headers={PLATFORM_ONCE: True},
    )
