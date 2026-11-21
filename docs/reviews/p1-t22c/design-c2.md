# P1-T22c2 design: disaster restore events (draft, 2026-11-21)

Designer: Claude (Opus 5.5, high), unattended run. Builds on `design.md` ("After the attack" wins)
and `build-c1.md`. Q-K83…Q-K91 confirmed 2026-11-21; new: Q-K99…Q-K104 (§12). Not built yet.

Words: **T** = the restore target. **Stub plan** = the plan written before a disaster restore,
with no hospitals. **Export doc** = the per-hospital half of the plan, written after the restore.
**Copy** = a scratch database restored from the pgBackRest repository. **L**, **F** = a
hospital's lower bound and fork point (design.md §3). **Level** = `ihr_audit.supersede.LEVEL`
(c1 = 1, c2 = 2), a constant the shell reads from an image.

## 1. The stub-plan rule (the open point, settled)

1. `staging.sh restore --target T` writes the stub plan `anchors/ihr-restores/<suffix>/<id>/`
   `plan.json` **before** `restore_live` replaces anything. Fields as c1's plan
   (`events.plan_document`) plus `flow: "disaster"`, `source: "repository"|"none"`,
   `packages: "bucket"|"local-only"`, `hospitals: {}`; `target.name` = `time-<UTC compact>`.
2. `old_timeline` (the database is gone) = the first 8 hex digits of `archive[].max` in
   `pgbackrest info --output=json`. Assumption, checked in step 4: the old database was on it.
3. An owner one-off of the configured image writes it (`ihr_audit_supersede stub`, events key). It
   touches no database (`requires_system_checks = []`; a test runs it with no database reachable).
4. Right after `restore_live`, with `db` alone up, `restore` reads the new timeline
   (`events.current_timeline()`) and the parent named in the new timeline's history file
   (`pg_wal/<new>.history`, last line, first field). Parent ≠ stub's `old_timeline`: the restore
   still serves; the operator is told to `close` the id (§8); the cuts stay (fail closed). It
   also writes `$DIR/supersede/<id>/restored.json` (mode 600): each hospital's head (seq, hash)
   at T, read before `up`, so before any new seal exists. A cross-check, not a boundary.
5. Why this holds the c1 checks unchanged: the stub is stored before the restore, so before the
   watch first sees the new timeline (`events.corroborated`), before every event (`check_whole`:
   "plan stored before it") and before the result. The result names the stub as its `plan`, so
   `anchor_results` and `_read_restore_docs` (result after plan, new > old) work as in c1.
6. The export doc (`<id>/export.json`, write-once, events key) carries what c1's plan carried per
   hospital, plus `restored` heads, `source_end` (§2.4) and the package digests. `check_whole`
   takes a disaster event's hospital entry from it and also requires: export doc stored at or
   before the event, export doc's `old_timeline` = the stub's.

## 2. Flow (b) end to end: serve first, then export, load, events

**2.1 Order (operator view).**
1. `staging.sh restore --target T --operator X [--local-packages-only --reason R]`: refusals
   (§8, §6.4), stub plan, `restore_live`, step 4 above. With `--no-supersede --reason R` (Q-K88)
   it writes the result now (every hospital `lost`, §5), before `up`, and steps 3-4 never run.
2. `staging.sh up`, smoke, `backup-now full`. Hospitals are served from here (F3).
3. `staging.sh audit-supersede export <id>`: the copy (2.2), the export (2.3), the packages put
   into the dumps bucket (§6), the export doc. Then the copy and its volume go.
4. `staging.sh audit-supersede load <id>`: c1's load (`supersede.load`) with the flow-(b) fork
   rule (2.5), the lost ranges (§5), absent hospitals (§4). Then the result, the full check, the
   bucket check and the deletion of local copies (§6.5).

Between steps 2 and 4 every hospital sealed after T reads as cut (AuditChainBroken, critical;
expected, the runbook says so). The timeline change shows as **pending** (2.6).

**2.2 The copy.** `restore_drill` (`scripts/staging.sh:1250`) is split: its body becomes
`repo_copy <name> [--set S] [--target-timeline TL]`, used by `restore-drill` and by the export.
- Volume and containers `ihr-supersede-copy` (same `pg_container` limits: `--memory 768m`,
  `--cap-drop ALL`, user 70), `archive_mode=off`, `cluster_name=ihr-supersede-copy`. It never
  pushes into the repository.
- pgBackRest restore with `--type=default --target-timeline=<old> --set=<S>`, where S is the
  newest backup set whose `archive.stop` begins with the old timeline. Reason: after step 2 the
  repository has the new timeline and a newer backup on it; the default ("latest", newest set)
  would follow the new timeline and replay nothing of the lost tail.
- Recovery ends at the end of the old timeline's archived WAL and promotes locally (its new
  timeline is never archived). Roles: `db_roles_apply` as in the drill.
- The export one-off (`audit-supersede` service, `ihr_platform.settings.watch`, the watch's
  read-only role, logging `none`) gets `DATABASE_URL` pointing at the copy.
- Disk: the check reads the backup set's size from `pgbackrest info` (never the live database).
  Need = set size + 2 GB (WAL replay) + 1 GB (package work). Refused with the numbers if short.
- RAM: copy ≤ 768 MB, export one-off ≤ 256 MB (criterion: peak recorded in the proof with
  `docker stats`). On the 8 GB server the copy runs beside the serving stack, so the copy gets
  `--oom-score-adj 900` and the dumper is stopped for the export's duration (Q-K103). On this
  6.7 GB machine: dev stack stopped, staging proof stack plus the copy (c1's proof stack fit).

**2.3 The export from the copy.** `supersede.export` with `source="repository"`:
- T and `old_timeline` come from the stub plan, never from the copy (`current_timeline()` of the
  copy is its own local timeline). c1's stale-fingerprint refusal (E3, `export_one`) is flow (a)
  only: in flow (b) seals between L and T are expected.
- L per hospital = design.md §3's rule: the smaller of the newest fingerprint's claim stored at or
  before T − `anchor.CLOCK_SKEW`, and `restored_to.seq` of each P1-T22b restore event stored after
  that fingerprint. No claim: 0 (genesis).
- Streaming (large tails: T may be days back). Each package is gzip-streamed into a work volume
  (`${project}_supersede_work`, mode 700, wiped after) while its sha256 is computed, then added to
  the stdout tar, then deleted. `read_tar` and the load read members one at a time. Criterion:
  export and load memory stay under 256 MB for a 1 GB package (a test with a generated tail).
- Per hospital outcome in the export doc: `exported`, `absent` (§4) or `unreadable` (§3).

**2.4 The provable end.** Recorded in the export doc as `source_end`:
- `last_commit`: the copy's last completed transaction (its log line, parsed as `restore_drill`
  parses `as_of`). Missing: the export refuses (nothing may then be called lost).
- `wal`: the last old-timeline segment replayed ("redo done at"), and `backup_set`: S. Both are
  for the incident record only.

**2.5 The fork point in flow (b).** Computed by the load, never chosen by the operator:
- F = the highest seq such that every restored seal L…F equals the package's. Seal L must match,
  else `refused` (not an ancestor; as c1).
- F must equal the hospital's head in `restored.json`, unless a P1-T22b restore event (not
  whole-database) stored after T − skew names `restored_to.seq` = F (a single-hospital restore
  from a dump older than T, after T; S3). Else `refused`.
- The restored seal F+1, if any, must differ from the package's F+1 (c1's
  `restored_fork_problem`, kept).
- `check_whole` for a disaster event: L re-derived from the bucket (as `_lower_claim`, with the
  min rule); L ≤ `restored_to.seq` ≤ head; head, hash and digest = the export doc's. F = L stays
  the rule for flow (a).

**2.6 The watch between serve and load.** `watch._whole_restores`: a change matching a plan
with no result (not superseded, its old = `before`, stored before `after.first`) counts in a new
gauge `ihr_audit_whole_restores_pending` (AuditWholeRestorePending, warning), not as unexplained;
after 48 h it is unexplained (critical). A pending plan excuses no claim.

## 3. Flow (c): partial (per-hospital outcomes)

- The export handles each hospital in its own savepoint. A failure (lock, damaged schema, missing
  table) is named; with `--slug S` one hospital is retried into the same folder. The export doc is
  written once every hospital is `exported`/`absent`, or with `--accept-unreadable --reason R`,
  which writes the rest as `unreadable` (reason kept).
- Flow (a) gets the same flag on `rollback --restore` (target level 2 only: Q-K101).
- The load: one transaction per hospital (as c1). Result outcomes: `loaded`, `unchanged`,
  `absent`, `refused`, `unreadable`. Each `loaded` or `unreadable` entry may carry
  `lost: {from_seq, to_seq, claims}` (§5). One outcome never blocks another.
- `ihr-supersede:` lines gain `outcome=unreadable` and `outcome=lost`.

## 4. Absent hospitals (S5, Q-K85)

- Export: a hospital in the copy (or live, flow a) is `absent` when **no** fingerprint stored at
  or before T − skew names its uuid (all fingerprints, not only the newest: per-uuid first stored
  time from `anchor.load_all`). L = 0; the package holds its whole chain.
- Load: refused if the restored database has its uuid or its slug. Else its chain is archived as
  generation g (normally 1) with `restored_to` = (0, genesis), and an ordinary restore event is
  written with `cause`, `restore_id` and `absent: true`. No trail entry (no schema). Two
  `RetiredAddress` rows (`kind=slug`, and `kind=domain` `<slug>.<IHR_TENANT_DOMAIN_SUFFIX>`) are
  inserted by raw SQL, `ON CONFLICT DO NOTHING` (constraint `unique_retired_address`), so
  `OnboardingForm._address_problems` refuses the slug for good.
- Reader: `anchor.unknown_hospitals(…, closed)` gets the uuids of absent events that verify
  (segment rule), pass `check_whole`, whose uuid's first fingerprint is stored after plan T − skew
  (re-read, never taken from the doc), and whose uuid and slug the database lacks.
- Claims of that uuid above the archived head follow §5 (lost after the end).

## 5. Lost tails and accept-loss (S4, Q-K87)

- The load computes, per hospital, the claims that no event excuses and that lie above the
  package head (exported) or above the `restored.json` head (`unreadable`, or `--no-supersede`).
  A claim is **lost** only if stored after `source_end.last_commit` − skew and before the stub
  plan's stored time. For `--no-supersede`, `source_end` = T. Any other such claim is a
  **mismatch**: named in the result, never lost.
- A mismatch at or below the restored head is never lost (it is ordinary verify).
- `audit-supersede accept-loss <id> --slug S --reason R --operator X` (owner one-off, events key)
  writes `<id>/loss-<uuid>.json` (write-once): hospital, slug, range, claim count, reason
  (`events.CERTIFICATE_RE` charset), operator. Refused for a hospital with no lost range.
- Readers never trust the doc's range: `Events.lost(uuid, seq, stored)` re-checks the window,
  the head and the result. A covered mismatch is reported as `lost`, not as a chain problem.
  It is never excused and never hidden: `ihr_audit_verify` lists it in a LOST section (exit code:
  Q-K100). Gauge `ihr_audit_hospitals_lost_tail` (hospitals with ≥ 1 lost claim); AuditLostTail
  (warning, permanent by design). Before accept-loss the hospital stays in AuditChainBroken.

## 6. Packages in the dumps bucket (Q-K84, Q-K90, S6, F8)

**6.1 Sealing.** `deploy/lib/sealer.py` gains purpose `"ihr-superseded"` (label only; a dump
manifest can never pass as a package's). New `deploy/dumps/dumper.py` subcommands, run with
`dumps_exec` (the dumper's keys, its networks):
- `superseded-put <id>` (tar on stdin): per package `superseded/<id>/<uuid>.enc` and
  `superseded/<id>/<uuid>.manifest.json` (hospital, restore id, plain sha256 = the plan's or
  export doc's `package_sha256`, sealed sha256, unsealed count, MAC). Write-once
  (`locked_bucket`, If-None-Match), then read back and decrypted to check both sha256s.
- `superseded-get <id> [--hospital U]`: a tar to stdout (the load uses it when the local tar is
  gone, e.g. a dead server mid-rollback). `superseded-check <id>`: every manifest present, each
  read back.

**6.2 When.** Flow (a): after the export, before the plan (so before anything is replaced).
Flow (b): after the export, before the export doc.

**6.3 Retention (Q-K90, Q-K99).** `deploy/dumps/expire.py`: a new name class `superseded/`. A
package is erased only when (1) older than the weekly retention (28 days), (2) the fingerprint
bucket's result (read with `secrets/anchor-read.env` on the owner's machine) says `loaded` or
`absent`-loaded for that hospital with no lost range, and (3) its manifest's unsealed count is 0.
Everything else (unsealed entries, lost, refused, unreadable, no result yet, result unreadable)
is kept. No reader key: nothing under `superseded/` is erased. The erasure record lists them as
today.

**6.4 Without dumps (F8).** `IHR_DUMPS` off: `rollback --restore` and `restore --target` refuse
unless `--local-packages-only --reason R`; the plan records `packages: "local-only"` and R. Local
copies then stay (mode 600) and `audit-supersede status` names them.

**6.5 Local copies (S6).** At the end of a load: result written, `superseded-check <id>` passed →
`export.tar` and the work volume are removed, `$DIR/supersede/<id>/local-deleted` written. The
rollback's kept database copy is unchanged (its own rule).

## 7. announce-timeline (F5)

- `audit-supersede announce-timeline start --kind move|major-upgrade --reason R --operator X`:
  a plan with `flow: "announce"`, no hospitals, `old_timeline` = now, and for major-upgrade
  `old_major` (`server_version_num` / 10000). `announce-timeline finish <id>`: the result
  (`new_timeline` now, `new_major`). Excuses nothing.
- `_read_restore_docs` keeps "new > old" except for `flow: "announce"`, `kind: major-upgrade`,
  `new_major > old_major`, and `new_major` = the database's major now (pg_upgrade starts at
  timeline 1; Q-K102).
- Runbook (README, new "Moving the database server" / "Major Postgres upgrade"): `start`;
  `pg_switch_wal()` and wait until `pgbackrest info` shows it archived; move or upgrade
  (`stanza-upgrade` after pg_upgrade); `finish`; `ihr_audit_verify --all` must pass, else it is an
  incident. A move by restore with lost WAL is flow (b), not an announce.

## 8. One whole-database restore at a time

- `supersede.export`, `stub` and `announce-timeline start` refuse while `Events.unfinished(tl)`
  is non-empty: plans with no result, not superseded, whose old timeline ≠ `tl`. `tl` = the
  database's timeline (flow a, announce) or the repository's newest (stub). The load and export
  of that same pending id are allowed. `rollback --restore` and `restore` also refuse on
  `sup_pending` (local) before any step.
- Escape: `audit-supersede close <id> --reason R` writes the result with every hospital not yet
  loaded as `refused` (Q-K104). The cuts stay; the timeline is explained; the next restore can go.

## 9. infra/aws (provider pieces only there)

- `infra/aws/anchor/main.tf`: variable `platform_suffix` (`^[a-z0-9-]{3,40}$`). The writer's
  `AddOnly` becomes `anchors/<suffix>/*`, with a deny on `anchors/ihr-events/*` and
  `anchors/ihr-restores/*`. New `aws_iam_user.events`: `s3:PutObject` on
  `anchors/ihr-{events,restores}/<suffix>/*` only, the `NeverRemoveOrUnlock` deny, and a deny of a
  put without `s3:if-none-match` (S3Store sends `IfNoneMatch="*"`, `anchor.py:150`). Its key is a
  sensitive output read once into the owner's password manager. `storage.tf`: the dumper's
  `AddAndReadDumps` adds `superseded/*`. Both `.tftest.hcl` files assert scopes and denies.

## 10. ADR-010 amendment text

> **Amendment (P1-T22c, 2026-11-21): the chain across a whole-database restore.** The bucket keeps
> its claims after T. Each hospital's tail after T is exported (live before a planned rollback; from
> a repository copy after a disaster restore), sealed into the dumps bucket and loaded back into
> the restore archive as an ordinary restore event. A claim is excused only by an archived
> original seal whose entries hash to it. A plan stored before the restore, a result chain on the
> current timeline and the watch's timeline history must agree; an owner-held events key writes
> them (Q-K91). Hospitals made after T are closed by a verified `absent` event; their slug is
> retired. Claims no copy holds are `lost`: an accepted loss is a permanent warning that never
> excuses or hides them. A timeline change with no restore is announced. Limits: the L…T window
> and the last unfingerprinted hour; an owner who edits the repository can shorten the provable
> end (claims stay visible as lost); other rolled-back `public` data (Keycloak) is outside this.

## 11. Tests, mutation rows, live proof

**Tenant tests** (`ihr_audit.tests.test_supersede`, new classes; c1's 41 stay green):
- Stub: written with no database; parses in c2 and in a fixture of c1's parser (extra fields
  ignored: criterion, else the stub is level-2 only); old timeline from a fake `info`.
- Disaster (c1's simulated end-to-end, tail kept as the "copy"): F = restored head; F lower with
  a P1-T22b event (allowed) and without (refused); F < L refused; a rewritten restored seal in
  (L, head] lowers the computed F and is refused (S3); an export doc stored after an event.
- Lost: in the window → lost; stored before end − skew → mismatch; at or below the restored head
  → never; after accept-loss: reported lost, gauge 1, no chain problem; a too-wide doc.
- Absent: closed only with no fingerprint before T − skew and uuid and slug free; slug then
  refused by `OnboardingForm`; a deleted archive row reopens it.
- Partial (`unreadable` beside `loaded`; refused for level 1); pending (second export refused,
  `close` frees it); announce (major-upgrade rule); watch (pending gauge, 48 h).
**Script tests:** `test_release_scripts.py` (order: stub before `restore_live`, `restored.json`
before `up`; refusals; level probe; F8); `test_dumps.py` (put/get/check, purpose, expire classes
with and without the reader key); promtool cases (AuditLostTail, AuditWholeRestorePending).
**Mutation rows** (exact texts fixed at build, each must FAIL): `supersede-b-fork-floor`,
`-b-fork-event`, `-export-doc-before-event`, `-lost-window`, `-lost-head`,
`-absent-first-fingerprint`, `-absent-slug` (design.md §6), `-absent-retired`, `-pending`,
`-expire-keep`, `-announce-major`.
**Live proof** (`scripts/test-supersede-live.sh disaster`; staging local proof, dev stack stopped,
synthetic data, about 40 min):
1. `s3-init` (events key), `dumps-init --local`, `backup-init --local`; alpha, beta; full backup;
   activity, seal, fingerprint; note T. After T: more of it; onboard gamma. Stop the `backup`
   service (criterion: only it pushes the spool), seal and fingerprint again (lost claims).
2. Disaster: `down`, remove `pgdata` and `walspool`. `restore --target T`; `up`, smoke OK; watch
   pending 1, unexplained 0.
3. `audit-supersede export`, `load`: verify `--all` excuses up to the copy's end, lists the lost
   claims; gamma closed, its slug refused; packages in the stand-in bucket; local copies gone.
   `accept-loss` → lost_tail 1, chain_broken 0.
4. Negatives: `restore --target` while pending (refused); a changed byte via `superseded-get`
   (refused); `dumps-expire --dry-run --allow-short` keeps the lost package. Optional: a P1-T22b
   restore of beta from a dump older than T, after T (F lower).
**Only provable at go-live:** provider IAM scopes and If-None-Match denies; the copy beside a
serving 8 GB server (memory, OOM order); a provider repository (`--target-timeline`, timings);
a real pg_upgrade announce; the owner's events key present only inside the one-off.

## 12. New decisions (auto-accepted in this run; awaiting the owner)

- **Q-K99 "Kept as long as the archive" (Q-K90).** A ⭐ never erased by `dumps-expire`; only a
  future offboarding erasure may remove them. B: 400 days. Rec A: the archive tables have no
  expiry either.
- **Q-K100 Verify's exit code with accepted lost claims.** A ⭐ exit 0, a LOST section printed,
  AuditLostTail permanent. B: a distinct exit 3 (every later drill and release check must learn
  it). C: exit 1 (a drill fails forever after one disaster). Rec A.
- **Q-K101 Builds older than P1-T22c2.** A ⭐ the load writes only objects the target's level
  reads (level 1: no export doc, loss doc, absent event or `unreadable`; absent stays
  `--accept-absent`); a rollback to a level-1 target is refused when the bucket already holds
  level-2 objects (fix forward). B: allow, alerts. Rec A (as Q-K86).
- **Q-K102 announce across a major upgrade (timeline resets to 1).** A ⭐ allowed only for
  `kind: major-upgrade` with a higher, current server major. B: defer to the upgrade task. Rec A.
- **Q-K103 Where the flow (b) copy runs.** A ⭐ on the server, OOM-first, dumper paused. B: on
  the owner's machine (personal data on a laptop). Rec A.
- **Q-K104 A pending load that can never finish.** A ⭐ `audit-supersede close <id> --reason`
  writes the result with the rest `refused`. B: nothing (every later restore refused). Rec A.

## 13. Size and split

About 2 task-equivalents (Opus builder, high; full review shape: audit). Two sittings:
- **P1-T22c2a: disaster flow.** §1, §2 (copy refactor, streaming, F rule, pending gauge), §3,
  §5's lost ranges in the result (no accept-loss yet), §8, the level and Q-K101, packages local
  only (as c1), tests, rows `b-fork-*`, `export-doc-before-event`, `lost-*`, `pending`, live
  steps 1-4 without the bucket and accept-loss. About 1.1 equivalents.
- **P1-T22c2b: closing and keeping.** §4 absent, §5 accept-loss and AuditLostTail, §6 bucket
  packages, expiry and F8, §7 announce, §9 infra/aws, README runbook (disaster, move, upgrade),
  §10 ADR-010 text, the remaining rows and live steps. About 0.9 equivalents.
