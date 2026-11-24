# P2-T0a: CARE bugs to report upstream (drafts, Q-K168)

Found while merging CARE `develop` at `ddfd840dbe31` (2026-11-23). The owner posts these on
`ohcnetwork/care` (one issue each); Claude posts nothing. Each draft is ready to paste.

---

## 1. Test action instruction `logging` and test context field `TestContext1` are registered in production

**Where:** `care/action_evaluator/instruction_engine/instructions/logging_test.py` (imported by
`instructions/__init__.py`), `care/action_evaluator/context_engine/fields/patient.py`
(`TestContext1`, `ActionFieldRegistry.register(TestContext1)`).

**What happens:** both are registered at import time in every deployment, not only in tests.
`LoggingActionInstruction.authorize()` returns `True` for every user, and `evaluate()` writes the
action's rendered inputs to the application log at INFO
(`logging.info(f"Logging Action Instruction: {self.inputs}")`). An action configuration that uses
it can therefore copy appointment and patient values into the logs, and any signed-in user can run
it through `execute_action`.

**Expected:** test-only pieces are registered only by the test settings (or live under
`care/.../tests/`), and an instruction's `authorize()` never answers `True` unconditionally.

**Workaround we use:** unregister both at start-up and check the registries against a reviewed
list.

---

## 2. `data/questionnaire_fixtures.json` lacks `auth_context`, so every demo questionnaire is silently dropped

**Where:** `data/questionnaire_fixtures.json`, loaded by
`care/fixtures/base.py` `load_questionnaires_from_file`.

**What happens:** questionnaire create now requires `auth_context`, and none of the fixture's
entries has one, so every create answers 400. `load_questionnaires_from_file` catches
`FixtureError` and continues, so `load_fixtures` finishes with no questionnaires and no message.

**Expected:** each fixture entry carries `"auth_context": "instance"` (or the loader sets it), and
the loader reports a failed entry instead of passing over it.

---

## 3. Value set `recent_views` does not look up the value set

**Where:** `care/emr/api/viewsets/valueset.py`, `ValueSetViewSet.recent_views` (and
`remove_recent_view`).

**What happens:** the action reads the lookup value from the URL and builds the cache key from it
without calling `get_object()`, so it answers 200 for any id, including a value set that does not
exist or that the user cannot open (it returns that user's cached list for the id, empty or not).
`recent_views_by_slug` does resolve the value set first.

The same holds for `clear_recent_views` (POST answers 200 for any id, because it never looks the
value set up) and `favourites` (GET answers a warm cache entry before `get_object()`, so it can
still answer 2xx after the person lost access to the value set).

**Expected:** `recent_views`, `remove_recent_view`, `clear_recent_views` and `favourites` call
`self.get_object()` first, so an unknown or unreadable value set answers 404 like the other
detail actions.

---

## 4. `InterpretationEvaluator` looks up a value set by slug with `.get()`, which fails once a facility copy exists

Found by the second P2-T0a review (D5), after Q-K168 approved items 1–3; post it only if the
owner agrees.

**Where:** `care/utils/evaluators/interpretation_evaluator.py`, `check_valueset`
(`ValueSet.objects.get(slug=valueset)`).

**What happens:** CARE 3.1 lets a facility admin create an inherited facility or user copy of a
value set, and the copy takes the parent's slug (`resources/valueset/spec.py`). Slugs are now
unique only per auth context, facility, organisation or user, so once such a copy exists the
unscoped `.get()` raises `MultipleObjectsReturned`. Every interpretation rule that names that
value set then fails, and writing a lab result answers 500 in every facility of the deployment.

**Expected:** resolve the value set the way the rest of CARE does (`get_closest_valueset`), or at
least filter `auth_context="instance"`.

---

## 5. A PUT on a past questionnaire revision answers 400 instead of 403

Found by our CI after the P2-T0a merge (24-11-2026). CARE's own `Test` job fails on the same test at
`ddfd840dbe31` (`care.emr.tests.test_questionnaire_scopes.TestQuestionnaireScopes.test_question_changes_create_a_revision_preserving_scope`:
`AssertionError: 400 != 403`). Post it only if the owner agrees.

**Where:** `care/emr/api/viewsets/questionnaire/questionnaire.py`, `QuestionnaireViewSet.validate_data`,
added by 84c3f05 ("add slug uniqueness validation to Questionnaire model", #3851).

**What happens:** `EMRUpdateMixin.handle_update` runs `validate_data` before `authorize_update`. A
past revision keeps its slug, and the latest revision has the same slug, so the new uniqueness check
answers 400 "Questionnaire with this slug already exists" before `authorize_update` can answer 403
"This Questionnaire is a past revision". Nothing is written either way; only the answer is wrong.

**Expected:** skip the slug check for a past revision (it can never be updated), or run
`authorize_update` before `validate_data`. Our local patch does the first
(`docs/UPSTREAM_PATCHES.md`, 2026-11-24).
