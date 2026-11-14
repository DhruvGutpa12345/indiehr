"""Run, resume and roll back hospital provisioning (P1-T5a, TENANCY_DESIGN §7.1).

One run per hospital at a time: a Postgres advisory lock is held for the whole run, so a
second command (or, later, a re-delivered background job) refuses instead of racing.
"""

import hashlib
import time
from contextlib import contextmanager

from django.db import connection, connections
from django.utils import timezone

from ihr_platform import release
from ihr_platform.models import SCHEMA_NAME_RE, Domain, Hospital, TenantOperation
from ihr_platform.provisioning import steps

# Steps after this one work in the schema: it must be this build's first (P1-T18a)
SCHEMA_STEP = steps.STEP_NAMES.index("schema")
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


class HospitalUpgrading(ProvisioningError):  # noqa: N818 (a state, not a failure)
    """The hospital is held for a release (ihr_fleet_run): try again once it is released."""


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


def lock_key(slug):
    """The hospital's advisory lock key. The nightly dumper (deploy/dumps/dumper.py, no Django)
    has its own copy of it: both are held together by a pinned vector,
    lock_key("alpha") == -8154903275597228642 (here ihr_platform.tests.test_provisioning; there
    scripts/test_dumps.py), so a dump and a whole-hospital job never run together."""
    return int.from_bytes(
        hashlib.sha256(slug.encode()).digest()[:8], "big", signed=True
    )


@contextmanager
def exclusive(slug):
    """Hold a per-hospital lock for the whole run, on a connection of its own: the
    migrations close Django's main connection, which would silently drop a lock held there.
    Also taken by every other job that changes a hospital as a whole (reference packs,
    ihr_refdata) and, as the same key, by the nightly dumper while pg_dump runs; it never
    waits: a second holder gets ProvisioningError at once."""
    key = lock_key(slug)
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


_exclusive = exclusive  # the name the tests and older callers use


def is_busy(slug):
    """True while some run holds this hospital's lock."""
    try:
        with exclusive(slug):
            return False
    except ProvisioningError:
        return True


def _safe_error(exc):
    """What operators see of a failure: never row values or secrets (release.safe_error)."""
    return release.safe_error(exc)


def built_by(op, hospital):
    """Refuse unless ``op`` is the operation the owner recorded as the one that built
    ``hospital`` (P1-T19a review S1). The app may insert operations, and an operation row says
    which hospital it names; Hospital.provisioning_operation is written only by the owner-run
    reserve step, so a forged or copied operation never reaches a hospital it did not build."""
    if hospital is None or hospital.provisioning_operation != op.pk:
        raise ProvisioningError("This operation did not build the hospital it names.")


def _check_not_held(hospital_id):
    hospital = Hospital.objects.filter(pk=hospital_id).first()
    row = release.recorded(hospital) if hospital is not None else None
    if row is not None and row[1] is not None:
        msg = f"{hospital.slug} is held for a release; try again once it is released."
        raise HospitalUpgrading(msg)
    return hospital


def _schema_is_this_builds(op, form, record):
    """A run resumed across a release must never go on with an older schema (C3, O6): if the
    schema was not verified for this build, the schema step runs again (a no-op migrate when
    nothing is missing), which records it."""
    hospital = Hospital.objects.filter(pk=op.hospital_id).first()
    row = release.recorded(hospital) if hospital is not None else None
    if hospital is None or (row is not None and row[0] == release.code_fingerprint()):
        return
    connection.set_schema_to_public()
    steps.schema(op, form)
    connection.set_schema_to_public()
    record["migrated_again_at"] = timezone.now().isoformat()


def run(op, stop_after=None):
    """Run every step that has not succeeded yet, in order. Returns the operation."""
    if op.kind != TenantOperation.Kind.PROVISION:
        raise ProvisioningError("Not a provisioning operation.")
    if op.status == TenantOperation.Status.SUCCEEDED:
        return op
    with exclusive(op.slug):
        op.refresh_from_db()
        if op.status not in OPEN:
            msg = f"This operation is {op.status}; start a new one instead."
            raise ProvisioningError(msg)
        if op.hospital_id:
            built_by(op, _check_not_held(op.hospital_id))
        form = steps.OnboardingForm(**op.input)
        # Operations recorded before a step was added get it, pending, in its place.
        by_name = {record["name"]: record for record in op.steps}
        op.steps = [
            by_name.get(name, {"name": name, "status": "pending"})
            for name in steps.STEP_NAMES
        ]
        op.status = TenantOperation.Status.RUNNING
        op.started_at = op.started_at or timezone.now()
        op.save()
        schema_record = op.steps[SCHEMA_STEP]
        for index, (record, (name, step)) in enumerate(
            zip(op.steps, steps.STEPS, strict=True)
        ):
            if record["status"] == "succeeded":
                continue
            record.update(status="running", started_at=timezone.now().isoformat())
            record.pop("error", None)
            op.save(update_fields=["steps", "status", "started_at"])
            began = time.monotonic()
            try:
                connection.set_schema_to_public()
                if index > SCHEMA_STEP:
                    _schema_is_this_builds(op, form, schema_record)
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
    with exclusive(op.slug):
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
                account_step = next(
                    (r for r in op.steps if r["name"] == "admin_account"), None
                )
                if account_step and account_step["status"] != "pending":
                    _remove_first_admin_account(op, hospital)
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
    built_by(op, hospital)
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


def resend_setup(op):
    """E-mail a live hospital's first admin their setup link again (console, command line)."""
    if (
        op.kind != TenantOperation.Kind.PROVISION
        or op.status != TenantOperation.Status.SUCCEEDED
    ):
        raise ProvisioningError("Only a finished provisioning has a setup e-mail.")
    with exclusive(op.slug):
        op.refresh_from_db()
        hospital = _check_not_held(op.hospital_id)
        built_by(op, hospital)
        if not release.schema_ready(hospital):
            msg = f"{op.slug} is not on this release's schema yet; try again later."
            raise HospitalUpgrading(msg)
        try:
            sent = steps.resend_setup(op)
        except Exception as exc:
            # Shown in the console next to the setup e-mail; the worker only logs.
            _note_resend(op, f"Sending again failed: {_safe_error(exc)}")
            raise
        if not sent:
            _note_resend(op, "Nothing sent: the first admin has already set up.")
            raise ProvisioningError(
                "Nothing to send: the first admin has already set up their account."
            )
        _note_resend(op, None)
    return op


def _note_resend(op, problem):
    op.refresh_from_db(fields=["result"])
    op.result["setup_resend_problem"] = problem
    if problem is None:
        op.result.pop("setup_resend_problem")
    op.save(update_fields=["result"])


def _remove_first_admin_account(op, hospital):
    """The account made for the first admin; never sent a link, since the hospital never
    went live. Only an account stamped with this hospital and never set up is removed. It
    is also found by its address and stamp, in case a crash came before its id was saved."""
    from ihr_platform.keycloak.invite import find_invited_by_email, remove_invited_user
    from ihr_platform.management.commands.ihr_keycloak_sync import keycloak_admin

    admin = keycloak_admin(purpose="onboarding")
    tag = str(hospital.uuid)
    email = op.input.get("admin_email", "")
    sub = op.result.get("admin_keycloak_sub") or (
        email and find_invited_by_email(admin, email, tag)
    )
    if sub:
        remove_invited_user(admin, sub, tag)


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
    remove_hospital(keycloak_admin(purpose="onboarding"), login)
