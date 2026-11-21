# P1-T22c2a build note: disaster point-in-time restore events, core (21-11-2026)

Builder: Claude (Opus 5.5, high), unattended run. Design: `design-c2.md`, its section "c2a as
decided" (written first, 80 lines; it wins over §1-§13), bound by the "Decision" of
`attack-c2.md`. Nothing is committed. Synthetic data only. graphify: 21 calls (15 `explain`,
6 `affected`) before the code was read or changed.

## Files

New: `plugins/ihr_platform/ihr_audit/tests/test_disaster.py` (23 tests), this note,
`proof-c2a.log` (the live proof, filtered).

Changed:
- `ihr_audit/events.py`: export doc (`ExportDoc`, `_export`, `export_key`, `export_document`),
  stub fields on `Plan` (`_plan`: `flow`, `source`, `no_supersede`), `closed` and `unreadable`
  on results, `Events.exports`/`unfinished`, `events_store`, the floor and the lower bound,
  `check_whole`'s disaster half, `read()` reorders, `Segment.latest_at`, `NO_SEGMENTS`.
- `ihr_audit/anchor.py` (`claims_at_full`), `ihr_audit/archive.py` (`latest_at`),
  `ihr_audit/supersede.py`, `ihr_audit/watch.py`,
  `ihr_audit/management/commands/ihr_audit_supersede.py` (`stub`, `export-copy`, `export-doc`,
  `close`, `level`).
- `scripts/staging.sh`; `scripts/isolation-mutation.sh` (5 rows).
- `deploy/monitoring/prometheus/rules/audit.yml` and `tests/audit.yml` (2 alerts, 1 case group);
  `deploy/staging/README.md` ("Disaster restores", alert rows, runbook line); `CLAUDE.md` (one
  sentence); `docs/QUESTIONS.md` (Q-K99…Q-K103); `docs/reviews/p1-t22c/design-c2.md`.
- Tests: `scripts/test_release_scripts.py` (`DisasterScript`, 9 tests), `test_staging_backup.py`
  and `test_restore_quarantine.py` (the drill's body is now `repo_copy`),
  `test_staging_monitoring.py` (2 alerts; its tracker check was already failing on HEAD since
  2ba7913ca split the row: it now asks for an open P1-T22c2 row).

## How each item is met

| Item | Where |
|---|---|
| Stub just before the pgBackRest restore, `state` at that step, no database, events key only | `staging.sh:1390` (inside `restore_live`, after its refusals), `supersede_stub` `:2350`; `supersede.py:1099` `write_stub` (`events.read(..., segments=NO_SEGMENTS, timeline=None)`) |
| Refusals with their way out (no image, level < 2, no events key) before anything is replaced | `sup_disaster_precheck` `staging.sh:2335`; `restore_cmd` `:2387` |
| Parent timeline from the history file; a mismatch blocks the export (`close` it) | `supersede_parent` `:2373` (hook `:1406`); `supersede_export_copy` `:2428` |
| Serve first; copy from the repository (`repo_copy`, shared with the drill); copy goes on every exit | `repo_copy` `:1260`; `supersede_export_copy` (trap before the copy, OOM-first, dumper stopped, disk from `pgbackrest info`) |
| Export with c1's code as the watch role; per-hospital savepoints; `--accept-unreadable` | `supersede.py:1178` `export_disaster` (calls `export_one`) |
| Export doc: sha256 and L checked again, events key only | `supersede.py:1285` `write_export`; parser `events.py:816` `_export`, `:882` `_check_exports` |
| Fork point bound by entry times, floor (audit S2), load and every reader | `events.py:1016` `fork_floor_problem`, `:1007` `FLOOR_MARGIN` (15 min, Q-K99), `:1109` `check_floor`; `supersede.py:697` `disaster_fork`, `:730` `_fork_floor`; `archive.py:269` `latest_at` |
| `check_whole` binds a disaster event (export doc before it, L re-derived, L ≤ F < head, head) | `events.py:1078` `_check_disaster`, `:1041` `lower_of`, `:1060` `disaster_lower` |
| Disaster documents with the events store only (audit S5) | `events.py:178` `events_store`; `supersede.py` `write_stub`, `write_export`, `_load` (disaster), `close`; `supersede_keys --events-only` `staging.sh:2173` |
| Pending from plans alone (C2-1); start without history | `events.py:465` `unfinished`; `watch.py:656` gauges, `:678` young pending plan; rules AuditWholeRestorePending / …Overdue |
| Refuse a second export while a load is pending | `supersede.py:130` `_refuse_pending` (flow a export, stub, disaster export); export doc already there |
| `--no-supersede` result needs plan and timeline, no lost ranges | `restore_cmd` → `supersede_close --no-supersede` with `db` alone; `supersede.py:1347` `close` |
| `LEVEL`, missing = 1; rollback below the bucket's level refused | `supersede.py:79`, `:109`, `:114`; `sup_level` `staging.sh:2208`; `rel_level_guard` `:1816` (call `:2703`) |
| Local tar deleted after a verified load unless it holds more (S7 part) | `supersede_load`, `sup_local_only` `staging.sh:2493` |

## Tests (real counts)

- `IHR_DB_ROLES=test scripts/care-tenancy.sh test --settings=ihr_platform.settings.test --noinput ihr_audit ihr_platform.tests.test_restore ihr_platform.tests.test_release ihr_platform.tests.test_db_roles`:
  **413 tests OK** (130 s; Redis audit: 66 keys, all tenant-prefixed). c1 left 390; +23 in `test_disaster`.
- `python3 -m unittest`: `test_release_scripts` 94 OK, `test_dumps` 91 OK (7 skipped), `test_restore_quarantine`
  20 OK (6 skipped), `test_staging_logs` 48 OK, `test_staging_monitoring` 56 OK, `test_staging_backup` 17,
  `test_staging_db_roles` 26, `test_staging_keys` 20, `test_staging_s3` 50, all OK.
- `scripts/check-monitoring.sh` (promtool rules and tests): OK.
- `pre-commit run --all-files` (new files added with `git add`): 45 hooks passed. One earlier run had a
  single `test_dumps` failure inside the dumps-tests hook that did not repeat (91 OK alone and in the next
  two hook runs); not investigated.

**Mutation rows** (`isolation-mutation.sh check`: 199 rows OK; each new row run alone, `apply`, its
test, `expect` matched, file restored by sha256): **all FAIL as required**:
`supersede-events-store-only` (1 FAIL), `supersede-fork-floor` (2 FAIL: the load and the reader),
`supersede-pending-from-plans` (1), `supersede-refuse-second-export` (1),
`supersede-stub-before-restore` (1; the reader side: a stub stored after the watch saw the new timeline
explains nothing). The shell order (stub before the pgBackRest restore) is guarded by
`DisasterScript.test_the_stub_is_written_just_before_the_pgbackrest_restore`; rows run Django labels only.

**Reverts without rows** (each alone, test FAILED, file restored): the export doc stored before the
event; the reader's L re-derived; the export doc needs a disaster stub; the live-proof fixes below.

## Live proof (local staging proof, dev stack stopped; `proof-c2a.log`)

Stack: `init --local`, `s3-init` (events key), `mail-init --test`, `backup-init --local`, `up` (180 s),
alpha and beta, Keycloak sync, smoke 37 ok. Full backup; an edit per hospital, sealed and fingerprinted;
watch pass on timeline 1; **T = 14:19:47 IST**; two more edits after T, sealed and fingerprinted (alpha
seals 3-4, beta 2-3), WAL switched and archived (archive max `…0D`); then the `backup` service stopped and
one more edit sealed and fingerprinted (alpha seal 5, beta seal 4: after the repository's end). Disaster:
`down`, `pgdata` and `walspool` removed.

1. `restore --target T` **without the events key**: refused before anything (`rc=1`, no volume, no folder).
2. Attempt 1: stub written, restore 23 s, but parent read as `unknown` (**finding 1**, below). Attempt 2
   (fixed script; the first stub left as an orphan sibling): **restore 23 s**, parent `00000001`, new timeline
   `00000002`; `up` 98 s, smoke 37 ok, full backup.
3. Between serve and load: watch `timeline_unexplained 0`, `whole_restores_pending 2` (the orphan and this
   one), chain_broken 2, mismatch 2; `ihr_audit_verify --all` exit 1 naming both pending restores and the cuts.
4. `audit-supersede export`: **11 s** first try (copy of backup `20261121-141846F`, timeline 1 to its end,
   last transaction 08:50:03 UTC); 2 exported; export doc written; copy container and volume gone.
5. Negatives while pending: the same export again refused ("already exported"); a flow-(a) export refused
   ("whole-database restore 2852e03e, 2caf6f31 restored the database and has no result yet"); without the
   events key `audit-supersede close` refused by the shell, and the owner one-off given only the writer key
   refused `close` and `export-doc` (`_EVENTS_KEY/_SECRET are not set`).
6. `load`: the first build's final check refused its own events (**finding 2**). Fixed build B2, resumed with
   `load --image` (**3 s**): alpha fork 2, beta fork 1 (the heads at T), result written, local tar deleted.
   `ihr_audit_verify --all` (B2, owner): **4 claims accounted for by 2caf6f31**; the claims of alpha seal 5 and
   beta seal 4 (after the copy's end) **stay cuts** (exit 1). Watch pass: unexplained 0, pending 0 (the orphan
   is superseded), whole_restores 1, chain_broken 2 and mismatch 2 (those cuts, critical as decided).
7. Teardown: `down -v`, proof volumes, `.env .local secrets supersede releases`, new `generated/` folders,
   both proof images removed; `indiehr-backend:staging` back on `e34de095bdf8`; `care-dev.sh up`.

**Numbers:** restore 23 s; export 11 s (copy restore + recovery + export + export doc); load 3 s; up 98-180 s.
Memory (host used, this 6.7 GB machine with other sessions open): stack up 6.04 GB (800 MB available); during
the export 6.08 → 6.27 GB (peak +190 MB; the copy sampled at 35 MiB of its 768 MiB limit); restore peaks
4.1-4.6 GB (stack down). Tiny hospitals: these do not size a real server.

**Findings fixed (each with a test that fails on the old code):**
1. `pg_wal/<tl>.history` ends with an empty line: `tail -n 1` read nothing, parent `unknown`, the export would
   have refused. Now the last line with fields (`supersede_parent`); test runs the awk on such a file.
2. With no fingerprint at T − skew, `lower_of` took the hospital's events, so the reader, seeing the load's own
   event, re-derived L = F and refused every event. Now no claim means genesis, and only events stored before
   the stub lower L (an event on the new timeline, also a later single-hospital restore, would otherwise have
   broken the excuse later). Tests `test_with_no_fingerprint_at_t_minus_skew_…`, `lower_of` unit cases.
3. After the proof: `audit-supersede status` ran the export's build even after `load --image` (it showed the
   old bug), and `up`'s warning said "load" for a disaster restore not yet exported. Both fixed, test added.

## Deviations from the design

1. `restored.json` dropped (zoom-out S-3): the floor binds F in every reader; a local file added nothing.
2. Subcommand `export-copy` (not `export --disaster`): c1's `export` requires a restore point's arguments.
3. No `--slug` retry of one hospital: a failed export refuses as a whole (nothing written) or, with
   `--accept-unreadable --reason`, names it `unreadable`. In flow (b) the export doc never says `absent`: a
   hospital missing from the restored database is `absent` in the result (decided by the load), and one with
   no fingerprint at T − skew is exported from genesis (this also covers audit S8 for loads).
4. `close` (design Q-K104, now Q-K100) is in c2a: without it a pending load blocks restores for good.
5. Q-IDs renumbered from Q-K99 (the design's §12 numbers were never written to QUESTIONS.md).

## Known limits

- Fails closed when the sealer stopped for 15 min or more before T (the first seal after T holds only older
  entries): refused, `close` it. Up to 15 min of trail before T can still be rewritten by the database owner.
- An orphan stub (a restore that failed after its stub) stays pending until a sibling has its result; a failed
  attempt marks its folder `abandoned` by itself, a restore redone on purpose by `audit-supersede abandon <id>
  --reason` (review Q2).
- **The live proof never took the path where L comes from a real fingerprint** (review Q3): the only
  fingerprint before T was 37 s earlier, so T - skew (10 min) fell before it and every hospital was exported from
  genesis (`lower bound (seal 0)`). The branch where L is the claim at T - skew, re-derived by the reader, is
  proved by the unit tests (`Lower`, `ExportDoc`) and the mutation rows `supersede-reader-lower` and
  `supersede-lower-verified`, not by a live run. A live re-run needs a fingerprint written at least 10 minutes
  before T; that is the first thing to do in the next proof (c2b).
- The floor margin assumes idle-in-transaction bounds a transaction (review T4): it bounds idle gaps only, so a
  transaction open longer than the margin that commits just after T causes a false refusal, failing closed
  (`close` it). Q-K99 says so; `transaction_timeout` would reopen Q-K96.
- A restore straight after another one, before the next backup, has no backup set on the newest timeline and is
  refused with the way out (review E4); an older timeline's set is not used (right only for a T after the fork,
  and the fork time is not read at that step). Q-K106.
- Claims sealed after the repository's last archived WAL stay critical cuts (c2b); hospitals made after T
  stay "unknown" (c2b); `restore` without `--target` writes no events (Q-K102).
- The events key's IAM scopes and If-None-Match on a provider bucket, the copy beside a serving 8 GB server,
  and large tails (packages in memory) are unproven here.

## Deferred to P1-T22c2b

Lost ranges and `accept-loss` (audit S1, S3), absent closing (S6, S8 for the reader, C2-8), packages in the
dumps bucket and their expiry, the infra/aws events user (C2-3), `announce-timeline` (S4), streaming (C2-6),
`--slug` retries, `restore` without a time target, ADR-010 text.

## Review

| id | finding | severity (reviewer → challenger → recheck) | fix | test |
|---|---|---|---|---|
| T1 | `lower_of` and `fork_floor_problem` counted any restore event, including a forged single-hospital event with no segment. Such an event lowered L and excused a rewrite above it, and nothing reported it. | high → medium → high (a boundary: the events key's holder plus the database owner) | Readers (`_check_disaster`, `check_floor`) now count only `verified_before` events: no problems, an earlier generation, and stored no later than the event (`lower_of`: before the stub). The exporters and the load read no segments, because a restore after T has its segment only in the package. They go by time only, so a forged event leaves the export's L different from the readers' L, and the disaster event excuses nothing. **Known limit:** a database owner who also holds the events key could still archive a segment copied from the chain's own seals. That is for c2b, together with S6. | `ForkFloor.test_a_forged_single_hospital_event_excuses_no_rewrite`, `…test_the_floor_exception_counts_only_a_verified_earlier_event`, `Lower.test_a_forged_event_never_lowers_the_readers_bound`, `…test_the_readers_bound_counts_only_a_verified_earlier_event`. Mutation rows `supersede-lower-verified` and `supersede-floor-verified`. |
| E3 | An orphan stub (a failed attempt) could be closed after its sibling restored. The closed result then marked the real plan superseded, which silenced its pending and overdue alerts. | medium → medium → medium | `restore_live`'s exit trap (`sup_abandon_failed`) marks an attempt that failed after its stub as `abandoned`. It does nothing once the restore finished (`SUP_RESTORED`). `supersede_close` refuses a disaster stub with no `new_timeline`. Python `close` refuses while a later attempt from the same old timeline has no result (`Events.later_attempts`). A closed result now supersedes only plans stored before it. | `Close.test_an_orphan_stub_is_not_closed_while_a_later_attempt_waits`; `DisasterScript.test_a_failed_attempt_is_marked_abandoned_and_close_refuses_it`. Mutation rows `supersede-close-later-attempt` and `supersede-closed-earlier-only`. |
| Q1 | A retry's stub shared its old timeline with a closed attempt, so it was superseded from the moment it was written. It got no pending gauge, and `_refuse_pending` let the next stub through. | medium → medium (narrower: only while the repository's newest WAL is still on the old timeline) → medium | This is the same `superseded_plan` change: a closed result never supersedes a later attempt. | `Close.test_a_retry_after_a_closed_attempt_stays_pending`. Mutation row `supersede-closed-earlier-only`. |
| T3 | The stub's T was never bound to the real recovery target. | medium → low-medium → low | `supersede_parent` writes `recovery_stop` (the history file's "before" time) and `recovery_replayed` (`pg_last_xact_replay_timestamp()`) to state. `export-doc` takes `--recovery-stop`/`--recovery-replayed` and refuses unless replayed ≤ T < stop. The export doc carries `recovery`, and every reader requires it (`events.recovery_problem`). This deviates from the review's "within 1 s" (Q-K104): the stop is the first commit *after* T, so a quiet database stops later than T and is still correct. The c2b ceiling (S6) completes it. | `ExportDoc.test_the_export_doc_binds_t_to_the_restores_own_stop`, `…test_every_reader_requires_the_restores_stop`; `DisasterScript.test_the_restores_own_stop_reaches_the_export_doc`. Mutation row `supersede-recovery-bound`. |
| Q4 | `supersede_load` exported the writer key into a disaster load's one-off, and `load --image` checked only that the tool imports. | low-medium → low-medium → low | `supersede_load` uses `supersede_keys --events-only` when `flow=disaster`. `--image` for a disaster load needs `sup_level ≥ 2`. | `DisasterScript.test_disaster_documents_never_fall_back_to_the_writer_key` now covers `supersede_load`. |
| T5 | `close` uses the writer key for flow (a). | low → nit (refuted) | No change. Plans and results of flow (a) already use it; the fix is the site-wide events key. | none |
| E1 | `write_stub` (and the disaster export) refused while an older, served and unloaded restore was pending: on a lost server it can be neither loaded nor closed, and the way out was undocumented. | medium (reviewer high → challenger medium-high → recheck medium: a workaround existed) | `_refuse_pending(..., ancestors_pass=True)` for the stub and the disaster export: a pending plan from an older timeline than the repository's newest no longer blocks (it keeps alerting as pending); the refusal names each plan's next step and `close`. Q-K106. | `Pending.test_a_stub_from_a_later_timeline_passes_a_pending_ancestor` (the retry test now checks the flow-a export instead). Mutation row `supersede-ancestors-pass`. |
| T2 | A closed or `--no-supersede` result explains the timeline change, but nothing reported it: data after the last fingerprint is lost with no trace. | medium → low-medium → medium | Gauge `ihr_audit_whole_restores_closed{how}`, alert `AuditWholeRestoreClosed` (warning), a line per closed result in `ihr_audit_verify`, both until the restore id is in `ihr_audit/acknowledged_closed.txt` (`events.acknowledged_closed`). Q-K105. | `Close.test_closed_results_alert_until_acknowledged`, `test_the_acknowledged_closed_list_is_reviewed_lines`; promtool case `a restore closed without its load alerts until acknowledged`. Mutation row `supersede-closed-gauge`. |
| T4 | `FLOOR_MARGIN` assumed the idle-in-transaction limit bounds a transaction. | low → low (fails closed) → low | No code change: the comment and Q-K99 now say it bounds idle gaps only (no `transaction_timeout`, Q-K96); a false refusal fails closed (`close`). Recorded as a known limit. | `DisasterReviewScript.test_the_floor_margin_is_a_documented_limit`. |
| T6 | `close` keeps `export.tar` (patient-linked entries) with no expiry. | low → nit → low | `supersede_close` writes a `closed` marker; `up` and `audit-supersede status` name every closed restore whose tar is still on disk; the expiry rule is P1-T22c2b's (TRACKER, with Q-K90). The tar is not deleted (the only copy of the old tail). | `DisasterReviewScript.test_a_closed_restores_tar_is_listed_until_it_is_deleted`. |
| E2 | `close` and `load` ran `compose up db` without the volume check: an empty pgdata got a fresh tl1 cluster (blocking `restore_live`), a half-restored one was promoted outside it. | medium → medium → medium | `sup_db_up` reads `PG_STATE` first and refuses an empty or half-restored volume. | `DisasterReviewScript.test_close_and_load_check_the_volume_before_starting_the_database` (all three states, with a fake docker). |
| E4 | Right after a restore, until the next backup, no set is on the newest timeline: every `--target` restore died with "no backup set". | medium → low-medium → low | The message names the cause and the way out (`backup-now full`, or `--target-name`); an ancestor set is not used (right only for a T after the fork, not known there). Q-K106. | `DisasterScript.test_the_repositorys_newest_timeline_and_set` (stderr). |
| E5 | `rel_level_guard` ran only in `rollback`, and passed silently when the running build was below level 2. | medium → low → low | It runs in `release` (before the hold) and, as `staging.sh level-guard`, before `deploy.sh`'s plain start; it asks with any level-2 image on hand (`rel_level_tool`: the running build, else another tag of the repository), and passes only when none exists. | `DisasterReviewScript.test_the_level_guard_runs_in_release_and_before_a_plain_start`, `test_the_level_tool_falls_back_to_another_level_two_image`. |
| E6 | A crash between the export doc and the `exported` marker made a re-run copy again, then fail with "run it again". | low → low → low | `sup_has_export_doc` (the reader key, `status --json`) before the copy: it writes the marker and points to `load`. | `DisasterReviewScript.test_an_export_that_stopped_after_its_doc_is_not_copied_again`. |
| E7 | A young orphan stub explained any unrelated change away from its old timeline for 48 h (warning instead of critical). | low → low → low | Only the newest stub of that old timeline, stored after the watch last saw the old timeline and no later than its first sight of the new one. | `Pending.test_an_orphan_stub_explains_no_unrelated_timeline_change`. Mutation rows `supersede-young-stub-window` (renamed, Q7) and `supersede-orphan-stub-window`. |
| E8 | `close` of a stub with no export named no hospitals. | low → low → low | The hospitals of the fingerprint at T (`claims_at`), as `--no-supersede`. | `Close.test_closing_a_stub_with_no_export_names_the_hospitals_at_t`. Mutation row `supersede-close-names-hospitals`. |
| Q2 | README "A real restore" was stale (no `--operator`, events key or link; "simply starts again"; no retry path). | medium → medium → medium | README rewritten (retry, volume removal, events key); `audit-supersede abandon <id> --reason` marks a restore redone on purpose. | `DisasterReviewScript.test_a_restore_is_abandoned_by_hand_for_a_retry` (command and README words). |
| Q3 | The live proof never tested L from a real fingerprint (the only one was 37 s before T). | medium → medium → medium | Not re-run (a staging stack down for a proof is outside this fix); the limit is stated under Known limits. The unit tests and rows `supersede-reader-lower`, `supersede-lower-verified` cover the branch. | none (limit stated). |
| Q5 | The pending texts said "load <id>" for a disaster restore not yet exported. | low → low → low | `Events.next_step` (flow-dependent: "export <id>, then load <id>") used by `_refuse_pending` and `ihr_audit_verify`. | `Pending.test_the_refusal_names_the_next_step_of_each_flow`. Mutation row `supersede-next-step`. |
| Q6 | Without `secrets/anchor-write.env`, `restore --target` wrote no stub and said nothing. | low → low → low | Refused unless `--no-supersede --reason`, which warns. | `DisasterReviewScript.test_restore_target_without_a_fingerprint_writer_key_is_refused`. |
| Q7 | Row `supersede-stub-before-restore` mutated the watch, and `_check_disaster` had no rows for the export doc's time and the reader's L. | low → partly → low | Renamed `supersede-young-stub-window`; new rows `supersede-export-doc-before-event` and `supersede-reader-lower` (the `elif` with its context line, unique), paired with the existing `ExportDoc` and `Lower` tests; `supersede-lower-verified` and `supersede-closed-earlier-only` re-written for ruff's line breaks. | the rows themselves (`isolation-mutation.sh check`, `apply`, `test`, `expect`). |
| Q8 | The 14-day export warning had no test. | low → low → low | Test only. | `ExportWarning.test_the_export_warns_when_its_stub_is_older_than_fourteen_days`, `test_a_fresh_stub_gets_no_warning`. Mutation row `supersede-export-warning`. |

## Verification

Verifier: Claude (Opus 5.5, high), 21-11-2026; wrote none of this. sha256 of all 25 changed or new files
taken first and checked again at the end (only this section added). **No LATE FIX.** graphify: 15 `explain`
calls, 0 `affected` (nothing changed).

| check | result |
|---|---|
| `IHR_DB_ROLES=test … ihr_audit ihr_platform.tests.test_restore …test_release …test_db_roles` | **PASS**: 429 tests OK (132.7 s); Redis audit 66 keys, all tenant-prefixed |
| `python3 -m unittest` | **PASS**: `test_release_scripts` 104, `test_dumps` 91 (7 skipped), `test_restore_quarantine` 20 (6 skipped), `test_staging_logs` 48, `test_staging_monitoring` 56, `test_staging_backup` 17: 336 OK |
| `scripts/check-monitoring.sh` (promtool) | **PASS** |
| `isolation-mutation.sh check` | **PASS**: 212 rows |
| the 18 c2a rows (`supersede-events-store-only` … `supersede-recovery-bound`), each `apply` alone, its `test` labels, `expect` | **PASS**: 18/18 matched, every named test FAIL (no ERROR), each file restored by sha256 |
| `pre-commit run --all-files` | **PASS**: 45 hooks, rc 0 |

**Fixes reverted in groups** (shell ones in scratch copies of `scripts/`, the Python one in place and restored
by sha256); each named test went red for its own reason: E3 shell (trap and the never-promoted refusal:
`abandoned` not written), T3 shell (`--recovery-stop` gone), Q4 (`supersede_load` back to `supersede_keys`), E2
(`sup_db_up` lets `empty` through), E4 (message without `backup-now full`), E5 (deploy.sh's `level-guard`
gone), E6 (no `sup_has_export_doc`: the copy ran), Q6 (no refusal: `restore_live` called), Q2 (second
abandon accepted), T6 (`up` no longer lists kept tars), T3 Python (`write_export`'s check off). Every other
Python fix is covered by its mutation row above.

**Spot checks** (16 claims of "How each item is met" and the Review table): every symbol and behaviour is
where it says; the table's line numbers predate the review fixes and have drifted (e.g. `fork_floor_problem`
`events.py:1016` is now `:1147`, `lower_of` `:1041` → `:1177`, `_check_disaster` `:1078` → `:1222`, `close`
`supersede.py:1347` → `:1368`, `supersede_stub` `staging.sh:2350` → `:2399`, `rel_level_guard` `:1816` → `:1833`);
exact: `_refuse_pending` `supersede.py:130`, `LEVEL` `:79`, `level_of` `:109`, `bucket_level` `:114`,
`archive.py:269` `latest_at`. Checked by reading: readers see earlier generations already `check_restore`d
(`read()` sorts by generation, so `verified_before` sees real `problems`); the load re-archives the
generations stored after T from the package, so a legitimate restore between the claim and the stub verifies
for the readers too; `superseded_plan`, `later_attempts`, `recovery_problem`, `sup_abandon_failed`/`SUP_RESTORED`
and the disaster `close` refusal match the Review rows.

**Nits (no change):**
1. "How each item is met" line numbers are stale (above).
2. With `write_export`'s recovery check reverted, `ExportDoc.test_the_export_doc_binds_t_to_the_restores_own_stop`
   ERRORs (`TypeError` on its first case, `None`) instead of FAILing; no mutation row guards the writer side
   (the readers' side has `supersede-recovery-bound`).
3. With deploy.sh's `level-guard` line reverted, `test_the_level_guard_runs_in_release_and_before_a_plain_start`
   ERRORs (`str.index`) rather than FAILs.
4. The "Tests (real counts)" section still gives the builder's 413 and 94; the current counts are above.

Residual risks stay as the note states: T1 (a database owner who also holds the events key), T3's stop and
replayed times come from the operator's state file (internally checked only; c2b S6), Q3 (no live run of L
from a real fingerprint).
