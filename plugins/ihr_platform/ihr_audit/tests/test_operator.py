"""Operator access entries in each hospital's trail (P1-T27b, Q-K136; docs/reviews/p1-t27/attack.md
"Orchestrator decision" -> "Per-hospital entries").

``ihr_operator_open`` (run by staging.sh's op_scope_open in the admin one-off) writes one ``operator``
entry per hospital in the session's scope, on its own autocommit connection, and seals it. A
one-off started with ``IHR_OPERATOR_SESSION`` names its database sessions ``op:<session>:<process>``
and its writes ``operator:<name>``. Old code reading the new kind (viewer, export, verifier, watch)
must not break. The trail is append-only: entries written here stay (each test uses its own session).
"""

import contextlib
import csv
import io
import json
import os
import secrets
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from care.emr.models import Patient
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command, load_command_class
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, transaction
from django.test import SimpleTestCase
from ihr_platform import db_limits, release
from ihr_platform.models import Hospital, RetiredAddress
from ihr_platform.settings.tenancy import apply_tenancy, operator_appname
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_platform.testing import as_owner, owner_for_test
from model_bakery import baker
from psycopg import sql

from ihr_audit import (
    anchor,
    context,
    events,
    incremental,
    operator,
    seal,
    verify_export,
    watch,
)

from .test_anchor import FakeStore
from .test_audit_trail import AuditCase, entries_since, mark
from .test_viewer import ViewerCase, today

SLUG = "testhosp"  # the test hospital (TEST_SCHEMA)
REASON = "TKT-42 bed board stuck"
NAME = "asha.ops"


def new_session():
    while True:  # 12 hex characters, never all digits (as staging.sh's op_new_session)
        value = secrets.token_hex(6)
        if not value.isdigit():
            return value


def open_cmd(session, scope=SLUG, reason=REASON, tier="data", name=NAME):
    out = io.StringIO()
    call_command(
        "ihr_operator_open",
        "--session",
        session,
        "--operator",
        name,
        "--scope",
        scope,
        "--reason",
        reason,
        "--tier",
        tier,
        stdout=out,
    )
    return out.getvalue()


def operator_entries(schema, session):
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT e.id, e.kind, e.actor, e.context, e.tbl, e.op, e.row_pk, e.patients, "
                "e.data, EXISTS (SELECT 1 FROM {seal} s WHERE e.txid >= s.lower_txid "
                "AND e.txid < s.upper_txid) AS sealed FROM {entry} e "
                "WHERE e.kind = 'operator' AND e.context->>'session' = %s ORDER BY e.id"
            ).format(
                seal=sql.Identifier(schema, "ihr_audit_seal"),
                entry=sql.Identifier(schema, "ihr_audit_entry"),
            ),
            [session],
        )
        names = [c.name for c in cursor.description]
        rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    for row in rows:
        for column in ("context", "data"):
            if isinstance(row[column], str):
                row[column] = json.loads(row[column])
    return rows


def parse(line):
    """``opened=a,b skipped=c:why,...`` -> ({a, b}, {c: why})."""
    opened, skipped = line.strip().split(" ")
    opened = opened.removeprefix("opened=")
    skipped = skipped.removeprefix("skipped=")
    return (
        set() if opened == "-" else set(opened.split(",")),
        {}
        if skipped == "-"
        else dict(item.split(":", 1) for item in skipped.split(",")),
    )


class Fleet:
    """Hospitals beside the test hospital, made and dropped as the owner: one whose trail takes
    the new kind (its tables copied from the test schema, with the sealing functions), one whose
    kind check is the old one, one held for a restore, one with no schema, one archived."""

    def __init__(self, testcase):
        self.testcase = testcase
        self.made = []
        self.tag = "op" + secrets.token_hex(
            3
        )  # slugs are never reused: one set per test

    def _hospital(self, slug, status=Hospital.Status.ACTIVE):
        connection.set_schema_to_public()
        try:
            slug = f"{self.tag}-{slug}"
            hospital = Hospital(slug=slug, name=slug.title(), status=status)
            hospital.auto_create_schema = False
            hospital.save()
        finally:
            connection.set_schema(TEST_SCHEMA)
        self.made.append(hospital)
        return hospital

    def _trail(self, hospital, kinds=None):
        schema = hospital.schema_name
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            for table in ("ihr_audit_entry", "ihr_audit_seal"):
                cursor.execute(
                    sql.SQL("CREATE TABLE {} (LIKE {} INCLUDING ALL)").format(
                        sql.Identifier(schema, table),
                        sql.Identifier(TEST_SCHEMA, table),
                    )
                )
            if kinds is not None:  # an older build's check
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {t} DROP CONSTRAINT ihr_audit_entry_kind_check; "
                        "ALTER TABLE {t} ADD CONSTRAINT ihr_audit_entry_kind_check "
                        "CHECK (kind IN ({k}))"
                    ).format(
                        t=sql.Identifier(schema, "ihr_audit_entry"),
                        k=sql.SQL(", ").join(sql.Literal(k) for k in kinds),
                    )
                )
            ident = sql.Identifier(schema)
            cursor.execute(seal.functions_sql(schema))
            cursor.execute(sql.SQL(seal.STAMP_FUNCTION).format(schema=ident))
            cursor.execute(sql.SQL(seal.STAMP_TRIGGER).format(schema=ident))

    def build(self):
        self.new = self._hospital("new")
        self._trail(self.new)
        self.old = self._hospital("old")
        self._trail(self.old, kinds=("write", "truncate", "request", "restore"))
        self.held = self._hospital("held")
        self._trail(self.held)
        release.restore_hold(self.held, uuid.uuid4())
        self.absent = self._hospital("absent")
        self.archived = self._hospital("gone", status=Hospital.Status.ARCHIVED)
        self._trail(self.archived)
        self.testcase.addCleanup(self.drop)
        return self

    def drop(self):
        with as_owner():
            connection.set_schema_to_public()
            try:
                for hospital in self.made:
                    hospital.delete(force_drop=True)
                RetiredAddress.objects.filter(value__startswith=self.tag).delete()
            finally:
                connection.set_schema(TEST_SCHEMA)


class OneMixin:
    def one(self, rows):
        """The single row (an assertion, so a missing entry FAILs rather than ERRORs)."""
        self.assertEqual(len(rows), 1, rows)
        return rows[0]


class ValidationTests(SimpleTestCase):
    """The same rules as staging.sh (Q-K134, Q-K135): refused before any database access."""

    def test_operator_names(self):
        for good in ("asha.ops", "ravi-k", "ab"):
            operator.check_operator(good)
        for bad in ("", "a", "Asha", "1asha", "asha_ops", "a" * 33, "asha ops"):
            with self.assertRaisesRegex(operator.OperatorError, "bad-operator"):
                operator.check_operator(bad)
        for shared in ("ihr", "root", "admin", "postgres"):
            with self.assertRaisesRegex(operator.OperatorError, "shared-operator"):
                operator.check_operator(shared)

    def test_reasons(self):
        for good in (REASON, "job:release", "abc", "x" * 120, "call 12345 fixed"):
            operator.check_reason(good)
        for bad in (
            "ab",
            "x" * 121,
            "patient 123456 chart",
            "name: Asha;",
            "tab\there",
            "é ok",
        ):
            with self.assertRaisesRegex(operator.OperatorError, "bad-reason"):
                operator.check_reason(bad)

    def test_sessions_scopes_and_tiers(self):
        operator.check_session("0123456789ab")
        for bad in ("", "0123456789AB", "0123456789a", "0123456789abc", "g123456789ab"):
            with self.assertRaisesRegex(operator.OperatorError, "bad-session"):
                operator.check_session(bad)
        operator.check_scope("alpha")
        operator.check_scope("all")
        for bad in ("", "Alpha", "ab", "none", "a" * 32, "alpha,beta"):
            with self.assertRaisesRegex(operator.OperatorError, "bad-scope"):
                operator.check_scope(bad)
        for tier in ("data", "job"):
            operator.check_tier(tier)
        for bad in ("", "none", "DATA"):
            with self.assertRaisesRegex(operator.OperatorError, "bad-tier"):
                operator.check_tier(bad)

    def test_the_command_refuses_bad_values_with_exit_1_and_no_answer(self):
        session = new_session()
        cases = {
            "bad-operator": {"name": "Asha"},
            "shared-operator": {"name": "root"},
            "bad-reason": {"reason": "patient 9876543210"},
            "bad-scope": {"scope": "none"},
            "bad-tier": {"tier": "none"},
        }
        for code, change in cases.items():
            with self.subTest(code), self.assertRaisesRegex(CommandError, code):
                open_cmd(session, **change)
        with self.assertRaisesRegex(CommandError, "bad-session"):
            open_cmd("not-a-session")
        err = io.StringIO()
        command = type(load_command_class("ihr_audit", "ihr_operator_open"))(stderr=err)
        with self.assertRaises(SystemExit) as stopped:
            command.run_from_argv(
                [
                    "manage.py",
                    "ihr_operator_open",
                    "--session",
                    session,
                    "--operator",
                    "root",
                    "--scope",
                    SLUG,
                    "--reason",
                    REASON,
                    "--tier",
                    "data",
                ]
            )
        self.assertEqual(stopped.exception.code, 1)
        self.assertIn("shared-operator", err.getvalue())

    def test_application_name_under_a_session(self):
        self.assertEqual(operator_appname("manage:shell", {}), "manage:shell")
        self.assertEqual(
            operator_appname("manage:shell", {"IHR_OPERATOR_SESSION": "0123456789ab"}),
            "op:0123456789ab:manage:shell",
        )
        self.assertEqual(
            len(operator_appname("x" * 63, {"IHR_OPERATOR_SESSION": "0123456789ab"})),
            63,
        )
        with self.assertRaisesRegex(ImproperlyConfigured, "IHR_OPERATOR_SESSION"):
            operator_appname("manage:shell", {"IHR_OPERATOR_SESSION": "nope; drop"})

    def test_apply_tenancy_names_the_session_and_leaves_the_limits_alone(self):
        def settings():
            return {
                "INSTALLED_APPS": [],
                "DATABASES": {"default": {}},
                "MIDDLEWARE": [],
                "CACHES": {},
                "REST_FRAMEWORK": {},
                "SPECTACULAR_SETTINGS": {},
                "DEBUG": True,
                "IHR_DB_LIMITS": "worker",
            }

        def get(name, default=""):
            return {"IHR_DB_ROLES": "test", "IHR_DB_ROLE": ""}.get(name, default)

        plain = settings()
        with patch.dict("os.environ", {"IHR_PROCESS_NAME": "manage:shell"}):
            apply_tenancy(plain)
            limits = db_limits.chosen(get, {})
        session = {
            "IHR_PROCESS_NAME": "manage:shell",
            "IHR_OPERATOR_SESSION": "0123456789ab",
        }
        named = settings()
        with patch.dict("os.environ", session):
            apply_tenancy(named)
            self.assertEqual(db_limits.chosen(get, {}), limits)
        self.assertEqual(
            plain["DATABASES"]["default"]["OPTIONS"]["application_name"], "manage:shell"
        )
        self.assertEqual(
            named["DATABASES"]["default"]["OPTIONS"]["application_name"],
            "op:0123456789ab:manage:shell",
        )
        self.assertEqual(
            named["DATABASES"]["default"]["OPTIONS"]["options"],
            plain["DATABASES"]["default"]["OPTIONS"]["options"],
        )


class AttributionTests(OneMixin, AuditCase):
    """IHR_OPERATOR_SESSION in the environment: every write of the one-off names the operator."""

    def env(self, **change):
        values = {
            "IHR_OPERATOR_SESSION": new_session(),
            "IHR_OPERATOR": NAME,
            "IHR_OPERATOR_SCOPE": SLUG,
            "IHR_OPERATOR_REASON": REASON,
            "IHR_OPERATOR_TIER": "data",
        }
        values.update(change)
        return values

    @contextlib.contextmanager
    def installed(self, env):
        saved = context._current.get()
        try:
            yield operator.install(env)
        finally:
            context._current.set(saved)
            context._apply(connection, saved)

    def test_no_session_installs_nothing(self):
        before = context._current.get()
        with self.installed({"IHR_OPERATOR": NAME}) as values:
            self.assertIsNone(values)
            self.assertEqual(context._current.get(), before)

    def test_a_write_names_the_operator_and_the_session(self):
        """Review A5: a one-off's write may land in any hospital's trail (no scope guard,
        Q-K136), so its context names no hospital and carries no free-text reason: the
        hospital's own ``operator`` entry has those, joined by the session."""
        env = self.env()
        since = mark()
        with self.installed(env):
            baker.make(Patient)
        row = self.one(entries_since(since, kind="write", tbl="emr_patient"))
        self.assertEqual(row["actor"], f"operator:{NAME}")
        session = env["IHR_OPERATOR_SESSION"]
        self.assertEqual(
            row["context"],
            {
                "via": "operator",
                "session": session,
                "tier": "data",
                "scope": "one",
                "reason": f"operator access (one hospital), session {session}",
            },
        )
        text = json.dumps(row["context"])
        self.assertNotIn(SLUG, text)
        self.assertNotIn("TKT-42", text)

    def test_a_job_of_no_hospital_is_not_called_all_hospitals(self):
        env = self.env(
            IHR_OPERATOR_SCOPE="none",
            IHR_OPERATOR_TIER="job",
            IHR_OPERATOR_REASON="job:fleet",
        )
        since = mark()
        with self.installed(env):
            baker.make(Patient)
        row = self.one(entries_since(since, kind="write", tbl="emr_patient"))
        session = env["IHR_OPERATOR_SESSION"]
        self.assertEqual(row["context"]["scope"], "none")
        self.assertEqual(row["context"]["reason"], f"operator job, session {session}")

    def test_a_new_connection_gets_it_too_and_all_keeps_the_reason_out(self):
        env = self.env(IHR_OPERATOR_SCOPE="all", IHR_OPERATOR_TIER="job")
        since = mark()
        with self.installed(env):
            connection.close()  # Celery and long commands reconnect
            baker.make(Patient)
        row = self.one(entries_since(since, kind="write", tbl="emr_patient"))
        self.assertEqual(row["actor"], f"operator:{NAME}")
        self.assertEqual(
            row["context"]["reason"],
            f"operator access (all hospitals), session {env['IHR_OPERATOR_SESSION']}",
        )

    def test_bad_values_stop_the_process_before_the_database(self):
        for change in (
            {"IHR_OPERATOR_SESSION": "0123"},
            {"IHR_OPERATOR": "root"},
            {"IHR_OPERATOR": ""},
            {"IHR_OPERATOR_REASON": "patient 1234567"},
            {"IHR_OPERATOR_SCOPE": "Alpha"},
            {"IHR_OPERATOR_TIER": "none"},
        ):
            with (
                self.subTest(change),
                self.assertRaises(ImproperlyConfigured),
                self.installed(self.env(**change)),
            ):
                pass


class OpenTests(OneMixin, AuditCase):
    def setUp(self):
        owner_for_test(self)  # the admin one-off runs as the owner
        super().setUp()
        self.session = new_session()

    def test_one_entry_in_the_named_hospital_only(self):
        fleet = Fleet(self).build()
        line = open_cmd(self.session)
        self.assertEqual(line, f"opened={SLUG} skipped=-\n")
        row = self.one(operator_entries(TEST_SCHEMA, self.session))
        self.assertEqual(row["actor"], f"operator:{NAME}")
        self.assertEqual(
            row["context"],
            {
                "via": "operator",
                "session": self.session,
                "tier": "data",
                "scope": SLUG,
                "reason": REASON,
            },
        )
        self.assertEqual(row["data"], {"event": "opened"})
        self.assertEqual(
            (row["tbl"], row["op"], row["row_pk"], row["patients"]), ("", "", None, [])
        )
        self.assertTrue(row["sealed"], "sealed right away")
        self.assertEqual(operator_entries(fleet.new.schema_name, self.session), [])
        problems, _ = seal.verify(TEST_SCHEMA)
        self.assertEqual(problems, [])

    def test_all_writes_one_entry_per_live_hospital_with_the_generic_reason(self):
        fleet = Fleet(self).build()
        opened, skipped = parse(
            open_cmd(self.session, scope="all", tier="job", reason="job:release")
        )
        self.assertLessEqual({SLUG, fleet.new.slug}, opened)
        self.assertEqual(
            {k: v for k, v in skipped.items() if k.startswith(fleet.tag)},
            {
                fleet.old.slug: "old-schema",
                fleet.held.slug: "held",
                fleet.absent.slug: "absent",
                fleet.archived.slug: "archived",  # named, never silent (review A3)
            },
        )
        generic = f"operator access (all hospitals), session {self.session}"
        for schema in (TEST_SCHEMA, fleet.new.schema_name):
            row = self.one(operator_entries(schema, self.session))
            self.assertEqual(row["context"]["reason"], generic)
            self.assertEqual(row["context"]["scope"], "all")
            self.assertEqual(row["context"]["tier"], "job")
            self.assertTrue(row["sealed"], schema)
        for hospital in (fleet.held, fleet.archived):
            self.assertEqual(operator_entries(hospital.schema_name, self.session), [])
        problems, _ = seal.verify(fleet.new.schema_name)
        self.assertEqual(problems, [])

    def test_the_hospital_query_reads_only_the_columns_every_build_has(self):
        """Review A2/O7: a release's new build runs this command on the old schema, so a
        column a later migration adds must never be selected."""
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            operator._hospitals("all")
            operator._hospitals(SLUG)
        selects = [q["sql"] for q in captured if q["sql"].startswith("SELECT")]
        self.assertTrue(selects)
        for text in selects:
            for column in (
                "keycloak_org_id",
                "activated_at",
                "provisioning_operation",
                "created_on",
                '"name"',
            ):
                self.assertNotIn(column, text)

    def test_the_command_runs_no_system_checks(self):
        command = load_command_class("ihr_audit", "ihr_operator_open")
        self.assertEqual(command.requires_system_checks, [])

    def test_all_and_none_are_reserved_slugs(self):
        from ihr_platform.models import RESERVED_SLUGS

        self.assertLessEqual({operator.ALL, operator.NONE}, RESERVED_SLUGS)

    def test_a_hospital_slugged_all_or_none_refuses_the_session(self):
        from ihr_platform.models import generate_schema_name

        connection.set_schema_to_public()
        try:
            Hospital.objects.bulk_create(
                [Hospital(slug="none", name="None", schema_name=generate_schema_name())]
            )
        finally:
            connection.set_schema(TEST_SCHEMA)
        self.addCleanup(lambda: Hospital.objects.filter(slug="none").delete())
        with self.assertRaisesRegex(CommandError, "reserved-slug"):
            open_cmd(self.session, scope="all", tier="job", reason="job:release")

    def test_a_held_hospital_is_skipped_by_name(self):
        fleet = Fleet(self).build()
        held = fleet.held.slug
        self.assertEqual(
            open_cmd(self.session, scope=held), f"opened=- skipped={held}:held\n"
        )
        self.assertEqual(operator_entries(fleet.held.schema_name, self.session), [])

    def test_absent_old_and_unknown_hospitals_are_skipped(self):
        fleet = Fleet(self).build()
        absent, old = fleet.absent.slug, fleet.old.slug
        self.assertEqual(
            open_cmd(self.session, scope=absent), f"opened=- skipped={absent}:absent\n"
        )
        self.assertEqual(
            open_cmd(self.session, scope=old), f"opened=- skipped={old}:old-schema\n"
        )
        self.assertEqual(
            open_cmd(self.session, scope="nosuch"), "opened=- skipped=nosuch:absent\n"
        )

    def test_the_entry_survives_a_rollback_of_the_callers_transaction(self):
        with transaction.atomic():
            operator.open_entries(self.session, NAME, SLUG, REASON, "data")
            transaction.set_rollback(True)
        self.assertEqual(len(operator_entries(TEST_SCHEMA, self.session)), 1)

    def test_once_per_process(self):
        open_cmd(self.session)
        self.assertEqual(open_cmd(self.session), f"opened={SLUG} skipped=-\n")
        self.assertEqual(len(operator_entries(TEST_SCHEMA, self.session)), 1)

    def test_an_insert_failure_is_answered_as_failed_and_exits_3(self):
        """Review A7: the answer still names the hospital (staging.sh records it in the scope
        line, then refuses the session on exit 3)."""
        out = io.StringIO()
        with (
            patch.object(operator, "_insert", side_effect=DatabaseError("boom")),
            self.assertRaisesRegex(
                CommandError, f"could not be written.*{SLUG}"
            ) as caught,
        ):
            call_command(
                "ihr_operator_open",
                "--session",
                self.session,
                "--operator",
                NAME,
                "--scope",
                SLUG,
                "--reason",
                REASON,
                "--tier",
                "data",
                stdout=out,
            )
        self.assertEqual(caught.exception.returncode, 3)
        self.assertEqual(out.getvalue(), f"opened=- skipped={SLUG}:failed\n")
        self.assertEqual(operator_entries(TEST_SCHEMA, self.session), [])

    def test_a_failure_in_one_hospital_keeps_the_others_and_names_it(self):
        """Review A7: entries already written stay (autocommit); every hospital is answered,
        the failed one as ``failed``, so the journal accounts for each entry."""
        fleet = Fleet(self).build()
        real = operator._insert

        def insert(cursor, schema, actor, ctx):
            if schema == fleet.new.schema_name:
                raise DatabaseError("canceling statement due to lock timeout")
            return real(cursor, schema, actor, ctx)

        out = io.StringIO()
        with (
            patch.object(operator, "_insert", side_effect=insert),
            self.assertRaises(CommandError) as caught,
        ):
            call_command(
                "ihr_operator_open",
                "--session",
                self.session,
                "--operator",
                NAME,
                "--scope",
                "all",
                "--reason",
                "job:release",
                "--tier",
                "job",
                stdout=out,
            )
        self.assertEqual(caught.exception.returncode, 3)
        self.assertIn(fleet.new.slug, str(caught.exception))
        opened, skipped = parse(out.getvalue())
        self.assertIn(SLUG, opened)
        self.assertEqual(skipped[fleet.new.slug], "failed")
        self.assertEqual(skipped[fleet.held.slug], "held")
        self.assertEqual(len(operator_entries(TEST_SCHEMA, self.session)), 1)
        self.assertEqual(operator_entries(fleet.new.schema_name, self.session), [])

    def test_the_chain_verifies_with_ihr_audit_verify(self):
        open_cmd(self.session)
        store = FakeStore()
        out = io.StringIO()
        with patch.object(anchor, "S3Store", lambda role, quick=False: store):
            anchor.write(store)
            try:
                call_command("ihr_audit_verify", slug=SLUG, stdout=out, stderr=out)
                code = 0
            except SystemExit as exc:
                code = exc.code or 0
        self.assertEqual(code, 0, out.getvalue())
        row = self.one(operator_entries(TEST_SCHEMA, self.session))
        self.assertTrue(row["sealed"])


class OldReadersTests(OneMixin, ViewerCase):
    """The viewer, the export, the standalone verifier and the watch take an ``operator`` entry
    as they take any other kind (no viewer change until P4-T6)."""

    def setUp(self):
        super().setUp()
        self.session = new_session()
        with as_owner():
            operator.open_entries(self.session, NAME, SLUG, REASON, "data")
        self.row = self.one(operator_entries(TEST_SCHEMA, self.session))

    def test_the_viewer_lists_and_shows_it_and_its_filter_still_refuses_the_kind(self):
        response = self.get("entries/", limit=200)
        self.assertEqual(response.status_code, 200)
        listed = {item["id"]: item for item in response.json()["results"]}
        self.assertEqual(listed[self.row["id"]]["kind"], "operator")
        self.assertEqual(listed[self.row["id"]]["actor"], f"operator:{NAME}")
        self.assertEqual(self.get(f"entries/{self.row['id']}/").status_code, 200)
        self.assertEqual(self.get("entries/", kind="operator").status_code, 400)

    def test_the_exports_and_the_verifier_accept_it(self):
        response = self.get("export/", **{"from": today(), "to": today()})
        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content).decode()
        rows = list(csv.DictReader(io.StringIO(body.lstrip("﻿"))))
        self.assertTrue(
            any(
                r["entry_id"] == str(self.row["id"]) and r["kind"] == "operator"
                for r in rows
            )
        )
        seal.seal_once(TEST_SCHEMA)
        response = self.get(
            "export/", **{"from": today(), "to": today(), "file": "json"}
        )
        self.assertEqual(response.status_code, 200)
        lines = b"".join(response.streaming_content).decode().splitlines()
        result = verify_export.check(lines)
        self.assertEqual(result.problems, [])
        # [at, patients, id, txid, kind, actor, ...]
        self.assertIn(
            [self.row["id"], "operator"], [[e[2], e[4]] for e in result.entries]
        )

    def test_the_watch_passes_over_it(self):
        hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
        with connection.cursor() as cursor:
            checked = incremental.check_from_checkpoint(
                cursor, TEST_SCHEMA, str(hospital.uuid), None, None, events.Events()
            )
        self.assertEqual(checked.problems, [])
        with (
            tempfile.TemporaryDirectory() as d,
            patch.object(watch, "_emit", lambda line: None),
        ):
            w = watch.Watch(watch.Config(root=Path(d)), store=None)
            w.run("fast")
            w.run("incremental")
            text = w.gauges.render()
        self.assertIn("ihr_audit_chain_broken_hospitals 0", text)
        for name in watch.PASSES[:2]:
            self.assertIn(f'ihr_audit_watch_last_run_ok{{pass="{name}"}} 1', text)


class ProcessTests(SimpleTestCase):
    """What a one-off of a session gets from the apps loading (no database): the actor on every
    write and the session in its application name; a bad value stops it."""

    CODE = (
        "import django; django.setup(); from django.conf import settings; "
        "from ihr_audit import context; print(context.current_actor()); "
        "print(settings.DATABASES['default']['OPTIONS']['application_name'])"
    )

    def run_python(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("IHR_OPERATOR")}
        env.update(
            DJANGO_SETTINGS_MODULE="ihr_platform.settings.test",
            IHR_PROCESS_NAME="manage:shell",
            **extra,
        )
        return subprocess.run(  # noqa: S603 (this interpreter, fixed code)
            [sys.executable, "-c", self.CODE],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )

    def test_a_one_off_of_a_session_names_the_operator(self):
        done = self.run_python(
            IHR_OPERATOR_SESSION="0123456789ab",
            IHR_OPERATOR=NAME,
            IHR_OPERATOR_SCOPE=SLUG,
            IHR_OPERATOR_REASON=REASON,
            IHR_OPERATOR_TIER="data",
        )
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.assertEqual(
            done.stdout.splitlines()[-2:],
            [f"operator:{NAME}", "op:0123456789ab:manage:shell"],
        )
        plain = self.run_python()
        self.assertEqual(plain.returncode, 0, plain.stderr[-2000:])
        self.assertEqual(plain.stdout.splitlines()[-2:], ["", "manage:shell"])

    SERVICES = {
        "drill": (
            "ihr-restore-drill",
            {"DATABASE_URL": "postgres://ihr_owner:x@db:5432/ihr"},
        ),
        "watch": (
            "ihr-audit-watch",
            {
                "DATABASE_URL": "postgres://ihr_audit_watch:x@db:5432/ihr",
                "IHR_AUDIT_ANCHOR_READ_KEY": "k",
                "IHR_TENANT_DOMAIN_SUFFIX": "example.in",
                "IHR_DB_ROLES": "required",
                "IHR_DB_ROLE": "watch",
            },
        ),
    }

    def test_the_drill_and_watch_settings_name_the_session_too(self):
        """Review S6/A6: their fixed names were set before apply_tenancy, whose setdefault then
        never named the session; under a session they are op:<session>:<name>."""
        code = (
            "import importlib, sys; m = importlib.import_module(sys.argv[1]); "
            "print(m.DATABASES['default']['OPTIONS']['application_name'])"
        )
        for module, (base, extra) in self.SERVICES.items():
            for session, want in (
                ("0123456789ab", f"op:0123456789ab:{base}"),
                ("", base),
            ):
                env = {
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                    "HOME": os.environ.get("HOME") or tempfile.gettempdir(),
                    **extra,
                }
                if session:
                    env["IHR_OPERATOR_SESSION"] = session
                with self.subTest(module=module, session=session):
                    done = subprocess.run(  # noqa: S603 (this interpreter, fixed code)
                        [sys.executable, "-c", code, f"ihr_platform.settings.{module}"],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=180,
                        check=False,
                    )
                    self.assertEqual(done.returncode, 0, done.stderr[-2000:])
                    self.assertEqual(done.stdout.strip(), want)

    def test_a_bad_value_stops_the_one_off(self):
        done = self.run_python(
            IHR_OPERATOR_SESSION="0123456789ab",
            IHR_OPERATOR="root",
            IHR_OPERATOR_SCOPE=SLUG,
            IHR_OPERATOR_REASON=REASON,
            IHR_OPERATOR_TIER="data",
        )
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("shared-operator", done.stderr)
