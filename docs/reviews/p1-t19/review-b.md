# P1-T19b independent review (2026-11-13)

A fresh reviewer pass over the built and locally proven staging move (design.md "P1-T19b as built"),
then a second agent tried to refute each finding. Of 5 findings: 0 refuted outright (S2's headline
scenario was refuted, its advice problems stand). A fixer fixed them and a verifier checked each fix
against its failure scenario; the verifier raised 3 items, a second fixer closed them, and a second
verifier confirmed all 3 closed. Two loose ends were then closed by hand.

| # | Finding | Verdict | Fix (verified) |
|---|---|---|---|
| S1 | A setting `ihr_app` stores on itself in another database (`ALTER ROLE ... IN DATABASE postgres SET ...`) survives every `up` and `db-roles --assert`, so the refusal never clears | confirmed, medium | `DB_ROLES_SQL` loops over every stored setting of the four roles in every database and fails if any is left; the checks name the databases (`_STORED_IN`); proven live on a throwaway Postgres 18.6; mutation (reset narrowed to `ihr`) fails the static test |
| C3 | The same defect seen from the checks: the reset covers two rows, the refusal counts every database | confirmed, medium | as S1 (plugin test names the database the setting sits in) |
| C2 | `db-roles --migrate` migrates whatever build is tagged, outside a release, with no restore point and no record (reachable with `deploy.sh --no-start`) | confirmed, medium | refuses unless the stack is stopped, a release is logged and the backend image is the one logged for the newest release; two mutations each fail their test |
| C1 | If the move fails before the rename, the recovery advice names commands that are refused or cannot connect (rollback, `up`, `fleet release`), and hospitals stay at 503 | confirmed, medium | every rollback refused while the database is single; the advice names only `db-roles --serve-previous` (or, with the backend running, `fleet release` then `fleet window --clear`); before the move `fleet release|hold|window` run in the running backend; first fix left a defect, see second round |
| S2 | After `db-roles --migrate` stops before the move, its recovery advice cannot work and a rerun leaves every hospital held | partly, low | the "rerun leaves every hospital held" scenario was refuted (the post-move fleet migrate lifts the holds); the advice problems stand and are fixed with C1 (the `held` stage is gone, the message says run `db-roles --migrate` again) |

## Second round (verifier)

The verifier closed S1, C3 and C2, and found C1 and S2 only partly closed, plus one new problem and two
minor ones. Three items went to a second fixer:

1. **`serve-previous` used `compose start`, which fails on real Compose.** Every app service depends
   on `migrate` (`service_completed_successfully`) and staging keeps no `migrate` container, so
   `docker compose start backend` returns "missing dependency migrate" (reproduced on Compose v5.7.1).
   The recovery would have died before it lifted the holds. The fake docker in the tests treated
   `compose start` as always succeeding, which hid it. Fixed: new `existing_containers` (`compose ps -a -q`,
   keeping only containers labelled not one-off) and `start_existing` (app services only, fail-closed,
   `docker start <ids>`, Compose never used); `db_serve_previous` starts the backend, lifts the holds,
   then starts the rest; new subcommand `scripts/staging.sh start-existing <service>...`; the old
   release advice now names it. The fake docker now resolves `compose start` dependencies the way
   Compose does, and a test ties it to the real `compose.yaml`. Proven on a throwaway Compose v5.7.1
   project with staging's dependencies (rc 0, a one-off left alone, names still resolve, the project
   removed afterwards).
2. **Recovery advice with the backend still running.** In the single-role state the advice now also
   names `fleet release` and `fleet window --clear` (they run in the running backend); not when the
   backend is down or the state is unreadable. Mutations of the block and of its condition fail.
3. **Fail-open check.** `db-roles --migrate` and `release`'s D8 check read the running app services
   with `compose ps ... || true`. New `running_app_services` has no `|| true`; if Compose errors, both
   die with "nothing was changed". Three mutations fail their tests.

Closed by hand afterwards (the second verifier's minor notes):

- `running_app_services` now uses `compose ps -a --status running`, so a running one-off
  (`compose run`) of an app service also blocks the move; proven on real Compose v5.7.1 with a
  throwaway project (the default `ps` leaves one-offs out).
- `start-existing` takes the release lock, so it cannot start old containers while a release or
  rollback runs.

Script tests after both rounds and the hand fixes: release 71 OK, db-roles 25 OK; `pre-commit run
--all-files` rc 0 after the second round.

## Proven only on fakes

- **The end-to-end serve-previous flow** (start the old backend, lift the holds, start the rest after a
  release stopped before the move) was never run against a real stack failing at that point. Its
  pieces are proven separately: `start-existing` and the `compose start` failure on real Compose
  v5.7.1; the order and the arguments by the fake-docker tests; `fleet release` / `window --clear`
  in the running backend by the main proof's holds.
- **The fail-closed `compose ps` paths** (`running_app_services`, `existing_containers`, the
  single-role advice reading a container) are proven with a fake docker whose `ps` fails
  (`FAKE_PS_FAILS`) and by mutation; real Compose was only run for its success output, never made to
  fail. Note: `compose ps` on a service missing from the compose files exits 0 with no output (real
  Compose v5.7.1), so the check fails closed only when Compose or Docker itself errors; all app
  services are in the base file today.

The 16-step local staging proof (Postgres 18.6, 3 then 4 hospitals) is in design.md "P1-T19b as built".
It ran before the review fixes; after them it was not repeated in full (the fixes were proven in the
pieces above).
