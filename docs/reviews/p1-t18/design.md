# P1-T18 design: release and rollback (draft for attack, 2026-11-13)

PLAN P1-T18 (Phase 1 review R1-07, High; TENANCY_DESIGN §7.2; ADR-002 Amendment 1 #6).
Research with file:line evidence: the run's scratchpad `t18/flow.md` and `t18/fleet.md`.

## The problem today

1. **Migrations run under live traffic.** `staging.sh up` runs the migrate one-shot while the
   **old** backend, workers and beat keep serving every hospital (staging.sh:278, replaced only at
   :288–294). Old code meets schemas under ALTER, renamed columns and finished data migrations.
2. **No error isolation.** django-tenants' `migrate` is serial and stops at the first failing
   schema. Hospitals 1…k−1 are then migrated and k+1…N are not, the old code keeps serving all of
   them, and nothing records per-hospital results.
3. **Nothing to roll back to.** `deploy.sh` loads new images onto mutable `:staging` tags and then
   runs `docker image prune -f` (deploy.sh:253), which deletes the previous release. The stack
   files are overwritten in place.
4. **No release record:** the version is only an image label, `APP_VERSION` stays "unknown", and
   there is no history.

## Split (by Claude, 2026-11-13, awaiting owner confirmation: Q-K29)

- **P1-T18a: the gate and the fleet runner** (backend code, tests).
- **P1-T18b: the release itself.**
  - images per release with the previous one kept;
  - `staging.sh rollback`;
  - the announced window;
  - a backup point before migrating;
  - the runbook;
  - a drill in the local staging proof.

## P1-T18a

1. **Fingerprints** (`ihr_platform/release.py`).
   - **Code:** the sha256 of the sorted leaf migrations of the tenant apps, from Django's
     `MigrationLoader`. It is computed once per process, lazily; its cost per gunicorn worker is
     measured.
   - **Schema:** recorded on the hospital (`Hospital.schema_fingerprint`, `schema_checked_at`),
     only after a check that the migration plan for that schema is empty *and* that
     `django_migrations` holds no row unknown to this build (a newer schema counts as a
     mismatch).
   - Recorded from django-tenants' `schema_migrated` signal (sent on success only) and by the
     fleet runner. That covers provisioning, `ihr_create_tenant`, the test runner and a manual
     `migrate_schemas`.
2. **The gate** (`ihr_platform/release_gate.py`, right after `TenantMainMiddleware`, before
   anything that writes to the hospital schema):
   - a hospital whose `upgrading_since` is set, or whose `schema_fingerprint` differs from the
     code's, answers **503** `{"detail": "upgrading"}` with `Retry-After` and `no-store`;
   - platform hosts (public schema: health, console, back-channel logout) are never gated.
   - **Defence in depth** (ADR-002 #7): the same check sits next to the status checks in staff and
     service authentication. Beat's `ActiveTenantScheduler` skips gated hospitals. Workers are
     stopped during the window (step 4).
   - **Old code on a newer schema** (a rollback without a restore) also gets 503, which is
     correct: CARE only migrates forward.
3. **`ihr_fleet_run`** (management command).
   - **Steps:** `migrate`, `roles` (the guarded `--check`, then the sync), `packs`, `check`.
   - **Options:** `--status active,suspended` (the default; never public, provisioning,
     offboarding or archived), `--slug`, `--parallel N` (default 2, the P1-T1c bench), `--check`,
     `--resume`.
   - **Isolation:** one subprocess per hospital
     (`manage.py migrate_schemas --tenant --schema=<s>`). One hospital's failure never stops the
     others.
   - **Locking:** the per-hospital advisory lock that provisioning also takes. A busy hospital
     is retried, then reported as deferred.
   - **Mark, then migrate:** a hospital's `upgrading_since` is set *before* its migration starts,
     so the old build's gate answers 503 for it while it migrates. This protects from the second
     gated release on; the release that introduces the gate needs a full stop (step 4).
   - **Records:** one `TenantOperation` per hospital, of a new kind `upgrade`, carrying the steps,
     the fingerprints before and after, seconds and a safe error. Each run is grouped by a
     `FleetRun` row (id, build fingerprint, git sha, started, finished, counts). The console
     filters by kind.
   - **A failed hospital stays gated (503) while the others go live.** It is recorded, alerted
     (fixed log line `ihr-fleet: hospital=<slug> outcome=failed`, as for packs) and resumed with
     `--resume` after a fix. Only fleet-wide faults (public migrations, the code fingerprint)
     stop the start.
4. **The one-shot in compose** becomes:
   1. `migrate_schemas --shared`;
   2. `ihr_create_tenant --public`;
   3. `ihr_fleet_run migrate roles packs check` (`--startup` semantics, like packs);
   4. then the start.
   - `staging.sh up` stops celery-beat, celery-worker, provisioner and staff-worker **before** the
     one-shot, not after, so no worker writes into a migrating schema.
   - The first deploy of P1-T18 records every hospital's fingerprint (`check --record`) before
     the gate is on.
5. **Tests:**
   - the fingerprint, and a schema behind or ahead;
   - the gate (503, platform hosts open, auth and beat refuse);
   - the fleet runner: parallel; one hospital failing while the rest succeed; resume; the lock;
     the status filter; records; the log line;
   - the signal path;
   - an isolation-suite check that a gated hospital shows nothing to anyone.

## P1-T18b

1. **Images per release** (`deploy.sh`).
   - Every image gets a release tag `:<git-sha12>` as well as `:staging`.
   - Before loading a new one, the server re-tags the current one as `:previous`.
   - Pruning keeps every image tagged `:previous` or with a release tag listed in `RELEASES`.
   - `DEPLOYED` becomes a history (`releases.log`: sha, time, image ids, fingerprint).
   - `APP_VERSION` is passed to the build.
   - The stack files of the previous release are kept beside the current ones.
2. **The window** (Q-T1: about 02:00 IST, announced).
   - `staging.sh release` = announce, take a backup point, stop the workers, run the one-shot,
     start, smoke.
   - The announcement is a public `MaintenanceWindow` row (start, minutes, note) shown in
     `/api/ihr/bootstrap/`, ready for the web app's banner (P1-T24). E-mailing hospital admins is
     sending on your behalf, so it is not built (yours).
   - The time budget is computed from the last release's per-hospital seconds.
3. **Backup point:** `backup-now` before migrating, with its time recorded in the release log as
   the restore target.
4. **Rollback** (`staging.sh rollback`):
   - **Release had no migrations** (the code fingerprints are equal): redeploy the `:previous`
     images and stack files; nothing is lost.
   - **Release had migrations:** an older image would be gated everywhere (correct). Rollback is
     then a point-in-time restore to the recorded backup point plus the previous images. It loses
     what hospitals wrote after the window, so it needs an explicit `--restore` and
     confirmation, and the runbook says so.
5. **Runbook** (`deploy/staging/README.md`): release, window, failed hospital, resume, both
   rollbacks, and what each costs.
6. **Drill:** in the local staging proof VM (`deploy.sh --local-proof`), with dev stack stopped:
   1. release N;
   2. release N+1 with a migration;
   3. one hospital forced to fail: it stays 503, the rest work;
   4. resume;
   5. a rollback without migrations;
   6. a rollback with a restore.
   Measure each step's time.

## Decisions (auto-accepted, awaiting the owner)

- **Q-K29:**
  - the split;
  - a failed hospital stays gated while the others go live;
  - parallelism 2;
  - the first P1-T18 release needs a full stop;
  - a rollback across migrations means a restore that loses post-window writes, and only with
    an explicit flag;
  - no e-mail announcement (yours to decide).

## After the attack (2026-11-13): the design as it is built

Two Opus reviewers (correctness and feasibility; operations, security and tenancy) raised 24
findings, and a third agent tried to refute each one. Result: 0 refuted, 6 confirmed, 18 partly,
none fatal. Where this section and the sections above disagree, this section wins.

### P1-T18a (the gate and the fleet runner)

1. **What "known" means (C1).**
   - The schema check counts only tenant-app labels.
   - A row is known if its migration is on disk, or named in any loaded migration's `replaces`
     (CARE's squashed migrations leave about 450 such rows).
   - A committed `retired_migrations.txt` lists names upstream may later delete; the
     after-sync checklist updates it.
   - Tests:
     - a fresh tenant gets a fingerprint and is not gated;
     - a synthetic future tenant-app row reads as ahead;
     - a shared-only row is ignored.
2. **The release state lives in its own row (C2).** `HospitalSchemaState` (public, one row per
   hospital: `schema_fingerprint`, `checked_at`, `upgrading_since`) is written only with
   `update()` or get-or-create. So a full `Hospital.save()` can never wipe it. The gate reads it
   with one indexed query per hospital request.
3. **`servable(hospital)` (C8, O3):** ACTIVE, not held, fingerprint equal to the code's. Used
   by:
   - the gate;
   - staff and service authentication;
   - beat's scheduler;
   - the staff-worker's invitations and requests (deferred, never failed for good);
   - provisioning paths that enter an existing hospital.
   Back-channel logout and the read-only console stay open.
4. **The gate's answers (C12, O8):**
   - `/api/…` gets a 503 JSON `{"detail": "upgrading"}`;
   - an HTML page gets a short 503 page;
   - `/api/ihr/bootstrap.js` gets a **200 stub** built only from public data (the hospital's
     name and the `MaintenanceWindow`). It shows an "upgrading" overlay in English and Hindi
     (the Hindi goes on a review sheet, 🟡) and never touches the hospital's schema.
   - Platform hosts are never gated.
5. **What the fleet migrates (C3, O6).**
   - `migrate` takes every hospital whose schema exists: provisioning, active, suspended,
     offboarding. Archived and schema-less hospitals are left out.
   - `roles` and `packs` take active and suspended.
   - A provisioning run checks the fingerprint before each step after `schema`, and re-runs the
     schema step if it does not match. So a run resumed across a release never goes live on an
     old schema.
6. **Roles across the fleet (C9):** one read-only `--check` pass over every selected hospital,
   then the sync for the hospitals that passed. Refusing hospitals stay gated and are reported.
7. **Parallelism and limits (C10, C7, O9).**
   - A fork pool (`max_tasks_per_child=1`, connections closed before forking) runs each
     hospital's migration in-process. Each task catches its own failure. A subprocess is the
     fallback for a child that crashes.
   - Each hospital's migration runs with `lock_timeout=10s`, retried with back-off on lock
     errors, and has a wall-clock limit. A hospital that cannot finish stays held, as deferred.
   - Per-hospital seconds are recorded, and the next window's budget is computed from them.
8. **Error records (O10):** class, SQLSTATE, constraint, table, column and the migration being
   applied, never a message that could hold row values. The same helper serves provisioning's
   records.
9. **`up` never migrates hospitals (O2).**
   - The default one-shot does `migrate_schemas --shared`, `ihr_create_tenant --public`, roles,
     packs and `ihr_fleet_run check`.
   - If any hospital's schema fingerprint differs from the code's, `up` refuses to start and
     points to `staging.sh release`.
   - Tenant migrations run only inside `staging.sh release` (P1-T18b), the announced window.
10. **Tests (C11):**
    - the per-hospital step can be injected (as the pack tests do), so parallelism, filters,
      resume, records and the log line are tested in-process;
    - one failing hospital is a schema-less row or a fake step;
    - one real subprocess or fork test proves the pool;
    - an isolation-suite check proves that a gated hospital shows nothing.

### P1-T18b (the release, rollback, runbook, drill)

11. **The window (C6, O4, C7, O9):** `staging.sh release` does, in order:
    1. announce;
    2. **hold every hospital in one transaction** (`ihr_fleet_run hold --all`);
    3. stop the workers;
    4. drain (wait until no backend transaction older than the hold remains, capped);
    5. **`pg_create_restore_point('release-<sha12>')`**, and record its name and LSN in the
       release log;
    6. run the shared migrations, then the fleet migration;
    7. start, then smoke every hospital.
    The public schema therefore migrates with every hospital held.
12. **The window stays open while any hospital is not servable (O1).** `staging.sh release`
    exits non-zero and keeps the banner up. A held hospital is reported every minute by a fixed
    log line, and an alert fires when it has been held longer than the window's budget.
    `ihr_fleet_run --resume` continues.
13. **Releases are a log written only after success (C4, O11).**
    - Every image is tagged `:<sha12>` (checked against its ID).
    - `releases.log` gets a line (sha, time, every image's ID, both fingerprints, the restore
      point) only after smoke passes.
    - `rollback` takes the newest logged release whose sha differs from the current one.
    - The last 3 logged releases' images are kept; pruning never touches them.
    - Free disk is checked before loading new images.
14. **Rollback without a restore** only when **both** the tenant and public fingerprints are
    equal **and** the Keycloak and Postgres images are the same (C5, O7). Otherwise it is refused
    with the reason.
15. **Rollback with a restore (O5):** `rollback --restore` asks for confirmation, then:
    1. stops the stack;
    2. moves the Postgres volume aside, if disk allows (so the rollback can itself be undone),
       otherwise removes only that volume. Never `down -v`, which also deletes the log store,
       keys and WAL spool;
    3. restores to the recorded restore point (`--type=name`, added to `restore`);
    4. starts the previous release.
    The runbook states what is lost: writes after the restore point, which are none if the
    rollback happens inside the window.
16. **Stack files (O12):** the step-3 tar stream itself (the tracked files only) is kept as
    `stack-<sha12>.tgz` (mode 600). It never contains `.env`, `secrets/`, `generated/` or keys.
17. **Runbook and drill** as above. The drill runs in the local staging proof VM, with the dev
    stack stopped, and each step is timed.

## After the review of P1-T18a (2026-11-13)

An independent review raised 20 findings on the built P1-T18a; a challenger confirmed 11 and
partly confirmed 9 (none refuted). All 20 were fixed, each with a regression test
(`ihr_platform/tests/test_release.py` unless named). What changed in the design above:

- **Fleet-wide faults first (C1, S5).** The public schema and the packs are checked before any
  hospital is held or migrated. A pack fault raised later still releases the hospitals whose
  own steps finished.
- **A known failure does not stop the start (C2).** A live hospital this build's migrate run
  already tried and left held stays at 503 and is reported; the others start. A hospital
  behind with no such run (including one only held by hand) still stops `up` (item 9).
- **Interrupted runs (C3, C8).** Each operation is written with outcome "running"; Ctrl-C and
  SIGTERM unwind the run and close it; `--resume` of a run left "running" closes it as
  interrupted and takes up its hospitals. One fleet run at a time (a fleet-wide advisory lock).
- **Holds carry a reason (C4, S2):** `window`, `migrate` or `roles`
  (`HospitalSchemaState.hold_reason`, migration 0010). A run whose roles pass releases a
  `roles` hold, never a window's. A live hospital left unserved is reported held, never done.
- **The migration's name comes from Django (C5):** `MigrationExecutor.apply_migration` is
  wrapped in the child; django-tenants' per-line message is never sent without a terminal.
- **The limit ends the database work too (C6, S9).** The child reports its session; on the
  limit or a crash the parent ends that session (`pg_terminate_backend`) before recording or
  falling back. Sessions check for a vanished client every 5 s. `--parallel 1` forks too.
- **No false alerts (C7, S8):** a provisioning or offboarding hospital not served by design is
  never named on the alert line.
- **Subprocess fallback (C9):** it keeps the process's own `DATABASE_URL` (TLS options); finds
  `manage.py` from `BASE_DIR`.
- **Celery (S1):** every queued task checks, after its schema switch, that its hospital is
  served; if not, it is sent again a minute later unchanged and never run there
  (`celery_gate.py`, on tenant_schemas_celery's task class from our app config; no change in
  `apps/`).
- **The alert is reached and stays up (S3):** operator runs go through `scripts/staging.sh
  fleet` (the logged `migrate` container, never the unlogged admin one), and the worker writes
  the held line every minute for each live hospital still not served (`ihr_platform.tasks`,
  read from `celery-worker`), so HospitalNotServed does not resolve while the hospital is down.
  The per-window budget alert of item 12 stays with P1-T18b.
- **`check --record` never writes "" (S4):** only a current schema's fingerprint is recorded,
  so a refused start never takes down a hospital the previous build still serves.
- **Operator commands (S6):** `ihr_pack_apply`, `ihr_load_geography`, `ihr_hospital_profile`
  and `ihr_link_staff` refuse a held or older schema (`release.require_ready`); `--all` leaves
  such hospitals out and names them.
- **Error records (S7):** only our own value-free exceptions (provisioning, fleet, the guarded
  role sync's `RoleSyncRefused`) keep their first line; any other keeps its class and file:line.
- **Console (S10):** each hospital's hold or schema state, `?kind=upgrade`, the fleet runs;
  a held hospital's schema is never read for its first admin.
