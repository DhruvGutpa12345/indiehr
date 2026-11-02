# IndieHR

Multi-tenant SaaS Hospital Information System for India (proprietary).

| Doc | Purpose |
|---|---|
| [docs/PLAN.md](docs/PLAN.md) | Master plan, architecture, phased tasks |
| [docs/TRACKER.md](docs/TRACKER.md) | Progress tracker |
| [docs/QUESTIONS.md](docs/QUESTIONS.md) | Decisions and open questions |
| [docs/adr/](docs/adr/) | Architecture Decision Records |
| [docs/VERSIONS.md](docs/VERSIONS.md) | Pinned tool and dependency versions |

## Layout

```
apps/      upstream forks (CARE backend + frontend) — from P0-T4
plugins/   our ihr_* plugins
deploy/    dev setup, compose files, seed data
scripts/   maintenance scripts
docs/      plan, tracker, ADRs, blueprint
reports/, research_notes/   Nov 2026 research
```

## Dev setup

1. Ubuntu 26.04: `sudo bash deploy/dev/setup-workstation-root.sh` (in Ubuntu's own terminal).
2. `pre-commit install`
3. `pre-commit run --all-files`
