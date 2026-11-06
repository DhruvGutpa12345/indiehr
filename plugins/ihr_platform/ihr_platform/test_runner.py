"""Run CARE's whole test suite inside a tenant schema (TENANCY_DESIGN.md §11.1).

The test database gets the shared apps in ``public`` plus one tenant, ``h_testhosp01``, reachable
from the test client as ``testserver``. Every new database connection (threads in CARE's
concurrency tests included) is pointed at that tenant.

Before the run, the test cache's own Redis database (15, checked) is emptied, and from then on
every key written to Redis is recorded (``WRITTEN_REDIS_KEYS``). After the last test the runner
audits every key present or written during the run, so expired or deleted keys are covered, and
fails the run on any key without a tenant schema prefix.

Parallel runs are not supported yet: Django clones the test database before we can add the
tenant schema, so ``--parallel`` is forced to 1.
"""

import importlib.util
import unittest
import weakref
from pathlib import Path

import redis.client
from django.db import connection, connections
from django.db.backends.signals import connection_created
from django.test.runner import DiscoverRunner

TEST_SCHEMA = "h_testhosp01"
EXCLUDED_CARE_TESTS = Path(__file__).with_name("care_tests_excluded.txt")
TEST_DOMAINS = ("testserver", "localhost", "127.0.0.1")
_initialised_connections = weakref.WeakSet()

TEST_REDIS_DB = 15  # settings/test.py; the runner refuses to flush any other database
WRITTEN_REDIS_KEYS = set()
# Keys that are deliberately global (not tenant data). Reviewed; keep short.
GLOBAL_REDIS_KEY_PREFIXES = ("_kombu", "celery", "unacked", "ihr:global:")

_SINGLE_KEY_WRITES = {
    "SET", "SETEX", "PSETEX", "SETNX", "SETRANGE", "GETSET", "GETEX", "APPEND", "INCR",
    "INCRBY", "INCRBYFLOAT", "DECR", "DECRBY", "LPUSH", "RPUSH", "LPUSHX", "RPUSHX", "LSET",
    "LINSERT", "LTRIM", "HSET", "HSETNX", "HMSET", "HINCRBY", "HINCRBYFLOAT", "SADD", "ZADD",
    "ZINCRBY", "EXPIRE", "PEXPIRE", "EXPIREAT", "PEXPIREAT", "RESTORE", "XADD", "PFADD",
}  # fmt: skip
_TWO_KEY_WRITES = {
    "RENAME",
    "RENAMENX",
    "COPY",
    "LMOVE",
    "BLMOVE",
    "RPOPLPUSH",
    "SMOVE",
}


def _text(value):
    return value.decode(errors="replace") if isinstance(value, bytes) else str(value)


def _keys_written(args):
    """The keys a Redis command writes, from its arguments (best effort, by command shape)."""
    command = _text(args[0]).upper()
    if command in _SINGLE_KEY_WRITES or command.endswith("STORE"):
        return args[1:2]
    if command in {"MSET", "MSETNX"}:
        return args[1::2]
    if command in _TWO_KEY_WRITES:
        return args[1:3]
    if command in {"EVAL", "EVALSHA"} and len(args) > 2:  # noqa: PLR2004
        return args[3 : 3 + int(args[2])]
    return ()


def _record_redis_writes(original):
    def execute(self, *args, **options):
        if args:
            WRITTEN_REDIS_KEYS.update(_text(key) for key in _keys_written(args))
        return original(self, *args, **options)

    return execute


def _start_recording_redis_writes():
    if getattr(redis.client.Redis.execute_command, "ihr_recording", False):
        return
    for cls, name in (
        (redis.client.Redis, "execute_command"),
        (redis.client.Pipeline, "pipeline_execute_command"),
        (redis.client.Pipeline, "immediate_execute_command"),
    ):
        wrapped = _record_redis_writes(getattr(cls, name))
        wrapped.ihr_recording = True
        setattr(cls, name, wrapped)


def _test_redis():
    from django_redis import get_redis_connection

    client = get_redis_connection("default")
    db = client.connection_pool.connection_kwargs.get("db")
    if db != TEST_REDIS_DB:
        msg = f"Test cache must use Redis database {TEST_REDIS_DB}, not {db}: refusing to flush."
        raise RuntimeError(msg)
    return client


def unprefixed_redis_keys():
    """Keys present now or written during the run that carry no tenant schema prefix."""
    from ihr_platform.models import SCHEMA_NAME_RE

    present = {_text(key) for key in _test_redis().scan_iter("*", count=1000)}
    return sorted(
        key
        for key in present | WRITTEN_REDIS_KEYS
        if not key.startswith(GLOBAL_REDIS_KEY_PREFIXES)
        and not SCHEMA_NAME_RE.match(key.split(":", 1)[0])
    )


def _point_new_connection_at_test_tenant(sender, connection, **kwargs):
    # Only connection objects we have never seen (e.g. one per worker thread) start in the test
    # tenant. A reconnect of a known connection keeps whatever schema the code chose, so
    # schema_context("public") etc. are respected.
    if connection.alias == "default" and connection not in _initialised_connections:
        _initialised_connections.add(connection)
        connection.set_schema(TEST_SCHEMA)


def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


class TenantTestRunner(DiscoverRunner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.parallel > 1:
            self.log("Tenant mode: running tests serially (parallel=1).")
            self.parallel = 1

    def build_suite(self, *args, **kwargs):
        """Leave out CARE's tests of features switched off in tenant mode (listed, reviewed)."""
        suite = super().build_suite(*args, **kwargs)
        excluded = [
            line.split("#")[0].strip()
            for line in EXCLUDED_CARE_TESTS.read_text().splitlines()
            if line.split("#")[0].strip()
        ]
        for label in excluded:
            if importlib.util.find_spec(label) is None:
                msg = f"care_tests_excluded.txt lists {label}, which no longer exists."
                raise RuntimeError(msg)
        kept, dropped = unittest.TestSuite(), 0
        for test in _flatten(suite):
            if test.id().startswith(tuple(f"{label}." for label in excluded)):
                dropped += 1
            else:
                kept.addTest(test)
        if dropped:
            self.log(
                f"Tenant mode: left out {dropped} CARE tests of switched-off features."
            )
        return kept

    def setup_databases(self, **kwargs):
        if not kwargs.get("aliases"):
            # Only database-free tests were selected (e.g. one mutation's guarding test):
            # Django creates no test database, so there is no tenant to set up.
            return super().setup_databases(**kwargs)
        # Start clean and record from the first write, so provisioning is audited too.
        _test_redis().flushdb()
        _start_recording_redis_writes()
        old_config = super().setup_databases(**kwargs)

        from ihr_platform.models import Domain, Hospital

        connection.set_schema_to_public()
        tenant = Hospital.objects.filter(schema_name=TEST_SCHEMA).first()
        if tenant is None:
            tenant = Hospital(
                schema_name=TEST_SCHEMA,
                slug="testhosp",
                name="Test Hospital",
                status=Hospital.Status.ACTIVE,
            )
            tenant.save()  # creates the schema and migrates every CARE app into it
        for domain in TEST_DOMAINS:
            Domain.objects.get_or_create(domain=domain, defaults={"tenant": tenant})

        connection.set_tenant(tenant)
        _initialised_connections.add(connections["default"])
        connection_created.connect(_point_new_connection_at_test_tenant)
        return old_config

    def suite_result(self, suite, result, **kwargs):
        """After every test has run, audit all Redis keys of the run; any unprefixed key fails it."""
        failures = super().suite_result(suite, result, **kwargs)
        if bad := unprefixed_redis_keys():
            self.log(f"Redis keys without a tenant prefix ({len(bad)}): {bad[:30]}")
            failures += 1
        else:
            self.log(
                f"Redis audit: {len(WRITTEN_REDIS_KEYS)} keys written, all tenant-prefixed."
            )
        return failures

    def teardown_databases(self, old_config, **kwargs):
        if not old_config:
            return super().teardown_databases(old_config, **kwargs)
        connection_created.disconnect(_point_new_connection_at_test_tenant)
        connection.set_schema_to_public()
        return super().teardown_databases(old_config, **kwargs)
