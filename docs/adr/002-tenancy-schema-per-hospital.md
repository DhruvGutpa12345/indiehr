# ADR-002: Multi-tenancy with one PostgreSQL schema per hospital

- **Status:** Accepted (approach). Implementation details are confirmed by the P1-T1 prototype.
- **Date:** 2026-11-03
- **Decided by:** product owner (Q-A2)

## Context

CARE's Organization/FacilityOrganization model gives permissions inside one shared database. That model was designed for government networks, not for isolating competing private hospitals.

We considered three ways to isolate tenants:

- one schema per hospital;
- one database per hospital;
- shared tables with Postgres row-level security (RLS).

## Decision

**One PostgreSQL schema per hospital (tenant), inside a shared database cluster.**

- **Shared `public` schema:** holds only platform-level data:
  - tenants and domains;
  - subscription/plan;
  - global reference masters (ICD-10, SNOMED subsets, the drug-generic master, PIN codes, PM-JAY package codes).
- **Each tenant schema:** holds all patient, clinical, billing, stock and audit data for that hospital.
- **Tenant resolution:** each request is matched to its tenant by subdomain (`<hospital>.<app-domain>`). Background jobs (Celery) carry the tenant ID explicitly and switch schema before touching data.
- **Identity:** Keycloak holds one global identity per person, and each hospital is a Keycloak Organization (ADR in P1-T3). A doctor who works at two hospitals has one login, a membership in each, and a separate user/staff record in each tenant schema, linked by the Keycloak `sub`.
- **Prototype candidates for P1-T1:**
  - `django-tenants`;
  - a thin in-house middleware that sets `search_path`.

  P1-T1 must test both against CARE's migrations, Celery tasks and caches before we pick one. *(Superseded by Amendment 1: django-tenants 4.0.1 chosen; the middleware option is dropped.)*
- **Isolation tests:** an automated cross-tenant test suite runs in CI and must stay green. It checks every API endpoint, file/object path and cache key.
- **Per-tenant backup/restore:** logical dump per schema. This is tested in P1-T1. *(2026-11-12, Phase 1 review R1-08: proven in the P1-T1c bench; the nightly per-hospital dumps, the single-hospital restore command and its drill are not built yet: P1-T22, a gate before the first pilot. Until then only whole-cluster point-in-time recovery exists, P1-T8c.)*
- **Object storage:** files live under a per-tenant prefix. Downloads use signed URLs. *(Amendment 1: prefix is `t/<hospital uuid>/`; patient files and public assets use separate buckets.)*
- **Cache keys:** every cache and Redis key is prefixed with the tenant ID. *(Amendment 1: except a short, reviewed allow-list of deliberately global keys, checked by a CI keyspace audit.)*

## Future paid tier

A "dedicated" option (its own database or own cluster) for large hospitals. The schema-per-tenant code path supports it by pointing a tenant at a different database connection. *(Amendment 1: delivered as a one-hospital "cell" — its own database and app servers, same code — moved with `move_hospital`.)*

## Consequences

- Strong isolation at low cost per tenant. One codebase and one upgrade covers everyone.
- Migrations run once per schema. Upgrade time grows with the number of tenants, so migrations must stay fast. *(Amendment 1: CARE's migrations are not backward-compatible, so upgrades use a short announced maintenance window with a per-tenant migration-fingerprint gate.)* We plan for 100s of tenants per cluster; beyond that we shard tenants across clusters. *(P1-T1c, owner-confirmed 2026-11-06: a cap of **100 hospitals per cell**; per-hospital dumps step up between 125 and 150, see docs/reviews/p1-t1c/scale-results.md.)*
- Upstream CARE code assumes a single schema. Any raw SQL, cross-schema queries or global caches in CARE must be audited (part of P1-T1).

---

## Amendment 1 (2026-11-06, P1-T0) — status: **Accepted** (owner, 2026-11-06)

Full design and evidence: [TENANCY_DESIGN.md](../TENANCY_DESIGN.md), [reviews/p1-t0/](../reviews/p1-t0/).

The schema-per-tenant decision stands. The P1-T0 code review changes **what lives where**:

1. **Every CARE app lives in the tenant schema**, including `users`, `security`, `facility`, `emr` and `audit_log`, plus Django `auth`, `contenttypes`, `sessions`, `admin` and `authtoken`. `public` holds **only IndieHR platform apps**: the tenant registry, domains, plans, the staff directory (Keycloak `sub` ↔ tenant, no PHI), operations log, pack registry and ABDM routing.
   - *Replaces:* "shared `public` schema holds global reference masters" and the open users design point. Option (a), User in public, is **rejected**: CARE's migrations link users to facilities and organisations, so it would need shadow tables and more patches.
2. **Reference masters are versioned packs applied to each tenant.** They are not shared rows. Only lookup tables that no CARE table references may live in `public`.
3. **Identity:** one Keycloak `sub` per person, one Keycloak Organization per tenant, and one CARE `User` per person per tenant, linked by `ihr_identity.StaffIdentity`. Tokens must carry exactly one organisation equal to the tenant's. Staff join by consented Keycloak invitation.
4. **Mechanism:** django-tenants **4.0.1** (security release; nothing older) and tenant-schemas-celery **5.0.0**, subject to the P1-T1 gates. Prototype candidate "thin `search_path` middleware" is **dropped**. django-pgschemas is the fallback.
5. **Provisioning** runs migrations and applies packs. Template cloning is **not** used in v1: it duplicates record ids across tenants and can hide missing migrations.
6. **Upgrades** use a short, announced platform maintenance window, because CARE migrations are not backward-compatible. A per-tenant migration-fingerprint gate blocks serving a schema that does not match the code.
7. **Tenant checks run in the DRF authentication and permission layer**, not only in middleware, because CARE's batch-request endpoint bypasses middleware.
8. Our apps are split into public-only and tenant-only Django apps, because django-tenants routes by app.
9. **Machine accounts** (analyzer bridge, integrations) keep DRF `TokenAuthentication`, restricted to `is_service_account` users. They are tenant-local because the token table is per schema, and they are created only by a platform command.
10. **Platform operators** use a separate Keycloak realm (`indiehr-ops`); support access to patient data is break-glass only.

Questions Q-T0 to Q-T11 were answered with the recommended defaults (QUESTIONS.md). In particular **a tenant is a legal entity (GSTIN holder) that may run several facilities**.

## Amendment 2 (2026-11-14, P1-T22b) — status: **Proposed** (Q-K33, awaiting the owner)

A single hospital is restored **in place by parking**, never under a side name: the live schema is renamed `ihr_parked_<schema>_<time>` (owner-only, dropped after 7 days) and the dump, after a quarantine that turns it into a checked package, is loaded under the real name in the same transaction; `Hospital.schema_name` never changes. A **restore hold** of its own (`HospitalSchemaState.restore_operation`) keeps every server, worker, release and fleet run away from the hospital until the restore lifts it. TENANCY_DESIGN §7.5 and §7.7 carry the amended runbook; deploy/staging/README.md, "Restoring one hospital (P1-T22b)", the commands.
