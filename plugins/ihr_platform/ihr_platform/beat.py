"""Celery beat in tenant mode (TENANCY_DESIGN.md §6.4).

CARE registers its periodic jobs (expired OTPs, token slots, incomplete uploads) without a tenant.
Sent from beat as-is they would run in ``public``, where no CARE table exists. This scheduler sends
each due job once per **active** hospital, inside that hospital's schema, so the task carries the
schema header and the worker switches to it. Provisioning, suspended, offboarding and archived
hospitals get no jobs.
"""

from tenant_schemas_celery.scheduler import TenantAwarePersistentScheduler


class ActiveTenantScheduler(TenantAwarePersistentScheduler):
    @classmethod
    def get_queryset(cls):
        from ihr_platform.models import Hospital

        return Hospital.objects.filter(status=Hospital.Status.ACTIVE).exclude(
            schema_name="public"
        )
