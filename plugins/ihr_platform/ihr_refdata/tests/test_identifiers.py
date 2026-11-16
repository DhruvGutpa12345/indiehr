"""The identifier_config kind in one hospital (P1-T15b2a; design.md §3.2, §3.5 and "After the
attack" items 7-11 and 13).

Test packs only (``ihr_refdata.testing.idc``, systems under https://ihr.test/id/): no real
identifier content ships. Every test runs in the shared test hospital inside a transaction that
is rolled back, except the advisory-lock test's second connection, which only holds a lock.
"""

import uuid
from io import StringIO
from unittest import mock

from care.emr.models.patient import (
    Patient,
    PatientIdentifier,
    PatientIdentifierConfig,
    PatientIdentifierConfigCache,
)
from care.facility.models import Facility
from care.users.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django_tenants.utils import get_public_schema_name
from ihr_platform import release as fleet_release
from ihr_platform.models import Hospital
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_platform.testing import as_owner, owner_for_test
from model_bakery import baker
from rest_framework.test import APIClient

from ihr_refdata import api, engine, identifiers, kinds
from ihr_refdata.manifest import PackError
from ihr_refdata.models import PackApply, PackItem
from ihr_refdata.testing import (
    TEST_PACK,
    TEST_SYSTEM,
    Packs,
    audit_mark,
    audit_since,
    idc,
)
from ihr_refdata.tests.test_api import REASON, URL, hospital_admin

SLUG = "testhosp"
KIND = identifiers.KIND


def system(name):
    return TEST_SYSTEM + name


def configs():
    """{system lower case: the live row (or the newest deleted one)}, ours only."""
    return identifiers.rows(
        {
            r.config["system"].lower()
            for r in PatientIdentifierConfig._base_manager.filter(
                config__system__istartswith=TEST_SYSTEM
            )
        }
    )


def record(name):
    return PackItem.objects.get(pack=TEST_PACK, item=system(name))


def by_hand(name, *, status="active", deleted=False, facility=None, **changes):
    """A config made outside the pack, as CARE's own API or shell would."""
    config = idc(name)["config"]
    config.update(changes)
    return PatientIdentifierConfig.objects.create(
        status=status, config=config, deleted=deleted, facility=facility
    )


def edit(row, **changes):
    row.config = {**row.config, **changes}
    row.save()
    return row


_PHONE = iter(range(10**6))


def patient_with(row, value):
    patient = Patient.objects.create(
        name="Pack Test Patient",
        gender="female",
        phone_number=f"+9198{next(_PHONE):08d}",
        year_of_birth=1990,
        blood_group="O_positive",
    )
    PatientIdentifier.objects.create(patient=patient, config=row, value=value)
    return patient


class IdentifierCase(TestCase):
    databases = {"default"}

    def setUp(self):
        owner_for_test(self)  # pack apply and decisions are owner-run (P1-T19a)
        Hospital.objects.exclude(
            schema_name__in=[get_public_schema_name(), TEST_SCHEMA]
        ).update(status=Hospital.Status.SUSPENDED)
        self.packs = Packs(self)

    def add(self, version, *items):
        self.packs.add(version, *items, kind=KIND)

    def apply(self):
        out, err = StringIO(), StringIO()
        call_command(
            "ihr_pack_apply", "--slug", SLUG, "--operator", "tester",
            stdout=out, stderr=err,
        )  # fmt: skip
        return out.getvalue(), err.getvalue()

    def pending_cmd(self, *args):
        out, err = StringIO(), StringIO()
        call_command("ihr_pack_pending", "--slug", SLUG, *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def decide(self, flag, name):
        return self.pending_cmd(
            flag, f"{TEST_PACK}:{system(name)}", "--operator", "tester",
            "--reason", "hospital admin OK, ticket 7",
        )  # fmt: skip

    def state(self, name):
        r = record(name)
        return r.state, r.reason

    def apply_skipped(self):
        """An apply the start-up short-circuit answers: no reconcile at all (item 11, Q6)."""
        with mock.patch.object(engine, "_reconcile", wraps=engine._reconcile) as run:
            out, err = self.apply()
        run.assert_not_called()
        self.assertIn("unchanged", out)
        return out, err


class RuleTests(IdentifierCase):
    def test_a_new_config_is_created_and_a_re_apply_writes_and_audits_nothing(self):
        self.add(1, idc("mrn"), idc("uhid", unique=True, regex=r"^U[0-9]+$"))
        out, _ = self.apply()
        self.assertIn("2 created", out)
        rows = configs()
        mrn = rows[system("mrn")]
        self.assertEqual(
            (mrn.status, mrn.facility_id, mrn.deleted), ("active", None, False)
        )
        self.assertFalse(mrn.config["auto_maintained"])  # CARE's defaults filled in
        self.assertEqual(self.state("mrn"), ("current", ""))
        self.assertEqual(self.state("uhid"), ("current", ""))
        since = audit_mark()
        self.apply_skipped()
        out, _ = self.pending_cmd(
            "--recheck", "--operator", "tester"
        )  # a full reconcile
        self.assertIn("rechecked", out)
        self.assertEqual(audit_since(since), [])

    def test_each_rule_row(self):
        self.add(1, idc("a"), idc("b"), idc("c"))
        self.apply()
        edit(configs()[system("b")], display="Ours")
        by_hand("u", display="Their own U")
        self.add(
            2, idc("a", display="A2"), idc("b", display="B2"), idc("c"),
            idc("u", display="Pack U"),
        )  # fmt: skip
        out, _ = self.apply()
        self.assertIn("1 updated", out)
        rows = configs()
        self.assertEqual(rows[system("a")].config["display"], "A2")
        self.assertEqual(self.state("a"), ("current", ""))
        self.assertEqual(rows[system("b")].config["display"], "Ours")
        self.assertEqual(self.state("b"), ("pending", "edited"))
        self.assertEqual(self.state("c"), ("current", ""))
        self.assertEqual(self.state("u"), ("pending", "unknown-origin"))
        self.decide("--keep", "b")
        self.assertEqual(self.state("b"), ("kept", "edited"))
        self.decide("--accept", "u")
        self.assertEqual(configs()[system("u")].config["display"], "Pack U")
        self.assertEqual(self.state("u"), ("current", ""))
        # a local edit the pack has nothing new for stays the hospital's own
        edit(configs()[system("a")], display="Our A")
        self.add(3, idc("a", display="A2"), idc("b", display="B2"), idc("c", display="C3"),
                 idc("u", display="Pack U"))  # fmt: skip
        out, _ = self.apply()
        self.assertIn("1 local-edit", out)
        self.assertEqual(configs()[system("a")].config["display"], "Our A")
        self.assertEqual(self.state("b"), ("kept", "edited"))

    def test_the_same_meaning_spelt_differently_is_adopted_and_the_system_kept(self):
        row = by_hand("s")
        row.config = {k: v for k, v in row.config.items() if k != "description"}
        row.config["system"] = system(
            "s"
        ).upper()  # another case: the same key (item 13)
        row.save()
        self.add(1, idc("s"))
        out, _ = self.apply()
        self.assertIn("1 adopted", out)
        self.assertEqual(self.state("s"), ("current", ""))
        self.add(2, idc("s", display="S2"))
        out, _ = self.apply()
        self.assertIn("1 updated", out)
        row.refresh_from_db()
        self.assertEqual(row.config["display"], "S2")
        self.assertEqual(row.config["system"], system("s").upper())  # never changed

    def test_a_removal_makes_the_row_inactive_and_old_values_resolve(self):
        self.add(1, idc("a"), idc("b"))
        self.apply()
        b = configs()[system("b")]
        patient_with(b, "B-1")
        self.add(2, idc("a"))
        out, _ = self.apply()
        self.assertIn("1 retired", out)
        b.refresh_from_db()
        self.assertEqual((b.status, b.deleted), ("inactive", False))
        self.assertEqual(self.state("b"), ("retired", ""))
        seen = PatientIdentifierConfigCache.get_config(b.external_id)
        self.assertEqual(seen["status"], "inactive")

    def test_a_deleted_row_waits_only_when_the_pack_changes_it_and_accept_restores(
        self,
    ):
        self.add(1, idc("a"), idc("b"))
        self.apply()
        configs()[system("a")].delete()  # CARE's soft delete
        before = record("a").changed_at
        since = audit_mark()
        self.apply_skipped()  # same release: the deletion is the hospital's own
        self.assertEqual(audit_since(since), [])
        self.add(2, idc("a"), idc("b", display="B2"))
        out, _ = self.apply()  # a full reconcile: still nothing for a
        self.assertIn("1 local-edit", out)
        self.assertEqual(self.state("a"), ("current", ""))
        self.assertEqual(record("a").changed_at, before)
        self.assertTrue(configs()[system("a")].deleted)
        self.add(3, idc("a", display="A3"), idc("b", display="B2"))
        self.apply()
        self.assertEqual(self.state("a"), ("pending", "deleted"))
        self.assertTrue(configs()[system("a")].deleted)
        self.apply_skipped()  # waits on the admin only: the start-up short-circuit holds
        self.decide("--accept", "a")
        a = configs()[system("a")]
        self.assertEqual((a.deleted, a.config["display"]), (False, "A3"))
        self.assertEqual(self.state("a"), ("current", ""))

    def test_a_kept_deletion_stays_kept(self):
        self.add(1, idc("a"))
        self.apply()
        configs()[system("a")].delete()
        self.add(2, idc("a", display="A2"))
        self.apply()
        self.decide("--keep", "a")
        self.add(3, idc("a", display="A2"), idc("z"))
        self.apply()
        self.assertEqual(self.state("a"), ("kept", "deleted"))
        self.assertTrue(configs()[system("a")].deleted)

    def test_a_default_value_change_applies_automatically(self):
        self.add(1, idc("n", auto_maintained=True, default_value="patient_count + 1"))
        self.apply()
        self.add(
            2, idc("n", auto_maintained=True, default_value="patient_count + 1000")
        )
        out, _ = self.apply()
        self.assertIn("1 updated", out)
        self.assertEqual(
            configs()[system("n")].config["default_value"], "patient_count + 1000"
        )


class RecheckTests(IdentifierCase):
    def test_recheck_takes_only_the_hospitals_an_apply_would_and_records_a_failure(
        self,
    ):
        """Review Q2: the recheck writes, so it uses ihr_pack_apply's selection and is
        recorded and alerted on exactly as an apply."""
        self.add(1, idc("a"))
        self.apply()
        hospital = Hospital.objects.get(slug=SLUG)
        Hospital.objects.filter(pk=hospital.pk).update(status=Hospital.Status.SUSPENDED)
        with self.assertRaisesMessage(CommandError, "only live hospitals"):
            self.pending_cmd("--recheck", "--operator", "tester")
        Hospital.objects.filter(pk=hospital.pk).update(status=Hospital.Status.ACTIVE)
        state = fleet_release._state_model().objects.filter(pk=hospital.pk)
        state.update(restore_operation=uuid.uuid4())
        with self.assertRaisesMessage(CommandError, "held for a restore"):
            self.pending_cmd("--recheck", "--operator", "tester")
        state.update(restore_operation=None)
        err = StringIO()
        with (
            mock.patch.object(engine, "_reconcile", side_effect=RuntimeError("boom")),
            self.assertRaises(CommandError),
        ):
            call_command(
                "ihr_pack_pending", "--slug", SLUG, "--recheck", "--operator", "tester",
                stdout=StringIO(), stderr=err,
            )  # fmt: skip
        self.assertIn(f"ihr-packs: hospital={SLUG} outcome=failed", err.getvalue())
        failed = PackApply.objects.filter(pack=TEST_PACK, outcome="failed").last()
        self.assertIn("boom", failed.error)


class LookupAndConflictTests(IdentifierCase):
    def test_the_live_row_is_preferred_over_a_deleted_one(self):
        old_live = by_hand("p", display="Live")
        by_hand("p", display="Deleted later", deleted=True)  # newer, but deleted
        self.add(1, idc("p", display="Live"))
        out, _ = self.apply()
        self.assertIn("1 adopted", out)
        self.assertEqual(configs()[system("p")].pk, old_live.pk)

    def test_two_live_rows_with_the_system_are_a_conflict(self):
        self.add(1, idc("d"))
        self.apply()
        twin = by_hand("d")  # as CARE's shell or a race could leave it
        self.add(2, idc("d", display="D2"))
        self.apply()
        self.assertEqual(self.state("d"), ("pending", "conflict"))
        self.assertIn("2 live", record("d").checks["conflict"])
        self.assertEqual(
            {r.config["display"] for r in PatientIdentifierConfig.objects.filter(
                config__system=system("d"))}, {"D"},
        )  # fmt: skip
        with self.assertRaisesMessage(CommandError, "conflict"):
            self.decide("--accept", "d")
        twin.delete()
        out, _ = self.apply()  # same release: a conflict is reconciled again
        self.assertIn("1 updated", out)
        self.assertEqual(self.state("d"), ("current", ""))

    def test_a_facility_row_with_the_system_is_a_conflict(self):
        user = User.objects.create_user(
            username="pk-idc-facility", password="x-Unused-1"
        )
        facility = baker.make(Facility, name="Pack Conflict Facility", created_by=user)
        own = by_hand("f", facility=facility)
        self.add(1, idc("f"))
        self.apply()
        self.assertEqual(self.state("f"), ("pending", "conflict"))
        self.assertFalse(
            PatientIdentifierConfig.objects.filter(
                config__system=system("f"), facility__isnull=True
            ).exists()
        )
        self.decide("--keep", "f")  # keep stays allowed
        self.assertEqual(self.state("f"), ("kept", "conflict"))
        own.delete()
        self.add(2, idc("f", display="F2"))
        out, _ = self.apply()
        self.assertIn("1 created", out)

    def test_a_kept_conflict_can_be_accepted_once_it_is_fixed(self):
        """Review E5: a kept item is asked afresh at its accept, never refused for good."""
        user = User.objects.create_user(username="pk-idc-kept", password="x-Unused-1")
        facility = baker.make(Facility, name="Pack Kept Facility", created_by=user)
        own = by_hand("k", facility=facility)
        self.add(1, idc("k"))
        self.apply()
        self.decide("--keep", "k")
        self.assertEqual(self.state("k"), ("kept", "conflict"))
        with self.assertRaisesMessage(CommandError, "conflict"):  # still there: refused
            self.decide("--accept", "k")
        self.apply()  # a kept create stays kept (E1)
        self.assertEqual(self.state("k"), ("kept", "conflict"))
        own.delete()
        self.decide("--accept", "k")
        self.assertEqual(self.state("k"), ("current", ""))
        self.assertEqual(configs()[system("k")].facility_id, None)

    def test_a_newer_duplicate_with_other_content_stays_a_conflict(self):
        """Review E2: the conflict found after commit is asked again at the next apply, even
        when the newest live row (CARE's) differs from the pack."""
        self.add(1, idc("n"))
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.apply()
        by_hand("n", display="Theirs")  # CARE's create, racing ours, committed too
        for callback in callbacks:
            callback()
        self.assertEqual(self.state("n"), ("pending", "conflict"))
        self.apply()
        self.assertEqual(self.state("n"), ("pending", "conflict"))

    def test_two_live_rows_are_a_conflict_even_when_the_newest_holds_the_pack(self):
        """Review E2: a first apply never adopts one of two live rows."""
        by_hand("q", display="Old")
        by_hand("q")  # newer, and what the pack holds
        self.add(1, idc("q"))
        self.apply()
        self.assertEqual(self.state("q"), ("pending", "conflict"))

    def test_an_edit_committed_while_the_apply_waits_is_never_overwritten(self):
        """Review E3: the row is read again under its lock before it is written."""
        self.add(1, idc("e"))
        self.apply()
        row = configs()[system("e")]
        self.add(2, idc("e", display="E2"))

        def hospital_edits(key):  # committed by CARE's API while the apply waited
            PatientIdentifierConfig.objects.filter(pk=row.pk).update(
                config={**row.config, "display": "Theirs"}
            )

        with mock.patch.object(identifiers, "lock", side_effect=hospital_edits):
            self.apply()
        row.refresh_from_db()
        self.assertEqual(row.config["display"], "Theirs")
        self.assertEqual(self.state("e"), ("pending", "edited"))

    def test_the_duplicate_race_is_serialised_and_found_after_commit(self):
        """Item 9: our writers of one system wait on an advisory lock; a duplicate made by a
        writer that does not take it (CARE's own) is found after commit."""
        self.add(1, idc("r"))
        other = connections.create_connection("default")
        try:
            with other.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_lock(hashtext(%s || %s))",
                    [connection.schema_name, system("r")],
                )
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '300ms'")
            err = StringIO()
            try:
                with self.assertRaises(CommandError):  # one hospital failed
                    call_command(
                        "ihr_pack_apply", "--slug", SLUG, "--operator", "tester",
                        stdout=StringIO(), stderr=err,
                    )  # fmt: skip
            finally:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = 0")
            self.assertIn("outcome=failed", err.getvalue())
            failed = PackApply.objects.filter(pack=TEST_PACK, outcome="failed").last()
            self.assertIn("lock timeout", failed.error)
            self.assertFalse(configs())
        finally:
            other.close()  # ends its session and the lock with it
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            out, _ = self.apply()
        self.assertIn("1 created", out)
        self.assertTrue(callbacks)
        self.assertEqual(self.state("r"), ("current", ""))
        by_hand("r")  # CARE's create, racing ours, committed too
        for callback in callbacks:
            callback()
        self.assertEqual(self.state("r"), ("pending", "conflict"))

    def test_malformed_items_and_cares_own_systems_are_refused(self):
        good = idc("ok")
        bad = [
            {**good, "slug": good["slug"] + "-other"},  # slug is not the system
            {**good, "status": "retired"},
            {**good, "extra": 1},
            {**good, "config": {**good["config"], "colour": "red"}},
            {**good, "config": {**good["config"], "regex": "("}},
            {**good, "config": {**good["config"], "default_value": "import os"}},
            {**idc("x"), "slug": "system.care.ohc.network/patient-name",
             "config": {**idc("x")["config"], "system": "system.care.ohc.network/patient-name"}},
        ]  # fmt: skip
        for item in bad:
            with (
                self.subTest(item=item),
                self.assertRaises((KeyError, TypeError, ValueError)),
            ):
                identifiers.check_item(item)
        upper = idc("ok")
        upper["slug"] = upper["config"]["system"] = TEST_SYSTEM + "OK"
        self.add(1, upper)
        with self.assertRaisesMessage(PackError, "lower case"):
            engine.prepare("tester")


class GateTests(IdentifierCase):
    """Changes only the admin may take (items 7 and 10) and data checks (item 8)."""

    def test_loosening_who_finds_a_patient_waits_for_the_admin(self):
        self.add(
            1, idc("strict"), idc("draft", status="draft", retrieve_config={
                "retrieve_with_year_of_birth": False}),
            idc("tight"),
        )  # fmt: skip
        self.apply()
        self.add(
            2,
            idc("partial", retrieve_config={"retrieve_partial_search": True}),
            idc("strict", retrieve_config={"retrieve_with_year_of_birth": False}),
            idc("draft", retrieve_config={"retrieve_with_year_of_birth": False}),
            idc("tight", retrieve_config={"retrieve_with_otp": True}),
        )
        self.apply()
        self.assertEqual(self.state("partial"), ("pending", "privacy"))
        # a new loose config: not created
        self.assertNotIn(system("partial"), configs())
        self.assertEqual(self.state("strict"), ("pending", "privacy"))
        strict = configs()[system("strict")]
        self.assertTrue(strict.config["retrieve_config"]["retrieve_with_year_of_birth"])
        self.assertEqual(
            self.state("draft"), ("pending", "privacy")
        )  # turns active, loose
        self.assertEqual(configs()[system("draft")].status, "draft")
        self.assertEqual(self.state("tight"), ("current", ""))  # tightening: automatic
        self.assertTrue(
            configs()[system("tight")].config["retrieve_config"]["retrieve_with_otp"]
        )
        self.apply_skipped()  # privacy waits on the admin: no reconcile (Q6)
        self.decide("--accept", "partial")
        self.assertEqual(configs()[system("partial")].status, "active")
        self.assertEqual(self.state("partial"), ("current", ""))

    def test_every_loosening_waits_for_the_admin(self):
        """Item 7 (reviews C1, C2, Q7): each way of widening who finds a patient."""
        no_year = {"retrieve_with_year_of_birth": False}
        self.add(
            1, idc("p1"), idc("dob1", retrieve_config={"retrieve_with_dob": True}),
            idc("otp1", retrieve_config={"retrieve_with_otp": True}), idc("ed"),
            idc("inact", status="inactive"), idc("del"),
        )  # fmt: skip
        self.apply()
        patient_with(configs()[system("inact")], "I-1")
        edit(configs()[system("ed")], display="Ours")
        configs()[system("del")].delete()
        self.add(
            2,
            idc("p1", retrieve_config={"retrieve_partial_search": True}),
            idc("dob1"),  # the date of birth no longer asked
            idc("otp1"),  # the OTP no longer asked
            idc("ed", retrieve_config=no_year),  # an edited row: privacy, not "edited"
            # CARE's search finds a patient through an inactive config too (C2)
            idc("inact", status="inactive", retrieve_config=no_year),
            # a deleted row: privacy, not "deleted"
            idc("del", retrieve_config=no_year),
            idc("none", retrieve_config=no_year),  # a new config with no check
            # CARE's server never checks the date of birth (C1)
            idc("dob", retrieve_config={**no_year, "retrieve_with_dob": True}),
        )
        self.apply()
        for name in ("p1", "dob1", "otp1", "ed", "inact", "del", "none", "dob"):
            with self.subTest(name=name):
                self.assertEqual(self.state(name), ("pending", "privacy"))
        self.assertNotIn(system("none"), configs())
        self.assertNotIn(system("dob"), configs())
        inact = configs()[system("inact")]
        self.assertTrue(inact.config["retrieve_config"]["retrieve_with_year_of_birth"])

    def test_a_new_number_series_replacing_one_with_values_waits(self):
        self.add(1, idc("old", auto_maintained=True))
        self.apply()
        old = configs()[system("old")]
        patient_with(old, "1")
        patient_with(old, "2")
        self.add(2, idc("new", auto_maintained=True))
        self.apply()
        self.assertEqual(configs()[system("old")].status, "inactive")
        self.assertEqual(self.state("new"), ("pending", "counter-restart"))
        self.assertEqual(record("new").checks, {"counts": {"old_values": 2}})
        self.assertNotIn(system("new"), configs())
        # E1: the hold stands at later applies (the old series is retired by now)
        self.apply_skipped()
        self.pending_cmd("--recheck", "--operator", "tester")  # a full reconcile
        self.assertEqual(self.state("new"), ("pending", "counter-restart"))
        self.assertNotIn(system("new"), configs())
        self.decide("--accept", "new")
        self.assertEqual(configs()[system("new")].status, "active")
        self.assertEqual(record("new").checks, {})

    def test_a_kept_number_series_is_never_created_unasked(self):
        """Review E1: keep holds for a create too, through a full reconcile."""
        self.add(1, idc("old", auto_maintained=True))
        self.apply()
        patient_with(configs()[system("old")], "1")
        self.add(2, idc("new", auto_maintained=True))
        self.apply()
        self.decide("--keep", "new")
        self.pending_cmd("--recheck", "--operator", "tester")
        self.assertEqual(self.state("new"), ("kept", "counter-restart"))
        self.assertNotIn(system("new"), configs())

    def test_only_the_rule_in_force_is_checked(self):
        """Review E7: CARE validates values against active configs only."""
        self.add(
            1, idc("inq", status="inactive"),
            idc("act", status="inactive", unique=True),
        )  # fmt: skip
        self.apply()
        for name in ("inq", "act"):
            row = configs()[system(name)]
            patient_with(row, "R-1")
            patient_with(row, "R-1")
        self.add(
            2, idc("inq", status="inactive", unique=True),  # no edit can fail
            idc("act", unique=True),  # made active: every repeat now fails edits
        )  # fmt: skip
        self.apply()
        self.assertEqual(self.state("inq"), ("current", ""))
        self.assertEqual(self.state("act"), ("pending", "data-check"))
        self.assertEqual(record("act").checks["counts"], {"repeated": 2})

    def test_an_accept_reuses_a_check_that_still_holds(self):
        """Reviews E4 and E6: a check that passed is kept with the hold, an accept reuses it
        while the data have not moved, and the row is locked FOR NO KEY UPDATE (CARE's
        identifier inserts take FOR KEY SHARE through their foreign key: never blocked)."""
        self.add(1, idc("acc"))
        self.apply()
        row = configs()[system("acc")]
        patient_with(row, "A-1")
        patient_with(row, "A-2")
        edit(row, display="Ours")
        self.add(2, idc("acc", unique=True))
        self.apply()
        self.assertEqual(self.state("acc"), ("pending", "edited"))
        checks = record("acc").checks
        self.assertEqual(
            (checks["counts"], checks["patients"], checks["complete"]),
            ({"repeated": 0}, 0, True),
        )
        with (
            mock.patch.object(identifiers, "run_data_checks") as run,
            CaptureQueriesContext(connection) as queries,
        ):
            self.decide("--accept", "acc")
        run.assert_not_called()
        locks = [
            q["sql"]
            for q in queries.captured_queries
            if "emr_patientidentifierconfig" in q["sql"] and " FOR " in q["sql"]
        ]
        self.assertTrue(locks)
        for sql in locks:
            self.assertIn("FOR NO KEY UPDATE", sql)
        row.refresh_from_db()
        self.assertTrue(row.config["unique"])

    def test_an_accept_over_the_cap_is_stored_and_alerted(self):
        """Review Q9: the accept's own check, when it cannot finish, is kept on the item and
        the next apply writes the alerted line."""
        self.add(1, idc("cap"))
        self.apply()
        row = configs()[system("cap")]
        patient_with(row, "C1")
        edit(row, display="Ours")
        self.add(2, idc("cap", regex=r"^C[0-9]$"))
        self.apply()
        self.assertEqual(self.state("cap"), ("pending", "edited"))
        patient_with(row, "C2")  # the data moved: the accept checks again
        with (
            mock.patch.object(identifiers, "REGEX_SECONDS", 0),
            self.assertLogs("ihr.refdata", "WARNING") as logs,
            self.assertRaisesMessage(CommandError, "could not be fully checked"),
        ):
            self.decide("--accept", "cap")
        self.assertTrue(any("at an accept" in line for line in logs.output))
        self.assertEqual(self.state("cap"), ("pending", "data-check"))
        self.assertFalse(record("cap").checks["complete"])
        _, err = self.apply()
        self.assertIn(f"ihr-packs: hospital={SLUG} outcome=failed", err.splitlines())

    def _unique_with_repeats(self):
        self.add(1, idc("card"))
        self.apply()
        row = configs()[system("card")]
        patient_with(row, "C-1")
        twin = patient_with(row, "C-1")
        patient_with(row, "C-2")
        self.add(2, idc("card", unique=True))
        return row, twin

    def test_unique_with_repeated_values_is_held_and_stored(self):
        row, twin = self._unique_with_repeats()
        _, err = self.apply()
        self.assertNotIn("outcome=failed", err)
        self.assertEqual(self.state("card"), ("pending", "data-check"))
        checks = record("card").checks
        self.assertEqual((checks["counts"], checks["patients"]), ({"repeated": 2}, 2))
        self.assertTrue(checks["complete"])
        self.assertNotIn("C-1", str(checks))  # counts only, never a value
        row.refresh_from_db()
        self.assertFalse(row.config["unique"])
        with self.assertRaisesMessage(CommandError, "data-check"):
            self.decide("--accept", "card")
        listed, _ = self.pending_cmd()  # Q4: what holds it, counts only
        self.assertIn("(data-check)", listed)
        self.assertIn("repeated 2; 2 patients; fully checked; checked ", listed)
        self.assertNotIn("C-1", listed)
        # nothing moved: the stored counts stand, no check runs, nothing is written
        since = audit_mark()
        with mock.patch.object(identifiers, "run_data_checks") as run:
            self.apply()
        run.assert_not_called()
        self.assertEqual(audit_since(since), [])
        # the hospital fixes its data: the watermark moves, the check runs, the change is taken
        PatientIdentifier.objects.filter(patient=twin).update(value="C-3")
        PatientIdentifier.objects.filter(patient=twin).first().save()
        out, _ = self.apply()
        self.assertIn("1 updated", out)
        self.assertEqual(self.state("card"), ("current", ""))
        self.assertEqual(record("card").checks, {})
        row.refresh_from_db()
        self.assertTrue(row.config["unique"])

    def test_keep_is_allowed_and_recheck_forces_the_check(self):
        self._unique_with_repeats()
        self.apply()
        with mock.patch.object(
            identifiers, "run_data_checks", wraps=identifiers.run_data_checks
        ) as run:
            self.pending_cmd("--recheck", "--operator", "tester")
        run.assert_called_once()
        self.decide("--keep", "card")
        self.assertEqual(self.state("card"), ("kept", "data-check"))

    def test_a_changed_regex_with_values_that_no_longer_match_is_held(self):
        self.add(1, idc("ward", regex=r"^W"))
        self.apply()
        row = configs()[system("ward")]
        patient_with(row, "W1")
        patient_with(row, "X1")  # matches neither
        patient_with(row, "W-2")
        self.add(2, idc("ward", regex=r"^W[0-9]+$"))
        self.apply()
        self.assertEqual(self.state("ward"), ("pending", "data-check"))
        self.assertEqual(record("ward").checks["counts"], {"not_matching": 2})

    def test_a_check_over_its_cap_is_held_not_fully_checked_and_alerted(self):
        self.add(1, idc("slow"))
        self.apply()
        patient_with(configs()[system("slow")], "S1")
        self.add(2, idc("slow", regex=r"^S[0-9]$"))
        with mock.patch.object(identifiers, "REGEX_SECONDS", 0):
            _, err = self.apply()
        self.assertIn(f"ihr-packs: hospital={SLUG} outcome=failed", err.splitlines())
        self.assertIn(
            f"pack={TEST_PACK} item={system('slow')} data check not fully", err
        )
        self.assertEqual(self.state("slow"), ("pending", "data-check"))
        self.assertFalse(record("slow").checks["complete"])
        listed, _ = self.pending_cmd()
        self.assertIn("NOT fully checked", listed)
        # Q3: alerted at every apply while the stored result is not fully checked
        _, err = self.apply()
        self.assertIn(f"ihr-packs: hospital={SLUG} outcome=failed", err.splitlines())
        _, err = self.pending_cmd("--recheck", "--operator", "tester")
        self.assertNotIn("outcome=failed", err)
        self.assertEqual(self.state("slow"), ("current", ""))

    def test_the_repeat_query_has_a_statement_timeout(self):
        self.add(1, idc("t"))
        self.apply()
        patient_with(configs()[system("t")], "T1")
        self.add(2, idc("t", unique=True))
        slow = "SELECT 1 FROM pg_sleep(1) WHERE %s > 0 AND %s > 0"
        with (
            mock.patch.object(identifiers, "REPEAT_SQL", slow),
            mock.patch.object(identifiers, "REPEAT_TIMEOUT_MS", 50),
        ):
            _, err = self.apply()
        self.assertIn(f"ihr-packs: hospital={SLUG} outcome=failed", err.splitlines())
        self.assertFalse(record("t").checks["complete"])
        with connection.cursor() as cursor:  # the cap did not outlive the check
            cursor.execute("SHOW statement_timeout")
            self.assertNotEqual(cursor.fetchone()[0], "50ms")


class ApiTests(TestCase):
    """The b1 decision API answers for an identifier item: blocked for a data check, the
    stored counts and their age (never computed in a GET, never a value), the two fields."""

    databases = {"default"}

    def setUp(self):
        self.packs = Packs(self)
        with as_owner():
            self.packs.add(1, idc("card"), kind=KIND)
            self.apply()
            row = configs()[system("card")]
            for value in ("C-1", "C-1", "C-2"):
                patient_with(row, value)
            self.packs.add(2, idc("card", unique=True), kind=KIND)
            self.apply()
        self.api = APIClient(HTTP_HOST="testserver")
        self.api.force_authenticate(hospital_admin(f"pk-idc-{id(self)}"))
        self.record = record("card")

    def apply(self):
        call_command(
            "ihr_pack_apply", "--slug", SLUG, "--operator", "tester",
            stdout=StringIO(), stderr=StringIO(),
        )  # fmt: skip

    def test_the_api_answers_for_an_identifier_item(self):
        listed = self.api.get(f"{URL}updates/?kind={KIND}")
        self.assertEqual(listed.status_code, 200, listed.content)
        (entry,) = listed.json()["results"]
        self.assertEqual((entry["label"], entry["reason"]), ("CARD", "data-check"))
        with mock.patch.object(identifiers, "run_data_checks") as run:
            answer = self.api.get(f"{URL}updates/{self.record.pk}/")
        run.assert_not_called()  # a GET shows what is stored
        self.assertEqual(answer.status_code, 200)
        body, raw = answer.json(), answer.content.decode()
        self.assertEqual([f["field"] for f in body["fields"]], ["status", "config"])
        self.assertEqual((body["blocked"], body["effect"]), ("data-check", "update"))
        held = body["conflicts"]
        self.assertEqual(
            (held["counts"], held["patients"], held["complete"]),
            ({"repeated": 2}, 2, True),
        )
        self.assertEqual(held["note"], "2 patients' edits would fail until fixed")
        self.assertGreaterEqual(held["age_seconds"], 0)
        self.assertIn("checked_at", held)
        for internal in ("watermark", "sha256"):
            self.assertNotIn(internal, held)
        self.assertNotIn("C-1", raw)
        (config,) = [f for f in body["fields"] if f["field"] == "config"]
        (unique,) = config["changes"]
        self.assertEqual(
            (unique["path"], unique["hospital"], unique["pack"], unique["previous"]),
            ("$.unique", False, True, False),
        )
        decision = {
            "version": body["pending_version"],
            "sha256": body["pending_sha256"],
            "row_sha256": body["row_sha256"],
            "reason": REASON,
        }
        refused = self.api.post(
            f"{URL}updates/{self.record.pk}/", {"action": "accept", **decision},
            format="json",
        )  # fmt: skip
        self.assertEqual(refused.status_code, 409)
        self.assertTrue(refused.json()["blocked"])
        kept = self.api.post(
            f"{URL}updates/{self.record.pk}/", {"action": "keep", **decision},
            format="json",
        )  # fmt: skip
        self.assertEqual(kept.status_code, 200, kept.content)
        self.assertEqual(kept.json()["state"], "kept")
        # E5: a kept item is asked afresh at its accept; the repeats still stand
        again = self.api.get(f"{URL}updates/{self.record.pk}/").json()
        self.assertEqual(again["blocked"], "")
        refused = self.api.post(
            f"{URL}updates/{self.record.pk}/", {"action": "accept", **decision},
            format="json",
        )  # fmt: skip
        self.assertEqual(refused.status_code, 409, refused.content)
        self.assertIn("2 patients", refused.json()["detail"])

    def test_the_in_flight_mark_outlasts_the_longest_decision(self):
        """Review E4: an accept may run a data check to its caps."""
        longest = identifiers.REPEAT_TIMEOUT_MS / 1000 + identifiers.REGEX_SECONDS
        self.assertGreater(api.IN_FLIGHT_SECONDS, longest + 30)


class RegistryTests(TestCase):
    def test_the_engine_reaches_identifier_configs_through_the_registry(self):
        self.assertIs(kinds.get(KIND).module, identifiers)
        with as_owner():
            self.assertTrue(identifiers.all_present([]))
