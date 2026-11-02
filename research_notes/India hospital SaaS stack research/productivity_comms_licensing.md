# Productivity, Communication and Patient-Engagement Layer + Licence Verification (India hospital SaaS, as of 2 Nov 2026)

Research date: 2026-11-02. Versions/dates below were read from GitHub release feeds/APIs, Docker Hub and vendor pages on that date unless stated. Where a claim rests only on blogs/vendors, it is labelled "secondary". Licence notes are engineering reading of public licence texts, not legal advice.

## 1. Nextcloud Hub, Collabora Online (CODE), ONLYOFFICE Docs - versions, licences, limits, multi-tenant hosting

### Takeaway
Nextcloud is on Hub 26 Summer (Nextcloud 35.0.2, 15 Oct 2026), AGPLv3. Collabora Online's code is MPL 2.0 and the CODE Docker build is at 26.04.5.2.1, but whether CODE still has a document/connection cap is contradictory online (an old forum post says no cap; 2026 blogs repeat "10 docs / 20 connections"), and Collabora itself says CODE is not recommended for production. ONLYOFFICE Docs Community removed its 20-connection cap in 9.4 (secondary reports); current release is v10.0.0 (12 Oct 2026), AGPLv3 plus extra branding terms.

### Cited Findings
- Nextcloud Hub 26 Summer = Nextcloud 35. Latest maintenance release 35.0.2 (15 Oct 2026); 35.0.1 (24 Sep 2026); 35.0.0 major (16 Sep 2026). Hub 26 Spring = Nextcloud 34, Hub 26 Winter = Nextcloud 33 are still tracked as separate maintained lines — [Nextcloud changelog](https://nextcloud.com/changelog/)
- Nextcloud server licence file is GNU AGPL v3 — [nextcloud/server COPYING](https://raw.githubusercontent.com/nextcloud/server/HEAD/COPYING)
- Collabora Online source (CollaboraOnline/online) is under Mozilla Public License 2.0 — [COPYING](https://raw.githubusercontent.com/CollaboraOnline/online/HEAD/COPYING)
- Latest `collabora/code` Docker tags: 26.04.5.2.1 (pushed 2026-10-23), 26.04.5.1.1 (2026-10-09), 26.04.4.2.1 (2026-09-24); 26.04 line began ~June 2026 (26.04.1.x) — [Docker Hub tags](https://hub.docker.com/v2/repositories/collabora/code/tags?page_size=25&name=26.04). An older 25.04 line (e.g. 25.04.9.4) was still shipping in Sept 2026 — [Releasebot](https://releasebot.io/updates/collabora)
- Collabora's own CODE page: CODE builds release on average monthly and are "not recommended for production environments"; the supported product is the paid Collabora Online with tested updates/security fixes. The page does not mention doc/connection limits or MPL — [Collabora CODE page](https://www.collaboraonline.com/code/)
- CODE limit evidence is contradictory: (a) Aug 2026 hosting guide says CODE allows 10 simultaneous documents / 20 connections, deliberately so it does not become de facto enterprise infrastructure (secondary) — [SSD Nodes](https://www.ssdnodes.com/learn/lang/fr/onlyoffice-vs-collabora-self-hosted); (b) Collabora forum user "mmeeks" said on 17 Jan 2022 "There are no limitations of documents / connections in CODE anymore" and recommended support/SLA for scale (not labelled official; a forum thread "Does Collabora CODE 26.04.2.4 Have Any User or Concurrent Connection Limits" from Sept 2026 exists but its replies were not readable) — [Collabora forum](https://forum.collaboraonline.com/t/code-docker-limitations/810); (c) older Nextcloud thread on 10 docs/20 connections — [Nextcloud forum](https://help.nextcloud.com/t/collabora-document-limitation-by-purpose-10docs-20connections/4425)
- ONLYOFFICE DocumentServer latest releases: v10.0.0 (2026-10-12), v9.4.0 (2026-05-19), v9.3.1 (2026-03-03), v9.3.0 (2026-02-24) — [GitHub API](https://api.github.com/repos/ONLYOFFICE/DocumentServer/releases?per_page=4)
- ONLYOFFICE DocumentServer repo LICENSE is GNU AGPL v3 — [LICENSE](https://raw.githubusercontent.com/ONLYOFFICE/DocumentServer/HEAD/LICENSE)
- ONLYOFFICE Docs 9.4 removed the Community Edition 20-simultaneous-connection limit (secondary coverage); also moved to a single process without RabbitMQ/DB dependencies and unminified code — [Linuxiac](https://linuxiac.com/onlyoffice-docs-9-4-removes-community-connection-limit/), [IT-Connect](https://www.it-connect.tech/onlyoffice-docs-9-4-drops-the-20-connection-limit-and-updates-its-open-source-license/), [Nextcloud forum](https://help.nextcloud.com/t/onlyoffice-officially-removed-the-limitation-of-20-simultaneously-opened-documents/244614)
- Licence terms reported for 9.4: still AGPLv3 with added terms; derived/modified versions must carry an easily accessible "About" section crediting ONLYOFFICE as original developer; no trademark rights. Linked to a dispute with the Euro-Office fork (Nextcloud/IONOS et al.). Sources conflict on whether the change makes the licence "non-contradictory" (Wikipedia) vs "additional terms added" (IT-Connect); exact text not read — [IT-Connect](https://www.it-connect.tech/onlyoffice-docs-9-4-drops-the-20-connection-limit-and-updates-its-open-source-license/), [Wikipedia](https://en.wikipedia.org/wiki/OnlyOffice)
- ONLYOFFICE FAQ (older text): under AGPLv3, if the product is made available over a network (SaaS) you must make source of modified versions available, and keep branding; commercial subscription for support/SLA/clustering — [ONLYOFFICE FAQ](https://helpcenter.onlyoffice.com/docs/faq/docs-community.aspx)

### Inferences
- Multi-tenant pattern (not sourced; standard architecture): one Nextcloud instance per hospital tenant (or per tenant group) pointing at a shared or per-tenant Collabora/ONLYOFFICE document server via WOPI/JWT; the document server is stateless w.r.t. tenant data, so sharing is feasible but per-tenant isolation is cleaner. Nextcloud itself has no hard multi-tenancy; instance-per-tenant (containers/K8s) is the safe model for PHI.
- For a commercial SaaS, plan on the paid Collabora Online (or a Collabora partner subscription) rather than CODE, given Collabora's "not for production" stance and the uncertain cap. ONLYOFFICE Community is now the more capacity-friendly free option but carries branding obligations and AGPL network-use duties if modified; keep it unmodified.

### Gaps
- Official, current statement on CODE document/connection limits (26.04) not found; verify by running the image and reading coolwsd defaults, or ask Collabora.
- Exact ONLYOFFICE 9.4+ additional-terms text and the primary ONLYOFFICE blog post were not read; 9.4 release date (appears ~19 May 2026 per tag) and whether Community still forbids hosting for third parties unmodified not verified.
- Collabora commercial pricing / partner (hosting) programme not researched.

## 2. Mail: Stalwart, Mailcow, transactional email (Postal, Amazon SES Mumbai)

### Takeaway
Stalwart has reached v1.0 (v1.0.2 on 2 Nov 2026) and is dual-licensed AGPL-3.0 + proprietary Stalwart Enterprise License v2 (SELv2); crucially for a multi-hospital SaaS, multi-tenancy (per-tenant quotas/isolation) is Enterprise-only. Mailcow is GPLv3 (release 2026-10, 21 Oct 2026). For transactional email, Postal (MIT, v3.3.7) or Amazon SES are the options; SES India-region price needs confirming on AWS's live page.

### Cited Findings
- Stalwart releases: v1.0.2 (2026-11-02), v1.0.1 (2026-10-28). v1.0.2 notes: encryption at rest must be re-enabled after upgrade; System Administrator role lacks new permissions on upgraded servers (needs `x:Role/set`); adds OpenPGP/post-quantum OpenPGP, S/MIME, DKIM2 changes. (Earlier 0.15.5 was latest in distro packaging in Apr 2026.) — [GitHub releases API](https://api.github.com/repos/stalwartlabs/stalwart/releases?per_page=6), [Arch package](https://archlinux.org/packages/extra-staging/x86_64/stalwart)
- Stalwart licence: dual-licensed AGPL-3.0 and Stalwart Enterprise License v2 (SELv2, proprietary, for businesses that do not want AGPL obligations); ~85% of features AGPL, rest Enterprise; "no existing open source feature will be moved to Enterprise" — [Stalwart README](https://raw.githubusercontent.com/stalwartlabs/stalwart/main/README.md)
- Enterprise-only (per comparison page): multi-tenancy with per-tenant quotas and isolation; per-domain directory backends; branding incl. multi-tenant branding; account archiving/un-deletion and restoring deleted mail; AI spam classifier and AI/LLM Sieve scripting; SCIM 2.0; LDAP failover; read replicas, sharded blob and in-memory stores; live telemetry/dashboard/alerts/delivery history; masked email addresses; branded scheduling/notification emails; Community metrics "Limited" — [Stalwart compare](https://stalw.art/compare/), [Stalwart enterprise](https://stalw.art/enterprise/)
- Enterprise licensing: per-mailbox subscription (annual/monthly, tiered), key issued to a server domain (e.g. `*.example.org`), unlimited servers under that domain and unlimited client domains hosted; Premium Support needs >=150 mailboxes; 30-day trial; cancelling reverts server to Community automatically (data and standard mail service continue). Exact prices not on the pages fetched — [Stalwart compare](https://stalw.art/compare/)
- Mailcow releases: 2026-10 (21 Oct 2026), 2026-09a (6 Oct 2026), 2026-09 (21 Sep 2026) — [GitHub API](https://api.github.com/repos/mailcow/mailcow-dockerized/releases?per_page=3). Mailcow-dockerized licence: GNU GPL v3 — [LICENSE](https://raw.githubusercontent.com/mailcow/mailcow-dockerized/HEAD/LICENSE)
- Postal (self-hosted SendGrid/Mailgun/Postmark-style): repo licence MIT (GitHub page spdxId MIT); latest tags v3.3.7 (2026-06-03), v3.3.6 (2026-04-28) — [GitHub releases feed](https://github.com/postalserver/postal/releases.atom), [Postal repo](https://github.com/postalserver/postal)
- Amazon SES: secondary sources say $0.10 per 1,000 emails uniform across commercial regions incl. Mumbai, dedicated IP $24.95/month/IP, managed dedicated IP $15/month/account + $0.08/1,000, attachments $0.12/GB beyond 1 GB. The AWS pricing page snippet shows a different $0.16/1,000 first-tier figure tied to newer Essentials/Pro/Enterprise plan tiers — conflict unresolved — [AWS SES pricing](https://aws.amazon.com/ses/pricing/), [Classmethod on new plans](https://dev.classmethod.jp/en/articles/ses-pricing-plans-price-list-api-sesv2-api/), [cloudburn](https://cloudburn.io/blog/amazon-ses-pricing)

### Inferences
- A SaaS that wants per-hospital mail domains with quota isolation and tenant admins on Stalwart needs the Enterprise licence (per-mailbox cost); on AGPL Community you can still host many domains but not with tenant isolation/branding/SCIM. Mailcow (GPLv3, docker-compose, domain-level admins) is simpler but not a true multi-tenant control plane; both are heavy to run for deliverability — most hospital SaaS should use hospital-owned Google/Microsoft mail or a managed provider, and reserve self-hosted mail for internal/system addresses.
- For product transactional mail (appointment receipts, reports), SES in ap-south-1 (data stays in India) or Postal behind per-tenant "servers/organizations" are both workable; Postal's MIT licence imposes no SaaS obligations but you own IP reputation/deliverability.

### Gaps
- Stalwart v1.0.0 date and full v1.0 release notes/breaking-change list not read; SELv2 text and pricing page not read.
- Postal's multi-tenant model (orgs/servers) documented only from general knowledge, not fetched; SES Mumbai exact price and whether SES is affected by India data-localisation expectations not confirmed on a primary page.

## 3. Chat: Matrix Synapse, Element Server Suite, Dendrite, Mattermost, Zulip

### Takeaway
Synapse is AGPL-3.0-or-later (or an Element Commercial License), not Apache 2.0, so the blueprint is wrong; latest is v1.164.2 (2 Nov 2026). Dendrite is AGPLv3 and reported to be in maintenance mode. Mattermost Team Edition is MIT-compiled binaries with a 250-user cap since v11; Zulip is Apache 2.0.

### Cited Findings
- Synapse is dual-licensed by Element Creations Ltd: free under AGPL v3 "or any later version", or paid Element Commercial License (licensing@element.io); copyright lines run to 2025 under Element Creations Ltd — [Synapse README](https://raw.githubusercontent.com/element-hq/synapse/develop/README.rst); separate files LICENSE-AGPL-3.0 and LICENSE-COMMERCIAL exist — [LICENSE-COMMERCIAL](https://raw.githubusercontent.com/element-hq/synapse/HEAD/LICENSE-COMMERCIAL)
- Synapse latest: v1.164.2 (2026-11-02), v1.164.1 (2026-10-28), v1.164.0 (2026-10-27) — [GitHub API](https://api.github.com/repos/element-hq/synapse/releases?per_page=4)
- Element Server Suite (ESS): official deployment method; ESS Community (free, aimed at small/mid non-commercial community use), ESS Pro (commercial), ESS TI-M (Pro variant for Germany's Gematik TI-Messenger). No Synapse support without an Element subscription. ESS Helm chart latest 26.10.4 (2026-10-29), 26.10.3 (2026-10-23) — [Synapse README](https://raw.githubusercontent.com/element-hq/synapse/develop/README.rst), [ess-helm feed](https://github.com/element-hq/ess-helm/releases.atom)
- Dendrite repo LICENSE is GNU AGPL v3; project README (April 2026 Gitee mirror, LinuxLinks) says it is in maintenance mode (security fixes only); Alpine packages 0.15.2 (May 2026) — [Dendrite LICENSE](https://raw.githubusercontent.com/element-hq/dendrite/HEAD/LICENSE), [mirror](https://gitee.com/psdnfu/dendrite), [LinuxLinks](https://www.linuxlinks.com/dendrite-second-generation-matrix-homeserver/)
- Mattermost licensing file: compiled binaries from Mattermost, Inc. are MIT ("MIT-COMPILED-LICENSE"); source can be used under AGPL v3.0 or a commercial licence; `server/templates, i18n, public, webapp` under Apache 2.0; promise not to enforce AGPL copyleft for apps that only use admin tools/config files and don't modify source — [LICENSE.txt](https://raw.githubusercontent.com/mattermost/mattermost/HEAD/LICENSE.txt)
- Mattermost v11 (effective Oct 2025): Team Edition (MIT-compiled) user limit cut from 1,000 to 250 and GitLab SSO removed; new free commercial "Entry" edition (~25-50 users; commercial licence with redistribution terms; planned limits incl. 10,000 messages, 10,000 push notifications/month); open-source project stays AGPLv3 — [Mattermost forum](https://forum.mattermost.com/t/mattermost-v11-changes-in-free-offerings/25126). Licence interpretation is disputed in community posts — [Mattermost docs FAQ](https://docs.mattermost.com/product-overview/faq-license.html)
- Zulip: LICENSE is Apache License 2.0; releases Zulip Server 12.3 (~2026-09-21) and 13.0-rc1 (2026-10-30) — [Zulip LICENSE](https://raw.githubusercontent.com/zulip/zulip/HEAD/LICENSE), [releases feed](https://github.com/zulip/zulip/releases.atom)

### Inferences
- Blueprint "Synapse Apache 2.0" is outdated: current licence is AGPL-3.0-or-later (this change is widely understood to date from Element's Nov 2023 relicensing; that date was not confirmed in a source this session).
- For hospital staff chat inside a SaaS, Zulip (Apache 2.0, no copyleft) and Mattermost source/AGPL are lower-obligation to embed than Synapse; Synapse is the right fit only if federation/Matrix clients are required, and unmodified use needs no source publication, while a commercial Element licence removes AGPL concerns. Dendrite should not be a new foundation given maintenance mode.

### Gaps
- Element's Nov 2023 AGPL announcement (blog) not fetched; date of change only inferred from the README's dual-licence/copyright text.
- Zulip multi-organization hosting details (realms) and Mattermost current Entry limits (planned values may have changed) not verified.

## 4. Patient engagement: Chatwoot, WhatsApp Cloud API (India), SMS/DLT, RCS

### Takeaway
Chatwoot v4.19.1 (24 Oct 2026) is MIT except an `enterprise/` directory under a commercial licence requiring a subscription for production use. WhatsApp bills per delivered template message since 1 July 2025; INR billing is mandatory for eligible India accounts by 31 Dec 2026, and from 1 Oct 2026 service messages beyond 1,000 free/month per number and utility messages inside the service window are charged. India SMS runs on TRAI DLT with registered headers (11 chars, -P/-S/-T/-G suffixes), content templates and consent templates, URL/APK/OTT/callback whitelisting (from Sep 2024) and a max two-telemarketer chain (2025 amendment).

### Cited Findings
- Chatwoot releases: v4.19.1 (2026-10-24), v4.19.0 (2026-10-20), v4.18.0 (2026-09-18), v4.17.1 (2026-08-27) — [GitHub API](https://api.github.com/repos/chatwoot/chatwoot/releases?per_page=4)
- Chatwoot LICENSE: everything outside `enterprise/` and third-party components is MIT; `enterprise/` is under `enterprise/LICENSE` — [LICENSE](https://raw.githubusercontent.com/chatwoot/chatwoot/develop/LICENSE). The enterprise licence says the software may only be used in production if you have agreed to Chatwoot's Subscription Terms (or other agreement) with a valid licence for the correct number of seats; modifications/patches allowed but Chatwoot retains rights — [enterprise/LICENSE](https://raw.githubusercontent.com/chatwoot/chatwoot/HEAD/enterprise/LICENSE)
- WhatsApp Business Platform pricing (Meta, fetched 2026-11): since 1 Jul 2025 charged per delivered message by category (marketing, utility, authentication) and recipient country code; service (non-template) replies within 24h window; user-to-business free; Meta may change prices only on 1 Jan/1 Apr/1 Jul/1 Oct with 1-6 months' notice. India: marketing rate rose 1 Jan 2026; authentication-international rate rose 1 Apr 2026; India not in the 1 Oct 2026 change table. From **1 Oct 2026**: service messages become charged after 1,000 free per business phone number per month (non-rolling); utility messages sent inside an open customer-service window become charged; businesses without payment method on file by 30 Sep 2026 may lose service-message delivery; eligible government/non-profits keep free service messages to 31 Dec 2027. Free Entry Point windows up to 7 days for Click-to-WhatsApp ads. INR billing: eligible India customers must migrate all accounts to INR by 31 Dec 2026; non-INR messages not delivered from 1 Jan 2027. Meta Business Agent messages charged $2.00/million tokens (from 1 Jul 2026) — [Meta pricing](https://developers.facebook.com/docs/whatsapp/pricing)
- India INR rates (secondary, ex-18% GST): marketing about INR 0.8631 (up from 0.7846 on 1 Jan 2026), utility and authentication about INR 0.115; July 2025 launch rates reported by Medianama as about 0.78 marketing / 0.11 utility. Rates not on Meta's page itself (in linked CSV/PDF rate cards) — [ChatMaxima](https://chatmaxima.com/whatsapp-api-pricing/india/), [InstantDM](https://instantdm.com/blog/whatsapp-business-api-pricing-2026-india-october-update), [Medianama](https://www.medianama.com/2025/07/223-whatsapp-business-per-message-pricing-india/)
- Template categories (Meta): Marketing (flexible; mixed or unclear content treated as marketing); Utility (non-promotional, follows a user action/request, tied to order/account/services/transactions or essential/safety-regulatory; generic surveys do not qualify); Authentication (OTP only, template-library template, no URLs/media/emojis). Since 9 Apr 2025 utility templates judged marketing are approved as marketing; misuse ladder: warning, rate limits on utility, restriction (all utility templates become marketing, 7/30 days), portfolio-level restriction; 60 days to appeal — [Meta template guidelines](https://developers.facebook.com/docs/whatsapp/updates-to-pricing/new-template-guidelines)
- Healthcare on WhatsApp (secondary/vendor): healthcare is "allowed with conditions" - support, follow-ups and appointment reminders OK as non-promotional; cannot promote restricted health products via ads/catalogue/templates; appointment reminders/confirmations classed utility; avoid sensitive health detail in templates (vendor advice, not a Meta rule). Official policy page appears updated 23 Sep 2026 (unconfirmed) — [WhatsApp Business Messaging Policy](https://business.whatsapp.com/policy), [respond.io](https://respond.io/id/help/whatsapp/check-whatsapp-business-eligibility-and-policy-rules.md), [D7 healthcare templates](https://d7networks.com/whatsapp-business-api/whatsapp-message-templates/healthcare/)
- BSPs/aggregators appearing in India pricing sources (price-lists, not endorsements): Gallabox, MyOperator, Interakt, AiSensy, Zoko, Fast2SMS (all via search results) — [Gallabox rate card](https://docs.gallabox.com/pricing-and-billing/whatsapp-pricing/rate-card.md), [MyOperator](https://myoperator.com/blog/whatsapp-business-api-pricing-india-2026)
- TRAI: Telecom Commercial Communications Customer Preference (Second Amendment) Regulations, 2025 (1 of 2025), notified 12 Feb 2025, in force after 30 days (some provisions 60 days). Provisions read: commercial communication only via registered headers/number series; Sender authentication of transmissions; traceability with no more than two Telemarketers (one Aggregator, one Delivery); immediate suspension of traffic from a Sender on header/content-template misuse until corrective action/complaint; annual self-certification of headers, content templates and consent templates (else automatic suspension); SMS Header = 11-character alphanumeric with suffixes "-P", "-S", "-T", "-G" for Promotional, Service, Transactional, Government; scrubbing of messages containing URLs/APKs/OTT links/callback numbers using sender-uploaded whitelists with undertaking — [TRAI regulation PDF](https://www.trai.gov.in/sites/default/files/2025-02/Regulation_12022025.pdf)
- Earlier DLT mechanics (vendor sources): CTA whitelisting of links/APKs/WhatsApp numbers for templates registered after 1 Sep 2024; URL-shortener domains tied to sender ID whitelisted from 1 Oct 2024; template variables must be tagged; message-chain (hash) traceability enforced from 11 Dec 2024 with initial leniency on hash mismatch — [Netcore](https://updates.netcorecloud.com/changelog/dlt-mandate-url-whitelisting-in-sms-templates-2), [CleverTap](https://docs.clevertap.com/docs/sms-regulations), [Business Standard](https://www.business-standard.com/industry/news/no-disruption-on-day-1-of-mandatory-message-traceability-says-trai-124121101196_1.html), [Medianama](https://www.medianama.com/2025/02/223-no-more-promotional-spam-telcos-must-detect-and-block-spammers-using-ai-under-trais-new-rules/)
- TRAI issued over 731,000 notices to unregistered telemarketers in 2025 — [Medianama](https://www.medianama.com/2026/02/223-trai-issued-731000-notices-unregistered-telemarketers-2025/)
- RCS in India (secondary, conflicting): Jio, Airtel and Vi all carry RCS; Jio launched commercial RCS Business Messaging for iPhone users in Sept 2026 with use-case pricing reported 170-275% above legacy; Airtel rate reported as an expected ~INR 0.11 (unconfirmed); sources disagree whether DLT applies to RCS; Google agent verification is mandatory and TRAI 9am-9pm commercial hours reportedly apply — [Mobile Ecosystem Forum Sept 2026](https://mobileecosystemforum.com/2026/09/14/jio-bring-rcs-business-messaging-to-iphones-in-india/), [Voice&Data](https://www.voicendata.com/mobile-services/rcs-gains-momentum-in-india-with-airtelgoogle-partnership-10893020), [CM.com RCS pricing](https://www.cm.com/en-in/blog/rcs-pricing-india/)

### Inferences
- Hospital appointment reminders, report-ready and billing receipts map to WhatsApp Utility / TRAI Service-or-Transactional; health-camp and offer messages are Marketing / Promotional and need separate consent, templates and (for SMS) -P headers. Keep clinical detail out of templates and link to an authenticated portal (link URLs must then be DLT-whitelisted).
- From Oct 2026, inbound-conversation replies are no longer free beyond 1,000/number/month, so per-tenant WhatsApp numbers and per-tenant pass-through billing should be designed into the SaaS price model; INR migration deadline 31 Dec 2026 applies to any WABA created in USD.
- Chatwoot is safe to self-host and modify (MIT) provided you avoid `enterprise/` code in production without a subscription; treat enterprise-gated features (SLAs, audit logs, SSO etc., per general knowledge, unverified) as unavailable.

### Gaps
- Exact current INR rate card (Meta CSV/PDF) not retrieved; INR figures are secondary and marketing-rate history differs across sources (0.7846 vs 0.86).
- TRAI's template registration procedure, entity (PE) onboarding fees, 140-series/1600-series numbering orders, promotional time window, and consent-template/DND (DLT) 2025-26 updates beyond the Feb 2025 regulation were not verified from primary sources; ONLY the regulation PDF was read.
- RCS DLT applicability, official Airtel/Jio tariffs unverified. Specific Meta "healthcare" policy text not read.
- Whether a 2026 TRAI amendment superseded the Feb 2025 text not checked.

## 5. Notification/omnichannel OSS: Novu, ntfy, Apprise

### Takeaway
Novu is open-core: MIT core with `enterprise/` and `ee/` folders under a commercial licence. ntfy's LICENSE file is Apache 2.0 (its README may offer a dual Apache/GPLv2 choice; not verified). Apprise is BSD 2-Clause.

### Cited Findings
- Novu repo page: core under MIT; `enterprise` folder at repo root and subfolders are commercial-licensed (files LICENSE-MIT, LICENSE-ENTERPRISE, EE-PACKAGES-LICENSE); README (fork copy) names `apps/web/src/ee` and `apps/dashboard/src/ee` as enterprise paths; self-hosted community edition uses single-user login, BYO Clerk is Enterprise — [Novu GitHub](https://github.com/novuhq/novu), [Novu community edition v2.2.0](https://novu.co/changelog/novu-community-edition-v220-self-hosting/)
- ntfy LICENSE file is Apache License 2.0 — [ntfy LICENSE](https://raw.githubusercontent.com/binwiederhier/ntfy/HEAD/LICENSE)
- Apprise LICENSE is BSD 2-Clause (copyright 2026, Chris Caron) — [Apprise LICENSE](https://raw.githubusercontent.com/caronc/apprise/HEAD/LICENSE)

### Inferences
- All three are permissive/open-core, no network copyleft: low licence risk for embedding in SaaS. None provides India DLT/WhatsApp BSP integration out of the box; Apprise/Novu would call SMS/WhatsApp gateways. Novu's multi-tenancy (organizations/environments) and exact release versions were not checked.

### Gaps
- Latest versions of Novu, ntfy, Apprise not retrieved. ntfy's dual-licence (Apache 2.0/GPLv2) detail, Novu's cloud-vs-self-host feature gaps unverified.

## 6. Licence verification of the blueprint table (corrections)

### Takeaway
Most blueprint entries check out. Corrections: Synapse is AGPL-3.0-or-later (not Apache 2.0); Gemma 4 is now Apache 2.0 (old Gemma terms obsolete); Orthanc is GPLv3+ with AGPLv3 on the PostgreSQL and web-viewer plugins; MinIO is AGPLv3 but community repo archived (Apr 2026); Bahmni's own components are AGPL 3.0; Mattermost, Chatwoot, Novu, Stalwart are open-core/dual.

### Cited Findings
| Component | Blueprint | Verified (2 Nov 2026) | Source |
|---|---|---|---|
| Keycloak | Apache 2.0 | Apache 2.0 - correct | [LICENSE.txt](https://raw.githubusercontent.com/keycloak/keycloak/HEAD/LICENSE.txt) |
| ERPNext | GPLv3 | GPLv3 - correct (license.txt) | [license.txt](https://raw.githubusercontent.com/frappe/erpnext/HEAD/license.txt) |
| Frappe | MIT | MIT - correct | [LICENSE](https://raw.githubusercontent.com/frappe/frappe/HEAD/LICENSE) |
| Orthanc | GPLv3; plugins AGPL? | Core GPLv3+; PostgreSQL and Web viewer plugins AGPLv3+; Stone/Osimis viewers AGPL; no dual licensing after 1.9.7 | [Orthanc Book](https://orthanc.uclouvain.be/book/faq/licensing.html) |
| OHIF | MIT | MIT - correct | [LICENSE](https://raw.githubusercontent.com/OHIF/Viewers/HEAD/LICENSE) |
| OpenELIS Global | MPL 2.0 | MPL 2.0 (I-TECH-UW/OpenELIS-Global-2 LICENSE) - correct | [LICENSE](https://raw.githubusercontent.com/I-TECH-UW/OpenELIS-Global-2/HEAD/LICENSE) |
| SENAITE | GPLv2 | GPLv2 (senaite.core LICENSE) - correct | [LICENSE](https://raw.githubusercontent.com/senaite/senaite.core/HEAD/LICENSE) |
| OIE/BridgeLink | MPL 2.0 | OIE (OpenIntegrationEngine/engine) MPL 2.0 confirmed; BridgeLink itself not checked | [LICENSE](https://raw.githubusercontent.com/OpenIntegrationEngine/engine/HEAD/LICENSE) |
| Nextcloud | AGPLv3 | AGPLv3 - correct | [COPYING](https://raw.githubusercontent.com/nextcloud/server/HEAD/COPYING) |
| Collabora Online | MPL 2.0 | MPL 2.0 - correct (CODE binaries: no production support) | [COPYING](https://raw.githubusercontent.com/CollaboraOnline/online/HEAD/COPYING) |
| Synapse | Apache 2.0 | WRONG: AGPL-3.0-or-later or Element Commercial Licence | [README](https://raw.githubusercontent.com/element-hq/synapse/develop/README.rst) |
| Stalwart | AGPLv3 | Partly right: dual AGPL-3.0 + proprietary SELv2; Enterprise-only features | [README](https://raw.githubusercontent.com/stalwartlabs/stalwart/main/README.md) |
| FastMCP | Apache 2.0 or MIT? | Apache 2.0 (PrefectHQ/fastmcp; jlowin/fastmcp redirects) | [LICENSE](https://raw.githubusercontent.com/PrefectHQ/fastmcp/HEAD/LICENSE) |
| CARE (ohcnetwork) | MIT | MIT, copyright 2026 Open Healthcare Network Foundation - correct | [LICENSE](https://raw.githubusercontent.com/ohcnetwork/care/HEAD/LICENSE) |
| OpenMRS | MPL 2.0 + Healthcare Disclaimer | MPL 2.0 with Healthcare Disclaimer - correct (core LICENSE is MPL 2.0; disclaimer per Wikipedia) | [openmrs-core LICENSE](https://raw.githubusercontent.com/openmrs/openmrs-core/HEAD/LICENSE), [Wikipedia](https://en.wikipedia.org/wiki/OpenMRS) |
| Bahmni | ? | New components/modifications AGPL 3.0; bundled products keep own licences (OpenMRS row: MPL 2.0) | [Bahmni licence](https://www.bahmni.org/license) |
| MinIO | AGPLv3 | AGPLv3 but community edition source-only/maintenance; repo archived 25 Apr 2026; admin console removed Jun 2025; paid AIStor | [Linuxiac](https://linuxiac.com/minio-ends-active-development/), [Blocks&Files](https://blocksandfiles.com/2025/06/19/minio-removes-management-features-from-basic-community-edition-object-storage-code/), [MinIO LICENSE](https://raw.githubusercontent.com/minio/minio/HEAD/LICENSE) |
| Grafana | AGPLv3 | AGPLv3 - correct | [LICENSE](https://raw.githubusercontent.com/grafana/grafana/HEAD/LICENSE) |
| Llama | Community licence | Llama Community Licence (not OSI): 700M MAU clause; Llama 4 EU-HQ restriction on multimodal models; no newer Llama found (secondary) | [Meta FAQ](https://developer.meta.com/ai/faq/), [conductatlas](https://conductatlas.com/platform/meta/llama-community-license-agreement/700-million-mau-commercial-threshold/) |
| Qwen | Apache 2.0 | Qwen3 / 3.5 / 3.6 open weights Apache 2.0 (per-checkpoint check needed; Qwen3.7 API-only; Qwen3.8 licence undetermined) | [Verdent](https://www.verdent.ai/guides/model/qwen-3-5), [OrcaRouter](https://www.orcarouter.ai/blog/qwen-3-8-27b-license) |
| Gemma | Gemma terms | Gemma 4 released under Apache 2.0 (Google blog; date shown as April 2, 2026 in press, URL path 2026/03), replacing custom Gemma terms; older Gemma 1-3 keep custom terms | [Google Open Source Blog](https://opensource.googleblog.com/2026/03/gemma-4-expanding-the-gemmaverse-with-apache-20.html), [Implicator](https://www.implicator.ai/google-releases-gemma-4-under-apache-2-0-dropping-its-custom-ai-license/) |
| Additional | - | Dendrite AGPL v3; Zulip Apache 2.0; Mattermost MIT-compiled/AGPL source/commercial; Chatwoot MIT + enterprise folder; Novu MIT + EE; ntfy Apache 2.0; Apprise BSD-2; Postal MIT; Mailcow GPLv3; ONLYOFFICE AGPLv3 (+extra terms) | see sections above |

### Inferences
- Table corrections to apply: Synapse (AGPL, not Apache); Gemma (Apache 2.0 for Gemma 4); Orthanc (specify AGPL plugins); Stalwart (dual licence + enterprise); MinIO (maintenance/archived - consider alternatives); Qwen (verify per checkpoint); Bahmni (AGPL).

### Gaps
- BridgeLink, Bahmni repo-level LICENSE, OpenMRS Healthcare Disclaimer file, Qwen/Llama primary licence texts not read. MinIO archived-date and status derive from a GitHub repo page and news (secondary).

## 7. SaaS licence implications and safest-to-modify components

### Takeaway
In a hosted SaaS you "convey" nothing for GPL (no source duty for modified GPL code you only run), but AGPL section 13 and ONLYOFFICE's AGPL variant require offering corresponding source of modified versions to network users; MPL is file-level copyleft triggered only on distribution (and typically not by SaaS use). Safest to modify: MIT / Apache / BSD components; riskiest: AGPL components you patch.

### Cited Findings
- Orthanc's own licensing FAQ states the "ASP loophole": GPL alone does not require sharing derived versions run on a server, which is why AGPL was chosen for cloud-related plugins (PostgreSQL, web viewers); calling Orthanc over REST/DICOM from a proprietary system is fine even with an AGPL plugin, while deriving from an AGPL plugin or viewer on a proprietary cloud platform is "restricted" (third-party code must go AGPLv3); plugins cannot be permissive-licensed — [Orthanc Book](https://orthanc.uclouvain.be/book/faq/licensing.html)
- ONLYOFFICE FAQ: modified AGPL product offered over a network (SaaS) must make modified source available under AGPL v3 and keep branding — [ONLYOFFICE FAQ](https://helpcenter.onlyoffice.com/docs/faq/docs-community.aspx)
- Mattermost promises not to enforce AGPL copyleft for apps that use only Admin Tools/Config files and do not modify Mattermost source — [Mattermost LICENSE.txt](https://raw.githubusercontent.com/mattermost/mattermost/HEAD/LICENSE.txt)
- Chatwoot `enterprise/` code requires a subscription for production use — [enterprise/LICENSE](https://raw.githubusercontent.com/chatwoot/chatwoot/HEAD/enterprise/LICENSE)

### Inferences
(General open-source licensing understanding, not from fetched sources; get counsel review.)
- GPL (ERPNext GPLv3, Mailcow, SENAITE GPLv2, Orthanc core GPLv3+): running a modified version as a hosted service is not "distribution", so no source duty unless you ship binaries/images/on-prem installers to hospitals. If you ship on-prem appliances, you must provide source for modified GPL components to those recipients.
- AGPL (Nextcloud, Synapse, Stalwart community, MinIO, Grafana, Dendrite, ONLYOFFICE, Orthanc PG/web-viewer plugins, Mattermost source): modifications that users interact with over the network trigger an offer of corresponding source of your modified version (the whole modified work, not unrelated separate services). Unmodified deployments carry no publication duty beyond not removing source-offer notices. Linking proprietary code into the same process is the risk; talking to them over HTTP/API is the standard separation.
- MPL 2.0 (Collabora, OpenMRS core, OIE/OpenELIS): file-level copyleft; if you modify an MPL file and distribute it, you must publish those modified files; no obligation for separate proprietary files; SaaS-only hosting generally does not trigger distribution. OpenMRS's Healthcare Disclaimer is an added notice, not a restriction.
- Permissive (MIT/Apache/BSD: Frappe, OHIF, CARE, Keycloak, FastMCP, Zulip, ntfy, Apprise, Postal, Chatwoot core, Novu core): safest to modify and embed; keep notices (Apache also requires NOTICE and patent terms). Beware open-core carve-outs (Chatwoot `enterprise/`, Novu `ee/`, Stalwart Enterprise features).
- Practical rule: keep AGPL/GPL components unmodified and configured via APIs/plugins you own in separate repos; put your proprietary IP in MIT/Apache layers (Frappe apps are MIT; note ERPNext apps themselves are GPLv3, so custom apps linking ERPNext code inherit GPLv3 only if distributed). Model licences: Llama community licence is not OSI and has MAU/EU/AUP conditions; Apache 2.0 models (Qwen3.x, Gemma 4) avoid these.

### Gaps
- No legal opinion obtained; interpretation of AGPL "modified version" boundaries for sidecar/plugin architectures, and treatment of ONLYOFFICE's added terms, need counsel. Euro-Office fork status and relevance not assessed.
