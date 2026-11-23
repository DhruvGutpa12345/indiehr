"""A small hospital for the access tests (P1-T11b; never imported by the app).

One facility with its root organisation and departments (Paediatrics, Laboratory, Radiology,
Stores), a ward with a bed, one user per role of ours plus CARE's Doctor, Nurse and Facility Admin,
and one patient with everything a restricted user must not see, each carrying a canary word:
blood group, a plugin extension, a programme identifier ("ART registration"), an inpatient
encounter in the bed with discharge advice, discharge disposition, diet, a care team, a
department and a tag, a booking with a note, a tag and a booked doctor, and a device on the
encounter. The patient carries an instance tag and a facility tag (tags can carry clinical
labels; their display holds the canary).

Built with the ORM, through CARE's own ``save()`` (caches included), as the app role: nothing
here is owner-only work. P1-T11b2/b3 extend it (``build_world`` returns a ``World`` they add to).
"""

import datetime
import time
import uuid
from dataclasses import dataclass, field

from care.emr.models import Encounter, Patient, PatientUser
from care.emr.models.device import Device
from care.emr.models.encounter import EncounterOrganization
from care.emr.models.location import FacilityLocation, FacilityLocationOrganization
from care.emr.models.organization import (
    FacilityOrganization,
    FacilityOrganizationUser,
    Organization,
    OrganizationUser,
)
from care.emr.models.patient import PatientIdentifier, PatientIdentifierConfig
from care.emr.models.scheduling.booking import TokenBooking, TokenSlot
from care.emr.models.scheduling.schedule import (
    Availability,
    SchedulableResource,
    Schedule,
)
from care.emr.models.tag_config import TagConfig
from care.facility.models import Facility
from care.security.models import RoleModel
from care.users.models import User
from django.core.management import call_command
from django.utils import timezone
from model_bakery import baker

CANARY = "Zz-Canary-7731"  # in every field a restricted user must not see
PROGRAMME_SYSTEM = (
    "http://example.test/art-registration"  # a programme number: reveals illness
)
PHONE_SYSTEM = (
    "system.care.ohc.network/patient-phone-number"  # on identifier_configs.txt
)
OUR_ROLES = (
    "Receptionist",
    "Cashier",
    "TPA Desk",
    "Owner",
    "MRD",
    "Lab Technician",
    "Radiologist",
    "Store Keeper",
)
# Where each user of the world is placed: role -> department (None: the facility root)
PLACES = {
    "Receptionist": None,
    "Cashier": None,
    "TPA Desk": None,
    "Owner": None,
    "MRD": None,
    "Lab Technician": "Laboratory",
    "Radiologist": "Radiology",
    "Store Keeper": "Stores",
    "Doctor": "Paediatrics",
    "Nurse": "Paediatrics",
    "Facility Admin": None,
}


@dataclass
class World:
    admin: User
    facility: Facility
    root: FacilityOrganization
    departments: dict
    users: dict  # role name -> user placed as PLACES says
    patient: Patient
    encounter: Encounter
    ward: FacilityLocation
    bed: FacilityLocation
    booking: TokenBooking
    device: Device
    programme_config: PatientIdentifierConfig
    phone_config: PatientIdentifierConfig
    tags: dict  # "encounter", "patient_instance", "patient_facility", "booking" -> TagConfig
    extra: dict = field(default_factory=dict)
    # name -> model instance, for the role matrix's path and query parameters (P1-T11b3):
    # filled by build_world(full=True); tests/list_params.txt names these
    objects: dict = field(default_factory=dict)

    def role(self, name):
        return RoleModel.objects.get(name=name, is_system=True)

    def user(self, label, *placements):
        """A new user placed as given: (role name, organisation) pairs."""
        user = User.objects.create_user(
            username=f"{label}-{_tag()}", password="x-Unused-1"
        )
        for role, organization in placements:
            FacilityOrganizationUser.objects.create(
                organization=organization, user=user, role=self.role(role)
            )
        return user


def _tag():
    return f"{time.time_ns() % 10**10:x}"


MARKERS = (
    set()
)  # every named marker a world was built with (the canary's positive control)


def marker(name):
    """A named canary: ``CANARY`` plus ``-<name>``, so the canary walk (P1-T11b3) can tell which
    field leaked and exempt one marker on one route (tests/canary_exemptions.txt)."""
    MARKERS.add(name)
    return f"{CANARY}-{name}"


SLOT_MINUTES = 15


def slot_start(now):
    """When the world's slot starts: ``now``, unless the slot would end on the next local day.
    CARE's slots-for-day matches a slot by the local date of both its start and its end, so a
    world built in the last minutes before midnight (IST) had no slot that day and the canary
    had nothing to leak (CI, 16-11 at 23:4x IST). Then the slot starts an hour earlier."""
    end = timezone.localtime(now + datetime.timedelta(minutes=SLOT_MINUTES))
    if end.date() != timezone.localtime(now).date():
        return now - datetime.timedelta(hours=1)
    return now


def world_day(world):
    """The local day the world's slot and booking fall on: ``@today`` in post_reads.txt, so a
    request made after midnight still asks for the world's day."""
    return timezone.localtime(world.booking.token_slot.start_datetime).date()


def build_world(full=False):
    """The test hospital. ``full``: also everything the role matrix and the canary walk need
    (P1-T11b3, ``add_matrix_world``); the b1/b2 tests use the small one."""
    world = _build_small_world()
    if full:
        add_matrix_world(world)
    return world


def _build_small_world():
    # Our roles and their grants, as every start writes them into each hospital
    call_command("sync_permissions_roles")
    admin = User.objects.create_superuser(
        username=f"acc-admin-{_tag()}",
        email=f"a{_tag()}@example.test",
        password="x-Unused-1",
    )
    facility = baker.make(
        Facility, created_by=admin, name=f"Access Test Hospital {_tag()}"
    )
    root = facility.default_internal_organization
    departments = {
        name: FacilityOrganization.objects.create(
            facility=facility, name=name, org_type="team"
        )
        for name in ("Paediatrics", "Laboratory", "Radiology", "Stores")
    }
    users = {}
    for role, department in PLACES.items():
        organization = departments[department] if department else root
        users[role] = User.objects.create_user(
            username=f"acc-{role.lower().replace(' ', '-')}-{_tag()}",
            password="x-Unused-1",
            first_name=role,
            last_name="Tester",
        )
        FacilityOrganizationUser.objects.create(
            organization=organization,
            user=users[role],
            role=RoleModel.objects.get(name=role, is_system=True),
        )

    ward = FacilityLocation.objects.create(
        facility=facility,
        name="Ward A",
        description="ward",
        status="active",
        operational_status="O",
        mode="kind",
        form="wa",
    )
    FacilityLocationOrganization.objects.create(
        location=ward, organization=departments["Paediatrics"]
    )
    bed = FacilityLocation.objects.create(
        facility=facility,
        parent=ward,
        name="Bed 1",
        description="bed",
        status="active",
        operational_status="O",
        mode="instance",
        form="bd",
    )

    geo = Organization.objects.create(name=f"District {_tag()}", org_type="govt")
    patient = Patient.objects.create(
        name=f"Asha Access {_tag()}",
        gender="female",
        phone_number="+919876543210",
        address="12 MG Road, Pune",
        pincode=411001,
        year_of_birth=1990,
        blood_group="AB_negative",
        geo_organization=geo,
        extensions={"core": {"note": CANARY}},
    )
    phone_config = PatientIdentifierConfig.objects.create(
        status="active",
        config={
            "use": "usual",
            "system": PHONE_SYSTEM,
            "display": "Phone",
            "required": False,
            "unique": False,
            "regex": "",
        },
    )
    programme_config = PatientIdentifierConfig.objects.create(
        status="active",
        config={
            "use": "official",
            "system": PROGRAMME_SYSTEM,
            "display": f"ART registration {marker('programme-config')}",
            "required": False,
            "unique": False,
            "regex": "",
        },
    )
    for config, value in (
        (phone_config, "+919876543210"),
        (programme_config, f"ART-{marker('programme-number')}"),
    ):
        PatientIdentifier.objects.create(patient=patient, config=config, value=value)
    patient.build_instance_identifiers()
    patient.save()

    resource = SchedulableResource.objects.create(
        facility=facility, resource_type="practitioner", user=users["Doctor"]
    )
    now = timezone.now()
    schedule = Schedule.objects.create(
        resource=resource,
        name=f"Dr Paediatrics OPD {marker('schedule')}",
        valid_from=now - datetime.timedelta(days=1),
        valid_to=now + datetime.timedelta(days=30),
    )
    availability = Availability.objects.create(
        schedule=schedule,
        name=f"Morning {marker('schedule')}",
        slot_type="appointment",
        slot_size_in_minutes=15,
        tokens_per_slot=1,
        availability=[],
    )
    start = slot_start(now)
    slot = TokenSlot.objects.create(
        resource=resource,
        availability=availability,
        start_datetime=start,
        end_datetime=start + datetime.timedelta(minutes=SLOT_MINUTES),
    )
    booking = TokenBooking.objects.create(
        token_slot=slot,
        patient=patient,
        booked_by=users["Receptionist"],
        status="booked",
        note=f"booking note {marker('booking-note')}",
    )

    encounter = Encounter.objects.create(
        patient=patient,
        facility=facility,
        status="in_progress",
        encounter_class="imp",
        priority="routine",
        period={"start": now.isoformat()},
        hospitalization={
            "admit_source": "outp",
            "re_admission": False,
            "discharge_disposition": "hosp",
            "diet_preference": "vegan",
        },
        discharge_summary_advice=f"advice {CANARY}",
        care_team=[{"user_id": users["Doctor"].id, "role": {"code": "doctor"}}],
        appointment=booking,
        status_history={"history": [{"status": "in_progress", "moved_at": str(now)}]},
    )
    EncounterOrganization.objects.create(
        encounter=encounter, organization=departments["Paediatrics"]
    )
    bed.current_encounter = encounter
    bed.save()
    encounter.current_location = bed
    encounter.save()

    # Tags that name an illness, on every tagged shape (review C7)
    tags = {
        key: TagConfig.objects.create(
            facility=tag_facility,
            status="active",
            display=f"{label} {canary}",
            category="diagnosis",
            resource=resource,
        )
        for key, tag_facility, resource, label, canary in (
            ("encounter", facility, "encounter", "Oncology", marker("tag-encounter")),
            (
                "patient_instance",
                None,
                "patient",
                "HIV positive",
                marker("tag-patient"),
            ),
            (
                "patient_facility",
                facility,
                "patient",
                "TB programme",
                marker("tag-patient-facility"),
            ),
            # a marker of its own: CARE's booking screens show it to the Receptionist
            # (tests/canary_exemptions.txt), our projected bookings never do
            ("booking", facility, "token_booking", "Psychiatry", marker("booking-tag")),
        )
    }
    encounter.tags = [tags["encounter"].id]
    encounter.save()
    patient.instance_tags = [tags["patient_instance"].id]
    patient.facility_tags = {str(facility.id): [tags["patient_facility"].id]}
    patient.save()
    booking.tags = [tags["booking"].id]
    booking.save()

    device = Device.objects.create(
        facility=facility,
        registered_name="Monitor 1",
        status="active",
        availability_status="available",
        manufacturer="Acme",
        current_encounter=encounter,
    )
    return World(
        admin=admin,
        facility=facility,
        root=root,
        departments=departments,
        users=users,
        patient=patient,
        encounter=encounter,
        ward=ward,
        bed=bed,
        booking=booking,
        device=device,
        programme_config=programme_config,
        phone_config=phone_config,
        tags=tags,
    )


def nurse_on_care_team(world):
    """A Nurse added to the patient's care team (CARE's add_user), as in the attack's A9. She
    works in Stores, which is not on the encounter: the PatientUser row is her only way in."""
    nurse = world.user("acc-moved-nurse", ("Nurse", world.departments["Stores"]))
    PatientUser.objects.create(
        user=nurse, patient=world.patient, role=world.role("Nurse")
    )
    return nurse


def move_to(world, user, role, organization=None):
    """The person changes job: their memberships are replaced by one ``role`` membership."""
    FacilityOrganizationUser.objects.filter(user=user).delete()
    OrganizationUser.objects.filter(user=user).delete()
    FacilityOrganizationUser.objects.create(
        organization=organization or world.root, user=user, role=world.role(role)
    )


def ext_id():
    return str(uuid.uuid4())


def grant_records(
    world, user, patient, reason="insurer", reference="TEST-1", hours=None
):
    """A records grant made through the real path (records.create_grant: the grant row, then
    the hidden-role PatientUser row); ``hours``: live that long instead of the setting's
    30 minutes (a crawl that outlasts it would see it expire)."""
    from ihr_access import records
    from ihr_access.models import RecordsGrant

    grant = records.create_grant(user, patient, reason, reference)
    if hours is not None:
        grant.expires_at = timezone.now() + datetime.timedelta(hours=hours)
        RecordsGrant.objects.filter(pk=grant.pk).update(expires_at=grant.expires_at)
    return grant


# --- the role matrix's world (P1-T11b3) ----------------------------------------------------

LAB = {
    "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
    "code": "LAB",
    "display": "Laboratory",
}
IMAGING = {
    "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
    "code": "RAD",
    "display": "Radiology",
}
BLOOD = {
    "system": "http://terminology.hl7.org/CodeSystem/v2-0487",
    "code": "BLD",
    "display": "Whole blood",
}


def _medication(name):
    return {"system": "http://snomed.info/sct", "code": "387517004", "display": name}


def _place(world, name, department, mode="instance", form="ro"):
    place = FacilityLocation.objects.create(
        facility=world.facility,
        name=name,
        description=name,
        status="active",
        operational_status="O",
        mode=mode,
        form=form,
    )
    FacilityLocationOrganization.objects.create(
        location=place, organization=world.departments[department]
    )
    return place


def add_matrix_world(world):
    """Everything the role matrix and the canary walk need (design-b.md "After the attack" §10
    items 12-19), added to the small world. Every clinical field holds a named marker
    (``marker(name)``); the request, specimen, report and observation come in a lab copy (at a
    Laboratory place), a ward copy (at the Paediatrics ward) and, for the request and report,
    an imaging copy (at a Radiology place), so a department's view can be told from the
    hospital's. A second patient has an open visit filed under Radiology (Q-K44 A: the
    Radiologist reads that chart, not the first patient's). ``world.objects`` names each row
    for tests/list_params.txt; the default row of a model is the first one named for it."""
    _MatrixWorld(world).build()
    return world


class _MatrixWorld:
    def __init__(self, world):
        self.w = world
        self.now = timezone.now()
        self.objects = world.objects
        self.doctor = world.users["Doctor"]

    def make(self, model, **values):
        values.setdefault("created_by", self.doctor)
        values.setdefault("updated_by", self.doctor)
        return model.objects.create(**values)

    def name(self, key, obj):
        self.objects[key] = obj
        return obj

    def build(self):
        w = self.w
        for key in ("facility", "patient", "encounter", "booking", "device"):
            self.objects[key] = getattr(w, key)
        # The bed (the default place) names its occupant's ward: never shown to our roles
        # (Q-K37 A); the encounter shapes drop it
        w.bed.name = f"Bed 1 {marker('bed')}"
        w.bed.save()
        self.objects["location"] = w.bed
        self.objects["ward"] = w.ward
        self.objects["doctor_user"] = self.doctor
        self.objects["district"] = w.patient.geo_organization
        self.places()
        # the first row named for a model is its default row: the first patient's come first
        self.clinical()
        self.orders()
        self.second_patient()
        self.billing()
        self.queue()
        self.store()
        self.records()
        self.people()
        self.packs()
        self.procedures()
        self.referrals()
        self.actions()

    def places(self):
        w = self.w
        self.lab = self.name(
            "lab_location", _place(w, "Sample collection", "Laboratory")
        )
        self.xray = self.name("xray_location", _place(w, "X-ray room", "Radiology"))
        self.store_place = self.name(
            "store_location", _place(w, "Main store", "Stores", mode="kind", form="wa")
        )

    def second_patient(self):
        """A patient whose open visit has Radiology on it (Q-K44 A)."""
        from care.emr.models.condition import Condition

        w = self.w
        patient = Patient.objects.create(
            name=f"Bharat Imaging {_tag()}",
            gender="male",
            phone_number="+919812345678",
            address="3 Station Road, Nagpur",
            pincode=440001,
            year_of_birth=1975,
            blood_group="O_negative",
            geo_organization=w.patient.geo_organization,
            extensions={"core": {"note": marker("rad-extension")}},
        )
        encounter = Encounter.objects.create(
            patient=patient,
            facility=w.facility,
            status="in_progress",
            encounter_class="amb",
            priority="routine",
            period={"start": self.now.isoformat()},
            discharge_summary_advice=f"advice {marker('rad-advice')}",
            hospitalization={"admit_source": "outp"},
        )
        EncounterOrganization.objects.create(
            encounter=encounter, organization=w.departments["Radiology"]
        )
        encounter.save()
        self.name("patient_b", patient)
        self.name("encounter_b", encounter)
        self.name(
            "condition_b",
            self.make(
                Condition,
                patient=patient,
                encounter=encounter,
                category="problem_list_item",
                clinical_status="active",
                verification_status="confirmed",
                severity="mild",
                code={
                    "system": "http://snomed.info/sct",
                    "code": "386661006",
                    "display": marker("rad-condition"),
                },
                recorded_date=self.now,
                note=marker("rad-condition"),
            ),
        )
        self.second_patient_orders(patient, encounter)
        # And a finished outpatient visit of the first patient (Paediatrics)
        done = Encounter.objects.create(
            patient=w.patient,
            facility=w.facility,
            status="completed",
            encounter_class="amb",
            priority="routine",
            period={
                "start": (self.now - datetime.timedelta(days=30)).isoformat(),
                "end": (self.now - datetime.timedelta(days=29)).isoformat(),
            },
            discharge_summary_advice=f"advice {marker('done-advice')}",
            hospitalization={},
        )
        EncounterOrganization.objects.create(
            encounter=done, organization=w.departments["Paediatrics"]
        )
        done.save()
        self.name("encounter_done", done)

    def second_patient_orders(self, patient, encounter):
        """The Radiology patient's own request, specimen, report and prescription, at no place
        (only the visit's rights reach them), each with its own marker: the grant authorizer's
        six read checks are crawled on another patient than the granted one, and must refuse
        MRD with a grant there (P1-T11b4 review C1; mutation records-grant-scope)."""
        from care.emr.models.diagnostic_report import DiagnosticReport
        from care.emr.models.medication_request import MedicationRequestPrescription
        from care.emr.models.service_request import ServiceRequest
        from care.emr.models.specimen import Specimen

        w = self.w
        common = {"patient": patient, "encounter": encounter}
        request = self.name(
            "service_request_b",
            self.make(
                ServiceRequest,
                facility=w.facility,
                title=f"Chest X-ray {marker('rad-request')}",
                category="imaging",
                status="active",
                intent="order",
                priority="routine",
                note=marker("rad-request"),
                code=IMAGING,
                requester=self.doctor,
                locations=[],
                **common,
            ),
        )
        self.name(
            "specimen_b",
            self.make(
                Specimen,
                facility=w.facility,
                accession_identifier=f"ACC-rad-{_tag()}",
                status="available",
                specimen_type=BLOOD,
                service_request=request,
                note=marker("rad-specimen"),
                **common,
            ),
        )
        self.name(
            "diagnostic_report_b",
            self.make(
                DiagnosticReport,
                facility=w.facility,
                status="final",
                category=IMAGING,
                service_request=request,
                note=marker("rad-report"),
                conclusion=marker("rad-report"),
                **common,
            ),
        )
        self.name(
            "prescription_b",
            self.make(
                MedicationRequestPrescription,
                name=f"Paracetamol {marker('rad-prescription')}",
                note=marker("rad-prescription"),
                status="active",
                prescribed_by=self.doctor,
                **common,
            ),
        )

    def clinical(self):
        """One row of each clinical kind on the first patient's inpatient visit."""
        from care.emr.models.allergy_intolerance import AllergyIntolerance
        from care.emr.models.condition import Condition
        from care.emr.models.consent import Consent
        from care.emr.models.file_upload import FileUpload
        from care.emr.models.medication_administration import MedicationAdministration
        from care.emr.models.medication_request import (
            MedicationRequest,
            MedicationRequestPrescription,
        )
        from care.emr.models.medication_statement import MedicationStatement
        from care.emr.models.meta_artifact import MetaArtifact
        from care.emr.models.notes import NoteMessage, NoteThread
        from care.emr.models.questionnaire import (
            FormSubmission,
            Questionnaire,
            QuestionnaireResponse,
        )

        w = self.w
        common = {"patient": w.patient, "encounter": w.encounter}
        self.name(
            "condition",
            self.make(
                Condition,
                category="problem_list_item",
                clinical_status="active",
                verification_status="confirmed",
                severity="mild",
                code={
                    "system": "http://snomed.info/sct",
                    "code": "386661006",
                    "display": marker("condition-code"),
                },
                recorded_date=self.now,
                note=marker("condition"),
                **common,
            ),
        )
        self.name(
            "allergy",
            self.make(
                AllergyIntolerance,
                clinical_status="active",
                verification_status="confirmed",
                category="medication",
                criticality="low",
                code=_medication("Penicillin"),
                recorded_date=self.now,
                note=marker("allergy"),
                **common,
            ),
        )
        self.name(
            "consent",
            self.make(
                Consent,
                encounter=w.encounter,
                status="active",
                category="treatment",
                date=self.now,
                decision="permit",
                note=marker("consent"),
            ),
        )
        prescription = self.name(
            "prescription",
            self.make(
                MedicationRequestPrescription,
                name=f"Tenofovir {marker('prescription')}",
                note=marker("prescription"),
                status="active",
                prescribed_by=self.doctor,
                **common,
            ),
        )
        self.request = self.name(
            "medication_request",
            self.make(
                MedicationRequest,
                status="active",
                intent="order",
                category="inpatient",
                priority="routine",
                do_not_perform=False,
                medication=_medication("Tenofovir"),
                dosage_instruction=[{"text": marker("dosage")}],
                authored_on=self.now,
                requester=self.doctor,
                prescription=prescription,
                note=marker("medication-request"),
                **common,
            ),
        )
        self.name(
            "medication_administration",
            self.make(
                MedicationAdministration,
                status="completed",
                category="inpatient",
                medication=_medication("Tenofovir"),
                request=self.request,
                authored_on=self.now,
                occurrence_period_start=self.now,
                note=marker("medication-administration"),
                **common,
            ),
        )
        self.name(
            "medication_statement",
            self.make(
                MedicationStatement,
                status="active",
                medication=_medication("Lithium"),
                information_source="patient",
                effective_period={"start": self.now.isoformat()},
                note=marker("medication-statement"),
                **common,
            ),
        )
        thread = self.name(
            "thread",
            self.make(
                NoteThread, title=f"Ward round {marker('note-thread')}", **common
            ),
        )
        self.name(
            "note",
            self.make(NoteMessage, thread=thread, message=marker("note-message")),
        )
        questionnaire = self.name(
            "questionnaire",
            self.make(
                Questionnaire,
                version="1.0",
                slug=f"ihr-matrix-{_tag()}",
                title="Mental state examination",
                status="active",
                subject_type="encounter",
                questions=[],
            ),
        )
        self.name(
            "form_submission",
            self.make(
                FormSubmission,
                questionnaire=questionnaire,
                status="submitted",
                **common,
            ),
        )
        self.name(
            "questionnaire_response",
            self.make(
                QuestionnaireResponse,
                questionnaire=questionnaire,
                subject_id=w.encounter.external_id,
                responses=[
                    {"question_id": "q1", "values": [{"value": marker("answer")}]}
                ],
                structured_response_type="",
                status="completed",
                **common,
            ),
        )
        upload = FileUpload(
            name=f"Biopsy {marker('patient-file')}",
            internal_name="probe.pdf",
            associating_id=str(w.patient.external_id),
            file_type="patient",
            file_category="unspecified",
            upload_completed=True,
            meta={"mime_type": "application/pdf"},
            created_by=self.doctor,
            updated_by=self.doctor,
        )
        upload.save()
        self.name("file", upload)
        self.name(
            "meta_artifact",
            self.make(
                MetaArtifact,
                associating_type="patient",
                associating_external_id=w.patient.external_id,
                name=f"Sketch {marker('artifact')}",
                object_type="drawing",
                object_value={"elements": []},
                note=marker("artifact"),
            ),
        )

    def orders(self):
        """Requests, specimens, reports and results: a ward copy (the default rows), a lab copy
        and an imaging copy (design §10 item 18)."""
        from care.emr.models.diagnostic_report import DiagnosticReport
        from care.emr.models.observation import Observation
        from care.emr.models.service_request import ServiceRequest
        from care.emr.models.specimen import Specimen

        w = self.w
        common = {"patient": w.patient, "encounter": w.encounter}
        for copy, place, category, code in (
            ("ward", w.ward, "laboratory", LAB),
            ("lab", self.lab, "laboratory", LAB),
            ("xray", self.xray, "imaging", IMAGING),
        ):
            prefix = "" if copy == "ward" else f"{copy}_"
            request = self.name(
                f"{prefix}service_request",
                self.make(
                    ServiceRequest,
                    facility=w.facility,
                    title=f"Viral load {marker(f'request-{copy}')}",
                    category=category,
                    status="active",
                    intent="order",
                    priority="routine",
                    note=marker(f"request-{copy}"),
                    code=code,
                    requester=self.doctor,
                    locations=[place.id],
                    **common,
                ),
            )
            if copy != "xray":
                self.name(
                    f"{prefix}specimen",
                    self.make(
                        Specimen,
                        facility=w.facility,
                        accession_identifier=f"ACC-{copy}-{_tag()}",
                        status="available",
                        specimen_type=BLOOD,
                        service_request=request,
                        note=marker(f"specimen-{copy}"),
                        **common,
                    ),
                )
            report = self.name(
                f"{prefix}diagnostic_report",
                self.make(
                    DiagnosticReport,
                    facility=w.facility,
                    status="final",
                    category=code,
                    service_request=request,
                    note=marker(f"report-{copy}"),
                    conclusion=marker(f"report-{copy}"),
                    **common,
                ),
            )
            self.name(
                f"{prefix}observation",
                self.make(
                    Observation,
                    status="final",
                    value_type="string",
                    value={"value": marker(f"result-{copy}")},
                    main_code={"system": "http://loinc.org", "code": "20447-9"},
                    effective_datetime=self.now,
                    diagnostic_report=report,
                    subject_type="encounter",
                    subject_id=w.encounter.external_id,
                    note=marker(f"result-{copy}"),
                    **common,
                ),
            )

    def billing(self):
        from decimal import Decimal

        from care.emr.models.account import Account
        from care.emr.models.charge_item import ChargeItem
        from care.emr.models.invoice import Invoice
        from care.emr.models.payment_reconciliation import PaymentReconciliation

        w = self.w
        account = self.name(
            "account",
            Account.objects.create(
                facility=w.facility,
                patient=w.patient,
                primary_encounter=w.encounter,
                status="active",
                billing_status="open",
                name="IPD account",
            ),
        )
        component = {
            "monetary_component_type": "base",
            "currency": "INR",
            "amount": "100.00",
        }
        self.name(
            "charge_item",
            ChargeItem.objects.create(
                facility=w.facility,
                title=f"HIV viral load {marker('charge')}",
                patient=w.patient,
                encounter=w.encounter,
                account=account,
                status="billable",
                quantity=Decimal("1.00"),
                unit_price_components=[component],
                total_price_components=[component],
                total_price=Decimal("100.00"),
            ),
        )
        self.name(
            "invoice",
            Invoice.objects.create(
                facility=w.facility,
                patient=w.patient,
                account=account,
                status="issued",
                title="Final bill",
            ),
        )
        self.name(
            "payment_reconciliation",
            PaymentReconciliation.objects.create(
                facility=w.facility,
                account=account,
                reconciliation_type="payment",
                status="active",
                kind="deposit",
                issuer_type="patient",
                outcome="complete",
                method="cash",
                tendered_amount=Decimal(100),
                returned_amount=Decimal(0),
                amount=Decimal(100),
            ),
        )

    def queue(self):
        from care.emr.models.scheduling import (
            SchedulableResource,
            Token,
            TokenCategory,
            TokenQueue,
        )

        w = self.w
        resource = SchedulableResource.objects.get(
            facility=w.facility, user=self.doctor
        )
        queue = self.name(
            "token_queue",
            self.make(
                TokenQueue,
                facility=w.facility,
                resource=resource,
                name="OPD queue",
                date=self.now.date(),
            ),
        )
        category = self.name(
            "token_category",
            self.make(
                TokenCategory,
                facility=w.facility,
                resource_type="practitioner",
                name="General",
                shorthand="G",
            ),
        )
        self.name(
            "token",
            self.make(
                Token,
                facility=w.facility,
                patient=w.patient,
                queue=queue,
                category=category,
                number=Token.get_next_number(queue, category),
                status="CREATED",
                note=marker("token-note"),
            ),
        )

    def store(self):
        """The store with stock, a dispense to the patient and a patient's return (Q-K43)."""
        from decimal import Decimal

        from care.emr.models import Product
        from care.emr.models.charge_item_definition import ChargeItemDefinition
        from care.emr.models.inventory_item import InventoryItem
        from care.emr.models.medication_dispense import (
            DispenseOrder,
            MedicationDispense,
        )
        from care.emr.models.product_knowledge import ProductKnowledge
        from care.emr.models.supply_delivery import DeliveryOrder, SupplyDelivery
        from care.emr.models.supply_request import RequestOrder, SupplyRequest

        w = self.w
        place = self.store_place
        knowledge = self.name(
            "product_knowledge",
            baker.make(
                ProductKnowledge,
                facility=w.facility,
                slug=f"f-{w.facility.external_id}-tenofovir",
                name="Tenofovir 300 mg",
                status="active",
                product_type="medication",
            ),
        )
        definition = self.name(
            "charge_item_definition",
            baker.make(
                ChargeItemDefinition,
                facility=w.facility,
                slug=f"f-{w.facility.external_id}-tenofovir-charge",
                title="Tenofovir 300 mg",
                status="active",
                price_components=[
                    {"amount": "100", "monetary_component_type": "base", "factor": None}
                ],
            ),
        )
        product = self.name(
            "product",
            baker.make(
                Product,
                facility=w.facility,
                product_knowledge=knowledge,
                charge_item_definition=definition,
                status="active",
                product_type="medication",
            ),
        )
        item = self.name(
            "inventory_item",
            baker.make(
                InventoryItem,
                location=place,
                product=product,
                net_content=Decimal(500),
                status="active",
            ),
        )
        order = self.name(
            "dispense_order",
            self.make(
                DispenseOrder,
                name=f"Dispense - {w.patient.name}",
                status="in_progress",
                note=marker("dispense-order"),
                location=place,
                patient=w.patient,
                facility=w.facility,
            ),
        )
        self.name(
            "medication_dispense",
            self.make(
                MedicationDispense,
                status="in_progress",
                category="inpatient",
                note=marker("dispense"),
                encounter=w.encounter,
                patient=w.patient,
                location=place,
                authorizing_request=self.request,
                item=item,
                quantity=1,
                order=order,
            ),
        )
        # A request order at the store and its supply request: both deliveries below answer it,
        # so CARE's delivery-order list (it needs ?request_order) shows the stock order and, to
        # CARE's roles, the return (P1-T11b3 review M8; Q-K43 A)
        request_order = self.name(
            "request_order",
            self.make(
                RequestOrder,
                name="Monthly indent",
                status="pending",
                priority="routine",
                intent="order",
                reason="ward_stock",
                category="central",
                destination=place,
            ),
        )
        wanted = self.name(
            "supply_request",
            self.make(
                SupplyRequest,
                status="active",
                quantity=Decimal(10),
                supplied_item_condition="normal",
                item=knowledge,
                order=request_order,
            ),
        )
        # The stock order is the default row (the Store Keeper's own work); the patient's
        # return is named apart (Q-K43 A: absent for every restricted role)
        stock = self.name(
            "delivery_order",
            self.make(
                DeliveryOrder,
                name="Stock from supplier",
                status="pending",
                destination=place,
            ),
        )
        self.name(
            "supply_delivery",
            self.make(
                SupplyDelivery,
                status="in_progress",
                supplied_item=product,
                supplied_item_quantity=Decimal(1),
                supplied_item_condition="normal",
                delivery_type="product",
                order=stock,
                supply_request=wanted,
            ),
        )
        returned = self.name(
            "return_order",
            self.make(
                DeliveryOrder,
                name=f"Medication return - {w.patient.name} {marker('return')}",
                status="pending",
                destination=place,
                patient=w.patient,
            ),
        )
        self.name(
            "return_delivery",
            self.make(
                SupplyDelivery,
                status="in_progress",
                supplied_item=product,
                supplied_item_quantity=Decimal(1),
                supplied_item_condition="normal",
                delivery_type="product",
                order=returned,
                supply_request=wanted,
            ),
        )

    def records(self):
        """Bed history, device histories, a resource request and an account report."""
        from care.emr.models.device import (
            DeviceEncounterHistory,
            DeviceLocationHistory,
            DeviceServiceHistory,
        )
        from care.emr.models.location import FacilityLocationEncounter
        from care.emr.models.report.report_upload import ReportUpload
        from care.emr.models.report.template import Template
        from care.emr.models.resource_request import (
            ResourceRequest,
            ResourceRequestComment,
        )

        w = self.w
        self.name(
            "bed_association",
            self.make(
                FacilityLocationEncounter,
                location=w.bed,
                encounter=w.encounter,
                status="active",
                start_datetime=self.now,
            ),
        )
        # A place the Lab Technician may list (can_list_facility_locations there), with the
        # patient's earlier stop in it: the bed-history swap is what refuses it
        self.make(
            FacilityLocationEncounter,
            location=self.lab,
            encounter=w.encounter,
            status="completed",
            start_datetime=self.now - datetime.timedelta(hours=2),
            end_datetime=self.now - datetime.timedelta(hours=1),
        )
        device = w.device
        device.current_location = w.bed
        device.save()
        self.name(
            "device_location_history",
            self.make(
                DeviceLocationHistory, device=device, location=w.bed, start=self.now
            ),
        )
        self.name(
            "device_encounter_history",
            self.make(
                DeviceEncounterHistory,
                device=device,
                encounter=w.encounter,
                start=self.now,
            ),
        )
        self.name(
            "device_service_history",
            self.make(
                DeviceServiceHistory,
                device=device,
                serviced_on=self.now,
                note=marker("device-service"),
            ),
        )
        request = self.name(
            "resource_request",
            self.make(
                ResourceRequest,
                origin_facility=w.facility,
                approving_facility=w.facility,
                title=f"Oxygen for ward {marker('resource')}",
                reason=marker("resource"),
                status="pending",
                category="patient_care",
                priority=1,
                related_patient=w.patient,
            ),
        )
        self.name(
            "resource_comment",
            self.make(
                ResourceRequestComment, request=request, comment=marker("resource")
            ),
        )
        account = self.objects["account"]
        template = self.name(
            "template",
            self.make(
                Template,
                facility=w.facility,
                slug=f"f-{w.facility.external_id}-final-bill",
                name="Final bill with diagnosis",
                status="active",
                template_type="account_report",
                default_format="pdf",
                template_data="<p>{{ encounter.diagnoses }}</p>",
            ),
        )
        report = ReportUpload(
            template=template,
            name=f"Final bill {marker('account-report')}",
            internal_name="final-bill.pdf",
            associating_id=str(account.external_id),
            upload_completed=True,
            report_type="account_report",
            meta={
                "mime_type": "application/pdf",
                "template_id": str(template.external_id),
                "output_format": "pdf",
            },
            created_by=w.users["Facility Admin"],
            updated_by=w.users["Facility Admin"],
        )
        report.save()
        self.name("report", report)

    def packs(self):
        """A reference pack item waiting for the hospital admin (P1-T15b1): the pack update
        route's ``<pk>`` names a real item, so its matrix row shows the gate, not a 404."""
        from ihr_refdata.models import PackItem

        self.w.extra["pack_item"] = PackItem.objects.create(
            pack="matrix-pack",
            kind="valueset",
            item="matrix-item",
            state=PackItem.State.PENDING,
            reason=PackItem.Reason.UNKNOWN_ORIGIN,
            pending_version=1,
            pending_sha256="0" * 64,
            changed_at=self.now,
        )

    def procedures(self):
        """CARE 2026-11's clinical rows on the first patient's visit (P2-T0a): a procedure, an
        immunisation and an immunisation recommendation, each with a marker."""
        from care.emr.models.immunisation import (
            Immunization,
            ImmunizationRecommendation,
        )
        from care.emr.models.procedure import Procedure

        w = self.w
        common = {"patient": w.patient, "encounter": w.encounter}
        self.name(
            "procedure",
            self.make(
                Procedure,
                status="completed",
                code={
                    "system": "http://snomed.info/sct",
                    "code": "80146002",
                    "display": marker("procedure-code"),
                },
                recorded_date=self.now,
                note=marker("procedure"),
                **common,
            ),
        )
        recommendation = self.name(
            "immunization_recommendation",
            self.make(
                ImmunizationRecommendation,
                forecast_status="due",
                is_group=False,
                description=marker("immunization-recommendation"),
                **common,
            ),
        )
        self.name(
            "immunization",
            self.make(
                Immunization,
                status="completed",
                primary_source=True,
                occurrence=self.now,
                code={
                    "system": "http://hl7.org/fhir/sid/cvx",
                    "code": "08",
                    "display": "Hepatitis B",
                },
                note=marker("immunization"),
                recommendation=recommendation,
                facility=w.facility,
                **common,
            ),
        )

    def referrals(self):
        """A referral of the first patient's visit to the hospital's physiotherapy desk (P2-T0a),
        assigned to every person of the matrix: CARE lists a referral to its assignees with no
        permission check (``referral_assigned``), and each row names the patient and the
        referral's clinical note (ihr_access/referrals.py). It carries an attached request, a
        form answer and a note thread."""
        from care.emr.models.notes import NoteMessage, NoteThread
        from care.emr.models.referral import (
            Referral,
            ReferralAvailability,
            ReferralQuestionnaireResponse,
            ReferralResource,
        )

        w = self.w
        availability = self.name(
            "referral_availability",
            self.make(
                ReferralAvailability,
                entity_type="facility",
                facility=w.facility,
                status="active",
                name="Physiotherapy desk",
                referral_type="consultation",
            ),
        )
        role = {
            "system": "http://snomed.info/sct",
            "code": "36682004",
            "display": "Physiotherapist",
        }
        people = sorted({u.pk for u in w.users.values()})
        thread = self.make(
            NoteThread,
            title="Referral talk",
            thread_type="referral",
            patient=w.patient,
            encounter=w.encounter,
        )
        self.name(
            "referral_note",
            self.make(NoteMessage, thread=thread, message=marker("referral-message")),
        )
        referral = self.name(
            "referral",
            self.make(
                Referral,
                availability=availability,
                patient=w.patient,
                encounter=w.encounter,
                title=f"Knee rehabilitation {marker('referral-title')}",
                note=marker("referral-note"),
                assignees=[{"user_id": pk, "role": role} for pk in people],
                thread=thread,
            ),
        )
        self.name(
            "referral_resource",
            self.make(
                ReferralResource,
                referral=referral,
                resource_type="service_request",
                resource_id=self.objects["service_request"].external_id,
            ),
        )
        self.name(
            "referral_questionnaire_response",
            self.make(
                ReferralQuestionnaireResponse,
                referral=referral,
                questionnaire=self.objects["questionnaire"],
                questionnaire_response={
                    "responses": [
                        {
                            "question_id": "q1",
                            "values": [{"value": marker("referral-answer")}],
                        }
                    ]
                },
            ),
        )

    def actions(self):
        """One performable APPOINTMENT action for the whole hospital (P2-T0a, review F2): a
        booking's ``execute_action`` and a slot's ``create_appointment`` run it. Its message is
        an expression that evaluates to the canary, so the definition (which every signed-in
        person may read) holds no marker and only a run's output does."""
        from care.emr.models.action import Action

        head, tail = CANARY[:5], CANARY[5:]
        self.w.extra["action_output"] = f"{CANARY}-action-output"
        self.name(
            "action",
            self.make(
                Action,
                name="Front desk greeting",
                description="Shown when an appointment is booked",
                action_context="APPOINTMENT",
                performable=True,
                actions=[
                    {
                        "condition": "True",
                        "instructions": [
                            {
                                "slug": "show_message",
                                "context": "self",
                                "params": {
                                    "message": f"{{{{'{head}' + '{tail}-action-output'}}}}"
                                },
                            }
                        ],
                    }
                ],
            ),
        )

    def people(self):
        """A Pharmacist at the store, a Nurse once on the care team who moved to the front
        desk (her PatientUser row stays; "After the attack" §3), and a second MRD clerk with a
        live records grant on the first patient (P1-T11b4: the matrix's "mrdg" viewer)."""
        w = self.w
        w.users["Pharmacist"] = w.user(
            "acc-pharmacist", ("Pharmacist", w.departments["Stores"])
        )
        nurse = nurse_on_care_team(w)
        move_to(w, nurse, "Receptionist")
        w.users["Moved Nurse"] = nurse
        w.users["MRD Granted"] = w.user("acc-mrd-granted", ("MRD", w.root))
        w.extra["records_grant"] = grant_records(
            w, w.users["MRD Granted"], w.patient, hours=24
        )
