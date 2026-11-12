"""scripts/check-pack-versions.sh (P1-T15a, review C3/S3): released reference pack versions are
never changed, deleted or renamed, both at commit time (the staged change) and in CI (against
the commit before the push). Each test builds a throwaway git repository; no network."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check-pack-versions.sh"
PACKS = "plugins/ihr_platform/ihr_refdata/packs"


class CheckPackVersions(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="ihr-pack-versions-"))
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        # never the caller's GIT_* (a commit hook runs with GIT_INDEX_FILE and the like set)
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        }
        self.git("init", "-q", "-b", "main")
        (self.repo / PACKS / "p").mkdir(parents=True)
        self.releases = []
        self.release("p", 1, "one")
        self.base = self.commit("v1")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, check=True, capture_output=True, text=True
        ).stdout.strip()

    def write(self, pack, version, content):
        data = (json.dumps({"pack": pack, "version": version, "x": content}) + "\n").encode()
        (self.repo / PACKS / pack / f"{version}.json").write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def manifest(self):
        (self.repo / PACKS / "MANIFEST.json").write_text(json.dumps({"releases": self.releases}) + "\n")

    def release(self, pack, version, content):
        sha = self.write(pack, version, content)
        self.releases = [r for r in self.releases if (r["pack"], r["version"]) != (pack, version)]
        self.releases.append({"pack": pack, "version": version, "file": f"{pack}/{version}.json", "sha256": sha})
        self.manifest()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def check(self, *args):
        return subprocess.run(
            [str(SCRIPT), *args], cwd=self.repo, env=self.env, capture_output=True, text=True, check=False
        )

    # --- CI: against every committed manifest ---

    def test_history_refuses_a_version_deleted_then_rebuilt_in_separate_pushes(self):
        (self.repo / PACKS / "p" / "1.json").unlink()
        self.releases = []
        self.manifest()
        self.commit("delete v1")  # a push of its own
        self.release("p", 1, "rebuilt")
        self.commit("rebuilt v1")  # the next push: its own base no longer holds v1
        self.assertEqual(self.check("--base", "HEAD~1").returncode, 0)  # what --base alone misses
        run = self.check("--history")
        self.assertEqual(run.returncode, 1, run.stdout)

    def test_history_passes_when_only_versions_were_added(self):
        self.release("p", 2, "two")
        self.commit("v2")
        run = self.check("--history")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("2 committed manifest(s)", run.stdout)

    # --- CI: against the commit before the push ---

    def test_a_new_version_passes(self):
        self.release("p", 2, "two")
        self.commit("v2")
        run = self.check("--base", self.base)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("1 release(s)", run.stdout)

    def test_a_rebuilt_version_with_its_manifest_updated_is_refused(self):
        self.release("p", 1, "rebuilt")  # the file and its manifest sha256 both changed
        self.commit("rebuild v1")
        run = self.check("--base", self.base)
        self.assertEqual(run.returncode, 1)
        self.assertIn("p v1 was released as", run.stderr)

    def test_delete_then_re_add_over_two_commits_is_refused(self):
        (self.repo / PACKS / "p" / "1.json").unlink()
        self.releases = []
        self.manifest()
        self.commit("drop v1")
        self.release("p", 1, "again")
        self.commit("re-add v1")
        run = self.check("--base", self.base)
        self.assertEqual(run.returncode, 1)
        self.assertIn("p v1 was released as", run.stderr)

    def test_a_renamed_file_or_a_changed_file_alone_is_refused(self):
        os.rename(self.repo / PACKS / "p" / "1.json", self.repo / PACKS / "p" / "01.json")
        self.releases[0]["file"] = "p/01.json"
        self.manifest()
        self.commit("rename")
        self.assertEqual(self.check("--base", self.base).returncode, 1)
        self.git("reset", "-q", "--hard", self.base)
        (self.repo / PACKS / "p" / "1.json").write_text("{}\n")  # manifest left as it was
        self.commit("edit")
        run = self.check("--base", self.base)
        self.assertEqual(run.returncode, 1)
        self.assertIn("no longer hashes", run.stderr)

    def test_a_base_with_no_packs_passes_and_an_unknown_base_fails(self):
        self.assertEqual(self.check("--base", self.git("rev-list", "--max-parents=0", "HEAD")).returncode, 0)
        run = self.check("--base", "0" * 40)
        self.assertEqual(run.returncode, 2)
        self.assertIn("not a commit", run.stderr)

    # --- pre-commit: the staged change ---

    def test_staged_modify_delete_and_rename_are_refused_and_an_addition_passes(self):
        self.release("p", 2, "two")
        self.git("add", "-A")
        self.assertEqual(self.check().returncode, 0)
        self.commit("v2")
        for change in ("modify", "delete", "rename"):
            with self.subTest(change=change):
                path = self.repo / PACKS / "p" / "1.json"
                if change == "modify":
                    path.write_text("{}\n")
                elif change == "delete":
                    path.unlink()
                else:
                    os.rename(path, self.repo / PACKS / "p" / "9.json")
                self.git("add", "-A")
                run = self.check()
                self.assertEqual(run.returncode, 1, change)
                self.assertIn("p/1.json", run.stderr)
                self.git("reset", "-q", "--hard", "HEAD")


if __name__ == "__main__":
    unittest.main()
