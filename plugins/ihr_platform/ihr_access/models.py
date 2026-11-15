"""Records grants: a time-boxed clinical read of one patient by one MRD user (design-b.md §7).

P1-T11b4 builds the flow (the endpoint, the end routine, expiry, audit context and alerts). The
table comes first, in P1-T11b1, because two controls read it: the placement trigger refuses a
``MRD Records Access`` PatientUser row without a live grant (placement.py), and the PatientUser
read-time rule counts such a row only while its grant is live (authz.py).
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class RecordsGrant(models.Model):
    class Reason(models.TextChoices):
        PATIENT_REQUEST = "patient_request"
        LEGAL = "legal"
        INSURER = "insurer"
        INTERNAL_AUDIT = "internal_audit"
        NABH_REVIEW = "nabh_review"
        CONTINUITY_OF_CARE = "continuity_of_care"

    # PROTECT: a grant is part of the record of who read what (the trail keeps it too)
    patient = models.ForeignKey(
        "emr.Patient", on_delete=models.PROTECT, related_name="+"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    reason = models.CharField(max_length=32, choices=Reason.choices)
    reference = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ihr_records_grant"
        indexes = [
            models.Index(
                fields=["user", "patient", "expires_at"],
                name="ihr_records_grant_live",
                condition=Q(ended_at__isnull=True),
            )
        ]

    def __str__(self):
        # Ids only: no patient or user name in logs and error messages
        return (
            f"records grant {self.pk} (patient {self.patient_id}, user {self.user_id})"
        )

    @classmethod
    def live(cls):
        """Grants in force now (the same rule as the placement trigger's)."""
        return cls.objects.filter(ended_at__isnull=True, expires_at__gt=timezone.now())
