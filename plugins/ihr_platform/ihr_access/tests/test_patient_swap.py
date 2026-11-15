"""The patient swap (P1-T11b2; design-b.md §5.3 item 1, "After the attack" §5, §10 tests 4-6,
§9 tests 9 (patient writes), 12 and 13). The front desk opens any patient of the hospital and
edits the demographics; the projection holds through our branch whatever else the user holds;
CARE's own roles get CARE's answers."""

import datetime
import json
from unittest import mock

from care.emr.locks.billing import PatientCreateLock
from care.emr.models import Patient, PatientUser
from care.emr.models.organization import OrganizationUser
from care.emr.models.patient import PatientIdentifier, PatientIdentifierConfig
from django.db import connection
from ihr_platform.testing import as_owner
from ihr_tenant_settings import geography
from ihr_tenant_settings.india import GST_STATE_CODES

from ihr_access import branches, views
from ihr_access.testing import CANARY, PHONE_SYSTEM
from ihr_access.tests.base import SwapCase, registration_body

FRONT_DESK = ("Receptionist", "Cashier", "TPA Desk", "MRD")
NO_PATIENTS = ("Owner", "Lab Technician", "Store Keeper")


class OpeningAPatient(SwapCase):
    def url(self, patient=None, action=""):
        patient = patient or self.world.patient
        return f"/api/v1/patient/{patient.external_id}/{action + '/' if action else ''}"

    def test_the_front_desk_opens_any_patient_projected(self):
        w = self.world
        doctor = self.api("Doctor").get(self.url())
        self.assertEqual(doctor.status_code, 200, doctor.content[:300])
        self.assertEqual(doctor.json()["blood_group"], "AB_negative")
        for role in FRONT_DESK:
            with self.subTest(role=role):
                response = self.api(role).get(self.url())
                self.assertEqual(response.status_code, 200, response.content[:300])
                body = response.json()
                self.assertNotIn(CANARY, response.content.decode())
                self.assertEqual(set(body), set(doctor.json()))
                self.assertEqual(body["name"], w.patient.name)
                self.assertIsNone(body["blood_group"])
                self.assertEqual(body["extensions"], {})
                self.assertEqual(
                    [
                        i["config"]["config"]["system"]
                        for i in body["instance_identifiers"]
                    ],
                    [PHONE_SYSTEM],
                )
        for role in NO_PATIENTS:
            with self.subTest(role=role):
                self.assertEqual(self.api(role).get(self.url()).status_code, 404)

    def test_the_web_apps_patient_request_with_the_facility(self):
        """Review C1: the web app always sends ``?facility=`` (PatientProfile.tsx,
        EncounterProvider.tsx). CARE refuses it without ``can_read_tag_config``, which only the
        Receptionist holds; our branch serves every front-desk role, projected, with no
        facility tags. An unknown facility is 404; CARE's own path is unchanged."""
        w = self.world
        facility = {"facility": str(w.facility.external_id)}
        tag = w.tags["patient_facility"].display
        doctor = self.api("Doctor").get(self.url(), facility)
        self.assertEqual(doctor.status_code, 200, doctor.content[:300])
        self.assertIn(tag, doctor.content.decode())
        for role in FRONT_DESK:
            with self.subTest(role=role):
                response = self.api(role).get(self.url(), facility)
                self.assertEqual(response.status_code, 200, response.content[:300])
                self.assertNotIn(CANARY, response.content.decode())
                self.assertIsNone(response.json()["blood_group"])
                unknown = self.api(role).get(
                    self.url(), {"facility": "6f1c8d4e-1111-4222-8333-444455556666"}
                )
                self.assertEqual(unknown.status_code, 404, unknown.content[:300])
        for role in NO_PATIENTS:
            with self.subTest(role=role):
                self.assertEqual(
                    self.api(role).get(self.url(), facility).status_code, 404
                )

    def test_any_patient_of_the_hospital_opens_not_only_one_with_a_visit(self):
        """Q-K34 A: the whole register (a returning patient has no open visit)."""
        patient, _ = self.other_patient()
        response = self.api("Receptionist").get(self.url(patient))
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertIsNone(response.json()["blood_group"])
        self.assertEqual(self.api("Doctor").get(self.url(patient)).status_code, 404)

    def test_receptionist_cannot_add_user_or_tag(self):
        """§10 test 4: the actions our branch does not widen answer 403, also with a target who
        holds a clinical role, and nothing is written."""
        w = self.world
        nurse = w.users["Nurse"]
        tag = w.tags["patient_instance"]
        before = Patient.objects.get(pk=w.patient.pk)
        calls = (
            (
                "add_user",
                {
                    "user": str(nurse.external_id),
                    "role": str(w.role("Doctor").external_id),
                },
            ),
            ("delete_user", {"user": str(w.users["Doctor"].external_id)}),
            ("set_instance_tags", {"tags": [str(tag.external_id)]}),
            ("remove_instance_tags", {"tags": [str(tag.external_id)]}),
            (
                "set_facility_tags",
                {
                    "tags": [str(w.tags["patient_facility"].external_id)],
                    "facility": str(w.facility.external_id),
                },
            ),
            (
                "remove_facility_tags",
                {
                    "tags": [str(w.tags["patient_facility"].external_id)],
                    "facility": str(w.facility.external_id),
                },
            ),
        )
        PatientUser.objects.create(
            user=w.users["Doctor"], patient=w.patient, role=w.role("Doctor")
        )
        for action, body in calls:
            with self.subTest(action=action):
                response = self.api("Receptionist").post(
                    self.url(action=action), body, format="json"
                )
                self.assertEqual(response.status_code, 403, response.content[:300])
                self.assertNotIn(CANARY, response.content.decode())
        get_users = self.api("Receptionist").get(self.url(action="get_users"))
        self.assertEqual(get_users.status_code, 403, get_users.content[:300])
        self.assertFalse(PatientUser.objects.filter(user=nurse).exists())
        self.assertTrue(
            PatientUser.objects.filter(
                user=w.users["Doctor"], patient=w.patient
            ).exists()
        )
        after = Patient.objects.get(pk=w.patient.pk)
        self.assertEqual(after.instance_tags, before.instance_tags)
        self.assertEqual(after.facility_tags, before.facility_tags)

    def test_add_user_refuses_restricted_target(self):
        """A Doctor cannot put a restricted person on a patient's care team (Q-K40): 403 from
        our gate before CARE writes anything (the placement trigger would answer 400)."""
        w = self.world
        # Each request in its own transaction, as in every deployed setting, so a refusal by
        # the trigger (the gate's back-up) is a clean 400 here, not a broken test transaction
        atomic = mock.patch.dict(connection.settings_dict, {"ATOMIC_REQUESTS": True})
        atomic.start()
        self.addCleanup(atomic.stop)
        for target in ("Receptionist", "Owner", "MRD"):
            with self.subTest(target=target):
                response = self.api("Doctor").post(
                    self.url(action="add_user"),
                    {
                        "user": str(w.users[target].external_id),
                        "role": str(w.role("Doctor").external_id),
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, 403, response.content[:300])
                self.assertIn("cannot be given", response.content.decode())
                self.assertFalse(
                    PatientUser.objects.filter(user=w.users[target]).exists()
                )
        # A clinical target is CARE's to decide
        response = self.api("Doctor").post(
            self.url(action="add_user"),
            {
                "user": str(w.users["Nurse"].external_id),
                "role": str(w.role("Nurse").external_id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])

    def test_patient_create_and_update_by_role(self):
        """§9 test 9: the Receptionist registers patients (CARE's create, projected answer);
        nobody else of ours does. Update: the billing and records desks are refused."""
        w = self.world
        body = {
            "name": "Chitra New",
            "gender": "female",
            "phone_number": "+919800011122",
            "emergency_phone_number": "+919800011122",
            "geo_organization": str(w.patient.geo_organization.external_id),
            "age": 30,
            "address": "1 Hill Road, Pune",
            "pincode": 411001,
            "blood_group": "B_positive",
            "identifiers": [],
            "tags": [],
            "extensions": {},
        }
        # CARE releases its patient-create lock on commit, which a TestCase never does
        with self.captureOnCommitCallbacks(execute=True):
            response = self.api("Receptionist").post(
                "/api/v1/patient/", body, format="json"
            )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertIsNone(response.json()["blood_group"])  # projected answer
        created = Patient.objects.get(external_id=response.json()["id"])
        self.assertEqual(
            created.blood_group, "B_positive"
        )  # registration may record it
        for role in ("Cashier", "TPA Desk", "MRD", *NO_PATIENTS):
            with self.subTest(role=role, write="create"):
                response = self.api(role).post(
                    "/api/v1/patient/", {**body, "phone_number": "+919800011133"},
                    format="json",
                )  # fmt: skip
                self.assertEqual(response.status_code, 403, response.content[:300])
        got = self.api("Receptionist").get(self.url()).json()
        for role in ("Cashier", "TPA Desk", "MRD"):
            with self.subTest(role=role, write="update"):
                response = self.api(role).put(
                    self.url(), registration_body(got, address="x"), format="json"
                )
                self.assertEqual(response.status_code, 403, response.content[:300])
        for role in NO_PATIENTS:
            with self.subTest(role=role, write="update"):
                response = self.api(role).put(
                    self.url(), registration_body(got, address="x"), format="json"
                )
                self.assertIn(response.status_code, (403, 404), response.content[:300])
        self.assertEqual(
            Patient.objects.get(pk=w.patient.pk).address, w.patient.address
        )

    def test_actions_are_classified(self):
        """ "After the attack" §5.1: an action upstream adds fails until classified."""
        found = {
            m
            for m in (
                "list",
                "create",
                "retrieve",
                "update",
                "partial_update",
                "destroy",
            )
            if hasattr(views.IhrPatientViewSet, m)
        }
        found |= {a.__name__ for a in views.IhrPatientViewSet.get_extra_actions()}
        self.assertEqual(found, set(views.actions("patient")))
        self.assertEqual(
            views.OPENED, {"retrieve", "update", "partial_update", "update_identifier"}
        )


class EditingDemographics(SwapCase):
    def url(self, patient=None, action=""):
        patient = patient or self.world.patient
        return f"/api/v1/patient/{patient.external_id}/{action + '/' if action else ''}"

    def save(self, who, body, patient=None):
        return self.api(who).put(self.url(patient), body, format="json")

    def test_receptionist_save_keeps_hidden_fields(self):
        """§10 test 5, the replay save: the web app's own body, rebuilt from the projected GET,
        saves the edited address and leaves everything hidden untouched."""
        w = self.world
        before = Patient.objects.get(pk=w.patient.pk)
        programme = PatientIdentifier.objects.get(
            patient=w.patient, config=w.programme_config
        ).value
        got = self.api("Receptionist").get(self.url()).json()
        body = registration_body(got, address="7 FC Road, Pune")
        response = self.save("Receptionist", body)
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.assertNotIn(CANARY, response.content.decode())
        self.assertIsNone(response.json()["blood_group"])
        after = Patient.objects.get(pk=w.patient.pk)
        self.assertEqual(after.address, "7 FC Road, Pune")
        self.assertEqual(after.blood_group, "AB_negative")
        self.assertEqual(after.extensions, {"core": {"note": CANARY}})
        self.assertEqual(after.instance_tags, before.instance_tags)
        self.assertEqual(after.facility_tags, before.facility_tags)
        # A client that sends hidden fields and a programme number changes none of them
        hostile = registration_body(
            got,
            address="8 FC Road, Pune",
            blood_group="O_positive",
            extensions={"core": {"note": "overwritten"}},
            meta={"x": 1},
            identifiers=[
                *body["identifiers"],
                {"config": str(w.programme_config.external_id), "value": "ART-1"},
            ],
        )
        response = self.save("Receptionist", hostile)
        self.assertEqual(response.status_code, 200, response.content[:400])
        after = Patient.objects.get(pk=w.patient.pk)
        self.assertEqual(after.address, "8 FC Road, Pune")
        self.assertEqual(after.blood_group, "AB_negative")
        self.assertEqual(after.extensions, {"core": {"note": CANARY}})
        self.assertEqual(
            PatientIdentifier.objects.get(
                patient=w.patient, config=w.programme_config
            ).value,
            programme,
        )
        # An empty value for it (which CARE reads as "delete") is dropped unread too
        response = self.save(
            "Receptionist",
            registration_body(
                got,
                identifiers=[
                    {"config": str(w.programme_config.external_id), "value": ""}
                ],
            ),
        )
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.assertTrue(
            PatientIdentifier.objects.filter(
                patient=w.patient, config=w.programme_config
            ).exists()
        )

    def test_the_date_of_death_is_clinical(self):
        """§10 test 5: a changed date of death is 400; resent unchanged it is ignored; a Doctor
        still sets it."""
        w = self.world
        got = self.api("Receptionist").get(self.url()).json()
        died = (
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)
        ).isoformat()
        response = self.save(
            "Receptionist", registration_body(got, deceased_datetime=died)
        )
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertIsNone(Patient.objects.get(pk=w.patient.pk).deceased_datetime)
        # A Doctor records it through CARE's own path
        full = self.api("Doctor").get(self.url()).json()
        response = self.save("Doctor", registration_body(full, deceased_datetime=died))
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertIsNotNone(Patient.objects.get(pk=w.patient.pk).deceased_datetime)
        # The front desk sees it and may resend it unchanged, never change or clear it
        got = self.api("Receptionist").get(self.url()).json()
        self.assertIsNotNone(got["deceased_datetime"])
        response = self.save("Receptionist", registration_body(got, address="9 Lane"))
        self.assertEqual(response.status_code, 200, response.content[:300])
        response = self.save(
            "Receptionist", registration_body(got, deceased_datetime=None)
        )
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertIsNotNone(Patient.objects.get(pk=w.patient.pk).deceased_datetime)

    def test_receptionist_updates_an_allowed_identifier(self):
        w = self.world
        response = self.api("Receptionist").post(
            self.url(action="update_identifier"),
            {"config": str(w.phone_config.external_id), "value": "+919800000000"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertNotIn(CANARY, response.content.decode())
        self.assertEqual(
            PatientIdentifier.objects.get(
                patient=w.patient, config=w.phone_config
            ).value,
            "+919800000000",
        )
        # The Cashier opens the patient but edits nothing
        response = self.api("Cashier").post(
            self.url(action="update_identifier"),
            {"config": str(w.phone_config.external_id), "value": "+919800000001"},
            format="json",
        )
        self.assertEqual(response.status_code, 403, response.content[:300])

    def test_two_jobs_keep_to_the_identifier_list(self):
        """Review W2: a Receptionist who is also a Nurse in Paediatrics is not restricted, so
        identifiers.py lets the request through; on a patient she reaches only through the
        front desk's branch, update_identifier still keeps to the identifier list (400, nothing
        written or deleted), as the PUT path does. An allowed identifier is written."""
        w = self.world
        both = w.user(
            "acc-rec-nurse",
            ("Receptionist", w.root),
            ("Nurse", w.departments["Paediatrics"]),
        )
        patient, _ = self.other_patient()
        PatientIdentifier.objects.create(
            patient=patient, config=w.programme_config, value="ART-5"
        )
        url = self.url(patient, "update_identifier")
        programme = str(w.programme_config.external_id)
        for value in ("", "ART-6"):
            with self.subTest(value=value):
                response = self.api(both).post(
                    url, {"config": programme, "value": value}, format="json"
                )
                self.assertEqual(response.status_code, 400, response.content[:300])
                self.assertEqual(
                    PatientIdentifier.objects.get(
                        patient=patient, config=w.programme_config
                    ).value,
                    "ART-5",
                )
        response = self.api(both).post(
            url,
            {"config": str(w.phone_config.external_id), "value": "+919800000002"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertNotIn("ART-5", response.content.decode())

    def test_a_district_move_compares_what_people_can_do(self):
        """Review W3 (a): a Volunteer of the state (who lists patients there) who is a Doctor in
        the new district would read the patient's clinical data after the move: refused, though
        the same person is on the old chain."""
        state_s = self.govt("State V")
        home = self.govt("District V1", state_s)
        watched = self.govt("District V2", state_s)
        person = self.world.user("acc-geo-volunteer")
        OrganizationUser.objects.create(
            organization=state_s, user=person, role=self.world.role("Volunteer")
        )
        OrganizationUser.objects.create(
            organization=watched, user=person, role=self.world.role("Doctor")
        )
        patient, _ = self.other_patient(geo_organization=home, pincode=411001)
        got = self.api("Receptionist").get(self.url(patient)).json()
        body = registration_body(got, geo_organization=str(watched.external_id))
        with mock.patch.object(branches, "emit"):
            response = self.save("Receptionist", body, patient)
        self.assertEqual(response.status_code, 403, response.content[:300])
        self.assertEqual(Patient.objects.get(pk=patient.pk).geo_organization, home)
        answer = self.api(person).get(f"{self.url(patient)}diagnosis/")
        self.assertEqual(answer.status_code, 403, answer.content[:200])

    def test_a_patient_with_no_district_moves_on_the_same_terms(self):
        """Review W3 (b): CARE sets the district to null when its organisation goes. Such a
        patient moves only to the PIN's district, and not when that brings in someone who
        would read the record."""
        from ihr_platform.models import PostOffice

        with as_owner():
            for pincode, district in (
                ("560001", "Bengaluru Urban"),
                ("570001", "Mysuru"),
            ):
                PostOffice.objects.create(
                    pincode=pincode,
                    name=f"{district} HO",
                    office_type="H.O",
                    district=district,
                    state_code="29",
                )
        karnataka = geography.ensure_state("29")
        watched, _ = geography.ensure_district(karnataka, "Bengaluru Urban")
        quiet, _ = geography.ensure_district(karnataka, "Mysuru")
        OrganizationUser.objects.create(
            organization=watched,
            user=self.world.user("acc-geo-doctor-2"),
            role=self.world.role("Doctor"),
        )
        patient, _ = self.other_patient(geo_organization=None, pincode=560001)
        self.assertIsNone(Patient.objects.get(pk=patient.pk).geo_organization)
        got = self.api("Receptionist").get(self.url(patient)).json()

        def move(organization, pincode):
            body = registration_body(
                got, geo_organization=str(organization.external_id), pincode=pincode
            )
            return self.save("Receptionist", body, patient)

        with mock.patch.object(branches, "emit") as emit:
            response = move(watched, 560001)
        self.assertEqual(response.status_code, 403, response.content[:300])
        self.assertEqual(emit.call_count, 1)
        self.assertIsNone(Patient.objects.get(pk=patient.pk).geo_organization)
        with mock.patch.object(branches, "emit"):
            self.assertEqual(move(quiet, 560001).status_code, 403)  # not the PIN's
        response = move(quiet, 570001)
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(Patient.objects.get(pk=patient.pk).geo_organization, quiet)

    def test_moving_a_patients_district(self):
        """§10 test 6 ("After the attack" §5.4): a move that would open the record to a doctor
        placed in a government organisation, or that leaves the state and the PIN's district,
        is refused; a move within the state or to the PIN's district is not."""
        state_s = self.govt("State S")
        home = self.govt("District One", state_s)
        watched = self.govt("District Two", state_s)
        quiet = self.govt("District Three", state_s)
        elsewhere = self.govt("District Far", self.govt("State T"))
        geo_doctor = self.world.user("acc-geo-doctor")
        OrganizationUser.objects.create(
            organization=watched, user=geo_doctor, role=self.world.role("Doctor")
        )
        patient, _ = self.other_patient(geo_organization=home, pincode=411001)
        got = self.api("Receptionist").get(self.url(patient)).json()

        def move(organization, **changes):
            body = registration_body(
                got, geo_organization=str(organization.external_id), **changes
            )
            return self.save("Receptionist", body, patient)

        with mock.patch.object(branches, "emit") as emit:
            response = move(watched)
        self.assertEqual(response.status_code, 403, response.content[:300])
        self.assertEqual(
            [c.args[0] for c in emit.call_args_list],
            ["ihr-access: refused geo_move hospital=" + self.hospital_slug()],
        )
        self.assertEqual(Patient.objects.get(pk=patient.pk).geo_organization, home)
        # ...and that doctor still cannot read the patient's clinical data
        for route in ("symptom", "diagnosis"):
            answer = self.api(geo_doctor).get(f"{self.url(patient)}{route}/")
            self.assertEqual(answer.status_code, 403, answer.content[:200])
        with mock.patch.object(branches, "emit"):
            self.assertEqual(move(elsewhere, pincode=411001).status_code, 403)
        self.assertEqual(Patient.objects.get(pk=patient.pk).geo_organization, home)
        # Within the state, nobody new: accepted
        self.assertEqual(move(quiet).status_code, 200)
        self.assertEqual(Patient.objects.get(pk=patient.pk).geo_organization, quiet)
        # Another state, but India Post puts the PIN in that district: accepted
        from ihr_platform.models import PostOffice

        with as_owner():
            PostOffice.objects.create(
                pincode="560001",
                name="Bangalore GPO",
                office_type="H.O",
                district="Bengaluru Urban",
                state_code="29",
            )
        karnataka = geography.ensure_state("29")
        self.assertEqual(karnataka.name, GST_STATE_CODES["29"])
        bengaluru, _ = geography.ensure_district(karnataka, "Bengaluru Urban")
        got = self.api("Receptionist").get(self.url(patient)).json()
        with mock.patch.object(branches, "emit"):
            self.assertEqual(move(bengaluru, pincode=440001).status_code, 403)
        response = move(bengaluru, pincode=560001)
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(Patient.objects.get(pk=patient.pk).geo_organization, bengaluru)

    def hospital_slug(self):
        return connection.tenant.slug


class SearchGate(SwapCase):
    def search(self, who, body):
        return self.api(who).post("/api/v1/patient/search/", body, format="json")

    def search_retrieve(self, who):
        w = self.world
        return self.api(who).post(
            "/api/v1/patient/search_retrieve/",
            {
                "phone_number": w.patient.phone_number,
                "year_of_birth": w.patient.year_of_birth,
                "partial_id": str(w.patient.external_id)[:5],
            },
            format="json",
        )

    def test_search_refused_to_roles_without_patient_access(self):
        """§9 test 12 (Q-K40): Owner, Lab Technician and Store Keeper cannot search; the front
        desk gets the projection; a Doctor is unchanged."""
        phone = {"phone_number": self.world.patient.phone_number}
        for role in NO_PATIENTS:
            with self.subTest(role=role):
                for response in (self.search(role, phone), self.search_retrieve(role)):
                    self.assertEqual(response.status_code, 403, response.content[:300])
                    self.assertNotIn(self.world.patient.name, response.content.decode())
        for role in FRONT_DESK:
            with self.subTest(role=role):
                found = self.search(role, phone)
                self.assertEqual(found.status_code, 200, found.content[:300])
                self.assertEqual(len(found.json()["results"]), 1)
                one = self.search_retrieve(role)
                self.assertEqual(one.status_code, 200, one.content[:300])
                self.assertIsNone(one.json()["blood_group"])
                self.assertNotIn(CANARY, one.content.decode())
        doctor = self.search_retrieve("Doctor")
        self.assertEqual(doctor.status_code, 200, doctor.content[:300])
        self.assertEqual(doctor.json()["blood_group"], "AB_negative")
        # A user of CARE's roles only who holds no patient right is CARE's to answer
        staff = self.world.user("acc-staff-only")
        self.assertEqual(branches.search_gate(staff), branches.CARE)


class MixedRoles(SwapCase):
    def test_mixed_roles_branch_is_still_projected(self):
        """§9 test 13 (Q-K35): a Receptionist who is also a Paediatrics Doctor reads the
        Paediatrics patient as a Doctor through CARE's path, and a patient of another
        department through our branch, projected, encounters included."""
        w = self.world
        both = w.user(
            "acc-rec-doc",
            ("Receptionist", w.root),
            ("Doctor", w.departments["Paediatrics"]),
        )
        client = self.api(both)
        own = client.get(f"/api/v1/patient/{w.patient.external_id}/")
        self.assertEqual(own.status_code, 200, own.content[:300])
        self.assertEqual(own.json()["blood_group"], "AB_negative")
        other, encounter = self.other_patient("Stores")
        response = client.get(f"/api/v1/patient/{other.external_id}/")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertIsNone(response.json()["blood_group"])
        self.assertNotIn(CANARY, response.content.decode())
        listed = client.get("/api/v1/encounter/", {"patient": str(other.external_id)})
        self.assertEqual(listed.status_code, 200, listed.content[:300])
        self.assertEqual(
            [r["id"] for r in listed.json()["results"]], [str(encounter.external_id)]
        )
        self.assertNotIn(CANARY, listed.content.decode())
        opened = client.get(f"/api/v1/encounter/{encounter.external_id}/")
        self.assertEqual(opened.status_code, 200, opened.content[:300])
        self.assertIsNone(opened.json()["discharge_summary_advice"])
        self.assertNotIn(CANARY, opened.content.decode())
        # ...while CARE's own path still answers in full
        mine = client.get(f"/api/v1/encounter/{w.encounter.external_id}/")
        self.assertEqual(mine.json()["discharge_summary_advice"], f"advice {CANARY}")


class BookingsAndTokens(SwapCase):
    def url(self, action):
        return f"/api/v1/patient/{self.world.patient.external_id}/{action}/"

    def test_receptionist_patient_appointments_are_projected(self):
        """ "After the attack" §5.2: restricted users name the facility, so CARE's booking and
        token checks run, and what they see is projected (no note, doctor or tags)."""
        w = self.world
        facility = {"facility": str(w.facility.external_id)}
        for action in ("get_appointments", "get_tokens"):
            with self.subTest(action=action):
                bare = self.api("Receptionist").get(self.url(action))
                self.assertEqual(bare.status_code, 403, bare.content[:300])
                named = self.api("Receptionist").get(self.url(action), facility)
                self.assertEqual(named.status_code, 200, named.content[:300])
                self.assertNotIn(CANARY, named.content.decode())
                for role in ("Cashier", "TPA Desk", "MRD", "Owner"):
                    answer = self.api(role).get(self.url(action), facility)
                    self.assertEqual(
                        answer.status_code, 403, (role, answer.content[:200])
                    )
        [booking] = (
            self.api("Receptionist")
            .get(self.url("get_appointments"), facility)
            .json()["results"]
        )
        self.assertEqual(booking["id"], str(w.booking.external_id))
        self.assertEqual(booking["note"], "")
        self.assertEqual(booking["tags"], [])
        self.assertEqual(booking["resource"], {})
        self.assertIsNone(booking["patient"]["blood_group"])
        # CARE's own roles keep CARE's answer
        doctor = self.api("Doctor").get(self.url("get_appointments"))
        self.assertEqual(doctor.status_code, 200, doctor.content[:300])
        self.assertIn(CANARY, json.dumps(doctor.json()))


class WritesDecideBeforeTheBody(SwapCase):
    """Reviews R1, W1, W4: CARE validates a patient body (a programme number's "not unique"
    answer included) before it authorizes. For restricted users, and on our branch, the write
    is decided first, so the answer never says whether someone holds a programme number; a
    registration keeps to the identifier list."""

    def setUp(self):
        # CARE's patient-create lock outlives a create that never committed (a TestCase)
        PatientCreateLock().release()
        w = self.world
        self.unique_config = PatientIdentifierConfig.objects.create(
            status="active",
            config={
                "use": "official",
                "system": "http://example.test/nikshay",
                "display": "Nikshay",
                "required": False,
                "unique": True,
                "regex": "",
            },
        )
        holder, _ = self.other_patient(phone_number="+919811122233")
        PatientIdentifier.objects.create(
            patient=holder, config=self.unique_config, value="NIK-777"
        )
        self.held = {"config": str(self.unique_config.external_id), "value": "NIK-777"}
        self.fresh = {"config": str(self.unique_config.external_id), "value": "NIK-000"}
        self.url = f"/api/v1/patient/{w.patient.external_id}/"

    def new_patient(self, **changes):
        return {
            "name": "Devika New",
            "gender": "female",
            "phone_number": "+919800022233",
            "emergency_phone_number": "+919800022233",
            "geo_organization": str(self.world.patient.geo_organization.external_id),
            "age": 41,
            "address": "4 Lake Road, Bhopal",
            "pincode": 462001,
            "identifiers": [],
            "tags": [],
            **changes,
        }

    def create(self, who, body, upsert=False):
        """CARE releases its patient-create lock when the request commits; run the hooks."""
        with self.captureOnCommitCallbacks(execute=True):
            if upsert:
                return self.api(who).post(
                    "/api/v1/patient/upsert/", {"datapoints": [body]}, format="json"
                )
            return self.api(who).post("/api/v1/patient/", body, format="json")

    def same_refusal(self, first, second):
        self.assertEqual(
            (first.status_code, first.content), (second.status_code, second.content)
        )
        for response in (first, second):
            self.assertNotIn("not unique", response.content.decode())
            self.assertNotIn("not valid", response.content.decode())

    def test_update_is_refused_before_the_body_is_read(self):
        """R1, W1: the billing and records desks open the patient but cannot edit it: 403 for
        an invalid body and for a programme number someone holds, as for a fresh one. Through
        CARE's upsert (which finds the patient by id) the Owner and the Store Keeper get the
        same answer for both numbers too."""
        w = self.world
        got = self.api("Receptionist").get(self.url).json()
        for role in ("Cashier", "TPA Desk", "MRD"):
            for method in ("put", "patch"):
                with self.subTest(role=role, method=method):
                    send = getattr(self.api(role), method)
                    bad = send(self.url, {"gender": "not-a-gender"}, format="json")
                    self.assertEqual(bad.status_code, 403, bad.content[:300])
                    held, fresh = (
                        send(
                            self.url,
                            registration_body(got, identifiers=[identifier]),
                            format="json",
                        )
                        for identifier in (self.held, self.fresh)
                    )
                    self.assertEqual(held.status_code, 403, held.content[:300])
                    self.same_refusal(held, fresh)
        for role in ("Owner", "Store Keeper", "Lab Technician", "Cashier"):
            with self.subTest(role=role, write="upsert"):
                held, fresh = (
                    self.api(role).post(
                        "/api/v1/patient/upsert/",
                        {
                            "datapoints": [
                                {
                                    "id": str(w.patient.external_id),
                                    "identifiers": [identifier],
                                }
                            ]
                        },
                        format="json",
                    )
                    for identifier in (self.held, self.fresh)
                )
                self.assertIn(held.status_code, (400, 403), held.content[:300])
                self.same_refusal(held, fresh)
        self.assertFalse(
            PatientIdentifier.objects.filter(
                patient=w.patient, config=self.unique_config
            ).exists()
        )

    def test_create_is_refused_before_the_body_is_read(self):
        """W1: a restricted user who cannot register patients gets 403 before CARE reads the
        body: for an invalid body, and the same answer whether the programme number in it is
        held or not, through POST and CARE's upsert."""
        for role in ("Cashier", "TPA Desk", "MRD", "Owner", "Store Keeper"):
            with self.subTest(role=role, body="invalid"):
                bad = self.create(role, self.new_patient(gender="not-a-gender"))
                self.assertEqual(bad.status_code, 403, bad.content[:300])
            for upsert in (False, True):
                with self.subTest(role=role, upsert=upsert):
                    held, fresh = (
                        self.create(
                            role, self.new_patient(identifiers=[identifier]), upsert
                        )
                        for identifier in (self.held, self.fresh)
                    )
                    self.assertIn(held.status_code, (400, 403), held.content[:300])
                    if not upsert:
                        self.assertEqual(held.status_code, 403, held.content[:300])
                    self.same_refusal(held, fresh)
        self.assertFalse(Patient.objects.filter(name="Devika New").exists())

    def test_registration_keeps_to_the_identifier_list(self):
        """W4: the Receptionist's registration drops a programme identifier (held or not)
        and extensions unread, keeps an allowed identifier, and cannot set a patient tag
        (CARE's can_apply_tag_config: 403, nothing made); the same through CARE's upsert."""
        w = self.world
        programme = {"config": str(w.programme_config.external_id), "value": "ART-NEW"}
        phone = {"config": str(w.phone_config.external_id), "value": "+919800022233"}
        for upsert, name in ((False, "Devika New"), (True, "Devika Upsert")):
            with self.subTest(upsert=upsert):
                response = self.create(
                    "Receptionist",
                    self.new_patient(
                        name=name,
                        identifiers=[programme, self.held, phone],
                        extensions={"core": {"note": "set at registration"}},
                    ),
                    upsert,
                )
                self.assertEqual(response.status_code, 200, response.content[:300])
                self.assertNotIn("not unique", response.content.decode())
                created = Patient.objects.get(name=name)
                self.assertEqual(
                    set(
                        PatientIdentifier.objects.filter(patient=created).values_list(
                            "config", flat=True
                        )
                    ),
                    {w.phone_config.pk},
                )
                self.assertNotIn("set at registration", json.dumps(created.extensions))
        tagged = self.create(
            "Receptionist",
            self.new_patient(
                name="Devika Tagged", tags=[str(w.tags["patient_instance"].external_id)]
            ),
        )
        self.assertEqual(tagged.status_code, 403, tagged.content[:300])
        self.assertFalse(Patient.objects.filter(name="Devika Tagged").exists())
