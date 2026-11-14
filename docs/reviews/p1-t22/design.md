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

## P1-T22b as built (14-11-2026)

Code: `plugins/ihr_platform/ihr_platform/restore/` (`engine.py` the steps, `package.py` the
quarantine's package and its load, `files.py` the buckets' versions, `drill_cache.py`),
`manage.py ihr_hospital_restore`, `ihr_audit/archive.py` (the superseded-segment archive,
`events.SEGMENTS.source`), the restore hold in `release.py` (+ `fleet.py`, `watch.py`,
`ihr_audit/tasks.py`, `deploy/dumps/dumper.py`), `runner.exclusive_for`, `settings/drill.py`,
migrations ihr_platform 0012 (operation kind, hold fields, the archive tables) and ihr_audit 0006
(the `restore` entry kind), `deploy/restore/quarantine.py`, `scripts/staging.sh hospital-restore*`,
log alerts `deploy/logs/alerts/restore.yml`, file-bucket versioning (35 days) in
`deploy/s3/setup_buckets.py` and `infra/aws/storage.tf`. Runbook: `deploy/staging/README.md`,
"Restoring one hospital (P1-T22b)". Proof: `proof-b.md`. Tests: `ihr_platform.tests.test_restore`
(end to end on a second hospital, every refusal, the hold everywhere, the package's checks, abort,
expiry), `scripts/test_restore_quarantine.py` (a real dump through the quarantine and eight kinds of
hostile dump refused, in the dumper image: `scripts/test-dumps-live.sh`), the log-alert cases, nine
mutation rows (`restore-sequence-advance`, `restore-session-invalidation`, `restore-hold-served`,
`restore-hold-fleet`; after the review `restore-final-seal`, `restore-sequences-before-migrate`,
`restore-patient-grants`, `restore-drain-jobs`, `restore-expressions-inspected`). TENANCY §7.5/§7.7 and ADR-002 (Amendment 2) and ADR-010 carry the
amendments, status Proposed (Q-K33).

### Deviations from the sections above

1. **Park and load are one transaction**, not "park, then `pg_restore --single-transaction`": the
   quarantine writes a **package** from its checked catalogue (each statement built here from
   `quote_ident`, `format_type` and `pg_get_*def`, the rows as COPY files with their SHA-256), and the
   restore's one transaction locks the live trail, seals all of it (a final seal of its own),
   renames the live schema, loads the package (each statement checked whole against the forms the
   exporter writes and sent alone through the extended protocol; CHECKs as NOT VALID, exclusion
   constraints and expression indexes made on the empty tables and every expression inspected in
   the live cluster before any row or later statement evaluates one; review S1/S2), compares the
   result's shape and every table's row count with the package's, recomputes the restored chain,
   advances the sequences, installs the trail's guard, defaults, triggers and sealing from this
   build's code, merges security state strictly, and checks every other schema's and public's
   catalogue is unchanged. A crash leaves either nothing or all
   of it; resume still reads the database's state (both schemas: committed; only the parked one: load
   again; neither: a lost schema, refused). `--abort` is allowed before reconcile (after it, outside
   state has changed): it drops the restored copy and renames the parked schema back.
2. **No function or trigger ever comes from a dump.** The quarantine refuses any that is not ours by
   name, kind and wiring; ours are compared with this build's **by definition hash** (the precheck
   reports a difference, e.g. an older build's) and are never loaded either way: the code installs
   them. The append-only guard, which only migration 0001 made, is re-made by
   `ihr_audit.install.ensure_append_only`.
3. **The restore hold** is two fields of `HospitalSchemaState` (`restore_operation`,
   `restore_since`): `schema_ready`, `ready_q` and so `servable`, the gate, sign-ins, beat, queued
   tasks, the staff worker and pack apply refuse it; `release_hold` never touches it; the fleet runner
   notes such a hospital "held" and leaves it alone (also at start-up, so a half-finished restore
   never stops `up`); the dumper skips it (`reason` restore); the audit watch shows it as restoring;
   the inactive sealer skips it; `require_ready` names it.
4. **One lock, held once:** `runner.exclusive_for(slug, operation)` records the holder in this
   process, and every `runner.exclusive(slug)` inside it (migrate, packs) takes nothing more.
5. **The steps after the database** run as separate invocations of the same operation:
   `ihr_hospital_restore` exits 4 for the Keycloak check (it needs the operator's bootstrap
   credential, which only the unlogged admin one-off holds: that step runs there with
   `--stop-after keycloak --unlogged`, the rest in the logged migrate one-off, which also writes the
   failure line of a step that failed there) and, only with `--restart`, 3 for a restart of the app
   containers. There is **no rolling restart** (review C5): with one backend container a restart
   takes every hospital down, and no process keeps state from a hospital's rows (its uuid lives in
   public, its settings and role caches in Redis, deleted); a schema a process gated for rights
   beyond the model stays gated there until a restart (`--restart`). `staging.sh` drives them.
6. **Files:** every object under the hospital's prefix in both buckets, not only the ones the
   restored rows name (keys are unique per upload, so it is the same set plus orphans). Listing
   versions needs the buckets' admin key on AWS (the app's keys have no ListBucket): typed by the
   operator for the run (`IHR_RESTORE_S3_ACCESS_KEY`), the stand-in's admin key in a proof.
7. **The erasure ledger** (P4-T1, `public.ihr_privacy_erasure`): its mere existence refuses every
   restore until P4-T1 teaches the restore to replay erasures after the restore point.
8. **Disk:** `staging.sh` refuses below 12 × the dump plus 1 GiB free; the command below 2 × the
   package plus 512 MiB (`--free-bytes`).
9. **Locks:** the precheck estimates 4 per relation and warns past the lock table (a load that runs
   out fails cleanly); measured 3,794 for a 1,101-relation schema.
10. **The drill:** a 30-day rotation picks the hospital; the restore runs in a backend-image one-off
    with `ihr_platform.settings.drill` (it refuses to start if any live setting reaches it; local
    cache `DrillCache`), on a Docker network holding only the copy; "unchanged" is a hash of each
    other schema's rows (count and sum of row hashes per table), triggers, function bodies and
    settings, grants and sequence values, and of every public row not the drilled hospital's,
    before and after (not a byte comparison of files; since the second verification the other
    hospitals' rows by their tables' write counters: item 15). The nightly schema-only check of
    every dump was built in P1-T22a.
11. **Strict merge, concretely:** users inactive, deleted or gone in the parked copy stay off; a
    staff link changed or removed since the dump switches its user off; `is_superuser` and `is_staff`
    never come back; assignments in `emr_organizationuser`, `emr_facilityorganizationuser`,
    `security_roleassociation` and `emr_patientuser` (a direct patient grant: review S4) absent or
    deleted in the parked copy are soft-deleted, and one whose role grants permissions the parked
    copy's role does not is set back to that role; patient shares (`emr_patientorganization`)
    absent or deleted in the parked copy are soft-deleted; role permissions not in the parked copy
    are soft-deleted; `cached_role_orgs` cleared for changed users, and the affected patients'
    `users_cache` and `organization_cache` built again by CARE's own save after migrate. It runs in
    the load's transaction, after the sequences are advanced and before migrate, roles or packs
    write anything (review C3: the two copies' ids still name the same rows). `--keep-restored-access
    "why"` skips the assignment, grant, share, role and flag merge (recorded); the user states,
    links and sessions are never skipped. Requests made after the dump that take access away (a
    sign-in reset, an invitation's withdrawal) for a user the restored copy has stay queued and are
    named (review S8).
12. **Parked schemas** are dropped by the owner's daily `hospital-restore-expire` (the app may not
    drop schemas); the worker's per-minute report names one past 8 days (ParkedSchemaOverdue).
13. **Package storage:** a Docker volume per restore (`ihr-restore-<slug>`), never the host's disk;
    kept while a restore is unfinished (for `--resume`), removed after it ends or is aborted.

14. **After the review of P1-T22b (14-11-2026)**, besides the above:
    - **S1:** the quarantine walks every stored expression's node tree (`package.expression_problems`:
      only immutable built-ins or extension functions and `nextval`, `now`, `clock_timestamp`,
      `statement_timestamp`, `transaction_timestamp`, `gen_random_uuid`, `pg_current_xact_id`; only
      this schema's relations), and the load runs the same check in the live cluster before it can
      evaluate any of them; the precheck refuses a package whose shape is not the live schema's when
      their migrations are the same; the trail's column defaults are set from code
      (`ihr_audit.install.ensure_defaults`).
    - **S2:** forms matched whole after a small lexer refuses a second statement or a comment;
      the extended protocol; the quarantine stops its server, re-checks the files and prints
      package.json's hash, which `staging.sh` passes as `--package-sha256` (recorded, checked again
      on a resume before the load).
    - **S5:** the final seal is a temporary function built from the sealing function's own SQL
      (`seal.final_sql`), up to the snapshot's xmax and without the sealer's lock; an entry left
      unsealed refuses and rolls the park back.
    - **S6/C3:** sequences advanced right after the load; the reconcile pass is a re-check.
    - **S7:** the restored chain is recomputed, and the dump's entries past its last seal compared
      with the parked ones, before the commit (still abortable).
    - **C1:** every step after migrate first checks the schema is this build's and migrates it again
      when it is behind (ahead: a stop); `release` and `rollback` refuse while a restore is open.
    - **C2:** the lock is tried before an operation exists (busy: refused, nothing recorded); a lock
      failure on resume is not recorded as a step, and abort ignores names that are not steps.
    - **C4:** every Celery task and staff-worker job holds its hospital's job lock (shared) for its
      whole run (`release.job`); the drain takes it once, exclusively, after the hold.
    - **C6:** a resume after the load never reads the package. **C7:** the drill's hash also covers
      every other schema's shape and public's functions, triggers and shape (on a live restore the
      same catalogue-only hash is compared inside the load's transaction). **C9:** a step that failed
      in the unlogged one-off is reported by the logged one (`--report-failure`, every `--resume`).
    - **S9:** free disk checked from the manifest before anything is fetched; the decrypted dump
      removed by an EXIT trap whatever stops the run; the drill's owner address passed in the
      command's environment, never a file.

15. **After the second verification (14-11-2026):**
    - **S2 (residual):** a constraint statement is matched as ONE constraint of its kind
      (`package._CONSTRAINT_FORMS`: its text with every top-level parenthesis emptied must be
      the form pg_get_constraintdef writes, a key's or a foreign key's parentheses hold column
      names only, a foreign key's schema is this one). ALTER TABLE takes several subcommands
      separated by commas, and one after a key (`, ALTER COLUMN x TYPE text USING (...)`,
      `, OWNER TO ...`, `, DISABLE TRIGGER ALL`) would have run as the owner after the rows and
      the inspection, a USING once per row and never stored. Refused before anything runs.
    - **S1 (residual):** nextval only of a regclass constant (a sequence named at run time,
      `('h_x.s'::name)::regclass` or `(oid)::regclass`, leaves no pg_depend row, so the
      reference check could not see another hospital's); the operators of exclusion constraints
      and foreign keys go through the same function check; an index's operator class must be a
      built-in or an extension's, and an index that names one is built on the empty table and
      inspected before the rows (it is no longer "plain").
    - **S7:** two forged dumps restored end to end: sealed entries that no longer verify, and a
      dump with no seal whose entries are not the live trail's own; both stop in the restore
      step, before the commit, and abort cleanly (mutation `restore-chain-recomputed`).
    - **C4 (jobs' side):** tests that a Celery task, an invitation job and an access-request job
      each hold their hospital's job lock while their body runs (mutations
      `restore-job-lock-celery`, `restore-job-lock-staff-worker`). A job never waits for the
      lock: `release.job` tries it, and while the drain holds it or waits for it (Postgres
      queues a new shared request behind a waiting exclusive one) the job is deferred at once,
      as for a held hospital, so no worker slot (they serve every hospital) waits out the drain
      (mutation `restore-job-lock-no-wait`).
    - **C8:** the "only the parked schema" state (a park whose load never happened) is resumed
      in the resume test; the trail's defaults from code are tested through the restore's own
      `_install_trail` (mutation `restore-trail-defaults`).
    - **The live "others unchanged" compare** is now `package.catalogue_versions`: per schema,
      the number and versions (key and xmin) of every catalogue row of its objects, set-based
      and without deparsing (any DDL writes a new row version; autovacuum and ANALYZE write in
      place, checked). Two reads inside the load's transaction cost the same at any fleet size.
      A hospital schema that appeared meanwhile (onboarding) and one held by another restore are
      left out, so neither stops this restore; a schema that changed or went away otherwise is
      named.
    - **An older build's dump:** the precheck refuses one whose tables lack a column the strict
      merge needs (`engine.MERGE_COLUMNS`), before anything is done, instead of failing half-way
      through the restore step; a flag or a cache column its users table did not have yet
      (`is_superuser`, `is_staff`, `cached_role_orgs`) is skipped and named.
    - **C7 (the drill's cost):** the drill no longer reads every other hospital's rows twice.
      On the drill copy nothing else runs, so each of their tables' write counters (rows
      inserted, updated, deleted, from the statistics, which also count a rolled-back write)
      and its relfilenode (a TRUNCATE) stand for its rows, with the catalogue and sequence
      values as before (`package.fingerprint(rows="writes")`, after
      `package.flush_write_counters`); public's rows are still hashed (all but the drilled
      hospital's). Where the server does not count writes (`track_counts` off) every row is read,
      as before.

### Numbers (proof-b.md)

- Restore of a 1,101-relation hospital with 200,000 audit entries (37.8 MB package): **42 s** end to
  end (fetch 2 s, quarantine 4 s, park and load 1.7 s, migrate 1.6 s, audit 1.5 s, the restart and
  one-off containers the rest); `--dry-run` 11 s.
- Monthly drill: 22 s (the copy from the backups 10 s). `--from-pitr` dry run: 19 s.
- Locks: 3,794 in the load transaction (default lock table 6,400). P1-T23: `max_locks_per_transaction
  = 256`.
- Tests (final code): plugin suites as the app role `Ran 719 tests ... OK (skipped=45)`, roles off
  `Ran 685 tests ... OK (skipped=49)`; dumper image `Ran 91 ... OK (skipped=7)` and quarantine
  `Ran 11 ... OK (skipped=4)` (`scripts/test-dumps-live.sh`); `scripts/test_dumps.py` +
  `test_release_scripts.py` `Ran 168 ... OK (skipped=7)`; `check-logs.py` "Log pipeline OK"; the four
  restore mutation rows each turn their test red (run locally: FAILED (failures=1) each).

### Known limits

- **`ihr_audit_verify` after a restore needs the owner or the watch's role:** the app role has no
  right on the archive (its entries hold values of every hospital), so run as the app a restore
  event reads as unverifiable and the superseded claims as a rewrite. The release's pre-check
  (`rel_audit_check`, the running backend) therefore warns after a restore (it never stops a
  release); `staging.sh manage ihr_audit_verify --all` with the reader key, or the audit watch, is
  the check. A SECURITY DEFINER function returning only hashes would let the app verify too (open).
- **The archive holds personal data in `public`** (the superseded entries' values), owner-only and
  append-only. Offboarding and DPDP erasure must cover it (TENANCY §7.7 amendment; P4-T1); not built.
- **Queued Celery tasks** of the hospital from before the restore run after it (the gate holds them
  while it is held); none is purged. A task or staff-worker job still running when the drain's wait
  ends stops the restore (held; `--resume`), it is never killed.
- **The live "others unchanged" compare sees DDL, not rows.** Inside the load's transaction the
  other hospitals go on writing, so only their catalogues are compared (by row version). A write
  of rows into another hospital by the load is ruled out by the statement forms and the
  expression inspection, not by this compare; the drill, where nothing else runs, compares rows
  too.
- **A fleet-wide DDL during a restore stops it:** `ihr_fleet_run migrate`, `db-roles --assert` or
  anything else that changes another hospital's or public's catalogue while the load runs is named
  by the compare and rolls the load back (the hospital stays held; `--resume` once it is done).
  Onboarding and another hospital's restore do not.
- **The drill's "other hospitals unchanged" trusts the statistics' write counters** for their
  rows: a statistics reset between its before and after (`pg_stat_reset`, which the drill's owner
  role may not run) would hide a write; a TRUNCATE shows by relfilenode, DDL by the catalogue.
  Tested in the suite (`test_the_drill_stands_for_other_hospitals_rows_by_their_write_counters`),
  not yet in a drill on the staging copy (the second fixer did not repeat the local staging
  proof).
- **An older build's dump without a column the strict merge needs is refused**, not merged
  (`engine.MERGE_COLUMNS`: ids, `deleted`, `user_id`, `role_id`, `patient_id`, `permission_id`,
  `is_active`, `keycloak_sub`; all long-standing in CARE). Restoring one would need this build's
  migrations applied before the merge, which the merge's order (before migrate, review C3)
  rules out.
- **The package's shape is compared with a reference only when the dump's migrations are the
  live schema's.** An older dump has no reference here (building one would need this build's
  migrations up to the dump's state in a scratch schema): its expressions are still inspected,
  its trail defaults set from code, and migrate brings it to this build.
- **Keycloak members added after the dump** are named, not removed; the operator decides.
- **Role definitions** are merged by permission only (a custom role's name or contexts come back as
  they were at the dump).
- **The parked schema** is readable by `ihr_dump` (`pg_read_all_data`; read-only, and the dumper never
  dumps it) as well as the owner.
- **A dump of another cluster** (a move) is refused by the transaction-id rule (rebase seal: not
  built). A restore after a whole-cluster point-in-time restore is not covered.
- **The restore event is written once per generation only when the reader key can see the bucket**
  (a re-run checks first); with only the writer key a crash between the write and its record could
  write a second object of the same generation (reported by the watch as a double).
- **HospitalNotServed fires during a restore** (the hospital is down, on purpose).
- **Not proven:** the quarterly live drill, alert delivery (rules tested by `check-logs.py`), E2E's
  bucket versioning, a busy hospital at 3 years (P1-T23).
