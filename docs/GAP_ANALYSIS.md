# Gap analysis: blueprint and MVP vs. CARE (as imported)

- **Task:** P0-T7 · **Date:** 2026-11-03
- **Code examined:** `apps/backend` @ `67a46185eb98` and `apps/frontend` @ `57205b897804` (see `upstream.lock`)
- **Method:** three read-only code surveys (backend clinical; billing/inventory/plugins/infra; frontend), with key claims spot-checked by hand (`grep` evidence noted below). Nothing was run, because CARE needs the 32 GB RAM upgrade (P0-T5). Items marked **(verify in P0-T5)** were inferred from code and must be confirmed in the running app.

**Legend:**
- ✅ **Use** — usable as-is or with configuration only
- 🟡 **Extend** — the base exists; we add fields, rules or screens through `ihr_*` plugins or extensions
- 🔴 **Build** — nothing usable exists
- ⏸ **Defer** — out of MVP scope (ADR-000 / ADR-005)

**Size:**
- **S** — under 1 task
- **M** — 1–2 tasks
- **L** — 3 or more tasks

---

## 1. Headline

**CARE is a strong base for clinical work, scheduling and inventory. It is weak on Indian commercial workflows.** 22 of the 28 MVP areas have a usable base in CARE (7 use as-is, 15 extend). The big builds are:

- tenant isolation;
- insurance/TPA (no models exist);
- surgery and treatment packages;
- the pharmacy purchase cycle (purchase order → goods receipt → vendor);
- statutory drug registers;
- MIS reports;
- ABDM.

| Verdict | MVP areas (§3, 28 rows) | Later-phase modules (§4, 17 rows) |
|---|---|---|
| ✅ Use | 7 | 0 |
| 🟡 Extend | 15 | 5 |
| 🔴 Build | 6 | 10 |
| ⏸ Defer | 0 | 2 |

## 2. Will CARE accept "one schema per hospital" (ADR-002)? Mostly yes

| Check | Finding | Evidence | Impact |
|---|---|---|---|
| Raw SQL | **None** outside migrations and tests | `grep .raw( / connection.cursor / RawSQL` → 0 | 🟢 Biggest risk is absent |
| Cache | 55 call sites, all through Django's cache (django-redis) | `config/settings/base.py` CACHES | 🟢 One `KEY_FUNCTION` setting makes every key tenant-aware |
| Celery | 8 task modules, plus 3 periodic jobs (token-slot, OTP, upload cleanup) | `care/emr/tasks/` | 🟡 Tasks must carry the tenant and periodic jobs must loop over tenants (tenant-aware Celery) |
| Migrations | 295 | `find care -path '*migrations*'` | 🟡 Run once per schema, so upgrade time grows with tenant count. Use parallel migration in P1-T1 |
| Postgres-specific fields | Used in 195 places (JSONField/ArrayField) | — | 🟢 Fine on Postgres |
| Data meant to be shared | `User`, `Organization` (govt geography), `ValueSet`, `ProductKnowledge` with no facility, `Patient` (global by design, with a cross-facility edit flag) | Patient model has no facility FK on itself | 🟡 Decide what lives in `public` vs. the tenant schema (P1-T1) |
| Built-in "tenant" | Only `Facility` / `FacilityOrganization` (soft, permission-based) | `care/security/authorization/*` | Within one tenant: one Facility per branch. Isolation between tenants comes from schemas |

**The open design point for P1-T1 is users.** CARE's `User` is global and holds role memberships. Our plan (ADR-002) is one Keycloak identity per person with per-tenant staff records. P1-T1 must choose:

- **(a)** keep `User` in the shared `public` schema and its memberships in the tenant schema; or
- **(b)** a full user copy per tenant.

**(a)** fits CARE's code better.

## 3. Module-by-module: MVP (Phase 2 and platform)

| # | Module | What CARE has today | Verdict | What we add | Size |
|---|---|---|---|---|---|
| 1 | **Login and SSO** | JWT login; TOTP MFA + backup codes; patient OTP portal. **No OIDC/SSO** | 🟡 | Keycloak OIDC login (P1-T4); keep TOTP | M |
| 2 | **Roles** | Doctor, Nurse, Staff, Pharmacist, Admin, Facility Admin, Volunteer; custom roles allowed | 🟡 | Seed Receptionist, Cashier/Billing, Lab Technician, Radiologist, TPA Desk, MRD, Store Keeper, Owner (MIS) | S |
| 3 | **Audit trail** | Model-change + request logging, **off by default**, writes to a logger only, no "who viewed" trail | 🟡 | Turn it on; append-only hash-chained DB sink per tenant; read-access audit (P1-T6) | M |
| 4 | **Branding per hospital** | Logo and title are fixed **at build time**; `REACT_CARE_URL_MAP` maps an origin to a backend | 🔴 | Runtime branding by subdomain: logo, name, colours, letterhead (P1-T2) | M |
| 5 | **Languages** | i18next. **Hindi has 795 of 6,877 keys (11.5%)**; also ta/kn ~11%, ml 41%, mr 1% | 🟡 | Complete Hindi for MVP screens (~6,000 strings; machine draft + human review) (P1-T7) | M |
| 6 | **Indian locale** | ₹ / `en-IN` money formatting hard-coded; +91 phone default; pincode field; state/district through a government organization tree loaded from CSV | 🟡 | Load India's Local Government Directory (LGD) states and districts; pincode → district lookup; check DD-MM-YYYY everywhere | S |
| 7 | **Terminology (diagnosis/drug/allergy search)** | **Depends on an external SNOMED server (Snowstorm)**. Default `SNOWSTORM_DEPLOYMENT_URL=http://165.22.211.144/fhir`, a third-party IP over plain HTTP. **No ICD-10** | 🔴 | Our own terminology service in India: either self-hosted Snowstorm 11 + SNOMED CT India (needs Elasticsearch, about 4–8 GB RAM), or a lighter curated SNOMED + ICD-10 + drug lookup. **New task P1-T10, decision Q-F1b** | M–L |
| 8 | **Patient registration** | Rich patient model; age-only entry; **configurable identifiers** (regex, unique, auto-maintained); search by phone or identifier | 🟡 | UHID generator; ABHA identifier slot; masked-Aadhaar rule; **MLC flag; unknown/emergency patient; relation (S/o, W/o, D/o); photo; duplicate detection and merge** | M |
| 9 | **Appointments and OPD tokens** | **Strong**: schedules, slots, bookings, token queues and sub-queues (counters), categories, revisit-days with a separate revisit charge, public booking, token print | ✅ | Queue display TV page; check walk-in vs. appointment priority (verify in P0-T5) | S |
| 10 | **OPD consultation EMR** | Questionnaires → structured observations, conditions, allergies; notes; consent; files; favourites | 🟡 | Specialty OPD templates (built as questionnaires); ICD-10 coding next to SNOMED (depends on #7) | M |
| 11 | **e-Prescription** | MedicationRequest + prescription header + approval status; prescription print preview | 🟡 | Indian dose shorthand (1-0-1, BD, TDS); generic-name-first display; Hindi/regional print; curated drug master (Q-F3) | M |
| 12 | **OPD billing** | **Substantial**: accounts, charge-item tariffs (base, surcharge, discount, tax components), invoices (lock, cancel, refund), payments, advances/deposits, credit notes; per-facility invoice-number formula; auto-charge from orders, appointments, dispenses and bed links | 🟡 | **Payment modes: UPI, NEFT/IMPS, wallet, cheque** (FHIR codes only today); **GST 2.0 slabs** (CARE presets still have 12%; now 5/18/40); HSN/SAC codes; hospital GSTIN on invoices; discount approval workflow; day-end cash close; financial-year invoice series | M |
| 13 | **IPD admit / transfer / discharge** | Encounter classes (inpatient, emergency …), admit source, discharge disposition incl. LAMA/DAMA and death; bed history; bed status | 🟡 | Guided admit and discharge workflow screens (clearances); transfer action; attendant pass; death/MLC handling | M |
| 14 | **Bed board and bed charges** | Location tree (ward → room → bed), occupancy history, availability dashboard | 🟡 | Bed category → tariff link; **nightly auto-posting of bed-day charges** (client-driven today); **5% GST rule for non-ICU rooms over ₹5,000/day** | M |
| 15 | **IPD nursing** | Vitals as observations; medication administration record (MAR) with print; notes; care team | 🟡 | Nursing chart views (vitals grid, intake/output balance); shift handover | M |
| 16 | **Discharge summary** | **Report template engine** (HTML → PDF via WeasyPrint) with a `discharge_summary` report type and data points | ✅ | A default discharge-summary template shaped like the NRCeS structure | S |
| 17 | **IPD billing and packages** | Interim invoices possible; deposits; **no package model** | 🔴 | Package model (inclusions, exclusions, caps, overrun rules); TPA vs. patient split (with #20) | L |
| 18 | **Pharmacy stores** | Product knowledge (drug master), batch-level products with expiry and purchase price, stock per location, requests between stores, deliveries (incl. supplier), returns | 🟡 | **Purchase order → goods receipt → vendor/supplier invoice**; MRP/PTR/margin; stock ledger; **FEFO picking**; expiry alerts; GST input fields | L |
| 19 | **Pharmacy dispensing** | Dispense orders linked to prescription and inventory, auto-billed, print | 🟡 | **Schedule H / H1 / X and NDPS registers** (none today); OTC counter sale (patient optional) | M |
| 20 | **Insurance / TPA / cashless** | **Nothing** (no coverage, payer, claim or pre-auth models); only `issuer_type=insurer` on payments | 🔴 | Payer and TPA master, panel tariffs, pre-auth and enhancement, claim documents, settlement and deductions; base for NHCX/PM-JAY | L |
| 21 | **Lab (basic)** | **Good FHIR flow**: order → specimen (accession no.) → results → diagnostic report; test catalogue with reference ranges; barcode/QR labels; report print | ✅ | NABL fields on report; age/sex reference ranges (verify in P0-T5); doctor signature | S |
| 22 | **Radiology (basic)** | No radiology module; possible as an order + report template + file | 🟡 | Radiology order category, report templates, PCPNDT flag for ultrasound | M |
| 23 | **MIS dashboards** | Document reports only; **no MIS** (collections, census, occupancy, length of stay, turnaround time) | 🔴 | MIS plugin: SQL views per tenant + dashboard + Excel export; owner daily summary | L |
| 24 | **Accounting handoff** | Nothing | 🔴 | Accounting outbox → Tally XML and ERPNext connector (ADR-004) | L |
| 25 | **Printing** | Many print views (Rx, invoice, MAR, dispense, lab, appointment slips, purchase orders); template builder | ✅ | Per-tenant letterheads; 80 mm thermal receipt layout | S |
| 26 | **Patient portal** | OTP login, public appointment booking | ✅ | Branding; ABHA link (Phase 3) | S |
| 27 | **Infra fit** | Python 3.13, Django 6.0, Celery 5.6, Postgres 17, Redis 8, S3-compatible storage; React 19 + Vite 8 + TS 6 + Tailwind 4; Playwright end-to-end tests | ✅ | Postgres 18 check; replace bundled MinIO-like storage with SeaweedFS/S3 (ADR-003) | S |
| 28 | **Plugins** | **Backend:** pip-installed Django apps at `api/<plug>/`, plus JSON-schema extension fields on 9 resources. **Frontend:** Module Federation remotes, 20+ UI slots (registration form, encounter actions, invoice payment options, …), runtime plugin config | ✅ | Our `ihr_*` plugins use both. Upstream patches only where no slot exists | — |

## 4. Module-by-module: rest of the blueprint (Phases 3–8)

| Module | CARE today | Verdict | Notes |
|---|---|---|---|
| ABDM M1–M3, Scan & Share, Fidelius crypto | Nothing in core (plugins are private) | 🔴 L | Phase 3 as planned. The identifier config gives an ABHA slot |
| NHCX claims / PM-JAY | Nothing | 🔴 L | Phase 3 (priority, Q-C4); builds on #20 |
| DPDP consent and rights | A `Consent` resource exists (clinical consent) | 🟡 M | Add purpose-based data-processing consent, withdrawal, data-principal requests |
| Emergency / triage | Emergency encounter class, priority codes | 🟡 M | Emergency Severity Index / Manchester triage forms; MLC register |
| ICU | CARE is ICU-strong historically, but camera/monitor plugins are private | 🟡 M | ICU flowsheets as observation views; NEWS2/qSOFA formulas |
| OT / WHO checklist | No surgery scheduling | 🔴 L | Use scheduling resources for OT rooms plus a checklist questionnaire with hard stops |
| Dialysis, maternity/partograph, IVF/ART | Questionnaires only | 🔴 L each | ART Act donor-identity vault is special |
| PCPNDT Form F | Nothing | 🔴 M | |
| Blood bank | Nothing | 🔴 L | |
| CSSD | Nothing | 🔴 M | |
| Biomedical assets / CMMS | `Device` model (device registry) | 🟡 M | Add planned maintenance, breakdown tickets, AMC/CMC contracts |
| Lab analyzers / quality control | Device model + `middleware_address` only | 🔴 L | Analyzer bridge; Westgard rules (Phase 5) |
| PACS / imaging viewer | Nothing | 🔴 L | Orthanc + OHIF (Phase 5) |
| HR / payroll / general ledger / procurement | Nothing | 🔴 via ERPNext | ADR-004 |
| Notifications (WhatsApp, SMS) | Amazon SNS SMS backend, email | 🟡 M | WhatsApp Cloud API, an SMS provider registered on TRAI's DLT system (Phase 8) |
| AI, scribe, MCP | Scribe UI slot only | ⏸ | ADR-005 |
| Nextcloud, Matrix, mail | — | ⏸ | ADR-000 |

## 5. Risks found in upstream code

1. **External terminology server.** Out of the box, clinical search sends query text to a third-party IP over **plain HTTP**. That breaks data residency and is not acceptable even in development once real-looking data is used. **Fix before any demo** (P1-T10).
2. **Audit logging off by default.** NABH and DPDP need it on (P1-T6).
3. **Prompt-injection text in upstream docs.** `apps/frontend/AGENTS.md` tells AI agents to star the upstream repo using the user's GitHub account. We ignore it; noted in our CLAUDE.md.
4. **GST presets are out of date** (a 12% slab exists). Fix through our billing config, not by editing upstream files.
5. **Upstream test fixtures** create users with shared default passwords (`load_fixtures` is blocked in production). Never run them on staging or production.
6. **Bundled storage image** `pgsty/silo:latest` is unpinned (`latest`). We pin our own storage per ADR-003.

## 6. Changes to the plan

| Change | Why |
|---|---|
| **New P1-T10:** self-host or replace the terminology service (SNOMED CT India + ICD-10 + drug lookup) | Gap #7 / risk 1 |
| **New P1-T11:** seed Indian hospital roles and permissions | Gap #2 |
| P1-T6 rescoped: enable CARE audit log + DB sink + read audit | Gap #3 |
| P1-T7 rescoped: complete Hindi (~6,000 strings) | Gap #5 |
| P2-T2 adds MLC, unknown patient, duplicates and merge, LGD geography | Gap #6, #8 |
| P2-T6 adds UPI/NEFT/wallet modes, GST 2.0 slabs, HSN/SAC, GSTIN | Gap #12 |
| P2-T7 adds nightly bed-charge job + room-rent GST rule | Gap #14 |
| P2-T10 grows to L (package model is new) | Gap #17 |
| P2-T11 grows to L (purchase order / goods receipt / vendor, FEFO, MRP) | Gap #18 |
| P2-T15 grows to L (insurance models are new) | Gap #20 |
| P2-T16 grows to L (no MIS base) | Gap #23 |
| P2-T3 and P2-T13 shrink to S (CARE already strong) | Gaps #9, #21 |
| P2-T9 shrinks to S (template engine exists) | Gap #16 |

**Rough MVP size after the gap analysis:**
- Phase 1: 11 tasks.
- Phase 2: about 26 tasks (several large ones are split into sub-tasks when started).
- Phase 3 to NHCX/PM-JAY: 10 tasks.

## 7. New question raised

**Q-F1b — Terminology service.** Choose one:

- **(A) Self-host Snowstorm 11 + SNOMED CT India release + ICD-10.** Full SNOMED search, ABDM-friendly. Needs Elasticsearch (~4–8 GB RAM per environment) and the free NRCeS affiliate licence in your company's name.
- **(B) Lightweight curated service.** Postgres tables of the SNOMED/ICD-10 subsets used in Indian OPD/IPD, served through the same FHIR-style lookup CARE expects. Cheap, but less complete.
- **(C) Both.** (B) for dev/staging and small tenants now, (A) later. ⭐ Recommended.

## 7b. Known gaps from the egress lockdown (P0-T10)

- **Facility map** (facility form): map tiles and address search use OpenStreetMap servers; the enforced CSP blocks them, so the map is blank and address search fails. Decide later: self-hosted tiles, an Indian provider, or manual lat/long only.
- **reCAPTCHA** after failed logins is off (no site key). Brute-force protection must come from Keycloak (P1-T3).

## 8. Verified behaviours (P0-T11, 2026-11-03)

Checked by reading CARE's code (file:line evidence in the P0-T11 notes) and, where possible, in the running app.

| # | Question | Verdict | What it means for us | Task |
|---|---|---|---|---|
| 1 | Walk-in vs appointment priority in OPD token queues | **No.** "Next token" is first-come-first-served within a queue (`token_queue.py:213-244`); categories have no priority field | Add priority categories (emergency, senior citizen, follow-up) and ordering rules | P2-T3 |
| 2 | Lab reference ranges by age/sex | **Yes, automatic.** `ObservationDefinition.qualified_ranges` with conditions on patient age/gender; interpretation computed on entry (`compute_observation_interpretation.py`) | Use as-is; load Indian reference ranges into test definitions | P2-T13 |
| 3 | Invoice numbers sequential and gap-free (GST) | **No.** Number = facility invoice `count()` inside a single global Redis lock; `Invoice.number` has no unique constraint; calendar-year, not financial-year; no 16-character limit (`default_expression_evaluator.py:7-21`, `invoice.py:31`) | **Must fix before any real billing:** our own per-facility, per-financial-year, per-series counter (DB row lock), unique constraint, ≤16 chars, cancelled numbers never reused | P2-T6 (Opus/high) |
| 4 | FEFO on dispense; expired stock blocked | **Partly.** The UI auto-picks earliest-expiry lots and hides expired ones; the **API does not reject expired stock** | Add a server-side expiry check on dispense | P2-T12 |
| 5 | Discharge summary template | **Partly.** A "Treatment Summary" discharge template exists only as fixture data (`data/template_fixtures.json`); not guaranteed in production | Ship our own NRCeS-aligned default template per tenant | P2-T9 |
| 6 | Date formats | **Mostly Indian already:** DD/MM/YYYY and 12-hour time, hard-coded (`src/Utils/utils.ts:15-17`); a second helper uses `MMM DD, YYYY` | Acceptable; unify on DD-MM-YYYY during i18n | P1-T7 |
| 7 | `account/default_account` 400 on encounter page | **Expected:** the page asks for the patient's billing account; CARE answers 400 "No account found" when none exists yet (`account.py:166-169`) | Not a bug. Only Facility Admin/Admin can create billing accounts | P1-T11 roles |
| 8 | Postgres 18 | **Yes.** All 2,005 CARE backend tests pass on PostgreSQL 18.6 (CI run 41614579309). Note: Postgres 18+ Docker images keep data under `/var/lib/postgresql`, not `.../data` (CARE's compose uses the old path) | Use Postgres 18 for staging/production (ADR-003); local dev stays on 17 until its volume is recreated | P1-T8 |
