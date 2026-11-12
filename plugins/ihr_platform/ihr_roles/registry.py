"""Our hospital roles, added to CARE's code-defined roles (P1-T11a, ADR-017 Proposed).

CARE defines roles in code (care/security/roles/role.py) and grants each permission to roles by
listing them on the permission (`Permission(..., roles=[...])`). `sync_permissions_roles` writes
both into every hospital's schema at each start, as system roles that hospitals cannot edit.

Rules here:
- **Names are permanent identifiers.** The sync matches roles by name, and a system role that is
  no longer registered is deleted, with every membership that holds it (OrganizationUser and
  FacilityOrganizationUser cascade). A role is therefore never renamed or removed: a retired role
  stays registered with no grants. `PERMANENT_NAMES` freezes the names; a test checks it.
- **Grants rebind, never append.** Several CARE permissions share one list object (all six
  encounter permissions use CLINICAL_DATA_ACCESS_ROLES), so `.append` would grant every one of
  them and change Doctor's and Nurse's grants too. `grant()` gives the permission a new list.
- **Fail at start-up** on an unknown permission slug (an upstream rename), or a name that clashes
  with one of CARE's roles.

The roles themselves (Receptionist, Cashier, ...) come in P1-T11b, after the owner's decision on
what non-clinical roles may see (Q-K21).
"""

from django.core.exceptions import ImproperlyConfigured

ROLES = []  # care.security.roles.role.Role instances (P1-T11b)
GRANTS = {}  # role name -> [permission slug, ...]
PERMANENT_NAMES = []  # every role name we have ever registered, in order; never shortened


def _permission(slug):
    from care.security.permissions.base import PermissionController

    for handler in (
        PermissionController.internal_permission_handlers
        + PermissionController.override_permission_handlers
    ):
        for perm in handler:
            if perm.name == slug:
                return perm
    msg = f"ihr_roles: no CARE permission named {slug!r} (renamed upstream?)"
    raise ImproperlyConfigured(msg)


def grant(slug, role):
    """Give `role` the permission `slug`, without touching the list other permissions share."""
    perm = _permission(slug)
    if any(r.name == role.name for r in perm.value.roles):
        return
    perm.value.roles = [*perm.value.roles, role]


def install(roles=None, grants=None, permanent=None):
    """Register our roles and their grants (once per process)."""
    from care.security.roles.role import RoleController

    roles = ROLES if roles is None else roles
    grants = GRANTS if grants is None else grants
    permanent = PERMANENT_NAMES if permanent is None else permanent
    care_names = {r.name for r in RoleController.internal_roles}
    registered = {r.name for r in RoleController.override_roles}
    for role in roles:
        if role.name in care_names:
            msg = f"ihr_roles: {role.name!r} clashes with a CARE role"
            raise ImproperlyConfigured(msg)
        if role.name not in permanent:
            msg = f"ihr_roles: {role.name!r} is not in PERMANENT_NAMES (role names are permanent identifiers)"
            raise ImproperlyConfigured(msg)
        if role.name not in registered:
            RoleController.register_role(role)
            registered.add(role.name)
    missing = [n for n in permanent if n not in {r.name for r in roles}]
    if missing:
        msg = f"ihr_roles: {missing} were registered once and must stay registered (retire a role with no grants)"
        raise ImproperlyConfigured(msg)
    by_name = {r.name: r for r in roles}
    for name, slugs in grants.items():
        for slug in slugs:
            grant(slug, by_name[name])
