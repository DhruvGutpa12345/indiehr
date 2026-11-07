"""Audit trail: recording, masking, request entries, append-only, the hash chain (P1-T6a).

The trail is append-only, so these tests never clean it up: each one looks only at entries it
made (newer than a mark it takes first).
"""

import json
import threading
import time
from types import SimpleNamespace

from care.emr.models import Encounter, Patient
from care.emr.models.patient import PatientIdentifier
from care.facility.models import Facility
from care.users.models import User
from django.db import DatabaseError, connection, connections, transaction
from django.test import SimpleTestCase
from django_tenants.utils import schema_context
from ihr_platform.test_runner import TEST_SCHEMA
from model_bakery import baker
from rest_framework.test import APIClient

from ihr_audit import context, reads, seal
from ihr_audit.install import coverage, ensure_triggers
from ihr_audit.middleware import purpose_of
from ihr_audit.seal import seal_once, verify

HOST = "testserver"


def mark():
    with connection.cursor() as cursor:
        cursor.execute("SELECT coalesce(max(id), 0) FROM ihr_audit_entry")
        return cursor.fetchone()[0]


def entries_since(since, **where):
    clauses = ["id > %s"]
    params = [since]
    for column, value in where.items():
        clauses.append(f"{column} = %s")
        params.append(value)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, kind, actor, context, tbl, op, row_pk, patients, data "  # noqa: S608
            f"FROM ihr_audit_entry WHERE {' AND '.join(clauses)} ORDER BY id",
            params,
        )
        names = [c.name for c in cursor.description]
        rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    for row in rows:  # Django's cursor hands jsonb back as text
        for column in ("context", "data"):
            if isinstance(row[column], str):
                row[column] = json.loads(row[column])
    return rows


class AuditCase(SimpleTestCase):
    databases = {"default"}

    def setUp(self):
        name = f"aud-{time.time_ns()}"[:16]
        self.admin = User.objects.create_superuser(
            username=name, email=f"{name}@example.test", password="x-Unused-1"
        )
        self.api = APIClient(HTTP_HOST=HOST, REMOTE_ADDR="10.1.2.3")
        self.api.force_authenticate(self.admin)

    def tearDown(self):
        self.admin.delete()

    def facility(self):
        return baker.make(Facility, created_by=self.admin)

    def encounter(self, patient):
        return baker.make(Encounter, patient=patient, facility=self.facility())


class CoverageTests(AuditCase):
    def test_every_table_is_covered_and_every_secret_looking_column_classified(self):
        self.assertEqual(coverage(connection, TEST_SCHEMA), [])

    def test_a_new_table_or_secret_column_is_reported(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "CREATE TABLE ihr_new_thing (id bigint, api_secret text)"
                )
            problems = coverage(connection, TEST_SCHEMA)
            self.assertIn(
                f"{TEST_SCHEMA}.ihr_new_thing: trigger ihr_audit is missing", problems
            )
            self.assertTrue(any("ihr_new_thing.api_secret" in p for p in problems))
            ensure_triggers(connection, TEST_SCHEMA)
            problems = coverage(connection, TEST_SCHEMA)
            self.assertEqual(
                problems,
                [
                    f"{TEST_SCHEMA}.ihr_new_thing.api_secret: looks secret; "
                    "classify it in install.py"
                ],
            )
            transaction.set_rollback(True)

    def test_a_disabled_trigger_is_reported(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE emr_patient DISABLE TRIGGER ihr_audit")
            self.assertIn(
                f"{TEST_SCHEMA}.emr_patient: trigger ihr_audit is not enabled",
                coverage(connection, TEST_SCHEMA),
            )
            transaction.set_rollback(True)

    def test_every_kind_of_attached_record_is_mapped(self):
        from care.emr.reports.report_type_registry import ReportTypeRegistry
        from care.emr.resources.file_upload.spec import FileTypeChoices
        from care.emr.resources.meta_artifact.spec import (
            MetaArtifactAssociatingTypeChoices,
        )

        kinds = {
            "emr.FileUpload": {c.value for c in FileTypeChoices},
            "emr.MetaArtifact": {c.value for c in MetaArtifactAssociatingTypeChoices},
            "emr.ReportUpload": set(ReportTypeRegistry._registry),
        }
        for label, values in kinds.items():
            owners = reads.BY_REFERENCE[label][2]
            self.assertLessEqual(values, set(owners), label)
            for owner in owners.values():
                reads.path_to_patient(owner)  # every owner leads to a patient

    def test_care_models_without_a_patient_link_were_reviewed(self):
        reviewed = {
            line.split("#")[0].strip()
            for line in reads.UNLINKED_FILE.read_text().splitlines()
        } - {""}
        self.assertEqual(set(reads.unlinked_care_models()), reviewed)


class WriteTests(AuditCase):
    def test_insert_update_delete_keep_values(self):
        since = mark()
        patient = baker.make(Patient, name="Audit Insert", phone_number="+919800000001")
        patient.name = "Audit Update"
        patient.save()
        pk = patient.pk
        Patient.objects.filter(pk=pk).delete()  # a real delete (CARE's own is a flag)
        rows = [
            r for r in entries_since(since, tbl="emr_patient") if r["row_pk"] == str(pk)
        ]
        self.assertEqual([r["op"] for r in rows], ["I", "U", "D"])
        self.assertEqual(rows[0]["data"]["name"], "Audit Insert")
        self.assertEqual(rows[1]["data"], {"name": ["Audit Insert", "Audit Update"]})
        self.assertEqual(rows[2]["data"]["name"], "Audit Update")
        for row in rows:
            self.assertEqual(row["patients"], [pk])
            self.assertEqual(row["kind"], "write")
            self.assertNotIn("history", row["data"])
            self.assertNotIn("instance_identifiers", row["data"])

    def test_a_save_that_changes_nothing_is_not_recorded(self):
        patient = baker.make(Patient)
        since = mark()
        patient.save()  # modified_date moves, nothing else
        self.assertEqual(entries_since(since, tbl="emr_patient"), [])

    def test_secrets_are_hidden_and_identifiers_masked(self):
        since = mark()
        user = baker.make(User, username=f"sec{time.time_ns()}"[:16])
        user.set_password("Very-Secret-Pass-1")
        user.totp_secret = "JBSWY3DPEHPK3PXP"
        user.save()
        patient = baker.make(Patient)
        identifier = baker.make(
            PatientIdentifier, patient=patient, value="12345678901234"
        )
        identifier.value = "12345678909999"
        identifier.save()
        text = json.dumps(entries_since(since), default=str)
        for secret in ("Very-Secret", "pbkdf2", "JBSWY3DPEHPK3PXP", "1234567890"):
            self.assertNotIn(secret, text)
        update = entries_since(since, tbl="users_user", op="U")[-1]
        self.assertEqual(update["data"]["password"], "(hidden)")
        self.assertEqual(update["data"]["totp_secret"], "(hidden)")
        ident = entries_since(since, tbl="emr_patientidentifier", op="U")[-1]
        self.assertEqual(ident["data"]["value"], ["***1234", "***9999"])
        self.assertEqual(ident["patients"], [patient.pk])
        user.delete()

    def test_encounter_rows_name_their_patient(self):
        patient = baker.make(Patient)
        since = mark()
        encounter = self.encounter(patient)
        baker.make("emr.Consent", encounter=encounter)
        consent = entries_since(since, tbl="emr_consent")
        self.assertEqual(consent[0]["patients"], [patient.pk])

    def test_outside_requests_the_actor_is_the_system(self):
        since = mark()
        baker.make(Patient)
        self.assertTrue(entries_since(since)[0]["actor"].startswith("system:"))

    def test_a_task_is_named_as_the_actor_and_cleared_after(self):
        task = SimpleNamespace(name="care.some_task", request=SimpleNamespace(id="t-1"))
        since = mark()
        context.begin_task(task)
        try:
            baker.make(Patient)
        finally:
            context.end_task()
        row = entries_since(since, tbl="emr_patient")[0]
        self.assertEqual(row["actor"], "task:care.some_task")
        self.assertEqual(row["context"]["tid"], "t-1")
        baker.make(Patient)
        self.assertTrue(entries_since(row["id"])[-1]["actor"].startswith("system:"))

    def test_truncate_is_recorded(self):
        since = mark()
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("CREATE TABLE ihr_trunc_probe (id bigint)")
            ensure_triggers(connection, TEST_SCHEMA)
            with connection.cursor() as cursor:
                cursor.execute("TRUNCATE ihr_trunc_probe")
            self.assertEqual(
                [
                    (r["kind"], r["op"])
                    for r in entries_since(since, tbl="ihr_trunc_probe")
                ],
                [("truncate", "T")],
            )
            transaction.set_rollback(True)


class RequestTests(AuditCase):
    def test_viewing_a_patient_records_who_from_where_and_why(self):
        patient = baker.make(Patient)
        since = mark()
        response = self.api.get(f"/api/v1/patient/{patient.external_id}/")
        self.assertEqual(response.status_code, 200, response.content[:300])
        [entry] = entries_since(since, kind="request")
        self.assertEqual(entry["actor"], f"user:{self.admin.pk}:{self.admin.username}")
        self.assertEqual(entry["patients"], [patient.pk])
        self.assertEqual(entry["data"], {"status": 200})
        self.assertEqual(entry["context"]["ip"], "10.1.2.3")
        self.assertEqual(entry["context"]["method"], "GET")
        self.assertEqual(entry["context"]["purpose"], "care")
        self.assertEqual(entry["context"]["via"], "web")

    def test_reads_without_patients_leave_no_entry(self):
        since = mark()
        response = self.api.get("/api/v1/users/getcurrentuser/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(entries_since(since, kind="request"), [])

    def test_a_change_and_its_rows_share_the_request_and_actor(self):
        target = baker.make(User, username=f"tgt{time.time_ns()}"[:16])
        since = mark()
        response = self.api.put(
            f"/api/v1/users/{target.username}/",
            {
                "first_name": "Renamed",
                "last_name": "Person",
                "phone_number": "+919800000002",
                "gender": "female",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        [request] = entries_since(since, kind="request")
        writes = entries_since(since, tbl="users_user", row_pk=str(target.pk))
        self.assertTrue(writes)
        for row in writes:
            self.assertEqual(row["actor"], request["actor"])
            self.assertEqual(row["context"]["rid"], request["context"]["rid"])
        self.assertEqual(request["context"]["purpose"], "admin")
        self.assertEqual(writes[0]["data"]["first_name"][1], "Renamed")
        target.delete()

    def test_refused_attempts_are_recorded(self):
        since = mark()
        response = APIClient(HTTP_HOST=HOST).post("/api/v1/patient/", {}, format="json")
        self.assertIn(response.status_code, (401, 403))
        [entry] = entries_since(since, kind="request")
        self.assertEqual(entry["actor"], "anonymous")
        self.assertEqual(entry["data"]["status"], response.status_code)

    def test_settings_are_cleared_after_each_request(self):
        patient = baker.make(Patient)
        self.api.get(f"/api/v1/patient/{patient.external_id}/")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_setting('ihr.audit_ctx', true), "
                "current_setting('ihr.audit_actor', true)"
            )
            self.assertEqual(cursor.fetchone(), ("", ""))

    def test_purpose_is_inferred_from_the_api(self):
        self.assertEqual(purpose_of("/api/v1/facility/x/invoice/"), "billing")
        self.assertEqual(purpose_of("/api/v1/patient/x/"), "care")
        self.assertEqual(purpose_of("/api/v1/users/x/"), "admin")
        self.assertEqual(purpose_of("/api/ihr/profile/"), "admin")


class ReadNotingTests(AuditCase):
    def test_lists_encounters_and_files_count_as_shown(self):
        patients = baker.make(Patient, _quantity=3)
        other = baker.make(Patient)
        encounter = self.encounter(other)
        file_patient = baker.make(Patient)
        baker.make(
            "emr.FileUpload",
            file_type="patient",
            associating_id=str(file_patient.external_id),
        )
        ids = [p.pk for p in patients]
        token = reads.start()
        list(Patient.objects.filter(pk__in=ids))
        list(Encounter.objects.filter(pk=encounter.pk).only("id", "patient_id"))
        list(
            reads.apps.get_model("emr", "FileUpload").objects.filter(
                associating_id=str(file_patient.external_id)
            )
        )
        shown, total = reads.finish(token)
        self.assertEqual(shown, sorted([*ids, other.pk, file_patient.pk]))
        self.assertEqual(total, 5)

    def test_records_linked_through_another_record_count(self):
        patient = baker.make(Patient)
        facility = self.facility()
        account = baker.make("emr.Account", patient=patient, facility=facility)
        payment = baker.make(
            "emr.PaymentReconciliation",
            account=account,
            facility=facility,
            target_invoice=None,
        )
        token = reads.start()
        model = reads.apps.get_model("emr", "PaymentReconciliation")
        list(model.objects.filter(pk=payment.pk))
        self.assertIn(patient.pk, reads.finish(token)[0])

    def test_nothing_is_noted_outside_a_request(self):
        token = reads.start()
        reads.finish(token)
        list(Patient.objects.all()[:3])  # after finish: not noted, no error
        self.assertIsNone(reads._seen.get())

    def test_the_list_is_capped_and_the_total_kept(self):
        token = reads.start()
        seen = reads._seen.get()
        seen.patients.update(range(1, reads.MAX_PATIENTS + 50))
        shown, total = reads.finish(token)
        self.assertEqual(len(shown), reads.MAX_PATIENTS)
        self.assertEqual(total, reads.MAX_PATIENTS + 49)


class AppendOnlyTests(AuditCase):
    def test_entries_and_seals_cannot_be_changed_or_removed(self):
        baker.make(Patient)
        seal_once(TEST_SCHEMA)
        for statement in (
            "UPDATE ihr_audit_entry SET actor = 'x'",
            "DELETE FROM ihr_audit_entry",
            "TRUNCATE ihr_audit_entry",
            "UPDATE ihr_audit_seal SET entries = 1",
            "DELETE FROM ihr_audit_seal",
            "TRUNCATE ihr_audit_seal",
        ):
            with (
                self.subTest(statement=statement),
                self.assertRaises(DatabaseError),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)


class ChainTests(AuditCase):
    def test_sealed_chain_verifies(self):
        baker.make(Patient)
        seal_once(TEST_SCHEMA)
        problems, summary = verify(TEST_SCHEMA)
        self.assertEqual(problems, [])
        self.assertEqual(summary["unsealed_entries"], 0)
        self.assertGreater(summary["seals"], 0)

    def _tampered(self, statement):
        baker.make(Patient)
        seal_once(TEST_SCHEMA)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE ihr_audit_entry DISABLE TRIGGER ihr_audit_append_only"
                )
                cursor.execute(
                    "ALTER TABLE ihr_audit_seal DISABLE TRIGGER ihr_audit_append_only"
                )
                cursor.execute(statement)
            problems, _ = verify(TEST_SCHEMA)
            transaction.set_rollback(True)
        return problems

    def test_a_changed_entry_is_detected(self):
        problems = self._tampered(
            "UPDATE ihr_audit_entry SET actor = 'someone-else' "
            "WHERE id = (SELECT min(id) FROM ihr_audit_entry)"
        )
        self.assertTrue(any("entries changed" in p for p in problems), problems)

    def test_a_removed_entry_is_detected(self):
        problems = self._tampered(
            "DELETE FROM ihr_audit_entry WHERE id = (SELECT max(id) FROM ihr_audit_entry "
            "WHERE txid < (SELECT max(upper_txid) FROM ihr_audit_seal))"
        )
        self.assertTrue(any("sealed" in p and "now" in p for p in problems), problems)

    def test_an_entry_slipped_into_a_sealed_window_is_detected(self):
        problems = self._tampered(
            "INSERT INTO ihr_audit_entry (txid, kind, actor) VALUES ('3'::xid8, 'write', 'x')"
        )
        self.assertTrue(any("entries sealed" in p for p in problems), problems)

    def test_a_removed_or_rewritten_seal_is_detected(self):
        baker.make(Patient)
        seal_once(TEST_SCHEMA)  # at least two seals, so the first has a successor
        problems = self._tampered("DELETE FROM ihr_audit_seal WHERE seq = 1")
        self.assertTrue(any("a seal is missing" in p for p in problems), problems)
        self.assertTrue(any("starts at" in p for p in problems), problems)
        problems = self._tampered("UPDATE ihr_audit_seal SET hash = sha256(hash)")
        self.assertTrue(any("hash does not match" in p for p in problems), problems)
        # Cutting off the newest seals and entries together leaves a shorter, valid chain:
        # only the daily fingerprint kept outside the database catches that (P1-T6b).

    def test_entries_of_a_running_transaction_wait_for_it(self):
        """An entry written by a transaction that is still running when a seal is made
        must be sealed later, never skipped, however long that transaction runs."""
        started, release = threading.Event(), threading.Event()
        written = {}

        def slow_writer():
            try:
                with schema_context(TEST_SCHEMA), transaction.atomic():
                    written["pk"] = baker.make(Patient).pk
                    started.set()
                    release.wait(30)
            finally:
                connections["default"].close()

        worker = threading.Thread(target=slow_writer)
        worker.start()
        try:
            self.assertTrue(started.wait(30))
            baker.make(Patient)  # committed after the slow one started
            seal_once(TEST_SCHEMA)
            self.assertEqual(verify(TEST_SCHEMA)[0], [])
        finally:
            release.set()
            worker.join(30)
        seal_once(TEST_SCHEMA)
        problems, summary = verify(TEST_SCHEMA)
        self.assertEqual(problems, [])
        self.assertEqual(summary["unsealed_entries"], 0)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM ihr_audit_entry e WHERE e.tbl = 'emr_patient' "
                "AND e.row_pk = %s AND e.txid < (SELECT max(upper_txid) FROM ihr_audit_seal)",
                [str(written["pk"])],
            )
            self.assertEqual(cursor.fetchone()[0], 1)


class ReviewRegressionTests(AuditCase):
    """One test per confirmed finding of the P1-T6a review."""

    def test_a_huge_transaction_after_a_gap_does_not_stop_sealing(self):
        seal_once(TEST_SCHEMA)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT pg_current_xact_id()")  # a txid with no entries
        original = seal.BATCH
        seal.BATCH = 3
        try:
            with transaction.atomic():  # one transaction, more than BATCH entries
                baker.make(Patient, _quantity=5)
            baker.make(Patient)
            seal_once(TEST_SCHEMA)
        finally:
            seal.BATCH = original
        problems, summary = verify(TEST_SCHEMA)
        self.assertEqual(problems, [])
        self.assertEqual(summary["unsealed_entries"], 0)

    def test_a_task_keeps_its_actor_when_its_connection_is_replaced(self):
        task = SimpleNamespace(name="care.reconnect", request=SimpleNamespace(id="t-2"))
        context.begin_task(task)
        try:
            connection.close()  # what Celery's Django fixup does right after task start
            since = mark()
            baker.make(Patient)
            row = entries_since(since, tbl="emr_patient")[0]
        finally:
            context.end_task()
        self.assertEqual(row["actor"], "task:care.reconnect")

    def test_a_task_run_inside_a_request_gives_the_request_back(self):
        context.begin_request({"rid": "r-1", "via": "web"})
        try:
            context.bind_actor(self.admin)
            task = SimpleNamespace(name="care.eager", request=SimpleNamespace(id="t-3"))
            context.begin_task(task)
            since = mark()
            baker.make(Patient)
            inner = entries_since(since, tbl="emr_patient")[0]
            context.end_task()
            baker.make(Patient)
            after = entries_since(inner["id"], tbl="emr_patient")[0]
        finally:
            context.end()
        actor = f"user:{self.admin.pk}:{self.admin.username}"
        self.assertEqual(
            (inner["actor"], inner["context"]["task"]), (actor, "care.eager")
        )
        self.assertEqual((after["actor"], after["context"]["rid"]), (actor, "r-1"))
        self.assertNotIn("task", after["context"])

    def test_background_writes_name_the_process(self):
        since = mark()
        baker.make(Patient)
        self.assertEqual(entries_since(since)[0]["actor"], "system:manage:test")

    def test_staff_sign_in_writes_are_theirs(self):
        from ihr_identity import testing
        from ihr_platform.models import Hospital

        with schema_context("public"):
            hospital = testing.ensure_org_id(
                Hospital.objects.get(schema_name=TEST_SCHEMA)
            )
        staff = User.objects.create_user(
            username=f"stf-{time.time_ns()}"[:16], password="x-Unused-1"
        )
        try:
            with testing.fake_keycloak():
                client = testing.staff_client(hospital, HOST, staff)
                since = mark()
                response = client.get("/api/v1/users/getcurrentuser/")
            self.assertEqual(response.status_code, 200, response.content[:200])
            [row] = entries_since(since, tbl="users_user", row_pk=str(staff.pk))
            self.assertIn("last_login", row["data"])
            self.assertEqual(row["actor"], f"user:{staff.pk}:{staff.username}")
        finally:
            staff.delete()

    def test_rows_linked_through_another_record_name_the_patient(self):
        patient = baker.make(Patient)
        facility = self.facility()
        account = baker.make("emr.Account", patient=patient, facility=facility)
        thread = baker.make("emr.NoteThread", patient=patient, encounter=None)
        since = mark()
        baker.make(
            "emr.PaymentReconciliation",
            account=account,
            facility=facility,
            target_invoice=None,
        )
        baker.make("emr.NoteMessage", thread=thread)
        baker.make(
            "emr.FileUpload",
            file_type="patient",
            associating_id=str(patient.external_id),
        )
        baker.make("emr.FileUpload", file_type="patient", associating_id="not-a-uuid")
        rows = {
            r["tbl"]: r["patients"]
            for r in entries_since(since, op="I")
            if r["tbl"] in ("emr_paymentreconciliation", "emr_notemessage")
        }
        self.assertEqual(
            rows,
            {
                "emr_paymentreconciliation": [patient.pk],
                "emr_notemessage": [patient.pk],
            },
        )
        files = entries_since(since, tbl="emr_fileupload", op="I")
        self.assertEqual([f["patients"] for f in files], [[patient.pk], []])

    def test_models_registered_late_are_noted(self):
        thread = baker.make(
            "emr.NoteThread", patient=baker.make(Patient), encounter=None
        )
        message = baker.make("emr.NoteMessage", thread=thread)
        reads._LINKS.clear()  # as if these classes had never been seen
        token = reads.start()
        list(type(message).objects.filter(pk=message.pk))
        self.assertEqual(reads.finish(token)[0], [thread.patient_id])

    def test_installing_again_changes_nothing_and_stale_triggers_are_reported(self):
        self.assertEqual(ensure_triggers(connection, TEST_SCHEMA), 0)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "CREATE OR REPLACE TRIGGER ihr_audit AFTER INSERT ON emr_patient "
                    f"FOR EACH ROW EXECUTE FUNCTION {TEST_SCHEMA}.ihr_audit_row('', '', '', '')"
                )
            self.assertIn(
                f"{TEST_SCHEMA}.emr_patient: trigger ihr_audit is out of date (migrate)",
                coverage(connection, TEST_SCHEMA),
            )
            self.assertEqual(ensure_triggers(connection, TEST_SCHEMA), 1)
            self.assertEqual(coverage(connection, TEST_SCHEMA), [])
            transaction.set_rollback(True)

    def test_partitioned_tables_are_refused(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "CREATE TABLE ihr_parted (id bigint, at date) PARTITION BY RANGE (at)"
                )
            self.assertTrue(
                any(
                    "ihr_parted: partitioned" in p
                    for p in coverage(connection, TEST_SCHEMA)
                )
            )
            transaction.set_rollback(True)


class SecondReviewRegressionTests(AuditCase):
    """One test per confirmed finding of the second review round."""

    def test_a_quiet_hospital_with_one_fresh_write_is_not_an_alarm(self):
        seal_once(TEST_SCHEMA)
        baker.make(Patient)
        original = seal.SEALER_GRACE_SECONDS
        seal.SEALER_GRACE_SECONDS = 0  # as if the last seal were long ago
        try:
            self.assertEqual(verify(TEST_SCHEMA, stale_minutes=10)[0], [])
            late = verify(TEST_SCHEMA, stale_minutes=0)[0]
        finally:
            seal.SEALER_GRACE_SECONDS = original
            seal_once(TEST_SCHEMA)
        self.assertTrue(any("is the sealer running" in p for p in late), late)

    def test_a_function_changed_by_hand_is_reported_and_restored(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE OR REPLACE FUNCTION {TEST_SCHEMA}.ihr_audit_truncate() "
                    "RETURNS trigger LANGUAGE plpgsql "
                    "SET search_path = pg_catalog, pg_temp AS $$ BEGIN RETURN NULL; END $$"
                )
            self.assertIn(
                f"{TEST_SCHEMA}: audit functions are missing or were changed (migrate)",
                coverage(connection, TEST_SCHEMA),
            )
            ensure_triggers(connection, TEST_SCHEMA)
            self.assertEqual(coverage(connection, TEST_SCHEMA), [])
            transaction.set_rollback(True)

    def test_a_renamed_table_never_stops_a_write(self):
        thread = baker.make(
            "emr.NoteThread", patient=baker.make(Patient), encounter=None
        )
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE emr_notethread RENAME TO emr_notethread_old"
                )
            since = mark()
            baker.make("emr.NoteMessage", thread_id=thread.pk)
            [row] = entries_since(since, tbl="emr_notemessage")
            self.assertEqual(row["patients"], [])
            self.assertEqual(row["context"]["patient_lookup"], "failed")
            transaction.set_rollback(True)

    def test_an_idle_sign_out_is_the_persons_even_when_the_view_is_refused(self):
        from ihr_identity import testing
        from ihr_platform.models import Hospital

        with schema_context("public"):
            hospital = testing.ensure_org_id(
                Hospital.objects.get(schema_name=TEST_SCHEMA)
            )
        staff = User.objects.create_user(
            username=f"idl-{time.time_ns()}"[:16], password="x-Unused-1"
        )
        try:
            with testing.fake_keycloak():
                client = testing.staff_client(hospital, HOST, staff)
                client.get("/api/v1/users/getcurrentuser/")  # last_login written now
                since = mark()
                with self.settings(IHR_SESSION_IDLE_SECONDS=-1):
                    response = client.get("/api/v1/users/getcurrentuser/")
            self.assertEqual(response.status_code, 403)
            actor = f"user:{staff.pk}:{staff.username}"
            revoked = entries_since(since, tbl="ihr_identity_staffidentity", op="U")
            self.assertTrue(revoked)
            self.assertIn("sessions_valid_after", revoked[-1]["data"])
            self.assertEqual(revoked[-1]["actor"], actor)
            # A refused GET that showed no patient leaves no request entry (by design).
            self.assertEqual(entries_since(since, kind="request"), [])
        finally:
            staff.delete()

    def test_a_worker_job_names_whom_it_was_done_for(self):
        since = mark()
        with context.job(
            {"job": "staff_access", "request": "r-9"}, "worker:for user:abc"
        ):
            baker.make(Patient)
        baker.make(Patient)
        first, second = entries_since(since, tbl="emr_patient")
        self.assertEqual(first["actor"], "worker:for user:abc")
        self.assertEqual(first["context"]["request"], "r-9")
        self.assertTrue(second["actor"].startswith("system:"))
