# P1-T23 design: Postgres operating rules, back-channel pool, load-test groundwork

Task: PLAN P1-T23; Phase 1 review R1-09 and R1-10 (with the challenger's notes, phase-1-review.md:139-140).
Designer: Claude (Opus, high), 2026-11-17, unattended run. Decisions Q-K92…Q-K98 are auto-accepted and
await owner confirmation. **The load test on an 8 GB target is blocked on the owner** (no paid server):
this task delivers the rules, a reduced measurement on the 6.7 GB dev machine, and the exact 8 GB plan.

## 0. What the code does today (checked)

- Staging `db` (deploy/staging/compose.yaml:128-177): `shared_buffers=${IHR_PG_SHARED_BUFFERS:-512MB}`,
  `max_connections=100`, `max_locks_per_transaction` at the default 64, `shm_size: 256m`,
  `mem_limit ${IHR_PG_MEMORY:-1536m}`, and no timeouts. Dev db (care-local.override.yaml:63):
  `shared_buffers=64MB`, `max_connections=40`, 64 MB `/dev/shm` (measured with `df` today).
- Evidence: P1-T1c (scale-results.md §5, §6): 64 locks ran out in a transaction that read a few hospitals;
  64 MB `/dev/shm` failed at about 158 hospitals; **one connection holds about 80 MB after light use of
  100 hospitals (0.5 GB worst case)**. P1-T22b: one hospital's restore load holds **3,794 locks for 1,101
  relations** (the whole default table is 6,400).
- Timeouts are set only locally: `SET LOCAL lock_timeout` in DDL paths (seal.py:448, install.py:418,
  forms_guard.py:575, placement.py:325, queue_guard.py:120, supersede.py:661, restore/engine.py:901/964/1957,
  console/views.py:380, db_roles.py:1160, migrations), `fleet.py:200/455` (10 s for migrate), seal 30 s
  statement (seal.py:109/120), `set_config('statement_timeout', …, true)` in identifiers.py:423 and the
  terminology engine (engine.py:342), `rf2.py:446` (0 for the loader). The audit watch already sets
  per-process startup options (settings/watch.py:46-50: read-only, 300 s, idle 60 s, lock 10 s).
- **The role model forbids stored settings** (db_roles.py:894-958 `role_problems`, `_identity_problems`
  re-checked on every new app connection: S9, "a role may set its own defaults, e.g. a forged audit
  actor"). So `ALTER ROLE ihr_app SET statement_timeout` would be reported as drift today.
- Web: `ATOMIC_REQUESTS=True` (deployment.py:20): every request is one transaction, idle while Python
  calls out (oidc.py `TIMEOUT = 5` s to Keycloak; boto to S3). gunicorn gthread 2 workers x 4 threads
  (backend.Dockerfile:74), `CONN_MAX_AGE=60`. The audit export streams from one REPEATABLE READ snapshot
  (export.py:188-260, `_snapshot()`), so a slow client leaves that transaction idle between fetches.
- Sign-out (ihr_identity/views.py:284) calls Keycloak's logout inside the request; Keycloak then posts the
  back-channel logout to `http://backend:9000/api/ihr/backchannel-logout/`, **the same 8 threads**
  (ADR-009 §78 already notes the nested wait). A lost logout token falls back to the one-minute refresh
  (ADR-009 §3, measured 60 s).

## 1. Settings: level, value, reason

**Levels used.** (1) Server command line for memory and lock-table sizes (restart). (2) **Per process**,
as libpq startup options built by one table in a new `ihr_platform/db_limits.py` and set in
`apply_tenancy` (`OPTIONS["options"] = "-c statement_timeout=… -c lock_timeout=… -c
idle_in_transaction_session_timeout=…"`), the way settings/watch.py already does it. Startup options
beat role and database defaults and reach every Django process in dev, CI and staging whatever role it
connects as (dev connects as `postgres`). (3) `SET LOCAL` / `set_config(…, true)` inside the code paths
that legitimately need more or less. **Not used: `ALTER ROLE … SET`** (keeps the "no stored settings"
invariant and its S9 check; the app role could ALTER itself anyway, and every app client is Django). Not
a security control: any session may `SET` these; they bound accidents and noisy neighbours (§6.16).

### 1a. Server (db command line; staging, dev override and CI's compose take the same list)

| Setting | Value | Reason |
|---|---|---|
| `max_locks_per_transaction` | **256** | 64 ran out (P1-T1c §6); a restore holds 3,794 locks; 256 x 100 connections = 25,600 slots: a restore plus a migrate plus traffic fit. Cost: a few MB of shared memory (measured in L2). |
| `shm_size` (container `/dev/shm`) | ~~1g~~ **256m** after review B1 (`IHR_PG_SHM`; 1g plus 512MB of buffers filled the 1536m limit) | It is a tmpfs ceiling, charged to the db container only when parallel queries use it. 256 MB covers the cap of 100 (challenger); 1 GB keeps the cap review (P1-T1c "at least 1 GB") from hitting the 158-hospital cliff. Must stay below the db `mem_limit`. |
| `shared_buffers` | **768MB** on the 8 GB server (env, unchanged default 512MB until the 8 GB test confirms) | Catalogue about 3.6 MB per hospital (P1-T1c) = 360 MB at 100, plus Keycloak's database and hot rows. More would starve the per-connection memory below. |
| db `mem_limit` | **2304m** (from 1536m), to be confirmed by the 8 GB test | shared_buffers 768 MB + about 19 hospital-touching connections x 80 MB (1.5 GB at 100 hospitals, P1-T1c §5) + Keycloak's 20 small connections. At 1536m a 100-hospital cell can be OOM-killed under load. |
| `effective_cache_size` | **3GB** | Planner hint only, no memory. |
| `max_connections` | 100 (unchanged) | Sum of pools is about 45 (backend 8, back-channel 2, celery 2, beat 1, workers 2, Keycloak 20, watch, dumper 2, exporter, one-offs). |
| `transaction_timeout` | **not set** (Q-K96) | TestCase classes, restores and long tasks are single transactions; statement + idle bounds are enough for requests. |

### 1b. Per-process profiles (`db_limits.PROFILES`, chosen by `IHR_DB_LIMITS`, else derived)

| Profile | Processes | statement | lock | idle in transaction |
|---|---|---|---|---|
| `web` | gunicorn backend, back-channel service, celery-beat, dev runserver | **30 s** (§6.16) | **10 s** | **60 s** (> Keycloak 5 s and S3 calls inside a request) |
| `worker` | celery-worker, staff-worker, an app-role `manage` one-off that sets `IHR_DB_LIMITS=worker` (staging compose gives every service `web`, so the derivation applies to dev and CI only; review A2) | **300 s** ("report queue", §6.16) | **30 s** | **300 s** (kc_http 30 s calls in staff tasks) |
| `owner` | migrate, admin, provisioner, restore, fleet runner, supersede, terminology loader | **0** | **0** at session (DDL paths already `SET LOCAL lock_timeout`) | **30 min** (an abandoned admin shell must not hold ACCESS EXCLUSIVE for hours) |
| `watch` | audit-watch | unchanged: 300 s | 10 s | 60 s |
| `dump` | dumper | none at session: `pg_dump` sets 0 itself; dumper keeps `SET LOCAL lock_timeout '60s'` and `--lock-wait-timeout` | | |
| `test` | test runner (both role modes) | **30 s** | **10 s** | **60 s** |

Derivation (no env needed): `process_name()` `gunicorn` -> web; `celery` with `beat` in argv -> web,
else worker; `manage:runserver` -> web; `manage:test` -> test; any other `manage:*` -> owner if
`IHR_DB_ROLE=owner` (or dev's superuser), else worker. Compose sets `IHR_DB_LIMITS` explicitly on each
service anyway (the derivation is the fallback for one-offs). `IHR_DB_LIMITS=off` is refused by
`settings/secure.py` on a server.

**Legitimate long work and how it stays inside** (the builder confirms each with `graphify explain`
and a measurement; any that does not fit gets a local override, never a looser profile):

- Fleet runner, provisioning, restore, supersede, terminology load: owner profile, no statement limit;
  their own `SET LOCAL lock_timeout` stays. The restore engine must be checked for idle gaps over
  30 min inside its load transaction (S3 version copies, Keycloak): if any, it sets
  `SET LOCAL idle_in_transaction_session_timeout = 0` there.
- Pack apply: API accept/keep (web) applies one item, well under 30 s; facility fill is a Celery task
  (worker); start-up and provisioning runs are owner.
- **Audit export (web):** the streaming snapshot must set `SET LOCAL idle_in_transaction_session_timeout
  = '10min'` and keep each batch query under 30 s (measured on the 500k-entry bench hospital, L1);
  a slow client past 10 min idle ends the export (a JSONL export is verifiable, so a cut file shows).
- Role matrix and isolation crawl (tests, test profile): each request's statements are milliseconds;
  CI proves the 30 s limit with CARE's full suite in tenant mode.
- Sealing (30 s local), identifiers' repeat check and terminology queries (own `set_config`) are
  already tighter than the profile and unchanged.
- Celery tasks: per-minute seal and grant expiry, hourly anchor, 15-minute pack backstop, 10-minute
  seal_inactive: all far under 300 s on dev data; the 8 GB test records their maximum.

**How dev, CI and the local proof get the same rules.** The profiles live in code, so every Django
process gets them everywhere. The server list lives once in `deploy/postgres/server-settings.txt`
(`name=value` lines); staging's `db` command, `deploy/dev/care-local.override.yaml`, the Keycloak dev
override and CI's compose override carry the same lines (dev keeps its smaller `shared_buffers` and
`max_connections`, which the file marks as "per environment"); a static test compares them.

**Drift detection.**
1. `ihr_db_role_check --for <process>` (every container start) and the per-connection re-check in
   `on_connection_created` read `current_setting()` of the three timeouts and refuse with reason
   `limits` when they differ from the process's profile (one more column in `_IDENTITY`'s query).
2. `staging.sh db-roles --assert` (run by every `up` and release) also checks `SHOW
   max_locks_per_transaction` >= 256, `shared_buffers` = the `.env` value, `/dev/shm` >= 1 GiB
   (`df -k` in the db container), and that `pg_db_role_setting` is empty for our roles and for the
   database (an `ALTER DATABASE … SET` would also be drift).
3. Alerts: `PostgresSettingsDrift` on postgres-exporter's `pg_settings_max_locks_per_transaction < 256`
   (builder confirms the metric name); log alert `StatementTimeouts` on Postgres's "canceling statement
   due to statement timeout" / "terminating connection due to idle-in-transaction timeout" (warning,
   more than 5 in 10 min; the terse log carries no values).

## 2. The back-channel logout pool

The risk is not volume (one POST per sign-out) but a **self-wait**: a sign-out holds a backend thread
(and its open transaction) while Keycloak posts back into the same 8 threads. With 8 sign-outs at once
(shift change, or many 30-minute idle time-outs, which also call Keycloak's logout) the posts queue
behind them; after oidc's 5 s timeout the sign-outs return without "everywhere", and late tokens are
lost to the 60 s refresh fallback. Slow requests (up to 30 s each) make the same queue.

| Option | What | RAM | For / against |
|---|---|---|---|
| **A ⭐** own service | `backchannel` container: same image, gunicorn 1 worker x 2 threads, `CONN_MAX_AGE=0`, internal network only, **no Traefik route** (Keycloak calls `http://backchannel:9000/…`); a settings flag answers only the logout path and `/health/` (404 otherwise); `web` profile; app role; no client key, no anchor key | about 160-200 MB used (dev backend idle 163 MiB, measured today), `mem_limit 256m`; 2 Postgres connections touching only identity tables | Removes the self-wait whatever the load; matches PLAN P1-T8 and ADR-009. One more container to keep in the role and secret tests. |
| B reserved worker | a second gunicorn on another port in the backend container | same RAM inside the backend's 1 GB | Two processes in one container, one health check for both; no gain over A. |
| C share, bounded | keep one pool; alert on Keycloak's back-channel failures and logout latency | 0 | Correctness depends on load; the fallback (60 s) is a security window the owner chose to close in P1-T5d. |

**Recommendation A** (Q-K95). Staging memory, as measured use (README: 2.2 GB on the proof) rather than
ceilings: + about 0.2 GB for the service, + 0.26 GB shared_buffers, + up to 1.5 GB Postgres connection
memory at 100 hospitals = about 4.2 GB projected, inside 8 GB with page cache to spare. Ceilings rise
from about 8.4 GB to 9.4 GB (256m + 768m); they are runaway stops, not reservations. The local
measurement (L3) runs the sign-out storm with A and with C, so the owner sees the difference.

## 3. Measurements

### Reduced local measurement (this 6.7 GB machine; results in `docs/reviews/p1-t23/local-measure.md`)

- **L1 (dev stack up, no restart):** a small script (`scripts/pg-conn-memory.py`, stdlib + psql) opens
  one connection, touches k hospitals' main tables (k = 1…8 dev hospitals; 25 and 50 in a
  `bench_*` database via `ihr_bench_scale` in the throwaway bench container), and records that backend's
  private memory from `/proc/<pid>/smaps_rollup`. Also time the audit export's batch query on the
  500k-entry bench hospital. Record: MB per hospital touched; export batch max.
- **L2 (dev db restart with the new server lines):** `pg_shmem_allocations` for the lock tables before
  and after 256; `df /dev/shm`; the timeout tests' live versions (`pg_sleep` past each limit as app,
  not cancelled as owner). Record: shared-memory cost, the error each limit raises.
- **L3 (staging local proof; `care-dev.sh down` first, `up` after, then remove
  `deploy/staging/{.env,.local,.db-created,secrets}`):** 2 synthetic hospitals, `scripts/load-test.py`
  at 20 virtual staff for 5 min, then the sign-out storm (16 concurrent sign-outs while 8 slow requests
  hold the pool) with option A and with the shared pool. Record: `docker stats` every 5 s (peak per
  service), p50/p95/p99 per request kind, errors, timeout cancels, back-channel delivery delay and
  "signed_out_everywhere" rate. **Never reported as the 8 GB result.**

### The 8 GB load test (owner, when a server exists; results in `docs/reviews/p1-t23/load-8gb.md`)

Script `scripts/load-test.py` (stdlib only; refuses any host not under the staging suffix in `.env` or
`.localhost`; synthetic hospitals only). Steps:
1. Deploy the release with these rules (`scripts/deploy.sh`), `staging.sh db-roles --assert` passes.
2. Seed 25 synthetic hospitals (then 50, 100) with the demo seeder; synthetic staff and patients only.
3. Sign in the virtual staff through the real OIDC flow (Q-K97); 20 active staff per hospital, one request
   per 15 s each from a recorded CARE request mix (patient search, encounter view, list pages, writes).
4. Ramp 5 min, hold 30 min per level; during the hold run one sign-out storm, one pack accept, one
   audit export and the nightly dump at once; note the hourly anchor and per-minute seal.
5. Record every 5 s: container memory, host free memory and swap, Postgres connections and per-backend
   memory, lock counts, latencies, errors, Celery queue length, timeout cancels.
6. Find the knee: raise the rate until p95 crosses 1 s; that rate gives the cell cap for this server.

**Pass (per level):** no OOM kill and no swap-in; p95 < 1 s and p99 < 3 s; errors < 0.5 % (none from
timeouts on the request mix); every back-channel token delivered within 2 s; Celery queue drained
within 60 s of each burst; dump finishes in its window; db memory below its limit with 15 % margin.
The cell cap becomes the lower of 100 and the passing level (P1-T1c review of the cap).

## 4. Tests and what each proves

| Test | Proves |
|---|---|
| `ihr_platform.tests.test_db_limits`: a fresh connection built by `db_limits.options(profile)` with the values scaled to 1 s (same builder) | app (`web`) is cancelled by `pg_sleep(2)` (QueryCanceled), idle 1.5 s in a transaction ends the session, a held lock raises LockNotAvailable; an `owner` connection runs `pg_sleep(2)` to the end |
| same, chooser cases | gunicorn/runserver/beat -> web, celery -> worker, `manage:migrate` as owner -> owner, `manage:test` -> test; `off` refused by secure.py |
| `ihr_db_role_check` with a connection whose timeout differs | refuses with reason `limits`; the per-connection re-check refuses too |
| `test_db_roles` (existing + one case) | a stored `statement_timeout` on `ihr_app` is still a problem (S9 kept); a database-level setting is a problem |
| export test with a slow consumer (sleep > profile idle, scaled) | the export's own `SET LOCAL` keeps the stream alive; the profile alone would cut it |
| restore and supersede tests (`test_restore`, `ihr_audit.tests.test_supersede`) under the `test` profile | owner paths keep working; locks within 256 (CI's db runs with the new lines) |
| CARE's full suite and the role matrix in tenant mode (CI) | the 30 s statement limit does not break real request paths |
| `scripts/test_staging_postgres.py` (new, static) | staging, dev and CI compose carry every line of `server-settings.txt`; `shm_size` >= 1g and < `mem_limit`; every app service sets `IHR_DB_LIMITS`; `backchannel` has no Traefik route, no key volumes, is on `internal` only, and is the only `IHR_BACKCHANNEL_LOGOUT_URL` target |
| `test_staging_db_roles.py` (+ cases) | `db-roles --assert` fails on max_locks < 256, small `/dev/shm`, a stored role or database setting (stubbed psql, as the existing tests do) |
| promtool test for `PostgresSettingsDrift`; check-logs case for `StatementTimeouts` | the alerts fire, and the log line carries no values |
| back-channel settings flag test | the service answers the logout path and `/health/` only (404 for any hospital or console path) |

Run: `IHR_DB_ROLES=test scripts/care-tenancy.sh test --settings=ihr_platform.settings.test --noinput
ihr_platform ihr_identity ihr_audit ihr_access ihr_refdata ihr_roles` (incl. test_db_roles,
test_release, test_restore), the off-mode run, `python3 -m unittest scripts/test_staging_*.py
scripts/test_release_scripts.py`, `scripts/check-logs.py`, then pre-commit.

## 5. Decisions (QUESTIONS.md, Q-K92 onward; auto-accepted by Claude, 2026-11-17, awaiting owner confirmation)

- **Q-K92 — Where do the timeouts live?** A ⭐ per-process startup options from one code table
  (reaches dev, CI and staging; keeps "no stored settings"). B `ALTER ROLE ihr_app SET` with an
  allow-list in the role check (covers non-Django clients; weakens S9, misses dev). C both. Reverse: B or C
  by changing `role_problems` to an exact allow-list.
- **Q-K93 — The values.** A ⭐ web 30 s / 10 s / idle 60 s; worker 300 s / 30 s / 300 s; owner none /
  none / idle 30 min; export idle 10 min locally. B tighter web (15 s). C no idle limit. Reversible by
  editing `PROFILES`.
- **Q-K94 — Server sizes.** A ⭐ max_locks 256, `/dev/shm` 1 GB, shared_buffers 768 MB and db limit
  2304 MB on the 8 GB server (512 MB / 1536 MB stay the defaults until the 8 GB test). B shm 256 MB as
  today (challenger). Reverse via `.env`.
- **Q-K95 — The back-channel pool.** A ⭐ own `backchannel` service (about 0.2 GB). B a second gunicorn
  in the backend container. C share with an alert. Reverse: point `IHR_BACKCHANNEL_LOGOUT_URL` back at
  `backend` and drop the service.
- **Q-K96 — `transaction_timeout`.** A ⭐ not set now; revisit after the 8 GB test. B web 120 s.
- **Q-K97 — How the load test signs in.** A ⭐ the real OIDC flow with seeded synthetic users (TOTP
  from their seeded secret if required); nothing new on the server. B a staging-only command that mints
  sessions (faster; a sign-in bypass that must never reach production).
- **Q-K98 — Load target and pass criteria.** A ⭐ 20 active staff per hospital, one request per 15 s,
  levels 25/50/100, the pass list in §3, cap = lower of 100 and the passing level. B the owner's own
  numbers per hospital size.
