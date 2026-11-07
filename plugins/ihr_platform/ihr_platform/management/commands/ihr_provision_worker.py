"""The provisioner: runs what operators queue in the console (P1-T5b).

    manage.py ihr_provision_worker [--once] [--interval 3]

A process of its own, the only one holding the Keycloak admin credential (from its
environment, IHR_KEYCLOAK_ADMIN_CLIENT_ID/_SECRET): the console and the hospitals' web app and
worker never do (TENANCY_DESIGN §4.5, review of P1-T5). It claims one queued operation at a
time (row lock, skip locked), so several provisioners can never take the same one.
"""

import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

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


def work_once():
    """Run one queued operation, if any. True if there was one."""
    claimed = claim_next()
    if claimed is None:
        return False
    op, action, who = claimed
    try:
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
        logger.warning("provisioning %s (%s) refused: %s", op.slug, action, exc)
    except Exception:  # recorded on the operation; the worker carries on
        logger.exception("provisioning %s (%s) stopped", op.slug, action)
    finally:
        connection.set_schema_to_public()
    return True


class Command(BaseCommand):
    help = "Run the provisioning operations queued from the operator console."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=float, default=3.0)

    def handle(self, *args, once, interval, **options):
        self.stdout.write("provisioner ready")
        while True:
            close_old_connections()
            did = work_once()
            if once:
                return
            if not did:
                time.sleep(interval)
