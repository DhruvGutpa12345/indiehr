"""The serializer guard on real objects (P1-T11b1; design-b.md §9 test 7, "After the attack" §2,
§10 test 1). Restricted users get the projection wherever CARE embeds the shape; CARE's own
roles get CARE's bodies unchanged."""

import datetime
import json

from care.emr.models.account import Account
from care.emr.models.charge_item import ChargeItem
from care.emr.models.charge_item_definition import ChargeItemDefinition
from care.emr.models.location import FacilityLocation, FacilityLocationEncounter
from care.emr.resources.device.spec import DeviceRetrieveSpec
from care.emr.resources.encounter.spec import EncounterListSpec, EncounterRetrieveSpec
from care.emr.resources.location.spec import FacilityLocationListSpec
from care.emr.resources.patient.spec import PatientListSpec, PatientRetrieveSpec
from care.emr.resources.scheduling.slot.spec import TokenBookingReadSpec
from care.emr.resources.scheduling.token.spec import TokenRetrieveSpec
from care.facility.models import Facility
from care.users.models import User
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from django_tenants.utils import schema_context
from ihr_identity import testing as identity
from ihr_platform.models import Hospital
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_roles.registry import NEVER_RESTRICTED
from model_bakery import baker
from rest_framework.test import APIClient

from ihr_access import guard, state
from ihr_access.models import RecordsGrant
from ihr_access.testing import (
    CANARY,
    PHONE_SYSTEM,
    PROGRAMME_SYSTEM,
    build_world,
    marker,
)

HOST = "testserver"


def tag_displays(tags):
    return [t["display"] for t in tags]


class AccessCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.world = build_world()
        cls.account = Account.objects.create(
            facility=cls.world.facility,
            patient=cls.world.patient,
            primary_encounter=cls.world.encounter,
            status="active",
            billing_status="open",
            name="IPD account",
        )

    def tearDown(self):
        state.end()

    def as_user(self, user):
        """Serialize as if inside ``user``'s request (the DRF hook's state)."""
        state.begin(user)

    def api(self, user):
        client = APIClient(HTTP_HOST=HOST)
        client.force_authenticate(user)
        return client


class EncounterProjection(AccessCase):
    def test_projection_keeps_only_listed_fields(self):
        w = self.world
        full = EncounterRetrieveSpec.serialize(
            w.encounter, user=w.users["Doctor"]
        ).to_json()
        self.assertIn(CANARY, json.dumps(full, default=str))
        # the world's encounter carries a tag that names an illness (review C7)
        self.assertEqual(
            tag_displays(full["tags"]), [f"Oncology {marker('tag-encounter')}"]
        )
        self.as_user(w.users["Receptionist"])
        projected = EncounterRetrieveSpec.serialize(
            w.encounter, user=w.users["Receptionist"]
        ).to_json()
        text = json.dumps(projected, default=str)
        self.assertNotIn(CANARY, text)
        # every key stays present; dropped ones hold their defaults
        self.assertEqual(set(projected), set(full))
        self.assertIsNone(projected["discharge_summary_advice"])
        self.assertEqual(projected["care_team"], [])
        self.assertEqual(projected["organizations"], [])
        self.assertIsNone(projected["current_location"])
        self.assertEqual(projected["location_history"], [])
        self.assertEqual(projected["tags"], [])
        self.assertEqual(projected["extensions"], {})
        self.assertEqual(
            projected["hospitalization"],
            {
                "admit_source": "outp",
                "re_admission": False,
                "discharge_disposition": None,
                "diet_preference": None,
            },
        )
        self.assertEqual(
            set(projected["appointment"]), {"id", "status", "booked_on", "token_slot"}
        )
        self.assertEqual(
            set(projected["appointment"]["token_slot"]),
            {"start_datetime", "end_datetime"},
        )
        self.assertEqual(projected["status"], "in_progress")
        self.assertEqual(projected["encounter_class"], "imp")
        self.assertEqual(projected["id"], str(w.encounter.external_id))
        # the embedded patient is projected too
        self.assertIsNone(projected["patient"]["blood_group"])
        self.assertEqual(projected["patient"]["name"], w.patient.name)
        # a kept field's value is CARE's own
        rules = guard.SHAPES["encounter"].rules
        for name, verb in rules.items():
            if "." in name or verb != "keep" or guard._children(rules, name):
                continue
            with self.subTest(field=name):
                self.assertEqual(projected[name], full[name])

    def test_list_shape_and_its_subclasses_are_projected(self):
        w = self.world
        self.as_user(w.users["Cashier"])
        listed = EncounterListSpec.serialize(w.encounter).to_json()
        self.assertNotIn(CANARY, json.dumps(listed, default=str))
        self.assertEqual(listed["care_team"], [])

    def test_care_roles_see_everything(self):
        w = self.world
        for role in ("Doctor", "Nurse", "Facility Admin"):
            with self.subTest(role=role):
                self.as_user(w.users[role])
                body = EncounterRetrieveSpec.serialize(w.encounter).to_json()
                self.assertEqual(body["discharge_summary_advice"], f"advice {CANARY}")
                self.assertEqual(body["patient"]["blood_group"], "AB_negative")
                self.assertTrue(body["care_team"])
                state.end()

    def test_the_radiologist_sees_a_chart_only_with_radiology(self):
        """The chart rule (P1-T11b3, Q-K44 A): the Radiologist, a clinical role of ours, gets a
        patient and visit unprojected only where CARE gives them that patient's clinical data
        (Radiology on an open visit); elsewhere (an imaging request of another department's
        patient embeds both) they are projected."""
        from care.emr.models.encounter import EncounterOrganization

        w = self.world
        radiologist = w.users["Radiologist"]
        self.as_user(radiologist)
        self.assertTrue(state.current().chart_limited)
        body = EncounterRetrieveSpec.serialize(w.encounter).to_json()
        self.assertNotIn(CANARY, json.dumps(body, default=str))
        self.assertIsNone(body["discharge_summary_advice"])
        self.assertIsNone(body["patient"]["blood_group"])
        patient = PatientRetrieveSpec.serialize(
            w.patient, facility=w.facility
        ).to_json()
        self.assertIsNone(patient["blood_group"])
        state.end()
        EncounterOrganization.objects.create(
            encounter=w.encounter, organization=w.departments["Radiology"]
        )
        w.encounter.save()
        self.as_user(radiologist)
        body = EncounterRetrieveSpec.serialize(w.encounter).to_json()
        self.assertEqual(body["discharge_summary_advice"], f"advice {CANARY}")
        self.assertEqual(body["patient"]["blood_group"], "AB_negative")
        # a Radiologist who also holds a CARE role is CARE's (Q-K35): never chart-limited
        doctor_too = w.user(
            "acc-rad-doctor",
            ("Radiologist", w.departments["Radiology"]),
            ("Doctor", w.departments["Stores"]),
        )
        state.end()
        self.as_user(doctor_too)
        self.assertFalse(state.current().chart_limited)

    def test_the_guard_is_off_outside_requests(self):
        body = EncounterRetrieveSpec.serialize(self.world.encounter).to_json()
        self.assertIn(CANARY, json.dumps(body, default=str))


class PatientProjection(AccessCase):
    def test_programme_identifier_and_clinical_fields_are_hidden(self):
        w = self.world
        full = PatientRetrieveSpec.serialize(w.patient, facility=w.facility).to_json()
        self.assertEqual(
            {i["config"]["config"]["system"] for i in full["instance_identifiers"]},
            {PHONE_SYSTEM, PROGRAMME_SYSTEM},
        )
        for role in (
            "Receptionist",
            "Cashier",
            "TPA Desk",
            "MRD",
            "Owner",
            "Store Keeper",
        ):
            with self.subTest(role=role):
                self.as_user(w.users[role])
                body = PatientRetrieveSpec.serialize(
                    w.patient, facility=w.facility
                ).to_json()
                self.assertNotIn(CANARY, json.dumps(body, default=str))
                self.assertEqual(set(body), set(full))
                self.assertEqual(
                    [
                        i["config"]["config"]["system"]
                        for i in body["instance_identifiers"]
                    ],
                    [PHONE_SYSTEM],
                )
                self.assertEqual(
                    body["instance_identifiers"][0]["value"], "+919876543210"
                )
                self.assertIsNone(body["blood_group"])
                self.assertEqual(body["extensions"], {})
                self.assertEqual(body["phone_number"], "+919876543210")
                state.end()

    def test_patient_tags_are_hidden(self):
        """Instance and facility tags can name an illness (review C7)."""
        w = self.world
        full = PatientRetrieveSpec.serialize(w.patient, facility=w.facility).to_json()
        self.assertEqual(
            tag_displays(full["instance_tags"]),
            [f"HIV positive {marker('tag-patient')}"],
        )
        self.assertEqual(
            tag_displays(full["facility_tags"]),
            [f"TB programme {marker('tag-patient-facility')}"],
        )
        for role in ("Receptionist", "Cashier", "MRD"):
            with self.subTest(role=role):
                self.as_user(w.users[role])
                body = PatientRetrieveSpec.serialize(
                    w.patient, facility=w.facility
                ).to_json()
                self.assertEqual(body["instance_tags"], [])
                self.assertEqual(body["facility_tags"], [])
                self.assertNotIn(CANARY, json.dumps(body, default=str))
                state.end()

    def test_permissions_hold_no_clinical_slug(self):
        w = self.world
        receptionist = w.users["Receptionist"]
        self.as_user(receptionist)
        body = PatientRetrieveSpec.serialize(w.patient, user=receptionist).to_json()
        self.assertIn("ihr_view_patient_demographics", body["permissions"])
        self.assertEqual(set(body["permissions"]) & NEVER_RESTRICTED, set())
        state.end()
        # A Receptionist who is also a Doctor, under our forced projection (P1-T11b2's branch)
        both = w.user("acc-rec-doc", ("Receptionist", w.root), ("Doctor", w.root))
        self.as_user(both)
        full = PatientRetrieveSpec.serialize(w.patient, user=both).to_json()
        self.assertIn(
            "can_view_clinical_data", full["permissions"]
        )  # not restricted: CARE's
        state.force_projection()
        body = PatientRetrieveSpec.serialize(w.patient, user=both).to_json()
        self.assertEqual(set(body["permissions"]) & NEVER_RESTRICTED, set())
        self.assertIsNone(body["blood_group"])

    def test_a_patient_under_a_live_records_grant_is_not_projected(self):
        w = self.world
        mrd = w.users["MRD"]
        grant = RecordsGrant.objects.create(
            patient=w.patient,
            user=mrd,
            reason="insurer",
            reference="TEST-1",
            expires_at=timezone.now() + datetime.timedelta(minutes=30),
        )
        self.as_user(mrd)
        self.assertEqual(
            PatientListSpec.serialize(w.patient).to_json()["blood_group"], "AB_negative"
        )
        encounter = EncounterListSpec.serialize(w.encounter).to_json()
        self.assertEqual(encounter["discharge_summary_advice"], f"advice {CANARY}")
        state.end()
        grant.expires_at = timezone.now() - datetime.timedelta(seconds=1)
        grant.save()
        self.as_user(mrd)
        self.assertIsNone(PatientListSpec.serialize(w.patient).to_json()["blood_group"])
        # Another restricted user is never exempt through MRD's grant
        state.end()
        grant.expires_at = timezone.now() + datetime.timedelta(minutes=30)
        grant.save()
        self.as_user(w.users["Receptionist"])
        self.assertIsNone(PatientListSpec.serialize(w.patient).to_json()["blood_group"])


class BedAndDevice(AccessCase):
    def test_the_bed_occupant_is_hidden(self):
        w = self.world
        self.assertIsNotNone(
            FacilityLocationListSpec.serialize(w.bed).to_json()["current_encounter"]
        )
        for role in ("Lab Technician", "Store Keeper", "Receptionist", "Owner"):
            with self.subTest(role=role):
                self.as_user(w.users[role])
                body = FacilityLocationListSpec.serialize(w.bed).to_json()
                self.assertIsNone(body["current_encounter"])
                self.assertEqual(body["name"], "Bed 1")
                self.assertNotIn(CANARY, json.dumps(body, default=str))
                state.end()

    def test_the_owner_sees_no_one_on_a_device(self):
        """A device's list and retrieve need only facility membership in CARE (A2). Since
        P1-T11b2 the device swap refuses the device to restricted users ("After the attack"
        §7.2), and the guard still drops the patient wherever CARE embeds a device."""
        w = self.world
        url = (
            f"/api/v1/facility/{w.facility.external_id}/device/{w.device.external_id}/"
        )
        doctor = self.api(w.users["Facility Admin"]).get(url)
        self.assertEqual(doctor.status_code, 200, doctor.content[:300])
        self.assertIsNotNone(doctor.json()["current_encounter"])
        owner = self.api(w.users["Owner"]).get(url)
        self.assertEqual(owner.status_code, 403, owner.content[:300])
        self.assertNotIn(CANARY, owner.content.decode())
        self.assertNotIn(w.patient.name, owner.content.decode())
        self.as_user(w.users["Owner"])
        body = DeviceRetrieveSpec.serialize(w.device).to_json()
        self.assertIsNone(body["current_encounter"])
        self.assertNotIn(CANARY, json.dumps(body, default=str))

    def test_device_shape_unit(self):
        w = self.world
        self.as_user(w.users["Owner"])
        body = DeviceRetrieveSpec.serialize(w.device).to_json()
        self.assertIsNone(body["current_encounter"])
        self.assertEqual(body["registered_name"], "Monitor 1")


class BookingAndToken(AccessCase):
    def test_a_booking_is_projected_only_under_forced_projection(self):
        w = self.world
        receptionist = w.users["Receptionist"]
        self.as_user(receptionist)
        own_screen = TokenBookingReadSpec.serialize(w.booking).to_json()
        self.assertEqual(
            own_screen["note"], f"booking note {marker('booking-note')}"
        )  # CARE-granted screen
        self.assertEqual(
            tag_displays(own_screen["tags"]), [f"Psychiatry {marker('booking-tag')}"]
        )
        self.assertIsNone(own_screen["patient"]["blood_group"])  # the patient still is
        state.force_projection()
        forced = TokenBookingReadSpec.serialize(w.booking).to_json()
        self.assertEqual(set(forced), set(own_screen))
        self.assertEqual(forced["note"], "")
        self.assertEqual(forced["tags"], [])
        self.assertEqual(forced["resource"], {})
        self.assertIsNone(forced["token_slot"]["availability"])
        self.assertNotIn(CANARY, json.dumps(forced, default=str))

    def test_token_projection(self):
        token = TokenRetrieveSpec.model_construct(
            id="t-1",
            number=7,
            status="created",
            note=f"token note {CANARY}",
            sub_queue={"name": f"Room 3 {CANARY}"},
            resource={"first_name": f"Dr {CANARY}"},
            queue={"id": "q-1", "name": f"Dr X OPD {CANARY}", "date": "2026-11-14"},
            booking={"id": "b-1", "note": f"b {CANARY}", "status": "booked"},
            patient={"name": "Asha", "blood_group": "O_negative", "unknown": CANARY},
            category={"name": "General"},
            resource_type="practitioner",
            created_by={"username": "x"},
        )
        body = guard.project_instance(
            TokenRetrieveSpec, token, guard.SHAPES["token"]
        ).to_json()
        self.assertNotIn(CANARY, json.dumps(body, default=str))
        self.assertEqual(body["number"], 7)
        self.assertEqual(
            body["queue"], {"id": "q-1", "name": None, "date": "2026-11-14"}
        )
        self.assertEqual(body["patient"], {"name": "Asha", "blood_group": None})


class ThroughTheApi(AccessCase):
    """The account embeds the full patient and encounter (account/spec.py:99-117): through the
    real request path, signed in as staff."""

    def get_account(self, client):
        url = f"/api/v1/facility/{self.world.facility.external_id}/account/{self.account.external_id}/"
        response = client.get(url)
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response

    def test_cashier_and_tpa_see_the_projection_doctor_sees_everything(self):
        w = self.world
        with schema_context("public"):
            hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
        with identity.fake_keycloak():
            for role in ("Cashier", "TPA Desk", "Receptionist"):
                with self.subTest(role=role):
                    client = identity.staff_client(hospital, HOST, w.users[role])
                    response = self.get_account(client)
                    text = response.content.decode()
                    self.assertNotIn(CANARY, text)
                    body = response.json()
                    self.assertIsNone(body["patient"]["blood_group"])
                    self.assertIsNone(
                        body["primary_encounter"]["discharge_summary_advice"]
                    )
                    self.assertEqual(body["primary_encounter"]["care_team"], [])
                    self.assertEqual(body["patient"]["name"], w.patient.name)
            doctor = identity.staff_client(hospital, HOST, w.users["Doctor"])
            body = self.get_account(doctor).json()
        self.assertEqual(body["patient"]["blood_group"], "AB_negative")
        self.assertEqual(
            body["primary_encounter"]["discharge_summary_advice"], f"advice {CANARY}"
        )
        self.assertEqual(connection.schema_name, TEST_SCHEMA)

    def add_bed_stays(self):
        """The visit's bed stays: the bed now, and an earlier stop elsewhere (CARE's room-rent
        table lists every stay of the account's visit)."""
        w = self.world
        now = timezone.now()
        earlier = FacilityLocation.objects.create(
            facility=w.facility,
            name="Casualty bay",
            description="casualty",
            status="active",
            operational_status="O",
            mode="instance",
            form="bd",
        )
        return [
            FacilityLocationEncounter.objects.create(
                location=w.bed, encounter=w.encounter, status="active",
                start_datetime=now,
            ),
            FacilityLocationEncounter.objects.create(
                location=earlier, encounter=w.encounter, status="completed",
                start_datetime=now - datetime.timedelta(days=2),
                end_datetime=now,
            ),
        ]  # fmt: skip

    def get_encounter(self, client, encounter):
        return client.get(f"/api/v1/encounter/{encounter.external_id}/")

    def test_billing_roles_see_the_bed_for_room_rent(self):
        """Q-K37a B (owner, 2026-11-15): the Cashier and TPA Desk see the bed's id and name,
        each stay's id, times and status (what BedChargeItemsTable.tsx reads from the account's
        visit), and nothing more of the place: no ward, no occupant. The treating doctor and
        the departments stay hidden."""
        w = self.world
        stays = self.add_bed_stays()
        expected = sorted(str(stay.external_id) for stay in stays)
        for role in ("Cashier", "TPA Desk"):
            client = self.api(w.users[role])
            bodies = {
                "account": self.get_account(client).json()["primary_encounter"],
                "encounter": self.get_encounter(client, w.encounter).json(),
            }
            for where, encounter in bodies.items():
                with self.subTest(role=role, where=where):
                    self.assertNotIn(CANARY, json.dumps(encounter))
                    self.assertEqual(
                        encounter["current_location"],
                        {"id": str(w.bed.external_id), "name": "Bed 1"},
                    )
                    history = encounter["location_history"]
                    self.assertEqual(sorted(h["id"] for h in history), expected)
                    for stay in history:
                        self.assertEqual(
                            set(stay),
                            {"id", "start_datetime", "end_datetime", "status",
                             "location"},
                        )  # fmt: skip
                        self.assertEqual(set(stay["location"]), {"id", "name"})
                    names = {h["location"]["name"]: h for h in history}
                    self.assertEqual(set(names), {"Bed 1", "Casualty bay"})
                    self.assertEqual(names["Bed 1"]["status"], "active")
                    self.assertIsNotNone(names["Casualty bay"]["end_datetime"])
                    self.assertEqual(encounter["care_team"], [])
                    self.assertEqual(encounter["organizations"], [])
                    self.assertEqual(encounter["tags"], [])
                    self.assertIsNone(encounter["discharge_summary_advice"])
                    self.assertNotIn("Ward A", json.dumps(encounter))
        doctor = self.get_account(self.api(w.users["Doctor"])).json()
        self.assertEqual(
            doctor["primary_encounter"]["current_location"]["parent"]["name"], "Ward A"
        )
        self.assertTrue(doctor["primary_encounter"]["care_team"])

    def test_the_cashier_adds_room_rent_with_the_projected_stay(self):
        """Review C-C1, the flow B was built for: the Cashier takes a stay's id from the
        projected account body, adds a room-rent charge for it (CARE checks the stay is the
        visit's) and lists it back as CARE's room-rent table does. The TPA Desk reads the stays
        but cannot add one (no can_create_charge_item)."""
        w = self.world
        self.add_bed_stays()
        definition = baker.make(
            ChargeItemDefinition,
            facility=w.facility,
            slug=f"f-{w.facility.external_id}-room-rent",
            title="Room rent (general ward)",
            status="active",
            price_components=[
                {"amount": "1500", "monetary_component_type": "base", "factor": None}
            ],
        )
        base = f"/api/v1/facility/{w.facility.external_id}/charge_item/"

        def add(client):
            encounter = self.get_account(client).json()["primary_encounter"]
            stay = next(
                h for h in encounter["location_history"] if h["status"] == "active"
            )
            request = {
                "charge_item_definition": definition.slug,
                "quantity": 1,
                "encounter": str(w.encounter.external_id),
                "account": str(self.account.external_id),
                "service_resource": "bed_association",
                "service_resource_id": stay["id"],
            }
            response = client.post(
                f"{base}apply_charge_item_defs/", {"requests": [request]}, format="json"
            )
            return stay["id"], response

        cashier = self.api(w.users["Cashier"])
        stay_id, response = add(cashier)
        self.assertEqual(response.status_code, 200, response.content[:300])
        item = ChargeItem.objects.get(
            encounter=w.encounter, service_resource="bed_association"
        )
        self.assertEqual(item.service_resource_id, stay_id)
        self.assertEqual(item.account_id, self.account.id)
        listed = cashier.get(
            base,
            {
                "account": str(self.account.external_id),
                "service_resource": "bed_association",
            },
        )
        self.assertEqual(listed.status_code, 200, listed.content[:300])
        self.assertEqual(
            [r["service_resource_id"] for r in listed.json()["results"]], [stay_id]
        )
        _stay, response = add(self.api(w.users["TPA Desk"]))
        self.assertEqual(response.status_code, 403, response.content[:300])
        self.assertEqual(
            ChargeItem.objects.filter(service_resource="bed_association").count(), 1
        )

    def test_other_desk_roles_still_see_no_bed(self):
        """Q-K37 A stays for the Receptionist and MRD (and every other restricted role): no
        ward, bed or bed history on a visit they reach."""
        w = self.world
        self.add_bed_stays()
        receptionist = self.api(w.users["Receptionist"])
        bodies = {
            ("Receptionist", "account"): self.get_account(receptionist).json()[
                "primary_encounter"
            ],
        }
        for role in ("Receptionist", "MRD"):
            response = self.get_encounter(self.api(w.users[role]), w.encounter)
            self.assertEqual(response.status_code, 200, response.content[:300])
            bodies[role, "encounter"] = response.json()
        for (role, where), encounter in bodies.items():
            with self.subTest(role=role, where=where):
                self.assertIsNone(encounter["current_location"])
                self.assertEqual(encounter["location_history"], [])
                self.assertNotIn("Bed 1", json.dumps(encounter))

    def test_the_bed_needs_the_billing_role_in_the_visits_facility(self):
        """A Receptionist here who is a Cashier in another facility of the hospital sees no bed
        on this facility's visits; nor does a Cashier of that other facility reach them."""
        w = self.world
        self.add_bed_stays()
        other = baker.make(Facility, created_by=w.admin, name="Other Facility")
        both = w.user(
            "acc-rec-cash-elsewhere",
            ("Receptionist", w.root),
            ("Cashier", other.default_internal_organization),
        )
        cashier_there = w.user(
            "acc-cash-elsewhere", ("Cashier", other.default_internal_organization)
        )
        encounter = self.get_account(self.api(both)).json()["primary_encounter"]
        self.assertIsNone(encounter["current_location"])
        self.assertEqual(encounter["location_history"], [])
        client = self.api(cashier_there)
        url = f"/api/v1/facility/{w.facility.external_id}/account/{self.account.external_id}/"
        for response in (client.get(url), self.get_encounter(client, w.encounter)):
            with self.subTest(url=response.request["PATH_INFO"]):
                self.assertIn(response.status_code, (403, 404), response.content[:300])
                self.assertNotIn("Bed 1", response.content.decode())

    def test_bed_history_route_stays_refused_to_the_billing_roles(self):
        """B opens the bed on the visit only: a place's history (every visit on the bed) stays
        refused (b2)."""
        w = self.world
        self.add_bed_stays()
        url = (
            f"/api/v1/facility/{w.facility.external_id}/location/"
            f"{w.bed.external_id}/association/"
        )
        for role in ("Cashier", "TPA Desk"):
            with self.subTest(role=role):
                response = self.api(w.users[role]).get(url)
                self.assertEqual(response.status_code, 403, response.content[:300])
                self.assertNotIn(w.patient.name, response.content.decode())

    def test_each_request_starts_afresh(self):
        """A restricted request's state never leaks into the next one on the thread."""
        w = self.world
        self.get_account(self.api(w.users["Cashier"]))
        body = self.get_account(self.api(w.users["Doctor"])).json()
        self.assertEqual(body["patient"]["blood_group"], "AB_negative")
        self.assertIsNone(state.current())


class RestrictedRule(AccessCase):
    def test_who_is_restricted(self):
        w = self.world
        for role in ("Receptionist", "Cashier", "TPA Desk", "Owner", "MRD", "Lab Technician",
                     "Store Keeper"):  # fmt: skip
            with self.subTest(role=role):
                self.assertTrue(state.restricted(w.users[role]))
                self.assertTrue(state.restricted_only(w.users[role]))
        for role in ("Doctor", "Nurse", "Facility Admin", "Radiologist"):
            with self.subTest(role=role):
                self.assertFalse(state.restricted(w.users[role]))
                self.assertFalse(state.restricted_only(w.users[role]))
        both = w.user(
            "acc-rec-doc2",
            ("Receptionist", w.root),
            ("Doctor", w.departments["Paediatrics"]),
        )
        self.assertFalse(state.restricted(both))  # permissions add up (Q-K35)
        self.assertFalse(state.restricted_only(both))
        admin = User.objects.get(pk=w.admin.pk)
        self.assertFalse(state.restricted(admin))
