from django.apps import AppConfig


class IhrPlatformConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ihr_platform"
    verbose_name = "IndieHR platform"

    def ready(self):
        from ihr_platform import celery_gate, signals  # noqa: F401

        # Queued Celery tasks never run in a hospital that is not served (P1-T18a, S1)
        celery_gate.install()
