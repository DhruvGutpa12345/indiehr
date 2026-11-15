"""The Owner's totals (P1-T11b4; design-b.md §8, §9 test 15; Q-K39 A).

Aggregates only, IST days, at most 366 days, amounts as two-decimal strings; never an id, a
name, a row or a breakdown; no patient loaded (the audit trail's read log stays empty); refused
to every role but the Owner.
"""

import datetime
import json
import re
from decimal import Decimal

from care.emr.models import Encounter
from care.emr.models.account import Account
from care.emr.models.invoice import Invoice
from care.emr.models.payment_reconciliation import PaymentReconciliation
from django.utils import timezone
from ihr_audit import reads
from ihr_roles import registry

from ihr_access import totals
from ihr_access.tests.base import SwapCase

URL = "/api/ihr/owner/totals/"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
IST = totals.IST


def ist_day(when):
    return when.astimezone(IST).date()


class OwnerTotals(SwapCase):
    """On the small world: one inpatient visit, in progress, in the one bed, started now."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        w = cls.world
        now = timezone.now()
        cls.today = ist_day(now)
        cls.yesterday = cls.today - datetime.timedelta(days=1)
        account = Account.objects.create(
            facility=w.facility,
            patient=w.patient,
            primary_encounter=w.encounter,
            status="active",
            billing_status="open",
            name="IPD account",
            total_balance=Decimal("250.50"),
        )
        Account.objects.create(  # closed in error: not outstanding
            facility=w.facility,
            patient=w.patient,
            status="entered_in_error",
            billing_status="closed_voided",
            name="Mistake",
            total_balance=Decimal("999.00"),
        )

        def bill(status, amount, issued):
            return Invoice.objects.create(
                facility=w.facility,
                patient=w.patient,
                account=account,
                status=status,
                title="Bill",
                total_gross=Decimal(amount),
                total_net=Decimal(amount),
                issue_date=issued,
            )

        bill("issued", "1000.00", now)
        bill("balanced", "200.25", now - datetime.timedelta(days=1))
        bill("draft", "99.00", None)
        bill("cancelled", "50.00", now)

        def pay(
            amount,
            method="cash",
            *,
            credit=False,
            status="active",
            kind="payment",
            outcome="complete",
        ):
            return PaymentReconciliation.objects.create(
                facility=w.facility,
                account=account,
                reconciliation_type=kind,
                status=status,
                kind="deposit",
                issuer_type="patient",
                outcome=outcome,
                method=method,
                payment_datetime=now,
                tendered_amount=Decimal(amount),
                returned_amount=Decimal(0),
                amount=Decimal(amount),
                is_credit_note=credit,
            )

        pay("700.50")
        pay("50.00", "debc", kind="advance")
        pay("100.00", credit=True)
        pay("999.00", status="cancelled")
        pay("999.00", kind="adjustment")  # moves no money (Q-K46, open)
        # Not money received (review A1): a declined card, a queued and a partial payment, a
        # credit note that failed; CARE's own totals leave each out
        pay("999.00", "ccca", outcome="error")
        pay("999.00", "ccca", outcome="queued")
        pay("999.00", "chck", outcome="partial", kind="advance")
        pay("999.00", credit=True, outcome="error")
        # A discharged inpatient visit: started and ended yesterday (10:00-12:00 IST)
        start = datetime.datetime.combine(cls.yesterday, datetime.time(10), IST)
        Encounter.objects.create(
            patient=w.patient,
            facility=w.facility,
            status="completed",
            encounter_class="imp",
            priority="routine",
            period={
                "start": start.isoformat(),
                "end": (start + datetime.timedelta(hours=2)).isoformat(),
            },
        )
        # A visit with no period at all (CARE's demo data): counted from its creation
        Encounter.objects.create(
            patient=w.patient,
            facility=w.facility,
            status="planned",
            encounter_class="amb",
            priority="routine",
        )

    def params(self, **change):
        values = {
            "facility": str(self.world.facility.external_id),
            "from": self.yesterday.strftime("%d-%m-%Y"),
            "to": self.today.strftime("%d-%m-%Y"),
        }
        values.update(change)
        return {k: v for k, v in values.items() if v is not None}

    def get(self, who="Owner", **change):
        return self.api(who).get(URL, self.params(**change))

    def test_owner_totals_count_and_sum(self):
        answer = self.get()
        self.assertEqual(answer.status_code, 200, answer.content[:300])
        body = answer.json()
        self.assertEqual(
            body["encounters"],
            [
                {"encounter_class": "amb", "status": "planned", "count": 1},
                {"encounter_class": "imp", "status": "completed", "count": 1},
                {"encounter_class": "imp", "status": "in_progress", "count": 1},
            ],
        )
        self.assertEqual(body["inpatients_now"], 1)
        self.assertEqual(body["beds"], {"total": 1, "occupied": 1})
        y, t = self.yesterday.strftime("%d-%m-%Y"), self.today.strftime("%d-%m-%Y")
        self.assertEqual(
            body["series"],
            [
                {"period": y, "admissions": 1, "discharges": 1, "invoiced": "200.25", "collected": "0.00"},
                {"period": t, "admissions": 1, "discharges": 0, "invoiced": "1000.00", "collected": "750.50"},
            ],
        )  # fmt: skip
        self.assertEqual(
            body["invoices_by_status"],
            [
                {"status": "balanced", "count": 1, "amount": "200.25"},
                {"status": "cancelled", "count": 1, "amount": "50.00"},
                {"status": "draft", "count": 1, "amount": "99.00"},
                {"status": "issued", "count": 1, "amount": "1000.00"},
            ],
        )
        self.assertEqual(
            body["collected_by_method"],
            [
                {"method": "cash", "count": 1, "amount": "700.50"},
                {"method": "debc", "count": 1, "amount": "50.00"},
            ],
        )
        self.assertEqual(body["refunded"], "100.00")
        self.assertEqual(body["outstanding_now"], "250.50")
        self.assertEqual((body["currency"], body["time_zone"]), ("INR", "Asia/Kolkata"))

    def test_owner_totals_by_month(self):
        body = self.get(by="month").json()
        months = sorted(
            {self.yesterday.strftime("%m-%Y"), self.today.strftime("%m-%Y")}
        )
        self.assertEqual([s["period"] for s in body["series"]], months)
        self.assertEqual(sum(s["admissions"] for s in body["series"]), 2)
        self.assertEqual(
            sum(Decimal(s["collected"]) for s in body["series"]), Decimal("750.50")
        )

    def test_owner_totals_load_no_patient(self):
        """Aggregates build no model instance: the audit trail's read log sees no patient
        (mutation owner-aggregates), and the request leaves no ``request`` entry."""
        token = reads.start()
        totals.compute(self.world.facility, self.yesterday, self.today, "day")
        patients, total = reads.finish(token, reads.MAX_PATIENTS)
        self.assertEqual((patients, total), ([], 0))
        from ihr_access.tests.test_records import entries_since, mark

        since = mark()
        self.assertEqual(self.get().status_code, 200)
        self.assertEqual(entries_since(since, kind="request"), [])

    def test_owner_totals_name_no_one(self):
        w = self.world
        text = self.get().content.decode()
        self.assertIsNone(UUID.search(text), text)
        for word in (w.patient.name, w.patient.phone_number[3:], w.facility.name):
            self.assertNotIn(word, text)
        body = json.loads(text)
        self.assertEqual(
            set(body),
            {
                "from", "to", "by", "time_zone", "currency", "encounters",
                "inpatients_now", "beds", "invoices_by_status", "collected_by_method",
                "refunded", "outstanding_now", "series",
            },
        )  # fmt: skip

    def test_owner_totals_refused_to_every_other_role(self):
        for who in (*registry.EIGHT, "Doctor", "Nurse", "Facility Admin"):
            if who == "Owner":
                continue
            with self.subTest(who=who):
                self.assertEqual(self.get(who).status_code, 403)
        # the Owner's patient search stays refused (b2's search gate)
        searched = self.api("Owner").post(
            "/api/v1/patient/search/",
            {"phone_number": self.world.patient.phone_number},
            format="json",
        )
        self.assertEqual(searched.status_code, 403)

    def test_owner_totals_range_rules(self):
        cases = (
            ({"from": "2026-11-01"}, 400),
            ({"to": None}, 400),
            ({"from": "02-11-2026", "to": "01-11-2026"}, 400),
            ({"from": "01-01-2025", "to": "02-01-2026"}, 400),  # 367 days
            ({"from": "01-01-2025", "to": "01-01-2026"}, 200),  # 366 days
            ({"by": "week"}, 400),
            # Review A2: far dates answer 400 on the right field, never 500
            ({"from": "01-01-9999", "to": "31-12-9999"}, 400),
            ({"from": "01-01-9999", "to": "31-12-9999", "by": "month"}, 400),
            ({"from": "01-01-0001", "to": "31-12-0001"}, 400),
            ({"from": "31-12-1999", "to": "01-01-2000"}, 400),
            ({"from": "01-01-2000", "to": "01-01-2000"}, 200),
            ({"facility": None}, 400),
            ({"facility": "not-an-id"}, 403),
            ({"facility": "00000000-0000-4000-8000-000000000000"}, 403),
        )
        for change, code in cases:
            with self.subTest(change=change):
                self.assertEqual(self.get(**change).status_code, code)
        far = self.get(**{"from": "01-01-9999", "to": "31-12-9999"}).json()
        self.assertEqual(set(far), {"to"})
        early = self.get(**{"from": "01-01-0001", "to": "31-12-0001"}).json()
        self.assertEqual(set(early), {"from"})
        ahead = self.today + datetime.timedelta(days=totals.MAX_DAYS_AHEAD)
        self.assertEqual(
            self.get(
                **{
                    "from": (ahead - datetime.timedelta(days=30)).strftime("%d-%m-%Y"),
                    "to": ahead.strftime("%d-%m-%Y"),
                }
            ).status_code,
            200,
        )
        from care.facility.models import Facility
        from model_bakery import baker

        elsewhere = baker.make(Facility, created_by=self.world.admin, name="Elsewhere")
        self.assertEqual(self.get(facility=str(elsewhere.external_id)).status_code, 403)
