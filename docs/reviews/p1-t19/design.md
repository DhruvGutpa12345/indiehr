# P1-T19 design: separate database roles (draft for attack, 2026-11-13)

PLAN P1-T19 (Phase 1 review R1-01, High; TENANCY_DESIGN §6.10). Research with file:line evidence
and Postgres 18 doc links: the run's scratchpad `t19/connections.md` and `t19/postgres.md`.

## The problem

One role, `ihr`, owns the database and every table and serves every request. So code execution
or SQL injection in the web app could:
- disable the audit triggers (`ALTER TABLE … DISABLE TRIGGER` needs only the owner);
- write the "read-only" terminology tables (the guard is a session flag any session can set);
- change or drop schemas.

The audit trail would show such tampering afterwards (verify plus hourly fingerprints), but
nothing stops it.

## Split (by Claude, 2026-11-13, awaiting owner confirmation: Q-K30)

- **P1-T19a:**
  - the roles and the grants (`ensure_grants`);
  - the run-time refusal;
  - tests that run real work as the app role (the isolation suite under `SET ROLE ihr_app`);
  - CI.
- **P1-T19b:** moving an existing staging database, the secrets, compose and the restore drill,
  proven in the local staging proof.

## Design

1. **Roles** (Postgres 18):
   - **`ihr_owner`:**
     - LOGIN; owns the database `ihr`, and so `public`;
     - no SUPERUSER, CREATEDB, CREATEROLE, REPLICATION or BYPASSRLS;
     - used by the migrate one-shot, `admin` one-offs (terminology loader, `ihr_create_tenant`,
       pack apply when run there), the provisioner, and per-hospital dumps and restores (P1-T22).
   - **`ihr_app`:**
     - LOGIN, NOINHERIT, no attributes, no memberships (it is never a member of `ihr_owner`:
       owner rights are inherited by members);
     - CONNECT only (no CREATE, no TEMPORARY);
     - used by the backend, celery-worker, celery-beat and staff-worker.
2. **Grants: one idempotent `ensure_grants(schema)`** beside the audit triggers.
   - It runs on `post_migrate`, in the provisioning schema step, and from `ihr_fleet_run check`
     (P1-T18). It changes only what differs.
   - **Hospital schemas:**
     - USAGE on the schema;
     - SELECT, INSERT, UPDATE, DELETE on tables;
     - USAGE and SELECT on sequences.
     - The audit trail is the exception: `ihr_audit_entry` and `ihr_audit_seal` get SELECT and
       INSERT only. No UPDATE, DELETE or TRUNCATE, so the append-only triggers become a second
       line.
     - Nothing gets TRUNCATE, TRIGGER, REFERENCES or MAINTAIN.
   - **`public`:** every table is classified in a reviewed list, `public_tables.txt`
     (`dml` | `select` | `none`). The terminology tables, the PIN directory, `PackRelease` and
     `django_migrations` are `select`. A table missing from the list fails the tests and the
     check.
   - Default privileges (`ALTER DEFAULT PRIVILEGES FOR ROLE ihr_owner …`) are a convenience only.
     `ensure_grants` is the authority, because anything made by another role (dev, CI, a
     restore) gets no defaults.
3. **Run-time refusal.** In deployment settings, the first database connection of every process
   (Django's `connection_created`) checks that the connected role:
   - is not a superuser;
   - is not a member of `ihr_owner`;
   - owns no table in `public` or any hospital schema;
   - has no CREATE or TEMPORARY on the database and no CREATE on any schema.
   If the check fails, it raises and stays refused (every request 500s, the health check fails),
   with a clear log line. Owner-run containers (migrate, admin, provisioner) set
   `IHR_DB_ROLE=owner` and skip the check; any other container must pass it.
4. **The provisioner** keeps the owner URL: it creates and drops schemas and migrates them. It is
   an internal worker with no route from the internet; the web app only queues its work. Moving
   the DDL to a separate one-shot job is the runner-up (Q-K30).
5. **Proof the app role is enough:**
   - **The tests:** a test-settings switch (`IHR_TEST_DB_ROLE=ihr_app`) creates the role in the
     test cluster, runs `ensure_grants`, and makes every test-client request connection
     `SET ROLE ihr_app`. The isolation suite (the crawl of every route, both hospitals) and the
     plugin suites then run as the app role in CI.
   - **Negative tests, as `ihr_app`:**
     - `ALTER TABLE … DISABLE TRIGGER`, `DROP SCHEMA`, `CREATE TABLE` and `CREATE SCHEMA` fail;
     - writing a terminology table fails, even with the session flag set;
     - UPDATE or DELETE of `ihr_audit_entry` fails at the privilege level;
     - TEMP tables fail.
   - A mutation row removes one grant restriction (for example the audit-entry UPDATE revoke) and
     a test must fail.
6. **Moving an existing database (P1-T19b)**, in `staging.sh up` (idempotent, as `postgres`):
   1. rename `ihr` to `ihr_owner` (SCRAM passwords survive a rename);
   2. create `ihr_app` with a new secret;
   3. run `ensure_grants` over every schema through the migrate one-shot.
   - Secrets: `app.env` holds the `ihr_app` URL; a new `db-owner.env` holds the owner URL,
     mounted only into migrate, admin and the provisioner.
   - The restore drill stops parsing `postgres://ihr:`.
   - pgBackRest restores roles and grants (physical).
   - The local staging proof shows the move on a database made by the previous release.
7. **Not fixed here** (noted): any session can still set the audit actor and context (it labels
   its own writes). The role split does not change that, since the app must write audit rows.

## Decisions (auto-accepted, awaiting the owner)

- **Q-K30:**
  - the split;
  - the provisioner keeps owner rights (runner-up: a separate DDL job);
  - run-time refusal on the first connection;
  - `public` tables classified in a reviewed list;
  - the rename of `ihr` to `ihr_owner` on existing servers.

## After the attack (2026-11-13): the design as it is built

Two Opus reviewers (correctness and feasibility; security and tenancy) raised 24 findings, and a
third agent tried to refute each one. Result: 0 refuted, 11 confirmed, 13 partly, none fatal.
Where this section and the sections above disagree, this section wins.

### P1-T19a (code: roles, grants, refusal, audit hardening, tests)

1. **Three modes, one setting (C2, C4): `IHR_DB_ROLES` = `off` | `test` | `required`.**
   - **`off`** is the default, for dev and the plain upstream CI job: `ensure_grants` does
     nothing and logs nothing beyond one line.
   - **`test`:** the test runner creates `ihr_app` (cluster-wide, idempotent) and enforces it.
   - **`required`:** deployment, switched on by P1-T19b in the same release that moves the
     roles. Missing roles or wrong ownership fail loudly.
   So P1-T19a can ship on its own without breaking staging.
2. **`ensure_grants` converges from the catalogue (S7, C12).**
   - It reads the actual ACLs (`relacl`, `nspacl`, column ACLs, `PUBLIC` included) and changes
     only what differs.
   - It connects with `dispatch_uid`.
   - Repair happens on `post_migrate`, in the provisioning schema step and in
     `ihr_fleet_run migrate`. `ihr_fleet_run check` and the run-time check only report.
   - No global table defaults; global defaults only for `USAGE ON SCHEMAS`. TENANCY §6.10's
     wording is fixed to match.
3. **Database ACL (C8):** `REVOKE ALL ON DATABASE … FROM PUBLIC`; CONNECT to `ihr_app`; CONNECT
   and TEMPORARY to `ihr_owner`.
4. **Two reviewed classification lists (C10, S5):**
   - `public_tables.txt`;
   - `hospital_tables.txt`, which holds the exceptions to "DML in a hospital schema".
     Hospital `django_migrations` is `select` (so the app cannot fake a migrated schema for
     P1-T18's gate), and so is every table the audit trail does not record.
   - A table missing from a list fails the tests.
5. **The provisioner's inputs (S1, High).** The owner-run provisioner trusts the queue, so the
   app must not be able to write what the provisioner acts on:
   - `Hospital`, `Domain`, `RetiredAddress` and `OperatorAccount` are `select` for `ihr_app`;
   - `TenantOperation` gets INSERT and SELECT plus column-level UPDATE on the queue columns the
     console writes;
   - the provisioner refuses a rollback of any hospital that was ever live, read from a column
     the app cannot write.
6. **The audit tables (S2, S3).**
   - `ihr_audit_entry`: column-level INSERT on the data columns only. A BEFORE INSERT trigger
     forces `txid`, `at` and `salt`, so the app can neither backdate nor pre-claim an id.
   - `ihr_audit_seal`: no INSERT for the app at all. Sealing goes through a SECURITY DEFINER
     function owned by `ihr_owner` (`search_path` pinned, EXECUTE only to `ihr_app`, no
     arguments) that computes the seal itself.
   - This changes how ADR-010's chain is written, not what it guarantees. ADR-010 gets an
     amendment (Proposed).
7. **The run-time refusal stays refused (C1, C9, S6, S9).**
   - On the first connection of a process, the check runs `ensure_grants` in report mode (one
     query with `has_table_privilege` over the classified tables). It also refuses on:
     - any role membership;
     - any role attribute;
     - any `pg_db_role_setting` row for the role;
     - CREATE or TEMPORARY anywhere.
   - On failure it logs the refusal line, closes the connection and sets a process-wide flag.
     Every later connection attempt then raises at once.
   - Every runtime container (backend, celery-worker, celery-beat, staff-worker) also runs
     `manage.py ihr_db_role_check` before it starts its process, so a misconfigured container
     exits and restart-loops visibly.
   - A log alert catches the refusal line.
   - `IHR_DB_ROLE` declares the expected role. An owner-declared container must actually be the
     owner, and an app-declared one the app, so the variable cannot quietly switch the check
     off.
8. **Tests as the app role, for real (C5, C6, S8).**
   - In `test` mode, a session-level `SET ROLE ihr_app` applies to every test connection (thread
     connections too, through `connection_created`), outside any atomic block.
   - An `execute_wrapper` asserts `current_user = 'ihr_app'` for every query while the mode is
     on, so a test can never silently run as the superuser.
   - The superuser is used only by explicit fixture helpers.
   - The isolation suite, the plugin suites and the worker paths (sealing, anchoring, the
     staff-worker cycle, beat) run in this mode in CI.
   - Negative tests and a mutation row as before, plus: the second connection after a refusal;
     audit columns forced; a forged seal impossible; hospital `django_migrations` not writable.

### P1-T19b (moving staging)

9. **Secrets are made on the deploying machine (C3, S4), as `s3-init` does.**
   `staging.sh db-split-secrets`:
   - a **new** owner password (never the old shared one);
   - an app password;
   - `app.env` gets the app URL;
   - `db-owner.env` gets the owner URL (mounted only into migrate, admin and the provisioner);
   - the old `IHR_DB_PASSWORD` is removed.
   The owner password goes into your password manager.
10. **The move runs only under a full stop (C7)**, inside P1-T18b's `staging.sh release` or
    `staging.sh db-roles --migrate`:
    1. hold everything and stop every app container;
    2. rename `ihr` to `ihr_owner`;
    3. set the new passwords;
    4. create `ihr_app`;
    5. reset both roles' settings (`ALTER ROLE … RESET ALL`);
    6. converge the grants;
    7. start.
    Every later `up` re-asserts the role settings and passwords.
11. **Restore drill and admin (C11, S10):** the drill renames and creates the roles on its copy,
    and runs `ihr_audit_verify` as the owner from `db-owner.env`. The admin one-off uses the
    owner URL.
12. **Proof:** the local staging proof moves a database made by the previous release, then runs
    smoke, the isolation checks and the refusal (a deliberately mis-set container must exit).
