"""Run, resume and roll back hospital provisioning (P1-T5a, TENANCY_DESIGN §7.1).

One run per hospital at a time: a Postgres advisory lock is held for the whole run, so a
second command (or, later, a re-delivered background job) refuses instead of racing.
"""

import hashlib
import time
from contextlib import contextmanager

from django.db import connection, connections
from django.utils import timezone

from ihr_platform.models import SCHEMA_NAME_RE, Domain, Hospital, TenantOperation
from ihr_platform.provisioning import steps

MAX_ERROR = 300
# A crashed run stays "running"; the lock above is what keeps two runs apart.
OPEN = {
    TenantOperation.Status.PENDING,
    TenantOperation.Status.FAILED,
    TenantOperation.Status.RUNNING,
}
# The first admin's personal details, dropped once a roll-back has made them pointless.
PERSONAL_FIELDS = ("admin_email", "admin_first_name", "admin_last_name", "phone")


class ProvisioningError(Exception):
    pass


def start(form, requested_by):
    """Record a new provisioning operation for ``form`` (an OnboardingForm)."""
    form.validate()
    return TenantOperation.objects.create(
        kind=TenantOperation.Kind.PROVISION,
        slug=form.slug,
        input=form.as_dict(),
        requested_by=requested_by,
        steps=[{"name": name, "status": "pending"} for name in steps.STEP_NAMES],
    )


@contextmanager
def _exclusive(slug):
    """Hold a per-hospital lock for the whole run, on a connection of its own: the
    migrations close Django's main connection, which would silently drop a lock held there."""
    key = int.from_bytes(hashlib.sha256(slug.encode()).digest()[:8], "big", signed=True)
    holder = connections.create_connection("default")
    try:
        with holder.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
            if not cursor.fetchone()[0]:
                msg = (
                    f"Another run is working on {slug}; try again when it has finished."
                )
                raise ProvisioningError(msg)
        yield
    finally:
        holder.close()  # ends that session, which releases the lock


def is_busy(slug):
    """True while some run holds this hospital's lock."""
    try:
        with _exclusive(slug):
            return False
    except ProvisioningError:
        return True


def _safe_error(exc):
    """The exception's class and a short message (operators see it; no secrets in it)."""
    text = str(exc).replace("\n", " ")
    return f"{type(exc).__name__}: {text[:MAX_ERROR]}"


def run(op, stop_after=None):
    """Run every step that has not succeeded yet, in order. Returns the operation."""
    if op.kind != TenantOperation.Kind.PROVISION:
        raise ProvisioningError("Not a provisioning operation.")
    if op.status == TenantOperation.Status.SUCCEEDED:
        return op
    with _exclusive(op.slug):
        op.refresh_from_db()
        if op.status not in OPEN:
            msg = f"This operation is {op.status}; start a new one instead."
            raise ProvisioningError(msg)
        form = steps.OnboardingForm(**op.input)
        op.status = TenantOperation.Status.RUNNING
        op.started_at = op.started_at or timezone.now()
        op.save()
        for record, (name, step) in zip(op.steps, steps.STEPS, strict=True):
            if record["status"] == "succeeded":
                continue
            record.update(status="running", started_at=timezone.now().isoformat())
            record.pop("error", None)
            op.save(update_fields=["steps", "status", "started_at"])
            began = time.monotonic()
            try:
                connection.set_schema_to_public()
                step(op, form)
            except Exception as exc:
                connection.set_schema_to_public()
                record.update(status="failed", error=_safe_error(exc))
                record["seconds"] = round(time.monotonic() - began, 1)
                op.status = TenantOperation.Status.FAILED
                op.save()
                raise
            connection.set_schema_to_public()
            record.update(status="succeeded", finished_at=timezone.now().isoformat())
            record["seconds"] = round(time.monotonic() - began, 1)
            op.save()
            if name == stop_after:
                op.status = TenantOperation.Status.PENDING
                op.save(update_fields=["status"])
                return op
        op.status = TenantOperation.Status.SUCCEEDED
        op.finished_at = timezone.now()
        op.save()
    return op


def rollback(op, requested_by):
    """Undo a provisioning whose hospital never went live: its Keycloak objects, its schema
    and its addresses (released again, owner's choice). A live hospital is never touched
    here; taking one down is offboarding (TENANCY_DESIGN §7.7)."""
    if op.kind != TenantOperation.Kind.PROVISION:
        raise ProvisioningError("Only a provisioning operation can be rolled back.")
    if op.status == TenantOperation.Status.ROLLED_BACK:
        return op
    with _exclusive(op.slug):
        op.refresh_from_db()
        hospital = Hospital.objects.filter(pk=op.hospital_id).first()
        if hospital is not None:
            _check_rollback_target(op, hospital)
        record = TenantOperation.objects.create(
            kind=TenantOperation.Kind.ROLLBACK,
            hospital=hospital,
            slug=op.slug,
            requested_by=requested_by,
            status=TenantOperation.Status.RUNNING,
            started_at=timezone.now(),
            input={"provisioning": str(op.id)},
        )
        try:
            if hospital is not None:
                keycloak_step = next(r for r in op.steps if r["name"] == "keycloak")
                # Removal is stamp-checked and idempotent; also run it when another tool
                # (ihr_keycloak_sync) may have created the hospital's Keycloak objects.
                if keycloak_step["status"] != "pending" or hospital.keycloak_org_id:
                    _remove_keycloak(hospital)
                for domain in Domain.objects.filter(tenant=hospital):
                    domain.ihr_release_addresses = True  # never served anyone
                    domain.delete()
                hospital.ihr_release_addresses = True
                hospital.delete(force_drop=True)
        except Exception as exc:
            record.status = TenantOperation.Status.FAILED
            record.result = {"error": _safe_error(exc)}
            record.finished_at = timezone.now()
            record.save()
            raise
        op.input = {k: v for k, v in op.input.items() if k not in PERSONAL_FIELDS}
        for model_op in (op, record):
            model_op.hospital = None  # the hospital row is gone
            model_op.status = TenantOperation.Status.ROLLED_BACK
            model_op.finished_at = timezone.now()
            model_op.save()
    return op


def _check_rollback_target(op, hospital):
    if (
        str(hospital.uuid) != op.result.get("hospital_uuid")
        or hospital.schema_name != op.result.get("schema_name")
        or not SCHEMA_NAME_RE.match(hospital.schema_name)
    ):
        raise ProvisioningError("This operation does not match the hospital it names.")
    if (
        hospital.activated_at is not None
        or hospital.status != Hospital.Status.PROVISIONING
    ):
        raise ProvisioningError(
            "This hospital has been live; it can only be offboarded, not rolled back."
        )


def _remove_keycloak(hospital):
    from django.core.management.base import CommandError

    from ihr_platform.keycloak.hospital import HospitalLogin, remove_hospital
    from ihr_platform.management.commands.ihr_keycloak_sync import (
        hospital_login,
        keycloak_admin,
    )

    try:
        login = hospital_login(hospital)
    except CommandError:  # no address yet: only the slug and uuid matter here
        login = HospitalLogin(
            hospital.slug, hospital.name, ("",), (), uuid=str(hospital.uuid)
        )
    remove_hospital(keycloak_admin(), login)
