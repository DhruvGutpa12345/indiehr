"""Audit viewer and export for hospital-wide admins (P1-T6b)."""

import csv
import hashlib
import io
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from care.emr.models import Organization, Patient
from care.emr.models.organization import OrganizationUser
from care.security.models import PermissionModel, RoleModel, RolePermission
from care.users.models import User
from django.db import connection
from model_bakery import baker

from ihr_audit import anchor, export, verify_export
from ihr_audit.export import IST
from ihr_audit.seal import seal_once

from .test_anchor import FakeStore
from .test_audit_trail import AuditCase, entries_since, mark

CLINICAL_VALUE = "Zebra-Diagnosis-4471"


def hospital_admin(name, **organization):
    user = baker.make(User, username=name, is_superuser=False)
    role = baker.make(RoleModel, name=f"role-{name}")
    permission, _ = PermissionModel.objects.get_or_create(
        slug="can_create_user", defaults={"name": "can_create_user"}
    )
    RolePermission.objects.create(role=role, permission=permission)
    OrganizationUser.objects.create(
        organization=baker.make(Organization, **organization), user=user, role=role
    )
    return user


def today():
    return datetime.now(IST).strftime("%Y-%m-%d")


class ViewerCase(AuditCase):
    def setUp(self):
        super().setUp()
        self.boss = hospital_admin(self.admin.username + "-b")
        self.api.force_authenticate(self.boss)
        self.patient = baker.make(Patient, name="Asha Viewer")
        self.patient.address = CLINICAL_VALUE
        self.patient.save()

    def tearDown(self):
        self.boss.delete()
        super().tearDown()

    def get(self, path, **params):
        return self.api.get(f"/api/ihr/audit/{path}", params)


class AccessTests(ViewerCase):
    def test_only_hospital_wide_admins_and_every_look_is_recorded(self):
        for who in (self.admin, baker.make(User, username=self.admin.username + "-p")):
            since = mark()
            self.api.force_authenticate(who)
            self.assertEqual(self.get("entries/").status_code, 403)
            self.assertEqual(
                self.get("export/", **{"from": today(), "to": today()}).status_code, 403
            )
            refused = entries_since(since, kind="request")
            self.assertEqual(len(refused), 2)
            self.assertEqual({e["context"]["purpose"] for e in refused}, {"audit"})
            self.assertEqual({e["data"]["status"] for e in refused}, {403})
        self.api.force_authenticate(self.boss)
        since = mark()
        self.assertEqual(self.get("status/").status_code, 200)
        (seen,) = entries_since(since, kind="request")
        self.assertEqual(seen["actor"], f"user:{self.boss.pk}:{self.boss.username}")
        self.assertEqual(seen["context"]["purpose"], "audit")


class ListTests(ViewerCase):
    def test_list_shows_what_changed_but_never_the_values(self):
        since = mark()
        response = self.get(
            "entries/", patient=str(self.patient.external_id), kind="write"
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(CLINICAL_VALUE, response.content.decode())
        results = response.json()["results"]
        update = next(r for r in results if r["op"] == "UPDATE")
        self.assertIn("address", update["fields"])
        self.assertNotIn("values", update)
        self.assertEqual(update["table"], "emr_patient")
        self.assertEqual(
            update["patients"],
            [{"id": str(self.patient.external_id), "name": "Asha Viewer"}],
        )
        self.assertRegex(update["at_ist"], r"^\d\d-\d\d-\d{4} \d\d:\d\d:\d\d$")
        # Showing the patient's name counts as viewing them
        (look,) = entries_since(since, kind="request")
        self.assertEqual(look["patients"], [self.patient.pk])

    def test_one_entry_shows_its_values_and_counts_as_viewing_the_patient(self):
        results = self.get(
            "entries/", patient=str(self.patient.external_id), op="UPDATE"
        )
        entry_id = results.json()["results"][0]["id"]
        since = mark()
        detail = self.get(f"entries/{entry_id}/").json()
        self.assertEqual(detail["values"]["address"][1], CLINICAL_VALUE)
        (look,) = entries_since(since, kind="request")
        self.assertEqual(look["patients"], [self.patient.pk])
        self.assertEqual(look["context"]["path"], f"/api/ihr/audit/entries/{entry_id}/")
        self.assertEqual(self.get("entries/999999999/").status_code, 404)

    def test_pages_and_filters(self):
        first = self.get("entries/", size=1).json()
        self.assertEqual(len(first["results"]), 1)
        older = self.get("entries/", size=1, before=first["next_before"]).json()
        self.assertLess(older["results"][0]["id"], first["results"][0]["id"])
        mine = self.get("entries/", actor=f"user:{self.boss.pk}:").json()["results"]
        self.assertTrue(mine)
        self.assertTrue(
            all(r["actor"].startswith(f"user:{self.boss.pk}:") for r in mine)
        )
        nobody = self.get("entries/", actor="user:%").json()["results"]
        self.assertEqual(nobody, [])  # LIKE wildcards are literal
        unknown = self.get("entries/", patient="00000000-0000-0000-0000-000000000000")
        self.assertEqual(unknown.json()["results"], [])
        for bad in (
            {"kind": "nope"},
            {"op": "drop"},
            {"table": "x; drop"},
            {"from": "07-11-2026"},
            {"from": "2026-11-08", "to": "2026-11-07"},
            {"before": "abc"},
            {"patient": "not-a-uuid"},
        ):
            self.assertEqual(self.get("entries/", **bad).status_code, 400, bad)


class ExportTests(ViewerCase):
    def test_csv_has_ist_times_and_changed_fields_without_values(self):
        since = mark()
        response = self.get("export/", **{"from": today(), "to": today()})
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response["Content-Disposition"])
        body = b"".join(response.streaming_content).decode()
        rows = list(csv.DictReader(io.StringIO(body.lstrip("\ufeff"))))
        update = next(
            r
            for r in rows
            if r["table"] == "emr_patient"
            and r["operation"] == "UPDATE"
            and r["record_id"] == str(self.patient.pk)
        )
        self.assertIn("address", update["fields_changed"].split())
        self.assertRegex(update["time_ist"], r"^\d\d-\d\d-\d{4} \d\d:\d\d:\d\d$")
        self.assertEqual(update["patient_ids"], str(self.patient.external_id))
        self.assertEqual(update["patient_names"], "Asha Viewer")
        self.assertNotIn(CLINICAL_VALUE, body)
        (look,) = entries_since(since, kind="request")
        self.assertIn(self.patient.pk, look["patients"])
        self.assertEqual(look["context"]["purpose"], "audit")

    def test_limits(self):
        start = (datetime.now(IST) - timedelta(days=40)).strftime("%Y-%m-%d")
        self.assertEqual(
            self.get("export/", **{"from": start, "to": today()}).status_code, 400
        )
        self.assertEqual(self.get("export/", **{"from": today()}).status_code, 400)
        self.assertEqual(
            self.get(
                "export/", **{"from": today(), "to": today(), "file": "xml"}
            ).status_code,
            400,
        )

    def test_spreadsheet_formulas_are_neutralised(self):
        self.assertEqual(export.cell("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(export.cell("-2+3"), "'-2+3")
        self.assertEqual(export.cell("user:1:x"), "user:1:x")
        self.assertEqual(export.cell(None), "")


class ProofTests(ViewerCase):
    def export_json(self, **extra):
        response = self.get(
            "export/", **{"from": today(), "to": today(), "file": "json", **extra}
        )
        self.assertEqual(response.status_code, 200)
        return b"".join(response.streaming_content).decode().splitlines()

    def test_json_export_proves_its_entries_and_reveals_no_values(self):
        seal_once(connection.schema_name)
        store = FakeStore()
        anchor.write(store)
        with patch.object(anchor, "S3Store", lambda role: store):
            lines = self.export_json(patient=str(self.patient.external_id))
        self.assertNotIn(CLINICAL_VALUE, "\n".join(lines))
        result = verify_export.check(lines)
        self.assertEqual(result.problems, [])
        notes = result.notes
        self.assertTrue(
            any("tied to the outside fingerprint" in n for n in notes), notes
        )
        self.assertTrue(result.entries)
        self.assertTrue(all(self.patient.pk in e[1] for e in result.entries))
        kinds = {json.loads(line)["type"] for line in lines}
        self.assertTrue({"export", "seal", "entry", "anchor", "end"} <= kinds)

    def test_tampering_with_an_export_is_found(self):
        seal_once(connection.schema_name)
        lines = self.export_json()
        result = verify_export.check(lines)
        self.assertEqual(result.problems, [])
        self.assertEqual(result.status, 3)  # no fingerprint: not proven, not OK
        self.assertTrue(any("not tied" in g for g in result.gaps), result.gaps)
        index = next(
            i
            for i, line in enumerate(lines)
            if '"type":"entry"' in line and '"seal":null' not in line
        )
        original = json.loads(lines[index])

        def check(changed, i=index):
            return verify_export.check(
                [*lines[:i], json.dumps(changed), *lines[i + 1 :]]
            ).problems

        def sha(text):
            return hashlib.sha256(text.encode()).hexdigest()

        details = json.loads(original["inner"])
        details[3] = "user:0:someone-else"  # the actor
        changed = {**original, "inner": json.dumps(details)}
        self.assertTrue(any("details do not match" in p for p in check(changed)))
        outer = json.loads(original["canonical"])
        outer[2] = sha(changed["inner"])
        changed["canonical"] = json.dumps(outer)
        self.assertTrue(any("does not match its hash" in p for p in check(changed)))
        changed["leaf"] = sha(changed["canonical"])
        problems = check(changed)
        self.assertTrue(
            any("entries do not match the seal" in p for p in problems), problems
        )
        # An exported entry turned into a bare hash line is found
        hidden = {k: v for k, v in original.items() if k not in ("inner", "patients")}
        problems = check({**hidden, "type": "leaf"})
        self.assertTrue(any("only as a hash" in p for p in problems), problems)
        # Entries naming a seal whose header is gone
        seal_line = next(i for i, line in enumerate(lines) if '"type":"seal"' in line)
        problems = verify_export.check(
            [*lines[:seal_line], *lines[seal_line + 1 :]]
        ).problems
        self.assertTrue(any("no full header" in p for p in problems), problems)
        dropped = [*lines[:index], *lines[index + 1 :]]
        self.assertTrue(verify_export.check(dropped).problems)
        self.assertTrue(verify_export.check(lines[:-1]).problems)  # cut short

    def test_seals_of_both_formats_verify(self):
        from ihr_audit import seal

        for version in (1, 2, 3):
            baker.make(Patient, name=f"format {version}")
            with patch.object(seal, "FORMAT", version):
                seal_once(connection.schema_name)
        problems, _summary = seal.verify(connection.schema_name)
        self.assertEqual(problems, [])
        with connection.cursor() as cursor:
            cursor.execute("SELECT DISTINCT version FROM ihr_audit_seal")
            self.assertEqual({row[0] for row in cursor.fetchall()}, {1, 2, 3})


class ReviewRegressionTests(ViewerCase):
    def test_only_a_top_level_active_organisation_admin_may_look(self):
        root = baker.make(Organization)
        for name, organization in (
            ("team", {"parent": root}),
            ("off", {"active": False}),
        ):
            user = hospital_admin(f"{self.admin.username}-{name}", **organization)
            self.api.force_authenticate(user)
            self.assertEqual(self.get("entries/").status_code, 403, name)

    def test_searched_phone_numbers_and_names_are_masked(self):
        from ihr_audit.context import mask_query

        self.assertEqual(
            mask_query("phone_number=%2B919876543210&name=Ramesh&limit=15"),
            "phone_number=***3210&name=***&limit=15",
        )
        since = mark()
        self.get("entries/", actor="+919876543210")
        (look,) = entries_since(since, kind="request")
        self.assertEqual(look["context"]["query"], "actor=***3210")
        listed = self.get("entries/", size=5).content.decode()
        self.assertNotIn("9876543210", listed)

    def test_an_export_records_every_patient_it_shows(self):
        from ihr_audit import reads

        other = baker.make(Patient, name="Second")
        other.address = "changed"
        other.save()
        since = mark()
        with patch.object(reads, "MAX_PATIENTS", 1):
            response = self.get("export/", **{"from": today(), "to": today()})
            b"".join(response.streaming_content)
        parts = entries_since(since, kind="request")
        self.assertGreater(len(parts), 1)
        self.assertEqual(
            {p["context"]["rid"] for p in parts}, {parts[0]["context"]["rid"]}
        )
        recorded = {pid for p in parts for pid in p["patients"]}
        self.assertTrue({self.patient.pk, other.pk} <= recorded)
        self.assertEqual(len(recorded), parts[0]["data"]["patients_total"])

    def test_value_hashes_are_salted(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT salt IS NOT NULL, encode(sha256(convert_to(data::text, 'UTF8')), "
                "'hex') FROM ihr_audit_entry WHERE tbl = 'emr_patient' AND op = 'U' "
                "AND row_pk = %s ORDER BY id DESC LIMIT 1",
                [str(self.patient.pk)],
            )
            salted, unsalted = cursor.fetchone()
        self.assertTrue(salted)
        seal_once(connection.schema_name)
        response = self.get(
            "export/",
            **{"from": today(), "to": today(), "file": "json"},
            patient=str(self.patient.external_id),
        )
        body = b"".join(response.streaming_content).decode()
        self.assertNotIn(unsalted, body)
        self.assertNotIn("salt", body)

    def test_out_of_range_numbers_are_refused_not_crashed(self):
        self.assertEqual(
            self.get("entries/", **{"from": "9999-12-31"}).status_code, 400
        )
        self.assertEqual(self.get("entries/", before="9" * 30).status_code, 400)
        self.assertEqual(self.get("entries/" + "9" * 30 + "/").status_code, 404)


class SecondReviewTests(ViewerCase):
    def lines(self):
        seal_once(connection.schema_name)
        response = self.get(
            "export/", **{"from": today(), "to": today(), "file": "json"}
        )
        return b"".join(response.streaming_content).decode().splitlines()

    def test_a_detailed_line_stripped_to_its_hash_is_found(self):
        lines = self.lines()
        i = next(
            i
            for i, line in enumerate(lines)
            if '"type":"entry"' in line and '"seal":null' not in line
        )
        item = json.loads(lines[i])
        bare = {"type": "leaf", "seal": item["seal"], "leaf": item["leaf"]}
        end = json.loads(lines[-1])
        end["entries"] -= 1
        forged = [*lines[:i], json.dumps(bare), *lines[i + 1 : -1], json.dumps(end)]
        problems = verify_export.check(forged).problems
        self.assertTrue(any("without its time" in p for p in problems), problems)

    def test_dropping_the_first_or_last_window_is_not_proven_or_fails(self):
        lines = self.lines()
        items = [json.loads(line) for line in lines]
        full = [x["seq"] for x in items if x["type"] == "seal" and x["leaves"]]
        first = full[0]
        kept = [
            x
            for x in items
            if not (x["type"] == "seal" and x["seq"] == first)
            and x.get("seal", "-") != first
        ]
        kept[-1]["entries"] = sum(1 for x in kept if x["type"] == "entry")
        result = verify_export.check([json.dumps(x) for x in kept])
        self.assertNotEqual(result.status, 0, (result.problems, result.gaps))
        if len(full) > 1:
            last = full[-1]
            hollow = [
                {**x, "leaves": False}
                if x["type"] == "seal" and x["seq"] == last
                else x
                for x in items
                if x.get("seal", "-") != last
            ]
            hollow[-1]["entries"] = sum(1 for x in hollow if x["type"] == "entry")
            result = verify_export.check([json.dumps(x) for x in hollow])
            self.assertNotEqual(result.status, 0)

    def test_the_range_ends_when_the_export_is_recorded(self):
        header = json.loads(self.lines()[0])
        self.assertLessEqual(
            datetime.fromisoformat(header["to_before"]),
            datetime.fromisoformat(header["generated_at"]),
        )

    def test_one_entry_never_shows_an_unmasked_search(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO ihr_audit_entry (kind, actor, context) VALUES "
                "('request', 'user:1:x', %s) RETURNING id",
                [json.dumps({"query": "phone_number=9876543210&otp=123456"})],
            )
            pk = cursor.fetchone()[0]
        body = self.get(f"entries/{pk}/").content.decode()
        self.assertNotIn("9876543210", body)
        self.assertNotIn("123456", body)

    def test_staff_access_still_protects_every_organisation_admin(self):
        from ihr_identity.staff_access import hospital_wide_admin, organisation_admin

        district = hospital_admin(
            self.admin.username + "-d", parent=baker.make(Organization)
        )
        self.assertTrue(organisation_admin(district))
        self.assertFalse(hospital_wide_admin(district))


class ThirdReviewTests(ViewerCase):
    def lines(self, **extra):
        seal_once(connection.schema_name)
        response = self.get(
            "export/", **{"from": today(), "to": today(), "file": "json", **extra}
        )
        return b"".join(response.streaming_content).decode().splitlines()

    def test_one_header_first_and_nothing_after_the_end(self):
        lines = self.lines()
        other = json.loads(lines[0])
        other["patient"] = 999999
        problems = verify_export.check(
            [lines[0], json.dumps(other), *lines[1:]]
        ).problems
        self.assertTrue(any("second export header" in p for p in problems), problems)
        problems = verify_export.check([*lines[1:], lines[0]]).problems
        self.assertTrue(
            any("before the export header" in p for p in problems), problems
        )
        problems = verify_export.check([*lines, lines[-2]]).problems
        self.assertTrue(any("after the end line" in p for p in problems), problems)

    def test_patient_ids_must_line_up(self):
        lines = self.lines(patient=str(self.patient.external_id))
        i = next(i for i, line in enumerate(lines) if '"type":"entry"' in line)
        item = json.loads(lines[i])
        item["patients"] = ["00000000-0000-0000-0000-000000000000", "extra"]
        problems = verify_export.check(
            [*lines[:i], json.dumps(item), *lines[i + 1 :]]
        ).problems
        self.assertTrue(any("do not line up" in p for p in problems), problems)

    def test_the_script_checks_the_request(self):
        import tempfile
        from contextlib import redirect_stdout
        from io import StringIO
        from pathlib import Path

        lines = self.lines(patient=str(self.patient.external_id))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "export.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            out = StringIO()
            with redirect_stdout(out):
                wrong = verify_export.main(
                    [
                        "v",
                        str(path),
                        "--patient",
                        "00000000-0000-0000-0000-000000000000",
                    ]
                )
                right = verify_export.main(
                    [
                        "v",
                        str(path),
                        "--hospital",
                        str(connection.tenant.uuid),
                        "--from",
                        today(),
                        "--to",
                        today(),
                        "--patient",
                        str(self.patient.external_id),
                    ]
                )
        self.assertEqual(wrong, 1)
        self.assertNotEqual(right, 1, out.getvalue())
        self.assertIn(f"patient:  {self.patient.external_id}", out.getvalue())


class FullProofTests(ViewerCase):
    def test_a_finished_range_is_fully_proven(self):
        from ihr_audit.export import Export

        def now():
            with connection.cursor() as cursor:
                cursor.execute("SELECT clock_timestamp()")
                return cursor.fetchone()[0]

        baker.make(Patient, name="before")
        seal_once(connection.schema_name)  # the window before the range: format 3
        start = now()
        inside = baker.make(Patient, name="inside")
        seal_once(connection.schema_name)
        end = now()
        baker.make(Patient, name="after")
        seal_once(connection.schema_name)  # the window after the range
        store = FakeStore()
        anchor.write(store)
        job = Export(connection.schema_name, connection.tenant, start, end)
        job.note_patients(proof=True)
        with patch.object(anchor, "S3Store", lambda role: store):
            result = verify_export.check(list(job.jsonl()))
        self.assertEqual(result.status, 0, (result.problems, result.gaps))
        self.assertTrue(any(inside.pk in e[1] for e in result.entries))
