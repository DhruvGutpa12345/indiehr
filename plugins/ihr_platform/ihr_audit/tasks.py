"""Seal each hospital's new audit entries every minute (beat sends it once per hospital), and
write the outside fingerprint of every hospital's chain every hour (once for the platform)."""

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


@shared_task(name="ihr_audit.seal", ignore_result=True)
def seal_audit_trail():
    if connection.schema_name != "public":
        seal_once(connection.schema_name)


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
        ANCHOR_EVERY_SECONDS,
        write_anchor.s(),
        name="ihr_audit_anchor",
        headers={PLATFORM_ONCE: True},
    )
