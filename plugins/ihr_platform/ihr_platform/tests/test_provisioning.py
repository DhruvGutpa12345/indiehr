"""The provisioning engine (P1-T5a): steps, resume, roll-back, input checks.

Each new schema costs about a minute of migrations, so these tests build two hospitals in
total: one taken all the way live through three injected failures, one rolled back.
Keycloak is faked here; keycloak/tests/test_live.py covers the real calls.
"""

import uuid
from types import SimpleNamespace
from unittest import mock

from django.core import mail
from django.db import connection
from django.test import SimpleTestCase
from django_tenants.utils import schema_context, schema_exists

from ihr_platform.models import Domain, Hospital, RetiredAddress, TenantOperation
from ihr_platform.provisioning import runner, steps
from ihr_platform.test_runner import TEST_SCHEMA


def form(slug, **changes):
    values = {
        "slug": slug,
        "name": f"{slug.title()} Health Pvt Ltd",
        "display_name": f"{slug.title()} Hospital",
        "state_code": "27",
        "district": "Pune",
        "city": "Pune",
        "pin_code": "411001",
        "phone": "+912000000099",
        "address": "1 Test Road, Pune",
        "facility_name": f"{slug.title()} Hospital",
        "admin_email": f"first.admin@{slug}.example.test",
        "admin_first_name": "First",
        "admin_last_name": "Admin",
    }
    return steps.OnboardingForm(**(values | changes))


class FakeKeycloak:
    """Records what provisioning asks of Keycloak."""

    def __init__(self):
        self.ensured, self.removed = [], []
        self.org_id = str(uuid.uuid4())

    def ensure(self, admin, login, org_id):
        self.ensured.append((login.slug, login.enabled, login.uuid, org_id))
        return SimpleNamespace(org_id=self.org_id)

    def remove(self, admin, login):
        self.removed.append((login.slug, login.uuid))


class ProvisioningTests(SimpleTestCase):
    databases = {"default"}

    def setUp(self):
        connection.set_schema_to_public()
        self.kc = FakeKeycloak()
        patches = [
            mock.patch(
                "ihr_platform.keycloak.hospital.ensure_hospital", self.kc.ensure
            ),
            mock.patch(
                "ihr_platform.keycloak.hospital.remove_hospital", self.kc.remove
            ),
            mock.patch(
                "ihr_platform.management.commands.ihr_keycloak_sync.keycloak_admin",
                lambda: object(),
            ),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        connection.set_schema(TEST_SCHEMA)  # where every other test expects to be

    def _drop(self, slug):
        """Remove a test hospital entirely (also a live one, which roll-back refuses)."""
        connection.set_schema_to_public()
        for hospital in Hospital.objects.filter(slug=slug):
            Domain.objects.filter(tenant=hospital).delete()
            hospital.delete(force_drop=True)
        RetiredAddress.objects.filter(value__startswith=slug).delete()
        TenantOperation.objects.filter(slug=slug).delete()
        connection.set_schema(TEST_SCHEMA)

    def _fail_once(self, name):
        original = dict(steps.STEPS)[name]
        calls = {"n": 0}

        def flaky(op, form):
            calls["n"] += 1
            if calls["n"] == 1:
                original(op, form) if name != "keycloak" else None
                msg = f"injected failure in {name}"
                raise RuntimeError(msg)
            return original(op, form)

        index = steps.STEP_NAMES.index(name)
        return mock.patch.object(
            steps,
            "STEPS",
            [*steps.STEPS[:index], (name, flaky), *steps.STEPS[index + 1 :]],
        )

    # --- the whole way live, through failures ---------------------------------------------

    def test_provisioning_survives_failures_and_goes_live(self):
        self.addCleanup(self._drop, "prov-one")
        op = runner.start(form("prov-one"), "test")
        for name in ("geography", "facility", "keycloak"):
            with self._fail_once(name), self.assertRaisesMessage(RuntimeError, name):
                runner.run(op)
            op.refresh_from_db()
            self.assertEqual(op.status, "failed")
            failed = [s for s in op.steps if s["status"] == "failed"]
            self.assertEqual([s["name"] for s in failed], [name])
            self.assertIn(f"injected failure in {name}", failed[0]["error"])
        runner.run(op)
        op.refresh_from_db()
        self.assertEqual(op.status, "succeeded")
        self.assertTrue(all(s["status"] == "succeeded" for s in op.steps))
        self.assertTrue(all("seconds" in s for s in op.steps))

        hospital = Hospital.objects.get(slug="prov-one")
        self.assertEqual(hospital.status, Hospital.Status.ACTIVE)
        self.assertIsNotNone(hospital.activated_at)
        self.assertEqual(str(hospital.keycloak_org_id), self.kc.org_id)
        self.assertEqual(
            list(
                Domain.objects.filter(tenant=hospital).values_list("domain", flat=True)
            ),
            ["prov-one.localhost"],
        )
        # Keycloak: the client stays off until the last step, then is turned on.
        enabled = [e for slug, e, tag, _ in self.kc.ensured if slug == "prov-one"]
        self.assertEqual(enabled[-1], True)
        self.assertTrue(all(e is False for e in enabled[:-1]))
        self.assertTrue(
            all(tag == str(hospital.uuid) for _, _, tag, _ in self.kc.ensured)
        )

        counts = self._contents(hospital, op)
        self.assertEqual(
            mail.outbox, []
        )  # CARE's "set your password" e-mail never sent

        # Running every step again changes nothing.
        op.steps = [dict(s, status="pending") for s in op.steps]
        op.status = "failed"
        op.save()
        runner.run(op)
        self.assertEqual(self._contents(hospital, op), counts)

        # A live hospital is never rolled back (that is offboarding).
        with self.assertRaisesMessage(runner.ProvisioningError, "has been live"):
            runner.rollback(op, "test")
        self.assertTrue(schema_exists(hospital.schema_name))

    def _contents(self, hospital, op):
        from care.emr.models.organization import Organization, OrganizationUser
        from care.emr.resources.facility.spec import FacilityReadSpec
        from care.facility.models import Facility, FacilityOrganizationUser
        from care.security.models import RoleModel
        from care.users.models import User
        from ihr_identity.models import StaffIdentity
        from ihr_tenant_settings.models import HospitalProfile

        with schema_context(hospital.schema_name):
            profile = HospitalProfile.current()
            self.assertEqual(
                (profile.display_name, profile.state_code, profile.pin_code),
                ("Prov-One Hospital", "27", "411001"),
            )
            state, district = (
                Organization.objects.get(pk=op.result["state_org_id"]),
                Organization.objects.get(pk=op.result["district_org_id"]),
            )
            self.assertEqual((state.name, state.parent), ("Maharashtra", None))
            self.assertEqual((district.name, district.parent), ("Pune", state))
            admin = User.objects.get(pk=op.result["admin_user_id"])
            self.assertFalse(
                admin.is_active
            )  # active only once the invitation is accepted
            self.assertFalse(admin.has_usable_password())
            self.assertFalse(StaffIdentity.objects.filter(user=admin).exists())
            self.assertTrue(
                OrganizationUser.objects.filter(
                    user=admin, organization=state, role__name="Administrator"
                ).exists()
            )
            facility = Facility.objects.get(pk=op.result["facility_id"])
            root = facility.default_internal_organization
            self.assertTrue(
                FacilityOrganizationUser.objects.filter(
                    organization=root, user=admin, role__name="Facility Admin"
                ).exists()
            )
            FacilityReadSpec.serialize(
                facility
            ).to_json()  # reads back through CARE's spec
            return (
                Organization.objects.count(),
                OrganizationUser.objects.count(),
                Facility.objects.count(),
                User.objects.get_entire_queryset().count(),
                RoleModel.objects.count(),
            )

    # --- roll-back ----------------------------------------------------------------------

    def test_rollback_removes_a_hospital_that_never_went_live(self):
        self.addCleanup(self._drop, "prov-two")
        op = runner.start(form("prov-two"), "test")
        with self._fail_once("keycloak"), self.assertRaises(RuntimeError):
            runner.run(op)
        op.refresh_from_db()
        hospital = Hospital.objects.get(slug="prov-two")
        schema = hospital.schema_name
        self.assertTrue(schema_exists(schema))

        # An operation whose record names another hospital is refused.
        real = op.result
        TenantOperation.objects.filter(pk=op.pk).update(
            result=dict(real, hospital_uuid=str(uuid.uuid4()))
        )
        with self.assertRaisesMessage(runner.ProvisioningError, "does not match"):
            runner.rollback(op, "test")
        self.assertTrue(schema_exists(schema))
        self.assertFalse(TenantOperation.objects.filter(kind="rollback").exists())
        TenantOperation.objects.filter(pk=op.pk).update(result=real)
        op.refresh_from_db()

        runner.rollback(op, "test")
        self.assertFalse(schema_exists(schema))
        self.assertFalse(Hospital.objects.filter(slug="prov-two").exists())
        self.assertFalse(Domain.objects.filter(domain="prov-two.localhost").exists())
        self.assertEqual(self.kc.removed, [("prov-two", op.result["hospital_uuid"])])
        # Never served anyone: its slug and address may be used again (owner's choice).
        self.assertFalse(
            RetiredAddress.objects.filter(value__startswith="prov-two").exists()
        )
        op.refresh_from_db()
        self.assertEqual(op.status, "rolled_back")
        self.assertTrue(
            TenantOperation.objects.filter(kind="rollback", slug="prov-two").exists()
        )
        steps.OnboardingForm.validate(form("prov-two"))  # the slug is free again

    def test_deleting_a_live_hospital_still_retires_its_addresses(self):
        """The release is only for roll-backs, marked on the very instances deleted."""
        hospital = Hospital(slug="prov-live", name="Live", status="active")
        hospital.auto_create_schema = False
        hospital.save()
        Domain.objects.create(domain="prov-live.localhost", tenant=hospital)
        self.addCleanup(self._drop, "prov-live")
        Domain.objects.filter(tenant=hospital).delete()
        hospital.delete()
        retired = set(RetiredAddress.objects.values_list("value", flat=True))
        self.assertTrue({"prov-live", "prov-live.localhost"} <= retired)

    # --- input and concurrency ----------------------------------------------------------

    def test_bad_input_is_refused_before_anything_is_created(self):
        for changes, problem in (
            ({"slug": "auth"}, "slug"),
            ({"slug": "Bad_Slug"}, "slug"),
            ({"state_code": "99"}, "state_code"),
            ({"pin_code": "011001"}, "pin_code"),
            ({"admin_email": "not-an-email"}, "admin_email"),
            ({"facility_type": "Spaceport"}, "facility_type"),
            ({"phone": "12345"}, "phone"),
            ({"district": " "}, "district"),
        ):
            slug = changes.pop("slug", "prov-bad")
            with (
                self.subTest(problem=problem),
                self.assertRaisesMessage(steps.StepError, problem),
            ):
                runner.start(form(slug, **changes), "test")
        RetiredAddress.objects.create(kind="slug", value="prov-retired")
        self.addCleanup(RetiredAddress.objects.filter(value="prov-retired").delete)
        with self.assertRaisesMessage(steps.StepError, "never reused"):
            runner.start(form("prov-retired"), "test")
        self.assertFalse(Hospital.objects.filter(slug__startswith="prov-").exists())

    def test_one_open_provisioning_per_address(self):
        from django.db import IntegrityError, transaction

        self.addCleanup(self._drop, "prov-three")
        runner.start(form("prov-three"), "test")
        with self.assertRaises(IntegrityError), transaction.atomic():
            runner.start(form("prov-three"), "test")

    def test_a_second_run_waits_for_the_first(self):
        """The lock holds for the whole run, also after the migrations (which close Django's
        main database connection)."""
        self.addCleanup(self._drop, "prov-four")
        op = runner.start(form("prov-four"), "test")
        seen = {}
        original = dict(steps.STEPS)["domains"]

        def domains_then_probe(op, form):
            original(op, form)
            try:
                with runner._exclusive("prov-four"):
                    seen["second run"] = "got in"
            except runner.ProvisioningError:
                seen["second run"] = "refused"

        index = steps.STEP_NAMES.index("domains")
        patched = [
            *steps.STEPS[:index],
            ("domains", domains_then_probe),
            *steps.STEPS[index + 1 :],
        ]
        with mock.patch.object(steps, "STEPS", patched):
            runner.run(op, stop_after="domains")
        self.assertEqual(seen, {"second run": "refused"})
        runner.rollback(op, "test")
        op.refresh_from_db()
        self.assertNotIn("admin_email", op.input)  # personal details dropped
        with self.assertRaisesMessage(runner.ProvisioningError, "rolled_back"):
            runner.run(op)
