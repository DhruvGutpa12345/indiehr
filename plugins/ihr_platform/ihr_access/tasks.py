"""End records grants past their time, every minute, in each active hospital (P1-T11b4).

Beat sends this per hospital, inside its schema (ihr_platform/beat.py, as ``ihr_audit.seal``):
no ``PLATFORM_ONCE`` header. Held, restoring, suspended and archived hospitals get no run; their
grants still close at read time (authz.py counts a hidden-role row only under a live grant) and
are ended by the first run after they come back.
"""

from celery import Celery, current_app, shared_task
from django.db import connection

from ihr_access import records

EXPIRE_EVERY_SECONDS = 60


@shared_task(name="ihr_access.expire_grants", ignore_result=True)
def expire_grants():
    if connection.schema_name != "public":
        records.expire_due()


@current_app.on_after_finalize.connect
def setup_periodic_tasks(sender: Celery, **kwargs):
    sender.add_periodic_task(
        EXPIRE_EVERY_SECONDS, expire_grants.s(), name="ihr_access_expire_grants"
    )
