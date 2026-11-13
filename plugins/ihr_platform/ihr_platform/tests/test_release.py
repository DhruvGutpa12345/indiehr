# ruff: noqa: S608 (test SQL names our own test schemas; values are parameters)
"""The release gate and the fleet runner (P1-T18a; docs/reviews/p1-t18/design.md, item 10).

Fingerprints, schema states, the gate and its defence in depth run on the test hospital inside
a transaction (rolled back). The fleet runner's logic runs in-process with the per-hospital
migration injected (``fleet.call_command``), on hospitals with empty schemas; three tests fork
real children (the pool, a crashed child's subprocess fallback, the wall-clock limit) on the
test hospital, whose migration is then a real no-op. A real failing migration is left to the
P1-T18b drill.
"""

import io
import os
import re
import signal
import tempfile
import time
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import psycopg
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import DatabaseError
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django_tenants.signals import schema_migrated
from django_tenants.utils import schema_context

from ihr_platform import celery_gate, fleet, invitations, release, tasks
from ihr_platform.models import (
    FleetRun,
    Hospital,
    HospitalSchemaState,
    MaintenanceWindow,
    RetiredAddress,
    StaffAccessRequest,
    StaffInvitation,
    TenantOperation,
)
from ihr_platform.provisioning import runner
from ihr_platform.test_runner import TEST_SCHEMA

HOST = "testserver"
OTHER_BUILD = "f" * 64


def testhosp():
    return Hospital.objects.get(schema_name=TEST_SCHEMA)


def _rows(schema):
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT app, name FROM "{schema}".django_migrations')
        return set(cursor.fetchall())


def _add_row(schema, app, name):
    with connection.cursor() as cursor:
        cursor.execute(
            f'INSERT INTO "{schema}".django_migrations (app, name, applied) '
            "VALUES (%s, %s, now())",
            [app, name],
        )


class FingerprintTests(TestCase):
    """C1: what "known" means, and the state of a schema against this build."""

    def setUp(self):
        connection.set_schema_to_public()
        self.addCleanup(connection.set_schema, TEST_SCHEMA)

    def test_a_new_hospital_is_recorded_as_this_build_and_is_served(self):
        hospital = testhosp()
        fingerprint, held = release.recorded(hospital)
        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")
        self.assertEqual(fingerprint, release.code_fingerprint())
        self.assertIsNone(held)
        self.assertTrue(release.servable(hospital))
        self.assertEqual(release.schema_state(TEST_SCHEMA).verdict, "current")
        response = self.client.get("/api/ihr/bootstrap/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)

    def test_every_recorded_migration_of_a_new_hospital_is_known(self):
        """CARE's squashed migrations leave hundreds of recorded names with no file: they are
        known through ``replaces``. A name upstream drops from both fails here until it is
        listed in retired_migrations.txt."""
        tenant = release.tenant_labels()
        rows = {r for r in _rows(TEST_SCHEMA) if r[0] in tenant}
        facts = release._facts()
        on_disk = set(facts.nodes)
        self.assertGreater(len(rows - on_disk), 400)  # replaced names, no file
        self.assertEqual(sorted(rows - facts.known), [])

    def _djangos_plan(self):
        with schema_context(TEST_SCHEMA):
            executor = MigrationExecutor(connection)  # reads django_migrations afresh
            tenant = release.tenant_labels()
            targets = [
                key for key in executor.loader.graph.leaf_nodes() if key[0] in tenant
            ]
            return {(m.app_label, m.name) for m, _ in executor.migration_plan(targets)}

    def test_the_computed_plan_matches_djangos(self):
        """schema_state's own reading of django_migrations agrees with Django's executor:
        on a current schema, and on one behind by the leaves of two apps and the one before
        a leaf (C10: where _applied and the graph's nodes could disagree with Django)."""
        self.assertEqual(self._djangos_plan(), set())
        self.assertEqual(release.schema_state(TEST_SCHEMA).pending, ())
        rows = _rows(TEST_SCHEMA)
        audit = sorted(n for a, n in rows if a == "ihr_audit")
        removed = [("ihr_identity", max(n for a, n in rows if a == "ihr_identity"))]
        removed += [("ihr_audit", audit[-1]), ("ihr_audit", audit[-2])]
        with connection.cursor() as cursor:
            for app, name in removed:
                cursor.execute(
                    f'DELETE FROM "{TEST_SCHEMA}".django_migrations '
                    "WHERE app = %s AND name = %s",
                    [app, name],
                )
        plan = self._djangos_plan()
        self.assertEqual(plan, set(removed))
        self.assertEqual(set(release.schema_state(TEST_SCHEMA).pending), plan)

    def test_a_future_tenant_row_reads_as_ahead_and_is_refused(self):
        _add_row(TEST_SCHEMA, "facility", "9999_future")
        state = release.schema_state(TEST_SCHEMA)
        self.assertEqual(state.verdict, "ahead")
        self.assertEqual(state.unknown, (("facility", "9999_future"),))
        hospital = testhosp()
        self.assertEqual(release.record(hospital).fingerprint, "")
        self.assertEqual(release.recorded(hospital)[0], "")
        self.assertFalse(release.servable(hospital))
        response = self.client.get("/api/ihr/bootstrap/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 503)

    def test_a_shared_only_row_is_ignored(self):
        _add_row(TEST_SCHEMA, "ihr_platform", "9999_future")
        _add_row(TEST_SCHEMA, "ihr_terminology", "9999_future")
        self.assertEqual(release.schema_state(TEST_SCHEMA).verdict, "current")

    def test_a_missing_tenant_migration_reads_as_behind(self):
        leaf = max(n for a, n in _rows(TEST_SCHEMA) if a == "ihr_tenant_settings")
        with connection.cursor() as cursor:
            cursor.execute(
                f'DELETE FROM "{TEST_SCHEMA}".django_migrations '
                "WHERE app = 'ihr_tenant_settings' AND name = %s",
                [leaf],
            )
        state = release.schema_state(TEST_SCHEMA)
        self.assertEqual(state.verdict, "behind")
        self.assertIn(("ihr_tenant_settings", leaf), state.pending)
        self.assertEqual(state.fingerprint, "")

    def test_a_schema_without_migrations_is_missing(self):
        with connection.cursor() as cursor:
            cursor.execute('CREATE SCHEMA "h_emptyrel01"')
        self.assertEqual(release.schema_state("h_emptyrel01").verdict, "missing")
        with self.assertRaises(ValueError):
            release.schema_state('x"; DROP TABLE y; --')

    def test_fingerprints(self):
        tenant, public = release.code_fingerprint(), release.code_fingerprint("public")
        self.assertRegex(tenant, r"^[0-9a-f]{64}$")
        self.assertNotEqual(tenant, public)
        self.assertEqual(release.code_fingerprint(), tenant)  # once per process
        self.assertEqual(release.schema_state("public").verdict, "current")

    def test_the_state_row_is_changed_only_by_update(self):
        state = HospitalSchemaState.objects.get(pk=testhosp().pk)
        state.upgrading_since = timezone.now()
        with self.assertRaises(RuntimeError):
            state.save()
        # A full save of the hospital (ihr_create_tenant does one) cannot touch it (C2)
        hospital = testhosp()
        release.hold([hospital.pk])
        hospital.name = hospital.name  # an old copy, saved whole
        hospital.save()
        self.assertIsNotNone(release.recorded(hospital)[1])

    def test_the_signal_records_and_fleet_children_leave_it_to_the_parent(self):
        hospital = testhosp()
        HospitalSchemaState.objects.filter(pk=hospital.pk).update(schema_fingerprint="")
        release.RECORD_ON_SIGNAL["on"] = False
        try:
            schema_migrated.send(sender=None, schema_name=TEST_SCHEMA)
        finally:
            release.RECORD_ON_SIGNAL["on"] = True
        self.assertEqual(release.recorded(hospital)[0], "")
        schema_migrated.send(sender=None, schema_name=TEST_SCHEMA)
        self.assertEqual(release.recorded(hospital)[0], release.code_fingerprint())
        schema_migrated.send(
            sender=None, schema_name="public"
        )  # no platform row: nothing


class SafeErrorTests(TestCase):
    """O10: an error record never holds row values."""

    def test_a_real_unique_violation_keeps_its_names_not_its_values(self):
        with connection.cursor() as cursor:
            cursor.execute("CREATE TEMP TABLE ihr_probe (phone text UNIQUE)")
            cursor.execute("INSERT INTO ihr_probe VALUES ('+919876543210')")
            try:
                with transaction.atomic():
                    cursor.execute("INSERT INTO ihr_probe VALUES ('+919876543210')")
            except IntegrityError as exc:
                error = release.safe_error(exc, "facility.0500_x")
        self.assertIn("SQLSTATE 23505", error)
        self.assertIn("constraint ihr_probe_phone_key", error)
        self.assertIn("table ihr_probe", error)
        self.assertIn("migration facility.0500_x", error)
        self.assertNotIn("9876543210", error)
        self.assertNotIn("DETAIL", error)

    def test_our_own_errors_keep_their_first_line_masked(self):
        exc = runner.steps.StepError(
            "Key (phone)=(+919876543210) already exists for a@b.example\nDETAIL: more"
        )
        error = release.safe_error(exc)
        self.assertTrue(error.startswith("StepError: Key (phone)=(<x>)"), error)
        for leak in ("9876543210", "a@b.example", "DETAIL"):
            self.assertNotIn(leak, error)
        self.assertIn(
            "<email>",
            release.safe_error(runner.ProvisioningError("sent to a@b.example")),
        )
        self.assertEqual(
            release.safe_error(fleet.FleetFault("the public schema is behind")),
            "FleetFault: the public schema is behind",
        )

    def test_any_other_error_keeps_no_text_only_where_it_was_raised(self):
        """S7: a data migration's ValueError or KeyError can carry a patient's name; neither
        the message nor any part of it is kept, only the class and file:line."""

        def convert(value):
            return int(value)

        for raise_it in (
            lambda: convert("Ramesh Kumar"),
            lambda: {}["Sunita Devi"],
            lambda: (_ for _ in ()).throw(
                RuntimeError("injected failure in geography")
            ),
        ):
            try:
                raise_it()
            except Exception as exc:
                error = release.safe_error(exc, "facility.0500_x")
            for leak in (
                "Ramesh",
                "Kumar",
                "Sunita",
                "Devi",
                "invalid literal",
                "injected",
            ):
                self.assertNotIn(leak, error)
            self.assertRegex(
                error,
                r"^(ValueError|KeyError|RuntimeError); at test_release\.py:\d+; "
                r"migration facility\.0500_x$",
            )
        self.assertEqual(
            runner._safe_error(RuntimeError("Ramesh Kumar")), "RuntimeError"
        )

    def test_a_refused_role_sync_keeps_its_value_free_message(self):
        from ihr_roles.management.commands.sync_permissions_roles import (
            RoleSyncRefused,
        )

        refused = RoleSyncRefused("role sync would be refused: system role 'X' held")
        self.assertEqual(
            release.safe_error(refused),
            "RoleSyncRefused: role sync would be refused: system role 'X' held",
        )
        self.assertEqual(
            release.safe_error(CommandError("Ramesh Kumar")), "CommandError"
        )


class GateTests(TestCase):
    """Design items 2-4: a held hospital, or one whose schema is not this build's, is not
    served; platform hosts always are."""

    def setUp(self):
        connection.set_schema_to_public()
        self.addCleanup(connection.set_schema, TEST_SCHEMA)
        self.hospital = testhosp()

    def _hold(self):
        release.hold([self.hospital.pk])
        connection.set_schema_to_public()

    def test_api_requests_get_503_json(self):
        self._hold()
        for method in ("get", "post"):
            response = getattr(self.client, method)(
                "/api/v1/users/getcurrentuser/", HTTP_HOST=HOST
            )
            self.assertEqual(response.status_code, 503, method)
            self.assertEqual(
                response.json(), {"detail": "upgrading", "code": "ihr_upgrading"}
            )
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(response["Retry-After"], "60")

    def test_a_page_request_gets_a_short_page_in_both_languages(self):
        self._hold()
        response = self.client.get(
            "/api/ihr/auth/login/", HTTP_HOST=HOST, HTTP_ACCEPT="text/html,*/*"
        )
        self.assertEqual(response.status_code, 503)
        page = response.content.decode()
        self.assertIn("being upgraded", page)
        self.assertIn("अपग्रेड", page)

    def test_bootstrap_js_is_a_stub_from_public_data_only(self):
        MaintenanceWindow.objects.create(
            start=timezone.now() - timedelta(minutes=5),
            minutes=60,
            created_by="test",
        )
        self._hold()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/ihr/bootstrap.js", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        body = response.content.decode()
        self.assertIn("window.__IHR_UPGRADING__", body)
        self.assertIn(self.hospital.name, body)
        self.assertIn(" IST", body)  # the open window's end
        self.assertNotIn("<", body)  # never closes a script tag (bootstrap.js rule)
        tables = set()
        for query in queries.captured_queries:
            tables.update(re.findall(r'FROM "([a-z_]+)"', query["sql"]))
        self.assertTrue(tables)
        self.assertTrue(all(t.startswith("ihr_platform_") for t in tables), tables)

    def test_a_gated_request_writes_no_audit_entry(self):
        def entries():
            with connection.cursor() as cursor:
                cursor.execute(f'SELECT count(*) FROM "{TEST_SCHEMA}".ihr_audit_entry')
                return cursor.fetchone()[0]

        before = entries()
        self._hold()
        self.client.post("/api/v1/patient/", {}, HTTP_HOST=HOST)
        connection.set_schema_to_public()
        self.assertEqual(entries(), before)

    def test_a_schema_of_another_build_is_gated_and_platform_hosts_never_are(self):
        call_command(
            "ihr_create_tenant",
            public=True,
            domain=["platform.testserver"],
            verbosity=0,
        )
        with mock.patch.object(release, "code_fingerprint", return_value=OTHER_BUILD):
            self.assertEqual(
                self.client.get("/api/ihr/bootstrap/", HTTP_HOST=HOST).status_code, 503
            )
            self.assertEqual(
                self.client.get("/ping/", HTTP_HOST="platform.testserver").status_code,
                200,
            )
        self.assertEqual(
            self.client.get("/api/ihr/bootstrap/", HTTP_HOST=HOST).status_code, 200
        )


class DefenceInDepthTests(TestCase):
    """Design item 3: the same check in sign-ins, beat, the staff worker and provisioning."""

    def setUp(self):
        connection.set_schema_to_public()
        self.addCleanup(connection.set_schema, TEST_SCHEMA)
        self.hospital = testhosp()

    def test_staff_and_service_sign_ins_refuse(self):
        from ihr_identity.authentication import (
            ServiceTokenAuthentication,
            check_stored_token,
        )
        from ihr_identity.tokens import TokenRejectedError
        from rest_framework.authentication import TokenAuthentication
        from rest_framework.exceptions import AuthenticationFailed

        connection.set_tenant(self.hospital)
        # positive control: our check passes, the next one (no Keycloak link here) speaks
        with self.assertRaises(TokenRejectedError) as raised:
            check_stored_token("x")
        self.assertNotIn("upgraded", str(raised.exception))
        release.hold([self.hospital.pk])
        connection.set_tenant(self.hospital)
        with self.assertRaisesMessage(TokenRejectedError, "being upgraded"):
            check_stored_token("x")
        machine = SimpleNamespace(is_service_account=True)
        with (
            mock.patch.object(
                TokenAuthentication,
                "authenticate_credentials",
                return_value=(machine, "t"),
            ),
            self.assertRaisesMessage(AuthenticationFailed, "being upgraded"),
        ):
            ServiceTokenAuthentication().authenticate_credentials("k")

    def test_beat_skips_a_held_hospital(self):
        from ihr_platform.beat import ActiveTenantScheduler

        self.assertIn(self.hospital, ActiveTenantScheduler.get_queryset())
        release.hold([self.hospital.pk])
        self.assertNotIn(self.hospital, ActiveTenantScheduler.get_queryset())
        release.release_hold([self.hospital.pk])
        with mock.patch.object(release, "code_fingerprint", return_value=OTHER_BUILD):
            self.assertNotIn(self.hospital, ActiveTenantScheduler.get_queryset())

    def test_the_staff_worker_waits_and_never_fails_the_work(self):
        # Only this test's work is due (rolled back afterwards)
        StaffInvitation.objects.filter(status__in=["queued", "sent"]).update(
            status=StaffInvitation.Status.CANCELLED
        )
        StaffAccessRequest.objects.filter(status="queued").update(status="done")
        inv = StaffInvitation.objects.create(
            hospital=self.hospital,
            care_user_id=987654,
            email="wait@example.test",
            first_name="Wait",
            invited_by="test",
        )
        req = StaffAccessRequest.objects.create(
            hospital=self.hospital,
            care_user_id=987654,
            action=StaffAccessRequest.Action.RESET_SIGN_IN,
            requested_by="test",
        )
        release.hold([self.hospital.pk])
        self.assertIsNone(invitations.claim_next())
        self.assertFalse(invitations.handle_request_once())
        self.assertEqual(invitations.expire_due(now=timezone.now()), 0)
        # Claimed just before the hold: put back, not counted as an attempt, never failed
        inv.attempts = 1
        with mock.patch.object(invitations, "claim_next", return_value=inv):
            self.assertTrue(invitations.deliver_once())
        inv.refresh_from_db()
        self.assertEqual((inv.status, inv.attempts), (StaffInvitation.Status.QUEUED, 0))
        self.assertIsNotNone(inv.not_before)
        req.refresh_from_db()
        self.assertEqual(req.status, StaffAccessRequest.Status.QUEUED)
        release.release_hold([self.hospital.pk])
        StaffInvitation.objects.filter(pk=inv.pk).update(not_before=None)
        self.assertEqual(invitations.claim_next().pk, inv.pk)

    def test_operator_commands_refuse_a_held_hospital(self):
        """S6: the commands an operator runs into one hospital (or all) never write into a
        schema that is held for a release or behind this build."""
        geography = "ihr_tenant_settings.management.commands.ihr_load_geography"
        release.hold([self.hospital.pk])
        for args in (
            ("ihr_pack_apply", "--slug", "testhosp", "--operator", "tester"),
            ("ihr_hospital_profile", "--slug", "testhosp", "--display-name", "X"),
            (
                "ihr_link_staff",
                "--slug",
                "testhosp",
                "--username",
                "nobody",
                "--sub",
                str(uuid.uuid4()),
            ),
            ("ihr_load_geography", "--slug", "testhosp"),
        ):
            with (
                mock.patch(f"{geography}.pincodes.current", return_value=object()),
                self.assertRaisesMessage(CommandError, "held for a release"),
            ):
                call_command(*args, stdout=io.StringIO(), stderr=io.StringIO())
        out = io.StringIO()
        with (
            mock.patch(f"{geography}.pincodes.current", return_value=object()),
            mock.patch(f"{geography}.geography.load_all", return_value=(0, 0)),
        ):
            call_command("ihr_load_geography", "--all", stdout=out)
        self.assertIn("testhosp: left out (held for a release", out.getvalue())
        self.assertNotIn("testhosp: 0 states", out.getvalue())

    def test_provisioning_waits_for_a_held_hospital(self):
        op = TenantOperation.objects.create(
            kind=TenantOperation.Kind.PROVISION,
            hospital=self.hospital,
            slug=self.hospital.slug,
            status=TenantOperation.Status.SUCCEEDED,
            requested_by="test",
            result={"admin_keycloak_sub": "x"},
        )
        release.hold([self.hospital.pk])
        with self.assertRaises(runner.HospitalUpgrading):
            runner.resend_setup(op)
        TenantOperation.objects.filter(pk=op.pk).update(status="failed")
        op.refresh_from_db()
        with self.assertRaises(runner.HospitalUpgrading):
            runner.run(op)


# --- the fleet runner ------------------------------------------------------------------


class FakeMigrate:
    """Stands in for ``migrate_schemas`` in fleet.migrate_one: per schema, "ok", "fail" (a
    unique violation carrying a mobile number), "lock" (a lock time-out N times) or
    "interrupt" (Ctrl-C). The migration a real failure names is proven on a real one
    (MigrationNameTests)."""

    def __init__(self, plan):
        self.plan = dict(plan)
        self.calls = []
        self.locks = {}

    def __call__(self, name, **kwargs):
        if name != "migrate_schemas":
            return call_command(name, **kwargs)
        schema = kwargs["schema_name"]
        self.calls.append(schema)
        what = self.plan.get(schema, "ok")
        if what == "interrupt":
            raise KeyboardInterrupt
        if what == "fail":
            cause = psycopg.errors.UniqueViolation(
                'duplicate key value violates unique constraint "x"\n'
                "DETAIL:  Key (phone)=(+919876543210) already exists."
            )
            raise IntegrityError(str(cause)) from cause
        if what.startswith("lock"):
            left = self.locks.setdefault(schema, int(what[4:] or 99))
            if left:
                self.locks[schema] = left - 1
                cause = psycopg.errors.LockNotAvailable("canceling statement")
                raise OperationalError(str(cause)) from cause
        return None


def _settle(hospital):
    release._write(
        hospital.pk,
        schema_fingerprint=release.code_fingerprint(),
        checked_at=timezone.now(),
    )
    return SimpleNamespace(current=True, verdict="current")


class FleetTests(SimpleTestCase):
    """The fleet runner's logic in-process (design item 10): parallelism off, the migration
    injected, hospitals with empty schemas (none of the minute a real one costs)."""

    databases = {"default"}

    def setUp(self):
        connection.set_schema_to_public()
        self.slugs = []
        self.addCleanup(self._clean)
        for patch in (
            mock.patch.object(fleet, "needs_migrate", return_value=True),
            mock.patch.object(fleet, "settle", side_effect=_settle),
            mock.patch.object(fleet, "LOCK_PAUSES", (0, 0)),
            mock.patch.object(fleet, "RETRY_DELAYS", (0, 0)),
            mock.patch.object(fleet, "INLINE", new=True),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _clean(self):
        connection.set_schema_to_public()
        ours = TenantOperation.objects.filter(
            slug__in=[*self.slugs, "testhosp"], kind=TenantOperation.Kind.UPGRADE
        )
        runs = set(ours.values_list("fleet_run", flat=True))
        ours.delete()
        release.release_hold([testhosp().pk])
        release.record(testhosp())
        FleetRun.objects.filter(pk__in=runs - {None}).delete()
        for hospital in Hospital.objects.filter(slug__in=self.slugs):
            hospital.delete(force_drop=True)
        RetiredAddress.objects.filter(value__in=self.slugs).delete()
        connection.set_schema(TEST_SCHEMA)

    def _hospital(self, slug, status=Hospital.Status.ACTIVE, schema=True):
        self.slugs.append(slug)
        hospital = Hospital(slug=slug, name=slug.title(), status=status)
        hospital.auto_create_schema = False
        hospital.save()
        if schema:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE SCHEMA "{hospital.schema_name}"')
        return hospital

    def _run(self, steps, plan=(), **options):
        out, err = [], []
        fake = FakeMigrate(plan)
        with mock.patch.object(fleet, "call_command", side_effect=fake):
            runner_ = fleet.Fleet(
                steps,
                operator="tester",
                slugs=options.pop("slugs", self.slugs),
                parallel=options.pop("parallel", 1),
                out=out.append,
                err=err.append,
                **options,
            )
            try:
                run = runner_.run()
            finally:
                connection.set_schema_to_public()
        return run, out, err, fake

    def _ops(self, run):
        return {op.slug: op for op in run.operations.all()}

    def test_one_failure_stays_with_its_hospital(self):
        a, b, c = (self._hospital(s) for s in ("fleet-a", "fleet-b", "fleet-c"))
        run, out, err, fake = self._run(["migrate"], {b.schema_name: "fail"})
        self.assertEqual(sorted(fake.calls), sorted(h.schema_name for h in (a, b, c)))
        ops = self._ops(run)
        self.assertEqual(
            {s: op.result["outcome"] for s, op in ops.items()},
            {"fleet-a": "done", "fleet-b": "failed", "fleet-c": "done"},
        )
        for hospital in (a, c):  # migrated, recorded, released
            self.assertTrue(release.schema_ready(hospital))
            self.assertEqual(ops[hospital.slug].status, "succeeded")
            self.assertEqual(ops[hospital.slug].kind, TenantOperation.Kind.UPGRADE)
        self.assertFalse(release.schema_ready(b))
        self.assertIsNotNone(release.recorded(b)[1])  # stays held
        [step] = ops["fleet-b"].steps
        self.assertEqual(step["status"], "failed")
        self.assertIn("SQLSTATE 23505", step["error"])
        recorded = str(ops["fleet-b"].steps) + str(ops["fleet-b"].result)
        self.assertNotIn("9876543210", recorded)
        self.assertNotIn("DETAIL", recorded)
        self.assertIn("ihr-fleet: hospital=fleet-b outcome=failed", err)
        self.assertEqual(
            [e for e in err if e.startswith("ihr-fleet: hospital=")],
            ["ihr-fleet: hospital=fleet-b outcome=failed"],
        )
        self.assertEqual(
            (run.status, run.counts), ("incomplete", {"done": 2, "failed": 1})
        )
        self.assertEqual(run.tenant_fingerprint, release.code_fingerprint())

        # After a fix, --resume takes only the unfinished hospital
        again, _, err, fake = self._run([], resume=run.pk, slugs=[])
        self.assertEqual(fake.calls, [b.schema_name])
        self.assertEqual(again.resumed_from_id, run.pk)
        self.assertEqual(again.counts, {"done": 1})
        self.assertTrue(release.schema_ready(b))
        self.assertEqual(err, [])
        nothing, out, _, _ = self._run([], resume=again.pk, slugs=[])
        self.assertIsNone(nothing)
        self.assertIn("nothing to resume", out[-1])

    def test_a_hospital_held_elsewhere_is_retried_then_deferred(self):
        a = self._hospital("fleet-lock")
        with runner.exclusive(
            "fleet-lock"
        ):  # another job (provisioning, packs) holds it
            run, _, err, fake = self._run(["migrate"])
        self.assertEqual(fake.calls, [])
        op = self._ops(run)["fleet-lock"]
        self.assertEqual(op.result["outcome"], "deferred")
        self.assertIn("another job held", op.steps[0]["error"])
        self.assertIn("ihr-fleet: hospital=fleet-lock outcome=deferred", err)
        self.assertEqual(
            sum("busy: trying again" in line for line in err), len(fleet.RETRY_DELAYS)
        )
        self.assertFalse(release.schema_ready(a))
        # Lock time-outs inside the migration are retried in the child, then deferred
        run, _, _, fake = self._run(["migrate"], {a.schema_name: "lock2"})
        op = self._ops(run)["fleet-lock"]
        self.assertEqual((op.result["outcome"], op.steps[0]["attempts"]), ("done", 3))
        run, _, err, _ = self._run(["migrate"], {a.schema_name: "lock"})
        op = self._ops(run)["fleet-lock"]
        self.assertEqual(op.result["outcome"], "deferred")
        self.assertIn("SQLSTATE 55P03", op.steps[0]["error"])

    def test_which_hospitals_each_step_takes(self):
        made = {
            status: self._hospital(f"fleet-{status[:5]}", status)
            for status in (
                "provisioning",
                "active",
                "suspended",
                "offboarding",
                "archived",
            )
        }
        self._hospital("fleet-noschema", schema=False)
        synced = []
        with (
            mock.patch.object(fleet, "roles_check"),
            mock.patch.object(fleet, "roles_sync", side_effect=synced.append),
            mock.patch("ihr_refdata.engine.prepare", return_value={}),
            mock.patch(
                "ihr_refdata.management.commands.ihr_pack_apply.Command._one",
                return_value="done",
            ) as packs,
        ):
            run, out, _, fake = self._run(["packs", "roles", "migrate"], slugs=[])
        self.assertEqual(run.steps, ["migrate", "roles", "packs"])  # always this order
        migrated = {h.slug for h in Hospital.objects.filter(schema_name__in=fake.calls)}
        self.assertEqual(
            migrated,
            {"fleet-provi", "fleet-activ", "fleet-suspe", "fleet-offbo", "testhosp"},
        )
        self.assertNotIn("fleet-noschema", self._ops(run))
        self.assertIn("fleet-noschema: left out (it has no schema)", out)
        self.assertEqual(
            sorted(h.slug for h in synced), ["fleet-activ", "fleet-suspe", "testhosp"]
        )
        self.assertEqual(
            sorted(c.args[0].slug for c in packs.call_args_list),
            ["fleet-activ", "testhosp"],
        )
        ops = self._ops(run)
        self.assertEqual(ops["fleet-provi"].steps[1]["status"], "skipped")
        self.assertNotIn(made["archived"].slug, ops)
        with self.assertRaisesMessage(CommandError, "never archived"):
            call_command("ihr_fleet_run", "migrate", status="archived", operator="t")

    def test_a_refused_role_sync_holds_that_hospital_and_the_start_goes_on(self):
        a, b = self._hospital("fleet-r1"), self._hospital("fleet-r2")
        _settle(a), _settle(b)
        synced = []

        def check(hospital):
            if hospital.slug == "fleet-r2":
                raise CommandError("role sync would be refused: system role 'X' held")

        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(
                release,
                "schema_state",
                return_value=SimpleNamespace(
                    current=True,
                    fingerprint=release.code_fingerprint(),
                    verdict="current",
                    describe=lambda: "current",
                ),
            ),
            mock.patch.object(fleet, "roles_check", side_effect=check),
            mock.patch.object(fleet, "roles_sync", side_effect=synced.append),
        ):
            call_command(
                "ihr_fleet_run",
                "roles",
                "check",
                startup=True,
                slug=self.slugs,
                stdout=out,
                stderr=err,
            )  # exits 0: one hospital's problem never stops the start
            self.assertEqual([h.slug for h in synced], ["fleet-r1"])
            self.assertTrue(release.schema_ready(a))
            self.assertFalse(release.schema_ready(b))
            self.assertIn("ihr-fleet: hospital=fleet-r2 outcome=held", err.getvalue())
            with self.assertRaisesMessage(CommandError, "1 hospital(s) not done"):
                call_command(
                    "ihr_fleet_run",
                    "roles",
                    operator="tester",
                    slug=self.slugs,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )
        # an explicit release clears the hold
        call_command(
            "ihr_fleet_run",
            "release",
            operator="tester",
            slug=["fleet-r2"],
            stdout=io.StringIO(),
        )
        self.assertTrue(release.schema_ready(b))

    def test_the_start_refuses_a_hospital_behind_this_build(self):
        """Design item 9: a live hospital behind this build that no release of this build
        tried stops the start. S4: the refusal writes nothing over the fingerprint the
        previous build recorded (it may still be serving the hospital); a hospital with no
        row gets none."""
        a, fresh = self._hospital("fleet-old"), self._hospital("fleet-norow")
        release._write(a.pk, schema_fingerprint=OTHER_BUILD, checked_at=timezone.now())
        out, err = io.StringIO(), io.StringIO()
        with self.assertRaisesMessage(CommandError, "migrate-hospitals"):
            call_command(
                "ihr_fleet_run",
                "check",
                "roles",
                "packs",
                record=True,
                startup=True,
                slug=self.slugs,
                stdout=out,
                stderr=err,
            )
        self.assertIn("ihr-fleet: hospital=fleet-old outcome=failed", err.getvalue())
        self.assertEqual(release.recorded(a), (OTHER_BUILD, None))
        self.assertIsNone(release.recorded(fresh))
        run = FleetRun.objects.get(
            pk=TenantOperation.objects.get(slug="fleet-old").fleet_run_id
        )
        self.assertEqual(run.status, "fault")
        with self.assertRaisesMessage(CommandError, "never migrates"):
            call_command("ihr_fleet_run", "migrate", startup=True)

    def test_hold_window_and_release(self):
        a = self._hospital("fleet-h")
        _settle(a)
        call_command(
            "ihr_fleet_run",
            "hold",
            operator="tester",
            slug=["fleet-h"],
            stdout=io.StringIO(),
        )
        self.assertFalse(release.schema_ready(a))
        out = io.StringIO()
        call_command(
            "ihr_fleet_run",
            "window",
            start="01-12-2026 02:00",
            minutes=90,
            note="CARE update",
            operator="tester",
            stdout=out,
        )
        self.assertIn("01-12-2026 02:00 IST for 90 min (CARE update)", out.getvalue())
        item = MaintenanceWindow.objects.get(created_by="tester")
        self.addCleanup(MaintenanceWindow.objects.filter(pk=item.pk).delete)
        self.assertEqual(item.start.isoformat(), "2026-11-30T20:30:00+00:00")
        call_command(
            "ihr_fleet_run",
            "release",
            operator="tester",
            slug=["fleet-h"],
            stdout=io.StringIO(),
        )
        self.assertTrue(release.schema_ready(a))
        call_command(
            "ihr_fleet_run",
            "window",
            clear=True,
            operator="tester",
            stdout=io.StringIO(),
        )
        self.assertFalse(MaintenanceWindow.objects.filter(pk=item.pk).exists())

    # --- review of P1-T18a (docs/reviews/p1-t18) -----------------------------------------

    def _current(self):
        """schema_state for empty test schemas, as if each were this build's."""
        real = release.schema_state

        def state(schema):
            if schema == release.PUBLIC:
                return real(schema)
            return SimpleNamespace(
                current=True,
                fingerprint=release.code_fingerprint(),
                verdict="current",
                describe=lambda: "current",
            )

        return mock.patch.object(release, "schema_state", side_effect=state)

    def _no_packs(self):
        return (
            mock.patch("ihr_refdata.engine.prepare", return_value={}),
            mock.patch(
                "ihr_refdata.management.commands.ihr_pack_apply.Command._one",
                return_value="done",
            ),
        )

    def _op(self, slug, run=None):
        ops = TenantOperation.objects.filter(
            slug=slug, kind=TenantOperation.Kind.UPGRADE
        )
        if run is not None:
            ops = ops.filter(fleet_run=run)
        return ops.order_by("-created_at").first()

    def test_a_fleet_wide_fault_stops_the_run_before_any_hospital_is_held(self):
        """C1/S5: the public schema behind, or a bad pack, is found before any hospital is
        held or migrated."""
        from ihr_refdata import engine

        a = self._hospital("fleet-pre")
        real = release.schema_state

        def public_behind(schema):
            if schema == release.PUBLIC:
                return SimpleNamespace(
                    current=False, verdict="behind", describe=lambda: "1 not applied"
                )
            return real(schema)

        fake = FakeMigrate({})
        with (
            mock.patch.object(release, "schema_state", side_effect=public_behind),
            mock.patch.object(fleet, "call_command", side_effect=fake),
            self.assertRaisesMessage(fleet.FleetFault, "migrate_schemas --shared"),
        ):
            fleet.Fleet(
                ["migrate", "check"],
                operator="tester",
                slugs=self.slugs,
                out=lambda line: None,
                err=lambda line: None,
            ).run()
        self.assertEqual(fake.calls, [])
        self.assertIsNone(release.recorded(a))  # never held: no state row at all
        op = self._op("fleet-pre")
        self.assertEqual(
            (op.result["outcome"], op.fleet_run.status), ("stopped", "fault")
        )

        bad = engine.PackError("ihr-x v2 was released before with another sha256")
        with (
            mock.patch("ihr_refdata.engine.prepare", side_effect=bad),
            mock.patch.object(fleet, "call_command", side_effect=fake),
            self.assertRaisesMessage(fleet.FleetFault, "refused for every hospital"),
        ):
            fleet.Fleet(
                ["migrate", "packs"],
                operator="tester",
                slugs=self.slugs,
                out=lambda line: None,
                err=lambda line: None,
            ).run()
        self.assertEqual(fake.calls, [])
        self.assertIsNone(release.recorded(a))

    def test_a_pack_fault_after_migrating_still_serves_the_migrated(self):
        """C1: a fault raised by the packs step itself (after the preflight) leaves the
        hospitals' own steps done: they are released, not left held."""
        from ihr_refdata import engine

        a, b = self._hospital("fleet-pk1"), self._hospital("fleet-pk2")

        def prepare(actor, record=True):
            if record:
                raise engine.PackError("a pack file changed under the run")
            return {}

        err = []
        with (
            self._current(),
            mock.patch.object(fleet, "roles_check"),
            mock.patch.object(fleet, "roles_sync"),
            mock.patch("ihr_refdata.engine.prepare", side_effect=prepare),
            mock.patch.object(fleet, "call_command", side_effect=FakeMigrate({})),
            self.assertRaises(fleet.FleetFault),
        ):
            fleet.Fleet(
                ["migrate", "check", "roles", "packs"],
                operator="tester",
                slugs=self.slugs,
                out=lambda line: None,
                err=err.append,
            ).run()
        self.assertTrue(release.schema_ready(a))
        self.assertTrue(release.schema_ready(b))
        self.assertEqual([e for e in err if e.startswith("ihr-fleet: hospital=")], [])
        self.assertEqual(
            self._op("fleet-pk1").result["outcome"], "stopped"
        )  # resumable

    def test_the_start_serves_the_others_when_this_release_left_one_held(self):
        """C2: a hospital this build's release tried and left held (its migration failed)
        stays gated and reported; it does not stop the start for the others. A hospital
        merely held (no migrate run of this build tried it) still stops it."""
        a = self._hospital("fleet-known")
        run, _, err, _ = self._run(["migrate"], {a.schema_name: "fail"})
        self.assertEqual(self._ops(run)["fleet-known"].result["outcome"], "failed")
        out, err = io.StringIO(), io.StringIO()
        packs, apply = self._no_packs()
        with (
            mock.patch.object(fleet, "roles_check"),
            mock.patch.object(fleet, "roles_sync"),
            packs,
            apply,
        ):
            call_command(
                "ihr_fleet_run",
                "check",
                "roles",
                "packs",
                record=True,
                startup=True,
                slug=["fleet-known", "testhosp"],
                stdout=out,
                stderr=err,
            )  # exits 0
        self.assertTrue(release.schema_ready(testhosp()))
        self.assertFalse(release.schema_ready(a))
        self.assertIn("ihr-fleet: hospital=fleet-known outcome=failed", err.getvalue())
        start = self._op("fleet-known").fleet_run
        self.assertEqual((start.startup, start.status), (True, "incomplete"))
        # Held by hand, with no migrate run of this build: the start still refuses
        b = self._hospital("fleet-handheld")
        call_command(
            "ihr_fleet_run",
            "hold",
            operator="tester",
            slug=[b.slug],
            stdout=io.StringIO(),
        )
        with self.assertRaisesMessage(CommandError, "not on this build's schema"):
            call_command(
                "ihr_fleet_run",
                "check",
                startup=True,
                slug=[b.slug],
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )

    def test_a_killed_run_is_taken_up_by_its_resume(self):
        """C3: a run killed half-way (SIGKILL, OOM, a reboot) leaves its row "running" and
        operations with no outcome at all; --resume closes it and takes them all up."""
        a, b = self._hospital("fleet-k1"), self._hospital("fleet-k2")
        dead = FleetRun.objects.create(
            steps=["migrate"],
            operator="tester",
            tenant_fingerprint=release.code_fingerprint(),
            public_fingerprint=release.code_fingerprint(release.PUBLIC),
        )
        for hospital, result in ((a, {"fingerprint_before": None}), (b, {})):
            TenantOperation.objects.create(
                kind=TenantOperation.Kind.UPGRADE,
                hospital=hospital,
                slug=hospital.slug,
                status=TenantOperation.Status.RUNNING,
                requested_by="tester",
                result=result,
                fleet_run=dead,
            )
        release.hold([a.pk, b.pk])
        again, _, _, fake = self._run([], resume=dead.pk, slugs=[])
        self.assertEqual(sorted(fake.calls), sorted([a.schema_name, b.schema_name]))
        self.assertEqual(again.counts, {"done": 2})
        self.assertTrue(release.schema_ready(a) and release.schema_ready(b))
        dead.refresh_from_db()
        self.assertEqual(dead.status, "fault")
        self.assertIn("interrupted", dead.error)
        self.assertEqual(
            {op.result["outcome"] for op in dead.operations.all()}, {"interrupted"}
        )

    def test_ctrl_c_closes_the_run_and_its_resume_finishes_it(self):
        """C3: Ctrl-C (or SIGTERM, which the command turns into SystemExit) still closes the
        run and its hospitals; the lock is free again and --resume finishes them."""
        a = self._hospital("fleet-int")
        with self.assertRaises(KeyboardInterrupt):
            self._run(["migrate"], {a.schema_name: "interrupt"})
        op = self._op("fleet-int")
        self.assertEqual(op.fleet_run.status, "fault")
        self.assertIn("interrupted (KeyboardInterrupt)", op.fleet_run.error)
        self.assertEqual(op.result["outcome"], "stopped")
        self.assertFalse(release.schema_ready(a))  # held: mark, then migrate
        _, _, _, fake = self._run([], resume=op.fleet_run_id, slugs=[])
        self.assertEqual(fake.calls, [a.schema_name])
        self.assertTrue(release.schema_ready(a))

    def test_sigterm_unwinds_the_command(self):
        from ihr_platform.management.commands import ihr_fleet_run as command

        a = self._hospital("fleet-term")
        _settle(a)
        seen = []
        with (
            mock.patch.object(
                fleet,
                "roles_check",
                side_effect=lambda h: seen.append(signal.getsignal(signal.SIGTERM)),
            ),
            mock.patch.object(fleet, "roles_sync"),
        ):
            call_command(
                "ihr_fleet_run",
                "roles",
                operator="tester",
                slug=[a.slug],
                stdout=io.StringIO(),
            )
        self.assertEqual(seen, [command._raise_exit])
        self.assertIsNot(signal.getsignal(signal.SIGTERM), command._raise_exit)
        with self.assertRaises(SystemExit):
            command._raise_exit(signal.SIGTERM, None)

    def test_a_role_refusal_hold_is_released_by_its_resume(self):
        """C4/S2: the start-up holds a hospital whose role sync is refused; once the cause
        is fixed, the alert's remedy (--resume of that run) serves it again, and it is never
        reported as done while it is not served."""
        from ihr_roles.management.commands.sync_permissions_roles import (
            RoleSyncRefused,
        )

        a, b = self._hospital("fleet-rr1"), self._hospital("fleet-rr2")
        _settle(a), _settle(b)
        refuse = {"on": True}

        def check(hospital):
            if refuse["on"] and hospital.slug == "fleet-rr2":
                raise RoleSyncRefused(
                    "role sync would be refused: system role 'X' held"
                )

        packs, apply = self._no_packs()
        with (
            self._current(),
            mock.patch.object(fleet, "roles_check", side_effect=check),
            mock.patch.object(fleet, "roles_sync"),
            packs,
            apply,
        ):
            err = io.StringIO()
            call_command(
                "ihr_fleet_run",
                "check",
                "roles",
                "packs",
                startup=True,
                slug=self.slugs,
                stdout=io.StringIO(),
                stderr=err,
            )
            self.assertIn("ihr-fleet: hospital=fleet-rr2 outcome=held", err.getvalue())
            self.assertEqual(release.hold_reason(b), release.HOLD_ROLES)
            start = self._op("fleet-rr2").fleet_run
            self.assertEqual(self._op("fleet-rr2").status, "failed")
            refuse["on"] = False  # the fix: the role is registered again
            err, refused = io.StringIO(), None
            try:
                call_command(
                    "ihr_fleet_run",
                    resume=str(start.pk),
                    operator="tester",
                    stdout=io.StringIO(),
                    stderr=err,
                )
            except CommandError as exc:  # an assertion below, not an error
                refused = str(exc)
        self.assertIsNone(refused)  # exits 0
        self.assertTrue(release.schema_ready(b))
        self.assertTrue(release.schema_ready(a))
        self.assertNotIn("ihr-fleet:", err.getvalue())
        resumed = self._op("fleet-rr2").fleet_run
        self.assertEqual(
            (resumed.resumed_from_id, resumed.counts), (start.pk, {"done": 1})
        )

    def test_a_run_whose_roles_pass_never_lifts_a_window_hold(self):
        """C4/S2: only a hold a role refusal set is lifted that way; a release window's
        stays, and the hospital is reported held (never done) while it is not served."""
        a = self._hospital("fleet-win")
        _settle(a)
        call_command(
            "ihr_fleet_run",
            "hold",
            operator="tester",
            slug=[a.slug],
            stdout=io.StringIO(),
        )
        packs, apply = self._no_packs()
        err = io.StringIO()
        with (
            mock.patch.object(fleet, "roles_check"),
            mock.patch.object(fleet, "roles_sync"),
            packs,
            apply,
            self._current(),
        ):
            call_command(
                "ihr_fleet_run",
                "check",
                "roles",
                "packs",
                startup=True,
                slug=[a.slug],
                stdout=io.StringIO(),
                stderr=err,
            )
        self.assertEqual(release.hold_reason(a), release.HOLD_WINDOW)
        self.assertFalse(release.schema_ready(a))
        self.assertIn("ihr-fleet: hospital=fleet-win outcome=held", err.getvalue())
        op = self._op("fleet-win")
        self.assertEqual((op.result["outcome"], op.status), ("held", "failed"))

    def test_hospitals_not_served_by_design_are_never_alerted(self):
        """C7/S8: a provisioning (failed at its schema step) or offboarding hospital whose
        schema is not this build's gets no alert line at the start, and the run is not
        counted as failed for it."""
        self._hospital("fleet-prv", Hospital.Status.PROVISIONING)
        self._hospital("fleet-off", Hospital.Status.OFFBOARDING)
        out, err = io.StringIO(), io.StringIO()
        packs, apply = self._no_packs()
        with (
            mock.patch.object(fleet, "roles_check"),
            mock.patch.object(fleet, "roles_sync"),
            packs,
            apply,
        ):
            call_command(
                "ihr_fleet_run",
                "check",
                "roles",
                "packs",
                record=True,
                startup=True,
                slug=["fleet-prv", "fleet-off", "testhosp"],
                stdout=out,
                stderr=err,
            )
        self.assertNotIn("ihr-fleet:", err.getvalue())
        self.assertIn("fleet-prv: not served (provisioning", out.getvalue())
        run = self._op("fleet-prv").fleet_run
        self.assertEqual((run.status, run.counts), ("finished", {"done": 3}))

    def test_one_fleet_run_at_a_time(self):
        """C8: a second run (or a --resume of a run still going) refuses at once."""
        self._hospital("fleet-one")
        with (
            fleet.fleet_lock(),
            self.assertRaisesMessage(CommandError, "another ihr_fleet_run is running"),
        ):
            self._run(["migrate"])
        self.assertIsNone(self._op("fleet-one"))
        run, *_ = self._run(["migrate"])  # free again afterwards
        self.assertEqual(run.counts, {"done": 1})

    def test_the_worker_reports_each_live_hospital_not_served(self):
        """S3: every minute, the fixed line for each live hospital still not served, so the
        alert never resolves while it is down; never a provisioning one (S8)."""
        self._hospital("fleet-rpp", Hospital.Status.PROVISIONING)
        self._hospital("fleet-rpa")  # live, never recorded
        release.hold([testhosp().pk])
        lines = []
        with mock.patch.object(tasks, "_emit", side_effect=lines.append):
            tasks.report_unserved()
        self.assertIn("ihr-fleet: hospital=fleet-rpa outcome=held", lines)
        self.assertIn("ihr-fleet: hospital=testhosp outcome=held", lines)
        self.assertNotIn("ihr-fleet: hospital=fleet-rpp outcome=held", lines)
        release.release_hold([testhosp().pk])
        lines.clear()
        with mock.patch.object(tasks, "_emit", side_effect=lines.append):
            tasks.report_unserved()
        self.assertNotIn("ihr-fleet: hospital=testhosp outcome=held", lines)
        # a hold on purpose for a release window is the window's own business (its budget alert,
        # P1-T18b), never a critical "not served" every minute
        release.hold([testhosp().pk], reason=release.HOLD_WINDOW)
        lines.clear()
        with mock.patch.object(tasks, "_emit", side_effect=lines.append):
            tasks.report_unserved()
        self.assertNotIn("ihr-fleet: hospital=testhosp outcome=held", lines)
        release.release_hold([testhosp().pk])


class ProvisioningAcrossAReleaseTests(TestCase):
    """C3/O6: a provisioning run never goes on after ``schema`` with an older schema."""

    def setUp(self):
        # the runner switches the connection to public; every other test expects the tenant
        self.addCleanup(connection.set_schema, TEST_SCHEMA)

    def test_steps_after_schema_migrate_again_first(self):
        hospital = testhosp()
        op = SimpleNamespace(hospital_id=hospital.pk)
        record = {}
        with mock.patch.object(runner.steps, "schema") as schema:
            runner._schema_is_this_builds(op, None, record)
            schema.assert_not_called()
            with mock.patch.object(
                release, "code_fingerprint", return_value=OTHER_BUILD
            ):
                runner._schema_is_this_builds(op, None, record)
            schema.assert_called_once_with(op, None)
        self.assertIn("migrated_again_at", record)


class ForkedPoolTests(SimpleTestCase):
    """The real pool (design item 10): forked children on the test hospital, whose migration
    is a real no-op; the parent checks and records."""

    databases = {"default"}

    def setUp(self):
        connection.set_schema_to_public()
        self.hospital = testhosp()
        self.addCleanup(self._clean)

    def _clean(self):
        connection.set_schema_to_public()
        runs = set(
            TenantOperation.objects.filter(
                slug="testhosp", kind=TenantOperation.Kind.UPGRADE
            ).values_list("fleet_run", flat=True)
        )
        TenantOperation.objects.filter(
            slug="testhosp", kind=TenantOperation.Kind.UPGRADE
        ).delete()
        FleetRun.objects.filter(pk__in=runs - {None}).delete()
        release.release_hold([self.hospital.pk])
        release.record(self.hospital)
        connection.set_schema(TEST_SCHEMA)

    def _run(self, parallel=2, **options):
        err = []
        with mock.patch.object(fleet, "needs_migrate", return_value=True):
            run = fleet.Fleet(
                ["migrate"],
                operator="tester",
                slugs=["testhosp"],
                parallel=parallel,
                out=lambda line: None,
                err=err.append,
                **options,
            ).run()
        connection.set_schema_to_public()
        return run.operations.get(), err

    def test_a_forked_child_migrates_and_the_parent_records(self):
        before = HospitalSchemaState.objects.get(pk=self.hospital.pk).checked_at
        op, err = self._run()
        self.assertEqual(op.result["outcome"], "done", op.steps)
        self.assertEqual(op.steps[0]["attempts"], 1)
        state = HospitalSchemaState.objects.get(pk=self.hospital.pk)
        self.assertGreater(state.checked_at, before)
        self.assertIsNone(state.upgrading_since)
        self.assertEqual(state.schema_fingerprint, release.code_fingerprint())
        self.assertEqual(err, [])

    def test_a_crashed_child_is_tried_again_in_a_subprocess(self):
        import os

        def crash(schema_name, slug):
            os._exit(3)

        # manage.py is found from the settings, never the working directory (C9)
        self.addCleanup(os.chdir, Path.cwd())
        os.chdir(tempfile.gettempdir())
        with mock.patch.object(fleet, "migrate_one", side_effect=crash):
            op, err = self._run()
        self.assertEqual(op.result["outcome"], "done", op.steps)
        self.assertEqual(op.steps[0]["via"], "subprocess")
        self.assertIn("exit code 3", op.steps[0]["first_try"])
        self.assertTrue(any("trying once more in a subprocess" in e for e in err))
        self.assertTrue(release.schema_ready(self.hospital))

    def test_a_child_past_the_limit_is_stopped_and_the_hospital_stays_held(self):
        def slow(schema_name, slug):
            time.sleep(60)

        began = time.monotonic()
        with mock.patch.object(fleet, "migrate_one", side_effect=slow):
            op, err = self._run(timeout=2)
        self.assertLess(time.monotonic() - began, 30)
        self.assertEqual(op.result["outcome"], "deferred")
        self.assertIn("2 s limit", op.steps[0]["error"])
        self.assertFalse(release.schema_ready(self.hospital))
        self.assertIn("ihr-fleet: hospital=testhosp outcome=deferred", err)

    def test_a_stopped_child_s_database_session_ends_with_it(self):
        """C6/S9: the limit stops the migration's statement and its locks, not only the
        process; and it holds with --parallel 1 too (forked, never in-process)."""
        marker = f"ihr-fleet-stuck-{uuid.uuid4().hex[:8]}"

        def stuck(schema_name, slug):
            fleet._report_backend()
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT pg_sleep(120) /* {marker} */")

        began = time.monotonic()
        with mock.patch.object(fleet, "migrate_one", side_effect=stuck):
            op, _ = self._run(parallel=1, timeout=2)
        self.assertLess(time.monotonic() - began, 40)
        self.assertEqual(op.result["outcome"], "deferred")
        self.assertTrue(op.steps[0]["backend_stopped"])
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE query LIKE %s AND pid <> pg_backend_pid()",
                [f"%{marker}%"],
            )
            self.assertEqual(cursor.fetchone()[0], 0)
        self.assertFalse(release.schema_ready(self.hospital))


class MigrationNameTests(TestCase):
    """C5: the migration a failure names comes from Django itself, not a message that is
    never sent without a terminal. A real migration fails here: the newest ihr_audit one,
    applied again over its own changes (rolled back with the test)."""

    def setUp(self):
        self.addCleanup(connection.set_schema, TEST_SCHEMA)

    def test_a_real_failing_migration_is_named(self):
        leaf = max(n for a, n in _rows(TEST_SCHEMA) if a == "ihr_audit")
        with connection.cursor() as cursor:
            cursor.execute(
                f'DELETE FROM "{TEST_SCHEMA}".django_migrations '
                "WHERE app = 'ihr_audit' AND name = %s",
                [leaf],
            )
        connection.set_schema_to_public()
        result = fleet.migrate_one(TEST_SCHEMA, "testhosp")
        self.assertEqual(result["outcome"], "failed", result)
        self.assertRegex(result["error"], r"SQLSTATE [0-9A-Z]{5}")
        self.assertTrue(
            result["error"].endswith(f"; migration ihr_audit.{leaf}"), result["error"]
        )


class CeleryGateTests(TestCase):
    """S1: a task queued for a hospital that is held (or not on this build's schema) never
    runs its body there; it is sent again later, unchanged, and never failed."""

    def setUp(self):
        self.addCleanup(connection.set_schema, TEST_SCHEMA)

    def test_a_queued_task_never_runs_in_a_held_hospital(self):
        from config.celery_app import app
        from tenant_schemas_celery.task import TenantTask

        self.assertIs(TenantTask.__call__, celery_gate.gated_call)
        ran = []

        @app.task(name="ihr_platform.tests.release_gate_probe")
        def gate_probe(value):
            ran.append(value)
            return value

        probe = app.tasks[gate_probe.name]  # the task itself, not a lazy proxy

        def call(value):
            probe.push_request(
                id="t", args=(value,), kwargs={}, retries=2, is_eager=False
            )
            try:
                return probe(value)
            finally:
                probe.pop_request()

        hospital = testhosp()
        connection.set_tenant(hospital)
        self.assertEqual(call(1), 1)  # served: runs
        release.hold([hospital.pk])
        connection.set_tenant(hospital)
        again = mock.MagicMock()
        with (
            mock.patch.object(
                probe, "signature_from_request", return_value=again
            ) as sig,
            self.assertRaises(celery_gate.Ignore),
        ):
            call(2)
        self.assertEqual(ran, [1])
        sig.assert_called_once_with(
            countdown=celery_gate.DEFER_SECONDS, headers={"_schema_name": TEST_SCHEMA}
        )
        again.apply_async.assert_called_once_with()
        # Run eagerly, in its caller's own process (gated itself): not checked
        self.assertEqual(probe.apply(args=(3,)).get(), 3)


class PoolUnitTests(SimpleTestCase):
    def test_inline_inside_a_transaction_and_failures_caught(self):
        def work(n):
            if n == 2:
                raise DatabaseError("boom")
            return {"outcome": "done", "n": n}

        with mock.patch.object(fleet, "INLINE", new=True):
            results = fleet.run_pool(work, [(n, (n,)) for n in (1, 2, 3)], 1, 10)
        self.assertEqual(results[1], {"outcome": "done", "n": 1})
        self.assertEqual(results[2]["outcome"], "failed")
        self.assertIn("DatabaseError", results[2]["error"])

    def test_the_fallback_keeps_the_database_url_whole(self):
        """C9: the subprocess gets the URL the process was started with (TLS options and
        all); only a database the test runner renamed is swapped in."""
        url = (
            "postgres://u:p%40ss@db.example:5432/ihr"
            "?sslmode=verify-full&sslrootcert=/run/ca.pem"
        )
        with (
            mock.patch.dict(os.environ, {"DATABASE_URL": url}),
            mock.patch.dict(connection.settings_dict, {"NAME": "ihr"}),
        ):
            self.assertEqual(fleet._database_url(), url)
        with (
            mock.patch.dict(os.environ, {"DATABASE_URL": url}),
            mock.patch.dict(connection.settings_dict, {"NAME": "test_ihr"}),
        ):
            self.assertEqual(fleet._database_url(), url.replace("/ihr?", "/test_ihr?"))
        self.assertTrue((settings.BASE_DIR / "manage.py").is_file())
