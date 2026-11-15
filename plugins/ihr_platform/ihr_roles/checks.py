"""System check ihr_roles.E021: our permissions are registered where CARE's sync reads them."""

from django.core.checks import Error


def permissions_registered(app_configs=None, **kwargs):
    from care.security.permissions.base import PermissionController

    from ihr_roles.permissions import SLUGS

    known = PermissionController.get_permissions()
    return [
        Error(
            f"permission {slug!r} is not registered with CARE (its sync would drop it and "
            "every grant of it)",
            id="ihr_roles.E021",
        )
        for slug in SLUGS
        if slug not in known
    ]
