"""CARE 3.1's referrals are not clinical slugs, and closed to ``branches.closed`` users (P2-T0a,
owner Q-K163; ihr_access/referrals.py).

CARE's role-organisation roles "Member" and "Manager" hold ``can_read_referral``,
``can_write_referral`` and ``can_create_referral``. A Receptionist who is also a referral
network's Member must stay restricted (patient projection; no care-team row: test_placement,
test_patient_user), and must not reach a referral, its notes, form answers, attachments or files
through that Member role: every referral route refuses before CARE reads the body, and CARE's
referral checks answer False for such a person (file links, references)."""

from care.emr.models.file_upload import FileUpload
from care.emr.models.organization import Organization, OrganizationUser
from care.emr.models.referral import Referral, ReferralAvailability
from care.security.authorization import AuthorizationController
from django.test import SimpleTestCase

from ihr_access import referrals, state
from ihr_access.testing import CANARY
from ihr_access.tests.base import SwapCase

REFERRAL_CHECKS = (
    "can_read_referral",
    "can_write_referral",
)


class ReferralNetworkMember(SwapCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        w = cls.world
        cls.network = Organization.objects.create(
            name="Physiotherapy network", org_type="role"
        )
        cls.member = w.user("ref-rec-member", ("Receptionist", w.root))
        cls.care_member = w.user("ref-care-member")  # CARE's Member only: CARE's answer
        for person in (cls.member, cls.care_member):
            OrganizationUser.objects.create(
                organization=cls.network, user=person, role=w.role("Member")
            )
        cls.availability = ReferralAvailability.objects.create(
            entity_type="external_organization",
            external_organization=cls.network,
            status="active",
            name="Physiotherapy desk",
            referral_type="consultation",
        )
        cls.referral = Referral.objects.create(
            availability=cls.availability,
            patient=w.patient,
            encounter=w.encounter,
            title=f"Knee rehabilitation {CANARY}",
            note=f"after the fall {CANARY}",
        )
        cls.upload = FileUpload(
            name=f"Knee X-ray {CANARY}",
            internal_name="knee.pdf",
            associating_id=str(cls.referral.external_id),
            file_type="referral",
            file_category="unspecified",
            upload_completed=True,
            meta={"mime_type": "application/pdf"},
        )
        cls.upload.save()

    def ref(self, tail=""):
        return f"/api/v1/referral/{self.referral.external_id}/{tail}"

    def reads(self):
        network = str(self.network.external_id)
        files = f"/api/v1/files/?file_type=referral&associating_id={self.referral.external_id}"
        return (
            f"/api/v1/referral/?entity_type=external_organization&entity_id={network}",
            f"/api/v1/referral/?encounter={self.world.encounter.external_id}",
            f"/api/v1/referral/?patient={self.world.patient.external_id}",
            self.ref(),
            self.ref("questionnaires/"),
            self.ref("referenced_by/"),
            self.ref("notes/"),
            self.ref("questionnaire_responses/"),
            self.ref("resources/"),
            files,
            f"/api/v1/files/{self.upload.external_id}/",
        )

    def writes(self):
        return (
            ("post", "/api/v1/referral/"),
            ("put", self.ref()),
            ("patch", self.ref()),
            ("post", self.ref("set_assignees/")),
            ("post", self.ref("thread/")),
            ("put", self.ref("thread/")),
            ("post", self.ref("notes/")),
            ("post", self.ref("questionnaire_responses/")),
            ("post", self.ref("resources/")),
        )

    def test_the_member_stays_restricted_and_sees_patients_projected(self):
        w = self.world
        self.assertTrue(state.restricted(self.member))
        response = self.api(self.member).get(
            f"/api/v1/patient/{w.patient.external_id}/"
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertNotIn(CANARY, response.content.decode())
        self.assertIsNone(response.json()["blood_group"])

    def test_care_answers_its_own_member(self):
        """Positive control: CARE's Member alone reads the referral, its list and its file."""
        for url in (self.ref(), self.reads()[0], self.reads()[-2]):
            with self.subTest(url=url):
                answer = self.api(self.care_member).get(url)
                self.assertEqual(answer.status_code, 200, answer.content[:300])
        for name in REFERRAL_CHECKS:
            self.assertTrue(
                AuthorizationController.call(name, self.care_member, self.referral)
            )

    def test_a_restricted_member_cannot_read_a_referral(self):
        for url in self.reads():
            with self.subTest(url=url):
                answer = self.api(self.member).get(url)
                self.assertIn(answer.status_code, (403, 404), answer.content[:300])
                self.assertNotIn(CANARY, answer.content.decode())
                self.assertNotIn(self.world.patient.name, answer.content.decode())

    def test_a_restricted_member_cannot_write_a_referral(self):
        """Refused before CARE reads the body: an empty body answers 403, never 400."""
        for method, url in self.writes():
            with self.subTest(method=method, url=url):
                answer = getattr(self.api(self.member), method)(url, {}, format="json")
                self.assertEqual(answer.status_code, 403, answer.content[:300])
        self.referral.refresh_from_db()
        self.assertIsNone(self.referral.thread_id)

    def test_cares_referral_checks_say_no_to_a_restricted_member(self):
        for name in REFERRAL_CHECKS:
            with self.subTest(check=name):
                self.assertFalse(
                    AuthorizationController.call(name, self.member, self.referral)
                )
        self.assertFalse(
            AuthorizationController.call(
                "can_list_patient_referrals", self.member, self.world.patient
            )
        )
        self.assertFalse(
            AuthorizationController.call(
                "can_list_entity_referrals", self.member, self.availability
            )
        )

    def test_the_gate_holds(self):
        self.assertEqual(referrals.problems(), [])


class ReferralRoutesReviewed(SimpleTestCase):
    """E022 finds every referral view the hospital's URL configuration serves, on any router or
    plugin urlconf, as actions.action_viewsets does (review A2)."""

    def served_with(self, *extra):
        original = referrals._views
        found = [*original(), *extra]
        referrals._views = lambda: found
        try:
            return referrals.problems()
        finally:
            referrals._views = original

    def test_every_served_referral_view_is_found(self):
        from care.emr.api.viewsets import referral

        names = {cls.__name__ for _route, cls in referrals._served()}
        self.assertEqual(names, set(referrals.GATED) | set(referrals.NOT_GATED))
        self.assertTrue(
            all(cls.__module__ == referral.__name__ for _r, cls in referrals._served())
        )

    def test_a_referral_view_served_elsewhere_is_a_problem(self):
        """A subclass of CARE's referral base on a patient route (another module), a plugin's
        view of the Referral model, and a reviewed-not-gated class served at another route."""
        from care.emr.api.viewsets.base import EMRBaseViewSet
        from care.emr.api.viewsets.referral import (
            AssignedReferralViewSet,
            ReferralBaseViewSet,
        )
        from care.emr.models.referral import Referral

        class PatientReferralViewSet(ReferralBaseViewSet):
            pass

        PatientReferralViewSet.__module__ = "care.emr.api.viewsets.patient"

        class PluginCasesViewSet(EMRBaseViewSet):
            database_model = Referral

        PluginCasesViewSet.__module__ = "ihr_plugin.views"
        problems = self.served_with(
            ("api/v1/patient/<patient>/cases/", PatientReferralViewSet),
            ("api/v1/cases/", PluginCasesViewSet),
            ("api/v1/patient/<patient>/assigned/", AssignedReferralViewSet),
        )
        for name in ("PatientReferralViewSet", "PluginCasesViewSet"):
            self.assertTrue(
                any(name in p and "has not reviewed" in p for p in problems), problems
            )
            self.assertTrue(
                any(f"{name}.initial is not our referral gate" in p for p in problems),
                problems,
            )
        self.assertTrue(
            any(
                "AssignedReferralViewSet" in p and "patient/<patient>/assigned" in p
                for p in problems
            ),
            problems,
        )
