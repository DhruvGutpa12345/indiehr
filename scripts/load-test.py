#!/usr/bin/env python3
"""Load test for the staff web path (P1-T23 groundwork; design docs/reviews/p1-t23/design.md §3).

Stdlib only. Signs virtual staff in through the real OIDC flow (Q-K97: GET
/api/ihr/auth/login/, Keycloak's login form, an optional one-time code, the callback), then
each one sends a read request every ``--interval`` seconds from a fixed mix, and at the middle
of the run ``--storm`` of them sign out at the same moment (the back-channel pool question,
Q-K95). It prints one JSON report: per request kind p50/p95/p99 and error counts, the sign-out
storm's timings and the share that were ended "everywhere".

Refuses any host that is not ``*.localhost`` or under the staging suffix of
``deploy/staging/.env`` (IHR_TENANT_DOMAIN_SUFFIX), and takes only synthetic users from a file
you name; no password or secret is ever printed or put on a command line:

    slug,username,password,totp_secret      (totp_secret empty when the account has none)

Examples
    scripts/load-test.py --users /path/users.csv --hospitals alpha,beta --staff 10 \\
        --duration 300 --storm 8 --suffix localhost --http-port 8080 --out result.json
    scripts/load-test.py --users /path/users.csv --hospitals h1,h2 --suffix app.example.in \\
        --scheme https --staff 20 --duration 1800 --storm 16     (staging, owner-run)

What it does not do (known limits): no writes (every kind below is a GET), so it measures the
read path and the pool, not billing or registration; one request kind per virtual user per
tick, not a recorded CARE mix; the numbers include this machine's own cost (run it from another
host for a real test).
"""

import argparse
import base64
import csv
import hashlib
import hmac
import html
import http.client
import json
import re
import struct
from collections import namedtuple
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, urljoin

ROOT = Path(__file__).resolve().parent.parent
# Reads only; 4xx answers (a role that may not see a page) count as "refused", never as errors
MIX = (
    ("session", "/api/ihr/auth/session/"),
    ("current-user", "/api/v1/users/getcurrentuser/"),
    ("facilities", "/api/v1/facility/?limit=10"),
    ("patients", "/api/v1/patient/?limit=10"),
    ("encounters", "/api/v1/encounter/?limit=10"),
    ("bootstrap", "/api/ihr/bootstrap/"),
)
TIMEOUT = 40


def staging_suffix():
    env = ROOT / "deploy" / "staging" / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("IHR_TENANT_DOMAIN_SUFFIX="):
                return line.split("=", 1)[1].strip()
    return ""


def allowed(host, suffix):
    host = host.lower()
    if host.endswith(".localhost") or host == "localhost":
        return True
    return bool(suffix) and (host == suffix or host.endswith("." + suffix))


Response = namedtuple("Response", "status body took url location")


class Jar:
    def __init__(self):
        self.cookies = {}

    def take(self, headers):
        for value in headers.get_all("Set-Cookie") or []:
            name, _, rest = value.partition("=")
            val = rest.split(";", 1)[0]
            if "max-age=0" in rest.lower() or val == "":
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = val

    def header(self):
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())


class Client:
    """One virtual user's browser: a cookie jar and manual redirects (hosts under .localhost are
    connected to 127.0.0.1, as a browser does)."""

    def __init__(self, suffix, scheme, allow_suffix):
        self.jar = {}
        self.suffix = suffix
        self.scheme = scheme
        self.allow_suffix = allow_suffix

    def request(self, method, url, data=None, headers=None, follow=0):
        parts = urlsplit(url)
        if not allowed(parts.hostname or "", self.allow_suffix):
            raise SystemExit(f"refusing {parts.hostname}: not *.localhost or the staging suffix")
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "https" else 80)
        connect_to = "127.0.0.1" if host.endswith(".localhost") or host == "localhost" else host
        cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = cls(connect_to, port, timeout=TIMEOUT)
        jar = self.jar.setdefault(host, Jar())
        send = {"Host": parts.netloc, "Cookie": jar.header(), "Accept": "text/html,application/json"}
        if headers:
            send.update(headers)
        body = None
        if data is not None:
            body = data if isinstance(data, bytes) else data.encode()
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        started = time.monotonic()
        conn.request(method, path, body=body, headers={k: v for k, v in send.items() if v != ""})
        response = conn.getresponse()
        payload = response.read()
        took = time.monotonic() - started
        jar.take(response.msg)
        conn.close()
        location = response.getheader("Location") or ""
        if follow and response.status in (301, 302, 303, 307) and location:
            return self.request("GET", urljoin(url, location), follow=follow - 1)
        return Response(response.status, payload, took, url, location)


def totp(secret, now=None):
    key = base64.b32decode(secret.replace(" ", "").upper() + "=" * (-len(secret) % 8))
    counter = int((now or time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return "%06d" % ((struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000)


FORM_ACTION = re.compile(r'<form[^>]*\baction="([^"]+)"', re.I)


def sign_in(client, base, username, password, secret):
    """The real flow. Returns seconds taken; raises RuntimeError with a short reason."""
    began = time.monotonic()
    page = client.request("GET", f"{base}/api/ihr/auth/login/?next=/", follow=6)
    if page.status != 200:
        raise RuntimeError(f"login page {page.status}")
    action = FORM_ACTION.search(page.body.decode("utf-8", "replace"))
    if not action:
        raise RuntimeError("no login form")
    form_headers = {"Content-Type": "application/x-www-form-urlencoded"}
    answer = client.request(
        "POST", html.unescape(action[1]),
        data=f"username={_q(username)}&password={_q(password)}&credentialId=", headers=form_headers)
    # a code is asked for: the account has an authenticator
    if answer.status == 200:
        text = answer.body.decode("utf-8", "replace")
        if 'name="otp"' not in text or not secret:
            raise RuntimeError("login refused, or a code is needed and none is configured")
        action = FORM_ACTION.search(text)
        answer = client.request(
            "POST", html.unescape(action[1]), data=f"otp={totp(secret)}&login=Sign+In", headers=form_headers)
    # back to the hospital: Keycloak's redirect, then the callback that sets the session cookie
    for _ in range(6):
        if answer.status not in (301, 302, 303) or not answer.location:
            break
        answer = client.request("GET", urljoin(answer.url, answer.location))
    else:
        raise RuntimeError("too many redirects")
    if answer.status >= 400:
        raise RuntimeError(f"callback {answer.status}")
    return time.monotonic() - began


def _q(text):
    from urllib.parse import quote_plus
    return quote_plus(text)


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * p / 100))], 3)


class Runner:
    def __init__(self, args):
        self.args = args
        self.results = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.storm_started = threading.Event()
        self.storm = []
        self.signins = []
        self.signin_failures = []

    def record(self, kind, status, seconds):
        with self.lock:
            self.results.setdefault(kind, []).append((status, seconds))

    def user(self, index, creds):
        a = self.args
        client = Client(a.suffix, a.scheme, staging_suffix())
        base = f"{a.scheme}://{creds['slug']}.{a.suffix}{a.port_part}"
        try:
            self.signins.append(sign_in(client, base, creds["username"], creds["password"], creds["totp_secret"]))
        except Exception as exc:
            with self.lock:
                self.signin_failures.append(f"user {index} {creds['slug']}: {exc}")
            return
        step = 0
        next_at = time.monotonic() + (index % max(1, int(a.interval))) * 1.0
        in_storm = index < a.storm
        while not self.stop.is_set():
            if in_storm and self.storm_started.is_set():
                self.sign_out(client, base)
                return
            if time.monotonic() >= next_at:
                kind, path = MIX[step % len(MIX)]
                step += 1
                try:
                    got = client.request("GET", base + path)
                    status, took = got.status, got.took
                except Exception:
                    status, took = 0, TIMEOUT
                self.record(kind, status, took)
                next_at = time.monotonic() + a.interval
            time.sleep(0.2)

    def sign_out(self, client, base):
        try:
            got = client.request(
                "POST", base + "/api/ihr/auth/logout/", data=b"{}",
                headers={"X-IHR-CSRF": "1", "Content-Type": "application/json"})
            status, took = got.status, got.took
            everywhere = status == 200 and json.loads(got.body or b"{}").get("signed_out_everywhere") is True
        except Exception:
            status, took, everywhere = 0, TIMEOUT, False
        with self.lock:
            self.storm.append((status, took, everywhere))

    def run(self, users):
        a = self.args
        threads = []
        for i in range(a.staff * len(a.hospitals)):
            creds = users[a.hospitals[i % len(a.hospitals)]][i // len(a.hospitals) % len(users[a.hospitals[i % len(a.hospitals)]])]
            t = threading.Thread(target=self.user, args=(i // 1, creds), daemon=True)
            t.start()
            threads.append(t)
            time.sleep(0.15)  # a ramp, not a stampede
        began = time.monotonic()
        if a.storm:
            time.sleep(max(0.0, a.duration / 2 - (time.monotonic() - began)))
            self.storm_started.set()
        time.sleep(max(0.0, a.duration - (time.monotonic() - began)))
        self.stop.set()
        for t in threads:
            t.join(timeout=TIMEOUT)

    def report(self):
        kinds = {}
        errors = 0
        total = 0
        for kind, rows in sorted(self.results.items()):
            times = [s for _, s in rows]
            bad = [st for st, _ in rows if st == 0 or st >= 500]
            refused = [st for st, _ in rows if 400 <= st < 500]
            errors += len(bad)
            total += len(rows)
            kinds[kind] = {"n": len(rows), "p50": percentile(times, 50), "p95": percentile(times, 95),
                           "p99": percentile(times, 99), "errors": len(bad), "refused_4xx": len(refused)}
        storm = {}
        if self.storm:
            took = [t for _, t, _ in self.storm]
            storm = {"signed_out": len(self.storm), "p50": percentile(took, 50), "max": round(max(took), 3),
                     "everywhere": sum(1 for _, _, e in self.storm if e),
                     "failed": sum(1 for s, _, _ in self.storm if s != 200)}
        return {"requests": total, "errors": errors,
                "error_rate": round(errors / total, 4) if total else None,
                "signins": len(self.signins), "signin_failures": self.signin_failures[:10],
                "signin_seconds_p95": percentile(self.signins, 95), "kinds": kinds, "storm": storm}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--users", required=True, help="CSV: slug,username,password,totp_secret (synthetic users only)")
    ap.add_argument("--hospitals", required=True, help="comma-separated slugs")
    ap.add_argument("--suffix", default="localhost", help="hospital address suffix (<slug>.<suffix>)")
    ap.add_argument("--scheme", default="http", choices=("http", "https"))
    ap.add_argument("--http-port", type=int, default=0, help="port of the hospitals' address (dev: 8080)")
    ap.add_argument("--staff", type=int, default=10, help="virtual staff per hospital")
    ap.add_argument("--interval", type=float, default=15.0, help="seconds between one user's requests")
    ap.add_argument("--duration", type=float, default=300.0)
    ap.add_argument("--storm", type=int, default=0, help="this many users sign out at the same moment, mid-run")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    args.hospitals = [h for h in args.hospitals.split(",") if h]
    args.port_part = f":{args.http_port}" if args.http_port else ""
    probe = f"x.{args.suffix}"
    if not allowed(probe, staging_suffix()):
        sys.exit(f"refusing suffix {args.suffix}: not .localhost and not the staging suffix in deploy/staging/.env")
    users = {}
    with open(args.users, newline="") as handle:
        for row in csv.DictReader(handle):
            users.setdefault(row["slug"], []).append(row)
    missing = [h for h in args.hospitals if h not in users]
    if missing:
        sys.exit(f"no users in the file for: {', '.join(missing)}")
    runner = Runner(args)
    runner.run(users)
    report = runner.report()
    text = json.dumps(report, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    return 1 if report["errors"] or report["signin_failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
