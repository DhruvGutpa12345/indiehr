"""Cross-tenant isolation suite (TENANCY_DESIGN.md §11.2, task P1-T1b).

Two tenants, Alpha (A) and Beta (B), are each seeded with CARE's full demo data. Every test tries
to see or reach one tenant's data from the other. Record ids are UUIDs that are random per
tenant, and names carry a per-tenant canary word, so either one turning up in the other tenant's
response is a leak. Most tests also check a positive control (the same call works for the owner),
so they cannot pass just because nothing answered.
"""

import re
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from django_tenants.utils import schema_context
from freezegun import freeze_time
from ihr_identity import testing
from ihr_identity.authentication import StaffSessionAuthentication
from ihr_identity.blocked import Blocked
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from ihr_platform.models import Hospital
from ihr_platform.test_runner import (
    TEST_SCHEMA,
    WRITTEN_REDIS_KEYS,
    unprefixed_redis_keys,
)
from ihr_platform.testing import as_owner
from ihr_platform.tests.isolation.routes import (
    CONVERTER_RE,
    SHARED_VALUE_PARAMS,
    fill,
    get_routes,
)
from ihr_platform.tests.isolation.world import UUID_RE, build_world

HERE = Path(__file__).parent
ANONYMOUS_ALLOW_LIST = HERE / "anonymous_allowlist.txt"
CRAWL_SKIPS = HERE / "crawl_skips.txt"
# A crawl as one tenant's admin must get real data back from at least this many routes.
# Observed 132-134 per crawl once extras.py seeded its rows (P1-T16b, 2026-11-13; 78-80 before),
# and 134 on the first crawl, 135 on each later one in four runs once world.py fixed the first
# facility public (review R1). It stays a floor: CARE's fixtures still pick some values at random.
MIN_ROUTES_WITH_OWN_DATA = 120
# Routes with path() converters, filled by routes.fill(); each must answer its owner. fill()
# picks a converter's value by its name alone, so the owner test also requires that these are
# exactly the GET converter routes fill() fills: a new one fails until it is reviewed here.
CONVERTER_ROUTES = (
    "api/ihr/staff/<str:username>/access/",
    "api/ihr/audit/entries/<int:pk>/",
    "api/ihr/pincode/<str:pincode>/",
)
# Detail routes of the rows extras.py seeds for the crawl (P1-T16b), per tenant.
SEEDED_ROUTES = 31
# Filled for nobody: POST-only (staff_urls.py), so the GET crawl never requests it.
POST_ONLY_ROUTE = "api/ihr/staff/<str:username>/<slug:action>/"
# Its value is this tenant's own audit entry of a Patient UPDATE (world.py, _collect_ids).
AUDIT_ENTRY_ROUTE = "api/ihr/audit/entries/<int:pk>/"
BATCH_URL = "/api/v1/batch_requests/"
# Open anonymously in every run: world.py makes the facility fill() puts in it public.
PUBLIC_FACILITY_ROUTE = "/api/v1/^getallfacilities/(?P<external_id>[^/.]+)/$"
# Open anonymously in every run: extras.py gives the first user, whom fill() puts in it, a
# public schedule at that facility (P1-T20a)
PUBLIC_DOCTOR_ROUTE = (
    "/api/v1/^facility/(?P<facility_external_id>[^/.]+)/schedulable_users/"
    "(?P<external_id>[^/.]+)/$"
)
# What anyone may see of a doctor, and of a booking through the patient-OTP routes (P1-T20a):
# written out here, not taken from public_routes, so a change there must change the test too
DOCTOR_KEYS = {
    "id",
    "first_name",
    "last_name",
    "prefix",
    "suffix",
    "profile_picture_url",
}
# A location or healthcare service a booking is for (review S1)
RESOURCE_KEYS = {
    "practitioner": DOCTOR_KEYS,
    "location": {
        "id",
        "name",
        "description",
        "location_type",
        "form",
        "status",
        "mode",
    },
    "healthcare_service": {"id", "name", "service_type", "internal_type"},
}
BOOKING_KEYS = {
    "id",
    "token_slot",
    "booked_on",
    "status",
    "note",
    "resource_type",
    "resource",
    "facility",
    "patient",
    "created_date",
    "modified_date",
    "token",
}


def _reviewed_list(path):
    return {
        line.split("#")[0].strip()
        for line in path.read_text().splitlines()
        if line.split("#")[0].strip()
    }


def _body(response):
    """The lower-cased body; downloads (audit export, files) are streamed, read once."""
    if not hasattr(response, "ihr_body"):
        raw = (
            b"".join(response.streaming_content)
            if response.streaming
            else response.content
        )
        response.ihr_body = raw.decode(errors="replace").lower()
    return response.ihr_body


def _leaks(response, other):
    """Ids and canary of tenant ``other`` found in a response body."""
    body = _body(response)
    found = set(UUID_RE.findall(body)) & other.ids
    if other.canary.lower() in body:
        found.add(other.canary)
    return found


def _owns(response, tenant):
    body = _body(response)
    return bool(set(UUID_RE.findall(body)) & tenant.ids)


NOT_SIGNED_IN = b"Authentication credentials were not provided."


def _rows(model, obj):
    """``obj``'s row through the plain manager: CARE's default one hides soft-deleted rows."""
    return model._base_manager.filter(pk=obj.pk)


# The crawl signs in as each tenant's CARE superuser so every route has data to show; real
# sign-ins refuse superusers (ADR-009), which ihr_identity's own tests check.
@override_settings(IHR_STAFF_REFUSE_SUPERUSERS=False)
class CrossTenantIsolationTests(SimpleTestCase):
    maxDiff = None
    databases = {"default"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Class clean-ups run last-in first-out, also when setUpClass fails part-way: each
        # write to public registers its own removal as it is made (world.py).
        cls.addClassCleanup(connection.set_schema, TEST_SCHEMA)
        cls.a, cls.b = build_world(cls.addClassCleanup)
        cls.routes = get_routes()
        cls._keycloak = testing.fake_keycloak()
        cls._keycloak.__enter__()
        cls.addClassCleanup(cls._keycloak.__exit__, None, None, None)

    @classmethod
    def tearDownClass(cls):
        connection.set_schema(TEST_SCHEMA)
        super().tearDownClass()

    def tearDown(self):
        connection.set_schema(TEST_SCHEMA)
        super().tearDown()

    def _staff_client(self, tenant, **claims):
        """A real staff session (not force_authenticate), so every view's own auth runs."""
        return testing.staff_client(
            tenant.hospital, tenant.domain, tenant.admin, **claims
        )

    # --- API crawl -------------------------------------------------------------------------

    def _crawl(self, viewer, other):
        """Every GET route as ``viewer``'s admin on its host, filled with its own ids."""
        client = self._staff_client(viewer)

        statuses, skipped, leaks, errors, own = Counter(), set(), [], [], 0
        refused = []  # staff routes that refused the staff token
        for route, cls, allows_get in self.routes:
            if (
                not allows_get or cls is Blocked
            ):  # switched off; ihr_identity tests them
                continue
            path = fill(route, cls, viewer)
            if path is None:
                skipped.add("/" + route)
                continue
            response = client.get(path)
            statuses[response.status_code] += 1
            if response.status_code >= 500:
                errors.append(path)
            staff_route = StaffSessionAuthentication in getattr(
                cls, "authentication_classes", ()
            )
            if (
                response.status_code in (401, 403)
                and NOT_SIGNED_IN in response.content
                and staff_route
            ):
                refused.append(path)
            if found := _leaks(response, other):
                leaks.append(f"{path} -> {sorted(found)[:3]}")
            if response.status_code < 300 and _owns(response, viewer):
                own += 1
        print(
            f"\n[isolation] crawl as {viewer.hospital.slug}: statuses {dict(statuses)}, "
            f"{own} with own data, {len(skipped)} skipped"
        )
        return refused, skipped, leaks, errors, own

    def test_crawl_never_shows_the_other_tenants_data(self):
        """Crawl A, then B, then A again (caches warm from the other tenant each time)."""
        for viewer, other in ((self.a, self.b), (self.b, self.a), (self.a, self.b)):
            with self.subTest(viewer=viewer.hospital.slug):
                refused, skipped, leaks, errors, own = self._crawl(viewer, other)
                self.assertEqual(leaks, [], "other tenant's data in responses")
                self.assertEqual(errors, [], "server errors can hide leaks")
                self.assertEqual(refused, [], "staff token refused on its own host")
                self.assertGreaterEqual(own, MIN_ROUTES_WITH_OWN_DATA)
                self.assertEqual(
                    sorted(skipped - _reviewed_list(CRAWL_SKIPS)),
                    [],
                    "routes skipped by the crawl but not in crawl_skips.txt",
                )
                if stale := sorted(_reviewed_list(CRAWL_SKIPS) - skipped):
                    print(f"\n[isolation] crawl_skips.txt entries now crawled: {stale}")

    def test_other_tenants_record_ids_are_not_reachable(self):
        """B's host, with A's ids: for the record only, and for every id in the path."""
        client = self._staff_client(self.b)
        checked, answered, leaks = 0, [], []
        for route, cls, allows_get in self.routes:
            if not allows_get or "(?P<external_id>" not in route:
                continue
            if any(f"(?P<{name}>" in route for name in SHARED_VALUE_PARAMS):
                continue
            paths = {
                fill(route, cls, self.b, external_id_from=self.a),
                fill(route, cls, self.b, ids_from=self.a),
            } - {None}
            for path in paths:
                response = client.get(path)
                checked += 1
                if response.status_code < 300:
                    answered.append(f"{path} -> {response.status_code}")
                if found := _leaks(response, self.a):
                    leaks.append(f"{path} -> {sorted(found)[:3]}")
        print(f"\n[isolation] cross-id probes: {checked} requests with A's ids on B")
        self.assertGreater(checked, 40)
        self.assertEqual(leaks, [], "A records reachable from B by id")
        self.assertEqual(answered, [], "B answered 2xx for A's record ids")

    def test_anonymous_access_matches_reviewed_allow_list(self):
        """On A's host with no login, only reviewed routes may answer 2xx (fail-closed)."""
        client = APIClient(HTTP_HOST=self.a.domain, raise_request_exception=False)
        open_routes = set()
        for route, cls, allows_get in self.routes:
            if not allows_get:
                continue
            path = fill(route, cls, self.a)
            if path is None:
                continue
            if client.get(path).status_code < 300:
                open_routes.add("/" + route)
        allowed = _reviewed_list(ANONYMOUS_ALLOW_LIST)
        self.assertEqual(
            sorted(open_routes - allowed),
            [],
            "Anonymous 2xx on routes not in anonymous_allowlist.txt",
        )
        # The public facility's detail: open every run (world.py fixes the facility public).
        for route in (PUBLIC_FACILITY_ROUTE, PUBLIC_DOCTOR_ROUTE):
            self.assertIn(route, allowed)
            self.assertIn(route, open_routes)
        # Some entries are open only for certain data, so entries that did not answer are
        # reported, not failed.
        if stale := sorted(allowed - open_routes):
            print(f"\n[isolation] allow-listed but not open this run: {stale}")

    def test_every_seeded_and_converter_route_answers_its_owner(self):
        """The crawl counts a filled route that answers 403 or 404 as crawled, with nothing
        seen. So each converter route, and each route seeded for the crawl, must answer its own
        tenant's admin with 200; the PIN lookup must name that tenant's own organisations."""
        routes = {route: (cls, allows_get) for route, cls, allows_get in self.routes}
        with self.subTest(route=POST_ONLY_ROUTE):
            self.assertIn(POST_ONLY_ROUTE, routes)
            self.assertFalse(routes[POST_ONLY_ROUTE][1], "must not be crawled by GET")
        for tenant, other in ((self.a, self.b), (self.b, self.a)):
            client = self._staff_client(tenant)
            with self.subTest(tenant=tenant.hospital.slug, route="converter routes"):
                filled = {
                    route
                    for route, (cls, allows_get) in routes.items()
                    if allows_get
                    and CONVERTER_RE.search(route)
                    and fill(route, cls, tenant)
                }
                self.assertEqual(
                    sorted(filled),
                    sorted(CONVERTER_ROUTES),
                    "a converter route filled by name and not reviewed in CONVERTER_ROUTES",
                )
            for route in CONVERTER_ROUTES:
                with self.subTest(tenant=tenant.hospital.slug, route=route):
                    self.assertIn(route, routes)
                    path = fill(route, routes[route][0], tenant)
                    self.assertIsNotNone(path, "converter route not filled")
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200, response.content[:300])
                    self.assertEqual(_leaks(response, other), set())
                    if (
                        route == AUDIT_ENTRY_ROUTE
                    ):  # an entry of its own, not a look-alike
                        self.assertTrue(
                            _owns(response, tenant)
                            or tenant.canary.lower() in _body(response),
                            "no own id or canary in the audit entry",
                        )
            with self.subTest(tenant=tenant.hospital.slug, route="pincode places"):
                response = client.get(f"/api/ihr/pincode/{tenant.pincode}/")
                self.assertEqual(response.status_code, 200, response.content[:300])
                orgs = {
                    place[key]
                    for place in response.json()["places"]
                    for key in ("state_organization", "geo_organization")
                }
                self.assertTrue(orgs)
                self.assertNotIn(None, orgs, "no organisation for the PIN's place")
                self.assertEqual(orgs - tenant.ids, set(), "not this tenant's own")
                self.assertEqual(orgs & other.ids, set())
            # the crawl's own paths: a seeded row must be the one fill() puts in the path
            crawled = {
                fill(route, cls, tenant)
                for route, (cls, allows_get) in routes.items()
                if allows_get
            }
            with self.subTest(tenant=tenant.hospital.slug, route="seeded rows"):
                paths = {path for path, _ in tenant.seeded_routes}
                self.assertEqual(len(paths), SEEDED_ROUTES)
            # _owns proves the route is reached with its owner's row (each seeded detail
            # carries its own id). It does not prove where the canary is rendered: the canary
            # is also in staff and patient names nested in most bodies. So where extras.py put
            # it in a field the read spec renders (``canary_field``), that field itself must
            # hold it.
            for path, canary_field in tenant.seeded_routes:
                with self.subTest(tenant=tenant.hospital.slug, seeded=path):
                    self.assertIn(path, crawled, "not the path the crawl requests")
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200, response.content[:300])
                    self.assertTrue(
                        _owns(response, tenant)
                        or tenant.canary.lower() in _body(response),
                        "no own id or canary in the body",
                    )
                    if canary_field:
                        rendered = str(response.json().get(canary_field)).lower()
                        self.assertIn(
                            tenant.canary.lower(),
                            rendered,
                            f"canary not rendered in {canary_field!r}",
                        )

    # --- tokens ----------------------------------------------------------------------------

    def test_staff_session_from_a_is_rejected_on_b(self):
        me = "/api/v1/users/getcurrentuser/"
        client = self._staff_client(self.a)
        self.assertEqual(client.get(me).status_code, 200)  # positive control
        # 1. A's session cookie, presented at B's address.
        cookie = client.cookies[settings.IHR_SESSION_COOKIE].value
        at_b = APIClient(HTTP_HOST=self.b.domain, HTTP_X_IHR_CSRF="1")
        at_b.cookies[settings.IHR_SESSION_COOKIE] = cookie
        self.assertEqual(at_b.get(me).status_code, 403)
        # 2. A session at B holding a token Keycloak issued for A's client.
        a_client = f"ihr-h-{self.a.hospital.slug}"
        for_a = {
            "azp": a_client,
            "aud": a_client,
            "resource_access": {a_client: {"roles": ["member"]}},
            "ihr_org": {
                "alias": self.a.hospital.slug,
                "id": str(self.a.hospital.keycloak_org_id),
            },
        }
        self.assertEqual(self._staff_client(self.b, **for_a).get(me).status_code, 403)
        # 3. A token for B's client that names A's organisation.
        a_org = {
            "alias": self.b.hospital.slug,
            "id": str(self.a.hospital.keycloak_org_id),
        }
        self.assertEqual(
            self._staff_client(self.b, ihr_org=a_org).get(me).status_code, 403
        )
        # 4. CARE's own JWT, which no longer opens anything.
        for tenant in (self.a, self.b):
            with schema_context(tenant.schema):
                token = str(RefreshToken.for_user(tenant.admin).access_token)
            care = APIClient(HTTP_HOST=tenant.domain)
            care.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            self.assertEqual(care.get(me).status_code, 403, tenant.hospital.slug)

    def test_patient_otp_token_from_a_is_rejected_on_b(self):
        """Same phone number in both tenants: A's patient-login token must not open B."""
        from care.emr.models import Patient
        from config.patient_otp_token import PatientToken

        with schema_context(self.a.schema):
            phone = Patient.objects.exclude(phone_number="").first().phone_number
            token = PatientToken()
            token["phone_number"] = phone
            token = str(token)
        with schema_context(self.b.schema):
            patient = Patient.objects.exclude(phone_number="").first()
            patient.phone_number = phone
            patient.save(update_fields=["phone_number"])

        own = APIClient(HTTP_HOST=self.a.domain)
        own.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = own.get("/api/v1/otp/patient/")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertTrue(_owns(response, self.a), "positive control: A sees its patient")

        client = APIClient(HTTP_HOST=self.b.domain)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = client.get("/api/v1/otp/patient/")
        self.assertIn(response.status_code, (401, 403), response.content[:300])

    # --- process-wide caches ---------------------------------------------------------------

    def test_patient_identifier_config_cache_is_per_tenant(self):
        from care.emr.signals.patient.base import BasePatientIdentifierConfig
        from care.facility.models import Facility

        subclasses = BasePatientIdentifierConfig.__subclasses__()
        self.assertTrue(subclasses, "no identifier config classes found")
        config_cls = subclasses[0]
        with schema_context(self.a.schema):
            a_facility = Facility.objects.order_by("id").first()
            a_config = config_cls.get_or_create_system_name_identifier_config(
                a_facility
            )
        with schema_context(self.b.schema):
            # same integer id, other tenant (ids line up on purpose, see world.py)
            b_facility = Facility.objects.get(id=a_facility.id)
            b_config = config_cls.get_or_create_system_name_identifier_config(
                b_facility
            )
            self.assertEqual(b_config.facility_id, b_facility.id)
            self.assertNotEqual(str(b_config.external_id), str(a_config.external_id))
            self.assertTrue(
                type(b_config)
                .objects.filter(external_id=b_config.external_id)
                .exists(),
                "B was handed a config object that does not exist in B",
            )

    def test_identifier_config_lookup_cache_is_per_tenant(self):
        """PatientIdentifierConfigCache: same config id in both tenants, each sees its own."""
        from care.emr.models.patient import (
            PatientIdentifierConfig,
            PatientIdentifierConfigCache,
        )

        def make(tenant, external_id=None):
            with schema_context(tenant.schema):
                kwargs = {"external_id": external_id} if external_id else {}
                return PatientIdentifierConfig.objects.create(
                    status="active",
                    config={"display": f"{tenant.canary} probe", "system": "probe"},
                    **kwargs,
                )

        a_config = make(self.a)
        make(self.b, external_id=a_config.external_id)
        try:
            with schema_context(self.a.schema):
                seen_by_a = str(
                    PatientIdentifierConfigCache.get_config(a_config.external_id)
                )
            with schema_context(self.b.schema):
                seen_by_b = str(
                    PatientIdentifierConfigCache.get_config(a_config.external_id)
                )
            self.assertIn(self.a.canary, seen_by_a)
            self.assertIn(self.b.canary, seen_by_b)
        finally:
            PatientIdentifierConfigCache.clear_cache()
            for tenant in (self.a, self.b):
                with schema_context(tenant.schema):
                    PatientIdentifierConfig.objects.filter(
                        external_id=a_config.external_id
                    ).delete()

    def test_raw_redis_recent_views_are_per_tenant(self):
        """CARE's recent-views list talks to Redis directly (no Django cache key function)."""
        from care.emr.models.valueset import RecentViewsManager

        key = "ihr-isolation-probe"
        with schema_context(self.a.schema):
            RecentViewsManager.add_recent_view(
                key, {"code": "A-ONLY", "system": "probe"}
            )
            self.assertEqual(
                [v["code"] for v in RecentViewsManager.get_recent_views(key)],
                ["A-ONLY"],
            )
        with schema_context(self.b.schema):
            self.assertEqual(RecentViewsManager.get_recent_views(key), [])

    # --- background jobs ------------------------------------------------------------------

    def test_celery_tasks_run_in_the_senders_tenant(self):
        """Through tenant-schemas-celery's real prerun/postrun hooks (eager in tests)."""
        from config.celery_app import app
        from tenant_schemas_celery.app import CeleryApp
        from tenant_schemas_celery.task import TenantTask

        self.assertIsInstance(app, CeleryApp)
        not_tenant_aware = [
            name
            for name, task in app.tasks.items()
            if not name.startswith("celery.") and not isinstance(task, TenantTask)
        ]
        self.assertEqual(not_tenant_aware, [])

        @app.task(name="ihr_platform.tests.isolation_probe")
        def probe():
            return connection.schema_name

        with schema_context(self.b.schema):
            self.assertEqual(probe.apply().get(), self.b.schema)
            sent_for_a = probe.apply(headers={"_schema_name": self.a.schema})
            self.assertEqual(sent_for_a.get(), self.a.schema)
            self.assertEqual(
                connection.schema_name, self.b.schema, "schema not restored"
            )

    def test_periodic_jobs_run_once_per_active_tenant(self):
        """Celery beat: CARE's periodic jobs go to every active hospital, never to public;
        a platform-wide job (the audit fingerprint) goes to public once and nowhere else."""
        from celery.beat import ScheduleEntry
        from config.celery_app import app

        self.assertEqual(
            settings.CELERY_BEAT_SCHEDULER, "ihr_platform.beat.ActiveTenantScheduler"
        )
        from care.emr.tasks import setup_periodic_tasks
        from ihr_audit.tasks import setup_periodic_tasks as setup_audit_tasks

        from ihr_platform.beat import PLATFORM_ONCE, ActiveTenantScheduler
        from ihr_platform.tasks import setup_periodic_tasks as setup_platform_tasks

        # CARE registers these from on_after_finalize; if an earlier test already finalized the
        # app, that hook never fired in this process. Registering again is idempotent (by name).
        setup_periodic_tasks(app)
        setup_audit_tasks(app)
        setup_platform_tasks(app)
        scheduler = ActiveTenantScheduler(app=app, lazy=True)
        entries = scheduler._tenant_aware_beat_schedule_to_dict(app.conf.beat_schedule)
        self.assertTrue(
            any("cleanup_expired_otps" in name for name in entries), entries
        )

        with schema_context("public"), as_owner():  # an operator's status change
            Hospital.objects.filter(pk=self.b.hospital.pk).update(
                status=Hospital.Status.SUSPENDED
            )
        try:
            sent = []
            with patch.object(
                scheduler,
                "apply_async",
                side_effect=lambda entry, **kw: sent.append(
                    (connection.schema_name, entry.name)
                )
                or MagicMock(id="x"),
            ):
                for name, fields in entries.items():
                    scheduler.apply_entry(ScheduleEntry(name=name, app=app, **fields))
        finally:
            with schema_context("public"), as_owner():
                Hospital.objects.filter(pk=self.b.hospital.pk).update(
                    status=Hospital.Status.ACTIVE
                )
        once = {
            name
            for name, fields in entries.items()
            if fields.get("options", {}).get("headers", {}).get(PLATFORM_ONCE)
        }
        # The audit fingerprint, and the per-minute report of hospitals not served (P1-T18a)
        self.assertEqual(
            once,
            {
                "ihr_audit_anchor@__all_tenants_only__",
                "ihr_fleet_unserved@__all_tenants_only__",
            },
        )
        self.assertEqual(
            sorted(s for s, name in sent if name in once), ["public"] * len(once), sent
        )
        per_schema = Counter(s for s, name in sent if name not in once)
        self.assertNotIn("public", per_schema)
        self.assertNotIn(self.b.schema, per_schema, "suspended hospital got jobs")
        self.assertEqual(per_schema[self.a.schema], len(entries) - len(once))

    # --- object storage --------------------------------------------------------------------

    def test_file_object_keys_carry_the_tenant(self):
        """Every S3 call site, for both tenants, gets t/<that tenant's uuid>/; public refuses."""
        from care.emr.utils.file_manager import S3FilesManager
        from care.utils.csp.config import BucketType
        from care.utils.file_uploads.cover_image import upload_cover_image

        keys = []

        def record(**params):
            if "Key" in params:
                keys.append(params["Key"])
            keys.extend(o["Key"] for o in params.get("Delete", {}).get("Objects", []))
            return MagicMock()

        fake_s3 = MagicMock()
        for method in ("put_object", "get_object", "delete_object", "delete_objects"):
            getattr(fake_s3, method).side_effect = record
        fake_s3.generate_presigned_url.side_effect = lambda *a, **kw: record(
            **kw.get("Params", {})
        )

        file_obj = SimpleNamespace(
            file_type="patient",
            internal_name="probe.pdf",
            name="probe",
            meta={},
            get_extension=lambda: ".pdf",
        )
        manager = S3FilesManager(BucketType.PATIENT)
        for tenant in (self.a, self.b):
            keys.clear()
            with (
                schema_context(tenant.schema),
                patch("boto3.client", return_value=fake_s3),
            ):
                manager.signed_url(file_obj)
                manager.read_signed_url(file_obj)
                manager.put_object(file_obj, b"x")
                manager.get_object(file_obj)
                manager.delete_object(file_obj)
                manager.delete_objects([file_obj])
                upload_cover_image(
                    SimpleUploadedFile("c.png", b"x"), "probe", "cover_images"
                )
                upload_cover_image(
                    SimpleUploadedFile("a.png", b"x"), "probe", "avatars"
                )
            self.assertEqual(len(keys), 8, keys)
            prefix = f"t/{tenant.hospital.uuid}/"
            self.assertEqual([k for k in keys if not k.startswith(prefix)], [])

        self.assertNotEqual(self.a.hospital.uuid, self.b.hospital.uuid)
        with schema_context("public"), self.assertRaises(ImproperlyConfigured):
            manager.signed_url(file_obj)

    # --- batch endpoint (allow-list since P1-T20a) --------------------------------------------

    def _entries(self, tenant, *, writes=False):
        """The number of audit entries in ``tenant``'s schema: any write anywhere adds one.
        ``writes``: row writes only, without the one entry per request that tried a change."""
        where = " WHERE kind = 'write'" if writes else ""
        with schema_context("public"), connection.cursor() as cursor:
            cursor.execute(
                f'SELECT count(*) FROM "{tenant.schema}".ihr_audit_entry{where}'  # noqa: S608
            )
            return cursor.fetchone()[0]

    def test_batch_requests_stay_in_the_callers_tenant(self):
        """On a route the web app batches (InvoiceChargeItemTitle.tsx: a dispense, GET)."""
        client = self._staff_client(self.b)
        own = self.b.first_id_by_model["emr.medicationdispense"]
        batch = {
            "requests": [
                {
                    "url": f"/api/v1/medication/dispense/{own}/",
                    "method": "GET",
                    "reference_id": "own",
                }
            ]
        }
        response = client.post(BATCH_URL, batch, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json()["results"][0]["status_code"], 200)
        self.assertIn(own, response.content.decode())
        self.assertEqual(_leaks(response, self.a), set())

        a_dispense = self.a.first_id_by_model["emr.medicationdispense"]
        foreign = {
            "requests": [
                {
                    "url": f"/api/v1/medication/dispense/{a_dispense}/",
                    "method": "GET",
                    "reference_id": "a-dispense",
                }
            ]
        }
        response = client.post(BATCH_URL, foreign, format="json")
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertEqual(response.json()["results"][0]["status_code"], 404)
        self.assertEqual(_leaks(response, self.a), set())

    def test_batch_refuses_unreviewed_sub_requests_and_runs_nothing(self):
        """One refused sub-request refuses the whole batch before anything runs: a reviewed
        write next to it is not made, nothing at all is written, and the refusal is one fixed
        line with no value from the request."""
        from care.emr.models.scheduling import TokenBooking

        from ihr_platform import batch

        client = self._staff_client(self.b)
        facility = self.b.first_id_by_model["facility.facility"]
        patient = self.b.first_id_by_model["emr.patient"]
        dispense = self.b.first_id_by_model["emr.medicationdispense"]
        with schema_context(self.b.schema):
            booking = (
                TokenBooking.objects.filter(
                    status="booked",
                    token_slot__resource__facility__external_id=facility,
                )
                .order_by("pk")
                .first()
            )
            self.assertIsNotNone(booking, "no booked appointment in the fixtures")
            note = booking.note
        appointment = f"/api/v1/facility/{facility}/appointments/{booking.external_id}/"
        # A reviewed write (useEncounterProgressController.ts: scheduleApi.appointments.update)
        write = {
            "url": appointment,
            "method": "PUT",
            "body": {"status": "booked", "note": f"{self.b.canary} batch probe"},
            "reference_id": "write",
        }
        dispense_url = f"/api/v1/medication/dispense/{dispense}/"
        refused = {
            "unlisted route": ("GET", "/api/v1/users/", {}, "not_listed"),
            "unlisted method on a listed route": (
                "DELETE",
                f"/api/v1/patient/{patient}/",
                {},
                "not_listed",
            ),
            "another route behind a listed template": (
                "GET",
                "/api/v1/medication/dispense/summary/",
                {},
                "not_listed",
            ),
            "patient-OTP route": (
                "GET",
                "/api/v1/otp/slots/get_appointments/",
                {},
                "not_listed",
            ),
            "blocked route": ("POST", "/api/v1/auth/login/", {}, "not_listed"),
            "nested batch": ("POST", BATCH_URL, {"requests": [write]}, "nested"),
            "encoded slash": (
                "PUT",
                f"/api/v1/patient/{patient}%2Fconsent%2F{patient}/",
                {},
                "bad_url",
            ),
            "path parameter": ("PUT", f"/api/v1/patient/{patient};x=1/", {}, "bad_url"),
            "dot segments": ("GET", f"{dispense_url}../../../users/", {}, "bad_url"),
            "query string": ("GET", f"{dispense_url}?limit=1", {}, "bad_url"),
            "GET with a body (a query)": (
                "GET",
                dispense_url,
                {"limit": "1"},
                "bad_url",
            ),
            "absolute URL": (
                "GET",
                f"http://evil.example{dispense_url}",
                {},
                "bad_url",
            ),
            "other host": ("GET", f"//evil.example{dispense_url}", {}, "bad_url"),
            "lower-case method": ("put", appointment, {}, "bad_method"),
            "HEAD": ("HEAD", dispense_url, {}, "bad_method"),
            "OPTIONS": ("OPTIONS", dispense_url, {}, "bad_method"),
            "a request-factory attribute": ("generic", dispense_url, {}, "bad_method"),
        }
        # The session's first request writes its own sign-in rows: before the counting
        self.assertEqual(client.get("/api/v1/users/getcurrentuser/").status_code, 200)
        line_re = (
            r"^ihr-batch: refused hospital=iso-beta reason=(?P<reason>[a-z_]+) "
            r"method=(?:GET|POST|PUT|PATCH|DELETE|other) route=[A-Za-z0-9_.:-]+$"
        )
        for case, (method, url, body, reason) in refused.items():
            with self.subTest(case=case):
                sub = {"url": url, "method": method, "body": body, "reference_id": "no"}
                before = self._entries(self.b, writes=True)
                with (
                    patch.object(
                        batch, "execute_serially", wraps=batch.execute_serially
                    ) as ran,
                    patch.object(batch, "_emit") as emitted,
                ):
                    response = client.post(
                        BATCH_URL, {"requests": [write, sub]}, format="json"
                    )
                self.assertEqual(response.status_code, 400, response.content[:300])
                self.assertEqual(
                    response.json(),
                    {
                        "results": [
                            {
                                "reference_id": "no",
                                "status_code": 403,
                                "data": batch.REFUSED,
                            }
                        ]
                    },
                )
                ran.assert_not_called()
                self.assertEqual(
                    self._entries(self.b, writes=True), before, "the batch wrote"
                )
                [line] = [c.args[0] for c in emitted.call_args_list]
                self.assertRegex(line, line_re)
                self.assertEqual(re.match(line_re, line)["reason"], reason)
                for value in (patient, dispense, str(booking.external_id), "evil"):
                    self.assertNotIn(value, line)
        with schema_context(self.b.schema):
            self.assertEqual(TokenBooking.objects.get(pk=booking.pk).note, note)
        # Positive control: the same write alone runs (so it was the refusal that stopped it)
        response = client.post(BATCH_URL, {"requests": [write]}, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        with schema_context(self.b.schema):
            self.assertEqual(
                TokenBooking.objects.get(pk=booking.pk).note, write["body"]["note"]
            )

    # --- anonymous and patient-OTP routes (P1-T20a) -------------------------------------------

    def _probe_practitioner(self, facility):
        """A staff member of A not yet scheduled at ``facility``, given a private schedule."""
        from datetime import timedelta

        from care.emr.models.scheduling import SchedulableResource, Schedule
        from care.users.models import User
        from django.utils import timezone

        with schema_context(self.a.schema):
            scheduled = SchedulableResource.objects.filter(facility=facility).values(
                "user_id"
            )
            probe = (
                User.objects.exclude(id__in=scheduled)
                .filter(is_active=True, deleted=False)
                .order_by("pk")
                .first()
            )
            resource = SchedulableResource.objects.create(
                facility=facility, resource_type="practitioner", user=probe
            )
            now = timezone.now()
            schedule = Schedule.objects.create(
                resource=resource,
                name="probe",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=7),
                is_public=False,
            )
        return probe, resource, schedule, now

    def test_public_doctor_list_shows_only_name_title_and_photo(self):
        """Exact keys on list and detail; only a public facility, only a practitioner with a
        public schedule that has not ended (by IST day, as CARE's slot generator: its last
        day still counts, review C1), only active staff (each one hidden from both)."""
        from care.emr.models.scheduling import SchedulableResource, Schedule
        from care.facility.models import Facility
        from care.users.models import User

        anonymous = APIClient(HTTP_HOST=self.a.domain, raise_request_exception=False)
        facility_id = self.a.first_id_by_model["facility.facility"]
        base = f"/api/v1/facility/{facility_id}/schedulable_users/"
        doctor = self.a.first_id_by_model["users.user"]  # extras.py: a public schedule

        def listed():
            response = anonymous.get(base)
            self.assertEqual(response.status_code, 200, response.content[:300])
            for item in response.json()["results"]:
                self.assertEqual(set(item), DOCTOR_KEYS)
            return {item["id"] for item in response.json()["results"]}

        def shown(user_id):
            response = anonymous.get(f"{base}{user_id}/")
            if response.status_code == 200:
                self.assertEqual(set(response.json()), DOCTOR_KEYS)
                return True
            self.assertEqual(response.status_code, 404, response.content[:300])
            return False

        self.assertIn(doctor, listed())
        self.assertTrue(shown(doctor))
        with schema_context(self.a.schema):
            facility = Facility.objects.get(external_id=facility_id)
            staff = User.objects.get(external_id=doctor)
            body = anonymous.get(base).content.decode()
            for value in {staff.username, staff.phone_number, staff.email} - {""}:
                self.assertNotIn(value, body)
        probe, resource, schedule, now = self._probe_practitioner(facility)
        probe_id = str(probe.external_id)
        # The web app sends valid_to as a date: stored as 00:00 IST of the schedule's last day
        midnight = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
        cases = (
            ("only a private schedule", {"is_public": False}, None, False),
            ("public and current", {"is_public": True}, None, True),
            ("its last day, 15:00 IST", {"valid_to": midnight}, None, True),
            (
                "ended yesterday",
                {"valid_to": midnight - timedelta(days=1)},
                None,
                False,
            ),
            (
                "deleted",
                {"valid_to": now + timedelta(days=7), "deleted": True},
                None,
                False,
            ),
            ("public again", {"deleted": False}, None, True),
            ("staff switched off", {}, {"is_active": False}, False),
            ("staff deleted", {}, {"is_active": True, "deleted": True}, False),
            ("staff back", {}, {"deleted": False}, True),
        )
        try:
            for case, schedule_change, user_change, visible in cases:
                with (
                    self.subTest(case=case),
                    schema_context(self.a.schema),
                    freeze_time(midnight + timedelta(hours=15)),
                ):
                    if schedule_change:
                        _rows(Schedule, schedule).update(**schedule_change)
                    if user_change:
                        _rows(User, probe).update(**user_change)
                    self.assertEqual(probe_id in listed(), visible)
                    self.assertEqual(shown(probe_id), visible)
            with schema_context(self.a.schema):
                Facility.objects.filter(pk=facility.pk).update(is_public=False)
            with self.subTest(case="facility not public"):
                self.assertEqual(listed(), set())
                self.assertFalse(shown(doctor))
        finally:
            with schema_context(self.a.schema):
                Facility.objects.filter(pk=facility.pk).update(is_public=True)
                _rows(User, probe).update(is_active=True, deleted=False)
                _rows(Schedule, schedule).delete()
                _rows(SchedulableResource, resource).delete()

    def _assert_public_booking(self, booking):
        self.assertEqual(set(booking), BOOKING_KEYS)
        slot = booking["token_slot"]
        self.assertEqual(
            set(slot),
            {"id", "availability", "start_datetime", "end_datetime", "allocated"},
        )
        self.assertEqual(
            set(slot["availability"]), {"id", "name", "tokens_per_slot", "schedule"}
        )
        self.assertEqual(set(slot["availability"]["schedule"]), {"id", "name"})
        self.assertEqual(
            set(booking["resource"]), RESOURCE_KEYS[booking["resource_type"]]
        )
        self.assertEqual(set(booking["facility"]), {"id", "name"})
        # the patient's own record, as CARE's OTP view shows it to the phone's owner
        self.assertEqual(
            set(booking["patient"]),
            {
                "id",
                "name",
                "gender",
                "phone_number",
                "emergency_phone_number",
                "address",
                "pincode",
                "date_of_birth",
                "year_of_birth",
                "geo_organization",
                "blood_group",
                "extensions",
            },
        )
        if booking["token"] is not None:
            self.assertEqual(set(booking["token"]), {"number", "status", "category"})
            self.assertEqual(
                set(booking["token"]["category"]), {"id", "name", "shorthand"}
            )

    def test_patient_otp_bookings_show_no_staff_records(self):
        """List (with a booking front-desk staff made), create and cancel: the exact keys at
        every level, and nothing of the staff who booked or of the doctor beyond the public
        projection."""
        from care.emr.models.scheduling import TokenBooking, TokenSlot
        from config.patient_otp_token import PatientToken
        from django.db.models import F
        from django.utils import timezone

        with schema_context(self.a.schema):
            staff_booking = (
                TokenBooking.objects.filter(booked_by__isnull=False)
                .exclude(patient__phone_number="")
                .select_related("patient", "booked_by", "token_slot__resource__user")
                .order_by("pk")
                .first()
            )
            self.assertIsNotNone(
                staff_booking, "no booking made by staff in the fixtures"
            )
            patient = staff_booking.patient
            people = (staff_booking.booked_by, staff_booking.token_slot.resource.user)
            secret_values = {
                value
                for person in people
                for value in (person.username, person.phone_number, person.email)
                if value
            }
            slot = (
                TokenSlot.objects.filter(
                    availability__schedule__is_public=True,
                    start_datetime__gt=timezone.now(),
                    allocated__lt=F("availability__tokens_per_slot"),
                )
                .exclude(
                    id__in=TokenBooking.objects.filter(patient=patient).values(
                        "token_slot_id"
                    )
                )
                .order_by("start_datetime")
                .first()
            )
            self.assertIsNotNone(slot, "no free public slot in the fixtures")
            token = PatientToken()
            token["phone_number"] = patient.phone_number
            token = str(token)
        client = APIClient(HTTP_HOST=self.a.domain)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        def check(response):
            self.assertEqual(response.status_code, 200, response.content[:300])
            body = response.content.decode()
            for value in secret_values:
                self.assertNotIn(value, body)
            return response.json()

        listed = check(client.get("/api/v1/otp/slots/get_appointments/"))["results"]
        self.assertIn(str(staff_booking.external_id), {b["id"] for b in listed})
        for booking in listed:
            self._assert_public_booking(booking)

        created = check(
            client.post(
                f"/api/v1/otp/slots/{slot.external_id}/create_appointment/",
                {"patient": str(patient.external_id), "note": "otp probe"},
                format="json",
            )
        )
        self._assert_public_booking(created)
        cancelled = check(
            client.post(
                "/api/v1/otp/slots/cancel_appointment/",
                {"patient": str(patient.external_id), "appointment": created["id"]},
                format="json",
            )
        )
        self._assert_public_booking(cancelled)
        self.assertEqual(cancelled["status"], "cancelled")

    def _probe_rooms(self, keep, facility, encounter):
        """A theatre in a block, both holding ``encounter`` (another patient's), the theatre's
        parent copy cold; and a healthcare service."""
        from care.emr.models.healthcare_service import HealthcareService
        from care.emr.models.location import FacilityLocation

        room = {
            "facility": facility,
            "description": "probe",
            "status": "active",
            "operational_status": "O",
            "system_availability_status": "available",
            "current_encounter": encounter,
        }
        block = keep(
            FacilityLocation, name="probe block", mode="kind", form="wa", **room
        )
        theatre = keep(
            FacilityLocation,
            name="probe theatre",
            mode="instance",
            form="ro",
            parent=block,
            **room,
        )
        _rows(FacilityLocation, theatre).update(cached_parent_json={})
        service = keep(
            HealthcareService,
            facility=facility,
            name="probe service",
            extra_details="",
            internal_type="scheduling",
        )
        return theatre, service

    def _book_as_staff(self, keep, kind, target, patient, staff):
        """A booking ``staff`` made for ``patient`` on a new, non-public schedule of
        ``target`` (a ``kind`` resource); its id."""
        from datetime import timedelta

        from care.emr.models.scheduling import (
            Availability,
            SchedulableResource,
            Schedule,
            TokenBooking,
            TokenSlot,
        )
        from django.utils import timezone

        now = timezone.now()
        resource = keep(
            SchedulableResource,
            facility=target.facility,
            resource_type=kind,
            **{kind: target},
        )
        schedule = keep(
            Schedule,
            resource=resource,
            name="probe",
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=7),
            is_public=False,
        )
        availability = keep(
            Availability,
            schedule=schedule,
            name="probe",
            slot_type="appointment",
            slot_size_in_minutes=10,
            tokens_per_slot=1,
        )
        slot = keep(
            TokenSlot,
            resource=resource,
            availability=availability,
            start_datetime=now + timedelta(days=1),
            end_datetime=now + timedelta(days=1, minutes=10),
            allocated=1,
        )
        booking = keep(
            TokenBooking,
            token_slot=slot,
            patient=patient,
            booked_by=staff,
            status="booked",
            note="probe",
        )
        return str(booking.external_id)

    def test_patient_otp_bookings_of_a_location_or_service_show_its_name_only(self):
        """Bookings staff made on a non-public location and healthcare-service schedule
        (review S1): the room and its parent each hold another patient's encounter, the room's
        parent copy is cold. The OTP list shows the exact keys at every level, nothing of the
        other patient or the care team, and the GET writes nothing (CARE's serialiser saves the
        parent copy, an audited write)."""
        from care.emr.models import Encounter
        from care.emr.models.patient import Patient
        from care.facility.models import Facility
        from care.users.models import User
        from config.patient_otp_token import PatientToken

        made = []  # (model, row), removed in reverse order

        def keep(model, **values):
            row = model.objects.create(**values)
            made.append((model, row))
            return row

        try:
            with schema_context(self.a.schema):
                facility = Facility.objects.get(
                    external_id=self.a.first_id_by_model["facility.facility"]
                )
                patient = (
                    Patient.objects.exclude(phone_number="").order_by("pk").first()
                )
                encounter = (
                    Encounter.objects.exclude(
                        patient__phone_number=patient.phone_number
                    )
                    .exclude(patient__name=patient.name)
                    .select_related("patient")
                    .order_by("pk")
                    .first()
                )
                self.assertIsNotNone(encounter, "no other patient's encounter")
                staff = User.objects.get(username=self.a.username)
                secret_values = {
                    encounter.patient.name,
                    encounter.patient.phone_number,
                    str(encounter.external_id),
                    staff.username,
                } - {""}
                theatre, service = self._probe_rooms(keep, facility, encounter)
                bookings = {
                    self._book_as_staff(keep, kind, target, patient, staff): kind
                    for kind, target in (
                        ("location", theatre),
                        ("healthcare_service", service),
                    )
                }
                token = PatientToken()
                token["phone_number"] = patient.phone_number
            client = APIClient(HTTP_HOST=self.a.domain)
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            before = self._entries(self.a, writes=True)
            response = client.get("/api/v1/otp/slots/get_appointments/")
            self.assertEqual(response.status_code, 200, response.content[:300])
            for value in secret_values:
                self.assertNotIn(value, response.content.decode())
            listed = {b["id"]: b for b in response.json()["results"]}
            for booking_id, kind in bookings.items():
                with self.subTest(resource_type=kind):
                    self.assertEqual(listed[booking_id]["resource_type"], kind)
                    self._assert_public_booking(listed[booking_id])
            self.assertEqual(
                self._entries(self.a, writes=True), before, "the OTP GET wrote a row"
            )
            with schema_context(self.a.schema):
                theatre.refresh_from_db()
                self.assertEqual(theatre.cached_parent_json, {})
        finally:
            with schema_context(self.a.schema):
                for model, row in reversed(made):
                    _rows(model, row).delete()

    def test_public_organisations_are_government_only(self):
        """The anonymous address picker: no team or role organisation by filter or by id."""
        from care.emr.models.organization import Organization

        anonymous = APIClient(HTTP_HOST=self.a.domain, raise_request_exception=False)
        with schema_context(self.a.schema):
            probes = [
                Organization.objects.create(
                    name=f"{self.a.canary} {kind} probe", org_type=kind
                )
                for kind in ("team", "role")
            ]
            govt = {
                str(i)
                for i in Organization.objects.filter(org_type="govt").values_list(
                    "external_id", flat=True
                )
            }
        try:
            self.assertTrue(govt)
            response = anonymous.get("/api/v1/govt/organization/?limit=1000")
            self.assertEqual(response.status_code, 200, response.content[:300])
            shown = {o["id"] for o in response.json()["results"]}
            self.assertTrue(shown)
            self.assertEqual(
                shown - govt, set(), "a non-government organisation listed"
            )
            for org_type in ("team", "role", "product_supplier", "TEAM"):
                with self.subTest(org_type=org_type):
                    response = anonymous.get(
                        f"/api/v1/govt/organization/?org_type={org_type}"
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["results"], [])
            for probe in probes:
                with self.subTest(detail=probe.org_type):
                    response = anonymous.get(
                        f"/api/v1/govt/organization/{probe.external_id}/"
                    )
                    self.assertEqual(response.status_code, 404, response.content[:300])
            response = anonymous.get(f"/api/v1/govt/organization/{sorted(govt)[0]}/")
            self.assertEqual(response.status_code, 200)  # positive control
        finally:
            with schema_context(self.a.schema):
                Organization.objects.filter(pk__in=[p.pk for p in probes]).delete()

    def test_public_facility_finder_drops_raw_key_field(self):
        """The duplicate raw-key field only (review C5): the key itself is still inside the
        unsigned read_cover_image_url until P1-T20b signs it, and that test is P1-T20b's."""
        anonymous = APIClient(HTTP_HOST=self.a.domain, raise_request_exception=False)
        facility = self.a.first_id_by_model["facility.facility"]
        listed = anonymous.get("/api/v1/getallfacilities/")
        detail = anonymous.get(f"/api/v1/getallfacilities/{facility}/")
        for response in (listed, detail):
            self.assertEqual(response.status_code, 200, response.content[:300])
        for item in [*listed.json()["results"], detail.json()]:
            self.assertNotIn("cover_image_url", item)
            self.assertIn("read_cover_image_url", item)

    def test_anonymous_gets_write_no_audit_entry(self):
        """Every anonymous data route, with every organisation's parent copy expired so CARE
        saves it again on read: the copy is refreshed, and no audit entry is written."""
        from care.emr.models.organization import Organization
        from care.facility.models import Facility

        anonymous = APIClient(HTTP_HOST=self.a.domain, raise_request_exception=False)
        facility_id = self.a.first_id_by_model["facility.facility"]
        doctor = self.a.first_id_by_model["users.user"]
        with schema_context(self.a.schema):
            child = (
                Organization.objects.filter(org_type="govt", parent__isnull=False)
                .select_related("parent")
                .order_by("pk")
                .first()
            )
            self.assertIsNotNone(child, "no government organisation with a parent")
            Facility.objects.filter(external_id=facility_id).update(
                geo_organization=child
            )
            Organization.objects.update(cached_parent_json={})
        paths = (
            "/api/v1/govt/organization/",
            f"/api/v1/govt/organization/?parent={child.parent.external_id}",
            f"/api/v1/govt/organization/{child.external_id}/",
            "/api/v1/getallfacilities/",
            f"/api/v1/getallfacilities/{facility_id}/",
            f"/api/v1/facility/{facility_id}/schedulable_users/",
            f"/api/v1/facility/{facility_id}/schedulable_users/{doctor}/",
            "/api/v1/plug_config/",
            "/api/ihr/bootstrap/",
        )
        before = self._entries(self.a)
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(anonymous.get(path).status_code, 200)
        self.assertEqual(self._entries(self.a), before, "an anonymous GET was audited")
        with schema_context(self.a.schema):
            child.refresh_from_db()
            self.assertTrue(child.cached_parent_json, "the save path did not run")

    def test_care_routes_reach_our_classes(self):
        """Every route of the swapped viewsets, in every form, reaches our class (A4)."""
        from ihr_platform import route_swaps

        self.assertEqual(route_swaps.check(), [])
        originals = set(route_swaps.swapped)
        self.assertEqual(len(originals), len(route_swaps.SWAPS))
        served = [route for route, cls, _ in self.routes if cls in originals]
        self.assertEqual(served, [], "a route still serves CARE's own class")

    # --- the release gate (P1-T18a) ---------------------------------------------------------

    def test_gated_hospital_shows_nothing(self):
        """B held for a release: every route on B's host answers 503 to B's own admin and to
        nobody signed in, except bootstrap.js, a stub from public data; nothing of B's in any
        answer, no audit entry written in B, and A is still served."""
        from ihr_platform import release

        def entries():
            with connection.cursor() as cursor:
                table = f'"{self.b.schema}".ihr_audit_entry'
                cursor.execute(f"SELECT count(*) FROM {table}")  # noqa: S608
                return cursor.fetchone()[0]

        admin = self._staff_client(self.b)  # signed in before the window opened
        anonymous = APIClient(HTTP_HOST=self.b.domain, raise_request_exception=False)
        with schema_context("public"):
            before = entries()
            with as_owner():  # ihr_fleet_run hold: the owner-run release
                release.hold([self.b.hospital.pk])
        try:
            answers, leaks, crawled = Counter(), [], 0
            for route, cls, allows_get in self.routes:
                if not allows_get:
                    continue
                path = fill(route, cls, self.b)
                if path is None:
                    continue
                for who, client in (("admin", admin), ("anonymous", anonymous)):
                    response = client.get(path)
                    crawled += 1
                    answers[response.status_code] += 1
                    # the one exception: the web app's first script, a public-data stub
                    expected = 200 if path == "/api/ihr/bootstrap.js" else 503
                    if response.status_code != expected:
                        leaks.append(f"{who} {path} -> {response.status_code}")
                    if found := _leaks(response, self.b):
                        leaks.append(f"{who} {path} -> {sorted(found)[:3]}")
            changed = admin.post("/api/v1/patient/", {}, format="json")
            stub = anonymous.get("/api/ihr/bootstrap.js")
            a_me = self._staff_client(self.a).get("/api/v1/users/getcurrentuser/")
            with schema_context("public"):
                after = entries()
        finally:
            with schema_context("public"), as_owner():
                release.release_hold([self.b.hospital.pk])
        print(f"\n[isolation] gated B: {crawled} requests, statuses {dict(answers)}")
        self.assertGreater(crawled, 200)
        self.assertEqual(leaks, [], "a held hospital answered or showed its data")
        self.assertEqual(changed.status_code, 503)
        self.assertEqual(stub.status_code, 200)
        self.assertIn(b"__IHR_UPGRADING__", stub.content)
        self.assertEqual(_leaks(stub, self.b), set())
        self.assertEqual(after, before, "a gated request wrote B's audit trail")
        self.assertEqual(a_me.status_code, 200)  # the gate is per hospital
        self.assertEqual(_leaks(a_me, self.b), set())

    # --- runs last: everything above has populated the cache --------------------------------

    def test_zz_redis_keyspace_is_tenant_prefixed(self):
        """Early signal; the runner repeats this audit after the whole run (test_runner.py)."""
        bad = unprefixed_redis_keys()
        print(
            f"\n[isolation] Redis: {len(WRITTEN_REDIS_KEYS)} keys written, unprefixed: {len(bad)}"
        )
        self.assertGreater(len(WRITTEN_REDIS_KEYS), 0, "write recorder saw nothing")
        self.assertEqual(bad[:30], [], "Redis keys without a tenant prefix")
