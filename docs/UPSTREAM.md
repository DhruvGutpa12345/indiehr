# Upstream code (CARE)

How CARE is brought in and kept current: [ADR-006](adr/006-upstream-fork-via-subtree.md). The exact pins are in [`upstream.lock`](../upstream.lock).

| App | Folder | Upstream | Pinned commit | Commit date |
|---|---|---|---|---|
| Backend (Django API) | `apps/backend` | ohcnetwork/care `develop` | `67a46185eb98` | 2026-11-01 |
| Frontend (React) | `apps/frontend` | ohcnetwork/care_fe `develop` | `57205b897804` | 2026-11-02 |

## Check for upstream changes (changes nothing)

```bash
make sync-upstream-check
```

## Pull upstream changes

```bash
make sync-upstream
```

Merges the latest `develop` into both apps, keeping our edits, then updates `upstream.lock` and commits. Update the table above by hand. Run the full test suite before pushing.

For one app or a specific commit:

```bash
scripts/sync-upstream.sh backend <sha>
```

Every edit we make inside `apps/` must be listed in [UPSTREAM_PATCHES.md](UPSTREAM_PATCHES.md).
