from django.apps import AppConfig


class IhrIdentityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ihr_identity"
    verbose_name = "IndieHR staff sign-in"

    def ready(self):
        from ihr_identity import checks, schema, signals  # noqa: F401
