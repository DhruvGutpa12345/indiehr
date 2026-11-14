"""Static checks of the split database roles on staging (P1-T19b; docs/reviews/p1-t19/design.md
items 9-12 and the known limits): the secrets (db-split-secrets, init), compose (who holds the
owner's address, who declares which role, each container's pre-start check), the first start of an
empty cluster (initdb), the re-assertion at every up, the move under a full stop (db-roles
--migrate and inside release), the restore drill, deploy.sh, and the settings that make required the
deployment default. db-split-secrets and the SCRAM verifiers run for real (no Docker).

The live proof is the local staging proof (deploy/staging/README.md, Moving to split roles).
Run: python3 -m unittest scripts/test_staging_db_roles.py
"""

import base64
import hashlib
import hmac
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "deploy" / "staging"
STAGING_SH = (ROOT / "scripts" / "staging.sh").read_text()
DEPLOY_SH = (ROOT / "scripts" / "deploy.sh").read_text()
COMPOSE = yaml.safe_load((STAGING / "compose.yaml").read_text())
SERVICES = COMPOSE["services"]
INITDB = (STAGING / "initdb" / "10-roles.sh").read_text()
PLATFORM = ROOT / "plugins" / "ihr_platform" / "ihr_platform"
DB_ROLES_PY = (PLATFORM / "db_roles.py").read_text()
README = (STAGING / "README.md").read_text()

APP_SERVICES = ("backend", "celery-worker", "celery-beat", "staff-worker")
OWNER_SERVICES = ("migrate", "admin", "provisioner")
CHECKED = {  # the service and the process its pre-start check guards (ihr_db_role_check --for)
    "migrate": "migrate",
    "backend": "backend",
    "celery-worker": "celery-worker",
    "celery-beat": "celery-beat",
    "provisioner": "provisioner",
    "staff-worker": "staff-worker",
}


def func(name):
    return STAGING_SH.split(f"\n{name}() {{", 1)[1].split("\n}\n", 1)[0]


def shell_var(name):
    """A single-quoted shell variable of staging.sh, as bash sees it."""
    out = subprocess.run(
        ["bash", "-c", f"source <(sed -n '/^{name}=/,/;'\"'\"'$/p' \"$1\"); printf '%s' \"${name}\"",
         "_", str(ROOT / "scripts" / "staging.sh")],
        capture_output=True, text=True, check=True)
    return out.stdout


def command_text(service):
    cmd = SERVICES[service]["command"]
    return " ".join(cmd) if isinstance(cmd, list) else cmd


class Compose(unittest.TestCase):
    def test_every_app_container_is_required_and_declares_its_role(self):
        for name in (*APP_SERVICES, *OWNER_SERVICES):
            with self.subTest(service=name):
                env = SERVICES[name]["environment"]
                self.assertEqual(env["IHR_DB_ROLES"], "required")
                self.assertEqual(env["IHR_DB_ROLE"], "owner" if name in OWNER_SERVICES else "app")

    def test_only_the_owners_three_hold_the_owners_address(self):
        """design item 9: db-owner.env is mounted only into migrate, admin and the provisioner,
        after app.env (the later file wins), so their DATABASE_URL is the owner's."""
        for name, svc in SERVICES.items():
            files = svc.get("env_file") or []
            with self.subTest(service=name):
                if name in OWNER_SERVICES:
                    self.assertEqual(files[-1], "secrets/db-owner.env")
                    self.assertLess(files.index("secrets/app.env"), files.index("secrets/db-owner.env"))
                else:
                    self.assertNotIn("secrets/db-owner.env", files)
        # never the web app or the staff worker (they hold the backend's and the staff client keys)
        for name in ("backend", "staff-worker"):
            self.assertNotEqual(SERVICES[name]["environment"].get("IHR_DB_ROLE"), "owner")

    def test_each_runtime_container_checks_its_role_before_it_starts(self):
        """design known limit 6 (C6, S5): --for names the process the container runs next; the
        migrate one-shot checks the roles before anything is migrated."""
        for name, process in CHECKED.items():
            with self.subTest(service=name):
                cmd = command_text(name)
                check = f"python manage.py ihr_db_role_check --for {process} &&"
                self.assertIn(check, cmd)
                self.assertEqual(cmd.count("ihr_db_role_check"), 1)
                # before the process itself (and, for migrate, before migrate_schemas)
                rest = cmd.split(check, 1)[1]
                self.assertRegex(rest, r"(collectstatic|exec celery|touch /tmp/healthy|migrate_schemas|exec python manage.py ihr_provision_worker)")
        self.assertIn("ihr_provision_worker --role onboarding", command_text("provisioner"))
        self.assertIn("ihr_provision_worker --role staff", command_text("staff-worker"))
        # the processes named are the ones the command knows
        processes = re.search(r"PROCESSES = \((.*?)\n\)", DB_ROLES_PY, re.S).group(1)
        for process in CHECKED.values():
            self.assertIn(process, processes + ' "migrate", "admin", "provisioner"')

    def test_no_provider_named(self):
        text = (STAGING / "compose.yaml").read_text().lower()
        for provider in ("e2e", "aws", "amazon", "azure", "gcp", "digitalocean"):
            self.assertNotIn(provider, text)


class Secrets(unittest.TestCase):
    def test_init_writes_the_split_roles(self):
        init = func("init")
        self.assertNotIn("IHR_DB_PASSWORD", init)
        self.assertIn("DATABASE_URL=postgres://ihr_app:$app@db:5432/ihr", init)
        self.assertIn("DATABASE_URL=postgres://ihr_owner:$owner@db:5432/ihr", init)
        self.assertIn("write_secret db-owner.env", init)
        self.assertNotIn("postgres://ihr:", init)

    def test_secrets_stay_out_of_git(self):
        ignored = (ROOT / ".gitignore").read_text()
        self.assertIn("deploy/staging/secrets/", ignored)

    def run_split(self, app_env, db_env="POSTGRES_PASSWORD=pg\nIHR_DB_PASSWORD=old\nKC_DB_PASSWORD=kc\n",
                  owner_env=None):
        d = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (d / ".env").write_text("IHR_TENANT_DOMAIN_SUFFIX=app.example.in\n")
        sec = d / "secrets"
        sec.mkdir(mode=0o700)
        (sec / "app.env").write_text(app_env)
        (sec / "db.env").write_text(db_env)
        if owner_env is not None:
            (sec / "db-owner.env").write_text(owner_env)
        bindir = d / "bin"
        bindir.mkdir()
        fake = bindir / "docker"
        fake.write_text("#!/bin/sh\necho \"docker must not run: $*\" >&2\nexit 9\n")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        env = {**os.environ, "IHR_STAGING_DIR": str(d), "PATH": f"{bindir}:{os.environ['PATH']}"}
        out = subprocess.run(["bash", str(ROOT / "scripts" / "staging.sh"), "db-split-secrets"], env=env,
                             capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        return out, sec

    def test_split_makes_new_passwords_and_drops_the_old_one(self):
        """design item 9: a new owner password (never the old shared one), an app password, the
        owner's address in its own file, the old IHR_DB_PASSWORD gone; mode 600; nothing
        printed but file names; run again, nothing changes."""
        legacy = "DJANGO_SECRET_KEY=k\nDATABASE_URL=postgres://ihr:oldsharedpassword@db:5432/ihr\nREDIS_URL=redis://:r@redis:6379/0\n"
        out, sec = self.run_split(legacy)
        self.assertEqual(out.returncode, 0, out.stderr)
        app = (sec / "app.env").read_text()
        owner = (sec / "db-owner.env").read_text()
        watch = (sec / "audit-watch.env").read_text()
        app_pw = re.search(r"^DATABASE_URL=postgres://ihr_app:([A-Za-z0-9_-]{43})@db:5432/ihr$", app, re.M).group(1)
        owner_pw = re.search(r"^DATABASE_URL=postgres://ihr_owner:([A-Za-z0-9_-]{43})@db:5432/ihr$", owner, re.M).group(1)
        # the audit watch's own read-only role (P1-T21), its address alone in its own file
        watch_pw = re.search(r"^DATABASE_URL=postgres://ihr_audit_watch:([A-Za-z0-9_-]{43})@db:5432/ihr$", watch, re.M).group(1)
        self.assertEqual(watch.count("DATABASE_URL="), 1)
        self.assertEqual(len({app_pw, owner_pw, watch_pw}), 3)
        self.assertNotIn("oldsharedpassword", app + owner)
        self.assertIn("DJANGO_SECRET_KEY=k\n", app)
        self.assertIn("REDIS_URL=redis://:r@redis:6379/0\n", app)
        self.assertEqual(app.count("DATABASE_URL="), 1)
        self.assertNotIn("IHR_DB_PASSWORD", (sec / "db.env").read_text())
        self.assertIn("KC_DB_PASSWORD=kc", (sec / "db.env").read_text())
        for f in ("app.env", "db-owner.env", "db.env", "audit-watch.env"):
            self.assertEqual(stat.S_IMODE((sec / f).stat().st_mode), 0o600, f)
        for pw in (app_pw, owner_pw, watch_pw, "oldsharedpassword"):
            self.assertNotIn(pw, out.stdout + out.stderr)
        self.assertIn("password manager", out.stdout)
        self.assertEqual(sorted(p.name for p in sec.iterdir()), ["app.env", "audit-watch.env", "db-owner.env", "db.env"])
        # idempotent: a second run keeps every secret
        again = subprocess.run(["bash", str(ROOT / "scripts" / "staging.sh"), "db-split-secrets"],
                               env={**os.environ, "IHR_STAGING_DIR": str(sec.parent)},
                               capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("split already: nothing changed", again.stdout)
        self.assertEqual((sec / "app.env").read_text(), app)
        self.assertEqual((sec / "db-owner.env").read_text(), owner)
        self.assertEqual((sec / "audit-watch.env").read_text(), watch)

    def test_check_needs_all_three_addresses(self):
        """P1-T21 review C13: a folder split before the audit watch (app.env and db-owner.env,
        no audit-watch.env) is not split for this build: --check fails, names files only, and
        changes nothing; after db-split-secrets it passes."""
        owner_env = f"DATABASE_URL=postgres://ihr_owner:{'o' * 43}@db:5432/ihr\n"
        app_env = f"DATABASE_URL=postgres://ihr_app:{'a' * 43}@db:5432/ihr\n"
        out, sec = self.run_split(app_env, db_env="POSTGRES_PASSWORD=pg\n", owner_env=owner_env)
        self.assertEqual(out.returncode, 0, out.stderr)
        (sec / "audit-watch.env").unlink()  # as made before P1-T21
        env = {**os.environ, "IHR_STAGING_DIR": str(sec.parent)}
        check = subprocess.run(["bash", str(ROOT / "scripts" / "staging.sh"), "db-split-secrets", "--check"], env=env,
                               capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(check.returncode, 1, check.stdout + check.stderr)
        self.assertIn("audit-watch.env", check.stderr)
        for secret in ("o" * 43, "a" * 43):
            self.assertNotIn(secret, check.stdout + check.stderr)
        self.assertFalse((sec / "audit-watch.env").exists())  # --check changes nothing
        subprocess.run(["bash", str(ROOT / "scripts" / "staging.sh"), "db-split-secrets"], env=env, check=True,
                       capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        again = subprocess.run(["bash", str(ROOT / "scripts" / "staging.sh"), "db-split-secrets", "--check"], env=env,
                               capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(again.returncode, 0, again.stderr)

    def test_an_interrupted_split_is_finished_with_the_secret_already_made(self):
        owner_env = f"DATABASE_URL=postgres://ihr_owner:{'o' * 43}@db:5432/ihr\n"
        out, sec = self.run_split("DATABASE_URL=postgres://ihr:old@db:5432/ihr\n", owner_env=owner_env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual((sec / "db-owner.env").read_text(), owner_env)  # kept, never replaced
        self.assertRegex((sec / "app.env").read_text(), r"postgres://ihr_app:[A-Za-z0-9_-]{43}@")

    def test_never_overwrites_what_it_does_not_recognise(self):
        for app_env, owner_env, why in (
            ("DATABASE_URL=postgres://someone:x@elsewhere:5432/ihr\n", None, "neither the single role's"),
            ("DATABASE_URL=postgres://ihr:old@db:5432/ihr\n", "DATABASE_URL=postgres://x:y@db/ihr\n", "holds no owner address"),
        ):
            with self.subTest(why=why):
                out, sec = self.run_split(app_env, owner_env=owner_env)
                self.assertEqual(out.returncode, 1)
                self.assertIn(why, out.stderr)
                self.assertEqual((sec / "app.env").read_text(), app_env)
                self.assertIn("IHR_DB_PASSWORD=old", (sec / "db.env").read_text())

    def test_deploy_needs_the_split_secrets_and_a_release_for_the_move(self):
        # P1-T21 review C13: the deploying machine checks what the server will (the app's, the
        # owner's and the audit watch's addresses), before anything is written or sent
        self.assertIn('IHR_STAGING_OUT="$ENV_DIR" scripts/staging.sh db-split-secrets --check', DEPLOY_SH)
        self.assertNotIn('[[ -f "$ENV_DIR/secrets/db-owner.env" ]]', DEPLOY_SH)
        self.assertLess(DEPLOY_SH.index("db-split-secrets --check"), DEPLOY_SH.index('roles_split="$(remote'))
        self.assertIn("scripts/staging.sh db-split-secrets", DEPLOY_SH)
        block = DEPLOY_SH[DEPLOY_SH.index('roles_split="$(remote'):DEPLOY_SH.index("needs-release")]
        self.assertIn('[[ "$db_created" == 1 && "$roles_split" != 1 ]]', block)
        self.assertIn("deploy with --release", block)


def scram_verify(verifier, password):
    m = re.fullmatch(r"SCRAM-SHA-256\$(\d+):([A-Za-z0-9+/=]+)\$([A-Za-z0-9+/=]+):([A-Za-z0-9+/=]+)", verifier)
    rounds, salt, stored, server = int(m[1]), base64.b64decode(m[2]), base64.b64decode(m[3]), base64.b64decode(m[4])
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    client = hmac.new(salted, b"Client Key", "sha256").digest()
    return (rounds, len(salt), hashlib.sha256(client).digest() == stored,
            hmac.new(salted, b"Server Key", "sha256").digest() == server)


class Roles(unittest.TestCase):
    def test_the_verifier_is_postgres_scram(self):
        """The passwords reach Postgres only as SCRAM-SHA-256 verifiers made here (never the
        password: not in its logs or pg_stat_activity), the password on stdin."""
        pw = "Ab3_-" * 9
        out = subprocess.run(["bash", "-c", 'source <(sed -n "/^scram() {/,/^}/p" "$1"); scram <<< "$2"', "_",
                              str(ROOT / "scripts" / "staging.sh"), pw], capture_output=True, text=True, check=True)
        verifier = out.stdout.strip()
        self.assertEqual(scram_verify(verifier, pw), (4096, 16, True, True))
        self.assertNotIn(pw, verifier)
        apply = func("db_roles_apply")
        self.assertIn('vo="$(scram <<< "$owner")"', apply)
        # one transaction, SQL on stdin, in the database ihr: a function's privileges are each
        # database's own, so the large-object revoke run in `postgres` left ihr's open (found by
        # the local proof's `ihr_db_role_check --for migrate`)
        self.assertIn("pg_su -1 -q -d ihr >/dev/null", apply)
        self.assertNotRegex(apply, r"-v v[oa]=|-c .*PASSWORD")
        self.assertIn("docker exec -i \"$PG_TARGET\" psql -U postgres", func("pg_su"))

    def test_every_role_as_the_model_has_it(self):
        """Attributes match db_roles.ROLES (the app NOINHERIT, the dump role INHERIT and in
        pg_read_all_data only), no stored settings anywhere (known limit 1: in every database,
        test_stored_settings_are_reset_in_every_database), memberships revoked whoever granted
        them, large objects closed (known limit 3)."""
        sql = shell_var("DB_ROLES_SQL")
        roles = {"ihr_owner": "INHERIT", "ihr_app": "NOINHERIT", "ihr_audit_watch": "NOINHERIT", "ihr_dump": "INHERIT"}
        for role, inherit in roles.items():
            with self.subTest(role=role):
                line = re.search(rf"^ALTER ROLE {role} WITH (.*);$", sql, re.M).group(1).split()
                self.assertIn(inherit, line)
                for attr in ("NOSUPERUSER", "NOCREATEDB", "NOCREATEROLE", "NOREPLICATION", "NOBYPASSRLS"):
                    self.assertIn(attr, line)
        self.assertRegex(sql, r"ALTER ROLE ihr_owner WITH LOGIN .*PASSWORD :'vo';")
        self.assertRegex(sql, r"ALTER ROLE ihr_app WITH LOGIN .*PASSWORD :'va';")
        # the audit watch logs in (P1-T21), with few connections; no service logs in as the dump
        # role yet (P1-T22): no login, no password
        self.assertRegex(sql, r"ALTER ROLE ihr_audit_watch WITH LOGIN .*CONNECTION LIMIT 4 PASSWORD :'vw';")
        self.assertRegex(sql, r"ALTER ROLE ihr_dump WITH NOLOGIN .*PASSWORD NULL;")
        self.assertIn("REVOKE %I FROM %I GRANTED BY %I CASCADE", sql)
        self.assertIn("GRANT pg_read_all_data TO ihr_dump", sql)
        # the model's own inherit flags (db_roles.ROLES)
        self.assertIn("APP: Role(APP, inherit=False)", DB_ROLES_PY)
        self.assertIn('DUMP: Role(DUMP, inherit=True, memberships=("pg_read_all_data",)', DB_ROLES_PY)
        # the large-object creators are the ones the required-mode check looks for
        creators = re.search(r"LARGE_OBJECT_CREATORS = \((.*?)\)\n", DB_ROLES_PY, re.S).group(1)
        revoke = re.search(r"REVOKE EXECUTE ON FUNCTION (.*?) FROM PUBLIC;", sql, re.S).group(1)
        self.assertEqual(sorted(re.findall(r'"([^"]+)"', creators)), sorted(re.findall(r"lo_\w+\([^)]*\)", revoke)))
        # no grant here: ensure_grants (the owner) is the authority
        self.assertNotRegex(sql, r"GRANT (SELECT|INSERT|UPDATE|DELETE|CONNECT|USAGE)")

    def test_stored_settings_are_reset_in_every_database(self):
        """Review S1/C3: a role may ALTER ROLE itself IN DATABASE any database (postgres,
        template1, keycloak: even one it cannot connect to), and the run-time and migrate checks
        count every row of pg_db_role_setting. So every up and db-roles --assert reset every
        row of the four roles, whatever its database, and fail if one is left; never only the
        role-wide row and the one in ihr (which left the refusal standing after the runbook).
        Live proof: a throwaway Postgres 18.6 with db_roles_apply itself (design.md, review
        fixes)."""
        sql = shell_var("DB_ROLES_SQL")
        self.assertNotRegex(sql, r"IN DATABASE \w+ RESET ALL")  # no fixed database
        self.assertNotRegex(sql, r"(?m)^ALTER ROLE ihr_\w+ RESET ALL;$")
        loop = sql[sql.index("FROM pg_db_role_setting x"):]
        loop = loop[:loop.index("END LOOP;")]
        self.assertIn("LEFT JOIN pg_database d ON d.oid = x.setdatabase", loop)  # 0: role-wide
        self.assertIn("WHERE r.rolname IN ('ihr_owner', 'ihr_app', 'ihr_audit_watch', 'ihr_dump')", loop)
        self.assertNotIn("datname = ", loop)  # no database left out
        self.assertIn("EXECUTE format('ALTER ROLE %I RESET ALL', s.rolname);", loop)
        self.assertIn("EXECUTE format('ALTER ROLE %I IN DATABASE %I RESET ALL', s.rolname, s.datname);", loop)
        after = sql[sql.index("END LOOP;"):]
        self.assertIn("RAISE EXCEPTION 'stored settings of the database roles are left after the reset'",
                      after[:after.index("END $$;")])
        # the checks count every database and name them; the test cluster's set-up resets each
        self.assertNotRegex(DB_ROLES_PY, r"pg_db_role_setting s WHERE s\.setrole = r\.oid\)")
        self.assertEqual(DB_ROLES_PY.count("{_STORED_IN}") + DB_ROLES_PY.count("+ _STORED_IN"), 2)
        self.assertIn('"ALTER ROLE {} IN DATABASE {} RESET ALL"', DB_ROLES_PY)
        # the runbook and alert say so
        self.assertIn("**in every database**", README)
        self.assertNotIn("IN DATABASE ihr RESET ALL", README)
        self.assertIn("stored settings in every database",
                      (ROOT / "deploy" / "logs" / "alerts" / "db-roles.yml").read_text())

    def test_the_move_renames_and_refuses_twice(self):
        move = shell_var("DB_ROLES_MOVE_SQL")
        self.assertIn("ALTER ROLE ihr RENAME TO ihr_owner;", move)
        self.assertIn("role ihr_owner exists already", move)
        self.assertIn("not owned by the role ihr", move)
        self.assertLess(move.index("pg_terminate_backend"), move.index("RENAME"))
        apply = func("db_roles_apply")
        self.assertLess(apply.index("DB_ROLES_MOVE_SQL"), apply.index("DB_ROLES_SQL"))

    def test_initdb_makes_the_owner_and_no_shared_role(self):
        self.assertIn("CREATE ROLE ihr_owner LOGIN;", INITDB)  # its password: up, before any login
        self.assertIn("CREATE DATABASE ihr OWNER ihr_owner;", INITDB)
        self.assertNotIn("IHR_DB_PASSWORD", INITDB)
        self.assertNotRegex(INITDB, r"CREATE ROLE ihr\b(?!_)")
        self.assertIn("REVOKE ALL ON DATABASE ihr FROM PUBLIC;", INITDB)
        self.assertNotIn("ihr_app", INITDB.split("SQL", 1)[1])  # made by up, granted by ensure_grants

    def test_every_up_re_asserts_and_never_moves(self):
        """known limit 1: every up re-asserts both passwords and resets every stored setting,
        as postgres, after the database is up and before anything connects; a database of the
        single role is refused (the move runs only under a full stop: design item 10)."""
        up = func("up")
        self.assertLess(up.index("db_need_split_secrets"), up.index("compose up -d --wait db redis"))
        # a running database of the single role is refused before db is recreated (its settings
        # changed with the split: recreating it would cut the old stack off)
        self.assertLess(up.index('== single ]]; then'), up.index("compose up -d --wait db redis"))
        at = up.index("split) db_roles_apply ;;")
        self.assertLess(up.index("compose up -d --wait db redis"), at)
        self.assertLess(at, up.index("compose run --rm --no-deps -T migrate"))
        self.assertIn("single) die", up)
        self.assertNotIn("--move", up)

    def test_the_move_runs_under_a_full_stop(self):
        stopped = func("db_roles_move_stopped")
        self.assertLess(stopped.index('compose stop "${DB_APP_SERVICES[@]}"'), stopped.index("db_roles_apply --move"))
        self.assertIn("DB_APP_SERVICES=(backend frontend celery-worker celery-beat provisioner staff-worker)", STAGING_SH)
        cmd = func("db_roles_cmd")
        migrate = cmd[cmd.index("    --migrate)\n"):]
        steps = ["db_need_split_secrets", "rel_lock", 'die "a release is in progress',
                 'running_app_services "${DB_APP_SERVICES[@]}"', '|| die "could not tell whether any app container runs',
                 'rlog state "$REL_LOG"',
                 'rel_logged_backend "$top"', 'image_id "$(env_get IHR_BACKEND_IMAGE)"',
                 "trap 'db_move_exit $?' EXIT", "compose up -d --wait db redis", "db_roles_state",
                 "db_roles_move_stopped", "ihr_db_role_check --for migrate",
                 "migrate_schemas --shared", "ihr_fleet_run migrate --operator", "\n      up\n"]
        at = [migrate.index(st) for st in steps]
        self.assertEqual(at, sorted(at), steps)
        # read fail-closed (verifier item 3): a compose ps that fails is never "none run"
        running = func("running_app_services")
        self.assertIn("|| return 1", running)
        self.assertNotIn("|| true", running)
        self.assertNotIn("2>/dev/null", running)

    def test_migrate_moves_only_a_stopped_stack_on_the_released_build(self):
        """Review C2: db-roles --migrate refuses, before anything changes (the trap, the database
        start, the move), while any app container runs (on a running server the move belongs to
        release: hold, seal, restore point, log) and unless this stack's backend image is the
        build of the release logged last (else it would migrate a build never released, outside
        the window, with no restore point). It holds nothing itself any more, and db-split-secrets
        no longer points a server at it. End-to-end refusals: test_release_scripts.py."""
        cmd = func("db_roles_cmd")
        migrate = cmd[cmd.index("    --migrate)\n"):cmd.index("\n    *) die")]
        first_change = migrate.index("trap 'db_move_exit $?' EXIT")
        for check in ('[[ -z "${apps// /}" ]] || die', '[[ -n "$top" ]] || die', '|| die "$(env_get IHR_BACKEND_IMAGE) is not'):
            self.assertLess(migrate.index(check), first_change, check)
        self.assertNotIn("ihr_fleet_run hold", migrate)
        self.assertNotIn("old_manage", migrate)
        self.assertNotIn("db-roles --migrate", func("db_split_secrets"))
        logged = func("rel_logged_backend")
        self.assertIn('"kind=release"', logged)
        self.assertIn('substr($i, 9)', logged)  # backend=<id>
        self.assertIn("only on a stopped stack whose backend image is the build of the release logged last", README)
        self.assertNotIn("(same build)", README)

    def test_no_recovery_advice_needs_the_split_roles_before_the_move(self):
        """Review C1/S2: before the move (the database still single) `up`, every rollback and
        the owner's migrate-container one-offs refuse or cannot log in, so no advice names them:
        db-roles --migrate's own stop says run it again; a release's stage 1/2 advice names
        db-roles --serve-previous (end to end: test_release_scripts.py)."""
        exit_ = func("db_move_exit")
        before = exit_[exit_.index("    *) echo"):]
        self.assertIn("run scripts/staging.sh db-roles --migrate again", before)
        for wrong in ("staging.sh up", "fleet release", "rollback"):
            self.assertNotIn(wrong, before)
        self.assertNotIn("held)", exit_)  # it never holds (review C2)
        serve = func("db_serve_previous")
        steps = ["rel_lock", "db_roles_state", '== single ]] || die', 'existing_containers "$s"',
                 "start_existing backend", "old_manage ihr_fleet_run release --operator",
                 "old_manage ihr_fleet_run window --clear --operator", 'start_existing "${rest[@]}"']
        at = [serve.index(st) for st in steps]
        self.assertEqual(at, sorted(at), steps)
        self.assertNotRegex(serve, r"\n\s*up\b|compose up|compose run")
        # never Compose's start (verifier item 1): it starts the migrate dependency, which has no
        # container on this stack, and fails; docker start of each service's own containers
        code = "\n".join(ln for ln in (serve + func("start_existing")).splitlines() if not ln.lstrip().startswith("#"))
        self.assertNotIn("compose start", code.replace("(docker compose start db)", ""))  # db: no dependency
        self.assertIn('docker start "${ids[@]}"', func("start_existing"))
        self.assertIn('"com.docker.compose.oneoff"', func("existing_containers"))
        self.assertIn('|| die "could not list the containers of $s', serve)
        guard = func("rel_roles_guard")
        self.assertLess(guard.index("if rel_db_single; then"), guard.index('rel_target_presplit "$1"'))
        advice = func("rel_rollback_advice")
        self.assertLess(advice.index('"$state" == single'), advice.index("rlog plan"))
        self.assertEqual(advice.count("rel_target_presplit"), 2)

    def test_a_release_moves_once_after_its_restore_point(self):
        """design item 10, inside P1-T18b's release: held, workers stopped, drained, sealed by
        the outgoing build (with its own address), restore point, then the move, then the roles
        checked and the migrations (the grants converged as the owner)."""
        body = func("release")
        steps = ["db_need_split_secrets", 'if ! old_manage ihr_fleet_run hold', "rel_move_pending",
                 'compose stop "${REL_WORKERS[@]}"', 'seal="$(rel_seal "$old_id")"', 'rel_restore_point "$tag"',
                 "if ((REL_MOVE)); then", "db_roles_move_stopped", "  else\n    # The roles as the model has them", "    db_roles_apply\n",
                 "ihr_db_role_check --for migrate",
                 "migrate_schemas --shared", "ihr_fleet_run migrate check roles packs", "\n  up\n"]
        at = [body.index(st) for st in steps]
        self.assertEqual(at, sorted(at), steps)
        # the stack not serving: the database starts, then the same question
        idle = body[body.index("The stack is not serving"):body.index("# 6. The restore point")]
        self.assertLess(idle.index("compose up -d --wait db redis"), idle.index("rel_move_pending"))
        seal = func("rel_seal")
        self.assertIn('url="$(outgoing_database_url)"', seal)
        self.assertIn("envs=(-e DATABASE_URL -e IHR_DB_ROLES -e IHR_DB_ROLE)", seal)

    def test_the_owners_commands_of_the_running_build_run_as_the_owner(self):
        """After the move the backend runs as ihr_app, which may not write the release's
        records (hold, window): those run in a one-off of the running backend's image in the
        owner's logged migrate container (never the admin one)."""
        old = func("old_manage")
        self.assertIn("if backend_runs_as_app; then", old)
        self.assertIn('IHR_BACKEND_IMAGE="$repo" compose run --rm --no-deps -T migrate python manage.py "$@"', old)
        self.assertIn("grep -qx 'IHR_DB_ROLE=app'", func("backend_runs_as_app"))

    def test_the_restore_drill_runs_as_the_owner_on_its_copy(self):
        """design item 11: no `postgres://ihr:` parsed any more; the copy's roles moved there too
        when it predates the move, then the audit check as ihr_owner."""
        drill = func("restore_drill")
        self.assertNotIn("postgres://ihr:", drill)
        self.assertIn('pw="$(db_password owner)"', drill)
        self.assertIn('dburl="postgres://ihr_owner:$pw@$DRILL:5432/ihr"', drill)
        self.assertIn('single) PG_TARGET="$DRILL" db_roles_apply --move', drill)
        self.assertLess(drill.index("db_roles_apply"), drill.index("ihr_audit_verify --all"))
        # only db-split-secrets still knows the single role's address (to replace it)
        users = [m.start() for m in re.finditer(r"postgres://ihr:", STAGING_SH)]
        split = STAGING_SH.index("\ndb_split_secrets() {")
        self.assertTrue(all(split < u < split + len(func("db_split_secrets")) + 40 for u in users), users)

    def test_no_rollback_across_the_move(self):
        guard = func("rel_roles_guard")
        self.assertIn('if rel_target_presplit "$1"; then', guard)
        self.assertIn('[[ "$text" != *"secrets/db-owner.env"* ]]', func("rel_target_presplit"))
        rollback = func("rollback")
        self.assertLess(rollback.index('rel_roles_guard "$target"'), rollback.index("rel_check_images"))

    def test_the_runbook_commands_exist(self):
        dispatch = STAGING_SH[STAGING_SH.index('case "${1:-}" in\n  init)'):]
        self.assertIn('db-split-secrets) db_split_secrets "${@:2}" ;;', dispatch)  # [--check] (P1-T21 C13)
        self.assertIn('db-roles) db_roles_cmd "${@:2}" ;;', dispatch)
        self.assertIn('--serve-previous) op="$(rel_operator "$op")"; db_serve_previous "$op" ;;', func("db_roles_cmd"))
        alerts = (ROOT / "deploy" / "logs" / "alerts" / "db-roles.yml").read_text()
        self.assertIn("scripts/staging.sh db-roles --assert", alerts)
        for heading in ("## Moving to split roles (P1-T19b)",):
            self.assertIn(heading, README)
        for text in ("db-split-secrets", "db-roles --migrate", "db-roles --serve-previous", "db-roles --assert",
                     "lo_unlink", "IN DATABASE <each> RESET ALL"):
            self.assertIn(text, README)


class Settings(unittest.TestCase):
    def test_required_is_the_deployment_default(self):
        deployment = (PLATFORM / "settings" / "deployment.py").read_text()
        self.assertIn('IHR_DB_ROLES = env("IHR_DB_ROLES", default="required")', deployment)
        secure = (PLATFORM / "settings" / "secure.py").read_text()
        self.assertIn('if roles != "required":', secure)


if __name__ == "__main__":
    unittest.main()
