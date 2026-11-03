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
| Django (security bump) | 6.0.8 | 2026-11-03 |
| djangorestframework | 3.18.2 | 2026-11-03 |
| vite | 8.3.5 | 2026-11-03 |
