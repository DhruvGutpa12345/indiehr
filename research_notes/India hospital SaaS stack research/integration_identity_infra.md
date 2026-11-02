# Integration, Identity and Infrastructure Layers for a Multi-tenant Hospital SaaS Hosted in India (state as of 2 November 2026)

Method note: version/date facts come from the official project release pages (GitHub releases, apache.org, kubernetes.io, Maven Central metadata) fetched on 2026-11-02. GitHub release pages show day/month only; the year 2026 is inferred from asset timestamps and surrounding context unless stated. Legal/regulatory points rely largely on law-firm commentary, plus the CERT-In primary PDF that was read directly. Items marked UNVERIFIED are single-source or vendor-sourced.

## 1. Keycloak: latest version, multi-tenancy (realm-per-tenant vs Organizations), scale, fine-grained authz, MFA/WebAuthn, licence

### Takeaway
Keycloak is at 26.8.1 (13 Oct 2026), Apache-2.0, with Organizations GA since 26.0 and the official docs themselves naming no realm cap. Third-party measurement (vendor blog) says use Organizations for most SaaS multi-tenancy and keep realm-per-tenant to a few hundred (ideally <100 per cluster) where hard isolation is demanded.

### Cited Findings
- Latest release is Keycloak 26.8.1, released 13 Oct 2026; prior: 26.8.0 (1 Oct), 26.7.5 (30 Sep), 26.7.4 (16 Sep), 26.7.3 (31 Aug). The blog archive shows no Keycloak 27 post. — [GitHub releases](https://github.com/keycloak/keycloak/releases); [Keycloak blog archive](https://www.keycloak.org/blog-archive)
- Keycloak 26.8.0 (1 Oct 2026): an IdP can now be linked to multiple Organizations with per-link auto-membership/membership type; Workflows gets an `invite-user` step; token-exchange delegation (preview) under Fine-Grained Admin Permissions V2 (`delegate`, `delegate-members` scopes, `act` claim); SCIM API and client secret rotation now supported; "stateless" feature and Multi-cluster v2 supported (sessions in DB, no external Infinispan), multi-cluster v1 (`multi-site`) deprecated; login failures stored in DB by default; OID4VCI/OID4VP in preview. — [Keycloak 26.8.0 release post](https://www.keycloak.org/2026/10/keycloak-2680-released)
- Also in July 2026: "Multi-Cluster v2 and Stateless Mode now in Preview" (blog), then supported in 26.8.0. — [Keycloak blog archive](https://www.keycloak.org/blog-archive)
- Licence: Apache License 2.0; project follows the CNCF Code of Conduct (repo shows CNCF CLOMonitor badge; incubation level not stated on the page fetched). — [GitHub keycloak/keycloak](https://github.com/keycloak/keycloak)
- Organizations (26.8.1 admin guide headings): create/disable/delete orgs, attributes, members, groups, per-org identity providers, domains and domain routing, identity-first login, managed/unmanaged membership, invitations. — [Server Admin Guide (latest)](https://www.keycloak.org/docs/latest/server_admin/index.html#_managing_organizations)
- Official docs: "One Keycloak deployment can define, store, and manage as many realms as there is space for in the database" (no fixed limit stated). — [Server Admin Guide](https://www.keycloak.org/docs/latest/server_admin/index.html#_managing_organizations)
- Organizations was preview in Keycloak 25 and GA since Keycloak 26 (secondary source). — [Skycloak summary via search](https://skycloak.io/blog/multitenancy-in-keycloak-using-the-organizations-feature/); original 26.0.0 post dated 4 Oct 2024: [Keycloak 26.0.0 released](https://www.keycloak.org/2024/10/keycloak-2600-released)
- Scale measurements (VENDOR blog, Skycloak, Keycloak 26.6, operator-managed): no hard realm limit; 3-replica cluster held 2000 realms; ~0.9–1.3 MB heap per empty realm; per-pod cold start 176–240 s at 2000 realms; full restart ~450 s on 3 replicas; full `GET /admin/realms` at 3000 realms took 39 s while paginated console endpoint took 137 ms; bulk realm creation slowed from 2.33/s (100 realms) to 0.89/s (2000); node loss stalled embedded Infinispan ~31–40 s; login/token/userinfo/JWKS performance stayed flat as realm count grew. Author's guidance: up to ~300 realms single cluster; 300–1000 bigger cluster; 1000–2000 shard into 3–5 clusters of ~400; use Organizations for most new SaaS; realm-per-tenant ideally <~100 per cluster where strict isolation needed. — [Skycloak realm scaling](https://skycloak.io/blog/keycloak-realm-scaling-how-many-realms-per-cluster/) (benchmarks not independently verified; Skycloak sells managed Keycloak)
- Community anecdotes: ~400 realms felt slow; one user ran 1 realm with 10k organizations via the Phase Two extension; consensus of vendor blogs is Organizations by default, realms only for true isolation. — [Keycloak forum thread via search results](https://forum.keycloak.org/t/keycloak-multi-tenancy-extensions-for-saas-applications/15426); [Phase Two](https://phasetwo.io/blog/multi-tenancy-options-keycloak/)
- MFA/WebAuthn: admin guide covers WebAuthn, passwordless WebAuthn, LoginLess WebAuthn, passkey authentication modes, recovery codes, TOTP/HOTP (Google Authenticator/FreeOTP), step-up authentication. 26.8.0 notes only mention WebAuthn fixes (concurrent registration bug, cached trust verifier). — [Server Admin Guide](https://www.keycloak.org/docs/latest/server_admin/index.html#_managing_organizations); [26.8.0 post](https://www.keycloak.org/2026/10/keycloak-2680-released)
- Fine-grained authz: two layers exist — Authorization Services (resource/scope/policy based, separate guide) and Fine-Grained Admin Permissions (FGAP; V1 documented as legacy section, newer "Delegating realm administration using permissions" section; V2 referenced in 26.8.0 release notes). — [Server Admin Guide](https://www.keycloak.org/docs/latest/server_admin/index.html#_managing_organizations); [26.8.0 post](https://www.keycloak.org/2026/10/keycloak-2680-released)

### Inferences
- For a hospital SaaS where each hospital/group is a tenant, a hybrid is plausible: one realm (or a small number of shards) for the application with Organizations per hospital; separate realm only for large enterprise customers demanding own IdP/branding/password policies/data export. Hard isolation vs ops cost is the trade-off; Organizations share realm-level config (password policy, MFA flows, themes).
- Because 26.8 added stateless mode / DB-backed sessions, Keycloak can run on Postgres without an Infinispan cluster, simplifying Kubernetes ops (but check that stateless is acceptable for your session load).
- Keycloak 26.x cadence is ~2 weeks for patch, ~quarterly minor (26.5 Jan 2026 -> 26.6 Jun -> 26.7 Jul -> 26.8 Oct); budget for regular upgrades.

### Gaps
- Official Keycloak numeric guidance on realm limits was not found; only vendor/community figures. Whether Organizations support per-org password/MFA policy (they do not appear to; realm-level) was not verified in the docs text.
- Keycloak required Java version for 26.8 not confirmed (release post silent).
- Exact CNCF maturity level (incubating) not confirmed from fetched pages.

## 2. Mirth Connect licence change, Open Integration Engine (OIE) and BridgeLink

### Takeaway
NextGen closed Mirth Connect from 4.6 (announced 19 Mar 2025); 4.5.2 is the last open-source release. Two MPL-2.0 forks exist: Open Integration Engine (now Eclipse OIE, v4.7.0 on 21 Oct 2026) and Innovar's BridgeLink (v26.9.0, 1 Oct 2026, with a commercially licensed WebAdmin component).

### Cited Findings
- NextGen blog (19 Mar 2025, Muhammad Chebli): moving from dual open-source/commercial licence to "a single commercial and proprietary" licensing model starting with Mirth Connect 4.6; last open-source version is 4.5.2; options are stay on current, upgrade to 4.5.2, or get licensed copy for 4.6; paid tiers Enterprise, Gold, Platinum (Enterprise adds SSL Manager, Channel History, Message Generator; Mirth Command Center in all tiers, not available in EU/UK); free "Mirth Connect for Research" licence for approved non-profit-affiliated studies. — [NextGen blog](https://www.nextgen.com/blog/industry-news/a-new-era-for-mirth-connect-by-nextgen-healthcare)
- OIE: first release 4.5.2 Technical Preview 1 (13 Jun 2025), functionally identical to Mirth 4.5.2 with rebranding, removed the old web admin and Mirth Manager tray tool; 4.5.2 stable (9 Jul 2025); 4.6.0-rc1 (7 May 2026); 4.6.0 (9 Jul 2026; Java 17 minimum, removed Xerces/xml-apis, 24 CVEs fixed via dependency upgrades incl. critical PostgreSQL JDBC SQLi, case-insensitive logins by default); 4.7.0 (21 Oct 2026, latest; fixes SQL injection and XXE issues, Gradle replaces Ant with Docker build, fresh DBs no longer default to admin/admin, default minimum password length 15, HTTP Sender local address binding). — [OIE GitHub releases](https://github.com/OpenIntegrationEngine/engine/releases)
- Years inferred: the OIE releases page shows no years; 4.6.0 date taken from the 2026 context (the OIE site says "OIE 4.6.0 generally available"); the 4.5.2 stable "9 Jul" and TP1 "13 Jun" are most plausibly 2025 (fork launched spring 2025). Treat the exact years of those two as UNVERIFIED.
- OIE site: 4.7.0 dated 21 Oct 2026, "generally available and recommended for production use", fixes 3 CVEs in engine and 45 in bundled components. Project is in incubation under Eclipse Development Process; formally "Eclipse Open Integration Engine" after creation review completed 7 Sep 2026; Eclipse provides vendor-neutral governance; IP review ongoing. — [openintegrationengine.org](https://openintegrationengine.org)
- OIE licence: MPL-2.0; builds with Gradle on JDK 17; repo 240 stars/86 forks at fetch; Docker Hub profile `openintegrationengine`; roadmap: stabilise code, docs, community, longer term new standards, UX, plugin ecosystem. — [GitHub OpenIntegrationEngine/engine](https://github.com/OpenIntegrationEngine/engine)
- BridgeLink (Innovar Healthcare): first release 4.5.4 on 21 Jul 2025 (press release), fork of Mirth 4.5.2, ~14 CVE dependency fixes; v4.6.0 (23 Oct 2025), v4.6.1 (8 Jan 2026), v26.3.0 (7 Apr 2026: Java 17+, Jetty 9.4->12.0, SMTP OAuth2, built-in Version History), v26.3.1 (15 May), v26.6.0 (22 Jul; BridgeLink WebAdmin browser console), v26.6.1 (16 Sep; four CVEs, Derby 10.17.1.0, Java 21 default for installers, Derby won't start on Java 17), v26.9.0 (1 Oct 2026, latest; Java 21 for built-in Derby, jTDS retired in favour of mssql-jdbc, Sybase dropped, legacy /webadmin dashboard removed, services run under dedicated accounts; 26.9.0.1 installers dated 27 Oct 2026). Years for 4.6.0/4.6.1 inferred from sequence. — [BridgeLink releases](https://github.com/Innovar-Healthcare/BridgeLink/releases); [press release 4.5.4](https://www.accessnewswire.com/newsroom/en/computers-technology-and-internet/innovar-releases-bridgelink-4.5.4-the-only-enterprise-grade-open-1051088)
- BridgeLink licence: MPL-2.0 for the engine; "BridgeLink WebAdmin" is separately under Business Source License 1.1. Docker: `innovarhealthcare/bridgelink` (amd64/arm64), `innovarhealthcare/bridgelink-webadmin`; `bridgelink-container` repo has Dockerfiles, Compose, Helm chart; hardened `-dhi` and `-slim` variants. States it is not affiliated with NXGN Management (Mirth trademark owner). — [GitHub Innovar-Healthcare/BridgeLink](https://github.com/Innovar-Healthcare/BridgeLink)
- Innovar claims >10,000 downloads (Jan 2026) — vendor marketing. — [AccessNewswire](https://www.accessnewswire.com/newsroom/en/computers-technology-and-internet/the-market-has-spoken-bridgelink-surpasses-10-000-downloads-solid-1130934)

### Inferences
- Both forks are MPL-2.0 (file-level copyleft), fine for embedding as a commercially run integration service; use of the "Mirth" name should be avoided (trademark).
- OIE has Eclipse governance (neutral, reduced single-vendor risk) and an active security-oriented 2026 cadence; BridgeLink has faster version numbering (26.x, Java 21 push) and a BSL-licensed UI piece, which adds licence complexity. OIE is probably the lower-risk default for a SaaS vendor; verify the OIE Docker image name/tags directly before adopting.
- Neither fork has any verified ABDM/NHCX-specific connector; HL7v2/FHIR transport is generic.

### Gaps
- OIE official Docker image name/tags not confirmed (page only links to Docker Hub profile).
- Exact years for OIE 4.5.2/TP1 and BridgeLink 4.6.0/4.6.1 not shown on release pages.
- Eclipse project incubation vs. mature status: described as "incubation" only per OIE site.

## 3. Apache Camel LTS, camel-hl7/camel-fhir, Ozone EIP client, HAPI FHIR and other FHIR servers (ABDM/NHCX facade)

### Takeaway
Camel 4.22.2 LTS (Oct 2026, EOL Aug 2027, Java 17/21/25) is the current LTS; HAPI FHIR is at 8.12.1 (Maven Central, 30 Oct 2026) under Apache 2.0 and is the natural basis for an ABDM/NHCX facade because NRCeS's own implementation guide has a HAPI section. Medplum (Apache 2.0) is the strongest OSS alternative; Aidbox and Firely are commercial.

### Cited Findings
- Camel: LTS 4.22.2 (Oct 2026, EOL Aug 2027, Java 17/21/25) and LTS 4.18.5 (Oct 2026, EOL Feb 2027, Java 17/21); latest non-LTS 4.23.0 (Oct 2026). — [Camel download page](https://camel.apache.org/download/)
- `camel-hl7`: artifact `org.apache.camel:camel-hl7` since Camel 2.0; uses HAPI HL7v2 `hapi-base` (user adds `hapi-structures-vXX` jars); Netty MLLP codec since 2.15. — [Camel HL7 docs](https://camel.apache.org/components/next/dataformats/hl7-dataformat.html)
- `camel-fhir`: since Camel 2.23; built on HAPI FHIR; `fhirVersion` supports DSTU2, DSTU2_HL7ORG, DSTU2_1, DSTU3, R4, R4B, R5; default R4. — [Camel FHIR component](https://camel.apache.org/components/next/fhir-component.html)
- Ozone (OpenMRS-based Ozone HIS): interoperability layer is Camel routes pulling directly from OpenMRS MySQL (e.g. repos `ozone-his/eip-odoo-openmrs`, `eip-openmrs-senaite`); sources dated mostly 2024. No India-specific "EIP client" status found. — [ozone-his/eip-odoo-openmrs](https://github.com/ozone-his/eip-odoo-openmrs); [OpenMRS interoperability wiki](https://openmrs.atlassian.net/wiki/spaces/docs/pages/300613733/Interoperability+Integration)
- HAPI FHIR: Maven Central `hapi-fhir-base` metadata latest/release = 8.12.1, last updated 2026-10-30 21:18 UTC; recent: 8.6.5–8.6.8, 8.8.0–8.8.2, 8.10.0–8.10.2, 8.12.0, 8.12.1. GitHub releases page listing: v8.10.2 (30 Oct), v8.8.2 (25 Sep), v8.12.1 (15 Sep), v8.12.0 (1 Sep) — i.e. parallel maintenance branches (8.10/8.8 patched after 8.12). — [Maven Central metadata](https://repo1.maven.org/maven2/ca/uhn/hapi/fhir/hapi-fhir-base/maven-metadata.xml); [HAPI releases](https://github.com/hapifhir/hapi-fhir/releases)
- HAPI FHIR licence Apache 2.0; commercial support via Smile CDR. — [Oracle doc quoting project / search result](https://docs.oracle.com/en/industries/health/health-patient-portal/pptlg/hapi-fhir.html) (secondary; verify at hapifhir.io)
- HAPI JPA server starter: Apache-2.0; min JDK 17; Docker image `hapiproject/hapi` (distroless variants `-distroless`, bundles OpenTelemetry Java agent); R4 default, R5 IG install supported. — [hapi-fhir-jpaserver-starter](https://github.com/hapifhir/hapi-fhir-jpaserver-starter)
- Medplum: latest 5.2.13 (30 Oct 2026; also 5.1.45 patch same day); platform mostly Apache 2.0 (FHIR server, APIs, SDKs, React components, CLI), self-hostable on Kubernetes/AWS/Azure/GCP/on-prem; paid = hosted service, enterprise support, compliance support. — [Medplum releases](https://github.com/medplum/medplum/releases); [Medplum open source](https://www.medplum.com/open-source)
- Aidbox (Health Samurai) is commercial: Dev $0 for non-PHI (5 GB limit), Base from $19,000/yr ($1,900/mo), enterprise by quote; flat-rate licence per unique database. — [Aidbox editions & pricing](https://www.health-samurai.io/docs/aidbox/overview/editions-and-pricing) (via search summary)
- Firely Server: commercial, tiered licences; free evaluation licence/sandbox; no public prices found. — [Firely docs](https://docs.simplifier.net/vonk/overview.html) (via search summary)
- ABDM FHIR Implementation Guide (NRCeS): v6.5.0 labelled current/Release on R4 (4.0.1); a 7.0.0 build exists as Draft (dated 2026-07-15) — not confirmed released; v5.0.0 added TaskBundle for multiple NHCX use cases; NRCeS "Implementation Guide for Adoption of FHIR in ABDM and NHCX" (Sept 2024) includes a "HAPI FHIR Library" section and NHCX collection bundle guidance. — [NRCeS ABDM IG](https://nrces.in/ndhm/fhir/r4/toc.html); [NRCeS adoption guide PDF](https://www.nrces.in/download/files/pdf/Implementation_Guide_for_Adoption_of_FHIR_in_ABDM_and_NHCX.pdf); [ABDM devforum v5.0.0](https://devforum.abdm.gov.in/t/release-of-fhir-implementation-guide-for-abdm-build-v5-0-0/8631)

### Inferences
- ABDM/NHCX profiles are R4 (4.0.1); all of HAPI, Medplum and Camel support R4, so choice rests on licence and operating model, not FHIR version. HAPI + Camel (Java stack) gives a facade/validation library with NRCeS-documented usage; Medplum is a full TypeScript FHIR platform (Postgres-backed, built-in auth/subscriptions) and may be heavier if you only need a facade. Aidbox/Firely add licence cost per DB/instance, a poor fit for per-tenant instances.
- HAPI's JPA server multi-tenancy is via "partitioning" (known feature, not verified on the fetched page) — confirm in docs for tenant isolation.
- HAPI's 8.x even-minor numbering and parallel branches means pin to a single minor and track the patch line (e.g. 8.12.x).

### Gaps
- No evidence found on Ozone EIP client status or any India deployment; treat "Ozone EIP client" as unverified/unknown.
- Whether Medplum or HAPI has ABDM/NHCX-certified components: nothing found.
- Camel 4.22/4.18 exact day release dates (page gives month only); HAPI FHIR licence not read from hapifhir.io directly.
- Firely licence terms and prices unavailable.

## 4. Message brokers: Kafka, Redpanda, NATS, RabbitMQ

### Takeaway
Latest: Kafka 4.4.0 (22 Oct 2026), RabbitMQ 4.4.0 (19 Oct 2026), NATS Server 2.15.2 (26 Oct 2026), Redpanda 26.2.2 (22 Aug 2026; GitHub releases ended). For a mid-size SaaS the pragmatic choices are RabbitMQ or NATS JetStream for simple eventing; Kafka (KRaft, 4.x) if durable event log/replay/CDC is required.

### Cited Findings
- Kafka 4.4.0 released 22 Oct 2026; 4.3.2 (page date 23 Oct), 4.2.2 (29 Sep 2026); earlier 4.3.0 (22 May 2026), 4.2.0 (17 Feb 2026), 4.1.0 (2 Sep 2025), 4.0.0 (18 Mar 2025). — [Apache Kafka downloads](https://kafka.apache.org/community/downloads/) (page has some out-of-order dates)
- RabbitMQ 4.4.0 released 19 Oct 2026 (breaking: removed parsing of certain AMQP 0-9-1 headers, stricter OIDC discovery validation); 4.3.7 on 20 Oct; 4.2.x still patched; all require Erlang/OTP 27+. — [RabbitMQ releases](https://github.com/rabbitmq/rabbitmq-server/releases)
- NATS Server v2.15.2 (26 Oct 2026) latest stable; 2.15.3-RC.1 and 2.14.10-RC.1 on 2 Nov. — [nats-server releases](https://github.com/nats-io/nats-server/releases)
- Redpanda v26.2.2 (22 Aug 2026) is marked "the last Redpanda release published on GitHub"; 26.1.17 and 25.3.17 (20 Aug); 26.1.16/25.3.16 marked "do not install" due to a critical bug. Licence not stated on the page (commonly BSL/Redpanda community licence — UNVERIFIED). — [Redpanda releases](https://github.com/redpanda-data/redpanda/releases)

### Inferences
- Redpanda moving release notes off GitHub and its source-available licence make it less attractive than Apache-2.0 Kafka for a self-hosted SaaS; NATS (Apache-2.0, small footprint) and RabbitMQ (MPL-2.0) are cheaper to run for a small team. Licences for Kafka (Apache-2.0), NATS (Apache-2.0), RabbitMQ (MPL-2.0/Apache) are from background knowledge, not fetched.
- Healthcare event needs (audit trail, replay to rebuild read models, per-tenant topics) favour Kafka only if team can run it; otherwise RabbitMQ streams or NATS JetStream.

### Gaps
- Licences for the four brokers were not verified from fetched sources. No performance/cost comparison done.

## 5. Kubernetes, lightweight distros, Postgres operators, MariaDB operator

### Takeaway
Kubernetes 1.37 (v1.37.0 on 26 Aug 2026; 1.37.1 on 15 Sep) is current, 1.36/1.35 still supported. CloudNativePG 1.30.1 (23 Sep 2026) is the most active, Apache-licensed K8s-native Postgres operator; k3s/RKE2 1.37.1 (30 Sep) and Talos 1.14.5 (26 Oct) all track 1.37.

### Cited Findings
- Supported minors: 1.37.1 (EOL 2027-10-28), 1.36.5 (EOL 2027-06-28), 1.35.9 (EOL 2027-02-28), 1.34.12 (EOL 2026-10-27, i.e. just expired); patch dates 2026-09-15. — [kubernetes.io/releases](https://kubernetes.io/releases/)
- v1.37.0 released 26 Aug 2026. — [GitHub tag v1.37.0](https://github.com/kubernetes/kubernetes/releases/tag/v1.37.0)
- k3s: latest stable v1.37.1+k3s1 (30 Sep 2026); 1.37.2-rc2 and 1.36.6-rc2 on 30 Oct. — [k3s releases](https://github.com/k3s-io/k3s/releases)
- RKE2: latest v1.37.1+rke2r1 (30 Sep 2026); RC builds for 1.37.2/1.36.6/1.35.10 on 29 Oct; the RC notes show both ingress-nginx (chart 4.15.114, image v1.15.1-prime17) and Traefik (v3.7.15/16) as bundled components. — [RKE2 releases](https://github.com/rancher/rke2/releases)
- Talos: v1.14.5 (26 Oct 2026) stable, ships Kubernetes v1.37.1; v1.15.0-alpha.2 (27 Oct); v1.13.12 (21 Oct). — [Talos releases](https://github.com/siderolabs/talos/releases)
- CloudNativePG: v1.30.1 (23 Sep 2026, default PG image 18.6) latest; v1.30.0 (29 Jun 2026, default PG 18.4); v1.29.3 (23 Sep) final 1.29.x, 1.29 no longer supported; mentions in-place major upgrades to PG 19+ with Image Volume extensions. — [CloudNativePG releases](https://github.com/cloudnative-pg/cloudnative-pg/releases)
- Zalando Postgres Operator v2.0.3 (2 Oct 2026); v2.0.2 (20 Aug); v2.0.1 (29 Jul). — [zalando/postgres-operator releases](https://github.com/zalando/postgres-operator/releases)
- Crunchy PGO v6.0.2 (2 Jun 2026), v5.8.8 (2 Jun), v6.0.1 (26 Feb). — [CrunchyData/postgres-operator releases](https://github.com/CrunchyData/postgres-operator/releases)
- Patroni v4.1.5 (12 Aug 2026, latest), v4.0.11 (12 Aug), v4.1.4 (7 Jul). — [Patroni releases](https://github.com/patroni/patroni/releases)
- MariaDB operator: 26.10.2 (24 Oct 2026); 26.10.0 added restorable logical backups for Galera and changed Galera bootstrap-source selection (highest valid sequence number; `forceClusterBootstrapInPod` needed when all members empty); MaxScale failover hardening. — [mariadb-operator releases](https://github.com/mariadb-operator/mariadb-operator/releases)

### Inferences
- CloudNativePG (Postgres 18 default; active cadence) is the best fit for per-tenant or shared Postgres with built-in barman-based backup/PITR; Zalando (Patroni-based, v2.0.x) is a credible second; Crunchy PGO has had slower 2026 releases (last 2 Jun) so check its container image licence/terms (Crunchy images historically under developer-programme terms — background knowledge, UNVERIFIED).
- Single-node/small-cluster options: k3s for simplicity; Talos for immutable, hardened nodes (good for audit); RKE2 for CIS-hardened/FIPS-style compliance. All track 1.37.

### Gaps
- CloudNativePG licence (Apache-2.0) and CNCF Sandbox status not shown on fetched pages; supported PG versions matrix not read.
- MariaDB operator licence not stated on fetched page.
- k3s/RKE2 dates of 1.37.0 initial releases not captured.

## 6. Object storage: MinIO status and alternatives

### Takeaway
MinIO community edition is effectively dead: source-only, no binaries, and the GitHub repo is archived and "no longer maintained" (AGPLv3 source remains). Treat MinIO CE as unusable for a new commercial SaaS; use Ceph RGW (Rook), SeaweedFS, or Garage, or a managed Indian-region S3.

### Cited Findings
- GitHub minio/minio: "archived by the owner on Apr 25, 2026"; README says "THIS REPOSITORY IS NO LONGER MAINTAINED"; community edition "now distributed as source code only", no pre-compiled binary releases, legacy binaries get no updates, recommends commercial AIStor; source AGPLv3, docs CC BY 4.0; Releases section empty. — [github.com/minio/minio](https://github.com/minio/minio)
- Timeline (third-party, Pigsty — author maintains a competing fork `pgsty/minio`): licence moved Apache-2.0 -> AGPLv3 in May 2021; full admin console removed from community edition May 2025; prebuilt binaries and Docker images discontinued Oct 2025; maintenance mode announced 3 Dec 2025; status changed to "no longer maintained" and archived 12 Feb 2026 (vs. 25 Apr 2026 per GitHub banner — CONFLICT on date; GitHub page is the primary source); community fork pgsty/minio announced 14 Feb 2026 with console restored. — [Vonng "MinIO is dead"](https://blog.vonng.com/en/db/minio-resurrect/); [ayedo.de](https://ayedo.de/en/posts/minio-im-maintenance-mode/); [RAGFlow issue](https://github.com/infiniflow/ragflow/issues/13840)
- SeaweedFS latest 4.52 (2 Nov 2026), 4.51/4.50 (25 Oct). — [SeaweedFS releases](https://github.com/seaweedfs/seaweedfs/releases)
- Rook v1.21.1 (26 Oct 2026); v1.21.0 notes say Ceph "Umbrella" (v21) supported once Ceph v21.2.0 releases. — [Rook releases](https://github.com/rook/rook/releases)
- Garage: release page not retrievable (Forgejo behind Anubis); website lists Docker Hub images only. — [Garage download](https://garagehq.deuxfleurs.fr/download/)

### Inferences
- Licences from background knowledge (UNVERIFIED here): SeaweedFS Apache-2.0; Garage AGPLv3; Ceph LGPL 2.1/3; Rook Apache-2.0. AGPL matters if you modify and expose the service; for internal infrastructure it is generally fine but needs legal review.
- Ceph/Rook is heavy for a small team (needs >=3 nodes with dedicated disks); SeaweedFS is simpler with S3 API but weaker enterprise tooling; Garage is light but lacks some S3 features (versioning/object-lock gaps — background knowledge). For compliance (immutability/WORM for medical records) check S3 Object Lock support per option.

### Gaps
- Garage version/date/licence and SeaweedFS licence not retrieved. S3 feature parity (Object Lock, SSE, versioning) not researched.

## 7. Ingress and certificates: Traefik, ingress-nginx, Gateway API, cert-manager

### Takeaway
ingress-nginx was retired in March 2026 (no further releases or security patches); migrate to Gateway API (v1.7.0, 29 Oct 2026) or an ingress controller such as Traefik (v3.7.17, 30 Oct 2026), which ships ingress-nginx annotation compatibility. cert-manager is at v1.21.3.

### Cited Findings
- Kubernetes announced Ingress NGINX retirement (Nov 2025 blog); Steering/Security Response Committees statement of 29 Jan 2026 urged immediate migration; after retirement there are no releases, bug fixes or security patches; Datadog research estimated ~50% of cloud-native environments used it; alternatives are not drop-in. — [Kubernetes blog Nov 2025](https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/); [Steering statement Jan 2026](https://www.kubernetes.io/blog/2026/01/29/ingress-nginx-statement/)
- Rancher docs: upstream maintenance ended March 2026; recommends Traefik as migration target; patched images after that date restricted to commercial customers. — [Rancher guide](https://ranchermanager.docs.rancher.com/how-to-guides/new-user-guides/kubernetes-resources-setup/load-balancer-and-ingress-controller/guide-to-ingress-nginx-retirement)
- Traefik v3.7.17 (30 Oct 2026) latest stable (security fix GHSA-xv5x-242w-7hf7); v3.7.16 (20 Oct, security advisory GHSA-hrpp-p4fc-wwf8); v3.8.0-rc.1 pre-release 30 Oct; v2.11.61 also patched; ingress-nginx provider supports many annotations (auth-url, auth-tls, cors, sticky cookies, ssl-passthrough, rewrite in config snippet). — [Traefik releases](https://github.com/traefik/traefik/releases)
- Gateway API v1.7.0 (29 Oct 2026) latest stable; v1.6.3 (6 Oct). — [gateway-api releases](https://github.com/kubernetes-sigs/gateway-api/releases)
- cert-manager v1.21.3 (23 Oct 2026) latest; v1.20.5 (28 Oct) maintenance patch. — [cert-manager releases](https://github.com/cert-manager/cert-manager/releases)

### Inferences
- Do not deploy new clusters with ingress-nginx. Traefik v3 with Gateway API (or Ingress with the nginx-compat provider) is the lowest-friction path, particularly as k3s bundles Traefik. Note RKE2 still bundles ingress-nginx in RC builds as of 29 Oct 2026 — check before relying on its default.
- Two Traefik security advisories in October 2026 imply keeping it on a fast patch cadence.

### Gaps
- Licences (Traefik MIT, cert-manager Apache-2.0, Gateway API Apache-2.0) from background knowledge, not fetched. Whether k3s/RKE2 default ingress changed in 1.37 not confirmed.

## 8. Backup and observability: pgBackRest, Barman, restic, Velero; Prometheus, Grafana, Loki, OpenTelemetry

### Takeaway
All current: pgBackRest 2.60.0 (18 Oct 2026), Barman 3.20.1 (29 Sep), restic 0.19.1 (5 Jul), Velero 1.18.4 (28 Sep); Prometheus 3.15.1, Grafana 13.3.0, Loki 3.7.9, OTel Collector 0.164.0. Grafana and Loki are AGPLv3 (background; not fetched this session).

### Cited Findings
- pgBackRest v2.60.0 (18 Oct 2026; adds `checksum-page-error` option, GCS Workload Identity Federation, object store prefetch); v2.59.3 (4 Oct; fixes weak encryption subkey/salt generation bug); v2.59.2 (27 Sep; PostgreSQL 19beta4 support). — [pgBackRest releases](https://github.com/pgbackrest/pgbackrest/releases)
- Barman 3.20.1 (29 Sep 2026), 3.20.0 (27 Aug), 3.19.1 (27 May). — [Barman releases](https://github.com/EnterpriseDB/barman/releases)
- restic 0.19.1 (5 Jul 2026), 0.19.0 (9 Jun 2026), 0.18.1 (21 Sep 2025). — [restic releases](https://github.com/restic/restic/releases)
- Velero v1.18.4 (28 Sep 2026), 1.18.3 (21 Sep). — [Velero releases](https://github.com/vmware-tanzu/velero/releases)
- Prometheus 3.15.1 (21 Oct 2026) latest stable; 3.16.0-rc.2 (27 Oct); 3.13.5 same day as 3.15.1 (LTS line not identified). — [Prometheus releases](https://github.com/prometheus/prometheus/releases)
- Grafana 13.3.0 (27 Oct 2026); 13.2.5, 13.1.9 same day. — [Grafana releases](https://github.com/grafana/grafana/releases)
- Loki v3.7.9 (20 Oct 2026); v3.6.19 (20 Oct). — [Loki releases](https://github.com/grafana/loki/releases)
- OpenTelemetry Collector (releases repo) v0.164.0 (26 Oct 2026), v0.163.2 (21 Oct). — [otel collector releases](https://github.com/open-telemetry/opentelemetry-collector-releases/releases)

### Inferences
- CloudNativePG's native backup uses Barman Cloud; pgBackRest is the other common choice (supported by Crunchy and Zalando-ecosystem setups, and via CNPG plugin). Use pgBackRest or Barman for Postgres PITR; Velero for cluster resources/PVs; restic for file-level.
- Backups for PHI must stay in India and be encrypted; both pgBackRest and Barman support encryption and S3-compatible targets.
- Licence of Grafana/Loki/Tempo (AGPLv3 since 2021), Prometheus/OTel (Apache-2.0) from background knowledge: running AGPL Grafana unmodified as an internal tool is generally low risk; do not modify and expose without review. UNVERIFIED in this session.

### Gaps
- Licence pages not fetched for any of these. Prometheus LTS designation unknown.

## 9. India hosting and regulation: cloud regions, Indian clouds, MeitY empanelment, CERT-In 2022, DPDP, ABDM localisation

### Takeaway
CERT-In's 2022 directions (primary text read) require 6-hour incident reporting, NTP sync to NIC/NPL, and 180-day rolling logs kept within Indian jurisdiction. DPDP Rules 2025 (notified 13 Nov 2025) phase in with the main obligations and the cross-border rule on 13 May 2027. ABDM's HDM Policy reportedly says personal data must not be stored outside India; hyperscalers have multiple Indian regions, and E2E and Yotta are MeitY-empanelled.

### Cited Findings
- CERT-In Directions under s.70B(6) dated 28 Apr 2022 (primary PDF): (i) connect to NTP servers of NIC or NPL (or traceable to them) to synchronise all ICT system clocks; multi-geography entities may use other accurate time sources provided they do not deviate from NPL/NIC; (ii) report listed cyber incidents (Annexure I) within 6 hours of noticing, via incident@cert-in.org.in / 1800-11-4949; (iii) designate a Point of Contact; (iv) enable logs of all ICT systems and keep them securely for a rolling 180 days within Indian jurisdiction; (v) data centres, VPS, cloud and VPN providers must keep subscriber registration info (validated names, etc.) for 5 years or longer after cancellation. — [CERT-In Directions PDF](https://www.cert-in.org.in/PDF/CERT-In_Directions_70B_28.04.2022.pdf)
- Effective 27 Jun 2022 (60 days after issue). — [Mondaq summary](https://www.mondaq.com/india/it-and-internet/1202576/everything-you-need-to-know-about-indias-new-guidelines-related-to-cyber-incident-reporting-by-cert-in)
- DPDP Rules 2025 notified 13 Nov 2025 (some sources say 14 Nov): Rules 1, 2, 17–21 effective immediately (incl. Data Protection Board); Rule 4 (consent managers) from 13 Nov 2026; Rules 3, 5–16, 22, 23 from 13 May 2027, including breach notification, data retention (one-year minimum log retention) and Rule 15 cross-border transfers; Rule 13(4) bars Significant Data Fiduciaries from transferring certain data outside India; sectoral localisation rules continue to apply. A Sept 2026 law-firm note says s.16/Rule 15 had not yet commenced. — [Hogan Lovells/HLC](https://www.hlc.com/en/publications/indias-digital-personal-data-protection-act-2023-brought-into-force-); [Mondaq](https://www.mondaq.com/india/data-protection/1708164/digital-personal-data-protection-rules-2025-notified); [SCC Online](https://www.scconline.com/blog/post/2025/12/26/digital-personal-data-protection-rules-2025-key-highlights/)
- ABDM Health Data Management Policy (revised April 2022): MediaNama quotes the clause "No personal data shall be stored beyond the geographical boundaries of India, subject always to the provision of applicable laws"; policy describes storage at central, state/UT and health-facility levels. A different snippet from the same outlet suggested localisation language had been removed — CONFLICTING secondary reports; primary PDF (abdm.gov.in) was unreachable (connection refused). — [MediaNama](https://www.medianama.com/2022/06/223-new-health-data-management-policy/)
- MeitY empanelment itself requires services hosted in India with data limited to India (IBM compliance page); NHA onboarded a MeitY-empanelled Tier-3 CSP for ABDM IT systems. — [IBM MeitY page](https://www.ibm.com/products/cloud/compliance/meity); [SFLC document](https://cdn.sflc.in/documents/AU580.pdf)
- Regions: AWS Mumbai (ap-south-1, since 2016) and Hyderabad (ap-south-2); Azure Central India (Pune), South India (Chennai), West India (Mumbai, per one source) and new India South Central (Hyderabad), announced GA 28 Jul 2026 as Microsoft's fourth India region (one outlet gives 6 Aug 2026; availability-zone status not confirmed); GCP Mumbai (asia-south1) and Delhi (asia-south2). — [Azure message centre](https://mc.merill.net/message/AZ568013); [Opsio](https://opsiocloud.com/in/knowledge-base/aws-vs-azure-vs-gcp/); [PDP Spectra](https://pdpspectra.com/blog/india-cloud-adoption-aws-azure-gcp-2026)
- MeitY empanelment: AWS Asia-Pacific (Hyderabad) empanelled (headline only); Yotta and E2E Networks reported empanelled (E2E coverage 2024; later NSE filing about AI-services empanelment); a Sept 2026 current-affairs piece says 30 CSPs empanelled incl. Jio, TCS, BSNL, RailTel, Yotta (non-official). — [Yotta](https://channeldrive.in/data-center/yotta-gets-empanelled-with-meity-as-accredited-csp/); [E2E](https://cxotoday.com/media-coverage/e2e-networks-receives-cloud-service-provider-empanelment-from-meity/); [Indian Infrastructure](https://indianinfrastructure.com/?p=148378); [Drishti IAS](https://www.drishtiias.com/daily-updates/daily-news-analysis/cloud-ecosystem-in-india)
- CtrlS: widely described as Tier IV data centres (Hyderabad, Mumbai, Noida, Bangalore); MeitY status only claimed by reseller/blog pages; Netmagic (NTT): no MeitY empanelment found. — [datacentres.com](https://www.datacentres.com/providers/ctrls); [Cyfuture](https://cyfuture.cloud/blog/top-10-server-colocation-providers-to-check-out-in-2025/)

### Inferences
- Design to keep all PHI, backups, logs (180-day minimum, ideally longer), and observability data in Indian regions, with NTP pointed at NIC/NPL (time.nplindia.org / samay1.nic.in — hostnames from background knowledge), and an incident-reporting runbook with a 6-hour clock. This satisfies CERT-In regardless of ABDM interpretation; treating ABDM's "not beyond India" as binding is the conservative reading.
- Cross-border (DPDP Rule 15) is not yet in force until 13 May 2027, but plan for it; SDF designation is plausible for large hospital-data processors.
- Using a hyperscaler Indian region + Indian-owned DR region (e.g. Mumbai + Hyderabad) gives in-country DR. MeitY empanelment is mandatory mainly for government (NHA, state) clients; private hospitals do not strictly need it, but it is a procurement credential.
- Using an Indian cloud (E2E, Yotta) aligns with sovereignty pitch and GPU/AI use; lower managed-service breadth than hyperscalers.

### Gaps
- Official MeitY empanelment list not retrieved (so CtrlS/Netmagic/E2E current status unverified); AWS Hyderabad empanelment date unconfirmed.
- Final ABDM HDM policy primary text not read; no confirmation of exact current clause numbering or whether localisation survived in the final version; no sector regulator (e.g. Telemedicine/ICMR/clinical establishment rules) analysis.
- Whether health data is "sensitive" under DPDP (the Act has no separate sensitive category) — not researched. Official CERT-In text confirms 180-day logs; no confirmation of any later amendment/FAQ changes.

## 10. Dev tooling: Docker, Compose, Helm, Tilt, Skaffold, local dev on a single Ubuntu workstation

### Takeaway
Docker Engine 29.9.2 (20 Oct 2026), Compose v5.7.0 (2 Nov 2026), Helm v4.3.1 (15 Oct 2026; v3.22.1 also current), Tilt v0.37.8 (1 Oct 2026), Skaffold v2.25.0 (16 Sep 2026). A single Ubuntu workstation can run Compose for fast loops and k3s/kind with Tilt for Kubernetes parity.

### Cited Findings
- Docker Engine 29.9.2 released 20 Oct 2026 (Engine 29 series). — [Docker Engine release notes](https://docs.docker.com/engine/release-notes/)
- Docker Compose v5.7.0 (2 Nov 2026), v5.6.1 (21 Oct), v5.6.0 (2 Oct). — [docker/compose releases](https://github.com/docker/compose/releases)
- Helm v4.3.1 (15 Oct 2026), v3.22.1 (15 Oct), v4.3.0 (9 Sep), v4.2.4 (13 Aug). — [helm releases](https://github.com/helm/helm/releases)
- Tilt v0.37.8 (1 Oct 2026); release notes mention updating deps to Kubernetes 1.37 and Go 1.27; project active (495 open issues). — [tilt releases](https://github.com/tilt-dev/tilt/releases)
- Skaffold v2.25.0 (16 Sep 2026), v2.24.0 (23 Jul), v2.23.0 (26 Jun). — [skaffold releases](https://github.com/GoogleContainerTools/skaffold/releases)

### Inferences
- Recommended single-workstation approach: Docker Engine + Compose for infra dependencies (Postgres, Keycloak, broker, OIE, HAPI) for day-to-day coding; k3s (or kind/k3d) plus Tilt (live-update, Helm/Kustomize) when testing operators (CNPG, cert-manager, Gateway API) and Helm charts. Skaffold is a reasonable alternative if CI/CD deploys via the same skaffold.yaml. Helm 4 is current but many charts still target v3; test with both.
- Resource planning for Keycloak + Postgres + OIE + HAPI + broker on one workstation: needs roughly 16–32 GB RAM (estimate, not sourced).

### Gaps
- No evidence gathered on Tilt's ownership/future after Docker's acquisition (not confirmed from fetched page); no benchmarks of kind vs k3d vs minikube; Docker Compose/Engine licensing not checked (Docker Desktop terms vs Engine on Ubuntu).
