"""P1-T32 E1 in scripts/staging.sh (ADR-020 §3, docs/reviews/p1-t32/design.md §4.1, §4.2, §4.5).

Covers the S2a part: a strict ``env_of``, ``init --environment|--local|--vm`` and ``init --check``,
production bucket names, ``ensure_environment`` on every ``compose()`` call, the Compose ``:?`` on
IHR_ENVIRONMENT, the usage text and the die strings. S2b (E2) is the second half of this file: the dispatch table of
stand-ins refused on production, ``env_state_problems`` (every marker, the bucket-name rule) on the
Compose start verbs, ``up``, ``release`` and ``init --check``, the ``smoke`` banner check, and the
drill and ``dumps_exec`` one-offs' environment.)

Run: python3 -m unittest scripts/test_staging_environment.py
"""

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGING_SH_PATH = ROOT / "scripts" / "staging.sh"
STAGING_SH = STAGING_SH_PATH.read_text()
STAGING = ROOT / "deploy" / "staging"

# A stand-in for docker: every call is logged with the IHR_ENVIRONMENT Compose would have seen;
# init's two lookups (an image digest, the key set) get fixed answers.
FAKE_DOCKER = r"""#!/usr/bin/env bash
printf 'IHR_ENVIRONMENT=%s args=%s\n' "${IHR_ENVIRONMENT-<unset>}" "$*" >> "$FAKE_LOG"
case "$*" in
  *"imagetools inspect"*) echo '{"digest":"sha256:00000000000000000000000000000000"}' ;;
  "image inspect"*) echo fakelabel ;;
  "run --rm"*) echo FAKEJWKS ;;
esac
exit 0
"""

WORKING_ENV = ["IHR_TENANT_DOMAIN_SUFFIX=app.example.in", "IHR_BACKEND_IMAGE=indiehr-backend:staging"]
ENV_LINE = re.compile(r"^IHR_ENVIRONMENT=(.*)$", re.M)


def func(name):
    return STAGING_SH.split(f"\n{name}() {{", 1)[1].split("\n}\n", 1)[0]


def definition(name):
    return f"{name}() {{" + func(name) + "\n}\n"


class Site:
    """A throwaway folder with a stand-in docker and a run of staging.sh against it."""

    def __init__(self, env_lines=None, local=False, buckets=None, trailing_newline=True):
        self._tmp = tempfile.TemporaryDirectory(prefix="ihr-env-test.")
        self.root = Path(self._tmp.name)
        self.dir = self.root / "staging"
        self.dir.mkdir()
        self.log = self.root / "docker-calls"
        bindir = self.root / "bin"
        bindir.mkdir()
        dk = bindir / "docker"
        dk.write_text(FAKE_DOCKER)
        dk.chmod(dk.stat().st_mode | stat.S_IXUSR)
        self.bindir = bindir
        if env_lines is not None:
            text = "\n".join(env_lines)
            (self.dir / ".env").write_text(text + ("\n" if trailing_newline else ""))
        if local:
            (self.dir / ".local").touch()
        if buckets is not None:
            sec = self.dir / "secrets"
            sec.mkdir()
            (sec / "app.env").write_text(f"FILE_UPLOAD_BUCKET={buckets[0]}\nFACILITY_S3_BUCKET={buckets[1]}\n")

    def close(self):
        self._tmp.cleanup()

    @property
    def env_file(self):
        return self.dir / ".env"

    def calls(self):
        return self.log.read_text() if self.log.exists() else ""

    def run(self, *args, out=None, extra_env=None, drop_env=("IHR_ENVIRONMENT", "IHR_STAGING_OUT", "IHR_OPERATOR")):
        env = {k: v for k, v in os.environ.items() if k not in drop_env}
        env.update({"PATH": f"{self.bindir}:{os.environ['PATH']}", "IHR_STAGING_DIR": str(self.dir),
                    "FAKE_LOG": str(self.log), "IHR_OPERATOR_LOCAL": "stderr"})
        if out is not None:
            env["IHR_STAGING_OUT"] = str(out)
        env.update(extra_env or {})
        return subprocess.run(["bash", str(STAGING_SH_PATH), *args], env=env, capture_output=True, text=True,
                              timeout=120, stdin=subprocess.DEVNULL)


class Case(unittest.TestCase):
    def site(self, **kw):
        s = Site(**kw)
        self.addCleanup(s.close)
        return s


def env_values(path):
    return dict(line.split("=", 1) for line in Path(path).read_text().splitlines()
                if "=" in line and not line.startswith("#"))


# ---- env_of -----------------------------------------------------------------------------------


class EnvOf(Case):
    def run_fn(self, folder):
        script = f'set -euo pipefail\n{definition("env_of")}\nv="$(env_of "$1")"\necho "[$v]alive"\n'
        out = subprocess.run(["bash", "-c", script, "_", str(folder)], capture_output=True)  # bytes: keep \r
        return subprocess.CompletedProcess(out.args, out.returncode, out.stdout.decode(), out.stderr.decode())

    def test_no_env_file_is_empty_and_never_kills_the_script(self):
        """design §4.1 [S4]: with `set -euo pipefail` a missing file must not exit the caller."""
        d = self.site()
        out = self.run_fn(d.dir)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, "[]alive\n")

    def test_reads_the_value_raw_and_the_last_line_wins(self):
        for text, want in (("IHR_ENVIRONMENT=staging\n", "staging"),
                           ("IHR_ENVIRONMENT=staging\nIHR_ENVIRONMENT=production-1\n", "production-1"),
                           ('IHR_ENVIRONMENT="staging"\n', '"staging"'),
                           ("IHR_ENVIRONMENT=staging # x\n", "staging # x"),
                           ("IHR_ENVIRONMENT=staging \n", "staging "),
                           ("IHR_ENVIRONMENT=staging\r\n", "staging\r"),
                           ("IHR_ENVIRONMENT=\n", ""),
                           (" IHR_ENVIRONMENT=staging\n", ""),
                           ("X_IHR_ENVIRONMENT=staging\n", ""),
                           ("IHR_ENVIRONMENT=Production-1\n", "Production-1"),
                           ("OTHER=1\n", "")):
            with self.subTest(text=text):
                d = self.site()
                d.env_file.write_bytes(text.encode())
                out = self.run_fn(d.dir)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(out.stdout, f"[{want}]alive\n")

    def test_the_regex_is_defined_once_in_the_script(self):
        self.assertEqual(STAGING_SH.count("\nENV_RE="), 1)


# ---- init --------------------------------------------------------------------------------------


class InitOptions(Case):
    def init(self, *args, site=None):
        s = site or self.site()
        return s, s.run("init", "app.example.in", *args)

    def test_local_and_vm_write_proof(self):
        for opt in ("--local", "--vm"):
            with self.subTest(opt=opt):
                s, p = self.init(opt)
                self.assertEqual(p.returncode, 0, p.stderr)
                lines = s.env_file.read_text().splitlines()
                self.assertEqual(lines[1], "IHR_ENVIRONMENT=proof")  # second line, before the suffix
                self.assertEqual(lines[2], "IHR_TENANT_DOMAIN_SUFFIX=app.example.in")
                self.assertEqual(len(ENV_LINE.findall(s.env_file.read_text())), 1)
                self.assertEqual((s.dir / ".local").exists(), opt == "--local")

    def test_environment_writes_the_given_name(self):
        for name in ("staging", "production-1", "production-12", "production-999"):
            with self.subTest(name=name):
                s, p = self.init("--environment", name)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(s.env_file.read_text().splitlines()[1], f"IHR_ENVIRONMENT={name}")
                self.assertFalse((s.dir / ".local").exists())

    def test_option_order_does_not_matter(self):
        s = self.site()
        p = s.run("init", "--environment", "staging", "app.example.in")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("IHR_ENVIRONMENT=staging", s.env_file.read_text())

    def test_every_malformed_call_is_refused_and_leaves_nothing(self):
        cases = {
            "no option": [],
            "environment proof": ["--environment", "proof"],
            "both options": ["--local", "--environment", "staging"],
            "both options, vm": ["--environment", "staging", "--vm"],
            "local and vm": ["--local", "--vm"],
            "production with local": ["--environment", "production-1", "--local"],
            "prod": ["--environment", "prod"],
            "Production-1": ["--environment", "Production-1"],
            "production-0": ["--environment", "production-0"],
            "production-01": ["--environment", "production-01"],
            "production-1000": ["--environment", "production-1000"],
            "dev": ["--environment", "dev"],
            "empty value": ["--environment", ""],
            "no value": ["--environment"],
            "unknown option": ["--local", "--bogus"],
        }
        for label, args in cases.items():
            with self.subTest(label=label):
                s, p = self.init(*args)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertFalse(s.env_file.exists())
                self.assertFalse((s.dir / "secrets").exists())
                self.assertEqual(s.calls(), "")  # refused before anything was asked of docker

    def test_the_refusal_says_how_to_fix_it(self):
        _, p = self.init()
        self.assertIn("--environment", p.stderr)
        self.assertIn("--local", p.stderr)
        _, p = self.init("--environment", "proof")
        self.assertIn("--local", p.stderr)

    def test_no_suffix_is_refused(self):
        s = self.site()
        p = s.run("init", "--local")
        self.assertEqual(p.returncode, 1)
        self.assertFalse(s.env_file.exists())

    def test_an_existing_folder_is_never_overwritten(self):
        s = self.site(env_lines=["IHR_ENVIRONMENT=staging"])
        before = s.env_file.read_text()
        p = s.run("init", "app.example.in", "--environment", "production-1")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(s.env_file.read_text(), before)


class InitBuckets(Case):
    def buckets(self, name):
        s = self.site()
        p = s.run("init", "app.example.in", "--environment", name)
        self.assertEqual(p.returncode, 0, p.stderr)
        return env_values(s.dir / "secrets" / "app.env")

    def test_production_buckets_carry_the_production_number(self):
        for n in ("1", "2", "12"):
            with self.subTest(n=n):
                app = self.buckets(f"production-{n}")
                self.assertEqual(app["FILE_UPLOAD_BUCKET"], f"ihr-prod{n}-files")
                self.assertEqual(app["FACILITY_S3_BUCKET"], f"ihr-prod{n}-pictures")

    def test_synthetic_buckets_never_carry_prod(self):
        for name, opt in (("staging", ("--environment", "staging")), ("proof", ("--local",)), ("proof", ("--vm",))):
            with self.subTest(name=name, opt=opt):
                s = self.site()
                p = s.run("init", "app.example.in", *opt)
                self.assertEqual(p.returncode, 0, p.stderr)
                app = env_values(s.dir / "secrets" / "app.env")
                for key in ("FILE_UPLOAD_BUCKET", "FACILITY_S3_BUCKET"):
                    self.assertNotIn("prod", app[key])
                    self.assertIn("staging", app[key])


class InitCheck(Case):
    def check(self, s, **kw):
        return s.run("init", "--check", **kw)

    def test_ok_prints_the_environment_and_the_suffix(self):
        for name in ("staging", "proof", "production-1", "production-37"):
            with self.subTest(name=name):
                s = self.site(env_lines=[*WORKING_ENV, f"IHR_ENVIRONMENT={name}"])
                p = self.check(s)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(p.stdout.strip(), f"ok {name} app.example.in")

    def test_a_folder_init_just_made_passes_for_every_kind(self):
        for opt in (("--local",), ("--vm",), ("--environment", "staging"), ("--environment", "production-2")):
            with self.subTest(opt=opt):
                s = self.site()
                self.assertEqual(s.run("init", "app.example.in", *opt).returncode, 0)
                p = self.check(s)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertTrue(p.stdout.startswith("ok "))

    def test_missing_or_malformed_values_are_refused_with_the_fix(self):
        bad = {"missing": None, "empty": "IHR_ENVIRONMENT=", "quoted": 'IHR_ENVIRONMENT="staging"',
               "single quoted": "IHR_ENVIRONMENT='proof'", "trailing space": "IHR_ENVIRONMENT=staging ",
               "comment": "IHR_ENVIRONMENT=staging # x", "wrong case": "IHR_ENVIRONMENT=Staging",
               "prod-1": "IHR_ENVIRONMENT=prod-1", "production-0": "IHR_ENVIRONMENT=production-0",
               "leading space": " IHR_ENVIRONMENT=staging", "dev": "IHR_ENVIRONMENT=dev"}
        for label, line in bad.items():
            with self.subTest(label=label):
                s = self.site(env_lines=[*WORKING_ENV] + ([line] if line else []))
                p = self.check(s)
                self.assertEqual(p.returncode, 1, p.stdout)
                self.assertNotIn("ok ", p.stdout)
                self.assertIn("IHR_ENVIRONMENT", p.stderr)
                self.assertIn("production-<n>", p.stderr)
                self.assertIn(str(s.dir / ".env"), p.stderr)

    def test_crlf_is_refused(self):
        s = self.site()
        s.env_file.write_bytes(b"IHR_TENANT_DOMAIN_SUFFIX=app.example.in\r\nIHR_ENVIRONMENT=staging\r\n")
        p = self.check(s)
        self.assertEqual(p.returncode, 1)
        self.assertNotIn("ok ", p.stdout)

    def test_no_env_file(self):
        s = self.site()
        p = self.check(s)
        self.assertEqual(p.returncode, 1)
        self.assertIn(str(s.dir / ".env"), p.stderr)

    def test_a_missing_suffix_is_a_problem(self):
        s = self.site(env_lines=["IHR_ENVIRONMENT=staging"])
        p = self.check(s)
        self.assertEqual(p.returncode, 1)
        self.assertIn("IHR_TENANT_DOMAIN_SUFFIX", p.stderr)

    def test_it_reads_out_not_dir(self):
        """On the deploying machine $OUT is production-1's folder and $DIR the repository's."""
        s = self.site(env_lines=[*WORKING_ENV, "IHR_ENVIRONMENT=staging"])
        other = s.root / "indiehr-envs" / "production-1"
        other.mkdir(parents=True)
        (other / ".env").write_text("\n".join([*WORKING_ENV, "IHR_ENVIRONMENT=production-1", ""]))
        p = self.check(s, out=other)
        self.assertEqual(p.stdout.strip(), "ok production-1 app.example.in")
        (other / ".env").write_text("IHR_TENANT_DOMAIN_SUFFIX=app.example.in\n")
        self.assertEqual(self.check(s, out=other).returncode, 1)  # DIR's good value does not count

    def test_every_problem_is_listed_not_only_the_first(self):
        s = self.site(env_lines=["IHR_ENVIRONMENT=prod"], buckets=("ihr-prod1-files", "ihr-prod1-pictures"))
        p = self.check(s)
        self.assertEqual(p.returncode, 1)
        self.assertIn("IHR_ENVIRONMENT", p.stderr)
        self.assertIn("IHR_TENANT_DOMAIN_SUFFIX", p.stderr)

    def test_bucket_names_must_fit_the_environment(self):
        rows = [
            ("staging", ("ihr-staging-files", "ihr-staging-facility"), True),
            ("proof", ("ihr-staging-files", "ihr-staging-facility"), True),
            ("staging", ("ihr-product-files", "ihr-staging-facility"), True),  # "product" is not a prod name
            ("staging", ("ihr-prod1-files", "ihr-staging-facility"), False),
            ("staging", ("ihr-staging-files", "ihr-prod1-pictures"), False),
            ("proof", ("ihr-prod-files", "ihr-staging-facility"), False),
            ("staging", ("prod", "ihr-staging-facility"), False),
            ("staging", ("ihr-production-files", "ihr-staging-facility"), False),
            ("production-1", ("ihr-prod1-files", "ihr-prod1-pictures"), True),
            ("production-1", ("ihr-staging-files", "ihr-prod1-pictures"), False),
            ("production-3", ("ihr-prod3-files", "ihr-staging-facility"), False),
            ("production-3", ("ihr-prod3-files", "ihr-prod3-staging-pictures"), False),
        ]
        for env, buckets, ok in rows:
            with self.subTest(env=env, buckets=buckets):
                s = self.site(env_lines=[*WORKING_ENV, f"IHR_ENVIRONMENT={env}"], buckets=buckets)
                p = self.check(s)
                self.assertEqual(p.returncode, 0 if ok else 1, p.stdout + p.stderr)
                if not ok:
                    self.assertIn("bucket", p.stderr)

    def test_no_secrets_folder_is_not_a_bucket_problem(self):
        """A server folder moved without secrets/ is other commands' complaint, not this one's."""
        s = self.site(env_lines=[*WORKING_ENV, "IHR_ENVIRONMENT=staging"])
        self.assertEqual(self.check(s).returncode, 0)

    def test_it_asks_docker_and_the_operator_log_for_nothing(self):
        """design §4.2: no operator wrapper, no operator session (like acme-proof) [L1]."""
        s = self.site(env_lines=[*WORKING_ENV, "IHR_ENVIRONMENT=staging"])
        p = s.run("init", "--check", extra_env={"IHR_OPERATOR_LOCAL": "stderr"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(s.calls(), "")
        self.assertNotIn("ihr-operator", p.stderr)
        self.assertNotIn("ihr-operator", p.stdout)

    def test_no_wrapper_is_called_in_the_check(self):
        body = func("init_check")
        for wrapper in ("admin_run", "migrate_run", "app_exec", "pg_su", "audit_supersede_run", "compose ", "op_need"):
            self.assertNotIn(wrapper, body)

    def test_check_takes_no_other_arguments(self):
        s = self.site(env_lines=[*WORKING_ENV, "IHR_ENVIRONMENT=staging"])
        for extra in (["app.example.in"], ["--local"], ["--environment", "staging"]):
            with self.subTest(extra=extra):
                self.assertEqual(s.run("init", "--check", *extra).returncode, 1)


# ---- compose() ---------------------------------------------------------------------------------


class ComposeEnsuresEnvironment(Case):
    def test_ensure_environment_runs_first_in_every_compose_call(self):
        body = func("compose")
        self.assertIn("ensure_environment", body)
        self.assertLess(body.index("ensure_environment"), body.index("docker compose"))
        self.assertLess(body.index("ensure_environment"), body.index("mkdir -p"))

    def test_a_valid_value_passes_and_is_what_compose_sees(self):
        for name in ("staging", "proof", "production-1"):
            with self.subTest(name=name):
                s = self.site(env_lines=[*WORKING_ENV, f"IHR_ENVIRONMENT={name}"])
                before = s.env_file.read_text()
                p = s.run("ps")
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn(f"IHR_ENVIRONMENT={name} args=", s.calls())
                self.assertEqual(s.env_file.read_text(), before)

    def test_it_runs_on_every_command_not_only_start_verbs(self):
        for cmd in (["ps"], ["logs"], ["down"]):
            with self.subTest(cmd=cmd):
                s = self.site(env_lines=[*WORKING_ENV])
                p = s.run(*cmd)
                self.assertEqual(p.returncode, 1, p.stdout)
                self.assertEqual(s.calls(), "")

    def test_a_folder_with_no_value_is_refused_with_the_fix_and_left_alone(self):
        s = self.site(env_lines=[*WORKING_ENV])
        before = s.env_file.read_text()
        p = s.run("ps")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(s.calls(), "")
        self.assertEqual(s.env_file.read_text(), before)
        self.assertIn("IHR_ENVIRONMENT", p.stderr)
        self.assertIn("staging", p.stderr)
        self.assertIn("production-<n>", p.stderr)
        self.assertIn("local-proof", p.stderr)

    def test_a_missing_env_file_is_refused(self):
        s = self.site()
        p = s.run("ps")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(s.calls(), "")
        self.assertIn(".env", p.stderr)

    def test_a_local_folder_is_a_proof_by_construction_and_upgrades_itself(self):
        s = self.site(env_lines=[*WORKING_ENV], local=True)
        p = s.run("ps")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("upgraded this proof folder: IHR_ENVIRONMENT=proof (ADR-020 E1)", p.stderr)
        self.assertEqual(ENV_LINE.findall(s.env_file.read_text()), ["proof"])
        self.assertIn("IHR_ENVIRONMENT=proof args=", s.calls())
        # once: the second call neither prints nor writes again
        again = s.run("ps")
        self.assertEqual(again.returncode, 0)
        self.assertNotIn("upgraded", again.stderr)
        self.assertEqual(ENV_LINE.findall(s.env_file.read_text()), ["proof"])

    def test_the_upgrade_keeps_a_file_without_a_final_newline_valid(self):
        s = self.site(env_lines=[*WORKING_ENV], local=True, trailing_newline=False)
        p = s.run("ps")
        self.assertEqual(p.returncode, 0, p.stderr)
        values = env_values(s.env_file)
        self.assertEqual(values["IHR_ENVIRONMENT"], "proof")
        self.assertEqual(values["IHR_BACKEND_IMAGE"], "indiehr-backend:staging")

    def test_a_local_folder_with_a_bad_value_is_refused_not_overwritten(self):
        for line in ('IHR_ENVIRONMENT="proof"', "IHR_ENVIRONMENT=", "IHR_ENVIRONMENT=Proof", "IHR_ENVIRONMENT=dev"):
            with self.subTest(line=line):
                s = self.site(env_lines=[*WORKING_ENV, line], local=True)
                before = s.env_file.read_text()
                p = s.run("ps")
                self.assertEqual(p.returncode, 1, p.stdout)
                self.assertEqual(s.env_file.read_text(), before)
                self.assertEqual(s.calls(), "")

    def test_nothing_writes_staging_or_production_automatically(self):
        s = self.site(env_lines=[*WORKING_ENV])
        s.run("ps")
        self.assertNotIn("staging", s.env_file.read_text().replace("indiehr-backend:staging", ""))
        self.assertNotIn("production", s.env_file.read_text())

    def test_the_shell_cannot_override_the_folder(self):
        """An exported IHR_ENVIRONMENT must neither beat the .env value nor stand in for it."""
        s = self.site(env_lines=[*WORKING_ENV, "IHR_ENVIRONMENT=staging"])
        p = s.run("ps", extra_env={"IHR_ENVIRONMENT": "production-1"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("IHR_ENVIRONMENT=staging args=", s.calls())
        self.assertNotIn("production-1", s.calls())
        t = self.site(env_lines=[*WORKING_ENV])
        q = t.run("ps", extra_env={"IHR_ENVIRONMENT": "staging"})
        self.assertEqual(q.returncode, 1)
        self.assertEqual(t.calls(), "")

    def test_local_proof_and_acme_proof_write_their_own_proof_value(self):
        self.assertRegex(func("acme_proof"), r"IHR_ENVIRONMENT=proof\n")
        self.assertIn('init "$LOCAL_SUFFIX" --local', STAGING_SH)


class ComposeFilesRequireIt(unittest.TestCase):
    FILES = ("compose.yaml", "compose.monitoring.yaml")

    def lines(self):
        for name in self.FILES:
            for n, line in enumerate((STAGING / name).read_text().splitlines(), 1):
                if re.match(r"\s*IHR_ENVIRONMENT:", line):
                    yield name, n, line.strip()

    def test_every_pass_through_requires_the_value(self):
        found = list(self.lines())
        self.assertGreaterEqual(len(found), 3)  # app, audit-supersede, audit-watch
        for name, n, line in found:
            with self.subTest(file=name, line=n):
                self.assertTrue(line.startswith("IHR_ENVIRONMENT: ${IHR_ENVIRONMENT:?"), line)
                self.assertIn("ADR-020 E1", line)

    def test_no_compose_file_defaults_it(self):
        for path in STAGING.glob("compose*.yaml"):
            with self.subTest(file=path.name):
                self.assertNotRegex(path.read_text(), r"IHR_ENVIRONMENT:-")

    @unittest.skipUnless(shutil.which("docker"), "docker not installed")
    def test_compose_fails_without_it_for_every_command(self):
        """A3: `${VAR:?msg}` is an interpolation error, so up, ps, down and logs all fail."""
        line = next(line for _, _, line in self.lines())
        with tempfile.TemporaryDirectory() as d:
            Path(d, "c.yaml").write_text(f"services:\n  a:\n    image: x\n    environment:\n      {line}\n")
            base = ["docker", "compose", "--project-directory", d, "-f", str(Path(d, "c.yaml"))]
            env = {k: v for k, v in os.environ.items() if k != "IHR_ENVIRONMENT"}
            probe = subprocess.run([*base, "version"], capture_output=True, env=env)
            if probe.returncode != 0:
                self.skipTest("docker compose not available")
            for cmd in (["config", "-q"], ["ps"], ["down"]):
                with self.subTest(cmd=cmd):
                    out = subprocess.run([*base, *cmd], capture_output=True, text=True, env=env)
                    self.assertNotEqual(out.returncode, 0, out.stdout)
                    self.assertIn("IHR_ENVIRONMENT", out.stderr)
            ok = subprocess.run([*base, "config", "-q"], capture_output=True, text=True,
                                env={**env, "IHR_ENVIRONMENT": "proof"})
            self.assertEqual(ok.returncode, 0, ok.stderr)


# ---- the words ---------------------------------------------------------------------------------


class UsageAndMessages(unittest.TestCase):
    def test_the_usage_header_documents_the_new_syntax(self):
        header = STAGING_SH.split("set -euo pipefail", 1)[0]
        self.assertIn("init <suffix> --environment", header)
        self.assertIn("--local", header)
        self.assertIn("--vm", header)
        self.assertIn("init --check", header)
        self.assertNotIn("init <suffix> [--local|--vm]", header)

    def test_no_message_still_tells_the_old_init_syntax(self):
        """Every `init <suffix>` hint names the environment option or a proof option."""
        for n, line in enumerate(STAGING_SH.splitlines(), 1):
            if "staging.sh init <suffix>" in line and not line.lstrip().startswith("#"):
                with self.subTest(line=n):
                    self.assertRegex(line, r"init <suffix> (--environment|--local)", line)

    def test_the_subcommand_list_is_not_changed(self):
        listed = (STAGING / "subcommands.txt").read_text().split()
        self.assertIn("init", listed)
        self.assertNotIn("init-check", listed)


# ---- E2 (S2b): the stand-ins refused on production ---------------------------------------------

PROD = "IHR_ENVIRONMENT=production-1"
REFUSED = "refused on production"


def table_block():
    return "E2_STAND_INS=(" + STAGING_SH.split("\nE2_STAND_INS=(", 1)[1].split("\n)\n", 1)[0] + "\n)\n"


class Fn:
    """Bash definitions cut out of staging.sh, run with DIR and OUT of the test's choosing."""

    @staticmethod
    def run(body, dir_, out=None, extra=""):
        script = "\n".join([
            "set -euo pipefail",
            f"ENV_RE={re.search(r'^ENV_RE=(.*)$', STAGING_SH, re.M).group(1)}",
            f"ENV_FIX={re.search(r'^ENV_FIX=(.*)$', STAGING_SH, re.M).group(1)}",
            'LOCAL_SUFFIX="app.ihr-proof.in"',
            'die() { echo "$*" >&2; exit 1; }',
            definition("env_of"), definition("env_val"), definition("env_state_problems"),
            table_block(), definition("stand_in_call"), definition("refuse_on_production"),
            'DIR="$1"; OUT="${2:-$1}"', extra, body,
        ])
        out_arg = str(out) if out is not None else str(dir_)
        return subprocess.run(["bash", "-c", script, "_", str(dir_), out_arg], capture_output=True, text=True)


STAND_INS = [  # every guarded stand-in invocation (design §4.3), with the subcommand it belongs to
    ("local-proof",), ("acme-proof",), ("acme-proof", "--keep"),
    ("acme-init", "pebble", "z.example.in", "ns.example.in"),
    ("acme-init", "letsencrypt-staging", "z.example.in", "ns.example.in"),
    ("mail-init", "--test"), ("monitoring-init", "--test"),
    ("init", "app.example.in", "--local"), ("init", "app.example.in", "--vm"),
    ("backup-init", "--local"), ("logs-archive-init", "--local"), ("dumps-init", "--local"),
]
NOT_STAND_INS = [  # real services, or not a stand-in: never refused by E2
    ("acme-init", "letsencrypt", "z.example.in", "ns.example.in"),
    ("mail-init", "ops@example.in"), ("monitoring-init", "ops@example.in"),
    ("init", "app.example.in", "--environment", "staging"),
    ("backup-init", "https://s3.example.in", "b", "ap-south-1"),
    ("logs-archive-init", "https://s3.example.in", "b", "ap-south-1"),
    ("dumps-init", "https://s3.example.in", "b", "ap-south-1"),
    ("s3-init",), ("dumps-expire", "--dry-run"), ("acme-proof-run",), ("ps",), ("up",),
    ("dumps-init", "--mark"),  # marks this instance's own bucket (E4): never a stand-in
]


class StandInTable(Case):
    def test_the_table_has_every_entry_of_the_design(self):
        block = table_block()
        for command in ("local-proof", "acme-proof", "acme-init", "mail-init", "monitoring-init", "init",
                        "backup-init", "logs-archive-init", "dumps-init"):
            with self.subTest(command=command):
                self.assertRegex(block, rf'"{re.escape(command)}\|')

    def test_it_is_checked_once_at_dispatch_before_main(self):
        """In op_open_eager (the dispatcher's first step, before any branch of main and any session)."""
        self.assertIn("dispatch_guard", func("op_open_eager"))
        self.assertLess(func("op_open_eager").index("dispatch_guard"), func("op_open_eager").index("op_begin"))
        body = func("dispatch_guard")
        self.assertIn("stand_in_call", body)
        self.assertIn("refuse_on_production", body)
        self.assertEqual(len(re.findall(r'^\s+dispatch_guard "', STAGING_SH, re.M)), 1)  # one call, one place
        self.assertNotIn("refuse_on_production", func("main"))
        # not edits inside twelve functions (M5): no stand-in branch calls the guard itself
        for fn in ("mail_init", "monitoring_init", "backup_init", "logs_archive_init", "dumps_init", "acme_init"):
            self.assertNotIn("refuse_on_production", func(fn), fn)

    def test_acme_proof_run_is_not_in_the_table(self):
        self.assertNotIn("acme-proof-run", table_block())

    def test_each_listed_call_is_a_stand_in_and_the_others_are_not(self):
        s = self.site()
        for argv in STAND_INS:
            with self.subTest(argv=argv):
                out = Fn.run('stand_in_call "$@"', s.dir, extra=f"set -- {' '.join(argv)}")
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(out.stdout.split()[0], argv[0])
        for argv in NOT_STAND_INS:
            with self.subTest(argv=argv):
                out = Fn.run('stand_in_call "$@"', s.dir, extra=f"set -- {' '.join(argv)}")
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(out.stdout, "")

    def test_the_flag_is_found_in_any_position(self):
        s = self.site()
        for argv in (("mail-init", "--from", "x", "--test"), ("init", "--local", "app.example.in"),
                     ("dumps-init", "https://e", "b", "r", "--local"), ("monitoring-init", "--test", "ops@example.in")):
            with self.subTest(argv=argv):
                out = Fn.run('stand_in_call "$@"', s.dir, extra=f"set -- {' '.join(argv)}")
                self.assertTrue(out.stdout.startswith(argv[0]), out.stdout)

    def test_removing_an_entry_or_the_call_would_be_caught(self):
        """The table and the call are text: a mutated copy of each must lose a refusal."""
        s = self.site(env_lines=[*WORKING_ENV, PROD])
        mutated = table_block().replace('"dumps-init|', '"dumps-init-removed|')
        out = Fn.run('stand_in_call dumps-init --local', s.dir, extra=mutated.replace("E2_STAND_INS=(", "E2_STAND_INS=(", 1))
        self.assertNotEqual(out.stdout.strip(), "dumps-init --local")  # the real one finds it, a removal does not
        out = Fn.run('stand_in_call dumps-init --local', s.dir)
        self.assertEqual(out.stdout.strip(), "dumps-init --local")


class RefuseOnProduction(Case):
    def refuse(self, s, argv, out=None):
        line = " ".join(argv)
        return Fn.run('c="$(stand_in_call "$@")"; [[ -z "$c" ]] || refuse_on_production "$c"; echo passed',
                      s.dir, out=out, extra=f"set -- {line}")

    def test_every_stand_in_is_refused_on_a_production_folder_as_dir(self):
        for argv in STAND_INS:
            with self.subTest(argv=argv):
                s = self.site(env_lines=[*WORKING_ENV, PROD])
                p = self.refuse(s, argv)
                self.assertEqual(p.returncode, 1, p.stdout)
                self.assertNotIn("passed", p.stdout)
                self.assertIn("production-1", p.stderr)
                self.assertIn(REFUSED, p.stderr)
                self.assertIn(argv[0], p.stderr)  # names the command line
                self.assertIn("app.example.in", p.stderr)  # and the instance's suffix

    def test_every_stand_in_is_refused_when_only_out_is_production(self):
        """On the deploying machine DIR is the repository's synthetic folder, OUT production-1's."""
        for argv in STAND_INS:
            with self.subTest(argv=argv):
                s = self.site(env_lines=[*WORKING_ENV, "IHR_ENVIRONMENT=staging"])
                out = s.root / "envs-production-1"
                out.mkdir()
                (out / ".env").write_text("\n".join([*WORKING_ENV, PROD]) + "\n")
                p = self.refuse(s, argv, out=out)
                self.assertEqual(p.returncode, 1, p.stdout)
                self.assertIn(REFUSED, p.stderr)
                self.assertIn(str(out), p.stderr)

    def test_every_stand_in_passes_on_staging_and_proof(self):
        for name in ("staging", "proof"):
            for argv in STAND_INS:
                with self.subTest(name=name, argv=argv):
                    s = self.site(env_lines=[*WORKING_ENV, f"IHR_ENVIRONMENT={name}"])
                    p = self.refuse(s, argv)
                    self.assertEqual(p.returncode, 0, p.stderr)
                    self.assertEqual(p.stdout.strip(), "passed")

    def test_no_env_in_dir_and_none_in_out_is_a_new_folder_and_passes(self):
        for argv in STAND_INS:
            with self.subTest(argv=argv):
                s = self.site()
                out = s.root / "fresh"
                p = self.refuse(s, argv, out=out)
                self.assertEqual(p.returncode, 0, p.stderr)

    def test_parsing_fails_closed(self):
        """Only exact staging or proof pass; quotes, spaces, CR, case, an empty value are refused (S5)."""
        bad = {"quoted": b'IHR_ENVIRONMENT="staging"\n', "single quoted": b"IHR_ENVIRONMENT='proof'\n",
               "trailing comment": b"IHR_ENVIRONMENT=staging # x\n", "trailing space": b"IHR_ENVIRONMENT=staging \n",
               "crlf": b"IHR_ENVIRONMENT=staging\r\n", "Production-1": b"IHR_ENVIRONMENT=Production-1\n",
               "prod-1": b"IHR_ENVIRONMENT=prod-1\n", "Staging": b"IHR_ENVIRONMENT=Staging\n",
               "empty value": b"IHR_ENVIRONMENT=\n", "deleted line": b"IHR_TENANT_DOMAIN_SUFFIX=app.example.in\n",
               "leading space": b" IHR_ENVIRONMENT=staging\n", "dev": b"IHR_ENVIRONMENT=dev\n"}
        for label, raw in bad.items():
            with self.subTest(label=label):
                s = self.site()
                s.env_file.write_bytes(raw)
                p = self.refuse(s, ("mail-init", "--test"))
                self.assertEqual(p.returncode, 1, p.stdout)
                self.assertNotIn("passed", p.stdout)
                self.assertIn("IHR_ENVIRONMENT", p.stderr)
                self.assertIn(str(s.dir / ".env"), p.stderr)

    def test_a_malformed_value_in_out_alone_is_refused_too(self):
        s = self.site(env_lines=[*WORKING_ENV, "IHR_ENVIRONMENT=staging"])
        out = s.root / "o"
        out.mkdir()
        (out / ".env").write_bytes(b"IHR_ENVIRONMENT=production-1\r\n")
        p = self.refuse(s, ("dumps-init", "--local"), out=out)
        self.assertEqual(p.returncode, 1)

    def test_the_only_exception_is_a_local_folder_with_no_line_at_all(self):
        """Q-K152: init --local made it (.local), so it is a proof by construction."""
        s = self.site(env_lines=[*WORKING_ENV], local=True)
        self.assertEqual(self.refuse(s, ("backup-init", "--local")).returncode, 0)
        s = self.site(env_lines=[*WORKING_ENV, "IHR_ENVIRONMENT="], local=True)
        self.assertEqual(self.refuse(s, ("backup-init", "--local")).returncode, 1)
        s = self.site(env_lines=[*WORKING_ENV, "IHR_ENVIRONMENT=Proof"], local=True)
        self.assertEqual(self.refuse(s, ("backup-init", "--local")).returncode, 1)

    @unittest.skipIf(os.geteuid() == 0, "root reads every file")
    def test_an_unreadable_env_is_refused_not_skipped(self):
        s = self.site(env_lines=[*WORKING_ENV, "IHR_ENVIRONMENT=staging"])
        s.env_file.chmod(0)
        self.addCleanup(s.env_file.chmod, 0o600)
        p = self.refuse(s, ("mail-init", "--test"))
        self.assertEqual(p.returncode, 1)
        self.assertNotIn("passed", p.stdout)


class DispatchRefusesBeforeAnything(Case):
    """The real script: a production folder refuses each stand-in at dispatch, docker untouched."""

    def test_each_stand_in_is_refused_with_the_whole_script(self):
        for argv in STAND_INS:
            with self.subTest(argv=argv):
                s = self.site(env_lines=[*WORKING_ENV, PROD])
                before = s.env_file.read_text()
                p = s.run(*argv)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn(REFUSED, p.stderr)
                self.assertIn("production-1", p.stderr)
                self.assertEqual(s.calls(), "")
                self.assertEqual(s.env_file.read_text(), before)
                self.assertFalse((s.dir / "secrets").exists())

    def test_init_local_into_a_new_out_is_refused_by_dir(self):
        """The folder init would write is new, so the check is on DIR (design §4.3)."""
        s = self.site(env_lines=[*WORKING_ENV, PROD])
        out = s.root / "newout"
        p = s.run("init", "app.example.in", "--local", out=out)
        self.assertEqual(p.returncode, 1)
        self.assertIn(REFUSED, p.stderr)
        self.assertFalse(out.exists())

    def test_staging_and_proof_folders_still_run_them(self):
        for name in ("staging", "proof"):
            with self.subTest(name=name):
                s = self.site(env_lines=[*WORKING_ENV, f"IHR_ENVIRONMENT={name}"])
                out = s.root / "o"
                p = s.run("init", "app.example.in", "--local", out=out)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertNotIn(REFUSED, p.stderr)
                self.assertTrue((out / ".env").exists())

    def test_a_real_service_is_not_touched_by_the_guard(self):
        s = self.site(env_lines=[*WORKING_ENV, PROD])
        p = s.run("mail-init", "ops@example.in")
        self.assertNotIn(REFUSED, p.stderr)  # it fails later (no secrets folder), on its own terms


# ---- E2 (S2b): the state check -----------------------------------------------------------------

MARKERS = {  # label -> (.env lines, secrets files, local?, text the problem must name)
    "IHR_ACME=pebble": (["IHR_ACME=pebble"], {}, False, "IHR_ACME=pebble"),
    "IHR_ACME=letsencrypt-staging": (["IHR_ACME=letsencrypt-staging"], {}, False, "IHR_ACME=letsencrypt-staging"),
    "IHR_MAIL=test": (["IHR_MAIL=test"], {}, False, "IHR_MAIL=test"),
    "IHR_MONITORING=test": (["IHR_MONITORING=test"], {}, False, "IHR_MONITORING=test"),
    "IHR_BACKUP_ENDPOINT stand-in": (["IHR_BACKUP_ENDPOINT=http://s3:8333"], {}, False, "IHR_BACKUP_ENDPOINT"),
    "IHR_DUMPS=local": (["IHR_DUMPS=local"], {}, False, "IHR_DUMPS=local"),
    "IHR_LOGS_ARCHIVE=local": (["IHR_LOGS_ARCHIVE=local"], {}, False, "IHR_LOGS_ARCHIVE=local"),
    ".local": ([], {}, True, ".local"),
    "proof suffix": (["IHR_TENANT_DOMAIN_SUFFIX=app.ihr-proof.in"], {}, False, "ihr-proof.in"),
    "other proof suffix": (["IHR_TENANT_DOMAIN_SUFFIX=x.ihr-proof.in"], {}, False, "ihr-proof.in"),
    "IHR_SMOKE_RESOLVE": (["IHR_SMOKE_RESOLVE=127.0.0.1"], {}, False, "IHR_SMOKE_RESOLVE"),
    "IHR_BIND=127.0.0.1": (["IHR_BIND=127.0.0.1"], {}, False, "IHR_BIND=127.0.0.1"),
    "anchor-read endpoint": ([], {"anchor-read.env": "IHR_AUDIT_ANCHOR_ENDPOINT=http://s3:8333\n"}, False, "anchor-read.env"),
    "anchor-write endpoint": ([], {"anchor-write.env": "IHR_AUDIT_ANCHOR_ENDPOINT=http://s3:8333\n"}, False, "anchor-write.env"),
    "anchor-events endpoint": ([], {"anchor-events.env": "IHR_AUDIT_ANCHOR_ENDPOINT=http://s3:8333\n"}, False, "anchor-events.env"),
    "file bucket says staging": ([], {"app.env": "FILE_UPLOAD_BUCKET=ihr-staging-files\nFACILITY_S3_BUCKET=ihr-prod1-pictures\n"},
                                 False, "FILE_UPLOAD_BUCKET=ihr-staging-files"),
    "picture bucket says staging": ([], {"app.env": "FILE_UPLOAD_BUCKET=ihr-prod1-files\nFACILITY_S3_BUCKET=ihr-staging-facility\n"},
                                    False, "FACILITY_S3_BUCKET=ihr-staging-facility"),
}


def folder_with(case, name, markers=(), secrets=None, local=False, extra=()):
    s = case.site(env_lines=[*WORKING_ENV, f"IHR_ENVIRONMENT={name}", *markers, *extra], local=local)
    if secrets:
        sec = s.dir / "secrets"
        sec.mkdir(exist_ok=True)
        for fname, text in secrets.items():
            (sec / fname).write_text(text)
    return s


class EnvStateProblems(Case):
    def problems(self, s, folder=None):
        p = Fn.run('env_state_problems "$DIR"', folder or s.dir)
        self.assertEqual(p.returncode, 0, p.stderr)  # a list, never a failure
        return [line for line in p.stdout.splitlines() if line]

    def test_a_clean_production_folder_has_no_problems(self):
        s = folder_with(self, "production-1", secrets={"app.env": "FILE_UPLOAD_BUCKET=ihr-prod1-files\nFACILITY_S3_BUCKET=ihr-prod1-pictures\n",
                                                       "anchor-read.env": "IHR_AUDIT_ANCHOR_ENDPOINT=https://s3.example.in\n"})
        self.assertEqual(self.problems(s), [])

    def test_every_marker_is_reported_on_production(self):
        for label, (lines, secrets, local, needle) in MARKERS.items():
            with self.subTest(marker=label):
                s = folder_with(self, "production-1", lines, secrets, local)
                found = self.problems(s)
                self.assertTrue(any(needle in line for line in found), (needle, found))

    def test_every_problem_is_listed_not_only_the_first(self):
        lines = ["IHR_ACME=pebble", "IHR_MAIL=test", "IHR_MONITORING=test", "IHR_DUMPS=local", "IHR_BIND=127.0.0.1"]
        s = folder_with(self, "production-1", lines, local=True)
        self.assertGreaterEqual(len(self.problems(s)), 6)

    def test_production_markers_are_not_problems_on_staging_or_proof(self):
        lines = ["IHR_ACME=pebble", "IHR_MAIL=test", "IHR_MONITORING=test", "IHR_DUMPS=local", "IHR_LOGS_ARCHIVE=local",
                 "IHR_BACKUP_ENDPOINT=http://s3:8333", "IHR_SMOKE_RESOLVE=127.0.0.1", "IHR_BIND=127.0.0.1"]
        for name in ("staging", "proof"):
            with self.subTest(name=name):
                s = folder_with(self, name, lines, {"anchor-read.env": "IHR_AUDIT_ANCHOR_ENDPOINT=http://s3:8333\n"}, local=True)
                self.assertEqual(self.problems(s), [])

    def test_staging_bucket_names_must_not_say_prod(self):
        for name in ("staging", "proof"):
            with self.subTest(name=name):
                s = folder_with(self, name, secrets={"app.env": "FILE_UPLOAD_BUCKET=ihr-prod1-files\nFACILITY_S3_BUCKET=ihr-staging-facility\n"})
                found = self.problems(s)
                self.assertEqual(len(found), 1, found)
                self.assertIn("FILE_UPLOAD_BUCKET=ihr-prod1-files", found[0])

    def test_marker_values_are_read_as_compose_would_read_them(self):
        """A quote, a CR or a blank around a value does not hide a stand-in (fail closed)."""
        for text in (b'IHR_MAIL="test"\n', b"IHR_MAIL='test'\n", b"IHR_MAIL=test\r\n", b"IHR_MAIL= test\n", b"IHR_MAIL=test \n"):
            with self.subTest(text=text):
                s = folder_with(self, "production-1")
                s.env_file.write_bytes(s.env_file.read_bytes() + text)
                self.assertTrue(any("IHR_MAIL" in line for line in self.problems(s)))

    def test_the_last_line_wins_like_compose(self):
        s = folder_with(self, "production-1", ["IHR_MAIL=test", "IHR_MAIL=smtp"])
        self.assertEqual(self.problems(s), [])
        s = folder_with(self, "production-1", ["IHR_MAIL=smtp", "IHR_MAIL=test"])
        self.assertTrue(self.problems(s))

    def test_a_real_provider_endpoint_is_not_a_marker(self):
        s = folder_with(self, "production-1", ["IHR_BACKUP_ENDPOINT=https://s3.ap-south-1.example.in", "IHR_DUMPS=s3",
                                               "IHR_LOGS_ARCHIVE=s3", "IHR_ACME=letsencrypt", "IHR_MAIL=smtp"],
                        {"anchor-write.env": "IHR_AUDIT_ANCHOR_ENDPOINT=https://s3.ap-south-1.example.in\n"})
        self.assertEqual(self.problems(s), [])

    def test_there_is_a_test_row_for_every_marker_of_the_design(self):
        """design §4.4's list, one entry per marker: add a marker, add a row."""
        text = func("env_state_problems")
        for needle in ("IHR_ACME", "pebble", "letsencrypt-staging", "IHR_MAIL", "IHR_MONITORING", "IHR_BACKUP_ENDPOINT",
                       "IHR_DUMPS", "IHR_LOGS_ARCHIVE", ".local", "ihr-proof.in", "IHR_SMOKE_RESOLVE", "IHR_BIND",
                       "IHR_AUDIT_ANCHOR_ENDPOINT", "FILE_UPLOAD_BUCKET", "FACILITY_S3_BUCKET"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertGreaterEqual(len({k.split()[0].split("=")[0] for k in MARKERS}), 10)

    def test_it_reads_only_and_calls_no_wrapper(self):
        body = func("env_state_problems")
        for wrapper in ("admin_run", "migrate_run", "app_exec", "pg_su", "audit_supersede_run", "compose ", "op_need", "docker"):
            self.assertNotIn(wrapper, body)


class InitCheckStateProblems(Case):
    def test_production_markers_fail_init_check_with_all_of_them(self):
        s = folder_with(self, "production-1", ["IHR_MAIL=test", "IHR_DUMPS=local"], local=True)
        p = s.run("init", "--check")
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertNotIn("ok ", p.stdout)
        for needle in ("IHR_MAIL=test", "IHR_DUMPS=local", ".local"):
            self.assertIn(needle, p.stderr)

    def test_the_same_markers_pass_on_staging(self):
        s = folder_with(self, "staging", ["IHR_MAIL=test", "IHR_DUMPS=local"], local=True)
        p = s.run("init", "--check")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "ok staging app.example.in")

    def test_it_reads_out_for_the_state_too(self):
        s = folder_with(self, "staging")
        out = s.root / "o"
        out.mkdir()
        (out / ".env").write_text("\n".join([*WORKING_ENV, PROD, "IHR_ACME=pebble"]) + "\n")
        p = s.run("init", "--check", out=out)
        self.assertEqual(p.returncode, 1)
        self.assertIn("IHR_ACME=pebble", p.stderr)
        self.assertIn(str(out), p.stderr)

    def test_a_clean_production_folder_passes(self):
        s = folder_with(self, "production-3", secrets={"app.env": "FILE_UPLOAD_BUCKET=ihr-prod3-files\nFACILITY_S3_BUCKET=ihr-prod3-pictures\n"})
        p = s.run("init", "--check")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "ok production-3 app.example.in")

    def test_init_check_uses_the_one_list_and_still_calls_no_wrapper(self):
        body = func("init_check")
        self.assertIn("env_state_problems", body)
        self.assertNotIn("FILE_UPLOAD_BUCKET", body)  # the bucket rule lives in the one list


class StartVerbsCheckState(Case):
    """compose() runs env_state_problems on up, run, start, restart, create; never on ps, down, logs."""

    def test_the_hook_is_in_compose_after_ensure_environment_and_before_docker(self):
        body = func("compose")
        self.assertLess(body.index("ensure_environment"), body.index("state_guard"))
        self.assertLess(body.index("state_guard"), body.index("docker compose"))
        self.assertIn("up|run|start|restart|create", body)

    def compose(self, s, *args):
        """compose() itself, cut out of staging.sh, with the stand-in docker on PATH."""
        env_re = re.search(r"^ENV_RE=(.*)$", STAGING_SH, re.M).group(1)
        env_fix = re.search(r"^ENV_FIX=(.*)$", STAGING_SH, re.M).group(1)
        script = "\n".join([
            "set -euo pipefail", f"ENV_RE={env_re}", f"ENV_FIX={env_fix}", 'LOCAL_SUFFIX="app.ihr-proof.in"',
            'die() { echo "$*" >&2; exit 1; }', f'DIR="{s.dir}"; SECRETS="$DIR/secrets"; GEN="$DIR/generated"',
            definition("env_get"), definition("env_of"), definition("env_val"), definition("env_state_problems"),
            definition("state_guard"), definition("compose_verb"), definition("ensure_environment"), definition("compose"),
            'compose "$@"',
        ])
        env = {**os.environ, "PATH": f"{s.bindir}:{os.environ['PATH']}", "FAKE_LOG": str(s.log)}
        env.pop("IHR_ENVIRONMENT", None)
        return subprocess.run(["bash", "-c", script, "_", *args], capture_output=True, text=True, env=env, timeout=60)

    def test_start_verbs_are_refused_on_a_folder_with_markers(self):
        for verb in (["up", "-d"], ["run", "--rm", "x"], ["start", "x"], ["restart", "x"], ["create", "x"],
                     ["--profile", "admin", "run", "--rm", "x"]):
            with self.subTest(verb=verb):
                s = folder_with(self, "production-1", ["IHR_MAIL=test"])
                p = self.compose(s, *verb)
                self.assertEqual(p.returncode, 1, p.stdout)
                self.assertIn("IHR_MAIL=test", p.stderr)
                self.assertEqual(s.calls(), "")

    def test_the_other_verbs_still_work(self):
        for cmd in (["ps"], ["down"]):
            with self.subTest(cmd=cmd):
                s = folder_with(self, "production-1", ["IHR_MAIL=test", "IHR_DUMPS=local"], local=True)
                p = s.run(*cmd)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertNotIn("IHR_MAIL=test", p.stderr)
                self.assertNotEqual(s.calls(), "")

    def test_logs_and_exec_and_stop_are_not_start_verbs(self):
        for verb in (["logs", "--tail=5"], ["exec", "x", "true"], ["stop", "x"], ["ps", "-q"], ["config"]):
            with self.subTest(verb=verb):
                s = folder_with(self, "production-1", ["IHR_MAIL=test"], local=True)
                p = self.compose(s, *verb)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn(verb[0], s.calls())

    def test_a_clean_production_folder_starts(self):
        for verb in (["up", "-d"], ["restart", "traefik"], ["--profile", "admin", "run", "x"]):
            with self.subTest(verb=verb):
                s = folder_with(self, "production-1")
                p = self.compose(s, *verb)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn(verb[-2] if verb[0] == "--profile" else verb[0], s.calls())

    def test_markers_on_staging_do_not_stop_a_start(self):
        s = folder_with(self, "staging", ["IHR_MAIL=test"])
        p = self.compose(s, "restart", "traefik")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("restart traefik", s.calls())

    def test_the_verb_is_found_behind_profile_options(self):
        """admin_run is `compose --profile admin run ...`: still a start verb."""
        body = definition("compose_verb")
        for argv, want in ((["--profile", "admin", "run", "--rm", "x"], "run"), (["up", "-d"], "up"),
                           (["ps", "-q", "db"], "ps"), (["--profile", "keys", "run"], "run"), ([], "")):
            with self.subTest(argv=argv):
                out = subprocess.run(["bash", "-c", f'set -euo pipefail\n{body}\ncompose_verb "$@"', "_", *argv],
                                     capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(out.stdout.strip(), want)

    def test_up_and_release_die_on_any_marker_first(self):
        """The Compose :? would stop them too, with a worse message (design §4.4)."""
        self.assertIn("state_guard", func("up"))
        self.assertIn("state_guard", func("release"))
        s = folder_with(self, "production-1", ["IHR_ACME=pebble"])
        p = s.run("up")
        self.assertEqual(p.returncode, 1)
        self.assertIn("IHR_ACME=pebble", p.stderr)
        self.assertEqual(s.calls(), "")
        s = folder_with(self, "production-1", ["IHR_ACME=pebble"])
        p = s.run("release", "--now", "--operator", "tester")
        self.assertEqual(p.returncode, 1)
        self.assertIn("IHR_ACME=pebble", p.stderr)
        self.assertEqual(s.calls(), "")

    def test_up_and_release_die_on_a_missing_or_invalid_environment(self):
        for cmd in (["up"], ["release", "--now", "--operator", "tester"]):
            for line in (None, "IHR_ENVIRONMENT=prod-1", 'IHR_ENVIRONMENT="staging"'):
                with self.subTest(cmd=cmd, line=line):
                    s = self.site(env_lines=[*WORKING_ENV] + ([line] if line else []))
                    p = s.run(*cmd)
                    self.assertEqual(p.returncode, 1)
                    self.assertIn("IHR_ENVIRONMENT", p.stderr)
                    self.assertEqual(s.calls(), "")


# ---- E2/E5 (S2b): smoke, the drill and the dumper one-off --------------------------------------


def py_raw_string(path, name):
    """The text of a module-level raw triple-quoted string (the scripts bootstrap.js serves)."""
    return path.read_text().split(f'{name} = r"""', 1)[1].split('"""', 1)[0]


SETTINGS_APP = ROOT / "plugins" / "ihr_platform" / "ihr_tenant_settings"
IST_BAR = py_raw_string(SETTINGS_APP / "device_time.py", "WARNING_SCRIPT")  # served on every instance
SYNTHETIC_BAR = py_raw_string(SETTINGS_APP / "synthetic_bar.py", "_TEMPLATE")  # only off production


class SmokeBanner(Case):
    def banner(self, environment, js):
        env_re = re.search(r"^ENV_RE=(.*)$", STAGING_SH, re.M).group(1)
        script = f'set -euo pipefail\nENV_RE={env_re}\n{definition("banner_line")}\nbanner_line "$1" "$2" "$3"'
        return subprocess.run(["bash", "-c", script, "_", "alpha", environment, js], capture_output=True, text=True)

    def test_synthetic_environments_must_carry_the_banner(self):
        for name in ("staging", "proof"):
            with self.subTest(name=name):
                ok = self.banner(name, IST_BAR + SYNTHETIC_BAR)
                self.assertEqual(ok.returncode, 0, ok.stdout)
                self.assertTrue(ok.stdout.startswith("ok "))
                bad = self.banner(name, IST_BAR)
                self.assertEqual(bad.returncode, 1)
                self.assertIn("FAIL  alpha: banner state does not match IHR_ENVIRONMENT", bad.stdout)

    def test_production_must_not_carry_it(self):
        ok = self.banner("production-1", IST_BAR)
        self.assertEqual(ok.returncode, 0, ok.stdout)
        bad = self.banner("production-1", IST_BAR + SYNTHETIC_BAR)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("FAIL  alpha: banner state does not match IHR_ENVIRONMENT", bad.stdout)

    def test_the_ist_bar_alone_is_not_the_synthetic_banner(self):
        """Review O2: the IST bar (served everywhere) offsets itself by `--ihr-synthetic-h`; that
        name is not the synthetic bar. Production with the IST bar alone passes; staging and proof
        with the IST bar alone fail (the synthetic bar is missing)."""
        self.assertIn("--ihr-synthetic-h", IST_BAR)  # the collision this guards against
        self.assertNotIn('var BAR_ID = "ihr-synthetic";', IST_BAR)
        self.assertIn('var BAR_ID = "ihr-synthetic";', SYNTHETIC_BAR)
        self.assertEqual(self.banner("production-1", IST_BAR).returncode, 0)
        self.assertEqual(self.banner("production-7", "var x = 'ihr-synthetic'; --ihr-synthetic-h").returncode, 0)
        for name in ("staging", "proof"):
            with self.subTest(name=name):
                self.assertEqual(self.banner(name, IST_BAR).returncode, 1)
                self.assertEqual(self.banner(name, SYNTHETIC_BAR).returncode, 0)

    def test_an_unreadable_answer_fails_a_synthetic_instance_and_an_unknown_environment_fails_closed(self):
        self.assertEqual(self.banner("staging", "").returncode, 1)
        self.assertEqual(self.banner("", SYNTHETIC_BAR).returncode, 1)  # no valid environment: never "not production"
        self.assertEqual(self.banner("prod-1", "").returncode, 1)

    def test_smoke_prints_the_environment_first_and_checks_only_hospitals_that_are_served(self):
        body = func("smoke")
        self.assertIn("environment: ", body)
        first_check = body.index("check ")
        self.assertLess(body.index('echo "environment: '), first_check)
        self.assertIn("/api/ihr/bootstrap.js", body)
        self.assertIn("banner_line", body)
        # not held: only after the hospital answered 200 on bootstrap/ (a held one answers 503 and a stub)
        loop = body[body.index('for slug in ${slugs[@]+"${slugs[@]}"}; do'):]
        self.assertIn("== 200", loop.split("done", 1)[0])
        self.assertIn("banner_line", loop.split("done", 1)[0])
        self.assertIn("curl_args", loop.split("done", 1)[0])  # through the same curl arguments (--resolve, :$port)


class DrillAndDumperEnvironment(Case):
    def test_the_drill_one_off_passes_the_instances_environment_and_suffix(self):
        body = func("hospital_restore_drill")
        run = body[body.index("docker run --rm --network"):body.index("--entrypoint python")]
        self.assertIn("-e IHR_ENVIRONMENT=", run)
        self.assertIn("-e IHR_TENANT_DOMAIN_SUFFIX=", run)

    def test_the_dumper_one_off_passes_both_values(self):
        """design §6.1 [R3]: a lost server's `check`, `get`, `probe` read the instance too."""
        body = func("dumps_exec")
        run = body[body.index("docker run --rm -i"):]
        self.assertIn("-e IHR_ENVIRONMENT=", run)
        self.assertIn("-e IHR_TENANT_DOMAIN_SUFFIX=", run)

    def test_the_dumper_one_off_runs_with_the_values_of_the_folder(self):
        s = folder_with(self, "staging", ["IHR_DUMPS=s3"])
        p = s.run("dumps-check")
        self.assertEqual(p.returncode, 0, p.stderr)
        run_lines = [line for line in s.calls().splitlines() if " run --rm -i" in line or "args=run --rm -i" in line]
        self.assertTrue(run_lines, s.calls())
        self.assertIn("-e IHR_ENVIRONMENT=staging", run_lines[0])
        self.assertIn("-e IHR_TENANT_DOMAIN_SUFFIX=app.example.in", run_lines[0])

    def test_every_dumper_py_run_in_the_script_carries_the_values(self):
        """Every `docker run` that executes dumper.py (not expire.py, not the offline quarantine)."""
        for fn in ("dumps_exec",):
            text = func(fn)
            self.assertIn("-e IHR_ENVIRONMENT=", text)
            self.assertIn("-e IHR_TENANT_DOMAIN_SUFFIX=", text)
        # the running container gets them from Compose (compose.dumps.yaml, S4); the quarantine and
        # expire.py read no instance
        self.assertNotIn("dumper.py", func("restore_quarantine"))
        self.assertNotIn("dumper.py", func("dumps_expire"))


class DumpsBucketMarker(Case):
    """Review O1 (E4, design §6.1): `dumper.py mark` is reachable from staging.sh. `dumps-init --mark`
    marks this instance's bucket (an explicit operator step for a real bucket, so a wrong empty
    bucket is still caught); a proof's own stand-in is marked by `up` before its dumper starts, so
    local-proof's smoke finds the marker; any other `up` hints when the marker is missing."""

    def test_dumps_init_mark_runs_dumper_mark_with_the_folders_instance(self):
        s = folder_with(self, "staging", ["IHR_DUMPS=s3"], secrets={"dumps.env": "IHR_DUMP_BUCKET=b\n"})
        p = s.run("dumps-init", "--mark")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        run_lines = [line for line in s.calls().splitlines() if "args=run --rm -i" in line]
        self.assertEqual(len(run_lines), 1, s.calls())
        self.assertTrue(run_lines[0].endswith(" mark"), run_lines[0])
        self.assertIn("-e IHR_ENVIRONMENT=staging", run_lines[0])
        self.assertIn("-e IHR_TENANT_DOMAIN_SUFFIX=app.example.in", run_lines[0])
        # the settings are left as they are (no second IHR_DUMPS, no new key)
        self.assertEqual(s.env_file.read_text().count("IHR_DUMPS="), 1)
        self.assertEqual((s.dir / "secrets" / "dumps.env").read_text(), "IHR_DUMP_BUCKET=b\n")

    def test_dumps_init_mark_needs_dumps_and_takes_nothing_else(self):
        s = folder_with(self, "staging", secrets={"x.env": ""})
        p = s.run("dumps-init", "--mark")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("no nightly dumps here", p.stderr)
        s = folder_with(self, "staging", ["IHR_DUMPS=s3"], secrets={"dumps.env": ""})
        p = s.run("dumps-init", "--mark", "--local")
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("args=run --rm -i", s.calls())

    def test_mark_runs_in_the_one_off_too(self):
        body = func("dumps_exec")
        allowed = re.search(r'case "\$1" in ([a-z|-]+)\) ;;', body).group(1).split("|")
        self.assertIn("mark", allowed)
        self.assertIn('dumps_exec mark', func("dumps_init"))

    def test_the_usage_and_the_refusal_name_the_same_option(self):
        self.assertIn("dumps-init --mark", STAGING_SH.split("set -euo pipefail", 1)[0])
        dumper = (ROOT / "deploy" / "dumps" / "dumper.py").read_text()
        self.assertIn("dumps-init --mark", dumper)

    def dumps_up(self, environment, dumps, check_out="", mark_rc=0):
        stubs = "\n".join([
            'die() { echo "DIE $*"; exit 1; }',
            'dumper_image() { echo "CALL image"; }',
            'compose() { echo "CALL compose $*"; }',
            f'env_get() {{ case "$1" in IHR_DUMPS) echo "{dumps}" ;; *) echo "" ;; esac; }}',
            f'env_of() {{ echo "{environment}"; }}',
            f'dumps_exec() {{ echo "CALL exec $*"; [[ "$1" != check ]] || printf %s "{check_out}"; '
            f'[[ "$1" != check ]] || return 1; [[ "$1" != mark ]] || return {mark_rc}; return 0; }}',
            "sleep() { :; }",
        ])
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "dumps.env").write_text("")
        script = f'set -euo pipefail\nSECRETS="{tmp}"\nDIR="{tmp}"\n{stubs}\n{definition("dumps_up")}\ndumps_up\n'
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)

    def test_up_marks_a_proofs_stand_in_before_its_dumper_starts(self):
        p = self.dumps_up("proof", "local")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        calls = [line for line in p.stdout.splitlines() if line.startswith("CALL ")]
        self.assertIn("CALL exec mark", calls)
        start = next(i for i, c in enumerate(calls) if c.startswith("CALL compose up") and c.endswith(" dumper"))
        self.assertLess(calls.index("CALL exec mark"), start)

    def test_up_never_marks_a_real_bucket_and_hints_when_it_has_no_marker(self):
        missing = "BAD   instance marker: the dumps bucket has no instance marker (dumps/instance.json): x\n"
        for environment, dumps in (("staging", "s3"), ("production-1", "s3"), ("staging", "local")):
            with self.subTest(environment=environment, dumps=dumps):
                p = self.dumps_up(environment, dumps, check_out=missing)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertNotIn("CALL exec mark", p.stdout)
                self.assertIn("scripts/staging.sh dumps-init --mark", p.stdout)
                hint = p.stdout.index("dumps-init --mark")
                start = p.stdout.index("CALL compose up -d --wait --no-deps dumper")
                self.assertLess(start, hint)
        # a marked bucket: no hint
        p = self.dumps_up("staging", "s3", check_out="ok    instance marker: staging (app.example.in)\n")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("dumps-init --mark", p.stdout)

    def test_a_proof_whose_mark_is_refused_stops_up_before_its_dumper_starts(self):
        p = self.dumps_up("proof", "local", mark_rc=1)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("DIE ", p.stdout)
        self.assertNotIn("CALL compose up -d --wait --no-deps dumper", p.stdout)

    def test_local_proof_marks_the_bucket_before_its_smoke(self):
        block = STAGING_SH.split("\n  local-proof)\n", 1)[1].split("\n    ;;\n", 1)[0]
        lines = [line.strip() for line in block.splitlines()]
        self.assertLess(lines.index("up"), lines.index("smoke"))
        self.assertIn("dumps_up", func("up"))
        body = func("dumps_up")
        self.assertLess(body.index("dumps_exec mark"), body.index("compose up -d --wait --no-deps dumper"))


if __name__ == "__main__":
    unittest.main()
