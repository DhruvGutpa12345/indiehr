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
scripts/sync-upstream.sh all --task P2-T4     # or: make sync-upstream TASK=P2-T4
```

Merges the latest `develop` into both apps, keeping our edits, then updates `upstream.lock` and commits. Update the table above by hand. Run the full test suite before pushing.

For one app or a specific commit:

```bash
scripts/sync-upstream.sh backend <full-40-char-sha> --task P2-T4
```

Every edit we make inside `apps/` must be listed in [UPSTREAM_PATCHES.md](UPSTREAM_PATCHES.md).

## After a frontend sync: translations (P1-T7f)

New upstream strings arrive in English only. After merging, list the strings the app now
uses that have no Hindi (`apps/frontend/public/locale/en.json` minus upstream `hi.json`,
`deploy/locale/hi.json` and `docs/i18n/unused-strings.csv`, checked against the code as in
P1-T7f), translate them with the glossary (`docs/i18n/hi-glossary.md`) and add a review
sheet. A string listed as unused that the new code now shows moves out of that list.

## After every sync, even a clean merge (P1-T17)

A clean merge can still change what one of our patches means. In the P1-T17 trial,
upstream changed a hook's signature next to our patch and git merged it without a word.
After each sync, before pushing:

1. **Patched files.** For each file the dry run flagged ("upstream also changed our patched
   file"), check that our patch is still there and still right against the new code. Update its
   UPSTREAM_PATCHES.md row.
2. **Lock files.** Did upstream change `Pipfile`, `Pipfile.lock`, `package.json`,
   `package-lock.json` or `.node-version`? If it did, compare versions with our security bumps
   even without a conflict.
3. **Our plugins' assumptions.** Look through upstream's diff for:
   - new CARE test modules that use routes we block on hospital addresses → add them to
     `plugins/ihr_platform/ihr_platform/care_tests_excluded.txt`, with the reason;
   - new `emr` models → classify them in `ihr_audit/unlinked_models.txt`, or check they have a
     patient link;
   - new routes → `ihr_identity/blocked.py`, the isolation crawl's `crawl_skips.txt` or
     `anonymous_allowlist.txt` (the suite fails until they are reviewed);
   - changed system value sets → rebuild the pack: `scripts/pack-build.sh care-valuesets` (the
     drift test fails until you do);
   - new raw cache, lock or Redis keys → TENANCY_DESIGN §6 (the test runner fails on any key
     without the hospital prefix);
   - new settings → `ihr_platform/settings/secure.py` if deployment must pin them;
   - INSTALLED_APPS changes → `ihr_platform/settings/tenancy.py`;
   - permission or role changes → the guarded role sync and `ihr_roles`;
   - new Playwright specs → check them against our India date formats.
4. **Translations.** Diff the English *values* in `apps/frontend/public/locale/en.json`, not just
   the keys. A reworded English string leaves our Hindi out of date, and
   `merge-locale.py --check` does not notice.
5. **Tests in tenant mode** before pushing: the plugin suites and the isolation suite
   (`scripts/care-tenancy.sh test …`), then Apps CI.

**To rehearse a sync without touching `main`:** use a worktree on a branch of its own:

```bash
git worktree add -b sync-trial ~/indiehr-sync-trial origin/main
```

Run the script there. The `upstream-*` remotes it adds live in the shared `.git` config.
`IHR_COMMIT_TRAILER` adds a trailer, such as an attribution line, to the script's own commits.
The weekly **Upstream drift** workflow reports how far behind we are: it warns at 30 days and
fails after 60.

## Conflict runbook

A sync stops with "MERGE CONFLICT" when upstream changed lines we also changed. `upstream.lock` is left untouched until the merge is finished.

1. `git status` lists the conflicted files.
2. **Lock files** (`apps/backend/Pipfile`, `Pipfile.lock`, `apps/frontend/package-lock.json`) conflict whenever upstream changes them, because we carry security bumps (UPSTREAM_PATCHES.md). The P1-T17 trial touched none. When they do conflict:
   - take **upstream's** version: `git checkout --theirs <file>`;
   - backend: re-apply any of our versions that are still newer than upstream's, using the same container method as P0-T9/P1-T12 (`pipenv upgrade "pkg==x.y.z"` inside `python:3.13-slim-bookworm` with pipenv 2025.1.1);
   - frontend: re-apply only the changed entries (transplant method from P0-T9/P1-T12);
   - if upstream's versions are already ≥ ours, drop those rows from UPSTREAM_PATCHES.md.
3. **Code files:** keep upstream's change and re-apply our patch as described in UPSTREAM_PATCHES.md; if upstream fixed the same thing, drop our patch and its row.
4. `git add` the files, then `git commit` with the task ID, and set the new SHA in `upstream.lock` (`<app>.sha` and `<app>.date`) in a second commit.
5. Run `scripts/sync-upstream.sh all --dry-run` (should say up to date), push, and confirm **Apps CI** is fully green.
6. To give up instead: `git merge --abort`.
