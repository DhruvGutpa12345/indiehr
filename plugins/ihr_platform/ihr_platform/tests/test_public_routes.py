"""The route swap and the batch allow-list (P1-T20a). No database: the URL configuration and
the batch check only. The isolation suite tests both against two seeded hospitals."""

import hashlib
import importlib
import inspect
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from django.urls import Resolver404, resolve
from ihr_identity.blocked import Blocked

from ihr_platform import batch, public_routes, route_swaps

# CARE's batch code our view copies or relies on (ihr_platform/batch.py). A change upstream
# fails here: read the new code, carry it into IhrBatchRequestView._run or the check, then
# update the fingerprint (docs/UPSTREAM.md, after every sync).
CARE_CREATE_SHA256 = "800e91c818447dd4f0aac3dc21d94101211b05f946c38ef76230acc6d04a952f"
CARE_EXECUTOR_SHA256 = (
    "74197a185371df32ca6062a2d9f905f2d0331e55d64df9f25b0dc24610096f5f"
)
# CARE's three patient-OTP booking actions, whose bodies PublicOTPSlotViewSet repeats with its
# own booking serialiser (public_routes.py, review S1). A change upstream fails here: carry it
# over, then update the fingerprint.
CARE_OTP_ACTIONS_SHA256 = (
    "fd0541e32ad546d45c0373c4995f43521e6716b975548e69a8757ef1b53d8d26"
)


def _sub(url, method="GET", body=None):
    return SimpleNamespace(url=url, method=method, body=body or {}, reference_id="r")


def _wsgi(path_info, method="GET", **meta):
    return SimpleNamespace(path_info=path_info, method=method, META=meta)


class RouteSwapTests(SimpleTestCase):
    def test_care_router_serves_our_classes(self):
        self.assertEqual(route_swaps.check(), [])
        names = {route_swaps._dotted(ours) for ours in route_swaps.swapped.values()}
        self.assertEqual(names, set(route_swaps.SWAPS.values()))
        for original, ours in route_swaps.swapped.items():
            self.assertTrue(issubclass(ours, original))

    def test_every_form_of_a_swapped_route_reaches_ours(self):
        """DEBUG adds format suffixes (``users.json``); tests run with DEBUG off, so build
        CARE's DEBUG router from its registry and look at every route it makes."""
        from config import api_router
        from rest_framework.routers import DefaultRouter

        router = DefaultRouter()
        for prefix, viewset, basename in api_router.router.registry:
            router.register(prefix, viewset, basename)
        served = {
            getattr(url.callback, "cls", None) for url in router.urls if url.callback
        }
        self.assertEqual(served & set(route_swaps.swapped), set())
        for ours in route_swaps.swapped.values():
            if ours in {c for _p, c, _b in api_router.router.registry}:
                self.assertIn(ours, served)


class RouteSwapRefusalTests(SimpleTestCase):
    """A swap missed at run time (an upstream sync moved a class) stops every process that
    builds the URLs, and every command that runs the checks (review C4)."""

    problem = ["router: users/ still serves UserViewSet"]

    def test_an_unmatched_swap_is_reported(self):
        with patch.dict(route_swaps.SWAPS, {"care.moved.Elsewhere": "ihr_platform.X"}):
            self.assertTrue(any("never registered" in p for p in route_swaps.check()))

    def test_the_url_conf_refuses_to_load(self):
        from ihr_platform import urls_tenant

        try:
            with (
                patch.object(route_swaps, "check", return_value=self.problem),
                self.assertRaisesRegex(ImproperlyConfigured, "users/ still serves"),
            ):
                importlib.reload(urls_tenant)
        finally:
            importlib.reload(urls_tenant)

    def test_the_system_check_reports_it(self):
        self.assertEqual(route_swaps.system_check(), [])
        with patch.object(route_swaps, "check", return_value=self.problem):
            errors = route_swaps.system_check()
        self.assertEqual([e.id for e in errors], ["ihr_platform.E020"])

    def test_django_runs_the_system_check(self):
        """Through Django's own check registry, as ``manage.py check``, migrate and the
        start-up one-shot run it: the registration in apps.py is what makes them refuse."""
        from django.core.checks import run_checks

        def ours(errors):
            return [e.id for e in errors if e.id == "ihr_platform.E020"]

        self.assertEqual(ours(run_checks()), [])
        with patch.object(route_swaps, "check", return_value=self.problem):
            self.assertEqual(ours(run_checks()), ["ihr_platform.E020"])


class PublicBookingTests(SimpleTestCase):
    """The outer allow-list over a booking (``public_booking``), on CARE's own shape: what it
    keeps of each resource type, and {} for an unknown one (review S1)."""

    def booking(self, resource_type, resource):
        return {
            "id": "b",
            "token_slot": None,
            "resource_type": resource_type,
            "resource": resource,
            "booked_by": {"username": "front-desk"},
            "tags": [{"id": "t"}],
            "charge_item": {"id": "c"},
            "facility": {"id": "f", "name": "F", "phone_number": "+91"},
            "token": None,
        }

    def test_each_resource_type_keeps_its_allow_list(self):
        encounter = {
            "patient": {"name": "someone else"},
            "care_team": [{"username": "x"}],
        }
        location = {
            "id": "l",
            "name": "Theatre 1",
            "description": "",
            "location_type": {},
            "form": "ro",
            "status": "active",
            "mode": "instance",
            "current_encounter": encounter,
            "parent": {"id": "p", "current_encounter": encounter},
            "tags": [{"id": "t"}],
            "has_children": False,
        }
        service = {
            "id": "s",
            "name": "OPD",
            "service_type": {},
            "internal_type": "scheduling",
            "styling_metadata": {},
            "extra_details": "",
            "locations": [{"current_encounter": encounter}],
        }
        doctor = {
            "id": "u",
            "first_name": "A",
            "last_name": "B",
            "prefix": None,
            "suffix": None,
            "profile_picture_url": None,
            "username": "doctor",
            "phone_number": "+91",
        }
        cases = {
            "location": (location, set(public_routes.LOCATION_KEYS)),
            "healthcare_service": (service, set(public_routes.HEALTHCARE_SERVICE_KEYS)),
            "practitioner": (doctor, set(public_routes.PUBLIC_PRACTITIONER_KEYS)),
            "device": ({"id": "d", "name": "x", "owner": "y"}, set()),
        }
        for resource_type, (resource, keys) in cases.items():
            with self.subTest(resource_type=resource_type):
                out = public_routes.public_booking(
                    self.booking(resource_type, resource)
                )
                self.assertEqual(set(out), set(public_routes.BOOKING_KEYS))
                self.assertEqual(set(out["resource"]), keys)
                self.assertEqual(out["facility"], {"id": "f", "name": "F"})
                self.assertNotIn("someone else", str(out))
        self.assertEqual(
            set(public_routes.LOCATION_KEYS),
            {"id", "name", "description", "location_type", "form", "status", "mode"},
        )


class BatchAllowListTests(SimpleTestCase):
    def test_every_entry_resolves_to_a_view_that_takes_its_method(self):
        """Catches an entry an upstream sync made stale (scripts/batch-allowlist.py --check
        catches the other way round: a batched route that is not listed)."""
        entries = batch.load_allow_list()
        self.assertGreater(len(entries), 30)
        problems = []
        for method, template in sorted(entries):
            path = batch.sample_path(template)
            if not batch.template_regex(template).fullmatch(path):
                problems.append(
                    f"{method} {template}: template does not match its sample"
                )
            if template.startswith(("/api/v1/otp/", "/api/v1/batch_requests")):
                problems.append(f"{method} {template}: never in a staff batch")
            try:
                match = resolve(path)
            except Resolver404:
                problems.append(f"{method} {template}: no route")
                continue
            cls = getattr(match.func, "cls", None)
            actions = getattr(match.func, "actions", None)
            takes = (
                method.lower() in actions
                if actions is not None
                else cls is not None and hasattr(cls, method.lower())
            )
            if cls is None or cls is Blocked or not takes:
                problems.append(f"{method} {template}: {match.route} does not take it")
        self.assertEqual(problems, [])

    def test_the_list_is_well_formed(self):
        for method, template in batch.load_allow_list():
            self.assertIn(method, batch.METHODS)
            self.assertRegex(template, r"^/api/v1/[A-Za-z0-9_/{}-]+/$")


class BatchCheckTests(SimpleTestCase):
    """``batch.check`` on hand-made request objects: the parts a real batch cannot reach."""

    listed_url = "/api/v1/medication/dispense/8e7f8b9e-9dc7-4e1c-8d59-d476e7affbe7/"

    def test_a_listed_read_passes(self):
        self.assertIsNone(batch.check(_sub(self.listed_url), _wsgi(self.listed_url)))

    def test_a_method_override_header_is_refused(self):
        for header in ("HTTP_X_HTTP_METHOD_OVERRIDE", "HTTP_X_METHOD_OVERRIDE"):
            outcome = batch.check(
                _sub(self.listed_url), _wsgi(self.listed_url, **{header: "DELETE"})
            )
            self.assertEqual(outcome[0], "override")

    def test_judged_on_what_runs(self):
        """The decoded path and the method of the request object, not the raw text."""
        other = "/api/v1/users/"
        self.assertEqual(batch.check(_sub(self.listed_url), _wsgi(other))[0], "bad_url")
        self.assertEqual(
            batch.check(_sub(self.listed_url), _wsgi(self.listed_url, "PUT"))[0],
            "bad_method",
        )

    def test_the_refusal_line_has_no_request_values(self):
        line = batch.refusal_line("not_listed", "GET", "users-list")
        self.assertRegex(
            line,
            r"^ihr-batch: refused hospital=[a-z][a-z0-9-]{2,30} reason=not_listed "
            r"method=GET route=users-list$",
        )


class UpstreamDrift(SimpleTestCase):
    def test_care_batch_code_is_the_reviewed_one(self):
        from care.emr.api.viewsets.batch_request import BatchRequestView
        from care.emr.utils import batch_requests as care

        create = inspect.getsource(BatchRequestView.create).encode()
        executor = b"".join(
            inspect.getsource(f).encode()
            for f in (
                care.get_wsgi_request_object,
                care.construct_wsgi_from_data,
                care.get_response,
            )
        )
        self.assertEqual(hashlib.sha256(create).hexdigest(), CARE_CREATE_SHA256)
        self.assertEqual(hashlib.sha256(executor).hexdigest(), CARE_EXECUTOR_SHA256)

    def test_care_otp_booking_actions_are_the_reviewed_ones(self):
        from care.emr.api.otp_viewsets.slot import OTPSlotViewSet

        actions = b"".join(
            inspect.getsource(getattr(OTPSlotViewSet, name)).encode()
            for name in ("create_appointment", "cancel_appointment", "get_appointments")
        )
        self.assertEqual(hashlib.sha256(actions).hexdigest(), CARE_OTP_ACTIONS_SHA256)
