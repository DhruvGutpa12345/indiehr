# Open-source clinical core (EHR/HMS) candidates for a multi-tenant Indian hospital SaaS (state as of 2 Nov 2026)

Method note: release/activity data was pulled directly from GitHub on 2026-11-02 (GitHub REST API plus blobless `git clone` of the repos, reading tags, commit logs, and files at HEAD). "Commits since" counts are on each repo's default branch. GitHub URLs below are the primary sources for those facts. The vendor pages and docs fetched were ohc.network, docs.ozone-his.com, bahmni.atlassian.net and discuss.frappe.io/GitHub for Marley.

## Q1. Latest stable versions, release dates, and maintenance activity (2026)

### Takeaway
All four are active in 2026, but they differ a lot. CARE has the most commit activity, but its formal GitHub releases stopped in Dec 2025. OpenMRS O3 ships on a regular cadence (RefApp 3.8.0, 20 Oct 2026). Ozone's Docker Compose distro reached 1.0.0 on 28 Oct 2026. Bahmni shipped Lite 2.0.0 in Sep 2026. Marley Health ships frequent tagged releases (v16.7.0 and v15.2.5, both 1 Nov 2026).

### Cited Findings
**CARE (ohcnetwork)**
- The backend `ohcnetwork/care` is MIT. Its default branch is `develop` and it was last pushed 2026-11-02 (418 stars, 630 forks). The latest GitHub *release* is **v3.0.0 "Care Re-architecture V3.0", 2025-02-12**. Earlier weekly tags used year.week names (v24.50.0 … v25.1.0, Dec 2024), and `pyproject.toml` at HEAD says `version = "3.0"`. — [GitHub care releases](https://github.com/ohcnetwork/care/releases), [repo](https://github.com/ohcnetwork/care)
- Backend activity on `develop`: 502 commits / 28 author emails since 2025-11-01, but only 34 commits / 15 authors since 2026-08-01, so the pace slowed in the latest quarter. The last commits are "[ENG-193] Allow 50-character facility slugs" (2026-11-01), "Add 0% GST tax code and component to default tax config" (2026-10-19), and "[ENG-1054] Cleanups for Delivery Order, Dispense orders" (2026-10-18). — [care commits](https://github.com/ohcnetwork/care/commits/develop)
- The frontend `ohcnetwork/care_fe` is MIT (668 stars, 1,171 forks). Its latest GitHub release is **v3.0.32 "Production release", 2025-12-23**, and none has been published since, although `package.json` at HEAD reads 3.0.5 (inconsistent). Activity: 1,077 commits / 61 authors since 2025-11-01, and 92 commits / 19 authors since 2026-08-01. The last commit is 2026-11-02. — [care_fe releases](https://github.com/ohcnetwork/care_fe/releases)
- The org is very active across plugin repos. Repos pushed in Oct–Nov 2026 include care_odoo_be, odoo_connector, care_clinic, care_dental_fe, care_eka_scribe_fe, care_token_display(_fe), care_multihost_files_be, care_mcp, care_zoom_be/fe, care_people_fe, care_onboarding_fe, care_io_balance_fe, care_pressure_sore_fe, care_im_wrapper, care_experience_be, care_analytics_sql and open-healthcare-terminology-server. Most are MIT, and some have no licence file. — [github.com/ohcnetwork](https://github.com/ohcnetwork)

**OpenMRS / O3**
- openmrs-core **2.8.10, released 2026-10-06**. The other recent releases are 2.8.0 (2025-08-15) and 2.7.6 (2025-09-12). The core was last pushed 2026-10-31. — [openmrs-core releases](https://github.com/openmrs/openmrs-core/releases)
- The O3 frontend framework `openmrs-esm-core` released **v11.0.0 on 2026-10-06**. Before that came v10.0.0 (2026-06-09) and v9.0.2 (2026-03-04). — [esm-core releases](https://github.com/openmrs/openmrs-esm-core/releases)
- The O3 Reference Application distro tagged **3.8.0 on 2026-10-20**, after rc.1 on 2026-10-08. The previous release was 3.7.1. — [openmrs-distro-referenceapplication tags](https://github.com/openmrs/openmrs-distro-referenceapplication/tags)

**Ozone HIS (Mekom)**
- `ozone-his/ozone-docker-compose` (MPL-2.0) released **1.0.0 on 2026-10-28**, its first non-alpha. Before that came 1.0.0-alpha.15 (2025-12-10) and alpha.13 (2025-03-06). — [ozone-docker-compose releases](https://github.com/ozone-his/ozone-docker-compose/releases)
- The distro repo `ozone-his/ozone` (MPL-2.0) is still at **1.0.0-alpha.15 (2026-01-01/02)**. It has had 61 commits / 8 authors since 2025-11-01 and 9 commits / 2 authors since 2026-08-01. Recent commits include "OZ-1053: Upgrade Bahmni version" and "OZ-1113: Add SNOMED mappings … in Odoo" (both Oct 2026). — [ozone releases](https://github.com/ozone-his/ozone/releases)
- The docs (still describing alpha.15) list these components: OpenMRS 3.5.0 and Bahmni 1.1.0 (EMR), SENAITE 2.5.0 (LIMS), **Odoo 17 (Core)**, **ERPNext 15.12.2 ("Legacy")**, Superset 4.0.1, Keycloak 22.0.5 and Orthanc 1.12.6. — [Ozone Docs overview](https://docs.ozone-his.com/users/)

**Bahmni**
- `Bahmni/bahmni-docker` tags: **2.0.0-lite and 1.2.0-lite (both 2026-09-24)**, and **1.0.2-standard / 1.0.2-lite (2026-07-02)**. Activity: 20 commits / 11 authors since 2025-11-01 and 5 commits since 2026-08-01, mostly CI and security-scan work. — [bahmni-docker](https://github.com/Bahmni/bahmni-docker)
- `openmrs-module-bahmniapps` released **2.0.0 on 2026-09-15**. `bahmni-core` released **1.3.0 on 2025-10-10**. — [bahmniapps releases](https://github.com/Bahmni/openmrs-module-bahmniapps/releases), [bahmni-core releases](https://github.com/Bahmni/bahmni-core/releases)

**Marley Health / Frappe Health (earthians/marley; `frappe/health` now redirects there)**
- **v16.7.0 and v15.2.5, both released 2026-11-01**. v16.6.1 came out 2026-09-22. The repo is GPL-3.0 (555 stars, 476 forks). Activity: 538 commits / 19 authors since 2025-11-01 and 252 commits / 11 authors since 2026-08-01. — [marley releases](https://github.com/earthians/marley/releases), [v16.7.0](https://github.com/earthians/marley/releases/tag/v16.7.0)

### Inferences
- In raw contributor count, CARE has the largest active developer base of the four (61 FE authors in 12 months). Its release engineering is poor, though: there are no tagged releases since Dec 2025. A SaaS fork would have to pin to commit SHAs and run its own release process.
- Ozone has only just reached 1.0.0 and has a small team (2–8 committers). It is maturity-risky as a foundation.

### Gaps
- I could not confirm whether CARE publishes versioned releases anywhere other than GitHub (container registry tags, for example), or why releases stopped after v3.0.32.
- No exact OpenMRS O3 RefApp 3.8.0 release-notes page was fetched, so the bundled core version is unconfirmed.

## Q2. CARE: FHIR re-architecture, modules, plugin architecture, multi-org model, deployment, ABDM, licence

### Takeaway
CARE v3 was rebuilt around FHIR-aligned Django models covering the full clinical, diagnostic, pharmacy, inventory and billing set. It has a real two-sided plugin system: Django-app "plugs" on the backend and Module Federation micro-frontends on the frontend. Its Organization/FacilityOrganization model is a governance and permission hierarchy, not hard tenant isolation. The core is MIT, but the production ABDM, Scribe, HCX and TeleICU plugins are no longer publicly visible on GitHub.

### Cited Findings
- **FHIR-aligned models exist at HEAD** in `care/emr/models/`: Account, ActivityDefinition, AllergyIntolerance, ChargeItem, ChargeItemDefinition, Condition, Consent, Device, DiagnosticReport, Encounter, HealthcareService, InventoryItem, Invoice, Location, MedicationAdministration, MedicationDispense, MedicationRequest, MedicationStatement, Observation, ObservationDefinition, Organization, Patient, PaymentReconciliation, Product, ProductKnowledge, Questionnaire, ServiceRequest, Specimen, SpecimenDefinition, SupplyDelivery, SupplyRequest, ValueSet, scheduling (schedule, booking, token), report templates, and more. — [care/emr/models](https://github.com/ohcnetwork/care/tree/develop/care/emr/models)
- The API resources mirror these and add facility_organization, questionnaire_response, form_submission, role, mfa and patient_identifier. — [care/emr/resources](https://github.com/ohcnetwork/care/tree/develop/care/emr/resources)
- India billing is live in core: a commit dated 2026-10-19 adds a "0% GST tax code and component to default tax config". Delivery-order and dispense-order cleanups landed 2026-10-18. — [care commits](https://github.com/ohcnetwork/care/commits/develop)
- The vendor page lists CARE Core modules as **Administration, Definitions, Clinical, Scheduling, Pharmacy, Labs, Billing**. Listed solutions are "Hospital OS, Critical care, Home care grid, Clinic network, Public health". — [ohc.network apps & integrations](https://ohc.network/product/apps-integrations)
- **Backend plugin architecture:** "Plugins are basically django apps that are defined in the `plug_config.py`." Each one is a `Plug(name, package_name="git+https://…", version="@v1.0.0", configs={...})`. Config precedence is plug_config, then environment variables, then plugin defaults. URLs mount under `/api/<plugin-name>/`. Plugins are recommended to be baked in at Docker build time, and a care-plugin-cookiecutter exists. — [docs/development/pluggable-apps.md](https://github.com/ohcnetwork/care/blob/develop/docs/development/pluggable-apps.md)
- **Frontend plugin architecture:** care_fe uses `@originjs/vite-plugin-federation` (Module Federation micro-frontends) with React ^19.2.6 and TanStack Query v5. Remote apps are enabled via `REACT_ENABLED_APPS` as `org/repo@url`. An app store index is read from `ohcnetwork/care_apps_registry`. `care.config.ts` also maps API URLs per browser origin ("mapped URL for current origin > REACT_CARE_API_URL fallback"), and a `plugs-versioning.md` file exists. — [care_fe package.json / care.config.ts](https://github.com/ohcnetwork/care_fe)
- **Org/permission model:** `OrganizationCommonBase` has an `org_type`. `FacilityOrganization` is a FK to Facility. `Organization` and `OrganizationUser` handle roles, and `FacilityOrganizationUser` is the "same as OrganizationContextRole but for facility scoped organizations". — [care/emr/models/organization.py](https://github.com/ohcnetwork/care/blob/develop/care/emr/models/organization.py)
- **Deployment:** Python ==3.13, Django ==6.0. The compose file has PostgreSQL 17 (postgres:17-alpine), Redis 8 and an S3-compatible object store (MinIO-compatible image). Compose variants exist for local, pre-built and Coolify, plus a Nix flake. The frontend ships as a Vite build behind nginx. — [care repo](https://github.com/ohcnetwork/care), [Pipfile](https://github.com/ohcnetwork/care/blob/develop/Pipfile)
- **Licence:** both care and care_fe are MIT (GitHub licence API). — [care](https://github.com/ohcnetwork/care), [care_fe](https://github.com/ohcnetwork/care_fe)
- **Plugin repo visibility (important):** on 2026-11-02, `ohcnetwork/care_abdm`, `care_scribe` and `care_hcx` returned GitHub API 404. `git ls-remote` for care_abdm, care_abdm_fe, care_scribe(_fe), care_hcx(_fe), care_camera_asset and care_teleicu_devices demanded authentication, which means they are private or deleted. A renamed repo would redirect instead. — [GitHub ohcnetwork org](https://github.com/ohcnetwork)
- The public `ohcnetwork/care-abdm` repo is **not** the production plugin. Its own docs call it "care-abdm-sbx", "An experiment": an AI agent integrating ABDM into CARE using only eka.care's unofficial ABDM HIECM v3 docs. They explicitly reference the existing `care_abdm` / `care_abdm_fe` as separate, forbidden codebases. It has 38 commits by one author, starting 2026-09-11 ("abdm m1"). — [care-abdm AGENTS.md / docs/00-context.md](https://github.com/ohcnetwork/care-abdm)
- **ABDM claims by the vendor:** the apps page describes ABDM Integration as supporting "M1, M2, M3 style workflows" through a plugin model. It makes no certification claim on the page fetched. One search summary of the same page reported "deployed across 6 states" for ABDM and "9 states" for TeleICU, but my direct fetch found no counts, so treat those numbers as unverified. — [ohc.network apps & integrations](https://ohc.network/product/apps-integrations)
- **Scale evidence:** an OHC LinkedIn post, roughly 623 days old (about early 2025), says CARE is deployed in 220 hospitals with 14 TeleICU hubs in 11 states. It also says Karnataka joined Jharkhand, Assam and Manipur as the fourth state piloting a CARE-based HMIS. — [LinkedIn (Vignesh Hari, OHC)](https://www.linkedin.com/in/vigneshhari/) (secondary, dated)

### Inferences
- CARE is the only candidate whose *data model* is natively FHIR-shaped end to end, including ChargeItem/Account/Invoice billing, inventory/supply and MedicationDispense. That reduces ABDM FHIR-bundle mapping work.
- **Multi-tenancy:** Organization/FacilityOrganization gives hierarchical, row-scoped access in a single Postgres DB. It is designed for government networks (state, district, facility), not for mutually distrusting commercial tenants. The per-origin API URL map in care_fe suggests deployers run one backend per host/tenant. For SaaS, the safer pattern is database-per-tenant or schema-per-tenant, or at minimum Postgres RLS added on top. Facility-as-tenant on shared tables would need a security audit.
- MIT allows closed proprietary extensions and SaaS without source disclosure. The plugin (plug + MFE) model lets proprietary modules sit outside the core repo.
- Because the production ABDM, Scribe, HCX and TeleICU plugins are no longer public, a commercial adopter should plan to build or licence its own ABDM M1–M3 integration, or negotiate access with OHC. "Native ABDM" is not something you get from the public MIT code today.

### Gaps
- No primary source found for CARE's NHA/ABDM certification status (no NHA certified-integrator list entry retrieved).
- No current (2026) deployment count from a primary source.
- I could not read docs.ohc.network for official deployment sizing or multi-tenant guidance in the tool budget.

## Q3. OpenMRS O3 / Ozone: multi-tenancy, deployment weight, Pro vs FOSS, India usage, ABDM

### Takeaway
OpenMRS has no built-in multi-tenancy, so a SaaS would run one instance per tenant. Ozone bundles 6+ heavyweight apps (OpenMRS, Bahmni, SENAITE, Odoo 17, Superset, Keycloak, Orthanc), each with its own DB, which makes per-tenant SaaS expensive. Ozone FOSS is MPL-2.0, and closed components go into "Ozone Pro".

### Cited Findings
- The OpenMRS wiki "Multitenancy & OpenMRS@Scale" is a planning page. It proposes central monitoring, updates, identity and backup across *many deployments*, on the hypothesis that this covers most implementer reasons for multitenancy. It does not describe a multi-tenant single instance. — [OpenMRS wiki: Multitenancy & OpenMRS@Scale](https://openmrs.atlassian.net/wiki/spaces/projects/pages/82313228/Multitenancy+OpenMRS+Scale)
- The openmrs-core licence file is **Mozilla Public License 2.0** (GitHub flags it "Other" because of the added Healthcare Disclaimer). — [openmrs-core LICENSE](https://github.com/openmrs/openmrs-core/blob/master/LICENSE)
- Ozone: "free and open-source under the MPL 2.0 licence". "Closed-source software is accommodated within the Pro edition of Ozone or through custom developments". Pro-only features carry a Pro marker. The platform is "powered by an interoperability EIP framework", and Docker is the default deployment mechanism. — [Ozone Docs overview](https://docs.ozone-his.com/users/)
- Ozone components: OpenMRS 3.5.0 + Bahmni 1.1.0, SENAITE 2.5.0, **Odoo 17 = Core ERP; ERPNext 15.12.2 = Legacy ("no longer actively maintained beyond that version")**, Superset, Keycloak, Orthanc. — [Ozone Docs overview](https://docs.ozone-his.com/users/)

### Inferences
- Deployment weight: Ozone needs at least 7 application services, each with its own database (MySQL/MariaDB for OpenMRS, PostgreSQL for Odoo/Superset/Keycloak, ZODB for SENAITE), plus an EIP service. Per-tenant cost is high.
- O3 is MPL-2.0 (file-level copyleft). Proprietary modules and ESM micro-frontends in separate files can stay closed, but modifications to MPL files must be shared if distributed. SaaS use does not by itself trigger distribution.
- OpenMRS has no native ABDM module in core that I could verify. ABDM in the OpenMRS ecosystem in India comes through Bahmni's ABDM work.

### Gaps
- Exact container count of the Ozone 1.0.0 compose file could not be read: the compose path in the repo differs from the one I tried.
- Ozone Pro licence terms and price were not found publicly.
- I could not find a primary source for Apache Camel usage. Ozone docs say "EIP framework" without naming Camel (from background knowledge, Ozone's EIP is Camel-based, but this is uncited).
- No primary source on O3/Ozone India deployments or an ABDM module for O3.

## Q4. Bahmni: ABDM status, India deployments, architecture weight, Odoo coupling, licences

### Takeaway
Bahmni completed ABDM HIP/HIU (M1–M3) integration work circa 2022–2023 with Thoughtworks. It is very heavy: Bahmni Standard runs about 30 services, including two Odoo versions, OpenELIS and a PACS stack. Bahmni Lite swaps Odoo for Crater billing. The current core is MPL-2.0, not AGPL.

### Cited Findings
- Bahmni's wiki page "Bahmni as Health Information Provider (ABDM/NDHM)" is listed as completed, with Thoughtworks as collaborator. It covers M1 (ABHA creation/verification), M2 (HIP) and M3 (HIU), with stated outcomes of NHA certification as HIP and HIU. Sub-pages include "Bahmni LITE ABDM E2E Tests" and "FAQs on ABDM and Bahmni compliance". — [Bahmni wiki ABDM HIP](https://bahmni.atlassian.net/wiki/spaces/BAH/pages/2901114904/Bahmni+as+Health+Information+Provider) (search snippet; direct fetch returned only navigation)
- A 2026 vendor comparison characterises Bahmni's ABDM as "Community-supported ABDM connectors": the adopter owns integration, certification and maintenance. — [VCDoctor blog](https://www.vcdoctor.com/blog/abdm-compliant-hims-software) (secondary)
- **Bahmni Standard** docker-compose services: proxy, bahmni-config, openelis + openelisdb, **odoo + odoodb + odoo-connect, odoo-10 + odoo-10-db + odoo-10-connect**, openmrs + openmrsdb, metabase + metabasedb, mart + martdb, bahmni-web, bahmni-apps-frontend, template-service, implementer-interface, reports + reportsdb, patient-documents, appointments, dcm4chee + pacs-integration + pacsdb + pacs-simulator, atomfeed-console, sms-service, ipd, cdss, snowstorm-lite. — [bahmni-docker/bahmni-standard/docker-compose.yml](https://github.com/Bahmni/bahmni-docker)
- **Bahmni Lite** services: openmrs, bahmni-lab, **crater-php/crater-nginx/craterdb + crater-atomfeed** (Crater invoicing instead of Odoo), metabase, mart, reports, appointments, cdss, snowstorm-lite, and others. — [bahmni-docker/bahmni-lite](https://github.com/Bahmni/bahmni-docker)
- Licences: `bahmni-core` LICENSE = **Mozilla Public License 2.0**. The `bahmni-docker` LICENSE is an MIT text headed "Copyright (c) 2024 OpenMRS Inc." — [bahmni-core LICENSE](https://github.com/Bahmni/bahmni-core/blob/master/LICENSE), [bahmni-docker LICENSE](https://github.com/Bahmni/bahmni-docker/blob/master/LICENSE)

### Inferences
- Odoo coupling is optional in practice: Lite uses Crater, and Standard still carries a legacy Odoo 10 alongside a newer Odoo. Sync relies on AtomFeed connectors, an older event-feed pattern.
- Bahmni is a single-tenant hospital deployment by design, the same as OpenMRS. Running about 30 containers per tenant makes it unsuitable as a SaaS core.

### Gaps
- No primary 2026 source on whether Bahmni's ABDM integration has been updated to ABDM v3 APIs (HIECM v3), or on its current NHA certification validity.
- No current India deployment count was found. Bahmni is historically used by JSS Chhattisgarh and others, but I had no source for that in this session.
- I did not verify the date Bahmni moved from AGPL to MPL.

## Q5. Marley Health / Frappe Health

### Takeaway
Marley Health is actively maintained (v16.7.0, 1 Nov 2026) for Frappe/ERPNext v16, with v15 still patched and `develop` already on Frappe v17-dev. ABDM doctypes are in the core app. It is GPL-3.0 and has a recently disclosed systemic API permission weakness.

### Cited Findings
- The `version-16` branch requires `frappe >=16.0.0,<17.0.0` and `erpnext >=16.0.0,<17.0.0`. `develop` requires `frappe >=17.0.0-dev,<18.0.0`, and branches version-14, 15 and 16 exist. — [marley pyproject.toml](https://github.com/earthians/marley/blob/develop/pyproject.toml)
- v16 highlights: Healthcare Insurance Management, Patient Portal, Patient Referral Management and Practitioner Availability. The prerequisites are Frappe, ERPNext and Healthcare v16. — [Frappe Forum: Marley Healthcare v16 release](https://discuss.frappe.io/t/marley-healthcare-version-16-release/159139)
- v16.7.0 (2026-11-01) includes security fixes that restrict portal booking to a user's own patients and gate ABDM gateway calls. — [marley v16.7.0](https://github.com/earthians/marley/releases/tag/v16.7.0)
- ABDM lives in the core app as doctypes `ABDM Request` and `ABDM Settings`. There is a branch `abdm-milestone-1-wip`, and an `interop` branch exists. — [marley repo tree](https://github.com/earthians/marley)
- Security audit issue #943: only 3 of 147 `@frappe.whitelist()` functions enforced permission checks. The issue says the problem is still present on version-16 (v16.6.1). Fix PRs #1274 and #1278 followed. — [issue #943](https://github.com/earthians/marley/issues/943), [PR #1274](https://github.com/earthians/marley/pull/1274)
- There is a separate `marley_frontend` repo, a frontend UI app. — [marley_frontend](https://github.com/earthians/marley_frontend)
- Licence: GPL-3.0. — [earthians/marley](https://github.com/earthians/marley)

### Inferences
- GPLv3 does not require source disclosure for SaaS use (no AGPL network clause), so proprietary Frappe apps can stay closed when only hosted. Distributing to on-prem customers would trigger GPL. Frappe framework is MIT and ERPNext is GPLv3 (background knowledge; not re-verified this session).
- Frappe's bench supports multi-site with a separate MariaDB database per site. That gives the strongest *built-in* tenant isolation of the four, but the clinical depth (ICU, labs and specimen workflow, FHIR-native model) is shallower than CARE's. The ABDM work appears to be in progress (M1 WIP branch).

### Gaps
- No primary source on Marley's ABDM milestone/certification status or India scale.
- The Frappe multi-site details above are background knowledge, not fetched.

## Q6. Licence implications, multi-tenancy suitability, stack, verdict, and blueprint corrections

### Takeaway
**CARE is the best foundation** for an India-focused commercial SaaS:
- It is MIT, so closed extensions are allowed.
- Its data model is natively FHIR-aligned, which suits ABDM.
- It already covers OPD, IPD, ICU, labs, pharmacy, inventory, GST billing and scheduling.
- It has a modern stack (Django 6 / Postgres 17 / React 19 MFEs) and the largest active India-based community.

It has caveats. You must add your own tenant isolation (DB- or schema-per-tenant, or RLS), own your release process, and build or obtain ABDM, Scribe and HCX plugins, which are no longer public. Marley is the runner-up for small clinics wanting ERP-integrated billing. O3/Ozone and Bahmni are too heavy and single-tenant for SaaS economics.

### Cited Findings
- Licence and stack summary (from the sources above):
  - CARE: MIT, Python 3.13/Django 6.0/PostgreSQL 17/Redis/S3; React 19 + Vite Module Federation. [care](https://github.com/ohcnetwork/care)
  - OpenMRS: MPL-2.0 + Healthcare Disclaimer, Java/Spring/MySQL-MariaDB + O3 React ESM. [openmrs-core](https://github.com/openmrs/openmrs-core)
  - Ozone: MPL-2.0 FOSS + closed "Pro". [Ozone Docs](https://docs.ozone-his.com/users/)
  - Bahmni core: MPL-2.0. [bahmni-core](https://github.com/Bahmni/bahmni-core)
  - Marley: GPL-3.0 on Frappe/ERPNext v15/v16 (MariaDB). [marley](https://github.com/earthians/marley)

**Blueprint claim checks**
- "Ozone uses Apache Camel EIP to ERPNext": **outdated or misleading.** Ozone's core ERP is **Odoo 17**, and ERPNext 15.12.2 is marked "Legacy … no longer actively maintained". The docs say "EIP framework" without naming Camel. — [Ozone Docs](https://docs.ozone-his.com/users/)
- "Bahmni MPL 2.0 / AGPLv3": **partly outdated.** bahmni-core is MPL 2.0 today, and bahmni-docker is MIT. AGPL is historic or applies only to specific components, and I verified no AGPL file this session. — [bahmni-core LICENSE](https://github.com/Bahmni/bahmni-core/blob/master/LICENSE)
- "CARE MIT, native ABDM v3, deployed across 10+ states": **MIT is correct.** ABDM is not native core: it is a plugin, and the production `care_abdm` repo is not public as of 2026-11-02. The only public ABDM repo is an experimental sandbox started Sep 2026, and no certification claim was found. "10+ states" traces to an about-early-2025 figure of 11 states for TeleICU and 220 hospitals. — [care-abdm](https://github.com/ohcnetwork/care-abdm), [ohc.network apps](https://ohc.network/product/apps-integrations), [LinkedIn](https://www.linkedin.com/in/vigneshhari/)
- "Frappe Health GPLv3, community ABDM app": **GPLv3 is correct.** ABDM is now inside the main Marley app (ABDM Request/Settings doctypes, ABDM gateway gating in v16.7.0), not a separate community app. The name is now "Marley Health" (earthians/marley), and `frappe/health` redirects there. — [marley](https://github.com/earthians/marley), [v16.7.0](https://github.com/earthians/marley/releases/tag/v16.7.0)

### Inferences
**Multi-tenancy ranking for SaaS**
1. Marley/Frappe: native site-per-tenant DB isolation.
2. CARE: a single-DB org hierarchy, so isolation must be engineered; its lightweight stack (about 4 containers) makes DB-per-tenant affordable.
3. O3/Ozone and Bahmni: instance-per-tenant only, and heavy.

**Closed-extension freedom**
1. CARE (MIT): unrestricted.
2. Ozone/OpenMRS/Bahmni (MPL): separate files stay closed.
3. Marley (GPL): closed only while it stays SaaS and is never distributed.

**Clinical depth for tertiary care (ICU, labs, pharmacy, billing)**
1. CARE
2. Bahmni/Ozone
3. Marley

### Gaps
- No load or scale benchmarks found for any candidate.
- The NHA certified-software list was not retrieved, so ABDM certification for all four is unverified from primary sources.
