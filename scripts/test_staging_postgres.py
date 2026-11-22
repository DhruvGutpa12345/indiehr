"""Static and stubbed checks of the Postgres operating rules (P1-T23, Q-K92..Q-K95):

- the server settings of deploy/postgres/server-settings.txt in every database container
  (staging, dev, CI) and in both recovery starts of staging.sh (a copy that replays the live
  server's WAL refuses to start with a smaller max_locks_per_transaction);
- /dev/shm plus shared_buffers plus room for connections inside the container's memory limit
  (also with the 8 GB server's values); the database and Keycloak carry negative OOM scores;
- the process limits profile (IHR_DB_LIMITS) of every app service;
- the back-channel logout service: internal network only, no route, no key, no anchor key, the
  only target of IHR_BACKCHANNEL_LOGOUT_URL;
- db_server_check (staging.sh, run by up, release and db-roles --assert) run against a stubbed
  database: it fails on a small lock table, a small /dev/shm, a changed shared_buffers and a
  setting stored for the whole database.

Run: python3 -m unittest scripts/test_staging_postgres.py
"""

import re
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "deploy" / "staging"
SETTINGS_FILE = ROOT / "deploy" / "postgres" / "server-settings.txt"
STAGING_SH = (ROOT / "scripts" / "staging.sh").read_text()


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_multi_constructor("!", lambda loader, suffix, node: (
    loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode)
    else loader.construct_mapping(node) if isinstance(node, yaml.MappingNode)
    else loader.construct_scalar(node)))


def load(path):
    return yaml.load(Path(path).read_text(), Loader=_Loader)


BASE = load(STAGING / "compose.yaml")
DEV = load(ROOT / "deploy" / "dev" / "care-local.override.yaml")
CI = load(ROOT / "deploy" / "ci" / "tenancy.override.yaml")
SERVICES = BASE["services"]
ROUTES = (STAGING / "traefik" / "routes.yml").read_text()


def server_lines():
    return [
        line.strip()
        for line in SETTINGS_FILE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


STAGING_ONLY_MARKER = "# ---- staging and recovery only"


def shared_lines():
    """The lines every database container carries, dev's and CI's upstream postgres:17 too:
    those before the staging-only marker (the rest need our Postgres 18 image)."""
    head = SETTINGS_FILE.read_text().split(STAGING_ONLY_MARKER, 1)
    return [
        line.strip()
        for line in head[0].splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def pairs(command):
    """The ``-c name=value`` options of a Postgres command, as a set of name=value."""
    found = set()
    for first, second in zip(command, command[1:]):
        if first == "-c":
            found.add(second)
    return found


def function(name):
    return STAGING_SH.split(f"\n{name}() {{", 1)[1].split("\n}\n", 1)[0]


def to_bytes(text):
    m = re.fullmatch(r"(\d+)(m|g)", str(text).lower())
    return int(m[1]) * (1024**2 if m[2] == "m" else 1024**3)


class ServerSettings(unittest.TestCase):
    def test_the_file_is_what_the_design_says(self):
        self.assertEqual(
            server_lines(),
            ["max_locks_per_transaction=256", "effective_cache_size=3GB", "log_connections=authorization",
             "log_disconnections=on", "log_min_messages=warning", "log_destination=stderr"],
        )

    def test_every_database_container_carries_every_line(self):
        for name, command, lines in (
            ("staging", SERVICES["db"]["command"], server_lines()),
            ("dev", DEV["services"]["db"]["command"], shared_lines()),
            ("ci", CI["services"]["db"]["command"], shared_lines()),
        ):
            with self.subTest(environment=name):
                self.assertLessEqual(set(lines), pairs(command))

    def test_dev_and_ci_carry_no_line_of_our_own_postgres(self):
        """Review O1: dev and CI run upstream's postgres:17 (apps/backend/docker-compose.yaml),
        where log_connections is a boolean: `authorization` stops the server from starting.
        Lines after the staging-only marker are for our Postgres 18 image only."""
        upstream = load(ROOT / "apps" / "backend" / "docker-compose.yaml")["services"]["db"]["image"]
        self.assertRegex(upstream, r"^postgres:17")
        self.assertIn("log_connections=authorization", set(server_lines()) - set(shared_lines()))
        for name, command in (("dev", DEV["services"]["db"]["command"]), ("ci", CI["services"]["db"]["command"])):
            with self.subTest(environment=name):
                self.assertEqual(pairs(command) & (set(server_lines()) - set(shared_lines())), set())
                self.assertFalse(any(p.startswith("log_connections=") for p in pairs(command)))

    def test_both_recovery_starts_carry_them_too(self):
        """A copy that replays this server's WAL refuses a smaller lock table than the
        original's: the drill copy and the real restore take the file's lines."""
        self.assertIn("PG_SERVER_SETTINGS", STAGING_SH)
        starts = re.findall(r"^.*postgres -c archive_mode=[^\n]*\n[^\n]*$", STAGING_SH, re.M)
        self.assertEqual(len(starts), 2, starts)
        for start in starts:
            self.assertIn('"${PG_SERVER_SETTINGS[@]}"', start)
        block = "PG_SERVER_SETTINGS=()" + STAGING_SH.split("PG_SERVER_SETTINGS=()", 1)[1].split("unset _line", 1)[0] + "unset _line"
        out = subprocess.run(
            ["bash", "-c", f'ROOT="{ROOT}"\n{block}\nprintf "%s\\n" "${{PG_SERVER_SETTINGS[@]}}"'],
            capture_output=True, text=True, check=True)
        self.assertEqual(
            out.stdout.split(),
            [w for line in server_lines() for w in ("-c", line)],
        )

    # /dev/shm and shared_buffers are both shared memory charged to the db container's limit:
    # together with room for the connections they must fit in it (P1-T23 review B1). A full
    # container is OOM-killed (every connection reset); a smaller /dev/shm only gives
    # Postgres's clean "could not resize shared memory segment" error.
    CONNECTION_ROOM = to_bytes("512m")

    def test_shared_memory_and_buffers_leave_room_for_connections(self):
        db = SERVICES["db"]
        shm = re.search(r"\$\{IHR_PG_SHM:-([0-9a-z]+)\}", db["shm_size"])[1]
        limit = re.search(r"\$\{IHR_PG_MEMORY:-([0-9a-z]+)\}", db["mem_limit"])[1]
        buffers = re.search(r"shared_buffers=\$\{IHR_PG_SHARED_BUFFERS:-([0-9A-Za-z]+)\}", " ".join(db["command"]))[1]
        self.assertEqual(shm, "256m")
        self.assertLessEqual(
            to_bytes(shm) + to_bytes(buffers.lower().replace("mb", "m")) + self.CONNECTION_ROOM, to_bytes(limit))
        # the 8 GB server's values (IHR_PG_MEMORY 2304m, IHR_PG_SHARED_BUFFERS 768MB, same shm)
        self.assertLessEqual(to_bytes(shm) + to_bytes("768m") + self.CONNECTION_ROOM, to_bytes("2304m"))
        # the old default (1g with 512MB of buffers) filled the limit exactly
        self.assertGreater(to_bytes("1g") + to_bytes("512m") + self.CONNECTION_ROOM, to_bytes("1536m"))
        self.assertEqual(SERVICES["db"]["command"][SERVICES["db"]["command"].index("max_connections=100")], "max_connections=100")

    def test_the_kernel_never_picks_the_database_or_keycloak_before_a_sidecar(self):
        """The mem_limits of all services add up to more than 8 GB (B2): under host memory
        pressure the OOM killer takes the highest score, so the database and Keycloak carry
        negative ones (the database lower)."""
        db, kc = SERVICES["db"]["oom_score_adj"], SERVICES["keycloak"]["oom_score_adj"]
        self.assertLess(db, kc)
        self.assertLess(kc, 0)
        for name, svc in SERVICES.items():
            if name not in ("db", "keycloak"):
                self.assertGreaterEqual(svc.get("oom_score_adj", 0), 0, name)

    def test_dev_database_has_room_for_the_ceiling(self):
        db = DEV["services"]["db"]
        self.assertLess(to_bytes(db["shm_size"]), to_bytes(db["mem_limit"]) * 2)
        self.assertEqual(db["shm_size"], "256m")


class Runbook(unittest.TestCase):
    def test_the_keycloak_sync_is_not_sent_through_manage(self):
        """`staging.sh manage` runs the owner one-off without the Keycloak bootstrap client, so
        it cannot run ihr_keycloak_sync; `up` runs it as admin_manage (review B5)."""
        readme = (STAGING / "README.md").read_text()
        self.assertNotRegex(readme, r"staging\.sh manage[^\n]*ihr_keycloak_sync")
        self.assertIn("admin_manage ihr_keycloak_sync --realm --all", function("up"))


class ProcessProfiles(unittest.TestCase):
    WANT = {
        "backend": "web",
        "backchannel": "web",
        "celery-beat": "web",
        "celery-worker": "worker",
        "staff-worker": "worker",
        "migrate": "owner",
        "admin": "owner",
        "provisioner": "owner",
    }

    def test_every_app_service_names_its_profile(self):
        for name, profile in self.WANT.items():
            with self.subTest(service=name):
                self.assertEqual(SERVICES[name]["environment"]["IHR_DB_LIMITS"], profile)

    def test_an_owner_profile_only_where_the_owner_connects(self):
        for name, service in SERVICES.items():
            env = service.get("environment") or {}
            if env.get("IHR_DB_LIMITS") == "owner":
                self.assertEqual(env["IHR_DB_ROLE"], "owner", name)
            if env.get("IHR_DB_ROLE") == "owner":
                self.assertEqual(env.get("IHR_DB_LIMITS"), "owner", name)


class BackchannelService(unittest.TestCase):
    service = SERVICES["backchannel"]

    def test_it_is_a_small_internal_only_web_process(self):
        env = self.service["environment"]
        self.assertEqual(env["IHR_SERVICE"], "backchannel")
        self.assertEqual(env["IHR_DB_ROLE"], "app")
        self.assertEqual(self.service["networks"], ["internal"])
        self.assertEqual(self.service["mem_limit"], "256m")
        command = " ".join(self.service["command"])
        self.assertIn("--workers 1", command)
        self.assertIn("--threads 2", env["GUNICORN_CMD_ARGS"])
        self.assertIn("ihr_db_role_check --for backchannel &&", command)

    def test_it_holds_no_key_and_no_volume(self):
        self.assertNotIn("volumes", self.service)
        self.assertNotIn("IHR_CLIENT_KEY_PURPOSE", self.service["environment"])
        files = self.service.get("env_file", ["secrets/app.env"])
        self.assertEqual(files, ["secrets/app.env"])
        self.assertNotIn("ports", self.service)

    def test_no_route_reaches_it(self):
        self.assertNotIn("backchannel", ROUTES)

    def test_keycloak_is_told_to_post_to_it_and_to_nothing_else(self):
        env = BASE["x-app"]["environment"]
        self.assertEqual(
            env["IHR_BACKCHANNEL_LOGOUT_URL"],
            "http://backchannel:9000/api/ihr/backchannel-logout/",
        )
        self.assertIn('"backchannel"', env["DJANGO_ALLOWED_HOSTS"])
        migrate = " ".join(SERVICES["migrate"]["command"])
        self.assertIn("--domain backchannel", migrate)
        text = (STAGING / "compose.yaml").read_text()
        self.assertEqual(text.count("IHR_BACKCHANNEL_LOGOUT_URL:"), 1)
        self.assertNotIn("backend:9000/api/ihr/backchannel", text)

    def test_the_role_check_and_db_move_know_the_service(self):
        self.assertIn("backchannel", re.search(r"DB_APP_SERVICES=\(([^)]*)\)", STAGING_SH)[1].split())


FAKE = r'''
set -u
die() { echo "DIE: $*" >&2; exit 1; }
env_get() { case "$1" in IHR_PG_SHARED_BUFFERS) echo "${FAKE_SB:-}" ;; IHR_PG_SHM) echo "${FAKE_SHM:-}" ;; esac; }
pg_su() { cat <<OUT
max_locks_per_transaction=${FAKE_LOCKS:-256}
effective_cache_size=${FAKE_CACHE:-3GB}
shared_buffers=${FAKE_BUFFERS:-512MB}
log_connections=${FAKE_LOG_CONNECTIONS:-authorization}
log_disconnections=on
log_min_messages=warning
log_destination=stderr
stored_database_settings=${FAKE_STORED:-0}
OUT
}
app_exec() { printf 'Filesystem 1024-blocks Used Available Capacity Mounted\ntmpfs %s 0 %s 0%% /dev/shm\n' "${FAKE_SHM_KB:-262144}" "${FAKE_SHM_KB:-262144}"; }
PG_SERVER_SETTINGS=(-c max_locks_per_transaction=256 -c effective_cache_size=3GB -c log_connections=authorization
  -c log_disconnections=on -c log_min_messages=warning -c log_destination=stderr)
'''


def run_check(**env):
    mode = env.pop("WARN", "")
    script = FAKE + function("to_kb").join(["to_kb() {", "\n}\n"]) + function("db_server_check").join(
        ["db_server_check() {", "\n}\n"]) + f"\ndb_server_check {mode}\n"
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", **env})


class ServerCheck(unittest.TestCase):
    def test_a_good_server_passes(self):
        out = run_check()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Database server settings ok", out.stdout)

    def test_each_drift_is_named_and_refused(self):
        for env, text in (
            ({"FAKE_LOCKS": "64"}, "max_locks_per_transaction is 64"),
            ({"FAKE_CACHE": "4GB"}, "effective_cache_size is 4GB"),
            ({"FAKE_BUFFERS": "128MB"}, "shared_buffers is 128MB"),
            ({"FAKE_STORED": "1"}, "stored for the whole database"),
            ({"FAKE_SHM_KB": "65536"}, "/dev/shm is 64 MiB"),
            ({"FAKE_LOG_CONNECTIONS": "off"}, "log_connections is off"),
        ):
            with self.subTest(env=env):
                out = run_check(**env)
                self.assertNotEqual(out.returncode, 0)
                self.assertIn(text, out.stderr)

    def test_a_larger_lock_table_and_a_set_size_are_accepted(self):
        self.assertEqual(run_check(FAKE_LOCKS="512").returncode, 0)
        self.assertEqual(run_check(FAKE_SB="768MB", FAKE_BUFFERS="768MB").returncode, 0)
        small = run_check(FAKE_SHM="256m", FAKE_SHM_KB="262144")
        self.assertEqual(small.returncode, 0, small.stderr)

    def test_it_runs_at_every_up_release_and_assert(self):
        self.assertIn("split) db_roles_apply; db_server_check ;;", function("up"))
        self.assertIn("db_roles_apply\n      db_server_check", function("db_roles_cmd"))
        release = function("release")
        self.assertIn("  db_server_check\n  # 7. The new build", release)

    def test_a_release_recreates_the_database_before_the_check_and_warns_before_the_hold(self):
        """On the serving path the db container is never recreated by anything else, so a build
        that changes a database setting (A1) must not die mid-window with every hospital held:
        the window recreates the db alone (a no-op when nothing changed) before the check, and the
        read-only check before the announcement only warns."""
        release = function("release")
        self.assertLess(release.index("compose up -d --wait --no-deps db\n  db_server_check\n"),
                        release.index("# 7. The new build"))
        self.assertLess(release.index("db_server_check --warn"), release.index("old_manage ihr_fleet_run window"))
        self.assertLess(release.index("db_server_check --warn"), release.index("REL_OP=\"$op\""))

    def test_warn_mode_names_the_drift_and_does_not_fail(self):
        out = run_check(FAKE_LOCKS="64", WARN="--warn")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("max_locks_per_transaction is 64", out.stderr)
        self.assertIn("the release recreates it", out.stderr)
        self.assertEqual(run_check(FAKE_LOCKS="64").returncode, 1)

    def test_the_release_audit_check_runs_as_a_worker(self):
        """Compose gives every service's one-off the web limits; the audit check's per-window
        statements can be long after a hold or a restore (A2)."""
        self.assertIn("-e IHR_DB_LIMITS=worker backend timeout", function("rel_audit_check"))


if __name__ == "__main__":
    unittest.main()
