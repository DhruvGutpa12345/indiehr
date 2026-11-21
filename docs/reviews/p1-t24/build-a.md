# P1-T24a backend build (21-11-2026, unattended run)

Builder: Opus high (access boundary). Scope: the backend half of T24a as the orchestrator's
decision in attack.md says. The web side (access.ts, useIhrAccess.ts, the audit page, strings)
is the frontend builder's. Nothing committed.

## Files

| File | Change |
|---|---|
| plugins/ihr_platform/ihr_access/endpoints.py | my-access answers `hospital_admin` (docstring 156-172, code 201-203) |
| plugins/ihr_platform/ihr_identity/staff_access.py | new `NotCrossSite` permission (98-113) |
| plugins/ihr_platform/ihr_audit/views.py | `AUDIT_GATE = (NotCrossSite, IsHospitalAdmin)` (193) on all five views; docstring |
| plugins/ihr_platform/ihr_platform/seeding.py | `HOSPITAL_ADMIN`, `make_hospital_admin()` (171-226) |
| plugins/ihr_platform/ihr_platform/management/commands/ihr_seed_demo.py | `_demo_facility`, `_hospital_admin` step (263-280); JSON gains `hospital_admin`, `hospital_admin_added` |
| plugins/ihr_platform/ihr_platform/management/commands/ihr_keycloak_demo_users.py | `admin.priya` → alpha `care-admin`, authenticator pre-set (36-38) |
| scripts/care-dev.sh | TEST_USERS.md row for care-admin names its use and admin.priya |
| plugins/ihr_platform/ihr_access/tests/role_matrix_keys.txt | `hospital_admin` under both my-access keys (reason: new key of a body our roles see; false for all of them) |
| plugins/ihr_platform/ihr_access/tests/role_matrix.txt | my-access row's reason names the third boolean (outcome unchanged) |
| docs/reviews/p1-t24/design.md | new section "T24a as decided" |

## How each item is met

- **Auth question (F1):** the cookie. The evidence with file:line is in design.md "T24a as
  decided". The backend's only staff class reads the HttpOnly cookie, and a `Bearer` header
  never signs anyone in. The web app's `Authorization` branch reads a legacy localStorage key
  that is removed at every start. So T24c's downloads can be plain same-origin links.
- **G1 `hospital_admin`:** endpoints.py:201-203 uses `IsHospitalAdmin().has_permission`, the
  audit trail's own gate. It is false for a superuser and makes no query for one. There is no
  `manage_staff`.
- **Query count:** before T24a, 12 statements with `?facility=` and 10 without. After, 13 and 11.
  Django-tenants' `SET search_path` lines are not counted. The count is pinned by
  `test_my_access.MyAccess.test_query_count_is_pinned`.
- **A2:** staff_access.py:98-113 and views.py:210 (refuse when `Sec-Fetch-Site` is present and
  is neither same-origin nor none). The cross-site check comes first, so a non-admin gets the
  same answer. A refused request is still recorded as a 403 request entry with no patients.
- **G4:** `admin.priya` signs in as `care-admin`. `make_hospital_admin` keeps CARE's fixture
  top-level admin role and adds "Facility Admin" at the demo facility root, so the facility
  menu reaches the admin pages. The step is idempotent.

## Tests (TDD: written first, red on unchanged code, then green)

- Red run on unchanged code (3 modules): 70 failures and 6 errors. They were the missing key,
  the cross-site routes answering 200, the missing `NotCrossSite` and the missing seed module.
  The query-count failures showed the "before" numbers.
- New and changed tests:
  - `ihr_access.tests.test_my_access`: 11 tests (3 new: the gate parity over every login plus
    hospital-wide, district, inactive-organisation and facility-only admins and the superuser
    admin; agreement with `/api/ihr/audit/status/`; the query count).
  - `ihr_access.tests.test_seed_admin`: 4 new.
  - `ihr_audit.tests.test_viewer.CrossSiteTests`: 4 new (5 routes × cross-site and same-site
    refused, same-origin, none and absent served; the gate on every URL pattern; the refusal
    comes before the admin check).
- `IHR_DB_ROLES=test scripts/care-tenancy.sh test --settings=ihr_platform.settings.test
  --noinput ihr_identity ihr_audit ihr_access ihr_roles`: **Ran 566 tests in 330.266s, OK**
  (role matrix included). The run used the code before ruff's formatting; the changed modules
  were re-run after it: **Ran 19 tests, OK**.
- `pre-commit run --all-files`: all hooks passed (45 Passed lines) after ruff fixed the import
  order. `scripts/check-ihr-frontend.py`: ok.

## Open for the orchestrator

- Q-K110…Q-K117 are written into QUESTIONS.md by the web builder (Q-K111 narrowed to
  `hospital_admin`, Q-K113 = B), with Q-K118 (below) and Q-K119 (the audit page's link filters).
- Proposed Q-K118: in the dev seed, `care-fac-admin` (`admin.bhavesh`) is already a hospital-wide
  admin, because CARE's fixture gives it "Facility Admin" on a top-level role organisation. T24b's
  A1 spec needs a facility-only admin login.
- Local frontend typecheck and vitest were not run by this builder (backend only; 1851 MB was
  available at the start).

## Web (P1-T24a, Sonnet high web builder, 21-11-2026)

Our own files only; **no upstream file under `apps/frontend` was touched**, so
`docs/UPSTREAM_PATCHES.md` has no new row. Nothing is committed (new files are `git add`ed).

**Files**
- `src/Utils/ihr/access.ts`: `MyAccess.hospital_admin`, `IhrAccess.hospitalAdmin` (true only in care
  mode: `!restricted && hospital_admin === true`; false while pending, after a failed answer with
  nothing cached, for a superuser and for every restricted answer), the `none` object carries it
  false. `useIhrAccess.ts`: the no-facility default has the flag false. `access.test.ts` (4 new
  cases) and the fixtures of `menu.test.ts`, `frontDesk.test.ts`, `routeKeys.test.ts` carry the field.
- `src/Utils/ihr/menu.ts`: item key `audit` (`/ihr/audit`), after CARE's links and ours, only when
  `access.hospitalAdmin`. `menu.test.ts`: care mode only, never restricted, pending, superuser or
  any of the eight roles; added last. `ihrMenu.tsx`: name and `ScrollText` icon.
- `src/Utils/ihr/audit.ts` + `audit.test.ts` (28 cases, pure): address state (today in IST; 23:59 and
  00:01 IST), DD-MM-YYYY to YYYY-MM-DD, range mistakes (format, before 2000, order) with the
  totals page's own message keys, the query, `before` cursor, filter key, actor and patient link
  rules, `what`/`result`/patients-shown, old/new `valueRows`, the status strip (fails closed).
- `src/Utils/ihr/ihrApi.ts`: `ihrAuditApi` (`entries`, `entry`, `status`).
- `src/components/ihr/IhrAuditTrail.tsx`, route `/facility/:facilityId/ihr/audit` in
  `IhrRoutes.tsx` (`OUR_ROUTES`, first key, `IhrRoutes.tsx:112`).
- `deploy/locale/{en,hi}.json` (68 keys, `ihr_audit_*`, `ihr_menu_audit_trail`),
  `docs/i18n/review/hi-batch-6.csv` (awaiting owner review, no clinical rows), README row,
  glossary section "Audit trail" (new terms marked with the question mark).
- Specs: `tests-ihr/admin-audit.spec.ts` (new), `tests-ihr/roles-menus.spec.ts` (admin.bhavesh and
  new admin.priya get "Audit trail" last; every other login's typed audit address shows "not part
  of your role" and makes no `/api/ihr/audit/` request).
- `docs/QUESTIONS.md`: Q-K110 to Q-K119.

**How each item is met**
- Gate: the inline pattern of `IhrOwnerTotals.tsx` (pending shows Loading, no flag shows
  `IhrNotForRole`, else `Page`). No new gate component. The menu item and the page read one flag.
- List with the API's filters: From, To (DD-MM-YYYY boxes, default today IST, converted for the
  API), Kind, Type of change; Load older uses `next_before` (a `useInfiniteQuery`, dropped when a
  filter changes). No typed Who and no patient search: an entry's actor and a patient's name are
  links that set `actor=` or `patient=` (exact actor string; chips with "Show all"), Q-K119.
- Status strip: last sealed (IST), not yet sealed, triggers, outside fingerprints; a missing field is
  "not known/confirmed", never "all in place".
- Entry sheet: values are asked for only when an entry is opened (one `entries/<id>/` call);
  update rows show old and new, other rows one value; the "opening is recorded" note.
- States: skeleton rows, empty line, error with Try again (403 without), 400 field messages beside
  the box, a mistaken date sends no request. Phone width: cards below `md`, table above.
- Audit calls are themselves recorded, so the queries never refetch on focus or reconnect and never
  poll (`refetchOnWindowFocus: false`, `retry: false`).
- India formats: DD-MM-YYYY and "IST" after every time (`at_ist` from the API). English and Hindi.
- Export and verifier links: T24c (not built here).

**Verified here (commands run, `free -m` showed 2437 MB available, `NODE_OPTIONS=--max-old-space-size=1800`)**
- `node --import tsx --test src/Utils/ihr/*.test.ts`: tests 263, pass 263, fail 0 (access, menu and
  audit tests were written red first for access and menu; audit.test was written with the module).
- `npx tsc --noEmit`: no output (clean). `npx tsc -p tests-ihr/tsconfig.json`: exit 0.
- `npx eslint` on the changed `src` files: 0 errors (62 warnings, all "translation key missing in
  the translations file" for our `ihr_*` keys, as for every existing ihr string, which merge at build).
  The `tests-ihr` specs are outside ESLint's project, as the existing ones are.
- `scripts/merge-locale.py --check`: en 6856 + 211 ours; hi ok. `scripts/check-ihr-frontend.py`: ok.
- Prettier on the changed files: clean.

**Left to CI** (not run here: needs the dev stack with the re-seeded admin.priya and a built bundle)
- `tests-ihr/admin-audit.spec.ts` and `roles-menus.spec.ts` (Playwright). Risks to read first if
  they fail: admin.priya's expected menu is CARE's full list plus "Audit trail" (assumed from the
  backend builder's seed; check on the first run); the "Triggers: all in place" line assumes the
  seeded hospital's triggers are complete; the actor step takes the first visible actor.
- Re-seed for the owner's click-through: `scripts/care-dev.sh seed`, then `scripts/care-dev.sh
  keycloak seed` (admin.priya's login appears in `deploy/seed/TEST_USERS.md`).
- No frontend build (never local).

## Review

Fixes by the Opus high fixer, 21-11-2026. Not committed.

| id | finding | severity (reviewer → challenger → recheck) | fix | test |
|---|---|---|---|---|
| W1 | The Playwright "Show all" step waits for a list request that never comes. "Show all" brings back the first load's filter key, and that list is still fresh in the cache (staleTime 60 s), so the spec would time out. | high → high (confirmed) → high (Opus) | `tests-ihr/admin-audit.spec.ts`: the `cleared` waitForResponse is removed. After "Show all" the spec checks that the chip has count 0, that `page.url()` has no `actor=`, and that a row is visible. No request count is asserted, because a slow run past 60 s may fetch again. | `audit.test.ts` "Show all brings back the first load's key" pins the key equality. A scratch run of TanStack's `InfiniteQueryObserver` with keys A → B → A at staleTime 60 s fetched only `["A","B"]`, which proves the old wait would hang. The spec itself needs the built web app and runs only in CI. |
| A1 | `hospital_wide_admin` accepts an admin of a top-level `govt` (state) organisation. | medium → low-medium (partly confirmed) → medium, boundary | **Gate not changed; Q-K120 A.** Provisioning (`steps.first_admin`) makes each hospital's first admin an Administrator of its **state** organisation. The suggested `org_type="role"` (or leaving out `govt`) would lock every real hospital's first admin out of the audit trail and the pack decisions, and no test guarded against that. The real fix is Q-K76 B (a recorded hospital root), to be settled with Q-K118 before T24b. The `hospital_wide_admin` docstring now says this. | `test_my_access` `test_the_provisioned_first_admin_is_a_hospital_admin` checks that an Administrator of a state is true on my-access and gets 200 on `/audit/status/`; the same person is also in the parity loop. `ihr_refdata test_api` `test_the_provisioned_first_admin_decides_pack_updates` checks 200 on updates and history. With `organization__org_type="role"` applied, both fail (3 failures); as kept, they pass. |

Fixes by the Sonnet fixer (medium and low findings), 21-11-2026, on the Opus fixer's tree. Not committed.

| id | finding | severity (reviewer → challenger → recheck) | fix | test |
|---|---|---|---|---|
| W2 | The Kind and Type-of-change selects applied only the URL's filters, and the key-change resync reset the From/To boxes, so typed but unsubmitted dates were lost. | medium → medium (confirmed) → n/a | The selects and "Show entries" go through `applyDraft`, which calls `withDraftDates` (`audit.ts`): the typed dates are checked first (a mistake sends no request) and travel with the change. | `audit.test.ts` "withDraftDates" (2 cases; fail with the dates dropped). Spec step "a kind chosen after a date was typed keeps the date" (CI only). |
| A3 / W4 | The "exact actor" link was a prefix match (`actor LIKE 'x%'`), so `user:5:priya` also showed `user:5:priya2`, and masked `patient:***1234` or `system:` actors matched others. | low / low-medium → low (confirmed) → n/a | New API parameter `actor_exact` (`actor = %s`, wildcards literal, over 200 characters is 400); the page sends it for its links. `actor` stays a prefix match. Design "Actor links are exact" and Q-K119 say so. | `test_viewer.SecondReviewTests.test_actor_exact_matches_one_actor_not_its_prefixes`; `audit.test.ts` "sends an actor link as an exact actor". |
| A2 | `Sec-Fetch-Site: none` and a missing header pass `NotCrossSite`, as designed. | low → low (confirmed) → n/a | Residual risk written into design.md's A2 paragraph, with the T24c suggestion (fetch plus blob export, refuse `none` on export). No code change. | none (documentation) |
| A4 | `make_hospital_admin` took the lowest-id system role holding `can_create_user`; none meant `role_id` None and an IntegrityError. | low → low (confirmed) → n/a | Role chosen by name (`seeding.ADMIN_ROLE_NAME` = "Administrator"); a missing one raises `RuntimeError` naming it, before anything is written. | `test_seed_admin`: the top-level membership is a top-level organisation with role "Administrator"; `test_a_missing_admin_role_is_a_clear_error`. |
| W3 | A request entry's sheet listed its data `{status, patients_total}` under "Values". | low → low (confirmed) → n/a | `valueRows(op, values, kind)` returns `[]` for `kind === "request"`; the sheet passes `entry.kind`. | `audit.test.ts` "shows no values for a request entry". |
| W5 | The purpose badge (`audit`, `records`, `billing`, `admin`, `care`) was English on the Hindi page. | low → low (confirmed) → n/a | Keys `ihr_audit_purpose__<p>` in `en.json` and `hi.json` (defaultValue `humanize`); five rows in `hi-batch-6.csv` (awaiting owner review). | `scripts/merge-locale.py --check`; the badge is not covered by a unit test. |
| W6 | Dropping §2.1's patient search was not in design.md "T24a as decided". | low → low → n/a | One paragraph added, pointing to Q-K119. | none (documentation) |
| W7 | The spec step "the list shows this sign-in's own entries" checked nothing about the actor. | low → low → n/a | Step renamed, and it now expects a visible actor `care-admin` (the CARE user admin.priya signs in as). | the spec itself (CI only) |
| W8 | This file said the route is `OUR_ROUTES` "last"; it is first. | low → low → n/a | Wording corrected (Web section). TRACKER.md is for the orchestrator. | none |

## Verification

Verifier: Opus high, 21-11-2026; wrote none of this. Nothing committed. **LATE FIX: none.** sha256 of
all 37 changed files was taken first and re-checked after every revert run and after pre-commit:
unchanged (this section is the only edit). 9 graphify calls (explain ×7, affected ×2).

| check | result |
|---|---|
| `IHR_DB_ROLES=test scripts/care-tenancy.sh test --settings=ihr_platform.settings.test --noinput ihr_identity ihr_audit ihr_access ihr_roles` | PASS: Ran 569 tests in 284.7 s, OK; Redis audit 560 keys, all tenant-prefixed (role matrix is in `ihr_access`) |
| `node --import tsx --test src/Utils/ihr/*.test.ts` (2.2 GB free, `--max-old-space-size=1800`) | PASS: 267 tests, 0 fail (audit.test.ts alone: 32) |
| `npx tsc --noEmit` and `npx tsc --noEmit -p tests-ihr/tsconfig.json` | PASS: both exit 0 |
| vitest | not run: the `src/Utils/ihr` tests use `node:test`, run above |
| Playwright `admin-audit.spec.ts`, `roles-menus.spec.ts`; ESLint; build | left to CI (need a built bundle and the re-seeded stack) |
| `scripts/merge-locale.py --check` | PASS: en 6856 + 216 ours; hi 5556 of 7072; 73 `ihr_audit*`/menu keys in en and hi, same set, all Devanagari |
| `scripts/check-ihr-frontend.py` | PASS |
| `pre-commit run --all-files` | PASS: exit 0, 45 Passed |
| no upstream file under `apps/frontend` touched | PASS: every staged frontend path is under `Utils/ihr`, `components/ihr` or `tests-ihr` |

**Fixes reverted (files restored byte for byte after each):**
- W2 (`withDraftDates` drops the typed dates): only "sends the typed dates with the change" fails
  (`'21-11-2026'` vs `'01-11-2026'`).
- A3 web (`actor` instead of `actor_exact`): only "sends an actor link as an exact actor" fails.
- W3 (request entries get values): only "shows no values for a request entry" fails
  (`patients_total`, `status` rows).
- A3 backend (exact clause removed) + A4 (missing-role check removed), one run of `test_viewer`,
  `test_seed_admin`, `ihr_refdata.tests.test_api` (69 tests): exactly 2 bad,
  `test_actor_exact_matches_one_actor_not_its_prefixes` (FAIL, prefix rows returned) and
  `test_a_missing_admin_role_is_a_clear_error` (ERROR, the old `IntegrityError` on `role_id`).
- A1 (gate narrowed to `organization__org_type="role"`): both guard tests fail
  (`False is not true`; 403 on `updates/` and `history/`), and 32 other pack API tests fail too
  (their admins are on `team`/untyped top-level organisations), which backs the fixer's refusal.
- W1, W5, W7 and the documentation rows (A2, W6, W8) have no runnable regression here: W1/W7 are
  Playwright steps (CI), W5 is covered only by merge-locale.

**Spot-checks of file:line claims (10):** confirmed — endpoints.py:201-203 (`hospital_admin`);
ihr_keycloak_demo_users.py:38 (admin.priya → alpha care-admin); IhrRoutes.tsx:112 (first key of
`OUR_ROUTES`); seeding.py:171-226; ihr_seed_demo.py:262-280; test_my_access.py:42-43 (13 and 11
queries); design.md auth evidence: tenancy.py:143-144, authentication.py:136 (cookie only), 67-68
(GET needs no CSRF), 242-249 (DRF `Token` keyword only), utils.ts:69-78, AuthUserProvider.tsx:63.
Stale after the review fixes (nits, no behaviour): `NotCrossSite` is now staff_access.py:98-113
(said 94-109) and `AUDIT_GATE` views.py:210 (said 193), in this file and in design.md "T24a as
decided" (corrected by the orchestrator, 2026-11-21); docs/i18n/review/README.md says batch 6 has 68 rows, the CSV now has 73 (W5 added 5; README corrected).

**Read, not changed:** the five audit URL patterns all use `AUDIT_GATE`; Q-K110…Q-K120 are in
QUESTIONS.md, auto-accepted and awaiting owner confirmation, Q-K113 = B.
