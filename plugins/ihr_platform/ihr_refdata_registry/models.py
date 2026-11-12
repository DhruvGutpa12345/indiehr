"""Which reference pack releases exist, for the whole fleet (P1-T15a, TENANCY_DESIGN §3, §5).

One row per (pack, version), in ``public``: its sha256, the CARE commit it was built against
and its item count. Versions are immutable: a release seen again with another sha256 is
refused (``ihr_refdata.engine.prepare``). No hospital or patient data; each hospital's own
state lives in its schema (``ihr_refdata``).
"""

from django.db import models


class PackRelease(models.Model):
    pack = models.CharField(max_length=64)
    version = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    care_commit = models.CharField(max_length=40, blank=True, default="")
    item_count = models.PositiveIntegerField()
    loaded_at = models.DateTimeField(auto_now_add=True)
    loaded_by = models.CharField(max_length=150)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["pack", "version"], name="ihr_packrelease_one_per_version"
            )
        ]

    def __str__(self):
        return f"{self.pack} v{self.version}"
