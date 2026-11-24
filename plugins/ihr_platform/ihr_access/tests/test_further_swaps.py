"""The further swaps (P1-T11b2; design-b.md §5.3 items 3-4, "After the attack" §7, §10 tests
10-11): bed history, resource requests, devices, account reports, and patient-linked delivery
orders and supply deliveries. Restricted users are refused (or never see the patient's rows);
CARE's own roles are unchanged."""

import json
from decimal import Decimal
from unittest import mock

from care.emr.models import Product
from care.emr.models.account import Account
from care.emr.models.charge_item_definition import ChargeItemDefinition
from care.emr.models.location import (
    FacilityLocation,
    FacilityLocationEncounter,
    FacilityLocationOrganization,
)
from care.emr.models.medication_dispense import DispenseOrder
from care.emr.models.organization import FacilityOrganizationUser
from care.emr.models.product_knowledge import ProductKnowledge
from care.emr.models.report.report_upload import ReportUpload
from care.emr.models.report.template import Template
from care.emr.models.resource_request import ResourceRequest, ResourceRequestComment
from care.emr.models.supply_delivery import DeliveryOrder, SupplyDelivery
from django.db import connection
from django.utils import timezone
from ihr_identity import testing as identity
from ihr_roles.registry import RESTRICTED
from model_bakery import baker

from ihr_access.testing import CANARY
from ihr_access.tests.base import SwapCase
from ihr_access.views import PATIENT_LINK_REFUSED

EVERY_RESTRICTED = tuple(sorted(RESTRICTED))


class BedHistoryAndResourceRequests(SwapCase):
    def test_bed_history_refused_to_restricted(self):
        """A place's history embeds each whole encounter (location/spec.py). CARE lists it to
        anyone holding can_list_facility_locations on the place: the Lab Technician and the
        Store Keeper hold it for their own department's places (design §5.3 item 3)."""
        w = self.world
        lab_bay = FacilityLocation.objects.create(
            facility=w.facility,
            name="Sample collection bay",
            description="lab",
            status="active",
            operational_status="O",
            mode="instance",
            form="bd",
        )
        FacilityLocationOrganization.objects.create(
            location=lab_bay, organization=w.departments["Laboratory"]
        )
        for place in (w.bed, lab_bay):
            FacilityLocationEncounter.objects.create(
                location=place,
                encounter=w.encounter,
                status="completed" if place == lab_bay else "active",
                start_datetime=timezone.now(),
            )
        for place in (w.bed, lab_bay):
            url = (
                f"/api/v1/facility/{w.facility.external_id}/location/"
                f"{place.external_id}/association/"
            )
            admin = self.api("Facility Admin").get(url)
            self.assertEqual(admin.status_code, 200, admin.content[:300])
            self.assertIn(CANARY, admin.content.decode())
            for role in EVERY_RESTRICTED:
                with self.subTest(role=role, place=place.name):
                    response = self.api(role).get(url)
                    self.assertEqual(response.status_code, 403, response.content[:300])
                    self.assertNotIn(w.patient.name, response.content.decode())

    def test_resource_requests_refused_to_restricted(self):
        """CARE checks no permission on resource requests beyond membership, and each names its
        patient."""
        w = self.world
        request = ResourceRequest.objects.create(
            origin_facility=w.facility,
            approving_facility=w.facility,
            title=f"Oxygen for ward {CANARY}",
            reason="needed",
            status="pending",
            category="medicines",
            related_patient=w.patient,
        )
        ResourceRequestComment.objects.create(request=request, comment=f"c {CANARY}")
        urls = (
            "/api/v1/resource/",
            f"/api/v1/resource/{request.external_id}/",
            f"/api/v1/resource/{request.external_id}/comment/",
        )
        for url in urls:
            doctor = self.api("Doctor").get(url)
            self.assertEqual(doctor.status_code, 200, (url, doctor.content[:300]))
            self.assertIn(CANARY, doctor.content.decode())
        for role in EVERY_RESTRICTED:
            for url in urls:
                with self.subTest(role=role, url=url):
                    response = self.api(role).get(url)
                    self.assertEqual(response.status_code, 403, response.content[:300])
                    self.assertNotIn(CANARY, response.content.decode())
            with self.subTest(role=role, write="comment"):
                response = self.api(role).post(urls[2], {"comment": "x"}, format="json")
                self.assertEqual(response.status_code, 403, response.content[:300])

    def test_devices_are_closed_to_restricted(self):
        """ "After the attack" §7.2: an empty list, 403 for a device; CARE's roles unchanged."""
        w = self.world
        base = f"/api/v1/facility/{w.facility.external_id}/device/"
        admin = self.api("Facility Admin").get(base)
        self.assertEqual(admin.status_code, 200, admin.content[:300])
        self.assertIn(str(w.device.external_id), admin.content.decode())
        for role in EVERY_RESTRICTED:
            with self.subTest(role=role):
                listed = self.api(role).get(base)
                self.assertEqual(listed.status_code, 200, listed.content[:300])
                self.assertEqual(listed.json()["results"], [])
                opened = self.api(role).get(f"{base}{w.device.external_id}/")
                self.assertEqual(opened.status_code, 403, opened.content[:300])
                self.assertNotIn(w.patient.name, opened.content.decode())


class AccountReports(SwapCase):
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
        cls.template = Template.objects.create(
            facility=w.facility,
            slug=f"f-{w.facility.external_id}-final-bill",
            name="Final bill with diagnosis",
            status="active",
            template_type="account_report",
            default_format="pdf",
            template_data=(
                "<p>{{ encounter.diagnoses }} {{ encounter.prescriptions }} "
                f"{CANARY}</p>"
            ),
        )
        cls.report = ReportUpload.objects.create(
            template=cls.template,
            name=f"Final bill {CANARY}",
            internal_name="final-bill.pdf",
            associating_id=str(cls.account.external_id),
            upload_completed=True,
            report_type="account_report",
            created_by=w.users["Facility Admin"],
            updated_by=w.users["Facility Admin"],
        )

    def test_account_report_refused_to_restricted(self):
        """§10 test 10 (Q-K41 A): a report made by the Facility Admin from a template that
        prints diagnoses and prescriptions: 403 on list, retrieve, archive and generate for
        every restricted role, with no signed link; the Facility Admin lists it."""
        listing = (
            "/api/v1/template_reports/",
            {
                "report_type": "account_report",
                "associating_id": str(self.account.external_id),
            },
        )
        one = f"/api/v1/template_reports/{self.report.external_id}/"
        admin = self.api("Facility Admin").get(*listing)
        self.assertEqual(admin.status_code, 200, admin.content[:300])
        self.assertIn(CANARY, admin.content.decode())
        generate = {
            "template_id": str(self.template.external_id),
            "associating_id": str(self.account.external_id),
        }
        for role in EVERY_RESTRICTED:
            with (
                self.subTest(role=role),
                mock.patch(
                    "care.emr.api.viewsets.report.report_upload.generate_report_task"
                ) as task,
            ):
                answers = (
                    self.api(role).get(*listing),
                    self.api(role).get(one),
                    self.api(role).post(
                        f"{one}archive/", {"archive_reason": "x"}, format="json"
                    ),
                    self.api(role).post(
                        "/api/v1/template_reports/generate/", generate, format="json"
                    ),
                )
                for response in answers:
                    self.assertEqual(response.status_code, 403, response.content[:300])
                    text = response.content.decode()
                    self.assertNotIn(CANARY, text)
                    self.assertNotIn("signed_url", text)
                task.delay.assert_not_called()
        self.report.refresh_from_db()
        self.assertFalse(self.report.is_archived)


class PatientReturns(SwapCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        w = cls.world
        cls.store = FacilityLocation.objects.create(
            facility=w.facility,
            name="Main store",
            description="store",
            status="active",
            operational_status="O",
            mode="kind",
            form="wa",
        )
        FacilityLocationOrganization.objects.create(
            location=cls.store, organization=w.departments["Stores"]
        )
        cls.pharmacist = w.user(
            "acc-pharmacist", ("Pharmacist", w.departments["Stores"])
        )
        knowledge = baker.make(
            ProductKnowledge,
            facility=w.facility,
            slug=f"f-{w.facility.external_id}-tenofovir",
        )
        definition = baker.make(
            ChargeItemDefinition,
            facility=w.facility,
            slug=f"f-{w.facility.external_id}-tenofovir-charge",
            price_components=[
                {"amount": "100", "monetary_component_type": "base", "factor": None}
            ],
        )
        cls.product = product = baker.make(
            Product,
            facility=w.facility,
            product_knowledge=knowledge,
            charge_item_definition=definition,
        )
        cls.return_order = DeliveryOrder.objects.create(
            name=f"Medication return - {w.patient.name} {CANARY}",
            status="pending",
            destination=cls.store,
            patient=w.patient,
        )
        cls.dispense = DispenseOrder.objects.create(
            name=f"Dispense - {w.patient.name} {CANARY}",
            status="in_progress",
            note=f"tenofovir {CANARY}",
            location=cls.store,
            patient=w.patient,
            facility=w.facility,
        )
        cls.stock_order = DeliveryOrder.objects.create(
            name="Stock from supplier", status="pending", destination=cls.store
        )
        cls.deliveries = {}
        for key, order in (("return", cls.return_order), ("stock", cls.stock_order)):
            cls.deliveries[key] = SupplyDelivery.objects.create(
                status="in_progress",
                supplied_item=product,
                supplied_item_quantity=Decimal(1),
                supplied_item_condition="normal",
                delivery_type="product",
                order=order,
            )

    def answers(self, client):
        """Since P2-T0a CARE lists a store's patient returns only when asked
        (``patient_return=true``, under its own medication-return right) and supply deliveries
        only by their order (``?order=``): each list is asked both ways."""
        w = self.world
        orders = f"/api/v1/facility/{w.facility.external_id}/order/delivery/"
        store = {"destination": str(self.store.external_id)}
        deliveries = "/api/v1/supply_delivery/"
        return {
            "order list": client.get(orders, store),
            "return order list": client.get(
                orders, {**store, "patient_return": "true"}
            ),
            "order": client.get(f"{orders}{self.return_order.external_id}/"),
            "delivery list": client.get(
                deliveries, {"order": str(self.stock_order.external_id)}
            ),
            "return delivery list": client.get(
                deliveries, {"order": str(self.return_order.external_id)}
            ),
            "delivery": client.get(
                f"{deliveries}{self.deliveries['return'].external_id}/"
            ),
        }

    def assert_no_rows(self, response):
        """Refused, or an empty list: either way no row of the return."""
        if response.status_code == 200:
            self.assertEqual(response.json()["results"], [])
        else:
            self.assertIn(response.status_code, (403, 404), response.content[:300])

    def test_store_keeper_never_sees_a_patient_return(self):
        """§10 test 11 (Q-K43 A): the return order and its delivery are absent from every list,
        retrieve and batch answer of the Store Keeper; a Pharmacist sees them."""
        w = self.world
        keeper = self.answers(self.api("Store Keeper"))
        self.assertEqual(keeper["order list"].status_code, 200)
        self.assertEqual(
            [o["id"] for o in keeper["order list"].json()["results"]],
            [str(self.stock_order.external_id)],
        )
        self.assertEqual(keeper["delivery list"].status_code, 200)
        self.assertEqual(
            [d["id"] for d in keeper["delivery list"].json()["results"]],
            [str(self.deliveries["stock"].external_id)],
        )
        self.assert_no_rows(keeper["return order list"])
        self.assert_no_rows(keeper["return delivery list"])
        self.assertEqual(keeper["order"].status_code, 404)
        self.assertEqual(keeper["delivery"].status_code, 404)
        for name, response in keeper.items():
            with self.subTest(answer=name):
                self.assertNotIn(CANARY, response.content.decode())
                self.assertNotIn(w.patient.name, response.content.decode())
        # A batch write as the Store Keeper (on batch_allowlist.txt) does not reach it either
        url = f"/api/v1/supply_delivery/{self.deliveries['return'].external_id}/"
        with identity.fake_keycloak():
            client = self.signed_in("Store Keeper")
            response = client.post(
                "/api/v1/batch_requests/",
                {
                    "requests": [
                        {
                            "url": url,
                            "method": "PUT",
                            "body": {"status": "completed"},
                            "reference_id": "a",
                        }
                    ]
                },
                format="json",
            )
        [result] = response.json()["results"]
        self.assertEqual(result["status_code"], 404, result)
        self.assertNotIn("not allowed in a batch", json.dumps(result))
        self.assertNotIn(CANARY, response.content.decode())
        self.deliveries["return"].refresh_from_db()
        self.assertEqual(self.deliveries["return"].status, "in_progress")
        # CARE's upsert finds a row by its id without the list's filter: not this one
        upsert = self.api("Store Keeper").post(
            f"/api/v1/facility/{w.facility.external_id}/order/delivery/upsert/",
            {
                "datapoints": [
                    {
                        "id": str(self.return_order.external_id),
                        "name": "renamed",
                        "status": "completed",
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(upsert.status_code, 400, upsert.content[:300])
        self.assertEqual(upsert.json()[0]["errors"][0]["type"], "object_not_found")
        self.assertNotIn(CANARY, upsert.content.decode())
        self.return_order.refresh_from_db()
        self.assertIn(CANARY, self.return_order.name)
        # Nor can it make a patient-linked row, whose answer would name the patient (W5):
        # a return order for a patient, or a delivery against the return order
        orders = f"/api/v1/facility/{w.facility.external_id}/order/delivery/"
        made = self.api("Store Keeper").post(
            orders,
            {
                "name": "Return made by the store",
                "status": "draft",
                "destination": str(self.store.external_id),
                "patient": str(w.patient.external_id),
            },
            format="json",
        )
        self.assertEqual(made.status_code, 403, made.content[:300])
        # Our refusal, not only CARE's: since CARE 3.1 CARE also answers 403 here, so the
        # message proves the control ran (mutation access-delivery-create-patient, P2-T0a CI)
        self.assertIn(PATIENT_LINK_REFUSED, made.content.decode())
        self.assertNotIn(w.patient.name, made.content.decode())
        self.assertFalse(
            DeliveryOrder.objects.filter(name="Return made by the store").exists()
        )
        delivery = {
            "status": "in_progress",
            "supplied_item": str(self.product.external_id),
            "supplied_item_quantity": 1,
            "supplied_item_condition": "normal",
            "order": str(self.return_order.external_id),
        }
        before = SupplyDelivery.objects.count()
        for answer in (
            self.api("Store Keeper").post(
                "/api/v1/supply_delivery/", delivery, format="json"
            ),
            self.api("Store Keeper").post(
                "/api/v1/supply_delivery/upsert/",
                {"datapoints": [delivery]},
                format="json",
            ),
        ):
            self.assertIn(answer.status_code, (400, 404), answer.content[:300])
            self.assertNotIn(CANARY, answer.content.decode())
            self.assertNotIn(w.patient.name, answer.content.decode())
        self.assertEqual(SupplyDelivery.objects.count(), before)
        # The Pharmacist handles returns (CARE's own path)
        pharmacist = self.answers(self.api(self.pharmacist))
        self.assertEqual(
            [o["id"] for o in pharmacist["return order list"].json()["results"]],
            [str(self.return_order.external_id)],
        )
        self.assertEqual(
            [d["id"] for d in pharmacist["return delivery list"].json()["results"]],
            [str(self.deliveries["return"].external_id)],
        )
        self.assertEqual(pharmacist["order"].status_code, 200)
        self.assertIn(CANARY, pharmacist["order"].content.decode())
        self.assertEqual(pharmacist["delivery"].status_code, 200)
        self.assertEqual(connection.schema_name, connection.tenant.schema_name)
        self.assertTrue(
            FacilityOrganizationUser.objects.filter(user=self.pharmacist).exists()
        )

    def test_store_keeper_never_sees_a_dispense_order(self):
        """Review R2 (Q-K43 A): CARE opens dispense orders, each naming its patient, to anyone
        holding the store's supply-delivery permissions at the location. The Store Keeper, who
        shares the store with the Pharmacist, gets an empty list and 403 on retrieve, create
        and upsert; every restricted role likewise; the Pharmacist sees the order."""
        w = self.world
        base = f"/api/v1/facility/{w.facility.external_id}/order/dispense/"
        store = {"location": str(self.store.external_id)}
        one = f"{base}{self.dispense.external_id}/"
        body = {
            "status": "draft",
            "name": "Dispense by the store",
            "patient": str(w.patient.external_id),
            "location": str(self.store.external_id),
        }
        for role in EVERY_RESTRICTED:
            client = self.api(role)
            with self.subTest(role=role):
                for listing in (store, {**store, "include_children": "true"}):
                    listed = client.get(base, listing)
                    self.assertEqual(listed.status_code, 200, listed.content[:300])
                    self.assertEqual(listed.json()["results"], [])
                answers = (
                    client.get(one),
                    client.post(base, body, format="json"),
                    client.post(
                        f"{base}upsert/",
                        {
                            "datapoints": [
                                {
                                    "id": str(self.dispense.external_id),
                                    "status": "completed",
                                }
                            ]
                        },
                        format="json",
                    ),
                )
                for response in answers:
                    self.assertEqual(response.status_code, 403, response.content[:300])
                    self.assertNotIn(CANARY, response.content.decode())
                    self.assertNotIn(w.patient.name, response.content.decode())
        self.assertFalse(DispenseOrder.objects.filter(name=body["name"]).exists())
        self.dispense.refresh_from_db()
        self.assertEqual(self.dispense.status, "in_progress")
        pharmacist = self.api(self.pharmacist).get(one)
        self.assertEqual(pharmacist.status_code, 200, pharmacist.content[:300])
        self.assertIn(w.patient.name, pharmacist.content.decode())

    def test_every_supply_delivery_route_is_swapped(self):
        """Review R2: a CARE viewset that authorizes through the store's supply-delivery
        permissions (which the Store Keeper holds) and so may show a patient-linked row must be
        one of our swaps; a new one fails here until it is reviewed."""
        import inspect
        import re

        from config import api_router
        from ihr_platform import route_swaps
        from rest_framework.routers import BaseRouter

        care_of = {ours: care for care, ours in route_swaps.swapped.items()}
        found = set()
        for router in vars(api_router).values():
            if not isinstance(router, BaseRouter):
                continue
            for _prefix, viewset, _basename in router.registry:
                care = care_of.get(viewset, viewset)
                source = "".join(
                    inspect.getsource(cls)
                    for cls in care.__mro__
                    if cls.__module__.startswith("care.")
                )
                if re.search(r"can_(list|write)_facility_supply_delivery", source):
                    found.add(care)
        self.assertEqual(len(found), 3, sorted(c.__name__ for c in found))
        self.assertEqual(
            sorted(c.__name__ for c in found - set(route_swaps.swapped)), []
        )


class MembershipOnlyLists(SwapCase):
    """ "After the attack" §7.4: every CARE viewset that reads memberships is reviewed in
    ihr_access/membership_only.txt (membership alone, a permission, or our swap)."""

    def test_membership_only_lists_are_reviewed(self):
        import inspect
        import re
        from pathlib import Path

        from config import api_router
        from ihr_platform import route_swaps
        from rest_framework.routers import BaseRouter

        from ihr_access import guard

        reviewed = guard.read_list(
            Path(guard.__file__).with_name("membership_only.txt"),
            verbs=frozenset({"membership", "permission", "swapped"}),
        )
        care_of = {ours: care for care, ours in route_swaps.swapped.items()}
        found = set()
        for router in vars(api_router).values():
            if not isinstance(router, BaseRouter):
                continue
            for _prefix, viewset, _basename in router.registry:
                care = care_of.get(viewset, viewset)
                source = "".join(
                    inspect.getsource(cls)
                    for cls in care.__mro__
                    if cls.__module__.startswith("care.")
                    and cls.__module__ != "care.emr.api.viewsets.base"
                )
                if re.search(r"\b(Facility)?OrganizationUser\b", source):
                    found.add(f"{care.__module__}.{care.__qualname__}")
        self.assertEqual(sorted(found - set(reviewed)), [], "not reviewed")
        self.assertEqual(
            sorted(set(reviewed) - found), [], "no longer read memberships"
        )
        swapped = {route_swaps._dotted(care) for care in route_swaps.swapped}
        for name, (verb, _reason) in reviewed.items():
            with self.subTest(viewset=name):
                self.assertEqual(verb == "swapped", name in swapped)
