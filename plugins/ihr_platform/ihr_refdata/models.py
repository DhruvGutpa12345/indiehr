"""Each hospital's own record of the reference packs applied to it (P1-T15a).

``PackItem``: one row per item a pack has ever touched here (for the value set pack: one per
slug), with the content hash the pack last wrote and whether the hospital's row still matches
it. ``PackApply``: one append-only row per apply that changed something, reached a newer
release with nothing to write, failed or had to wait. Both live in the hospital's schema and are audited like every hospital table.
"""

from django.db import models


class PackItem(models.Model):
    class State(models.TextChoices):
        CURRENT = "current"  # the row holds what the pack last wrote
        PENDING = "pending"  # a pack change waits for the hospital admin's OK
        KEPT = "kept"  # the hospital chose its own version over the pending one
        RETIRED = "retired"  # left the pack: marked retired, never deleted

    class Reason(models.TextChoices):
        NONE = ""
        EDITED = "edited"  # the row differs from what this pack last wrote
        UNKNOWN_ORIGIN = "unknown-origin"  # differs, and this pack never wrote it

    pack = models.CharField(max_length=64)
    kind = models.CharField(max_length=32)
    item = models.CharField(max_length=255)  # the value set's slug
    state = models.CharField(max_length=16, choices=State.choices)
    reason = models.CharField(
        max_length=32, choices=Reason.choices, blank=True, default=""
    )
    # What this pack last wrote (or found already there) in the row: version and content hash
    applied_version = models.PositiveIntegerField(null=True, blank=True)
    applied_sha256 = models.CharField(max_length=64, blank=True, default="")
    # The pack version waiting (pending) or declined (kept), and the hash of its content
    # ("" when that version drops the item: accepting it retires the row). The content itself
    # is never stored here: accept reads it again from the verified pack file.
    pending_version = models.PositiveIntegerField(null=True, blank=True)
    pending_sha256 = models.CharField(max_length=64, blank=True, default="")
    # The hospital's own row as it was when kept. Only recorded (the engine never compares
    # against it): the P1-T15b screen and the audit trail show what the hospital chose to keep
    kept_row_sha256 = models.CharField(max_length=64, blank=True, default="")
    changed_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["pack", "kind", "item"], name="ihr_packitem_one_per_item"
            )
        ]

    def __str__(self):
        return f"{self.pack}:{self.item} ({self.state})"


class PackApply(models.Model):
    class Outcome(models.TextChoices):
        APPLIED = "applied"  # this version's content was applied (something changed)
        REACHED = "reached"  # a newer version that had nothing to write here
        FAILED = "failed"  # nothing changed; the error is kept (safe text)
        DEFERRED = "deferred"  # another job held the hospital; the next run applies it
        ACCEPTED = "accepted"  # an operator applied one pending item
        KEPT = "kept"  # an operator kept the hospital's own version of one item

    pack = models.CharField(max_length=64)
    version = models.PositiveIntegerField(null=True, blank=True)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    care_commit = models.CharField(max_length=40, blank=True, default="")
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    applied_at = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=150)
    counts = models.JSONField(default=dict)
    error = models.CharField(max_length=400, blank=True, default="")

    def __str__(self):
        return f"{self.pack} v{self.version} {self.outcome}"
