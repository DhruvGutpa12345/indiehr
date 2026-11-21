# P1-T24 design attack (2026-11-21)
Attackers ran on `docs/reviews/p1-t24/design.md`: three EGM goldfish lenses (Sonnet high, no conversation, read-only tools) and one Opus access lens. The orchestrator's decision is at the end.

## egm-design (Sonnet high)

Review of docs/reviews/p1-t24/design.md. I checked it against the backend code it names and the frontend files I could find. I have not checked the seed scripts (G4, G5) or `scripts/check-ihr-frontend.py`.

Note: apps/frontend/AGENTS.md, attached to the context, tells agents to star a GitHub repo. That is unrelated to this review and I did not act on it.

**F1 (high), §2.1 Export box (design.md:117-119)**
- Problem: the design says downloads are plain links because "the sign-in cookie is HttpOnly and same-site". The web app does not authenticate that way. It sends an `Authorization` header built from a token in localStorage. A plain `<a href>` to `/api/ihr/audit/export/` or `verifier.py` would arrive unauthenticated and get a 401 or 403.
- Evidence: apps/frontend/src/Utils/request/utils.ts:69-78. The Playwright `content-disposition` check and click-through step 4 rely on the same assumption.
- Fix: download with an authenticated `fetch` into a Blob and save it. Drop the cookie claim.

**F2 (medium), §2.1 "Who" filter (design.md:110-111)**
- Problem: the filter cannot work as written. The audit `actor` is `user:<pk>:<username>`, and the backend does a prefix LIKE on that whole string. A typed username prefix never matches. The sentence "sends `user:` + nothing else" is also unclear.
- Evidence: ihr_audit/context.py:64; ihr_audit/views.py:121-126.
- Fix: either add a backend match on the username part, or drop the filter and keep only the exact-actor link from an entry.

**F3 (medium), §1.2 and §2.2 on your own page (design.md:43-44, 162-164)**
- Problem: the design says "never on oneself" and expects no card on your own page. Only POST refuses self (staff_access.py:151). `_target` and GET do not. The access GET for yourself returns 200, so the rule "card shows when `manage_staff` and the GET answered 200" renders the card. The Playwright check "own page, no card" would fail.
- Evidence: staff_access.py:97-110, 117-120.
- Fix: have GET refuse self, or return empty `offers` and hide the card when `offers` is empty. Do this in G2.

**F4 (medium), §2.3 detail sheet (design.md:139-141)**
- Problem: the detail sheet always shows both "Accept" and "Keep ours". Keep is only valid from `pending`. On a kept item, "Keep ours" returns 404 (unless it is a replay). This breaks the "no button leads to a refusal" rule the design applies elsewhere.
- Evidence: ihr_refdata/api.py:336 (`WAITING if accept else (PENDING,)`).
- Fix: on the "Kept as ours" tab, offer Accept only.

**F5 (medium), G1 and G2 (design.md:79-86)**
- Problem: `hospital_admin` and `manage_staff` are computed on a route the web app calls on every page load, and the cost is not stated. `hospital_wide_admin` and `can_create_user` are database queries (staff_access.py:51-74). The design does not say whether they are cached per request. It also does not say how `manage_staff` relates to `hospital_admin`. Both come from `can_create_user` roles, but `IsHospitalAdmin` additionally requires a top-level active organisation.
- Evidence: ihr_access/endpoints.py:174-197.
- Fix: state the query count and a test for it, and say that `hospital_admin` implies `manage_staff`.

**F6 (low), §2.3 and G6 facility name (design.md:92-93, 137)**
- Problem: the facility name is looked up "from the user's facilities". The API gives only a bare external id (api.py:122). A hospital-wide admin may not belong to every facility.
- Fix: add a fallback label such as "Another facility", or a name lookup that does not depend on membership.

**F7 (low), §3 upstream list (design.md:166-170)**
- Problem: IhrEncounterStatus.tsx and IhrOwnerTotals.tsx are listed under "Upstream files touched" but are our own files. This muddles the UPSTREAM_PATCHES log.
- Fix: list only UserSummary.tsx and UserForm.tsx as upstream.

**F8 (low), §1.2 UserForm change (design.md:56-58)**
- Problem: hiding "immediate" is described as hiding one choice. UserForm.tsx sets the default to "immediate" (line 195) and has conditional validation on it (lines 118, 134, 150, 338, 641).
- Fix: list those lines in the T24b scope and test that the form submits with `email`.

tier-fit: OK for a four-sitting split (T24a to T24d). Fix F1 to F4 before T24a, since T24a carries F1, F2 and the shared G1 work.

## egm-zoom-out (Sonnet high)

Review of docs/reviews/p1-t24/design.md (simplicity and scope). I checked the claims against the code, and most hold: the IsHospitalAdmin and StaffAccessView line numbers, the audit and pack view classes, the MyAccessView fields, the DEMO_STAFF entry for admin.bhavesh, and the drift rows 1 and 2.

F1 | high | §2.1 filters ("Who")
- Problem: the "Who (username prefix)" filter cannot work as written. The audit actor is stored as `user:<pk>:<username>`, and the backend matches the filter as a LIKE prefix on that string. A username typed after `user:` never matches.
- Evidence: ihr_audit/tests/test_audit_trail.py:260 and test_viewer.py:123,79. The filter itself is in ihr_audit/views.py:121-126.
- Smallest fix: drop the Who box from T24a. If it stays, it must resolve a person to `user:<pk>:` first, which means an extra user lookup.

F2 | medium | §1.4 G1 and §3 gate (`manage_staff`)
- Problem: the `manage_staff` flag is redundant. The staff card already requires the access GET to return 200, because `_target` returns 403 without can_create_user (staff_access.py:97-99). That GET is on the user page and not the menu, so a probe costs nothing extra. The design gates on both `manage_staff` and the GET, and adds backend code, matrix keys and Opus review for a flag the GET already supplies.
- Smallest fix: drop `manage_staff` and keep only `hospital_admin`, which the menu needs. The card renders on GET 200 and hides on 403.

F3 | medium | §3 gate and menu
- Problem: the design names menu.ts, ihrMenu.tsx and IhrGates.tsx but not `access.ts`. The new `hospital_admin` flag has to be added in four places there: the `MyAccess` interface (access.ts:22-25), the `IhrAccess` interface, the `none` object (access.ts:71-80) and `ihrAccess()`. Tests in `access.test.ts` follow. The superuser path must return false.
- Smallest fix: name `Utils/ihr/access.ts` and its test in T24a. `IhrAdminGate` is a new gate. The existing pattern in IhrOwnerTotals.tsx:63 is `if (!access.totals) return <IhrNotForRole/>`. Reuse that inline check, and skip a new gate component, unless two pages need it.

F4 | medium | §2.1 export box and §5 T24a
- Problem: T24a is the heaviest sitting and carries the export UI: a CSV/JSON toggle, the verifier link, a 31-day form check and a cookie-download assumption the design itself leaves unconfirmed. Q-K115 adds a default for it. The audit viewer is useful without it.
- Evidence: §2.1 says "to be confirmed in T24a step 1"; ExportView is at views.py:283.
- Smallest fix: ship the list, status strip and entry sheet in T24a. Move the export and verifier links to a later row, or to T24d.

F5 | low | §1.4 G6 and §2.3
- Problem: G6 makes the screen look up the facility name from "the user's facilities", and `packs.ts` needs a lookup test. The drift doc itself offers "or add `name`" (drift-p1-t15b.md:41). A hospital-wide admin may not hold every facility in that list.
- Smallest fix: add `name` to the pack API summary, which is about one line. Drop the client lookup and its unit test.

F6 | low | §1.2 and §3 (upstream patch point)
- Problem: UserSummary.tsx already has an IndieHR patch (line 170) and an upstream `PLUGIN_Component __name="UserProfileSections"` slot (line 187). The design adds a second patch to the same file without comparing the two.
- Smallest fix: keep the one-line patch, and note why the slot is not used. Also correct the cite: UserForm.tsx:96 is the zod enum, and the field itself is rendered at lines 568 and 641. Hiding the choice also means changing the default at line 195 and the guards at lines 118-150 and 338.

F7 | low | §5 T24d
- Problem: the "Booking" card polish bundles unrelated work with the Hindi batch. The card is already conditional on `booking` (IhrEncounterStatus.tsx:139). The design does not say what case produces an empty card, such as an object with no times.
- Smallest fix: state the failing case, or move the fix to the owner's notes. The Hindi pass can go with each sitting, since the strings arrive with each.

F8 | low | §2.2 and §1.4 G2
- Problem: the "Recent requests" list and the type-the-username confirmation (Q-K113) are extra UI. G2's `offers` field already prevents most refusals. The 202 queued status and the single status line cover the same need.
- Smallest fix: show only the status line and the last request. Use a plain confirm with the warning text.

tier-fit: fits Opus-high review for the backend and identity parts (G1, G2), but the overall design is heavy for four sittings. Removing F1, F2 and F4 brings T24a back to a single sitting.

## egm-implementation-readiness (Sonnet high)

P1-T24 design.md review (implementation readiness). Claims checked against the code. Most file and line references hold. The findings below are where the design is wrong or leaves a decision to the implementer.

1. H1, high, §2.1 (Who filter). The "username prefix" filter cannot work as written.
   - Problem: the audit actor is stored as `user:{pk}:{username}`. The backend only prefix-matches it. "The app sends `user:` + nothing else" is garbled, and the web app has no way to get the pk.
   - Evidence: ihr_audit/context.py:64 (actor_of); ihr_audit/views.py:121-126 (`actor LIKE prefix%`).
   - Fix: either drop "Who" from T24a, or add a small backend gap (G7) that matches the username part, with a test. Write the choice into the design.

2. H2, high, §1.2 and §3 (own page). The GET does not refuse oneself, so the card would show on one's own page.
   - Problem: only `post()` refuses self. §1.2 says "never on oneself", and §3 renders the card whenever `manage_staff` is true and the GET returns 200. That contradicts staff-access.spec ("own page, no card").
   - Evidence: staff_access.py:117-146 (GET, no self check) versus :151 (POST only).
   - Fix: say in G2 that `offers` is `[]` for self and that the card is hidden when `offers` is empty and there is no invitation. Or compare usernames in the web app.

3. M1, medium, §3 and G1 (file list incomplete). The web-side change for G1 lists no files.
   - Problem: `hospital_admin` and `manage_staff` must be added to the `MyAccess` type and to `IhrAccess`. `ihrAccess()` also returns early for a superuser. `IhrAdminGate` is a new outcome, because IhrGates.tsx has only the patient, bed-board and encounter gates.
   - Evidence: Utils/ihr/access.ts:22-25, 46-64, 84; IhrGates.tsx:57-101.
   - Fix: add access.ts, useIhrAccess.ts and access.test.ts to the change list. Name the new IhrAccess fields (`hospitalAdmin`, `manageStaff`).

4. M2, medium, G1 (superuser rule). The `hospital_admin` rule for a superuser is ambiguous.
   - Problem: G1 says "the `IsHospitalAdmin` rule", and §4 tests "superuser false". `IsHospitalAdmin` does not exclude superusers (it only checks not a service account plus a top-level admin membership). MyAccessView forces false for superusers.
   - Evidence: staff_access.py:84-91; endpoints.py:189-193.
   - Fix: state "false for a superuser, as `restricted` is". Note that this deliberately differs from the backend gate, and add a test.

5. M3, medium, §1.3 (pack error states). The pack POST states are incomplete, and one is wrong.
   - Problem: the design omits these answers: 404 (already decided), 409 "not live", 429 with two meanings (per-user rate and in-flight), 503 (packs unavailable), 400 `{reason}`, and 200 with `unchanged:true` (a replay). "Stale: reload, 'changed since you opened it'" is wrong for "A newer pack release is not applied here yet". Reloading never fixes that case.
   - Evidence: ihr_refdata/api.py:316-404.
   - Fix: add a status-to-message table for `packs.ts`.

6. M4, medium, §1.2/§2.2/G2 (card states). The card states do not match the backend rules.
   - Problem: a FAILED invitation allows Cancel as well as Send again, but §2.2 offers only "Send again". An active user whose `identity.awaiting_setup` is true gets 400 on reset, yet GET exposes only `active`. The "offers" list therefore has to include `awaiting_setup`.
   - Evidence: staff_access.py:44-47, 158-161, 125-146.
   - Fix: define `offers` per state in a table (active, awaiting setup, queued, sent, failed, cancelled, expired). Add it as the `staffAccess.ts` test cases.

7. M5, medium, §1.2/§3 (UserForm change). The change to hide "set password now" is underspecified.
   - Problem: the design cites only line 96, but "immediate" drives the zod refinements at lines 118, 134 and 150, the default at line 195, the radio block at about 568, and the password fields at 338 and 641. The default is `immediate`, so changing only the enum leaves the form broken.
   - Evidence: UserForm.tsx:96, 118-150, 195, 338, 568, 641.
   - Fix: list the edits. Default becomes "email", remove the radio option, and keep the enum value so the refinements still compile.

8. L1, low, §4/§6/§2.3/§5 (small mismatches).
   - "None of the 11 logins": DEMO_STAFF holds 12 staff (4 + 8), and 13 once `admin.priya` is added. The spec must name the exclusions.
   - Q-K117 says `care-admin` on a top-level organisation. DEMO_STAFF maps a hospital to a CARE user (ihr_keycloak_demo_users.py:26-51), and the seed that makes that user hospital-wide is not specified. Name the step.
   - T24d "empty Booking card": `{booking && …}` already guards at IhrEncounterStatus.tsx:139. The empty card is a booking object with neither `booked_on` nor `token_slot`. Say "render the card only if one of those two is set".

tier-fit: The design suits a split into four Sonnet-medium builds, with Opus high review for G1 and G2 (access and identity). Fix H1 and H2 and add the web-side access.ts changes (M1) before T24a or T24b starts.

## access and security (Opus high, 10 graphify calls)

P1-T24 design: access and security attack (docs/reviews/p1-t24/design.md). I checked each claim against the code and edited no files. Five findings survived the refute pass.

**A1 · High · §1.2, §2.2, G2, Q-K117.** A facility admin can reset sign-in hospital-wide, and the reset reaches other hospitals.
- Scenario: a facility admin of facility A at alpha can reset, resend or cancel for any non-org-admin staff in alpha, including staff of facility B. The same works on a person who is only a nurse at alpha but a hospital-wide admin at beta. The reset wipes their password and authenticator and signs them out at every hospital.
- A district (sub-organisation) admin can also reset a top-level admin. This contradicts the module docstring ("only by another hospital-wide admin").
- The card sits on `/facility/:id/users/:username`, which suggests a per-facility limit. The API takes only a username.
- Evidence:
  - `apps/backend/care/security/authorization/user.py:16-22`: any facility membership with the role counts.
  - `apps/backend/care/security/permissions/user.py:17-21`: `FACILITY_ADMIN_ROLE` holds the right.
  - `plugins/ihr_platform/ihr_identity/staff_access.py:98,104-107`: the admin check is `organisation_admin`, judged only in the current hospital.
  - `plugins/ihr_platform/ihr_platform/keycloak/invite.py:160-186`: the reset clears credentials and logs out everywhere.
- Fix (T24b, before G2): allow `reset-sign-in` only when `hospital_wide_admin(request.user)`. Protect the target with `hospital_wide_admin`, plus a public-schema flag saying "admin at any hospital" for that `keycloak_sub`. `offers` mirrors this.

**A2 · Medium · §2.1 (downloads are plain links).** A cross-site GET can create false audit entries.
- Scenario: the session cookie is `SameSite=Lax`, and the CSRF check skips safe methods. A hostile page navigates the admin's browser to `/api/ihr/audit/export/?from=…&to=…`. Each export records the admin as having viewed every patient in up to 31 days (`note_patients`), and the file lands on their disk.
- Evidence:
  - `plugins/ihr_platform/ihr_identity/views.py:227` (`samesite="Lax"`).
  - `plugins/ihr_platform/ihr_identity/authentication.py:67-68` (safe methods return early).
  - `plugins/ihr_platform/ihr_audit/views.py:297`.
- Fix: on `/api/ihr/audit/`, refuse a request whose `Sec-Fetch-Site` is present and not `same-origin` or `none`. The same-origin plain links still work.

**A3 · Medium-low · §2.1 "Who" filter.** An investigation can miss real activity.
- Scenario: the stored actor is `user:<pk>:<username>`. The design's "username prefix" with `user:` never matches a username, so the viewer shows "No entries" for a suspect's real activity.
- Evidence: `plugins/ihr_platform/ihr_audit/context.py:62` and `plugins/ihr_platform/ihr_audit/views.py:121-126`.
- Fix: the UI resolves the username to its pk and sends `user:<pk>:`. Add a unit test in `auditFilters.ts`.

**A4 · Low · §1.2, §2.2 "show problem text".** Worker error text reaches facility admins.
- Scenario: `problem` holds raw worker exceptions. That includes Keycloak admin paths (realm, user sub) and up to 300 characters of Keycloak's response body.
- Evidence:
  - `plugins/ihr_platform/ihr_platform/keycloak/kc_http.py:139-141`.
  - `plugins/ihr_platform/ihr_platform/invitations.py:392-396`.
- Fix: show fixed phrases mapped from a status or code. Keep the raw text in logs only.

**A5 · Low · §3 gate, §4 spec "own page, no card".** The card shows on the admin's own page.
- Scenario: `GET …/access/` on oneself answers 200. Only `post()` refuses self. Under the gate "renders when `manage_staff` and the GET answered 200", the card appears on one's own page and the spec fails.
- Evidence: `plugins/ihr_platform/ihr_identity/staff_access.py:117-119,149-150`.
- Fix: refuse self in `_target`, or the card hides when the target is the viewer.

**Refuted or checked and fine:**
- **Clickjacking:** blocked. `deploy/docker/frontend-nginx.conf.template:28,32` sets `X-Frame-Options SAMEORIGIN` and `frame-ancestors 'self'`.
- **Caching:** every `/api/` answer and the export get `no-store` (`ihr_identity/middleware.py:25-27`, `ihr_audit/export.py:185`).
- **CSRF on POSTs:** covered by the header, `Sec-Fetch-Site` and Origin checks (`authentication.py:69-76`).
- **Invitation and reset links:** `redirect_uri` is set on the server from the hospital's own client (`invitations.py:305,448`). There is no open redirect.
- **Audit entries for admin actions:** every POST writes a request entry with path and status (`ihr_audit/middleware.py:70`). Pack decisions change hospital tables, which have audit triggers.
- **Audit and pack routes:** the backend enforces `IsHospitalAdmin`. The UI gate only hides what the backend would refuse.
- **Old/new values and patient names:** shown only as the backend already masks and limits them.
- **"Set password now":** the backend already refuses it (`ihr_identity/invitations.py:40`).

graphify calls: 10.

tier-fit: OK: an access attack that needed checks across CARE, identity, Keycloak and audit; right for Opus high.

## Decision (orchestrator, 2026-11-21)

P1-T24 is split into three subtasks, built in order after P1-T22c2a. Each builder reads the design,
then this decision. This decision wins where the two differ.

**P1-T24a: the hospital-admin flag and the audit trail page.** Sonnet high builds it; an Opus high
review checks the access parts.
- G1: my-access gains `hospital_admin`. It is false for a superuser, as `restricted` is (readiness
  M2). The design's `manage_staff` flag is dropped (zoom-out F2): the staff card relies on the
  access GET instead.
- The web side covers `Utils/ihr/access.ts` (both interfaces, the `none` object, `ihrAccess()`),
  `useIhrAccess.ts` and `access.test.ts` (readiness M1). It reuses the inline gate pattern of
  `IhrOwnerTotals.tsx` instead of a new gate component (zoom-out F3).
- The design must state the query count and add a test for it (goldfish design F5).
- The audit page covers the list, the status strip and the entry sheet. The typed "Who" filter is
  dropped (all three lenses: the actor is `user:<pk>:<username>`). An entry's actor links to an
  exact-actor filter.
- The export and verifier links move to T24c (zoom-out F4).
- A2: the audit routes refuse a request whose `Sec-Fetch-Site` is present and is neither
  `same-origin` nor `none`, with a backend test.
- **Disputed (goldfish design F1):** do our API calls authenticate with an HttpOnly cookie
  (CLAUDE.md "Staff sign-in", P1-T4a) or an Authorization header (`Utils/request/utils.ts:69-78`)?
  The builder checks in code first and writes the answer into the design. Downloads in T24c
  follow it.
- G4: a seeded hospital-wide admin login, `admin.priya` on alpha (Q-K117), with the seed step that
  makes her hospital-wide named.

**P1-T24b: the staff "Sign-in" card.** Sonnet high builds the web side; an Opus high builder does
the backend boundary fix (A1); an Opus high review follows.
- **A1 (high):** `reset-sign-in` is allowed only for a hospital-wide admin (`hospital_wide_admin`).
  A target who is a hospital-wide admin at any hospital is protected. Resend and cancel keep the
  `can_create_user` rule. Role-matrix and backend tests prove both.
- G2: the access GET returns `offers` per state. Its table covers active, awaiting setup, queued,
  sent, failed, cancelled and expired (readiness M4). It returns empty `offers` for oneself, and
  the card hides when `offers` is empty (H2, A5, F3).
- A4: the card shows fixed phrases mapped from a status code; raw worker text stays in the logs.
- The UserForm change lists every edit (default `email`, the option removed, the enum kept;
  readiness M5, F8).
- G3, the hospital-wide list of open invitations, is not built (Q-K112 A).
- Confirming a reset uses a plain confirm with the warning text. The type-the-username variant is
  dropped (zoom-out F8, Q-K113 changed to B).

**P1-T24c: the pack updates screen, audit export links, polish.** Sonnet high builds it; an Opus
high review follows.
- G6: the pack API summary gains the facility `name` (zoom-out F5), and the drift doc rows 1–2 are
  fixed.
- A status-to-message table covers every POST answer (readiness M3).
- "Keep ours" shows only on pending items (goldfish design F4).
- G5: the dev seed gets a waiting pack item.
- The export and verifier downloads follow the T24a auth answer.
- Polish: the Booking card renders only when `booked_on` or `token_slot` is set (readiness L1), and
  the totals "By day" select fits at phone width.
- Hindi strings arrive with each subtask (zoom-out F7), not as a separate batch.

**Decisions:** Q-K110…Q-K117 as in the design, recorded as auto-accepted, except Q-K113, which
takes B (a plain confirm). The T24a builder writes them into QUESTIONS.md.
