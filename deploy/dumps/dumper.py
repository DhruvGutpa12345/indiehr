"""Nightly per-hospital dumps (P1-T22a; docs/reviews/p1-t22/design.md, "After the attack";
ADR-007 amendment; owner decision Q-K33 pending). Runbook: deploy/staging/README.md, "Hospital
dumps". Sizes and timings: docs/reviews/p1-t22/size-model.md.

Every night at IHR_DUMP_TIME (03:30 IST: after the 02:00 release window and the 02:30 pgBackRest
backup) each active, suspended and offboarding hospital is dumped on its own, IHR_DUMP_PARALLEL
(2) at a time. A hospital held for a release (P1-T18) is skipped and reported; provisioning and
archived ones are not dumped. For each hospital:

1. its advisory lock (the one provisioning, the fleet runner and pack apply take; waited for up
   to IHR_DUMP_LOCK_WAIT seconds, then reported busy), on a session of its own;
2. on that same session (two sessions a dump: it and pg_dump's), one REPEATABLE READ snapshot,
   exported (pg_export_snapshot): in it the manifest's facts (the schema's migrations and
   fingerprint, the audit trail's head, every sequence, the relation count, the extensions) and
   the hospital's own rows in `public` (every row of a public table that points at the
   hospital, one level), then `pg_dump -Fc -n <schema> --snapshot=<id> --no-owner --no-acl
   --lock-wait-timeout=60s` of exactly that snapshot. When pg_dump exits the session ends, and
   with it the lock: the upload and the checks below never keep the hospital from a release or
   provisioning (review C1);
3. the dump and the public rows sealed (deploy/lib/sealer.py: Fernet, one MiB a token, with
   IHR_DUMP_KEY, a key of its own), uploaded once per name (If-None-Match, Content-MD5) into the
   dumps bucket, read back as they arrive (nothing more on disk) and checked: size and SHA-256 of
   the stored version, every token decrypts, the plaintext's size and SHA-256, `pg_restore
   --list` holds that schema's objects only; then the manifest: ids, hashes, sizes, the stored
   versions, fingerprints and sequence values only (no personal data), chained per hospital (the
   SHA-256 of the one before) and authenticated (HMAC with a key derived from IHR_DUMP_KEY);
4. after the run, the night's check: each new dump fetched, checked whole and restored
   schema-only into a throwaway Postgres inside this container (no network, its own socket,
   removed afterwards).

Every reader takes the version a manifest recorded (a manifest: the first version of its name),
and any name under dumps/ written more than once, or hidden by a delete marker, is a problem
(DumpNotAuthentic): the dumper never writes a name twice, so a second version is a replacement,
whoever holds the keys (review S1).

Names: dumps/<daily|weekly>/<hospital uuid>/<YYYY-MM-DD>.r<N>.{dump.enc,public.enc,manifest.json};
Sunday's run (IHR_DUMP_WEEKLY_DAY) writes under weekly/. Kept 14 nights and 4 Sundays: the owner's
dump-expire (deploy/dumps/expire.py) erases older ones after writing an authenticated erasure
record; the bucket's Governance lock (30 days) and its versioning keep everything else. This
process's key may add, read and list versions under dumps/ and read erasure/, never delete.

    dumper.py run                    the service: metrics on :9110 (Prometheus only), the nightly
                                     run and check, the bucket's holdings read every 6 hours
    dumper.py once [--slug S]...     one run now (all due hospitals without a dump since today's
                                     run time, or the named ones), then its check
    dumper.py check [--deep [--all]] manifests authentic and chained, every name written once,
                                     objects present with their sizes; --deep: the newest dump of
                                     each hospital (--all: every dump) read back, decrypted, read
                                     whole by pg_restore, listed and restored schema-only
    dumper.py get <uuid> <date|latest> <dump|public>
                                     the object decrypted and checked, on stdout (P1-T22b restores)
    dumper.py manifest <uuid> <date|latest>
                                     that dump's manifest, authentic and chained, as JSON (the
                                     restore's pre-checks; no personal data)
    dumper.py probe                  the bucket's lock and what this key may do (go-live)
    dumper.py role                   this process's database role is ihr_dump as modelled (exit 1:
                                     it is not; 2: no session to check it with)
    dumper.py drill-mark <hospital uuid> <ok|failed>
                                     records a restore drill (P1-T22b's drill calls it; until then
                                     DumpDrillOverdue fires 35 days after the first dump ever)

The fixed lines, one per hospital whose dump failed or was skipped (deploy/logs/alerts/dumps.yml):
    ihr-dump: hospital=<slug> outcome=failed reason=<lock|disk|window|dump|upload|verify|check|error>
    ihr-dump: hospital=<slug> outcome=held
Everything else this process prints starts "dumper: " with every control character escaped.
"""

import collections
import concurrent.futures
import contextlib
import datetime as dt
import fcntl
import gzip
import hashlib
import http.server
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse


def _shared_lib():
    """The shared modules: /opt/ihr-lib in a container (a read-only mount), else deploy/lib beside
    this folder (tests, the owner's machine). Never the working directory (python3 -I)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in ("/opt/ihr-lib", os.path.normpath(os.path.join(here, "..", "lib"))):
        if os.path.isfile(os.path.join(path, "sealer.py")):
            return path
    sys.exit("dumper: the shared sealer (deploy/lib/sealer.py) is missing")


sys.path.insert(0, _shared_lib())
import chain  # noqa: E402
import locked_bucket  # noqa: E402
from locked_bucket import taken_error  # noqa: E402
from sealer import CHUNK, HEX64, Sealer, mac_ok, record_bytes  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
PURPOSE = "ihr-dump"
FORMAT = "ihr-dump/1"
ERASURE_FORMAT = "ihr-dump-erasure/1"
PREFIX = "dumps/"
PROBE_PREFIX = "dumps/ihr-key-check/"
KINDS = ("daily", "weekly")
RETENTION_DAYS = {"daily": 14, "weekly": 28}
LOCK_DAYS = 30  # the bucket's Governance lock: a little longer than the longest retention
# A proof's own SeaweedFS, as the dumper (compose.dumps.local.yaml) and a one-off command
# (dumps-init --local) reach it. Only there is an erasure record written with --allow-short
# (expire.py refuses that flag anywhere else) taken for a proof's; anywhere else it is checked
# against the retention like any other record (review S2)
STAND_INS = ("http://s3:8333", "http://s3-dumpsout:8333")
DUE = ("active", "suspended", "offboarding")
KEY_RE = re.compile(r"^dumps/(daily|weekly)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/"
                    r"(\d{4}-\d{2}-\d{2})\.r([1-9]\d{0,3})\.(dump\.enc|public\.enc|manifest\.json)$")
SCHEMA_RE = re.compile(r"^h_[a-z0-9]{10}$")
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{2,30}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
REASONS = ("lock", "disk", "window", "dump", "upload", "verify", "check", "error")
# Sessions as ihr_dump: each dump holds two while pg_dump runs (the lock's, which also exports the
# snapshot, and pg_dump's); the role's CONNECTION LIMIT (scripts/staging.sh) is
# SESSIONS_PER_DUMP x MAX_PARALLEL + 2, the two for `dumper.py role` and the hospitals' listing
MAX_PARALLEL = 4
SESSIONS_PER_DUMP = 2
# The size model (docs/reviews/p1-t22/size-model.md): a sealed zstd dump is about 0.20 of the
# schema's bytes on disk; 0.30 and a fixed margin leave room
SEALED_PER_DISK_BYTE = 0.30
DISK_MARGIN = 512 * 1024 * 1024
# Room beyond the sealed object for a copy on disk (the night's check, check --deep, get)
COPY_MARGIN = 64 * 1024 * 1024


def one_line(text):
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in str(text))


def say(text):
    print("dumper: " + one_line(text), flush=True)


def emit(slug, outcome, reason=None):
    """The fixed line the log alerts read: the slug only when it is slug-shaped."""
    slug = slug if isinstance(slug, str) and SLUG_RE.match(slug) else "invalid-slug"
    line = f"ihr-dump: hospital={slug} outcome={outcome}"
    if reason:
        line += f" reason={reason if reason in REASONS else 'error'}"
    print(line, flush=True)


def env(name, default=None):
    v = os.environ.get(name, default)
    if v is None:
        sys.exit(f"dumper: {name} is not set")
    return v


def hhmm(text, name):
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", text)
    if not m:
        sys.exit(f"dumper: {name} must be HH:MM")
    return dt.time(int(m.group(1)), int(m.group(2)))


class Config:
    def __init__(self, environ=None):
        e = os.environ if environ is None else environ
        g = e.get
        self.at = hhmm(g("IHR_DUMP_TIME", "03:30"), "IHR_DUMP_TIME")
        quiet = g("IHR_DUMP_QUIET", "01:45-03:25").split("-")
        if len(quiet) != 2:
            sys.exit("dumper: IHR_DUMP_QUIET must be HH:MM-HH:MM")
        self.quiet = (hhmm(quiet[0], "IHR_DUMP_QUIET"), hhmm(quiet[1], "IHR_DUMP_QUIET"))
        self.weekly_day = int(g("IHR_DUMP_WEEKLY_DAY", "7"))  # ISO weekday, 7 = Sunday
        self.parallel = int(g("IHR_DUMP_PARALLEL", "2"))
        if not 1 <= self.parallel <= MAX_PARALLEL:
            sys.exit(f"dumper: IHR_DUMP_PARALLEL is 1 to {MAX_PARALLEL} (ihr_dump's connection limit allows "
                     f"{SESSIONS_PER_DUMP} sessions a dump)")
        self.lock_wait = int(g("IHR_DUMP_LOCK_WAIT", "600"))
        self.compress = g("IHR_DUMP_COMPRESS", "zstd")
        if self.compress not in ("zstd", "gzip", "lz4"):
            sys.exit("dumper: IHR_DUMP_COMPRESS is zstd, gzip or lz4")
        self.workdir = g("IHR_DUMP_WORKDIR", "/state")
        self.refresh = int(g("IHR_DUMP_REFRESH_SECONDS", str(6 * 3600)))
        self.port = int(g("IHR_DUMP_METRICS_PORT", "9110"))
        self.scrapers = [s for s in g("IHR_DUMP_SCRAPERS", "prometheus").split(",") if s]
        if not 1 <= self.weekly_day <= 7:
            sys.exit("dumper: IHR_DUMP_WEEKLY_DAY is 1 (Monday) to 7 (Sunday)")

    def in_quiet(self, t):
        a, b = self.quiet
        return a <= t < b if a <= b else (t >= a or t < b)


def now_ist():
    return dt.datetime.now(IST)


def iso(t):
    return t.astimezone(IST).isoformat()


# ---- names ------------------------------------------------------------------------------------


def base_key(kind, hospital, day, rev):
    return f"{PREFIX}{kind}/{hospital}/{day.isoformat()}.r{rev}"


def object_keys(kind, hospital, day, rev):
    b = base_key(kind, hospital, day, rev)
    return f"{b}.dump.enc", f"{b}.public.enc", f"{b}.manifest.json"


def parse_key(key):
    """(kind, hospital, day, rev, part) of a dump key, or None."""
    m = KEY_RE.match(key)
    if not m:
        return None
    try:
        day = dt.date.fromisoformat(m.group(3))
    except ValueError:
        return None
    return m.group(1), m.group(2), day, int(m.group(4)), m.group(5)


def _hexsha(v):
    return isinstance(v, str) and bool(HEX64.match(v))


def on_stand_in(environ=None):
    """True when this process's dumps bucket is a proof's stand-in (STAND_INS)."""
    return (os.environ if environ is None else environ).get("IHR_DUMP_ENDPOINT") in STAND_INS


def erasure_name(key):
    """One erasure, whichever of its records: the closing record (<stamp>-done.json) repeats
    the first (<stamp>.json)."""
    return key[:-len("-done.json")] + ".json" if key.endswith("-done.json") else key


def _obj_ok(o, key):
    """An object's entry: its name, sizes, hashes and the version the store gave it (None only
    on a store without versioning, which the lock check reports)."""
    return (isinstance(o, dict) and o.get("key") == key and isinstance(o.get("bytes"), int)
            and _hexsha(o.get("sha256")) and _hexsha(o.get("plain_sha256"))
            and isinstance(o.get("plain_bytes"), int)
            and (o.get("version") is None or (isinstance(o["version"], str) and 0 < len(o["version"]) <= 1024)))


def valid_manifest(m, key, sealer):
    """True when m is an authentic manifest stored under its own name."""
    if not isinstance(m, dict) or m.get("format") != FORMAT or not mac_ok(sealer.mac_key, m):
        return False
    p = parse_key(key)
    if not p or p[4] != "manifest.json":
        return False
    kind, hospital, day, rev, _ = p
    try:
        dkey, pkey, mkey = object_keys(kind, hospital, day, rev)
        ok = (m["kind"] == kind and m["hospital"] == hospital and m["date"] == day.isoformat()
              and m["revision"] == rev and mkey == key and SCHEMA_RE.match(m["schema"])
              and _obj_ok(m["dump"], dkey) and _obj_ok(m["public"], pkey)
              and isinstance(m["sequences"], dict) and isinstance(m["relations"], int)
              and (m["previous"] is None or _hexsha(m["previous"])) and isinstance(m["created"], str))
    except (KeyError, TypeError, ValueError):
        return False
    return bool(ok)


# ---- what the bucket holds ----------------------------------------------------------------------


class Holdings:
    """Every manifest and erasure record in the bucket, validated, and each hospital's chain.

    Read from the bucket's version listing, never its current versions alone: the dumper writes
    each name once, so a name with a second version or a delete marker was replaced or hidden
    (with the dumper's key or any other) and is a problem, and a manifest is read in its first
    version (review S1). Erasure records (the owner's, under erasure/) are trusted only when
    authentic, naming each manifest they erase by its dump name, and erasing only what was past
    its retention when they were written (review S2). The one exception is a proof's: a record
    written with --allow-short, read from a proof's stand-in (proof; STAND_INS). Every erasure
    written with --allow-short, whichever its records' status, is noted as an early erasure (its
    own gauge and alert), once per erasure (its record and its closing record are one). The
    record's contents are what its writer says: the dates it names are checked, not proven
    (docs/reviews/p1-t22/design.md, Known limits)."""

    def __init__(self, bucket, sealer, now=None, proof=None):
        now = (now or now_ist()).astimezone(IST)
        self.proof = on_stand_in() if proof is None else proof
        self.manifests = {}  # sha256 of its bytes -> (key, manifest)
        self.bad = []  # keys that are not authentic
        self.problems = []  # chain problems and names written twice (text)
        self.taken = collections.defaultdict(int)  # (kind, hospital, day) -> highest revision of any key
        self.erased = {}  # sha256 of an erased manifest -> its previous
        self.erased_days = []  # the dates of every manifest an authentic record erased
        early_erasures = set()
        self.oldest_object = None  # date of the oldest dump name present
        names = collections.defaultdict(list)
        for v in bucket.versions(PREFIX):
            if not v["key"].startswith(PROBE_PREFIX):
                names[v["key"]].append(v)
        for key in sorted(names):
            vs = names[key]
            kept = [v for v in vs if not v["marker"]]
            if len(kept) > 1 or len(kept) < len(vs):
                self.problems.append(f"{key}: {len(kept)} version(s) and {len(vs) - len(kept)} delete marker(s); "
                                     "the dumper writes a name once (replaced or hidden?)")
            p = parse_key(key)
            if p:
                k = (p[0], p[1], p[2])
                self.taken[k] = max(self.taken[k], p[3])
                if self.oldest_object is None or p[2] < self.oldest_object:
                    self.oldest_object = p[2]
            if not p or p[4] != "manifest.json":
                if not p:
                    self.bad.append(key)
                continue
            if not kept:
                continue  # only a delete marker: the problem above names it
            first = min(kept, key=_written)
            try:
                raw = bucket.get(key, first["version"]).read()
                m = json.loads(raw)
                ok = valid_manifest(m, key, sealer)
            except (ValueError, TypeError, UnicodeDecodeError):
                ok = False
            if not ok:
                self.bad.append(key)
                continue
            self.manifests[hashlib.sha256(raw).hexdigest()] = (key, m)
        for key in bucket.keys("erasure/"):
            try:
                rec = json.loads(bucket.get(key).read())
                ok = (isinstance(rec, dict) and rec.get("format") == ERASURE_FORMAT
                      and mac_ok(sealer.erasure_key, rec) and isinstance(rec.get("erased"), list))
                entries = rec["erased"] if ok else []
                for e in entries:
                    if not (isinstance(e, dict) and _hexsha(e.get("sha256"))):
                        ok = False
                erased_at = dt.datetime.fromisoformat(rec["erased_at"]).astimezone(IST) if ok else None
            except (ValueError, TypeError, UnicodeDecodeError, KeyError, AttributeError):
                ok = False
            if not ok:
                self.bad.append(key)
                continue
            early = bool(rec.get("allow_short"))
            # Counted whatever the record's status: a single record that says "completed" is
            # an erasure too (review S2)
            if early:
                early_erasures.add(erasure_name(key))
            # Only a proof's own records may erase inside the retention; anywhere else a record
            # with allow_short is checked like any other
            proof = early and self.proof
            if erased_at > now + dt.timedelta(hours=1):
                self.problems.append(f"{key}: an erasure record dated in the future ({erased_at.isoformat()})")
            for e in entries:
                p = parse_key(e["key"]) if isinstance(e.get("key"), str) else None
                if not p or p[4] != "manifest.json":
                    # An erasure that cannot be dated never bridges the chain: nothing would
                    # check it against the retention (review S2)
                    text = (f"{erasure_name(key)}: erases {e['sha256'][:12]}... without naming its manifest "
                            f"({str(e.get('key'))[:80]!r})")
                    if text not in self.problems:
                        self.problems.append(text)
                    continue
                prev = e.get("previous")
                self.erased[e["sha256"]] = prev if _hexsha(prev) else None
                self.erased_days.append(p[2])
                if not proof and (erased_at.date() - p[2]).days <= RETENTION_DAYS[p[0]]:
                    # Named once per erasure (its closing record repeats the first)
                    text = (f"{erasure_name(key)}: erases {e['key']} inside its retention ({RETENTION_DAYS[p[0]]} days)"
                            + (" with --allow-short, off a proof's stand-in" if early else ""))
                    if text not in self.problems:
                        self.problems.append(text)
        self.early_erasures = sorted(early_erasures)
        self.by_hospital = collections.defaultdict(dict)
        for sha, (_, m) in self.manifests.items():
            self.by_hospital[m["hospital"]][sha] = m
        self.order, self.heads = {}, {}
        for hospital, present in self.by_hospital.items():
            def label(sha, present=present, hospital=hospital):
                m = present[sha]
                return f"{hospital} {m['kind']} {m['date']} r{m['revision']}"
            order, head, problems = chain.follow(present, self.erased, lambda sha, p=present: p[sha]["created"], label)
            self.order[hospital], self.heads[hospital] = order, head
            self.problems.extend(problems)

    def head(self, hospital):
        return self.heads.get(hospital)

    def newest(self, hospital):
        """The hospital's newest manifest (by creation), or None."""
        ms = self.by_hospital.get(hospital) or {}
        return max(ms.values(), key=lambda m: m["created"]) if ms else None

    def next_revision(self, kind, hospital, day):
        return self.taken.get((kind, hospital, day), 0) + 1


def _written(v):
    """Orders the versions of one name, first written first (the listing gives the newest
    first within a name: a later position is older when the times tie)."""
    t = v["modified"]
    return (t.timestamp() if isinstance(t, dt.datetime) else 0, -v["order"])


# ---- the database -------------------------------------------------------------------------------


def pg_env(url):
    """libpq's variables for pg_dump, from the address: the password never on a command line."""
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("postgres", "postgresql") or not u.hostname or not u.username:
        sys.exit("dumper: IHR_DUMP_DATABASE_URL is not a postgres:// address")
    out = {"PGHOST": u.hostname, "PGPORT": str(u.port or 5432), "PGUSER": urllib.parse.unquote(u.username),
           "PGDATABASE": u.path.lstrip("/") or "ihr", "PGAPPNAME": "ihr-dumper", "PGCONNECT_TIMEOUT": "10"}
    if u.password:
        out["PGPASSWORD"] = urllib.parse.unquote(u.password)
    return out


def lock_key(slug):
    """The hospital's advisory lock, as provisioning/runner.py's lock_key() (taken by its
    exclusive()) has it: the same key, so a dump and provisioning, the fleet runner or pack apply
    never run together. A pinned vector holds the two copies together (review C10): both suites
    assert lock_key("alpha") == -8154903275597228642."""
    return int.from_bytes(hashlib.sha256(slug.encode()).digest()[:8], "big", signed=True)


ROLE_SQL = """
SELECT current_user::text, session_user::text, r.rolsuper,
       r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls, r.rolinherit,
       ARRAY(SELECT g.rolname::text FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid
             WHERE m.member = r.oid ORDER BY 1),
       EXISTS (SELECT 1 FROM pg_db_role_setting s WHERE s.setrole = r.oid),
       has_database_privilege(current_database(), 'CREATE'),
       has_database_privilege(current_database(), 'TEMPORARY'),
       (SELECT count(*) FROM pg_namespace n WHERE has_schema_privilege(n.oid, 'CREATE')
          AND n.nspname !~ '^pg_(toast_)?temp_'),
       (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         CROSS JOIN unnest(ARRAY['INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) w(p)
         WHERE (n.nspname = 'public' OR n.nspname ~ '^h_[a-z0-9]{10}$') AND c.relkind IN ('r', 'p')
           AND has_table_privilege(c.oid, w.p)),
       EXISTS (SELECT 1 FROM pg_class c WHERE c.relowner = r.oid)
         OR EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspowner = r.oid)
         OR EXISTS (SELECT 1 FROM pg_proc p WHERE p.proowner = r.oid)
FROM pg_roles r WHERE r.rolname = current_user
"""


def role_problems(row):
    """Why this connection must not serve as the dumper's role (P1-T19's model: ihr_dump, LOGIN,
    a member of pg_read_all_data only, no attributes, no stored settings; it writes nothing)."""
    user, session, superuser, attributes, _inherit, groups, stored, db_create, db_temp, schema_create, writes, owns = row
    found = []
    if user != "ihr_dump" or session != "ihr_dump":
        found.append(f"connected as {session} ({user}), not ihr_dump")
    if superuser:
        found.append("a superuser")
    if attributes:
        found.append("has CREATEROLE, CREATEDB, REPLICATION or BYPASSRLS")
    if list(groups) != ["pg_read_all_data"]:
        found.append(f"member of {', '.join(groups) or 'nothing'}; the model: pg_read_all_data only")
    if stored:
        found.append("has stored settings (ALTER ROLE ... SET)")
    if db_create or db_temp or schema_create:
        found.append("may CREATE or make TEMPORARY tables")
    if writes:
        found.append(f"holds {writes} write privilege(s) on tables in public or a hospital schema")
    if owns:
        found.append("owns objects in the database")
    return found


HOSPITALS_SQL = """
SELECT h.uuid::text, h.slug, h.schema_name, h.status,
       (s.upgrading_since IS NOT NULL OR s.restore_operation IS NOT NULL),
       CASE WHEN s.restore_operation IS NOT NULL THEN 'restore' ELSE coalesce(s.hold_reason, '') END,
       extract(epoch FROM coalesce(h.activated_at, h.created_on))::bigint,
       (SELECT coalesce(sum(pg_total_relation_size(c.oid)), 0)::bigint FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = h.schema_name AND c.relkind IN ('r', 'm'))
FROM public.ihr_platform_hospital h
LEFT JOIN public.ihr_platform_hospitalschemastate s ON s.hospital_id = h.id
WHERE h.schema_name <> 'public'
ORDER BY h.slug
"""


class Hospital:
    def __init__(self, uuid, slug, schema, status, held, hold_reason, since, schema_bytes=0):
        self.uuid, self.slug, self.schema, self.status = uuid, slug, schema, status
        self.held, self.hold_reason, self.since = held, hold_reason, since
        self.schema_bytes = int(schema_bytes or 0)  # tables, TOAST and indexes on disk

    def __repr__(self):
        return f"Hospital({self.slug})"


class CannotConnect(Exception):
    """No session as ihr_dump (its connection limit, the database down, a wrong password): the
    role was not checked, which is not the role being wrong (`dumper.py role` exits 2)."""


class Source:
    """The live database, as ihr_dump."""

    def __init__(self, url):
        self.url = url
        self.pgenv = pg_env(url)

    def connect(self, autocommit=True):
        import psycopg

        try:
            return psycopg.connect(self.url, autocommit=autocommit, application_name="ihr-dumper",
                                   connect_timeout=10)
        except psycopg.OperationalError as exc:
            raise CannotConnect((str(exc).strip().splitlines() or ["?"])[-1][:300]) from None

    def role_problems(self):
        with self.connect() as conn:
            return role_problems(conn.execute(ROLE_SQL).fetchone())

    def hospitals(self):
        with self.connect() as conn:
            return [Hospital(*row) for row in conn.execute(HOSPITALS_SQL).fetchall()]

    def lock(self, slug, wait):
        """A session holding the hospital's advisory lock (closing it releases the lock), or
        None when another run held it for `wait` seconds. The snapshot is taken on this same
        session (snapshot()): two sessions a dump, not three (review C7)."""
        conn = self.connect()
        deadline = time.monotonic() + wait
        while True:
            if conn.execute("SELECT pg_try_advisory_lock(%s)", [lock_key(slug)]).fetchone()[0]:
                return conn
            if time.monotonic() >= deadline:
                conn.close()
                return None
            time.sleep(min(15, max(1, wait / 20)))

    def snapshot(self, hospital, conn):
        """On the lock's session (conn), an open REPEATABLE READ, READ ONLY transaction:
        (snapshot id, facts, public rows). The advisory lock is the session's, so it outlives the
        transaction; the caller keeps the session open until pg_dump is done, then closes it,
        which ends both."""
        conn.autocommit = False
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        snap = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
        conn.execute("SET LOCAL lock_timeout = '60s'")
        facts, rows = read_facts(conn, hospital)
        return snap, facts, rows


def read_facts(conn, hospital):
    """The manifest's facts and the hospital's public rows, in the snapshot. Facts are ids,
    names of tables and sequences, counts, hashes and sequence values only."""
    from psycopg import sql

    s = hospital.schema
    q = lambda text, args=(): conn.execute(text, args).fetchall()  # noqa: E731
    row = q("SELECT h.status, (s.upgrading_since IS NOT NULL OR s.restore_operation IS NOT NULL), h.id "
            "FROM public.ihr_platform_hospital h "
            "LEFT JOIN public.ihr_platform_hospitalschemastate s ON s.hospital_id = h.id WHERE h.uuid = %s",
            [hospital.uuid])
    if not row:
        raise RuntimeError("the hospital is gone")
    status, held, hid = row[0]
    snap_at, xmin, xmax, version = q("SELECT now(), pg_snapshot_xmin(pg_current_snapshot())::text, "
                                     "pg_snapshot_xmax(pg_current_snapshot())::text, current_setting('server_version')")[0]
    fp = q("SELECT schema_fingerprint FROM public.ihr_platform_hospitalschemastate WHERE hospital_id = %s", [hid])
    migrations = q(sql.SQL("SELECT app || '.' || name FROM {}.django_migrations ORDER BY 1").format(sql.Identifier(s)))
    seal = q(sql.SQL("SELECT seq, encode(hash, 'hex'), upper_txid::text FROM {}.ihr_audit_seal "
                     "ORDER BY seq DESC LIMIT 1").format(sql.Identifier(s)))
    entries = q(sql.SQL("SELECT max(id), max(txid)::text FROM {}.ihr_audit_entry").format(sql.Identifier(s)))
    seqs = q("SELECT sequencename::text, last_value FROM pg_sequences WHERE schemaname = %s ORDER BY 1", [s])
    rels = q("SELECT count(*), coalesce(sum(pg_total_relation_size(c.oid)) FILTER (WHERE c.relkind IN ('r', 'm')), 0) "
             "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s", [s])[0]
    exts = q("SELECT extname::text, extversion FROM pg_extension ORDER BY 1")
    facts = {
        "status": status,
        "held": bool(held),
        "snapshot": {"at": iso(snap_at), "xmin": xmin, "xmax": xmax},
        "server_version": version,
        "schema_fingerprint": fp[0][0] if fp else "",
        "migrations": len(migrations),
        "migrations_sha256": hashlib.sha256("\n".join(r[0] for r in migrations).encode()).hexdigest(),
        "audit": {"seal_seq": seal[0][0] if seal else 0, "seal_hash": seal[0][1] if seal else "",
                  "seal_upper_txid": seal[0][2] if seal else "",
                  "max_entry_id": entries[0][0] or 0, "max_entry_txid": entries[0][1] or ""},
        "sequences": {name: value for name, value in seqs},
        "relations": int(rels[0]),
        "schema_bytes": int(rels[1]),
        "extensions": [[n, v] for n, v in exts],
    }
    # The hospital's own rows in public: its row, and every row of a public table with a foreign
    # key to it (domains, schema state, invitations, access requests, operations...): encrypted
    # with the dump, never in the manifest (they hold names and e-mail addresses)
    rows = {"ihr_platform_hospital": q(
        "SELECT row_to_json(x)::text FROM public.ihr_platform_hospital x WHERE x.id = %s", [hid])}
    fks = q("""SELECT c.relname::text, a.attname::text FROM pg_constraint k
               JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
               JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
               WHERE k.contype = 'f' AND n.nspname = 'public' AND array_length(k.conkey, 1) = 1
                 AND k.confrelid = 'public.ihr_platform_hospital'::regclass ORDER BY 1, 2""")
    for table, column in fks:
        got = q(sql.SQL("SELECT row_to_json(x)::text FROM public.{} x WHERE x.{} = %s").format(
            sql.Identifier(table), sql.Identifier(column)), [hid])
        rows.setdefault(table, []).extend(got)
    rows = {t: [json.loads(r[0]) for r in v] for t, v in rows.items()}
    return facts, rows


# ---- pg_dump, pg_restore ------------------------------------------------------------------------


class Tools:
    """pg_dump and pg_restore (the tests replace this)."""

    def __init__(self, pgenv=None):
        self.pgenv = pgenv or {}

    def dump(self, schema, snapshot, compress, stderr):
        """pg_dump of one schema in the exported snapshot, its archive on stdout."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("IHR_")}
        env.update(self.pgenv)
        return subprocess.Popen(  # noqa: S603 (fixed program, checked arguments)
            ["pg_dump", "-Fc", f"--compress={compress}", "-n", schema, "--strict-names",
             f"--snapshot={snapshot}", "--no-owner", "--no-acl", "--lock-wait-timeout=60s"],
            stdout=subprocess.PIPE, stderr=stderr, env=env)

    def toc(self, plain_chunks):
        """`pg_restore --list` of a dump fed as chunks: its lines. It reads the table of contents
        only and stops; the caller reads the rest (read_through)."""
        return _feed(["pg_restore", "--list"], plain_chunks, early_exit=True).decode(errors="replace").splitlines()

    def read_all(self, plain_chunks):
        """`pg_restore -f /dev/null` of a dump fed as chunks: reads and decompresses every data
        block (a broken or short data section fails here; check --deep)."""
        _feed(["pg_restore", "-f", "/dev/null"], plain_chunks)


def _feed(cmd, chunks, env=None, early_exit=False):
    """chunks into cmd's stdin; its stdout. early_exit: the program may stop reading before the
    end (pg_restore --list, --schema-only); otherwise one that stops early has failed."""
    stopped = False
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err, \
            subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=err, env=env) as p:  # noqa: S603
        try:
            for c in chunks:
                p.stdin.write(c)
        except BrokenPipeError:
            stopped = True
        finally:
            try:
                p.stdin.close()
            except BrokenPipeError:
                stopped = True
        rc = p.wait()
        out.seek(0)
        err.seek(0)
        if rc != 0:
            raise RuntimeError(f"{cmd[0]} exit {rc}: {err.read()[-300:].decode(errors='replace')}")
        if stopped and not early_exit:
            raise RuntimeError(f"{cmd[0]} stopped reading before the end of its input")
        return out.read()


def read_through(chunks, consumer):
    """consumer(chunks)'s result; whatever it left unread is then read too (pg_restore --list
    stops after the table of contents), so every token is decrypted and the checks at the end
    of the chunks (sizes and SHA-256) always run (review C3)."""
    it = iter(chunks)
    result = consumer(it)
    for _ in it:
        pass
    return result


TOC_RE = re.compile(r"^\d+; \d+ \d+ (?P<rest>.*)$")
# pg_dump's entry types of more than one word (pg_backup_archiver.c, pg_dump.c); an unknown one
# reads as a type followed by a schema that is not ours, and fails the check (closed)
TOC_TYPES = {
    "TABLE DATA", "SEQUENCE SET", "SEQUENCE OWNED BY", "FK CONSTRAINT", "CHECK CONSTRAINT",
    "INDEX ATTACH", "TABLE ATTACH", "DEFAULT ACL", "MATERIALIZED VIEW", "MATERIALIZED VIEW DATA",
    "TEXT SEARCH CONFIGURATION", "TEXT SEARCH DICTIONARY", "TEXT SEARCH PARSER", "TEXT SEARCH TEMPLATE",
    "EVENT TRIGGER", "FOREIGN TABLE", "LARGE OBJECT", "LARGE OBJECTS", "STATISTICS DATA", "ACCESS METHOD",
    "FOREIGN DATA WRAPPER", "FOREIGN SERVER", "USER MAPPING", "ROW SECURITY", "OPERATOR CLASS",
    "OPERATOR FAMILY", "SHELL TYPE", "PUBLICATION TABLE", "PUBLICATION TABLES IN SCHEMA", "TRANSFORM",
}


def toc_entry(line):
    """(type, schema, rest) of a `pg_restore --list` line, or None."""
    m = TOC_RE.match(line)
    if not m:
        return None
    words = m.group("rest").split(" ")
    for n in (5, 4, 3, 2):
        if " ".join(words[:n]) in TOC_TYPES and len(words) > n:
            return " ".join(words[:n]), words[n], " ".join(words[n + 1:])
    if len(words) < 2 or not re.fullmatch(r"[A-Z]+", words[0]):
        return None
    return words[0], words[1], " ".join(words[2:])
TOC_REFUSED = ("DATABASE", "EXTENSION", "ROLE", "TABLESPACE", "EVENT TRIGGER", "PUBLICATION", "SUBSCRIPTION",
               "FOREIGN DATA WRAPPER", "SERVER", "USER MAPPING")


def toc_problems(lines, schema):
    """What in a dump's table of contents is not that hospital schema's own."""
    found, entries, has_schema = [], 0, False
    for line in lines:
        if not line.strip() or line.startswith(";"):
            continue
        e = toc_entry(line)
        if not e:
            found.append(f"unreadable entry: {line[:80]}")
            continue
        entries += 1
        kind, where, rest = e
        if kind == "SCHEMA":
            if rest.split(" ")[0] != schema:
                found.append(f"another schema: {rest.split(' ')[0]}")
            has_schema = True
        elif kind in TOC_REFUSED:
            found.append(f"a {kind} entry")
        elif where not in (schema, "-"):
            found.append(f"an object in {where}")
    if not has_schema:
        found.append(f"no SCHEMA {schema} entry")
    if not entries:
        found.append("empty")
    return found


# ---- one hospital -------------------------------------------------------------------------------


class Failed(Exception):
    def __init__(self, reason, text):
        super().__init__(text)
        self.reason = reason


class Held(Exception):
    pass


def _gz_json(doc):
    return gzip.compress(json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str).encode(), mtime=0)


def _lines(body):
    """The lines of a stream as it arrives, each with its newline (the last may lack one)."""
    buf = b""
    for block in iter(lambda: body.read(CHUNK), b""):
        buf += block
        start = 0
        while (i := buf.find(b"\n", start)) >= 0:
            yield buf[start:i + 1]
            start = i + 1
        buf = buf[start:]
    if buf:
        yield buf


def _decrypted(sealer, lines, obj):
    """The plaintext chunks of an object's lines (each token authenticated as it is decrypted);
    at the end the object's size and SHA-256, then the plaintext's, checked against obj (its
    manifest entry; None: not checked). Raises RuntimeError when any differs."""
    sealed, plain = hashlib.sha256(), hashlib.sha256()
    n = pn = 0
    for line in lines:
        sealed.update(line)
        n += len(line)
        token = line.strip()
        if token:
            chunk = sealer.fernet.decrypt(token)
            plain.update(chunk)
            pn += len(chunk)
            yield chunk
    if obj is None:
        return
    if n != obj["bytes"] or sealed.hexdigest() != obj["sha256"]:
        raise RuntimeError(f"{obj['key']}: its size or SHA-256 differs from its manifest")
    if pn != obj["plain_bytes"] or plain.hexdigest() != obj["plain_sha256"]:
        raise RuntimeError(f"{obj['key']}: the decrypted object differs from its manifest")


def streamed(bucket, sealer, obj):
    """The plaintext chunks of a stored object (the version its manifest names) as it arrives,
    nothing written to disk; checked at the end (_decrypted). For the read-back of a dump just
    uploaded: no second copy on the work volume (review C2)."""
    return _decrypted(sealer, _lines(bucket.get(obj["key"], obj.get("version"))), obj)


def need_room(nbytes, what):
    """Fails early, with a clear message, when the temporary folder cannot hold a copy."""
    where = tempfile.gettempdir()
    st = os.statvfs(where)
    free = st.f_bavail * st.f_frsize
    if free < nbytes + COPY_MARGIN:
        raise RuntimeError(f"{what}: {where} has {free // 2**20} MiB free, a copy needs about "
                           f"{(nbytes + COPY_MARGIN) // 2**20} MiB")


def fetch_checked(bucket, obj, dst):
    """The stored object (the version its manifest names) copied into dst, its size and SHA-256
    checked before anything reads it."""
    body = bucket.get(obj["key"], obj.get("version"))
    digest, n = hashlib.sha256(), 0
    for block in iter(lambda: body.read(CHUNK), b""):
        digest.update(block)
        n += len(block)
        dst.write(block)
    if n != obj["bytes"] or digest.hexdigest() != obj["sha256"]:
        raise RuntimeError(f"{obj['key']}: its size or SHA-256 differs from its manifest")
    dst.seek(0)


def opened(sealer, sealed, obj=None):
    """The plaintext chunks of a fetched copy; at the end, the plaintext checked against obj."""
    return _decrypted(sealer, sealed, obj)


def _toc_checked(tools, m, chunks):
    problems = toc_problems(read_through(chunks, tools.toc), m["schema"])
    if problems:
        raise RuntimeError(f"{m['dump']['key']}: not that hospital's schema alone: {'; '.join(problems[:3])}")


def verify_dump(bucket, sealer, tools, m, scratch=None, full=False):
    """The dump and public objects match the manifest, in the versions it recorded: the size and
    SHA-256 of both, every token decrypts, the plaintext's size and SHA-256, the dump's table of
    contents is that schema's only, the public rows parse. Without scratch or full, read as they
    arrive (the nightly read-back). With them, the dump is first copied and checked whole (nothing
    restores from bytes not yet checked): full, `pg_restore` reads every data block; scratch, it
    restores schema-only with the manifest's relations."""
    json.loads(gzip.decompress(b"".join(streamed(bucket, sealer, m["public"]))))
    if scratch is None and not full:
        _toc_checked(tools, m, streamed(bucket, sealer, m["dump"]))
        return
    need_room(m["dump"]["bytes"], m["dump"]["key"])
    with tempfile.TemporaryFile() as sealed:
        fetch_checked(bucket, m["dump"], sealed)
        _toc_checked(tools, m, opened(sealer, sealed, m["dump"]))
        if full:
            sealed.seek(0)
            read_through(opened(sealer, sealed, m["dump"]), tools.read_all)
        if scratch is not None:
            sealed.seek(0)
            got = read_through(opened(sealer, sealed, m["dump"]),
                               lambda chunks: scratch.restore_schema(m["schema"], m.get("extensions") or [], chunks))
            if got != m["relations"]:
                raise RuntimeError(f"{m['dump']['key']}: {got} relations restored schema-only, its manifest says {m['relations']}")


def disk_need(cfg, hospital, previous):
    """Bytes the work volume needs for this hospital: the larger of its last sealed dump and
    public object with a fifth more, and the size model's share of the schema's bytes on disk
    now (a first dump, or one that grew), plus the margin. The read-back streams (no second
    copy), so this is the dump's whole need."""
    by_model = int(hospital.schema_bytes * SEALED_PER_DISK_BYTE)
    by_last = int((previous["dump"]["bytes"] + previous["public"]["bytes"]) * 1.2) if previous else 0
    return max(by_model, by_last) + DISK_MARGIN


class Reserve:
    """Work-volume space promised to dumps running now (two at a time share one disk). A dump
    that does not fit next to the others waits for them (review C2); it is refused (reason=disk)
    only when it would not fit on the volume with nothing else running. The volume's free space
    is read when nothing is promised, and the promises are counted against that figure, so a
    running dump's own writes are never counted twice."""

    def __init__(self, workdir):
        self.workdir = workdir
        self.cond = threading.Condition()
        self.held = 0
        self.base = 0

    def free(self):
        st = os.statvfs(self.workdir)
        return st.f_bavail * st.f_frsize

    def take(self, n):
        with self.cond:
            while True:
                if self.held == 0:
                    self.base = self.free()
                    if n > self.base:
                        return False
                if self.base - self.held >= n:
                    self.held += n
                    return True
                self.cond.wait()

    def give(self, n):
        with self.cond:
            self.held -= n
            self.cond.notify_all()


def dump_one(cfg, source, tools, bucket, sealer, hospital, kind, day, previous_sha, rev, workdir, tries=5):
    """Locks, snapshots, dumps and seals one hospital, ends the lock's session, then uploads and
    checks; returns (manifest, raw). Raises Held (skipped) or Failed(reason)."""
    lock = source.lock(hospital.slug, cfg.lock_wait)
    if lock is None:
        raise Failed("lock", f"{hospital.slug}: another run held its lock for {cfg.lock_wait} s")
    started = time.monotonic()
    try:
        # The wait for the lock (up to IHR_DUMP_LOCK_WAIT) may have run into the quiet window
        if cfg.in_quiet(now_ist().time()):
            raise Failed("window", "reached the quiet window waiting for the hospital's lock: left for the next run")
        with tempfile.TemporaryDirectory(dir=workdir) as tmp:
            sealed_path = os.path.join(tmp, "dump.enc")
            public_path = os.path.join(tmp, "public.enc")
            try:
                try:
                    snap, facts, rows = source.snapshot(hospital, lock)
                except Exception as exc:  # noqa: BLE001
                    raise Failed("dump", f"snapshot: {type(exc).__name__}: {exc}") from exc
                if facts["held"]:
                    raise Held()
                with open(os.path.join(tmp, "stderr"), "w+b") as errf:
                    proc = tools.dump(hospital.schema, snap, cfg.compress, errf)
                    try:
                        with open(sealed_path, "wb") as dst:
                            dump_obj = sealer.seal(proc.stdout, dst)
                    finally:
                        proc.stdout.close()
                        rc = proc.wait()
                    errf.seek(0)
                    err = errf.read()
            finally:
                # The snapshot is pg_dump's from its first query on, and pg_dump has exited: the
                # session ends here, and with it the transaction and the hospital's lock. The
                # upload and the checks below never hold the hospital (review C1)
                lock.close()
            if rc != 0:
                raise Failed("dump", f"pg_dump exit {rc}: {err[-300:].decode(errors='replace')}")
            if dump_obj["plain_bytes"] == 0:
                raise Failed("dump", "pg_dump wrote nothing")
            with open(public_path, "wb") as dst:
                import io

                public_obj = sealer.seal(io.BytesIO(_gz_json(rows)), dst)
            seconds = round(time.monotonic() - started, 1)
            for _ in range(tries):
                dkey, pkey, mkey = object_keys(kind, hospital.uuid, day, rev)
                try:
                    try:
                        dver = bucket.put_file(dkey, sealed_path, "application/octet-stream")
                        pver = bucket.put_file(pkey, public_path, "application/octet-stream")
                    except Exception as exc:
                        if taken_error(exc):
                            raise
                        raise Failed("upload", f"{type(exc).__name__}: {exc}") from exc
                    manifest = {
                        "format": FORMAT,
                        "hospital": hospital.uuid,
                        "schema": hospital.schema,
                        "kind": kind,
                        "date": day.isoformat(),
                        "revision": rev,
                        "created": iso(now_ist()),
                        **{k: v for k, v in facts.items() if k not in ("status", "held")},
                        "hospital_status": facts["status"],
                        "dump": {"key": dkey, **dump_obj, "version": dver,
                                 "format": f"pg_dump -Fc --compress={cfg.compress}"},
                        "public": {"key": pkey, **public_obj, "version": pver,
                                   "tables": {t: len(v) for t, v in sorted(rows.items())}},
                        "encryption": "fernet, 1 MiB chunks (deploy/lib/sealer.py)",
                        "seconds": seconds,
                        "previous": previous_sha,
                    }
                    manifest["mac"] = sealer.mac(manifest)
                    raw = record_bytes(manifest)
                    try:
                        verify_dump(bucket, sealer, tools, manifest)
                    except Exception as exc:  # noqa: BLE001
                        raise Failed("verify", f"{type(exc).__name__}: {exc}") from exc
                    try:
                        bucket.put_bytes(mkey, raw, "application/json")
                    except Exception as exc:
                        if taken_error(exc):
                            raise
                        raise Failed("upload", f"manifest: {type(exc).__name__}: {exc}") from exc
                    return manifest, raw
                except Failed:
                    raise
                except Exception as exc:  # noqa: BLE001 (only a taken name is retried)
                    if not taken_error(exc):
                        raise
                    say(f"{dkey} or its companions exist already (an interrupted upload, or not ours): using revision {rev + 1}")
                    rev += 1
            raise Failed("upload", f"{tries} revision names in a row were taken")
    finally:
        lock.close()  # (closed above once pg_dump is done; closing again does nothing)


# ---- a run --------------------------------------------------------------------------------------


class Status:
    METRICS = {
        "last_run_start_timestamp_seconds": "When the last run started.",
        "last_run_end_timestamp_seconds": "When the last run ended.",
        "last_run_seconds": "How long the last run took.",
        "running": "1 while a run is going.",
        "running_since_timestamp_seconds": "When the run going now started (0: none).",
        "hospitals": "Hospitals due a dump (active, suspended, offboarding) at the last run.",
        "hospitals_dumped": "Hospitals dumped and checked by the last run.",
        "hospitals_failed": "Hospitals whose dump failed in the last run (the log line names them).",
        "hospitals_held": "Hospitals skipped by the last run because a release held them.",
        "oldest_good_timestamp_seconds": "Over every due hospital, the time of its newest good dump "
                                         "(one never dumped: when it went live); the alert's age. "
                                         "Updated as each hospital's dump is done.",
        "stale_hospitals": "Due hospitals with no good dump in the last 26 hours.",
        "first_good_timestamp_seconds": "When the oldest dump still held was made.",
        "first_dump_ever_timestamp_seconds": "When the first dump was made, also when it has been erased "
                                             "(erasure records name it; never raised): the drill alert's clock.",
        "oldest_object_timestamp_seconds": "Start of the oldest dump date in the bucket (erasure due after 28 days).",
        "last_drill_timestamp_seconds": "When the last restore drill passed (0: never; P1-T22b).",
        "bad_manifests": "Manifests or erasure records that are not authentic, chain problems, names "
                         "written more than once or hidden, erasures inside the retention.",
        "early_erasures": "Erasures written with --allow-short (a proof on the local stand-in only), each once, whatever its records say.",
        "check_problems": "Problems the last check found (a dump that does not read back, decrypt, "
                          "list or restore schema-only).",
        "last_check_timestamp_seconds": "When the last check ended.",
        "bucket_locked": "1 if the dumps bucket has Object Lock for at least 30 days and versioning (-1: not readable).",
        "last_run_bytes": "Sealed bytes the last run uploaded.",
        "largest_bytes": "The largest sealed dump and public object among each hospital's newest.",
        "work_need_bytes": "Free bytes the work volume needs for a night: IHR_DUMP_PARALLEL dumps the "
                           "size of the largest, with a fifth more and the margin each.",
        "largest_seconds": "The longest single-hospital dump of the last run.",
        "work_free_bytes": "Free bytes on the work volume.",
        "role_ok": "1 if the database role is ihr_dump as modelled (0: refused, nothing is dumped).",
    }

    def __init__(self, path=None, fresh=False):
        """path: the status file in the work volume, shared by the service and the one-off runs
        and checks (`dumps-now`, `dumps-check`): every change is written there under a file
        lock and every scrape reads it, so the metrics show the newest run, whoever ran it.
        fresh (the service starting): a run left "running" by a killed process is cleared; a
        one-off process never clears it (the service may be in the middle of a run)."""
        self.lock = threading.Lock()
        self.values = {"running": 0, "running_since_timestamp_seconds": 0}
        self.path = path
        if path:
            with self._locked():
                self._load()
                if fresh:
                    self.values.update(running=0, running_since_timestamp_seconds=0)
                self._save()

    @contextlib.contextmanager
    def _locked(self):
        with open(self.path + ".lock", "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _load(self):
        try:
            with open(self.path) as f:
                saved = json.load(f)
            self.values.update({k: v for k, v in saved.items() if k in self.METRICS and isinstance(v, int | float)})
        except (OSError, ValueError):
            pass

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.values, f)
        os.replace(tmp, self.path)

    def set(self, **kw):
        with self.lock:
            if not self.path:
                self.values.update(kw)
                return
            with self._locked():
                self._load()
                self.values.update(kw)
                self._save()

    def get(self, name):
        with self.lock:
            return self.values.get(name)

    def render(self):
        with self.lock:
            if self.path:
                with self._locked():
                    self._load()
            v = dict(self.values)
        lines = []
        for name, help_ in self.METRICS.items():
            if v.get(name) is not None:
                lines += [f"# HELP ihr_dump_{name} {help_}", f"# TYPE ihr_dump_{name} gauge", f"ihr_dump_{name} {v[name]}"]
        return ("\n".join(lines) + "\n").encode()


def bucket_state(bucket):
    return locked_bucket.lock_state(bucket, LOCK_DAYS)


def good_time(manifest, hospital):
    """When a due hospital last had a good dump: its newest manifest's creation, else when it
    went live (the rule DumpTooOld reads)."""
    return dt.datetime.fromisoformat(manifest["created"]).timestamp() if manifest else float(hospital.since or 0)


def good_ages(times, now):
    """The gauges of the newest good dumps (times: one per due hospital)."""
    times = list(times)
    return {"oldest_good_timestamp_seconds": int(min(times)) if times else int(now.timestamp()),
            "stale_hospitals": sum(1 for t in times if now.timestamp() - t > 26 * 3600)}


def refresh(bucket, sealer, status, hospitals=None, now=None, held=None, parallel=2):
    """The metrics read from the bucket: the newest good dump of every due hospital, chain
    problems, the oldest dump name, the largest newest dump and what the work volume needs.
    Returns the Holdings."""
    now = now or now_ist()
    held = held or Holdings(bucket, sealer, now)
    vals = {"bad_manifests": len(held.bad) + len(held.problems), "bucket_locked": bucket_state(bucket),
            "early_erasures": len(held.early_erasures)}
    if hospitals is not None:
        due = [h for h in hospitals if h.status in DUE]
        vals.update(good_ages((good_time(held.newest(h.uuid), h) for h in due), now))
    if held.manifests:
        vals["first_good_timestamp_seconds"] = int(min(dt.datetime.fromisoformat(m["created"]).timestamp()
                                                       for _, m in held.manifests.values()))
    # The drill's clock: the first dump ever, never raised by expiry (erasure records keep the
    # dates of what they erased, and the gauge never goes up; review C5)
    firsts = [vals.get("first_good_timestamp_seconds")]
    firsts += [int(dt.datetime.combine(d, dt.time(0), IST).timestamp()) for d in held.erased_days]
    firsts.append(status.get("first_dump_ever_timestamp_seconds") or None)
    firsts = [f for f in firsts if f]
    if firsts:
        vals["first_dump_ever_timestamp_seconds"] = min(firsts)
    if held.oldest_object:
        vals["oldest_object_timestamp_seconds"] = int(dt.datetime.combine(held.oldest_object, dt.time(0), IST).timestamp())
    # Sized from what the bucket holds, not from the last run (a catch-up run that dumps nothing,
    # or one small hospital, never lowers it; review C2)
    newest = [held.newest(h) for h in held.by_hospital]
    largest = max((m["dump"]["bytes"] + m["public"]["bytes"] for m in newest if m), default=0)
    vals.update(largest_bytes=largest, work_need_bytes=parallel * (int(largest * 1.2) + DISK_MARGIN))
    status.set(**vals)
    for key in held.bad:
        say(f"not authentic: {key}")
    for p in held.problems:
        say(f"chain: {p}")
    for key in held.early_erasures:
        say(f"early erasure: {key} (--allow-short: a proof on the local stand-in only)")
    return held


def run(cfg, source, tools, bucket, sealer, status, only=None, catch_up=False, now=None, scratch_factory=None):
    """One run: every due hospital without a good dump since today's run time (catch_up: in the
    last 26 hours; only: those slugs), then the check of the new dumps. Returns
    {slug: "ok"|"held"|reason}."""
    now = (now or now_ist()).astimezone(IST)
    started = time.time()
    status.set(running=1, running_since_timestamp_seconds=int(started), last_run_start_timestamp_seconds=int(started))
    results, done = {}, []
    try:
        problems = source.role_problems()
        if problems:
            status.set(role_ok=0)
            say(f"refused: this process's database role is not ihr_dump as modelled: {'; '.join(problems)}")
            print("ihr-dump: refused role", flush=True)
            return None
        status.set(role_ok=1)
        hospitals = source.hospitals()
        held = Holdings(bucket, sealer, now)
        day = now.date()
        kind = "weekly" if now.isoweekday() == cfg.weekly_day else "daily"
        since = (now - dt.timedelta(hours=26)) if catch_up else dt.datetime.combine(day, cfg.at, IST)
        todo = []
        for h in hospitals:
            if h.status not in DUE or (only and h.slug not in only):
                continue
            if not SCHEMA_RE.match(h.schema) or not UUID_RE.match(h.uuid):
                results[h.slug] = "error"
                emit(h.slug, "failed", "error")
                continue
            if h.held:
                results[h.slug] = "held"
                emit(h.slug, "held")
                say(f"{h.slug}: held ({h.hold_reason or 'release'}): skipped")
                continue
            m = held.newest(h.uuid)
            if m and dt.datetime.fromisoformat(m["created"]) >= since and not only:
                continue
            todo.append(h)
        reserve = Reserve(cfg.workdir)
        chain_lock = threading.Lock()
        # The newest good dump of each due hospital, moved on as each dump is done: the gauges
        # follow the run, not only its end (a long night never pages DumpTooOld for hospitals it
        # already dumped; review C4)
        newest = {h.uuid: good_time(held.newest(h.uuid), h) for h in hospitals if h.status in DUE}

        def one(h):
            if cfg.in_quiet(now_ist().time()):
                raise Failed("window", "inside the quiet window (the release window and the backup): left for the next run")
            need = disk_need(cfg, h, held.newest(h.uuid))
            if not reserve.take(need):
                raise Failed("disk", f"the work volume has {reserve.free() // 2**20} MiB free, this dump needs about {need // 2**20} MiB")
            try:
                # take() may have waited for another dump's room into the quiet window (a
                # catch-up run or dumps-now late in the evening): checked again (dump_one checks
                # once more after the hospital's lock, whose wait can run into it too)
                if cfg.in_quiet(now_ist().time()):
                    raise Failed("window", "reached the quiet window waiting for room on the work volume: left for the next run")
                with chain_lock:
                    previous = held.head(h.uuid)
                    rev = held.next_revision(kind, h.uuid, day)
                    held.taken[(kind, h.uuid, day)] = rev  # a parallel worker never takes it too
                return dump_one(cfg, source, tools, bucket, sealer, h, kind, day, previous, rev, cfg.workdir)
            finally:
                reserve.give(need)

        longest, total = 0.0, 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.parallel) as pool:
            futures = {pool.submit(one, h): h for h in todo}
            for fut in concurrent.futures.as_completed(futures):
                h = futures[fut]
                try:
                    m, raw = fut.result()
                except Held:
                    results[h.slug] = "held"
                    emit(h.slug, "held")
                    continue
                except Failed as exc:
                    results[h.slug] = exc.reason
                    emit(h.slug, "failed", exc.reason)
                    say(f"{h.slug}: {exc.reason}: {exc}")
                    continue
                except Exception as exc:  # noqa: BLE001 (the others go on; the line names it)
                    results[h.slug] = "error"
                    emit(h.slug, "failed", "error")
                    say(f"{h.slug}: {type(exc).__name__}: {exc}")
                    continue
                results[h.slug] = "ok"
                done.append(m)
                newest[h.uuid] = dt.datetime.fromisoformat(m["created"]).timestamp()
                status.set(**good_ages(newest.values(), now_ist()))
                total += m["dump"]["bytes"] + m["public"]["bytes"]
                longest = max(longest, m["seconds"])
                say(f"{h.slug}: dumped ({m['kind']} {m['date']} r{m['revision']}, {m['dump']['bytes']} bytes sealed, "
                    f"{m['relations']} relations, {m['seconds']} s)")
        # The night's check: each new dump restored schema-only in a throwaway server
        if done and scratch_factory is not None:
            failed = check_new(bucket, sealer, tools, done, scratch_factory)
            by_uuid = {h.uuid: h for h in hospitals}
            for uuid in failed:
                h = by_uuid[uuid]
                results[h.slug] = "check"
                emit(h.slug, "failed", "check")
            status.set(check_problems=len(failed), last_check_timestamp_seconds=int(time.time()))
        ended = time.time()
        status.set(hospitals=sum(1 for h in hospitals if h.status in DUE),
                   hospitals_dumped=sum(1 for v in results.values() if v == "ok"),
                   hospitals_failed=sum(1 for v in results.values() if v not in ("ok", "held")),
                   hospitals_held=sum(1 for v in results.values() if v == "held"),
                   last_run_bytes=total, largest_seconds=int(longest),
                   last_run_end_timestamp_seconds=int(ended), last_run_seconds=int(ended - started),
                   work_free_bytes=Reserve(cfg.workdir).free())
        refresh(bucket, sealer, status, hospitals, parallel=cfg.parallel)
        say(f"run ended in {int(ended - started)} s: {sum(1 for v in results.values() if v == 'ok')} dumped, "
            f"{sum(1 for v in results.values() if v == 'held')} held, "
            f"{sum(1 for v in results.values() if v not in ('ok', 'held'))} failed")
        return results
    finally:
        status.set(running=0, running_since_timestamp_seconds=0)


def check_new(bucket, sealer, tools, manifests, scratch_factory):
    """Each manifest's dump restored schema-only; returns the hospitals that failed."""
    failed = []
    with scratch_factory() as scratch:
        for m in manifests:
            try:
                verify_dump(bucket, sealer, tools, m, scratch)
            except Exception as exc:  # noqa: BLE001
                failed.append(m["hospital"])
                say(f"check: {m['hospital']} {m['date']} r{m['revision']}: {type(exc).__name__}: {exc}")
    return failed


# ---- the throwaway server of the check ----------------------------------------------------------


class Scratch:
    """A Postgres server inside this container for schema-only restores: its own data folder in
    the work volume, no TCP (a Unix socket in that folder), fsync off; removed on exit."""

    def __init__(self, workdir):
        self.workdir = workdir

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="scratch-", dir=self.workdir)
        self.data = os.path.join(self.dir, "data")
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("IHR_", "PG"))},
                    "PGHOST": self.dir, "PGUSER": "scratch", "PGDATABASE": "postgres"}
        subprocess.run(["initdb", "-D", self.data, "-U", "scratch", "--auth=trust", "-E", "UTF8",  # noqa: S607
                        "--no-sync", "--no-instructions"], check=True, capture_output=True, env=self.env)
        opts = (f"-c listen_addresses='' -c unix_socket_directories={self.dir} -c fsync=off "
                "-c shared_buffers=16MB -c max_locks_per_transaction=512 -c full_page_writes=off")
        subprocess.run(["pg_ctl", "-D", self.data, "-o", opts, "-w", "-l", os.path.join(self.dir, "log"),  # noqa: S607
                        "start"], check=True, capture_output=True, env=self.env)
        return self

    def psql(self, sqltext, db="postgres"):
        r = subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-qAt", "-d", db, "-c", sqltext],  # noqa: S607
                           check=True, capture_output=True, env=self.env)
        return r.stdout.decode().strip()

    def restore_schema(self, schema, extensions, chunks):
        """The dump's schema restored schema-only into a fresh database; its relation count."""
        if not SCHEMA_RE.match(schema):
            raise RuntimeError("not a hospital schema name")
        self.psql("DROP DATABASE IF EXISTS check_restore")
        self.psql("CREATE DATABASE check_restore")
        for name, _version in extensions:
            if re.fullmatch(r"[a-z0-9_]{1,63}", name or "") and name != "plpgsql":
                self.psql(f'CREATE EXTENSION IF NOT EXISTS "{name}"', "check_restore")
        # Schema-only reads the table of contents and stops; the caller reads the rest
        # (read_through), so every token is still decrypted and checked
        _feed(["pg_restore", "--schema-only", "--no-owner", "--no-acl", "--exit-on-error",
               "-d", "check_restore"], chunks, env=self.env, early_exit=True)
        n = self.psql("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                      f"WHERE n.nspname = '{schema}'", "check_restore")
        return int(n)

    def __exit__(self, *a):
        subprocess.run(["pg_ctl", "-D", self.data, "-m", "immediate", "-w", "stop"],  # noqa: S607
                       capture_output=True, env=self.env)
        shutil.rmtree(self.dir, ignore_errors=True)


# ---- check, get, probe --------------------------------------------------------------------------


def check(bucket, sealer, tools, deep=False, every=False, scratch_factory=None, out=sys.stdout):
    """Everything in the bucket authentic and chained per hospital, every name written once;
    objects present with their sizes (the versions the manifests recorded). deep: the newest
    dump of each hospital (every: all) read back, decrypted and read whole by pg_restore, and
    restored schema-only when scratch_factory is given. Returns (problems, hospitals that
    failed)."""
    held = Holdings(bucket, sealer)
    problems = len(held.bad) + len(held.problems)
    failed = set()
    for key in held.bad:
        print(f"BAD   {key}: not authentic (not ours, changed, or not a manifest / erasure record)", file=out)
    for p in held.problems:
        print(f"BAD   chain: {p}", file=out)
    for key in held.early_erasures:
        print(f"WARN  {key}: an erasure inside the retention (--allow-short: a proof on the local stand-in only)", file=out)
    deep_list = []
    for hospital, order in sorted(held.order.items()):
        newest = held.newest(hospital)
        for sha in order:
            m = held.by_hospital[hospital][sha]
            try:
                for part in ("dump", "public"):
                    if bucket.size(m[part]["key"], m[part].get("version")) != m[part]["bytes"]:
                        raise RuntimeError(f"{m[part]['key']}: its size differs from its manifest")
                if deep and (every or m is newest):
                    deep_list.append(m)
                print(f"ok    {hospital} {m['kind']} {m['date']} r{m['revision']}: {m['dump']['bytes']} bytes", file=out)
            except Exception as exc:  # noqa: BLE001
                problems += 1
                failed.add(hospital)
                print(f"BAD   {hospital} {m['kind']} {m['date']} r{m['revision']}: {exc}", file=out)
    if deep_list:
        ctx = scratch_factory() if scratch_factory else _Null()
        with ctx as scratch:
            for m in deep_list:
                try:
                    verify_dump(bucket, sealer, tools, m, scratch if scratch_factory else None, full=True)
                    print(f"ok    {m['hospital']} {m['date']} r{m['revision']}: reads back, decrypts, reads whole, lists"
                          + (" and restores schema-only" if scratch_factory else ""), file=out)
                except Exception as exc:  # noqa: BLE001
                    problems += 1
                    failed.add(m["hospital"])
                    print(f"BAD   {m['hospital']} {m['date']} r{m['revision']}: {exc}", file=out)
    state = bucket_state(bucket)
    print(f"{'ok   ' if state == 1 else 'WARN '} bucket lock: "
          + {1: f"Object Lock, at least {LOCK_DAYS} days, versioning on",
             0: f"NOT locked for {LOCK_DAYS} days with versioning",
             -1: "not readable with this key (check it in the provider's console)"}[state], file=out)
    print(f"{len(held.manifests)} dump(s) of {len(held.order)} hospital(s), {problems} problem(s)", file=out)
    return problems, failed


class _Null:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def get(bucket, sealer, hospital, when, part, out):
    held = Holdings(bucket, sealer)
    ms = sorted((m for m in (held.by_hospital.get(hospital) or {}).values()
                 if when == "latest" or m["date"] == when), key=lambda m: m["created"])
    if not ms:
        sys.exit(f"dumper: no dump of {hospital}" + ("" if when == "latest" else f" on {when}"))
    m = ms[-1]
    print(f"dumper: {m['kind']} {m['date']} r{m['revision']} ({m['schema']})", file=sys.stderr)
    # A copy checked whole before any plaintext is written out (a truncated or reordered object
    # shows only in the whole SHA-256); on disk, never in memory (review C8)
    need_room(m[part]["bytes"], m[part]["key"])
    with tempfile.TemporaryFile() as sealed:
        fetch_checked(bucket, m[part], sealed)
        for chunk in opened(sealer, sealed, m[part]):
            out.write(chunk)


def manifest(bucket, sealer, hospital, when, out):
    """The manifest of the dump `get` would return (P1-T22b: the restore's pre-checks read it):
    authentic (its MAC) and chained, as every reader takes it, or exit 1. Ids, hashes, sizes,
    fingerprints and sequence values only: no personal data."""
    held = Holdings(bucket, sealer)
    ms = sorted((m for m in (held.by_hospital.get(hospital) or {}).values()
                 if when == "latest" or m["date"] == when), key=lambda m: m["created"])
    if not ms:
        sys.exit(f"dumper: no dump of {hospital}" + ("" if when == "latest" else f" on {when}"))
    if any(hospital in text for text in [*held.problems, *held.bad]):
        sys.exit(f"dumper: {hospital}'s dumps have problems (dumper.py check says which): none is used")
    out.write(json.dumps({"authentic": True, "manifest": ms[-1]}, sort_keys=True) + "\n")


def probe(bucket, out=sys.stdout):
    """What this key may do in the dumps bucket, and its lock (run at go-live). A small probe
    object stays under dumps/ihr-key-check/ (locked like the rest; dump-expire erases it)."""
    s3, b = bucket.s3, bucket.name
    fails = 0
    stamp = now_ist().strftime("%Y-%m-%dT%H%M%S")
    key = f"{PROBE_PREFIX}{stamp}.probe"
    body = b"probe"

    def outcome(fn):
        try:
            fn()
            return "ok"
        except Exception as exc:  # noqa: BLE001
            return locked_bucket.error_code(exc) or type(exc).__name__

    def expect(name, want, got):
        nonlocal fails
        good = got == "ok" if want == "ok" else got != "ok"
        fails += not good
        print(f"{'PASS' if good else 'FAIL'}  {name} ({got})", file=out)

    state = bucket_state(bucket)
    expect(f"the bucket is locked for at least {LOCK_DAYS} days, with versioning", "ok",
           {1: "ok", 0: "not locked", -1: "ok"}[state])
    if state == -1:
        print("      (this key cannot read the lock settings: check them in the provider's console)", file=out)
    import base64

    md5 = base64.b64encode(hashlib.md5(body).digest()).decode()  # noqa: S324
    expect("add a file under dumps/", "ok", outcome(lambda: s3.put_object(Bucket=b, Key=key, Body=body, ContentMD5=md5, **bucket._once())))
    expect("read it back", "ok", outcome(lambda: s3.get_object(Bucket=b, Key=key)))
    expect("list the versions under dumps/ (how a replaced dump shows)", "ok",
           outcome(lambda: list(bucket.versions(PROBE_PREFIX))))
    expect("write the same name again (If-None-Match)", "denied", outcome(lambda: s3.put_object(Bucket=b, Key=key, Body=b"x", ContentMD5=base64.b64encode(hashlib.md5(b"x").digest()).decode(), IfNoneMatch="*")))  # noqa: S324
    # Without the header (what someone else holding this key could send, review S1): refused by
    # the store where it enforces conditional writes (AWS: infra/aws's policy); elsewhere it
    # becomes a second version, which dumps-check and DumpNotAuthentic report for any dump name
    got = outcome(lambda: s3.put_object(Bucket=b, Key=key, Body=b"y", ContentMD5=base64.b64encode(hashlib.md5(b"y").digest()).decode()))  # noqa: S324
    if got != "ok":
        expect("replace it without If-None-Match: refused by the store", "denied", got)
    else:
        try:
            n = sum(1 for v in bucket.versions(key) if v["key"] == key and not v["marker"])
        except Exception:  # noqa: BLE001
            n = 0
        expect("replace it without If-None-Match: the store took it as a second version, which "
               f"dumps-check reports ({n} versions)", "ok", "ok" if n >= 2 else f"{n} version(s) listed")
    vid = None
    try:
        vid = s3.head_object(Bucket=b, Key=key).get("VersionId")
    except Exception:  # noqa: BLE001, S110
        pass
    soon = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30)
    if vid:
        expect("delete that version", "denied", outcome(lambda: s3.delete_object(Bucket=b, Key=key, VersionId=vid)))
        expect("delete it with the lock bypass", "denied", outcome(lambda: s3.delete_object(Bucket=b, Key=key, VersionId=vid, BypassGovernanceRetention=True)))
        expect("shorten its lock", "denied", outcome(lambda: s3.put_object_retention(Bucket=b, Key=key, VersionId=vid, Retention={"Mode": "GOVERNANCE", "RetainUntilDate": soon})))
    else:
        print("FAIL  the file has no version id (is versioning on?)", file=out)
        fails += 1
    expect("hide it with a delete marker", "denied", outcome(lambda: s3.delete_object(Bucket=b, Key=key)))
    expect("change the bucket's lock", "denied", outcome(lambda: s3.put_object_lock_configuration(
        Bucket=b, ObjectLockConfiguration={"ObjectLockEnabled": "Enabled", "Rule": {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 1}}})))
    expect("write outside dumps/ (erasure records)", "denied", outcome(lambda: s3.put_object(Bucket=b, Key=f"erasure/probe-{stamp}.json", Body=b"{}", ContentMD5=base64.b64encode(hashlib.md5(b"{}").digest()).decode())))  # noqa: S324
    expect("write at the bucket's top", "denied", outcome(lambda: s3.put_object(Bucket=b, Key=f"probe-{stamp}", Body=b"{}", ContentMD5=base64.b64encode(hashlib.md5(b"{}").digest()).decode())))  # noqa: S324
    print(f"{fails} problem(s)", file=out)
    return fails


# ---- service ------------------------------------------------------------------------------------


class Scrapers:
    """Who may read the metrics: this container (its health check) and the scrapers' addresses,
    resolved by a thread of their own every minute, never while a scrape waits (a name that does
    not resolve, such as prometheus in a proof without monitoring, takes seconds to fail)."""

    LOCAL = {"127.0.0.1", "::1"}

    def __init__(self, names, resolve=socket.getaddrinfo):
        self.names, self.resolve = names, resolve
        self.addrs = frozenset()

    def refresh(self):
        got = set()
        for name in self.names:
            try:
                got |= {str(ipaddress.ip_address(i[4][0])) for i in self.resolve(name, None)}
            except (OSError, ValueError):
                pass
        self.addrs = frozenset(got)  # one assignment: a scrape sees the old set or the new one

    def allows(self, client):
        try:
            client = str(ipaddress.ip_address(client))
        except ValueError:
            return False
        return client in self.LOCAL or client in self.addrs

    def keep_fresh(self, every=60):
        def loop():
            while True:
                self.refresh()
                time.sleep(every)

        threading.Thread(target=loop, daemon=True).start()


def serve_metrics(status, port, scrapers):
    """Metrics for Prometheus only and this container."""
    allowed = Scrapers(scrapers)
    allowed.keep_fresh()

    class H(http.server.BaseHTTPRequestHandler):
        timeout = 10

        def do_GET(self):
            if not allowed.allows(self.client_address[0]):
                self.send_error(403)
                return
            if self.path != "/metrics":
                self.send_error(404)
                return
            body = status.render()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            # A scraper that went away (a health check's timeout): one escaped line, no traceback
            say(f"metrics: {type(sys.exc_info()[1]).__name__} answering a scrape")

    srv = Server(("0.0.0.0", port), H)  # noqa: S104 (internal networks only)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


class RunLock:
    """One run at a time (the service and `once` share the work volume)."""

    def __init__(self, workdir):
        self.path = os.path.join(workdir, "dumper.lock")

    def __enter__(self):
        self.f = open(self.path, "a")  # noqa: SIM115
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def read_drills(workdir):
    try:
        with open(os.path.join(workdir, "drills.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def drill_mark(workdir, hospital, outcome, status):
    if not UUID_RE.match(hospital) or outcome not in ("ok", "failed"):
        sys.exit("usage: dumper.py drill-mark <hospital uuid> <ok|failed>")
    drills = read_drills(workdir)
    drills.append({"at": int(time.time()), "hospital": hospital, "outcome": outcome})
    with open(os.path.join(workdir, "drills.json.tmp"), "w") as f:
        json.dump(drills[-100:], f)
    os.replace(os.path.join(workdir, "drills.json.tmp"), os.path.join(workdir, "drills.json"))
    ok = [d["at"] for d in drills if d.get("outcome") == "ok"]
    status.set(last_drill_timestamp_seconds=max(ok) if ok else 0)


def role_cmd(source, out=sys.stdout):
    """`dumper.py role`: 0 as modelled, 1 not, 2 could not connect to check (review C7: a full
    connection limit is not a wrong role)."""
    try:
        problems = source.role_problems()
    except CannotConnect as exc:
        print(f"cannot connect as ihr_dump to check its role: {one_line(exc)}", file=out)
        return 2
    for p in problems:
        print(f"FAIL  {p}", file=out)
    print("ihr_dump as modelled" if not problems else f"{len(problems)} problem(s)", file=out)
    return 1 if problems else 0


def scheduled_due(cfg, now, last_run_date):
    return now.time() >= cfg.at and last_run_date != now.date().isoformat() and not cfg.in_quiet(now.time())


def service(cfg, source, tools, bucket, sealer, status):
    serve_metrics(status, cfg.port, cfg.scrapers)
    say(f"running (bucket {bucket.name}; nightly at {cfg.at:%H:%M} IST, {cfg.parallel} at a time, {cfg.compress})")
    drills = [d["at"] for d in read_drills(cfg.workdir) if d.get("outcome") == "ok"]
    status.set(last_drill_timestamp_seconds=max(drills) if drills else 0)
    last_file = os.path.join(cfg.workdir, "last-run")
    last_refresh, first = 0.0, True
    while True:
        try:
            now = now_ist()
            try:
                with open(last_file) as f:
                    last_run = f.read().strip()
            except OSError:
                last_run = ""
            if scheduled_due(cfg, now, last_run):
                with RunLock(cfg.workdir):
                    run(cfg, source, tools, bucket, sealer, status, scratch_factory=lambda: Scratch(cfg.workdir))
                with open(last_file, "w") as f:
                    f.write(now.date().isoformat())
                last_refresh = time.time()
            elif first and not cfg.in_quiet(now.time()):
                # A fresh service (a new server, a restart after an outage): every hospital
                # without a good dump in 26 hours is dumped now, not at the next night (a run
                # with nothing to do only reads the bucket and reports)
                with RunLock(cfg.workdir):
                    run(cfg, source, tools, bucket, sealer, status, catch_up=True,
                        scratch_factory=lambda: Scratch(cfg.workdir))
                last_refresh = time.time()
            elif time.time() - last_refresh >= cfg.refresh:
                refresh(bucket, sealer, status, source.hospitals(), parallel=cfg.parallel)
                status.set(work_free_bytes=Reserve(cfg.workdir).free())
                last_refresh = time.time()
            first = False
        except Exception as exc:  # noqa: BLE001 (the service goes on; the metrics say it failed)
            say(f"pass failed: {type(exc).__name__}: {exc}")
            first = False
            last_refresh = time.time()
        time.sleep(30)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "run"
    if cmd not in ("run", "once", "check", "get", "manifest", "probe", "role", "drill-mark"):
        sys.exit(__doc__)
    cfg = Config()
    status = Status(os.path.join(cfg.workdir, "status.json") if os.path.isdir(cfg.workdir) else None,
                    fresh=cmd == "run")
    if cmd == "drill-mark":
        drill_mark(cfg.workdir, argv[2] if len(argv) > 2 else "", argv[3] if len(argv) > 3 else "", status)
        return
    sealer = Sealer(env("IHR_DUMP_KEY"), PURPOSE)
    bucket = locked_bucket.Bucket.from_env("IHR_DUMP", "dumper", key_name="ACCESS_KEY")
    url = os.environ.get("IHR_DUMP_DATABASE_URL")
    tools = Tools(pg_env(url) if url else None)
    if cmd == "probe":
        sys.exit(1 if probe(bucket) else 0)
    if cmd == "get":
        if len(argv) != 5 or not UUID_RE.match(argv[2]) or argv[4] not in ("dump", "public"):
            sys.exit("usage: dumper.py get <hospital uuid> <YYYY-MM-DD|latest> <dump|public>")
        get(bucket, sealer, argv[2], argv[3], argv[4], sys.stdout.buffer)
        return
    if cmd == "manifest":
        if len(argv) != 4 or not UUID_RE.match(argv[2]):
            sys.exit("usage: dumper.py manifest <hospital uuid> <YYYY-MM-DD|latest>")
        manifest(bucket, sealer, argv[2], argv[3], sys.stdout)
        return
    if cmd == "check":
        n, _ = check(bucket, sealer, tools, deep="--deep" in argv, every="--all" in argv,
                     scratch_factory=(lambda: Scratch(cfg.workdir)) if "--deep" in argv and shutil.which("initdb") else None)
        status.set(check_problems=n, last_check_timestamp_seconds=int(time.time()))
        sys.exit(1 if n else 0)
    source = Source(env("IHR_DUMP_DATABASE_URL"))
    if cmd == "role":
        sys.exit(role_cmd(source))
    if cmd == "once":
        only = [argv[i + 1] for i, a in enumerate(argv) if a == "--slug" and i + 1 < len(argv)]
        with RunLock(cfg.workdir):
            results = run(cfg, source, tools, bucket, sealer, status, only=only or None,
                          scratch_factory=lambda: Scratch(cfg.workdir))
        sys.exit(1 if results is None or any(v not in ("ok", "held") for v in results.values()) else 0)
    service(cfg, source, tools, bucket, sealer, status)


if __name__ == "__main__":
    try:
        main(sys.argv)
    except KeyboardInterrupt:
        # The Postgres base image stops containers with SIGINT: a stop, not an error (a dump in
        # progress ends without its manifest and is redone by the next run)
        say("stopped")
        sys.exit(130)
