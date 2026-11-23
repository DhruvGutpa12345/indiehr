"""One canonical environment-name regex (P1-T32 E1, design docs/reviews/p1-t32/design.md §2, L4).

The canonical copy is ``SERVER_PATTERN`` in plugins/ihr_platform/ihr_platform/environment.py.
Every other copy in the repository (shell, Compose, scripts, plugins) must equal it, and every
file that is told to hold one must still hold one, so a deleted guard cannot pass quietly.

Run: python3 -m unittest scripts/test_environment_regex.py
"""

import importlib.util
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PY = ROOT / "plugins/ihr_platform/ihr_platform/environment.py"
CANONICAL = r"^(production-[1-9][0-9]{0,2}|staging|proof)$"
# Anything that looks like a copy: the production-<n> shape starting a group, to the closing $
COPY_RE = re.compile(r"\^\(production-[^\n]*?\)\$")
SUFFIXES = {".py", ".sh", ".yaml", ".yml", ".txt", ".lua", ".json", ".toml"}
# Files that name the regex without being a working copy (this test, and the records)
EXCLUDED = {
    "scripts/test_environment_regex.py",
    "plugins/ihr_platform/ihr_platform/tests/test_environment.py",
}
# Files that must hold a copy. Each later P1-T32 step adds its own file here when it adds its
# copy (S2a: scripts/staging.sh ENV_RE; S3: scripts/deploy.sh and scripts/instance_check.py;
# S4: deploy/dumps/dumper.py), so a deleted guard fails this test instead of passing quietly.
MUST_HOLD = ("plugins/ihr_platform/ihr_platform/environment.py", "scripts/staging.sh",
             "scripts/deploy.sh", "scripts/instance_check.py", "deploy/host/bootstrap.sh",
             "deploy/dumps/dumper.py")


def tracked():
    out = subprocess.run(  # noqa: S603, S607 (fixed git call)
        ["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def load_environment():
    spec = importlib.util.spec_from_file_location("ihr_environment_under_test", ENV_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # must work with no Django settings
    return module


class RegexEqualityTests(unittest.TestCase):
    def copies(self):
        found = {}
        for rel in tracked():
            path = ROOT / rel
            if (
                rel in EXCLUDED
                or rel.startswith(("docs/", "apps/", "graphify-out/"))
                or path.suffix not in SUFFIXES
                or not path.is_file()
            ):
                continue
            for text in COPY_RE.findall(path.read_text(errors="ignore")):
                found.setdefault(rel, []).append(text)
        return found

    def test_the_canonical_copy_is_the_documented_one(self):
        module = load_environment()
        self.assertEqual(module.SERVER_PATTERN, CANONICAL)

    def test_every_copy_equals_the_canonical_regex(self):
        for rel, texts in self.copies().items():
            for text in texts:
                with self.subTest(file=rel):
                    self.assertEqual(text, CANONICAL, f"{rel} holds a different copy")

    def test_the_files_that_must_hold_a_copy_still_do(self):
        copies = self.copies()
        for rel in MUST_HOLD:
            with self.subTest(file=rel):
                self.assertIn(rel, copies, f"{rel} lost its copy of the name regex")

    def test_the_shell_copies_agree_with_python_on_every_row(self):
        """The same table through Python's fullmatch and bash's =~ gives the same answers."""
        module = load_environment()
        rows = [
            "production-1",
            "production-12",
            "production-999",
            "staging",
            "proof",
            "",
            "dev",
            "prod",
            "production-0",
            "production-01",
            "production-1000",
            "Production-1",
            "staging ",
            "'staging'",
            "proof-1",
        ]
        for row in rows:
            with self.subTest(row=row):
                shell = subprocess.run(  # noqa: S603, S607 (fixed bash one-liner)
                    ["bash", "-c", 'RE=$1; [[ $2 =~ $RE ]]', "_", CANONICAL, row],
                    check=False,
                ).returncode
                self.assertEqual(shell == 0, module.valid(row), repr(row))


if __name__ == "__main__":
    unittest.main()
