"""Seal each hospital's new audit entries every minute (beat sends it once per hospital)."""

from celery import Celery, current_app, shared_task
from django.db import connection

from ihr_audit.seal import seal_once

SEAL_EVERY_SECONDS = 60


@shared_task(name="ihr_audit.seal", ignore_result=True)
def seal_audit_trail():
    if connection.schema_name != "public":
        seal_once(connection.schema_name)


@current_app.on_after_finalize.connect
def setup_periodic_tasks(sender: Celery, **kwargs):
    sender.add_periodic_task(
        SEAL_EVERY_SECONDS, seal_audit_trail.s(), name="ihr_audit_seal"
    )
