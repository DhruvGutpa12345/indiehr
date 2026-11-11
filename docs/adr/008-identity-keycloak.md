# ADR-008: Staff identity in Keycloak

- **Status:** Accepted (owner's answers of 2026-11-06 on roles, MFA, passwords and session length)
- **Date:** 2026-11-06
- **Task:** P1-T3. Builds on TENANCY_DESIGN §4 and Q-T4 (one login client per hospital).

## Context

Every hospital is a separate tenant, but a doctor may work at several hospitals. We need:

- one login per person;
- a hard wall between hospitals;
- a second factor for staff;
- password, lock-out and session rules that hold up in an Indian hospital setting.

The plan row for P1-T3 asked for job roles in Keycloak and MFA for admins only. The tenancy design (§4), approved later, asked for job roles in CARE and MFA for everyone. On 2026-11-06 the owner chose the tenancy design on both points.

## Decision

**One realm, `indiehr`, on Keycloak 26.8.2.** Each hospital `<slug>` gets:

| Item | What it is |
|---|---|
| Organisation `<slug>` | Named "`<hospital name>` (`<slug>`)", because organisation names must be unique. Its staff are *unmanaged* members, so removing someone from one hospital never deletes their account. The organisation id is stored once in `Hospital.keycloak_org_id`. |
| Public client `ihr-h-<slug>` | Authorization code + PKCE S256 only. No password grant, implicit flow, device flow, CIBA or token exchange. `fullScopeAllowed=false`. No optional scopes. Exact redirect URIs. |
| Client role `ihr-h-<slug>.member` | The only role Keycloak holds. **Job roles (doctor, nurse, billing…) stay in CARE**, assigned by each hospital's admin (owner's choice). |
| Login flow `ihr-hospital-<slug>` | Password, then a TOTP code, then a **gate**: anyone without `member` is refused. The gate also covers single sign-on from another hospital. |

**What a hospital's access token says**, set by the server and never by the request:

- `azp` and `aud` are `ihr-h-<slug>`;
- `resource_access` is `{"ihr-h-<slug>": {"roles": ["member"]}}`, and nothing about other hospitals;
- `ihr_org` is `{"alias": "<slug>", "id": "<organisation id>"}`, a fixed claim on the client;
- `acr` is `mfa`;
- it lives 5 minutes.

Keycloak's own `organization` claim is **not** used: the request could ask for other hospitals with `organization:*`. Hospital clients refuse that scope (`invalid_scope`).

**MFA for all staff** (owner's choice):
- The login flow has two levels: level 1 is the password, level 2 is a TOTP code.
- Every hospital client sets `minimum.acr.value=mfa`, so asking for less is overruled.
- Staff without an authenticator must set one up at their first sign-in.
- The built-in account console and admin console of the realm also demand `mfa`.
- P1-T4 must still check `acr == "mfa"` in the backend.

**Passwords** (owner's choice, NIST SP 800-63B):
- at least 12 characters;
- not one of the last 5;
- must not contain the username or email;
- not on a block list of 46,146 common passwords of 12+ characters (SecLists, MIT; `deploy/keycloak/password-blocklist/`);
- no composition rules.

**Lock-out:**
- After 5 failures the account is locked temporarily: 1 minute at first, growing to 15 minutes.
- Two failures less than 1 second apart also lock it for 1 minute (Keycloak's quick-retry rule, which slows scripted guessing). Anyone who knows a username can therefore lock that person out for a while. Per-IP rate limits and alerts come in P1-T8.
- Errors are identical for an unknown user, a wrong password and a locked account.

**Sessions** (owner's choice):
- idle limit 30 minutes, hard limit 12 hours;
- access tokens last 5 minutes;
- refresh tokens rotate and can be used once.

Removing a member also ends that person's sessions, because a refresh does not re-run the login flow.

**Other doors are shut:**
- self-registration is off;
- anonymous dynamic client registration is refused;
- the realm's direct-grant flow refuses every password grant (`ihr-deny-all`);
- users cannot change their own email or name, because email is a login name;
- login and admin events are kept 180 days (CERT-In).

**Gates fail closed.** Keycloak's role and client-scope conditions skip the Deny step when their role or scope can't be found, which would let everyone in. So:
- The realm's own browser flow refuses any client that carries the `ihr-hospital` marker scope. A hospital client that loses its flow binding is therefore refused, not opened.
- The template flow's gate names a sentinel role that nobody holds, so the template refuses everyone.
- A new hospital client stays **disabled** until its gate is configured and bound.
- `ihr_keycloak_sync --check` reports drift in:
  - the login flows: their exact shape (password + OTP), the gate configuration, the role the gate needs, and the deny-all flow;
  - each hospital client: settings, per-client overrides of token or session lifetimes, flow bindings, scopes, role scope mappings and token mappers;
  - how `member` is granted: only directly, never through a group, a composite role or the default roles;
  - the shared scopes, which must not add roles, and the marker scope, which must not add claims;
  - realm flags;
  - any unknown client, and with `--all` any client left from a deleted hospital.
- The sync repairs what it can, and a broken hospital doesn't stop the others. Removing a member fails loudly if they would keep `member` some other way.
- P1-T4 re-checks `member` and `ihr_org` on every request as defence in depth.

**Configuration as code.**
- Our own standard-library Python (`plugins/ihr_platform/ihr_platform/keycloak/`) talks to the Admin REST API. One code path serves dev, CI and tests.
- `manage.py ihr_keycloak_sync [--realm] [--all|--slug x] [--check] [--rebuild-flows]` creates or repairs the realm and each hospital's setup.
- We do **not** use keycloak-config-cli: no released version handles 26.8 organisations correctly. We also do not use `--import-realm`, which skips realms that already exist.

**Credentials.**
- In dev and CI, the sync uses Keycloak's bootstrap service account in `master`, passed to one `docker compose exec` by name only. It is never a Django setting and never in the web app's environment.
- That account is equivalent to a server admin. It **must not exist in production.**

## Deferred (with owner)

| Item | When |
|---|---|
| CARE backend accepts Keycloak tokens (signature, `iss`, `azp`/`aud`, `member`, `ihr_org` id = `Hospital.keycloak_org_id`, `acr`), web-app login at `/auth/callback`, token storage (R0-20) | P1-T4 |
| Narrow production credentials *(P1-T5f, owner: two processes; built differently from the first idea, measured live on Keycloak 26.8)*: two service accounts of the hospitals' realm instead of the master bootstrap admin. `ihr-onboarding` (manage-realm, manage-clients, manage-users, organisations; console-queued hospital runs) in the onboarding worker; `ihr-staff-worker` (manage-users, read clients, organisations; invitations, resets, expiry) in the staff worker, refused realm settings, clients, flows, its own roles and other realms. Neither reaches `master` or the operator realm. Admin-API audience (`kc-admin-api`) required by 26.8. Drift-checked, plus a check that no person holds a realm-management role. FGAP v2 per hospital not used: it cannot restrict organisations or flows, and a per-hospital account would multiply secrets; the staff account's reach over all staff accounts in the realm stays, by design. Secret storage and rotation: P1-T8. | done |
| Staff invitations by email (no admin-chosen passwords); MFA reset by single-use link; recovery codes at enrolment | P1-T5 *(built in P1-T5c/T5e1/T5e2; recovery codes not now, owner 2026-11-07)* |
| Operator realm `indiehr-ops` and platform super-admin | P1-T5 |
| Hindi login pages (Keycloak ships no Hindi): a reviewed translation | P1-T7 |
| Hospital-branded login pages (theme) | P1-T4 / P1-T7 |
| Keycloak production settings: ~~own database instance~~ (owner 2026-11-09: own database and role on the shared Postgres server, Q-K14), `start --optimized` with a pre-built image, single-node cache, proxy limits on `/admin` and `/realms/master` (P1-T8a), trusted proxy address, per-IP rate limits, login and lock-out events in the 180-day journal | done (P1-T8d1) |
| Alerts on lock-out bursts | P1-T9 (owner 2026-11-09, Q-K15) |
| A small Keycloak extension whose gate fails closed by itself (would replace the per-hospital flow copies) | When there are about 50 hospitals, or a template change becomes frequent |
| HPR id as a verified user attribute and token claim (§4.10) | ABDM phase |

## Consequences

- One flow copy per hospital: at the cell cap of 100 hospitals that is 100 flows. Changing the template needs `ihr_keycloak_sync --all --rebuild-flows`. While each hospital's flow is replaced, its client is disabled and bound to the deny-all flow.
- Keycloak's TOTP is HmacSHA1, 6 digits and 30 seconds, so every authenticator app works.
- Keycloak 26.8.2 is required. Older versions have a 2FA-setup bypass, an organisation-login takeover and an ACR-enforcement bypass.
- Hospital staff need a phone authenticator from day one. This is a support cost that pilots must plan for.
