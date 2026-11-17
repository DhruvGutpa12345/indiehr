# P1-T22c1 build note: restore events for a planned `rollback --restore` (17-11-2026)

Builder: Claude (Opus 5.5, high), unattended run. Design: `docs/reviews/p1-t22c/design.md` ("After
the attack" wins over §1-§6). Scope c1 (design §8): flows (a) planned rollback, (d) crash/resume,
(e) a later second whole-database restore; the generation fix; plans, results, the result chain and
timeline check; the separate events key; log lines, alerts, mutation rows; a live proof. Nothing is
committed (the orchestrator commits).

## Files

New
- `plugins/ihr_platform/ihr_audit/supersede.py`: export (packages + plan draft, read-only), plan
  write, load (steps 1-8), status.
- `plugins/ihr_platform/ihr_audit/management/commands/ihr_audit_supersede.py`: `export | plan | load | status`.
- `plugins/ihr_platform/ihr_audit/tests/test_supersede.py`: 24 tests.
- this note.

Changed
- `ihr_audit/events.py`: plan/result objects, `cause`/`restore_id` on restore events, the
  whole-database checks, the result chain, timeline, events-key writer store.
- `ihr_audit/anchor.py`: writer scope per key (`scope`, `ScopeError`), `claims_at`.
- `ihr_audit/archive.py`: `next_generation(cursor, uuid, in_bucket)` (the defect fix), `generations_in_db`.
- `ihr_audit/watch.py`: timeline history, `ihr_audit_timeline_unexplained`, `ihr_audit_whole_restores`,
  `..._unanchored`, `ihr_audit_restore_plans_abandoned`, the new line.
- `ihr_audit/management/commands/ihr_audit_verify.py`: excused claims per restore id, events that
  excuse nothing, unfinished loads as problems.
- `ihr_platform/restore/engine.py`: generation from the bucket too; refuses while a whole-database
  load is unfinished.
- `scripts/staging.sh`: `rollback --restore` (stage 5: hold, drain, seal, export, plan; load before
  `up`), `--accept-absent`, `audit-supersede load|status|pending`, `s3-events-key`, `up` warning,
  `s3-init` makes the events key, `s3-check --anchor-suffix`, restore containers removed.
- `scripts/release_log.py`: rollback field `supersede=<id|none>`.
- `scripts/deploy.sh`: never sends `secrets/anchor-events.env`.
- `scripts/care-dev.sh`: dev events key (`deploy/dev/audit-anchor-events.env`, git-ignored, no
  container holds it), `audit-anchors` restarts the gateway, `s3-check --anchor-suffix localhost`.
- `deploy/s3/s3.template.json`, `deploy/s3/start.sh`, `deploy/s3/check_keys.py`,
  `deploy/staging/compose.s3.yaml`, `deploy/dev/care-local.override.yaml`: the events identity and
  the writer's scope.
- `deploy/staging/compose.yaml`: one-off `audit-supersede` (watch role, reader key only).
- Alerts: `deploy/logs/alerts/audit.yml` (AuditWholeRestoreRecorded), cases in
  `deploy/logs/tests/alert-cases.json`, `scripts/check-logs.py`;
  `deploy/monitoring/prometheus/rules/audit.yml` (AuditDatabaseRestoredUnannounced) and its promtool test.
- `deploy/monitoring/host-metrics.sh` + `MON_ONESHOTS`: the new one-off; `deploy/grafana/dashboards/ihr-server.json`: 23 log rules.
- `deploy/staging/README.md` ("Whole-database restores (P1-T22c1)", alert tables, runbook);
  `CLAUDE.md` (one sentence); `.gitignore`.
- Tests: `scripts/test_release_scripts.py`, `test_staging_s3.py`, `test_staging_logs.py`,
  `test_staging_monitoring.py`; `ihr_audit/tests/test_watch.py` (a loop cap raised: the test
  schema's chain grows with every test that seals; no assertion changed).

## How each item is met

| Item | Where |
|---|---|
| Flow (a) export: L = newest fingerprint stored at or before T, read from the bucket (S3) | `anchor.py:599` `claims_at`; `supersede.py:300` `export_one` (refuses a live chain whose seal L is not the claim) |
| Package: seals L..head, entries, unsealed (kept, not loaded), archive generations after T | `supersede.py` `Package`, `export_one`; carried = generations whose event was stored after T (`_events_before`) |
| Plan written before the destructive step, write-once, packages' sha256 and L checked again | `supersede.py:449` `write_plan`; `events.py:1001` `write_restore_doc`, `:1017` `plan_document`; `staging.sh:2096` |
| Load steps 1-8 (sha, generations re-loaded or compared row by row, F = L, unchanged, g = bucket + 1 agreeing with the database, archive + probe, event, restore entry, result) | `supersede.py:590` `load_one`, `:511` `_carry_generation`, `:612` F = L, `:728` `load`; final check of every event of the restore `:816` |
| Old builds see an ordinary event (extra fields only) | `events.py:326`; test `Rules.test_compatibility_old_readers_see_an_ordinary_restore_event`; live: build A (pre-c1) verifies |
| Whole-database checks: plan before, result names it, head/digest = plan, F = L re-derived from fingerprints (S3) | `events.py:779` `check_whole`, `:764` `_lower_claim` |
| S2: result after plan, new timeline later than old; watch history between stored times | `events.py` `_read_restore_docs` (bad result dropped as a store problem), `:740` `corroborated` |
| F4: only the newest result must be the database's timeline; older ones through the chain | `events.py:701` `anchor_results` (`:717`, `:727`) |
| Q-K89 watch: timeline each pass, unexplained gauge, alert | `watch.py:608` `_timeline`, `:636` `_whole_restores`; rule AuditDatabaseRestoredUnannounced |
| New lines/alerts in the `ihr-audit:` format | `watch.py:185` `restore_line`; AuditWholeRestoreRecorded; `ihr-supersede:` lines `supersede.py` |
| Generation defect (bucket ahead of database) | `archive.py:45` / `:63`; `engine.py:741` `_generation` |
| Engine refuses while a load is unfinished | `engine.py:722` (precheck); `events.py:395` `pending_loads` |
| Q-K91 events key: Celery's writer only `anchors/<suffix>/`, events key only events/restores; transition until a site has one | `s3.template.json`, `start.sh:42-46`, `anchor.py:96`/`:139` (client-side scope), `events.py:139` `writer_store`; `staging.sh:2054` `supersede_keys` (subshell only), `:2174` `s3-events-key`; `deploy.sh` exclude |
| F1 own rollback stage, holds accepted on a re-run | `staging.sh:2389` stage 5, `:2207` message; `releases/rollback-hold` marker (see deviation 1) |
| F2 best-effort hold/drain/seal; export and load from the newest image with the tool, recorded | `staging.sh:2389-2403`; `state` file (`image=`, `load_image=`), `audit-supersede load --image` |
| F6 hospitals made after T need `--accept-absent` | `staging.sh` rollback, `sup_absent` |
| F9 `up` warns, never waits | `staging.sh:824` |
| Flow (d): every step checked before done; stage 3/4 messages name the load | `supersede.py` (record by operation, event by generation+digest, entry by restore_id, plan by content); `rel_rollback_exit` |
| Flow (e): second restore carries the first one's generation, chain anchors both | test `Generations.test_a_second_whole_restore_carries_the_first_ones_generation`; live second cycle |
| Q-K86 a target without the archive is refused | `supersede.py` `load` (`to_regclass`) |
| `releases.log` `supersede=` | `staging.sh:2436`; `release_log.py` |

## Tests and results (real counts)

- `IHR_DB_ROLES=test scripts/care-tenancy.sh test --settings=ihr_platform.settings.test --noinput ihr_audit ihr_platform.tests.test_restore ihr_platform.tests.test_release ihr_platform.tests.test_db_roles`: **373 tests OK** (24 new in `test_supersede`).
- `python3 -m unittest`: `test_release_scripts.py` 82 OK; `test_dumps.py` 91 OK (7 skipped, live only);
  `test_restore_quarantine.py` 20 OK (6 skipped, live only); `test_staging_logs.py` 46 OK;
  `test_staging_s3.py` 50 OK; `test_staging_monitoring.py` 56 OK.
- `scripts/check-monitoring.sh` (promtool rules + tests): OK.
- `pre-commit run --all-files`: all hooks pass.
- Mutation rows (`scripts/isolation-mutation.sh check` OK; each `apply`, its test run, `expect`
  matched, tree restored), every one **FAIL** as required:
  `supersede-events-scope`, `supersede-timeline`, `supersede-chain`, `supersede-generation`,
  `supersede-fork-is-lower` (F = L, S3), `supersede-plan-sha`.

## Live proof (local staging proof, dev stack stopped, synthetic data only)

Images: A = a build of commit 5706194f1 (P1-T22b code, no supersede tool: the rollback target), B =
this working tree (dirty), B2 = B with the two fixes below. Stack: `init --local`, `s3-init` (now
with the events key), `mail-init --test`, `backup-init --local`, `up` (162 s); hospitals alpha and
beta; release A logged (`release-note`, smoke, `release-record`).

1. `s3-check`: writer refused for an event, a plan and outside its suffix; events key refused for a
   fingerprint and outside its two prefixes; all checks passed (same in dev: `care-dev.sh s3-check`).
2. `release --now` to B (121 s; restore point `release-…-1794902312`, the release's fingerprint
   stored in the same second); two rounds of profile edits per hospital, sealed and fingerprinted:
   alpha seals 3-5, beta 2-4 sealed past the point. A watch pass (its own role, a state volume)
   recorded timeline 00000001.
3. `rollback --restore --operator proof` (68 s to stage 4): hold, drain, seal, export (2 exported,
   9 KB tar, mode 600 in a 700 folder), plan written, down, copy, restore, load. **Found:** the load's
   final check refused its own events, "its result was stored before it": S3 stores whole seconds
   and the event and result landed in one second. Fixed (same second counts as after;
   test `test_storage_times_in_whole_seconds_accept_the_same_second`). Resumed with
   `audit-supersede load <id> --image <B2>`: loaded (alpha gen 1 fork 2, beta gen 1 fork 1).
   `ihr_audit_verify --all` (B2, owner, reader key): **OK, 6 claims excused**; watch pass: two
   `event=restore` lines, `ihr-audit: restore=859f21a1 event=whole-database hospitals=2 outcome=recorded`,
   chain_broken 0, fingerprint_mismatch 0, timeline_unexplained 0. After `up` on A:
   `ihr_audit_verify --all` **with build A** (owner): OK, 6 claims excused (older builds accept on
   the segment rule).
4. Second cycle (flow e + the negative): `backup-now full`, release B2 (121 s), two more rounds sealed
   past the new point, watch pass on timeline 00000002, `rollback --restore` again with
   `secrets/anchor-events.env` moved aside after the plan. The rollback stopped at stage 3 on a
   **pre-existing defect** (the first restore's `ihr-restore` containers were left behind: rollback
   replaces restore_live's EXIT trap); fixed (`restore_live` removes them, test added) and the restore
   finished by hand as the stage-3 message says. The load without the events key: **AccessDenied from
   the gateway** (the writer key cannot add events). Negative state on the restored database, no
   events of this restore: `ihr_audit_verify` **exit 1** (seals 6-8 cut, 3-5 rewritten, the first
   restore's events "its new timeline 00000002 is not the database's (00000003)", the unfinished load
   named); watch exit 1: `check=chain` and `check=fingerprint` lines for alpha and beta,
   chain_broken 2, fingerprint_mismatch 2, **timeline_unexplained 1**. Key back, `audit-supersede
   load`: loaded (gen 2 each). Then verify **OK, 12 claims excused (6 per restore id)**; watch clean,
   restore_events 4, whole_restores 2, unanchored 0, unexplained 0, the new line once. After `up` on
   A: verify with build A OK, 12 excused; smoke 37 ok.
5. Teardown: `down -v`, proof volumes and copies removed, `.env .local .db-created secrets supersede
   releases` removed, `generated/` put back, proof image tags removed, `indiehr-backend:staging`
   back on its previous image; dev stack `care-dev.sh up`.

Measured: export 2 s and plan 2 s for two small hospitals (two tars of 9 KB); load 4-6 s including
the one-off's start; whole rollback 68 s to the load; the release 121 s.

## Deviations from the design (and why)

1. **Hold reason.** The rollback holds with `ihr_fleet_run hold` (reason `window`, which every
   build knows) and marks its own holds in `releases/rollback-hold`, which a second
   `rollback --restore` accepts (F1). A new `rollback` reason would need the leaving build's fleet
   runner and is unknown to older targets.
2. **Export role.** The export runs as the audit watch's read-only role (`ihr_platform.settings.watch`,
   one-off `audit-supersede`), not `ihr_dump`: Django processes may only declare app/owner/watch, and
   the watch role reads exactly the audit tables and the archive. The plan is written by the
   owner's migrate one-off (the watch settings refuse any writer secret). No new
   `ihr_db_role_check` process was needed.
3. **Packages** live only in `deploy/staging/supersede/<id>/export.tar` (mode 600), unencrypted, like
   the copy of the whole database the rollback keeps beside it; the sealed copy in the dumps bucket
   (Q-K84/Q-K90, the `ihr-superseded` sealer purpose) is c2.
4. **The load's image** may be named (`audit-supersede load <id> --image`) after a fix of the tool;
   recorded in `state` (`load_image=`). The plan records the export's image.
5. The rollback re-points the images to the target **before** the load (the first live run showed a
   load failure at stage 4 would otherwise leave `up` starting the leaving build).

## Known limits

- Until a site has the events key (and on provider buckets: `infra/aws/anchor` has no events user
  yet), events, plans and results go with Celery's writer key: the plan/result/timeline checks are
  defence in depth, not a security boundary; the archived-segment rule still is.
- Flow (a) only: F must equal L; a hospital with a single-hospital restore from a dump older than T
  after T is refused at export (named), before anything changes. The L..T window is not covered
  by the bucket (design §8).
- A hospital made after T stays reported as unknown (`--accept-absent`); closing it is c2.
- Stage 3 (restore finished by hand) does not write the `releases.log` rollback line (pre-existing:
  the message says to finish by hand).
- The export holds each package in memory (the watch one-off's tmpfs is 16 MB); fine for tails of a
  window, to be streamed for large tails.
- The watch's corroboration uses its own pass times; a watch that never saw the old timeline
  (new volume) cannot judge: counted as uncorroborated (AuditWholeRestoreUncorroborated, warning; review T5).

## Deferred to c2

Disaster flow (b) and its stub-plan rule (open point), flow (c) partial, absent hospitals closed
(S5), `accept-loss`/AuditLostTail (S4), bucket packages and their expiry (Q-K84/Q-K90, S6, the
sealer purpose), `--local-packages-only` (F8), `announce-timeline` (F5) and its runbook steps,
`restore --target --supersede`, the ADR-010 amendment, `infra/aws` events user.

## Review (17-11-2026; three Opus lenses T, E and Q, each with a challenger; Opus fixer)

Every confirmed or partly confirmed finding is fixed below; none was refuted. Each fix has a
regression test that was run with the fix reverted and **failed** (the reverts applied together in one
run; the T2 tests in a run of their own), then passed with the fix. The mutation
rows were each run on their own (`apply`, `test`, `expect`, tree restored). Severity is
"reviewer → challenger". Tests are in `ihr_audit/tests/test_supersede.py` unless named
otherwise. An earlier fixer run was cut off by a machine restart before it changed anything
(the line numbers the reviews quote were still exact); the work below is all from this run.

| Id | Finding | Severity | Fix | Test (fails with the fix reverted) |
|---|---|---|---|---|
| T1 | `audit-supersede` inherited the journald log driver: the export's tar of audit entries (stdout) would reach the journal, the log store and the 400-day archive | high → high | `logging: {driver: none}` on the one-off (compose.yaml), with a comment; its `ihr-supersede:` lines still reach the operator's terminal; the load (under `migrate`) still logs | `test_staging_logs.py` `test_fleet_runs_reach_the_alert` (asserts `driver: none` on `audit-supersede`); `test_staging_keys.py` lists it among the unlogged one-offs |
| T2 / E2 | F = L read as "restored head == L" before the resume path: a load run again after `up` (beat sealed the restore entry, new traffic) refused every hospital for good, so no result, and every single-hospital restore refused | medium/high → medium(-high) | `supersede.restored_fork_problem`: the restored seal at L must be the plan's L and the restored seal L+1, if any, must not be the package's own L+1 (that one was sealed before T: a stale L). Seals the restored database added since are a new branch and accepted | `ReviewLoad.test_a_load_run_again_after_up_accepts_seals_added_since`, `test_a_first_load_after_up_accepts_a_new_branch_after_lower`; the stricter half: `test_the_packages_own_next_seal_in_the_restored_chain_is_refused` (row `supersede-fork-next-seal`); row `supersede-fork-is-lower` moved to the new check |
| T3 | `deploy/staging/supersede/` not git-ignored in a public repo | medium → medium | `.gitignore` entry | `test_staging_logs.py` `test_exported_audit_tails_are_never_committed` (`git check-ignore`) |
| T4 / Q9 | The events key's secret is in `secrets/s3-gateway.env`, which `deploy.sh` sends (IHR_S3=local); the comments said "never on its disk" | low → partly (document) | Documented as a limit (the gateway must hold the secret to check it; Celery never sees it; a provider bucket keeps it in its IAM): README "The events key", `deploy.sh` and `supersede_keys` comments reworded | doc only (no behaviour changed) |
| T5 | S2 passed silently when the watch's history could not judge; timeline `00000000` accepted | low → partly | `TIMELINE_RE` refuses `00000000` (Postgres starts at 1); `events.corroborated` returns `CANNOT_SAY`, counted in `Events.uncorroborated` and the new gauge `ihr_audit_whole_restores_uncorroborated` (alert AuditWholeRestoreUncorroborated, warning); the event still excuses on the other rules | `ReviewRules.test_timeline_zero_is_no_timeline`, `test_a_history_that_never_saw_the_change_cannot_say_and_is_counted`; promtool case |
| T6 | `Events.generation()` was a count: a dropped object (garbage second version, unreadable body) let a new restore reuse a generation | low → partly | `generation()` is the highest generation; event objects that cannot be counted (unreadable, written twice, hidden by a marker) are attributed to their hospital by the key's name (`Events.unsure`); the load and the engine (`_generation`) refuse that hospital's next generation (`generation_unsure`) | `ReviewGenerations.test_the_generation_is_the_highest_not_a_count`, `test_an_event_object_that_cannot_be_counted_refuses_the_next_generation` |
| T7 | The timeline gauge looked only at the latest change | low → partly | `_whole_restores` judges every adjacent pair the watch keeps (`TIMELINES_KEPT`); the gauge is the number of unexplained changes and the rule is `> 0` | `ReviewWatch.test_an_unannounced_change_stays_unexplained_after_an_explained_one` (row `supersede-timeline-history`); promtool case "two unexplained changes" |
| T8 | Mutation gaps: the load's sha256 check, `check_whole`'s re-read L and plan-before-event | low → confirmed | Rows `supersede-load-sha`, `supersede-whole-claim`, `supersede-plan-before-event` | `ReviewLoad.test_a_changed_but_well_formed_package_is_refused_by_its_sha256`, `ReviewRules.test_the_lower_bound_is_read_again_from_the_fingerprints`, `test_an_event_stored_before_its_plan_excuses_nothing` |
| E1 / Q2 | The write-once plan was written before the `--accept-absent` refusal; an abandoned plan then counted as a pending load for good after the timeline moved (every single-hospital restore refused, `ihr_audit_verify` exit 1) | high → high | `staging.sh`: `supersede_export` exports only; the absent check runs on the draft; then `supersede_plan` writes the plan (marks `planned`). `Events.superseded_plan`: a plan without a result is not pending (nor "abandoned" in the watch) when another plan from the same old timeline has a result (only one restore can move the database off a timeline) | `ReviewRules.test_a_plan_superseded_by_a_later_run_from_the_same_timeline_is_not_pending`; `test_release_scripts.py` order list (Q10) and `supersede_export` without the plan |
| E3 | A stale L (best-effort release seal) was caught only by the load, after the database was replaced | medium → medium | Export refuses (by name, nothing changed) an exported hospital whose first seal after L was sealed at or before T | `ReviewExport.test_a_stale_fingerprint_at_the_restore_point_is_refused_at_the_export` |
| E4 | "Absent" meant different things at export and load (provisioning hospitals) | medium → medium | `_restored_hospitals` uses the export's own filter (`_hospitals`: no provisioning, no schema without a trail) | `ReviewLoad.test_the_restored_hospitals_are_counted_as_the_export_counts_them` |
| E5 | A bucket already ahead of the database before T was caught only at the load | low → low | Export refuses the hospital by name when the database records fewer generations than the bucket's events before T | `ReviewExport.test_a_database_behind_the_bucket_before_the_point_is_refused_at_the_export` |
| E6 | Two loads at once could write two events of one generation | low → low | `load` takes `pg_try_advisory_lock(LOAD_LOCK)` before it reads the bucket; a second load is refused ("another load ... is running"); unlocked in `finally` | `ReviewLoad.test_only_one_load_runs_at_a_time` (a second connection holds the lock) |
| E2 (s3 sub-point) | After a rollback to a pre-c1 target on a local gateway, a load resumed after `up` meets a gateway without the events identity | (part of E2) → plausible | Documented in README "A rollback that stopped": load before `up` (as the rollback does), or unset the events key so the writer's key adds the events | doc only |
| Q1 | `hospital-restore` passed no events key: once `s3-init` made one, the gateway refuses the restore's event (AccessDenied mid-restore) | high → high | `restore_keys` also reads `secrets/anchor-events.env` and passes `IHR_AUDIT_ANCHOR_EVENTS_KEY/_SECRET` from the owner's shell; warns when there is none | `test_release_scripts.py` `test_hospital_restore_passes_the_events_key` |
| Q3 | The live proof never ran the automated rollback through uninterrupted on the final tree | medium → medium | One clean cycle on the final tree (below, "Live proof after the review"), output in `docs/reviews/p1-t22c/proof-c1-review.log` | (live) |
| Q4 | `releases/rollback-hold` leaked (stage 3/4 stops, `up` + `fleet release`), silently skipping D2 later | medium → medium | Removed when the restore starts (stage 3: the holds go with the old database) and by every `fleet release` | `test_release_scripts.py` `test_a_fleet_release_ends_a_stopped_rollbacks_hold_marker`; order list |
| Q5 | The engine half of the generation fix was untested | medium → medium | Test + row `supersede-engine-generation` | `ReviewGenerations.test_the_engine_takes_the_next_generation_from_the_bucket` |
| Q6 | The load's sha256 check was not proven (a flipped gzip byte fails on the CRC alone) | medium → partly, low | A well-formed, re-gzipped package with a changed entry; row `supersede-load-sha` (= T8) | `ReviewLoad.test_a_changed_but_well_formed_package_is_refused_by_its_sha256` |
| Q7 | `ihr_audit_restore_plans_abandoned` and `..._unanchored` had no alert or runbook line | low → low | Warnings AuditRestorePlanAbandoned, AuditWholeRestoreUnanchored (and T5's AuditWholeRestoreUncorroborated), promtool cases, README alert table and runbook | `deploy/monitoring/prometheus/tests/audit.yml`; `test_staging_monitoring.py` `test_alerts` |
| Q8 | Stage 5's message always said "every hospital is held"; README said "nothing changed" while a plan was already written | low → low | `REL_RB_HELD` (held, failed, none) chooses the message; it says the plan stays when one was written; README reworded | `test_release_scripts.py` `test_the_trap_names_the_audit_load` |
| Q10 | The order test could not catch the plan before the absent refusal | low → partly | The order list now has the absent check, `((accept_absent == 0))`, `supersede_plan`, `compose down`, the marker's removal and `restore_live` | `test_audit_tails_exported_before_and_loaded_after_the_restore` |

**Refuted:** none. (T4, the E2 s3 sub-point and Q9 are documentation: the gateway has to hold
the secret, and a pre-c1 gateway cannot be changed by this build.)

**Mutation rows** (each run alone: `apply`, its test, `expect` matched, file restored; all
**FAIL** as required): the six of the build (`supersede-events-scope`, `-timeline`, `-chain`,
`-generation`, `-fork-is-lower` moved to the new F = L check, `-plan-sha`) and six new ones:
`supersede-fork-next-seal`, `supersede-load-sha`, `supersede-whole-claim`,
`supersede-plan-before-event`, `supersede-engine-generation`, `supersede-timeline-history`.
`isolation-mutation.sh check`: OK.

**Suites after the fixes:** `IHR_DB_ROLES=test scripts/care-tenancy.sh test --settings=ihr_platform.settings.test --noinput ihr_audit ihr_platform.tests.test_restore ihr_platform.tests.test_release ihr_platform.tests.test_db_roles`: **390 tests OK** (17 new in `test_supersede`, now 41).
`python3 -m unittest`: `test_release_scripts.py` 85 OK, `test_dumps.py` 91 OK (7 skipped, live
only), `test_restore_quarantine.py` 20 OK (6 skipped), `test_staging_logs.py` 47 OK,
`test_staging_s3.py` 50 OK, `test_staging_monitoring.py` 56 OK, `test_staging_dashboards.py` 28 OK,
`test_staging_keys.py` OK (pre-commit hook); `scripts/check-monitoring.sh` (promtool rules and
tests) OK; `pre-commit run --all-files`: all pass.

**Live proof after the review (Q3; local staging proof, dev stack stopped, synthetic hospitals
alpha and beta; output: `docs/reviews/p1-t22c/proof-c1-review.log`).** Images: A = the final
tree plus one label (the target, release N), B = the final tree (release N+1). `init --local`,
`s3-init` (with the events key), `mail-init --test`, `backup-init --local`; release N
(`release-note`, `up`, two hospitals, `smoke`, `release-record`; 354 s with the first `up`),
`backup-now full`, `release --now` to B (127 s), two rounds of profile edits per hospital, each
sealed and fingerprinted (alpha seals 3-5, beta 2-4 after the point). Then **one uninterrupted
`rollback --restore`** (184 s; 204 s with the full backup): hold, drain, seal, export (2
exported), plan, `down`, copy kept, restore, load before `up` (alpha generation 1 fork 2, beta
generation 1 fork 1), `up`, `fleet release`, `fleet roles`, `fleet window --clear`, smoke (every
check ok), the `rollback-hold` marker gone, `releases.log` `supersede=<id>`, full backup on
timeline 2. Afterwards: `audit-supersede status` both loaded; `ihr_audit_verify --all` as the
owner with the reader key: **audit trail OK, 6 claims accounted for by this restore id** (alpha's
seal 3 is already a new one, sealed after `up`). `audit-supersede load` run again after `up`,
`fleet release` and that new seal: **loaded** (the T2/E2 case, refused before the fix).
`audit-supersede`'s rendered logging: `driver: none`.

Notes: (1) the same check from the running backend (the app role) reports the superseded
segments unreadable: by design the app role has no archive rights, so the check runs as the
owner (as in the build's proof). (2) The typed confirmation fed from a pipe was cut by a docker
step before it that reads stdin in chunks (two tries refused "not confirmed", nothing changed);
the proof fed it in 64-byte lines; a person typing it is not affected. (3) Release N+1 logged
`audit=problems` from its pre-hold check (best effort, as before c1; the hospitals were made
after release N's start); not investigated here. Teardown: the stack and its volumes, the proof
folder's files (`.env`, `.local`, `.db-created`, `secrets`, `supersede`, `releases`, the new
`generated/` folders) and the proof image tags are gone; `indiehr-backend:staging` is back on
its previous image; dev stack `care-dev.sh up`.

**Not covered in this cycle:** the audit watch was not run (its timeline rules are covered by
the tests and the build's proof); a server's journal (T1 rests on the config test and the
rendered config: a proof logs to Docker's files).

## Verification (17-11-2026; Opus, high; wrote none of this)

Before anything, sha256 of all 40 changed and new files was recorded. After every check the
same 40 hashes came back (this section aside). **LATE FIX: none.** Fix reverts and mutation
rows ran on scratch copies of `plugins/`, against a throwaway database `care_tenancy_v`
(dropped afterwards). The shell reverts were made in place from backups, restored, and checked
by hash.

| Check | Result |
|---|---|
| `IHR_DB_ROLES=test scripts/care-tenancy.sh test … ihr_audit ihr_platform.tests.test_restore ihr_platform.tests.test_release ihr_platform.tests.test_db_roles` | **PASS**: 390 tests OK (127.8 s); Redis audit: 66 keys written, all tenant-prefixed |
| Script suites | **PASS**: `test_release_scripts` 85, `test_dumps` 91 (7 skipped), `test_restore_quarantine` 20 (6 skipped), `test_staging_logs` 47, `test_staging_s3` 50, `test_staging_monitoring` 56, `test_staging_dashboards` 28, `test_staging_keys` 20 |
| `scripts/check-monitoring.sh` | **PASS** |
| `isolation-mutation.sh check` | **PASS** (194 rows, 12 `supersede-*`) |
| 12 `supersede-*` rows, each alone (`apply` on a copy, `test`, `env`, `expect`) | **PASS**: each ran 1 test with 1 FAIL and 0 ERROR, and `expect` matched. Note: with `supersede-load-sha` applied, the changed package is still refused, later, by the probe ("archived tail does not verify"). The sha256 check is the first of two defences, not the only one. |
| `pre-commit run --all-files` (new files already in the index as `A`) | **PASS**: 45 hooks passed, 0 failed, no file changed |
| Fixes reverted, Python group 1 (E1 `superseded_plan`, T5 timeline 0 and `CANNOT_SAY`, T6 count and `unsure`, E3, E5, E4, E6 lock) | **PASS**: in `test_supersede`, exactly the 9 matching tests failed (8 FAIL, 1 ERROR), the other 32 passed. Each failed for its own reason: "Refused not raised" (E3, E5); `2 != 3` (T6); `'' is not true` (T6 unsure); the provisioning hospital still counted (E4); the abandoned plan still pending (E1); "ValueError not raised" (T5); uncorroborated set empty (T5). E6 shows as an ERROR: with no lock, the load runs on into the mocked read (`MagicMock is not JSON serializable`) instead of refusing. |
| Fixes reverted, Python group 2 (T2/E2: F = L read as head == L) | **PASS**: only the two "after up" tests fail, refused with "the restored chain ends at seal N+1 … F = L" |
| Fixes reverted, shell (T1, T3, Q1, Q4, Q8, E1/Q10 order, Q7 alerts) | **PASS**: each named test fails on its own assertion (`driver` is `${IHR_LOG_DRIVER:-journald}`; export.tar not ignored; no events file in `restore_keys`; no marker removal; no `$held`; order list out of order; `AuditRestorePlanAbandoned` missing), and promtool fails on the three warnings. Three further failures came from the placeholder rule the revert used, not from the fixes. `test_staging_keys` only lists the one-off; T1's guard is `test_fleet_runs_reach_the_alert`. |

**Claims spot-checked against the code (all hold).** Line numbers from before the review have
moved; each definition was found:
- `anchor.py:599` `claims_at`; the scope check at `anchor.py:140`.
- `archive.py:45` `next_generation` (`max(in_db, in_bucket) + 1`, refused when the database is ahead).
- `engine.py:722` `_no_pending_whole_restore` and `:741` `_generation` (bucket generation, `generation_unsure`).
- `supersede.py:305` `export_one` (E3 stale check), `:484` `write_plan` (sha256 and L read again, write-once).
- `supersede.py:620` `restored_fork_problem`, `:645` `load_one` (sha256 first), `:784` `load` (advisory lock before the bucket read), `:778` `_restored_hospitals` = `_hospitals`.
- `events.py:91` `TIMELINE_RE` (no `00000000`), `:141` `writer_store`, `:379`/`:387` `generation`/`generation_unsure`, `:416`/`:429` `pending_loads`/`superseded_plan`.
- `events.py:735` `anchor_results`, `:779` `corroborated` (`CANNOT_SAY`), `:819` `check_whole`.
- `watch.py:187` `restore_line`, `:610` `_timeline`, `:638` `_whole_restores` (every pair, `> 0`).
- `staging.sh:2054` `supersede_keys`, `:2097`/`:2121` `supersede_export`/`supersede_plan` (the absent check between them), `:2221` `REL_RB_HELD`.
- `staging.sh:2458`/`:4821` the marker removals, `:4504` `restore_keys` (events file), `:2464` `supersede=`.
- `compose.yaml:293` `driver: none`; `.gitignore` `deploy/staging/supersede/`.
- `deploy.sh` excludes `secrets/anchor-events.env`; `s3.template.json` events policy covers `anchors/ihr-events/*` and `anchors/ihr-restores/*`, and the writer policy `@ANCHOR_WRITE_PREFIX@*` (`start.sh`: `anchors/<suffix>/`).
- `proof-c1-review.log`: 184 s, both loaded, "audit trail OK", 6 claims excused, the load run again after `up` loaded. No secrets in the log.

**Observation (not fixed; low, fails closed).** `supersede.export` and `rollback --restore` do
not refuse while an earlier whole-database load is still pending (`Events.pending_loads()`; only
`up` warns). Suppose an operator ignores the stage-4 stop and the `up` warning, then rolls back
again. The second restore loads, but loading the first one afterwards leaves the second
result unanchored, because the chain's next plan does not continue from its timeline. Not
loading the first one leaves it pending for good. Either way the outcome is alerts and a
refusal, never a silent pass. Suggested for c2: refuse the export while `pending_loads()` is
non-empty.

Not re-run here: the live staging proof (the fixer's `proof-c1-review.log` was read, not repeated).
