# ADR-009: Staff sessions held by the backend

- **Status:** Accepted (owner's answers of 2026-11-07)
- **Date:** 2026-11-07
- **Task:** P1-T4a (backend). P1-T4b moves the web app over to it.
- **Builds on:** ADR-008 (Keycloak), TENANCY_DESIGN §4. Closes review item R0-20 (tokens in `localStorage`).

## Context

CARE signs staff in with its own password form and keeps its JWTs in the browser's `localStorage`, where any script on the page can read them (R0-20). Staff must now sign in through Keycloak (ADR-008). Three choices were the owner's to make:
- where the tokens live;
- what "sign out" and the idle time-out end;
- whether CARE's old login survives anywhere.

## Decision

**1. The backend holds the tokens** (owner).
- Django runs the Keycloak sign-in (authorization code + PKCE) and keeps the tokens.
- The browser gets one random, HttpOnly session cookie per hospital address: `ihr_session` in dev, `__Host-ihr_session` (Secure) elsewhere.
- No staff token ever reaches page scripts, so a script injected into a page cannot steal or replay one.

| Endpoint | What it does |
|---|---|
| `GET /api/ihr/auth/login/?next=/x` | Sends the browser to Keycloak (password, code, membership gate) |
| `GET /api/ihr/auth/callback/` | Exchanges the code, checks both tokens, links the person to a CARE user, sets the cookie, returns to `/x` |
| `POST /api/ihr/auth/logout/` | Ends the session and single sign-on |
| `GET /api/ihr/auth/session/` | Who is signed in (never tokens) |

**2. Every request is checked again** (`StaffSessionAuthentication`). The checks:
- the session belongs to this hospital's schema;
- for unsafe methods: our `X-IHR-CSRF: 1` header, `Sec-Fetch-Site: same-origin` and the hospital's own `Origin`;
- idle and 12-hour limits;
- the stored access token against ADR-008: RS256 via the realm keys, `iss`, `typ`, lifetime, `azp` and exact `aud` = `ihr-h-<slug>`, `member`, `ihr_org` = this hospital's organisation, `acr = mfa`, hospital active;
- the person's link (`StaffIdentity`, by Keycloak `sub`, never by e-mail) to an active, non-deleted CARE user who is neither a superuser nor a machine account;
- no sign-out since the session started (`sessions_valid_after`, kept in Postgres so a Redis outage cannot undo it).

Each check fails closed. A dead session simply counts as not signed in: staff routes answer 403, the same as CARE's own classes, and the cookie is cleared on the way out.

**3. Tokens refresh once they are a minute old**, one request per session at a time. Keycloak refresh tokens work only once, so other requests wait up to 3 seconds for the new tokens rather than reuse the old one. An expired token seen while another request refreshes is a temporary refusal, not the end of the session. The realm's signing keys stay cached through a Keycloak outage. A refresh asks Keycloak again, so a sign-out at another hospital ends this one within about a minute (measured: 60 s). If Keycloak can't be reached, a still-valid token keeps working. A refused refresh ends the session.

**4. Sign-out and the 30-minute idle time-out both end single sign-on everywhere** (owner). The backend:
- calls Keycloak's logout with the refresh token;
- deletes the session;
- marks the person's other sessions at this hospital as ended.

The next sign-in at any hospital asks for the password and code again. The "signed out since" time is written after the request's database transaction, so a refused request cannot roll it back. Even if Keycloak cannot be told (`{"signed_out_everywhere": false}`), the next sign-in **here** must be a fresh one: the backend compares the ID token's `auth_time` with the sign-out time, and asks again with `prompt=login` when needed.

Idle time is reported by the web app (`X-IHR-Idle`, seconds since the last keyboard, mouse or touch input). Background polling therefore does not keep a session alive. A request without the header counts as activity: only the web app holds the cookie, and P1-T4b always sends the header.

A person who reaches the callback but is not linked here (or is deactivated) is simply refused (`/login?error=not_linked`). Their sign-in at other hospitals is left alone, because this is not a sign-out.

**5. CARE's own staff sign-in is switched off on hospital addresses** (owner: "Keycloak only", dev included). These routes return 404:
- `auth/login`, `logout`, `token/*`;
- `mfa/*`;
- `password_reset/*`, `password_change/`, `otp/password_reset/*`;
- the Django admin;
- `users/<id>/generate_service_account_token/`. Machine tokens are made by a platform command only (TENANCY §4.8).

CARE's JWT, Basic and Django-session authentication classes are removed. Patient OTP sign-in is unchanged. DRF tokens still work, for machine accounts of an active hospital only.

Five CARE test modules exercise the removed features and are left out of the tenant-mode run, with reasons (`ihr_platform/care_tests_excluded.txt`). They still run in the plain Backend job. The blocked patterns also catch DRF's `.json` format-suffix routes, which exist when DEBUG is on.

**6. Storage.**
- **Sessions** live in Redis through Django's cache, so every key carries the schema prefix. Each key is a hash of the cookie, and each value is AES-GCM encrypted with a key derived from the cookie: a Redis dump alone yields neither cookies nor tokens.
- **Login state** (state, nonce, PKCE verifier, `next`) travels in a sealed, 10-minute, HttpOnly cookie. An anonymous request therefore writes nothing to Redis.
- **API responses** get `Cache-Control: no-store` and `Vary: Cookie`. A cookie, unlike an `Authorization` header, does not keep a response out of shared caches.

**7. Keycloak clients stay public** (technical choice):
- The backend's code exchange uses PKCE.
- The only change on the Keycloak side is the redirect URI: `<hospital>/api/ihr/auth/callback/`.
- Signing the backend's requests to Keycloak with a key (`private_key_jwt`) moves to P1-T8, together with key delivery.

**8. One upstream patch:** CARE's batch endpoint now passes the cookie and our headers to each sub-request, so every sub-request is checked the same way.

## Consequences

- The web app must change (P1-T4b): a "Sign in" button that goes to `login/`, no stored tokens, and on every request the `X-IHR-CSRF` and `X-IHR-Idle` headers. It must also clear its cached data on sign-out and when the session has expired (Q-T9), and warn before the idle time-out. **Until then, staff cannot sign in through the current web bundle**; the API works as described.
- Other hospitals' open tabs end within about a minute of a sign-out, not at once. Instant back-channel logout comes in P1-T5.
- One Keycloak refresh call per active user per minute.
- Idle enforcement trusts the web app's report. A modified browser could stay signed in up to the 12-hour limit. Keycloak's own 30-minute idle still applies to its session, but not to the refreshes the backend makes.

## The web app (P1-T4b)

- **Sign-in:** "Sign in with your hospital account" (`IhrStaffSignIn`) replaces CARE's username/password form, its reCAPTCHA and "forgot password". It goes to `/api/ihr/auth/login/?next=<where the person was>` and shows the backend's outcome (`/login?error=…`) in words. The patient OTP tab is unchanged.
- **No staff token in the browser:**
  - CARE's token refresh poll, its two-factor screen (`/2fa`) and its password-reset pages are gone; those addresses redirect to `/login`.
  - Left-over `care_access_token` / `care_refresh_token` / `mfa_temp_token` keys are deleted on load.
  - The user page no longer offers "reset password" or "two-factor"; Keycloak owns both.
- **Headers:** every request to our own API carries `X-IHR-CSRF: 1` and `X-IHR-Idle` (seconds since the last keyboard, mouse or touch input). Requests to any other host never get them.
- **Idle:**
  - The last-input time is shared by the hospital's tabs. This tab's own time is the fallback, so "unknown" never counts as active.
  - A warning appears 2 minutes before the limit, with "Stay signed in".
  - At the limit, the person is signed out everywhere.
  - While someone types without making API calls, a light request every 4 minutes keeps single sign-on alive.
- **Sign-out:**
  - The backend ends the session and single sign-on.
  - Every cache in the browser is cleared (react-query in memory and on disk, session storage, view, filter and pharmacy caches) and the page reloads (Q-T9).
  - Other tabs of the hospital follow at once, with the same reason.
  - If the backend cannot be reached, the app stays signed out in this browser and retries on every load. The message says sign-in elsewhere may still be active.
- **Session ended on the server** (idle at another tab, sign-out elsewhere, removal): the same clean-up, then the sign-in page, which returns the person to where they were.
- **Consequence of the owner's "idle ends everywhere":** activity is tracked per hospital address. A forgotten tab of hospital A, idle for 30 minutes, therefore also signs the person out of hospital B, even while they work there. Recorded for the owner. A server-side "any hospital active" check would need the back-channel work of P1-T5.

## Deferred

| Item | When |
|---|---|
| Web-app patches and the owner's click-through | P1-T4b |
| Back-channel logout (instant), staff invitations, the CARE "set your password" e-mail replaced by an invitation | P1-T5 |
| `private_key_jwt` client authentication, key delivery and rotation; rate limits on `/api/ihr/auth/login/`; Redis persistence and eviction policy for sessions | P1-T8 |
| Playwright across two hospital addresses | P1-T1d |
