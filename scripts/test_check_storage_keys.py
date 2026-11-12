"""Tests for scripts/check-storage-keys.py (P1-T1d). Run: python3 -m unittest scripts/test_check_storage_keys.py"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "check_storage_keys", Path(__file__).with_name("check-storage-keys.py")
)
csk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(csk)

CONSTANTS = """export const LocalStorageKeys = {
  accessToken: "care_access_token",
  patientTokenKey: "care_patient_token",
};
"""

STAFF_SESSION = """import { LocalStorageKeys } from "@/common/constants";
const LEGACY_KEYS = ["care_access_token"];
const PERSISTED_QUERIES_KEY = "REACT_QUERY_OFFLINE_CACHE";
const PATIENT_PORTAL_KEYS = [LocalStorageKeys.patientTokenKey, "user"];
const CACHED_PREFIXES = ["pharmacy-dispense-service--"];
const PENDING = "ihr_sign_out_pending";
export function pending() {
  return storage()?.getItem(PENDING);
}
export function clearStaffStorage() {
  const store = storage();
  for (const key of [...LEGACY_KEYS, PERSISTED_QUERIES_KEY, ...PATIENT_PORTAL_KEYS]) {
    store.removeItem(key);
  }
  for (const key of Object.keys(store)) {
    if (CACHED_PREFIXES.some((prefix) => key.startsWith(prefix))) store.removeItem(key);
  }
  sessionStorage.clear();
  FiltersCache.invalidateAll();
}
"""

FILES = {
    "common/constants.tsx": CONSTANTS,
    "Utils/ihr/staffSession.ts": STAFF_SESSION,
    "Utils/FiltersCache.tsx": "const get = (key) => localStorage.getItem(key ?? getKey());\n",
    "lib/appVersion.ts": 'export const APP_VERSION_KEY = "app-version";\n'
    "localStorage.setItem(APP_VERSION_KEY, v);\n",
    "components/Lang.tsx": 'localStorage.setItem("i18nextLng", lang);\n',
    "atoms/pharmacy.ts": "atomWithStorage<State | null>(\n  `pharmacy-dispense-service--${id}`,\n  null,\n);\n",
    "atoms/user-atom.ts": "atomWithStorage<User | undefined>(\n"
    '  "care-auth-user",\n  undefined,\n  createJSONStorage(() => sessionStorage),\n);\n',
    "atoms/nav.ts": "atomWithStorage<boolean | null>(`nav-expansion-state--${name}`, null);\n",
    "Utils/useView.ts": "export function useView(name: string, d: string) {\n"
    "  return localStorage.getItem(name) || d;\n}\n",
    "pages/Users.tsx": 'const [view] = useView("users", "card");\n',
    "pages/Portal.tsx": "localStorage.getItem(LocalStorageKeys.patientTokenKey);\n"
    'localStorage.setItem("user", JSON.stringify(u));\n',
}

CLASSIFIED = {
    "may_remain": {
        "exact": {
            "i18nextLng": "language",
            "app-version": "version",
            "users": "view",
            "ihr_sign_out_pending": "flag",
        },
        "prefixes": {"nav-expansion-state--": "menus"},
    },
    "content_checked": {"local": ["REACT_QUERY_OFFLINE_CACHE"], "session": ["care-auth-user"]},
    "cleared_by_helpers": {"filters--": "FiltersCache.invalidateAll"},
    "key_helpers": {"useView": "local"},
    "dynamic_sites": {
        "Utils/FiltersCache.tsx": ["filters--"],
        "Utils/useView.ts": [],
        "Utils/ihr/staffSession.ts": [],
    },
}


def make_tree(extra: dict[str, str] | None = None, classified: dict | None = None) -> Path:
    root = Path(tempfile.mkdtemp())
    src = root / csk.SRC
    for rel, text in {**FILES, **(extra or {})}.items():
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / csk.CLASSIFIED).parent.mkdir(parents=True, exist_ok=True)
    (root / csk.CLASSIFIED).write_text(json.dumps(classified or CLASSIFIED), encoding="utf-8")
    return root


class CheckStorageKeysTests(unittest.TestCase):
    def test_classified_tree_passes(self):
        self.assertEqual(csk.check(make_tree()), [])

    def test_finds_every_kind_of_key(self):
        root = make_tree()
        keys, problems = csk.find_keys(root / csk.SRC, CLASSIFIED)
        self.assertEqual(problems, [])
        found = {(key.store, key.name, key.prefix) for key in keys}
        for expected in [
            ("local", "app-version", False),  # through a constant
            ("local", "i18nextLng", False),  # literal
            ("local", "pharmacy-dispense-service--", True),  # template literal
            ("session", "care-auth-user", False),  # atom on sessionStorage
            ("local", "users", False),  # key helper (useView)
            ("local", "care_patient_token", False),  # LocalStorageKeys member
            ("local", "ihr_sign_out_pending", False),  # storage()? accessor
            ("local", "filters--", True),  # dynamic site
        ]:
            self.assertIn(expected, found)

    def test_new_unclassified_local_key_fails(self):
        problems = csk.check(
            make_tree({"pages/Patients.tsx": 'localStorage.setItem("recent_patients", ids);\n'})
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("recent_patients", problems[0])
        self.assertIn("src/pages/Patients.tsx:1", problems[0])

    def test_new_atom_prefix_fails(self):
        problems = csk.check(
            make_tree({"atoms/recent.ts": "atomWithStorage<string[]>(`recent--${id}`, []);\n"})
        )
        self.assertTrue(any("recent--" in problem for problem in problems))

    def test_session_key_passes_only_while_session_storage_is_cleared(self):
        extra = {"pages/Enc.tsx": 'sessionStorage.setItem("enc_filters", f);\n'}
        self.assertEqual(csk.check(make_tree(extra)), [])
        without_clear = STAFF_SESSION.replace("  sessionStorage.clear();\n", "")
        problems = csk.check(make_tree({**extra, "Utils/ihr/staffSession.ts": without_clear}))
        self.assertTrue(any("enc_filters" in problem for problem in problems))

    def test_key_stops_counting_as_cleared_when_clear_up_drops_it(self):
        without_portal = STAFF_SESSION.replace(", ...PATIENT_PORTAL_KEYS]", "]")
        problems = csk.check(make_tree({"Utils/ihr/staffSession.ts": without_portal}))
        self.assertTrue(any("`user`" in problem for problem in problems))
        self.assertTrue(any("care_patient_token" in problem for problem in problems))

    def test_helper_cleared_prefix_needs_the_helper_call(self):
        without_helper = STAFF_SESSION.replace("  FiltersCache.invalidateAll();\n", "")
        problems = csk.check(make_tree({"Utils/ihr/staffSession.ts": without_helper}))
        self.assertTrue(any("filters--" in problem for problem in problems))

    def test_unresolvable_key_outside_a_dynamic_site_fails(self):
        problems = csk.check(
            make_tree({"pages/Odd.tsx": "localStorage.setItem(makeKey(id), v);\n"})
        )
        self.assertTrue(any("cannot be worked out" in problem for problem in problems))

    def test_missing_dynamic_site_is_reported(self):
        classified = json.loads(json.dumps(CLASSIFIED))
        classified["dynamic_sites"]["Utils/Gone.ts"] = []
        problems = csk.check(make_tree(classified=classified))
        self.assertTrue(any("Utils/Gone.ts no longer exists" in problem for problem in problems))

    def test_real_tree_is_classified(self):
        self.assertEqual(csk.check(), [])


if __name__ == "__main__":
    unittest.main()
