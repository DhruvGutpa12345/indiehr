# P1-T17 plan: a trial upstream sync and a drift check (2026-11-13)

PLAN row P1-T17 (from the Phase 1 review, R1-18): try a sync to current CARE on a throwaway
branch, following the conflict runbook and recording the hours, and add a scheduled drift check.

## Where CARE stands (dry run, 2026-11-13)

| App | Pinned | Upstream `develop` | Commits | Files | Our patched files also touched upstream |
|---|---|---|---|---|---|
| backend | `67a46185eb98` (2026-11-01) | `782d511a38fc` | 10 | 79 (+8,285 / −477) | `care/emr/utils/batch_requests.py`, `care/emr/tests/test_reset_password_api.py`, `care/emr/reports/renderer/template_engine.py` |
| frontend | `57205b897804` (2026-11-02) | `c9519b7f3f50` | 7 | 14 (+394 / −145) | `PinPageDialog.tsx`, `public/locale/en.json`, `AccountList.tsx`, `PatientRegistration.tsx` |

## The trial

1. **Where:** a separate git worktree, outside the project folder, on a branch of its own
   (`p1-t17-sync-trial`, created for this) from `main`. `main` is never touched by the trial.
2. **Run** `scripts/sync-upstream.sh backend --task P1-T17`, then `frontend`, exactly as the
   runbook says. Resolve each conflict by the runbook (docs/UPSTREAM.md, "Conflict runbook"):
   - keep upstream's change and re-apply our patch as UPSTREAM_PATCHES.md describes;
   - drop a patch that upstream made unnecessary;
   - lock files are re-done the documented way.
   Each step is timed.
3. **Test the trial:**
   - the plugin suites and the isolation suite in tenant mode;
   - the reference-pack drift test, which P1-T15a built for exactly this: an upstream change to a
     system value set must fail it until a new pack version is built;
   - then CI, through a manual run on the branch: the full CARE suite in tenant mode, every
     mutation, and the frontend build.
4. **Record** the hours per step, every conflict and how it was resolved, what the runbook got
   wrong or left out (fixed in the runbook), and whether the branch is ready to merge.
5. **The trial is not merged into `main` in this run.** The PLAN row asks for a trial, and a sync
   brings in about 8,300 lines of upstream change. Merging it is the next sync's decision, built
   on this branch (Q-K28).

## The drift check

- A weekly CI job runs `scripts/sync-upstream.sh all --dry-run` and writes the result (commits
  behind, files, our patched files touched upstream) to the run's summary.
- It warns when a pin is more than 30 days old and fails when it is more than 60 days old
  (PLAN §8 promises monthly syncs).
