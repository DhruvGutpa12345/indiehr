"""Static checks of the object storage pieces (P1-T8b3): deploy/s3/, compose.s3.yaml, the dev
override's SeaweedFS, routes.yml's files router and staging.sh. The live checks are
`scripts/care-dev.sh s3-check` and `scripts/staging.sh s3-check` (deploy/s3/check_keys.py).
Run: python3 -m unittest scripts/test_staging_s3.py
"""

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
S3 = ROOT / "deploy" / "s3"
STAGING = ROOT / "deploy" / "staging"


class _Loader(yaml.SafeLoader):
    pass


# Compose's !override / !reset tags in the dev override
_Loader.add_multi_constructor("!", lambda loader, suffix, node: loader.construct_sequence(node)
                              if isinstance(node, yaml.SequenceNode) else loader.construct_mapping(node)
                              if isinstance(node, yaml.MappingNode) else loader.construct_scalar(node))

OVERLAY = yaml.load((STAGING / "compose.s3.yaml").read_text(), Loader=_Loader)
DEV = yaml.load((ROOT / "deploy" / "dev" / "care-local.override.yaml").read_text(), Loader=_Loader)
TEMPLATE = json.loads((S3 / "s3.template.json").read_text())
START = (S3 / "start.sh").read_text()
BASE = yaml.load((STAGING / "compose.yaml").read_text(), Loader=_Loader)
ROUTES = (STAGING / "traefik" / "routes.yml").read_text()
STAGING_SH = (ROOT / "scripts" / "staging.sh").read_text()
CARE_DEV = (ROOT / "scripts" / "care-dev.sh").read_text()
DEPLOY_SH = (ROOT / "scripts" / "deploy.sh").read_text()
MODELS = (ROOT / "plugins" / "ihr_platform" / "ihr_platform" / "models.py").read_text()
IMAGE = re.compile(r"^chrislusf/seaweedfs:[\d.]+@sha256:[0-9a-f]{64}$")


def storage_services():
    """(name, service) for the SeaweedFS container in staging and dev"""
    return [("staging s3", OVERLAY["services"]["s3"]), ("dev s3", DEV["services"]["s3"])]


def merged(svc):
    # YAML merge keys (<<) are resolved by the loader already
    return svc


class InternalApisStayPrivate(unittest.TestCase):
    """The filer, master and volume server have no access control and sit underneath S3's
    locks: they must listen on 127.0.0.1 only; the gateway in the same container is the only API."""

    def test_storage_binds_loopback_without_s3(self):
        mini = START.split("mini ", 1)[1].split("&", 1)[0]
        for flag in ("-ip=127.0.0.1", "-ip.bind=127.0.0.1", "-s3=false", "-webdav=false", "-admin.ui=false",
                     '-volume.max="$VOLUMES"', '-master.volumeSizeLimitMB="$VOLUME_MB"'):
            self.assertIn(flag, mini, flag)
        self.assertNotIn("ports", OVERLAY["services"]["s3"])  # staging: reached only by containers

    def test_both_processes_or_neither(self):
        # either process stopping stops the container (no gateway left in a dead namespace)
        self.assertIn('while kill -0 "$STORAGE" 2>/dev/null && kill -0 "$GATEWAY" 2>/dev/null', START)
        self.assertTrue(START.rstrip().endswith("exit 1"))
        for name, svc in storage_services():
            self.assertNotIn("network_mode", svc, name)
            self.assertEqual(svc["entrypoint"], ["/bin/sh", "/etc/seaweedfs/start.sh"], name)
            self.assertEqual(svc["restart"], "unless-stopped", name)
            self.assertIn("8888/healthz", " ".join(svc["healthcheck"]["test"]), name)
        self.assertNotIn("s3-gateway", OVERLAY["services"])
        self.assertNotIn("s3-gateway", DEV["services"])

    def test_gateway_flags(self):
        for flag in ("-filer=127.0.0.1:8888", "-port.iceberg=0", "-port.lance=0", "-iam=false"):
            self.assertIn(flag, START)
        self.assertIn("unset $KEYS", START)  # keys leave the environment before weed starts
        self.assertLess(START.index("unset $KEYS"), START.index("/usr/bin/weed -logtostderr mini"))
        self.assertIn("umask 077", START)

    def test_disk_is_capped(self):
        self.assertIn("IHR_S3_MAX_GB: ${IHR_S3_MAX_GB:-20}", (STAGING / "compose.s3.yaml").read_text())

    def test_files_network_is_internal(self):
        self.assertTrue(OVERLAY["networks"]["files"]["internal"])
        nets = OVERLAY["services"]["s3"]["networks"]
        # monitoring (P1-T9a): Prometheus reads the gateway's metrics port; internal, no way out
        self.assertEqual(sorted(nets), ["files", "internal", "monitoring"])
        self.assertIn("-metricsPort=9327", START)
        self.assertNotIn("-metricsIp", START)  # follows -ip.bind; the storage stays on 127.0.0.1


class Hardening(unittest.TestCase):
    def test_every_storage_container(self):
        for name, svc in storage_services():
            self.assertRegex(svc["image"], IMAGE, name)
            self.assertEqual(svc["user"], "1000:1000", name)
            self.assertTrue(svc["read_only"], name)
            self.assertEqual(svc["cap_drop"], ["ALL"], name)
            self.assertNotIn("cap_add", svc, name)
            self.assertIn("no-new-privileges:true", svc["security_opt"], name)

    def test_same_image_in_dev_and_staging(self):
        self.assertEqual(OVERLAY["services"]["s3"]["image"], DEV["services"]["s3"]["image"])

    def test_upstream_minio_never_starts_in_dev(self):
        self.assertEqual(DEV["services"]["minio"]["profiles"], ["upstream-minio"])
        self.assertEqual(DEV["services"]["s3"]["ports"], ["127.0.0.1:9100:8333"])

    def test_tool_runs_only_on_request(self):
        tool = OVERLAY["services"]["s3-tool"]
        self.assertEqual(tool["profiles"], ["admin"])
        self.assertEqual(tool["networks"], ["files"])


class Identities(unittest.TestCase):
    def identity(self, name):
        return next(i for i in TEMPLATE["identities"] if i["name"] == name)

    def policy(self, name):
        return json.loads(next(p["content"] for p in TEMPLATE["policies"] if p["name"] == name))

    def test_care_one_key_per_bucket(self):
        """P1-T20b: patient files and pictures each have their own identity, reaching only
        their own bucket's objects (no listing); the combined ihr-care is gone."""
        for name, bucket in (("ihr-care-files", "PATIENT"), ("ihr-care-pictures", "FACILITY")):
            with self.subTest(name=name):
                identity = self.identity(name)
                self.assertEqual(identity["actions"], [f"Read:@{bucket}_BUCKET@", f"Write:@{bucket}_BUCKET@"])
                self.assertNotIn("policyNames", identity)
        names = [i["name"] for i in TEMPLATE["identities"]]
        self.assertNotIn("ihr-care", names)
        self.assertEqual(len(names), len(set(names)))
        self.assertNotIn("@CARE_KEY@", json.dumps(TEMPLATE))

    def test_start_fills_both_care_keys(self):
        keys = START.split('KEYS="', 1)[1].split('"', 1)[0].split()
        for k in ("FILES", "PICTURES"):
            self.assertIn(f"IHR_S3_{k}_KEY", keys)
            self.assertIn(f"IHR_S3_{k}_SECRET", keys)
            self.assertIn(f'-e "s|@{k}_KEY@|$IHR_S3_{k}_KEY|g"', START)
        self.assertNotIn("IHR_S3_CARE_KEY", START)

    def test_writer_may_only_add(self):
        w = self.identity("ihr-anchor-writer")
        self.assertNotIn("actions", w)
        self.assertEqual(w["policyNames"], ["ihr-anchor-write"])
        st = self.policy("ihr-anchor-write")["Statement"]
        self.assertEqual(len(st), 1)
        self.assertEqual(st[0]["Action"], ["s3:PutObject"])
        self.assertEqual(st[0]["Resource"], ["arn:aws:s3:::@ANCHOR_BUCKET@/anchors/*"])

    def test_reader_may_only_read(self):
        r = self.identity("ihr-anchor-reader")
        self.assertNotIn("actions", r)
        acts = {a for s in self.policy("ihr-anchor-read")["Statement"] for a in s["Action"]}
        self.assertTrue(acts)
        for a in acts:
            self.assertRegex(a, r"^s3:(Get|List)")

    def test_no_key_in_the_template(self):
        for i in TEMPLATE["identities"]:
            for c in i["credentials"]:
                self.assertRegex(c["accessKey"], r"^@[A-Z_]+@$")
                self.assertRegex(c["secretKey"], r"^@[A-Z_]+@$")


class Routing(unittest.TestCase):
    def test_files_router_only_object_paths_of_care_buckets(self):
        block = ROUTES.split('# {{ if env "IHR_FILES_BUCKETS" }}', 1)[1].split("# {{ end }}", 1)[0]
        self.assertIn('PathRegexp(`^/({{ env "IHR_FILES_BUCKETS" }})/.+`)', block)
        self.assertIn("service: noop@internal", block)  # everything else on files.<suffix>
        env = OVERLAY["services"]["traefik"]["environment"]["IHR_FILES_BUCKETS"]
        self.assertEqual(env, "${IHR_S3_PATIENT_BUCKET:?}|${IHR_S3_FACILITY_BUCKET:?}")
        self.assertNotIn("ANCHOR", env)

    def test_upload_size_and_time(self):
        self.assertIn("middlewares: [files-headers, files-size]", ROUTES)
        self.assertIn("maxRequestBodyBytes: 110000000", ROUTES)
        cmd = BASE["services"]["traefik"]["command"]
        self.assertIn("--entrypoints.websecure.transport.respondingTimeouts.readTimeout=15m", cmd)

    def test_files_is_a_reserved_slug(self):
        self.assertIn('"files",', MODELS)


class Scripts(unittest.TestCase):
    def test_compliance_ten_years_in_staging(self):
        self.assertIn('echo "IHR_S3_LOCK_MODE=COMPLIANCE"', STAGING_SH)
        self.assertIn('echo "IHR_S3_LOCK_DAYS=3650"', STAGING_SH)

    def test_keys_never_on_a_command_line(self):
        for script in (STAGING_SH, CARE_DEV):
            body = script.split("s3_tool() {", 1)[1].split("\n}\n", 1)[0]
            self.assertIn('envs+=(-e "S3_${k}_${v}")', body)

    def test_deploy_ships_deploy_s3(self):
        self.assertIn("git ls-files -z deploy/staging deploy/s3 ", DEPLOY_SH)

    def test_no_public_bucket(self):
        """P1-T20b (Q-T3): pictures are read with signed links; no bucket gets a policy that
        lets anyone in, and the facility bucket's old public-read policy is removed."""
        setup = (S3 / "setup_buckets.py").read_text()
        self.assertNotIn('"Principal": "*"', setup)
        self.assertNotIn("public_read", setup)
        self.assertNotIn("put_bucket_policy", setup)
        self.assertIn("no_policy(s3, a.facility)", setup)

    def test_care_gets_one_key_per_bucket(self):
        # staging: s3-init and the once-only move of an older server's key files
        self.assertIn('S3_CARE_IDS="FILES PICTURES"', STAGING_SH)
        care_env = STAGING_SH.split("s3_care_env() {", 1)[1].split("\n}\n", 1)[0]
        for bucket in ("FILE_UPLOAD", "FACILITY_S3"):
            for part in ("KEY", "SECRET"):
                self.assertIn(f"{bucket}_{part}=%s", care_env)
        init = STAGING_SH.split("s3_init() {", 1)[1].split("\n}\n", 1)[0]
        self.assertNotIn('echo "BUCKET_KEY=', init)
        self.assertIn("s3_care_env", init)
        # the split itself (once, idempotent, the old key retired): SplitKeys below runs it;
        # every start of the s3 container checks or splits the keys first: S3Starts below
        # dev: the same split and move
        self.assertIn("for k in ADMIN FILES PICTURES ANCHOR_WRITE ANCHOR_READ BACKUP LOGS_ARCHIVE", CARE_DEV)
        self.assertIn("FILE_UPLOAD_KEY=%s", CARE_DEV)
        self.assertIn("FACILITY_S3_KEY=%s", CARE_DEV)
        self.assertNotIn("BUCKET_KEY=%s", CARE_DEV)
        self.assertIn('> "$S3_RETIRED"', CARE_DEV)

    def test_smoke_expects_pictures_to_need_a_signature(self):
        self.assertIn('check "files: hospital pictures need a signature (P1-T20b)" 403', STAGING_SH)
        self.assertNotIn("pictures are public", STAGING_SH)

    def test_live_check_proves_the_old_key_dead(self):
        check = (S3 / "check_keys.py").read_text()
        self.assertIn('good = got == "InvalidAccessKeyId"', check)
        self.assertIn('client(a.endpoint, "RETIRED_CARE")', check)
        self.assertIn('check("anonymous cannot read a facility picture", "denied"', check)
        self.assertIn('check("a signed link reads it", "ok"', check)
        for script in (STAGING_SH, CARE_DEV):
            self.assertIn('envs+=(-e "S3_RETIRED_CARE_${v}")', script)

    def test_setup_refuses_unlocked_or_weaker_anchor_bucket(self):
        setup = (S3 / "setup_buckets.py").read_text()
        self.assertIn("refusing to use it", setup)
        self.assertIn("refusing to weaken it", setup)
        self.assertIn("ObjectLockEnabledForBucket=True", setup)


# --- P1-T20b review: where CARE's keys are split, and who refuses unsplit ones (S1, S2, C1, C2, C3) ---

OLD_KEYS = ("ADMIN", "CARE", "ANCHOR_WRITE", "ANCHOR_READ", "BACKUP", "LOGS_ARCHIVE")
# Each value is distinctive, so a test can tell that none of them was printed
# The old CARE secret, as GATEWAY_OLD makes it for "CARE" (built, so no literal looks like a key)
OLD_CARE_SECRET = "care" + "-secret-" + "0123456789"
GATEWAY_OLD = "".join(f"IHR_S3_{k}_KEY=ihr{k.lower()}keyid\nIHR_S3_{k}_SECRET={k.lower()}-secret-0123456789\n"
                      for k in OLD_KEYS)
DB_URLS = (f"DATABASE_URL=postgres://ihr_app:{'a' * 43}@db:5432/ihr\n",
           f"DATABASE_URL=postgres://ihr_owner:{'o' * 43}@db:5432/ihr\n")
APP_OLD = (DB_URLS[0] + "FILE_UPLOAD_BUCKET=ihr-staging-files\nFACILITY_S3_BUCKET=ihr-staging-facility\n"
           "BUCKET_ENDPOINT=http://s3:8333\nBUCKET_EXTERNAL_ENDPOINT=https://files.app.example.in\n"
           "BUCKET_KEY=ihrcarekeyid\nBUCKET_SECRET=" + OLD_CARE_SECRET + "\n")
FAKE_DOCKER = "#!/bin/sh\necho \"$*\" >> \"$FAKE_LOG\"\necho \"docker must not run: $*\" >&2\nexit 9\n"


def make_site(d, *, s3="local", gateway=GATEWAY_OLD, app=APP_OLD, local=False):
    """A staging folder (.env, secrets/) as init and s3-init made it; P1-T19b's roles split."""
    d.mkdir(parents=True, exist_ok=True)
    (d / ".env").write_text("IHR_TENANT_DOMAIN_SUFFIX=app.example.in\nIHR_BACKEND_IMAGE=indiehr-backend:staging\n"
                            + (f"IHR_S3={s3}\n" if s3 else ""))
    sec = d / "secrets"
    sec.mkdir(mode=0o700)
    (sec / "app.env").write_text(app)
    (sec / "db-owner.env").write_text(DB_URLS[1])
    if gateway is not None:
        (sec / "s3-gateway.env").write_text(gateway)
    if local:
        (d / ".local").touch()
    return d


def env_values(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))


def tree_digest(d):
    return {str(f.relative_to(d)): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(d.rglob("*")) if f.is_file()}


def run_staging(*args, site, out=None):
    """staging.sh with docker replaced by a stand-in that only logs (any call is a failure here)"""
    bindir = site.parent / "bin"
    bindir.mkdir(exist_ok=True)
    dk = bindir / "docker"
    dk.write_text(FAKE_DOCKER)
    dk.chmod(dk.stat().st_mode | stat.S_IXUSR)
    log = site.parent / "docker-calls"
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "IHR_STAGING_DIR": str(site), "FAKE_LOG": str(log)}
    env.pop("IHR_STAGING_OUT", None)
    if out is not None:
        env["IHR_STAGING_OUT"] = str(out)
    proc = subprocess.run(["bash", str(ROOT / "scripts" / "staging.sh"), *args], env=env, capture_output=True,
                          text=True, timeout=60, stdin=subprocess.DEVNULL)
    return proc, (log.read_text() if log.exists() else "")


class SplitKeys(unittest.TestCase):
    """S1/S2: CARE's one key is split where the secrets are kept (the deploying machine:
    s3-split-keys), once, never on a server; deploy.sh sends nothing unsplit."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def assertNoSecretPrinted(self, proc, *files):
        printed = proc.stdout + proc.stderr
        for f in files:
            for name, value in env_values(f).items():
                if "SECRET" in name or name.endswith("_KEY") or name == "DATABASE_URL":
                    self.assertNotIn(value, printed, name)

    def test_split_on_the_deploying_machine_once(self):
        out = make_site(self.tmp / "envdir")
        server = self.tmp / "unused"  # DIR: never touched, the command works on IHR_STAGING_OUT
        server.mkdir()
        proc, calls = run_staging("s3-split-keys", site=server, out=out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(calls, "")
        sec = out / "secrets"
        gw, app, retired = env_values(sec / "s3-gateway.env"), env_values(sec / "app.env"), env_values(sec / "s3-retired.env")
        self.assertNotIn("IHR_S3_CARE_KEY", gw)
        self.assertNotIn("IHR_S3_CARE_SECRET", gw)
        for k in ("FILES", "PICTURES"):
            self.assertRegex(gw[f"IHR_S3_{k}_KEY"], r"^ihr[0-9a-f]{20}$")
            self.assertRegex(gw[f"IHR_S3_{k}_SECRET"], r"^[0-9a-f]{40}$")
        self.assertNotEqual(gw["IHR_S3_FILES_KEY"], gw["IHR_S3_PICTURES_KEY"])
        self.assertEqual((app["FILE_UPLOAD_KEY"], app["FILE_UPLOAD_SECRET"]), (gw["IHR_S3_FILES_KEY"], gw["IHR_S3_FILES_SECRET"]))
        self.assertEqual((app["FACILITY_S3_KEY"], app["FACILITY_S3_SECRET"]),
                         (gw["IHR_S3_PICTURES_KEY"], gw["IHR_S3_PICTURES_SECRET"]))
        self.assertNotIn("BUCKET_KEY", app)
        self.assertNotIn("BUCKET_SECRET", app)
        for kept in ("DATABASE_URL", "BUCKET_ENDPOINT", "BUCKET_EXTERNAL_ENDPOINT", "FILE_UPLOAD_BUCKET"):
            self.assertIn(kept, app)
        for k in ("ADMIN", "ANCHOR_WRITE", "ANCHOR_READ", "BACKUP", "LOGS_ARCHIVE"):
            self.assertEqual(gw[f"IHR_S3_{k}_KEY"], f"ihr{k.lower()}keyid")
        self.assertEqual(retired, {"IHR_S3_RETIRED_CARE_KEY": "ihrcarekeyid",
                                   "IHR_S3_RETIRED_CARE_SECRET": OLD_CARE_SECRET})
        for f in ("s3-gateway.env", "app.env", "s3-retired.env"):
            self.assertEqual(stat.S_IMODE((sec / f).stat().st_mode), 0o600, f)
        self.assertIn("s3-gateway.env", proc.stdout)
        self.assertNoSecretPrinted(proc, sec / "s3-gateway.env", sec / "app.env", sec / "s3-retired.env")
        self.assertEqual(sorted(p.name for p in server.iterdir()), [])
        # once: a second run changes nothing, and the check passes
        before = tree_digest(out)
        proc, _ = run_staging("s3-split-keys", site=server, out=out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("split already: nothing changed", proc.stdout)
        self.assertEqual(tree_digest(out), before)
        proc, _ = run_staging("s3-split-keys", "--check", site=server, out=out)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_an_interrupted_split_is_finished_from_the_gateways_keys(self):
        gw = GATEWAY_OLD.replace(f"IHR_S3_CARE_KEY=ihrcarekeyid\nIHR_S3_CARE_SECRET={OLD_CARE_SECRET}\n", "")
        gw += "IHR_S3_FILES_KEY=ihrfileskeyid\nIHR_S3_FILES_SECRET=files-secret-0123456789\n"
        out = make_site(self.tmp / "envdir", gateway=gw)  # app.env still has BUCKET_KEY
        proc, _ = run_staging("s3-split-keys", site=out, out=out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        g, app = env_values(out / "secrets" / "s3-gateway.env"), env_values(out / "secrets" / "app.env")
        self.assertEqual(g["IHR_S3_FILES_KEY"], "ihrfileskeyid")  # kept, never replaced
        self.assertEqual(app["FILE_UPLOAD_KEY"], "ihrfileskeyid")
        self.assertEqual(app["FACILITY_S3_KEY"], g["IHR_S3_PICTURES_KEY"])
        self.assertNotIn("BUCKET_KEY", app)

    def test_check_refuses_unsplit_keys_and_names_the_command(self):
        out = make_site(self.tmp / "envdir")
        before = tree_digest(out)
        proc, calls = run_staging("s3-split-keys", "--check", site=out, out=out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("still holds the combined CARE key", proc.stderr)
        self.assertIn("scripts/staging.sh s3-split-keys", proc.stderr)
        self.assertEqual(tree_digest(out), before)
        self.assertEqual(calls, "")

    def test_a_provider_site_needs_one_key_per_bucket(self):
        cases = (
            (APP_OLD, "BUCKET_KEY / BUCKET_SECRET"),
            (DB_URLS[0] + "BUCKET_ENDPOINT=https://s3.example\nFILE_UPLOAD_KEY=f\n", "lacks FILE_UPLOAD_KEY or FACILITY_S3_KEY"),
            (DB_URLS[0] + "BUCKET_ENDPOINT=https://s3.example\nFILE_UPLOAD_KEY=f\nFACILITY_S3_KEY=p\n", None),
            (DB_URLS[0] + "BUCKET_ENDPOINT=https://s3.example\nBUCKET_PROVIDER=AWS_ROLE_BASED\n", None),
            (DB_URLS[0], None),  # no object storage set up here
        )
        for i, (app, why) in enumerate(cases):
            with self.subTest(why=why, i=i):
                out = make_site(self.tmp / f"provider{i}", s3=None, gateway=None, app=app)
                proc, _ = run_staging("s3-split-keys", "--check", site=out, out=out)
                if why:
                    self.assertEqual(proc.returncode, 1)
                    self.assertIn(why, proc.stderr)
                    self.assertIn("Moving to a provider", proc.stderr)
                else:
                    self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_deploy_refuses_unsplit_keys_before_sending_anything(self):
        check = DEPLOY_SH.index('IHR_STAGING_OUT="$ENV_DIR" scripts/staging.sh s3-split-keys --check')
        self.assertLess(check, DEPLOY_SH.index('log "Checking $HOST'))
        self.assertLess(check, DEPLOY_SH.index('log "Sending .env and secrets/'))
        self.assertIn('|| die "$ENV_DIR: CARE\'s storage keys are not split', DEPLOY_SH[check:check + 300])
        # the throwaway VM's own folder is split here, before the check
        vm = DEPLOY_SH.index('if ((LOCAL_PROOF)); then IHR_STAGING_OUT="$ENV_DIR" scripts/staging.sh s3-split-keys')
        self.assertLess(vm, check)


class ServerRefusesUnsplitKeys(unittest.TestCase):
    """S1/S2: on a server (no .local) every command that starts the new build refuses CARE's
    combined key before anything changes: no docker call, no key made there."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_release_db_roles_up_and_rollback_refuse_before_any_change(self):
        for args in (("release", "--operator", "tester"), ("release", "--now", "--operator", "tester"),
                     ("db-roles", "--migrate", "--operator", "tester"), ("up",), ("rollback", "--operator", "tester")):
            with self.subTest(args=args):
                site = make_site(self.tmp / "-".join(args) / "staging")
                before = tree_digest(site)
                proc, calls = run_staging(*args, site=site)
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertIn("still holds the combined CARE key", proc.stderr)
                self.assertIn("scripts/staging.sh s3-split-keys", proc.stderr)
                self.assertIn("Nothing was changed", proc.stderr)
                self.assertEqual(calls, "")  # nothing held, stopped, run or started
                self.assertEqual(tree_digest(site), before)  # no key made on the server
                self.assertFalse((site / "releases" / "attempt").exists())

    def test_the_guard_comes_before_the_first_compose_call(self):
        for fn in ("release", "db_roles_cmd", "up", "rollback"):
            with self.subTest(fn=fn):
                body = STAGING_SH.split(f"\n{fn}() {{", 1)[1].split("\n}\n", 1)[0]
                if fn == "db_roles_cmd":
                    body = body.split("    --migrate)", 1)[1]
                first = min(i for i in (body.find("compose "), body.find("rel_lock"), body.find("old_manage"))
                            if i >= 0)
                self.assertLess(body.index("s3_need_split_keys"), first)

    def test_every_start_of_s3_refuses_on_a_server_and_splits_in_a_proof(self):
        """s3_keys_current runs before every start of the s3 container (up, the backup
        repository's reads, restore): a server's unsplit keys are refused there too, never split."""
        fns = "".join(staging_func(f) for f in ("die", "write_secret", "s3_care_env", "s3_archive_key",
                                               "s3_split_care_key", "s3_keys_problem", "s3_need_split_keys",
                                               "s3_keys_current"))
        for local in (False, True):
            with self.subTest(local=local):
                site = make_site(self.tmp / f"keys-{local}" / "staging", local=local)
                before = tree_digest(site)
                script = (f'set -euo pipefail\nDIR="{site}"\nSECRETS="{site}/secrets"\nS3_CARE_IDS="FILES PICTURES"\n'
                          f'env_get() {{ sed -n "s/^$1=//p" "$DIR/.env" | tail -1; }}\n{fns}s3_keys_current\n')
                out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
                gw = env_values(site / "secrets" / "s3-gateway.env")
                if local:
                    self.assertEqual(out.returncode, 0, out.stderr)
                    self.assertIn("IHR_S3_FILES_KEY", gw)
                else:
                    self.assertEqual(out.returncode, 1, out.stderr)
                    self.assertIn("scripts/staging.sh s3-split-keys", out.stderr)
                    self.assertEqual(tree_digest(site), before)

    def test_a_local_proof_splits_its_own_keys(self):
        site = make_site(self.tmp / "proof" / "staging", local=True)
        proc, calls = run_staging("up", site=site)
        gw = env_values(site / "secrets" / "s3-gateway.env")
        self.assertIn("IHR_S3_FILES_KEY", gw)
        self.assertNotIn("IHR_S3_CARE_KEY", gw)
        self.assertNotIn("BUCKET_KEY", env_values(site / "secrets" / "app.env"))
        self.assertNotIn("still holds the combined CARE key", proc.stderr)
        self.assertNotEqual(calls, "")  # then goes on to start (the stand-in docker stops it)


def stack_tgz(path, files):
    with tarfile.open(path, "w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def staging_func(name):
    m = re.search(r"^%s\(\) \{(.*?)^\}" % re.escape(name), STAGING_SH, re.S | re.M)
    assert m, name
    return f"{name}() {{{m.group(1)}}}\n"


OLD_START = 'KEYS="IHR_S3_ADMIN_KEY IHR_S3_CARE_KEY IHR_S3_CARE_SECRET"\n'


class RollbackAcrossTheKeySplit(unittest.TestCase):
    """C1/S3: a release from before P1-T20b cannot run on split keys (and would make the pictures
    bucket public again): the target is recognised by its kept stack's start.sh."""

    def pre(self, start, app):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "secrets").mkdir()
            (d / "secrets" / "app.env").write_text(app)
            if start is not None:
                stack_tgz(d / "stack-R0.tgz", {"deploy/s3/start.sh": start, "deploy/staging/compose.yaml": "x"})
            else:
                stack_tgz(d / "stack-R0.tgz", {"deploy/staging/compose.yaml": "x"})
            script = (f'set -euo pipefail\nREL_DIR="{d}"\nSECRETS="{d}/secrets"\n' + staging_func("rel_s3_split_here")
                      + staging_func("rel_target_pre_s3split") + "rel_target_pre_s3split R0 && echo yes || echo no\n"
                      + "rel_target_pre_s3split R9 && echo yes || echo no\n")
            out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.splitlines()[1], "no")  # no kept stack: never refused for it
            return out.stdout.splitlines()[0] == "yes"

    def test_target_from_before_the_split(self):
        split = DB_URLS[0] + "FILE_UPLOAD_KEY=f\nFACILITY_S3_KEY=p\n"
        self.assertTrue(self.pre(OLD_START, split))
        self.assertTrue(self.pre(None, split))  # no start.sh at all: older still
        self.assertFalse(self.pre(START, split))  # this release's own start.sh
        self.assertFalse(self.pre(OLD_START, APP_OLD))  # a site not split: both old, nothing to refuse

    def test_guard_and_advice_use_it(self):
        guard = STAGING_SH.split("\nrel_roles_guard() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('if rel_target_pre_s3split "$1"; then', guard)
        self.assertIn("predates the split object-storage keys (P1-T20b)", guard)
        self.assertIn("nothing was changed", guard)
        advice = STAGING_SH.split("\nrel_rollback_advice() {", 1)[1].split("\n}\n", 1)[0]
        self.assertEqual(advice.count('elif rel_target_pre_s3split "$t"; then pres3=1'), 2)
        self.assertIn("predates the split object-storage keys (P1-T20b). Fix forward", advice)


# Services whose start also starts s3 (their depends_on, followed through every staging file)
def s3_dependents():
    deps = {}
    for f in STAGING.glob("compose*.yaml"):
        for name, svc in (yaml.load(f.read_text(), Loader=_Loader).get("services") or {}).items():
            d = (svc or {}).get("depends_on") or []
            deps.setdefault(name, set()).update(d if isinstance(d, list) else d.keys())
    found, grew = {"s3"}, True
    while grew:
        grew = False
        for name, d in deps.items():
            if name not in found and d & found:
                found.add(name)
                grew = True
    return found


_ARRAY_USE = re.compile(r"\$\{(\w+)\[@\]")
_EXPANSION = re.compile(r"\$")


def _logical_lines(body):
    """The body's lines with backslash continuations joined: a call split over several lines is
    one line here"""
    out, cur = [], ""
    for line in body:
        if line.rstrip().endswith("\\"):
            cur += line.rstrip()[:-1] + " "
            continue
        out.append(cur + line)
        cur = ""
    if cur:
        out.append(cur)
    return out


def _array_words(body, name):
    """Every word the function puts into the array `name` (name=(...), name+=(...)), or None when
    any of them is not a plain literal (an expansion, mapfile/read into it): unknown"""
    words, seen = set(), False
    for line in body:
        code = line.split(" # ", 1)[0]
        if re.search(rf"\b(mapfile|readarray|read)\b[^;|]*\b{re.escape(name)}\b", code):
            return None
        for m in re.finditer(rf"(?<![\w$]){re.escape(name)}\+?=\(([^)]*)\)", code):
            seen = True
            for w in m.group(1).split():
                w = w.strip("\"'")
                if not re.fullmatch(r"[A-Za-z0-9_.-]+", w):
                    return None
                words.add(w)
    return words if seen else None


def s3_starts_unchecked(text, dependents):
    """(function, line) for each compose call that starts the s3 container (by name, or through a
    service that depends on it, without --no-deps) with no s3_keys_current (or s3_up, which runs
    it first) on that line or earlier in the same function. Top-level code counts as one
    function. A call split over lines (backslash) is read as one line; services given through
    an array are what the function puts into it, and an array it fills from anything but
    literals (or a plain variable) counts as starting s3."""
    bodies, lines, i = [], text.splitlines(), 0
    top = []
    while i < len(lines):
        m = re.match(r"^([A-Za-z_]\w*)\(\) \{", lines[i])
        if m and lines[i].rstrip().endswith("}"):
            bodies.append((m.group(1), [lines[i]]))
        elif m:
            j = i + 1
            while j < len(lines) and lines[j] != "}":
                j += 1
            bodies.append((m.group(1), lines[i:j]))
            i = j
        else:
            top.append(lines[i])
        i += 1
    bodies.append(("<top level>", top))
    found = []
    for name, raw in bodies:
        body = _logical_lines(raw)
        for n, line in enumerate(body):
            code = line.split(" # ", 1)[0]
            m = re.search(r"\bcompose\b(.*?)\b(up|start|restart|create|run)\b(.*)", code)
            if code.lstrip().startswith("#") or not m:
                continue
            words = {w.strip("\"'();{}") for w in code.split()}
            if "--no-start" in words:
                continue
            # What the call names after its verb, with arrays resolved; unknown counts as s3
            named, unknown = set(), False
            for w in m.group(3).split():
                w = w.strip("\"'();{}")
                arrays = _ARRAY_USE.findall(w)
                if arrays:
                    for a in set(arrays):
                        vals = _array_words(raw, a)
                        if vals is None:
                            unknown = True
                        else:
                            named |= vals
                elif _EXPANSION.match(w) and not w.startswith("$("):
                    unknown = True
                else:
                    named.add(w)
            no_deps = "--no-deps" in words
            starts = "s3" in named or (not no_deps and (unknown or named & (dependents - {"s3"})))
            if starts and not any("s3_keys_current" in b or re.search(r"(^|[\s;|&{(])s3_up\b", b)
                                  for b in body[:n + 1]):
                found.append((name, line.strip()))
    return found


class S3Starts(unittest.TestCase):
    """C3: every start of the s3 container (any form) has the key files checked first, in the
    same function; the check is shown able to fail."""

    def test_every_start_checks_the_keys_first(self):
        dependents = s3_dependents()
        self.assertTrue({"s3", "backend", "celery-worker"} <= dependents, dependents)
        self.assertEqual(s3_starts_unchecked(STAGING_SH, dependents), [])
        up = STAGING_SH.split("\ns3_up() {", 1)[1].split("\n}\n", 1)[0]
        self.assertLess(up.index("s3_keys_current"), up.index("compose up -d --wait s3"))

    def test_the_check_can_fail(self):
        deps = {"s3", "backend"}
        for snippet, flagged in (
            ("foo() {\n  compose up -d --wait s3\n}\n", True),
            ("foo() {\n  compose start s3\n}\n", True),
            ("foo() {\n  compose restart s3 >/dev/null\n}\n", True),
            ("foo() {\n  compose up -d --wait --force-recreate s3\n}\n", True),
            ("foo() {\n  compose up -d --wait backend\n}\n", True),  # starts s3 as its dependency
            ("bar() { s3_keys_current; }\nfoo() {\n  compose up -d --wait s3\n}\n", True),  # another function's
            ("compose up -d --wait s3\n", True),  # top level
            ("foo() {\n  compose up -d --wait --no-deps backend\n}\n", False),
            ("foo() {\n  s3_keys_current\n  compose up -d --wait s3\n}\n", False),
            ("foo() {\n  [[ x ]] || { s3_keys_current >/dev/null; compose up -d --wait s3 >/dev/null; }\n}\n", False),
            ("foo() {\n  compose --profile admin run --rm --no-deps -T s3-tool x\n}\n", False),
            ("foo() {\n  compose up --no-start db backup s3\n}\n", False),
            # Split over a backslash continuation: still one call (the second fixer's review)
            ("foo() {\n  compose up -d --wait \\\n    s3\n}\n", True),
            ("foo() {\n  compose up -d \\\n    --wait backend\n}\n", True),
            ("foo() {\n  compose up -d --wait \\\n    --no-deps backend\n}\n", False),
            # Services through an array: what the function puts into it
            ("foo() {\n  local extra=()\n  extra+=(backend)\n  compose up -d --wait \"${extra[@]}\" traefik\n}\n", True),
            ("foo() {\n  local extra=(s3)\n  compose up -d --wait --no-deps \"${extra[@]}\"\n}\n", True),
            ("foo() {\n  local extra=()\n  extra+=(traefik)\n  compose up -d --wait \"${extra[@]}\"\n}\n", False),
            ("foo() {\n  mapfile -t extra < x\n  compose up -d --wait \"${extra[@]}\"\n}\n", True),  # unknown
            ("foo() {\n  compose up -d --wait \"${extra[@]}\"\n}\n", True),  # never filled here: unknown
            ("foo() {\n  compose up -d --wait \"$svc\"\n}\n", True),  # a plain variable: unknown
            ("foo() {\n  local extra=(backend)\n  s3_up\n  compose up -d --wait \"${extra[@]}\"\n}\n", False),
            ("foo() {\n  [[ x ]] || s3_up\n  compose up -d --wait backend\n}\n", False),
        ):
            with self.subTest(snippet=snippet):
                self.assertEqual(bool(s3_starts_unchecked(snippet, deps)), flagged)


class DevSplitMovesTheRunningContainers(unittest.TestCase):
    """C2: care-dev.sh splits the dev keys on whatever command runs first after the pull; the
    running gateway is recreated at once (so it knows the new keys and forgets the old one), then
    the running backend and celery, never a backend on keys the gateway does not know."""

    def run_dev(self, running, gateway=GATEWAY_OLD):
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "scripts").mkdir()
        for f in ("care-dev.sh", "client-keys.sh"):
            shutil.copy2(ROOT / "scripts" / f, repo / "scripts" / f)
        (repo / "deploy" / "dev").mkdir(parents=True)
        (repo / "deploy" / "dev" / "s3-gateway.env").write_text(gateway)
        bindir = repo / "bin"
        bindir.mkdir()
        dk = bindir / "docker"
        dk.write_text('#!/bin/sh\necho "$*" >> "$FAKE_LOG"\ncase "$*" in\n'
                      '  *" ps -q "*) for s in $FAKE_RUNNING; do case "$*" in *" ps -q $s") echo "cid-$s";; esac; done;;\n'
                      'esac\nexit 0\n')
        dk.chmod(0o755)
        log = repo / "calls"
        env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_LOG": str(log), "FAKE_RUNNING": running}
        proc = subprocess.run(["bash", str(repo / "scripts" / "care-dev.sh"), "ps"], env=env, capture_output=True,
                              text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        care = env_values(repo / "deploy" / "dev" / "s3-care.env")
        # The compose starts, and the bucket set-up runs, in the order they were made
        calls = [ln for ln in (log.read_text() if log.exists() else "").splitlines()
                 if " up " in f" {ln} " or "setup_buckets.py" in ln]
        return calls, care, proc

    def test_the_split_recreates_the_gateway_then_the_app(self):
        ups, care, proc = self.run_dev("s3 backend celery")
        self.assertIn("FILE_UPLOAD_KEY", care)
        self.assertEqual(len(ups), 3, ups)
        self.assertTrue(ups[0].endswith("up -d --wait --no-deps --force-recreate s3"), ups)
        self.assertTrue(ups[1].endswith("up -d --wait --no-deps --force-recreate celery backend"), ups)
        self.assertIn("s3 recreated with the split keys, then celery backend", proc.stderr)

    def test_the_split_takes_the_old_public_policy_off_at_once(self):
        # The next command after the pull may not be `up` (keycloak up, ps): the buckets are set
        # up again right after the move, so the pictures bucket is private now
        ups, _, proc = self.run_dev("s3")
        self.assertEqual(len(ups), 2, ups)
        self.assertTrue(ups[0].endswith("--force-recreate s3"), ups)
        self.assertIn(" run --rm --no-deps -T ", ups[1])
        self.assertIn("/ihr-s3/setup_buckets.py --endpoint http://s3:8333", ups[1])
        self.assertIn("buckets set up again (pictures private)", proc.stderr)

    def test_only_what_runs_is_recreated(self):
        ups, _, _ = self.run_dev("s3 backend")
        self.assertTrue(ups[1].endswith("--force-recreate backend"), ups)
        self.assertIn("setup_buckets.py", ups[2])
        ups, _, _ = self.run_dev("")  # nothing runs: the next `up` starts everything on the new keys
        self.assertEqual(ups, [])

    def test_a_backend_running_without_its_gateway_moves_too(self):
        # s3 stopped, backend running on the retired key: the gateway is started with the split
        # keys first, then the backend moves onto them
        ups, _, _ = self.run_dev("backend")
        self.assertEqual(len(ups), 3, ups)
        self.assertTrue(ups[0].endswith("--force-recreate s3"), ups)
        self.assertTrue(ups[1].endswith("--force-recreate backend"), ups)
        self.assertIn("setup_buckets.py", ups[2])

    def test_nothing_is_recreated_once_split(self):
        split = GATEWAY_OLD.replace(f"IHR_S3_CARE_KEY=ihrcarekeyid\nIHR_S3_CARE_SECRET={OLD_CARE_SECRET}\n", "")
        split += "IHR_S3_FILES_KEY=ihrf\nIHR_S3_FILES_SECRET=fs\nIHR_S3_PICTURES_KEY=ihrp\nIHR_S3_PICTURES_SECRET=ps\n"
        ups, _, _ = self.run_dev("s3 backend celery", gateway=split)
        self.assertEqual(ups, [])


class ReviewedTexts(unittest.TestCase):
    """S4/C4: the reviewed reasons and limits say what P1-T20b built, not what came before."""

    def test_anonymous_allowlist_names_the_signed_links(self):
        text = (ROOT / "plugins" / "ihr_platform" / "ihr_platform" / "tests" / "isolation"
                / "anonymous_allowlist.txt").read_text()
        self.assertNotIn("unsigned read_cover_image_url", text)
        finder = next(ln for ln in text.splitlines() if ln.startswith("/api/v1/^getallfacilities/$"))
        self.assertIn("24 h signed link", finder)
        self.assertIn("test_picture_links_are_signed_for_this_hospital_only", finder)
        doctors = next(ln for ln in text.splitlines() if ln.split("#")[0].rstrip().endswith("schedulable_users/$"))
        self.assertIn("signed link", doctors)

    def test_design_limits_match_the_web_app_and_aws(self):
        design = (ROOT / "docs" / "reviews" / "p1-t20" / "design.md").read_text()
        self.assertNotIn("The web app falls back to initials if a link fails.", design)
        self.assertNotIn("answers 404 at once", design)
        self.assertIn("404 on SeaweedFS, 403 AccessDenied on infra/aws", design)
        self.assertIn("show a broken image", design)
        self.assertIn("No rollback to a release from before P1-T20b", design)

    def test_storage_comment_matches_the_design(self):
        # The code's own account of a deleted picture's link says what design.md says (C4)
        text = (ROOT / "plugins" / "ihr_platform" / "ihr_platform" / "storage.py").read_text()
        self.assertNotIn("simply answers 404", text)
        self.assertIn("404 on SeaweedFS, 403 AccessDenied on\n# infra/aws", text)


if __name__ == "__main__":
    unittest.main()
