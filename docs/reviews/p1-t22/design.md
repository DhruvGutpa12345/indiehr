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
     and the separate key limit what one leak can do; per-hospital keys come with P4-T4.
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
