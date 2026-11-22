# P1-T27 design attack (2026-11-22)

Workflow wf_1fe29ae7-141. Lenses: S = Opus high security/audit/operations; D = egm-design, Z = egm-zoom-out, R = egm-implementation-readiness (Sonnet high). The orchestrator's decision is at the end.


## S: security, audit, operations (Opus high)

**P1-T27 design review (security, audit and operations lens), 2026-11-22**

Findings, most severe first:

**S1 (high). A deliberate insider can stay out of `PrivilegedLoginUnrecorded`.**
- Claim: the alert's allow-list identifies a session by its (role, application name) pair, and the client chooses the application name. Examples that stay silent:
  - `docker exec backend env IHR_PROCESS_NAME=gunicorn python manage.py shell` (ihr_app, which reads every hospital);
  - `PGAPPNAME=manage:migrate_schemas` as ihr_owner, using `secrets/db-owner.env`, which `ihr` can read;
  - `PGAPPNAME='pgBackRest…' docker exec db psql -U postgres`.
- A forged start line also opens a "sanctioned" `op:` session: `logger -t ihr-operator "...session=<s> event=start..."`. The kept `uid` does not help, because the real lines also come from `ihr`. Section 4.2's case for keeping the ihr_app lines does not hold: they are stored, but nothing alerts on them.
- Evidence: design §4.4, §5.1, §5.5; the actor fallback reads `application_name` (ihr_audit/install.py:99-101); `tenancy.py:39-46` (`IHR_PROCESS_NAME` overrides the name).
- Fix:
  - `op_begin` records `ses=$(cat /proc/self/sessionid)`, which the kernel sets.
  - The main alert becomes: any `ihr-docker` exec record with an auid set and no start line from the same `ses`.
  - Until P1-T27c is live, §1 and the ADR-010 amendment say that 5.1 only catches mistakes, not a deliberate insider.

**S2 (high). One superuser session can switch Postgres logging off.**
- Claim: only `log_connections` and `log_disconnections` are pinned with `-c`. `ALTER SYSTEM SET log_min_messages='panic'` (or `log_destination='syslog'`) followed by `pg_reload_conf()` hides every later authorization line. `db_server_check` runs only at `up`, release and `db-roles`.
- Evidence: staging.sh:539-575; server-settings.txt.
- Fix:
  - Pin `log_min_messages=warning` and `log_destination=stderr` too.
  - Add a check, run every minute (the root `ExecStartPre` or the audit watch), that `pg_file_settings` / `postgresql.auto.conf` is empty, with a critical alert.

**S3 (medium). G3's "attributed to operator:<name>" is not true for shell and dbshell, and the hospital entry can be erased.**
- Claim:
  - A shell can run `set_config('ihr.audit_actor','user:12:dr.x',false)`, or `SET application_name`, and so put its writes on a clinician.
  - As owner it can also disable `ihr_audit_append_only` and delete its own unsealed `operator` entry. ADR-010 already accepts this kind of edit for about a minute (unsealed) and up to about an hour (sealed, before the next fingerprint).
- Evidence: install.py:97-102; ADR-010:167-169.
- Fix:
  - The trigger adds `session_user` to the context (a non-superuser cannot change it).
  - The hook seals that schema right after its insert.
  - §6.5 names the journal line as the primary evidence.

**S4 (medium). The "outside a bootstrap run" exemption is a fake window anyone can open, and `HostAuditSilent` has a hole.**
- Claim:
  - Any host user can write the exemption marker: `logger -t ihr-bootstrap "audit-rules=loaded"`. It then covers 10 minutes of stopping auditd or removing its rules.
  - `HostAuditSilent` fires only when there were start lines in the window, so an attacker who never uses staging.sh never trips it.
- Fix:
  - The marker counts only with `uid:=0`.
  - The root `ExecStartPre` writes a metric counting the loaded `ihr-*` rule keys, with an alert when it is short.
  - `HostAuditSilent` becomes "no `ihr-docker` record for 10 minutes". The every-minute timer's `docker exec` (host-metrics.sh:187) gives a free heartbeat.

**S5 (medium). In a one-operator pilot the insider receives the alerts.**
- Claim:
  - There is a single `IHR_ALERT_TO` (staging.sh:3846).
  - The journal and VictoriaLogs are on the same host, under the attacker's docker or root.
  - The archive runs once a day, so the "two independent places" are neither independent nor off the host.
- Fix:
  - Send the operator.yml critical alerts (and `JournalSuppressed`) to a second recipient who is not an operator.
  - Say this in §1 and §10.

**S6 (medium). Volume and flooding.**
- Claim:
  - Beat runs every minute-job once per hospital (beat.py:5-7), and with `CONN_MAX_AGE=0` each run opens a session. The added lines therefore grow with hospitals × jobs, not with requests: about 1M lines a day at 200 hospitals.
  - All container logs share docker.service's journald limit of 20000 lines per 30 s (bootstrap.sh:152-153). A deliberate stdout flood from any container suppresses the db line, and `JournalSuppressed` is only a warning.
- Fix:
  - Measure at the bench scale in the local proof.
  - Make `JournalSuppressed` critical, and name it in 5.1's action text.

**S7 (medium). The data-tier hook can block a restore or a dump.**
- Claim:
  - `hospital-restore` and `dumps-get` insert into the hospital's live schema. That schema may be under a restore hold, or absent (an offboarded or archived hospital's dump).
  - Insert failure is fail-closed, so the restore's Keycloak step, `--resume` or a DPDP data return is refused.
- Fix: the hook skips schemas that are held or absent and writes `hospital_entry=skipped` on the operator line instead. `hospital-restore` keeps its own `restore` entry.

**S8 (medium). Two paths are missing or under-tiered.**
- Claim:
  - `audit-supersede export` sends audit entries (old and new row values; only identifiers are masked) to the operator's copy, as job tier with no reason and no hospital entry.
  - The Keycloak admin console (`ops-tunnel`, tier `none`) can reset a doctor's password or OTP and then read patients as that doctor. The trail would blame the doctor. Impersonation is disabled (keycloak.Dockerfile:16) and admin events are shipped (realm.py:172-178), but no alert watches them.
- Fix:
  - Make the export data tier, scope `all`.
  - Add the Keycloak admin path to the inventory, plus a vlogs alert on credential reset or remove admin events outside the staff-reset flow.

**S9 (low). The change breaks CI.**
- Claim: apps.yml:777 runs `staging.sh acme-proof`. That matches `acme-*`, which is job tier, and is refused without `IHR_OPERATOR` / `IHR_OPERATORS`. §9 does not list the workflow.
- Fix: make `acme-proof` tier `none`, or set the variables in the step, and add the workflow to the test list.

**S10 (low). Gaps in the auditd rules.**
- Claim:
  - There is no `arch=b32` twin of the execve rule.
  - The `docker.sock` watch (A5) will very likely not see `connect()`, so `curl --unix-socket` or Python run by `ihr` (euid not 0) goes unrecorded.
  - Keeping only `a0`/`a1` drops the arguments of `docker run -v /:/host`.
- Fix:
  - Record execve for `auid!=unset` without the `euid=0` condition, with a b32 rule beside it.
  - Shape EXECVE arguments with the `op_shape` rule (flags kept, values replaced by `<v>`).

**S11 (low). Option parsing is ambiguous.**
- Claim:
  - `--reason` and `--operator` exist both at staging level and in manage commands (`ihr_hospital_root`, `ihr_pack_pending`).
  - `dumps-get` needs `pg_su` (which requires `op_need`) to find its scope before `op_begin` can write the start line.
- Fix:
  - Staging-level options are read only before the first word that is not an option.
  - `dumps-get` takes its scope from the dump's bucket path.

**S12 (low). Over-built for a one-operator pilot (PR1-8).**
- Claim: the `set_tenant` scope guard, exit 77, `OperatorOutOfScope` and two mutation rows bound only an honest operator: a shell can unpatch the guard or use raw SQL (the design's §10 admits it). The D6 audit-page label plus the Hindi strings add frontend work.
- Fix: drop the guard (keep the up-front entries) and move D6 to P4-T6. That saves about an hour of P1-T27b and one frontend build.

**What the design gets right:**
- The inventory is honest and mostly complete, the boundary statement is honest ("docker group = root"), and it keeps command values out of the record without hashing them (D2).
- A single top-level `op_end` around a subshell `main` handles staging.sh's 35 competing EXIT traps correctly, and it fails closed when the journal cannot be written.
- The `operator` entries are written up front for the declared scope, on a separate autocommit connection, so they survive the command rolling back. This tells the hospital the truth: we know who had access, not which rows they read.

**Method:** 14 graphify calls (`explain` on pg_su, admin_manage, rel_operator, apply_tenancy, process_name, reapply, db_server_check, monitoring_textfile, monitoring_key_metrics, kt, ActiveTenantScheduler; one `query`). I changed no code, so I ran no `affected`. After that I grepped only for exact text in files the graph had named. I wrote no files.

tier-fit: OK: an adversarial security, audit and operations review of an access-control design is Opus-high work. Assumptions A1 and A5 are inferred, not tested live.


## D: architecture and design (egm-design)

Design review of docs/reviews/p1-t27/design.md (architecture lens). I read the design, the PR1-3 finding, and spot-checked staging.sh and bootstrap.sh. I did not check any other source behaviour. Line numbers refer to design.md unless a file is named.

1. HIGH. Claim: `( main "$@" ) || rc=$?` is a transparent wrapper ("today's dispatcher, unchanged inside", L228-233). It is not. staging.sh sets `set -euo pipefail` (staging.sh:125). Bash ignores errexit for everything inside a subshell that runs in a `||` list. That turns off `set -e` for all 5,420 lines. Failing steps would no longer abort, and release, restore and rollback would carry on past errors. The design never states this assumption.
   Fix: capture the status without `||`, for example `set +e; ( set -e; main "$@" ); rc=$?; set -e`. Then add a test that a failing mid-function command still aborts. Rerun the whole release and restore suites against the wrapper.

2. HIGH. Claim: the alert allow-list (5.1, 4.4) separates sanctioned from unsanctioned sessions.
   - The application name is client-chosen, via `PGAPPNAME` or `-e IHR_PROCESS_NAME`.
   - An operator running `docker compose run admin` can set `manage:migrate_schemas`, and `ihr_owner` is then allow-listed. With `docker exec backend` and `gunicorn`, `ihr_app` is allow-listed. `ihr_app` reads every hospital.
   - A past or forged `op:<hex>` is accepted when any start line exists in the last 24h (L373). The check does not bind the session to a user, a time or an open session.
   - 5.5 covers only forged start lines. D4 argues that keeping the `ihr_app` lines is better than dropping them, but an allow-listed line never alerts.
   Fix: state that the alert catches the careless path only. Add a start line with no end line to the evidence. Require the `op:` session to be open (start present, end absent) at connect time. Where possible, key the allow-list on something the client cannot choose, such as the connecting container's address or the role plus the `db` service. Write the `ihr_app`/`gunicorn` gap into section 10.

3. HIGH. Claim: the record is trustworthy and tamper-evident.
   - The design assumes the operator is root, so root can delete VictoriaLogs data, stop Fluent Bit, or edit the archive.
   - 1 and 5.4 cover only auditd and Postgres tampering.
   - The design never says where the 180-day store and the 400-day archive live (the same host, or off-host and write-locked) or who can delete them. PR1-3 names CERT-In as a driver.
   Fix: add an explicit statement of store location and deletion rights. Add a "logs absent or shipping stopped" alert (a dead-man check on `ihr-operator` and `audit` volume). Add the store's location to the section 10 limits.

4. MEDIUM. Claim: raw logs are covered. D3 says `all` means an `operator` entry in every hospital because a shell could read it.
   - `logs` and `logs-archive-get` take `all` implicitly (3.5) but write no hospital entry (6.4 says "the start/end line is the record").
   - `staging.sh logs` is the raw, unredacted stream (row 15), so this contradicts the rationale.
   - Rows 4, 8 and 13 face the same gap. `restore-drill --keep` leaves a full copy of every hospital open for later psql after the session end line (rows 8, 14).
   Fix: give raw-log commands hospital `all` entries, or state in section 10 that they have no hospital-side record.

5. MEDIUM. Claim: the free-text `reason` and the command shape are safe to write into hospital chains (3.2, 6.2).
   - The reason regex allows digits. Fluent Bit masks protect only the log copy.
   - The hospital's own append-only, immutable chain stores the raw text.
   - With scope `all`, a reason such as "INC-12 bed fix" is written into every hospital's chain and shown on each hospital's audit page (D6). That leaks one hospital's incident to the others.
   Fix: reject runs of 8 or more digits and any name-like text in `bad-reason`. Use a ticket-id-only pattern for `all`, or write a generic reason into hospital entries and keep the full reason in the log store only.

6. MEDIUM. Claim: the end line is reliably written (3.6).
   - The INT/TERM trap in the parent runs only after the foreground child exits, because bash defers traps while it waits. A kill or SIGKILL gives no end line.
   - `interrupted` therefore cannot be relied on.
   - No alert fires on a start line with no end line after N hours.
   Fix: add a `SessionNoEnd` alert (start without end after 12h), and document that an absent end line is normal for a killed run.

7. MEDIUM. Claim: fail-closed (D9, 3.1) is safe.
   - `logger` succeeding does not mean journald stored the line. journald's default rate limit and disk limits can drop it silently.
   - Fail-closed also blocks emergency work. A journal that is full or unwritable blocks `rollback`, and a broken audit table blocks `manage shell`. The emergency path is stated to be root/docker, "itself audited" (L749), but auditd is not live until P1-T26.
   - `all` writes one up-front insert per hospital. One schema that is restoring, or whose status is not in `MIGRATE_STATUSES` (fleet.py:89), makes the whole session refuse or go unrecorded. Archived hospitals are not covered at all.
   Fix: name a break-glass procedure. Make it a documented `--break-glass` variant with a mandatory reason line, an alert, and fixed job-tier exceptions for rollback. Say which statuses `all` covers.

8. MEDIUM. Claim: the operator is identified (3.4, D1).
   - `IHR_OPERATORS` lives in `.env` on the host, which the shared `ihr` account can write. The list is sent by the deploying machine's `deploy.sh`.
   - Anyone can add themselves or use a colleague's name.
   - The fingerprint comes from `$SSH_USER_AUTH`, an environment variable the operator controls. `sudo` strips it. The file has `publickey <type> <key>` lines, and the `ssh-keygen -lf` conversion is not specified.
   - No registry maps a key fingerprint to a person.
   Fix: add a fingerprint-to-name map, written at bootstrap and root-owned. Refuse a name whose fingerprint is not mapped to it. At minimum, state that the check proves nothing until D1-B.

9. MEDIUM. Claim: auditd rules record the operator's own CLI use only (G4, 7).
   - `-w /usr/bin/docker -p x` has no `auid` filter. The every-minute metrics timer (bootstrap.sh:267-285, `User=ihr`) runs `staging.sh` and probably `docker compose` (an inference, not checked). It will flood the audit stream and hide the real records. `HostAuditSilent` also depends on volume.
   - The rules are b64 only (no `arch=b32`).
   - The `EXECVE` allow-list keeps only `a0` and `a1`. A record of `docker exec ...` or `docker compose run ...` keeps neither the container name nor the command. It cannot distinguish `docker exec db psql` from `docker exec x true`, so the 5.1 action text cannot be followed.
   Fix: use `-a always,exit -S execve -F path=... -F auid>=1000 -F auid!=unset`, add b32, and keep `a2` (and `a3` when `a1` is `compose`) for docker records, limited to non-argument words.

10. MEDIUM. Claim: the inventory is complete (section 2).
    - Row 20 (`ops-tunnel`, Keycloak admin console) is classed `none` (3.3). Keycloak impersonation or user creation gives access to patient data as a hospital user. That access is attributed to the impersonated staff member in the hospital trail, which is worse than no record. `grafana-reset-admin` and `keys` are not discussed in this respect.
    - Also unaddressed: Redis contents and SeaweedFS volumes (reached only via row 21, auditd).
    Fix: state in section 10 that Keycloak admin actions are out of the record. Add the Keycloak admin events and the tunnel session as `ihr-operator` lines.

11. MEDIUM. Claim: scope is derived once the session opens (3.5) for `dumps-get`.
    - The start line must carry `hospital=<scope>`, but the slug is looked up with `pg_su` after the session is open (L214, 6.4). The start line cannot name the scope.
    - `--hospital`, `--reason` and `--operator` are parsed "before the subcommand's own args" (3.5). The example (L211) puts them after `manage`, and pass-through to manage.py is ambiguous (the commands may have their own `--reason`).
    Fix: write the start line with `hospital=pending` and add a scope event line, or do the lookup before `op_begin`. Define the parse order for each subcommand.

12. LOW/MEDIUM. Claim: migration 0007 is safe on rollback.
    - The audit chain gains `operator` rows. Rolled-back code (viewer, export, `verify_export`, watch) may break on the unknown kind.
    - The unknown-kind test covers only new code.
    - In a data-tier session started before 0007 is applied, the kind check rejects the `operator` insert, and fail-closed blocks the session.
    Fix: make 0007 expand-only and ship the unknown-kind tolerance a release earlier. State the order.

13. LOW. Claim (A1, 4.1): a superuser cannot hide its own authorization line. This is only argued for `log_connections`. The quoted doc says superusers can change the parameter at session start. `log_disconnections` is read at disconnect, after startup options, so `PGOPTIONS='-c log_disconnections=off'` may suppress it. Fix: extend the A1 test to both settings, and drop any reliance on the disconnection line.

14. LOW. Claim: the alert tolerates ordering (5.1). Fluent Bit may ship the `db` line before the matching start line. A 1m interval with `_time:10m` then fires a critical false positive. Fix: delay the evaluation by 2m, or add `for: 2m` to the rule.

15. LOW. Claim: effort of about 8 agent hours. The estimate covers refactoring more than 100 call sites in a 5,420-line script, classifying about 70 subcommands, updating every `test_staging_*` suite, a frontend change, locales and an ADR. PR1-3 also said 6-8h, and this design adds auditd and 3 subtasks. The G3 wording ("refuses to open a hospital outside scope") overclaims, since raw SQL and Python bypass it (acknowledged only in section 10). Fix: reword G3 to "bounds Django's tenant switch only" and add a 20% reserve.

Unnamed terms a new reader cannot resolve: P1-T26, P1-T30, PR1-8, Q-K-D1 to D7 (these appear to be this design's own D1-D7), `graphify`, `IHR_LOG_DRIVER`, `MIGRATE_STATUSES`, "the admin user".


## Z: simplicity and scope (egm-zoom-out)

Zoom-out review of /home/ubuntu/AI Projects/IndieHR/docs/reviews/p1-t27/design.md. Line numbers are design.md unless a file is named.

Overall, the design answers PR1-3 but is about 8 agent hours across three subtasks. The finding itself budgeted 6-8 hours (closing-review L178-183). Several parts go beyond PR1-3, and several rest on assumptions the text never states.

1. **High: the Django scope guard goes beyond PR1-3 and is admitted to be bypassable.**
   - Claim: 6.2 item 3 wraps `DatabaseWrapper.set_tenant`. That adds `SystemExit(77)`, a `refused` entry, the `OperatorOutOfScope` alert (5.3), a mutation row, and assumption A4.
   - Evidence: L461-464, L663-664. The ADR text says raw SQL in a one-off reaches any hospital, and L469-473 says the same. PR1-3 asks only for "an audit event when a one-off opens a hospital" (closing-review L181).
   - Fix: delete item 3, exit 77, 5.3, A4 and the `operator-scope-guard` mutation row. Keep the up-front entries and the attribution.

2. **High: the log store is assumed to be tamper-resistant against the insider the problem names.**
   - Claim: L16-17 and L44-49 name the root operator as the threat. The "two independent places" (auditd, Postgres lines) both ship to the same VictoriaLogs and vmalert. The design never says where that runs or who can delete from it. It also never says who receives the alerts.
   - Evidence: L416, L388-390. Alerts go to one person (closing-review PR1-4, L186-192), and that person may be the operator.
   - Fix: add one paragraph stating where the store, the archive and alert mail live. State whether the 400-day archive is the off-host, write-once copy. If it is not, say so in the ADR-010 limits.

3. **Medium: the operator allowlist is assumed to mean something.**
   - Claim: `IHR_OPERATORS` is a self-declared name check. Any `ihr` login can declare any listed name. The `key=` fingerprint is "a hint written by a process the operator controls" (L147-152).
   - Evidence: L198-202, L665-666.
   - Fix: state in 3.4 that this is attribution by convention, not authentication. Drop `key=` and the `ExposeAuthInfo` change (L522). sshd's own `Accepted publickey` line plus `from=` and the timestamp already gives the join.

4. **Medium: the number of operators is never stated.**
   - Claim: the text says "one of us" (L7) and D1 recommends A "until more than one operator". With one or two operators, the shared-name rejection, the allowlist, `deploy.sh` forwarding and the `init --operators` option are ceremony.
   - Evidence: L194-206, L704-710.
   - Fix: state the operator count. If it is 1-2, use `IHR_OPERATOR` plus the reason only, and skip the allowlist.

5. **Medium: the tier table is heavier than it needs to be.**
   - Claim: `OP_TIER` is a table covering about 70 subcommands. Job-tier commands get a synthesized reason, and the table exists mainly so that job-tier owner logins have a start line and do not trip alert 5.1.
   - Evidence: L167-190. A static test forces every label to have a row. The fix for a mistyped row is left to "the builder confirms each row" (L189).
   - Fix: classify by wrapper, not by subcommand. `admin_run`, `migrate_run` and `pg_su` open an implicit job-tier session if none exists. Only the 7 data-tier commands need a table row. Anything unclassified defaults to data tier (fail closed).

6. **Medium: the job/data split has an unstated gap.**
   - Claim: job-tier commands write no per-hospital entry, but some of them reach whole-hospital data.
   - Evidence: `restore-drill` without `--keep` is job tier, yet row 4 (L64) says it is a copy of every hospital. `hospital-restore-drill`, `dumps-now` and `release` also open every schema as owner (L174-181, L466-467).
   - Fix: state the rule ("a job leaves its own record elsewhere") and list which job commands have one. Move the rest to the data tier.

7. **Medium: the alert's sanctioned list is built from a one-time capture, with two copies to keep in step.**
   - Claim: `service-logins.txt` comes from one local proof and is embedded in the LogsQL rule. Any new service, role or command fires a critical alert until the list is updated.
   - Evidence: L343-355, L602.
   - Fix: generate the rule from the file in `check-logs.py`, so there is one source. Add a line on who owns updating it.

8. **Medium: the hospital-facing page is premature.**
   - Claim: the new `operator` kind adds migration 0007, a viewer kind, a frontend component, `en.json` and `hi.json` strings, and export/verifier checks. The DPA work (P4-T6) is where the hospital is promised this view.
   - Evidence: L484-489, L734-736. Existing precedent: `ihr_audit/views.py:39` is `KINDS = frozenset(("write","truncate","request"))`. The `restore` kind is not shown in the viewer either.
   - Fix: keep migration 0007 and the verifier/export tolerance. Defer the viewer, label and locale work to P4-T6 (D6 option B).

9. **Medium: the host-audit pipeline rests on unverified assumptions and is the largest build item.**
   - Claim: a Lua EXECVE rebuild, a type allow-list, canary checks and three alerts depend on unverified A5 and A6. Live proof waits for P1-T26. PR1-3 asked for "an auditd rule covering docker exec".
   - Evidence: L549-559, L647-648, L751-754 (D10 audits all root exec by logged-in users).
   - Fix: for T27c, ship the `docker`/`nsenter` rules plus a SYSCALL-only allow-list. Drop `PROCTITLE`, `PATH` and EXECVE argument rebuilding, and drop D10. Add them only if P1-T26 shows a need.

10. **Medium: Postgres log volume is accepted and not measured.**
    - Claim: two extra lines per web request, with measurement postponed to P1-T26 (L322-327). Any sizing failure then lands at go-live. The fallback is stated, but only as "Q-K-D4 option B".
    - Fix: say that the volume number from the local proof (9.4 step 5) gates T27a "done". Name the limit being compared against.

11. **Medium: the subshell wrapper changes behaviour that is not stated.**
    - Claim: `( main "$@" )` runs the whole dispatcher in a subshell. Today the dispatcher is top-level (`case "${1:-}"` at staging.sh:5290, no `main`). `manage` uses `-T` (staging.sh:5294), so an interactive `shell` or `dbshell` is not a TTY today. D5 says `dbshell` is allowed because an incident needs it.
    - Evidence: L228-236, L730-732. The "35 `trap ... EXIT` sites" figure (L234) does not match what I found. `grep "trap .* EXIT" scripts/staging.sh` returns 17 lines.
    - Fix: verify the 35 figure. State whether `dbshell`/`shell` get a TTY, and test it. Also note that SIGKILL or a dropped ssh session leaves a start line with no end line, and say whether that is alerted.

12. **Low: `logger` fail-closed assumes the journal reports its own drops.**
    - Claim: journald rate limiting can silently drop lines, and `logger` still returns 0. The journal's persistence setting is never stated. The design assumes the host journal is persistent.
    - Evidence: L121-123.
    - Fix: add "journald `Storage=persistent` and rate limit" to the bootstrap `--check`, or state the risk in ADR-010.

13. **Low: the alert is defeated by one `logger` line plus `PGAPPNAME`, until auditd exists.**
    - Claim: 5.5 admits this. But T27a ships and passes review before T27c exists and before P1-T26 proves it.
    - Evidence: L420-425, L686-688.
    - Fix: say in 11 that G2 is not trustworthy against a deliberate operator until T27c is live.

14. **Low: undefined host dependencies are assumed.**
    - Claim: `-w /usr/bin/psql` and `-w /usr/bin/docker` assume those paths exist on the host and that Docker is installed from a known source. `connections.create_connection` is assumed to be available in the pinned Django. The design also assumes graphify is available.
    - Evidence: L528-534, L458, L189.
    - Fix: add one "verified" line per item. Confirm that auditd accepts a watch on a path that does not exist.

15. **Low: some content is below design altitude, and the numbering is inconsistent.**
    - Claim: exact regexes, full LogsQL, bash blocks and mutation row names are build detail. The numbering is also inconsistent. Open decisions are labelled `Q-K-D1..D7` in the text but D1..D11 in section 13, and the Q-K134+ numbers are unassigned.
    - Evidence: L145, L368-376, L228-234, L759-760.
    - Fix: move the code and queries to the build brief. Keep the interfaces and the names the tests grep for.


## R: implementation readiness (egm-implementation-readiness)

Implementation readiness review of docs/reviews/p1-t27/design.md (P1-T27, PR1-3). Read-only. I checked the design against scripts/staging.sh L125 and L5290-5420, plus a few symbol greps. Severities are high, medium or low.

1. HIGH. The new top level is described as "today's dispatcher, unchanged inside", but there is no `main()` today.
   - Evidence: design L227-234. staging.sh L5290-5420 is a bare top-level `case`, with `exit "$rc"` at L5320 and `*) sed ...; exit 1` at L5419. staging.sh L125 sets `set -euo pipefail`.
   - Problem: `( main "$@" ) || rc=$?` runs main under an `||` list, so errexit is switched off for the whole subshell. Every release, rollback and restore would lose its `set -e` safety. That is a behaviour change in the most dangerous scripts.
   - The design does not say how to wrap the `case` into a function, what happens to the `restore_envs=()` assignments and the `fleet` branch's `exit "$rc"`, or how `main` receives argv after the staging-level options are stripped.
   - Fix: state the wrapper explicitly, for example `( set -e; main "$@" ); rc=$?` guarded with `set +e` around it. Name the file lines to move. Say that `main` receives the argv with `--hospital`, `--reason` and `--operator` removed.

2. HIGH. The parsing of the staging-level options is not specified.
   - Evidence: design L194-195, L208-213, L223.
   - `--operator` is already parsed inside release, rollback, restore, db-roles, hospital-restore, audit-supersede and hospital-root (L194-195). `--reason` is already parsed by hospital-restore (L174).
   - `op_begin <cmd> <args...>` must find these options at subcommand-specific positions before dispatch. The design does not say whether it strips them or leaves them. `staging.sh dumps-get` checks `$# -eq 4` at L5385, which breaks if options are added.
   - Missing rules: conflict and precedence when `--operator` is given both ways, and how `--hospital` is told apart from a subcommand's own flag.
   - Fix: add an argument grammar. The staging-level options come immediately after the subcommand, are consumed by `op_begin`, and are not passed on. Say how each subcommand's own `--operator` and `--reason` are handled (accepted and equal, or the staging-level value is passed through).

3. HIGH. Scope is contradictory for the data tier and the job tier.
   - Evidence: 3.2 L146 says job-tier scope is `none`. 6.2 item 4 (L475) says job tier runs the hook with "scope `all`". Hook item 3 therefore needs `all` while `IHR_OPERATOR_SCOPE` is `none`.
   - 3.3 L171 says data-tier commands require `--hospital`. 3.5 L214-215 says `dumps-get` derives it "with `pg_su` once the session is open". `op_begin` validates before the session exists, and a `pg_su` call needs `op_need`, which needs an open session.
   - The same applies to `hospital-root` without `--slug`, if any sub-form lacks one.
   - Fix: define the `IHR_OPERATOR_SCOPE` value the hook receives for each tier (`none` is treated as `all` for the tenant guard). Say that `dumps-get` opens the session with a deferred scope and re-writes the start line, or looks the slug up first through a named unrecorded fixed-SQL helper.

4. HIGH. The row the hook inserts into `<schema>.ihr_audit_entry` is under-specified.
   - Evidence: design L453-458 gives only kind, actor, context, tbl, op, row_pk, patients and data, and says "column order as supersede.py L884".
   - Missing: the chain fields (sequence, previous hash, entry hash, timestamp). Who sets them, a trigger or the code? Does a plain INSERT on a separate connection work, given that the connection needs the audit GUCs and may need the owner role?
   - Also missing: `connections.create_connection("default")` under django-tenants, and which schema and `search_path` the fresh connection has. The `connection_created` receiver would re-apply the operator context, and the spec does not say whether that is wanted here.
   - Fix: give the exact INSERT statement and say which trigger or default fills the chain columns. Name the connection helper and cite it in the installed Django.

5. MEDIUM. The hook's patch points are unstated or idempotent-unsafe.
   - Evidence: design L450-464.
   - "Wraps `BaseCommand.execute`" means the entry is written on every nested `call_command`, so `shell`, `migrate_schemas` and `ihr_fleet_run` would write duplicates. Nothing makes the patch run once per process.
   - `set_tenant` wrapping is "assumption A4": the builder must discover whether `set_schema`, `set_schema_to_public` and `schema_context` go through it.
   - `SystemExit(77)` raised inside `set_tenant` may be swallowed by `except Exception` blocks or by the `shell` REPL.
   - Fix: add a once-per-process guard with the key. State A4's expected target method name and a fallback (for example a patch of `DatabaseWrapper.set_schema` as well). Say how 77 propagates out of `shell`.

6. MEDIUM. `restore-drill` has two tiers, but the table is keyed by subcommand only.
   - Evidence: design L167 (an associative array keyed by subcommand), L173 and L176 (`restore-drill` with `--keep` is data, without it is job).
   - `hospital-root` and `manage` are also listed as data tier, but `hospital-root` sub-forms differ.
   - Fix: say tier can be a function of argv (name the function, `op_tier <cmd> <args>`) and add the static-test rule for it.

7. MEDIUM. The tier lists are not checkable as written, and one entry is a glob.
   - Evidence: design L180-181 (`acme-*` and `cert`), L182-185.
   - A key of `acme-*` in an associative array does not match as a glob, so the "every `case` label has an entry" test fails or passes trivially.
   - Fix: list the literal labels: `acme-init`, `acme-register`, `acme-new-cert`, `acme-retry` and `acme-proof`. Make the static test compare against the labels extracted from the dispatcher.

8. MEDIUM. Some job-tier and none-tier decisions are unjustified and could be wrong.
   - Evidence: design L174-186.
   - `down -v` destroys volumes and `ps` runs `compose ps`, but both are classed `none`. `dumps-expire` and `logs-archive-expire` are `none` while the file lists them as holding keys. `acme-proof-run` is `none`.
   - "None must reach no wrapper" is tested only under stubs (L186-187).
   - Fix: give a one-line reason per `none` entry, or state the rule that decides the class.

9. MEDIUM. The `logger` write order and the fail-closed rule are incomplete.
   - Evidence: design L113-123.
   - Missing: what `logger` does when `/dev/log` is absent (the `.local` proof, CI, containers). Which of the dispatcher's `die` paths run before `op_begin`, for example `*)` usage output? Local proofs with `IHR_LOG_DRIVER` unset "go to stderr only" (L117-118), yet fail-closed requires a journal write.
   - Fix: say whether `logger` is absent-tolerant in `.local` mode, and name the env that decides it.

10. MEDIUM. The operator-name and reason rules are inconsistent with the rest of the system.
    - Evidence: design L136, L154-158, L161-162, L198-202.
    - The `shared-operator` check reads `/etc/indiehr/admin-user`. The file's existence and format are not shown anywhere, and it does not exist on a laptop or in CI.
    - `IHR_OPERATORS` is "written by `staging.sh init` from `--operators`". The flag exists nowhere today, and no `init` changes are listed in the sub-task table (L686), nor is the `deploy.sh` change location.
    - Reason charset (L154) excludes `=` and quotes, but `"job:<cmd>"` contains `:`. That is allowed, but the job default for `acme-*`, `monitoring-*` and similar was not checked against it.
    - Fix: add the `init` and `deploy.sh` edits to T27a, name the file's content, and add a fallback for it being absent.

11. MEDIUM. Alert 5.1's LogsQL is untested guesswork and cannot be built directly.
    - Evidence: design L371-376 and A7 (L699).
    - `app:~"^Y$"` clauses and the `session:in(<subquery>)` form are written from memory. The `extract_regexp ... from app` syntax and `$` anchoring against a trailing `application_name=` are unverified.
    - The 24h subquery inside a 10m alert window means a start line older than 24h is lost. That is not discussed.
    - `service-logins.txt` has no file format, only "role, pattern, where, why". The test "every line appears in the rule" has no stated serialization.
    - Fix: specify the file's columns and separator, and the exact clause template. Give a ready fallback query (prefix-only) as the default and move the subquery to an upgrade.

12. MEDIUM. The Postgres change touches the existing files by reference only.
    - Evidence: design L301-316.
    - `compose.yaml` `db` command, `care-local.override.yaml`, `tenancy.override.yaml`: no line numbers or exact edits. The settings loader regex `^[a-z_]+=[A-Za-z0-9]+$` accepts the values, which is fine.
    - `PGAPPNAME` for the throwaway `docker exec ... psql` (row 7) needs `-e PGAPPNAME`; `pg_su` is told to gain it (L249-251), but the `pg_dump` forms are not named.
    - Fix: list the file edits.

13. MEDIUM. The Fluent Bit side is not specified for T27a.
    - Evidence: design L423-425 and L563-581.
    - `shape()` "now keeps the trusted `_UID` of host lines as `uid`". There is no description of how the Lua change reaches the journald input's field list, nor whether `uid` is a new output column in VictoriaLogs.
    - Fix: name the `redact.lua` function edit and the input or parser config line.

14. LOW. The `none`-tier `op_begin` call is a no-op, but the end-line trap would still fire on INT and TERM for a session that has no start line.
    - Evidence: design L230-233.
    - `op_end` needs a guard `[[ -n "$OP_SESSION" ]]`. Not stated.

15. LOW. The `refused` line carries `command=` and `operator=` with no allow-list for `command=`.
    - Evidence: design L130, L138.
    - An argument typo such as `manage "9876543210"` could land in `<cmd>`. Only the manage word is covered by the regex.
    - Fix: the `command=` value passes the same `<v>` rule.

16. LOW. Counts and references to check: "35 `trap ... EXIT` sites", "dozen direct psql calls", the line citations in 2. Several L-numbers (L301, L402-408, L1462) match, but the design should say which commit they were read at, since the file is 5,420 lines and the dispatcher moves when wrapped.

17. LOW. No rollback or compatibility note.
    - Evidence: design L477-483 (migration 0007) and L481-483.
    - There is no statement of how a release carrying the hook behaves on a database whose schemas lack the `operator` kind (the first release applies 0007 to every hospital in `migrate_schemas`), nor whether the hook is inert until then.
    - Fix: say data-tier entries are only attempted after 0007 is applied, and state what a rollback to the old build does with existing `operator` rows (the old kind check would reject VALIDATE).

## Orchestrator decision (2026-11-22; binding over design.md wherever they differ)

The design stands except as changed here. Builders read design.md, then this section. The decisions go to
docs/QUESTIONS.md as Q-K134…Q-K139 (auto-accepted by Claude, awaiting owner confirmation).

### What the record is, stated honestly (S1, S5, D2, D3, Z2, Z13)

- The `ihr-operator:` record and the Postgres login lines **attribute and bound an honest or careless operator**.
  Against a deliberate insider with the `ihr` account (docker group = root) they are evidence, not a barrier: the
  application name and the start line can be forged, and the journal, VictoriaLogs and vmalert run on the same host.
  §1 of design.md, the ADR-010 amendment and the README say exactly this. Off-host evidence is the daily locked archive
  (P1-T9c) and, from P1-T27c, the kernel audit records; alert delivery to a second person who is not an operator is an
  **owner go-live item** (with PR1-4's second channel), not built here.
- `PrivilegedLoginUnrecorded` stays (critical), and it accepts an `op:<session>` application name only while that
  session is **open**: a start line exists and no end line yet (D2). Its text says it catches mistakes, not a
  deliberate insider. `op_begin` records the kernel session id (`ses=` from `/proc/self/sessionid`, S1) so P1-T27c's
  audit records can be joined to it.
- Add `OperatorSessionNoEnd` (warning): a start line with no end line after 12 h (D6). An absent end line after a kill
  is normal and documented.

### Wrapper and grammar (D1, R1, R2, R3, R14, S9, S11)

- The dispatcher moves into `main()` and runs as `set +e; ( set -e; main "$@" ); rc=$?; set -e`. Never `|| rc=$?`.
  A static test fails on `( main` followed by `||`, and a behaviour test proves a failing command inside a function
  still aborts the run (exit non-zero, no later step runs). The release, rollback and restore suites pass unchanged.
- Grammar: `staging.sh <cmd> [--operator NAME] [--reason TEXT] [--hospital SLUG|all] <cmd args…>`. These three come
  **immediately after the subcommand**, are consumed by `op_begin` and are not passed on. A subcommand that has its own
  `--operator` (release, rollback, restore, db-roles, hospital-restore, audit-supersede, hospital-root) receives the
  session's operator; giving both with different values is refused. `IHR_OPERATOR` in the environment is the
  fallback for `--operator`.
- `op_end` runs only when `OP_SESSION` is set. A `none`-tier command writes nothing.
- `acme-proof` (run by CI, apps.yml) is `none`; the CI workflows that call staging.sh are listed in the test plan and
  pass unchanged.
- `dumps-get` takes its hospital from the dump's bucket path before `op_begin`, so the start line names it (S11, D11).

### Tiers by wrapper, not a 70-row table (Z5, R6, R7, R8)

- **Data tier** is an explicit, short list, decided by `op_tier <cmd> <args…>` (argv-aware): `manage`, `logs`,
  `logs-archive-get`, `dumps-get`, `restore-drill --keep`, `audit-supersede export`, `hospital-root`, and any direct
  psql/dbshell subcommand. Data tier needs operator, `--reason` and `--hospital` (dumps-get: derived).
- **Job tier** is everything else that reaches one of the privileged wrappers (`admin_run`, `migrate_run`, `app_exec`,
  `pg_su`, `audit_supersede_run`): the wrapper opens an implicit session with reason `job:<cmd>` if none is open, and
  needs an operator. No per-subcommand table for job and none tiers.
- A static test: no privileged call (`compose … run … admin|migrate`, `compose exec`, `docker exec … psql|pg_dump`,
  `psql`) outside the wrappers; every data-tier label exists in the dispatcher.

### Operator identity (Z3, Z4, D8): Q-K134

- The pilot has one operator (the owner). `--operator` / `IHR_OPERATOR` is **required, with no default**; refused if
  empty, if it is a shared or system account name (`ihr`, `root`, the bootstrap admin user, `admin`, `postgres`), or
  if it fails `^[a-z][a-z0-9.-]{1,31}$`. No `IHR_OPERATORS` allow-list, no `init --operators`, no `key=` field, no
  sshd `ExposeAuthInfo` change. `from=` (the SSH client address from `SSH_CONNECTION`) plus sshd's own "Accepted
  publickey" line and the time give the join. It is attribution by convention, not authentication: said in the ADR.
  Personal Unix accounts per operator are the answer when a second operator joins (ADR-020's production-1).

### What the line records (D5, Z15): Q-K135

- `args=`: option names kept, every value replaced by `<v>`; the `command=` word passes the same rule.
- `reason=`: 3–120 characters from `[A-Za-z0-9 ._:/#-]`, no run of 6 or more digits (refused: `bad-reason`).
- `manage dbshell` and `manage shell` are allowed (data tier), recorded. `logs`, `logs-archive-get` and journalctl
  through staging.sh are data tier.

### Per-hospital entries (Z1, Z8, S3, S7, S12, R4, R5, R17, D5, D12): Q-K136

- `ihr_audit/operator.py` writes one `operator` entry per hospital in scope at the start of a data-tier session (and of
  a job-tier session that opens hospital schemas: release, restore-drill, dumps-now, hospital-restore-drill write
  `scope=all` entries; the restore keeps its own `restore` entry). **No `set_tenant` scope guard, no exit 77, no
  `OperatorOutOfScope`, no viewer, frontend or locale work**: the audit page shows the new kind with P4-T6 (the DPA).
- `all` = every hospital whose status is in the fleet's live statuses; a schema under a restore hold, or absent, is
  skipped and the line says `hospital_entry=skipped:<slug>`. The entry's reason is the given reason for a single
  hospital and the fixed text `operator access (all hospitals), session <id>` for `all`; the full reason stays in the
  log store.
- The insert reuses the path that already writes `restore` entries (migration 0006, `ihr_audit/events.py` /
  `restore/engine.py`): the builder states the exact INSERT and which trigger or default fills the chain columns,
  writes it on a separate autocommit connection, once per process (guard), and seals that schema right after.
- Migration 0007 is expand-only (adds the kind). The hook writes only when the kind is accepted; otherwise the line
  says `hospital_entry=skipped:old-schema`. Old code reading `operator` rows: the viewer's `KINDS` filter hides them;
  the builder proves `verify_export` and the watch tolerate them.
- `session_user` in the trigger context (S3) is **not** done here (it changes the audit trigger, a chain boundary):
  recorded as a known limit and a follow-up.

### Postgres (S2, D13, Z10, S6): Q-K137

- `log_connections=authorization` and `log_disconnections=on` server-wide, **plus** `log_min_messages=warning` and
  `log_destination=stderr` pinned with `-c` in the db command and in server-settings.txt. The every-minute host
  timer adds a metric that `postgresql.auto.conf` holds no setting (via `pg_file_settings`), with a critical alert
  `PostgresSettingOverridden`.
- No reliance on disconnection lines (D13). `JournalSuppressed` becomes critical and is named in the alert text (S6).
- The local proof measures the added lines per hour at two hospitals and extrapolates to 200 with the beat job count;
  the number and the limit it is compared with (the logs-volume budget) are recorded. If it exceeds the budget, the
  fallback (drop `ihr_app` service lines in Fluent Bit) becomes a decision, not a silent change.

### When the record cannot be written (D7, Z12, R9): Q-K138

- Refuse the command. **Break-glass:** `IHR_BREAK_GLASS="<reason>"` lets a job-tier `rollback`, `restore` or
  `hospital-restore` run when `logger` fails; the line goes to stderr and to `$OUT/operator-pending.log`, and the next
  recorded session ships the pending lines first as `event=late`. Data-tier commands never break glass.
- `.local` proofs and CI without `/dev/log`: `logger` failure refuses there too, except under `IHR_OPERATOR_LOCAL=stderr`
  set by the local-proof and CI paths, which write the lines to stderr. The bootstrap `--check` asserts journald
  `Storage=persistent`.

### Host audit, P1-T27c (Z9, S4, S10, D9): Q-K139

- Rules: `execve` of docker, ctr, runc, nsenter, psql with `-F auid>=1000 -F auid!=unset`, b64 and b32 twins; the
  docker socket watch only if a test proves it records; no `-e 2` (tamper is alerted); no rule for all root execs.
- Records: SYSCALL + EXECVE only, EXECVE arguments a0…a3 shaped like `args=` (flags kept, values `<v>`); PROCTITLE and
  PATH dropped.
- `HostAuditRulesMissing`: the root `ExecStartPre` writes a metric with the count of loaded `ihr-*` rule keys; alert
  when short. The bootstrap marker counts only with `uid:=0` (S4). No volume-based `HostAuditSilent`.
- Live proof waits for P1-T26 (sudo on a real host). Here: files, static tests, Fluent Bit cases.

### Also in scope (S8, D10)

- `audit-supersede export` is data tier, scope `all`.
- Keycloak admin console (`ops-tunnel`): stays tier `none` (it is the tunnel), but a log alert
  `KeycloakAdminCredentialChange` fires on Keycloak admin events that reset or remove a hospital user's credential
  outside our staff-reset flow, if the event line shape is already among the log cases; otherwise it is a stated
  limit and a follow-up. Say which in the build report.

### Split and order

- **P1-T27a** session record, grammar, tiers, Postgres settings and pins, alerts, redaction and alert cases, CI.
- **P1-T27b** `operator` entries (migration 0007, `ihr_audit/operator.py`, tests, mutation rows). Depends on T27a's
  environment (`IHR_OPERATOR_SESSION`, `IHR_OPERATOR_SCOPE`, `IHR_OPERATOR_REASON`, `IHR_OPERATOR` exported into the
  one-offs by T27a's wrappers).
- **P1-T27c** host audit files and static tests.
- Build T27a, then T27b and T27c in parallel; one review over all three (full shape); then the local live proof.
