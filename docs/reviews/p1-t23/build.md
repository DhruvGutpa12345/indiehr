# P1-T23 build: Postgres operating rules, back-channel service, load-test groundwork

Builder: Claude (Sonnet, high), 2026-11-17, unattended run, from `design.md` (same folder).
Decisions Q-K92 to Q-K98 are in `docs/QUESTIONS.md` (auto-accepted by Claude, awaiting owner confirmation).
**The load test on an 8 GB target is blocked on the owner (no paid server). Nothing here is an 8 GB result.**
Nothing is committed.

## 1. Files changed

| Area | Files |
|---|---|
| Limits table and wiring | `plugins/ihr_platform/ihr_platform/db_limits.py` (new), `settings/tenancy.py`, `settings/watch.py`, `settings/secure.py` |
| Drift checks | `ihr_platform/db_roles.py`, `scripts/staging.sh` (`db_server_check`, `to_kb`, `PG_SERVER_SETTINGS`) |
| Server settings | `deploy/postgres/server-settings.txt` (new), `deploy/staging/compose.yaml`, `deploy/dev/care-local.override.yaml`, `deploy/ci/tenancy.override.yaml` |
| Back-channel service | `ihr_platform/urls_backchannel.py` and `urls_none.py` (new), `settings/tenancy.py`, `settings/deployment.py`, `settings/secure.py`, `deploy/staging/compose.yaml`, `scripts/staging.sh`, `db_roles.py` (`PROCESSES`) |
| Audit export | `plugins/ihr_platform/ihr_audit/export.py` |
| Alerts | `deploy/logs/alerts/db-roles.yml`, `deploy/logs/tests/alert-cases.json`, `scripts/check-logs.py`, `deploy/grafana/dashboards/ihr-server.json` (regenerated: the alert count) |
| Tests | `ihr_platform/tests/test_db_limits.py` (new), `ihr_identity/tests/test_backchannel.py`, `ihr_platform/tests/test_secure_settings.py`, `ihr_audit/tests/test_watch.py`, `scripts/test_staging_postgres.py` (new), `scripts/test_load_test.py` (new), `scripts/test_staging_db_roles.py`, `scripts/test_staging_logs.py` |
| Measurement tools | `scripts/pg-conn-memory.py` (new), `scripts/load-test.py` (new) |
| Docs | `docs/QUESTIONS.md` (Q-K92..Q-K98), `deploy/staging/README.md` ("Postgres operating rules and the back-channel service"), `CLAUDE.md` (one bullet) |

## 2. How each design item is met

| Design | Where | Notes |
|---|---|---|
| §1b process profiles, one table, startup options | `db_limits.py:54` (`PROFILES`), `:71` (`derive`), `:131` (`apply_to_database_options`); wired at `settings/tenancy.py:205-214` | `IHR_DB_LIMITS` chooses; else derived from the process name. A settings module's own `options` is never merged (the watch, `settings/watch.py`). `ALTER ROLE ... SET` is not used (S9 kept). |
| Compose sets each service's profile | `deploy/staging/compose.yaml:61` (web default), `:253`/`:285`/`:457` (owner), `:429`/`:472` (worker) | `scripts/test_staging_postgres.py::ProcessProfiles` checks every app service; an owner profile only where the owner connects. |
| `IHR_DB_LIMITS=off` refused on a server | `settings/secure.py:229` (`db_limits_problems`), watch must be `watch` | `test_secure_settings`, `test_watch`. |
| Export snapshot idle 10 min | `ihr_audit/export.py:70` (`IDLE`), `:432` | Test shows `10min` inside, `1min` outside, and fails without the line. |
| Drift: process role check reads the limits back | `db_roles.py:1428` (`limits_problems`), first connection `:1635` (before any role check), every later one `:1444`, reason `limits` at `:1298` | The alert regex already carries any `[a-z_]{3,24}` reason. |
| Drift: database-level stored setting | `db_roles.py:974` (in `role_problems`) | Startup options win over `ALTER DATABASE ... SET`, so it would hide behind them. |
| Drift: server settings, `/dev/shm`, `shared_buffers` | `scripts/staging.sh:529` `db_server_check`; called by `db-roles --assert` (`:797`), `up` (`:886`) and `release` | Prints every difference then fails. Run against a stubbed database in `test_staging_postgres.py::ServerCheck`. |
| Server lines in one file, every container | `deploy/postgres/server-settings.txt`; staging `compose.yaml:154-156`; dev override `:66`; CI override `:15-18`; `staging.sh:962` builds `PG_SERVER_SETTINGS` | **Extra finding:** both recovery starts in `staging.sh` (the restore-drill copy `:1280`, the real restore) carried `max_connections=100` only; a recovery with a smaller `max_locks_per_transaction` than the original's refuses to replay WAL, so both now take the file's lines. |
| `shm_size` | `compose.yaml:195` (`${IHR_PG_SHM:-1g}`); dev `:68` (256m) | Below the db `mem_limit` (static test, also with the 8 GB values). |
| `shared_buffers` / `mem_limit` | defaults stay 512MB / 1536m | Q-K94 A: the 8 GB values (`IHR_PG_SHARED_BUFFERS=768MB`, `IHR_PG_MEMORY=2304m`) are for `.env` after the test. The design's §1a table lists 2304m as the value; its own Q-K94 keeps 1536m until the test. I followed Q-K94. |
| Back-channel pool: own service | `compose.yaml:401` (`backchannel`: gunicorn 1 worker x 2 threads, `networks: [internal]`, 256m, no volumes, no key, no route), `urls_backchannel.py` (logout path, `/ping/`, `/health/`), `urls_none.py` (a hospital address serves nothing), `IHR_SERVICE` at `tenancy.py:73` | `staging.sh`: `DB_APP_SERVICES`, `up` starts it with the backend (`:916`, before the Keycloak sync), `restore_restart` (`:4650`) restarts it. Keycloak URL `http://backchannel:9000/...`; `backchannel` added to `--domain`s and allowed hosts. |
| Tests that a hospital host or any other path gets 404 | `test_backchannel.py::test_the_service_answers_the_logout_path_and_two_probes_only` | Also run live in a throwaway gunicorn (below). |
| Alerts | `deploy/logs/alerts/db-roles.yml:74` `DatabaseStatementTimeouts` (more than 5 in 10 min, db container lines only); `backchannel` added to the refusal and gated service lists | `PostgresSettingsDrift` (metric) **not built**: the pinned postgres-exporter 0.20.1 has no settings collector (checked its collector list), so the up/release/assert check carries it. |
| README runbook, QUESTIONS, CLAUDE.md | done | One CLAUDE.md bullet. |

**Owner profile and idle gaps (design: restore engine over 30 min idle).** I read `restore/engine.py` (every `transaction.atomic()` block: 538, 813, 900, 963, 1177, 1583, 1640, 1871, 1956). The S3 version copy (`files.restore`), Keycloak steps and `pg_restore` run outside them, and the load transaction took 1.7 s in the P1-T22b proof. No gap near 30 min, so no `SET LOCAL idle_in_transaction_session_timeout = 0` was added. Not measured on a large hospital.

## 3. Tests and real results

| Run | Result |
|---|---|
| App-role mode (`IHR_DB_ROLES=test`), the labels `ihr_platform ihr_identity ihr_audit ihr_access ihr_refdata ihr_roles ihr_refdata_registry ihr_terminology ihr_tenant_settings` | **Ran 1201 OK (45 skipped)**, 1265 s. That run started before the back-channel service and export tests were added; those were run afterwards: `test_backchannel` + `test_db_limits` + `test_secure_settings` **36 OK** and (after the `urls_none` fix) `test_backchannel` + `test_db_limits` **24 OK** |
| Off mode, same labels, with every new test | **Ran 1168 OK (49 skipped)**, 1095 s |
| CARE's full suite in tenant mode (`.`, off mode as CI runs it), with the `test` profile (30 s / 10 s / 60 s) | **Ran 1929: 1919 OK, 10 not OK, all storage tests** (file uploads and profile pictures: `Failed to resolve 'minio'` / `Failed to upload file to storage`; this machine has no MinIO, CI does). The concurrency and lock tests of CARE's inventory pass under the 30 s / 10 s limits. (In app-role mode `.` also fails the `TransactionTestCase` flushes with `permission denied`, a limit of that mode that CI does not use for CARE's suite.) |
| `python3 -m unittest scripts/test_staging_*.py scripts/test_release_scripts.py scripts/test_dumps.py scripts/test_restore_quarantine.py scripts/test_load_test.py` | **Ran 500 OK (13 skipped)** at the end |
| `scripts/test_staging_postgres.py` (new) | 16 OK; `scripts/test_load_test.py` (new) 5 OK |
| `scripts/check-logs.py` (real Fluent Bit, VictoriaLogs, vmalert) | `Log pipeline OK`; `DatabaseStatementTimeouts` fires for `statement` only (6 lines of Postgres's own; 3 idle lines are under the threshold; a forged `(forged)` line and a backend access-log line do not count) |
| pre-commit (`--all-files`) | all hooks pass at the last run |
| App-role mode, the modules this task touches, last run | `test_db_roles test_db_limits test_secure_settings test_backchannel test_watch test_viewer test_release`: **Ran 228 OK** |

**Tests that fail with the rule removed (each shown):**

- The profile wired into the connection: removing the `apply_to_database_options(` call from `apply_tenancy` fails `test_apply_tenancy_puts_the_profile_into_the_connection_options` and `test_the_test_runs_own_connection_carries_the_test_profile` (the live connection then shows no limit).
- The first-connection check: removing the `limits_problems` call from `check_connection` fails `test_the_first_connection_check_refuses_first_for_it` for app, owner and watch; the later-connection test fails when the profile is unwired.
- The timeouts themselves (`LiveLimitTests`, real sessions opened with the scaled values 1 s / 0.5 s / 1.5 s by the same builder): a web session is cancelled by `pg_sleep(3)` (`QueryCanceled`), is ended when idle inside a transaction (`IdleInTransactionSessionTimeout`), gives up on a held lock (`LockNotAvailable`, advisory lock); the owner profile runs `pg_sleep(2)` to its end; a session with no options waits the sleep out (the control).
- The export: deleting the `SET LOCAL` line fails `test_the_snapshot_waits_longer_than_the_profile_and_only_for_itself` (`'1min' != '10min'`).
- The server check: replacing the lock-table comparison fails `test_each_drift_is_named_and_refused` (`FAKE_LOCKS=64`).
- Not shown by removal: the database-level stored setting (`test_the_database_level_setting_is_a_problem`, runs a real `ALTER DATABASE` and resets it), the static compose checks.

## 4. Measured numbers (this 6.7 GB machine; groundwork, never the 8 GB result)

**L1: private memory of one connection after touching k hospitals** (`scripts/pg-conn-memory.py`: sum of `pg_backend_memory_contexts.total_bytes`, 10 request tables per hospital; the real RSS is somewhat higher). Throwaway Postgres 18.6 container with the new server lines; hospitals made by `ihr_bench_scale grow` (1 real, the rest clones; 2.79 s each):

| Hospitals touched | 1 | 5 | 10 | 25 | 50 |
|---|---|---|---|---|---|
| Contexts total (MB) | 2.41 | 6.96 | 12.97 | 26.57 | 44.0 |
| Of which cache (MB) | 1.03 | 4.17 | 8.37 | 16.9 | 25.8 |
| MB per hospital | 0.33 | 0.98 | 1.09 | 0.98 | 0.84 |

(Baseline of an untouched connection 2.09 MB.) The dev database, 8 real dev hospitals: 6.75 MB at 8, 0.61 MB per hospital. About 0.8 to 1.1 MB per hospital for light use agrees with P1-T1c (about 80 MB at 100 hospitals); it supports the 2304m plan (about 19 connections that touch hospitals) but does not replace the 8 GB test. Onboarding the 50th real hospital took 76.5 s.

**L2: server settings and limits (dev database restarted with the new lines):**

| | Before | After |
|---|---|---|
| `max_locks_per_transaction` | 64 | 256 |
| `/dev/shm` | 64 MB | 256 MB |
| Shared memory in use (`shared_memory_size`, max_connections 40, shared_buffers 64MB) | 73 MB | 77 MB (+4 MB, mostly the lock table); at max_connections 100 the lock table grows with the connection count, about +10 MB |
| Bench container (max_connections 100, shared_buffers 128MB, locks 256) | | 160 MB shared memory |

Live limits in the dev backend container: a `web` session ran `pg_sleep(35)` and was cancelled at **30.0 s** (`canceling statement due to statement timeout`); an `owner` session ran the same sleep to **35.0 s**. Settings shown by the sessions: web 60000 / 10000 / 30000 ms, owner 1800000 / 0 / 0 ms.

**Back-channel service, live:** a throwaway gunicorn (`IHR_SERVICE=backchannel`, 1 worker x 2 threads, dev settings, same image) answered `/ping/` 200 and `/health/` 200 on `localhost`, a bad logout token 400, `/ops/`, `/api/v1/users/getcurrentuser/` and `/` 404, and every path on `alpha.localhost` 404 (after a fix: with an empty URL list Django's DEBUG welcome page answered 200; `urls_none.py` has a catch-all 404 now). Memory at idle **143 MiB** after its first requests (design estimate 160 to 200 MB; limit 256m).

**L3 (reduced; dev stack, not the staging proof):** the staging local proof needs the dev stack down, the production images and the web bundle, which I did not set up here. Instead `scripts/load-test.py` ran against the dev stack (Django `runserver`, no gunicorn, Keycloak, real OIDC flow with seeded users and TOTP): 2 hospitals x 10 virtual staff, one request per 3 s each (5 times the design's rate), 180 s, then 16 sign-outs at the same moment at the half. 729 requests, **0 server errors**, 20 of 20 sign-ins (p95 0.16 s). Per kind p50 / p95 / p99 (ms): bootstrap 12/14/18, current-user 24/27/46, facilities 23/38/40, patients 23/27/28, encounters 17/22/30, session 17/27/30 (a 4xx answer, such as a role that may not see a page, counts as "refused": 55 to 120 per kind, no 5xx). Sign-out storm: 16 signed out, p50 117 ms, max 207 ms, 9 of 16 reported "signed out everywhere". Postgres 105 MiB, backend 132 MiB, Keycloak 570 MiB at the end.
Caveats, all real: the dev web server is threaded without a limit, so the **self-wait of the gunicorn pool cannot occur there** and this run says nothing for or against option A; the two sessions of the same person (9 people sign in at both hospitals) probably explain the 7 of 16 that did not report "everywhere" (I did not investigate); the mix is read-only and 5 of its 6 kinds touch few rows. **Not done:** the comparison of the shared pool with the back-channel service under a sign-out storm, `docker stats` every 5 s, the audit export batch time on the 500k-entry bench hospital.

## 5. The 8 GB load test: the owner's plan (when a server exists)

Nothing here replaces it. Results go in `docs/reviews/p1-t23/load-8gb.md`.

1. Server: the target (8 GB, the staging layout), `IHR_PG_SHARED_BUFFERS=768MB` and `IHR_PG_MEMORY=2304m` in `.env`; deploy this build with `scripts/deploy.sh`; `scripts/staging.sh db-roles --assert` prints "Database server settings ok" (it names any drift). `up` (run by the release) runs the Keycloak sync, so each hospital's client posts logouts to `http://backchannel:9000/...` (review B5).
2. Hospitals: 25 synthetic ones, then 50, then 100 (the demo seeder; synthetic staff and patients only), each with staff accounts and a TOTP secret.
3. Load from another host: `scripts/load-test.py --users <csv: slug,username,password,totp_secret> --hospitals <slugs> --suffix <staging suffix> --scheme https --staff 20 --interval 15 --duration 1800 --storm 16 --out result.json` (it refuses any host outside `*.localhost` and the suffix in `deploy/staging/.env`; writes no password anywhere). 5 minutes ramp, 30 minutes hold per level. During the hold run one pack accept, one audit export and the nightly dump together, and note the hourly anchor and the per-minute seal.
4. Record every 5 s (a small loop on the server): `docker stats` per service, host free memory and swap, `pg_stat_activity` count and each backend's memory (`/proc/<pid>/smaps_rollup` in the db container), `pg_locks` count, Celery queue length, `DatabaseStatementTimeouts` and `canceling statement` lines.
5. Find the knee: raise `--staff` or lower `--interval` until p95 crosses 1 s; that rate and the memory at it give the cell cap for this server.
6. Pass (Q-K98): no OOM kill and no swap-in; p95 < 1 s and p99 < 3 s; errors < 0.5 % (none from timeouts on the request mix); every back-channel token delivered within 2 s; Celery queue drained within 60 s of each burst; the dump finishes in its window; db memory below its limit with 15 % margin. The cell cap becomes the lower of 100 and the passing level.
7. Also compare the shared pool with the service: repeat the storm with `IHR_BACKCHANNEL_LOGOUT_URL` pointing back at `backend` and the sync run; the design expects lost logout tokens with 8 sign-outs at once on 8 threads.
8. Confirm or change: `shared_buffers`, the db `mem_limit`, the 30 s / 300 s limits, and whether `transaction_timeout` is needed (Q-K96).

## 6. Known limits

- The load test, the shared-pool comparison, the export batch time on 500k entries, the per-5-second memory curve and the staging local proof (L3 as designed) were not done here.
- Statement and idle limits are accidents-only controls (any session may `SET` them); the role check catches a pooler that drops them, not a session that changes them later.
- `PostgresSettingsDrift` as a Prometheus alert was not built (no settings collector in the pinned exporter); `db_server_check` runs at `up`, `release` and `db-roles --assert`, not continuously. A server whose db container is restarted by hand with other settings is found at the next `up`.
- The back-channel service has no uptime probe of its own (the backend's health probe covers only the backend); it is not in the release's worker stop list (it behaves like the backend: gated hospitals answer 503 as before). It restarts with the other app containers at `up`.
- Existing hospitals' Keycloak clients keep posting to `backend` until `ihr_keycloak_sync --realm --all` runs once after this build (README).
- The `test` profile equals `web` (30 s / 10 s / 60 s). The project's labels passed under it in both role modes, and CARE's own suite did except the storage tests (section 3).
- `scripts/load-test.py` is reads only, one request kind per virtual user per tick, and its numbers include the load generator's own cost.

tier-fit: OK (Sonnet high was right for a specified build; the restart-order, release-list and recovery-start findings needed reading the shell, not more capability).


## Review (Opus, high) and fixes (Sonnet, high), 2026-11-17

Test commands: `python3 -m unittest scripts/test_staging_*.py scripts/test_release_scripts.py scripts/test_dumps.py scripts/test_restore_quarantine.py scripts/test_load_test.py` **504 OK (13 skipped)** (was 500; 4 new tests plus 1 changed); `scripts/check-monitoring.sh` (promtool rules and tests) OK; `scripts/check-logs.py` OK with the new `lock` case. Each fix below was reverted once and its test failed (shown); files restored afterwards. No Python plugin code changed in this round, so the Django suites were not re-run.

| Finding | Severity | Fix | Test (fails with the fix reverted) |
|---|---|---|---|
| A1 a release that changes a db setting dies mid-window | High | `release` (serving path) now runs `compose up -d --wait --no-deps db` before `db_server_check` (a no-op when nothing changed; every hospital is held and the workers stopped, so the restart costs no request), and a read-only `db_server_check --warn` runs before the announcement and the hold: it names the drift and returns 0 (the closing `up` and the window recreate it). | `test_staging_postgres.ServerCheck.test_a_release_recreates_the_database_before_the_check_and_warns_before_the_hold` fails without the recreate and without the preflight; `test_warn_mode_names_the_drift_and_does_not_fail` fails without `--warn` handling. |
| A2 one-offs in the backend container get web limits | Low | `rel_audit_check` passes `-e IHR_DB_LIMITS=worker`; README table and design.md say staging one-offs inherit `web` unless they ask for `worker` (compose comment too). | `test_the_release_audit_check_runs_as_a_worker`; `test_release_scripts` updated to the new command. |
| B1 /dev/shm plus shared_buffers equal the db limit | Medium | Default `IHR_PG_SHM` 256m (was 1g: 1g + 512MB = 1536m). `db_server_check`'s fallback want is 256m. Static test: shm + shared_buffers + 512 MB for connections must fit in `mem_limit`, with the defaults and with the 8 GB values (768MB, 2304m). Q-K94, README and design updated. | `test_shared_memory_and_buffers_leave_room_for_connections` fails with `1g`. |
| B2 memory ceilings exceed 8 GB | Low | `oom_score_adj: -500` on `db`, `-300` on `keycloak` (compose); README records the sum (about 7.1 GB core plus about 3 GB sidecars) and that the 8 GB test must record the real total. | `test_the_kernel_never_picks_the_database_or_keycloak_before_a_sidecar` fails without the db line (and checks no other service is negative). |
| B3 30 s web limit not measured on slow paths | Medium | **Measured, no code change needed.** Throwaway Postgres 18.6 (this machine, 256 MB shared_buffers), one dev hospital's schema plus 502,444 audit entries (busy-day mix of `dump-size-bench.sh`), the export's real SQL (`_select` printed from the code) in a plpgsql keyset loop of 2,000-row batches: JSONL window over the whole trail **253 batches, max 152 ms, 12.1 s total**; JSONL "unsealed" (worst case, nothing sealed) max 56 ms, 11.2 s; CSV whole range max 12 ms, 0.9 s; narrow CSV windows (start, middle, end) max 6 ms. Pre-stream queries: patient set over the whole range 221 ms, span min/max 7 ms, proof patient union 112 ms. So the 30 s limit is about 200 times the slowest batch and the export needs no `SET LOCAL statement_timeout`. **Not measured:** `owner/totals/?by=month` (aggregates over encounters and invoices, not the trail) and CARE's own CSV exports; they keep the 30 s web limit and the DatabaseStatementTimeouts alert (kind `statement`) is how a large hospital would show it. | none (measurement). |
| B4 the timeout alert never counts lock time-outs | Medium-Low | `DatabaseStatementTimeouts` regex and filter accept `lock` (`canceling statement due to lock timeout`); a fixture of 7 lock lines in `alert-cases.json` (expect `["lock","statement"]`); README row updated. | `test_staging_logs.test_timeout_lines_are_postgres_own` fails without `lock`; `check-logs.py` (real vmalert) fires for `lock`. |
| B5 README sync command wrong | Low | README says `up` (and so every release) runs the sync as `admin_manage`; the 8 GB plan step 1 says the same. | `Runbook.test_the_keycloak_sync_is_not_sent_through_manage` fails with the old sentence. |
| B6 back-channel service: no evidence, no down alert | Low | The down case was already covered, and now pinned: `ContainerNotRunning` (critical, every service not in its exclusion list) and `ContainerUnhealthy` (the image's health check calls `/ping/`, which the service answers) both reach `backchannel`; two series added to the promtool test. A probe through blackbox would need the service on the `monitoring` network, which its "internal only" rule forbids, so none was added. A log alert on Keycloak's own back-channel failure line was not added: I have no sample of that line. Q-K95 now says **unmeasured** (storm against the shared pool not run; reasoning only). | `deploy/monitoring/prometheus/tests/availability.yml` (promtool): adding `backchannel` to the rule's exclusion list fails the test (shown). |

Open for the owner: Keycloak's own back-channel failure log line is not alerted on; add one after the first real failure shows its text.

tier-fit: OK (Sonnet high suited well-specified fixes; A1 needed reading the release's serving path).
