"""Static checks of metrics and e-mail alerts (P1-T9a): compose.monitoring*.yaml, the monitoring
settings in deploy/monitoring/, Traefik's metrics and status routes, the exporters' narrow
database and Redis users, the host timer's script, staging.sh's monitoring commands, and the
Postgres log settings that keep query values out of the logs.

The rules themselves are unit-tested by promtool (scripts/check-monitoring.sh); the live proof is
`scripts/staging.sh local-proof` plus the steps in deploy/staging/README.md ("Monitoring and
alerts"). Run: python3 -m unittest scripts/test_staging_monitoring.py
"""

import ast
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "deploy" / "staging"
MON = ROOT / "deploy" / "monitoring"


class _Loader(yaml.SafeLoader):
    pass


# Compose's merge tags (!override, !reset) in the dev overrides: read as the plain value
_Loader.add_multi_constructor("!", lambda loader, suffix, node: (
    loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode)
    else loader.construct_mapping(node) if isinstance(node, yaml.MappingNode)
    else loader.construct_scalar(node)))


def load(path):
    return yaml.load(Path(path).read_text(), Loader=_Loader)


BASE = load(STAGING / "compose.yaml")
ACME = load(STAGING / "compose.acme.yaml")
OVERLAY = load(STAGING / "compose.monitoring.yaml")
TEST_OVERLAY = load(STAGING / "compose.monitoring.test.yaml")
ALL_COMPOSE = {f.name: load(f) for f in sorted(STAGING.glob("compose*.yaml"))}
DEV = load(ROOT / "deploy" / "dev" / "care-local.override.yaml")
ROUTES_TEXT = (STAGING / "traefik" / "routes.yml").read_text()
ROUTES = yaml.safe_load(ROUTES_TEXT)
PROMETHEUS = load(MON / "prometheus" / "prometheus.yml")
RULE_FILES = sorted((MON / "prometheus" / "rules").glob("*.yml"))
TEST_FILES = sorted((MON / "prometheus" / "tests").glob("*.yml"))
STAGING_SH = (ROOT / "scripts" / "staging.sh").read_text()
HOST_METRICS = (MON / "host-metrics.sh").read_text()
CHECK_SH = (ROOT / "scripts" / "check-monitoring.sh").read_text()
DEPLOY_SH = (ROOT / "scripts" / "deploy.sh").read_text()
PRECOMMIT = (ROOT / ".pre-commit-config.yaml").read_text()
PINNED = re.compile(r"^[a-z0-9./_-]+:v?[\w.-]+@sha256:[0-9a-f]{64}$")


def func(name):
    """The body of a shell function of staging.sh."""
    return STAGING_SH.split(f"\n{name}() {{", 1)[1].split("\n}\n", 1)[0]


def members(network):
    """Every (file, service) of any compose file that joins the network."""
    out = set()
    for fname, doc in ALL_COMPOSE.items():
        for name, svc in (doc.get("services") or {}).items():
            if network in (svc.get("networks") or []):
                out.add((fname, name))
    return out


def rules():
    for f in RULE_FILES:
        for group in load(f)["groups"]:
            for rule in group["rules"]:
                yield f.name, rule


class PostgresLogPrivacy(unittest.TestCase):
    """Django binds parameters on the client: a logged statement carries the values typed in
    searches (names, phone numbers). Fixed first in P1-T9a."""

    WANT = ("log_min_duration_statement=-1", "log_min_error_statement=panic", "log_error_verbosity=terse")

    def test_staging_database(self):
        cmd = BASE["services"]["db"]["command"]
        for setting in self.WANT:
            self.assertIn(setting, cmd)
        joined = " ".join(cmd)
        self.assertNotIn("log_statement", joined)
        self.assertNotIn("log_min_duration_statement=2000", joined)
        self.assertNotRegex(joined, r"log_min_duration_statement=(?!-1\b)")

    def test_dev_database(self):
        cmd = DEV["services"]["db"]["command"]
        for setting in self.WANT:
            self.assertIn(setting, cmd)

    def test_restored_copies_log_the_same_way(self):
        self.assertIn("PG_LOG_PRIVACY=(-c log_min_duration_statement=-1 -c log_min_error_statement=panic -c log_error_verbosity=terse)", STAGING_SH)
        runs = re.findall(r"pg_container [^\n]*\n?[^\n]*postgres -c archive_mode=[^\n]*\n[^\n]*", STAGING_SH)
        self.assertEqual(len(runs), 2, runs)
        for run in runs:
            self.assertIn('"${PG_LOG_PRIVACY[@]}"', run)


class Hardening(unittest.TestCase):
    def services(self):
        return OVERLAY["services"].items()

    def test_every_service_pinned_and_hardened(self):
        for name, svc in self.services():
            self.assertRegex(svc["image"], PINNED, name)
            self.assertEqual(svc["cap_drop"], ["ALL"], name)
            self.assertIn("no-new-privileges:true", svc["security_opt"], name)
            if name == "alertmanager-data":  # one-off owner fix, no network
                self.assertEqual(sorted(svc["cap_add"]), ["CHOWN", "FOWNER"])
                self.assertEqual(svc["network_mode"], "none")
                continue
            self.assertNotIn("cap_add", svc, name)
            self.assertTrue(svc["read_only"], name)
            self.assertIn("mem_limit", svc, name)
            self.assertEqual(svc["logging"]["driver"], "${IHR_LOG_DRIVER:-journald}", name)
            self.assertNotIn("privileged", svc, name)
            self.assertNotIn("network_mode", svc, name)
            self.assertNotIn("pid", svc, name)
            user = svc["user"]
            self.assertNotIn(user.split(":")[0], ("0", "root"), name)

    def test_no_docker_socket_and_nothing_published(self):
        for f in sorted(STAGING.glob("compose*.yaml")):
            self.assertNotIn("docker.sock", f.read_text(), f.name)
        for doc in (OVERLAY, TEST_OVERLAY):
            for name, svc in doc["services"].items():
                self.assertNotIn("ports", svc, name)
                self.assertNotIn("expose", svc, name)

    def test_prometheus_on_the_monitoring_and_alerting_networks_only(self):
        prom = OVERLAY["services"]["prometheus"]
        self.assertEqual(sorted(prom["networks"]), ["alerting", "monitoring"])
        self.assertEqual(prom["networks"]["monitoring"]["ipv4_address"], "${IHR_PROMETHEUS_IP:-10.203.0.70}")
        net = BASE["networks"]["monitoring"]
        self.assertTrue(net["internal"])
        cfg = net["ipam"]["config"][0]
        self.assertEqual(cfg["subnet"], "${IHR_MONITORING_NET:-10.203.0.64/26}")
        # the fixed addresses lie outside what Docker hands out
        self.assertEqual(cfg["ip_range"], "${IHR_MONITORING_DYNAMIC:-10.203.0.96/27}")
        cmd = prom["command"]
        self.assertIn("--storage.tsdb.retention.time=${IHR_PROMETHEUS_RETENTION:-30d}", cmd)
        self.assertIn("--storage.tsdb.retention.size=${IHR_PROMETHEUS_MAX_SIZE:-4GB}", cmd)
        for flag in ("--web.enable-admin-api", "--web.enable-lifecycle", "--web.enable-remote-write-receiver"):
            self.assertFalse([c for c in cmd if c.startswith(flag)], flag)

    def test_only_alertmanager_has_a_way_out(self):
        for name, svc in self.services():
            nets = list(svc.get("networks") or [])
            if name == "alertmanager":
                self.assertEqual(sorted(nets), ["alerting", "alertmail"])
            else:
                self.assertNotIn("edge", nets, name)
                self.assertNotIn("alertmail", nets, name)

    def test_alertmanager_reachable_by_prometheus_alone(self):
        """Its API (silences, alerts) has no login: no web-app, Traefik or scrape-target container
        may reach it. P1-T9a review."""
        nets = OVERLAY["networks"]
        self.assertTrue(nets["alerting"]["internal"])
        self.assertEqual(nets["alerting"]["ipam"]["config"][0]["subnet"], "${IHR_ALERTING_NET:-10.203.0.16/28}")
        self.assertEqual(nets["alerting"]["ipam"]["config"][0]["ip_range"], "${IHR_ALERTING_DYNAMIC:-10.203.0.24/29}")
        self.assertNotIn("internal", nets["alertmail"] or {})  # its way out
        # alerting: Prometheus and Alertmanager (and the dead-man stand-in, in a proof only)
        self.assertEqual(members("alerting"), {("compose.monitoring.yaml", "prometheus"),
                                               ("compose.monitoring.yaml", "alertmanager"),
                                               ("compose.monitoring.test.yaml", "heartbeat-stub")})
        # alertmail: Alertmanager alone (and the mail stand-in, in a proof only)
        self.assertEqual(members("alertmail"), {("compose.monitoring.yaml", "alertmanager"),
                                                ("compose.monitoring.test.yaml", "smtp-test")})
        self.assertEqual(TEST_OVERLAY["services"]["smtp-test"]["networks"]["alertmail"]["aliases"], ["mail.ihr-proof.test"])
        # Alertmanager joins nothing else in any file (a proof neither)
        for fname, doc in ALL_COMPOSE.items():
            am = (doc.get("services") or {}).get("alertmanager") or {}
            self.assertLessEqual(set(am.get("networks") or []), {"alerting", "alertmail"}, fname)
        am = OVERLAY["services"]["alertmanager"]
        self.assertEqual(am["networks"]["alerting"]["ipv4_address"], "${IHR_ALERTMANAGER_IP:-10.203.0.20}")
        # it listens on that address only (alertmail is a bridge the host reaches)
        self.assertIn("--web.listen-address=${IHR_ALERTMANAGER_IP:-10.203.0.20}:9093", am["command"])
        self.assertIn("http://${IHR_ALERTMANAGER_IP:-10.203.0.20}:9093/-/ready", am["healthcheck"]["test"])
        # everything that talks to it uses that address
        self.assertIn('--alertmanager.url="http://$ip:9093"', func("am_tool"))
        self.assertNotIn("--alertmanager.url=http://127.0.0.1", STAGING_SH)
        self.assertEqual(STAGING_SH.count("amtool --alertmanager.url="), 1)  # am_tool only
        self.assertIn("am_tool alert add IhrAlertTest", func("alert_test"))
        self.assertIn("am_tool alert query --active", func("alerts"))
        self.assertIn('am="$(am_ip)"', func("ops_tunnel"))
        self.assertIn('v="${v:-10.203.0.20}"', func("am_ip"))
        self.assertIn('targets: ["alertmanager:9093"]', (MON / "prometheus" / "prometheus.yml").read_text())

    def test_acme_dns_probed_on_its_own_network(self):
        """The internet-facing acme-dns shares a network with blackbox-exporter alone."""
        self.assertTrue(BASE["networks"]["dnsprobe"]["internal"])
        self.assertEqual(members("dnsprobe"), {("compose.acme.yaml", "acme-dns"),
                                               ("compose.monitoring.yaml", "blackbox-exporter")})
        self.assertEqual(members("monitoring") & {(f, "acme-dns") for f in ALL_COMPOSE}, set())

    def test_alertmanager_no_cluster_and_secrets_as_files(self):
        am = OVERLAY["services"]["alertmanager"]
        self.assertIn("--cluster.listen-address=", am["command"])
        self.assertEqual(am["user"], "${IHR_SECRETS_UID:?}:${IHR_SECRETS_GID:?}")
        mounts = [v for v in am["volumes"] if v.startswith("./secrets/")]
        self.assertEqual(len(mounts), 3)
        for v in mounts:
            self.assertTrue(v.endswith(":ro"), v)
            self.assertIn(":/run/ihr-alerts/", v)
        self.assertNotIn("env_file", am)
        # staging.sh sets the owner of secrets/ for every compose call
        self.assertIn('IHR_SECRETS_UID="$(id -u)"; IHR_SECRETS_GID="$(id -g)"', func("compose"))

    def test_postgres_exporter_reads_no_hospital_schemas(self):
        pe = OVERLAY["services"]["postgres-exporter"]
        for flag in ("--no-collector.stat_user_tables", "--no-collector.statio_user_tables",
                     "--no-collector.statio_user_indexes", "--no-collector.stat_statements"):
            self.assertIn(flag, pe["command"])
        self.assertFalse([c for c in pe["command"] if "auto-discover" in c or "extend.query" in c])
        self.assertEqual(pe["env_file"], ["secrets/postgres-exporter.env"])
        role = func("monitoring_db_role")
        self.assertIn("GRANT pg_monitor TO ihr_monitor", role)
        # initdb revokes CONNECT from PUBLIC: without this the exporter reports pg_up 0
        self.assertIn("GRANT CONNECT ON DATABASE postgres TO ihr_monitor", role)
        self.assertIn("NOSUPERUSER", role)
        # only pg_monitor, and CONNECT to the postgres database (never ihr or keycloak)
        self.assertNotRegex(role, r"GRANT (?!pg_monitor |CONNECT ON DATABASE postgres TO ihr_monitor;)")
        # the password reaches psql on stdin (a heredoc), never as an argument
        self.assertIn("psql -U postgres -d postgres -v ON_ERROR_STOP=1 -q >/dev/null <<SQL", role)

    def test_redis_exporter_least_access(self):
        re_ = OVERLAY["services"]["redis-exporter"]
        cmd = re_["command"]
        for flag in ("-config-command=-", "-disable-scrape-endpoint", "-redis.user=ihr-monitor",
                     "-check-single-keys=db0=celery", "-disable-exporting-key-values"):
            self.assertIn(flag, cmd)
        joined = " ".join(cmd)
        self.assertNotIn("password", joined)  # from the env file only
        self.assertNotIn("debug", joined)  # --debug prints the password
        self.assertNotIn("check-keys=", joined)  # never key patterns
        redis_cmd = BASE["services"]["redis"]["command"][-1]
        acl = re.search(r"user ihr-monitor (.*)\}", redis_cmd).group(1)
        self.assertIn("#$$IHR_REDIS_MONITOR_SHA256", acl)  # only the hash reaches Redis
        self.assertIn("resetkeys ~celery ", acl)
        self.assertIn("-@all", acl)
        granted = re.findall(r"\+([a-z|@]+)", acl)
        self.assertEqual(sorted(granted), sorted(["ping", "info", "select", "type", "llen", "memory|usage"]))
        self.assertNotIn("~*", acl)
        init = func("monitoring_init")
        self.assertIn("IHR_REDIS_MONITOR_SHA256=%s", init)
        self.assertIn("sha256sum", init)


class TraefikMetricsAndStatus(unittest.TestCase):
    METRICS = ["--entrypoints.metrics.address=:8083", "--metrics.prometheus=true",
               "--metrics.prometheus.manualrouting=true", "--metrics.prometheus.addRoutersLabels=true"]

    def test_metrics_flags_in_both_command_lists(self):
        for cmd in (BASE["services"]["traefik"]["command"], ACME["services"]["traefik"]["command"]):
            for flag in self.METRICS:
                self.assertIn(flag, cmd)
            self.assertNotIn("--api.insecure=true", cmd)

    def test_metrics_port_never_published(self):
        for port in BASE["services"]["traefik"]["ports"]:
            self.assertNotIn("8083", port)

    def test_metrics_router_answers_prometheus_only(self):
        r = ROUTES["http"]["routers"]["metrics"]
        self.assertEqual(r["entryPoints"], ["metrics"])
        self.assertEqual(r["service"], "prometheus@internal")
        self.assertEqual(r["middlewares"], ["prometheus-only"])
        allow = ROUTES["http"]["middlewares"]["prometheus-only"]["ipAllowList"]
        self.assertEqual(allow["sourceRange"], ['{{ env "IHR_PROMETHEUS_IP" }}/32'])
        self.assertNotIn("ipStrategy", allow)  # the connecting address, never a forwarded header
        self.assertEqual(BASE["services"]["traefik"]["environment"]["IHR_PROMETHEUS_IP"], "${IHR_PROMETHEUS_IP:-10.203.0.70}")
        # no other router on the metrics port, and the metrics router on no other port
        for name, router in ROUTES["http"]["routers"].items():
            if name != "metrics":
                self.assertNotIn("metrics", router["entryPoints"], name)

    def test_status_is_traefiks_ping_only(self):
        routers = ROUTES["http"]["routers"]
        status = routers["status"]
        self.assertEqual(status["rule"], 'Host(`status.{{ env "IHR_TENANT_DOMAIN_SUFFIX" }}`) && Path(`/healthz`)')
        self.assertEqual(status["service"], "ping@internal")
        self.assertEqual(status["middlewares"], ["status-ratelimit", "status-path"])
        self.assertEqual(ROUTES["http"]["middlewares"]["status-path"]["replacePath"]["path"], "/ping")
        # status. and ops. match the hospitals' pattern: closed above the hospitals, status above closed
        closed = routers["closed"]
        for host in ("console", "auth", "status", "ops"):
            self.assertIn(f'Host(`{host}.{{{{ env "IHR_TENANT_DOMAIN_SUFFIX" }}}}`)', closed["rule"])
        self.assertGreater(closed["priority"], routers["hospital-auth"]["priority"])
        self.assertGreater(status["priority"], closed["priority"])
        self.assertEqual(closed["service"], "noop@internal")

    def test_probes_reach_public_names_through_traefik(self):
        aliases = BASE["services"]["traefik"]["networks"]["monitoring"]["aliases"]
        self.assertEqual(aliases, ["auth.${IHR_TENANT_DOMAIN_SUFFIX:?}", "status.${IHR_TENANT_DOMAIN_SUFFIX:?}"])
        self.assertIn("monitoring", BASE["services"]["keycloak"]["networks"])
        self.assertIn("monitoring", BASE["services"]["backend"]["networks"])
        self.assertIn("dnsprobe", ACME["services"]["acme-dns"]["networks"])
        self.assertNotIn("monitoring", ACME["services"]["acme-dns"]["networks"])

    def test_platform_hosts_are_reserved_slugs(self):
        tree = ast.parse((ROOT / "plugins/ihr_platform/ihr_platform/models.py").read_text())
        reserved = next(ast.literal_eval(n.value) for n in tree.body
                        if isinstance(n, ast.Assign) and n.targets[0].id == "RESERVED_SLUGS")
        hosts = set(re.findall(r"Host\(`([a-z0-9-]+)\.\{\{", ROUTES_TEXT))
        self.assertTrue({"console", "auth", "files", "status", "ops"} <= hosts | {"ops"})
        self.assertLessEqual(hosts | {"ops"}, reserved)


class PrometheusSettings(unittest.TestCase):
    def test_jobs(self):
        jobs = {j["job_name"]: j for j in PROMETHEUS["scrape_configs"]}
        self.assertEqual(set(jobs), {"prometheus", "alertmanager", "node", "blackbox", "postgres", "redis",
                                     "traefik", "keycloak", "s3", "probe-public", "probe-internal", "probe-dns"})
        self.assertEqual(jobs["traefik"]["static_configs"][0]["targets"], ["traefik:8083"])
        self.assertEqual(jobs["keycloak"]["static_configs"][0]["targets"], ["keycloak:9000"])
        for j in jobs.values():
            self.assertNotIn("basic_auth", j)
            self.assertNotIn("honor_labels", j)

    def test_rule_labels_carry_no_personal_data(self):
        banned = {"ip", "ipaddress", "client", "clienthost", "user", "username", "userid", "email",
                  "address", "hospital", "slug", "schema", "path", "url", "query"}
        for fname, rule in rules():
            name = rule["alert"]
            self.assertTrue(re.fullmatch(r"[A-Z][A-Za-z0-9]+", name), name)
            labels = rule.get("labels") or {}
            self.assertIn(labels.get("severity"), {"critical", "warning", "info", "none"}, name)
            self.assertFalse(banned & {k.lower() for k in labels}, name)
            self.assertFalse(re.search(r"by \((?:[^)]*\b(?:ip|user|username|email|path)\b)", rule["expr"]), name)
            summary = rule["annotations"]["summary"]
            # alert text is built from label values (and the number) only
            for ref in re.findall(r"\{\{(.*?)\}\}", summary):
                self.assertRegex(ref.strip(), r"^\$(labels\.(service|endpoint|job|purpose|integration|rule_group|mountpoint)|value \| printf \"%\.0f\")$", name)

    def test_every_alert_has_a_unit_test(self):
        tested = set()
        for f in TEST_FILES:
            for test in load(f)["tests"]:
                for case in test.get("alert_rule_test") or []:
                    if case.get("exp_alerts"):
                        tested.add(case["alertname"])
        names = {rule["alert"] for _, rule in rules()}
        self.assertEqual(names - tested, set())

    def test_required_alerts_exist(self):
        names = {rule["alert"] for _, rule in rules()}
        for want in ("Watchdog", "DailyHeartbeat", "TargetDown", "ContainerNotRunning", "ContainerUnhealthy",
                     "ContainerRestarting", "KeycloakNotReady", "BackendHealth", "PublicEndpointDown",
                     "TlsCertExpiry", "AcmeDnsDown", "DiskFreeLow", "MemoryLow", "JournalNearCap",
                     "BackupUnhealthy", "BackupTooOld", "WalSpoolStuck", "NtpUnsynced", "ClientKeyAge",
                     "AlertmanagerNotificationsFailing", "HostMetricsStale", "PostgresDown", "RedisDown",
                     "NtpReadingFailed", "ClientKeyCheckStale"):
            self.assertIn(want, names)

    def by_name(self, name):
        return [rule for _, rule in rules() if rule["alert"] == name]

    def test_exporters_own_verdict_alerts(self):
        """postgres/redis exporters answer (up 1) while their login fails: pg_up / redis_up count."""
        for name, expr, service in (("PostgresDown", "pg_up == 0", "db"), ("RedisDown", "redis_up == 0", "redis")):
            (rule,) = self.by_name(name)
            self.assertEqual(rule["expr"], expr)
            self.assertEqual(rule["labels"], {"severity": "critical", "service": service})
            self.assertEqual(rule["for"], "5m")
        smoke = func("monitoring_smoke")
        self.assertIn("for s in pg_up redis_up; do", smoke)
        self.assertIn('prom_get "http://127.0.0.1:9090/api/v1/query?query=$s"', smoke)

    def test_ntp_offset_is_chronys_own(self):
        """chrony never uses the kernel PLL: node_timex_offset_seconds stays 0 under it."""
        for rule in self.by_name("NtpOffset"):
            self.assertIn("ihr_ntp_offset_seconds", rule["expr"])
        self.assertNotIn("node_timex_offset_seconds", " ".join(rule["expr"] for _, rule in rules()))

    def test_disk_rules_skip_boot(self):
        for name in ("DiskFreeLow", "DiskFillingUp"):
            for rule in self.by_name(name):
                for sel in re.findall(r"node_filesystem_avail_bytes\{([^}]*)\}", rule["expr"]):
                    self.assertRegex(sel, r'mountpoint(="/"|!~"[^"]*/boot\(/\.\*\)\?")', (name, sel))

    def test_container_memory_is_the_working_set(self):
        (rule,) = self.by_name("ContainerMemoryNearLimit")
        self.assertIn("ihr_container_memory_working_set_bytes", rule["expr"])
        self.assertNotRegex(" ".join(rule["expr"] for _, rule in rules()), r"ihr_container_memory_bytes\b")

    def test_key_check_also_when_never_written(self):
        exprs = [rule["expr"] for rule in self.by_name("ClientKeyCheckStale")]
        self.assertIn("absent(ihr_client_keys_checked_timestamp_seconds)", exprs)

    def test_checked_by_the_pinned_images(self):
        self.assertIn('image prom/prometheus', CHECK_SH)
        self.assertIn('image prom/alertmanager', CHECK_SH)
        self.assertIn("--network none", CHECK_SH)
        self.assertIn("test rules", CHECK_SH)
        self.assertIn("check-config", CHECK_SH)
        self.assertIn("entry: scripts/check-monitoring.sh", PRECOMMIT)


class AlertmanagerSettings(unittest.TestCase):
    CONFIG = func("monitoring_config")

    def test_rendered_without_secrets(self):
        self.assertIn("smtp_auth_password_file: /run/ihr-alerts/smtp-password", self.CONFIG)
        self.assertIn("url_file: /run/ihr-alerts/watchdog-url", self.CONFIG)
        self.assertIn("url_file: /run/ihr-alerts/watchdog-fail-url", self.CONFIG)
        self.assertNotRegex(self.CONFIG, r"smtp_auth_password:|\burl: ")
        self.assertIn("smtp_require_tls: true", self.CONFIG)
        self.assertIn("min_version: TLS12", self.CONFIG)

    def test_inhibit_compares_every_instance_label(self):
        """Rendered by staging.sh itself: a critical alert mutes the warning of the same name for
        the same thing only (one missing on both sides counts as equal)."""
        with tempfile.TemporaryDirectory() as d:
            Path(d, ".env").write_text("\n".join([
                "IHR_TENANT_DOMAIN_SUFFIX=app.example.in", "IHR_MONITORING=on",
                "IHR_ALERT_TO=ops@example.in", "IHR_ALERT_FROM=alerts@example.in",
                "IHR_ALERT_SMTP_HOST=smtp.example.in", "IHR_ALERT_SMTP_PORT=587",
                "IHR_ALERT_SMTP_USER=emailapikey", "IHR_ALERT_DEADMAN=on", ""]))
            subprocess.run([str(ROOT / "scripts" / "staging.sh"), "monitoring-render"], check=True,
                           env={**os.environ, "IHR_STAGING_DIR": d}, capture_output=True)
            cfg = yaml.safe_load(Path(d, "generated", "monitoring", "alertmanager.yml").read_text())
        first = cfg["inhibit_rules"][0]
        self.assertEqual(first["source_matchers"], ['severity="critical"'])
        self.assertEqual(first["target_matchers"], ['severity="warning"'])
        self.assertEqual(first["equal"], ["alertname", "service", "job", "instance", "mountpoint", "purpose", "endpoint"])
        # every label the rules use to tell instances of one alert apart is compared
        distinguishing = {"service", "job", "instance", "mountpoint", "purpose", "endpoint", "integration", "rule_group"}
        for _, rule in rules():
            for ref in re.findall(r"\$labels\.(\w+)", rule["annotations"]["summary"]):
                self.assertIn(ref, distinguishing)
        # a ContainerNotRunning alert is grouped by name and severity only: its resolved mail is
        # sent when it resolves, whatever else of that name still fires
        self.assertEqual(cfg["route"]["group_by"], ["alertname", "severity"])
        self.assertTrue(cfg["receivers"][0]["email_configs"][0]["send_resolved"])

    def test_routing(self):
        c = self.CONFIG
        self.assertIn("matchers: ['alertname=\"Watchdog\"']", c)
        self.assertIn("repeat_interval: 4h", c)
        self.assertIn("repeat_interval: 24h\n      active_time_intervals: [daytime-ist]", c)
        self.assertIn('times: [{start_time: "08:00", end_time: "22:00"}]', c)
        self.assertIn("location: Asia/Kolkata", c)
        self.assertIn("inhibit_rules:", c)

    def test_recipient_never_in_the_repository(self):
        # The address comes from .env (monitoring-init); no address in the monitoring files
        for f in list(MON.rglob("*")) + [STAGING / "compose.monitoring.yaml"]:
            if f.is_file():
                self.assertNotRegex(f.read_text(), r"[A-Za-z0-9._%+-]+@(?!sha256)[a-z0-9-]+\.[a-z]{2,}", f.name)


class StagingCommands(unittest.TestCase):
    def test_overlays_follow_env(self):
        c = func("compose")
        self.assertIn('files+=(-f "$DIR/compose.monitoring.yaml")', c)
        self.assertIn('[[ "$mon" == test ]] && files+=(-f "$DIR/compose.monitoring.test.yaml")', c)

    def test_tokens_never_on_a_command_line(self):
        init = func("monitoring_init")
        self.assertIn('read -rsp "ZeptoMail SMTP password for alerts', init)
        self.assertIn("IFS= read -r token", init)
        for name in ("alertmanager-smtp-password", "alertmanager-watchdog-url", "alertmanager-watchdog-fail-url",
                     "postgres-exporter.env", "redis-exporter.env", "redis.env.new"):
            self.assertIn(f"write_secret {name}", init)
        self.assertNotIn("echo \"$token", init)
        self.assertNotRegex(init, r"--token|\$\{2:\?--token")

    def test_proof_uses_the_stand_ins(self):
        self.assertIn("grep -q '^IHR_MONITORING=' \"$DIR/.env\" || monitoring_init --test", STAGING_SH)
        self.assertIn('MON_TEST_DEADMAN="http://heartbeat-stub:8080/ping"', STAGING_SH)
        self.assertIn("ca_file: /proof/ca.pem", func("monitoring_config"))
        self.assertIn("monitoring_smoke", func("smoke"))
        self.assertIn("monitoring_up", func("up"))

    def test_changed_settings_reach_the_running_services(self):
        """Settings are single files mounted read-only and rewritten as new files: a running
        container keeps the old ones until it is recreated."""
        up = func("monitoring_up")
        self.assertIn("for s in prometheus alertmanager blackbox-exporter; do", up)
        self.assertIn('compose up -d --wait --no-deps --force-recreate "${stale[@]}"', up)
        self.assertIn('monitoring_settings_sum "$s" > "$GEN/monitoring/.settings-$s"', up)
        # the stale check runs before the stamps are rewritten
        self.assertLess(up.index("stale+=("), up.index('> "$GEN/monitoring/.settings-$s"'))
        settings = func("monitoring_settings")
        self.assertIn('"$MON_DIR/prometheus/prometheus.yml" "$MON_DIR"/prometheus/rules/*.yml', settings)
        self.assertIn('"$GEN/monitoring/alertmanager.yml"', settings)
        self.assertIn('"$MON_DIR/blackbox/blackbox.yml"', settings)
        self.assertIn('MON_DIR="$DIR/../monitoring"', STAGING_SH)
        # the files the overlay mounts are the files hashed
        for svc, mount in (("prometheus", "../monitoring/prometheus/prometheus.yml:"),
                           ("prometheus", "../monitoring/prometheus/rules:"),
                           ("alertmanager", "./generated/monitoring/alertmanager.yml:"),
                           ("blackbox-exporter", "../monitoring/blackbox/blackbox.yml:")):
            self.assertTrue([v for v in OVERLAY["services"][svc]["volumes"] if v.startswith(mount)], mount)
        # never a secret in the hash input
        self.assertNotIn("secrets", func("monitoring_settings_sum") + settings)

    def test_tunnel_binds_loopback_only(self):
        t = func("ops_tunnel")
        self.assertIn('-L "127.0.0.1:9090:$prom:9090"', t)
        self.assertIn('-L "127.0.0.1:9093:$am:9093"', t)

    def test_deploy_ships_monitoring_files(self):
        self.assertRegex(DEPLOY_SH, r"git ls-files -z deploy/staging deploy/s3 deploy/monitoring ")


class HostMetricsScript(unittest.TestCase):
    def test_reads_states_never_environments(self):
        self.assertNotIn(".Config.Env", HOST_METRICS)
        self.assertNotIn("docker inspect --format '{{json", HOST_METRICS)
        self.assertNotIn("set -x", HOST_METRICS)
        self.assertIn("/usr/local/bin/ihr-backup-health >/dev/null 2>&1", HOST_METRICS)

    def test_atomic_write(self):
        self.assertIn('mv "$tmp" "$OUT/ihr_host.prom"', HOST_METRICS)
        self.assertIn('mv "$tmp" "$out/ihr_client_keys.prom"', STAGING_SH)

    def test_service_names_checked_before_use_as_labels(self):
        self.assertIn("NAME_RE='^[a-z0-9][a-z0-9-]*$'", HOST_METRICS)

    def test_one_shot_lists_agree(self):
        script = re.search(r"ONESHOT_RE='\^\(([^)]*)\)\$'", HOST_METRICS).group(1)
        staging = re.search(r"MON_ONESHOTS='([^']*)'", STAGING_SH).group(1)
        self.assertEqual(script, staging)
        for name, doc in ALL_COMPOSE.items():
            for svc, spec in (doc.get("services") or {}).items():
                if spec.get("restart") == "no":
                    self.assertIn(svc, script.split("|"), (name, svc))
        self.assertIn('grep -Evx "$MON_ONESHOTS"', func("monitoring_textfile"))


class HostMetricsRun(unittest.TestCase):
    """host-metrics.sh run for real against a stand-in `docker`, cgroup tree and chrony reading."""

    FAKE_DOCKER = """#!/usr/bin/env bash
case "$1 $2" in
  "ps -aq") cut -d'|' -f5 "$FAKE_INSPECT" ;;
  "ps -q") ;;
  "inspect --format") cat "$FAKE_INSPECT" ;;
  *) echo "fake docker: $*" >&2; exit 1 ;;
esac
"""
    # service|status|health|restarts|id|memory limit
    CONTAINERS = [
        "db|running|healthy|0|aaa|1610612736",
        "provisioner|exited|none|1|bbb|0",
        "alertmanager-data|exited|none|0|ccc|0",
        "traefik-data|exited|none|0|ddd|0",
        "migrate|exited|none|0|eee|0",
    ]

    def run_script(self, services, chrony=None, chrony_age=0):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "bin").mkdir()
            (d / "bin" / "docker").write_text(self.FAKE_DOCKER)
            (d / "bin" / "docker").chmod(0o755)
            (d / "inspect").write_text("\n".join(self.CONTAINERS) + "\n")
            scope = d / "cg" / "system.slice" / "docker-aaa.scope"
            scope.mkdir(parents=True)
            (scope / "memory.current").write_text("1500000000\n")
            (scope / "memory.stat").write_text("anon 400000000\nfile 1100000000\ninactive_file 1000000000\n")
            (scope / "memory.events").write_text("low 0\nhigh 0\nmax 3\noom 2\noom_kill 2\n")
            (d / "chrony").mkdir()
            for name, text in (chrony or {}).items():
                f = d / "chrony" / name
                f.write_text(text)
                if chrony_age:
                    t = time.time() - chrony_age
                    os.utime(f, (t, t))
            (d / "out").mkdir()
            env = {**os.environ, "PATH": f"{d / 'bin'}:{os.environ['PATH']}", "FAKE_INSPECT": str(d / "inspect"),
                   "IHR_CGROUP_ROOT": str(d / "cg")}
            subprocess.run(["bash", str(MON / "host-metrics.sh"), "--out", str(d / "out"), "--project", "indiehr-staging",
                            "--services", services, "--chrony-dir", str(d / "chrony")],
                           check=True, env=env, capture_output=True, timeout=60)
            text = (d / "out" / "ihr_host.prom").read_text()
        return {line.rsplit(" ", 1)[0]: line.rsplit(" ", 1)[1] for line in text.splitlines() if not line.startswith("#")}

    SOURCES = "^,*,samay1.nic.in,1,6,377,12,0.000012,0.000010,0.020\n^,+,samay2.nic.in,1,6,377,13,0.000020,0.000018,0.025\n"
    TRACKING = "A29FC87B,samay1.nic.in,2,1700000000.123456789,-0.000123456,0.000001,0.000010,-12.345,0.001,0.020,0.005,0.001,64.5,Normal\n"

    def test_exited_one_shots_never_reported(self):
        for services in ("db provisioner backend migrate", ""):
            m = self.run_script(services)
            for one_shot in ("alertmanager-data", "traefik-data", "migrate"):
                self.assertFalse([k for k in m if f'service="{one_shot}"' in k], (services, one_shot))
            self.assertEqual(m['ihr_container_running{service="db"}'], "1")
            self.assertEqual(m['ihr_container_running{service="provisioner"}'], "0")
        m = self.run_script("db provisioner backend")
        # a service of the stack with no container at all still counts as not running
        self.assertEqual(m['ihr_container_present{service="backend"}'], "0")
        self.assertEqual(m['ihr_container_running{service="backend"}'], "0")

    def test_memory_is_the_working_set(self):
        m = self.run_script("db")
        self.assertEqual(m['ihr_container_memory_working_set_bytes{service="db"}'], "500000000")
        self.assertEqual(m['ihr_container_memory_limit_bytes{service="db"}'], "1610612736")
        self.assertEqual(m['ihr_container_oom_kills_total{service="db"}'], "2")
        self.assertFalse([k for k in m if k.startswith("ihr_container_memory_bytes")])

    def test_chrony_reading(self):
        m = self.run_script("db", {"sources.csv": self.SOURCES, "tracking.csv": self.TRACKING})
        self.assertEqual(m["ihr_ntp_reading_ok"], "1")
        self.assertEqual(m["ihr_ntp_indian_source_selected"], "1")
        self.assertEqual(m["ihr_ntp_offset_seconds"], "-0.000123456")
        # another source selected: a real 0
        other = self.SOURCES.replace("samay1.nic.in", "pool.example.org")
        m = self.run_script("db", {"sources.csv": other, "tracking.csv": self.TRACKING})
        self.assertEqual(m["ihr_ntp_indian_source_selected"], "0")

    def test_chrony_unreadable_is_unknown_not_zero(self):
        for chrony, age in (({}, 0),  # the root step removed the files (chronyc failed)
                            ({"sources.csv": self.SOURCES, "tracking.csv": self.TRACKING}, 600),  # old
                            ({"sources.csv": "", "tracking.csv": "506 Cannot talk to daemon\n"}, 0)):
            m = self.run_script("db", chrony, age)
            self.assertEqual(m["ihr_ntp_reading_ok"], "0", (chrony, age))
            self.assertNotIn("ihr_ntp_indian_source_selected", m)
            self.assertNotIn("ihr_ntp_offset_seconds", m)


if __name__ == "__main__":
    unittest.main()
