#!/usr/bin/env python3
"""Live checks of the operator's Grafana (P1-T9d, ADR-015). Standard library only.

Run by `scripts/staging.sh smoke` (on a server, or a proof on this machine) and by CI's "Grafana
image" job (--image-only: no datasources or dashboards there). It talks to Grafana's fixed
address directly, with the Host header Grafana answers to, as the SSH tunnel would.

    check-dashboards.py --url http://10.203.0.50:3000 --domain grafana.localhost \
        --admin-env secrets/grafana-admin.env --password-file secrets/grafana-admin-password \
        [--dashboards deploy/grafana/dashboards --allowed-empty deploy/grafana/allowed-empty.txt
         --case proof --case archive-off ...]
        [--image-only]

Prints "ok    ..." or "FAIL  ..." per check (the smoke format) and exits 1 on any failure. The
password is read from its file and never printed.
"""

import argparse
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

DATASOURCE_PLUGINS = {"prometheus", "victoriametrics-logs-datasource"}
DATASOURCE_UIDS = {"prometheus", "victorialogs"}
# Every setting that would call out, or widen what Grafana does, with the value it must have
# (/api/admin/settings shows them as strings)
SETTINGS = {
    ("analytics", "reporting_enabled"): "false",
    ("analytics", "check_for_updates"): "false",
    ("analytics", "check_for_plugin_updates"): "false",
    ("analytics", "feedback_links_enabled"): "false",
    ("news", "news_feed_enabled"): "false",
    ("snapshots", "enabled"): "false",
    ("snapshots", "external_enabled"): "false",
    ("plugins", "preinstall_disabled"): "true",
    ("plugins", "preinstall_auto_update"): "false",
    ("plugins", "plugin_admin_enabled"): "false",
    ("plugins", "public_key_retrieval_disabled"): "true",
    ("unified_alerting", "enabled"): "false",
    ("security", "disable_gravatar"): "true",
    ("security", "content_security_policy"): "true",
    ("auth.anonymous", "enabled"): "false",
    ("auth.basic", "enabled"): "false",
    ("users", "allow_sign_up"): "false",
    ("metrics", "enabled"): "false",
    ("server", "enforce_domain"): "true",
    ("public_dashboards", "enabled"): "false",
    ("grafana_com", "url"): "http://127.0.0.1:9",
    ("grafana_com", "api_url"): "http://127.0.0.1:9/api",
    ("security", "data_source_proxy_whitelist"): "prometheus:9090 logs-reader:8427",
    ("help", "enabled"): "false",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Grafana:
    def __init__(self, url, host):
        self.url, self.host = url.rstrip("/"), host
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method, path, body=None, headers=None, host=None, cookies=True):
        data = None if body is None else json.dumps(body).encode()
        h = {"Host": host or self.host, "Accept": "application/json"}
        if data is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=h)
        opener = self.opener if cookies else urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=60) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def get_json(self, path):
        status, _, raw = self.call("GET", path)
        if status != 200:
            raise RuntimeError(f"GET {path}: HTTP {status}")
        return json.loads(raw)


def frames_have_data(result):
    for frame in result.get("frames") or []:
        values = (frame.get("data") or {}).get("values") or []
        if any(len(col or []) for col in values):
            return True
    return False


def panel_targets(board):
    for panel in board.get("panels", []):
        for sub in [panel] + panel.get("panels", []):
            for t in sub.get("targets") or []:
                yield sub["title"], t


CASES = {"always", "proof", "archive-off", "archive-new", "backup-off"}
ALLOWED_RE = re.compile(r"([a-z-]+(?:,[a-z-]+)*)\s+(\S+?)/(.+?)\s+#\s*(\S.*)")


def load_allowed_empty(path, cases):
    """Lines '<case[,case...]> <board uid>/<panel title>  # reason'. A panel may be empty when
    one of its cases applies here ('always' always does). A reason is required."""
    allowed = set()
    if not path:
        return allowed
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = ALLOWED_RE.fullmatch(line)
        if not m or not set(m[1].split(",")) <= CASES:
            sys.exit(f"{path}:{n}: expected '<case[,case...]> <uid>/<title>  # reason', cases {sorted(CASES)}")
        if set(m[1].split(",")) & ({"always"} | set(cases)):
            allowed.add((m[2], m[3].strip()))
    return allowed


METRIC_SKIP = {"by", "without", "on", "ignoring", "group_left", "group_right", "bool", "offset", "or", "and", "unless", "le"}


def metric_names(expr):
    """The metric names a PromQL expression reads (label matchers, ranges and groupings out)."""
    e = re.sub(r"\{[^}]*\}", "", expr)
    e = re.sub(r"\[[^\]]*\]", "", e)
    e = re.sub(r"\b(by|without)\s*\([^)]*\)", "", e)
    out = set()
    for m in re.finditer(r"(?<![A-Za-z0-9_:.])[A-Za-z_:][A-Za-z0-9_:]*", e):
        if m.group() not in METRIC_SKIP and not e[m.end():].lstrip().startswith("("):
            out.add(m.group())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--admin-env", required=True)
    ap.add_argument("--password-file", required=True)
    ap.add_argument("--dashboards")
    ap.add_argument("--allowed-empty")
    ap.add_argument("--case", action="append", default=[], choices=sorted(CASES - {"always"}))
    ap.add_argument("--image-only", action="store_true")
    a = ap.parse_args()

    env = dict(re.findall(r"^([A-Z_]+)=(.*)$", Path(a.admin_env).read_text(), re.M))
    user = env["GF_SECURITY_ADMIN_USER"]
    password = Path(a.password_file).read_text().strip()
    host = f"{a.domain}:3000"
    g = Grafana(a.url, host)
    failed = []

    def check(label, ok, detail=""):
        print(("ok    " if ok else "FAIL  ") + "grafana: " + label + (f" ({detail})" if detail else ""))
        if not ok:
            failed.append(label)

    status, _, raw = g.call("GET", "/api/health", cookies=False)
    check("healthy", status == 200 and json.loads(raw or b"{}").get("database") == "ok", f"HTTP {status}")

    # Another host name (a page using DNS rebinding) is sent to Grafana's own name, not served
    status, headers, _ = g.call("POST", "/login", {"user": user, "password": "x"}, host="evil.example:3000", cookies=False)
    loc = headers.get("Location", "")
    check("another host name is redirected, not served", status in (301, 302, 307, 308) and loc.startswith(f"http://{host}/"),
          f"HTTP {status} {loc}")

    check("default 'admin' name has no account", g.call("POST", "/login", {"user": "admin", "password": password}, cookies=False)[0] == 401)
    check("admin name is not the default", user != "admin" and len(user) >= 12)
    check("wrong password refused", g.call("POST", "/login", {"user": user, "password": password + "x"}, cookies=False)[0] == 401)
    status, _, _ = g.call("POST", "/login", {"user": user, "password": password})
    check("admin signs in", status == 200, f"HTTP {status}")
    if status != 200:
        print("FAIL  grafana: cannot sign in; the remaining checks need a session")
        sys.exit(1)

    basic = __import__("base64").b64encode(f"{user}:{password}".encode()).decode()
    check("basic auth on the API is off", g.call("GET", "/api/org", headers={"Authorization": "Basic " + basic}, cookies=False)[0] == 401)

    _, headers, _ = g.call("GET", "/login", cookies=False)
    csp = headers.get("Content-Security-Policy", "")
    check("Content-Security-Policy: nothing from outside, never framed",
          "frame-ancestors 'none'" in csp and "grafana.com" not in csp and "img-src *" not in csp and "connect-src 'self'" in csp)
    check("Grafana's own /metrics not served", g.call("GET", "/metrics", cookies=False)[0] != 200)

    # Cross-site writes with the session cookie: another loopback UI (Alertmanager, 9093) is refused
    status, _, _ = g.call("POST", "/api/user/stars/dashboard/uid/ihr-server", headers={"Origin": "http://127.0.0.1:9093"})
    check("a write from another origin is refused", status == 403, f"HTTP {status}")

    settings = g.get_json("/api/admin/settings")
    bad = [f"{s}.{k}={settings.get(s, {}).get(k)!r}" for (s, k), v in SETTINGS.items() if str(settings.get(s, {}).get(k)).lower() != v]
    check("no outside calls, no alerting, no sign-up (settings)", not bad, ", ".join(bad))
    check("admin login lifetime ≤ 12 h, idle ≤ 1 h",
          settings.get("auth", {}).get("login_maximum_lifetime_duration") == "12h"
          and settings.get("auth", {}).get("login_maximum_inactive_lifetime_duration") == "1h")

    plugins = {p["id"] for p in g.get_json("/api/plugins?type=datasource")}
    check("only the two datasource plugins", plugins == DATASOURCE_PLUGINS, " ".join(sorted(plugins)))
    if a.image_only:
        sys.exit(1 if failed else 0)

    sources = {d["uid"]: d for d in g.get_json("/api/datasources")}
    check("datasources as provisioned", set(sources) == DATASOURCE_UIDS and all(d.get("readOnly") for d in sources.values()),
          " ".join(sorted(sources)))
    for uid in sorted(DATASOURCE_UIDS & set(sources)):
        status, _, raw = g.call("GET", f"/api/datasources/uid/{uid}/health")
        check(f"datasource {uid} answers", status == 200 and json.loads(raw).get("status") == "OK",
              f"HTTP {status} {json.loads(raw or b'{}').get('message', '')[:120]}")

    files = {json.loads(p.read_text())["uid"]: json.loads(p.read_text()) for p in Path(a.dashboards).glob("*.json")}
    found = {d["uid"] for d in g.get_json("/api/search?type=dash-db")}
    check("dashboards as in git", found == set(files), " ".join(sorted(found)))
    empty_ok = load_allowed_empty(a.allowed_empty, a.case)
    print("info  grafana: allowed-empty cases here: " + ", ".join(["always"] + sorted(a.case)))
    # Every metric a panel names exists in Prometheus (with any value): a misspelt metric in a
    # panel that may be empty (no alert firing, no 429s) is still caught. Not those allowed empty
    # by a case that applies here other than 'always' (archive or backups off, a proof).
    case_only = load_allowed_empty(a.allowed_empty, a.case) - load_allowed_empty(a.allowed_empty, [])
    wanted = set()
    for uid, board in files.items():
        for title, t in panel_targets(board):
            if t["datasource"]["uid"] == "prometheus" and (uid, title) not in case_only:
                wanted |= metric_names(t["expr"])
    for name in sorted(wanted):
        q = {"refId": "M", "datasource": {"type": "prometheus", "uid": "prometheus"},
             "expr": f'count({{__name__="{name}"}})', "instant": True, "range": False}
        status, _, raw = g.call("POST", "/api/ds/query", {"queries": [q], "from": "now-1h", "to": "now"})
        try:
            ok = status == 200 and frames_have_data(json.loads(raw)["results"]["M"])
        except (ValueError, KeyError):
            ok = False
        check(f"metric exists in Prometheus: {name}", ok)
    for uid, board in sorted(files.items()):
        for title, t in panel_targets(board):
            q = dict(t)
            if "expr" in q:
                q["expr"] = q["expr"].replace("${service:raw}", "*").replace("${search:raw}", "").strip()
            q |= {"intervalMs": 60000, "maxDataPoints": 300, "maxLines": 50}
            status, _, raw = g.call("POST", "/api/ds/query", {"queries": [q], "from": "now-1h", "to": "now"})
            try:
                res = json.loads(raw)["results"][q["refId"]]
            except (ValueError, KeyError):
                res = {"error": f"HTTP {status} {raw[:200]!r}"}
            name = f"{uid}/{title} [{q['refId']}]"
            if res.get("error") or status != 200:
                check(f"query runs: {name}", False, str(res.get("error"))[:200])
            elif frames_have_data(res):
                check(f"query has data: {name}", True)
            elif (uid, title) in empty_ok:
                print(f"skip  grafana: empty, allowed here: {name}")
            else:
                check(f"query has data: {name}", False, "no series or rows (allowed-empty.txt lists the reviewed exceptions)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
