"""CARE's identifier config lookup read through its Redis cache (P1-T15b2a, Q-K64;
ihr_platform/identifier_cache.py; mutation row identifier-config-swap).

CARE's own ``get_config`` keeps a per-process dict nothing clears, so a changed config showed
its old display and rules in every other web process until a restart. Each "process" here is
CARE's class dict swapped for one of its own: the lookup must answer the change at once from
any of them, keep nothing in them, and keep its copy only under this hospital's schema.
"""

import hashlib
import importlib
import inspect
from unittest import mock

from care.emr.models.patient import (
    PatientIdentifierConfig,
    PatientIdentifierConfigCache,
)
from care.emr.resources.base import model_cache_key, model_string
from care.emr.resources.patient_identifier.spec import PatientIdentifierListSpec
from django.core import checks
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django_redis import get_redis_connection

from ihr_platform import identifier_cache


def _config(display):
    return {
        "use": "official",
        "system": "https://ihr.test/id/cache-probe",
        "display": display,
        "required": False,
        "unique": False,
        "regex": "",
        "retrieve_config": {"retrieve_with_year_of_birth": True},
    }


class SwapTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.row = PatientIdentifierConfig.objects.create(
            status="active", config=_config("Before")
        )
        self.addCleanup(self._forget)

    def _forget(self):
        cache.delete_pattern(
            model_cache_key(
                model_string(PatientIdentifierConfig), pk=self.row.external_id
            )
        )

    def process(self, local):
        """Run as one web process: CARE's class dict is ``local``."""
        return mock.patch.object(PatientIdentifierConfigCache, "configs", local)

    def test_the_swap_is_in_place_and_checked(self):
        self.assertEqual(identifier_cache.check(), [])
        self.assertEqual(identifier_cache.system_check(), [])
        swapped = PatientIdentifierConfigCache.__dict__["get_config"]
        with mock.patch.object(
            PatientIdentifierConfigCache, "get_config", swapped.ihr_original
        ):
            errors = identifier_cache.system_check()
            self.assertEqual([e.id for e in errors], ["ihr_platform.E025"])
            self.assertIn("per-process dict", errors[0].msg)
            identifier_cache.install()  # puts the swap back over CARE's own
            self.assertEqual(identifier_cache.check(), [])

    def test_the_check_is_registered(self):
        registered = checks.registry.registry.registered_checks
        self.assertIn(identifier_cache.system_check, registered)

    def test_a_lookup_care_dropped_is_left_to_the_check(self):
        """Review C3: install() never adds a lookup CARE no longer has, so E025 says so."""
        swapped = PatientIdentifierConfigCache.__dict__["get_config"]
        del PatientIdentifierConfigCache.get_config
        try:
            identifier_cache.install()
            self.assertNotIn("get_config", PatientIdentifierConfigCache.__dict__)
            (problem,) = identifier_cache.check()
            self.assertIn("has no get_config", problem)
        finally:
            PatientIdentifierConfigCache.get_config = swapped

    def test_the_answer_is_cares_own(self):
        expected = PatientIdentifierListSpec.serialize(self.row).to_json()
        for key in (self.row.external_id, str(self.row.external_id)):
            self.assertEqual(PatientIdentifierConfigCache.get_config(key), expected)
        self.row.delete()  # soft delete: old values keep resolving
        self.assertEqual(
            PatientIdentifierConfigCache.get_config(self.row.external_id)["id"],
            str(self.row.external_id),
        )
        with self.assertRaises(PatientIdentifierConfig.DoesNotExist):
            PatientIdentifierConfigCache.get_config(
                "00000000-0000-4000-8000-000000000000"
            )

    def test_a_save_is_seen_at_once_by_another_process(self):
        b_dict = {}
        with self.process(b_dict):
            seen = PatientIdentifierConfigCache.get_config(self.row.external_id)
        self.assertEqual(seen["config"]["display"], "Before")
        with self.process({}):  # another process saves a change (CARE's update, a pack)
            self.row.config = _config("After")
            self.row.save()
        with self.process(b_dict):
            seen = PatientIdentifierConfigCache.get_config(self.row.external_id)
        self.assertEqual(seen["config"]["display"], "After")
        self.assertEqual(b_dict, {}, "nothing is kept per process")
        # the shared copy is in Redis, under this hospital's schema only
        keys = [
            key.decode()
            for key in get_redis_connection("default").scan_iter(
                f"*{self.row.external_id}*"
            )
        ]
        self.assertTrue(keys, "the lookup reads CARE's Redis cache")
        self.assertTrue(
            all(key.startswith(f"{connection.schema_name}:") for key in keys), keys
        )

    def test_a_reader_before_the_commit_never_keeps_the_old_config(self):
        """Review C4: CARE clears the copy in post_save, inside the writer's transaction; a
        reader in another process before the commit caches the old row again. The copy is
        cleared once more after the commit."""
        key = model_cache_key(
            model_string(PatientIdentifierConfig),
            PatientIdentifierListSpec.__name__,
            self.row.external_id,
        )
        PatientIdentifierConfigCache.get_config(self.row.external_id)
        old = cache.get(key)
        self.assertIsNotNone(old, "the lookup reads CARE's Redis cache")
        with self.captureOnCommitCallbacks(execute=True):
            self.row.config = _config("After")
            self.row.save()  # CARE's post_save clears the copy here
            cache.set(key, old)  # another process reads the committed old row meanwhile
        seen = PatientIdentifierConfigCache.get_config(self.row.external_id)
        self.assertEqual(seen["config"]["display"], "After")


class CarePins(SimpleTestCase):
    """Review C3: CARE's lookup we replace and the callers that reach it, pinned at the
    upstream pin (identifier_cache.CARE_GET_CONFIG_SHA256, CARE_CALLERS). After an upstream
    sync changes one: re-read CARE's change, make ours answer the same, pin the new value."""

    def test_cares_lookup_and_its_callers_are_pinned(self):
        swapped = PatientIdentifierConfigCache.__dict__["get_config"]
        source = inspect.getsource(swapped.ihr_original.__func__)
        self.assertEqual(
            hashlib.sha256(source.encode()).hexdigest(),
            identifier_cache.CARE_GET_CONFIG_SHA256,
            "CARE's get_config changed: re-read it, then pin the new digest",
        )
        for module, calls in identifier_cache.CARE_CALLERS.items():
            text = inspect.getsource(importlib.import_module(module))
            self.assertEqual(
                text.count("PatientIdentifierConfigCache.get_config("), calls, module
            )
