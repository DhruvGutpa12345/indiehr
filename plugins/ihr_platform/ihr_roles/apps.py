from django.apps import AppConfig
from django.core import checks


class IhrRolesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ihr_roles"
    verbose_name = "IndieHR roles"

    def ready(self):
        from care.security.permissions.base import PermissionController

        from ihr_roles import checks as role_checks
        from ihr_roles import registry
        from ihr_roles.permissions import IhrPermissions

        # Our permissions first (design-b.md §3), so grant() finds them. CARE builds its
        # permission cache lazily, once: one built before this point would miss our slugs.
        PermissionController.register_permission_handler(IhrPermissions)
        PermissionController.cache.clear()
        # The eight roles, the hidden one and their grants (P1-T11b1)
        registry.install()
        checks.register(role_checks.permissions_registered)
