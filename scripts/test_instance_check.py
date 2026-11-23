"""E3: deploy.sh sends a folder only to the server that says it is that instance (P1-T32, ADR-020 §3;
design docs/reviews/p1-t32/design.md §5.2, attack S2, D2, M11).

``scripts/instance_check.py <env> <suffix> <file>`` reads the one remote read deploy.sh makes of
the server (``INSTANCE``, ``DEPLOYED`` and the server ``.env``'s two keys, ending in ``#end``).
Exit 0: every source present agrees with the folder. Exit 1: refused, the reason on stdout.
Exit 2: a wrong command line. There is no "new server ok" and no override.

Run: python3 -m unittest scripts/test_instance_check.py
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "scripts" / "instance_check.py"

STG = ("staging", "app.stg.example.in")
PROD = ("production-1", "app.example.in")
PROOF = ("proof", "app.ihr-proof.in")


def instance(env="staging", suffix="app.stg.example.in", owner="root", mode="644", extra=()):
    lines = ["# Managed by deploy/host/bootstrap.sh --instance (ADR-020 §3 E3)"]
    if env is not None:
        lines.append(f"environment={env}")
    if suffix is not None:
        lines.append(f"suffix={suffix}")
    return (owner, mode, lines + list(extra))


def deployed(env="staging", suffix="app.stg.example.in", extra=()):
    lines = ["git_sha=0123456789abcdef0123456789abcdef01234567", "release_tag=r1"]
    if env is not None:
        lines.append(f"environment={env}")
    if suffix is not None:
        lines.append(f"suffix={suffix}")
    return ("ihr", "644", lines + list(extra))


def server_env(env="staging", suffix="app.stg.example.in"):
    lines = []
    if env is not None:
        lines.append(f"IHR_ENVIRONMENT={env}")
    if suffix is not None:
        lines.append(f"IHR_TENANT_DOMAIN_SUFFIX={suffix}")
    return ("ihr", "600", lines)


def answer(inst=None, dep=None, env=None, end=True) -> str:
    """What the remote read prints: a header per source, its lines prefixed with |, then #end."""
    out = []
    for name, source in (("instance", inst), ("deployed", dep), ("env", env)):
        if source is None:
            out.append(f"#{name} missing")
        else:
            owner, mode, lines = source
            out.append(f"#{name} present {owner} {mode}")
            out.extend("|" + line for line in lines)
    if end:
        out.append("#end")
    return "\n".join(out) + "\n"


def run(folder, text, extra_args=None):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "instance"
        path.write_text(text)
        args = list(folder) + [str(path)] if extra_args is None else extra_args
        return subprocess.run([sys.executable, "-I", str(CHECK), *args],
                              capture_output=True, text=True, timeout=30)


class CommandLineTests(unittest.TestCase):
    def test_fixed_argv(self):
        for args in ([], ["staging"], ["staging", "app.stg.example.in"],
                     ["staging", "app.stg.example.in", "f", "extra"]):
            with self.subTest(args=args):
                self.assertEqual(run(STG, "", extra_args=args).returncode, 2)

    def test_the_folders_own_values_must_be_valid(self):
        for env, suffix in (("Staging", "app.stg.example.in"), ("production-01", "app.example.in"),
                            ("", "app.stg.example.in"), ("staging", "not a domain"),
                            ("staging", ""), ("staging\n", "app.stg.example.in")):
            with self.subTest(env=env, suffix=suffix):
                self.assertEqual(run((env, suffix), answer(instance())).returncode, 2)

    def test_a_missing_file_is_refused(self):
        out = subprocess.run([sys.executable, "-I", str(CHECK), *STG, "/nonexistent/instance"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 1)
        self.assertIn("cannot read the server", out.stdout)


class SentinelTests(unittest.TestCase):
    """D2: the read must have finished; an empty answer is never "a new server"."""

    def assert_unreadable(self, text):
        out = run(STG, text)
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("cannot read the server", out.stdout)

    def test_no_sentinel(self):
        self.assert_unreadable(answer(instance(), end=False))

    def test_empty_answer(self):
        self.assert_unreadable("")

    def test_sentinel_not_last(self):
        self.assert_unreadable(answer(instance()) + "#instance missing\n")

    def test_a_section_missing_or_twice(self):
        self.assert_unreadable("#instance missing\n#deployed missing\n#end\n")
        self.assert_unreadable("#instance missing\n#instance missing\n#deployed missing\n#env missing\n#end\n")

    def test_a_stray_line(self):
        text = answer(instance()).replace("#deployed missing", "Permission denied\n#deployed missing")
        self.assert_unreadable(text)

    def test_a_header_without_owner_and_mode(self):
        self.assert_unreadable(answer(instance()).replace("#instance present root 644", "#instance present"))


class InstanceFileTests(unittest.TestCase):
    """S2: host identity. INSTANCE, root's, decides; nothing else stands in for it."""

    def test_ok_when_instance_agrees(self):
        out = run(STG, answer(instance()))
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertTrue(out.stdout.startswith("ok"), out.stdout)

    def test_missing_instance_is_refused_even_on_an_empty_server(self):
        # the old "no DEPLOYED and no .env: new server, ok" row is gone
        for dep, env in ((None, None), (deployed(), server_env())):
            with self.subTest(dep=dep is not None):
                out = run(STG, answer(None, dep, env))
                self.assertEqual(out.returncode, 1)
                self.assertIn("bootstrap.sh --instance", out.stdout)
                self.assertIn("staging at app.stg.example.in", out.stdout)

    def test_production_folder_never_goes_to_an_unmarked_server(self):
        out = run(PROD, answer(None))
        self.assertEqual(out.returncode, 1)
        self.assertIn("bootstrap.sh --instance", out.stdout)

    def test_another_environment_is_refused_and_both_are_named(self):
        out = run(PROD, answer(instance()))
        self.assertEqual(out.returncode, 1)
        self.assertIn("this server runs staging at app.stg.example.in", out.stdout)
        self.assertIn("the folder is production-1 at app.example.in", out.stdout)
        self.assertIn("an instance is never deployed over another (ADR-020 §3 E3)", out.stdout)

    def test_another_suffix_is_refused(self):
        out = run(("staging", "app.stg2.example.in"), answer(instance()))
        self.assertEqual(out.returncode, 1)
        self.assertIn("this server runs staging at app.stg.example.in", out.stdout)

    def test_a_proof_folder_never_goes_over_a_real_instance(self):
        out = run(PROOF, answer(instance()))
        self.assertEqual(out.returncode, 1)

    def test_instance_must_be_roots_and_not_writable_by_others(self):
        for owner, mode in (("ihr", "644"), ("root", "664"), ("root", "646"), ("root", "777"),
                            ("UNKNOWN", "644")):
            with self.subTest(owner=owner, mode=mode):
                out = run(STG, answer(instance(owner=owner, mode=mode)))
                self.assertEqual(out.returncode, 1, out.stdout)
                self.assertIn("root", out.stdout)

    def test_a_deploy_user_instance_counts_only_for_a_proof(self):
        # deploy.sh --local-proof writes proof on the throwaway VM over SSH (the deploy user's)
        out = run(PROOF, answer(instance(*PROOF, owner="ihr", mode="644")))
        self.assertEqual(out.returncode, 0, out.stdout)
        out = run(PROD, answer(instance(*PROOF, owner="ihr", mode="644")))
        self.assertEqual(out.returncode, 1)

    def test_an_environment_without_a_suffix_and_the_reverse(self):
        for env, suffix in (("staging", None), (None, "app.stg.example.in"), (None, None)):
            with self.subTest(env=env, suffix=suffix):
                out = run(STG, answer(instance(env, suffix)))
                self.assertEqual(out.returncode, 1, out.stdout)

    def test_malformed_values_fail_closed(self):
        for env in ("Staging", '"staging"', "staging ", "staging\r", "production-01", "prod"):
            with self.subTest(env=env):
                out = run(STG, answer(instance(env)))
                self.assertEqual(out.returncode, 1, out.stdout)
                self.assertNotIn("\r", out.stdout)

    def test_two_different_values_in_one_source(self):
        out = run(STG, answer(instance(extra=["environment=production-1"])))
        self.assertEqual(out.returncode, 1)
        out = run(STG, answer(instance(extra=["suffix=app.example.in"])))
        self.assertEqual(out.returncode, 1)

    def test_an_unknown_line_in_instance(self):
        out = run(STG, answer(instance(extra=["override=yes"])))
        self.assertEqual(out.returncode, 1)


class SourcesAgreeTests(unittest.TestCase):
    """M11: every source present (INSTANCE, DEPLOYED, the server .env) must agree."""

    def test_all_three_agree(self):
        self.assertEqual(run(STG, answer(instance(), deployed(), server_env())).returncode, 0)

    def test_deployed_says_x_and_env_says_y(self):
        out = run(STG, answer(instance(), deployed(), server_env(*PROD)))
        self.assertEqual(out.returncode, 1)
        self.assertIn("production-1 at app.example.in", out.stdout)
        self.assertIn("the folder is staging at app.stg.example.in", out.stdout)
        out = run(STG, answer(instance(), deployed(*PROD), server_env()))
        self.assertEqual(out.returncode, 1)
        self.assertIn("DEPLOYED", out.stdout)

    def test_deployed_suffix_differs(self):
        out = run(STG, answer(instance(), deployed(suffix="app.other.example.in")))
        self.assertEqual(out.returncode, 1)
        self.assertIn("app.other.example.in", out.stdout)

    def test_deployed_environment_without_suffix(self):
        out = run(STG, answer(instance(), deployed(suffix=None)))
        self.assertEqual(out.returncode, 1)

    def test_empty_deployed_is_refused(self):
        empty = ("ihr", "644", [])
        for env in (server_env(), None):
            with self.subTest(env=env is not None):
                out = run(STG, answer(instance(), empty, env))
                self.assertEqual(out.returncode, 1)
                self.assertIn("DEPLOYED", out.stdout)

    def test_deployed_naming_nothing_is_refused(self):
        out = run(STG, answer(instance(), deployed(None, None)))
        self.assertEqual(out.returncode, 1)

    def test_legacy_deployed_needs_instance_and_must_agree(self):
        # a deploy before E3 recorded no environment: INSTANCE decides; the folder's label is
        # never taken from the folder alone
        self.assertEqual(run(STG, answer(instance(), deployed(env=None))).returncode, 0)
        out = run(STG, answer(None, deployed(env=None), server_env(env=None)))
        self.assertEqual(out.returncode, 1)
        self.assertIn("bootstrap.sh --instance", out.stdout)
        out = run(STG, answer(instance(), deployed(env=None, suffix="app.other.example.in")))
        self.assertEqual(out.returncode, 1)
        self.assertIn("this server runs unknown at app.other.example.in", out.stdout)

    def test_server_env(self):
        self.assertEqual(run(STG, answer(instance(), None, server_env())).returncode, 0)
        # a server .env from before E1: no IHR_ENVIRONMENT, the suffix still has to agree
        self.assertEqual(run(STG, answer(instance(), None, server_env(env=None))).returncode, 0)
        for env in (server_env(suffix=None), server_env(None, None),
                    server_env(suffix="app.other.example.in"), server_env("production-1")):
            with self.subTest(env=env):
                self.assertEqual(run(STG, answer(instance(), None, env)).returncode, 1)

    def test_proof_round_trip(self):
        self.assertEqual(run(PROOF, answer(instance(*PROOF), deployed(*PROOF), server_env(*PROOF))).returncode, 0)


if __name__ == "__main__":
    unittest.main()
