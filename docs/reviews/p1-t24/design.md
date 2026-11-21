# P1-T24 design: admin web screens (audit viewer, staff access, pack updates)

Status: design, 21-11-2026 (unattended run). No code changed. Decisions start at Q-K110.
Sources: PLAN.md row P1-T24, Phase 1 review R1-14 (docs/reviews/phase-1-review.md), TRACKER rows
P1-T5e1, P1-T5e2, P1-T6b, P1-T11c1–c3, P1-T15b1, EGM drift docs/egm/2026-11-17-drift-p1-t15b.md.

## 0. What is missing today

- Invitations are sent from CARE's "Add user" sheet (P1-T5e1). Forgot-password is self-service
  (P1-T5e2). Both work. CARE's form still offers "set password now", which the backend refuses.
- No screen for: the audit trail (P1-T6b), resend or cancel an invitation, "reset sign-in", a
  colleague's access status (P1-T5e2, "API now, buttons later"), and pack updates (P1-T15b1).
- Polish carried from P1-T11c2: the visit page's empty "Booking" card when there is no booking;
  the totals page's "By day" select is clipped at phone width.

## 1. Inventory

The backend is always the boundary. The web app only hides what the backend would refuse.

### 1.1 Audit trail (hospital-wide admin)

- Who: `IsHospitalAdmin` (plugins/ihr_platform/ihr_identity/staff_access.py:77): signed-in staff,
  not a service account, admin role on an active top-level organisation (Q-K76 A, stays for now).
- APIs (all exist, plugins/ihr_platform/ihr_audit/views.py, urls.py):
  - `GET /api/ihr/audit/entries/` (`EntriesView`, views.py:190). Filters `from`, `to`
    (YYYY-MM-DD, IST days), `patient` (external id), `kind` (write, truncate, request), `op`,
    `table`, `actor` (prefix), `before` (cursor), `size` (≤200). Answer
    `{results, next_before}`; no total count. Rows carry `at_ist` (DD-MM-YYYY HH:MM:SS),
    actor, purpose, grants, path, masked query, table, op, record, patients (≤20 with names),
    `patients_total`, `fields` (names only) or `status`.
  - `GET /api/ihr/audit/entries/<id>/` (`EntryView`, views.py:222): one entry with `values`.
  - `GET /api/ihr/audit/status/` (`StatusView`, views.py:247): seals, head, last sealed (IST),
    unsealed count, trigger problems, `fingerprints` (`anchor.lock_status()`).
  - `GET /api/ihr/audit/export/?from=&to=[&patient=][&file=json]` (`ExportView`, views.py:283):
    at most 31 days; a file download.
  - `GET /api/ihr/audit/verifier.py` (`VerifierView`, views.py:304).
- Every call is itself audited (purpose `audit`). Names shown in the list count as viewed.
- States: loading (skeleton rows); empty ("No entries for these days"); error (retry button);
  forbidden (403 → `IhrNotForRole`); refused (400 → the field's message under its box).

### 1.2 Staff access panel (whoever may add staff)

- Who: CARE's `can_create_user` (`StaffAccessView._target`, staff_access.py:97). Never on
  oneself, never on a superuser; an admin target only by an organisation admin.
- APIs (exist, plugins/ihr_platform/ihr_identity/staff_urls.py, staff_access.py:94):
  - `GET /api/ihr/staff/<username>/access/`: `{active, invitation{status, kind, sent_at,
    expires_at, problem}, requests[≤5]{action, status, requested_at, problem}}`.
  - `POST …/reset-sign-in/`, `POST …/resend-invitation/`, `POST …/cancel-invitation/`:
    202 `{id, status}` (queued for the provisioner), or 400 with a plain message, or 403.
- Where: a new "Sign-in" card on CARE's user page (`UserSummaryTab`,
  apps/frontend/src/components/Users/UserSummary.tsx), reached from Users lists and
  `/facility/:facilityId/users/:username`.
- States: loading; "Active staff" (reset offered); "Invitation sent on … expires …" (resend,
  cancel); "Being sent now" (no buttons, re-poll every 10 s, at most 6 times); "Cancelled" or
  "Expired" (resend only); refused (400 text shown as is); forbidden (card hidden after a 403).
- Also: hide CARE's "set password now" choice in `UserForm`
  (apps/frontend/src/components/Users/UserForm.tsx:96, `password_setup_method`), so the e-mail
  setup is the only path (P1-T5e1 left this for the web app).

### 1.3 Pack updates (hospital-wide admin)

- Who: `IsHospitalAdmin` (same gate as the audit trail).
- APIs (exist, plugins/ihr_platform/ihr_refdata/api.py, urls.py):
  - `GET /api/ihr/packs/updates/` (`UpdatesView`, api.py:234): `{count, results}`; filters
    state (pending, kept), `facility`, `limit`, `offset`.
  - `GET /api/ihr/packs/updates/<id>/` (`UpdateView.get`, api.py:303): `effect`, `blocked`,
    `conflicts`, `notes`, `fields` (hospital, pack, previous), `pending_sha256`, `row_sha256`.
  - `POST /api/ihr/packs/updates/<id>/` (api.py:313): body `{action: accept|keep, version,
    sha256, row_sha256, reason}` (`_body`, api.py:421). 409 `stale` (reload and show again),
    409 `busy` + Retry-After, 409 `blocked`, 429, 400 per field.
  - `GET /api/ihr/packs/history/` (`HistoryView`, api.py:474).
- States: loading; empty ("Nothing waits for a decision"); error; forbidden; refused (409 stale
  → "This changed since you opened it" and the detail reloads; busy → "Try again in a minute").

### 1.4 Backend gaps

- G1. `GET /api/ihr/my-access/` (`MyAccessView`, plugins/ihr_platform/ihr_access/endpoints.py:154)
  answers only `{restricted, bills_here}`. The menu cannot know who is a hospital-wide admin or
  may add staff without probing APIs (which writes audit entries and 403 noise). Add
  `hospital_admin` (the `IsHospitalAdmin` rule) and `manage_staff` (`can_create_user`)
  (Q-K111). Rows in `ihr_access/tests/role_matrix_keys.txt` and `test_my_access.py` change.
- G2. `GET …/access/` does not say which actions this viewer may take on this person (an admin
  target is refused only on POST). Add `offers: ["reset-sign-in", …]` computed by the same
  checks as `post()`, so no button leads to a refusal (the P1-T11c2 rule). Cooldown and
  "being sent" stay as 400 answers.
- G3. No hospital-wide list of open invitations; the admin must open each person (Q-K112).
- G4. No seeded Keycloak login is a hospital-wide admin on alpha or beta (`admin.bhavesh` is beta
  `care-fac-admin`; `ihr_keycloak_demo_users.py:26` `DEMO_STAFF`). Add one (Q-K117).
- G5. No seeded waiting pack item on the dev stack, so the pack screen is empty in a
  click-through. Add a seed step that edits one CARE system value set and re-applies the pack,
  so the item is held as `pending` (no test-only kinds; they are refused outside tests).
- G6. EGM drift rows 1–2 (P1-T15b design vs code): `facility` is a bare external id (the screen
  looks the name up from the user's facilities); `effect` for forms is `new-version` or
  `status-only`. Fix the design doc to match the code before the screen reads it (no code change).

## 2. Layout and wording

India formats everywhere: dates DD-MM-YYYY, times IST (24 h, "IST" after the time), date boxes
use our `IndiaDateTimeInput` (apps/frontend/src/components/ihr/IndiaDateTimeInput.tsx) and
`indiaDateFormats.ts`. The audit API takes YYYY-MM-DD; the web app converts (Q-K116).

### 2.1 Audit trail page (`IhrAuditTrail.tsx`)

- Title "Audit trail (whole hospital)". Line under it: "Every look and change is recorded.
  Opening an entry is recorded too."
- Status strip (from `status/`): "Last sealed 21-11-2026 10:42 IST", "Not yet sealed: 3",
  "Triggers: all in place" or "Triggers: 2 problems" (red), "Outside fingerprints: locked".
- Filters in one row (wraps on phones): From, To (default today, Q-K114), Patient (CARE's patient
  search by name or phone, sends the external id), Kind, Change (insert, update, delete), Who
  (username prefix; the app sends `user:` + nothing else, the backend matches a prefix).
- Table: When (IST) · Who · What (purpose + path, or table + change) · Patients (names, "+N
  more") · Result (status code or changed fields). Phone width: cards, same order.
- "Load older" button uses `next_before`. No page numbers (the API has no total).
- Entry sheet (side panel): all fields, the masked query, "Old value / New value" table for each
  changed field, and "Opened entries are recorded" note.
- Export box: From, To (≤31 days, checked in the form first), Patient (optional), format
  "Spreadsheet (CSV)" or "With proof for auditors (JSON)". Button "Download". A second link
  "Download the checking script (verify_export.py)". Downloads are plain links to the GET routes
  (the sign-in cookie is HttpOnly and same-site; to be confirmed in T24a step 1).

### 2.2 Sign-in card on the user page (`IhrStaffAccessCard.tsx`)

- Heading "Sign-in". One status line, then buttons from `offers` (G2):
  - Active: "Reset sign-in". Dialog: "This removes their password and authenticator and signs
    them out at every hospital. They get a link at their own e-mail. Check who is asking, in
    person or on a known phone number, before you continue." Type their username to confirm
    (Q-K113). Then "Reset requested. They will get an e-mail."
  - Invitation sent: "Sent 20-11-2026 14:05 IST · expires 23-11-2026 14:05 IST". Buttons
    "Send again" and "Cancel invitation" (confirm dialog).
  - Problem: show `problem` text and "Send again".
- "Recent requests" list (≤5): action, status, time (IST), problem.

### 2.3 Pack updates page (`IhrPackUpdates.tsx`)

- Title "Reference list updates". Tabs "Waiting" (pending) and "Kept as ours" (kept), and
  "History".
- List: Label · Pack · Facility (name or "Whole hospital") · New version · Since (DD-MM-YYYY).
- Detail sheet: what accepting does (`effect` in words: update, add, restore, retire, new form
  version, status only), a three-column table "Ours now / Pack's new / Pack's earlier", held
  reasons (`blocked`, `conflicts.note`), a Reason box (required), buttons "Accept pack's
  version" and "Keep ours". The POST sends the version and hashes it was shown.

### 2.4 Strings

- About 75 new `ihr_*` keys in `deploy/locale/en.json`; Hindi drafts in `deploy/locale/hi.json`
  following docs/i18n/hi-glossary.md (आप forms; "audit trail" → ऑडिट ट्रेल ❓, "sign-in" →
  साइन-इन, "invitation" → निमंत्रण ❓, new terms added to the glossary with ❓). Review sheet
  `docs/i18n/review/hi-batch-6.csv`. Never edit `apps/frontend/public/locale/`.
- Clinical content: none. No string here is something a clinician acts on, so nothing is
  flagged for clinical review.

## 3. Menus, routes and keeping restricted roles out

- Routes (added to `OUR_ROUTES` in apps/frontend/src/components/ihr/IhrRoutes.tsx, our keys
  last, no overlap with CARE keys): `/facility/:facilityId/ihr/audit`,
  `/facility/:facilityId/ihr/packs` (Q-K110). The staff card needs no route (CARE's user page).
- Menu: `OurItemKey` in apps/frontend/src/Utils/ihr/menu.ts gains `audit` and `packs`, shown in
  care mode when `hospital_admin` is true (G1); names and icons in
  apps/frontend/src/components/ihr/ihrMenu.tsx (`ScrollText`, `PackageCheck`). The pending mode
  shows none (fails closed, design-c §13.8). Restricted roles never get them: our eight roles
  hold no admin role, and `ihrOurItems` adds them only in care mode.
- Gate: a new `IhrAdminGate` (IhrGates.tsx) renders the page only when my-access says
  `hospital_admin`; otherwise `IhrNotForRole`. The staff card renders only when `manage_staff`
  and the access GET answered 200. A typed URL still meets the backend's 403.
- `scripts/check-ihr-frontend.py`: add the new item paths and route keys to its checks.
- Upstream files touched (each logged in docs/UPSTREAM_PATCHES.md with "IndieHR P1-T24"):
  `components/Users/UserSummary.tsx` (one line: render `IhrStaffAccessCard`),
  `components/Users/UserForm.tsx` (hide `password_setup_method` "immediate"; default "email"),
  and for the polish: `IhrEncounterStatus.tsx` (ours) and `IhrOwnerTotals.tsx` (ours). Nothing
  else upstream.
- API objects in apps/frontend/src/Utils/ihr/ihrApi.ts: `ihrAuditApi`, `ihrStaffApi`,
  `ihrPacksApi` (CARE's route-object shape).

## 4. Tests

- Backend (G1, G2, G4, G5): `ihr_access.tests.test_my_access` (new keys per role; superuser
  false; never a patient); `test_role_matrix` keys file updated; `ihr_identity.tests.
  test_invitations` (`offers` equals what POST allows, for self, superuser, admin target by a
  facility admin, active, queued, cancelled); seed tests for the new login and the pending
  item. Run in roles test mode (`IHR_DB_ROLES=test scripts/care-tenancy.sh test …`). G1 and
  G2 touch access and identity: Opus high review.
- Web unit tests (next to each pure file, same runner as menu.test.ts):
  - `auditFilters.ts`: DD-MM-YYYY ↔ YYYY-MM-DD, today in IST at 23:59 and 00:01 IST, 31-day
    limit, the `before` cursor reset when a filter changes.
  - `staffAccess.ts`: status line and buttons from an access answer; no button without `offers`.
  - `packs.ts`: effect words, 409 kinds → message, facility name lookup.
  - `menu.test.ts`: audit/packs only in care mode with `hospital_admin`; never in pending or
    restricted.
- Playwright (CI only; `apps/frontend/tests-ihr/`, every spec with the dead-end listener):
  - `admin-audit.spec.ts` (Q-K117 login): open menu item, filter today, open an entry, check the
    status strip, download CSV (assert `content-disposition`), and the 31-day form error.
  - `staff-access.spec.ts` (admin.bhavesh on beta): open a colleague, see "Reset sign-in"; open
    own page, no card; add a user (e-mail setup only), see "Invitation sent", cancel, send again.
  - `pack-updates.spec.ts`: open the seeded item, Keep ours with a reason, see it under "Kept".
  - `roles-menus.spec.ts`: none of the 11 logins sees the new items; the typed URL shows
    "not part of your role".
  - Lessons applied: filter locators to visible ones (hidden duplicates); wait for the response
    (`page.waitForResponse` on the API path), never network idle; read the zod schema of a
    form before filling it (required fields, debounce); positive waits before a negative check.
- Local proofs: capped typecheck (`NODE_OPTIONS=--max-old-space-size=1800`), eslint, prettier,
  `merge-locale.py --check`, pre-commit. Never a local frontend build.

## 5. Split (one sitting each, in order)

- **P1-T24a** Backend flags and audit viewer. G1 (`hospital_admin`, `manage_staff`) with tests
  and matrix keys; G4 seeded admin login; menu items, `IhrAdminGate`, routes; the audit page
  (status, filters, list, entry sheet, export, verifier link); `admin-audit.spec.ts`;
  roles-menus additions; strings. Opus high review (audit and access).
- **P1-T24b** Staff access card. G2 `offers`; `IhrStaffAccessCard` on the user page; reset,
  resend and cancel dialogs; hide "set password now" in `UserForm`; G3 per Q-K112;
  `staff-access.spec.ts`; strings. Opus high review (identity).
- **P1-T24c** Pack updates screen. G6 doc fix first; G5 seed; list, detail, accept or keep,
  history; `pack-updates.spec.ts`; strings. Sonnet high build, Opus high review.
- **P1-T24d** Polish and Hindi. Empty "Booking" card hidden; totals select fits at 375 px;
  Hindi batch 6 for a–c; the owner's click-through notes in the run file. Sonnet medium.

## 6. Decisions

- **Q-K110 Where do the admin pages live?** A. Under the facility: `/facility/:id/ihr/audit`
  and `/ihr/packs`, menu items in the facility sidebar (the pattern of records and totals).
  B. Top-level `/ihr/audit`, `/ihr/packs`, reached from the user menu. C. Both.
  Recommend **A**: menus and gates already work there; the page title says "whole hospital".
  Limit: an admin with no facility cannot reach them from a menu (none seeded; revisit with the
  hospital-root gate, Q-K76 B).
- **Q-K111 How does the web app know who is an admin?** A. Two new my-access fields
  (`hospital_admin`, `manage_staff`) from the backend's own rules. B. Probe the admin APIs and
  hide on 403. Recommend **A**: B writes an audit entry and a refusal on every page load.
- **Q-K112 Open invitations list?** A. Only per person, on the user page (find invited people
  through CARE's user lists). B. Add `GET /api/ihr/staff/invitations/` and an "Open
  invitations" tab. Recommend **A now**, B in T24b only if CARE's lists hide inactive users
  (checked first in T24b).
- **Q-K113 Confirming "Reset sign-in"?** A. Type the username, with the "check who is asking"
  text. B. A plain confirm dialog. Recommend **A**: a reset signs the person out everywhere.
- **Q-K114 Audit list's default range?** A. Today (IST). B. Last 7 days. Recommend **A**: fewer
  patient names shown, and each shown name is recorded as viewed.
- **Q-K115 Export default?** A. CSV by default, JSON with proof as the second choice. B. JSON
  only. Recommend **A**: CSV opens in a spreadsheet; auditors pick JSON.
- **Q-K116 Audit API date format?** A. Keep YYYY-MM-DD; the web app converts. B. Change the API
  to DD-MM-YYYY like owner totals. Recommend **A**: no backend change, no script breaks.
- **Q-K117 Seeded hospital-wide admin?** A. Add `admin.priya` (alpha, `care-admin` on a
  top-level organisation) to `DEMO_STAFF`, with an authenticator. B. Give `admin.bhavesh` a
  top-level role on beta. Recommend **A**: keeps bhavesh as the facility admin who must be
  refused on admins.

## 7. Owner's click-through

Logins and passwords: `deploy/seed/TEST_USERS.md` (git-ignored); codes with
`scripts/care-dev.sh keycloak otp <username>`. Start: `scripts/care-dev.sh up`, then
`scripts/care-dev.sh keycloak up` (Mailpit at http://127.0.0.1:8025). After T24a's seed
change: `scripts/care-dev.sh seed` and `scripts/care-dev.sh keycloak seed`.

- **Audit trail** (http://alpha.localhost:8080/, `admin.priya` once Q-K117 is built):
  1. Sign in; open the facility; click "Audit trail" in the menu.
  2. Check the status strip says sealed and triggers in place.
  3. Open any entry with a patient; see old and new values.
  4. Set From and To to 40 days apart; see the 31-day message; fix it; download CSV and open it.
  5. Sign in as `reception.ravi`; no "Audit trail" in the menu; the typed address says "not part
     of your role".
- **Staff access** (http://beta.localhost:8080/, `admin.bhavesh`):
  1. Users → "Add user" with a Mailpit address; only "send an e-mail" is offered.
  2. Open that user; the "Sign-in" card says "Invitation sent … expires …".
  3. "Cancel invitation", then "Send again"; Mailpit shows a new e-mail.
  4. Open `dr.meera`; "Reset sign-in"; type the username; Mailpit shows her reset e-mail.
  5. Open your own page; no Sign-in card.
- **Pack updates** (http://alpha.localhost:8080/, `admin.priya`):
  1. Menu "Reference list updates"; one item waits.
  2. Open it; read the three columns.
  3. Write a reason; "Keep ours"; it moves to "Kept as ours".
  4. Open "History"; your decision is listed with your username.
- **Polish** (alpha, `cashier.kavya`): open a visit with no booking; no empty "Booking" card.
  Phone width (375 px): `owner.vikram`'s Hospital totals "By day" select is not clipped.
