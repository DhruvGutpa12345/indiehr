"""PIN code directory, state and district geography, PIN lookup (task P1-T7a)."""

from pathlib import Path

from care.emr.models.organization import Organization
from care.users.models import User
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import SimpleTestCase
from django_tenants.utils import schema_context
from ihr_identity import testing
from ihr_identity.models import StaffIdentity
from ihr_platform import pincodes
from ihr_platform.models import Hospital, PinDirectoryLoad, PostOffice
from ihr_platform.test_runner import TEST_SCHEMA
from rest_framework.test import APIClient

from ihr_tenant_settings import geography

SAMPLE = Path(__file__).parent / "data" / "pincodes_sample.csv"
HEADER = "officename,pincode,officetype,delivery,district,statename\n"


class ParseTests(SimpleTestCase):
    def test_state_names_old_and_new(self):
        for name, code in (
            ("MAHARASHTRA", "27"),
            ("Andaman & Nicobar Islands", "35"),
            ("THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU", "26"),
            ("DAMAN & DIU", "26"),
            ("PONDICHERRY", "34"),
            ("ORISSA", "21"),
            ("Jammu & Kashmir", "01"),
            ("LADAKH", "38"),
        ):
            with self.subTest(name=name):
                self.assertEqual(pincodes.state_code(name), code)
        self.assertIsNone(pincodes.state_code("OTHER TERRITORY"))
        self.assertIsNone(pincodes.state_code("ATLANTIS"))

    def test_district_names(self):
        self.assertEqual(
            pincodes.district_name("NORTH 24  PARGANAS"), "North 24 Parganas"
        )
        self.assertEqual(pincodes.district_name("Y.S.R. KADAPA"), "Y.S.R. Kadapa")
        self.assertEqual(
            pincodes.district_name("MEDCHAL-MALKAJGIRI"), "Medchal-Malkajgiri"
        )
        self.assertEqual(pincodes.district_name("K.V.RANGAREDDY"), "K.V.Rangareddy")
        self.assertEqual(pincodes.district_name("BIJAPUR(KAR)"), "Bijapur(Kar)")

    def test_older_release_columns_encoding_and_repeats(self):
        data = (
            "officename,pincode,officeType,Deliverystatus,Districtname,statename,Latitude\n"
            "Pune H.O,411001,H.O,Non-Delivery,PUNE,MAHARASHTRA,18.5\n"
            "Pune H.O,411001,H.O,Non-Delivery,PUNE,MAHARASHTRA,NA\n"
            "Cr\u00e8che Road B.O,411002,B.O,Delivery,PUNE,MAHARASHTRA,NA\n"
        ).encode("cp1252")
        parsed = pincodes.parse(data)
        self.assertEqual(parsed.duplicates, 1)
        self.assertEqual(
            [(o["name"], o["delivery"], o["district"]) for o in parsed.offices],
            [("Pune H.O", False, "Pune"), ("Cr\u00e8che Road B.O", True, "Pune")],
        )

    def test_sample_file(self):
        parsed = pincodes.parse(SAMPLE.read_bytes())
        self.assertEqual(len(parsed.offices), 10)
        self.assertEqual(
            [r for _, r in parsed.skipped], ["PIN code '0110'", "no district"]
        )
        self.assertEqual(parsed.unknown_states, {})
        cantonment = parsed.offices[1]
        self.assertEqual(
            cantonment,
            {
                "pincode": "411001",
                "name": "Pune Cantonment S.O",
                "office_type": "S.O",
                "delivery": False,
                "district": "Pune",
                "state_code": "27",
            },
        )

    def test_not_the_directory(self):
        with self.assertRaisesMessage(ValueError, "missing column(s) district"):
            pincodes.parse(b"officename,pincode,statename\nX,411001,MAHARASHTRA\n")
        with self.assertRaisesMessage(ValueError, "empty"):
            pincodes.parse(b"")


class DirectoryTests(SimpleTestCase):
    """Loads into ``public`` and adds organisations to the test hospital; both are removed
    afterwards so CARE's own tests see the geography they expect."""

    databases = {"default"}

    def setUp(self):
        connection.set_schema(TEST_SCHEMA)
        self.orgs_before = set(Organization.objects.values_list("id", flat=True))
        self.addCleanup(self._clean)

    def _clean(self):
        connection.set_schema(TEST_SCHEMA)
        new = Organization.objects.exclude(id__in=self.orgs_before)
        new.filter(parent__isnull=False).delete()
        new.delete()
        PostOffice.objects.all().delete()
        PinDirectoryLoad.objects.all().delete()

    def _load(self):
        call_command(
            "ihr_load_pincodes", str(SAMPLE), "--released", "01-10-2026", verbosity=0
        )

    def test_load_replaces_and_refuses_unknown_states(self):
        self._load()
        self.assertEqual(PostOffice.objects.count(), 10)
        loaded = pincodes.current()
        self.assertEqual((loaded.pincodes, loaded.offices, loaded.skipped), (8, 10, 2))
        self.assertEqual(loaded.released_on.isoformat(), "2026-10-01")
        bad = (
            HEADER
            + "Pune H.O,411001,H.O,Delivery,PUNE,MAHARASHTRA\nX,999999,B.O,Delivery,X,ATLANTIS\n"
        )
        with self.assertRaisesMessage(ValueError, "'ATLANTIS' (1 rows)"):
            pincodes.load(bad.encode(), "bad.csv")
        self.assertEqual(PostOffice.objects.count(), 10)  # unchanged
        small = (HEADER + "Pune H.O,411001,H.O,Delivery,PUNE,MAHARASHTRA\n").encode()
        # A one-state extract would leave the other states without districts
        with self.assertRaisesMessage(ValueError, "no offices in Andhra Pradesh, "):
            pincodes.load(small, "small.csv")
        self.assertEqual(PostOffice.objects.count(), 10)
        pincodes.load(small, "small.csv", force=True)
        self.assertEqual(PostOffice.objects.count(), 1)
        self.assertEqual(pincodes.current().source_name, "small.csv")
        with self.assertRaises(CommandError):
            call_command("ihr_load_pincodes", str(SAMPLE), "--released", "2026-10-01")

    def test_geography_is_loaded_once(self):
        with self.assertRaisesMessage(CommandError, "ihr_load_pincodes first"):
            call_command("ihr_load_geography", "--all", verbosity=0)
        self._load()
        # A hospital that already has its own district, typed in another case
        state = geography.ensure_state("27")
        geography.ensure_district(state, "PUNE")
        with schema_context("public"):
            slug = Hospital.objects.get(schema_name=TEST_SCHEMA).slug
        call_command("ihr_load_geography", "--slug", slug, verbosity=0)
        connection.set_schema(TEST_SCHEMA)
        self.assertEqual(geography.load_all(), (8, 0))  # repeat adds nothing
        districts = Organization.objects.filter(
            org_type="govt", metadata=geography.DISTRICT_META
        ).exclude(id__in=self.orgs_before)
        self.assertEqual(districts.filter(name__iexact="pune").count(), 1)
        self.assertEqual(districts.count(), 9)  # 10 offices, 9 distinct districts
        delhi = Organization.objects.get(name="Delhi", parent=None, org_type="govt")
        self.assertEqual(
            sorted(delhi.children.values_list("name", flat=True)),
            ["Central Delhi", "New Delhi"],
        )
        kadapa = Organization.objects.get(name="Y.S.R. Kadapa")
        self.assertEqual(kadapa.parent.name, "Andhra Pradesh")
        self.assertEqual(kadapa.level_cache, 1)
        self.assertEqual(geography.directory_district("27", " pune "), "Pune")
        self.assertEqual(geography.directory_district("27", "Nowhere"), "Nowhere")

    def test_typed_district_must_be_in_the_directory(self):
        self.assertIsNone(geography.district_problem("06", "Gurgaon"))  # not loaded
        self._load()
        self.assertIsNone(geography.district_problem("07", " new delhi "))
        self.assertEqual(
            geography.district_problem("07", "New Delhy"),
            "not in India Post's list for Delhi (did you mean New Delhi?)",
        )
        self.assertEqual(
            geography.district_problem("29", "Mysore Rural"),
            "not in India Post's list for Karnataka",
        )
        # A state the directory has no offices in (Haryana here, or "Other Territory")
        # cannot be checked, so it is not refused
        self.assertIsNone(geography.district_problem("06", "Gurgaon"))
        self.assertIsNone(geography.district_problem("97", "Anywhere"))
        from ihr_platform.provisioning.steps import StepError
        from ihr_platform.tests.test_provisioning import form

        with self.assertRaisesMessage(StepError, "district: not in India Post's list"):
            form("geocheck", district="Poona").validate()
        form("geocheck", district="pune").validate()

    def test_geography_made_by_care_is_reused(self):
        # CARE's own API and fixtures store government organisations without metadata
        kerala = Organization.objects.create(name="Kerala", org_type="govt")
        ernakulam = Organization.objects.create(
            name="Ernakulam", org_type="govt", parent=kerala
        )
        count = Organization.objects.count()
        # The oldest "Kerala" is reused (CARE's own tests may have made one before ours)
        self.assertLessEqual(geography.ensure_state("32").id, kerala.id)
        self.assertEqual(
            geography.ensure_district(kerala, "ERNAKULAM"), (ernakulam, False)
        )
        self.assertEqual(Organization.objects.count(), count)

    def test_only_live_hospitals_are_changed(self):
        self._load()
        with schema_context("public"):
            hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
            Hospital.objects.filter(pk=hospital.pk).update(
                status=Hospital.Status.SUSPENDED
            )
        try:
            with self.assertRaisesMessage(CommandError, "is suspended"):
                call_command("ihr_load_geography", "--slug", hospital.slug)
            call_command("ihr_load_geography", "--all", verbosity=0)
            connection.set_schema(TEST_SCHEMA)
            self.assertFalse(
                Organization.objects.exclude(id__in=self.orgs_before).exists()
            )
        finally:
            with schema_context("public"):
                Hospital.objects.filter(pk=hospital.pk).update(status=hospital.status)

    def test_lookup(self):
        url = "/api/ihr/pincode/110001/"
        self.assertIn(
            APIClient().get(url, HTTP_HOST="testserver").status_code, (401, 403)
        )
        user = User.objects.create_user(username="pin-reader", password="x-Unused-1")
        with schema_context("public"):
            hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
        try:
            with testing.fake_keycloak():
                client = testing.staff_client(hospital, "testserver", user)
                self.assertEqual(client.get(url).status_code, 503)  # not loaded yet
                self._load()
                connection.set_schema(TEST_SCHEMA)
                geography.load_all()
                response = client.get(url)
                bad = client.get("/api/ihr/pincode/011000/")
                unknown = client.get("/api/ihr/pincode/999999/")
            self.assertEqual((bad.status_code, unknown.status_code), (400, 404))
            self.assertEqual(response.status_code, 200)
            data = response.json()
            connection.set_schema(TEST_SCHEMA)
            new_delhi = Organization.objects.get(name="New Delhi")
            ids = ("state_code", "state", "district", "state_organization")
            self.assertEqual(
                [{k: p[k] for k in (*ids, "geo_organization")} for p in data["places"]],
                [
                    {
                        "state_code": "07",
                        "state": "Delhi",
                        "district": "Central Delhi",
                        "state_organization": str(new_delhi.parent.external_id),
                        "geo_organization": str(
                            Organization.objects.get(name="Central Delhi").external_id
                        ),
                    },
                    {
                        "state_code": "07",
                        "state": "Delhi",
                        "district": "New Delhi",
                        "state_organization": str(new_delhi.parent.external_id),
                        "geo_organization": str(new_delhi.external_id),
                    },
                ],
            )
            # Full organisations for the address form (P1-T7b), as CARE's list returns them
            district = data["places"][1]["geo_organization_object"]
            self.assertEqual(
                (district["id"], district["name"], district["level_cache"]),
                (str(new_delhi.external_id), "New Delhi", 1),
            )
            self.assertFalse(district["has_children"])
            self.assertEqual(
                district["parent"]["id"], str(new_delhi.parent.external_id)
            )
            state = data["places"][0]["state_organization_object"]
            self.assertEqual((state["name"], state["level_cache"]), ("Delhi", 0))
            self.assertTrue(state["has_children"])
            self.assertEqual(
                [o["name"] for o in data["post_offices"]],
                ["Sample Border S.O", "Connaught Place S.O"],
            )
            self.assertEqual(data["source"]["released_on"], "2026-10-01")
        finally:
            connection.set_schema(TEST_SCHEMA)
            StaffIdentity.objects.filter(user=user).delete()
            user.delete()

    def test_lookup_from_the_patient_page(self):
        """A patient signed in by OTP on this hospital's appointment page may look up a PIN
        (P1-T7b); a token issued by another hospital, or none, is refused."""
        from config.patient_otp_token import PatientToken

        url = "/api/ihr/pincode/110001/"
        self._load()
        connection.set_schema(TEST_SCHEMA)
        geography.load_all()
        token = PatientToken()  # bound to this hospital's schema ("tid")
        token["phone_number"] = "+919999999999"  # synthetic
        self.assertEqual(token["tid"], TEST_SCHEMA)
        other = PatientToken()
        other["phone_number"] = "+919999999999"
        other["tid"] = "h_otherhosp01"  # as minted by another hospital

        patient = APIClient(HTTP_HOST="testserver")
        patient.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = patient.get(url)
        self.assertEqual(response.status_code, 200, response.content[:300])
        places = response.json()["places"]
        # 110001 spans two districts: both are offered, each with its organisation
        self.assertEqual(
            [p["geo_organization_object"]["name"] for p in places],
            ["Central Delhi", "New Delhi"],
        )
        self.assertEqual(
            {p["state_organization_object"]["name"] for p in places}, {"Delhi"}
        )

        foreign = APIClient(HTTP_HOST="testserver")
        foreign.credentials(HTTP_AUTHORIZATION=f"Bearer {other}")
        self.assertIn(foreign.get(url).status_code, (401, 403))
        self.assertIn(
            APIClient(HTTP_HOST="testserver").get(url).status_code, (401, 403)
        )
