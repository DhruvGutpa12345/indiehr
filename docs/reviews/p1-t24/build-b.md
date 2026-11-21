# P1-T24b backend build (21-11-2026, unattended run)

Builder: Opus high (an access boundary that crosses hospitals). Scope: the backend half of T24b
as the orchestrator's decision in attack.md says (A1, G2, A4, Q-K118 A). The web side (the
"Sign-in" card, the UserForm change, strings, Playwright) is the frontend builder's. Nothing
committed. The rules as built are in design.md "T24b as decided".

## Files

| File | Change |
|---|---|
| plugins/ihr_platform/ihr_identity/staff_access.py | docstring; `OFFERS` (132), `access_state` (154), `reset_allowed` (169), `offers_for` (175); GET adds `state`, `offers`, problem codes (201-232); POST reset refuses a non-hospital-wide asker and a hospital-wide target (`_reset_sub`, 248, 251) |
| plugins/ihr_platform/ihr_platform/invitations.py | `PROTECTED`/`UNCHECKED`/`NOT_ALLOWED` (351-353), `PROBLEM_CODES` + `problem_code()` (359-390); refusals logged with code and text (439); `_reset_sign_in` re-checks asker and target (496, 498); `_asker_may_reset` (514), `_admin_anywhere` (526) |
| plugins/ihr_platform/ihr_platform/seeding.py | `FACILITY_ADMIN`, `make_facility_admin()` (225-260) |
| plugins/ihr_platform/ihr_platform/management/commands/ihr_seed_demo.py | `SEED_USERS` (112, the passwords list now includes the new user), step `_facility_admin` (170, 296); JSON gains `facility_admin`, `facility_admin_added` |
| plugins/ihr_platform/ihr_platform/management/commands/ihr_keycloak_demo_users.py | `admin.anil` → alpha `care-facility-admin`, authenticator pre-set (41) |
| scripts/care-dev.sh | TEST_USERS.md row for `care-facility-admin` (admin.anil) |
| plugins/ihr_platform/ihr_platform/tests/isolation/routes.py | `ROUTE_CONVERTER_VALUES` fills the staff `<slug:action>` from `staff_action` (129); the isolation world has none, so its crawl is unchanged |
| plugins/ihr_platform/ihr_access/tests/matrix.py | the matrix tenant's `staff_action="reset-sign-in"` (516) |
| plugins/ihr_platform/ihr_access/tests/role_matrix.txt | row `W /api/ihr/staff/<str:username>/<slug:action>/` `all:skip` → `all:deny` (reason: the route is now filled, and the write crawl proves the facility-only admin is refused); GET access row's reason names `offers` (outcome unchanged) |
| plugins/ihr_platform/ihr_access/tests/role_matrix_writes.txt | new row: `auth` for admin and every role of ours, `body` for the Doctor (on himself: "ask another admin", 400) |
| plugins/ihr_platform/ihr_identity/tests/test_invitations.py | fixtures moved to `RecoveryCase`; `admin_role`, `make_hospital_wide` helpers; RecoveryTests' asker is made hospital-wide (A1) |
| plugins/ihr_platform/ihr_identity/tests/test_staff_offers.py | new (below) |
| plugins/ihr_platform/ihr_access/tests/test_seed_admin.py | new class `SeedFacilityAdmin` |
| docs/reviews/p1-t24/design.md | section "T24b as decided" |
| docs/QUESTIONS.md | Q-K121, Q-K122; Q-K118 marked built |

No file under `apps/` changed (docs/UPSTREAM_PATCHES.md unchanged). No Keycloak-calling code
changed (`keycloak/invite.reset_sign_in` untouched), so the Keycloak live tests were not run.

## How each item is met

- **A1 caller:** staff_access.py:248 (`_reset_sub`, 403 before anything about the person is said),
  `reset_allowed` (169) for the offer, invitations.py:521 (`_asker_may_reset` at 540, the provisioner
  re-checks the asker by `requested_by`). Resend and cancel keep `_target`'s `can_create_user`
  rule, unchanged.
- **A1 target, this hospital:** staff_access.py:251 and `reset_allowed`.
- **A1 target, any other hospital:** invitations.py:523 → `_admin_anywhere` (564). The
  provisioner reads the schema of every hospital in `fleet.MIGRATE_STATUSES` (a provisioning one
  before its schema step is skipped) under its job lock for a `StaffIdentity` with
  the person's Keycloak `sub` whose user is hospital-wide there; found → `protected`; a hospital
  not ready → `try-later` (fails closed). The web process never reads another schema; nothing
  new in `public`. The asking hospital sees only the code (test checks the other hospital's
  slug, name and schema are not in the answer). Q-K121 A.
- **G2:** `access_state` and `offers_for`; oneself → `[]`. POST applies the same rules; the
  only offered-but-400 answers are the daily reset limit and an already-open request.
- **A4:** `problem_code` (invitations.py:405); every literal message in invitations.py has its
  own code (an AST test fails on a new uncoded message). The text stays in the record and in the
  provisioner's log line `staff request <id> refused: <code> (<text>)`.
- **Q-K118 A:** `admin.anil` / `care-facility-admin`, "Facility Admin" at the demo facility's
  root only; `make_facility_admin` refuses if that user is hospital-wide.

## Tests

Results (21-11-2026, run as the app role):
- `IHR_DB_ROLES=test scripts/care-tenancy.sh test --settings=ihr_platform.settings.test --noinput
  ihr_identity ihr_audit ihr_access ihr_roles ihr_platform` → `Ran 995 tests in 919.246s`,
  `OK (skipped=45)`. The matrix inside it: GET crawl 3840 requests, write crawl 3437 requests
  (24 unfilled routes; the staff action route is now filled and its snapshot row matched).
- Targeted first: `ihr_identity.tests.test_staff_offers ihr_identity.tests.test_invitations
  ihr_access.tests.test_seed_admin` → 45 tests OK. Before the code existed, the same run failed
  (import error for the seed, and every new test).
- Mutation run (A1 removed): `FAILED (failures=6)`, then the code restored.
- `PATH="$HOME/.local/bin:$PATH" pre-commit run --all-files` → every hook passed (exit 0).
- Not run: web checks (backend-only build), Keycloak live tests (no Keycloak-calling change).

New, `ihr_identity.tests.test_staff_offers` (11):
- `CallerRule`: a non-hospital-wide admin gets no reset offer and 403 (nothing queued), the
  hospital-wide one is offered it and 202; resend and cancel by the lower admin (202); an asker
  who lost the role by the time the provisioner runs → `not-allowed`, no reset.
- `TargetRule`: a hospital-wide target here → no offer, 403; oneself → `offers: []`.
- `OffersPerState`: eight states (active, awaiting-setup, queued, sent, failed, cancelled,
  expired, none) × three actions: `offers` equals exactly what POST accepts (202 iff offered).
- `ProblemCodes`: a Keycloak-like raw error and a known worker message come back as `failed`
  and `not-replaced`, and none of the raw text is in the answer; every worker message has a code.
- `ProtectedElsewhere` (a second hospital with its own schema): admin there → request fails
  `protected`, Keycloak never called, nothing about the other hospital in the answer; plain staff
  there → reset done; the other hospital not readable → `try-later`, no reset.

New, `ihr_access.tests.test_seed_admin.SeedFacilityAdmin` (3): the login row and seed list; the
facility-only admin may add staff (`can_create_user`), is not hospital-wide, `my-access`
`hospital_admin` false, GET access on the Doctor without `reset-sign-in`, POST reset 403, safe
to re-run; the seed refuses a hospital-wide user.

Mutation proof (A1 checks removed in staff_access.py and invitations.py, then restored): 6 of
the 14 new tests fail (both caller tests, the target test, the two other-hospital refusals and
the facility admin's reset), as they must.

## Decisions

- Q-K121 A: the other hospitals are checked by the provisioner, exactly, at reset time; no new
  public flag (limit: the GET may offer a reset that then ends `protected`).
- Q-K122 A: a hospital-wide admin is never reset from a staff screen, not even by a peer.
- The answer's `problem` keys keep their names; their values are now codes (or null). A
  superuser counts as hospital-wide only through a membership (as on my-access).

## Web

Built by the web builder (Sonnet high), 21-11-2026, after the backend half above. Nothing committed.

**Files (ours)**
- `apps/frontend/src/components/ihr/IhrStaffAccessCard.tsx`: the "Sign-in" card.
- `apps/frontend/src/Utils/ihr/staffAccess.ts` (pure) and `staffAccess.test.ts` (27 node:test cases).
- `apps/frontend/src/Utils/ihr/ihrApi.ts`: `ihrStaffApi.get` / `.act`.
- `apps/frontend/tests-ihr/staff-access.spec.ts` (new), `roles-menus.spec.ts` (`admin.anil` added).
- `deploy/locale/{en,hi}.json` (55 `ihr_staff_*` keys after the review), `docs/i18n/review/hi-batch-6.csv` (73 → 120 rows, README count), glossary section "Staff sign-in card".

**Upstream edits (all in docs/UPSTREAM_PATCHES.md)**
- `components/Users/UserSummary.tsx`: one marked JSX block and its import.
- `components/Users/UserForm.tsx`, every edit: (1) default `password_setup_method` `"immediate"` → `"email"`; (2) the `immediate` radio option deleted (the enum, its refinements and the password inputs stay, never reached); (3) the unused `Lock` import dropped. The e-mail field was already required on create.

**How each item is met**
- Shows only when `offers` is non-empty (`cardVisible`); once we asked an action it stays so the outcome is seen. It is not asked for at all for oneself, a service account, a `restricted` or `pending` mode (`wantsCard`); a 403 or 404 hides it. Outside a facility route the mode cannot be known, so the backend's 403 is what hides it there.
- Buttons come only from `offers` in a fixed order; an action this build does not know is dropped. Reset and cancel use a plain confirm (Q-K113 B) with the warning text; "Send again" asks nothing.
- Status is a fixed phrase from `state`; a problem is a fixed phrase from its code (`problemKey`), an unknown code is the generic failure, raw text never shown. The `protected` phrase says nothing about why or where. A 400 on an action shows the backend's own plain text.
- Times: DD-MM-YYYY HH:MM IST computed from the instant (not the browser's zone). States: loading skeleton, error with "Try again", hidden when empty. While queued or a request runs: re-read every 10 s, at most 6 times.
- Facility-only admin: the GET offers nothing for an active colleague, so no card and no reset button; a reset typed at the API is 403 (spec).

**Tests**
- Unit (ran locally): `node --import tsx --test src/Utils/ihr/*.test.ts` → 294 tests, 0 fail (27 new). The new file failed first (module missing).
- Playwright `staff-access.spec.ts` (CI only; written, not run): the colleague's own page has no card and links her sign-in; admin.priya sees "Active staff" and "Reset sign-in", the confirm shows the exact warning, Cancel sends no POST, her own page has no card and no staff call; admin.anil sees no card and no button, and a reset typed at the API is 403 (declared dead end); reception.ravi has no card and makes no staff API call. Nothing ever confirms a reset (one a day per person, mails the person).
- `roles-menus.spec.ts`: `admin.anil` expects CARE's full menu without "Audit trail" (my reading, from `admin.bhavesh`'s identical role; unverified until CI).

**Ran locally / left to CI**
- Ran: staffAccess unit tests and all Utils/ihr tests; prettier on every touched web file; `scripts/merge-locale.py --check`; `scripts/check-ihr-frontend.py`; pre-commit.
- Not run: `tsc --noEmit` (both configs) and eslint: `free -m` showed about 1.9 GB available, under the 2000 MB rule. The Playwright specs also left to CI.

## Review

Fresh-eyes review (lenses A access/tenancy, K flow/state, W web), batched challengers, Opus fixer for the boundary findings. Severity: reviewer → challenger → re-check.

| id | finding | severity | fix | test (fails with the fix reverted) |
|---|---|---|---|---|
| A1/K2 | `_admin_anywhere` read only ACTIVE hospitals: an admin at a suspended, offboarding or provisioning hospital could be reset from another (binding Decision says "any hospital"; Q-K121 had narrowed it silently) | medium → medium → medium (boundary) | scans `fleet.MIGRATE_STATUSES` (not archived); a provisioning hospital with no recorded schema state (before its schema step, no StaffIdentity possible) is skipped so it never fails every reset closed; Q-K121 and design corrected | `test_staff_offers.ProtectedElsewhere.test_an_admin_at_a_hospital_not_live_is_protected_too` (3 subtests, all failed before); `test_a_hospital_still_before_its_schema_step_is_skipped` (failed with the skip removed); `test_an_archived_hospital_is_not_read` (guard) |
| A3/K4 | provisioner's asker re-check ignored a deactivated, soft-deleted or service account (memberships stay; a queued request can wait through a hold) | low/medium → confirmed medium → medium (boundary) | `_asker_may_reset` looks the asker up with `is_active=True, deleted=False, is_service_account=False` | `CallerRule.test_a_deactivated_deleted_or_service_asker_is_refused` (3 subtests, all failed before) |
| K1 | fail-closed scan: one held/restoring/other-build/gated hospital makes every reset fleet-wide `try-later` (final), and each reset reads all N schemas | medium → medium (availability only) → medium | kept the full scan (decision, not code): scoping by Keycloak organisation membership would make the boundary rest on two stores staying in step; limit written in Q-K121, design "T24b as decided" and `deploy/staging/README.md` (Releases: "Staff sign-in resets during a hold") | existing `test_a_hospital_it_cannot_read_fails_closed` pins the fail-closed behaviour |
| A2/K3 | a superuser (no admin membership) at another hospital was not protected; `_target` refuses superusers only here | low/medium → low → low (boundary) | `there.user.is_superuser or hospital_wide_admin(there.user)` | `ProtectedElsewhere.test_a_superuser_elsewhere_is_protected` (failed before) |
| A6 | design/Q-K121 said X learns "never which hospital or role"; `protected` does tell X the person is an admin elsewhere (one bit) | low → confirmed → wording | reworded Q-K121 and design to state the one bit | — (wording) |
| A4 | `post`'s daily limit excluded FAILED, so a `protected` refusal could be repeated (one open request at a time, a fleet scan each) | low → partly (trusted admins only; one accepted bit) → low | a `protected` failure counts toward the limit (400 `protected-recent`); `not-allowed` and `try-later` do not (they would block a colleague): Q-K123 A; alert `StaffResetRefused` covers the repeats (K5) | `test_staff_offers.RefusalsAndLogs.test_a_protected_refusal_counts_toward_the_daily_limit` |
| A5 | `try-later` is final and one held hospital blocks every reset, undocumented | low → partly (by design) → low | one sentence in Q-K121 Limit, design "T24b as decided" and the runbook (deploy/staging/README.md "Staff sign-in resets during a hold") | — (wording) |
| A6 | design and Q-K121 said X learns "only refused" / "never which hospital or role" | low → confirmed → wording | both reworded to the exact bit: the person is a hospital-wide admin at some other hospital, not which (supersedes the earlier rewording above) | — (wording) |
| K5 | refusals logged without hospital or a stable prefix; no alert; web 403s unlogged | low/medium → confirmed → low | `invitations.log_outcome` writes `ihr-staff: hospital=<slug> request=<id\|none> action=<a> outcome=<code>` for each worker outcome and for the web's 403s (`reset-refused`, `admin-target`); `deploy/logs/alerts/staff.yml` (`StaffResetRefused`, 3 in an hour); fixtures in alert-cases.json, key label in check-logs.py, a line test in scripts/test_staging_logs.py | `RefusalsAndLogs.test_refusals_leave_a_stable_line`; `scripts/check-logs.py` ("Log pipeline OK", the new alert fires for alpha and gamma only, forged lines do not); `test_staging_logs.test_staff_line_is_ours_alone` |
| K6 | the final wait failure mapped to `waiting-for-setup` | low → confirmed → low | own code `other-account-not-set-up`, phrase `ihr_staff_problem_other_account_not_set_up` (en, hi, review sheet) | `RefusalsAndLogs.test_final_wait_failure_is_not_shown_as_waiting`; staffAccess.test.ts "the new problem code" |
| K7 | a cancelled or expired invitation kept its old problem; the card showed it in any state | low → confirmed → low | `_close` clears `error` when it sets a status (resend already did); the card shows the problem only for queued, sent, failed (`problemShown`) | `RefusalsAndLogs.test_cancelling_clears_the_old_problem`; staffAccess.test.ts "problemShown" |
| K8 | a final `InvitationError` was never logged | low → confirmed → low | `logger.info("invitation %s failed: %s (%s)", ...)` in `deliver_once` | `RefusalsAndLogs.test_a_final_delivery_failure_is_logged` |
| K9 | role_matrix.txt comment said every role gets 403 | low → confirmed → comment | reworded: the Doctor, the target, gets the self-check's 400 first (role_matrix_writes.txt `body:doctor`) | — (comment; the matrix data is unchanged) |
| K10 | the one-a-day 400 said "perhaps at another hospital" | low → confirmed → low | the phrase is dropped; the known one-bit limit is written under Q-K121 | `RefusalsAndLogs.test_the_daily_limit_does_not_name_another_hospital` |
| W1 | the spec exercised neither the UserForm change nor sent, cancel, resend | medium → confirmed (with correction) → medium | **Not added, on purpose.** (1) The add-user sheet is reached through an organisation's users page whose id the seed does not hand the spec; I could not run Playwright here, and an `#immediate` count of 0 on a page where the sheet never opened passes without proving anything. The removed option is guarded instead by `scripts/check-ihr-frontend.py` (W6: `value="immediate"` must be absent; pre-commit and CI). (2) admin.anil sees no card for active colleagues, and a click path needs a freshly invited person and the provisioner, which the web-app CI stack does not run; the click paths (resend, cancel, sent line) are covered by `test_staff_offers.OffersPerState` and `CallerRule` against the real views, and the pure rules by staffAccess.test.ts | `check-ihr-frontend.py` fails when `value="immediate"` returns (shown by editing it back in) |
| W2 | a Doctor or Nurse on a colleague's page gets a 403 and a skeleton that flashes | low → partly (403 is the binding Decision) → low | the card renders nothing while loading; the accepted 403 is recorded in design.md ("Review fixes (T24b)"); unused key `ihr_staff_loading` removed | web: no unit test (a render); `tsc` and Playwright left to CI |
| W3 | a 400 showed backend English in the Hindi UI | low → partly (no worker text) → low | `staff_access.RefusalError` sends `{code, detail}`; the card maps the code to `ihr_staff_error_*` (8 keys, en and hi, review sheet) and never shows `detail`; `messageOf` removed | `RefusalsAndLogs.test_every_400_carries_a_code_the_screen_can_translate`; staffAccess.test.ts "refusalKey" |
| W6 | no anchors for the UserSummary mount or the UserForm option | low → confirmed → low | `STAFF_ANCHORS` and `STAFF_ABSENT` in scripts/check-ihr-frontend.py (card mount and import, e-mail default, the P1-T24b marker, `value="immediate"` absent) | edited each away and back: the script fails with the exact line, then "ok" |
| W7 | `admin.anil`'s expected menu is unverified | low → partly → very low | marked "confirm on the first CI run" in the run file (docs/runs/2026-11-21-run-to-P1-T24.md) | — |
| W4, W5 | refuted by the challenger | — | no action | — |

## Verification

Independent verifier (Opus high, 21-11-2026; wrote none of the build). Nothing committed. Hashes of
all 39 changed or new files were recorded first; every mutation below was restored from a backup
and the restored hashes matched. The only change left by the verifier is the LATE FIX below and
this section.

| check | result |
|---|---|
| `IHR_DB_ROLES=test scripts/care-tenancy.sh test --settings=ihr_platform.settings.test --noinput ihr_identity ihr_audit ihr_access ihr_roles ihr_platform` | PASS: `Ran 1007 tests in 934.787s`, `OK (skipped=45)`, rc 0; Redis audit 887 keys, all tenant-prefixed; isolation Redis 780 keys, 0 unprefixed; matrix GET crawl 3840 requests (13 unfilled), write crawl 3437 (24 unfilled) |
| `ihr_identity` again after the late fix | PASS: `Ran 114 tests`, OK; Redis 209 keys, all prefixed |
| web unit tests `node --import tsx --test src/Utils/ihr/*.test.ts` | PASS: 297 tests, 0 fail |
| `npx tsc --noEmit` and `npx tsc -p tests-ihr/tsconfig.json` (2646 MB available, `--max-old-space-size=1800`) | PASS: both rc 0 (first local run of both) |
| eslint, Playwright (`staff-access.spec.ts`, `roles-menus.spec.ts` admin.anil) | left to CI |
| `scripts/merge-locale.py --check` | PASS (en 271 ours, hi 4816 ours); 55 `ihr_staff_*` keys in en and hi, all used by the card, all in hi-batch-6.csv (128 rows = README) |
| `scripts/check-ihr-frontend.py` | PASS; fails with `value="immediate"` added back and with the card mount renamed (both restored) |
| `pre-commit run --all-files` | PASS: every hook, rc 0 (run after this section and the late fix) |

**Reverting the review fixes (test_staff_offers, 23 tests; each failure for its own reason):**
- R1 (scan only ACTIVE; asker filters off; superuser-elsewhere off; K6 code; K7 `_close`; K8 log): 8 failures: not-live ×3 (reset done), asker `is_active` (reset done), superuser elsewhere (reset done), K8 "no logs", K6 `'waiting-for-setup' != 'other-account-not-set-up'`, K7 `'waiting-for-setup' is not None`.
- R2 (provisioning skip off; A4 protected-recent off; K5 `log_outcome` off; K10 old wording): 4 failures: skip test `[] != [sub]` (try-later), protected `202 != 400`, stable line "no logs", "another hospital" found.
- R3 (W3, 400 without a code): 9 errors, `list indices must be integers` (every 400 subtest plus the two tests reading `code`/`detail`).
- Web (staffAccess.ts: `problemShown`, the new problem code, `refusalKey`): exactly the 3 matching unit tests fail.

**Reverting the A1 rule itself:**
- Caller, web (`_reset_sub` 403 and `reset_allowed`'s caller half): 5 failures: CallerRule (offer and 403), SeedFacilityAdmin (`400 != 403`), stable line, and the **role matrix**: `admin` (facility-only Facility Admin) `202: a write was accepted` against role_matrix_writes `auth:admin`.
- Caller, provisioner (`_asker_may_reset` skipped): lost-role test, all 3 asker subtests, stable line `not-allowed`.
- Target here (`_reset_sub` admin-target and `reset_allowed`'s target half): TargetRule `('active', ['reset-sign-in']) != ('active', [])`, stable line `202 != 403`.
- Target elsewhere (`_admin_anywhere` skipped): 6 failures: admin elsewhere, not-live ×3, superuser elsewhere, fails-closed (Keycloak reset made in each).

**LATE FIX (test only), `ihr_identity/tests/test_staff_offers.py` `CallerRule.test_a_deactivated_deleted_or_service_asker_is_refused`:**
its `finally` restored the admin with `User.objects.filter(...)`, but CARE's `CustomUserManager`
hides deleted users, so after the `deleted` subtest the admin stayed deleted and the
`is_service_account` subtest passed without the fix (it was testing "deleted" again). With the
A3 filter reverted only the `is_active` subtest failed, so the fixer's "3 subtests, all failed
before" was not true. Now `get_entire_queryset()`; with the filter reverted the `is_active` and
`is_service_account` subtests fail; the `deleted` one still passes without the fix because CARE's
manager already excludes deleted users (the filter there is belt and braces). Code unchanged.

**Spot checks (file:line):** right: staff_access.py `OFFERS` 132; seeding.py `FACILITY_ADMIN`
225 and `make_facility_admin` 234; ihr_seed_demo.py `SEED_USERS` 112, `_facility_admin` 170/296;
ihr_keycloak_demo_users.py 41 (`admin.anil` → `care-facility-admin`); routes.py 129; matrix.py
516; fleet.py 84 `MIGRATE_STATUSES` = provisioning, active, suspended, offboarding; provisioning
`schema` step records state (steps.py 194) before `first_admin` (266), so the provisioning skip
holds; staff_urls.py unchanged; `release.job` takes a *shared* advisory lock, so re-taking the
asking hospital's own lock inside `_admin_anywhere` is safe; StaffAccessRequest ids are UUIDs as
staff.yml's regex expects. Stale after the fixes (docs only): staff_access.py `access_state`
146→154, `reset_allowed` 161→169, `offers_for` 167→175, POST checks 247/249→248/251;
invitations.py constants 351-353→356-361, `problem_code` 383→405, re-checks 496/498→521/523,
`_asker_may_reset` 514→540, `_admin_anywhere` 526→564; "How each item is met" still says "every
ACTIVE hospital" (now `MIGRATE_STATUSES`); the class is `RefusalError`, not `staff_access.Refusal`
(design.md, staffAccess.ts comment); UPSTREAM_PATCHES.md and the "Web" section say 47
`ihr_staff_*` strings, there are 55. **Orchestrator, 21-11-2026:** all of these corrected before the commit.
