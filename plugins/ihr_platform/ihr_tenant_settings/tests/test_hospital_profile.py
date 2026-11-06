"""Hospital profile, branding by host and per-hospital links (task P1-T2a)."""

import re

from care.emr.tasks.totp import send_totp_disabled_email, send_totp_enabled_email
from care.emr.utils.reset_password import (
    generate_password_reset_token,
    send_password_reset_email,
    verify_password_reset_token,
)
from care.users.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase
from django_tenants.utils import schema_context
from ihr_identity import testing
from ihr_identity.models import StaffIdentity
from ihr_platform.links import frontend_base_url
from ihr_platform.models import Domain, Hospital, RetiredAddress, generate_schema_name
from ihr_platform.test_runner import TEST_SCHEMA
from rest_framework.test import APIClient

from ihr_tenant_settings.branding import initials
from ihr_tenant_settings.models import HospitalProfile
from ihr_tenant_settings.validators import (
    gstin_check_character,
    logo_content_type,
    validate_gstin,
    validate_pin_code,
)

# Published sample GSTINs (GST portal documentation), both with a valid check character.
VALID_GSTINS = ("27AAPFU0939F1ZV", "29AAGCB7383J1Z4")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SECOND_SLUG = "brandtest"


class ValidatorTests(SimpleTestCase):
    def test_gstin_checksum(self):
        for gstin in VALID_GSTINS:
            validate_gstin(gstin)
            self.assertEqual(gstin_check_character(gstin[:14]), gstin[14])

    def test_gstin_rejects_bad_values(self):
        bad = (
            "27AAPFU0939F1ZW",  # wrong check character
            "99AAPFU0939F1ZV",  # not a GST state code
            "27aapfu0939f1zv",  # lower case
            "27AAPFU0939F1Z",  # too short
            "",
        )
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_gstin(value)

    def test_pin_code(self):
        validate_pin_code("411001")
        for value in ("011001", "41100", "4110011", "41100A"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_pin_code(value)

    def test_logo_type_comes_from_the_bytes(self):
        self.assertEqual(logo_content_type(PNG), "image/png")
        self.assertEqual(
            logo_content_type(b"\xff\xd8\xff\xe0" + b"0" * 10), "image/jpeg"
        )
        self.assertEqual(
            logo_content_type(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "image/webp"
        )
        for data in (
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            b"GIF89a",
            PNG + b"\x00" * (256 * 1024),  # too big
        ):
            with self.subTest(data=data[:12]), self.assertRaises(ValidationError):
                logo_content_type(data)


class HospitalProfileTests(SimpleTestCase):
    """Runs in the test hospital's schema; a second hospital is provisioned for real."""

    databases = {"default"}
    second = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        connection.set_schema_to_public()
        if not Hospital.objects.filter(slug=SECOND_SLUG).exists():
            # No --domain: the address must default to <slug>.<IHR_TENANT_DOMAIN_SUFFIX>.
            call_command(
                "ihr_create_tenant",
                slug=SECOND_SLUG,
                name="Brand Test LLP",
                verbosity=0,
            )
        connection.set_schema_to_public()
        cls.second = Hospital.objects.get(slug=SECOND_SLUG)
        cls.second_domain = Domain.objects.get(tenant=cls.second).domain
        with schema_context(cls.second.schema_name):
            cls.provisioned_name = HospitalProfile.objects.get().display_name
        connection.set_schema(TEST_SCHEMA)

    @classmethod
    def tearDownClass(cls):
        connection.set_schema_to_public()
        schema = cls.second.schema_name
        Domain.objects.filter(tenant=cls.second).delete()
        Hospital.objects.filter(pk=cls.second.pk).delete()
        with connection.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        connection.set_schema(TEST_SCHEMA)
        super().tearDownClass()

    def tearDown(self):
        connection.set_schema(TEST_SCHEMA)
        super().tearDown()

    def _set(self, schema, **fields):
        with schema_context(schema):
            profile = HospitalProfile.current()
            for name, value in fields.items():
                setattr(profile, name, value)
            profile.save()
            return profile

    # --- provisioning and the registry ------------------------------------------------------

    def test_provisioning_gives_a_default_address_and_profile(self):
        self.assertEqual(self.second_domain, f"{SECOND_SLUG}.localhost")
        self.assertEqual(self.provisioned_name, "Brand Test LLP")

    def test_slug_can_never_change(self):
        with schema_context("public"):
            hospital = Hospital.objects.get(pk=self.second.pk)
            hospital.slug = "renamed-hospital"
            with self.assertRaises(ValidationError):
                hospital.save()
            self.assertEqual(Hospital.objects.get(pk=self.second.pk).slug, SECOND_SLUG)

    def test_retired_slugs_and_addresses_are_never_reused(self):
        with schema_context("public"):
            ghost = Hospital(
                slug="ghost-hospital", name="Ghost", schema_name=generate_schema_name()
            )
            ghost.auto_create_schema = False  # registry row only, no schema needed here
            ghost.save()
            Domain.objects.create(domain="ghost-hospital.localhost", tenant=ghost)
            Hospital.objects.filter(
                pk=ghost.pk
            ).delete()  # queryset delete: signals still fire
            self.assertTrue(
                RetiredAddress.objects.filter(
                    kind="slug", value="ghost-hospital"
                ).exists()
            )
            reuse = Hospital(
                slug="ghost-hospital", name="New", schema_name=generate_schema_name()
            )
            with self.assertRaises(ValidationError):
                reuse.full_clean(exclude=["uuid"])
            with self.assertRaises(ValidationError):
                Domain.objects.create(
                    domain="ghost-hospital.localhost", tenant=self.second
                )
            # an existing address cannot be re-pointed or renamed
            domain = Domain.objects.get(tenant=self.second)
            domain.domain = "renamed.localhost"
            with self.assertRaises(ValidationError):
                domain.save()

    # --- the profile itself -----------------------------------------------------------------

    def test_public_pages_never_write_the_profile(self):
        """No row yet: bootstrap answers from the registry name and creates nothing."""
        with schema_context(self.second.schema_name):
            saved = HospitalProfile.objects.get()
            HospitalProfile.objects.all().delete()
        try:
            response = APIClient().get(
                "/api/ihr/bootstrap/", HTTP_HOST=self.second_domain
            )
            self.assertEqual(response.json()["name"], "Brand Test LLP")
            self.assertEqual(response["Cache-Control"], "private, no-cache")
            with schema_context(self.second.schema_name):
                self.assertFalse(HospitalProfile.objects.exists())
        finally:
            with schema_context(self.second.schema_name):
                saved.save()

    def test_long_legal_names_fit(self):
        profile = self._set(TEST_SCHEMA, display_name="N" * 255)
        self.assertEqual(len(profile.display_name), 255)
        self._set(TEST_SCHEMA, display_name="Test Hospital")

    def test_only_one_profile_per_hospital(self):
        HospitalProfile.current()
        with transaction.atomic(), self.assertRaises(IntegrityError):
            HospitalProfile.objects.bulk_create(
                [HospitalProfile(display_name="Second row")]
            )

    def test_gstin_state_must_match_address_state(self):
        with self.assertRaises(ValidationError):
            self._set(TEST_SCHEMA, gstin=VALID_GSTINS[0], state_code="29")
        profile = self._set(TEST_SCHEMA, gstin=VALID_GSTINS[0], state_code="27")
        self.assertEqual(profile.state_name, "Maharashtra")

    # --- branding by host -------------------------------------------------------------------

    def test_each_host_gets_its_own_branding(self):
        self._set(TEST_SCHEMA, display_name="Test Hospital", primary_colour="#112233")
        self._set(
            self.second.schema_name,
            display_name="Brand <b>Test</b>",
            primary_colour="#445566",
            logo=PNG,
        )
        own = APIClient().get("/api/ihr/bootstrap/", HTTP_HOST="testserver").json()
        other = (
            APIClient().get("/api/ihr/bootstrap/", HTTP_HOST=self.second_domain).json()
        )
        self.assertEqual(
            (own["name"], own["primary_colour"], own["logo_url"]),
            ("Test Hospital", "#112233", "/api/ihr/logo/"),
        )
        self.assertEqual(
            (other["name"], other["primary_colour"]), ("Brand <b>Test</b>", "#445566")
        )
        self.assertRegex(other["logo_url"], r"^/api/ihr/logo/\?v=[0-9a-f]{16}$")
        self.assertNotIn("gstin", other)  # branding only for anonymous callers

    def test_bootstrap_script_cannot_break_out_of_a_script_tag(self):
        self._set(self.second.schema_name, display_name="Evil </script><script>x()")
        response = APIClient().get(
            "/api/ihr/bootstrap.js", HTTP_HOST=self.second_domain
        )
        body = response.content.decode()
        self.assertTrue(body.startswith("window.__IHR_BRANDING__ = {"))
        self.assertIn("document.title = window.__IHR_BRANDING__.short_name;", body)
        self.assertNotIn("<", body)
        self.assertEqual(response["Content-Type"], "text/javascript; charset=utf-8")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_logo_is_served_safely(self):
        profile = self._set(self.second.schema_name, logo=PNG)
        client = APIClient()
        url = f"/api/ihr/logo/?v={profile.logo_sha256[:16]}"
        response = client.get(url, HTTP_HOST=self.second_domain)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PNG)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["Content-Security-Policy"], "default-src 'none'")
        self.assertIn("immutable", response["Cache-Control"])
        stale = client.get(
            "/api/ihr/logo/?v=0000000000000000", HTTP_HOST=self.second_domain
        )
        self.assertEqual(stale["Cache-Control"], "private, no-cache")

    def test_hospitals_without_a_logo_get_their_initials(self):
        self._set(
            TEST_SCHEMA,
            display_name="Alpha <Hospital>",
            primary_colour="#112233",
            logo=None,
        )
        response = APIClient().get("/api/ihr/logo/", HTTP_HOST="testserver")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml; charset=utf-8")
        self.assertEqual(response["Content-Security-Policy"], "default-src 'none'")
        self.assertEqual(response["Cache-Control"], "private, no-cache")
        svg = response.content.decode()
        self.assertIn('fill="#112233"', svg)
        self.assertIn(">AH</text>", svg)
        self.assertIn("<title>Alpha &lt;Hospital&gt;</title>", svg)  # escaped
        self.assertEqual(initials("  sunrise   multi speciality"), "SM")
        self.assertEqual(initials("<>"), "H")

    def test_full_profile_is_for_signed_in_staff_only(self):
        self._set(TEST_SCHEMA, gstin=VALID_GSTINS[0], state_code="27")
        self.assertIn(
            APIClient().get("/api/ihr/profile/", HTTP_HOST="testserver").status_code,
            (401, 403),
        )
        user = User.objects.create_user(
            username="profile-reader", password="x-Unused-1"
        )
        with schema_context("public"):
            hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
        try:
            with testing.fake_keycloak():
                client = testing.staff_client(hospital, "testserver", user)
                response = client.get("/api/ihr/profile/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["gstin"], VALID_GSTINS[0])
        finally:
            StaffIdentity.objects.filter(user=user).delete()
            user.delete()

    def test_platform_host_has_no_hospital_routes(self):
        with schema_context("public"):
            platform, created = Hospital.objects.get_or_create(
                schema_name="public",
                defaults={"slug": "public", "name": "Platform", "status": "active"},
            )
            domain = Domain.objects.create(
                domain="platform.testserver", tenant=platform
            )
        try:
            client = APIClient()
            self.assertEqual(
                client.get("/ping/", HTTP_HOST="platform.testserver").status_code, 200
            )
            response = client.get(
                "/api/ihr/bootstrap/", HTTP_HOST="platform.testserver"
            )
            self.assertEqual(response.status_code, 404)
        finally:
            with schema_context("public"):
                domain.delete()
                if created:
                    platform.delete()

    # --- password links and tokens per hospital ----------------------------------------------

    def test_password_links_point_at_the_hospitals_own_address(self):
        with schema_context(self.second.schema_name):
            self.assertEqual(frontend_base_url(), f"https://{self.second_domain}")
            user = User.objects.create_user(
                username="link-test",
                email="link-test@example.test",
                password="x-Unused-1",
            )
            mail.outbox.clear()
            send_password_reset_email(user, "reset_password")
        link = re.search(r"https?://[^\s\"'<]+/password_reset/", mail.outbox[0].body)
        self.assertIsNotNone(link)
        self.assertEqual(link.group(0), f"https://{self.second_domain}/password_reset/")

    def test_emails_speak_for_the_hospital_not_care(self):
        self._set(self.second.schema_name, display_name="Brand Test Hospital")
        with schema_context(self.second.schema_name):
            user = User.objects.create_user(
                username="mail-test", email="mail-test@example.test", password="x-1"
            )
            mail.outbox.clear()
            send_password_reset_email(user, "create_password")
            send_password_reset_email(user, "reset_password")
            send_totp_enabled_email(user.email, user.username)
            send_totp_disabled_email(user.email, user.username)
        self.assertEqual(
            [m.subject for m in mail.outbox[:2]],
            [
                "Set Up Your Password for Brand Test Hospital",
                "Password Reset for Brand Test Hospital",
            ],
        )
        for message in mail.outbox:
            with self.subTest(subject=message.subject):
                self.assertIn("Brand Test Hospital", message.body)
                self.assertNotRegex(
                    message.body + message.subject + message.from_email,
                    r"(?i)\bcare\b|open healthcare|ohc",
                )
        self.assertRegex(
            mail.outbox[2].body, r"\d{2}-\d{2}-\d{4}, \d{2}:\d{2} [AP]M IST"
        )

    def test_reset_token_from_one_hospital_is_refused_by_another(self):
        with schema_context(self.second.schema_name):
            user = User.objects.create_user(username="same-name", password="x-Unused-1")
            token = generate_password_reset_token(user)
            self.assertEqual(verify_password_reset_token(token)[0], user)
        # An exact copy of that user (same username, password hash and id) in the test hospital:
        # every other check in CARE would pass, so only the hospital claim can refuse it.
        twin = User.objects.create(
            username=user.username, password=user.password, external_id=user.external_id
        )
        try:
            self.assertEqual(
                verify_password_reset_token(token), (None, "Invalid token")
            )
        finally:
            twin.delete()
