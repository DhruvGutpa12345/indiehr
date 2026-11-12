"""Cross-tenant isolation suite (TENANCY_DESIGN.md §11.2, task P1-T1b).

Two tenants, Alpha (A) and Beta (B), are each seeded with CARE's full demo data. Every test tries
to see or reach one tenant's data from the other. Record ids are UUIDs that are random per
tenant, and names carry a per-tenant canary word, so either one turning up in the other tenant's
response is a leak. Most tests also check a positive control (the same call works for the owner),
so they cannot pass just because nothing answered.
"""

from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django_tenants.utils import schema_context
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
# Open anonymously in every run: world.py makes the facility fill() puts in it public.
PUBLIC_FACILITY_ROUTE = "/api/v1/^getallfacilities/(?P<external_id>[^/.]+)/$"


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
        self.assertIn(PUBLIC_FACILITY_ROUTE, allowed)
        self.assertIn(PUBLIC_FACILITY_ROUTE, open_routes)
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

        # CARE registers these from on_after_finalize; if an earlier test already finalized the
        # app, that hook never fired in this process. Registering again is idempotent (by name).
        setup_periodic_tasks(app)
        setup_audit_tasks(app)
        scheduler = ActiveTenantScheduler(app=app, lazy=True)
        entries = scheduler._tenant_aware_beat_schedule_to_dict(app.conf.beat_schedule)
        self.assertTrue(
            any("cleanup_expired_otps" in name for name in entries), entries
        )

        with schema_context("public"):
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
            with schema_context("public"):
                Hospital.objects.filter(pk=self.b.hospital.pk).update(
                    status=Hospital.Status.ACTIVE
                )
        once = {
            name
            for name, fields in entries.items()
            if fields.get("options", {}).get("headers", {}).get(PLATFORM_ONCE)
        }
        self.assertEqual(once, {"ihr_audit_anchor@__all_tenants_only__"})
        self.assertEqual(
            sorted(s for s, name in sent if name in once), ["public"], sent
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

    # --- batch endpoint ---------------------------------------------------------------------

    def test_batch_requests_stay_in_the_callers_tenant(self):
        client = self._staff_client(self.b)
        own = {
            "requests": [
                {"url": "/api/v1/users/", "method": "GET", "reference_id": "users"},
                {"url": "/api/v1/facility/", "method": "GET", "reference_id": "fac"},
            ]
        }
        response = client.post("/api/v1/batch_requests/", own, format="json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertIn(str(self.b.admin.external_id), response.content.decode())
        self.assertEqual(_leaks(response, self.a), set())

        a_facility = self.a.first_id_by_model["facility.facility"]
        foreign = {
            "requests": [
                {
                    "url": f"/api/v1/facility/{a_facility}/",
                    "method": "GET",
                    "reference_id": "a-facility",
                }
            ]
        }
        response = client.post("/api/v1/batch_requests/", foreign, format="json")
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertEqual(response.json()["results"][0]["status_code"], 404)
        self.assertEqual(_leaks(response, self.a), set())

    # --- runs last: everything above has populated the cache --------------------------------

    def test_zz_redis_keyspace_is_tenant_prefixed(self):
        """Early signal; the runner repeats this audit after the whole run (test_runner.py)."""
        bad = unprefixed_redis_keys()
        print(
            f"\n[isolation] Redis: {len(WRITTEN_REDIS_KEYS)} keys written, unprefixed: {len(bad)}"
        )
        self.assertGreater(len(WRITTEN_REDIS_KEYS), 0, "write recorder saw nothing")
        self.assertEqual(bad[:30], [], "Redis keys without a tenant prefix")
