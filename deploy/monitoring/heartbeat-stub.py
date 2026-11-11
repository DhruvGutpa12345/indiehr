"""Stand-in for the outside dead-man switch (healthchecks.io) in a proof (P1-T9a).

Alertmanager posts the always-firing Watchdog alert here about once a minute, and the
"notifications failing" alert to /fail. This only counts what arrives: GET /count answers
{"ping": n, "fail": n, "last_ping": <unix time or null>}. Request bodies are read and dropped,
never stored or logged. Runs in compose.monitoring.test.yaml only.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COUNTS = {"ping": 0, "fail": 0, "last_ping": None}
LOCK = threading.Lock()
MAX_BODY = 1 << 20


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802 (http.server's naming)
        length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
        self.rfile.read(length)
        kind = {"/ping": "ping", "/ping/fail": "fail"}.get(self.path)
        if kind is None:
            self._reply(404, {"error": "unknown path"})
            return
        with LOCK:
            COUNTS[kind] += 1
            if kind == "ping":
                COUNTS["last_ping"] = int(time.time())
        self._reply(200, {"ok": True})

    def do_GET(self):  # noqa: N802
        if self.path != "/count":
            self._reply(404, {"error": "unknown path"})
            return
        with LOCK:
            self._reply(200, dict(COUNTS))

    def log_message(self, *args):
        pass  # no access log: nothing to keep


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
