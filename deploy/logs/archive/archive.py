"""Daily locked archive of the redacted logs (P1-T9c, ADR-014; owner decisions Q-O3, Q-O10..Q-O12).

Each India day (00:00-24:00 IST) that VictoriaLogs holds is exported once it is over (plus a
grace period for late lines), hour by hour with each hour's count checked before and after,
compressed, encrypted with our own key, and uploaded into a bucket with Object Lock (400 days).
Beside it goes a small manifest: day, lines per hour, size, SHA-256 of the object, and the
SHA-256 of the manifest before it (a chain), authenticated with a key derived from the
encryption key. After each upload the object is read back and checked.

Every day still in the store is recounted on every pass: a day that gained lines (Fluent Bit
delivering a backlog late) is archived again as the next revision; the earlier one stays. A
genuine later revision is a re-export of the same day, so it holds every line of the one before:
`check --deep` proves that hour by hour, so a revision that drops lines is caught.

The chain is followed by its links, not by time: one root, no forks, no gaps. Days erased after
400 days (expire.py, from the owner's machine) leave an authenticated erasure record naming each
erased manifest and its predecessor, through which the chain is followed.

The writer's key can add and read, never delete or change a lock. A name already taken (an
object uploaded before a crash, without its manifest) is skipped: the next revision is used.

    archive.py run                 the service: a pass every IHR_LOGS_ARCHIVE_INTERVAL seconds, a
                                   full check every IHR_LOGS_ARCHIVE_FULL_CHECK_DAYS, metrics on :9100
    archive.py once                one pass, then exit
    archive.py check [--deep] [--recent N]
                                   manifests authentic and chained, erasure records authentic,
                                   objects present with their size; SHA-256 of the last N days'
                                   objects (default: all); --deep: every object decrypted, its
                                   lines counted, and each revision a superset of the one before
    archive.py get DAY [REV]       the day's object (latest revision), checked and decrypted, as
                                   JSON lines on stdout; the day's revisions listed on stderr
    archive.py probe               the bucket's lock settings and what this key may do (go-live)

Format of an object: lines of Fernet tokens (AES-128-CBC + HMAC-SHA256, `cryptography`), each
holding one MiB of a gzip stream of the day's JSON lines in hour order, as VictoriaLogs returns
them (deploy/lib/sealer.py, shared with the nightly dumps). Configuration: environment (see
deploy/staging/compose.logs-archive.yaml).
"""

import base64
import collections
import datetime as dt
import fcntl
import gzip
import hashlib
import http.server
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


def _shared_lib():
    """The shared modules: /opt/ihr-lib in a container (a read-only mount), else deploy/lib beside
    this folder (tests, the owner's machine). Never the working directory (python3 -I)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in ("/opt/ihr-lib", os.path.normpath(os.path.join(here, "..", "..", "lib"))):
        if os.path.isfile(os.path.join(path, "sealer.py")):
            return path
    sys.exit("the shared sealer (deploy/lib/sealer.py) is missing")


sys.path.insert(0, _shared_lib())
import chain  # noqa: E402
import locked_bucket  # noqa: E402
from locked_bucket import taken_error  # noqa: E402, F401 (used below and by the tests)
from sealer import CHUNK, HEX64, Sealer, erasure_mac_key, mac_ok, record_bytes  # noqa: E402, F401

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
FORMAT = "ihr-log-archive/1"
ERASURE_FORMAT = "ihr-log-archive-erasure/2"
PREFIX = "logs/"
KEY_RE = re.compile(r"^logs/(\d{4})/(\d{2})/(\d{4}-\d{2}-\d{2})\.r([1-9]\d{0,3})\.(jsonl\.gz\.enc|manifest\.json)$")
MIN_LOCK_DAYS = 400


def env(name, default=None):
    v = os.environ.get(name, default)
    if v is None:
        sys.exit(f"archive: {name} is not set")
    return v


# ---- time -------------------------------------------------------------------------------------


def day_bounds(day):
    """[start, end) of an India day, as aware datetimes."""
    start = dt.datetime.combine(day, dt.time(0, 0), IST)
    return start, start + dt.timedelta(days=1)


def iso(t):
    return t.isoformat()


def today_ist(now=None):
    return (now or dt.datetime.now(IST)).astimezone(IST).date()


# ---- VictoriaLogs -----------------------------------------------------------------------------


class Store:
    """The log store, read with its password (basic auth, user ihr-logs)."""

    def __init__(self, url, password, timeout=60):
        self.url = url.rstrip("/")
        token = base64.b64encode(("ihr-logs:" + password).encode()).decode()
        self.headers = {"Authorization": "Basic " + token}
        self.timeout = timeout

    def _open(self, path, params):
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(
            self.url + path, data=data,
            headers={**self.headers, "Content-Type": "application/x-www-form-urlencoded"})
        return urllib.request.urlopen(req, timeout=self.timeout)

    def count(self, start, end):
        with self._open("/select/logsql/query", {
                "query": "* | stats count() rows", "start": iso(start), "end": iso(end)}) as r:
            body = r.read().decode().strip()
        return int(json.loads(body)["rows"]) if body else 0

    def export(self, start, end, sink):
        """Every line in [start, end) into sink.write(bytes); returns the number of lines.
        The store ends a slow query early without an error: the caller compares counts."""
        n = 0
        with self._open("/select/logsql/query", {"query": "*", "start": iso(start), "end": iso(end)}) as r:
            for line in r:
                if line.strip():
                    sink.write(line if line.endswith(b"\n") else line + b"\n")
                    n += 1
        return n

    def day_counts(self, first, last):
        """Lines per India day, first..last inclusive: {date: count}."""
        start, _ = day_bounds(first)
        _, end = day_bounds(last)
        with self._open("/select/logsql/hits", {
                "query": "*", "start": iso(start), "end": iso(end), "step": "1d", "offset": "5h30m"}) as r:
            hits = json.load(r).get("hits") or []
        out = {}
        for series in hits:
            for ts, n in zip(series.get("timestamps", []), series.get("values", [])):
                t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(IST)
                out[t.date()] = out.get(t.date(), 0) + int(n)
        return out


# ---- encryption and authentication: the shared sealer (deploy/lib/sealer.py) ------------------


def manifest_bytes(manifest):
    return record_bytes(manifest)


def object_key(day, rev):
    return f"{PREFIX}{day:%Y/%m}/{day.isoformat()}.r{rev}.jsonl.gz.enc"


def manifest_key(day, rev):
    return f"{PREFIX}{day:%Y/%m}/{day.isoformat()}.r{rev}.manifest.json"


def parse_key(key):
    """(day, revision, 'object'|'manifest') of an archive key, or None."""
    m = KEY_RE.match(key)
    if not m:
        return None
    try:
        day = dt.date.fromisoformat(m.group(3))
    except ValueError:
        return None
    if (f"{day:%Y}", f"{day:%m}") != (m.group(1), m.group(2)):
        return None
    return day, int(m.group(4)), "manifest" if m.group(5) == "manifest.json" else "object"


def parse_manifest_key(key):
    p = parse_key(key)
    return (p[0], p[1]) if p and p[2] == "manifest" else None


def valid_manifest(m, key, sealer):
    """True when m is an authentic manifest stored under its own name."""
    if not isinstance(m, dict) or m.get("format") != FORMAT or not mac_ok(sealer.mac_key, m):
        return False
    try:
        day = dt.date.fromisoformat(m["day"])
        hours = m["hours"]
        ok = (isinstance(m["revision"], int) and key == manifest_key(day, m["revision"])
              and m["object"] == object_key(day, m["revision"])
              and isinstance(hours, list) and len(hours) == 24 and all(isinstance(h, int) and h >= 0 for h in hours)
              and m["lines"] == sum(hours) and isinstance(m["bytes"], int)
              and isinstance(m["sha256"], str) and HEX64.match(m["sha256"])
              and (m["previous"] is None or (isinstance(m["previous"], str) and HEX64.match(m["previous"])))
              and isinstance(m["created"], str))
    except (KeyError, TypeError, ValueError):
        return False
    return bool(ok)


# ---- the bucket: the shared one (deploy/lib/locked_bucket.py) ------------------------------------


class Bucket(locked_bucket.Bucket):
    @classmethod
    def from_env(cls):
        return super().from_env("IHR_LOGS_ARCHIVE", "archive")


# ---- what the bucket holds ------------------------------------------------------------------


class Holdings:
    """Every manifest and erasure record in the bucket, validated, and the chain they form."""

    def __init__(self, bucket, sealer):
        self.manifests = []  # (day, rev, manifest, sha256 of its bytes)
        self.bad = []  # keys that are not authentic (manifests or erasure records)
        self.problems = []  # chain problems (text)
        self.taken = collections.defaultdict(int)  # day -> highest revision of any key
        self.erased = {}  # sha256 of an erased manifest -> its previous
        self.early_erasures = []  # records of erasures inside 400 days (proofs)
        keys = list(bucket.keys(PREFIX))
        for key in keys:
            p = parse_key(key)
            if p:
                self.taken[p[0]] = max(self.taken[p[0]], p[1])
            if not p or p[2] != "manifest":
                continue
            try:
                raw = bucket.get(key).read()
                m = json.loads(raw)
                ok = valid_manifest(m, key, sealer)
            except (ValueError, TypeError, UnicodeDecodeError):
                ok = False
            if not ok:
                self.bad.append(key)
                continue
            self.manifests.append((p[0], p[1], m, hashlib.sha256(raw).hexdigest()))
        for key in bucket.keys("erasure/"):
            try:
                rec = json.loads(bucket.get(key).read())
                ok = (isinstance(rec, dict) and rec.get("format") == ERASURE_FORMAT
                      and mac_ok(sealer.erasure_key, rec) and isinstance(rec.get("erased"), list))
                entries = rec["erased"] if ok else []
                for e in entries:
                    if not (isinstance(e, dict) and isinstance(e.get("sha256"), str) and HEX64.match(e["sha256"])):
                        ok = False
            except (ValueError, TypeError, UnicodeDecodeError, KeyError):
                ok = False
            if not ok:
                self.bad.append(key)
                continue
            for e in entries:
                prev = e.get("previous")
                self.erased[e["sha256"]] = prev if isinstance(prev, str) and HEX64.match(prev) else None
            if not rec.get("older_than_days") or rec["older_than_days"] < MIN_LOCK_DAYS:
                self.early_erasures.append(key)
        self._chain()

    def _chain(self):
        present = {sha: (d, r, m) for d, r, m, sha in self.manifests}

        def label(sha):
            d, r, _ = present[sha]
            return f"{d} r{r}"

        self.order, self.head, problems = chain.follow(
            {sha: m for sha, (_, _, m) in present.items()}, self.erased,
            lambda sha: present[sha][2]["created"], label)
        self.problems.extend(problems)
        self.present = present

    def latest(self):
        """{day: (rev, manifest)} of the newest revision of each day."""
        out = {}
        for d, r, m, _ in self.manifests:
            if d not in out or r > out[d][0]:
                out[d] = (r, m)
        return out


# ---- one day ----------------------------------------------------------------------------------


def export_day(store, day, gz, tries=3):
    """The day's lines into the gzip writer, hour by hour; [count per hour]. An hour whose
    export does not match its count before and after is tried again, then refused."""
    hours = []
    start, _ = day_bounds(day)
    for h in range(24):
        a, b = start + dt.timedelta(hours=h), start + dt.timedelta(hours=h + 1)
        for attempt in range(tries):
            before = store.count(a, b)
            buf = tempfile.SpooledTemporaryFile(max_size=8 * CHUNK)
            got = store.export(a, b, buf)
            after = store.count(a, b)
            if got == before == after:
                buf.seek(0)
                for block in iter(lambda: buf.read(CHUNK), b""):
                    gz.write(block)
                buf.close()
                hours.append(got)
                break
            buf.close()
            time.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError(f"{day} hour {h:02d}: export and count disagree after {tries} tries "
                               f"(count {before}/{after}, exported {got})")
    return hours


def archive_day(store, bucket, sealer, day, rev, previous_sha, workdir, now=None, tries=5):
    """Exports, seals, uploads and checks one revision of a day; returns (manifest, raw). A name
    already taken (a file left by an interrupted upload) moves on to the next revision."""
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        plain = os.path.join(tmp, "day.jsonl.gz")
        sealed = os.path.join(tmp, "day.enc")
        with open(plain, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            hours = export_day(store, day, gz)
        with open(plain, "rb") as src, open(sealed, "wb") as dst:
            sealer.encrypt_file(src, dst)
        digest = hashlib.sha256()
        with open(sealed, "rb") as f:
            for block in iter(lambda: f.read(CHUNK), b""):
                digest.update(block)
        for _ in range(tries):
            okey, mkey = object_key(day, rev), manifest_key(day, rev)
            manifest = {
                "format": FORMAT,
                "day": day.isoformat(),
                "revision": rev,
                "timezone": "+05:30",
                "lines": sum(hours),
                "hours": hours,
                "object": okey,
                "bytes": os.path.getsize(sealed),
                "sha256": digest.hexdigest(),
                "encryption": "fernet, 1 MiB chunks of gzip",
                "previous": previous_sha,
                "created": iso((now or dt.datetime.now(IST)).astimezone(IST)),
            }
            manifest["mac"] = sealer.mac(manifest)
            raw = manifest_bytes(manifest)
            try:
                bucket.put_file(okey, sealed, "application/octet-stream")
                verify_object(bucket, sealer, manifest, deep=True)
                bucket.put_bytes(mkey, raw, "application/json")
                return manifest, raw
            except Exception as exc:  # noqa: BLE001 (only a taken name is retried)
                if not taken_error(exc):
                    raise
                print(f"archive: {okey} or its manifest exists already (an interrupted upload, or not ours): "
                      f"using revision {rev + 1}", flush=True)
                rev += 1
        raise RuntimeError(f"{day}: {tries} revision names in a row were taken")


def fetch_checked(bucket, manifest, dst):
    """The object into the open file dst, checked against its manifest's SHA-256."""
    body = bucket.get(manifest["object"])
    digest = hashlib.sha256()
    for block in iter(lambda: body.read(CHUNK), b""):
        digest.update(block)
        dst.write(block)
    if digest.hexdigest() != manifest["sha256"]:
        raise RuntimeError(f"{manifest['object']}: SHA-256 differs from its manifest")
    dst.seek(0)


def open_plain(sealer, sealed, workfile):
    """The decrypted gzip stream of a fetched object (through a temporary file: a day can be
    larger than the container's memory)."""
    for chunk in sealer.decrypt_lines(sealed):
        workfile.write(chunk)
    workfile.seek(0)
    return gzip.GzipFile(fileobj=workfile)


def verify_object(bucket, sealer, manifest, deep=False):
    """The object matches its manifest (SHA-256; deep: it decrypts and has its lines)."""
    with tempfile.TemporaryFile() as sealed, tempfile.TemporaryFile() as work:
        fetch_checked(bucket, manifest, sealed)
        if deep:
            with open_plain(sealer, sealed, work) as gz:
                lines = sum(1 for _ in gz)
            if lines != manifest["lines"]:
                raise RuntimeError(f"{manifest['object']}: {lines} lines, its manifest says {manifest['lines']}")
    return True


def superset(bucket, sealer, older, newer):
    """None when every line of the older revision is in the newer one (hour by hour: both are
    exported in hour order with their per-hour counts), else the number of lines missing."""
    missing = 0
    with tempfile.TemporaryFile() as s1, tempfile.TemporaryFile() as w1, \
            tempfile.TemporaryFile() as s2, tempfile.TemporaryFile() as w2:
        fetch_checked(bucket, older, s1)
        fetch_checked(bucket, newer, s2)
        with open_plain(sealer, s1, w1) as a, open_plain(sealer, s2, w2) as b:
            for h in range(24):
                have = collections.Counter(hashlib.sha256(next(b)).digest() for _ in range(newer["hours"][h]))
                for _ in range(older["hours"][h]):
                    k = hashlib.sha256(next(a)).digest()
                    if have[k]:
                        have[k] -= 1
                    else:
                        missing += 1
    return missing or None


# ---- a pass -----------------------------------------------------------------------------------


class Status:
    def __init__(self):
        self.lock = threading.Lock()
        self.values = {}

    def set(self, **kw):
        with self.lock:
            self.values.update(kw)

    def get(self, name):
        with self.lock:
            return self.values.get(name)

    def render(self):
        with self.lock:
            v = dict(self.values)
        lines = []
        for name, help_ in METRICS.items():
            if name in v and v[name] is not None:
                lines += [f"# HELP ihr_logs_archive_{name} {help_}", f"# TYPE ihr_logs_archive_{name} gauge",
                          f"ihr_logs_archive_{name} {v[name]}"]
        return ("\n".join(lines) + "\n").encode()


METRICS = {
    "last_run_timestamp_seconds": "When the last pass ended.",
    "last_run_ok": "1 if the last pass archived everything due without an error.",
    "last_day_end_timestamp_seconds": "End of the newest archived India day.",
    "oldest_day_timestamp_seconds": "Start of the oldest day in the bucket (erasure due after 400 days).",
    "days": "Days in the bucket.",
    "revisions_total": "Days with more than one revision (the store gained lines after archiving).",
    "bad_manifests": "Manifests or erasure records that are not authentic, and chain problems.",
    "check_problems": "Problems the last full check (every object decrypted) found.",
    "last_full_check_timestamp_seconds": "When the last full check ended.",
    "shrunk_days": "Days the store now holds fewer lines of than their archive.",
    "bucket_locked": "1 if the bucket has Object Lock with at least 400 days and versioning (-1: not readable).",
    "last_object_bytes": "Size of the newest uploaded object.",
    "last_lines": "Lines in the newest uploaded object.",
}


def lock_state(bucket):
    """1: Object Lock with at least 400 days and versioning on; 0: not; -1: this key cannot read it."""
    return locked_bucket.lock_state(bucket, MIN_LOCK_DAYS)


def run_pass(store, bucket, sealer, status, workdir, grace, recheck_days, retention_days, now=None):
    now = (now or dt.datetime.now(IST)).astimezone(IST)
    held = Holdings(bucket, sealer)
    latest = held.latest()
    head = held.head
    today = today_ist(now)
    # Days the store can still hold whole (retention drops UTC days: keep a day's margin)
    first_kept = today - dt.timedelta(days=retention_days - 2)
    recheck_from = max(first_kept, today - dt.timedelta(days=recheck_days))
    last_due = None
    for back in range(1, 3):
        d = today - dt.timedelta(days=back)
        if now >= day_bounds(d)[1] + grace:
            last_due = d
            break
    done = []
    error = None
    shrunk = 0
    if last_due and last_due >= first_kept:
        counts = store.day_counts(first_kept, last_due)
        first_archived = min(latest) if latest else None
        d = first_kept
        while d <= last_due:
            n = counts.get(d, 0)
            need = False
            if d not in latest:
                # A day with lines, or any day after archiving began (an empty day is recorded
                # too: the record shows there was nothing, not that a day is missing)
                need = n > 0 or bool(first_archived and d > first_archived)
            elif d >= recheck_from:
                have = latest[d][1]["lines"]
                need = n > have
                shrunk += n < have
            if need:
                rev = max(latest[d][0] if d in latest else 0, held.taken.get(d, 0)) + 1
                try:
                    m, raw = archive_day(store, bucket, sealer, d, rev, head, workdir, now)
                except Exception as exc:  # noqa: BLE001 (reported; the next pass tries again)
                    error = f"{d}: {type(exc).__name__}: {exc}"
                    break
                head = hashlib.sha256(raw).hexdigest()
                latest[d] = (m["revision"], m)
                held.taken[d] = m["revision"]
                first_archived = first_archived or d
                done.append((d, m["revision"]))
                status.set(last_object_bytes=m["bytes"], last_lines=m["lines"])
            d += dt.timedelta(days=1)
    revisions = sum(1 for _, (rev, _) in latest.items() if rev > 1)
    status.set(
        last_run_timestamp_seconds=int(time.time()),
        last_run_ok=0 if error else 1,
        days=len(latest),
        revisions_total=revisions,
        bad_manifests=len(held.bad) + len(held.problems),
        shrunk_days=shrunk,
        bucket_locked=lock_state(bucket),
    )
    if latest:
        status.set(last_day_end_timestamp_seconds=int(day_bounds(max(latest))[1].timestamp()),
                   oldest_day_timestamp_seconds=int(day_bounds(min(latest))[0].timestamp()))
    for d, rev in done:
        print(f"archive: {d} revision {rev} archived ({latest[d][1]['lines']} lines, {latest[d][1]['bytes']} bytes)", flush=True)
    for key in held.bad:
        print(f"archive: not authentic: {key}", flush=True)
    for p in held.problems:
        print(f"archive: chain: {p}", flush=True)
    if error:
        print(f"archive: pass stopped: {error}", flush=True)
    return done, error


# ---- check, get, probe --------------------------------------------------------------------------


def check(bucket, sealer, deep=False, recent=None, out=sys.stdout, now=None):
    """Everything in the bucket authentic, chained and intact; returns the number of problems.
    The objects of the last `recent` days are fetched and their SHA-256 checked (None: all);
    the others' presence and size. deep: every object also decrypted, its lines counted, and
    each revision a superset of the one before."""
    held = Holdings(bucket, sealer)
    problems = len(held.bad) + len(held.problems)
    for key in held.bad:
        print(f"BAD   {key}: not authentic (not ours, changed, or not a manifest / erasure record)", file=out)
    for p in held.problems:
        print(f"BAD   chain: {p}", file=out)
    for key in held.early_erasures:
        print(f"NOTE  {key}: an erasure inside {MIN_LOCK_DAYS} days (a proof's --allow-short)", file=out)
    today = today_ist(now)
    by_day = collections.defaultdict(list)
    for d, r, m, sha in held.manifests:
        by_day[d].append((r, m))
    rest = sorted((sha for sha in held.present if sha not in set(held.order)), key=lambda x: held.present[x][2]["created"])
    for sha in held.order + rest:
        d, r, m = held.present[sha]
        try:
            if deep or recent is None or d >= today - dt.timedelta(days=recent):
                verify_object(bucket, sealer, m, deep=deep)
            elif bucket.size(m["object"]) != m["bytes"]:
                raise RuntimeError(f"{m['object']}: its size differs from its manifest")
            print(f"ok    {d} r{r}: {m['lines']} lines, {m['bytes']} bytes", file=out)
        except Exception as exc:  # noqa: BLE001
            problems += 1
            print(f"BAD   {d} r{r}: {exc}", file=out)
    # A later revision re-exports the same day: never fewer lines in any hour, and (deep) every
    # line of the revision before it
    for d, revs in sorted(by_day.items()):
        revs.sort(key=lambda x: x[0])
        for (ra, a), (rb, b) in zip(revs, revs[1:]):
            if any(x > y for x, y in zip(a["hours"], b["hours"])):
                problems += 1
                print(f"BAD   {d} r{rb}: fewer lines in some hour than r{ra}", file=out)
            elif deep:
                try:
                    lost = superset(bucket, sealer, a, b)
                except Exception as exc:  # noqa: BLE001
                    lost = f"unreadable ({exc})"
                if lost:
                    problems += 1
                    print(f"BAD   {d} r{rb}: drops {lost} line(s) of r{ra}", file=out)
    state = lock_state(bucket)
    print(f"{'ok   ' if state == 1 else 'WARN '} bucket lock: "
          + {1: f"Object Lock, at least {MIN_LOCK_DAYS} days, versioning on",
             0: f"NOT locked for {MIN_LOCK_DAYS} days with versioning",
             -1: "not readable with this key (check it in the provider's console)"}[state], file=out)
    print(f"{len(held.manifests)} archived day revision(s), {problems} problem(s)", file=out)
    return problems


def get_day(bucket, sealer, day, rev=None, out=None, err=sys.stderr):
    out = out or sys.stdout.buffer
    held = Holdings(bucket, sealer)
    found = sorted((r, m) for d, r, m, _ in held.manifests if d == day and (rev is None or r == rev))
    if not found:
        sys.exit(f"no archive of {day}" + (f" revision {rev}" if rev else ""))
    every = sorted((r, m) for d, r, m, _ in held.manifests if d == day)
    if len(every) > 1:
        print(f"{day}: {len(every)} revisions (a later one is meant to hold every line of the ones before; "
              f"`check --deep` proves it):", file=err)
        for r, m in every:
            print(f"  r{r}: {m['lines']} lines, archived {m['created']}", file=err)
    _, m = found[-1]
    with tempfile.TemporaryFile() as sealed, tempfile.TemporaryFile() as work:
        fetch_checked(bucket, m, sealed)
        with open_plain(sealer, sealed, work) as gz:
            for line in gz:
                out.write(line)


def probe(bucket, out=sys.stdout):
    """What this key may do in the bucket, and its lock settings (run at go-live: E2E documents
    neither). A small probe object is left under logs/ihr-key-check/ (locked like the rest)."""
    s3, b = bucket.s3, bucket.name
    fails = 0
    stamp = dt.datetime.now(IST).strftime("%Y-%m-%dT%H%M%S")
    key = f"logs/ihr-key-check/{stamp}.probe"
    body = b"probe"

    def outcome(fn):
        try:
            fn()
            return "ok"
        except Exception as exc:  # noqa: BLE001
            return str(((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code", "")) or type(exc).__name__

    def expect(name, want, got):
        nonlocal fails
        good = got == "ok" if want == "ok" else got != "ok"
        fails += not good
        print(f"{'PASS' if good else 'FAIL'}  {name} ({got})", file=out)

    state = lock_state(bucket)
    expect("the bucket is locked for at least 400 days, with versioning", "ok",
           {1: "ok", 0: "not locked", -1: "unreadable"}[state] if state != -1 else "ok")
    if state == -1:
        print("      (this key cannot read the lock settings: check them in the provider's console)", file=out)
    md5 = base64.b64encode(hashlib.md5(body).digest()).decode()  # noqa: S324
    expect("add a file under logs/", "ok", outcome(lambda: s3.put_object(Bucket=b, Key=key, Body=body, ContentMD5=md5, **bucket._once())))
    expect("read it back", "ok", outcome(lambda: s3.get_object(Bucket=b, Key=key)))
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
    expect("write outside logs/ (erasure records)", "denied", outcome(lambda: s3.put_object(Bucket=b, Key=f"erasure/probe-{stamp}.json", Body=b"{}")))
    print(f"{fails} problem(s)", file=out)
    return fails


# ---- service ----------------------------------------------------------------------------------


def serve_metrics(status, port):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/metrics":
                self.send_error(404)
                return
            body = status.render()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # no access log
            pass

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), H)  # noqa: S104 (logs network only)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


class PassLock:
    """One pass at a time (the service and `once` share /state)."""

    def __init__(self, workdir):
        self.path = os.path.join(workdir, "archive.lock")

    def __enter__(self):
        self.f = open(self.path, "a")  # noqa: SIM115
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "run"
    sealer = Sealer(env("IHR_LOGS_ARCHIVE_ENCRYPTION_KEY"))
    bucket = Bucket.from_env()
    if cmd == "check":
        recent = None
        if "--recent" in argv:
            recent = int(argv[argv.index("--recent") + 1])
        sys.exit(1 if check(bucket, sealer, deep="--deep" in argv, recent=recent) else 0)
    if cmd == "get":
        get_day(bucket, sealer, dt.date.fromisoformat(argv[2]), int(argv[3]) if len(argv) > 3 else None)
        return
    if cmd == "probe":
        sys.exit(1 if probe(bucket) else 0)
    if cmd not in ("run", "once"):
        sys.exit(__doc__)
    with open(env("IHR_LOGS_PASSWORD_FILE", "/run/ihr-logs/http-password")) as f:
        store = Store(env("IHR_LOGS_URL", "http://victorialogs:9428"), f.read().strip())
    workdir = env("IHR_LOGS_ARCHIVE_WORKDIR", "/state")
    grace = dt.timedelta(seconds=int(env("IHR_LOGS_ARCHIVE_GRACE_SECONDS", "3600")))
    retention = int(env("IHR_LOGS_RETENTION_DAYS", "180"))
    recheck = int(env("IHR_LOGS_ARCHIVE_RECHECK_DAYS", str(retention)))
    status = Status()
    if cmd == "once":
        with PassLock(workdir):
            _, error = run_pass(store, bucket, sealer, status, workdir, grace, recheck, retention)
        sys.exit(1 if error else 0)
    serve_metrics(status, int(env("IHR_LOGS_ARCHIVE_METRICS_PORT", "9100")))
    interval = int(env("IHR_LOGS_ARCHIVE_INTERVAL", "600"))
    full_every = float(env("IHR_LOGS_ARCHIVE_FULL_CHECK_DAYS", "7")) * 86400
    print(f"archive: running (bucket {bucket.name}, a pass every {interval} s)", flush=True)
    while True:
        try:
            with PassLock(workdir):
                run_pass(store, bucket, sealer, status, workdir, grace, recheck, retention)
                last = status.get("last_full_check_timestamp_seconds") or 0
                if time.time() - last >= full_every:
                    report = open(os.devnull, "w")  # noqa: SIM115 (the metric and the log say it)
                    n = check(bucket, sealer, deep=True, out=report)
                    report.close()
                    status.set(check_problems=n, last_full_check_timestamp_seconds=int(time.time()))
                    print(f"archive: full check: {n} problem(s)", flush=True)
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            status.set(last_run_ok=0, last_run_timestamp_seconds=int(time.time()))
            print(f"archive: pass failed: {exc}", flush=True)
        except Exception as exc:  # noqa: BLE001 (the service keeps going; the metric says it failed)
            status.set(last_run_ok=0, last_run_timestamp_seconds=int(time.time()))
            print(f"archive: pass failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main(sys.argv)
