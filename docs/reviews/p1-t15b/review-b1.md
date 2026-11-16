# P1-T15b1 independent code review (2026-11-16)

Three reviewers who did not write the code (Opus 5.5, high effort) read the hospital admin's pack
updates API: `ihr_refdata/{api,diff,urls}.py`, the engine changes (`decide(expect=…)`,
`replayed`, the change list), `valuesets.py`, migration 0002, `IsHospitalAdmin` (moved to
`ihr_identity/staff_access.py`), the matrix and isolation rows and the two new mutation rows.
The lenses were access and leaks (A), transactions and correctness (T), and tests, CI and docs (Q).
One challenger per lens (Opus 5.5) tried to refute each finding. Of 14 findings: 0 refuted,
4 confirmed in part (A1, A2, A3, T3), 10 confirmed. A fixer (Opus 5.5, high) fixed 12, left A3
to the owner (Q-K76) and did Q7 in part (TRACKER was mine). A verifier (Opus 5.5, medium)
re-proved the fixes by reverting them and applied both new mutation rows; it made no late fix,
and a re-check confirmed the files matched its backups byte for byte.

## Findings

| # | Finding | Severity (reviewer → challenger) | Fix | Test | Proof |
|---|---|---|---|---|---|
| A1 | A POST "replay" answered 200 with the full detail for an item that was never pending (current, or retired by a start-up apply), skipping the 404 rule | low → low (partly: the replay must come before the state check) | `engine.replayed` also needs the newest accept or keep decision on that item to match the action and version | `DecisionTests.test_an_item_no_one_decided_is_never_a_replay` | revert → FAIL |
| A2 | The in-flight and rate-limit cache keys were never written in the isolation or matrix runs, and the `cache-key-function` mutation could not see them | low → low (partly: the runner's Redis audit already catches an unprefixed key) | raw-key test, isolation test that fills hospital A's limits and checks B is unaffected; both named in the `cache-key-function` row | `DecisionTests.test_both_limits_are_kept_under_this_hospitals_schema`, `test_pack_decision_limits_are_per_hospital` | all named tests FAIL under the mutation |
| A3 | `IsHospitalAdmin` accepts an admin on any top-level organisation, state government organisations included | low → low (partly: older rule, hard to exploit) | not fixed: nothing records which organisation is the hospital's root; narrowing to `team` would lock out every current admin | — | **Q-K76** (A taken: keep; B before go-live) |
| T1 | The stale check ignored a soft delete, so accepting an "update" could silently undelete a row | low → low | `valuesets.decision_hash` (fields plus `deleted`) and `engine.row_sha256`, used by the detail, the pre-check and `decide` under the lock | `test_a_soft_delete_after_the_look_is_stale` | revert → FAIL |
| T2 | Record-only writes were unlisted: a pending item the pack withdrew vanished, and an "applied" row could list no changes | low → low | `settled()` lists `resolved` or `adopted`; kept items that follow a release list as `kept` | `ChangeListTests.test_each_kind_of_change_is_listed_once`, `test_a_pending_change_the_hospital_took_by_hand_is_resolved` | revert → FAIL |
| T3 | Migration 0002 gave every older applied row `changes=[]`, which reads as "nothing changed" | low → low (partly: older rows lack `changes_total`) | history shows `changes: null` for an applied row without `changes_total` | `ListTests.test_an_apply_from_before_the_change_list_shows_none` | revert → FAIL |
| T4 | The per-process release cache could raise `KeyError` under gunicorn's threads | low → low | one `.get()`, the cache replaced in one assignment | `ReleaseCacheTests` | test written to fail with `KeyError` on the old code |
| Q1 | ruff lint and format failed on the new code (CI would go red) | medium → medium | rename to `_BadQueryError`, unused `noqa` removed, formatted | pre-commit | 45 hooks pass |
| Q2 | The matrix row for the detail route passed with the gate removed (its pk was not a pack item, so 404 counted as deny) | medium → medium | the matrix world seeds a waiting pack item (`_MatrixWorld.packs()`) and passes it as `pack_item_id` | `RoleMatrix.test_role_matrix` | under `packs-api-gate`, 36 matrix failures, 12 on the detail row |
| Q3 | The isolation check on the pack detail route did not prove the answer was the tenant's own row | low → low | the answer must contain `iso-<canary>` (`PACK_ITEM_ROUTE`) | `test_isolation` | FAIL for both tenants with the item renamed |
| Q4 | The API told the admin "the operators were alerted", but nothing logs or alerts on it | low → low | wording: "try again later, and tell your operator if it persists"; `FAILED_NOTE` likewise | `ListTests.test_unreadable_packs_answer_503_and_never_claim_an_alert` | revert → FAIL |
| Q5 | As A2 (the design's "both limits in the matrix and isolation runs" was not met) | low → low | as A2 | as A2 | as A2 |
| Q6 | Most of the change list had no test (the 500 cap, `changes_total`, updated/restored/repaired/retired) | low → low | `ChangeListTests` (the cap patched to 2, the real total kept) plus the T2 tests | `test_the_list_is_cut_at_max_changes_with_the_real_total` | removing the slice → FAIL |
| Q7 | TRACKER and matrix comments did not match the build | low → low | matrix comments say `ihr_identity IsHospitalAdmin`; TRACKER split and status done by the orchestrator | — | — |

## Verification

Run by the verifier with `IHR_DB_ROLES=test`: ihr_refdata, ihr_refdata_registry, ihr_audit and
ihr_identity 313 OK; the role matrix 31 OK; isolation 25 OK (159 Redis keys, none without a
hospital prefix); `pre-commit run --all-files` 45 passed; `isolation-mutation.sh check` OK.
Mutations: `packs-api-atomic` FAILs `CommitInsideTheLock`; `packs-api-gate` gives 49 failures
(GateTests 13 subtests, the matrix 36). My own re-run before the commit: 540 tests OK (those
labels plus the whole of ihr_access and isolation); every Redis key carried a hospital prefix.

## Open points (no fix in b1)

1. Q-K76 (A3): who counts as "the hospital" for the hospital-wide admin gate. b2's forms linking
   uses the same rule.
2. `CommitInsideTheLock` drops its own hospital schema at class end, as the isolation world does;
   design item 15 says audit trails in tests are never cleaned up. Kept, noted.
3. No matrix role passes the gate, so the matrix cannot reach the two limits; the isolation run
   and `test_api` cover them.
4. Migration 0002 changes the code fingerprint: the dev stack needs
   `scripts/care-dev.sh migrate-hospitals`.
