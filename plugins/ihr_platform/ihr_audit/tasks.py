"""Seal each hospital's new audit entries every minute (beat sends it once per hospital), and
write the outside fingerprint of every hospital's chain every hour (once for the platform).

Beat sends per-hospital jobs to active hospitals only, so a suspended, offboarding or archived
hospital's last entries would stay unsealed (and outside the fingerprints) for good. Every 10
minutes ``ihr_audit.seal_inactive`` (once for the platform) seals those (P1-T21 item 3)."""

import logging

from celery import Celery, current_app, shared_task
from django.conf import settings
from django.db import connection
from ihr_platform.beat import PLATFORM_ONCE

from ihr_audit import anchor
from ihr_audit.seal import seal_once

logger = logging.getLogger(__name__)
SEAL_EVERY_SECONDS = 60
ANCHOR_EVERY_SECONDS = 3600
SEAL_INACTIVE_EVERY_SECONDS = 600


@shared_task(name="ihr_audit.seal", ignore_result=True)
def seal_audit_trail():
    if connection.schema_name != "public":
        seal_once(connection.schema_name)


def seal_inactive_hospitals():
    """Seal every suspended, offboarding and archived hospital whose schema has this build's
    sealing function. One whose function is missing (a schema older than ihr_audit 0004,
    never migrated since: archived ones are not, fleet.py) is skipped and counted, never an
    error (review A7). Returns {"sealed": hospitals, "entries": n, "skipped": hospitals}."""
    from ihr_platform.models import Hospital

    from ihr_audit.install import has_trail

    inactive = (
        Hospital.Status.SUSPENDED,
        Hospital.Status.OFFBOARDING,
        Hospital.Status.ARCHIVED,
    )
    done = {"sealed": 0, "entries": 0, "skipped": 0}
    for hospital in Hospital.objects.filter(status__in=inactive).order_by("slug"):
        with connection.cursor() as cursor:
            if not has_trail(cursor, hospital.schema_name):
                done["skipped"] += 1
                continue
            cursor.execute(
                "SELECT to_regprocedure(%s) IS NOT NULL",
                [f'"{hospital.schema_name}".ihr_audit_seal_next()'],
            )
            if not cursor.fetchone()[0]:
                done["skipped"] += 1
                continue
        try:
            done["entries"] += seal_once(hospital.schema_name)
            done["sealed"] += 1
        except Exception:  # one hospital never stops the others; the watch sees its lag
            done["skipped"] += 1
            logger.exception("could not seal hospital %s", hospital.slug)
    return done


@shared_task(name="ihr_audit.seal_inactive", ignore_result=True)
def seal_inactive():
    done = seal_inactive_hospitals()
    if done["sealed"] or done["skipped"]:
        logger.info(
            "inactive hospitals sealed: %(sealed)s (%(entries)s entries), skipped: "
            "%(skipped)s",
            done,
        )


@shared_task(name="ihr_audit.anchor", ignore_result=True)
def write_anchor():
    try:
        key = anchor.write()
    except anchor.NotConfiguredError:
        if not settings.DEBUG:
            raise  # production must keep fingerprints: the task fails loudly
        logger.warning(
            "audit fingerprints are not set up (scripts/care-dev.sh audit-anchors)"
        )
        return
    logger.info("audit fingerprint written: %s", key)


@current_app.on_after_finalize.connect
def setup_periodic_tasks(sender: Celery, **kwargs):
    sender.add_periodic_task(
        SEAL_EVERY_SECONDS, seal_audit_trail.s(), name="ihr_audit_seal"
    )
    sender.add_periodic_task(
        SEAL_INACTIVE_EVERY_SECONDS,
        seal_inactive.s(),
        name="ihr_audit_seal_inactive",
        headers={PLATFORM_ONCE: True},
    )
    sender.add_periodic_task(
        ANCHOR_EVERY_SECONDS,
        write_anchor.s(),
        name="ihr_audit_anchor",
        headers={PLATFORM_ONCE: True},
    )
