"""CARE's action engine and its assigned-referral list, closed to our roles (P2-T0a, review F2;
ihr_access/actions.py, ihr_access/referrals.py)."""

import datetime
import re
from pathlib import Path

from care.emr.models.action import Action
from care.emr.models.referral import Referral, ReferralAvailability
from care.emr.models.scheduling.booking import TokenBooking, TokenSlot
from django.core.cache import cache
from django.test import SimpleTestCase
from django.utils import timezone
from ihr_roles.registry import RESTRICTED

from ihr_access import actions, referrals
from ihr_access.testing import CANARY
from ihr_access.tests.base import SwapCase

OURS = (*sorted(RESTRICTED), "Radiologist")  # branches.closed: restricted, or ours only
OUTPUT = f"{CANARY}-action-output"


def performable_action():
    """An APPOINTMENT action whose message evaluates to OUTPUT (the definition holds no
    marker: anyone signed in may read it)."""
    head, tail = CANARY[:5], CANARY[5:]
    return Action.objects.create(
        name="Greeting",
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
    )


class ActionEngine(SwapCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cache.clear()  # CARE caches each context's actions for a day (models/action.py)
        cls.action = performable_action()

    def facility_url(self):
        return f"/api/v1/facility/{self.world.facility.external_id}"

    def execute(self, who):
        booking = self.world.booking
        return self.api(who).post(
            f"{self.facility_url()}/appointments/{booking.external_id}/execute_action/",
            {"action": str(self.action.external_id)},
            format="json",
        )

    def slot(self, hours):
        slot = self.world.booking.token_slot
        now = timezone.now()
        return TokenSlot.objects.create(
            resource=slot.resource,
            availability=slot.availability,
            start_datetime=now + datetime.timedelta(hours=hours),
            end_datetime=now + datetime.timedelta(hours=hours, minutes=15),
        )

    def book(self, who, slot):
        return self.api(who).post(
            f"{self.facility_url()}/slots/{slot.external_id}/create_appointment/",
            {"patient": str(self.world.patient.external_id), "note": "first visit"},
            format="json",
        )

    def test_our_roles_cannot_execute_an_action(self):
        """CARE checks only the booking right ("TODO: Authorize action permission"): 403 for
        every role of ours, the Receptionist (who holds it) included, and no output; CARE's
        Facility Admin runs it (the positive control)."""
        for role in OURS:
            with self.subTest(role=role):
                answer = self.execute(role)
                self.assertEqual(answer.status_code, 403, answer.content[:300])
                self.assertIn(actions.ACTIONS_REFUSED, answer.content.decode())
                self.assertNotIn(CANARY, answer.content.decode())
        admin = self.execute("Facility Admin")
        self.assertEqual(admin.status_code, 200, admin.content[:300])
        self.assertIn(OUTPUT, admin.content.decode())

    def test_the_answer_does_not_depend_on_the_body(self):
        """Refused before CARE reads the body: an empty body answers 403 too, not 400."""
        booking = self.world.booking
        answer = self.api("Receptionist").post(
            f"{self.facility_url()}/appointments/{booking.external_id}/execute_action/",
            {},
            format="json",
        )
        self.assertEqual(answer.status_code, 403, answer.content[:300])

    def test_a_front_desk_booking_runs_no_action(self):
        """A slot's create_appointment runs the APPOINTMENT actions as the person booking: for
        the Receptionist the booking is made, no action runs and ``_actions`` is not sent;
        CARE's Facility Admin gets the action's output."""
        answer = self.book("Receptionist", self.slot(2))
        self.assertEqual(answer.status_code, 200, answer.content[:300])
        self.assertNotIn("_actions", answer.json())
        self.assertNotIn(
            OUTPUT, answer.content.decode()
        )  # (the schedule's marker shows)
        self.assertTrue(
            TokenBooking.objects.filter(external_id=answer.json()["id"]).exists()
        )
        admin = self.book("Facility Admin", self.slot(3))
        self.assertEqual(admin.status_code, 200, admin.content[:300])
        [[ran]] = admin.json()["_actions"]
        self.assertEqual(ran["results"], {"message": OUTPUT})

    def test_the_evaluator_runs_nothing_for_our_roles(self):
        """Every other caller (the questionnaire submit) goes through ActionEvaluator.evaluate."""
        from care.action_evaluator.base import ActionEvaluator

        booking = self.world.booking

        def run(who):
            return ActionEvaluator(
                None,
                self.world.users[who],
                "Appointment",
                booking,
                self.action.actions,
            ).evaluate()

        for role in OURS:
            with self.subTest(role=role):
                self.assertEqual(run(role), [])
        [ran] = run("Doctor")
        self.assertEqual(ran["results"], {"message": OUTPUT})


class EngineReviewed(SimpleTestCase):
    """The gate holds, and a viewset mixing in the engine without review fails the start."""

    def test_the_gate_holds_on_every_reviewed_viewset(self):
        self.assertEqual(actions.problems(), [])
        self.assertEqual(
            set(actions.action_viewsets()), set(actions.reviewed()), "reviewed list"
        )

    def test_an_unreviewed_or_overriding_viewset_is_a_problem(self):
        from care.emr.api.viewsets.action_base import EMRActionBaseViewSet
        from care.emr.api.viewsets.base import EMRBaseViewSet

        class NewEngineViewSet(EMRBaseViewSet, EMRActionBaseViewSet):
            def perform_actions(self, instance):
                return []

        NewEngineViewSet.__module__ = "care.emr.api.viewsets.new"
        found = dict(actions.action_viewsets())
        found["care.emr.api.viewsets.new.NewEngineViewSet"] = {NewEngineViewSet}
        original = actions.action_viewsets
        actions.action_viewsets = lambda: found
        try:
            problems = actions.problems()
        finally:
            actions.action_viewsets = original
        self.assertTrue(any("is not reviewed" in p for p in problems), problems)
        self.assertTrue(
            any("overrides our action gate" in p for p in problems), problems
        )

    def test_every_caller_of_the_evaluator_is_reviewed(self):
        """ActionEvaluator.evaluate is the gate for callers outside EMRActionBaseViewSet;
        a new caller is reviewed here (does its answer carry the outputs to our roles?)."""
        import care

        root = Path(care.__file__).parent
        callers = sorted(
            str(path.relative_to(root))
            for path in root.rglob("*.py")
            if "/tests/" not in str(path)
            and re.search(r"\bActionEvaluator\(", path.read_text())
        )
        self.assertEqual(
            callers,
            [
                # perform_actions and execute_action (wrapped)
                "emr/api/viewsets/action_base.py",
                # the questionnaire submit's _actions: no role of ours may submit a form
                # (registry.CLINICAL_SLUGS: can_submit_*), and evaluate runs nothing for them
                "emr/api/viewsets/questionnaire/questionnaire.py",
            ],
        )


class AssignedReferrals(SwapCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        w = cls.world
        availability = ReferralAvailability.objects.create(
            entity_type="facility",
            facility=w.facility,
            status="active",
            name="Physiotherapy desk",
            referral_type="consultation",
        )
        role = {"system": "http://snomed.info/sct", "code": "36682004"}
        cls.referral = Referral.objects.create(
            availability=availability,
            patient=w.patient,
            encounter=w.encounter,
            title=f"Knee rehabilitation {CANARY}",
            note=f"after the fall {CANARY}",
            assignees=[{"user_id": u.pk, "role": role} for u in w.users.values()],
        )
        # review A1: assignees CARE's own roles give no referral read right: a person whose
        # memberships were removed (signed in still) and a Pharmacist on the care team
        cls.roleless = w.user("ref-roleless")
        cls.pharmacist = w.user("ref-pharmacist", ("Pharmacist", w.root))
        cls.referral.assignees = [
            *cls.referral.assignees,
            *({"user_id": u.pk, "role": role} for u in (cls.roleless, cls.pharmacist)),
        ]
        cls.referral.save()

    def test_our_roles_see_no_referral_assigned_to_them(self):
        """CARE lists a referral to its assignees with no permission check, each row naming
        the patient and the referral's clinical note: empty for every role of ours; CARE's
        Doctor sees the one assigned to him."""
        for role in OURS:
            with self.subTest(role=role):
                answer = self.api(role).get("/api/v1/referral_assigned/")
                self.assertEqual(answer.status_code, 200, answer.content[:300])
                self.assertEqual(answer.json()["results"], [])
                self.assertNotIn(CANARY, answer.content.decode())
                self.assertNotIn(self.world.patient.name, answer.content.decode())
        doctor = self.api("Doctor").get("/api/v1/referral_assigned/")
        self.assertEqual(
            [r["id"] for r in doctor.json()["results"]],
            [str(self.referral.external_id)],
        )

    def test_an_assignee_without_the_read_right_sees_nothing(self):
        """CARE's list checks no permission: an assignee whose roles here grant no
        can_read_referral (none at all, or CARE's Pharmacist) gets an empty list (review A1)."""
        self.assertEqual(
            sorted(self.referral.assignee_users),
            sorted(
                [u.pk for u in self.world.users.values()]
                + [self.roleless.pk, self.pharmacist.pk]
            ),
        )
        for who in (self.roleless, self.pharmacist):
            with self.subTest(user=who.username):
                answer = self.api(who).get("/api/v1/referral_assigned/")
                self.assertEqual(answer.status_code, 200, answer.content[:300])
                self.assertEqual(answer.json()["results"], [])
                self.assertNotIn(CANARY, answer.content.decode())
                self.assertNotIn(self.world.patient.name, answer.content.decode())

    def test_the_gate_holds(self):
        self.assertEqual(referrals.problems(), [])


class ConfigAndFileWrites(SwapCase):
    """P2-T0a: CARE now validates these bodies before it authorizes, so the write crawl's empty
    body answers 400 (role_matrix_writes.txt: body). With a valid body each is still refused by
    authorization to every role of ours: a personal form or value set (CARE's
    can_write_questionnaire / can_write_valueset), and a patient file's rename or archive
    (CARE's file_authorizer write)."""

    @classmethod
    def setUpTestData(cls):
        from care.emr.models.file_upload import FileUpload

        super().setUpTestData()
        w = cls.world
        cls.upload = FileUpload(
            name=f"Biopsy {CANARY}",
            internal_name="probe.pdf",
            associating_id=str(w.patient.external_id),
            file_type="patient",
            file_category="unspecified",
            upload_completed=True,
            meta={"mime_type": "application/pdf"},
        )
        cls.upload.save()

    def test_valid_bodies_are_refused_by_authorization(self):
        from care.emr.models import Questionnaire
        from care.emr.models.valueset import ValueSet

        facility = str(self.world.facility.external_id)
        file_url = f"/api/v1/files/{self.upload.external_id}/"
        for role in OURS:
            tag = role.lower().replace(" ", "-")
            writes = (
                (
                    "post",
                    "/api/v1/questionnaire/",
                    {
                        "slug": f"ihr-p2t0a-{tag}",
                        "title": "Front desk form",
                        "status": "active",
                        "subject_type": "encounter",
                        "questions": [],
                        "auth_context": "user",
                        "facility": facility,
                    },
                ),
                (
                    "post",
                    "/api/v1/valueset/",
                    {
                        "slug": f"ihr-p2t0a-{tag}",
                        "name": "Front desk codes",
                        "description": "",
                        "status": "active",
                        "compose": {"include": [{"system": "http://loinc.org"}]},
                        "disable_composition": False,
                        "auth_context": "user",
                        "facility": facility,
                        "inherited": False,
                    },
                ),
                ("patch", file_url, {"name": "renamed"}),
                ("post", f"{file_url}archive/", {"archive_reason": "duplicate"}),
            )
            for method, url, body in writes:
                with self.subTest(role=role, url=url):
                    answer = getattr(self.api(role), method)(url, body, format="json")
                    self.assertIn(answer.status_code, (403, 404), answer.content[:400])
                    self.assertNotIn(CANARY, answer.content.decode())
        self.assertFalse(
            Questionnaire.objects.filter(slug__startswith="ihr-p2t0a-").exists()
        )
        self.assertFalse(
            ValueSet.objects.filter(slug__startswith="ihr-p2t0a-").exists()
        )
        self.upload.refresh_from_db()
        self.assertIn(CANARY, self.upload.name)
        self.assertFalse(self.upload.is_archived)


def _accept_codes(_field, _slug, coding):
    """CARE's value-set check, accepting any coding: SNOMED answers nothing here until
    licensed content is loaded (P1-T10b), and these tests are about authorization."""
    return coding


class ValidBodyWritesRefused(SwapCase):
    """Review A3: the write crawl's empty body answers 400 on these routes (role_matrix_writes.txt:
    body), so the matrix no longer shows the refusal. With a valid body, every role of ours is
    refused by CARE's authorization and nothing is written; the same body by a superuser is
    accepted (the positive control: the body is valid). An upsert answers 400 for any refused
    datapoint, so there the datapoint's own answer must be a refusal (``detail``), not a
    validation error."""

    @classmethod
    def setUpTestData(cls):
        from care.emr.models.immunisation import (
            ImmunizationPolicy,
            ImmunizationRecommendation,
        )

        super().setUpTestData()
        w = cls.world
        cls.recommendation = ImmunizationRecommendation.objects.create(
            patient=w.patient,
            is_group=True,
            forecast_status="due",
            description="Childhood schedule",
            created_by=w.admin,
        )
        cls.policy = ImmunizationPolicy.objects.create(
            name="Childhood schedule",
            policy_template={"is_group": True, "description": "Childhood schedule"},
            created_by=w.admin,
        )

    def creates(self, tag):
        """(method, url, body, model) of each create, its body unique to ``tag``."""
        from care.emr.models.action import Action
        from care.emr.models.immunisation import (
            Immunization,
            ImmunizationPolicy,
            ImmunizationRecommendation,
        )
        from care.emr.models.procedure import Procedure
        from care.emr.models.valueset import ValueSet
        from care.emr.models.workspace import Workspace

        w = self.world
        facility = str(w.facility.external_id)
        patient = f"/api/v1/patient/{w.patient.external_id}"
        encounter = str(w.encounter.external_id)
        code = {"system": "http://snomed.info/sct", "code": "80146002", "display": "x"}
        workspace = {
            "name": f"Desk {tag}",
            "description": "",
            "template": {},
            "status": "active",
            "auth_context": "user",
            "facility": facility,
            "inherited": False,
        }
        action = {
            "name": f"Greeting {tag}",
            "actions": [],
            "performable": False,
            "action_context": "APPOINTMENT",
        }
        procedure = {"encounter": encounter, "status": "completed", "code": code}
        valueset = {
            "slug": f"ihr-a3-{tag}",
            "name": "Front desk codes",
            "description": "",
            "status": "active",
            "compose": {"include": [{"system": "http://loinc.org"}]},
            "disable_composition": False,
            "auth_context": "facility",
            "facility": facility,
            "inherited": False,
        }
        return (
            ("post", "/api/v1/workspace/", workspace, Workspace),
            (
                "post",
                "/api/v1/workspace/upsert/",
                {"datapoints": [workspace]},
                Workspace,
            ),
            ("post", "/api/v1/action_configuration/", action, Action),
            (
                "post",
                "/api/v1/action_configuration/upsert/",
                {"datapoints": [action]},
                Action,
            ),
            (
                "post",
                "/api/v1/immunization/policy/",
                {
                    "name": f"Schedule {tag}",
                    "policy_template": {"is_group": True},
                    "facility": facility,
                },
                ImmunizationPolicy,
            ),
            (
                "post",
                "/api/v1/referral_availability/",
                {
                    "name": f"Physiotherapy {tag}",
                    "status": "active",
                    "referral_type": "consultation",
                    "entity_type": "facility",
                    "facility": facility,
                },
                ReferralAvailability,
            ),
            ("post", f"{patient}/procedure/", procedure, Procedure),
            (
                "post",
                f"{patient}/procedure/upsert/",
                {"datapoints": [procedure]},
                Procedure,
            ),
            (
                "post",
                f"{patient}/immunization/record/",
                {
                    "encounter": encounter,
                    "status": "completed",
                    "code": code,
                    "primary_source": True,
                },
                Immunization,
            ),
            (
                "post",
                f"{patient}/immunization/recommendation/",
                {
                    "patient": str(w.patient.external_id),
                    "is_group": True,
                    "forecast_status": "due",
                    "description": f"Catch-up {tag}",
                },
                ImmunizationRecommendation,
            ),
            (
                "post",
                f"{patient}/immunization/recommendation/apply_policy/",
                {
                    "policy": str(self.policy.external_id),
                    "reference_date": "2026-11-24",
                },
                ImmunizationRecommendation,
            ),
            ("post", "/api/v1/valueset/upsert/", {"datapoints": [valueset]}, ValueSet),
        )

    def assert_refused(self, answer, url):
        if url.endswith("/upsert/"):
            self.assertEqual(answer.status_code, 400, answer.content[:400])
            self.assertIn("detail", answer.json()[0], answer.content[:400])
        else:
            self.assertIn(answer.status_code, (403, 404), answer.content[:400])
        self.assertNotIn(CANARY, answer.content.decode())

    def test_valid_creates_are_refused_and_write_nothing(self):
        from unittest import mock

        with mock.patch(
            "care.emr.utils.valueset_coding_type.validate_valueset", _accept_codes
        ):
            for role in OURS:
                user = self.user(role)
                tag = role.lower().replace(" ", "-")
                for method, url, body, model in self.creates(tag):
                    with self.subTest(role=role, url=url):
                        answer = getattr(self.api(role), method)(
                            url, body, format="json"
                        )
                        self.assert_refused(answer, url)
                        self.assertFalse(model.objects.filter(created_by=user).exists())
            for method, url, body, _model in self.creates("admin"):
                with self.subTest(control=url):
                    answer = getattr(self.api(self.world.admin), method)(
                        url, body, format="json"
                    )
                    self.assertIn(answer.status_code, (200, 201), answer.content[:600])

    def test_valid_updates_are_refused_and_change_nothing(self):
        from care.emr.models import Questionnaire

        w = self.world
        admin = self.api(w.admin)
        created = admin.post(
            "/api/v1/questionnaire/",
            {
                "slug": "ihr-a3-desk-form",
                "title": "Front desk form",
                "status": "active",
                "subject_type": "encounter",
                "questions": [],
                "auth_context": "facility",
                "facility": str(w.facility.external_id),
            },
            format="json",
        )
        self.assertEqual(created.status_code, 200, created.content[:600])
        form = Questionnaire.objects.get(slug="ihr-a3-desk-form")
        recommendation = (
            f"/api/v1/patient/{w.patient.external_id}/immunization/recommendation/"
            f"{self.recommendation.external_id}/"
        )
        writes = (
            (
                f"/api/v1/questionnaire/{form.external_id}/",
                {"title": "Renamed", "status": "active", "questions": []},
            ),
            (recommendation, {"description": "Renamed"}),
        )
        for role in OURS:
            for url, body in writes:
                for method in ("put", "patch"):
                    with self.subTest(role=role, method=method, url=url):
                        answer = getattr(self.api(role), method)(
                            url, body, format="json"
                        )
                        self.assert_refused(answer, url)
        form.refresh_from_db()
        self.recommendation.refresh_from_db()
        self.assertEqual(form.title, "Front desk form")
        self.assertEqual(self.recommendation.description, "Childhood schedule")
        for url, body in writes:
            with self.subTest(control=url):
                answer = admin.patch(url, body, format="json")
                self.assertEqual(answer.status_code, 200, answer.content[:600])
