"""The placement trigger (P1-T11b1; design-b.md §5.1, "After the attack" §4, §10 test 2)."""

import datetime
import importlib

from care.emr.models import PatientUser
from care.emr.models.organization import (
    FacilityOrganizationUser,
    Organization,
    OrganizationUser,
)
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_platform.testing import as_owner
from ihr_roles import registry
from psycopg import sql

from ihr_access import placement
from ihr_access.models import RecordsGrant
from ihr_access.testing import build_world, move_to, nurse_on_care_team


class PlacementCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.world = build_world()

    def assert_refused(self, write, needle="ihr_role_placement"):
        with self.assertRaises(IntegrityError) as refused, transaction.atomic():
            write()
        self.assertIn(needle, str(refused.exception))

    def facility_member(self, user, role, organization):
        return FacilityOrganizationUser.objects.create(
            organization=organization, user=user, role=self.world.role(role)
        )


class OrganisationRules(PlacementCase):
    def test_the_trigger_is_installed(self):
        with connection.cursor() as cursor:
            self.assertEqual(placement.problems(cursor, TEST_SCHEMA), [])

    def test_a_trigger_re_created_with_fewer_columns_is_drift_and_repaired(self):
        """A changed trigger is reported, and ensure() puts this build's definition back; it
        never stamps the old one as current (review C2)."""
        table = sql.Identifier(TEST_SCHEMA, "emr_patientuser")
        with as_owner(), connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "CREATE OR REPLACE TRIGGER ihr_role_placement_guard BEFORE INSERT OR "
                    "UPDATE OF role_id ON {} FOR EACH ROW EXECUTE FUNCTION {}()"
                ).format(table, sql.Identifier(TEST_SCHEMA, placement.FUNCTION))
            )
            found = placement.problems(cursor, TEST_SCHEMA)
            self.assertEqual(len(found), 1, found)
            self.assertIn("emr_patientuser", found[0])
            self.assertIn("not this build's", found[0])
            # The test world's rows left deferred trigger events; a migrate has none
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            self.assertTrue(placement.ensure(connection, TEST_SCHEMA))
            self.assertEqual(placement.problems(cursor, TEST_SCHEMA), [])
            columns = placement._triggers(cursor, TEST_SCHEMA)["emr_patientuser"][3]
        self.assertEqual(columns, ["deleted", "patient_id", "role_id", "user_id"])

    def test_hidden_role_is_refused_in_any_organisation(self):
        w = self.world
        user = w.user("acc-hidden")
        self.assert_refused(lambda: self.facility_member(user, registry.HIDDEN, w.root))
        geo = Organization.objects.create(name="Some district", org_type="govt")
        self.assert_refused(
            lambda: OrganizationUser.objects.create(
                organization=geo, user=user, role=w.role(registry.HIDDEN)
            )
        )

    def test_our_roles_are_refused_in_government_and_role_organisations(self):
        w = self.world
        user = w.user("acc-geo")
        for org_type in ("govt", "role"):
            organization = Organization.objects.create(
                name=f"Org {org_type}", org_type=org_type
            )
            for name in registry.EIGHT:
                with self.subTest(org_type=org_type, role=name):
                    self.assert_refused(
                        lambda organization=organization, name=name: (
                            OrganizationUser.objects.create(
                                organization=organization, user=user, role=w.role(name)
                            )
                        )
                    )
        # CARE's own roles there are untouched
        OrganizationUser.objects.create(
            organization=organization, user=user, role=w.role("Doctor")
        )

    def test_radiologist_refused_at_root(self):
        w = self.world
        user = w.user("acc-root")
        for name in sorted(registry.DEPARTMENT_ONLY):
            with self.subTest(role=name):
                self.assert_refused(
                    lambda name=name: self.facility_member(user, name, w.root)
                )
                self.facility_member(
                    user, name, w.departments["Radiology"]
                )  # a department: fine
        # the front office may sit at the root
        for name in ("Receptionist", "Cashier", "TPA Desk", "Owner", "MRD"):
            self.facility_member(w.user("acc-front"), name, w.root)

    def test_an_update_into_a_refused_place_is_refused(self):
        w = self.world
        member = self.facility_member(
            w.user("acc-move"), "Radiologist", w.departments["Radiology"]
        )
        member.organization = w.root
        self.assert_refused(member.save)

    def test_soft_deleting_a_membership_always_passes(self):
        w = self.world
        member = self.facility_member(
            w.user("acc-soft"), "Lab Technician", w.departments["Laboratory"]
        )
        member.deleted = True
        member.save()


class PatientUserRules(PlacementCase):
    def grant(self, user, minutes=30):
        return RecordsGrant.objects.create(
            patient=self.world.patient,
            user=user,
            reason="legal",
            reference="CASE-1",
            expires_at=timezone.now() + datetime.timedelta(minutes=minutes),
        )

    def hidden_row(self, user):
        return PatientUser.objects.create(
            user=user, patient=self.world.patient, role=self.world.role(registry.HIDDEN)
        )

    def test_patient_user_with_records_role_needs_a_grant(self):
        mrd = self.world.users["MRD"]
        self.assert_refused(lambda: self.hidden_row(mrd), "needs a live records grant")
        expired = self.grant(mrd, minutes=-1)
        self.assert_refused(lambda: self.hidden_row(mrd), "needs a live records grant")
        expired.ended_at = timezone.now()
        expired.save()
        live = self.grant(mrd)
        row = self.hidden_row(mrd)
        # an ended grant: an update that keeps the row live is refused
        live.ended_at = timezone.now()
        live.save()
        row.deleted = False
        self.assert_refused(row.save, "needs a live records grant")
        # ending it always passes: soft delete and hard delete
        row.refresh_from_db()
        row.delete()  # CARE's soft delete
        PatientUser._base_manager.filter(pk=row.pk).delete()
        self.assertFalse(PatientUser._base_manager.filter(pk=row.pk).exists())

    def test_patient_user_refused_for_a_restricted_only_user(self):
        w = self.world
        for name in ("Receptionist", "Owner", "Lab Technician"):
            with self.subTest(role=name):
                self.assert_refused(
                    lambda name=name: PatientUser.objects.create(
                        user=w.users[name], patient=w.patient, role=w.role("Nurse")
                    ),
                    "restricted roles",
                )
        # CARE's own roles keep CARE's behaviour (Q-K40)
        PatientUser.objects.create(
            user=w.users["Doctor"], patient=w.patient, role=w.role("Doctor")
        )
        PatientUser.objects.create(
            user=w.user("acc-nobody"), patient=w.patient, role=w.role("Nurse")
        )
        mixed = w.user(
            "acc-mixed",
            ("Receptionist", w.root),
            ("Nurse", w.departments["Paediatrics"]),
        )
        PatientUser.objects.create(user=mixed, patient=w.patient, role=w.role("Nurse"))

    def test_patient_user_refused_for_a_restricted_person_with_another_non_clinical_role(
        self,
    ):
        """A Receptionist who also is a role organisation's "Member" holds no clinical slug:
        still restricted, still refused a care-team row (review S1)."""
        w = self.world
        person = w.user("acc-rec-member", ("Receptionist", w.root))
        team = Organization.objects.create(name="Front office", org_type="role")
        OrganizationUser.objects.create(
            organization=team, user=person, role=w.role("Member")
        )
        self.assert_refused(
            lambda: PatientUser.objects.create(
                user=person, patient=w.patient, role=w.role("Nurse")
            ),
            "restricted roles",
        )

    def test_a_grant_being_deleted_by_the_role_sync_is_not_clinical(self):
        """Second review of S1: CARE's permission lookups skip a role permission marked
        temp_deleted (sync_permissions_roles marks every one, then unmarks those it keeps);
        the trigger and state.restricted skip it too, so they never count a clinical slug
        CARE itself no longer grants."""
        from care.security.models import PermissionModel, RolePermission

        from ihr_access import state

        w = self.world
        mixed = w.user(
            "acc-mixed-sync",
            ("Receptionist", w.root),
            ("Nurse", w.departments["Paediatrics"]),
        )
        self.assertFalse(state.restricted(mixed))
        clinical = RolePermission.objects.filter(
            role=w.role("Nurse"), permission__slug__in=registry.CLINICAL_SLUGS
        )
        self.assertTrue(clinical.exists())
        for marked in (
            lambda: clinical.update(temp_deleted=True),
            lambda: PermissionModel.objects.filter(
                slug__in=registry.CLINICAL_SLUGS
            ).update(temp_deleted=True),
        ):
            with self.subTest(marked=marked), transaction.atomic():
                with as_owner():
                    marked()
                self.assertTrue(state.restricted(mixed))
                self.assert_refused(
                    lambda: PatientUser.objects.create(
                        user=mixed, patient=w.patient, role=w.role("Nurse")
                    ),
                    "restricted roles",
                )
                transaction.set_rollback(True)
        PatientUser.objects.create(user=mixed, patient=w.patient, role=w.role("Nurse"))

    def test_a_moved_nurses_row_can_be_removed_but_not_revived(self):
        w = self.world
        nurse = nurse_on_care_team(w)
        move_to(w, nurse, "Receptionist")
        row = PatientUser.objects.get(user=nurse, patient=w.patient)
        row.delete()  # CARE's soft delete passes
        row.deleted = False
        self.assert_refused(row.save, "restricted roles")  # un-deleting does not
        other = nurse_on_care_team(w)
        move_to(w, other, "Cashier")
        PatientUser.objects.filter(
            user=other
        ).delete()  # CARE's delete_user: a hard delete
        self.assertFalse(PatientUser._base_manager.filter(user=other).exists())

    def test_setting_the_hidden_role_on_an_existing_row_without_a_grant_is_refused(
        self,
    ):
        w = self.world
        row = PatientUser.objects.create(
            user=w.users["Doctor"], patient=w.patient, role=w.role("Doctor")
        )
        row.role = w.role(registry.HIDDEN)
        self.assert_refused(row.save, "needs a live records grant")


class GuardChangesShipWithAMigration(SimpleTestCase):
    """Existing hospitals get a changed guard only from post_migrate, which the fleet runs only
    for a hospital with a migration new to it (review C1; like ihr_audit's
    TriggerChangesShipWithAMigration): a change to the guard's SQL or to the role lists it is
    written from must come with an ihr_access migration that states it."""

    def test_the_latest_migration_states_this_builds_guard(self):
        leaves = MigrationLoader(None, ignore_no_migrations=True).graph.leaf_nodes(
            "ihr_access"
        )
        self.assertEqual(len(leaves), 1, leaves)
        latest = importlib.import_module(f"ihr_access.migrations.{leaves[0][1]}")
        self.assertEqual(
            getattr(latest, "PLACEMENT_SHA256", None),
            placement.fingerprint(),
            "the role placement guard or its role lists changed: add an ihr_access "
            "migration (no operations) with PLACEMENT_SHA256 = the new fingerprint",
        )
