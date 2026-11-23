# P1-T31 design attack (2026-11-23)

Lenses: D = egm-design, Z = egm-zoom-out, R = egm-implementation-readiness (Sonnet high),
S = Opus tenancy/security (high). Design: `design.md` (not edited). Synthesis by Opus high.
Every high finding was spot-checked against the code; results are noted under each one.

Counts after de-duplication: **4 high, 12 medium, 14 low** (30 findings from 51 lens items).

## High

### S1 (high) — the new SECURITY DEFINER trigger function is unknown to db_roles
- Evidence (checked): `db_roles.py:167-172` lists only the audit functions in `FUNCTIONS` and
  `SECURITY_DEFINER`. `_read_functions` (`db_roles.py:560-576`) puts *every* definer in
  `state["definers"]`, trigger functions included. `schema_findings` (`db_roles.py:781-790`)
  turns each one into a `problem`. The restore smoke step fails on any problem
  (`restore/engine.py:1827-1834`), and `ihr_fleet_run check` and `staging.sh db-roles --assert`
  report it too. The design says only "db_roles must not give the app EXECUTE".
- Fix: add `"ihr_directory_touch": set()` to `FUNCTIONS` and the name to `SECURITY_DEFINER`,
  so `_function_acl` checks owner = `ihr_owner`, no app EXECUTE, and the pinned `search_path`.
  Add a `test_db_roles` case: the function made INVOKER or granted to `ihr_app` gives a problem.

### S2 (high) — single-hospital restores of any dump taken after the change are refused
- Evidence (checked): `restore/package.py:50-74` `OUR_FUNCTIONS` / `OUR_TRIGGERS` are the only
  functions and triggers a dump may hold. Anything else gets "a function/trigger that is not
  ours" (`package.py:256`, `:270`). Every nightly dump after P1-T31 holds `ihr_directory_touch`
  and its three triggers, so every restore would stop at the quarantine.
- Fix: add the function to `OUR_FUNCTIONS`, and add `ihr_directory_link`, `ihr_directory_user`
  and `ihr_directory_truncate` → `ihr_directory_touch` to `OUR_TRIGGERS`. Never export them:
  the restore's migrate reinstalls them through `ensure_directory`. Add a case to
  `scripts/test_restore_quarantine.py` and to the live test.

### S6 (high; merges R1) — service-account tokens are not refused
- Evidence (checked): `DEFAULT_AUTHENTICATION_CLASSES` includes `ServiceTokenAuthentication`
  (`settings/tenancy.py:164-167`). With only `(NotCrossSite, IsAuthenticated)`, a DRF service
  token passes and gets 200. `IsHospitalAdmin` has to add `not user.is_service_account` itself
  (`staff_access.py:113`). Q-K147 and test 7 say the token is refused, and §3.4 says a user
  with no `ihr_identity` gets "only the current hospital". The design contradicts itself.
- Fix: set `authentication_classes = [StaffSessionAuthentication]` (as `SessionView` does,
  `views.py:303`), and add a permission that refuses `user.is_service_account`. A user with no
  `ihr_identity` gets 403, not a list. Test 7 asserts the exact status per credential type.
  Add a mutation row (`directory-service-token`) → test 7.

### D1 (high; merges S7, R5, Z12) — E027 as a serving gate is built on a wrong precedent and risks availability
- Evidence (checked): `ihr_refdata.E026` checks the code's wiring only
  (`ihr_refdata/apps.py:33-48`, `forms_guard.static_problems`). It reads no hospital schema.
  The design's E027 scans every `MIGRATE_STATUSES` schema "else refuse to serve". System checks
  run on every `manage.py` command, so the check would fail:
  - on the first start before the triggers exist;
  - for a provisioning schema;
  - for the very commands that repair the drift.

  One hospital's disabled trigger would stop the whole process. That contradicts §3.3 ("a stale
  menu is not a reason to stop serving") and per-hospital gating (TENANCY §6.10). E027 was also
  proposed and dropped in P1-T25.
- Fix: E027 stays **static only**, in the E026/W026 pattern:
  - `ihr_identity.E027`: `ensure_directory` is wired to `post_migrate`.
  - `ihr_identity.W027`: the SQL fingerprint differs from the one the newest `ihr_identity`
    migration states.

  Per-schema trigger presence, enabled state and the function stamp are reported by
  `ihr_staff_directory --check` (see the medium finding on scope) as the log line
  `ihr-directory: hospital=<slug> outcome=drift`. Drift never gates a hospital and never refuses
  the process. Delete the "gated via gated_schemas only if…" hedge.

## Medium

### D2 (high → medium; merges Z1, R2, Z2, Z3 in part) — the triggers are justified, the machinery around them is not
- Spot-check: D2 says the case against signals (option B) is not evidenced. That is
  **refuted**. Our own code changes `is_active` with `queryset.update()`, where no signal fires:
  - `ihr_identity/views.py:269` `.update(is_active=True)` (accepting an invitation);
  - `ihr_identity/join.py:217` `.update(is_active=True)` (Join);
  - `ihr_identity/invitations.py:68-69` `.update(…, is_active=False)`.

  So the trigger is needed **today**, for the exact paths that add and remove a listed link,
  and not only for P4-T8. Z2's "the reason rests on a later task" is answered by this. Record
  these paths as Q-K144 A's reason, and make test 2 call the real accept, join and invitation
  functions.
- Kept from Z1/R2/Z3: a menu does not need a fleet step, `rebuild_gone`, a runtime E027, the
  `since` field, the 50 cap or five-plus test extras.
- Z3 (fold the rebuild into `ensure_directory`) is **rejected**. `ensure_directory` runs on the
  restore's `post_migrate`, which is the abortable `migrate` step, so writing public rows there
  breaks the abort rule (see D6). `ensure_directory` installs the function and triggers only.
- Fix:
  - Drop the fleet step `directory`, `DIRECTORY_STATUSES`, the `finished` ignore edit and the
    start-up/release command-line edits.
  - First fill: the new `ihr_identity` migration (0004) installs the triggers and runs
    `rebuild()` for its own schema, as owner. Each hospital fills when it migrates, and a new
    hospital fills as users are created.
  - Keep `rebuild(hospital)` and one small command, `manage.py ihr_staff_directory
    --check|--repair [--slug S]`:
    - `--check` reports row differences and trigger/stamp drift;
    - `--repair` re-runs `ensure_directory` and `rebuild`, and deletes rows of hospitals outside
      `MIGRATE_STATUSES` (this replaces `rebuild_gone`).
  - Add `--check` to the weekly `staging.sh due` list (from S9).

### D3 (medium; merges Z4) — "about 3 queries" is wrong
- Evidence: `release.schema_ready` (`release.py:390-406`) reads `HospitalSchemaState` once per
  hospital, and `gated_schemas()` is called each time.
- Fix: add `release.servable_many(hospitals)`: one `HospitalSchemaState` read with `pk__in`,
  one `gated_schemas()`, one `code_fingerprint()`. Pin the query count (a constant number,
  independent of N) with `assertNumQueries` for 1 and 3 hospitals.

### D4 (medium; merges Z5, R6, S12) — the browser's sibling-host rule drops valid hospitals and still accepts wrong paths
- Evidence: `Domain` allows any host (`models.py`; `_redirect_uri` works for any hospital
  domain), so a custom domain silently disappears. The rule also accepts any path on any sibling,
  including `auth.<suffix>`.
- Fix: drop the parent-domain rule. The server builds the URL from its own `Domain` rows. The
  client accepts an entry only if:
  - its scheme is the current page's scheme;
  - it has no credentials;
  - its pathname is exactly `/api/ihr/auth/login/`;
  - it has no query or fragment (see D10).

  `hospitals.test.ts` has one case per rule (`javascript:`, credentials, other path, query,
  http on an https page).

### Z6 (medium) — the URL is built by a fifth copy of the template rule
- Evidence: `IHR_FRONTEND_URL_TEMPLATE.format(domain=…)` already appears in `links.py:13-44`,
  `ihr_identity/views.py:58` and `ihr_keycloak_sync.py:144`. `frontend_base_url` falls back to
  any domain, while the design leaves out a hospital with no primary domain.
- Fix: add `links.origin_for(domain)` and have `frontend_base_url` use it. The endpoint uses one
  batched primary-domain query. Rule: no primary domain → not listed. That stays a test 8 case.

### D5 (medium; merges R3, R4) — tests 5 and 6 and mutation row 5 cannot be built as written
- Evidence:
  - The tables carry no hospital value, so "a row value instead of `TG_TABLE_SCHEMA`" has no
    mutant.
  - Test 6 cannot "call the trigger with a sub".
  - Test 5's fallback ("`since` unchanged") proves nothing, because `ON CONFLICT DO NOTHING`
    keeps it anyway.
- Fix:
  - **Test 6:** S is linked only in B, and S is activated in B → the row (S, B) exists and no
    (S, A) exists. A write in A for another sub leaves (S, B) untouched.
  - **Mutation row 5 `directory-schema-bound`:** the function resolves `hid` as the first ACTIVE
    hospital by id → test 6 fails.
  - **Test 5:** one mechanism. As owner, set `track_functions = 'pl'` locally, update
    `cached_role_orgs`, and assert `pg_stat_user_functions.calls` for `ihr_directory_touch` is
    unchanged.

### D6 (medium; merges S3) — restore ordering and abort
- Evidence: `restore/engine.py:79` `ABORTABLE` includes `migrate`. The design puts
  `_directory()` in `_migrate()` (`engine.py:1154`), which rewrites public rows from a copy that
  an abort then discards. No nightly reconcile catches it.
- Fix:
  - Call `rebuild(hospital)` in `_reconcile` (after the point of no return, next to
    `_public_rows()`, `engine.py:1201`).
  - Call it in `abort()` after un-parking, because the triggers installed by the restore's
    migrate may already have fired.
  - Test both in `test_restore`. A failed rebuild in reconcile is logged and does not fail the
    restore; `--repair` is the repair path.
  - State the order: load (no directory triggers, they are never exported) → rename → migrate
    (`ensure_directory` installs) → reconcile (`rebuild`).

### D7 (medium) — migration dependencies and canonical SQL
- Fix: the `ihr_identity` 0004 migration depends on CARE's latest `users` migration and
  `ihr_platform` 0013 (the new table). `ensure_directory`'s SQL is canonical, and 0004 states
  its sha256 (W027 compares them). Add a test that a freshly created tenant has the three
  triggers before any user write. Add a static check that the columns named in the `UPDATE OF`
  list exist on CARE's `User` model (`is_active`, `deleted`, `is_superuser`,
  `is_service_account`, `care/users/models.py:167,170`), so an upstream rename fails the tests.

### S4 (medium) — no rollback rule
- Evidence: after a rollback to a build from before P1-T31, the function and triggers remain.
  That build's `db_roles` reports an unknown definer (S1), and its `OUR_FUNCTIONS` refuses every
  newer dump (S2).
- Fix: "rollback across P1-T31 refused, fix forward", as P1-T19b and P1-T20b did (a
  `release_log.py` marker and a test).

### S5 (medium; merges Z11) — the public table maps people to hospitals, and §7's P4-T8 note does not work
- Evidence: the table holds the raw `keycloak_sub`, which is the same at every hospital. One
  `select` lists every person's hospitals, where today that needs a scan of every schema.
  Inside a SECURITY DEFINER function `current_user` is the owner, so "rows for a sub linked in
  the calling role's own hospital" cannot be decided from `current_user`. `session_user` might
  work, depending on how P4-T8 logs in. Today the shared `ihr_app` reads every hospital schema
  anyway, so nothing new is exposed now.
- Fix: keep the raw sub for now; it is Q-K144's sub-option, and the owner decides. Replace §7's
  note with a correct one in P4-T8's tracker row:
  - the directory needs a hospital-bound read, either a definer read keyed on `session_user`
    (if P4-T8 uses per-hospital logins) or a keyed hash of the sub (`HMAC(key, sub)`, key in
    an owner-only public table and a web secret);
  - correlation across hospitals stays a residual risk.

### R7 (medium) — a passive menu must not toast or redirect
- Evidence: `queryClient.ts:30` installs the global `handleHttpError`.
- Fix: `query(ihrHospitalsApi.list, { silent: true })`. On any error the group renders nothing.
  The existing session-expiry flow handles signed-out pages. Add a component or unit test for
  the error case.

### R8 (medium) — Playwright steps 2 and 6 are not writable as stated
- Fix:
  - **Step 2:** reuse the `signInBySso()` mechanism (`tests-ihr/lib/fixtures.ts:161`). Record
    each navigation with `page.on("framenavigated")` and assert that none lands on Keycloak's
    login form.
  - **Step 6:** `expect.poll(…, { timeout: 10_000 })` for **403** with the not-signed-in body
    (see D14), citing the P1-T5d back-channel test.

### Z7 (medium) — too many owner decisions
- Fix: keep the four owner decisions Q-K144 (source, plus S5's sub-option), Q-K145 (who),
  Q-K146 (which hospitals) and Q-K147 (what the endpoint reveals). Log Q-K148 (`Hospital.name`)
  and Q-K149 (no server cache) under "follows from the pattern", keeping their IDs. The
  cross-site guard and "not audited" are not decisions.

## Low

- **D8 (merges Z8):** drop `since` and the extra `Index(["keycloak_sub"])`. The unique
  constraint leads with `keycloak_sub`. The rebuild becomes a set difference of pairs.
- **D9:** a hospital role that can write `StaffIdentity.keycloak_sub` (today `ihr_app`) can add
  its own hospital to a victim's menu. The click ends at Keycloak's gate. Note it in §7 and
  weaken the "can only write its own hospital's rows" claim to "only rows for its own hospital".
- **D10:** omit `?next=%2F`, because `_safe_next` defaults to `/`. The client then requires no
  query (D4).
- **D11:** note that an expired Keycloak SSO session shows the normal login at the target and
  still lands on that hospital. No new test is needed.
- **D12:** add the username to the query key: `["ihr", "my-hospitals", username]`.
- **D13 (merges Z9, R11, R14):** ignore query parameters (no 400 `no-parameters`), and drop the
  50 cap, the `truncated` log line and the grep test. Test 7 already covers "never another
  person's". The query-key recording of the role matrix still pins that nothing is read.
- **D14 (merges S10):** pin the codes. `StaffSessionAuthentication.authenticate_header` returns
  `""` (`authentication.py:132`), so anonymous `same-origin` gives 403 with the not-signed-in
  body, and cross-site gives 403 "Cross-site request refused.". There is no 401 anywhere.
- **R9:** the trigger cannot read `IHR_STAFF_REFUSE_SUPERUSERS`. State that the directory
  always leaves superusers out (the production rule, `authentication.py:91`).
- **R10:** moot. The fleet step is dropped (D2).
- **R13:** fix the file facts:
  - `Utils/ihr/ihrApi.ts` exists ("add to");
  - `NAV_USER` and `SWITCHER_ANCHORS` are new constants, looped over like `STAFF_ANCHORS`
    (`check-ihr-frontend.py:203`, `:382`);
  - migrations are `ihr_platform` 0013 and `ihr_identity` 0004.
- **S8:** `ihr_create_tenant --public` makes an ACTIVE `Hospital(schema_name="public")`. Add
  `schema_name <> 'public'` in the trigger, `rebuild`, `--repair` and the endpoint query, and
  add the public tenant to test 8.
- **S9 (merges R12):** take `pg_advisory_xact_lock(hid, hashtext(sub::text))` in the trigger,
  and the same per-sub locks in `rebuild`, so two concurrent recomputes cannot leave a wrong row.
  Weekly `--check` (D2) is the backstop.
- **S11:** the isolation crawl's leak check does not cover `Hospital.name`, slug or domain
  (`world.py:82`, `:130-132`). The new isolation test asserts B's name, slug and domain text are
  absent for an A-only person, with one person linked in both world tenants.
- **Z10:** state the invariants instead of plpgsql step order where the code is left to the
  builder. Keep the exact trigger names and `WHEN` clause, because checks and the dump
  allow-list depend on them. Use `ihr_hospitals_switch_aria` in the component, or drop it.

## Orchestrator decision (accepted by the orchestrator, 2026-11-23)

Binding: where this section and design.md differ, this section wins.

**Design changes required:**
1. Add the definer to the db_roles model (S1) and to the restore quarantine allow-lists (S2),
   each with a test.
2. Make the endpoint staff-only: `authentication_classes = [StaffSessionAuthentication]` plus a
   service-account refusal (S6). A user with no identity gets 403. Exact codes are 403
   everywhere (D14).
3. E027 is static wiring and W027 the SQL fingerprint. Drift is only a `--check` report and
   log line, and never gates a hospital (D1).
4. Cut the fleet step, `rebuild_gone`, `since` and the index, the 50 cap and log line, the 400
   on parameters and the grep test (D2, D8, D13). The first fill is the `ihr_identity` 0004
   migration. Repair is `ihr_staff_directory --check|--repair`, with `--check` in `staging.sh
   due`.
5. Restore: `rebuild` in `_reconcile` and in `abort()`, never in `_migrate` (D6). Rollback
   across P1-T31 is refused (S4).
6. Add `servable_many` and a constant query count (D3), and `links.origin_for` (Z6). Exclude
   `public` (S8). Add per-sub advisory locks (S9).
7. Web: the scheme, no-credentials, exact-path and no-query client rule (D4, D10); `silent:
   true` (R7); a user-scoped query key (D12).
8. Tests: concrete tests 5 and 6 and mutation row 5 (D5); test 2 calls the real accept, join
   and invitation paths (D2 spot-check); a fresh-tenant trigger test and a `UPDATE OF` column
   check (D7); name, slug and domain in the isolation test (S11); Playwright mechanisms (R8).
9. Replace §7's P4-T8 note with S5's version in P4-T8's tracker row. Add D9's note to §7.

**Decisions (log as "awaiting owner confirmation"):**
- **Q-K144 — A:** a public directory table kept by per-hospital definer triggers. Reason:
  `queryset.update(is_active=…)` at `views.py:269`, `join.py:217` and `invitations.py:68`
  bypasses signals, and the app gets no write grant. First fill comes from the migration, repair
  from the command. Sub-option: **raw sub now** (no new exposure under the shared `ihr_app`); a
  hospital-bound read or keyed hash is decided in P4-T8.
- **Q-K145 — A:** only active, non-deleted, non-superuser, non-service-account links. Superusers
  are always left out.
- **Q-K146 — A:** ACTIVE hospitals with an org id and a primary domain, never `public`. A held
  one is shown disabled. The group shows from two entries.
- **Q-K147:** `GET /api/ihr/my-hospitals/`, staff cookie only, `NotCrossSite` first. It returns
  only name, url, current and available. Query parameters are ignored. No cap, no audit entry.
- **Q-K148 (follows):** `Hospital.name`. **Q-K149 (follows):** no server cache, a constant query
  count, no `meta.persist`.

**Split (≈11.5 agent hours):**

| Sub | Content | Builder | Est. |
|---|---|---|---|
| T31a | Table and 0013; `public_tables.txt`; `ensure_directory`, the trigger SQL, advisory locks; `ihr_identity` 0004 (install and first fill); E027/W027; db_roles `FUNCTIONS`/`SECURITY_DEFINER`; `OUR_FUNCTIONS`/`OUR_TRIGGERS`; tests 1–6 and 12, db_roles and quarantine cases | Opus high | 3 h |
| T31b | `rebuild`; `ihr_staff_directory --check\|--repair`; the drift log line and `staging.sh due` entry; restore `_reconcile` and `abort()` hooks; the rollback-refused marker; test 11 and restore tests | Opus high | 1.5 h |
| T31c | Endpoint (auth class, service-account refusal), `servable_many`, `links.origin_for`, route; tests 7–10; isolation test; role-matrix rows; mutation rows 1–5 (5 redefined) and `directory-service-token` | Opus high | 2 h |
| T31d | Web: `hospitals.ts` (new rule) and its tests, `ihrApi` addition, `IhrHospitalsMenu` (`silent`, user-scoped key), nav-user patch, `UPSTREAM_PATCHES`, anchors, en/hi strings and review CSV | Sonnet high | 1.5 h |
| T31e | Playwright `switcher.spec.ts` (R8 mechanisms) and the `roles-menus` row; CI; docs (TENANCY §4.4, CLAUDE.md, TRACKER, P4-T8 row note) | Sonnet high | 1.5 h |
| Review | Full review shape (tenancy, access) | Opus / Sonnet | 2 h |
