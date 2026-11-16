"""The kinds contract (P1-T15b2a; ihr_refdata/kinds.py, design.md §3.1): every registered kind
keeps it, and nothing reaches a kind but through the registry."""

import inspect
from pathlib import Path

from django.test import SimpleTestCase

from ihr_refdata import kinds, manifest

HERE = Path(__file__).resolve().parent.parent


class ContractTests(SimpleTestCase):
    def test_every_registered_kind_keeps_the_contract(self):
        self.assertEqual(
            kinds.names(), ("identifier_config", "questionnaire", "valueset")
        )
        for name in kinds.names():
            with self.subTest(kind=name):
                kind = kinds.get(name)
                self.assertEqual(kind.KIND, name)
                self.assertIn(kind.SCOPE, kinds.SCOPES)
                self.assertIsInstance(kind.RESTORE_DELETED, bool)
                self.assertIsInstance(kind.RECHECK_CONFLICTS, bool)
                self.assertIsInstance(kind.FIELDS, tuple)
                self.assertTrue(kind.FIELDS)
                self.assertTrue(all(isinstance(f, str) for f in kind.FIELDS))
                for attr in kinds.CONTRACT:
                    value = getattr(kind, attr)
                    if not attr.isupper():
                        self.assertTrue(callable(value), attr)
                params = inspect.signature(kind.rows).parameters
                self.assertEqual(params["lock"].default, False)
                self.assertIsNone(params["targets"].default)  # item 2
                self.assertIsNone(
                    inspect.signature(kind.all_present).parameters["targets"].default
                )
                self.assertEqual(
                    inspect.signature(kind.write).parameters["restore"].default, False
                )
                self.assertEqual(
                    set(inspect.signature(kind.shown).parameters),
                    {"row", "item", "retire"},
                )

    def test_value_sets_only_gained_attributes(self):
        kind = kinds.get("valueset")
        self.assertTrue(kind.RESTORE_DELETED)
        self.assertFalse(kind.RECHECK_CONFLICTS)
        self.assertEqual(kind.target({"slug": "x"}), "")
        self.assertEqual(kind.conflict("x", None, {}), "")
        self.assertEqual(kind.data_checks(None, {}), ())
        self.assertEqual(kind.admin_reason(None, {}, []), ("", {}))

    def test_identifier_configs_do_not_restore_and_recheck(self):
        kind = kinds.get("identifier_config")
        self.assertFalse(kind.RESTORE_DELETED)
        self.assertTrue(kind.RECHECK_CONFLICTS)
        self.assertEqual(kind.FIELDS, ("status", "config"))

    def test_forms_do_not_restore_and_name_their_row(self):
        kind = kinds.get("questionnaire")  # P1-T15b2b
        self.assertFalse(kind.RESTORE_DELETED)
        self.assertFalse(kind.RECHECK_CONFLICTS)
        item = {"slug": "intake", "form_version": 3}
        self.assertEqual(kind.target(item), "ihr-pack-intake-v3")
        self.assertEqual(kind.record_target(item, None), "ihr-pack-intake-v3")
        self.assertEqual(kinds.get("valueset").record_target(item, None), "")

    def test_an_unknown_kind_is_refused_and_the_manifest_reads_the_registry(self):
        with self.assertRaises(KeyError):
            kinds.get("observation_definition")  # P1-T15b3
        self.assertEqual(manifest.kind_names(), kinds.names())
        self.assertFalse(hasattr(manifest, "KINDS"))

    def test_nothing_reaches_a_kind_module_but_through_the_registry(self):
        for name in ("engine.py", "api.py", "manifest.py"):
            text = (HERE / name).read_text()
            with self.subTest(file=name):
                self.assertNotIn("HANDLERS", text)
                self.assertNotIn("import valuesets", text)
                self.assertNotIn("valuesets.", text)
