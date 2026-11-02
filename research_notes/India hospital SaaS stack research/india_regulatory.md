# India Regulatory, Digital-Health and Accreditation Obligations for a Commercial Hospital Information System SaaS Vendor (as of November 2026)

Research date: 2 November 2026. Scope: India only. Where a source is a vendor blog or other secondary source, it is marked. Drafts are marked separately from notified (in-force) instruments.

---

## 1. ABDM (Ayushman Bharat Digital Mission): APIs, sandbox, milestones, registries, ABHA, Scan & Share, consent, encryption, FHIR, DHIS, open-source helpers

### Takeaway
ABDM integration through the M1 (ABHA), M2 (HIP) and M3 (HIU) milestones on the **v3 APIs** is the main digital-health requirement for an Indian HIS. In practice it is a commercial requirement, not a legal mandate on private hospitals. NHA's own incentive scheme (DHIS Corrigendum 7, 9 Apr 2026) now pays only for **KYC-verified-ABHA-linked records on v3 APIs** and needs **M3 for hospitals and clinics** (M2 is enough for labs and pharmacies). NABH's HIS/EMR certification also requires a valid **ABDM M3 compliance certificate**. Treat M1+M2+M3 on v3 as table stakes.

### Cited Findings
**Scale and status (official, July 2026)**
- PIB backgrounder dated 6 July 2026: in May 2026, over 100 crore health records were linked to more than 90 crore ABHA accounts. Over 23.21 crore ABHA-linked Scan & Share tokens had been issued as of 18 June 2026. An IIHMR study found Scan & Share cut OPD wait times from about 1 hour to 2–5 minutes. — [PIB backgrounder "ABDM: India's Digital Health Backbone", 6 Jul 2026](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/jul/doc202676912801.pdf)
- Aarogya Setu 2.0 launched on 29 June 2026 as the citizen PHR app under ABDM. It covers ABHA creation, "Scan & Register", NHCX-linked insurance and PM-JAY wallet views, eRaktKosh blood availability, and UHI appointment booking. — [PIB backgrounder, 6 Jul 2026](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/jul/doc202676912801.pdf)
- DHIS paid out (as of 18 June 2026): hospitals Rs 107+ crore, diagnostics/labs/pharmacies Rs 2.95 crore, Digital Solution Companies Rs 26+ crore. — [PIB backgrounder, 6 Jul 2026](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/jul/doc202676912801.pdf)
- ABDM building blocks named officially: ABHA number (14-digit), Healthcare Professionals Registry (HPR), Health Facility Registry (HFR), Unified Health Interface (UHI), and NHCX for insurance. — [PIB backgrounder, 6 Jul 2026](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/jul/doc202676912801.pdf)

**Milestones and sandbox onboarding (mostly vendor or secondary sources; verify on sandbox.abdm.gov.in)**
- M1 covers creating and verifying ABHA by Aadhaar OTP or mobile OTP, showing the ABHA card and QR, Scan & Share at registration, and registering each facility's HFR ID as its HIP ID. M2 (HIP) covers linking care contexts to ABHA, discovery and link callbacks, storing consent artefacts, and pushing encrypted FHIR R4 bundles on request. M3 (HIU) covers raising consent requests, fetching and decrypting records, and purging them on consent revoke or expiry. — [Nirmitee (vendor blog), updated Sept/Oct 2026](https://nirmitee.io/blog/abdm-integration-milestones-m1-m2-m3-m4-multi-software-guide/)
- "M4" is not an ABDM milestone. The term is industry shorthand for NHCX claims integration (coverage eligibility, pre-auth, claim, payment notice), which needs M1 in production first. — [Nirmitee (vendor)](https://nirmitee.io/blog/abdm-integration-milestones-m1-m2-m3-m4-multi-software-guide/)
- Sandbox steps reported by a vendor:
  1. Register on the sandbox and obtain a client ID and secret.
  2. Get a gateway session token. Sandbox calls use header `X-CM-ID: sbx`.
  3. Register the bridge (callback) URL with `PATCH /api/hiecm/gateway/v3/bridge/url`.
  4. Build at least 2 milestones. NHA support reportedly said "any 2 milestones to be completed to exit sandbox".
  5. Pass NHA functional test cases.
  6. Pass a WASA (web application security audit) by an empanelled auditor.
  7. Complete the demo and exit form.
  8. Receive production credentials (`X-CM-ID: abdm`, apis.abdm.gov.in) and configure HFR per facility.
  — [Nirmitee (vendor)](https://nirmitee.io/blog/abdm-integration-milestones-m1-m2-m3-m4-multi-software-guide/). The "2 milestones" exit rule conflicts with another vendor guide, which found no stated minimum. — [ABDM search summary: verticomply / codingclave vendor guides](https://verticomply.com/blog/abdm-m1-m2-m3-integration-guide)
- v3 is the current API generation, and v0.5 and v1 are legacy. One vendor says NHA incentive rules require v3 for M1–M3 from July 2026. — [Nirmitee v3 migration guide (vendor)](https://nirmitee.io/blog/abdm-v3-api-migration-guide-everything-that-changed/); [adrine.in (vendor)](https://www.adrine.in/blog/abdm-fhir-integration-guide-2026). The official DHIS Corrigendum 7 lists "Improve interoperability and compliance (v3 APIs)" as an objective. — [DHIS Corrigendum 7, NHA, 9 Apr 2026](https://rajswasthya.rajasthan.gov.in/admin/upload/letter/2026/05/40%20Dt.05.05.2026%20NHM%20CSR%20NDHM%20(ABDM)-01779-PART-14.pdf)
- A public Postman collection (March 2026) lists most M1/M2 v3 endpoints as working in sandbox. The M3 health-information request endpoint returned 403 at that time. This is a third-party snapshot. — [Nirmitee abdm-v3-postman-collection (GitHub)](https://github.com/Nirmitee-tech/abdm-v3-postman-collection)

**Scan & Share**
- The patient scans the facility QR (tied to its HFR/HIP ID). The ABHA profile arrives at the HMS bridge URL. The HMS registers or matches the patient and returns a token via `/patient-share/v3/on-share`. — [Nirmitee (vendor)](https://nirmitee.io/blog/abdm-integration-milestones-m1-m2-m3-m4-multi-software-guide/). The official description says a digital queue token is generated, with demographics retrieved from ABHA and integrated into the facility's HIS. — [PIB, 6 Jul 2026](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/jul/doc202676912801.pdf)

**Fidelius encryption**
- Fidelius uses ECDH key agreement, HKDF-SHA256 key derivation and AES-256-GCM. The keys are Curve25519 in Weierstrass form (not X25519 Montgomery), and this is required even in sandbox. M3 needs a fresh key pair and nonce per request. — [abdm_fidelius Dart package docs](https://pub.dev/documentation/abdm_fidelius/0.1.0/); [Nirmitee Fidelius Node guide (vendor)](https://nirmitee.io/blog/abdm-fidelius-encryption-nodejs-implementation-guide). The reference implementation with test vectors is [mgrmtech/fidelius-cli](https://github.com/mgrmtech/fidelius-cli).
- The HIP pushes the encrypted bundles to the HIU's `dataPushUrl`, then notifies the gateway via `/data-flow/v3/health-information/notify` with status TRANSFERRED. — [Nirmitee M3 reference architecture (vendor)](https://nirmitee.io/blog/building-abdm-hiu-from-scratch-m3-flow-reference-architecture/)

**FHIR profiles (NRCeS)**
- The current published IG is **"FHIR Implementation Guide for ABDM v6.5.0"** (build 9 May 2025, FHIR 4.0.1). It includes NHCX profiles and refers to the EHR Standards for India (2016) code systems. No 2026 IG release was found. — [NRCeS IG v6.5.0](https://nrces.in/ndhm/fhir/r4/); [NHCX profiles page](https://www.nrces.in/ndhm/fhir/r4/hcx-profile.html)
- HI types and bundle types: OPConsultRecord/OPConsultNote, Prescription, DiagnosticReport, DischargeSummary, ImmunizationRecord, WellnessRecord, HealthDocumentRecord, and Invoice. Coding is SNOMED CT, LOINC and ICD-10. — [Nirmitee (vendor)](https://nirmitee.io/blog/abdm-integration-milestones-m1-m2-m3-m4-multi-software-guide/); [DHIS Corrigendum 7 (official, lists HI types incl. Invoices)](https://rajswasthya.rajasthan.gov.in/admin/upload/letter/2026/05/40%20Dt.05.05.2026%20NHM%20CSR%20NDHM%20(ABDM)-01779-PART-14.pdf)

**DHIS: Digital Health Incentive Scheme (official primary text)**
- Ref S-12021/070/2022-ABDM(Coord), **Corrigendum 7 dated 9 April 2026**. It is effective **April 2026 to September 2026**, "subject to the availability of funds". The scheme launched on 1 Jan 2023. Earlier corrigenda were dated 16 Mar 2023, 29 Jul 2023, 13 Dec 2023, 11 Jun 2024, 19 Feb 2025 and 20 Nov 2025. — [DHIS Corrigendum 7](https://rajswasthya.rajasthan.gov.in/admin/upload/letter/2026/05/40%20Dt.05.05.2026%20NHM%20CSR%20NDHM%20(ABDM)-01779-PART-14.pdf)
- The incentives in Corrigendum 7 are:
  - **Eligibility:** only records linked to **KYC-verified ABHA addresses** count. There is no incentive for Scan & Share or health-locker record linking.
  - **Threshold:** more than 100 eligible transactions per facility per month (the first 100 are deducted).
  - **Category 1 (DiagnosticReport or DischargeSummary):** Rs 10 to the facility and Rs 5 to the DSC.
  - **Category 2 (other HI types: Prescription, OPConsult, Immunization, HealthDocument, Wellness, Invoice):** Rs 5 to the facility and Rs 2.5 to the DSC.
  - **Consent-based sharing:** Rs 10 to the HIU and Rs 5 to each HIP source. Auto-consent is not eligible, and there is no payment if HIP and HIU are the same entity.
  - **UHI:** Rs 5 each to the EUA and the HSPA.
  - **Scan & Pay:** Rs 5 to the facility and Rs 1 to the DSC.
  - **AB PM-JAY claims through NHCX in FHIR:** Rs 200 or 10% of the claim to the facility, and Rs 10 per claim to the DSC, capped at Rs 1 crore.
  - **Per-ABHA cap:** 1 ABHA per day and 5 per month.
  - **DSC eligibility:** 10 facilities linking records per month.
  - **Back-claims:** limited to the last 3 months.
  - **Integration level:** hospitals and clinics must be integrated **up to M3**, while labs and pharmacies need M2.
  — [DHIS Corrigendum 7](https://rajswasthya.rajasthan.gov.in/admin/upload/letter/2026/05/40%20Dt.05.05.2026%20NHM%20CSR%20NDHM%20(ABDM)-01779-PART-14.pdf)

**Open-source helpers**
- The NHA-ABDM/ABDM-wrapper exists, and its wiki history shows edits as of June 2024. A third-party blog claims it "now exclusively uses V3 endpoints", which is unverified. — [NHA-ABDM/ABDM-wrapper](https://github.com/NHA-ABDM/ABDM-wrapper); [Nirmitee v3 migration guide](https://nirmitee.io/blog/abdm-v3-api-migration-guide-everything-that-changed)

### Inferences
- Software checklist (ABDM):
  - ABHA create and verify, via Aadhaar OTP and mobile OTP, plus ABHA address. Capture **KYC status**, because only KYC-verified ABHA records earn DHIS.
  - ABHA card and QR display, plus print.
  - Facility counter QR and Scan & Share token queue mapped to the HFR ID.
  - Care-context linking for every OP visit, IP admission, lab report and invoice.
  - HIP discovery, link and data-push, and HIU consent request and fetch.
  - Consent artefact store with expiry and revoke purge.
  - Fidelius encryption (Weierstrass Curve25519).
  - FHIR R4 bundle generation per NRCeS IG v6.5.0 for all HI types.
  - Per-tenant HFR/HIP configuration and a per-tenant bridge URL. A multi-tenant SaaS needs one bridge with facility routing.
  - HPR ID capture for doctors on prescriptions and records.
  - A DHIS claims and reporting export.
  - Consent-success notification API, which DHIS requires DSCs to integrate.
- Vendor (company) checklist: sandbox registration as an integrator, at least 2 milestones (aim for all 3), NHA functional test, a WASA audit by an empanelled (likely CERT-In) auditor, demo, and the production exit form. Register as a DSC for DHIS. Help each customer register in HFR and link its doctors' HPR IDs.
- DHIS has an end date of September 2026 in the latest corrigendum found. A November 2026 renewal (Corrigendum 8) may exist but was not found.

### Gaps
- No official NHA page was fetched for the current sandbox exit criteria, the WASA auditor list or scope, or the v0.5 sunset date. Vendor claims could not be verified against sandbox.abdm.gov.in, which is a JS-rendered portal.
- HPR and HFR API specifics (HPR ID verification on e-prescriptions, facility onboarding APIs for bulk HFR registration by an integrator) were not researched in depth.
- DHIS status after 30 Sep 2026 is unknown, with no Corrigendum 8 found.
- The current activity of ohcnetwork/care_abdm and the ABDM-wrapper could not be checked because the GitHub API was rate-limited.
- No primary statement was found that ABDM integration is legally mandatory for private hospitals. It is incentivised and is a precondition for NABH DHS certification and PM-JAY/NHCX flows.

---

## 2. NHCX (National Health Claims Exchange)

### Takeaway
NHCX is NHA's claims-exchange gateway, built with IRDAI and using FHIR claim profiles in the NRCeS IG. Onboarding provisions exist in IRDAI's 2024 health master circular. A universal mandate was **recommended by an IRDAI panel** but no 2026 notified circular making it mandatory was found. PM-JAY claims through NHCX are incentivised under DHIS.

### Cited Findings
- NHCX was developed by NHA in consultation with IRDAI under ABDM. — [Wikipedia: NHCX](https://en.wikipedia.org/wiki/National_Health_Claims_Exchange); [PIB, 6 Jul 2026](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/jul/doc202676912801.pdf)
- IRDAI's **Master Circular on IRDAI (Insurance Products) Regulations 2024 – Health Insurance** contains provisions on onboarding hospitals and providers onto NHCX. An IRDAI panel later recommended universal NHCX integration and incentives. This is a recommendation, not a circular. — [TaxGuru on IRDAI panel](https://taxguru.in/corporate-law/irdai-health-insurance-panel-pushes-simpler-policies-faster-claims-nhcx-adoption.html)
- As of July 2024 (dated), 34 insurers and TPAs and over 300 hospitals were live on NHCX. Adoption is described as uneven. — [InterPixels (secondary)](https://interpixels.ai/insights/insights-irdai-health-insurance-claims-processing-india-tpa/); [Business Standard editorial](https://www.business-standard.com/opinion/editorial/high-claims-low-payouts-why-india-s-health-insurance-cover-isn-t-enough-125111901436_1.html)
- NHCX FHIR profiles (CoverageEligibilityRequest/Response, Claim, ClaimResponse and others) are published in NRCeS IG v6.5.0. — [NRCeS NHCX profiles](https://www.nrces.in/ndhm/fhir/r4/hcx-profile.html); [NRCeS FHIR adoption guide for ABDM and NHCX](https://www.nrces.in/download/files/pdf/Implementation_Guide_for_Adoption_of_FHIR_in_ABDM_and_NHCX.pdf)
- The DHIS incentive for AB PM-JAY claims linked to ABHA and filed in FHIR through NHCX is Rs 200 or 10% to the facility and Rs 10 per claim to the DSC, capped at Rs 1 crore. — [DHIS Corrigendum 7](https://rajswasthya.rajasthan.gov.in/admin/upload/letter/2026/05/40%20Dt.05.05.2026%20NHM%20CSR%20NDHM%20(ABDM)-01779-PART-14.pdf)

### Inferences
- Build a TPA/insurance module as an NHCX participant: coverage eligibility, pre-auth, enhancement, claim, communication request and payment notice, with FHIR bundles and the JWE payload protocol. Keep legacy TPA portal and e-mail workflows, because adoption is partial. Treat NHCX as a strong differentiator rather than a hard legal mandate.

### Gaps
- No primary IRDAI 2025–26 circular text making NHCX mandatory was found. The current participant counts and the NHCX protocol version (HCX v0.9 or later) were not verified.

---

## 3. DPDP Act 2023 and DPDP Rules 2025

### Takeaway
The DPDP Rules 2025 were **notified on 13 Nov 2025**. The Board provisions took effect immediately. Consent Manager registration (Rule 4) starts 12 months later, around **13 Nov 2026**. The core obligations start **18 months later, around 13–14 May 2027**: notice, security, 72-hour breach report, retention, children, SDF and cross-border. A proposal to cut the SDF timeline to 12 months had **not** been notified as of about October 2026. A hospital is the Data Fiduciary and the HIS SaaS vendor is normally its **Data Processor**. The vendor is contractually bound to Rule 6 safeguards, and is itself a fiduciary for its own customer and user data.

### Cited Findings
- **Notification and commencement:** notified 13 Nov 2025. Rules 1, 2 and 17–21 apply immediately. Rule 4 (Consent Manager) applies after 1 year. Rules 3, 5–16 and 22–23 apply 18 months after publication. — [ILF Law summary](https://www.ilflaw.com/publications/digital-personal-data-protection-rules-2025-notified/). G.S.R. 846(E) is cited as the gazette number in secondary summaries and was not verified against egazette. — [search summary of cy5 / glocert guides](https://www.glocertinternational.com/resources/guides/dpdp-rules-2025-compliance-timeline/)
- **Rule 3 (notice):** notice must be in clear, plain language, itemise the personal data and purposes, and link to where the Data Principal can withdraw consent, exercise rights and complain to the Board. — [ILF Law](https://www.ilflaw.com/publications/digital-personal-data-protection-rules-2025-notified/)
- **Rule 4 (Consent Manager):** a Consent Manager must be an Indian company with net worth of at least Rs 2 crore, registered with the Board, and must keep consent records for at least 7 years. — [ILF Law](https://www.ilflaw.com/publications/digital-personal-data-protection-rules-2025-notified/)
- **Rule 6 (security):** required measures include encryption, masking or tokenisation, access control, logging and monitoring, and backups. Logs and personal data must be kept **at least 1 year** unless law requires longer. Data Processors must be bound by contract. — [ILF Law](https://www.ilflaw.com/publications/digital-personal-data-protection-rules-2025-notified/)
- **Rule 7 (breach):** inform affected Data Principals and the Board "without delay". Send a detailed report to the Board **within 72 hours** of becoming aware, or longer if the Board allows. — [ILF Law](https://www.ilflaw.com/publications/digital-personal-data-protection-rules-2025-notified/). The separate CERT-In 6-hour incident reporting comes from the CERT-In directions of April 2022, not DPDP. — [Matters.ai (secondary)](https://www.matters.ai/article/dpdp-breach-notification)
- **Rule 8 (retention and erasure):** erase data when the purpose is served or consent is withdrawn **unless law requires retention**. Give 48 hours' notice before erasure where the Third Schedule applies. That schedule's specific 3-year caps apply to e-commerce, gaming and social media classes, not hospitals. — [ILF Law](https://www.ilflaw.com/publications/digital-personal-data-protection-rules-2025-notified/)
- **Children (s.9 and Rule 10–12, Fourth Schedule Part A):** clinical establishments (as defined in s.2(c) of the Clinical Establishments Act 2010), mental health establishments and healthcare professionals are exempt from verifiable parental consent (s.9(1)) and from the tracking and targeted-advertising ban (s.9(3)). The exemption applies only where processing is "restricted to provision of health services to the child … to the extent necessary for the protection of her health". — [TaxClue on Rule 12 / Fourth Schedule](https://taxclue.in/blog/rule-12-dpdp-rules-2025-fourth-schedule-part-a-exempt-classes-clinical-educational-and-creche/); [Storyboard18](https://www.storyboard18.com/amp/digital/dpdp-rules-carve-out-key-exemptions-for-healthcare-providers-schools-and-childcare-services-processing-childrens-data-84208.htm); [dpdpa.com Fourth Schedule](https://www.dpdpa.com/schedule/schedule4.html)
- **Rule 13 (Significant Data Fiduciary):** an SDF must have an India-based DPO reporting to the board, an independent data auditor, a DPIA and audit every 12 months, and checks that algorithmic software does not risk Data Principals' rights. — [ILF Law](https://www.ilflaw.com/publications/digital-personal-data-protection-rules-2025-notified/). SDF designation is by central government notification under s.10. No healthcare entity designations were found.
- **Cross-border transfer (Rule 15 / s.16):** the central government may restrict transfers to notified countries (a negative list). No such list was found to be notified. — [ILF Law](https://www.ilflaw.com/publications/digital-personal-data-protection-rules-2025-notified/)
- **Proposed timeline compression:** in January 2026 MeitY was reported to plan cutting the SDF timeline to 12 months (about Nov 2026) and making the cross-border and information-call provisions immediate. A September and October 2026 legal tracker says this was **not notified**. — [Business Standard, Jan 2026](https://www.business-standard.com/technology/tech-news/meity-may-cut-compliance-timeline-for-key-dpdp-rules-to-12-months-126012201293_1.html); [MirvoLegal tracker (Sept 2026)](https://www.mirvolegal.com/insights/dpdp-rules-2025-compliance-timeline/); [dpdprules.org](https://dpdprules.org/blog/dpdp-18-months-to-12-months-proposal)

### Inferences
- **Software (build before May 2027; useful for selling now):**
  - Consent capture with purpose itemisation, multilingual notice text (the Act allows English or any Eighth Schedule language), and consent withdrawal.
  - A Data Principal rights portal or workflow: access, correction, erasure requests and grievance.
  - A legal-hold and retention engine. Medical-records retention law overrides erasure, so erasure requests on clinical data are refused with reasons. Non-clinical marketing data can be erased.
  - Immutable access audit logs kept at least 1 year. Clinical-record audit logs should be kept as long as the records themselves.
  - Encryption at rest and in transit, RBAC, and breach detection.
  - A breach-notification workflow: a 72-hour Board report template, affected-patient notification, and a 6-hour CERT-In report.
  - Separate handling of minors' records, using the exemption only for health services. No marketing or analytics on children.
- **Vendor (company):**
  - A DPA/processor agreement template with every hospital, covering processing on instructions, Rule 6 safeguards, breach notice to the fiduciary, sub-processor list, and deletion or return at contract end.
  - Its own privacy notice and DPO or contact person.
  - A data residency decision. India hosting is not mandated by DPDP generally, but is expected by hospitals, by ABDM security guidance and possibly by any future negative list.
  - An incident response plan aligned with CERT-In 2022.
  - Watch for a Consent Manager regime from 13 Nov 2026. ABDM's consent manager is a separate ABDM construct.

### Gaps
- The gazette number (G.S.R. 846(E)) and exact rule text were not verified against egazette.gov.in.
- The status of an alleged "Removal of Difficulties Order, 2026" (5 Oct 2026) is unconfirmed.
- No guidance was found on how the Board treats medical-records retention versus erasure. Inferred from Rule 8's "unless law requires retention".

---

## 4. Medical-records retention, EHR Standards 2016, terminologies (SNOMED CT, LOINC, ICD)

### Takeaway
The NMC Professional Conduct Regulations 2023 (3-year record retention, generic prescribing) were **put in abeyance on 23 Aug 2023**. The **MCI Code of Ethics Regulations 2002** therefore still govern. Under them, indoor (IPD) records are kept 3 years, a common provision whose primary text was not fetched here. Medico-legal records are kept until the case is disposed of. PCPNDT records are kept 2 years and Schedule H1 records 3 years. The **EHR Standards for India 2016** are still the referenced baseline, and no replacement was found. SNOMED CT is free for use in India via an NRCeS/MLDS affiliate licence. ICD-10 is the notified classification.

### Cited Findings
- The NMC RMP (Professional Conduct) Regulations 2023 were notified on 2 Aug 2023 and kept in abeyance on 23 Aug 2023. The 2002 MCI regulations remain valid. The 2023 text had a 3-year retention period and a 5-working-day record supply requirement. — [Drishti IAS](https://www.drishtiias.com/daily-updates/daily-news-analysis/nmc-registered-medical-practitioner-professional-conduct-regulations-2023); [Medical Dialogues](https://medicaldialogues.in/health-news/nmc/breaking-news-nmc-puts-its-controversial-registered-medical-practitioner-professional-conduct-regulations-2023-put-on-hold-116314); [Pharmabiz](https://www.pharmabiz.com/NewsDetails.aspx?aid=161170&sid=2)
- The NRCeS FHIR IG v6.5.0 still refers to code systems from the EHR Standards for India (2016). — [NRCeS IG](https://nrces.in/ndhm/fhir/r4/); [EHR Standards for India 2016 PDF](https://www.nrces.in/download/files/pdf/nrces_ehr_stand_india.pdf)
- MoHFW notified clinical terminology and coding standards in the EHR Standards 2016 and the National Digital Health Blueprint. These include SNOMED CT, LOINC and WHO ICD-10 for statistical classification. — [NRCeS Clinical Terminology and Coding Standards](https://www.nrces.in/download/files/pdf/nrces_Clinical%20Terminology%20and%20Coding%20Standards.pdf)
- India has been a SNOMED International member since 2014, with C-DAC Pune/NRCeS as the national release centre. MoHFW has made SNOMED CT free for use in India, but an affiliate licence obtained via MLDS is required. — [NRCeS FAQs](https://nrces.in/faqs); [SNOMED International: India](https://www.snomed.org/members/india)
- NRCeS national extensions include the India Drug Extension (Common Drug Codes for India), with releases on 13 Mar 2026, 15 Jun 2026 and 31 Aug 2026. The 2026 note gives 10,055 generic and 91,094 branded medicines. NRCeS also publishes AYUSH, geo-location and patient-instruction language extensions and national refsets. — [NRCeS national releases](https://www.nrces.in/services/national-releases); [India Drug Extension release notes 20260831](https://nrces.in/download/files/pdf/doc_SnomedCT-IndiaDrugExtension-PRODUCTION-ReleaseNotes_Current-en-US_IN1000189_20260831.pdf); [Patient Instructions Language Extension 20251215](https://www.nrces.in/download/files/pdf/doc_SnomedCT-IndiaPatientInstructionsLanguageExtension-ReleaseNotes_Current-en-US_IN1000189_20251215.pdf)

### Inferences
- **Software:**
  - Configurable retention per record class: OPD and IPD at least 3 years, medico-legal cases until disposal, minors' records until majority plus a margin (a common hospital policy, not law), PCPNDT 2 years, H1 3 years, ART per Act. Default to longer retention, such as 10 years or more for IPD, because NABH IMS requires a defined retention policy and states differ.
  - Legal-hold flag and MLC register.
  - Record supply to patients on request within a turnaround timer. 72 hours is the MCI 2002 norm (not fetched), and the abeyant 2023 rule said 5 working days.
  - Bundle a SNOMED CT browser and ValueSets, including the India Drug Extension for drug master and e-prescription, with LOINC for labs and ICD-10 for diagnoses, MRD coding and claims.
  - Design for ICD-11 later.

### Gaps
- The MCI 2002 regulation 1.3 text (3-year indoor records, 72-hour supply) was not fetched. It is widely cited but unverified here.
- State-specific retention rules (for example Maharashtra and Kerala) were not researched.
- No source was found on ICD-11 adoption in India, or on any 2025–26 revision of the EHR Standards.

---

## 5. Clinical Establishments (Registration and Regulation) Act 2010 and Rules 2012

### Takeaway
Rule 9 of the CE (Central Government) Rules 2012 requires registered establishments to display rates for each service, charge within a government-set range, follow Standard Treatment Guidelines, and maintain records (EMR/EHR as prescribed). On about 12 Oct 2026, the Union Health Secretary wrote to all states to enforce rate display in English and the local language. The Act applies only in states that have adopted it. Others use their own nursing-home laws.

### Cited Findings
- Rule 9(i) requires display of the rates for each service at a conspicuous place, in the local language and English. Rule 9(ii) requires charging within the range the centre determines. Establishments must follow STGs, which have been issued for 227 allopathic conditions. Rule 9(ii) is under Supreme Court challenge. — [LiveLaw](https://www.livelaw.in/top-stories/centre-defends-clinical-establishment-rule-in-supreme-court-says-it-curbs-excessive-pricing-of-medical-services-545337); [SC order 3 Nov 2022](https://api.sci.gov.in/supremecourt/2020/13006/13006_2020_3_9_39438_Order_03-Nov-2022.pdf)
- October 2026: MoHFW (Union Health Secretary Punya Salila Srivastava) wrote to state Chief Secretaries asking for conspicuous rate display at reception, emergency, diagnostics and billing in English and the vernacular, updated on cost changes. Digital kiosks and matching online rates were recommended. States were also asked to set standard package rates for foreign patients on Medical/Ayush visas. Non-adopting states (West Bengal, Maharashtra, Karnataka) use their own laws. — [Omnicuris MedShots, 12 Oct 2026](https://www.omnicuris.com/medshots/daily_updates/centre-mandates-display-of-hospital-service-rates_12-Oct-26_18-11); [Outlook India](https://www.outlookindia.com/healthcare-spotlight/centre-asks-states-to-ensure-hospitals-display-treatment-rates)

### Inferences
- **Software:** a tariff master with versioning and effective dates. Bilingual (English and regional) rate-card print, display or kiosk export and a public web rate page. Package master, including foreign-patient packages. Itemised bills matching the displayed tariff. STG and order-set templates. Data and reports for the CE registry and state returns.

### Gaps
- The text of Rule 9(iii) and 9(iv) (EMR maintenance and the information-supply requirement) was not fetched. The current status of the Supreme Court case was not confirmed. The Omnicuris piece is secondary, and the primary MoHFW letter was not found.

---

## 6. NABH: hospital standards (6th edition) and Digital Health Standards for HIS/EMR

### Takeaway
The NABH Hospital Standards **6th edition took effect on 1 Jan 2025**. It has 10 chapters, including **IMS (Information Management System)**. NABH runs a **vendor-facing certification: "Digital Health Standards for HIS/EMR Systems", 1st edition September 2024 (V12.1)**. It has Basic and Advanced levels and 2-year validity. Eligibility requires **at least 3 live hospital deployments in India and an ABDM M3 certificate**. A **2nd edition draft** was put out for comment by public notice on **22 July 2026**.

### Cited Findings
- NABH notification NABH/Gen/2024/6413 (10 Sep 2024): the 6th edition is effective 1 Jan 2025. No new or renewal applications under the 5th edition after 31 Dec 2024. 5th-edition surveillance continued until 31 Mar 2025. — [NABH notification](https://dev.nabh.co/wp-content/uploads/2025/07/Notification-%E2%80%93-Implementation-Plan-of-6th-Edition-Standards.pdf)
- The chapters are AAC, COP, MOM, PRE, IPC, PSQ, ROM, FMS, HRM and IMS. IMS covers information needs, medical records, confidentiality, retention, and the HIS/MIS. — [Pharmabiz](https://www.pharmabiz.com/PrintArticle.aspx?aid=172394&sid=1); [Humbleaim guide (secondary)](https://www.humbleaim.com/guides/nabh-6th-edition/). Counts of 639 objective elements and 105 core are from vendor sites and unverified. — [Cliniqwise](https://cliniqwise.com/resources/nabh-accreditation-handbook)
- NABH DHS for HIS/EMR, 1st edition September 2024 (V12.1, 14 Sep 2024):
  - **Levels and validity:** Basic or Advanced, valid 2 years.
  - **Eligibility:** "minimum of three (3) deployments in operational hospitals in India" and a valid ABDM M3 compliance certificate.
  - **Fees (plus 18% GST):** Rs 50,000 for HIS or EMR Basic, Rs 75,000 for both at Basic, Rs 1,00,000 for one at Advanced, Rs 1,50,000 for both at Advanced.
  - **Process:** portal registration, technical evaluation, demo or testing by NABH-approved testing agencies, non-conformity closure, then committee review.
  - **Portal:** digitalhealth.nabh.co.
  — [NABH DHS HIS/EMR programme page](https://nabh.co/programmes/digital-health-standards-his-emr-systems/); [Standards PDF V12.1](https://nabh-portal-live.s3.ap-south-1.amazonaws.com/wp-content/uploads/2025/07/24065304/NABH-Standards-for-HIS-and-EMR-Systems-V12.1-14th-September-2024.pdf)
- A 2nd edition public notice (22 Jul 2026) put the first draft of the "Digital Health Certification Standards for HIS/EMR Systems" out for stakeholder comment. — [NABH public notice PDF](https://nabh-portal-live.s3.ap-south-1.amazonaws.com/wp-content/uploads/2026/07/22172921/Public-Notice-for-Digital-Health-Standards_HIS-EMR-2nd-Edition.pdf)
- NABH also has a separate **"Digital Health Standards for Hospitals"**, for which a 2nd-edition draft was dated September 2025. This is hospital-side digital maturity accreditation. — [NABH DHS for Hospitals draft 2nd edition](https://portal.nabh.co/Announcement/Draft%20NABH%20DHS%202nd%20Edition.pdf); [NABH digital health standards page](https://nabh.co/digital-health-standards/)
- Koita Foundation reports 50 HIS/EMR vendors applied and 21 in process. The figure is likely dated. — [Koita Foundation](https://www.koitafoundation.org/DigitalHealth/NABH)

### Inferences
- **Vendor:** plan NABH DHS HIS/EMR certification once there are 3 live hospitals and M3. Target Basic first. Track the 2nd-edition final release, likely 2027.
- **Software:** build NABH 6th-edition support:
  - IMS: MRD, retention and destruction policy, confidentiality and audit trail, downtime procedures.
  - PSQ: a quality indicator dashboard and an incident reporting module.
  - MOM: high-risk and look-alike/sound-alike drug flags.
  - COP: consent forms.
  - AAC: an initial assessment timer.
- The exact list of NABH 6th-edition quality indicators needs the standard text.

### Gaps
- The full chapter-wise objective elements of the NABH DHS HIS/EMR standard were not extracted; the PDF needs to be read. The finalisation date of the 2nd edition is unknown. The NABH 6th-edition KPI list was not extracted.

---

## 7. NABL / ISO 15189:2022 (LIS implications)

### Takeaway
ILAC set a 3-year transition from ISO 15189:2012 to ISO 15189:2022, ending in **December 2025**. Sources give 6 Dec 2025 (UKAS) and 31 Dec 2025 (an Indian notice). NABL interprets the standard through **NABL 112** (112A/112B). As of November 2026, NABL-accredited labs should all be on the 2022 version.

### Cited Findings
- ILAC set a 3-year transition from publication in December 2022. — [ILAC](https://ilac.org/?p=124879)
- The deadline is given as 6 Dec 2025 (UKAS) and, in one Indian notice, as completing transition assessments by 31 Dec 2025. Labs not transitioned lose accreditation. — [UKAS](https://www.ukas.com/accreditation/iso-15189-transition/); [taxtmi news (secondary)](https://www.taxtmi.com/news?id=52926)
- NABL 112 interprets ISO 15189:2022 for India. A NABL medical application portal launched in August 2025. — [JLabPhy article](https://jlabphy.org/translating-global-standards-to-local-practice-comparing-international-organization-for-standardization-151892012-and-2022-with-implementation-insights-from-the-national-accreditation-board-for-test/); [Chronicle India](https://www.chronicleindia.in/online-magazine/csch-october-2025/nabl-medical-application-portal)

### Inferences
- **LIS software:** LOINC-coded test master and reference intervals by age and sex. Critical value alerts with acknowledgement logs. Specimen tracking, TAT monitoring and IQC (Levey-Jennings and Westgard). EQA records. Report authorisation with digital signature and an amended-report audit trail. Risk register. Validation documentation for the LIS itself (ISO 15189:2022 cl. 7.11 on information systems management). FHIR DiagnosticReport push to ABDM (DHIS Category 1, Rs 10 per record).

### Gaps
- NABL's own transition notice and the exact NABL 112 issue number and date were not fetched.

---

## 8. Software as a Medical Device: CDSCO / Medical Devices Rules 2017

### Takeaway
CDSCO issued a draft guidance on medical device software on 21 Oct 2025 and a **final "Guidance Document on Medical Device Software under MDR 2017"** in July 2026. It is reported as CDSCO/MD/GD/MDSW/01/2026, dated 21 Jul 2026 per one source and reported 30 Jul 2026. Software with a **medical purpose** (diagnosis, treatment, prediction, AI-CDS) is a medical device classified A–D under MDR 2017. Administrative, ERP and data-management software is not. A plain HIS/EMR is not a device. Diagnostic or therapeutic decision modules such as AI triage, sepsis prediction, dose calculators and image reading may be.

### Cited Findings
- The draft was dated 21 Oct 2025. The final guidance was issued in July 2026, with number CDSCO/MD/GD/MDSW/01/2026, about 62 pages. It clarifies how MDR 2017 applies, as guidance rather than new law, and uses "MDSW" as the umbrella term. — [search summary of Mondaq / BioSpectrum / Digital Health News](https://www.mondaq.com/india/healthcare/1826286/cdsco-publishes-guidance-on-medical-device-software-under-mdr-2017); [Digital Health News](https://www.digitalhealthnews.com/cdsco-unveils-final-medical-device-software-guidance-bringing-greater-regulatory-clarity-for-digital-health-innovators); [Cyril Amarchand blog, Jan 2026 (on draft)](https://corporate.cyrilamarchandblogs.com/2026/01/medical-device-as-software-has-cdsco-guidance-changed-the-rules/)
- Emergo (30 Jul 2026) on scope and classification:
  - **In scope:** "any software intended for a medical purpose", including AI-enabled software and some mobile apps.
  - **Out of scope:** software without a medical purpose, such as QMS/ERP, general wellness and data management software.
  - **Classification:** Classes A–D. Software driving hardware inherits the hardware's class. Standalone MDSW is classed by the healthcare condition and the significance of the information to decisions, in the IMDRF style.
  - **AI and ML:** applicants must disclose the composition and demographics of training, validation and test data, and bias and generalisability for Indian sub-populations.
  - **Also covered:** cybersecurity, labelling, clinical evidence and post-market surveillance.
  — [Emergo by UL](https://www.emergobyul.com/news/india-cdsco-finalizes-guidance-medical-device-software)

### Inferences
- **Vendor:** keep the core HIS (registration, billing, EMR documentation, order entry, display of results) as non-device. Write an intended-use statement that avoids diagnostic claims. Any AI module that recommends diagnosis or treatment, risk scoring that drives clinical action, or dose calculation should get a regulatory assessment. It may need CDSCO registration or a licence (Class B or higher needs an MD manufacturing licence through the SUGAM/online MD portal). Drug-interaction alerts based on a static knowledge base are borderline and should be assessed.
- Keep AI features clearly "clinician-in-the-loop", and document datasets in case CDSCO classification is triggered.

### Gaps
- The primary CDSCO PDF was not fetched. The exact class examples for CDS, the transition period, and whether HIS/EMR is explicitly named as excluded are all unverified.

---

## 9. Sector-specific statutes affecting HIS modules

### Takeaway
Several modules carry statutory registers and records:
- Ultrasound/radiology (PCPNDT: Form F, 2-year retention, online Form F in many states)
- Pharmacy (Schedule H1 register, 3 years; NDPS Form 3E and licence)
- IVF (ART Act 2021, national registry reporting)
- Telemedicine (2020 Guidelines, records)
- Others: MTP, BMW, blood bank/eRaktKosh, Mental Healthcare Act, birth and death registration (CRS)

Only some of these were verified in this pass.

### Cited Findings
- **PCPNDT:** Form F is mandatory per pregnant woman's ultrasound. Records are kept 2 years, or until disposal of legal proceedings. A signed printout is kept even when filed online. The monthly report to the Appropriate Authority by the 5th is not needed if Form F is filed online. Online Form F portals exist (Goa NIC software, Delhi pcpndt.delhi.gov.in, Bihar). — [CAHO PCPNDT guidance](https://caho.in/files/PCPNDT%20(1).pdf); [Meghalaya SOP](https://invest.meghalaya.gov.in/Documents/SOP/SoP_PCPNDT.pdf); [NHM Goa](https://nhm.goa.gov.in/?p=53); [SCC Online on record-maintenance](https://www.scconline.com/blog/?p=384827). The Supreme Court reaffirmed strict PCPNDT compliance in Dr Ramesh v State of Maharashtra, 2026 INSC 635. — [Mondaq](https://www.mondaq.com/india/healthcare/1806800/dr-ramesh-v-state-of-maharashtra-anr-2026-insc-635-supreme-court-reaffirms-strict-compliance-with-the-pcpndt-act)
- **Drugs & Cosmetics Rules: Schedule H1:** supply must be recorded in a separate register with the prescriber's name and address, the patient's name, the drug and the quantity, kept 3 years and open for inspection. **NDPS:** a separate record per essential narcotic drug (Form 3E, NDPS Rule 52H(3)), and a licence is needed to stock. — [NHSRC attachment: Schedule H1 register and NDPS forms](https://www.nhsrcindia.org/sites/default/files/2021-07/Attachment%20A%2C%20Schedule%20H1%20register%20and%20NDPS%20Forms.pdf); [CAHO NDPS](https://mail.caho.in/files/NDPS.pdf)
- **Telemedicine Practice Guidelines (25 Mar 2020, Appendix 5 to the IMC Regulations 2002):** the RMP must keep logs of tele-interactions (phone, email, chat, video logs), patient records and prescriptions "for the period as prescribed from time to time". — [MoHFW Telemedicine Guidelines PDF](https://covid19dashboard.mohfw.gov.in/pdf/Telemedicine.pdf); [Khaitan & Co](https://khaitanco.com/thought-leaderships/Telemedicine-Practice-Guidelines)
- **ART (Regulation) Act 2021:** clinics and banks must register with the National ART & Surrogacy Registry (registration valid 5 years). They must report enrolment of commissioning parties and donors, procedures and outcomes to the registry. — [SCC Online](https://www.scconline.com/blog/post/2021/12/23/assisted-reproductive-technology-regulation-act-2021/); [IFS Newsletter Jul 2025](https://indianfertilitysociety.org/wp-content/uploads/2025/07/IFSNewsletter-July-2025.pdf)
- **e-Pharmacy:** the 2018 draft amendment to the Drugs & Cosmetics Rules (e-pharmacy registration with CDSCO) **has not been notified**. Online sale is governed by court orders and existing licences. No 2025–26 notification was found. — [Scroll (2018 draft)](https://scroll.in/latest/893030/health-ministry-releases-draft-rules-to-regulate-online-sale-of-medicines); [Vajiram explainer](https://vajiramandravi.com/current-affairs/e-pharmacies-on-regulating-online-sale-of-drugs-in-india/)
- **Blood banks:** eRaktKosh shows real-time blood availability and is surfaced in Aarogya Setu 2.0. — [PIB, 6 Jul 2026](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/jul/doc202676912801.pdf)
- **Generic prescribing:** the NMC 2023 generic-name mandate is in abeyance (see section 4). — [Pharmabiz](https://www.pharmabiz.com/NewsDetails.aspx?aid=161170&sid=2)

### Inferences
- **Software modules and features:**
  - Radiology/USG: a Form F capture module with patient declaration and doctor declaration, state-portal export or print, a 2-year lock, and machine registration details.
  - Pharmacy: H1 register auto-generated at dispensing (append-only), NDPS register and stock reconciliation, and Schedule X handling.
  - IVF: ART registry reporting fields, donor coding and anonymisation.
  - Telemedicine: consent capture, identity verification, session logs and e-prescription following the Telemedicine Guidelines' drug lists.
  - E-prescription: generic name shown alongside brand. This is good practice and anticipates NMC, and maps to the SNOMED India Drug Extension.
  - Other modules: an MTP register (Form I/II/III), Bio-Medical Waste segregation and weight logs (barcode, annual report Form IV), a blood bank inventory/eRaktKosh sync, Mental Healthcare Act 2017 admission records, and birth and death notification data for CRS.

### Gaps
- **Not verified in this pass:**
  - BMW Rules 2016 (barcode, annual report form, record retention of 5 years)
  - Blood bank requirements under D&C Rules Part XB / NBTC and the eRaktKosh integration API
  - MTP Act register forms after the 2021 amendment
  - Mental Healthcare Act 2017 records and the MHRB
  - CRS (Civil Registration System) reporting timelines (21 days) and the RBD Amendment Act 2023 (digital birth certificate as single document, effective 1 Oct 2023)
  - ART record retention period and donor anonymity provisions
  - Surrogacy Act registry details
  - The Telemedicine Guidelines' retention period
  These need primary-source verification before being stated as fact.

---

## 10. Accessibility and Indian languages

### Takeaway
Language requirements arise from multiple sources:
- **Notices:** DPDP allows notice in English or any Eighth Schedule language, but this was not verified in this pass.
- **Rate cards:** must be in English and the local language under CE Rule 9 and the October 2026 MoHFW letter.
- **Patient instructions:** NRCeS publishes a SNOMED CT India Patient Instructions **Language Extension** for multilingual patient instructions.

### Cited Findings
- Rate display must be in English and the local language. — [Omnicuris, 12 Oct 2026](https://www.omnicuris.com/medshots/daily_updates/centre-mandates-display-of-hospital-service-rates_12-Oct-26_18-11); [LiveLaw](https://www.livelaw.in/top-stories/centre-defends-clinical-establishment-rule-in-supreme-court-says-it-curbs-excessive-pricing-of-medical-services-545337)
- The SNOMED CT India Patient Instructions Language Extension was released on 15 Dec 2025. — [NRCeS release notes](https://www.nrces.in/download/files/pdf/doc_SnomedCT-IndiaPatientInstructionsLanguageExtension-ReleaseNotes_Current-en-US_IN1000189_20251215.pdf)

### Inferences
- **Software:** an i18n framework with at least Hindi plus major regional languages for patient-facing outputs (consent forms, DPDP notices, rate cards, prescriptions and patient instructions, discharge summaries). Unicode Indic fonts in PDFs.
- **Accessibility:** apply WCAG 2.1 AA as good practice. GIGW 3.0 and IS 17802 apply mainly to government procurement.

### Gaps
- The DPDP Eighth Schedule language rule, GIGW/IS 17802 applicability to private-sector software, and any state-level language mandate for medical records were not verified.

---

## Consolidated checklist (inference, synthesising sections 1–10)

**Must-have software capabilities (as of November 2026):**
1. ABDM v3: ABHA (Aadhaar and mobile OTP, ABHA address, KYC status), Scan & Share token, HFR/HPR linkage, M2 HIP (care contexts, FHIR R4 NRCeS IG v6.5.0 bundles for all HI types, Fidelius), M3 HIU (consent, fetch, decrypt, purge), and DHIS reporting.
2. NHCX claims (FHIR claims profiles), alongside legacy TPA workflows.
3. DPDP-ready privacy stack (deadline about 13 May 2027): itemised multilingual notice and consent, withdrawal, rights requests, grievance, audit logs (at least 1 year), encryption and RBAC, 72-hour breach workflow, retention and legal-hold engine.
4. Terminologies: SNOMED CT (plus the India Drug Extension), LOINC, ICD-10, all under the free NRCeS licence.
5. Regulatory registers: Form F (PCPNDT), Schedule H1, NDPS, MTP, BMW, ART, MLC, and birth and death.
6. CE Act tariff management: bilingual rate display and rate-card exports, packages, and STG order sets.
7. NABH 6th-edition support: the IMS chapter, a quality indicators dashboard, incident reporting and medication safety.
8. LIS aligned to ISO 15189:2022 / NABL 112.
9. Keep CDS and AI features out of medical-device scope, or route them through a CDSCO assessment under the July 2026 MDSW guidance.

**Vendor company actions:**
- **ABDM:** register as an integrator in the ABDM sandbox, pass functional tests, a WASA security audit, and the demo to exit with M1–M3 production certification. Register as a DSC under DHIS.
- **NABH:** obtain the Digital Health Standards HIS/EMR certification (needs 3 deployments and M3; Rs 50k–1.5L plus GST; 2-year validity). Track the 2nd edition (draft 22 Jul 2026).
- **DPDP:** sign processor DPAs with hospitals, prepare a CERT-In 6-hour incident process, choose India data residency, and appoint a privacy contact or DPO.
- **CDSCO:** decide intended use and classification for any AI or CDS module under the MDSW guidance.
- **SNOMED:** obtain the SNOMED CT affiliate licence via NRCeS/MLDS.
