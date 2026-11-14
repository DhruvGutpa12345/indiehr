"""Tenant registry, stored in the shared ``public`` schema (TENANCY_DESIGN.md §3).

A tenant is one legal entity (GSTIN holder) that may run several facilities (Q-T0).
"""

import re
import secrets
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django_tenants.models import DomainMixin, TenantMixin

SCHEMA_NAME_RE = re.compile(r"^h_[a-z0-9]{10}$")
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{2,30}$")
RESERVED_SLUGS = {
    "www",
    "api",
    "app",
    "auth",
    "abdm",
    "console",
    "admin",
    "static",
    "mail",
    "status",  # status.<suffix>/healthz: the outside uptime check (P1-T9a)
    "public",
    "files",  # files.<suffix>: object storage behind Traefik (P1-T8b3)
    "ops",  # ops.<suffix>: kept for operator tools such as dashboards (P1-T9a)
}
_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def generate_schema_name():
    return "h_" + "".join(secrets.choice(_ALPHABET) for _ in range(10))


class Hospital(TenantMixin):
    class Status(models.TextChoices):
        PROVISIONING = "provisioning"
        ACTIVE = "active"
        SUSPENDED = "suspended"
        OFFBOARDING = "offboarding"
        ARCHIVED = "archived"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    slug = models.CharField(max_length=31, unique=True)
    name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PROVISIONING
    )
    created_on = models.DateTimeField(auto_now_add=True)
    # Keycloak organisation of this hospital (TENANCY_DESIGN §4, P1-T3). Set once by
    # ihr_keycloak_sync and never changed: tokens are checked against it (P1-T4).
    keycloak_org_id = models.UUIDField(
        null=True, blank=True, unique=True, editable=False
    )
    # When it first went live (P1-T5a). Never cleared: only a hospital that never went live
    # can be rolled back, and only its slug and addresses are released again.
    activated_at = models.DateTimeField(null=True, blank=True, editable=False)
    # The provisioning operation that built it (P1-T19a review S1): set once, by the owner-run
    # provisioner's reserve step, never by the app (this table is read-only for it). Roll-back
    # and resending the first admin's setup e-mail act only for that operation.
    provisioning_operation = models.UUIDField(null=True, blank=True, editable=False)

    # v1 provisioning: create the schema and run every migration in it (no cloning).
    auto_create_schema = True
    auto_drop_schema = False

    def clean(self):
        if self.schema_name != "public" and not SCHEMA_NAME_RE.match(
            self.schema_name or ""
        ):
            raise ValidationError(
                {"schema_name": "Must match h_ + 10 lowercase letters/digits."}
            )
        if self.schema_name != "public" and (
            not SLUG_RE.match(self.slug or "") or self.slug in RESERVED_SLUGS
        ):
            raise ValidationError({"slug": "Invalid or reserved slug."})
        if (
            not self.pk
            and RetiredAddress.objects.filter(kind="slug", value=self.slug).exists()
        ):
            raise ValidationError({"slug": "This slug belonged to another hospital."})

    def save(self, *args, **kwargs):
        if not self.schema_name:
            self.schema_name = generate_schema_name()
        if self.pk:
            # Web addresses are never renamed or reused (TENANCY_DESIGN.md §6.9): bookmarks,
            # printed QR codes and old links must never reach a different hospital.
            old_slug = (
                Hospital.objects.filter(pk=self.pk)
                .values_list("slug", flat=True)
                .first()
            )
            if old_slug is not None and old_slug != self.slug:
                raise ValidationError({"slug": "A hospital's slug can never change."})
        self.full_clean(exclude=["uuid"])
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.slug} / {self.schema_name})"


class Domain(DomainMixin):
    def save(self, *args, **kwargs):
        if self.pk:
            # An address never moves to another hospital and is never renamed (§6.9).
            old = (
                Domain.objects.filter(pk=self.pk).values("domain", "tenant_id").first()
            )
            if old and (old["domain"], old["tenant_id"]) != (
                self.domain,
                self.tenant_id,
            ):
                raise ValidationError("A hospital's web address can never be changed.")
        elif RetiredAddress.objects.filter(kind="domain", value=self.domain).exists():
            raise ValidationError("This web address belonged to another hospital.")
        super().save(*args, **kwargs)


class RetiredAddress(models.Model):
    """Slugs and web addresses of deleted hospitals, never given to another one (§6.9).

    Filled by the delete signals in ``signals.py``, which also fire for queryset deletes.
    """

    KINDS = (("slug", "slug"), ("domain", "domain"))

    kind = models.CharField(max_length=6, choices=KINDS)
    value = models.CharField(max_length=253)
    retired_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "value"], name="unique_retired_address"
            )
        ]

    def __str__(self):
        return f"{self.kind}:{self.value}"


class TenantOperation(models.Model):
    """One provisioning (or roll-back) run for a hospital, step by step (TENANCY_DESIGN §7.1).

    Resumable: each step records its outcome, and a re-run continues after the last step that
    succeeded. Holds no secrets; ``input`` is the onboarding form.
    """

    class Kind(models.TextChoices):
        PROVISION = "provision"
        ROLLBACK = "rollback"
        # One hospital's part of a fleet run (ihr_fleet_run, P1-T18a); grouped by fleet_run
        UPGRADE = "upgrade"
        # A single-hospital restore from a dump (ihr_hospital_restore, P1-T22b)
        RESTORE = "restore"

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        ROLLED_BACK = "rolled_back"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    hospital = models.ForeignKey(
        Hospital, null=True, blank=True, on_delete=models.SET_NULL
    )
    slug = models.CharField(max_length=31)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    input = models.JSONField(default=dict)
    steps = models.JSONField(default=list)
    result = models.JSONField(default=dict)
    requested_by = models.CharField(max_length=255)
    # Set by the console (P1-T5b): the provisioner process picks the operation up and runs
    # "run" or "rollback". The command line runs operations itself and never queues.
    queued_action = models.CharField(max_length=16, blank=True)
    queued_by = models.CharField(max_length=255, blank=True)
    queued_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    fleet_run = models.ForeignKey(
        "FleetRun",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="operations",
    )

    class Meta:
        constraints = [
            # One open provisioning per hospital address at a time.
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(kind="provision", status__in=["pending", "running"]),
                name="one_open_provisioning_per_slug",
            ),
            # One unfinished restore per hospital (P1-T22b): a failed one is resumed or
            # aborted (rolled_back) before another may start
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(
                    kind="restore", status__in=["pending", "running", "failed"]
                ),
                name="one_open_restore_per_slug",
            ),
        ]

    def __str__(self):
        return f"{self.kind} {self.slug} ({self.status})"


class HospitalSchemaState(models.Model):
    """Which build a hospital's schema was last verified for, and whether it is held for a
    release (P1-T18a, ``ihr_platform/release.py``).

    In its own row, never on ``Hospital``: a full ``Hospital.save()`` (ihr_create_tenant and
    others) must never be able to write an old copy back over it. Written only with
    ``get_or_create`` and queryset ``update()``; ``save()`` refuses to change an existing row.

    - ``schema_fingerprint``: the code fingerprint the schema was verified against (every
      tenant-app migration of that build applied, no unknown one recorded), or "" when it was
      checked and found behind or ahead. The gate serves the hospital only when it equals the
      running build's.
    - ``upgrading_since``: the hold of a release window; set and cleared only by ihr_fleet_run.
    - ``hold_reason``: why it is held (``window``, ``migrate`` or ``roles``).
    - ``restore_operation`` / ``restore_since``: the restore hold (P1-T22b, design "After the
      attack" 4): set by ihr_hospital_restore for its operation and lifted only by it (or its
      --abort). Separate from the release hold: a release, the fleet runner and pack apply
      never clear it, and every check that serves a hospital (``release.schema_ready``,
      ``release.ready_q``) refuses it while it is set.
    """

    hospital = models.OneToOneField(
        Hospital,
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="schema_state",
    )
    schema_fingerprint = models.CharField(max_length=64, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    upgrading_since = models.DateTimeField(null=True, blank=True)
    # Why it is held (release.HOLD_*): a window, a migration, or a refused role sync. A run
    # whose roles then pass releases only a "roles" hold, never a window's (C4/S2).
    hold_reason = models.CharField(max_length=16, blank=True)
    # The restore hold (P1-T22b): the restore operation that holds the hospital, and since when
    restore_operation = models.UUIDField(null=True, blank=True)
    restore_since = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        held = f" held ({self.hold_reason})" if self.upgrading_since else ""
        if self.restore_operation:
            held += f" held for restore {self.restore_operation}"
        return f"{self.hospital_id}: {self.schema_fingerprint[:12] or '-'}{held}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            msg = "HospitalSchemaState is changed only with update() (release.py)."
            raise RuntimeError(msg)
        super().save(*args, **kwargs)


class FleetRun(models.Model):
    """One run of ``ihr_fleet_run`` over the fleet (P1-T18a). Each hospital's part is a
    TenantOperation of kind ``upgrade`` pointing here; ``--resume <id>`` continues a run."""

    class Status(models.TextChoices):
        RUNNING = "running"
        FINISHED = "finished"  # every selected hospital done
        INCOMPLETE = "incomplete"  # some hospital failed, was deferred or stays held
        FAULT = "fault"  # stopped for the whole fleet (a check failed, a crash)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    steps = models.JSONField(default=list)
    selection = models.JSONField(default=dict)  # statuses, slugs, resumed hospitals
    parallel = models.PositiveSmallIntegerField(default=2)
    operator = models.CharField(max_length=150)
    startup = models.BooleanField(default=False)
    tenant_fingerprint = models.CharField(max_length=64)
    public_fingerprint = models.CharField(max_length=64)
    app_version = models.CharField(max_length=64, blank=True)
    resumed_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.RUNNING
    )
    counts = models.JSONField(default=dict)  # outcome -> hospitals
    error = models.CharField(max_length=400, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"fleet run {self.id} ({self.status})"


class MaintenanceWindow(models.Model):
    """An announced release window (P1-T18a; set by ``ihr_fleet_run window``). Public data:
    the gate's upgrading notice shows it, so staff know when to expect their hospital back."""

    start = models.DateTimeField()
    minutes = models.PositiveIntegerField()
    note = models.CharField(max_length=200, blank=True)
    created_by = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-start",)

    def __str__(self):
        return f"window {self.start:%d-%m-%Y %H:%M} +{self.minutes} min"

    @property
    def end(self):
        from datetime import timedelta

        return self.start + timedelta(minutes=self.minutes)


class OperatorAccount(models.Model):
    """A platform operator allowed into the console (P1-T5b, TENANCY_DESIGN §4.9).

    Two locks: the person must hold the ``ihr-operator`` role in the operator realm *and*
    be on this list, which only the ``ihr_operator`` command changes. Operators have no CARE
    user in any hospital.
    """

    keycloak_sub = models.UUIDField(unique=True)
    name = models.CharField(max_length=255)
    active = models.BooleanField(default=True)
    added_by = models.CharField(max_length=255)
    added_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({'active' if self.active else 'removed'})"


class StaffInvitation(models.Model):
    """A hospital's invitation to one person (P1-T5e1, TENANCY_DESIGN §4.5).

    Written by the hospital's web app when an admin adds a user in CARE (the user is created
    switched off); delivered by the provisioner, the only process holding the Keycloak admin
    credential. A new address gets Keycloak's one-time setup e-mail; an address that already
    has an account gets a "join this hospital" e-mail whose link must be followed by a sign-in
    and a confirmation. Only a hash of that link's token is stored.
    """

    class Status(models.TextChoices):
        QUEUED = "queued"
        SENT = "sent"
        ACCEPTED = "accepted"
        FAILED = "failed"
        CANCELLED = "cancelled"  # by a hospital admin (P1-T5e2)
        EXPIRED = "expired"  # not accepted in time; what it granted is withdrawn

    class Kind(models.TextChoices):
        NEW = "new"  # no account yet: the setup e-mail (P1-T5c's mechanism)
        JOIN = "join"  # an existing account: sign in and confirm

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE)
    care_user_id = models.IntegerField()  # in the hospital's schema
    email = models.EmailField()
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150, blank=True)
    invited_by = models.CharField(max_length=255)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    kind = models.CharField(max_length=8, choices=Kind.choices, blank=True)
    keycloak_sub = models.UUIDField(null=True, blank=True)
    join_token_hash = models.CharField(max_length=64, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    not_before = models.DateTimeField(null=True, blank=True)  # retry after a failure
    error = models.CharField(max_length=400, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["hospital", "care_user_id"], name="one_invitation_per_user"
            )
        ]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self):
        return f"{self.kind or '?'} invitation at {self.hospital_id} ({self.status})"


class StaffAccessRequest(models.Model):
    """A hospital admin's request about one staff member's sign-in (P1-T5e2), carried out by
    the provisioner (the only holder of the Keycloak admin credential) and kept as that
    hospital's record of who asked for what, and when.

    - ``reset_sign_in``: lost authenticator. The person's password and authenticator are both
      cleared (clearing only the authenticator would let anyone with the password enrol their
      own), every sign-in ends, and a one-time link to set both again goes to their own
      verified e-mail. The admin never sees it.
    - ``resend_invitation`` / ``cancel_invitation``: for invitations not accepted yet.
    """

    class Action(models.TextChoices):
        RESET_SIGN_IN = "reset_sign_in"
        RESEND_INVITATION = "resend_invitation"
        CANCEL_INVITATION = "cancel_invitation"

    class Status(models.TextChoices):
        QUEUED = "queued"
        # Claimed by the provisioner. Never retried by itself: a reset wipes credentials and
        # sends e-mail, so a crash mid-way is left for the admin to ask again.
        RUNNING = "running"
        DONE = "done"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE)
    care_user_id = models.IntegerField()
    # The person (one account across hospitals): resets are limited per person, not per
    # hospital, so admins at several hospitals cannot keep wiping one doctor's sign-in.
    keycloak_sub = models.UUIDField(null=True, blank=True, db_index=True)
    action = models.CharField(max_length=24, choices=Action.choices)
    requested_by = models.CharField(max_length=255)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    error = models.CharField(max_length=400, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["hospital", "care_user_id", "action"],
                condition=models.Q(status__in=["queued", "running"]),
                name="one_open_request_per_user_and_action",
            )
        ]

    def __str__(self):
        return f"{self.action} at {self.hospital_id} ({self.status})"


class PinDirectoryLoad(models.Model):
    """One load of India Post's All India Pincode Directory (data.gov.in, GODL-India; P1-T7a).

    The directory is the same for every hospital, so it lives here in ``public``. The newest
    row describes the offices in ``PostOffice``; older rows are the load history.
    """

    source_name = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    released_on = models.DateField(null=True, blank=True)  # the date data.gov.in shows
    offices = models.PositiveIntegerField()
    pincodes = models.PositiveIntegerField()
    skipped = models.PositiveIntegerField(default=0)
    loaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-loaded_at", "-id")

    def __str__(self):
        return f"{self.source_name} ({self.pincodes} PIN codes)"


class PostOffice(models.Model):
    """A post office from the PIN directory: which state and district a PIN code is in."""

    pincode = models.CharField(max_length=6, db_index=True)
    name = models.CharField(max_length=255)
    office_type = models.CharField(max_length=8, blank=True)  # B.O, S.O, H.O
    delivery = models.BooleanField(default=True)
    district = models.CharField(max_length=100)
    state_code = models.CharField(
        max_length=2
    )  # GST state code (ihr_tenant_settings.india)

    class Meta:
        indexes = [models.Index(fields=["state_code", "district"])]

    def __str__(self):
        return f"{self.pincode} {self.name}"
