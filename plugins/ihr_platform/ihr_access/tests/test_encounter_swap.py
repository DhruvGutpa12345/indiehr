"""The encounter swap (P1-T11b2; design-b.md §5.3 item 2, "After the attack" §6, §10 tests 7-9,
§9 test 9 (encounter create)). The status view for the front desk, with the filter allow-list
(Q-K37); CARE's own roles, and a clinician who also works at the front desk, keep CARE's
answers."""

import datetime
import hashlib
import inspect
import json
from pathlib import Path
from unittest import mock

from care.emr.models import Encounter
from care.emr.models.encounter import EncounterOrganization
from django.db import connection
from django.utils import timezone
from ihr_identity import testing as identity

from ihr_access import views
from ihr_access.testing import CANARY
from ihr_access.tests.base import SwapCase

STATUS_VIEW = ("Receptionist", "Cashier", "TPA Desk", "MRD")
NO_PATIENTS = ("Owner", "Lab Technician", "Store Keeper")
# Queries our swaps may add for a user of CARE's roles only (review C3), measured warm against
# CARE's own class: one, the roles that grant our slugs (branches.may_hold; read once per
# request, the user's held roles are shared with the serializer guard). Neither our status
# check nor CARE's write check runs a second time for them
ENCOUNTER_HISTORY_BUDGET = 1
PATIENT_SAVE_BUDGET = 1


def ids(response):
    return [row["id"] for row in response.json()["results"]]


class StatusList(SwapCase):
    def setUp(self):
        self.other, self.other_encounter = self.other_patient("Stores")

    def scopes(self):
        w = self.world
        return (
            {"facility": str(w.facility.external_id)},
            {"patient": str(w.patient.external_id)},
        )

    def test_the_status_view_by_patient_and_by_facility(self):
        w = self.world
        for role in STATUS_VIEW:
            with self.subTest(role=role):
                by_patient = self.api(role).get(
                    "/api/v1/encounter/", {"patient": str(w.patient.external_id)}
                )
                self.assertEqual(by_patient.status_code, 200, by_patient.content[:300])
                self.assertEqual(ids(by_patient), [str(w.encounter.external_id)])
                by_facility = self.api(role).get(
                    "/api/v1/encounter/", {"facility": str(w.facility.external_id)}
                )
                self.assertEqual(
                    by_facility.status_code, 200, by_facility.content[:300]
                )
                self.assertEqual(
                    set(ids(by_facility)),
                    {
                        str(w.encounter.external_id),
                        str(self.other_encounter.external_id),
                    },
                )
                for response in (by_patient, by_facility):
                    self.assertNotIn(CANARY, response.content.decode())
                    for row in response.json()["results"]:
                        self.assertIsNone(row["discharge_summary_advice"])
                        self.assertIsNone(row["patient"]["blood_group"])
                        self.assertIsNone(row["current_location"])
        for role in NO_PATIENTS:
            with self.subTest(role=role):
                by_patient = self.api(role).get(
                    "/api/v1/encounter/", {"patient": str(w.patient.external_id)}
                )
                self.assertEqual(by_patient.status_code, 403, by_patient.content[:300])
                by_facility = self.api(role).get(
                    "/api/v1/encounter/", {"facility": str(w.facility.external_id)}
                )
                self.assertEqual(by_facility.status_code, 200)
                self.assertEqual(ids(by_facility), [])

    def test_restricted_encounter_filters_refused(self):
        """§10 test 7: the filters that find a bed's, a department's, a doctor's or a tag's
        patients answer 400 on both branches, with real values; the allowed ones return the
        rows of the projection."""
        w = self.world
        refused = {
            "location": str(w.bed.external_id),
            "organization": str(w.departments["Paediatrics"].external_id),
            "care_team_user": w.users["Doctor"].username,
            "tags": str(w.tags["encounter"].external_id),
            "tags_behavior": "any",
            "something_new": "1",
        }
        allowed = {
            "status": "in_progress",
            "live": "false",
            "encounter_class": "imp",
            "priority": "routine",
            "name": w.patient.name[:8],
            "phone_number": "98765432",
            "created_date_after": "2000-01-01T00:00:00Z",
            "patient_filter": str(w.patient.external_id),
            "ordering": "-created_date",
            "limit": "5",
            "offset": "0",
        }
        for role in STATUS_VIEW:
            for scope in self.scopes():
                for name, value in refused.items():
                    with self.subTest(role=role, scope=next(iter(scope)), filter=name):
                        response = self.api(role).get(
                            "/api/v1/encounter/", {**scope, name: value}
                        )
                        self.assertEqual(
                            response.status_code, 400, response.content[:300]
                        )
                        self.assertNotIn(w.patient.name, response.content.decode())
                with self.subTest(role=role, scope=next(iter(scope)), filter="allowed"):
                    response = self.api(role).get(
                        "/api/v1/encounter/", {**scope, **allowed}
                    )
                    self.assertEqual(response.status_code, 200, response.content[:300])
                    self.assertEqual(ids(response), [str(w.encounter.external_id)])
                    self.assertNotIn(CANARY, response.content.decode())
        # CARE's path keeps CARE's full filters
        doctor = self.api("Doctor").get(
            "/api/v1/encounter/",
            {
                "facility": str(w.facility.external_id),
                "location": str(w.bed.external_id),
            },
        )
        self.assertEqual(doctor.status_code, 200, doctor.content[:300])
        self.assertEqual(ids(doctor), [str(w.encounter.external_id)])

    def test_mixed_roles_on_the_facility_list_get_cares_rows_only(self):
        """§10 test 8: a Receptionist who is also a Paediatrics Doctor gets exactly CARE's
        Paediatrics rows, unprojected, and nothing of ours added; a Receptionist only gets the
        whole hospital, projected."""
        w = self.world
        both = w.user(
            "acc-rec-doc",
            ("Receptionist", w.root),
            ("Doctor", w.departments["Paediatrics"]),
        )
        scope = {"facility": str(w.facility.external_id)}
        response = self.api(both).get("/api/v1/encounter/", scope)
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(ids(response), [str(w.encounter.external_id)])
        [row] = response.json()["results"]
        self.assertEqual(row["discharge_summary_advice"], f"advice {CANARY}")
        receptionist = self.api("Receptionist").get("/api/v1/encounter/", scope)
        self.assertEqual(
            set(ids(receptionist)),
            {str(w.encounter.external_id), str(self.other_encounter.external_id)},
        )
        self.assertNotIn(CANARY, receptionist.content.decode())

    def test_actions_are_classified(self):
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
            if hasattr(views.IhrEncounterViewSet, m)
        }
        found |= {a.__name__ for a in views.IhrEncounterViewSet.get_extra_actions()}
        self.assertEqual(found, set(views.actions("encounter")))

    def test_a_front_desk_placed_in_one_unit_sees_that_unit_only(self):
        """Review R3: a Receptionist placed in Radiology (not the root) gets, on every path of
        our branch, only the encounters filed under Radiology: the ``?patient`` list, the
        ``?facility`` list and retrieve, as CARE's own encounter check counts units."""
        w = self.world
        unit = w.user("acc-unit-rec", ("Receptionist", w.departments["Radiology"]))
        patient, radiology = self.other_patient("Radiology")
        paediatrics = Encounter.objects.create(
            patient=patient,
            facility=w.facility,
            status="in_progress",
            encounter_class="amb",
            priority="routine",
            period={"start": timezone.now().isoformat()},
        )
        EncounterOrganization.objects.create(
            encounter=paediatrics, organization=w.departments["Paediatrics"]
        )
        paediatrics.save()
        client = self.api(unit)
        by_patient = client.get(
            "/api/v1/encounter/", {"patient": str(patient.external_id)}
        )
        self.assertEqual(by_patient.status_code, 200, by_patient.content[:300])
        self.assertEqual(ids(by_patient), [str(radiology.external_id)])
        by_facility = client.get(
            "/api/v1/encounter/", {"facility": str(w.facility.external_id)}
        )
        self.assertEqual(ids(by_facility), [str(radiology.external_id)])
        opened = client.get(f"/api/v1/encounter/{radiology.external_id}/")
        self.assertEqual(opened.status_code, 200, opened.content[:300])
        for other in (paediatrics, w.encounter):
            with self.subTest(encounter=str(other.external_id)):
                refused = client.get(f"/api/v1/encounter/{other.external_id}/")
                self.assertEqual(refused.status_code, 403, refused.content[:300])
                self.assertNotIn(CANARY, refused.content.decode())
        # At the root (the seed and the admin guide) the whole facility, as before
        root = self.api("Receptionist").get(
            "/api/v1/encounter/", {"patient": str(patient.external_id)}
        )
        self.assertEqual(
            set(ids(root)), {str(radiology.external_id), str(paediatrics.external_id)}
        )

    def test_the_create_list_classifies_cares_fields(self):
        """Review C2: every field of CARE's encounter create spec is classified in
        fields/encounter_create.txt (E024 says so at start-up too)."""
        self.assertEqual(views.create_problems(), [])
        rules = views.create_rules()
        self.assertEqual(rules["discharge_summary_advice"], "clinical")
        self.assertEqual(rules["extensions"], "hidden")

    def test_the_filter_list_classifies_cares_filters(self):
        self.assertEqual(views.filter_problems(), [])
        for name in ("location", "organization", "care_team_user", "tags"):
            self.assertNotIn(name, views.allowed_parameters())


class OneEncounter(SwapCase):
    def url(self, action=""):
        e = self.world.encounter.external_id
        return f"/api/v1/encounter/{e}/{action + '/' if action else ''}"

    def test_encounter_organizations_refused(self):
        """ "After the attack" §6.1: our branch widens retrieve only; the departments of an
        encounter (which reveal the specialty) keep CARE's check."""
        for role in STATUS_VIEW:
            with self.subTest(role=role):
                opened = self.api(role).get(self.url())
                self.assertEqual(opened.status_code, 200, opened.content[:300])
                self.assertEqual(opened.json()["organizations"], [])
                self.assertNotIn(CANARY, opened.content.decode())
                departments = self.api(role).get(self.url("organizations"))
                self.assertEqual(
                    departments.status_code, 403, departments.content[:300]
                )
                self.assertNotIn("Paediatrics", departments.content.decode())
        for role in NO_PATIENTS:
            with self.subTest(role=role):
                self.assertEqual(self.api(role).get(self.url()).status_code, 403)
        doctor = self.api("Doctor").get(self.url("organizations"))
        self.assertEqual(doctor.status_code, 200, doctor.content[:300])
        self.assertIn("Paediatrics", doctor.content.decode())

    def test_restricted_users_cannot_change_an_encounter(self):
        for role in STATUS_VIEW:
            with self.subTest(role=role):
                for action, body in (
                    ("restart", {}),
                    (
                        "organizations_add",
                        {"organization": str(self.world.root.external_id)},
                    ),
                    ("set_tags", {"tags": []}),
                ):
                    response = self.api(role).post(
                        self.url(action), body, format="json"
                    )
                    self.assertEqual(
                        response.status_code, 403, (action, response.content[:200])
                    )


class CreatingAVisit(SwapCase):
    def create(self, client, body):
        """CARE releases its per-facility create lock when the request commits; a TestCase
        never commits, so run the on-commit hooks here."""
        with self.captureOnCommitCallbacks(execute=True):
            return client.post("/api/v1/encounter/", body, format="json")

    def body(self, patient, **extra):
        return {
            "patient": str(patient.external_id),
            "facility": str(self.world.facility.external_id),
            "status": "planned",
            "encounter_class": "amb",
            "priority": "routine",
            "period": {"start": timezone.now().isoformat()},
            "organizations": [str(self.world.departments["Paediatrics"].external_id)],
            "tags": [],
            **extra,
        }

    def test_receptionist_cannot_write_discharge_advice(self):
        """§9 test 9: a restricted user cannot set clinical fields at create (400, nothing
        made); without them the visit is made and the answer is projected."""
        patient, _ = self.other_patient()
        clinical = (
            {"discharge_summary_advice": "rest for a week"},
            {"hospitalization": {"discharge_disposition": "home"}},
            {"hospitalization": {"diet_preference": "vegetarian"}},
        )
        for extra in clinical:
            with self.subTest(extra=extra):
                response = self.create(
                    self.api("Receptionist"), self.body(patient, **extra)
                )
                self.assertEqual(response.status_code, 400, response.content[:300])
                self.assertFalse(Encounter.objects.filter(patient=patient).exists())
        response = self.create(
            self.api("Receptionist"),
            self.body(patient, hospitalization={"admit_source": "outp"}),
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json()["organizations"], [])
        self.assertEqual(Encounter.objects.filter(patient=patient).count(), 1)
        # A Doctor writes them through CARE's path
        response = self.create(
            self.api("Doctor"),
            self.body(patient, discharge_summary_advice="rest for a week"),
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json()["discharge_summary_advice"], "rest for a week")
        for role in ("Cashier", "TPA Desk", "MRD", *NO_PATIENTS):
            with self.subTest(role=role):
                response = self.create(self.api(role), self.body(patient))
                self.assertEqual(response.status_code, 403, response.content[:300])

    def test_unclassified_and_hidden_fields_at_create(self):
        """Review C2: a field CARE adds is refused to restricted users until it is classified
        (fail closed); a hidden field (extensions) is dropped unread, the visit still made."""
        patient, _ = self.other_patient()
        with mock.patch.object(
            views,
            "create_rules",
            return_value={**views.create_rules(), "priority": "new"},
        ):
            refused = self.create(self.api("Receptionist"), self.body(patient))
        self.assertEqual(refused.status_code, 400, refused.content[:300])
        self.assertIn("priority", refused.content.decode())
        self.assertFalse(Encounter.objects.filter(patient=patient).exists())
        response = self.create(
            self.api("Receptionist"),
            self.body(patient, extensions={"core": {"note": "front desk"}}),
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        made = Encounter.objects.get(patient=patient)
        self.assertNotIn("front desk", json.dumps(made.extensions))

    def test_receptionist_starts_a_visit_with_a_department(self):
        """§10 test 9: the web app's calls, signed in as the Receptionist: their organisations,
        the department list, then a visit filed under Radiology; the Radiologist can then read
        the patient, and the department link is in the audit trail."""
        w = self.world
        patient, _ = self.other_patient()
        facility = w.facility.external_id
        with identity.fake_keycloak():
            client = self.signed_in("Receptionist")
            mine = client.get(f"/api/v1/facility/{facility}/organizations/mine/")
            self.assertEqual(mine.status_code, 200, mine.content[:300])
            self.assertEqual(
                [o["id"] for o in mine.json()["results"]], [str(w.root.external_id)]
            )
            departments = client.get(
                f"/api/v1/facility/{facility}/organizations/", {"limit": 50}
            )
            self.assertEqual(departments.status_code, 200, departments.content[:300])
            names = {o["name"] for o in departments.json()["results"]}
            self.assertLessEqual({"Paediatrics", "Radiology"}, names)
            radiology = w.departments["Radiology"]
            response = self.create(
                client,
                {**self.body(patient), "organizations": [str(radiology.external_id)]},
            )
            self.assertEqual(response.status_code, 200, response.content[:300])
            self.assertEqual(response.json()["organizations"], [])
            self.assertIsNone(response.json()["patient"]["blood_group"])
            encounter = Encounter.objects.get(external_id=response.json()["id"])
            link = EncounterOrganization.objects.get(encounter=encounter)
            self.assertEqual(link.organization, radiology)
            radiologist = self.signed_in("Radiologist")
            opened = radiologist.get(f"/api/v1/patient/{patient.external_id}/")
            self.assertEqual(opened.status_code, 200, opened.content[:300])
            self.assertEqual(opened.json()["blood_group"], "O_negative")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM ihr_audit_entry "
                "WHERE tbl = 'emr_encounterorganization' AND op = 'I' AND row_pk = %s",
                [str(link.pk)],
            )
            self.assertEqual(cursor.fetchone()[0], 1)
        # Without Radiology on the visit the Radiologist sees no chart (Q-K44)
        another, _ = self.other_patient(phone_number="+919812300000")
        self.assertEqual(
            self.api("Radiologist")
            .get(f"/api/v1/patient/{another.external_id}/")
            .status_code,
            404,
        )
        self.assertLess(
            abs(encounter.created_date - timezone.now()), datetime.timedelta(minutes=5)
        )


class CareCopies(SwapCase):
    """Review C2: CARE methods our swaps copy are pinned by digest (ihr_access/care_copies.txt),
    and CARE's own classes still inherit them, so an upstream change cannot be shadowed by our
    copy unnoticed."""

    def test_copied_care_methods_are_unchanged(self):
        from care.emr.api.viewsets.encounter import EncounterViewSet
        from care.emr.api.viewsets.patient import PatientViewSet
        from django.utils.module_loading import import_string

        self.assertNotIn("retrieve", EncounterViewSet.__dict__)
        self.assertNotIn("handle_update", PatientViewSet.__dict__)
        self.assertIn("get_queryset", EncounterViewSet.__dict__)
        pinned = {}
        path = Path(views.__file__).with_name("care_copies.txt")
        for raw in path.read_text().splitlines():
            line = raw.split("#", 1)[0].split()
            if line:
                pinned[line[0]] = line[1]
        self.assertEqual(len(pinned), 5)
        for name, digest in pinned.items():
            with self.subTest(method=name):
                module, cls, method = name.rsplit(".", 2)
                source = inspect.getsource(
                    getattr(import_string(f"{module}.{cls}"), method)
                )
                self.assertEqual(
                    hashlib.sha256(source.encode()).hexdigest(),
                    digest,
                    f"CARE changed {name}: re-read it, update our copy, pin the new digest",
                )

    def test_our_rows_are_cares_rows(self):
        """Review C2: the status list's rows are CARE's encounter rows (joins, order, base
        filters) before CARE's list checks, compared as SQL with CARE's own queryset."""
        from care.emr.api.viewsets.encounter import EncounterViewSet
        from rest_framework.test import APIRequestFactory

        request = APIRequestFactory().get("/api/v1/encounter/")
        request.user = self.world.users["Receptionist"]
        care = EncounterViewSet()
        care.request, care.action, care.kwargs = request, "retrieve", {}
        ours = views.IhrEncounterViewSet()
        ours.request, ours.action, ours.kwargs = request, "list", {}
        # retrieve: CARE's get_queryset returns its rows without the list checks
        self.assertEqual(str(ours._rows().query), str(care.get_queryset().query))


class QueryBudget(SwapCase):
    """Review C3: what the swaps add to the queries of CARE's own roles on common requests,
    measured against CARE's own class for the same request."""

    def run_view(self, view_class, actions, request):
        from django.test.utils import CaptureQueriesContext

        from ihr_access import state

        view = view_class.as_view(actions)
        state.end()
        with CaptureQueriesContext(connection) as queries:
            response = view(request, **getattr(request, "ihr_kwargs", {}))
        state.end()
        # django-tenants sets the search path before each statement: not a query of ours
        return response, sum(
            not q["sql"].startswith("SET search_path") for q in queries.captured_queries
        )

    def compare(self, care_class, ours, actions, make_request):
        """Queries ours makes beyond CARE's, both measured warm (a first pass of each fills
        the process-wide caches, e.g. CARE's identifier configs)."""
        counts = {}
        for _pass in range(2):
            for view_class in (care_class, ours):
                response, counts[view_class] = self.run_view(
                    view_class, actions, make_request()
                )
                self.assertEqual(response.status_code, 200, response.data)
        return counts[ours] - counts[care_class]

    def test_a_doctors_encounter_history_and_patient_save(self):
        from care.emr.api.viewsets.encounter import EncounterViewSet
        from care.emr.api.viewsets.patient import PatientViewSet
        from rest_framework.test import APIRequestFactory, force_authenticate

        from ihr_access.tests.base import registration_body

        w = self.world
        doctor = w.users["Doctor"]
        factory = APIRequestFactory()

        def history():
            request = factory.get(
                "/api/v1/encounter/", {"patient": str(w.patient.external_id)}
            )
            force_authenticate(request, user=doctor)
            return request

        added = self.compare(
            EncounterViewSet, views.IhrEncounterViewSet, {"get": "list"}, history
        )
        self.assertLessEqual(added, ENCOUNTER_HISTORY_BUDGET, "encounter ?patient list")

        got = self.api("Doctor").get(f"/api/v1/patient/{w.patient.external_id}/").json()
        body = registration_body(got, address="5 Budget Lane")

        def save():
            request = factory.put(
                f"/api/v1/patient/{w.patient.external_id}/", body, format="json"
            )
            force_authenticate(request, user=doctor)
            request.ihr_kwargs = {"external_id": str(w.patient.external_id)}
            return request

        added = self.compare(
            PatientViewSet, views.IhrPatientViewSet, {"put": "update"}, save
        )
        self.assertLessEqual(added, PATIENT_SAVE_BUDGET, "patient save")
