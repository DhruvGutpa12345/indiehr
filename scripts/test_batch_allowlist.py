"""Tests for scripts/batch-allowlist.py (P1-T20a). Stdlib only: python3 -m unittest."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("batch_allowlist", HERE / "batch-allowlist.py")
ba = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ba)

API = """\
import { HttpMethod, Type } from "@/Utils/request/types";

export default {
  list: {
    path: "/api/v1/thing/",
    TRes: Type<Record<string, string>>(),
  },
  // update: { path: "/api/v1/commented/", method: HttpMethod.DELETE },
  update: {
    path: "/api/v1/thing/{id}/",
    method: HttpMethod.PUT,
    TBody: Type<{ a: string; b: number }>(),
  },
  nested: {
    deeper: {
      path: "/api/v1/thing/{id}/deeper/",
      method: HttpMethod.POST,
    },
  },
  otp: { path: "/api/v1/otp/slots/{id}/create_appointment/", method: HttpMethod.POST },
} as const;
"""


class Generator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name)
        (self.src / "types").mkdir()
        (self.src / "types" / "thingApi.ts").write_text(API)
        self.saved = ba.SRC, ba.DYNAMIC
        ba.SRC, ba.DYNAMIC = self.src, {}

    def tearDown(self):
        ba.SRC, ba.DYNAMIC = self.saved
        self.tmp.cleanup()

    def site(self, body, name="page.tsx", helpers=True):
        head = 'import thingApi from "@/types/thingApi";\n'
        if helpers:
            head += 'import { useBatchRequest } from "@/Utils/request/batch";\n'
        (self.src / name).write_text(head + body)
        return head + body

    def reviewed(self, body, routes=("thingApi.update",)):
        """``body`` written as page.tsx and reviewed as it stands (DYNAMIC pinned to it)."""
        ba.DYNAMIC = {("page.tsx", "route"): {"routes": routes, "sha256": ""}}
        ba.DYNAMIC["page.tsx", "route"]["sha256"] = ba.site_hash(self.site(body))

    def test_routes_methods_and_call_sites(self):
        self.site(
            "const r = [{ api: thingApi.update, body: {} },\n"
            "  { api: thingApi.list, body: {} },\n"
            "  { api: isNew ? thingApi.nested.deeper : thingApi.update }];\n"
            "// { api: thingApi.otp }\n"
        )
        entries, problems = ba.call_sites()
        self.assertEqual(problems, [])
        self.assertEqual(
            entries,
            {
                ("PUT", "/api/v1/thing/{id}/"): {"page.tsx: thingApi.update"},
                ("GET", "/api/v1/thing/"): {"page.tsx: thingApi.list"},
                ("POST", "/api/v1/thing/{id}/deeper/"): {"page.tsx: thingApi.nested.deeper"},
            },
        )
        text = ba.render(entries)
        self.assertIn("PUT /api/v1/thing/{id}/  # page.tsx: thingApi.update\n", text)

    def test_files_without_the_helpers_are_not_batch_sites(self):
        self.site("const x = { api: thingApi.update };\n", helpers=False)
        self.assertEqual(ba.call_sites(), ({}, []))

    def test_a_variable_needs_a_reviewed_entry(self):
        self.site("const r = [{ api: route, body: {} }];\n")
        _, problems = ba.call_sites()
        self.assertEqual(len(problems), 1)
        self.assertIn("cannot be resolved", problems[0])
        self.reviewed("const route = thingApi.update;\nconst r = [{ api: route }];\n")
        entries, problems = ba.call_sites()
        self.assertEqual(problems, [])
        self.assertEqual(list(entries), [("PUT", "/api/v1/thing/{id}/")])

    def test_a_stale_reviewed_entry_fails(self):
        self.reviewed("const r = [{ api: thingApi.list }];\n")
        _, problems = ba.call_sites()
        self.assertTrue(any("no longer used" in p for p in problems), problems)

    # LinkDepartmentsSheet's shape: the reviewed batch takes getMutationParams(..., true)
    PARAMS = (
        "function params(isAdd) {\n"
        "  return { route: isAdd ? thingApi.update : thingApi.nested.deeper };\n"
        "}\n"
    )
    BATCH = "const { route } = params(%s);\nconst r = [{ api: route, body: {} }];\n"

    def test_comments_and_spacing_do_not_count_as_a_change(self):
        self.reviewed(self.PARAMS + self.BATCH % "true")
        self.site("// moved\n" + self.PARAMS + "\n\n" + self.BATCH % "true")
        self.assertEqual(ba.call_sites()[1], [])

    def test_a_changed_dynamic_site_fails_until_re_reviewed(self):
        """Review C3: the add-only batch starting to batch removes stays red."""
        self.reviewed(self.PARAMS + self.BATCH % "true")
        changed = self.site(self.PARAMS + self.BATCH % "isAdd")
        _, problems = ba.call_sites()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("DYNAMIC site changed", problems[0])
        self.assertIn(ba.site_hash(changed), problems[0])

    def test_a_second_use_of_the_variable_fails(self):
        body = self.PARAMS + self.BATCH % "true"
        body += "const s = [{ api: route, body: {} }];\n"
        self.reviewed(body)  # even when the new file is pinned
        _, problems = ba.call_sites()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("used 2 times", problems[0])

    def test_patient_otp_routes_never_belong(self):
        self.site("const r = [{ api: thingApi.otp }];\n")
        _, problems = ba.call_sites()
        self.assertTrue(any("patient-OTP" in p for p in problems), problems)

    def test_posting_to_the_batch_endpoint_by_hand_fails(self):
        self.site('fetch("/api/v1/batch_requests/");\n', name="raw.ts", helpers=False)
        _, problems = ba.call_sites()
        self.assertTrue(any("without the helpers" in p for p in problems), problems)

    def test_an_unknown_symbol_fails(self):
        self.site("const r = [{ api: thingApi.missing }];\n")
        _, problems = ba.call_sites()
        self.assertTrue(any("not found" in p for p in problems), problems)


class Repository(unittest.TestCase):
    @unittest.skipUnless(ba.SRC.is_dir(), "no web app checkout")
    def test_the_committed_list_is_current(self):
        entries, problems = ba.call_sites()
        self.assertEqual(problems, [])
        self.assertEqual(ba.OUT.read_text(), ba.render(entries))


if __name__ == "__main__":
    unittest.main()
