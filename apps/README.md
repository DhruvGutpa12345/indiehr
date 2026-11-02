# apps/

Deployable applications. Upstream CARE, imported in P0-T4 as squashed git subtrees (ADR-006, pins in `/upstream.lock`):

- `apps/backend/` — fork of `ohcnetwork/care` (Django API)
- `apps/frontend/` — fork of `ohcnetwork/care_fe` (React web app)

Upstream code stays as untouched as possible; record every edit in `docs/UPSTREAM_PATCHES.md`. Sync with `scripts/sync-upstream.sh`.
