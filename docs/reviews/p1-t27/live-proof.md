# P1-T27 live proof (local staging stack, 2026-11-23)

Commit under test: `0dd8f9363` (main). Machine: 6.7 GB, dev stack stopped for the run and restored after.
Operator `proof.claude`, reasons "P1-T27 live proof" (a few add a suffix such as "create beta").
Stack: `scripts/staging.sh local-proof --operator proof.claude` with `.env` set to `IHR_LOG_DRIVER=journald`
(init was run first with `IHR_BACKEND_IMAGE=indiehr-backend:t27proof`, an image built from `git archive HEAD`,
because `indiehr-backend:staging` was 8 days old and predates T27; the proof tag was removed afterwards).
`local-proof` brought up the whole stack (db, redis, s3, keycloak, backend, celery, workers, traefik, backup,
dumper, monitoring, Fluent Bit, VictoriaLogs v1.53, vmalert, Prometheus, Grafana, logs archive): 317 s,
smoke all ok. Memory with the stack up: 5.3-5.9 GB used, 0.4-1.1 GB available (tight but held).
Hospitals: `alpha` (made by local-proof), `beta` and `gamma` (made through `manage ... ihr_create_tenant`).
Web part: not exercised (not needed for T27; a bundle exists in `deploy/dev/frontend-build`).
Graphify calls made: 5 (`explain` local_proof (no exact match), op_begin, op_write, op_scope_open, pg_su).

## Result per step

| # | Step | Result |
|---|---|---|
| 1 | data-tier command writes start, scope, end lines; scope shows `opened:<slug>` | PASS |
| 2 | one sealed `operator` entry in the hospital's trail; `ihr_audit_verify` exit 0 | PASS (with a note on the wrapper's exit code) |
| 3 | `--hospital all` writes one entry per live hospital, fixed generic reason | PASS |
| 4 | refusals: `no-reason`, `shared-operator` (and `no-operator`): nothing ran | PASS |
| 5 | PG 18.6 authorization line carries `op:<session>:...`; unrecorded `psql` line | PASS (text matches the alert's regex) |
| 6 | PrivilegedLoginUnrecorded fires for unrecorded, not for recorded | PASS for the rule's logic; 4 findings on the reviewed service-login list (F1-F4) |
| 7 | `postgresql.auto.conf` metric 0 -> 1 -> 0, PostgresSettingOverridden | PASS |
| 8 | measurements | done (below) |
| 9 | break-glass | skipped as instructed |

## 1. Session lines (data tier, `manage`)

```
$ scripts/staging.sh manage --operator proof.claude --reason "P1-T27 live proof" --hospital alpha ihr_hospital_profile --slug alpha
$ journalctl -t ihr-operator --since ... -o cat
ihr-operator: session=69762adbb568 event=start tier=data operator=proof.claude command=manage:ihr_hospital_profile args=--slug,<v> hospital=alpha ses=3 from=local reason="P1-T27 live proof"
ihr-operator: session=69762adbb568 event=scope hospital=alpha hospital_entry=opened:alpha
ihr-operator: session=69762adbb568 event=end outcome=ok rc=0 seconds=6
```
The tag is `ihr-operator` (`op_write`, scripts/staging.sh:427). The slug never reaches `args=` (`<v>`).

## 2. Entry in the hospital's trail

Read through the recorded wrapper (`manage ... shell -c`, schema of alpha is `h_eernp2115z`; `dbshell` does not work
in the admin image: "You appear not to have the 'psql' program installed", see F6):
```
(852, ..., 'operator', 'operator:proof.claude', '', '', '{"via": "operator", "tier": "data", "scope": "alpha", "reason": "P1-T27 live proof", "session": "69762adbb568"}', '{"event": "opened"}')
```
One row per session, six sessions on alpha gave six rows (852, 854-858). The beat sealer had them sealed:
```
$ docker exec indiehr-staging-celery-worker-1 python manage.py ihr_audit_verify --all --seal --anchor
fingerprint written: anchors/app.ihr-proof.in/2026/11/22/20261122T210253Z-7dd991ca.json
alpha: 5 seals, 855 entries sealed, 0 not yet, head aba3328fc388cc9f
$ docker exec indiehr-staging-backend-1 python manage.py ihr_audit_verify --slug alpha    # rc=0
1 outside fingerprints read
alpha: 5 seals, 855 entries sealed, 0 not yet, head aba3328fc388cc9f
audit trail OK
```
Note: through the wrapper (`manage ... ihr_audit_verify --slug alpha`) the same check printed
`alpha: 4 seals, 854 entries sealed, 0 not yet` but **exited 1**, only because the admin one-off holds no fingerprint
bucket key ("outside fingerprints not checked: IHR_AUDIT_ANCHOR_BUCKET and _READ_KEY/_SECRET are not set",
`ihr_audit_verify.py:108-113`). Exit 0 needs the backend container (reader key) after a fingerprint exists
(the first backend run said "no fingerprint has been written yet", rc=1, until the celery `--anchor` run).
That is environment, not a T27 defect, but an operator cannot get exit 0 from the wrapper alone (F5).

## 3. `--hospital all`

```
ihr-operator: session=545e69043119 event=start tier=data operator=proof.claude command=manage:ihr_hospital_profile args=--slug,<v> hospital=all ...
ihr-operator: session=545e69043119 event=scope hospital=all hospital_entry=opened:alpha,opened:beta
ihr-operator: session=545e69043119 event=end outcome=ok rc=0 seconds=6
alpha: (860, 'operator', 'operator:proof.claude', '{"via": "operator", "tier": "data", "scope": "all", "reason": "operator access (all hospitals), session 545e69043119", "session": "545e69043119"}', '{"event": "opened"}')
beta:  (851, 'operator', ... same context ...)
beta:  (852, 'write',    'operator:proof.claude', same context, '{"updated_on": [...]}')
```
One `operator` entry per live hospital, fixed generic reason, the real reason only in the log. The `write` row is the
one-off's own write attributed to the operator (`ihr_hospital_profile` without options touched beta's profile
`updated_on`; harmless, shows write attribution works).

## 4. Refusals (nothing ran: no `admin-run` container, no start line)

```
manage --operator proof.claude --hospital alpha ...      -> ihr-operator: event=refused why=no-reason command=manage operator=proof.claude   rc=1
manage --operator root --reason "..." --hospital alpha   -> ihr-operator: event=refused why=shared-operator command=manage operator=root     rc=1
manage --reason "..." --hospital alpha (no operator)     -> ihr-operator: event=refused why=no-operator command=manage operator=none
```
Grammar note: `backup-now diff --operator X` is refused as `no-operator`; the options must come right after the command
(`backup-now --operator X diff`), as the README says.

## 5. Postgres 18.6 connection lines (db container log)

Exact text (no SSL suffix on these local-network sessions):
```
LOG:  connection authorized: user=ihr_owner database=ihr application_name=op:69762adbb568:manage:ihr_operator_open
LOG:  connection authorized: user=ihr_owner database=ihr application_name=op:69762adbb568:manage:ihr_hospital_profile
LOG:  connection authorized: user=postgres database=postgres application_name=op:7bc75aac4fb1:exec      (backup-now, recorded)
LOG:  disconnection: session time: 0:00:00.003 user=postgres database=postgres host=[local]
```
The regex in `deploy/logs/alerts/operator.yml` (`connection authorized: user=... database=...(?: application_name=...)?`)
matches all of them. Unrecorded login made on purpose:
```
$ docker exec indiehr-staging-db-1 psql -U postgres -c 'select 1'
LOG:  connection authorized: user=postgres database=postgres application_name=psql
```

## 6. vmalert (`/api/v1/alerts`)

Unrecorded `psql` login at 21:05:28 UTC: `PrivilegedLoginUnrecorded firing  user=postgres app=psql  value=1  activeAt 21:06:00`.
No alert for any `op:<session>` of an open or just-closed session I ran (69762adbb568, 545e69043119, 7bc75aac4fb1,
...), which were all in the same windows. `OperatorSessionRefused` fired (3 refusals, value 1 per `why`).
My hand-typed `docker exec ... ihr_audit_verify` also fired (`ihr_app manage:ihr_audit_verify`, 6 logins), as designed.

Findings on the reviewed list (`deploy/postgres/service-logins.txt`, mirrored in the alert), each seen live:
- **F1 pgBackRest has no application name.** The backup service's start-up full backup, `check` and the pgBackRest
  Archive Check restore point produce `user=postgres database=postgres` with **no** `application_name`
  (db log pids 102, 103, 153 at start-up; again after `docker restart indiehr-staging-backup-1`). The list expects
  `pgBackRest [backup]`, `[check]`, `[stanza-create]`; none appeared (pgBackRest 2.60.0). Alert fired:
  `PrivilegedLoginUnrecorded firing postgres "" value=2`. It would fire at every hourly check and every backup.
  Fix direction: list `postgres` with `-` (empty), or set `PGAPPNAME` in `deploy/docker/backup/schedule.sh`.
- **F2 `pg_isready`.** `deploy/docker/backup/schedule.sh:29` (`pg_isready -q -h /var/run/postgresql`) logs
  `user=postgres database=postgres application_name=pg_isready` at every backup-service start; not on the list.
  Fired: `postgres pg_isready value=1`.
- **F3 Keycloak has no application name.** `connection authorized: user=keycloak database=keycloak` (3 at start-up, 1 more
  after a Keycloak restart); the list expects `PostgreSQL JDBC Driver`. Fired: `PrivilegedLoginUnrecorded firing keycloak "" value=3`.
- **F4 first db start only:** the image entrypoint's init scripts log two `user=postgres application_name=psql`
  lines on an empty data directory (20:55:17). They happen before any session or Fluent Bit tail in a local proof,
  but on a server's first `up` they would alert once.
- Proof artefact, not a defect: the proof's own `local-proof` session (`op:7f27363f61e7:*` logins from
  `ihr_keycloak_sync`, `ihr_create_tenant`, `ihr_client_keys`, `ihr_terminology_load`, `psql`) alerted because its start
  line was written before Fluent Bit existed (VictoriaLogs holds only the `end` line: "session=7f27363f61e7 event=end").
  On a server Fluent Bit is already running, so this only recurs for a first `up` that creates the log stack itself.

## 7. `postgresql.auto.conf` metric

No wrapper subcommand runs ad-hoc SQL as `postgres` on the live db (only `restore-drill-psql`, F6), so the ALTER SYSTEM
was typed by hand with `docker exec db psql -U postgres` (itself an unrecorded login, correctly alerting).
```
$ scripts/staging.sh monitoring-textfile ; grep ^ihr_postgres_auto_conf ...prom
ihr_postgres_auto_conf_check_ok 1
ihr_postgres_auto_conf_settings 0
$ docker exec db psql -U postgres -c "ALTER SYSTEM SET log_min_messages='notice'"   ->   log_min_messages = 'notice'
ihr_postgres_auto_conf_settings 1        (Prometheus: PostgresSettingOverridden firing, activeAt 21:08:21Z, no `for:`)
$ docker exec db psql -U postgres -c "ALTER SYSTEM RESET ALL"
ihr_postgres_auto_conf_settings 0        (Prometheus: alert gone; auto.conf has 0 non-comment lines)
```
The value was never reloaded (no logging change).

## 8. Measurements

Session lines: a data-tier session writes 3 journal lines (start, scope, end), a job-tier one 2 (no scope unless it opens
hospitals); refusals 1. 12 sessions and 4 refusals gave 4,287 bytes of journal text (about 120 B per line). All lines
reached VictoriaLogs through Fluent Bit unchanged.

Postgres connection lines (`log_connections=authorization`, `log_disconnections=on`), 304 s windows, counting
authorization and disconnection lines of the db container, no operator or hand-typed logins in the windows:

| Window | Stack | lines/hour | bytes/hour | notes |
|---|---|---|---|---|
| A | 2 hospitals, celery worker and beat stopped | 1,563 | 199,658 | healthcheck 30, monitor 10, gunicorn 10, watch 6, provisioners 10 (per 5 min) |
| B | 2 hospitals, beat and worker running | 3,020 | 382,157 | celery 59 logins per 5 min |
| C | 3 hospitals, same | 3,209 | 405,272 | celery 70 logins per 5 min |

Average line 126 bytes. Per hospital: (70-59) logins per 5 min is 2.2 logins a minute (two beat tasks a minute,
`ihr_audit.seal` and `ihr_access.expire_grants`) = about 264 lines/hour and 33 KB/hour per hospital.
Fixed part about 2,400 lines/hour (healthcheck 720/hour alone).

Extrapolation to 200 hospitals (beat only, no web or task traffic; each request or task adds about 2 lines):
2,400 + 200 x 264 = about 55,000 lines/hour = 1.3 M lines/day, 7 MB/hour, 167 MB/day as sent. VictoriaLogs
compressed this stack's lines 8.9x (`logs-volume`), so about 19 MB/day on disk, 3.4 GB for 180 days against the default
`IHR_LOGS_MAX_SIZE` of 20 GiB (17 %). The journal side is not measured here (journald stores a few hundred bytes of fields
per entry; at 1.3 M entries/day a rough 0.4-0.5 GB/day x 14 days = 6-7 GB against `SystemMaxUse=8G`,
`deploy/host/bootstrap.sh:40`): that is close to the cap and should be measured on a real host before 200 hospitals.
The Fluent Bit drop of `ihr_app` service lines (attack.md fallback) would remove the celery share (about 70 %).

## Gaps

- Journal size per entry not measured (shared host journal). Web-request load not measured (no traffic).
- Single-hospital dumps, release and restore-drill sessions (their scope-all, the `ihr_restore_drill` list entry) not run.
- Web app not driven. Break-glass skipped. Disconnection lines were counted, not relied on.
- Memory: the stack ran with 0.4 GB free at its tightest; no OOM.

## Other findings

- F5 `staging.sh manage ... ihr_audit_verify` cannot exit 0 on a stack with the fingerprint bucket (no reader key in the
  admin one-off); the operator sees "audit trail OK"-less output and rc=1.
- F6 `manage dbshell` fails in the admin image (no psql); there is no recorded way to run SQL on the live db except
  `manage shell -c` as `ihr_owner`. Data-tier "psql" tier labels in the README therefore have no live subcommand besides
  `restore-drill-psql`.
- After the proof the dev stack needed `scripts/care-dev.sh migrate-hospitals` (migration `ihr_audit.0007_operator_entries`
  was not applied to the dev hospitals; `care-dev.sh up` failed "fleet run ...: fault, 8 failed" until then). That is
  the documented step after pulling migrations, not a bug.

## Clean-up

`down -v` on project `indiehr-staging` (17 labelled volumes plus 4 anonymous ones of its containers; no other volume
touched; `indiehr-care_*` intact), `deploy/staging/{.env,.local,.db-created,secrets}` and the proof's new
`generated/` files removed (the older `generated/mailtest` keys left), no `operator-pending.log` was made, image
`indiehr-backend:t27proof` removed (`indiehr-backend:staging` untouched). Dev stack restored: `care-dev.sh up` after
`migrate-hospitals`, then `care-dev.sh frontend`; `care-dev.sh ps` shows backend, celery, db, redis, s3 healthy.
No tracked file edited (git status shows only other sessions' docs changes).
