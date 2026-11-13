"""Object storage settings and picture links (P1-T20b; TENANCY_DESIGN §6.8).

The isolation suite checks the links per hospital (test_picture_links_are_signed_for_this_hospital_only);
these are the rules underneath: which key each bucket uses, the key shape, and the link cache.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import boto3
from care.utils.csp.config import BucketType, get_client_config
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django_tenants.utils import schema_context

from ihr_platform import storage
from ihr_platform.test_runner import TEST_SCHEMA

KEYS = {
    "BUCKET_PROVIDER": "MINIO",
    "BUCKET_KEY": "",
    "BUCKET_SECRET": "",
    "FILE_UPLOAD_KEY": "files-key",
    "FILE_UPLOAD_SECRET": "files-pass",
    "FILE_UPLOAD_REGION": "ap-south-1",
    "FILE_UPLOAD_BUCKET": "patient-bucket",
    "FILE_UPLOAD_BUCKET_ENDPOINT": "http://s3:8333",
    "FILE_UPLOAD_BUCKET_EXTERNAL_ENDPOINT": "https://files.example.in",
    "FACILITY_S3_KEY": "pics-key",
    "FACILITY_S3_SECRET": "pics-pass",
    "FACILITY_S3_REGION": "ap-south-1",
    "FACILITY_S3_BUCKET": "facility-bucket",
    "FACILITY_S3_BUCKET_ENDPOINT": "http://s3:8333",
    "FACILITY_S3_BUCKET_EXTERNAL_ENDPOINT": "https://files.example.in",
}


@override_settings(**KEYS)
class CredentialsPerBucketTests(SimpleTestCase):
    """CARE's patient and report clients used the pictures bucket's key (upstream bug); the
    logged patch in care/utils/csp/config.py gives them FILE_UPLOAD_*."""

    def test_each_bucket_signs_with_its_own_key(self):
        for bucket_type, key, secret, bucket in (
            (BucketType.PATIENT, "files-key", "files-pass", "patient-bucket"),
            (BucketType.REPORT, "files-key", "files-pass", "patient-bucket"),
            (BucketType.FACILITY, "pics-key", "pics-pass", "facility-bucket"),
        ):
            for external in (False, True):
                with self.subTest(bucket=bucket_type, external=external):
                    config, name = get_client_config(bucket_type, external=external)
                    self.assertEqual(config["aws_access_key_id"], key)
                    self.assertEqual(config["aws_secret_access_key"], secret)
                    self.assertEqual(name, bucket)
                    self.assertEqual(
                        config["endpoint_url"],
                        "https://files.example.in" if external else "http://s3:8333",
                    )

    @override_settings(FILE_UPLOAD_REGION="ap-south-2")
    def test_patient_files_use_their_own_region(self):
        self.assertEqual(
            get_client_config(BucketType.PATIENT)[0]["region_name"], "ap-south-2"
        )
        self.assertEqual(
            get_client_config(BucketType.FACILITY)[0]["region_name"], "ap-south-1"
        )

    @override_settings(BUCKET_PROVIDER="AWS_ROLE_BASED")
    def test_role_based_access_carries_no_key(self):
        for bucket_type in BucketType:
            self.assertNotIn("aws_access_key_id", get_client_config(bucket_type)[0])


def picture_key(prefix, folder="avatars"):
    return f"{prefix}{folder}/{uuid.uuid4()}_{uuid.uuid4().hex[:16]}.png"


@override_settings(**KEYS)
class PictureLinkTests(SimpleTestCase):
    databases = {"default"}

    def setUp(self):
        connection.set_schema(TEST_SCHEMA)
        self.prefix = storage.tenant_key_prefix()

    def test_a_signed_link_for_this_hospitals_key(self):
        key = picture_key(self.prefix)
        url = storage.facility_image_url(key)
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        self.assertEqual(
            f"{parts.scheme}://{parts.netloc}{parts.path}",
            f"https://files.example.in/facility-bucket/{key}",
        )
        self.assertEqual(query["X-Amz-Expires"], [str(storage.LINK_SECONDS)])
        self.assertTrue(query["X-Amz-Credential"][0].startswith("pics-key/"))
        self.assertIn("X-Amz-Signature", query)

    def test_only_keys_of_this_hospital_and_of_the_upload_shape(self):
        other = f"t/{uuid.uuid4()}/"
        good = picture_key(self.prefix)
        refused = (
            picture_key(other),  # another hospital
            picture_key("", "avatars"),  # no prefix (upstream key)
            picture_key(self.prefix, "patient"),  # another folder
            good.replace(".png", ".png/../x"),
            good.replace("avatars/", "avatars/../"),
            f"{self.prefix}avatars/x.png",
            f"{self.prefix}{picture_key(other)}",  # another hospital's key under ours
            f" {good}",
            f"{good}\n",
            "",
            None,
        )
        for key in refused:
            with self.subTest(key=key):
                self.assertIsNone(storage.facility_image_url(key))
        self.assertIsNotNone(storage.facility_image_url(good))
        self.assertIsNotNone(
            storage.facility_image_url(picture_key(self.prefix, "cover_images"))
        )

    def test_public_schema_returns_no_link_without_raising(self):
        key = picture_key(self.prefix)
        with schema_context("public"):
            self.assertIsNone(storage.facility_image_url(key))

    def test_the_same_link_is_handed_out_until_it_is_renewed(self):
        key = picture_key(self.prefix)
        first = storage.facility_image_url(key)
        with patch("boto3.client") as client:
            self.assertEqual(storage.facility_image_url(key), first)
            client.assert_not_called()  # served from the cache

    @override_settings(FACILITY_S3_KEY="")
    def test_key_based_access_is_decided_by_the_provider_not_the_keys_value(self):
        """An empty key (a test container's settings) is still key-based access: cached, and
        never mistaken for role-based credentials."""
        key = picture_key(self.prefix)
        with patch.object(storage, "_role_credentials") as role:
            first = storage.facility_image_url(key)
            self.assertEqual(storage.facility_image_url(key), first)
            role.assert_not_called()

    def test_one_client_per_signing_identity(self):
        """Review C5: a cold list (every link a cache miss) signs with one reused client, not one
        per picture; a rotated key gets its own. The secret itself is never part of its name."""
        storage._signers.clear()
        self.addCleanup(storage._signers.clear)
        with patch("boto3.client", wraps=boto3.client) as client:
            for _ in range(3):
                self.assertIsNotNone(
                    storage.facility_image_url(picture_key(self.prefix))
                )
            self.assertEqual(client.call_count, 1)
            with override_settings(
                FACILITY_S3_KEY="pics-key-2", FACILITY_S3_SECRET="pics-pass-2"
            ):
                self.assertIn(
                    "pics-key-2%2F",
                    storage.facility_image_url(picture_key(self.prefix)),
                )
            self.assertEqual(client.call_count, 2)
            self.assertIn(
                "pics-key%2F", storage.facility_image_url(picture_key(self.prefix))
            )
            self.assertEqual(client.call_count, 2)
        self.assertNotIn("pics-pass", repr(list(storage._signers)))

    def test_the_cache_name_changes_with_key_id_endpoint_and_lifetime(self):
        key = picture_key(self.prefix)
        base = {
            "key_id": "pics-key",
            "endpoint": "https://files.example.in|ap-south-1",
            "bucket": "facility-bucket",
            "seconds": 86400,
        }
        name = storage.link_cache_name(self.prefix, key, **base)
        self.assertTrue(
            name.startswith(f"ihr:picture-link:t:{self.prefix.split('/')[1]}:"), name
        )
        for change in (
            {"key_id": "pics-key-2"},
            {"endpoint": "https://files2.example.in|ap-south-1"},
            {"bucket": "other-bucket"},
            {"seconds": 3600},
        ):
            with self.subTest(change=change):
                self.assertNotEqual(
                    storage.link_cache_name(self.prefix, key, **(base | change)), name
                )

    def test_a_rotated_key_or_moved_endpoint_signs_again(self):
        key = picture_key(self.prefix)
        first = storage.facility_image_url(key)
        with override_settings(FACILITY_S3_KEY="pics-key-2"):
            rotated = storage.facility_image_url(key)
        self.assertNotEqual(rotated, first)
        self.assertIn("pics-key-2%2F", rotated)
        with override_settings(
            FACILITY_S3_BUCKET_EXTERNAL_ENDPOINT="https://files2.example.in"
        ):
            moved = storage.facility_image_url(key)
        self.assertTrue(moved.startswith("https://files2.example.in/"), moved)
        with patch.object(storage, "LINK_SECONDS", 3600):
            shorter = storage.facility_image_url(key)
        self.assertIn("X-Amz-Expires=3600", shorter)


def _temporary(expires_in):
    frozen = SimpleNamespace(access_key=f"ASIA{uuid.uuid4().hex[:12]}", token="t")
    return SimpleNamespace(
        get_frozen_credentials=lambda: frozen,
        _expiry_time=None
        if expires_in is None
        else datetime.now(UTC) + timedelta(seconds=expires_in),
    )


@override_settings(**KEYS | {"BUCKET_PROVIDER": "AWS_ROLE_BASED"})
class RoleBasedLinkTests(SimpleTestCase):
    """Role-based access signs with temporary credentials: a link dies with them, so it is
    never handed out again beyond their own expiry (A7)."""

    databases = {"default"}

    def setUp(self):
        connection.set_schema(TEST_SCHEMA)
        self.prefix = storage.tenant_key_prefix()
        # each test's stand-in signer, never a client another test built
        storage._signers.clear()
        self.addCleanup(storage._signers.clear)

    def _sign(self, credentials):
        signer = MagicMock()
        signer.generate_presigned_url.side_effect = lambda *a, **kw: (
            f"https://s3.example/{uuid.uuid4().hex}"
        )
        with (
            patch.object(storage, "_role_credentials", return_value=credentials),
            patch("boto3.client", return_value=signer),
            patch.object(cache, "set", wraps=cache.set) as cache_set,
        ):
            url = storage.facility_image_url(picture_key(self.prefix))
        self.assertTrue(url)
        return cache_set

    def test_cached_no_longer_than_the_credentials_live(self):
        cache_set = self._sign(_temporary(3600))
        seconds = cache_set.call_args.args[2]
        self.assertLessEqual(seconds, 3600 - storage.TEMPORARY_MARGIN_SECONDS)
        self.assertGreater(seconds, 0)

    def test_not_cached_when_the_expiry_is_unknown_or_too_close(self):
        for expires_in in (None, storage.TEMPORARY_MARGIN_SECONDS - 60):
            with self.subTest(expires_in=expires_in):
                self._sign(_temporary(expires_in)).assert_not_called()

    def test_long_lived_credentials_are_capped_at_half_the_link(self):
        cache_set = self._sign(_temporary(7 * 24 * 3600))
        self.assertEqual(cache_set.call_args.args[2], storage.CACHE_SECONDS)
