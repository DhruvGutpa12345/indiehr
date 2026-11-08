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


if __name__ == "__main__":
    unittest.main()
