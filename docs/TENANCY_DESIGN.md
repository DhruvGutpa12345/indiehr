# Tenancy design: how each hospital's data is kept separate (P1-T0)

- **Status:** **Accepted** 2026-11-06. The owner approved the design and accepted every §12 recommendation (answers recorded in QUESTIONS.md).
- **Date:** 2026-11-06
- **Amends:** [ADR-002](adr/002-tenancy-schema-per-hospital.md), see "Amendment 1" there.
- **How it was made:** an orchestrated review over CARE's actual code, run as one workflow of 17 agents:
  - **Mapping:** 6 parallel mappers looked at 100 CARE models, runtime, frontend and libraries.
  - **Design:** 3 independent designs were scored by 3 judges.
  - **Verification:** 5 adversarial reviewers each attacked the winner through a different lens.
  - **Verdict:** all five said *sound with fixes*. Every fatal flaw they found is fixed below.
  - **Completeness check:** an independent reviewer compared this document with all 48 review items and found 24 missing or misstated, plus 7 contradictions. **All are fixed in this version (v2).**
  - **Evidence:** [docs/reviews/p1-t0/](reviews/p1-t0/).

---

## 1. In plain words

- **Each tenant gets its own private section of the database (a Postgres "schema").** Everything about that tenant lives inside it: patients, bills, stock, staff accounts and settings.
- **Nothing inside one hospital's section points at another's.**
- **The shared area (`public`) holds only our platform's own records.** That means the list of hospitals and their web addresses, plus a few routing tables. It holds **no patient data and no CARE tables at all.**
- **A person has one login (Keycloak) across all hospitals.** Inside each hospital they work at, they have a separate staff record with that hospital's roles. A doctor at two hospitals signs in once and switches between them; each hospital sees only its own copy.
- **Hospitals are told apart by web address** (`sunrise.app.<domain>`). The browser keeps each hospital's login separate automatically.
- **Leak-proofing isn't left to good intentions.** Caches, background jobs, file storage, tokens and anonymous pages all carry the hospital's identity, and an automated test suite tries to break isolation on every change.

## 2. Decision and rejected alternatives

**Chosen: every CARE app in each tenant's schema; `public` is only the platform registry** (the winning design, 123.5; the near-identical design 3 scored 120).
- **Every CARE Django app goes into the tenant schema:** `users`, `security`, `facility`, `emr` and `audit_log`, plus Django's `auth`, `contenttypes`, `sessions`, `sites` (CARE ships its own migrations for it), `admin` and `authtoken`.
- **`public` holds only IndieHR platform apps.**
- **Mechanism:** **django-tenants 4.0.1**, the 2026-10-23 security release (anything older is vulnerable, GHSA-693j-7v9f-x7pw), plus **tenant-schemas-celery 5.0.0**. Both declare support for Django 6.1. P1-T1 must prove both against CARE.

Two of the three independent designs converged on this.

| Alternative | Why rejected |
|---|---|
| **One shared user record per person in `public`** (design 2, score 84) | CARE's upstream migrations link users to facilities and organisations. It would need empty, locked "shadow" copies of every CARE table in `public`, more patches, a global `is_superuser`, and deletes cascading across schemas. |
| Hand-written `search_path` middleware | Would re-implement django-tenants' per-schema migrations, routing, tenant commands and Celery integration, with the same connection-pooling hazard. |
| **Baseline: one full CARE installation per hospital** (zero CARE patches) | Strongest isolation and no tenancy code, but one Django + Celery + Redis + DB set per hospital costs roughly 600 MB–1 GB of RAM each and N upgrades per release. That is uneconomic for 10–100 bed hospitals at our price point. This is still the shape of the **dedicated tier** (§7.6). |
| Postgres row-level security | Needs a `tenant_id` on every CARE model, which conflicts with ADR-002 and the minimal-patch rule. |
| django-pgschemas 1.3.2 | Smaller community and no Celery integration. **Kept as a fallback** if django-tenants fails P1-T1. |
| Database per hospital | Kept for the paid "dedicated" tier only (§7.6). |

## 3. What lives where

**Rule:** django-tenants splits by **whole Django app**, not by model, so an app is either entirely `public` or entirely tenant. Our apps are therefore split in pairs.

| Schema | Apps | Contents |
|---|---|---|
| `public` (shared) | `django_tenants`, `django.contrib.contenttypes` (also in tenant), **`ihr_platform`**, **`ihr_refdata_registry`**, **`ihr_abdm_router`** | `Hospital` (tenant registry), `Domain`, `Plan`, `StaffDirectoryEntry` (Keycloak `sub` ↔ hospital, no names or contacts), `TenantOperation` (provisioning, migration and export log), `PackRelease` (reference-pack registry), `HfrFacilityRoute`, `AbdmCorrelation`, optional read-only India lookups (PIN directory) that **no CARE table references** |
| `h_<10 chars>` (one per tenant) | All CARE apps and Django auth/session/admin apps, plus **`ihr_identity`**, **`ihr_tenant_settings`**, **`ihr_refdata`** (applied packs) and every future `ihr_*` domain plugin | Everything clinical, billing, stock, staff, roles, value sets and audit |

**Enforced by tests:**
- `public` contains only the allow-listed tables;
- no app label appears in both lists (except `contenttypes`);
- no CARE table exists in `public`. Code that runs without a tenant therefore **fails loudly instead of leaking**.

**Naming:** this document says "hospital" for a tenant. If you choose "legal entity" (Q-T0), one tenant can hold several facilities (branches). Nothing in the design changes except that ABDM routes and the switcher point to a **facility within a tenant** (§8 already stores the facility id).

**Schema names** are generated (`h_` + 10 random lowercase characters) and never typed by anyone. The web address slug is a separate field.

**Scale fact:** each schema has about **1,077 database relations** (tables, indexes, sequences) and 822 migration records. 300 tenants means about 325,000 relations in one database. P1-T1 measures catalogue size, migration time and memory at 1, 50 and 200 tenants before we set the per-cluster cap (§7.6, open question Q-T6).

## 4. Identity and login

1. **Keycloak 26.8.x is the only global identity:** one realm, one `sub` per person, passwords and MFA. Each tenant is a **Keycloak Organization**.
2. **Inside each tenant**, the person has an ordinary CARE `users.User` row, linked to Keycloak by `ihr_identity.StaffIdentity(user, keycloak_sub)`.
   - `User.external_id` stays random per schema, so CARE's own tokens and ids cannot be replayed or correlated across hospitals.
   - Roles are CARE's own per-hospital roles, assigned by that hospital's admin.
3. **Token checks**, done in the DRF authentication layer so they also cover CARE's batch-request endpoint (fatal flaw fixed, §6.5):
   - RS256 signature, `iss`, `aud`, `exp`, `typ=Bearer`;
   - `azp` must be in the allow-list;
   - ~~the `organization` claim must contain **exactly one** organisation~~ *(P1-T3, ADR-008: the request can widen Keycloak's `organization` claim, so hospital clients refuse that scope)*. Instead the server-set claim `ihr_org.id` must equal the current tenant's `keycloak_org_id`, `azp` must be `ihr-h-<slug>`, and `resource_access.ihr-h-<slug>.roles` must contain `member`;
   - `sub` → `StaffIdentity` → active `User`.
   - **Recommended: one Keycloak client per tenant** (Q-T4), so a script on hospital A's page cannot get a token for hospital B.
   - **MFA everywhere for staff:** required at the realm level, and the token's `acr` must show MFA. *(P1-T3: Keycloak enforces `acr=mfa` per hospital client with a two-level login flow; a per-hospital gate flow refuses non-members, including through single sign-on. Details: ADR-008.)* A weak login at one hospital therefore cannot open a doctor's other hospitals through silent single sign-on.
4. **A doctor at two hospitals:** one Keycloak account and two organisation memberships.
   - The "Hospitals" switcher in the user menu lists their hospitals from `StaffDirectoryEntry`.
   - Choosing one goes to that hospital's address, where Keycloak single sign-on logs them in silently.
   - Each origin keeps its own token. Leave, schedules and favourites are per hospital.
5. **Adding staff** uses Keycloak's **organisation invitation**, so the person must accept. Matching is by **verified email only, never phone**, and the response is always the same "invited", so it can't be used to probe for existing accounts. CARE's own user-create and password endpoints are blocked in production.
   - Keycloak admin calls run in a **separate provisioning worker** with a narrowly-scoped credential (Keycloak fine-grained admin permissions v2, limited to organisation membership), not in the hospital-facing web app.
   - Realm self-registration is **off**. The tenant `User` stays **inactive until the invitation is accepted**. Accounts are never auto-linked by email from external identity providers.
   - *(P1-T5c, built for a new hospital's first admin: the provisioner creates their Keycloak account with no password or authenticator, stamped `ihr_invited_for=<hospital uuid>` (an admin-only user-profile attribute), and asks Keycloak to e-mail a one-time link (`execute-actions-email`: verify e-mail, choose password, set up authenticator; 72 hours). Keycloak cannot cancel a link once sent, so sending again replaces the unused account, which kills every earlier link. Deactivating the user before first sign-in cancels the setup. *(P1-T5e1: every "Add user" in CARE is such an invitation: the CARE user is created inactive with no usable password and CARE's own e-mail suppressed; CARE's bulk upsert and format-suffix routes are blocked; the answer is the same whether or not the address has an account. The provisioner delivers: a new address gets the setup e-mail; an address with an account someone has set up gets Keycloak membership and our "join" e-mail (one-time token, hash stored, 72 h), and becomes active only after following it, signing in with password + code and clicking Join. Another hospital's account that was never set up makes the invitation wait (up to 72 h) instead of binding to it. Names in e-mails are plain text; 50 invitations per hospital per day.)* The CARE user is linked with `StaffIdentity.awaiting_setup` and stays inactive until their first sign-in at that hospital with password + code and a verified address. An address that already has an account stops provisioning at its second step; the "join this hospital" confirmation for existing accounts comes with staff invitations in P1-T5e. Keycloak's realm mail server is set from `IHR_KEYCLOAK_SMTP` (TLS required outside development); its password only from `IHR_KEYCLOAK_SMTP_PASSWORD` in the sync process; development and CI use Mailpit.)*
6. **Leaving and revocation:**
   - access tokens last **5 minutes or less**;
   - *(P1-T5d: a sign-in that ends in Keycloak — sign-out, idle time-out, an admin ending it — reaches every hospital it touched at once through back-channel logout: one internal URL on the platform host (`IHR_BACKCHANNEL_LOGOUT_URL`), signed logout token for exactly one hospital client, per-hospital `sid` marker in the cache.)*
   - removing someone deactivates their tenant `User` immediately (Keycloak admin event, plus a nightly reconciler);
   - a short-lived **revocation list** (Redis, global key, TTL = token lifetime) of `sub`+tenant pairs is checked on every request, so removal takes effect within seconds rather than at token expiry. *(P1-T4a, ADR-009: kept in Postgres instead, as `StaffIdentity.sessions_valid_after`, so a Redis outage cannot undo it; set at sign-out, deactivation and deletion and checked on every request. Unlinking deletes the row.)*
7. **Patient OTP logins** (CARE's patient portal) get a `tid` (tenant) claim, checked on every request.
8. **Machine accounts** (analyzer bridge, integrations): we keep DRF `TokenAuthentication`, restricted to `is_service_account` users. Tokens are tenant-local because the token table is per schema, and they are created only by a platform command.
9. *(P1-T5b, built: realm `indiehr-ops` with the same baseline as the hospitals' realm — password policy, lock-out, password + code with minimum ACR on the console client; role `ihr-operator` mapped explicitly to the console client; our own operator list `OperatorAccount`, changed only by `ihr_operator`. The console is server-rendered pages under `/ops/` on the platform host (dev `http://console.localhost:9000`), with its own backend-held session cookie, a strict CSP and no CARE routes. Work is queued on `TenantOperation` and run by a separate provisioner process (`ihr_provision_worker`), the only process holding the Keycloak admin credential.)*
9. **Platform operators** have no CARE user anywhere. They sign in through a **separate Keycloak realm** (`indiehr-ops`), so the provisioning credential can never touch operator accounts, and they use a separate console on `public`. Support access to patient data is **break-glass only**: approved by the hospital admin, time-limited and logged (Q-T7). A scheduled job **expires grants on time** and deactivates the support `User`.
10. **Doctors' HPR ids** (national registry) are a **verified, global attribute on the Keycloak user**, set only by a platform verification flow and passed as a token claim. Hospitals cannot type an arbitrary HPR id onto a staff record.

## 5. Reference data (drug catalogue, value sets, roles, forms, price lists)

- **Everything CARE reads lives in the tenant schema.** Shared reference data is shipped as **versioned "packs"** applied to each tenant: JSON in the repo, sha256 recorded in `PackRelease`. Examples: `core-in` for drugs, lab definitions, standard forms, print templates and identifier configs (UHID, ABHA); `pmjay-2026` for government package codes.
- **Permissions and system roles** come from CARE's code. `sync_permissions_roles` runs per tenant after every upgrade, and our Indian roles (P1-T11) are a pack.
- **Pack updates:**
  - a row the hospital hasn't edited is updated;
  - an edited row is left alone and shown to the hospital admin as "update available";
  - nothing is deleted, only retired;
  - **forms are never edited in place:** a new version is a new form, because CARE re-renders past answers with the current definition.
- **Each pack release names the CARE commit it was built against.** Content hashes are computed over the fields the pack sets, not whole rows. CI re-applies every pack to a fresh tenant after each upstream sync, so a CARE schema change is caught as a pack failure, not mis-reported as "hospital edited".
- **Government geography** (state, district, local body) is loaded by our own pack loader for the hospital's state(s). CARE's `load_govt_organization_csv` only takes a URL and has no state filter, so we don't depend on it.
- **SNOMED / ICD / LOINC** search comes from our shared terminology service (P1-T10) over HTTP, not from database tables.

## 6. Runtime isolation: every path that could leak

| # | Path | Control |
|---|---|---|
| 6.1 | **Cache** (Redis via Django cache) | `KEY_FUNCTION` adds the schema to every key; settings only, no CARE edits. A small allow-list of deliberately global keys (Celery/kombu, `ihr:global:*`). **CI audit:** after the full suite, scan every Redis key; anything unprefixed and not allow-listed fails the build. |
| 6.2 | **Raw Redis** (`RecentViewsManager`, the only raw user in CARE) | Patch: schema-prefixed keys. Our own code must use `ihr_platform.redis.tenant_key()`; a lint test enforces it. |
| 6.3 | **In-process caches** (class-level dicts) | Patch both: `PatientIdentifierConfigCache` and `CACHED_CONFIG` keyed by schema. **(Fatal flaw fixed:** the winning draft left one unpatched.) The monthly upstream-sync checklist greps for new class-level caches. *(P1-T4a: one reviewed process-wide cache added, the Keycloak realm's public signing keys in `ihr_identity/oidc.py`; they belong to the realm, not to a hospital.)* |
| 6.4 | **Background jobs** (Celery) | tenant-schemas-celery puts the schema in each message; a task without one runs in `public`, finds no CARE tables and fails. Periodic jobs run per **active, fully-migrated** tenant: `ihr_platform.beat.ActiveTenantScheduler` (P1-T1b; without it CARE's three cleanup jobs would run in `public` and fail). A separate reports queue with per-tenant concurrency limits. |
| 6.5 | **CARE's batch-request endpoint** (calls views directly, skipping middleware) | **(Fatal flaw fixed:**) every tenant check lives in the **DRF layer**, using the database connection's tenant rather than the request object, so it runs for batch sub-requests too. A logged patch limits batch sub-requests to an allow-list of paths. *(P1-T4a: a logged patch passes the staff session cookie and our CSRF/idle headers to each sub-request, which is re-checked by `StaffSessionAuthentication`; blocked CARE routes stay blocked inside a batch.)* |
| 6.6 | **Routes, anonymous and authenticated** | **Fail-closed route allow-list:** every URL route must be listed (anonymous / staff / patient-OTP / service / blocked), and a CI lint walks the URL resolver and fails on any unlisted route or any view whose `authentication_classes` isn't a tenant-bound class. New upstream routes (e.g. password reset, password change, token refresh and verify) therefore arrive **blocked**. `OrganizationPublicViewSet` is restricted to government geography only. The CI crawl tests anonymous access too. |
| 6.7 | **Our public tables read from tenant code** (directory, routes) | Their default manager raises unless scoped to the current tenant (or allow-listed, e.g. "my hospitals" filtered by `sub`). |
| 6.8 | **Files** (S3 in an Indian region) | Every object key starts with `t/<hospital uuid>/` (patched at all **6** key sites in `file_manager.py` and the cover-image upload). **Patient files and public assets are in separate buckets with separate credentials.** Logo, avatar and cover URLs are presigned (logged patch); CARE builds them unsigned today. **Separate credentials need a patch to `care/utils/csp/config.py`:** CARE's patient and report clients reuse the facility bucket's key; until patched, both buckets share one credential (recorded risk). Per-tenant encryption keys (KMS, crypto-shredding at offboarding) are deferred to **P4-T4**. |
| 6.9 | **Browser** | One origin per tenant; tokens, caches and filters are isolated by origin. **Slugs and addresses are never reused or renamed** (retired ones stay reserved). *(P1-T2a: enforced in code. A slug cannot change; a domain cannot be renamed or moved to another hospital; deleted hospitals' slugs and domains go to `RetiredAddress` and are refused for new ones.)* The logout patch clears cached data on shared computers (Q-T9). |
| 6.10 | **Database privileges** | Two roles: `ihr_owner` (DDL; migrations and provisioning in a separate job) and `ihr_app` (runtime, data-only, no `CREATE`). `ALTER DEFAULT PRIVILEGES` gives `ihr_app` access to tables created by future migrations. **Hardening (new task P4-T8):** a per-tenant DB role switched with `SET ROLE`, so a SQL-level escape in one tenant cannot read another. |
| 6.11 | **Connection pooling** | No transaction-mode PgBouncer, ever (known django-tenants leak); session mode only, if one is added at all. **From P1-T1c:**<br>• Each connection keeps about 0.8 MB (light use) to 5 MB (every table) of table definitions per hospital it has served, and never frees it. So app and worker connections are recycled: a finite `CONN_MAX_AGE` of a few minutes, plus worker recycling.<br>• Postgres runs with `max_locks_per_transaction = 256` and, when containerised, at least 1 GB of shared memory (`shm_size`).<br>• Releases use the multiprocessing migration executor. |
| 6.12 | **Logs and monitoring** | Every log line carries the tenant (CERT-In traceability). CARE's own audit log stays off (pinned in `apply_tenancy`, P1-T1b: its thread-local survives GET requests and could attribute one tenant's change to another's user); P1-T6 replaces it with a per-tenant one. Per-tenant request and error rates, queue depth and schema size: **P1-T9**. |
| 6.13 | **Process-wide settings** (tax codes, cancel windows, `PATIENT_GLOBAL_EDIT_ACCESS_ENABLED`) | Pinned to safe, uniform values (global edit access off). Per-hospital variations go into `ihr_tenant_settings`. **Found in P1-T1b, fixed in P1-T2a:** `CURRENT_DOMAIN` built staff password set-up/reset links, so every tenant but one got links to the wrong host. Links now come from each hospital's own address (`ihr_platform.links`), and reset tokens carry a `tid` claim. |
| 6.14 | **OTP SMS abuse across tenants** | A global per-phone send throttle (deliberately cross-tenant key, hashed). |
| 6.16 | **Noisy neighbours** | Per-tenant API rate limits at the ingress; `statement_timeout` (30 s for web, longer for the report queue) on the runtime role; per-tenant Celery concurrency (§6.4). Storage and database-size quotas come with plans and billing (P9-T1). |
| 6.17 | **Data residency beyond files** | Postgres, Redis, Keycloak, logs, backups and the CI artefacts that hold real data all run in Indian regions (ADR-003). CI only ever uses synthetic data. |
| 6.18 | **Settings drift** | CARE's `config/settings/test.py` replaces `CACHES` (dropping our key function). Our test settings re-apply every tenancy setting **after** importing CARE's. A Django system check (`ihr_platform.E001…`) fails startup if the engine, router, key function, `TENANT_APPS` or auth classes are not ours; it runs in every environment and in CI. |
| 6.15 | **Features that crossed facilities in CARE** (resource requests between facilities, global patient search) | These now work **within one tenant only**. Inter-hospital referral is redesigned later on ABDM (Q-T8). |

## 7. Provisioning, upgrades and operations

1. **New hospital (v1: no template cloning).** Two fatal flaws came from cloning a template schema: identical record ids in every tenant, and missing columns marked as "migrated". So v1 does:
   1. create the schema and run all migrations;
   2. apply reference packs;
   3. load the state's geography;
   4. create the facility and the Keycloak organisation and client;
   5. send the first admin invite.

   Target: **under 10 minutes**, run as a background job by `ihr_owner`. *(P1-T5a, owner: target **under 5 minutes**; measured **67 s** on the dev stack (schema migrations 65 s). Built as `ihr_provision` / `ihr_platform.provisioning`: steps reserve → schema → domains → roles → value sets → profile → state + district → inactive first admin → facility → Keycloak (client off) → activate (client on, `activated_at`). Each step is idempotent and recorded in `TenantOperation`; a Postgres advisory lock allows one run per hospital; roll-back only for a hospital never live (`activated_at` empty), and it releases the slug and address (owner's choice). Keycloak objects carry `ihr.hospital_uuid` and are never adopted or removed for another hospital. Background job and console: P1-T5b.)* **Idempotent and resumable:** each step is recorded in `TenantOperation`, re-running continues from the last completed step, and a failed onboarding can be rolled back (drop schema, Keycloak org and client, domain). Template cloning can come back later as an optimisation, with id regeneration and a "template is current" check.
2. **Upgrades (CARE's migrations aren't backward-compatible).** CARE ships column renames, data rewrites and hard deletes, so "old code on new schema" can't be promised. For pilots: **a short platform-wide maintenance window** (default nightly around 02:00 IST, announced; Q-T1).
   - **Order:** migrate `public`, then all tenants in parallel subprocesses, then per-tenant `sync_permissions_roles` and **pack rollout**. **Not `sync_valueset`** on existing tenants: it either skips existing value sets or, with `--overwrite`, wipes hospital edits. Its `type=bool` flag means even `--overwrite False` overwrites. System value sets ship in packs instead (§5); `sync_valueset` runs only on brand-new schemas.
   - **Fleet runner `ihr_fleet_run`** (not django-tenants' serial `all_tenants_command`):
     - per-tenant error isolation (one failure doesn't stop the rest);
     - resumable;
     - filters by status (skips public, provisioning, suspended);
     - writes one result row per tenant to `TenantOperation`.
   - **Window length budget** = (pre-flight minutes per schema × tenants ÷ parallelism) + 15 minutes margin, published with each release. Hospitals get a **downtime procedure** (read-only notice, paper forms, back-entry afterwards) even though there is no full outage kit (ADR-000). If an upstream migration is too slow, we ship a logged patch that rewrites it (e.g. a batched data migration).
   - CARE's `maintenance_mode` app is process-wide; during the window we use our per-tenant gate instead (checked in P1-T1a).
   - A per-tenant **migration fingerprint gate** returns "upgrading" (503) for any tenant whose schema doesn't match the running build.
   - **Pre-flight on staging:** time the migration against a copy of the largest tenant; fail the release if one schema takes over 120 s or a migration rewrites a large table.
3. **Our own container entry scripts** live outside `apps/`:
   - the beat script never runs `migrate`;
   - migrations are a separate release job;
   - the health check is answered on the platform host: `localhost`, `127.0.0.1` and the probe host map to the public tenant and are in `ALLOWED_HOSTS`, `/ping/` exists on the public URL config, and the image `HEALTHCHECK` uses our script.
4. **The `pg_trgm` extension** is created once in `public` by our first migration, before any tenant migrates. CARE's migration 0064 would otherwise put it in the first tenant's schema and break the others.
5. **Backup and restore:**
   - **Cluster-level point-in-time recovery** with pgBackRest (continuous WAL archiving, second Indian region) meets ADR-007's targets (data loss ≤ 15 min, restore ≤ 4 h). Set up in P1-T8 and tested in P4-T7.
   - **Per-tenant logical dumps** (`pg_dump -n h_x` plus the file prefix) nightly, for single-hospital restores and moves.
   - **Single-hospital restore runbook:**
     1. freeze the tenant (503);
     2. restore the schema to a side name;
     3. migrate it to the current release;
     4. reconcile the public rows, Keycloak org, ABDM routes and file prefix;
     5. replay the **erasure ledger** (§7.7) so deleted personal data stays deleted;
     6. swap and unfreeze.
   - Automated monthly restore drill. CI test: restore one schema into an empty database, and confirm the others are byte-identical before and after an in-place restore.
   - **DPDP requests are per person, not per hospital.** Access, correction and erasure are handled by P4-T1, not by schema dumps.
6. **Cells.** A cell is one database plus app servers serving a group of tenants, capped at N (Q-T6).
   - The dedicated tier is a one-hospital cell, using the same code.
   - A scripted `move_hospital` command does: freeze → drain the tenant's queued tasks → dump → restore (target must be on the same release) → copy files → move the tenant's public rows (registry, directory, ABDM routes and correlations) → switch the route and ABDM forwarding → unfreeze.
   - The single ABDM bridge forwards to the right cell.

7. **Tenant lifecycle and offboarding (§7.7).**
   - **States:** `provisioning` → `active` → `suspended` (read-only, e.g. non-payment) → `offboarding` → `archived` → `deleted`. Each state controls what the gate, Celery beat and the fleet runner do.
   - **Offboarding:**
     1. notice period per contract;
     2. a **hand-over export** for the hospital: FHIR/CSV plus files. It is **scrubbed of secrets** (password hashes, TOTP secrets, tokens, sessions, OTPs) and is separate from our internal DR dumps;
     3. medical-record retention honoured (we keep or the hospital keeps, per contract and law);
     4. then drop the schema, the file prefix, the Keycloak org and client, and the routes;
     5. backups age out by a stated date;
     6. a **deletion certificate** is issued.
   - An **erasure ledger** in each tenant records person-level DPDP erasures so that restores can re-apply them (§7.5). CARE's deletes are soft deletes, so P4-T1 adds true erasure for non-statutory data.

## 8. ABDM routing (many hospitals, one bridge)

- **One bridge** endpoint `abdm.app.<domain>` on the platform host, registered once for our integrator id. Each hospital facility is a HIP and/or HIU service under it.
- **Routing inbound requests:**
  - `X-HIP-ID` / `X-HIU-ID` → `HfrFacilityRoute`; only **verified, active** routes count, and the uniqueness rule is (HFR id, role). **Only a platform command may create a route or set `verified_at`**, after checking our bridge's registered services on the ABDM gateway. Hospitals cannot write routes.
  - reply callbacks → `requestId` → `AbdmCorrelation`;
  - health-data pushes → an opaque per-transaction path token.
  - Anything unresolved or conflicting is rejected and logged.
- **Correlation rows are written outside the request transaction** (autocommit connection, or the send happens in a Celery task after commit). Otherwise fast callbacks race an uncommitted row.
- **The handler acknowledges fast** and continues in a Celery task in the right tenant.

## 9. Frontend

- **One web-app build for every hospital.** The API is same-origin (`REACT_CARE_API_URL=""`), `REACT_CARE_URL_MAP` is never set, and the CSP allows `self`, the Keycloak host and the file-storage host.
- An unauthenticated `bootstrap` call returns the hospital's name, branding and Keycloak settings. *(P1-T2a: `/api/ihr/bootstrap/`, `/api/ihr/bootstrap.js` and `/api/ihr/logo/` serve branding only, with `private` caching. The full profile, `/api/ihr/profile/`, is for signed-in staff. Keycloak settings join in P1-T4.)*
- **Ingress:** wildcard certificate `*.app.<domain>`; `/api` and `/plugins` go to the backend with the Host header kept. *(P1-T2b: our frontend nginx does this for `/api/` when `API_UPSTREAM` is set; `index.html` loads `/api/ihr/bootstrap.js` before the bundle, and `care.config.ts` takes the hospital's name, colour and logo from it. Hospitals without a logo get an initials mark. Emails use our templates and the hospital's name.)*
- The **hospital switcher** is an `ihr_*` frontend plugin.
- **Login moves to Keycloak** (P1-T4), including the token-storage decision (review R0-20). *(P1-T4a, ADR-009: the backend holds the tokens and the browser gets an HttpOnly cookie; the jump to Keycloak is a top-level redirect from the backend, so the CSP needs no Keycloak host after all. Web-app side: P1-T4b.)*

## 10. Upstream patch ledger (all logged in UPSTREAM_PATCHES.md)

There are about **17 small files**: 15 backend, 2 frontend, counting the two lock files as one entry. Most rarely change upstream; the recurring cost is the lock files and `Login.tsx`.

| File | Change |
|---|---|
| `config/celery_app.py` | Celery app from tenant-schemas-celery (2 lines) |
| `config/patient_otp_token.py`, `care/emr/api/otp_viewsets/login.py` | Tenant (`tid`) claim on patient OTP tokens, checked on verify |
| `care/emr/signals/patient/base.py`, `care/emr/models/patient.py` | Schema in in-process cache keys |
| `care/emr/models/valueset.py` | Schema-prefixed raw Redis keys |
| `care/emr/utils/file_manager.py` (6 sites), `care/utils/file_uploads/cover_image.py` | Tenant prefix on object keys via a settings hook (default empty = upstream behaviour) |
| `care/facility/models/facility.py`, `care/users/models.py` | Presigned cover and avatar URLs |
| `care/emr/utils/batch_requests.py` | Path allow-list for batch sub-requests |
| `care/utils/csp/config.py` | Separate credentials for the patient and report buckets |
| `care/emr/api/viewsets/organization.py` | `OrganizationPublicViewSet`: government geography only (or done as a plugin override, if CARE's plugin hooks allow) |
| `Pipfile` / `Pipfile.lock` | Add django-tenants 4.0.1 and tenant-schemas-celery 5.0.0 |
| `apps/frontend/src/Providers/AuthUserProvider.tsx` | (Optional) clear caches on logout |
| `Login.tsx` | Keycloak login (P1-T4, not P1-T1) |

**Settings are not patched.** Our settings module (`plugins/ihr_platform/.../settings`) starts with `from config.settings.<env> import *` and is selected with `DJANGO_SETTINGS_MODULE`.

**Report upstream:**
- `Patient.rebuild_organization_cache` stores the wrong id;
- the patient and report bucket clients use the facility bucket's credentials.

## 11. P1-T1 acceptance gates (what "proven" means)

P1-T1 is split into sub-tasks. a → b → c must each pass in CI before the next starts. **d runs after P1-T4** (it needs Keycloak login). **CI tiers** keep minutes in budget:
- **every push:** fast checks plus the isolation suite on 2 tenants;
- **nightly:** the full CARE suite in tenant mode plus mutation runs;
- **release:** the scale run (50/200 schemas) and the restore test.

1. **P1-T1a, library spike — ✅ met 2026-11-06** (CI run 42099914452: 2,013/2,013 tests in tenant mode; tenant creation ~68 s locally; 100 tables / 1,077 relations per tenant; tests run serially for now, about 13 min in CI):
   - django-tenants 4.0.1 + tenant-schemas-celery 5.0.0 installed;
   - our settings module, split apps, `pg_trgm` in public;
   - create two tenants by migrate + seed;
   - **CARE's full suite (2,005 tests) passes inside a tenant schema.** The test runner must give the tenant to every database connection, including threads and parallel workers.
2. **P1-T1b, isolation suite — ✅ met 2026-11-06 locally** (CI run 42174788127: 2,027/2,027 tests in tenant mode incl. 22 isolation tests; 10/10 mutations killed) — see `plugins/ihr_platform/ihr_platform/tests/isolation/` and `scripts/isolation-mutation.sh`; results in TRACKER:
   - structural: public table allow-list, no app in both lists;
   - API crawl of every route with canary strings, as tenant B against tenant A, and anonymously;
   - Redis keyspace audit, Celery, file keys, tokens (Keycloak org, `tid`, `azp`), batch requests;
   - **mutation runs:** remove the cache key function, the `tid` check or the org check, and each run must fail. Built with 10 mutations (cache key function, file and cover-image prefixes, OTP `tid`, both identifier caches, recent-views prefix, tenant Celery app, active-only beat, default-allow permissions); the Keycloak org/`azp` checks join when P1-T4 exists. CI runs them when an isolation control or its tests change, weekly and on demand (not nightly: Actions minutes).
3. **P1-T1c, scale and operations — measured 2026-11-06; cap 100 confirmed by the owner** (`docs/reviews/p1-t1c/scale-results.md`):
   - migration time and catalogue size at 1, 50 and 200 schemas;
   - per-tenant backup/restore round trip;
   - onboarding time.
4. **P1-T1d, Playwright** on two `*.localhost` subdomains: token isolation, logout clean-up, switcher.

## 12. Questions for you (needed before P1-T1)

| # | Question | My recommendation |
|---|---|---|
| **Q-T0** | **Is a tenant one hospital, or one legal entity?** For a 3-branch chain, the entity option is one schema with 3 facilities. | **Legal entity (GSTIN holder).** Billing and GST, shared staff and shared patients sit at that level. Small hospitals are simply an entity with one facility. |
| Q-T1 | Upgrades: a short maintenance window (e.g. ~02:00 IST, announced) acceptable for pilots? | **Yes.** Zero-downtime upgrades with CARE's migrations are expensive; revisit at scale. |
| Q-T2 | A doctor at two hospitals has a **separate staff record in each** (profile edited separately); OK? | **Yes.** It's the safest isolation. One login, one HPR id, separate hospital records. |
| Q-T3 | Facility logos and covers: presigned (private) URLs (as written in §6.8), or public images in a separate bucket? | **Presigned**, as §6.8 assumes. One small patch, no public bucket. |
| Q-T4 | One Keycloak login client **per hospital** (stronger) or one shared client with strict checks? | **One per hospital.** Created automatically at onboarding. |
| Q-T5 | May one designated hospital admin be "superuser" **inside their own hospital only** (CARE needs it to edit hospital-wide forms and templates)? | **No for v1.** Hospital-wide forms come from our packs; hospitals customise at facility level. Revisit with pilot feedback. |
| Q-T6 | Cap of hospitals per database cluster ("cell")? | **Start at 100**, adjust after P1-T1c measurements. **Decided 2026-11-06: 100** (P1-T1c: per-hospital dumps step up between 125 and 150). |
| Q-T7 | Platform support access to patient data: break-glass only (hospital admin approves, time-limited, logged)? | **Yes**, break-glass only. |
| Q-T8 | Drop inter-hospital referral and transfer (CARE's cross-facility resource requests) for v1, redesign later on ABDM? | **Yes.** It still works between facilities of the same tenant. |
| Q-T9 | Include the patch that clears cached data at logout (shared hospital computers)? | **Yes.** |
| Q-T10 | Reference-pack updates (drug list, prices, forms): automatic for unedited rows, or only when each hospital opts in? | **Automatic for unedited rows**, with a visible change list; edited rows need the admin's OK. |
| Q-T11 | Domains: `<hospital>.app.<domain>`, `auth.app.<domain>` (Keycloak), `abdm.app.<domain>`, `console.app.<domain>`. Which `<domain>`? | Use a domain you own; this can wait until P1-T8 (staging). |

## 13. Review trail

| Stage | Result |
|---|---|
| Mapping | 100 CARE models classified (47 model-level risks); 28 runtime, 9 frontend and 15 library findings |
| Designs | "all-in-tenant" 123.5 · "risk-first" 120 (two name variants summed) · "shared identity" 84 |
| Verification | 5/5 *sound with fixes*. **5 fatal flaws, all fixed here:**<br>• template-clone duplicate ids → §7.1 and §6.3<br>• batch-request bypass → §6.5<br>• mixed public/tenant apps → §3<br>• migration availability → §7.2<br>• template fakes migrations → §7.1<br>A completeness check then found 24 of 48 review items missing or misstated, plus 7 contradictions; **all fixed in v2**. |
| Library facts to re-check at install | django-tenants 4.0.1 (2026-10-23), tenant-schemas-celery 5.0.0 (2026-08-01), Keycloak 26.8.2 |
