# IndieHR — Progress Tracker

**Legend:**
- ✅ done (tested + you approved)
- 🟡 done, awaiting your review
- 🔵 in progress
- ⬜ not started
- ⏸ deferred
- ❌ dropped

Detailed task definitions: [PLAN.md](PLAN.md) §6. Open questions: [QUESTIONS.md](QUESTIONS.md).

**Current phase:** P0 Foundations
**Next task:** P1-T5e2 (account recovery: lost authenticator reset by a hospital admin, forgotten password, resend/cancel invitations). Still open: P1-T4b bundle + click-through (**blocked: GitHub billing**; CI has not run since P1-T4a). P1-T1b to P1-T5e1 await your review — P1-T2b, P1-T3, P1-T5a, P1-T5b, P1-T5c and P1-T5e1 have click-throughs for you. Owner's deep-dive click-through still to come (docs/CLICK_THROUGH.md). Review: docs/reviews/phase-0-review.md
**Recommended model for next task:** Opus · **medium** for P1-T5e2 (P1-T4b when billing is fixed: Opus · medium)

---

## Completed log

| Date | ID | What | Evidence |
|---|---|---|---|
| 2026-11-02 | R-0 | Read the full blueprint (441 lines); copied it to `docs/blueprint/` | — |
| 2026-11-02 | R-1 | Deep research, 8 parallel areas: clinical core, ERP/billing/claims, PACS/LIS, integration/identity/infra, India regulation, AI layer, productivity/licensing, market | `research_notes/India hospital SaaS stack research/*.md` |
| 2026-11-02 | R-2 | Consolidated research report | `reports/India hospital SaaS stack research.md` |
| 2026-11-02 | R-3 | Master plan v0.1, question list, tracker, CLAUDE.md | `docs/` |
| 2026-11-02 | R-4 | Workstation audit: 6.7 GB RAM, no GPU, Docker 26.1.3 without group access | PLAN.md §4 |
| 2026-11-06 | P1-T2b | **Hospital branding in the web app; dev stack with two demo hospitals.**<br>• The web app loads `/api/ihr/bootstrap.js` from its own origin before the bundle. Login page, logos and tab titles show the hospital's name, colour and logo; the OHC logo/links are replaced by "Powered by IndieHR" (5 small logged frontend patches).<br>• Our nginx image passes `/api/` to `API_UPSTREAM` with the browser's host, so one bundle serves every hospital; off by default (404).<br>• Hospitals without a logo get an initials mark in their colour (SVG we generate, escaped, CSP `default-src 'none'`).<br>• Emails use our templates and name the hospital (subject hook, logged); From "IndieHR", IST times, no CARE/OHC. SMS OTP texts and API docs title no longer say "Care".<br>• Dev stack in tenant mode (database `ihr_dev`): `seed` creates Alpha Multispeciality Hospital and Beta Children's Hospital (synthetic) with CARE's demo data and a different password per hospital (`ihr_seed_demo`).<br>**Checked in the browser:** each address shows its own branding; Alpha's password is refused at Beta; each hospital lists only its own 10 encounters; Beta's API answers 404 for Alpha's facility; Alpha's reset email links to alpha.localhost.<br>**Tests:** 20/20 hospital-profile tests locally (3 new: initials logo, emails, title). CI's first run caught one CARE test expecting "for Care" in the subject; that assertion was loosened (logged).<br>**CI (run 42221806339):** all green — 2,047/2,047 tests in tenant mode; 833 Redis keys, all tenant-prefixed; 11/11 mutations killed; frontend typecheck, lint, unit tests, build and image smoke test pass | Runs 42218861591, 42221806339 |
| 2026-11-07 | P1-T5e1 | **Staff invitations through CARE's own "Add user"** (owner: split T5e into invitations and account recovery; invite from CARE's Add user; an existing account joins by link + sign-in + confirm).<br>• "Add user" (`POST /api/v1/users/`, same roles and permission checks) now creates the CARE user **inactive with no usable password**, sends none of CARE's own e-mails (they pointed at switched-off pages) and queues a `StaffInvitation`. A password from the admin is refused with a clear message (CARE's form already offers "send an e-mail"; hiding "set password now" is a web-app patch for when CI runs). CARE's bulk upsert and format-suffix routes are blocked. The answer is identical whether or not the address has an account.<br>• The provisioner delivers: **new address** → account with no password, membership, Keycloak's setup e-mail (P1-T5c); **existing account** → membership and our "Join <hospital> on IndieHR" e-mail with a one-time link (hash stored, 72 h). Every refusal is checked before anything is granted; retries (3) for passing errors.<br>• **Join:** the link → normal sign-in with password + code → "Join <hospital>?" page → Join. Only then is the CARE user active; no session before. A sign-in without the link, someone else's link, an expired or used link, a cross-site or forged click: all refused. An account set up after this hospital made it also needs the join.<br>• Another hospital's account that was never set up makes the invitation **wait** (up to 72 h) rather than binding to it, so one hospital cannot block an address for others. Names in e-mails are plain text; at most 50 invitations per hospital per day.<br>• Independent review (3 lenses incl. attacking the design): 10 confirmed findings, all fixed (bulk upsert bypass; squatting another hospital's address; a set-up account skipping the join; membership granted before refusals; names injected into the e-mail; no cap; a busy operation starving invitations; a test that proved nothing). 2 rejected. Full-suite run also caught CARE's demo seeder using passwords (it keeps CARE's behaviour, in-process only) and the new table missing from the reviewed list of platform tables.<br>**Checked on the dev stack:** Theta's admin added Nina (new) and Meera (existing `dr.meera`), same answer for both; a password was refused. Nina set up from Keycloak's e-mail and signed in at Theta. Meera's join e-mail → sign-in → "Join Theta Test Hospital?" (screenshot checked; page made readable in dark browsers) → Join → signed in by single sign-on as her new Theta user; the used link is refused.<br>**Tests:** 185 plugin tests in tenant mode incl. the isolation suite, all passing (152 run, 33 live ones skipped there; new: 17 invitation/join tests — Add user, no password, no other route, plain names, daily cap, new vs join vs waiting delivery, refusals before grants, retries, link + sign-in + click, every refusal path); 33/33 live Keycloak tests. **CI not run** (GitHub billing) | TENANCY_DESIGN §4.5 |
| 2026-11-07 | P1-T5d | **Instant sign-out at every hospital** (Keycloak back-channel logout, OIDC Back-Channel Logout 1.0).<br>• Hospital clients now send sign-outs server to server (front-channel off) to one internal platform address, `IHR_BACKCHANNEL_LOGOUT_URL` → `/api/ihr/backchannel-logout/` (dev `http://backend:9000/…`; "backend" added as a platform domain). Drift-checked; required outside development (`ihr_identity.E006`).<br>• The endpoint accepts only a logout token signed by the realm (header `logout+jwt`, exactly one hospital client as audience, back-channel event, a `sid`, no nonce, at most 5 min old; shape captured from real Keycloak 26.8) and marks that sign-in as ended in that hospital's cache only. Sessions of that sign-in are refused and deleted from their next request; other sign-ins (another device) are untouched. The one-minute refresh stays as the fallback.<br>• Covers every way Keycloak ends a sign-in: sign-out or idle time-out at any hospital, an admin ending it.<br>• Independent review: 1 confirmed finding, fixed — a sign-out waits on Keycloak while Keycloak calls back into the same backend, so CARE's single-threaded gunicorn could block; the backend image now runs threaded workers (`GUNICORN_CMD_ARGS`, checked with gunicorn 23.0.0). 5 rejected. Production follow-up (own internal pool, not reachable through the public proxy) added to P1-T8.<br>**Measured on the dev stack:** signed in as `dr.meera` at Alpha and Beta (single sign-on), signed out at Alpha: Beta refused the session **0.10 s** later (before: up to 60 s). The drift check flagged all 7 dev hospital clients before the sync, clean after.<br>**Tests:** 169/169 plugin tests in tenant mode incl. isolation (new: a signed token ends that sign-in at once and nothing else; 18 kinds of bad token refused; marker per hospital; the token check demands this hospital's client; only on the platform host); 33/33 live Keycloak tests (new: back-channel settings and drift). Two new mutation rows (`backchannel-sid`, `backchannel-aud`) checked by hand: each makes its test fail. **CI not run** (GitHub billing) | ADR-009 |
| 2026-11-07 | P1-T5c | **First admin's one-time setup e-mail** (owner: new accounts only in this task; an existing account's "join this hospital" confirmation comes with P1-T5e).<br>• New step `admin_account` (2nd): the provisioner creates the first admin's Keycloak account with **no password or authenticator** (nobody can sign in with it), username = e-mail, stamped `ihr_invited_for=<hospital uuid>` (admin-only user-profile attribute; refused if Keycloak drops it). An e-mail that already has any account stops the run here, before anything is built.<br>• New step `invite` (last, once live): member role + organisation, CARE user linked with `StaffIdentity.awaiting_setup`, and Keycloak e-mails a link (verify e-mail → set up authenticator → choose password; works once, 72 h). Nobody but the person ever knows the password.<br>• The CARE user stays **inactive** until their first sign-in at that hospital with password + code and a verified address; deactivating them before that cancels the setup.<br>• Console: setup state per hospital ("sent on …, waiting" / "has set up") and **Send the setup e-mail again** (also `ihr_provision resend-setup`). Keycloak cannot cancel a sent link (checked live: signing the account out does not), so sending again **replaces the unused account**: every earlier link stops working.<br>• Realm mail server from `IHR_KEYCLOAK_SMTP` (TLS required outside development; password only from `IHR_KEYCLOAK_SMTP_PASSWORD` in the sync process), drift-checked. Dev and CI catch mail with **Mailpit v1.33.0** (inbox http://127.0.0.1:8025).<br>• Independent review: 8 confirmed findings, all fixed (old links alive after a resend; deactivation did not cancel a pending setup; booleans sent as "True"; unstamped account possible; roll-back missed an account whose id was not yet saved; a resumed run failed if the link was already used; resend failures invisible in the console; SMTP password in every process's settings). 1 rejected.<br>**Checked in the browser:** created **theta** from the console (live in **69 s**); the e-mail arrived in Mailpit from "IndieHR"; the link led through authenticator and password set-up to "Account updated" and back to Theta's sign-in; first sign-in (password + code) activated the account, the console showed "has set up", and the same person is refused at Alpha.<br>**Tests:** 32/32 live Keycloak tests (new: full set-up from the e-mail, link works once, e-mail verified, a resend kills the old link, existing accounts never adopted or removed, unstamped accounts never left, mail server + stamp drift-checked); **163/163 plugin tests** in tenant mode incl. the isolation suite (new: setup e-mail sent once and linked; resend replaces the account; resumed run after a used link; roll-back finds an account whose id was not saved; an existing address stops at step 2; activation only with a verified address, never for a deactivated person, and not cancelled by CARE's role-cache save; mail-server TLS and password rules). Also fixed: the P1-T5b drift-check test reached the operator realm over the network (it had not been run in P1-T5b). **CI not run** (GitHub billing) | TENANCY_DESIGN §4.5 |
| 2026-11-07 | P1-T5b | **Operator console: new hospitals from a web form** (owner: server pages, separate operator realm).<br>• Realm `indiehr-ops` built with the hospitals' baseline (password policy, lock-out, password + code; minimum ACR on the console client); role `ihr-operator` mapped only to the console client; plus our own operator list (`OperatorAccount`, `ihr_operator add/list/remove`). Hospital tokens, hospital cookies and hospital addresses never reach the console.<br>• Console at `/ops/` on the platform host (dev http://console.localhost:9000): hospitals and runs, "New hospital" form, a progress page that refreshes itself, Retry and Roll back (also for a run a stopped provisioner left behind). Backend-held session cookie, Django CSRF on every form, strict CSP, no caching; its own automatic refresh does not count as activity (30 min idle, 12 h max).<br>• A separate `provisioner` process (`ihr_provision_worker`) runs what the console queues; it alone holds the Keycloak admin credential (`deploy/dev/provisioner.env`), with read-only code. The web app and the worker never have it.<br>• `ihr_keycloak_sync --realm` creates the operator realm and console client; `--check` drift-checks it too. Demo operator `op.owner` from `keycloak seed`.<br>• Browser check found a real bug unit tests could not: `Referrer-Policy: no-referrer` made browsers send `Origin: null`, so every console form failed CSRF; fixed (same-origin) and pinned by a test. Independent review: 9 confirmed findings, all fixed (auto-refresh kept sessions alive; refresh race; Keycloak outage signed operators out; provisioner code writable by other containers; stalled runs had no Retry; refused runs dropped; operator realm not drift-checked; a mutation target duplicated; `keycloak stop` left the provisioner).<br>**Checked in the browser:** `op.owner` signs in (password + code), sees all hospitals; the form created **zeta**, live in **69 s** with the page following each step; sign-out ends the operator sign-in. A queued **eta** was run by the read-only provisioner.<br>**Tests:** 15 console tests; 27/27 live Keycloak tests (new: operator realm, console client, operator vs non-operator tokens, drift detected and repaired); identity and tenancy suites green. **CI not run** (GitHub billing) | TENANCY_DESIGN §4.9 |
| 2026-11-07 | P1-T5a | **Provisioning engine: a new hospital live from one command** (owner, 2026-11-07: P1-T5 split into six; starter data = the minimum; console = server pages; onboarding = one-time setup e-mail).<br>• `manage.py ihr_provision start/status/resume/rollback` (dev: `care-dev.sh provision …`, `provision-demo <slug>`). Steps: reserve (hospital row + empty schema, one transaction) → migrations → address → CARE roles → code lists → profile → state + district (CARE's own geography shape) → **inactive** first admin (Administrator on the state, no password, no CARE e-mail) → facility (CARE adds its root organisation; admin = Facility Admin) → Keycloak organisation + client (off) → activate (client on, `activated_at`).<br>• Each step idempotent and recorded in `TenantOperation` (ids saved in the same transaction as the rows); one run per hospital through a Postgres lock held on its own connection; failed runs resume where they stopped.<br>• Roll-back only for a hospital that never went live: removes its Keycloak objects (stamped with the hospital's uuid, never another's), its schema and address, releases the slug (owner's choice), drops the first admin's personal details from the record.<br>• Keycloak organisations and clients now carry `ihr.hospital_uuid`; a leftover under the same slug is never adopted. `ihr_keycloak_sync` leaves hospitals in provisioning alone. `provision-demo` gives the first admin a dev login until P1-T5c's setup e-mail.<br>• Design attacked before building (security + feasibility); independent review after: 14 confirmed findings, all fixed (the lock was silently released by the migrations; ids not saved with their rows; a reserved hospital without schema; Keycloak leftovers on roll-back; personal data kept; test-user file sections).<br>**Measured:** gamma and epsilon live in **67 s** each (65 s of it migrations; target under 5 min). Gamma's contents match Alpha's (30 code lists, 10 roles, facility with root organisation). Its admin signs in at Gamma with password + code and is refused at Alpha. Delta stopped after geography, then rolled back: address 404, slug free again.<br>**Tests:** 6 provisioning tests (three injected failures then success, re-run changes nothing, roll-back incl. refusing a live or mismatched hospital, address release only for never-live, input checks, one open run per address, lock still held after migrations); 133 plugin tests; isolation suite 22/22; 26/26 live Keycloak tests (new: leftovers of another hospital never adopted or removed). **CI not run** (GitHub billing) | TENANCY_DESIGN §7.1 |
| 2026-11-07 | P1-T4b | **Web app on the backend-held Keycloak sign-in** (ADR-009, "The web app").<br>• "Sign in with your hospital account" replaces CARE's password form (reCAPTCHA and "forgot password" gone); `/2fa` and the password-reset pages redirect to `/login`; the user page no longer offers password reset or CARE two-factor. Patient OTP unchanged.<br>• No staff token in the browser; our API requests carry `X-IHR-CSRF` and `X-IHR-Idle` (never sent to other hosts).<br>• Idle: shared last-input time across the hospital's tabs; warning 2 min before; sign-out everywhere at 30 min; a heartbeat keeps single sign-on alive while someone types.<br>• Sign-out (button, idle, another tab, server-side end) clears every browser cache and reloads (Q-T9); an unconfirmed sign-out keeps the app signed out and retries.<br>• Backend: `bootstrap.js` sends `window.__IHR_AUTH__` (idle settings); `IHR_SESSION_IDLE_SECONDS` can be shortened in dev for the click-through.<br>• 6 CARE frontend files patched + 5 new files, all logged. Independent review: 12 confirmed findings, all fixed (own sign-out broadcast overwrote the reason; idle could read 0 after a server-side end; failed sign-out re-signed the person in; incomplete cache clearing; lost deep links; redirect normalisation).<br>**Checked locally:** TypeScript type check clean (apart from the CI-generated `supportedBrowsers` file), ESLint no errors on the changed files, Prettier clean, 7 unit tests of the idle/redirect rules, 20 backend profile tests (bootstrap script).<br>**Not done:** the bundle build and your click-through. GitHub Actions is blocked by a billing problem, and the frontend is never built on this machine | ADR-009 |
| 2026-11-07 | P1-T4a | **Backend sign-in through Keycloak; tokens held by the backend** (ADR-009; owner's answers: tokens on the server, sign-out and 30-min idle end sign-in at every hospital, Keycloak only for staff, task split in two).<br>• New tenant app `ihr_identity`: `/api/ihr/auth/login/`, `callback/`, `logout/`, `session/`; Django runs the Keycloak sign-in (code + PKCE) and the browser only gets an HttpOnly cookie (R0-20 closed).<br>• Every request re-checks the stored token for *this* hospital (signature, issuer, client, exact audience, `member`, organisation, `acr=mfa`, hospital active), the person's link to an active CARE user (`StaffIdentity`, by Keycloak id, never e-mail), CSRF header + origin, idle and 12 h limits, and "signed out since". Superusers and machine accounts cannot sign in as staff.<br>• Sessions encrypted in Redis with a cookie-derived key; login state in a sealed cookie; API answers `no-store`. Tokens refresh every minute (one request at a time), so a sign-out elsewhere ends a session within ~60 s.<br>• CARE's staff login, JWT, MFA, password reset, password change, Django admin and machine-token creation return 404 on hospital addresses (also inside batch requests and with `.json` suffixes). Patient OTP unchanged; DRF tokens only for machine accounts. 5 CARE test modules (61 tests) of those features left out of the tenant run, with reasons.<br>• One logged CARE patch: the batch endpoint passes the session cookie and our headers to sub-requests. Keycloak redirect is now `<hospital>/api/ihr/auth/callback/`; demo staff are linked to CARE users (`dr.meera` → care-doctor at both, etc.); `ihr_link_staff` for operators.<br>• Design attacked before building (security + feasibility). Independent review after building: 18 confirmed findings, all fixed (refresh race could end valid sessions; idle "signed out since" rolled back by the request transaction; `.json` route bypass; a mutation row that tested nothing; JWKS outage handling; weak tests).<br>**Tests:** 79 identity/profile/sync unit tests; isolation suite 22/22 (A's session refused at B, a token for A's client or organisation refused at B, CARE's JWT refused everywhere); 25/25 live Keycloak tests (real tokens pass the backend's checks for their hospital only; back-channel logout ends single sign-on); 6 new mutation rows. Scripted sign-in through the dev stack: Dr Meera signs in at Alpha (password + code), Beta signs her in silently, Alpha's cookie is refused at Beta, sign-out at Beta ends Alpha within 60 s and the next sign-in asks for the password, a nurse of Alpha is refused at Beta.<br>**CI (run 42280399587):** green — backend, backend in tenant mode (full CARE suite plus `ihr_identity`), Keycloak live tests, frontend, Trivy, production image, 15 of 17 mutation runs. The other 2 (`oidc-azp`, `oidc-acr`) failed because the test runner could not run a selection of database-free tests. Fixed in `3f32341` and checked locally: both mutations now make their tests fail. **CI for `3f32341` did not start: GitHub reports a billing problem** ("recent account payments have failed or your spending limit needs to be increased"). Re-run once billing is fixed.<br>**Not yet:** the web app still shows CARE's password form, which no longer works (P1-T4b) | ADR-009, run 42280399587 |
| 2026-11-06 | P1-T3 | **Keycloak 26.8.2 staff sign-in: one organisation, login client and gate per hospital; MFA for all staff** (ADR-008; owner's answers: job roles stay in CARE, MFA for everyone, 12+ character passwords with a common-password block list, 30 min idle / 12 h sessions).<br>• Optional dev service (`care-dev.sh keycloak up`, ~540 MB, own database `keycloak`), realm `indiehr` built and checked by our own stdlib code (`ihr_keycloak_sync`, `--check` for drift); `Hospital.keycloak_org_id` (migration 0003).<br>• Each hospital: organisation, public PKCE client `ihr-h-<slug>`, role `member`, and a login flow that asks for password + 6-digit code and then refuses non-members, including through single sign-on.<br>• Hospital tokens carry only that hospital: `azp`/`aud`, `resource_access.ihr-h-<slug>=[member]`, `ihr_org` (organisation id), `acr=mfa`, 5-minute life. Requests for other organisations are refused.<br>• Demo staff (synthetic): `dr.meera` (both hospitals), `nurse.asha` (Alpha), `admin.bhavesh` (Beta), `new.neha` (Alpha, sets up her authenticator at first sign-in). Dev token viewer at http://alpha.localhost:8082/.<br>• Design reviewed before building (security + feasibility agents found 3 flaws: gates that fail open, a window before a new client is guarded, account console without MFA, all fixed in the design). Independent review after building: 16 confirmed findings, all fixed (rebuild-flows could not work; a half-built core flow would drop OTP unnoticed; drift check missed lifetime overrides, scope mappings, group/composite membership and stray clients; duplicate hospital names; admin-event expiry; one vacuous test; DB password on a command line).<br>**Checked in the browser:** Dr Meera signs in at Alpha with password + code, then Beta signs her in silently with a Beta-only token; sign-out ends both; a nurse of Alpha is refused at Beta.<br>**Tests:** 23/23 live Keycloak tests (throwaway realm, real login pages): claims, silent SSO, non-members refused (fresh, via SSO, `prompt=none`), password alone gets no code, first-login authenticator set-up, account console needs MFA, refresh rotation, removal ends refresh at once, other organisations refused, gates fail closed, drift detection and repair, flow rebuild, password policy and block list, lock-out after 5, identical errors, password grants and registration refused, no secrets in admin events. 5 new Django tests; `--check` clean on the dev realm. CI: new "Keycloak live tests" job.<br>**CI (run 42255466850):** all green — Keycloak live tests, backend, backend in tenant mode, 11/11 isolation mutations killed, frontend, Trivy, production image; CI workflow run 42255466908 green | ADR-008, run 42255466850 |
| 2026-11-06 | P1-T2a | **Backend for per-hospital routing and branding.**<br>• New tenant app `ihr_tenant_settings`: one profile per hospital with names, GSTIN (format + check character + state match), address with GST state and PIN, registration numbers (CEA, NABH, HFR, ROHINI), letterhead footer, colour and logo (PNG/JPEG/WebP sniffed from bytes, ≤256 KB, no SVG).<br>• Public, host-based `/api/ihr/bootstrap/`, `bootstrap.js` and `logo/`: branding only, `private` caching, script-safe output. Staff-only `/api/ihr/profile/`.<br>• Hospital addresses default to `<slug>.<suffix>`. Slugs and addresses can never be renamed, moved or reused (`RetiredAddress`).<br>• Password links point at the hospital's own address, and reset tokens carry a `tid` claim (logged CARE patch). This closes the P1-T1b follow-up.<br>• Bug fixed: a 404 on the platform host became a 500 (CARE's error page links to a CARE route).<br>**Tests:** 40/40 plugin tests (incl. 18 new) and the isolation suite green locally; new mutation `reset-token-tid` killed. Adversarial review: 27 agents, 10 distinct findings, all fixed.<br>**CI:** 2,045/2,045 tests in tenant mode (CARE + ours); 833 Redis keys, all tenant-prefixed; 11/11 mutations killed. The first CI run caught CARE's reset tests expecting their own error messages, so the hospital check now runs last | Run 42203433586 |
| 2026-11-06 | Q&A 5 | Cell cap (Q-T6) confirmed: **100 hospitals per database cell**, reviewed again on staging hardware (P1-T8) and with real data | QUESTIONS.md |
| 2026-11-06 | P1-T1c | **Scale and operations measured at 1/25/50/100/200 hospitals** (PostgreSQL 18.6, dev machine, demo-sized hospitals).<br>• Onboarding a hospital: 65–82 s.<br>• One-migration release: 58 s at 100 (32 s with 2 processes), 136 s at 200.<br>• Catalogue: 367 MB at 100, 723 MB at 200.<br>• Per-hospital dump: 1.4 s at 100, 1.7 s at 125, **11.8 s at 150** (a step; same instance).<br>• Restores exact at every size, both into an empty database and in place under a side name. Checked by a hash of rows, sequences, indexes and constraints; negative controls passed.<br>• Connection memory: about 0.8–5 MB per hospital served per connection, never freed → recycle connections.<br>• Defaults that fail at scale: Docker 64 MB `/dev/shm` (about 158 hospitals) and `max_locks_per_transaction` 64.<br>**Recommendation: keep the cap at 100** (your decision, Q-T6), with operating rules in TENANCY_DESIGN §6.11.<br>New: `scripts/tenancy-scale-bench.sh`, `ihr_bench_scale` command, `ihr_platform.seeding`; research brief and 2 review rounds (4 + 2 agents) | docs/reviews/p1-t1c/ |
| 2026-11-06 | P1-T1b | **Cross-tenant isolation suite: 22/22 green locally, all 10 mutation runs killed.**<br>• Two seeded tenants (CARE's full demo data each, canary words in facility/patient/staff names).<br>• Crawl of 132 GET routes as A, then B, then A again: 0 leaks, 0 server errors, ~76 routes return own data.<br>• 63 cross-id probes (B's host, A's ids): no 2xx, no leak.<br>• Anonymous access: 17 reviewed routes in `anonymous_allowlist.txt`, fail-closed.<br>• Also covered: staff and patient-OTP tokens (with positive controls), identifier caches, raw-Redis recent views, Celery tasks through real tenant hooks, beat per active tenant, every S3 key site incl. cover images, batch requests.<br>• Redis: 107 keys written, all tenant-prefixed; the runner now audits the whole run at the end.<br>**Leaks found and fixed (6 logged CARE patches):** identifier-config cache handed tenant B tenant A's config; identifier lookup cache; recent-views raw Redis keys; patient OTP token had no tenant claim (now `tid`); object keys had no tenant prefix (now `t/<uuid>/`).<br>**Production bugs fixed:** Celery beat would have run CARE's cleanup jobs in `public` (new `ActiveTenantScheduler`); CARE audit log pinned off.<br>**Mutation proof:** removing the cache key function makes B's crawl show A's ids and canary.<br>**Reviews:** 2 adversarial rounds (9 agents); 22 + 9 findings, all fixed or scheduled as follow-ups below.<br>New: `scripts/isolation-mutation.sh`; CI jobs "Isolation mutation" (when isolation files change, weekly, on demand).<br>**CI:** CARE suite + isolation suite in tenant mode, 2,027/2,027 pass; 832 Redis keys, all tenant-prefixed; 10/10 mutation jobs killed | Run 42174788127 |
| 2026-11-06 | P1-T1a | Tenancy works with CARE: `plugins/ihr_platform` (Hospital = legal entity + Domain in public; settings overlay puts every CARE app in the tenant schema; tenant cache keys; pg_trgm in public first; `ihr_create_tenant` = migrate + sync roles/value sets; TenantTestRunner). Upstream patches: Pipfile/lock (django-tenants 4.0.1, tenant-schemas-celery 5.0.0 only) + conditional tenant-aware Celery app. **CI: CARE's 2,005 tests + 8 tenancy tests pass inside a tenant schema (13 min, serial)**; normal-mode suite still green. Local: public holds only 4 allowed tables; tenant creation ~68 s; 100 tables / 1,077 relations per tenant; rows and cache values don't cross tenants. One bug of mine fixed on the way (runner re-pointed reconnects). New: `scripts/care-tenancy.sh`, ruff hook for plugins | Run 42099914452 |
| 2026-11-06 | Q&A 4 | Tenancy design approved; all Q-T0…Q-T11 defaults accepted: tenant = legal entity (GSTIN holder, may have several facilities), maintenance-window upgrades, per-tenant staff records, presigned URLs, Keycloak client per tenant, no in-tenant superuser, cell cap 100, break-glass support, no inter-hospital referral in v1, logout cache clear, auto pack updates for unedited rows, domain at P1-T8 | QUESTIONS.md |
| 2026-11-06 | P1-T0 | Tenancy design: 17-agent workflow (6 mappers over 100 CARE models + runtime/frontend/libraries; 3 independent designs; 3 judges; 5 adversarial reviewers — all *sound with fixes*). Decision: every CARE app per tenant schema, `public` = platform registry only, Keycloak = global identity, per-tenant User rows; django-tenants 4.0.1 + tenant-schemas-celery 5.0.0 (verified on PyPI); no template cloning in v1; maintenance-window upgrades; tenant checks in DRF layer. 5 fatal flaws found and fixed in the design. P1-T1 split into a–d with gates. 12 questions for you (Q-T0…Q-T11). Independent completeness check then found 24 of 48 review items missing/misstated + 7 contradictions → all fixed (design v2); new task P4-T8 | docs/TENANCY_DESIGN.md, docs/reviews/p1-t0/ |
| 2026-11-04 | P0-T13 | Latest versions: Django **6.1.2**, DRF 3.18.3, PyJWT 2.15.1, cryptography 50.0.2, Authlib 1.9.3, aiohttp 3.14.5, sqlparse 0.6.1, certifi 2026.10.23 + full relock of all transitive deps (81 changes; tzdata dropped, IST verified via system zoneinfo). CI: **2,005/2,005 tests pass**, prod image clean. Weekly outdated report (baseline: backend 45/158 behind, frontend 92/132 — mostly CARE upstream's own pins). ADR-007 (blueprint items deferred/dropped; backups/RPO/RTO gated before first pilot). ~15 stale doc statements fixed; 33 doc links OK | Run 41635315688 |
| 2026-11-04 | P0-T12+ | Owner approved: repo Actions restricted to GitHub-owned + 4 allow-listed third-party commits; full-SHA pinning required. First run proved it works (blocked trivy-action's internal setup-trivy); setup-trivy v0.2.6 verified and allowed | GitHub settings |
| 2026-11-04 | P0-T12 | CI hardening: all 7 actions pinned to commit SHAs (third-party commits verified on default branch, signed); gitleaks full-history scan in CI (5 upstream false positives reviewed; planted fake token → scan fails, verified); Trivy secret scan blocking; **production backend image** now built from `deploy/docker/backend.Dockerfile` (pinned python:3.13.18, pip removed — image scan had found 6 fixable vulns incl. 1 CRITICAL certifi inside pip's vendored libs), smoke-tested, non-root, `trivy image` clean; shellcheck hook; no cancel-in-progress on main; wider filters; sync script: full SHAs, --task required, conflict exit 2 + runbook (both paths rehearsed). CI green | Runs 41622517186, 41625426568 |
| 2026-11-03 | Owner check | Quick look at the running app: "seems ok"; deep dive later. P0-T5 and P0-T11 approved | — |
| 2026-11-03 | P0-T11 | `care-dev.sh seed` / `reset-passwords`: adds care-pharmacist + care-billing, rotates all 12 passwords, writes TEST_USERS.md (verified: 4 roles log in + open facility; old password 401). `up` rebuilds the image when the lock file changes. GAP §8 verified: token queue FIFO (no priority); age/sex lab ranges automatic; **invoice numbering not GST-safe** (count()+1, no unique, calendar year) → P2-T6; FEFO UI-only, API accepts expired stock → P2-T12; discharge template fixture-only → P2-T9; dates DD/MM/YYYY. Billing 400 = expected 'no account yet'. **Postgres 18.6: all 2,005 backend tests pass.** Click-through written, all links checked | GAP_ANALYSIS §8, run 41614579309 |
| 2026-11-03 | P0-T10 | Egress lockdown: 5 CARE frontend patches (OHC Sentry init + default DSN, SBOM default, OHC CDN badge, CDN image-compression worker, CDN QR engine); `deploy/frontend.env`; `scripts/check-bundle-egress.sh` + reviewed allowlist (fails on OHC Sentry/CDN/API/SBOM and any unreviewed host); CSP **enforced** via nginx template (API/storage origins at runtime). Verified: old bundle fails the check (3 denied hosts), new passes (45 hosts reviewed), OHC Sentry key absent; browser test with fresh tab — 0 CSP violations across encounters, patient encounter, plots, medicines. CI green. Known gaps: facility map blocked; reCAPTCHA off | Runs 41611564311, 41612150150 |
| 2026-11-03 | Q&A 3 | Review questions answered: minimal logged CARE model edits OK; customer discovery before Phase 2 (P1-T14); no outage kit (accepted risk); stop other services on dev PC | QUESTIONS.md |
| 2026-11-03 | Review | Phase 0 independent Opus review: grade B−, 21 findings (2 High: OHC Sentry hard-coded in frontend; CARE `User` model blocks schema-per-tenant as designed). Verified the High items myself. Found and fixed: P0-T5 commit had silently failed (check-yaml rejected Compose `!override`); now committed as 8eeaea81e. New tasks P0-T10..T13, P1-T0 | docs/reviews/phase-0-review.md |
| 2026-11-03 | P0-T5 | CARE running locally on 6.7 GB: backend stack ~550 MB (one Celery process, memory caps), all ports bound to 127.0.0.1, terminology calls blocked, fixture passwords replaced; web bundle built on GitHub (new `Frontend bundle` workflow, OHC URLs removed) and served by our non-root nginx image. Verified: API login 200 (old public password 401), browser login as doctor, facility + patient search pages, **all browser requests to localhost only** | docs/LOCAL_DEV.md, scripts/care-dev.sh |
| 2026-11-03 | P1-T12 | Fixed all 29 fixable HIGH vulns (Django 6.0.8, DRF 3.18.2, Authlib 1.9.1, urllib3 2.8.0, aiohttp 3.14.4, cryptography 50.0.0, sqlparse 0.6.0; vite 8.3.5 + 4 npm pkgs). DRF 3.18 broke CARE (CoreAPI removed) → patched 2 filter files. New non-root frontend image `deploy/docker/frontend.Dockerfile` (nginx-unprivileged 1.30.6, uid 101). Trivy gate raised to HIGH + misconfig with scoped exceptions. CI green: backend 11 min, frontend + image smoke 5 min | Run 41583635960 |
| 2026-11-03 | P0-T9 | Security bumps: PyJWT 2.14.0, Pillow 13.0.0, Authlib 1.9.0, Brotli 1.2.1, certifi 2026.10.18, DOMPurify 3.4.17. Local Trivy 0.76.1: 0 CRITICAL. CI all green: backend full suite 11 min, frontend 4 min, Trivy pass. Remaining: 29 fixable HIGH + root Docker images → P1-T12 | Run 41580332662, docs/UPSTREAM_PATCHES.md |
| 2026-11-03 | P0-T8 | Apps CI: backend full upstream test suite passes (12 min); frontend typecheck/lint/unit/build pass (4 min); Trivy gate fails on 10 fixable CRITICAL vulns in upstream locks: PyJWT 2.12.0 (auth bypass), Pillow 12.2.0, Authlib 1.6.12, Brotli 1.2.0, certifi, DOMPurify 3.4.12 | Run 41571634449 |
| 2026-11-03 | Q&A 2 | Remaining questions answered: curated terminology now, SNOMED licence prep, OT/ICU/Maternity first, payroll + e-invoice later via ERPNext, phase-end second-opinion reviews, product owner reviews clinical content | QUESTIONS.md |
| 2026-11-03 | P0-T7 | Gap analysis from CARE code: 28 MVP areas (7 use, 15 extend, 6 build) + 17 later modules; tenancy-readiness check (no raw SQL); 6 upstream risks; plan rescoped (+P1-T10 terminology, +P1-T11 roles) | docs/GAP_ANALYSIS.md |
| 2026-11-03 | P0-T4 | CARE backend @67a46185eb98 and frontend @57205b897804 imported as squashed subtrees (ADR-006); `upstream.lock`, `scripts/sync-upstream.sh` (dry-run + real sync rehearsed in a scratch repo: conflict-risk warning, 3-way merge, lock update all OK) | apps/, docs/UPSTREAM.md |
| 2026-11-03 | P0-T3 | git repo on `main`, private GitHub repo, monorepo layout, proprietary LICENSE + NOTICE, pre-commit (gitleaks, actionlint), CI workflow | github.com/DhruvGutpa12345/indiehr |
| 2026-11-03 | P0-T2 | Docker 29.9.2, Compose 5.7.0, gh 2.103.3, uv 0.13.6, pnpm 12.16.1, pre-commit 4.7.0 installed; hello-world OK | docs/VERSIONS.md, deploy/dev/setup-workstation-root.sh |
| 2026-11-03 | P0-T1 | ADR-000…005 written; PLAN.md → v1.0 (P0-T6 dropped, P2-T17 split, NHCX/PM-JAY moved up, AI deferred) | `docs/adr/`, PLAN.md |
| 2026-11-03 | Q&A | Blocking questions answered: CARE core, schema-per-tenant, 10–100 bed segment, RAM upgrade, Tally + ERPNext, PM-JAY early, no AI for now, commit to main (see QUESTIONS.md) | QUESTIONS.md |
| 2026-11-02 | Q-E2 (part) | User `ubuntu` added to `docker` group; Docker reachable without sudo (verified via `sg docker`). Upgrade to Docker 29.x still pending (P0-T2) | `getent group docker` |

## Phase status

| Phase | Name | Status | Tasks done |
|---|---|---|---|
| — | Research and planning | 🟡 | 5/5 |
| P0 | Foundations and decisions | 🟡 | 12/12 — all review follow-ups done; awaiting your review |
| P1 | Platform core (tenancy, identity, staging) | 🔵 | 3/17 |
| P2 | MVP hospital workflows | ⬜ | 0/20 |
| P3 | ABDM and national rails | ⬜ | 0/12 |
| P4 | Compliance and security | ⬜ | 0/8 |
| P5 | Diagnostics depth (PACS, LIS, QC) | ⬜ | 0/8 |
| P6 | Specialty and enterprise modules | ⬜ | 0/~30 |
| P7 | AI layer | ⏸ deferred (ADR-005) | — |
| P8 | Patient engagement and comms | ⬜ | 0/4 |
| P9 | Commercial SaaS readiness | ⬜ | 0/7 |

## Task board

### P0 — Foundations

- 🟡 P0-T1 Record decisions → ADR-000..005, plan v1.0
- 🟡 P0-T2 Workstation setup (docker group, Docker 29 / Compose v5, uv, pnpm, gh)
- 🟡 P0-T3 Repo bootstrap (git, GitHub, monorepo layout, licence/NOTICE)
- 🟡 P0-T4 Upstream fork strategy (CARE backend + frontend, pinned SHAs)
- ✅ P0-T5 Run CARE locally with test users (done on 6.7 GB: backend ~550 MB, web app bundle built on GitHub)
- ❌ P0-T6 Spike: Marley + ERPNext v16 (dropped — Q-A1 decided)
- 🟡 P0-T7 Gap analysis blueprint ↔ CARE
- 🟡 P0-T8 CI pipeline: backend ✅, frontend ✅, Trivy ✅ (after P0-T9)
- 🟡 P0-T10 Egress lockdown: OHC Sentry/CDN/reCAPTCHA/OSM off, bundle deny-list in CI, enforced CSP (review High)
- ✅ P0-T11 Close P0-T5: scripted password rotation, billing + pharmacist users, GAP §8 checks, Postgres 18 check, billing 400 explained (owner: quick look OK 2026-11-03; deep dive later)
- 🟡 P0-T12 CI hardening: SHA-pinned actions, real secret scanning, production image scan, shellcheck
- 🟡 P0-T13 Docs + versions sync, ADR-007 deferred blueprint items
- 🟡 P0-T9 Security bumps of upstream dependencies (PyJWT, Pillow, Authlib, Brotli, certifi, DOMPurify) → Trivy green ✅

### P1 — Platform core

- ✅ P1-T0 Tenancy design → docs/TENANCY_DESIGN.md + ADR-002 Amendment 1 (accepted 2026-11-06; defaults for Q-T0…Q-T11)
- 🟡 P1-T1a Tenancy library spike; CARE suite in tenant mode — **2,013/2,013 tests pass inside a tenant schema**
- 🟡 P1-T1b Isolation suite (+ mutation runs) — 22/22 green, 10/10 mutations killed; CI 2,027/2,027 (run 42174788127)
  - Follow-ups found by P1-T1b:
    - ✅ Staff password set-up/reset links use one global `CURRENT_DOMAIN`, and the reset token has no tenant claim → fixed in P1-T2a
    - ⬜ Anonymous routes to harden: `/health/*` reachable only from monitoring (P1-T8); `govt/organization` limited to government geography; `schedulable_users` exposes staff username, last login and roles (trim, require patient OTP); PlugConfig `meta` must never hold secrets
    - ⬜ Seed the 34 models CARE's fixtures leave empty, so their detail routes are crawled too (`crawl_skips.txt`)
    - ⬜ Remaining design patches not yet built: presigned cover/avatar URLs, batch sub-request path allow-list, separate patient/report bucket credentials (TENANCY_DESIGN §10)
- 🟡 P1-T1c Scale & ops numbers — measured; **cell cap 100 confirmed** (Q-T6, 2026-11-06)
  - Follow-ups: re-measure on staging hardware (P1-T8) and with real data volumes (P4-T7 / pilot); apply the §6.11 operating rules in P1-T8
- ⬜ P1-T1d Playwright two-subdomain isolation (after P1-T4)
- 🟡 P1-T2a Hospital profile, bootstrap endpoint, subdomain domains (backend) — CI 2,045/2,045 (run 42203433586)
- 🟡 P1-T2b Runtime branding in the web app; dev stack with two demo hospitals — click-through waiting for you
  - Follow-ups: buttons and links still use CARE's green (a full theme from the brand colour needs the theme tokens, P1-T7 or later); printed QR codes and queue token cards still show CARE's mark (letterhead work); the production backend image does not yet include `plugins/` (P1-T8)
- 🟡 P1-T4a Backend sign-in through Keycloak, tokens held by the backend (ADR-009) — review waiting for you; the web app follows in P1-T4b
- 🟡 P1-T4b Web app on the new sign-in — code done and checked locally; bundle build + your click-through wait for GitHub billing
- 🟡 P1-T3 Keycloak 26.8.2: one organisation, login client and gate per hospital; MFA for all staff (ADR-008) — click-through waiting for you
- ⬜ P1-T4 CARE ↔ OIDC
- 🟡 P1-T5a Provisioning engine (command line): new hospital live in ~67 s, resumable, roll-back — click-through waiting for you
- 🟡 P1-T5b Operator realm + console — click-through waiting for you
- 🟡 P1-T5c First-admin setup e-mail (Mailpit in dev) — click-through waiting for you
- 🟡 P1-T5d Instant sign-out at every hospital (back-channel logout) — review waiting for you
- 🟡 P1-T5e1 Staff invitations (Add user → setup e-mail or join confirmation) — click-through waiting for you
- ⬜ P1-T5e2 account recovery · ⬜ P1-T5f narrow Keycloak credentials
- ⬜ P1-T6 Tamper-evident audit trail
- ⬜ P1-T7 i18n + Indian locale
- ⬜ P1-T8 Staging server + one-click URLs
- ⬜ P1-T9 Observability
- ⬜ P1-T10 Terminology service (SNOMED/ICD-10, no external calls)
- ⬜ P1-T11 Indian hospital roles
- ⬜ P1-T14 Customer discovery: 5–10 hospital interviews, pilot LOI (before Phase 2)
- 🟡 P1-T12 Hardening before staging: fix 29 fixable HIGH dependency vulns (22 backend, 7 frontend) + non-root Docker images

### P2 — MVP workflows

- ⬜ P2-T1 Masters and rate cards
- ⬜ P2-T2 Patient registration (UHID, MLC, masked Aadhaar)
- ⬜ P2-T3 Appointments + OPD token queue
- ⬜ P2-T4 OPD consultation EMR
- ⬜ P2-T5 e-Prescription
- ⬜ P2-T6 OPD billing
- ⬜ P2-T7 IPD admission/transfer/discharge + bed board
- ⬜ P2-T8 IPD clinical (orders, nursing, medication record)
- ⬜ P2-T9 Discharge summary
- ⬜ P2-T10 IPD billing, packages, TPA split
- ⬜ P2-T11 Pharmacy stores (purchase order, goods receipt, FEFO)
- ⬜ P2-T12 Pharmacy dispensing + statutory registers
- ⬜ P2-T13 Lab basic
- ⬜ P2-T14 Radiology basic
- ⬜ P2-T15 Insurance/TPA
- ⬜ P2-T16 MIS dashboards
- ⬜ P2-T17a Accounting outbox + Tally XML export
- ⬜ P2-T17b ERPNext v16 stack + connector
- ⬜ P2-T18 Print engine
- ⬜ P2-T19 MVP end-to-end test

### P3 — ABDM

- ⬜ P3-T1 (You) ABDM sandbox + HFR/HPR registration
- ⬜ P3-T2 Gateway client + mock
- ⬜ P3-T3 Fidelius crypto
- ⬜ P3-T4 M1 ABHA create/verify
- ⬜ P3-T5 M1 Scan & Share
- ⬜ P3-T6 FHIR bundle builder + validator
- ⬜ P3-T7 M2 Health Information Provider
- ⬜ P3-T8 M3 Health Information User
- ⬜ P3-T9 NHCX claims (priority)
- ⬜ P3-T10 PM-JAY / state schemes (priority)
- ⬜ P3-T11 Security audit (WASA/VAPT) readiness
- ⬜ P3-T12 DHIS incentive tracker

### P4 — Compliance

- ⬜ P4-T1 DPDP notice/consent/rights
- ⬜ P4-T2 Breach workflow
- ⬜ P4-T3 Retention engine
- ⬜ P4-T4 Encryption + secrets
- ⬜ P4-T5 Care-team access + break-the-glass
- ⬜ P4-T6 Legal pack (terms, Data Processing Agreement, privacy policy)
- ⬜ P4-T7 Disaster recovery
- ⬜ P4-T8 Per-tenant DB role (SET ROLE)

### P5 — Diagnostics

- ⬜ P5-T1 Orthanc + authorization plugin
- ⬜ P5-T2 OHIF + OIDC launch
- ⬜ P5-T3 DICOM worklist
- ⬜ P5-T4 Radiology reporting
- ⬜ P5-T5 Analyzer bridge (ASTM/HL7)
- ⬜ P5-T6 Westgard / Levey-Jennings QC
- ⬜ P5-T7 Delta checks + critical alerts
- ⬜ P5-T8 (Optional) OpenELIS integration

### P6 — Specialty and enterprise (broken into tasks when started)

- ⬜ Emergency/triage
- ⬜ OT + WHO checklist
- ⬜ ICU + early-warning scores
- ⬜ Dialysis
- ⬜ Maternity
- ⬜ IVF/ART
- ⬜ PCPNDT
- ⬜ Blood bank
- ⬜ CSSD
- ⬜ Biomedical
- ⬜ Dietary / housekeeping / BMW
- ⬜ NABH quality + incidents
- ⬜ MRD
- ⬜ ERPNext back office

### P7 — AI — ⏸ deferred (ADR-005)

- ⬜ P7-T1 LLM gateway (Bedrock India)
- ⬜ P7-T2 MCP read tools
- ⬜ P7-T3 MCP draft tools
- ⬜ P7-T4 Ambient scribe
- ⬜ P7-T5 Coding assist
- ⬜ P7-T6 Drug-interaction checks
- ⏸ P7-T7 Radiology AI

### P8 — Engagement

- ⬜ P8-T1 Notification service
- ⬜ P8-T2 Reminders / report links
- ⬜ P8-T3 Patient portal
- ⬜ P8-T4 Chatwoot
- ⏸ Nextcloud / Matrix / Stalwart

### P9 — Commercial

- ⬜ P9-T1 Plans + subscription billing
- ⬜ P9-T2 Onboarding + data import
- ⬜ P9-T3 Marketing site + demo
- ⬜ P9-T4 Support
- ⬜ P9-T5 Status page / SLAs
- ⬜ P9-T6 NABH software certification
- ⬜ P9-T7 Scale-out (k3s)

## Test credentials and one-click URLs

| What | Where |
|---|---|
| Web app (local) | http://alpha.localhost:8080 and http://beta.localhost:8080 (two synthetic hospitals) — start with `scripts/care-dev.sh up` then `scripts/care-dev.sh frontend` |
| Usernames | `care-doctor`, `care-nurse`, `care-pharmacist`, `care-billing`, `care-staff`, `care-fac-admin`, `care-admin`, `admin` (superuser) — in each hospital, as separate accounts |
| Passwords | one per hospital, in `deploy/seed/TEST_USERS.md` (git-ignored); new ones any time: `scripts/care-dev.sh reset-passwords` |
| Guided tour | docs/CLICK_THROUGH.md |
| Demo patients | generated per hospital by CARE's fixtures (synthetic; names differ between Alpha and Beta) |
| How it runs | docs/LOCAL_DEV.md |
| Operator console (P1-T5b) | http://console.localhost:9000/ops/ — user `op.owner`: password + authenticator key in `deploy/seed/TEST_USERS.md` (Keycloak section); code: `scripts/care-dev.sh keycloak otp op.owner`. Needs `scripts/care-dev.sh keycloak up` (starts the provisioner) and `keycloak seed` |
| Staff invitations (P1-T5e1) | Inbox http://127.0.0.1:8025 — a "Join Theta Test Hospital on IndieHR" e-mail for `nurse.asha` is waiting for you. Sign in as `nurse.asha` (password + key in `deploy/seed/TEST_USERS.md`, Keycloak section; code: `scripts/care-dev.sh keycloak otp nurse.asha`). Theta's admin to invite more: `first.admin@theta.example.test`. Invited and set up: `nina.047389@theta.example.test` ("Hospitals made by the provisioning engine" section) |
| First-admin setup e-mail (P1-T5c) | Inbox http://127.0.0.1:8025 (Mailpit; starts with `scripts/care-dev.sh keycloak up`). Theta's first admin `first.admin@theta.example.test` (set up from the e-mail): password + authenticator key in `deploy/seed/TEST_USERS.md`; code: `scripts/care-dev.sh keycloak otp first.admin@theta.example.test`. New hospitals from the console send a fresh e-mail |
| Provisioned hospitals (P1-T5a) | http://gamma.localhost:8080/api/ihr/auth/login/?next=/api/ihr/auth/session/ (also `epsilon`) — first admins `admin.gamma`, `admin.epsilon`: password + authenticator key in `deploy/seed/TEST_USERS.md` ("Hospitals made by the provisioning engine"); current code: `scripts/care-dev.sh keycloak otp admin.gamma` |
| Staff sign-in through the backend (P1-T4a) | http://alpha.localhost:8080/api/ihr/auth/login/?next=/api/ihr/auth/session/ — signs you in through Keycloak and shows who you are at that hospital (the web app's own sign-in button comes in P1-T4b). Needs `scripts/care-dev.sh keycloak up` and `keycloak seed`; the demo staff below are linked to CARE users |
| Keycloak staff sign-in (P1-T3) | http://alpha.localhost:8082/ and http://beta.localhost:8082/ (token viewer) — start with `scripts/care-dev.sh keycloak up`, `keycloak seed`, `keycloak devtools`. Users `dr.meera`, `nurse.asha`, `admin.bhavesh`, `new.neha`: passwords and authenticator keys in `deploy/seed/TEST_USERS.md`; current 6-digit code: `scripts/care-dev.sh keycloak otp <user>` |
