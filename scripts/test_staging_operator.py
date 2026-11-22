"""The operator access record (P1-T27a; docs/reviews/p1-t27/design.md and, binding where they
differ, the "Orchestrator decision" of docs/reviews/p1-t27/attack.md; Q-K134..Q-K139).

What is checked here, with no Docker and no journal (``docker`` and ``logger`` are stubs on PATH,
and scripts/staging.sh runs for real against a throwaway stack folder):

- the grammar: ``staging.sh <cmd> [--operator N] [--reason T] [--hospital S|all] <args>``, the three
  consumed and never passed on; ``IHR_OPERATOR`` as the fallback; a subcommand's own --operator
  must name the same person;
- the tiers: the data tier (operator, reason, hospital) opens its session before anything runs;
  a job opens one at its first privileged call (a wrapper), with the reason ``job:<cmd>``; a
  command that reaches no wrapper writes nothing;
- the lines (start, scope, end, refused, late) exactly, with ``ses=`` and ``from=``; no value of the
  command line ever reaches them (option names only, values ``<v>``);
- the name and reason rules; fail closed when the journal cannot be written; break-glass for
  rollback, restore and hospital-restore only, with the pending lines shipped later as late;
- the session's values exported into every one-off and exec the wrappers start, and
  ``PGAPPNAME=op:<session>:<what>`` for psql and pg_dump;
- the end line after a failure, and errexit still aborting inside functions (the dispatcher runs
  as ``set +e; ( set -e; ... main ... ); rc=$?; set -e``, never under ``||``);
- statically: no privileged call outside the wrappers; deploy.sh passes the operator; the alert
  file and the reviewed list of service logins agree; the Postgres pins.

Run: python3 -m unittest scripts/test_staging_operator.py
"""

import json
import os
import random
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STAGING_SH_PATH = ROOT / "scripts" / "staging.sh"
STAGING_SH = STAGING_SH_PATH.read_text()
DEPLOY_SH = (ROOT / "scripts" / "deploy.sh").read_text()
ALERTS = ROOT / "deploy" / "logs" / "alerts" / "operator.yml"
SERVICE_LOGINS = ROOT / "deploy" / "postgres" / "service-logins.txt"
SETTINGS_FILE = ROOT / "deploy" / "postgres" / "server-settings.txt"

START_RE = re.compile(
    r'^ihr-operator: session=(?P<session>[0-9a-f]{12}) event=start tier=(?P<tier>data|job) '
    r'operator=(?P<operator>[a-z][a-z0-9.-]{1,31}) command=(?P<command>\S+) args=(?P<args>\S+) '
    r'hospital=(?P<hospital>\S+) ses=(?P<ses>[0-9]+|none) from=(?P<from>\S+) reason="(?P<reason>[^"]*)"$')
END_RE = re.compile(r'^ihr-operator: session=(?P<session>[0-9a-f]{12}) event=end '
                    r'outcome=(?P<outcome>ok|failed|interrupted) rc=(?P<rc>[0-9]+) seconds=[0-9]+$')
SCOPE_RE = re.compile(r'^ihr-operator: session=(?P<session>[0-9a-f]{12}) event=scope '
                      r'hospital=(?P<hospital>\S+) hospital_entry=(?P<entry>\S+)$')
REFUSED_RE = re.compile(r'^ihr-operator: event=refused why=(?P<why>[a-z-]+) command=(?P<command>\S+) '
                        r'operator=(?P<operator>\S+)$')

UUID = "6f1c2d3e-0000-4000-8000-000000000001"

# A stand-in for docker: every call is written to $STUB_LOG (one line, arguments joined by
# \x1f); the answers below are chosen by the test through the environment.
DOCKER_STUB = r'''#!/usr/bin/env bash
printf '%s\x1f' "$@" >> "$STUB_LOG"; printf '\n' >> "$STUB_LOG"
args=" $* "
if [[ "$args" == *" ihr_operator_open "* ]]; then
  [[ -z "${OPEN_ERR:-}" ]] || echo "$OPEN_ERR" >&2
  [[ -z "${OPEN_OUT:-}" ]] || printf '%s\n' "$OPEN_OUT"
  exit "${OPEN_RC:-0}"
fi
if [[ "$args" == *" ps -q "* ]]; then
  for s in ${RUNNING:-}; do [[ "$args" == *" ps -q $s "* ]] && echo "c0ffee-$s"; done
  exit 0
fi
if [[ "$args" == *" psql "* ]]; then printf '%s\n' "${PSQL_OUT:-}"; exit "${PSQL_RC:-0}"; fi
if [[ -n "${FAIL_ON:-}" && "$args" == *"$FAIL_ON"* ]]; then exit "${FAIL_RC:-3}"; fi
exit 0
'''
LOGGER_STUB = r'''#!/usr/bin/env bash
[[ -z "${LOGGER_FAIL:-}" ]] || { echo "logger: socket /dev/log: No such file or directory" >&2; exit 1; }
printf '%s\n' "${@: -1}" >> "$LOGGER_LOG"
'''


def func(name):
    return STAGING_SH.split(f"\n{name}() {{", 1)[1].split("\n}\n", 1)[0]


def op_block():
    """The operator record's functions and settings, as staging.sh defines them."""
    start = STAGING_SH.index("# ---- Operator access record (P1-T27a)")
    end = STAGING_SH.index("# ---- end of the operator access record")
    return STAGING_SH[start:end]


class Stack:
    """A throwaway stack folder, stub docker and logger, and a run of staging.sh against them."""

    def __init__(self, env_lines=("IHR_BACKEND_IMAGE=indiehr-backend:test",), local=False):
        self.tmp = tempfile.TemporaryDirectory(prefix="ihr-op-test.")
        self.dir = Path(self.tmp.name) / "staging"
        self.dir.mkdir()
        (self.dir / ".env").write_text("\n".join(env_lines) + "\n")
        (self.dir / "secrets").mkdir(mode=0o700)
        (self.dir / "secrets" / "keycloak.env").write_text("KC_BOOTSTRAP_ADMIN_CLIENT_ID=x\nKC_BOOTSTRAP_ADMIN_CLIENT_SECRET=y\n")
        if local:
            (self.dir / ".local").touch()
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        for name, text in (("docker", DOCKER_STUB), ("logger", LOGGER_STUB)):
            p = self.bin / name
            p.write_text(text)
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
        self.log = Path(self.tmp.name) / "docker.log"
        self.journal = Path(self.tmp.name) / "journal.log"
        self.log.touch()
        self.journal.touch()

    def run(self, *args, env=None, timeout=60):
        base = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": self.tmp.name, "IHR_STAGING_DIR": str(self.dir),
                "STUB_LOG": str(self.log), "LOGGER_LOG": str(self.journal), "TMPDIR": self.tmp.name}
        base.update(env or {})
        return subprocess.run(["bash", str(STAGING_SH_PATH), *args], env=base, capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)

    def run_shell(self, script, env=None, timeout=60):
        """``bash -c script`` with the same environment as run() (a parent of a staging.sh run)."""
        base = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": self.tmp.name, "IHR_STAGING_DIR": str(self.dir),
                "STUB_LOG": str(self.log), "LOGGER_LOG": str(self.journal), "TMPDIR": self.tmp.name}
        base.update(env or {})
        return subprocess.run(["bash", "-c", script], env=base, capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)

    def lines(self):
        return [ln for ln in self.journal.read_text().splitlines() if ln]

    def calls(self):
        return [ln.rstrip("\x1f").split("\x1f") for ln in self.log.read_text().splitlines() if ln]

    def runs(self):
        """docker compose calls that run or exec something (never ps, up, config...)."""
        out = []
        for c in self.calls():
            if c[:1] == ["compose"] and ("run" in c or "exec" in c):
                out.append(c)
            elif c[:1] == ["exec"]:
                out.append(c)
        return out

    def close(self):
        self.tmp.cleanup()


def env_of(call):
    """The -e NAME=value pairs of a docker compose run/exec call."""
    found = {}
    for a, b in zip(call, call[1:]):
        if a == "-e" and "=" in b:
            k, v = b.split("=", 1)
            found[k] = v
    return found


class Base(unittest.TestCase):
    def setUp(self):
        self.stack = Stack()
        self.addCleanup(self.stack.close)


class DataTier(Base):
    def test_a_manage_shell_session_start_scope_end_and_the_one_off(self):
        out = self.stack.run("manage", "--operator", "alice", "--reason", "INC-12 bed fix", "--hospital", "alpha",
                             "shell", "-c", "print(Patient.objects.get(phone='9876543210'))",
                             env={"OPEN_OUT": "opened=alpha skipped=-"})
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = self.stack.lines()
        self.assertEqual(len(lines), 3, lines)
        start = START_RE.match(lines[0])
        self.assertTrue(start, lines[0])
        self.assertEqual(start["tier"], "data")
        self.assertEqual(start["operator"], "alice")
        self.assertEqual(start["command"], "manage:shell")
        self.assertEqual(start["args"], "-c,<v>")
        self.assertEqual(start["hospital"], "alpha")
        self.assertEqual(start["from"], "local")
        self.assertEqual(start["reason"], "INC-12 bed fix")
        scope = SCOPE_RE.match(lines[1])
        self.assertTrue(scope, lines[1])
        self.assertEqual((scope["session"], scope["hospital"], scope["entry"]), (start["session"], "alpha", "opened:alpha"))
        end = END_RE.match(lines[2])
        self.assertTrue(end, lines[2])
        self.assertEqual((end["session"], end["outcome"], end["rc"]), (start["session"], "ok", "0"))
        for ln in lines:
            self.assertNotIn("9876543210", ln)
            self.assertNotIn("Patient", ln)
        runs = self.stack.runs()
        self.assertEqual(len(runs), 2, runs)
        opener, shell = runs
        i = opener.index("ihr_operator_open")
        self.assertEqual(opener[i + 1:], ["--session", start["session"], "--operator", "alice", "--scope", "alpha",
                                          "--reason", "INC-12 bed fix", "--tier", "data"])
        self.assertIn("--profile", opener)
        self.assertIn("admin", opener)
        # The staging-level options are consumed, never passed on to manage.py
        j = shell.index("manage.py")
        self.assertEqual(shell[j + 1:], ["shell", "-c", "print(Patient.objects.get(phone='9876543210'))"])
        self.assertEqual(env_of(shell), {
            "IHR_OPERATOR": "alice", "IHR_OPERATOR_SESSION": start["session"], "IHR_OPERATOR_SCOPE": "alpha",
            "IHR_OPERATOR_REASON": "INC-12 bed fix", "IHR_OPERATOR_TIER": "data",
            "PGAPPNAME": f"op:{start['session']}:admin"})

    def test_the_operator_comes_from_the_environment_when_not_given(self):
        out = self.stack.run("manage", "--reason", "INC-12 bed fix", "--hospital", "all", "check",
                             env={"IHR_OPERATOR": "bob", "OPEN_OUT": "opened=alpha,beta skipped=gamma:held"})
        self.assertEqual(out.returncode, 0, out.stderr)
        start = START_RE.match(self.stack.lines()[0])
        self.assertEqual((start["operator"], start["hospital"], start["args"]), ("bob", "all", "-"))
        self.assertEqual(SCOPE_RE.match(self.stack.lines()[1])["entry"], "opened:alpha,opened:beta,skipped:gamma:held")
        # The option given on the command line wins over the environment
        self.stack.journal.write_text("")
        self.stack.run("manage", "--operator", "alice", "--reason", "INC-12 bed fix", "--hospital", "all", "check",
                       env={"IHR_OPERATOR": "bob", "OPEN_OUT": "opened=- skipped=-"})
        self.assertEqual(START_RE.match(self.stack.lines()[0])["operator"], "alice")

    def test_refusals_name_why_and_run_nothing(self):
        good = ["--operator", "alice", "--reason", "INC-12 bed fix", "--hospital", "alpha"]
        cases = (
            ([a for a in good if a not in ("--operator", "alice")], {}, "no-operator", "none"),
            (["--operator", "ihr"] + good[2:], {}, "shared-operator", "ihr"),
            (["--operator", "root"] + good[2:], {}, "shared-operator", "root"),
            (["--operator", "postgres"] + good[2:], {}, "shared-operator", "postgres"),
            (["--operator", "admin"] + good[2:], {}, "shared-operator", "admin"),
            (["--operator", "Ravi Kumar 9876543210"] + good[2:], {}, "bad-operator", "<v>"),
            (["--operator", "x"] + good[2:], {}, "bad-operator", "<v>"),
            (good[:2] + good[4:], {}, "no-reason", "alice"),
            (good[:2] + ["--reason", "call 9876543210 back"] + good[4:], {}, "bad-reason", "alice"),
            (good[:2] + ["--reason", "run 123456"] + good[4:], {}, "bad-reason", "alice"),
            (good[:2] + ["--reason", 'fix "x"'] + good[4:], {}, "bad-reason", "alice"),
            (good[:2] + ["--reason", "a=b test"] + good[4:], {}, "bad-reason", "alice"),
            (good[:2] + ["--reason", "ok"] + good[4:], {}, "bad-reason", "alice"),
            (good[:2] + ["--reason", "x" * 121] + good[4:], {}, "bad-reason", "alice"),
            (good[:4], {}, "no-hospital", "alice"),
            (good[:4] + ["--hospital", "Alpha!"], {}, "bad-hospital", "alice"),
            (good + ["--operator", "bob"], {}, "bad-option", "alice"),
        )
        for argv, env, why, who in cases:
            with self.subTest(why=why, argv=argv):
                self.stack.journal.write_text("")
                self.stack.log.write_text("")
                out = self.stack.run("manage", *argv, "shell", env=env)
                self.assertNotEqual(out.returncode, 0)
                lines = self.stack.lines()
                self.assertEqual(len(lines), 1, lines)
                m = REFUSED_RE.match(lines[0])
                self.assertTrue(m, lines[0])
                self.assertEqual((m["why"], m["command"], m["operator"]), (why, "manage", who))
                self.assertEqual(self.stack.runs(), [])
                self.assertNotIn("9876543210", lines[0])

    def test_hospital_root_takes_its_slug_and_its_own_options(self):
        out = self.stack.run("hospital-root", "--slug", "alpha", "place", "dr.x", "--operator", "alice",
                             "--reason", "INC-3 root fix", env={"OPEN_OUT": "opened=alpha skipped=-"})
        self.assertEqual(out.returncode, 0, out.stderr)
        start = START_RE.match(self.stack.lines()[0])
        self.assertEqual((start["command"], start["hospital"], start["operator"], start["reason"]),
                         ("hospital-root", "alpha", "alice", "INC-3 root fix"))
        cmd = self.stack.runs()[-1]
        self.assertEqual(cmd[cmd.index("ihr_hospital_root") + 1:],
                         ["--slug", "alpha", "place", "dr.x", "--operator", "alice", "--reason", "INC-3 root fix"])
        # The staging-level form: the command still gets the session's operator and reason
        self.stack.log.write_text("")
        self.stack.journal.write_text("")
        out = self.stack.run("hospital-root", "--operator", "alice", "--reason", "INC-3 root fix", "--slug", "alpha",
                             "show", env={"OPEN_OUT": "opened=alpha skipped=-"})
        self.assertEqual(out.returncode, 0, out.stderr)
        cmd = self.stack.runs()[-1]
        self.assertEqual(cmd[cmd.index("ihr_hospital_root") + 1:],
                         ["--slug", "alpha", "show", "--operator", "alice", "--reason", "INC-3 root fix"])

    def test_a_subcommands_own_operator_must_be_the_same_person(self):
        out = self.stack.run("hospital-root", "--operator", "alice", "--reason", "INC-3 root fix", "--slug", "alpha",
                             "place", "dr.x", "--operator", "bob")
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(REFUSED_RE.match(self.stack.lines()[0])["why"], "conflict-operator")
        self.assertEqual(self.stack.runs(), [])
        # ...and a scope that disagrees with the slug
        self.stack.journal.write_text("")
        out = self.stack.run("hospital-root", "--operator", "alice", "--reason", "INC-3 root fix", "--hospital", "beta",
                             "--slug", "alpha", "show")
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(REFUSED_RE.match(self.stack.lines()[0])["why"], "bad-hospital")

    def test_logs_are_data_tier_for_all_hospitals_without_hospital_entries(self):
        out = self.stack.run("logs", "--operator", "alice", "--reason", "INC-7 look at errors", "backend")
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = self.stack.lines()
        start = START_RE.match(lines[0])
        self.assertEqual((start["tier"], start["hospital"], start["command"], start["args"]), ("data", "all", "logs", "<v>"))
        self.assertTrue(END_RE.match(lines[-1]))
        self.assertFalse(any("ihr_operator_open" in c for call in self.stack.calls() for c in call))
        out = self.stack.run("logs", "--operator", "alice", "backend")
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(REFUSED_RE.match(self.stack.lines()[-1])["why"], "no-reason")

    def test_restore_drill_keep_and_supersede_export_are_data_tier(self):
        for argv in (["restore-drill", "--keep"], ["audit-supersede", "export", "abc"]):
            with self.subTest(argv=argv):
                self.stack.journal.write_text("")
                out = self.stack.run(argv[0], "--operator", "alice", *argv[1:])
                self.assertNotEqual(out.returncode, 0)
                self.assertEqual(REFUSED_RE.match(self.stack.lines()[0])["why"], "no-reason")

    def test_dumps_get_names_the_hospital_from_its_bucket_path(self):
        stack = Stack(env_lines=("IHR_BACKEND_IMAGE=indiehr-backend:test", "IHR_DUMPS=s3"))
        self.addCleanup(stack.close)
        out = stack.run("dumps-get", "--operator", "alice", "--reason", "DPDP return R-12", UUID, "latest", "dump",
                        env={"RUNNING": "db dumper", "PSQL_OUT": "alpha", "OPEN_OUT": "opened=alpha skipped=-"})
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = stack.lines()
        start = START_RE.match(lines[0])
        self.assertEqual((start["hospital"], start["command"], start["args"]), (UUID, "dumps-get", "<v>,<v>,<v>"))
        self.assertEqual(SCOPE_RE.match(lines[1])["entry"], "opened:alpha")
        opener = [c for c in stack.runs() if "ihr_operator_open" in c][0]
        self.assertEqual(opener[opener.index("--scope") + 1], "alpha")
        psql = [c for c in stack.runs() if "psql" in c][0]
        self.assertEqual(env_of(psql)["PGAPPNAME"], f"op:{start['session']}:psql")
        # No database here (a lost server): the start and end lines are the record
        stack.journal.write_text("")
        out = stack.run("dumps-get", "--operator", "alice", "--reason", "DPDP return R-12", UUID, "latest", "dump",
                        env={"RUNNING": "dumper"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(SCOPE_RE.match(stack.lines()[1])["entry"], f"skipped:{UUID}:no-database")
        # A uuid-shaped path only
        stack.journal.write_text("")
        out = stack.run("dumps-get", "--operator", "alice", "--reason", "DPDP return R-12", "../x", "latest", "dump")
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(REFUSED_RE.match(stack.lines()[0])["why"], "bad-hospital")


class ScopeOpen(Base):
    ARGS = ("manage", "--operator", "alice", "--reason", "INC-12 bed fix", "--hospital", "alpha", "check")

    def test_an_older_build_without_the_command_is_recorded_not_refused(self):
        out = self.stack.run(*self.ARGS, env={"OPEN_RC": "1", "OPEN_ERR": "Unknown command: 'ihr_operator_open'"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(SCOPE_RE.match(self.stack.lines()[1])["entry"], "skipped:all:old-build")
        self.assertEqual(len(self.stack.runs()), 2)

    def test_any_other_failure_refuses_and_ends_the_session(self):
        for env in ({"OPEN_RC": "1", "OPEN_ERR": "connection refused"}, {"OPEN_RC": "0", "OPEN_OUT": "hello"},
                    {"OPEN_RC": "0", "OPEN_OUT": "opened=alpha skipped=-\nopened=beta skipped=-"},
                    {"OPEN_RC": "0", "OPEN_OUT": "opened=ALPHA skipped=-"}):
            with self.subTest(env=env):
                self.stack.journal.write_text("")
                self.stack.log.write_text("")
                out = self.stack.run(*self.ARGS, env=env)
                self.assertNotEqual(out.returncode, 0)
                lines = self.stack.lines()
                self.assertTrue(START_RE.match(lines[0]))
                self.assertEqual(REFUSED_RE.match(lines[1])["why"], "scope-failed")
                end = END_RE.match(lines[2])
                self.assertEqual((end["outcome"], end["rc"]), ("failed", "1"))
                self.assertEqual(len(self.stack.runs()), 1)  # the opener only, never the command

    def test_a_hospital_that_failed_is_recorded_before_the_refusal(self):
        """Review A7: entries already written stay (autocommit); the opener answers them with the
        failed hospital and exits 3; the scope line records both, then the session is refused."""
        stack = Stack(env_lines=("IHR_BACKEND_IMAGE=indiehr-backend:test", "IHR_DUMPS=s3"))
        self.addCleanup(stack.close)
        out = stack.run("dumps-now", env={"IHR_OPERATOR": "alice", "OPEN_RC": "3",
                                          "OPEN_OUT": "opened=alpha,gamma skipped=beta:failed",
                                          "OPEN_ERR": "the operator entry could not be written in beta", "RUNNING": "dumper"})
        self.assertNotEqual(out.returncode, 0)
        lines = stack.lines()
        self.assertTrue(START_RE.match(lines[0]), lines)
        scope = SCOPE_RE.match(lines[1])
        self.assertIsNotNone(scope, lines)
        self.assertEqual(scope["entry"], "opened:alpha,opened:gamma,skipped:beta:failed")
        self.assertEqual(REFUSED_RE.match(lines[2])["why"], "scope-failed")
        self.assertEqual(END_RE.match(lines[3])["outcome"], "failed")
        self.assertIn("could not be written in beta", out.stderr)
        self.assertEqual([c for c in stack.runs() if "ihr_operator_open" not in c], [])
        # exit 3 with no answer (or a malformed one) is still refused with no scope line
        stack.journal.write_text("")
        out = stack.run("dumps-now", env={"IHR_OPERATOR": "alice", "OPEN_RC": "3", "OPEN_OUT": "hello",
                                          "RUNNING": "dumper"})
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual([REFUSED_RE.match(ln)["why"] for ln in stack.lines() if REFUSED_RE.match(ln)],
                         ["scope-failed"])
        self.assertFalse(any(SCOPE_RE.match(ln) for ln in stack.lines()))


class JobTier(Base):
    def test_a_job_opens_at_its_first_privileged_call_with_a_job_reason(self):
        out = self.stack.run("hospital-restore-status", env={"IHR_OPERATOR": "alice"})
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = self.stack.lines()
        self.assertEqual(len(lines), 2, lines)
        start = START_RE.match(lines[0])
        self.assertEqual((start["tier"], start["hospital"], start["reason"], start["command"], start["args"]),
                         ("job", "none", "job:hospital-restore-status", "hospital-restore-status", "-"))
        runs = self.stack.runs()
        self.assertEqual(len(runs), 2)
        for r in runs:
            self.assertIn("migrate", r)
            self.assertEqual(env_of(r)["IHR_OPERATOR_TIER"], "job")
            self.assertEqual(env_of(r)["IHR_OPERATOR_SCOPE"], "none")
            self.assertEqual(env_of(r)["IHR_OPERATOR_SESSION"], start["session"])
        self.assertTrue(END_RE.match(lines[1]))

    def test_a_job_without_an_operator_is_refused_before_its_first_call(self):
        out = self.stack.run("hospital-restore-status")
        self.assertNotEqual(out.returncode, 0)
        lines = self.stack.lines()
        self.assertEqual(len(lines), 1, lines)
        m = REFUSED_RE.match(lines[0])
        self.assertEqual((m["why"], m["command"]), ("no-operator", "hospital-restore-status"))
        self.assertEqual(self.stack.runs(), [])

    def test_a_failure_still_ends_the_session_and_errexit_still_aborts(self):
        """restore_manage --status fails: under set -e the --parked call never runs, and the end
        line carries the exit code."""
        out = self.stack.run("hospital-restore-status", env={"IHR_OPERATOR": "alice", "FAIL_ON": "--status", "FAIL_RC": "3"})
        self.assertEqual(out.returncode, 3, out.stderr)
        runs = self.stack.runs()
        self.assertEqual(len(runs), 1, runs)
        end = END_RE.match(self.stack.lines()[-1])
        self.assertEqual((end["outcome"], end["rc"]), ("failed", "3"))

    def test_the_jobs_that_open_every_hospital_open_their_scope_first(self):
        stack = Stack(env_lines=("IHR_BACKEND_IMAGE=indiehr-backend:test", "IHR_DUMPS=s3"))
        self.addCleanup(stack.close)
        self.stack = stack
        out = self.stack.run("dumps-now", env={"IHR_OPERATOR": "alice", "OPEN_OUT": "opened=alpha skipped=-",
                                               "RUNNING": "dumper"})
        lines = self.stack.lines()
        start = START_RE.match(lines[0])
        self.assertEqual((start["tier"], start["hospital"]), ("job", "all"), out.stderr)
        self.assertEqual(SCOPE_RE.match(lines[1])["hospital"], "all")
        opener = self.stack.runs()[0]
        self.assertEqual(opener[opener.index("--tier") + 1], "job")
        self.assertEqual(opener[opener.index("--reason") + 1], "job:dumps-now")
        dumper = self.stack.runs()[1]
        self.assertIn("dumper", dumper)
        self.assertEqual(env_of(dumper)["PGAPPNAME"], f"op:{start['session']}:exec")
        self.assertTrue(END_RE.match(lines[-1]))

    def test_a_refused_scope_stops_every_later_call(self):
        stack = Stack(env_lines=("IHR_BACKEND_IMAGE=indiehr-backend:test", "IHR_DUMPS=s3"))
        self.addCleanup(stack.close)
        out = stack.run("dumps-now", env={"IHR_OPERATOR": "alice", "OPEN_RC": "1", "OPEN_ERR": "boom",
                                          "RUNNING": "dumper"})
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual([c for c in stack.runs() if "dumper" in c and "ihr_operator_open" not in c], [])
        self.assertEqual(REFUSED_RE.match(stack.lines()[1])["why"], "scope-failed")
        end = END_RE.match(stack.lines()[-1])
        self.assertEqual(end["outcome"], "failed")

    def test_a_command_that_reaches_no_wrapper_writes_nothing(self):
        out = self.stack.run("ps", env={"IHR_OPERATOR": "alice"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.stack.lines(), [])

    def test_an_exported_session_is_never_reused(self):
        out = self.stack.run("hospital-restore-status", env={"IHR_OPERATOR": "alice",
                                                             "IHR_OPERATOR_SESSION": "abcdefabcdef"})
        self.assertEqual(out.returncode, 0, out.stderr)
        start = START_RE.match(self.stack.lines()[0])
        self.assertNotEqual(start["session"], "abcdefabcdef")
        self.assertEqual(env_of(self.stack.runs()[0])["IHR_OPERATOR_SESSION"], start["session"])

    def test_ses_and_from_come_from_the_login(self):
        out = self.stack.run("hospital-restore-status", env={"IHR_OPERATOR": "alice",
                                                             "SSH_CONNECTION": "203.0.113.9 51234 10.0.0.5 22"})
        self.assertEqual(out.returncode, 0, out.stderr)
        start = START_RE.match(self.stack.lines()[0])
        self.assertEqual(start["from"], "203.0.113.9")
        try:
            want = Path("/proc/self/sessionid").read_text().strip()
        except OSError:
            want = "none"
        self.assertEqual(start["ses"], "none" if want in ("", "4294967295") else want)
        self.stack.journal.write_text("")
        self.stack.run("hospital-restore-status", env={"IHR_OPERATOR": "alice", "SSH_CONNECTION": "x;y 1 2 3"})
        self.assertEqual(START_RE.match(self.stack.lines()[0])["from"], "local")


class Journal(Base):
    def test_an_unwritable_journal_refuses(self):
        out = self.stack.run("manage", "--operator", "alice", "--reason", "INC-12 bed fix", "--hospital", "alpha",
                             "check", env={"LOGGER_FAIL": "1"})
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("event=refused why=journal-unwritable", out.stderr)
        self.assertEqual(self.stack.runs(), [])
        out = self.stack.run("hospital-restore-status", env={"IHR_OPERATOR": "alice", "LOGGER_FAIL": "1",
                                                             "IHR_BREAK_GLASS": "INC-9 journal full"})
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(self.stack.runs(), [])

    def test_stderr_only_in_ci_or_a_local_proof(self):
        out = self.stack.run("hospital-restore-status", env={"IHR_OPERATOR": "alice", "IHR_OPERATOR_LOCAL": "stderr",
                                                             "GITHUB_ACTIONS": "true"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.stack.lines(), [])
        self.assertRegex(out.stderr, r"(?m)^ihr-operator: session=[0-9a-f]{12} event=start tier=job ")
        self.assertRegex(out.stderr, r"(?m)^ihr-operator: session=[0-9a-f]{12} event=end outcome=ok ")
        # On a server it is ignored: the journal gets the lines
        out = self.stack.run("hospital-restore-status", env={"IHR_OPERATOR": "alice", "IHR_OPERATOR_LOCAL": "stderr"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(len(self.stack.lines()), 2)
        local = Stack(local=True)
        self.addCleanup(local.close)
        out = local.run("hospital-restore-status", env={"IHR_OPERATOR": "alice", "IHR_OPERATOR_LOCAL": "stderr"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(local.lines(), [])

    def test_stderr_never_on_a_bootstrapped_host(self):
        """Review S4: on a host deploy/host/bootstrap.sh set up (its marker), neither
        GITHUB_ACTIONS nor a .local file in a folder the caller names sends the lines to stderr."""
        self.assertIn('\nOP_HOST_MARKER="/etc/indiehr/installed-at"', op_block())
        with tempfile.TemporaryDirectory() as t:
            for env, local in (({"GITHUB_ACTIONS": "true"}, False), ({}, True)):
                with self.subTest(env=env, local=local):
                    mark = 'touch "$DIR/.local"; ' if local else 'rm -f "$DIR/.local"; '
                    script = 'OP_HOST_MARKER="$T/installed-at"; touch "$OP_HOST_MARKER"; ' + mark + "op_dest"
                    out = run_block(script, {"PATH": "/usr/bin:/bin", "T": t, "IHR_OPERATOR_LOCAL": "stderr", **env})
                    self.assertEqual(out.returncode, 0, out.stderr)
                    self.assertEqual(out.stdout.strip(), "journal")
                    self.assertIn("bootstrapped", out.stderr)
            # without the marker, CI keeps its stderr mode
            out = run_block('OP_HOST_MARKER="$T/none"; op_dest',
                            {"PATH": "/usr/bin:/bin", "T": t, "IHR_OPERATOR_LOCAL": "stderr", "GITHUB_ACTIONS": "true"})
            self.assertEqual(out.stdout.strip(), "stderr")


class LongLines(unittest.TestCase):
    """Review A1: logger cuts a message at 1 KiB by default and still exits 0; the scope line of
    an `all` session at 200 hospitals is several KiB. With the real logger (--no-act --stderr)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        self.capture = Path(self.tmp.name) / "logged"
        real = subprocess.run(["bash", "-c", "command -v logger"], capture_output=True, text=True).stdout.strip()
        if not real:
            self.skipTest("no logger here")
        shim = self.bin / "logger"
        shim.write_text(f'#!/bin/sh\nexec {real} --no-act --stderr "$@" 2>>"{self.capture}"\n')
        shim.chmod(0o755)

    def write(self, line):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "T": self.tmp.name, "LINE": line}
        return run_block('OP_DEST=journal; op_write "$LINE"', env)

    def test_the_scope_line_of_200_hospitals_reaches_the_journal_whole(self):
        slugs = [f"h{i:03d}" + "x" * 27 for i in range(200)]  # 31 characters each
        entry = ",".join([f"opened:{s}" for s in slugs[:190]] + [f"skipped:{s}:old-schema" for s in slugs[190:]])
        line = f"ihr-operator: session=0123456789ab event=scope hospital=all hospital_entry={entry}"
        self.assertGreater(len(line), 7000)  # well past logger's default 1 KiB
        out = self.write(line)
        self.assertEqual(out.returncode, 0, out.stderr)
        logged = self.capture.read_text()
        self.assertTrue(logged.rstrip("\n").endswith(f"skipped:{slugs[-1]}:old-schema"), logged[-200:])
        self.assertIn(line, logged)
        self.assertRegex(func("op_write"), r"logger --size \d+ ")

    def test_a_line_past_the_limit_is_refused_never_cut(self):
        out = self.write("ihr-operator: session=0123456789ab event=scope hospital=all hospital_entry=" + "a" * 70000)
        self.assertNotEqual(out.returncode, 0)
        self.assertFalse(self.capture.exists() and self.capture.read_text())


class Nested(Base):
    """Review O2/O3: the steps a rollback runs as nested staging.sh calls join its session (its
    operator and break-glass) instead of opening their own; only from a live staging.sh that is
    an ancestor of the step, never from a file the caller names."""

    def state(self, pid, session="abcdef012345", dest="journal"):
        p = Path(self.stack.tmp.name) / "parent-state"
        p.write_text(f"session={session}\nname=alice\nreason=job:rollback\nscope=none\ntier=job\ndest={dest}\n"
                     f"t0=1\nscoped=0\nrefused=0\npid={pid}\n")
        p.chmod(0o600)
        return p

    def test_a_step_joins_its_ancestors_session(self):
        p = self.state("$$")
        # (exit $? after it: bash would otherwise exec the last command in place of itself)
        script = (f'sed -i "s/^pid=.*/pid=$$/" "{p}"; '
                  f'IHR_OPERATOR_PARENT_STATE="{p}" bash "{STAGING_SH_PATH}" hospital-restore-status; exit $?')
        out = self.stack.run_shell(script, env={"LOGGER_FAIL": "1"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.stack.lines(), [])  # no start or end line of its own
        runs = self.stack.runs()
        self.assertEqual(len(runs), 2, runs)
        for r in runs:
            self.assertEqual(env_of(r)["IHR_OPERATOR_SESSION"], "abcdef012345")
            self.assertEqual(env_of(r)["IHR_OPERATOR"], "alice")
        self.assertTrue(Path(p).exists(), "the parent's state is the parent's to remove")

    def test_a_data_tier_step_never_joins_a_session(self):
        # verifier's note after the review: a joined step skips op_begin, so a data-tier command
        # must open its own session (its own --reason, hospital scope and entries), never ride a job's
        p = self.state("$$")
        script = (f'sed -i "s/^pid=.*/pid=$$/" "{p}"; '
                  f'IHR_OPERATOR_PARENT_STATE="{p}" bash "{STAGING_SH_PATH}" logs --operator bob backend; exit $?')
        out = self.stack.run_shell(script)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("not joined", out.stderr)
        self.assertEqual(REFUSED_RE.match(self.stack.lines()[-1])["why"], "no-reason")

    def test_op_child_wraps_only_job_tier_steps(self):
        calls = re.findall(r'op_child "\$ROOT/scripts/staging.sh" (\S+)(?: (\S+))?', STAGING_SH)
        self.assertTrue(calls)
        for cmd, arg in calls:
            with self.subTest(cmd=cmd):
                self.assertNotIn(cmd, ("manage", "logs", "logs-archive-get", "dumps-get", "hospital-root",
                                       "restore-drill-psql"))
                self.assertFalse(cmd == "audit-supersede" and arg == "export")
        self.assertIn('op_tier "${@:2}"', func("op_child"))

    def test_a_state_file_of_no_live_ancestor_is_not_joined(self):
        for pid in ("1", "999999999", "x"):
            with self.subTest(pid=pid):
                self.stack.journal.write_text("")
                self.stack.log.write_text("")
                p = self.state(pid)
                out = self.stack.run("hospital-restore-status",
                                     env={"IHR_OPERATOR": "bob", "IHR_OPERATOR_PARENT_STATE": str(p)})
                self.assertEqual(out.returncode, 0, out.stderr)
                start = START_RE.match(self.stack.lines()[0])
                self.assertEqual(start["operator"], "bob")
                self.assertNotEqual(start["session"], "abcdef012345")
                self.assertIn("not joined", out.stderr)


def run_block(script, env=None, cwd=None):
    """The operator block with small stand-ins, then ``script``."""
    base = f'''set -euo pipefail
die() {{ echo "$*" >&2; exit 1; }}
env_get() {{ :; }}
DIR="$T/staging"; OUT="$DIR"; ROOT="{ROOT}"
mkdir -p "$DIR"
{op_block()}
'''
    return subprocess.run(["bash", "-c", base + script], capture_output=True, text=True, env=env, timeout=30)


class BreakGlass(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        p = self.bin / "logger"
        p.write_text(LOGGER_STUB)
        p.chmod(0o755)
        self.journal = Path(self.tmp.name) / "journal.log"
        self.journal.touch()

    def env(self, **kw):
        return {"PATH": f"{self.bin}:/usr/bin:/bin", "T": self.tmp.name, "LOGGER_LOG": str(self.journal), **kw}

    def test_rollback_breaks_glass_then_the_next_session_ships_late_lines(self):
        script = '''OP_STATE="$(mktemp)"; op_parse rollback; op_need psql; echo "OPEN $OP_SESSION $OP_REASON"; op_end 0'''
        out = run_block(script, self.env(IHR_OPERATOR="alice", LOGGER_FAIL="1", IHR_BREAK_GLASS="INC-9 journal full"))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertRegex(out.stdout, r"OPEN [0-9a-f]{12} break-glass: INC-9 journal full")
        pending = (Path(self.tmp.name) / "staging" / "operator-pending.log")
        kept = pending.read_text().splitlines()
        self.assertEqual(len(kept), 2, kept)
        self.assertIn(" event=start tier=job ", kept[0])
        self.assertIn(" event=end outcome=ok ", kept[1])
        self.assertEqual(oct(pending.stat().st_mode & 0o777), "0o600")
        # The next recorded session: late lines first, then its own start; the file is gone
        script = '''OP_STATE="$(mktemp)"; op_parse hospital-restore-status; op_need psql; op_end 0'''
        out = run_block(script, self.env(IHR_OPERATOR="alice"))
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = self.journal.read_text().splitlines()
        self.assertRegex(lines[0], r"^ihr-operator: event=late at=\S+ session=[0-9a-f]{12} was=start tier=job ")
        self.assertRegex(lines[1], r"^ihr-operator: event=late at=\S+ session=[0-9a-f]{12} was=end outcome=ok ")
        self.assertTrue(START_RE.match(lines[2]), lines[2])
        self.assertFalse(pending.exists())

    def test_never_for_the_data_tier_or_another_job(self):
        for cmd in ("hospital-restore-status", "release"):
            with self.subTest(cmd=cmd):
                out = run_block(f'OP_STATE="$(mktemp)"; op_parse {cmd}; op_need psql; echo opened',
                                self.env(IHR_OPERATOR="alice", LOGGER_FAIL="1", IHR_BREAK_GLASS="INC-9 journal full"))
                self.assertNotEqual(out.returncode, 0)
                self.assertNotIn("opened", out.stdout)
        out = run_block('OP_STATE="$(mktemp)"; op_parse manage --reason "INC-1 fix" --hospital alpha shell; op_begin data; echo opened',
                        self.env(IHR_OPERATOR="alice", LOGGER_FAIL="1", IHR_BREAK_GLASS="INC-9 journal full"))
        self.assertNotEqual(out.returncode, 0)
        self.assertNotIn("opened", out.stdout)

    def test_a_wrapper_without_a_session_or_a_run_dies(self):
        out = run_block('OP_STATE=""; admin_run admin python manage.py check', self.env())
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("operator session", out.stderr)


class Shape(unittest.TestCase):
    TOKEN = re.compile(r"^(--[A-Za-z][A-Za-z0-9-]{0,40}(=<v>)?|-[A-Za-z](=<v>|<v>)?|<v>)$")

    def shape(self, args):
        out = run_block('op_shape "$@"', {"PATH": "/usr/bin:/bin", "T": tempfile.gettempdir()})
        return out

    def test_values_never_reach_the_line(self):
        rnd = random.Random(27)
        pool = ["9876543210", "+91 98765 43210", "2345 6789 0123", "alpha", UUID, "SELECT * FROM emr_patient",
                "/srv/indiehr/x.sql", "2026-11-22", "Ravi Kumar", "a=b", "--slug", "-c", "--date=2026-11-22",
                "--phone=9876543210", "-pS3cretToken", "-Uadmin", "-dpatientdb", "ravi@example.in", "'; DROP TABLE x;--", "--", "-", "", "--x y"]
        argvs = [[rnd.choice(pool) for _ in range(rnd.randint(0, 8))] for _ in range(200)]
        script = "\n".join("op_shape " + " ".join("'" + a.replace("'", "'\\''") + "'" for a in argv) for argv in argvs)
        out = run_block(script, {"PATH": "/usr/bin:/bin", "T": tempfile.gettempdir()})
        self.assertEqual(out.returncode, 0, out.stderr)
        got = out.stdout.splitlines()
        self.assertEqual(len(got), 200)
        for argv, shape in zip(argvs, got):
            with self.subTest(argv=argv):
                if not argv:
                    self.assertEqual(shape, "-")
                    continue
                self.assertLessEqual(len(shape), 203)
                for tok in shape.removesuffix("...").split(","):
                    if shape.endswith("...") and tok == shape.removesuffix("...").split(",")[-1]:
                        continue
                    self.assertRegex(tok, self.TOKEN)
                for bad in ("9876543210", "alpha", "emr_patient", "Ravi", "2026", "@", "DROP", "S3cret", "admin", "patientdb"):
                    self.assertNotIn(bad, shape)

    def test_option_names_kept(self):
        out = run_block("op_shape --slug alpha -c 'x' --date=2026-11-22 show", {"PATH": "/usr/bin:/bin", "T": tempfile.gettempdir()})
        self.assertEqual(out.stdout.strip(), "--slug,<v>,-c,<v>,--date=<v>,<v>")

    def test_the_command_word_passes_the_same_rule(self):
        out = run_block('op_word manage shell; op_word manage "print(1)"; op_word dumps-get; op_word "9876543210"',
                        {"PATH": "/usr/bin:/bin", "T": tempfile.gettempdir()})
        self.assertEqual(out.stdout.split(), ["manage:shell", "manage:<v>", "dumps-get", "<v>"])

    def test_a_session_id_is_never_all_digits(self):
        out = run_block('for i in $(seq 300); do op_new_session; done', {"PATH": "/usr/bin:/bin", "T": tempfile.gettempdir()})
        ids = out.stdout.split()
        self.assertEqual(len(ids), 300)
        self.assertEqual(len(set(ids)), 300)
        for s in ids:
            self.assertRegex(s, r"^[0-9a-f]{12}$")
            self.assertRegex(s, r"[a-f]")


class Static(unittest.TestCase):
    def code_lines(self):
        """staging.sh's lines that are code (not comments), with the wrapper bodies left out."""
        text = STAGING_SH
        for name in ("admin_run", "migrate_run", "app_exec", "pg_su", "audit_supersede_run"):
            text = text.replace(f"\n{name}() {{" + func(name) + "\n}\n", "\n")
        return [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]

    def test_the_dispatcher_runs_main_in_a_subshell_without_an_or_list(self):
        tail = [ln for ln in STAGING_SH.rstrip().splitlines() if ln.strip() and not ln.lstrip().startswith("#")][-4:]
        self.assertEqual(tail, [
            'op_prepare "$@"',
            "set +e; ( set -e; op_open_eager; main \"${OP_ARGV[@]}\" ); rc=$?; set -e",
            'op_end "$rc"; op_tidy',
            'exit "$rc"',
        ])
        self.assertNotRegex(STAGING_SH, r"\(\s*(set -e;\s*)?[^)]*main\b[^)]*\)\s*\|\|")
        self.assertIn("\nmain() {\n  case \"${1:-}\" in\n", STAGING_SH)

    def test_no_privileged_call_outside_the_wrappers(self):
        privileged = {"db", "backend", "backchannel", "celery-worker", "celery-beat", "provisioner", "staff-worker",
                      "backup", "dumper", "logs-archive", "admin", "migrate", "audit-supersede", "s3-tool"}
        bad = []
        for ln in self.code_lines():
            s = ln.strip()
            if re.search(r"--profile admin run", s):
                bad.append(s)
            for m in re.finditer(r"\bcompose (?:--profile \S+ )?(run|exec)\b((?:\s+(?:-[A-Za-z-]+(?:=\S+)?|-e \S+|-v \S+|--entrypoint \S+|\$\{[^}]+\}|\"\$\{[^}]+\}\"))*)\s+(\"?[\w$-]+\"?)", s):
                svc = m.group(3).strip('"')
                if svc in privileged or svc.startswith("$"):
                    bad.append(s)
            if re.search(r"\bdocker exec\b[^#]*\b(psql|pg_dump)\b", s) and not re.match(r"^(echo|die)", s):
                bad.append(s)
            if re.search(r"(^|[;&|(]\s*|\$\(\s*)(psql|pg_dump)\s", s):
                bad.append(s)
        self.assertEqual(bad, [])

    def test_the_wrappers_need_a_session(self):
        for name in ("admin_run", "migrate_run", "app_exec", "pg_su", "audit_supersede_run"):
            with self.subTest(wrapper=name):
                body = func(name)
                self.assertIn("op_need ", body)
                first_call = min(i for i in (body.find("compose "), body.find("docker ")) if i >= 0)
                self.assertLess(body.index("op_need "), first_call)

    def test_every_data_tier_label_is_in_the_dispatcher(self):
        main = func("main")
        for label in ("manage", "logs", "logs-archive-get", "dumps-get", "hospital-root", "restore-drill",
                      "restore-drill-psql", "audit-supersede"):
            with self.subTest(label=label):
                self.assertRegex(main, rf"(?m)^  {re.escape(label)}\)")
                self.assertIn(label, func("op_tier"))

    def test_no_session_needs_an_operator_list_or_a_key_field(self):
        self.assertNotIn("IHR_OPERATORS", STAGING_SH)
        self.assertNotIn(" key=", op_block())

    def test_deploy_passes_the_operator_on_every_remote_staging_call(self):
        """Every staging.sh call deploy.sh makes on the server carries the operator (the calls
        on this machine before step 5 are none-tier: secrets, keys, the reader check)."""
        section = DEPLOY_SH.split("# ---- 5. Start and check", 1)[1].split("# ---- 6.", 1)[0]
        code = "\n".join(ln for ln in section.splitlines()
                         if not re.match(r"\s*(#|log |\|\| die |die )", ln))
        calls = [m.start() for m in re.finditer(r"scripts/staging\.sh", code)]
        self.assertGreaterEqual(len(calls), 7)
        for i in calls:
            with self.subTest(call=code[i:i + 40]):
                self.assertEqual(code[i - len("IHR_OPERATOR=$OPERATOR "):i], "IHR_OPERATOR=$OPERATOR ")
        self.assertIn("--operator) OPERATOR=", DEPLOY_SH)
        self.assertIn('OPERATOR="${OPERATOR:-${IHR_OPERATOR:-}}"', DEPLOY_SH)

    def test_deploy_refuses_without_an_operator(self):
        out = subprocess.run(["bash", str(ROOT / "scripts" / "deploy.sh"), "--host", "ihr@192.0.2.1", "--no-build"],
                             capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": tempfile.gettempdir()},
                             timeout=60, cwd=ROOT)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("--operator", out.stderr)
        out = subprocess.run(["bash", str(ROOT / "scripts" / "deploy.sh"), "--host", "ihr@192.0.2.1", "--no-build",
                              "--operator", "ihr"], capture_output=True, text=True,
                             env={"PATH": "/usr/bin:/bin", "HOME": tempfile.gettempdir()}, timeout=60, cwd=ROOT)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("not a person", out.stderr)


def service_logins():
    rows = []
    for ln in SERVICE_LOGINS.read_text().splitlines():
        if not ln.strip() or ln.startswith("#"):
            continue
        service, role, app, _why = ln.split("\t", 3)
        rows.append((service, role, "" if app == "-" else app))
    return rows


class Alerts(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.safe_load(ALERTS.read_text())
        self.rules = {r["alert"]: r for g in self.doc["groups"] for r in g["rules"]}

    def test_the_rules_and_their_severities(self):
        self.assertEqual(set(self.rules), {"PrivilegedLoginUnrecorded", "OperatorSessionRefused",
                                           "OperatorSessionNoEnd", "KeycloakAdminCredentialChange"})
        sev = {k: v["labels"]["severity"] for k, v in self.rules.items()}
        self.assertEqual(sev, {"PrivilegedLoginUnrecorded": "critical", "OperatorSessionRefused": "warning",
                               "OperatorSessionNoEnd": "warning", "KeycloakAdminCredentialChange": "critical"})
        for g in self.doc["groups"]:
            self.assertEqual((g["type"], g["interval"]), ("vlogs", "1m"))

    def test_the_sanctioned_clauses_are_the_reviewed_list(self):
        expr = self.rules["PrivilegedLoginUnrecorded"]["expr"]
        got = re.findall(r'\(service:="([^"]*)" user:="([^"]*)" app:="([^"]*)"\)', expr)
        self.assertEqual(sorted(got), sorted(service_logins()))
        self.assertGreater(len(got), 10)

    def test_an_op_name_counts_only_while_its_session_is_open(self):
        expr = self.rules["PrivilegedLoginUnrecorded"]["expr"]
        self.assertIn('"event=start"', expr)
        self.assertIn('"event=end"', expr)
        self.assertIn("_time:24h offset 10m", expr)
        self.assertIn("source:=container", expr)
        self.assertIn('source:=host service:="ihr-operator"', expr)
        self.assertIn("JournalSuppressed", self.rules["PrivilegedLoginUnrecorded"]["annotations"]["action"])
        self.assertIn("mistake", self.rules["PrivilegedLoginUnrecorded"]["annotations"]["summary"]
                      + self.rules["PrivilegedLoginUnrecorded"]["annotations"]["action"])

    def test_the_line_formats_in_the_rules_match_what_staging_writes(self):
        text = ALERTS.read_text()
        for word in ("event=refused why=", '"event=start" or "was=start"', '"event=end" or "was=end"',
                     "(?:event|was)=start ", "(?:event|was)=end "):
            self.assertIn(word, text)
        self.assertIn("ihr-operator: event=refused why=", op_block())


class Postgres(unittest.TestCase):
    def test_the_logging_pins(self):
        lines = [ln for ln in SETTINGS_FILE.read_text().splitlines() if ln and not ln.startswith("#")]
        for want in ("log_connections=authorization", "log_disconnections=on", "log_min_messages=warning",
                     "log_destination=stderr"):
            self.assertIn(want, lines)
        compose = yaml.safe_load((ROOT / "deploy" / "staging" / "compose.yaml").read_text().replace("!reset", "").replace("!override", ""))
        cmd = compose["services"]["db"]["command"]
        for want in ("log_connections=authorization", "log_disconnections=on", "log_min_messages=warning",
                     "log_destination=stderr"):
            self.assertIn(want, cmd)
        health = compose["services"]["db"]["healthcheck"]["test"]
        self.assertIn("PGAPPNAME=ihr-health pg_isready", " ".join(health))

    def test_the_drift_check_names_the_new_settings(self):
        fake = '''set -u
die() { echo "DIE: $*" >&2; exit 1; }
env_get() { :; }
pg_su() { printf 'max_locks_per_transaction=256\\neffective_cache_size=3GB\\nlog_connections=%s\\nlog_disconnections=on\\nlog_min_messages=%s\\nlog_destination=stderr\\nshared_buffers=512MB\\nstored_database_settings=0\\n' "$LC" "$LM"; }
app_exec() { printf 'Filesystem 1024-blocks Used Available Capacity Mounted\\ntmpfs 262144 0 262144 0%% /dev/shm\\n'; }
'''
        block = "PG_SERVER_SETTINGS=()" + STAGING_SH.split("PG_SERVER_SETTINGS=()", 1)[1].split("unset _line", 1)[0]
        script = fake + f'ROOT="{ROOT}"\n' + block + "\n" + "to_kb() {" + func("to_kb") + "\n}\n" \
            + "db_server_check() {" + func("db_server_check") + "\n}\ndb_server_check\n"
        ok = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin", "LC": "authorization", "LM": "warning"})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        for env, text in (({"LC": "", "LM": "warning"}, "log_connections is"), ({"LC": "authorization", "LM": "panic"},
                                                                                 "log_min_messages is panic")):
            out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", **env})
            self.assertNotEqual(out.returncode, 0)
            self.assertIn(text, out.stderr)


class CiWorkflows(unittest.TestCase):
    def test_ci_calls_only_commands_that_need_no_session(self):
        for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            for m in re.finditer(r"scripts/staging\.sh ([a-z-]+)", wf.read_text()):
                with self.subTest(workflow=wf.name, command=m.group(1)):
                    self.assertIn(m.group(1), ("acme-proof",))
        self.assertNotIn("op_need", func("acme_proof"))


class ReviewFixes(Base):
    """Findings of the P1-T27 review (fixer round): each test failed before its fix."""

    # S1: the kept restore-drill copy has a wrapped way in, and no hint names the raw one
    def test_restore_drill_psql_is_the_wrapped_way_into_the_kept_copy(self):
        out = self.stack.run("restore-drill-psql", "--operator", "alice", "-c", "select 1")
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(REFUSED_RE.match(self.stack.lines()[0])["why"], "no-reason")
        self.stack.journal.write_text("")
        out = self.stack.run("restore-drill-psql", "--operator", "alice", "--reason", "INC-4 look at the copy",
                             "-c", "select 1", env={"OPEN_OUT": "opened=alpha skipped=-"})
        self.assertEqual(out.returncode, 0, out.stderr)
        start = START_RE.match(self.stack.lines()[0])
        self.assertEqual((start["tier"], start["hospital"], start["command"]), ("data", "all", "restore-drill-psql"))
        execs = [c for c in self.stack.calls() if c[:1] == ["exec"] and "ihr-restore-drill" in c]
        self.assertEqual(len(execs), 1, self.stack.calls())
        self.assertEqual(env_of(execs[0])["PGAPPNAME"], f"op:{start['session']}:psql")
        self.assertEqual(execs[0][execs[0].index("psql") + 1:execs[0].index("psql") + 3], ["-U", "postgres"])

    def test_the_kept_copy_hint_names_no_unwrapped_psql(self):
        body = func("restore_drill")
        self.assertNotIn("docker exec -it", body)
        self.assertIn("restore-drill-psql", body)

    # S5: a value attached to a short option never reaches the line
    def test_a_short_option_with_its_value_attached_keeps_only_its_letter(self):
        out = run_block("op_shape -pS3cretToken -c x -Uadmin --slug -dpatientdb --date=2026-11-22",
                        {"PATH": "/usr/bin:/bin", "T": tempfile.gettempdir()})
        self.assertEqual(out.stdout.strip(), "-p<v>,-c,<v>,-U<v>,--slug,-d<v>,--date=<v>")
        for bad in ("S3cret", "admin", "patientdb"):
            self.assertNotIn(bad, out.stdout)

    # S7: no docker run that carries a database address outside a session
    def test_a_docker_run_with_a_database_address_is_inside_a_session(self):
        text = STAGING_SH.replace("\\\n", " ")
        bodies = {m.group(1): m.group(0) for m in re.finditer(r"(?ms)^(\w+)\(\) \{.*?^\}\n", text)}
        found = []
        for name, body in bodies.items():
            for stmt in re.findall(r"docker run [^\n]*", body):
                if "-e DATABASE_URL" in stmt:
                    found.append(name)
                    self.assertIn("op_need", body, name)
                    self.assertIn("OP_ENV[@]", stmt, name)
                    self.assertIn('--operator "$OP_NAME"', stmt, name)
        self.assertIn("hospital_restore_drill", found)

    # S8, O10: the commands the docs and alert texts give run under the grammar
    DATA_WORDS = re.compile(r"staging\.sh (manage|logs|logs-archive-get|dumps-get|hospital-root|audit-supersede export|restore-drill-psql)(?![\w-])")
    DOC_FILES = ("deploy/staging/README.md", "deploy/host/README.md")

    def doc_lines(self):
        files = [ROOT / f for f in self.DOC_FILES]
        for sub in ("deploy/logs/alerts", "deploy/monitoring/prometheus/rules", "deploy/monitoring/prometheus/tests"):
            files += sorted((ROOT / sub).glob("*.yml"))
        files.append(STAGING_SH_PATH)
        for f in files:
            for n, ln in enumerate(f.read_text().splitlines(), 1):
                yield f.relative_to(ROOT), n, ln

    def test_no_documented_data_tier_command_lacks_a_reason(self):
        bad = []
        for f, n, ln in self.doc_lines():
            if not self.DATA_WORDS.search(ln):
                continue
            if "--reason" in ln or re.search(r"never `?(scripts/)?staging\.sh manage|one-offs \(`staging\.sh manage|staging\.sh manage\W*$|usage:", ln):
                continue
            bad.append(f"{f}:{n}: {ln.strip()[:120]}")
        self.assertEqual(bad, [])

    def test_no_documented_deploy_or_fleet_call_lacks_its_operator(self):
        bad = []
        for f, n, ln in self.doc_lines():
            if re.search(r"scripts/deploy\.sh .*(--host|--local-proof)", ln) and "--operator" not in ln \
                    and "--build-only" not in ln and "--no-start" not in ln:
                bad.append(f"{f}:{n}: {ln.strip()[:120]}")
        self.assertEqual(bad, [])

    # S9: a line shipped late still closes (or opens) its session
    def test_the_alerts_read_late_lines(self):
        doc = yaml.safe_load(ALERTS.read_text())
        rules = {r["alert"]: r["expr"] for g in doc["groups"] for r in g["rules"]}
        for name in ("PrivilegedLoginUnrecorded", "OperatorSessionNoEnd"):
            self.assertIn("was=start", rules[name], name)
            self.assertIn("was=end", rules[name], name)

    # O4: fleet's own --operator is the session's operator (same person), appended when missing
    def fleet(self, *args, env=None):
        base = {"RUNNING": "db", "PSQL_OUT": "split"}
        base.update(env or {})
        return self.stack.run("fleet", *args, env=base)

    def fleet_calls(self):
        return [c for c in self.stack.runs() if "ihr_fleet_run" in c]

    def test_fleets_own_operator_opens_the_session_without_an_environment_name(self):
        out = self.fleet("hold", "--operator", "bob")
        self.assertEqual(out.returncode, 0, out.stderr)
        start = START_RE.match(self.stack.lines()[0])
        self.assertEqual(start["operator"], "bob")
        calls = self.fleet_calls()
        self.assertEqual(len(calls), 1, self.stack.runs())
        self.assertEqual(calls[0].count("--operator"), 1)
        self.assertEqual(calls[0][calls[0].index("--operator") + 1], "bob")

    def test_fleets_own_operator_must_be_the_sessions(self):
        out = self.fleet("hold", "--operator", "bob", env={"IHR_OPERATOR": "alice"})
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(REFUSED_RE.match(self.stack.lines()[-1])["why"], "conflict-operator")
        self.assertEqual(self.fleet_calls(), [])

    def test_fleet_without_its_own_operator_gets_the_sessions(self):
        for args in (("hold",), ("--operator", "bob", "hold")):
            with self.subTest(args=args):
                self.stack.log.write_text("")
                self.stack.journal.write_text("")
                out = self.fleet(*args, env={"IHR_OPERATOR": "bob"})
                self.assertEqual(out.returncode, 0, out.stderr)
                calls = self.fleet_calls()
                self.assertEqual(len(calls), 1, self.stack.runs())
                self.assertEqual(calls[0][calls[0].index("--operator") + 1], "bob")
                self.assertEqual(calls[0].count("--operator"), 1)

    # O5: a refused operator stops the run before anything changes
    def test_a_conflicting_operator_stops_supersede_before_anything_runs(self):
        uid = "6f1c2d3e-0000-4000-8000-0000000000aa"
        for args in (("--reason", "INC-9 export", "export", uid, "--operator", "bob"),
                     ("close", uid, "--reason", "INC-9 cannot load", "--operator", "bob"),
                     ("load", uid, "--operator", "bob")):
            with self.subTest(args=args):
                self.stack.log.write_text("")
                self.stack.journal.write_text("")
                out = self.stack.run("audit-supersede", *args, env={"IHR_OPERATOR": "alice", "OPEN_OUT": "opened=alpha skipped=-"})
                self.assertNotEqual(out.returncode, 0)
                self.assertEqual([c for c in self.stack.lines() if "event=refused" in c and "conflict-operator" in c].__len__(), 1,
                                 self.stack.lines())
                heavy = [c for c in self.stack.calls() if any(w in c for w in ("stop", "up", "ihr_audit_supersede", "backup"))]
                self.assertEqual(heavy, [])

    def test_no_operator_substitution_sits_inside_an_argument_list(self):
        for n, ln in enumerate(STAGING_SH.splitlines(), 1):
            if "$(rel_operator" in ln and not ln.lstrip().startswith("#"):
                self.assertRegex(ln, r'(^\s*|[;&)]\s*|\bthen\s+|\{\s*)\w+="\$\(rel_operator', f"staging.sh:{n}: {ln.strip()}")

    # O11: the pending file is never committed by accident
    def test_the_pending_file_is_git_ignored(self):
        out = subprocess.run(["git", "check-ignore", "-q", "deploy/staging/operator-pending.log"], cwd=ROOT)
        self.assertEqual(out.returncode, 0)

    # O6: the guard tests run at commit time
    def test_the_guard_tests_have_pre_commit_hooks(self):
        cfg = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
        hooks = {h["id"]: h for r in cfg["repos"] for h in r["hooks"]}
        for test, paths in (
            ("test_staging_operator.py", ("scripts/staging.sh", "scripts/deploy.sh", "deploy/postgres/service-logins.txt",
                                          "deploy/logs/alerts/operator.yml", "deploy/staging/compose.yaml",
                                          "scripts/test_staging_operator.py")),
            ("test_staging_postgres.py", ("deploy/postgres/server-settings.txt", "scripts/staging.sh",
                                          "scripts/test_staging_postgres.py")),
            ("test_bootstrap_static.py", ("deploy/monitoring/host-metrics.sh", "deploy/monitoring/prometheus/rules/host-audit.yml",
                                          "deploy/logs/alerts/operator.yml", "scripts/staging.sh", "docs/VERSIONS.md",
                                          "deploy/logs/fluent-bit/redact.lua", "deploy/host/bootstrap.sh")),
        ):
            hook = next((h for h in hooks.values() if test in h.get("entry", "")), None)
            self.assertIsNotNone(hook, test)
            for path in paths:
                self.assertRegex(path, hook["files"], f"{test}: {path}")

    # O9: the operator alerts are compared by their labels, not only counted
    def test_check_logs_names_the_operator_alerts_labels(self):
        text = (ROOT / "scripts" / "check-logs.py").read_text()
        self.assertRegex(text, r'"PrivilegedLoginUnrecorded": "app"')
        self.assertRegex(text, r'"OperatorSessionRefused": "why"')


class LiveProofFindings(Base):
    """The P1-T27 live proof (docs/reviews/p1-t27/live-proof.md, F1-F6) against PG 18.6."""

    README = (ROOT / "deploy" / "staging" / "README.md").read_text()

    def section(self):
        return self.README.split("## Operator access record (P1-T27)", 1)[1].split("\n## ", 1)[0]

    # F1-F3: services that logged in unnamed are named, never listed unnamed
    def test_backup_and_keycloak_sessions_are_listed_by_their_new_names(self):
        rows = service_logins()
        self.assertIn(("db", "postgres", "ihr-backup"), rows)
        self.assertIn(("db", "keycloak", "ihr-keycloak"), rows)
        for service, role, app in rows:
            if role in ("postgres", "keycloak", "ihr_owner", "ihr_app"):
                with self.subTest(role=role, app=app):
                    # an empty name, psql or pg_isready would let a careless raw session through
                    self.assertNotIn(app, ("", "psql", "pg_isready", "PostgreSQL JDBC Driver"))
                    self.assertFalse(app.startswith("pgBackRest"))

    # F4: no container environment names an operator's `docker exec ... psql`
    def test_no_database_container_environment_sets_a_session_name(self):
        for f in ("compose.yaml", "compose.backup.yaml", "compose.dumps.yaml"):
            doc = yaml.safe_load((ROOT / "deploy" / "staging" / f).read_text()
                                 .replace("!reset", "").replace("!override", ""))
            for name, svc in (doc.get("services") or {}).items():
                env = svc.get("environment") or {}
                with self.subTest(file=f, service=name):
                    self.assertNotIn("PGAPPNAME", env if isinstance(env, dict) else " ".join(env))

    def test_the_first_start_logins_are_documented(self):
        text = self.section()
        self.assertIn("first `up` on a new server", text)
        self.assertIn("docker-entrypoint-initdb.d", text)

    # F5: the full audit check runs where the reader's key is, recorded, never in the admin one-off
    def test_audit_verify_runs_in_the_audit_watch_or_the_backend(self):
        for running, where in (("audit-watch", "audit-watch"), ("", "backend")):
            with self.subTest(running=running):
                self.stack.log.write_text("")
                self.stack.journal.write_text("")
                out = self.stack.run("audit-verify", "--operator", "alice", "--slug", "alpha",
                                     env={"RUNNING": running, "OPEN_OUT": "opened=alpha skipped=-"})
                self.assertEqual(out.returncode, 0, out.stderr)
                runs = [c for c in self.stack.runs() if "ihr_audit_verify" in c]
                self.assertEqual(len(runs), 1, runs)
                call = runs[0]
                self.assertIn("exec", call)
                self.assertEqual(call[call.index("python") - 1], where)
                self.assertEqual(call[call.index("ihr_audit_verify") + 1:], ["--slug", "alpha"])
                start = START_RE.match(self.stack.lines()[0])
                self.assertEqual((start["tier"], start["hospital"], start["command"]), ("job", "alpha", "audit-verify"))
                self.assertEqual(env_of(call)["IHR_OPERATOR_SESSION"], start["session"])
        self.stack.journal.write_text("")
        out = self.stack.run("audit-verify", "--operator", "alice", "--all",
                             env={"RUNNING": "audit-watch", "OPEN_OUT": "opened=alpha,beta skipped=-"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(START_RE.match(self.stack.lines()[0])["hospital"], "all")

    def test_audit_verify_only_reads(self):
        for flag in ("--seal", "--anchor"):
            with self.subTest(flag=flag):
                self.stack.log.write_text("")
                out = self.stack.run("audit-verify", "--operator", "alice", "--all", flag, env={"RUNNING": "audit-watch"})
                self.assertNotEqual(out.returncode, 0)
                self.assertFalse([c for c in self.stack.runs() if "ihr_audit_verify" in c])

    def test_no_document_sends_ihr_audit_verify_through_the_admin_one_off(self):
        bad = [ln.strip() for ln in self.README.splitlines()
               if re.search(r"staging\.sh manage\b[^`]*ihr_audit_verify", ln)]
        self.assertEqual(bad, [])
        self.assertRegex(self.README, r"staging\.sh audit-verify --operator <you> --slug <")

    # F6: the admin image has no psql: dbshell is not a recorded path, shell -c is
    def test_dbshell_is_not_offered_as_a_recorded_path(self):
        for ln in self.README.splitlines():
            if "dbshell" in ln:
                with self.subTest(line=ln.strip()[:80]):
                    self.assertIn("no `psql`", ln)
        self.assertIn("shell -c", self.section())


if __name__ == "__main__":
    unittest.main()
