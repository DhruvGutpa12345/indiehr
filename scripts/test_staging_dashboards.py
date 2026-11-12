"""Static checks for the operator's dashboards (P1-T9d, ADR-015): Grafana on its own internal
network with Prometheus and the read-only log door alone, hardened, listening on its fixed address
and one host name; its secrets from files; the image switching off every outside call; the log
door allowing reads only; the dashboards current with their generator, naming only provisioned
datasources and metrics the stack really emits; and the wiring in staging.sh and deploy.sh.

Grafana itself is checked live by scripts/check-dashboards.py (smoke, and CI's "Grafana image").
Run: python -m unittest scripts/test_staging_dashboards.py (pre-commit hook "staging-dashboards-static")."""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "deploy" / "staging"
GRAFANA = ROOT / "deploy" / "grafana"


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_multi_constructor("!", lambda loader, suffix, node: (
    loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode)
    else loader.construct_mapping(node) if isinstance(node, yaml.MappingNode)
    else loader.construct_scalar(node)))


def load(path):
    return yaml.load(Path(path).read_text(), Loader=_Loader)


OVERLAY = load(STAGING / "compose.dashboards.yaml")
SVC = OVERLAY["services"]
ALL_COMPOSE = {f.name: load(f) for f in sorted(STAGING.glob("compose*.yaml"))}
DOCKERFILE = (ROOT / "deploy" / "docker" / "grafana.Dockerfile").read_text()
STAGING_SH = (ROOT / "scripts" / "staging.sh").read_text()
DEPLOY_SH = (ROOT / "scripts" / "deploy.sh").read_text()
DATASOURCES = load(GRAFANA / "provisioning" / "datasources" / "ihr.yaml")
PROVIDERS = load(GRAFANA / "provisioning" / "dashboards" / "ihr.yaml")
READER = load(GRAFANA / "logs-reader.yml")
BOARDS = {p.name: json.loads(p.read_text()) for p in sorted((GRAFANA / "dashboards").glob("*.json"))}
INTERNET_FACING = {"traefik", "backend", "frontend", "keycloak", "celery-worker", "celery-beat", "provisioner",
                   "staff-worker", "s3", "acme-dns", "smtp-test", "pebble", "coredns"}


def func(name):
    m = re.search(r"^%s\(\) \{(.*?)^\}" % re.escape(name), STAGING_SH, re.S | re.M)
    assert m, name
    return m.group(1)


def members(net):
    return {(f, s) for f, doc in ALL_COMPOSE.items() for s, spec in (doc.get("services") or {}).items()
            if net in (spec.get("networks") or [])}


def nets_of(service):
    return {n for doc in ALL_COMPOSE.values() for s, spec in (doc.get("services") or {}).items()
            if s == service for n in (spec.get("networks") or [])}


def env_of(dockerfile):
    """The ENV block of the final stage as a dict."""
    final = dockerfile.split("FROM ${GRAFANA_IMAGE}\n", 1)[1]
    out = {}
    for m in re.finditer(r"ENV (.*?)(?:\n(?!\s))", final, re.S):
        body = m.group(1).replace("\\\n", " ")
        for k, v in re.findall(r'(GF_[A-Z0-9_]+)=("(?:[^"\\]|\\.)*"|\S+)', body):
            out[k] = v.strip('"')
    return out


IMAGE_ENV = env_of(DOCKERFILE)


class Isolation(unittest.TestCase):
    def test_grafana_on_dashboards_alone_with_a_fixed_address(self):
        g = SVC["grafana"]
        self.assertEqual(list(g["networks"]), ["dashboards"])
        self.assertEqual(g["networks"]["dashboards"]["ipv4_address"], "${IHR_GRAFANA_IP:-10.203.0.50}")
        # listens on that address only, so a network added later does not expose it
        self.assertEqual(g["environment"]["GF_SERVER_HTTP_ADDR"], "${IHR_GRAFANA_IP:-10.203.0.50}")
        self.assertEqual(nets_of("grafana"), {"dashboards"})

    def test_dashboards_network_members(self):
        net = OVERLAY["networks"]["dashboards"]
        self.assertTrue(net["internal"])
        cfg = net["ipam"]["config"][0]
        self.assertEqual(cfg["subnet"], "${IHR_DASHBOARDS_NET:-10.203.0.48/28}")
        # Grafana's .50 lies outside what Docker hands out (.56-.63)
        self.assertEqual(cfg["ip_range"], "${IHR_DASHBOARDS_DYNAMIC:-10.203.0.56/29}")
        self.assertEqual(members("dashboards"), {("compose.dashboards.yaml", s) for s in ("grafana", "logs-reader", "prometheus")})

    def test_nothing_internet_facing_shares_a_network_with_grafana_or_its_door(self):
        for s in INTERNET_FACING:
            self.assertNotIn("dashboards", nets_of(s), s)
        # the door is on logs and dashboards only: never monitoring, alerting, edge or internal
        self.assertEqual(sorted(SVC["logs-reader"]["networks"]), ["dashboards", "logs"])

    def test_subnets_do_not_overlap(self):
        import ipaddress
        nets = []
        for doc in ALL_COMPOSE.values():
            for name, spec in (doc.get("networks") or {}).items():
                for c in ((spec or {}).get("ipam") or {}).get("config") or []:
                    m = re.fullmatch(r"\$\{[A-Z_]+:-([0-9./]+)\}", c["subnet"])
                    if m and m.group(1).startswith("10.203."):
                        nets.append((name, ipaddress.ip_network(m.group(1))))
        for i, (a, na) in enumerate(nets):
            for b, nb in nets[i + 1:]:
                if a != b:
                    self.assertFalse(na.overlaps(nb), f"{a} {na} overlaps {b} {nb}")

    def test_nothing_published(self):
        for name, svc in SVC.items():
            self.assertNotIn("ports", svc, name)
            self.assertNotIn("expose", svc, name)


class Hardening(unittest.TestCase):
    def test_long_running_services_hardened(self):
        for name in ("grafana", "logs-reader"):
            svc = SVC[name]
            self.assertEqual(svc["security_opt"], ["no-new-privileges:true"], name)
            self.assertEqual(svc["cap_drop"], ["ALL"], name)
            self.assertNotIn("cap_add", svc, name)
            self.assertTrue(svc["read_only"], name)
            self.assertIn("mem_limit", svc, name)
            self.assertEqual(svc["user"], "${IHR_SECRETS_UID:?}:${IHR_SECRETS_GID:?}", name)
        self.assertEqual(SVC["grafana"]["mem_limit"], "${IHR_GRAFANA_MEMORY:-512m}")

    def test_nothing_widens_them_in_any_overlay(self):
        for doc_name, doc in ALL_COMPOSE.items():
            for name in ("grafana", "logs-reader", "grafana-data"):
                svc = (doc.get("services") or {}).get(name)
                if not svc:
                    continue
                for key in ("privileged", "pid", "ipc", "devices", "volumes_from", "ports", "expose", "secrets", "configs"):
                    self.assertNotIn(key, svc, (doc_name, name, key))
                if name != "grafana-data":
                    self.assertNotIn("cap_add", svc, (doc_name, name))
                    self.assertNotIn("network_mode", svc, (doc_name, name))

    def test_images_pinned(self):
        self.assertRegex(SVC["logs-reader"]["image"], r"^victoriametrics/vmauth:v[0-9.]+-scratch@sha256:[0-9a-f]{64}$")
        self.assertRegex(SVC["grafana-data"]["image"], r"@sha256:[0-9a-f]{64}$")
        self.assertRegex(DOCKERFILE, r"ARG GRAFANA_IMAGE=grafana/grafana:[0-9.]+@sha256:[0-9a-f]{64}\n")
        self.assertRegex(DOCKERFILE, r"ADD --checksum=sha256:[0-9a-f]{64} \\\n\s+https://github\.com/VictoriaMetrics/victorialogs-datasource/releases/download/v")

    def test_bind_mounts_never_create_folders(self):
        for name in ("grafana", "logs-reader"):
            for v in SVC[name]["volumes"]:
                # Repository paths only in long form (short form would let Docker create them)
                self.assertFalse(isinstance(v, str) and v.startswith("../"), v)
                if isinstance(v, dict) and v["type"] == "bind":
                    self.assertTrue(v["read_only"], v)
                    self.assertEqual(v["bind"], {"create_host_path": False}, v)

    def test_every_bind_source_is_shipped_by_deploy(self):
        """A ../<path> mounted by any overlay must be in deploy.sh's list of files it sends."""
        m = re.search(r"^git ls-files -z (deploy/staging .*?)\n  \| tar", DEPLOY_SH, re.S | re.M)
        shipped = [t for t in m.group(1).split() if t != "\\"]
        for path in shipped:  # real paths only: a stray word such as "deploy" would match everything
            self.assertTrue((ROOT / path).exists(), path)
        for need in ("scripts/check-dashboards.py", "deploy/grafana/allowed-empty.txt", "deploy/grafana/logs-reader.yml",
                     "deploy/grafana/provisioning", "deploy/grafana/dashboards"):
            self.assertIn(need, shipped)
        for fname, doc in ALL_COMPOSE.items():
            for sname, spec in (doc.get("services") or {}).items():
                for v in spec.get("volumes") or []:
                    src = v["source"] if isinstance(v, dict) else v.split(":")[0]
                    if not str(src).startswith("../"):
                        continue
                    rel = "deploy/" + src[3:]
                    if rel.startswith("deploy/dev/") or src.startswith("../../"):
                        continue  # proofs on this machine only
                    self.assertTrue(any(rel == s or rel.startswith(s.rstrip("/") + "/") for s in shipped),
                                    f"{fname} {sname}: {rel} is not sent by deploy.sh")


class Access(unittest.TestCase):
    def test_one_host_name_and_the_tunnel(self):
        env = SVC["grafana"]["environment"]
        self.assertEqual(env["GF_SERVER_DOMAIN"], "${IHR_GRAFANA_DOMAIN:-grafana.localhost}")
        self.assertEqual(env["GF_SERVER_ROOT_URL"], "http://${IHR_GRAFANA_DOMAIN:-grafana.localhost}:3000/")
        self.assertEqual(env["GF_SERVER_ENFORCE_DOMAIN"], "true")
        self.assertEqual(env["GF_AUTH_BASIC_ENABLED"], "false")
        self.assertIn('-L "127.0.0.1:3000:$gf:3000"', func("ops_tunnel"))
        # Only a proof on this machine may answer to its address instead
        self.assertIn('-f "$DIR/.local" && "$v" == "$(grafana_ip)"', func("grafana_domain"))

    def test_secrets_from_files(self):
        env = SVC["grafana"]["environment"]
        self.assertEqual(env["GF_SECURITY_ADMIN_PASSWORD__FILE"], "/run/ihr-grafana/admin-password")
        self.assertEqual(env["GF_SECURITY_SECRET_KEY__FILE"], "/run/ihr-grafana/secret-key")
        self.assertEqual(SVC["grafana"]["env_file"], ["secrets/grafana-admin.env"])
        for k in env:
            self.assertNotRegex(k, r"PASSWORD$|SECRET_KEY$|ADMIN_USER$", k)
        for name in ("grafana-admin.env", "grafana-admin-password", "grafana-secret-key", "logs-reader-password", "logs-reader.env"):
            self.assertIn(f"write_secret {name}", func("dashboards_secrets"), name)
        # an admin name that is not guessable (lock-outs count per name), never "admin"
        self.assertIn('name="ops-$(python3 -c', func("dashboards_secrets"))
        # a missing secret with an existing data volume stops instead of making new ones
        self.assertIn("_grafanadata", func("dashboards_secrets"))

    def test_reset_admin_reads_the_password_on_stdin(self):
        body = func("grafana_reset_admin")
        self.assertRegex(body, r"--password-from-stdin \\\n\s+< \"\$SECRETS/grafana-admin-password.new\"")
        for path in (ROOT / "scripts").glob("*.sh"):
            for line in path.read_text().splitlines():
                if "reset-admin-password" in line and not line.lstrip().startswith("#"):
                    self.assertIn("--password-from-stdin", line, path.name)

    def test_grafana_holds_no_password_of_the_log_store(self):
        """Exactly its data volume, the two folders from git and its own three secret files."""
        vols = sorted(v if isinstance(v, str) else f'{v["type"]}:{v["source"]}:{v["target"]}' for v in SVC["grafana"]["volumes"])
        self.assertEqual(vols, sorted([
            "volume:grafanadata:/var/lib/grafana",
            "bind:../grafana/provisioning:/etc/grafana/provisioning",
            "bind:../grafana/dashboards:/etc/grafana/ihr-dashboards",
            "./secrets/grafana-admin-password:/run/ihr-grafana/admin-password:ro",
            "./secrets/grafana-secret-key:/run/ihr-grafana/secret-key:ro",
            "./secrets/logs-reader-password:/run/ihr-grafana/logs-password:ro"]))
        ds = {d["uid"]: d for d in DATASOURCES["datasources"]}
        self.assertEqual(ds["victorialogs"]["url"], "http://logs-reader:8427")
        self.assertEqual(ds["victorialogs"]["basicAuthUser"], "grafana-ro")
        self.assertEqual(ds["victorialogs"]["secureJsonData"]["basicAuthPassword"], "$__file{/run/ihr-grafana/logs-password}")

    def test_log_store_admin_key_stays_with_the_store(self):
        """secrets/logs-admin-key deletes logs: VictoriaLogs checks it, no other service holds it."""
        holders = {(f, n) for f, doc in ALL_COMPOSE.items() for n, spec in (doc.get("services") or {}).items()
                   if "logs-admin-key" in json.dumps(spec.get("volumes") or [])}
        self.assertEqual(holders, {("compose.logs.yaml", "victorialogs")})
        self.assertIn("write_secret logs-reader-key", func("dashboards_secrets"))

    def test_log_door_reads_only(self):
        self.assertEqual(set(READER), {"users"})  # no unauthorized_user, no shared default
        users = READER["users"]
        self.assertEqual(set(users[0]), {"username", "password", "url_map"})
        self.assertEqual([u["username"] for u in users], ["grafana-ro"])
        self.assertEqual(users[0]["password"], "%{IHR_LOGS_READER_PASSWORD}")
        self.assertNotIn("url_prefix", users[0])  # nothing outside the map below
        (route,) = users[0]["url_map"]
        self.assertEqual(set(route), {"src_paths", "url_prefix", "headers"})
        self.assertEqual(route["src_paths"], ["/select/logsql/.+", "/select/tenant_ids"])
        self.assertEqual(route["url_prefix"], "http://victorialogs:9428")
        cmd = SVC["logs-reader"]["command"]
        for flag in ("metricsAuthKey", "flagsAuthKey", "pprofAuthKey", "reloadAuthKey"):
            self.assertIn(f"-{flag}=file:///run/ihr-logs-reader/key", cmd)


class Image(unittest.TestCase):
    def test_no_outside_calls_and_viewer_only(self):
        want = {
            "GF_ANALYTICS_REPORTING_ENABLED": "false", "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
            "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES": "false", "GF_ANALYTICS_FEEDBACK_LINKS_ENABLED": "false",
            "GF_NEWS_NEWS_FEED_ENABLED": "false", "GF_SECURITY_DISABLE_GRAVATAR": "true",
            "GF_SNAPSHOTS_ENABLED": "false", "GF_SNAPSHOTS_EXTERNAL_ENABLED": "false",
            "GF_PLUGINS_PREINSTALL_DISABLED": "true", "GF_PLUGINS_PREINSTALL_AUTO_UPDATE": "false",
            "GF_PLUGINS_PLUGIN_ADMIN_ENABLED": "false", "GF_PLUGINS_PUBLIC_KEY_RETRIEVAL_DISABLED": "true",
            "GF_UNIFIED_ALERTING_ENABLED": "false", "GF_AUTH_ANONYMOUS_ENABLED": "false",
            "GF_USERS_ALLOW_SIGN_UP": "false", "GF_HELP_ENABLED": "false", "GF_METRICS_ENABLED": "false",
            "GF_SECURITY_CONTENT_SECURITY_POLICY": "true", "GF_PUBLIC_DASHBOARDS_ENABLED": "false",
            "GF_SECURITY_DATA_SOURCE_PROXY_WHITELIST": "prometheus:9090 logs-reader:8427",
            "GF_GRAFANA_COM_URL": "http://127.0.0.1:9", "GF_GRAFANA_COM_API_URL": "http://127.0.0.1:9/api",
            "GF_AUTH_LOGIN_MAXIMUM_INACTIVE_LIFETIME_DURATION": "1h", "GF_AUTH_LOGIN_MAXIMUM_LIFETIME_DURATION": "12h",
        }
        for k, v in want.items():
            self.assertEqual(IMAGE_ENV.get(k), v, k)
        csp = IMAGE_ENV["GF_SECURITY_CONTENT_SECURITY_POLICY_TEMPLATE"]
        self.assertNotIn("grafana.com", csp)
        self.assertNotIn("img-src *", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertRegex(DOCKERFILE, r"\nUSER 472\n?$")

    def test_only_the_two_datasource_plugins(self):
        self.assertIn("prometheus|canvas) ;;", DOCKERFILE)
        disabled = set(IMAGE_ENV["GF_PLUGINS_DISABLE_PLUGINS"].split(","))
        self.assertNotIn("prometheus", disabled)
        self.assertNotIn("victoriametrics-logs-datasource", disabled)
        self.assertTrue({"mysql", "grafana-postgresql-datasource", "mssql", "loki", "grafana-testdata-datasource"} <= disabled)


class Dashboards(unittest.TestCase):
    def test_generated_files_current(self):
        r = subprocess.run([sys.executable, str(GRAFANA / "build_dashboards.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_provisioned_read_only(self):
        (p,) = PROVIDERS["providers"]
        self.assertFalse(p["allowUiUpdates"])
        self.assertTrue(p["disableDeletion"])
        self.assertEqual(p["options"]["path"], "/etc/grafana/ihr-dashboards")
        for d in DATASOURCES["datasources"]:
            self.assertFalse(d["editable"], d["uid"])
        for name, b in BOARDS.items():
            self.assertFalse(b["editable"], name)

    def targets(self):
        for name, b in BOARDS.items():
            for p in b["panels"]:
                for t in p.get("targets") or []:
                    yield name, p, t

    def test_every_panel_names_a_provisioned_datasource(self):
        uids = {d["uid"]: d["type"] for d in DATASOURCES["datasources"]}
        for name, p, t in self.targets():
            self.assertEqual(uids.get(t["datasource"]["uid"]), t["datasource"]["type"], (name, p["title"]))

    def test_no_personal_field_grouped_except_the_sign_in_table(self):
        for name, p, t in self.targets():
            expr = t.get("expr", "")
            for field in ("client_ip", "kc_user", "username", "email", "user_id"):
                if field in expr:
                    self.assertEqual((name, p["title"], field),
                                     ("ihr-traffic.json", "Addresses with the most sign-in failures (selected time range)", "client_ip"))

    def test_metric_names_are_emitted_by_the_stack(self):
        """A panel on a metric nobody writes stays empty for ever: every name must come from an
        exporter we run, or be written by our own scripts."""
        seen = {line.split()[0] for line in (GRAFANA / "metrics-seen.txt").read_text().splitlines()
                if line.strip() and not line.startswith("#")}
        # Where our own metrics are written (not where they are queried): host-metrics.sh's
        # describe/family/metric, staging.sh's "# TYPE" lines
        emitters = (ROOT / "deploy/monitoring/host-metrics.sh").read_text()
        emitted = set(re.findall(r'\b(?:describe|family)\s+(ihr_[a-z0-9_]+)', emitters))
        emitted |= set(re.findall(r'\bmetric\s+"(ihr_[a-z0-9_]+)', emitters))
        emitted |= set(re.findall(r"# TYPE (ihr_[a-z0-9_]+)", STAGING_SH))
        archive = (ROOT / "deploy/logs/archive/archive.py").read_text()
        words = {"by", "without", "on", "ignoring", "group_left", "group_right", "bool", "offset", "or", "and", "unless", "le"}
        for name, p, t in self.targets():
            if t["datasource"]["uid"] != "prometheus":
                continue
            expr = re.sub(r"\{[^}]*\}", "", t["expr"])
            expr = re.sub(r"\[[^\]]*\]", "", expr)
            expr = re.sub(r"\b(by|without)\s*\([^)]*\)", "", expr)
            for m in re.finditer(r"(?<![A-Za-z0-9_:.])[A-Za-z_:][A-Za-z0-9_:]*", expr):
                tok = m.group(0)
                if tok in words or expr[m.end():].lstrip().startswith("("):
                    continue  # a keyword or a function
                if not tok.startswith("ihr_"):
                    # an exporter's metric: seen in a proof's Prometheus (the smoke checks it live)
                    self.assertIn(tok, seen, (name, p["title"], tok))
                    continue
                if tok.startswith("ihr_logs_archive_"):
                    self.assertIn(f'"{tok[len("ihr_logs_archive_"):]}":', archive, (name, p["title"], tok))
                    continue
                self.assertIn(tok, emitted, (name, p["title"], tok))

    def test_log_fields_come_from_the_pipeline(self):
        """Every field a log panel filters, groups or sorts by is one the pipeline writes (exact
        names: redact.lua's out.<field> and r.<field>), VictoriaLogs's own, or one the panel makes."""
        lua = (ROOT / "deploy/logs/fluent-bit/redact.lua").read_text()
        fields = set(re.findall(r"\b(?:out|r)\.([a-z_]+)\s*=", lua)) | {"_msg", "_time", "_stream"}
        for name, p, t in self.targets():
            if t["datasource"]["uid"] != "victorialogs":
                continue
            expr = re.sub(r"`[^`]*`|\"[^\"]*\"|\$\{[^}]*\}", '""', t["expr"])  # strings and variables out
            made = set(re.findall(r"\bas\s+([a-z_]+)", expr)) | set(re.findall(r"count\(\)\s+([a-z_]+)", expr))
            refs = set(re.findall(r"(?<![\w$])([a-z_][a-z0-9_]*):", expr))
            for group in re.findall(r"\bby\s*\(([^)]*)\)", expr):
                refs |= {f.split()[0] for f in group.split(",") if f.strip()}
            for field in refs - made:
                self.assertIn(field, fields, (name, p["title"], field))

    def test_firing_alerts_include_the_log_rules(self):
        server = BOARDS["ihr-server.json"]
        exprs = " ".join(t["expr"] for p in server["panels"] for t in p.get("targets") or [])
        self.assertIn("ALERTS{", exprs)
        self.assertIn('vmalert_alerts_firing{job="vmalert"}', exprs)
        n = sum(len(re.findall(r"^\s*- alert:", f.read_text(), re.M)) for f in (ROOT / "deploy/logs/alerts").glob("*.yml"))
        runs = next(p for p in server["panels"] if p["title"] == "Log alert rules running")
        self.assertEqual(runs["fieldConfig"]["defaults"]["thresholds"]["steps"][1]["value"], n)

    def test_allowed_empty_list_has_cases_and_reasons(self):
        cases = {"always", "proof", "archive-off", "archive-new", "backup-off"}
        for n, line in enumerate((GRAFANA / "allowed-empty.txt").read_text().splitlines(), 1):
            line = line.strip()
            if line and not line.startswith("#"):
                m = re.fullmatch(r"([a-z-]+(?:,[a-z-]+)*)\s+(\S+?)/(.+?)\s+#\s*(\S.*)", line)
                self.assertTrue(m, f"line {n}")
                self.assertLessEqual(set(m[1].split(",")), cases, f"line {n}")
                self.assertIn(f"{m[2]}.json", BOARDS, f"line {n}")
                titles = {p["title"] for p in BOARDS[f"{m[2]}.json"]["panels"]}
                self.assertIn(m[3].strip(), titles, f"line {n}")


class Wiring(unittest.TestCase):
    def test_on_with_monitoring_restamped_and_built(self):
        self.assertIn('files+=(-f "$DIR/compose.dashboards.yaml")', func("compose"))
        up = func("monitoring_up")
        self.assertIn("logs-reader grafana)", up)
        self.assertIn("dashboards_secrets", up)
        self.assertIn("grafana_image", up)
        self.assertIn("grafana) printf", func("monitoring_settings"))
        self.assertIn("logs-reader) printf", func("monitoring_settings"))
        self.assertIn('ship_image "$GRAFANA"', DEPLOY_SH)
        self.assertIn("ihr.dockerfile.sha256=$GRAFANA_DF_SUM", DEPLOY_SH)
        self.assertIn("dashboards_smoke", func("monitoring_smoke"))

    def test_smoke_checks_reach(self):
        body = func("dashboards_smoke")
        self.assertIn("compose exec -T backend", body)
        self.assertIn("check-dashboards.py", body)
        self.assertIn("insert/jsonline", body)


if __name__ == "__main__":
    unittest.main()
