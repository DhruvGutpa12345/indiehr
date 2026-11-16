from django.apps import AppConfig
from django.core import checks
from django.db.models.signals import post_migrate


class IhrRefdataConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ihr_refdata"
    verbose_name = "IndieHR reference packs"

    def ready(self):
        checks.register(forms_guard_checks)
        # The answered-form guard (P1-T15b2b, forms_guard.py): installed and kept current in
        # every hospital schema by post_migrate, never by re-running a migration
        post_migrate.connect(_install, sender=self, dispatch_uid=INSTALL_UID)


INSTALL_UID = "ihr_refdata_forms_guard"


def forms_guard_checks(app_configs=None, **kwargs):
    """ihr_refdata.E026: the answered-form guard is wired as this build means it (its install
    on post_migrate, its three tables, every frozen column apart from the free ones).
    ihr_refdata.W026: its fingerprint differs from the one the newest ihr_refdata migration that states one gives, so
    existing hospitals would not get it (a warning, so a mutation run still starts; the test
    ``GuardChangesShipWithAMigration`` fails on it)."""
    from ihr_refdata import forms_guard

    installed = any(
        receiver[0][0] == INSTALL_UID for receiver in post_migrate.receivers
    )
    found = [
        checks.Error(problem, id="ihr_refdata.E026")
        for problem in forms_guard.static_problems(installed=installed)
    ]
    found += [
        checks.Warning(problem, id="ihr_refdata.W026")
        for problem in forms_guard.code_problems()
    ]
    return found


def _install(using="default", **kwargs):
    from django.db import connections

    from ihr_refdata import forms_guard

    connection = connections[using]
    schema = getattr(connection, "schema_name", "public")
    if schema != "public":
        forms_guard.ensure(connection, schema)
