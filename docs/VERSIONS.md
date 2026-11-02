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
