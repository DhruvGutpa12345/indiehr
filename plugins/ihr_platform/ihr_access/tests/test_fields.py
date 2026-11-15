"""The guard's field lists match CARE's specs (P1-T11b1; design-b.md §9 tests 6-7, "After the
attack" §10 test 1). An upstream field added to a guarded spec fails here until it is classified
(meanwhile it is reset for restricted users: fail closed); a field CARE dropped fails too."""

import types
import typing

from care.emr.resources.patient.spec import PatientUpdateSpec
from django.test import SimpleTestCase
from django.utils.module_loading import import_string
from pydantic import BaseModel

from ihr_access import authz, guard
from ihr_access.checks import guard_installed

EDIT_VERBS = frozenset({"edit", "hidden", "visible"})


def _is_container(annotation):
    """dict, list of dicts or models, a model, or an Optional of one."""
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        return any(_is_container(a) for a in args if a is not type(None))
    if origin is typing.Annotated:
        return _is_container(args[0])
    if annotation is dict or origin is dict:
        return True
    if origin is list:
        return bool(args) and _is_container(args[0])
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


class FieldLists(SimpleTestCase):
    def test_each_list_classifies_exactly_the_shapes_fields(self):
        for shape in guard.SHAPES.values():
            with self.subTest(shape=shape.name):
                rules = guard.read_list(guard.FIELDS / f"{shape.name}.txt")
                top = {path for path in rules if "." not in path}
                fields = set()
                for cls in guard.classes_of(import_string(shape.base)):
                    fields |= set(cls.model_fields)
                self.assertEqual(
                    sorted(fields - top), [], f"unclassified in fields/{shape.name}.txt"
                )
                self.assertEqual(
                    sorted(top - fields),
                    [],
                    f"no longer in CARE's spec: fields/{shape.name}.txt",
                )

    def test_every_kept_container_names_its_nested_keys(self):
        for shape in guard.SHAPES.values():
            rules = shape.rules
            for name, info in shape.fields.items():
                verb = rules.get(name)
                if verb not in ("keep", "identifiers") or not _is_container(
                    info.annotation
                ):
                    continue
                with self.subTest(shape=shape.name, field=name):
                    self.assertTrue(
                        any(path.startswith(name + ".") for path in rules),
                        f"{shape.name}.{name} is a kept container: name its nested keys",
                    )

    def test_nested_rules_hang_off_kept_containers_and_wildcards_stand_alone(self):
        for shape in guard.SHAPES.values():
            for path, verb in shape.rules.items():
                if "." not in path:
                    continue
                parent, _, key = path.rpartition(".")
                top = parent.split(".", 1)[0]
                with self.subTest(shape=shape.name, path=path):
                    self.assertIn(shape.rules.get(top), ("keep", "identifiers"))
                    self.assertNotIn(shape.rules.get(parent, "keep"), ("drop",))
                    if key == "*":
                        siblings = [
                            p
                            for p in shape.rules
                            if p.rpartition(".")[0] == parent and p != path
                        ]
                        self.assertEqual(
                            siblings, [], "a wildcard keeps the card whole"
                        )
                        self.assertEqual(verb, "keep")

    def test_the_patient_edit_list_classifies_the_update_spec(self):
        rules = guard.read_list(guard.FIELDS / "patient_edit.txt", verbs=EDIT_VERBS)
        self.assertEqual(set(rules), set(PatientUpdateSpec.model_fields))
        dropped = {p for p, (v, _r) in guard.read_list(guard.FIELDS / "patient.txt").items()
                   if v == "drop"}  # fmt: skip
        for name, (verb, _reason) in rules.items():
            if name in dropped:
                with self.subTest(field=name):
                    self.assertEqual(
                        verb, "hidden", "a field hidden from them is never written"
                    )

    def test_the_identifier_list_holds_no_programme(self):
        systems = guard.allowed_identifier_systems()
        self.assertTrue(systems)
        for system in systems:
            self.assertNotRegex(system.lower(), r"art|nikshay|hiv|tb|programme|program")

    def test_a_malformed_line_is_refused(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            for text in (
                "name keep\n",
                "name show  # x\n",
                "a keep  # x\na drop  # y\n",
            ):
                path = Path(folder) / "list.txt"
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(ValueError):
                    guard.read_list(path)


class GuardInstalled(SimpleTestCase):
    def test_the_system_check_passes(self):
        self.assertEqual(guard_installed(), [])

    def test_every_class_of_every_shape_serializes_through_the_guard(self):
        for shape in guard.SHAPES.values():
            for cls in guard.classes_of(import_string(shape.base)):
                with self.subTest(cls=cls.__name__):
                    self.assertEqual(
                        getattr(cls.serialize, guard.MARKER, None), shape.name
                    )

    def test_a_subclass_with_its_own_serialize_is_reported(self):
        from care.emr.resources.patient.spec import PatientRetrieveSpec

        class Sneaky(PatientRetrieveSpec):
            @classmethod
            def serialize(cls, obj, user=None, *args, **kwargs):
                return super().serialize(obj, user, *args, **kwargs)

        try:
            self.assertTrue(any("Sneaky" in p for p in guard.problems()))
        finally:
            # The throwaway class inherits the guard again, for the other tests
            del Sneaky.serialize
        self.assertFalse(any("Sneaky" in p for p in guard.problems()))

    def test_the_patient_user_rule_is_installed(self):
        self.assertEqual(authz.problems(), [])
