"""The demo seed's inpatient stay (P1-T11c2; design-c.md §4, risk R6).

CARE's demo data gives every visit a random class and no bed, so the billing desks' screens
(the visit's bed on the status page, the account's "Bed charges" tab) had nothing to show. The
seed helper ``add_inpatient_stay`` turns one account's visit into an inpatient stay in a free
bed. Synthetic data only; it must be safe to run again.
"""

from care.emr.models import Encounter
from care.emr.models.account import Account
from care.emr.models.location import FacilityLocation, FacilityLocationEncounter
from ihr_platform.seeding import add_inpatient_stay

from ihr_access.tests.base import SwapCase


class SeedStay(SwapCase):
    def outpatient_account(self, name="Demo OPD account"):
        """A visit that is not an inpatient stay, and an account whose primary visit it is."""
        w = self.world
        patient, _ = self.other_patient()
        encounter = Encounter.objects.create(
            patient=patient,
            facility=w.facility,
            status="planned",
            encounter_class="amb",
            priority="routine",
            period={},
        )
        account = Account.objects.create(
            facility=w.facility,
            patient=patient,
            primary_encounter=encounter,
            status="active",
            billing_status="open",
            name=name,
        )
        return account, encounter

    def free_bed(self, name="Bed 7"):
        w = self.world
        return FacilityLocation.objects.create(
            facility=w.facility,
            parent=w.bed.parent,
            name=name,
            description="a free bed",
            status="active",
            operational_status="O",
            mode="instance",
            form="bd",
        )

    def test_nothing_to_do_without_an_account(self):
        Account.objects.all().delete()
        self.assertIsNone(add_inpatient_stay())

    def test_a_visit_without_a_free_bed_is_left_alone(self):
        # the world's one bed is taken by its own visit
        _account, encounter = self.outpatient_account()
        self.assertIsNone(add_inpatient_stay())
        encounter.refresh_from_db()
        self.assertEqual(encounter.encounter_class, "amb")
        self.assertIsNone(encounter.current_location)

    def test_the_account_visit_becomes_an_inpatient_stay_in_the_free_bed(self):
        account, encounter = self.outpatient_account()
        bed = self.free_bed()

        done = add_inpatient_stay()

        self.assertEqual(done.pk, encounter.pk)
        encounter.refresh_from_db()
        bed.refresh_from_db()
        self.assertEqual(encounter.encounter_class, "imp")
        self.assertEqual(encounter.status, "in_progress")
        self.assertEqual(encounter.current_location_id, bed.pk)
        self.assertEqual(bed.current_encounter_id, encounter.pk)
        stay = FacilityLocationEncounter.objects.get(encounter=encounter)
        self.assertEqual(
            (stay.location_id, stay.status, stay.end_datetime),
            (bed.pk, "active", None),
        )
        # what the account page reads
        account.refresh_from_db()
        self.assertEqual(account.primary_encounter_id, encounter.pk)

    def test_the_stay_patient_gets_the_permanent_address_cares_form_would_give(self):
        """CARE's demo patients have none, and CARE's edit form then refuses to save (the
        front-desk spec edits this patient's phone)."""
        _, encounter = self.outpatient_account()
        self.free_bed()
        patient = encounter.patient
        type(patient).objects.filter(pk=patient.pk).update(
            address="12 MG Road, Kochi", permanent_address=""
        )

        add_inpatient_stay()

        patient.refresh_from_db()
        self.assertEqual(patient.permanent_address, "12 MG Road, Kochi")

    def test_the_stay_patient_gets_the_emergency_phone_cares_form_would_give(self):
        """CARE refuses an empty emergency phone, which its edit form sends when the stored one
        is empty (the box "same as phone" is then unticked)."""
        _, encounter = self.outpatient_account()
        self.free_bed()
        patient = encounter.patient
        type(patient).objects.filter(pk=patient.pk).update(
            phone_number="+919876543210", emergency_phone_number=""
        )

        add_inpatient_stay()

        patient.refresh_from_db()
        self.assertEqual(patient.emergency_phone_number, "+919876543210")

    def test_an_emergency_phone_already_set_is_left_alone(self):
        _, encounter = self.outpatient_account()
        self.free_bed()
        patient = encounter.patient
        type(patient).objects.filter(pk=patient.pk).update(
            phone_number="+919876543210", emergency_phone_number="+919812345678"
        )

        add_inpatient_stay()

        patient.refresh_from_db()
        self.assertEqual(patient.emergency_phone_number, "+919812345678")

    def test_a_permanent_address_already_set_is_left_alone(self):
        _, encounter = self.outpatient_account()
        self.free_bed()
        patient = encounter.patient
        type(patient).objects.filter(pk=patient.pk).update(
            address="12 MG Road, Kochi", permanent_address="4 Station Road, Thrissur"
        )

        add_inpatient_stay()

        patient.refresh_from_db()
        self.assertEqual(patient.permanent_address, "4 Station Road, Thrissur")

    def test_running_it_again_changes_nothing(self):
        self.outpatient_account()
        self.free_bed()
        self.free_bed("Bed 8")
        self.assertIsNotNone(add_inpatient_stay())

        self.assertIsNone(add_inpatient_stay())

        self.assertEqual(
            FacilityLocationEncounter.objects.filter(
                encounter__account__isnull=False
            ).count(),
            1,
        )
        self.assertEqual(
            FacilityLocation.objects.filter(
                current_encounter__isnull=False, name__in=["Bed 7", "Bed 8"]
            ).count(),
            1,
        )
