# ADR-001: Build on a fork of CARE

- **Status:** Accepted
- **Date:** 2026-11-03
- **Decided by:** product owner (Q-A1)

## Context

We compared four clinical cores (PLAN.md §3; research note `clinical_core.md`).

| Option | Tenancy fit | Licence | Weight | Verdict |
|---|---|---|---|---|
| OpenMRS O3 / Ozone / Bahmni (blueprint) | One install per hospital | MPL / AGPL parts | 15–30 containers | Rejected: too heavy per tenant |
| Marley Health + ERPNext (Frappe) | Native site-per-tenant | GPLv3 | Medium | Rejected: shallow clinical depth, permission-check history, MariaDB only |
| **CARE (ohcnetwork)** | Organisation hierarchy; needs hard isolation added | **MIT** | Light (Django + Postgres + Redis + React) | **Chosen** |

## Decision

Fork CARE's backend (`ohcnetwork/care`, Django 6, Python 3.13, PostgreSQL) and frontend (`ohcnetwork/care_fe`, React 19).

Rules for the fork:

1. **Upstream stays as close to unmodified as possible.** Our features go into CARE plugins named `ihr_*`:
   - backend: Django apps registered through `plug_config.py`;
   - frontend: micro-frontend plugins.

   Any edit to upstream files is listed in `docs/UPSTREAM_PATCHES.md` with the reason.
2. **Pin exact upstream commit SHAs.** CARE's last tags are backend v3.0.0 (Feb 2025) and frontend v3.0.32 (Dec 2025); development continues on main without tags.
   - We sync from upstream monthly with a scripted merge and a full test run.
   - We run our own release versions.
3. **We build ABDM M1–M3, NHCX and the scribe ourselves.** The CARE plugins for these (`care_abdm`, `care_hcx`, `care_scribe`) are no longer public. If OHC offers licensed access later, we will evaluate it then.
4. **Licence:**
   - Keep CARE's MIT copyright notices in a `NOTICE` file.
   - Our plugins and our changes are proprietary.
   - Do not use "CARE" in our branding.

## Consequences

- One main stack (Python + TypeScript + PostgreSQL) and a light footprint. This fits both the dev machine (after the 32 GB upgrade) and a single-VM staging server.
- We take on multi-tenancy (ADR-002), ABDM and India-specific workflows ourselves.
- If upstream goes quiet, we can carry the fork ourselves; the MIT licence allows it.
- P0-T5 (running CARE locally) confirms the module coverage. P0-T7 (gap analysis) maps every blueprint module to one of: exists in CARE, extend it, build new, or defer.
