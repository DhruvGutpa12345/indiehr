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

  P1-T1 must test both against CARE's migrations, Celery tasks and caches before we pick one.
- **Isolation tests:** an automated cross-tenant test suite runs in CI and must stay green. It checks every API endpoint, file/object path and cache key.
- **Per-tenant backup/restore:** logical dump per schema. This is tested in P1-T1.
- **Object storage:** files live under a per-tenant prefix (`tenants/<tenant_id>/...`). Downloads use signed URLs.
- **Cache keys:** every cache and Redis key is prefixed with the tenant ID.

## Future paid tier

A "dedicated" option (its own database or own cluster) for large hospitals. The schema-per-tenant code path supports it by pointing a tenant at a different database connection.

## Consequences

- Strong isolation at low cost per tenant. One codebase and one upgrade covers everyone.
- Migrations run once per schema. Upgrade time grows with the number of tenants, so migrations must stay fast and backwards-compatible. We plan for 100s of tenants per cluster; beyond that we shard tenants across clusters.
- Upstream CARE code assumes a single schema. Any raw SQL, cross-schema queries or global caches in CARE must be audited (part of P1-T1).
