"""Per-process database limits (P1-T23, Q-K92/Q-K93; ihr_platform/db_limits.py).

- ``ProfileTests`` (no database): which profile a process gets, the options string it becomes,
  and that a settings module's own choice (the audit watch) is never merged with ours.
- ``LiveLimitTests`` (a real second connection per case, the limits scaled down to about a
  second by the same builder): the web profile cancels a long statement, ends a session left
  idle in a transaction and gives up on a lock; the owner profile runs the same sleep to its end.
- ``AppliedTests``: the test run's own connection carries the ``test`` profile, so the rule is
  wired into ``apply_tenancy`` (it fails without that line), and a connection that shows other
  values than its process's profile is refused with the reason ``limits`` (at the first
  connection and at every later one).
"""

import time

import psycopg
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import SimpleTestCase, override_settings
from psycopg import errors

from ihr_platform import db_limits, db_roles
from ihr_platform.settings.tenancy import apply_tenancy
from ihr_platform.testing import as_owner

SCALED = db_limits.Limits(statement=1000, lock=500, idle_in_transaction=1500)


def fresh(limits, *, autocommit=True):
    """A second session on the test database that opens with ``limits`` as its startup
    options, in the form ``apply_to_database_options`` gives them to Django."""
    config = connection.settings_dict
    return psycopg.connect(
        dbname=config["NAME"],
        user=config["USER"],
        password=config["PASSWORD"],
        host=config["HOST"],
        port=config["PORT"] or None,
        options=limits.options() if limits else None,
        autocommit=autocommit,
    )


class ProfileTests(SimpleTestCase):
    def test_values_are_the_designed_ones(self):
        web, worker, owner = (db_limits.PROFILES[n] for n in ("web", "worker", "owner"))
        self.assertEqual(
            (web.statement, web.lock, web.idle_in_transaction), (30_000, 10_000, 60_000)
        )
        self.assertEqual(
            (worker.statement, worker.lock, worker.idle_in_transaction),
            (300_000, 30_000, 300_000),
        )
        self.assertEqual(
            (owner.statement, owner.lock, owner.idle_in_transaction), (0, 0, 1_800_000)
        )
        self.assertEqual(
            db_limits.PROFILES["watch"],
            db_limits.Limits(300_000, 10_000, 60_000),
            "the audit watch keeps the values it had before P1-T23",
        )

    def test_the_options_string(self):
        self.assertEqual(
            SCALED.options(),
            "-c statement_timeout=1000 -c lock_timeout=500 "
            "-c idle_in_transaction_session_timeout=1500",
        )
        text = db_limits.options_string("watch", read_only=True)
        self.assertTrue(text.startswith("-c default_transaction_read_only=on "))
        for part in (
            "statement_timeout=300000",
            "lock_timeout=10000",
            "idle_in_transaction_session_timeout=60000",
        ):
            self.assertIn(part, text)
        self.assertEqual(db_limits.options_string("off"), "")

    def test_the_process_decides_the_profile(self):
        cases = (
            ("gunicorn", "required", "app", [], "web"),
            ("celery", "required", "app", ["celery", "worker"], "worker"),
            ("celery", "required", "app", ["celery", "beat"], "web"),
            ("manage:runserver", "off", "", [], "web"),
            ("manage:test", "test", "", [], "test"),
            ("manage:test", "required", "owner", [], "test"),
            ("manage:migrate_schemas", "required", "owner", [], "owner"),
            ("manage:ihr_hospital_restore", "required", "owner", [], "owner"),
            ("manage:ihr_provision_worker", "required", "app", [], "worker"),
            ("manage:shell", "required", "app", [], "worker"),
            ("manage:ihr_seed_demo", "off", "", [], "owner"),
            ("python", "required", "app", [], "worker"),
        )
        for process, roles, role, argv, want in cases:
            with self.subTest(process=process, roles=roles, role=role, argv=argv):
                self.assertEqual(db_limits.derive(process, roles, role, argv), want)

    def test_a_named_profile_wins_and_an_unknown_one_is_refused(self):
        def get(name, default=""):
            return {"IHR_DB_ROLES": "off", "IHR_DB_ROLE": ""}.get(name, default)

        self.assertEqual(db_limits.chosen(get, {"IHR_DB_LIMITS": "web"}), "web")
        self.assertEqual(db_limits.chosen(get, {"IHR_DB_LIMITS": "off"}), "off")
        with self.assertRaisesRegex(ImproperlyConfigured, "IHR_DB_LIMITS"):
            db_limits.chosen(get, {"IHR_DB_LIMITS": "nonsense"})

    def test_apply_tenancy_puts_the_profile_into_the_connection_options(self):
        base = {
            "INSTALLED_APPS": [],
            "DATABASES": {"default": {}},
            "MIDDLEWARE": [],
            "CACHES": {},
            "REST_FRAMEWORK": {},
            "SPECTACULAR_SETTINGS": {},
            "DEBUG": True,
            "IHR_DB_LIMITS": "worker",
        }
        out = dict(base, DATABASES={"default": {}})
        apply_tenancy(out)
        self.assertEqual(
            out["DATABASES"]["default"]["OPTIONS"]["options"],
            db_limits.PROFILES["worker"].options(),
        )

    def test_a_settings_module_that_chose_its_own_options_keeps_them(self):
        """The audit watch sets read-only sessions itself: nothing of ours is merged in."""
        options = {"options": "-c default_transaction_read_only=on"}
        db_limits.apply_to_database_options(options, "web")
        self.assertEqual(options["options"], "-c default_transaction_read_only=on")
        empty = {}
        db_limits.apply_to_database_options(empty, "off")
        self.assertEqual(empty, {})


class LiveLimitTests(SimpleTestCase):
    """Real sessions, each opened with the (scaled) limits as startup options."""

    databases = {"default"}

    def open(self, limits, **kwargs):
        session = fresh(limits, **kwargs)
        self.addCleanup(session.close)
        return session

    def test_a_long_statement_is_cancelled_for_a_web_session(self):
        web = self.open(SCALED)
        with self.assertRaises(errors.QueryCanceled):
            web.execute("SELECT pg_sleep(3)")

    def test_the_owner_profile_runs_the_same_statement_to_its_end(self):
        owner = self.open(db_limits.Limits(0, 0, 1_800_000))
        began = time.monotonic()
        owner.execute("SELECT pg_sleep(2)")
        self.assertGreaterEqual(time.monotonic() - began, 1.9)

    def test_a_session_left_idle_inside_a_transaction_is_ended(self):
        web = self.open(SCALED, autocommit=False)
        web.execute("SELECT 1")  # a transaction is now open
        time.sleep(2.6)  # past idle_in_transaction_session_timeout (1.5 s)
        with self.assertRaises(errors.IdleInTransactionSessionTimeout):
            web.execute("SELECT 1")
        # and without a transaction open, idling is free
        calm = self.open(SCALED)
        calm.execute("SELECT 1")
        time.sleep(2.0)
        self.assertEqual(calm.execute("SELECT 1").fetchone(), (1,))

    def test_a_lock_that_is_not_given_in_time_gives_up(self):
        holder = self.open(None)
        holder.execute("SELECT pg_advisory_lock(907023)")
        web = self.open(SCALED)
        began = time.monotonic()
        with self.assertRaises(errors.LockNotAvailable):
            web.execute("SELECT pg_advisory_lock(907023)")
        self.assertLess(time.monotonic() - began, 2.5)

    def test_without_the_options_none_of_this_happens(self):
        """The control: a session opened with no limits waits the whole sleep out, so the
        tests above prove the options and nothing else."""
        plain = self.open(None)
        row = plain.execute(
            "SELECT pg_sleep(1.3), current_setting('statement_timeout')"
        ).fetchone()
        self.assertIn(row[1], ("0", "0ms"))


class AppliedTests(SimpleTestCase):
    databases = {"default"}

    def test_the_test_runs_own_connection_carries_the_test_profile(self):
        """Fails when apply_tenancy does not put the profile into OPTIONS."""
        self.assertEqual(settings.IHR_DB_LIMITS, "test")
        with connection.cursor() as cursor:
            self.assertEqual(
                db_limits.observed(cursor),
                db_limits.PROFILES["test"].values(),
            )
            self.assertEqual(db_limits.mismatches(cursor), [])

    def test_a_connection_that_shows_other_values_is_found(self):
        """A pooler that drops startup options, or a server default that won over them."""
        with connection.cursor() as cursor:
            # (the test profile and the web one hold the same values: only the others differ)
            for profile in ("worker", "owner"):
                with (
                    self.subTest(profile=profile),
                    override_settings(IHR_DB_LIMITS=profile),
                ):
                    found = db_limits.mismatches(cursor)
                    self.assertTrue(found, profile)
                    problems = db_roles.limits_problems(cursor)
                    self.assertEqual({reason for reason, _ in problems}, {"limits"})
                    self.assertIn("ms here", problems[0][1])
            with override_settings(IHR_DB_LIMITS="off"):
                self.assertEqual(db_roles.limits_problems(cursor), [])

    def test_the_check_of_every_later_connection_names_it(self):
        with connection.cursor() as cursor, override_settings(IHR_DB_LIMITS="worker"):
            reasons = [reason for reason, _ in db_roles.reconnect_problems(cursor)]
        self.assertIn("limits", reasons)
        with connection.cursor() as cursor:  # the test profile: nothing to find
            reasons = [reason for reason, _ in db_roles.reconnect_problems(cursor)]
        self.assertNotIn("limits", reasons)

    def test_the_first_connection_check_refuses_first_for_it(self):
        """check_connection puts the limits before every role check, for any declared role."""
        with override_settings(IHR_DB_LIMITS="worker"):
            for role in ("app", "owner", "watch"):
                with self.subTest(role=role):
                    found = db_roles.check_connection(connection, role)
                    self.assertEqual([reason for reason, _ in found][:1], ["limits"])

    def test_limits_is_a_reason_the_log_alert_can_carry(self):
        self.assertIn("limits", db_roles.REASONS)
        self.assertRegex(
            db_roles.refusal_line("app", "limits"),
            r"^ihr-db-role: refused role=app reason=[a-z_]{3,24}$",
        )

    def test_the_database_level_setting_is_a_problem(self):
        """A setting stored for the whole database sits under every session: it would hide
        behind the startup options, so the role check names it."""
        name = connection.settings_dict["NAME"]
        with as_owner(), connection.cursor() as cursor:
            cursor.execute(f"ALTER DATABASE \"{name}\" SET statement_timeout = '1h'")
            try:
                found = db_roles.role_problems(cursor, "test")
            finally:
                cursor.execute(f'ALTER DATABASE "{name}" RESET ALL')
            clean = db_roles.role_problems(cursor, "test")
        self.assertTrue(
            any(
                "the database has stored settings (statement_timeout)" in text
                for _, text in found
            ),
            found,
        )
        self.assertFalse(
            any("the database has stored settings" in text for _, text in clean)
        )


class ExportSnapshotTests(SimpleTestCase):
    """The audit export streams from one transaction that sits idle between two fetches while
    the client reads: the web profile's 60 s idle limit would cut a slow download, so the
    snapshot gives itself 10 minutes (and no more) for its own transaction only."""

    databases = {"default"}

    def test_the_snapshot_waits_longer_than_the_profile_and_only_for_itself(self):
        from ihr_audit import export

        def idle(cursor):
            cursor.execute(
                "SELECT current_setting('idle_in_transaction_session_timeout')"
            )
            return cursor.fetchone()[0]

        with connection.cursor() as cursor:
            outside = idle(cursor)
        self.assertEqual(outside, "1min")  # the test profile's 60 s
        with export._snapshot() as cursor:
            self.assertEqual(idle(cursor), "10min")
        with connection.cursor() as cursor:
            self.assertEqual(idle(cursor), outside)
