"""scripts/load-test.py (P1-T23 groundwork): the guard that keeps it on synthetic, local or
staging hosts, the one-time code it computes (RFC 6238's test vectors), the report's maths.

Run: python3 -m unittest scripts/test_load_test.py
"""

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("load_test", ROOT / "scripts" / "load-test.py")
load_test = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(load_test)


class Guard(unittest.TestCase):
    def test_only_local_and_the_staging_suffix(self):
        self.assertTrue(load_test.allowed("alpha.localhost", ""))
        self.assertTrue(load_test.allowed("auth.localhost", ""))
        self.assertTrue(load_test.allowed("alpha.app.example.in", "app.example.in"))
        for host in ("example.com", "alpha.app.example.in.evil.com", "localhost.evil.com", "alpha.app.example.in"):
            self.assertFalse(load_test.allowed(host, ""), host)

    def test_the_command_refuses_a_foreign_suffix_before_anything_else(self):
        out = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "load-test.py"), "--users", "/nonexistent",
             "--hospitals", "a", "--suffix", "example.com"],
            capture_output=True, text=True, check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("refusing suffix example.com", out.stderr)


class Code(unittest.TestCase):
    SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # RFC 6238's "12345678901234567890"

    def test_rfc6238_vectors(self):
        self.assertEqual(load_test.totp(self.SECRET, now=59), "287082")
        self.assertEqual(load_test.totp(self.SECRET, now=1111111109), "081804")
        self.assertEqual(load_test.totp(self.SECRET.lower(), now=1234567890), "005924")


class Maths(unittest.TestCase):
    def test_percentiles(self):
        values = [i / 100 for i in range(1, 101)]
        self.assertEqual(load_test.percentile(values, 50), 0.51)
        self.assertEqual(load_test.percentile(values, 99), 1.0)
        self.assertIsNone(load_test.percentile([], 50))

    def test_the_report_counts_errors_and_refusals_apart(self):
        class Args:
            storm = 0
        runner = load_test.Runner.__new__(load_test.Runner)
        runner.results = {"patients": [(200, 0.1), (403, 0.1), (500, 0.2), (0, 40.0)]}
        runner.storm, runner.signins, runner.signin_failures = [], [1.0], []
        report = runner.report()
        self.assertEqual(report["errors"], 2)
        self.assertEqual(report["kinds"]["patients"]["refused_4xx"], 1)
        self.assertEqual(report["error_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
