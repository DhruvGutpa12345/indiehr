"""Static checks of the Keycloak signing keys' stack side (P1-T8d2): one volume per key holder,
mounted read-only into that holder only; the key tool offline and unlogged; no worker secret
left in any env file; the order of `staging.sh up`; the rotation steps; the dev token viewer
retired. The live proof is local-proof (smoke) plus `scripts/staging.sh keys rotate all`.
Run: python3 -m unittest scripts/test_staging_keys.py
"""

import re
import unittest
from pathlib import Path

import yaml


class _Loader(yaml.SafeLoader):
    pass


# Compose's !override / !reset tags in the dev override
_Loader.add_multi_constructor("!", lambda loader, suffix, node: loader.construct_sequence(node)
                              if isinstance(node, yaml.SequenceNode) else loader.construct_mapping(node)
                              if isinstance(node, yaml.MappingNode) else loader.construct_scalar(node))


def load(path):
    return yaml.load(path.read_text(), Loader=_Loader)


ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "deploy" / "staging"
COMPOSE = load(STAGING / "compose.yaml")
SERVICES = COMPOSE["services"]
DEV = load(ROOT / "deploy" / "dev" / "keycloak.override.yaml")
DEV_LOCAL = load(ROOT / "deploy" / "dev" / "care-local.override.yaml")
STAGING_SH = (ROOT / "scripts" / "staging.sh").read_text()
CARE_DEV = (ROOT / "scripts" / "care-dev.sh").read_text()
LIB = (ROOT / "scripts" / "client-keys.sh").read_text()
DEPLOY = (ROOT / "scripts" / "deploy.sh").read_text()

# purpose -> the one service that holds and signs with that key
HOLDERS = {"backend": "backend", "onboarding": "provisioner", "staff": "staff-worker"}
JWKS = "/run/ihr-jwks"


def function(text, name):
    return text.split(f"\n{name}() {{", 1)[1].split("\n}\n", 1)[0]


def mounts(service):
    """(source, target, mode) of each short-syntax volume."""
    out = []
    for v in service.get("volumes") or []:
        parts = v.split(":")
        out.append((parts[0], parts[1], parts[2] if len(parts) > 2 else "rw"))
    return out


def compose_files():
    files = list(STAGING.glob("compose*.yaml"))
    files += [ROOT / "deploy" / "dev" / "keycloak.override.yaml", ROOT / "deploy" / "dev" / "care-local.override.yaml"]
    return {f.name: load(f) for f in files}


class Volumes(unittest.TestCase):
    def check_holders(self, services):
        for purpose, holder in HOLDERS.items():
            keys = [m for m in mounts(services[holder]) if m[0].startswith("clientkey-")]
            self.assertEqual(keys, [(f"clientkey-{purpose}", "/run/ihr-keys", "ro")], holder)

    def test_each_holder_mounts_only_its_own_key_read_only(self):
        self.check_holders(SERVICES)
        self.check_holders(DEV["services"])

    def test_no_other_service_sees_a_key(self):
        allowed = set(HOLDERS.values()) | {"keytool"}
        for name, doc in compose_files().items():
            for svc_name, svc in (doc.get("services") or {}).items():
                text = str(svc.get("volumes") or [])
                if "clientkey-" in text:
                    self.assertIn(svc_name, allowed, f"{name}: {svc_name}")

    def test_volumes_declared(self):
        for doc in (COMPOSE, DEV):
            for purpose in HOLDERS:
                self.assertIn(f"clientkey-{purpose}", doc["volumes"])

    def test_public_keys_only_where_needed(self):
        # The sync (admin) and the worker that creates hospital clients; never a private key
        def jwks(svc):
            return [m for m in mounts(svc) if m[1] == JWKS]

        self.assertEqual(jwks(SERVICES["admin"]), [("./generated/client-keys", JWKS, "ro")])
        self.assertEqual(jwks(SERVICES["provisioner"]), [("./generated/client-keys", JWKS, "ro")])
        for name in ("backend", "staff-worker", "celery-worker", "celery-beat", "migrate"):
            self.assertEqual(jwks(SERVICES[name]), [], name)
        self.assertEqual(jwks(DEV["services"]["staff-worker"]), [])
        for name in ("backend", "provisioner"):  # the dev backend also runs the dev sync
            self.assertEqual(jwks(DEV["services"][name]), [("../../deploy/dev/client-keys", JWKS, "ro")])

    def test_folders_made_before_docker_could(self):
        self.assertIn('mkdir -p "$GEN/client-keys"', function(STAGING_SH, "compose"))
        self.assertIn('mkdir -p "$ROOT/deploy/dev/client-keys"', CARE_DEV.split("\nkt() {", 1)[0])
        self.assertIn("deploy/dev/client-keys/", (ROOT / ".gitignore").read_text().splitlines())


class KeyTool(unittest.TestCase):
    def test_offline_unlogged_one_off(self):
        for kt in (SERVICES["keytool"], DEV["services"]["keytool"]):
            self.assertEqual(kt["profiles"], ["keys"])
            self.assertEqual(kt["network_mode"], "none")
            self.assertEqual(kt["logging"], {"driver": "none"})
            self.assertEqual(kt["restart"], "no")
            self.assertNotIn("env_file", kt)
            self.assertNotIn("networks", kt)
            self.assertNotIn("ports", kt)
            self.assertEqual(
                sorted(m for m in mounts(kt) if m[0].startswith("clientkey-")),
                [(f"clientkey-{p}", f"/run/ihr-keytool/{p}", "rw") for p in sorted(HOLDERS)],
            )
            self.assertIn("ihr_platform.client_keys", str(kt["entrypoint"]))

    def test_staging_runs_the_tool_as_the_app_user(self):
        kt = SERVICES["keytool"]
        self.assertEqual(kt["image"], "${IHR_BACKEND_IMAGE:?set IHR_BACKEND_IMAGE}")
        self.assertEqual(kt["cap_drop"], ["ALL"])
        self.assertEqual(sorted(kt["cap_add"]), ["CHOWN", "SETGID", "SETUID"])
        self.assertEqual(kt["security_opt"], ["no-new-privileges:true"])
        script = kt["entrypoint"][2]
        # root only hands an empty volume over; the tool itself runs as django
        self.assertIn("exec setpriv --reuid=django --regid=django --clear-groups python -m ihr_platform.client_keys", script)
        self.assertIn("chown django:django", script)

    def test_only_these_services_are_one_offs_without_logs(self):
        # Never log what the tool or the sync prints; never let a service keep running unlogged
        for name, svc in SERVICES.items():
            if (svc.get("logging") or {}).get("driver") == "none":
                self.assertIn(name, ("admin", "keytool"))


class Secrets(unittest.TestCase):
    def test_no_worker_secret_file_anywhere(self):
        for name, doc in compose_files().items():
            text = yaml.safe_dump(doc)
            for gone in ("provisioner.env", "staff-worker.env", "_ONBOARDING_SECRET", "_STAFF_SECRET"):
                self.assertNotIn(gone, text, name)
        for text in (STAGING_SH, CARE_DEV):
            self.assertNotIn("ihr_keycloak_service_secret", text)
            self.assertNotIn("worker_secret", text)
            self.assertNotIn("write_worker_env", text)
        self.assertNotIn("provisioner.env", function(STAGING_SH, "init"))

    def test_leftovers_removed_and_never_shipped(self):
        self.assertIn('rm -f "$SECRETS/provisioner.env" "$SECRETS/staff-worker.env"', function(STAGING_SH, "up"))
        self.assertIn('rm -f "$ROOT/deploy/dev/provisioner.env" "$ROOT/deploy/dev/staff-worker.env"', CARE_DEV)
        self.assertIn("--exclude=secrets/provisioner.env --exclude=secrets/staff-worker.env", DEPLOY)
        # the server gets the rotation it runs
        self.assertIn("scripts/staging.sh scripts/client-keys.sh", DEPLOY)

    def test_public_output_checked_before_it_is_written(self):
        write = function(LIB, "kt_change")
        self.assertIn('out="$(kt "$1" public)"', write)
        self.assertIn('python3 -c "$KEY_PUBLIC_CHECK"', write)
        self.assertLess(write.index("KEY_PUBLIC_CHECK"), write.index("mktemp"))
        self.assertIn('{"kty", "crv", "x", "y", "kid", "use", "alg"}', LIB)  # never "d"
        self.assertIn('("EC", "P-256", "sig", "ES256")', LIB)


class Up(unittest.TestCase):
    def test_order(self):
        up = function(STAGING_SH, "up")
        steps = [
            "compose up -d --wait db redis",
            "keys_ensure",
            "traefik keycloak",
            "compose run --rm --no-deps -T migrate",
            # the new backend before the sync (public clients ignore its assertions), the
            # old workers stopped across it (their secrets die there)
            "compose up -d --wait --no-deps backend",
            "compose stop provisioner staff-worker",
            "admin_manage ihr_keycloak_sync --realm --all",
            "celery-worker celery-beat frontend",
            "compose up -d --no-deps provisioner staff-worker",
            'rm -f "$SECRETS/provisioner.env"',
        ]
        at = [up.index(s) for s in steps]
        self.assertEqual(at, sorted(at))
        # backend, celery and the workers start without re-running migrate
        self.assertEqual(up.count("--no-deps"), 4)

    def test_dev_order(self):
        kc_up = CARE_DEV.split("    up)\n", 1)[1].split(";;", 1)[0]
        steps = ["keycloak mailpit", "keys_ensure", 'up -d --wait backend', "ihr_keycloak_sync --realm --all", "provisioner staff-worker"]
        at = [kc_up.index(s) for s in steps]
        self.assertEqual(at, sorted(at))


class Rotation(unittest.TestCase):
    rotate = function(LIB, "keys_rotate")

    def test_wait_covers_the_assertion_lifetime_and_skew(self):
        wait = int(re.search(r"^KEY_RETIRE_WAIT=(\d+)$", LIB, re.M).group(1))
        self.assertGreaterEqual(wait, 60 + 15)
        self.assertIn('sleep "$KEY_RETIRE_WAIT"', self.rotate)

    def test_steps_in_order(self):
        before, rest = self.rotate.split("\n  if ((compromised)); then", 1)
        normal = rest.split("\n  else\n", 1)[1]
        steps = ['kt_change "$p" stage\n', "keys_publish", "key_check_sync", '--kid "${new_kid[$p]}"',
                 'kt_change "$p" activate --kid "${new_kid[$p]}"\n', 'sleep "$KEY_RETIRE_WAIT"',
                 'kt_change "$p" retire --kid "${old_kid[$p]}"', "keys_publish", "key_check_sync",
                 "keys_withdraw_retired"]
        pos, text = 0, before + normal
        for step in steps:
            pos = text.index(step, pos) + 1
        withdraw = function(LIB, "keys_withdraw_retired")
        # the old key is deleted only once Keycloak refuses it (probe exit 1)
        self.assertLess(withdraw.index("((rc == 1))"), withdraw.index('kt_change "$p" delete --kid'))
        # public files rebuilt from the key state before anything is published
        self.assertLess(self.rotate.index("keys_refresh"), self.rotate.index("keys_publish"))
        self.assertIn("keys_refresh", function(LIB, "keys_finish"))
        # a run that stopped after its switch is finished, never followed by a second rotation
        self.assertLess(self.rotate.index("keys_finish"), self.rotate.index('kt_change "$p" stage'))
        self.assertIn('sleep "$KEY_RETIRE_WAIT"', function(LIB, "keys_finish"))

    def test_compromised(self):
        comp = self.rotate.split("\n  if ((compromised)); then", 1)[1].split("\n  else\n", 1)[0]
        self.assertNotIn("sleep", comp)
        self.assertIn('activate --kid "${new_kid[$p]}" --at-once', comp)  # the old key retired with it
        self.assertLess(comp.index("--at-once"), comp.index("keys_publish"))
        # sessions end before publishing: a sync that stops elsewhere never leaves them alive
        self.assertLess(comp.index("key_revoke_sessions"), comp.index("keys_publish"))
        # never a staged key left over in the possibly stolen folder; a marker until finished
        self.assertIn('kt_change "$p" stage --fresh', self.rotate)
        self.assertIn("reused", self.rotate)
        self.assertIn("has not finished: run keys rotate $p --compromised again", self.rotate)
        self.assertIn('rm -f "$(key_marker "$p")"', self.rotate)
        self.assertIn("revoke-sessions)", function(LIB, "keys_cmd"))
        self.assertIn(" backend ", comp)
        self.assertIn("ihr_client_keys revoke-sessions", function(STAGING_SH, "key_revoke_sessions"))

    def test_journal_kid_and_time_only(self):
        log = function(STAGING_SH, "key_log")
        self.assertIn("logger -t indiehr/keys", log)
        for call in re.findall(r'key_log "([^"]*)"', LIB):
            for var in re.findall(r"\$\{?(\w+)", call):
                self.assertIn(var, ("p", "new_kid", "old_kid", "kid"), call)

    def test_age_limits(self):
        # the verdict comes from the key tool's status (owner: rotate every 180 days)
        tool = (ROOT / "plugins" / "ihr_platform" / "ihr_platform" / "client_keys.py").read_text()
        self.assertIn("WARN_DAYS = 150", tool)
        self.assertIn("FAIL_DAYS = 365", tool)
        self.assertIn('kt "$1" status --json', LIB)
        smoke = function(STAGING_SH, "smoke")
        for s in ("keys_age_check || fail=1", "key_check_sync", "key_probe_in backend"):
            self.assertIn(s, smoke)

    def test_commands_wired(self):
        self.assertIn('keys) keys_cmd "${@:2}" ;;', STAGING_SH)
        self.assertIn('keys) keys_cmd "${@:2}" ;;', CARE_DEV)
        for name in ("kt", "key_publish_sync", "key_check_sync", "key_probe_in", "key_revoke_sessions", "key_log"):
            self.assertIn(f"\n{name}() {{", STAGING_SH)
            self.assertIn(f"\n{name}() {{", CARE_DEV)
        for text in (STAGING_SH, CARE_DEV):
            self.assertIn('keytool "$2" --purpose "$1" --dir "/run/ihr-keytool/$1" "${@:3}"', function(text, "kt"))


class DevTokenViewerRetired(unittest.TestCase):
    def test_gone(self):
        self.assertFalse((ROOT / "deploy" / "keycloak" / "devtools").exists())
        for text in (CARE_DEV, yaml.safe_dump(DEV), yaml.safe_dump(DEV_LOCAL)):
            self.assertNotIn("devtools", text)
            self.assertNotIn("8082", text)


if __name__ == "__main__":
    unittest.main()
