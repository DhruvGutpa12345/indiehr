# P1-T1d design: Playwright on two hospital addresses, in CI (draft for attack, 2026-11-13)

PLAN P1-T1d: Playwright on two `*.localhost` subdomains: token isolation, logout clean-up,
switcher (TENANCY §11.4; the Phase 1 review, R1-05). The dev machine cannot build the web app
(about 4 GB), so this runs in GitHub Actions only.

Research with file:line evidence: the run's scratchpad `t1d/frontend.md` and `t1d/stack.md`.

## What exists

- `apps/frontend` already pins `@playwright/test` 1.61.1 in its lock file.
  - Its own config and specs are unusable: CARE's username/password login is gone since
    P1-T4b, and its globalSetup injects CARE JWTs.
- Sign-in (P1-T4b, ADR-009):
  1. "Sign in with your hospital account";
  2. a top-level redirect through `/api/ihr/auth/login/` to Keycloak (`auth.localhost:8081`,
     TOTP for all staff);
  3. back with a host-only HttpOnly `ihr_session` cookie.
  - The web app holds no token.
- Sign-out clean-up (Q-T9): `clearStaffStorage()` in `src/Utils/ihr/staffSession.ts` clears the
  persisted query cache, all of sessionStorage, the filter caches and a few named caches.
  - Cross-tab sign-out uses BroadcastChannel.
  - Cross-hospital sign-out arrives "within the minute" (back-channel logout).
- The dev scripts already build the whole stack:
  - `care-dev.sh up`;
  - `seed` (alpha and beta, synthetic users);
  - `keycloak up`, then `keycloak seed`, which gives `dr.meera` (both hospitals),
    `nurse.asha` (alpha), `admin.bhavesh` (beta) and `new.neha`, each with fresh passwords and
    TOTP keys written to the git-ignored `deploy/seed/TEST_USERS.md`;
  - `frontend` (our nginx image, Host passthrough).

## Design

1. **Where the tests live.**
   - New files of ours in `apps/frontend/tests-ihr/` and `apps/frontend/playwright.ihr.config.ts`,
     using the frontend's own pinned Playwright, with no new npm dependency.
   - They are logged in UPSTREAM_PATCHES.md as new files (no upstream file changes), so upstream
     syncs do not conflict.
   - The config:
     - no globalSetup and no webServer;
     - baseURL per test;
     - `--host-resolver-rules=MAP *.localhost 127.0.0.1` (Docker publishes on 127.0.0.1 only);
     - traces and video only on failure, uploaded as an artifact;
     - no secrets in titles or logs.
2. **The browser:** Playwright's own Chromium for 1.61.1, installed in CI with
   `npx playwright install --with-deps chromium`. That is a CI-only download, version-matched to
   the runner, and recorded in VERSIONS.md. No browser runs on the server.
3. **A new CI job, "Browser isolation (Playwright)"**, in `apps.yml`.
   - **Triggers:** changes under `apps/frontend/`, `plugins/`, `deploy/dev/`, `scripts/care-dev.sh`
     or `deploy/docker/frontend*`; the weekly schedule; manual runs.
   - **Time limit:** 45 minutes.
   - **No new third-party action:** only the already-pinned checkout, setup-node, buildx and
     upload-artifact.
   - **Steps:**
     1. build `care_local` from the gha cache with the `indiehr.deps` label, so `care-dev.sh`
        does not rebuild it;
     2. `care-dev.sh up`, `seed`, `keycloak up`, `keycloak seed`;
     3. build the bundle same-origin (`deploy/frontend.env`, the bootstrap assertion and the
        egress check, as in `frontend-bundle.yml`);
     4. `care-dev.sh frontend`;
     5. run the specs. They read the users and TOTP keys from the generated `TEST_USERS.md` and
        compute codes in Node (HMAC-SHA1, 30 s). Every password and key is masked with
        `::add-mask::`.
4. **The specs:**
   - **Token isolation:**
     - `nurse.asha` (alpha only) signs in on alpha.
     - Her alpha cookie, copied to beta's host, is refused (403 on the current-user API).
     - Opening beta in the same browser ends at `/login?error=not_linked`.
     - `dr.meera` signed in on both has two different host-only cookies. Signing in on beta does
       not change alpha's cookie.
     - No request from either origin carries the other's cookie.
   - **Sign-out clean-up (Q-T9):**
     1. Sign in, then plant sentinel values under every key prefix the clean-up owns, plus the
        known gaps.
     2. Sign out.
     3. Assert: no cookie; sessionStorage empty; no persisted query cache; none of the owned
        keys; no `/api/` URL in Cache Storage; the "signed out" notice shown.
   - **Other tabs and other hospitals:**
     - a second tab of the same hospital ends at once (BroadcastChannel);
     - `dr.meera`'s session at the other hospital ends within the back-channel minute (poll,
       never sleep blindly).
   - **Idle:** a short idle limit (`IHR_SESSION_IDLE_SECONDS` in the CI environment) ends the
     session with the idle notice.
   - **Switcher:** `test.skip` naming R1-05. It is not built, so it never counts as passing.
5. **Clean-up gap found by the research (fixed in our own patch, logged).**
   - `clearStaffStorage()` does not clear `user`: a staff member chosen on the scheduling screen,
     with name and username, on a shared computer.
   - The patch adds `user` and the other person-bearing keys: `care_queue_*` preferences hold
     ids, not names, so they stay.
   - The spec asserts each.
   - Non-personal preferences stay: language, view preferences, the app version, navigation
     state.
6. **TOTP and lock-outs:** codes cannot be reused within their window, and five wrong codes lock
   the account. Each spec uses its own user, or the next code window, never a retry loop on a
   wrong code.

## Decisions (auto-accepted, awaiting the owner)

- **Q-K27:**
  - Playwright runs in CI only, with its own pinned Chromium downloaded there;
  - the switcher test is skipped until the switcher exists;
  - the sign-out clean-up also clears the `user` key.

## After the attack (2026-11-13): the design as it is built

One Opus reviewer covered feasibility, security and privacy, and a second agent tried to refute
each finding. Result: 11 findings, 3 confirmed and 8 partly, none refuted. Where this section and
the sections above disagree, this section wins.

1. **A non-member never reaches our "not linked" page (T1).** Keycloak's hospital flow stops her
   with its own membership gate, on the single-sign-on path too. The spec asserts what really
   happens:
   - the page is on `auth.localhost:8081` with "You are not a member of this hospital." (the
     locale is pinned to English);
   - beta has no `ihr_session` cookie;
   - her alpha session still answers 200.
   The not-linked redirect is already covered by `ihr_identity` tests and the live Keycloak tests.
2. **Check contents, not absence (T2).** The login page rewrites `care-auth-user` as an empty
   value, and an empty persisted query cache, after sign-out. So the spec asserts:
   - no planted sentinel value survives in any storage or cookie;
   - `care-auth-user` holds no user;
   - the persisted cache holds no query.
   It runs once when the notice appears, and again after the network is idle plus 2 s.
   - An explicit allow-list names the keys that may remain (language, view preferences, app
     version, navigation state and the like). Any other key fails the spec until it is
     classified.
3. **Assertions that would fail if the control broke (T3).**
   - Cross-hospital sign-out: sign out at beta, then within about 10 s make one alpha API call.
     It must be refused (403) and the app lands on `reason=session_ended`. This is well inside
     the 60 s refresh, so the test cannot pass on a timeout.
   - The second tab's URL carries `reason=signed_out`, and it sent no `/api/` request between the
     click and its navigation.
   - The server-side controls are already proven by the isolation mutations. The browser specs
     prove the web app's part.
4. **Idle (T4).** The idle limit stays at its default for the job. Idle is tested in the browser
   with Playwright's clock (`page.clock`): the warning, then the sign-out with `reason=idle`.
5. **TOTP (T5).**
   - Config: `workers: 1`, `retries: 0`, not fully parallel.
   - A per-user used-counter means a code is never typed twice, and a failed code is never
     retried.
   - The key comes from the `TEST_USERS.md` column exactly as Keycloak expects it.
   - The job checks one code against Keycloak before the browser specs start.
6. **Nothing secret in a public artifact (T6).**
   - No trace, video or HTML report is uploaded.
   - On failure: screenshots taken with no password or code field on screen, plus a small JSON
     per test (final URL, console errors, assertion message).
   - `::add-mask::` covers every value from `TEST_USERS.md`.
   - Log dumps on failure strip `code=`, `state=` and `session_state=`.
7. **Chromium (T7).** Playwright's own Chromium for 1.61.1 is installed, and its archive's
   SHA-256 is recorded in VERSIONS.md and checked in the job. The `--with-deps` apt step uses the
   runner's own Ubuntu archive.
8. **Service-worker guard (T8).** Not a clean-up check: "the service worker still caches no API
   responses". Wait for the worker, browse signed-in screens, and assert that every cache is
   `workbox-precache…` with no `/api/` URL.
9. **Triggers and bundle (T9).**
   - The job's filter is the union of the frontend and Keycloak filters, plus `apps/backend/**`,
     `deploy/dev/**`, `scripts/care-dev.sh` and the workflow file.
   - The bundle is built the same way as `frontend-bundle.yml`, into `deploy/dev/frontend-build`.
10. **The `user` key (T10).** It is the practitioner a *patient* picked in the public booking
    flow, not staff data. It is cleared in the patient sign-out (`usePatientSignOut`) and in
    `endLocally`, logged as a patch. The spec checks the patient path.
11. **Types (T11).** `tests-ihr/tsconfig.json`, typechecked in the existing Frontend job after
    "Type check".
12. **Local runs.** The dev machine cannot *build* the bundle, but can run the specs against the
    CI-built bundle in `deploy/dev/frontend-build` with Playwright's Chromium installed locally.
    Only the new clean-up patch needs a fresh bundle, so its check is proven in CI.

## After the code review (2026-11-13)

An independent review of the built job and specs found 14 issues; a second agent challenged
each (2 refuted, S2 and T4). What changed:

- **Masks first, even on failure (S1).** The steps that write secrets (`care-dev.sh up`, `seed`,
  `keycloak up` + `keycloak seed`) register the masks from an `EXIT` trap, so they hold when
  the step fails. The failure log dump masks again before printing anything.
- **The mask fails closed (S5).** `scripts/ci-mask-dev-secrets.py` exits non-zero on an env key
  that is neither secret-named nor on its reviewed non-secret list, on a `TEST_USERS.md`
  password or key cell (or hospital password line, or `raw:` comment) that yields nothing, and
  on a file with no value at all. It still masks what it found first. Tests:
  `scripts/test_ci_mask_dev_secrets.py` (pre-commit).
- **No cookie value is ever asserted (S3).** Specs ask `hasSessionCookie()` (true or false); the
  few value comparisons happen inside the spec and assert booleans. Evidence also scrubs every
  `ihr_session` value it sees (`Set-Cookie` headers and the jars at save time).
- **Scrub regex (S4)** anchors on `?`, `&`, `#` or `;`, so `session_code` is removed too.
- **Checkout tokens (S6).** Every `actions/checkout` in `.github/workflows/` sets
  `persist-credentials: false`; no job pushes or calls the API with that token.
- **Clean-up checked before the sign-in page runs (T1).** An init script copies storage at the
  start of each `/login` load; the leftovers check runs on that copy as well as on the page.
  The planted persisted query cache now carries the app's own buster and a dehydrated query
  marked to persist, so start-up code would restore it, not discard it.
- **Single sign-on waits (T2)** wait to leave for the sign-in route or Keycloak before waiting
  for the return, and the session poll tolerates a navigation in progress.
- **Server-ended sessions clean up as much as a sign-out (T3).** The patient portal's token,
  the booking `user` key and `selectedPatient` moved into `clearStaffStorage`, which both
  `endLocally` and `endExpiredSession` call. `other-tabs.spec.ts` now checks leftovers after
  `reason=session_ended` (alpha's own clean-up, no peer tab) and in the second tab's own
  sessionStorage.
- **Refused-code flag (T5)** is cleared at the start of each run (`lib/global-setup.ts`); the
  used steps stay. Both error messages name the state file.
- **Every storage key classified (T6).** `scripts/check-storage-keys.py` reads
  `apps/frontend/src` and fails on any key that is neither in
  `tests-ihr/storage-keys.json` (the spec's allow-list, now shared) nor cleared by
  `clearStaffStorage`. It runs in pre-commit and in the Frontend job. It found two
  unclassified keys on its first run: `care:developer_mode` (allowed) and `selectedPatient`
  (now cleared).
- **Time limit (T7)** 60 minutes until measured runs give a figure.

**Known gap (S7), item 10 amended.** `user`, `care_patient_token` and `selectedPatient` are
cleared on an explicit sign-out only: the patient's own, or any staff session end (sign-out,
another tab, idle, server-ended). A patient session that lapses (the portal treats its token as
valid for 14 minutes, and nothing clears it afterwards) or a kiosk simply abandoned keeps all
three. This is upstream behaviour; clearing them on load when the token is stale was rejected
for now (the `user` key is written before sign-in, so a reload mid-booking would lose the
chosen practitioner).
