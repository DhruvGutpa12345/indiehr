# Questions for you (the "grilling")

## ✅ Answers received (2026-11-03)

| Q | Answer | Plan impact |
|---|---|---|
| Q-A1 | **CARE-based** (Option B) | Fork CARE; build ABDM ourselves |
| Q-A2 | **Schema per hospital** | ADR-002; django-tenants-style tenancy |
| Q-A3 | **Online-only** for MVP | No offline work |
| Q-A4 | **Web app only** (responsive) | No native apps in MVP |
| Q-A6 | **Proprietary** | Keep CARE MIT notices; our code closed |
| Q-B1 | **10–100 bed private hospitals, Tier 2/3** | MVP = Phase 2 as planned |
| Q-B3 | **English + Hindi** | P1-T7 |
| Q-B6/B7 | Ask later; per-bed pricing as working assumption | — |
| Q-C1 | **Both Tally export and ERPNext v16 connector from the start** | P2-T17 grows into two tasks; ERPNext needs MariaDB 11.8, so it runs as a separate stack |
| Q-C4 | **Full PM-JAY / NHCX integration early** | NHCX and PM-JAY are now P3-T9/P3-T10, right after ABDM M1–M3 |
| Q-D1 | **Registered Pvt Ltd/LLP with GSTIN exists** | ABDM sandbox paperwork (P3-T1) can start during Phase 2 |
| Q-D4/D5 | **India-only data; masked Aadhaar + ABHA only** | As planned |
| Q-D6 | **No AI for now** | Phase 7 deferred ⏸ |
| Q-E1 | **Upgrade RAM to 32 GB** | Not yet installed; P0-T5 ran in a low-memory setup instead |
| Q-E2 | **Done** — user in docker group, verified after restart | P0-T2 only needs the Docker upgrade |
| Q-E3 | **Private GitHub repo** | GitHub connector needs reconnecting, or `gh auth login` |
| Q-E4 | **Cloud: decide later** (before P1-T8) | Local-only until staging |
| Q-F3 | **Own curated drug list** for MVP | P2-T5 |
| Q-G1/G3 | **Synthetic test users + screenshots + URL** | As planned |
| Q-G2 | **Commit directly to main** | No PR workflow |
| Q-F1b | **Curated SNOMED/ICD-10 service now; full Snowstorm later** (2026-11-03) | P1-T10 builds the curated service |
| Q-F1 | **Yes:** prepare the SNOMED CT India affiliate licence application in the company's name; ICD-10 for diagnosis coding | Checklist added to P1-T10 |
| Q-A5 | **Keep "IndieHR"** as the working name | Final brand later |
| Q-B5 | **Top specialties after MVP: OT + WHO checklist, ICU, Maternity** | Phase 6 order |
| Q-C2 | **Payroll/HR later via ERPNext** (HRMS + India Payroll) | Phase 6 ERPNext back office |
| Q-C3 | **e-Invoice later** (via ERPNext India Compliance) | Not in MVP |
| Q-C5 | **SaaS billing gateway: decide later** | Before P9-T1 |
| Q-E5 | **Kubernetes at about 10 hospitals** | As in ADR-003 |
| Q-B2 / Q-F5 | **No pilots yet**; build standard Excel import templates | P9-T2 |
| Q-F4 | **The product owner reviews clinical content** | Clinical templates are sent to you for review |
| Q-E6 / Q-D2 / Q-D3 | **Email alerts to you**; auditor shortlist and legal templates when those phases arrive | P1-T9, P3-T11, P4-T6 |
| Q-S1 | **Yes:** minimal, logged edits to CARE's data model where tenancy needs them (2026-11-03) | P1-T0 may patch `User` links; each patch in UPSTREAM_PATCHES.md |
| Q-S2 | **Yes:** customer discovery before Phase 2 (5–10 hospitals, aim for one pilot LOI) | New task P1-T14 |
| Q-S3 | **No outage kit**; rely on the hospital's backup internet | Accepted risk (49% of buyers cite poor internet); revisit after pilots |
| Q-S4 | **Stop** the non-IndieHR services on the dev PC (user runs the steps) | Instructions given 2026-11-03 |
| Q-T0 | **Tenant = legal entity (GSTIN holder)**; one tenant may have several facilities/branches (2026-11-06, owner accepted defaults) | Hospital model represents the entity; ABDM routes and switcher point to a facility within a tenant |
| Q-T1 | **Short announced maintenance window** (~02:00 IST) for upgrades | TENANCY_DESIGN §7.2 |
| Q-T2 | **Separate staff record per tenant** for multi-hospital doctors | §4 |
| Q-T3 | **Presigned URLs** for logos/covers/avatars | §6.8, logged patch |
| Q-T4 | **One Keycloak client per tenant** | §4.3, created at onboarding |
| Q-T5 | **No in-tenant superuser** for v1 | Hospital-wide forms come from packs |
| Q-T6 | **Cell cap 100 tenants** initially | Revisit after P1-T1c numbers. **P1-T1c measured (2026-11-06):** keep 100. Per-hospital dumps step up between 125 and 150 hospitals; releases take about 30–60 s at 100. **Confirmed by the owner 2026-11-06: cap = 100 hospitals per cell**, to be reviewed on staging hardware (P1-T8) and with real data. See docs/reviews/p1-t1c/scale-results.md |
| Q-T7 | **Support access = break-glass only** | §4.9 |
| Q-T8 | **No inter-hospital referral in v1** (works between facilities of one tenant) | Redesign on ABDM later |
| Q-T9 | **Clear cached data at logout** (frontend patch) | §6.9 |
| Q-T10 | **Packs auto-update unedited rows**; edited rows need admin OK | §5 |
| Q-T11 | **Domain decided at P1-T8** (staging) | — |
| Q-K1 | **Job roles live in CARE only** (2026-11-06); Keycloak holds one `member` role per hospital | ADR-008, P1-T3 |
| Q-K2 | **MFA for all staff** (2026-11-06), enforced by Keycloak and checked by the backend | ADR-008 |
| Q-K3 | **Passwords: 12+ characters, last 5 not reused, no username/email, common-password block list**, no composition rules (2026-11-06) | ADR-008 |
| Q-K4 | **Sessions: 30 min idle, 12 h maximum**; access tokens 5 min (2026-11-06) | ADR-008 |
| Q-K5 | **Staff tokens kept by the backend**, HttpOnly cookie in the browser (2026-11-07; closes R0-20) | ADR-009, P1-T4a |
| Q-K6 | **Sign-out and the 30-min idle time-out end sign-in at all hospitals** (2026-11-07) | ADR-009 |
| Q-K7 | **Keycloak only for staff**, in development too: CARE's password login, its MFA, password reset and the Django admin are off on hospital addresses (2026-11-07) | ADR-009 |
| Q-K8 | **P1-T4 split** into P1-T4a (backend) and P1-T4b (web app + click-through) (2026-11-07) | PLAN |
| Q-K9 | **P1-T5 split into six tasks** T5a…T5f (2026-11-07) | PLAN |
| Q-K10 | **Operator console = simple server-rendered pages**, operators sign in through a separate operator realm (2026-11-07) | P1-T5b |
| Q-K11 | **First admin and all staff get a one-time setup e-mail** (confirm e-mail, choose password, set up authenticator); an existing account gets a "join this hospital" confirmation instead (2026-11-07) | P1-T5c/T5e |
| Q-K12 | **Starter data = the minimum**: roles, code lists, profile, state + district, one facility, first admin; departments/price lists/GST later as reviewed packs (2026-11-07) | P1-T5a |
| Q-G4 | **`/goal` and `/advisor` = second-opinion reviews:** an independent Opus agent reviews each finished phase against the plan | Working agreement §5 |

**Still open:** Q-B4 (final MVP module list: assumed as in PLAN.md Phase 2) and Q-B6/B7 (pricing, timeline). Q-F2 is moot while AI is deferred.

---

Answer inline: write under each question, or reply in chat with `Q-A1: B, Q-A2: ...`.
Each question has a **recommended default (⭐)**. If you are happy with it, just write "default".
Questions marked 🔴 **block the next task**. The rest can wait, but answering early prevents rework.

---

## A. Product and architecture

**Q-A1 🔴 Core platform.**
- **A** Compose the blueprint as written (OpenMRS/Ozone + ERPNext + OpenELIS + …).
- **B ⭐** CARE-centred SaaS (fork CARE, add multi-tenancy, ABDM, India modules).
- **C** Frappe-centred (Marley Health + ERPNext + India Compliance).
- **D** Decide after hands-on spikes of B and C (adds about 1 task).

See PLAN.md §3.

**Q-A2 🔴 Tenancy isolation level.**
- **schema-per-hospital ⭐**
- database-per-hospital (stronger isolation, more ops)
- shared tables with row-level security (cheapest, riskiest)

Some big customers may later demand their own database or on-prem. Should we plan for "dedicated tenant" as a paid tier?

**Q-A3 Deployment models to support eventually.**
- SaaS only ⭐ for the first 12 months
- SaaS + on-prem/hybrid for large hospitals
- Must it work **offline** during internet outages (common in Tier 2/3)? Options:
  - online-only;
  - read-only offline cache;
  - full offline-first (very expensive).

**Q-A4 Clients.**
- Web app (responsive, works on tablets) only ⭐ for MVP
- also native Android app for doctors/nurses
- also patient mobile app

**Q-A5 Product name / brand.** The folder is called "IndieHR". Is that the product name? What does "HR" stand for (Health Records? Hospital?).
- Do you own a domain (e.g. `indiehr.in`)?
- We cannot use "ERPNext", "Frappe" or "CARE" in the brand (trademark policies).

**Q-A6 Licensing of OUR code.**
- Proprietary/closed ⭐ (allowed on top of MIT CARE)
- open-core (public core, paid modules)
- fully open source

## B. Market and scope

**Q-B1 🔴 First customer segment** (shapes the MVP):
- 10–100 bed private hospitals / nursing homes ⭐
- single-doctor and poly-clinics
- diagnostic labs
- specialty chains (IVF, dialysis, eye, dental)
- 100–300 bed multi-specialty
- government

**Q-B2 Do you already have pilot hospital(s)?**
- Name / bed count / city / specialties.
- What software do they use today?
- Who would be our champion there?

**Q-B3 Geography and languages.** Which states first? Which UI languages beyond English?
- ⭐ Hindi first, then add Marathi, Tamil, Telugu, Kannada, Bengali, Gujarati by demand.

**Q-B4 MVP module list — confirm or edit** (PLAN.md Phase 2):
- registration, appointments/tokens, OPD EMR, e-prescription, OPD billing;
- IPD ADT, nursing, discharge summary, IPD billing;
- pharmacy stores and dispensing, basic lab, basic radiology;
- TPA/insurance tracking, MIS, Tally export, printing.

Anything to add or drop?

**Q-B5 Specialty modules you most care about** (blueprint lists OT, ICU, cath lab, dialysis, maternity, IVF/ART, blood bank, CSSD, biomedical). Rank your top 3.

**Q-B6 Pricing model idea?**
- per bed/month ⭐
- per doctor/month
- per module
- per transaction
- freemium for clinics

This affects metering design (Phase 9).

**Q-B7 Timeline / target date** for the first pilot go-live?

## C. Finance, billing and insurance

**Q-C1 🔴(before P2-T17) Hospital accounting.**
- Export to **Tally** ⭐ (what most Indian hospitals' accountants use)
- built-in **ERPNext v16** back office (full GL, procurement, HR/payroll; heavier, adds MariaDB)
- both

**Q-C2 Payroll/HR for hospital staff** in scope?
- later via ERPNext HRMS + India Payroll ⭐
- not in scope

**Q-C3 GST e-invoicing** needed for hospital bills? Most hospital clinical services are exempt, but pharmacy/B2B/corporate invoices can need IRN if the hospital's turnover exceeds ₹5 crore.

Do target hospitals need e-invoice/e-way bill from day one?
- ⭐ "later"

**Q-C4 Insurance.**
- Is PM-JAY / state scheme (e.g. MJPJAY, Aarogyasri, CMCHIS) support required in the MVP?
- Which TPAs are most common for your pilots?

**Q-C5 Our own SaaS billing.**
- Razorpay ⭐ / Cashfree / PhonePe
- Do you have a registered company (Pvt Ltd/LLP) with GSTIN? This is needed for ABDM, payment gateways and WhatsApp Business verification.

## D. Compliance and legal

**Q-D1 🔴(before Phase 3) Legal entity.**
ABDM sandbox and integrator registration, the security audit (WASA) and NABH software certification all need a registered organisation.
- Do you have one? Name/type?
- Who is the authorised signatory?

**Q-D2** Do you have (or will you engage) a **CERT-In empanelled auditor** for the security audit (VAPT/WASA) needed for ABDM go-live? (I can shortlist.)

**Q-D3 Lawyer** for SaaS terms, the Data Processing Agreement and the privacy policy? I draft; a lawyer reviews ⭐.

**Q-D4 Aadhaar.** Confirm we **never store full Aadhaar numbers** (only masked, plus the ABHA number/address) ⭐. Any requirement to do otherwise?

**Q-D5 Data residency.** Confirm **all data stays in India** (Indian cloud region, Indian backups) ⭐.

**Q-D6 AI with patient data.**
- Is it acceptable to send de-identified or minimised clinical text to **Claude on AWS Bedrock with India-only routing** (Mumbai/Hyderabad), with per-hospital opt-in ⭐?
- Or must AI be 100% self-hosted (requires GPU servers, roughly ₹1–3 lakh/month)?

## E. Engineering environment

**Q-E1 🔴 Dev machine.** This PC has **6.7 GB RAM (~1 GB free)**. CARE dev needs ≥ 16 GB; the full stack needs 32 GB. Options:
- **upgrade RAM to 32 GB ⭐** (i3-12100 board supports it; about ₹6–8k)
- rent a cloud dev VM in India (about ₹4–8k/month, 8 vCPU/32 GB)
- both

Until then I can only run small pieces.

**Q-E2 🔴 Docker access.** Your user is not in the `docker` group. You will need to run (once):

```bash
sudo usermod -aG docker $USER
```

then log out and back in. You may also allow me to upgrade Docker to 29.x / Compose v5 (needs sudo — you run the commands I give). OK?

**Q-E3 🔴 Source control.**
- Create a **private GitHub repo** ⭐ (org name?) — or GitLab/other?
- The GitHub connector in this app failed to connect ("Authorization header badly formatted"). Please reconnect it, or install `gh` and run `gh auth login`.

**Q-E4 Cloud for staging/production.**
- AWS Mumbai ⭐ (also gives Bedrock India for AI)
- Azure Pune
- GCP Mumbai
- E2E Networks / Yotta (Indian, MeitY-empanelled, cheaper)

Do you have an account? Monthly budget for staging (about ₹3–6k) and production?

**Q-E5 Kubernetes now or later?** ⭐ Single VM with Docker Compose for staging/pilots; k3s when we exceed about 10 tenants. Agree?

**Q-E6 Email / alerts for you.** Where should I send ops alerts (email, WhatsApp, both)? Can I use your email for staging alerts?

## F. Clinical content and data

**Q-F1 Terminology.** OK to register for the free **SNOMED CT India affiliate licence** (via NRCeS) in your company's name? ICD-10 for diagnosis coding by default (ICD-11 later)? ⭐ yes

**Q-F2 Speech-to-text for the ambient scribe.**
- Sarvam API ⭐ (best Hinglish, paid, Indian company)
- self-hosted AI4Bharat IndicConformer (free, needs GPU)
- Bhashini (government)

**Q-F3 Drug database.** There is no open, commercially licensed Indian drug-brand + interaction dataset.
- License a commercial Indian drug DB (e.g. CIMS/MIMS India, 1mg/Medindia APIs) — cost
- curate our own generic list with a small interaction set ⭐ for the MVP
- skip interaction checks in the MVP

**Q-F4 Clinical templates.** Do you have doctors who can review specialty OPD templates, discharge summary formats and nursing charts? Who is our clinical advisor?

**Q-F5 Existing data migration.** Will pilot hospitals need patient/billing history imported from their current software? Which software?

## G. Working style

**Q-G1 Review cadence.** After each task I stop and wait for your "proceed". Do you also want a short demo video/GIF of UI tasks? ⭐ screenshots + URL.

**Q-G2 Commits.**
- I commit to a feature branch per task and open a PR you merge ⭐
- I commit directly to main

**Q-G3 Test users.** I'll create synthetic users like `admin@demo-hospital.test` with generated passwords stored in `deploy/seed/TEST_USERS.md` (git-ignored on staging/production). OK? ⭐

**Q-G4 Slash commands you listed** (`/goal`, `/advisor`): these are not available in this Claude Code session. What did you expect them to do? I can set up equivalents:
- a project goal statement in CLAUDE.md;
- a "second opinion" Opus review agent at each milestone.
