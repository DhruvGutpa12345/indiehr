"""MRD records grants (P1-T11b4; design-b.md §7, "After the attack" §8, tests 21-23 and the b3
list of §9 test 14).

- the endpoint and the dev command's one path (records.create_grant) and end routine
  (records.end_grant), the reasons and references of Q-K36 A, the log line;
- the grant authorizer (authz.RecordsGrantAuthorizer): each of CARE's read checks it wraps
  opens that patient's rows with a grant, never another patient's, never after the grant ends,
  never a write;
- expiry before the beat (per request) and by the beat, one ``expired`` line per grant;
- signed file links under a grant live at most min(seconds left, 300);
- the request entry of a read under a grant carries ``purpose: records`` and the grant id.
"""

import datetime
import json
import re
from types import SimpleNamespace
from unittest import mock

from care.emr.models import Patient, PatientUser
from care.users.models import User
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from ihr_roles import registry

from ihr_access import authz, records, state, tasks
from ihr_access.models import RecordsGrant
from ihr_access.testing import CANARY, LAB, build_world, grant_records
from ihr_access.tests.base import SwapCase

# The whole line the alerts read (deploy/logs/alerts/records.yml)
LINE = re.compile(
    r"^ihr-records: hospital=[a-z][a-z0-9-]{2,30} grant=(?P<grant>[0-9]{1,18}|none) "
    r"user=(?P<user>[0-9]{1,18}|none) reason=(?P<reason>patient_request|legal|insurer|"
    r"internal_audit|nabh_review|continuity_of_care|invalid|none) "
    r"outcome=(?P<outcome>granted|ended|expired|refused) hour=(?P<hour>[01][0-9]|2[0-3])$"
)


def mark():
    with connection.cursor() as cursor:
        cursor.execute("SELECT coalesce(max(id), 0) FROM ihr_audit_entry")
        return cursor.fetchone()[0]


def entries_since(since, **where):
    clauses, params = ["id > %s"], [since]
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
    for row in rows:
        for column in ("context", "data"):
            if isinstance(row[column], str):
                row[column] = json.loads(row[column])
    return rows


class RecordsCase(SwapCase):
    """Log lines are captured (records.emit), never written to the test's standard error."""

    def setUp(self):
        super().setUp()
        self.lines = []
        emitting = mock.patch.object(records, "emit", self.lines.append)
        emitting.start()
        self.addCleanup(emitting.stop)

    def outcomes(self):
        found = []
        for line in self.lines:
            match = LINE.match(line)
            self.assertIsNotNone(match, line)
            found.append((match["outcome"], match["grant"], match["reason"]))
        return found

    def hidden_rows(self, user, patient=None):
        rows = PatientUser.objects.filter(
            user=user, role__name=registry.HIDDEN, role__is_system=True
        )
        return rows.filter(patient=patient) if patient is not None else rows

    def ask(self, who, patient, reason="insurer", reference="TEST-1"):
        return self.api(who).post(
            "/api/ihr/records-access/",
            {
                "patient": str(getattr(patient, "external_id", patient)),
                "reason": reason,
                "reference": reference,
            },
            format="json",
        )

    def expire(self, grant):
        """Time moves past the grant's end (its row, as the clock would see it)."""
        RecordsGrant.objects.filter(pk=grant.pk).update(
            expires_at=timezone.now() - datetime.timedelta(seconds=1)
        )


class GrantFlow(RecordsCase):
    """The endpoint (POST, GET, DELETE) and the rules of Q-K36 A."""

    def test_mrd_asks_reads_and_ends(self):
        w = self.world
        mrd = w.users["MRD"]
        clinical = f"/api/v1/patient/{w.patient.external_id}/diagnosis/"
        self.assertEqual(self.api(mrd).get(clinical).status_code, 403)
        with self.captureOnCommitCallbacks(execute=True):
            asked = self.ask(mrd, w.patient)
        self.assertEqual(asked.status_code, 201, asked.content[:300])
        body = asked.json()
        grant = RecordsGrant.objects.get(pk=body["id"])
        self.assertEqual((grant.reason, grant.reference), ("insurer", "TEST-1"))
        self.assertEqual(body["patient"], str(w.patient.external_id))
        self.assertAlmostEqual(
            (grant.expires_at - grant.created_at).total_seconds(), 30 * 60, delta=1
        )
        self.assertEqual(self.hidden_rows(mrd, w.patient).count(), 1)
        self.assertIn(mrd.id, Patient.objects.get(pk=w.patient.pk).users_cache)
        self.assertEqual(self.api(mrd).get(clinical).status_code, 200)
        listed = self.api(mrd).get("/api/ihr/records-access/").json()["results"]
        self.assertEqual([g["id"] for g in listed], [grant.pk])
        self.assertEqual(
            self.api("Receptionist").get("/api/ihr/records-access/").json(),
            {"results": [], "grant_minutes": records.grant_minutes()},
        )
        with self.captureOnCommitCallbacks(execute=True):
            ended = self.api(mrd).delete(f"/api/ihr/records-access/{grant.pk}/")
        self.assertEqual(ended.status_code, 204)
        grant.refresh_from_db()
        self.assertIsNotNone(grant.ended_at)
        self.assertFalse(self.hidden_rows(mrd).exists())
        self.assertNotIn(mrd.id, Patient.objects.get(pk=w.patient.pk).users_cache)
        self.assertEqual(self.api(mrd).get(clinical).status_code, 403)
        self.assertEqual(
            self.api(mrd).delete(f"/api/ihr/records-access/{grant.pk}/").status_code,
            404,
        )
        self.assertEqual(
            self.outcomes(),
            [
                ("granted", str(grant.pk), "insurer"),
                ("ended", str(grant.pk), "insurer"),
            ],
        )

    def test_reasons_and_references(self):
        """Q-K36 A: a reason code; legal and insurer need a reference (a case or claim number,
        never free text); one live grant per patient; an unknown patient is refused. Each
        refusal writes one ``refused`` line that echoes nothing from the request."""
        w = self.world
        mrd = w.users["MRD"]
        cases = (
            ({"reason": ""}, "none"),
            ({"reason": "curious <script>"}, "invalid"),
            ({"reason": "legal", "reference": ""}, "legal"),
            ({"reason": "insurer", "reference": "  "}, "insurer"),
            ({"reason": "insurer", "reference": "x" * 101}, "insurer"),
            ({"reason": "insurer", "reference": "claim\n+91 98765 43210"}, "insurer"),
            ({"reason": "nabh_review", "patient": "not-a-patient"}, "nabh_review"),
        )
        for change, logged in cases:
            with self.subTest(change=change):
                self.lines.clear()
                body = {
                    "patient": str(w.patient.external_id),
                    "reference": "",
                    **change,
                }
                answer = self.api(mrd).post(
                    "/api/ihr/records-access/", body, format="json"
                )
                self.assertEqual(answer.status_code, 400, answer.content[:300])
                self.assertEqual(self.outcomes(), [("refused", "none", logged)])
                self.assertNotIn("script", self.lines[0])
                self.assertNotIn("98765", self.lines[0])
        self.assertFalse(RecordsGrant.objects.filter(user=mrd).exists())
        for reason in ("patient_request", "internal_audit"):  # no reference needed
            with self.subTest(reason=reason):
                patient, _ = self.other_patient()
                self.assertEqual(self.ask(mrd, patient, reason, "").status_code, 201)
        twice = self.ask(mrd, patient)
        self.assertEqual(twice.status_code, 400, twice.content[:300])
        self.assertIn("end it first", twice.json()["detail"])

    def test_only_the_records_role_may_ask(self):
        w = self.world
        for who in (*registry.EIGHT, "Doctor", "Nurse", "Facility Admin"):
            if who == "MRD":
                continue
            with self.subTest(who=who):
                self.lines.clear()
                answer = self.ask(who, w.patient)
                self.assertEqual(answer.status_code, 403, answer.content[:300])
                user = w.users[who]
                self.assertEqual(
                    self.outcomes(), [("refused", "none", "insurer")], self.lines
                )
                self.assertIn(f" user={user.pk} ", self.lines[0])
        self.assertFalse(RecordsGrant.objects.exists())

    def test_only_its_holder_or_a_hospital_admin_ends_a_grant(self):
        from ihr_identity.staff_access import hospital_wide_admin

        w = self.world
        mrd = w.users["MRD"]
        other = w.user("acc-mrd-2", ("MRD", w.root))
        grant = grant_records(w, mrd, w.patient)
        for who in (other, "Receptionist", "Doctor"):
            with self.subTest(who=who):
                answer = self.api(who).delete(f"/api/ihr/records-access/{grant.pk}/")
                self.assertEqual(answer.status_code, 404)
        self.assertFalse(
            self.api(other).get("/api/ihr/records-access/").json()["results"]
        )
        admin = w.users["Facility Admin"]
        answer = self.api(admin).delete(f"/api/ihr/records-access/{grant.pk}/")
        grant.refresh_from_db()
        if hospital_wide_admin(admin):
            self.assertEqual(answer.status_code, 204)
            self.assertIsNotNone(grant.ended_at)
        else:  # the world's admin is not a hospital-wide one: the holder ends it
            self.assertEqual(answer.status_code, 404)
            self.assertEqual(
                self.api(mrd)
                .delete(f"/api/ihr/records-access/{grant.pk}/")
                .status_code,
                204,
            )

    def test_grant_and_patient_user_changes_are_in_the_trail(self):
        w = self.world
        mrd = w.users["MRD"]
        since = mark()
        grant = RecordsGrant.objects.get(pk=self.ask(mrd, w.patient).json()["id"])
        self.api(mrd).delete(f"/api/ihr/records-access/{grant.pk}/")
        grants = entries_since(since, tbl="ihr_records_grant", row_pk=str(grant.pk))
        self.assertEqual([e["op"] for e in grants], ["I", "U"])
        rows = entries_since(since, tbl="emr_patientuser")
        self.assertEqual([e["op"] for e in rows], ["I", "D"])
        for entry in grants + rows:
            self.assertEqual(entry["actor"], f"user:{mrd.pk}:{mrd.username}")
            self.assertEqual(entry["patients"], [w.patient.pk])
            self.assertEqual(entry["context"]["purpose"], "records")
        # Each request entry (asking, ending) names the grant it was made for or under
        asked, ended = entries_since(since, kind="request")
        self.assertEqual(asked["context"]["grants"], [grant.pk])
        self.assertEqual(ended["context"]["grants"], [grant.pk])
        self.assertEqual(asked["patients"], [w.patient.pk])

    def test_the_dev_command_calls_the_same_code(self):
        from io import StringIO

        from django_tenants.utils import schema_context
        from ihr_platform.models import Hospital

        w = self.world
        mrd = w.users["MRD"]
        schema = connection.schema_name
        with schema_context("public"):
            hospital = Hospital.objects.get(schema_name=schema)
        out = StringIO()
        with override_settings(DEBUG=True, IHR_TENANT_DOMAIN_SUFFIX="localhost"):
            call_command(
                "ihr_records_access",
                mrd.username,
                str(w.patient.external_id),
                slug=hospital.slug,
                reason="legal",
                reference="CASE-12",
                stdout=out,
            )
            grant = RecordsGrant.objects.get(user=mrd)
            self.assertIn(f"Records grant {grant.pk}", out.getvalue())
            self.assertEqual(self.hidden_rows(mrd, w.patient).count(), 1)
            with self.assertRaises(
                CommandError
            ):  # the same rules: legal needs a reference
                call_command(
                    "ihr_records_access",
                    mrd.username,
                    str(w.patient.external_id),
                    slug=hospital.slug,
                    reason="legal",
                )
            with self.assertRaises(CommandError):  # Receptionist: not theirs to ask
                call_command(
                    "ihr_records_access",
                    w.users["Receptionist"].username,
                    str(w.patient.external_id),
                    slug=hospital.slug,
                    reason="insurer",
                    reference="X1",
                )
            call_command(
                "ihr_records_access",
                mrd.username,
                slug=hospital.slug,
                end=grant.pk,
                stdout=out,
            )
        grant.refresh_from_db()
        self.assertIsNotNone(grant.ended_at)
        self.assertFalse(self.hidden_rows(mrd).exists())
        with (
            override_settings(DEBUG=False),
            self.assertRaisesMessage(CommandError, "Development only"),
        ):
            call_command(
                "ihr_records_access", mrd.username, end=grant.pk, slug=hospital.slug
            )

    def test_the_line_names_no_one(self):
        line = records.log_line("granted", 17, 5, "insurer")
        self.assertRegex(line, LINE)
        self.assertRegex(records.log_line("refused", None, 5, "forged words"), LINE)
        self.assertIn("reason=invalid", records.log_line("refused", None, 5, "x y"))
        self.assertIn("grant=none user=none", records.log_line("refused"))
        night = datetime.datetime(2026, 11, 14, 17, 0, tzinfo=datetime.UTC)  # 22:30 IST
        self.assertTrue(
            records.log_line("granted", 1, 1, "legal", night).endswith("hour=22")
        )


class GrantList(RecordsCase):
    """P1-T11c3 (design-c.md §13.4, SS5 and SS7): the grant body names the patient (name, year
    of birth) for the MRD's own list and the POST answer; the list says how long a new grant
    lasts; it lists only the caller's grants; listing reads no record (no grant id in its
    request entry). Names only with ``?names=1`` (the records page): the grant watcher's poll is
    ids and times and shows no patient (review BA1)."""

    KEYS = frozenset(
        {
            "id",
            "patient",
            "patient_name",
            "patient_year_of_birth",
            "reason",
            "reference",
            "created_at",
            "expires_at",
            "seconds_left",
        }
    )
    POLL_KEYS = frozenset({"id", "patient", "expires_at", "seconds_left"})

    def test_list_and_create_name_the_patient(self):
        w = self.world
        mrd = w.users["MRD"]
        patient = Patient.objects.get(pk=w.patient.pk)
        self.assertIsInstance(patient.year_of_birth, int)
        asked = self.ask(mrd, w.patient)
        self.assertEqual(asked.status_code, 201, asked.content[:300])
        listed = self.api(mrd).get("/api/ihr/records-access/?names=1").json()
        self.assertEqual(set(listed), {"results", "grant_minutes"})
        [item] = listed["results"]
        for body in (asked.json(), item):
            with self.subTest(body=body["id"]):
                self.assertEqual(set(body), self.KEYS)
                self.assertEqual(body["patient"], str(patient.external_id))
                self.assertEqual(body["patient_name"], patient.name)
                self.assertEqual(body["patient_year_of_birth"], patient.year_of_birth)
        self.assertEqual(item["id"], asked.json()["id"])

    def test_a_patient_without_a_year_of_birth_is_null(self):
        from ihr_access.endpoints import grant_body

        now = timezone.now()
        stub = SimpleNamespace(
            pk=7,
            patient=SimpleNamespace(external_id="p-1", name="A B", year_of_birth=None),
            reason="insurer",
            reference="TEST-1",
            created_at=now,
            expires_at=now + datetime.timedelta(minutes=5),
        )
        body = grant_body(stub, now)
        self.assertEqual(set(body), self.KEYS)
        self.assertIsNone(body["patient_year_of_birth"])
        self.assertEqual(body["patient_name"], "A B")
        self.assertEqual(body["seconds_left"], 300)

    def test_grant_minutes_is_the_setting(self):
        w = self.world
        mrd = w.users["MRD"]
        answer = self.api(mrd).get("/api/ihr/records-access/").json()
        self.assertEqual(answer["grant_minutes"], records.grant_minutes())
        with override_settings(IHR_RECORDS_GRANT_MINUTES=45):
            for who in (mrd, "Receptionist"):
                with self.subTest(who=who):
                    answer = self.api(who).get("/api/ihr/records-access/").json()
                    self.assertEqual(answer["grant_minutes"], 45)
            asked = self.ask(mrd, w.patient)
            self.assertEqual(asked.status_code, 201, asked.content[:300])
        grant = RecordsGrant.objects.get(pk=asked.json()["id"])
        self.assertAlmostEqual(
            (grant.expires_at - grant.created_at).total_seconds(), 45 * 60, delta=1
        )
        self.assertAlmostEqual(asked.json()["seconds_left"], 45 * 60, delta=2)

    def test_another_users_grant_never_appears(self):
        w = self.world
        mrd = w.users["MRD"]
        other = w.user("acc-mrd-2", ("MRD", w.root))
        second, _visit = self.other_patient()
        mine = grant_records(w, mrd, w.patient)
        theirs = grant_records(w, other, second, reason="patient_request", reference="")
        same = grant_records(w, other, w.patient, reason="nabh_review", reference="")
        expected = {mrd: [mine.pk], other: sorted([theirs.pk, same.pk])}
        for who, ids in expected.items():
            for path in (
                "/api/ihr/records-access/",
                "/api/ihr/records-access/?names=1",
            ):
                with self.subTest(who=who.username, path=path):
                    listed = self.api(who).get(path).json()["results"]
                    self.assertEqual(sorted(g["id"] for g in listed), ids)
        names = {
            g["patient_name"]
            for g in self.api(mrd)
            .get("/api/ihr/records-access/?names=1")
            .json()["results"]
        }
        self.assertEqual(names, {Patient.objects.get(pk=w.patient.pk).name})
        # A hospital-wide admin may end anyone's grant (DELETE) but lists only their own
        admin = w.users["Facility Admin"]
        self.assertEqual(
            self.api(admin).get("/api/ihr/records-access/").json()["results"], []
        )

    def test_listing_reads_no_record(self):
        """The list with names (``?names=1``, the records-access page) shows each granted
        patient's name and year of birth: its request entry lists them as shown (patients),
        with the path's own purpose (``records``, the records-access screen:
        ihr_audit.middleware.PURPOSES), and no grant id; a read of the patient right after
        still names the grant."""
        w = self.world
        mrd = w.users["MRD"]
        grant = grant_records(w, mrd, w.patient)
        since = mark()
        answer = self.api(mrd).get("/api/ihr/records-access/?names=1")
        self.assertEqual(answer.status_code, 200)
        self.assertEqual([g["id"] for g in answer.json()["results"]], [grant.pk])
        self.assertEqual(entries_since(since, kind="write"), [])
        [entry] = entries_since(since, kind="request")
        self.assertEqual(entry["patients"], [w.patient.pk])
        self.assertNotIn("grants", entry["context"])
        self.assertEqual(entry["context"]["purpose"], "records")  # the path's
        self.assertEqual(entry["actor"], f"user:{mrd.pk}:{mrd.username}")
        self.assertEqual(entry["data"], {"status": 200})
        since = mark()
        url = f"/api/v1/patient/{w.patient.external_id}/"
        self.assertEqual(self.api(mrd).get(url).status_code, 200)
        [read] = entries_since(since, kind="request")
        self.assertEqual(read["context"]["purpose"], "records")
        self.assertEqual(read["context"]["grants"], [grant.pk])

    def test_the_watchers_poll_shows_no_patient(self):
        """The grant watcher's poll (no ``names``; every 30 seconds on every page an MRD has
        open) answers ids and times only and loads no grant or patient: its request shows no
        patient, so it writes no audit entry (review BA1). ``names`` other than ``1`` is the
        same poll."""
        w = self.world
        mrd = w.users["MRD"]
        grant = grant_records(w, mrd, w.patient)
        external = str(Patient.objects.get(pk=w.patient.pk).external_id)
        for path in (
            "/api/ihr/records-access/",
            "/api/ihr/records-access/?names=0",
            "/api/ihr/records-access/?names=yes",
        ):
            with self.subTest(path=path):
                since = mark()
                answer = self.api(mrd).get(path)
                self.assertEqual(answer.status_code, 200)
                body = answer.json()
                self.assertEqual(set(body), {"results", "grant_minutes"})
                [item] = body["results"]
                self.assertEqual(set(item), self.POLL_KEYS)
                self.assertEqual(item["id"], grant.pk)
                self.assertEqual(item["patient"], external)
                self.assertEqual(item["expires_at"], grant.expires_at.isoformat())
                self.assertGreater(item["seconds_left"], 0)
                self.assertEqual(entries_since(since, kind="request"), [])
                self.assertEqual(entries_since(since, kind="write"), [])

    def test_withdrawing_drops_only_its_own_note(self):
        from ihr_audit import context

        context.begin_request({"purpose": "care"})
        try:
            context.annotate_patients("grants", {11: 7}, purpose="records")
            context.withdraw_patients("other")
            self.assertEqual(
                context.patient_fields([11]), {"purpose": "records", "grants": [7]}
            )
            context.withdraw_patients("grants")
            self.assertEqual(context.patient_fields([11]), {})
            context.annotate_patients("grants", {11: 8}, purpose="records")
            self.assertEqual(
                context.patient_fields([11]), {"purpose": "records", "grants": [8]}
            )
        finally:
            context.end()
        context.withdraw_patients("grants")  # outside a request: nothing to drop


class GrantedReads(RecordsCase):
    """Test 21: each of CARE's read checks the grant authorizer wraps (item 8.1), on the full
    world: 200 with a grant on the patient, 403 without, 403 with a grant on another patient,
    403 after the grant ends; writes stay refused."""

    @classmethod
    def setUpTestData(cls):
        cls.world = build_world(full=True)

    def routes(self):
        w, o = self.world, self.world.objects
        f = f"/api/v1/facility/{w.facility.external_id}"
        p = f"/api/v1/patient/{w.patient.external_id}"
        visit = {"encounter": str(w.encounter.external_id)}
        return {
            "can_read_service_request": (
                f"{f}/service_request/{o['service_request'].external_id}/",
                {},
            ),
            "can_view_service_request_for_encounter": (f"{f}/service_request/", visit),
            "can_read_specimen": (f"{f}/specimen/{o['specimen'].external_id}/", {}),
            "can_read_diagnostic_report": (
                f"{p}/diagnostic_report/{o['diagnostic_report'].external_id}/",
                {},
            ),
            "can_read_diagnostic_report_in_encounter": (
                f"{p}/diagnostic_report/",
                visit,
            ),
            "can_view_encounter_obj": (
                f"{p}/medication/prescription/{o['prescription'].external_id}/",
                {},
            ),
        }

    def answers(self, who):
        found = {}
        for name, (url, query) in self.routes().items():
            response = self.api(who).get(url, query)
            found[name] = response.status_code
            if response.status_code == 200:
                text = response.content.decode()
                self.assertIn(CANARY, text, name)  # the record itself, unprojected
        return found

    def test_every_wrapped_read_is_a_route(self):
        self.assertEqual(set(self.routes()), set(authz.GRANT_READS))

    def test_a_grant_opens_its_patients_requests_reports_and_prescriptions(self):
        w = self.world
        mrd = w.users["MRD"]
        self.assertEqual(set(self.answers(mrd).values()), {403})
        grant = grant_records(w, mrd, w.patient)
        state.end()
        self.assertEqual(self.answers(mrd), dict.fromkeys(authz.GRANT_READS, 200))
        records.end_grant(grant, "ended")
        state.end()
        self.assertEqual(set(self.answers(mrd).values()), {403})

    def test_grant_does_not_open_other_patient_reports(self):
        """A grant on the Radiology patient opens nothing of the first patient's (the
        authorizer keys on the object's patient: mutation records-grant-scope)."""
        w = self.world
        mrd = w.users["MRD"]
        grant_records(w, mrd, w.objects["patient_b"])
        self.assertEqual(self.answers(mrd), dict.fromkeys(authz.GRANT_READS, 403))
        # and a visit of the first patient named under the granted patient's path is refused
        # by the visit check (ihr_access/visits.py)
        crossed = self.api(mrd).get(
            f"/api/v1/patient/{w.objects['patient_b'].external_id}/diagnostic_report/",
            {"encounter": str(w.encounter.external_id)},
        )
        self.assertEqual(crossed.status_code, 403)

    def test_an_ended_or_expired_grant_opens_nothing(self):
        w = self.world
        mrd = w.users["MRD"]
        grant = grant_records(w, mrd, w.patient)
        self.expire(grant)
        self.assertEqual(set(self.answers(mrd).values()), {403})

    def test_writes_stay_refused_with_a_grant(self):
        w, o = self.world, self.world.objects
        mrd = w.users["MRD"]
        grant_records(w, mrd, w.patient)
        f = f"/api/v1/facility/{w.facility.external_id}"
        p = f"/api/v1/patient/{w.patient.external_id}"
        report = o["diagnostic_report"]
        type(report).objects.filter(pk=report.pk).update(status="preliminary")
        writes = (
            ("put", f"{p}/diagnostic_report/{report.external_id}/", {"status": "preliminary", "category": LAB, "conclusion": "edited"}),
            ("post", f"{p}/diagnostic_report/", {"status": "preliminary", "category": LAB, "service_request": str(o["service_request"].external_id)}),
            ("post", f"{f}/service_request/{o['service_request'].external_id}/cancel/", {"reason": "entered_in_error"}),
            ("post", f"{p}/thread/", {"title": "x", "encounter": str(w.encounter.external_id)}),
            ("put", f"{p}/", {"name": "Renamed"}),
        )  # fmt: skip
        for method, url, body in writes:
            with self.subTest(url=url):
                answer = getattr(self.api(mrd), method)(url, body, format="json")
                self.assertIn(answer.status_code, (400, 403, 404), answer.content[:300])
                if (
                    answer.status_code == 400
                ):  # validated first: never a permission leak
                    self.assertNotIn(CANARY, answer.content.decode())
        report.refresh_from_db()
        self.assertNotEqual(report.conclusion, "edited")

    def test_updates_under_a_grant_are_refused_by_authorization(self):
        """role_matrix_writes.txt: where MRD with a grant answers an empty write with 400 (CARE
        found the granted patient's row, then validated the body first) and MRD without one
        with 403, CARE's own authorization of that write, given the row, still refuses MRD with
        a grant: a valid body would be refused too (review M3's question, for the grant)."""
        from django.urls import resolve
        from rest_framework.exceptions import PermissionDenied
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory, force_authenticate

        from ihr_access.tests import matrix

        w = self.world
        mrdg = w.users["MRD Granted"]  # the full world's grant on the first patient
        rows = matrix.read_matrix()
        targets = {
            (method.upper(), key): path
            for key, method, path in matrix.write_targets(w, rows)
        }
        cells = [
            target
            for target, found in matrix.read_writes().items()
            if found.get("mrdg") == "body" and found.get("mrd") == "auth"
        ]
        self.assertTrue(cells)
        factory = APIRequestFactory()
        for method, key in cells:
            with self.subTest(method=method, key=key):
                path = targets[(method, key)]
                match = resolve(path)
                view = match.func.cls(
                    action_map=getattr(match.func, "actions", None) or {}
                )
                raw = getattr(factory, method.lower())(path, {}, format="json")
                force_authenticate(raw, user=mrdg)
                view.request = Request(raw)
                view.request.user = mrdg
                view.args, view.kwargs, view.format_kwarg = (), match.kwargs, None
                view.action = (getattr(match.func, "actions", None) or {}).get(
                    method.lower()
                )
                instance = view.get_object()
                self.assertEqual(
                    getattr(instance, "patient_id", w.patient.pk) or w.patient.pk,
                    w.patient.pk,
                )
                with self.assertRaises(PermissionDenied):
                    if (
                        view.action == "archive"
                    ):  # FileUploadViewSet.archive's own check
                        from care.emr.api.viewsets.file_upload import file_authorizer

                        file_authorizer(
                            mrdg, instance.file_type, instance.associating_id, "write"
                        )
                    else:
                        # the visit a body would name: the row's own (allergies authorize on
                        # the body's visit, CARE's allergy_intolerance.authorize_update)
                        encounter = getattr(instance, "encounter", None)
                        body = SimpleNamespace(
                            encounter=getattr(encounter, "external_id", None)
                        )
                        view.authorize_update(body, instance)

    def test_expired_grant_is_refused_before_the_beat(self):
        """The first request after the end ends the grant itself (one indexed query in the
        authentication hook): the row closes, the PatientUser row goes, one ``expired`` line
        on commit; the clinical read is refused. The beat then finds nothing to do."""
        w = self.world
        mrd = w.users["MRD"]
        grant = grant_records(w, mrd, w.patient)
        self.expire(grant)
        self.assertTrue(self.hidden_rows(mrd).exists())
        with self.captureOnCommitCallbacks(execute=True):
            # demographics only (200): the request commits, so its ending of the grant does
            opened = self.api(mrd).get(f"/api/v1/patient/{w.patient.external_id}/")
        self.assertEqual(opened.status_code, 200)
        self.assertIsNone(opened.json()["blood_group"])  # projected: no grant now
        grant.refresh_from_db()
        self.assertIsNotNone(grant.ended_at)
        self.assertFalse(self.hidden_rows(mrd).exists())
        self.assertEqual(self.outcomes(), [("expired", str(grant.pk), "insurer")])
        self.assertEqual(set(self.answers(mrd).values()), {403})
        with self.captureOnCommitCallbacks(execute=True):
            tasks.expire_grants()
        self.assertEqual(len(self.lines), 1)

    def test_a_refused_request_still_ends_its_expired_grants(self):
        """Review C3: the request's ending of its expired grants runs after the view, in its own
        short transaction (RecordsExpiryMiddleware), so a refused request (403, rolled back
        under ATOMIC_REQUESTS) no longer undoes it, and no grant or patient row lock is held
        while the view runs. One ``expired`` line per grant, in id order; the beat then finds
        nothing to do."""
        w = self.world
        mrd = w.users["MRD"]
        first = grant_records(w, mrd, w.patient)
        second = grant_records(w, mrd, w.objects["patient_b"])
        self.expire(first)
        self.expire(second)
        self.lines.clear()
        # As deployed (CARE's base settings; its test settings leave it out): one transaction
        # per request, which a refusal rolls back (DRF's set_rollback)
        with (
            mock.patch.dict(connection.settings_dict, {"ATOMIC_REQUESTS": True}),
            self.captureOnCommitCallbacks(execute=True),
        ):
            refused = self.api(mrd).get(
                f"/api/v1/patient/{w.patient.external_id}/diagnosis/"
            )
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(
            self.outcomes(),
            [
                ("expired", str(first.pk), "insurer"),
                ("expired", str(second.pk), "insurer"),
            ],
        )
        self.assertFalse(self.hidden_rows(mrd).exists())
        self.assertFalse(
            RecordsGrant.objects.filter(user=mrd, ended_at__isnull=True).exists()
        )
        with self.captureOnCommitCallbacks(execute=True):
            tasks.expire_grants()
        self.assertEqual(len(self.lines), 2)

    def test_the_request_hook_ends_nothing_itself(self):
        """Review C3: records.on_request only notes expired grants (no lock, no write inside
        the view's transaction); without the middleware around it nothing is ended."""
        w = self.world
        mrd = w.users["MRD"]
        grant = grant_records(w, mrd, w.patient)
        self.expire(grant)
        state.begin(mrd)
        self.addCleanup(state.end)
        token = records.start_due()
        with CaptureQueriesContext(connection) as seen:
            records.on_request(mrd)
        for query in seen.captured_queries:
            sql = query["sql"].lstrip().upper()
            self.assertNotIn("FOR UPDATE", sql)
            self.assertTrue(sql.startswith(("SELECT", "SET SEARCH_PATH")), query)
        grant.refresh_from_db()
        self.assertIsNone(grant.ended_at)
        self.assertEqual(state.current().grants, {})
        with self.captureOnCommitCallbacks(execute=True):
            records.end_due(token)
        grant.refresh_from_db()
        self.assertIsNotNone(grant.ended_at)
        self.assertEqual(self.outcomes(), [("expired", str(grant.pk), "insurer")])

    def test_the_request_hook_gives_an_unsaved_user_nothing(self):
        """CARE's SimpleTestCase previews authenticate an in-memory User that was never saved:
        it is no row, so the hook reads nothing and gives it no grant, even with the pk of a
        person holding one; that person still gets theirs."""
        w = self.world
        mrd = w.users["MRD"]
        grant_records(w, mrd, w.patient)
        ghost = User(pk=mrd.pk, username="ghost")
        state.begin(ghost)
        self.addCleanup(state.end)
        with self.assertNumQueries(0):
            records.on_request(ghost)
        self.assertEqual(state.current().grants, {})
        state.begin(mrd)
        records.on_request(mrd)
        self.assertEqual(set(state.current().grants), {w.patient.pk})

    def test_the_beat_ends_each_grant_once(self):
        """The beat ends each expired grant, removes the PatientUser row and logs one
        ``expired`` line; a second run logs nothing."""
        w = self.world
        mrd = w.users["MRD"]
        first = grant_records(w, mrd, w.patient)
        second = grant_records(w, mrd, w.objects["patient_b"])
        self.expire(first)
        self.expire(second)
        self.lines.clear()
        self.assertEqual(self.hidden_rows(mrd).count(), 2)
        with self.captureOnCommitCallbacks(execute=True):
            tasks.expire_grants()
        self.assertEqual(
            self.outcomes(),
            [
                ("expired", str(first.pk), "insurer"),
                ("expired", str(second.pk), "insurer"),
            ],
        )
        self.assertFalse(self.hidden_rows(mrd).exists())
        self.assertEqual(
            RecordsGrant.objects.filter(user=mrd, ended_at__isnull=True).count(), 0
        )
        with self.captureOnCommitCallbacks(execute=True):
            tasks.expire_grants()
        self.assertEqual(len(self.lines), 2)

    def test_a_new_grant_ends_the_lapsed_one_first(self):
        """Review G1: a grant past its time but not yet ended (no request or beat since) is
        ended, with its own ``expired`` line, by the next grant on the same patient (the dev
        command runs no request hook), so its later end cannot take the new grant's row."""
        w = self.world
        mrd = w.users["MRD"]
        with self.captureOnCommitCallbacks(execute=True):
            old = records.create_grant(mrd, w.patient, "insurer", "TEST-1")
        self.expire(old)
        with self.captureOnCommitCallbacks(execute=True):
            new = records.create_grant(mrd, w.patient, "legal", "CASE-2")
        old.refresh_from_db()
        self.assertIsNotNone(old.ended_at)
        self.assertEqual(
            self.outcomes(),
            [
                ("granted", str(old.pk), "insurer"),
                ("expired", str(old.pk), "insurer"),
                ("granted", str(new.pk), "legal"),
            ],
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(records.expire_due(), 0)
        self.assertEqual(self.hidden_rows(mrd, w.patient).count(), 1)
        self.assertIn(mrd.pk, Patient.objects.get(pk=w.patient.pk).users_cache)
        self.assertEqual(len(self.lines), 3)

    def test_ending_a_grant_keeps_the_row_another_live_grant_needs(self):
        """Review G1 (backstop): end_grant removes the shared hidden-role row only when no
        other live grant of that user on that patient remains."""
        w = self.world
        mrd = w.users["MRD"]
        old = grant_records(w, mrd, w.patient)
        self.expire(old)
        now = timezone.now()
        live = (
            RecordsGrant.objects.create(  # stacked by some other path than create_grant
                patient=w.patient,
                user=mrd,
                reason="insurer",
                reference="TEST-3",
                created_at=now,
                expires_at=now + datetime.timedelta(minutes=10),
            )
        )
        self.assertTrue(records.end_grant(old, "expired"))
        self.assertEqual(self.hidden_rows(mrd, w.patient).count(), 1)
        self.assertIn(mrd.pk, Patient.objects.get(pk=w.patient.pk).users_cache)
        self.assertTrue(records.end_grant(live, "ended"))
        self.assertFalse(self.hidden_rows(mrd, w.patient).exists())
        self.assertNotIn(mrd.pk, Patient.objects.get(pk=w.patient.pk).users_cache)

    def test_a_grant_never_writes_back_a_stale_patient(self):
        """Review G2: the patient as loaded before the grant's transaction is never saved
        whole: a demographics edit committed in between stays."""
        w = self.world
        mrd = w.users["MRD"]
        stale = Patient.objects.get(pk=w.patient.pk)
        Patient.objects.filter(pk=w.patient.pk).update(
            phone_number="+919000012345", address="New address, edited meanwhile"
        )
        records.create_grant(mrd, stale, "insurer", "TEST-1")
        fresh = Patient.objects.get(pk=w.patient.pk)
        self.assertEqual(fresh.phone_number, "+919000012345")
        self.assertEqual(fresh.address, "New address, edited meanwhile")
        self.assertIn(mrd.pk, fresh.users_cache)
        self.assertEqual(self.hidden_rows(mrd, w.patient).count(), 1)

    def test_the_beat_job_runs_per_hospital(self):
        from config.celery_app import app
        from ihr_platform.beat import PLATFORM_ONCE

        tasks.setup_periodic_tasks(app)
        entry = app.conf.beat_schedule["ihr_access_expire_grants"]
        self.assertEqual(entry["task"], "ihr_access.expire_grants")
        self.assertFalse(entry.get("options", {}).get("headers", {}).get(PLATFORM_ONCE))


class FileLinks(RecordsCase):
    """Test 22: a file read under a grant gets a link that lives min(seconds left, 300)."""

    @classmethod
    def setUpTestData(cls):
        cls.world = build_world(full=True)

    def link_seconds(self, who):
        upload = self.world.objects["file"]
        fake = mock.MagicMock()
        fake.generate_presigned_url.side_effect = lambda *a, **kw: (
            f"https://files.example.test/x?expires={kw['ExpiresIn']}"
        )
        with mock.patch("boto3.client", return_value=fake):
            answer = self.api(who).get(
                f"/api/v1/files/{upload.external_id}/",
                {"file_type": "patient", "associating_id": upload.associating_id},
            )
        self.assertEqual(answer.status_code, 200, answer.content[:300])
        self.assertTrue(answer.json()["read_signed_url"])
        return [
            c.kwargs["ExpiresIn"]
            for c in fake.generate_presigned_url.call_args_list
            if c.args and c.args[0] == "get_object"
        ]

    def test_links_under_a_grant_are_short(self):
        w = self.world
        mrd = w.users["MRD"]
        self.assertEqual(self.link_seconds("Doctor"), [3600])  # CARE's own hour
        grant = grant_records(w, mrd, w.patient)
        [full] = self.link_seconds(mrd)
        self.assertEqual(full, 300)
        RecordsGrant.objects.filter(pk=grant.pk).update(
            expires_at=timezone.now() + datetime.timedelta(minutes=2)
        )
        [short] = self.link_seconds(mrd)
        self.assertGreater(short, 0)
        self.assertLessEqual(short, 120)


class RecordsAudit(RecordsCase):
    """Test 23: a read under a grant that changes no rows leaves a ``request`` entry with
    ``purpose: records`` and the grant's id; the same read without one does not say records."""

    def test_records_request_entry_has_purpose(self):
        w = self.world
        mrd = w.users["MRD"]
        url = f"/api/v1/patient/{w.patient.external_id}/"
        since = mark()
        self.assertEqual(self.api(mrd).get(url).status_code, 200)
        [plain] = entries_since(since, kind="request")
        self.assertEqual(plain["context"]["purpose"], "care")
        self.assertNotIn("grants", plain["context"])
        grant = grant_records(w, mrd, w.patient)
        since = mark()
        answer = self.api(mrd).get(url)
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json()["blood_group"], "AB_negative")  # unprojected
        self.assertEqual(entries_since(since, kind="write"), [])  # it changed no rows
        [entry] = entries_since(since, kind="request")
        self.assertEqual(entry["context"]["purpose"], "records")
        self.assertEqual(entry["context"]["grants"], [grant.pk])
        self.assertEqual(entry["patients"], [w.patient.pk])
        self.assertEqual(entry["actor"], f"user:{mrd.pk}:{mrd.username}")

    def test_only_reads_of_a_granted_patient_say_records(self):
        """Review A3: a grant names itself, with ``purpose: records``, only on the entries of
        requests that showed its own patient: never on a read of another granted patient (with
        that grant's reason), nor on a read of a patient under no grant."""
        w = self.world
        mrd = w.users["MRD"]
        other, _visit = self.other_patient()
        third, _visit = self.other_patient(name=f"Chitra Third {w.patient.pk}")
        mine = grant_records(w, mrd, w.patient)
        theirs = grant_records(w, mrd, other, reason="patient_request", reference="")
        for patient, grants in ((w.patient, [mine.pk]), (other, [theirs.pk])):
            with self.subTest(patient=patient.pk):
                since = mark()
                url = f"/api/v1/patient/{patient.external_id}/"
                self.assertEqual(self.api(mrd).get(url).status_code, 200)
                [entry] = entries_since(since, kind="request")
                self.assertEqual(entry["patients"], [patient.pk])
                self.assertEqual(entry["context"]["purpose"], "records")
                self.assertEqual(entry["context"]["grants"], grants)
        since = mark()
        self.api(mrd).get(f"/api/v1/patient/{third.external_id}/")
        [entry] = entries_since(since, kind="request")
        self.assertEqual(entry["patients"], [third.pk])
        self.assertEqual(entry["context"]["purpose"], "care")
        self.assertNotIn("grants", entry["context"])

    def test_a_path_purpose_is_kept_and_a_mixed_read_names_only_its_grant(self):
        """Independent check of review A3's fix: a request showing a granted patient and
        another names only that grant; on an audit, billing or admin path the path's own
        purpose stays and only the grant ids are added."""
        from ihr_audit import context

        context.begin_request({"purpose": "care"})
        try:
            context.annotate_patients("grants", {11: 7}, purpose="records")
            self.assertEqual(
                context.patient_fields([11, 12]), {"purpose": "records", "grants": [7]}
            )
            self.assertEqual(context.patient_fields([12]), {})
            for purpose in ("audit", "billing", "admin"):
                with self.subTest(purpose=purpose):
                    self.assertEqual(
                        context.patient_fields([11, 12], purpose), {"grants": [7]}
                    )
        finally:
            context.end()
