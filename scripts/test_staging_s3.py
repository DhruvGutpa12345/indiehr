"""Static checks of the object storage pieces (P1-T8b3): deploy/s3/, compose.s3.yaml, the dev
override's SeaweedFS, routes.yml's files router and staging.sh. The live checks are
`scripts/care-dev.sh s3-check` and `scripts/staging.sh s3-check` (deploy/s3/check_keys.py).
Run: python3 -m unittest scripts/test_staging_s3.py
"""

import json
import re
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

    def test_care_only_its_two_buckets(self):
        actions = self.identity("ihr-care")["actions"]
        self.assertTrue(actions)
        for a in actions:
            self.assertRegex(a, r"^(Read|Write|List):@(PATIENT|FACILITY)_BUCKET@$")
        self.assertNotIn("Admin", " ".join(actions))

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

    def test_setup_refuses_unlocked_or_weaker_anchor_bucket(self):
        setup = (S3 / "setup_buckets.py").read_text()
        self.assertIn("refusing to use it", setup)
        self.assertIn("refusing to weaken it", setup)
        self.assertIn("ObjectLockEnabledForBucket=True", setup)


if __name__ == "__main__":
    unittest.main()
