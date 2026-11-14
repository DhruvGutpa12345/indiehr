# P1-T22 design: per-hospital dumps and single-hospital restore (draft for attack, 2026-11-13)

PLAN P1-T22 (Phase 1 review R1-08; TENANCY_DESIGN §7.5, §7.7; ADR-002; ADR-007). Today only
the whole cluster can be restored (pgBackRest point in time). Undoing one hospital's mistake would
roll back every hospital.

Research with file:line evidence: the run's scratchpad `t22/research.md`. The research found that
the TENANCY §7.5 runbook as written is unsafe here; see "Corrections".

## Corrections to TENANCY §7.5 / §7.7 (from the research)

1. **No restore under a side name.** Our audit trigger functions have the schema name inside
   their bodies and arguments. A restored copy under a side name would write its audit entries
   into the **live** hospital's trail, or, once re-installed, break every write after the swap.
   - Instead, with the hospital held, the **live** schema is renamed to a parked name
     (`ihr_parked_<schema>_<time>`, not `h_…`; the app has no rights on it).
   - The dump is restored under the hospital's real schema name.
   - `Hospital.schema_name` never changes.
2. **The erasure ledger cannot live inside the hospital's schema:** a restore would roll it back
   too. It lives in `public`, keyed by hospital, with ids only (P4-T1). Until P4-T1 exists, a
   restore refuses if any erasure is recorded.
3. **The audit trail across a restore.** Fingerprints taken after the dump's time name seals the
   restored trail no longer has, and that would read as tampering. So a restore:
   1. seals and fingerprints the live trail;
   2. keeps the live tail (the seals and entries after the dump) as a superseded segment in the
      restored schema;
   3. writes a write-once **restore record** (hashes only) to the fingerprint bucket;
   4. adds a `restore` audit entry.
   Verification then checks earlier claims against the superseded segment.
4. **Transaction ids:** the restore refuses unless the cluster's current transaction id is
   beyond every id in the dump. A restore into the same cluster always passes this.
   Moving a hospital between clusters needs a "rebase seal", recorded as a later risk.
5. **Ids are not re-used:** every sequence is advanced to at least the parked schema's value.
   Public invitations and access requests pointing at users who no longer exist, or were made
   after the dump, are cancelled.
6. **Security state never goes back:**
   - users inactive in the parked schema stay inactive;
   - every staff session is invalidated;
   - the hospital's session keys are dropped;
   - the access difference (roles, memberships) is shown for the operator to confirm.

## Split (by Claude, 2026-11-13, awaiting owner confirmation: Q-K33)

- **P1-T22a: nightly dumps.**
- **P1-T22b: single-hospital restore and the drill** (after P1-T22a).

## P1-T22a: nightly dumps

1. **A `dumper` service** in `compose.backup.yaml`.
   - The Postgres 18 image (so `pg_dump` matches the server) with Python.
   - It runs at about 03:30 IST: after the 02:00 release window and the 02:30 pgBackRest backup.
   - It takes each hospital's advisory lock, uses `--lock-wait-timeout=60s`, and runs 2 at a
     time.
   - It dumps active, suspended and offboarding hospitals. A held hospital is skipped and
     reported.
2. **Its own role, `ihr_dump`:** LOGIN, member of `pg_read_all_data` only. A nightly job that
   reaches the network holds no write or DDL rights.
3. **Consistency:** one repeatable-read snapshot covers both:
   - the manifest data (the hospital's public rows, schema fingerprint, audit head, sequence
     values);
   - `pg_dump -Fc -n <schema> --snapshot=… --no-owner --no-acl`.
   Grants come back through P1-T19's `ensure_grants`.
4. **Encryption:**
   - authenticated encryption in chunks, reusing the log archive's sealer, moved into a shared
     module;
   - a per-hospital manifest chain with an HMAC;
   - its own key, `IHR_DUMP_KEY` (not the pgBackRest passphrase), which goes into your password
     manager;
   - every object is read back and checked after upload.
5. **Where:** the backup bucket in the other Indian region, under
   `dumps/<hospital uuid>/<date>.{dump,manifest}`. SeaweedFS for the local proof. A put-only
   writer key.
6. **Retention:** 14 nightly plus 4 weekly (about 28 days, the same as pgBackRest), by bucket
   lifecycle. Offboarding can delete a hospital's prefix (owner-run, like the log archive's
   expiry).
7. **Monitoring:** status metrics (no hospital label) and alerts:
   - a hospital with no good dump for 26 h;
   - no drill for 35 days;
   - a fixed log line names a hospital whose dump failed.
8. **Tests:**
   - script tests for the chain and encryption (as for the log archive);
   - the snapshot consistency;
   - held hospitals skipped;
   - the local staging proof times a dump of every hospital.

## P1-T22b: single-hospital restore and drill

1. `scripts/staging.sh hospital-restore <slug> (--dump <date>|latest | --from-pitr "<time>")
   --reason "…" [--dry-run]` drives `manage.py ihr_hospital_restore` (owner-run), through
   resumable steps recorded as a `TenantOperation` of kind `restore`, under the hospital's lock:
   1. **Pre-checks:**
      - the manifest's MAC and sha256;
      - the dump holds only that schema;
      - its fingerprint is not ahead of the code;
      - the transaction-id rule;
      - free disk;
      - no un-replayable erasures.
   2. **Hold** (a restore hold that a release cannot lift), and drain.
   3. Seal and fingerprint the live trail.
   4. Park the live schema.
   5. `pg_restore --single-transaction` under the real name. On failure, un-park and lift the
      hold.
   6. `ihr_fleet_run migrate roles packs --slug x`.
   7. **Reconcile:**
      - sequences;
      - invitations and access requests;
      - security state and sessions;
      - Redis keys, plus a rolling restart for per-process caches;
      - a Keycloak membership check (fail closed);
      - files (with bucket versioning, item 2);
      - the profile difference shown.
      ABDM is refused if any route exists (not built).
   8. **Audit:** the superseded segment, the restore record, a fingerprint, then
      `ihr_audit_verify --slug x` must pass.
   9. Smoke this hospital, lift the hold.
   10. The parked schema is dropped after 7 days (it holds personal data).
2. **File versioning** on the patient and pictures buckets, with old versions expiring after 35
   days. The restore brings back each referenced object as it was at the dump's time, and keeps
   (reports) newer uploads.
3. **`--from-pitr`** builds the restore-drill copy at that time, dumps the hospital from the copy,
   then follows the same path. The nightly dump's up-to-24 h loss is the fast path; this is the
   ≤15 min path (ADR-007).
4. **The drill:**
   - **Monthly:** on the restore-drill copy (never live), restore one hospital (rotating) from its
     latest dump. Every other schema must be byte-identical before and after, and the restored
     one passes migration, fingerprint and verify. Times are recorded.
   - **Quarterly:** a live drill on a synthetic `drill` hospital in staging.
5. **Tests:**
   - two seeded hospitals: dump A, change A, restore A. A is back; B is unchanged; sequences
     advanced; sessions invalidated; Redis keys gone; stale invitations cancelled; verify passes
     with the restore record;
   - a forged restore record, or a cut without one, fails;
   - each refusal;
   - a regression test proving why side names are refused;
   - mutation rows: skip the sequence advance, the session invalidation or the hold;
   - the local staging proof.
   `max_locks_per_transaction` for a single-schema restore is measured (P1-T23's rules).

## Decisions (auto-accepted, awaiting the owner)

- **Q-K33:**
  - the split;
  - dumps at 03:30 IST, kept about 28 days;
  - a dump key of its own;
  - a read-only `ihr_dump` role;
  - versioning on both file buckets;
  - parked schemas dropped after 7 days;
  - the superseded audit segment kept in the database, with hashes in the fingerprint bucket;
  - the nightly dump's up-to-24 h window, with `--from-pitr` for ≤15 min.
- TENANCY §7.5 and §7.7 get the corrections above (amendment, Proposed).

## After the attack (2026-11-13): the design as it is built

One Opus reviewer covered correctness, security and operations, and a second agent tried to refute
each finding. Result: 12 findings, 1 refuted (A5), 4 confirmed, 7 partly. Where this section and
the sections above disagree, this section wins.

### P1-T22a (dumps)

1. **A bucket of their own, built like the log archive's (A7).**
   - Versioning and a Governance Object Lock slightly longer than the retention.
   - A dumper-only key that may put and read under `dumps/` and never delete.
   - Retention by an owner-run `dump-expire` (as `expire.py`), which writes an authenticated
     erasure record before deleting. Daily and weekly prefixes.
   - Manifests hold **no personal data**: ids, hashes, sizes, fingerprints and sequence values
     only. The hospital's public rows are encrypted with the dump, not in the manifest.
   - The encryption key is still on the server (as for pgBackRest and the log archive). The lock
     keeps every version written, and the readers take the recorded versions and report any
     name written twice or hidden (fixer, review S1); that, not the MACs, is what holds against
     a compromised server, which can derive every key from the encryption key. Per-hospital
     keys come with P4-T4.
2. **`ihr_dump` joins P1-T19's role model (A9):** made and asserted by `ensure_grants`. CONNECT
   only, `pg_read_all_data` only, no attributes, no role settings.
3. **A size model before building (A10):**
   - bytes per audit entry, measured;
   - at P1-T21's 50,000 entries per busy hospital per day, the GB per hospital at 1 and 3 years;
   - dump and restore speed measured on generated volume in the staging proof, not on 820 KB
     demo hospitals.
   This sets the parallelism, the window and the disk check, and goes to P1-T23.

### P1-T22b (restore and drill)

4. **One lock, held once (A1, High).**
   - The lock helper records "held for this hospital by operation N", so the restore calls the
     same migrate, roles and pack code in-process, inside its own lock. It never starts
     `ihr_fleet_run`.
   - A separate `restore_hold` (with the operation id) is checked by `servable`, the release and
     fleet runner, beat, the dumper and pack apply. A release never clears it.
5. **A drill that cannot touch the live system (A2, High).**
   - `--drill` refuses unless the database is the drill container.
   - Every external step (Redis, restarts, Keycloak, file restore, fingerprint writes) becomes a
     reported dry run.
   - The drill container gets no `app.env`, no S3 or Redis settings and no `edge` network.
   - The "other hospitals unchanged" comparison also hashes triggers, function bodies, ACLs and
     public rows.
   - A cheap nightly check covers every dump: `pg_restore --list` plus a schema-only restore
     into scratch, not one hospital a month.
6. **Restore records that cannot hide a cut (A3).**
   - Verify accepts a restore record only when every fingerprinted claim after the dump is
     matched by the superseded segment, with its chain recomputed from the entries.
   - A record with no segment, or one that does not match, is reported as a cut.
   - There is no `--live-schema-lost` switch; a lost live schema is a reported cut.
7. **Superseded segments live outside the hospital schema (A4):** an append-only archive (UPDATE,
   DELETE and TRUNCATE refused, no app rights), keyed by hospital and restore number. So repeated
   restores, P1-T21's watch and trigger coverage all work. The watch reads it through P1-T21's
   restore events.
8. **A dump is untrusted SQL (A8).**
   1. It is first restored into a throwaway, network-isolated Postgres, as a role that owns only
      an empty schema.
   2. The result is checked: only that schema; no functions or triggers other than ours, by
      definition hash; no extensions, roles or grants.
   3. It is then re-dumped from there and restored into the live cluster.
9. **Crash-safe park and restore (A11).**
   - Resume reads the database's state, not only the step record:
     - the real name and the parked schema both exist → the restore committed;
     - only the parked schema → restore;
     - un-park only when the real name is absent.
   - `hospital-restore --abort` un-parks and lifts the hold at any step before the final swap.
   - A half-finished restore holds only that hospital, never `up`.
10. **Revocations are merged strictly by default (A12).** After `pg_restore`, any role assignment
    or membership present or stronger in the restored copy than in the parked one is
    soft-deleted or downgraded back. `--keep-restored-access` overrides it, recorded with the
    reason.
11. **DPDP (A6).**
    - A whole-cluster restore also rolls back a `public` erasure ledger. So until P4-T1 builds
      the ledger with an outside copy (the fingerprint bucket), any restore refuses when an
      erasure was recorded after its point. The check is real: it is the P4-T1 table, absent
      today, so the refusal triggers on its presence.
    - Files uploaded after the dump are reported and kept for the operator, never kept silently.

## P1-T22a as built (14-11-2026)

Code: `deploy/dumps/dumper.py` (the service and its commands), `deploy/dumps/expire.py` (the
owner's erasure), `deploy/lib/` (`sealer.py`, `chain.py`, `locked_bucket.py`: moved out of the
log archive, which now uses them too), `deploy/docker/dumper.Dockerfile`,
`deploy/staging/compose.dumps.yaml` (+ `.local.yaml`), `scripts/staging.sh dumps-*`, the
`ihr-dumps` identity in `deploy/s3/`, the bucket and key in `infra/aws/storage.tf`, rules
`deploy/monitoring/prometheus/rules/dumps.yml` and `deploy/logs/alerts/dumps.yml`. Runbook:
`deploy/staging/README.md`, "Hospital dumps". Size model: `size-model.md`. Proof: `proof-a.md`.
Tests: `scripts/test_dumps.py` (90; 6 run only in the dumper image, `scripts/test-dumps-live.sh`,
CI job "Dumper image"), and the staging static tests.

### Deviations from the sections above

1. **Its own compose file and image**, not `compose.backup.yaml`: `compose.dumps.yaml`, on with
   `IHR_DUMPS`; the image is the database's own `postgres:18.6-alpine3.23` (by digest) plus
   Alpine's Python, cryptography, boto3 and psycopg, running as `postgres`, no gosu. Networks
   `internal`, `monitoring` and its own `dumpsout`; never `edge`.
2. **Retention is by the owner's `dumps-expire`, not a bucket lifecycle** (item 6 above; the
   "After the attack" item 1 wins): 14 days under `dumps/daily/`, 28 under `dumps/weekly/`
   (Sunday's run), the bucket's Governance lock 30 days. It refuses shorter periods (a proof's
   `--allow-short` only on the local stand-in), counts age from the younger of the name and the
   upload, and writes a signed erasure record before deleting.
3. **Names carry a revision** (`<date>.r<N>`): an interrupted upload leaves a locked name, and
   the next attempt takes the next revision. The hospital's public rows are a second sealed
   object (`*.public.enc`), never in the manifest.
4. **Two waits, not one:** `pg_dump --lock-wait-timeout=60s` as designed, and before it the
   hospital's advisory lock (the provisioning runner's key) waited for up to 10 minutes
   (`IHR_DUMP_LOCK_WAIT`), then reported `reason=lock`.
5. **Added:** a quiet window 01:45–03:25 in which no dump starts (the release window and the
   backup); a catch-up run when the service starts (every hospital without a good dump in 26
   hours); the held check repeated inside the snapshot; `pg_dump --strict-names
   --compress=zstd` (the size model); a disk check per hospital; the night's schema-only restore
   of every new dump in a throwaway Postgres inside the container (moved forward from P1-T22b's
   item 5); `dumper.py drill-mark` for P1-T22b's drill.
6. **`ihr_dump` logs in now** (P1-T19's model: `in_use`), made by `staging.sh`'s role SQL, its
   address in `secrets/dumper-db.env` (from `init` or `db-split-secrets`). The dumper is not a
   Django process, so it checks its own role before every run (`dumper.py role`; superuser,
   attributes, memberships other than `pg_read_all_data`, stored settings, CREATE or TEMPORARY,
   any write privilege, any owned object) and dumps nothing otherwise (DumpRoleRefused).
7. **Metrics** on :9110 for Prometheus's address and the container only; 26 gauges, no labels.
   Alerts: DumpTooOld, DumpRunMissing, DumpRunFailing, DumpRunOverBudget, DumpNotAuthentic,
   DumpErasedEarly, DumpBucketNotLocked, DumpRoleRefused, DumpDiskLow, DumpDrillOverdue,
   DumpErasureOverdue, DumperDown, DumpsNotReporting; log alerts DumpHospitalFailed (with the
   reason) and DumpHospitalHeld.
8. **Where:** on AWS a bucket of its own in Hyderabad (`infra/aws`, Governance 30 days,
   versioning, a put-and-read key under `dumps/`; `tofu test` refuses one bucket for dumps and
   backups); on E2E a Chennai bucket made in the console at go-live (README). The provider is
   named only in `infra/` and the runbook.

### Numbers

- **Size model** (synthetic, this machine): 811 bytes per audit entry on disk, linear between
  3.65 M and 10.95 M entries; zstd dump 123 bytes per entry (0.15 of the disk bytes), sealed
  1.333 times that; `pg_dump` 405k entries/s; full restore 100k entries/s at 3.65 M, 77k at
  10.95 M. Per busy hospital (50,000 entries a day): **14.8 GB on disk, 3.0 GB sealed, about
  45 s to dump and 4 min to restore at 1 year; 44.4 GB, 9.0 GB, 2 min 15 s and 12–15 min at
  3 years.** The bucket holds about 18 dumps per hospital with daily expiry, and up to about
  25 just before the runbook's weekly expiry (19 nights, 6 Sundays): 75 GB at 1 year, 225 GB at
  3 years (size-model.md, "Bucket").
- **Settings from it:** zstd; 2 at a time; window 03:30–06:00 IST (DumpRunOverBudget at 2.5 h);
  disk check 0.30 of the schema's bytes on disk or the last sealed dump plus a fifth, plus
  512 MiB. Transfer is the limit: about 75 GB sealed a night at 25 MB/s (25 busy hospitals at
  1 year, 8 at 3 years).
- **Proof** (local staging, four hospitals, one with 500,745 entries and 358 MB): every
  hospital dumped, uploaded, read back and checked in **7.3 s** (the large one 1.6 s to dump and
  seal, 74 MB sealed); `dumps-check --deep --all` of 10 dumps 11.5 s; a full restore of the
  large one 5.1 s; the held hospital skipped and reported every run; the dumper's key refused
  every delete, lock change and write outside `dumps/`; the role refused every write;
  `dump-expire` refused under the retention.

### Known limits

- **The dump key is on the server** (as pgBackRest's and the log archive's): a server compromise
  reads the dumps and can derive the manifest and erasure-record keys, so it can write new dumps
  that look authentic (from that night on: it holds the database anyway). The dumps already
  written are protected by the lock (every version kept 30 days) and the version-aware readers
  (a replaced or hidden name is DumpNotAuthentic, review S1); on AWS the store also refuses a
  write without If-None-Match. The MACs catch accidents and outsiders, not the server.
  Per-hospital keys: P4-T4.
- **Erasure records say what their writer says** (review S2): they are authenticated with a key
  derived from the dump key, which the server holds. The dumper checks what a record says, not
  whether it is true: each erased manifest must be named by its dump name (an entry without one
  is a problem and never bridges the chain), and its date must be past its retention when the
  record was written (also for a record written with `--allow-short`, unless the dumper reads a
  proof's own stand-in, `STAND_INS`; every `--allow-short` erasure is counted as early whatever
  its records' status, once per erasure). A writer holding the dump key **and** the bucket's
  admin key could still name an older date for a manifest it erases, or erase a whole run of
  nights and keep only the newest. The real control is the store: the dumper key cannot write
  `erasure/` or delete a version (the prefix policy) and every version is locked 30 days. An
  owner-held signing key (Ed25519, the public half on the server) would close it; not built.
  The proof decision reads `IHR_DUMP_ENDPOINT`: `dumps-init` writes a stand-in address only with
  `--local` (a server's must be `https://`).
- **The log archive's erasure records** (P1-T9c) are checked less: an early erasure is read from
  the record's own `older_than_days`, and the days it names are not checked against 400 days.
  Its `expire.py` now refuses `--allow-short` off the stand-in and any abbreviation (review S3,
  second fix); the dumps' date check would close the rest there (not in P1-T22a).
- **A dump cut short by an operator's stop** (`fleet migrate|roles|packs|--resume`, the move of
  the roles, `up`, a release) leaves no log line for its hospital (the process is stopped): it
  is redone when the dumper starts again (the night's run if it had not finished, else the
  catch-up run), and DumpTooOld fires only if that fails too for 26 hours.
- **A store that ignores If-None-Match without the header** (the local stand-in; E2E unknown
  until `dumps-probe`) keeps a rewrite as a second version: detected and reported, not refused.
- **The log archive (P1-T9c) reads current versions** as the dumps did before review S1; the
  same version-aware readers would close it there too (not in P1-T22a).
- **Full dumps grow with the audit trail's age**, and each night every dump crosses the link
  three times (upload, read-back, night check). Before busy hospitals pass about a year:
  incremental audit dumps and a single download (size-model.md, "For P1-T23").
- **A hospital held at 03:30** is not dumped again that night once the hold lifts (DumpTooOld
  after 26 hours; `dumps-now --slug` by hand).
- **Not proven live:** Prometheus, Alertmanager and the log alerts (promtool tests and
  `check-logs.py` cases only); E2E's Writer role on a locked bucket (`dumps-probe` at go-live).
- **The proof's bucket** is the stack's own SeaweedFS (same disk); it needs 768m with dumps
  (`dumps-init --local` sets it).
- **No caller of `drill-mark`** until P1-T22b: DumpDrillOverdue fires 35 days after the first
  dump ever (`ihr_dump_first_dump_ever_timestamp_seconds`, which expiry never raises; review C5)
  unless the drill exists by then.
- **Clinical tables** are not in the size model (the audit trail is assumed to dominate).
- The first builder's proof logs were lost in a reboot; every number above was measured again.
