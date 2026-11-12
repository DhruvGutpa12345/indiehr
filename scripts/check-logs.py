#!/usr/bin/env python3
"""Checks the log pipeline (P1-T9b, ADR-013) with the very images that run it, pinned by digest
in deploy/staging/compose.logs.yaml. Needs Docker; about 30 seconds; nothing leaves this machine.

1. Redaction: every case in deploy/logs/tests/redaction-cases.json goes through Fluent Bit with
   the server's own filters (deploy/logs/fluent-bit/filters.yaml, redact.lua). The stored line
   must be exactly the expected one, carry the expected fields, and contain none of the
   forbidden strings anywhere.
2. Storage: the output goes into a throwaway VictoriaLogs through the server's own ingestion
   address (the uri in fluent-bit.yaml); nothing forbidden is found there either.
3. Log alerts: vmalert checks deploy/logs/alerts/ (dry run), then runs them on the fixtures of
   deploy/logs/tests/alert-cases.json; each alert must fire (or wait to fire) for exactly the
   expected addresses.

Run by pre-commit (hook "logs-check") and so by CI. Usage: scripts/check-logs.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "deploy", "logs")
COMPOSE = os.path.join(ROOT, "deploy", "staging", "compose.logs.yaml")
FAIL = []


def image(name):
    m = re.search(r"image: (%s:[^ \n]*@sha256:[0-9a-f]{64})" % re.escape(name), open(COMPOSE).read())
    if not m:
        sys.exit("pinned image %s not found in %s" % (name, COMPOSE))
    return m.group(1)


FB = image("fluent/fluent-bit")
VL = image("victoriametrics/victoria-logs")
VMA = image("victoriametrics/vmalert")
# The label each alert names (the rest name client_ip): compared, not just counted
KEY_LABEL = {"PackApplyFailed": "hospital"}


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        FAIL.append(what)


def docker(*args, **kw):
    return subprocess.run(["docker", *args], check=True, text=True, capture_output=True, **kw).stdout


def http(url, data=None, timeout=10, ctype="application/stream+json"):
    # Never urllib's form type: VictoriaLogs would read a form-encoded body as parameters
    req = urllib.request.Request(url, data=data, headers={"Content-Type": ctype} if data is not None else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


def run_fluent_bit(work, records, name):
    with open(os.path.join(work, "input.jsonl"), "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    os.chmod(work, 0o755)
    os.chmod(os.path.join(work, "input.jsonl"), 0o644)
    out = subprocess.run(
        ["docker", "run", "--rm", "--name", name, "--network", "none", "--log-driver", "none",
         "-u", "%d:%d" % (os.getuid(), os.getgid()),
         "-v", "%s:/fluent-bit/etc/ihr:ro" % os.path.join(LOGS, "fluent-bit"),
         "-v", "%s:/t.yaml:ro" % os.path.join(LOGS, "tests", "fluent-bit-test.yaml"),
         "-v", "%s:/cases:ro" % work, FB, "-c", "/t.yaml"],
        text=True, capture_output=True, timeout=120)
    if out.returncode != 0:
        print(out.stderr[-3000:])
        sys.exit("Fluent Bit failed (exit %d)" % out.returncode)
    # Its own end-of-input notice is the one expected warning
    errors = [ln for ln in out.stderr.splitlines()
              if re.search(r"\[\s*(error|warn)\]", ln) and "service will shutdown" not in ln]
    check(not errors, "Fluent Bit ran the filters without errors or warnings" + ("".join("\n      " + e for e in errors[:5])))
    return [json.loads(ln) for ln in out.stdout.splitlines() if ln.startswith("{")]


def redaction(out, cases):
    for c, o in zip(cases, out):
        bad = []
        if "expect_msg" in c and o.get("msg") != c["expect_msg"]:
            bad.append("message\n        got:  %s\n        want: %s" % (o.get("msg"), c["expect_msg"]))
        for k, v in c.get("expect", {}).items():
            if o.get(k) != v:
                bad.append("%s: got %r, want %r" % (k, o.get(k), v))
        blob = json.dumps(o)
        bad += ["forbidden %r present" % f for f in c.get("forbid", []) if f in blob]
        check(not bad, "redaction: " + c["name"] + "".join("\n      " + b for b in bad))


def wait(url, what, seconds=60):
    for _ in range(seconds):
        try:
            http(url, timeout=2)
            return
        except Exception:
            time.sleep(1)
    sys.exit("%s did not start (%s)" % (what, url))


def main():
    cases = json.load(open(os.path.join(LOGS, "tests", "redaction-cases.json")))
    alerts = json.load(open(os.path.join(LOGS, "tests", "alert-cases.json")))
    fixtures = [f["record"] for f in alerts["fixtures"] for _ in range(f["count"])]
    work = tempfile.mkdtemp(prefix="ihr-logs-check.")
    net = "ihr-logs-check-%d" % os.getpid()
    names = ["%s-vl" % net, "%s-vmalert" % net, "%s-fb" % net]
    try:
        print("== Redaction (%s)" % FB)
        try:
            out = run_fluent_bit(work, [c["record"] for c in cases] + fixtures, names[2])
        except subprocess.TimeoutExpired as e:
            sys.exit("Fluent Bit did not finish within 120 s:\n%s" % ((e.stderr or b"")[-2000:] if isinstance(e.stderr, bytes) else (e.stderr or "")[-2000:]))
        check(len(out) == len(cases) + len(fixtures), "every line came through (%d of %d)" % (len(out), len(cases) + len(fixtures)))
        redaction(out, cases)

        print("== vmalert's rule files (%s)" % VMA)
        dry = subprocess.run(["docker", "run", "--rm", "--network", "none", "--log-driver", "none",
                              "-v", "%s:/r:ro" % os.path.join(LOGS, "alerts"), VMA,
                              "-dryRun", "-rule=/r/*.yml", "-rule.defaultRuleType=vlogs"],
                             text=True, capture_output=True, timeout=60)
        check(dry.returncode == 0, "vmalert -dryRun accepts deploy/logs/alerts/" + ("\n" + dry.stderr[-2000:] if dry.returncode else ""))

        print("== Storage and log alerts (%s)" % VL)
        docker("network", "create", net)
        docker("run", "-d", "--name", names[0], "--network", net, "--network-alias", "victorialogs",
               "--log-driver", "none", "-u", "%d:%d" % (os.getuid(), os.getgid()),
               "--mount", "type=tmpfs,destination=/vlogs,tmpfs-mode=1777", "-p", "127.0.0.1::9428",
               VL, "-storageDataPath=/vlogs", "-retentionPeriod=180d")
        vl = "http://" + docker("port", names[0], "9428").splitlines()[0].strip()
        wait(vl + "/health", "VictoriaLogs")
        uri = re.search(r"^\s+uri: (\S+)", open(os.path.join(LOGS, "fluent-bit", "fluent-bit.yaml")).read(), re.M).group(1)
        body = "".join(json.dumps(o) + "\n" for o in out).encode()
        http(vl + uri, data=body)
        http(vl + "/internal/force_flush", data=b"")  # POST only (v1.53)
        stored = http(vl + "/select/logsql/query?" + urllib.parse.urlencode({"query": "*", "limit": "100000"}), timeout=30)
        rows = [json.loads(ln) for ln in stored.splitlines() if ln.strip()]
        check(len(rows) == len(out), "VictoriaLogs stored every line (%d of %d)" % (len(rows), len(out)))
        forbidden = sorted({f for c in cases for f in c.get("forbid", []) if f in stored})
        check(not forbidden, "nothing forbidden in the store" + (": " + ", ".join(forbidden) if forbidden else ""))
        streams = {r.get("_stream") for r in rows}
        check(all(s and re.fullmatch(r'\{host="[^"]*",service="[^"]*",source="(container|host)"\}', s) for s in streams),
              "streams are host, service and source only (%d streams)" % len(streams))

        rules = os.path.join(work, "rules")
        os.mkdir(rules)
        for fn in os.listdir(os.path.join(LOGS, "alerts")):
            text = open(os.path.join(LOGS, "alerts", fn)).read()
            # Same rules, evaluated every 5 s instead of every minute
            open(os.path.join(rules, fn), "w").write(re.sub(r"(?m)^(\s+interval:) 1m$", r"\1 5s", text))
            os.chmod(os.path.join(rules, fn), 0o644)
        os.chmod(rules, 0o755)
        docker("run", "-d", "--name", names[1], "--network", net, "--log-driver", "none",
               "-u", "%d:%d" % (os.getuid(), os.getgid()), "-v", "%s:/r:ro" % rules, "-p", "127.0.0.1::8880", VMA,
               "-rule=/r/*.yml", "-rule.defaultRuleType=vlogs", "-datasource.url=http://victorialogs:9428",
               "-notifier.blackhole", "-rule.evalDelay=0s", "-httpListenAddr=:8880")
        vma = "http://" + docker("port", names[1], "8880").splitlines()[0].strip()
        wait(vma + "/health", "vmalert")
        want = {name: sorted(ips, key=str) for name, ips in alerts["expect"].items()}
        want_state = alerts["expect_state"]
        got, states = {}, {}
        for _ in range(60):
            data = json.loads(http(vma + "/api/v1/alerts"))["data"]["alerts"]
            got, states = {}, {}
            for a in data:
                # each alert's own key label: a wrong hospital or address fails the check
                key = KEY_LABEL.get(a["name"], "client_ip")
                got.setdefault(a["name"], []).append(a["labels"].get(key))
                states.setdefault(a["name"], set()).add(a["state"])
            got = {k: sorted(v, key=str) for k, v in got.items()}
            if got == want and all(states.get(n) == {s} for n, s in want_state.items()):
                break
            time.sleep(1)
        for name, ips in want.items():
            check(got.get(name) == ips, "alert %s for %s (got %s)" % (name, ips, got.get(name)))
            check(states.get(name) == {want_state[name]}, "alert %s is %s (got %s)" % (name, want_state[name], sorted(states.get(name, []))))
        extra = sorted(set(got) - set(want))
        check(not extra, "no other log alert fires" + (": " + ", ".join(extra) if extra else ""))
        groups = json.loads(http(vma + "/api/v1/rules"))["data"]["groups"]
        errs = [r["name"] + ": " + r["lastError"] for g in groups for r in g["rules"] if r.get("lastError")]
        check(not errs, "every log alert query runs without error" + "".join("\n      " + e for e in errs))
    finally:
        for n in names:
            subprocess.run(["docker", "rm", "-f", n], capture_output=True)
        subprocess.run(["docker", "network", "rm", net], capture_output=True)
        shutil.rmtree(work, ignore_errors=True)
    if FAIL:
        sys.exit("%d check(s) failed" % len(FAIL))
    print("Log pipeline OK.")


if __name__ == "__main__":
    main()
