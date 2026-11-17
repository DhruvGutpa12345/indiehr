# P1-T22c design: audit restore events for a whole-database restore (draft for attack, 2026-11-17)

Designer: Claude (Opus 5.5, high). Task row: docs/TRACKER.md, P1-T22c. Builds on ADR-010 (P1-T21
and P1-T22b amendments), docs/reviews/p1-t22/design.md and docs/reviews/p1-t21/design.md.
Decisions below are Q-K83…Q-K91 (auto-accepted in the unattended run, reversible). The design was
attacked on 2026-11-17 (security and feasibility lenses); the outcome is in "After the attack"
before §7, and where it differs from §1-§6, the "After the attack" text wins.

## 1. The problem

1. A whole-database restore (`staging.sh rollback --restore`, `staging.sh restore --target`) puts
   every hospital schema **and** `public` back to one moment T. The fingerprint bucket is not
   rolled back: its claims for seals sealed after T stay.
2. Verify and the watch then see those claims as tampering: `anchor.py:345` ("is missing (the
   trail was cut)") and `anchor.py:350` ("rewritten", once new seals re-use the numbers);
   `incremental.py:597` / `:603` (the watch's checkpoint is past the restored head);
   `incremental.py:527` / `:532` (a reseed from the newest fingerprint).
3. No restore event exists, and none could verify: `public.ihr_restore_*` is rolled back too, so
   even P1-T22b's own segments newer than T vanish and their events fail at `events.py:309`
   ("has no superseded segment… excuses nothing").
4. Hospitals created after T are gone from the database: `anchor.unknown_hospitals`
   (`anchor.py:534`) reports "fingerprinted but no longer in the database".
5. The rollback says so itself (`scripts/staging.sh:2192-2195`) and the runbook
   (`deploy/staging/README.md:1150-1156`) tells the operator to record an incident.

### Facts checked in the code (and two that are not as briefed)

- True: events.py format and excuse rule (`events.py:279-293`, `:295-334`); archive.py tables,
  append-only, app has no right (`archive.py:1-20`, migration 0012); checkpoints keyed by uuid
  and generation (`incremental.py:363-372`); the release holds, drains, seals and fingerprints
  before its restore point (`staging.sh:1909`, `:1936`, `:1940`, `:1955`).
- **Defect found:** the next restore generation is read from the database only
  (`archive.py:33-38`, used at `restore/engine.py:1581-1583`). After a whole-database restore the
  database has fewer generations than the bucket. A later single-hospital restore takes a
  generation the bucket already has, and `_write_event` (`engine.py:1715-1716`) then returns the
  **old** event's key: the new restore gets no event and reads as a cut. P1-T22c fixes this.
- **Fact the brief left out:** `rollback --restore` ends on the **older** build
  (`staging.sh:2220-2225`). That build's verify and watch must accept the cut. So the fix must
  work through what P1-T22b builds already read: the `ihr_restore_*` tables and
  `ihr-audit-event/1` restore events. New object kinds must stay invisible to older readers.
- Postgres continues from T's transaction counter on the new timeline, so new txids repeat old
  ones. The archive is keyed by (hospital, generation), so this is harmless there; P1-T22b's
  precheck already refuses a dump from the lost timeline (`engine.py:554-560`).

## 2. Options

**A. Export before, reload after (chosen).** While the old database (or a copy of it) is still
readable, export each hospital's tail since T as an encrypted package. Commit its hashes to the
fingerprint bucket as a *plan* before the destructive step. After the restore, load each
segment into the existing `ihr_restore_*` tables, re-load the P1-T22b generations newer than T,
and write ordinary `ihr-audit-event/1` restore events.
- For: any build since P1-T22b verifies it unchanged (rollback-safe); no migration; the
  superseded entries stay evidence inside the database and its backups; the event's excuse rule
  is not weakened.
- Against: needs a readable old database or the WAL repository; an extra owner step; disk and
  time for a large tail.

**B. Segments in the bucket only; verify reads them there.** No reload; verify and the watch
fetch segments from object storage.
- For: survives a lost database without a reload step.
- Against: entries hold personal data, so the watch and verify would need the decryption key;
  older builds (the rollback target) cannot read them, so a rollback still alerts; a second code
  path for the excuse rule. Rejected.

**C. Events without segments ("trust the operator").** Excuse every claim above the restored head
on the operator's word.
- For: trivial; works when nothing survives.
- Against: a database owner with the writer key could hide any rewrite newer than a chosen point;
  evidence is lost. Rejected as an excuse. Kept only as a **loss record** that never excuses (§3c,
  Q-K87).

## 3. Flows

Names: T = the restore target; **plan** and **result** = two write-once objects per whole-database
restore (`anchors/ihr-restores/<suffix>/…`, §5); **package** = one hospital's encrypted export.

**Lower bound L per hospital** (where its export starts): the smaller of (1) the seal claimed by its
newest fingerprint stored at or before T − 10 min (`anchor.CLOCK_SKEW`), and (2) `restored_to.seq`
of each of its restore events stored after that fingerprint. No such fingerprint: L = 0 (genesis).
Read from the bucket, never from `sealed_at` (owner-controlled). This makes L at or below the point
where the chain at T and the live chain part, even across a P1-T22b restore in between.

**Package** (format `ihr-superseded/1`, sealed with `deploy/lib/sealer.py`, purpose
`ihr-superseded`): header (hospital uuid, slug, restore id, L, head seq and hash, counts); seals
L…head; entries of seals L+1…head; unsealed entries at the end (kept, not loaded; §7 limits); and
every `ihr_restore_*` row of generations whose bucket event was stored after T − 10 min.

### (a) Planned `rollback --restore` (old database alive)

1. As today up to the confirmation (plan, images, disk, typed tag).
2. **New:** hold every hospital (`fleet hold`), stop the workers, drain, then `rel_seal` with the
   running build (seal and fingerprint the whole tail).
3. **New:** `audit-supersede export --for-point <rp> --source live`: a one-off of the running
   backend image, as `ihr_dump` (read-only). Per hospital: L, package, sha256. Packages go to
   `$DIR/supersede/<id>/` (mode 600) and the dumps bucket (Q-K84). Then the **plan** is written to
   the fingerprint bucket: restore id (uuid4), T, source, old timeline, and per hospital L, head
   (seq, hash), `segment_digest` of seals L+1…head, entry count, package sha256. The image id used
   is recorded in the plan file, so the load runs the same tool.
4. `compose down`, the copy aside (as now), `restore_live`.
5. **New:** start `db` alone. `audit-supersede load <id>` (same image, as `ihr_owner`, raw SQL,
   `--skip-checks`; §5), before `up`, so the watch first sees the restored database with its
   events already there.
6. `up`, fleet release/roles/window, smoke, full backup (as now). The rollback's releases.log line
   gains `supersede=<id>`.

**Load, per hospital, each step idempotent:**
1. Package sha256 equals the plan's; the plan is in the bucket and stored before the load.
2. Re-load the P1-T22b generations the restore removed (same generation, same rows). A
   generation already present is compared row by row, never written again.
3. Find the **fork point** F: the newest seq ≥ L where the restored seal equals the package's.
   Seal L must match; if it does not, the hospital is **refused** (the restored chain is not an
   ancestor of the exported one) and named. Normally F is the restored head; it is lower only
   after a P1-T22b restore from a dump older than T.
4. Nothing after F in the package: **unchanged** (no event).
5. Else: generation g = (the bucket's restore events for it) + 1, cross-checked with the
   database after step 2 (they must agree, else refused). Insert record, seals F+1…head and their
   entries (`operation` = restore id, `parked_schema` = `whole-database`, `dump` = T), then
   `events.check_restore` exactly as P1-T22b does (`engine.py:1604-1626`).
6. Write the restore event (`restored_to` = F, superseded F+1…head; extra fields `cause:
   "whole-database"`, `restore_id`), skipped if generation g with this digest is already there.
7. Add one `restore` entry to the hospital's trail (`data.restore_id`, generation, F, range, T,
   reason), skipped if present. Beat seals it; the hourly job fingerprints it.
8. After every hospital: write the **result** (restore id, new timeline, per hospital outcome,
   generation, event key, F). A run that stops before it is resumed by running `load` again.

### (b) Disaster point in time (old database gone or unreadable)

- `audit-supersede export --for "<T>" --source repository`: a scratch copy restored from the
  pgBackRest repository to its **newest** moment (as `restore-drill`: own volume, no network but
  an internal one to the one-off, removed after), exported as in (a), as `ihr_dump`.
- Seals sealed after the newest WAL the repository holds are truly lost: the load names them
  **lost** per hospital (claims above the package head). They stay a cut (Q-K87).
- Then `staging.sh restore --target T --supersede <id>`; `up` waits for `load` (it runs between
  `restore_live` and `up`, as in (a)). Unsealed entries at the copy's end are kept only in the
  package (no seal ever named them).
- `restore` without `--supersede <id>` is refused unless `--no-supersede --reason "<incident>"`
  (Q-K88), which writes a plan and result with every hospital `lost`: the timeline change is
  explained, the cuts stay.

### (c) Some hospitals readable, some not

- Export is per hospital; one that fails (a lock, a damaged schema, a missing table) is `failed`
  in the plan file and retried with `--slug` or another `--source` (live, then a copy volume
  `--source volume:<name>`, then repository). The plan is written once all are `exported` or the
  operator passes `--accept-unreadable`, which writes them as `lost` in the plan.
- The load handles each hospital in its own transaction; a refused or lost one never blocks the
  others. The result names each outcome; the watch counts lost tails (§5).

### (d) A crash midway

- Export: packages are named by restore id and hospital; a re-run verifies an existing one by
  sha256 and skips it. The plan is written last (write-once: a second plan for the same id is
  refused unless byte-identical). Before the plan nothing has changed: restart the rollback.
- Between plan and restore: the plan is harmless alone (the watch ignores a plan whose timeline
  never changed, after 24 h it reports it once as `abandoned`).
- During the restore: the existing stage 3 message gains "then `audit-supersede load <id>`".
- Load: every step above is checked before it is done (record by (hospital, restore id), event by
  generation and digest, entry by `restore_id`). `rel_rollback_exit` stage 4 names the load. `up`
  warns while `$DIR/supersede/<id>/` has no result.

### (e) A second whole-database restore later

- Generations come from the bucket, so they never repeat. The second export re-exports the first
  restore's archive rows that are newer than its T (they are in its package and reloaded).
- The chain of events per hospital is g, g+1, …; the watch rebases its checkpoint through each
  (`incremental.py:472`) unchanged. A fork point works the same way.
- The engine's `next_generation` reads the bucket too (defect above).

## 4. How verify and the watch accept the cut, and why the owner cannot abuse it

**Accepting (unchanged rule, every build since P1-T22b):** a claim the database no longer matches
is excused only when a restore event of that hospital, stored after the claim, covers its seq, and
its archived segment verifies: links from `restored_to`, every seal hash recomputed, ends at
`head_hash`, its `digest`, and its entries recomputed (`events.check_restore`).

**Added in P1-T22c builds (defence in depth; older builds skip it):**
- An event with `cause: whole-database` must have a plan stored before it and a result naming it;
  its `head_hash` must equal the plan's head for that hospital, and `restored_to.seq` ≥ the plan's
  L. Else it gets a problem and excuses nothing.
- **Timeline corroboration (Q-K89).** The watch records the database's timeline each pass in its
  own volume (`substr(pg_walfile_name(pg_current_wal_lsn()), 1, 8)`; the grant is checked at
  build). A change with no result naming the new timeline and a plan naming the old one: metric
  `ihr_audit_timeline_unexplained` = 1, alert AuditDatabaseRestoredUnannounced (critical). A
  whole-database event whose result's timeline the database does not have: excuses nothing.
  `ihr_audit_verify` (no state) checks the result's new timeline equals the current one.
- Absent hospitals (created after T): closed in `unknown_hospitals` only when the event says
  `absent`, it verifies, and the database has neither the uuid nor the slug (Q-K85).

**Who can write what:**

| Object | Written by | Key / role | Holds |
|---|---|---|---|
| Plan, result, events | owner-run `audit-supersede` | anchor writer (add-only; also held by Celery) | hashes, numbers, uuids, slugs, timeline |
| Packages | owner-run export | dumps bucket key + dump encryption key | personal data, encrypted |
| Archive rows, `restore` entry | owner-run load | `ihr_owner` | as P1-T22b |
| Reading all of it | watch, verify | reader key, `ihr_audit_watch` | — |

**Why the database owner cannot hide a rewrite with it:**
- The owner role holds no bucket key: it cannot write a plan, result or event.
- An event excuses a claim only if the archive holds the **original** seal with that hash and its
  entries hash to it: forging that needs a sha256 preimage. So the original must be kept, in an
  append-only table; deleting it (the owner can drop the trigger) makes the event excuse nothing
  again, so it fails closed.
- With the writer key too (a stolen Celery key), a fake "restore" still needs the original kept
  and is announced: one AuditEventRecorded per hospital plus one AuditWholeRestoreRecorded line,
  and a timeline change only a real restore (host access) produces.
- The plan is committed before the restore, so the load cannot swap packages afterwards.
- Unchanged limit: the last hour not yet fingerprinted can still be rewritten by the owner.

**What a human confirms:** the typed tag (rollback) or target (restore); `audit-supersede status
<id>` after the load (per hospital: F, range, generation, outcome); the AuditWholeRestoreRecorded
mail names the restore id, which must match releases.log or the incident record; every
AuditEventRecorded mail. A whole-database restore is still recorded as an incident (CERT-In 6 h
when it follows an attack).

## 5. Changes

**New**
- `plugins/ihr_platform/ihr_audit/supersede.py`: lower bound, package build/read (sealer), plan
  and result documents (parse like `events.parse`: every field checked, key names fixed), fork
  point, per-hospital load steps, `status`. Raw SQL only (no ORM), so it runs against an older
  schema that has migration 0012.
- `plugins/ihr_platform/ihr_audit/management/commands/ihr_audit_supersede.py`:
  `export --for T | --for-point RP [--source live|volume:<v>|repository] [--slug S]
  [--accept-unreadable] --operator X`, `load <id> --operator X`, `status <id> [--json]`,
  `accept-loss <id> --slug S --reason "<incident>" --operator X` (Q-K87). Refuses a target schema
  without `ihr_restore_record` (Q-K86).
- `deploy/lib/sealer.py`: purpose `ihr-superseded` (new label only).
- Tests (§6) and `docs/reviews/p1-t22c/` proof files.

**Changed**
- `ihr_audit/events.py`: optional `cause`, `restore_id`, `absent` on restore events (written,
  parsed, checked); `restores_prefix()`; `read()` also reads plans and results and adds the
  whole-database checks of §4. Old objects parse as before.
- `ihr_audit/archive.py`: `load_segment(cursor, …)` (from a package, same columns);
  `next_generation(cursor, uuid, bucket_events)` = max(database, bucket) + 1, refused when the
  database is ahead.
- `ihr_platform/restore/engine.py` `_audit`: the generation from the bucket too (defect fix);
  refuses while a whole-database load is unfinished.
- `ihr_audit/anchor.py` `unknown_hospitals`: `closed` may include absent-by-restore uuids.
- `ihr_audit/watch.py`: timeline record and check; plan/result reading; lines and metrics below.
- `ihr_audit/management/commands/ihr_audit_verify.py`: reports `excused` per restore id and
  the whole-database checks.
- `ihr_platform/db_roles.py`: processes `supersede-export` (ihr_dump) and `supersede-load`
  (ihr_owner) for `ihr_db_role_check --for`; the watch's timeline function grant if needed.
- `scripts/staging.sh`: `audit-supersede export|load|status|accept-loss` (one-offs with the right
  secrets); `rollback --restore` gains hold, drain, seal, export, db-only load (flow a);
  `restore` gains `--supersede <id>` / `--no-supersede --reason` (flow b); `rel_rollback_exit`
  stage 3/4 texts; the warning at 2191-2195 rewritten; `up` warns on a pending load.
- `scripts/release_log.py`: rollback field `supersede` (uuid or `none`); tests.
- `deploy/dumps/expire.py`: `superseded/` prefix expires with the dumps (Q-K90).
- No migration: the P1-T22b tables carry it.

**Log lines**
- Tool (owner-run, journal and log store):
  `ihr-supersede: hospital=<slug> outcome=<exported|unchanged|loaded|absent|lost|refused> [step=<name>]`
  and `ihr-supersede: restore=<id8> outcome=<planned|loaded|incomplete>`.
- Watch (unchanged): `ihr-audit: hospital=<slug> event=restore outcome=recorded` per event.
- Watch (new, once per result):
  `ihr-audit: restore=<id8> event=whole-database hospitals=<n> outcome=recorded`.

**Alerts**
- `deploy/logs/alerts/audit.yml`: AuditWholeRestoreRecorded (warning; the new watch line only,
  from the audit-watch service, whole line matched).
- `deploy/monitoring/prometheus/rules/audit.yml`: AuditDatabaseRestoredUnannounced
  (`ihr_audit_timeline_unexplained == 1`, critical), AuditLostTail
  (`ihr_audit_hospitals_lost_tail > 0`, warning, after an accepted loss; before it the hospital
  stays in AuditChainBroken). New metrics `ihr_audit_whole_restores`,
  `ihr_audit_timeline_unexplained`, `ihr_audit_hospitals_lost_tail`. Rule tests in
  `deploy/monitoring/prometheus/tests/audit.yml`, log cases in `deploy/logs/tests/`.

**Runbook (`deploy/staging/README.md`)**
- "Audit watch": replace the "until P1-T22c" paragraph with: what the rollback now does; the
  three new alerts; how to confirm a whole-database restore; what `lost` means.
- "Releases": rollback steps and stage messages; time and disk for the export.
- Backups section: `restore --target` with `--supersede` (export from the repository first);
  `--no-supersede`.
- ADR-010: amendment "P1-T22c: the chain across a whole-database restore".

## 6. Tests

**Unit and tenant tests** (`ihr_audit.tests.test_supersede`; add to the tenant labels):
- L from fingerprints and from restore events between F and T; L = 0 without fingerprints.
- Fork point: normal (F = restored head); after a P1-T22b restore older than T (F lower);
  not an ancestor (refused).
- Package: round trip; one changed byte refused; a package not the plan's refused.
- Load idempotent: run twice, then crash after each step (record, event, entry), same end state,
  one event per generation.
- Generations from the bucket: a bucket ahead of the database (the defect) gives the right g;
  database ahead is refused.
- End to end (simulated restore: two hospitals; state at T copied into a schema, like
  P1-T22b's park, and the post-T archive rows removed as the owner): before the load verify fails
  (cut and rewritten); after it `ihr_audit_verify --all` passes with excused claims; the watch's
  incremental pass rebases and is clean; an absent hospital is closed; a deleted archive row
  makes it fail again.
- Compatibility: an event with the new fields read by the P1-T22b parser shape (a fixture of
  the old `parse`): accepted, same excuse.
- Watch: timeline change with no result → unexplained = 1; with plan and result → 0; an event
  of a result whose timeline the database lacks excuses nothing; the new line is emitted once.
- `scripts/test_release_scripts.py`: the `supersede` field; rollback stage texts.

**Mutation rows** (`scripts/isolation-mutation.sh`; texts fixed at build, `check` keeps them):
```
supersede-ancestor|…/ihr_audit/supersede.py|if db_hash != pkg_hash:|if False:|ihr_audit.tests.test_supersede.Load.test_not_an_ancestor_is_refused||
supersede-plan-sha|…/ihr_audit/supersede.py|if sha != planned["sha256"]:|if False:|ihr_audit.tests.test_supersede.Load.test_a_package_not_the_plans_is_refused||
supersede-generation|…/ihr_audit/archive.py|max(in_db, in_bucket) + 1|in_db + 1|ihr_audit.tests.test_supersede.Generations.test_bucket_ahead_of_database||
supersede-timeline|…/ihr_audit/events.py|if result.timeline != current_timeline:|if False:|ihr_audit.tests.test_supersede.Watch.test_event_without_its_timeline_excuses_nothing||
supersede-absent|…/ihr_audit/anchor.py|and uuid not in hospitals and slug not in by_slug|and True|ihr_audit.tests.test_supersede.Absent.test_absent_closed_only_when_slug_is_free||
```
Each proves that test really guards that rule.

**Live proof** (`scripts/test-supersede-live.sh`, the local staging proof with a local
repository, about 40 min, dev stack stopped):
1. Two seeded hospitals; release (restore point); activity; a P1-T22b restore of one hospital
   after the point (generation 1); a third hospital onboarded after the point.
2. `rollback --restore`: verify `--all` as the watch's role passes; watch lines (two restore
   events, one whole-database line); hospital three closed as absent; restored generations 1
   and 2 for the restored one.
3. `restore --target` a second time (flow e) with `--source repository` after removing the
   volume (flow b): passes; claims after the repository's end are `lost`.
4. Negatives: a changed package byte (refused); an archive row deleted (AuditChainBroken back);
   a plan and events written without a restore (excuse refused, timeline unchanged).
Proves the whole flow on real Postgres timelines, pgBackRest and the locked buckets.

## After the attack (2026-11-17)

Two attackers (security S1-S6, feasibility F1-F9) read this design against the code. All fifteen
findings are accepted. Each item says what changes; §3-§6 are to be read with these applied.

**Security lens**

1. **S1 (high), events in an older shape.** Plans, results and restore events get their own
   add-only key, scoped to `anchors/ihr-events/` and `anchors/ihr-restores/` (Q-K91). Celery's
   anchor key is limited to `anchors/<suffix>/` hourly fingerprints. The new key is made on the
   owner's deploying machine and kept in the owner's password manager; servers hold it only inside
   the owner-run restore one-off. Until it exists, the §4 checks are documented as defence in
   depth, not a security boundary. The "who can write what" table changes to match.
2. **S2 (medium), an old timeline named again.** A result must have plan.old_timeline ≠
   result.new_timeline, the result must be stored after its plan, and the watch's own timeline
   history must show the change between their stored times. Results form a chain (item F4).
3. **S3 (medium), the operator lowers the fork point.** Flow (a): L comes from the release's
   fingerprint of the sealed head, recorded in the plan, and F must equal L. Flow (b): an F below
   the restored head is refused unless a P1-T22b event in the bucket names that F. The "T − 10 min"
   rule is dropped for flow (a). The L..T window stays a known limit (§8).
4. **S4 (medium), declared loss turns tampering into a warning.** A claim is "lost" only if it was
   stored after the source's provable end (newest archived WAL from pgBackRest info, or the
   copy's end), and that end is recorded in the plan. Never "lost" for a hospital any source
   exported. `accept-loss` never clears a mismatch at or below the restored head.
5. **S5 (medium), closing a live hospital as `absent`.** `absent` is accepted only when no
   fingerprint of that uuid was stored before plan.T − skew, the plan lists it absent with L = 0,
   and the S2 timeline checks pass.
6. **S6 (low), evidence lost early, personal data kept long.** Packages holding unsealed-only, lost
   or refused content are kept as long as the archive (Q-K90). Local copies under
   `$DIR/supersede/<id>/` are deleted once the result and the bucket copy verify.

**Feasibility lens**

7. **F1 (high), a half-done rollback cannot be re-run.** Hold and export get their own rollback
   stage (`REL_RB_STAGE`) and message. The hold reason is `rollback`, which the rollback precheck
   accepts; `fleet release` undoes it.
8. **F2 (medium), the leaving build is usually the broken one.** Hold, drain and seal are best
   effort, as `rel_seal` already is. Export and load run from the newest local image that has the
   command; the plan records which image.
9. **F3 (high), the disaster restore waits on two full restores.** Flow (b): restore to T and
   serve first, then export from the repository copy and load. Security comes from the preimage
   rule (§4), not from writing the plan first. `restore_drill`'s disk check reads the live
   database, which a disaster has lost: the export checks disk on its own volume.
10. **F4 (high), earlier restores stop excusing after a second one.** The "result's new timeline
    equals the current one" test applies only to the newest result. Each plan's old timeline equals
    the previous result's new timeline, and older events are accepted through that chain. Mutation
    row `supersede-timeline` and live-proof step 3 follow this.
11. **F5 (medium), other timeline changes alert falsely.** `audit-supersede announce-timeline
    --reason "<why>"` writes a plan and result with no hospitals. It becomes a step in the
    pg_upgrade and server-move runbook text (Q-K89).
12. **F6 (medium), "older builds verify it unchanged" is wrong for hospitals made after T.**
    Builds before P1-T22c close only offboarded hospitals in `unknown_hospitals`, not `absent`
    ones, so those hospitals keep alerting on such a target. A rollback to a pre-P1-T22c target
    lists them and needs `--accept-absent`. The live proof uses a real image built from the
    P1-T22b commit (about 1.5 GB of stack, as P1-T22b's proof).
13. **F7 (low), the generation defect stays on the rollback target.** `--accept-unreadable` is
    refused for a pre-P1-T22c target, since its engine reads generations from the database only.
14. **F8 (low), dumps are optional (`IHR_DUMPS`).** Without dumps, a whole-database restore that
    would export is refused unless `--local-packages-only --reason`; the plan records the choice
    (Q-K84).
15. **F9 (low), over-built.** `--source volume:<name>` is dropped; sources are `live` and
    `repository`. `up` warns (never waits) while a supersede has no result. This replaces "`up`
    waits for `load`" in flow (b).

**Refuted or dropped by the attackers:** plan overwrite or replay (the bucket put is write-once and
a result names its own event keys); package forgery with the symmetric dump key (the plan fixes the
package sha256); pre-planted generations (only a refused restore, and the segment still binds);
`ihr_dump` reading the archive (it already has `pg_read_all_data`); per-hospital alert noise
(Alertmanager groups it).

**Open point (not reconciled).** Item 9 writes the plan after the restore in flow (b), but item 2
wants the watch's timeline history to show the change between the plan's and the result's stored
times, and §4 wants the plan stored before the load. For flow (b) the builder needs a rule. Proposed:
`restore --target` writes a stub plan (restore id, T, source `repository`, old timeline, no
hospitals) before the destructive step; the export writes the full per-hospital plan afterwards and
the result names both. Owner confirmation of the exact rule is not needed for P1-T22c1, which has
no flow (b). It must be settled before P1-T22c2 starts.

## 7. Decisions for the owner

- **Q-K83 Approach.** A: export before, reload into the archive, ordinary events (rec.); B: bucket
  only; C: events without segments. A is the only one the rollback's older build verifies, and
  keeps the evidence.
- **Q-K84 Where packages live.** (a) local only; (b) local plus the dumps bucket, encrypted with
  the dump key (rec.: survives a dead server mid-rollback; same data class as dumps; without
  dumps, refused unless `--local-packages-only --reason`; local copies deleted after the result
  verifies); (c) the fingerprint bucket, encrypted (no: 10-year retention of personal data).
- **Q-K85 Hospitals onboarded after T.** (a) closed by a verified `absent` event under the S5
  conditions, their slug stays reserved (re-onboarding gets a new slug) (rec.: keeps "slugs never
  reused"); (b) same slug allowed again with an owner override written to the bucket; (c) alert
  until offboarded. A pre-P1-T22c target lists them and needs `--accept-absent`.
- **Q-K86 Rollback to a build before P1-T22b** (no archive tables). (a) refused, fix forward
  (rec.); (b) allowed, the cuts stay. A pre-P1-T22c target also refuses `--accept-unreadable`.
- **Q-K87 Lost tails** (no readable copy). (a) stay critical until silenced per hospital; (b)
  `accept-loss` with an incident reference moves the hospital to AuditLostTail, a permanent
  warning that never excuses or hides the claims (rec.). "Lost" only for claims after the source's
  provable end, never for a hospital any source exported, never at or below the restored head.
- **Q-K88 `restore` without an export.** (a) refused unless `--no-supersede --reason` (rec.); (b)
  allowed, warning only. In a disaster the restore and `up` come first, then export and load.
- **Q-K89 Timeline corroboration.** (a) yes: an unexplained timeline change is critical; results
  form a chain (each plan's old timeline = the previous result's new one), only the newest result
  must equal the current timeline, plan old ≠ result new, result after plan; `announce-timeline`
  for pg_upgrade or a server move (rec.); (b) no, events alone. Revisit if a standby failover is
  added (it changes the timeline too).
- **Q-K90 Package retention.** (a) as the dumps, except packages holding unsealed-only, lost or
  refused content, kept as long as the archive (rec.: the archive tables and the backups keep the
  rest after the load); (b) 400 days like the log archive.
- **Q-K91 Key for plans, results and events** (new, from S1). (a) a separate add-only key scoped to
  `anchors/ihr-events/` and `anchors/ihr-restores/`, made on the owner's machine, in the password
  manager, never on servers except inside the owner-run restore one-off; Celery's key limited to
  `anchors/<suffix>/` (rec.); (b) one writer key as now (checks are then not a security boundary).

## 8. Size and split

About 2 task-equivalents (Opus builder, high; full review shape, audit). After the attack the work is
split into two sittings (owner decision of the unattended run, 2026-11-17):

- **P1-T22c1: planned rollback** (flows a, d, e). The generation fix (`archive.next_generation`
  reads the bucket); `supersede.py` with export `--source live`, load, status; plan and result
  objects; the result chain and timeline check (S2, F4); the separate events key wiring (S1,
  Q-K91); watch timeline check, new line, metric and the new alerts; rollback stages for hold and
  export (F1), best-effort hold/drain/seal and the recorded image (F2); pre-P1-T22c target rules
  (F6, F7); `up` warning (F9); unit tests, mutation rows, and a live proof of the rollback
  (steps 1, 2 and 4, with a real P1-T22b-commit image as the older target).
- **P1-T22c2: disaster and partial.** Flow (b) as restore-and-serve first, then export from the
  repository copy and load (F3, with the open point above settled); flow (c); absent hospitals
  (S5, Q-K85); `accept-loss`, AuditLostTail and the "lost" rule (S4); bucket packages, their
  expiry and retention (S6, Q-K90), `--local-packages-only` (F8); `announce-timeline` (F5);
  live proof step 3; runbook backups section, the upgrade/move runbook text and the ADR-010
  amendment.

### Known limits (by design)

- Seals after the last archived WAL, and unsealed entries at the old database's end, cannot be
  excused (the entries are kept in the package only).
- An unfinished P1-T22b restore at T is refused by the load for that hospital (named).
- The owner can still rewrite the last unfingerprinted hour (unchanged).
- Builds older than P1-T22c accept the events on the segment rule alone (no timeline check), and
  keep alerting for hospitals made after T (F6).
- Flow (a): the window between the release's fingerprint (L) and T is not covered by the bucket
  (S3); until the Q-K91 key exists the plan, result and timeline checks are not a security boundary.
- Other `public` data rolled back (Keycloak, erasure ledger) is outside this task.
