#!/usr/bin/env python3
"""The due list of `scripts/staging.sh due` (P1-T30, Q-K141 A). Stdlib only; run as `python3 -I`.

It reads nothing itself: staging.sh fetches three answers (`plan` lists the questions) into a
private temporary folder, and `render` turns them into a table. A row is OVERDUE exactly when the
alert it follows is firing now (Prometheus's ALERTS series; the log alerts, which vmalert hands to
Alertmanager, from Alertmanager's alert list), so this list and the inbox never disagree and no
second bookkeeping exists. The plain gauge the alert reads is shown beside it as context.

    ops_due.py plan                                   id<TAB>prom|am<TAB>path, one per question
    ops_due.py render <dir> --now <epoch> [--features dumps,logs-archive]

States: OVERDUE (the alert fires), ok, off (the feature is not switched on here), UNREADABLE (a
source did not answer, or the key check is stale). Exit: 0 nothing firing, 1 something firing,
2 a source unreadable (2 wins over 1) or a usage error. Nothing personal is ever printed: no
hospital, no schema name, no operator.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

IST = timezone(timedelta(hours=5, minutes=30))
KEY_CHECK_MAX_AGE = 3 * 3600  # ClientKeyCheckStale (time-certs-keys.yml): not checked for 3 hours

GAUGES = (
    "ihr_dump_oldest_object_timestamp_seconds",
    "ihr_dump_last_drill_timestamp_seconds",
    "ihr_logs_archive_oldest_day_timestamp_seconds",
    "ihr_client_key_active_age_days",
    "ihr_client_keys_checked_timestamp_seconds",
    "probe_ssl_earliest_cert_expiry",
    "up",  # a row whose target is down or unknown is UNREADABLE, not ok (its gauge went stale with it)
)

# id -> (where, path). prom: Prometheus on 127.0.0.1:9090; am: Alertmanager's fixed address.
QUESTIONS = {
    "alerts-prom": ("prom", "/api/v1/query?query=" + quote('ALERTS{alertstate="firing"}', safe="")),
    "gauges": ("prom", "/api/v1/query?query=" + quote('{__name__=~"%s"}' % "|".join(GAUGES), safe="")),
    "alerts-am": ("am", "/api/v2/alerts"),
}

# Every row: what it is, which switched-on feature it needs, the alert it follows (and the source
# that alert is read from), the gauge shown as context, and the command to run.
ROWS = (
    dict(kind="chore", item="dumps-expire", feature="dumps", alert="DumpErasureOverdue", source="alerts-prom",
         live={"job": "dumps"}, gauge="ihr_dump_oldest_object_timestamp_seconds", label="oldest dump",
         run="scripts/staging.sh dumps-expire --dry-run, then without (your machine)"),
    dict(kind="chore", item="hospital-restore-expire", feature=None, alert="ParkedSchemaOverdue", source="alerts-am",
         gauge=None, label="parked schema", fired="a parked schema is over 8 days", quiet="none over 8 days",
         run="scripts/staging.sh hospital-restore-expire --dry-run, then without"),
    # P1-T31b (attack S9): the weekly backstop of the hospital switcher's directory. It has no gauge and
    # cannot see the check: that runs by hand in the unlogged admin one-off, so its drift lines never reach
    # a rule. The row follows the log alert, which only a restore's failed rebuild raises; the quiet text
    # is a reminder and says so (review C2)
    dict(kind="chore", item="staff-directory-check", feature=None, alert="StaffDirectoryDrift", source="alerts-am",
         gauge=None, label="staff directory", fired="a restore's directory rebuild failed in 24 hours (--repair)",
         quiet="weekly, by hand; this row cannot see whether it was run",
         run='scripts/staging.sh manage --operator <you> --reason "weekly check" ihr_staff_directory --check'),
    dict(kind="chore", item="logs-archive-expire", feature="logs-archive", alert="LogArchiveErasureOverdue",
         source="alerts-prom", live={"job": "logs-archive"},
         gauge="ihr_logs_archive_oldest_day_timestamp_seconds", label="oldest archived day",
         run="scripts/staging.sh logs-archive-expire --dry-run, then without (your machine)"),
    dict(kind="drill", item="hospital-restore-drill", feature="dumps", alert="DumpDrillOverdue", source="alerts-prom",
         live={"job": "dumps"}, gauge="ihr_dump_last_drill_timestamp_seconds", label="last drill", zero="never",
         run="scripts/staging.sh hospital-restore-drill"),
) + tuple(
    dict(kind="key", item=f"client key {p}", feature=None, alert="ClientKeyAge", source="alerts-prom",
         also=("ClientKeyMissing",), gauge="ihr_client_key_active_age_days", label="active key", match={"purpose": p},
         keycheck=True,
         run=f"scripts/staging.sh keys rotate {p}")
    for p in ("backend", "onboarding", "staff")
) + tuple(
    dict(kind="key", item=f"TLS certificate {e}", feature=None, alert="TlsCertExpiry", source="alerts-prom",
         live={"job": "probe-public"}, gauge="probe_ssl_earliest_cert_expiry", label="expires", match={"endpoint": e},
         gauge_match={"job": "probe-public"},
         run="scripts/staging.sh cert (acme-retry if it is stuck)")
    for e in ("auth", "status")
)

ORDER = {"OVERDUE": 0, "UNREADABLE": 1, "ok": 2, "off": 3}


class Unreadable(Exception):
    pass


def load(directory, ident):
    """One saved answer, or Unreadable (missing, an error marker, not JSON, the wrong shape)."""
    f = Path(directory) / f"{ident}.json"
    if (Path(directory) / f"{ident}.err").exists() or not f.is_file():
        raise Unreadable(ident)
    try:
        body = json.loads(f.read_text())
    except ValueError:
        raise Unreadable(ident) from None
    if ident == "alerts-am":
        if not isinstance(body, list):
            raise Unreadable(ident)
        return [a.get("labels") or {} for a in body if isinstance(a, dict)]
    try:
        if body["status"] != "success":
            raise Unreadable(ident)
        result = body["data"]["result"]
        if not isinstance(result, list):
            raise Unreadable(ident)
        return [(r["metric"], float(r["value"][1])) for r in result]
    except (KeyError, TypeError, ValueError, IndexError):
        raise Unreadable(ident) from None


def stamp(ts):
    return datetime.fromtimestamp(ts, IST).strftime("%d-%m-%Y %H:%M")


def evaluate(directory, now, features):
    sources = {}
    for ident in QUESTIONS:
        try:
            sources[ident] = load(directory, ident)
        except Unreadable:
            sources[ident] = None
    gauges = sources["gauges"]
    checked = None
    if gauges is not None:
        for metric, value in gauges:
            if metric.get("__name__") == "ihr_client_keys_checked_timestamp_seconds":
                checked = value
    out = []
    for row in ROWS:
        state, context = "ok", "-"
        if row["feature"] and row["feature"] not in features:
            out.append((row, "off", "not switched on here"))
            continue
        alerts = sources[row["source"]]
        needs_gauge = row["gauge"] is not None
        if alerts is None or (needs_gauge and gauges is None):
            out.append((row, "UNREADABLE", "no answer from its source"))
            continue
        want = row.get("match", {})
        names = (row["alert"], *row.get("also", ()))
        if row["source"] == "alerts-am":
            fires = any(lbl.get("alertname") in names for lbl in alerts)
        else:
            fires = any(m.get("alertname") in names and all(m.get(k) == v for k, v in want.items())
                        for m, _ in alerts)
        # only the client-key rows can be missing a key; a missing key for one purpose says nothing about a chore
        missing = row["source"] != "alerts-am" and "ClientKeyMissing" in names and any(
            m.get("alertname") == "ClientKeyMissing" and all(m.get(k) == v for k, v in want.items()) for m, _ in alerts)
        if row.get("keycheck"):
            stale = checked is None or now - checked > KEY_CHECK_MAX_AGE
            stale = stale or any(m.get("alertname") == "ClientKeyCheckStale" for m, _ in alerts)
            if stale:
                out.append((row, "UNREADABLE", "the key check is stale or never ran"))
                continue
        if row.get("live") and gauges is not None:
            lwant = {**want, **row["live"]}
            up = [v for m, v in gauges if m.get("__name__") == "up" and all(m.get(k) == x for k, x in lwant.items())]
            if not up or min(up) < 1:
                out.append((row, "UNREADABLE", "its target is down or unknown (no gauge can be trusted)"))
                continue
        if missing:
            out.append((row, "OVERDUE", "no active key (ClientKeyMissing fires)"))
            continue
        if needs_gauge:
            value = None
            gwant = {**want, **row.get("gauge_match", {})}
            for m, v in gauges:
                if m.get("__name__") == row["gauge"] and all(m.get(k) == x for k, x in gwant.items()):
                    value = v
            if value is None:
                context = f"{row['label']}: none yet"
            elif row["gauge"] == "ihr_client_key_active_age_days":
                context = f"{row['label']} {value:.0f} days old"
            elif value == 0 and row.get("zero"):
                context = f"{row['label']}: {row['zero']}"
            else:
                context = f"{row['label']} {stamp(value)}"
        else:
            context = row["fired"] if fires else row["quiet"]
        state = "OVERDUE" if fires else "ok"
        out.append((row, state, context))
    return out


def render(directory, now, features):
    results = evaluate(directory, now, features)
    results.sort(key=lambda r: (ORDER[r[1]], r[0]["kind"], r[0]["item"]))
    lines = [f"IndieHR due list at {stamp(now)} IST"]
    table = [("KIND", "ITEM", "STATE", "CONTEXT", "COMMAND")]
    table += [(r["kind"], r["item"], state, context, r["run"]) for r, state, context in results]
    widths = [max(len(t[i]) for t in table) for i in range(4)]
    for t in table:
        lines.append("  ".join(t[i].ljust(widths[i]) for i in range(4)) + "  " + t[4])
    overdue = sum(1 for _, s, _ in results if s == "OVERDUE")
    unreadable = sum(1 for _, s, _ in results if s == "UNREADABLE")
    code = 2 if unreadable else (1 if overdue else 0)
    lines.append(f"{overdue} overdue, {unreadable} unreadable. Exit {code}.")
    print("\n".join(lines))
    return code


def usage():
    print("usage: ops_due.py plan | render <dir> --now <epoch> [--features dumps,logs-archive]", file=sys.stderr)
    return 2


def main(argv):
    if argv[:1] == ["plan"] and len(argv) == 1:
        for ident, (where, path) in QUESTIONS.items():
            print(f"{ident}\t{where}\t{path}")
        return 0
    if argv[:1] == ["render"] and len(argv) >= 4 and argv[2] == "--now":
        try:
            now = int(argv[3])
        except ValueError:
            return usage()
        features = set()
        rest = argv[4:]
        if rest:
            if len(rest) != 2 or rest[0] != "--features":
                return usage()
            features = {f for f in rest[1].split(",") if f}
        return render(argv[1], now, features)
    return usage()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
