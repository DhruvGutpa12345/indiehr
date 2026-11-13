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
