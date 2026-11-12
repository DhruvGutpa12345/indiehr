from django.apps import AppConfig


class IhrTerminologyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ihr_terminology"
    verbose_name = "IndieHR terminology"

    def ready(self):
        # CARE's terminology calls are answered here, in this process; nothing goes out (ADR-016)
        from ihr_terminology import client

        client.install()
