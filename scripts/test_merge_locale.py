"""Tests for scripts/merge-locale.py (P1-T7c). Run: python3 -m unittest scripts/test_merge_locale.py"""

import importlib.util
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "merge_locale", Path(__file__).with_name("merge-locale.py")
)
ml = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ml)

EN = {
    "save": "Save",
    "hello_name": "Hello {{name}}",
    "rich": "Read <strong>this</strong> and <1>that</1>",
    "nested": {"a": "A {{x}}", "b": "B"},
    "item_one": "{{count}} item",
    "item_other": "{{count}} items",
    "alert": "Alert",
}
UP = {"save": "सेव करें", "alert": "चेतावनी"}


class MergeLocaleTests(unittest.TestCase):
    def test_fills_gaps_only_and_applies_corrections(self):
        ours = {"save": "बचाएँ", "hello_name": "नमस्ते {{name}}"}
        merged = ml.merge(ours, {"alert": "सचेत"}, UP)
        self.assertEqual(merged["save"], "सेव करें")  # upstream kept
        self.assertEqual(merged["hello_name"], "नमस्ते {{name}}")  # gap filled
        self.assertEqual(merged["alert"], "सचेत")  # approved correction

    def test_good_strings_pass(self):
        ours = {
            "hello_name": "नमस्ते {{ name }}",
            "rich": "<strong>यह</strong> और <1>वह</1> पढ़ें",
            "nested": {"a": "ए {{x}}", "b": "बी"},
            "item_one": "{{count}} आइटम",
            "item_other": "{{count}} आइटम",
        }
        self.assertEqual(ml.check("hi", ours, EN, {"alert": "सचेत"}, UP), [])

    def test_problems_are_reported(self):
        ours = {
            "hello_name": "नमस्ते {{naam}}",  # placeholder renamed
            "rich": "यह और <1>वह</1> पढ़ें",  # tag dropped
            "nested": {"a": "ए {{x}}"},  # shape differs
            "save_all": "सब सेव करें",  # no such English key
            "item_one": "{{count}} आइटम",  # _other missing
            "hello": " ",
        }
        problems = "\n".join(ml.check("hi", ours, EN, {"hello_name": "x"}, UP))
        for expected in (
            "hello_name: placeholders/tags differ",
            "rich: placeholders/tags differ",
            "nested: shape differs",
            "save_all: not an English key",
            "item_one has no item_other",
            "hi.corrections.json: hello_name: upstream has no string to correct",
            "hello: not an English key",
        ):
            self.assertIn(expected, problems)


class EnglishOverlayTests(unittest.TestCase):
    def test_good_english_passes(self):
        self.assertEqual(ml.check_english({"ihr_a": "A {{x}}"}, EN), [])

    def test_collision_refused(self):
        problems = ml.check_english({"ihr_a": "A"}, {"ihr_a": "Other"})
        self.assertIn("upstream en.json already has this key", "\n".join(problems))

    def test_missing_prefix_refused(self):
        self.assertIn("must start with 'ihr_'", "\n".join(ml.check_english({"save2": "S"}, EN)))

    def test_non_string_refused(self):
        for bad in ({"a": "b"}, 3, None, ["x"]):
            self.assertIn("must be a string", "\n".join(ml.check_english({"ihr_a": bad}, EN)))

    def test_ihr_key_in_upstream_english_refused(self):
        problems = ml.check_upstream_english({"save": "Save", "ihr_new": "New"})
        self.assertEqual(len(problems), 1)
        self.assertIn("ihr_new", problems[0])
        self.assertIn("deploy/locale/en.json", problems[0])
        self.assertEqual(ml.check_upstream_english(EN), [])

    def test_hindi_ihr_key_validated_against_our_english(self):
        english = {**EN, "ihr_hi": "Hi {{name}}"}
        self.assertEqual(ml.check("hi", {"ihr_hi": "नमस्ते {{name}}"}, english, {}, UP), [])
        problems = ml.check("hi", {"ihr_hi": "नमस्ते {{naam}}"}, english, {}, UP)
        self.assertIn("ihr_hi: placeholders/tags differ", "\n".join(problems))
        # without our English the same Hindi key is unknown
        self.assertIn("not an English key", "\n".join(ml.check("hi", {"ihr_hi": "x"}, EN, {}, UP)))


class CommandTests(unittest.TestCase):
    """Runs the script against a scratch copy of the layout."""

    def run_script(self, root, *args):
        import shutil, subprocess, sys, tempfile  # noqa: E401

        return subprocess.run(
            [sys.executable, str(root / "scripts" / "merge-locale.py"), *args],
            capture_output=True, text=True,
        )

    def make_root(self, tmp, ours_en, up_en):
        import json, shutil  # noqa: E401

        root = Path(tmp)
        (root / "scripts").mkdir()
        shutil.copy(Path(__file__).with_name("merge-locale.py"), root / "scripts")
        (root / "deploy/locale").mkdir(parents=True)
        (root / "apps/frontend/public/locale").mkdir(parents=True)
        (root / "deploy/locale/en.json").write_text(json.dumps(ours_en))
        (root / "deploy/locale/hi.json").write_text(json.dumps({"ihr_a": "ए {{x}}"}))
        (root / "apps/frontend/public/locale/en.json").write_text(json.dumps(up_en))
        (root / "apps/frontend/public/locale/hi.json").write_text("{}")
        return root

    def test_merge_adds_ours_and_check_writes_nothing(self):
        import json, tempfile  # noqa: E401

        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp, {"ihr_a": "A {{x}}"}, {"save": "Save"})
            en = root / "apps/frontend/public/locale/en.json"
            before = en.read_text()
            result = self.run_script(root, "--check")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(en.read_text(), before)
            result = self.run_script(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(en.read_text()), {"save": "Save", "ihr_a": "A {{x}}"})
            hi = json.loads((root / "apps/frontend/public/locale/hi.json").read_text())
            self.assertEqual(hi, {"ihr_a": "ए {{x}}"})
            # a second in-place merge finds our key already there and refuses
            self.assertNotEqual(self.run_script(root).returncode, 0)

    def test_bad_english_exits_non_zero(self):
        import tempfile  # noqa: E401

        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp, {"nope": "x"}, {"save": "Save"})
            result = self.run_script(root, "--check")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must start with", result.stderr)

    def test_ihr_key_only_in_upstream_en_exits_non_zero(self):
        import tempfile  # noqa: E401

        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp, {"ihr_a": "A {{x}}"}, {"save": "Save", "ihr_b": "B"})
            result = self.run_script(root, "--check")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ihr_b: our key must live in deploy/locale/en.json", result.stderr)


if __name__ == "__main__":
    unittest.main()
