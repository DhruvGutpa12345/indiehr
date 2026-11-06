from django.apps import AppConfig


class IhrPlatformConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ihr_platform"
    verbose_name = "IndieHR platform"

    def ready(self):
        from ihr_platform import signals  # noqa: F401
