# IndieHR — Master Build Plan

> Status: **v1.0 — decisions accepted 2026-11-03** (see [ADR log](#decision-log) and the [QUESTIONS.md](QUESTIONS.md) answers table) · First draft 2026-11-02
> Source blueprint: [docs/blueprint/Open Source Hospital Architecture Blueprint.md](blueprint/Open%20Source%20Hospital%20Architecture%20Blueprint.md)
> Research behind this plan: [reports/India hospital SaaS stack research.md](../reports/India%20hospital%20SaaS%20stack%20research.md) and the notes in `research_notes/India hospital SaaS stack research/`
> Live progress: [TRACKER.md](TRACKER.md)

---

## 1. What we are building

**The goal:** a commercial, multi-tenant **SaaS Hospital Information System for India**, assembled from open-source parts (the blueprint) and sold on subscription.

The blueprint describes **one large tertiary hospital running its own servers**. A SaaS is a different shape. We host **many hospitals on one platform**, so we need:

- strict separation between tenants;
- low cost per tenant;
- self-service onboarding;
- one codebase that we upgrade for everyone at once.

That difference drives most of the changes below.

### Decision log

| ADR | Decision |
|---|---|
| [ADR-000](adr/000-scope-and-segment.md) | Scope: web-only, online-only, English + Hindi, 10–100 bed segment, proprietary code, commits to `main` |
| [ADR-001](adr/001-core-platform-care.md) | Core platform: fork CARE; our features as `ihr_*` plugins; pinned upstream SHAs |
| [ADR-002](adr/002-tenancy-schema-per-hospital.md) | Tenancy: one Postgres schema per hospital; Keycloak global identity + Organizations |
| [ADR-003](adr/003-hosting-and-environments.md) | Hosting: local → single India-region VM staging → k3s later; cloud provider chosen before P1-T8 |
| [ADR-004](adr/004-accounting-tally-and-erpnext.md) | Accounting: accounting outbox → Tally XML export **and** ERPNext v16 connector |
| [ADR-005](adr/005-ai-deferred.md) | AI: deferred; deterministic clinical rules only |
| [ADR-006](adr/006-upstream-fork-via-subtree.md) | CARE imported as squashed git subtrees in our private repo, pinned in `upstream.lock` |
| [ADR-007](adr/007-blueprint-items-deferred.md) | Blueprint items deferred or dropped, with the reason and when to revisit |

### 1.1 Who we sell to first (decided — ADR-000)

**10–100 bed private hospitals and nursing homes in Tier 2/3 cities.**

- Roughly 75,000 of India's ~80,000 hospitals have under 100 beds.
- Incumbent vendors build for corporate chains and are too costly or heavy for these hospitals.
- Their top buying barriers: cost and doubt about the return (68%), training (57%), poor internet or power (49%).
- What they actually use today: billing (81%) and registration (78%) far more than an electronic health record (42%).
- Hospital-tier prices are rarely published. The figures found suggest about ₹150–800 per bed per month.

So the minimum product (MVP) must win on **billing + registration + OPD + IPD + pharmacy + basic lab**, all usable from day one. It also needs **ABDM M1–M3**, because without them a hospital loses the government incentive payouts (DHIS).

---

## 2. Key findings that change the blueprint

| # | Blueprint says | Reality (Nov 2026) | Impact on us |
|---|---|---|---|
| 1 | Run all of Ozone/OpenMRS + ERPNext + OpenELIS + Orthanc + OIE + Keycloak per hospital on Kubernetes | OpenMRS/Ozone/Bahmni are **one hospital per installation**. Bahmni Standard is about 30 containers. | Unaffordable per tenant. Pick a **multi-tenant-capable core**. |
| 2 | Ozone uses Apache Camel to ERPNext | Ozone 1.0.0's core ERP is **Odoo 17**; ERPNext is marked "Legacy" | Ozone is not an ERPNext path |
| 3 | CARE has native ABDM v3 (MIT) | CARE core is MIT and FHIR-shaped, but the **care_abdm, care_scribe, care_hcx and TeleICU plugins are no longer public**. The last tagged releases are backend v3.0.0 (Feb 2025) and frontend v3.0.32 (Dec 2025); work since then is on main with no tags. | We **build ABDM M1–M3 ourselves** (or license from OHC) and pin CARE to commits with our own releases |
| 4 | Matrix Synapse is Apache 2.0 | **AGPL-3.0** (v1.164.2) | Use unmodified only, or skip |
| 5 | Stalwart is a free multi-tenant mailer | Multi-tenancy is **Enterprise-only** | Use Amazon SES (Mumbai) or Postal for transactional mail |
| 6 | MinIO for object storage | MinIO community edition is **unmaintained** (repo reportedly archived in 2026, no binaries) | Use SeaweedFS 4.52, or S3 in an Indian region |
| 7 | Nginx ingress | **ingress-nginx retired March 2026** | Use Traefik v3.7 / Gateway API |
| 8 | "OpenQC engine" handles Westgard rules | A 3-day, single-author PCR-lab hobby repo | Build Westgard rules and Levey-Jennings charts ourselves (OpenELIS 3.3 has basic QC) |
| 9 | Blood bank and CSSD from GitHub repos | The cited repos are student projects | Build these ourselves |
| 10 | Llama 3.3 70B / Qwen 2.5 72B / Meditron / BioMistral | Outdated. Current open options: **Gemma 4 31B (Apache-2.0)**, **Qwen3.8-27B (Apache-2.0)**, MedGemma 1.5 (gated terms); Claude Opus 5 / Sonnet 5 on **AWS Bedrock with India-only routing** | Your machine has no GPU, so use Bedrock India for now and self-hosted models later |
| 11 | Mirth fork: OIE / BridgeLink | Correct. OIE 4.7.0 (Eclipse incubation, MPL-2.0); BridgeLink 26.9.0 (MPL-2.0, but its WebAdmin is under the **Business Source License**) | Use OIE only if we need visual HL7 channels |
| 12 | MCP over stdio/SSE | MCP spec **2026-07-28** is stateless, SSE is deprecated, auth is OAuth 2.1 | Use FastMCP 4.1.x on Streamable HTTP |
| 13 | Orthanc is GPLv3 | Core is GPLv3; PostgreSQL, DICOMweb, authorization, Python and object-storage plugins are **AGPLv3** | Use unmodified; talk to it over REST/DICOMweb only |
| 14 | NABH 5th/6th edition | 6th edition is in force from 1 Jan 2025. NABH also **certifies HIS/EMR software** under its Digital Health Standards: needs 3 live hospitals plus an ABDM M3 certificate. | A sales lever once we have 3 hospitals live |
| 15 | DPDP Act | **DPDP Rules notified 13 Nov 2025**. Consent-manager rules from Nov 2026; core duties from **~13 May 2027**; breach report within 72 h; logs kept ≥ 1 year | Build DPDP features in from the start |
| 16 | — (not mentioned) | **CDSCO medical-device-software guidance (Jul 2026)**: AI clinical decision support can be a regulated medical device | AI stays "documentation assist / clinician confirms"; no autonomous diagnosis |
| 17 | — | **CERT-In**: report incidents within 6 h, keep logs 180 days in India, sync clocks to NIC/NPL NTP | Ops requirements |
| 18 | — | GST: clinical services are exempt; **non-ICU room rent above ₹5,000/day is 5% GST with no input credit**; GST 2.0 (22 Sep 2025) moved most medicines and devices to 5% | Encode in the billing engine |
| 19 | ERPNext on Postgres | ERPNext v16 runs on **MariaDB 11.8 only**; Postgres support is not yet official | If we add ERPNext it brings its own MariaDB |
| 20 | — | Frappe trademark policy: no "ERPNext"/"Frappe" in our product name | Branding constraint |

---

## 3. Architecture options (decided: Option B — [ADR-001](adr/001-core-platform-care.md))

### Option A — "Compose the blueprint" (Ozone/OpenMRS + ERPNext + OpenELIS + Orthanc + OIE + Keycloak)

- **Pros:** most mature clinical forms (OpenMRS); matches the blueprint as written.
- **Cons:** single-tenant by design; 15–30 containers per hospital; Java, Python and Node stacks to maintain; the ERP side has moved to Odoo; will not run on your 6.7 GB machine. **Not viable as a SaaS for small hospitals.**

### Option B — "CARE-centred SaaS" ⭐ **Recommended**

Fork **CARE** (MIT, Django 6 / Python 3.13 / PostgreSQL / React 19). It already has FHIR-shaped models for patients, encounters, lab orders (ServiceRequest), specimens, diagnostic reports, questionnaires, **billing (ChargeItem / Account / Invoice, with GST tax config)**, inventory, pharmacy dispensing, beds and an ICU focus.

Add:
- **multi-tenancy** — a schema or database per hospital (ADR-001);
- **ABDM M1–M3**, built by us;
- **Keycloak 26.8** (Organizations) for login;
- **Orthanc 1.13 + OHIF 3.13** (with per-tenant labels) for imaging;
- **our own analyzer bridge, Westgard rules and India-specific modules**.

Hand the **accounting** books off either to **Tally export** (what Indian hospital accountants already use) or to **ERPNext v16 + India Compliance** (one Frappe site per tenant) in a later phase.

- **Pros:**
  - MIT licence, so our extensions can stay proprietary;
  - one main stack (Python/TS/Postgres);
  - FHIR-shaped data makes ABDM easier;
  - light enough for a single VM per environment;
  - India-native, with ICU strength.
- **Cons:**
  - we must add tenant isolation and build ABDM;
  - CARE releases are sporadic, so we run our own release train;
  - some outpatient workflows need polish.

### Option C — "Frappe-centred" (Marley Health v16 + ERPNext v16 + India Compliance + HRMS)

- **Pros:**
  - one site per tenant is native, so multi-tenancy is solved;
  - accounting, GST e-invoice, inventory and HR come built in;
  - Marley already includes ABDM;
  - all GPLv3, which is fine for SaaS.
- **Cons:**
  - shallow clinical and ICU depth;
  - security concerns (only 3 of 147 API endpoints checked permissions until recently);
  - GPL derivative-work questions for our custom apps;
  - MariaDB only;
  - trademark limits;
  - Frappe UI is ERP-ish and less clinician-friendly.

**Recommendation: Option B**, with an optional ERPNext back-office module in Phase 6.

If you prefer to reach a billing-heavy MVP fastest and accept weaker clinical depth, Option C is the alternative.

_Decided: Option B (ADR-001). The planned CARE-vs-Marley spike (P0-T6) was dropped; CARE alone ran in P0-T5._

### 3.1 Target architecture (Option B)

```
                         ┌──────────────── Traefik v3.7 (TLS 1.3, *.indiehr.in) ───────────────┐
                         │                                                                      │
  Browser / mobile ──►  app.<tenant>.<domain>   auth.<domain> (Keycloak 26.8)   pacs.<domain> (OHIF 3.13)
                         │                                                                      │
          ┌──────────────┴───────────────┐        ┌─────────────────┐       ┌──────────────────┐
          │ IndieHR Web (CARE fe fork,   │        │ Keycloak        │       │ Orthanc 1.13 +   │
          │ React 19 + our plugins)      │        │ Organizations = │       │ authz plugin     │
          └──────────────┬───────────────┘        │ tenants, MFA    │       │ (labels/tenant)  │
                         │ REST                   └─────────────────┘       └──────────────────┘
          ┌──────────────┴───────────────────────────────────────────────┐
          │ IndieHR API (CARE backend fork, Django 6) + tenancy layer     │
          │  plugins: ihr_tenancy · ihr_abdm · ihr_billing_in · ihr_pharmacy_in │
          │           ihr_lab_qc · ihr_tpa · ihr_mis · ihr_notify · ihr_accounts │
          └───┬──────────┬────────────┬──────────────┬────────────────────┘
              │          │            │              │
        PostgreSQL 18  Redis/Valkey  Celery workers  Object store (SeaweedFS / S3 ap-south-1)
        (schema or DB   (cache,       (ABDM callbacks,
         per tenant)     queues)       PDFs, notifications)
              │
   Side services: ABDM gateway (sandbox→prod) · NHCX · WhatsApp Cloud API · SMS (DLT) · SES Mumbai
                  Analyzer bridge (ASTM/HL7 → API) · Tally XML export
                  ERPNext v16 + India Compliance (per-tenant site, via accounting outbox — ADR-004)
                  Optional: OpenELIS 3.3 for big labs · Deferred: AI layer (ADR-005)
```

### 3.2 Environments

| Env | Where | Purpose |
|---|---|---|
| **local** | Your Ubuntu 26.04 workstation (needs RAM upgrade — Q-E1) or a cloud dev VM | Dev + unit/integration tests |
| **staging** | One India-region VM (AWS Mumbai / E2E / Azure Pune), Docker Compose → k3s later | One-click test URLs for you, demo hospitals |
| **production** | India region, managed Postgres or CloudNativePG on k3s/RKE2, backups replicated to a second Indian region | Paying tenants |

---

## 4. Constraints discovered on this machine

| Item | Found | Needed |
|---|---|---|
| RAM | **6.7 GB total, ~1 GB free** (Firefox + Claude use most of it) | Upgrade to 32 GB decided (not yet installed). CARE runs anyway in a ~600 MB low-memory setup (P0-T5); the web app is built on GitHub |
| CPU | i3-12100, 8 threads | OK |
| GPU | Intel UHD 730 (none usable) | Self-hosted LLM/ASR impossible locally → use Bedrock India / API, or a GPU VM later |
| Disk | 394 GB free | OK |
| Docker | ✅ Docker 29.9.2 / Compose 5.7.0; user in the `docker` group (P0-T2) | — |
| Tools | ✅ uv, pnpm, gh, pre-commit installed (P0-T2); kubectl, helm, Java not yet | Install as needed per task |
| Git | ✅ Private repo github.com/DhruvGutpa12345/indiehr (P0-T3); `gh` CLI logged in | — |

---

## 5. Working agreement (how each task runs)

1. **One small task at a time.** I finish it, test it, update [TRACKER.md](TRACKER.md), and stop. You review and say "proceed".
2. **Every task has acceptance tests.** Automated tests (pytest / vitest / Playwright) run before I report done. I paste the results honestly, failures included.
3. **One-click testing for you.** Whenever you need to check something, I give you:
   - a URL (e.g. `http://localhost:9000/login` or `https://staging.<domain>/...`);
   - test login(s) created by a seed script and recorded in `deploy/seed/TEST_USERS.md` (never real credentials);
   - a 3–5 step "what to click" checklist.
4. **Effort advice.** At the end of every reply I state the **model + effort level** for the next task:
   - **Sonnet (low/medium):** setup, config, boilerplate.
   - **Opus (medium):** feature work.
   - **Opus (high):** architecture, security, crypto, ABDM, tenancy, billing maths.
5. **Subagents** are used for research or parallel review with a matching model:
   - Haiku or Sonnet for lookups;
   - Opus for design or security review.
6. **Latest versions only.** Each task re-checks the current stable version of what it installs, pins it, and records it in `docs/VERSIONS.md`.
7. **ADRs.** Every significant decision gets a short Architecture Decision Record (ADR) in `docs/adr/`.
8. **No real patient data** anywhere outside production. Seed data is synthetic, with Indian names, addresses and phone numbers in reserved test ranges.
9. **Phase reviews (`/goal`, `/advisor`):** when a phase is finished, an independent Opus agent reviews it against this plan, the ADRs and the product goal, then reports gaps before the next phase starts.
10. **Clinical review:** clinical templates (OPD, discharge summary, nursing charts) go to the product owner for review before they are marked done.
11. **Git:** commit directly to `main`, with one or more commits per task, each message referencing the task ID (e.g. `P0-T3: ...`). There is no pull-request step (ADR-000).

---

## 6. Phased roadmap

Task IDs are `P<phase>-T<n>`. Each task is sized for **one sitting (≈1–3 hours of agent work)**. **Effort** = suggested model / effort for that task. A ✅ here means the work is done; whether you have approved it is shown in [TRACKER.md](TRACKER.md) (🟡 = awaiting your review).

### Phase 0 — Foundations and decisions

| ID | Task | Sub-tasks | Done when | Effort |
|---|---|---|---|---|
| P0-T1 ✅ | Record decisions | Turn your answers to QUESTIONS.md into ADR-000 to ADR-005; update this plan | ADRs written, plan v1.0 | Opus / medium |
| P0-T2 | Workstation setup | Guide you to add your user to the `docker` group; upgrade Docker 29.x + Compose v5; install uv, pnpm, gh, pre-commit; verify | `docker run hello-world` works without sudo; versions logged | Sonnet / low |
| P0-T3 | Repo bootstrap | `git init`, private GitHub repo, monorepo layout (`apps/`, `plugins/`, `deploy/`, `docs/`), licence and NOTICE files (MIT attribution for CARE), .editorconfig, pre-commit, CLAUDE.md refresh | First commit pushed; CI "hello" green | Sonnet / medium |
| P0-T4 ✅ | Upstream fork strategy | Import `ohcnetwork/care` and `care_fe` as squashed git subtrees (ADR-006; a GitHub fork would be public); pin commit SHAs; scripted upstream-sync; record versions | `scripts/sync-upstream.sh all --dry-run` works | Sonnet / medium |
| P0-T5 ✅ | **Spike: run CARE locally** | Bring up CARE backend + frontend + Postgres + Redis with Compose; load sample data; create test users (admin, doctor, nurse, billing, pharmacist) | **You** log in at a localhost URL and click through OPD, IPD, lab, pharmacy, billing | Opus / medium |
| ~~P0-T6~~ | ~~Spike: Marley + ERPNext~~ — **dropped** (Q-A1 decided). ERPNext is set up in P2-T17b instead. | — | — | — |
| P0-T7 ✅ | Gap analysis | Map every blueprint module to: exists in CARE / needs extension / build new / defer; estimate | `docs/GAP_ANALYSIS.md` approved | Opus / high |
| P0-T8 ✅ | CI pipeline | GitHub Actions: lint (ruff, eslint), CARE backend test suite, frontend typecheck/unit tests/build, image builds, Trivy scan. **Playwright end-to-end not yet** (needs the full stack in CI; planned with P2-T19) | Green pipeline on every push to `main` (no PRs, ADR-000) | Sonnet / medium |
| P0-T9 ✅ | Security bumps found by CI | Raise PyJWT → 2.14.0, Pillow → 13.0.0, Authlib → 1.9.0, Brotli → 1.2.1, certifi → 2026.10.18 (backend `Pipfile`/`Pipfile.lock`), DOMPurify → 3.4.17 (frontend lock); log in UPSTREAM_PATCHES.md; full backend + frontend CI | Trivy gate green, all tests pass | Opus / medium |
| P0-T10 ✅ | **Egress lockdown** (review R0-01, R0-02 — High) | Patch CARE so Sentry is off unless our own DSN is set; remove OHC CDN logo, reCAPTCHA and OpenStreetMap defaults; CI check that fails the bundle build on `sentry.io`, `ohc.network`, `google.com/recaptcha`, `openstreetmap`; enforce (not report-only) CSP with `connect-src` limited to our API | Bundle contains no third-party hosts; browser shows CSP enforced | Opus / medium |
| P0-T11 ✅ | **Close P0-T5 properly** (R0-05, R0-11) | `care-dev.sh seed` rotates passwords and writes `TEST_USERS.md`; add Cashier/Billing and Pharmacist test users; rebuild image when the lock file changes; run GAP_ANALYSIS §8 checks (invoice numbering, FEFO, reference ranges, date formats) and record results; Postgres 18 compatibility check; owner click-through script | Fresh `seed` gives working logins with no public passwords; §8 results recorded | Sonnet / medium |
| P0-T12 ✅ | **CI hardening** (R0-07, R0-08, R0-12, R0-13, R0-14) | Pin all actions to commit SHAs; real gitleaks scan in CI + blocking Trivy secret scan; build and `trivy image`-scan the production backend image; shellcheck; `cancel-in-progress: false` on `main`; widen path filters; sync script: full SHAs only, `--task` ID, conflict/relock runbook | All gates green; a planted test secret makes CI fail | Sonnet / medium |
| P0-T13 ✅ | **Docs + versions sync** (R0-06, R0-10, R0-19) | Bump to latest patch releases (Django 6.1.x or 6.0.9 after tests, DRF 3.18.3, PyJWT 2.15.1, cryptography 50.0.2, Authlib 1.9.2); weekly "outdated" report; fix the ~15 stale doc statements; ADR-007 "blueprint items deferred or dropped" (ICU telemetry, integration engine, LDAP/SAML/ABAC, HA + RPO/RTO) | CI green on new versions; review §2 list cleared | Sonnet / medium |

### Phase 1 — Platform core (tenancy, identity, staging)

| ID | Task | Done when | Effort |
|---|---|---|---|
| P1-T0 ✅ | **Tenancy design spike** (review R0-03, R0-21): 17-agent mapping/design/verification workflow → [TENANCY_DESIGN.md](TENANCY_DESIGN.md) + ADR-002 Amendment 1 (proposed) | Your approval of the design and answers to Q-T0…Q-T11 | Opus / high |
| P1-T1a ✅ | Tenancy library spike: django-tenants 4.0.1 + tenant-schemas-celery 5.0.0 (relock), our settings module, split public/tenant apps, `pg_trgm` in public, create two tenants by migrate + seed, **CARE's 2,005 tests pass inside a tenant schema** (TENANCY_DESIGN §11.1) | Full suite green in tenant mode in CI | Opus / high |
| P1-T1b 🟡 | Isolation suite: structural checks, API crawl with canaries (cross-tenant and anonymous), Redis keyspace audit, Celery, file keys, token checks, batch requests, **mutation runs must fail** (§11.2) | Suite green; each mutation run red | Opus / high |
| P1-T1c 🟡 | Scale and operations: migration time and catalogue size at 1/50/200 schemas, per-tenant backup/restore round trip, onboarding time (§11.3) → set the cell cap | Numbers recorded; cap decided with you | Opus / medium |
| P1-T1d | Playwright on two `*.localhost` subdomains: token isolation, logout clean-up, switcher (§11.4; needs P1-T4 login) | Green | Sonnet / medium |
| P1-T2a | Backend for per-hospital routing and branding: `ihr_tenant_settings` (hospital profile: names, GSTIN with checksum, address with GST state + PIN, registration numbers, letterhead, logo, colour), public `bootstrap` endpoint per subdomain, `<slug>.<suffix>` domains, immutable slugs, password links per hospital + tenant claim on reset tokens | Two hospitals return their own branding by Host; tests + isolation suite green | Opus / medium |
| P1-T2b | Frontend + dev stack: runtime branding in the web app (bootstrap script, small logged patch), same-origin `/api` proxy, dev stack in tenant mode with two seeded demo hospitals on `alpha.localhost` / `beta.localhost`, our own email templates (no CARE branding) | Two demo hospitals with separate branding, clicked through by you | Opus / medium |
| P1-T3 | Keycloak 26.8: realm, Organizations = tenants, ~~roles (doctor, nurse, billing, pharmacist, lab tech, admin, MRD, superadmin), MFA/TOTP for admins~~ **per owner 2026-11-06 (ADR-008): job roles stay in CARE, Keycloak holds one `member` role per hospital; MFA for all staff; superadmin in the operator realm (P1-T5)**, password policy | Login via Keycloak for both tenants; hospital-scoped role and organisation claims in token | Opus / medium |
| P1-T4a | **Backend** for Keycloak sign-in (owner 2026-11-07, ADR-009): tokens held by the backend (HttpOnly cookie, R0-20), every request re-checks the ADR-008 claims, `sub` → CARE user link (never by email), sign-out and 30-min idle end single sign-on everywhere, CARE's own staff login/JWT/MFA/password routes and Django admin switched off; job roles stay CARE's own | Unit, isolation, mutation and live-Keycloak tests green; scripted sign-in through the dev stack | Opus / high |
| P1-T4b | **Web app** on the new sign-in: "Sign in with your hospital account", no stored tokens, CSRF + idle headers, idle warning, clear cached data at sign-out/expiry (Q-T9), session-expired page; owner's click-through | SSO works; logout; session timeout — clicked through by you | Opus / medium |
| P1-T5 | Super-admin console: create tenant → provision schema, Keycloak org, seed masters, first admin user. **Split (owner, 2026-11-07) into:** P1-T5a provisioning engine (command line, resumable, roll-back) → P1-T5b operator realm + server-rendered console → P1-T5c first-admin setup e-mail (one-time link) → P1-T5d instant back-channel logout → P1-T5e staff invitations + MFA reset (**split, owner 2026-11-07:** P1-T5e1 invitations through CARE's Add user, new accounts by setup e-mail, existing accounts by link + sign-in + Join → P1-T5e2 account recovery: lost authenticator reset by a hospital admin, forgotten password, resend/cancel invitations, removing membership of unaccepted joins) → P1-T5f narrow Keycloak credentials (gate for P1-T8) | New hospital live in < 5 min from a form | Opus / medium |
| P1-T6 | Audit trail: append-only log of every create/read/update/delete on patient data (who, when, from where, purpose); tamper-evident hash chain | Audit viewer + tests | Opus / high |
| P1-T7 | i18n + Indian locale (**split into batches when started:** about 6,000 Hindi strings, machine draft + your review; clinical terms need clinical review): English + Hindi first, INR formatting (₹1,00,000), DD-MM-YYYY, IST, Indian address (state → district → PIN) | UI toggles language; PIN lookup works | Sonnet / medium |
| P1-T8 | Staging server (+ secure settings that refuse to start without secret key, JWKS, allowed hosts — review R0-09) (+ P1-T5d: `IHR_BACKCHANNEL_LOGOUT_URL` on an internal-only address served by its own backend pool, not reachable through the public proxy): India-region VM, Traefik + Let's Encrypt, Compose deploy from CI, backups (pgBackRest), NTP to NIC/NPL, 180-day logs | **One-click staging URL** with test logins | Sonnet / high |
| P1-T9 | Observability: Prometheus + Grafana + Loki, uptime alerts to you (email/WhatsApp) | Dashboard URL | Sonnet / medium |
| P1-T10 | **Terminology service** (from the gap analysis): CARE calls an external SNOMED server over plain HTTP by default. **Decided (Q-F1b): a curated SNOMED + ICD-10 + drug lookup service now**, served in the FHIR-style form CARE expects; self-hosted Snowstorm later. Includes the SNOMED CT India affiliate licence checklist for you to submit | Diagnosis/drug search works with no external calls | Opus / high |
| P1-T11 | Seed Indian hospital roles: Receptionist, Cashier, Lab Tech, Radiologist, TPA Desk, MRD, Store Keeper, Owner | Each role sees only its screens (tests) | Sonnet / medium |
| P1-T12 ✅ | Hardening before staging: fix the 29 fixable HIGH dependency vulnerabilities Trivy reports (22 backend, 7 frontend); run containers as a non-root user; `--no-install-recommends`; then make Trivy fail on fixable HIGH too | Trivy gate at HIGH is green | Opus / medium |
| P1-T14 | **Customer discovery** (Q-S2): interview guide, one-page pitch (English + Hindi), pilot letter-of-intent template, a simple tracker for 5–10 target hospitals; **you** hold the conversations; findings feed the Phase 2 scope | At least 5 interviews logged; go/no-go on Phase 2 scope with you | Opus / medium |

### Phase 2 — MVP hospital workflows (sellable to a 10–100 bed hospital)

> Scope and size of each task were adjusted after [GAP_ANALYSIS.md](GAP_ANALYSIS.md) §6. That file is the reference for what CARE already provides.

| ID | Task | Key India specifics | Effort |
|---|---|---|---|
| P2-T1 | Masters: departments, doctors (NMC / state-council reg. no.), service and tariff master, rate cards (Cash / TPA / Corporate / PM-JAY / CGHS / ECHS), ward and bed types | Multiple rate cards per service | Opus / medium |
| P2-T2 | Patient registration: UHID, demographics, Indian address, relation (S/o, W/o, D/o), ID type (no full Aadhaar number stored), MLC flag, unknown/emergency patient, photo, duplicate detection | Aadhaar must be masked; ABHA field ready | Opus / medium |
| P2-T3 | Appointments + OPD token queue (**add priority categories — CARE's queue is first-come-first-served only, GAP §8 #1**): doctor schedules, slots, walk-in tokens, queue display screen, follow-up validity rules (e.g. free revisit in 7 days) | Token-display TV page | Opus / medium |
| P2-T4 | OPD consultation EMR: vitals, complaints, history, exam, diagnosis (ICD-10 + SNOMED CT India), advice, follow-up, templates per specialty | SNOMED CT India affiliate licence (free) | Opus / medium |
| P2-T5 | e-Prescription: drug master (generic + brand), dose/frequency in Indian style (1-0-1, BD, TDS), allergy check, print in English + regional language | Generic-name prescribing | Opus / medium |
| P2-T6 | OPD billing (**incl. GST-safe invoice numbering: per facility + financial year + series, DB-locked counter, unique, ≤16 chars — CARE's is not safe, GAP §8 #3**): services, packages, discounts with approval, cash/UPI/card/split payments, receipts, refunds, credit notes, day-end cash close | GST-exempt clinical services; invoice numbering per financial year | Opus / high |
| P2-T7 | IPD admission / transfer / discharge (ADT): admission, bed board (visual ward matrix), transfers, bed-charge auto-posting, attendant pass, discharge workflow (clearances) | Room-rent 5% GST rule (non-ICU > ₹5,000/day) | Opus / high |
| P2-T8 | IPD clinical: doctor orders, nursing vitals chart, intake/output, medication administration record (MAR), nursing notes, progress notes | NABH documentation | Opus / medium |
| P2-T9 | Discharge summary (NRCeS-aligned structure) + print | Ready for ABDM M2 later | Opus / medium |
| P2-T10 | IPD billing: deposits/advances, interim bill, packages (surgery packages), final bill, TPA vs patient split, bill audit | Package inclusions/exclusions | Opus / high |
| P2-T11 | Pharmacy stores: item master, vendors, purchase order, goods receipt (GRN, with batch, expiry, MRP, GST), stock ledger, FEFO, expiry alerts | Indian GST rates on medicines post-GST 2.0 | Opus / medium |
| P2-T12 | Pharmacy dispensing (**server-side block on expired stock — CARE only filters in the UI, GAP §8 #4**): OPD sales, IPD indents/issue, returns, Schedule H/H1/X registers, NDPS register | Drugs & Cosmetics Rules registers | Opus / medium |
| P2-T13 | Lab (basic): test master + reference ranges (age/sex), order → sample collection (barcode label) → result entry → verification → PDF report with signatures | NABL report fields | Opus / medium |
| P2-T14 | Radiology (basic, no PACS yet): orders, report templates, report PDF | PCPNDT Form F for ultrasound (later task P6) | Sonnet / medium |
| P2-T15 | Insurance/TPA: payer master, pre-auth requests and approvals tracking, enhancement, claim documents, settlement + deductions | Cashless workflow | Opus / medium |
| P2-T16 | MIS dashboards: collections, census, occupancy, average length of stay (ALOS), doctor-wise revenue, lab turnaround time (TAT); export to Excel | Owner's WhatsApp daily summary (later) | Sonnet / medium |
| P2-T17a | Accounting outbox + Tally Prime XML export, with ledger mapping per tenant (ADR-004) | Tally Prime import test | Opus / medium |
| P2-T17b | ERPNext v16 + India Compliance stack (one site per tenant) + idempotent connector from the outbox (ADR-004) | Bills appear in ERPNext; re-run creates no duplicates | Opus / high |
| P2-T18 | Print engine: letterheads, A4/A5, 80 mm thermal receipts, barcode/QR | Per-tenant templates | Sonnet / medium |
| P2-T19 | **MVP end-to-end test**: patient → OPD → admit → lab → pharmacy → discharge → bill → Tally export (Playwright) | You run a full scripted day | Opus / high |

### Phase 3 — ABDM and national rails

| ID | Task | Effort |
|---|---|---|
| P3-T1 | **(You)** Company registration on ABDM sandbox, HFR facility IDs for test hospitals, HPR for test doctors; I prepare the forms and checklist | Sonnet / low |
| P3-T2 | ABDM gateway client: session tokens, callbacks, request signing; mock gateway for tests | Opus / high |
| P3-T3 | Fidelius encryption (ECDH Curve25519 + AES-GCM) library, tested against NHA reference vectors | Opus / high |
| P3-T4 | M1: ABHA creation (Aadhaar OTP / mobile), verification, ABHA card download, link to UHID | Opus / high |
| P3-T5 | M1: Scan & Share — counter QR codes → profile share → token in the OPD queue | Opus / medium |
| P3-T6 | FHIR bundle builder for NRCeS IG 6.5.0 profiles (OPConsultation, Prescription, DiagnosticReport, DischargeSummary, …) + HAPI FHIR validator in CI | Opus / high |
| P3-T7 | M2 (Health Information Provider): care-context linking, user-initiated linking, discovery, consent notification, encrypted data push | Opus / high |
| P3-T8 | M3 (Health Information User): consent requests, fetch, decrypt, render external records in the chart | Opus / high |
| P3-T9 | **NHCX claims (priority — Q-C4):** participant onboarding, FHIR claim bundles (CoverageEligibility, pre-auth, Claim), signing/encryption, async callbacks | Opus / high |
| P3-T10 | **PM-JAY / state schemes (priority — Q-C4):** HBP package master, beneficiary verification steps, pre-auth with document checklist, claim submission via the NHCX-PMJAY route where available (TMS portal otherwise), settlement tracking | Opus / high |
| P3-T11 | Security audit (WASA) readiness: OWASP ASVS checklist, VAPT by a CERT-In-empanelled auditor **(you engage)**, fix findings | Opus / high |
| P3-T12 | DHIS incentive tracker (eligible transactions vs the 100/month threshold) | Sonnet / medium |

### Phase 4 — Compliance and security hardening (some items run in parallel with Phases 2–3)

| ID | Task | Effort |
|---|---|---|
| P4-T1 | DPDP: privacy notice + purpose-wise consent capture (multi-language), withdrawal, data-principal requests (access/correction/erasure with medical-record retention override) | Opus / high |
| P4-T2 | Breach-response workflow: 72 h report to the Data Protection Board, 6 h to CERT-In, runbook + templates | Sonnet / medium |
| P4-T3 | Retention engine: per-record-type retention (MCI 2002 ≥ 3 yrs; PCPNDT 2 yrs; Schedule H1 3 yrs; IPD/medico-legal longer) | Opus / medium |
| P4-T4 | Encryption at rest (Postgres disk + backups), TLS 1.3 everywhere, secrets management | Opus / high |
| P4-T5 | Role-based access by care team ("break-the-glass" with reason and audit) | Opus / high |
| P4-T6 | Legal pack **(you + lawyer)**: SaaS terms, Data Processing Agreement (we act as processor for the hospital), privacy policy; I draft templates | Opus / medium |
| P4-T7 | **(Gate: before the first pilot with real data — ADR-007)** Disaster recovery (DR): automated restore test, second-region replica, recovery time/point targets (RTO/RPO) documented | Sonnet / high |
| P4-T8 | Per-tenant database role switched with `SET ROLE` alongside the schema, so a SQL-level escape in one hospital cannot read another (TENANCY_DESIGN §6.10) | Opus / high |

### Phase 5 — Diagnostics depth

| ID | Task | Effort |
|---|---|---|
| P5-T1 | Orthanc 1.13 + PostgreSQL + DICOMweb + authorization plugin + orthanc-auth-service; tenant labels | Opus / high |
| P5-T2 | OHIF 3.13 via OIDC; launch from the patient chart with study context | Opus / medium |
| P5-T3 | DICOM modality worklist (MWL) from radiology orders; modality onboarding guide | Opus / medium |
| P5-T4 | Radiology reporting UI linked to studies; teleradiology access | Opus / medium |
| P5-T5 | Analyzer bridge (Python service): ASTM E1381/E1394 + HL7 v2 over serial/TCP, on-site edge agent, bidirectional (host query) | Opus / high |
| P5-T6 | Lab QC: Westgard rules (1-2s, 1-3s, 2-2s, R-4s, 4-1s, 10x), Levey-Jennings charts, run blocking | Opus / medium |
| P5-T7 | Delta checks + critical-value alerts | Opus / medium |
| P5-T8 | (Optional) OpenELIS 3.3 integration for standalone large labs | Opus / high |

### Phase 6 — Specialty and enterprise modules

**Priority order (Q-B5):** 1. OT + WHO checklist · 2. ICU · 3. Maternity · then the rest by customer demand.

Each module listed below is roughly 2–4 tasks:

- **Emergency and triage:** ESI or Manchester triage; unknown-patient registration; medico-legal case (MLC) registers.
- **Operating theatre:** OT scheduling, pre-anaesthesia checkup (PAC), WHO Surgical Safety Checklist (enforced), implant serial and batch numbers, CSSD tray linkage.
- **ICU:** flowsheets, ventilator/inotrope/ABG charting; NEWS2/qSOFA/SOFA (deterministic).
- **Dialysis:** dialysis sessions, vascular access, dialyzer reuse, virology screens (NDP guidelines).
- **Maternity:** antenatal care, partograph, newborn APGAR, immunisation; birth registration data.
- **IVF/ART:** stimulation and folliculometry, embryology lab, cryopreservation inventory, ART Act registers, donor anonymity (separate encrypted vault).
- **PCPNDT:** Form F capture and register.
- **Blood bank:** donors, transfusion-transmitted infection (TTI) screening, components with shelf lives, cross-match, issue, NBTC/eRaktKosh export.
- **CSSD:** tray barcodes, cycles, biological and chemical indicators, recall trace.
- **Biomedical engineering:** asset register, planned preventive maintenance (PPM), breakdown tickets, mean time to repair / between failures (MTTR/MTBF), annual and comprehensive maintenance contracts (AMC/CMC).
- **Other operations:** dietary, housekeeping, laundry, bio-medical waste register (BMW Rules 2016).
- **NABH quality:** quality-indicator engine, incident reporting with root-cause analysis and corrective/preventive actions (RCA/CAPA), and adverse drug reaction (ADR) reporting.
- **MRD:** medical-records department (MRD) archive and file tracking.
- **ERPNext v16 back office (optional):** General ledger (GL), procurement, HR + India Payroll (PF/ESI/PT, Labour Codes), assets, and GST e-invoice through India Compliance.

### Phase 7 — AI layer — ⏸ DEFERRED ([ADR-005](adr/005-ai-deferred.md))

Kept for reference. Nothing here is built until you re-approve it.

| ID | Task | Effort |
|---|---|---|
| P7-T1 | LLM gateway: Claude via AWS Bedrock India profile (`in.anthropic.*`), per-tenant opt-in, PHI minimisation, logging; abstraction to swap to self-hosted vLLM 0.33 (Gemma 4 / Qwen3.8) later | Opus / high |
| P7-T2 | MCP server (FastMCP 4.1, spec 2026-07-28, Streamable HTTP, OAuth via Keycloak): read tools (patient summary, bed availability, PACS metadata) | Opus / high |
| P7-T3 | MCP draft-only write tools → "Provisional – Agent Drafted" status requiring clinician sign-off | Opus / high |
| P7-T4 | Ambient scribe: audio capture → ASR (Sarvam / IndicConformer, Q-F2) → SOAP draft → clinician edits → save | Opus / high |
| P7-T5 | Coding assist: ICD-10/SNOMED suggestions (Snowstorm 11 terminology server) | Opus / medium |
| P7-T6 | Drug-interaction checks (needs a licensed dataset — Q-F3) | Opus / medium |
| P7-T7 | Radiology AI (MONAI) — **deferred**; needs GPU + CDSCO assessment | — |

### Phase 8 — Patient engagement and communications

| ID | Task | Effort |
|---|---|---|
| P8-T1 | Notification service: WhatsApp Cloud API (utility templates, INR billing), SMS via a DLT-registered provider, email via SES Mumbai | Opus / medium |
| P8-T2 | Appointment reminders, lab-report-ready links (short-lived signed URLs), bill/receipt delivery | Sonnet / medium |
| P8-T3 | Patient portal / PWA: appointments, reports, bills, ABHA link | Opus / medium |
| P8-T4 | (Optional) Chatwoot inbox for front desk | Sonnet / medium |
| — | Nextcloud / Collabora / Matrix / Stalwart from the blueprint — **deferred / out of SaaS scope** (hospitals use their own Google/Microsoft accounts and WhatsApp). Revisit for an on-prem enterprise edition. | — |

### Phase 9 — Commercial SaaS readiness

| ID | Task | Effort |
|---|---|---|
| P9-T1 | Plans and metering (per bed / per doctor / modules), subscription billing via Razorpay (UPI AutoPay e-mandates), GST invoices for our own SaaS fees | Opus / medium |
| P9-T2 | Self-serve onboarding wizard + data import (Excel templates for patients, tariffs, stock opening balances) | Opus / medium |
| P9-T3 | Marketing site + demo tenant reset nightly | Sonnet / medium |
| P9-T4 | Support desk + in-app help + training videos | Sonnet / low |
| P9-T5 | Status page, SLAs, on-call runbooks | Sonnet / medium |
| P9-T6 | NABH Digital Health Standards software certification (after 3 live hospitals + ABDM M3) **(you apply)** | Sonnet / medium |
| P9-T7 | Scale-out: k3s/RKE2 + CloudNativePG 1.30 + SeaweedFS, horizontal workers | Opus / high |

---

## 7. Test strategy

- **Unit:** pytest (backend) and vitest (frontend). Target ≥ 80% coverage on our plugins; billing, tenancy and crypto must have 100% of branches covered.
- **Integration:** Docker Compose test stack, with a contract test for each external rail. ABDM, NHCX, WhatsApp and SMS are mocked.
- **End-to-end:** Playwright, with scripted hospital "day-in-the-life" flows, run on every PR against ephemeral stacks.
- **Security:** Trivy (images), Bandit/Semgrep (code), OWASP ZAP baseline on staging, a dependency audit in CI, and an annual external VAPT.
- **Tenancy isolation suite:** cross-tenant access attempts on every endpoint. It runs in CI and must stay green.
- **Compliance checks:** FHIR bundles validated against the NRCeS IG, and GST calculations checked against golden test cases.
- **You (UAT):** a one-click URL, test users from the `TEST_USERS.md` file, and a short click-script per task.

## 8. Risks

| Risk | Mitigation |
|---|---|
| CARE upstream changes or goes quiet | Pin commits; our code lives in plugins; sync monthly; we can sustain the fork (MIT) |
| ABDM certification timelines | Start the sandbox paperwork early (P3-T1 can run during Phase 2) |
| 6.7 GB dev machine | Low-memory CARE setup works (P0-T5); RAM upgrade still recommended before heavy Phase 1–2 work |
| Regulatory drift (DPDP May 2027, CDSCO, NABH 2nd-edition Digital Health draft) | `docs/COMPLIANCE.md` register reviewed quarterly |
| AGPL components | Use unmodified; call over network APIs; keep a licence register (`docs/LICENSES.md`) |
| Drug-interaction data licensing | Licensed vendor or curated in-house list (Q-F3) |
| Solo founder bandwidth | Strict MVP scope; no Phase 6+ until the MVP is sold to 1–3 pilot hospitals |
