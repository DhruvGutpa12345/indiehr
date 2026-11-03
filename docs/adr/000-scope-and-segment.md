# ADR-000: Product scope and first segment

- **Status:** Accepted
- **Date:** 2026-11-03
- **Decided by:** product owner (QUESTIONS.md Q-A3, Q-A4, Q-A6, Q-B1, Q-B3, Q-C4, Q-D4, Q-D5, Q-G2)

## Context

The blueprint describes a fully self-hosted, on-premises platform for one tertiary hospital. Our goal is different: a commercial SaaS that serves many hospitals from one platform.

## Decision

1. **Product:** a multi-tenant, web-based Hospital Information System (HIS) sold as SaaS in India.
2. **First segment:** private hospitals and nursing homes with 10–100 beds in Tier 2/3 cities.
3. **MVP scope:** PLAN.md Phase 2.
   - registration, appointments and OPD queue tokens, OPD EMR, e-prescription, OPD billing;
   - IPD admission/transfer/discharge (ADT), nursing, discharge summary, IPD billing;
   - pharmacy, basic lab, basic radiology;
   - TPA/insurance, MIS reports, accounting handoff, printing.

   Then ABDM M1–M3, followed by **PM-JAY/NHCX claims as an early priority**.
4. **Clients:** a responsive web app only (desktop and tablet). No native apps in the MVP.
5. **Connectivity:** online-only. Offline support will be revisited after the pilots.
6. **Languages:** English and Hindi. More languages will be added on demand.
7. **Data:**
   - All data is stored in Indian cloud regions only.
   - Aadhaar is never stored in full; only masked Aadhaar and ABHA number/address are kept.
   - No real patient data outside production.
8. **Our code:** proprietary. We keep the required MIT/Apache notices for upstream components.
9. **Workflow:** commit directly to `main`; there is no pull-request step.

## Out of scope for now

- AI features (ADR-005)
- Offline mode
- Native mobile apps
- On-prem edition
- Productivity suite: Nextcloud, Collabora, Matrix, Stalwart mail
- Specialty modules (Phase 6) until pilots ask for them

## Consequences

- Phase 2 and the PM-JAY work decide success. Phase 6 waits until 1–3 pilot hospitals are live.
- Online-only keeps the architecture simple. Hospitals need a reliable connection; we recommend a 4G failover router at onboarding.
- **Accepted risk (Q-S3, 2026-11-03):** no paper/offline outage kit. About half of target buyers cite poor internet or power as a barrier; revisit after pilots.
