# ADR-007: Blueprint items deferred or dropped

- **Status:** Accepted (records decisions already implied by ADR-000…006 and the owner's answers; nothing new is decided silently)
- **Date:** 2026-11-04
- **Raised by:** Phase 0 review finding R0-19

## Context

The owner's blueprint (`docs/blueprint/`) describes a self-hosted platform for **one tertiary hospital**. Our product is a **multi-tenant SaaS for 10–100 bed hospitals** (ADR-000). Several blueprint components are not being built as written. This record makes each of those choices explicit, so none is lost by accident.

**Legend:**
- **Deferred:** planned later, with a trigger to revisit.
- **Replaced:** same need, different tool.
- **Dropped:** out of scope for this product.

## Decision

| Blueprint item | Status | What we do instead | Revisit when |
|---|---|---|---|
| **HA data tier**: Patroni PostgreSQL cluster, Galera for MariaDB, Kubernetes HA namespaces | Deferred | Single VM with Docker Compose for staging and early pilots; k3s/RKE2 + CloudNativePG at about 10 hospitals (ADR-003, Q-E5) | ~10 paying hospitals, or a customer SLA that needs it |
| **Backups every 6 hours, encrypted, off-site** | Deferred, **with a gate** | pgBackRest with continuous WAL archiving to a second Indian region (P4-T7). **Targets proposed now, to confirm before the first pilot with real data:** data-loss window (RPO) ≤ 15 minutes; time to restore (RTO) ≤ 4 hours | **Before the first pilot with real data** (hard gate) |
| **Integration engine** (Open Integration Engine / Apache Camel) as the event spine | Deferred | Direct REST APIs inside one codebase; per-tenant **accounting outbox** for ERP/Tally (ADR-004); our own analyzer bridge (P5-T5) | When we need several HL7 v2 interfaces per hospital (e.g. third-party RIS/LIS); OIE 4.7 (MPL-2.0) is the candidate |
| **Keycloak LDAP/SAML federation** and **attribute-based access control (ABAC)** | Deferred | Keycloak OIDC + Organizations per hospital, MFA (P1-T3/T4); CARE's role permissions + care-team access and break-the-glass (P4-T5) | First enterprise customer with Active Directory / SSO requirements |
| **ICU bedside-monitor telemetry, TeleICU video/waveform streaming** | Deferred | ICU flowsheets with manual or batch device entry (Phase 6 ICU); CARE's camera/monitor plugins are no longer public | ICU module (Phase 6, priority #2 per Q-B5), after choosing a device gateway |
| **OpenELIS Global / SENAITE** as the lab system | Replaced | CARE's own lab flow (orders → specimens → results → reports, with age/sex ranges); OpenELIS 3.3 stays optional for standalone large labs (P5-T8) | A lab-chain customer, or analyzer volume that CARE's lab cannot handle |
| **dcm4chee-arc-light** PACS option | Dropped | Orthanc + OHIF (Phase 5) | — |
| **MinIO** object storage | Replaced | S3 in an Indian region, or SeaweedFS (MinIO community edition is unmaintained) | — |
| **ingress-nginx** | Replaced | Traefik v3 (ingress-nginx retired in March 2026) | — |
| **Odoo** ERP option | Dropped | Tally export + ERPNext v16 connector (ADR-004) | — |
| **Productivity suite**: Nextcloud + Collabora, Stalwart/Mailcow mail, Matrix chat, Mattermost/Zulip | Dropped from SaaS scope | Hospitals use their own email/office/WhatsApp; we send notifications via WhatsApp Cloud API, SMS (DLT) and Amazon SES Mumbai (Phase 8) | A future on-prem enterprise edition |
| **Chatwoot** omnichannel inbox | Deferred (optional) | Phase 8, P8-T4 | Customer demand |
| **AI layer**: MCP server, vLLM/Ollama, ambient scribe, MONAI radiology | Deferred | ADR-005: deterministic clinical rules only | MVP live with pilots **and** the owner approves a data-handling model |
| **On-prem deployment per hospital** | Deferred | SaaS only for the first 12 months (ADR-000) | Large-hospital demand; would reuse the same images |
| **Offline / outage mode** | Dropped (accepted risk) | Online-only; 4G failover router recommended (ADR-000, Q-S3) | After pilots |

**Not dropped:** the clinical and operational modules (OT, ICU, maternity, dialysis, IVF/ART, blood bank, CSSD, biomedical, PCPNDT, NABH quality) remain in the plan as **Phase 6** builds, ordered by Q-B5 (OT, ICU and maternity first).

## Consequences

- Phase 1–2 stay small enough for one founder plus agents.
- Two items carry real risk if forgotten, so they are gated:
  - **Backups/RPO/RTO** must be done before any real patient data (P4-T7 moves before the first pilot).
  - **Offline mode** is an accepted risk to re-check after pilots.
- When a "revisit" trigger happens, write a new ADR rather than editing this one.
