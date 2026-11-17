"""Tests for the per-worker cache runner (run inside the CARE backend image)."""

import unittest

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        CACHES={
            "default": {
                "BACKEND": "django_redis.cache.RedisCache",
                "LOCATION": "redis://redis:6379",
                "OPTIONS": {"KEY_PREFIX": "test_"},
            },
            "local": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
        }
    )
    django.setup()

import ihr_ci_runner  # noqa: E402
from django.core.cache import caches  # noqa: E402


class WorkerLocation(unittest.TestCase):
    def test_each_worker_gets_its_own_database(self):
        self.assertEqual(
            ihr_ci_runner.worker_location("redis://redis:6379", 1), "redis://redis:6379/1"
        )
        self.assertEqual(
            ihr_ci_runner.worker_location("redis://redis:6379/", 7), "redis://redis:6379/7"
        )
        self.assertEqual(
            ihr_ci_runner.worker_location("redis://redis:6379/0?ssl=x", 3),
            "redis://redis:6379/3?ssl=x",
        )

    def test_database_zero_and_out_of_range_workers_are_refused(self):
        for worker in (0, 16, 40):
            with self.assertRaises(RuntimeError):
                ihr_ci_runner.worker_location("redis://redis:6379", worker)

    def test_a_named_database_or_non_redis_url_is_refused(self):
        with self.assertRaises(RuntimeError):
            ihr_ci_runner.worker_location("redis://redis:6379/5", 1)
        with self.assertRaises(RuntimeError):
            ihr_ci_runner.worker_location("memcached://x:11211", 1)


class IsolateCaches(unittest.TestCase):
    def test_redis_caches_move_and_the_handler_rereads_settings(self):
        before = caches["default"]
        ihr_ci_runner.isolate_caches(4)
        self.assertEqual(settings.CACHES["default"]["LOCATION"], "redis://redis:6379/4")
        self.assertEqual(
            settings.CACHES["local"]["BACKEND"],
            "django.core.cache.backends.locmem.LocMemCache",
        )
        after = caches["default"]
        self.assertIsNot(before, after)
        self.assertIn("redis://redis:6379/4", str(after._server))

    def test_the_suite_uses_our_worker_initialiser(self):
        self.assertIs(
            ihr_ci_runner.PerWorkerCacheRunner.parallel_test_suite,
            ihr_ci_runner.PerWorkerCacheSuite,
        )
        self.assertIs(
            ihr_ci_runner.PerWorkerCacheSuite.init_worker.__func__
            if hasattr(ihr_ci_runner.PerWorkerCacheSuite.init_worker, "__func__")
            else ihr_ci_runner.PerWorkerCacheSuite.init_worker,
            ihr_ci_runner._init_worker,
        )


if __name__ == "__main__":
    unittest.main()
