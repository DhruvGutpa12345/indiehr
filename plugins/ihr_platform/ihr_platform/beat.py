"""Celery beat in tenant mode (TENANCY_DESIGN.md §6.4).

CARE registers its periodic jobs (expired OTPs, token slots, incomplete uploads) without a tenant.
Sent from beat as-is they would run in ``public``, where no CARE table exists. This scheduler sends
each due job once per **active** hospital, inside that hospital's schema, so the task carries the
schema header and the worker switches to it. Provisioning, suspended, offboarding and archived
hospitals get no jobs.

A job registered with the ``PLATFORM_ONCE`` header (the audit fingerprint, P1-T6b) is sent
once, in ``public``, instead: it covers every hospital itself.
"""

import logging

from django_tenants.utils import schema_context
from tenant_schemas_celery.scheduler import TenantAwarePersistentScheduler

logger = logging.getLogger(__name__)
PLATFORM_ONCE = "ihr_platform_once"


class ActiveTenantScheduler(TenantAwarePersistentScheduler):
    @classmethod
    def get_queryset(cls):
        from ihr_platform.models import Hospital

        return Hospital.objects.filter(status=Hospital.Status.ACTIVE).exclude(
            schema_name="public"
        )

    def apply_entry(self, entry, producer=None):
        if entry.options.get("headers", {}).get(PLATFORM_ONCE):
            try:
                with schema_context("public"):
                    self.apply_async(entry, producer=producer, advance=False)
            except Exception:  # like the parent: one failed send never stops beat
                logger.exception("could not send %s", entry.name)
            return
        super().apply_entry(entry, producer=producer)
