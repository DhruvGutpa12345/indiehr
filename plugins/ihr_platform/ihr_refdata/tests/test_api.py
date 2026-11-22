"""The hospital admin's "updates available" API for reference packs (P1-T15b1;
docs/reviews/p1-t15b/design.md §2.8 and "After the attack" items 6, 15, 17).

Most tests run in the shared test hospital inside a transaction that is rolled back; the API
runs as the app role (``IHR_DB_ROLES=test``), fixtures that only owner-run processes write
(pack applies) inside ``as_owner()``. The commit-inside-the-lock test needs a second connection
to see committed state, so it is a SimpleTestCase on a hospital of its own with keys unique to
the run; the audit trail it writes is never cleaned up (append-only).
"""

import json
import time
from contextlib import contextmanager
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from care.emr.models import Organization
from care.emr.models.organization import OrganizationUser
from care.emr.models.valueset import ValueSet
from care.security.models import PermissionModel, RoleModel, RolePermission
from care.users.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django_tenants.utils import schema_context
from ihr_access.testing import build_world
from ihr_platform.models import Domain, Hospital
from ihr_platform.provisioning import runner
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_platform.testing import as_owner
from ihr_refdata_registry.models import PackRelease
from rest_framework.test import APIClient

from ihr_refdata import api, engine, valuesets
from ihr_refdata.models import PackApply, PackItem
from ihr_refdata.testing import TEST_PACK, Packs, audit_mark, audit_since, vs

HOST = "testserver"
SLUG = "testhosp"
URL = "/api/ihr/packs/"
REASON = "hospital admin OK, ticket 12"
DESCRIPTION = "Zz-Never-Shown-Description"
REFDATA_TABLES = {"ihr_refdata_packitem", "ihr_refdata_packapply"}


def hospital_admin(name, **organization):
    """A user with an admin role (can_create_user): on the hospital's root (P1-T25, made if the
    test schema has none; the test's transaction removes it), or with ``organization`` on a new
    organisation of that shape, which does not make anyone hospital-wide."""
    from ihr_identity import roots

    user = User.objects.create_user(username=name, password="x-Unused-1")
    role = RoleModel.objects.create(name=f"role-{name}")
    permission, _ = PermissionModel.objects.get_or_create(
        slug="can_create_user", defaults={"name": "can_create_user"}
    )
    RolePermission.objects.create(role=role, permission=permission)
    if organization:
        place = Organization.objects.create(name=f"org-{name}", **organization)
    else:
        place = roots.valid_root() or roots.ensure_root("test")[0]
    OrganizationUser.objects.create(organization=place, user=user, role=role)
    return user


def _tag():
    return f"{time.time_ns() % 10**12:x}"


def edit(slug, **fields):
    row = ValueSet._base_manager.get(slug=slug)
    for name, value in fields.items():
        setattr(row, name, value)
    row.save()
    return row


def apply(slug=SLUG):
    with as_owner():
        call_command(
            "ihr_pack_apply",
            "--slug",
            slug,
            "--operator",
            "tester",
            stdout=StringIO(),
            stderr=StringIO(),
        )


def row_entries(mark):
    return [e for e in audit_since(mark) if e["tbl"]]


def request_entries(mark):
    return [e for e in audit_since(mark) if not e["tbl"]]


class ApiCase(TestCase):
    """v1 and v2 of a test pack applied to the test hospital, leaving one item of each kind
    waiting: ihr-a and ihr-k edited (v2 changes them too), ihr-r
    edited and dropped by v2 (a removal), ihr-u there before any apply (unknown origin); ihr-x
    is current."""

    databases = {"default"}

    def setUp(self):
        self.packs = Packs(self)
        with as_owner():
            ValueSet._base_manager.all().delete()
            valuesets.create(vs("ihr-u", "9-9", name="Their Own U"))
            self.packs.add(
                1,
                *(vs(s, "1-8") for s in ("ihr-a", "ihr-k", "ihr-r", "ihr-u", "ihr-x")),
            )
        apply()
        with as_owner():
            for slug in ("ihr-a", "ihr-k", "ihr-r"):
                edit(slug, name=f"Edited {slug}", description=DESCRIPTION)
            self.packs.add(
                2,
                vs("ihr-a", "2-8"),
                vs("ihr-k", "2-8"),
                vs("ihr-u", "1-8"),
                vs("ihr-x", "1-8"),
            )
        apply()
        self.admin = hospital_admin(f"pk-admin-{_tag()}")
        self.api = self.client_for(self.admin)

    def tearDown(self):
        cache.delete(api._in_flight_key())

    def client_for(self, user):
        client = APIClient(HTTP_HOST=HOST)
        if user is not None:
            client.force_authenticate(user)
        return client

    def record(self, slug):
        return PackItem.objects.get(pack=TEST_PACK, item=slug)

    def detail(self, slug):
        response = self.api.get(f"{URL}updates/{self.record(slug).pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def decide(self, slug, action="accept", client=None, **changes):
        shown = self.detail(slug)
        body = {
            "action": action,
            "version": shown["pending_version"],
            "sha256": shown["pending_sha256"],
            "row_sha256": shown["row_sha256"],
            "reason": REASON,
            **changes,
        }
        return (client or self.api).post(
            f"{URL}updates/{shown['id']}/", body, format="json"
        )


class GateTests(ApiCase):
    @classmethod
    def setUpTestData(cls):
        cls.world = build_world()

    def test_only_a_hospital_wide_admin_may_call_it(self):
        pk = self.record("ihr-a").pk
        service = hospital_admin(f"pk-svc-{_tag()}")
        User.objects.filter(pk=service.pk).update(is_service_account=True)
        service.refresh_from_db()
        refused = {**self.world.users, "service account": service, "nobody": None}
        for who, user in refused.items():
            client = self.client_for(user)
            with self.subTest(who=who):
                for path in ("updates/", f"updates/{pk}/", "history/"):
                    self.assertIn(client.get(URL + path).status_code, (401, 403))
                # refused before the body is read: garbage gets 403, never 400
                answer = client.post(
                    f"{URL}updates/{pk}/", "x{", content_type="text/plain"
                )
                self.assertIn(answer.status_code, (401, 403))
        self.assertEqual(self.record("ihr-a").state, "pending")
        for path in ("updates/", f"updates/{pk}/", "history/"):
            self.assertEqual(self.api.get(URL + path).status_code, 200)

    def test_a_facility_admin_is_not_a_hospital_wide_admin(self):
        client = self.client_for(self.world.users["Facility Admin"])
        response = client.get(f"{URL}updates/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"], "Only a hospital-wide admin can do this."
        )

    def test_a_state_or_other_top_level_admin_does_not_decide(self):
        """Q-K76 B (P1-T25; Q-K120 superseded): an admin role on a state organisation (where
        provisioning put the first admin before P1-T25) or on any other top-level organisation
        than the root no longer opens the pack decisions."""
        for label, shape in (
            ("state", {"org_type": "govt"}),
            ("other top-level", {"org_type": "role"}),
        ):
            client = self.client_for(
                hospital_admin(f"pk-{label[:5]}-{_tag()}", **shape)
            )
            for path in ("updates/", "history/"):
                with self.subTest(admin=label, path=path):
                    self.assertEqual(client.get(URL + path).status_code, 403)

    def test_reads_write_no_audit_entry(self):
        mark = audit_mark()
        self.api.get(f"{URL}updates/")
        self.api.get(f"{URL}updates/{self.record('ihr-a').pk}/")
        self.api.get(f"{URL}history/")
        self.assertEqual(audit_since(mark), [])


class ListTests(ApiCase):
    def test_pending_and_kept_items_paged_and_filtered(self):
        body = self.api.get(f"{URL}updates/").json()
        self.assertEqual(body["count"], 4)
        found = {r["item"]: r for r in body["results"]}
        self.assertEqual(list(found), ["ihr-a", "ihr-k", "ihr-r", "ihr-u"])
        self.assertEqual(
            {k: (r["state"], r["reason"]) for k, r in found.items()},
            {
                "ihr-a": ("pending", "edited"),
                "ihr-k": ("pending", "edited"),
                "ihr-r": ("pending", "edited"),
                "ihr-u": ("pending", "unknown-origin"),
            },
        )
        self.assertEqual(found["ihr-a"]["label"], "Edited ihr-a")
        self.assertEqual(found["ihr-u"]["label"], "Their Own U")
        self.assertEqual(
            set(found["ihr-a"]),
            {
                "id", "pack", "kind", "item", "label", "facility", "facility_name",
                "state", "reason", "applied_version", "pending_version", "changed_at",
            },
        )  # fmt: skip
        self.assertIsNone(found["ihr-a"]["facility"])
        self.assertIsNone(found["ihr-a"]["facility_name"])  # P1-T24c G6
        self.assertEqual(
            (found["ihr-a"]["applied_version"], found["ihr-a"]["pending_version"]),
            (1, 2),
        )
        page = self.api.get(f"{URL}updates/", {"limit": 2, "offset": 1}).json()
        self.assertEqual([r["item"] for r in page["results"]], ["ihr-k", "ihr-r"])
        self.assertEqual(page["count"], 4)
        self.assertEqual(self.decide("ihr-k", "keep").status_code, 200)
        kept = self.api.get(f"{URL}updates/", {"state": "kept"}).json()
        self.assertEqual([r["item"] for r in kept["results"]], ["ihr-k"])
        pending = self.api.get(
            f"{URL}updates/",
            {"state": "pending", "pack": TEST_PACK, "kind": "valueset"},
        ).json()
        self.assertEqual(pending["count"], 3)
        for bad in (
            {"state": "current"},
            {"pack": "no-such-pack"},
            {"kind": "lab"},
            {"limit": "0"},
            {"limit": "201"},
            {"limit": "x"},
            {"offset": "-1"},
        ):
            with self.subTest(bad=bad):
                answer = self.api.get(f"{URL}updates/", bad)
                self.assertEqual(answer.status_code, 400)
                self.assertEqual(set(answer.json()), set(bad))

    def test_the_change_list_names_each_item_and_who(self):
        self.assertEqual(self.decide("ihr-a").status_code, 200)
        body = self.api.get(f"{URL}history/", {"pack": TEST_PACK}).json()
        accepted, applied_v2, applied_v1 = body["results"][:3]
        self.assertEqual(
            accepted["by"],
            {"user": str(self.admin.external_id), "username": self.admin.username},
        )
        self.assertEqual(accepted["outcome"], "accepted")
        self.assertEqual(accepted["counts"], {"item": "ihr-a", "reason": REASON})
        self.assertEqual(accepted["changes"], [])
        self.assertEqual(applied_v2["by"], "operator")
        self.assertEqual(
            sorted((c["item"], c["change"]) for c in applied_v2["changes"]),
            [("ihr-a", "pending"), ("ihr-k", "pending"), ("ihr-r", "pending"),
             ("ihr-u", "pending")],
        )  # fmt: skip
        self.assertEqual(applied_v2["counts"]["changes_total"], 4)
        self.assertEqual(
            sorted((c["item"], c["change"]) for c in applied_v1["changes"]),
            [("ihr-a", "created"), ("ihr-k", "created"), ("ihr-r", "created"),
             ("ihr-u", "pending"), ("ihr-x", "created")],
        )  # fmt: skip
        self.assertEqual({c["facility"] for c in applied_v1["changes"]}, {None})
        self.assertEqual(
            self.api.get(f"{URL}history/", {"pack": "x;"}).status_code, 400
        )

    def test_a_re_apply_that_writes_nothing_still_writes_nothing(self):
        before = PackApply.objects.count()
        apply()
        self.assertEqual(PackApply.objects.count(), before)

    def test_a_failure_is_shown_without_its_error(self):
        with as_owner():
            PackApply.objects.create(
                pack=TEST_PACK, version=2, outcome="failed", actor="packs:startup",
                error="KeyError: secret-ish detail",
            )  # fmt: skip
        body = self.api.get(f"{URL}history/").json()
        failed = body["results"][0]
        self.assertEqual((failed["outcome"], failed["by"]), ("failed", "start-up"))
        self.assertEqual(
            failed["note"], "failed; the details are kept for the operators"
        )
        self.assertNotIn("secret-ish", str(body))

    def test_an_apply_from_before_the_change_list_shows_none(self):
        """T3: an applied row written before P1-T15b1 (no changes_total) reads "not recorded"
        (null), never as an apply that changed nothing."""
        with as_owner():
            PackApply.objects.create(
                pack=TEST_PACK, version=2, outcome="applied", actor="packs:startup",
                counts={"created": 3},
            )  # fmt: skip
        newest = self.api.get(f"{URL}history/").json()["results"][0]
        self.assertEqual(newest["outcome"], "applied")
        self.assertIsNone(newest["changes"])
        applied = [
            r for r in self.api.get(f"{URL}history/").json()["results"][1:]
            if r["outcome"] == "applied"
        ]  # fmt: skip
        self.assertTrue(applied)
        self.assertTrue(all(isinstance(r["changes"], list) for r in applied))

    def test_unreadable_packs_answer_503_and_never_claim_an_alert(self):
        """Q4: nothing logs or alerts on this answer, so it does not say anyone was told."""
        with mock.patch.object(
            engine, "verified_releases", side_effect=engine.PackError("bad file")
        ):
            for answer in (
                self.api.get(f"{URL}updates/"),
                self.api.get(f"{URL}updates/{self.record('ihr-a').pk}/"),
            ):
                self.assertEqual(answer.status_code, 503)
                self.assertEqual(
                    answer.json(), {"detail": api.UNAVAILABLE, "code": "unavailable"}
                )
                self.assertNotIn("alerted", answer.json()["detail"])


class DetailTests(ApiCase):
    def columns(self, body, name):
        (entry,) = [f for f in body["fields"] if f["field"] == name]
        return entry

    def assert_only_pack_fields(self, body, raw):
        self.assertEqual([f["field"] for f in body["fields"]], list(valuesets.FIELDS))
        self.assertNotIn(DESCRIPTION, raw)
        for key in ("history", "meta", "created_by", "updated_by", "description"):
            self.assertNotIn(f'"{key}"', raw)

    def get(self, slug):
        response = self.api.get(f"{URL}updates/{self.record(slug).pk}/")
        self.assertEqual(response.status_code, 200)
        return response.json(), response.content.decode()

    def test_an_edited_item_shows_the_three_columns(self):
        body, raw = self.get("ihr-a")
        self.assert_only_pack_fields(body, raw)
        self.assertEqual((body["effect"], body["blocked"]), ("update", ""))
        self.assertEqual(
            body["row_sha256"],
            valuesets.decision_hash(ValueSet._base_manager.get(slug="ihr-a")),
        )
        self.assertEqual(body["pending_sha256"], self.record("ihr-a").pending_sha256)
        name = self.columns(body, "name")
        self.assertEqual(
            (name["hospital"], name["pack"], name["previous"]),
            ("Edited ihr-a", "Ihr A", "Ihr A"),
        )
        compose = self.columns(body, "compose")
        self.assertFalse(compose["truncated"])
        (code,) = compose["changes"]
        self.assertEqual(code["path"], "$.include[0].concept[0].code")
        self.assertEqual(
            (code["hospital"], code["pack"], code["previous"]), ("1-8", "2-8", "1-8")
        )

    def test_unknown_origin_has_no_previous(self):
        body, raw = self.get("ihr-u")
        self.assert_only_pack_fields(body, raw)
        name = self.columns(body, "name")
        self.assertEqual(
            (name["hospital"], name["pack"], name["previous"]),
            ("Their Own U", "Ihr U", None),
        )
        self.assertIsNone(body["applied_version"])

    def test_a_removal_retires_the_hospitals_row(self):
        body, raw = self.get("ihr-r")
        self.assert_only_pack_fields(body, raw)
        self.assertEqual((body["effect"], body["pending_sha256"]), ("retire", ""))
        status = self.columns(body, "status")
        self.assertEqual(
            (status["hospital"], status["pack"], status["previous"]),
            ("active", "retired", "active"),
        )
        self.assertEqual(self.columns(body, "compose")["changes"], [])

    def test_a_deleted_row_is_restored(self):
        with as_owner():
            edit("ihr-k", deleted=True)
            PackItem.objects.filter(pk=self.record("ihr-k").pk).update(reason="deleted")
        body, raw = self.get("ihr-k")
        self.assert_only_pack_fields(body, raw)
        self.assertEqual((body["reason"], body["effect"]), ("deleted", "restore"))
        self.assertEqual(self.decide("ihr-k").status_code, 200)
        self.assertFalse(ValueSet._base_manager.get(slug="ihr-k").deleted)

    def test_a_conflict_blocks_accept_and_allows_keep(self):
        PackItem.objects.filter(pk=self.record("ihr-u").pk).update(reason="conflict")
        body, raw = self.get("ihr-u")
        self.assert_only_pack_fields(body, raw)
        self.assertEqual((body["blocked"], body["conflicts"]), ("conflict", {}))
        refused = self.decide("ihr-u")
        self.assertEqual(refused.status_code, 409)
        self.assertTrue(refused.json()["blocked"])
        self.assertEqual(self.decide("ihr-u", "keep").status_code, 200)
        self.assertEqual(self.record("ihr-u").state, "kept")

    def test_large_json_is_cut(self):
        with as_owner():
            concepts = [{"code": f"{n}-{'x' * 300}"} for n in range(150)]
            edit(
                "ihr-a",
                compose={"include": [{"system": "http://loinc.org", "concept": concepts}],
                         "exclude": []},
            )  # fmt: skip
        compose = self.columns(self.get("ihr-a")[0], "compose")
        self.assertTrue(compose["truncated"])
        self.assertEqual(len(compose["changes"]), 100)
        self.assertTrue(
            all(len(c["hospital"] or "") <= 200 for c in compose["changes"])
        )

    def test_no_such_item_or_nothing_waiting(self):
        current = self.record("ihr-x").pk
        for pk in (current, 999999999, 2**63):
            self.assertEqual(self.api.get(f"{URL}updates/{pk}/").status_code, 404)


class DecisionTests(ApiCase):
    def test_accept_writes_the_packs_content_as_the_admin(self):
        mark = audit_mark()
        response = self.decide("ihr-a")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["state"], body["unchanged"]), ("current", False))
        row = ValueSet._base_manager.get(slug="ihr-a")
        self.assertEqual(row.name, "Ihr A")
        self.assertEqual(row.compose["include"][0]["concept"], [{"code": "2-8"}])
        self.assertEqual(row.description, DESCRIPTION)  # a field the pack does not set
        decision = PackApply.objects.get(outcome="accepted")
        self.assertEqual(decision.actor, f"packs:user-{self.admin.pk}")
        self.assertEqual(decision.counts, {"item": "ihr-a", "reason": REASON})
        rows = row_entries(mark)
        self.assertEqual({e["tbl"] for e in rows}, {"emr_valueset", *REFDATA_TABLES})
        for entry in rows:
            ctx = entry["context"]
            self.assertEqual(
                {k: ctx.get(k) for k in ("job", "via", "action", "item", "reason", "pack")},
                {"job": "packs", "via": "api", "action": "accept", "item": "ihr-a",
                 "reason": REASON, "pack": TEST_PACK},
            )  # fmt: skip
            self.assertEqual(ctx["version"], 2)
            self.assertEqual(
                entry["actor"], f"user:{self.admin.pk}:{self.admin.username}"
            )
        (request,) = request_entries(mark)
        self.assertEqual(request["context"]["purpose"], "admin")
        self.assertNotIn("reason", request["context"])

    def test_keep_leaves_the_hospitals_row(self):
        response = self.decide("ihr-k", "keep")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.record("ihr-k").state, "kept")
        self.assertEqual(ValueSet._base_manager.get(slug="ihr-k").name, "Edited ihr-k")
        self.assertEqual(
            PackApply.objects.get(outcome="kept").actor, f"packs:user-{self.admin.pk}"
        )
        # a kept item can still be accepted
        self.assertEqual(self.decide("ihr-k").status_code, 200)
        self.assertEqual(self.record("ihr-k").state, "current")

    def test_accepting_a_removal_retires_the_row(self):
        self.assertEqual(self.decide("ihr-r").status_code, 200)
        self.assertEqual(self.record("ihr-r").state, "retired")
        self.assertEqual(ValueSet._base_manager.get(slug="ihr-r").status, "retired")

    def test_bad_bodies_and_reasons(self):
        pk = self.record("ihr-a").pk
        shown = self.detail("ihr-a")
        good = {
            "action": "accept",
            "version": shown["pending_version"],
            "sha256": shown["pending_sha256"],
            "row_sha256": shown["row_sha256"],
            "reason": REASON,
        }
        mark = audit_mark()
        for field, value in (
            ("action", "revert"),
            ("version", "2"),
            ("version", True),
            ("version", 0),
            ("sha256", "ABC"),
            ("row_sha256", None),
            ("reason", ""),
            ("reason", "   "),
            ("reason", "x" * 301),
            ("reason", 12),
        ):
            with self.subTest(field=field, value=value):
                answer = self.api.post(
                    f"{URL}updates/{pk}/", {**good, field: value}, format="json"
                )
                self.assertEqual(answer.status_code, 400)
                self.assertEqual(list(answer.json()), [field])
        form = self.api.post(f"{URL}updates/{pk}/", good)  # a form, not JSON
        self.assertEqual(form.status_code, 400)
        self.assertEqual(row_entries(mark), [])
        self.assertEqual(self.record("ihr-a").state, "pending")

    def test_the_command_line_cannot_pose_as_a_staff_decision(self):
        for operator in ("user-1", "USER-7"):
            with self.subTest(operator=operator), as_owner():
                with self.assertRaisesRegex(CommandError, "may not start with 'user-'"):
                    call_command(
                        "ihr_pack_pending", "--slug", SLUG, "--accept", f"{TEST_PACK}:ihr-a",
                        "--operator", operator, "--reason", REASON, stdout=StringIO(),
                    )  # fmt: skip
                with self.assertRaisesRegex(CommandError, "may not start with 'user-'"):
                    call_command(
                        "ihr_pack_apply", "--slug", SLUG, "--operator", operator,
                        stdout=StringIO(), stderr=StringIO(),
                    )  # fmt: skip

    def test_stale_content_or_row_is_refused_and_nothing_written(self):
        before = PackApply.objects.count()
        mark = audit_mark()
        for change in ({"version": 1}, {"sha256": "0" * 64}, {"row_sha256": "f" * 64}):
            with self.subTest(change=change):
                answer = self.decide("ihr-a", **change)
                self.assertEqual(answer.status_code, 409)
                self.assertTrue(answer.json()["stale"])
        # the hospital edits the row between the admin's look and the decision (item 6)
        shown = self.detail("ihr-a")
        with as_owner():
            edit("ihr-a", name="Edited again")
        mark = audit_mark()
        answer = self.api.post(
            f"{URL}updates/{shown['id']}/",
            {
                "action": "accept",
                "version": shown["pending_version"],
                "sha256": shown["pending_sha256"],
                "row_sha256": shown["row_sha256"],
                "reason": REASON,
            },
            format="json",
        )
        self.assertEqual(answer.status_code, 409)
        self.assertTrue(answer.json()["stale"])
        self.assertEqual(PackApply.objects.count(), before)
        self.assertEqual(row_entries(mark), [])
        self.assertEqual(ValueSet._base_manager.get(slug="ihr-a").name, "Edited again")

    def test_a_soft_delete_after_the_look_is_stale(self):
        """T1: the admin was shown an update of a live row; the row is soft-deleted before the
        POST. Accepting would now restore it: 409 stale, nothing written."""
        shown = self.detail("ihr-a")
        self.assertEqual(shown["effect"], "update")
        with as_owner():
            edit("ihr-a", deleted=True)
        mark = audit_mark()
        answer = self.api.post(
            f"{URL}updates/{shown['id']}/",
            {
                "action": "accept",
                "version": shown["pending_version"],
                "sha256": shown["pending_sha256"],
                "row_sha256": shown["row_sha256"],
                "reason": REASON,
            },
            format="json",
        )
        self.assertEqual(answer.status_code, 409, answer.content)
        self.assertTrue(answer.json()["stale"])
        self.assertEqual(row_entries(mark), [])
        self.assertTrue(ValueSet._base_manager.get(slug="ihr-a").deleted)
        self.assertEqual(self.record("ihr-a").state, "pending")
        # the engine's own check under the lock sees it too
        with self.assertRaises(engine.StaleError):
            engine.decide(
                Hospital.objects.get(schema_name=TEST_SCHEMA), TEST_PACK, "ihr-a",
                accept=True, operator="packs:user-1", reason=REASON,
                expect=(shown["pending_version"], shown["pending_sha256"],
                        shown["row_sha256"]),
                record=False,
            )  # fmt: skip

    def test_an_item_no_one_decided_is_never_a_replay(self):
        """A1: ihr-x is current from an apply, never pending: a POST naming exactly what the
        apply wrote is 404, not a 200 replay with the detail."""
        record = self.record("ihr-x")
        self.assertEqual(record.state, "current")
        row = ValueSet._base_manager.get(slug="ihr-x")
        body = {
            "action": "accept",
            "version": record.applied_version,
            "sha256": record.applied_sha256,
            "row_sha256": engine.row_sha256(valuesets, row),
            "reason": REASON,
        }
        answer = self.api.post(f"{URL}updates/{record.pk}/", body, format="json")
        self.assertEqual(answer.status_code, 404, answer.content)
        self.assertEqual(answer.json(), {"detail": api.NOT_FOUND, "code": "not-found"})
        # a removal the start-up apply retired, never decided: not a replay either
        with as_owner():
            self.packs.add(
                3, vs("ihr-a", "2-8"), vs("ihr-k", "2-8"), vs("ihr-u", "1-8")
            )
        apply()
        retired = self.record("ihr-x")
        self.assertEqual(retired.state, "retired")
        answer = self.api.post(
            f"{URL}updates/{retired.pk}/",
            {**body, "version": retired.applied_version, "sha256": ""},
            format="json",
        )
        self.assertEqual(answer.status_code, 404, answer.content)

    def test_both_limits_are_kept_under_this_hospitals_schema(self):
        """A2/Q5: the raw Redis keys of the in-flight mark and the minute bucket start with
        this hospital's schema (cache.get would add the prefix itself and prove nothing)."""
        from django_redis import get_redis_connection

        self.assertTrue(cache.add(api._in_flight_key(), "another", 60))
        clock = SimpleNamespace(time=lambda: 1_700_000_000.0)
        with mock.patch.object(api, "time", clock):
            self.assertEqual(self.decide("ihr-a").status_code, 429)
        raw = {
            key.decode() if isinstance(key, bytes) else key
            for key in get_redis_connection("default").scan_iter("*ihr:packs:*")
        }
        bucket = [
            k for k in raw if k.endswith(f"ihr:packs:rate:{self.admin.pk}:28333333")
        ]
        self.assertTrue(any(k.startswith(f"{TEST_SCHEMA}:") for k in bucket), raw)
        marks = [k for k in raw if k.endswith("ihr:packs:decision")]
        self.assertTrue(any(k.startswith(f"{TEST_SCHEMA}:") for k in marks), raw)

    def test_a_stale_record_is_also_refused_inside_the_lock(self):
        """The engine re-checks under the lock what the view checked before it."""
        shown = self.detail("ihr-a")
        record = self.record("ihr-a")
        with self.assertRaises(engine.StaleError):
            engine.decide(
                Hospital.objects.get(schema_name=TEST_SCHEMA), TEST_PACK, "ihr-a",
                accept=True, operator="packs:user-1", reason=REASON,
                expect=(shown["pending_version"], shown["pending_sha256"], "f" * 64),
                record=False,
            )  # fmt: skip
        record.refresh_from_db()
        self.assertEqual(record.state, "pending")

    def test_a_replay_answers_unchanged_and_writes_nothing(self):
        shown = self.detail("ihr-a")
        body = {
            "action": "accept",
            "version": shown["pending_version"],
            "sha256": shown["pending_sha256"],
            "row_sha256": shown["row_sha256"],
            "reason": REASON,
        }
        path = f"{URL}updates/{shown['id']}/"
        self.assertEqual(self.api.post(path, body, format="json").status_code, 200)
        decisions = PackApply.objects.count()
        mark = audit_mark()
        again = self.api.post(path, body, format="json")
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()["unchanged"])
        self.assertEqual(PackApply.objects.count(), decisions)
        (entry,) = audit_since(mark)  # exactly one: the request entry (item 17)
        self.assertFalse(entry["tbl"])
        self.assertEqual(entry["context"]["purpose"], "admin")
        data = entry["data"]
        self.assertEqual(
            (json.loads(data) if isinstance(data, str) else data), {"status": 200}
        )

    def test_a_busy_hospital_answers_409_with_retry_after(self):
        holder = connections.create_connection("default")
        self.addCleanup(holder.close)
        with holder.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [runner.lock_key(SLUG)])
            self.assertTrue(cursor.fetchone()[0])
        before = PackApply.objects.count()
        mark = audit_mark()
        answer = self.decide("ihr-a")
        self.assertEqual(answer.status_code, 409)
        self.assertEqual(answer["Retry-After"], "60")
        self.assertEqual(
            answer.json(), {"detail": api.BUSY, "busy": True, "code": "busy"}
        )
        self.assertEqual(PackApply.objects.count(), before)
        self.assertEqual(row_entries(mark), [])
        self.assertEqual(self.record("ihr-a").state, "pending")
        self.assertIsNone(cache.get(api._in_flight_key()))

    def test_one_decision_in_flight_per_hospital(self):
        self.assertTrue(cache.add(api._in_flight_key(), "another", 60))
        answer = self.decide("ihr-a")
        self.assertEqual(answer.status_code, 429)
        self.assertEqual(answer["Retry-After"], "60")
        self.assertEqual(self.record("ihr-a").state, "pending")
        self.assertEqual(cache.get(api._in_flight_key()), "another")

    def test_at_most_30_decisions_per_user_per_minute(self):
        pk = self.record("ihr-a").pk
        clock = SimpleNamespace(time=lambda: 1_800_000_000.0)
        with mock.patch.object(api, "time", clock):
            for _ in range(api.PER_USER_PER_MINUTE):
                bad = self.api.post(f"{URL}updates/{pk}/", {}, format="json")
                self.assertEqual(bad.status_code, 400)
            refused = self.api.post(f"{URL}updates/{pk}/", {}, format="json")
            self.assertEqual(refused.status_code, 429)
            self.assertEqual(refused["Retry-After"], "60")
            other = self.client_for(hospital_admin(f"pk-other-{_tag()}"))
            self.assertEqual(
                other.post(f"{URL}updates/{pk}/", {}, format="json").status_code, 400
            )
        # the keys carry this hospital's schema (the runner fails the run otherwise)
        self.assertTrue(cache.get(f"ihr:packs:rate:{self.admin.pk}:{30_000_000}"))

    def test_an_unrecorded_release_is_refused_without_recording_it(self):
        with as_owner():
            PackRelease.objects.filter(pack=TEST_PACK, version=2).delete()
        with CaptureQueriesContext(connection) as queries:
            answer = self.decide("ihr-a")
        self.assertEqual(answer.status_code, 409)
        self.assertIn("not released here yet", answer.json()["detail"])
        self.assertFalse(
            [q for q in queries if "INSERT" in q["sql"] and "packrelease" in q["sql"]]
        )
        self.assertFalse(PackRelease.objects.filter(pack=TEST_PACK, version=2).exists())
        self.assertEqual(self.record("ihr-a").state, "pending")

    def test_a_newer_release_not_applied_here_yet_is_stale(self):
        with as_owner():
            self.packs.add(3, vs("ihr-a", "3-8"), vs("ihr-k", "1-8"))
        answer = self.decide("ihr-a")
        self.assertEqual(answer.status_code, 409)
        self.assertTrue(answer.json()["stale"])

    def test_a_hospital_that_is_not_live_is_refused(self):
        with as_owner(), schema_context("public"):
            Hospital.objects.filter(schema_name=TEST_SCHEMA).update(
                status=Hospital.Status.SUSPENDED
            )
        answer = self.decide("ihr-a")
        self.assertEqual(answer.status_code, 409)
        self.assertEqual(self.record("ihr-a").state, "pending")


class AnswerKindTests(ApiCase):
    """P1-T24c: every refusal of a POST names its kind (``code``), so the web page maps it to
    a fixed phrase and never shows the English ``detail``. The table is in
    docs/reviews/p1-t24/design.md "T24c as decided"; who may do what is unchanged."""

    def body(self, slug="ihr-a", **changes):
        shown = self.detail(slug)
        return shown["id"], {
            "action": "accept",
            "version": shown["pending_version"],
            "sha256": shown["pending_sha256"],
            "row_sha256": shown["row_sha256"],
            "reason": REASON,
            **changes,
        }

    def post(self, pk, body):
        return self.api.post(f"{URL}updates/{pk}/", body, format="json")

    def test_no_such_item_is_not_found(self):
        _, body = self.body()
        answer = self.post(999999999, body)
        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer.json(), {"detail": api.NOT_FOUND, "code": "not-found"})

    def test_a_hospital_that_is_not_live_is_not_live(self):
        pk, body = self.body()
        with as_owner(), schema_context("public"):
            Hospital.objects.filter(schema_name=TEST_SCHEMA).update(
                status=Hospital.Status.SUSPENDED
            )
        answer = self.post(pk, body)
        self.assertEqual(answer.status_code, 409)
        self.assertEqual(answer.json(), {"detail": api.NOT_LIVE, "code": "not-live"})

    def test_a_stale_answer_keeps_its_flag_and_names_its_kind(self):
        pk, body = self.body(sha256="0" * 64)
        answer = self.post(pk, body)
        self.assertEqual(answer.status_code, 409)
        self.assertTrue(answer.json()["stale"])
        self.assertEqual(answer.json()["code"], "stale")

    def test_a_blocked_answer_keeps_its_flag_and_names_its_kind(self):
        PackItem.objects.filter(pk=self.record("ihr-u").pk).update(reason="conflict")
        pk, body = self.body("ihr-u")
        answer = self.post(pk, body)
        self.assertEqual(answer.status_code, 409)
        self.assertTrue(answer.json()["blocked"])
        self.assertEqual(answer.json()["code"], "blocked")

    def test_the_two_429s_differ(self):
        pk, body = self.body()
        self.assertTrue(cache.add(api._in_flight_key(), "another", 60))
        answer = self.post(pk, body)
        self.assertEqual(answer.status_code, 429)
        self.assertEqual(answer["Retry-After"], "60")
        self.assertEqual(answer.json()["code"], "in-flight")
        cache.delete(api._in_flight_key())
        clock = SimpleNamespace(time=lambda: 1_800_000_000.0)
        with mock.patch.object(api, "time", clock):
            for _ in range(api.PER_USER_PER_MINUTE):
                self.post(pk, {})
            answer = self.post(pk, {})
        self.assertEqual(answer.status_code, 429)
        self.assertEqual(answer.json(), {"detail": api.TOO_MANY, "code": "rate"})

    def test_unreadable_packs_on_a_post_are_unavailable(self):
        pk, body = self.body()
        with mock.patch.object(
            engine, "prepare", side_effect=engine.PackError("bad file")
        ):
            answer = self.post(pk, body)
        self.assertEqual(answer.status_code, 503)
        self.assertEqual(
            answer.json(), {"detail": api.UNAVAILABLE, "code": "unavailable"}
        )

    def test_a_refusal_by_the_engine_is_refused(self):
        pk, body = self.body()
        with mock.patch.object(
            engine, "decide", side_effect=engine.RefusedError("not today")
        ):
            answer = self.post(pk, body)
        self.assertEqual(answer.status_code, 400)
        self.assertEqual(answer.json(), {"reason": "not today", "code": "refused"})

    def test_a_body_error_names_only_its_fields(self):
        """400 for a bad body is keyed by field (action, version, sha256, row_sha256,
        reason); the page maps those keys, so they carry no ``code`` (see
        test_bad_bodies_and_reasons)."""
        pk, body = self.body(reason="")
        answer = self.post(pk, body)
        self.assertEqual(answer.status_code, 400)
        self.assertEqual(list(answer.json()), ["reason"])

    def test_a_decision_still_works_and_its_200_has_no_kind(self):
        pk, body = self.body()
        answer = self.post(pk, body)
        self.assertEqual(answer.status_code, 200)
        self.assertNotIn("code", answer.json())
        self.assertEqual(answer.json()["unchanged"], False)


class CommitInsideTheLock(SimpleTestCase):
    """Review item 15: the decision is committed while the hospital's lock is still held, so
    a pack apply that takes the lock next always reads it. A second connection looks when the
    lock is about to be released. Its own hospital, committed fixtures, keys unique to the run;
    the trail it writes is never cleaned up."""

    databases = {"default"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tag = _tag()
        cls.slug = f"pk{tag}"[:31]
        cls.domain = f"{cls.slug}.packs.test"
        cls.pack = f"api-{tag}"
        cls.addClassCleanup(connection.set_schema, TEST_SCHEMA)
        shim = SimpleNamespace(
            addCleanup=cls.addClassCleanup, enterContext=cls.enterClassContext
        )
        cls.packs = Packs(shim)
        with as_owner():
            connection.set_schema_to_public()
            cls.hospital = Hospital(
                slug=cls.slug, name="Packs Lock Test", status=Hospital.Status.ACTIVE
            )
            cls.hospital.save()
            cls.addClassCleanup(cls._drop)
            Domain.objects.create(domain=cls.domain, tenant=cls.hospital)
            cls.packs.add(1, vs("ihr-c1", "1-8"), pack=cls.pack)
            engine.apply_packs(cls.hospital, operator="packs:tester")
            with schema_context(cls.hospital.schema_name):
                edit("ihr-c1", name="Edited C1")
            cls.packs.add(2, vs("ihr-c1", "2-8"), pack=cls.pack)
            engine.apply_packs(cls.hospital, operator="packs:tester")
        with schema_context(cls.hospital.schema_name):
            cls.admin = hospital_admin(f"pk-lock-{tag}")
            cls.record = PackItem.objects.get(pack=cls.pack, item="ihr-c1")

    @classmethod
    def _drop(cls):
        with as_owner(), schema_context("public"), connection.cursor() as cursor:
            Domain.objects.filter(tenant=cls.hospital).delete()
            Hospital.objects.filter(pk=cls.hospital.pk).delete()
            cursor.execute(
                f'DROP SCHEMA IF EXISTS "{cls.hospital.schema_name}" CASCADE'
            )

    def test_the_decision_is_committed_before_the_lock_is_released(self):
        schema = self.hospital.schema_name
        seen = []
        real = runner.exclusive

        @contextmanager
        def watched(slug):
            with real(slug):
                yield
                other = connections.create_connection("default")
                try:
                    with other.cursor() as cursor:
                        cursor.execute(
                            f'SELECT state FROM "{schema}".ihr_refdata_packitem '  # noqa: S608
                            "WHERE id = %s",
                            [self.record.pk],
                        )
                        seen.append(cursor.fetchone()[0])
                finally:
                    other.close()

        client = APIClient(HTTP_HOST=self.domain)
        client.force_authenticate(self.admin)
        shown = client.get(f"{URL}updates/{self.record.pk}/").json()
        # Deployed settings turn ATOMIC_REQUESTS on (config/settings/base.py, deployment.py);
        # CARE's test settings rebuild DATABASES without it, so it is put back for this request
        with (
            mock.patch.dict(connection.settings_dict, {"ATOMIC_REQUESTS": True}),
            mock.patch.object(api.runner, "exclusive", watched),
        ):
            self.assertTrue(connections.settings["default"]["ATOMIC_REQUESTS"])
            answer = client.post(
                f"{URL}updates/{self.record.pk}/",
                {
                    "action": "accept",
                    "version": shown["pending_version"],
                    "sha256": shown["pending_sha256"],
                    "row_sha256": shown["row_sha256"],
                    "reason": REASON,
                },
                format="json",
            )
        self.assertEqual(answer.status_code, 200, answer.content)
        self.assertEqual(seen, ["current"])
