"""CARE's sync_permissions_roles, guarded (P1-T11a, ADR-017).

CARE's command hard-deletes (QuerySet delete) every system role no longer registered in code, and
every permission no longer registered. Django then deletes what points at them: the memberships
holding the role (OrganizationUser, FacilityOrganizationUser cascade; PatientUser protects, which
stops start-up), and the grants a hospital gave its own roles. It runs for every hospital at each
start, so a rollback or a renamed role would silently take staff out of their jobs.

This command shadows CARE's (ihr_roles is the first installed app) and refuses, before that
hospital changes, when:

- a system role CARE would delete is still referenced by anything other than CARE's own
  RolePermission rows (every reverse relation of RoleModel is counted, soft-deleted rows included:
  the cascade removes those too);
- a permission CARE would delete is still granted to a hospital's own (non-system) role;
- a hospital's own role has the name of a registered role (CARE would take it over).

Candidates are chosen exactly as CARE chooses them (its default managers: soft-deleted roles are
never touched by CARE, so they never block). The check and CARE's sync run in one transaction,
with the doomed roles locked FOR UPDATE, so a membership created meanwhile waits and then fails
rather than being cascaded away.

`--check` only reports (exit 1 on a problem) and changes nothing: the start-up runs it for every
hospital first, so one hospital's problem stops the run before any hospital is synced.
"""

from care.security.management.commands.sync_permissions_roles import Command as CareSync
from care.security.models import PermissionModel, RoleModel, RolePermission
from care.security.permissions.base import PermissionController
from care.security.roles.role import RoleController
from django.core.management.base import CommandError
from django.db import transaction

# Reverse relations of RoleModel that CARE's sync rebuilds itself
REBUILT = {RolePermission}


def holders():
    """Every model that points at RoleModel, except the ones CARE's sync rebuilds."""
    return [
        rel
        for rel in RoleModel._meta.related_objects  # noqa: SLF001 (Django's model metadata)
        if rel.related_model not in REBUILT
    ]


def problems(lock=False):
    registered = {r.name for r in RoleController.get_roles()}
    out = []
    doomed = RoleModel.objects.filter(is_system=True).exclude(name__in=registered)
    if lock:
        doomed = doomed.select_for_update()
    for role in doomed:
        held = {}
        for rel in holders():
            n = rel.related_model._base_manager.filter(**{rel.field.name: role}).count()  # noqa: SLF001
            if n:
                held[rel.related_model.__name__] = n
        if held:
            out.append(
                f"system role {role.name!r} is no longer registered but is still held by {held}"
            )
    slugs = set(PermissionController.get_permissions())
    gone = PermissionModel.objects.exclude(slug__in=slugs)
    for perm in gone:
        own = RolePermission._base_manager.filter(  # noqa: SLF001 (soft-deleted grants count too)
            permission=perm, role__is_system=False
        )
        if own.exists():
            names = sorted(own.values_list("role__name", flat=True).distinct())
            out.append(
                f"permission {perm.slug!r} is no longer registered but is granted to this hospital's own roles {names}"
            )
    taken = RoleModel.objects.filter(is_system=False, name__in=registered).values_list(
        "name", flat=True
    )
    out += [
        f"a hospital's own role is named {name!r}, like a registered role"
        for name in taken
    ]
    return out


class RoleSyncRefused(CommandError):  # noqa: N818 (a refusal, named as the command says it)
    """The guarded sync refused (or would refuse). Its message is built only from role names,
    permission slugs and holder counts per model, never row values, so error records keep it
    (release.safe_error, S7)."""


class Command(CareSync):
    help = CareSync.help + " (guarded: never deletes what someone holds; ihr_roles)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="report problems only (exit 1), change nothing",
        )

    def handle(self, *args, **options):
        if options.get("check"):
            found = problems()
            if found:
                raise RoleSyncRefused("role sync would be refused: " + "; ".join(found))
            return None
        with transaction.atomic():
            found = problems(lock=True)
            if found:
                msg = ("refusing to sync roles: " + "; ".join(found)
                       + ". Re-register the role (names are permanent), or reassign its holders first.")  # fmt: skip
                raise RoleSyncRefused(msg)
            return super().handle(*args, **options)
