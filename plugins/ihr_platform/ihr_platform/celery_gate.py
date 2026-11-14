"""CARE's Celery tasks never run in a hospital that is not served (P1-T18a review S1).

The gate (release_gate.py), sign-ins and beat stop new work for a hospital that is held for a
release or whose schema is not this build's. A task already queued for it (sent just before
the hold, an autoretry, a countdown) would still run once the workers are back, with new code
against a schema that is behind. So every task, after tenant_schemas_celery has switched to its
hospital's schema and before its body runs, checks that the hospital is served; if it is not,
the task is sent again a minute later, unchanged (same id, same retry count: its own retry
budget is untouched) and this run ends as ignored. It is never run and never failed.

Installed on tenant_schemas_celery's task class (every task of CARE's app subclasses it) from
IhrPlatformConfig.ready(). A task run eagerly (in the caller's own process) is not checked: its
caller, a request or a command, is gated itself.
"""

import logging

from celery.exceptions import Ignore
from django.db import connection

logger = logging.getLogger(__name__)
DEFER_SECONDS = 60
_ORIGINAL = {}


def _served(schema):
    from ihr_platform import release
    from ihr_platform.models import Hospital

    return (
        Hospital.objects.filter(schema_name=schema).filter(release.ready_q()).exists()
    )


def gated_call(task, *args, **kwargs):
    from ihr_platform import release

    schema = getattr(connection, "schema_name", "public")
    if schema == "public" or task.request.is_eager:
        return _ORIGINAL["call"](task, *args, **kwargs)
    # The hospital's job lock for the whole run, taken before the check (P1-T22b review C4):
    # a restore's drain waits for this task, and a task that starts after the hold, or while
    # the drain holds or waits for the lock (``taken`` False, at once), defers
    with release.job(schema) as taken:
        if not taken or not _served(schema):
            again = task.signature_from_request(
                countdown=DEFER_SECONDS, headers={"_schema_name": schema}
            )
            again.apply_async()
            logger.warning(
                "ihr-fleet: schema=%s task=%s outcome=task-deferred", schema, task.name
            )
            msg = f"{schema} is not served (held or not this build's): sent again later"
            raise Ignore(msg)
        return _ORIGINAL["call"](task, *args, **kwargs)


def install():
    """Put the check in front of every task's body (idempotent)."""
    from tenant_schemas_celery.task import TenantTask

    if "call" not in _ORIGINAL:
        _ORIGINAL["call"] = TenantTask.__call__
        TenantTask.__call__ = gated_call
