# **Sovereign Open-Source Healthcare Platform Architecture: A Comprehensive Blueprint for Indian Tertiary Hospital Operations**

## **Architectural Paradigm and Interoperability Topology**

Eliminating proprietary software dependencies across acute-care hospital networks in India requires transitioning from isolated software utilities to a unified, standards-based, federated ecosystem. Modern healthcare operations require transactional integrity for clinical and financial data alongside real-time data exchange across diagnostic, administrative, and clinical departments1.  
A single monolithic system cannot address the breadth of tertiary healthcare operations, which span low-latency Digital Imaging and Communications in Medicine (DICOM) rendering, high-throughput serial instrument interfacing in pathology, multi-echelon inventory accounting adhering to Indian Goods and Services Tax (GST) mandates, critical care telemetry in intensive care units, and national regulatory interfaces3.  
Attempting to force clinical bedside charting into a traditional enterprise resource planning (ERP) framework results in low clinician adoption, while extending clinical medical records software into general ledger accounting or supply chain management compromises statutory audit controls3.  
The architectural solution is a federated, event-driven topology anchored by an open integration spine and governed by a centralized identity provider8. Inter-service communication relies on international clinical protocols—primarily Fast Healthcare Interoperability Resources (HL7 FHIR) Release 4, DICOMweb, and HL7 v2/ASTM—alongside standard RESTful application programming interfaces and asynchronous message brokers4.  
Centralized identity and access management is established through Keycloak, an open-source OpenID Connect (OIDC) and OAuth 2.0 identity provider9. Keycloak enables Single Sign-On (SSO), centralized Role-Based Access Control (RBAC), and fine-grained Attribute-Based Access Control (ABAC), ensuring that clinical, nursing, administrative, and auditing staff navigate specialized applications using unified digital credentials9.  
The system coordinates services through an enterprise integration middleware layer, drawing on enterprise integration patterns (EIP) to route clinical encounters, orders, diagnostic results, and financial events across operational boundaries8.

| Architectural Layer | Core Functional Responsibility | Primary Open-Source Technology | Applied Open-Source License | Core Interoperability Protocols |
| :---- | :---- | :---- | :---- | :---- |
| **Identity & Access Management** | Federated SSO, directory sync, user lifecycle, MFA | Keycloak | Apache License 2.0 | OpenID Connect, OAuth 2.0, SAML 2.0, LDAP9 |
| **Enterprise Integration Spine** | Message transformation, orchestration, interface routing | Open Integration Engine / Apache Camel | MPL 2.0 / Apache 2.0 | HL7 v2 (MLLP), FHIR R4 (HTTP/REST), ASTM, Webhooks10 |
| **Clinical Core (EHR/HMS)** | Patient registry, ward management, doctor/nurse charting | Ozone HIS (OpenMRS 3\) / CARE Platform | MPL 2.0 / MIT License | HL7 FHIR R4, REST, SNOMED CT, LOINC, ICD-106 |
| **Enterprise Backbone (ERP)** | General ledger, stores, procurement, HRMS, billing | ERPNext | GNU GPLv3 | REST, GraphQL, Indian GST E-Invoicing APIs3 |
| **Diagnostic Imaging (PACS)** | DICOM image ingestion, storage, rendering, routing | Orthanc \+ OHIF Viewer v3 | GPLv3 / MIT License | DICOMweb (WADO-RS, QIDO-RS, STOW-RS), DIMSE11 |
| **Diagnostic Pathology (LIS)** | Sample accessioning, analyzer interfacing, QA/QC | OpenELIS Global / SENAITE | MPL 2.0 / GNU GPLv2 | HL7 v2.5.1, ASTM 1394/1381, LOINC, FHIR4 |
| **Regulatory & National Rails** | ABDM M1, M2, M3, ABHA verification, Scan & Share | CARE ABDM / NHA ABDM Wrapper | MIT License / Apache 2.0 | ABDM v3 Gateway APIs, Fidelius (ECDH Curve25519)18 |
| **AI Agentic Layer (MCP)** | Autonomous workflows, clinical decision support | FastMCP Server \+ vLLM / Ollama | MIT License / Apache 2.0 | Model Context Protocol (JSON-RPC), OpenAI API Spec20 |

## **Hospital Information Management and Clinical Specialty Cores**

The clinical core manages longitudinal patient charts, bedside observations, clinical orders, and ward operations2. In an Indian tertiary care setting, this layer must natively accommodate high-volume outpatient departments (OPD), complex inpatient admissions (IPD), multidisciplinary clinical protocols, and statutory Indian health identifiers6.

### **Evaluation of Open-Source Clinical Engines**

Four primary platforms serve as viable candidates for the clinical core:  
Bahmni integrates OpenMRS on the backend with an optimized clinical user interface, coupled with OpenELIS for laboratory workflows and historical integrations with Odoo or Crater for billing7. Developed primarily by Thoughtworks and supported by the Bahmni Coalition, it has extensive validation across rural hospitals and public health networks in India and was among the first platforms certified for the Ayushman Bharat Digital Mission (ABDM)2. Its strengths lie in mature clinical workflows, active community support in India, and strong terminology bindings to SNOMED CT and LOINC2.  
However, Bahmni exhibits architectural fragmentation, as synchronizing clinical encounters from OpenMRS (Java/MySQL) to enterprise billing modules relies on Atom Feeds and complex synchronization scripts, which can produce consistency bottlenecks during sudden inpatient surges28.  
Ozone HIS represents an enterprise-grade digital public health architecture spearheaded by Mekom Solutions9. It brings together OpenMRS 3 (O3)—built with modern React-based microfrontends—alongside ERPNext for operations, SENAITE or OpenELIS for laboratory workflows, and Orthanc for imaging9. The modules communicate directly over an event-driven Apache Camel enterprise integration pattern client rather than fragile point-to-point database synchronizations9.  
Its microfrontend architecture is optimized for mobile, tablet, and desktop workstations, and it features native coupling to ERPNext rather than proprietary-licensed versions of Odoo9. The primary trade-off involves its highly modular, containerized architecture, which demands Kubernetes orchestration expertise and significant operational maturity from local technical teams10.  
CARE, engineered by the Open Healthcare Network (originally Coronasafe Network) and recognized as the 50th United Nations Digital Public Good, is a Python/Django and React platform designed for acute hospital management, bed allocation, and intensive care operations6. Deployed across hundreds of hospitals in multiple Indian states under the 10BedICU project, CARE features native ABDM v3 integration, advanced TeleICU interfaces, and medical equipment telemetry tracking6.  
Its advantages include a single-stack architecture (Django, PostgreSQL, Redis, React), production-proven critical care workflows, and alignment with Indian public health infrastructure6. Its main limitation is that its out-of-the-box primary care templates are less modular than OpenMRS, focusing heavily on emergency, inpatient, and acute critical care6.  
Frappe Health extends the Python-based ERPNext framework directly into clinical domains, managing patient appointments, clinical consultations, vital sign entry, inpatient bed occupancy, and medication orders within the primary ERP database3.  
Its primary benefit is monolithic simplicity; transactions occur within a single database schema, eliminating inter-service synchronization overhead and simplifying invoicing, inventory deduction, and accounting3.  
However, its clinical charting and bedside documentation lack the depth of OpenMRS or CARE, meaning that complex clinical protocols, multi-specialty clinical flows, and real-time medical device telemetry require substantial custom application development3.

| Platform | Core Language / Framework | Database Architecture | Primary License | ABDM Readiness | Acute Care & ICU Depth | ERP & Financial Coupling | Maintainer Dynamics |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| **Bahmni** | Java (Spring) / React, AngularJS | MySQL, PostgreSQL | MPL 2.0 / AGPLv3 | High (Certified M1, M2, M3)8 | Moderate | Loose (External via Atom Feed / Odoo)28 | Bahmni Coalition / Thoughtworks2 |
| **Ozone HIS** | Java, TypeScript, Python | MySQL, PostgreSQL | MPL 2.036 | High (Inherits OpenMRS O3 FHIR)8 | Moderate to High | Native (Apache Camel to ERPNext)9 | Mekom Solutions & OpenMRS Community9 |
| **CARE (OHC)** | Python (Django) / React (TS) | PostgreSQL | MIT License34 | Native (Production across 10+ States)6 | Exceptional (Built for ICU / TeleICU)6 | Moderate (Internal billing; requires ERP hook)32 | Open Healthcare Network / eGov6 |
| **Frappe Health** | Python (Frappe) / Vue.js | MariaDB / PostgreSQL | GNU GPLv39 | Moderate (Community ABDM app)24 | Basic | Native Monolith (Single Engine)3 | Frappe Technologies & Community14 |

### **Departmental Inpatient and Specialty Workflows**

Inpatient and bed management requires dynamic coordination of admission, discharge, and transfer workflows9. Dynamic visual ward matrices capture physical bed layouts, nursing station assignments, room categories (e.g., general ward, twin sharing, private suite, isolation), and sanitization statuses9. Inpatient orders route systematically to central pharmacies, laboratories, and dietary units8. Bed consumption auto-generates recurring charges inside the billing ledger based on length-of-stay algorithms and specialty tariffs3.  
Intensive care and TeleICU environments demand continuous physiological parameter tracking, blood-gas interpretation, ventilator setting documentation, and sequential organ failure assessment scoring34. Deploying the CARE EMR architecture provides dedicated ICU flowsheets, tracking inotropic support, arterial blood gas trends, fluid input/output balances, and invasive line durations6.  
For hub-and-spoke multi-facility operations, CARE's TeleICU framework streams live multi-parameter monitor waveforms and ventilator metrics to centralized intensivist monitoring commands, bridging acute specialist shortages in regional facilities6.  
Emergency intake prioritizes rapid patient registration, accommodating unidentified individuals using emergency universal identifiers. Emergency workflows utilize the Emergency Severity Index or Manchester Triage System to categorize clinical urgency. Automated alerts notify duty trauma teams, on-call specialists, and cross-match blood banks the moment high-acuity cases are accessioned.  
Operating theatre and catheterization lab scheduling integrates surgical suite booking, anesthetic review records, sterile instrument pack checks, implant serialization, and digital execution of the WHO Surgical Safety Checklist. For Interventional Cardiology and Cath Lab workflows, clinical forms record fluoroscopy exposure metrics, pre-procedure anti-platelet therapy, coronary angiogram vessel scoring, stent lot numbers, and automated post-procedure telemetry monitoring protocols.  
Dialysis modules manage scheduled recurring cycles for hemodialysis and peritoneal dialysis. Key records track vascular access points, dialyzer reuse cycles, pre- and post-dialysis weight differentials, heparin dosing regimens, machine disinfections, and monthly virology screening panels (HBV, HCV, HIV) to maintain compliance with National Dialysis Programme guidelines.  
Maternal documentation requires obstetric records tracking gravidity, parity, abortion, and living children, symphysis-fundal measurements, ultrasound dating, and automated Partograph generation during active labor. Post-delivery, the system handles newborn APGAR scoring, congenital screening records, immunization scheduling, and neonatal intensive care unit incubator parameter tracking.  
Operating an Assisted Reproductive Technology (ART) and In Vitro Fertilization (IVF) clinic under India's Assisted Reproductive Technology (Regulation) Act, 2021 and Surrogacy (Regulation) Act, 2021 requires distinct clinical workflows38. The clinical documentation platform must implement granular reproductive tracking while protecting sensitive data38:  
Ovarian stimulation monitoring requires logging transvaginal ultrasound folliculometry with follicle sizes mapped per ovary, alongside continuous hormone profiling and trigger timing.  
Embryology laboratory workflows encompass oocyte retrieval records, semen analysis and preparation logs, fertilization documentation, embryo culture scoring under the Gardner grading system at cleavage and blastocyst stages, cryopreservation batch logging with canister cane and straw coordinates, and embryo transfer notes.  
Statutory compliance under the ART Act 2021 mandates strict cryptographic separation of donor and commissioning couple identities to preserve statutory donor anonymity38.  
The system validates that all donor gamete sourcing connects directly to licensed ART banks, enforces non-modifiable consent verification protocols, and maintains longitudinal audit trails for all cryopreserved reproductive tissues, complete with mandatory annual storage tracking and regulatory incident logging38.

## **Diagnostic Ancillary Systems: PACS and Laboratory Information Systems**

Medical imaging infrastructure requires a Picture Archiving and Communication System (PACS) compliant with DICOM standards, coupled with a zero-footprint web viewing interface capable of displaying images across multi-monitor diagnostic setups, clinical wards, and remote teleradiology environments11.  
In the primary medical imaging topology, imaging modalities (CT, MRI, X-ray) transmit studies via standard DICOM C-STORE protocols to an Orthanc PACS archive backed by PostgreSQL metadata indexing and S3-compatible object storage11.  
The archive exposes studies through DICOMweb services, specifically WADO-RS for retrieving pixel data and QIDO-RS for querying study metadata11.  
The front-end rendering layer is driven by the OHIF Viewer v3, which communicates with Orthanc over DICOMweb, using Cornerstone3D and client-side WebGL acceleration to render multi-planar reconstructions, maximum intensity projections, and volumetric models without local caching11.  
Authentication and contextual launching are mediated via Keycloak, which validates user identity and allows OpenMRS 3 or CARE patient charts to launch imaging studies directly using SMART-on-FHIR launch protocols9.

### **Medical Imaging: Orthanc PACS and OHIF Viewer**

Orthanc serves as a lightweight, reliable, open-source DICOM server9. Written in C++, it offers a RESTful architecture, native DICOMweb support (WADO-RS, QIDO-RS, STOW-RS), and extensible plugins for PostgreSQL index storage and S3-compatible object storage backends11. It functions as a DICOM Service Class Provider and User, ingesting raw studies directly from imaging modalities via traditional DIMSE network protocols15.  
OHIF Viewer v3 is a zero-footprint web viewer developed in React and TypeScript, running in standard web browsers using the Cornerstone3D rendering engine11. It renders multi-planar reconstructions, Maximum Intensity Projections, volume rendering, and complex 2D/3D measurements directly via WebGL, drawing on client GPU acceleration without requiring persistent local caching of patient imaging files11.  
DCM4CHEE Arc Light represents a heavy-duty, Java EE-based enterprise clinical image archive deployed inside a WildFly application container8. It provides extensive support for large-scale enterprise archiving and multi-tiered Hierarchical Storage Management, but its operational overhead is substantial compared to Orthanc15.

| Architectural Attribute | Primary: Orthanc \+ OHIF Viewer v3 | Alternative: DCM4CHEE Arc Light 5 |
| :---- | :---- | :---- |
| **Codebase & Runtime** | C++ core daemon; Node.js/React TypeScript viewer11 | Enterprise Java EE running inside WildFly application server40 |
| **Base Footprint** | Low memory footprint (approximately 512 MB base)11 | Substantial resource requirement (4 GB to 8 GB minimum allocation) |
| **API & Programmability** | RESTful HTTP API, Lua scripting, Python plugins, DICOMweb11 | Java-centric configuration, JMX consoles, complex HL7 v2 mapping8 |
| **Storage Architecture** | Decoupled metadata (PostgreSQL) and pixel data (MinIO/S3) | Hierarchical Storage Management spanning local filesystems and tape archives |
| **Viewer Integration** | Native integration with OHIF v3 via Cornerstone3D engine11 | Bundles basic legacy web interface; external OHIF requires complex setup40 |
| **DevOps Complexity** | Containerized deployment via single Docker Compose or Helm chart | Multi-container Java EE deployment requiring LDAP and WildFly tuning12 |

### **Laboratory Information Systems and Instrument Interfacing**

Pathology operations require sample tracking, test ordering, analyzer interfacing, quality assurance, and result verification4.  
OpenELIS Global, developed in Java with a PostgreSQL backend, is designed for clinical diagnostic environments and reference laboratories16. It manages sample lifecycles from accessioning, barcoding, and work plan generation to automated instrument routing and electronic sign-offs9. It integrates clinical vocabulary standards—such as LOINC for investigations and SNOMED CT for microbiological pathogens—and communicates over HL7 v2, FHIR, and REST protocols8.  
SENAITE, built on Python and the Plone/Zope enterprise content management framework, is a LIMS supporting laboratory compliance with ISO/IEC 17025 and ISO 151899. It manages worksheets, sample partitions, aliquots, chain-of-custody tracking, and instrument calibrations9. While SENAITE offers deep analytical sample controls, OpenELIS Global provides closer alignment with clinical hospital workflows and public health reporting channels9.  
Automated bidirectional instrument interfacing connects clinical analyzers (e.g., Sysmex hematology systems, Roche Cobas, Beckman Coulter chemistry units) to the laboratory core4. The process initiates when a physical analyzer scans a barcoded specimen tube at the intake rack4.  
The analyzer transmits an inquiry message across an RS-232 serial interface or TCP/IP socket using the ASTM E1381/E1394 standard4. An edge integration service intercepts this transmission, decodes the raw protocol frames, and converts them into an HL7 v2 query message or RESTful API call directed to the integration middleware5.  
The integration engine matches the sample identifier against active orders in OpenELIS or SENAITE and returns the required test parameters to the instrument4.  
Once the analytical run finishes, the analyzer outputs raw numerical values via ASTM or an HL7 v2 ORU^R01 result message4.  
The integration middleware validates the results against physiological plausibility thresholds, executes automated delta checks against historical patient records, and loads the data into the LIS electronic verification queue for pathologist authorization4.

### **Laboratory Quality Control and NABL (ISO 15189:2022) Compliance**

Achieving accreditation under the National Accreditation Board for Testing and Calibration Laboratories (NABL), which operates under ISO 15189:2022, requires statistical internal quality control (IQC) procedures16. Control sample runs must be continuously evaluated against target mean values (![][image1]) and standard deviations (![][image2])46. Statistical calculations are defined mathematically as:  
![][image3]  
![][image4]  
Internal Quality Control monitoring evaluates every run against Westgard evaluation criteria:

> * ![][image5]: A single control measurement exceeds the mean by ![][image6], serving as an internal warning flag46.  
> * ![][image7]: A single control measurement exceeds ![][image8], requiring immediate run rejection due to random error46.  
> * ![][image9]: Two consecutive control measurements exceed ![][image10] or ![][image11], requiring run rejection due to systematic error46.  
> * ![][image12]: One measurement exceeds ![][image10] and the consecutive measurement exceeds ![][image11], identifying severe random error46.  
> * ![][image13]: Four consecutive control measurements exceed ![][image14] or ![][image15], signaling systematic instrument drift46.  
> * ![][image16]: Ten consecutive measurements fall on one side of the mean, requiring calibration adjustment for systematic bias46.

Using the open-source OpenQC engine (FastAPI, Python, SQLAlchemy) or analytical extensions within SENAITE/OpenELIS, every analyzer test run is automatically plotted on digital Levey-Jennings charts16.  
Test results trigger automated delta checks: if a patient's current parameter diverges from their previous result beyond dynamic physiological thresholds, the system flags the result for manual pathologist review to prevent false-positive or erroneous clinical reporting4.

## **Enterprise Operations: ERP, Supply Chain, Biomedical Engineering, CSSD, and Blood Banking**

Tertiary healthcare institutions function as complex logistics operations requiring real-time inventory management, high-volume billing, double-entry general ledger accounting, statutory tax reporting, and physical asset engineering3.

### **Enterprise Resource Planning, Finance, and Billing**

ERPNext serves as the platform's enterprise operations core3. Completely open source under the GNU GPLv3 license, ERPNext includes modules for accounting, procurement, warehouse logistics, fixed asset tracking, human resource payroll, and customer relationship management3.  
For statutory financial compliance, ERPNext provides built-in localization for the Indian regulatory landscape, including automated handling of Indian GST (CGST, SGST, IGST calculations across tax slabs), automated electronic invoicing under GST via API bridges, electronic waybill generation, and Tax Deducted at Source registers.  
Inpatient consolidated billing aggregates multiple operational touchpoints into a unified transaction stream3. Daily bed occupancy fees are calculated dynamically from inpatient ward movements3.  
Material debits are posted in real-time as pharmaceuticals and surgical consumables are dispensed3. Consultation and procedural tariffs are linked directly to clinical specialty charge books, and diagnostic entries are captured automatically from pathology and radiology orders3.  
The billing architecture handles multi-payer arrangements, accommodating Third Party Administrators (TPAs) and private corporate insurance23. It maintains pre-authorization records, tracks co-pays and deductibles, segregates non-medical expenses, and integrates claim submissions for the Ayushman Bharat Pradhan Mantri Jan Arogya Yojana (AB PM-JAY) national scheme23.  
Odoo Community provides an alternative modular architecture under LGPLv39. However, Odoo SA omits critical financial functionality from its open-source Community edition, including automated bank reconciliations, complete analytical general ledger views, and dynamic tax audit reports, reserving them for its proprietary Enterprise edition. ERPNext therefore provides a more dependable foundation for a completely open-source enterprise hospital installation9.

### **Supply Chain, Store Management, and Hospital Pharmacy**

Hospital supply chain management balances multi-echelon warehouse structures across central medical stores, surgical stores, catheterization labs, and outpatient pharmacies3.  
ERPNext enforces First-Expiry, First-Out (FEFO) dispensing rules for pharmaceutical inventories3. Stock intake mandates batch numbers, manufacturer registrations, and expiration dates. Automated alerts notify supply chain managers when batches approach expiration thresholds, initiating clearance protocols or vendor returns3.  
Prescriptions entered electronically by clinicians in OpenMRS or CARE populate instantly within the relevant pharmacy dispensing queues3. For schedule-controlled narcotics and restricted antibiotics, the system enforces secondary clinical sign-offs, tracks physical stock balances, and logs dispensing events against prescribing medical registration numbers to maintain compliance with the Drugs and Cosmetics Act.

### **Biomedical Engineering and Asset Maintenance**

Biomedical asset management safeguards clinical equipment—from oxygen delivery systems and patient monitors to multi-slice CT scanners and linear accelerators—against operational failure33.  
The platform coordinates maintenance through ERPNext Asset Maintenance or the open-source openMAINT framework:

> * Asset registries record manufacturer details, serial numbers, installation dates, operational documentation, and service technician contacts48.  
> * Planned preventive maintenance (PPM) workflows generate periodic service schedules and calibration checklists aligned with NABH engineering guidelines48.  
> * Breakdown maintenance ticketing enables clinical nursing staff to log equipment faults directly, automatically tracking Mean Time to Repair (MTTR), Mean Time Between Failures (MTBF), and coverage under Annual Maintenance Contracts (AMC) and Comprehensive Maintenance Contracts (CMC)48.

### **Central Sterile Services Department**

Sterile processing prevents hospital-acquired infections by tracking the decontamination, inspection, packaging, and autoclaving of surgical instrument sets49.  
Individual surgical sets are barcoded or laser-etched with 2D DataMatrix serial identifiers. Machine telemetry—including cycle durations, temperature profiles, and pressure levels—is recorded across washer-disinfectors, high-pressure steam autoclaves, and low-temperature ethylene oxide or plasma sterilizers48.  
Batches cannot be marked ready for clinical release without an electronic log verifying that Class 5/6 chemical indicators and biological spore tests (*Geobacillus stearothermophilus*) have cleared inspection. If a sterilization failure occurs, the platform traces every instrument set from that autoclave run to its current location or patient encounter.

### **Blood Bank Management System**

Blood banking operations require donor recruitment tracking, serological screening, component separation, storage management, and patient cross-matching52.  
The platform captures donor health criteria, deferral logs, and voluntary donation consents in compliance with National Blood Transfusion Council guidelines55. Whole blood donations are tracked through centrifugation into Packed Red Blood Cells (42-day shelf life), Fresh Frozen Plasma, Platelet Concentrates (5-day agitated shelf life), and Cryoprecipitate.  
Electronic records verify mandatory Transfusion-Transmitted Infection testing panels covering HIV 1 & 2, Hepatitis B, Hepatitis C, Syphilis, and Malaria, with positive units routed to automated biohazard disposal workflows. Forward and reverse grouping, antibody screening, physical cross-match verifications, and blood issue documentation integrate directly with national reporting interfaces such as eRaktKosh55.

## **Enterprise Productivity, Communication, and Marketing**

Hospitals require secure document collaboration tools, internal communications, and patient outreach mechanisms that run entirely on self-hosted infrastructure to prevent protected health information leaks3.

### **Collaborative Office Infrastructure**

Standardizing administrative documentation, operational protocols, and clinical review panels requires a collaborative productivity suite. Nextcloud Hub paired with Collabora Online provides a self-hosted workspace that allows multi-user document, spreadsheet, and presentation editing within standard web browsers. It enforces fine-grained access control, at-rest storage encryption, and institutional data sovereignty.  
ONLYOFFICE Docs offers an alternative collaborative engine with high Microsoft Office format fidelity. However, its free Community Edition restricts mobile web editing and imposes concurrent connection limits, making Nextcloud Hub with Collabora Online the more scalable choice for an unrestricted enterprise deployment.

### **Enterprise Mail Systems**

Clinical communication, diagnostic alerts, purchasing transactions, and administrative coordination rely on self-hosted mail infrastructure. Stalwart Mail Server is an open-source, single-binary mail server written in Rust. It natively implements JMAP, IMAP4, and SMTP protocols, and includes integrated spam filtering, OpenDKIM, OpenDMARC, and RocksDB or PostgreSQL storage backends. Its concurrent, memory-safe architecture provides high throughput with low memory overhead.  
Mailcow-dockerized offers a proven containerized alternative that combines Postfix, Dovecot, Rspamd, SOGo webmail, and an administrative interface, though its resource footprint is larger than Stalwart's unified binary architecture.

### **Real-Time Clinical Communications**

Critical care consults, code responses, and nursing coordination require reliable, low-latency messaging platforms that eliminate dependence on unencrypted commercial chat applications.  
The Matrix Protocol, using Synapse or Dendrite servers paired with Element web and mobile clients, establishes a decentralized messaging architecture. It features end-to-end encryption via the Olm/Megolm cryptographic ratchets, granular room permission structures, automated webhooks for critical lab alerts, and secure voice/video calling.  
Mattermost Team Edition and Zulip Server provide organized, channel-based team messaging environments. However, Mattermost limits enterprise access controls and compliance exports in its open-source edition, whereas Matrix remains unrestricted and federatable across regional multi-hospital networks.

### **Healthcare CRM and Omnichannel Patient Engagement**

Patient outreach, elective procedure inquiries, and preventive health campaigns require coordinated customer relationship management3. The ERPNext CRM module tracks inquiries, patient conversion lifecycles, and scheduled appointments within the core platform database3.  
To manage multi-channel communication, Chatwoot integrates with the platform as an open-source communication layer, consolidating inbound messages from hospital web portals, verified WhatsApp Business API gateways, SMS services, and email into an administrative inbox.

## **Indian Regulatory Compliance and Quality Accreditations**

Deploying a hospital management platform in India requires adherence to national digital health standards, statutory data protection mandates, and hospital quality accreditations3.

### **Ayushman Bharat Digital Mission Interoperability**

Under the National Health Authority, ABDM standardizes national health data exchange8. The platform natively implements all three core operational milestones:  
Milestone M1 establishes patient identity management58. It integrates ABHA number and address creation, verified through mobile OTP or Aadhaar KYC18.  
The platform supports the ABDM Scan & Share workflow: patients scan a counter-specific QR code using their ABHA-enabled personal health record application, transmitting their demographic profile directly to the hospital's outpatient registration queue to generate encounter tokens without manual data entry18.  
Milestone M2 establishes Health Information Provider functionality18. Clinical encounters, diagnostic reports, and discharge summaries are registered as discrete care contexts linked to the patient's ABHA18.  
When a validated external consent artifact is received, the HIP engine compiles patient records into standardized FHIR document bundles aligned with the National Resource Centre for EHR Standards (NRCeS) profiles8.  
Milestone M3 establishes Health Information User capabilities, allowing clinicians to request and review external longitudinal health records from across the national ABDM network18.  
The system generates digital consent requests to the patient, processes approval responses from the national gateway, and parses external FHIR bundles into the primary clinical chart18.  
To safeguard health data across untrusted public networks, ABDM mandates end-to-end encryption using Elliptic-Curve Diffie-Hellman key exchanges over Curve2551918. Payloads are encrypted using AES-256-GCM, with symmetric keys derived by hashing shared secrets with ephemeral salt values18.  
The integration architecture utilizes the open-source care\_abdm module from the Open Healthcare Network or the Java-based NHA ABDM Wrapper to manage these cryptographic handshakes and message transfers18.

### **Digital Personal Data Protection Act, 2023**

As a Data Fiduciary under the DPDP Act 2023, the hospital platform enforces technical controls across all data stores38.  
The system logs timestamped digital consents detailing specific processing purposes, while providing patients with mechanisms to withdraw authorization3.  
Role-based access controls enforce purpose limitation, restricting clinical chart access strictly to attending clinical teams on a need-to-know basis3.  
Data erasure workflows support patient deletion requests for non-statutory data, while preserving medical records required under National Medical Commission retention rules3.  
All data at rest is secured via LUKS or database-level AES-256 encryption, complemented by mandatory TLS 1.3 across all internal and external communication channels3.

### **National Accreditation Board for Hospitals & Healthcare Providers Standards**

NABH 5th and 6th Edition standards require digital tracking of clinical quality indicators and patient safety parameters:

> * Medication safety protocols enforce real-time checks for drug-drug interactions, duplicate therapeutic orders, and recorded allergies, alongside dual-verification requirements for high-alert drugs.  
> * Incident management workflows provide confidential reporting channels for adverse drug reactions, sentinel events, needle-stick injuries, and hospital-acquired infections, complete with automated Root Cause Analysis and Corrective and Preventive Action tracking.  
> * Surgical safety modules mandate documented execution of all three phases of the WHO Surgical Safety Checklist (Sign-In, Time-Out, Sign-Out) before operating room procedures can proceed3.  
> * Quality monitoring engines automatically calculate key institutional metrics, including bed occupancy rates, average length of stay, inpatient mortality rates, 30-day readmissions, emergency return rates, and diagnostic turnaround times35.

## **Deep AI Integration and Model Context Protocol Server Layer**

Artificial intelligence functions most effectively when deployed not as an isolated chatbot, but as an integrated, protocol-bounded co-pilot embedded within clinical and administrative workflows27.

### **Architectural Integration of Model Context Protocol**

The Model Context Protocol (MCP), open-sourced by Anthropic, establishes an open standard for large language models and autonomous agents to query external tools, databases, and operational context via JSON-RPC messages20.  
In this architecture, an agent orchestrator—such as Claude Desktop or an open-source autonomous agent framework—communicates with an enterprise FastMCP server over JSON-RPC channels via standard input/output or Server-Sent Events20.  
The FastMCP server exposes specialized clinical and operational tools, mediating between the autonomous model and underlying hospital services22.

FastMCP Operational Tool Definitions:  
\- get\_patient\_clinical\_summary(patient\_id): Aggregates conditions, vitals, and lab results into a FHIR JSON context.  
\- check\_drug\_interactions(medication\_code, current\_medications): Identifies contraindications via a local pharmacological database.  
\- draft\_inpatient\_prescription(patient\_id, drug\_code, route, dose, frequency): Creates an unverified order in the EHR queue.  
\- query\_bed\_availability(department, ward\_type): Returns real-time bed availability from ERPNext or CARE.  
\- fetch\_pacs\_study\_metadata(accession\_number): Retrieves imaging series parameters and preliminary findings from Orthanc.

Security and non-repudiation are maintained by routing all MCP tool requests through Keycloak authentication, restricting agents to authorized role boundaries9.  
All agent write actions are non-destructive: orders, notes, and charge entries are created with a status of Provisional \- Agent Drafted, requiring explicit physical or biometric sign-off from a licensed clinician before affecting patient treatment or billing ledgers32.

### **Self-Hosted Local AI Runtime Infrastructure**

Data sovereignty and privacy mandates require running all AI inference on self-hosted infrastructure, avoiding external proprietary cloud APIs.  
The inference tier runs on vLLM or Ollama deployed across dedicated local GPU clusters.  
The primary conversational and reasoning models are open-weights foundation models, such as Llama 3.3 70B Instruct or Qwen 2.5 72B, supplemented by biomedical domain models such as Meditron or BioMistral61.  
These models are deployed using 4-bit or 8-bit quantization (AWQ/GPTQ), reducing VRAM requirements while preserving diagnostic reasoning and instruction-following accuracy.

### **Operational Clinical AI Workflows**

Ambient clinical scribing streamlines documentation during high-volume outpatient consultations27. A localized deployment of Faster-Whisper (Large-v3) transcribes doctor-patient discussions, with acoustic models tuned for Indian accents, regional languages (Hindi, Tamil, Kannada, Marathi, Bengali), and mixed English clinical phrasing.  
The local LLM processes the transcript against clinical templates, extracting symptoms, histories, and physical exam findings to assemble structured SOAP notes.  
The pipeline suggests relevant ICD-10 and SNOMED CT diagnostic codes and drafts outpatient prescriptions for one-click physician verification2.  
Critical care decision support follows protocol-bounded analysis rather than open-ended text generation34. Streaming telemetry from bedside patient monitors (SpO2, blood pressure, pulse, respiration) and laboratory feeds (arterial blood gas, lactate, creatinine) are evaluated continuously against validated clinical protocols from the Indian Society of Critical Care Medicine and Surviving Sepsis Campaign34.  
The system computes real-time early warning scores (NEWS2, qSOFA), alerting clinical teams to early indicators of septic shock, acute respiratory distress, or organ failure before decompensation occurs34.  
Radiology computer vision workflows integrate through MONAI Label running adjacent to Orthanc and the OHIF Viewer11. When new CT or digital X-ray series arrive in Orthanc, an asynchronous worker routes pixel data through specialized deep learning pipelines11.  
Models identify acute abnormalities—such as intracranial hemorrhages, pulmonary nodules, or pneumothoraces—and return findings to Orthanc as DICOM Structured Reports or Segmentation Objects, displaying visual overlays directly within the OHIF Viewer for radiologist validation11.

## **Healthcare Integration Engine and Middleware Architecture**

Coordinating specialized open-source platforms across clinical, diagnostic, and financial domains requires an enterprise integration engine to manage message transformation, interface routing, and protocol bridging5.

### **Transitioning from Mirth Connect**

NextGen Healthcare transitioned its Mirth Connect interface engine to a commercial, closed-source model, discontinuing upstream open-source security updates and protocol maintenance for its MPL 2.0 release13.  
Relying on deprecated open-source Mirth releases introduces long-term security vulnerabilities and compliance risks for acute healthcare infrastructure7.  
Hospitals transitioning away from legacy Mirth Connect can pursue two open-source alternatives:  
Open Integration Engine (OIE) and BridgeLink represent direct, community-driven continuations of the Mirth Connect codebase13. OIE is developed under an Eclipse Foundation working group, while BridgeLink is maintained and hardened by Innovar Healthcare13.  
Both projects retain the established channel configurations, JavaScript transformers, and administrative interfaces of Mirth Connect, making them drop-in replacements for existing HL7 v2 and ASTM pipelines without requiring workflow redesigns13.  
Apache Camel offers an enterprise-grade, code-first integration alternative10. Used within Ozone HIS, Apache Camel coordinates data exchange between OpenMRS, ERPNext, and OpenELIS through containerized, event-driven routes9.  
While it provides high throughput and cloud-native scalability, building complex HL7 v2 transformations in Camel requires greater programming expertise than using the visual channel designers in OIE or BridgeLink10.

| Interface Engine | Core Platform Lineage | Primary License | Interface & Design Paradigm | Protocol Support Scope | Deployment Architecture | Operational Fit |
| :---- | :---- | :---- | :---- | :---- | :---- | :---- |
| **Open Integration Engine (OIE)** | Direct fork of Mirth Connect 4.5.213 | MPL 2.013 | Graphical administrator client, channel design UI13 | HL7 v2/v3, ASTM, DICOM, FHIR, MLLP, TCP, HTTP13 | Standalone Java daemon / Container13 | Direct replacement for legacy Mirth setups63 |
| **BridgeLink** | Hardened fork of Mirth Connect 4.5.262 | MPL 2.013 | Graphical administrator client, channel design UI13 | HL7 v2/v3, ASTM, DICOM, FHIR, MLLP, Webhooks13 | Hardened enterprise container images62 | Production migrations needing commercial-grade stability64 |
| **Apache Camel (Ozone EIP)** | Top-Level Apache Software Foundation project10 | Apache License 2.0 | Code-first (Java DSL, Spring Boot, YAML routes)10 | 300+ components: FHIR, HL7, Kafka, JMS, AMQP, REST10 | Microservice embedded in Spring Boot containers10 | Cloud-native microservices and complex event pipelines10 |

### **Operational Transaction Lifecycle**

An end-to-end clinical workflow demonstrates how the integration layer coordinates diverse subsystems during patient care:  
The workflow begins when an attending physician orders a complete blood count and intravenous antibiotic therapy within the OpenMRS 3 or CARE clinical chart9.  
The clinical system records the encounter and publishes an event notification to the integration bus10.  
Apache Camel routes capture this event and broadcast it simultaneously across three operational pipelines10:  
In the laboratory stream, the integration engine transforms the clinical order into an HL7 v2 ORM^O01 message, forwarding it over MLLP to OpenELIS Global8.  
OpenELIS generates a laboratory accession barcode. When the sample reaches the pathology unit, an automated analyzer queries the LIS over ASTM, performs the assay, and returns raw numerical metrics4.  
OpenQC validates the run against Westgard rules46. Once approved, an HL7 v2 ORU^R01 result message returns through the interface engine, posting the verified findings to the patient's electronic health record5.  
In the pharmacy stream, the medication order appears in the central dispensing worklist, where ERPNext reserves the required medication batches using FEFO logic3.  
Upon physical dispensing, the inventory ledger updates stock balances automatically3.  
In the financial stream, pending debit lines for both the diagnostic test and pharmaceutical consumable post immediately to the patient's open billing account in ERPNext3.  
At patient discharge, the billing engine reconciles these debits against insurance pre-authorizations, applies relevant co-pay rules, and formats a finalized claim submission for TPA or AB PM-JAY settlement3.

## **Infrastructure, High Availability, and Open-Source Licensing**

Maintaining an enterprise hospital platform requires resilient infrastructure configurations and careful open-source license governance to ensure operational reliability and legal compliance10.

### **High-Availability Production Architecture**

Production reliability requires containerized orchestration across high-availability multi-node clusters managed via Kubernetes:  
An external ingress layer running Traefik or Nginx handles incoming network traffic, terminates TLS 1.3 connections, manages SSL certificates, and applies rate limiting to protect internal endpoints69.  
Traffic routes internally to three isolated application namespaces:

> * The clinical cluster hosts OpenMRS 3 microfrontends, the CARE EMR backend, the FastMCP AI server, and the Open Integration Engine6.  
> * The diagnostic cluster runs the Orthanc PACS daemon, OHIF Viewer instances, OpenELIS Global, and local vLLM/Ollama GPU inference workers11.  
> * The operational cluster manages ERPNext, Keycloak IAM, Nextcloud Hub, Stalwart Mail, and Matrix Synapse messaging9.

Data persistence is decoupled from application containers across dedicated high-availability storage clusters:

> * Relational data is managed by a high-availability PostgreSQL cluster orchestrated via Patroni, using etcd for distributed consensus, automated leader election, and streaming replication. MariaDB deployments for ERPNext utilize Galera Cluster for multi-master synchronization.  
> * Unstructured data—including raw DICOM image series, clinical PDF attachments, and office document files—is stored in a distributed MinIO or Ceph object storage cluster providing S3-compliant endpoints with encryption at rest.  
> * Disaster recovery is maintained through automated backup utilities such as Restic or pgBackRest, taking differential database snapshots and object storage backups every six hours and replicating encrypted archives to an off-site recovery site69.

### **Open-Source License Governance and Commercial Viability**

To ensure the platform remains completely open-source, every component must carry a recognized open-source license that allows unrestricted commercial use, modification, and self-hosting without recurring software fees2.

| Software Component | Target Software | Applied License | Commercial Usability | Statutory Obligations and IP Implications |
| :---- | :---- | :---- | :---- | :---- |
| **Clinical Core (EHR)** | Ozone HIS / OpenMRS | Mozilla Public License 2.0 | Unrestricted2 | File-level copyleft; core engine modifications must be disclosed, but independent modules may retain separate licensing31. |
| **Acute Care (EMR)** | CARE (OHC / 10BedICU) | MIT License | Unrestricted34 | Fully permissive; allows unrestricted modification, deployment, and commercial customization34. |
| **Enterprise Backbone** | ERPNext | GNU General Public License v3 | Unrestricted9 | Strong copyleft; source modifications must remain under GPLv3. Custom apps created on the Frappe framework remain independently licensable9. |
| **PACS Server** | Orthanc | GNU GPLv3 (with exceptions) | Unrestricted | Strong copyleft; backend modifications must be published, but standard REST and DICOMweb API clients are exempt. |
| **Diagnostic Viewer** | OHIF Viewer v3 | MIT License | Unrestricted11 | Permissive; permits complete white-labeling, commercial distribution, and proprietary extension development11. |
| **Laboratory Core** | OpenELIS Global | Mozilla Public License 2.0 | Unrestricted16 | Weak copyleft; allows straightforward integration with external hospital frameworks while protecting core stability16. |
| **Laboratory LIMS** | SENAITE | GNU General Public License v2 | Unrestricted17 | Strong copyleft; all derivative modules and extensions must remain published under GPLv217. |
| **Integration Engine** | BridgeLink / OIE | Mozilla Public License 2.0 | Unrestricted13 | Free from the commercial licensing restrictions introduced in newer versions of Mirth Connect13. |
| **Identity / IAM** | Keycloak | Apache License 2.0 | Unrestricted9 | Fully permissive; unrestricted enterprise integration without copyleft obligations9. |
| **Productivity Core** | Nextcloud Hub / Collabora | AGPLv3 / MPL 2.0 | Unrestricted | Network copyleft applies to remote modifications, safeguarding open-source integrity for self-hosted deployments. |
| **Enterprise Messaging** | Matrix Synapse / Stalwart | Apache 2.0 / AGPLv3 | Unrestricted | Permits self-hosted deployment, regional server federation, and programmatic automation via webhooks. |
| **AI Agentic Layer** | FastMCP SDK | MIT License | Unrestricted22 | Permissive; supports open or commercial agentic workflow development without platform licensing fees22. |

## **Strategic Blueprint and Implementation Roadmap**

Eliminating proprietary software across an Indian tertiary hospital network requires a methodical deployment strategy. Rather than attempting a high-risk transition across all systems at once, implementations succeed by rolling out capabilities in phased, manageable stages1:  
The foundational deployment phase focuses on core infrastructure and identity management. This includes standing up the Kubernetes environment, deploying Patroni PostgreSQL and MinIO storage, and establishing Keycloak as the centralized SSO and IAM provider9.  
The primary enterprise backbone, ERPNext, is introduced alongside the integration middleware (Open Integration Engine or Apache Camel) to manage basic accounting, human resources, procurement, and warehouse inventory3.  
The clinical deployment phase introduces patient-facing and departmental operations. The Ozone HIS (OpenMRS 3\) or CARE platform is rolled out across outpatient registration, triage, and inpatient wards6.  
The ABDM integration subsystem is activated, bringing Milestone M1 (ABHA creation and Scan & Share QR registration) directly to patient registration desks18.  
Simultaneously, the central pharmacy and store management workflows are connected to ERPNext, ensuring prescription-driven dispensing and automated inventory tracking3.  
The diagnostic deployment phase connects medical imaging and laboratory systems. Orthanc PACS and the OHIF Viewer are installed and connected to imaging modalities via DICOMweb and DIMSE protocols11.  
OpenELIS Global is deployed across pathology departments, with the integration middleware establishing bidirectional ASTM/HL7 links to automated hematology and biochemistry analyzers4.  
Analytical quality control is standardized through the OpenQC engine, enforcing Westgard rules and Levey-Jennings monitoring to satisfy NABL ISO 15189 standards16.  
The advanced clinical phase extends operations into high-acuity environments. Specialized modules are configured for Intensive Care Units (integrating CARE's TeleICU capabilities), Operating Theatres, Cath Labs, Hemodialysis units, and Assisted Reproductive Technology clinics compliant with the ART Act 20216.  
Milestones M2 and M3 of ABDM are brought online, enabling encrypted FHIR document sharing (as a Health Information Provider) and longitudinal record retrieval (as a Health Information User)8.  
The optimization phase activates the local artificial intelligence layer. Dedicated GPU inference nodes running vLLM or Ollama are deployed within the local network, hosting quantized open-weights models like Llama 3.3 70B or Qwen 2.5 72B61.  
The FastMCP server connects these models to underlying clinical and operational tools via JSON-RPC, automating ambient clinical scribing, ICU early warning detection, and MONAI-assisted radiology triaging under strict human-in-the-loop review11.  
By following this architectural blueprint, healthcare institutions in India can establish a resilient, standards-compliant, and fully open-source digital platform—freeing hospital systems from vendor lock-in while maintaining the performance, safety, and compliance required for modern tertiary patient care1.

#### **Works cited**

> 1. Top Open Source EMR & EHR Software Platforms in 2026, [https\://www\.trootech.com/blog/the-best-open-source-ehr-emr-software](https://www.trootech.com/blog/the-best-open-source-ehr-emr-software)  
> 2. Guide to Global Health Management Information Systems (HMIS), [https\://intuitionlabs.ai/articles/global-health-information-systems](https://intuitionlabs.ai/articles/global-health-information-systems)  
> 3. ERPNext Healthcare – Comprehensive Analysis \- ClefinCode, [https\://clefincode.com/blog/global-digital-vibes/en/erpnext-healthcare-and-marley-comprehensive-analysis](https://clefincode.com/blog/global-digital-vibes/en/erpnext-healthcare-and-marley-comprehensive-analysis)  
> 4. Sysmex pocH-100i Bi-Directional LIS Interfacing Guide \- AILS, [https\://ailaboratorysystem.com/integrations/sysmex-poch-100i-hematology-guide](https://ailaboratorysystem.com/integrations/sysmex-poch-100i-hematology-guide)  
> 5. The Vertical Nervous System Blueprint \| Trigonal Solutions, [https\://trigonaltechnology.com/solutions](https://trigonaltechnology.com/solutions)  
> 6. Open Healthcare Network \- GitHub, [https\://github.com/ohcnetwork](https://github.com/ohcnetwork)  
> 7. Hospital Management Software Cost: 2026 Pricing Guide \- Litslink, [https\://litslink.com/blog/hospital-management-software-development-cost](https://litslink.com/blog/hospital-management-software-development-cost)  
> 8. Bahmni support for Open Global Standards \- Confluence, [https\://bahmni.atlassian.net/l/cp/jmFCp6eZ](https://bahmni.atlassian.net/l/cp/jmFCp6eZ)  
> 9. Discover \- Ozone HIS, [https\://www\.ozone-his.com/discover](https://www.ozone-his.com/discover)  
> 10. EIP Client \- Ozone Docs, [https\://docs.ozone-his.com/devs/eip-client/](https://docs.ozone-his.com/devs/eip-client/)  
> 11. OHIF Viewer 2026: Free Web Medical Imaging Explained, [https\://valuestreamai.com/blog/ohif-viewer-free-web-medical-imaging-2026](https://valuestreamai.com/blog/ohif-viewer-free-web-medical-imaging-2026)  
> 12. OHIF \+ Keycloak but wihtout docker \- Support, [https\://community.ohif.org/t/ohif-keycloak-but-wihtout-docker/1416](https://community.ohif.org/t/ohif-keycloak-but-wihtout-docker/1416)  
> 13. Mirth Connect \- Wikipedia, [https\://en.wikipedia.org/wiki/Mirth\_Connect](https://en.wikipedia.org/wiki/Mirth_Connect)  
> 14. ERPNext for Healthcare: Hospital Management System \| Ksolves, [https\://www\.ksolves.com/erpnext-services/erpnext-for-healthcare](https://www.ksolves.com/erpnext-services/erpnext-for-healthcare)  
> 15. OHIF Viewer: When to Extend It or Build a Custom Viewer \- PYCAD, [https\://pycad.co/blog/ohif-viewer-overview/](https://pycad.co/blog/ohif-viewer-overview/)  
> 16. Understanding Open-Source LIMS: Core Functionalities \- IntuitionLabs, [https\://intuitionlabs.ai/articles/open-source-lims-functionalities](https://intuitionlabs.ai/articles/open-source-lims-functionalities)  
> 17. 7 A Framework Design for Information Management in Heritage, [https\://dspace.uevora.pt/rdpc/bitstream/10174/30398/1/R\_Bordalo\_C\_Bottaini\_A\_Candeias\_2021\_A\_F.pdf](https://dspace.uevora.pt/rdpc/bitstream/10174/30398/1/R_Bordalo_C_Bottaini_A_Candeias_2021_A_F.pdf)  
> 18. care\_abdm/ABDM\_INTEGRATION.md at develop \- GitHub, [https\://github.com/10bedicu/care\_abdm/blob/develop/ABDM\_INTEGRATION.md](https://github.com/10bedicu/care_abdm/blob/develop/ABDM_INTEGRATION.md)  
> 19. NHA-ABDM/ABDM-wrapper \- GitHub, [https\://github.com/NHA-ABDM/ABDM-wrapper](https://github.com/NHA-ABDM/ABDM-wrapper)  
> 20. FHIR MCP Server \- wso2 \- GitHub, [https\://github.com/wso2/fhir-mcp-server](https://github.com/wso2/fhir-mcp-server)  
> 21. FHIR-MCP is an open-source MCP (Model Context Protocol) server, [https\://github.com/xSoVx/fhir-mcp](https://github.com/xSoVx/fhir-mcp)  
> 22. the-momentum/fhir-mcp-server \- GitHub, [https\://github.com/the-momentum/fhir-mcp-server](https://github.com/the-momentum/fhir-mcp-server)  
> 23. Best Hospital Management System in India (2026): HMS Compared, [https\://www\.tridevtechnologies.com/best-hospital-management-system-india](https://www.tridevtechnologies.com/best-hospital-management-system-india)  
> 24. ABDM \- Frappe Health, [https\://frappehealth.com/docs/v14/user/manual/en/healthcare/abdm](https://frappehealth.com/docs/v14/user/manual/en/healthcare/abdm)  
> 25. Open-Source Electronic Health Record Systems for Low-Resource, [https\://pmc.ncbi.nlm.nih.gov/articles/PMC5703976/](https://pmc.ncbi.nlm.nih.gov/articles/PMC5703976/)  
> 26. View Source, [https\://openmrs.atlassian.net/wiki/plugins/viewsource/viewpagesrc.action?pageId=300613733](https://openmrs.atlassian.net/wiki/plugins/viewsource/viewpagesrc.action?pageId=300613733)  
> 27. The Bahmni Community Newsletter, [https\://www\.bahmni.org/campaigns/view-campaign/ew6hzFmR3lUlsnjDT9NHCtZ-txnXHznXsIGXB6snPsfDS6emCiV2Wx9SzLWZFXxNa-SP7qf0c7nB\_mpkrt8rcOjYYgi-mYEm](https://www.bahmni.org/campaigns/view-campaign/ew6hzFmR3lUlsnjDT9NHCtZ-txnXHznXsIGXB6snPsfDS6emCiV2Wx9SzLWZFXxNa-SP7qf0c7nB_mpkrt8rcOjYYgi-mYEm)  
> 28. Introduction to Bahmni Hospital management system \| PDF, [https\://www\.slideshare.net/slideshow/bahmni-hospital-management-system/28525018](https://www.slideshare.net/slideshow/bahmni-hospital-management-system/28525018)  
> 29. awesome-healthcare/README.md at master \- GitHub, [https\://github.com/kakoni/awesome-healthcare/blob/master/README.md](https://github.com/kakoni/awesome-healthcare/blob/master/README.md)  
> 30. Configure Apps \- Ozone Docs, [https\://docs.ozone-his.com/implementers/configure-apps/](https://docs.ozone-his.com/implementers/configure-apps/)  
> 31. OpenMRS \- Grokipedia, [https\://grokipedia.com/page/OpenMRS](https://grokipedia.com/page/OpenMRS)  
> 32. Open Healthcare Network Foundation \- Open Health Infrastructure, [https\://ohc.network/](https://ohc.network/)  
> 33. 10BedICU, [https\://10bedicu.org/](https://10bedicu.org/)  
> 34. CARE AI Modules \- 10BedICU, [https\://10bedicu.org/care-ai-modules](https://10bedicu.org/care-ai-modules)  
> 35. Open Source ERP Software for Healthcare Industry \| ERPNext, [https\://frappe.io/erpnext/for-healthcare](https://frappe.io/erpnext/for-healthcare)  
> 36. mekomsolutions/ozone-ha \- GitHub, [https\://github.com/mekomsolutions/ozone-ha](https://github.com/mekomsolutions/ozone-ha)  
> 37. Healthcare ERP \- Frappe Healthcare \- Sanskar Technolab, [https\://sanskartechnolab.com/healthcare-erp](https://sanskartechnolab.com/healthcare-erp)  
> 38. EMR Software in India: ABDM-Ready EMR Solution \| ICG, [https\://ichelonconsulting.com/emr-software-india](https://ichelonconsulting.com/emr-software-india)  
> 39. Modern Medical Imaging Software in 2026: AI, PACS, DICOM and, [https\://www\.decipherzone.com/blog-detail/modern-medical-imaging-software](https://www.decipherzone.com/blog-detail/modern-medical-imaging-software)  
> 40. How to setup Ohif viewer, dcm4chee and keycloak, [https\://community.ohif.org/t/how-to-setup-ohif-viewer-dcm4chee-and-keycloak/1302](https://community.ohif.org/t/how-to-setup-ohif-viewer-dcm4chee-and-keycloak/1302)  
> 41. Secondary use of radiological imaging data: Vanderbilt's ImageVU, [https\://pmc.ncbi.nlm.nih.gov/articles/PMC13275161/](https://pmc.ncbi.nlm.nih.gov/articles/PMC13275161/)  
> 42. GitHub \- ozone-his/eip-openelis-openmrs: Camel routes that, [https\://github.com/ozone-his/eip-openelis-openmrs](https://github.com/ozone-his/eip-openelis-openmrs)  
> 43. Data Deletion & GDPR Erasure Guide \| AILS LIMS, [https\://ailaboratorysystem.com/data-deletion](https://ailaboratorysystem.com/data-deletion)  
> 44. Sysmex CS-2500 Bi-Directional LIS Interfacing Guide \- AILS, [https\://ailaboratorysystem.com/integrations/sysmex-cs-2500-coagulation-lis-guide](https://ailaboratorysystem.com/integrations/sysmex-cs-2500-coagulation-lis-guide)  
> 45. ISO15189:2022 (NABL) compliance? \- Bahmni \- OpenMRS Talk, [https\://talk.openmrs.org/t/iso15189-2022-nabl-compliance/40889](https://talk.openmrs.org/t/iso15189-2022-nabl-compliance/40889)  
> 46. OpenQC is a local first quality control platform for PCR and ... \- GitHub, [https\://github.com/ashuein/OpenQC](https://github.com/ashuein/OpenQC)  
> 47. FAQs \- Frequently Asked Questions \| ABDM \| NHA, [https\://abdm.gov.in/faq/aarogyasetuapp](https://abdm.gov.in/faq/aarogyasetuapp)  
> 48. GitHub \- remonalbear/CMMS: Computerized Maintenance, [https\://github.com/remonalbear/CMMS](https://github.com/remonalbear/CMMS)  
> 49. Instrument Sterilization Baskets \| Stainless Steel Medical Cleaning, [https\://www\.electrosurgicals.com/blog/insights-4/instrument-sterilization-baskets-guide-7](https://www.electrosurgicals.com/blog/insights-4/instrument-sterilization-baskets-guide-7)  
> 50. Surgical & Sterile Processing \- Tải về, [https\://surgical-sterile-processing.updatestar.com/vi](https://surgical-sterile-processing.updatestar.com/vi)  
> 51. Diploma in Medical Equipment Technology.pdf \- NCAHP, [https\://ncahp.abdm.gov.in/Curriculum/Diploma%20in%20Medical%20Equipment%20Technology.pdf](https://ncahp.abdm.gov.in/Curriculum/Diploma%20in%20Medical%20Equipment%20Technology.pdf)  
> 52. Shridharshukl/Blood-Bank-Management-System \- GitHub, [https\://github.com/Shridharshukl/Blood-Bank-Management-System](https://github.com/Shridharshukl/Blood-Bank-Management-System)  
> 53. python and Django based Blood Bank Management System \- GitHub, [https\://github.com/naemazam/Blood-Bank-Management-System](https://github.com/naemazam/Blood-Bank-Management-System)  
> 54. sumitkumar1503/bloodbankmanagement: Blood Bank Management, [https\://github.com/sumitkumar1503/bloodbankmanagement](https://github.com/sumitkumar1503/bloodbankmanagement)  
> 55. bloodbank-software \- GitHub, [https\://github.com/bloodbank-software](https://github.com/bloodbank-software)  
> 56. eRaktKosh \- A Blood Bank Management System \- GitHub, [https\://github.com/iamchiranjeebpanda01/eRaktKosh](https://github.com/iamchiranjeebpanda01/eRaktKosh)  
> 57. The Ayushman Bharat Digital Mission (ABDM): making of India's, [https\://pmc.ncbi.nlm.nih.gov/articles/PMC10064942/](https://pmc.ncbi.nlm.nih.gov/articles/PMC10064942/)  
> 58. ABDM Integration Service Provider: M1, M2, M3 \| Nirmitee.io, [https\://nirmitee.io/healthcare-interoperability/abdm-integration-services/](https://nirmitee.io/healthcare-interoperability/abdm-integration-services/)  
> 59. Ayushman Bharat Digital Mission in Rajasthan, [https\://sansad.in/getFile/loksabhaquestions/annex/184/AU4830\_XIdzor.pdf?source=pqals](https://sansad.in/getFile/loksabhaquestions/annex/184/AU4830_XIdzor.pdf?source=pqals)  
> 60. FHIR MCP Server: Use Cases for Healthcare Developers, [https\://dev.to/momentumai/fhir-mcp-server-use-cases-for-healthcare-developers-4c5i](https://dev.to/momentumai/fhir-mcp-server-use-cases-for-healthcare-developers-4c5i)  
> 61. Alibaba's AI model tried to 'secretly' reach China server: Security risk or training bug?, [https\://indianexpress.com/article/technology/artificial-intelligence/alibaba-qwen-model-security-risk-or-training-glitch-china-10938887/](https://indianexpress.com/article/technology/artificial-intelligence/alibaba-qwen-model-security-risk-or-training-glitch-china-10938887/)  
> 62. GitHub \- Innovar-Healthcare/BridgeLink: The open-source backbone, [https\://github.com/Innovar-Healthcare/BridgeLink](https://github.com/Innovar-Healthcare/BridgeLink)  
> 63. Eclipse Open Integration Engine \- Open Source Healthcare Integration, [https\://openintegrationengine.org/](https://openintegrationengine.org/)  
> 64. BridgeLink® Standard Edition an Open Source Mirth Connect Fork, [https\://aws.amazon.com/marketplace/pp/prodview-6nb2pe66ylagc](https://aws.amazon.com/marketplace/pp/prodview-6nb2pe66ylagc)  
> 65. Eclipse Open Integration Engine, [https\://projects.eclipse.org/proposals/eclipse-open-integration-engine](https://projects.eclipse.org/proposals/eclipse-open-integration-engine)  
> 66. mirth-connect · GitHub Topics, [https\://github.com/topics/mirth-connect](https://github.com/topics/mirth-connect)  
> 67. BridgeLink: Open-Source Mirth Connect Fork Guide (2026), [https\://nirmitee.io/blog/bridgelink-open-source-mirth-connect-fork-migration-guide/](https://nirmitee.io/blog/bridgelink-open-source-mirth-connect-fork-migration-guide/)  
> 68. Open Integration Engine \- GitHub, [https\://github.com/OpenIntegrationEngine](https://github.com/OpenIntegrationEngine)  
> 69. MSF-OCG/LIME-EMR: OpenMRS 3 Distribution for Doctors ... \- GitHub, [https\://github.com/MSF-OCG/LIME-EMR](https://github.com/MSF-OCG/LIME-EMR)  
> 70. PrefectHQ/fastmcp: The fast, Pythonic way to build MCP servers and, [https\://github.com/PrefectHQ/fastmcp](https://github.com/PrefectHQ/fastmcp)

[image1]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAwAAAAWCAYAAAD0OH0aAAAAyUlEQVR4XmNgGAXDAswG4gVoYrlAfBJNDAxYgPgLEE9EEz8BxNvRxMCAZA3mQPwfiEORxLiA+BcQ1yOJwUEZA0SDBJKYA1TMDUkMDrYA8W00sVog/gfE/GjiDExA/J4BM4R2AvEVKNsSWUKfAWJ1M5IYyNRPDJCgZgbifUhyDDkMEA1roXweIF4PxD+BuAmIfYG4AioHBquA+A0QH2eARNJ+IHYG4iQGiL+2MkAMgYNnQLwCWQAfUGaAOCcbXQIXiGOAaNBBl8AGAP33KP1KsC54AAAAAElFTkSuQmCC>

[image2]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAwAAAAWCAYAAAD0OH0aAAAAoklEQVR4XmNgGAXDAsQC8UUg/gfE/9HwNiR1YDABiH8A8XQgrgPih0D8C4gLgbgAiK0QSiGAJA0gp4AkkQW9GCBOMUASAwMmIH4ExIvQxE0ZIBpc0cQZLKESfmji8VBxbTRxhjiohASa+BoGiD8Y0cQZfBggGtiRxBSB+DsDxDAMwAPEzxkQThIF4hNA3AdXgQUYAfEhID4MxAeBOApVelADAMcAJmar0FtkAAAAAElFTkSuQmCC>

[image3]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAmwAAABLCAYAAADNo9uCAAAEUElEQVR4Xu3dW6jlUxwH8IUxmpRcakTKpUg01Dyh4UEiHsgLTaFcEsV4UCI1eCANEg9Mcg0PLiWmeFKaXCKTRjI8iBLNIDEuoRG/1dq7//+sOefsyzl7n73nfD71bf//v/X/n8t++vW/rJUSAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAADAEno7sjHyUGRLZMXMYQAAltLayMORrZ39NZFjm2EAABZisRqr7ZE7O9t3tAcAABjcPZH/Ijs6n4thdyq3QY9L5Wc+OmMUAICBrE7lytoNafEatqc7nysj70bWt8YAABjSYjZsAACMgIYNAGDCadgAACachg0AYMIN0rBti3wf2RnZFfmhkx+r/NRHAADo0yAN26pUjs15uRrrJb85emHk31TOP2rmMAAAcxmkYcv+Sk3Tdm011o9TIh+kMg8cAADzuC41jVc7vewXeTM1x+crZ8PITRsAACOSJ93tNmwPVmP9Or0uAABMoiMje2bJve2DJlRe6L3btOUGDgCACXRyapq2W6oxAAAmRLdhyy8jAADscz6M3BY5qbOdF0x/MfX38P+kOCw1TdvmamwYl0VOizwb+S1yeeSsyHOtYwAAxuaB1nZueHKjMt/bmvelciWrV/7snjAm3bnVci6pxgZxROSbzvbNqfkeciOrYQMAltT6yFN1cYS6zdV8GcSKyPupOTdP/bFQX6a5/45NkVPrIgDAKD0RubIuTpnjU9Ow7V+NDSP/nK/qIgDAOB0QeT5yYJp5JencyCut/bb7095TgMyWf7onjFH+f76LHFMPDOCMyBud7fydXN0aW9P5vDvyeKsOADAyF0VuipwQ2R45JJUH+B9rHzQlbk2lUVyojyKfpjIPXW7Y8nNs56dye7Qr334d5+1jAGAZy1ekdkW+iBwd+SWyo1OfNvmlgxvr4hDyFbbPIk9Gro98G3k9lfneuvJ3ta61DwBADzvT4rxk0K+8Bmlu2DbUAwAA7O3QNP5F3G+PPJKm80okAMBY5Rcmfq6LA3ivLgAA0MhXqdam8mD/O6msA7oq8nnk8OawOV0TuaIu9ilP/7E18mo9AABA4+vIiak0bHkpqK6XIue09mdzXhpu2pD8nNuZkb9T+b0LWREBAGBZyFNjtOd/OziVZa3ytBlzWZ1mroawkAAA0ENeRaDdOF1a7c/mtVSmH/k1lYXZf++RfMzuVvJ53QAA0ENuzp5p7f+RSjO2MnJQqw4AwBLJDdtV1f4LafrXNAUA2GfkFwza8vqceZqOs6s6AABTKr+ckNdA7dcFkc11EQCA0cnTdGyqi3O4K5U53j6uBwAAmBx5QXcNGwDAGL2VynNv/dKwAQCM0cWRLalMvtsvDRsAwJjtiayri/PIDdu2uggAwOhsTIM3bJ/URQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAYJn4Hxop/FTeFBPMAAAAAElFTkSuQmCC>

[image4]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAmwAAABPCAYAAABWMpmUAAAHLUlEQVR4Xu3dWYglVxkH8E/HJS6Ju8YtiYrigEZEhMS4EHdFXB+MW1AU9MEVAsYoEvRFQR03RFyIMEYDMSJuuOBDwEQFjQZ8cCeKUZOIcY+gUc/HuUXXPd19u+6dqe7q278f/Onbp253dU0P9J9azokAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAADgIPtpOwAAwLQobAAAE3Z+KGwAAJN2RShsAACTdW7J/2K9C9vzSr5QcrjkSMl3S+439w4AgAm7NNa/sB0tuXfJRSWHSn5Uct7cOwAAJizL2roXtvSB3uubS+7T+xwAYNJ2s7A9oR3YRT/uvf5myS17nwMATNbDYvzCdnnU73/t7ONeeFzMH99XSy7sfQ4AMFlXxviFrXNJ7F1hAwDYl+5e8t9Q2AAAJutzsVHWFDYAgAn6RyhsAACT1i9r61DYflvyu5I/lFxXcv0sN/TyxwExPxsAMBnrVtj6x/KGkjN6ObPkMSVnRX1i9EklTy95fdQnRrPgdV/7m5JbBQDAHrsg1q+w3bHk57FxPKu4W9SvzalIAAD2TPt06NiF7bYlJ0VdYD73k0tEZbkaS3c8fyl5YLNtqPe0AzN52fX0dnDCrmoHinuWfCLq79uZRACYqFfE5rI2ZmH7dGzeV2Ys34qNfeQi76u4XckJzdhdSl7bjE3dZ2Pzig5fKXlQySNKjjTbAICJaJ8OHbuw7YXnxMZxnd1sW8WjYtySuapcbus/Tb499456pjHPqnbyoYx3zF7nMVlTFQAmqC1q61jYUndceRnzWOWcdb9uByfgASUPbXLq3DtqiXtrM9bJByxu0Q4CAHuvLWrrWtgeXnJT1GN7dbNtWfk9zmkH94n3R/35W79qBwCArV1WcmPJd9oNI8kb5tuitq6FLeU9Z3ls/yw53GxbRn6PU5uxPDP1pqiXHPO+sJwuJC9RHu2/aUT9/d8/6v7z/1K7/yyabWHLB0HyXsZ86MAZNgBY4ONR/1jmzex52Wo7eU/SvwZkiP60F23WsbClnEy3O8ZVbXVZNUv2odnrLN45p1s+qHAs+1lGf/+5z9z/v2ev+05pxi6M+mDG60p+0BsHALZwdckzoz7F975m21jaknYQCtuTY+MYn9ZsGyrPnLWe1Xv9s6jFKH2wNz6m/v7z2HL/H47N+z8x5gtb/3d+ZW8cANjCg2PjD2deyhrbS2JzSevnJxtv3aR9715nWd+P+nV5k/3JzbYh8uu38+LY/me6b8l728Gei0u+tkPy97ZI7v+T7WBPXvbc7ucDABbIS6B531N6eSz+g5qXvtqpG7bKIudEPfPSFp9+1vUMW3pb1MuFq8o1RltfjDqf2S9j/vf3i97rMfX3n0+LpjvF5v0/JBb//wIAtpF/QPPyVXpLyd9628aQ9yrlmaW8dNcWtYNQ2HJlh1e1g0u4Oepkup27Rv036+Z7y6dRO++efczVHLJsn9vbdry0+++8Mzb233l8KGwAsJJ3lVwT9Wb2J85vGtVBe0o03RDH/iRk/vuc14zlvGw5Ce1top69vKbkQ73tz496Zu5lvbHjqb//P0Xdfz7A0so55P7cDgIA09YWtf1Q2FY9S5VnotqZ/1dxbcmX28EBcsqN27eDu+z3JRe1gwDAtLVFbaqFLS9D5s/VTX67rM/E1veerSLvDbu+HdxBFrW8HHlBu2GXfakdAACmLy+ltWVtioUtl1nKS3x52W+VwpZz053VDg6U95/doRl7YclpzdhOLom9XTA+p43pphsBAPaRR8fmsjbFwtZZtrDlXGvH8kToa6I+pLCVfFDk/HZwwsa6fw4A2AU5N9iyhe17JSeVXFfykZJ7zcaXKVOrWKaw5bxnX48699iy3h71Emru60izDQBg150Zyxe2584+5nuzsHWGlqlVDS1seQnzhyUviLq6wVZ5SslTS55R8uySN0Z9irJdsuuRAQAwAW1hW7TSQScvseUaqH2LytRpJY/dIbniwyJDC1tObZHTV+STmX8t+fuC5Lx3XfK9mfy6TJY4AIBJaAvbTmfYUi6B9NLe5/eIWqi28+bYvNRSm3ai19bQwgYAsHbysuYyhS3vD2uL0+UlZ5R8PuoUFmNQ2ACAA+vsWO6S6ItivjjdOepcaSmnClnlRv8hFDYA4ED7aAw/w5ZTWrSl7pVRl0QaQy7b1S+UXQAADpRDMbyw7Td5/9wQ34h6aTcnywUAmKR1LWzLyAcpFDYAYLJyKot1KmyHSy4uOb3dsIDCBgBM2tEY9tDBfpHHc+vYWMPzYyWf2iYdhQ0AmLQTSm6K9Slsqb8SwxBZ2E5sBwEApiSXZ1qXS6JZQG+MurzUKTH8DFuukwoAMGnrcobt5JKrY/hEvpdFXUv0iqjFFQBgsq5qBwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIDj5//pv1heH6ulMQAAAABJRU5ErkJggg==>

[image5]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABkAAAAVCAYAAACzK0UYAAABH0lEQVR4Xu2UMUsDQRBGB0kaGwmWKZQg2gTBLhEVYiX4D2ys4z+wDBZaBVIkhdhYaKOFiKKWYiVW9tqLCLYKgr5h9rx1rO6IRSQPHtzN3N23t7e3IkMGhQlf6BejOI+neOZ6faGJz3iOH/JHITFvMgzJwP8K0VXmKWMHD/EWaz/b2dCQC1+EI1wJxxv4IvZv5UJDLn0RTrAVjnVH+MTltJ0NDbnyRceiWMhUVNvELexhJar/ooDveO0bjmPcjc7nJH37fWxEvW9W8RFfxUaoPuEDlqLrlDU8EBtQwqzYdnSH61E9F3Xs4gjOBJUqLuCO2EyMhXpmJsU+vgbpbt3GabGlrCtNg4t4H67PxY2kU6nq9OhDx8W+wzbu4VJyQ8IXgMw61eOToJUAAAAASUVORK5CYII=>

[image6]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACQAAAAVCAYAAAAuJkyQAAABmklEQVR4Xu2VTShEURTHj4XvRFmISMpHYqPIEhvJztrawopYWbKxtLNTZCUSUZOPSGGFiCx8lGJDWWBBSfyPc2fmvjOv3h3zxmp+9au555zXO+/MffcRZciQPrLgnA76wHVj8A6+wD3YYReERQ7c1EEfJkgaz4VV8AB+wW67KAzy4JYOKkrgIyyyYrUkDd1YsVBwaagLfsNlFb828QYVTwluaFsHFS0kN75X8WMT53xouDTE9MJma8177w2+k/evTBnXhjT9JNOZ1glQDRfhAozAKW86zgo8VZ6RPKmOs6NyWQL58BaeU+J0+C28gn1mzW/hB6yJVQTwlwnNkGzoCp0ArSSTG4SFsBNOkpxjTiTb0ADJdCp1wlAKn0ma+oRrsNxTEUAyDbWTTIb3SJQRWGetGT4GxuEOSWOznmwArg2VwRNYr+L8CSk2v4fgK2yMp3/PuHlrHYhLQ9lwHz7BC+MlyXeNT/AofHDuwgKz5tP8AbbFKhRL8EjJhxs/lY6zw3IZ9ZCM3s9DU8M0wQ24SrJ31ilNH+AM/84PNZVf/KvjIYYAAAAASUVORK5CYII=>

[image7]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABkAAAAVCAYAAACzK0UYAAABHklEQVR4XmNgGAVDBcijC1ALcAGxFRBvAuItaHJUAZlA/AqItwLxHwYaWYIMfjCMWkICGF6WgFIZOuAD4j4g7gbiPUBshypNGgBZsh1dEAjqgXgelJ0DxC+AmAkhTRoAWbIDXRAIooB4BpSdBsTfGCi0ZCe6IBoABSco2JBBJRA3A/E0IFZCk0MBLED8E4gPoktAgTYDJMiOA7EwkrghA8L3C4HYEUkODryB+C4Qvwfi/1AMCvM7QCyIpA4GUoD4PgNCTo8BUhydBuJ4mCJyQBYDwoWaDBCHhEP5OkBsA8QdDJCQ4IeKkwxuA3EplO0JxL+BWAuILYD4DQMkEbAC8UWoGrIAyKVLGCApbD8Qe0HFQXEDiod2IJ7DgCX/AABqrTqj0VWgigAAAABJRU5ErkJggg==>

[image8]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACQAAAAVCAYAAAAuJkyQAAABoUlEQVR4Xu2VTStFQRjHH3l/CaUICykl2SILxYqVBVIWvoBSFAsLGxRLX+HKSiSi5CVsJEJedopuopSkSAnF//HMOXfONHXOybFQ91e/xfznOXeeO2eaQ5Qkyd+RAmfM0EIqHIEn8EjZ7amIiAy4YYYWxmEMpqnxJPyCvU5BVGTBTTO0EIefsEqNG0ga4h2LlKAN7cFXWKHGjSQNHboVEcENbZmhhUxYqI37SRoa1bJICNqQDr+2G7gLs71TvydMQwXwAD7AC1jsnXbh1zoP5+AanPZOJ1iCp4Zn8MWSs0PymJUp+ASbjJxf7SXsUONW+AYr3QofwuyQTg7JQvcwX8vrSM5WH8yFLSTN830XiCAN8Y+1wXIjj5Ms3q5lRfBR5R9wBZZq874EaaiHZIFzI79TeZeRV8MxuE0yH/PM+hCmIT6gDnkkO/AOS1Q2AJ9hjVNEcsfNamNfgjTE5+WKZMF0lU2QNDnsFIFFuENSz/D1cAvr3QqDBUp8GB2PSf6VmbOD8tgPZSRbz/fPNdyHndo8UwvX4TLJ2VmFzZ6KJP+Vb6cYXtiwrVrLAAAAAElFTkSuQmCC>

[image9]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABkAAAAVCAYAAACzK0UYAAABeklEQVR4Xu3UvSvFURzH8a+nwkAxGOQhiZLIhjxcoiwyGJSSScpmNIoiRRkYsBhYGEyS1SApRWTBX6CUGOTx/b3nXPfc46rrd68y+NSre37fc+75PZ3zE/nPX04vzvBgf0eQFjMiyXThBEXIwxLeMe0OSjaHaHeOM3CNN5Q49cDRCV9xg3ynvirmbkadWuCk407MhJVOfd7Wxp1aUmlAt1fbF3MSv56ylOIZF2Lu9FeyiXvU+R2pyhAeEfLqxVgUcwFHaIztTjz1uEWn30G20GPbY2LG5Ua7E0sBLiU6kSaEAdvewaRtl4lZFPEu5tvoXtlFv1fXSfu8mqZVvi75CUxhGRVO/TNzYt7DuaWr6gpPqHHGRbKNFedYt8Ceba+jw+kLJ0fMVcWjn5Xs6NBwBrGBTKemq/AFxxh26oHSJObjqXun2tLUogWzYu7e/Tz9KOViXr6eqBkLqBKzlHWl6YmzcGrHB8qBxD5KfTw6aaGY9zCDNbRF/hDJB+fLSe7tm834AAAAAElFTkSuQmCC>

[image10]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACQAAAAVCAYAAAAuJkyQAAABj0lEQVR4Xu2VPyjFURTHj/JfogwikvInsSgyYpFsZrPBRDYji9FmU2QSKSGJSGHCQgZ/SrFQBgyUxPd07nvu77xf7n3v/WLwPvWpd8+5v975nXt/9xJlyPD3ZMExeAOf4B7stCdESa4OhDAB52AerIYH8AP22JOigN98XQcVpfAeFluxOpKCrqxYJGTDbR1UdMNPuKzilybeqOJpwcvlKqiV5I9vVfzYxDkfGfnkLojpgy3WmF/kBb5ScCnTxrcgzQBJd6Z1AtTARbgAN+BUMP0zqRRUAK/hKSV2h7/CC9hvxvwVvsHa+AyLJXik5H3wHBJnR+SxBGZINnSlToA2ks4NwSLYBSdJvmYvku3QIEl3qnTCUAYfSYp6h6uwIjDDQTIFdZB0hvdIjFFYb40ZPgbG4Q5JYbOBrAPfgsrhCWxQcb5CSszvYZLlb/pO0xact8ZOfArKgfvwAZ4Zz0nuNT7BY/DBuQsLzZhP8zvYHp/hgU9BvSStD/PQmtcMN+EKyd5ZoxQuYJ+Cfh3erP+bL2iRVhb8GXqzAAAAAElFTkSuQmCC>

[image11]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACQAAAAVCAYAAAAuJkyQAAABW0lEQVR4Xu2VPyhFURjAPwNCogwikvInsSgyCYtkM5sNJmUzshhtNkUmKSVKUl4KGwsZ/CnFQhkwUBK/03fee+eeZzCcd+9yf/Wre77ve71zv3PuOSIpKclTgnN4j694hENuQdws4BqWYzOe4DeOukVxUYtPWO3E2kQndOvEYmMEf3DLi9/YeKcXLzq9on/84MXPbNzkY2cce5xxGb7jh0SXMjEmRbuz7CegBTdxA/dwKZoOTwXe4YUUdsd8hdc4YcfmK/zE1lxFEVgR3dCNfgL6RDs3jVU4jIui51gBg3j8Tw+xUn8WYUq0O01+wlKHL6KT+sIdbIhUBGRAtDNmj2SZxXZnbDDHwLzoS5mJrUaygajHc+zw4uYKqbHPM/iGXfm0HOC6Mw5CqegyPuOl9Ur0XjMneBZzcGYkv9TmNH/E/lxFIMZEW/+Xp05dN+7jtuje2ZWEL+CUYPwCRylLYzaiFIgAAAAASUVORK5CYII=>

[image12]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAB8AAAAVCAYAAAC+NTVfAAABh0lEQVR4Xu2UPSiHURTGHx/JV6xKMSBlkZAykywG2RmkyGJDsUhRojAKKSWRkBSFxYTJV5lMikG+BhY8p3Pff9f5T//c/8RTv3rf59zb03vPPS/wr3/9VdWTK/JMvsg9uSY35I4ckHaSEm1Ihjah4SWel0q6nT/h+UElIY/k0haoCmj4gy2EUi00YMwWqH5obdsWQmkQGiD9j5QJ7fUbOSMFXi2oDsk72YL2/ti9n5IOkh5bGVjZ5INsGH+a3JJi40eS23+Enxc0YTVDj7zP+I3OHzV+pE7ySUptIRFNQUOqjT/g/BHjiwrJMLQ1vwqX8Xohacbfh4b3GF80SXIQHy4tnCFDZJZkeLU4FUEDdm0BOlpS63Lvi9D+tpIm59lwaZ2Mpuic5Hu1mGSk5Bf6BA14JRekwVtTDr1Qsm4H2uM8Mu6tseG9ztsjdZ4fRC1kDXoKS9ALt05qSBYpI21klZzoluQoF3pq0ZfLcUfjWkWW3XNwyf9+ARq+QiqhrZyHjuYcdCLi9A0xulUhmXMxRAAAAABJRU5ErkJggg==>

[image13]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABkAAAAVCAYAAACzK0UYAAABI0lEQVR4XmNgGAVDBfAC8TMg1kSXoCboBuL/QKyDLkEtoArE3xlobMkGIF7EQENL3IF4OhCXMNDIEhYgPgrEIgw0tCQPiAuhbJpYIgTEx4GYFcqniSVTgNgbiY/PEmcgtkMXJAS0gXgLmhg2S0CGzwfix0CcgiROFCgC4rdA/AIJf2GAWPIGiC8hlILBAQYyLMEGQMGH7hMYOMCA3ZJKIG4G4mlArIQmhxXMYIBYoocuwYDdEkMg3gFlLwRiRyQ5DBAJxPeB+A8DxJL3DJBUhwwOMGBaAnIMSM9pII5HkyMLHADiVDQxULDaAHEHEP8EYn5UadLBASBOQ+JbMEASCBMDJJ9dRJIjGRgzQBIEyMCTQDyBAWKwMAMkHtqBeA4DljwEAFlwPyueDShxAAAAAElFTkSuQmCC>

[image14]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACQAAAAVCAYAAAAuJkyQAAABKklEQVR4Xu2VvUrDYBSGjyBVdLMgdBKhIOKq6KSd3PUunOygqKNdOnoLgnQQRRD/EEVvwE7dHASlq4uDCEXqE04sXw7VfIZUCu0Dz5DznpCX/BCRPn26ixEct8M0ydjBD4zhKtZww2SpMYAXdtiGCr7gLTalg4UGRS/iy4J0uFDwuLqq0LD0QKEJPMJDvMK9aPw7aRcawkdcCY+X8QMnWxsOx/hgrOJbm3lgUU+L8F1o0wYhs6L5Go5iAcuiX7MXSe/Qlg1CsvgqutPAM8xFNmJIWmjbBg5TuIt3orv7kTSGpIV2bADroo9/2pnd4IFzHMtfCy2KFgrugOUE70X/dQF5rONca8MD30IlfMJ30UKf+IyXzs4MXuOp6LtzjktO7oVvoX9l3g56ji8nKUb0hdCNeQAAAABJRU5ErkJggg==>

[image15]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACQAAAAVCAYAAAAuJkyQAAABA0lEQVR4Xu3UP0tCURiA8bcpsM1AaApBEGktaKmc3PNbOLko5mhLo18hEAcJBCmMKGrpG7g5CIqrS4MIIfZczj3Xc8+gDodUuA/8Bu/7Dq9/UCQqareKIWE/3EZx5NFDyZr9e02M8IGF7MBBukuJDlrdXh50iie08Ip6eOy2dQcdoo9b/3UOMySDDcfpg8r2wO9c1LyAI2TxgANjJ+gK3xv6FPUHaKcPqtgDv2NMRO384hknoQ3H6YPu7IFRGjVRb8rbfQxNHacPqtoDKuIHGePZOxrGa+ddizrI+wTs2viS5VedwhgXwYbD7jHAVNRBcwzRNXbO8IaOqN/OC26MedT+9gcFwTxBptrr7gAAAABJRU5ErkJggg==>

[image16]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAB4AAAAVCAYAAABR915hAAABi0lEQVR4Xu2VzSsFURjGX5/lY4FSl5R8ZCcLK7K2uLKyEgtLUnZiR0RZ+AMUJTt0F+SzlGthw4JSsmBNUlYKCc/b+4453mbKTI0Nv/rVneecuc+9Z05niP75q9TaQMmDk/AMHsMd2OROiEMxbIebcMuMeczDc1iq14PwFlZ+zYjIELyH2/CNgotr4CvsdbIckuIZJ4vNMwUXD8MP2GzyLLw0WSzCihdJiu3z34DvsMjkkQkr5sfAxVUmX9e83uSRCSs+JClImXxV8xaTRyasOEu/UMzLaglb6jXNG00eGS7etSFYICmoMzlvLs69zdUGl40/Wg0u3rMhyWHBBa0m5xPsymSx4OJ9G4JqksOlz8kK4AOcdbIeOEWy20dJVmrMGQ8kH77AIzug8JHJ53SZXo+TnFzlet2gGbMEp+EcyX2BdMEb+EiynOwdvCb/Sxl+SfC/uYCnJLvffeYlsFA/85wOZyxxckleGk/k/4gKfzgZBuAB7IcnmnXCtDchKbphBk7AFZJNN/JthvIJvgtaVO/POtIAAAAASUVORK5CYII=>
