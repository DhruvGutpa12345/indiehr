# ADR-006: Bring in CARE with squashed git subtrees and pinned commits

- **Status:** Accepted
- **Date:** 2026-11-03
- **Task:** P0-T4

## Context

- ADR-001 chose to fork CARE. On GitHub, a fork of a public repository **must stay public**. That would expose our proprietary changes (ADR-000).
- Upstream state on 2026-11-03:
  - **No current releases.** The last GitHub Releases are backend v3.0.0 (2025-02-12) and frontend v3.0.32 (2025-12-23). The `v25.x` tags are older than both.
  - **Active development** happens on the `develop` branch of both repos.
  - **CI status:** upstream's latest `develop` commits pass their test checks. The only failures are bot "comment" jobs.

## Decision

1. **Copy method:** CARE lives inside our private monorepo as **squashed git subtrees**:
   - `apps/backend` ← `https://github.com/ohcnetwork/care` (branch `develop`)
   - `apps/frontend` ← `https://github.com/ohcnetwork/care_fe` (branch `develop`)

   Squashing keeps upstream history out of our repo. Only one snapshot commit is added per import or update.
2. **Pins:** the exact upstream commit for each app is recorded in [`upstream.lock`](../../upstream.lock), which is machine-readable, and explained in [UPSTREAM.md](../UPSTREAM.md).
3. **Updates** go through `scripts/sync-upstream.sh`, or `make sync-upstream`:
   - `--dry-run` shows how many commits and files changed upstream, and which of our patched files upstream also touched (conflict risk). It changes nothing.
   - A real run does a `git subtree pull --squash` (a 3-way merge that keeps our edits), updates `upstream.lock`, and commits.
   - **Cadence:** monthly, or when a needed fix lands upstream. Our full test suite must pass before we push.
4. **Our edits:**
   - We prefer plugins (`plugins/ihr_*`) over editing upstream files.
   - Every unavoidable edit inside `apps/` is listed in [UPSTREAM_PATCHES.md](../UPSTREAM_PATCHES.md).
5. **Repo checks:** our pre-commit hooks **exclude** `apps/backend/` and `apps/frontend/`, so upstream files are never reformatted (that would create needless diffs). Upstream's own linters run on that code in later tasks.
6. **Licence:** upstream `LICENSE` files (MIT) stay in place. `NOTICE` points to them.

## Alternatives rejected

- **GitHub fork:** forced public.
- **git submodule:** cannot hold our patches privately without a private mirror, and is awkward for developers.
- **Plain copy with no subtree:** loses the 3-way merge when syncing.

## Consequences

- Syncing is one command, with conflicts shown in normal git form.
- Our repo grows by the size of each upstream snapshot only, not its full history.
