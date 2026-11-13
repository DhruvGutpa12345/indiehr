# P1-T17 trial upstream sync: results (2026-11-13)

The branch is `p1-t17-sync-trial`, made from `main` at `024e061cb` in a separate worktree. `main`
was never touched by the trial.

## Outcome

| What | Result |
|---|---|
| Backend | `67a46185eb98` → `782d511a38fc`: 10 commits, 79 files (+8,285 / −477), **no conflict** |
| Frontend | `57205b897804` → `c9519b7f3f50`: 7 commits, 14 files (+394 / −145), **no conflict** |
| Time | the merge took about 2 s per app. Checking our patches and upstream's changes took about 4 min. The tests took about 15 min locally and about 90 min in CI |
| Our patches | all intact and still right against the new code (7 patched files touched upstream, each checked by hand); none dropped |
| Lock files, value sets, models, migrations, permissions | unchanged upstream, so no lock bumps, no pack rebuild, no new audit classification |
| Local tests (tenant mode) | plugin suites 474 (45 live-Keycloak skips); isolation suite. Two environment fixes were needed first (below) |
| CI on the branch | Apps CI run 43330066412: **all 40 jobs green** after one re-run of the plain Backend job (below) |

## What the trial found

1. **Two new CARE test modules** use CARE's own sign-in routes, which we switch off on hospital
   addresses: `care.users.tests.test_auth_lock` and `care.users.tests.test_token_revocation`.
   Every test answered 404 in tenant mode. They are excluded there (they still run in the plain
   Backend job), in commit `b83aa1285` on the branch. Upstream's other new tests pass in tenant
   mode: admin TOTP, batch transactions, the user update lock.
2. **A worktree lacks two git-ignored items** the tests need:
   - the dev key `apps/backend/jwks.b64.txt`;
   - the collected `apps/backend/staticfiles/` (error pages render a static favicon, so without
     the manifest every 404 became a 500).
   With both copied in, every test passes. Added to the runbook.
3. **One flaky upstream test.** `care.emr.tests.test_organization_api` (`…as_user_with_permission`)
   failed once in the plain Backend job, which runs CARE's suite with `--parallel --shuffle`, and
   passed on re-run. Neither that test nor the code it calls changed in the sync. It is noted for
   the next sync and not excluded.
4. **For the real merge** (Q-K28):
   - pin `REPORT_ALLOWED_RESOURCE_ORIGINS` empty in deployment (new upstream setting; today it is
     empty anyway);
   - add Hindi for `file_too_large`, and re-translate `pin_page_limit_reached` (its English
     changed);
   - confirm that CARE's new user lock key (`lock:user_auth:<id>`) carries the hospital prefix
     (the test runner's Redis check passed in the trial: every key was prefixed).
5. **Runbook gaps found and fixed** (docs/UPSTREAM.md, `scripts/sync-upstream.sh`):
   - clean merges had no checklist;
   - there was no list of our plugins' assumptions;
   - changed English strings went unnoticed;
   - "lock files conflict on most syncs" was wrong;
   - the script printed duplicate paths and missed brace rows;
   - its commits had no trailer option;
   - the worktree files above were not mentioned.

## Drift check

The **Upstream drift** workflow (weekly and manual, only the pinned checkout action) reports how
far behind the pins are. It warns at 30 days and fails after 60. Tested with fixed dates.
