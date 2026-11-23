# P1-T31 — Hospital switcher: design

Status: final after attack (docs/reviews/p1-t31/attack.md, Orchestrator decision)

Design for the unattended run of 2026-11-23 (no code yet). Finding ids in brackets (S1, D2, …) are from the attack; where the attack's Orchestrator decision and this file differ, the decision wins. Decisions Q-K144…Q-K149 were
proposed here; the run logs them in `docs/QUESTIONS.md` as "awaiting owner confirmation".
Unblocks `apps/frontend/tests-ihr/switcher.spec.ts` (P1-T1d, Phase 1 review R1-05).

## 0. Goal and what exists today

A staff member linked to more than one hospital sees a **"Hospitals"** group in the web app's
user menu. It lists their hospitals (the current one marked). Choosing another goes, top-level and
in the same tab, to that hospital's own address, where Keycloak single sign-on signs them in
(ADR-008's per-hospital gate flow and MFA ACR still decide; each origin keeps its own HttpOnly
cookie, ADR-009). Someone linked to one hospital sees nothing new.

Checked in the code on 2026-11-23 (a builder re-checks each line before relying on it):

| # | Fact | Where |
|---|---|---|
| F1 | `StaffDirectoryEntry` (TENANCY §3, §4.4) does **not** exist; no model, table or reference in code. | `grep -rn StaffDirectoryEntry plugins apps` → only docs |
| F2 | `StaffIdentity(user OneToOne, keycloak_sub unique, sessions_valid_after, awaiting_setup, join_confirmation_required)` lives in each hospital schema. An invited or awaiting-setup person's CARE user is **inactive** until accepted. | `plugins/ihr_platform/ihr_identity/models.py:7` |
| F3 | `end_sessions_of_disabled_users` updates `StaffIdentity` with `queryset.update()` (no model signals fire); CARE re-saves `users_user.cached_role_orgs` on every membership change. | `ihr_identity/signals.py` |
| F4 | `Hospital.Status`: provisioning, active, suspended, offboarding, archived. `fleet.MIGRATE_STATUSES = (PROVISIONING, ACTIVE, SUSPENDED, OFFBOARDING)`. | `ihr_platform/models.py:40`, `fleet.py:89` |
| F5 | `release.servable(hospital)` = ACTIVE and `schema_ready()`; `schema_ready` reads only public tables (`HospitalSchemaState`: release hold `upgrading_since`, restore hold `restore_operation`, fingerprint) plus `db_roles.gated_schemas()`. Safe to call for another hospital at request time, also after P4-T8. | `ihr_platform/release.py:383-413` |
| F6 | Hospital addresses: `Domain` (django-tenants, `is_primary` set by `ihr_create_tenant` and provisioning); the browser origin of a domain is `settings.IHR_FRONTEND_URL_TEMPLATE.format(domain=…)` (dev `http://{domain}:8080`, deployment `https://{domain}`, `secure.py` requires https). | `ihr_identity/views.py:54` `_redirect_uri`, `settings/local.py:11` |
| F7 | Sign-in start: `GET /api/ihr/auth/login/` (`views.login`, `_safe_next` keeps only same-host paths). SSO at a second hospital is proven by `signInBySso()` in `tests-ihr/lib/fixtures.ts:161` (no password or code asked). | `ihr_identity/urls.py`, fixtures |
| F8 | `NotCrossSite` (DRF permission; refuses `Sec-Fetch-Site` cross-site/same-site) exists and guards the audit routes. | `ihr_identity/staff_access.py:118` |
| F9 | User menu: `FacilityNavUser` in `apps/frontend/src/components/ui/sidebar/nav-user.tsx:33` (staff; `PatientNavUser` is the patient portal's). CARE's plugin hook `userNavItems` only navigates **inside** the app (`/facility/<id>/users/<username>/<url>`), so it cannot open another origin. | nav-user.tsx |
| F10 | TanStack queries are persisted to localStorage only with `meta.persist === true`. | `apps/frontend/src/Utils/request/queryClient.ts:42` |
| F11 | Seed: `dr.meera` is `care-doctor` in **alpha and beta**; so are the eight restricted-role logins (`reception.ravi` …); `nurse.asha`, `admin.priya`, `admin.anil`, `new.neha` are alpha only, `admin.bhavesh` beta only. | `ihr_platform/management/commands/ihr_keycloak_demo_users.py` `DEMO_STAFF` |
| F12 | Precedent for owner-owned `SECURITY DEFINER` functions per hospital schema, pinned `search_path`, kept current by `post_migrate` with a stamp: `ihr_audit/seal.py` (`ensure_sealing`), `db_functions.py`; triggers we own on CARE tables: `ihr_access/placement.py`. | |
| F13 | Fleet step `root` runs at start-up, in releases and after a single-hospital restore's migrate (`fleet.py:1188`, `restore/engine.py:1154` `_migrate` → `_root`). | |

## 1. Shape (final)

```
hospital schema h_x                         public
 users_user ─┐  AFTER UPDATE OF is_active,   ihr_platform_staffdirectoryentry
 ihr_identity_staffidentity ─┘ deleted,…  ──►  (keycloak_sub, hospital_id)   class `select` for ihr_app
   trigger ihr_directory_touch()               ▲ first fill: ihr_identity 0004 migration;
   (SECURITY DEFINER, owner)                   │ repair: ihr_staff_directory --check|--repair

 GET /api/ihr/my-hospitals/  (hospital address, staff cookie only)
   → rows for request's own sub only, ACTIVE non-public hospitals, joined with Domain + servable_many()
   → {"hospitals":[{name,url,current,available}]}
                                               │
 web app: <IhrHospitalsMenu/> in FacilityNavUser (one marked tag + import)
   → window.location.assign(url)  = https://<slug>.<suffix>/api/ihr/auth/login/
   → Keycloak SSO (gate flow, ACR) → callback → that origin's own cookie
```

Cut (attack D2): the fleet step `directory`, `rebuild_gone`, the `since` column and the extra index.

## 2. Decisions (options, recommendation, reason)

### Q-K144 — Where does the list come from?

- **A. Public directory table kept by per-hospital triggers (taken; recommended).**
  `ihr_platform.StaffDirectoryEntry` in `public`; one row per *listed* link (no row = not
  listed). Each hospital schema gets an owner-owned `SECURITY DEFINER` trigger function that, for
  the affected `keycloak_sub`, **recomputes** from that schema's own rows whether the link is
  listed and upserts or deletes the one `(sub, hospital)` row. The hospital is found from
  `TG_TABLE_SCHEMA` → `ihr_platform_hospital.schema_name` (never from row values), so a schema can
  only ever write rows for its own hospital [D9]. The first fill is the `ihr_identity` 0004
  migration; repair is `ihr_staff_directory --check|--repair` (no fleet step) [D2].
  *Why:* a trigger is needed **today**: `queryset.update(is_active=…)` at `ihr_identity/views.py:269`
  (accepting an invitation), `join.py:217` (Join) and `invitations.py:68-69` (deactivating) bypass
  signals, and the app gets no write grant on the table [D2]. It also catches ORM, raw SQL, CARE
  bulk changes and provisioning as owner; the web app reads only `public` at request time, so
  P4-T8's per-hospital DB roles do not break it.
  **Sub-option (taken): raw sub now** — no new exposure under the shared `ihr_app` (it already
  reads every hospital schema); a hospital-bound read or keyed hash is decided in P4-T8 (§7) [S5].
- B. Same table, kept by Django `post_save`/`post_delete` signals plus a reconcile. Misses
  `queryset.update()` (our own code uses it, above), raw SQL and CARE bulk paths, and the app role
  would need `dml` on a public table, which under P4-T8 lets one hospital's role write any
  hospital's rows.
- C. Scan every hospital schema per request. Rejected: N queries per menu open, reads other
  hospitals' schemas at request time (breaks at P4-T8 by design), and a held hospital's schema may
  be mid-restore.
- D. Ask Keycloak (organisation memberships) through a worker. Rejected: the web backend holds no
  admin credential (ADR-008/P1-T5f), Keycloak membership exists before acceptance (P1-T5e1) and so
  does not mean "can work here", and a Keycloak outage empties the menu.
- E. A token claim listing hospitals. Rejected: reverses ADR-008 (hospital clients refuse the
  `organization` scope precisely so a token never lists other hospitals).

### Q-K145 — Who and which links are listed?

A link `(sub, hospital)` is **listed** when, in that hospital's schema, a `StaffIdentity` with
that `keycloak_sub` points to a `users_user` row with `is_active = true`, `deleted = false`,
`is_superuser = false`, `is_service_account = false`. Consequences (each a test in §5):

- invited-not-accepted, awaiting setup (`awaiting_setup`), waiting for "Join"
  (`join_confirmation_required`): the user is inactive (F2) → **not listed**;
- deactivated or soft-deleted: **removed at once** (same transaction as the deactivation);
- unlinked (`StaffIdentity` deleted, or the user hard-deleted → cascade): removed;
- a person with no role or organisation yet but active: listed (they can sign in there);
- superusers are **always** left out (the production rule, `authentication.py:91`; the trigger
  cannot read `IHR_STAFF_REFUSE_SUPERUSERS`) [R9].

Options: **A (taken; recommended) the rule above**; B also list invited/pending links as "Invitation
waiting" (rejected: an invitation is accepted by the e-mailed link, not here, and listing it
would tell a person a hospital invited them before they chose to accept).

### Q-K146 — Which hospitals appear, and how does a held one look?

- **A (taken; recommended):** only `status = ACTIVE` hospitals with a `keycloak_org_id` and a
  primary `Domain`, **never `public`** [S8] (`ihr_create_tenant --public` makes an ACTIVE
  `Hospital(schema_name="public")`; `schema_name <> 'public'` is in the trigger, `rebuild`,
  `--repair` and the endpoint query, and the public tenant is a test 8 case). An ACTIVE hospital
  that is not `servable()` (release hold, restore hold, other build, gated schema; F5) is listed
  with `available: false` and shown **disabled** with "Not available right now". Suspended,
  offboarding, archived and provisioning hospitals are **not listed**.
  *Why:* a release or restore window is short and the person knows they work there, so hiding it
  would look like lost access; a suspended or leaving hospital refuses sign-in anyway.
- B: hide every non-servable hospital (simpler; confusing during release windows).
- C: list suspended ones as disabled too (that is the hospital's message to give, not ours).

The current hospital always appears (marked `current: true`, not clickable), taken from
`connection.tenant`, even if its directory row is missing (self-healing view; `--repair` adds the
row). The menu group shows only when the list has **two or more** entries.

### Q-K147 — What does the endpoint reveal, and to whom?

`GET /api/ihr/my-hospitals/` on a hospital address. **Staff only** [S6]:
`authentication_classes = [StaffSessionAuthentication]` (as `SessionView`, `views.py:303`; DRF
`ServiceTokenAuthentication` is among the defaults, `settings/tenancy.py:164-167`, so it must not
apply), plus a permission that refuses `user.is_service_account`. Permissions in this order:
`NotCrossSite`, `IsAuthenticated`, the service-account refusal. Patient OTP, CARE JWT and
service-account tokens are refused. A user with no `ihr_identity` gets **403**, not a list.
Exact codes are **403 everywhere** [D14]: `StaffSessionAuthentication.authenticate_header` returns
`""` (`authentication.py:132`), so anonymous `same-origin` gives 403 with the not-signed-in body,
and cross-site gives 403 "Cross-site request refused."; there is no 401. Answer:

```json
{"hospitals": [
  {"name": "Alpha General Hospital", "url": "http://alpha.localhost:8080/api/ihr/auth/login/",
   "current": true, "available": true},
  {"name": "Beta Care Hospital", "url": "http://beta.localhost:8080/api/ihr/auth/login/",
   "current": false, "available": true}
]}
```

- The `sub` is **always** the signed-in person's (`request.user.ihr_identity.keycloak_sub`);
  the view reads **no parameters** (query parameters are ignored; no 400) [D13]. Never a `sub`,
  slug list, other person, hospital id, schema name, status or count of other people.
- `name` = `Hospital.name` (Q-K148). `url` is built server-side only by the new
  `links.origin_for(domain)` (which `frontend_base_url` also uses) + `/api/ihr/auth/login/`, with
  **no** `?next` (`_safe_next` defaults to `/`) [D10, Z6]. One batched primary-domain query; a
  hospital with no primary domain is left out.
- Order: current first, then by `name` (case-insensitive). **No cap, no log line** [D13].
- No server cache (Q-K149). `Cache-Control: no-store` (`never_cache`).
- **Not audited**: no patient data and no change, so it is outside the `request` entry rule of
  ADR-010; the switch itself is a sign-in at the target, already recorded there. A test pins "no
  audit entry, no Redis key" for this route.
- **Cross-site guard: yes** (`NotCrossSite` first); not a decision [Z7].

### Q-K148 — Which name is shown? (follows from an existing pattern)

- **A (taken): `Hospital.name`** from `public` (set by the operator at provisioning). Reading
  another hospital's profile (`ihr_tenant_settings`, per schema) at request time would read
  another schema (rejected, P4-T8).
- B: mirror the profile's display name into `public` with another definer trigger. Later, if
  owners rename hospitals in their profile and the menu looks wrong; not now.

### Q-K149 — Caching and the tenant key (follows from an existing pattern)

- **A (taken): no server-side cache** and a **constant query count** independent of the number of
  hospitals [D3]: `release.servable_many(hospitals)` does one `HospitalSchemaState` read with
  `pk__in`, one `gated_schemas()` and one `code_fingerprint()` (`schema_ready` otherwise reads
  per hospital, `release.py:390-406`); pinned with `assertNumQueries` for 1 and 3 hospitals. The
  web app caches with TanStack Query (`queryKey: ["ihr", "my-hospitals", username]` [D12],
  `staleTime` 5 min, `retry: false`, **no** `meta.persist`, F10). Nothing to invalidate.
- B: Redis cache under `<schema>:ihr:directory:<user pk>` (60 s TTL). Rejected: saves one cheap
  query, and a deactivated person's other hospitals stay visible for the TTL.

### Not a decision (follows from the above)

- **Navigation:** `window.location.assign(url)` in the same tab; no `window.open`, no
  `postMessage`, nothing written to storage first. The target's `/api/ihr/auth/login/` starts the
  normal OIDC round trip; Keycloak's per-hospital gate flow refuses a non-member (the existing
  "You are not a member of this hospital." page) and enforces the MFA ACR (ADR-008). An expired
  Keycloak SSO session shows the normal login at the target and still lands on that hospital
  [D11]. The origin's own cookie is set by its callback (ADR-009).
- **Defence in depth in the browser [D4, D10]:** `Utils/ihr/hospitals.ts` accepts an entry only if
  its scheme is the current page's scheme, it has no credentials, its pathname is exactly
  `/api/ihr/auth/login/`, and it has no query or fragment. (The earlier sibling-host rule is cut:
  it dropped custom domains and still accepted any path on a sibling such as `auth.<suffix>`.)
- **Sign-out:** unchanged; Keycloak back-channel logout (P1-T5d) already ends every hospital the
  sign-in touched.

## 3. Backend build (files and symbols)

### 3.1 Model and table (`ihr_platform`, shared app)

`plugins/ihr_platform/ihr_platform/models.py`:

```python
class StaffDirectoryEntry(models.Model):
    """Which hospitals a Keycloak person may switch to (TENANCY §4.4, P1-T31). Written only by
    the per-hospital trigger ihr_directory_touch() and the repair command (owner); read-only for
    the app. No names or contacts: a sub and a hospital."""
    keycloak_sub = models.UUIDField()
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="+")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["keycloak_sub", "hospital"],
                                               name="ihr_directory_one_per_hospital")]
```

- No `since` column and no extra index: the unique constraint leads with `keycloak_sub` [D8].
- Migration `ihr_platform` **0013** (the new table; confirm the next free number) [R13].
- `ihr_platform/public_tables.txt`: `ihr_platform_staffdirectoryentry select written only by the
  per-hospital trigger ihr_directory_touch() (owner, SECURITY DEFINER) and the repair command
  (owner); the app reads its own person's rows (P1-T31)`.
- `Hospital` delete cascades (only owner-run rollback deletes hospitals).

### 3.2 Trigger (new module `plugins/ihr_platform/ihr_identity/directory.py`)

State the invariants; the builder writes the plpgsql [Z10]. Keep the exact trigger names and
`WHEN` clause: the checks and the dump allow-list depend on them.

- Function `{schema}.ihr_directory_touch()` `RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
  SET search_path = pg_catalog, pg_temp`, owned by the database owner, `REVOKE ALL … FROM PUBLIC`.
  Invariants:
  1. `hid` = the `ihr_platform_hospital.id` whose `schema_name` equals `TG_TABLE_SCHEMA`, and
     `schema_name <> 'public'` [S8]; NULL (a parked schema after a restore, a restore's staging
     schema, `public`) → return.
  2. Affected subs: for `ihr_identity_staffidentity` OLD/NEW `keycloak_sub`; for `users_user` the
     sub linked to OLD/NEW `id` (looked up in the same schema, qualified with
     `format('%I', TG_TABLE_SCHEMA)`).
  3. Per sub, take `pg_advisory_xact_lock(hid, hashtext(sub::text))` [S9], then recompute `listed`
     = a `StaffIdentity` with that sub links to a `users_user` that is `is_active`, not `deleted`,
     not `is_superuser`, not `is_service_account`; listed → `INSERT … ON CONFLICT DO NOTHING`;
     else `DELETE` that `(sub, hid)` row.
  4. `TRUNCATE` (statement trigger) → delete all rows of `hid`.
- Triggers (names fixed):
  - `ihr_directory_link` `AFTER INSERT OR DELETE OR UPDATE OF user_id, keycloak_sub ON
    ihr_identity_staffidentity FOR EACH ROW`;
  - `ihr_directory_user` `AFTER UPDATE OF is_active, deleted, is_superuser, is_service_account ON
    users_user FOR EACH ROW WHEN (OLD.is_active IS DISTINCT FROM NEW.is_active OR OLD.deleted IS
    DISTINCT FROM NEW.deleted OR OLD.is_superuser IS DISTINCT FROM NEW.is_superuser OR
    OLD.is_service_account IS DISTINCT FROM NEW.is_service_account)` (so CARE's
    `cached_role_orgs` re-saves cost nothing, F3);
  - `ihr_directory_truncate` `AFTER TRUNCATE ON ihr_identity_staffidentity FOR EACH STATEMENT`.
  - User hard delete cascades to `StaffIdentity` → covered by `ihr_directory_link`.
- **Install and first fill [D2, D7]:** the `ihr_identity` **0004** migration installs the function
  and triggers (SQL frozen in the migration, sha256 stated like `ihr_refdata` 0004) and runs
  `rebuild()` for its own schema, as owner. It depends on CARE's latest `users` migration and
  `ihr_platform` 0013. Each hospital fills when it migrates; a new hospital fills as users are
  created. `ensure_directory(conn, schema)` runs on every hospital schema's `post_migrate`, using
  `ihr_platform.db_functions` (`held`, `stamp`, `unchanged`, `write_stamp`) as
  `ihr_audit.seal.ensure_sealing` does (F12), and **installs the function and triggers only**: it
  writes no public rows, because the restore's `post_migrate` is the abortable `migrate` step
  (D6). The SQL is canonical; 0004 states its sha256.
- **db_roles [S1]:** add `"ihr_directory_touch": set()` to `db_roles.FUNCTIONS` and the name to
  `SECURITY_DEFINER` (`db_roles.py:167-172`), so `_function_acl` checks owner = `ihr_owner`, no app
  EXECUTE and the pinned `search_path`. Otherwise `schema_findings` (`db_roles.py:781-790`) turns
  the definer into a problem and the restore smoke step fails (`restore/engine.py:1827-1834`).
  Test: the function made INVOKER or granted to `ihr_app` gives a problem.
- **Restore quarantine allow-lists [S2]:** add the function to `restore/package.py` `OUR_FUNCTIONS`
  and `ihr_directory_link`, `ihr_directory_user`, `ihr_directory_truncate` → `ihr_directory_touch`
  to `OUR_TRIGGERS` (`package.py:50-74`; else every dump after P1-T31 is refused,
  `package.py:256`, `:270`). Never export them: the restore's migrate reinstalls them through
  `ensure_directory`. Add a case to `scripts/test_restore_quarantine.py` and to the live test.
- **System checks [D1]** (E026/W026 pattern, static only; they read no hospital schema):
  - `ihr_identity.E027` (next free number; `grep -rn "E02[0-9]" plugins/ihr_platform`):
    `ensure_directory` is wired to `post_migrate`.
  - `ihr_identity.W027`: the SQL fingerprint differs from the one the newest `ihr_identity`
    migration states.
  Per-schema trigger presence, enabled state and the function stamp are reported by
  `ihr_staff_directory --check` as the log line `ihr-directory: hospital=<slug> outcome=drift`.
  Drift never gates a hospital and never refuses the process.
- **Tests added [D7]:** a freshly created tenant has the three triggers before any user write; a
  static check that the columns named in the `UPDATE OF` list exist on CARE's `User` model
  (`is_active`, `deleted`, `is_superuser`, `is_service_account`, `care/users/models.py:167,170`),
  so an upstream rename fails the tests.
- Audit: the triggers do not touch hospital tables, so the audit triggers see nothing new. The
  public table is not audited. Check `ihr_audit` `install.py` still passes.
- **Rollback [S4]:** rollback across P1-T31 is refused, "fix forward" (a `release_log.py` marker
  and a test), as P1-T19b and P1-T20b did: an older build's `db_roles` reports the unknown definer
  and its `OUR_FUNCTIONS` refuses every newer dump.

### 3.3 Repair and restore (no fleet step)

- `ihr_identity/directory.py: rebuild(hospital)`: as the owner, in one transaction, take the same
  per-sub advisory locks [S9], compute the listed subs of that schema (same predicate as §3.2 step
  3, excluding `public`) and make the hospital's rows equal to it: a set difference of
  `(sub, hospital)` pairs [D8].
- Command `manage.py ihr_staff_directory --check|--repair [--slug S]` (owner; staging via
  `scripts/staging.sh manage … --operator X --reason …`):
  - `--check` reports row differences and trigger/stamp drift (log line above), exits 1 on any;
  - `--repair` re-runs `ensure_directory` and `rebuild`, and deletes rows of hospitals outside
    `MIGRATE_STATUSES`.
  - `--check` is added to the weekly `staging.sh due` list [S9]: the backstop for concurrent
    recomputes.
- **Restore [D6]:** order: load (no directory triggers; they are never exported) → rename →
  migrate (`ensure_directory` installs) → reconcile (`rebuild`). Call `rebuild(hospital)` in
  `_reconcile` (after the point of no return, next to `_public_rows()`, `engine.py:1201`) and in
  `abort()` after un-parking, because the triggers installed by the restore's migrate may already
  have fired. **Never in `_migrate`** (`ABORTABLE` includes `migrate`, `engine.py:79`; an abort
  would discard the copy the rows came from). A failed rebuild in reconcile is logged and does not
  fail the restore; `--repair` is the repair path. Test both in `test_restore`.

### 3.4 Endpoint

- `ihr_identity/directory.py: MyHospitalsView(APIView)`, `authentication_classes =
  [StaffSessionAuthentication]`, `permission_classes = (NotCrossSite, IsAuthenticated,
  <refuses service accounts>)` [S6], `http_method_names = ["get", "head", "options"]`,
  `never_cache`. `hospitals_for(request) -> list[dict]` is the one function that reads the table.
  It uses `release.servable_many` [D3] and `links.origin_for` [Z6].
- Route: `plugins/ihr_platform/ihr_platform/urls_tenant.py` `path("api/ihr/my-hospitals/",
  MyHospitalsView.as_view(), name="ihr-my-hospitals")` before the `api/ihr/` includes.
- A person with no `ihr_identity` → 403 (Q-K147).

## 4. Web app build (smallest upstream patch)

Ours (new files):

- `apps/frontend/src/Utils/ihr/hospitals.ts` + `hospitals.test.ts`: types, `switcherEntries(
  answer, currentOrigin)` (the client rule of §2 "Defence in depth", order, `null` when fewer than
  two), unit tests with one case per rule (`javascript:`, credentials, other path, query, http on
  an https page) [D4].
- `Utils/ihr/ihrApi.ts` (exists; **add** `ihrHospitalsApi.list`, `GET /api/ihr/my-hospitals/`) [R13].
- `apps/frontend/src/components/ihr/IhrHospitalsMenu.tsx`: `useQuery` (Q-K149; queryKey
  `["ihr", "my-hospitals", username]` [D12]) called as `query(ihrHospitalsApi.list, { silent: true })`
  [R7] (the global `handleHttpError`, `queryClient.ts:30`, would otherwise toast or redirect). On
  any error, while loading, or with fewer than two entries it renders nothing; the existing
  session-expiry flow handles signed-out pages. Otherwise `<DropdownMenuSeparator/>` +
  `<DropdownMenuGroup>` with a `DropdownMenuLabel` "Hospitals" and one `DropdownMenuItem` per
  entry (lucide `Building2`; the current one disabled with a check mark and "Current"; an
  unavailable one disabled with "Not available right now"); `onSelect` →
  `window.location.assign(entry.url)`. Use `ihr_hospitals_switch_aria` for each item's aria label,
  or drop the string [Z10]. `data-testid="ihr-hospitals-menu"` and per-item
  `data-ihr-hospital="current|other|unavailable"` for Playwright. A component or unit test covers
  the error case (nothing rendered).

Upstream patch (one, logged in `docs/UPSTREAM_PATCHES.md`, marked `IndieHR P1-T31`):

- `apps/frontend/src/components/ui/sidebar/nav-user.tsx`, `FacilityNavUser` only: one marked
  import `import { IhrHospitalsMenu } from "@/components/ihr/IhrHospitalsMenu";` and one marked
  tag `<IhrHospitalsMenu />` directly **before** the `<DropdownMenuSeparator />` that precedes the
  logout item. `PatientNavUser` untouched.
- `scripts/check-ihr-frontend.py`: **new** constants `NAV_USER` (path) and `SWITCHER_ANCHORS =
  [(NAV_USER, r"<IhrHospitalsMenu\b", "the Hospitals group in the user menu"), (NAV_USER,
  r"import \{ IhrHospitalsMenu \}", "its import")]`, looped over like `STAFF_ANCHORS`
  (`check-ihr-frontend.py:203`, `:382`) [R13]; `scripts/test_check_ihr_frontend.py` gets the
  missing-anchor case.

Strings (`deploy/locale/en.json`, `deploy/locale/hi.json`; never `apps/frontend/public/locale/`):
`ihr_hospitals_menu` "Hospitals", `ihr_hospitals_current` "Current",
`ihr_hospitals_unavailable` "Not available right now", `ihr_hospitals_switch_aria` "Switch to
{{name}}". Hindi drafts go to a new `docs/i18n/review/hi-batch-7.csv` (awaiting owner), glossary
`docs/i18n/hi-glossary.md` checked for "hospital". `scripts/merge-locale.py --check` must pass.

No new storage key. No CSP change. Bundle egress check unaffected.

## 5. Test plan

Backend (tenant mode; labels `ihr_platform ihr_identity ihr_access ihr_audit`; tests run as
`ihr_app` with `IHR_DB_ROLES=test`, owner work inside `as_owner()`):

`ihr_identity/tests/test_directory.py` (two tenants from `TenantTestRunner`; `staff_client()`,
`fake_keycloak()`):

1. Link created (active user) → one row; second hospital link with the same sub → two rows.
2. `is_active=False` via `save()` **and** via `queryset.update()` → row gone in the same
   transaction; back to active → row back; `deleted=True` → gone; `is_superuser=True` → gone. It
   calls the **real** accept (`views.py:269`), join (`join.py:217`) and invitation
   (`invitations.py:68`) functions [D2].
3. `StaffIdentity` deleted; user hard-deleted (cascade); `TRUNCATE` → rows gone.
4. `awaiting_setup` / `join_confirmation_required` invitation states (inactive user) → no row.
5. `cached_role_orgs` update → trigger function not entered: as owner set `track_functions = 'pl'`
   locally, update `cached_role_orgs`, and assert `pg_stat_user_functions.calls` for
   `ihr_directory_touch` is unchanged [D5].
6. S is linked only in B and activated in B → row (S, B) exists and no (S, A); a write in A for
   another sub leaves (S, B) untouched [D5].
7. Endpoint: dr-like user in both → `current` first, other second, `url` = template + primary
   domain + `/api/ihr/auth/login/`; user in one hospital → one entry; query parameters ignored;
   exact status per credential type (all 403 [D14]: anonymous, cross-site, CARE JWT, patient OTP
   token, service-account token, user with no identity) [S6]; response contains no `sub`, slug
   list, schema name or other person's data (key set pinned).
8. Status filter: other hospital `suspended`, `offboarding`, `archived`, `provisioning`, no
   `keycloak_org_id`, no primary domain, and the `public` tenant [S8] → not listed; release hold
   and restore hold (`upgrading_since`, `restore_operation` set) → listed with `available: false`.
9. `Sec-Fetch-Site: cross-site` / `same-site` → 403; `same-origin`, `none`, absent → 200.
10. No audit entry written for the GET, no Redis key created; query count constant
    (`assertNumQueries` for 1 and 3 hospitals) [D3].
11. `rebuild()`: a missing row added, an extra row (inserted as owner) removed, rows of a hospital
    outside `MIGRATE_STATUSES` removed by `--repair`; `ihr_staff_directory --check` exits 1 then 0
    after `--repair`.
12. `ensure_directory`: function dropped / made `SECURITY INVOKER` / body edited → the next
    `post_migrate` restores it; E027 static; a fresh tenant has the triggers before any user
    write; the `UPDATE OF` columns exist on CARE's `User` [D7].

Elsewhere:

- `ihr_platform/tests/test_restore.py`: after a single-hospital restore whose dump lacks a link
  the live schema had, that row is gone (via `_reconcile`); after `abort()` the rows are rebuilt;
  the parked schema's trigger is a no-op [D6].
- `ihr_platform/tests/test_db_roles.py`: the new public table is `select` for `ihr_app`; an INSERT
  as `ihr_app` is refused; the function INVOKER or granted → problem [S1].
- `scripts/test_restore_quarantine.py` and the live test: the function and triggers are accepted
  [S2].
- **Isolation suite** (`ihr_platform.tests.isolation`): the crawl reaches
  `/api/ihr/my-hospitals/` (authenticated, not in `anonymous_allowlist.txt`); add
  `test_directory_lists_another_hospital_only_to_its_own_member` (a tenant-B-only user signed in at
  A sees only A's name), with one person linked in both world tenants, and assert B's **name, slug
  and domain** text are absent for an A-only person [S11] (the crawl's leak check does not cover
  them, `world.py:82`, `:130-132`). If the crawl's marker check flags B's `Hospital.name` for a
  person linked in both, add one reviewed exemption line naming this route and that test.
- **Role matrix** (`ihr_access/tests/`): `role_matrix.txt` rows for `GET /api/ihr/my-hospitals/`
  (200 for every role incl. moved Nurse, Doctor, Facility Admin, Radiologist, `mrdg`) and the
  non-GET methods (405); `role_matrix_keys.txt` keys `hospitals`, `hospitals[].name`, `.url`,
  `.current`, `.available`; `list_params.txt` nothing; `role_matrix_writes.txt` as the matrix asks.
- **Mutation rows** (`scripts/isolation-mutation.sh`, each must make its named test FAIL):
  1. `directory-user-trigger`: drop the `is_active` condition from the listed predicate → test 2;
  2. `directory-own-sub`: `hospitals_for` returns all rows (not only the caller's sub) → test 7;
  3. `directory-status`: drop the `status = ACTIVE` filter → test 8;
  4. `directory-cross-site`: remove `NotCrossSite` from `permission_classes` → test 9;
  5. `directory-schema-bound`: the function resolves `hid` as the first ACTIVE hospital by id
     instead of from `TG_TABLE_SCHEMA` → test 6 [D5];
  6. `directory-service-token`: drop the `StaffSessionAuthentication`/service-account refusal →
     test 7 [S6].
- Web unit: `hospitals.test.ts` (fewer than two → null; one case per client rule, §4; order;
  current/unavailable flags); the menu error case (nothing rendered, no toast) [R7].
- `python3 -m unittest scripts/test_check_ihr_frontend.py`; `scripts/merge-locale.py --check`;
  `pre-commit run --all-files`.

**Playwright** (`apps/frontend/tests-ihr/switcher.spec.ts`, un-skipped; CI job "Browser isolation
(Playwright)" with real Keycloak on `alpha.localhost:8080` / `beta.localhost:8080`) [R8]:

1. `dr.meera` `signIn(alpha)`; open the user menu; the `ihr-hospitals-menu` group lists two
   entries: alpha `current` (disabled), beta clickable; names compared with
   `apiJson("/api/ihr/my-hospitals/")`, not hard-coded.
2. Click beta, reusing the `signInBySso()` mechanism (`tests-ihr/lib/fixtures.ts:161`): record each
   navigation with `page.on("framenavigated")` and assert that none lands on Keycloak's login form
   (`#username`, `#password`, `#otp` never show) → `expectSignedIn` on beta.
3. Cookies: `ihr_session` exists host-only on both origins with different values (reuse
   `sessionCookieForComparison`); beta's storage snapshot holds no alpha-origin key
   (`snapshot()`).
4. Switch back to alpha from beta's menu → signed in at alpha, no credentials asked.
5. `nurse.asha` (alpha only): user menu has no `ihr-hospitals-menu`.
6. Sign out at beta → `expect.poll(…, { timeout: 10_000 })` for alpha's next
   `/api/ihr/my-hospitals/` answering **403** with the not-signed-in body (single sign-out, citing
   the P1-T5d back-channel test) [D14], and alpha's tab keeps nothing (`leftovers()`).

Add one row to `roles-menus.spec.ts`: `reception.ravi` (a restricted role in both hospitals)
sees the group (the switcher is identity, not role).

## 6. Split (final; about 11.5 agent hours, in order)

| Sub | Content | Builder | Est. |
|---|---|---|---|
| T31a | Table and 0013; `public_tables.txt`; `ensure_directory`, the trigger SQL, advisory locks; `ihr_identity` 0004 (install and first fill); E027/W027; db_roles `FUNCTIONS`/`SECURITY_DEFINER`; `OUR_FUNCTIONS`/`OUR_TRIGGERS`; tests 1–6 and 12, db_roles and quarantine cases | Opus high | 3 h |
| T31b | `rebuild`; `ihr_staff_directory --check\|--repair`; the drift log line and `staging.sh due` entry; restore `_reconcile` and `abort()` hooks; the rollback-refused marker; test 11 and restore tests | Opus high | 1.5 h |
| T31c | Endpoint (auth class, service-account refusal), `servable_many`, `links.origin_for`, route; tests 7–10; isolation test; role-matrix rows; mutation rows 1–5 (5 redefined) and `directory-service-token` | Opus high | 2 h |
| T31d | Web: `hospitals.ts` (new rule) and its tests, `ihrApi` addition, `IhrHospitalsMenu` (`silent`, user-scoped key), nav-user patch, `UPSTREAM_PATCHES`, anchors, en/hi strings and review CSV | Sonnet high | 1.5 h |
| T31e | Playwright `switcher.spec.ts` (R8 mechanisms) and the `roles-menus` row; CI; docs (TENANCY §4.4, CLAUDE.md, TRACKER, P4-T8 row note) | Sonnet high | 1.5 h |
| Review | Full review shape (tenancy, access) | Opus / Sonnet | 2 h |

The CI Playwright run is the only proof of SSO across origins (the web app is not built locally).

## 7. Not done (and why)

- No P4-T8 per-hospital role work now. **Add to P4-T8's tracker row** (replacing the earlier note
  that did not work: inside a SECURITY DEFINER function `current_user` is the owner, so "the calling
  role's own hospital" cannot be decided from it) [S5]: the directory needs a hospital-bound read,
  either a definer read keyed on `session_user` (if P4-T8 uses per-hospital logins) or a keyed hash
  of the sub (`HMAC(key, sub)`, key in an owner-only public table and a web secret); correlation
  across hospitals stays a residual risk. Today the shared `ihr_app` reads every hospital schema
  anyway, so nothing new is exposed.
- A hospital role that can write `StaffIdentity.keycloak_sub` (today `ihr_app`) can add its own
  hospital to a victim's menu; the click ends at Keycloak's gate [D9]. The trigger writes only rows
  for its own hospital.
- No Keycloak membership check: a person active in CARE but removed from the Keycloak
  organisation stays listed until CARE deactivates them; clicking ends at Keycloak's gate ("You
  are not a member of this hospital."). Keycloak stays the authority for sign-in.
- No profile display name or logo in the menu (Q-K148 B later).
- No switching between facilities inside one hospital (CARE's own facility switcher does that);
  "legal entity" tenants (Q-T0) are out of scope.
- No audit entry or log line per read (Q-K147).
- No nightly reconcile beat, no fleet step, no drift gate: the triggers are the live path;
  `ihr_staff_directory --check|--repair` (weekly `--check` in `staging.sh due`) is the repair path.
  A stale entry only shows a hospital the person may not enter, never another person's data.
- No mobile-app work; no patient-portal menu change.

## 8. Owner's click-through (for the tracker, after build)

URL: http://alpha.localhost:8080 (after `scripts/care-dev.sh up`, `seed`, `keycloak up`,
`keycloak seed`). Logins: `dr.meera` and `nurse.asha` from `deploy/seed/TEST_USERS.md`.

1. Sign in as `dr.meera` at alpha; open the user menu (bottom of the sidebar): "Hospitals" lists
   alpha (Current) and beta.
2. Click beta: you land on beta signed in, with no password or code asked.
3. Open the user menu on beta: alpha is listed; click it and you are back on alpha.
4. Sign out on beta; reload alpha: you are signed out there too.
5. Sign in as `nurse.asha` at alpha: no "Hospitals" group.
