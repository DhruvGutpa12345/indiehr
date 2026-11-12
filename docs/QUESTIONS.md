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
| Q-E4 | **Cloud: provider-neutral; target E2E Networks Delhi NCR, backups to E2E Chennai; AWS Mumbai/Hyderabad as the documented alternative; nothing paid yet, trials on this machine** (owner, 2026-11-08, replaces the same day's "AWS Mumbai"; "AWS Delhi" is only a Local Zone with no storage, ~4x the price). The owner creates any account and pays; Claude never enters card details | ADR-011 |
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
| Q-R1 | **Repository public, view-only licence** (owner, 2026-11-08): all rights reserved to Dhruv Gupta; no use, copying, modification, distribution or AI training without written permission; CARE parts stay MIT. Issues/Wiki/Projects off; outside contributors' CI needs approval; private vulnerability reporting on | LICENSE, SECURITY.md |
| Q-E7 | **Audit fingerprint bucket with the server's provider** (owner, 2026-11-08): on E2E that is Governance-mode lock, which an E2E account admin could remove (ADR-010 wanted COMPLIANCE). Accepted with a separate account and an explicit setting | ADR-011 §3 |
| Q-E8 | **Servers run Ubuntu 26.04 LTS** (owner, 2026-11-09); the setup script also accepts 24.04 in case a provider has no 26.04 image yet | P1-T8b1 |
| Q-E9 | **SSH: key-only, rate-limited, open to the internet** (owner, 2026-11-09): no passwords, no root login, repeated attempts blocked; a private WireGuard tunnel may come with P1-T8d | P1-T8b1 |
| Q-E10 | **E-mail: Zoho ZeptoMail (India data centre)** for staff setup and invitation e-mails once live (owner, 2026-11-09); the owner gets Zoho's written confirmation of India-only storage before go-live; locally Mailpit stays; the app only needs plain SMTP settings | P1-T8b4 |
| Q-T11 | **amhcloud.in for now** (owner, 2026-11-08; may change later); DNS stays at the registrar; certificates via our own acme-dns | Hospitals at `<slug>.app.amhcloud.in`; ADR-011 |
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
| Q-K11 | **First admin and all staff get a one-time setup e-mail** (confirm e-mail, choose password, set up authenticator); an existing account gets a "join this hospital" confirmation instead (2026-11-07). *Owner, 2026-11-07: P1-T5c covers new accounts only; an existing account stops provisioning at its second step, and the join confirmation comes with P1-T5e.* *Owner, 2026-11-07 (P1-T5e): split into T5e1 invitations and T5e2 account recovery; staff are invited through CARE's own "Add user"; an existing account joins only by following the e-mailed link, signing in with password + code and clicking Join; a lost authenticator may be reset by an admin of any hospital the person works at (link to their own verified e-mail only).* *Owner, 2026-11-07 (P1-T5e2): forgotten password = self-service link; no recovery codes for now; unaccepted invitations are cleaned up at expiry; admin actions as an API now, buttons later in a web-app patch.* | P1-T5c/T5e |
| Q-K12 | **Starter data = the minimum**: roles, code lists, profile, state + district, one facility, first admin; departments/price lists/GST later as reviewed packs (2026-11-07) | P1-T5a |
| Q-K13 | **P1-T8d split** (owner, 2026-11-09): P1-T8d1 Keycloak runtime (pre-built image, single-node cache, readiness, per-IP rate limits, login events in the journal) → P1-T8d2 `private_key_jwt` for the backend and the two workers, key delivery and rotation | PLAN |
| Q-K14 | **Keycloak keeps its own database and role on the shared Postgres server** (owner, 2026-11-09), already in the pgBackRest backups; an own Postgres instance is revisited with a second server | ADR-008 |
| Q-K15 | **Lock-out bursts: logged now, alerts with P1-T9** (owner, 2026-11-09): Keycloak's login failures and lock-outs go to the 180-day journal | P1-T8d1, P1-T9 |
| Q-K16 | **No WireGuard tunnel for now** (owner, 2026-11-09): the Keycloak admin console stays reachable only through an SSH tunnel to 127.0.0.1; WireGuard may become its own task | Q-E9 |
| Q-K17 | **Client signing keys on the server only** (owner, 2026-11-11): backend, onboarding and staff keys each in their own Docker volume, readable only by their container; not on the deploying machine, not backed up — a lost key is replaced and re-enrolled with the bootstrap credential | P1-T8d2, ADR-012 |
| Q-K18 | **Key rotation: manual every 180 days** (owner, 2026-11-11): `staging.sh keys rotate`; smoke warns at 150 days, fails at 365; never a side effect of a deploy | P1-T8d2 |
| Q-K19 | **Dev token viewer retired** (owner, 2026-11-11): the real hospital sign-in replaces it | P1-T8d2 |
| Q-K20 | **Suspected key theft: rotate at once and end every staff and operator session** (owner, 2026-11-11) | P1-T8d2 |
| Q-O1 | **P1-T9 split in four** (owner, 2026-11-11): T9a metrics + e-mail alerts (+ stop Postgres writing query values into logs, first) → T9b log collection with username redaction + log alerts → T9c 180-day retention (measured sizing, daily locked archive) → T9d dashboards and access | PLAN |
| Q-O2 | **Log store: VictoriaLogs**, not Loki (owner, 2026-11-11): Apache-2.0, a hard disk cap, about a third of Loki's memory; it accepts Loki's push format | ADR-013 (T9b) |
| Q-O3 | **The 180-day log record (CERT-In) is the log store plus a daily locked archive** in the Indian backup-region bucket (400 days, built from the redacted logs); the server's journal becomes a 14-day buffer (owner, 2026-11-11) | P1-T9c |
| Q-O4 | **Outside uptime checks: free healthchecks.io (dead-man switch) + UptimeRobot (public status URL)** (owner, 2026-11-11): only pings and probes leave the server, no data; the owner creates both accounts; optionally a small second VM in India at go-live | P1-T9a |
| Q-O5 | **Log collector: Fluent Bit** (owner, 2026-11-12): Apache-2.0, small, reads the server journal directly, redacts with Lua before anything is stored | ADR-013 (T9b) |
| Q-O6 | **Whole server's logs go to the log store** (owner, 2026-11-12): our containers plus the host's own security logs (SSH, sudo, firewall, system services) | ADR-013 (T9b) |
| Q-O7 | **Redaction: targeted + patterns** (owner, 2026-11-12): the typed username of Keycloak "user not found" events is dropped (real staff usernames kept for lock-out checks); everywhere, e-mail addresses, mobile numbers, 12-digit (Aadhaar-like) and ABHA numbers, tokens and query strings are masked before storage | ADR-013 (T9b) |
| Q-O8 | **Log alerts name the address** (owner, 2026-11-12): lock-out bursts and sustained 429s per address are evaluated by vmalert on the log store and mailed through the existing Alertmanager with the IP address in the mail | ADR-013 (T9b) |
| Q-O9 | **Sign-in abuse alerts are mailed at any hour** (owner, 2026-11-12): SignInLockoutsFromAddress, FailedSignInsFromAddress and RateLimitedAddress go out at once day or night (repeated at most daily); other warnings still wait for 08:00–22:00 IST | ADR-013 (T9b) |
| Q-O10 | **Log archive at go-live: a new E2E Chennai bucket** next to the database backups (owner, 2026-11-12): Governance lock (E2E's only mode; the account admin could remove it, as accepted for the audit fingerprints, Q-E7); AWS Hyderabad (COMPLIANCE) stays an option for later; proven locally on SeaweedFS | ADR-014 (T9c) |
| Q-O11 | **Log archives encrypted on the server before upload** with our own key (owner, 2026-11-12), like the database backups; the key goes into the owner's password manager | ADR-014 (T9c) |
| Q-O12 | **Erasure after 400 days (DPDP Rules 8(3)) runs monthly from the owner's machine** with the bucket's admin key (owner, 2026-11-12); the server never holds a key that can delete; an alert when it is overdue | ADR-014 (T9c) |
| Q-G4 | **`/goal` and `/advisor` = second-opinion reviews:** an independent Opus agent reviews each finished phase against the plan | Working agreement §5 |

## 🟡 Auto-accepted by Claude, awaiting owner confirmation (run 2026-11-12, `/run-until auto`)

Taken during an unattended run (docs/runs/2026-11-12-run-to-P1-T10a.md). Each is built to be easy
to reverse; say "Q-xx: confirm" or give another answer.

### Q-O13 — Which dashboard tool, and what may it do? (P1-T9d)

- **A ⭐ (taken). Grafana OSS 13.3.1, unmodified, as a viewer only.** The VictoriaLogs plugin
  v0.34.0 is baked into our image, checked by its SHA-256. Dashboards and datasources come from
  git and are read-only in the UI. Grafana's own alerting is off: Prometheus, vmalert and
  Alertmanager stay the only alerting. Scorecard: Grafana 91.5 %, Perses 72.5 %
  (docs/reviews/p1-t9d/scorecard.md).
- B. Perses (Apache-2.0, CNCF sandbox): permissive licence, but its logs support is young and
  it has no VictoriaLogs plugin; we would switch if Grafana's licence or telemetry changed.
- C. No dashboard tool: Prometheus's, Alertmanager's and VictoriaLogs's own pages through the
  tunnel. These show no overview and do not meet the task.

Why A: it was already planned (PLAN, ADR-013), it reads both stores, and being AGPL it is used
unmodified and reached over the network only (CLAUDE.md). **To reverse:** drop
`compose.dashboards.yaml` from `scripts/staging.sh compose()`. Nothing else depends on it.

### Q-O14 — How does the operator reach Grafana and sign in? (P1-T9d)

- **A ⭐ (taken). The existing SSH tunnel only, with Grafana's own admin login.**
  - `scripts/staging.sh ops-tunnel` adds `http://grafana.localhost:3000`.
  - Grafana answers to that one host name, sits on an internal network with Prometheus and a
    read-only log door only, and publishes nothing.
  - The admin name and password are generated and kept in `secrets/`. There is no anonymous
    access, no sign-up and no API basic auth.
- B. Single sign-on through Keycloak's operator realm (MFA). This is better for a team, but it
  adds a client, a realm role and a dependency on Keycloak being up to see why it is down. It is
  worth doing when a second operator joins.
- C. Publish Grafana through Traefik on `ops.<suffix>`. Not recommended: it would put a new
  login page on the internet.

**To reverse:** B is an added `[auth.generic_oauth]` block plus a Keycloak client, and the tunnel
stays as a way in when Keycloak is down.

### Q-F6 — Which terminology engine? (P1-T10a1)

- **A ⭐ (taken). Our own engine** in the plugin `ihr_terminology`, answering CARE's four FHIR
  terminology calls in-process from shared, read-only tables in `public`. It adds no process,
  no network hop and no image. Score 405/500.
- B. Snowstorm Lite (SNOMED International, Apache-2.0), 375/500, within 10 %. It is SNOMED-only,
  so it would sit next to our engine for HL7, UCUM and LOINC, and it needs about 500 MB of RAM.
- C. HAPI FHIR (370) and FHIRsmith (335): heavier, or too young.

Why A: CARE needs only a small subset, and the 8 GB server is tight. **To reverse:** point
`ResourceManger._fhir_client` at an HTTP client for Snowstorm Lite for SNOMED. P1-T10a2's
synthetic SNOMED-scale benchmark is the trigger for that. Evidence:
docs/reviews/p1-t10a/scorecard.md, ADR-016.

### Q-F7 — What counts as a valid code? (P1-T10a1)

- **A ⭐ (taken). Strict.**
  - A coded write is accepted only when it carries the value set's system, an active code
    that is in the value set, and, if it carries a display, one of that concept's terms.
  - Value sets that use filters beyond CARE's own (anything but is-a, descendent-of, =, in,
    not-in, and simple ECL) are refused, never widened.
  - SNOMED, LOINC and ICD answer nothing until their licensed content is loaded, even codes
    that CARE's own value sets list.
  - Pinned HL7 versions answer from the loaded version.
  - UCUM accepts any well-formed unit built from the common list, such as `{capsule}`.
- B. Lenient: accept unknown codes until content arrives. Records would then hold codes no one
  checked.

**To reverse:** the rules are in one module, `ihr_terminology/engine.py`, with a test each.

### Q-F8 — What content ships with the code? (P1-T10a1)

- **A ⭐ (taken).** HL7 Terminology 7.5.0 (CC0): the 7 code systems CARE uses. UCUM common
  units from FHIR R4 4.0.1 (CC0, UCUM's terms of use). Nothing licensed.
- SNOMED CT India, LOINC, ICD-10/11 and a drug list wait for P1-T10b, which needs your licences
  and accounts.

### Q-F9 — Splitting P1-T10 (P1-T10a1, T10a2, T10b)

- **A ⭐ (taken).** Split as in TRACKER: the engine and free content first; then SNOMED
  readiness with a benchmark; then licensed content, behind your licences.
- The design attack found that one T10a was 8 to 15 hours, against the 1 to 3 the plan allows.

### Q-K21 — 🔴 What may front-office and back-office roles see? (P1-T11b; blocks it)

*Not auto-accepted: it decides what patient data each job sees (DPDP purpose limitation, NABH
access control), and the P1-T11 design attack showed CARE cannot do it without an authorization
override.* The facts, with evidence in docs/reviews/p1-t11/:

- CARE lets a user open or edit a patient only with **clinical-data** rights. A Receptionist
  without them cannot open a patient they just registered.
- "Read an encounter" is not demographic: it includes discharge summaries and discharge advice,
  prescriptions and bed occupants.
- So in CARE there is no non-clinical way to work with patients at all.

Options:

- **A ⭐. A narrow override in our plugin.** Non-clinical roles (Receptionist, Cashier, TPA Desk,
  Owner) get a **demographics-and-billing view**: patient identity, contact and encounter status,
  never notes, diagnoses, reports or advice. Clinical endpoints stay closed to them, with tests
  per endpoint. MRD and Radiologist get clinical read, MRD only through an audited path.
  - Owner: totals only (census counts, revenue) and no identified patient list, until MIS
    (P2-T16).
  - Cost: about 2 sittings (P1-T11b), and one more place to keep in step with upstream.
- B. Give non-clinical roles clinical-data read, as CARE's own Staff role effectively needs.
  Simple, but a cashier would then read discharge summaries: against least privilege.
- C. Only CARE's roles for now, and come back in Phase 2 (P2-T2 registration, P2-T6 billing),
  when their screens are rebuilt anyway.

**Also yours:**
- Does the Owner approve discounts, cancellations and locked invoices? (Proposed: no; those stay
  with Facility Admin.)
- Does the Owner see identified billing (who was billed for what)? (Proposed: totals only.)

### Q-K22 — P1-T11 split into a, b and c (auto-accepted by Claude, 2026-11-12, awaiting owner confirmation)

- **A ⭐ (taken).**
  - **P1-T11a (built):** the safe groundwork.
    - A guarded role sync: CARE's sync would hard-delete a role, with every membership holding
      it, in every hospital, on a rollback or a rename. The guarded sync refuses instead.
    - A role registry: names are permanent, grants never alias CARE's shared lists, and it fails
      at start-up on an upstream rename.
    - A refusal of CARE's switch that lets any user open any patient by id (and anyone with
      patient-write rights edit any patient).
  - **P1-T11b (after Q-K21):** the eight roles, their grants and the access override, with
    tests per endpoint and a seeded user per role.
  - **P1-T11c:** the web app's menus, shown by permission (an `apps/frontend` patch, CI only).
- B. One task. The design attack showed it is 3 to 4 sittings and rests on Q-K21.

### Q-K23 — The Phase 1 review's findings as tasks (auto-accepted by Claude, 2026-11-12, awaiting owner confirmation)

The independent Phase 1 review (docs/reviews/phase-1-review.md) had 22 findings. Two agents
challenged each one: 1 was refuted, 3 confirmed as written, and 18 confirmed with corrections.

- **A ⭐ (taken).** Ten new TRACKER rows, P1-T15 to P1-T24, plus a docs sync, in two groups:
  - **before Phase 2:** reference-data packs, isolation coverage, a trial upstream sync, docs;
  - **before the first pilot with real data** (gates under ADR-007): release and rollback,
    separate database roles, anonymous-route patches, audit watch, single-hospital restore,
    Postgres rules and capacity, admin screens.
  - Nothing was built from them in this run.
  - Two items are yours, not tasks: marking the Phase 4 compliance duties as gates, and the
    alert channel.
- B. Keep them only in the review file until the owner sorts them.

Why A: the run rules turn phase-review findings into tasks. Two findings also touch your own
answers:
- staging's public-read facility bucket goes against Q-T3;
- the second alert channel revisits Q-E6/Q-O4.

They are listed so you can decide, not changed. **To reverse:** delete or reorder the rows.

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

**Q-E11 E2E disk encryption (P1-T8b4, before the first apply).** E2E nodes can be created with
"encryption" on (`disk_encryption` in `infra/e2e`; it cannot be changed later). Its docs do not
say whether the passphrase is needed at every boot; if it is, the server would not come back by
itself after a restart. ⭐ Ask E2E in writing (with the Q-E10/ADR-011 questions); keep it off
until they confirm unattended restarts work. The database disk on AWS is always encrypted.

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
