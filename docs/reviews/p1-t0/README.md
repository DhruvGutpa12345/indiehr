# P1-T0 evidence (tenancy design workflow, 2026-11-05/06)

Raw outputs of the 17-agent workflow behind [TENANCY_DESIGN.md](../../TENANCY_DESIGN.md). Agents were read-only over `apps/backend` and `apps/frontend` at the commits in `upstream.lock`.

| File | Contents |
|---|---|
| model-map.json | 100 CARE models: proposed scope, outgoing FKs, uniqueness/cache notes; 47 model-level risks |
| runtime-frontend-research.json | Settings, caches, Celery, storage, commands (28); frontend (9); library and ABDM/Keycloak research (15) |
| designs-and-judging.json | 3 independent designs, 3 judge score sheets, grafts |
| verdicts.json | 5 adversarial reviews (leakage, CARE compatibility, operations, identity & ABDM, upstream burden) |
