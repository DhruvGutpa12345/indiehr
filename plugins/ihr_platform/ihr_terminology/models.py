"""Terminology content (P1-T10a1, ADR-016): code systems, their concepts and designations.

Shared by every hospital, so the tables live in ``public`` (like the PIN directory, P1-T7a). No
hospital table points at them: CARE stores codes as JSON. They are read-only at run time; only
``manage.py ihr_terminology_load`` writes, and a database trigger refuses any other write
(migration 0002). One version of each code system is ``active``; older ones are ``retired``.
"""

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models


class CodeSystemVersion(models.Model):
    class Status(models.TextChoices):
        LOADING = "loading"
        ACTIVE = "active"
        RETIRED = "retired"

    class Content(models.TextChoices):
        COMPLETE = "complete"  # every code of the system
        FRAGMENT = (
            "fragment"  # a subset (UCUM's common units: UCUM itself is a grammar)
        )

    url = models.CharField(max_length=255)  # the FHIR system URL
    version = models.CharField(max_length=64)
    name = models.CharField(max_length=255, default="")
    title = models.CharField(max_length=255, default="")
    content = models.CharField(max_length=16, choices=Content.choices)
    licence = models.TextField(default="")
    source = models.CharField(max_length=255)  # the file or package it came from
    sha256 = models.CharField(max_length=64)
    concepts = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.LOADING
    )
    loaded_by = models.CharField(max_length=128)  # the operator who ran the load
    loaded_on_host = models.CharField(max_length=128, default="")
    loaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("url", "-loaded_at")
        constraints = [
            models.UniqueConstraint(
                fields=["url"],
                condition=models.Q(status="active"),
                name="ihr_term_one_active_version",
            )
        ]

    def __str__(self):
        return f"{self.url}|{self.version} ({self.status})"


class Concept(models.Model):
    system = models.ForeignKey(
        CodeSystemVersion, on_delete=models.CASCADE, related_name="+"
    )
    code = models.CharField(max_length=255)
    display = models.TextField()
    active = models.BooleanField(default=True)
    # Direct is-a parents and every ancestor (ids of Concept rows of the same version)
    parents = ArrayField(models.BigIntegerField(), default=list)
    ancestors = ArrayField(models.BigIntegerField(), default=list)
    properties = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["system", "code"], name="ihr_term_code_once_per_version"
            )
        ]
        indexes = [GinIndex(fields=["ancestors"]), GinIndex(fields=["parents"])]

    def __str__(self):
        return f"{self.code} {self.display}"


class Designation(models.Model):
    concept = models.ForeignKey(
        Concept, on_delete=models.CASCADE, related_name="designations"
    )
    language = models.CharField(max_length=16, default="en")
    use_system = models.CharField(max_length=255, default="")
    use_code = models.CharField(max_length=64, default="")
    value = models.TextField()
    preferred = models.BooleanField(
        default=False
    )  # the preferred term for its language
    # Lower case, words separated by single spaces (search.normalise); searched by word prefix
    search_text = models.TextField()

    class Meta:
        indexes = [models.Index(fields=["concept", "language"])]

    def __str__(self):
        return self.value
