# IndieHR — Progress Tracker

**Legend:**
- ✅ done (tested + you approved)
- 🟡 done, awaiting your review
- 🔵 in progress
- ⬜ not started
- ⏸ deferred
- ❌ dropped

Detailed task definitions: [PLAN.md](PLAN.md) §6. Open questions: [QUESTIONS.md](QUESTIONS.md).

**Current phase:** P0 Foundations
**Next task:** P0-T8 (CI pipeline). P0-T5 waits for the 32 GB RAM upgrade.
**Recommended model for next task:** Sonnet · **medium** for P0-T8

---

## Completed log

| Date | ID | What | Evidence |
|---|---|---|---|
| 2026-11-02 | R-0 | Read the full blueprint (441 lines); copied it to `docs/blueprint/` | — |
| 2026-11-02 | R-1 | Deep research, 8 parallel areas: clinical core, ERP/billing/claims, PACS/LIS, integration/identity/infra, India regulation, AI layer, productivity/licensing, market | `research_notes/India hospital SaaS stack research/*.md` |
| 2026-11-02 | R-2 | Consolidated research report | `reports/India hospital SaaS stack research.md` |
| 2026-11-02 | R-3 | Master plan v0.1, question list, tracker, CLAUDE.md | `docs/` |
| 2026-11-02 | R-4 | Workstation audit: 6.7 GB RAM, no GPU, Docker 26.1.3 without group access | PLAN.md §4 |
| 2026-11-03 | Q&A 2 | Remaining questions answered: curated terminology now, SNOMED licence prep, OT/ICU/Maternity first, payroll + e-invoice later via ERPNext, phase-end second-opinion reviews, product owner reviews clinical content | QUESTIONS.md |
| 2026-11-03 | P0-T7 | Gap analysis from CARE code: 28 MVP areas (7 use, 15 extend, 6 build) + 17 later modules; tenancy-readiness check (no raw SQL); 6 upstream risks; plan rescoped (+P1-T10 terminology, +P1-T11 roles) | docs/GAP_ANALYSIS.md |
| 2026-11-03 | P0-T4 | CARE backend @67a46185eb98 and frontend @57205b897804 imported as squashed subtrees (ADR-006); `upstream.lock`, `scripts/sync-upstream.sh` (dry-run + real sync rehearsed in a scratch repo: conflict-risk warning, 3-way merge, lock update all OK) | apps/, docs/UPSTREAM.md |
| 2026-11-03 | P0-T3 | git repo on `main`, private GitHub repo, monorepo layout, proprietary LICENSE + NOTICE, pre-commit (gitleaks, actionlint), CI workflow | github.com/DhruvGutpa12345/indiehr |
| 2026-11-03 | P0-T2 | Docker 29.9.2, Compose 5.7.0, gh 2.103.3, uv 0.13.6, pnpm 12.16.1, pre-commit 4.7.0 installed; hello-world OK | docs/VERSIONS.md, deploy/dev/setup-workstation-root.sh |
| 2026-11-03 | P0-T1 | ADR-000…005 written; PLAN.md → v1.0 (P0-T6 dropped, P2-T17 split, NHCX/PM-JAY moved up, AI deferred) | `docs/adr/`, PLAN.md |
| 2026-11-03 | Q&A | Blocking questions answered: CARE core, schema-per-tenant, 10–100 bed segment, RAM upgrade, Tally + ERPNext, PM-JAY early, no AI for now, commit to main (see QUESTIONS.md) | QUESTIONS.md |
| 2026-11-02 | Q-E2 (part) | User `ubuntu` added to `docker` group; Docker reachable without sudo (verified via `sg docker`). Upgrade to Docker 29.x still pending (P0-T2) | `getent group docker` |

## Phase status

| Phase | Name | Status | Tasks done |
|---|---|---|---|
| — | Research and planning | 🟡 | 5/5 |
| P0 | Foundations and decisions | 🔵 | 5/7 (T6 dropped) |
| P1 | Platform core (tenancy, identity, staging) | ⬜ | 0/11 |
| P2 | MVP hospital workflows | ⬜ | 0/20 |
| P3 | ABDM and national rails | ⬜ | 0/12 |
| P4 | Compliance and security | ⬜ | 0/7 |
| P5 | Diagnostics depth (PACS, LIS, QC) | ⬜ | 0/8 |
| P6 | Specialty and enterprise modules | ⬜ | 0/~30 |
| P7 | AI layer | ⏸ deferred (ADR-005) | — |
| P8 | Patient engagement and comms | ⬜ | 0/4 |
| P9 | Commercial SaaS readiness | ⬜ | 0/7 |

## Task board

### P0 — Foundations

- 🟡 P0-T1 Record decisions → ADR-000..005, plan v1.0
- 🟡 P0-T2 Workstation setup (docker group, Docker 29 / Compose v5, uv, pnpm, gh)
- 🟡 P0-T3 Repo bootstrap (git, GitHub, monorepo layout, licence/NOTICE)
- 🟡 P0-T4 Upstream fork strategy (CARE backend + frontend, pinned SHAs)
- ⬜ P0-T5 Spike: run CARE locally with test users ← **first thing you'll click** (blocked: needs 32 GB RAM)
- ❌ P0-T6 Spike: Marley + ERPNext v16 (dropped — Q-A1 decided)
- 🟡 P0-T7 Gap analysis blueprint ↔ CARE
- ⬜ P0-T8 CI pipeline

### P1 — Platform core

- ⬜ P1-T1 Tenancy ADR + isolation prototype
- ⬜ P1-T2 Subdomain tenant routing + branding
- ⬜ P1-T3 Keycloak 26.8 (Organizations, roles, MFA)
- ⬜ P1-T4 CARE ↔ OIDC
- ⬜ P1-T5 Super-admin tenant provisioning
- ⬜ P1-T6 Tamper-evident audit trail
- ⬜ P1-T7 i18n + Indian locale
- ⬜ P1-T8 Staging server + one-click URLs
- ⬜ P1-T9 Observability
- ⬜ P1-T10 Terminology service (SNOMED/ICD-10, no external calls)
- ⬜ P1-T11 Indian hospital roles

### P2 — MVP workflows

- ⬜ P2-T1 Masters and rate cards
- ⬜ P2-T2 Patient registration (UHID, MLC, masked Aadhaar)
- ⬜ P2-T3 Appointments + OPD token queue
- ⬜ P2-T4 OPD consultation EMR
- ⬜ P2-T5 e-Prescription
- ⬜ P2-T6 OPD billing
- ⬜ P2-T7 IPD admission/transfer/discharge + bed board
- ⬜ P2-T8 IPD clinical (orders, nursing, medication record)
- ⬜ P2-T9 Discharge summary
- ⬜ P2-T10 IPD billing, packages, TPA split
- ⬜ P2-T11 Pharmacy stores (purchase order, goods receipt, FEFO)
- ⬜ P2-T12 Pharmacy dispensing + statutory registers
- ⬜ P2-T13 Lab basic
- ⬜ P2-T14 Radiology basic
- ⬜ P2-T15 Insurance/TPA
- ⬜ P2-T16 MIS dashboards
- ⬜ P2-T17a Accounting outbox + Tally XML export
- ⬜ P2-T17b ERPNext v16 stack + connector
- ⬜ P2-T18 Print engine
- ⬜ P2-T19 MVP end-to-end test

### P3 — ABDM

- ⬜ P3-T1 (You) ABDM sandbox + HFR/HPR registration
- ⬜ P3-T2 Gateway client + mock
- ⬜ P3-T3 Fidelius crypto
- ⬜ P3-T4 M1 ABHA create/verify
- ⬜ P3-T5 M1 Scan & Share
- ⬜ P3-T6 FHIR bundle builder + validator
- ⬜ P3-T7 M2 Health Information Provider
- ⬜ P3-T8 M3 Health Information User
- ⬜ P3-T9 NHCX claims (priority)
- ⬜ P3-T10 PM-JAY / state schemes (priority)
- ⬜ P3-T11 Security audit (WASA/VAPT) readiness
- ⬜ P3-T12 DHIS incentive tracker

### P4 — Compliance

- ⬜ P4-T1 DPDP notice/consent/rights
- ⬜ P4-T2 Breach workflow
- ⬜ P4-T3 Retention engine
- ⬜ P4-T4 Encryption + secrets
- ⬜ P4-T5 Care-team access + break-the-glass
- ⬜ P4-T6 Legal pack (terms, Data Processing Agreement, privacy policy)
- ⬜ P4-T7 Disaster recovery

### P5 — Diagnostics

- ⬜ P5-T1 Orthanc + authorization plugin
- ⬜ P5-T2 OHIF + OIDC launch
- ⬜ P5-T3 DICOM worklist
- ⬜ P5-T4 Radiology reporting
- ⬜ P5-T5 Analyzer bridge (ASTM/HL7)
- ⬜ P5-T6 Westgard / Levey-Jennings QC
- ⬜ P5-T7 Delta checks + critical alerts
- ⬜ P5-T8 (Optional) OpenELIS integration

### P6 — Specialty and enterprise (broken into tasks when started)

- ⬜ Emergency/triage
- ⬜ OT + WHO checklist
- ⬜ ICU + early-warning scores
- ⬜ Dialysis
- ⬜ Maternity
- ⬜ IVF/ART
- ⬜ PCPNDT
- ⬜ Blood bank
- ⬜ CSSD
- ⬜ Biomedical
- ⬜ Dietary / housekeeping / BMW
- ⬜ NABH quality + incidents
- ⬜ MRD
- ⬜ ERPNext back office

### P7 — AI — ⏸ deferred (ADR-005)

- ⬜ P7-T1 LLM gateway (Bedrock India)
- ⬜ P7-T2 MCP read tools
- ⬜ P7-T3 MCP draft tools
- ⬜ P7-T4 Ambient scribe
- ⬜ P7-T5 Coding assist
- ⬜ P7-T6 Drug-interaction checks
- ⏸ P7-T7 Radiology AI

### P8 — Engagement

- ⬜ P8-T1 Notification service
- ⬜ P8-T2 Reminders / report links
- ⬜ P8-T3 Patient portal
- ⬜ P8-T4 Chatwoot
- ⏸ Nextcloud / Matrix / Stalwart

### P9 — Commercial

- ⬜ P9-T1 Plans + subscription billing
- ⬜ P9-T2 Onboarding + data import
- ⬜ P9-T3 Marketing site + demo
- ⬜ P9-T4 Support
- ⬜ P9-T5 Status page / SLAs
- ⬜ P9-T6 NABH software certification
- ⬜ P9-T7 Scale-out (k3s)

## Test credentials and one-click URLs

_None yet. They will appear here, and in `deploy/seed/TEST_USERS.md`, from P0-T5 onward._
