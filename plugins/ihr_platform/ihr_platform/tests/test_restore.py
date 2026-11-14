# ruff: noqa: S608 (test SQL names our own test schemas; values are parameters)

"""Single-hospital restore (P1-T22b; docs/reviews/p1-t22/design.md, P1-T22b item 5 and "After
the attack" 4-11).

``RestoreEndToEnd`` builds a second, seeded hospital A beside the test hospital B, takes A's
package (the quarantine's output, made here from the live schema: the quarantine itself is
tested with a real Postgres in scripts/test_restore_quarantine.py), changes A, restores it and
checks: A is back, B and public unchanged, sequences advanced, sessions invalidated, Redis keys
gone, stale invitations cancelled, security state never going back, verify passing with the
restore record, a forged record or a cut without one failing, the side-name hazard, the parked
schema's expiry. The other classes: every refusal, the restore hold everywhere it must be
honoured, the package's own checks.
"""

import copy
import dataclasses
import json
import shutil
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.db import OperationalError, connection, connections, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from django_tenants.utils import schema_context

from ihr_platform import celery_gate, fleet, release
from ihr_platform.models import (
    Domain,
    Hospital,
    HospitalSchemaState,
    RetiredAddress,
    StaffAccessRequest,
    StaffInvitation,
    TenantOperation,
)
from ihr_platform.restore import engine, files, package
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_platform.testing import as_owner, owner_for_test

A_SLUG, A_DOMAIN = "restore-a", "restore-a.test"


def testhosp():
    return Hospital.objects.get(schema_name=TEST_SCHEMA)


def raw():
    """Django's own psycopg connection (the engine's package calls use it)."""
    connection.ensure_connection()
    return connection.connection


def make_package(schema, out, dump_at=None, **source):
    """The package of ``schema`` as the quarantine writes it (export from the catalogue), marked
    as having passed the quarantine."""
    # (in a transaction, as the quarantine's export: the catalogue read with no search_path)
    with as_owner(), transaction.atomic():
        doc = package.export(raw(), schema, out, snapshot=False)
    doc["report"] = {"quarantine": "passed", "problems": []}
    doc["source"] = {
        "kind": "test",
        "dump_at": (dump_at or timezone.now()).isoformat(),
        "dump": "test",
        **source,
    }
    (Path(out) / "package.json").write_text(json.dumps(doc, sort_keys=True))
    return doc


class FakeKeycloak:
    def __init__(self, members):
        self.members = list(members)

    def get(self, path):
        if "/members?first=0" in path:
            return [{"id": m} for m in self.members]
        return []


class FakeBucket:
    """A versioned bucket: {key: [(when, version id or None, latest)]}; copies recorded."""

    def __init__(self, keys):
        self.keys, self.copies = keys, []

    def get_bucket_versioning(self, Bucket):  # noqa: N803 (boto3's names)
        return {"Status": "Enabled"}

    def get_paginator(self, name):
        bucket = self

        class Pages:
            def paginate(self, Bucket, Prefix):  # noqa: N803
                versions, markers = [], []
                for key, items in bucket.keys.items():
                    if not key.startswith(Prefix):
                        continue
                    for when, version, latest in items:
                        item = {"Key": key, "LastModified": when, "IsLatest": latest}
                        if version is None:
                            markers.append(item)
                        else:
                            versions.append({**item, "VersionId": version})
                return [{"Versions": versions, "DeleteMarkers": markers}]

        return Pages()

    def copy_object(self, Bucket, Key, CopySource):  # noqa: N803
        self.copies.append((Key, CopySource["VersionId"]))


def _store():
    from ihr_audit.tests.test_anchor import FakeStore

    return FakeStore()


@override_settings(
    FILE_UPLOAD_BUCKET="restore-files",
    FACILITY_S3_BUCKET="restore-pictures",
    IHR_STAFF_REFUSE_SUPERUSERS=False,
)
class RestoreEndToEnd(SimpleTestCase):
    """Two seeded hospitals: dump A, change A, restore A (design P1-T22b item 5)."""

    databases = {"default"}
    maxDiff = None

    @classmethod
    def setUpClass(cls):  # noqa: PLR0915 (one world, built once)
        super().setUpClass()
        from care.emr.models.organization import (
            FacilityOrganizationUser,
            Organization,
            OrganizationUser,
        )
        from care.emr.models.patient import Patient, PatientOrganization, PatientUser
        from care.security.models import PermissionModel, RoleModel, RolePermission
        from care.users.models import User
        from ihr_audit import anchor
        from ihr_audit.seal import seal_once
        from ihr_identity.models import StaffIdentity

        from ihr_platform.seeding import load_demo_data

        owner = as_owner()
        owner.__enter__()
        cls.addClassCleanup(owner.__exit__, None, None, None)
        connection.set_schema_to_public()
        cls.addClassCleanup(connection.set_schema, TEST_SCHEMA)
        cls.store = _store()
        cls.dir = Path(tempfile.mkdtemp(prefix="restore-test-"))
        cls.addClassCleanup(shutil.rmtree, cls.dir, ignore_errors=True)
        for patch in (
            mock.patch("ihr_audit.anchor.S3Store", lambda role, quick=False: cls.store),
        ):
            patch.start()
            cls.addClassCleanup(patch.stop)

        # --- hospital A, seeded (CARE's demo fixtures), its trail sealed and fingerprinted
        cls.a = Hospital(
            slug=A_SLUG, name="Restore A Pvt Ltd", status=Hospital.Status.ACTIVE
        )
        cls.a.save()
        cls.addClassCleanup(cls._drop)
        Domain.objects.create(domain=A_DOMAIN, tenant=cls.a)
        Hospital.objects.filter(pk=cls.a.pk).update(keycloak_org_id=uuid.uuid4())
        cls.a.refresh_from_db()
        load_demo_data(cls.a.schema_name, A_DOMAIN)
        connection.set_schema_to_public()
        with schema_context(cls.a.schema_name):
            users = list(User.objects.order_by("pk")[:4])
            cls.kept, cls.revoked, cls.unlinked, cls.not_member = users
            cls.identities = {}
            for user in users:
                identity, _ = StaffIdentity.objects.get_or_create(
                    user=user, defaults={"keycloak_sub": uuid.uuid4()}
                )
                cls.identities[user.pk] = identity
            # an assignment that exists at the dump and is revoked afterwards
            cls.assignment = (
                FacilityOrganizationUser.objects.filter(deleted=False)
                .order_by("pk")
                .first()
                or OrganizationUser.objects.filter(deleted=False).order_by("pk").first()
            )
            # a direct patient grant and a share with an organisation, both taken back
            # after the dump (review S4)
            cls.patient = Patient.objects.order_by("pk").first()
            cls.users_before = sorted(cls.patient.users_cache)
            cls.orgs_before = sorted(cls.patient.organization_cache)
            cls.grant = PatientUser.objects.create(
                user=cls.kept,
                patient=cls.patient,
                role=RoleModel.objects.order_by("pk").first(),
            )
            cls.share = PatientOrganization.objects.create(
                patient=cls.patient,
                organization=Organization.objects.filter(deleted=False)
                .order_by("pk")
                .first(),
            )
            seal_once(cls.a.schema_name)
        anchor.write(cls.store)
        # an invitation sent before the dump (its withdrawal is asked for after it: S8)
        cls.old_invitation = StaffInvitation.objects.create(
            hospital=cls.a,
            care_user_id=cls.kept.pk,
            email="kept@example.in",
            first_name="Kept",
            invited_by="test",
            status=StaffInvitation.Status.SENT,
        )

        # --- the dump (the package the quarantine would write)
        cls.dump_at = timezone.now()
        cls.pkg = cls.dir / "package"
        cls.doc = make_package(cls.a.schema_name, cls.pkg, dump_at=cls.dump_at)
        with connection.cursor() as cursor:
            cls.seq_at_dump = package.sequences(cursor.cursor, cls.a.schema_name)

        # --- A changes after the dump
        later = timezone.now()
        with schema_context(cls.a.schema_name):
            cls.new_user = User.objects.create(
                username="after-the-dump", first_name="After", last_name="Dump"
            )
            User.objects.filter(pk=cls.revoked.pk).update(is_active=False)
            User.objects.filter(pk=cls.kept.pk).update(first_name="ChangedAfterDump")
            StaffIdentity.objects.filter(user=cls.unlinked).update(
                keycloak_sub=uuid.uuid4()
            )
            if cls.assignment is not None:
                type(cls.assignment).objects.filter(pk=cls.assignment.pk).update(
                    deleted=True
                )
            cache.set("ihr:test:restore-a", "a")  # A's own key (schema-prefixed)
            cls.new_user_id = cls.new_user.pk
            # CARE's own revocations: hard deletes (S4)
            PatientUser.objects.filter(pk=cls.grant.pk).delete()
            PatientOrganization.objects.filter(pk=cls.share.pk).delete()
            # a custom role made after the dump, with one permission (C3)
            first, second = PermissionModel.objects.order_by("pk").values_list(
                "pk", flat=True
            )[:2]
            cls.role_after = RoleModel.objects.create(name="custom-after-the-dump")
            RolePermission.objects.create(role=cls.role_after, permission_id=first)
            cls.permission_by_migrate = second
            seal_once(cls.a.schema_name)
        with schema_context(TEST_SCHEMA):
            cache.set("ihr:test:restore-b", "b")  # B's: must survive A's restore
        cls.invitation = StaffInvitation.objects.create(
            hospital=cls.a,
            care_user_id=cls.new_user_id,
            email="after@example.in",
            first_name="After",
            invited_by="test",
        )
        # requests after the dump: two that take access away from a user the restored copy
        # has (kept queued), one for a user it does not have (cancelled; S8)
        cls.requests = {
            action: StaffAccessRequest.objects.create(
                hospital=cls.a,
                care_user_id=user,
                action=action,
                requested_by="admin-test",
            ).pk
            for action, user in (
                (StaffAccessRequest.Action.RESET_SIGN_IN, cls.kept.pk),
                (StaffAccessRequest.Action.CANCEL_INVITATION, cls.kept.pk),
                (StaffAccessRequest.Action.RESEND_INVITATION, cls.new_user_id),
            )
        }
        anchor.write(cls.store)  # claims seals the restore will supersede
        # Someone holds the sealer's lock and a long transaction pins the horizon, after a
        # write: the live trail's tail cannot be sealed the usual way (S5)
        cls.blocker = connections.create_connection("default")
        cls.addClassCleanup(cls.blocker.close)
        with cls.blocker.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_lock(hashtext(%s))",
                [f"ihr_audit_seal:{cls.a.schema_name}"],
            )
            cursor.execute(
                f'UPDATE "{cls.a.schema_name}".users_user SET last_name = last_name || %s '
                "WHERE id = %s",
                ["-", cls.kept.pk],
            )
            cursor.execute("BEGIN")
            cursor.execute("SELECT pg_current_xact_id()")
        with connection.cursor() as cursor:
            cls.parked_seq = package.sequences(cursor.cursor, cls.a.schema_name)
            cursor.execute(f'SELECT max(seq) FROM "{cls.a.schema_name}".ihr_audit_seal')
            cls.live_head = cursor.fetchone()[0]

        # --- objects in the file buckets: one replaced after the dump, one new
        before = cls.dump_at - timedelta(minutes=5)
        prefix = f"t/{cls.a.uuid}/"
        cls.bucket = FakeBucket(
            {
                f"{prefix}patient/replaced.pdf": [
                    (before, "v1", False),
                    (later, "v2", True),
                ],
                f"{prefix}patient/unchanged.pdf": [(before, "v1", True)],
                f"{prefix}patient/new.pdf": [(later, "v1", True)],
                "t/someone-else/x.pdf": [(later, "v9", True)],
            }
        )
        members = [
            str(i.keycloak_sub)
            for uid, i in cls.identities.items()
            if uid != cls.not_member.pk
        ]

        # --- B and public, before
        with connection.cursor() as cursor:
            cls.b_before = package.fingerprint(
                cursor.cursor, [TEST_SCHEMA], (cls.a.pk, cls.a.uuid)
            )

        # --- the restore
        cls.out, cls.err = [], []
        real_sync = fleet.roles_sync

        def roles_sync(hospital):
            """This build's role sync, and a role it makes (a release's new system role)."""
            real_sync(hospital)
            with schema_context(hospital.schema_name):
                role = RoleModel.objects.create(name="made-by-migrate")
                RolePermission.objects.create(
                    role=role, permission_id=cls.permission_by_migrate
                )
            cls.role_by_migrate = role.pk

        with (
            mock.patch(
                "ihr_platform.management.commands.ihr_keycloak_sync.keycloak_admin",
                return_value=FakeKeycloak(members),
            ),
            mock.patch.dict(files.CLIENT, {"factory": lambda setting: cls.bucket}),
            mock.patch.object(fleet, "roles_sync", side_effect=roles_sync),
        ):
            cls.started = timezone.now()
            run = engine.Restore(
                engine.Options(
                    slug=A_SLUG,
                    package_dir=cls.pkg,
                    reason="test: restore A to its dump",
                    operator="tester",
                    drain_seconds=0,
                    out=cls.out.append,
                    err=cls.err.append,
                )
            )
            try:
                cls.code = run.start()
            except Exception as exc:  # each test then fails, naming it
                cls.code = f"{type(exc).__name__}: {exc}"
            finally:
                with cls.blocker.cursor() as cursor:
                    cursor.execute("ROLLBACK")
                    cursor.execute("SELECT pg_advisory_unlock_all()")
            cls.op = run.op
        connection.set_schema_to_public()
        with connection.cursor() as cursor:
            cls.b_after = package.fingerprint(
                cursor.cursor, [TEST_SCHEMA], (cls.a.pk, cls.a.uuid)
            )

    @classmethod
    def _drop(cls):
        with as_owner():
            connection.set_schema_to_public()
            TenantOperation.objects.filter(slug=A_SLUG).delete()
            StaffInvitation.objects.filter(hospital=cls.a).delete()
            Domain.objects.filter(tenant=cls.a).delete()
            with connection.cursor() as cursor:
                for name, *_ in engine.parked_schemas():
                    if cls.a.schema_name in name:
                        cursor.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
            Hospital.objects.filter(pk=cls.a.pk).delete()
            RetiredAddress.objects.filter(value__in=[A_SLUG, A_DOMAIN]).delete()
            with connection.cursor() as cursor:
                cursor.execute(f'DROP SCHEMA IF EXISTS "{cls.a.schema_name}" CASCADE')
            with schema_context(TEST_SCHEMA):
                cache.delete("ihr:test:restore-b")

    def setUp(self):
        owner_for_test(self)
        connection.set_schema_to_public()
        self.addCleanup(connection.set_schema_to_public)

    def _verify(self, store):
        from io import StringIO

        out = StringIO()
        with mock.patch("ihr_audit.anchor.S3Store", lambda role, quick=False: store):
            try:
                call_command("ihr_audit_verify", slug=A_SLUG, stdout=out, stderr=out)
            except SystemExit as exc:
                return exc.code or 0, out.getvalue()
        return 0, out.getvalue()

    # --- the restore itself -----------------------------------------------------------------

    def test_the_restore_finished_and_lifted_its_hold(self):
        self.assertEqual(self.code, 0, self.out + self.err)
        op = TenantOperation.objects.get(pk=self.op.pk)
        self.assertEqual(op.status, "succeeded")
        self.assertEqual(op.kind, "restore")
        self.assertEqual({s["status"] for s in op.steps}, {"done"})
        self.assertIn(f"ihr-restore: hospital={A_SLUG} outcome=done", self.err)
        self.assertEqual(release.restore_holder(self.a), "")
        self.assertTrue(release.servable(Hospital.objects.get(pk=self.a.pk)))
        # Served again without restarting any app container (review C5)
        restart = next(s for s in op.steps if s["name"] == "restart")
        self.assertEqual(restart["note"], "nothing to restart")

    def test_the_restored_hospital_is_back_as_dumped(self):
        from care.users.models import User

        with schema_context(self.a.schema_name):
            self.assertFalse(User.objects.filter(pk=self.new_user_id).exists())
            self.assertNotEqual(
                User.objects.get(pk=self.kept.pk).first_name, "ChangedAfterDump"
            )

    def test_other_hospitals_and_public_are_unchanged(self):
        """B's rows, triggers, function bodies, grants, sequences, and every public row that is
        not A's: the same hashes before and after."""
        self.assertEqual(self.b_before, self.b_after)

    def test_sequences_are_advanced_past_the_parked_copy(self):
        with connection.cursor() as cursor:
            now = package.sequences(cursor.cursor, self.a.schema_name)
        users_seq = next(n for n in self.parked_seq if n.startswith("users_user_id"))
        self.assertGreater(
            self.parked_seq[users_seq][0], self.seq_at_dump[users_seq][0]
        )
        self.assertGreaterEqual(now[users_seq][0], self.parked_seq[users_seq][0])
        entries = next(n for n in now if n.startswith("ihr_audit_entry"))
        # (the restore's own entries alone would pass ``> seq_at_dump``: review C8)
        self.assertGreaterEqual(now[entries][0], self.parked_seq[entries][0])
        # an id the parked copy used is never given again
        from care.users.models import User

        with schema_context(self.a.schema_name):
            fresh = User.objects.create(username="after-the-restore")
        self.addCleanup(self._delete_user, fresh.pk)
        self.assertGreater(fresh.pk, self.new_user_id)

    def _delete_user(self, pk):
        from care.users.models import User

        with schema_context(self.a.schema_name):
            User.objects.filter(pk=pk).delete()

    def test_every_staff_session_is_invalidated(self):
        from ihr_identity.models import StaffIdentity

        with schema_context(self.a.schema_name):
            after = list(
                StaffIdentity.objects.values_list("sessions_valid_after", flat=True)
            )
        self.assertTrue(after)
        self.assertTrue(all(t is not None and t >= self.started for t in after), after)

    def test_redis_keys_of_the_hospital_are_gone(self):
        with schema_context(self.a.schema_name):
            self.assertIsNone(cache.get("ihr:test:restore-a"))
        with schema_context(TEST_SCHEMA):
            self.assertEqual(cache.get("ihr:test:restore-b"), "b")
        result = TenantOperation.objects.get(pk=self.op.pk).result["reconcile"]
        self.assertGreaterEqual(result["redis"]["deleted"], 1)

    def test_stale_invitations_are_cancelled(self):
        invitation = StaffInvitation.objects.get(pk=self.invitation.pk)
        self.assertEqual(invitation.status, StaffInvitation.Status.CANCELLED)
        self.assertIn("restore", invitation.error)

    def test_security_state_never_goes_back(self):
        from care.users.models import User

        with schema_context(self.a.schema_name):
            self.assertFalse(
                User.objects.get(pk=self.revoked.pk).is_active
            )  # off since
            self.assertFalse(
                User.objects.get(pk=self.unlinked.pk).is_active
            )  # link changed
            self.assertFalse(
                User.objects.get(pk=self.not_member.pk).is_active
            )  # Keycloak
            self.assertTrue(User.objects.get(pk=self.kept.pk).is_active)
        if self.assignment is not None:
            with connection.cursor() as cursor:
                cursor.execute(
                    f'SELECT deleted FROM "{self.a.schema_name}"."{self.assignment._meta.db_table}" '
                    "WHERE id = %s",
                    [self.assignment.pk],
                )
                self.assertTrue(
                    cursor.fetchone()[0], "a revocation after the dump came back"
                )
        access = TenantOperation.objects.get(pk=self.op.pk).result["access"]
        self.assertGreaterEqual(access["users_deactivated"], 1)

    def test_patient_grants_and_shares_taken_back_stay_so(self):
        """Review S4: a clinician's grant on one patient (emr_patientuser) and a patient's
        share with an organisation, both deleted after the dump, do not come back, and the
        patient's access caches (the list filters on them) no longer name them."""
        from care.emr.models.patient import Patient

        with connection.cursor() as cursor:
            for table, pk in (
                ("emr_patientuser", self.grant.pk),
                ("emr_patientorganization", self.share.pk),
            ):
                cursor.execute(
                    f'SELECT deleted FROM "{self.a.schema_name}".{table} WHERE id = %s',
                    [pk],
                )
                self.assertTrue(
                    cursor.fetchone()[0], f"{table}: a revocation came back"
                )
        with schema_context(self.a.schema_name):
            patient = Patient.objects.get(pk=self.patient.pk)
        self.assertNotIn(self.kept.pk, patient.users_cache)
        self.assertEqual(sorted(patient.users_cache), self.users_before)
        self.assertEqual(set(patient.organization_cache), set(self.orgs_before))
        result = TenantOperation.objects.get(pk=self.op.pk).result
        self.assertEqual(result["access"]["emr_patientuser_removed"], 1)
        self.assertEqual(result["access"]["emr_patientorganization_removed"], 1)
        self.assertEqual(result["reconcile"]["patients_rebuilt"], 1)

    def test_ids_written_during_migrate_are_never_the_parked_copys(self):
        """Reviews S6 and C3: migrate (here this build's role sync making a role, as a release's
        new system role would) writes rows and audit entries after the sequences went past
        the parked copy's, and after the strict merge: no id the parked copy used after the
        dump is used again, and the new role keeps its permission."""
        from care.security.models import RoleModel, RolePermission

        with schema_context(self.a.schema_name):
            self.assertGreater(self.role_by_migrate, self.role_after.pk)
            self.assertTrue(RoleModel.objects.filter(pk=self.role_by_migrate).exists())
            self.assertTrue(
                RolePermission.objects.filter(
                    role_id=self.role_by_migrate,
                    permission_id=self.permission_by_migrate,
                    deleted=False,
                ).exists()
            )
        parked = TenantOperation.objects.get(pk=self.op.pk).result["parked"]
        dumped = self.doc["facts"]["audit"]["max_entry_id"]
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT max(id) FROM "{parked}".ihr_audit_entry')
            parked_max = cursor.fetchone()[0]
            self.assertGreater(parked_max, dumped)
            cursor.execute(
                f'SELECT count(*) FROM "{self.a.schema_name}".ihr_audit_entry '
                "WHERE id > %s AND id <= %s",
                [dumped, parked_max],
            )
            self.assertEqual(cursor.fetchone()[0], 0, "an entry id was used twice")

    def test_the_live_tail_is_sealed_before_the_park(self):
        """Review S5: with the sealer's lock held elsewhere and a transaction pinning the
        horizon, the live trail's last entries are still sealed (by the restore itself) and
        archived: none is left only in the parked schema."""
        result = TenantOperation.objects.get(pk=self.op.pk).result
        self.assertEqual(result.get("parked_unsealed"), 0, self.code)
        self.assertGreaterEqual(result.get("final_sealed", 0), 1)
        parked = result["parked"]
        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT count(*) FROM "{parked}".ihr_audit_entry WHERE txid >= '
                f'(SELECT max(upper_txid) FROM "{parked}".ihr_audit_seal)'
            )
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute(
                "SELECT count(*) FROM public.ihr_restore_superseded_entry e "
                f'JOIN "{parked}".ihr_audit_entry p ON p.id = e.id AND p.txid = e.txid '
                "WHERE e.hospital = %s AND e.generation = 1",
                [str(self.a.uuid)],
            )
            archived = cursor.fetchone()[0]
            cursor.execute(
                f'SELECT count(*) FROM "{parked}".ihr_audit_entry WHERE txid >= '
                f'(SELECT lower_txid FROM "{parked}".ihr_audit_seal WHERE seq = %s)',
                [self.doc["facts"]["audit"]["seal_seq"] + 1],
            )
            self.assertEqual(archived, cursor.fetchone()[0])

    def test_requests_that_take_access_away_stay_queued(self):
        """Review S8: a sign-in reset and an invitation's withdrawal asked for after the dump,
        for a user the restored copy has, stay queued (named); a request for a user it does
        not have is cancelled; the invitation sent before the dump is left to the worker."""
        status = dict(
            StaffAccessRequest.objects.filter(
                pk__in=self.requests.values()
            ).values_list("action", "status")
        )
        self.assertEqual(status[StaffAccessRequest.Action.RESET_SIGN_IN], "queued")
        self.assertEqual(status[StaffAccessRequest.Action.CANCEL_INVITATION], "queued")
        self.assertEqual(status[StaffAccessRequest.Action.RESEND_INVITATION], "failed")
        self.assertEqual(
            StaffInvitation.objects.get(pk=self.old_invitation.pk).status,
            StaffInvitation.Status.SENT,
        )
        public = TenantOperation.objects.get(pk=self.op.pk).result["reconcile"][
            "public"
        ]
        self.assertEqual(len(public["kept_queued"]), 2)
        self.assertTrue(
            any("kept queued" in line or "stay queued" in line for line in self.out)
        )

    def test_files_come_back_from_their_versions_and_newer_ones_are_kept(self):
        self.assertIn(
            (f"t/{self.a.uuid}/patient/replaced.pdf", "v1"), self.bucket.copies
        )
        self.assertEqual(
            len([c for c in self.bucket.copies if "replaced" in c[0]]), 2
        )  # once per bucket (the same fake serves both)
        step = next(
            s
            for s in TenantOperation.objects.get(pk=self.op.pk).steps
            if s["name"] == "files"
        )
        self.assertEqual(step["files"]["newer_kept"], 1)
        self.assertEqual(step["files"]["unchanged"], 1)

    def test_verify_passes_with_the_restore_record(self):
        from ihr_audit import archive

        code, text = self._verify(self.store)
        self.assertEqual(code, 0, text)
        self.assertIn("accounted for by restore events", text)
        with connection.cursor() as cursor:
            record = archive.recorded(cursor, str(self.a.uuid), 1)
        self.assertIsNotNone(record)
        self.assertGreaterEqual(record["to_seq"], self.live_head)
        self.assertEqual(record["operation"], str(self.op.pk))
        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT count(*) FROM "{self.a.schema_name}".ihr_audit_entry WHERE kind = %s',
                ["restore"],
            )
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_a_forged_restore_record_or_a_cut_without_one_fails(self):
        from ihr_audit import anchor, events

        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT seq, encode(hash, 'hex') FROM \"{self.a.schema_name}\".ihr_audit_seal "
                "ORDER BY seq DESC LIMIT 1"
            )
            head, digest = cursor.fetchone()
        # a cut with no record: a fingerprint names a seal the chain no longer has
        cut = copy.deepcopy(self.store)
        claim = {
            "format": anchor.FORMAT,
            "platform": anchor.prefix(),
            "written_at": datetime.now(UTC).isoformat(),
            "hospitals": {
                str(self.a.uuid): {
                    "slug": A_SLUG,
                    "seq": head + 5,
                    "hash": "ab" * 32,
                    "sealed_at": None,
                }
            },
        }
        cut.put(
            f"{anchor.prefix()}2026/11/14/20261114T000000Z-cut00000.json",
            json.dumps(claim).encode(),
        )
        code, text = self._verify(cut)
        self.assertEqual(code, 1, text)
        self.assertIn("is missing (the trail was cut)", text)
        # the same cut with a forged restore record (no superseded segment kept for it)
        rows = [
            events.SealRow(
                head + 5,
                "1",
                "2",
                1,
                b"\x01" * 32,
                bytes.fromhex(digest),
                bytes.fromhex("ab" * 32),
                3,
            )
        ]
        forged = events.restore_document(self.a, 2, (head + 4, digest), rows, "forger")
        events.write(forged, cut)
        code, text = self._verify(cut)
        self.assertEqual(code, 1, text)
        self.assertIn("is missing (the trail was cut)", text)

    def test_a_changed_archived_entry_fails_verify(self):
        """Review C8: the archive is what a restore record is checked against. One superseded
        entry changed there (the owner turning the append-only guard off) and the real
        archive's segment no longer recomputes: ihr_audit_verify fails."""
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE public.ihr_restore_superseded_entry DISABLE TRIGGER USER"
                )
                cursor.execute(
                    "UPDATE public.ihr_restore_superseded_entry SET actor = actor || '!' "
                    "WHERE id = (SELECT min(id) FROM public.ihr_restore_superseded_entry "
                    "WHERE hospital = %s AND generation = 1) AND hospital = %s "
                    "AND generation = 1",
                    [str(self.a.uuid), str(self.a.uuid)],
                )
                self.assertEqual(cursor.rowcount, 1)
                cursor.execute(
                    "ALTER TABLE public.ihr_restore_superseded_entry ENABLE TRIGGER USER"
                )
            code, text = self._verify(self.store)
            transaction.set_rollback(True)
        self.assertEqual(code, 1, text)
        self.assertNotIn("accounted for by restore events", text)

    def test_side_names_are_refused_and_why(self):
        """TENANCY §7.5 correction 1: a copy under another name keeps trigger functions that
        name the real schema, so a write there lands in the live trail. The parked schema is
        such a copy: shown here, and why nobody but the owner may reach it."""
        parked = TenantOperation.objects.get(pk=self.op.pk).result["parked"]
        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT count(*) FROM "{self.a.schema_name}".ihr_audit_entry'
            )
            before = cursor.fetchone()[0]
            cursor.execute(
                f'UPDATE "{parked}".users_user SET last_name = last_name || %s WHERE id = %s',
                ["!", self.kept.pk],
            )
            cursor.execute(
                f'SELECT count(*) FROM "{self.a.schema_name}".ihr_audit_entry'
            )
            after = cursor.fetchone()[0]
            self.assertEqual(
                after, before + 1, "the side copy's write went into the live trail"
            )
            cursor.execute(
                "SELECT r.rolname, has_schema_privilege(r.rolname, %s, 'USAGE') FROM pg_roles r "
                "WHERE r.rolname IN ('ihr_app', 'ihr_audit_watch')",
                [parked],
            )
            for role, usage in cursor.fetchall():
                self.assertFalse(usage, f"{role} reaches the parked schema")
        # ...so a package is loaded under its own (the real) name only
        with (
            self.assertRaises(package.PackageError),
            transaction.atomic(),
            connection.cursor(),
        ):
            package.load(raw(), self.pkg, "h_sidename01", self.doc)
        run = engine.Restore(engine.Options(slug=A_SLUG, package_dir=self.pkg))
        run.hospital = testhosp()
        with self.assertRaisesRegex(engine.Refused, "real name only"):
            run._read_package()

    # --- a dump whose history does not hold (review S7) ----------------------------------------

    def _forged_restore(self, name, change):
        """A restore of A from a copy of its package that ``change(pkg, doc)`` altered (the
        files' hashes then put right, as a forger would): it must stop in the restore step,
        before the commit (A's live schema untouched, nothing parked), and is then aborted.
        Returns the failed step's error."""
        from care.users.models import User

        pkg = self.dir / name
        shutil.copytree(self.pkg, pkg)
        self.addCleanup(shutil.rmtree, pkg, ignore_errors=True)
        doc = json.loads((pkg / "package.json").read_text())
        change(pkg, doc)
        for item in doc["tables"]:
            item["sha256"] = package._sha(pkg / item["file"])
        (pkg / "package.json").write_text(json.dumps(doc, sort_keys=True))
        with schema_context(self.a.schema_name):
            marker = User.objects.create(username=f"live-before-{name}")
        self.addCleanup(self._delete_user, marker.pk)
        run = engine.Restore(
            engine.Options(
                slug=A_SLUG,
                package_dir=pkg,
                reason=f"test: {name}",
                operator="tester",
                drain_seconds=0,
                out=lambda line: None,
                err=lambda line: None,
            )
        )
        with self.assertRaises(engine.StepFailed) as stop:
            run.start()
        connection.set_schema_to_public()
        op = TenantOperation.objects.get(pk=run.op.pk)
        failed = next(s for s in op.steps if s["status"] == "failed")
        self.assertEqual(failed["name"], "restore")
        with connection.cursor() as cursor:
            self.assertFalse(engine._schema_exists(cursor, op.result["parked"]))
        with schema_context(self.a.schema_name):  # the live schema, as it was
            self.assertTrue(User.objects.filter(pk=marker.pk).exists())
        engine.Restore(engine.Options(slug=A_SLUG, err=lambda line: None)).abort(op.pk)
        self.assertEqual(release.restore_holder(self.a), "")
        return str(stop.exception)

    def _entries(self, pkg, doc, edit):
        """The dump's audit entries file, its lowest id's line passed through ``edit``."""
        item = next(t for t in doc["tables"] if t["table"] == "ihr_audit_entry")
        path = pkg / item["file"]
        lines = path.read_bytes().split(b"\n")
        first = min(
            (i for i, line in enumerate(lines) if line),
            key=lambda i: int(lines[i].split(b"\t")[item["columns"].index("id")]),
        )
        fields = lines[first].split(b"\t")
        fields[item["columns"].index("actor")] = b"forged-actor"
        lines[first] = b"\t".join(fields)
        path.write_bytes(b"\n".join(lines))
        edit(pkg, doc)

    def test_w1_a_dump_whose_sealed_entries_do_not_verify_stops_before_the_commit(self):
        error = self._forged_restore(
            "sealed-entry-changed",
            lambda pkg, doc: self._entries(pkg, doc, lambda pkg, doc: None),
        )
        self.assertIn("the restored trail does not verify", error)
        self.assertIn("entries changed since sealing", error)

    def test_w2_a_dump_with_no_seal_cannot_bring_entries_of_its_own(self):
        """A dump with no seal (all its entries past its last seal) whose entries are not the
        live trail's own of the same id: refused before the commit."""

        def no_seal(pkg, doc):
            item = next(t for t in doc["tables"] if t["table"] == "ihr_audit_seal")
            (pkg / item["file"]).write_bytes(b"")
            item["rows"] = 0
            doc["facts"]["audit"].update(seal_seq=0, seal_hash="", seal_upper_txid="0")

        error = self._forged_restore(
            "no-seal-foreign-entry", lambda pkg, doc: self._entries(pkg, doc, no_seal)
        )
        self.assertIn("past its last seal are not the live trail's own", error)

    def test_x_a_resumed_restore_survives_its_stops(self):  # noqa: PLR0915 (one story)
        """Review C8 (and C1, C6): one restore driven through every stop it can make. Only
        the parked schema there (a park whose load never happened, as an older build's two
        transactions could leave it): resume loads the package under the real name. The
        load committed but not recorded (a crash): resume finds both schemas and goes on. The
        package removed after the load: no later step needs it. A release ran meanwhile (the
        schema looks behind): the next step migrates it again. The Keycloak step without the
        operator's credential (exit 4), then with it. The event write failing after the
        archive was written: resume reuses that generation and writes one record. A restart
        asked for (exit 3), then --restarted. Then it is done and verify passes."""
        from django.core.management.base import CommandError
        from ihr_audit import archive

        pkg = self.dir / "package-resume"
        shutil.copytree(self.pkg, pkg)
        self.addCleanup(shutil.rmtree, pkg, ignore_errors=True)
        lines = []
        options = {
            "slug": A_SLUG,
            "reason": "test: a restore that stops and resumes",
            "operator": "tester",
            "drain_seconds": 0,
            "out": lines.append,
            "err": lines.append,
        }
        run = engine.Restore(
            engine.Options(package_dir=pkg, stop_after="seal", **options)
        )
        self.assertEqual(run.start(), 0)
        op = TenantOperation.objects.get(pk=run.op.pk)
        self.assertEqual(op.status, "pending")
        # Only the parked schema: the live one parked, its load never done (review C8)
        parked = engine.parked_name(self.a.schema_name, timezone.now().astimezone(UTC))
        with connection.cursor() as cursor:
            cursor.execute(f'ALTER SCHEMA "{self.a.schema_name}" RENAME TO "{parked}"')
        op.result = {**op.result, "parked": parked}
        op.save(update_fields=["result"])
        self.assertEqual(
            engine.Restore(engine.Options(stop_after="restore", **options)).resume(
                op.pk
            ),
            0,
        )
        op.refresh_from_db()
        restore = next(s for s in op.steps if s["name"] == "restore")
        self.assertEqual((restore["status"], restore["parked"]), ("done", parked))
        self.assertEqual(restore["rows"], sum(t["rows"] for t in self.doc["tables"]))
        with connection.cursor() as cursor:
            self.assertTrue(engine._schema_exists(cursor, self.a.schema_name))
            self.assertTrue(engine._schema_exists(cursor, parked))
            cursor.execute(
                "SELECT has_schema_privilege('ihr_app', %s, 'USAGE') FROM pg_roles "
                "WHERE rolname = 'ihr_app'",
                [parked],
            )
            self.assertFalse(any(r[0] for r in cursor.fetchall()))
        # A crash right after the commit: the step was never recorded as done
        op.steps = [
            {**s, "status": "running"} if s["name"] == "restore" else s
            for s in op.steps
        ]
        op.save(update_fields=["steps"])
        self.assertEqual(
            engine.Restore(engine.Options(stop_after="reconcile", **options)).resume(
                op.pk
            ),
            0,
        )
        op.refresh_from_db()
        restore = next(s for s in op.steps if s["name"] == "restore")
        self.assertEqual(restore["note"], "already committed (both schemas there)")
        shutil.rmtree(pkg)  # no step after the load reads the package (C6)
        real_state, migrations = release.schema_state, []
        real_migrate = fleet.migrate_one

        def behind_once(schema):
            state = real_state(schema)
            if schema == self.a.schema_name and not migrations:
                return dataclasses.replace(
                    state, pending=(("ihr_audit", "9999_a_new_build"),)
                )
            return state

        def migrate_one(*args, **kwargs):
            migrations.append(args)
            return real_migrate(*args, **kwargs)

        with (
            mock.patch.object(release, "schema_state", side_effect=behind_once),
            mock.patch.object(fleet, "migrate_one", side_effect=migrate_one),
            mock.patch(
                "ihr_platform.management.commands.ihr_keycloak_sync.keycloak_admin",
                side_effect=CommandError("no credential here"),
            ),
        ):
            code = engine.Restore(engine.Options(**options)).resume(op.pk)
        self.assertEqual(code, engine.EXIT_KEYCLOAK)
        self.assertEqual(
            len(migrations), 1, "a schema behind this build was not migrated"
        )
        op.refresh_from_db()
        self.assertEqual(
            next(s for s in op.steps if s["name"] == "migrate")["remigrated"], 1
        )
        members = [str(i.keycloak_sub) for i in self.identities.values()]
        with (
            mock.patch(
                "ihr_platform.management.commands.ihr_keycloak_sync.keycloak_admin",
                return_value=FakeKeycloak(members),
            ),
            mock.patch.dict(files.CLIENT, {"factory": lambda setting: FakeBucket({})}),
            mock.patch.object(
                engine.Restore,
                "_write_event",
                side_effect=engine.StepFailed("the bucket is down"),
            ),
            self.assertRaisesRegex(engine.StepFailed, "the bucket is down"),
        ):
            engine.Restore(engine.Options(**options)).resume(op.pk)
        with connection.cursor() as cursor:
            generation = archive.generation_of(cursor, str(self.a.uuid), op.pk)
        self.assertIsNotNone(generation)
        code = engine.Restore(engine.Options(restart=True, **options)).resume(op.pk)
        self.assertEqual(code, engine.EXIT_RESTART)
        code = engine.Restore(engine.Options(restarted=True, **options)).resume(op.pk)
        self.assertEqual(code, 0, lines)
        op.refresh_from_db()
        self.assertEqual(op.status, "succeeded")
        with connection.cursor() as cursor:
            self.assertEqual(
                archive.generation_of(cursor, str(self.a.uuid), op.pk), generation
            )
            cursor.execute(
                "SELECT count(*) FROM public.ihr_restore_record WHERE operation = %s",
                [str(op.pk)],
            )
            self.assertEqual(cursor.fetchone()[0], 1)
        code, text = self._verify(self.store)
        self.assertEqual(code, 0, text)

    def test_y_a_failed_restore_stays_held_and_its_abort_brings_the_live_schema_back(
        self,
    ):
        """A step that fails leaves the hospital held (never served half restored); --abort
        before reconcile drops the restored copy, un-parks the live schema and lifts the hold."""
        from care.users.models import User

        with schema_context(self.a.schema_name):
            marker = User.objects.create(username="live-before-abort")
        err = []
        with mock.patch.object(
            fleet, "migrate_one", return_value={"outcome": "failed", "error": "test"}
        ):
            run = engine.Restore(
                engine.Options(
                    slug=A_SLUG,
                    package_dir=self.pkg,
                    reason="test: a restore that fails",
                    operator="tester",
                    no_restart=True,
                    drain_seconds=0,
                    out=lambda line: None,
                    err=err.append,
                )
            )
            with self.assertRaisesRegex(engine.StepFailed, "migrate"):
                run.start()
        connection.set_schema_to_public()
        op = TenantOperation.objects.get(pk=run.op.pk)
        self.assertEqual(op.status, "failed")
        self.assertIn(
            f"ihr-restore: hospital={A_SLUG} outcome=failed step=migrate", err
        )
        self.assertEqual(release.restore_holder(self.a), str(op.pk))
        self.assertFalse(release.servable(Hospital.objects.get(pk=self.a.pk)))
        with schema_context(self.a.schema_name):  # the restored copy is in place, held
            self.assertFalse(User.objects.filter(pk=marker.pk).exists())
        engine.Restore(
            engine.Options(slug=A_SLUG, out=lambda line: None, err=err.append)
        ).abort(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.status, "rolled_back")
        self.assertIn(f"ihr-restore: hospital={A_SLUG} outcome=aborted", err)
        self.assertEqual(release.restore_holder(self.a), "")
        with schema_context(self.a.schema_name):  # the live schema is back
            self.assertTrue(User.objects.filter(pk=marker.pk).exists())
        self.assertNotIn(op.result["parked"], [p[0] for p in engine.parked_schemas()])
        self.assertTrue(release.servable(Hospital.objects.get(pk=self.a.pk)))
        with self.assertRaisesRegex(engine.Refused, "rolled_back"):
            engine.Restore(engine.Options(slug=A_SLUG)).abort(op.pk)

    def test_z_a_parked_schema_is_dropped_after_seven_days(self):
        parked = TenantOperation.objects.get(pk=self.op.pk).result["parked"]
        names = [p[0] for p in engine.parked_schemas()]
        self.assertIn(parked, names)
        with self.assertRaises(engine.Refused):
            engine.expire_parked(days=3)
        self.assertEqual(
            engine.expire_parked(now=timezone.now() + timedelta(days=6)), []
        )
        later = timezone.now() + timedelta(days=8)
        self.assertIn(
            f"ihr-restore: parked={parked} outcome=overdue", engine.overdue_lines(later)
        )
        self.assertIn((parked, True), engine.expire_parked(now=later))
        self.assertNotIn(parked, [p[0] for p in engine.parked_schemas()])


class RefusalTests(SimpleTestCase):
    """Each pre-check refuses, and nothing is changed (no operation, no hold)."""

    databases = {"default"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dir = Path(tempfile.mkdtemp(prefix="restore-refusals-"))
        cls.addClassCleanup(shutil.rmtree, cls.dir, ignore_errors=True)
        connection.set_schema_to_public()
        cls.addClassCleanup(connection.set_schema, TEST_SCHEMA)
        with as_owner():
            from ihr_audit.seal import seal_once

            with schema_context(TEST_SCHEMA):
                seal_once(TEST_SCHEMA)
            cls.doc = make_package(TEST_SCHEMA, cls.dir / "pkg")

    def setUp(self):
        owner_for_test(self)
        connection.set_schema_to_public()
        self.addCleanup(connection.set_schema_to_public)

    def _run(self, doc=None, hospital=None, **options):
        run = engine.Restore(
            engine.Options(
                slug=testhosp().slug,
                package_dir=self.dir / "pkg",
                reason="test refusals",
                operator="tester",
                out=lambda line: None,
                err=lambda line: None,
                **options,
            )
        )
        run.hospital = hospital or testhosp()
        run.doc = copy.deepcopy(doc or self.doc)
        return run

    def assertRefused(self, pattern, doc=None, hospital=None, **options):  # noqa: N802
        before = TenantOperation.objects.filter(kind="restore").count()
        with self.assertRaisesRegex(engine.Refused, pattern):
            self._run(doc, hospital, **options).precheck()
        self.assertEqual(TenantOperation.objects.filter(kind="restore").count(), before)
        self.assertEqual(release.restore_holder(testhosp()), "")

    def test_a_good_package_passes(self):
        report = self._run().precheck()
        self.assertEqual(report["functions_differ"], [])
        self.assertEqual(
            report["restored_to"][0], self.doc["facts"]["audit"]["seal_seq"]
        )

    def test_not_through_the_quarantine(self):
        doc = copy.deepcopy(self.doc)
        doc["report"] = {}
        self.assertRefused("quarantine", doc)

    def test_no_dump_time_or_one_in_the_future(self):
        doc = copy.deepcopy(self.doc)
        doc["source"]["dump_at"] = None
        self.assertRefused("when its dump was taken", doc)
        doc["source"]["dump_at"] = (timezone.now() + timedelta(days=1)).isoformat()
        self.assertRefused("future", doc)

    def test_a_package_of_another_schema(self):
        run = self._run()
        run.hospital = copy.copy(testhosp())
        run.hospital.schema_name = "h_another001"
        with self.assertRaisesRegex(engine.Refused, "real name only"):
            run._read_package()

    def test_an_erasure_ledger_refuses_every_restore(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(f"CREATE TABLE public.{engine.ERASURE_LEDGER} (id int)")
            with self.assertRaisesRegex(engine.Refused, "erasure"):
                self._run().precheck()
            transaction.set_rollback(True)

    def test_transaction_ids_beyond_this_cluster(self):
        doc = copy.deepcopy(self.doc)
        doc["facts"]["audit"]["max_entry_txid"] = str(10**15)
        self.assertRefused("rebase seal", doc)

    def test_an_audit_head_not_in_the_live_chain(self):
        doc = copy.deepcopy(self.doc)
        if not doc["facts"]["audit"]["seal_seq"]:
            self.skipTest("the test hospital has no seal yet")
        doc["facts"]["audit"]["seal_hash"] = "cd" * 32
        self.assertRefused("not of this hospital's history", doc)

    def test_a_dump_ahead_of_this_build(self):
        doc = copy.deepcopy(self.doc)
        doc["facts"]["migrations"].append(["ihr_audit", "9999_from_the_future"])
        self.assertRefused("ahead of this build", doc)

    def test_drill_only_on_the_drill_copy_and_never_live_there(self):
        self.assertRefused("restore-drill copy", drill=True)
        with override_settings(IHR_RESTORE_DRILL=True):
            self.assertRefused("only --drill")

    def test_only_active_or_suspended_hospitals(self):
        hospital = copy.copy(testhosp())
        hospital.status = Hospital.Status.OFFBOARDING
        self.assertRefused("only active or suspended", hospital=hospital)

    def test_a_hospital_held_by_another_restore(self):
        other = uuid.uuid4()
        release.restore_hold(testhosp(), other)
        self.addCleanup(release.lift_restore_hold, testhosp(), other)
        with self.assertRaisesRegex(engine.Refused, "held for restore"):
            self._run().precheck()

    def test_abdm_tables(self):
        doc = copy.deepcopy(self.doc)
        doc["tables"].append({"table": "abdm_healthid", "rows": 0})
        self.assertRefused("ABDM", doc)

    def test_not_enough_disk(self):
        self.assertRefused("not enough disk", free_bytes=1024)

    def test_an_unfinished_restore_must_be_resumed_first(self):
        op = TenantOperation.objects.create(
            kind=TenantOperation.Kind.RESTORE,
            hospital=testhosp(),
            slug=testhosp().slug,
            status=TenantOperation.Status.FAILED,
            requested_by="tester",
        )
        self.addCleanup(op.delete)
        run = self._run()
        with self.assertRaisesRegex(engine.Refused, "unfinished"):
            run.start()

    def test_a_package_whose_tables_are_not_the_live_schemas(self):
        """Review S1: with the same migrations as the live schema, the package's tables,
        defaults, constraints and indexes must be the live schema's exactly (a dump's DDL is
        never trusted); the tables that differ are named."""
        doc = copy.deepcopy(self.doc)
        self.assertEqual(self._run().precheck()["shape"], "the live schema's")
        doc["shape"] = "0" * 64
        doc["shape_tables"]["ihr_audit_entry"] = "f" * 16
        self.assertRefused("not the live schema's.*ihr_audit_entry", doc)

    def test_a_dump_too_old_for_the_strict_merge(self):
        """A dump of an older build whose tables lack a column the strict merge of security
        state needs is refused before anything is done (it used to fail half-way through the
        restore step); the column is named."""
        doc = copy.deepcopy(self.doc)
        doc["facts"]["migrations"] = doc["facts"]["migrations"][:-1]  # an older build
        users = next(t for t in doc["tables"] if t["table"] == "users_user")
        users["columns"] = [c for c in users["columns"] if c != "deleted"]
        self.assertRefused(r"older than the strict merge.*users_user\.deleted", doc)

    def test_a_busy_hospital_is_refused_before_any_operation(self):
        """Review C2: its lock held (a nightly dump): refused, and no operation is left."""
        from ihr_platform.provisioning import runner

        with runner.exclusive(testhosp().slug):
            before = TenantOperation.objects.filter(kind="restore").count()
            with self.assertRaisesRegex(engine.Refused, "busy"):
                self._run().start()
            self.assertEqual(
                TenantOperation.objects.filter(kind="restore").count(), before
            )

    def test_a_lock_failure_is_no_step_and_leaves_it_abortable(self):
        """Review C2: a resume that finds the lock held records no made-up step; the
        operation can still be aborted (as can one recorded so by an older build)."""
        from ihr_platform.provisioning import runner

        op = TenantOperation.objects.create(
            kind=TenantOperation.Kind.RESTORE,
            hospital=testhosp(),
            slug=testhosp().slug,
            status=TenantOperation.Status.FAILED,
            requested_by="tester",
            input={"package": str(self.dir / "pkg"), "reason": "test refusals"},
            steps=[{"name": "precheck", "status": "done"}]
            + [{"name": n, "status": "pending"} for n in engine.STEPS[1:]],
            result={"precheck": {"dump_at": timezone.now().isoformat()}},
        )
        self.addCleanup(op.delete)
        errors = []
        with runner.exclusive(testhosp().slug):
            run = engine.Restore(
                engine.Options(slug="", operator="tester", err=errors.append)
            )
            with self.assertRaises(engine.StepFailed):
                run.resume(op.pk)
        op.refresh_from_db()
        self.assertLessEqual({s["name"] for s in op.steps}, set(engine.STEPS))
        self.assertEqual(op.status, "failed")
        self.assertIn("lock busy", op.result["last_error"])
        self.assertIn(
            f"ihr-restore: hospital={testhosp().slug} outcome=failed step=lock", errors
        )
        self.assertEqual(release.restore_holder(testhosp()), "")
        op.steps = [*op.steps, {"name": "lock", "status": "failed"}]  # an older build's
        op.save(update_fields=["steps"])
        engine.Restore(engine.Options(slug="", err=errors.append)).abort(op.pk)
        op.refresh_from_db()
        self.assertEqual(op.status, "rolled_back")

    def test_a_lost_schema_is_never_restored_over(self):
        """Review C8: neither the live nor the parked schema there: the restore step stops."""
        run = self._run()
        run.hospital = copy.copy(testhosp())
        run.hospital.schema_name = "h_nothere001"
        run.op = TenantOperation(
            result={"parked": "ihr_parked_h_nothere001_20260101T000000"}
        )
        with self.assertRaisesRegex(engine.StepFailed, "lost"):
            run._restore()

    def test_the_package_must_be_the_quarantines(self):
        """Review S2: package.json must hash to what the quarantine printed."""
        run = self._run()
        printed = package.read(self.dir / "pkg")["package_sha256"]
        self.assertEqual(run._read_package(printed)["schema"], TEST_SCHEMA)
        with self.assertRaisesRegex(engine.Refused, "not the one the quarantine wrote"):
            run._read_package("0" * 64)

    def test_a_failure_in_the_unlogged_one_off_is_reported_once(self):
        """Review C9: a step that failed in the admin one-off (no log) gets its alert line from
        the next logged run, once."""
        op = TenantOperation.objects.create(
            kind=TenantOperation.Kind.RESTORE,
            hospital=testhosp(),
            slug=testhosp().slug,
            status=TenantOperation.Status.FAILED,
            requested_by="tester",
            steps=[
                {"name": "keycloak", "status": "failed", "error": "x", "logged": False}
            ],
        )
        self.addCleanup(op.delete)
        lines = []
        run = engine.Restore(engine.Options(slug="", err=lines.append))
        self.assertEqual(run.report_failures(op.pk), 1)
        self.assertEqual(
            lines,
            [f"ihr-restore: hospital={testhosp().slug} outcome=failed step=keycloak"],
        )
        self.assertEqual(run.report_failures(op.pk), 0)
        op.refresh_from_db()
        self.assertTrue(op.steps[0]["logged"])
        self.assertEqual(op.steps[0]["error"], "x")

    def test_reason_and_operator_are_required(self):
        run = self._run()
        run.options.reason = "short"
        with self.assertRaisesRegex(engine.Refused, "reason"):
            run.start()
        run = self._run()
        run.options.operator = "bad name!"
        with self.assertRaisesRegex(engine.Refused, "operator"):
            run.start()


class RestoreHoldTests(SimpleTestCase):
    """The restore hold (After the attack 4): every check that serves a hospital refuses it, and
    a release, the fleet runner, beat, pack apply and the inactive sealer leave it alone."""

    databases = {"default"}

    def setUp(self):
        owner_for_test(self)
        connection.set_schema_to_public()
        self.addCleanup(connection.set_schema, TEST_SCHEMA)
        self.hospital = testhosp()
        self.op = uuid.uuid4()
        release.restore_hold(self.hospital, self.op)
        self.addCleanup(release.lift_restore_hold, self.hospital, self.op)

    def test_a_held_hospital_is_not_served_anywhere(self):
        from ihr_platform.beat import ActiveTenantScheduler

        self.assertFalse(release.schema_ready(self.hospital))
        self.assertFalse(release.servable(self.hospital))
        self.assertFalse(
            Hospital.objects.filter(pk=self.hospital.pk)
            .filter(release.ready_q())
            .exists()
        )
        self.assertNotIn(self.hospital, list(ActiveTenantScheduler.get_queryset()))
        response = self.client.get("/api/ihr/profile/", HTTP_HOST="testserver")
        self.assertEqual(response.status_code, 503)

    def test_a_release_never_lifts_it_and_another_operation_cannot(self):
        release.hold([self.hospital.pk], reason=release.HOLD_WINDOW)
        release.release_hold([self.hospital.pk])
        self.assertEqual(release.restore_holder(self.hospital), str(self.op))
        self.assertEqual(release.lift_restore_hold(self.hospital, uuid.uuid4()), 0)
        with self.assertRaises(release.RestoreHeldError):
            release.restore_hold(self.hospital, uuid.uuid4())
        self.assertFalse(release.schema_ready(self.hospital))

    def test_the_fleet_runner_leaves_it_alone(self):
        out, err = [], []
        with mock.patch.object(fleet, "INLINE", new=True):
            run = fleet.Fleet(
                ["hold", "release"],
                operator="tester",
                slugs=[self.hospital.slug],
                parallel=1,
                out=out.append,
                err=err.append,
            ).run()
        self.addCleanup(FleetCleanup(run))
        state = HospitalSchemaState.objects.get(pk=self.hospital.pk)
        self.assertEqual(str(state.restore_operation), str(self.op))
        self.assertIsNone(state.upgrading_since)  # the window's hold never touched it
        op = run.operations.get()
        self.assertEqual(op.result["outcome"], "held")
        self.assertTrue(any("held for restore" in line for line in out))

    def test_the_watch_and_the_inactive_sealer_see_a_restore(self):
        from ihr_audit import tasks, watch

        [found] = [h for h in watch._hospitals() if h.uuid == str(self.hospital.uuid)]
        self.assertEqual(found.hold, watch.HOLD_RESTORE)
        self.assertFalse(found.ready)
        Hospital.objects.filter(pk=self.hospital.pk).update(
            status=Hospital.Status.SUSPENDED
        )
        self.addCleanup(
            Hospital.objects.filter(pk=self.hospital.pk).update,
            status=Hospital.Status.ACTIVE,
        )
        with mock.patch.object(tasks, "seal_once") as sealer:
            tasks.seal_inactive_hospitals()
        self.assertNotIn(TEST_SCHEMA, [c.args[0] for c in sealer.call_args_list])

    def test_operator_commands_refuse_it(self):
        from django.core.management.base import CommandError

        with self.assertRaisesRegex(CommandError, "held for a restore"):
            release.require_ready(self.hospital)

    def test_one_lock_held_once(self):
        from ihr_platform.provisioning import runner

        with runner.exclusive_for(self.hospital.slug, self.op):
            with runner.exclusive(
                self.hospital.slug
            ):  # the same process, the same operation
                self.assertEqual(runner.held_for(self.hospital.slug), str(self.op))
            with (
                self.assertRaises(runner.ProvisioningError),
                runner.exclusive_for(self.hospital.slug, uuid.uuid4()),
            ):
                pass
        self.assertFalse(runner.is_busy(self.hospital.slug))


class DrainTests(SimpleTestCase):
    """Review C4: a background job of the hospital still running (it holds the hospital's
    job lock, shared, for its whole run) stops the drain; once it has ended, the drain goes on."""

    databases = {"default"}

    def setUp(self):
        owner_for_test(self)
        connection.set_schema_to_public()
        self.addCleanup(connection.set_schema, TEST_SCHEMA)

    def test_the_drain_waits_for_running_jobs(self):
        run = engine.Restore(
            engine.Options(slug=testhosp().slug, drain_seconds=0, out=lambda line: None)
        )
        run.hospital = testhosp()
        run.op = TenantOperation(result={"held_since": timezone.now().isoformat()})
        job = connections.create_connection("default")
        self.addCleanup(job.close)
        with job.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_lock_shared(%s)", [release.job_key(TEST_SCHEMA)]
            )
        with self.assertRaisesRegex(engine.StepFailed, "still running"):
            run._drain()
        with job.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_unlock_shared(%s)", [release.job_key(TEST_SCHEMA)]
            )
        self.assertEqual(run._drain()["still_open"], 0)

    def test_a_job_holds_the_lock_while_it_runs(self):
        with release.job(TEST_SCHEMA), connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND pid = pg_backend_pid() AND mode = 'ShareLock'"
            )
            self.assertEqual(cursor.fetchone()[0], 1)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND pid = pg_backend_pid()"
            )
            self.assertEqual(cursor.fetchone()[0], 0)


class JobLockTests(TestCase):
    """Review C4, the jobs' side: every background job of a hospital (a CARE Celery task, the
    staff worker's invitation and access-request jobs) holds that hospital's job lock while
    its body runs, so the drain waits for it; and none waits for the lock while a drain holds
    it or waits for it (a worker slot serves every hospital): it is deferred at once."""

    def setUp(self):
        self.hospital = testhosp()
        self.key = release.job_key(TEST_SCHEMA)
        self.addCleanup(connection.set_schema, TEST_SCHEMA)
        # Only this test's staff work is due (rolled back afterwards)
        StaffInvitation.objects.filter(status__in=["queued", "sent"]).update(
            status=StaffInvitation.Status.CANCELLED
        )
        StaffAccessRequest.objects.filter(status="queued").update(status="done")
        with connection.cursor() as cursor:  # a job that waited would fail, not hang
            cursor.execute("SET LOCAL lock_timeout = '3s'")

    def held(self):
        """Whether this connection holds the hospital's job lock, shared."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted "
                "AND pid = pg_backend_pid() AND mode = 'ShareLock' AND classid = %s "
                "AND objid = %s AND objsubid = 1",
                [(self.key >> 32) & 0xFFFFFFFF, self.key & 0xFFFFFFFF],
            )
            return cursor.fetchone()[0] == 1

    def _task(self, body):
        """A CARE task (tenant_schemas_celery's class, so the gate is in front of it) run
        by a worker in the test hospital: (call, the deferral mock)."""
        from config.celery_app import app

        @app.task(name=f"ihr_platform.tests.restore_job_probe_{uuid.uuid4().hex[:8]}")
        def job_probe():
            return body()

        probe = app.tasks[job_probe.name]
        again = mock.MagicMock()
        defer = mock.patch.object(probe, "signature_from_request", return_value=again)

        def call():
            connection.set_tenant(self.hospital)
            probe.push_request(id="t", args=(), kwargs={}, retries=0, is_eager=False)
            try:
                return probe()
            finally:
                probe.pop_request()

        return call, defer, again

    def _work(self):
        inv = StaffInvitation.objects.create(
            hospital=self.hospital,
            care_user_id=987655,
            email="job-lock@example.test",
            first_name="Job",
            invited_by="test",
            attempts=1,
        )
        req = StaffAccessRequest.objects.create(
            hospital=self.hospital,
            care_user_id=987655,
            action=StaffAccessRequest.Action.RESET_SIGN_IN,
            requested_by="test",
        )
        return inv, req

    def test_each_job_holds_its_hospitals_lock_while_it_runs(self):
        from ihr_platform import invitations

        seen = []
        call, _, _ = self._task(lambda: seen.append(("task", self.held())))
        call()
        inv, _ = self._work()
        connection.set_schema_to_public()
        with (
            mock.patch.object(invitations, "claim_next", return_value=inv),
            mock.patch.object(
                invitations,
                "deliver",
                side_effect=lambda inv: seen.append(("invitation", self.held())),
            ),
        ):
            self.assertTrue(invitations.deliver_once())
        with mock.patch.object(
            invitations,
            "handle_request",
            side_effect=lambda req: seen.append(("request", self.held())),
        ):
            self.assertTrue(invitations.handle_request_once())
        self.assertEqual(
            seen, [("task", True), ("invitation", True), ("request", True)]
        )
        self.assertFalse(self.held(), "a job left its lock behind")

    def test_no_job_waits_for_a_drain(self):
        from ihr_platform import invitations

        ran = []
        call, defer, again = self._task(lambda: ran.append("task"))
        inv, req = self._work()
        drain = connections.create_connection("default")
        self.addCleanup(drain.close)
        with drain.cursor() as cursor:  # the drain holds the lock
            cursor.execute("SET lock_timeout = '10s'")
            cursor.execute("SELECT pg_advisory_lock(%s)", [self.key])
        with release.job(TEST_SCHEMA) as taken:
            self.assertFalse(taken)
        self.assertFalse(self.held())
        with defer as sent, self.assertRaises(celery_gate.Ignore):
            call()
        sent.assert_called_once()
        again.apply_async.assert_called_once_with()
        connection.set_schema_to_public()
        with (
            mock.patch.object(invitations, "claim_next", return_value=inv),
            mock.patch.object(
                invitations, "deliver", side_effect=lambda inv: ran.append("invitation")
            ),
        ):
            self.assertTrue(invitations.deliver_once())
        inv.refresh_from_db()
        # Not an attempt, not failed: tried again later
        self.assertEqual((inv.status, inv.attempts), (StaffInvitation.Status.QUEUED, 0))
        with mock.patch.object(
            invitations, "handle_request", side_effect=lambda req: ran.append("request")
        ):
            self.assertTrue(invitations.handle_request_once())
        req.refresh_from_db()
        self.assertEqual(req.status, StaffAccessRequest.Status.QUEUED)
        self.assertEqual(ran, [])
        with drain.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [self.key])

    def test_no_job_queues_behind_a_waiting_drain(self):
        """The drain waits (an older job still holds the lock): Postgres would queue a new
        shared request behind it; the job is told at once instead."""
        import threading

        older = connections.create_connection("default")
        self.addCleanup(older.close)
        with older.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock_shared(%s)", [self.key])
        done = []

        def drain():
            waiting = connections.create_connection("default")
            try:
                with waiting.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '20s'")
                    cursor.execute("SELECT pg_advisory_lock(%s)", [self.key])
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [self.key])
                done.append(True)
            finally:
                waiting.close()

        thread = threading.Thread(target=drain)
        thread.start()
        try:
            for _ in range(100):
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                        "AND NOT granted AND classid = %s AND objid = %s",
                        [(self.key >> 32) & 0xFFFFFFFF, self.key & 0xFFFFFFFF],
                    )
                    if cursor.fetchone()[0]:
                        break
                time.sleep(0.05)
            else:
                self.fail("the drain never waited")
            began = time.monotonic()
            try:
                with release.job(TEST_SCHEMA) as taken:
                    self.assertFalse(taken)
            except OperationalError:  # lock_timeout: it queued behind the drain
                self.fail("a job waited for the drain")
            self.assertLess(time.monotonic() - began, 1)
        finally:
            with older.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock_shared(%s)", [self.key])
            thread.join(30)
        self.assertEqual(done, [True])
        with release.job(TEST_SCHEMA) as taken:
            self.assertTrue(taken)
            self.assertTrue(self.held())


class FleetCleanup:
    def __init__(self, run):
        self.run = run

    def __call__(self):
        from ihr_platform.models import FleetRun

        with as_owner():
            if self.run is not None:
                TenantOperation.objects.filter(fleet_run=self.run).delete()
                FleetRun.objects.filter(pk=self.run.pk).delete()
            release.release_hold([testhosp().pk])


class PackageTests(SimpleTestCase):
    """The package's own checks: files by sha256, statements by form, schema contents."""

    databases = {"default"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dir = Path(tempfile.mkdtemp(prefix="restore-package-"))
        cls.addClassCleanup(shutil.rmtree, cls.dir, ignore_errors=True)
        connection.set_schema_to_public()
        cls.addClassCleanup(connection.set_schema, TEST_SCHEMA)
        cls.doc = make_package(TEST_SCHEMA, cls.dir / "pkg")

    def setUp(self):
        owner_for_test(self)
        self.pkg = self.dir / f"copy-{uuid.uuid4().hex[:8]}"
        shutil.copytree(self.dir / "pkg", self.pkg)

    def test_a_changed_data_file_is_refused(self):
        item = self.doc["tables"][0]
        with (self.pkg / item["file"]).open("ab") as f:
            f.write(b"1\t2\n")
        with self.assertRaisesRegex(
            package.PackageError, "not the one package.json names"
        ):
            package.read(self.pkg)

    def test_a_statement_of_another_form_is_refused(self):
        ddl = json.loads((self.pkg / "ddl.json").read_text())
        ddl["post"].append(
            f'CREATE FUNCTION "{TEST_SCHEMA}".x() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql'
        )
        (self.pkg / "ddl.json").write_text(json.dumps(ddl))
        doc = json.loads((self.pkg / "package.json").read_text())
        doc["ddl_sha256"] = package._sha(self.pkg / "ddl.json")
        (self.pkg / "package.json").write_text(json.dumps(doc))
        with self.assertRaises(package.PackageError):
            package._check_statement("index", ddl["post"][-1], TEST_SCHEMA)
        fk = (
            f'ALTER TABLE ONLY "{TEST_SCHEMA}"."t" ADD CONSTRAINT "x" FOREIGN KEY (a) '
            "REFERENCES public.ihr_platform_hospital(id)"
        )
        with self.assertRaisesRegex(package.PackageError, "another schema"):
            package._check_statement("constraint", fk, TEST_SCHEMA)

    def test_nothing_but_tables_sequences_and_ours_in_a_schema(self):
        name = "h_quartest01"
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(f"CREATE SCHEMA {name}")
                cursor.execute(f"CREATE TABLE {name}.t (id int PRIMARY KEY, v text)")
                cursor.execute(
                    f"CREATE FUNCTION {name}.evil() RETURNS trigger LANGUAGE plpgsql "
                    "AS $$ BEGIN RETURN NEW; END $$"
                )
                cursor.execute(
                    f"CREATE TRIGGER evil BEFORE INSERT ON {name}.t FOR EACH ROW "
                    f"EXECUTE FUNCTION {name}.evil()"
                )
                cursor.execute(f"CREATE VIEW {name}.v AS SELECT 1 AS one")
                cursor.execute(
                    f"CREATE FUNCTION {name}.f() RETURNS text LANGUAGE sql AS $$ SELECT 'x' $$"
                )
                cursor.execute(
                    f"ALTER TABLE {name}.t ALTER COLUMN v SET DEFAULT {name}.f()"
                )
                cursor.execute(f"CREATE TYPE {name}.mood AS ENUM ('a')")
                problems = package.schema_problems(cursor.cursor, name)
            transaction.set_rollback(True)
        text = "\n".join(problems)
        for expected in (
            "a function that is not ours: evil",
            "a trigger that is not ours: t.evil",
            "a relation of kind 'v'",
            "a type of its own: mood",
            f"an expression calls {name}.f",
        ):
            self.assertIn(expected, text)

    def test_the_test_hospital_itself_is_clean(self):
        with connection.cursor() as cursor:
            self.assertEqual(package.schema_problems(cursor.cursor, TEST_SCHEMA), [])

    # --- a forged package (review S1, S2) ------------------------------------------------------

    FORGED, VICTIM = "h_forgery001", "h_victim0001"

    def _forgeable(self):
        """A small schema's package (and a victim sequence of another schema at 5), in a
        transaction the caller rolls back; the schema itself dropped so it can be loaded."""
        out = self.dir / f"forged-{uuid.uuid4().hex[:8]}"
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA {self.FORGED}")
            cursor.execute(
                f"CREATE TABLE {self.FORGED}.t (id bigint GENERATED BY DEFAULT AS IDENTITY "
                "PRIMARY KEY, v int NOT NULL DEFAULT 1 CHECK (v > 0), note text)"
            )
            cursor.execute(f"CREATE INDEX t_v ON {self.FORGED}.t (v)")
            cursor.execute(
                f"INSERT INTO {self.FORGED}.t (v) SELECT g FROM generate_series(1, 5) g"
            )
            cursor.execute(f"CREATE SCHEMA {self.VICTIM}")
            cursor.execute(f"CREATE SEQUENCE {self.VICTIM}.s")
            cursor.execute(f"SELECT setval('{self.VICTIM}.s', 5)")
        doc = package.export(raw(), self.FORGED, out, snapshot=False)
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA {self.FORGED} CASCADE")
        return out, doc

    def _forge(self, out, change):
        ddl = json.loads((out / "ddl.json").read_text())
        change(ddl)
        (out / "ddl.json").write_text(json.dumps(ddl))
        doc = json.loads((out / "package.json").read_text())
        doc["ddl_sha256"] = package._sha(out / "ddl.json")
        (out / "package.json").write_text(json.dumps(doc))

    def _victim(self):
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT last_value FROM {self.VICTIM}.s")
            return cursor.fetchone()[0]

    def test_the_small_package_loads(self):
        with transaction.atomic():
            out, _ = self._forgeable()
            result = package.load(raw(), out, self.FORGED)
            self.assertEqual(result["rows"], 5)
            transaction.set_rollback(True)

    def test_a_forged_check_is_inspected_before_anything_evaluates_it(self):
        """A CHECK that would reset another hospital's sequence (setval is not undone by a
        roll-back): refused before any row meets it, and the sequence is untouched."""
        victim = f"pg_catalog.setval('{self.VICTIM}.s'::regclass, (1)::bigint)"

        def change(ddl):
            ddl["post"] = [
                t.replace("CHECK ((v > 0))", f"CHECK (((v > 0) AND ({victim} > 0)))")
                for t in ddl["post"]
            ]
            self.assertIn(victim, json.dumps(ddl))

        with transaction.atomic():
            out, _ = self._forgeable()
            self._forge(out, change)
            with self.assertRaisesRegex(
                package.PackageError, r"calls pg_catalog\.setval \(volatile\)"
            ):
                package.load(raw(), out, self.FORGED)
            self.assertEqual(self._victim(), 5, "the forged CHECK ran")
            transaction.set_rollback(True)

    def test_a_forged_default_that_reads_another_hospital_is_refused(self):
        """A default every insert would run (an audit entry's, in the review's case) that reads
        another schema's rows through query_to_xml: refused at the load."""

        def change(ddl):
            ddl["pre"] = [
                t.replace(
                    '"note" text',
                    "\"note\" text DEFAULT (pg_catalog.query_to_xml('select 1'::text, true, "
                    "false, ''::text))::text",
                )
                for t in ddl["pre"]
            ]

        with transaction.atomic():
            out, _ = self._forgeable()
            self._forge(out, change)
            with self.assertRaisesRegex(package.PackageError, "query_to_xml"):
                package.load(raw(), out, self.FORGED)
            transaction.set_rollback(True)

    def test_a_default_on_another_schemas_sequence_is_refused(self):
        def change(ddl):
            ddl["pre"] = [
                t.replace(
                    '"note" text',
                    f"\"note\" text DEFAULT (pg_catalog.nextval('{self.VICTIM}.s'::regclass))::text",
                )
                for t in ddl["pre"]
            ]

        with transaction.atomic():
            out, _ = self._forgeable()
            self._forge(out, change)
            with self.assertRaisesRegex(
                package.PackageError, "refers to h_victim0001.s"
            ):
                package.load(raw(), out, self.FORGED)
            self.assertEqual(self._victim(), 5)
            transaction.set_rollback(True)

    def test_a_second_statement_in_a_package_never_runs(self):
        """Review S2: text after a table's or an index's statement is refused before anything
        runs, and the server itself refuses a second command (extended protocol)."""
        tail = f"; SELECT pg_catalog.setval('{self.VICTIM}.s', 1)"
        for phase in ("pre", "post"):
            with self.subTest(phase), transaction.atomic():
                out, _ = self._forgeable()

                def change(ddl, phase=phase):
                    index = next(
                        i
                        for i, t in enumerate(ddl[phase])
                        if t.startswith(("CREATE TABLE", "CREATE INDEX"))
                    )
                    ddl[phase][index] += tail

                self._forge(out, change)
                with self.assertRaisesRegex(package.PackageError, "not one statement"):
                    package.load(raw(), out, self.FORGED)
                self.assertEqual(self._victim(), 5)
                transaction.set_rollback(True)
        import psycopg

        with transaction.atomic():
            out, _ = self._forgeable()
            with self.assertRaises(psycopg.Error), transaction.atomic():
                package._execute(raw(), f"SELECT 1{tail}")
            self.assertEqual(self._victim(), 5, "the second command ran")
            transaction.set_rollback(True)

    def test_the_trail_defaults_are_the_migrations(self):
        """Review S1: the defaults install sets are a migrated schema's exactly (it changes
        nothing there), and it puts back one a dump changed."""
        from ihr_audit import install

        with transaction.atomic(), connection.cursor() as cursor:
            before = package.shape(cursor.cursor, TEST_SCHEMA)[0]
            install.ensure_defaults(cursor, TEST_SCHEMA)
            self.assertEqual(package.shape(cursor.cursor, TEST_SCHEMA)[0], before)
            cursor.execute(
                f'ALTER TABLE "{TEST_SCHEMA}".ihr_audit_entry ALTER COLUMN tbl '
                "SET DEFAULT pg_catalog.current_database()"
            )
            self.assertNotEqual(package.shape(cursor.cursor, TEST_SCHEMA)[0], before)
            install.ensure_defaults(cursor, TEST_SCHEMA)
            self.assertEqual(package.shape(cursor.cursor, TEST_SCHEMA)[0], before)
            transaction.set_rollback(True)

    def test_others_unchanged_also_means_their_tables_and_publics_functions(self):
        """Review C7: a CHECK dropped in another hospital, or a public function's body
        changed, changes the drill's "others unchanged" hash."""
        with transaction.atomic(), connection.cursor() as cursor:
            first = package.fingerprint(cursor.cursor, [TEST_SCHEMA], (0, uuid.uuid4()))
            cursor.execute(
                f'ALTER TABLE "{TEST_SCHEMA}".ihr_audit_seal DROP CONSTRAINT '
                "ihr_audit_seal_entries_check"
            )
            second = package.fingerprint(
                cursor.cursor, [TEST_SCHEMA], (0, uuid.uuid4())
            )
            self.assertNotEqual(first[TEST_SCHEMA], second[TEST_SCHEMA])
            cursor.execute(
                "SELECT pg_get_functiondef('public.ihr_operation_guard'::regproc)"
            )
            body = cursor.fetchone()[0]
            cursor.execute(body.replace("BEGIN", "BEGIN\n    PERFORM 1;", 1))
            third = package.fingerprint(cursor.cursor, [TEST_SCHEMA], (0, uuid.uuid4()))
            self.assertNotEqual(second["public"], third["public"])
            transaction.set_rollback(True)

    def test_the_final_seal_follows_the_sealing_function(self):
        """Review S5: the restore's final seal is built from the sealing function's own SQL
        (it raises if that changed in a way it does not follow)."""
        from ihr_audit import seal

        text = seal.final_sql(TEST_SCHEMA).as_string(raw())
        self.assertIn("pg_temp.ihr_audit_seal_final", text)
        self.assertIn("pg_snapshot_xmax", text)
        self.assertNotIn("pg_try_advisory_xact_lock", text)

    # --- after the second verification ----------------------------------------------------------

    def test_a_constraint_statement_carries_nothing_after_its_constraint(self):
        """Review S2 (residual): ALTER TABLE takes several subcommands. One after a key (which
        runs as the owner after the rows and the inspection) is refused before anything runs:
        a USING that would run for every row (setval is not undone by a roll-back), an owner,
        triggers switched off."""
        victim = f"pg_catalog.setval('{self.VICTIM}.s'::regclass, (1)::bigint)"
        for tail in (
            f', ALTER COLUMN "note" TYPE text USING (({victim})::text)',
            ", OWNER TO ihr_app",
            ", DISABLE TRIGGER ALL",
        ):
            with self.subTest(tail), transaction.atomic():
                out, _ = self._forgeable()

                def change(ddl, tail=tail):
                    index = next(
                        i for i, t in enumerate(ddl["post"]) if " PRIMARY KEY (" in t
                    )
                    ddl["post"][index] += tail

                self._forge(out, change)
                with self.assertRaisesRegex(
                    package.PackageError, "PRIMARY KEY constraint not of the form"
                ):
                    package.load(raw(), out, self.FORGED)
                self.assertEqual(self._victim(), 5, "the extra subcommand ran")
                transaction.set_rollback(True)

    def test_nextval_of_a_sequence_named_at_run_time_is_refused(self):
        """Review S1 (residual): a sequence named at run time (a cast from name, or from an
        oid) leaves no dependency behind, so the reference check cannot see it is another
        hospital's: refused. nextval takes only a regclass constant."""
        for name, argument in (
            ("name", f"('{self.VICTIM}.s'::pg_catalog.name)::pg_catalog.regclass"),
            ("oid", "((%s)::pg_catalog.oid)::pg_catalog.regclass"),
        ):
            with self.subTest(name), transaction.atomic():
                out, _ = self._forgeable()
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT '{self.VICTIM}.s'::regclass::oid")
                    oid = cursor.fetchone()[0]
                default = argument % oid if "%s" in argument else argument

                def change(ddl, default=default):
                    ddl["pre"] = [
                        t.replace(
                            '"note" text',
                            f'"note" text DEFAULT (pg_catalog.nextval({default}))::text',
                        )
                        for t in ddl["pre"]
                    ]

                self._forge(out, change)
                with self.assertRaisesRegex(
                    package.PackageError, "nextval on a sequence named at run time"
                ):
                    package.load(raw(), out, self.FORGED)
                self.assertEqual(self._victim(), 5)
                transaction.set_rollback(True)

    def test_an_operator_class_of_another_schema_is_refused_before_the_rows(self):
        """An index naming an operator class is built on the empty table and inspected
        first (its functions run for every row): only a built-in or an extension's."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            if not cursor.fetchone()[0]:
                self.skipTest(
                    "an operator class needs a superuser (the run with roles off)"
                )

        def change(ddl):
            ddl["post"].append(
                f'CREATE INDEX t_v_ops ON "{self.FORGED}"."t" USING btree '
                f"(v {self.VICTIM}.int_ops)"
            )

        with transaction.atomic():
            out, _ = self._forgeable()
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE OPERATOR CLASS {self.VICTIM}.int_ops FOR TYPE int4 USING btree "
                    "AS OPERATOR 1 <, OPERATOR 2 <=, OPERATOR 3 =, OPERATOR 4 >=, "
                    "OPERATOR 5 >, FUNCTION 1 btint4cmp(int4, int4)"
                )
            self._forge(out, change)
            self.assertEqual(
                package._post(
                    f'CREATE INDEX t_v_ops ON "{self.FORGED}"."t" '
                    f"USING btree (v {self.VICTIM}.int_ops)",
                    self.FORGED,
                )[0],
                "early",
            )
            with self.assertRaisesRegex(
                package.PackageError, f"operator class {self.VICTIM}.int_ops"
            ):
                package.load(raw(), out, self.FORGED)
            transaction.set_rollback(True)

    def test_the_live_compare_sees_any_ddl_elsewhere_and_only_that(self):
        """Review C7 and the second verification: inside the load's transaction the other
        schemas' and public's catalogue rows are compared by version (cheap at any fleet
        size). Rows written anywhere change nothing; DDL in another hospital or in public is
        named; a hospital schema that appeared meanwhile, or one held by another restore,
        is not this load's doing and never stops it."""
        run = engine.Restore(engine.Options(slug="x"))
        run.hospital = Hospital(pk=0, schema_name=self.FORGED)
        with transaction.atomic(), connection.cursor() as cursor:
            before = package.catalogue_versions(cursor.cursor, self.FORGED)
            self.assertIn(TEST_SCHEMA, before)
            self.assertIn("public", before)
            self.assertNotIn(self.FORGED, before)
            cursor.execute(
                f'UPDATE "{TEST_SCHEMA}".django_migrations SET applied = applied '
                "WHERE id = (SELECT min(id) FROM "
                f'"{TEST_SCHEMA}".django_migrations)'
            )
            cursor.execute(f'ANALYZE "{TEST_SCHEMA}".django_migrations')
            cursor.execute("CREATE SCHEMA h_appeared01")
            cursor.execute("CREATE TABLE h_appeared01.t (x int)")
            self.assertEqual(run._others_changed(cursor, before), ([], len(before) + 1))
            for ddl in (
                f'ALTER TABLE "{TEST_SCHEMA}".ihr_audit_seal DISABLE TRIGGER USER',
                f'GRANT SELECT ON "{TEST_SCHEMA}".django_migrations TO PUBLIC',
                f'ALTER TABLE "{TEST_SCHEMA}".django_migrations ALTER COLUMN app '
                "SET DEFAULT 'x'",
            ):
                with self.subTest(ddl), transaction.atomic():
                    cursor.execute(ddl)
                    self.assertEqual(
                        run._others_changed(cursor, before)[0], [TEST_SCHEMA]
                    )
                    transaction.set_rollback(True)
            cursor.execute(
                "SELECT pg_get_functiondef('public.ihr_operation_guard'::regproc)"
            )
            body = cursor.fetchone()[0]
            cursor.execute(body.replace("BEGIN", "BEGIN\n    PERFORM 1;", 1))
            cursor.execute(
                f'ALTER TABLE "{TEST_SCHEMA}".ihr_audit_seal DISABLE TRIGGER USER'
            )
            self.assertEqual(
                run._others_changed(cursor, before)[0], [TEST_SCHEMA, "public"]
            )
            release.restore_hold(testhosp(), uuid.uuid4())  # another restore's hospital
            self.assertEqual(run._others_changed(cursor, before)[0], ["public"])
            transaction.set_rollback(True)

    def test_the_restore_gives_the_trail_this_builds_defaults(self):
        """Review C8: the restore step installs the trail's column defaults from code
        (ihr_audit.install.ensure_defaults), whatever the dump's were."""
        run = engine.Restore(engine.Options(slug="x"))
        with transaction.atomic(), connection.cursor() as cursor:
            before = package.shape(cursor.cursor, TEST_SCHEMA)[0]
            cursor.execute(
                f'ALTER TABLE "{TEST_SCHEMA}".ihr_audit_entry ALTER COLUMN tbl '
                "SET DEFAULT pg_catalog.current_database()"
            )
            run._install_trail(cursor, TEST_SCHEMA)
            self.assertEqual(package.shape(cursor.cursor, TEST_SCHEMA)[0], before)
            transaction.set_rollback(True)

    def test_an_older_builds_tables_are_merged_without_what_they_lack(self):
        """The strict merge on a dump of an older build: a flag or a cache its users table
        did not have yet is skipped and named; a merged column one copy lacks stops the
        merge, naming it (the precheck refuses such a dump first)."""
        restored, parked = "h_mergeold01", "h_mergenew01"
        run = engine.Restore(engine.Options(slug="x"))
        run.hospital = Hospital(schema_name=restored)
        run.op = TenantOperation(result={"parked": parked})
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA {parked}")
            cursor.execute(
                f"CREATE TABLE {parked}.users_user (id bigint PRIMARY KEY, is_active boolean, "
                "deleted boolean, is_superuser boolean, is_staff boolean, cached_role_orgs jsonb)"
            )
            cursor.execute(
                f"INSERT INTO {parked}.users_user VALUES "
                "(1, true, false, false, false, NULL), (2, false, false, false, false, NULL)"
            )
            cursor.execute(f"CREATE SCHEMA {restored}")
            cursor.execute(
                f"CREATE TABLE {restored}.users_user (id bigint PRIMARY KEY, "
                "is_active boolean, deleted boolean, is_superuser boolean)"
            )
            cursor.execute(
                f"INSERT INTO {restored}.users_user VALUES (1, true, false, true), "
                "(2, true, false, false)"
            )
            out = run._security(cursor)
            self.assertEqual(
                out["skipped"], ["users_user.is_staff", "users_user.cached_role_orgs"]
            )
            self.assertEqual(
                (out["users_deactivated"], out["is_superuser_removed"]), (1, 1)
            )
            cursor.execute(
                f"CREATE TABLE {parked}.security_rolepermission (role_id bigint, "
                "permission_id bigint, deleted boolean)"
            )
            cursor.execute(
                f"CREATE TABLE {restored}.security_rolepermission (role_id bigint, "
                "permission_id bigint)"
            )
            with self.assertRaisesRegex(
                engine.StepFailed, r"security_rolepermission\.deleted"
            ):
                run._security(cursor)
            transaction.set_rollback(True)

    def test_the_drill_stands_for_other_hospitals_rows_by_their_write_counters(self):
        """Review C7 (the cost): on the drill copy nothing else runs, so another hospital's
        rows are not read twice; each table's write counters and relfilenode stand for them.
        Reading changes nothing; an update (even to the same value), an insert, a delete, a
        TRUNCATE and a rolled-back insert each change the hash."""
        name = "h_counters01"
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA {name}")
        self.addCleanup(self._drop_schema, name)
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE TABLE {name}.t (id int PRIMARY KEY, v text)")
            cursor.execute(f"INSERT INTO {name}.t VALUES (1, 'a')")

        def hashed():
            with connection.cursor() as cursor:
                self.assertTrue(package.flush_write_counters(cursor.cursor))
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT pg_stat_clear_snapshot()")
                return package.fingerprint(cursor.cursor, [name], rows="writes")[name]

        first = hashed()
        self.assertEqual(hashed(), first)
        for write in (
            f"UPDATE {name}.t SET v = v WHERE id = 1",
            f"INSERT INTO {name}.t VALUES (2, 'b')",
            f"DELETE FROM {name}.t WHERE id = 2",
            f"TRUNCATE {name}.t",
        ):
            with self.subTest(write):
                with connection.cursor() as cursor:
                    cursor.execute(write)
                after = hashed()
                self.assertNotEqual(after, first)
                first = after
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(f"INSERT INTO {name}.t VALUES (3, 'c')")
            transaction.set_rollback(True)
        self.assertNotEqual(hashed(), first)

    def _drop_schema(self, name):
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
