# P1-T30 Operator surface: design

Source: `docs/reviews/phase-1-closing-review.md` PR1-8 (Low, blocks neither). Three parts: (a) an
ops freeze until PR1-1 (P1-T26, real-server dress rehearsal) is done, (b) a read-only
`scripts/staging.sh due` view, (c) alert tiers. Line numbers are as of commit 0dd8f9363.

## 1. Principles

1. **No second bookkeeping.** `due` reads only what the alerts already read (Prometheus series,
   the log store's fixed lines) or the operator access record (P1-T27). It writes nothing.
2. **`due` and the inbox never disagree.** A row is OVERDUE exactly when its alert's threshold is
   passed; the thresholds in `due` are pinned to the rule expressions by a test.
3. **No session.** `due` reaches none of the five wrappers (`admin_run`, `audit_supersede_run`,
   `migrate_run`, `app_exec`, `pg_su`; staging.sh:715-735, :843, and the Static tests,
   scripts/test_staging_operator.py:753-806), so it opens no operator session and writes no line,
   like `acme-proof` (test_staging_operator.py:921-927; JobTier test at :458).
4. **Freeze, not rewrite.** No subcommand is removed or renamed here.

## 2. Files

| File | Change |
|---|---|
| `scripts/staging.sh` | `due) due_cmd "${@:2}" ;;` in `main` (staging.sh:5772); `due_cmd()` near `alerts()` (:4979); a usage line; the usage printer `sed -n '5,105p'` (:5916) widened to the real header end (line 129: today it cuts the last lines) |
| `scripts/ops_due.py` (new, stdlib only, run with `python3 -I`) | the row table, query plan, evaluation, table rendering, exit code |
| `deploy/staging/subcommands.txt` (new) | the reviewed list of dispatcher labels: `<name>\t<task>\t<reason>` |
| `deploy/staging/overlays.txt` (new; if Q-K140 B) | the reviewed list of `deploy/staging/compose*.yaml` |
| `scripts/staging.sh` `monitoring_config()` (:4455-4594) | the routing tree of §5 |
| `deploy/monitoring/prometheus/rules/availability.yml:12` + `tests/availability.yml:15` | Watchdog `severity: none` → `info` (Q-K146) |
| `scripts/check-monitoring.sh` | `amtool config routes test` for every rule (§6.3) |
| `.pre-commit-config.yaml` | `monitoring-promtool` files filter (:244) adds `deploy/logs/alerts/`; new hook `staging-surface` runs `scripts/test_staging_surface.py` and `scripts/test_ops_due.py` |
| `scripts/test_staging_surface.py` (new) | freeze, severity and routing tests (§6) |
| `scripts/test_ops_due.py` (new) | `due` tests (§6) |
| `scripts/alert_tiers.py` (new, stdlib + PyYAML as the other tests) | `expected_receiver(labels, variant)` and the rule loader, used by §6 tests 3 and 4 |
| `scripts/test_staging_monitoring.py:549` | severity set becomes exactly {critical, warning, info}; `test_routing` (:662) updated to the new tree |
| `scripts/test_staging_logs.py:893-897` | the `notify="anytime"` route stays before the digest route |
| `deploy/staging/README.md` | a section "Operator surface (P1-T30)": the freeze note, `due`, the tiers; the Monitoring "Alerts" paragraph (:670-676) rewritten |
| `docs/TRACKER.md`, `docs/QUESTIONS.md` | P1-T30 row, Q-K140…Q-K147 |

## 3. (a) The freeze

- `deploy/staging/subcommands.txt` lists every label of `main`'s `case` (68 today plus `due`),
  each with the task that added it and one line of why; comment lines start with `#`. Header text:
  "Ops freeze (PR1-8): no new subcommand or ops subsystem until P1-T26 is ✅. A line added here
  needs the owner's approval (date) or a security fix (task ID)."
- Test `Freeze.test_dispatcher_labels_are_the_reviewed_list`: parse `func("main")` (the helper in
  test_staging_operator.py) for `^  <label>)` and `^  a|b)` alternations; the set must equal the
  file's names; every line has a non-empty task and reason; no duplicates.
- (Q-K140 B) `Freeze.test_overlays_are_the_reviewed_list`: `deploy/staging/compose*.yaml` equals
  `overlays.txt` (21 files today). Second-level verbs (`keys rotate`, `db-roles --assert`) are
  out of scope.
- README: "Operator surface (P1-T30)" opens with a CONTRIBUTING-style note: what the freeze
  covers, how to ask (a QUESTIONS.md entry, then the list line), when it lifts (P1-T26 ✅, owner),
  and that fixes, new options on existing subcommands and alert rules are not frozen.

## 4. (b) `scripts/staging.sh due`

**Flow.** `due_cmd` refuses unless `.env` has `IHR_MONITORING` (exit 2, "no monitoring here: no
source to read"). It runs `python3 -I scripts/ops_due.py plan --env <feature flags>`, which prints
`prom <id> <url-path>` and `logs <id> <url-path>` lines. For each, bash fetches with the existing
`prom_get "http://127.0.0.1:9090<path>"` (staging.sh:4739-4742, `compose exec -T prometheus`,
not a privileged service) or `vl_get "<path>"` (:4895-4908, the log password on stdin), writing
`$tmp/<id>.json` (`$tmp` = `mktemp -d`, mode 700, removed by a trap); a failed fetch writes
`$tmp/<id>.err`. Then `ops_due.py render --dir "$tmp" --now "$(date +%s)"` prints the table and
exits 0/1/2. Feature flags passed: `IHR_DUMPS`, `IHR_LOGS_ARCHIVE`, `IHR_ACME` (env_get).

**Rows** (`ROWS` in ops_due.py; one dict per row: kind, item, every, source queries, last-done
rule, due rule, alert name + threshold text, command):

| # | Row | Every | Source (file:line) | Last done | Due (= alert threshold) | Command shown |
|---|---|---|---|---|---|---|
| 1 | chore `dumps-expire` | weekly | `ihr_dump_oldest_object_timestamp_seconds` (deploy/dumps/dumper.py:1031, set :1154-1155); live check `up{job="dumps"}` | `-` (Q-K143) | oldest + 36 d (DumpErasureOverdue, rules/dumps.yml:119) | `scripts/staging.sh dumps-expire --dry-run`, then without (your machine) |
| 2 | chore `hospital-restore-expire` | daily | log store, the alert's own query: `_time:30m source:=container service:in(celery, celery-worker) "ihr-restore: parked="` (alerts/restore.yml:34-35; line from restore/engine.py:1983-1989 via tasks.py:161) | operator record: newest `event=start command=hospital-restore-expire` whose session has `event=end outcome=ok` (`source:=host service:="ihr-operator"`, `_time:180d`) | parked time (from the schema name, `PARKED_RE` engine.py:81) + 8 d (PARKED_DAYS+1, :82); no line = "none overdue" | `scripts/staging.sh hospital-restore-expire --dry-run`, then without |
| 3 | chore `logs-archive-expire` | monthly | `ihr_logs_archive_oldest_day_timestamp_seconds` (deploy/logs/archive/archive.py:454, set :531); live `up{job="logs-archive"}` | `-` (Q-K143) | oldest + 435 d (LogArchiveErasureOverdue, rules/logs.yml:144) | `scripts/staging.sh logs-archive-expire --dry-run`, then without (your machine) |
| 4 | drill `hospital-restore-drill` (= the dumps drill: it calls `dumps_exec drill-mark`, staging.sh:5762, :5765) | monthly | `ihr_dump_last_drill_timestamp_seconds` (dumper.py:1032, `drill_mark` :1618-1627, start :1652), `ihr_dump_first_dump_ever_timestamp_seconds` (:1029, :1150-1153) | last drill (0 = never) | last + 35 d, or first dump + 35 d when never (DumpDrillOverdue, rules/dumps.yml:105-110) | `scripts/staging.sh hospital-restore-drill` |
| 5 | drill `restore-drill` (pgBackRest copy) | monthly (README:309) | operator record: `command=restore-drill` start lines whose `args=` has neither `--keep` nor `--clean`, joined to `outcome=ok` end lines | that start's time | last + 35 d (no alert: mirrors row 4) | `scripts/staging.sh restore-drill` |
| 6 | drill quarterly live drill | quarterly (README:601-604) | operator record: `command=hospital-restore hospital=drill`, end `outcome=ok` | that start's time | last + 92 d (no alert) | `scripts/staging.sh hospital-restore drill --dump latest --reason "quarterly live drill"` |
| 7-9 | key `client key <purpose>` for backend, onboarding, staff (`KEY_PURPOSES`, scripts/client-keys.sh:29) | 180 d (README:244) | `ihr_client_key_active_age_days{purpose}`, `ihr_client_key_active`, `ihr_client_keys_checked_timestamp_seconds` (written by `monitoring_key_metrics`, staging.sh:4712-4737) | now − age | last + 150 d (ClientKeyAge warning, time-certs-keys.yml:83); checked > 3 h ago = unreadable (ClientKeyCheckStale :106) | `scripts/staging.sh keys rotate <purpose>` |
| 10-11 | key `TLS certificate <endpoint>` (auth, status) | automatic | `probe_ssl_earliest_cert_expiry{job="probe-public"}` (time-certs-keys.yml:54-55) | `-` | expiry − 21 d (TlsCertExpiry warning) | `scripts/staging.sh cert`; `acme-retry` if stuck |
| 12 | key `other secrets` (S3 file and picture keys, dump key, archive key, events key, Keycloak bootstrap admin, alert mail token) | no policy | none (Q-K142) | `-` | `-` | `-` (state `no-policy`) |

Rows 1 and 3 are `off` when `IHR_DUMPS` / `IHR_LOGS_ARCHIVE` is empty; rows 10-11 use the
self-signed probe module before `acme-init` and still show the expiry. Operator-record rows read
180 days (the log store's span); an older or absent run shows `never seen` and is OVERDUE.

**States.** `ok`; `soon` (due within 7 days); `OVERDUE` (now ≥ due); `off` (feature not on here);
`no-policy`; `UNREADABLE` (fetch failed, a live check is 0 or absent, the key check stale, or a JSON
shape unexpected). A metric that is absent while its source is live means "nothing yet" (e.g. no
dump in the bucket): `ok` with due `-`.

**Output** (plain text, fixed-width columns, no colour, ASCII; every time IST `DD-MM-YYYY HH:MM`;
nothing personal: no slug, no operator name, no schema name beyond its date):

```
IndieHR due list at 23-11-2026 10:15 IST
KIND   ITEM                          EVERY      LAST DONE         DUE               STATE    COMMAND
chore  dumps-expire                  weekly     -                 08-12-2026 05:30  ok       scripts/staging.sh dumps-expire --dry-run, then without (your machine)
drill  hospital-restore-drill        monthly    02-11-2026 11:40  07-12-2026 11:40  soon     scripts/staging.sh hospital-restore-drill
key    client key backend            180 days   01-06-2026 00:00  29-10-2026 00:00  OVERDUE  scripts/staging.sh keys rotate backend
...
2 overdue, 1 due within 7 days, 0 unreadable. Exit 1.
```

Order: OVERDUE, UNREADABLE, soon, ok, off, no-policy; then kind, item. **Exit codes:** 0 nothing
overdue or unreadable; 1 something overdue; 2 a source unreadable (2 wins over 1; overdue rows
are still printed). Usage error: 2. No options in this task (no `--json`).

## 5. (c) Alert tiers

**Today** (staging.sh:4510-4545): critical → `email` at once, every 4 h; `notify="anytime"` →
`email` at once, daily (Q-O9); warning → `email` 08:00-22:00 IST, daily per alert name; info →
the root `email` (MrdRecordsOffHours is mailed at once by owner decision Q-K36 A); Watchdog,
DailyHeartbeat, AlertmanagerNotificationsFailing have their own routes. vmalert's log alerts
(deploy/logs/alerts, 12 files) **do go through the same Alertmanager**:
`-notifier.url=http://${IHR_ALERTMANAGER_IP:-10.203.0.20}:9093` (deploy/staging/compose.logs.yaml:140).
160 rules in all (129 Prometheus, 31 log).

**New tree** (first match wins; order fixed by test):

| # | Matchers | Receiver | Timing |
|---|---|---|---|
| 1 | `alertname="Watchdog"` | `deadman` / `blackhole` | unchanged |
| 2 | `alertname="DailyHeartbeat"` | `email-heartbeat` | unchanged |
| 3 | `alertname="AlertmanagerNotificationsFailing"`, continue | `deadman-fail` / `blackhole` | unchanged |
| 4 | `severity="critical"` | `page` | group by alertname, severity; repeat 4 h, any hour |
| 5 | `notify="anytime"` | `notice` (Q-K144) | repeat 24 h, any hour |
| 6 | `alertname="MrdRecordsOffHours"` | `notice` (Q-K144) | repeat 24 h, any hour |
| 7 | `severity=~"warning\|info"` | `digest` | below |
| root | anything else | `page` | an unknown severity is loud (the test keeps it empty) |

**Receivers** (all over the existing global SMTP settings, staging.sh:4502-4509: ZeptoMail
`smtp.zeptomail.in:587`, STARTTLS required, `smtp_auth_password_file`, sender `IHR_ALERT_FROM`,
recipient `IHR_ALERT_TO` from `monitoring-init`, README:697; the proof uses the Mailpit stand-in):
- `page`: the old `email` receiver, subject `[IndieHR PAGE] <alertname> (critical)`,
  `send_resolved: true` (Q-K145 for the channel).
- `notice`: the same mail, subject `[IndieHR] <alertname> (<severity>)`.
- `digest`: `group_by: [severity]` (so at most one warning digest and one info digest a day),
  `group_wait: 30s`, `group_interval: 15m`, `repeat_interval: 23h`,
  `active_time_intervals: [digest-ist]` (`09:00`-`09:30`, `Asia/Kolkata`), `send_resolved: false`,
  subject `[IndieHR digest] {{ .Alerts.Firing | len }} {{ .CommonLabels.severity }} alert(s) firing`;
  the default e-mail body lists each alert's labels and summary (no new template file).
  Mechanism: outside the window the time stage drops the flush; the first flush inside it (one
  of the 15-min ticks lands in a 30-min window) mails the whole group; later ticks that day send
  only if the firing set changed; the next day's window resends (23 h passed).
- `deadman`, `deadman-fail`, `email-heartbeat`, `blackhole`: unchanged.
- The `daytime-ist` interval goes; `inhibit_rules` (:4547-4556) stay as they are.

**What the digest loses (stated in README):** a warning that fires and resolves between two
windows is never mailed (Alertmanager's history and `staging.sh alerts` still show it while it
fires; the log store keeps vmalert's lines). A warning can wait up to 24 h for its mail.

## 6. Tests

1. `test_staging_surface.Freeze`: §3 (list equality, reasons, overlays if B; README note present).
2. `test_staging_surface.Severity`: every rule in `deploy/monitoring/prometheus/rules/*.yml` and
   `deploy/logs/alerts/*.yml` has `labels.severity` in exactly {critical, warning, info}.
3. `test_staging_surface.Routing` (no Docker): render with `monitoring-render` into a temp
   `IHR_STAGING_DIR` (as test_staging_monitoring.py:629-640 does), evaluate the tree first-match in
   Python for every rule's static labels, and compare with the expected receiver table of §5;
   check the digest route's timing keys and the window, `send_resolved` per receiver, and the order
   of the routes (5 and 6 before 7).
4. `scripts/check-monitoring.sh` (Docker, the pinned `prom/alertmanager:v0.35.0`): for each
   rendered variant (`on yes`, `on no`, `test no`), one `sh` run inside the image executes a
   generated list of `amtool config routes test --config.file=/c/alertmanager.yml
   --verify.receivers=<expected> alertname=<A> severity=<S> [notify=anytime]` lines, one per rule
   (the expected receiver comes from a small `scripts/alert_tiers.py`, the table test 3 also
   uses). Any mismatch fails the hook.
5. `test_ops_due` (pure Python, fixture JSON in the test): IST and DD-MM-YYYY rendering; each row's
   states; exit 0/1/2 and 2-wins; `off` rows; a stale client-key check → 2; absent series with a
   live source → `ok`; the operator-record join (an unended or failed session never counts;
   `--keep` runs excluded); every row's `alert` threshold text appears verbatim in that rule's
   `expr` (e.g. `36 * 86400` in DumpErasureOverdue); every command's subcommand is in
   `subcommands.txt`; no output line has an `@`, a slug pattern or an operator name.
6. `test_ops_due.Static`: `func("due_cmd")` contains none of `op_need`, `admin_run`, `admin_manage`,
   `migrate_run`, `app_exec`, `pg_su`, `audit_supersede_run`, `restore_manage`, `kt `; `op_tier due`
   is `job`; and with the operator test's `Stack` stub (test_staging_operator.py:95), `due` writes
   no `ihr-operator:` line (like :458).
7. Existing suites still pass: test_staging_monitoring, test_staging_logs, test_staging_operator,
   promtool rule tests (Watchdog's expected label changes), `pre-commit run --all-files`.

**Done when:** tests 1-6 fail before the change and pass after; `scripts/check-monitoring.sh`
passes; on a local proof (`local-proof`) `due` prints all rows with exit 0, and stopping the
dumper container makes rows 1 and 4 `UNREADABLE` with exit 2; `alert-test` (staging.sh:4996)
still mails (critical path), and a warning fired by hand reaches Mailpit only inside the window
(checked by rendering a throwaway copy whose window is moved to the next minutes; never in code).

## 7. Out of scope

New subcommands besides `due`; renaming or merging subcommands; `--json`; a pager or SMS
service; rotation policies or rotation records for row 12's secrets; new alert rules (rows 5-6
have none); changing any rule's severity except Watchdog's; secret-count reduction; README
shortening; P1-T26 itself.

## 8. Assumptions (each checkable)

- A1: vmalert notifies the same Alertmanager: compose.logs.yaml:140 has `-notifier.url=…:9093`.
- A2: Alertmanager v0.35.0 supports `time_intervals`, `active_time_intervals` and `amtool config
  routes test --verify.receivers`: `amtool config routes test --help` in the pinned image.
- A3: the time stage drops (does not queue) a flush outside the window: proven in the proof by a
  warning fired at 10:00 that arrives at the next window, not before.
- A4: `prom_get` and `vl_get` open no session: neither body names a wrapper (staging.sh:4739-4742,
  :4895-4908), and test_staging_operator's Static tests pass with `due` added.
- A5: the operator lines reach the log store with `source:=host service:="ihr-operator"` (as
  alerts/operator.yml queries them) and keep 180 days (README:975-983).
- A6: the job start line of `hospital-restore drill …` carries `hospital=drill` (op_job_scope,
  staging.sh:409-415).
- A7: `python3` (3.10+) is on the server (staging.sh already runs `python3 -c` on the host).
- A8: no rule has a severity outside {critical, warning, info} once Watchdog is relabelled
  (today only Watchdog has `none`).

## 9. Decisions (proposed Q-IDs; auto-accept the recommendation in an unattended run)

- **Q-K140 Freeze scope.** A: top-level subcommands only. B: A + the compose overlay list.
  C: B + second-level verbs. **Rec. B**: a new subsystem arrives as an overlay plus commands.
- **Q-K141 `due` without a session reading the log store.** A: no session (fixed queries, no
  user input, prints dates only). B: data tier with `--reason`. **Rec. A** (the user's own brief
  asks for a no-session command like acme-proof).
- **Q-K142 Secrets with no rotation policy** (row 12). A: listed as `no-policy`, never in the exit
  code. B: secret file mtimes as ages (unreliable: copies reset them). C: set cadences and record
  rotations (new bookkeeping: after the freeze). **Rec. A**, revisit with P1-T26.
- **Q-K143 "Last done" of the erasures run from the owner's machine.** A: `-`, due from the oldest
  object (alert parity). B: the dumper and the archiver export `last_erasure_timestamp_seconds`
  from the erasure records they already read. **Rec. A** now; B is a small later change.
- **Q-K144 Non-critical alerts mailed at once today** (Q-O9 sign-in abuse, Q-K36 A
  MrdRecordsOffHours). A: keep them immediate through `notice`. B: fold into the digest (reverses
  owner decisions). C: page them. **Rec. A.**
- **Q-K145 Page channel.** A: the same mailbox, subject `[IndieHR PAGE]` for a phone mail rule.
  B: an optional second address `IHR_ALERT_PAGE_TO` (an option of `monitoring-init`, default the
  first). C: a push or SMS service (new host and secret: frozen). **Rec. A**; B if the owner asks.
- **Q-K146 Watchdog's severity `none`.** A: relabel `info` (routed by name first). B: keep `none`,
  exempt by name in the test. **Rec. A.**
- **Q-K147 Digest timing.** A: one window 09:00-09:30 IST, grouped by severity. B: two windows
  (09:00 and 17:00). C: keep per-alert warning mails 08:00-22:00 (today). **Rec. A.**

## 10. Attack and orchestrator decision (2026-11-23)

Attack: EGM `egm-design`, `egm-zoom-out`, `egm-implementation-readiness` (Sonnet high, workflow wf_62fd5a1c-538), 36
findings. All three lenses found the same two breaks and the same over-build:

- **Break 1:** moving `info` into the digest silences `staging.sh alert-test` (it sends `severity=info`, README:702,
  test_staging_monitoring.py:201) and `MrdRecordsOffHours` (records.yml, `severity: info`, immediate by owner decision).
- **Break 2:** `scripts/ops_due.py` would never reach the server: deploy.sh ships an explicit file list
  (deploy.sh:356-361).
- **Over-build:** "due" computed from alert thresholds is a second bookkeeping; the operator-record join, `no-policy`
  rows, `overlays.txt`, reasons per subcommand and a new `notice` receiver add surface the finding (PR1-8) asked to cut.

**Decision (binding for the build; supersedes §3-§6 and §9 where they differ):**

1. **Freeze (Q-K140 A).** `deploy/staging/subcommands.txt`: today's dispatcher labels plus `due`, names only, one per
   line, a header saying the freeze holds until PR1-1 (P1-T26 dress rehearsal) is ✅ and that a new name needs the
   owner's OK in the commit message. A test parses `main`'s case labels and requires equality with the file. No
   overlay list, no per-line reasons. README "Operator surface (P1-T30)" states the rule.
2. **`due` (Q-K141 A).** Read-only, no operator session (reaches none of the five wrappers; a static test and a Stack
   test prove no `ihr-operator` line). One bash `due_cmd` makes a few `prom_get` calls into a mode-700 temp dir (removed
   on return) and pipes them to `python3 -I scripts/ops_due.py render <dir>`; `scripts/ops_due.py` is added to
   deploy.sh's shipped list with a test that every `scripts/*.py` staging.sh invokes is shipped. Rows:
   - chores: dumps-expire (`DumpErasureOverdue`), hospital-restore-expire (`ParkedSchemaOverdue`),
     logs-archive-expire (`LogArchiveErasureOverdue`); drills: `DumpDrillOverdue` and any other drill that already has
     an alert; key ages: client keys (`ClientKeyAge`, `ClientKeyCheckStale`), TLS expiry; each with the plain gauge
     the alert reads shown as context (oldest object, last drill, age, expiry), in IST, DD-MM-YYYY.
   - state per row: `OVERDUE` = the named alert is firing now (Prometheus `ALERTS{alertstate="firing"}`; log alerts
     read through Alertmanager's alert list, which already holds them), `ok` otherwise, `off` when the feature is
     switched off (its env flag), `UNREADABLE` when a source does not answer. No `soon`, no computed due dates, no
     log-store or operator-record queries, no `no-policy` rows (the README names the secrets without a rotation policy).
   - exit 0 nothing firing, 1 something firing, 2 a source unreadable (2 wins). Every row also prints the command to run.
3. **Alert tiers (Q-K142 A).** Smallest change to the existing tree (staging.sh `monitoring_config`):
   - critical → a new `page` receiver (same mailbox, subject prefixed `[IndieHR PAGE]`), immediate, repeat 4 h;
   - `notify="anytime"` and every `info` alert (alert-test, MrdRecordsOffHours, Watchdog stays on its own route) →
     unchanged, immediate;
   - warning → a `digest` receiver: `group_by: [severity]`, so one mail lists every warning; sent only in two windows,
     09:00-09:30 and 17:00-17:30 IST (`active_time_intervals`), `group_interval` 15m, `repeat_interval` 23h, resolved
     not mailed. Worst-case delay of a warning's first mail: about 16 h (a warning starting at 17:31). Warnings that
     must not wait get `notify: anytime` in their rule; the builder lists any it changes, none by default.
   - the README states what the digest loses (a warning that fires and clears between windows is never mailed).
4. **Tests.** Every rule has `severity` in {critical, warning, info} (Watchdog `none` exempt by name). `amtool config
   routes test --verify.receivers` on a fixed matrix (critical, warning, info, notify=anytime, Watchdog,
   DailyHeartbeat, AlertmanagerNotificationsFailing, MrdRecordsOffHours, IhrAlertTest) inside check-monitoring.sh,
   whose hook filter gains `deploy/logs/alerts/`. Fixture tests of `ops_due.py` render (exit codes, IST, firing,
   unreadable, off). The usage printer's `sed` range covers the whole header (side fix).
5. **Proof.** The window behaviour (Alertmanager v0.35 drops a send outside the window and sends at the next flush
   inside it) is a stated assumption, checked by the reviewer against the Alertmanager source or docs, and live in
   P1-T26.
