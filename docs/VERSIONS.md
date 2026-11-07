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
| object storage (upstream compose) | pgsty/silo:latest (unpinned upstream; replace per ADR-003 before staging) | 2026-11-03 |
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

## Identity — P1-T3

| Item | Version | Checked |
|---|---|---|
| Keycloak | 26.8.2 (2026-11-05; fixes a 2FA-setup bypass, an organisation-login takeover and a minimum-ACR bypass in older 26.x) — `quay.io/keycloak/keycloak:26.8.2@sha256:3879c623248408de29a5303178b5816c77e8dfef3890c361f8c0bbeaa979ba3b` | 2026-11-06 |
| Keycloak database (dev) | the dev Postgres 17.11, own database and owner role `keycloak` | 2026-11-06 |
| Keycloak database (CI) | `postgres:18.6-alpine@sha256:123b17998ccaf47e53b502e8c6a1e79afd8f579c6f3eec83c563da026f957395` | 2026-11-06 |
| Password block list | SecLists commit `2f31b22e1f9963ec2fd2b12606c00f0fb04910f1` (MIT), filtered to 12+ characters | 2026-11-06 |
| Mailpit (dev and CI mail catcher, P1-T5c) | v1.33.0 (2026-10-31) — `axllent/mailpit:v1.33.0@sha256:9b3a463be15354c0f0eba91697d80e3e0d8a5623ef0e627cacc836bc17fd5231`; version check off (`MP_DISABLE_VERSION_CHECK`); uses about 11 MB | 2026-11-07 |
| keycloak-config-cli | not used: latest release v6.5.1 (2026-05-22) predates Keycloak 26.7 and mishandles 26.8 organisations | 2026-11-06 |

Measured locally with the CARE stack running: Keycloak uses about 540 MB (limit 1 GB, heap 50%); first start about 30 s.
