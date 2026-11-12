from django.apps import AppConfig


class IhrRolesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ihr_roles"
    verbose_name = "IndieHR roles"

    def ready(self):
        # Indian hospital roles are registered here in P1-T11b (registry.ROLES, registry.GRANTS);
        # until then the registry is empty and only the guarded sync is active.
        from ihr_roles import registry

        registry.install()
