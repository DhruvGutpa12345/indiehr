# P1-T15a design: reference packs, first pack = CARE's system value sets (draft for attack, 2026-11-13)

PLAN row P1-T15: versioned packs per hospital with `PackRelease`; unedited rows updated,
edited rows flagged for the admin, nothing deleted; CI re-applies packs to a fresh hospital after
each upstream sync; `up` stops running `sync_valueset` on existing hospitals. Owner rules:
TENANCY_DESIGN §5 and §7.2, and Q-T10 ("automatic for unedited rows, with a visible change list;
edited rows need the admin's OK").

The research is in the run's scratchpad (`t15/valuesets.md`, `refmodels.md`, `ours.md`, with
file:line evidence). It is summarised where used.

## Split (by Claude, 2026-11-13, awaiting owner confirmation: Q-K24)

- **P1-T15a (this design):**
  - the engine (registry, per-hospital state, apply, edit detection, retire, accept/keep);
  - the first pack, `care-valuesets`, built from CARE's own registry of 31 system value sets;
  - the roll-out command in place of `sync_valueset`;
  - the provisioning step;
  - drift and re-apply tests that run on every upstream sync.
- **P1-T15b (later, before the Phase 2 masters):**
  - the hospital admin's "updates available" API (the screen comes with P1-T24);
  - more kinds: PatientIdentifierConfig (UHID, ABHA) and Questionnaire, with "forms are never
    edited in place" (a new version is a new form, plus a guard against in-place edits of a form
    that has answers);
  - per-facility kinds (the lab catalogue needs same-facility links).
  - Content for these is clinical or the owner's (Q-K12), so it arrives with Phase 2.

## Why value sets first

- They are the only default content CARE ships, and our `up` already writes them into every
  hospital at every start: `all_tenants_command --no-public sync_valueset`
  (deploy/staging/compose.yaml:215, dev override :52). That goes against TENANCY §7.2, and it has
  two concrete faults:
  - a soft-deleted system value set in any hospital makes `sync_valueset` insert a duplicate
    slug, which raises an IntegrityError and stops the whole staging start (`migrate` is a
    dependency of every service);
  - a new upstream value set lands in every hospital outside any versioned or audited release.
- Slugs are a permanent API. About 73 `ValueSetBoundCoding[slug]` fields, questionnaire saves,
  the interpretation evaluator and 21 hard-coded frontend pickers resolve value sets by slug.
  A pack must never rename or delete a slug, only retire it.

## Design

### 1. Apps (names from TENANCY_DESIGN §3)

- **`ihr_refdata_registry`** (shared, in `public`): `PackRelease(pack, version, sha256,
  care_commit, item_count, loaded_at, loaded_by)`, unique (pack, version). It is the fleet-wide
  record of which releases exist. It holds no hospital or patient data and is added to the
  public-table allow-list with a reason.
- **`ihr_refdata`** (tenant app, in each hospital's schema):
  - `PackItem(pack, kind, item, applied_version, applied_sha256, state, reason, pending_version,
    pending_content, changed_at)`, unique (pack, kind, item). The column names avoid the audit
    classifier's "key/token/pin" pattern.
  - `PackApply(pack, version, sha256, care_commit, applied_at, actor, counts)`, one append-only
    row per apply that changed something.

### 2. Pack files (repo, like terminology's bundled content)

- `plugins/ihr_platform/ihr_refdata/packs/<pack>/<version>.json` holds `{pack, version (an
  integer), kind, care_commit, items: [...]}`.
- `packs/MANIFEST.json` lists `{pack, version, file, sha256}`.
- The apply step verifies every file's sha256 against the manifest and refuses a mismatch.
- Only the latest version of each pack is applied.
- **Built, not hand-written:** `ihr_pack_build care-valuesets` reads `SystemValueSet._valuesets`
  through CARE's code and writes a new version file only when the content differs from the
  latest. It records CARE's commit from `upstream.lock` and the item count.
  - `--check` exits 1 when CARE's registry differs from the latest committed version. A test
    runs `--check`, so an upstream sync that changes a system value set fails CI until a new
    pack version is built and committed (the "§5: CI re-applies packs after each upstream sync"
    rule).
  - The build asserts the registry is fully imported, with the count in the file. Fewer items
    than the last version fails rather than silently retiring sets.

### 3. Edit detection (hash of the fields the pack sets)

- **Fields set for a value set:** `name`, `status`, `compose`, `is_system_defined`. Never `slug`,
  never `description`.
- **Hash:** sha256 of canonical JSON (sorted keys, no spaces) of exactly those fields, taken from
  the database row and from the pack item alike.
- **Rules per item, for the hospital:**

| Situation | Action | State |
|---|---|---|
| no row with that slug (deleted rows included) | create it from the pack | current |
| row soft-deleted | leave it | pending, reason `deleted` |
| row equals the new content | nothing written (adopt) | current |
| row equals what this pack last applied | update the four fields | current |
| no record of this pack applying it, and the row differs (a row from an older `sync_valueset` or written by hand) | leave it | pending, reason `unknown-origin` |
| row differs from what this pack last applied | leave it | pending, reason `edited` |
| the hospital kept its own version earlier | leave it | pending again only when the pack changes again |

- **Removed from the pack** (in the last applied version, not in the new one): if the row is
  unedited, set `status=retired` (never delete, so old data still resolves); otherwise pending,
  reason `edited`.
- **Nothing is written when nothing changed.** The audit trail is append-only and records every
  write, so a re-apply must be silent.

### 4. Applying (`ihr_pack_apply`)

- `ihr_pack_apply --all | --slug <s> [--check]`:
  1. load or verify the manifest into `PackRelease` (idempotent);
  2. for each target hospital, each in its own transaction under the same per-hospital advisory
     lock as provisioning (`runner._exclusive` made public), in that hospital's schema, apply
     every pack.
- `--all` takes **ACTIVE** hospitals only (like beat and `ihr_load_geography`). Provisioning,
  suspended and archived schemas are skipped.
- One hospital's failure is reported, the rest continue, and the command exits 1 at the end
  (error isolation, §7.2).
- A pack whose last recorded apply has the same sha256 is skipped, unless it has pending items
  to re-check.
- **Audit:** the writes run inside `ihr_audit.context.job({"job": "packs", "pack", "version",
  "sha256"}, "packs:<operator>")`, so each value-set write in the trail names the pack and
  version.
- `--check` reports per hospital what would change and writes nothing.
- **Pending items:** `ihr_pack_pending --slug <s> [--accept <pack>:<item> | --keep <pack>:<item>]`.
  - "Accept" applies the pending content, and restores a soft-deleted row with it.
  - "Keep" records that the hospital chose its own version (state `kept`, with the row's hash).
  - The API and screen come in P1-T15b and P1-T24.

### 5. Where it runs

- **Start-up (staging compose and the dev override):** `ihr_pack_apply --all` replaces
  `all_tenants_command --no-public sync_valueset`, after the guarded role sync (§7.2 order:
  migrate, roles, packs).
- **Provisioning:** the `value_sets` step (name kept, so older runs resume) applies the packs to
  the new hospital instead of calling `sync_valueset`.
- **`ihr_create_tenant`** (dev, seed and tests): the same.
- CARE's `sync_valueset` is no longer called by any of our paths.

### 6. Tests (tenant mode, labels `ihr_refdata ihr_refdata_registry`)

- `--check` drift: CARE's registry equals the latest committed pack.
- A fresh hospital gets all items; a second apply writes nothing (no `PackApply` row, no new
  audit entries, no changed `modified_date`).
- Each rule in the table above, with a second pack version from a temporary manifest.
- Retire, never delete; accept and keep; sha256 mismatch refused.
- `--all` isolates a failing hospital and exits 1; ACTIVE only; it refuses while provisioning
  holds the lock.
- The audit entries carry `job=packs` and the version.
- Provisioning and `ihr_create_tenant` use packs, and no path calls `sync_valueset`.

## Questions this design takes (auto-accepted, awaiting the owner)

- **Q-K24:** the split into P1-T15a and P1-T15b.
- **Q-K25:**
  - `up` applies packs to ACTIVE hospitals only;
  - rows that differ with no record of this pack writing them count as edited ("unknown
    origin"), so the admin decides;
  - "keep" is final until the pack changes again.

## After the attack (2026-11-13): the design as it is built

Two Opus reviewers (correctness and feasibility; security, tenancy and operations) raised 24
findings, and a third agent tried to refute each one. Result: 1 refuted (C12), 5 confirmed, 18
partly. None was fatal. Every confirmed or partly confirmed finding changes the design as
follows. Where this section and the sections above disagree, this section wins.

1. **Layering (C1, S3).**
   - `engine.apply_packs(hospital, *, operator)` is lock-free and assumes its caller holds the
     hospital's lock.
   - `ihr_pack_apply` takes `runner.exclusive(slug)` (made public) per hospital and calls it.
   - These call the engine directly: the provisioning `value_sets` step, `activate` (before the
     status flips, next to geography: a resumed run must not go live on an older pack, C4),
     `ihr_create_tenant`, and `seeding.load_demo_data`.
   - The `schema_created_here` guard goes: the engine is idempotent.
   - `--slug` refuses a hospital that is not ACTIVE.
2. **Start-up never blocks the fleet for one hospital (C2, S1, S2).**
   - `ihr_pack_apply --all --startup` exits non-zero only for fleet-wide faults: a manifest or
     sha256 mismatch, a version conflict, or a registry failure.
   - A failed hospital is recorded as a `PackApply` row with `outcome=failed` (safe error text)
     and one fixed log line, `ihr-packs: hospital=<slug> outcome=failed`, which a log alert
     catches.
   - A busy lock is `outcome=deferred` (the next start or a manual run picks it up).
   - Outside `--startup`, any failure exits 1.
3. **The hash compares meaning (C3, S4).**
   - Pack items store `compose` exactly as `sync_valueset` writes it:
     `composition.model_dump(exclude_defaults=True)`.
   - For hashing, both sides are normalised: `ValueSetCompose.model_validate(compose)` is dumped
     with `exclude_none=True`, and empty lists are dropped. List order is kept.
   - A test runs CARE's real `sync_valueset` in a fresh schema, then the packs, and asserts every
     item is `current` with zero value-set writes and zero audit entries.
4. **The build (C6, C7, S11, S12).**
   - `ihr_pack_build care-valuesets` imports every module under `care.emr.resources` and the
     URLconf before reading the registry, sorts items by slug and refuses duplicates.
   - It compares the slug set with a committed, hand-reviewed `expected_slugs.txt`: a missing or
     new slug fails until the list is updated. A slug removed upstream needs
     `--allow-removal <slug>`.
   - It writes canonical bytes (`json.dumps(indent=1, sort_keys=True, ensure_ascii=False)` plus
     a newline) to a writable output folder. The host wrapper `scripts/pack-build.sh` passes
     `--care-commit` from `upstream.lock` and updates `MANIFEST.json`.
   - `--check` compares the canonical items only, never `care_commit`.
   - **Version files are immutable:** a test checks every manifest sha256, and a pre-commit hook
     refuses a modified (as opposed to added) version file.
   - `system-medication-not-given` is registered by a module nothing imported, so CARE never
     shipped it. Walking the packages includes it: it is CARE's own system set, so it is shipped
     (noted in Q-K25).
5. **Deleted system sets are restored (C8).** CARE binds these slugs in code, so a soft-deleted
   one breaks every write bound to it. The engine restores it with the pack content (recorded,
   audited). A cheap existence check of every pack slug runs even when the sha256 matches.
6. **Retire bookkeeping (C9).**
   - Retiring sets `status=retired` and records the hash of the retired row. A re-add then
     returns the row to `current`.
   - Retire only marks the set: CARE's validation ignores status.
7. **Versions only go forward (C10, S6, S9).**
   - A manifest version lower than the highest version the hospital applied skips that pack
     (one warning, exit 0): an image rollback must still start.
   - An equal version with a different sha256 refuses that hospital (a rebuilt version).
   - `PackRelease` is loaded with get_or_create and then compared. A sha256 conflict is a named
     refusal ("versions are immutable, build vN+1").
8. **Audit context (C11, S8).**
   - `audit.job()` wraps only the two commands, with `via: startup` or `via: operator`.
   - The engine adds `pack`, `version` and `sha256` to whatever context is already set, through
     a new `audit.extend()`. So the provisioning writes keep their operation and requesting
     operator.
9. **Accept and keep (S7).**
   - `ihr_pack_pending` requires `--operator` and `--reason`.
   - Only `pending_version` and `pending_sha256` are stored. At accept, the content is rebuilt
     from the verified pack file, and accept is refused if the pending version is no longer the
     latest.
   - The admin role and API come in P1-T15b.
10. **Every path covered (S10).**
    - `seeding.load_demo_data` (the demo seed, the isolation world and the scale bench) routes
      CARE's `sync_valueset` call to the engine.
    - A test patches `sync_valueset` to raise and then runs provisioning, `ihr_create_tenant` and
      the seeding.
11. **Not in this task (S5):** reactivating a suspended hospital has no code path yet. When it
    gets one, it calls the engine as `activate` does. `public` is never a target.
12. **Second review (2026-11-13, findings C1-C10 and S1-S9 of the implementation).** These
    change the rules above; where they disagree, this item wins.
    - **A local edit is pending only when the pack has something new for it (C4).** In the §3
      table, the row "row differs from what this pack last applied -> pending, reason `edited`"
      applies only when the pack's content for the item changed since it was last applied
      (`applied_sha256 != new`). When the pack has nothing new, the row and its record are
      left as they are (counted as `local-edit`), whether or not a full comparison runs; a
      pending or kept change the pack has since withdrawn returns the record to `current`.
    - **Kept items follow the newest release (C1).** A kept item whose declined content is
      unchanged in a newer release has its `pending_version` moved to that release (still
      `kept`), so accept stays possible. The same for a kept removal.
    - **Unusable compose is repaired (C2).** When the hashes match but the stored compose lacks
      a list CARE's `create_composition` iterates over (`include`, `exclude`) or holds null
      there, the pack's canonical compose is written (one audited write, counted `repaired`).
      The start-up existence check counts such rows as missing, so they are repaired without
      waiting for a new release. Rows written by `sync_valueset` still adopt with zero writes.
    - **A newer release that writes nothing is recorded (C5)** as one `PackApply` row with
      outcome `reached`, which counts as applied for the sha256 short-circuit and the version
      checks.
    - **Malformed items are fleet-wide faults (C8):** `prepare()` checks every item of each
      pack's newest release (all four fields and the slug, their types, `compose` through
      CARE's `ValueSetCompose` with both lists present) and refuses the fleet (exit 1).
    - **Version files are checked against history (C3, S3):** `check-pack-versions.sh --base
      <ref>` in CI requires every release in the base `MANIFEST.json` to be listed unchanged
      and its file to hash to it; the local hook refuses staged M, D, R and T.
    - **Busy hospitals at start-up (S4):** tried again after 5, 15 and 30 s; still busy, they
      are recorded (`deferred`) and logged with the alerted `outcome=failed` line.
    - **Operator and reason (S6, S7):** `--operator` is 1-64 of `[A-Za-z0-9._@-]`, refused
      with `--startup`; `PackApply.actor` and `PackRelease.loaded_by` are `packs:<operator>`
      or `packs:startup`. The reason is put on one line, refused above 300 characters, names a
      role or a ticket (never personal details), and is recorded once (the command's audit
      context and `PackApply.counts`), never re-added by the engine.
    - **The demo seed takes the hospital's lock (C9, S5)** when the hospital is already live;
      a busy lock is a clear refusal.
    - **The log alert (S1)** reads only the `migrate` (dev `celery`) container's whole line
      `^ihr-packs: hospital=<slug> outcome=failed$`, and the mailed command never carries the
      label.
    - Unchanged on purpose: a kept row is not compared with `kept_row_sha256` (C10; Q-K25 "keep
      is final until the pack changes again"); the column records the row as kept for the
      P1-T15b screen and the audit.
