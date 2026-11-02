# Diagnostic Ancillary Systems (PACS and LIS) for an India Multi-Tenant Hospital SaaS (state as of 2 Nov 2026)

Method note: GitHub release web pages omit the year for recent dates, so every GitHub date below was re-checked against the repo's `releases.atom` feed, which gives full ISO dates. Orthanc dates come from the official NEWS file and the download directory listings on orthanc.uclouvain.be. Where a value was relayed by a summarising fetch tool and I could not cross-check it, I say so. Date format is YYYY-MM-DD.

## Orthanc core: latest version, date, licence, resource footprint

### Takeaway
Latest Orthanc core is 1.13.0, released 2026-08-15. 1.12.11 (2026-04-14) is the last 1.12.x. The core is GPLv3+, and the key scale-related plugins (PostgreSQL, DICOMweb, object-storage, authorization, Python, Stone) are AGPLv3. The official docs give no hard CPU/RAM sizing, only tuning guidance.

### Cited Findings
- Orthanc 1.13.0 is dated 2026-08-15. Changes: rewritten storage access and DICOM parsing/transcoding (better caching, lower memory), config split into main and advanced files, Orthanc refuses to start if authentication and remote access are enabled with no registered user, `OverwriteInstances` accepts Never/Always/IfChanged, Lua key-value store (PostgreSQL and SQLite only), REST API version 31, and security fixes including a nested-sequence DoS (CVE-2026-10528) — [Orthanc NEWS](https://orthanc.uclouvain.be/hg/orthanc/file/default/NEWS)
- Previous releases: 1.12.11 (2026-04-14, DCMTK 3.7.0, REST API v30, nine CVE fixes), 1.12.10 (2025-11-26), 1.12.9 (2025-08-11), 1.12.8 (2025-06-13) — [Orthanc NEWS](https://orthanc.uclouvain.be/hg/orthanc/file/default/NEWS)
- The downloads index confirms the highest source tarball is Orthanc-1.13.0 (2026-08-15) and highest 1.12 is 1.12.11 (2026-04-14) — [Orthanc source downloads](https://orthanc.uclouvain.be/downloads/sources/orthanc/index.html)
- Unreleased mainline (post-1.13.0) already has fixes for a transcoder cache memory under-estimate causing OOM crashes and a deadlock in anonymization/modification jobs, so a 1.13.1 is likely soon — [Orthanc NEWS](https://orthanc.uclouvain.be/hg/orthanc/file/default/NEWS)
- Core licence: GPLv3 (repo COPYING is "GNU GENERAL PUBLIC LICENSE Version 3") — [Orthanc COPYING](https://orthanc.uclouvain.be/hg/orthanc/raw-file/default/COPYING); the Orthanc Book says core was released under GPLv3+ in 2012 — [Orthanc licensing FAQ](https://orthanc.uclouvain.be/book/faq/licensing.html)
- The licensing FAQ says the PostgreSQL plugin and web viewer plugin went AGPLv3+ because they provide scalability/cloud-related features, and lists Osimis Web Viewer, Stone Web Viewer and the Stone sample apps as AGPL. It also says a plugin cannot use a permissive licence because it is combined with the GPL core — [Orthanc licensing FAQ](https://orthanc.uclouvain.be/book/faq/licensing.html)
- Scalability FAQ: production deployments at hospitals with >15 TB, ~125,000 studies and ~50 million instances (a newer revision says ~150 million instances). Much apparent RSS growth is glibc memory arenas (example: up to 50 threads x 50 MB = 2.5 GB); official Docker images set MALLOC_ARENA_MAX=5 since 1.6.1. Since Orthanc 1.9.2 and PostgreSQL/MySQL plugin 4.0 the DB engine supports multiple writers/readers sharing one DB. Network latency to the DB matters (20 ms latency can make `/studies/{id}` take about 120 ms) — [Scalability of Orthanc](https://orthanc.uclouvain.be/book/faq/scalability.html)
- Orthanc team Docker images (`orthancteam/orthanc`) ship the AWS S3 object-storage plugin by default and Azure/Google plugins only in `-full` images — [Object storage plugin page](https://orthanc.uclouvain.be/book/plugins/object-storage.html)

### Inferences
- A single multi-tenant deployment should pin 1.13.0 (or 1.12.11 as conservative fallback) and expect a quick 1.13.x patch release; 1.13.0 only shipped 2.5 months before this date and changed storage/parsing internals.
- AGPLv3 on the DB/auth/DICOMweb/viewer plugins matters for a commercial SaaS: AGPL section 13 obliges you to offer source to network users if you modify those plugins. Unmodified use with your own separate services (via REST) is the usual safe pattern, but get legal review before shipping modified plugins or linking proprietary code into them.
- Footprint: Orthanc is a single C++ binary and is light by itself (idle footprint is small); real memory cost is driven by concurrent threads and transcoding. Plan for roughly 1-4 GB RAM per busy instance, which is my estimate, not an official figure.

### Gaps
- No official RAM/CPU sizing table was found; the figures above are not sourced.
- The `orthanc.uclouvain.be/book/faq/changelog.html` page returned 404 and the Orthanc download pages for object-storage and stone-webviewer rendered empty listings via the fetch tool; versions for those two come from NEWS files instead.

## Orthanc plugins: versions and licences (PostgreSQL, DICOMweb, S3, authorization, OHIF, Python, Stone, Worklists)

### Takeaway
All plugins are current as of Aug-Oct 2026. Licences: PostgreSQL, DICOMweb, object-storage, authorization, Python and Stone are AGPLv3 (verified from COPYING files where noted). The OHIF plugin licence could not be confirmed. The sample (legacy) worklists plugin is GPLv3+.

### Cited Findings
| Plugin | Latest version / date | Licence | Source |
|---|---|---|---|
| PostgreSQL index/storage | 11.0 / 2026-09-19 (value from the download-directory listing as relayed by the fetch tool; unusually high jump from the 7.x/8.x series I know of, treat as needing a re-check) | AGPLv3 (COPYING) | [downloads](https://orthanc.uclouvain.be/downloads/sources/orthanc-postgresql/index.html), [COPYING](https://orthanc.uclouvain.be/hg/orthanc-postgresql/raw-file/default/COPYING) |
| DICOMweb | 1.24 / 2026-08-19 | AGPLv3 (COPYING) | [downloads](https://orthanc.uclouvain.be/downloads/sources/orthanc-dicomweb/index.html), [COPYING](https://orthanc.uclouvain.be/hg/orthanc-dicomweb/file/default/COPYING) |
| Object storage (AWS S3, Azure Blob, Google Cloud Storage) | 2.5.4 / 2026-07-22 | AGPLv3 (COPYING) | [NEWS](https://orthanc.uclouvain.be/hg/orthanc-object-storage/file/default/NEWS), [COPYING](https://orthanc.uclouvain.be/hg/orthanc-object-storage/raw-file/default/COPYING) |
| Authorization | 0.12.0 / 2026-08-28 (added inbox-links); 0.11.4 2026-07-23; 0.11.3 2026-04-23 | AGPLv3 (COPYING) | [downloads](https://orthanc.uclouvain.be/downloads/sources/orthanc-authorization/index.html), [NEWS](https://orthanc.uclouvain.be/hg/orthanc-authorization/file/default/NEWS), [COPYING](https://orthanc.uclouvain.be/hg/orthanc-authorization/file/default/COPYING) |
| OHIF plugin | 1.8 / 2026-03-17 (bundles OHIF 3.12.0; 1.7 bundled 3.11.0; 1.6 bundled 3.10.1) | Not confirmed (no COPYING in repo manifest; README silent) | [NEWS](https://orthanc.uclouvain.be/hg/orthanc-ohif/file/default/NEWS), [downloads](https://orthanc.uclouvain.be/downloads/sources/orthanc-ohif/index.html) |
| Python plugin | 7.1 / 2026-04-07 (7.0 on 2025-12-02) | AGPLv3 (COPYING) | [downloads](https://orthanc.uclouvain.be/downloads/sources/orthanc-python/index.html), [COPYING](https://orthanc.uclouvain.be/hg/orthanc-python/file/default/COPYING) |
| Stone Web Viewer | 3.0 / 2025-12-03 (adds DICOM-SR text reports, pinch/zoom, PDF print/download; 2.6 was 2024-08-31) | AGPLv3 (orthanc-stone COPYING) | [NEWS](https://orthanc.uclouvain.be/hg/orthanc-stone/file/default/Applications/StoneWebViewer/NEWS), [COPYING](https://orthanc.uclouvain.be/hg/orthanc-stone/raw-file/default/COPYING) |
| Worklists (legacy sample plugin) | 0.9.2 / 2025-12-08 | GPLv3+ (per Orthanc Book) | [downloads](https://orthanc.uclouvain.be/downloads/sources/orthanc-worklists/index.html), [book](https://orthanc.uclouvain.be/book/plugins/worklists-plugin.html) |

- Object-storage plugin features: hybrid mode (Disabled / WriteToFileSystem / WriteToObjectStorage), `/move-storage` API, optional client-side AES-256 encryption with key rotation, works with MinIO and Ceph (must set `VirtualAddressing=false` for MinIO) and other S3-compatible services via `Endpoint`; AWS 2.5.2 added object `Tags`, 2.5.4 added `MaxConnections` and `MaxRetries` — [Object storage plugin page](https://orthanc.uclouvain.be/book/plugins/object-storage.html), [NEWS](https://orthanc.uclouvain.be/hg/orthanc-object-storage/file/default/NEWS)
- PostgreSQL plugin repo carries a warning that further development has moved to the `orthanc-databases` repository — [orthanc-postgresql NEWS](https://orthanc.uclouvain.be/hg/orthanc-postgresql/raw-file/default/NEWS)
- Python plugin NEWS: SDK range min 1.7.2, max 1.13.0 (default); wraps `RegisterHttpAuthentication` — [orthanc-python NEWS](https://orthanc.uclouvain.be/hg/orthanc-python/raw-file/default/NEWS)
- Orthanc team Python tooling `python-orthanc-tools` has releases up to 0.23.0 (2026-09-29) — [GitHub releases feed](https://github.com/orthanc-team/python-orthanc-tools/releases)
- Note: the licensing FAQ lists only PostgreSQL and web viewers as AGPL by name; the other AGPL rows rest on each repo's COPYING file, fetched directly.

### Inferences
- GPLv3 vs AGPLv3 summary for the blueprint: core = GPLv3+; PostgreSQL, DICOMweb, object-storage, authorization, Python, Stone = AGPLv3; Worklists sample = GPLv3+. OHIF itself (viewer code) is MIT (see OHIF section), but the Orthanc OHIF plugin wrapper licence should be checked in the source tarball.
- The PostgreSQL "11.0" version number should be re-verified from the tarball name before it is written into a spec.

### Gaps
- OHIF plugin licence not confirmed (probably AGPLv3 by Orthanc team convention, unverified).
- Object-storage and Stone download-index pages returned empty tables; used NEWS instead. PostgreSQL plugin minimum Orthanc version not checked.

## Orthanc multi-tenancy: labels + authorization plugin, orthanc-auth-service, Keycloak, one instance per tenant

### Takeaway
Orthanc has no native tenant concept, but since 1.12.0 it has indexed "labels", and the authorization plugin can force a label filter per user (`authorized-labels`), which is the supported building block for shared-instance multi-tenancy. orthanc-auth-service (Orthanc Team, actively released through 26.10.2 on 2026-10-22) is the reference external service that issues share tokens and integrates with Keycloak.

### Cited Findings
- Labels were introduced in Orthanc 1.12.0; they are valueless tags on patients, studies, series and instances and are indexed in the DB for fast lookup — [Multitenant DICOM plugin page](https://orthanc.uclouvain.be/book/plugins/multitenant-dicom.html)
- Authorization plugin: the user profile from `/user/get-profile` can include `authorized-labels` (`*` = all); when labels are returned the plugin adds a label filter to `tools/find` and checks accessed resources carry an authorized label. Permissions are regex rules mapping HTTP method + URI to a permission. Two modes: user-based (profile permissions, used with Orthanc Explorer 2) and resource-based tokens (single-resource share links) validated at `/tokens/validate` — [Authorization plugin page](https://orthanc.uclouvain.be/book/plugins/authorization.html)
- Authorization plugin changelog: it filters unauthorized labels out of the "Labels" field in responses of `/tools/find` and `/studies/{id}`; 0.11.2 fixed a bulk-delete bug that let users delete resources they could not access, and a `/tools/find` issue that ignored ParentStudy/ParentSeries (relevant to tenant isolation) — [authorization NEWS](https://orthanc.uclouvain.be/hg/orthanc-authorization/file/default/NEWS)
- Official "Multitenant DICOM" sample plugin (Orthanc >= 1.12.0) starts extra DICOM servers, each with own AET and port, restricted by labels (`LabelsConstraint` All/Any/None) and auto-labels C-STOREd data; supports C-FIND, C-MOVE and C-STORE only — [Multitenant DICOM plugin page](https://orthanc.uclouvain.be/book/plugins/multitenant-dicom.html)
- orthanc-auth-service (github.com/orthanc-team/orthanc-auth-service): web service next to Orthanc that issues JWTs the authorization plugin validates, generates share links for Stone, OHIF and MedDream viewers, integrates with Keycloak for user authentication, ships docker-compose examples including Keycloak, maps roles to permissions via `permissions.json` (default admin=all, doctor=view/download/share/send), and supports role-level `authorized-labels` and an `available-labels` field — [orthanc-auth-service README](https://github.com/orthanc-team/orthanc-auth-service)
- orthanc-auth-service releases use calendar versioning: 26.10.2 (2026-10-22), 26.10.1 (2026-10-21), 26.10.0 (2026-10-20), 26.8.1 (2026-08-28), 26.8.0 (2026-08-13), 26.4.1 (2026-04-23) — [releases feed](https://github.com/orthanc-team/orthanc-auth-service/releases)
- orthanc-auth-service licensing is REUSE-style (LICENSES folder: Apache-2.0, CC-BY-4.0, CC0-1.0, GPL-3.0-or-later; the README is CC-BY-4.0). The folder may contain more files; the effective licence of the service code was not isolated — [LICENSES folder](https://github.com/orthanc-team/orthanc-auth-service/tree/main/LICENSES)
- The authorization plugin was deprecated 2020-2022 and active development resumed in May 2022 — [Authorization plugin page](https://orthanc.uclouvain.be/book/plugins/authorization.html)

### Inferences
- Two viable tenancy models: (a) one shared Orthanc + PostgreSQL with a label per tenant (hospital) enforced by authorization plugin and Keycloak roles/groups mapped to `authorized-labels` through the auth service; (b) one Orthanc container per tenant (each with its own PostgreSQL schema/DB and S3 prefix/bucket) for hard isolation. Given AGPL plugins, per-tenant instances plus a shared control plane is the cleaner isolation story for hospital data; label-based sharing is cheaper but relies on every API route being filtered correctly (past bugs like the bulk-delete one show this risk).
- DICOM-side (C-STORE/C-FIND from modalities) isolation in shared mode needs the Multitenant DICOM plugin with one AET/port per tenant, which does not scale well to many tenants (port per tenant). For many small hospitals, per-tenant instances behind one DICOM proxy/port-mapper may be simpler.
- Keycloak mapping (realm/group/attribute to `authorized-labels`) is something the blueprint will need to implement in the auth service or a custom profile endpoint; I found no published turnkey multi-hospital Keycloak recipe.

### Gaps
- No documented end-to-end multi-hospital Keycloak configuration found; the Orthanc forum threads were not opened.
- orthanc-auth-service effective licence of its own code (Apache-2.0 vs GPL-3.0-or-later) not determined.
- No authoritative published statement that labels are a security boundary (vs convenience filter).

## OHIF Viewer: version, licence, Keycloak/OIDC, embedding, MONAI Label

### Takeaway
Latest stable OHIF is v3.13.13 (2026-10-16, a security-dependency release); v3.14.0 is in rapid beta (beta.68 on 2026-10-29). Licence is MIT. It supports OIDC via an `oidc` config key; Keycloak recipes exist but the Orthanc plus Keycloak path is best done via Orthanc's own plugin bundling.

### Cited Findings
- Latest stable: v3.13.13, 2026-10-16T17:18Z (marked Latest; fix(security) dependency update). The 3.12.0 release tag is dated 2026-02-06. Pre-release v3.14.0-beta.68 is dated 2026-10-29 — [GitHub latest release](https://github.com/OHIF/Viewers/releases/latest), [releases feed](https://github.com/OHIF/Viewers/releases.atom)
- Licence MIT, Copyright (c) 2018 Open Health Imaging Foundation — [LICENSE](https://raw.githubusercontent.com/OHIF/Viewers/master/LICENSE)
- OIDC: settings live under the `oidc` config key; the `userAuthenticationService` attaches the token as an Authorization header on DICOMweb requests (metadata and pixel data). Implicit flow is default but docs recommend Authorization Code flow (`useAuthorizationCodeFlow`, `response_type: code`) and warn implicit will stop working as browsers block third-party cookies. `dangerouslyAllowedOriginsForAuthenticatedEnvironments` restricts which origins get credentials for dicomwebproxy. Token-in-URL auth is supported — [OHIF authorization docs](https://docs.ohif.org/deployment/authorization)
- A community thread shows a Keycloak + dcm4chee config (client_id `ohif-viewer`, redirect `/callback`) producing a black screen, and notes Keycloak recipes were being added to docs — [OHIF community thread](https://community.ohif.org/t/ohif-keycloak-dcm4chee-config-gives-black-screen/1575)
- Orthanc ships OHIF as a plugin (OHIF 3.12.0 in plugin 1.8, 2026-03-17), so the Orthanc-bundled OHIF lags upstream by roughly one minor version — [OrthancOHIF NEWS](https://orthanc.uclouvain.be/hg/orthanc-ohif/file/default/NEWS)
- orthanc-auth-service generates publication links for the Stone, OHIF and MedDream viewers (usable for launching from an EHR with a short-lived study-scoped token) — [orthanc-auth-service README](https://github.com/orthanc-team/orthanc-auth-service)
- MONAI Label: latest release 0.8.5 was published 2024-11-25 (rc2 2024-11-23 refactored the OHIF plugin for OHIF V3 3.9+; adds auto segmentation, SAM2, point/class prompts). OHIF integration needs a DICOMweb study source such as Orthanc (`monailabel start_server --app apps/radiology --studies http://127.0.0.1:8042/dicom-web`); OHIF is served at `/ohif/` on the MONAI Label server; OHIF lacks multi-label interaction for DeepEdit — [MONAILabel releases feed](https://github.com/Project-MONAI/MONAILabel/releases.atom), [monailabel PyPI docs](https://pypi.org/project/monailabel/0.6.0rc3)

### Inferences
- EHR launch pattern: EHR backend asks auth service (or Keycloak token exchange) for a study-scoped token, then redirects the browser to the Orthanc-hosted OHIF/Stone URL carrying `StudyInstanceUIDs`; embedding in an iframe also works for the OHIF/Stone viewer if CSP/X-Frame-Options are configured (not verified in sources).
- MONAI Label integration is a research/annotation workflow, not a clinical reporting workflow; its OHIF plugin targets OHIF 3.9+ as of Nov 2024 and has had no tagged release since, so compatibility with OHIF 3.12/3.13 is unverified.

### Gaps
- Exact Keycloak client settings for OHIF (PKCE, web origins) were not found in a primary recipe.
- Iframe embedding/CSP guidance not sourced.
- Whether MONAI Label works with OHIF 3.13 is unknown.

## dcm4chee-arc-light (comparison)

### Takeaway
dcm4chee-arc-light is at 5.35.2 (2026-10-15), a Java EE archive on WildFly with LDAP configuration, much heavier than Orthanc but built-in multi-AE/multi-tenant style configuration and IHE profile coverage. Licence is Mozilla Public License 1.1 based (tri-licence likely, only MPL text confirmed here).

### Cited Findings
- Release dates from the feed: 5.35.2 (2026-10-15), 5.35.1 (2026-08-24 per feed entry), 5.35.0 (2026-09-03 per feed entry; ordering in the feed is inconsistent), 5.34.3 (2026-04-24), 5.34.2 (2026-07-03), 5.34.1 (2025-09-19), 5.34.0 (2026-04-30), 5.33.1 (2024-12-18) — [releases feed](https://github.com/dcm4che/dcm4chee-arc-light/releases.atom). The feed's per-entry dates are non-monotonic, so use only the 5.35.2 / 2026-10-15 "Latest" claim with confidence — [GitHub releases](https://github.com/dcm4che/dcm4chee-arc-light/releases)
- README: DICOM archive Java EE application running in WildFly; uses LDAP as central configuration (DICOM Application Configuration Management Profile); build needs Java 17+ — [README](https://raw.githubusercontent.com/dcm4che/dcm4chee-arc-light/master/README.md)
- The repo's licence text references "Mozilla Public License Version 1.1" (GitHub page) — [GitHub repo](https://github.com/dcm4che/dcm4chee-arc-light)

### Inferences
- Footprint is heavier than Orthanc (WildFly + LDAP + relational DB + often Keycloak/Elasticsearch in typical deployments); this is general knowledge, not sourced here.
- MPL 1.1 is file-level copyleft and friendlier to proprietary SaaS than AGPL, which is the main reason to consider dcm4chee over Orthanc for a commercial multi-tenant product.

### Gaps
- Exact licence set (MPL 1.1 / GPL 2 / LGPL 2.1 tri-licence) not confirmed; docs.dcm4che.org was unreachable.
- Resource footprint and multi-tenancy features not sourced.

## Reporting / RIS / DICOM Modality Worklist options

### Takeaway
Orthanc offers MWL only through a worklists plugin; the legacy sample is a folder-of-`.wl`-files server, and a newer separately packaged Worklists plugin (since Nov 2025) adds a REST API and Orthanc Explorer 2 UI. I found no mature open-source radiology reporting/RIS suitable as a drop-in; reporting would be built on the platform (orders from the HIS, worklist creation, report authoring, PDF/DICOM-SR output).

### Cited Findings
- The legacy Worklists plugin makes Orthanc a Modality Worklist SCP serving `.wl` files from a folder (mimicking DCMTK `wlmscpfs`; `dump2dcm` generates files); options `Enable`, `FilterIssuerAet`, `LimitAnswers`, `Database`. Described as a sample plugin for developers, source GPLv3+ — [Worklists plugin page](https://orthanc.uclouvain.be/book/plugins/worklists-plugin.html)
- Since November 2025 a more feature-rich Worklists plugin is packaged separately: REST API to create/delete/edit worklists, integrated in Orthanc Explorer 2; the auth service permission list includes `worklists` — [Worklists plugin page](https://orthanc.uclouvain.be/book/plugins/worklists-plugin.html), [orthanc-auth-service README](https://github.com/orthanc-team/orthanc-auth-service)
- Stone Web Viewer 3.0 (2025-12-03) added DICOM-SR text reports and experimental DICOM-SR Measurement Report support and PDF viewer print/download — [Stone NEWS](https://orthanc.uclouvain.be/hg/orthanc-stone/file/default/Applications/StoneWebViewer/NEWS)

### Inferences
- A HIS-driven flow (order in HIS -> worklist via REST -> modality C-FIND -> images to Orthanc -> report in HIS/RIS linked to StudyInstanceUID) is feasible with Orthanc alone; the reporting editor itself is custom work.
- The newer Worklists plugin's licence and version are not documented in what I fetched.

### Gaps
- No search was run for standalone open-source RIS/teleradiology products (e.g. OpenRIS-style projects); none is claimed.
- Newer Worklists plugin licence/version not verified.

## OpenELIS Global 3.x (version, licence, stack, FHIR/HL7, analyzers, multi-tenancy, India, NABL)

### Takeaway
Latest release is OpenELIS-Global 3.3.0 (2026-10-20), MPL-2.0, Java 21/Spring + React (Carbon) + Docker, FHIR R4 native, with a separate Analyzer Bridge service for ASTM/HL7 analyzers. No multi-tenancy feature and no India deployment evidence were found; ISO 15189 is claimed only at a marketing level.

### Cited Findings
- Release cadence per feed: 3.3.0 (2026-10-20), 3.2.3.0 (2026-09-26), 3.2.2.0 (2026-08-18), 3.2.1.11 (2026-07-14), 3.2.1.10 (2026-06-04), 3.2.1.9 (2026-05-19) — [releases feed](https://github.com/DIGI-UW/OpenELIS-Global-2/releases.atom)
- 3.3.0 notes: External Quality Assessment V2, three-step clinical order entry, inventory board, new Bridge-based analyzer integration with per-analyzer mappings and a test harness, legacy analyzer plugin API removed, PDF generation moved from iText 5 to OpenPDF, Spring Security 6.2.8 -> 6.5.11, analyzer pairing via certificates, critical-value acknowledgment, release blocking on open QC failures, FHIR fixes — [3.3.0 release notes](https://github.com/DIGI-UW/OpenELIS-Global-2/releases/tag/3.3.0)
- 3.2.x notes: validation triage queue and bulk release, questionnaires stored in OpenELIS with FHIR store optional, early bacteriology MVP, SBOM and Trivy in releases, outbound order dispatch over ASTM and HL7 to the analyzer bridge (3.2.1.10), QC analyzer/FHIR follow-ups and "scenario-only QC contract" (3.2.1.9) — [releases page](https://github.com/DIGI-UW/OpenELIS-Global-2/releases)
- Licence MPL-2.0; Java/Spring backend, React with Carbon design system, Docker deployment, Java 21 for dev; "FHIR R4 native"; FHIR server and Analyzer Bridge needed only for exchanges with EMRs/analyzers (light install can skip both by leaving `ANALYZER_BRIDGE_URL` empty); used in "25+ countries" — [GitHub repo](https://github.com/DIGI-UW/OpenELIS-Global-2)
- DIGI wiki: used by more than 1,000 laboratories, 18.7 million patients, 26 countries; bidirectional interfaces for over 50 instruments; supports ISO 15189 accreditation as a framework; HL7 FHIR R4 and LOINC. India is not mentioned — [About OpenELIS Global](https://uwdigi.atlassian.net/wiki/spaces/oeg/pages/239337475/About+OpenELIS+Global)
- Older (2.7-era) analyzer architecture: OpenELIS 2.7.0.5+, an analyzer plugin jar, and an ASTM-HTTP bridge; HL7 v2.5.1 messages for results with EMRs; analyzer plugins are loaded only at server start. A 3.2 development-target page planned ASTM plugin in default Docker deployment, a GUI mapping panel, and pre-configured GeneXpert mappings — [Part 7 Setup Analyzers](https://uwdigi.atlassian.net/wiki/spaces/OG/pages/257196062/PART+7+SETUP+ANALYZERS), [v3.2 Development Target](https://uwdigi.atlassian.net/wiki/spaces/OG/pages/939655182)
- Search for OpenELIS in India with NABL/ISO 15189 returned no deployment or accreditation evidence — [web search results (NABL portal for ISO 15189:2022 context)](https://www.biospectrumindia.com/news/22/26548/nabl-unveils-new-medical-application-portal-for-iso-151892022-applicant-laboratories.html)
- OpenELIS last commit on develop: 2026-11-02 (very active) — [commits feed](https://github.com/DIGI-UW/OpenELIS-Global-2/commits/develop.atom)

### Inferences
- MPL-2.0 is file-level copyleft: you can run it as a SaaS and add proprietary components alongside it, but modified MPL files must be published; this is friendlier than AGPL for a commercial offering.
- OpenELIS Global 2/3 is a single-organisation system (national-scale via sites/lab units inside one instance), so a multi-tenant SaaS would most likely be one deployment (Postgres DB plus app containers) per tenant, which is inferred, not documented.
- Its public-health origin (HIV/TB, Africa) means India-specific needs (NABL report formats, GST billing, Indian test master, local-language reports, ABDM) would need local work; 3.3.0 EQA and QC release-blocking features are directly relevant to NABL.

### Gaps
- Exact supported-analyzer list for the 3.x Analyzer Bridge (Sysmex, Cobas, Beckman, Mindray, Erba, Transasia) was not found; the analyzer-bridge repository URL I tried returned 404.
- Multi-tenancy, resource requirements, NABL-specific features not documented in sources found.
- 3.3.0 release year is inferred from the atom feed (2026-10-20), consistent with page footer.

## SENAITE LIMS

### Takeaway
Latest SENAITE is 2.6.0 (2025-04-04); no release in the 19 months since, although the repo still receives commits (2026-11-02). GPL-2.0, Plone/Python. It is a LIMS (environmental/QC/industrial heritage) with a senaite.health clinical add-on; no evidence found of native multi-tenancy or built-in HL7/FHIR.

### Cited Findings
- senaite.lims and senaite.core 2.6.0 are dated 2025-04-04; prior 2.5.0 was 2024-01-03; 2.4.1 2023-03-11 — [senaite.lims releases feed](https://github.com/senaite/senaite.lims/releases.atom), [senaite.core feed](https://github.com/senaite/senaite.core/releases.atom)
- SENAITE built on Plone, GPL-2.0; senaite.health adds patients, doctors and clinical cases; hosting pages claim a REST JSON API; HL7/FHIR/IHE integrations are offered by a partner (YME), not core — [Hostinger application page](https://www.hostinger.com/lv/applications/senaite-lims), [IntuitionLabs open-source LIMS overview](https://intuitionlabs.ai/articles/open-source-lims-functionalities)
- Commit activity on senaite.core 2.x: latest commit feed entry 2026-11-02 (branch name assumed 2.x) — [commits feed](https://github.com/senaite/senaite.core/commits/2.x.atom)

### Inferences
- For a hospital SaaS, SENAITE is a weaker fit than OpenELIS (clinical workflow, FHIR, bidirectional analyzer bridge); multi-tenancy would again be one Plone site per tenant.
- GPL-2.0 (not AGPL) has no network-use clause, so SaaS use without distribution does not trigger source disclosure; modified-and-distributed code does.

### Gaps
- Licence text of the repo not directly fetched (LICENSE path 404); GPL-2.0 comes from secondary sources and your brief.
- No primary source for multi-tenancy or HL7 in core; hosting-provider pages are marketing material.

## Analyzer interfacing: ASTM E1381/E1394, HL7 v2 middleware options, and Indian analyzer notes

### Takeaway
Open-source options exist at the library/engine level (python-astm, Open Integration Engine, BridgeLink, OpenELIS Analyzer Bridge) but none is a turnkey, vendor-validated Indian-analyzer driver set. Mirth Connect went proprietary after 4.5.2, so use OIE (MPL-2.0) or BridgeLink (Apache-2.0), not NextGen Mirth. Per-analyzer protocol specifics for Erba/Transasia could not be sourced.

### Cited Findings
- python-astm (original by Alexander Shorin) implements ASTM E1381/E1394 with TCP/IP communication and driver ideas; BSD-3-Clause; PyPI lists `astm` 0.5.0 and the republished `astm-2025` (1.0.0 per PyPI JSON; BSD-3-Clause; homepage still code.google.com/p/python-astm) — [PyPI astm](https://pypi.org/project/astm/), [PyPI JSON astm-2025](https://pypi.org/pypi/astm-2025/json)
- `astmio` is a fork aiming at serial connections, MIT-licensed, alpha (1.0.0a3) — [PyPI astmio](https://pypi.org/project/astmio/)
- NextGen announced on 2025-03-19 that Mirth Connect moves to a single commercial licence from 4.6; 4.5.2 (2025-07-10 tag) is the last open-source release. Forks: Open Integration Engine (MPL-2.0) and BridgeLink (Apache-2.0, by Innovar, based on 4.5.x) — [Healthcare IT Today on BridgeLink](https://www.healthcareittoday.com/2025/08/14/a-look-at-bridgelinks-fork-of-the-open-source-mirth-connect/), [Nirmitee.io on Mirth alternatives](https://nirmitee.io/blog/mirth-connect-alternatives-2026-after-licensing-change)
- Open Integration Engine (github.com/OpenIntegrationEngine/engine): MPL-2.0; releases v4.7.0 (2026-10-21), v4.6.0 (2026-07-09); supports HL7, FHIR, DICOM, HTTP, files, etc. — [OIE repo](https://github.com/OpenIntegrationEngine/engine), [releases feed](https://github.com/OpenIntegrationEngine/engine/releases.atom)
- OpenELIS Analyzer Bridge: separate service converting between analyzer protocols (ASTM, HL7) and OpenELIS (HTTP/FHIR-style) — see OpenELIS section; 3.3.0 pins the bridge at 3.2.6 per the release summary — [releases page](https://github.com/DIGI-UW/OpenELIS-Global-2/releases)
- Protocol layering: CLSI LIS01 (formerly ASTM E1381) covers low-level transfer for serial and TCP/IP; message content is LIS02 (formerly E1394); HL7 v2 over TCP uses MLLP framing; vendor guides (e.g. Beckman) typically offer ASTM TCP/IP, ASTM serial, or HL7 options; serial-to-Ethernet converters (MOXA, Lantronix) are common — [CLSI LIS01](https://clsi.org/shop/standards/lis01/), [AGG Software HL7 tutorial](https://www.aggsoft.com/serial-data-logger/tutorials/hl7.htm)
- Practitioner-reported (low-reliability) notes: Mindray BS-series chemistry analyzers integrated via HL7 2.3.1 over TCP; Sysmex KX-21/XN series via ASTM E1394/LIS2-A or HL7 v2; Roche analyzers have their own "ASTM" and "COBAS" protocol modes — [freelancer/AGG/forum snippets](https://www.freelancer.in/projects/api/lab-analyzer-lis-integration), [Windev forum](https://forum.pcsoft.fr/es-ES/pcsoft.us.windev/40127-windev-17-connecting-analyzers-roche/read.awp)

### Inferences
- Reasonable architecture: per-site edge gateway (small Linux box or container) that speaks serial/TCP ASTM and HL7 MLLP to analyzers, forwards normalized results over HTTPS/MQTT to the cloud middleware; use OIE or custom python-astm code, with test-code mapping tables per tenant.
- Use of python-astm in production needs a code review: the 0.5.0 library is old (Google Code era) and the `astm-2025` republish provenance is unclear.

### Gaps
- No vendor LIS manuals fetched for Sysmex XN, Roche cobas, Beckman AU/DxC, Mindray BC/BS, Erba (XL/EM series), Transasia; no authoritative Indian-market protocol notes.
- OpenELIS Analyzer Bridge repo not located; supported list unknown.

## Verification of blueprint claim: "OpenQC engine (FastAPI) github.com/ashuein/OpenQC", and mature Westgard/Levey-Jennings tools

### Takeaway
The repo exists but it is a very young, single-author, AI-assisted (has a CLAUDE.md agent guide), GPL-3.0 PCR-lab QC app (FastAPI + Vue 3, SQLite), not an "engine" for LIS-integrated clinical chemistry QC. It has 32 commits made between 2026-03-30 and 2026-04-01, 0 stars, 1 fork, no releases. Do not treat it as a mature dependency; at most it is a reference for Westgard rule logic. I found no mature open-source Westgard/LJ package that is LIS-ready; OpenELIS 3.x's built-in QC is the nearest.

### Cited Findings
- GitHub page: "local first quality control platform for PCR and IVD laboratories"; GPL-3.0; 0 stars, 0 watchers, 1 fork, 32 commits; no releases or packages — [OpenQC repo](https://github.com/ashuein/OpenQC)
- Local clone shows: first commit 2026-03-30 "OpenQC v1: full implementation for review", last commit 2026-04-01; all 32 commits by one author; roughly 18,200 lines of Python/Vue/JS; 11 backend test files (westgard, sigma, validation, audit, rag, etc.); a `CLAUDE.md` "AI Agent Guide" with a mandatory automated review gate; example data sets (clinical chemistry, hematology, immunoassay) — [OpenQC repo contents](https://github.com/ashuein/OpenQC)
- README features: Excel upload from QuantStudio/CFX Manager, six Westgard rules (1-2s, 1-3s, 2-2s, R-4s, 4-1s, 10x) with Levey-Jennings charts, Six Sigma analysis, LOD/LOQ/precision/linearity validation, SHA-256 hash-chain audit trail, lot tracker, WeasyPrint PDF reports, and a RAG "regulatory assistant" over CDSCO MD-15, ICMR and ISO 15189 documents using the Anthropic Claude API; stack: Python 3.12+, FastAPI 0.115+, SQLAlchemy, Pydantic v2, SQLite, ChromaDB, Vue 3, Tailwind v4 — [OpenQC README](https://github.com/ashuein/OpenQC)
- README lists multi-user access, Docker deployment, and HL7 integration as future work (per the fetched summary of the README) — [OpenQC repo](https://github.com/ashuein/OpenQC)
- `indialabqc` on PyPI: version 1.0.0 uploaded 2026-08-17, describes Westgard multirule and NABL-style QC reports; its metadata homepage is a placeholder (`github.com/yourusername/indialabqc`) and licence is unset, so it is not credible as a dependency — [PyPI indialabqc](https://pypi.org/project/indialabqc/) (metadata via PyPI JSON)
- Other Westgard/LJ results are commercial (Westgard Green Belt, IAMQC Daily, LabLink xL, QC Constellation) or academic (Swansea Levey-Jennings and Westgard Tutor) — [Westgard store](https://mail.westgard.com/store/software/green-belt-qc-charting-detail.html), [Swansea record](https://cronfa.swan.ac.uk/Record/cronfa71716/Details)
- OpenELIS 3.2.1.9 mentions a "scenario-only QC contract" and QC analyzer/FHIR follow-ups, and 3.3.0 adds release blocking on open QC failures — [OpenELIS releases](https://github.com/DIGI-UW/OpenELIS-Global-2/releases)

### Inferences
- The blueprint's description ("OpenQC engine (FastAPI)") is accurate only in the narrow sense that the backend uses FastAPI with a pure-logic `backend/engine/westgard` module; it is a standalone app, PCR-oriented, Excel-ingesting, SQLite-only and single-tenant.
- Because it is GPL-3.0 (not AGPL), embedding its code in a closed SaaS would require care; reimplementing Westgard rules (simple, well-defined) is lower risk than adopting this code.
- A multi-tenant SaaS QC module is realistically a custom build (Westgard multirules are easy; the work is lot/target/SD management, per-analyzer-channel data capture, audit trail) or reliance on OpenELIS 3.x QC.

### Gaps
- I did not run OpenQC's tests or audit code quality.
- Did not find a mature open-source Westgard library on PyPI/GitHub in the limited search; other candidates may exist.

## MONAI Label and MONAI Deploy: versions, licences, Orthanc integration

### Takeaway
MONAI Label's latest tagged release is 0.8.5 (2024-11-25), Apache-2.0, with DICOMweb/OHIF support but no tagged release in about two years (repo still gets commits; last seen 2026-10-22). MONAI Deploy App SDK is at 4.0.0 (2026-06-13) and active.

### Cited Findings
- MONAI Label releases: 0.8.5 (2024-11-25), 0.8.4 (2024-10-17), 0.8.3 (2024-07-23); licence Apache-2.0 — [releases feed](https://github.com/Project-MONAI/MONAILabel/releases.atom), [LICENSE](https://raw.githubusercontent.com/Project-MONAI/MONAILabel/main/LICENSE)
- MONAI Label repo most recent commit-feed entry 2026-10-22 (branch main) — [commits feed](https://github.com/Project-MONAI/MONAILabel/commits/main.atom)
- MONAI Label connects to 3D Slicer and OHIF; PACS connectivity over DICOMweb; works against Orthanc DICOMweb at port 8042 with OHIF served by the MONAI Label server — [monailabel PyPI docs](https://pypi.org/project/monailabel/0.6.0rc3)
- MONAI Deploy App SDK releases: 4.0.0 (2026-06-13), 3.5.0 (2026-01-17), 3.4.0 (2025-12-07), 3.3.0 (2025-10-31), 3.2.0 (2025-10-01), 3.1.0 (2025-09-06), 3.0.0 (2025-04-23). The GitHub releases HTML page showed only v3.0.0 as the tagged "Latest" release (2025-04-22/23), so the atom-feed entries may include tags without formal GitHub releases — [releases feed](https://github.com/Project-MONAI/monai-deploy-app-sdk/releases.atom), [GitHub releases page](https://github.com/Project-MONAI/monai-deploy-app-sdk/releases)

### Inferences
- MONAI Deploy licence is expected to be Apache-2.0 (as for MONAI projects) but I could not fetch its LICENSE file; verify before relying on it.
- Neither MONAI tool is a regulated clinical product; for a hospital SaaS they are suitable for AI inference pipelines (Deploy) triggered from Orthanc events (e.g. via Python plugin or DICOM C-STORE/ DICOMweb), and for research annotation (Label), not for clinical decision support without CDSCO medical-device pathway work (not researched).

### Gaps
- MONAI Deploy licence not verified; no Orthanc-specific MONAI Deploy integration docs found.
- MONAI Label compatibility with OHIF >= 3.10 unverified.
