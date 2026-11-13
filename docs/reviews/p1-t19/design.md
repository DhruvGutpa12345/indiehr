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

## Review of the build (2026-11-13): what changed

Two reviewers attacked the P1-T19a code (security; correctness) and a third tried to refute each
finding (findings and verdicts: the run's scratchpad `t19/review.json`). Three were refuted
(S7, S9, C7). The fixes, each with a regression test in `ihr_platform/tests/test_db_roles.py`
unless named otherwise:

- **S1 (high), the provisioner's queue.** The app's table-level INSERT on `TenantOperation`
  let it forge every column the owner-run provisioner trusts. A BEFORE INSERT trigger owned by
  the owner, `ihr_operation_guard` (`ihr_platform/queue_guard.py`, migration
  `ihr_platform.0011`), refuses any kind but `provision` from anyone but the table's owner and
  forces `status`, `steps`, `result`, `hospital_id`, `started_at`, `finished_at` and
  `fleet_run_id`. Second line: `Hospital.provisioning_operation`, written only by the owner's
  reserve step (filled for older hospitals by the migration); run, rollback and resend_setup
  act on a hospital only for that operation (`runner.built_by`).
- **C1, a repair that took more than it revoked.** After any REVOKE the state is read again
  and every grant then missing is made, in the same transaction (a table's REVOKE also takes
  its column grants).
- **C2, one hospital stopping the fleet.** The process-wide refusal now covers process-wide
  facts only (the role, the database, public). A right beyond the model for the app or PUBLIC
  in one hospital's schema gates that hospital in that process (`release.schema_ready`,
  `release.ready_q`: web, tasks, beat, the staff worker), writes
  `ihr-db-role: gated schema=<schema> reason=extra_privileges`, and the worker's per-minute
  report makes HospitalNotServed name it. `ihr_fleet_run migrate` also converges the grants of
  a hospital whose migration failed, as far as they can be.
- **C3.** A RolesError or lock time-out repairing one hospital's grants holds and notes that
  hospital; the run goes on and the others are released (`test_release.FleetTests`). The
  "repaired" count counts only what changed.
- **C5, the sealing functions.** Migration 0004 no longer imports live code: its SQL is frozen
  in the file. `ihr_audit.seal.ensure_sealing` (every hospital schema's post_migrate, before
  the grants) keeps both sealing functions and the stamp trigger this build's, versioned by a
  comment stamp (`ihr_platform/db_functions.py`), so any migrate puts back a dropped or altered
  one and brings every schema to a newer build's; `coverage()` reports one that is not current.
  The accepted formats in `ihr_audit_seal_run` come from `CANONICAL`.
- **S2.** Every later new connection of an app process re-reads the role's identity,
  attributes, memberships and stored settings (one catalogue query) and refuses as the first
  one does. `ihr_fleet_run check` already reports stored settings for every role.
- **S3.** In required mode the owner's default EXECUTE to PUBLIC on new functions is revoked; a
  per-schema default for PUBLIC is a finding; any SECURITY DEFINER function in public or a
  hospital schema that is not in the model is a problem; the sealing functions' owner is
  checked; a grant option held by anyone but an object's owner is a right beyond the model
  (repaired with `REVOKE GRANT OPTION FOR ... CASCADE`).
- **S4.** Only plain and partitioned tables take the hospital default (`dml`). A view,
  materialized view or foreign table in a hospital schema gets nothing and fails the tests
  until `hospital_tables.txt` lists it.
- **S5.** `ihr_db_role_check --for <process>` judges the process the container runs next,
  never its own command line; only migrate, admin and provisioner may be the owner. The
  provisioner's `--role` is read with the command's own parser (`--rol staff` is staff).
- **S6.** A sealing run that finds the schema's lock taken returns 0 at once
  (`pg_try_advisory_xact_lock`), and `seal_once` bounds each call with a 30 s
  `statement_timeout`.
- **S8.** A large object owned by the app is an `owns_objects` refusal at the next start.
- **S10.** `IHR_DB_ROLE` set with `IHR_DB_ROLES=off` is refused by the deployment settings, and
  in off mode a process that connects as `ihr_app` or `ihr_owner` is refused (reason
  `roles_off`).
- **C4.** The per-schema work is linear in the fleet (each schema's objects are indexed once),
  and the run-time check logs one count instead of one warning per difference.
- **C6.** A required-mode refusal found by post_migrate's grants repair is raised by the last
  post_migrate receiver, so the other apps' receivers run first; a missing LOGIN on a role no
  service uses yet (watch, dump) is reported, never raised; `ihr_db_role_check --for migrate`
  checks the roles, the database's grants and the default privileges before anything is
  migrated (wired into the migrate one-shot by P1-T19b).
- **C8.** In test mode, SQL that changes the role (`SET`/`RESET ROLE`, `SESSION
  AUTHORIZATION`, `set_config('role', ...)`) is refused wherever it is in the text, outside
  `as_owner()`.

### Second review of the fixes (2026-11-13)

A verifier checked each fix above and found gaps and new problems; the second round of fixes,
each with a regression test (`test_db_roles.py` unless named otherwise), proved by mutating
each control alone:

- **S3's default revoke broke a new hospital's masking (regression, high once `required`).**
  With the owner's new functions nobody's, a new hospital schema's `ihr_audit_last4` was too,
  and the row trigger (run as the app) calls it directly: a trigger function fires without
  EXECUTE, a function it calls does not. `ihr_audit_last4` is now in `db_roles.FUNCTIONS` with
  EXECUTE for the app (PUBLIC's is revoked), and any other function that is not a trigger
  function and is not in the model is a problem until its EXECUTE is decided. The test makes
  the helper again under the default revoke and writes a masked identifier as `ihr_app`.
- **Broken controls are no longer only counted at run time (S1, S3).** A problem with a control
  (`Finding.control`: the queue guard, the sealing functions' security, search path and owner,
  a sealing function missing or changed by hand since it was installed, the stamp trigger off,
  large objects) refuses the process in public or the database (reason `controls`) and gates
  the hospital in its schema (`reason=controls`). An older build's sealing functions, unchanged
  as installed, do not gate (the schema's next migrate brings them up to date; a release that
  changes their SQL without a migration must not stop every hospital). An unknown SECURITY
  DEFINER function the app may execute refuses the process wherever it is
  (`Finding.escalates`): like CREATE on a schema, it is a way to its owner's rights from any
  hospital the process serves, and no release leaves one behind.
- **C2: gating hides a hospital, it does not take the right away.** The app role is shared, so
  a right beyond the model in a gated hospital's schema stays usable through any hospital still
  served. So: a fleet-wide `ihr_fleet_run migrate` converges the grants of every hospital
  schema in the catalogue (archived, deferred, unselected, or with no hospital row), and a
  fleet-wide `check` reports them (`test_release.FleetTests`); the worker repeats each gated
  line every minute and the new alert DatabaseSchemaGated (`deploy/logs/alerts/db-roles.yml`,
  `scripts/check-logs.py`) names the schema for as long as it stays gated, archived ones too.
  CREATE on any one hospital's schema still refuses the whole process, on purpose: the owner's
  migrations and provisioner run there, so an object the app made there is one the owner may
  resolve; the model never grants it, so no release leaves it stale.
- **S8:** required mode now checks that the app cannot make large objects
  (`large_object_problems`, a control problem): migrate fails and the run-time check refuses
  until P1-T19b's REVOKE has been run as postgres.
- **S2, known limit 1:** a changed password is still invisible to every role but the
  superuser; the first sign of one, Postgres refusing a split role's login, is now the alert
  DatabaseRoleLoginFailed (the db container's own line).
- **C6:** each migrate run starts with nothing deferred (`pre_migrate` clears it), so a refusal
  left by an emission that stopped early is never raised by a later, unrelated migrate.
- **C4:** one schema's findings read no list file (the run reads each once), and the check is
  timed at the 100-hospital cap (known limit 7: 0.5 s).
- **S10:** on a server, off mode also refuses a process that logs in as a superuser, so a
  container that lost both variables and connects as `postgres` does not run unchecked.
- **C8:** the test wrapper also refuses `SET`/`RESET session_authorization`, `DISCARD ALL` and
  `set_config('session_authorization', ...)`, and a `set_config` whose name is a query
  parameter; an UPDATE of a column named `role` is not taken for one.

### Known limits (accepted, or left to P1-T19b)

1. **The app role can change its own password (S2).** Postgres lets any role `ALTER ROLE` itself
   (`SET` a default, `PASSWORD`). Stored settings are caught at the next connection. A changed
   password cannot be seen: `pg_authid` is readable only by a superuser, and neither the owner
   nor the app is one (an event trigger never fires for roles, and logging statements would
   write the new password into the log). The effect is a lock-out at the next login, which the
   alert DatabaseRoleLoginFailed reports from Postgres's own line; the operator sets the
   password again, which also ends the attacker's login. The database is on the internal
   network only. P1-T19b's `up` re-asserts both roles' passwords and runs `ALTER ROLE ... RESET
   ALL` as `postgres`.
   - **Its other side (second review):** the same self-service makes the refusal a lever. An
     attacker with SQL as `ihr_app` can `ALTER ROLE ihr_app SET ...`, and every app process then
     refuses at its next new connection (sticky, DatabaseRoleRefused `role_settings`): a
     fleet-wide outage. Accepted: availability against a compromised app role is not a goal of
     P1-T19 (the app can already delete hospital data), and refusing beats serving with a
     forged default (an audit actor, a statement time-out). Runbook (P1-T19b): as postgres,
     `ALTER ROLE ihr_app RESET ALL` and `ALTER ROLE ihr_app IN DATABASE <each> RESET ALL` for every
     database `pg_db_role_setting` names (a role may store a default for itself in any database,
     one it cannot connect to included, and the check counts them all: `staging.sh db-roles
     --assert` does this), treat it as an incident, then restart the app containers.
2. **Availability against a compromised app role (S6).** Holding the sealing key now makes
   sealing skip, never wait. The app can still delay sealing by keeping a transaction open
   (the horizon) or exhaust connections; nothing can be forged, and SealerLate catches a stall.
   Not a goal of P1-T19 (integrity).
3. **Large objects (S8).** Any role may create large objects by default, a small write channel
   the audit triggers do not see (it holds only what the attacker writes; the app can already
   read and write the hospital data it could copy there). Leftover objects owned by the app
   refuse its next start. Closing the channel needs the superuser: P1-T19b runs
   `REVOKE EXECUTE ON FUNCTION lo_create(oid), lo_creat(integer), lo_from_bytea(oid, bytea),
   lo_import(text), lo_import(text, oid) FROM PUBLIC` (the last two are superuser-only already
   since Postgres 11). The check that it did is built (second review): in required mode a
   creator the app may execute is a control problem, so migrate fails and every app process
   refuses until the REVOKE is done. Before it is, a leftover large object refuses every app
   container at its next start (`owns_objects`), another refusal an attacker could cause;
   runbook (P1-T19b): as postgres, keep a copy for the incident, then
   `SELECT lo_unlink(oid) FROM pg_largeobject_metadata WHERE lomowner = 'ihr_app'::regrole`.
4. **The console's own power (S1).** The app can still record a new provisioning request with
   input it chooses and queue run, rollback or resend_setup on an existing operation: that is
   the console's job, and its sign-in protects it. The provisioner validates a new request,
   refuses a hospital that already exists, refuses a roll-back of any hospital that was ever
   live, and acts on an existing hospital only for the operation that built it.
5. **Gating hides a hospital; it does not contain the right (C2, second review).** The app role
   is shared, so a right beyond the model in one hospital's schema (an UPDATE on its trail, a
   write to its `django_migrations`, TRUNCATE or TRIGGER) stays usable by an SQL injection
   through any hospital still served, until the owner repairs it. Refusing every process for it
   instead would let one held hospital's stale grants stop the fleet (C2's outage). What
   narrows the window: a release's fleet-wide migrate repairs every hospital schema in the
   catalogue (failed, deferred and archived ones too) before the app containers start; a repair
   that fails (a lock time-out) is named in its output; DatabaseSchemaGated names each gated
   schema every minute while it stays gated; and anything that is a way to the owner's rights
   (CREATE on any schema, a definer function the app may execute) refuses the process instead.
   Gating is per process and fixed at its first connection: after the repair (a fleet-wide
   `ihr_fleet_run migrate`), restart the app containers (a release restarts them anyway). An
   archive step that revokes every app right on an archived hospital's schema comes with
   offboarding; until then the fleet-wide repair keeps it at the model.
6. **Off stays the default until P1-T19b (S10).** P1-T19b makes `required` the deployment
   default in the same release that moves the roles, wires `ihr_db_role_check --for <process>`
   into every runtime container and `--for migrate` before `migrate_schemas` (C6, S5), and
   creates all four roles (watch and dump too). Until then off mode still refuses, on a
   server (the deployment settings), a process that logs in as a superuser (second review of
   S10: no deployed process does; staging's `ihr` is a plain role that owns the database), as
   well as one that connects as a split role.
7. **The first-connection check reads the whole catalogue (C4).** Once per process, linear in
   the number of hospitals (each list file read once per run, each schema's objects indexed
   once); it logs how long it took (`ihr-db-role: app role checked in N s`). It reads ACLs
   rather than one `has_table_privilege` aggregate (the design's wording), because the ACLs
   also show PUBLIC's and column grants and grant options, which an effective-rights query
   folds away. Measured (second review, 2026-11-13) on this 6.7 GB dev machine's Postgres 17
   with 100 copies of a current hospital schema's DDL (129,420 catalogue relations, no data):
   the check's catalogue part takes 0.5 s at 100 hospitals (0.05 s at 1, 0.15 s at 25, 0.25 s
   at 50; the same as `ihr_app`), and `ensure_grants` over public and all 100 takes 0.7 s when
   nothing differs and 2.9 s to make 21,424 missing grants. P1-T19b's local staging proof times
   it again on Postgres 18.6.
8. **A grant option whose grantor is not the owner (S3)** cannot be revoked by the owner's
   repair: it is reported (and refuses or gates) on every run until `postgres` revokes it.
9. **Test mode (C8).** COPY (`cursor.copy`), `callproc` and raw psycopg calls do not pass the
   test wrapper; they still run under the session `SET ROLE ihr_app`. SQL that builds a
   role change at run time (`EXECUTE 'SET ' || 'ROLE ...'` in a DO block) passes the text check;
   the next statement's `current_user` check catches it. Nothing in the code base does either. The CARE upstream suite
   runs as the superuser (its CI step is not in test mode); the isolation suite, which crawls
   every route of both hospitals, is.
10. **Unused roles still stop migrate (C6).** A wrong membership, attribute or stored setting on
    `ihr_audit_watch` or `ihr_dump` fails the migrate one-shot in required mode even before
    their services exist (only a missing LOGIN is a notice): they are LOGIN roles, and such a
    state is a privilege path. Deliberate.

### P1-T19b as built (2026-11-13)

Built as items 9-12 and the known limits' P1-T19b parts say: `staging.sh db-split-secrets`
(deploying machine; new owner password; `app.env` the app's address, `db-owner.env` the owner's,
mounted only into migrate, admin and the provisioner; `IHR_DB_PASSWORD` gone; idempotent, never
replaces a secret), the move under a full stop inside `staging.sh release` or `staging.sh db-roles
--migrate`, every `up` re-asserting both passwords and `RESET ALL` (in every database) and
the large-object revoke as postgres, `IHR_DB_ROLES=required` as the deployment default, the
pre-start `ihr_db_role_check --for <process>` in every runtime container and `--for migrate`
first in the migrate one-shot, the restore drill as the owner, and the README runbooks ("Moving
to split roles"; the alert runbooks name `db-roles --assert`). Static tests:
`scripts/test_staging_db_roles.py` (pre-commit hook `staging-db-roles-static`) and new cases in
`scripts/test_release_scripts.py`.

**Deviations and additions:**

1. **Passwords never in `db.env`, never as text to Postgres:** `staging.sh` turns each password
   into a SCRAM-SHA-256 verifier and sends `ALTER ROLE ... PASSWORD '<verifier>'` on psql's stdin,
   so the password is in no log, `pg_stat_activity` or command line. A new cluster's
   `initdb/10-roles.sh` makes `ihr_owner` without a password; `up` sets it before anything logs in.
2. **`ihr_audit_watch` and `ihr_dump` are NOLOGIN with no password** until their services exist,
   and every `up` re-asserts that: P1-T21 and P1-T22 must change `DB_ROLES_SQL`. Their missing
   LOGIN is a notice; `db_roles.check_connection` now skips notices in its drift count (it logged
   "2 differences ... migrate repairs them" on every app start; test
   `RefusalTests.test_a_notice_is_not_a_difference`).
3. **The role SQL runs in the database `ihr`** (one transaction, rename included): a function's
   privileges are each database's own. Run in `postgres` first, it left `ihr`'s large-object
   creators open, and the release's `ihr_db_role_check --for migrate` refused, as designed (found
   by the proof). Every `release` now re-asserts the roles before that check, so a release that
   stopped after the move finishes when run again.
4. **After the move the running backend is `ihr_app`,** which may not write the release's own
   records (hold, window, fleet runs: the owner's tables). `old_manage` (and `staging.sh fleet
   window`) then run the running build's command as a one-off of its image in the owner's logged
   migrate container; before the move they run in the backend as before. Without this every
   release after the move would have failed at its hold.
5. **The seal inside the move's window** runs the outgoing build with its own address (read from
   the running backend container) and `IHR_DB_ROLES=off` for that one-off: the split roles do not
   exist yet.
6. **No way back across the move:** `rollback` (both kinds) refuses, before any change, a target
   whose kept stack has no `db-owner.env` (fix forward; README). A restore to a moment before the
   move brings back the single role; `up` refuses it and `db-roles --migrate` moves it again
   (proven).
7. **`up` refuses a running single-role database before recreating anything** (the db container's
   settings changed with the split; recreating it would cut the old stack off).
8. **The settings refuse `IHR_DB_ROLES` other than `required` on a server** (not only a default).
9. **`deploy.sh`** refuses without `secrets/db-owner.env` on the deploying machine, and a plain
   start to a server with a database whose secrets are not split yet (`--release` moves it).
10. **Memberships** are revoked whoever granted them (`GRANTED BY ... CASCADE`), including any
    role's membership in ours. `db-roles --migrate` runs only on a stopped stack whose backend
    image is the last logged release's (the restore case), refuses while a release is
    unfinished, and says where it stopped (review fixes below).

**Measured** (local staging proof, Postgres 18.6, 3 hospitals; record: the run's scratchpad
`t19b/proof.md`): the move's transaction 1 s; stopping every app container plus the move 13 s;
grants converged as the owner: public 25 granted, each hospital 213 (7 s for the platform step
and 7 s for the three hospitals, one-shot start included; 3 s fleet-wide in `db-roles --migrate`);
the move release 241 s end to end (`up` 146 s); `db-roles --migrate` on a stopped stack 120 s
(`up` ~107 s); `ensure_grants` with nothing to change over public and 3 hospitals (3,390
catalogue relations) 0.04 s, report 0.02 s, an app process's first-connection check 0.0-0.1 s;
`db-roles --assert` 1 s; restore drill 13 s. Smoke 0 FAIL after both moves; every isolation spot
check refused; each mis-set container exited non-zero with the refusal line.

**Review fixes (2026-11-13):**

- **S1/C3, stored settings in another database.** A role may `ALTER ROLE` itself `IN DATABASE`
  any database (also `postgres`, `template1`, `keycloak`, which it cannot connect to); the run-time
  and migrate checks count every row, but `up` and `db-roles --assert` reset only role-wide and in
  `ihr`, so the runbook could not end that refusal. `DB_ROLES_SQL` now resets every row of the
  four roles in `pg_db_role_setting` (a loop, `%I`-quoted) and raises if any is left; the test
  runner's `create_test_roles` does the same; the refusal and the migrate check name the
  databases. Proven on a throwaway Postgres 18.6 with `db_roles_apply` itself.
- **C2, `db-roles --migrate` outside a release.** It now refuses while any app container runs
  and unless this stack's backend image is the last logged release's build: on a running server
  the move belongs to `release` (hold, seal, restore point, log). Its own hold is gone (dead), as
  is `db-split-secrets`' hint to use it on a server.
- **C1/S2, recovery before the move.** A release that stops before the move (stage 1 or 2, the
  database still single) no longer advises a rollback (`rel_roles_guard` refuses every rollback
  while the database is single; `rel_rollback_advice` drops a rollback whose target predates the
  split), nor `up` / a migrate-container fleet step. It names `db-roles --serve-previous`, which
  starts the build that ran on its own containers (`docker start`; see the verifier fixes), lifts the holds and
  clears the window with it. Before the move `fleet release|hold|window` run in the running
  backend; other fleet steps refuse. `db-roles --migrate`'s stopped-before-the-move message says
  to run it again.
- **Verifier fixes (2026-11-13).** (1) `--serve-previous` ran `docker compose start backend`, which
  in Compose v5.7.1 also starts the service's dependencies: every app service depends on the
  one-shot `migrate` (`service_completed_successfully`), which has no container on this stack
  (`compose run --rm`), so it failed with `missing dependency migrate` and, under `set -e`, died
  before the holds were lifted (proven on a throwaway busybox project shaped like the stack; the
  release's old "start the old backend again (docker compose start backend)" advice had the same
  cause). It now starts each app service's own containers with `docker start` (`start_existing`;
  one-offs of `compose run` skipped by their `oneoff` label; the backend first, the others after
  the holds are lifted), also as `scripts/staging.sh start-existing <service>...`, which the
  release's advice names; the same throwaway project proved `start-existing` starts them all and
  their names resolve again. The fake docker now resolves `compose start` dependencies as Compose
  does, so the test would fail on the old code. (2) With the database still single and its
  backend running, the advice also names `fleet release` then `fleet window --clear` (routed into
  that backend before the move); not when the backend is down or the database did not answer.
  (3) `db-roles --migrate`'s "no app container runs" check (and `release`'s D8 check) read
  `compose ps` fail-closed: an error refuses, never counts as "none run".

**Open:** the restore drill to a moment before the move failed only the first-hour fingerprint
rule (no moment existed after the first fingerprint and before the move in a fresh proof); the
deploy over SSH (`deploy.sh --release`, `--local-proof` on the VM) is covered by static tests only;
Postgres's own `trust` for the db container's socket and loopback (any process in that container
may log in as any role) is unchanged, as before (that container is the superuser's anyway).
