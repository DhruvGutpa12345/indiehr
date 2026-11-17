# P1-T15b design: pack decisions API, identifier configs and forms, per-facility kinds (attacked 2026-11-16; see the last section)

Synced with the code on 2026-11-17 (drift rows 1–5, 14–17 of docs/egm/2026-11-17-drift-p1-t15b.md).

Builds on P1-T15a (docs/reviews/p1-t15/design.md; its "After the attack" items win there).
Owner rules: Q-T10 (automatic for unedited rows, with a visible change list; edited rows need
the admin's OK; nothing deleted), Q-K12 (identifier and form content is the owner's or
clinical), TENANCY §5-§7. New decisions are Q-K60 onward (§5), auto-accepted, awaiting the owner.

## 1. Scope and split

- **P1-T15b1:** the hospital admin's API: pending and kept items, the field difference, accept
  or keep with a reason, and the change list of automatic applies. No screen (P1-T24).
- **P1-T15b2:** the handler contract; two hospital-wide kinds, `identifier_config`
  (PatientIdentifierConfig) and `questionnaire`; the guard against editing an answered form;
  the identifier-config cache fix. Suggested builder split: b2a (contract + identifier
  configs + cache) and b2b (forms + guard).
- **P1-T15b3:** facility scope in the engine (items applied in each facility, links inside one
  facility, pack order), proven with test-only kinds.
- **Not in scope:**
  - any shipped content (UHID, ABHA, forms, lab catalogue): Phase 2, owner and clinical review;
  - real per-facility kinds (lab catalogue) and anything with prices (§5, Q-K69);
  - web screens; facility admins deciding; a hospital versioning its *own* forms;
  - the reactivation path of a suspended hospital (P1-T15a item 11, unchanged).

## 2. P1-T15b1: the "updates available" API

### 2.1 Routes (tenant URLconf, `path("api/ihr/packs/", include("ihr_refdata.urls"))`, placed before the `api/ihr/` includes)

| Method | Path | What |
|---|---|---|
| GET | `/api/ihr/packs/updates/` | pending and kept items, paged |
| GET | `/api/ihr/packs/updates/<int:pk>/` | one item with its field difference |
| POST | `/api/ihr/packs/updates/<int:pk>/` | accept or keep it |
| GET | `/api/ihr/packs/history/` | the change list: applies and decisions, newest first |

- `pk` is the `PackItem` id in this hospital's schema. Item keys cannot go in a path: an
  identifier key is a URI.
- **List query:** `state=pending|kept` (default both), `pack=`, `kind=`, `facility=<uuid>` (b3),
  `limit` (default 50, max 200), `offset`. Unknown values are a 400. Sorted by pack, then item.
- **List body:** `{count, results: [{id, pack, kind, item, label, facility, state, reason, applied_version, pending_version, changed_at}]}`. `facility` is the facility's external id as
  a bare string, or null for a hospital-wide item (no name; `api.py:122-124`). `label` is the row's display
  name: value set name, identifier `display`, form title, definition title.
- **Detail body:** the list fields plus `pending_sha256`, `effect` (`create`, `update`,
  `restore`, `retire`; forms return `new-version` or `status-only` instead of `update`, see
  `forms.py:368-375`), `blocked` (`""`, `conflict`, `data-check`, `missing-link`; accept
  is refused while set), `conflicts` (counts only, §3.3) and `fields`:
  - one entry per field the pack sets: `{field, hospital, pack, previous}`. `previous` is what
    the pack last applied, rebuilt from the applied version's file (released files stay in the
    manifest for good). It is null when that version is unknown (unknown-origin);
  - large JSON fields (`compose`, `questions`, `config`) are one entry `{field, changes,
    truncated}`: `changes` holds up to 100 `{path, hospital, pack, previous}` items, each value
    cut to 200 characters, and `truncated` is true when anything was cut (`diff.py:63-81`).
- **POST body:** `{action: "accept"|"keep", version: <int>, sha256: <hex>, reason: <text>}`.
  `version` and `sha256` are the `pending_version` and `pending_sha256` the admin was shown.
- **History query:** `pack=`, `limit`, `offset`. Body rows: `{id, pack, version, outcome, at,
  by, counts, changes}`. `by` is `start-up`, `operator`, or the deciding staff member
  (`{user: <external id>, username}`). The reason of a decision is shown (it names a role or
  ticket by rule). `error` is never shown: a failed apply reads "failed; the details are kept
  for the operators" (review Q4: not every failure alerts). An applied row from before b1 (no
  `changes_total`) shows `changes: null`, "not recorded" (review T3).

### 2.2 Who may call, and why

- Only a hospital-wide admin: `ihr_identity.staff_access.hospital_wide_admin(user)`, signed in
  as staff, not a service account. This is the audit viewer's gate (`IsHospitalAdmin`); the
  builder moves that class to `ihr_identity/staff_access.py` and both apps import it.
- Why: Q-T10 names the hospital admin, and every pack kind so far changes hospital-wide
  configuration. Facility admins deciding for facility items is Q-K60.
- The gate is a DRF permission class, so a refused write is decided before the body is read
  (`role_matrix_writes.txt`: `auth`). GETs show no patients, so they write no request entry.

### 2.3 Never shown

- Patient data of any kind. Identifier conflicts are counts, never values or patients.
- A row's `history`, `meta`, `created_by`, `updated_by`, ids of other rows, or any field the
  pack does not set.
- `PackApply.error`, `PackRelease.loaded_by`, our operators' names beyond `packs:<operator>`.
- Items of deleted facilities (b3): hidden, and POST answers 404.

### 2.4 Accept and keep

- The view calls `engine.decide(hospital, pack, item, accept=…, operator=…, reason=…,
  expect=(version, sha256), facility=…, record=False)`.
- **Operator:** `user-<user pk>`, so the actor is `packs:user-<pk>`. This fits the existing
  operator rule (1-64 of `[A-Za-z0-9._@-]`). `check_operator` reserves the `user-` prefix, so
  a CLI operator cannot pose as a staff decision. The names `startup`, `check`, `facility-task` and
  `facility-beat` are reserved too (`management/commands/ihr_pack_apply.py:48`); the last two
  are the actors `packs:facility-task` and `packs:facility-beat` of b3 (`tasks.py:47-48`).
- **Reason:** `engine.normal_reason` (one line, 1-300 characters, a role or a ticket). The
  view adds it to the audit context once (`audit.extend(job="packs", via="api", action, item,
  reason)`), as `ihr_pack_pending` does. The engine never re-adds it.
- **Expect:** `decide` refuses with a named error when the record's pending version or sha256
  is not the one sent (the content changed since the admin looked).
- **record=False (new):** the web app runs as `ihr_app`, which only has SELECT on
  `PackRelease`. `prepare(record=False)` only reads; a release not yet recorded is refused
  ("this update is not released here yet") instead of trying an INSERT.
- **Idempotent replay:** inside the lock, if the record is already where the action leads
  (accept: `current`, `applied_version == version`; keep: `kept`, `pending_version ==
  version`), the answer is 200 with `"unchanged": true` and nothing is written (no
  `PackApply`, no audit entry).

### 2.5 Lock, transaction and answers

- The view takes `runner.exclusive(hospital.slug)` around `decide`. It never waits.
- **The view is `transaction.non_atomic_requests`.** Otherwise CARE's ATOMIC_REQUESTS would
  commit *after* the lock is released, and a pack apply starting in between would read and
  overwrite the uncommitted state. `decide`'s own `transaction.atomic()` commits inside the lock.
- The view also refuses a hospital that is not ACTIVE (409). The release gate already answers
  503 for held or restoring hospitals before the view runs.

| Case | Status | Body |
|---|---|---|
| done | 200 | the item as in the detail |
| replay | 200 | the item, `unchanged: true` |
| bad body, bad reason | 400 | `{field: message}` |
| not a hospital-wide admin | 403 | fixed text |
| no such item, not pending/kept, deleted facility | 404 | fixed text |
| stale version/sha, newer release not applied yet, blocked | 409 | `{detail, stale|blocked: true}` |
| lock busy (pack apply, release, dump, restore) | 409 | `{detail, busy: true}`, `Retry-After: 60` |

- The busy text never names the other job.
- **Limits:** at most 30 decisions per user per minute, answered 429 beyond that. The counter is
  bumped before the body is validated, so refused and invalid requests count too (`api.py:315`,
  `411-418`). Every 429 carries `Retry-After: 60`, as the busy 409 does (`api.py:229`).

### 2.6 The visible change list (Q-K61)

- New column `PackApply.changes` (JSON list, migration `0002`). Written only with an `applied`
  row: `[{item, facility, change}]`, where `change` is `created`, `updated`, `restored`,
  `repaired`, `retired` or `pending`, and the record-only `resolved` (a
  waiting or kept change the pack withdrew, or the row now holds), `adopted` (a row that
  already held the pack's content) and `kept` (a kept item following a newer release); every
  write of an applied row is listed (review T2). A new form version is listed as `updated`
  (`engine.py:962`) with extra keys: `previous` (the old slug), `links: "copied"`, `copied` and
  `stranded`; a first version has `links: "linked-to-hospital"` (`forms.py:500-546`). At most 500 entries plus
  `changes_total` in `counts`. Decisions keep `counts = {item, reason}`.
- A re-apply that writes nothing still writes nothing.

### 2.7 Files to update

- `ihr_access/tests/role_matrix.txt`: the GET rows and the POST row, `all:deny` (no one in the
  matrix world is a hospital-wide admin, as for the audit rows); `list_params.txt`:
  `updates/ none`, `history/ none` (filters tested in ihr_refdata); `role_matrix_writes.txt`:
  POST answers `auth`; `role_matrix_keys.txt`: nothing (every role refused).
- `anonymous_allowlist.txt`: nothing (the crawl must find the routes refused signed out). No
  new tables: `PackItem` and `PackApply` stay `dml`.
- `scripts/isolation-mutation.sh`: row `packs-api-gate` (gate → `AllowAny`; the admin-only test
  and the role matrix must fail).

### 2.8 Tests (`ihr_refdata.tests.test_api`; tenant mode, `IHR_DB_ROLES=test`)

- Gate: every matrix role, a facility admin and a service account get 403, anonymous 401/403,
  a hospital-wide admin 200.
- Paging and filters; the detail for each reason (edited, unknown-origin, removal, deleted,
  conflict) shows the right three columns and never a non-pack field.
- Accept and keep end to end; bad reasons; actor `packs:user-<pk>`; the CLI refuses
  `--operator user-1`. Stale → 409, no write; replay → 200, zero audit entries; lock held by
  a second connection → 409 with `Retry-After`, nothing written.
- Commit inside the lock: a patched `runner.exclusive` asserts, on exit, that a second
  connection already sees the new state. With the view made atomic this test fails (a
  mutation row too).
- Audit: the trigger entries carry `job=packs, via=api, action, item, reason, pack,
  version`; the request entry has purpose `admin`.
- An unrecorded release is refused, and no INSERT on `PackRelease` is tried as `ihr_app`.

## 3. P1-T15b2: the handler contract and two hospital-wide kinds

### 3.1 The contract (`ihr_refdata/kinds.py`)

The contract is the docstring of `ihr_refdata/kinds.py` (lines ~8-58); that file is
authoritative, and the table below is the b2a core. `valuesets.py` stays a module and only
gains attributes; the engine reaches every kind through `kinds.get(kind)`, which fills defaults.
A test checks every registered kind against the contract. Later work added hooks that the table
does not list: `record_target`, `change`, `notes`, `lock`, `missing_link`, `RECHECK_CONFLICTS`,
`data_checks`, `run_data_checks`, `watermark`, `admin_reason`, `LINKS`, `linkable` and
`stale_hash`, and a required `decision_hash` and `FIELDS`.

| Name | Meaning | valueset |
|---|---|---|
| `KIND`, `SCOPE` | `"hospital"` or `"facility"` (b3) | `"hospital"` |
| `RESTORE_DELETED` | restore a soft-deleted row (True) or make it pending `deleted` (False) | True (C8) |
| `check_item(item)` | shape only, no database (prepare runs in `public`) | as now |
| `check_release(versions)` | optional, across all releases of a pack | none |
| `item_hash`, `row_hash`, `retired_hash` | as now | as now |
| `target(item)` | natural key of the row the item writes | the slug (stored as `""`) |
| `rows(keys, targets, facility=None)` | `{key: row}` (deleted rows included) | as now |
| `conflict(key, row, item)` | `""` or a reason that blocks writing | `""` |
| `all_present(keys, …)` | nothing to do without a pack change | as now |
| `create`, `write(row, item, restore=)`, `retire`, `is_retired(row)` | writes, always `.save()` (post_save signals fire) | as now; `is_retired` replaces `valuesets.RETIRED` in engine.py:352 |
| `usable`, `make_usable` | optional | as now |
| `FIELDS`, `shown(row=, item=, retire=)`, `label(row, item)`, `effect(row, item)` | for the b1 difference (`FIELDS` is a tuple of names; `shown` returns the fields as compared) | the four fields, `name`, `create/update/restore/retire` |

- Engine changes are mechanical. The registry replaces `HANDLERS` and `manifest.KINDS`.
  `row.deleted` with `RESTORE_DELETED=False` goes to pending `deleted` (accept restores, then
  writes). `conflict()` is checked before every create or write, and again in `decide`. A
  blocked item is pending with reason `conflict` and is re-checked at every apply.
- `PackItem` gains `target` (CharField 255, blank) and the reasons `deleted` and `conflict`
  (migration `0003`). Value sets keep `target = ""`.
- `all_present` for kinds that do not restore counts rows that exist in any state. A hospital
  that deleted a pack form should not re-run the reconcile at every start; its record is
  already pending.

### 3.2 `identifier_config` (hospital-wide PatientIdentifierConfig, `facility` null)

- **Key:** `config.system`. The pack item's `slug` is the system itself; `check_item` refuses
  any difference. A system is never changed: it is the identity of every PatientIdentifier
  pointing at the row, so a new system is a new item plus a removal.
- **Fields set:** `status` and the whole `config` (use, description, system, required,
  unique, regex, display, retrieve_config, default_value, auto_maintained).
- **Hash:** canonical JSON of `{status, config}`, where config is
  `IdentifierConfig.model_validate(config).model_dump(mode="json")` (defaults filled, so
  spelling differences vanish). An invalid stored config is hashed as it is.
- **check_item:** CARE's `IdentifierConfig` (pure: it also checks `default_value` through
  CARE's dummy evaluator); `regex` compiles; status in draft/active/inactive.
- **Rows:** live rows with `facility IS NULL AND config->>'system' = key`; deleted ones only
  when there is no live one (the newest).
- **Conflicts (nothing written, pending `conflict`):**
  - two or more live hospital-wide rows with the system;
  - a facility-level row with the system (CARE's own create would refuse it);
  - Q-K62: `unique` turning true while live PatientIdentifier values repeat, or `regex`
    changing while a live value of that config no longer matches (checked with Python `re`,
    as CARE does). The difference shows counts only.
- **Removal:** `status = "inactive"` (CARE has no "retired"). Old values keep resolving.
- **Deleted row:** pending `deleted` (Q-K63). Accept restores it with the pack content.
- **Cache (Q-K64).** `model_from_cache` (Redis, tenant-prefixed, cleared by CARE's
  `post_save` for id and external id) is right in every process when the engine writes with
  `.save()`. But `PatientIdentifierConfigCache.get_config` keeps a per-process dict nothing
  ever clears: after any update, every other web process shows the old display, regex and
  retrieve options until restart (CARE's own update has the same fault). Fix: `ihr_platform`
  replaces `get_config` at app start with `model_from_cache(PatientIdentifierListSpec,
  external_id=…)` (same output); a system check refuses to start without the swap; the
  isolation test at test_isolation.py:480 still passes; mutation row `identifier-config-cache`.

### 3.3 `questionnaire` (forms)

- **Pack item:** `{slug: <form key>, form_version: <int ≥ 1>, title, description,
  subject_type, status (active|draft), styling_metadata, questions}`. The key is 2–30
  characters of CARE's slug pattern, lower case (`forms.py:55`; it was 40 before the attack,
  see "After the attack" item 3).
- **A new version is a new form (Q-K65):**
  - the CARE row's slug is `ihr-pack-<key>-v<form_version>` (with a `-2`…`-9` suffix when a
    foreign row holds it; "After the attack" item 3), its `version` is `str(form_version)`, and
    `PackItem.target` names it;
  - `write()` never updates content in place: it creates the new row, copies the previous
    version's `QuestionnaireOrganization` and `QuestionnaireFacilityOrganization` links with
    `.save()` (CARE's caches sync), then sets the previous row's `status = "retired"`;
  - the first version gets no links. It is invisible (CARE shows a form only through its
    organisation links) until the admin links it; the change list says so;
  - answers keep pointing at the old row, which never changes, so old answers render as they
    were given (`render_responses` reads the current form).
- **Immutability:** `check_release` refuses (PackError, fleet-wide) when one
  `(key, form_version)` has two different hashes across a pack's releases, or when a key's
  form_version goes down. A content change must come with a new form_version.
- **Hash and "edited":** canonical JSON of form_version, title, description, subject_type,
  status, styling_metadata and questions (`forms.py:103-113`). Org links are the hospital's own and never counted. "Edited"
  means the row named by `target` differs from what the pack wrote. Today only a superuser
  can edit in place, and superusers are refused on hospital hosts
  (`IHR_STAFF_REFUSE_SUPERUSERS`); the rule still holds for the shell and future upstream
  changes.
- **Adopt and conflict:** the engine never adopts a foreign row: `rows()` finds only the row the
  record's `target` names (`forms.py:296-310`). A foreign row at the slug is skipped for the
  next free `-2`…`-9` suffix; only ten taken slugs are a `conflict`. (Changed after the attack:
  the first design adopted an equal-content row; see item 3.)
- **Removal:** the current row is retired. **Deleted row:** pending `deleted`.
- **check_item** checks shape only: types, unique `link_id` and `id` in the nested tree, slug
  pattern. Full CARE validation (coded fields may touch the database) runs in a test on every
  committed form pack, in a test hospital.

### 3.4 The guard: an answered form is never edited in place (Q-K66)

- **Where:** a database trigger, `ihr_questionnaire_guard`. It covers CARE's API (if upstream
  relaxes the superuser rule), the shell, raw SQL as `ihr_app`, and our own engine. A viewset
  swap would cover the API only. Function and trigger follow the `ihr_role_placement_guard`
  pattern: owner-owned, `REVOKE ALL FROM PUBLIC`, kept current by post_migrate, listed for
  the db-role function check, with a fingerprint check in `problems()`.
- **BEFORE UPDATE on `emr_questionnaire`:** if any frozen column is `IS DISTINCT FROM` its old
  value and an answer exists (`emr_questionnaireresponse` or `emr_formsubmission` with that
  `questionnaire_id`), it raises
  `ihr: form <slug> has answers; make a new version instead`.
  - Frozen: `slug, version, title, description, subject_type, styling_metadata, questions,
    external_id`.
  - Free: `status`, `organization_cache`, `internal_organization_cache`, `deleted`,
    `modified_date`, `updated_by`, `created_by` (SET_NULL when a user goes), `history`, `meta`.
  - A test classifies every column of the table, so a new upstream column fails until it is
    listed.
- **BEFORE DELETE** (a hard delete) with answers is refused too: CASCADE would delete patients'
  answers. A soft delete is free.
- **The race.** An UPDATE takes FOR NO KEY UPDATE on the form row, and a foreign-key check
  only takes FOR KEY SHARE, so an answer being inserted could commit against content changed
  at the same moment. Fix: a trigger BEFORE INSERT on both answer tables runs `SELECT 1 FROM
  emr_questionnaire WHERE id = NEW.questionnaire_id FOR SHARE` (insert only, `forms_guard.py:200-203`;
  an UPDATE of `questionnaire_id` is refused outright, see "After the attack" item 4). That lock conflicts with the guard's update, so one waits for the other and the
  guard's check sees the committed answer.
- **How a hospital changes a form:**
  - a pack form changes only by a new pack form_version (§3.3);
  - for its own forms the hospital creates a new form through CARE's create (new slug) and
    links it; the old one is retired by status. A "new version of our own form" action
    belongs to P1-T24's screens (out of scope).

### 3.5 Tests and mutation proofs (b2)

- Contract test over every registered kind. All P1-T15a value-set tests stay green unchanged
  (that proves the generalisation did not break `valuesets.py`).
- **Identifier:** every rule row and conflict; removal → inactive; deleted → pending → accept
  restores; a re-apply writes nothing and audits nothing; a save is seen at once (Redis, empty
  local dict).
- **Forms:** v1 (no links); v2 → new row, v1 retired, links copied, v1's content unchanged;
  edits of an answered form through CARE's viewset (superuser, in a test), the ORM and raw SQL
  all refused; status and link changes allowed; hard delete refused; immutability PackError;
  adopt and conflict; CARE's spec over every committed form pack.
- **Race:** connection A inserts an answer without committing; connection B updates a frozen
  column with `lock_timeout = 1s` and must time out; after A commits, B's retry is refused.
- **Mutations** (rows in `scripts/isolation-mutation.sh`): `forms-guard` (no trigger),
  `forms-answer-lock` (no FOR SHARE), `forms-in-place` (`write()` updates the old row),
  `identifier-config-cache` (CARE's `get_config` back). Each named test must fail.

## 4. P1-T15b3: per-facility kinds

- **Scope.** A kind with `SCOPE = "facility"` is applied once per target facility. The pack
  file says nothing extra; the kind decides.
- **Which facilities (Q-K67):** every facility of the hospital that is not deleted.
  - A facility created later gets the items from a Celery task queued on commit of its
    creation (Facility `post_save` created, tenant-aware Celery). The task takes the hospital
    lock; if busy, it retries 3 times with backoff and then leaves the facility to the 15-minute beat job
    `ihr_refdata.fill_facilities`, which is the backstop (`tasks.py:209-216`; the next start-up
    was the first design's backstop);
  - the task, the start-up and `ihr_pack_apply` all call the same engine function.
- **PackItem keying (migration `0004`):** a nullable `facility` FK (PROTECT); `item` = the
  pack key (CARE's `slug_value`); `target` = CARE's stored slug `f-<facility external
  id>-<key>`; the unique constraint becomes `(pack, kind, facility, item)` with
  `nulls_distinct=False` (Django 6.1, Postgres 18), replacing `ihr_packitem_one_per_item`;
  a new reason `missing-link`.
- **Engine:** `_reconcile(release, handler, facility)` runs once per target facility (by id),
  each with its own records and rows; one `PackApply` row per pack per run, its `changes`
  naming the facility; the sha256 short-circuit also needs records in every live facility and
  `all_present` per facility; `decide` takes `facility`, and `ihr_pack_pending` gains
  `--facility <external id>`.
- **Links inside one facility:**
  - a kind declares `LINKS = {field: target_kind}`. Pack items hold link targets as pack keys;
    the handler resolves them to rows of the *same facility* with slug
    `f-<facility>-<key>`, not deleted, whatever their origin (CARE's own rule,
    activity_definition.py:85). It stores ids in CARE's arrays;
  - hashes use link keys (ids mapped back to keys by stripping the prefix), never ids, so
    every facility hashes alike. List order is kept;
  - **resolution order:** a pack names the packs it links into, in `after: [packs]`.
    `prepare()` sorts packs by that order (ties by name) and refuses, as a fleet-wide
    PackError, a cycle, an unknown pack, or a `LINKS` target kind with no pack in `after`.
    One pack failing stops the later packs of that hospital (as now);
  - **a missing target** in that facility (deleted, kept away, or never created): nothing is
    written, the item is pending `missing-link`, every apply tries again automatically, and
    accept is refused until the target exists (Q-K68).
- **Removal:** retire per facility. A target that leaves its pack is retired; links to it stay.
- **A deleted facility** (CARE soft delete) is skipped. Its records and rows are left as they
  are, hidden from the API, and resumed if the facility is restored. A hard delete is
  already blocked by CARE's PROTECT on catalogue rows, and by ours.
- **Test-only kinds (Q-K70):**
  - `t_specimen` (SpecimenDefinition: title, status, a minimal `type_collected`) and
    `t_activity` (ActivityDefinition: title, status, fixed classification, kind and code, with
    `LINKS = {"specimen_requirements": "t_specimen"}`);
  - handlers in `ihr_refdata/tests/kinds.py`, registered only when `IHR_REFDATA_TEST_KINDS`
    is on (test settings only). The deployment settings refuse it (`secure.py`), and the
    production `prepare()` refuses an unknown kind, so a test pack can never be applied on a
    server.
- **Tests:** two facilities get both packs with the right ids, and a re-apply is silent; a
  facility created later is filled by the task, or by the next apply when busy; links never
  cross facilities (same keys, different ids); a deleted target → `missing-link` → restored →
  created at the next apply; `after` order and its refusals; a deleted facility is skipped and
  hidden; the API's facility filter; an edit in one facility leaves the other automatic; the
  unique constraint with null and non-null facilities.

## 5. Risks and questions (auto-accepted, awaiting the owner; each easy to reverse)

| Q | Question | A ⭐ (taken) | Other option | Reason |
|---|---|---|---|---|
| Q-K60 | Who decides pack items | hospital-wide admins only, every scope | B: facility admins for their facility's items | one reviewed gate; no facility content ships yet; B fits P1-T24 |
| Q-K61 | The visible change list (Q-T10) | per-item `changes` on each applied `PackApply` (≤500 + total) | B: counts only, details in the audit trail | Q-T10 asks for a list an admin can read |
| Q-K62 | Identifier changes that could break data | automatic unless a data check fails (`unique` on with repeats, `regex` changed with non-matching values) → pending `data-check`, counts stored, accept refused until fixed, keep allowed (item 8) | B: always wait for the admin; C: always automatic; D: accept with acknowledged counts | keeps "automatic for unedited"; CARE re-validates identifiers on every patient edit, so D breaks front-desk edits |
| Q-K63 | A soft-deleted identifier config or form | a local edit: pending `deleted` only when the pack's content changes; keep stays kept; accept restores (item 11) | B: restore automatically, as value sets; C: pending at every apply | CARE binds value-set slugs in code, not these; deleting one is the hospital's choice |
| Q-K64 | The never-cleared `get_config` dict | our plugin swaps in CARE's Redis-backed `model_from_cache`; refused at start if missing | B: restart web processes after a change | CARE's own edits have the same fault; a restart rule gets forgotten |
| Q-K65 | Form versions | slug `ihr-pack-<key>-v<N>`; old one retired by status; v1 linked to the hospital's top-level organisations, later versions copy the previous links (items 1-3) | B: one slug moved to the newest row; C: v1 unlinked | slugs are unique and old answers must keep their form; an unlinked form is invisible to everyone on a hospital host |
| Q-K66 | Where the answered-form guard lives | database triggers: form-side frozen columns and hard delete; answer-side hard delete refused, link fixed after insert, FOR SHARE on insert (item 4) | B: a viewset swap | covers every writer, CARE's commented-out check and Django's cascade included |
| Q-K67 | Which facilities get a per-facility pack | every live facility; new ones by a deduplicated on-commit task (facility packs only), a 15-minute beat job as backstop (item 14) | B: the admin enrols facilities; C: start-up as the only backstop | CARE's catalogue is per facility; start-ups happen only on deploy |
| Q-K68 | A link whose target is missing | write nothing, pending `missing-link`, retried automatically | B: write without that link | a lab test without its specimen is wrong in a way staff would not see |
| Q-K69 | Prices in packs | never: ChargeItemDefinition prices and stock stay the hospital's; a pack may ship definitions without price components | B: packs ship prices | money is the hospital's decision, and an unedited price would change "automatically" |
| Q-K70 | How b3 is proven | real CARE models with test-only handlers (§4) | B: a test-only model and migration | proves CARE's slug prefix, id arrays and PROTECT; no migration |
| Q-K71 | Identifier changes that loosen patient search | pending `privacy` whatever the edit state (item 7) | B: automatic for unedited rows (Q-T10 as written) | a pack must not widen who finds a patient by identifier without the hospital's OK |
| Q-K72 | A foreign form already at a pack form's slug | reserved `ihr-pack-` namespace; else the next free `-2`…`-9` suffix, recorded (item 3) | B: block until renamed | renaming is impossible on a hospital host once the form has answers |
| Q-K73 | Drafts and templates on a retired form version | retire at once; the change list counts stranded drafts and templates (item 12) | B: keep v(N-1) active while drafts exist | pack form versions are rare and reviewed; B leaves two live versions |
| Q-K74 | A new auto-numbered identifier replacing an old one | pending `counter-restart` (item 10) | B: automatic, warned in the change list | the new count starts at 0 and desk staff see look-alike numbers |
| Q-K75 | Renamed natural keys (catalogue slugs) | accepted for the test-only kinds; a production facility kind binds by `external_id` first (item 13) | B: bind now in b3 | no facility content ships in P1-T15b; binding lands with the first real kind |

- **Risks:** a pack with any pending item is reconciled (reads only) at every start, as in
  P1-T15a; the guard adds one row lock per answer saved; a CARE upgrade may add questionnaire
  columns (the classification test catches it) or change `get_config` (the system check
  catches it); pack files are read and hashed per decision (small; a per-process cache keyed by
  the manifest sha256 is allowed, as it holds repo files, not hospital data).

## 6. File plan and effort

| Subtask | Files | Builder effort |
|---|---|---|
| b1 | `ihr_refdata/{api.py, diff.py, urls.py}` new; `engine.py` (`decide`: expect, record, facility hook, replay; `changes`); `models.py` + `0002`; `ihr_pack_apply.check_operator` (`user-` reserved); `ihr_identity/staff_access.py` (`IsHospitalAdmin` moved); `ihr_audit/views.py` (import); `ihr_platform/urls_tenant.py`; the matrix files of §2.7; `scripts/isolation-mutation.sh`; `tests/test_api.py` | one sitting, ~700 lines with tests; Opus · high builder (access), Opus · high review |
| b2a | `kinds.py` new; `valuesets.py` (attributes only); `engine.py` (registry, conflict, restore flag, target); `manifest.py` (kinds from the registry); `identifiers.py` new; `models.py` + `0003`; `ihr_platform` cache swap + system check; `test_isolation.py`; tests | one sitting, ~800 lines; Opus · high |
| b2b | `forms.py`, `forms_guard.py` (trigger SQL, post_migrate, `problems()`); db-role function list; mutation rows; `test_forms.py`, `test_forms_guard.py` | one sitting, ~800 lines; Opus · high (data integrity) |
| b3 | `engine.py` (facility loop, `after` order, short-circuit); `manifest.py` (`after`); `models.py` + `0004`; `tasks.py` + Facility signal in `apps.py`; `tests/kinds.py`; settings flag + `secure.py`; `ihr_pack_pending --facility`; `test_facility.py` | one sitting, ~700 lines; Opus · high builder, Sonnet · high tests |

- Every subtask runs `ihr_refdata ihr_refdata_registry ihr_access ihr_audit` in tenant mode
  with `IHR_DB_ROLES=test`. b1 also runs the role matrix; b2 and b3 also run the isolation suite.
- Docs per subtask: TRACKER, CLAUDE.md's "Reference packs" line, QUESTIONS (Q-K60…Q-K75),
  UPSTREAM_PATCHES only if the cache fix touches `apps/` (§3.2 avoids that).

## After the attack (2026-11-16): the design as it is built

The attack round raised 25 findings (S: security and integrity, C: correctness), and a
challenger tried to refute each one. Result: 1 refuted (S10: a retired form takes no answers),
3 confirmed (C1, S11, S12), 21 partly. One was high (C1: no one could ever see a pack form), four
medium (S1, S2, S3, C2), the rest low; none was fatal. Every confirmed or partly confirmed
finding changes the design as follows. Where this section and the sections above disagree, this
section wins.

1. **A pack form's first version is linked to the hospital (C1, Q-K65).** Without links CARE
   hides a form from everyone but superusers, who are refused on hospital hosts, so nobody could
   link it. At create, v1 gets a `QuestionnaireOrganization` row (`.save()`, the cache syncs) for
   each of the hospital's top-level organisations (`parent` null, active, not deleted: the ones
   `hospital_wide_admin` uses). The change list says `linked-to-hospital`. The admin then widens
   or narrows the links through CARE's own `set_organizations`. Later versions copy the previous
   version's links as before. Test: as a hospital-wide admin, the v1 form is listed and its links
   are set through CARE's viewset.
2. **The form handler contract (C2).** `rows()` resolves by `record.target` when a record exists
   and by `target(item)` only for a first create or adoption. The `done`/`note` dict carries
   `target`, so the engine moves `PackItem.target` in the same transaction as the write.
   `write()` branches: same `form_version` → undelete only (no content write, no new slug);
   newer `form_version` → create, copy links, retire the old row. `decide` passes the record's
   target to `rows()`. The adopt/conflict check on the new slug includes soft-deleted rows (the
   slug's unique index covers them). Test: v1 → v2 → re-apply writes nothing and
   `PackItem.target` is the v2 slug; a deleted v2 accepted is undeleted, not duplicated.
3. **Pack form slugs are a reserved namespace (S8, C12, Q-K72).** The CARE slug is
   `ihr-pack-<key>-v<N>`; the key is 2-30 characters of CARE's slug pattern, lower case, and
   `form_version` 1-999, so the slug fits CARE's 5-50. The questionnaire guard (item 4) refuses
   an INSERT, or an UPDATE of `slug`, into `ihr-pack-%` unless the transaction set
   `ihr.pack_write = on` (`set_config(…, true)`, only in the engine's form writes); CARE's
   create then answers an error, not a stolen name. The engine adopts only a row its own record
   names, never a foreign row. A foreign row already at the slug (made before b2b, or by the
   shell) is not a block: the engine takes the next free `-2` … `-9` suffix, records it in
   `target` and lists it; ten taken slugs is a `conflict`. Nobody is ever asked to rename.
4. **The answered-form guard sits on the answers too (S1, S2, Q-K66).**
   - A BEFORE DELETE trigger on `emr_questionnaireresponse` and `emr_formsubmission` refuses
     every hard delete (soft delete through `deleted` stays free). Django's cascade deletes the
     answers before the form, so the form-side check alone saw none; that check stays as well.
     An owner-only erasure path, if one is ever needed, will be a SECURITY DEFINER function.
   - The same answer-side trigger refuses any UPDATE where `questionnaire_id IS DISTINCT FROM
     OLD.questionnaire_id`: the link is fixed after insert (CARE sets it only on create), so
     detach, edit and reattach is impossible. The FOR SHARE lock now runs BEFORE INSERT only.
   - Tests: `Questionnaire.objects.filter(pk=…).delete()` and
     `QuestionnaireResponse.objects.filter(…).delete()` as `ihr_app` are refused; the detour
     (response to NULL, submission to another form, edit, back) is refused at its first step.
     Mutation rows `forms-answer-delete` and `forms-answer-relink`.
5. **Restores accept the new guards (S3).** `ihr_questionnaire_guard` and the answer-side
   function and triggers join `OUR_FUNCTIONS` / `OUR_TRIGGERS` in `restore/package.py`, never
   taken from a dump: this build's code installs them in the restore's migrate step after the
   load, like the placement guard, so restored rows are not re-judged. b2b's file plan adds
   `restore/package.py` and `test_restore` cases: a dump with the guards restores; a dump with
   a tampered guard body is refused.
6. **The decision API checks before it locks (S4, S9).**
   - Without the lock: body, record exists and is pending or kept, `expect` against the record,
     blocked, release recorded. `prepare()` runs outside the lock, cached per manifest sha256.
     Inside the lock the record and the row are checked again, then written.
   - The detail shows `row_sha256` (the hospital row's hash), and the POST sends it in `expect`
     with `version` and `sha256`; a different row hash is 409 `stale`, nothing written (Q-T10's
     OK covers the edit the admin saw). Test: edit the row through CARE between GET and POST.
   - One decision in flight per hospital and at most 30 per user per minute (Redis keys with
     the schema prefix); beyond that 429. Busy answers keep `Retry-After: 60`. Both limits are
     in the role-matrix and isolation runs.
7. **Identifier changes that loosen disclosure wait for the admin (S5, Q-K71).** Whatever the
   edit state, the item is pending `privacy` when an active config's `retrieve_config` loosens
   (`retrieve_partial_search` turns true, or `retrieve_with_dob`, `retrieve_with_year_of_birth`
   or `retrieve_with_otp` turns false), when a config with such options turns active, or when a
   new active hospital-wide config is created with partial search or no retrieve check.
   `check_item` refuses CARE's own systems (`system.care.ohc.network/…`).
8. **Data checks are a separate reason, stored, bounded and never run in a GET (S6, C6, C7,
   Q-K62).**
   - Structural problems (two live rows, a facility-level row with the system, ten taken form
     slugs) stay `conflict`. Q-K62's findings (`unique` on with repeats, `regex` changed with
     non-matching values) become reason `data-check`. Accept stays refused for both: CARE
     re-validates every identifier a patient edit sends, so taking the change would make those
     patients' demographics edits fail. Keep is allowed. The detail says "N patients' edits
     would fail until fixed".
   - The counts are stored on `PackItem` (`checks`: counts, the `pending_sha256` they were made
     for, and a watermark: max id and modified date of the config's identifiers). An apply
     recomputes only when the pack content or the watermark moved; `ihr_pack_pending --recheck`
     forces it. The detail shows the stored counts and their age.
   - The repeat check is one SQL `GROUP BY` under `statement_timeout = 30s`; the regex check
     streams values in batches with a 60 s cap (Python `re`, as CARE). Over a cap the item stays
     `data-check` with "not fully checked" and the log line `ihr-packs: hospital=<slug>
     outcome=failed` (alerted). A ReDoS lint is not taken: pack regexes are reviewed files.
9. **Duplicate systems (S7).** The engine takes `pg_advisory_xact_lock` on
   `hashtext(schema || system)` around its conflict check and write, and re-checks after commit
   (a duplicate found then makes the item `conflict`). CARE's own writers still race each other
   as upstream; no trigger is put on CARE's table. `unique` stays advisory, as in CARE.
10. **A generator restart needs the admin (C8, Q-K74).** A `default_value` change applies
    automatically (the count continues on the same config). A new `auto_maintained` config
    created in a hospital where the same pack release retires an `auto_maintained` config that
    has values is pending `counter-restart`, with the old config's count shown.
11. **Deleted rows and the start-up short-circuit (C5, Q-K63).** For kinds that do not restore,
    a deleted row is handled like an edit (P1-T15a C4): kept stays kept; when
    `record.applied_sha256` equals the pack's hash the deletion is a counted local edit, the
    record stays `current` and nothing is pending; it becomes pending `deleted` only when the
    pack's content for it changes. The short-circuit skips the reconcile when every pending
    record waits only on the admin (`edited`, `unknown-origin`, `deleted`, `privacy`,
    `counter-restart`, `removal`); it re-runs for `conflict`, `data-check` (subject to item 8)
    and `missing-link` (first one query per facility for the targets' existence; the full
    reconcile only when one now exists). §3.1's "should not re-run" sentence is replaced by this.
12. **The previous form version and what still points at it (C9, Q-K73).** Retiring v(N-1) stays
    immediate. The change list and the item detail name what is stranded: draft
    `FormSubmission` rows on v(N-1) and response templates keyed to its slug (counts only).
    Favourites are not moved (the web app does not use them for forms).
13. **Row lookup for every kind (C4, C3, Q-K75).** `check_item` requires lower-case keys.
    `rows()` matches `lower(slug)` (within the facility for facility kinds), prefers the live
    row (newest id), counts deleted rows only when no live one exists, and makes two or more
    live matches a `conflict`; link resolution uses the same rule. Tests: a case-variant row and
    a deleted-plus-live pair. A hospital can rename a catalogue slug (CARE recomputes it from
    `slug_value`), which would make the engine create a second row: the test-only kinds accept
    this limit; a production facility kind (b4+) must first bind each `PackItem` to the row's
    `external_id` (a renamed bound row is pending `edited`, never a create).
14. **New and restored facilities (S13, C10, Q-K67).** The on-commit task passes `record=False`
    (an unrecorded release is refused), applies only facility-scope packs and only to that
    facility, and is deduplicated per hospital (one queued task, a schema-prefixed Redis key).
    It never applies hospital-wide packs. A give-up logs `ihr-packs: hospital=<slug>
    outcome=deferred facility=<id>`, alerted after two hours. A beat job every 15 minutes per
    ACTIVE hospital applies facility-scope packs to live facilities that have no record for the
    current release (one query when there are none); it is the backstop for give-ups and for a
    restored facility. Tests: lock held, task gives up, the beat run fills it; a restored
    facility is brought up to date.
15. **Tests that cross connections (C11).** The commit-inside-the-lock test (§2.8) and the
    answer-race test (§3.5) are `SimpleTestCase(databases="__all__")` with committed fixtures in
    a dedicated tenant and `connections.create_connection`, as `test_restore.py` does. They use
    unique keys per run, never cleanup (the audit trail is append-only). Mutation rows name
    them: `packs-api-atomic` and `forms-answer-lock`.
16. **Mutation rows and the cache test (S11).** The new row is `identifier-config-swap` (the
    existing `identifier-config-cache` row is CARE's signal cache and stays). Row
    `identifier-lookup-cache` (patient.py `_key`) is retargeted to the tenant prefix of the
    swapped function's Redis key, or retired with a reason when another row already proves that
    prefix. The isolation test's cleanup deletes both tenants' Redis keys, not only the local dict.
17. **Replay wording (S12).** A replay writes no `PackApply` and no trigger (row) entries. The
    test asserts exactly one request entry, purpose `admin`, status 200, and nothing else.
