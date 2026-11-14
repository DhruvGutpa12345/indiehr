"""The quarantine of a hospital's dump (P1-T22b; docs/reviews/p1-t22/design.md, "After the
attack" 8). A dump is untrusted SQL: it never reaches the live cluster as it is.

Run by scripts/staging.sh hospital-restore in a one-off container of the dumper image (the same
Postgres 18.6 as the database), with no network (``--network none``), a read-only root and the
dump mounted read-only:

    python3 -I /opt/ihr-restore/quarantine.py --dump /in/dump --plan /in/plan.json \
        [--manifest /in/manifest.json | --pitr '<time>'] --out /out/package

1. A throwaway Postgres in this container (its own data folder, a Unix socket, no TCP), with only
   the extensions the live database has (``plan.json``: ihr_hospital_restore --plan).
2. The dump's table of contents read first (the dumper's own check: only that schema's objects);
   then restored by ``pg_restore --single-transaction --exit-on-error --no-owner --no-acl`` as
   ``ihr_quarantine``, a role that owns only an empty schema of the hospital's name and may
   create nothing else: anything the dump tries outside it fails the restore.
3. What came out is checked (ihr_platform/restore/package.py): only that schema; no function or
   trigger but ours (by name and wiring; their bodies are never kept, and their definition
   hashes are compared with this build's by the restore); no extension, role, grant, rule,
   policy, event trigger, publication or large object; no expression that calls a function
   outside pg_catalog. With the nightly dump's manifest (dumper.py manifest: authentic and
   chained), its audit head, sequences and migrations must be what was restored.
4. Only then is the package written, from this server's catalogue: tables, sequences, keys,
   indexes and rows. That package, never the dump, is what the live cluster loads.
5. The server is stopped, then the package's files are checked against what this process wrote
   (``package.recheck``: nothing the dump ran can have changed them unseen), and the SHA-256 of
   package.json, from this process's memory, is printed as the last line:
   ``quarantine: package sha256 <hex>``. scripts/staging.sh passes it to the restore
   (``--package-sha256``), which refuses a package.json that does not hash to it.

Exit 0 with the package; 1 with the problems (nothing written). No row value is ever printed.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _path in ("/opt/ihr-restore", HERE):
    if os.path.isfile(os.path.join(_path, "package.py")):
        sys.path.insert(0, _path)
        break
else:  # the repository: the package module lives with the restore engine
    sys.path.insert(0, os.path.normpath(os.path.join(
        HERE, "..", "..", "plugins", "ihr_platform", "ihr_platform", "restore")))
for _path in ("/opt/ihr-dumps", os.path.normpath(os.path.join(HERE, "..", "dumps"))):
    if os.path.isfile(os.path.join(_path, "dumper.py")):
        sys.path.insert(0, _path)
        break

import package  # noqa: E402

SCHEMA_RE = re.compile(r"^h_[a-z0-9]{10}$")
EXT_RE = re.compile(r"^[a-z0-9_]{1,63}$")
ROLE = "ihr_quarantine"


def say(text):
    print(f"quarantine: {text}", flush=True)


class Scratch:
    """A Postgres server for this run only: no TCP, a Unix socket in its own folder."""

    def __init__(self, workdir):
        self.dir = tempfile.mkdtemp(prefix="quarantine-", dir=workdir)
        self.data = os.path.join(self.dir, "data")
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("IHR_", "PG"))},
                    "PGHOST": self.dir, "PGUSER": "quarantine", "PGDATABASE": "postgres"}

    def __enter__(self):
        subprocess.run(["initdb", "-D", self.data, "-U", "quarantine", "--auth=trust", "-E", "UTF8",  # noqa: S607
                        "--no-sync", "--no-instructions"], check=True, capture_output=True, env=self.env)
        opts = (f"-c listen_addresses='' -c unix_socket_directories={self.dir} -c fsync=off "
                "-c shared_buffers=32MB -c max_locks_per_transaction=1024 -c full_page_writes=off "
                "-c log_min_messages=fatal")
        subprocess.run(["pg_ctl", "-D", self.data, "-o", opts, "-w", "-l", os.path.join(self.dir, "log"),  # noqa: S607
                        "start"], check=True, capture_output=True, env=self.env)
        return self

    def psql(self, text, db="postgres", user="quarantine"):
        r = subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-qAt", "-U", user, "-d", db, "-c", text],  # noqa: S607
                           check=True, capture_output=True, env=self.env)
        return r.stdout.decode().strip()

    def url(self, db, user="quarantine"):
        return f"postgresql://{user}@/{db}?host={self.dir}"

    def __exit__(self, *a):
        subprocess.run(["pg_ctl", "-D", self.data, "-m", "immediate", "-w", "stop"],  # noqa: S607
                       capture_output=True, env=self.env)
        shutil.rmtree(self.dir, ignore_errors=True)


def toc_lines(dump, env):
    r = subprocess.run(["pg_restore", "--list", dump], capture_output=True, env=env)  # noqa: S607
    if r.returncode:
        raise RuntimeError("pg_restore --list failed: the dump is not a readable archive")
    return r.stdout.decode(errors="replace").splitlines()


def manifest_problems(manifest, schema, facts, seqs):
    """The nightly dump's manifest (authentic, from dumper.py manifest) against what was
    restored here."""
    problems = []
    m = manifest.get("manifest", manifest)
    if not manifest.get("authentic", False):
        problems.append("the manifest was not read as authentic (dumper.py manifest)")
    if m.get("schema") != schema:
        problems.append("the manifest is of another schema")
    f = m.get("facts") or m
    audit = f.get("audit") or {}
    if int(audit.get("seal_seq") or 0) != int(facts["audit"]["seal_seq"] or 0) or (
        (audit.get("seal_hash") or "") != (facts["audit"]["seal_hash"] or "")
    ):
        problems.append("the audit head is not the manifest's")
    # pg_sequences.last_value is NULL for a sequence never used: only used ones compare
    want = {k: int(v) for k, v in (f.get("sequences") or {}).items() if v is not None}
    have = {k: int(v[0]) for k, v in seqs.items() if v[1]}
    if want != have:
        problems.append("the sequence values are not the manifest's")
    # The dumper's text ordering (ORDER BY on the server: byte order on the Alpine images)
    rows = "\n".join(sorted(f"{a}.{n}" for a, n in facts["migrations"]))
    if f.get("migrations_sha256") and hashlib.sha256(rows.encode()).hexdigest() != f["migrations_sha256"]:
        problems.append("the migrations are not the manifest's")
    return problems


def main(argv=None):  # noqa: PLR0912, PLR0915 (one flat run)
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--dump", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--manifest")
    p.add_argument("--pitr", help="the time a --from-pitr dump was taken at (ISO 8601 with offset)")
    p.add_argument("--out", required=True)
    p.add_argument("--work", default=os.environ.get("TMPDIR", "/tmp"))  # noqa: S108
    a = p.parse_args(argv)
    plan = json.loads(open(a.plan).read())
    schema = plan.get("schema", "")
    if not SCHEMA_RE.match(schema):
        sys.exit("quarantine: the plan's schema is not a hospital schema name")
    extensions = [e for e in plan.get("extensions", []) if EXT_RE.match(e)]
    manifest = json.loads(open(a.manifest).read()) if a.manifest else None
    if bool(manifest) == bool(a.pitr):
        sys.exit("quarantine: --manifest (a nightly dump) or --pitr (a copy's), one of them")
    began = time.monotonic()
    problems, report = [], {"schema": schema}
    import dumper  # the TOC check of the nightly run (deploy/dumps/dumper.py)

    with Scratch(a.work) as s:
        lines = toc_lines(a.dump, s.env)
        problems += [f"table of contents: {x}" for x in dumper.toc_problems(lines, schema)]
        report["toc_entries"] = sum(1 for x in lines if x.strip() and not x.startswith(";"))
        if problems:
            return finish(problems, report)
        s.psql("CREATE DATABASE q")
        for ext in extensions:
            s.psql(f'CREATE EXTENSION IF NOT EXISTS "{ext}" SCHEMA public', "q")
        s.psql("REVOKE ALL ON DATABASE q FROM PUBLIC", "q")
        s.psql("REVOKE CREATE ON SCHEMA public FROM PUBLIC", "q")
        s.psql(f"CREATE ROLE {ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT", "q")
        s.psql(f"GRANT CONNECT ON DATABASE q TO {ROLE}", "q")
        s.psql(f'CREATE SCHEMA "{schema}" AUTHORIZATION {ROLE}', "q")
        # Every entry but the schema itself (it exists, empty, the role's), as the role
        listing = os.path.join(s.dir, "toc.list")
        with open(listing, "w") as f:
            for line in lines:
                entry = dumper.toc_entry(line) if line.strip() and not line.startswith(";") else None
                if entry and entry[0] == "SCHEMA":
                    continue
                f.write(line + "\n")
        r = subprocess.run(  # noqa: S603
            ["pg_restore", "-L", listing, "--no-owner", "--no-acl", "--exit-on-error",  # noqa: S607
             "--single-transaction", "-U", ROLE, "-d", "q", a.dump],
            capture_output=True, env=s.env,
        )
        if r.returncode:
            # The last line names the object (never the rows): pg_restore's own message
            tail = r.stderr.decode(errors="replace").strip().splitlines()[-3:]
            problems.append("the dump does not restore as a role owning only its schema: "
                            + " / ".join(t[:200] for t in tail))
            return finish(problems, report)
        report["restored_seconds"] = round(time.monotonic() - began, 1)
        import psycopg

        with psycopg.connect(s.url("q"), autocommit=True) as conn:
            cur = conn.cursor()
            problems += package.database_problems(cur, schema, extensions, roles=(ROLE, "quarantine"))
            problems += package.schema_problems(cur, schema)
            facts = package.facts(cur, schema)
            seqs = package.sequences(cur, schema)
            if manifest is not None:
                problems += manifest_problems(manifest, schema, facts, seqs)
            if problems:
                return finish(problems, report)
            if manifest is not None:
                m = manifest.get("manifest", manifest)
                snap = (m.get("facts") or {}).get("snapshot") or m.get("snapshot") or {}
                source = {"kind": "nightly", "dump_at": snap.get("at"), "dump": f"{m.get('date')} r{m.get('revision')}",
                          "kind_of_dump": m.get("kind"),
                          "manifest_sha256": hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()}
            else:
                source = {"kind": "pitr", "dump_at": a.pitr, "dump": f"pitr {a.pitr}"}
            report.update(quarantine="passed", problems=[],
                          checks="schema only; functions and triggers ours by name (bodies not kept); "
                                 "no extension, role, grant, rule, policy or outside call; "
                                 "expressions call only pure built-ins",
                          seconds=round(time.monotonic() - began, 1))
            digests = {}
            doc = package.export(conn, schema, a.out, source=source, report=report, digests=digests)
    # The server is stopped now: whatever ran in it can no longer change the package unseen
    problems = package.recheck(a.out, digests)
    if problems:
        shutil.rmtree(a.out, ignore_errors=True)
        return finish(problems, report)
    # The restore reads the package as another user (the app image's): readable, never writable
    for root, dirs, names in os.walk(a.out):
        for name in dirs:
            os.chmod(os.path.join(root, name), 0o755)  # noqa: S103 (read-only for the reader)
        for name in names:
            os.chmod(os.path.join(root, name), 0o644)
    os.chmod(a.out, 0o755)  # noqa: S103
    os.chmod(os.path.dirname(os.path.abspath(a.out)), 0o755)  # noqa: S103 (its volume's root)
    say(f"passed: {len(doc['tables'])} tables, {sum(t['rows'] for t in doc['tables'])} rows, "
        f"audit seal {doc['facts']['audit']['seal_seq']}, in {round(time.monotonic() - began, 1)} s")
    say(f"package sha256 {digests['package.json']}")
    return 0


def finish(problems, report):
    for problem in problems[:50]:
        say(f"REFUSED {problem}")
    say(f"{len(problems)} problem(s): no package written")
    return 1


if __name__ == "__main__":
    sys.exit(main())
