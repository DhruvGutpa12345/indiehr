"""The named write tests (P1-T11b2; design-b.md §9 test 9): clinical writes stay closed to every
restricted role, and the billing desks keep exactly their grants (Q-K21: discounts,
cancellations and locked invoices stay with the Facility Admin). Patient and encounter writes
are in test_patient_swap and test_encounter_swap."""

import datetime
from decimal import Decimal

from care.emr.models.account import Account
from care.emr.models.charge_item import ChargeItem
from care.emr.models.file_upload import FileUpload
from care.emr.models.invoice import Invoice
from care.emr.models.payment_reconciliation import PaymentReconciliation
from care.security.authorization.base import AuthorizationController
from django.conf import settings
from django.utils import timezone
from ihr_roles.registry import RESTRICTED

from ihr_access.tests.base import SwapCase

BILLING_DENIED = ("Cashier", "TPA Desk", "Owner")


class ClinicalWrites(SwapCase):
    def test_clinical_writes_refused_to_every_restricted_role(self):
        """Allergy, notes and patient files: 403 for each restricted role (CARE's
        ``can_write_patient_obj`` and encounter checks, which none of them holds)."""
        w = self.world
        pid = w.patient.external_id
        writes = (
            (
                f"/api/v1/patient/{pid}/allergy_intolerance/",
                {
                    "encounter": str(w.encounter.external_id),
                    "category": "food",
                    "clinical_status": "active",
                    "verification_status": "confirmed",
                    "criticality": "low",
                    "code": {
                        "display": "Test Value",
                        "system": "http://test_system.care/test",
                        "code": "123",
                    },
                },
            ),
            (f"/api/v1/patient/{pid}/thread/", {"title": "Front desk note"}),
            (
                "/api/v1/files/",
                {
                    "name": "file",
                    "original_name": "file.jpg",
                    "file_type": "patient",
                    "file_category": "unspecified",
                    "associating_id": str(pid),
                    "mime_type": "image/jpeg",
                },
            ),
        )
        for role in sorted(RESTRICTED):
            for url, body in writes:
                with self.subTest(role=role, url=url):
                    response = self.api(role).post(url, body, format="json")
                    self.assertEqual(response.status_code, 403, response.content[:300])
        self.assertFalse(FileUpload.objects.filter(associating_id=str(pid)).exists())
        # CARE's Doctor still writes them
        response = self.api("Doctor").post(
            f"/api/v1/patient/{pid}/thread/", {"title": "Doctor note"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content[:300])


class BillingWrites(SwapCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        w = cls.world
        cls.account = Account.objects.create(
            facility=w.facility,
            patient=w.patient,
            primary_encounter=w.encounter,
            status="active",
            billing_status="open",
            name="IPD account",
        )

    def base(self):
        return f"/api/v1/facility/{self.world.facility.external_id}"

    def backdate(self, model, obj, minutes):
        model.objects.filter(pk=obj.pk).update(
            created_date=timezone.now() - datetime.timedelta(minutes=minutes + 60)
        )

    def charge_item(self):
        w = self.world
        component = {
            "monetary_component_type": "base",
            "currency": "INR",
            "amount": "100.00",
        }
        item = ChargeItem.objects.create(
            facility=w.facility,
            title="Room rent",
            patient=w.patient,
            encounter=w.encounter,
            account=self.account,
            status="billable",
            quantity=Decimal("1.00"),
            unit_price_components=[component],
            total_price_components=[component],
            total_price=Decimal("100.00"),
        )
        self.backdate(ChargeItem, item, settings.CHARGE_ITEM_FREE_CANCEL_PERIOD_MINUTES)
        return item

    def invoice(self):
        w = self.world
        invoice = Invoice.objects.create(
            facility=w.facility,
            patient=w.patient,
            account=self.account,
            status="issued",
            title="Final bill",
        )
        self.backdate(Invoice, invoice, settings.INVOICE_FREE_CANCEL_PERIOD_MINUTES)
        return invoice

    def payment(self):
        payment = PaymentReconciliation.objects.create(
            facility=self.world.facility,
            account=self.account,
            reconciliation_type="payment",
            status="active",
            kind="deposit",
            issuer_type="patient",
            outcome="complete",
            method="cash",
            tendered_amount=Decimal(100),
            returned_amount=Decimal(0),
            amount=Decimal(100),
        )
        self.backdate(
            PaymentReconciliation,
            payment,
            settings.PAYMENT_RECONCILIATION_FREE_CANCEL_PERIOD_MINUTES,
        )
        return payment

    def test_charge_item_create(self):
        """The Cashier adds charges; the TPA Desk reads them only."""
        w = self.world
        body = {
            "title": "Consultation",
            "status": "billable",
            "quantity": 1.0,
            "unit_price_components": [
                {
                    "monetary_component_type": "base",
                    "currency": "INR",
                    "amount": "500.00",
                }
            ],
            "encounter": str(w.encounter.external_id),
            "account": str(self.account.external_id),
        }
        url = f"{self.base()}/charge_item/"
        response = self.api("Cashier").post(url, body, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        for role in ("TPA Desk", "Owner", "Receptionist"):
            with self.subTest(role=role):
                response = self.api(role).post(url, body, format="json")
                self.assertEqual(response.status_code, 403, response.content[:300])

    def test_billing_denials(self):
        """Cancelling a bill, payment or charge after CARE's free period, and locking or
        unlocking a bill, need the Facility Admin's rights: 403 for the Cashier, the TPA Desk
        and the Owner, 200 for the Facility Admin."""

        def calls():
            invoice, payment, item = self.invoice(), self.payment(), self.charge_item()
            invoices = f"{self.base()}/invoice/{invoice.external_id}"
            return (
                ("invoice cancel", f"{invoices}/cancel_invoice/", "post", {"reason": "cancelled"}),
                ("invoice lock", f"{invoices}/lock/", "post", {}),
                ("payment cancel",
                 f"{self.base()}/payment_reconciliation/{payment.external_id}/cancel_payment_reconciliation/",
                 "post", {"reason": "cancelled"}),
                ("charge cancel", f"{self.base()}/charge_item/{item.external_id}/", "put",
                 {"title": item.title, "status": "entered_in_error", "quantity": "1.00",
                  "unit_price_components": item.unit_price_components}),
            )  # fmt: skip

        for role in BILLING_DENIED:
            for name, url, method, body in calls():
                with self.subTest(role=role, call=name):
                    response = getattr(self.api(role), method)(url, body, format="json")
                    self.assertEqual(response.status_code, 403, response.content[:300])
        for name, url, method, body in calls():
            with self.subTest(role="Facility Admin", call=name):
                response = getattr(self.api("Facility Admin"), method)(
                    url, body, format="json"
                )
                self.assertEqual(response.status_code, 200, response.content[:300])
        # Unlocking a locked bill
        invoice = self.invoice()
        Invoice.objects.filter(pk=invoice.pk).update(locked=True)
        url = f"{self.base()}/invoice/{invoice.external_id}/unlock/"
        for role in BILLING_DENIED:
            with self.subTest(role=role, call="invoice unlock"):
                self.assertEqual(
                    self.api(role).post(url, {}, format="json").status_code, 403
                )
        self.assertEqual(
            self.api("Facility Admin").post(url, {}, format="json").status_code, 200
        )
        # A negative charge: CARE computes it without the negative part unless the user holds
        # the right (charge_item.py apply_charge_item_defs); only the Facility Admin does
        w = self.world
        for role in BILLING_DENIED:
            with self.subTest(role=role, call="negative charge"):
                self.assertFalse(
                    AuthorizationController.call(
                        "can_create_negative_charge_item_in_facility",
                        w.users[role],
                        w.facility,
                    )
                )
        self.assertTrue(
            AuthorizationController.call(
                "can_create_negative_charge_item_in_facility",
                w.users["Facility Admin"],
                w.facility,
            )
        )
