"""Per-process database limits (P1-T23, Q-K92/Q-K93; design docs/reviews/p1-t23/design.md §1b).

Each Django process opens its connections with three Postgres limits as libpq startup options
(``-c statement_timeout=... -c lock_timeout=... -c idle_in_transaction_session_timeout=...``),
chosen from ``PROFILES`` by what the process is (a web process, a worker, an owner one-off):

* a request that holds a lock or runs long can no longer take the pool with it (web: 30 s);
* a session left idle inside a transaction (ATOMIC_REQUESTS wraps every request) cannot sit on
  its locks for ever (web: 60 s), and an abandoned owner shell cannot hold ACCESS EXCLUSIVE for
  hours (owner: 30 min).

Why startup options, not ``ALTER ROLE ... SET``: they reach dev, CI and staging whatever role
the process connects as, and the role model keeps its rule that a role stores no setting of its
own (db_roles.role_problems, S9). They are not a security control: any session may ``SET`` them
again. They bound accidents and noisy neighbours. The connection check (db_roles.limits_problems)
reads the live values back, so a pooler or proxy that drops startup options is found.

Code that legitimately needs more or less sets it locally (``SET LOCAL``), as it always did.
``watch`` (the audit watch) keeps its own values; the dumper is not a Django process.
"""

import os
import sys
from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured

SETTINGS = (
    "statement_timeout",
    "lock_timeout",
    "idle_in_transaction_session_timeout",
)


@dataclass(frozen=True)
class Limits:
    """Milliseconds; 0 is Postgres's "no limit"."""

    statement: int
    lock: int
    idle_in_transaction: int

    def values(self):
        return {
            "statement_timeout": self.statement,
            "lock_timeout": self.lock,
            "idle_in_transaction_session_timeout": self.idle_in_transaction,
        }

    def options(self):
        return " ".join(f"-c {name}={value}" for name, value in self.values().items())


PROFILES = {
    # gunicorn (backend and the back-channel service), celery beat, the dev web server
    "web": Limits(statement=30_000, lock=10_000, idle_in_transaction=60_000),
    # celery workers, the staff worker, app-role one-offs ("report queue" of §6.16)
    "worker": Limits(statement=300_000, lock=30_000, idle_in_transaction=300_000),
    # migrate, admin, provisioner, restore, fleet runner, loaders: no statement or lock limit
    # at session level (their DDL paths set their own SET LOCAL lock_timeout); an abandoned
    # session must not hold ACCESS EXCLUSIVE for hours
    "owner": Limits(statement=0, lock=0, idle_in_transaction=1_800_000),
    # the audit watch (settings/watch.py also makes its sessions read-only)
    "watch": Limits(statement=300_000, lock=10_000, idle_in_transaction=60_000),
    # the test runner, in both role modes
    "test": Limits(statement=30_000, lock=10_000, idle_in_transaction=60_000),
}
OFF = "off"


def derive(process, db_roles, db_role, argv=None):
    """The profile for a process that sets no IHR_DB_LIMITS (one-offs, dev): by its name."""
    argv = list(sys.argv if argv is None else argv)
    fixed = {"gunicorn": "web", "manage:runserver": "web", "manage:test": "test"}
    if process in fixed:
        return fixed[process]
    if process == "celery":
        return "web" if "beat" in argv else "worker"
    # Development (roles off) connects as the superuser: owner work
    if process.startswith("manage:") and (db_roles == "off" or db_role == "owner"):
        return "owner"
    return "worker"


def chosen(settings_get, environ=None):
    """The profile name for this process: IHR_DB_LIMITS if set, else derived."""
    from ihr_platform.settings.tenancy import process_name

    environ = os.environ if environ is None else environ
    name = environ.get("IHR_DB_LIMITS", "")
    if not name:
        name = derive(
            process_name(),
            settings_get("IHR_DB_ROLES", ""),
            settings_get("IHR_DB_ROLE", ""),
        )
    if name != OFF and name not in PROFILES:
        msg = f"IHR_DB_LIMITS must be one of {', '.join(sorted(PROFILES))} or off, not {name!r}."
        raise ImproperlyConfigured(msg)
    return name


def expected(profile):
    """The Limits a connection of this profile must show; None when limits are off."""
    return None if profile in (None, "", OFF) else PROFILES[profile]


def current_expected():
    """The Limits this process's connections must show (from settings)."""
    from django.conf import settings

    return expected(getattr(settings, "IHR_DB_LIMITS", ""))


def options_string(profile, *, read_only=False):
    """The libpq ``options`` value for a profile ("" when off)."""
    limits = expected(profile)
    parts = []
    if read_only:
        parts.append("-c default_transaction_read_only=on")
    if limits is not None:
        parts.append(limits.options())
    return " ".join(parts)


def apply_to_database_options(database_options, profile):
    """Put the profile's startup options into a DATABASES entry's OPTIONS, unless the settings
    module already chose its own (the watch): never merged, so a stray value cannot widen them."""
    if "options" in database_options:
        return
    text = options_string(profile)
    if text:
        database_options["options"] = text


def observed(cursor):
    """{name: milliseconds} as this connection shows them."""
    cursor.execute(
        "SELECT name, setting FROM pg_settings WHERE name = ANY(%s)", [list(SETTINGS)]
    )
    return {name: int(value) for name, value in cursor.fetchall()}


def mismatches(cursor, limits=None):
    """[text] for each limit this connection shows differently from the process's profile."""
    limits = current_expected() if limits is None else limits
    if limits is None:
        return []
    live = observed(cursor)
    return [
        f"{name} is {live.get(name)} ms here, the profile says {wanted} ms"
        for name, wanted in limits.values().items()
        if live.get(name) != wanted
    ]
