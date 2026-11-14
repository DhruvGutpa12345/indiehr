# P1-T21 independent code review (2026-11-14)

Two fresh Opus reviewers who did not write the code (correctness and tests; security, tenancy and
operations), then a third agent tried to refute each finding. Of 21 findings: 1 refuted, 8 confirmed
in part, 12 confirmed. A fixer agent fixed them, and a fourth agent checked each fix against its
failure scenario. The verifier closed 17, partly closed 3 (S4, C1, C3) and found 6 new problems; a
second fixer dealt with all of them (second round below). Design: design.md ("As built", "Review
fixes", "Second review fixes").

| # | Finding | Verdict | Fix (verified) |
|---|---|---|---|
| S1 | A pass that fails on one hospital leaves every integrity gauge at its last clean value, so critical alerts drop to a 30-minute warning | confirmed, high | trigger gauge published before any per-hospital query; every hospital checked in its own transaction and try/except; a hospital that raises is `unchecked` (gauge, a `check=unchecked` line from its second failing pass, critical alert AuditHospitalsUnchecked) |
| S2 | Owner-writable hospital rows make the watch skip a hospital with no alert (status provisioning, or hold reason `restore`) | confirmed, medium | a "provisioning" row the watch judged before is reported as a broken chain; a restore hold keeps standing findings and is counted (AuditHospitalsRestoring, warning 6 h, critical 24 h). Deviation: no fingerprint check during a restore hold |
| S3 | The nightly rolling-recompute horizon is chosen by the unhashed, owner-writable `sealed_at` | confirmed, medium | the watch keeps its own 30-day record of each head; the horizon starts at the earlier of the two, so a back-dated `sealed_at` can only widen it. Not built: comparing `sealed_at` with the fingerprints |
| S4 | Offboarding and restore events can be written with the Celery-held writer key, are accepted with almost no checks and raise no alert | partly, medium | an offboarding closes a hospital only if the database, the watch's own record and the newest fingerprint claim agree; other events are an `events` store problem; each new event is named once (AuditEventRecorded, warning); restore generation gaps count. Verifier: partly closed (owner decision left, see known limits) |
| S5 | A missing checkpoint is seeded from the database head when the bucket is unreadable or has no recent fingerprint (A8) | partly, low | no seeding while a configured store is unreadable (AuditCheckpointUnseeded); the last sweep's claim is the fallback; a genesis seed of a chain with seals counts as lost |
| S6 | A database-supplied slug goes unvalidated into the error log, and an invalid slug silently drops the per-hospital alert line | partly, low | a slug off its pattern is named `invalid-slug` instead of dropped; free-text logs print slugs with `%r` |
| S7 | The metrics server listens on every network and spawns one thread per connection without limit | confirmed, low | only localhost and the resolved scrapers are served (checked before a thread starts); 4 requests at once; 10 s per request; `pids_limit: 64` |
| C1 | `rollback --restore` (or any whole-database restore) produces permanent critical alerts that nothing can clear | partly, medium | docs, warning and scope: the rollback text names the alerts and says to record an incident, never a silence; P1-T22b now also writes restore events for whole-database restores. No reset path built. Verifier: partly closed (a known limit until P1-T22b) |
| C2 | One failing hospital aborts the whole pass; gauges keep stale values and only a warning fires | confirmed, medium | as S1 |
| C3 | Held suspended and offboarding hospitals are not skipped for seal lag, so releases raise false seal-late alerts | partly, medium | the release seals with the new build before `up`; a start dates a wait no earlier than one sealing cycle; docstring corrected. Verifier: partly closed (the lock-during-migration case, fixed in round two) |
| C4 | With monitoring on and no fingerprint storage, the watch refuses to start and `up` (release step 8) fails | confirmed, medium | `watch_need_reader` refuses in `up` and `release` before anything changes; `monitoring-init` warns |
| C5 | The nightly time limit cannot be configured, and the AuditRehashOverdue remedy does nothing when the time limit binds | confirmed, medium | all three knobs pass through compose; gauges for which limit ended the night and the rate; the alert is two rules (`limit=budget`, `limit=time`) that each name their own knob |
| C6 | A missing checkpoint is seeded from genesis and the database head adopted, with no AuditCheckpointLost, when no fingerprint is at hand | confirmed, medium | as S5; plus seals read in keyset batches of 10,000 and a per-hospital cap on a catch-up (5M entries) |
| C7 | A malformed bucket object (valid JSON that is not an object) escapes the classification, so the sweep fails forever | confirmed, medium | every field type-checked; a parse fault marks that one object, never the read; deep nesting covered; acknowledgeable |
| C8 | A failed sweep or nightly pass is not retried and looks fresh, so staleness alerts cannot fire | confirmed, low | `ok_at` moves only on success and feeds every staleness rule; a failed sweep is retried at the next nightly, a failed nightly after 1 h; AuditWatchFailing critical for fast and incremental |
| C9 | AuditCheckpointLost fires falsely for newly activated hospitals, and on every pass for a hospital whose seeded check finds problems | confirmed, low | lost counts only a hospital made before the checkpoint store (one pass of margin), once per process |
| C10 | state.json is serialised while the nightly thread mutates the cycle dict outside the lock | **refuted** | none needed: the snapshot stays well-formed and the nightly pass saves the final cycle itself; it would matter only on a free-threaded Python build, which we do not use |
| C11 | The promtool tests check the absent() rules only on the firing side | partly, low | silent-side cases added for AuditFingerprintNeverRead, AuditWatchAbsent, AuditWatchFullCheckAbsent; a static test that every metric a rule reads is one the watch exports |
| C12 | `measure` recomputes every chain from seal 1 on the live database, and the runbook's `once` shares the serving watch's state directory | partly, low | `once` without `--dir` never writes the serving state; `measure` bounded (7 days, 200,000 entries a hospital); `--full` only on a `bench_*` or `*_drill` database |
| C13 | deploy.sh's pre-flight does not cover the new `secrets/audit-watch.env` | confirmed, low | `staging.sh db-split-secrets --check` (all three addresses, file names only), run by `deploy.sh` before anything is sent |
| C14 | An offboarding event does not stop chain and fingerprint alerts while the hospital row still exists; and once accepted it hides edits to a trail that is still there | partly, low | an accepted offboarding excuses a hospital only once its schema is gone (`ihr_audit_hospitals_closed`); a closed hospital with a trail keeps every check; TENANCY section 7.7 states the order (seal, fingerprint, event, then schema and row in one transaction) |

How the fixes were checked: for 13 findings the old behaviour was put back and the new regression
test failed (new class `ReviewWatchTests`, 19 tests); the verifier then mutated the fixes again (18
mutations) and each made its test fail.

## Second round

The verifier closed 17 findings, partly closed S4, C1 and C3, and found 6 new problems. A second
fixer closed all that code could close (new class `SecondReviewTests`, 6 tests, plus static tests;
seven reversions, every one made its test fail).

| # | New problem or gap | Fix |
|---|---|---|
| N1 | A real offboarding raised a false critical AuditEventInvalid for up to a week (once the hospital left the hourly fingerprints, the event no longer matched a claim) | accepted offboardings are kept in the watch's state and stand unless a newer claim contradicts them; an event the watch never saw is judged against the fingerprints around it |
| N2 | Log-line injection through bucket object keys (a key with a newline and a forged `ihr-audit:` line, held by anyone with the add-only writer key) | the event key must have the writer's exact shape, kind and uuid matching the body; keys are shown escaped; every log record of the watch is one line (`ihr_audit/oneline.py`) |
| N3 | False critical pages during a release (a migration locking a held hospital makes it `unchecked`) | a hospital a release holds is skipped for seal lag and its unfinished checks are deferred (gauge `ihr_audit_hospitals_held`, warning line) for at most two hours, the window budget, from the earlier of the hold's start and the watch's first sighting |
| N4 | The head record was reset, and generations compared falsely, during an events outage or on a new restore event | the record is never wiped on a generation change, never written from a failed events read; generations are not compared when events could not be read |
| N5 | deploy.sh did not check the reader key, so a folder with monitoring on and an empty key sent everything before the server refused | `staging.sh watch-reader --check`, run by `deploy.sh` before DEPLOYING is written and before `docker save` (not for a local proof) |
| N6 | The live proof was not repeated after the fixes | not repeated; stated under known limits |
| S4 | Celery's add-only writer key can still add events | judging stronger (N1, N2); the separate writer key or prefix is an owner decision (bucket policy), left as a known limit |
| C1 | Nothing writes restore events until P1-T22b | known limit by design, documented in the rollback text and the runbook |
| C3 | The release-time lock case | fixed with N3 |

Final results: app-role mode Ran 679 OK (45 skipped); off mode Ran 645 OK (49 skipped);
`ihr_audit.tests.test_watch` 47 OK; `isolation-mutation.sh check`, `check-logs.py` and pre-commit
(41 hooks) OK; static script tests OK.

## Known limits left

- Events share Celery's writer key (S4, owner decision): a forged offboarding or restore event
  backed by real data is gated only by the warning AuditEventRecorded.
- Nothing writes restore events until P1-T22b: a `rollback --restore` raises AuditChainBroken and
  AuditFingerprintMismatch until then (record an incident, never a silence).
- A release longer than two hours pages like any other unreadable hospital; an owner can defer a
  hospital's failures for two hours by holding it.
- `ihr_audit_verify --all` still honours any well-formed offboarding event; the watch is the
  alerting path. The metrics allow-list trusts Docker's name resolution of `prometheus`.
- The header walk costs about 5 minutes of one core per 15-minute pass at worst-case scale after a
  year (P1-T23 measures it on the server).
- Nightly findings clear only at the next 04:00 IST run.
- The live proof was not repeated after the review fixes.
