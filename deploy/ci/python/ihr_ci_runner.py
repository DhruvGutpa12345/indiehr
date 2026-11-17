"""CI-only test runner for CARE's plain suite: one Redis database per parallel worker.

CARE's own suite runs with ``--parallel --shuffle`` against one Redis. Two of its test
modules call ``cache.clear()`` in setUp (``test_reset_password_api``, ``test_valueset_api``),
and with django-redis that flushes the whole Redis database, so one worker could wipe
another worker's rate-limit counters or cached favourites mid-test. Each worker's database
copy also hands out the same primary keys, so cache keys built from ids collide too. That
caused the rare failures of ``test_add_favorite``, the rate-limiting tests and an
organization test (TRACKER "CI flakes", 2026-11-13/14).

This runner gives worker N the Redis database N (1-15; database 0 stays the default for
anything outside the tests). It changes no CARE code or test, only where each worker's
cache lives. It is used only by the plain Backend job (``deploy/ci/plain.override.yaml``).
"""

from __future__ import annotations

import django.test.runner as django_runner
from django.conf import settings
from django.core.cache import caches
from django.test.runner import DiscoverRunner, ParallelTestSuite
from django.utils.connection import Local

REDIS_DATABASES = 16  # Redis' default `databases`; 0 is left alone


def worker_location(location: str, worker_id: int) -> str:
    """The cache LOCATION for one worker: the same server, database ``worker_id``."""
    if not 1 <= worker_id < REDIS_DATABASES:
        raise RuntimeError(
            f"ihr_ci_runner: worker {worker_id} has no Redis database of its own "
            f"(1-{REDIS_DATABASES - 1}); run with --parallel {REDIS_DATABASES - 1} or fewer"
        )
    if not location.startswith(("redis://", "rediss://")):
        raise RuntimeError(f"ihr_ci_runner: not a redis URL: {location!r}")
    scheme, rest = location.split("://", 1)
    host, _, path = rest.partition("/")
    if path.split("?", 1)[0] not in ("", "0"):
        raise RuntimeError(
            f"ihr_ci_runner: the cache already names a Redis database ({location!r}); "
            "refusing to guess"
        )
    query = path.split("?", 1)[1] if "?" in path else ""
    return f"{scheme}://{host}/{worker_id}" + (f"?{query}" if query else "")


def isolate_caches(worker_id: int) -> None:
    """Point every Redis cache at this worker's database and drop open connections."""
    for alias, config in settings.CACHES.items():
        location = config.get("LOCATION")
        if isinstance(location, str) and location.startswith(("redis://", "rediss://")):
            config["LOCATION"] = worker_location(location, worker_id)
        elif isinstance(location, (list, tuple)):
            config["LOCATION"] = [worker_location(item, worker_id) for item in location]
    caches.close_all()
    caches.__dict__.pop("settings", None)  # re-read settings.CACHES on next use
    caches._settings = None
    caches._connections = Local(caches.thread_critical)


def _init_worker(counter, *args, **kwargs):
    django_runner._init_worker(counter, *args, **kwargs)
    isolate_caches(django_runner._worker_id)


class PerWorkerCacheSuite(ParallelTestSuite):
    init_worker = _init_worker


class PerWorkerCacheRunner(DiscoverRunner):
    parallel_test_suite = PerWorkerCacheSuite
