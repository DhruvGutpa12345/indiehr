"""Tests for scripts/check-date-formats.py (P1-T7b). Run: python3 -m unittest scripts/test_check_date_formats.py"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "check_date_formats", Path(__file__).with_name("check-date-formats.py")
)
cdf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cdf)

MAP_TS = """
// prettier-ignore
export const DATE_FNS_FORMAT_MAP: Readonly<Record<string, string>> = {
  "dd MMM yyyy": "dd-MM-yyyy",
  "PPP 'at' p": "dd-MM-yyyy, hh:mm a",
};

// prettier-ignore
export const DAYJS_FORMAT_MAP: Readonly<Record<string, string>> = {
  "DD/MM/YYYY": "DD-MM-YYYY",
};
"""


def make_tree(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "Utils/ihr").mkdir(parents=True)
    (root / cdf.MAP_FILE).write_text(MAP_TS, encoding="utf-8")
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


class CheckDateFormatsTests(unittest.TestCase):
    def test_recognises_date_patterns_only(self):
        for literal in ["dd MMM yyyy", "PPP 'at' p", "hh:mm A; DD/MM/YYYY", "PPp", "HH:mm"]:
            self.assertTrue(cdf.is_date_pattern(literal), literal)
        for literal in ["a", "s", "md", "flex", "Hello", "p"]:
            self.assertFalse(cdf.is_date_pattern(literal), literal)

    def test_mapped_results_allowed_and_non_dates_pass(self):
        root = make_tree(
            {
                "a.tsx": (
                    'format(d, "dd MMM yyyy");\n'
                    'format(d, "dd-MM-yyyy");\n'
                    "dayjs(d).format('DD/MM/YYYY');\n"
                    'format(d, "yyyy-MM-dd");\n'
                    'format(d, "PPP \'at\' p");\n'
                    'const code = "PM";\n'
                )
            }
        )
        self.assertEqual(cdf.unknown_patterns(root), {})

    def test_reports_unknown_display_pattern_with_place(self):
        root = make_tree({"pages/b.tsx": 'x;\nformat(d, "MMM do, yyyy");\n'})
        self.assertEqual(cdf.unknown_patterns(root), {"MMM do, yyyy": ["pages/b.tsx:2"]})
        self.assertEqual(cdf.main(["--root", str(root)]), 1)

    def test_skips_comments_and_our_own_folder(self):
        root = make_tree(
            {
                "c.ts": '// format(d, "MMM do, yyyy")\n',
                "Utils/ihr/x.test.ts": 'format(d, "MMM do, yyyy");\n',
            }
        )
        self.assertEqual(cdf.main(["--root", str(root)]), 0)

    def test_24_hour_patterns_only_in_listed_files(self):
        root = make_tree(
            {
                "pages/Scheduling/utils.ts": 'format(t, "HH:mm");\nparse(x, "HH:mm:ss", d);\n',
                "pages/Other.tsx": 'x;\n<span>{format(t, "HH:mm")}</span>;\n',
                "pages/Scheduling/components/CreateScheduleTemplateSheet.tsx": (
                    'dayjs(v, "HH:mm:ss");\n'
                ),
            }
        )
        self.assertEqual(
            cdf.unknown_patterns(root),
            {
                "HH:mm": ["pages/Other.tsx:2"],
                "HH:mm:ss": ["pages/Scheduling/components/CreateScheduleTemplateSheet.tsx:1"],
            },
        )
        self.assertNotIn("HH:mm", cdf.ALLOWED)
        self.assertNotIn("HH:mm:ss", cdf.ALLOWED)

    def test_finds_locale_calls(self):
        root = make_tree(
            {
                "a.tsx": (
                    "new Date(x).toLocaleString();\n"
                    "d.toLocaleDateString('en-IN');\n"
                    "d.toLocaleTimeString([], {});\n"
                    "new Intl.DateTimeFormat('en', {}).format(d);\n"
                    "formatRelative(d, new Date());\n"
                    "// d.toLocaleString()\n"
                    "n.toFixed(2);\n"
                ),
                "Utils/ihr/x.ts": "d.toLocaleString();\n",
            }
        )
        self.assertEqual(cdf.locale_calls(root), {"a.tsx": [1, 2, 3, 4, 5]})

    def test_locale_calls_need_a_reviewed_entry(self):
        root = make_tree({"a.tsx": "d.toLocaleString();\n", "b.tsx": "x;\n"})
        self.assertEqual(
            cdf.unreviewed_locale_calls(root, {}), ({"a.tsx": [1]}, [])
        )
        self.assertEqual(
            cdf.unreviewed_locale_calls(root, {"a.tsx": "reason"}), ({}, [])
        )
        # An entry whose file has no such call any more is reported, so the list stays honest.
        self.assertEqual(
            cdf.unreviewed_locale_calls(root, {"a.tsx": "r", "b.tsx": "r"}),
            ({}, ["b.tsx"]),
        )
        # main() uses the real allow-list, which does not list a.tsx.
        self.assertEqual(cdf.main(["--root", str(root)]), 1)

    def test_every_allow_list_entry_has_a_reason(self):
        for rel, reason in cdf.LOCALE_CALLS_ALLOWED.items():
            self.assertGreater(len(reason.strip()), 10, rel)

    def test_real_tree_is_clean(self):
        self.assertEqual(cdf.unknown_patterns(cdf.DEFAULT_ROOT), {})
        self.assertEqual(cdf.unreviewed_locale_calls(cdf.DEFAULT_ROOT), ({}, []))
        self.assertEqual(cdf.main([]), 0)


if __name__ == "__main__":
    unittest.main()
