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
**Next task:** P1-T0 (tenancy design) or P0-T12/T13. Owner's deep-dive click-through still to come (docs/CLICK_THROUGH.md). Review: docs/reviews/phase-0-review.md
**Recommended model for next task:** Opus · **high** for P1-T0; Sonnet · **medium** for P0-T12/T13

---

## Completed log

| Date | ID | What | Evidence |
|---|---|---|---|
| 2026-11-02 | R-0 | Read the full blueprint (441 lines); copied it to `docs/blueprint/` | — |
| 2026-11-02 | R-1 | Deep research, 8 parallel areas: clinical core, ERP/billing/claims, PACS/LIS, integration/identity/infra, India regulation, AI layer, productivity/licensing, market | `research_notes/India hospital SaaS stack research/*.md` |
| 2026-11-02 | R-2 | Consolidated research report | `reports/India hospital SaaS stack research.md` |
| 2026-11-02 | R-3 | Master plan v0.1, question list, tracker, CLAUDE.md | `docs/` |
| 2026-11-02 | R-4 | Workstation audit: 6.7 GB RAM, no GPU, Docker 26.1.3 without group access | PLAN.md §4 |
| 2026-11-03 | Owner check | Quick look at the running app: "seems ok"; deep dive later. P0-T5 and P0-T11 approved | — |
| 2026-11-03 | P0-T11 | `care-dev.sh seed` / `reset-passwords`: adds care-pharmacist + care-billing, rotates all 12 passwords, writes TEST_USERS.md (verified: 4 roles log in + open facility; old password 401). `up` rebuilds the image when the lock file changes. GAP §8 verified: token queue FIFO (no priority); age/sex lab ranges automatic; **invoice numbering not GST-safe** (count()+1, no unique, calendar year) → P2-T6; FEFO UI-only, API accepts expired stock → P2-T12; discharge template fixture-only → P2-T9; dates DD/MM/YYYY. Billing 400 = expected 'no account yet'. **Postgres 18.6: all 2,005 backend tests pass.** Click-through written, all links checked | GAP_ANALYSIS §8, run 41614579309 |
| 2026-11-03 | P0-T10 | Egress lockdown: 5 CARE frontend patches (OHC Sentry init + default DSN, SBOM default, OHC CDN badge, CDN image-compression worker, CDN QR engine); `deploy/frontend.env`; `scripts/check-bundle-egress.sh` + reviewed allowlist (fails on OHC Sentry/CDN/API/SBOM and any unreviewed host); CSP **enforced** via nginx template (API/storage origins at runtime). Verified: old bundle fails the check (3 denied hosts), new passes (45 hosts reviewed), OHC Sentry key absent; browser test with fresh tab — 0 CSP violations across encounters, patient encounter, plots, medicines. CI green. Known gaps: facility map blocked; reCAPTCHA off | Runs 41611564311, 41612150150 |
| 2026-11-03 | Q&A 3 | Review questions answered: minimal logged CARE model edits OK; customer discovery before Phase 2 (P1-T14); no outage kit (accepted risk); stop other services on dev PC | QUESTIONS.md |
| 2026-11-03 | Review | Phase 0 independent Opus review: grade B−, 21 findings (2 High: OHC Sentry hard-coded in frontend; CARE `User` model blocks schema-per-tenant as designed). Verified the High items myself. Found and fixed: P0-T5 commit had silently failed (check-yaml rejected Compose `!override`); now committed as 8eeaea81e. New tasks P0-T10..T13, P1-T0 | docs/reviews/phase-0-review.md |
| 2026-11-03 | P0-T5 | CARE running locally on 6.7 GB: backend stack ~550 MB (one Celery process, memory caps), all ports bound to 127.0.0.1, terminology calls blocked, fixture passwords replaced; web bundle built on GitHub (new `Frontend bundle` workflow, OHC URLs removed) and served by our non-root nginx image. Verified: API login 200 (old public password 401), browser login as doctor, facility + patient search pages, **all browser requests to localhost only** | docs/LOCAL_DEV.md, scripts/care-dev.sh |
| 2026-11-03 | P1-T12 | Fixed all 29 fixable HIGH vulns (Django 6.0.8, DRF 3.18.2, Authlib 1.9.1, urllib3 2.8.0, aiohttp 3.14.4, cryptography 50.0.0, sqlparse 0.6.0; vite 8.3.5 + 4 npm pkgs). DRF 3.18 broke CARE (CoreAPI removed) → patched 2 filter files. New non-root frontend image `deploy/docker/frontend.Dockerfile` (nginx-unprivileged 1.30.6, uid 101). Trivy gate raised to HIGH + misconfig with scoped exceptions. CI green: backend 11 min, frontend + image smoke 5 min | Run 41583635960 |
| 2026-11-03 | P0-T9 | Security bumps: PyJWT 2.14.0, Pillow 13.0.0, Authlib 1.9.0, Brotli 1.2.1, certifi 2026.10.18, DOMPurify 3.4.17. Local Trivy 0.76.1: 0 CRITICAL. CI all green: backend full suite 11 min, frontend 4 min, Trivy pass. Remaining: 29 fixable HIGH + root Docker images → P1-T12 | Run 41580332662, docs/UPSTREAM_PATCHES.md |
| 2026-11-03 | P0-T8 | Apps CI: backend full upstream test suite passes (12 min); frontend typecheck/lint/unit/build pass (4 min); Trivy gate fails on 10 fixable CRITICAL vulns in upstream locks: PyJWT 2.12.0 (auth bypass), Pillow 12.2.0, Authlib 1.6.12, Brotli 1.2.0, certifi, DOMPurify 3.4.12 | Run 41571634449 |
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
| P0 | Foundations and decisions | 🔵 | 10/12 — Phase 0 review (B−) added P0-T10..T13 |
| P1 | Platform core (tenancy, identity, staging) | 🔵 | 1/14 |
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
- ✅ P0-T5 Run CARE locally with test users (done on 6.7 GB: backend ~550 MB, web app bundle built on GitHub)
- ❌ P0-T6 Spike: Marley + ERPNext v16 (dropped — Q-A1 decided)
- 🟡 P0-T7 Gap analysis blueprint ↔ CARE
- 🟡 P0-T8 CI pipeline: backend ✅, frontend ✅, Trivy ✅ (after P0-T9)
- 🟡 P0-T10 Egress lockdown: OHC Sentry/CDN/reCAPTCHA/OSM off, bundle deny-list in CI, enforced CSP (review High)
- ✅ P0-T11 Close P0-T5: scripted password rotation, billing + pharmacist users, GAP §8 checks, Postgres 18 check, billing 400 explained (owner: quick look OK 2026-11-03; deep dive later)
- ⬜ P0-T12 CI hardening: SHA-pinned actions, real secret scanning, production image scan, shellcheck
- ⬜ P0-T13 Docs + versions sync, ADR-007 deferred blueprint items
- 🟡 P0-T9 Security bumps of upstream dependencies (PyJWT, Pillow, Authlib, Brotli, certifi, DOMPurify) → Trivy green ✅

### P1 — Platform core

- ⬜ P1-T0 Tenancy design spike: shared vs tenant model map, user model, ADR-002 amendment (review High)
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
- ⬜ P1-T14 Customer discovery: 5–10 hospital interviews, pilot LOI (before Phase 2)
- 🟡 P1-T12 Hardening before staging: fix 29 fixable HIGH dependency vulns (22 backend, 7 frontend) + non-root Docker images

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

| What | Where |
|---|---|
| Web app (local) | http://localhost:8080 — start with `scripts/care-dev.sh up` then `scripts/care-dev.sh frontend` |
| Usernames | `care-doctor`, `care-nurse`, `care-pharmacist`, `care-billing`, `care-staff`, `care-fac-admin`, `care-admin`, `admin` (superuser) |
| Password | in `deploy/seed/TEST_USERS.md` (git-ignored); new one any time: `scripts/care-dev.sh reset-passwords` |
| Guided tour | docs/CLICK_THROUGH.md |
| Demo patients | e.g. Nakul Chadha (+91 79642 54593), Madhavi Narayanan (+91 88323 75901) — synthetic |
| How it runs | docs/LOCAL_DEV.md |
