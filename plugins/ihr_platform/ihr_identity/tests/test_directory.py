"""The staff directory behind the hospital switcher (P1-T31a; docs/reviews/p1-t31/design.md
§3.1-3.2 and §5 tests 1-6 and 12, as changed by attack.md "Proposed orchestrator decision").

``public.ihr_platform_staffdirectoryentry`` holds one row per *listed* link (a Keycloak person,
a hospital). Each hospital schema's ``ihr_directory_touch()`` (SECURITY DEFINER, owner) keeps
it: it recomputes the link from that schema's own rows, for the hospital whose schema fired it.
The app only reads the table (tests run as ihr_app, IHR_DB_ROLES=test).

- ``Listing`` (the test hospital): tests 2-5, the real activation paths (first sign-in after
  the setup e-mail, the "Join" click) and the first fill of ihr_identity 0004.
- ``TwoHospitals`` (a second hospital made for real by ihr_create_tenant): tests 1, 3
  (TRUNCATE) and 6, and a fresh hospital's triggers before any user write (attack D7).
- ``Upkeep``: test 12 (``ensure_directory`` puts back a dropped, weakened, edited or disabled
  function or trigger), E027 and W027.
- ``Repair`` (P1-T31b): test 11 (``rebuild`` and ``manage.py ihr_staff_directory
  --check|--repair``, its drift line).
"""

import importlib
import inspect
import re
import uuid
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from care.users.models import User
from django.apps import apps
from django.core.management import CommandError, call_command
from django.db import connection, transaction
from django.db.models.signals import post_migrate
from django.test import SimpleTestCase
from django_tenants.utils import schema_context
from ihr_platform import release
from ihr_platform.models import Domain, Hospital, RetiredAddress, StaffDirectoryEntry
from ihr_platform.provisioning import runner
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_platform.testing import as_owner
from rest_framework.test import APIClient

from ihr_identity import checks, directory, testing
from ihr_identity.models import StaffIdentity
from ihr_identity.tests import test_invitations as invitation_tests

SECOND_SLUG = "dirtest"
MIGRATION = "ihr_identity.migrations.0004_staff_directory"


def listed(sub):
    """The schemas of the hospitals whose directory lists ``sub`` (read as ihr_app)."""
    return set(
        StaffDirectoryEntry.objects.filter(keycloak_sub=sub).values_list(
            "hospital__schema_name", flat=True
        )
    )


def new_user(**fields):
    name = f"dir-{uuid.uuid4().hex[:10]}"
    return User.objects.create_user(username=name, password="x-Unused-1", **fields)


def calls(cursor, schema):
    """How often this transaction ran ``schema``.ihr_directory_touch() (track_functions)."""
    cursor.execute(
        "SELECT coalesce(sum(calls), 0) FROM pg_stat_xact_user_functions "
        "WHERE schemaname = %s AND funcname = %s",
        [schema, directory.FUNCTION],
    )
    return cursor.fetchone()[0]


class DirectoryCase(SimpleTestCase):
    databases = {"default"}

    def setUp(self):
        connection.set_schema(TEST_SCHEMA)
        self.subs = []

    def tearDown(self):
        connection.set_schema(TEST_SCHEMA)
        made = User._base_manager.filter(username__startswith="dir-")
        StaffIdentity.objects.filter(user__in=made).delete()
        made.delete()
        with as_owner():  # a backstop: the triggers have removed them already
            StaffDirectoryEntry.objects.filter(keycloak_sub__in=self.subs).delete()
        super().tearDown()

    def linked(self, sub=None, **fields):
        """A new user here, linked to ``sub`` (a new one by default): (user, sub)."""
        user = new_user(**fields)
        sub = sub or uuid.uuid4()
        self.subs.append(sub)
        testing.link(user, sub)
        return user, sub


class Listing(DirectoryCase):
    def test_a_link_of_an_active_user_is_listed(self):
        user, sub = self.linked()
        self.assertEqual(listed(sub), {TEST_SCHEMA})
        _inactive, other = self.linked(is_active=False)
        self.assertEqual(listed(other), set())
        # Moving the link to another person moves the listing (UPDATE OF keycloak_sub)
        third = uuid.uuid4()
        self.subs.append(third)
        StaffIdentity.objects.filter(user=user).update(keycloak_sub=third)
        self.assertEqual((listed(sub), listed(third)), (set(), {TEST_SCHEMA}))

    def test_deactivation_removes_the_row_at_once(self):
        """Test 2: by save() and by queryset.update() (no signal fires), in the same
        transaction; back to active brings it back. Deleted, superuser and service
        accounts are never listed (Q-K145 A)."""
        user, sub = self.linked()
        with transaction.atomic():
            user.is_active = False
            user.save()
            self.assertEqual(listed(sub), set())
            user.is_active = True
            user.save()
            self.assertEqual(listed(sub), {TEST_SCHEMA})
            transaction.set_rollback(True)
        for change in (
            {"is_active": False},
            {"deleted": True},
            {"is_superuser": True},
            {"is_service_account": True},
        ):
            with self.subTest(change), transaction.atomic():
                User._base_manager.filter(pk=user.pk).update(**change)
                self.assertEqual(listed(sub), set())
                User._base_manager.filter(pk=user.pk).update(
                    **dict.fromkeys(change, not next(iter(change.values())))
                )
                self.assertEqual(listed(sub), {TEST_SCHEMA})
                transaction.set_rollback(True)
        self.assertEqual(listed(sub), {TEST_SCHEMA})

    def test_unlinking_or_deleting_removes_the_row(self):
        """Test 3: the link deleted; the user hard-deleted (the link goes with it)."""
        user, sub = self.linked()
        StaffIdentity.objects.filter(user=user).delete()
        self.assertEqual(listed(sub), set())
        user, sub = self.linked()
        User._base_manager.filter(pk=user.pk).delete()
        self.assertFalse(User._base_manager.filter(pk=user.pk).exists())
        self.assertEqual(listed(sub), set())

    def test_a_cached_role_orgs_save_never_runs_the_function(self):
        """Test 5 (attack D5): CARE re-saves cached_role_orgs on every membership change; the
        trigger's WHEN clause keeps the function from even starting."""
        user, sub = self.linked()
        with as_owner(), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL track_functions = 'pl'")
            cursor.execute(
                "UPDATE users_user SET cached_role_orgs = '[]'::jsonb, last_login = now(), "
                "first_name = first_name || 'x' WHERE id = %s",
                [user.pk],
            )
            self.assertEqual(calls(cursor, TEST_SCHEMA), 0)
            cursor.execute(
                "UPDATE users_user SET is_active = false WHERE id = %s", [user.pk]
            )
            self.assertEqual(calls(cursor, TEST_SCHEMA), 1)  # it is counted at all
            transaction.set_rollback(True)
        self.assertEqual(listed(sub), {TEST_SCHEMA})

    def test_the_first_fill_makes_the_rows_equal_to_the_links(self):
        """ihr_identity 0004 fills its own hospital's rows (attack D2): a missing row is
        added, an extra one removed, other hospitals' rows untouched."""
        module = importlib.import_module(MIGRATION)
        _user, sub = self.linked()
        stray = uuid.uuid4()
        self.subs.append(stray)
        with as_owner(), transaction.atomic():
            hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
            StaffDirectoryEntry.objects.filter(keycloak_sub=sub).delete()
            StaffDirectoryEntry.objects.create(keycloak_sub=stray, hospital=hospital)
            self.assertEqual((listed(sub), listed(stray)), (set(), {TEST_SCHEMA}))
            with connection.cursor() as cursor:
                module.fill(cursor, TEST_SCHEMA)
            self.assertEqual((listed(sub), listed(stray)), ({TEST_SCHEMA}, set()))
            with connection.cursor() as cursor:
                module.fill(cursor, "h_nosuchone")  # no hospital: nothing
            transaction.set_rollback(True)

    def test_the_first_fill_leaves_a_hospital_held_by_a_restore_alone(self):
        """T1 (attack D6): a restore's migrate step runs 0004 under the hospital's own schema
        name while the hospital is held; nothing outside the schema changes before the
        restore's reconcile, so the fill writes no row for a hospital held by a restore."""
        module = importlib.import_module(MIGRATION)
        _user, sub = self.linked()
        stray = uuid.uuid4()
        self.subs.append(stray)
        with as_owner(), transaction.atomic():
            hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
            StaffDirectoryEntry.objects.filter(keycloak_sub=sub).delete()
            StaffDirectoryEntry.objects.create(keycloak_sub=stray, hospital=hospital)
            release.restore_hold(hospital, uuid.uuid4())
            with connection.cursor() as cursor:
                module.fill(cursor, TEST_SCHEMA)
            self.assertEqual((listed(sub), listed(stray)), (set(), {TEST_SCHEMA}))
            release.lift_restore_hold(hospital, release.restore_holder(hospital))
            with connection.cursor() as cursor:
                module.fill(cursor, TEST_SCHEMA)  # lifted: the fill works again
            self.assertEqual((listed(sub), listed(stray)), ({TEST_SCHEMA}, set()))
            transaction.set_rollback(True)

    def test_migration_0004_freezes_its_sql(self):
        """Like ihr_audit 0004 (review C5): the migration never imports the live code, and
        it states the fingerprint W027 compares (attack D7)."""
        module = importlib.import_module(MIGRATION)
        imports = [
            line
            for line in inspect.getsource(module).splitlines()
            if line.lstrip().startswith(("import ", "from "))
        ]
        self.assertFalse([line for line in imports if "ihr_" in line], imports)
        self.assertRegex(module.DIRECTORY_SHA256, r"^[0-9a-f]{64}$")
        dependencies = dict(module.Migration.dependencies)
        self.assertEqual(dependencies["ihr_platform"], "0013_staffdirectoryentry")
        self.assertIn("users", dependencies)


class ActivationPaths(invitation_tests.InvitationCase):
    """Test 2 and 4 through the real paths (attack D2): our own code activates a person with
    queryset.update(), which no signal sees. Invited, awaiting setup and waiting for "Join"
    are inactive: not listed. The first sign-in after the setup e-mail (views._finish_setup)
    and the "Join" click (join.py) list them at once."""

    sign_in = invitation_tests.JoinTests.sign_in

    def setUp(self):
        super().setUp()
        self.browser = APIClient(HTTP_HOST=invitation_tests.HOST)

    def test_the_first_sign_in_after_the_setup_email_lists_the_person(self):
        user, inv = self.invite()
        self.assertEqual(StaffIdentity.objects.filter(user=user).count(), 0)
        self.deliver()
        inv.refresh_from_db()
        self.sub = str(inv.keycloak_sub)
        self.assertTrue(StaffIdentity.objects.get(user=user).awaiting_setup)
        self.assertEqual(listed(self.sub), set())
        self.sign_in()
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(listed(self.sub), {TEST_SCHEMA})

    def test_the_join_click_lists_the_person(self):
        user, inv = self.invite()
        self.deliver(existing=[inv.email])
        inv.refresh_from_db()
        self.sub = str(inv.keycloak_sub)
        self.assertTrue(StaffIdentity.objects.get(user=user).join_confirmation_required)
        link = re.search(
            r"(/api/ihr/join/\?t=[\w-]+)", invitation_tests.mail.outbox[0].body
        )
        self.browser.get(link.group(1))
        self.sign_in()
        self.assertEqual(listed(self.sub), set())  # signed in, not joined
        page = self.browser.get("/api/ihr/join/confirm/").content.decode()
        nonce = re.search(r'name="nonce" value="([^"]+)"', page).group(1)
        self.browser.post(
            "/api/ihr/join/confirm/",
            {"nonce": nonce},
            HTTP_SEC_FETCH_SITE="same-origin",
        )
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(listed(self.sub), {TEST_SCHEMA})


class TwoHospitals(DirectoryCase):
    """A second hospital, made for real (its migrations, its post_migrate)."""

    second = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        connection.set_schema_to_public()
        if not Hospital.objects.filter(slug=SECOND_SLUG).exists():
            with as_owner():
                # (a deleted hospital's slug is retired: an earlier run's, with --keepdb)
                RetiredAddress.objects.filter(value__startswith=SECOND_SLUG).delete()
                call_command(
                    "ihr_create_tenant",
                    slug=SECOND_SLUG,
                    name="Directory Test",
                    verbosity=0,
                )
        connection.set_schema_to_public()
        cls.second = Hospital.objects.get(slug=SECOND_SLUG)
        connection.set_schema(TEST_SCHEMA)

    @classmethod
    def tearDownClass(cls):
        connection.set_schema_to_public()
        schema = cls.second.schema_name
        with as_owner(), connection.cursor() as cursor:
            StaffDirectoryEntry.objects.filter(hospital=cls.second).delete()
            Domain.objects.filter(tenant=cls.second).delete()
            Hospital.objects.filter(pk=cls.second.pk).delete()
            cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        connection.set_schema(TEST_SCHEMA)
        super().tearDownClass()

    def tearDown(self):
        with schema_context(self.second.schema_name):
            made = User._base_manager.filter(username__startswith="dir-")
            StaffIdentity.objects.filter(user__in=made).delete()
            made.delete()
        super().tearDown()

    def in_second(self, sub=None, **fields):
        with schema_context(self.second.schema_name):
            return self.linked(sub, **fields)

    def test_a_new_hospital_has_the_triggers_before_any_user_write(self):
        """Attack D7: ihr_create_tenant's migrate installs them (0004 and post_migrate)."""
        with connection.cursor() as cursor:
            self.assertEqual(directory.problems(cursor, self.second.schema_name), [])
            cursor.execute(
                "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s AND t.tgname LIKE 'ihr_directory_%%' ORDER BY 1",
                [self.second.schema_name],
            )
            self.assertEqual(
                [name for (name,) in cursor.fetchall()], sorted(directory.TRIGGERS)
            )

    def test_one_person_at_two_hospitals_has_two_rows(self):
        """Test 1."""
        _b, sub = self.in_second()
        self.assertEqual(listed(sub), {self.second.schema_name})
        self.linked(sub)
        self.assertEqual(listed(sub), {self.second.schema_name, TEST_SCHEMA})
        self.assertEqual(
            StaffDirectoryEntry.objects.filter(keycloak_sub=sub).count(), 2
        )

    def test_a_write_in_one_hospital_never_touches_another(self):
        """Test 6 (attack D5): the hospital is the schema that fired, never a row's value.
        S linked only in B and activated there: (S, B) and no (S, A). Writes in A, for
        another person and for S linked there to an inactive user, leave (S, B) alone."""
        b_user, sub = self.in_second(is_active=False)
        self.assertEqual(listed(sub), set())
        with schema_context(self.second.schema_name):
            User._base_manager.filter(pk=b_user.pk).update(is_active=True)
        self.assertEqual(listed(sub), {self.second.schema_name})
        a_user, other = self.linked()
        User._base_manager.filter(pk=a_user.pk).update(is_active=False)
        self.assertEqual(listed(other), set())
        a_inactive, _ = self.linked(sub, is_active=False)
        self.assertEqual(listed(sub), {self.second.schema_name})
        StaffIdentity.objects.filter(user=a_inactive).delete()
        self.assertEqual(listed(sub), {self.second.schema_name})

    def test_truncate_empties_only_its_own_hospital(self):
        """Test 3: TRUNCATE (a statement trigger) removes every row of that hospital only."""
        _a, here = self.linked()
        _b, there = self.in_second()
        with as_owner(), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(f"TRUNCATE {TEST_SCHEMA}.ihr_identity_staffidentity")
            self.assertEqual(
                StaffDirectoryEntry.objects.filter(
                    hospital__schema_name=TEST_SCHEMA
                ).count(),
                0,
            )
            self.assertEqual(listed(there), {self.second.schema_name})
            transaction.set_rollback(True)
        self.assertEqual(listed(here), {TEST_SCHEMA})


class Upkeep(DirectoryCase):
    BREAKAGES = {
        "dropped": f"DROP FUNCTION {TEST_SCHEMA}.ihr_directory_touch() CASCADE",
        "invoker": f"ALTER FUNCTION {TEST_SCHEMA}.ihr_directory_touch() SECURITY INVOKER",
        "path": f"ALTER FUNCTION {TEST_SCHEMA}.ihr_directory_touch() RESET search_path",
        "body": (
            f"CREATE OR REPLACE FUNCTION {TEST_SCHEMA}.ihr_directory_touch() RETURNS trigger "
            "LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp "
            "AS 'BEGIN RETURN NULL; END'"
        ),
        "disabled": (
            f"ALTER TABLE {TEST_SCHEMA}.users_user DISABLE TRIGGER ihr_directory_user"
        ),
        "trigger dropped": (
            f"DROP TRIGGER ihr_directory_truncate ON {TEST_SCHEMA}.ihr_identity_staffidentity"
        ),
        "every column": (
            f"CREATE OR REPLACE TRIGGER ihr_directory_user AFTER UPDATE ON "
            f"{TEST_SCHEMA}.users_user FOR EACH ROW EXECUTE FUNCTION "
            f"{TEST_SCHEMA}.ihr_directory_touch()"
        ),
        "no when": (
            f"CREATE OR REPLACE TRIGGER ihr_directory_user AFTER UPDATE OF is_active, "
            f"deleted, is_superuser, is_service_account ON {TEST_SCHEMA}.users_user "
            f"FOR EACH ROW EXECUTE FUNCTION {TEST_SCHEMA}.ihr_directory_touch()"
        ),
    }

    def test_this_builds_directory_is_installed(self):
        with connection.cursor() as cursor:
            self.assertEqual(directory.problems(cursor, TEST_SCHEMA), [])
        self.assertFalse(directory.ensure_directory(connection, TEST_SCHEMA))

    def test_post_migrate_puts_back_what_was_broken(self):
        """Test 12: dropped, made SECURITY INVOKER, search_path lost, body edited, a trigger
        disabled, dropped, or firing on every column or without its WHEN: ``problems`` names
        it (``ihr_staff_directory --check``'s drift, P1-T31b) and the next post_migrate's
        ``ensure_directory`` restores it; the directory then works again."""
        for what, statement in self.BREAKAGES.items():
            with self.subTest(what), as_owner(), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(statement)
                    self.assertTrue(directory.problems(cursor, TEST_SCHEMA), what)
                self.assertTrue(directory.ensure_directory(connection, TEST_SCHEMA))
                with connection.cursor() as cursor:
                    self.assertEqual(directory.problems(cursor, TEST_SCHEMA), [], what)
                user, sub = self.linked()
                self.assertEqual(listed(sub), {TEST_SCHEMA})
                User._base_manager.filter(pk=user.pk).update(is_active=False)
                self.assertEqual(listed(sub), set())
                transaction.set_rollback(True)

    def test_a_newer_builds_sql_reaches_the_schema_at_its_next_migrate(self):
        with as_owner(), transaction.atomic():
            with mock.patch.object(directory, "VERSION", "0" * 64):
                with connection.cursor() as cursor:
                    self.assertTrue(directory.problems(cursor, TEST_SCHEMA))
                self.assertTrue(directory.ensure_directory(connection, TEST_SCHEMA))
                with connection.cursor() as cursor:
                    self.assertEqual(directory.problems(cursor, TEST_SCHEMA), [])
            transaction.set_rollback(True)

    def test_the_function_is_the_owners_and_nobody_may_call_it(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT p.prosecdef, p.proconfig, "
                "has_function_privilege('ihr_app', p.oid, 'EXECUTE') "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = %s AND p.proname = %s",
                [TEST_SCHEMA, directory.FUNCTION],
            )
            self.assertEqual(
                cursor.fetchone(), (True, ["search_path=pg_catalog, pg_temp"], False)
            )

    def test_e027_names_a_directory_not_installed_on_post_migrate(self):
        self.assertEqual(checks.staff_directory(None), [])
        uid = apps.get_app_config("ihr_identity").DIRECTORY_UID
        kept = [r for r in post_migrate.receivers if r[0][0] != uid]
        self.assertLess(len(kept), len(post_migrate.receivers))
        with mock.patch.object(post_migrate, "receivers", kept):
            found = checks.staff_directory(None)
        self.assertEqual([f.id for f in found], ["ihr_identity.E027"])

    def test_e027_names_a_care_column_the_trigger_no_longer_finds(self):
        """Attack D7: an upstream rename of a column in the UPDATE OF list fails the tests
        (and the check) instead of silently ending the trigger."""
        self.assertEqual(directory.static_problems(installed=True), [])
        columns = {"is_active", "deleted", "is_superuser"}
        found = directory.static_problems(installed=True, user_columns=columns)
        self.assertEqual(len(found), 1)
        self.assertIn("is_service_account", found[0])

    def test_w027_wants_a_migration_stating_this_builds_sql(self):
        self.assertEqual(directory.code_problems(), [])
        stale = [("0004_staff_directory", SimpleNamespace(DIRECTORY_SHA256="0" * 64))]
        [problem] = directory.code_problems(stale)
        self.assertIn(directory.fingerprint(), problem)
        self.assertTrue(directory.code_problems([]))
        with mock.patch.object(directory, "code_problems", return_value=["x"]):
            self.assertEqual(
                [f.id for f in checks.staff_directory(None)], ["ihr_identity.W027"]
            )


class Repair(DirectoryCase):
    """Test 11 (P1-T31b; attack D2, S8, S9): ``rebuild`` makes a hospital's rows equal to its
    listed links; ``ihr_staff_directory --check`` reports row differences and trigger drift
    (exit 1, the line ``ihr-directory: hospital=<slug> outcome=drift``), ``--repair`` puts both
    right and removes the rows of hospitals the switcher never lists (not migrated, ``public``)."""

    GHOST = "dir-archived"
    SECOND = "dir-second"

    def setUp(self):
        super().setUp()
        connection.set_schema_to_public()
        self.hospital = Hospital.objects.get(schema_name=TEST_SCHEMA)
        with as_owner():
            release.record(
                self.hospital
            )  # --repair writes only into a ready schema (S6)
        connection.set_schema(TEST_SCHEMA)

    def tearDown(self):
        connection.set_schema_to_public()
        with as_owner():
            holder = release.restore_holder(self.hospital)
            if holder:
                release.lift_restore_hold(self.hospital, holder)
            release.release_hold([self.hospital.pk])
            release.record(self.hospital)
            for slug in (self.GHOST, self.SECOND):
                Hospital.objects.filter(slug=slug).delete()  # its rows cascade
                # (a deleted hospital's slug is retired: never reused, so a later run could not)
                RetiredAddress.objects.filter(value=slug).delete()
        super().tearDown()

    def ghost(self):
        """A hospital outside fleet.MIGRATE_STATUSES (archived; no schema)."""
        ghost = Hospital(
            slug=self.GHOST,
            name="Archived",
            schema_name="h_dirarchiv0",
            status=Hospital.Status.ARCHIVED,
        )
        ghost.auto_create_schema = False
        connection.set_schema_to_public()
        RetiredAddress.objects.filter(value=self.GHOST).delete()  # an interrupted run's
        ghost.save()
        connection.set_schema(TEST_SCHEMA)
        return ghost

    def run_command(self, *args):
        """(raised?, stdout, stderr) of ihr_staff_directory."""
        out, err = StringIO(), StringIO()
        try:
            call_command("ihr_staff_directory", *args, stdout=out, stderr=err)
        except CommandError as exc:
            err.write(str(exc))
            return True, out.getvalue(), err.getvalue()
        finally:
            connection.set_schema(TEST_SCHEMA)
        return False, out.getvalue(), err.getvalue()

    def test_rebuild_adds_the_missing_and_removes_the_extra(self):
        _user, sub = self.linked()
        _inactive, off = self.linked(is_active=False)
        stray = uuid.uuid4()
        self.subs.append(stray)
        with as_owner():
            StaffDirectoryEntry.objects.filter(keycloak_sub=sub).delete()
            for extra in (stray, off):  # no link, and a link of an inactive user
                StaffDirectoryEntry.objects.create(
                    keycloak_sub=extra, hospital=self.hospital
                )
            self.assertEqual(listed(sub), set())
            report = directory.rebuild(self.hospital)
        self.assertEqual(report, {"added": 1, "removed": 2})
        self.assertEqual(
            (listed(sub), listed(stray), listed(off)), ({TEST_SCHEMA}, set(), set())
        )
        with connection.cursor() as cursor:
            self.assertEqual(
                directory.differences(cursor, self.hospital), (set(), set())
            )
        with as_owner():  # nothing to do the second time
            self.assertEqual(
                directory.rebuild(self.hospital), {"added": 0, "removed": 0}
            )

    def test_rebuild_writes_nothing_for_public(self):
        """S8: ``ihr_create_tenant --public`` makes an ACTIVE Hospital(schema_name="public")."""
        public = Hospital(schema_name="public", slug="public", pk=self.hospital.pk)
        with as_owner():
            self.assertEqual(directory.rebuild(public), {"added": 0, "removed": 0})

    def test_rebuild_is_the_owners(self):
        """The app reads the table only (public_tables.txt): a rebuild as ihr_app is refused."""
        from django.db import ProgrammingError

        _user, sub = self.linked()
        with as_owner():
            StaffDirectoryEntry.objects.filter(keycloak_sub=sub).delete()
        with self.assertRaises(ProgrammingError):
            directory.rebuild(self.hospital)
        with as_owner():
            directory.rebuild(self.hospital)

    def test_check_reports_until_repair(self):
        """Test 11: --check exits 1 (rows and drift named, one line per hospital), then
        --repair, then --check exits 0. Rows of a hospital outside MIGRATE_STATUSES go."""
        _user, sub = self.linked()
        stray = uuid.uuid4()
        self.subs.append(stray)
        line = f"ihr-directory: hospital={self.hospital.slug} outcome=drift"
        ghost_line = f"ihr-directory: hospital={self.GHOST} outcome=drift"
        with as_owner():
            StaffDirectoryEntry.objects.filter(keycloak_sub=sub).delete()
            StaffDirectoryEntry.objects.create(
                keycloak_sub=stray, hospital=self.ghost()
            )
        failed, out, err = self.run_command("--check")
        self.assertTrue(failed, out)
        self.assertIn(line, err.splitlines())
        self.assertIn(ghost_line, err.splitlines())
        self.assertIn(f"{self.hospital.slug}: 1 row(s) missing, 0 extra", out)
        self.assertIn(
            f"{self.GHOST}: 1 row(s) of a hospital the switcher never lists", out
        )
        self.assertNotIn(str(sub), out + err)  # never a person's sub in the output
        self.assertNotIn(str(stray), out + err)
        failed, _out, _err = self.run_command("--check", "--slug", self.GHOST)
        self.assertTrue(failed)
        with as_owner():
            failed, out, err = self.run_command("--repair")
        self.assertFalse(failed, out + err)
        self.assertEqual((listed(sub), listed(stray)), ({TEST_SCHEMA}, set()))
        failed, out, err = self.run_command("--check")
        self.assertFalse(failed, out + err)
        self.assertNotIn("outcome=drift", err)

    def test_check_names_trigger_drift_and_repair_puts_it_back(self):
        with as_owner(), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(Upkeep.BREAKAGES["disabled"])
            failed, out, err = self.run_command("--check", "--slug", self.hospital.slug)
            self.assertTrue(failed)
            self.assertIn("trigger ihr_directory_user is disabled", out)
            self.assertIn(
                f"ihr-directory: hospital={self.hospital.slug} outcome=drift",
                err.splitlines(),
            )
            failed, out, err = self.run_command(
                "--repair", "--slug", self.hospital.slug
            )
            self.assertFalse(failed, out + err)
            with connection.cursor() as cursor:
                self.assertEqual(directory.problems(cursor, TEST_SCHEMA), [])
            transaction.set_rollback(True)

    def drop_row(self):
        """A person linked here whose directory row is gone: (sub) -- drift for --check."""
        _user, sub = self.linked()
        with as_owner():
            StaffDirectoryEntry.objects.filter(keycloak_sub=sub).delete()
        return sub

    def test_check_skips_a_hospital_held_by_a_restore(self):
        """T2: during a restore the schema is parked or not yet reconciled, so a row
        difference is no drift (its reconcile rebuilds it): no finding, no drift line, exit 0."""
        self.drop_row()
        with as_owner():
            release.restore_hold(self.hospital, uuid.uuid4())
        failed, out, err = self.run_command("--check", "--slug", self.hospital.slug)
        self.assertFalse(failed, out + err)
        self.assertNotIn("outcome=drift", err)
        self.assertIn("held by a restore, skipped", out)
        with as_owner():  # the hold lifted, the same difference is drift again
            release.lift_restore_hold(
                self.hospital, release.restore_holder(self.hospital)
            )
        failed, _out, err = self.run_command("--check", "--slug", self.hospital.slug)
        self.assertTrue(failed)
        self.assertIn("outcome=drift", err)

    def test_repair_of_a_restore_held_hospital_is_skipped_and_not_checked(self):
        sub = self.drop_row()
        with as_owner():
            release.restore_hold(self.hospital, uuid.uuid4())
            failed, out, err = self.run_command(
                "--repair", "--slug", self.hospital.slug
            )
        self.assertFalse(failed, out + err)  # a restore in progress is no exit 1
        self.assertNotIn("outcome=drift", err)
        self.assertEqual(listed(sub), set())  # untouched

    def test_repair_leaves_a_hospital_held_for_a_release_untouched(self):
        """C1: a hospital held for a release (or on another build's schema) gets no DDL and no
        rows from --repair: it is reported as skipped, and that is no failure."""
        sub = self.drop_row()
        with as_owner():
            release.hold([self.hospital.pk])
            failed, out, err = self.run_command(
                "--repair", "--slug", self.hospital.slug
            )
        self.assertFalse(failed, out + err)
        self.assertIn("skipped", out)
        self.assertEqual(listed(sub), set())
        with as_owner():
            release.release_hold([self.hospital.pk])
            release.record(self.hospital)
            failed, out, err = self.run_command(
                "--repair", "--slug", self.hospital.slug
            )
        self.assertFalse(failed, out + err)
        self.assertEqual(listed(sub), {TEST_SCHEMA})

    def test_repair_takes_the_hospitals_lock_and_reports_a_busy_one(self):
        """C1: another run working on the hospital (a migrate, a restore) is a per-hospital
        failure line and exit 1, never a traceback, and nothing is written."""
        sub = self.drop_row()
        with as_owner(), runner.exclusive(self.hospital.slug):
            failed, out, err = self.run_command(
                "--repair", "--slug", self.hospital.slug
            )
        self.assertTrue(failed)
        self.assertIn(
            f"ihr-directory: hospital={self.hospital.slug} outcome=failed",
            err.splitlines(),
        )
        self.assertEqual(listed(sub), set())
        self.assertNotIn("Traceback", out + err)

    def test_one_hospitals_failure_never_stops_the_others(self):
        """C1: each hospital catches its own failure; the rest are still repaired."""
        sub = self.drop_row()
        second = Hospital(
            slug=self.SECOND,
            name="Second",
            schema_name="h_dirsecond0",
            status=Hospital.Status.ACTIVE,
        )
        second.auto_create_schema = False
        connection.set_schema_to_public()
        with as_owner():
            RetiredAddress.objects.filter(value=self.SECOND).delete()
            second.save()
        connection.set_schema(TEST_SCHEMA)
        real = directory.ensure_directory

        def ensure(conn, schema):
            if schema == "h_dirsecond0":
                msg = "links kept changing"
                raise RuntimeError(msg)
            return real(conn, schema)

        with (
            as_owner(),
            mock.patch.object(directory, "ensure_directory", ensure),
            mock.patch.object(release, "schema_ready", return_value=True),
        ):
            failed, out, err = self.run_command("--repair")
        self.assertTrue(failed)
        self.assertIn(
            f"ihr-directory: hospital={self.SECOND} outcome=failed", err.splitlines()
        )
        self.assertEqual(listed(sub), {TEST_SCHEMA})  # the other hospital was repaired
        self.assertNotIn("Traceback", out + err)

    def test_one_of_check_or_repair_and_a_known_slug(self):
        for args in ((), ("--check", "--repair"), ("--check", "--slug", "no-such-one")):
            with self.subTest(args), self.assertRaises(CommandError):
                call_command("ihr_staff_directory", *args, stdout=StringIO())
            connection.set_schema(TEST_SCHEMA)

    def test_the_line_is_fixed(self):
        self.assertEqual(
            directory.log_line("alpha", "drift"),
            "ihr-directory: hospital=alpha outcome=drift",
        )
        with self.assertRaises(ValueError):
            directory.log_line("alpha", "anything")
