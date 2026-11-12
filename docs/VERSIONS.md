# Pinned versions

Every tool and dependency we install, with its exact version and when it was checked. Re-check for the latest stable release before upgrading.

## Workstation (dev machine) — P0-T2

| Tool | Version | Source | Checked |
|---|---|---|---|
| Ubuntu | 26.04.1 LTS (resolute) | — | 2026-11-03 |
| Docker Engine | 29.9.2 | download.docker.com apt (resolute) | 2026-11-03 |
| containerd | 2.4.2 | download.docker.com apt | 2026-11-03 |
| Docker Compose | 5.7.0 | download.docker.com apt | 2026-11-03 |
| Docker Buildx | 0.38.1 | download.docker.com apt | 2026-11-03 |
| GitHub CLI (gh) | 2.103.3 | cli.github.com apt, pinned over Ubuntu ESM | 2026-11-03 |
| uv | 0.13.6 | astral.sh installer → `~/.local/bin` | 2026-11-03 |
| pre-commit | 4.7.0 | `uv tool install` | 2026-11-03 |
| pnpm | 12.16.1 | `npm install -g` (nvm Node) | 2026-11-03 |
| Node.js | 25.6.0 | nvm (already present; project-level version set in P0-T3/P0-T5 to match CARE) | 2026-11-03 |
| Python | 3.14.4 | system (project interpreters managed by uv) | 2026-11-03 |
| git | 2.53.0 | system | 2026-11-03 |

Setup script: [deploy/dev/setup-workstation-root.sh](../deploy/dev/setup-workstation-root.sh). It needs sudo and must be run from a normal Ubuntu terminal (Ctrl+Alt+T), not the Claude app's terminal.

## Repo tooling — P0-T3

| Item | Version | Checked |
|---|---|---|
| pre-commit-hooks | v6.0.0 | 2026-11-03 |
| gitleaks | v8.30.1 | 2026-11-03 |
| actionlint | v1.7.12 | 2026-11-03 |
| actions/checkout | v7.0.1 | 2026-11-03 |
| actions/setup-python | v7.1.0 | 2026-11-03 |
| CI runner | ubuntu-24.04, Python 3.14 | 2026-11-03 |

## Upstream CARE — P0-T4

| App | Upstream | Commit | Date | Checked |
|---|---|---|---|---|
| Backend | ohcnetwork/care `develop` | 67a46185eb98 | 2026-11-01 | 2026-11-03 |
| Frontend | ohcnetwork/care_fe `develop` | 57205b897804 | 2026-11-02 | 2026-11-03 |

Last GitHub Releases are v3.0.0 (backend, 2025-02-12) and v3.0.32 (frontend, 2025-12-23), so we pin `develop` commits. Source of truth: `upstream.lock`.

## CI actions — P0-T8

| Item | Version | Checked |
|---|---|---|
| actions/setup-node | v7.1.0 (Node from `apps/frontend/.node-version` = 24) | 2026-11-03 |
| docker/setup-buildx-action | v4.4.1 | 2026-11-03 |
| dorny/paths-filter | v4.0.3 | 2026-11-03 |
| aquasecurity/trivy-action | v0.36.0 | 2026-11-03 |
| ruff (backend lint, matches upstream) | 0.14.8 | 2026-11-03 |

## Hardening — P1-T12

| Item | Version | Checked |
|---|---|---|
| nginxinc/nginx-unprivileged (frontend image) | 1.30.6-alpine | 2026-11-03 |
| Trivy (local CLI image) | 0.76.1 | 2026-11-03 |
| Django (security bump) | 6.0.8 → **6.1.2** (P0-T13) | 2026-11-04 |
| djangorestframework | 3.18.2 → **3.18.3** (P0-T13) | 2026-11-04 |
| vite | 8.3.5 | 2026-11-03 |

## Local CARE stack — P0-T5

| Item | Version | Checked |
|---|---|---|
| postgres (upstream compose) | 17-alpine | 2026-11-03 |
| redis (upstream compose) | 8-alpine | 2026-11-03 |
| object storage (upstream compose) | pgsty/silo:latest (unpinned upstream): still started by CI's upstream `make up` jobs; dev and staging use SeaweedFS since P1-T8b3 (below) | 2026-11-09 |
| backend dev image `care_local` | built from apps/backend @ upstream.lock, 1.07 GB | 2026-11-03 |
| actions/upload-artifact | v7.0.2 | 2026-11-03 |

## Postgres compatibility — P0-T11

| Item | Version | Result | Checked |
|---|---|---|---|
| postgres (staging/production target) | 18.6-alpine | CARE suite 2,005/2,005 pass | 2026-11-03 |
| postgres (local dev, upstream compose) | 17-alpine | unchanged for now | 2026-11-03 |

## CI hardening — P0-T12

| Item | Version / pin | Checked |
|---|---|---|
| actions/checkout | v7.0.1 @ 3d3c42e5aac5ba805825da76410c181273ba90b1 | 2026-11-04 |
| actions/setup-python | v7.1.0 @ 6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 | 2026-11-04 |
| actions/setup-node | v7.1.0 @ 949feb2413d6458794dcd2491c4babbbce0c15c1 | 2026-11-04 |
| actions/upload-artifact | v7.0.2 @ cf430e030ddbb5b0abf93d22962f4752f3646cd9 | 2026-11-04 |
| docker/setup-buildx-action | v4.4.1 @ f87e5991a6d7451dcb8d9637bfbc97413f497069 | 2026-11-04 |
| dorny/paths-filter | v4.0.3 @ ceb8a2b8f2d89434be7ff52d3de7ec3738c5cc9d | 2026-11-04 |
| aquasecurity/trivy-action | v0.36.0 @ ed142fd0673e97e23eac54620cfb913e5ce36c25 | 2026-11-04 |
| gitleaks (CI image) | v8.30.1 @ sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f | 2026-11-04 |
| shellcheck (pre-commit) | v0.11.0 | 2026-11-04 |
| python base (backend image) | 3.13.18-slim-bookworm | 2026-11-04 |
| aquasecurity/setup-trivy (used inside trivy-action) | v0.2.6 @ 3fb12ec12f41e471780db15c232d5dd185dcb514 | 2026-11-04 |

**GitHub repo setting:** `allowed_actions=selected`, `github_owned_allowed=true`, `verified_allowed=false`, `sha_pinning_required=true`; allow-list = the four third-party SHAs above (buildx, paths-filter, trivy-action, setup-trivy).

## Latest versions — P0-T13

Backend lock fully re-resolved on 2026-11-04 (latest compatible for every package). Key pins: Django 6.1.2, DRF 3.18.3, PyJWT 2.15.1, cryptography 50.0.2, Authlib 1.9.3, aiohttp 3.14.5, sqlparse 0.6.1, certifi 2026.10.23. Packages still behind are pinned by CARE upstream's `Pipfile`; see the weekly **Outdated dependencies report** job in Apps CI (`scripts/outdated-report.py`).

## Tenancy — P1-T1a

| Item | Version | Checked |
|---|---|---|
| django-tenants | 4.0.1 (2026-10-23, security release; declares Django 5.2–6.1) | 2026-11-06 |
| tenant-schemas-celery | 5.0.0 (2026-08-01; requires celery>=5) | 2026-11-06 |
| ruff (pre-commit, plugins/) | v0.14.8 (same as CARE's config) | 2026-11-06 |

## Staging stack — P1-T8a

| Component | Version | Pinned how |
|---|---|---|
| Traefik | v3.7.17 (released 30-10-2026, latest stable) | tag + index digest written to `deploy/staging/.env` by `staging.sh init` |
| PostgreSQL | 18.6-alpine | tag + digest (as above) |
| Redis | 8.10.3-alpine (released 26-10-2026) | tag + digest (as above) |
| Keycloak | 26.8.2 | digest in `deploy/staging/compose.yaml` (same as dev) |
| Backend image | `deploy/docker/backend.Dockerfile` + `plugins/` (second build context) | built per commit |

## Reference data — P1-T7a

| Data | Source | Licence | Loaded |
|---|---|---|---|
| India Post PIN code directory | data.gov.in, "All India Pincode Directory" (Department of Posts; resource `5c2f62fe-5afa-4119-a499-fec9d604d5bd`) — downloaded by hand (name, e-mail, captcha) | Government Open Data License – India (attribution: the lookup API names the source) | Not yet: only the 10-office test sample in dev. Record the release date and sha256 (`ihr_load_pincodes --show`) here when loaded |

## Identity — P1-T3

| Item | Version | Checked |
|---|---|---|
| Keycloak | 26.8.2 (2026-11-05; fixes a 2FA-setup bypass, an organisation-login takeover and a minimum-ACR bypass in older 26.x; still the newest release on 2026-11-11, no 26.8.3 or 27.x) — `quay.io/keycloak/keycloak:26.8.2@sha256:723b1d6e254c0fc96d5cffec860c7e0744d6a3277822b865bb538cf891575e41` (quay's re-push `26.8.2-1` of 2026-11-09: same source revision, refreshed base OS; was `…3879c623…`, `26.8.2-0`) in dev, CI and as the base of our staging build | 2026-11-11 |
| Keycloak, staging (P1-T8d1) | `deploy/docker/keycloak.Dockerfile`: the image above pre-built (`kc.sh build`: Postgres, health, metrics, device-flow/CIBA/impersonation/Kerberos off), `start --optimized`, single-node cache (`KC_CACHE=local`), forwarded headers trusted from Traefik's fixed address only; built by `scripts/deploy.sh`, CI job "Keycloak image" (build, smoke, Trivy). One reviewed Trivy exception: CVE-2026-102554 in Keycloak's own Guava 33.6.0 (needs Java native deserialization of untrusted data, which Keycloak does not do; `.trivyignore.yaml`, expires 2026-12-31) | 2026-11-11 |
| Keycloak database (dev) | the dev Postgres 17.11, own database and owner role `keycloak` | 2026-11-06 |
| Keycloak database (CI) | `postgres:18.6-alpine@sha256:123b17998ccaf47e53b502e8c6a1e79afd8f579c6f3eec83c563da026f957395` | 2026-11-06 |
| Password block list | SecLists commit `2f31b22e1f9963ec2fd2b12606c00f0fb04910f1` (MIT), filtered to 12+ characters | 2026-11-06 |
| Mailpit (dev and CI mail catcher, P1-T5c) | v1.33.0 (2026-10-31) — `axllent/mailpit:v1.33.0@sha256:9b3a463be15354c0f0eba91697d80e3e0d8a5623ef0e627cacc836bc17fd5231`; version check off (`MP_DISABLE_VERSION_CHECK`); uses about 11 MB | 2026-11-07 |
| keycloak-config-cli | not used: latest release v6.5.1 (2026-05-22) predates Keycloak 26.7 and mishandles 26.8 organisations | 2026-11-06 |

Measured locally with the CARE stack running: Keycloak uses about 540 MB (limit 1 GB, heap 50%); first start about 30 s.

## Host kit — P1-T8b1

| Item | Version | Source | Checked |
|---|---|---|---|
| Server OS | Ubuntu 26.04 LTS (resolute); 24.04 LTS (noble) accepted | Q-E8 | 2026-11-09 |
| Local test VM image | `ubuntu-26.04-server-cloudimg-amd64.img`, serial **20261016**, SHA-256 `5645d0318ad21f643ebb1ac4567514620e9893380b07b0201fda163baa5b7eb5` | cloud-images.ubuntu.com/releases/resolute/release-20261016; `SHA256SUMS.gpg` signed by D2EB 4462 6FDD C30B 513D 5BB7 1A5D 6C4C 7DB8 7C81 (UEC Image Automatic Signing Key; `ubuntu-cloudimage-keyring`) | 2026-11-09 |
| docker-ce, docker-ce-cli | 5:29.9.3-1~ubuntu.26.04~resolute (…~ubuntu.24.04~noble) | download.docker.com apt, key 9DC8 5822 9FC7 DD38 854A E2D8 8D81 803C 0EBF CD88; held | 2026-11-09 |
| containerd.io | 2.4.2-3~ubuntu.26.04~resolute | same | 2026-11-09 |
| docker-buildx-plugin | 0.38.1-1~ubuntu.26.04~resolute | same | 2026-11-09 |
| docker-compose-plugin | 5.7.0-1~ubuntu.26.04~resolute | same | 2026-11-09 |
| chrony, fail2ban, ufw, unattended-upgrades | Ubuntu archive version of the release (security updates via unattended-upgrades) | Ubuntu | 2026-11-09 |
| Time servers | samay1.nic.in, samay2.nic.in (NIC, stratum 2), time.nplindia.org (NPL, stratum 1); no NTS | CERT-In FAQ Q43; live NTP query | 2026-11-09 |

The workstation now runs the same Docker packages (29.9.3; the P0-T2 row above shows 29.9.2 as first installed).

## Wildcard certificate — P1-T8b2

| Item | Version | Pinned how | Checked |
|---|---|---|---|
| acme-dns | source commit `43b0acaacef24d1058d70ed584d6b7275a8344ed` (08-06-2026) = v2.0.2 (05-02-2026, latest release) + the unreleased DNS read-path bound and TXT index; MIT | `ADD --checksum=sha256:7faa9845…7206` of the GitHub archive, built by `deploy/docker/acme-dns.Dockerfile` (upstream's image is `alpine:latest` + root) | 2026-11-09 |
| acme-dns module overlay | `deploy/docker/acme-dns/go.mod` + `go.sum`: golang.org/x/crypto v0.58.0, x/net v0.62.0, x/text v0.43.0 (+ x/sys 0.49.0, x/sync 0.24.0, x/mod 0.41.0, x/tools 0.51.0) instead of upstream's 0.38/0.40/0.25, which carry fixable HIGH CVEs; github.com/klauspost/compress v1.20.3 instead of 1.15.13 (test-only, HIGH CVE-2026-63209 flagged by the repository scan); upstream's tests pass with them | `go mod verify` in the build | 2026-11-09 |
| Go (build only) | 1.27.3 — `golang:1.27.3-alpine3.23@sha256:5d5113301ffcac84e2bfb20c42648803fecaea6789c89bb9b66884e18cbb30a7`, `GOTOOLCHAIN=local` | digest | 2026-11-09 |
| acme-dns base image | `gcr.io/distroless/static-debian13:nonroot@sha256:25ac8f0c3b2334a9eb05452266bd4697864a8c8ef0ef71e5e6480d7f92868329` (no shell, user 65532) | digest | 2026-11-09 |
| Traefik certificate library | lego v5.6.0 (inside Traefik v3.7.17): DNS provider `acmedns`, no ARI; renews 30 days before expiry (`certificatesDuration` 2160) | — | 2026-11-09 |
| Pebble (proof only) | v2.10.1 (20-04-2026) — `ghcr.io/letsencrypt/pebble:2.10.1@sha256:ddf230642b1a584f519f32e347de1b05a6e4c1f6c35c1863b33effeab5f78199` | digest in `compose.pebble.yaml` | 2026-11-09 |
| CoreDNS (proof only) | 1.15.0 (21-10-2026) — `coredns/coredns:1.15.0@sha256:0a9d8c8ddf34347eb59aad9a8784f0710de88d6d112726d11d271f9bc6daca26` | digest in `compose.pebble.yaml` | 2026-11-09 |
| Let's Encrypt | classic profile: 90-day certificates now, 64 days from 10-02-2027, 45 days from 16-02-2028; limits: 50 certificates per registered domain per week, 5 duplicates per week, 5 failed validations per name per hour | letsencrypt.org/docs/rate-limits, /docs/profiles | 2026-11-09 |

Regenerate the module overlay when Trivy flags the image: in upstream's source at the pinned commit run `go get golang.org/x/crypto@latest golang.org/x/net@latest golang.org/x/text@latest github.com/klauspost/compress@latest && go mod tidy` (Go 1.27.3), check `go test ./...`, copy `go.mod` and `go.sum` to `deploy/docker/acme-dns/`.

## Object storage — P1-T8b3

| Item | Version | Pinned how | Checked |
|---|---|---|---|
| SeaweedFS | 4.53 (08-11-2026, latest; Apache-2.0) — `chrislusf/seaweedfs:4.53@sha256:ccad23e18639c8b98cee17e7100b95830a06077b56d8e5b006d6dcf8ec5e94a0`. Object Lock deletion fixes landed through 4.50–4.52 (issues #11333, #12006): stay at 4.52 or later | digest in `deploy/staging/compose.s3.yaml` and `deploy/dev/care-local.override.yaml` | 2026-11-09 |
| Replaced | upstream CARE's `docker.io/pgsty/silo:latest` (an unpinned MinIO fork) in dev: kept out of every start (`profiles`) | — | 2026-11-09 |

Measured: storage + gateway about 57 MB together in dev (MinIO had a 300 MB limit).

## Cloud modules and mail — P1-T8b4

| Item | Version | Pinned how | Checked |
|---|---|---|---|
| OpenTofu | 1.13.9 (05-11-2026) | `required_version = ">= 1.13.9, < 1.14.0"`; workstation binary in `~/.local/bin` and CI install both check the release signature (OpenTofu key E3E6 E43D 84CB 852E ADB0 051D 0C0A F313 E5FD 9F80) and SHA-256 | 2026-11-09 |
| E2E provider | `e2eterraformprovider/e2e` 2.2.12 (31-08-2026); checksums signed by 0915 4369 65FE BBA4 428B A77C 3EF4 0241 C2FB 9A1B; repository has no licence file (tool only, never shipped) | exact version + `infra/e2e/.terraform.lock.hcl` | 2026-11-09 |
| AWS provider | `hashicorp/aws` 6.72.0 (04-11-2026), OpenTofu registry build signed by OpenTofu's key | exact version + lock files | 2026-11-09 |
| Ubuntu on AWS | Canonical's SSM pointer `/aws/service/canonical/ubuntu/server/26.04/stable/current/amd64/hvm/ebs-gp3/ami-id` (image changes never rebuild the server) | resolved at apply | 2026-11-09 |
| Mail | Zoho ZeptoMail SMTP `smtp.zeptomail.in:587` STARTTLS (certificate `*.zeptomail.in`, Sectigo, checked live), user `emailapikey` | `scripts/staging.sh mail-init` defaults | 2026-11-09 |
| Mail stand-in (proofs) | Mailpit v1.33.0 (same pin as dev) with STARTTLS required and a password file | `deploy/staging/compose.mailtest.yaml` | 2026-11-09 |

Lock files carry the linux_amd64 `h1:` hashes only: GitHub's release-asset host answered 504 to
OpenTofu's own downloads on 09-11-2026, so the packages were fetched through GitHub's API, checked
by signature and SHA-256, and installed from a local mirror (`~/.cache/indiehr/tofu.rc`, this
machine only). Add other platforms with `tofu providers lock -platform=…` when needed.

## Backups — P1-T8c

| Item | Version | Pinned how | Checked |
|---|---|---|---|
| pgBackRest | 2.60.0 (18-10-2026; Alpine packages 2.58) built from the release tarball (tag commit 2e1c4f2b) | `ADD --checksum=sha256:7409309d…22d0` in `deploy/docker/postgres.Dockerfile` | 2026-11-09 |
| Postgres image | `postgres:18.6-alpine3.23@sha256:f1031bf693d8d60b0a0380c51a3510607c7a93c7c1d18bf7af94a3a8dbeb40b8` (re-pinned 2026-11-11) + pgBackRest; Alpine's pending security updates applied at build (`apk upgrade`: the base lacked fixed `krb5-libs` and `libxslt`, 20 HIGH advisories on 2026-11-11); `gosu` removed (old Go, 26 CVEs; never needed as the image runs as postgres) | digest | 2026-11-09 |

## Metrics and e-mail alerts — P1-T9a

Every image pinned by its multi-arch index digest in `deploy/staging/compose.monitoring.yaml`
(resolved with `docker buildx imagetools inspect`; each is the project's latest release on GitHub,
checked 2026-11-11). Release notes read for the two newest: Prometheus 3.16.0 (no removed flags,
promtool unchanged for us) and Alertmanager 0.35.0 (only `--enable-feature=auto-gomemlimit` became
a flag, eventrecorder outputs need names; neither used here).

| Item | Version | Pinned how | Checked |
|---|---|---|---|
| Prometheus (and promtool) | v3.16.0 (05-11-2026; Apache-2.0) — `prom/prometheus:v3.16.0@sha256:e04987641b57704f408e31f6373ba4138ecf27cb4a165d24aabb6c38fddd0f06`; user nobody | digest in compose; `scripts/check-monitoring.sh` reads it from there | 2026-11-11 |
| Alertmanager (and amtool) | v0.35.0 (09-11-2026; Apache-2.0) — `prom/alertmanager:v0.35.0@sha256:e638797febd034838eca8b98cdbd46f951e9bf7f178777aa8669167406a9946c`; runs as the owner of `secrets/` | digest | 2026-11-11 |
| node_exporter | v1.12.1 (14-07-2026; Apache-2.0) — `prom/node-exporter:v1.12.1@sha256:1b4e4438faca4dd7e001dd445d161a4a2091b0fededa84093b3a8dfeae1f1be0` | digest | 2026-11-11 |
| blackbox_exporter | v0.29.1 (21-10-2026; Apache-2.0) — `prom/blackbox-exporter:v0.29.1@sha256:ea1c7aa3ef3f307cd03e85f34abb604478ccb90b567fc690da0cd9776da76b13`; the image runs as root, we run it as nobody | digest | 2026-11-11 |
| postgres_exporter | v0.20.1 (08-07-2026; Apache-2.0) — `prometheuscommunity/postgres-exporter:v0.20.1@sha256:ac5ec343104fae0e2d84a27bb8d69b38430a11910c5382cad85d478d2bab713e` | digest | 2026-11-11 |
| redis_exporter | v1.97.0 (04-11-2026; MIT) — `oliver006/redis_exporter:v1.97.0@sha256:f5f903fbe77571d455ede0453b084d6840bc62fc105efac001034421cab83b99`; its `--debug` prints the Redis password (seen 2026-11-11): never used | digest | 2026-11-11 |
| Dead-man switch | healthchecks.io (free plan, owner's account, Q-O4): pinged by Alertmanager's Watchdog about once a minute | URL in `secrets/alertmanager-watchdog-url` | 2026-11-11 |
| Outside uptime check | UptimeRobot (free plan, owner's account, Q-O4) on `https://status.<suffix>/healthz` | — | 2026-11-11 |
| Alert mail | Zoho ZeptoMail `smtp.zeptomail.in:587` STARTTLS, user `emailapikey`, a Mail Agent and token of its own for alerts | `scripts/staging.sh monitoring-init` defaults | 2026-11-11 |

Memory limits: Prometheus 384 MB, Alertmanager 64 MB, the exporters 48–64 MB each (about 0.7 GB in
all). Not measured live yet (the stack was not started in this task).

## Log collection, redaction and log alerts — P1-T9b

Pinned by multi-arch index digest in `deploy/staging/compose.logs.yaml` (digests read from the
registry and pulled; each is the project's latest stable release on GitHub, checked 2026-11-12).
`scripts/check-logs.py` reads the digests from there.

| Item | Version | Pinned how | Checked |
|---|---|---|---|
| Fluent Bit | 5.2.0 (09-11-2026; Apache-2.0) — `fluent/fluent-bit:5.2.0@sha256:a48190bf67679495a332fd61025421a46e7e4d168380185a8e16da2e9acc3126`; distroless, in_systemd and LuaJIT built in; the image runs as root, we run it as nobody with the journal's group. 5.1.5 is the previous line; the cursor-rewrite issue #12778 is in both | digest | 2026-11-12 |
| VictoriaLogs | v1.53.0 (01-10-2026; Apache-2.0; v1.52.1 is the LTS line) — `victoriametrics/victoria-logs:v1.53.0@sha256:251121fa882af99b95ba0c230a4a2f412ea602d2698c64a96c58dc9842bb755d`; runs as the owner of `secrets/` (reads the admin key) | digest | 2026-11-12 |
| vmalert | v1.156.0 (09-11-2026; Apache-2.0) — `victoriametrics/vmalert:v1.156.0-scratch@sha256:e1ee6661c4c2f5fe811090a03069a9b8b75f55c137d7892acd2197dba34ec47b` (no shell); runs as nobody. `vmalert-tool` cannot test LogsQL rules yet (upstream #11049): `scripts/check-logs.py` runs vmalert itself on fixtures | digest | 2026-11-12 |

Memory limits: VictoriaLogs 256 MB, Fluent Bit 128 MB, vmalert 64 MB.
