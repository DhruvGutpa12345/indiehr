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

## ✅ Auto-accepted by Claude in the run of 2026-11-12, confirmed by you 2026-11-22

Taken during an unattended run (docs/runs/2026-11-12-run-to-P1-T10a.md). **Owner answer (2026-11-22):**
Q-O13, Q-O14, Q-F6, Q-F7, Q-F8 and Q-F9 confirmed as taken (option A each), in chat. Q-F6's
benchmark condition stands: one clean load on an 8 GB VM (P1-T26) before P1-T10b's first real load.

### Q-O13 — Which dashboard tool, and what may it do? (P1-T9d; confirmed by you 2026-11-22)

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

### Q-O14 — How does the operator reach Grafana and sign in? (P1-T9d; confirmed by you 2026-11-22)

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

### Q-F6 — Which terminology engine? (P1-T10a1; confirmed by you 2026-11-22)

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

**Benchmark result (P1-T10a2, 2026-11-12; A kept, auto-accepted by Claude, confirmed by you
2026-11-22):**
- **Queries:** every limit passes with room once the engine has one expression index. Multi-word
  p95 was 107–145 ms against 300 ms, with no timeouts, in runs 4–7.
- **Loads:** the 30-minute limit was met in 2 of 4 fresh loads (21–22 min). It was missed in the
  2 taken under heavy host swap on this 6.7 GB desktop (34 and 49 min). No run met the strict
  no-swap rule.
- **To settle it:** one clean run on an 8 GB VM before P1-T10b's first real load. If that load
  still takes more than 30 min with no swap, tune the load path, or reopen this question.
- Details: docs/reviews/p1-t10a2/bench-results.md.

### Q-F7 — What counts as a valid code? (P1-T10a1; confirmed by you 2026-11-22)

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

### Q-F8 — What content ships with the code? (P1-T10a1; confirmed by you 2026-11-22)

- **A ⭐ (taken).** HL7 Terminology 7.5.0 (CC0): the 7 code systems CARE uses. UCUM common
  units from FHIR R4 4.0.1 (CC0, UCUM's terms of use). Nothing licensed.
- SNOMED CT India, LOINC, ICD-10/11 and a drug list wait for P1-T10b, which needs your licences
  and accounts.

### Q-F9 — Splitting P1-T10 (P1-T10a1, T10a2, T10b; confirmed by you 2026-11-22)

- **A ⭐ (taken).** Split as in TRACKER: the engine and free content first; then SNOMED
  readiness with a benchmark; then licensed content, behind your licences.
- The design attack found that one T10a was 8 to 15 hours, against the 1 to 3 the plan allows.

### Q-K21 — What may front-office and back-office roles see? (P1-T11b; **answered by owner, 2026-11-14**)

**Owner answer (2026-11-14):** in chat, "Answer Q-K21": the recommended defaults are taken.
- **A:** a narrow override in our plugin. Receptionist, Cashier, TPA Desk and Owner get a
  demographics-and-billing view only (identity, contact, encounter status; never notes,
  diagnoses, reports or advice); clinical endpoints stay closed to them, with a test per
  endpoint. MRD and Radiologist get clinical read, MRD only through an audited path.
- **Owner approvals:** no. Discounts, cancellations and locked invoices stay with Facility Admin.
- **Owner's billing view:** totals only (census counts, revenue), no identified patient or
  billing list, until MIS (P2-T16).

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

### Q-K22 — P1-T11 split into a, b and c (auto-accepted by Claude, 2026-11-12; **confirmed by owner, 2026-11-14**)

**Owner answer (2026-11-14):** confirmed as taken (option A ⭐), in chat: "confirm the earlier Q-K22 to Q-K28".

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

### Q-K23 — The Phase 1 review's findings as tasks (auto-accepted by Claude, 2026-11-12; **confirmed by owner, 2026-11-14**)

**Owner answer (2026-11-14):** confirmed as taken (option A ⭐), in chat: "confirm the earlier Q-K22 to Q-K28".

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

### Q-K24 — P1-T15 split into a and b (auto-accepted by Claude, 2026-11-13; **confirmed by owner, 2026-11-14**)

**Owner answer (2026-11-14):** confirmed as taken (option A ⭐), in chat: "confirm the earlier Q-K22 to Q-K28".

- **A ⭐ (taken).**
  - **P1-T15a (this run):** the pack engine, plus CARE's own system value sets as the first pack.
    It replaces `sync_valueset` at every start and in provisioning, and adds drift and re-apply
    tests that run on every upstream sync.
  - **P1-T15b (after P1-T17, before the Phase 2 masters):**
    - the hospital admin's "updates available" API (the screen with P1-T24);
    - identifier configs (UHID, ABHA) and forms (a new version is a new form, plus a guard
      against editing a form that has answers);
    - per-facility kinds such as the lab catalogue.
    - Their content is clinical or yours (Q-K12), so it comes with Phase 2.
- B. One task: more than one sitting, and most of B's content cannot be written yet.

### Q-K25 — How reference packs behave (P1-T15a; auto-accepted by Claude, 2026-11-13; **confirmed by owner, 2026-11-14**)

**Owner answer (2026-11-14):** confirmed as taken (option A ⭐), in chat: "confirm the earlier Q-K22 to Q-K28".

These are within your Q-T10 answer and TENANCY §5. Each is a rule in one module,
`ihr_refdata/engine.py`, with a test.

- **A ⭐ (taken).**
  - **Who gets updates at start-up:** active hospitals only. A hospital that fails is recorded and
    alerted, and the rest start.
  - **Provisioning:** a new hospital gets the packs as it is created and again just before it
    goes live.
  - **Unknown origin:** a row that differs from the pack, with no record that this pack wrote it,
    counts as edited. You decide.
  - **Keep:** "keep my version" stands until the pack changes again.
  - **Deleted CARE system value sets are restored automatically.** CARE's code needs them by name,
    and without one every form bound to it fails. The restore is in the audit trail.
  - **Versions only go forward.** An older image skips the pack with a warning, and a version
    number is never reused.
  - `system-medication-not-given`, a CARE system set that CARE's own sync never loaded (a module
    nothing imported), is shipped.
- B. Leave deleted system sets for the admin to decide (they would break clinical forms until
  then).

**To reverse:** change the rule in `engine.py` and its test.

### Q-K26 — P1-T16 split into a and b (auto-accepted by Claude, 2026-11-13; **confirmed by owner, 2026-11-14**)

**Owner answer (2026-11-14):** confirmed as taken (option A ⭐), in chat: "confirm the earlier Q-K22 to Q-K28".

- **A ⭐ (taken).**
  - **P1-T16a:** the isolation crawl.
    - It fails closed on routes it cannot fill, and fills Django path-converter routes.
    - It fixes the staff-access route that would have answered GET with a server error.
    - Seven mutation rows (audit coverage, terminology refusal, role guard, global-edit refusal,
      packs), plus the guard tests they need.
  - **P1-T16b:** seeded rows for the models behind the skipped crawl routes, and a test that
    each one answers its own hospital.
- B. One task: two sittings of different kinds (test infrastructure, then 31 seeded models).

### Q-K27 — Browser isolation tests (P1-T1d; auto-accepted by Claude, 2026-11-13; **confirmed by owner, 2026-11-14**)

**Owner answer (2026-11-14):** confirmed as taken (option A ⭐), in chat: "confirm the earlier Q-K22 to Q-K28".

- **A ⭐ (taken).**
  - The browser tests run in GitHub Actions on every relevant change and weekly, with
    Playwright's own Chromium (hash-checked) downloaded there.
  - Nothing secret is uploaded: no traces, videos or HTML reports, only screenshots without
    form fields.
  - The hospital switcher test is skipped until the switcher exists. It is not built (TENANCY
    §9) and has no task yet.
  - The patient sign-out now also clears the doctor a patient picked in the public booking
    flow, so a shared kiosk does not show it.
- B. Run them only by hand (cheaper, but a sign-in or clean-up regression would go unnoticed).

### Q-K28 — Merge the trial upstream sync? (P1-T17; auto-accepted by Claude, 2026-11-13; **confirmed by owner, 2026-11-14**)

**Owner answer (2026-11-14):** confirmed as taken (option A ⭐), in chat: "confirm the earlier Q-K22 to Q-K28" (so `p1-t17-sync-trial` stays unmerged; the next sync merges it after a rebase, with the three follow-ups listed below).

The trial synced CARE (backend 10 commits, frontend 7) on the branch `p1-t17-sync-trial`, with
no conflicts and our tests passing.

- **A ⭐ (taken). Do not merge it into `main` in this run.** The PLAN row asks for a trial, and
  the sync brings about 8,700 lines of upstream change: an admin sign-in rework, token
  revocation, safer report rendering. The branch stays ready, and the next sync (or your "go")
  merges it after a rebase.
- B. Merge now: less drift. But an unreviewed upstream bump would land in the same run that
  built the rehearsal for it.

Before merging, also do these (found by the trial):
- pin `REPORT_ALLOWED_RESOURCE_ORIGINS` empty in deployment;
- add Hindi for `file_too_large`;
- re-translate `pin_page_limit_reached` (its English changed).

### Q-K29 — How releases and rollbacks work (P1-T18; auto-accepted by Claude, 2026-11-13; **confirmed by owner, 2026-11-14**)

**Owner answer (2026-11-14):** accepted as taken (option A ⭐), in chat: "Accept all five" (Q-K29…Q-K33).

This builds TENANCY §7.2 and your Q-T1 answer (a short announced window around 02:00 IST).

- **A ⭐ (taken).**
  - **Split:** P1-T18a (the per-hospital gate and the fleet runner), then P1-T18b (release,
    rollback, runbook, drill).
  - **Hospital migrations run only in `staging.sh release`,** the announced window. A plain
    `up` refuses to start if any hospital's schema does not match the code.
  - **At the window's start every hospital is held** (it answers "upgrading"). A restore point
    is then taken, the public schema and each hospital are migrated (2 at a time), and each
    hospital is released when it is done.
  - **A hospital whose migration fails stays held while the others go live.** The window does
    not close, and you are alerted. It is resumed after a fix.
  - **Rollback without a restore** only when nothing changed in the database or Keycloak.
    Otherwise it is a restore to the window's restore point, which needs an explicit flag and
    loses only writes made after the window, if any.
  - **No e-mail announcement to hospitals.** That is sending on your behalf: yours to decide.
    The announcement is a banner (the web app's part comes with P1-T24).
- B. Keep migrating at every `up` (simpler, but migrations would run at any hour, under live
  traffic, with no way back).

### Q-K30 — Separate database roles (P1-T19; auto-accepted by Claude, 2026-11-13; **confirmed by owner, 2026-11-14**)

**Owner answer (2026-11-14):** accepted as taken (option A ⭐), in chat: "Accept all five" (Q-K29…Q-K33).

This builds TENANCY §6.10. The Phase 1 review rated it High.

- **A ⭐ (taken).**
  - **Split:** P1-T19a (the roles, the grants, the run-time refusal, the audit hardening,
    tests run as the app role) and P1-T19b (moving staging: new secrets made on your machine,
    the move under a full stop, the drill, the local proof).
  - **Two roles:**
    - `ihr_owner` runs migrations, the provisioner and admin one-offs;
    - `ihr_app` serves requests and runs the workers. It cannot change schemas, disable
      triggers, write terminology, fake a migrated schema, or forge audit entries and seals.
  - **The provisioner keeps owner rights.** It creates and drops hospital schemas. Everything it
    acts on is made read-only for the app, so a web-app bug cannot steer it. The runner-up is a
    separate DDL job.
  - **Sealing the audit chain** goes through a database function owned by the owner, and the
    entry's time and transaction id are set by the database (an ADR-010 amendment, Proposed).
  - **A container on the wrong role refuses to start,** and you are alerted.
  - **Existing servers:** `ihr` is renamed to `ihr_owner` with a **new** password. The new
    password goes into your password manager.
- B. Keep one role and rely on detection (verify plus fingerprints) alone.

### Q-K31 — Public routes and pictures (P1-T20; auto-accepted by Claude, 2026-11-13; **confirmed by owner, 2026-11-14**)

**Owner answer (2026-11-14):** accepted as taken (option A ⭐), in chat: "Accept all five" (Q-K29…Q-K33).

This builds your Q-T3 answer (presigned, no public bucket) and the Phase 1 review's R1-02 and
R1-03.

- **A ⭐ (taken).**
  - **Split:** P1-T20a (routes), then P1-T20b (files).
  - **The public doctor list stays open before the patient signs in** (the booking page shows
    doctors first). It is trimmed to name, title and photo, and covers public facilities only.
    Today it also gives out staff phone numbers, user names and last sign-in times.
  - **Pictures** (staff photos, facility covers) are served by signed links valid for 24 hours.
    The public bucket is removed. Anyone holding a current link can still see that picture.
  - **Batch requests** may only call the routes the web app actually batches; anything else is
    refused before it runs.
  - **Patient files and pictures** get separate storage keys. On AWS with role-based access,
    the separation relies on bucket policy.
- B. Require the patient's OTP sign-in before showing any doctor (more private, but it changes
  how booking works).

### Q-K32 — Watching the audit trail (P1-T21; auto-accepted by Claude, 2026-11-13; **confirmed by owner, 2026-11-14**)

**Owner answer (2026-11-14):** accepted as taken (option A ⭐), in chat: "Accept all five" (Q-K29…Q-K33).

- **A ⭐ (taken).**
  - **A small `audit-watch` service** (about 192 MB) holds only the fingerprint *reader* key. It
    checks:
    - seal lag every minute;
    - each hospital's chain from a stored checkpoint, and the newest fingerprints, every 15
      minutes;
    - a slice of old entries every night;
    - the full recompute, monthly, on the restore-drill copy.
  - **You are mailed** when a chain breaks, a fingerprint mismatches, triggers are missing, a
    fingerprint is late (over 2 h 15 min), sealing lags (over 15 min, critical over 1 h), or the
    watch itself stops. Only a fixed log line names the hospital.
  - **Suspended, offboarding and archived hospitals** are sealed every 10 minutes too (today they
    never are).
- B. A host timer instead of a resident service (no resident memory, but it needs your sudo for
  a new timer).

### Q-K33 — Per-hospital dumps and single-hospital restore (P1-T22; auto-accepted by Claude, 2026-11-13; **confirmed by owner, 2026-11-14**)

**Owner answer (2026-11-14):** accepted as taken (option A ⭐), in chat: "Accept all five" (Q-K29…Q-K33).

- **A ⭐ (taken).**
  - **Split:** P1-T22a (nightly dumps), then P1-T22b (single-hospital restore and the drill).
  - **Dumps:** each hospital at about 03:30 IST, encrypted with a key of their own (which goes
    into your password manager), kept about 28 days in the other-region backup bucket.
  - **The dumper** has read-only database rights.
  - **Restoring one hospital** parks its live schema, restores under the real name, migrates,
    reconciles, and keeps the audit trail provable with a restore record. It never touches
    other hospitals.
  - Security state never goes back: deactivations stay, and all sessions end.
  - Parked schemas are deleted after 7 days.
  - Picture and patient-file buckets get versioning (old versions kept 35 days).
  - The audit entries a restore replaces are kept in an append-only archive in `public`
    (added when P1-T22b was built, 2026-11-14): verify and the audit watch check the restore
    against it. It holds the replaced values, so its erasure and offboarding wait for P4-T1.
  - A nightly dump can lose up to 24 h for that hospital; `--from-pitr` restores to within 15
    minutes.
- B. Rely on whole-cluster restore only (undoing one hospital's mistake rolls back every
  hospital).

TENANCY §7.5 and §7.7 are corrected (amendment, Proposed):
- no restore under a side name, because our audit triggers would write into the live trail;
- the erasure ledger lives outside the hospital's schema.

### Q-K34 — Which patients may the front desk open? (P1-T11b; auto-accepted by Claude, 2026-11-14; **confirmed by the owner, 2026-11-15**)

- **A ⭐ (taken). The whole hospital register.** Receptionist, Cashier, TPA Desk and MRD may open any patient of this hospital (demographics only).
- B. Only patients with an encounter in a facility where the user works.

*Recommendation A.* Each hospital is its own schema, so "the register" is that hospital's. The patient search already finds any patient by phone. A returning patient has no open encounter until the front desk creates one, so B would block the main front-desk job.

### Q-K35 — One person, two jobs (P1-T11b; auto-accepted by Claude, 2026-11-14; **confirmed by the owner, 2026-11-15**)

- **A ⭐ (taken). Permissions add up.** A person who holds a clinical role (Doctor, Nurse, Radiologist) and one of our non-clinical roles is treated as a clinician. Example: an owner who is also a doctor.
- B. Refuse mixed assignments. Such a person needs two accounts.
- C. The narrowest role wins: a doctor-owner sees projected records everywhere.

*Recommendation A.* It is how CARE's roles already combine. Doctor-owners are common in small Indian hospitals, and B or C would break their daily work. Our widening branches still project whatever else the user holds, so a non-clinical role never widens clinical access. After the attack: our encounter branch is used only where CARE's own path gives the user nothing, and it never adds its rows to CARE's. A Receptionist who is also a Paediatrics doctor gets CARE's Paediatrics encounters, unprojected, and nothing else through `?facility`. The attack's main case, a department doctor reading every account, booking, token and payment, is CARE's own Doctor behaviour and is not caused by adding a role. It is logged in Q-K40.

### Q-K36 — MRD records grants: length, reasons, alerts (P1-T11b4; auto-accepted by Claude, 2026-11-14; **confirmed by the owner, 2026-11-15**)

- **A ⭐ (taken).** 30 minutes, read only. Reason codes: patient request, legal, insurer, internal audit, NABH review, continuity of care. Legal and insurer need a reference. No second approver. Alerts: 3 refusals in 10 minutes, more than 40 grants a day per user, grants between 22:00 and 07:00 IST.
- B. As A, but a Facility Admin approves each grant.
- C. Standing clinical read for MRD, with the audit trail only.

*Recommendation A.* Records work is frequent and B would stall it. C would give a records clerk every patient's record at all times. A stated reason plus the trail and the alerts meets NABH access-control expectations at small-hospital scale. After the attack: under A, a grant also opens that patient's lab and imaging requests, specimens, reports and prescriptions, read only. CARE checks these by department, not by `PatientUser`. File links issued during a grant last at most 5 minutes (CARE's last 60). Until a records screen exists (Q-K45), MRD uses the grant through the API only.

*Built in P1-T11b4 (2026-11-15) as A.* The length and the link cap are settings
(`IHR_RECORDS_GRANT_MINUTES` 30, `IHR_RECORDS_LINK_SECONDS` 300), the codes are
`RecordsGrant.Reason`, the reference rule `IHR_RECORDS_REFERENCE_REQUIRED` (legal, insurer), and
the three alert thresholds are the numbers in `deploy/logs/alerts/records.yml` (night grants are
`info`, mailed at once). To change any, say so. Details: docs/reviews/p1-t11/design-b.md
"P1-T11b4 as built", ADR-018 (Proposed).

### Q-K37 — Does the front desk see where an inpatient is? (P1-T11b; auto-accepted by Claude, 2026-11-14; **confirmed by the owner, 2026-11-15**)

- **A ⭐ (taken). No.** Encounters shown to Receptionist, Cashier, TPA Desk and MRD carry no location and no department. Visitors' "which ward?" questions go to the nursing station.
- B. The ward name only (not the bed), for visitor enquiries.

*Recommendation A.* Q-K21 excludes bed occupants. A ward or department name (oncology, psychiatry) reveals the kind of illness. B can come with the P2-T2 registration screens if wanted. After the attack, A also means these roles:
- cannot find patients by bed, department, doctor or tag (those encounter filters answer 400);
- cannot read an encounter's departments;
- do not see who is on a device;
- see no booking notes and no booked doctor.

### Q-K37a — Do the Cashier and TPA Desk see the bed they bill for? (P1-T11b1 review C4, 2026-11-15; **owner: B, 2026-11-15**; built as B, 2026-11-15)

Under Q-K37 A, every encounter the Cashier and TPA Desk see has no ward, bed, bed history or treating doctor, also the one inside a billing account. CARE's account screen builds its room-rent (bed charge) table from the bed history, so for these two roles it shows "no locations" and they cannot add a room-rent charge there. Their printed bill shows no ward, bed or treating doctor. Room rent is the main inpatient charge, and the GST rule above ₹5,000 a day applies to it.

- A. Keep A for them too. Room-rent charges are added by other staff, or wait for a billing screen in P1-T11c that groups bed charges from the charge-item list.
- **B ⭐ (recommended). The two billing roles see the bed.** They get the bed's id and name, with its start and end times, and the current bed. The Receptionist and MRD keep A. The treating doctor stays hidden until the TPA claim layout (P2-T15, with Q-K41).

*Recommendation B.* Under Q-K38 these roles already see service names such as "chemotherapy cycle", so hiding the bed protects little and breaks the main inpatient charge.

*Built as B (2026-11-15; review fixes 2026-11-16):* `fields/encounter_billing.txt` is laid over the encounter for a Cashier or TPA Desk in the visit's own facility (`state.bills_at`): the bed's id and name and each bed stay, no ward, no occupant, no treating doctor. The test that pinned A was replaced by four B tests in `ihr_access.tests.test_guard.ThroughTheApi`. The Cashier reads and adds room rent; the TPA Desk only reads it (it has no right to add charges), and hiding its add button is P1-T11c's. Details: design-b.md "Q-K37a B".

### Q-K38 — Charge-item names seen by Cashier and TPA Desk (P1-T11b; auto-accepted by Claude, 2026-11-14; **confirmed by the owner, 2026-11-15**)

- **A ⭐ (taken). Accept.** Billing lines name the service ("HIV test", "chemotherapy cycle"), which billing needs.
- B. Hide service names from the Cashier and show codes only.

*Recommendation A.* A bill without service names cannot be explained to the patient or the insurer. The view is still limited to people whose job is billing. ADR-018 records this as a known residual exposure.

### Q-K39 — Small numbers in the Owner's totals (P1-T11b4; auto-accepted by Claude, 2026-11-14; **confirmed by the owner, 2026-11-15**)

- **A ⭐ (taken). No suppression, and no clinical breakdown.** Totals are by encounter class and status, beds and money only. There are no department, doctor, service or diagnosis splits until MIS (P2-T16).
- B. Hide any count under 5 in per-day figures.

*Recommendation A.* Without a clinical dimension, a small count does not point to a patient's condition. Suppression would make daily figures wrong for small hospitals. The question comes back with MIS breakdowns.

*Built in P1-T11b4 (2026-11-15) as A:* `GET /api/ihr/owner/totals/` (visits by class and status, admissions and discharges, inpatients now, beds, bills, payments and outstanding balance; per IST day or month, at most 366 days), nothing suppressed and nothing broken down. The Owner's screen is P1-T11c's (Q-K45).

### Q-K40 — CARE's own access gaps found on the way (P1-T11b; auto-accepted by Claude, 2026-11-14; **confirmed by the owner, 2026-11-15**)

These CARE behaviours affect CARE's own roles:
- patient search is open to any signed-in staff user;
- `add_user` can give any role on a patient;
- prescriptions and the discharge summary are readable with `can_read_encounter` alone;
- resource requests have no permission check;
- `can_write_patient` also gates allergy, note and file writes;
- (added after the attack) Doctor, Nurse, Staff, Volunteer and Pharmacist read every account, booking, token and payment of the hospital, each embedding the full encounter, wherever their department is;
- the device list and retrieve need only facility membership and show the patient on each device;
- a `PatientUser` care-team row keeps giving clinical read after the person's job changes;
- an account-report template can print the whole clinical encounter, and anyone who reads the account can download it;
- (added by P1-T11b3's role matrix) the delivery-order list with neither origin nor destination answers every order of the hospital to any member;
- (P1-T11b3) a service request or diagnostic report read through its place (a lab or imaging room) embeds the patient's whole record and visit (discharge advice, bed, care team, tags, programme identifiers), also for a department that has no clinical right on that patient;
- (P1-T11b3) favourites (`add_favorite`) are written for an object the caller may not read (only the caller's own list changes; nothing is shown);
- (P1-T11b3 review K1) the patient-scoped clinical lists and rows (diagnosis, symptom, observation and `observation/analyse`, consent, medication request, prescription, statement and administration, note threads and messages) let a reader with no clinical right on the patient in through `?encounter=<a visit>`, without checking that the visit is that patient's, and a row read by id is not narrowed to that visit: anyone who can read one visit's clinical data reads any patient's rows whose ids they know. Closed for users of our roles (`ihr_access/visits.py`); CARE's roles unchanged;
- (P1-T11b3 review) `slots/get_slots_for_day` checks no right ("TODO: Add Authorization"): any member reads any doctor's or room's day, schedule included (its sibling `availability_stats` checks the booking right). Closed for users of our roles without the booking right (`IhrSlotViewSet`);
- (P1-T11b3 review M8) the request-order (indent) list with neither origin nor destination answers every request order of the hospital to any member (no patient link, as the delivery-order list).

Our roles are protected from all of them by the design (after the attack), and by P1-T11b3's fixes for the Radiologist (CARE's membership-only lists are closed to users of our roles only, and a patient or visit embedded in another department's imaging request is projected for them) and its review fixes (a visit named in the query must be the path patient's; slots-for-day needs the booking right). The request-order list is not patient data (§7) and stays open to our roles as to CARE's.
- **A ⭐ (taken). Protect our roles now, and log the rest as a follow-up task** ("P1-T11d: close CARE-role access gaps", after P1-T11c). The `add_user` gate for restricted targets ships now.
- B. Close all of them now for every role. That changes Doctor, Nurse and Staff behaviour inside this task.

*Recommendation A.* P1-T11b's tests assert that CARE's roles are unchanged. The gaps are real, but fixing them for CARE's roles needs their own design and checks against the web app's flows.

### Q-K41 — Account reports for the Cashier and TPA Desk (P1-T11b; auto-accepted by Claude, 2026-11-14; **confirmed by the owner, 2026-11-15**)

An account report is a PDF built from a template. CARE's account template can print the whole clinical encounter (diagnoses, prescriptions, discharge advice), and anyone who may read the account may download any account report. A TPA "final bill" that shows the diagnosis is the normal Indian claim layout, so a hospital will make one.
- **A ⭐ (taken). Refuse account reports to the restricted roles until P2-T15.** Receptionist, Cashier, TPA Desk, Owner, Lab Technician, Store Keeper and MRD cannot list, download, generate or archive account reports. Bills and receipts still print from the invoice screens. P2-T15 (TPA) designs a claim layout and a reviewed list of billing-only templates, and comes back to you if TPA claims need diagnosis codes.
- B. Allow templates now that a check proves print no clinical field (a field-graph walker, re-checked on every template change).

*Recommendation A.* It closes the leak with a small swap and a test. B is a sizeable piece of work whose real need, the claim layout, belongs to P2-T15.

### Q-K42 — May the Receptionist see the list of departments? (P1-T11b; auto-accepted by Claude, 2026-11-14; **confirmed by the owner, 2026-11-15**)

CARE's "create encounter" form needs a department. With no department rights, the Receptionist's picker offers only the hospital as a whole, so every front-desk encounter is filed under the whole hospital and a department's doctors may not see it.
- **A ⭐ (taken). Yes.** The Receptionist gets CARE's `can_view_facility_organization` at the hospital level: department names and the department tree. These are hospital settings, not patient data. They still never see which department an existing patient's encounter is in.
- B. No. Front-desk encounters land on the hospital as a whole and clinical staff move them to a department.

*Recommendation A.* Sending a patient to the right department is the front desk's job. The department list holds no patient data, and the filters that could use department ids to find patients answer 400 (Q-K37).

### Q-K43 — Store Keeper and patient medication returns (P1-T11b; auto-accepted by Claude, 2026-11-14; **confirmed by the owner, 2026-11-15**)

CARE records a medication return as a delivery order to the store or pharmacy, named "Medication return - <patient name>", with the patient attached. Where the store and the pharmacy share a location, the Store Keeper would see a named patient next to the returned drugs (for example antiretrovirals).
- **A ⭐ (taken). Hide them.** Delivery orders and supply deliveries linked to a patient are invisible to the Store Keeper and to every other restricted role. Pharmacists handle returns.
- B. The Store Keeper handles returns and sees the patient's name on them.

*Recommendation A.* It keeps the design's "nothing patient" rule for the store, and Q-K21 lists prescriptions among what non-clinical staff never see.

### Q-K44 — When does the Radiologist see a patient's clinical record? (P1-T11b; auto-accepted by Claude, 2026-11-14; **confirmed by the owner, 2026-11-15**)

In CARE a department sees a patient's record only when the department is on the encounter, or the patient's bed belongs to it. An X-ray request at the Radiology room does not count. So the earlier design's "wherever Radiology is involved" was wrong.
- **A ⭐ (taken). CARE's rule, in P1.** The Radiologist sees the full record when the Radiology department is on the encounter (the front desk adds it at registration, or a clinician adds it later). Otherwise the Radiologist sees the imaging requests and reports only, not the chart.
- B. A plugin rule: while a patient has an open imaging request at a Radiology room, the Radiologist may read the chart, time-boxed and audited like MRD's grants. It widens clinical access, so it would come with the imaging work (Orthanc/OHIF, Phase 2).

*Recommendation A.* It needs no new access rule. B can be decided with the imaging workflow, when ordering a scan can add Radiology to the encounter by itself.

### Q-K45 — Front-desk, records and owner screens in the web app (P1-T11b → P1-T11c; auto-accepted by Claude, 2026-11-14; **confirmed by the owner, 2026-11-15**)

CARE's web app opens the patient tabs, the encounter list and the edit form only for CARE's own permissions, which these roles deliberately lack. After creating an encounter it opens a clinical page. There is no page for MRD's records access or for the Owner's totals.
- **A ⭐ (taken). Widen P1-T11c** (the web-app patch, built in CI) to cover:
  - the patient tabs, the encounter history, the patient home's encounter tab and the edit form;
  - the redirect after creating an encounter;
  - a records-access page (patient, reason, reference, active grants, end now);
  - an Owner totals page (lakh/crore).
  - (added 2026-11-16, Q-K37a B review C-C1) hide the room-rent table's "Add charge items" button from the TPA Desk, which may read room rent but not add it (the add answers 403).

  P1-T11b's own click-through is checked partly through the API.
- B. Keep P1-T11c to menus and add a new task for the screens.

*Recommendation A.* The screens and the menus are one patch to the same web app and one CI build. Until then the eight roles can be seeded and tested, but the front desk, MRD and the Owner cannot use them day to day.

### Q-K46 — Do adjustments count as "collected" in the Owner's totals? (P1-T11b4 review A1, 2026-11-15; **owner: A, 2026-11-15**, as built)

A payment record can be a payment, an advance, or an adjustment (a write-off or correction, no money moves). CARE's own "total paid" on an account counts all three, so the outstanding balance the Owner sees already allows for adjustments.
- **A ⭐ (built for now). "Collected" counts only money received:** completed payments and advances. Adjustments stay out of "collected" but still lower "outstanding now" (CARE's balance).
- B. Count adjustments as collected too, so "collected" matches CARE's "total paid".
- C. Show adjustments as their own figure next to "collected" and "refunded".

*Recommendation A, with C later if you want it.* "Collected" should mean cash in the bank; an adjustment is not. The two figures then answer different questions, and the totals page (P1-T11c) can say so. Pinned by `ihr_access.tests.test_totals.OwnerTotals.test_owner_totals_count_and_sum` (an adjustment of ₹999 is not collected); the choice is `RECEIVED_TYPES` in `ihr_access/totals.py`. Failed, queued and partial payments are never counted (review A1, not a choice: CARE does not count them either).

### Q-K47 — Where do our English web-app strings live? (P1-T11c design, docs/reviews/p1-t11/design-c.md §11, §13.7; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** A new `deploy/locale/en.json` holds our `ihr_*` English keys only; `merge-locale.py` merges it into the web app at build; upstream `public/locale/en.json` goes back to upstream's text (the 27 keys we added there move). This is subtask P1-T11c0.
- B. Keep adding `ihr_*` keys to upstream `en.json` as logged patches (as P1-T4b and P1-T7b did).

*Recommendation A:* about 60 new keys would otherwise be a patch in a file every upstream sync touches; CI already merges our locale files before each build. Reverse: move the keys back into upstream `en.json` and drop the file.

### Q-K48 — How do the Lab Technician and Store Keeper reach their lab or store? (P1-T11c; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** A "My places" menu item of ours lists the places they belong to (CARE's `location/?mine=true`) and links to the first page they may open. No permission changes.
- B. Give both roles `can_read_healthcare_service` so CARE's Services menu works (a registry change; matrix rows and key snapshots change).

*Recommendation A:* it changes no permission and keeps P1-T11b's reviewed matrix. Reverse: drop the menu item and add the grant.

### Q-K49 — Who gets the stricter web-app menus? (P1-T11c, revised after the design attack (SS3); auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A' ⭐ (taken).** Exactly the backend's own rule (`state.restricted`: holds one of our restricted roles and no clinical slug anywhere), published to the web app by a new `GET /api/ihr/my-access/`. CARE roles without clinical slugs keep CARE's menus.
- B (withdrawn). Anyone without a clinical or patient-opening slug in this facility: it disagrees with the backend's rule.

*Recommendation A':* the menus and the API then never disagree about who is restricted. Reverse: change the rule in one place (`my-access`).

### Q-K50 — What does the front desk see when it opens a visit, and after creating one? (P1-T11c; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** A status page of ours (status, class, dates, priority, visit number, status history; for the Cashier and TPA Desk also the bed (Q-K37a B) and the account link) for every visit link and after "create encounter".
- B. Return to the patient's encounter tab after create, and hide "View encounter" for them.

*Recommendation A:* one route wrap covers every link and the redirect; the billing desks need the bed and the account from the visit. Reverse: remove the route wrap.

### Q-K51 — How does the MRD clerk pick the patient for a records grant? (P1-T11c, design attack SS2/FF3; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Only from the patient page (CARE's own verify flow: phone, year of birth); the records page takes `?patient=<id>` and has no search of its own.
- B. CARE's two steps (phone, then year of birth and `search_retrieve`) inside our records page.

*Recommendation A:* no second copy of CARE's knowledge check to keep in step. Reverse: add the two steps to the page.

### Q-K52 — What does the Owner's "Visits" tile count? (P1-T11c; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Visits excluding cancelled and entered-in-error.
- B. All statuses, labelled "visits recorded, all statuses".

*Recommendation A:* a cancelled or mistaken visit is not a visit the hospital served. Reverse: one filter in `totals.ts`.

### Q-K53 — How are CARE's own three roles' menus pinned in the browser tests? (P1-T11c, design attack FF4; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Hand-derived lists in `roles-menus.spec.ts` plus a unit test that the menu code in CARE mode deep-equals CARE's unpatched menus.
- B. A recording-mode commit before the hooks, then an artifact download (one extra CI round and commit).

*Recommendation A:* CI keeps artifacts only on failure, and the unit test already proves CARE's menus are untouched. Reverse: add the recording step.

### Q-K54 — What does the menu show while the web app is still asking the backend who you are? (P1-T11c1 review C1-4, design-c.md §13.8; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Only Overview and Users until `my-access` answers (and if it keeps failing, retried every 15 s): a restricted role never sees CARE's full menu flash, and a failed `my-access` never opens it. CARE's roles get their full menu one round trip later; during a backend outage clinicians also see only Overview and Users.
- B. CARE's full menu while pending (design §13.2): no delay for clinicians, but a Receptionist briefly sees, and can click, menus that answer 403.

*Recommendation A:* fail closed; the delay is one small request, cached for the session. Reverse: `PENDING_KEEP` in `src/Utils/ihr/menu.ts` (and its tests).

### Q-K55 — May the Receptionist print an appointment slip? (P1-T11c2, design-c.md §13.8; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

CARE's print page (`AppointmentPrint.tsx`) always also asks for the visit's charge items and invoice. The Receptionist holds neither `can_read_charge_item` nor `can_read_invoice`, so every print would answer 403.

- **A ⭐ (taken).** Keep the print page refused for the demographics view ("not part of your role"); the Receptionist sees the appointment itself on the patient's Appointments tab.
- B. A one-line hook in `AppointmentPrint.tsx` that skips the charge-item and invoice queries without those slugs, and open the page: a printable slip without the bill part.

*Recommendation A:* no new upstream edit and no dead end now; B is cheap if the front desk asks for printed slips. Reverse: add the print key to `DEMOGRAPHICS_KEYS` in `src/Utils/ihr/routeKeys.ts` plus the hook (B).

### Q-K56 — Which billing buttons do the Cashier, TPA Desk and Receptionist see on an account? (P1-T11c2 review F3; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

Design §3.3 listed only the account page's tabs; CARE drew every write button to everyone who opens the account, and the backend refuses most of them for our desks.

- **A ⭐ (taken).** A button shows only when the request behind it can succeed (`ihrAccountActions` in `frontDesk.ts`): the Cashier keeps all of them; the TPA Desk keeps Settle and close and tags (`can_update_account`), no invoice, payment, credit note, transfer or charge-item row menu; the Receptionist none. CARE's roles unchanged. Two more upstream files carry a small hook (the two charge-item tables).
- B. Leave CARE's buttons and let the backend answer 403 on press.

*Recommendation A:* no dead ends at the desk. Reverse: make `ihrAccountActions` return CARE's value for restricted roles too.

### Q-K57 — When does the web app treat a records grant as ended if it cannot reach the backend? (P1-T11c3 review F2/F4; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

The grant watcher (`IhrGrantWatcher.tsx`, rules in `records.ts` `watchStep`) asks `GET /api/ihr/records-access/` every 30 s, on focus, on any 403 and when a chart opens; each look gives up after 10 s.

- **A ⭐ (taken).** Two failed looks in a row while a grant is held or a chart is open → the page is cleared (every cached answer dropped, toast "Records access could not be confirmed", back to the records page). One failure is tolerated, so a single dropped request does not throw the clerk out.
- B. Clear on the first failed look (strictest; a flaky network interrupts MRD often).
- C. Never clear on failures; rely on the grant's own expiry time (the screen could outlive an admin's early end while offline).

*Recommendation A:* the backend already refuses every read after the end, so this only decides how long already-loaded data stays on screen (at most about 40 s). Reverse: `MAX_FAILURES_WHILE_HELD` in `records.ts`.

### Q-K58 — What does the audit trail say about MRD listing their own grants? (P1-T11c3 backend; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** The list request keeps its path's purpose `records` (as asking for and ending a grant do, b4's `PURPOSES`) but carries **no** `grants` key, so it never looks like a read of the record under a grant. The records page's list (`?names=1`) shows the patient's name and year of birth and is recorded as showing that patient.
- B. Give list requests their own purpose (e.g. `admin`), splitting `ihr_audit/middleware.PURPOSES` by method.

*Recommendation A:* the `grants` key is what tells a record read apart; one purpose per path keeps the audit rule simple. Reverse: split `PURPOSES` by method.

### Q-K59 — May the grant watcher's 30-second poll show patients? (P1-T11c3 review BA1; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** No. The bare `GET /api/ihr/records-access/` returns ids and times only and writes no "patient shown" audit entry; names come only with `?names=1`, which the records page asks for. Without this, every MRD page would add a patient-shown audit entry every 30 s.
- B. One list body for both (names always), accepting the audit noise.

*Recommendation A.* Reverse: drop the `names` switch in `endpoints.py` and the `none:names` row of `list_params.txt`.

### Q-K60 — Who decides pack items? (P1-T15b design; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Hospital-wide admins only, for every scope (the API's `IsHospitalAdmin`).
- B. Facility admins decide their own facility's items.

*Recommendation A:* one reviewed gate, and no facility content ships yet. B fits the facility packs of P1-T24. Reverse: widen the permission class in `ihr_refdata/api.py` and add a facility check.

### Q-K61 — How does an admin see what a pack applied? (P1-T15b design, Q-T10; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Each applied `PackApply` carries a per-item `changes` list (up to 500 items, plus the total).
- B. Counts only, with details left to the audit trail.

*Recommendation A:* Q-T10 asks for a list an admin can read. Reverse: store counts only and point to the audit trail.

### Q-K62 — What happens to identifier changes that could break existing data? (P1-T15b design, item 8; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Automatic unless a data check fails: `unique` switched on while values repeat, or `regex` changed while stored values no longer match. Then the item is pending with reason `data-check`, the counts are stored, accept is refused until the data is fixed, and keep is allowed. Checks are bounded and never run in a GET.
- B. Always wait for the admin.
- C. Always automatic.
- D. Accept with acknowledged counts.

*Recommendation A:* it keeps "automatic for unedited rows". CARE re-validates identifiers on every patient edit, so D would make front-desk demographic edits fail. Reverse: make every identifier change pending.

### Q-K63 — What if the hospital deleted an identifier config or form that a pack ships? (P1-T15b design, item 11; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** A deletion is a local edit. The item turns pending `deleted` only when the pack's content for it changes. Keep stays kept. Accept restores it.
- B. Restore automatically, as value sets are.
- C. Make it pending at every apply.

*Recommendation A:* CARE binds value-set slugs in code, but not these, and deleting one is the hospital's choice. Reverse: set the kind's restore flag.

### Q-K64 — How do identifier configs reach every web process after a change? (P1-T15b design, S11; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

CARE's `get_config` keeps a dict that is never cleared.

- **A ⭐ (taken).** Our plugin swaps in CARE's Redis-backed `model_from_cache`. The server refuses to start if the swap is missing (system check).
- B. Restart the web processes after a change.

*Recommendation A:* CARE's own edits have the same fault, and a restart rule gets forgotten. Reverse: remove the swap and document the restart.

### Q-K65 — How are pack form versions named and linked? (P1-T15b design, items 1-3; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Slug `ihr-pack-<key>-v<N>`. The old version is retired by status. Version 1 is linked to the hospital's top-level organisations, and later versions copy the previous version's links.
- B. One slug moved to the newest row.
- C. Version 1 left unlinked.

*Recommendation A:* slugs are unique and old answers must keep their form. An unlinked form is invisible to everyone on a hospital host, because superusers are refused there. Reverse: change the slug rule in `forms.py` (old answers keep the old slug).

### Q-K66 — Where does the "answered form is never edited in place" guard live? (P1-T15b design, item 4; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** In database triggers. Form side: frozen columns and hard delete are refused. Answer side: hard delete of `QuestionnaireResponse` and `FormSubmission` rows is refused, the form link is fixed after insert, and the lock is taken (FOR SHARE) on insert. Soft delete stays free.
- B. A viewset swap.

The answer-side trigger refuses hard deletes of patients' form answers. This bears on retention and on any future DPDP erasure path (an owner-only SECURITY DEFINER function would be needed), so the owner should confirm it explicitly.

*Recommendation A:* it covers every writer, including CARE's commented-out check and Django's cascade. Reverse: drop the answer-side triggers and rely on the form-side check.

### Q-K67 — Which facilities get a per-facility pack? (P1-T15b design, item 14; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Every live facility. A new facility gets its packs from a deduplicated on-commit task (facility packs only). A 15-minute beat job per active hospital is the backstop.
- B. The admin enrols facilities.
- C. Start-up as the only backstop.

*Recommendation A:* CARE's catalogue is per facility, and start-ups happen only on deploy. Reverse: remove the signal and the beat job.

### Q-K68 — What if a pack item's link target is missing? (P1-T15b design; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Write nothing. The item is pending `missing-link` and retried automatically.
- B. Write it without that link.

*Recommendation A:* a lab test without its specimen is wrong in a way staff would not notice. Reverse: let the handler skip the link.

### Q-K69 — May packs carry prices? (P1-T15b design; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Never. Prices and stock stay the hospital's. A pack may ship charge item definitions without price components.
- B. Packs ship prices.

*Recommendation A:* money is the hospital's decision, and an unedited price would change "automatically". Reverse: allow price components in a kind's handler.

### Q-K70 — How is the per-facility mechanism proven in b3? (P1-T15b design; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Real CARE models with test-only handlers.
- B. A test-only model and migration.

*Recommendation A:* it proves CARE's slug prefix, id arrays and PROTECT, with no migration. Reverse: add the test model.

### Q-K71 — Do identifier changes that loosen patient search wait for the admin? (P1-T15b design, item 7; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Yes. Pending `privacy`, whatever the edit state: partial search turned on, a retrieve check (DOB, year of birth, OTP) turned off, or a new active hospital-wide config with such options.
- B. Automatic for unedited rows (Q-T10 as written).

*Recommendation A:* a pack must not widen who can find a patient by identifier without the hospital's OK. Reverse: drop the `privacy` rule in `identifiers.py`.

### Q-K72 — What if a foreign form already sits at a pack form's slug? (P1-T15b design, item 3; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** `ihr-pack-` is a reserved namespace, enforced by the questionnaire trigger. If a foreign row is already there, the engine takes the next free suffix `-2` to `-9` and records it. Ten taken slugs is a `conflict`.
- B. Block until the form is renamed.

*Recommendation A:* renaming is impossible on a hospital host once a form has answers. Reverse: make a foreign row a `conflict`.

### Q-K73 — What happens to drafts and templates on a retired form version? (P1-T15b design, item 12; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** The old version is retired at once. The change list and item detail count the stranded drafts and response templates.
- B. Keep the old version active while drafts exist.

*Recommendation A:* pack form versions are rare and reviewed, and B leaves two live versions. Reverse: add a drafts check before retiring.

### Q-K74 — What if a new auto-numbered identifier replaces an old one? (P1-T15b design, item 10; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Pending `counter-restart`, with the old count shown. A `default_value` change on the same config is automatic.
- B. Automatic, warned in the change list.

*Recommendation A:* the new count starts at 0, and desk staff would see look-alike numbers. Reverse: drop the `counter-restart` reason.

### Q-K75 — What if the hospital renames a catalogue slug a pack bound to? (P1-T15b design, item 13; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Accepted for the test-only kinds. A production facility kind must first bind by `external_id` (a renamed bound row is pending `edited`, never a create).
- B. Bind in b3 now.

*Recommendation A:* no facility content ships in P1-T15b, so binding lands with the first real kind (b4+). Reverse: add the `external_id` binding to b3.

### Q-K76 — Who counts as a "hospital-wide admin"? (P1-T15b1 review, finding A3; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21 (A now; B, the recorded hospital root, as its own task before the first pilot))

The gate (`ihr_identity.staff_access.IsHospitalAdmin`, shared by the audit viewer of P1-T6b and the pack decisions of P1-T15b1) accepts an admin role on **any** active top-level organisation of the hospital. Nothing records which organisation is "the hospital": CARE's top-level organisations also include the government state organisations (`ihr_load_geography`) and role organisations, and the dev seed's admins hold their role on a role organisation.

- **A ⭐ (taken).** Keep the gate as it is for now (no change to who sees the audit trail today). Known limit: an admin role on a state organisation would also pass.
- B. Record one "hospital root" organisation per hospital at provisioning (a field on the hospital profile) and accept only admins there; the seed and provisioning move their first admin onto it.
- C. Accept only org_type `team` top-level organisations: this locks out every current hospital-wide admin, including the seed's.

*Recommendation A now, B before go-live:* B needs the provisioning flow and the seed to change together, and P1-T15b2a item 1 needs the same answer, so it is best built once as its own small task after you confirm. Nobody is given an admin role on a state organisation by our code today. Reverse: build B (a field plus a check in `hospital_wide_admin`).

*Built (P1-T25, 2026-11-22):* B, as a marked root organisation rather than a profile field (Q-K126…Q-K131). Only an admin role on the hospital's root is hospital-wide now.

### Q-K77 — Should a deleted identifier config still answer lookups? (P1-T15b2a; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

Our replacement for CARE's identifier-config lookup (`ihr_platform/identifier_cache.py`, so every web process sees a changed config at once) reads through CARE's own Redis-backed cache, and that cache reads soft-deleted rows too. CARE's original raised "does not exist" for a deleted config.

- **A ⭐ (taken).** Keep it: a patient's identifier recorded under a config that was later deleted still shows its label and rules. Only the same hospital's data is ever returned.
- B. Refuse deleted configs as CARE did (one check in `get_config`).

*Recommendation A:* an identifier already on a patient's record should keep displaying; nothing new can be recorded under a deleted config either way. Reverse: B.

### Q-K78 — Exact or case-insensitive item names for value sets? (P1-T15b2a; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

Design item 13 said pack items are matched to rows by lower-case name "for every kind". As built, identifier configs match case-insensitively, but value sets keep CARE's exact slug match.

- **A ⭐ (taken).** Exact for value sets: CARE binds value-set slugs exactly and its slug is unique, so a case-insensitive match could bind the wrong row.
- B. Case-insensitive for every kind, as item 13 was worded.

*Recommendation A.* Reverse: one lookup in `valuesets.rows`.

### Q-K79 — Re-check held identifier items at every start? (P1-T15b2a; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

An identifier-config item held because patients' data fails its rules (duplicates, values the new pattern refuses) is checked again at every start, because new patient data may have changed the answer. Each check is capped at 90 seconds and runs inside that hospital's start-up step.

- **A ⭐ (taken).** Keep it for now. No identifier content ships before Phase 2, so no hospital can have such an item yet.
- B. Start-up never runs data checks; held items are re-checked only by `ihr_pack_pending --recheck` or a nightly job.

*Recommendation A now, B before identifier content ships (Phase 2):* at fleet scale, many held items could each add up to 90 s to a release window. Reverse: B (skip `run_data_checks` under `--startup`, add a beat job).

### Q-K80 — Which organisations get a pack form's first version? (P1-T15b2b; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

CARE shows a form only through its organisation links, so a pack form's first version is linked to "the hospital" when it is created (design item 1). The rule for "the hospital" is the hospital-wide admin rule of Q-K76: every active top-level organisation. Those include the 36 state government organisations that `ihr_load_geography` adds.

- **A ⭐ (taken).** Link v1 to the active top-level organisations **except** government ones (`org_type` `govt`). The admin then widens or narrows the links in CARE.
- B. Link v1 to every top-level organisation, government ones included (the Q-K76 rule as it stands).
- C. Wait for Q-K76 B (a recorded hospital root) and link v1 only to it.

*Recommendation A now, C once Q-K76 B is built:* a form linked to every state is noise in CARE's lists and makes no sense for a hospital's own form. No form content ships before Phase 2 (test packs only). Reverse: one filter in the forms kind's link step.

### Q-K81 — What happens to a pack form in a hospital that has only government organisations? (P1-T15b2b review E1/E2; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

A newly provisioned hospital has only the state government organisations that `ihr_load_geography` adds, so under Q-K80 A a pack form's first version has nothing to link to. A form with no links is invisible on a hospital host.

- **A ⭐ (taken).** Hold v1 as pending `missing-link` (Q-K68 A) until the hospital has an active non-government top-level organisation; it is re-checked at every apply and then created and linked. Accept is refused while it waits.
- B. Link v1 to the admin's state organisation (overrides Q-K80 A).
- C. Settle Q-K76 B first (a recorded hospital root) and link v1 only to it.

*Recommendation A now, C before form content ships (Phase 2):* nothing is written that no one can see, and no form content ships before Phase 2. Reverse: change `forms.missing_link` and the link step in `forms.py`.

### Q-K82 — How will a DPDP erasure get past the answered-form guard? (P1-T15b2b review Q2; auto-accepted by Claude, 2026-11-16, confirmed by you 2026-11-21)

The answer-side triggers (Q-K66 A) refuse every hard delete and TRUNCATE of form answers, also through a cascade from a patient or visit, and also from an owner-only SECURITY DEFINER function. Only Django's flush between tests turns `ihr.answers_truncate` on.

- **A ⭐ (taken).** Keep the refusal; decide the erasure path in P4-T1 (DPDP), with the retention rules.
- B. Now: a bypass setting the trigger checks, usable only by the owner role.
- C. Now: erase by redacting answers in place (the row stays, the content goes).

*Recommendation A:* no erasure feature exists yet, and the right answer depends on the retention rules (medical records are kept for years). Reverse: add the chosen path to `forms_guard.py` with its own test and mutation row.

### Q-K83 — How does the audit trail get across a whole-database restore? (P1-T22c design; auto-accepted by Claude, 2026-11-17, confirmed by you 2026-11-21)

A whole-database restore puts every hospital back to one moment T, but the fingerprint bucket keeps claims for seals made after T. Verify and the watch read that as tampering.

- **A ⭐ (taken).** Export each hospital's tail since T before the restore, record a plan in the bucket, reload the tail into the existing archive tables afterwards, and write ordinary restore events.
- B. Keep the tails in the bucket only; verify reads them there.
- C. Write events without tails ("trust the operator").

*Recommendation A:* it is the only one that the older build a rollback ends on can still verify, and it keeps the evidence. B needs the decryption key in the watch; C would let an owner hide a rewrite. Reverse: a new design; A keeps no new tables, so nothing is lost by switching later.

### Q-K84 — Where do the exported tails (packages) live? (P1-T22c design; auto-accepted by Claude, 2026-11-17, confirmed by you 2026-11-21)

A package holds one hospital's superseded seals and entries, so it holds personal data.

- **A ⭐ (taken).** Local copy plus the dumps bucket, encrypted with the dump key. Without dumps (`IHR_DUMPS` off) a whole-database restore that would export is refused unless `--local-packages-only --reason`, and the plan records that choice. Local copies are deleted once the result and the bucket copy verify.
- B. Local only.
- C. The fingerprint bucket, encrypted (no: 10-year retention of personal data).

*Recommendation A:* it survives a dead server mid-rollback and is the same data class as the dumps. Reverse: drop the bucket copy step in `audit-supersede export`.

### Q-K85 — What happens to hospitals onboarded after T? (P1-T22c design; auto-accepted by Claude, 2026-11-17, confirmed by you 2026-11-21)

They are gone from the restored database, so the watch reports "fingerprinted but no longer in the database". The new `absent` event closes that, but it could also hide a deleted live hospital.

- **A ⭐ (taken).** `absent` is accepted only when no fingerprint of that uuid was stored before the plan's T minus clock skew, the plan lists it as absent with L = 0, and the timeline checks pass. The slug stays reserved (re-onboarding gets a new slug). A rollback to a build before P1-T22c still alerts for such hospitals: it lists them and needs `--accept-absent`.
- B. Same slug allowed again, with an owner override written to the bucket.
- C. Alert until the hospital is offboarded.

*Recommendation A:* it keeps "slugs never reused" and cannot close a live hospital. Reverse: B or C change one check in `unknown_hospitals`.

### Q-K86 — Rollback to a build that cannot hold the tails? (P1-T22c design; auto-accepted by Claude, 2026-11-17, confirmed by you 2026-11-21)

A build before P1-T22b has no archive tables. A build between P1-T22b and P1-T22c has them, but reads generations from the database only and does not know `absent`.

- **A ⭐ (taken).** Refuse a target before P1-T22b (fix forward). For a pre-P1-T22c target, refuse `--accept-unreadable` and list hospitals made after T (`--accept-absent`).
- B. Allow both; the cuts stay as alerts.

*Recommendation A:* B leaves an alert nobody can clear. Reverse: remove the refusal in `rel_rollback` (staging.sh).

### Q-K87 — A hospital's tail cannot be read at all (lost)? (P1-T22c design; auto-accepted by Claude, 2026-11-17, confirmed by you 2026-11-21)

No readable copy exists (damaged schema, or seals after the repository's last archived WAL).

- **A ⭐ (taken).** Stays critical (AuditChainBroken) until `accept-loss <id> --slug --reason "<incident>"`, which moves it to a permanent AuditLostTail warning that never excuses or hides the claims. "Lost" applies only to claims stored after the source's provable end (newest archived WAL, or the copy's end) recorded in the plan; never to a hospital any source exported; and accept-loss never clears a mismatch at or below the restored head.
- B. Silence the alert per hospital.

*Recommendation A:* B hides evidence. Reverse: B is an alert silence in Alertmanager; no code.

### Q-K88 — May `restore --target` run without an export? (P1-T22c design; auto-accepted by Claude, 2026-11-17, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Refused unless `--no-supersede --reason "<incident>"`, which writes a plan and result with every hospital `lost`: the timeline change is explained and the cuts stay. In a disaster (flow b) the restore to T and `up` come first; export from the repository copy and load follow, so hospitals are served without waiting for two full restores.
- B. Allowed with a warning only.

*Recommendation A:* an unexplained cut must leave a record. Reverse: drop the check in `restore`; flow b still works.

### Q-K89 — Should a changed database timeline be checked? (P1-T22c design; auto-accepted by Claude, 2026-11-17, confirmed by you 2026-11-21)

The watch records the Postgres timeline each pass.

- **A ⭐ (taken).** Yes. Results form a chain: each plan's old timeline equals the previous result's new timeline; only the newest result must equal the current timeline, and older events are accepted through the chain. A plan's old timeline must differ from its result's new one, the result is stored after the plan, and the watch's own timeline history must show the change between their stored times. An unexplained change is critical. `audit-supersede announce-timeline --reason` records a pg_upgrade or a server move (no hospitals).
- B. No, events alone. Revisit if a standby failover is added (it changes the timeline too).

*Recommendation A.* Reverse: B removes the watch check and the chain test; events then rest on the segment rule only.

### Q-K90 — How long are packages kept? (P1-T22c design; auto-accepted by Claude, 2026-11-17, confirmed by you 2026-11-21)

- **A ⭐ (taken).** As the dumps, except packages holding unsealed-only, lost or refused content, which are kept as long as the archive (nothing else holds those entries). Local copies are deleted once the result and the bucket copy verify.
- B. 400 days for every package, like the log archive.

*Recommendation A:* the archive tables and backups already keep what was loaded. Reverse: change the prefix rule in `deploy/dumps/expire.py`.

### Q-K91 — Who holds the key that writes restore plans, results and events? (P1-T22c attack S1; auto-accepted by Claude, 2026-11-17, confirmed by you 2026-11-21)

Celery holds the fingerprint writer key, so with the owner password a compromise could write a restore event in the older shape and skip the new checks.

- **A ⭐ (taken).** A separate add-only key scoped to `anchors/ihr-events/` and `anchors/ihr-restores/`, made on the owner's deploying machine and kept in the owner's password manager (like the dumps key). Servers never hold it except inside the owner-run restore one-off. Celery's key is limited to `anchors/<suffix>/` hourly fingerprints. Until the key exists, the new checks are documented as not a security boundary.
- B. Keep one writer key (Celery holds it).

*Recommendation A:* B makes the plan, result and timeline checks decoration against a Celery compromise. Reverse: B, with the checks documented as defence in depth only.

### Q-K92 — Where do the database timeouts live? (P1-T23 design; auto-accepted by Claude, 2026-11-17, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Per-process startup options from one code table (`ihr_platform/db_limits.py`, set in `apply_tenancy`). They reach dev, CI and staging whatever role connects, and keep the rule that a role stores no setting of its own (S9). Drift is caught three ways: the role check compares the live values with the process's profile (reason `limits`, at the first connection and every later one); `staging.sh up`, `release` and `db-roles --assert` check the server's lock table, `shared_buffers`, `/dev/shm` and any setting stored for the whole database; and the log alert DatabaseStatementTimeouts.
- B. `ALTER ROLE ihr_app SET ...` with an allow-list in the role check: covers a non-Django client, weakens S9, misses dev.
- C. Both.

*Recommendation A.* Not a security control (any session may `SET` them again): they bound accidents and noisy neighbours. Reverse: B or C by turning `role_problems` into an exact allow-list. A metric alert on `max_locks_per_transaction` was not built: the pinned postgres-exporter has no settings collector; the up/release/assert check covers it.

### Q-K93 — What are the limit values? (P1-T23 design; auto-accepted by Claude, 2026-11-17, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Web (gunicorn, the back-channel service, beat, dev runserver): statement 30 s, lock 10 s, idle in a transaction 60 s. Worker (celery worker, staff worker, app-role one-offs): 300 s, 30 s, 300 s. Owner (migrate, admin, provisioner, restore, fleet runner, loaders): no statement or lock limit at session level (their DDL paths keep their own `SET LOCAL lock_timeout`), idle in a transaction 30 min. Tests: as web. The audit export's snapshot sets 10 min idle for itself.
- B. Tighter web (15 s). C. No idle limit.

*Recommendation A:* every request is one transaction (`ATOMIC_REQUESTS`) that sits idle while Python calls Keycloak (5 s) or S3. Reverse: edit `PROFILES`.

### Q-K94 — How big is the database container? (P1-T23 design; auto-accepted by Claude, 2026-11-17, confirmed by you 2026-11-21)

- **A ⭐ (taken).** `max_locks_per_transaction` 256 (staging, dev, CI and both restore starts: a copy that replays this server's WAL refuses to start with a smaller value), `effective_cache_size` 3GB, `/dev/shm` 256 MB on staging and in dev (review B1: shm and `shared_buffers` are both charged to the container's limit, and 1 GB plus 512MB filled the 1536m limit; a larger `IHR_PG_SHM` needs a larger `IHR_PG_MEMORY`, checked by `scripts/test_staging_postgres.py`; the database and Keycloak carry negative `oom_score_adj` because the containers' limits sum to more than 8 GB, review B2). Until the 8 GB test, `shared_buffers` stays 512MB and the db limit 1536m; the 8 GB server's values are `IHR_PG_SHARED_BUFFERS=768MB` and `IHR_PG_MEMORY=2304m` in `.env`.
- B. `/dev/shm` 256 MB as before.

*Recommendation A.* Reverse via `.env` (`IHR_PG_SHM`) and `deploy/postgres/server-settings.txt`.

### Q-K95 — Where does Keycloak's back-channel logout land? (P1-T23 design; auto-accepted by Claude, 2026-11-17, confirmed by you 2026-11-21)

A sign-out holds a backend thread while Keycloak posts the logout back into the same eight threads.

- **A ⭐ (taken).** Its own `backchannel` service: the same image, gunicorn 1 worker x 2 threads, internal network only, no route, app role, no client key, serving the logout path, `/ping/` and `/health/` only; about 0.2 GB of memory (limit 256m). Existing hospitals' Keycloak clients take the new address at the next `staging.sh up` (it runs the sync). **Unmeasured:** the shared-pool storm against the own service (design L3) was not run, and the dev runserver cannot show the self-wait; the choice rests on reasoning until the 8 GB test. A crash, OOM kill or unhealthy `backchannel` pages through ContainerNotRunning / ContainerUnhealthy (critical; its image health check calls `/ping/`); logouts then fall back to the 60 s refresh.
- B. A second gunicorn in the backend container. C. Share the pool and alert.

*Recommendation A.* Reverse: point `IHR_BACKCHANNEL_LOGOUT_URL` back at `backend`, run the sync, drop the service.

### Q-K96 — Should `transaction_timeout` be set? (P1-T23 design; auto-accepted by Claude, 2026-11-17, confirmed by you 2026-11-21)

- **A ⭐ (taken).** Not now. Restores, tests and long tasks are single transactions; the statement and idle limits are enough for requests. Revisit after the 8 GB test.
- B. Web 120 s.

### Q-K97 — How does the load test sign in? (P1-T23 design; auto-accepted by Claude, 2026-11-17, confirmed by you 2026-11-21)

- **A ⭐ (taken).** The real OIDC flow with seeded synthetic users (`scripts/load-test.py`; a one-time code from the user's seeded secret when the account has one). Nothing new on the server.
- B. A staging-only command that mints sessions: faster, but a sign-in bypass that must never reach production.

### Q-K98 — Load target and pass criteria (P1-T23 design; auto-accepted by Claude, 2026-11-17, confirmed by you 2026-11-21)

- **A ⭐ (taken).** 20 active staff per hospital, one request per 15 s each; levels 25, 50, 100 hospitals; pass: no OOM kill and no swap-in; p95 < 1 s and p99 < 3 s; errors < 0.5 % (none from timeouts on the request mix); every back-channel token delivered within 2 s; Celery queue drained within 60 s of each burst; the dump finishes in its window; db memory below its limit with 15 % margin. The cell cap becomes the lower of 100 and the passing level. **The 8 GB run is blocked on the owner** (no server); `docs/reviews/p1-t23/build.md` holds the plan.
- B. The owner's own numbers per hospital size.

### Q-K99 — How far below T may a disaster restore's fork point go? (P1-T22c2a, audit S2; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

After a disaster restore to T the load computes the fork point F (the restored head). The database owner could rewrite restored seals to push F lower and have older trail excused.

- **A ⭐ (taken).** The first superseded seal must hold an entry (its `at` is in the seal hash) newer than T − 15 min: the 10-minute clock allowance plus the worker profile's 5-minute idle-in-transaction limit (`db_limits`). Every reader checks it again from the archived segment. A single-hospital restore event after T may name a lower F. A seal sealed after T whose entries are all older (the sealer stopped for 15 min before T) is refused (fails closed; `close` it).
- B. T − 40 min (the owner profile's 30-minute idle limit): fewer refusals, a 40-minute rewritable window.
- C. No floor (F floats down to L): up to about an hour before T rewritable.

*Recommendation A:* it shrinks the rewritable window to 15 minutes, and the only false refusal needs the sealer down before T. Reverse: change `events.FLOOR_MARGIN`.

*Known limit (review T4):* the idle-in-transaction limit bounds only the idle gaps of a transaction, not its total time (the worker profile's statement limit is 300 s, the owner profile has none, and no `transaction_timeout` is set: Q-K96). A transaction held open longer than the margin that commits just after T makes the first seal after T hold only older entries: a false refusal that fails closed (availability only: `close` it, the cuts stay). Setting `transaction_timeout` at or below the margin in the profiles that write audited rows would remove it; that reopens Q-K96, so it waits for the 8 GB test.

### Q-K100 — What happens to a whole-database restore that can never be loaded? (P1-T22c2a, design-c2 Q-K104; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

A pending load blocks every later whole-database restore and every single-hospital restore.

- **A ⭐ (taken).** `staging.sh audit-supersede close <id> --reason` writes its result with every hospital `refused` and the reason (`closed`); the timeline change is explained, the cuts stay critical. Refused while any event of that restore is in the bucket (load it instead). `restore --target --no-supersede --reason` uses the same result for a restore that exports nothing.
- B. Nothing: the restore stays pending for good.

*Recommendation A:* B blocks restores for good. Reverse: drop `supersede.close`.

### Q-K101 — Builds older than P1-T22c2a (P1-T22c2a, design-c2 Q-K101; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

Older builds read a disaster restore's export doc as a bad object, and its events excuse nothing there.

- **A ⭐ (taken).** `ihr_audit.supersede.LEVEL` = 2 (a build without it is 1). A rollback to a build below the bucket's level is refused before the confirmation (fix forward). `restore --target` needs level 2, or `--no-supersede --reason`.
- B. Allow it; the older build alerts on the objects.

*Recommendation A,* as Q-K86. Reverse: drop `rel_level_guard`.

### Q-K102 — A restore without a time target (P1-T22c2a; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

`staging.sh restore` to the newest moment or to a release's restore point has no time T to write in a stub.

- **A ⭐ (taken).** c2a writes restore events only for `restore --target <time>`. The others warn that the timeline change and the cuts will alert (record an incident). P1-T22c2b decides them with the lost tails.
- B. Use the restored moment (the copy's last transaction) as T.

*Recommendation A:* B needs the lost-tail rules first. Reverse: give `restore_cmd` a T for them.

### Q-K103 — Where does a disaster restore's copy run? (P1-T22c2a, design-c2 Q-K103; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

- **A ⭐ (taken).** On the server, beside the serving stack: memory 768 MB, `--oom-score-adj 900` (killed first), the nightly dumper stopped meanwhile, the copy removed on every exit.
- B. On the owner's machine (personal data on a laptop).

*Recommendation A.* Reverse: run `audit-supersede export` elsewhere with the repository's keys.

### Q-K104 — How is a disaster stub's T bound to the real restore? (P1-T22c2a review T3; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

The stub's T was never checked against the target pgBackRest really used, so a hand-run stub with an earlier T could widen what the floor excuses.

- **A ⭐ (taken).** Use the restore's own record. The new timeline's history file says recovery stopped "before" the first commit after its target, which was not applied. Postgres also keeps the last commit it applied. `supersede_parent` writes both to state. `export-doc` refuses unless last applied ≤ T < stop, and the export doc carries both. Every reader requires them. The rule holds exactly when the restored database is the state at T, in a quiet database too.
- B. The review's rule: the stop within 1 s of T. The stop is never T itself, so this refuses a correct restore whenever no commit followed T within a second (at night, or in a small hospital).

*Recommendation A.* Reverse: change `events.recovery_problem`. The c2b ceiling (S6) completes it.

### Q-K105 — How is a restore closed without its load kept visible? (P1-T22c2a review T2; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

Q-K100 lets `close` and `restore --target --no-supersede` explain the timeline change without loading any tail. The cuts stay, but data written after the last fingerprint and before the restore leaves no claim, so its loss shows nowhere (audit S4's hole, reached through a path c2a adds).

- **A ⭐ (taken).** A gauge `ihr_audit_whole_restores_closed{how}` and the warning **AuditWholeRestoreClosed** stay on until the restore id is acknowledged in `ihr_audit/acknowledged_closed.txt` (restore id, date, what was reviewed), and `ihr_audit_verify` lists each unacknowledged closed result as a problem. A closed restore's `export.tar` stays on the server (the only copy of the old tail); `up` and `status` say so, and its expiry rule is P1-T22c2b's, with Q-K90.
- B. Refuse `--no-supersede` when T is older than the repository's archive end: no silent path, but no way out for a repository that is gone.

*Recommendation A:* B blocks the recovery it is for. Reverse: drop the gauge, the alert and the verify line.

### Q-K106 — A second disaster while a restore is not loaded (P1-T22c2a review E1/E4; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

Restore A (tl1 to tl2) is served and not loaded; the server is lost again. The repository's newest timeline is tl2, A's old timeline is tl1. A lost server can neither load A (its folder and database are gone) nor close it (`close` needs a database on another timeline).

- **A ⭐ (taken).** A pending plan from an **older** timeline than the repository's newest does not block the next stub or its export; it keeps alerting as pending until its own result. A restore from the repository straight after another one, before the next backup, has no backup set on the newest timeline: it is refused with the way out (`backup-now full`, or `--target-name`); an older timeline's set is not used, because it is right only for a T after the fork and the fork time is not known at that step.
- B. Keep refusing, and let `close` take `--timeline <the repository's newest>` with no database.

*Recommendation A.* Reverse: drop `ancestors_pass` in `supersede._refuse_pending`.

### Q-K110 — Where do the admin pages live? (P1-T24 design; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

- **A ⭐ (taken).** Under the facility: `/facility/:id/ihr/audit` (T24a), `/ihr/packs` (T24c), menu items in the facility sidebar, last, in care mode only (the pattern of records and totals). The page title says "whole hospital".
- B. Top-level `/ihr/audit`, `/ihr/packs`, from the user menu. C. Both.

*Limit:* an admin with no facility cannot reach them from a menu (none seeded; revisit with the hospital-root gate, Q-K76 B). *Reverse:* move the keys in `IhrRoutes.tsx` and the paths in `menu.ts`.

### Q-K111 — How does the web app know who is an admin? (P1-T24 design, attack decision; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

- **A ⭐ (taken, narrowed).** One new my-access field, `hospital_admin` (the audit trail's own gate `IsHospitalAdmin`; false for a superuser). The design's second flag `manage_staff` is dropped (zoom-out F2): the T24b staff card relies on the access GET instead. Cost: one `EXISTS`, pinned by `test_my_access` (10 to 11 statements without `?facility=`, 12 to 13 with).
- B. Probe the admin APIs and hide on 403: every page load would write an audit entry and a refusal.

*Reverse:* remove the field, `hospitalAdmin` in `access.ts` and the `audit` menu key.

### Q-K112 — A list of open invitations? (P1-T24 design; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

- **A ⭐ (taken).** Only per person, on the user page (T24b). B. `GET /api/ihr/staff/invitations/` and an "Open invitations" tab, only if CARE's lists hide inactive users (checked in T24b).

### Q-K113 — Confirming "Reset sign-in" (P1-T24 attack decision; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

- A. Type the username, with the "check who is asking" text.
- **B ⭐ (taken, changed from the design's A).** A plain confirm dialog with the warning text (zoom-out F8). Built in T24b.

*Reverse:* add the typed confirmation to the dialog.

### Q-K114 — The audit list's default range (P1-T24a; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

- **A ⭐ (taken).** Today in IST, From and To both. Fewer patient names are shown, and each name shown is recorded as viewed. B. The last 7 days.

### Q-K115 — Export default format (P1-T24c; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

- **A ⭐ (taken).** CSV first, JSON with proof as the second choice. B. JSON only. The export and verifier links move to T24c (zoom-out F4).

### Q-K116 — The audit API's date format (P1-T24a; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

- **A ⭐ (taken).** Keep YYYY-MM-DD in the API; the page shows and keeps DD-MM-YYYY and converts (`Utils/ihr/audit.ts`, `toApiDay`). B. Change the API to DD-MM-YYYY like owner totals.

### Q-K117 — A seeded hospital-wide admin login (P1-T24a; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

- **A ⭐ (taken).** `admin.priya` on alpha (`care-admin`, an authenticator pre-set), made hospital-wide by `seeding.make_hospital_admin`. B. Give `admin.bhavesh` a top-level role on beta (unneeded, see Q-K118).

### Q-K118 — A facility-only admin login for T24b (P1-T24a backend builder; proposed; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

In the dev seed `admin.bhavesh` (beta, `care-fac-admin`) already holds "Facility Admin" on a top-level organisation, so he is a hospital-wide admin and sees the audit trail. The design's reason for Q-K117 ("bhavesh must be refused on admins") does not hold on the demo.

- **A ⭐ (proposed).** T24b adds a facility-only admin login (Facility Admin at the facility root only) to test the A1 refusal. `roles-menus.spec.ts` lists bhavesh with the audit trail until then.
- B. Remove the top-level role from bhavesh's fixture (changes CARE's demo data).

*Built in P1-T24b:* `admin.anil` (alpha) signs in as `care-facility-admin`, "Facility Admin" at the demo facility's root only (`seeding.make_facility_admin`).

### Q-K119 — How is the audit page's patient and person filter set? (P1-T24a web builder; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

The design listed a patient search box and a typed "Who" box; the attack decision dropped "Who" (the actor is `user:<pk>:<username>`).

- **A ⭐ (taken).** Both are links: an entry's actor, and a patient's name in a row or in the sheet, filter on that exact actor or patient id (kept in the address as `actor=` and `patient=`, with a chip and "Show all"; the page asks the API for `actor_exact`, an exact match, while the API's `actor` stays a prefix match). No search box in T24a: CARE's patient search lists names the admin did not ask for, and each name shown is recorded as viewed. B. Add CARE's patient search to the filter row (a later task).

*Reverse:* add the box in `IhrAuditTrail.tsx`; the address and the API already carry the filter.

### Q-K120 — Should an admin of a state organisation pass the hospital-admin gate? (P1-T24a review A1; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

Review A1 found that `hospital_wide_admin` (the gate of the audit trail, the pack decisions and, since T24a, the `hospital_admin` flag and the Audit trail menu item) accepts an admin role on any active top-level organisation, government states included. Q-K76 A already names this limit. The reviewer suggested adding `org_type="role"`. That would not help: provisioning (`provisioning/steps.py`, `first_admin`) makes every hospital's first admin an **Administrator of its state organisation**, a top-level `govt` one. Pack tests also use `team` and untyped top-level organisations. Leaving out `govt`, or keeping only `role`, would lock every real hospital's first admin out of the audit trail and the pack decisions.

- **A ⭐ (taken).** Keep the gate. Pin the case with tests: the provisioned first admin is hospital-wide on my-access, `/audit/status/` and the pack API (`test_my_access`, `ihr_refdata test_api`). Both tests fail if the gate leaves out `govt` or keeps only `role`. The real fix is Q-K76 B: record one hospital root, move the first admin onto it, then accept only that root. Settle it together with Q-K118 before T24b's refusal (A1) relies on the gate.
- B. Leave out `govt` now and move the first admin onto a new top-level `role` organisation in provisioning, with a data step for existing hospitals. This is half of Q-K76 B, so it is better done once as Q-K76 B.
- C. `org_type="role"` only (the review's suggestion). This locks out provisioned first admins and the pack tests' `team` admins.

*Limit, unchanged:* anyone given an admin role on any state organisation passes. Only someone who may create users can do that, and our code does it only for the first admin. *Reverse:* none needed. Q-K76 B replaces this.

*Superseded (P1-T25, 2026-11-22):* Q-K76 B is built. A state-only Administrator no longer passes the gate (`test_a_state_only_administrator_is_not_a_hospital_admin`); provisioning places the first admin on the root.


### Q-K121 — How is "a hospital-wide admin at any hospital" protected from a reset? (P1-T24b backend builder, attack A1; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

A reset wipes a person's password and authenticator at every hospital, so attack A1 asked that someone who is a hospital-wide admin anywhere is never reset from one hospital's staff screen. Nothing in `public` or Keycloak holds roles today, and a hospital's web request must not read another hospital's schema.

- **A ⭐ (taken).** This hospital checks its own schema when asked (not offered, POST 403). The provisioner, which already reads every hospital's schema, checks every hospital whose schema is kept (`fleet.MIGRATE_STATUSES`: provisioning, active, suspended, offboarding; not archived; corrected by review A1/K2, 2026-11-21: "any hospital" in the binding decision, which an earlier draft had narrowed to ACTIVE) before it touches Keycloak (`invitations._admin_anywhere`, each read under the hospital's job lock); a provisioning hospital not yet past its schema step (no recorded schema state) is skipped, since it holds no StaffIdentity yet. Found as a hospital-wide admin **or a superuser** there (review A2/K3) → the request fails with code `protected`; a hospital it cannot read (held, restoring, other build, db_roles-gated) → `try-later` (fails closed). It also re-checks that the asker is still an active, not deleted, non-service account (review A3/K4) and still a hospital-wide admin (`not-allowed`). Exact at the moment of the reset, nothing new stored. The asking hospital learns exactly one bit (review A6): since "admin here" was already refused at request time, `protected` tells its admin that the person is a hospital-wide admin (or superuser) at some other hospital. It does not learn which hospital. A `protected` refusal counts toward the one-reset-a-day limit for that person, so it cannot be repeated for free (review A4); a `not-allowed` one does not (it says something about the asker, and must not block another admin).
- B. A public-schema flag "admin at some hospital" per Keycloak account, kept by signals on memberships, plus a nightly rebuild. Lets the GET hide the button, but goes stale on bulk changes and restores, and is a new table the app writes from every hospital.

*Limits:* the GET cannot know about other hospitals, so "Reset sign-in" can show for such a person; the request then ends `protected` and the card says so. **Fleet-wide try-later (review K1, kept on purpose):** the check fails closed on the first hospital it cannot read, so while any one hospital is held (a release window, a failed migration left held, a single-hospital restore, a db_roles gate), every reset at every hospital ends `try-later` and the admin asks again once that hospital is served; each reset also reads all N schemas (a shared job lock, a few queries each). Scoping the scan to the hospitals whose Keycloak organisation holds the `sub` was considered and not taken: it would make the boundary rest on Keycloak's organisation membership matching the CARE `StaffIdentity` links (they are written by different steps and can drift, e.g. after a restore), and Keycloak could not then be the fail-closed fallback for a missing link. `try-later` is a final answer for that request (it is not re-queued like a held hospital's own job): the admin asks again by hand, and this is by design (review A5). Resets are rare, owner-visible and retryable; a bounded automatic re-queue can come later if this bites. **Known one-bit limits (review A6, K10):** the one-reset-a-day check counts requests from every hospital (`StaffAccessRequest` is in `public`), so a second hospital's admin who asks within the day is told only "reset in the last day" (no hospital named, since review K10); that still shows that a reset happened for the person recently, which the person's own e-mail already says. *Reverse:* drop the provisioner check (one function).

### Q-K122 — May one hospital-wide admin reset another at the same hospital? (P1-T24b backend builder; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

The attack's decision says a target who is a hospital-wide admin at *any* hospital is protected; the module's old docstring allowed "another hospital-wide admin".

- **A ⭐ (taken).** No. A hospital-wide admin is never reset from a hospital's staff screen, by anyone. They use "Forgot password" for a lost password; a lost authenticator goes to the platform operator, who today removes it in Keycloak's own admin console (there is no IndieHR command or console button for this yet: a follow-up for the operator console).
- B. Allow a peer at the same hospital (only if the person is a hospital-wide admin nowhere else). More convenient; one compromised admin account could then wipe every other admin's sign-in.

*Limit:* a hospital whose only admin loses their authenticator needs the operator. *Reverse:* in `staff_access.reset_allowed` and `invitations._admin_anywhere`, skip the asking hospital.

### Q-K123 — Does a refused (`protected`) reset count toward the one-a-day limit, and what does the card say in Hindi? (P1-T24b review A4, W3; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

Before the review a failed reset never counted toward the daily limit, so a `protected` refusal could be asked again and again (one open request at a time; each one reads every hospital's schema), and every 400 showed the backend's English sentence on the Hindi card.

- **A ⭐ (taken).** A `protected` failure counts: the next ask for that person (any hospital) within the day is a 400 with code `protected-recent` ("Try again tomorrow"). `not-allowed` and `try-later` do not count (they say something about the asker or the moment, and must not block another admin). Every 400 from the staff API is `{code, detail}` (`staff_access.Refusal`); the card chooses its own phrase from the code (`ihr_staff_error_*`, English and Hindi) and never shows `detail`. Alert `StaffResetRefused` (`deploy/logs/alerts/staff.yml`) watches protected, not-allowed, admin-target and reset-refused, 3 or more in an hour at one hospital.
- B. Count every failure. Simpler, but one admin's `not-allowed` or a hold's `try-later` would block a colleague for a day.

*Limit:* a person whose admin role is removed within the day cannot be reset from another hospital until the day passes. *Reverse:* drop the `protected-recent` query in `staff_access.StaffAccessView.post`.

### Q-K124 — How does the pack API name a facility, and how does a refusal name its kind? (P1-T24c backend builder, G6; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

The pack updates screen shows "Facility (name or Whole hospital)" and must map every refusal of a decision to a fixed phrase (readiness M3). The list gave only the facility's external id, and refusals gave an English `detail`, with `stale`/`blocked`/`busy` flags on some.

- **A ⭐ (taken).** `facility` stays the external id (the value `?facility=` takes, and what tests and the design name); a new `facility_name` carries the name (null for a hospital-wide item), from the join the list already makes, so no extra query. Every refusal other than a field-keyed 400 carries `code` (`not-found`, `not-live`, `stale`, `blocked`, `busy`, `rate`, `in-flight`, `unavailable`; the engine's 400 is `{reason, code: "refused"}`). The older flags stay. The word is `code`, not `kind`, because a pack item's own `kind` (valueset, questionnaire) is in the same body.
- B. Make `facility` an object `{id, name}` (what the P1-T15b design first said): one field, but it changes a shape the tests and the filter already use.

*Limit:* an old client reading only `detail` is unaffected. *Reverse:* drop `facility_name` in `api._summary` and the `code` argument of `api._refused`.

### Q-K125 — Which pack item does the dev seed leave waiting, and how? (P1-T24c backend builder, G5; auto-accepted by Claude, 2026-11-21, confirmed by you 2026-11-22)

The dev stack has one pack release, so nothing waits for a decision and the screen is empty in a click-through. The engine skips an apply of a release it already applied, and treats an edit the pack has no newer content for as the hospital's own.

- **A ⭐ (taken).** `seeding.make_waiting_pack_item()` (in `ihr_seed_demo`, not with `--passwords-only`): when nothing is pending, it renames one system value set (`system-additional-instruction`, a name only) to a demo name, drops that item's pack record, and runs the packs with `recheck`, so the engine holds it as pending, reason "unknown origin" (nothing earlier to show in the "earlier" column). Accepting restores the pack's name; keeping leaves the demo name; a later seed after a decision makes a new one. It touches no test-only kind and no pack file.
- B. Ship a second pack release with a changed item. Gives an "edited" item with an earlier version, but a pack version file is immutable and ships to every hospital.

*Limit:* the demo shows "unknown origin", not "edited". *Reverse:* delete the call in `ihr_seed_demo` and `seeding.make_waiting_pack_item`.

### Q-K126 — Where is a hospital's root recorded? (P1-T25 design attack Z1/D3; auto-accepted by Claude, 2026-11-22, confirmed by you 2026-11-22)

Q-K76 B needs one "hospital root" per hospital: the organisation whose admins are hospital-wide (audit trail, pack decisions, records-grant ends, sign-in resets).

- **A ⭐ (taken).** The organisation itself is the record: a top-level CARE organisation carrying `metadata.ihr = "hospital-root"`. One predicate, `ihr_identity.roots.valid_root()`, accepts it only if exactly one is marked and it is active, not deleted, parentless, of type `role` and has no managing organisations; anything else closes the admin screens for everyone. Dumps and restores carry it with no new code.
- B. A one-row table `ihr_identity.HospitalRoot` (the first design), read-only for the app role.
- C. A field on the hospital profile (Q-K76 B's wording).

*Why A:* the app role can already write memberships, so a protected pointer protects nothing that matters; A has no table, migration, restore branch or dump change. *Limit:* a superuser can still edit or unmark the root (superusers are trusted everywhere). *Reverse:* add B and make `valid_root` read it.

### Q-K127 — What kind of organisation is the root? (P1-T25; auto-accepted by Claude, 2026-11-22, confirmed by you 2026-11-22)

- **A ⭐ (taken).** A new top-level `role` organisation, `system_generated`, named "Hospital administration" (the name means nothing; `ensure_root` never adopts an organisation by name). CARE refuses non-superusers who try to update, deactivate, retype or delete a role organisation, or add a child to it, and its "Add user" places staff only in role organisations (checked first: `test_r5_a_root_admin_adds_staff_to_the_root_up_to_administrator`).
- B. A `team` organisation: an "Admin" of it could deactivate, rename or retype it.
- C. Adopt an existing one (the fixture's "Administrator"): a guess, with members already on it.

*Limit:* an Administrator on the root can add staff to it only with roles whose permissions are a subset of their own (Administrator yes; Admin, Staff no), as CARE rules. *Reverse:* change `roots.ensure_root`.

### Q-K128 — What happens to the first admin's state membership? (P1-T25; auto-accepted by Claude, 2026-11-22, confirmed by you 2026-11-22)

- **A ⭐ (taken).** Removed. New hospitals never get it (`first_admin` places the admin on the root only); for existing ones the fleet step soft-deletes only the moved admin's own Administrator row on a top-level government organisation, audited. Other admin rows on states are counted (`ihr-root: … outcome=state-admins n=<k>`) and removed only by the operator (`ihr_hospital_root remove-state`).
- B. Keep it, ignored by the gate.

*Why A:* B is not harmless: an Administrator of the state lists every patient of the hospital with an address in that state, visit or not. The admin keeps user and facility creation (as root Administrator) and facility updates (as Facility Admin). *Reverse:* drop the `remove_state` call in `roots.converge`.

### Q-K129 — Which existing admins move onto the root? (P1-T25 attack S1/D2; auto-accepted by Claude, 2026-11-22, confirmed by you 2026-11-22)

- **A ⭐ (taken).** The fleet step `root` (active and suspended hospitals; start-up and releases; also the restore) places only the provisioning record's first admin, and only if that person still exists, is active or awaiting setup, and is an admin on some top-level organisation today. Everyone else goes through `ihr_hospital_root show`, then `place`.
- B. Also carry over everyone who passes the old gate (carries the hole over).
- C. The operator only (locks real admins out after a release).

*Limit:* a hospital made by `ihr_create_tenant` (dev, tests) gets a root with nobody on it (`no-admin`, alert `HospitalRootProblem`) until the seed or `place` adds one. *Reverse:* change `roots.converge`.

### Q-K130 — Who is protected from a sign-in reset? (P1-T25; auto-accepted by Claude, 2026-11-22, confirmed by you 2026-11-22)

- **A ⭐ (taken).** The person being reset keeps the old, wider rule (`staff_access.top_level_admin`: an admin role on any top-level organisation, the root included), here (`reset_allowed`, `_reset_sub`) and at every other hospital (`invitations._admin_anywhere`). Only the asker's side narrowed to the root. The refusal keeps the existing `admin-target` code.
- B. The root only, like the gate.

*Why A:* protection must not narrow while admins move. *Limit:* a state-only admin's lost authenticator goes to the platform operator (Q-K122). *Reverse:* use `hospital_wide_admin` on the target side.

### Q-K131 — Does anything guard the root beyond CARE's own rules? (P1-T25 attack Z2; auto-accepted by Claude, 2026-11-22, confirmed by you 2026-11-22)

- **A ⭐ (taken).** No trigger: CARE's role-organisation rules, pinned by `ihr_identity.tests.test_root.CareRoleOrganisationRules` (update, deactivate, retype, delete and a child refused; a managing organisation makes `valid_root` None, so the screens close and never widen). An upstream sync that changes those rules fails the tests. Pack forms are unchanged (Q-K80 A, Q-K81 A stand: the root is a non-government top-level organisation, so a new form links to it).
- B. A database trigger refusing changes to the root's row (the first design): its only extra reach is stopping a superuser.

*Reverse:* add B.

### Q-K132 — Where does the first pilot's real data run? (P1-T28, ADR-020; auto-accepted by Claude, 2026-11-22; **confirmed by owner, 2026-11-23**)

CLAUDE.md says "No real patient data outside production", but everything built since P1-T8 is called "staging", and ADR-003 and PLAN §3.2 described production differently (closing review PR1-2).

- **A ⭐ (taken).** The same Compose stack (`deploy/staging/`, `scripts/staging.sh`, `scripts/deploy.sh`) deployed as a separate instance, **production-1**. It gets its own new VM, its own `init` (own secrets), buckets with the instance in their names, its own Keycloak, acme-dns, ZeptoMail token and suffix (`<slug>.app.amhcloud.in`), and `IHR_ENVIRONMENT=production-1`. It never shares a VM, database, bucket, key or Keycloak with a synthetic environment, and the P1-T26 rehearsal VM is never reused for it. Its settings folder lives outside the repository checkout (`deploy.sh --env-dir`). The code supports this by configuration today. The "staging" names in folders and scripts stay; they now mean "the Compose stack". Safety checks still to build (ADR-020 §3, E1 to E5): `IHR_ENVIRONMENT` required, proof stand-ins refused on production, `deploy.sh` refusing a mismatched settings folder, the environment named in dump manifests, and a "synthetic data only" banner off production.
- B. Put the pilot's real hospitals on the one staging server as it is. This is the cheapest, but it leaves nowhere to rehearse a release or drill away from real data, and demo hospitals would share a server with real ones.
- C. Build P9-T7 (k3s/RKE2 + CloudNativePG) or a managed database first. This rebuilds the whole Phase 1 ops layer before the pilot (roughly 80 to 150 agent hours), and ADR-011 rules out managed databases.

*Why A:* every go-live gate was proven on this stack, a second instance needs no new code, and the no-real-data rule then holds. *Limit:* until check E3 is built, only the operator's care stops the synthetic settings folder from being deployed to production-1, or the reverse. *Also decided in ADR-020:* real dumps are drilled only on production-1's own `ihr-restore-drill` copy, so real data never leaves production-1. P9-T7 is triggered by measured limits (ADR-020 §4: availability needs or too many cells), not by "about 10 tenants"; a bigger VM and more Compose cells come first. *Open for you:* provider, VM size (P1-T26 decides 8 or 16 GB, plus disk for the drill copy) and when to create it (after the go-live gates, when a pilot signs). *To reverse:* choose B or C and amend ADR-020. With B, the no-real-data rule must be reworded and the staging server's synthetic hospitals removed first.

### Q-K133 — Where does synthetic staging live? (P1-T28, ADR-020 §2; auto-accepted by Claude, 2026-11-22; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** For now, this workstation: `staging.sh local-proof` and the throwaway local VM (`deploy.sh --local-proof`). P1-T26's rented VM is synthetic staging while it exists, and is destroyed afterwards. An always-on paid staging VM comes only when you choose to pay for one, for example when pilot staff need a training URL.
- B. A second small paid VM from the first pilot on, always on, with its own suffix (e.g. `<slug>.stg.amhcloud.in`). It costs about ₹4,780 a month with GST at ADR-011's E2E price, or less if P1-T26 shows the small settings fit.
- C. The restore-drill copy. **Not possible:** it holds production data.

*Why A:* it costs nothing until a pilot needs a public demo or training URL, and releases and drills are already rehearsed in the local proof. *Rule in every case:* synthetic staging never receives real data, including production-1's backups, dumps, log archive or exports, and never holds a production-1 key. *To reverse:* choose B, then run `staging.sh init stg.amhcloud.in` on a new VM with its own buckets and acme zone.

### Q-K134 — Who is the operator in the record? (P1-T27; auto-accepted by Claude, 2026-11-22; **confirmed by owner, 2026-11-23**)

Every privileged `staging.sh` command now writes an `ihr-operator:` line naming who ran it (docs/reviews/p1-t27/attack.md, "Orchestrator decision").

- **A ⭐ (taken).** `--operator NAME` or `IHR_OPERATOR`, required, no default; refused if empty, a shared or system name (`ihr`, `root`, `admin`, `postgres`, the bootstrap admin user) or not `^[a-z][a-z0-9.-]{1,31}$`. `from=` (the SSH client address) plus sshd's own login line give the join. Attribution by convention, not authentication.
- B. A personal Unix account per operator (real authentication). Right when a second operator joins (ADR-020's production-1).
- C. A reviewed allow-list `IHR_OPERATORS` plus the SSH key fingerprint (the first design): ceremony for one operator, and the fingerprint comes from a variable the operator controls.

*Reverse:* B is a bootstrap change plus a check in `op_need`.

### Q-K135 — What does the record keep of the command? (P1-T27; auto-accepted by Claude, 2026-11-22; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** Option names only, every value replaced by `<v>` (`command=` too); a reason of 3–120 characters from `[A-Za-z0-9 ._:/#-]`, refused if it has a run of 6 or more digits. `manage shell`/`dbshell`, `logs`, `logs-archive-get` and journalctl through staging.sh are recorded as data access (reason and hospital required).
- B. A plus a hash of the full command: a short command can be recovered by guessing.
- C. The full command, redacted: SQL and `-c` bodies carry names.

*Reverse:* change `op_shape` in `scripts/staging.sh`.

### Q-K136 — What does each hospital's own audit trail get? (P1-T27; auto-accepted by Claude, 2026-11-22; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** One `operator` entry per hospital in scope at the start of a data session (and of a job that opens hospital schemas: release, restore drill, dumps). `all` writes into every live hospital; a held or absent schema is skipped and the line says so. For `all` the entry carries a fixed reason (`operator access (all hospitals), session <id>`); the full reason stays in the log store. The hospital's audit page shows the new kind later, with the DPA work (P4-T6). No guard that refuses opening another hospital inside a shell (it is bypassable by raw SQL).
- B. A plus the audit-page view and Hindi strings now.
- C. Entries only for schemas actually opened: misses raw SQL and dbshell.

*Limit:* `session_user` is not yet in the trigger context, so a shell can still put its writes on another actor; the journal line is the primary evidence. *Reverse:* migration 0007 adds the kind only; drop the hook in `ihr_audit/operator.py`.

### Q-K137 — How does Postgres log privileged logins? (P1-T27; auto-accepted by Claude, 2026-11-22; **confirmed by owner, 2026-11-23**)

PG 18's `log_connections` cannot be set per role.
- **A ⭐ (taken).** `log_connections=authorization` and `log_disconnections=on` server-wide, with `log_min_messages=warning` and `log_destination=stderr` pinned on the command line so one superuser session cannot switch logging off; a minute check that `postgresql.auto.conf` holds nothing (critical alert `PostgresSettingOverridden`). Every line kept; the local proof measures the volume against the logs budget.
- B. Drop the app's own service lines in Fluent Bit: hides a deliberately renamed shell.
- C. A login event trigger: a superuser can switch it off.

*Reverse:* `deploy/postgres/server-settings.txt` and the db command.

### Q-K138 — What if the record cannot be written? (P1-T27; auto-accepted by Claude, 2026-11-22; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** Refuse the command. Break-glass for emergencies only: `IHR_BREAK_GLASS="<reason>"` lets `rollback`, `restore` or `hospital-restore` run; the line goes to stderr and a pending file, shipped as `event=late` by the next recorded session. Data access commands never break glass. Local proofs and CI write the lines to stderr (`IHR_OPERATOR_LOCAL=stderr`).
- B. Warn and run: the record would be optional.

### Q-K139 — How much host auditing (auditd)? (P1-T27; auto-accepted by Claude, 2026-11-22; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** `execve` of docker, ctr, runc, nsenter and psql by logged-in users (b64 and b32), SYSCALL and EXECVE records only with arguments shaped like the operator line; no immutable `-e 2` (tampering is alerted: a minute metric counts the loaded rules); no rule for every root command. Live proof needs sudo on a real host, so it waits for P1-T26.
- B. Also every root command by a logged-in user (more volume, more redaction).
- C. Immutable rules (`-e 2`): bootstrap could no longer be re-run without a reboot.

### Q-K140 — How strict is the ops freeze? (P1-T30; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** `staging.sh` subcommands only: a names-only list (`deploy/staging/subcommands.txt`) that a test keeps equal to the dispatcher; a new name needs your OK, until P1-T26 (PR1-1) is ✅.
- B. Also pin the compose overlays.
- C. A rule in the README only, no test.

### Q-K141 — What does `staging.sh due` show? (P1-T30; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** Chores, drills and key ages that already have an alert: OVERDUE means that alert is firing now, with the gauge it reads as context and the command to run; no session, no second bookkeeping.
- B. Computed due dates from each chore's cadence (needs "last done" records that do not exist yet).

### Q-K142 — Alert tiers (P1-T30; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** Critical pages at once (same mailbox, subject `[IndieHR PAGE]`); `notify=anytime` and info stay immediate (your Q-O9 and Q-K36 answers kept); warnings come as one digest mail at 09:00 and 17:00 IST. A warning can wait up to about 16 h; one that fires and clears between windows is never mailed.
- B. One digest a day (09:00 only).
- C. Keep today's per-alert warning mails 08:00-22:00.

### Q-K143 — Should event and security warnings skip the digest? (P1-T30 review R1; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- Under Q-K142 A a warning is mailed only if it is still firing at 09:00 or 17:00 IST. The log alerts last only their `_time` window (10 minutes to 1 hour), so one-off events can fire and clear unmailed: AuditEventRecorded and AuditWholeRestoreRecorded (the add-only writer key used by someone else), OperatorSessionRefused, BatchRequestRefused, MrdRecordsRefused, StaffResetRefused, DatabaseStatementTimeouts. A 1-hour alert is inside a window about 12.5 % of the day.
- A. Add `notify: anytime` to the event and security warning rules only (audit, operator, batch, records, staff, root, dumps failures), and a Severity test that every warning log alert with a window under 16 h carries it. In practice that is every log alert (all windows are 10 minutes to 1 hour), so it equals C with a longer list to keep.
- B. Keep as built: the digest only, and read the inbox plus `staging.sh alerts` for one-off events.
- **C ⭐ (taken).** Every log alert (deploy/logs/alerts) carries `notify: anytime` and is mailed at once; the digest keeps the metric warnings, which are states that stay firing until a window. A test fails on a log alert without it. Reverse by removing the label from a rule.

### Q-K144 — Where does the hospital switcher's list come from? (P1-T31; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** A public directory table kept by per-hospital `SECURITY DEFINER` triggers; the first fill is the `ihr_identity` 0004 migration, repair is `ihr_staff_directory --check|--repair`. The raw `keycloak_sub` is kept for now (no new exposure under the shared `ihr_app`); a hospital-bound read or keyed hash is decided in P4-T8.
- B. Django signals plus a reconcile. C. Scan every hospital schema per request. D. Ask Keycloak. E. A token claim.
- *Why:* `queryset.update(is_active=…)` in `views.py:269`, `join.py:217` and `invitations.py:68` bypasses signals, and the app gets no write grant. *Reverse:* a later migration drops the table and triggers; the menu then lists one hospital.

### Q-K145 — Which links are listed? (P1-T31; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** Only active, non-deleted, non-superuser, non-service-account links. Superusers are always left out.
- B. Also list invited or pending links as "Invitation waiting".
- *Why:* an invitation is accepted by the e-mailed link, not in the menu. *Reverse:* change the trigger's predicate and run `--repair`.

### Q-K146 — Which hospitals appear, and how does a held one look? (P1-T31; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** ACTIVE hospitals with an org id and a primary domain, never `public`. A held one (release or restore hold, gated schema) is shown disabled; the group shows from two entries.
- B. Hide every non-servable hospital. C. Also list suspended ones as disabled.
- *Why:* a release window is short and hiding it looks like lost access; a suspended hospital refuses sign-in anyway. *Reverse:* the status filter in the endpoint query.

### Q-K147 — What does the endpoint reveal, and to whom? (P1-T31; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** `GET /api/ihr/my-hospitals/`, staff cookie only (service tokens and users with no identity get 403), `NotCrossSite` first. It returns only name, url, current and available for the caller's own sub. Query parameters are ignored; no cap; no audit entry.
- B. An audit entry per read.
- *Why:* no patient data and no change, so it is outside ADR-010's `request` rule. *Reverse:* add the entry to the view.

### Q-K148 — Which name does the switcher show? (P1-T31; follows from an existing pattern; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** `Hospital.name` from `public`.
- B. Mirror the profile's display name into `public` with another trigger (later, if owners rename hospitals).
- *Why:* reading another schema's profile at request time breaks at P4-T8. *Reverse:* add the mirror trigger.

### Q-K149 — Is the switcher's answer cached? (P1-T31; follows from an existing pattern; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** No server cache; a constant query count (`servable_many`); the web app caches with a user-scoped TanStack key and no `meta.persist`.
- B. Redis cache with a 60 s TTL (a deactivated person's hospitals would stay visible).
- *Why:* one cheap query; nothing to invalidate. *Reverse:* add a tenant-prefixed key.

### Q-K150 — Which environment names exist? (P1-T32; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** `production-<n>` (1–999), `staging`, `proof` on servers; dev and tests leave it unset (reported `dev`).
- B. Free-form names with a `production-` prefix rule. C. `dev` as a server value too.
- *Why:* a closed list makes a typo a refusal. *Reverse:* widen the regex in `environment.py` (one canonical copy, equality test).

### Q-K151 — How does `init` learn the environment? (P1-T32; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** `--local` and `--vm` write `proof`; otherwise `--environment production-<n>|staging` is required; production folders get `ihr-prod<n>-*` bucket names.
- B. Default `staging` when not given.
- *Why:* a silent default is the bug ADR-020 found. *Reverse:* make `--environment` optional.

### Q-K152 — Folders made before E1 (P1-T32; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** The upgrade runs in `compose()` on every command: `.local` folders become `proof` with a printed line; the VM cache upgrades by exact path and suffix; any other folder is refused with the fix.
- B. Write `staging` into any folder lacking it.
- *Why:* B could label a real server synthetic; A touches only throwaways today. *Reverse:* remove `ensure_environment`.

### Q-K153 — How far does E2 reach? (P1-T32; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** One dispatch table of stand-in commands, plus the state check on every Compose start verb, `init --check`, `release` and `deploy.sh`; parsing fails closed (only exact `staging` or `proof` pass).
- B. Commands only.
- *Why:* a copied or hand-edited `.env` bypasses B. *Reverse:* drop the state check.

### Q-K154 — What does E3 check before a deploy? (P1-T32; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **C ⭐ (taken; new).** A plus host identity: `bootstrap.sh --instance` writes a root-owned `INSTANCE`; missing or different refuses before anything is sent; no "new server ok"; no override.
- A. The §5.2 table alone. B. A plus `--replace-instance`.
- *Why:* without it, production's secrets could go to a fresh synthetic VM. *Reverse:* relax in `instance_check.py` (owner approval).

### Q-K155 — Dumps and backups older than P1-T32 (P1-T32; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** Accepted, with "environment not recorded" in the report; key and chain checks still apply. Backups drop out of P1-T32 (backup labels cut).
- B. Refused.
- *Why:* they exist only in proofs. *Reverse:* require the fields in `valid_manifest`.

### Q-K156 — Another instance's objects in this instance's dump bucket (P1-T32; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **C ⭐ (taken; new).** Marker-based: an authentic `instance.json` (written by `dumps-init`, sealed with the dump key) decides; a missing or foreign marker refuses `run`; a stray foreign object is a `check` problem and an alert (`reason=foreign-object`), and dumps continue.
- A. Refuse every dump on any foreign manifest. B. Report only.
- *Why:* A lets one stray object halt all nightly dumps. *Reverse:* drop the marker check.

### Q-K157 — Where does the synthetic banner show? (P1-T32; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **A ⭐ (taken).** Every web-app page and the operator console; no stub banner (the overlay covers it); no `__IHR_ENVIRONMENT__` global; the public JSON keeps `synthetic: true|false`, never the instance name; not Keycloak or ops hosts.
- B. A plus a Keycloak theme. C. The web app only.
- *Why:* it covers every page where a patient can be typed. *Reverse:* add a theme later.

### Q-K158 — Dev, CI and layout of the banner (P1-T32; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**)

- **C ⭐ (taken; new).** Dev and CI show the banner; no shared top-bar host (its own bar plus a CSS-variable offset for the IST bar); fallback B (servers only) after a 0.5 h layout cap.
- A. One shared top-bar host. B. Servers only.
- *Why:* the IST bar is shipped and owner-confirmed; a refactor broke it on production. *Reverse:* drop the script for dev.

### Q-K159 — Restore across suffixes (P1-T32; auto-accepted by Claude, 2026-11-23; **confirmed by owner, 2026-11-23**; new)

- **A ⭐ (taken).** The environment must match; a different suffix warns and is recorded in the restore report (disaster recovery to a new domain).
- B. Refuse any suffix difference.
- *Why:* a rebuilt production-1 on a new domain must restore its own dumps. *Reverse:* make the suffix strict in `Restore.precheck`.

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
