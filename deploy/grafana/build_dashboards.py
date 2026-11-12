#!/usr/bin/env python3
"""The operator's Grafana dashboards (P1-T9d, ADR-015), written from the compact definitions
below into deploy/grafana/dashboards/*.json. Grafana loads them read-only (provisioning): change
a dashboard here, run this script, commit both. A test checks the JSON is what this makes.

    python3 deploy/grafana/build_dashboards.py          write the files
    python3 deploy/grafana/build_dashboards.py --check  exit 1 if a file differs

Privacy: metrics carry service, router and probe names only. Log panels read the redacted store
(ADR-013); the one table that names addresses is the sign-in table, as the alert mails do (Q-O8).
"""

import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "dashboards"
LOG_RULES = Path(__file__).resolve().parents[1] / "logs" / "alerts"
PROM = {"type": "prometheus", "uid": "prometheus"}
LOGS = {"type": "victoriametrics-logs-datasource", "uid": "victorialogs"}


class Board:
    def __init__(self, uid, title, description, variables=()):
        self.uid, self.title, self.description = uid, title, description
        self.variables = list(variables)
        self.panels = []
        self.x = self.y = 0
        self.row_h = 0
        self.next_id = 1

    def _place(self, w, h):
        if self.x + w > 24:
            self.x, self.y = 0, self.y + self.row_h
            self.row_h = 0
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        return pos

    def row(self, title):
        if self.x:
            self.x, self.y = 0, self.y + self.row_h
            self.row_h = 0
        self.panels.append({"id": self._id(), "type": "row", "title": title, "collapsed": False,
                            "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}, "panels": []})
        self.y += 1

    def _id(self):
        i = self.next_id
        self.next_id += 1
        return i

    def add(self, kind, title, targets, w=8, h=8, unit=None, description="", **extra):
        panel = {"id": self._id(), "type": kind, "title": title, "gridPos": self._place(w, h),
                 "datasource": targets[0]["datasource"], "targets": targets}
        if description:
            panel["description"] = description
        defaults = {}
        if unit:
            defaults["unit"] = unit
        panel["fieldConfig"] = {"defaults": defaults | extra.pop("defaults", {}), "overrides": []}
        panel["options"] = extra.pop("options", {})
        panel.update(extra)
        self.panels.append(panel)

    def json(self):
        return {
            "uid": self.uid, "title": self.title, "description": self.description,
            "tags": ["indiehr"], "timezone": "Asia/Kolkata", "editable": False,
            "graphTooltip": 1, "refresh": "1m", "schemaVersion": 41, "version": 1,
            "time": {"from": "now-6h", "to": "now"},
            "templating": {"list": self.variables}, "panels": self.panels,
            "annotations": {"list": []}, "links": [],
        }


def prom(expr, legend="", ref="A", instant=False):
    t = {"refId": ref, "datasource": PROM, "expr": expr, "legendFormat": legend}
    if instant:
        t |= {"instant": True, "range": False}
    return t


def logs(expr, query_type, ref="A", legend=""):
    t = {"refId": ref, "datasource": LOGS, "expr": expr, "queryType": query_type}
    if legend:
        t["legendFormat"] = legend
    return t


# A container line's level from its message (logfmt, JSON, or a capitalised word; Python tracebacks)
ERROR_RE = r'level=(error|fatal|panic|crit)|"level":"(error|fatal|panic|critical)"|\b(ERROR|FATAL|CRITICAL|PANIC)\b|Traceback \(most recent call last\)'
WARN_RE = r'level=warn|"level":"warn|\bWARN(ING)?\b'


def thresholds(*steps):
    """steps: (value or None, color)"""
    return {"thresholds": {"mode": "absolute", "steps": [{"value": v, "color": c} for v, c in steps]}}


OK_BAD = thresholds((None, "red"), (1, "green"))
BAD_OK = thresholds((None, "green"), (1, "red"))


def stat(b, title, expr, unit=None, w=4, h=4, steps=None, legend="", description=""):
    b.add("stat", title, [prom(expr, legend, instant=True)], w=w, h=h, unit=unit, description=description,
          defaults=(steps or {}), options={"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background",
                                           "graphMode": "none", "textMode": "value"})


def series(b, title, targets, unit=None, w=8, h=8, description=""):
    b.add("timeseries", title, targets, w=w, h=h, unit=unit, description=description,
          options={"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}})


# ---- 1. Server and stack ----------------------------------------------------------------------
def server():
    b = Board("ihr-server", "Server and stack",
              "The server, its containers and the alerting pipeline (P1-T9a).")
    # Two sources of alerts: Prometheus's rules (ALERTS) and vmalert's log rules, which ALERTS
    # never shows. A missing vmalert must not read as all-clear: its rule count is shown too.
    n_log_rules = sum(len(re.findall(r"^\s*- alert:", f.read_text(), re.M)) for f in sorted(LOG_RULES.glob("*.yml")))
    b.row("Now")
    stat(b, "Firing alerts (metrics and logs)",
         '(count(ALERTS{alertstate="firing", alertname!="Watchdog"}) or vector(0))'
         ' + (count(vmalert_alerts_firing{job="vmalert"} > 0) or vector(0))',
         steps=thresholds((None, "green"), (1, "orange")))
    stat(b, "Log alert rules running", 'count(vmalert_alerts_firing{job="vmalert"}) or vector(0)',
         steps=thresholds((None, "red"), (n_log_rules, "green")),
         description=f"{n_log_rules} rules in deploy/logs/alerts; fewer means vmalert is down or not scraped")
    stat(b, "Targets down", 'count(up == 0) or vector(0)', steps=BAD_OK)
    stat(b, "Containers down or unhealthy",
         'count(count by (service) ((ihr_container_running == 0) or (ihr_container_unhealthy == 1))) or vector(0)',
         steps=BAD_OK)
    stat(b, "Free memory", "node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes", "percentunit",
         steps=thresholds((None, "red"), (0.1, "orange"), (0.2, "green")))
    stat(b, "Free disk (/)", 'node_filesystem_avail_bytes{mountpoint="/"}', "bytes",
         steps=thresholds((None, "red"), (12e9, "orange"), (20e9, "green")))
    b.add("table", "Firing alerts (metrics)", [prom('ALERTS{alertstate="firing", alertname!="Watchdog"}', instant=True)],
          w=12, h=6, options={"showHeader": True},
          transformations=[{"id": "labelsToFields", "options": {}},
                           {"id": "organize", "options": {"excludeByName": {"Time": True, "Value": True, "__name__": True, "alertstate": True}}}])
    b.add("table", "Firing alerts (logs)", [prom('vmalert_alerts_firing{job="vmalert"} > 0', instant=True)],
          w=12, h=6, options={"showHeader": True},
          description="vmalert's log rules (deploy/logs/alerts); the value is how many are firing per rule",
          transformations=[{"id": "labelsToFields", "options": {}},
                           {"id": "organize", "options": {"excludeByName": {"Time": True, "__name__": True, "job": True, "instance": True}, "renameByName": {"Value": "firing"}}}])
    b.row("Host")
    series(b, "CPU busy", [prom('1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))', "busy")], "percentunit")
    series(b, "Memory", [prom("node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes", "used"),
                          prom("node_memory_SwapTotal_bytes - node_memory_SwapFree_bytes", "swap used", "B")], "bytes")
    series(b, "Disk free", [prom('node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs", mountpoint!~"/boot.*"}', "{{mountpoint}}")], "bytes")
    series(b, "Load (5 min)", [prom("node_load5", "load")])
    series(b, "Clock offset (chrony)", [prom("ihr_ntp_offset_seconds", "offset")], "s",
           description="CERT-In: NIC/NPL time; alerts at 100 ms and 1 s")
    series(b, "Out-of-memory kills", [prom("increase(node_vmstat_oom_kill[1h])", "per hour")])
    b.row("Containers")
    series(b, "Memory against limit", [prom("ihr_container_memory_working_set_bytes / (ihr_container_memory_limit_bytes > 0)", "{{service}}")],
           "percentunit", w=12, h=10)
    series(b, "Restarts (1 h)", [prom("increase(ihr_container_restarts_total[1h])", "{{service}}")], w=12, h=10)
    b.add("table", "Targets", [prom("up", instant=True)], w=24, h=8,
          transformations=[{"id": "labelsToFields", "options": {}},
                           {"id": "organize", "options": {"excludeByName": {"Time": True, "__name__": True}, "renameByName": {"Value": "up"}}}])
    return b


# ---- 2. Data and records --------------------------------------------------------------------
def data():
    b = Board("ihr-data", "Data and records",
              "Database, cache, backups, the log store and archive, the journal, keys and certificates (P1-T8c, P1-T9).")
    b.row("Now")
    stat(b, "Postgres", "min(pg_up)", steps=OK_BAD)
    stat(b, "Redis", "min(redis_up)", steps=OK_BAD)
    stat(b, "Last good backup", "time() - max(ihr_backup_last_success_timestamp_seconds)", "s",
         steps=thresholds((None, "green"), (26 * 3600, "red")))
    stat(b, "Archive: newest day ended", "time() - max(ihr_logs_archive_last_day_end_timestamp_seconds)", "s",
         steps=thresholds((None, "green"), (30 * 3600, "orange"), (4 * 86400, "red")))
    stat(b, "Archive bucket locked", "max(ihr_logs_archive_bucket_locked)", steps=OK_BAD)
    stat(b, "Not authentic in the archive", "max(ihr_logs_archive_bad_manifests) + max(ihr_logs_archive_check_problems or vector(0))",
         steps=BAD_OK)
    b.row("Postgres and Redis")
    series(b, "Connections per database", [prom("sum by (datname) (pg_stat_activity_count)", "{{datname}}")])
    series(b, "Longest transaction", [prom("max by (datname) (pg_stat_activity_max_tx_duration)", "{{datname}}")], "s")
    series(b, "WAL archiving failures", [prom("increase(pg_stat_archiver_failed_count[1h])", "per hour")])
    series(b, "Redis memory", [prom("redis_memory_used_bytes", "used"), prom("redis_memory_max_bytes", "max", "B")], "bytes")
    series(b, "Celery queue", [prom('redis_key_size{key="celery"}', "waiting tasks")])
    series(b, "Backup WAL spool", [prom("ihr_backup_spool_bytes", "spool"), prom("ihr_backup_spool_max_bytes", "cap", "B")], "bytes")
    b.row("Logs, archive and journal")
    series(b, "Lines into the log store", [prom("sum(rate(vl_rows_ingested_total[5m])) * 60", "lines/min")])
    series(b, "Log store disk against its cap", [prom("sum(vl_data_size_bytes)", "used"), prom("max(ihr_logs_max_bytes)", "cap", "B")], "bytes")
    series(b, "Redaction drops and lost lines", [
        prom('sum(increase(fluentbit_filter_drop_records_total{name=~"shape|redact"}[1h]))', "dropped by redaction"),
        prom('sum(increase(fluentbit_output_dropped_records_total{name="victorialogs"}[1h]))', "lost", "B")])
    series(b, "Journal against its cap", [prom("ihr_journal_disk_usage_bytes", "used"), prom("ihr_journal_max_use_bytes", "cap", "B")], "bytes")
    series(b, "Journal reaches back", [prom("time() - ihr_journal_oldest_entry_timestamp_seconds", "age of oldest entry")], "s",
           description="A 14-day buffer (ADR-014)")
    series(b, "Archive passes", [prom("ihr_logs_archive_last_run_ok", "last pass ok"), prom("ihr_logs_archive_days", "days archived", "B")])
    b.row("Keys and certificates")
    series(b, "Client key age", [prom("ihr_client_key_active_age_days", "{{purpose}}")], "d", w=12,
           description="Rotate every 180 days (Q-K18): scripts/staging.sh keys rotate")
    series(b, "Certificate expiry", [prom("(probe_ssl_earliest_cert_expiry - time()) / 86400", "{{endpoint}}")], "d", w=12)
    return b


# ---- 3. Traffic and sign-in -------------------------------------------------------------------
def traffic():
    b = Board("ihr-traffic", "Traffic and sign-in",
              "Requests through Traefik, the public probes, and sign-in failures and lock-outs from the logs (P1-T8d1, P1-T9b).")
    b.row("Requests")
    series(b, "Requests per router", [prom("sum by (router) (rate(traefik_router_requests_total[5m]))", "{{router}}")], "reqps", w=12)
    series(b, "Answers by status class", [prom('sum by (code) (rate(traefik_router_requests_total{code=~"[45].."}[5m]))', "{{code}}")], "reqps", w=12)
    series(b, "Too many requests (429)", [prom('sum by (router) (rate(traefik_router_requests_total{code="429"}[5m]))', "{{router}}")], "reqps", w=12,
           description="Per-IP limits (P1-T8d1); RateLimitedAddress alerts on one address")
    series(b, "95th percentile time", [prom("histogram_quantile(0.95, sum by (le, router) (rate(traefik_router_request_duration_seconds_bucket[5m])))", "{{router}}")],
           "s", w=12)
    b.row("Public probes")
    series(b, "Probe success", [prom("probe_success", "{{job}} {{endpoint}}{{service}}")], w=12)
    series(b, "Probe time", [prom("probe_duration_seconds", "{{job}} {{endpoint}}{{service}}")], "s", w=12)
    b.row("Sign-in (from the logs)")
    series(b, "Sign-in failures and lock-outs", [
        logs('source:=container service:=keycloak kc_event:in(LOGIN_ERROR, USER_DISABLED_BY_TEMPORARY_LOCKOUT, USER_DISABLED_BY_PERMANENT_LOCKOUT) | stats by (kc_event) count() n',
             "statsRange", legend="{{kc_event}}")], w=12, h=9)
    b.add("table", "Addresses with the most sign-in failures (selected time range)", [
        logs('source:=container service:=keycloak kc_event:=LOGIN_ERROR kc_error:in(invalid_user_credentials, user_not_found, invalid_user, user_temporarily_disabled, user_disabled) client_ip:* | stats by (client_ip) count() failures | sort by (failures desc) limit 20',
             "stats")], w=12, h=9,
          description="Names addresses, as the alert mails do (Q-O8). Usernames are not shown here.",
          # One frame per address comes back: labels to columns, then one table
          transformations=[{"id": "labelsToFields", "options": {"mode": "columns"}}, {"id": "merge", "options": {}},
                           {"id": "organize", "options": {"excludeByName": {"Time": True}}},
                           {"id": "sortBy", "options": {"sort": [{"field": "failures", "desc": True}]}}])
    return b


# ---- 4. Logs ----------------------------------------------------------------------------------
def logbook():
    # A custom variable whose values are LogsQL filters ("label : filter")
    services = "traefik,backend,keycloak,frontend,celery-worker,celery-beat,provisioner,staff-worker,db,redis,s3,backup,fluent-bit,victorialogs,vmalert,logs-archive,sshd,sudo,kernel,systemd".split(",")
    var_service = {"name": "service", "label": "Service", "type": "custom", "multi": False, "includeAll": False,
                   "query": ", ".join(["all : *"] + [f"{s} : service:={s}" for s in services]),
                   "current": {"text": "all", "value": "*"}}
    var_text = {"name": "search", "label": "Words", "type": "textbox", "query": "", "current": {"text": "", "value": ""}}
    b = Board("ihr-logs", "Logs",
              "The redacted log store (ADR-013): pick a service, type words to search. Personal data and secrets were masked before storage.",
              [var_service, var_text])
    # The level Grafana colours lines by. Host lines: journald's priority (0-3 error, 4 warning,
    # 5-7 info). Containers: Docker writes all of stderr at priority 3, and most services log
    # normally to stderr, so their level comes from the message instead.
    level = ('| format if (source:=host priority:<=3) "error" as level'
             ' | format if (source:=host priority:=4) "warning" as level'
             ' | format if (source:=host priority:>=5) "info" as level'
             ' | format if (source:=container) "info" as level'
             f' | format if (source:=container _msg:~`{WARN_RE}`) "warning" as level'
             f' | format if (source:=container _msg:~`{ERROR_RE}`) "error" as level')
    b.add("logs", "Lines", [logs("${service:raw} ${search:raw} " + level, "instant")],
          w=24, h=14, options={"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True})
    series(b, "Lines per service", [logs("* | stats by (service) count() n", "statsRange", legend="{{service}}")], w=12, h=8)
    series(b, "Error-level lines per service",
           [logs(f"(source:=host priority:<=3) or (source:=container _msg:~`{ERROR_RE}`) | stats by (service) count() n",
                 "statsRange", legend="{{service}}")],
           w=12, h=8, description="Host: journald priority 0–3. Containers: an error level in the message "
                                  "(level=error, ERROR, a Python traceback), since Docker marks all of stderr as priority 3")
    return b


BOARDS = [server, data, traffic, logbook]


def render():
    return {f"{fn().uid}.json": json.dumps(fn().json(), indent=1, sort_keys=True) + "\n" for fn in BOARDS}


def main():
    check = "--check" in sys.argv
    OUT.mkdir(exist_ok=True)
    stale = []
    for name, text in render().items():
        path = OUT / name
        if check:
            if not path.exists() or path.read_text() != text:
                stale.append(name)
        else:
            path.write_text(text)
    if check and stale:
        sys.exit(f"dashboards out of date (run deploy/grafana/build_dashboards.py): {', '.join(stale)}")
    print("dashboards " + ("current" if check else "written") + f": {len(BOARDS)}")


if __name__ == "__main__":
    main()
