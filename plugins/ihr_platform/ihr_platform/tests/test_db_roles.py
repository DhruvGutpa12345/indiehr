"""Separate database roles (P1-T19a; ihr_platform/db_roles.py, docs/reviews/p1-t19/design.md).

- ``ClassificationTests`` (every mode): each public table is classified, each audit-exempt
  hospital table has a class other than dml, and the lists name no table that is gone.
- ``AppRoleTests`` (``IHR_DB_ROLES=test`` only; skipped otherwise): the tests really run as
  ihr_app, and as ihr_app each control holds at the privilege level. Every statement that
  must be refused runs in a transaction that is rolled back even if it were allowed.
- ``GrantsTests`` (test mode): ensure_grants reports and repairs drift (extra rights to the
  app, to PUBLIC, column rights, the database's TEMPORARY), and the run-time check's queries.
- ``RefusalTests`` (every mode): a refused process stays refused (C1), and the rules for who
  may run as the owner.
- After the review of P1-T19a (docs/reviews/p1-t19/design.md, "Known limits"): the provisioner's
  queue (S1), repairs that keep column grants (C1), one hospital's drift gating that hospital
  only (C2), re-checks on later connections (S2), functions, views and grant options (S3, S4),
  the pre-start check's process (S5), sealing that never waits (S6) and is kept current (C5),
  large objects (S8), off mode (S10), the deferred post_migrate raise (C6), linear checks
  (C4) and role-changing SQL in tests (C8).
- After the second review: a new hospital's masking helper under the owner's default revoke
  (S3), functions the model does not list, broken controls refusing or gating at run time,
  large objects closed in required mode (S8), the worker repeating gated lines (C2), each
  migrate starting with nothing deferred (C6), no list read per schema (C4) and more
  role-changing SQL (C8).
"""

import inspect
import json
import os
import threading
import uuid
from unittest import SkipTest, mock

from django.core.management import call_command
from django.db import DatabaseError, connection, connections, transaction
from django.db.backends.signals import connection_created
from django.db.models.signals import post_migrate
from django.test import Client, SimpleTestCase, TestCase, override_settings
from psycopg import sql

from ihr_platform import db_roles, release, testing
from ihr_platform.models import Hospital, TenantOperation
from ihr_platform.provisioning import runner
from ihr_platform.settings import secure
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_platform.testing import as_owner


def sqlstate(error):
    return getattr(getattr(error, "__cause__", None), "sqlstate", None)


def fetch(statement, params=None):
    with connection.cursor() as cursor:
        cursor.execute(statement, params)
        return cursor.fetchone()


class ClassificationTests(SimpleTestCase):
    databases = {"default"}

    def test_every_public_table_is_classified(self):
        self.assertEqual(db_roles.classification_problems(connection, "public"), [])

    def test_every_table_the_trail_does_not_record_is_not_writable(self):
        """S5/C10: an audit-exempt table (django_migrations, the trail itself...) or one
        shaped like an append-only one is never plain dml for the app."""
        self.assertEqual(db_roles.classification_problems(connection, TEST_SCHEMA), [])
        from ihr_audit.install import EXEMPT

        classes = db_roles.hospital_classes()
        for table in EXEMPT:
            self.assertNotEqual(classes.get(table, "dml"), "dml", table)
        self.assertEqual(classes["django_migrations"], "select")
        self.assertEqual(classes["ihr_audit_entry"], "append")
        self.assertEqual(classes["ihr_audit_seal"], "select")

    def test_what_the_provisioner_trusts_is_read_only_for_the_app(self):
        """S1: the owner-run provisioner acts on these; the app may only read them, and may
        set only who queued what on an operation."""
        classes = db_roles.public_classes()
        for table in (
            "ihr_platform_hospital",
            "ihr_platform_domain",
            "ihr_platform_retiredaddress",
            "ihr_platform_operatoraccount",
            "ihr_platform_hospitalschemastate",
            "django_migrations",
            "ihr_terminology_concept",
            "ihr_terminology_designation",
            "ihr_terminology_codesystemversion",
        ):
            self.assertEqual(classes[table], "select", table)
        self.assertEqual(classes["ihr_platform_tenantoperation"], "queue")
        self.assertEqual(
            db_roles.CLASSES["queue"],
            db_roles.Grant(
                frozenset({"SELECT", "INSERT"}),
                (("UPDATE", ("queued_action", "queued_by", "queued_at")),),
            ),
        )

    def test_the_lists_need_a_reason_and_a_known_class(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "list.txt")
            for text, error in (
                ("t dml\n", "a reason is required"),
                ("t write because\n", "not one of"),
                ("t dml one\nt select two\n", "listed twice"),
            ):
                path.write_text(text)
                with (
                    self.subTest(text=text),
                    self.assertRaisesRegex(db_roles.RolesError, error),
                ):
                    db_roles.read_list(path)

    def test_only_public_and_hospital_schemas_are_managed(self):
        """S7: never another schema, whatever a row says (pg_catalog, a parked copy)."""
        for schema in ("pg_catalog", "information_schema", "ihr_parked_h_x", "h_short"):
            with self.subTest(schema=schema), self.assertRaises(db_roles.RolesError):
                db_roles.schema_kind(schema)

    def test_required_mode_refuses_a_cluster_without_the_roles(self):
        """C4: required mode needs every role (made with the move, P1-T19b)."""
        with (
            override_settings(IHR_DB_ROLES="required"),
            self.assertRaisesRegex(db_roles.RolesError, "ihr_owner"),
        ):
            db_roles.ensure_grants(connection, "public")

    def test_off_does_nothing(self):
        with override_settings(IHR_DB_ROLES="off"):
            self.assertEqual(len(db_roles.ensure_grants(connection)), 0)
            self.assertEqual(len(db_roles.report(connection)), 0)


class AppRoleCase(SimpleTestCase):
    databases = {"default"}

    @classmethod
    def setUpClass(cls):
        if not testing.active():
            raise SkipTest("needs IHR_DB_ROLES=test (the tests run as ihr_app)")
        super().setUpClass()

    def setUp(self):
        connection.set_schema(TEST_SCHEMA)  # unqualified names below are the hospital's

    def refused(self, statement, params=None, message="permission denied"):
        """``statement`` must fail as ihr_app with insufficient_privilege (42501). It runs
        in a transaction that is rolled back even if it were allowed."""
        error = None
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(statement, params)
                transaction.set_rollback(True)
        except DatabaseError as exc:
            error = exc
        if error is None:
            self.fail(f"ihr_app was allowed: {statement}")
        self.assertEqual(sqlstate(error), "42501", str(error))
        self.assertIn(message, str(error))

    def allowed(self, statement, params=None):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(statement, params)
            transaction.set_rollback(True)


class AppRoleTests(AppRoleCase):
    def test_tests_run_as_the_app_role(self):
        self.assertEqual(fetch("SELECT current_user")[0], db_roles.APP)
        seen = {}

        def worker():
            try:
                seen["user"] = fetch("SELECT current_user")[0]
            finally:
                connections["default"].close()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(30)
        self.assertEqual(seen["user"], db_roles.APP)  # thread connections too (C5)

    def test_the_owner_only_on_purpose(self):
        with as_owner():
            self.assertNotEqual(fetch("SELECT current_user")[0], db_roles.APP)
            with as_owner():  # nests
                self.assertNotEqual(fetch("SELECT current_user")[0], db_roles.APP)
            self.assertNotEqual(fetch("SELECT current_user")[0], db_roles.APP)
        self.assertEqual(fetch("SELECT current_user")[0], db_roles.APP)

    def test_a_query_that_silently_became_the_superuser_is_caught(self):
        """C6/S8: a rollback or a stray RESET ROLE must never let a test run as the
        superuser unnoticed."""
        connection.ensure_connection()
        connection.connection.execute("RESET ROLE")
        try:
            with self.assertRaisesRegex(AssertionError, "not ihr_app"):
                fetch("SELECT 1")
        finally:
            with as_owner():
                pass  # back to the app role
        self.assertEqual(fetch("SELECT current_user")[0], db_roles.APP)

    def test_requests_run_as_the_app_role(self):
        """The request path itself (middleware, the audit entry) runs as ihr_app."""
        seen = []

        def spy(execute, statement, params, many, context):
            raw = context["connection"].connection
            if not seen and raw is not None:
                seen.append(raw.execute("SELECT current_user").fetchone()[0])
            return execute(statement, params, many, context)

        with connection.execute_wrapper(spy):
            Client(HTTP_HOST="testserver").get("/api/v1/users/getcurrentuser/")
        self.assertEqual(seen, [db_roles.APP])

    def test_the_workers_run_as_the_app_role(self):
        """C5: what celery-worker, celery-beat and the staff worker do, run here as ihr_app
        (each also has its own tests, all run as the app role): the audit seal and
        fingerprint snapshot, the per-minute report, beat's hospital list, the staff
        worker's queue."""
        from unittest import mock as _mock

        from ihr_audit import anchor
        from ihr_audit import tasks as audit_tasks

        from ihr_platform import invitations, tasks
        from ihr_platform.beat import ActiveTenantScheduler

        seen = []

        def spy(execute, statement, params, many, context):
            raw = context["connection"].connection
            seen.append(raw.execute("SELECT current_user").fetchone()[0])
            return execute(statement, params, many, context)

        with connection.execute_wrapper(spy):
            audit_tasks.seal_audit_trail()  # in the hospital's schema
            connection.set_schema_to_public()
            self.assertIn("testhosp", {h["slug"] for h in anchor.snapshot().values()})
            with _mock.patch.object(tasks, "_emit"):
                tasks.report_unserved()
            self.assertIsInstance(list(ActiveTenantScheduler.get_queryset()), list)
            with transaction.atomic():
                invitations.claim_next()
                invitations.expire_due()
                transaction.set_rollback(True)
            connection.set_schema(TEST_SCHEMA)
        self.assertTrue(seen)
        self.assertEqual(set(seen), {db_roles.APP})

    def test_no_ddl(self):
        self.refused(
            "ALTER TABLE emr_patient DISABLE TRIGGER ihr_audit", message="must be owner"
        )
        self.refused(
            f"ALTER TABLE {TEST_SCHEMA}.ihr_audit_entry DISABLE TRIGGER USER",
            message="must be owner",
        )
        self.refused(f"DROP SCHEMA {TEST_SCHEMA} CASCADE", message="must be owner")
        self.refused("DROP TABLE emr_patient", message="must be owner")
        self.refused("CREATE SCHEMA ihr_p1t19a_probe")
        self.refused(f"CREATE TABLE {TEST_SCHEMA}.ihr_p1t19a_probe (id int)")
        self.refused("CREATE TABLE public.ihr_p1t19a_probe (id int)")
        self.refused("CREATE TEMP TABLE ihr_p1t19a_probe (id int)")
        self.refused(
            "CREATE TRIGGER ihr_p1t19a_probe BEFORE INSERT ON emr_patient "
            "FOR EACH ROW EXECUTE FUNCTION ihr_audit_refuse()"
        )
        self.refused("TRUNCATE emr_patient")
        self.refused("SET session_replication_role = replica")
        self.refused("ALTER ROLE ihr_audit_watch NOLOGIN")

    def test_terminology_is_read_only_even_with_the_loaders_flag(self):
        """The guard trigger is a session flag any session can set; the privilege is not."""
        with transaction.atomic():
            fetch("SELECT set_config('ihr.terminology_load', 'on', true)")
            for statement in (
                "INSERT INTO public.ihr_terminology_codesystemversion DEFAULT VALUES",
                "UPDATE public.ihr_terminology_concept SET code = code WHERE false",
                "DELETE FROM public.ihr_terminology_designation WHERE false",
            ):
                with self.subTest(statement=statement):
                    self.refused(statement)
            transaction.set_rollback(True)
        self.assertGreater(
            fetch("SELECT count(*) FROM public.ihr_terminology_concept")[0], -1
        )

    def test_audit_entries_are_append_only_by_privilege(self):
        """Refused by the privilege itself, before the append-only trigger (a second line)."""
        for statement in (
            "UPDATE ihr_audit_entry SET actor = 'x' WHERE false",
            "DELETE FROM ihr_audit_entry WHERE false",
            "TRUNCATE ihr_audit_entry",
            "UPDATE ihr_audit_seal SET entries = 1 WHERE false",
            "DELETE FROM ihr_audit_seal WHERE false",
        ):
            with self.subTest(statement=statement):
                self.refused(
                    statement, message="permission denied for table ihr_audit_"
                )

    def test_an_entrys_id_txid_time_and_salt_are_the_databases(self):
        """S2: no backdating, no slipping into a sealed window, no pre-claimed ids."""
        for column, value in (
            ("txid", "'3'::xid8"),
            ("at", "'2026-01-01 10:00+05:30'"),
            ("salt", "'\\x00'::bytea"),
        ):
            with self.subTest(column=column):
                self.refused(
                    f"INSERT INTO ihr_audit_entry ({column}, kind, actor) "  # noqa: S608
                    f"VALUES ({value}, 'request', 'test:p1-t19a')"
                )
        self.refused(
            "INSERT INTO ihr_audit_entry (id, kind, actor) OVERRIDING SYSTEM VALUE "
            "VALUES (9000000000, 'request', 'test:p1-t19a')"
        )
        # The data columns, as the middleware writes them: the database stamps the rest
        with transaction.atomic():
            row = fetch(
                "INSERT INTO ihr_audit_entry (kind, actor, data) "
                "VALUES ('request', 'test:p1-t19a', '{\"probe\": true}') "
                "RETURNING txid = pg_current_xact_id(), salt IS NOT NULL"
            )
        self.assertEqual(row, (True, True))

    def test_seals_are_made_only_by_the_database(self):
        """S3: no forged seal; the app seals through ihr_audit_seal_next() alone."""
        from ihr_audit.seal import seal_once, verify

        self.refused(
            "INSERT INTO ihr_audit_seal (seq, lower_txid, upper_txid, entries, "
            "entries_hash, prev_hash, hash) VALUES (900000, '9000000000000000000', "
            "'9000000000000000001', 1, '', '', '')"
        )
        self.refused(f"SELECT {TEST_SCHEMA}.ihr_audit_seal_run(3, 3::smallint)")
        seal_once(TEST_SCHEMA)  # as the app, as the worker does
        self.assertEqual(verify(TEST_SCHEMA)[0], [])

    def test_a_hospitals_migrations_cannot_be_faked(self):
        """C10/S5: P1-T18's gate and a later security migration rely on this table."""
        self.refused(
            "INSERT INTO django_migrations (app, name, applied) "
            "VALUES ('ihr_audit', '9999_fake', now())"
        )
        self.refused("DELETE FROM django_migrations WHERE false")
        self.refused("UPDATE django_migrations SET name = name WHERE false")
        self.assertGreater(fetch("SELECT count(*) FROM django_migrations")[0], 0)

    def test_the_provisioners_inputs_are_read_only(self):
        """S1: the app cannot re-point, revive or forge what the owner-run provisioner acts
        on; it may queue an operation and set only the queue columns."""
        self.refused(
            "UPDATE public.ihr_platform_hospital SET status = 'provisioning', "
            "activated_at = NULL WHERE false"
        )
        self.refused(
            "INSERT INTO public.ihr_platform_domain (domain, tenant_id, is_primary) "
            "VALUES ('x.localhost', 1, false)"
        )
        self.refused("DELETE FROM public.ihr_platform_retiredaddress WHERE false")
        self.refused(
            "UPDATE public.ihr_platform_operatoraccount SET active = true WHERE false"
        )
        self.refused(
            "UPDATE public.ihr_platform_hospitalschemastate SET upgrading_since = NULL "
            "WHERE false"
        )
        for column in ("status", "steps", "result", "hospital_id", "input", "kind"):
            with self.subTest(column=column):
                self.refused(
                    f"UPDATE public.ihr_platform_tenantoperation "  # noqa: S608
                    f"SET {column} = {column} WHERE false"
                )
        self.refused("DELETE FROM public.ihr_platform_tenantoperation WHERE false")
        self.allowed(
            "UPDATE public.ihr_platform_tenantoperation SET queued_action = 'run', "
            "queued_by = 'x', queued_at = now() WHERE false"
        )

    def test_a_forged_operation_is_only_a_new_request(self):
        """S1: the app may insert an operation (the console's request), but every column the
        owner-run provisioner trusts is the database's: a forged "succeeded" operation naming
        a live hospital becomes a pending request for nothing, and only the owner records
        roll-backs and upgrades."""
        hospital_id = Hospital.objects.get(schema_name=TEST_SCHEMA).pk
        with transaction.atomic():
            row = fetch(
                "INSERT INTO public.ihr_platform_tenantoperation (id, kind, slug, status, "
                "input, steps, result, requested_by, queued_action, queued_by, queued_at, "
                "created_at, started_at, finished_at, hospital_id) VALUES (%s, 'provision', "
                "'p1t19a-forged', 'succeeded', '{\"admin_email\": \"x@example.test\"}', "
                '\'[{"name": "reserve", "status": "succeeded"}, 7]\', '
                '\'{"hospital_uuid": "u", "admin_user_id": 1}\', \'forger\', '
                "'resend_setup', 'forger', now(), now(), now(), now(), %s) "
                "RETURNING status, steps, result, hospital_id, started_at, finished_at, "
                "fleet_run_id, input, queued_action",
                [uuid.uuid4(), hospital_id],
            )
            transaction.set_rollback(True)
        row = tuple(json.loads(v) if i in (1, 2, 7) else v for i, v in enumerate(row))
        self.assertEqual(
            row,
            (
                "pending",
                [{"name": "reserve", "status": "pending"}],
                {},
                None,
                None,
                None,
                None,
                {"admin_email": "x@example.test"},  # the request itself is the app's
                "resend_setup",
            ),
        )
        for kind in ("rollback", "upgrade"):
            with self.subTest(kind=kind):
                self.refused(
                    "INSERT INTO public.ihr_platform_tenantoperation (id, kind, slug, "
                    "status, input, steps, result, requested_by, queued_action, queued_by, "
                    "created_at) VALUES (%s, %s, 'testhosp', 'running', '{}', '[]', '{}', "
                    "'forger', '', '', now())",
                    [uuid.uuid4(), kind],
                    message="only the owner records",
                )

    def test_a_large_object_shows_up_as_owned(self):
        """S8: any role may make a large object (Postgres's default); one the app owns is a
        finding at its next start (the channel itself is a recorded limit)."""
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT lo_create(0)")
            found = [r for r, _ in db_roles.app_problems(cursor)]
            transaction.set_rollback(True)
        self.assertIn("owns_objects", found)

    def test_off_mode_refuses_the_split_roles(self):
        """S10: a process that connects as ihr_app (or ihr_owner) with IHR_DB_ROLES=off."""
        with connection.cursor() as cursor:
            self.assertEqual(
                [r for r, _ in db_roles.off_problems(cursor)], ["roles_off"]
            )
            with as_owner(), connection.cursor() as owner_cursor:
                self.assertEqual(db_roles.off_problems(owner_cursor), [])
                # Second review: on a server, a superuser login is refused too
                with override_settings(IHR_ENVIRONMENT="staging"):
                    self.assertEqual(
                        [r for r, _ in db_roles.off_problems(owner_cursor)],
                        ["superuser"],
                    )

    def test_a_query_that_changes_the_role_is_refused(self):
        """C8: the check before a query cannot see a role change inside the same text. Second
        review: the parameter forms too (session_authorization, DISCARD ALL, a set_config
        whose name is a query parameter)."""
        for statement, params in (
            ("SELECT 1; RESET ROLE", None),
            ("SET LOCAL lock_timeout = '1s'; SET SESSION AUTHORIZATION DEFAULT", None),
            ("SELECT set_config('role', 'none', false)", None),
            ("select 1;\nset role postgres", None),
            ("SET session_authorization = DEFAULT", None),
            ("RESET session_authorization", None),
            ("DISCARD ALL", None),
            ("SELECT set_config('session_authorization', 'postgres', false)", None),
            ("SELECT set_config(%s, %s, false)", ["role", "none"]),
        ):
            with self.subTest(statement=statement):
                try:
                    with self.assertRaisesRegex(
                        AssertionError, "changes the database role"
                    ):
                        fetch(statement, params)
                finally:
                    with as_owner():
                        pass  # back to the app role, whatever happened
                self.assertEqual(fetch("SELECT current_user")[0], db_roles.APP)
        # An UPDATE of a column named role changes no role
        for statement in (
            'UPDATE "x" SET "role" = %s WHERE false',
            "UPDATE x SET role = 1, role_id = 2 WHERE false",
            "SELECT set_config('ihr.audit_actor', %s, false)",
        ):
            with self.subTest(statement=statement):
                self.assertFalse(testing._changes_role(statement, ["role"]))

    def test_the_app_role_cannot_keep_settings_for_itself(self):
        """S9: a role may store its own defaults (a statement time-out, a read-only switch);
        the run-time check refuses a role that has any."""
        with connection.cursor() as cursor:
            self.assertNotIn(
                "role_settings", [r for r, _ in db_roles.app_problems(cursor)]
            )
            # (an ordinary role may store any parameter it may set; placeholders such as
            # ihr.audit_actor are refused for it on Postgres 17, statement_timeout is not)
            cursor.execute("ALTER ROLE ihr_app SET statement_timeout = '1h'")
            try:
                found = [r for r, _ in db_roles.app_problems(cursor)]
                # S2: read again on every later connection, not only the first
                again = [r for r, _ in db_roles.reconnect_problems(cursor)]
            finally:
                cursor.execute("ALTER ROLE ihr_app RESET ALL")
        self.assertIn("role_settings", found)
        self.assertIn("role_settings", again)

    def test_a_setting_the_app_stores_in_another_database_counts_and_is_named(self):
        """Review S1/C3: Postgres lets a role store its own default IN DATABASE any database,
        one it cannot connect to included. The run-time check and migrate's role check count
        it and name the database, so the runbook (db-roles --assert, which resets every
        database) and the operator know where it is."""
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER ROLE ihr_app IN DATABASE postgres SET statement_timeout = '1h'"
            )
            try:
                found = dict(db_roles.app_problems(cursor))
                again = dict(db_roles.reconnect_problems(cursor))
                roles = db_roles.role_problems(cursor, "test")
            finally:
                cursor.execute("ALTER ROLE ihr_app IN DATABASE postgres RESET ALL")
            self.assertNotIn(
                "role_settings", [r for r, _ in db_roles.app_problems(cursor)]
            )
        self.assertEqual(
            found["role_settings"],
            "ihr_app has stored settings in postgres (ALTER ROLE ... SET)",
        )
        self.assertEqual(again["role_settings"], found["role_settings"])
        self.assertIn(
            (
                "problem",
                "role ihr_app has stored settings in postgres (ALTER ROLE ... RESET ALL, "
                "in each: scripts/staging.sh db-roles --assert)",
            ),
            roles,
        )


class GrantsTests(AppRoleCase):
    def test_the_model_holds_after_set_up(self):
        findings = db_roles.report(connection, ["public", TEST_SCHEMA])
        self.assertEqual([str(f) for f in findings], [])

    def test_the_run_time_checks_pass_for_the_app_role(self):
        """As ihr_app (switched to with SET ROLE: its session user is the superuser, which
        is all the check names here)."""
        with connection.cursor() as cursor:
            found = db_roles.app_problems(cursor)
            self.assertEqual([r for r, _ in found], ["wrong_role"], found)
            self.assertEqual(db_roles.effective_public_problems(cursor), [])

    def _drift(self, statement, expect, kind="extra"):
        with as_owner(), connection.cursor() as cursor:
            cursor.execute(statement)
        try:
            texts = [
                f.text
                for f in db_roles.report(connection, ["public", TEST_SCHEMA]).of(kind)
            ]
            self.assertTrue(any(expect in t for t in texts), texts)
        finally:
            with as_owner():
                db_roles.ensure_grants(connection, ["public", TEST_SCHEMA])
        self.test_the_model_holds_after_set_up()

    def test_drift_is_reported_and_repaired(self):
        cases = (
            (
                f"GRANT UPDATE ON {TEST_SCHEMA}.ihr_audit_entry TO ihr_app",
                f"{TEST_SCHEMA}.ihr_audit_entry: ihr_app has UPDATE",
            ),
            (
                f"GRANT SELECT ON {TEST_SCHEMA}.emr_patient TO PUBLIC",
                f"{TEST_SCHEMA}.emr_patient: PUBLIC has SELECT",
            ),
            (
                "GRANT UPDATE (status) ON public.ihr_platform_tenantoperation TO ihr_app",
                "ihr_app has UPDATE on column(s) status",
            ),
            (
                "GRANT INSERT ON public.ihr_terminology_concept TO ihr_audit_watch",
                "ihr_audit_watch has INSERT",
            ),
            (
                f"GRANT CREATE ON SCHEMA {TEST_SCHEMA} TO ihr_app",
                f"schema {TEST_SCHEMA}: ihr_app has CREATE",
            ),
            (
                f"GRANT EXECUTE ON FUNCTION {TEST_SCHEMA}.ihr_audit_seal_run(integer, "
                "smallint) TO PUBLIC",
                "ihr_audit_seal_run(): PUBLIC has EXECUTE",
            ),
            (
                sql.SQL("GRANT TEMPORARY ON DATABASE {} TO PUBLIC").format(
                    sql.Identifier(connection.settings_dict["NAME"])
                ),
                "PUBLIC has TEMPORARY",
            ),
            (  # S3: a grant option lets the app pass a right on
                f"GRANT SELECT ON {TEST_SCHEMA}.emr_patient TO ihr_app WITH GRANT OPTION",
                f"{TEST_SCHEMA}.emr_patient: ihr_app may grant SELECT to others",
            ),
            (
                f"GRANT INSERT (kind) ON {TEST_SCHEMA}.ihr_audit_entry TO ihr_app "
                "WITH GRANT OPTION",
                "ihr_audit_entry.kind: ihr_app may grant INSERT to others",
            ),
            (
                sql.SQL(
                    "GRANT CONNECT ON DATABASE {} TO ihr_app WITH GRANT OPTION"
                ).format(sql.Identifier(connection.settings_dict["NAME"])),
                "ihr_app may grant CONNECT to others",
            ),
        )
        for statement, expect in cases:
            with self.subTest(expect=expect):
                self._drift(statement, expect)
        self._drift(
            f"REVOKE SELECT ON {TEST_SCHEMA}.emr_patient FROM ihr_app",  # noqa: S608
            f"{TEST_SCHEMA}.emr_patient: ihr_app lacks SELECT",
            kind="missing",
        )

    def test_a_table_revoke_keeps_the_column_grants(self):
        """C1: revoking a stray table-level INSERT (UPDATE) also takes the column-level one
        (Postgres, REVOKE); one repair still ends with the app able to write the trail and
        queue work."""
        for statement, expect in (
            (
                f"GRANT INSERT ON {TEST_SCHEMA}.ihr_audit_entry TO ihr_app",
                f"{TEST_SCHEMA}.ihr_audit_entry: ihr_app has INSERT",
            ),
            (
                "GRANT UPDATE ON public.ihr_platform_tenantoperation TO ihr_app",
                "public.ihr_platform_tenantoperation: ihr_app has UPDATE",
            ),
        ):
            with self.subTest(expect=expect):
                with as_owner(), connection.cursor() as cursor:
                    cursor.execute(statement)
                texts = [f.text for f in db_roles.report(connection).of("extra")]
                self.assertIn(expect, texts)
                with as_owner():
                    db_roles.ensure_grants(connection, ["public", TEST_SCHEMA])  # once
                self.test_the_model_holds_after_set_up()
        self.allowed(
            "INSERT INTO ihr_audit_entry (kind, actor) VALUES ('request', 'test:c1')"
        )
        self.allowed(
            "UPDATE public.ihr_platform_tenantoperation SET queued_at = now() WHERE false"
        )

    def test_drift_in_one_hospital_gates_only_that_hospital(self):
        """C2: a right beyond the model in a hospital's schema does not stop the process (it
        would stop every hospital); that hospital alone is gated. In public it still
        refuses."""
        name = sql.Identifier(connection.settings_dict["NAME"])
        for grant, revoke, refused, gated in (
            (
                f"GRANT UPDATE ON {TEST_SCHEMA}.ihr_audit_entry TO ihr_app",
                f"REVOKE UPDATE ON {TEST_SCHEMA}.ihr_audit_entry FROM ihr_app",
                [],
                [TEST_SCHEMA],
            ),
            (
                sql.SQL("GRANT CREATE ON DATABASE {} TO ihr_audit_watch").format(name),
                sql.SQL("REVOKE CREATE ON DATABASE {} FROM ihr_audit_watch").format(
                    name
                ),
                [],  # another role's right: the owner's to repair, not a refusal
                [],
            ),
            (
                "GRANT UPDATE ON public.ihr_platform_hospital TO ihr_app",
                "REVOKE UPDATE ON public.ihr_platform_hospital FROM ihr_app",
                ["extra_privileges"],
                [],
            ),
        ):
            with self.subTest(grant=str(grant)):
                with as_owner(), connection.cursor() as cursor:
                    cursor.execute(grant)
                try:
                    found_gated = {}
                    with mock.patch.object(db_roles, "app_problems", return_value=[]):
                        found = db_roles.check_connection(
                            connection, "app", gated=found_gated
                        )
                finally:
                    with as_owner(), connection.cursor() as cursor:
                        cursor.execute(revoke)
                self.assertEqual(sorted({r for r, _ in found}), refused, found)
                self.assertEqual(TEST_SCHEMA in found_gated, bool(gated), found_gated)

    def test_the_check_reads_each_schema_once(self):
        """C4: the per-process check is linear in the fleet: one schema's findings never walk
        every other schema's tables."""
        with connection.cursor() as cursor:
            state = db_roles.read_state(cursor, ["public", TEST_SCHEMA])

        class Unscannable(dict):
            def _refuse(self, *args):
                raise AssertionError("walked every schema's relations")

            __iter__ = items = keys = values = _refuse

        state["relations"] = Unscannable(state["relations"])
        state["sequences"] = Unscannable(state["sequences"])
        classes = {
            "public": db_roles.public_classes(),
            "hospital": db_roles.hospital_classes(),
        }
        # ... and reads no list file: the run reads each once (second review of C4)
        with mock.patch.object(
            db_roles, "read_list", side_effect=AssertionError("read a list per schema")
        ):
            for schema, kind in (("public", "public"), (TEST_SCHEMA, "hospital")):
                db_roles.schema_findings(state, schema, "test", classes[kind])

    def test_functions_views_and_definers_beyond_the_model(self):
        """S3/S4: an unknown SECURITY DEFINER function is a problem; a view gets nothing by
        default (it reads with its owner's rights) and must be listed; the sealing function's
        owner is checked in required mode."""
        with as_owner(), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE FUNCTION {TEST_SCHEMA}.ihr_p1t19a_definer() RETURNS int "
                    "LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'"
                )
                cursor.execute(
                    f"CREATE VIEW {TEST_SCHEMA}.ihr_p1t19a_view AS "  # noqa: S608
                    f"SELECT * FROM {TEST_SCHEMA}.django_migrations"
                )
            texts = [str(f) for f in db_roles.report(connection, [TEST_SCHEMA])]
            gaps = db_roles.classification_problems(connection, TEST_SCHEMA)
            with connection.cursor() as cursor:
                state = db_roles.read_state(cursor, [TEST_SCHEMA])
            required = [
                str(f)
                for f in db_roles.schema_findings(
                    state, TEST_SCHEMA, "required", db_roles.hospital_classes()
                )
            ]
            transaction.set_rollback(True)
        self.assertTrue(
            any(
                "ihr_p1t19a_definer(): a SECURITY DEFINER function" in t for t in texts
            ),
            texts,
        )
        self.assertFalse(
            any("ihr_p1t19a_view: ihr_app lacks" in t for t in texts), texts
        )
        self.assertTrue(
            any(g.startswith("ihr_p1t19a_view: a view") for g in gaps), gaps
        )
        self.assertTrue(
            any(".ihr_audit_seal_next() is owned by" in t for t in required), required
        )

    def test_new_functions_are_nobodys_by_default(self):
        """S3: required mode revokes Postgres's default EXECUTE to PUBLIC on the owner's new
        functions (with a throwaway ihr_owner in a rolled-back transaction)."""
        with as_owner(), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", [db_roles.OWNER]
            )
            if cursor.fetchone():
                transaction.set_rollback(True)
                self.skipTest("this cluster has a real ihr_owner")
            cursor.execute("CREATE ROLE ihr_owner NOLOGIN")
            before = [f.text for f in db_roles.default_findings(cursor).of("extra")]
            cursor.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE ihr_owner IN SCHEMA public "
                "GRANT EXECUTE ON FUNCTIONS TO PUBLIC"
            )
            cursor.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE ihr_owner REVOKE EXECUTE ON FUNCTIONS "
                "FROM PUBLIC"
            )
            after = [f.text for f in db_roles.default_findings(cursor).of("extra")]
            transaction.set_rollback(True)
        self.assertIn(
            "default privileges of ihr_owner: PUBLIC gets EXECUTE on new functions",
            before,
        )
        self.assertNotIn(
            "default privileges of ihr_owner: PUBLIC gets EXECUTE on new functions",
            after,
        )
        self.assertIn(
            "default privileges of ihr_owner in public: PUBLIC gets EXECUTE on new "
            "functions",
            after,
        )

    def test_a_new_hospitals_masking_helper_is_the_apps(self):
        """Second review of S3: in required mode the owner's new functions are nobody's (the
        default revoke), so a new hospital schema's ihr_audit_last4 is too. The row trigger
        runs as the app and calls it directly (a trigger function fires without EXECUTE, a
        function it calls does not): the grants must give it to the app, or every app write
        of a masked identifier fails. Simulated on the test hospital, as the test database's
        owner, in a rolled-back transaction: the default revoke, the helper made again as for
        a new schema (ensure_triggers), the grants (post_migrate), then a write as ihr_app."""
        from care.emr.models import Patient
        from care.emr.models.patient import PatientIdentifier
        from ihr_audit.install import ensure_triggers
        from model_bakery import baker

        helper = f"{TEST_SCHEMA}.ihr_audit_last4(jsonb)"
        with transaction.atomic():
            with as_owner():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT current_user")
                    owner = sql.Identifier(cursor.fetchone()[0])
                    cursor.execute(
                        sql.SQL(
                            "ALTER DEFAULT PRIVILEGES FOR ROLE {} REVOKE EXECUTE ON "
                            "FUNCTIONS FROM PUBLIC"
                        ).format(owner)
                    )
                    cursor.execute(f"DROP FUNCTION {helper}")
                ensure_triggers(connection, TEST_SCHEMA)  # as for a new hospital
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT has_function_privilege('ihr_app', %s, 'EXECUTE')",
                        [helper],
                    )
                    self.assertEqual(cursor.fetchone(), (False,))  # nobody's now
                db_roles.ensure_grants(connection, [TEST_SCHEMA])  # post_migrate
            try:
                patient = baker.make(Patient)  # as ihr_app from here on
                identifier = baker.make(
                    PatientIdentifier, patient=patient, value="12345678901234"
                )
                identifier.value = "12345678909999"
                identifier.save()
            except DatabaseError as exc:
                # An assertion failure, not an error: the mutation row expects a FAIL.
                self.fail(f"the app cannot write a masked identifier: {exc}")
            row = fetch(
                "SELECT data->'value' FROM ihr_audit_entry WHERE tbl = "
                "'emr_patientidentifier' AND op = 'U' ORDER BY id DESC LIMIT 1"
            )
            transaction.set_rollback(True)
        self.assertEqual(json.loads(row[0]), ["***1234", "***9999"])

    def test_a_function_the_model_does_not_list_is_a_finding(self):
        """Second review of S3: a function that is not a trigger function and is not in
        db_roles.FUNCTIONS (such as a new helper a trigger calls) is a problem until its
        EXECUTE is decided; a trigger function is not (it fires without EXECUTE)."""
        with as_owner(), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE FUNCTION {TEST_SCHEMA}.ihr_p1t19a_helper(v int) RETURNS int "
                    "LANGUAGE sql AS 'SELECT v'"
                )
                cursor.execute(
                    f"CREATE FUNCTION {TEST_SCHEMA}.ihr_p1t19a_trigger() RETURNS trigger "
                    "LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'"
                )
            texts = [str(f) for f in db_roles.report(connection, [TEST_SCHEMA])]
            transaction.set_rollback(True)
        self.assertIn(
            f"problem: {TEST_SCHEMA}.ihr_p1t19a_helper(v integer): a function the model "
            "does not list (who may execute it? A helper a trigger calls needs EXECUTE for "
            "the app: add it to db_roles.FUNCTIONS)",
            texts,
        )
        self.assertFalse(any("ihr_p1t19a_trigger" in t for t in texts), texts)

    def test_a_broken_control_refuses_or_gates(self):
        """Second review (S1 and S3 at run time): a broken control is not only counted. The
        queue guard (public) refuses the process. A sealing function that lost its security
        or search path, one changed by hand since it was installed, or the stamp trigger
        switched off gates its hospital; an older build's sealing functions, unchanged as
        installed, do not (the schema's next migrate brings them up to date, and a release
        that changes their SQL alone must not stop every hospital). An unknown SECURITY
        DEFINER function the app may execute refuses the process even in a hospital's schema
        (a way to its owner's rights from every hospital served); one the app may not
        execute is a problem for the owner, neither a gate nor a refusal."""
        from ihr_audit import seal

        definer = f"{TEST_SCHEMA}.ihr_p1t19a_definer()"
        make_definer = (
            f"CREATE FUNCTION {definer} RETURNS int LANGUAGE sql SECURITY DEFINER "
            "AS 'SELECT 1'"
        )
        older_build = mock.patch.object(seal, "SEALING_VERSION", "0" * 64)
        for statements, refused, gated, patch in (
            (
                [
                    "ALTER TABLE public.ihr_platform_tenantoperation "
                    "DISABLE TRIGGER ihr_operation_guard"
                ],
                ["controls"],
                False,
                None,
            ),
            (
                [
                    f"ALTER FUNCTION {TEST_SCHEMA}.ihr_audit_seal_next() RESET search_path"
                ],
                [],
                True,
                None,
            ),
            (
                [
                    f"ALTER FUNCTION {TEST_SCHEMA}.ihr_audit_seal_run(integer, smallint) "
                    "SET work_mem = '64kB'"
                ],
                [],
                True,
                None,
            ),
            (
                [
                    f"ALTER TABLE {TEST_SCHEMA}.ihr_audit_entry "
                    "DISABLE TRIGGER ihr_audit_stamp"
                ],
                [],
                True,
                None,
            ),
            ([], [], False, older_build),
            ([make_definer], ["controls"], False, None),
            (
                [make_definer, f"REVOKE EXECUTE ON FUNCTION {definer} FROM PUBLIC"],
                [],
                False,
                None,
            ),
        ):
            with self.subTest(statements=statements, older_build=bool(patch)):
                found_gated = {}
                with transaction.atomic():
                    with as_owner(), connection.cursor() as cursor:
                        for statement in statements:
                            cursor.execute(statement)
                    with (
                        mock.patch.object(db_roles, "app_problems", return_value=[]),
                        patch or mock.patch.object(db_roles, "_emit"),
                    ):
                        if patch:  # what migrate would report and repair
                            with connection.cursor() as cursor:
                                self.assertTrue(
                                    seal.sealing_problems(cursor, TEST_SCHEMA)
                                )
                        found = db_roles.check_connection(
                            connection, "app", gated=found_gated
                        )
                    transaction.set_rollback(True)
                self.assertEqual(sorted({r for r, _ in found}), refused, found)
                self.assertEqual(
                    {r for r, _ in found_gated.get(TEST_SCHEMA, [])},
                    {"controls"} if gated else set(),
                    found_gated,
                )
                self.assertEqual(fetch("SELECT current_user")[0], db_roles.APP)

    def test_required_mode_needs_large_objects_closed(self):
        """Second review of S8: the app's large objects are a write channel only the
        superuser can close (P1-T19b, as postgres); required mode checks it was."""
        revoke = (
            "REVOKE EXECUTE ON FUNCTION lo_create(oid), lo_creat(integer), "
            "lo_from_bytea(oid, bytea) FROM PUBLIC"
        )
        with as_owner(), transaction.atomic(), connection.cursor() as cursor:
            before = db_roles.large_object_problems(cursor)
            required = [
                f
                for f in db_roles.database_findings(cursor, "required")
                if f.text.startswith("large objects:")
            ]
            cursor.execute(revoke)
            after = db_roles.large_object_problems(cursor)
            transaction.set_rollback(True)
        self.assertEqual(len(before), 3, before)
        self.assertTrue(any("lo_create(oid)" in text for text in before), before)
        self.assertEqual(len(required), 3)
        self.assertTrue(all(f.kind == "problem" and f.control for f in required))
        self.assertEqual(after, [])

    def test_a_role_no_service_uses_yet_may_lack_login(self):
        """C6: a missing LOGIN on the watch or dump role (no service logs in as them yet) is
        a notice, never a problem that stops migrate; on the app role it is a problem."""
        with as_owner(), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("ALTER ROLE ihr_audit_watch NOLOGIN")
            cursor.execute("ALTER ROLE ihr_app NOLOGIN")
            found = db_roles.role_problems(cursor, "test")
            transaction.set_rollback(True)
        self.assertIn(
            ("notice", "role ihr_audit_watch must be able to log in (LOGIN)"), found
        )
        self.assertIn(("problem", "role ihr_app must be able to log in (LOGIN)"), found)

    def test_a_membership_or_the_databases_temporary_is_refused_at_run_time(self):
        """C8/C9/S6: what the per-process check catches beyond the ACLs."""
        name = sql.Identifier(connection.settings_dict["NAME"])
        for grant, revoke, reason in (
            (
                "GRANT pg_write_all_data TO ihr_app",
                "REVOKE pg_write_all_data FROM ihr_app",
                "membership",
            ),
            (
                sql.SQL("GRANT TEMPORARY ON DATABASE {} TO PUBLIC").format(name),
                sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC").format(name),
                "database_temporary",
            ),
            # CREATE in any one hospital's schema refuses the process, never only gates
            # that hospital: the owner's migrations and provisioner run there, so an object
            # the app made there is one the owner may resolve (a way to its rights). The
            # model never grants it, so no release leaves it stale (second review of C2)
            (
                f"GRANT CREATE ON SCHEMA {TEST_SCHEMA} TO ihr_app",
                f"REVOKE CREATE ON SCHEMA {TEST_SCHEMA} FROM ihr_app",
                "schema_create",
            ),
        ):
            with self.subTest(reason=reason):
                with as_owner(), connection.cursor() as cursor:
                    cursor.execute(grant)
                try:
                    with connection.cursor() as cursor:
                        found = [r for r, _ in db_roles.app_problems(cursor)]
                finally:
                    with as_owner(), connection.cursor() as cursor:
                        cursor.execute(revoke)
                self.assertIn(reason, found)

    def test_a_new_table_gets_its_class_after_migrate(self):
        """A new hospital table is data (dml) for the app after the post_migrate repair;
        an unlisted new public table gets nothing and is a finding."""
        with as_owner(), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(f"CREATE TABLE {TEST_SCHEMA}.ihr_p1t19a_new (id int)")
                cursor.execute("CREATE TABLE public.ihr_p1t19a_new (id int)")
                cursor.execute("GRANT SELECT ON public.ihr_p1t19a_new TO ihr_app")
            findings = db_roles.ensure_grants(connection, ["public", TEST_SCHEMA])
            texts = [str(f) for f in findings]
            self.assertIn(
                f"missing: {TEST_SCHEMA}.ihr_p1t19a_new: ihr_app lacks DELETE, INSERT, "
                "SELECT, UPDATE",
                texts,
            )
            self.assertIn("extra: public.ihr_p1t19a_new: ihr_app has SELECT", texts)
            self.assertTrue(
                any("public.ihr_p1t19a_new: not in public_tables" in t for t in texts)
            )
            transaction.set_rollback(True)


class RefusalTests(SimpleTestCase):
    databases = {"default"}

    def setUp(self):
        self.saved = dict(db_roles._STATE)
        self.lines = []
        self.addCleanup(self._restore)

    def _restore(self):
        connection_created.disconnect(dispatch_uid="ihr_db_role_check_test")
        db_roles._STATE.clear()
        db_roles._STATE.update(self.saved)
        db_roles._DEFERRED.clear()

    def _refusing(self, problems):
        db_roles._STATE.update(refused=None, checked=False)
        connection_created.connect(
            db_roles.on_connection_created,
            weak=False,
            dispatch_uid="ihr_db_role_check_test",
        )
        return (
            mock.patch.object(db_roles, "check_connection", return_value=problems),
            mock.patch.object(db_roles, "_emit", side_effect=self.lines.append),
        )

    def test_a_refused_role_stays_refused(self):
        """C1: the refused connection is closed, so the next query reconnects and is refused
        at once (without checking again); a second connection of the process too."""
        check, emit = self._refusing([("membership", "a member of ihr_owner")])
        wrapper = connections.create_connection("default")
        try:
            with check as checked, emit, override_settings(IHR_DB_ROLE="app"):
                with self.assertRaises(db_roles.RoleRefused):
                    wrapper.ensure_connection()
                self.assertIsNone(wrapper.connection)
                with (
                    self.assertRaises(db_roles.RoleRefused),
                    wrapper.cursor() as cursor,
                ):
                    cursor.execute("SELECT 1")
                other = connections.create_connection("default")
                try:
                    with self.assertRaises(db_roles.RoleRefused):
                        other.ensure_connection()
                finally:
                    other.close()
                self.assertEqual(checked.call_count, 1)
        finally:
            wrapper.close()
        self.assertEqual(
            self.lines, ["ihr-db-role: refused role=app reason=membership"]
        )

    def test_a_notice_is_not_a_difference(self):
        """P1-T19b: the watch and dump roles have no login until their services exist (a
        notice): an app process does not warn about them as drift that migrate repairs; a
        missing right still is counted."""
        notice = db_roles.Findings()
        notice.add("notice", "role ihr_audit_watch must be able to log in (LOGIN)")
        missing = db_roles.Findings()
        missing.add("missing", "public.x: ihr_app lacks SELECT", grantee=db_roles.APP)
        quiet = (
            mock.patch.object(db_roles, "app_problems", return_value=[]),
            mock.patch.object(db_roles, "effective_public_problems", return_value=[]),
            mock.patch("ihr_audit.seal.sealing_problems_by_schema", return_value={}),
        )
        with quiet[0], quiet[1], quiet[2]:
            with (
                mock.patch.object(db_roles, "report", return_value=notice),
                self.assertNoLogs(db_roles.logger, "WARNING"),
            ):
                self.assertEqual(db_roles.check_connection(connection, "app"), [])
            with (
                mock.patch.object(db_roles, "report", return_value=missing),
                self.assertLogs(db_roles.logger, "WARNING") as logs,
            ):
                self.assertEqual(db_roles.check_connection(connection, "app"), [])
        self.assertIn("1 difference(s) from the model", logs.output[0])

    def test_two_requests_both_fail(self):
        """C1: the same gunicorn worker's next request is refused too."""
        check, emit = self._refusing([("superuser", "a superuser")])
        client = Client(HTTP_HOST="testserver", raise_request_exception=False)
        connection.close()
        try:
            with check, emit, override_settings(IHR_DB_ROLE="app"):
                first = client.get("/api/v1/users/getcurrentuser/")
                second = client.get("/api/v1/users/getcurrentuser/")
        finally:
            self._restore()
            connection.close()
        self.assertEqual((first.status_code, second.status_code), (500, 500))
        self.assertEqual(self.lines, ["ihr-db-role: refused role=app reason=superuser"])

    def test_a_check_that_cannot_run_refuses(self):
        db_roles._STATE.update(refused=None, checked=False)
        wrapper = connections.create_connection("default")
        try:
            with (
                mock.patch.object(db_roles, "check_connection", side_effect=KeyError),
                mock.patch.object(db_roles, "_emit", side_effect=self.lines.append),
                override_settings(IHR_DB_ROLE="app"),
            ):
                connection_created.connect(
                    db_roles.on_connection_created,
                    weak=False,
                    dispatch_uid="ihr_db_role_check_test",
                )
                with self.assertRaises(db_roles.RoleRefused):
                    wrapper.ensure_connection()
        finally:
            wrapper.close()
        self.assertEqual(
            self.lines, ["ihr-db-role: refused role=app reason=check_failed"]
        )

    def test_an_owner_must_be_the_owner_and_an_owner_process(self):
        """S6: the declaration cannot switch the check off: an owner-declared process must
        connect as the owner, and only owner-run commands may."""
        with connection.cursor() as cursor:
            found = [
                r for r, _ in db_roles.owner_problems(cursor, ["manage.py", "migrate"])
            ]
        self.assertIn("wrong_role", found)
        self.assertEqual(
            db_roles.check_connection(connection, "nobody"), [("undeclared", mock.ANY)]
        )
        for argv, allowed in (
            (["manage.py", "migrate"], True),
            (["manage.py", "ihr_provision_worker", "--role", "onboarding"], True),
            (["manage.py", "ihr_provision_worker", "--role", "staff"], False),
            (["manage.py", "ihr_provision_worker", "--role=staff"], False),
            # S5: the command's own parser (an abbreviation is the same option)
            (["manage.py", "ihr_provision_worker", "--rol", "staff"], False),
            (["manage.py", "ihr_provision_worker", "--rol", "onboarding"], True),
            (["manage.py", "ihr_provision_worker"], False),
            # S5: the pre-start check judges the process it guards, not itself
            (["manage.py", "ihr_db_role_check"], False),
            (["manage.py", "ihr_db_role_check", "--for", "celery-worker"], False),
            (["manage.py", "ihr_db_role_check", "--for", "celery-beat"], False),
            (["manage.py", "ihr_db_role_check", "--for", "migrate"], True),
            (["manage.py", "ihr_db_role_check", "--for=provisioner", "--report"], True),
            (["/usr/local/bin/gunicorn", "config.wsgi"], False),
            (["/usr/local/bin/celery", "-A", "config.celery_app", "worker"], False),
            ([], False),
        ):
            with self.subTest(argv=argv):
                self.assertEqual(db_roles.owner_process_allowed(argv), allowed)
        for process in db_roles.PROCESSES:
            with self.subTest(process=process):
                self.assertEqual(
                    db_roles.owner_process_allowed(["manage.py", "x"], process),
                    process in ("migrate", "admin", "provisioner"),
                )

    def test_a_later_connection_is_checked_again(self):
        """S2: a role can ALTER ROLE itself after the first check (a stored setting, a
        membership granted later): every new connection of an app process reads its role
        again, and a refusal then is as sticky as the first one."""
        check, emit = self._refusing([])
        db_roles._STATE.update(checked=True, declared="app")
        wrapper = connections.create_connection("default")
        try:
            with (
                check,
                emit,
                override_settings(IHR_DB_ROLE="app"),
                mock.patch.object(
                    db_roles,
                    "reconnect_problems",
                    return_value=[("role_settings", "stored settings")],
                ) as again,
            ):
                with self.assertRaises(db_roles.RoleRefused):
                    wrapper.ensure_connection()
                self.assertEqual(again.call_count, 1)
                other = connections.create_connection("default")
                try:
                    with self.assertRaises(db_roles.RoleRefused):
                        other.ensure_connection()
                finally:
                    other.close()
        finally:
            wrapper.close()
        self.assertEqual(
            self.lines, ["ihr-db-role: refused role=app reason=role_settings"]
        )

    def test_a_hospital_with_drift_is_gated_not_the_process(self):
        """C2: the first connection's check found rights beyond the model in one hospital's
        schema: the process starts, writes one line for it, and does not serve it."""

        def found(connection, declared_role=None, gated=None, **kwargs):
            gated[TEST_SCHEMA] = [
                (
                    "extra_privileges",
                    f"{TEST_SCHEMA}.emr_patient: ihr_app has TRUNCATE",
                ),
                ("controls", f"{TEST_SCHEMA}.ihr_audit_seal_next: must be ..."),
            ]
            return []

        db_roles._STATE.update(refused=None, checked=False)
        connection_created.connect(
            db_roles.on_connection_created,
            weak=False,
            dispatch_uid="ihr_db_role_check_test",
        )
        wrapper = connections.create_connection("default")
        try:
            with (
                mock.patch.object(db_roles, "check_connection", side_effect=found),
                mock.patch.object(db_roles, "_emit", side_effect=self.lines.append),
                override_settings(IHR_DB_ROLE="app"),
            ):
                wrapper.ensure_connection()
        finally:
            wrapper.close()
        self.assertEqual(db_roles.gated_schemas(), {TEST_SCHEMA})
        self.assertEqual(
            self.lines,
            [
                f"ihr-db-role: gated schema={TEST_SCHEMA} reason=controls",
                f"ihr-db-role: gated schema={TEST_SCHEMA} reason=extra_privileges",
            ],
        )

    def test_off_mode_refuses_a_moved_database(self):
        """S10: with IHR_DB_ROLES=off a process that connects as a split role is refused."""
        db_roles._STATE.update(refused=None, off_checked=False)
        connection_created.connect(
            db_roles.on_connection_created_off,
            weak=False,
            dispatch_uid="ihr_db_role_check_test",
        )
        wrapper = connections.create_connection("default")
        try:
            with (
                mock.patch.object(
                    db_roles,
                    "off_problems",
                    return_value=[("roles_off", "connected as ihr_app")],
                ),
                mock.patch.object(db_roles, "_emit", side_effect=self.lines.append),
                override_settings(IHR_DB_ROLE=""),
                self.assertRaises(db_roles.RoleRefused),
            ):
                wrapper.ensure_connection()
        finally:
            wrapper.close()
        self.assertEqual(
            self.lines, ["ihr-db-role: refused role=none reason=roles_off"]
        )

    def test_post_migrate_raises_only_at_its_end(self):
        """C6: a required-mode refusal found by the grants repair waits for the last
        post_migrate receiver, so the other apps' receivers still run first."""
        from django.apps import apps

        db_roles._DEFERRED.clear()
        with override_settings(IHR_DB_ROLES="required"):
            db_roles.repair_after_migrate(connection, "public")  # no ihr_owner here
        with self.assertRaisesRegex(db_roles.RolesError, "ihr_owner"):
            db_roles.raise_deferred()
        self.assertEqual(db_roles._DEFERRED, [])
        db_roles.raise_deferred()  # nothing left
        last = [c for c in apps.get_app_configs() if c.models_module is not None][-1]
        self.assertTrue(
            any(
                key == ("ihr_db_roles_deferred", id(last))
                for key, *_rest in post_migrate.receivers
            )
        )

    def test_a_new_migrate_starts_with_nothing_deferred(self):
        """Second review of C6: a refusal left by a post_migrate emission that stopped before
        its last receiver is never raised by a later, unrelated migrate (pre_migrate clears
        it)."""
        from django.db.models.signals import pre_migrate

        self.assertTrue(
            any(key[0] == "ihr_db_roles_clear" for key, *_rest in pre_migrate.receivers)
        )
        db_roles._DEFERRED.append("database roles: left from an earlier run")
        db_roles.clear_deferred(sender=None)
        db_roles.raise_deferred()  # nothing to raise

    def test_settings_rules(self):
        # P1-T19b: a server runs the split roles required; off (unmanaged, unchecked) and the
        # tests' mode are refused there
        self.assertTrue(secure.db_role_problems({"IHR_DB_ROLES": "off"}))
        self.assertTrue(secure.db_role_problems({}))
        # S10: a declared role is always checked
        self.assertTrue(secure.db_role_problems({"IHR_DB_ROLE": "app"}))
        self.assertTrue(
            secure.db_role_problems({"IHR_DB_ROLES": "off", "IHR_DB_ROLE": "owner"})
        )
        self.assertTrue(secure.db_role_problems({"IHR_DB_ROLES": "test"}))
        self.assertTrue(secure.db_role_problems({"IHR_DB_ROLES": "required"}))
        self.assertEqual(
            secure.db_role_problems({"IHR_DB_ROLES": "required", "IHR_DB_ROLE": "app"}),
            [],
        )
        for purpose in ("backend", "staff"):
            with self.subTest(purpose=purpose):
                self.assertTrue(
                    secure.db_role_problems(
                        {
                            "IHR_DB_ROLES": "required",
                            "IHR_DB_ROLE": "owner",
                            "IHR_CLIENT_KEY_PURPOSE": purpose,
                        }
                    )
                )

    def test_the_command_names_the_process_it_guards(self):
        """S5: --for takes only the runtime processes and the owner's three."""
        from django.core.management.base import CommandError

        with (
            override_settings(IHR_DB_ROLES="off"),
            self.assertRaisesRegex(CommandError, "invalid choice"),
        ):
            call_command("ihr_db_role_check", "--for", "gunicorn-as-owner")

    def test_the_command_does_nothing_while_off(self):
        from io import StringIO

        out = StringIO()
        with override_settings(IHR_DB_ROLES="off"):
            call_command("ihr_db_role_check", stdout=out)
        self.assertIn("nothing to check", out.getvalue())

    def test_the_refusal_line_is_the_alerts(self):
        """deploy/logs/alerts/db-roles.yml matches exactly this line, one of these reasons."""
        import re

        pattern = re.compile(
            r"^ihr-db-role: refused role=(?P<role>app|owner|none) "
            r"reason=(?P<reason>[a-z_]{3,24})$"
        )
        for reason in db_roles.REASONS:
            self.assertRegex(db_roles.refusal_line("app", reason), pattern)
        self.assertNotIn("\n", db_roles.refusal_line("app", "superuser"))
        self.assertTrue(os.environ.get("IHR_DB_ROLE", "") in ("", "app", "owner"))


class GatedHospitalTests(SimpleTestCase):
    """C2: a hospital this process gated is served by none of its paths: the web gate, the
    worker's tasks and lists (release.ready_q), and HospitalNotServed names it every minute."""

    databases = {"default"}

    def setUp(self):
        saved = dict(db_roles._STATE["gated"])
        self.addCleanup(db_roles._STATE.__setitem__, "gated", saved)
        db_roles._STATE["gated"] = {TEST_SCHEMA: ("extra_privileges",)}

    def test_a_gated_hospital_is_not_served(self):
        from ihr_platform import celery_gate, tasks

        hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
        self.assertFalse(release.schema_ready(hospital))
        self.assertFalse(
            Hospital.objects.filter(pk=hospital.pk).filter(release.ready_q()).exists()
        )
        self.assertFalse(celery_gate._served(TEST_SCHEMA))
        self.assertIn(hospital.slug, tasks.unserved_slugs())
        response = Client(HTTP_HOST="testserver").get("/api/v1/users/getcurrentuser/")
        self.assertEqual(
            (response.status_code, response.json()["code"]), (503, "ihr_upgrading")
        )
        db_roles._STATE["gated"] = {}
        self.assertTrue(release.schema_ready(hospital))

    def test_the_worker_names_a_gated_schema_every_minute(self):
        """Second review of C2: the per-minute report repeats the gated line, so the alert
        DatabaseSchemaGated lasts while the gating does, for an archived hospital's schema
        too (HospitalNotServed names live hospitals only)."""
        from ihr_platform import tasks

        db_roles._STATE["gated"] = {
            TEST_SCHEMA: ("extra_privileges",),
            "h_archived01": ("controls", "extra_privileges"),
        }
        lines = []
        connection.set_schema_to_public()  # a platform job, sent in public (beat)
        self.addCleanup(connection.set_schema, TEST_SCHEMA)
        with mock.patch.object(tasks, "_emit", side_effect=lines.append):
            tasks.report_unserved()
        gated = [line for line in lines if line.startswith("ihr-db-role: gated")]
        self.assertEqual(
            gated,
            [
                "ihr-db-role: gated schema=h_archived01 reason=controls",
                "ihr-db-role: gated schema=h_archived01 reason=extra_privileges",
                f"ihr-db-role: gated schema={TEST_SCHEMA} reason=extra_privileges",
            ],
        )


class ProvisionerInputTests(TestCase):
    """S1, the provisioner's own line: it acts only for the operation the owner recorded as
    the one that built the hospital, never on what an operation row says."""

    def setUp(self):
        testing.owner_for_test(self)  # the provisioner runs as the owner
        self.hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)

    def _operation(self, status):
        return TenantOperation.objects.create(
            kind=TenantOperation.Kind.PROVISION,
            slug="p1t19a-copied",
            hospital=self.hospital,
            status=status,
            requested_by="forger",
            input={"admin_email": "x@example.test"},
            result={
                "hospital_uuid": str(self.hospital.uuid),
                "schema_name": self.hospital.schema_name,
                "admin_user_id": 1,
            },
        )

    def test_only_the_building_operation_reaches_its_hospital(self):
        message = "did not build the hospital"
        with self.assertRaisesRegex(runner.ProvisioningError, message):
            runner.resend_setup(self._operation("succeeded"))
        with self.assertRaisesRegex(runner.ProvisioningError, message):
            runner.rollback(self._operation("failed"), "forger")
        with self.assertRaisesRegex(runner.ProvisioningError, message):
            runner.run(self._operation("pending"))
        self.assertFalse(
            TenantOperation.objects.filter(kind=TenantOperation.Kind.ROLLBACK)
            .filter(slug__startswith="p1t19a-")
            .exists()
        )


class SealingTests(SimpleTestCase):
    """S6 and C5: sealing never waits on a lock anyone may hold, and every migrate brings
    the sealing functions and the stamp trigger back to this build's."""

    databases = {"default"}

    def setUp(self):
        connection.set_schema(TEST_SCHEMA)

    def test_a_held_sealing_lock_never_makes_the_sealer_wait(self):
        from ihr_audit.seal import seal_once

        holder = connections.create_connection("default")
        try:
            with holder.cursor() as cursor:  # the app role may take this key too
                cursor.execute(
                    "SELECT pg_advisory_lock(hashtext(%s))",
                    [f"ihr_audit_seal:{TEST_SCHEMA}"],
                )
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '3s'")
                self.assertEqual(seal_once(TEST_SCHEMA), 0)  # at once, not after 3 s
        finally:
            holder.close()

    def test_migration_0004_freezes_its_sql(self):
        import importlib

        module = importlib.import_module(
            "ihr_audit.migrations.0004_owner_writes_the_chain"
        )
        imports = [
            line
            for line in inspect.getsource(module).splitlines()
            if line.lstrip().startswith(("import ", "from "))
        ]
        self.assertFalse([line for line in imports if "ihr_" in line], imports)
        self.assertNotIn("functions_sql", inspect.getsource(module.make_functions))
        self.assertIn("pg_try_advisory_xact_lock", module.FUNCTIONS)

    def test_a_broken_sealing_function_is_put_back_by_migrate(self):
        from ihr_audit import seal
        from ihr_audit.install import coverage

        with as_owner(), transaction.atomic():
            with connection.cursor() as cursor:
                self.assertEqual(seal.sealing_problems(cursor, TEST_SCHEMA), [])
                cursor.execute(
                    f"ALTER FUNCTION {TEST_SCHEMA}.ihr_audit_seal_next() SECURITY INVOKER"
                )
                cursor.execute(
                    f"DROP FUNCTION {TEST_SCHEMA}.ihr_audit_seal_run CASCADE"
                )
                cursor.execute(
                    f"ALTER TABLE {TEST_SCHEMA}.ihr_audit_entry "
                    "DISABLE TRIGGER ihr_audit_stamp"
                )
                broken = seal.sealing_problems(cursor, TEST_SCHEMA)
            self.assertEqual(len(broken), 2, broken)
            self.assertTrue(
                any("sealing functions" in p for p in coverage(connection, TEST_SCHEMA))
            )
            self.assertTrue(seal.ensure_sealing(connection, TEST_SCHEMA))
            db_roles.ensure_grants(connection, [TEST_SCHEMA])  # EXECUTE back to the app
            with connection.cursor() as cursor:
                self.assertEqual(seal.sealing_problems(cursor, TEST_SCHEMA), [])
                cursor.execute(
                    "SELECT p.prosecdef FROM pg_proc p JOIN pg_namespace n "
                    "ON n.oid = p.pronamespace WHERE n.nspname = %s "
                    "AND p.proname = 'ihr_audit_seal_next'",
                    [TEST_SCHEMA],
                )
                self.assertEqual(cursor.fetchone(), (True,))
            self.assertFalse(
                seal.ensure_sealing(connection, TEST_SCHEMA)
            )  # current now
            # A newer build's sealing SQL reaches every schema at its next migrate
            with mock.patch.object(seal, "SEALING_VERSION", "0" * 64):
                with connection.cursor() as cursor:
                    self.assertTrue(seal.sealing_problems(cursor, TEST_SCHEMA))
                self.assertTrue(seal.ensure_sealing(connection, TEST_SCHEMA))
                with connection.cursor() as cursor:
                    self.assertEqual(seal.sealing_problems(cursor, TEST_SCHEMA), [])
            transaction.set_rollback(True)
