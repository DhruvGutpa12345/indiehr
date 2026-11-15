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


def build_world():
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
            "display": f"ART registration {CANARY}",
            "required": False,
            "unique": False,
            "regex": "",
        },
    )
    for config, value in (
        (phone_config, "+919876543210"),
        (programme_config, f"ART-{CANARY}"),
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
        name=f"Dr Paediatrics OPD {CANARY}",
        valid_from=now - datetime.timedelta(days=1),
        valid_to=now + datetime.timedelta(days=30),
    )
    availability = Availability.objects.create(
        schedule=schedule,
        name=f"Morning {CANARY}",
        slot_type="appointment",
        slot_size_in_minutes=15,
        tokens_per_slot=1,
        availability=[],
    )
    slot = TokenSlot.objects.create(
        resource=resource,
        availability=availability,
        start_datetime=now,
        end_datetime=now + datetime.timedelta(minutes=15),
    )
    booking = TokenBooking.objects.create(
        token_slot=slot,
        patient=patient,
        booked_by=users["Receptionist"],
        status="booked",
        note=f"booking note {CANARY}",
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
            display=f"{label} {CANARY}",
            category="diagnosis",
            resource=resource,
        )
        for key, tag_facility, resource, label in (
            ("encounter", facility, "encounter", "Oncology"),
            ("patient_instance", None, "patient", "HIV positive"),
            ("patient_facility", facility, "patient", "TB programme"),
            ("booking", facility, "token_booking", "Psychiatry"),
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
