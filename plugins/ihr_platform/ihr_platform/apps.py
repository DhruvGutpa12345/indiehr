from django.apps import AppConfig
from django.db.models.signals import post_migrate


class IhrPlatformConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ihr_platform"
    verbose_name = "IndieHR platform"

    def ready(self):
        from ihr_platform import celery_gate, db_roles, signals  # noqa: F401

        # Queued Celery tasks never run in a hospital that is not served (P1-T18a, S1)
        celery_gate.install()
        # Every process checks its database role on its first connection (P1-T19; only
        # with IHR_DB_ROLES=required)
        db_roles.install_check()
        # The provisioner's queue takes only new requests from the app (P1-T19a, S1)
        post_migrate.connect(
            _ensure_queue_guard, sender=self, dispatch_uid="ihr_queue_guard"
        )


def _ensure_queue_guard(using="default", **kwargs):
    from django.db import connections

    from ihr_platform import queue_guard

    connection = connections[using]
    if getattr(connection, "schema_name", "public") == "public":
        queue_guard.ensure(connection)
