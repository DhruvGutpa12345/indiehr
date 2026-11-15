"""A PatientUser row follows the person's current job (P1-T11b1; "After the attack" §3, §10
test 3)."""

import datetime
import json
from io import StringIO
from unittest import mock

from care.emr.models import Patient, PatientUser
from care.emr.models.organization import (
    FacilityOrganizationUser,
    Organization,
    OrganizationUser,
)
from care.emr.models.patient import PatientIdentifier
from care.security.authorization.base import AuthorizationController
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.utils import timezone
from ihr_roles import registry
from model_bakery import baker
from rest_framework.test import APIClient

from ihr_access.models import RecordsGrant
from ihr_access.testing import (
    PROGRAMME_SYSTEM,
    build_world,
    move_to,
    nurse_on_care_team,
)

HOST = "testserver"
CLINICAL_ROUTES = ("symptom", "diagnosis", "allergy_intolerance", "medication/request")
PATIENT_CHECKS = (
    "can_view_clinical_data",
    "can_view_patient_obj",
    "can_write_patient_obj",
    "can_view_patient_questionnaire_responses",
    "can_submit_questionnaire_patient_obj",
)


class PatientUserRule(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.world = build_world()

    def api(self, user):
        client = APIClient(HTTP_HOST=HOST)
        client.force_authenticate(user)
        return client

    def clinical_answers(self, user):
        client = self.api(user)
        pid = self.world.patient.external_id
        return {
            route: client.get(f"/api/v1/patient/{pid}/{route}/").status_code
            for route in CLINICAL_ROUTES
        }

    def checks(self, user, patient=None):
        patient = patient or self.world.patient
        return {
            name: AuthorizationController.call(name, user, patient)
            for name in PATIENT_CHECKS
        }

    def test_moved_nurse_loses_patient_user_read(self):
        w = self.world
        nurse = nurse_on_care_team(w)
        self.assertTrue(all(self.checks(nurse).values()), self.checks(nurse))
        self.assertEqual(set(self.clinical_answers(nurse).values()), {200})
        move_to(w, nurse, "Receptionist")
        self.assertEqual(self.checks(nurse), dict.fromkeys(PATIENT_CHECKS, False))
        self.assertEqual(set(self.clinical_answers(nurse).values()), {403})
        # A Receptionist who still is a Nurse somewhere keeps CARE's care-team read (Q-K35)
        move_to(w, nurse, "Nurse", w.departments["Stores"])
        self.assertTrue(self.checks(nurse)["can_view_clinical_data"])

    def front_desk_member(self, w):
        """A Nurse on the care team who moves to the front desk and also is a "Member" of a
        role organisation: a non-clinical role that is not one of ours (review S1)."""
        nurse = nurse_on_care_team(w)
        move_to(w, nurse, "Receptionist")
        team = Organization.objects.create(name="Front office team", org_type="role")
        OrganizationUser.objects.create(
            organization=team, user=nurse, role=w.role("Member")
        )
        return nurse

    def listed(self, user):
        response = self.api(user).get("/api/v1/patient/", {"limit": 50})
        self.assertEqual(response.status_code, 200, response.content[:200])
        return {r["id"] for r in response.json()["results"]}

    def test_a_moved_nurse_with_another_non_clinical_role_loses_the_row_too(self):
        w = self.world
        nurse = self.front_desk_member(w)
        Patient.objects.filter(pk=w.patient.pk).update(users_cache=[nurse.id])
        self.assertEqual(self.checks(nurse), dict.fromkeys(PATIENT_CHECKS, False))
        self.assertEqual(set(self.clinical_answers(nurse).values()), {403})
        self.assertNotIn(str(w.patient.external_id), self.listed(nurse))
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("ihr_patient_user_check", stdout=out)
        row = PatientUser.objects.get(user=nurse)
        self.assertIn(f"stale patient_user={row.pk} user={nurse.pk}", out.getvalue())
        # and no new care-team row can be added for them
        other = baker.make(Patient, name="Another Patient")
        with self.assertRaises(IntegrityError) as refused, transaction.atomic():
            PatientUser.objects.create(user=nurse, patient=other, role=w.role("Nurse"))
        self.assertIn("restricted roles", str(refused.exception))

    def test_hidden_role_counts_only_under_a_live_grant(self):
        w = self.world
        mrd = w.users["MRD"]
        self.assertFalse(self.checks(mrd)["can_view_clinical_data"])
        grant = RecordsGrant.objects.create(
            patient=w.patient,
            user=mrd,
            reason="patient_request",
            expires_at=timezone.now() + datetime.timedelta(minutes=30),
        )
        PatientUser.objects.create(
            user=mrd, patient=w.patient, role=w.role(registry.HIDDEN)
        )
        self.assertTrue(self.checks(mrd)["can_view_clinical_data"])
        self.assertFalse(self.checks(mrd)["can_write_patient_obj"])  # read only
        self.assertEqual(set(self.clinical_answers(mrd).values()), {200})
        other = baker.make(Patient, name="Someone Else", users_cache=[mrd.id])
        self.assertFalse(self.checks(mrd, other)["can_view_clinical_data"])
        # Expired, with the clean-up jobs not run: the row is still there, and counts no more
        RecordsGrant.objects.filter(pk=grant.pk).update(
            expires_at=timezone.now() - datetime.timedelta(seconds=1)
        )
        self.assertTrue(
            PatientUser.objects.filter(user=mrd, patient=w.patient).exists()
        )
        self.assertFalse(self.checks(mrd)["can_view_clinical_data"])
        self.assertEqual(set(self.clinical_answers(mrd).values()), {403})

    def test_the_hidden_role_never_counts_without_a_grant_for_care_roles_either(self):
        """A hidden-role row that somehow exists (made before the trigger, or by the owner)
        gives a Doctor nothing on a patient their department does not see."""
        w = self.world
        doctor = w.user("acc-other-doc", ("Doctor", w.departments["Stores"]))
        grant = RecordsGrant.objects.create(
            patient=w.patient,
            user=doctor,
            reason="legal",
            expires_at=timezone.now() + datetime.timedelta(minutes=30),
        )
        PatientUser.objects.create(
            user=doctor, patient=w.patient, role=w.role(registry.HIDDEN)
        )
        self.assertTrue(self.checks(doctor)["can_view_clinical_data"])
        grant.ended_at = timezone.now()
        grant.save()
        self.assertFalse(self.checks(doctor)["can_view_clinical_data"])

    def test_patient_list_of_restricted_users(self):
        w = self.world
        receptionist, mrd, doctor = (
            w.users["Receptionist"],
            w.users["MRD"],
            w.users["Doctor"],
        )
        other = baker.make(Patient, name="Other Patient")
        # users_cache can be stale; CARE's list reads it directly
        Patient.objects.filter(pk__in=[w.patient.pk, other.pk]).update(
            users_cache=[receptionist.id, mrd.id, doctor.id]
        )

        def listed(user):
            response = self.api(user).get("/api/v1/patient/", {"limit": 50})
            self.assertEqual(response.status_code, 200, response.content[:200])
            return {r["id"] for r in response.json()["results"]}

        self.assertEqual(listed(receptionist), set())
        self.assertEqual(listed(mrd), set())
        RecordsGrant.objects.create(
            patient=w.patient,
            user=mrd,
            reason="insurer",
            reference="TPA-9",
            expires_at=timezone.now() + datetime.timedelta(minutes=30),
        )
        self.assertEqual(listed(mrd), {str(w.patient.external_id)})
        # CARE's own roles keep CARE's list
        self.assertEqual(
            listed(doctor), {str(w.patient.external_id), str(other.external_id)}
        )

    def test_check_command_keeps_records_access_under_a_live_grant(self):
        """A records-access row under a live grant is legitimate (review C5): never listed as
        stale; once its grant ended it is listed apart, for the end routine (P1-T11b4)."""
        w = self.world
        mrd = w.users["MRD"]
        grant = RecordsGrant.objects.create(
            patient=w.patient,
            user=mrd,
            reason="legal",
            expires_at=timezone.now() + datetime.timedelta(minutes=30),
        )
        row = PatientUser.objects.create(
            user=mrd, patient=w.patient, role=w.role(registry.HIDDEN)
        )
        out = StringIO()
        try:
            call_command("ihr_patient_user_check", stdout=out)  # exit 0
        except CommandError as failed:
            self.fail(f"a live grant's row was reported: {failed}; {out.getvalue()}")
        self.assertNotIn(f"patient_user={row.pk}", out.getvalue())
        self.assertIn("no PatientUser row", out.getvalue())
        RecordsGrant.objects.filter(pk=grant.pk).update(ended_at=timezone.now())
        out = StringIO()
        call_command("ihr_patient_user_check", stdout=out)  # still exit 0
        self.assertIn(
            f"grant-ended patient_user={row.pk} user={mrd.pk}", out.getvalue()
        )
        self.assertNotIn(f"stale patient_user={row.pk}", out.getvalue())

    def test_check_command_lists_rows_of_restricted_only_users(self):
        w = self.world
        out = StringIO()
        call_command("ihr_patient_user_check", stdout=out)
        self.assertIn("no PatientUser row", out.getvalue())
        nurse = nurse_on_care_team(w)
        move_to(w, nurse, "Cashier")
        row = PatientUser.objects.get(user=nurse)
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("ihr_patient_user_check", stdout=out)
        self.assertIn(f"patient_user={row.pk} user={nurse.pk}", out.getvalue())
        self.assertNotIn(w.patient.name, out.getvalue())


class PlacementRefusalsOverTheApi(TestCase):
    """A placement refusal answers 400 with the trigger's reason, not a bare 500 (review C3)."""

    @classmethod
    def setUpTestData(cls):
        cls.world = build_world()

    def setUp(self):
        # Each request in its own transaction, as in every deployed setting (CARE's test
        # settings leave ATOMIC_REQUESTS off)
        patch = mock.patch.dict(connection.settings_dict, {"ATOMIC_REQUESTS": True})
        patch.start()
        self.addCleanup(patch.stop)

    def api(self, user):
        # A 500 comes back as a response (an assertion FAIL), not a raised exception
        client = APIClient(HTTP_HOST=HOST, raise_request_exception=False)
        client.force_authenticate(user)
        return client

    def test_a_department_role_at_the_root_answers_400(self):
        w = self.world
        target = w.user("acc-new-lab")
        url = (
            f"/api/v1/facility/{w.facility.external_id}/organizations/"
            f"{w.root.external_id}/users/"
        )
        response = self.api(w.users["Facility Admin"]).post(
            url,
            {
                "user": str(target.external_id),
                "role": str(w.role("Lab Technician").external_id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertIn("never at the facility root", response.content.decode())
        self.assertNotIn("ihr_role_placement", response.content.decode())
        self.assertFalse(FacilityOrganizationUser.objects.filter(user=target).exists())
        # the request's transaction was rolled back cleanly: the next one works
        response = self.api(w.users["Facility Admin"]).post(
            url.replace(
                str(w.root.external_id), str(w.departments["Laboratory"].external_id)
            ),
            {
                "user": str(target.external_id),
                "role": str(w.role("Lab Technician").external_id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])

    def test_add_user_with_the_hidden_role_and_no_grant_answers_400(self):
        w = self.world
        response = self.api(w.users["Doctor"]).post(
            f"/api/v1/patient/{w.patient.external_id}/add_user/",
            {
                "user": str(w.user("acc-no-grant").external_id),
                "role": str(w.role(registry.HIDDEN).external_id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertIn("needs a live records grant", response.content.decode())

    def test_a_refusal_inside_a_batch_answers_400_not_500(self):
        """Second review of C3: CARE's batch view binds the handler by name at import. A
        membership write is not on batch_allowlist.txt today; listed here for the test, a
        refusal inside a batch is the sub-request's 400 with the reason, never a 500, and the
        whole batch is rolled back."""
        from care.emr.api.viewsets.batch_request import BatchRequestView
        from django_tenants.utils import schema_context
        from ihr_identity import testing as identity
        from ihr_platform import batch
        from ihr_platform.models import Hospital
        from ihr_platform.test_runner import TEST_SCHEMA

        from ihr_access import exceptions

        self.assertEqual(exceptions.problems(), [])
        self.assertTrue(
            getattr(
                BatchRequestView().get_exception_handler(), exceptions.MARKER, False
            )
        )
        w = self.world
        target = w.user("acc-batch-lab")
        template = "/api/v1/facility/{facility}/organizations/{organization}/users/"
        listed = (("POST", template, batch.template_regex(template)),)
        url = template.format(
            facility=w.facility.external_id, organization=w.root.external_id
        )
        body = {
            "user": str(target.external_id),
            "role": str(w.role("Lab Technician").external_id),
        }
        with schema_context("public"):
            hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
        with (
            identity.fake_keycloak(),
            mock.patch.object(batch, "_compiled", return_value=listed),
        ):
            client = identity.staff_client(hospital, HOST, w.users["Facility Admin"])
            response = client.post(
                "/api/v1/batch_requests/",
                {
                    "requests": [
                        {
                            "url": url,
                            "method": "POST",
                            "body": body,
                            "reference_id": "a",
                        }
                    ]
                },
                format="json",
            )
        self.assertEqual(connection.schema_name, TEST_SCHEMA)
        self.assertEqual(response.status_code, 400, response.content[:400])
        [result] = response.json()["results"]
        self.assertEqual(result["status_code"], 400, result)
        self.assertIn("never at the facility root", json.dumps(result))
        self.assertFalse(FacilityOrganizationUser.objects.filter(user=target).exists())

    def test_a_handler_bound_by_name_before_the_wrap_is_rebound(self):
        """Second review of C3: a CARE module that imported the handler before ihr_access was
        ready holds CARE's unwrapped one; E022 names it and install() rebinds it."""
        from care.emr.api.viewsets import base, batch_request

        from ihr_access import exceptions

        original = base.emr_exception_handler.ihr_original
        with mock.patch.object(batch_request, "emr_exception_handler", original):
            self.assertIn(
                "care.emr.api.viewsets.batch_request.emr_exception_handler is not wrapped",
                exceptions.problems(),
            )
            exceptions.install()
            self.assertIs(
                batch_request.emr_exception_handler, base.emr_exception_handler
            )
            self.assertEqual(exceptions.problems(), [])

    def test_other_integrity_errors_still_raise(self):
        from django.db import IntegrityError as DjangoIntegrityError

        from ihr_access.exceptions import placement_refusal

        self.assertIsNone(placement_refusal(DjangoIntegrityError("duplicate key")))
        self.assertIsNone(placement_refusal(ValueError("ihr_role_placement: x")))


class IdentifierAllowListOnSearchAndWrites(TestCase):
    """The identifier allow-list on search and writes (review S2; deviation 10): a restricted
    user cannot search by, or change, an identifier whose system is not on
    identifier_configs.txt (the world's ART registration), so an answer never confirms that a
    person holds a programme number. Others search as in CARE."""

    @classmethod
    def setUpTestData(cls):
        cls.world = build_world()

    def api(self, user):
        client = APIClient(HTTP_HOST=HOST)
        client.force_authenticate(user)
        return client

    def search(self, user, config, value):
        return self.api(user).post(
            "/api/v1/patient/search/",
            {"config": str(config.external_id), "value": value},
            format="json",
        )

    def test_receptionist_cannot_search_by_a_programme_identifier(self):
        w = self.world
        self.assertEqual(w.programme_config.config["system"], PROGRAMME_SYSTEM)
        value = PatientIdentifier.objects.get(
            patient=w.patient, config=w.programme_config
        ).value
        for wrong in (value, "ART-0"):  # the same answer whether or not it matches
            with self.subTest(value=wrong):
                response = self.search(
                    w.users["Receptionist"], w.programme_config, wrong
                )
                self.assertEqual(response.status_code, 403, response.content[:300])
                self.assertNotIn(str(w.patient.external_id), response.content.decode())
                self.assertIn(
                    "cannot search by this identifier", response.content.decode()
                )

    def test_allowed_identifiers_and_other_roles_search_as_in_care(self):
        w = self.world
        phone = self.search(w.users["Receptionist"], w.phone_config, "+919876543210")
        self.assertEqual(phone.status_code, 200, phone.content[:300])
        self.assertEqual(
            [r["id"] for r in phone.json()["results"]], [str(w.patient.external_id)]
        )
        value = PatientIdentifier.objects.get(
            patient=w.patient, config=w.programme_config
        ).value
        doctor = self.search(w.users["Doctor"], w.programme_config, value)
        self.assertEqual(doctor.status_code, 200, doctor.content[:300])
        self.assertEqual(
            [r["id"] for r in doctor.json()["results"]], [str(w.patient.external_id)]
        )

    def test_receptionist_cannot_update_a_programme_identifier(self):
        w = self.world
        before = PatientIdentifier.objects.get(
            patient=w.patient, config=w.programme_config
        ).value
        response = self.api(w.users["Receptionist"]).post(
            f"/api/v1/patient/{w.patient.external_id}/update_identifier/",
            {"config": str(w.programme_config.external_id), "value": "ART-1"},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertIn("cannot change this identifier", response.content.decode())
        self.assertEqual(
            PatientIdentifier.objects.get(
                patient=w.patient, config=w.programme_config
            ).value,
            before,
        )
        # An allowed identifier is written: P1-T11b2 widens the action for the front desk
        response = self.api(w.users["Receptionist"]).post(
            f"/api/v1/patient/{w.patient.external_id}/update_identifier/",
            {"config": str(w.phone_config.external_id), "value": "+919800000000"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(
            PatientIdentifier.objects.get(
                patient=w.patient, config=w.phone_config
            ).value,
            "+919800000000",
        )

    def test_the_actions_are_guarded(self):
        from care.emr.api.viewsets.patient import PatientViewSet

        from ihr_access import identifiers

        self.assertEqual(identifiers.problems(), [])
        for name in identifiers.ACTIONS:
            self.assertTrue(getattr(PatientViewSet, name).detail is not None)
