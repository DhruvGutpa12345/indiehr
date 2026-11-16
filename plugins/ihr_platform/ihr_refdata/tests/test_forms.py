# ruff: noqa: S608 (test SQL names our own test schemas and columns; values are parameters)
"""Pack forms and the answered-form guard (P1-T15b2b; design.md §3.3-3.5 and "After the attack"
items 1-5, 12 and 15).

Test packs only (``ihr_refdata.testing.form``): no real form content ships. Most tests run in
the shared test hospital inside a transaction that is rolled back: pack applies as the owner,
the guard's refusals as the app role (``IHR_DB_ROLES=test``). The answer race needs two
connections that see committed rows, so it is a SimpleTestCase on a hospital of its own with
keys unique to the run; the trail it writes is never cleaned up.
"""

import json
import time
import types
import uuid
from io import StringIO
from pathlib import Path
from unittest import mock

from care.emr.models.organization import Organization
from care.emr.models.patient import Patient
from care.emr.models.questionnaire import (
    FormSubmission,
    Questionnaire,
    QuestionnaireFacilityOrganization,
    QuestionnaireOrganization,
    QuestionnaireResponse,
    QuestionnaireResponseTemplate,
)
from care.emr.resources.questionnaire.spec import QuestionnaireWriteSpec
from care.facility.models import Facility
from care.security.models import PermissionModel, RoleModel, RolePermission
from care.users.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import (
    IntegrityError,
    OperationalError,
    connection,
    connections,
    transaction,
)
from django.test import SimpleTestCase, TestCase, override_settings
from django_tenants.utils import get_public_schema_name, schema_context
from ihr_platform import db_functions
from ihr_platform.models import Domain, Hospital
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_platform.testing import as_owner, owner_for_test
from model_bakery import baker
from rest_framework.test import APIClient

from ihr_refdata import engine, forms, forms_guard, manifest
from ihr_refdata.models import PackApply, PackItem
from ihr_refdata.testing import (
    TEST_PACK,
    Packs,
    audit_mark,
    audit_since,
    form,
    question,
)
from ihr_refdata.tests.test_api import URL

SLUG = "testhosp"
HOST = "testserver"
KIND = forms.KIND
ANSWERED = "has answers; make a new version instead"


def _tag():
    return f"{time.time_ns() % 10**12:x}"


def card(key, version, suffix=""):
    return f"ihr-pack-{key}-v{version}{suffix}"


def row_at(slug):
    return Questionnaire._base_manager.get(slug=slug)


def exists(slug):
    return Questionnaire._base_manager.filter(slug=slug).exists()


_PHONE = iter(range(10**6))


def patient():
    return Patient.objects.create(
        name="Form Test Patient",
        gender="female",
        phone_number=f"+9197{next(_PHONE):08d}",
        year_of_birth=1990,
        blood_group="O_positive",
    )


def answer(row, who=None):
    who = who or patient()
    return QuestionnaireResponse.objects.create(
        questionnaire=row, subject_id=who.external_id, patient=who, responses=[]
    )


def submission(row, who=None, status="submitted"):
    return FormSubmission.objects.create(
        questionnaire=row, patient=who or patient(), status=status
    )


def pack_write(*, on):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config(%s, %s, true)",
            [forms.WRITE_SETTING, "on" if on else "off"],
        )


def foreign(slug, *, deleted=False, title="Their own form"):
    """A form at a reserved slug the pack did not write (made before P1-T15b2b, or in the
    shell with the setting on)."""
    pack_write(on=True)
    try:
        return Questionnaire.objects.create(
            slug=slug,
            version="1",
            title=title,
            subject_type="patient",
            status="active",
            questions=[],
            deleted=deleted,
        )
    finally:
        pack_write(on=False)


def plain_form(slug=None, **fields):
    """A hospital's own form (not a pack's)."""
    return Questionnaire.objects.create(
        slug=slug or f"own-{uuid.uuid4().hex[:12]}",
        version="1",
        title=fields.pop("title", "Our own form"),
        subject_type="patient",
        status="active",
        questions=[question("own", 1)],
        **fields,
    )


def links(row):
    return (
        sorted(
            QuestionnaireOrganization.objects.filter(questionnaire=row).values_list(
                "organization_id", flat=True
            )
        ),
        sorted(
            QuestionnaireFacilityOrganization.objects.filter(
                questionnaire=row
            ).values_list("organization_id", flat=True)
        ),
    )


def content(row):
    row.refresh_from_db()
    return (
        row.slug,
        row.version,
        row.title,
        row.description,
        row.subject_type,
        row.styling_metadata,
        row.questions,
    )


def admin_of(organisation, name):
    """A hospital-wide admin who may read and write forms at ``organisation``."""
    user = User.objects.create_user(username=name, password="x-Unused-1")
    role = RoleModel.objects.create(name=f"role-{name}")
    for slug in (
        "can_create_user",
        "can_read_questionnaire",
        "can_write_questionnaire",
    ):
        permission, _ = PermissionModel.objects.get_or_create(
            slug=slug, defaults={"name": slug}
        )
        RolePermission.objects.create(role=role, permission=permission)
    from care.emr.models.organization import OrganizationUser

    OrganizationUser.objects.create(organization=organisation, user=user, role=role)
    return user


class FormCase(TestCase):
    databases = {"default"}

    def setUp(self):
        owner_for_test(self)  # pack applies and decisions are owner-run (P1-T19a)
        Hospital.objects.exclude(
            schema_name__in=[get_public_schema_name(), TEST_SCHEMA]
        ).update(status=Hospital.Status.SUSPENDED)
        self.packs = Packs(self)
        self.key = f"tf{_tag()}"
        # a non-government top-level organisation for v1 to be linked to (Q-K80 A)
        self.team = Organization.objects.create(
            name=f"Team {self.key}", org_type="team"
        )

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

    def accept(self, key=None):
        return self.pending_cmd(
            "--accept", f"{TEST_PACK}:{key or self.key}", "--operator", "tester",
            "--reason", "hospital admin OK, ticket 9",
        )  # fmt: skip

    def record(self, key=None):
        return PackItem.objects.get(pack=TEST_PACK, item=key or self.key)

    def changes(self):
        last = (
            PackApply.objects.filter(pack=TEST_PACK, outcome=PackApply.Outcome.APPLIED)
            .order_by("-id")
            .first()
        )
        return {c["item"]: c for c in last.changes} if last else {}


class FirstVersionTests(FormCase):
    def test_v1_is_created_and_linked_to_the_hospital_but_not_its_states(self):
        """Item 1, Q-K80 A: linked to every active, live, top-level organisation but the
        government ones; the change list says linked-to-hospital."""
        top = Organization.objects.create(name=f"Top {self.key}", org_type="team")
        govt = Organization.objects.create(name=f"State {self.key}", org_type="govt")
        child = Organization.objects.create(
            name=f"Child {self.key}", org_type="team", parent=top
        )
        idle = Organization.objects.create(
            name=f"Idle {self.key}", org_type="team", active=False
        )
        gone = Organization.objects.create(
            name=f"Gone {self.key}", org_type="team", deleted=True
        )
        self.add(1, form(self.key, 1))
        self.apply()
        row = row_at(card(self.key, 1))
        self.assertEqual((row.version, row.status), ("1", "active"))
        linked = set(links(row)[0])
        self.assertIn(top.pk, linked)
        for other in (govt, child, idle, gone):
            self.assertNotIn(other.pk, linked, other.name)
        self.assertEqual(linked, {o.pk for o in forms.hospital_organisations()})
        self.assertFalse(
            Organization.objects.filter(pk__in=linked, org_type="govt").exists()
        )
        row.refresh_from_db()
        self.assertIn(top.pk, row.organization_cache)
        record = self.record()
        self.assertEqual((record.state, record.target), ("current", row.slug))
        change = self.changes()[self.key]
        self.assertEqual(change["change"], "created")
        self.assertEqual(change["links"], "linked-to-hospital")
        self.assertEqual(change["target"], row.slug)


class ProvisionedShapeTests(FormCase):
    def test_no_form_nobody_could_see_is_made_and_it_is_asked_again(self):
        """Reviews E1, E2 (Q-K68 A): a provisioned hospital has only government top-level
        organisations (its first admin on the state one) and its packs are applied before
        geography: v1 waits as missing-link, writes no unlinked row, cannot be accepted, and
        is created and linked by the next apply of the same release once there is an
        organisation to link it to."""
        Organization.objects.filter(parent__isnull=True).exclude(
            org_type="govt"
        ).update(active=False)
        state = Organization.objects.create(name=f"State {self.key}", org_type="govt")
        admin_of(state, f"pv-{self.key}")
        self.assertEqual(forms.hospital_organisations(), [])
        self.add(1, form(self.key, 1))
        for _ in range(2):  # provisioning's apply, then activate's of the same release
            self.apply()
            record = self.record()
            self.assertEqual((record.state, record.reason), ("pending", "missing-link"))
            self.assertIn(
                "no active top-level organisation", record.checks["missing-link"]
            )
            self.assertFalse(
                Questionnaire._base_manager.filter(
                    slug__startswith=f"ihr-pack-{self.key}-"
                ).exists()
            )
        with self.assertRaisesRegex(CommandError, "missing-link"):
            self.accept()
        hospital = Organization.objects.create(name=f"Root {self.key}", org_type="team")
        self.apply()
        row = row_at(card(self.key, 1))
        self.assertEqual(links(row)[0], [hospital.pk])
        self.assertEqual(
            (self.record().state, self.record().target), ("current", row.slug)
        )
        change = self.changes()[self.key]
        self.assertEqual((change["links"], change["linked"]), ("linked-to-hospital", 1))


class NewVersionTests(FormCase):
    def test_a_new_version_never_writes_the_old_row(self):
        """Q-K65 (mutation forms-in-place): v2 is a new row; v1 keeps its content."""
        self.add(1, form(self.key, 1))
        self.apply()
        v1 = row_at(card(self.key, 1))
        before = content(v1)
        self.add(2, form(self.key, 2))
        self.apply()
        self.assertTrue(exists(card(self.key, 2)))
        self.assertEqual(content(v1), before)
        self.assertEqual(v1.status, "retired")

    def test_v2_is_a_new_row_with_v1s_links_and_v1_retired(self):
        user = User.objects.create_user(username=f"fac-{self.key}", password="x-1")
        facility = baker.make(Facility, name=f"Forms {self.key}", created_by=user)
        extra = Organization.objects.create(name=f"Extra {self.key}", org_type="team")
        self.add(1, form(self.key, 1))
        self.apply()
        v1 = row_at(card(self.key, 1))
        QuestionnaireOrganization(questionnaire=v1, organization=extra).save()
        QuestionnaireFacilityOrganization(
            questionnaire=v1, organization=facility.default_internal_organization
        ).save()
        answer(v1)
        submission(v1, status="draft")
        QuestionnaireResponseTemplate.objects.create(name="Kept", questionnaire=v1)
        before, v1_links = content(v1), links(v1)
        self.add(2, form(self.key, 2))
        self.apply()
        v2 = row_at(card(self.key, 2))
        self.assertEqual(v2.version, "2")
        self.assertEqual(links(v2), v1_links)
        v2.refresh_from_db()
        self.assertIn(extra.pk, v2.organization_cache)
        self.assertIn(
            facility.default_internal_organization.pk, v2.internal_organization_cache
        )
        self.assertEqual(content(v1), before)  # answered, never edited
        self.assertEqual(v1.status, "retired")
        record = self.record()
        self.assertEqual((record.state, record.target), ("current", v2.slug))
        change = self.changes()[self.key]
        self.assertEqual(change["change"], "updated")
        self.assertEqual(change["target"], v2.slug)
        self.assertEqual(change["previous"], v1.slug)
        self.assertEqual(change["links"], "copied")
        # item 12: what still points at v1, counts only
        self.assertEqual(
            change["stranded"], {"draft_submissions": 1, "response_templates": 1}
        )
        # a re-apply writes nothing and audits nothing (item 2)
        applies = PackApply.objects.count()
        mark = audit_mark()
        self.apply()
        self.assertEqual(PackApply.objects.count(), applies)
        self.assertEqual([e for e in audit_since(mark) if e["tbl"]], [])
        self.assertEqual(self.record().target, v2.slug)
        self.assertEqual(
            Questionnaire._base_manager.filter(
                slug__startswith=f"ihr-pack-{self.key}-"
            ).count(),
            2,
        )

    def test_a_deleted_v2_accepted_is_undeleted_not_duplicated(self):
        """Item 2: the same form_version only undeletes (and sets the status)."""
        item = form(self.key, 2)
        self.add(1, form(self.key, 1))
        self.apply()
        self.add(2, item)
        self.apply()
        self.add(3)  # the form leaves the pack: v2 retired
        self.apply()
        v2 = row_at(card(self.key, 2))
        self.assertEqual(v2.status, "retired")
        v2.delete()  # CARE's own delete: a flag
        self.add(4, item)  # the same form_version comes back
        self.apply()
        self.assertEqual(
            (self.record().state, self.record().reason), ("pending", "deleted")
        )
        self.accept()
        v2.refresh_from_db()
        self.assertFalse(v2.deleted)
        self.assertEqual(v2.status, "active")
        self.assertEqual(
            Questionnaire._base_manager.filter(
                slug__startswith=f"ihr-pack-{self.key}-"
            ).count(),
            2,
        )
        record = self.record()
        self.assertEqual((record.state, record.target), ("current", v2.slug))

    def test_removal_retires_and_a_deleted_form_waits_for_a_new_version(self):
        self.add(1, form(self.key, 1))
        self.apply()
        v1 = row_at(card(self.key, 1))
        v1.delete()
        self.add(2, form(self.key, 1))  # nothing new for it: the deletion is theirs
        self.apply()
        self.assertEqual(self.record().state, "current")
        self.add(3, form(self.key, 2))
        self.apply()
        self.assertEqual(self.record().reason, "deleted")
        self.assertFalse(exists(card(self.key, 2)))
        self.accept()
        self.assertTrue(exists(card(self.key, 2)))
        self.assertEqual(self.record().target, card(self.key, 2))
        self.add(4)
        self.apply()
        self.assertEqual(row_at(card(self.key, 2)).status, "retired")
        self.assertEqual(self.record().state, "retired")

    def test_the_detail_names_what_a_new_version_strands(self):
        """Item 12 in the item detail: an edited v1 waits; the detail counts its drafts and
        templates. Accepting makes v2 and moves the target in the same transaction."""
        top = Organization.objects.create(name=f"Adm {self.key}", org_type="team")
        admin = admin_of(top, f"fadm-{self.key}")
        self.add(1, form(self.key, 1))
        self.apply()
        v1 = row_at(card(self.key, 1))
        v1.status = "draft"  # the hospital's own (free) change
        v1.save(update_fields=["status"])
        submission(v1, status="draft")
        submission(v1, status="draft")
        QuestionnaireResponseTemplate.objects.create(name="T", questionnaire=v1)
        self.add(2, form(self.key, 2))
        self.apply()
        record = self.record()
        self.assertEqual((record.state, record.reason), ("pending", "edited"))
        client = APIClient(HTTP_HOST=HOST)
        client.force_authenticate(admin)
        shown = client.get(f"{URL}updates/{record.pk}/").json()
        self.assertEqual(shown["effect"], "new-version")
        self.assertEqual(
            shown["notes"],
            {
                "previous": v1.slug,
                "stranded": {"draft_submissions": 2, "response_templates": 1},
            },
        )
        self.accept()
        self.assertTrue(exists(card(self.key, 2)))
        self.assertEqual(self.record().target, card(self.key, 2))


class SameVersionTests(FormCase):
    def test_accepting_the_pack_over_an_edited_same_version_makes_a_new_row(self):
        """Review E3: an unanswered v1 edited in the shell after the pack retired it; the pack
        brings v1 back and the admin accepts: the pack's content goes into a new row (its
        links copied, the edited one retired), and the recorded hash is the one that row
        holds."""
        self.add(1, form(self.key, 1))
        self.apply()
        v1 = row_at(card(self.key, 1))
        self.add(2)  # the form leaves the pack: v1 retired
        self.apply()
        Questionnaire.objects.filter(pk=v1.pk).update(title="Edited in the shell")
        self.add(3, form(self.key, 1))
        self.apply()
        record = self.record()
        self.assertEqual((record.state, record.reason), ("pending", "edited"))
        self.accept()
        record = self.record()
        holder = row_at(record.target)
        self.assertEqual(holder.slug, card(self.key, 1, "-2"))
        self.assertEqual(holder.title, form(self.key, 1)["title"])
        self.assertEqual(record.applied_sha256, forms.row_hash(holder))
        self.assertEqual(links(holder), links(v1))
        v1.refresh_from_db()
        self.assertEqual((v1.title, v1.status), ("Edited in the shell", "retired"))
        self.apply()  # nothing waits, and it is not a local edit
        self.assertEqual((self.record().state, self.record().reason), ("current", ""))

    def test_a_same_version_status_change_is_status_only(self):
        self.add(1, form(self.key, 1))
        self.apply()
        v1 = row_at(card(self.key, 1))
        self.assertEqual(forms.effect(v1, form(self.key, 1)), "status-only")
        self.assertEqual(forms.notes(v1, form(self.key, 1)), {})


class NamespaceTests(FormCase):
    def test_a_foreign_row_at_the_slug_is_never_adopted(self):
        """Item 3: the next free suffix is used and recorded, deleted rows count as taken,
        and equal content does not make a foreign row ours."""
        item = form(self.key, 1)
        theirs = foreign(card(self.key, 1), title=item["title"])
        foreign(card(self.key, 1, "-2"), deleted=True)
        before = content(theirs)
        self.add(1, item)
        self.apply()
        ours = card(self.key, 1, "-3")
        self.assertTrue(exists(ours))
        self.assertEqual(content(theirs), before)
        self.assertEqual(self.record().target, ours)
        self.assertEqual(self.changes()[self.key]["target"], ours)
        # the next version starts again from its own first free slug
        self.add(2, form(self.key, 2))
        self.apply()
        self.assertEqual(self.record().target, card(self.key, 2))
        self.assertEqual(row_at(ours).status, "retired")

    def test_ten_taken_slugs_are_a_conflict(self):
        for suffix in forms.SUFFIXES:
            foreign(card(self.key, 1, suffix), deleted=suffix == "-5")
        self.add(1, form(self.key, 1))
        self.apply()
        record = self.record()
        self.assertEqual((record.state, record.reason), ("pending", "conflict"))
        self.assertIn("are all taken", record.checks["conflict"])
        with self.assertRaisesRegex(CommandError, "conflict"):
            self.accept()
        # one freed (renamed out of the namespace by an operator, with the setting): the
        # next apply creates it there
        freed = row_at(card(self.key, 1, "-7"))
        freed.slug = f"theirs-{self.key}"
        pack_write(on=True)
        freed.save()
        pack_write(on=False)
        self.apply()
        self.assertEqual(self.record().target, card(self.key, 1, "-7"))
        self.assertEqual(self.record().state, "current")


class ShapeTests(SimpleTestCase):
    def test_check_item_refuses_bad_shapes(self):
        good = form("intake", 1)
        forms.check_item(good)
        bad = {
            "upper": {"slug": "Intake"},
            "short": {"slug": "a"},
            "long": {"slug": "a" * 31},
            "dash end": {"slug": "intake-"},
            "version 0": {"form_version": 0},
            "version 1000": {"form_version": 1000},
            "version bool": {"form_version": True},
            "subject": {"subject_type": "device"},
            "status": {"status": "retired"},
            "no questions": {"questions": []},
            "extra": {"organizations": []},
            "twice": {
                "questions": [question("intake", 1), question("intake", 1)],
            },
            "nested id twice": {
                "questions": [
                    {
                        **question("intake", 1, "g"),
                        "type": "group",
                        "questions": [question("intake", 1, "a")],
                    },
                    {
                        **question("intake", 1, "b"),
                        "id": question("intake", 1, "a")["id"],
                    },
                ]
            },
            "not a uuid": {"questions": [{**question("intake", 1), "id": "x"}]},
            "long title": {"title": "x" * 256},
        }
        canonical = question("intake", 1)["id"]
        for name, written in {
            "upper case id": canonical.upper(),
            "braced id": "{" + canonical + "}",
            "urn id": f"urn:uuid:{canonical}",
            "hex id": canonical.replace("-", ""),
            "version 1 id": str(uuid.uuid1()),
        }.items():
            bad[name] = {"questions": [{**question("intake", 1), "id": written}]}
        for name, change in bad.items():
            with (
                self.subTest(name),
                self.assertRaises((KeyError, TypeError, ValueError)),
            ):
                forms.check_item({**good, **change})
        self.assertEqual(
            len(card("a" * 30, 999, "-9")), 46
        )  # within CARE's 50-character slug
        forms.check_item({**good, "title": "x" * forms.MAX_TITLE})

    def test_a_release_may_not_change_a_form_version_or_go_back(self):
        """§3.3 immutability: refused for the whole fleet (PackError)."""
        cases = {
            "changed": (form("intake", 1), form("intake", 1, title="Changed")),
            "down": (form("intake", 2), form("intake", 1)),
        }
        for name, (first, second) in cases.items():
            with self.subTest(name):
                packs = Packs(self)
                packs.add(1, first, kind=KIND)
                packs.add(2, second, kind=KIND)
                with self.assertRaisesRegex(engine.PackError, "form_version"):
                    engine.verified_releases(packs.root)
        packs = Packs(self)
        packs.add(1, form("intake", 1))  # valueset kind: refused as malformed
        with self.assertRaises(engine.PackError):
            engine.verified_releases(packs.root)


class CareSpecTests(TestCase):
    """§3.5: CARE's own validation of every committed form pack, in a test hospital (coded
    fields may read value sets). No form pack ships yet; a test form proves the path."""

    def validate(self, item):
        QuestionnaireWriteSpec.model_validate(
            {
                "slug": forms.target(item),
                "title": item["title"],
                "description": item["description"],
                "status": item["status"],
                "subject_type": item["subject_type"],
                "styling_metadata": item["styling_metadata"],
                "questions": item["questions"],
            }
        )

    def validate_all(self, root=None):
        """CARE's spec over every form item of every release; how many it checked."""
        count = 0
        for versions in manifest.releases(root).values():
            for release in versions:
                if release.kind != KIND:
                    continue
                for item in release.items:
                    with self.subTest(pack=release.pack, item=item["slug"]):
                        self.validate(item)
                        count += 1
        return count

    @staticmethod
    def form_items_in(root):
        """Counted apart from the manifest (review Q6): every form item in the pack files."""
        count = 0
        for path in Path(root).rglob("*.json"):
            if path.name == manifest.MANIFEST:
                continue
            data = json.loads(path.read_text())
            if isinstance(data, dict) and data.get("kind") == KIND:
                count += len(data.get("items") or [])
        return count

    def test_every_committed_form_passes_cares_spec(self):
        self.assertEqual(self.validate_all(), self.form_items_in(manifest.packs_dir()))
        packs = Packs(self)  # the loop reaches form items (none ship before Phase 2)
        packs.add(1, form("intake", 1), kind=KIND)
        self.assertEqual(self.validate_all(packs.root), 1)
        self.assertEqual(self.form_items_in(packs.root), 1)
        with self.assertRaises(ValueError):
            self.validate(
                form("intake", 1, questions=[question("intake", 1, type="nope")])
            )


class CareViewTests(FormCase):
    def test_a_hospital_wide_admin_sees_v1_and_sets_its_links(self):
        """Item 1: the v1 form is listed for a hospital-wide admin, who sets its links
        through CARE's own viewset."""
        top = Organization.objects.create(name=f"Admins {self.key}", org_type="team")
        Organization.objects.create(name=f"Ward {self.key}", org_type="team")
        admin = admin_of(top, f"fv-{self.key}")
        self.add(1, form(self.key, 1))
        self.apply()
        slug = card(self.key, 1)
        client = APIClient(HTTP_HOST=HOST)
        client.force_authenticate(admin)
        listed = client.get("/api/v1/questionnaire/", {"title": self.key})
        self.assertEqual(listed.status_code, 200, listed.content[:300])
        self.assertIn(slug, [r["slug"] for r in listed.json()["results"]])
        self.assertGreater(len(links(row_at(slug))[0]), 1)  # every top-level one
        answer_ = client.post(
            f"/api/v1/questionnaire/{slug}/set_organizations/",
            {"organizations": [str(top.external_id)]},  # narrowed to the admins' own
            format="json",
        )
        self.assertEqual(answer_.status_code, 200, answer_.content[:300])
        self.assertEqual(links(row_at(slug))[0], [top.pk])


class GuardTests(TestCase):
    """The guard as the app role (IHR_DB_ROLES=test): CARE's ORM, its viewset and raw SQL."""

    databases = {"default"}

    def refused(self, write, text=ANSWERED):
        with self.assertRaisesRegex(IntegrityError, text), transaction.atomic():
            write()

    def raw(self, statement, params=()):
        with connection.cursor() as cursor:
            cursor.execute(statement, params)

    def test_every_column_is_frozen_or_free(self):
        """A column upstream adds fails here until forms_guard classifies it."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s",
                [TEST_SCHEMA, forms_guard.FORM_TABLE],
            )
            columns = {name for (name,) in cursor.fetchall()}
        self.assertEqual(columns, set(forms_guard.FROZEN) | set(forms_guard.FREE))
        self.assertFalse(set(forms_guard.FROZEN) & set(forms_guard.FREE))
        self.assertEqual(forms_guard.static_problems(), [])

    def test_the_guard_is_installed_as_this_builds(self):
        with connection.cursor() as cursor:
            self.assertEqual(forms_guard.problems(cursor, TEST_SCHEMA), [])
            self.assertEqual(
                forms_guard.runtime_problems_by_schema(cursor, [TEST_SCHEMA]), {}
            )

    def test_an_answered_form_refuses_frozen_edits_by_the_orm_and_raw_sql(self):
        row = plain_form()
        row.title = "Before any answer"
        row.save()  # unanswered: free
        answer(row)
        for column, value in (
            ("title", "Changed"),
            ("description", "Changed"),
            ("version", "2"),
            ("subject_type", "encounter"),
            ("styling_metadata", {"x": 1}),
            ("questions", [question("own", 2)]),
            ("slug", f"moved-{uuid.uuid4().hex[:8]}"),
            ("external_id", uuid.uuid4()),
        ):
            with self.subTest(orm=column):
                fresh = Questionnaire.objects.get(pk=row.pk)
                setattr(fresh, column, value)
                self.refused(fresh.save)
            with self.subTest(update=column):
                self.refused(
                    lambda c=column, v=value: Questionnaire.objects.filter(
                        pk=row.pk
                    ).update(**{c: v})
                )
        with self.subTest("raw"):
            self.refused(
                lambda: self.raw(
                    "UPDATE emr_questionnaire SET questions = '[]'::jsonb WHERE id = %s",
                    [row.pk],
                )
            )
        # a form submission alone counts, and so does a soft-deleted answer
        other = plain_form()
        submission(other).delete()
        other.title = "Changed"
        self.refused(other.save)

    def test_status_links_and_a_soft_delete_stay_free(self):
        row = plain_form()
        answer(row)
        organisation = Organization.objects.create(name="Free Links", org_type="team")
        row.save()  # every column written, none changed
        row.status = "retired"
        row.save()
        QuestionnaireOrganization(questionnaire=row, organization=organisation).save()
        QuestionnaireOrganization.objects.filter(questionnaire=row).delete()
        row.delete()  # CARE's delete: a flag
        row.refresh_from_db()
        self.assertTrue(row.deleted)

    @override_settings(IHR_STAFF_REFUSE_SUPERUSERS=False)
    def test_cares_viewset_refuses_an_answered_edit_and_takes_a_status_change(self):
        patch = mock.patch.dict(connection.settings_dict, {"ATOMIC_REQUESTS": True})
        patch.start()
        self.addCleanup(patch.stop)
        root = User.objects.create_superuser(
            username=f"fsu-{_tag()}", password="x-Unused-1", email="su@example.in"
        )
        organisation = Organization.objects.create(name="SU Forms", org_type="team")
        client = APIClient(HTTP_HOST=HOST, raise_request_exception=False)
        client.force_authenticate(root)
        slug = f"su-{_tag()}"
        made = client.post(
            "/api/v1/questionnaire/",
            {
                "slug": slug,
                "title": "Superuser form",
                "status": "active",
                "subject_type": "patient",
                "questions": [{"link_id": "1", "text": "How?", "type": "string"}],
                "organizations": [str(organisation.external_id)],
            },
            format="json",
        )
        self.assertEqual(made.status_code, 200, made.content[:300])
        row = Questionnaire.objects.get(slug=slug)
        answer(row)
        body = client.get(f"/api/v1/questionnaire/{slug}/").json()
        body = {
            k: body[k]
            for k in (
                "slug",
                "title",
                "description",
                "status",
                "subject_type",
                "styling_metadata",
                "questions",
            )
        }
        edited = client.put(
            f"/api/v1/questionnaire/{slug}/", {**body, "title": "Edited"}, format="json"
        )
        self.assertEqual(edited.status_code, 400, edited.content[:300])
        self.assertIn("make a new version", edited.content.decode())
        self.assertEqual(Questionnaire.objects.get(pk=row.pk).title, "Superuser form")
        retired = client.put(
            f"/api/v1/questionnaire/{slug}/",
            {**body, "status": "retired"},
            format="json",
        )
        self.assertEqual(retired.status_code, 200, retired.content[:300])
        # the reserved namespace: CARE's create answers an error, not a stolen pack slug
        stolen = client.post(
            "/api/v1/questionnaire/",
            {
                "slug": "ihr-pack-stolen-v1",
                "title": "Stolen",
                "status": "active",
                "subject_type": "patient",
                "questions": [{"link_id": "1", "text": "How?", "type": "string"}],
                "organizations": [str(organisation.external_id)],
            },
            format="json",
        )
        self.assertEqual(stolen.status_code, 400, stolen.content[:300])
        self.assertFalse(exists("ihr-pack-stolen-v1"))

    def test_hard_deletes_of_answers_are_refused(self):
        """Item 4 (mutation forms-answer-delete): the cascade from the form, the answers
        themselves, raw SQL; a form without answers may still go."""
        row = plain_form()
        response = answer(row)
        held = submission(row)
        never = "answers are never deleted"
        self.refused(lambda: Questionnaire.objects.filter(pk=row.pk).delete(), never)
        self.refused(
            lambda: QuestionnaireResponse.objects.filter(pk=response.pk).delete(), never
        )
        self.refused(lambda: FormSubmission.objects.filter(pk=held.pk).delete(), never)
        self.refused(
            lambda: self.raw(
                "DELETE FROM emr_questionnaireresponse WHERE id = %s", [response.pk]
            ),
            never,
        )
        self.refused(
            lambda: self.raw("DELETE FROM emr_questionnaire WHERE id = %s", [row.pk]),
            "it is never deleted",
        )
        response.delete()  # CARE's own: a flag
        unanswered = plain_form()
        Questionnaire.objects.filter(pk=unanswered.pk).delete()
        self.assertFalse(exists(unanswered.slug))

    def test_the_relink_detour_is_refused_at_its_first_step(self):
        """Item 4 (mutation forms-answer-relink): detach, edit, reattach never starts."""
        row, other = plain_form(), plain_form()
        response = answer(row)
        held = submission(row)
        stays = "stays with the form"
        response.questionnaire = None
        self.refused(response.save, stays)
        held.questionnaire = other
        self.refused(held.save, stays)
        self.refused(
            lambda: QuestionnaireResponse.objects.filter(pk=response.pk).update(
                questionnaire=other
            ),
            stays,
        )
        response.refresh_from_db()
        response.status = "entered_in_error"
        response.save()  # anything else of an answer stays CARE's to change

    def test_the_pack_namespace_is_reserved(self):
        """Item 3: an INSERT, or an UPDATE of slug, into ihr-pack- (any case) needs the
        engine's setting."""
        reserved = "belong to reference packs"
        for slug in ("ihr-pack-theirs-v1", "IHR-PACK-Theirs-v1"):
            with self.subTest(slug):
                self.refused(lambda s=slug: plain_form(s), reserved)
        mine = plain_form()
        mine.slug = "ihr-pack-moved-v1"
        self.refused(mine.save, reserved)
        plain_form("ihr-packs-are-not-it")
        pack_write(on=True)
        plain_form("ihr-pack-allowed-v1")
        pack_write(on=False)
        self.assertTrue(exists("ihr-pack-allowed-v1"))
        # the setting is off again for the rest of the transaction
        self.refused(lambda: plain_form("ihr-pack-after-v1"), reserved)


class GuardOwnerTests(TestCase):
    """What the guard refuses the owner too (the app role has no TRUNCATE at all)."""

    databases = {"default"}

    def setUp(self):
        owner_for_test(self)

    def test_truncate_of_answers_is_refused(self):
        """Review G2 (mutation forms-answer-truncate): directly, and reached by CASCADE from
        the patients; only a session that says so (the test runner's flush) may."""
        never = "answers are never deleted"
        for statement in (
            "TRUNCATE emr_formsubmission CASCADE",
            "TRUNCATE emr_questionnaireresponse CASCADE",
            "TRUNCATE emr_patient CASCADE",
        ):
            with (
                self.subTest(statement),
                self.assertRaisesRegex(IntegrityError, never),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config(%s, 'on', true)", [forms_guard.TRUNCATE_SETTING]
            )
            cursor.execute("TRUNCATE emr_formsubmission CASCADE")
            transaction.set_rollback(True)

    def test_a_pack_form_is_never_freed_without_the_setting(self):
        """Review E6: a hard delete of a reserved-slug row, or its slug moved out of the
        namespace, needs the engine's setting, so another pack's record never takes it."""
        pack_write(on=True)
        row = plain_form(f"ihr-pack-gone{_tag()}-v1")
        pack_write(on=False)
        reserved = "reference pack"
        with self.assertRaisesRegex(IntegrityError, reserved), transaction.atomic():
            Questionnaire.objects.filter(pk=row.pk).delete()
        row.slug = f"freed-{_tag()}"
        with self.assertRaisesRegex(IntegrityError, reserved), transaction.atomic():
            row.save()
        pack_write(on=True)
        Questionnaire.objects.filter(pk=row.pk).delete()
        pack_write(on=False)
        self.assertFalse(Questionnaire._base_manager.filter(pk=row.pk).exists())

    def test_every_reference_to_a_form_or_an_answer_is_classified(self):
        """Review Q5: a table upstream adds that points at a form or an answer fails here
        until forms_guard.REFERENCES says whether it is an answer."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT c.relname, a.attname FROM pg_constraint k "
                "JOIN pg_class c ON c.oid = k.conrelid "
                "JOIN pg_class r ON r.oid = k.confrelid "
                "JOIN pg_namespace n ON n.oid = r.relnamespace "
                "JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = ANY(k.conkey) "
                "WHERE k.contype = 'f' AND n.nspname = %s AND r.relname = ANY(%s)",
                [TEST_SCHEMA, list(forms_guard.TABLES)],
            )
            found = {f"{table}.{column}" for table, column in cursor.fetchall()}
        self.assertEqual(found, set(forms_guard.REFERENCES))
        answers = {
            ref.split(".")[0]
            for ref, kind in forms_guard.REFERENCES.items()
            if kind.startswith("answer")
        }
        self.assertEqual(answers, set(forms_guard.ANSWER_TABLES))


class GuardInstallTests(TestCase):
    databases = {"default"}

    def test_a_dropped_or_disabled_trigger_is_found_and_put_back(self):
        owner_for_test(self)
        table = f'"{TEST_SCHEMA}".emr_formsubmission'
        for statement in (
            f"DROP TRIGGER {forms_guard.ANSWER_FUNCTION} ON {table}",
            f"ALTER TABLE {table} DISABLE TRIGGER {forms_guard.ANSWER_FUNCTION}",
            f'ALTER FUNCTION "{TEST_SCHEMA}".{forms_guard.FORM_FUNCTION}() '
            "SET work_mem = '64kB'",
        ):
            with self.subTest(statement), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(statement)
                    self.assertTrue(forms_guard.problems(cursor, TEST_SCHEMA))
                self.assertTrue(forms_guard.ensure(connection, TEST_SCHEMA))
                with connection.cursor() as cursor:
                    self.assertEqual(forms_guard.problems(cursor, TEST_SCHEMA), [])
                transaction.set_rollback(True)

    def test_owner_tampering_is_found_by_the_check_and_the_gate(self):
        """Review G3: a trigger re-pointed to a same-named function in another schema, an
        edited body re-stamped as this build's, and both functions dropped with CASCADE are
        each named by problems() (migrate repairs them) and gate the hospital at run time."""
        owner_for_test(self)
        schema = f'"{TEST_SCHEMA}"'
        name = forms_guard.ANSWER_FUNCTION
        edited = (
            forms_guard.function_sql(TEST_SCHEMA).as_string().replace("FOR SHARE;", ";")
        )

        def repointed(cursor):
            cursor.execute(
                f"CREATE FUNCTION public.{name}() RETURNS trigger LANGUAGE plpgsql AS "
                "$f$BEGIN RETURN coalesce(NEW, OLD); END $f$"
            )
            cursor.execute(
                f"CREATE OR REPLACE TRIGGER {name} BEFORE INSERT OR UPDATE OF "
                f"questionnaire_id OR DELETE ON {schema}.emr_formsubmission FOR EACH ROW "
                f"EXECUTE FUNCTION public.{name}()"
            )

        def restamped(cursor):
            cursor.execute(edited)
            db_functions.write_stamp(
                cursor,
                TEST_SCHEMA,
                forms_guard.FORM_FUNCTION,
                "",
                list(forms_guard.FUNCTIONS),
                forms_guard.fingerprint(),
            )

        def dropped(cursor):
            for function in forms_guard.FUNCTIONS:
                cursor.execute(f"DROP FUNCTION {schema}.{function}() CASCADE")

        for tamper in (repointed, restamped, dropped):
            with self.subTest(tamper.__name__), transaction.atomic():
                with connection.cursor() as cursor:
                    tamper(cursor)
                    self.assertTrue(forms_guard.problems(cursor, TEST_SCHEMA))
                    self.assertIn(
                        TEST_SCHEMA,
                        forms_guard.runtime_problems_by_schema(cursor, [TEST_SCHEMA]),
                    )
                self.assertTrue(forms_guard.ensure(connection, TEST_SCHEMA))
                with connection.cursor() as cursor:
                    self.assertEqual(forms_guard.problems(cursor, TEST_SCHEMA), [])
                transaction.set_rollback(True)


class GuardChangesShipWithAMigration(SimpleTestCase):
    """Existing hospitals get a changed guard only from post_migrate, which the fleet runs
    only for a hospital with a migration new to it: the latest ihr_refdata migration states
    this build's fingerprint (also check ihr_refdata.W026)."""

    def test_the_latest_migration_states_this_builds_guard(self):
        self.assertEqual(forms_guard.code_problems(), [])
        with mock.patch.object(forms_guard, "VERSION", "0" * 64):
            self.assertTrue(forms_guard.code_problems())

    def test_a_later_migration_need_not_restate_it(self):
        """Review Q7: the newest migration that states the guard is the one compared; an
        unrelated later migration is no warning, and a stale statement names its migration."""
        now, old = forms_guard.fingerprint(), "0" * 64

        def stating(value=None):
            return types.SimpleNamespace(
                **({} if value is None else {"FORMS_GUARD_SHA256": value})
            )

        self.assertEqual(
            forms_guard.code_problems(
                [("0004_a", stating(now)), ("0005_b", stating())]
            ),
            [],
        )
        stale = forms_guard.code_problems(
            [("0004_a", stating(old)), ("0005_b", stating())]
        )
        self.assertEqual(len(stale), 1)
        self.assertIn("0004_a", stale[0])
        self.assertEqual(
            forms_guard.code_problems(
                [("0004_a", stating(old)), ("0005_b", stating(now))]
            ),
            [],
        )
        self.assertTrue(forms_guard.code_problems([("0001_x", stating())]))


class AnswerRace(SimpleTestCase):
    """§3.4's race (item 15, mutation forms-answer-lock): connection A inserts an answer and
    does not commit; connection B's change of a frozen column must wait for it (it times out
    at 1 s), and after A commits B's retry is refused. Its own hospital, committed fixtures,
    keys unique to the run; the trail it writes is never cleaned up."""

    databases = "__all__"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tag = _tag()
        cls.slug = f"fr{tag}"[:31]
        cls.addClassCleanup(connection.set_schema, TEST_SCHEMA)
        with as_owner():
            connection.set_schema_to_public()
            cls.hospital = Hospital(
                slug=cls.slug, name="Forms Race Test", status=Hospital.Status.ACTIVE
            )
            cls.hospital.save()
            cls.addClassCleanup(cls._drop)
            Domain.objects.create(domain=f"{cls.slug}.forms.test", tenant=cls.hospital)
            with schema_context(cls.hospital.schema_name):
                cls.form = plain_form(f"race-{tag}")
                cls.unanswered = plain_form(f"race-rr-{tag}")
                cls.patient = patient()
        connection.set_schema(TEST_SCHEMA)

    @classmethod
    def _drop(cls):
        # DROP SCHEMA fires no row trigger: the hospital goes whole
        with as_owner(), schema_context("public"), connection.cursor() as cursor:
            Domain.objects.filter(tenant=cls.hospital).delete()
            Hospital.objects.filter(pk=cls.hospital.pk).delete()
            cursor.execute(
                f'DROP SCHEMA IF EXISTS "{cls.hospital.schema_name}" CASCADE'
            )

    def _insert_answer(self, cursor, form=None):
        schema = self.hospital.schema_name
        cursor.execute(
            f'INSERT INTO "{schema}".emr_questionnaireresponse (external_id, '
            "created_date, modified_date, deleted, history, meta, subject_id, responses, "
            "structured_responses, patient_id, questionnaire_id, status) VALUES "
            "(%s, now(), now(), false, '{}', '{}', %s, '[]', '{}', %s, %s, 'completed')",
            [
                uuid.uuid4(),
                self.patient.external_id,
                self.patient.pk,
                (form or self.form).pk,
            ],
        )

    def _change_title(self, cursor, form=None):
        cursor.execute(
            f'UPDATE "{self.hospital.schema_name}".emr_questionnaire SET title = %s '
            "WHERE id = %s",
            [f"Changed {uuid.uuid4().hex[:6]}", (form or self.form).pk],
        )

    def test_a_repeatable_read_snapshot_cannot_edit_after_an_answer(self):
        """Review G1 (mutation forms-answer-snapshot): B's REPEATABLE READ snapshot is taken
        before A's answer commits, so its answer check could not see it: a frozen change
        outside READ COMMITTED is refused."""
        a = connections.create_connection("default")
        b = connections.create_connection("default")
        try:
            with b.cursor() as cb:
                cb.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
                cb.execute(
                    f'SELECT count(*) FROM "{self.hospital.schema_name}".'
                    "emr_questionnaireresponse"
                )
            with a.cursor() as ca:
                self._insert_answer(ca, self.unanswered)  # autocommit: committed
            with (
                b.cursor() as cb,
                self.assertRaisesRegex(IntegrityError, "needs READ COMMITTED"),
            ):
                self._change_title(cb, self.unanswered)
        finally:
            with b.cursor() as cb:
                cb.execute("ROLLBACK")
            a.close()
            b.close()

    def test_an_answer_being_given_holds_off_a_frozen_change(self):
        a = connections.create_connection("default")
        b = connections.create_connection("default")
        try:
            with a.cursor() as ca:
                ca.execute("BEGIN")
                self._insert_answer(ca)
            with b.cursor() as cb:
                cb.execute("SET lock_timeout = '1s'")
                with self.assertRaisesRegex(OperationalError, "lock timeout"):
                    self._change_title(cb)
            with a.cursor() as ca:
                ca.execute("COMMIT")
            with b.cursor() as cb, self.assertRaisesRegex(IntegrityError, ANSWERED):
                self._change_title(cb)
        finally:
            with a.cursor() as ca:
                ca.execute("ROLLBACK")
            a.close()
            b.close()
