# P1-T15b3 independent code review (2026-11-16)

An Opus builder (high) built per-facility pack kinds: design §4 (in `design.md` of this folder)
plus items 11, 13 and 14. The pieces:

- `ihr_refdata/catalogue.py` (a shared base for per-facility kinds);
- `PackItem.facility` and the new `PackFacility` record (migration 0005);
- the `after:` order between packs;
- an on-commit task for a new facility, and a 15-minute backstop per hospital (`ihr_refdata/tasks.py`);
- `--facility` on `ihr_pack_pending`, and `?facility=` on the API;
- the test-only kinds `t_specimen` and `t_activity`, registered only while `IHR_REFDATA_TEST_KINDS`
  is on (`settings/secure.py` refuses the flag on a server);
- the alert `PackFacilityDeferred`;
- 5 mutation rows.

No catalogue content ships: content comes with Phase 2.

The task is tenancy-type (links and items must never cross a facility or a hospital), so it got
the full review shape:

- **Reviewers:** three Opus 5.5 reviewers at high effort, none of whom wrote the code, each through
  one lens: tenancy and facility isolation (T), the engine (E), and tests and operations (Q).
- **Challengers:** one batched Opus challenger per lens tried to refute each finding. Of 19
  findings, 1 was refuted (Q6), 8 confirmed and 10 confirmed in part.
- **Fixer:** an Opus fixer at high effort fixed all 18 surviving findings. Each fix has a
  regression test that failed with the fix reverted.
- **Verifier:** an Opus verifier ran these checks itself:
  - it re-ran every suite;
  - it applied the 5 mutation rows;
  - it reverted all 18 fixes in groups and read each failure for its own reason;
  - it checked every file against hashes it took before starting.

  It made no late fix.

Workflow: 9 agents, 1.79 M tokens, 2 h 44 min. The estimate was 1.7 M.

## Findings

Paths are under `plugins/ihr_platform/ihr_refdata/` unless named.

| # | Finding | Severity (reviewer → challenger) | Fix |
|---|---|---|---|
| T1 | `links_of()` mapped a soft-deleted or superseded link target to its pack key, so a row linking to a dead target looked in sync | medium → low (partly) | A link to a dead row reads `<key>~stale`. A row whose only difference is a stale link is not counted as the hospital's edit. The link is re-pointed to the live row with that key, or the item is held `missing-link` (`catalogue.py`, `engine.py`, default `stale_hash` in `kinds.py`). Tests: `test_a_deleted_target_waits_…` and `test_a_link_to_a_replaced_target_is_re_pointed_to_the_live_row`. **Limit:** deleting a target alone does not wake the start-up short-circuit. The next reconcile of that facility catches it. |
| T2 = E1 = Q1 | `linkable()` said yes when any target was live, not when the held item's missing ones were back, so the short-circuit (item 11) never settled and the backstop reconciled on every run | low / **medium** → **medium** (E1) | A held item counts only when every key it links to is live. Still one query per link field. Test: `test_one_live_and_one_missing_target_is_not_reconciled_again`. |
| T3 = Q2 | After an image roll-back, a facility pack skipped as "older" was never marked, so every facility lagged forever. The backstop then took every hospital's lock every 15 minutes. | low/medium → low | A newer `PackFacility` version counts as complete (`applied_ahead`). The task drops packs this hospital already took a newer version of. Test: `test_after_an_image_roll_back_nothing_lags_and_no_lock_is_taken`. |
| E2 | The backstop counted every missing-link facility as lagging before the cheap check. It took the lock and wrote false "deferred" lines. | low → low | `needs_filling` uses the `ihr_behind` annotation (still one query) and filters before the lock. |
| E3 | `decide()` checked the facility's deleted flag on a stale instance, outside the transaction | low → low (partly) | It re-reads the facility with FOR NO KEY UPDATE inside the transaction. Test: `test_a_facility_deleted_after_it_was_read_is_never_decided_in`. |
| E4 | A link target deleted between the unlocked check and the locked write raised ValueError. The whole apply aborted, and the decision API answered 500. | low → low (partly) | `missing_link` resolves targets with `lock=True`. Test: `test_link_targets_are_locked_when_checked`. |
| E5 | A CARE create racing an engine create could leave two live rows that the short-circuit never noticed | low → low (partly) | `RECHECK_CONFLICTS = True`: a duplicate made during the apply is caught after commit. No advisory lock was added, because CARE never takes one. Test: `test_a_duplicate_made_during_the_apply_is_found_after_commit`. |
| E6 | An accept refused for a missing link left the stored reason `edited`, so the list never showed it as blocked | low → low (partly) | A conflict or missing link that refuses an accept is stored as the reason on the PENDING item. |
| E7 | `check_order` raised KeyError, not PackError, for a link to a kind this build does not register | low → low (partly) | It is now a fleet-wide PackError. |
| Q3 | `PackFacilityDeferred` could reset forever: a 20-minute window against a 15-minute cadence, with `for: 2h` | medium → **medium** | The window is now 40 minutes. A test reads `FILL_EVERY_SECONDS` from `tasks.py` and requires a window of at least twice that (`scripts/test_staging_logs.py`). |
| Q4 | The staging runbook covered neither the new alert nor the per-facility mechanism | medium → low | `deploy/staging/README.md` gains an alert row and a "Per-facility packs" paragraph. `test_every_alert_is_in_the_staging_runbook` covers it. |
| Q5 | The alert's action pointed at PackApply rows that the worker path never writes | low → low (partly) | The action points at the workers' `ihr-packs` log lines. |
| Q6 | No test failed when `decide()` stopped filtering by facility | low → refuted | none needed |
| Q7 | The backstop named no facility when it failed before working out which facilities lag | low → low (partly) | The failure names the lagging facilities, or `facility=unknown`. |
| Q8 | The deferred line named the facility by its internal pk, which no operator tool accepts | low → low (partly) | The line carries the facility's external UUID. The alert regex and its cases (a numeric forgery, `unknown`) were updated. |
| Q9 | `ihr_pack_pending` ignored `--facility` when listing or rechecking | low → low | `--facility` now filters the list, is refused with `--recheck`, and is named in the success message. |

The fixer also found and fixed one bug of its own, before the final runs: the retire branch treated
an empty `applied_sha256` as matching an empty `stale_hash`.

## Verification

Verifier, with `IHR_DB_ROLES=test` and `git add -N` on the 5 new files:

| Suite | Result | Redis keys (all tenant-prefixed) |
|---|---|---|
| ihr_refdata, ihr_refdata_registry, ihr_audit, ihr_identity | 418 OK | 196 |
| ihr_access (role matrix included) | 202 OK | 393 |
| isolation | 26 OK | 165 |
| test_restore, test_db_roles, test_release, test_secure_settings | 211 OK | — |
| `scripts/test_staging_logs.py` | 45 OK | — |

Also passing: `makemigrations --check ihr_refdata` (no changes), `isolation-mutation.sh check`, and
`pre-commit run --all-files`.

All 5 mutation rows FAIL their named tests:

- `facility-links-cross`: a link resolved in another facility;
- `facility-test-kinds-guard`: the test kinds allowed on a server;
- `facility-beat-hold`: the backstop runs in a held hospital;
- `facility-beat-scope`: the backstop touches hospital-wide items;
- `facility-task-dedup-key`: the dedup key without the hospital.

Every fix failed its test when reverted. One gap remains: Q4's table row alone is not caught,
because the paragraph also names the alert. Removing every mention fails.

My own re-run before the commit: see TRACKER.

## Implementation choices kept without a question

These are reversible in code and have no live data yet.

1. The per-facility record of "applied at this release" is a new table, `PackFacility` (pack,
   facility, version, sha256). It gets the app role's default for a hospital table (DML), so
   `hospital_tables.txt` needs no line; the database-role and audit tests pass with it.
2. The task fills every live facility that lacks the current release, not only the one that
   queued it. A create the dedup key skipped is still covered, and a facility that already has
   the release is never touched.
3. The backstop also reconciles facilities that hold a missing-link item, but only once all its
   targets are live. This is Q-K68's "retried automatically" between start-ups.
4. A task run that fails gives up at once. A run that finds the hospital busy retries 3 times
   (30, 120 and 300 s). The backstop writes the deferred line at every run it cannot fill. The
   alert needs 2 hours of lines.
5. If any per-facility release is unrecorded, the whole task or backstop run is skipped.
6. A task or backstop run writes PackApply APPLIED only when it wrote something, and never REACHED.
7. A facility catalogue item may carry exactly its kind's keys, so it carries no prices (Q-K69).
8. A kind may be a class (`Catalogue`), not only a module.
9. `?facility=` accepts only a lower-case UUID. An upper-case one gets 400.

## Open points

1. No catalogue content ships before Phase 2. Only the test kinds exercise this, and the
   `external_id` binding is Q-K75, for later.
2. The dev stack needs `scripts/care-dev.sh migrate-hospitals` for migration 0005.
3. Deleting a link target alone does not wake the start-up short-circuit. The next reconcile of
   that facility re-points or holds the link.
