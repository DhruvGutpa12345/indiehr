# P1-T9d: operator dashboards, tool scorecard (2026-11-12)

The run-until rubric (§5): hard gates first, then weighted scores (0–5). Every number was
measured on 2026-11-12 with `gh api`, the Docker registry, api.securityscorecards.dev, and the
images run locally with `--network none`. The raw research is in this folder's notes below.

## Hard gates

| Gate | Grafana OSS 13.3.1 | Perses v0.55.1 | Built-in UIs (Prometheus, vmui) |
|---|---|---|---|
| Licence | AGPL-3.0: allowed unmodified and reached over the network (CLAUDE.md). The plugin is baked in beside it, not modified | Apache-2.0 | Apache-2.0 |
| No outside calls | Yes, once switched off. Reporting, update checks, plugin preinstall and updates, news, gravatar, external snapshots and the key fetch all have settings, and it started cleanly with `--network none` | No telemetry found; started with `--network none` | Yes |
| Security (Trivy) | Decided in CI (the image job). 13.3.1 fixes 6 CVEs | Not scanned | Already scanned |
| Fit (RAM, stack) | 516 MiB idle measured; fits the 8 GB server, tight on 6.7 GB | 327 MiB idle | 0 |
| **Can it do the task (dashboards)?** | Yes | Yes | **No**: no saved dashboards or panels. Fails the task's "Dashboard URL" |

## Scores

| Criterion (weight) | Grafana | Evidence | Perses | Evidence |
|---|---|---|---|---|
| Maintenance (20) | 5 | 3,774 commits/90 d, 12,403/365 d; patches every ~2 weeks on 5 branches; Grafana Labs; Scorecard 7.4 | 3.5 | 212 commits/90 d, 626/365 d; CNCF sandbox; 115 contributors; Scorecard 5.6 |
| Velocity (10) | 5 | v13.3.1 2026-11-10, minor every 1–2 months | 4 | v0.55.1 2026-11-02, minor every 2–3 months |
| Community (10) | 5 | 77,856 stars, 14,968 forks | 3 | 2,529 stars, 268 forks |
| Architecture (15) | 4.5 | Mature (v13), file provisioning, read-only dashboards, brute-force protection | 3 | Still 0.x; sign-in off by default and its datasource proxy then reaches any URL (its own docs warn) |
| Fit (15) | 5 | Official VictoriaLogs plugin by the store's vendor (v0.34.0, Apache-2.0, signed); Prometheus built in; named in PLAN P1-T9 | 3.5 | VictoriaLogs plugin 0.5.x and a beta stream, no feature docs |
| Deployment (10) | 4 | Non-root, read-only with a data volume, plugin baked in by checksum; 506 MB image | 4 | Distroless, non-root; read-only needs a tmpfs for plugins |
| Resources (10) | 2.5 | ~516 MiB idle | 3.5 | ~327 MiB idle |
| Cost (5) | 5 | Free, self-hosted | 5 | Free, self-hosted |
| India (5) | 5 | Self-hosted on the server | 5 | Self-hosted on the server |
| **Weighted total** | **457.5 / 500 (91.5 %)** | | **362.5 / 500 (72.5 %)** | |

## Result

- **Winner: Grafana OSS 13.3.1**, with the VictoriaLogs datasource v0.34.0.
- **Runner-up: Perses.** The gap is 19 points, so this is not a close call. It also changes no
  ADR, since PLAN P1-T9 already names Grafana.
- **What would make us switch to Perses:**
  - memory on the 8 GB server becomes the limit (Grafana's 0.5–0.8 GB);
  - Perses reaches 1.0 with a stable VictoriaLogs plugin;
  - a customer or auditor objects to an AGPL component in the operator stack.
