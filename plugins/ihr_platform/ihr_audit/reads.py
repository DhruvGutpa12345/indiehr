# ruff: noqa: SLF001 (Django model metadata is read through _meta)
"""Which patients a request showed (owner: every patient shown, lists and searches included).

While a request runs, every model instance loaded from the database that belongs to a patient
is noted: the patient itself, anything with a ``patient`` link, anything with an ``encounter``
link, anything linked to a record that has a patient link (a payment's account), and files and
drawings attached to a patient's records. At the end these are turned into patient ids with a
few small queries. Nothing is noted outside requests.

"Shown" errs on the side of more: everyone *loaded* while handling the request is listed,
including patients checked but not returned (a phone-number search that matches a relative,
a permission lookup). Missing someone who was shown would be the worse failure.

Reads that never build model instances (``values()``, raw SQL) are not seen; CARE's API
serialises model instances. ``unlinked_models.txt`` lists the CARE models reviewed as not
patient data; a test fails on any other model with no patient link, so new upstream models
get reviewed.
"""

import uuid
from contextvars import ContextVar
from pathlib import Path

from django.apps import apps
from django.db.models.signals import post_init

MAX_PATIENTS = 1000
_seen = ContextVar("ihr_audit_seen", default=None)
UNLINKED_FILE = Path(__file__).with_name("unlinked_models.txt")

# Records that point at their owner by a type and an external id:
# model -> (type field, id field, {type: model whose external_id that is})
_CLINICAL = {
    "patient": "emr.Patient",
    "encounter": "emr.Encounter",
    "consent": "emr.Consent",
    "diagnostic_report": "emr.DiagnosticReport",
    "service_request": "emr.ServiceRequest",
}
BY_REFERENCE = {
    "emr.FileUpload": ("file_type", "associating_id", _CLINICAL),
    "emr.MetaArtifact": ("associating_type", "associating_external_id", _CLINICAL),
    "emr.ReportUpload": (
        "report_type",
        "associating_id",
        {
            "discharge_summary": "emr.Encounter",
            "patient_summary": "emr.Patient",
            "account_report": "emr.Account",
            "encounter_report": "emr.Encounter",
        },
    ),
}
# How to reach the patient from a model that has no link of its own to one
PATIENT_PATH = {"emr.Patient": "id", "emr.Consent": "encounter__patient_id"}


class Seen:
    __slots__ = ("patients", "refs", "via")

    def __init__(self):
        self.patients = set()
        self.via = {}  # model label -> primary keys of rows that name a patient
        self.refs = {}  # model label -> external ids


def patient_field(model):
    """The model's own link to a patient (``patient`` first, then e.g. ``related_patient``)."""
    links = [
        f
        for f in model._meta.concrete_fields
        if f.many_to_one
        and f.related_model is not None
        and f.related_model._meta.label == "emr.Patient"
    ]
    links.sort(key=lambda f: f.name != "patient")
    return links[0] if links else None


def path_to_patient(label):
    if label in PATIENT_PATH:
        return PATIENT_PATH[label]
    return patient_field(apps.get_model(label)).attname


def _links_of(model):
    """How an instance of ``model`` points at patients: a list of (kind, field, target)."""
    label = model._meta.label
    if label == "emr.Patient":
        return [("patient", "id", None)]
    if label in BY_REFERENCE:
        return [("reference", label, None)]
    if field := patient_field(model):
        return [("patient", field.attname, None)]
    fields = model._meta.concrete_fields
    for field in fields:
        if (
            field.name == "encounter"
            and field.many_to_one
            and field.related_model._meta.label == "emr.Encounter"
        ):
            return [("via", field.attname, "emr.Encounter")]
    # Through a record that has its own patient link (a payment's account, a comment's request)
    return [
        ("via", field.attname, field.related_model._meta.label)
        for field in fields
        if field.many_to_one
        and field.related_model is not None
        and field.related_model is not model
        and patient_field(field.related_model) is not None
    ]


_LINKS = {}  # model class -> its links (empty: not patient data), decided on first sight


def _note(sender, instance, **kwargs):
    """``post_init`` for every model. One receiver without a sender, so models CARE
    registers late (several are imported only with the API's URLs) are covered too."""
    seen = _seen.get()
    if seen is None:
        return
    links = _LINKS.get(sender)
    if links is None:
        links = _LINKS[sender] = _links_of(sender._meta.concrete_model)
    if not links:
        return
    values = instance.__dict__  # never a deferred-field load
    for kind, field, target in links:
        if kind == "patient":
            if (value := values.get(field)) is not None:
                seen.patients.add(value)
        elif kind == "via":
            if (value := values.get(field)) is not None:
                seen.via.setdefault(target, set()).add(value)
        else:  # reference: field is the model's label
            type_field, id_field, owners = BY_REFERENCE[field]
            owner, ref = owners.get(values.get(type_field)), values.get(id_field)
            if owner and ref:
                try:
                    seen.refs.setdefault(owner, set()).add(uuid.UUID(str(ref)))
                except ValueError:  # a malformed reference never breaks the request
                    pass


def connect():
    post_init.connect(_note, weak=False, dispatch_uid="ihr_audit_reads")


def unlinked_care_models():
    """CARE models in the clinical app with no patient link (reviewed in UNLINKED_FILE)."""
    return sorted(
        m._meta.label
        for m in apps.get_app_config("emr").get_models()
        if not m._meta.proxy and not _links_of(m)
    )


def start():
    return _seen.set(Seen())


def finish(token):
    """Stop noting and return (patient ids, total) for what the request showed."""
    seen = _seen.get()
    _seen.reset(token)
    if seen is None:
        return [], 0
    patients = set(seen.patients)
    for label, pks in seen.via.items():
        patients.update(
            apps.get_model(label)
            .objects.filter(pk__in=pks)
            .values_list(path_to_patient(label), flat=True)
        )
    for label, refs in seen.refs.items():
        patients.update(
            apps.get_model(label)
            .objects.filter(external_id__in=refs)
            .values_list(path_to_patient(label), flat=True)
        )
    patients.discard(None)
    ordered = sorted(patients)
    return ordered[:MAX_PATIENTS], len(ordered)
