# ruff: noqa: SLF001 (CARE's registries have no public way to list or remove an entry)
"""CARE's action engine: only reviewed instructions and context fields (P2-T0a, review F3).

CARE 3.1 added an action engine: questionnaires, bookings and slots carry stored actions whose
instructions run with values taken from the patient, the visit and the answers. Every
instruction and context field CARE (or a CARE plugin) registers is reachable by any author of
such an action. Upstream ships two test pieces registered in production: the ``logging``
instruction, whose ``authorize()`` always passes and which writes its inputs (patient names,
phone numbers, answers) to the INFO log, which our log store keeps 180 days and the archive
400 days; and the dynamic field ``TestContext1``, which answers ``"test1234"`` for anything.

``prune()`` (from ``IhrPlatformConfig.ready``) takes those two out of CARE's registries, and
``system_check`` (``ihr_platform.E028``) refuses to start while any registered instruction or
field is missing from the reviewed ``action_registry.txt``, or a removed one is back. A new
upstream instruction or field therefore fails start-up and the tests until it is read and
classified there with a reason.
"""

from pathlib import Path

REVIEWED_FILE = Path(__file__).with_name("action_registry.txt")

# Removed at start-up, never served: test pieces upstream registers in production
REMOVED_INSTRUCTIONS = frozenset({"logging"})
REMOVED_DYNAMIC_FIELDS = frozenset(
    {"care.action_evaluator.context_engine.fields.patient.TestContext1"}
)


def _dotted(cls):
    return f"{cls.__module__}.{cls.__qualname__}"


def _registries():
    # Importing the engine registers CARE's own pieces (care.emr.apps does the same import
    # later; a module runs once, so nothing is registered again after prune())
    import care.action_evaluator  # noqa: F401
    from care.emr.registries.actions.field import ActionFieldRegistry
    from care.emr.registries.actions.instruction import ActionInstructionRegistry

    return ActionInstructionRegistry, ActionFieldRegistry


def prune():
    """Take the removed instructions and dynamic fields out of CARE's registries."""
    instructions, fields = _registries()
    registered = instructions._instructions  # CARE has no unregister
    for slug in REMOVED_INSTRUCTIONS:
        registered.pop(slug, None)
    dynamic = fields._dynamic_contexts
    for context_type, entries in dynamic.items():
        dynamic[context_type] = [
            f for f in entries if _dotted(f) not in REMOVED_DYNAMIC_FIELDS
        ]


def registered_keys():
    """Every registered piece as the key ``action_registry.txt`` names it."""
    instructions, fields = _registries()
    keys = {f"instruction {slug}" for slug in instructions._instructions}
    # Static and global fields alike are kept per context (register() also files each under
    # its name in _global_fields, which therefore holds nothing of its own)
    for context_type, entries in fields._contexts.items():
        keys |= {f"field {context_type}.{name}" for name in entries}
    for context_type, entries in fields._dynamic_contexts.items():
        keys |= {f"dynamic {context_type} {_dotted(f)}" for f in entries}
    return keys


def reviewed_keys(path=REVIEWED_FILE):
    """``key | reason`` lines; a line without a reason is a problem, not a review."""
    keys, problems = set(), []
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, reason = (part.strip() for part in line.partition("|"))
        if not reason:
            problems.append(f"{path.name}:{number} has no reason: {line!r}")
        keys.add(key)
    return keys, problems


def check(path=REVIEWED_FILE):
    keys, problems = reviewed_keys(path)
    present = registered_keys()
    instructions, fields = _registries()
    problems += [
        f"{key} is registered but not reviewed in {path.name}"
        for key in sorted(present - keys)
    ]
    problems += [
        f"{key} is reviewed in {path.name} but no longer registered (remove the line)"
        for key in sorted(keys - present)
    ]
    problems += [
        f"instruction {slug} is registered again; it must stay removed"
        for slug in sorted(REMOVED_INSTRUCTIONS)
        if instructions.get_instruction(slug) is not None
    ]
    problems += [
        f"dynamic field {_dotted(f)} is registered again; it must stay removed"
        for entries in fields._dynamic_contexts.values()
        for f in entries
        if _dotted(f) in REMOVED_DYNAMIC_FIELDS
    ]
    return problems


def system_check(app_configs=None, **kwargs):
    from django.core.checks import Error

    return [
        Error(f"action engine: {problem}", id="ihr_platform.E028")
        for problem in check()
    ]
