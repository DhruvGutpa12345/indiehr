"""A visit named in the query must be the path patient's (P1-T11b3 review fixes K1/M1;
ihr_access/visits.py). The role matrix sends the same requests as every role (its generated
``?encounter=encounter_b`` variant of every patient route, and post_reads.txt's analyse)."""

import datetime

from care.emr.models import Encounter
from care.emr.models.condition import Condition
from care.emr.models.encounter import EncounterOrganization
from care.emr.models.notes import NoteMessage, NoteThread
from django.utils import timezone

from ihr_access import testing, visits
from ihr_access.testing import build_world
from ihr_access.tests.base import SwapCase

ANALYSE = {"codes": [{"system": "http://loinc.org", "code": "20447-9"}]}


class VisitParam(SwapCase):
    @classmethod
    def setUpTestData(cls):
        cls.world = build_world(full=True)

    def patient_routes(self):
        """(method, path, body) of the first patient's clinical rows: every one answered the
        Radiologist 200 with its markers through ``?encounter=<another patient's visit>``."""
        o = self.world.objects
        p = f"/api/v1/patient/{o['patient'].external_id}"
        thread = o["thread"].external_id
        return (
            ("get", f"{p}/diagnosis/{o['condition'].external_id}/", None),
            ("get", f"{p}/symptom/", None),
            ("get", f"{p}/observation/{o['observation'].external_id}/", None),
            ("get", f"{p}/observation/{o['lab_observation'].external_id}/", None),
            ("get", f"{p}/consent/{o['consent'].external_id}/", None),
            (
                "get",
                f"{p}/medication/request/{o['medication_request'].external_id}/",
                None,
            ),
            (
                "get",
                f"{p}/medication/statement/{o['medication_statement'].external_id}/",
                None,
            ),
            (
                "get",
                f"{p}/medication/administration/"
                f"{o['medication_administration'].external_id}/",
                None,
            ),
            ("get", f"{p}/thread/{thread}/", None),
            ("get", f"{p}/thread/{thread}/note/", None),
            ("get", f"{p}/thread/{thread}/note/{o['note'].external_id}/", None),
            ("post", f"{p}/observation/analyse/", ANALYSE),
        )

    def call(self, who, method, path, body, encounter):
        query = f"?encounter={encounter}" if encounter else ""
        return getattr(self.api(who), method)(f"{path}{query}", body, format="json")

    def test_another_patients_visit_opens_nothing(self):
        """The Radiologist holds Radiology on the second patient's visit only: naming that visit
        on the first patient's routes is refused, as is a visit that does not exist or a
        malformed id (403, no marker); without it CARE already refuses."""
        other = self.world.objects["encounter_b"].external_id
        for method, path, body in self.patient_routes():
            for encounter in (None, other, "00000000-0000-4000-8000-000000000000", "x"):
                with self.subTest(path=path, method=method, encounter=encounter):
                    answer = self.call("Radiologist", method, path, body, encounter)
                    self.assertEqual(answer.status_code, 403, answer.content[:300])
                    self.assertNotIn(testing.CANARY, answer.content.decode())

    def test_restricted_roles_are_refused_the_same_way(self):
        other = self.world.objects["encounter_b"].external_id
        for who in ("Receptionist", "Lab Technician", "MRD"):
            for method, path, body in self.patient_routes():
                with self.subTest(who=who, path=path):
                    answer = self.call(who, method, path, body, other)
                    self.assertIn(answer.status_code, (403, 404), answer.content[:300])
                    self.assertNotIn(testing.CANARY, answer.content.decode())

    def test_the_patients_own_open_radiology_visit_reads_as_before(self):
        """The second patient's chart (Radiology on the open visit, Q-K44 A): naming its own
        visit changes nothing for the Radiologist."""
        o = self.world.objects
        path = (
            f"/api/v1/patient/{o['patient_b'].external_id}/diagnosis/"
            f"{o['condition_b'].external_id}/"
        )
        for encounter in (None, o["encounter_b"].external_id):
            with self.subTest(encounter=encounter):
                answer = self.call("Radiologist", "get", path, None, encounter)
                self.assertEqual(answer.status_code, 200, answer.content[:300])
                self.assertIn(testing.marker("rad-condition"), answer.content.decode())

    def finished_radiology_visit(self):
        """A finished visit of the first patient filed under Radiology: the Radiologist holds
        no chart of that patient (CARE's can_view_clinical_data counts open visits only)."""
        w = self.world
        now = timezone.now()
        done = Encounter.objects.create(
            patient=w.patient,
            facility=w.facility,
            status="completed",
            encounter_class="amb",
            priority="routine",
            period={
                "start": (now - datetime.timedelta(days=9)).isoformat(),
                "end": (now - datetime.timedelta(days=8)).isoformat(),
            },
            hospitalization={},
        )
        EncounterOrganization.objects.create(
            encounter=done, organization=w.departments["Radiology"]
        )
        done.save()
        return done

    def test_a_finished_radiology_visit_opens_its_own_rows_only(self):
        """Without the chart (no open Radiology visit), a finished Radiology visit opens its
        own rows: the list (CARE narrows it to the visit) and a row of that visit; never a row
        of another visit by id, a note thread of another visit, or ``analyse`` (not narrowed by
        the visit)."""
        w = self.world
        now = timezone.now()
        done = self.finished_radiology_visit()
        doctor = w.users["Doctor"]
        own = Condition.objects.create(
            patient=w.patient,
            encounter=done,
            category="problem_list_item",
            clinical_status="active",
            verification_status="confirmed",
            code={"system": "http://snomed.info/sct", "code": "386661006"},
            recorded_date=now,
            note="the finished Radiology visit's own row",
            created_by=doctor,
            updated_by=doctor,
        )
        own_thread = NoteThread.objects.create(
            patient=w.patient,
            encounter=done,
            title="Imaging note",
            created_by=doctor,
            updated_by=doctor,
        )
        NoteMessage.objects.create(
            thread=own_thread, message="seen", created_by=doctor, updated_by=doctor
        )
        p = f"/api/v1/patient/{w.patient.external_id}"
        allowed = (
            ("get", f"{p}/diagnosis/", None),
            ("get", f"{p}/diagnosis/{own.external_id}/", None),
            ("get", f"{p}/thread/", None),
            ("get", f"{p}/thread/{own_thread.external_id}/note/", None),
        )
        for method, path, body in allowed:
            with self.subTest(path=path):
                answer = self.call("Radiologist", method, path, body, done.external_id)
                self.assertEqual(answer.status_code, 200, answer.content[:300])
                self.assertNotIn(testing.CANARY, answer.content.decode())
        listed = self.call(
            "Radiologist", "get", f"{p}/diagnosis/", None, done.external_id
        )
        self.assertEqual(
            [r["id"] for r in listed.json()["results"]], [str(own.external_id)]
        )
        for method, path, body in self.patient_routes():
            if path.endswith("/symptom/"):
                continue  # a list: narrowed to the visit (above)
            with self.subTest(path=path, method=method):
                answer = self.call("Radiologist", method, path, body, done.external_id)
                self.assertEqual(answer.status_code, 403, answer.content[:300])
                self.assertNotIn(testing.CANARY, answer.content.decode())

    def test_cares_roles_are_unchanged(self):
        """The fix is ours only (Q-K40's list): the Paediatrics Doctor reads the first
        patient's rows with or without a visit named, CARE's own answer."""
        other = self.world.objects["encounter_b"].external_id
        (path,) = (
            p for m, p, _b in self.patient_routes() if "/consent/" in p and m == "get"
        )
        for encounter in (None, other):
            with self.subTest(encounter=encounter):
                answer = self.call("Doctor", "get", path, None, encounter)
                self.assertEqual(answer.status_code, 200, answer.content[:300])

    def test_the_check_holds(self):
        self.assertEqual(visits.problems(), [])
        routes = visits.patient_routes()
        self.assertGreater(len(routes), 20)

    def test_a_create_naming_a_visit_is_refused(self):
        """Review NEW-5 (a known limit, fail closed): an objectless write that names
        ``?encounter=`` is refused for a closed user without the chart, even where CARE's
        create authorizes on the body's visit alone (thread, note, report creates), so the
        check may over-refuse such a call. No web-app call sends one (the note, questionnaire
        and report forms put the visit in the body). Without the query CARE decides as
        before."""
        done = self.finished_radiology_visit()
        path = f"/api/v1/patient/{self.world.patient.external_id}/thread/"
        body = {"title": "Imaging note", "encounter": str(done.external_id)}
        named = self.call("Radiologist", "post", path, body, done.external_id)
        self.assertEqual(named.status_code, 403, named.content[:300])
        self.assertIn(visits.REFUSED, named.content.decode())
        plain = self.call("Radiologist", "post", path, body, None)
        self.assertNotIn(visits.REFUSED, plain.content.decode())
