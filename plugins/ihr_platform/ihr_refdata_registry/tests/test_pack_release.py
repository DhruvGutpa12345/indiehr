"""The fleet's record of pack releases (P1-T15a): recorded once, and a version is immutable."""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django_tenants.utils import get_public_schema_name
from ihr_platform.models import Hospital
from ihr_platform.test_runner import TEST_SCHEMA
from ihr_platform.testing import owner_for_test
from ihr_refdata.models import PackApply
from ihr_refdata.testing import CARE_COMMIT, TEST_PACK, Packs, vs

from ihr_refdata_registry.models import PackRelease


class PackReleaseTests(TestCase):
    databases = {"default"}

    def setUp(self):
        owner_for_test(self)  # pack apply records releases: owner-run (P1-T19a)
        Hospital.objects.exclude(
            schema_name__in=[get_public_schema_name(), TEST_SCHEMA]
        ).update(status=Hospital.Status.SUSPENDED)
        self.packs = Packs(self)

    def startup(self):
        call_command(
            "ihr_pack_apply", "--all", "--startup", stdout=StringIO(), stderr=StringIO()
        )

    def test_each_release_is_recorded_once(self):
        self.packs.add(1, vs("ihr-a", "1"))
        self.startup()
        self.packs.add(2, vs("ihr-a", "1", "2"))
        self.startup()
        self.startup()
        self.assertEqual(
            list(
                PackRelease.objects.filter(pack=TEST_PACK)
                .order_by("version")
                .values_list("version", "item_count", "care_commit", "loaded_by")
            ),
            [
                (1, 1, CARE_COMMIT, "packs:startup"),
                (2, 1, CARE_COMMIT, "packs:startup"),
            ],
        )

    def test_a_rebuilt_version_is_refused_by_name_for_the_whole_fleet(self):
        self.packs.add(1, vs("ihr-a", "1"))
        self.startup()
        self.packs.add(1, vs("ihr-a", "1", "2"))  # the same version, other content
        with self.assertRaisesMessage(
            CommandError, "versions are immutable, build v2 instead"
        ):
            self.startup()
        self.assertEqual(PackApply.objects.filter(outcome="failed").count(), 0)
