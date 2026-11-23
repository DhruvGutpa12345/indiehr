"""CARE's action engine serves only reviewed instructions and fields (P2-T0a, review F3)."""

import tempfile
from pathlib import Path

from django.core import checks
from django.test import SimpleTestCase

from ihr_platform import actions


def _registries():
    from care.emr.registries.actions.field import ActionFieldRegistry
    from care.emr.registries.actions.instruction import ActionInstructionRegistry

    return ActionInstructionRegistry, ActionFieldRegistry


class ActionEngineTests(SimpleTestCase):
    def test_the_logging_instruction_is_not_served(self):
        """Its authorize() always passes and it logs patient fields and answers at INFO."""
        instructions, _ = _registries()
        self.assertIsNone(instructions.get_instruction("logging"))
        slugs = {d["slug"] for d in instructions.render_all_instructions()}
        self.assertNotIn("logging", slugs)

    def test_an_action_naming_it_is_refused_when_authored(self):
        from care.action_evaluator.base import ActionEvaluator
        from rest_framework.exceptions import ValidationError as DRFValidationError

        action = {
            "condition": "True",
            "instructions": [
                {"slug": "logging", "context": "self", "params": {"message": "x"}}
            ],
        }
        with self.assertRaises((DRFValidationError, ValueError)) as caught:
            ActionEvaluator.authorize(None, None, action)
        self.assertIn("logging", str(caught.exception))

    def test_the_test_dynamic_field_is_not_served(self):
        _, fields = _registries()
        served = {
            actions._dotted(f)
            for entries in fields._dynamic_contexts.values()
            for f in entries
        }
        self.assertFalse(served & actions.REMOVED_DYNAMIC_FIELDS, served)

    def test_every_registered_piece_is_reviewed(self):
        self.assertEqual(actions.check(), [])
        self.assertIn(actions.system_check, checks.registry.registry.get_checks())

    def test_an_unreviewed_instruction_stops_start_up(self):
        instructions, _ = _registries()

        class Unreviewed:
            slug = "ihr_test_unreviewed"

        instructions._instructions[Unreviewed.slug] = Unreviewed
        try:
            errors = actions.system_check()
        finally:
            instructions._instructions.pop(Unreviewed.slug)
        self.assertEqual([e.id for e in errors], ["ihr_platform.E028"])
        self.assertIn("instruction ihr_test_unreviewed", errors[0].msg)

    def test_a_removed_piece_coming_back_stops_start_up(self):
        from care.action_evaluator.instruction_engine.instructions.logging_test import (
            LoggingActionInstruction,
        )

        instructions, _ = _registries()
        before = dict(instructions._instructions)
        instructions._instructions["logging"] = LoggingActionInstruction
        try:
            problems = actions.check()
        finally:
            instructions._instructions.clear()
            instructions._instructions.update(before)
        self.assertTrue(any("must stay removed" in p for p in problems), problems)

    def test_a_line_without_a_reason_or_a_stale_line_is_a_problem(self):
        lines = actions.REVIEWED_FILE.read_text().splitlines()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "action_registry.txt"
            path.write_text(
                "\n".join(
                    [
                        *(line for line in lines if "show_message" not in line),
                        "instruction show_message",
                        "instruction gone_upstream | was here once",
                    ]
                )
            )
            problems = actions.check(path)
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("has no reason", problems[0])
        self.assertIn("gone_upstream", problems[1])
