# P1-T15b2a independent code review (2026-11-16)

An Opus builder (high) built the kinds contract (`ihr_refdata/kinds.py`), the hospital-wide
identifier-config kind (`identifiers.py`), the engine's write gates (lock, conflict, data check,
privacy, counter restart), migration 0003 (`PackItem.target`, `PackItem.checks`), and the swap of
CARE's per-process identifier-config dict for its Redis cache (`ihr_platform/identifier_cache.py`,
check `ihr_platform.E025`). Three reviewers who did not write it (Opus 5.5, high) read it through
three lenses: the cache swap and CARE's behaviour (C), engine correctness and concurrency (E), and
tests, operations and docs (Q). One challenger per lens (batched) tried to refute each finding:
21 findings, 0 refuted, 17 confirmed, 4 confirmed in part (E3, Q5, Q7, Q9). An Opus fixer
(high) fixed all 21, each with a regression test that failed with the fix reverted (4 batched
revert runs). An Opus verifier (high) re-ran everything, reverted 7 fixes itself (all failed as
expected), applied the five mutation rows, and made no late fix.

## Findings

| # | Finding | Severity (reviewer → challenger) | Fix (paths under `plugins/ihr_platform/`) |
|---|---|---|---|
| C1 | The privacy gate counted "retrieve with date of birth" and "with OTP" as protection, but CARE's server checks only the year of birth | medium → medium | `identifiers._loose`: partial search or no year of birth is loose; turning dob or OTP off still holds the item (subtest "dob") |
| C2 | The privacy gate looked only at active configs, but CARE's search uses configs of any status | medium → medium | loosening is compared for any existing row (subtest "inact") |
| C3 | E025 could not see the upstream changes it claimed to catch | low → low | `install()` leaves a missing lookup alone so E025 fires; CARE's `get_config` pinned by sha256 and its 2 callers counted (`CarePins`) |
| C4 | CARE clears the Redis copy before commit, so a concurrent reader could put the old config back for up to 300 s | low → low | a post_save receiver clears both keys again after commit, in the writer's schema |
| C5 | Mutation row `identifier-config-swap` passed on the system check alone | low → low | the row now removes the swap and the check's registration; four new rows (`-check-registered`, `-check-itself`, `-behaviour`, `-after-commit`) |
| E1 = Q1 | **A counter-restart hold vanished on the next apply, so a new number series was created without the admin** | **high → high** | the hold, or a kept create, sticks for the same content (`engine.py`); `test_a_kept_number_series_is_never_created_unasked` |
| E2 | A conflict found after commit was cleared by the next apply when CARE's racing duplicate was the newer row | medium → medium | conflict re-asked before every branch when the reason is conflict or 2+ live rows exist |
| E3 | Reconcile wrote from a row read before the lock and the data checks, overwriting a hospital edit made meanwhile | medium → medium (partly) | `moved()` re-reads the row under its lock; a changed row is held as edited |
| E4 | An accept held FOR UPDATE on the config for up to 90 s of data checks, blocking patient identifier inserts | medium → medium | FOR NO KEY UPDATE; an accept reuses a stored check while content and watermark hold; in-flight mark 150 s |
| E5 | A kept item whose stored reason was conflict or data-check could never be accepted after the problem was fixed | medium → medium | stored reasons refuse only pending items; a kept item is checked afresh at accept |
| E6 | Passed data checks were thrown away, so every reconcile reran them | low → low | a passed check is stored with the hold |
| E7 | Data checks ignored status, but CARE validates only active configs | low → low | checks follow the rule in force (before: live active row; after: item active) |
| Q2 | `ihr_pack_pending --recheck` wrote into hospitals `ihr_pack_apply` refuses (suspended, restore hold), and its failures were neither recorded nor alerted | medium → medium | recheck goes through `ihr_pack_apply`'s selection and failure recording |
| Q3 | The "not fully checked" alert line fired only on the run that hit the cap | medium → medium | a stored incomplete check counts as unchecked at every apply |
| Q4 | Operators could not see what held an item, and the alert pointed to a command that did not clear it | low → low | the listing shows each hold; the cap line names pack and item; alert text and the staging README runbook updated |
| Q5 | as C5, and E025 itself had no mutation | low → low (partly) | as C5 |
| Q6 | The start-up short-circuit tests could not tell a skip from a no-write reconcile | low → low | `apply_skipped()` asserts `_reconcile` is not called; held creates no longer force a reconcile |
| Q7 | The privacy rules were only partly tested; the edited and deleted paths skipped or mislabelled them | low → low (partly) | a deleted row's hold takes privacy from `admin_reason`; subtests p1, dob1, otp1, ed, del, none |
| Q8 | as C4 (the cache test proved "seen at once" only inside one transaction) | low → low | as C4 |
| Q9 | An accept ran the full data checks inside the decision's lock, and a cap hit there was neither stored nor alerted | low → low (partly) | the accept's check is stored on the item as data-check and logged |

## Verification

Verifier (`IHR_DB_ROLES=test`): ihr_refdata, ihr_refdata_registry, ihr_audit, ihr_identity and
`ihr_platform.tests.test_identifier_cache` 357 OK; isolation 25 OK (163 Redis keys, all
hospital-prefixed); ihr_access 202 OK; `pre-commit run --all-files` and `isolation-mutation.sh
check` pass; `makemigrations --check` no changes. Seven fixes reverted at once: 8 failures and
1 error, as expected. Mutation `identifier-config-swap` FAILs its 3 tests; the four new rows FAIL
their 4 tests (run as one batch). Retired row `identifier-lookup-cache`: with CARE's own key patch
reverted its test passes, so the row had become a no-op.

## Decisions taken outside the design

Builder judgement calls the owner may want to see: Q-K77 (a deleted config still answers lookups),
Q-K78 (value sets keep the exact slug match), Q-K79 (held identifier items are re-checked at
every start, capped at 90 s). Implementation choices kept without a question: the order of hold
reasons (conflict, data-check, privacy, counter-restart, then deleted, edited, unknown-origin);
identifier systems compared in lower case with the row's own spelling kept; data-check counts are
patients; `--recheck` is a full apply with every data check forced; the cap alert line comes only
from the two commands (provisioning logs a warning).
