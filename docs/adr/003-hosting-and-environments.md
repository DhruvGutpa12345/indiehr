# ADR-003: Hosting and environments

- **Status:** Accepted (the cloud provider is chosen before P1-T8)
- **Date:** 2026-11-03
- **Decided by:** product owner (Q-D5, Q-E1, Q-E4, Q-E5 default)

## Decision

| Env | Where | Shape |
|---|---|---|
| **local** | The user's Ubuntu 26.04 workstation, **after the RAM upgrade to 32 GB** | Docker Compose, one stack at a time |
| **staging** | One VM in an **Indian region** (provider TBD before P1-T8) | Docker Compose + Traefik v3 (TLS via Let's Encrypt); deployed from CI |
| **production** | Indian region, with backups replicated to a second Indian region | Starts as Compose on a VM. Moves to k3s/RKE2 + CloudNativePG at about 10 tenants or when HA is needed (P9-T7) |

**Rules for every environment:**

1. **Data residency:** all data, backups and logs stay in India.
2. **CERT-In:**
   - clocks synced to NIC/NPL NTP servers;
   - logs kept for 180 days, in India;
   - incident-report runbook targets 6 hours.
3. **Ingress:** Traefik (ingress-nginx was retired in March 2026).
4. **Object storage:** S3 in an Indian region, or SeaweedFS. Not MinIO, which is unmaintained.
5. **Postgres:** the latest major version supported by CARE (18.x if CARE supports it; checked in P0-T5).
6. **Secrets:** never in git. A `.env` file locally; a secret manager on staging and production (chosen in P4-T4).
7. **Versions:** pinned exactly and recorded in `docs/VERSIONS.md`.

## Consequences

- No cloud spend until P1-T8. Until then the user tests on `localhost` URLs.
- Until the RAM upgrade, only lightweight tasks run locally: P0-T1 to P0-T4, P0-T7 and P0-T8. P0-T5 (running CARE) is blocked.
