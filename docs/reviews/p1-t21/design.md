# P1-T21 design: audit watch (draft for attack, 2026-11-13)

PLAN P1-T21 (Phase 1 review R1-11, confirmed): nobody watches the audit trail's integrity.
Sealing and the hourly fingerprints run on beat, but `ihr_audit_verify` runs only by hand, and no
alert covers seal lag, a late fingerprint or a broken chain.

Research with file:line evidence and cost estimates: the run's scratchpad `t21/research.md`.

## Facts that shape it

- A full `verify --all` recomputes every chain from seal 1, one query per seal. At 100 hospitals
  after a year that is about 1.8 billion entries: hours of a core. It can run on a copy (the
  monthly restore drill already does), never on the live database.
- Only the backend holds the fingerprint *reader* key; Celery holds the writer key only (by
  design). So a Celery task cannot check fingerprints.
- The monitoring rules forbid hospital labels in metrics (privacy). A hospital is named only
  through a fixed log line and vmalert, as `PackApplyFailed` does.
- Sealing waits for the oldest open transaction in the whole cluster. One long transaction
  anywhere (even Keycloak's) stops sealing everywhere.
- Suspended, offboarding and archived hospitals are never sealed today: beat runs only for active
  ones.

## Design

1. **An `audit-watch` service** (in `compose.monitoring.yaml`, modelled on the log archiver).
   - **Run:** the backend image, `manage.py ihr_audit_watch serve`.
   - **Credentials:** the fingerprint **reader** key only (never the writer); the database as the
     app role once P1-T19 lands (reads only).
   - **Networks:** `internal` and `monitoring`.
   - **Container:** read-only filesystem, a small volume for its checkpoints, `mem_limit` about
     192 MB. On the 8 GB server this is measured by P1-T23. The runner-up is a host timer with a
     one-off run, which needs your sudo for a new timer.
   - **Metrics:** a stdlib metrics endpoint serving its last results; Prometheus scrapes it via a
     target file. It is in the container-down alert's list.
2. **Passes:**
   - **Every 60 s (fast):** per hospital, the oldest finished entry not yet sealed and the oldest
     held back by a running transaction (one index range query each).
   - **Every 15 min (incremental):**
     - trigger coverage per hospital;
     - each chain from its stored checkpoint (seal number, hash, upper bound). A checkpoint
       hash that no longer matches fails;
     - fingerprints since yesterday, listed per day folder.
   - **Nightly:**
     - a weekly full fingerprint sweep;
     - a rolling recompute of old windows within a budget (about 5 million entries a night,
       many windows per query), so a quiet edit of an old entry is found within a cycle.
   - **Monthly:** the full recompute stays in the restore drill (on a copy).
   - The checkpoints live in the watch's own volume, outside the database, so the database owner
     cannot move them.
3. **Sealing gaps closed:**
   - a platform-once beat task, `ihr_audit.seal_inactive` (every 10 min), seals suspended,
     offboarding and archived hospitals;
   - the watch skips provisioning and held (P1-T18) hospitals for seal lag only, and counts them;
   - `ihr_audit_verify` gets `--json` and a `--from-checkpoint` option (shared code with the
     watch).
4. **Metrics** (global, no hospital label; only `pass` on the watch's own): when each pass last
   ran and whether it was OK; hospitals checked or skipped; hospitals with a broken chain, trigger
   problems or a fingerprint mismatch; fingerprint store problems; newest fingerprint time;
   fingerprints readable; bucket lock OK; oldest unsealed and oldest held-back entry; when the
   rolling recompute last completed a cycle.
5. **Alerts** (`rules/audit.yml`, each with a promtool test on both sides of its threshold):

| Alert | Fires when | Severity |
|---|---|---|
| `AuditChainBroken` | any hospital's chain fails | critical, at once |
| `AuditFingerprintMismatch` | a fingerprint does not match, or the store has problems | critical, at once |
| `AuditTriggersMissing` | a table lacks its trigger, for 30 min (covers migrate, then `post_migrate`) | critical |
| `AuditFingerprintLate` | newest fingerprint older than 2 h 15 min (warning) / 6 h (critical); missing for 3 h | warning / critical |
| `AuditFingerprintsUnreadable` / `AuditFingerprintBucketWeak` | the reader fails for 1 h / the bucket lock is weakened for 30 min | warning / critical |
| `AuditSealLag` | oldest unsealed entry older than 15 min (warning, for 5 min) / 1 h (critical) | warning / critical |
| `AuditSealHeldBack` | an entry held back over 1 h (points to the long-transaction alert) | warning |
| `AuditWatchStale` / `FullCheckStale` / `Failing` / `RehashOverdue` | the watch itself stops checking | warning |

   Plus a log alert, `AuditHospitalCheckFailed`, on the fixed line
   `ihr-audit: hospital=<slug> check=<chain|fingerprint|triggers|seal> outcome=failed`. It
   accepts only the watch service and an exact line, and names the hospital in the mail.
6. **Tests:**
   - promtool for every alert (both sides of every threshold, absent series);
   - the monitoring settings test (reader key only, read-only filesystem, memory limit,
     networks, target file, container-down list);
   - `check-logs.py` fixtures, including a forged line;
   - unit tests of the incremental verify (a checkpoint, a changed old entry found by the
     rolling recompute, a moved checkpoint refused);
   - the seal of inactive hospitals;
   - a live proof on the local staging stack: break a chain on purpose, and see the alert fire.

## Decisions (auto-accepted, awaiting the owner)

- **Q-K32:**
  - a resident `audit-watch` service (about 192 MB), runner-up a host timer;
  - the thresholds above;
  - inactive hospitals are sealed every 10 minutes;
  - the full recompute stays monthly on the drill copy;
  - an old-entry edit is found within one rolling cycle (about a month at 100 hospitals), and
    within an hour for anything newer than the last fingerprint.

## After the attack (2026-11-13): the design as it is built

One Opus reviewer covered correctness, security and operations, and a second agent tried to refute
each finding. Result: 12 findings, 0 refuted, 5 confirmed, 7 partly. Where this section and the
sections above disagree, this section wins.

1. **Networks (A1).**
   - The watch gets its own way out, `auditout` (like the log archive's `archiveout`), plus
     `internal` for the database and `monitoring` for Prometheus. It is never on `edge`.
   - The local proof puts the S3 stand-in on `auditout` too, so it exercises the real path.
   - The metrics server is threaded, with short socket timeouts, and answers `GET /metrics`
     only.
2. **What the rolling recompute promises (A2, A3).**
   - **Every 15 min:** a cheap **header walk** checks every seal's link (`prev_hash` and the seal
     hash recomputed from its stored fields) from the newest fingerprinted seal back. So a
     rewritten seal header is found at once.
   - **Nightly:** the rolling recompute of entries covers a recent horizon (the last 30 days of
     windows), with its budget sized from a measured ingest rate.
   - **Monthly:** the full recompute on the drill copy, batched by windows, not one query per
     seal.
   - So the guarantee is stated honestly: an edit of a recent entry is found within a night; of
     any entry within a month (the drill).
   - `AuditRehashOverdue` is relative to that horizon.
3. **Restores and offboarding are events, not alarms (A4, High).**
   - P1-T22's restore record and an offboarding deletion are written as write-once **events** in
     the fingerprint bucket (owner-run, existing writer path).
   - The watch and `ihr_audit_verify` read them, so a restored hospital is checked against its
     superseded segment, and a deleted hospital's fingerprints are closed rather than "missing".
   - Checkpoints are keyed by hospital uuid with a restore generation. They are never reset
     silently.
4. **Store problems are classed, and hospitals are named per hospital (A5).** The global store
   gauge is split by class, each with its own alert. The per-hospital log alert stays the main
   channel (it re-fires for each new hospital and can be silenced per hospital).
5. **Seal lag that a long transaction cannot inflate (A6, A12).**
   - Lag is measured the way verify measures a stalled sealer: unsealed entries whose
     transactions are already older than the snapshot horizon, against the last seal.
   - A long transaction shows as `AuditSealHeldBack` (pointing to the long-transaction alert),
     not as a critical lag.
   - `staging.sh release` seals and fingerprints right after the hold, with the old build.
6. **Only comparable hospitals are judged (A7):** trigger coverage runs only for hospitals that
   are servable (P1-T18). Held, failed or older-version hospitals count as "not checked", with a
   long-threshold warning. Chain and fingerprint checks still run for all.
7. **Bootstrap without blind trust (A8):** a missing or contradicting checkpoint is seeded from
   the newest **fingerprint** (write-once bucket), never from the database head, and the header
   walk continues from there. A lost checkpoint raises a warning.
8. **Fail closed (A9):** every pass has its own stale threshold and an `absent()` rule (fast
   300 s, incremental 45 min, nightly 26 h). Lag series are always present: 0 means none, never
   missing.
9. **Disabled triggers within a minute (A10):** the 60 s pass runs one cluster-wide catalogue
   query for audit triggers that are not enabled. Its own metric fires critical at once.
10. **Its own read-only role (A11):** `ihr_audit_watch` (made by P1-T19's `ensure_grants`):
    CONNECT; USAGE on hospital schemas; SELECT on the two audit tables and the public rows it
    reads. Never the app role.

## As built (2026-11-14)

What runs is "After the attack" above, with these deviations and numbers.

**Code.**
- `ihr_audit/incremental.py`: the header walk (one SQL query per hospital: every seal's number,
  window bound, link and seal hash recomputed from its stored fields), the batched window
  recompute (many seals per query, each seal hash recomputed from the recomputed entries hash
  and the stored `prev_hash`, A3), the fast query, the disabled-trigger catalogue query, the
  checkpoint store and `check_from_checkpoint` (shared with `ihr_audit_verify --from-checkpoint`).
- `ihr_audit/watch.py` + `manage.py ihr_audit_watch serve|once|measure`: four passes (fast,
  incremental, nightly, sweep), each in its own thread; threaded stdlib metrics server (GET
  /metrics only, 5 s socket timeout, no access log).
- `ihr_audit/events.py`: the restore and offboarding event format (`ihr-audit-event/1`), reader,
  verification of a restore against its superseded segment, and the writers `write_restore` /
  `write_offboarding` for P1-T22 and offboarding.
- `ihr_audit.seal_inactive` (platform-once, every 10 min); `ihr_audit_verify --json
  --from-checkpoint`; `anchor.py`: day-folder listing with `since`, store problems by class with
  their object key, fingerprint fields validated (slug, uuid, hash; A5), an `excuse` hook.
- `ihr_platform.settings.watch` + `secure.watch_problems`; `db_roles`: `IHR_DB_ROLE=watch` is
  checked (`watch_problems`: identity, no CREATE/TEMPORARY, owns nothing, no write or sealing
  right anywhere), the watch role is in use (a missing LOGIN is a problem), it reads
  `ihr_platform_hospitalschemastate` too. `staging.sh`: `secrets/audit-watch.env` made by `init`
  and `db-split-secrets`, LOGIN with `CONNECTION LIMIT 4` re-asserted at every `up`.

**Deviations from the design.**
1. **Seal lag is the watch's own sighting**, not `max(sealed_at)`: the time since the watch first
   saw finished entries waiting with the same newest seal; any new seal resets it. Right after a
   start the sighting is bounded by the database (the newer of the newest seal and the oldest
   waiting entry). The gauges are seconds (`ihr_audit_seal_lag_seconds`,
   `ihr_audit_seal_held_back_seconds`), always present, 0 for none, instead of timestamps.
2. **The absent() rules have their own names** (AuditFingerprintNeverRead, AuditWatchAbsent,
   AuditWatchFullCheckAbsent): promtool's lint refuses two rules of one name with the same labels.
3. **AuditRehashOverdue** is "no complete 30-day cycle for 50 hours" (two nights), the horizon's
   promise being one night.
4. **Store problems by class** are five alerts (object unreadable, clock skew, rewritten or
   hidden, unknown hospital, bad event); the lock class is AuditFingerprintBucketWeak's. A
   reviewed object is acknowledged in `ihr_audit/acknowledged_objects.txt` (whole key, date,
   reason; A5's acknowledgement path), not by a mute.
5. **Two log alerts**: AuditHospitalCheckFailed (chain, fingerprint, triggers; critical, 30 min)
   and AuditHospitalSealLate (seal; warning, 10 min), both from `audit-watch` only.
6. **A restoring hospital** (held with reason `restore`, P1-T22's) is skipped by every check and
   counted (`ihr_audit_hospitals_restoring`), as A4 asks; `restore` is not a hold reason yet.
7. **mem_limit 256m** (design: about 192 MB; A8: up to 384m). Measured RSS below.
8. Events live under `anchors/ihr-events/<suffix>/`: inside the writer's and reader's existing
   policies (`anchors/*`), outside the fingerprints' listing (`anchors/<suffix>/`). No bucket
   policy changes.
9. **Superseded segments are stubbed** (`events.SEGMENTS.source`, None): P1-T22 archives them and
   sets the source to `(hospital uuid, generation) -> Segment(seals, verify_entries)`. Until then
   every restore event excuses nothing (fail closed); the tests use a stand-in segment.
10. The DatabaseRoleRefused log alert now also takes `role=watch` from `audit-watch`.
11. `ihr_audit_watch` skips Django's system checks (`requires_system_checks = []`): they are the
    web app's (Keycloak addresses, its client key, a shared rate-limit cache) and refused to
    start the watch, which holds none of them; its own settings and role are checked instead.
12. A nightly or sweep finding stands until the next nightly re-checks it (persisted in the
    watch's state), also after the trail was put back by hand: seen in the live proof.

**Measured** (this machine; dev data and a synthetic bench in a throwaway `bench_*` database,
dropped afterwards):
- Dev trail: 8 hospitals, 13,030 entries, 721 seals; ingest 1,861 entries a day (7-day mean),
  so the 30-day horizon is about 56,000 entries today. Passes on dev: fast 0.16 s, incremental
  0.4 s, nightly 0.3 s (all 13,030 entries recomputed in 0.12 s), sweep 0.2 s (103 fingerprints).
- Bench, 500,000 entries of about 2 KB (101 seals): the window recompute **58,600-65,000 entries
  a second** (one core, cold and warm); the fast query 0.4 ms after the index fix (it was a
  0.34 s full scan with a subquery bound: fixed, measured).
- Bench, 525,600 seals (a year of one seal a minute): the header walk **2.7-3.3 s, 160,000-200,000
  seals a second**.
- The watch's container on the proof stack: **130 MiB** in use, peak RSS 142 MB (limit 256m).
- **Budget:** 160 million entries a night (`IHR_AUDIT_WATCH_REHASH_BUDGET`), with a 45-minute
  limit: 45 min at the measured 60,000 a second; it covers the research's 100 busy hospitals
  (50,000 entries a day each, 150 million in 30 days). Today's fleet needs about 1 s.

**Known limits.**
- The header walk reads every seal of every hospital every 15 minutes: at the worst case (100
  hospitals sealing every minute) about 5 minutes of one core per pass after a year, growing with
  age. Fine for the pilot; P1-T23 measures it on the 8 GB server, and the fix is walking every
  N-th pass or keeping a digest per range.
- The rolling recompute covers 30 days; older entries are recomputed only by the monthly restore
  drill on a copy. A disable-write-enable inside one owner transaction leaves no trace for any
  poll (A10, out of scope: P1-T19 removed DISABLE TRIGGER from every runtime role).
- Seal lag is measured only while the watch runs; a restart starts a new sighting bounded by the
  database.
- A restore event excuses nothing until P1-T22 provides segments; a single-hospital restore
  before then reads as a cut (correctly).
- A first start raises AuditCheckpointLost once (every hospital seeded from its fingerprint).

**Live proof** (local staging stack, `IHR_MONITORING=test`, journald logging, the proof image
`indiehr-backend:p1t21-proof` built from this tree; the dev stack stopped; Keycloak, the web
app, Traefik, Grafana and the two Keycloak workers stopped after `up` to fit the RAM):
- The watch started with its own role (`ihr-db-role: watch role checked`), read the fingerprint
  bucket over `auditout` only (`s3-auditout`), and served `/metrics` (GET only: `/` and others
  404, POST 501). Prometheus target `audit-watch` up.
- First pass after a fingerprint: `reseeded 1` (the checkpoint seeded from the fingerprint),
  then 0.
- **Break:** as postgres, seal 1 of hospital `alpha` rewritten consistently in itself (a forged
  entries hash and the hash recomputed from its own prev_hash, A3), checkpoint at seal 2. The
  watch: `seal 2: does not follow the previous seal (its prev_hash)` (header walk) and `seal 1
  differs from its fingerprint` (fingerprint); `ihr-audit: hospital=alpha check=chain
  outcome=failed` and `... check=fingerprint ...` on its log; `ihr_audit_chain_broken_hospitals 1`.
  AuditChainBroken and AuditFingerprintMismatch firing in Prometheus 46 s after the watch's pass;
  both mailed to the stand-in at 22:42:41 UTC; the log alert AuditHospitalCheckFailed
  (`hospital="alpha"`) in Alertmanager and mailed at 22:43:38, naming the hospital and the action.
- The catch-up nightly pass found it too (rolling recompute: `seal 1: entries changed since
  sealing`; sweep: the fingerprint).
- **A switched-off trigger** (`ALTER TABLE ... emr_patient DISABLE TRIGGER ihr_audit`):
  AuditTriggerDisabled firing 56 s later, `check=triggers` line; switched back on.
- The seal put back: a fresh check (`once --pass incremental --pass nightly`) clean; the serving
  watch kept the nightly findings until its next nightly (deviation 12).
- Torn down: `staging.sh down -v`, secrets, `.env`, `.local`, the proof image and the generated
  files removed or put back as they were; no volume or network left; dev stack back
  (`/api/ihr/bootstrap/` 200).

**Tests.** App-role mode `Ran 654 tests ... OK (skipped=45)`; off mode `Ran 620 tests ... OK
(skipped=49)` (a first off-mode run had one error, the watch-role test, which needs the roles:
now skipped there like the other role tests); `ihr_audit.tests.test_watch` 22 tests;
`isolation-mutation.sh check` OK with the new row `audit-header-walk` (applied by hand: both
HeaderWalkTests failed, then the file was put back); `check-logs.py` "Log pipeline OK";
`pre-commit run --all-files` all passed (promtool tests of `rules/audit.yml` included).

## Review fixes (2026-11-14)

An independent review of the built change: 20 findings (S1-S7, C1-C14 less C10), every one
confirmed or partly confirmed by a challenger, each fixed with a regression test that fails
without its fix (checked by reverting each fix in turn: the review tests fail).

- **S1/C2, fail closed per hospital.** Each hospital is checked in its own transaction; one that
  raises (a lock past `lock_timeout`, a statement timeout, a revoked grant) is **unchecked**
  (`ihr_audit_hospitals_unchecked{pass}`; AuditHospitalsUnchecked critical: fast 5 min,
  incremental 20 min; nightly warning) and named `check=unchecked` from its second failing pass
  in a row; the others are still checked. The disabled-trigger count is published straight after
  its catalogue query. Seal lag of an unchecked hospital keeps ageing its last sighting.
- **C8, success is what counts.** `ihr_audit_watch_last_success_timestamp_seconds{pass}` moves only
  when a pass ends without an error; every staleness and `absent()` rule reads it. AuditWatchFailing
  is critical for fast (5 min) and incremental (20 min). A failed sweep is tried again at the next
  nightly; a failed nightly an hour later.
- **S2, rows the owner can write.** A hospital the watch judged before (a checkpoint, a fingerprint,
  the last sweep's claims) whose row now says provisioning is a broken chain. A restore-held
  hospital keeps its standing nightly and sweep findings and is counted; AuditHospitalsRestoring
  warns after 6 h and pages after 24 h. *Deviation from the challenge:* no fingerprint comparison
  during the hold: the restore replaces the chain, so it would page on every genuine restore; the
  comparison runs again the moment the hold ends, and the hold itself is alerted.
- **S3, the horizon.** The watch records each hospital's head at its first pass of each day (state
  `heads`, by restore generation, 33 days kept). The nightly cycle starts at the lower of the
  database's first seal sealed since then and the watch's record; a back-dated `sealed_at` can only
  widen it. The cycle says how far the record reached (`horizon_by_watch_record`: full, partial,
  none). *Not done:* comparing `sealed_at` with the fingerprinted value.
- **S4, events.** An offboarding event closes a hospital only if the database no longer lists it
  as anything but offboarding or archived, the watch saw it so, its `final` is the newest
  fingerprint claim (recent listing or the last sweep's), and it does not contradict the
  checkpoint; or its object key is acknowledged. Otherwise it is a bad event (class `events`), the
  hospital stays unknown and keeps its checkpoint. Restore generation gaps and doubles are bad
  events too (a missing segment is not). Each new event is named once,
  `ihr-audit: hospital=<slug> event=<kind> outcome=recorded` (AuditEventRecorded, warning; slug from
  the database or the watch's own record, `unknown-hospital` otherwise). *Owner decision left:* a
  writer key or prefix for events that Celery's add-only key cannot use (a bucket-policy change).
- **S5/C6, seeding.** No checkpoint is seeded while a configured store cannot be read
  (`ihr_audit_checkpoints_unseeded_hospitals`, AuditCheckpointUnseeded after 1 h); a hospital with
  no recent claim is seeded from the last sweep's claim; a genesis seed of a chain with seals counts
  as lost. A first check is bounded (`IHR_AUDIT_WATCH_INCREMENTAL_MAX_ENTRIES`, 5 million a pass;
  the checkpoint moves as far as was recomputed), and seal rows are read in keyset batches of
  10,000, never all at once.
- **S6.** A slug off its pattern is named `invalid-slug`, never dropped; free-text logs quote slugs.
- **S7.** The metrics server answers only Prometheus's addresses (resolved again as it moves) and
  itself, checked before any thread starts; at most 4 requests at once; each cut off after 10 s;
  `pids_limit: 64`.
- **C1.** `rollback --restore` says, before it is confirmed, that every hospital sealed since the
  point will read as cut (critical) until restore events exist; P1-T22b (TRACKER, PLAN) carries
  those events for whole-database restores. No reset path was added.
- **C3.** A release seals and fingerprints with the new build after its fleet run and before `up`;
  after a start the watch dates a wait no earlier than one `seal_inactive` cycle and a minute
  before a line could come. The docstring now says what is skipped.
- **C4.** `up` and `release` refuse a stack with monitoring and no reader key before they change
  anything; `monitoring-init` warns.
- **C5.** `IHR_AUDIT_WATCH_REHASH_MAX_SECONDS` and `_DAYS` reach the container; the watch exports
  `ihr_audit_rehash_stopped_by` and `ihr_audit_rehash_rate_entries_per_second`; AuditRehashOverdue
  is two rules (`limit: budget` / `limit: time`, the latter also when the gauge is missing) naming
  the right knob. A static test checks every knob the README names reaches compose and the watch.
- **C7.** Any exception from one object's body (a list, a string, null, deep nesting) marks that
  object alone (class `unreadable` or `events`), acknowledgeable; a storage error still makes the
  read unreadable.
- **C9.** Lost counts only hospitals made before the checkpoint store, once per process.
- **C11.** Silent-side promtool cases for the three `absent()` rules; a static test that every
  metric a rule reads is one the watch exports.
- **C12.** `once` without `--dir` never writes the serving watch's state and counts standing
  findings apart; `measure` is bounded (7 days, 200,000 entries a hospital, counts from the seals);
  `--full` only on a `bench_*` or `*_drill` database.
- **C13.** `staging.sh db-split-secrets --check`; `deploy.sh` runs it (and fills a proof VM's
  folder) before anything is written or sent.
- **C14.** An accepted offboarding excuses a hospital only once its schema is gone
  (`ihr_audit_hospitals_closed`); a closed hospital whose trail is still there keeps every check.
  TENANCY §7.7 states the order (seal, fingerprint, event, then schema and row in one transaction).

**Known limits added.** `ihr_audit_verify --all` still honours any well-formed offboarding event
(it has no record of what the watch saw); the watch is the alerting path. The S7 allow-list
trusts Docker's name resolution of `prometheus` on the shared networks. The live proof was not
repeated after these fixes.

## Second review fixes (2026-11-14)

A verifier re-checked the review fixes: S4, C1 and C3 partly closed, and six new problems. Each
fix has a regression test in `ihr_audit.tests.test_watch.SecondReviewTests` (or the staging static
tests) that fails without it: checked by reverting each fix in turn (seven reversions, every one
made its test fail), then the files put back.

- **A real offboarding no longer raises AuditEventInvalid for up to a week.** The watch keeps
  each offboarding event it accepted (state `offboarding_accepted`, with its final seal). Once the
  closed hospital drops out of the hourly fingerprints, the acceptance stands unless a newer claim
  (the next sweep's) names a seal after the final one; any refusal withdraws it. An event the watch
  never got to accept (it was down while the hospital was still fingerprinted) is judged against
  the fingerprints since the day before the event, read once per pass, for an event younger than a
  sweep cycle; older ones against the last sweep's claim, which by then has read them.
- **No log line from the bucket's object keys.** An event object whose key is not the name the
  writer gives it (`<prefix>YYYY/MM/DD/<time>Z-<kind>-<uuid>-<8 hex>.json`, kind and uuid those of
  its body) is a bad event, and its key is shown escaped. Every log record of the watch is one line
  whatever it holds, traceback included (`ihr_audit/oneline.py`, set for every formatter by
  `settings/watch.py`; checked in the dev image: a message with a line break and the fixed line
  came out as one `ERROR ...` line). The fixed lines go to standard error directly, never through
  logging.
- **Releases do not page (the C3 residual and the fail-closed one).** A hospital a release holds
  (`ihr_fleet_run`'s window, migrate or roles hold; any status) is skipped for seal lag, and a
  check it cannot finish in the fast, incremental or nightly pass is deferred, not unchecked
  (`ihr_audit_hospitals_held`; logged as a warning), for at most two hours (`HOLD_GRACE_SECONDS`,
  the window's own budget, `WINDOW_BUDGET_MINUTES`) from the earlier of the hold's start and the
  watch's first sighting of it (kept in its state: re-dating `upgrading_since` cannot stretch it).
  The release keeps every hospital held until its fleet run ends and seals with the new build
  right after (C3), so nothing waits unjudged. A restore hold is not such a hold. Chain checks of
  held hospitals still run; only a failure to finish is deferred.
- **The 30-day head record survives an events outage and new restore events.** It is no longer
  reset when the restore generation changes (an older day can only widen the nightly), and it is
  not written from a failed events read. `check_from_checkpoint` no longer compares generations
  when the events could not be read (it was a false "one was lost?" AuditChainBroken for every
  restored hospital during any outage); the checkpoint's seal is still checked as it is, so a
  restore during an outage reads as a rewrite (fail closed).
- **deploy.sh asks for the reader key** (`staging.sh watch-reader --check` on the folder it
  sends) before DEPLOYING is written or anything sent, as it does for the split database secrets
  (C13). A VM proof's own init sets its storage up there and is not asked.

**Known limits (second review).**
- **Events share Celery's writer key (S4, owner decision).** The add-only fingerprint writer key,
  which Celery holds, can add restore and offboarding events. A forged offboarding still closes
  a hospital if the owner first sets it archived and offboarding, lets the watch see it, and writes
  `final` as the newest fingerprinted seal; a forged restore event backed by a real superseded
  segment (once P1-T22 sets `SEGMENTS.source`) likewise. Each new event is named once by the
  warning AuditEventRecorded, which a human must confirm. Closing it needs a separate writer key or
  prefix for events that Celery cannot use (a bucket-policy change: an owner decision).
- **Whole-database restores (C1).** Nothing writes restore events until P1-T22b. A documented
  `rollback --restore` therefore raises AuditChainBroken and AuditFingerprintMismatch for every
  hospital sealed since the restore point, and nothing clears them until P1-T22b ships; the
  rollback says so before it is confirmed, and the README says to record an incident, never a
  silence.
- **A release longer than two hours** pages as any other unreadable or late hospital once the
  grace ends (ReleaseWindowOverrun covers the hold itself while the workers run).
- **An owner can defer a hospital's failures** by holding it, for two hours from the watch's first
  sighting of the hold; holding it longer, or lifting and re-holding it while the watch looks,
  brings it back into every check. A held hospital's chain is still checked whenever it can be read.
- **The live proof was not repeated** after these fixes; they are covered by the unit and static
  tests above, and the one-line formatter was checked in the dev image.
