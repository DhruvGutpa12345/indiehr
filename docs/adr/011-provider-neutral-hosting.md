# ADR-011: Provider-neutral hosting (sovereign, switchable)

- **Status:** Accepted
- **Date:** 2026-11-08
- **Decided by:** product owner (this conversation), on research checked against provider
  pages, price files and docs fetched 2026-11-08
- **Amends:** ADR-003 (Amendment 2), ADR-010 §9 (lock mode, see below)

## Context

The owner wants a sovereign architecture: never bound to one provider, switching as easy as
possible, nothing paid until needed, trials on this machine. Findings that shaped this:

- **"AWS Delhi" is not a region.** Delhi is an AWS Local Zone (`ap-south-1-del-1a`, parent
  Mumbai): EC2 (T3/C5/M5/R5/G4dn only) and gp2 disks, **no S3, no RDS, no Graviton**. Its
  smallest machine with 8 GB or more, t3.xlarge, costs about $177 (~₹16,600) a month, about four
  times a Mumbai/Hyderabad t4g.large (~₹3,070 + disk + IP ≈ ₹4,300 before GST).
- **E2E Networks** (Indian, MeitY-empanelled per its 2024 NSE filing) sells 4 vCPU / 8 GB /
  100 GB NVMe (C3.8GB) for ₹3,422 a month + GST in **Delhi NCR**, with a second region in
  **Chennai**; S3-compatible object storage at ₹625 for the first 250 GB. Its object lock is
  **Governance mode only** (an account admin can shorten or remove it); no COMPLIANCE mode.
  Its OpenTofu provider (e2e 2.2.12) covers servers, object stores and networks, not DNS; its
  "start script" (2,048 characters, every boot) is not full cloud-init.
- No Indian provider is supported by lego (Traefik's certificate library) for DNS checks.
- Zero-cost hosted options all fall short for us: Oracle Always Free (Hyderabad/Mumbai) is now
  2 ARM cores / 12 GB, needs a credit card, can be reclaimed when idle, and has no S3-style
  object lock; AWS's free plan lasts at most 6 months and then closes the account (which
  conflicts with locked buckets); Cloudflare Tunnel decrypts traffic outside India; Netlify,
  Vercel and Cloudflare Pages host only the static web app, not the backend or database.

## Decision (owner, 2026-11-08)

1. **Target host: E2E Networks, Delhi NCR**, backups and object replication to **E2E Chennai**.
   AWS Mumbai (VM) + Hyderabad (backups) is kept as the documented alternative; the AWS Delhi
   Local Zone is not used (no storage there, about four times the price).
2. **Trials run on this machine only** until a paid host is switched on: the staging stack
   (`scripts/staging.sh local-proof`) with a local certificate authority, a local S3 store
   and a local mail catcher. No public URL; no cost; synthetic data only.
3. **The audit fingerprint bucket lives with the server's provider.** On E2E that means
   Governance-mode locks, which the owner accepts knowing that an E2E account admin could
   remove them (ADR-010 §9 asked for COMPLIANCE mode). Mitigation: the bucket sits in a
   separate E2E account or project whose credentials the server and its admins never hold; the
   anchor code accepts Governance mode only when `IHR_AUDIT_ANCHOR_LOCK_MODE=GOVERNANCE` is set
   explicitly (P1-T8c), and the status page says so. Moving to COMPLIANCE later is a change of
   endpoint and keys only (AWS S3 Mumbai, or SeaweedFS).
4. **amhcloud.in** stays at its registrar. Hospitals live at `<slug>.app.amhcloud.in`; the
   wildcard certificate is checked by DNS through **acme-dns** that we run ourselves: one CNAME
   at the registrar (`_acme-challenge.app.amhcloud.in` → our acme-dns name) and one NS record
   for that name. Works with any registrar and any host; no DNS provider API needed.

## The layout that keeps switching cheap

| Layer | Provider-neutral part (same everywhere) | Provider-specific part (swap one piece) |
|---|---|---|
| Server | `deploy/host/bootstrap.sh`: one idempotent script for Ubuntu LTS (Docker, firewall, SSH hardening, automatic security updates, **NTP to NIC/NPL servers**, **journald kept 180 days**, deploy user). Run over SSH from this machine, so it needs no cloud-init | `infra/<provider>/` (OpenTofu 1.13.9, MPL-2.0): creates the VM, disk, firewall and buckets. `e2e/` first, `aws/` second. Same inputs (size, region, SSH key) and outputs (IP, bucket endpoints) |
| App | `deploy/staging/compose.yaml` unchanged | `.env` values only: S3 endpoint/region/keys, SMTP, certificate settings |
| Files, backups, audit anchors | Plain S3 API (CARE files, pgBackRest 2.60 with `repo-s3-endpoint` + path style, our anchor code) | Endpoint, region, keys; lock mode (above) |
| Certificates | Traefik + Let's Encrypt DNS-01 through our own acme-dns | Nothing (registrar keeps one CNAME and one NS record) |
| Mail | Plain SMTP with TLS | Which SMTP service (decided in P1-T8b; must be India-hosted) |
| Database, Redis, Keycloak | Our own containers (no managed database) | Nothing |
| Local test double | Pebble (Let's Encrypt's test CA), SeaweedFS (local S3 with object lock), Mailpit | — |

**Switching providers** then means: run the other `infra/<provider>/` module, run the same
bootstrap, restore the database from the pgBackRest repository (or copy it), copy the buckets,
change the `.env` values, change one A record at the registrar. No application change.

**Rules:**
- Nothing in `apps/`, `plugins/` or `deploy/staging/compose.yaml` may name a provider.
- No provider-managed service that has no portable equivalent (managed databases, provider
  queues, provider KMS) without a new ADR.
- OpenTofu state is encrypted (passphrase key) and kept in an Indian S3-compatible bucket.
- MinIO is not used for new work: its community edition was archived in April 2026. Local
  object storage moves to SeaweedFS (dev's MinIO container is replaced in P1-T8b).

## Costs (staging, per month, before 18% GST)

| Option | Server | Storage | Total (approx.) |
|---|---|---|---|
| **E2E Delhi NCR (chosen)** | ₹3,422 (4 vCPU / 8 GB / 100 GB) | ₹625 (first 250 GB) | **≈ ₹4,050 (≈ ₹4,780 with GST)** |
| AWS Mumbai + Hyderabad | ≈ ₹3,070 (t4g.large) + ₹860 disk + ₹340 IP | S3 ≈ ₹2.35/GB | ≈ ₹4,300 + storage |
| AWS Delhi Local Zone | ≈ ₹16,600 (t3.xlarge) + ₹2,460 gp2 | in Mumbai | ≈ ₹19,000+ |
| This machine (now) | ₹0 | ₹0 | ₹0 |

## Open items

- **Confirm with E2E in writing:** COMPLIANCE-mode object lock on the roadmap; SLA figure
  (their pages say both 99.9% and 99.95%); MeitY empanelment on the official list (the MeitY
  site was not reachable from here).
- **Decided 2026-11-09 (owner):** mail via Zoho ZeptoMail (India data centre; written confirmation of India-only storage before go-live), Ubuntu 26.04 LTS, SSH key-only and rate-limited. Time servers (CERT-In FAQ Q43, checked live 09-11-2026): `samay1.nic.in`, `samay2.nic.in` (NIC, stratum 2) and `time.nplindia.org` (NPL, stratum 1); none offers NTS.
- **E2E start script** runs on every boot, so the bootstrap must stay idempotent.
- **Certificate (P1-T8b2, built 2026-11-09):** acme-dns runs on the same server (our own image,
  port 53 open in the firewall). The registrar keeps three records, not two: an A record for the
  NS name (`acme-ns.amhcloud.in`; kept outside the delegated zone so no glue record is needed),
  the NS record for `acme.amhcloud.in`, and the `_acme-challenge.app.amhcloud.in` CNAME; plus
  the hospitals' `*.app.amhcloud.in` A record and, recommended, a CAA record for Let's Encrypt.
  Runbook: `deploy/staging/README.md`.

## Sources (fetched 2026-11-08)

AWS price list files and Local Zones features page; AWS Free plan pages; E2E rate card
(e2enetworks.com/pricing.md, 2026-11-04) and object-lock docs (2026-11-02); E2E NSE filing
(19-06-2024); OpenTofu releases (v1.13.9, 05-11-2026) and registry; lego v5.6.0 DNS provider
list (28-10-2026); Oracle Always Free docs; pgBackRest v2.60.0; Pebble v2.10.1; SeaweedFS 4.52;
MinIO repository archive notice (25-04-2026). Full notes with URLs:
`docs/reviews/p1-t8/hosting-research.md`.
