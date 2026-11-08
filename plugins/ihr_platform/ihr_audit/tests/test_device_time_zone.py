"""Changes made from a computer that is not on IST name its time zone (P1-T7b).

The web app sends the device's zone as X-IHR-TZ to our own API. The trail notes it (context
key ``tz``) only when it is not IST, and never trusts it for anything else.
"""

import csv
import io
import time
from types import SimpleNamespace

from care.users.models import User
from django.test import SimpleTestCase
from model_bakery import baker
from rest_framework.test import APIClient

from ihr_audit.context import device_zone

from .test_audit_trail import HOST, AuditCase, entries_since, mark
from .test_viewer import ViewerCase, today


def _request(value):
    return SimpleNamespace(META={} if value is None else {"HTTP_X_IHR_TZ": value})


class DeviceZoneRuleTests(SimpleTestCase):
    def test_ist_and_missing_are_not_noted(self):
        for value in (
            None,
            "Asia/Kolkata",
            "Asia/Calcutta",
            "UTC+05:30",
            " Asia/Kolkata ",
        ):
            with self.subTest(value=value):
                self.assertIsNone(device_zone(_request(value)))

    def test_other_zones_are_noted_as_sent(self):
        for value in (
            "UTC",
            "Europe/London",
            "America/Argentina/Buenos_Aires",
            "Etc/GMT-5",
            "UTC-03:30",
            "Asia/Colombo",
        ):
            with self.subTest(value=value):
                self.assertEqual(device_zone(_request(value)), value)

    def test_anything_else_is_invalid(self):
        for value in (
            "",
            "Europe/London<script>",
            "a b",
            "Asia/Kolkata;x=1",
            "Z" * 65,
            "Asia/Kolkatá",
            "zone\nnext",
        ):
            with self.subTest(value=value):
                self.assertEqual(device_zone(_request(value)), "invalid")


class DeviceZoneTrailTests(AuditCase):
    def _refused_post(self, zone):
        since = mark()
        response = APIClient(HTTP_HOST=HOST).post(
            "/api/v1/patient/", {}, format="json", HTTP_X_IHR_TZ=zone
        )
        self.assertIn(response.status_code, (401, 403))
        [entry] = entries_since(since, kind="request")
        return entry

    def test_an_attempt_from_another_zone_names_it(self):
        entry = self._refused_post("Europe/London")
        self.assertEqual(entry["context"]["tz"], "Europe/London")

    def test_an_attempt_from_ist_adds_nothing(self):
        entry = self._refused_post("Asia/Kolkata")
        self.assertNotIn("tz", entry["context"])

    def test_a_bad_header_is_recorded_as_invalid(self):
        entry = self._refused_post("x" * 80)
        self.assertEqual(entry["context"]["tz"], "invalid")

    def test_changed_rows_carry_the_zone_too(self):
        target = baker.make(User, username=f"tz{time.time_ns()}"[:16])
        since = mark()
        response = self.api.put(
            f"/api/v1/users/{target.username}/",
            {
                "first_name": "Zoned",
                "last_name": "Person",
                "phone_number": "+919800000003",
                "gender": "female",
            },
            format="json",
            HTTP_X_IHR_TZ="UTC",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        [request] = entries_since(since, kind="request")
        writes = entries_since(since, tbl="users_user", row_pk=str(target.pk))
        self.assertTrue(writes)
        self.assertEqual(request["context"]["tz"], "UTC")
        for row in writes:
            self.assertEqual(row["context"]["tz"], "UTC")
        target.delete()


class DeviceZoneExportTests(ViewerCase):
    def test_the_csv_export_has_the_zone_column(self):
        since = mark()
        response = APIClient(HTTP_HOST=HOST).post(
            "/api/v1/patient/", {}, format="json", HTTP_X_IHR_TZ="UTC-03:30"
        )
        self.assertIn(response.status_code, (401, 403))
        [entry] = entries_since(since, kind="request")
        response = self.get("export/", **{"from": today(), "to": today()})
        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content).decode()
        rows = list(csv.DictReader(io.StringIO(body.lstrip("\ufeff"))))
        [row] = [r for r in rows if r["entry_id"] == str(entry["id"])]
        self.assertEqual(row["device_tz"], "UTC-03:30")
