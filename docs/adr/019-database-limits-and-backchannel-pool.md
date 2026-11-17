# ADR-019: Database limits per process, and the back-channel logout pool

- **Status:** Proposed. Q-K92…Q-K98 were auto-accepted by Claude on 2026-11-17 and await the
  owner's confirmation.
- **Context:** P1-T23, and Phase 1 review findings R1-09 and R1-10. The design is
  `docs/reviews/p1-t23/design.md` and the build is `docs/reviews/p1-t23/build.md`.

## Decision

1. **Statement, lock and idle limits are set per process**, as connection startup options from
   `ihr_platform/db_limits.py` (`PROFILES`), never with `ALTER ROLE … SET`. The database-role check
   already treats any stored role setting as drift.

   | Profile | Used by | Statement | Lock | Idle in transaction |
   |---|---|---|---|---|
   | `web` | backend, back-channel, beat | 30 s | 10 s | 60 s |
   | `worker` | Celery, the staff worker, app one-offs | 300 s | 30 s | 300 s |
   | `owner` | migrate, admin, provisioner, restore, fleet runner | none | none (code sets its own `SET LOCAL lock_timeout`) | 30 min |
   | `watch` | the audit watch | unchanged | unchanged | unchanged |
   | `test` | tests | same as `web` | | |

   `pg_dump` sets its own. `settings/secure.py` refuses `IHR_DB_LIMITS=off` on a server.

   Drift is checked in three places:
   - `ihr_db_role_check` and the per-connection re-check read the live values back;
   - `staging.sh` `db_server_check` runs at `up`, `release` and `db-roles --assert`;
   - log alert `DatabaseStatementTimeouts` covers the statement, lock and idle kinds.
2. **Server settings** are listed once, in `deploy/postgres/server-settings.txt`, and staging, dev,
   CI and both recovery starts use them:
   - `max_locks_per_transaction=256`, because a hospital restore holds about 3,800 locks;
   - `effective_cache_size=3GB`.

   On staging, `/dev/shm` defaults to 256 MB. `shared_buffers` stays at 512 MB and the db
   `mem_limit` at 1536m until the 8 GB load test; the plan for that test raises them to 768 MB and
   2304m. A static test keeps shm + `shared_buffers` + 512 MB of connection memory within the
   `mem_limit`.
3. **Keycloak's back-channel logout gets its own small service**, `backchannel` (Q-K95):
   - one gunicorn worker with 2 threads, about 0.2 GB of RAM, limit 256m;
   - on the internal network only, with no Traefik route and no keys;
   - it answers only the logout path.

   The reason is that a sign-out holds a backend thread while Keycloak calls back into the same
   pool, so a burst of sign-outs could lose its logout tokens. This choice is **reasoned, not yet
   measured**: the shared-pool comparison is step 7 of the 8 GB test plan.
4. `transaction_timeout` is not set for now (Q-K96).

## Consequences

- A runaway web query is cancelled after 30 s. Owner work (migrations, restores, the fleet runner)
  is never cut off by these limits.
- A release that changes a database setting recreates only the db container before it checks
  (review finding A1). A read-only check warns before the window opens.
- **The owner must run the load test on an 8 GB server before go-live**
  (`docs/reviews/p1-t23/build.md` §5). The numbers above are groundwork measured on the 6.7 GB
  development machine.
- These limits only guard against accidents: any session may `SET` its own limits.
