"""The provisioner: runs what operators queue in the console (P1-T5b).

    manage.py ihr_provision_worker --role onboarding|staff [--once] [--interval 3]

It also delivers staff invitations queued by hospitals' "Add user" (P1-T5e1), carries out
hospital admins' requests (reset a lost authenticator, resend or cancel an invitation) and
withdraws invitations nobody accepted in time (P1-T5e2).

Two processes (P1-T5f, owner): ``--role onboarding`` (hospital runs, its secret
IHR_KEYCLOAK_ONBOARDING_SECRET) and ``--role staff`` (everything about staff accounts,
IHR_KEYCLOAK_STAFF_SECRET), each a narrow service account of the hospitals' realm
(keycloak/accounts.py). Neither may hold the bootstrap admin credential, and the console and
the hospitals' web app and worker hold none. Work is claimed with row locks (skip locked), so
several workers never take the same item.
"""

import logging
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connection, transaction
from django.utils import timezone
from ihr_audit import context as audit

from ihr_platform import invitations
from ihr_platform.models import TenantOperation
from ihr_platform.provisioning import runner

logger = logging.getLogger(__name__)


def claim_next():
    """Take the oldest queued operation off the queue: (operation, action, who) or None."""
    connection.set_schema_to_public()
    with transaction.atomic():
        op = (
            TenantOperation.objects.select_for_update(skip_locked=True)
            .filter(queued_at__isnull=False)
            .order_by("queued_at")
            .first()
        )
        if op is None:
            return None
        action, who = op.queued_action, op.queued_by
        TenantOperation.objects.filter(pk=op.pk).update(
            queued_action="", queued_by="", queued_at=None
        )
    op.refresh_from_db()
    return op, action, who


ROLES = ("onboarding", "staff")


def work_once(role=None):
    """One unit of work. ``role`` (P1-T5f): "onboarding" runs queued hospital operations,
    "staff" delivers invitations, carries out admins' requests and expires invitations;
    None does both (tests). Each role's process holds only its own Keycloak secret. True if
    there was something to do."""
    if role in (None, "onboarding") and run_one_operation():
        return True
    if role == "onboarding":
        return False
    try:
        return (
            invitations.deliver_once()
            or invitations.handle_request_once()
            or bool(invitations.expire_due())
        )
    finally:
        connection.set_schema_to_public()


def run_one_operation():
    """Run one queued operation, if any. True if one ran; False also when the one claimed
    had to go back on the queue (its hospital is busy): that is no progress, so invitations
    get their turn and the loop sleeps instead of spinning."""
    claimed = claim_next()
    if claimed is None:
        return False
    op, action, who = claimed
    try:
        with audit.job(
            {"job": "provisioning", "operation": op.pk, "action": action},
            f"worker:for {who}",
        ):
            if action == "rollback":
                runner.rollback(op, who)
            elif action == "resend_setup":
                runner.resend_setup(op)
            else:
                runner.run(op)
    except runner.ProvisioningError as exc:
        if runner.is_busy(op.slug):
            # Another run (the command line, another provisioner) holds this hospital:
            # put the request back rather than dropping it.
            TenantOperation.objects.filter(pk=op.pk, queued_at__isnull=True).update(
                queued_action=action, queued_by=who, queued_at=timezone.now()
            )
            logger.info("provisioning %s (%s) waits: %s", op.slug, action, exc)
            connection.set_schema_to_public()
            return False
        logger.warning("provisioning %s (%s) refused: %s", op.slug, action, exc)
    except Exception:  # recorded on the operation; the worker carries on
        logger.exception("provisioning %s (%s) stopped", op.slug, action)
    finally:
        connection.set_schema_to_public()
    return True


class Command(BaseCommand):
    help = "Run the provisioning operations queued from the operator console."

    def add_arguments(self, parser):
        parser.add_argument("--role", choices=ROLES, required=True)
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=float, default=3.0)

    def handle(self, *args, role, once, interval, **options):
        secret = f"IHR_KEYCLOAK_{role.upper()}_SECRET"
        if not os.environ.get(secret):
            msg = f"Set {secret}: each worker uses only its own account."
            raise CommandError(msg)
        if os.environ.get("IHR_KEYCLOAK_ADMIN_CLIENT_SECRET"):
            raise CommandError(
                "The bootstrap admin credential is in this worker's environment: "
                "workers get only their own narrow secret (P1-T5f)."
            )
        for other in ROLES:
            if other != role and os.environ.get(f"IHR_KEYCLOAK_{other.upper()}_SECRET"):
                msg = (
                    f"IHR_KEYCLOAK_{other.upper()}_SECRET is in the {role} worker's "
                    "environment: each worker holds only its own account (P1-T5f)."
                )
                raise CommandError(msg)
        self.stdout.write(f"provisioner ready ({role})")
        while True:
            close_old_connections()
            did = work_once(role)
            if once:
                return
            if not did:
                time.sleep(interval)
