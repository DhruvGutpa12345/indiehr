"""scripts/check-upstream-drift.sh (P1-T17): pin ages, warning and failure thresholds. No network
(DRIFT_SKIP_FETCH), a temporary lock file, a fixed date."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check-upstream-drift.sh"


class UpstreamDrift(unittest.TestCase):
    def run_with(self, backend, frontend, today):
        with tempfile.NamedTemporaryFile("w", suffix=".lock", delete=False) as lock:
            lock.write(f"backend.date={backend}\nfrontend.date={frontend}\n")
        self.addCleanup(os.unlink, lock.name)
        env = {**os.environ, "DRIFT_LOCK": lock.name, "DRIFT_TODAY": today, "DRIFT_SKIP_FETCH": "1"}
        env.pop("GITHUB_STEP_SUMMARY", None)
        return subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True, check=False)

    def test_fresh_pins_pass_quietly(self):
        run = self.run_with("2026-11-01", "2026-11-02", "2026-11-13")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("backend pin 2026-11-01 is 12 day(s) old", run.stdout)
        self.assertNotIn("::warning::", run.stdout)

    def test_a_month_old_pin_warns(self):
        run = self.run_with("2026-10-01", "2026-11-02", "2026-11-13")
        self.assertEqual(run.returncode, 0)
        self.assertIn("::warning::backend pin 2026-10-01 is 43 day(s) old", run.stdout)

    def test_a_pin_over_sixty_days_fails(self):
        run = self.run_with("2026-11-01", "2026-09-01", "2026-11-13")
        self.assertEqual(run.returncode, 1)
        self.assertIn("::error::frontend pin 2026-09-01 is 73 day(s) old", run.stdout)

    def test_a_missing_date_is_an_error_not_a_pass(self):
        run = self.run_with("", "2026-11-02", "2026-11-13")
        self.assertEqual(run.returncode, 2)


if __name__ == "__main__":
    unittest.main()
