from django.apps import AppConfig
from django.db.models.signals import post_migrate


class IhrIdentityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ihr_identity"
    verbose_name = "IndieHR staff sign-in"
    # The staff directory's trigger (P1-T31a, directory.py): installed and kept current in
    # every hospital schema by post_migrate (check ihr_identity.E027 looks for this receiver)
    DIRECTORY_UID = "ihr_identity_staff_directory"

    def ready(self):
        from ihr_identity import checks, schema, signals  # noqa: F401

        post_migrate.connect(
            _install_directory, sender=self, dispatch_uid=self.DIRECTORY_UID
        )


def _install_directory(using="default", **kwargs):
    from django.db import connections

    from ihr_identity.directory import ensure_directory

    connection = connections[using]
    schema = getattr(connection, "schema_name", "public")
    if schema != "public":
        ensure_directory(connection, schema)
