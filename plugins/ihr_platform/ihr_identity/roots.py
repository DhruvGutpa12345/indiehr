"""The hospital root (P1-T25, Q-K76 B; docs/reviews/p1-t25/attack.md "Decision", Q-K126…Q-K131).

A hospital's root is one top-level CARE organisation of type ``role``, ``system_generated``, with
``metadata["ihr"] == "hospital-root"`` (its name means nothing; the marker identifies it). There is
no table, field or trigger: the organisation is the record, so dumps and restores carry it, and
CARE's own rules for role organisations protect it (a non-superuser cannot update, deactivate,
retype or delete one, or add a child to it; ``ihr_identity.tests.test_root`` pins each rule).

``valid_root()`` is the one predicate everything uses (the gate, provisioning, the fleet step,
the restore, the operator command): exactly one marked organisation, active, not deleted,
parentless, of type ``role`` and with no managing organisations (a managing organisation's
admins could add members to it: review S4). Anything else is None, and with no valid root nobody
is a hospital-wide admin (fail closed). It is read from the database on every call, never cached
across requests (S10), so a change applies at once.

Writes (``ensure_root``, ``place``, ``remove_state``, ``converge``) are made by the database owner
(provisioning, the fleet runner, the restore, ``ihr_hospital_root``) or by the seed, inside the
hospital's schema, each under that hospital's lock (the caller's ``runner.exclusive``) and a
transaction-level advisory lock of its own, audited under ``root:<operator>``. Each writes one
fixed line, read by the log alert ``HospitalRootProblem`` (deploy/logs/alerts/root.yml; the
lines of an operator's ``ihr_hospital_root`` run in the unlogged admin one-off, so their record
is the audit trail only, review T5):

    ihr-root: hospital=<slug> action=<ensure|place|remove-state> outcome=<o>[ n=<k>] by=<operator>

with ``o`` one of ``done``, ``exists``, ``no-admin``, ``invalid``, ``state-admins`` or ``failed``
(the fleet step raised or was deferred, review T3; ``n`` the admin rows left on government
organisations, review S5). No person is named in it.
"""

import hashlib
import os
import re

from django.db import connection, transaction

MARK_KEY = "ihr"
MARK = "hospital-root"
NAME = "Hospital administration"
DESCRIPTION = (
    "Hospital-wide administrators (IndieHR): an admin role here opens the audit trail, "
    "reference pack decisions and sign-in resets."
)
ADMIN_ROLE = "Administrator"  # CARE's system role for a hospital's first admin
LOG_PREFIX = "ihr-root:"
OUTCOMES = ("done", "exists", "no-admin", "invalid", "state-admins", "failed")
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


class RootError(Exception):
    """A root write that cannot be done (no valid root, a missing role, a refused user)."""


# --- reading --------------------------------------------------------------------------------


def _organizations():
    from care.emr.models.organization import Organization

    # Deleted rows too (the default manager hides them): a deleted marked organisation is
    # still "a marked organisation", so ensure_root never makes a second one beside it
    return Organization._base_manager.all()  # noqa: SLF001


def marked():
    """Every system-made organisation carrying the marker, deleted ones included. Only owner
    code sets ``system_generated`` (CARE's write specs have no such field), while CARE's API
    takes ``metadata`` from a team's "Admin": a marked organisation made or edited there is
    ignored, never a second root that would switch the screens off (review A1)."""
    return _organizations().filter(
        system_generated=True, **{f"metadata__{MARK_KEY}": MARK}
    )


def is_valid(organization):
    return (
        organization is not None
        and not organization.deleted
        and organization.active
        and organization.parent_id is None
        and organization.org_type == "role"
        and not organization.managing_organizations
        and organization.system_generated
        and (organization.metadata or {}).get(MARK_KEY) == MARK
    )


def valid_root():
    """The hospital's root, or None (none marked, more than one, or the marked one fails a
    check): fail closed. Run inside the hospital's schema; one query."""
    rows = list(marked().order_by("pk")[:2])
    if len(rows) != 1 or not is_valid(rows[0]):
        return None
    return rows[0]


def admin_role_ids():
    from care.security.models import RolePermission
    from care.security.permissions.user import UserPermissions

    return RolePermission.objects.filter(
        permission__slug=UserPermissions.can_create_user.name
    ).values_list("role_id", flat=True)


def admins(root):
    """The root's live admin memberships (a role holding ``can_create_user``; the user not
    deleted)."""
    from care.emr.models.organization import OrganizationUser

    return OrganizationUser.objects.filter(
        organization=root,
        deleted=False,
        role_id__in=admin_role_ids(),
        user__deleted=False,
    )


def live_admins(root, pending=None):
    """``admins(root)`` whose person can still act (review A3): active, or awaiting their
    setup e-mail (P1-T5c; a deactivation clears it), or ``pending`` (the provisioning
    record's own first admin at the activate step, before their invitation is sent)."""
    from django.db.models import Q

    from ihr_identity.models import StaffIdentity

    can_act = Q(user__is_active=True) | Q(
        user_id__in=StaffIdentity.objects.filter(awaiting_setup=True).values("user_id")
    )
    if pending:
        can_act |= Q(user_id=pending)
    return admins(root).filter(can_act)


def state_admins():
    """Admin memberships left on top-level government organisations (review S5): before P1-T25
    the first admin was placed on the hospital's state; such rows still list every patient with
    an address in that state, and are reset-protected (Q-K130 A)."""
    from care.emr.models.organization import OrganizationUser

    return OrganizationUser.objects.filter(
        deleted=False,
        role_id__in=admin_role_ids(),
        user__deleted=False,
        organization__org_type="govt",
        organization__parent__isnull=True,
        organization__deleted=False,
    )


# --- the log line ---------------------------------------------------------------------------


def operator_token(operator):
    """The operator as written in the line: letters, digits, ``_ . -`` only (never an e-mail
    address), at most 64 characters."""
    token = _SAFE.sub("-", str(operator or ""))[:64].strip("-")
    return token or "unknown"


def log_line(slug, action, outcome, operator, n=None):
    count = f" n={int(n)}" if n is not None else ""
    return (
        f"{LOG_PREFIX} hospital={slug} action={action} outcome={outcome}{count} "
        f"by={operator_token(operator)}"
    )


def _slug():
    tenant = getattr(connection, "tenant", None)
    return getattr(tenant, "slug", None) or connection.schema_name


def _write(line):
    # stderr, unbuffered, one line: the container's journal (as ihr_access.records does)
    os.write(2, f"{line}\n".encode())


def say(action, outcome, operator, n=None, *, slug=None):
    line = log_line(slug or _slug(), action, outcome, operator, n)
    _write(line)
    return line


# --- writing --------------------------------------------------------------------------------


def _lock():
    """This hospital's root writes one at a time, whoever the caller (a transaction lock)."""
    digest = hashlib.sha256(f"ihr:root:{connection.schema_name}".encode()).digest()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [int.from_bytes(digest[:8], "big", signed=True)],
        )


def _audited(action, operator, reason=None):
    from ihr_audit import context as audit

    ctx = {"job": "hospital-root", "action": action}
    if reason:
        ctx["reason"] = reason
    return audit.job(ctx, f"root:{operator}"[:150])


def ensure_root(operator, *, slug=None):
    """Create the marked organisation when none exists. Never adopts one by name and never
    makes a second: a marked organisation that is not valid is left alone (``invalid``).
    Returns ``(root or None, outcome)``, outcome ``done`` (created), ``exists`` or ``invalid``."""
    from care.emr.models.organization import Organization

    with _audited("ensure", operator), transaction.atomic():
        _lock()
        if marked().exists():
            root = valid_root()
            if root is not None:
                return root, "exists"
            say("ensure", "invalid", operator, slug=slug)
            return None, "invalid"
        root = Organization.objects.create(
            name=NAME,
            description=DESCRIPTION,
            org_type="role",
            system_generated=True,
            metadata={MARK_KEY: MARK},
        )
        say("ensure", "done", operator, slug=slug)
        return root, "done"


def _admin_role():
    from care.security.models import RoleModel

    role = (
        RoleModel.objects.filter(name=ADMIN_ROLE, is_system=True).order_by("id").first()
    )
    if role is None:
        msg = f"the system role {ADMIN_ROLE!r} is missing: run sync_permissions_roles"
        raise RootError(msg)
    return role


def place(user, operator, *, slug=None, reason=None):
    """Make ``user`` Administrator on the valid root (the Q-K122 recovery, the seed, provisioning).
    True when a membership was added or changed. Refuses (RootError) with no valid root, or for
    a deleted, service or superuser account."""
    from care.emr.models.organization import OrganizationUser

    if user.deleted or user.is_service_account or user.is_superuser:
        msg = "only a staff account (not deleted, not a service account or superuser)"
        raise RootError(msg)
    role = _admin_role()
    with _audited("place", operator, reason), transaction.atomic():
        _lock()
        root = valid_root()
        if root is None:
            raise RootError("this hospital has no valid root")
        if admins(root).filter(user=user).exists():
            return False
        existing = OrganizationUser.objects.filter(organization=root, user=user).first()
        if existing is not None:
            existing.role = role
            existing.save(update_fields=["role", "modified_date"])
        else:
            OrganizationUser.objects.create(organization=root, user=user, role=role)
        say("place", "done", operator, slug=slug)
        return True


def remove_state(user, operator, *, slug=None, reason=None):
    """Soft-delete ``user``'s Administrator rows on top-level government organisations, the row
    provisioning made before P1-T25 (Q-K128 A; never another role, never another person).
    Returns how many."""
    role = _admin_role()
    with _audited("remove-state", operator, reason), transaction.atomic():
        _lock()
        rows = list(state_admins().filter(user=user, role=role))
        for row in rows:
            row.deleted = True
            row.save(update_fields=["deleted", "modified_date"])
        if rows:
            say("remove-state", "done", operator, n=len(rows), slug=slug)
        return len(rows)


def entitled(user):
    """May the provisioning record's first admin be placed on the root (Q-K129 A, review S1 and
    D2)? Their account exists, is not deleted, not a service account or superuser, is active
    or still awaiting its setup, and passes today's wider rule (``top_level_admin``)."""
    from ihr_identity.models import StaffIdentity
    from ihr_identity.staff_access import top_level_admin

    if user is None or user.deleted or user.is_service_account or user.is_superuser:
        return False
    waiting = StaffIdentity.objects.filter(user=user, awaiting_setup=True).exists()
    return (user.is_active or waiting) and top_level_admin(user)


def onboarding_admin(hospital):
    """The CARE user id provisioning made the first admin (the last succeeded onboarding), or
    None (a hospital made by ihr_create_tenant, the dev seed or a test)."""
    from ihr_platform.models import TenantOperation

    for result in (
        TenantOperation.objects.filter(
            hospital_id=hospital.pk,
            kind=TenantOperation.Kind.PROVISION,
            status=TenantOperation.Status.SUCCEEDED,
        )
        .order_by("-finished_at", "-created_at")
        .values_list("result", flat=True)
    ):
        if (result or {}).get("admin_user_id"):
            return result["admin_user_id"]
    return None


def converge(hospital, operator):
    """One hospital's root step (the fleet step ``root`` and the restore): ensure the root; if
    it has no admin who can still act (``live_admins``), place the provisioning record's first
    admin when still entitled and drop that person's state row, in one transaction. Run inside the hospital's schema, under its lock. Returns a dict
    with ``outcome`` (``exists``, ``done``, ``no-admin`` or ``invalid``), ``created``,
    ``placed`` (a CARE user id or None), ``removed`` and ``state_admins``."""
    from care.users.models import User

    slug = hospital.slug
    root, made = ensure_root(operator, slug=slug)
    report = {"created": made == "done", "placed": None, "removed": 0}
    if root is None:
        return {**report, "outcome": "invalid", "state_admins": state_admins().count()}
    outcome = "exists"
    if not live_admins(root).exists():
        user_id = onboarding_admin(hospital)
        user = User._base_manager.filter(pk=user_id).first() if user_id else None  # noqa: SLF001
        if entitled(user):
            # One change (review T2): a run that finds an admin on the root never drops a
            # state row, so the two never commit apart
            with transaction.atomic():
                place(user, operator, slug=slug)
                report["removed"] = remove_state(user, operator, slug=slug)
            report["placed"] = user.pk
            outcome = "done"
        else:
            say("place", "no-admin", operator, slug=slug)
            outcome = "no-admin"
    left = state_admins().count()
    if left:
        say("ensure", "state-admins", operator, n=left, slug=slug)
    return {**report, "outcome": outcome, "state_admins": left}
