# P1-T27 Operator access record: design

> **Read with `attack.md` "Orchestrator decision" (2026-11-22): it changes this design and wins where they differ.**

Task: P1-T27 (Phase 1 closing review finding PR1-3, `docs/reviews/phase-1-closing-review.md`
lines 160-185). Status: design, 2026-11-22. Blocks go-live. No code is written by this document.

## 0. The problem in one paragraph

A platform operator (one of us, signed in to the server over SSH as the shared deploy user `ihr`,
which is in the `docker` group, or as the admin user, which has passwordless sudo) can read any
hospital's patient rows and files today and leave no record anywhere: `scripts/staging.sh manage
shell` runs in the `admin` one-off as the database owner with `logging: driver: none`; `pg_su` and
a dozen direct `compose exec -T db psql -U postgres` calls run SQL as the superuser; Postgres does
not log connections; the host has no auditd. The audit trail (ADR-010) records reads only through
the web request middleware; operator *writes* are caught by the row triggers but are attributed
to `system:manage:<command>`, not to a person. We are the hospitals' data processor (DPDP), CERT-In
expects privileged-access logs kept 180 days in India, and one operator with root is the insider
case.

## 1. Goal, non-goals, and the honest boundary

**Goal (testable):** after P1-T27,

- G1. Every `staging.sh` invocation that can reach patient rows, patient files, raw logs or a
  privileged database session writes an `ihr-operator:` **start** line before it does anything and
  an **end** line (outcome, exit code, seconds) when it stops, to the server's journal; Fluent Bit
  ships both to VictoriaLogs (180 days) and the daily archive (400 days). A missing operator name or
  (for the data tier) a missing reason refuses the command and writes a **refused** line.
- G2. Every database session opened as `ihr_owner`, `postgres` or any role that is not a known
  service role is logged by Postgres (`connection authorized` / `disconnection`), and an alert
  fires within 10 minutes when such a session was not opened by a recorded operator session or a
  reviewed service.
- G3. A data-tier one-off that opens Django (manage.py) writes one `operator` entry into the audit
  chain of each hospital in its declared scope, and refuses (exit 77) to open a hospital outside
  that scope; every write it makes is attributed to `operator:<name>` with the session and reason.
- G4. The host records every execution of `docker`, `nsenter`, `ctr`, `runc`, `journalctl` and
  every command run as root by a logged-in user (auditd), ships those records redacted, and alerts
  on `nsenter` use and on audit being stopped or its rules removed outside a bootstrap run.
- G5. ADR-010 states plainly what this does **not** cover (section 10).

**Non-goals:** no new subsystem (journal + Fluent Bit + VictoriaLogs + vmalert + ihr_audit only,
PR1-8); no new `staging.sh` subcommand (P1-T30 freezes them); no patient-level record of what an
operator saw (impossible for shell/SQL: section 6.5); no prevention against a root user (section 10).

**The honest boundary.** Membership of the `docker` group is root on the host. Anyone with it can
start a container without our wrappers, read Docker volumes, stop auditd, or edit Postgres's
settings and restart it. This design makes the *sanctioned* path record itself, makes the
*unsanctioned* path leave evidence in at least two independent places (auditd's record of the
`docker` call, Postgres's connection line), and makes tampering with those two places itself
alert. It does not make the unsanctioned path impossible.

## 2. Inventory: every way an operator reaches patient data or a privileged session today

Read from `scripts/staging.sh` (5,420 lines; dispatcher at L5290-5420), `deploy/staging/compose*.yaml`,
`deploy/host/bootstrap.sh`. Roles: `owner` = `ihr_owner` (owns every table, can disable triggers);
`postgres` = superuser; `app` = `ihr_app` (reads every hospital's rows; the web app's role);
`watch` = `ihr_audit_watch` (reads audit chains); `dump` = `ihr_dump` (`pg_read_all_data`).
"Logged today" = something reaches the log store naming the action.

| # | Path (file:line today) | Runs as | Patient rows/files? | Logged today |
|---|---|---|---|---|
| 1 | `staging.sh manage <args>` (L5294): `compose --profile admin run ... admin python manage.py` | owner, container `admin`, **logging none** | yes: `shell`, `shell_plus`, `dbshell`, any CARE command | no |
| 2 | `staging.sh hospital-root ...` (L5296), same admin one-off | owner, logging none | staff usernames; writes audited as `root:<op>` | writes only (audit trail) |
| 3 | `admin_manage` (L301): Keycloak sync, `keys`, `up` (L943), `release` (L2146), `db-roles --migrate` (L859) | owner, logging none, holds Keycloak bootstrap credential | staff, not patients | no |
| 4 | Raw admin one-offs: `up` terminology load (L932), `mail_check` (L3074), `local-proof` (L5413), `restore_admin` (L5033), `restore_drill` (L1342) | owner, logging none | terminology/none; drill: a copy of every hospital | no |
| 5 | `pg_su` (L402-408): `compose exec -T db psql -U postgres` or `docker exec $PG_TARGET psql` | **postgres** | yes (any SQL); callers pass fixed SQL | no |
| 6 | Direct `compose exec -T db psql -U postgres` outside `pg_su`: L1021, L1307, L1444 (`rel_sql`), L4125, L5234 | postgres | fixed SQL (slugs, sizes, release state) | no |
| 7 | Direct `docker exec <drill/recovery> psql -U postgres`: L1258, L1321-1322, L1414, L2438-2449, L5178-5182 (`pg_dump` of one hospital schema from the drill copy) | postgres on a restored copy | yes (L5182 dumps a hospital) | no |
| 8 | `restore-drill --keep` (L1344) leaves container `ihr-restore-drill` and prints `docker exec -it ihr-restore-drill psql -U postgres` | postgres on a full copy | yes, every hospital | no |
| 9 | `migrate` one-offs: `fleet` (L5318), `release` (L2135-2182), `restore_manage` (L5014), supersede load (L2312, 2336, 2418, 2567, 2587), `old_manage` after the role move (L1550), `up` (L853-856) | owner, container `migrate`, logged (journald) | fixed commands; restore loads a hospital | their own lines (`ihr-fleet:`, `ihr-restore:`), not who ran them |
| 10 | `compose exec -T backend python manage.py ...` (`old_manage` before the move L1554, `key_probe_in` L324, terminology status L1084-1085, smoke probes L4351-4496) and `compose exec -T -e IHR_DB_LIMITS=worker backend ... ihr_audit_verify` (L1611) | **app** (reads every hospital) | fixed commands | no |
| 11 | `compose run ... celery-worker` one-offs (L1647, L1664) | app | fixed (release audit checks) | container is logged |
| 12 | `audit-supersede` one-off (compose.yaml L315-339; staging.sh L1850, 2298, 2507, 2560, 2666) | watch, **logging none** (stdout is a tar of audit entries) | audit entries (masked values) | its load step only |
| 13 | `dumps-get <uuid> <day> dump` (L5378-5381) | dumper key (bucket), decrypted to the operator's stdout | **yes: one hospital's whole dump** | no |
| 14 | `logs-archive-get <day>` (L5366) | archive key, decrypted | redacted logs | no |
| 15 | `logs [service]` (L5342) = `compose logs`; and `journalctl` directly | Docker / journal | **raw, unredacted** lines (redaction happens only in Fluent Bit) | no |
| 16 | `hospital-restore` (L5110-5210) | owner (migrate) + admin one-off (Keycloak step) | loads a hospital | `ihr-restore:` lines + `restore` audit entry; operator name given (`--operator`) |
| 17 | `restore`, `rollback --restore` (whole database, L1383-1420, L2900-2932) | postgres in throwaway containers | whole database | release log + supersede records |
| 18 | `backup-now`, `backup-status` (L5358, L1225-1229): pgBackRest in `backup` | postgres (socket) | backup stream (encrypted repo) | container logged |
| 19 | `s3-init`, `s3-check`, `s3-split-keys` (`s3-tool` one-off, L3166) | storage keys | fixed scripts; no file is fetched to the terminal | container logged |
| 20 | `ops-tunnel` (Grafana, Prometheus, Alertmanager), Keycloak admin console over an SSH tunnel | SSH local forwarding | redacted logs, metrics; staff identities | Grafana's own log |
| 21 | **Bypass, not through staging.sh:** `docker exec <backend|celery|provisioner> python manage.py shell`, `docker compose run admin ...`, `docker exec db psql -U postgres`, `docker run -v indiehr-staging_pgdata:...`, `docker cp`, reading `/var/lib/docker/volumes` or SeaweedFS's volume as root, `nsenter`, `redis-cli` in `redis` | any, root-equivalent | yes | no |

Out of scope: `scripts/care-dev.sh` (dev only; no shared code with these paths); the deploying
machine's own commands (`db-split-secrets`, `s3-split-keys`, `dumps-expire`, `logs-archive-expire`
with `IHR_STAGING_OUT`): they hold keys, not a session to patient data on the server.

Facts this design relies on (checked):

- staging.sh runs **on the server** from `/srv/indiehr/scripts` (header L115-119); `deploy.sh`
  reaches it with `ssh ihr@<server> "cd /srv/indiehr && scripts/staging.sh ..."` (deploy.sh L378-399).
- Everyone signs in as `ihr` (shared, docker group) or the admin user (sudo) (bootstrap.sh L41-42,
  L884-895, README "Run it on a new server"). The SSH user therefore does **not** name a person.
- `rel_operator` (L1462) defaults to `id -un`, i.e. `ihr`: today's `--operator` names nobody.
- The admin one-off's `logging: driver: none` is on purpose (compose.yaml L279-281): it holds the
  Keycloak bootstrap credential and a shell's output may print patient data; logs are kept 180/400
  days. Same for `audit-supersede` (stdout is a tar of audit entries) and `keytool`.
- Every Django process already sets `application_name` = `process_name()` (`manage:<command>`,
  `gunicorn`, `celery`; `ihr_platform/settings/tenancy.py` L39-64; `drill.py` and `watch.py` set
  their own). Django's `CONN_MAX_AGE` is 0 (CARE base.py L74): one connection per request.
- Fluent Bit reads the whole journal; `shape()` (redact.lua L22-50) marks a line `source=container`
  only when journald says Docker wrote it (`_COMM=dockerd`, `_UID=0`), otherwise `source=host` with
  `service = SYSLOG_IDENTIFIER`. Every alert today reads `source:=container` only.
- The `db` service logs to journald with tag `indiehr/{{.Name}}` and label `com.docker.compose.service`
  (compose.yaml L21-27): Postgres's stderr arrives as `source=container service=db`. Throwaway
  Postgres containers (`pg_container`, L1240; the drill) use the daemon default (journald, tag
  `docker/{{.Name}}`, bootstrap.sh L189): `service=<container name>` (`ihr-restore-drill`,
  `ihr-restore-recovery`).

## 3. The operator record line (session start, end, refusal)

### 3.1 Where it is written

On the server, by `staging.sh`, with `logger -t ihr-operator -p user.notice -- "<line>"`
(util-linux `logger`; journald sets `SYSLOG_IDENTIFIER=ihr-operator` and the trusted `_UID`,
`_PID`). Fluent Bit ships it as `source=host service=ihr-operator`. Same mechanism as `key_log`
(L326-331) and the terminology lines (L934). In a local proof (`.local`) the line is written to this
machine's journal only when `IHR_LOG_DRIVER=journald` is in `.env` (as `compose.local-journal.yaml`
already decides for containers); otherwise it goes to stderr only. Every line is also echoed to the
operator's stderr.

**Fail closed:** if `logger` fails for a start line, the command is refused (`die "the operator
record could not be written to the journal: nothing was run"`). An end line that fails prints a
warning (the work is already done).

### 3.2 Format (exact; one line each; fields in this order; `key=value`, no spaces in values except the last field)

```
ihr-operator: session=<s> event=start tier=<data|job> operator=<name> command=<cmd> args=<shape> hospital=<scope> key=<fp> from=<addr> reason="<text>"
ihr-operator: session=<s> event=end outcome=<ok|failed|out-of-scope|interrupted> rc=<n> seconds=<n>
ihr-operator: event=refused why=<why> command=<cmd> operator=<name|none>
```

- `<s>`: 12 lower-case hex characters from `/dev/urandom` (`op_new_session`), new for every
  invocation. Never inherited from the environment (an exported `IHR_OPERATOR_SESSION` is ignored
  and overwritten), so a session id always has a start line.
- `<name>`: `^[A-Za-z0-9._@-]{1,64}$` (the existing `rel_operator` rule), see 3.4.
- `<cmd>`: the staging.sh subcommand; for `manage` also the manage.py command name when it matches
  `^[a-z_][a-z0-9_]{0,63}$` (`manage shell`, `manage dbshell`), else `manage <v>`.
- `<shape>`: the remaining arguments with **every value replaced**: a token matching
  `^--?[A-Za-z][A-Za-z0-9-]{0,40}$` is kept; `--opt=value` becomes `--opt=<v>`; anything else
  becomes `<v>`; joined with `,`; cut at 200 characters (`...` appended); `-` if none. Example:
  `manage shell -c "print(Patient.objects.get(phone='9876543210'))"` gives
  `command=manage shell args=-c,<v>`. **The body of `-c`, SQL text, slugs, uuids, file paths and
  dates are never recorded.** No hash of the argv either: a hash of short, guessable text (a phone
  number in a query) can be reversed by guessing. (Q-K-D2.)
- `<scope>`: `none` (job tier), `all`, or a comma list of slugs `^[a-z][a-z0-9-]{2,30}$` (3.5).
- `<fp>`: the SSH key that opened this login, `SHA256:<43 base64 chars>`, read from the file named
  by `SSH_USER_AUTH` (sshd `ExposeAuthInfo yes`, added by bootstrap: section 7) with
  `ssh-keygen -lf`; `none` when not over SSH (a timer, the console) or the file is absent. This is a
  hint written by a process the operator controls; the trusted copy is sshd's own journal line
  (`Accepted publickey for ihr from <addr> port <p> ssh2: ED25519 SHA256:...`), which `from=` lets
  a reader join.
- `<addr>`: first field of `SSH_CONNECTION` if it is an IPv4/IPv6 literal, else `local`.
- `reason`: last field, in double quotes, 8-160 characters of `[A-Za-z0-9 ._,:;#/()+-]` (no quote,
  backslash, `=`, newline or control character: refused, not escaped). For the job tier, when not
  given, `job:<cmd>` (e.g. `reason="job:release"`). The runbook says: a ticket or incident id and
  a few words; **never a patient's name or number**. The Fluent Bit masks still run on the line
  (a 10-digit mobile becomes `<phone>`, a 12-digit number `<aadhaar>`).
- `outcome`: `ok` (rc 0), `out-of-scope` (rc 77, section 6.3), `interrupted` (INT/TERM/HUP),
  `failed` (any other rc). `seconds` from bash `$SECONDS`.
- `why` (refused): `no-operator`, `shared-operator` (the name is `ihr`, `root` or the admin user's
  name), `unknown-operator`, `no-reason`, `bad-reason`, `no-hospital`, `bad-hospital`,
  `journal-unwritable` (stderr only; it cannot reach the journal by definition).

### 3.3 Tiers: which subcommands open a session

One table in staging.sh, `OP_TIER` (an associative array keyed by subcommand), consulted by a new
`main()` before dispatch. Static test: every `case` label in `main()` has an entry, and no other
value than `data`, `job`, `none` appears.

- **data** (operator, reason and `--hospital` required): `manage`, `hospital-root` (scope taken from
  its `--slug`), `dumps-get` (scope = the hospital that uuid names), `logs`, `logs-archive-get`,
  `restore-drill` with `--keep`, `hospital-restore` (scope = its slug; it already takes `--reason`).
- **job** (operator required; reason defaults to `job:<cmd>`): `up`, `smoke`, `fleet`, `release`,
  `rollback`, `restore`, `audit-supersede`, `db-roles`, `start-existing`, `keys`, `local-proof`,
  `restore-drill` (without `--keep`), `hospital-restore-drill`, `hospital-restore-status`,
  `hospital-restore-expire`, `backup-now`, `backup-status`, `backup-init`, `mail-check`, `mail-init`,
  `s3-init`, `s3-check`, `dumps-now`, `dumps-check`, `dumps-role`, `dumps-probe`, `dumps-drill-mark`,
  `logs-archive-now`, `logs-archive-check`, `logs-archive-probe`, `monitoring-init`, `alert-test`,
  `grafana-reset-admin`, `release-note`, `release-record`, `level-guard`, `acme-*`, `cert`,
  `logs-volume`.
- **none** (no session, and must reach no wrapper of 3.6): `init`, `ps`, `down`, `releases`,
  `db-split-secrets`, `watch-reader`, `s3-split-keys`, `s3-events-key`, `monitoring-textfile` (the
  every-minute host timer, bootstrap.sh L280-285), `monitoring-render`, `monitoring-deadman`,
  `alerts`, `ops-tunnel`, `dumps-init`, `logs-archive-init`, `logs-archive-expire`, `dumps-expire`,
  `acme-proof-run`. A `none` command that reaches a wrapper dies (`op_need`): the static test runs
  each `none` command's function under stubs to prove it.

The builder confirms each row by reading the subcommand's function (`graphify explain`); a row that
turns out to reach patient data moves up a tier, never down.

### 3.4 Who the operator is (Q-K-D1)

- Source, in order: `--operator NAME` (already accepted by release, rollback, restore, db-roles,
  hospital-restore, audit-supersede, hospital-root), else `IHR_OPERATOR`. **No default** for a
  session: `rel_operator`'s fallback to `id -un` stays only for `none` commands and is removed from
  the session path.
- Refused: missing (`no-operator`); `ihr`, `root`, or the admin user's name from
  `/etc/indiehr/admin-user` (`shared-operator`); a name not in `.env`'s `IHR_OPERATORS=` comma list
  (`unknown-operator`). `IHR_OPERATORS` is a site setting (no secret), written by `staging.sh init`
  from `--operators a,b` or added by hand, sent by `deploy.sh` with `.env`. An empty or absent list
  refuses every session with the message naming the setting (fail closed).
- `deploy.sh` passes its operator to every remote staging.sh call (`IHR_OPERATOR=<op>
  scripts/staging.sh ...`): it already builds `op` for releases (deploy.sh L381); it gets it from
  `--operator` or `IHR_OPERATOR` on the deploying machine and refuses without one.
- The bootstrap timer (`monitoring-textfile`) is tier `none` and needs no operator.

### 3.5 Scope (`--hospital`) for the data tier (Q-K-D3)

`--hospital <slug>[,<slug>...]` or `--hospital all`, a staging.sh-level option read before the
subcommand's own arguments (`staging.sh manage --hospital alpha --reason "INC-12 bed fix" shell`)
and never passed on to manage.py. staging.sh checks the shape only; the Django hook (6.2) checks the
slugs exist (unknown slug: exit 77, `outcome=out-of-scope`). Derived automatically for
`hospital-root` (`--slug`), `hospital-restore` (its slug), `dumps-get` (the uuid's slug, looked up
with `pg_su` once the session is open). `logs` and `logs-archive-get` take `all` implicitly (logs
are not per hospital).

### 3.6 Wrappers: every privileged call goes through one of five functions

New functions in staging.sh (names binding; the builder may move code but not rename them, the
tests grep for them):

- `op_begin <cmd> <args...>`: validates operator, reason, scope; makes the session; writes the start
  line; exports `OP_SESSION OP_NAME OP_REASON OP_SCOPE OP_TIER OP_SHAPE` (shell variables only).
- `op_end <rc>`: writes the end line. Called by the top level, never by a trap inside a function
  (staging.sh has 35 `trap ... EXIT` sites that replace each other). New top level:

  ```bash
  main() { case "${1:-}" in ... esac; }       # today's dispatcher, unchanged inside
  op_begin "$@"                               # no-op for tier none
  trap 'op_end 130; exit 130' INT; trap 'op_end 143; exit 143' TERM HUP
  rc=0; ( main "$@" ) || rc=$?
  op_end "$rc"; exit "$rc"
  ```

  `main` runs in a subshell so its `exit`, `die` and traps cannot skip `op_end`. (Static test:
  the last non-comment lines of staging.sh are exactly these.)
- `op_env`: prints the `-e` arguments every one-off gets: `IHR_OPERATOR_SESSION`, `IHR_OPERATOR`,
  `IHR_OPERATOR_REASON`, `IHR_OPERATOR_SCOPE`, `IHR_OPERATOR_TIER`, `IHR_OPERATOR_COMMAND` (the
  shape) and `PGAPPNAME=op:<s>:<cmd-word>` (for psql/dbshell/pg_dump, which do not read Django's
  settings). Values are passed by name (`-e VAR` with the variable exported for that one command),
  never on the command line, as `admin_manage` already does for its credential.
- `op_need`: dies with "this needs an operator session (scripts/staging.sh <tier table>)" when
  `OP_SESSION` is empty. Called first by every wrapper below.
- The wrappers themselves: `admin_run <args>` (every `--profile admin run ... admin`, including
  `admin_manage`, `restore_admin`, the `manage` and `hospital-root` branches, L932, L1342, L3074,
  L5413), `migrate_run <args>` (every `compose run ... migrate`, including `old_manage`,
  `restore_manage`, supersede load, fleet, release, up), `app_exec <service> <args>` (every
  `compose exec -T <app service>` and the celery-worker one-offs), `pg_su` (gains `op_env`'s
  `PGAPPNAME`; L1021, 1307, 1444, 4125, 5234 and every `docker exec <container> psql/pg_dump` of
  row 7 are rewritten to call it with `PG_TARGET`), and `audit_supersede_run` (row 12).
- Static test (`scripts/test_staging_operator.py`): outside these wrappers, staging.sh contains no
  `--profile admin run`, no `compose run` of `migrate`/`celery-worker`, no `compose exec -T`
  of `db|backend|celery-worker|celery-beat|provisioner|staff-worker`, and no `psql`/`pg_dump` word
  except inside `pg_su`.

**Reason for automation without a human.** Job-tier flows (release, rollback, restore, up, the
monthly drill) get `reason="job:<cmd>"` and keep their `--operator`; their inner calls
(`old_manage`, `restore_manage`, `admin_manage`) inherit the one session through the exported
variables. Nothing inside a flow asks for a reason again. `scripts/test_release_scripts.py` and the
`scripts/test_staging_*.py` suites stub `logger` and set `IHR_OPERATOR` and `IHR_OPERATORS` in their
environment; any test that calls a function directly (not through `main`) sets `OP_SESSION` to a
fixed value so `op_need` passes.

### 3.7 The admin one-off stays unlogged; its start and end are recorded

`admin` (and `audit-supersede`, `keytool`) keep `logging: driver: none`: their output may hold the
Keycloak bootstrap credential, patient data printed by a shell, or audit entries. Nothing inside the
container is shipped. The **start and end** are written by staging.sh around it (3.2), the database
session is written by Postgres (section 4) and the hospital entry by the Django hook (section 6).
The end line's `rc` and `seconds` are the one-off's (the session spans the whole staging.sh run;
for `manage` and `hospital-root` that is the one-off itself).

## 4. Postgres connection logging

### 4.1 What PG 18 offers (sources)

- `log_connections` in PG 18 takes a list: `receipt`, `authentication`, `authorization`,
  `setup_durations`, `all` (old booleans still accepted; `on` = the first three). `authorization`
  "Logs successful completion of authorization ... The log message includes the authorized
  username as well as the database name and application name". **Failed authentication is always
  logged**, whatever the setting. `log_disconnections` logs session end with its duration. Both:
  "Only superusers and users with the appropriate SET privilege can change this parameter at
  session start, and it cannot be changed at all within a session."
  <https://www.postgresql.org/docs/18/runtime-config-logging.html>
- **They cannot be set per role.** `ALTER ROLE x SET log_connections` fails or has no effect: the
  connection is logged before role and startup-packet options are applied (pgsql-admin thread
  "ALTER ROLE foo SET log_connections = true;",
  <https://www.postgresql.org/message-id/6788.1331903976%40sss.pgh.pa.us>; "Setting log_connection
  in connection string doesn't work",
  <https://www.postgresql.org/message-id/20211026.173350.2163793213857103911.horikyota.ntt%40gmail.com>).
  So the only way is server-wide, and the same timing means a superuser's `PGOPTIONS=-c
  log_connections=` cannot hide its own authorization line (**assumption A1**, proved by a test in
  the build: 9.4).
- Values given with `-c` on the server's command line outrank `postgresql.auto.conf`, so `ALTER
  SYSTEM` cannot switch them off short of changing compose.yaml and restarting the container
  (visible: a release or `up`, and `db_server_check` at every up and release).

### 4.2 Settings (Q-K-D4)

Add to `deploy/postgres/server-settings.txt` (both match the loader's `^[a-z_]+=[A-Za-z0-9]+$`, L987):

```
# Who opened a session (P1-T27, PR1-3): one line per authorized session (user, database,
# application name) and one at its end (duration). Server-wide: Postgres cannot log one role only.
log_connections=authorization
log_disconnections=on
```

and the same two `-c` lines to compose.yaml's `db` command, `deploy/dev/care-local.override.yaml`,
`deploy/ci/tenancy.override.yaml` and the two recovery starts (`PG_SERVER_SETTINGS` already
carries the file's lines to them; `test_staging_postgres.py::test_every_database_container_carries_every_line`
and `test_both_recovery_starts_carry_them_too` enforce it). `db_server_check` (L539) compares
`current_setting()` case-insensitively: `log_connections` reads back `authorization`,
`log_disconnections` reads back `on`; no code change, two new cases in
`test_staging_postgres.py::test_each_drift_is_named_and_refused`.

`log_line_prefix` stays the default (`'%m [%p] '`): the authorization message names the user,
database and application itself. `receipt` and `authentication` are left out (the client address
and auth method add nothing: everything connects over the internal network or the socket).

**Volume.** With `CONN_MAX_AGE=0`, every web request and Celery task opens one `ihr_app` session:
two Postgres lines per request, about the same as Traefik's one access line. Kept, not dropped in
Fluent Bit: dropping `ihr_app` lines by application name would let a deliberate `docker exec
backend env IHR_PROCESS_NAME=gunicorn python manage.py shell` vanish from the store. Measured with
`staging.sh logs-volume` in the local proof and at P1-T26; if it exceeds the store's sizing, the
fallback is Q-K-D4 option B.

### 4.3 Making a sanctioned session recognisable

- In `ihr_platform/settings/tenancy.py::apply_tenancy`, after `options.setdefault("application_name",
  process_name())`: when `IHR_OPERATOR_SESSION` matches `^[0-9a-f]{12}$`, the name becomes
  `op:<session>:<process_name()>`, cut to 63 characters. `process_name()` itself is **not** changed
  (`db_limits.derive` reads it: `graphify affected process_name` lists `db_limits.chosen` and its
  tests). `drill.py` and `watch.py` set their own names after `apply_tenancy`; they get the same
  prefix rule (one helper `operator_appname(base)` in tenancy.py, used by all three).
- psql, `dbshell` (Django's client does not pass `application_name`) and `pg_dump` get
  `PGAPPNAME=op:<s>:psql|dbshell|pg_dump` from `op_env`.
- The `db` healthcheck becomes `PGAPPNAME=ihr-health pg_isready -U postgres -d postgres` **if** the
  local proof shows `pg_isready` producing an authorization line (**assumption A2**: it may stop
  before authorization; the proof decides and the sanctioned list records the outcome).

### 4.4 The reviewed list of service logins

`deploy/postgres/service-logins.txt`: one line per sanctioned (role, application name pattern,
where, why). Built in the build from a live capture of the local proof (every `connection
authorized` line of a full `local-proof` + `release` + `hospital-restore-drill` run), reviewed, not
guessed. Expected at least: `ihr_owner` with `manage:ihr_db_role_check`, `manage:migrate_schemas`,
`manage:ihr_create_tenant`, `manage:ihr_fleet_run` (the start-up one-shot, compose.yaml L263-270),
`manage:ihr_provision_worker` (provisioner, L99); `ihr_app` with `gunicorn`, `celery`,
`manage:ihr_db_role_check`, `manage:ihr_provision_worker` (staff-worker), and what collectstatic and
compilemessages do; `ihr_audit_watch` with `ihr-audit-watch`; `ihr_dump` with the dumper's name;
`postgres` with `pgBackRest [backup]`-like names; the postgres-exporter's role; Keycloak's role;
`ihr_owner` with `ihr-restore-drill` on the drill container only. The alert (5.1) embeds this list;
`test_staging_operator.py` checks every line of the file appears in the rule and nothing else does.

## 5. Alerts (vmalert, `deploy/logs/alerts/operator.yml`, new file; tested by `scripts/check-logs.py`)

Shape copied from `root.yml`/`restore.yml`: `type: vlogs`, `interval: 1m`, an `extract_regexp`
anchored on the whole line, label values that are never put into the action's command.

### 5.1 `PrivilegedLoginUnrecorded` (critical)

Any `connection authorized` line from the database containers whose application name is not
`op:<12 hex>:...` **with a matching start line**, and whose (user, application) pair is not in
`service-logins.txt`.

```
_time:10m source:=container service:in(db, ihr-restore-drill, ihr-restore-recovery)
  "connection authorized: user="
| extract_regexp "LOG:  connection authorized: user=(?P<user>[a-z_][a-z0-9_]{0,62}) database=(?P<db>[a-z_][a-z0-9_]{0,62})(?: application_name=(?P<app>[ -~]{0,63}))?$"
| extract_regexp "^op:(?P<session>[0-9a-f]{12}):" from app
| filter not (session:in(_time:24h source:=host service:="ihr-operator" "event=start" | extract_regexp "^ihr-operator: session=(?P<session>[0-9a-f]{12}) event=start " | fields session))
| filter not (<one clause per service-logins.txt line: user:=X app:~"^Y$">)
| stats by (user, app) count() as logins
```

The exact Postgres 18 message text (`LOG:  connection authorized: user=... database=...
application_name=...`, possibly with SSL/GSS suffixes) is **assumption A3**: the build captures one
real line in the local proof and writes the case from it. If the pinned VictoriaLogs (v1.53.0)
refuses a subquery after `extract_regexp`, the fallback is the prefix check alone
(`app:~"^op:[0-9a-f]{12}:"`) and the limit is written down; check-logs.py decides.

Why no "release or restore window": releases, rollbacks and restores run through staging.sh and so
are recorded sessions; their owner sessions carry `op:` names. The start-up one-shot and the
provisioner are listed services. Nothing legitimate is left that needs a time window.

Action text: "A database session as <user> (<app>) was opened without a recorded operator session.
Check `source:=audit` docker/nsenter records of the last 10 minutes and the sshd logins; if no
operator owns it, treat as an incident (deploy/staging/README.md, Operator access)."

### 5.2 `OperatorSessionRefused` (warning)

`_time:1h source:=host service:="ihr-operator" "event=refused"`, `stats by (why) count()`. Fires on
unknown or shared operator names, missing reasons, bad scopes. (The task's "operator lines from an
unknown operator": staging.sh refuses an unknown name, so the case reaches the store as a refused
line.)

### 5.3 `OperatorOutOfScope` (warning)

`event=end outcome=out-of-scope` in 24 h: a one-off tried to open a hospital outside its declared
scope (6.3); the hospital's own trail holds a `refused` entry.

### 5.4 Host audit alerts (in P1-T27c)

- `HostNsenter` (critical): any `source:=audit` EXECVE record with key `ihr-nsenter` (our scripts
  never call nsenter; `grep -rn nsenter scripts deploy` is empty today; a static test keeps it so).
- `HostAuditTampered` (critical): a `CONFIG_CHANGE` record that sets `audit_enabled=0`, or removes a
  rule with an `ihr-*` key, or a `DAEMON_END`/`DAEMON_ABORT`, in a 10-minute window **without** an
  `ihr-bootstrap: audit-rules=loaded` host line in the same window:
  `stats count() if (source:=audit ...) as changes, count() if (source:=host service:="ihr-bootstrap" "audit-rules=loaded") as loads | filter changes:>0 loads:=0`
  (LogsQL "stats with additional filters",
  <https://docs.victoriametrics.com/victorialogs/logsql/>). The bootstrap writes that marker line
  every time it loads the rules (every boot, via the provider start script).
- `HostAuditSilent` (warning): no `source:=audit` record at all for 2 hours on a host that sent
  `ihr-operator` start lines in that time (shipping broken or audit off).

### 5.5 Forgery

Any process on the host can write a line with `SYSLOG_IDENTIFIER=ihr-operator`. A forged **start**
line can at most make one forged `op:` session look sanctioned; the forger must then also choose
the application name, i.e. act deliberately, and the `docker` call itself is in the audit records.
To let a reader judge a line, `shape()` now keeps the trusted `_UID` of host lines as `uid`
(redact.lua, P1-T27a): the deploy user's and admin's uids are known per server. Container and audit
lines cannot be forged by a host process (journald sets `_COMM`, `_TRANSPORT`).

## 6. Per-hospital audit event when a one-off opens a hospital

### 6.1 What "opens" means

A Django one-off "opens" a hospital when it switches its connection to that hospital's schema:
`connection.set_tenant(...)`, which `schema_context`, `tenant_context` and `set_schema` all reach
(django-tenants; **assumption A4**: in the pinned django-tenants, `set_schema` and `set_tenant`
both go through `DatabaseWrapper.set_tenant`; the builder confirms in the installed package before
patching, it is not in our graph). Our `TenantMainMiddleware` does not run in a management
command, so the hook is installed at command start instead.

### 6.2 The hook: `plugins/ihr_platform/ihr_audit/operator.py` (new)

Installed from `IhrAuditConfig.ready()` (ihr_audit/apps.py L11) only when `IHR_OPERATOR_SESSION`
is set; otherwise it does nothing (web, workers, tests unaffected). When set, it validates all
`IHR_OPERATOR_*` values (same rules as 3.2; bad value: `ImproperlyConfigured` before any database
access) and:

1. **Attributes every write.** Puts `ctx = {"via": "operator", "session": s, "tier": t, "reason":
   r, "command": shape, "scope": scope}` and actor `operator:<name>` into `ihr_audit.context`'s
   current values (`_current.set`, no database access in `ready()`); the existing
   `connection_created` receiver (`context.reapply`) puts them on every connection. A command that
   uses `context.job(...)` (e.g. `roots.py`'s `root:<operator>`) overrides and restores them as today.
2. **Records the opening, data tier only.** Wraps `BaseCommand.execute` (so `dbshell`, which never
   switches schema, is covered): before the command body runs, for each hospital in scope (`all` =
   every hospital whose status is in `fleet.MIGRATE_STATUSES`), inserts one entry into
   `<schema>.ihr_audit_entry`, on a **separate autocommit connection**
   (`django.db.connections.create_connection("default")`, closed after) so a later rollback in the
   command cannot remove it:
   `kind='operator', actor='operator:<name>', context=<ctx>, tbl='', op='', row_pk=NULL,
   patients='{}', data={"event": "opened"}` (column order as `supersede.py` L884 inserts a
   `restore` entry). Unknown slug in scope: exit 77 before anything else. Any insert error: the
   command is refused (raise `CommandError("the operator record could not be written ...")`), fail
   closed.
3. **Bounds the Django path.** Wraps `DatabaseWrapper.set_tenant`: switching to a hospital schema
   not in scope (scope `all` allows every one; `public` always allowed) first inserts a
   `data={"event": "refused"}` entry in **that** hospital (separate connection), then raises
   `SystemExit(77)`. staging.sh maps rc 77 to `outcome=out-of-scope` (5.3).
4. Job tier: (1) and (3) with scope `all`; no (2) entries. Release, restore and fleet runs already
   leave their own records (`restore` entry, `ihr-fleet:`/`ihr-restore:` lines) and would otherwise
   put hundreds of entries in every hospital per release.

Why entries for the whole declared scope up front, not only for schemas actually switched to: a
shell or dbshell can read any schema with raw SQL (`SELECT ... FROM h_xxxx.emr_patient`) without
`set_tenant`. The entry therefore means "an operator session had access to your hospital at T for
reason R", which is true, and not "the operator looked at these patients", which we cannot know.
Declaring `all` puts an entry in every hospital: the honest record, and a reason to declare one.

### 6.3 Chain, seal and watch

- Migration `ihr_audit/migrations/0007_operator_entries.py`: the kind check gains `'operator'`,
  exactly as 0006 added `'restore'` (`DROP CONSTRAINT IF EXISTS ... ADD ... NOT VALID; VALIDATE`),
  run per hospital schema by migrate_schemas. Nothing else in the chain changes: entries are sealed
  by the minute beat job like any other, verified by `ihr_audit_verify` and the audit watch.
- Who may insert: the admin and migrate one-offs are the owner (allowed since 0004). Job-tier
  `app_exec` one-offs (ihr_app) never write `operator` entries (job tier: no entries). If the
  builder finds a trigger or grant limiting which kinds `ihr_app` may insert, nothing changes here.
- The viewer: `ihr_audit/views.py::KINDS` (L39) gains `operator`; the audit page
  (`apps/frontend/src/components/ihr/IhrAuditTrail.tsx`, rules `Utils/ihr/audit.ts`) shows it as
  "Platform operator access" with session, reason and scope (Q-K-D6). Text in
  `deploy/locale/en.json` (`ihr_` keys) and `hi.json`.
- The watch, export and verifier: `graphify affected` on `KINDS`, `export.py`'s kind cases (L201)
  and `verify_export.py` before changing; an unknown kind must not break any of them (test).

### 6.4 Paths that bypass Django

- `pg_su` and every psql/pg_dump call (rows 5-8): the start/end line, Postgres's connection and
  disconnection lines with `op:<s>:psql`, and no per-hospital entry (they are job-tier, fixed SQL)
  except row 7's `pg_dump -n <schema>` of one hospital from the drill copy, whose subcommand
  (`hospital-restore`) already writes a `restore` entry.
- `dumps-get` (row 13): staging.sh first runs `admin_run python manage.py ihr_operator_session`
  (a new 10-line management command in ihr_audit that does nothing; its only effect is the hook's
  "opened" entry for the scope) and only then fetches the dump. `restore-drill --keep` (row 8):
  scope `all`, the same note command against the live database before the copy is kept.
- `logs`, `logs-archive-get`: no hospital is named; the start/end line is the record.

### 6.5 What the hospital can now be told

"Who looked at my record?": for staff, per patient, as today (request entries). For platform
operators: per hospital and time, with the operator's name, reason and session id, from the
hospital's own chain; the session's command shape and duration from the log store (180 days) and
archive (400 days). Not per patient. ADR-010 says so (section 10).

## 7. auditd on the host (P1-T27c)

Files only; applying them needs sudo on a server, so the live proof waits for P1-T26 (or the owner
running `scripts/local-vm.sh`).

- `bootstrap.sh`: `auditd` joins `BASE_PACKAGES` (Ubuntu archive, unpinned like the others; listed
  in docs/VERSIONS.md as "Ubuntu archive"); writes `/etc/audit/rules.d/60-indiehr.rules` (mode
  640) and loads it with `augenrules --load` only when the file changed, then
  `logger -t ihr-bootstrap "audit-rules=loaded"`; ensures `systemd-journald-audit.socket` is
  enabled and active (journald's collection of audit records; `journald.conf` `Audit=` defaults to
  yes in the default namespace since systemd 246,
  <https://www.freedesktop.org/software/systemd/man/latest/journald.conf.html>); adds
  `ExposeAuthInfo yes` to the sshd drop-in (`sshd_conf`, L157) and `[exposeauthinfo]=yes` to
  `SSHD_EXPECT` (L180).
- Rules (`-k` keys are what Fluent Bit and the alerts select on):

  ```
  ## IndieHR (P1-T27c): who ran what with root-equivalent tools. Managed by bootstrap.sh.
  -w /usr/bin/docker -p x -k ihr-docker
  -w /usr/bin/ctr -p x -k ihr-docker
  -w /usr/bin/runc -p x -k ihr-docker
  -w /usr/bin/nsenter -p x -k ihr-nsenter
  -w /usr/bin/journalctl -p x -k ihr-journal
  -w /usr/bin/psql -p x -k ihr-psql
  -w /var/run/docker.sock -p rwa -k ihr-docker-sock
  -a always,exit -F arch=b64 -S execve -F euid=0 -F auid>=1000 -F auid!=unset -k ihr-root-exec
  -w /etc/audit/ -p wa -k ihr-audit-config
  -w /etc/docker/daemon.json -p wa -k ihr-docker-config
  ```

  `auid` is the login uid set at SSH login (pam_loginuid); it survives `sudo` and is what ties a
  record to the SSH session (`ses=`). Processes Docker starts inside containers have no auid, so
  only the operator's own CLI calls are recorded, not the stack's. Whether a watch on
  `docker.sock` records API calls that do not use the CLI is **assumption A5** (CIS Docker
  Benchmark lists it; the effect on `connect()` is to be shown live in P1-T26); if not, the limit
  is written down. `-e 2` (immutable rules): not set (Q-K-D7); `HostAuditTampered` (5.4) instead.
- `bootstrap.sh --check`: the rules file's content, `auditctl -l` containing every `-k ihr-*`
  key, `systemctl is-active auditd systemd-journald-audit.socket`, and a canary
  (`auditctl -m "ihr-audit-canary <random>"` then `journalctl _TRANSPORT=audit -g <random>`).
- Shipping and redaction (`redact.lua::shape`, P1-T27c): a record with `_TRANSPORT == "audit"`
  becomes `source="audit"`, `service="audit"`, `audit_type=<_AUDIT_TYPE_NAME>`; kept only for types
  `SYSCALL`, `EXECVE`, `CONFIG_CHANGE`, `DAEMON_START`, `DAEMON_END`, `DAEMON_ABORT`, `USER_LOGIN`,
  `USER_START`, `USER_END`, `USER_CMD`; everything else (`PROCTITLE`: the whole command line in
  hex; `PATH`, `CWD`, `SOCKADDR`) is dropped (`return -1`). `EXECVE` is rebuilt from an allow-list:
  `argc`, `a0` and `a1` only (hex-encoded values decoded, cut to 64 characters), plus
  `more=<argc-2>`; so `psql -c "SELECT ... '9876543210'"` keeps `a0=psql a1=-c more=1` and never the
  SQL. `SYSCALL` keeps `auid uid euid ses tty comm exe key success` only. Then the normal masks run.
  The journald field names (`_AUDIT_TYPE_NAME`, the MESSAGE layout) are **assumption A6**: the
  redaction cases are written from the systemd and audit documentation and re-checked against real
  records in P1-T26; a record that does not match the allow-list is dropped, never stored raw.
- `/var/log/audit/audit.log` stays local (root-only, auditd's own rotation): Fluent Bit does not
  read it (it runs as nobody with the journal group; the journal copy is the shipped one).

## 8. Fluent Bit and redaction cases (written first, as CLAUDE.md requires)

New cases in `deploy/logs/tests/redaction-cases.json` before any code (each with `forbid`):

1. An `ihr-operator` start line, host source: stored unchanged, `source=host`,
   `service=ihr-operator`, `uid` kept.
2. A start line whose reason carries `9876543210` and `token=abc`: `<phone>`, `token=<redacted>`.
3. A start line for `manage shell` built by `op_shape` from an argv carrying a phone number and an
   Aadhaar-like number: the shape test in staging tests proves neither reaches the line; this case
   proves the masks even if they did.
4. Postgres `connection authorized` from `service=db` with `application_name=op:0123456789ab:manage:shell`:
   unchanged. One with an e-mail address as application name: `<email>`.
5. `disconnection: session time: ... user=ihr_owner database=ihr host=...`: unchanged.
6. (P1-T27c) audit `EXECVE argc=4 a0="psql" a1="-c" a2="SELECT * FROM emr_patient WHERE phone='9876543210'" a3=...`:
   stored as `a0=psql a1=-c more=2`; `forbid: ["9876543210", "emr_patient"]`.
7. (P1-T27c) audit `PROCTITLE proctitle=<hex>`: dropped.
8. (P1-T27c) audit `SYSCALL ... key="ihr-docker"`: only the allow-listed fields.
9. A host process forging `CONTAINER_NAME=x COM_DOCKER_COMPOSE_SERVICE=db` with a `connection
   authorized` text: stays `source=host` (existing rule, new case for this text).

Alert cases in `deploy/logs/tests/alert-cases.json`: 5.1 fires for an owner login with app
`manage:shell`; fires for `op:<s>:manage:shell` with no start line; stays silent with a matching
start line, and for every `service-logins.txt` pair; fires for `postgres` with app `psql`; does
not fire for a forged host line. 5.2, 5.3 one each. 5.4 (P1-T27c): nsenter fires; a rule removal
with a bootstrap marker in the window stays silent, without one fires.

## 9. Tests and proof

### 9.1 Static and unit (no Docker)

- `scripts/test_staging_operator.py` (new; pattern of `test_staging_db_roles.py`: `func()` extracts
  a function, runs it under bash with stubs for `logger`, `docker`, `compose`):
  the tier table covers every dispatcher label; the top-level block (3.6); no privileged call
  outside the wrappers; `op_shape` never emits a value (property test over 200 generated argvs
  with phone numbers, slugs, SQL, paths); `op_begin` refusals (each `why`), with the exact refused
  line; start/end lines exact; `logger` failing refuses the run; `main` exiting through `die`,
  `exit 3` and a trap still produces the end line with the right rc; rc 77 gives
  `outcome=out-of-scope`; an exported `IHR_OPERATOR_SESSION` is not reused; `op_env` passes values
  by name only; `deploy.sh` passes `IHR_OPERATOR` to every remote staging.sh call and refuses
  without one; the alert's sanctioned clauses equal `service-logins.txt`.
- `scripts/test_staging_postgres.py`: the two new server lines everywhere; drift cases.
- `scripts/test_release_scripts.py`, `test_staging_*.py`: updated environment (stub `logger`,
  `IHR_OPERATOR`, `IHR_OPERATORS`), behaviour unchanged.
- `scripts/test_bootstrap_static.py` (P1-T27c): the rules text, `auditd` in packages,
  `ExposeAuthInfo`, `--check` probes, the marker line, no `nsenter` anywhere in scripts/deploy.

### 9.2 Django (tenant labels: add `ihr_audit`)

`ihr_audit.tests.test_operator` (new):
- no `IHR_OPERATOR_SESSION`: nothing installed (web and worker unaffected);
- data tier, scope `alpha`: one `operator` entry in alpha only, written even when the command then
  raises inside `transaction.atomic`; its context and actor exact; a write the command makes is
  attributed `operator:<name>` with the session;
- `set_tenant` to beta under scope `alpha`: a `refused` entry in beta, `SystemExit(77)`;
- scope `all`: one entry per live hospital; unknown slug: exit 77, no entry anywhere;
- the insert failing: the command is refused (fail closed);
- job tier: attribution only, no entries;
- `apply_tenancy` with the session set gives `op:<s>:<process>`; `db_limits.chosen` unchanged;
- migration 0007: the kind check accepts `operator`; the chain seals and verifies with it; the
  viewer lists it and accepts `kind=operator`; export and the standalone verifier accept it.

### 9.3 Mutation rows (`scripts/isolation-mutation.sh`; each must make the named test FAIL)

- `operator-entry-separate-connection`: insert on `connection` instead of the separate one -> the
  rollback test fails.
- `operator-scope-guard`: the scope check returns True -> the beta refusal test fails.
- `operator-fail-closed`: the insert error is swallowed -> the fail-closed test fails.
- `operator-appname`: `apply_tenancy` drops the `op:` prefix -> the appname test fails.
(Shell controls are guarded by the static tests above, as for P1-T19b.)

### 9.4 Live proof on this machine (`scripts/staging.sh local-proof`, dev stack stopped, `.env` with
`IHR_LOG_DRIVER=journald`, monitoring `--test`)

1. `IHR_OPERATOR=proof IHR_OPERATORS=proof scripts/staging.sh manage --hospital alpha --reason
   "P1-T27 proof run" ihr_operator_session`: start and end lines in VictoriaLogs; the owner login
   with `op:` name; alpha's chain has one `operator` entry; `ihr_audit_verify --all` passes.
2. The same without `--reason`, with `IHR_OPERATOR=ihr`, and with `--hospital nosuch`: refused
   lines; `OperatorSessionRefused` and `OperatorOutOfScope` fire on the mail stand-in.
3. `docker compose ... exec db psql -U postgres -c 'select 1'` by hand (no staging.sh):
   `PrivilegedLoginUnrecorded` fires within 10 minutes. Then `PGOPTIONS='-c log_connections='` on
   the same: still logged (A1).
4. A full `release` (job tier) and `hospital-restore-drill`: no alert; capture every authorization
   line into `service-logins.txt` (A2, A3).
5. `logs-volume` before and after: the added lines per day, written into the README.
Waits for P1-T26 (a VM with sudo): auditd rules loaded, the canary, journald audit collection,
the audit redaction cases against real records (A5, A6), `HostNsenter` and `HostAuditTampered`.

## 10. Documents to change

- **ADR-010** (`docs/adr/010-audit-trail.md`): new "Amendment (P1-T27, 2026-11-..): platform operator
  access" (sections 3-7 in short: session lines, Postgres logging, `operator` entries, auditd) and
  the "What this does not protect against (yet)" list (L167-171) gains:
  - **What a platform operator saw.** Operator entries say a session had access to the hospital,
    with name, reason and time; not which patients were read. Shell and SQL sessions cannot be
    narrowed to rows.
  - **A host root user or a member of the `docker` group** can bypass every wrapper (start an
    unrecorded container, read volumes, stop auditd, restart Postgres with logging off). Such
    access leaves the `docker`/`nsenter` audit records and Postgres's connection line, and switching
    either off alerts; it is not prevented. Two-person control of root is a production decision
    (ADR-020 / P1-T28).
  - **Raw SQL inside a recorded one-off** can reach hospitals outside its declared scope; the scope
    bounds Django's tenant switch only, and the up-front entries cover the declared scope only.
  - **Operator names are declared**, checked against a site list and joined to the SSH key by
    sshd's own line; the deploy account itself is shared.
- **`deploy/staging/README.md`**: new section "Operator access (P1-T27)": the tiers, the line
  format, `IHR_OPERATOR`/`IHR_OPERATORS`, `--reason` and `--hospital` with examples, what never
  goes in a reason, the alerts and what to do, the known limits; the hospital-root note at L1775-1778
  is rewritten (its start and end now reach the store); `logs-volume` numbers.
- **`deploy/host/README.md`**: auditd, `ExposeAuthInfo`, the canary.
- **`CLAUDE.md`**: one line under Staging: "Operator access (P1-T27): every privileged
  `staging.sh` run needs `IHR_OPERATOR` (listed in `.env` `IHR_OPERATORS`), data-tier commands
  `--reason` and `--hospital`; lines `ihr-operator: session=...`; Postgres logs every session;
  one-offs write `operator` audit entries (`ihr_audit/operator.py`); alerts in
  `deploy/logs/alerts/operator.yml`."
- **`docs/TRACKER.md`**: P1-T27 rows (and the three subtasks).
- **`docs/VERSIONS.md`**: auditd (Ubuntu archive).
- `docs/UPSTREAM_PATCHES.md`: only if the audit page change lands in an upstream file (our
  `components/ihr/` files are ours).

## 11. Split (the build is about 8 agent hours; three subtasks)

| Sub | Content | Done when | Size |
|---|---|---|---|
| **P1-T27a** Session record + Postgres | redaction and alert cases first; `op_*` functions, tier table, wrappers and refactor of every row 1-15 call site; `IHR_OPERATORS`, deploy.sh; `log_connections`/`log_disconnections`; `apply_tenancy` `op:` name; `service-logins.txt`; `operator.yml` 5.1-5.3; `shape()` keeps host `uid`; tests 9.1, the Postgres part of 9.4 | `test_staging_operator.py`, `test_staging_postgres.py`, release and staging suites green; check-logs.py green with the new cases; local proof steps 2-5 | about 3.5 h, Opus high (security) |
| **P1-T27b** Operator entries | `ihr_audit/operator.py`, `ihr_operator_session`, migration 0007, viewer kind and label, locale; Django tests 9.2; mutation rows 9.3; ADR-010 amendment and limits; README section | tenant tests with `ihr_audit` green; `isolation-mutation.sh check` and the four rows FAIL as named; local proof step 1 | about 2.5 h, Opus high (audit) |
| **P1-T27c** Host audit | bootstrap rules, packages, sshd `ExposeAuthInfo`, `--check`; `shape()` audit branch; redaction cases 6-8; alerts 5.4; static tests; host README | `test_bootstrap_static.py` and check-logs.py green; live proof recorded as waiting for P1-T26 | about 2 h, Opus high |

Order: a, then b (b's one-offs need a's session variables), c independent of b. Each subtask runs
the full review shape (security/audit). P1-T27 as a whole stays 🟡 until the P1-T26 live part.

## 12. Assumptions to prove in the build (each with its test)

A1 a superuser's `PGOPTIONS` cannot suppress its own authorization line (9.4 step 3). A2 whether
`pg_isready` logs an authorization (9.4 step 4). A3 the exact PG 18.6 message text (captured, then
the alert case). A4 django-tenants routes `set_schema` through `set_tenant` (read the installed
package; test 9.2 covers `schema_context`). A5 a `docker.sock` watch records API use without the
CLI (P1-T26). A6 journald's audit field names and EXECVE layout (P1-T26). A7 the pinned
VictoriaLogs accepts a subquery filter after `extract_regexp` (check-logs.py; fallback in 5.1).

## 13. Open decisions

**D1 Who the operator is.** (A) Declared name (`--operator`/`IHR_OPERATOR`), refused unless listed
in `.env` `IHR_OPERATORS` and not the shared account, plus the SSH key fingerprint recorded
(`ExposeAuthInfo`) and joinable to sshd's line. (B) A personal Unix account per operator on every
server (sshd's user then names the person; bootstrap grows user management and per-user docker
group membership). (C) Declared name only. **Recommend A**: real names with no bootstrap user
management; B is the production answer once there is more than one operator (record in P1-T28's
environments ADR).

**D2 What of the command is recorded.** (A) Subcommand, manage command name and option names, every
value replaced. (B) A plus a SHA-256 of the full argv. (C) The full command, redacted by Fluent Bit.
**Recommend A**: C leaks patient data in `-c` bodies past what masks catch (names); B's hash of
short text is reversible by guessing.

**D3 Scope and `all`.** (A) `--hospital` required for data-tier commands; `all` writes an
`operator` entry into every live hospital. (B) `all` writes no hospital entries (log store only).
(C) No scope option; entries only for schemas actually switched to. **Recommend A**: the hospital's
trail tells the truth (a shell with `all` could read it), and declaring one hospital is the cheap
path; C misses raw SQL and dbshell entirely.

**D4 Postgres connection lines.** (A) `log_connections=authorization` + `log_disconnections=on`,
every line kept. (B) The same, but Fluent Bit drops `ihr_app` lines from `gunicorn`/`celery`.
(C) Only a login event trigger for owner/postgres (PG 17+ `ON login`), no server setting.
**Recommend A**, measured in the proof and at P1-T26; B hides a deliberately renamed `docker exec
backend` shell; C is skipped by a superuser (`event_triggers=off`) and a broken trigger locks
everyone out.

**D5 `manage dbshell`.** (A) Allowed as a data-tier command (scope, reason, `PGAPPNAME`, up-front
entries). (B) Refused by staging.sh; only fixed SQL through job-tier commands. **Recommend A**: an
incident needs it, and it is now recorded; B only moves people to the unrecorded `docker exec` path.

**D6 Operator entries on the hospital's audit page.** (A) Now, in P1-T27b (kind filter and label).
(B) Later with the DPA work (P4-T6). **Recommend A**: the hospital is the controller and can then
see its processor's access; it is a filter value and a label.

**D7 Immutable audit rules (`-e 2`).** (A) No; alert on rule removal and audit stopped outside a
bootstrap run (5.4). (B) Yes; rule changes then need a reboot, and the every-boot bootstrap must
load rules before `-e 2`. **Recommend A** for Phase 1 (bootstrap must stay idempotent and
re-runnable); revisit for production.

**D8 Raw logs.** (A) `staging.sh logs` and `logs-archive-get` are data tier (reason required), and
`journalctl` is in the auditd rules. (B) Leave them unrecorded (the journal is root's anyway).
**Recommend A**: the raw journal holds unredacted lines (redaction happens only in Fluent Bit).

**D9 When the record cannot be written.** (A) Refuse the command (journal unwritable, hospital
entry not inserted). (B) Warn and run. **Recommend A** (fail closed, as every other control here);
an emergency path is a root user with `docker`, which is itself audited.

**D10 Root commands by the admin user.** (A) Record every execve with euid 0 by a logged-in user
(`ihr-root-exec`, program and first argument only). (B) docker/nsenter/ctr/runc only.
**Recommend A**: `sudo -i` otherwise leaves only sudo's own first line; volume is small (only
interactive admin work).

**D11 Severity.** `PrivilegedLoginUnrecorded`, `HostNsenter`, `HostAuditTampered` critical (mail at
once); the rest warning. **Recommend** as stated; P1-T30's alert tiers may regroup them.

Note for the orchestrator: `docs/QUESTIONS.md` already holds Q-K132 and Q-K133 (P1-T28), so these
are Q-K134 onward.
