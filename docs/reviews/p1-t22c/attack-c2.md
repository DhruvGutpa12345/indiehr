# P1-T22c2 design attack (2026-11-21)
Attackers ran on `docs/reviews/p1-t22c/design-c2.md`. Three were EGM goldfish lenses with no conversation (Sonnet high, read-only tools, so no graphify) and one was an Opus audit lens (12 graphify calls). The orchestrator's decision follows the reports.

## egm-design (Sonnet high)

Review of design-c2.md (read-only). graphify calls: 0. This session has no shell tool, so I could not run `graphify explain` or `graphify affected`. I used Read and Grep on code only after reading the design. Treat code facts below as grep-checked, not graph-checked.

C2-1 (high) §2.6, §1.5: the pending gauge and the timeline check depend on history a replaced server does not have.
- The watch's timeline history lives in its own `state.json` (watch.py:610-623). `_whole_restores` only sees a change by pairing history entries (watch.py:650). `corroborated` returns CANNOT_SAY when the new timeline is the first entry (events.py:785-790).
- A disaster on a new server, or one that loses the watch volume, has no `before` entry. So the pending gauge never fires, the 48 h critical never fires, and S2 corroboration is skipped.
- Fix: compute pending from plans alone (stub plan, no result, old timeline ≠ current, not `superseded_plan`, age from `plan.stored`). State what the watch does on a history-less start.

C2-2 (medium) §2.5, §1.6: the reader does not enforce the F rule.
- The rule "F = restored head unless a P1-T22b event names F" is enforced only at load, against a local `restored.json` that the design itself calls "a cross-check, not a boundary".
- `check_whole` for disaster events is only L ≤ restored_to ≤ head. Today it enforces F = L (events.py:846-851).
- Fix: `check_whole` must require `restored_to.seq` = the export doc's `restored` head, or a P1-T22b event stored after T − skew naming it.

C2-3 (medium) §9: the IAM deny can break writers the design does not mention.
- `events.write` uses `writer_store()` (events.py:141-148). Without an events key it falls back to the WRITE key scoped as EVENTS. Callers are the owner-run P1-T22b hospital restore and offboarding (events.py:1046-1058).
- Denying `anchors/ihr-events/*` and `ihr-restores/*` to the writer key makes those fail wherever the events key is absent.
- Fix: name every events writer, say how each gets the key, and add a README step and a test.

C2-4 (medium) §6.3 vs §12 Q-K99: the two contradict.
- §6.3 erases clean packages after 28 days. Q-K99 rec A says "never erased by dumps-expire".
- Fix: pick one. If A, delete the erasure clause and the `expire-keep` rule text. If B, state the retention.

C2-5 (medium) §1.3, §1.4, §2.1: the first steps need an image and the events key on a possibly new server.
- The stub is written by "an owner one-off of the configured image", and `restored.json` is read before `up`. On a new server that image may not be present. `restore_live` only needs the Postgres image (staging.sh:1356).
- A bucket outage also blocks the stub, and `--no-supersede` writes to the same bucket (§2.1.1).
- Fix: say where the image comes from, and give the refusal text and the escape.

C2-6 (low) §11, Q-K101: the LEVEL constant is not in the c1 code.
- Grep finds no `LEVEL` in `supersede.py` or anywhere in the repo.
- Fix: c2 adds it; the probe must treat a missing constant as level 1, with a test. The design says "c1 = 1".

C2-7 (low) §2.2: the disk estimate is a fixed guess.
- Need = set size + 2 GB for WAL replay, but the tail may be "days" (§2.3).
- Fix: size it from the archive range in `pgbackrest info`, or fail with the numbers after the copy starts.

C2-8 (low) §2.2 vs §13: no deadline on the export.
- Between serve and export, the old-timeline set S must stay in the repository. Retention is time-based at 28 days (staging.sh:1188-1189), so S is safe only inside that window.
- Fix: record S and the archive max in the stub, and warn when the pending id is older than the retention margin (for example 14 days).

tier-fit: Opus/high with two sittings fits (c2a about 1.1 equivalents). Fix C2-1, C2-2 and C2-3 in the design before c2a starts.

## egm-zoom-out (Sonnet high)

Graphify calls: 0. I had no shell tool (Read, Grep and Glob only), so I could not run `graphify explain` or `graphify affected`. I used Grep after the Read hook warned the graph may be stale. Every symbol claim below comes from grep only, and none of it went through the graph.

Findings, most severe first:

1. **S-1 (high), §13 and §11, split and size.** The design is sized at about 2 task-equivalents, but it contains four separable features: the disaster flow, lost-tail accept-loss, bucket packages with retention, and announce-timeline. Several of these are new surface with no failure that needs them yet. Evidence: §13 puts absent hospitals, accept-loss, §6 bucket packages, §7 announce and §9 infra/aws all in T22c2b. §10 says an owner-held events key and the L…T window stay open until go-live. Smallest fix: build T22c2a only. Move 2b's items to a later task row, each needing its own trigger. This is a recommendation to cut scope, not a mandate.

2. **S-2 (high), §6, §9 and §12 Q-K99, bucket packages and IAM.** This adds a third storage layer: `superseded/` in the dumps bucket, 3 new dumper subcommands, an `expire.py` class with a 3-part erase rule, and a new IAM user. Local packages with `--local-packages-only` (F8) already work. The design itself says the stub, export doc and result carry the digests. Packages "kept as long as the archive" (Q-K99) also keep personal data indefinitely. Smallest fix: defer §6 and the §9 `AddAndReadDumps` change. Keep `$DIR/supersede/<id>` copies (mode 600) and treat a lost server as an incident. Move the Q-K99 retention question out of this task.

3. **S-3 (medium), §1 and §2.2, the stub-plan rule.** The rule layers on a second plan document, `restored.json`, a history-file parse and "parent ≠ stub old_timeline" handling. `restored.json` is called "a cross-check, not a boundary", yet §2.5 uses it as a hard fork-floor condition. The 8-hex `old_timeline` is derived from `archive[].max`, with an assumption flagged but not tested. Smallest fix: drop `restored.json`. Take F from the export doc and the bucket claims. Or state it as a required boundary, and test the assumption in step 4.

4. **S-4 (medium), §2.2, the copy refactor.** `restore_drill` (scripts/staging.sh:1250) is split into `repo_copy` with `--set`, `--target-timeline`, `--oom-score-adj`, a dumper pause and a disk formula. That is reuse of the drill body, which is good, but the `as_of` parse (staging.sh:1291) is the only existing log-parse. The design also needs "redo done at" and `backup_set`, and the code has no parse for them (grep found none). Smallest fix: take `source_end` from `last_commit` only, and drop `wal` and `backup_set` (§2.4 marks them "incident record only").

5. **S-5 (medium), §2.3, streaming.** It adds a work volume, a per-package gzip stream and a 256 MB / 1 GB memory criterion. No large tail has been shown. Smallest fix: reuse c1's `export`/`read_tar` (supersede.py:393, :464) as they are. Add streaming only if the live proof shows a memory failure.

6. **S-6 (medium), §2.6 and §7, the new gauge and announce flow.** The pending gauge with a 48 h critical rule, and a whole `announce-timeline` flow with a `major-upgrade` special case in `_read_restore_docs` (events.py:691), serve problems that only appear at go-live (pg_upgrade, server move). Q-K102 offers option B, "defer to the upgrade task". Smallest fix: take Q-K102 B and drop §7. Keep the pending gauge only if a test needs it.

7. **S-7 (low), §11, altitude and test volume.** The mutation rows, the 40-minute live proof and the exhaustive negatives are detailed, and several rows overlap (`-lost-window` and `-lost-head`, and `-absent-*` ×3). Smallest fix: keep one row per distinct guard. Cut the rows for any item deferred under S-1.

8. **S-8 (low), §1 and §11, level constant.** `ihr_audit.supersede.LEVEL` ("c1 = 1, c2 = 2") does not exist in supersede.py. Grep found no `LEVEL` there or in c1's docs. The design treats it as c1's constant, and the Q-K101 level probe depends on it. Smallest fix: say that T22c2 adds `LEVEL` and the shell probe, or add it in c1's closing step.

Reuse confirmed (grep only): `export_one` (supersede.py:305), `restored_fork_problem` (:620), `events.check_whole` (:819), `_lower_claim` (:804), `corroborated` (:779), `anchor.load_all` (anchor.py:511), `sup_pending` (staging.sh:2165), `restore_drill` (staging.sh:1250). No duplicate new helper found.

tier-fit: over-sized for one task. Split T22c2a (disaster, partial, lost ranges) from the rest, and defer S-2, S-6 and part of S-1.

## egm-implementation-readiness (Sonnet high)

Review of design-c2.md (implementation readiness)

graphify calls: 0. This session had no shell tool, so I could not run `graphify explain` or `graphify affected`. The Read hook also said the graph was stale. I used Read and Grep on exact text, so every file:line below is unverified by the graph. The proposed new symbols (`Events.unfinished`, `Events.lost`, `supersede.LEVEL`) do not exist yet.

**C2-1 (high), §2.1.1 and §1.1.** `--no-supersede` says "writes the result now ... steps 3-4 never run". That cannot work.
- A result needs a plan. `events.py:719-731` pops a result with no plan.
- A result needs `new_timeline`. That is read only in step 4, so it is also skipped.
- A result needs a hospital list. The database is gone, and the stub has `hospitals: {}`.
- `lost` is not a result outcome. `RESULT_OUTCOMES` at `events.py:97` is loaded, unchanged, absent and refused.
- Fix: for `--no-supersede`, still write the stub and read the timeline. Write the result after `db` is up alone. Take the hospital list from `anchor.claims_at(store, T)`. Add `lost` to the outcomes.

**C2-2 (high), whole document, no file or struct list.** The design has no list of file changes or schemas.
- `events.py:98` (`RESTORE_KEY_RE`) accepts only `plan|result`. `export.json` and `loss-<uuid>.json` need a new key rule, otherwise `_read_restore_docs` reports them as unreadable objects.
- `_result_hospital` (`events.py:593`) requires an event key if and only if the outcome is `loaded`. c2 adds `unreadable`, `lost`, and `absent` with an event.
- The export doc's JSON schema is only prose.
- `check_whole` (`events.py:843`) reads `plan.hospitals`. The switch to the export doc is not specified.
- Fix: add a "Changed" list with the schema and parser for each new object.

**C2-3 (medium), §1.1 and §1.4.** The stub is written before `restore_live`, whose own `die` paths run later (`staging.sh:1349-1359`).
- Each failed attempt leaves an orphaned stub.
- The pending gauge (§2.6) matches plans by old timeline, so two stubs are ambiguous.
- Fix: write the stub inside `restore_live`, just before the pgbackrest restore (`staging.sh:1368`). Or state that `superseded_plan` (`events.py:429`) covers it.

**C2-4 (medium), §1.4 and §8.** When the parent timeline differs from the stub's `old_timeline`, `close` cannot explain the change.
- The watch explains a change only when `plan.old == before.timeline` (`watch.py:657`).
- A result must carry `old == plan.old` (`events.py:726`).
- A wrongly derived stub is write-once, so the "timeline is explained" claim fails.
- Fix: on mismatch, write a second stub with the true parent and use it for `close`. Or derive `old_timeline` from the history file before the restore.

**C2-5 (medium), §1.4 and §8.**
- `restored.json` has no reader command, role or `db_roles` process. Its behaviour when missing at load is not stated.
- `sup_pending` (`staging.sh:2165-2170`) looks only for `$DIR/supersede/<id>/state` and a `loaded` marker. Flow (b) never creates `state`, so the local refusal and the `up` warning (`staging.sh:884`) miss it. A new server has no local directory at all.
- Fix: name the one-off. Write `state` at the stub step. Say what happens when `restored.json` is absent.

**C2-6 (medium), §2.3.** The 256 MB limit for a 1 GB package is a criterion only. The current code holds whole packages in memory.
- `supersede.py:253` calls `gzip.decompress(body)`.
- `read_tar` (`supersede.py:477`) reads each member whole.
- `export_one` (`supersede.py:363`) loads all entries.
- Fix: specify a streaming `Package` reader and writer API, including how sha256 is computed and how seals are read before entries.

**C2-7 (low), §12 Q-K101 and §2 "level".** `supersede.LEVEL` does not exist. `sup_image_ok` (`staging.sh:2161`) only imports the module.
- A c1 image has no constant, so the design must say a missing constant means level 1.
- "Bucket already holds level-2 objects" needs the reader key. The rollback runs on the server, and the design does not say how it gets that key.

**C2-8 (low), §4.** c1 already has the `absent` outcome in plans and results (`events.py:96-97`, `supersede.py:316`). Its rule is the newest fingerprint at or before T. §4 uses a different rule: no fingerprint of that uuid at or before T − skew, across all history. The design does not say it replaces c1's rule, or which c1 tests change.

tier-fit: the design is Opus-high work. The readiness gaps are large for a builder, but they are tractable once C2-1, C2-2 and C2-6 are settled.

## audit integrity (Opus high)

P1-T22c2 design-c2.md: audit-integrity attack (Opus, high). I made 12 graphify calls (11 explain, 1 affected). I edited no files.

**S1 (high, §5 and §2.1): `--no-supersede` lets an operator turn an arbitrary tail into a warning.** With `--no-supersede`, `source_end` is set to T. So every claim stored after T − skew becomes "lost". An operator restores to any T, skips the export and runs `accept-loss` for each hospital. AuditChainBroken then becomes the permanent AuditLostTail warning, even though the repository held the tail. This contradicts confirmed Q-K87 and design.md:345-348, where the provable end is the newest archived WAL. Fix: a `--no-supersede` result carries no `lost` ranges and `accept-loss` refuses that id. Claims after T stay mismatches until a later export from the repository.

**S2 (high, §2.5): the fork point F floats, so a rewrite in (L, head at T] is excused.** c1 pins F = L (supersede.py:664-672, events.py:842-848). c2 accepts L ≤ F ≤ head, and the only binding is `restored.json`, an operator-local file that §1.4 itself calls "not a boundary". The DB owner rewrites and re-seals the restored seals after L, lowers `restored.json` to match, and the archived originals then excuse the claims. Up to about an hour of trail before T is rewritten silently. Fix: tie F to T with times the hash already covers (each entry's `at` is in the seal hash, seal.py:9-13). F must be at least the highest package seal whose entries all have `at` ≤ T − (skew + longest transaction allowed by db_limits), apart from the P1-T22b exception.

**S3 (high, §5 and §2.4): the "lost" window has no evidenced bound.** c2 lets `loaded` (exported) hospitals carry lost ranges, which confirmed Q-K87 forbids ("never for a hospital any source exported"). `last_commit` comes from the operator's copy log, and `wal` is "for the record only". An operator who stops the `backup` service (exactly what live-proof step 1 does) and keeps serving for days can then make every claim since "lost". Fix: a claim is lost only if stored no later than the package head's newest entry `at` + archive_timeout + one fingerprint interval + skew. Refuse a `last_commit` earlier than that `at`. Ask the owner again about the change to the exported-hospital rule.

**S4 (high, §7): announce-timeline can hide a cut.** The operator announces a "move" but actually restores to just after the last fingerprint. Up to an hour of trail and data disappears and nothing alerts: no claim is cut, the timeline change is "explained", and `verify` passes. In c1 the same change would be an unexplained, critical alert (Q-K89). Fix: `start` records `pg_current_wal_lsn()` and every hospital's head (seq, hash). `finish` refuses unless the new timeline's switch point is at or after that LSN. Readers check that each recorded head is still in its chain.

**S5 (high, PLAUSIBLE; I did not check the local gateway's scopes): the Celery key can still write the restore documents.** `writer_store` falls back to the writer key (events.py:141-147), and staging only warns (staging.sh:2149-2150). c1 accepted this because "the archived-segment rule still is" the boundary (build-c1:155-157). The lost, absent and accept-loss paths have no archived segment behind them. With a stolen Celery key, an attacker writes the write-once result for a flow-(b) restore still waiting for its load first, marking hospitals `unreadable` with lost ranges, plus the loss documents. Every cut turns into a warning, and the real load can no longer store its result. Fix: level-2 objects, and any result of a disaster plan, are written with the EVENTS store only, with no fallback. Every gateway and provider policy, not only infra/aws, denies the writer key on `ihr-restores/` and `ihr-events/`.

**S6 (medium, §4): a deliberately deleted hospital can be closed as `absent`.** Nothing checks that the database was really restored to T. The operator writes a stub with T before hospital X's first fingerprint, actually restores to "now", drops X, then runs the export and load, and X is closed. Corroboration does not catch it: CANNOT_SAY (the normal case when the watch's history is lost in a disaster) only raises a warning (events.py:876-877; rules/audit.yml:255-258). Fix: the load refuses, and readers ignore absent closings, when any hospital's restored head is above the last package seal whose entries are all `at` ≤ T + seal interval + longest transaction. This is S2's floor used as a ceiling.

**S7 (medium, §6.5 and §2.2): personal data stays on the server's disk.** `export.tar` (gzip only, holding patient-linked entries) is deleted only after a fully successful load. After a refused, partial or closed load, or in local-only mode, it stays with no expiry. The copy database volume also survives failed exports and `--slug` retries. Fix: delete the local plain tar as soon as `superseded-check` passes at §6.2, and let the load use `superseded-get`. Remove the copy volume on every exit. Give local-only packages a daily expiry check.

**S8 (low, §4): a hospital created shortly before T is treated as `absent`.** A hospital made in [T − 1 h, T] has no fingerprint yet but is in the restored database. The export calls it `absent`, the load refuses it (uuid present), and its cut stays critical until someone runs `close`. Fix: `absent` only if the hospital is also missing from the restored database; otherwise export it with L = genesis.

Refutation pass:
- **Dropped:** forged or dump-key-written packages (the event-key documents' sha256 binds them, so a bad one is rejected and the worst case is a denial of service). Dropped: the operator lowering `restored.json` to widen the lost range (the stored-time window catches it). Dropped: `close` naming the wrong new timeline (the cuts stay).
- **Survived:** S1-S6. S5 depends on the events key not being set up.

tier-fit: OK: multi-document adversarial design review against c1 code; it needed Opus-level reasoning about time and trust bindings.

## Decision (orchestrator, 2026-11-21)

- **This run builds P1-T22c2a.** It covers:
  - the stub plan, written inside `restore_live` just before the pgBackRest restore, with the
    parent timeline checked against the history file;
  - flow (b): serve first, copy from the backup repository (reusing `restore_drill`), export with
    c1's code as the watch role, load, events;
  - flow (c), per-hospital outcomes without lost ranges;
  - a refusal of a second export while a load is pending;
  - `LEVEL`, where a missing constant means level 1.
- **These findings are fixed in c2a:**
  - Goldfish design C2-1: pending is computed from plans alone, and the start without history is
    stated.
  - Goldfish design C2-2 and audit S2: `check_whole` binds the fork point by entry times, as a
    floor.
  - Audit S5: disaster documents are written with the events store only, with no fallback; a
    site without the events key fails closed.
  - Goldfish design C2-5: the image and the refusal text.
  - Goldfish design C2-8: the set is recorded in the stub, with a warning past 14 days.
  - Readiness C2-1: the `--no-supersede` result needs a plan and the timeline, and carries no lost
    ranges.
  - Readiness C2-2: a "Changed" list of files, objects, keys and parsers, written by the builder
    first.
  - Readiness C2-3 and C2-4: stub placement and the parent timeline.
  - Readiness C2-5: the reader of `restored.json`, and `state` written at the stub step.
  - Readiness C2-7 and zoom-out S-8: `LEVEL`.
  - Audit S7 (part): the copy volume is removed on every exit, and the local tar after a verified
    load.
- **Deferred to P1-T22c2b**, as its own TRACKER row:
  - lost tails and `accept-loss` (audit S1, S3);
  - absent closing (audit S6, S8; readiness C2-8);
  - bucket packages, expiry and the IAM user (zoom-out S-2; goldfish design C2-3);
  - `announce-timeline` (audit S4; zoom-out S-6);
  - streaming (zoom-out S-5; readiness C2-6);
  - ADR-010 text.

  Claims after the copy's end stay critical cuts until c2b. That fails closed, as Q-K87's original
  option did.
- **Process note:** the EGM agent types have only Read, Grep and Glob, so the graphify step cannot
  apply to them. The run rules now say so, and the orchestrator passes them graph output instead
  when it matters.
