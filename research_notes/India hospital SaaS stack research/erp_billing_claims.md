# ERP, Billing, HR and Indian Healthcare Finance/Claims Layer for a Commercial Multi-Tenant Hospital SaaS in India (state as of 2 Nov 2026)

Method note: GitHub raw files, release tags and release pages were fetched directly (high confidence). Forum, vendor-blog and "explainer" sources are flagged as secondary. Where a number is only from a vendor blog (e.g. Nirmitee) it is marked "vendor-reported". Release pages show day/month only; the year was inferred from page footers (2026) and tag timestamps.

---

## 1. Latest stable versions and dates; runtime requirements; Postgres support

### Takeaway
As of 2 Nov 2026 the current lines are Frappe/ERPNext/HRMS **v16** (GA 12 Jan 2026) and **v15** (still receiving releases). v16 requires Python 3.14, Node 24, MariaDB 11.8. PostgreSQL is NOT officially supported in ERPNext v15 or v16 (only develop/nightly work in progress; a Frappe-side contributor says it is "pretty much done" except transaction isolation, no committed date).

### Cited Findings
**Latest releases seen on GitHub release pages (late Oct / 2 Nov 2026):**
- Frappe Framework: v16.54.0 (27 Oct 2026, marked Latest); v15.125.0 (27 Oct) — [frappe/frappe releases](https://github.com/frappe/frappe/releases); `__version__ = "16.54.0"` on the version-16 branch — [frappe `__init__.py`](https://raw.githubusercontent.com/frappe/frappe/version-16/frappe/__init__.py)
- ERPNext: v16.53.0 (28 Oct 2026); v15.124.3 (30 Oct 2026, the one labelled "Latest" on the page) — [frappe/erpnext releases](https://github.com/frappe/erpnext/releases)
- Frappe HRMS: v16.53.0 (30 Oct 2026, Latest); v15.64.7 (28 Oct) — [frappe/hrms releases](https://github.com/frappe/hrms/releases)
- India Compliance (resilient-tech): v16.11.0 (21 Oct 2026, Latest; adds GSTR-2A/2B export tool); v15.33.0 (27 Oct 2026); v16.9.0 (1 Sep 2026) added e-Waybill generation/asset movement GST handling; v16.10.0 (24 Sep 2026) added ISD invoicing and purchase invoice/bill-of-entry sync — [india-compliance releases](https://github.com/resilient-tech/india-compliance/releases)
- Frappe Insights: v3.15.1 (20 Oct 2026, Latest) — [frappe/insights releases](https://github.com/frappe/insights/releases)
- Frappe Helpdesk: v1.31.1 (29 Oct 2026, Latest) — [frappe/helpdesk releases](https://github.com/frappe/helpdesk/releases)
- Frappe CRM: v1.91.1 (2 Nov 2026, Latest) — [frappe/crm releases](https://github.com/frappe/crm/releases)
- Frappe "India Payroll" (new extension app, see section 6): v16.0.0 tagged 3 Jul 2026 — [release tag](https://github.com/frappe/india-payroll/releases/tag/v16.0.0)

**GA / major-version dates (from GitHub release-tag timestamps):**
- v16.0.0 GA: Frappe 2026-01-12 11:18 UTC; ERPNext 2026-01-12 10:59 UTC; HRMS 2026-01-12 11:05 UTC; India Compliance 2026-01-12 13:23 UTC — tags: [frappe](https://github.com/frappe/frappe/releases/tag/v16.0.0), [erpnext](https://github.com/frappe/erpnext/releases/tag/v16.0.0), [hrms](https://github.com/frappe/hrms/releases/tag/v16.0.0), [india-compliance](https://github.com/resilient-tech/india-compliance/releases/tag/v16.0.0)
- v16.0.0-beta.1 ERPNext: 2025-11-17; v16.0.0-rc.1: 2025-12-23; India Compliance v16.0.0-beta.1: 2025-11-21 — [erpnext beta tag](https://github.com/frappe/erpnext/releases/tag/v16.0.0-beta.1), [rc.1](https://github.com/frappe/erpnext/releases/tag/v16.0.0-rc.1), [IC beta](https://github.com/resilient-tech/india-compliance/releases/tag/v16.0.0-beta.1)
- Frappe's forum plan originally said Beta 15 Nov 2025, Final 6 Dec 2025, later changed to 12 Jan 2026 (consistent with tags) — [forum release-date post](https://discuss.frappe.io/t/erpnext-hrms-frappe-framework-v16-release-dates/156349). Note: some blogs (e.g. Ksolves) still cite 6 Dec 2025 as stable — outdated.
- v15.0.0: Frappe 2023-10-20; ERPNext v15.0.0 ~2023-10-20; HRMS 2023-10-25; India Compliance v15.0.0 2023-11-17 — [frappe tag](https://github.com/frappe/frappe/releases/tag/v15.0.0), [hrms tag](https://github.com/frappe/hrms/releases/tag/v15.0.0), [IC tag](https://github.com/resilient-tech/india-compliance/releases/tag/v15.0.0)
- Insights v3.0.0: 2024-11-04; Helpdesk v1.0.0: 2024-10-03; CRM v1.0.0: 2024-04-01 — [insights](https://github.com/frappe/insights/releases/tag/v3.0.0), [helpdesk](https://github.com/frappe/helpdesk/releases/tag/v1.0.0), [crm](https://github.com/frappe/crm/releases/tag/v1.0.0)

**Runtime requirements:**
- Official Frappe install doc table: v14/v15 = Python 3.10+, Node 18+, MariaDB 10.6.6+, Redis/Valkey 6, Yarn 1.12+, pip 20+; v16/develop = Python 3.14, Node 24, MariaDB 11.8, Redis/Valkey 6+, Yarn 1.22+, pip 25.3+. PostgreSQL is not in the table; PDFs are generated with Chromium (not wkhtmltopdf) in the current doc — [Frappe install docs](https://docs.frappe.io/framework/user/en/installation.md)
- v16 branch `pyproject.toml`: Frappe `requires-python = ">=3.14,<3.15"`, `engines.node >=24`; ERPNext v16 `requires-python >=3.14`; HRMS v16 `requires-python >=3.10` — [frappe pyproject](https://raw.githubusercontent.com/frappe/frappe/version-16/pyproject.toml), [package.json](https://raw.githubusercontent.com/frappe/frappe/version-16/package.json), [erpnext pyproject](https://raw.githubusercontent.com/frappe/erpnext/version-16/pyproject.toml), [hrms pyproject](https://raw.githubusercontent.com/frappe/hrms/version-16/pyproject.toml)
- v15 branch: Frappe `requires-python = ">=3.10,<3.15"`; ERPNext/HRMS `>=3.10` — [frappe v15 pyproject](https://raw.githubusercontent.com/frappe/frappe/version-15/pyproject.toml)
- India Compliance v16 pins `frappe >=16.0.0,<17.0.0` (bench dependency) and is GPLv3 — [IC pyproject v16](https://raw.githubusercontent.com/resilient-tech/india-compliance/version-16/pyproject.toml)

**PostgreSQL:**
- Forum thread (Jun-Oct 2026): Frappe-side commenter mihir-kandoi said on 29 Jun 2026 "Not in v16. Only develop/nightly for now"; on 6 Aug 2026 said apart from transaction isolation it is "pretty much done" and declined to commit to a date; another commenter says Postgres works in the framework but isn't supported once ERPNext is installed; a 30 Oct 2026 reply advises staying on MariaDB unless you already run Postgres — [forum thread](https://discuss.frappe.io/t/is-erpnext-officially-supported-with-postgresql/163329)
- May 2026 thread: Frappe community maintainer (revant_one) says the Postgres docker override exists because the framework supports Postgres, but no app has daily CI on Frappe+Postgres and "you are on your own"; another poster cites a Frappe Build 2026 talk that ~700 Indian banks want Postgres — [forum thread](https://discuss.frappe.io/t/postgres-support/162440)
- Frappe repo ships both `mysqlclient/PyMySQL` and `psycopg2-binary` drivers (framework-level Postgres support exists) — [frappe pyproject](https://raw.githubusercontent.com/frappe/frappe/version-16/pyproject.toml)

### Inferences
- For a production hospital SaaS planned on ERPNext, treat **MariaDB 11.8 + Python 3.14 + Node 24** (v16) as the supported baseline. Choosing Postgres means being off the supported path until a later release (probably v17/develop) — my inference from the forum statements; no official roadmap published.
- Python 3.14 + Node 24 is a notably new platform baseline; many third-party Frappe apps (including payment/payroll extensions) may lag. Check each app's v16 branch.
- v15 is still actively released (v15.124/125 in late Oct 2026), so a v15 -> v16 migration is not forced yet, but I found no official v15 EOL date (see Gaps).

### Gaps
- No official statement found of the v15 end-of-support date or Frappe's formal support policy for N-1 versions.
- Exact "stable" labeling: GitHub marks v15.124.3 as "Latest" on ERPNext while v16.x is the primary line; there is no explicit "LTS" label found.
- No official roadmap or target release for Postgres GA in ERPNext (v17?) found.

---

## 2. Frappe multi-tenancy; Frappe Press; how commercial ERPNext SaaS hosts tenants

### Takeaway
Frappe's native tenancy unit is the **site** (own DB + files) with many sites sharing one **bench** (shared code, so app upgrades hit all sites on that bench); isolation across code versions uses multiple benches. Press (the control plane behind Frappe Cloud) is public, 100% open source under AGPL-3.0, but the only documented setup is a Frappe-staff-oriented local dev environment; no supported turnkey "self-hosted Press for resellers" guide was found.

### Cited Findings
- One bench can serve many sites; each site has own database and files; DNS-based multitenancy: `bench config dns_multitenant on`, `bench new-site <domain>`, `bench setup nginx`; port-based routing is an alternative — [Frappe multitenancy docs](https://docs.frappe.io/framework-git/administration/multitenancy-domains), [bench guide](https://docs.frappe.io/framework/user/en/bench/guides/setup-multitenancy)
- Sites in a bench share the same code, so an update can affect every site in that bench (community statement); each bench is self-contained with its own web/Redis/Node processes, so one host can run several benches of different Frappe versions — [forum](https://discuss.frappe.io/t/how-to-setup-multiple-sites-on-single-bench-instance/129147), [Frappe architecture overview](https://docs.frappe.io/customer-guide/scalability/architecture-overview.md)
- Press README: "100% open-source cloud hosting for the Frappe stack", powers Frappe Cloud; repo is public on `develop`, AGPL-3.0 (license.txt is AGPL v3; `app_license = "GNU Affero General Public License v3.0"`), no GitHub Releases published; ~28k commits — [frappe/press](https://github.com/frappe/press), [press hooks.py](https://raw.githubusercontent.com/frappe/press/develop/press/hooks.py)
- Press docs: the linked guide (updated 2026-02-16) is a "Local Development Environment Setup Guide" replicating the FC production environment; needs 3 Ubuntu 20.04/22.04 servers, Route53, Docker, Digital Ocean/Hetzner/AWS accounts, optional Stripe test account; does not address self-hosting for end users or give licence/production-support terms — [Press local setup](https://docs.frappe.io/cloud/local-fc-setup)
- Frappe's architecture doc states a self-hosted setup gives the same components as Frappe Cloud but your team carries the operational work (secondary summary of the doc) — [architecture overview](https://docs.frappe.io/customer-guide/scalability/architecture-overview.md)
- Forum users reported Redis/nginx/Socket.IO issues and sudden logouts when running multiple benches/sites on one IP in production (unresolved/undescribed) — [forum](https://discuss.frappe.io/t/running-multiple-sites-on-single-ip-in-production-mode-without-bench-start/152490)

### Inferences
- A hospital SaaS built on ERPNext would most plausibly use one of: (a) **site-per-tenant (hospital group)** on shared benches, with canary benches for upgrades; (b) **multi-company inside one site** for a hospital chain (single group, multiple legal entities/GSTINs). Site-per-tenant gives hard DB isolation, which matters for DPDP/health-data separation; multi-company is the right unit for one customer's branches. (Multi-company docs not fetched — see Gaps.)
- Press is AGPL: if you modify Press and offer it to others over a network you must publish those modifications; as an internal control plane that only you use, AGPL source-offer duties to your own users are limited, but have counsel confirm. Operating Press without Frappe-staff support is a heavy ops commitment (inference from docs).

### Gaps
- No official multi-company-in-one-site doc was fetched in this pass.
- No documented examples of third-party commercial ERPNext SaaS providers running on self-hosted Press found; no sizing limits (sites per bench/server) from Frappe.
- Press production-readiness outside Frappe Cloud is unconfirmed; the "warnings against self-hosting" seen came from a low-quality third-party page and are not cited as fact.

---

## 3. Licences (Frappe MIT, ERPNext GPLv3, HRMS, India Compliance, Insights, Helpdesk, CRM, Press) and the SaaS/proprietary-app question

### Takeaway
Frappe Framework = MIT; ERPNext, HRMS, India Compliance, India Payroll, Marley/Healthcare = GPLv3; **Helpdesk, CRM, Insights (licence file), Press = AGPL-3.0**. GPLv3 has no network-use clause, so running GPLv3 apps as hosted SaaS does not by itself trigger source release; AGPL apps do if modified. Proprietary custom apps built on the MIT framework are fine; apps that import/extend GPLv3 code (e.g. ERPNext doctypes) are legally grey. Trademark: "ERPNext/Frappe" cannot be used in your product/company/domain name without written permission.

### Cited Findings
- Frappe's official licence page (updated 28 Jun 2026): ERPNext = GPLv3; Frappe Framework = MIT; Frappe CRM and Helpdesk = AGPLv3; "Other Frappe apps: the license in each respective repository"; docs CC BY-SA; page does not address hosting/SaaS terms — [Frappe license & trademark](https://docs.frappe.io/legal/others/license-and-trademark.md)
- Framework `LICENSE` = MIT; `app_license = "MIT"` — [frappe LICENSE](https://raw.githubusercontent.com/frappe/frappe/develop/LICENSE)
- ERPNext `license.txt` = GPLv3; `app_license` GPL v3 — [erpnext license](https://raw.githubusercontent.com/frappe/erpnext/develop/license.txt)
- HRMS `license.txt` = GPLv3 — [hrms license](https://raw.githubusercontent.com/frappe/hrms/develop/license.txt)
- India Compliance `app_license = "GNU General Public License (v3)"`, LICENSE = GPLv3 — [IC hooks](https://raw.githubusercontent.com/resilient-tech/india-compliance/develop/india_compliance/hooks.py)
- Helpdesk and CRM: `LICENSE` = AGPL v3 (`app_license = "AGPLv3"`) — [helpdesk](https://raw.githubusercontent.com/frappe/helpdesk/develop/LICENSE), [crm](https://raw.githubusercontent.com/frappe/crm/develop/LICENSE)
- **Insights: licence mismatch** — `license.txt` is AGPL v3 but `hooks.py` says `app_license = "GNU GPLv3"`; treat as AGPL-3.0 to be safe — [insights license.txt](https://raw.githubusercontent.com/frappe/insights/develop/license.txt), [insights hooks.py](https://raw.githubusercontent.com/frappe/insights/develop/insights/hooks.py)
- Press: AGPL-3.0 — [press license](https://raw.githubusercontent.com/frappe/press/develop/license.txt)
- Frappe `payments` app (Razorpay, Stripe, PayPal, Braintree, PayTM, Paymob, M-Pesa, GoCardless doctypes): MIT — [payments README](https://raw.githubusercontent.com/frappe/payments/develop/README.md)
- Marley Health (`earthians/marley`, installable app `healthcare`): GPL-3.0 — [marley repo](https://github.com/earthians/marley)
- Frappe India Payroll extension: GPL-3.0 — [india-payroll](https://github.com/frappe/india-payroll)
- Trademark: ERPNext name/logo owned by Frappe Technologies; the current policy allows truthful references ("Compatible with ERPNext") but requires written permission to use Frappe trademarks in company/product/service names or domains; contact legal@frappe.io — [license & trademark](https://docs.frappe.io/legal/others/license-and-trademark.md). Older ERPNext README policy (more restrictive: no use in product/company/domain names; non-commercial permission criteria) — [erpnext trademark policy (older copy)](https://raw.githubusercontent.com/frappe/erpnext/develop/TRADEMARK_POLICY.md)
- A forum question (Apr 2024) on whether using self-hosted ERPNext for ledgers behind a proprietary SaaS triggers GPL has no visible maintainer reply — [forum](https://discuss.frappe.io/t/using-erpnext-for-maintaining-ledgers-and-accounts-in-e-commerce-saas/120840)

### Inferences
- GPLv3 "SaaS loophole" (my knowledge of GPLv3 text; not stated in a fetched source): hosting unmodified or modified GPLv3 ERPNext/HRMS/IC for users without distributing copies generally does not require releasing source. Distributing on-premise installs to hospitals (a likely sale model) WOULD trigger GPLv3 for anything combined with ERPNext/HRMS (including custom apps that derive from it).
- Safest architecture for keeping IP proprietary: keep proprietary logic in separate services (own API/FHIR layer) or in Frappe apps that depend only on the MIT framework; integrate with ERPNext/HRMS through REST/webhooks rather than by importing their code. Whether a Frappe app that extends ERPNext DocTypes is a "derivative work" is an unresolved legal question - needs counsel.
- Avoid AGPL apps (Helpdesk, CRM, Insights) as embedded, modified components of a proprietary stack unless you are happy to publish modifications (AGPL section 13 network clause); using them unmodified as separate services is lower risk.
- Do not name the product "ERPNext-anything" or use erpnext in domains.

### Gaps
- No Frappe-authored official statement on whether proprietary Frappe apps depending on ERPNext are acceptable; no maintainer reply found to the forum question.
- Whether the old non-commercial criteria in the erpnext repo trademark file still applies vs. the docs.frappe.io page (newer, 28 Jun 2026) is unresolved; treat docs.frappe.io as current.

---

## 4. India Compliance: e-invoice (IRP), e-way bill, GSTR-1/3B, TDS; API/GSP and pricing; healthcare GST specifics and GST 2.0

### Takeaway
India Compliance (GPLv3) covers GST e-invoice (IRN/QR), e-waybill, GSTR-1 prep, GSTR-2A/2B reconciliation, GSTIN validation, and ISD, using an API backend (India Compliance Account, credit-based) because government APIs need an ASP/GSP-type intermediary. API is bundled free with Frappe Cloud; self-hosted/SaaS operators buy credits (1 successful call = 1 credit; min order 1,000 credits per a 2024 forum note; per-credit price NOT found). For hospitals, most clinical services are GST-exempt; non-ICU room rent > INR 5,000/day is 5% without ITC; medicines are now 5% (some nil) post GST 2.0 effective 22 Sep 2025.

### Cited Findings
**India Compliance features and API model:**
- Features per README/site: GSTR-1 preparation, Invoice Management System (IMS) actions, GSTR-2B/2A purchase reconciliation, e-Invoice and e-Waybill via NIC/IRP, real-time GSTIN verification, pre-built GST reports; some automation features need GST API access with costs via an India Compliance Account; TDS not mentioned on the README — [IC GitHub](https://github.com/resilient-tech/india-compliance), [indiacompliance.app](https://indiacompliance.app/pricing)
- Release notes (2026): v16.9.0 e-Waybill generation + asset movement; v16.10.0 ISD invoicing, purchase invoice/bill-of-entry sync; v16.11.0 GSTR-2A/2B export — [IC releases](https://github.com/resilient-tech/india-compliance/releases)
- Credits: Frappe Cloud sites get API access included (no credits to buy) unless logged into an India Compliance Account, in which case that account's credits are consumed; self-hosted sites must buy credits from the India Compliance Account page; purchased credits are non-refundable — [Frappe docs: GST API credentials and credits](https://docs.frappe.io/erpnext/gst-api-credentials-and-credits)
- Forum (May 2026): India Compliance API is "completely free for Frappe Cloud users"; no fair-use cap stated — [forum](https://discuss.frappe.io/t/are-india-compliance-api-charges-free-for-users-on-frappe-cloud-or-are-there-any-additional-costs/162732)
- Forum (Sep 2024 reply): minimum order slashed 50% to 1,000 credits, multiples of 1,000 (was 2,000); 1 credit = 1 API request; a 2023 notice mentions credit expiry/roll-over; latest replies Jul 2025 — [forum](https://discuss.frappe.io/t/is-india-compliance-not-free-to-use/110166)
- Docs: installable on Frappe Cloud, Docker (version-15 branches in example) or bench — [IC installation docs](https://docs.indiacompliance.app/docs/getting-started/installation)

**Healthcare GST rules:**
- Room rent: GST Council 47th meeting; non-ICU/CCU etc. hospital room charges above INR 5,000 per day per patient attract 5% GST without ITC on inputs; ICU/CCU/NICU/ICCU rooms stay exempt; threshold applied per patient and tax is on the full room rent, not just the excess; effective 18 Jul 2022 (a proviso inserted in the healthcare exemption entry) — [Grant Thornton](https://www.grantthornton.in/en/insights/blogs/non-icu-rooms-to-be-carved-out-from-healthcare-services-under-gst), [Ditto explainer](https://joinditto.in/articles/health-insurance/gst-on-hospital-room-rent/)
- Core exemption: healthcare services by clinical establishments/authorised medical practitioners/paramedics are exempt (Notification 12/2017-CT(Rate)); hospitals making exempt supplies cannot claim ITC on those supplies (2026 secondary guides; 2026 search found no Council change to hospital ITC rules) — [CharteredHelp](https://charteredhelp.com/?p=21052), [Taxaj 2026 guide](https://www.taxaj.com/learn/?p=718) (the notification number 12/2017 comes from a search-tool summary, not from the CBIC text; verify)
- Unsettled areas in secondary sources: in-patient medicines/consumables and food to admitted patients (composite-supply analyses; AAR rulings exist) — see [GST Council AAR listing](https://gstcouncil.gov.in/en/authority-for-advance-ruling?page=53); also Council clarification that retention fee charged by hospitals is not taxable — [Taxscan](https://www.taxscan.in/?p=16398)

**GST 2.0 (56th GST Council, 3 Sep 2025; effective 22 Sep 2025 for goods and services):**
- GST on 33 lifesaving drugs 12% -> nil; 3 drugs (cancer/rare disease) 5% -> nil; all other drugs/medicines 12% -> 5%; medical apparatus/devices (surgical, dental, veterinary, analysis) 18% -> 5%; wadding, gauze, bandages, diagnostic kits/reagents, glucometers etc. 12% -> 5%; individual life and health insurance exempt; 4-slab structure collapsed into 5% / 18% (+40% de-merit); "Hotel accommodation" 12% -> 5% (this is not hospital room rent) — [GST Council 56th press release PIB (PDF)](https://gstcouncil.gov.in/sites/default/files/2025-09/press_release_press_information_bureau.pdf)
- Implementation: services and goods rate changes effective 22 Sep 2025 (tobacco items deferred) — same press release.
- Notification numbers 9/2025-CT(Rate) and 10/2025-CT(Rate) cited by one secondary site; some outlets say 36 nil-rate drugs (33+3) — [Swarajya](https://swarajyamag.com/amp/story/news-brief%2Fgst-20-what-gets-cheaper-what-gets-costlier-from-22-september), [Taxscan](https://www.taxscan.in/gst/healthcare-gets-cheaper-gst-council-proposes-nil-tax-on-36-lifesaving-drugs-slashes-rate-on-medical-devices-to-5-1431904)

### Inferences
- Hospital billing engine must support per-line tax category: exempt (clinical services, ICU), 5% no-ITC (non-ICU room > INR 5,000/day per patient, evaluated per patient per day), and taxable goods (pharmacy at 0/5%, some items still 18% e.g., non-qualifying products; consumer items). Rate config must be date-effective (pre/post 22 Sep 2025) to rebill/amend historical docs.
- Because hospital exempt supplies carry no output GST and no ITC, the heavy IC features (GSTR-1 for B2B, e-invoice) matter mainly for the taxable side (pharmacy B2B sales, room rent, cafeteria/parking, equipment sales), not for core clinical billing. e-invoice applicability is turnover-threshold driven; hospitals with only exempt/B2C supplies may not need IRP (general knowledge, not cited here).
- As a SaaS operator self-hosting ERPNext+IC (not Frappe Cloud), you pay for API credits per tenant call or negotiate with the IC team; budget unknown. Passing the cost through per tenant GSTIN is an inference.
- Post-GST 2.0 inverted-duty correction likely reduces input-tax cost issues for hospitals buying devices/medicines (now 5%), but hospitals could not claim ITC anyway on exempt supplies — treat as unverified.

### Gaps
- Per-credit price of the India Compliance API, credit validity today, and the identity of the GSP/ASP partner: not found (docs pages 404 or silent).
- IC's TDS (Section 194x) feature: README does not list TDS; ERPNext core has tax-withholding; no verified IC TDS capability found.
- Primary CBIC notification text for 12/2017-CT(Rate) entry 74/75 and the room-rent proviso notification not retrieved; numbers above are from secondary sources.
- Whether hospital pharmacy sales to in-patients are treated as composite supply of healthcare (exempt) vs. separate taxable supply: no authoritative source found; AAR rulings vary.
- Any GST Council decisions after Sep 2025 (57th/58th meetings) on healthcare: nothing found.

---

## 5. Insurance / claims rails: NHCX, PM-JAY TMS, TPA pre-auth, CGHS/ECHS/state schemes

### Takeaway
NHCX (National Health Claims Exchange, NHA/ABDM; FHIR R4, built on the HCX protocol) is live but **not legally mandatory** for hospitals or HMS vendors per the latest (vendor) analysis; the IRDAI circulars are advisory ("may on-board", "endeavor to"). PM-JAY runs on NHA's TMS 2.0 portal; no public TMS API was documented; NHA says an NHCX-PMJAY-HMIS route for HMIS-originated cases is planned/early-integrator stage. Incentives (DHIS) exist for NHCX claims. No CGHS/ECHS/state scheme API information was found.

### Cited Findings
**NHCX:**
- IRDAI circular IRDAI/HLT/CIR/MISC/124/06/2023 (8 Jun 2023) "Testing and adoption of HCX specifications and e-claim standards": insurers/TPAs advised to test and adopt NHCX specs; "may on-board themselves"; IRDAI Master Circular on Health Insurance Business (29 May 2024): insurers "may endeavor to" onboard providers onto NHCX (vendor reading; primary circulars not fetched) — [Nirmitee: Is NHCX Mandatory in 2026](https://nirmitee.io/blog/is-nhcx-mandatory-hospitals-hmis-2026/) (vendor with commercial interest)
- Official count: as of 21 Jul 2024, 34 insurers/TPAs live on NHCX and ~300 hospitals ramping up (Health Ministry written reply in Lok Sabha / PIB) — [MoHFW press release](https://mohfw.gov.in/press-info/7558), also via [Nirmitee](https://nirmitee.io/blog/is-nhcx-mandatory-hospitals-hmis-2026/). No newer official figure found.
- NHA recognised a first cohort of "NHCX-PMJAY Early Integrators" at NHCX Innovation Meet (IIT Hyderabad, 6-7 Mar 2026) (vendor-reported) — [Nirmitee](https://nirmitee.io/blog/is-nhcx-mandatory-hospitals-hmis-2026/)
- DHIS (Digital Health Incentive Scheme): vendor reports Corrigendum 7 (9 Apr 2026, valid Apr-Sep 2026, subject to funds): INR 200 per AB PM-JAY claim (or 10% of claim if lower) to the facility for ABHA-linked, FHIR-format claims via NHCX; INR 10 per claim to the software company; caps INR 1 crore each; from Jul 2026 requires ABDM v3 M1/M2/M3 compliance and a WASA report; prior Corrigendum 6 (Nov 2025-Mar 2026) was INR 500/claim to facilities and nil to vendors. Official NHA text for Corr. 7 not retrieved; an older blog says INR 500 or 10% — [Nirmitee](https://nirmitee.io/blog/is-nhcx-mandatory-hospitals-hmis-2026/), older corrigenda at [abdm.gov.in](https://abdm.gov.in:8081/uploads/Corrigendum_to_Digital_Health_Incentive_Scheme_vf_e4f1ca0b19.pdf)
- Technical: messages are FHIR R4 bundles (CoverageEligibilityRequest bundle = Bundle type collection; Claim bundle with Claim + Coverage); signed/encrypted (JWE) using participant registry keys; async callbacks; sandbox registration through ABDM sandbox with client ID/secret; no formal certification (self-attested) — [HCX specs](https://github.com/hcx-project/hcx-specs/blob/v0.8/hcx-domain-specifications/domain-data-models/), [ABDM FHIR IG HCX profile](https://nrces.in/ndhm/fhir/r4/hcx-profile.html), [Nirmitee TMS vs NHCX](https://nirmitee.io/blog/nhcx-pmjay-tms-ayushman-bharat-claims/), [Nirmitee onboarding checklist](https://nirmitee.io/blog/nhcx-onboarding-checklist-sandbox-to-production-hims/)
- ABDM FHIR IG (NRCES) has multiple versions (3.0.0, 3.0.1, 3.1.0, latest published v6.5.0 per search snippet); verify which version NHCX sandbox targets — [NRCES IG](https://nrces.in/ndhm/fhir/r4/hcx-profile.html)
- Open-source reference: Swasth "hcx-platform" (Health Claims Data Exchange reference implementation), MIT licence — [GitHub](https://github.com/Swasth-Digital-Health-Foundation/hcx-platform)
- Vendor checklist (vendor-reported): ABDM M1 in production, ABDM v3 APIs, current WASA report, HFR/HPR IDs, FHIR claim bundles, procedure/package mapping, async callbacks, reconciliation — [Nirmitee](https://nirmitee.io/blog/is-nhcx-mandatory-hospitals-hmis-2026/)

**PM-JAY TMS:**
- Claims are raised in TMS 2.0 (provider.nha.gov.in; older tms.pmjay.gov.in/OneTMS); provider manual v12.0 dated 10 Jan 2025; helpline 14555; Flow: beneficiary ID (Ayushman ID/mobile/ABHA) with Aadhaar biometric/iris/face authentication -> registration -> pre-auth -> treatment/discharge -> claim (post-discharge) -> payment. Private hospitals: 48h to initiate pre-auth, payer decision in 6 working hours then auto-approval, claim submission within 7 days of discharge (extendable), payment within 15 days intra-state; multiple procedures priced 100%/50%/25% — [Nirmitee TMS vs NHCX (updated 26 Oct 2026)](https://nirmitee.io/blog/nhcx-pmjay-tms-ayushman-bharat-claims/)
- That source found no public TMS 2.0 API for HMIS vendors; NHA has published NHCX-PMJAY-HMIS documents on the NHCX sandbox portal describing how hospitals may raise PMJAY cases from their own HMIS, presented as planned/in progress — same Nirmitee article (secondary; not verified against NHA documents)
- CAG audit mentions PM-JAY pre-auth timing problems (39.57 lakh claims exceeded 12 hours) — [CAG report](https://cag.gov.in/uploads/download_audit_report/2023/09_Chapter-V-064d22bab412c53.02505839.pdf)

### Inferences
- Plan the claims layer as two adapters behind an internal "payer gateway" interface: (1) NHCX/HCX FHIR adapter (eligibility, pre-auth, claim, payment notice, communication) for private insurers/TPAs and eventually PM-JAY via NHCX; (2) TMS 2.0 as portal/RPA/manual-assist for PM-JAY until a documented HMIS API exists. Do not assume an API; confirm with NHA through the HMS-vendor channel.
- NHCX adoption is incentive/procurement driven rather than legally forced; the DHIS vendor incentive (INR 10/claim) is trivial; WASA + ABDM v3 certification cost is the real gate.
- Tariff/package engine must hold multiple payer-specific rate cards (insurer/TPA network rates, PM-JAY HBP packages, CGHS rates); this is a core product asset and not available off-the-shelf in ERPNext/Marley.

### Gaps
- Could not retrieve official NHCX documentation (nhcx.abdm.gov.in returned no body content), the primary IRDAI circulars, or the official Corrigendum 7 text; many details are vendor-reported (Nirmitee).
- No 2026 official onboarding statistics for NHCX.
- No source found for CGHS, ECHS, or state-scheme (e.g., Aarogyasri, MJPJAY, CMCHIS) claims APIs or integration requirements.
- TPA pre-auth workflows: no authoritative document found (common practice: TPA portal/email/fax forms; IRDAI cashless TAT rules not retrieved).
- One docs domain (docs.hcxprotocol.io) redirected to an unrelated shopping domain (shopmebevui.com); possible domain hijack/lapse; do not rely on it.

---

## 6. Indian payroll in Frappe HRMS (PF, ESI, PT, TDS, Form 16) and the Labour Codes

### Takeaway
In 2026 Frappe shipped a separate **India Payroll** extension app (v16.0.0, 3 Jul 2026, GPLv3) covering EPF, ESI, Professional Tax (15+ states), Labour Welfare Fund (14 states), tax-regime comparison and statutory registers; its GitHub README also claims TDS (Form 24Q e-filing) and Form 16, which the product page does not mention (conflicting). The four Labour Codes took legal effect on 21 Nov 2025; central rules were notified ~8 May 2026 (sources vary); state rules lag; the 50% wages rule changes PF/gratuity bases. I found no evidence India Payroll implements the new wage definition.

### Cited Findings
- India Payroll announced 9 Jul 2026 as an extension of Frappe HR; v1 = PT, LWF, EPF, ESI, tax-regime comparison/selection, employee declarations, reports and registers; "Version 1 is called v16"; a pyproject dependency omission was fixed in v16.0.1 (13 Jul) — [forum announcement](https://discuss.frappe.io/t/introducing-india-payroll-for-frappe-hr/163503)
- Product page: PT 15+ states, LWF 14 states, ESI wage ceiling incl. higher ceiling for persons with disabilities, PF 12% with voluntary top-ups and actual-wage contributions, surcharge and marginal relief, old-vs-new regime comparator, one-click EPF ECR / ESIC / LWF registers; no pricing, no mention of TDS/Form 16 or Labour Codes — [frappe.io/hr/india-payroll](https://frappe.io/hr/india-payroll)
- GitHub README (`frappe/india-payroll`): lists EPF, ESI, PT, LWF, income tax and TDS (including Form 24Q e-filing and Form 16), regime selector, statutory reports; GPL-3.0; requires Frappe HR; `develop` branch install; GitHub Releases page empty but a v16.0.0 tag exists — [india-payroll repo](https://github.com/frappe/india-payroll), [tag v16.0.0](https://github.com/frappe/india-payroll/releases/tag/v16.0.0)
- Labour Codes: all four took legal effect 21 Nov 2025 (repealing 29 central Acts); 1 Apr 2026 was a target date for full enforcement; central rules pre-published 30-31 Dec 2025 and (other sources) final central rules notified 8 May 2026; Ministry FAQs 16 Mar 2026; "wages" must be at least 50% of remuneration (basic + DA + retaining allowance, excess of excluded components added back) affecting PF, gratuity, bonus; fixed-term employees get pro-rata gratuity after 1 year; state rules: notified under all four in Maharashtra, Gujarat, Karnataka, MP, Delhi; partial in AP, Telangana, TN, UP; pending in West Bengal, Nagaland (as of May 2026); Kerala HC upheld IR Code s.104(1-A) on 8 Apr 2026; no court stay on any provision — [iPleaders (last verified 8 May 2026)](https://blog.ipleaders.in/labour-codes-2026-implementation-complete-guide-to-what-is-in-force-what-changed-and-what-to-do-now/)
- Search results indicate final central rules were notified on 8 May 2026 and some state follow-ups (e.g., AP IR rules 12 Jun 2026) — [search summary of ELB Bulletin/Khaitan and others](https://www.khaitanco.com/sites/default/files/2026-07/ELB%20Bulletin-June%202026.pdf); one weak note-style source claims PF scheme changes "not notified" and ESI framework "deferred to 20-11-2026" — unverified.

### Inferences
- HRMS core v15/v16 plus India Payroll gives a statutory payroll baseline; but for hospital specifics (shift/on-call allowances, doctor consultant TDS under 194J, resident stipend, nurses on contract) a custom layer is required.
- Because the 50% wage rule and state-specific rules are recent, run an explicit conformance test of India Payroll's PF/gratuity/bonus calculations against the new "wages" definition before relying on it; likely requires custom salary-structure configuration.
- Form 16/24Q capability is contradictory across Frappe's own sources (README vs web page); verify in code before promising.

### Gaps
- Pricing and self-hosting terms for India Payroll (GPLv3 repo suggests self-hostable; the web page links only to Frappe Cloud).
- Status of PF/ESI rule changes under the Social Security Code as of Nov 2026 (EPFO/ESIC primary notifications not retrieved).
- Whether HRMS v16 core itself includes Form 16 or TDS Form 24Q generation.
- Official Labour Ministry gazette notification numbers.

---

## 7. Payment gateways / UPI for Indian SaaS billing; Frappe integrations

### Takeaway
Frappe ships an MIT-licensed `payments` app with a Razorpay (and Stripe, PayPal, Braintree, PayTM, Paymob, M-Pesa, GoCardless) integration, which is geared to one-off checkout, not multi-tenant SaaS subscription billing. No open-source Cashfree or PhonePe Frappe app was found. Recurring SaaS billing in India is bound by RBI e-mandate rules; specifics for Razorpay/Cashfree/PhonePe subscriptions were only partially verified.

### Cited Findings
- `frappe/payments` (MIT): Payment Gateways module includes Razorpay, Stripe, Braintree, PayPal, PayTM DocTypes; doctype dirs also include Paymob, M-Pesa, GoCardless; "new API design" and Mollie integration pending in README — [payments README](https://raw.githubusercontent.com/frappe/payments/develop/README.md), [payments directory](https://github.com/frappe/payments/tree/develop/payments/payment_gateways/doctype)
- ECOSIRE sells (proprietary, build-to-order) Razorpay/RazorpayX and "Razorpay Suite" apps for ERPNext v15/v16 (collections, payment links, UPI, Smart Collect, eMandate subscriptions, payouts, webhook HMAC verification), indicative from USD 249 (vendor marketing; not verified) — [ECOSIRE Razorpay Suite](https://ecosire.com/tr/apps/erpnext/erpnext-razorpay-suite)
- RBI e-mandate framework: recurring payments up to INR 15,000 per transaction without additional authentication (Zoho guide); raised limits to INR 1 lakh for certain categories (mutual funds, insurance, credit card repayment) from Dec 2023; UPI AutoPay launched by NPCI in 2020; UPI mandate effective cap is the lowest of bank/UPI app limits (Shopify engineering example: INR 5,000) — [Zoho UPI AutoPay guide](https://www.zoho.com/payments/academy/payment-processing/upi-autopay-mandates-and-recurring-debits.html), [Business Standard Dec 2023](https://business-standard.com/finance/news/automatic-payment-limit-through-upi-raised-to-rs-1-lakh-says-rbi-123121201097_1.html), [Shopify Engineering](https://shopify.engineering/technology-behind-navigating-recurring-payments-india)
- Razorpay recurring payments docs and PhonePe subscription guide exist (not read in depth) — [Razorpay recurring docs](https://razorpay.com/docs/payments/recurring-payments/overview/integrate), [PhonePe guide](https://business.phonepe.com/articles/how-to-accept-subscription-payments-in-india-upi-auto-pay-vs-e-nach-vs-cards)
- RBI also proposed raising UPI limits for educational institutions and hospitals to INR 5 lakh per transaction (Dec 2023 proposal) — [YourStory](https://her.yourstory.com/2023/12/upi-transaction-limit-educational-institutions-hospitals-rs-5-lakh); relevant to patient-facing hospital payments, current status unverified.

### Inferences
- For SaaS subscription billing (platform fee from hospitals), Razorpay Subscriptions or Cashfree Subscriptions via direct API from a billing service (not necessarily from ERPNext) is the pragmatic route; Frappe's `payments` app will need extension for webhooks/mandates. Patient-facing collections (OPD/IPD deposits) via Razorpay/Cashfree/PhonePe plus UPI QR/Payment Links fit the ERPNext Payment Entry model.
- INR 15,000 auto-debit limit implies enterprise hospital SaaS plans above that need e-NACH/netbanking mandates or invoice-and-pay (manual) billing.

### Gaps
- Pricing/MDR of Razorpay, Cashfree, PhonePe; current Cashfree Subscriptions and PhonePe Autopay specifics; any open-source Cashfree/PhonePe Frappe app (none found).
- Current (Nov 2026) RBI e-mandate thresholds and PA-PG licensing status of each gateway: not verified.

---

## 8. Open-source hospital billing / package / tariff engines worth reusing

### Takeaway
No ready-made Indian hospital tariff/package/payer-contract engine was found in open source. The closest reusable assets are Marley Health (GPLv3 Healthcare app on Frappe/ERPNext, FHIR-inspired), CARE (MIT, Open Healthcare Network), and Swasth's HCX reference platform (MIT) for the claims protocol side.

### Cited Findings
- Marley Health (earthians/marley; app name `healthcare`): GPL-3.0; patient, OPD/IPD, clinical procedures, lab, rehab, service units, multiple medical code standards; pharmacy/purchasing/HR/accounts through ERPNext; REST API, payment integration, patient portal; 555 stars, 1,880 commits on develop; no GitHub releases; README does not mention insurance claims or tariff/price lists; design "based on HL7 FHIR" — [earthians/marley](https://github.com/earthians/marley)
- CARE (ohcnetwork/care): "Digital Public Good" backend for managing patients, health workers, hospitals; MIT; ICU/bed/ventilator analytics, facility inventory, telemedicine; no billing/insurance/ABDM/PM-JAY features listed on the repo page; ~5.2k commits — [ohcnetwork/care](https://github.com/ohcnetwork/care)
- HCX platform reference implementation (MIT) — [Swasth hcx-platform](https://github.com/Swasth-Digital-Health-Foundation/hcx-platform)
- ERPNext v16 features relevant to billing: price lists, subcontracting, "Custom Financial Report Templates", "Purchase Expense Booking", Consolidated Trial Balance, MRP, Inward Subcontracting (vendor blog summary) — [Webkul ERPNext v16 intro](https://webkul.com/blog/erpnext-v16-introduction/); Frappe's own release page — [frappe.io v16](https://frappe.io/erpnext/version-16)

### Inferences
- Marley's healthcare DocTypes could be a starting point for patient/encounter/service-unit data, but GPLv3 limits proprietary distribution; if you host as SaaS only, GPLv3 does not force source release, but any on-prem distribution would. A cleaner path is to design your own billing/package/tariff service and use ERPNext purely as ledger/AR/inventory.
- Tariff engine requirements (my inference from claims sections): payer-specific rate cards, package definitions with inclusions/exclusions, room-category multipliers, procedure-multiple-pricing rules (PM-JAY 100/50/25), co-pay/deductible/non-payable items splitting, GST classification per line, TPA deduction reconciliation.

### Gaps
- Marley's insurance/tariff capabilities (docs at marleyhealth.io/docs not fetched); other candidates (Bahmni, OpenMRS billing module, OpenEMR, Indian HMS projects) not evaluated in this pass.
- No evidence found of production Indian hospital deployments of Marley/ERPNext at scale.
