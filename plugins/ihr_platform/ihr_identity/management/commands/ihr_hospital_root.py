"""The hospital root's operator command (P1-T25, Q-K76 B; decision item 6).

    manage.py ihr_hospital_root --slug S show
    manage.py ihr_hospital_root --slug S place <username> --operator <you> --reason "<ticket>"
    manage.py ihr_hospital_root --slug S remove-state <username> --operator <you> --reason "…"

``show`` (read-only): the root (or why there is no valid one), its admins, and the admin-role
holders on other top-level organisations (who lost the admin screens with P1-T25, and the state
admins review S5 names). ``place``: the recovery when no hospital-wide admin is left (Q-K122);
the person becomes Administrator on the root. ``remove-state``: soft-deletes that person's
Administrator rows on top-level government organisations (review S5). Writes run as the database
owner (the admin one-off; staging ``scripts/staging.sh hospital-root``), under the hospital's
lock, and are audited as ``root:<operator>`` with the reason. Only usernames are printed.
"""

import re

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context
from ihr_platform import release
from ihr_platform.models import Hospital
from ihr_platform.provisioning import runner

from ihr_identity import roots

OPERATOR = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
MAX_REASON = 200


# The audit actor is ``root:<operator>``: these are the names the automatic sources write
# (fleet runner ``fleet-*``, restore ``restore-*``, provisioning ``provisioning-<id>``, the
# demo seed, ``ihr_create_tenant``, the tests), so a manual write may not use them (review T4)
AUTOMATIC_PREFIXES = ("fleet-", "restore-", "provisioning-")
AUTOMATIC_NAMES = ("demo-seed", "ihr_create_tenant", "test", "startup")


def _operator(text):
    if not text or not OPERATOR.fullmatch(text):
        raise CommandError("--operator must be 1-64 letters, digits or . _ @ -")
    low = text.lower()
    if low.startswith(AUTOMATIC_PREFIXES) or low in AUTOMATIC_NAMES:
        msg = (
            f"--operator {text!r} is a name the fleet runner, a restore, provisioning or "
            "the seed writes: use your own name"
        )
        raise CommandError(msg)
    return text


def _reason(text):
    reason = " ".join((text or "").split())
    if not reason or len(reason) > MAX_REASON:
        msg = f"--reason is required, on one line, at most {MAX_REASON} characters"
        raise CommandError(msg)
    return reason


class Command(BaseCommand):
    help = "Show a hospital's root, or place someone on it (P1-T25)."

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True)
        parser.add_argument("action", choices=["show", "place", "remove-state"])
        parser.add_argument("username", nargs="?")
        parser.add_argument("--operator")
        parser.add_argument("--reason")

    def handle(self, *args, slug, action, username, operator, reason, **options):
        hospital = (
            Hospital.objects.filter(slug=slug).exclude(schema_name="public").first()
        )
        if hospital is None:
            msg = f"No hospital {slug!r}"
            raise CommandError(msg)
        release.require_ready(hospital)  # never into a held or older schema
        if action == "show":
            with schema_context(hospital.schema_name):
                self._show(hospital)
            return
        if not username:
            msg = f"{action} needs a username"
            raise CommandError(msg)
        operator, reason = _operator(operator), _reason(reason)
        try:
            with (
                runner.exclusive(hospital.slug),
                schema_context(hospital.schema_name),
            ):
                self._write(hospital, action, username, operator, reason)
        except runner.ProvisioningError as exc:
            raise CommandError(str(exc)) from None

    def _write(self, hospital, action, username, operator, reason):
        from care.users.models import User

        user = User.objects.filter(username=username).first()
        if user is None:
            msg = f"No user {username!r} in {hospital.slug}"
            raise CommandError(msg)
        if action == "place":
            try:
                added = roots.place(user, operator, slug=hospital.slug, reason=reason)
            except roots.RootError as exc:
                raise CommandError(str(exc)) from None
            self.stdout.write(
                f"{hospital.slug}: {username} "
                + ("placed on the root" if added else "is already an admin there")
            )
        else:
            removed = roots.remove_state(
                user, operator, slug=hospital.slug, reason=reason
            )
            self.stdout.write(
                f"{hospital.slug}: {username}: {removed} state Administrator row(s) removed"
            )

    def _show(self, hospital):
        from care.emr.models.organization import OrganizationUser

        root = roots.valid_root()
        out = self.stdout.write
        if root is None:
            marked = list(roots.marked().order_by("pk"))
            why = (
                "none is marked"
                if not marked
                else f"{len(marked)} marked organisation(s), none valid (checks: one only, "
                "active, not deleted, top-level, type role, no managing organisations)"
            )
            out(
                f"{hospital.slug}: no valid root ({why}): nobody is a hospital-wide admin"
            )
        else:
            out(f"{hospital.slug}: root {root.external_id} {root.name!r}")
            names = sorted(
                roots.admins(root).values_list("user__username", flat=True).distinct()
            )
            out(f"  admins on the root ({len(names)}): {', '.join(names) or 'none'}")
        rows = (
            OrganizationUser.objects.filter(
                deleted=False,
                role_id__in=roots.admin_role_ids(),
                user__deleted=False,
                organization__parent__isnull=True,
                organization__deleted=False,
            )
            .exclude(organization_id=getattr(root, "pk", None))
            .select_related("organization", "user", "role")
            .order_by("organization__name", "user__username")
        )
        out(
            f"  admin roles on other top-level organisations ({rows.count()}; not "
            "hospital-wide since P1-T25):"
        )
        for row in rows:
            out(
                f"    {row.user.username}: {row.role.name} on {row.organization.name!r} "
                f"({row.organization.org_type})"
            )
        left = roots.state_admins().count()
        if left:
            out(
                f"  {left} admin row(s) on government organisations: remove one with "
                "remove-state <username> --operator <you> --reason ..."
            )
