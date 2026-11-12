"""Rows the demo fixtures do not create, seeded so the crawl reaches their detail routes (P1-T16b).

CARE's fixture script seeds no row of these models, so their detail routes had no id to fill
and were listed in crawl_skips.txt. Here each gets one row per tenant, with the ORM:

- every value is a valid CARE choice, copied from CARE's own test helpers (``care/emr/tests``),
  because a random value would break CARE's read specs;
- each row hangs off the FIRST row by pk of every parent, because ``routes.fill()`` takes that
  row's id for the parent's place in the path, and a child of another parent answers a silent
  404;
- the tenant's canary goes into a free-text field (title, note, name, message ...) wherever the
  model has one, as a second marker beside the row's ids.

Each row's detail route is recorded on the tenant (``seeded_routes``) as ``(path,
canary_field)``. ``test_every_seeded_and_converter_route_answers_its_owner`` checks that each
answers its own tenant's admin with 200 and carries one of the tenant's own ids: that proves the
route is reached with its owner's row, not where the canary is rendered (most bodies also nest a
canaried staff or patient name). Where ``canary_field`` is set (the route's CARE read spec was
checked to render that top-level field, and a run confirmed it), the test also requires the
canary in that field; where it is None (the read spec has no field that takes it), nothing
relies on it. Nothing is asserted in this module (docs/reviews/p1-t16/design.md, A3).

Any error while seeding is re-raised naming the model ("extras: seeding <Model> failed (CARE
model drift?)"), so an upstream sync that changes a CARE model shows up as that, not as a bare
traceback in the isolation suite's set-up.

Not seeded, and still in crawl_skips.txt with the reason: FacilityLocationEncounter (CARE refuses
an encounter on a location "kind", and the first location, which fill() uses, is Ward A, a
kind).
"""

from contextlib import contextmanager
from datetime import date, time

from django.utils import timezone
from django_tenants.utils import schema_context

# A synthetic coding for the medication rows (as CARE's tests use a test coding).
MEDICATION = {
    "system": "http://snomed.info/sct",
    "code": "387517004",
    "display": "Paracetamol",
}
LAB = {
    "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
    "code": "LAB",
    "display": "Laboratory",
}
BLOOD = {
    "system": "http://terminology.hl7.org/CodeSystem/v2-0487",
    "code": "BLD",
    "display": "Whole blood",
}
PLUG_SLUG = "ihr-iso-probe"


def _first(model, **filters):
    """The first row by pk; a missing parent is drift too, so it fails naming the model."""
    with _seeding(model):
        row = model.objects.filter(**filters).order_by("pk").first()
        if row is None:
            raise LookupError(f"no {model.__name__} row {filters or ''}".strip())
    return row


@contextmanager
def _seeding(model):
    """Re-raise any error with the model's name: in this module that means CARE model drift."""
    try:
        yield
    except Exception as exc:
        msg = f"extras: seeding {model.__name__} failed (CARE model drift?)"
        raise RuntimeError(msg) from exc


def seed_extras(tenant):
    """Seed one row of each model behind a skipped detail route, in ``tenant``'s schema."""
    with schema_context(tenant.schema):
        _Seeder(tenant).run()


class _Seeder:
    def __init__(self, tenant):
        from care.emr.models.encounter import Encounter
        from care.emr.models.patient import Patient
        from care.facility.models import Facility
        from care.users.models import User

        self.tenant = tenant
        self.canary = tenant.canary
        self.now = timezone.now()
        self.admin = User.objects.get(username="admin")
        self.facility = _first(Facility)
        self.patient = _first(Patient)
        # The demo fixtures make each patient's encounter in patient order at the first
        # facility, so the first encounter is the first patient's: a mismatch would show as
        # a 404 in the owner test, never as a silent pass.
        self.encounter = _first(Encounter)

    def route(self, path, *, canary):
        """Record ``path`` with ``canary``: the top-level field of its read spec that renders
        the canary (checked against CARE's read spec and a run), or None if none does."""
        self.tenant.seeded_routes.append((path, canary))

    def make(self, model, **values):
        with _seeding(model):
            return model.objects.create(
                created_by=self.admin, updated_by=self.admin, **values
            )

    def run(self):
        self.clinical()
        self.files()
        self.operations()

    # --- clinical ------------------------------------------------------------------------

    def clinical(self):
        from care.emr.models.allergy_intolerance import AllergyIntolerance
        from care.emr.models.condition import Condition
        from care.emr.models.consent import Consent
        from care.emr.models.diagnostic_report import DiagnosticReport
        from care.emr.models.medication_administration import MedicationAdministration
        from care.emr.models.medication_request import (
            MedicationRequest,
            MedicationRequestPrescription,
        )
        from care.emr.models.medication_statement import MedicationStatement
        from care.emr.models.notes import NoteMessage, NoteThread
        from care.emr.models.observation import Observation
        from care.emr.models.questionnaire import (
            FormSubmission,
            Questionnaire,
            QuestionnaireResponse,
        )
        from care.emr.models.service_request import ServiceRequest
        from care.emr.models.specimen import Specimen

        f, p, e, canary = self.facility, self.patient, self.encounter, self.canary
        facility, patient = f.external_id, p.external_id
        common = {"patient": p, "encounter": e}

        service_request = self.make(
            ServiceRequest,
            facility=f,
            title=f"{canary} probe",
            category="laboratory",
            status="active",
            intent="order",
            priority="routine",
            note=canary,
            code=LAB,
            requester=self.admin,
            **common,
        )
        self.route(
            f"/api/v1/facility/{facility}/service_request/{service_request.external_id}/",
            canary="note",
        )

        specimen = self.make(
            Specimen,
            facility=f,
            accession_identifier=f"{canary}-1",
            status="available",
            specimen_type=BLOOD,
            service_request=service_request,
            note=canary,
            **common,
        )
        self.route(
            f"/api/v1/facility/{facility}/specimen/{specimen.external_id}/",
            canary="note",
        )

        report = self.make(
            DiagnosticReport,
            facility=f,
            status="final",
            category=LAB,
            service_request=service_request,
            note=canary,
            **common,
        )
        self.route(
            f"/api/v1/patient/{patient}/diagnostic_report/{report.external_id}/",
            canary="note",
        )

        observation = self.make(
            Observation,
            status="final",
            value_type="integer",
            value={"value": "55"},
            main_code={"system": "http://loinc.org", "code": "8480-6"},
            effective_datetime=self.now,
            diagnostic_report=report,
            subject_type="encounter",
            subject_id=e.external_id,
            note=canary,
            **common,
        )
        self.route(
            f"/api/v1/patient/{patient}/observation/{observation.external_id}/",
            canary="note",
        )

        # A problem-list item is both a symptom (SymptomViewSet filters on that category)
        # and a diagnosis (DiagnosisViewSet takes every category).
        condition = self.make(
            Condition,
            category="problem_list_item",
            clinical_status="active",
            verification_status="confirmed",
            severity="mild",
            code={"system": "http://snomed.info/sct", "code": "386661006"},
            recorded_date=self.now,
            note=canary,
            **common,
        )
        self.route(
            f"/api/v1/patient/{patient}/symptom/{condition.external_id}/", canary="note"
        )
        self.route(
            f"/api/v1/patient/{patient}/diagnosis/{condition.external_id}/",
            canary="note",
        )

        allergy = self.make(
            AllergyIntolerance,
            clinical_status="active",
            verification_status="confirmed",
            category="medication",
            criticality="low",
            code=MEDICATION,
            recorded_date=self.now,
            note=canary,
            **common,
        )
        self.route(
            f"/api/v1/patient/{patient}/allergy_intolerance/{allergy.external_id}/",
            canary="note",
        )

        consent = self.make(
            Consent,
            encounter=e,
            status="active",
            category="treatment",
            date=self.now,
            decision="permit",
            note=canary,
        )
        self.route(
            f"/api/v1/patient/{patient}/consent/{consent.external_id}/", canary="note"
        )

        prescription = self.make(
            MedicationRequestPrescription,
            name="probe",
            note=canary,
            status="active",
            prescribed_by=self.admin,
            **common,
        )
        self.route(
            f"/api/v1/patient/{patient}/medication/prescription/"
            f"{prescription.external_id}/",
            canary="note",
        )
        request = self.make(
            MedicationRequest,
            status="active",
            intent="order",
            category="inpatient",
            priority="routine",
            do_not_perform=False,
            medication=MEDICATION,
            dosage_instruction=[],
            authored_on=self.now,
            requester=self.admin,
            prescription=prescription,
            note=canary,
            **common,
        )
        self.route(
            f"/api/v1/patient/{patient}/medication/request/{request.external_id}/",
            canary="note",
        )
        administration = self.make(
            MedicationAdministration,
            status="completed",
            category="inpatient",
            medication=MEDICATION,
            request=request,
            authored_on=self.now,
            occurrence_period_start=self.now,
            note=canary,
            **common,
        )
        self.route(
            f"/api/v1/patient/{patient}/medication/administration/"
            f"{administration.external_id}/",
            canary="note",
        )
        statement = self.make(
            MedicationStatement,
            status="active",
            medication=MEDICATION,
            information_source="patient",
            effective_period={"start": self.now.isoformat()},
            note=canary,
            **common,
        )
        self.route(
            f"/api/v1/patient/{patient}/medication/statement/{statement.external_id}/",
            canary="note",
        )
        self.request = request

        questionnaire = _first(Questionnaire)
        submission = self.make(
            FormSubmission,
            questionnaire=questionnaire,
            status="submitted",
            **common,
        )
        self.route(f"/api/v1/form_submission/{submission.external_id}/", canary=None)
        response = self.make(
            QuestionnaireResponse,
            questionnaire=questionnaire,
            subject_id=e.external_id,
            responses=[],
            status="completed",
            **common,
        )
        self.route(
            f"/api/v1/patient/{patient}/questionnaire_response/{response.external_id}/",
            canary=None,
        )

        thread = self.make(NoteThread, title=f"{canary} probe", **common)
        self.route(
            f"/api/v1/patient/{patient}/thread/{thread.external_id}/", canary="title"
        )
        message = self.make(NoteMessage, thread=thread, message=canary)
        self.route(
            f"/api/v1/patient/{patient}/thread/{thread.external_id}/note/"
            f"{message.external_id}/",
            canary="message",
        )

    # --- files -----------------------------------------------------------------------------

    def files(self):
        from care.emr.models.encounter import Encounter
        from care.emr.models.file_upload import FileUpload
        from care.emr.models.meta_artifact import MetaArtifact
        from care.emr.models.patient import Patient
        from care.emr.models.report.report_upload import ReportUpload
        from care.emr.models.report.template import Template
        from care.emr.reports.report_type_registry import ReportTypeRegistry

        p, canary = self.patient, self.canary
        with _seeding(FileUpload):
            upload = FileUpload(
                name=f"{canary} probe",
                internal_name="probe.pdf",  # save() turns it into a random name, keeping .pdf
                associating_id=str(p.external_id),
                file_type="patient",
                file_category="unspecified",
                upload_completed=True,
                meta={"mime_type": "application/pdf"},
                created_by=self.admin,
                updated_by=self.admin,
            )
            upload.save()
        self.route(f"/api/v1/files/{upload.external_id}/", canary="name")

        artifact = self.make(
            MetaArtifact,
            associating_type="patient",
            associating_external_id=p.external_id,
            name=f"{canary} probe",
            object_type="drawing",
            object_value={"elements": []},
            note=canary,
        )
        self.route(f"/api/v1/meta_artifacts/{artifact.external_id}/", canary="note")

        # A report as CARE's generator leaves it (report_utils.generate_and_upload_report):
        # a template, its own report type, an id of that type's model. The template is chosen
        # by its report type's associating model (the first one of an encounter or a patient),
        # never just the first by pk: an account report has no row here to point at.
        associating = {Encounter: self.encounter, Patient: p}
        with _seeding(ReportUpload):
            template, target = next(
                (t, associating[model])
                for t in Template.objects.order_by("pk")
                if (model := ReportTypeRegistry.get(t.template_type).associating_model)
                in associating
            )
            report = ReportUpload(
                template=template,
                name=f"{canary} probe",
                internal_name="probe.pdf",
                associating_id=str(target.external_id),
                report_type=template.template_type,
                upload_completed=True,
                meta={
                    "mime_type": "application/pdf",
                    "template_id": str(template.external_id),
                    "output_format": "pdf",
                },
                created_by=self.admin,
                updated_by=self.admin,
            )
            report.save()
        self.route(f"/api/v1/template_reports/{report.external_id}/", canary="name")

    # --- devices, pharmacy, scheduling, resources, tags --------------------------------------

    def operations(self):
        from care.emr.models.device import (
            Device,
            DeviceEncounterHistory,
            DeviceLocationHistory,
            DeviceServiceHistory,
        )
        from care.emr.models.inventory_item import InventoryItem
        from care.emr.models.location import FacilityLocation
        from care.emr.models.medication_dispense import (
            DispenseOrder,
            MedicationDispense,
        )
        from care.emr.models.questionnaire import QuestionnaireResponseTemplate
        from care.emr.models.resource_request import (
            ResourceRequest,
            ResourceRequestComment,
        )
        from care.emr.models.scheduling import (
            AvailabilityException,
            SchedulableResource,
            Token,
            TokenCategory,
            TokenQueue,
        )
        from care.emr.models.tag_config import TagConfig
        from care.users.models import PlugConfig

        f, p, e, canary = self.facility, self.patient, self.encounter, self.canary
        facility = f.external_id

        # The device's own associate_location / associate_encounter actions, as the ORM writes
        # they make (device.py): the device points at both, and each gets an open history row.
        with _seeding(Device):
            device = _first(
                Device
            )  # fill() takes the first device, whatever its facility
            location = _first(FacilityLocation)
            device.current_location = location
            device.current_encounter = e
            device.updated_by = self.admin
            device.save(
                update_fields=[
                    "current_location",
                    "current_encounter",
                    "updated_by",
                    "modified_date",
                ]
            )
        base = f"/api/v1/facility/{facility}/device/{device.external_id}"
        located = self.make(
            DeviceLocationHistory, device=device, location=location, start=self.now
        )
        self.route(f"{base}/location_history/{located.external_id}/", canary=None)
        used = self.make(
            DeviceEncounterHistory, device=device, encounter=e, start=self.now
        )
        self.route(f"{base}/encounter_history/{used.external_id}/", canary=None)
        serviced = self.make(
            DeviceServiceHistory, device=device, serviced_on=self.now, note=canary
        )
        self.route(f"{base}/service_history/{serviced.external_id}/", canary="note")

        # Pharmacy: an order and a dispense of the first stock item, at its own location.
        item = _first(InventoryItem)
        order = self.make(
            DispenseOrder,
            name=f"{canary} probe",
            status="in_progress",
            note=canary,
            location=item.location,
            patient=p,
            facility=f,
            alternate_identifier=f"{canary}-1",
        )
        self.route(
            f"/api/v1/facility/{facility}/order/dispense/{order.external_id}/",
            canary="note",
        )
        dispense = self.make(
            MedicationDispense,
            status="in_progress",
            category="inpatient",
            note=canary,
            encounter=e,
            patient=p,
            location=item.location,
            authorizing_request=self.request,
            item=item,
            quantity=1,
            order=order,
        )
        self.route(
            f"/api/v1/medication/dispense/{dispense.external_id}/", canary="note"
        )

        # Scheduling: an exception on the first resource (in 2030, clear of every booking),
        # and a token in the first queue.
        resource = _first(SchedulableResource, facility=f)
        exception = self.make(
            AvailabilityException,
            resource=resource,
            name=f"{canary} probe",
            reason=canary,
            valid_from=date(2030, 1, 1),
            valid_to=date(2030, 1, 2),
            start_time=time(9),
            end_time=time(10),
        )
        self.route(
            f"/api/v1/facility/{facility}/schedule_exceptions/{exception.external_id}/",
            canary="reason",
        )
        queue = _first(TokenQueue)
        category = _first(TokenCategory)
        with _seeding(Token):
            number = Token.get_next_number(queue, category)
        token = self.make(
            Token,
            facility=f,
            patient=p,
            queue=queue,
            category=category,
            number=number,
            status="CREATED",
            note=canary,
        )
        self.route(
            f"/api/v1/facility/{facility}/token/queue/{queue.external_id}/token/"
            f"{token.external_id}/",
            canary="note",
        )

        resource_request = self.make(
            ResourceRequest,
            origin_facility=f,
            related_patient=p,
            title=f"{canary} probe",
            reason=canary,
            status="pending",
            category="patient_care",
            priority=1,
        )
        self.route(f"/api/v1/resource/{resource_request.external_id}/", canary="reason")
        comment = self.make(
            ResourceRequestComment, request=resource_request, comment=canary
        )
        self.route(
            f"/api/v1/resource/{resource_request.external_id}/comment/"
            f"{comment.external_id}/",
            canary="comment",
        )

        tag = self.make(
            TagConfig,
            facility=f,
            status="active",
            display=f"{canary} probe",
            description=canary,
            category="admin",
            resource="encounter",
        )
        self.route(f"/api/v1/tag_config/{tag.external_id}/", canary="description")

        # Its retrieve shows only templates the viewer made or shares (created_by).
        template = self.make(
            QuestionnaireResponseTemplate,
            facility=f,
            name=f"{canary} probe",
            description=canary,
        )
        self.route(
            f"/api/v1/questionnaire_response_template/{template.external_id}/",
            canary="description",
        )

        # CARE's web-app plugin config, open before sign-in (anonymous_allowlist.txt).
        with _seeding(PlugConfig):
            PlugConfig.objects.create(slug=PLUG_SLUG, meta={"note": canary})
        self.route(f"/api/v1/plug_config/{PLUG_SLUG}/", canary="meta")
