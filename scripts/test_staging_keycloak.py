"""Static checks of Keycloak's production settings (P1-T8d1): the pre-built image, the staging
service, Traefik's forwarded headers and the per-IP limits in front of the sign-in pages. The
live proof is local-proof plus the checks listed in deploy/staging/README.md (Keycloak section).
Run: python3 -m unittest scripts/test_staging_keycloak.py
"""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "deploy" / "docker" / "keycloak.Dockerfile").read_text()
COMPOSE = yaml.safe_load((ROOT / "deploy" / "staging" / "compose.yaml").read_text())
ROUTES_TEXT = (ROOT / "deploy" / "staging" / "traefik" / "routes.yml").read_text()
ROUTES = yaml.safe_load(ROUTES_TEXT)
DEV = yaml.safe_load((ROOT / "deploy" / "dev" / "keycloak.override.yaml").read_text())
KC = COMPOSE["services"]["keycloak"]
ENV = KC["environment"]
TRAEFIK = COMPOSE["services"]["traefik"]
BUILD_TIME = ("KC_DB", "KC_HEALTH_ENABLED", "KC_METRICS_ENABLED", "KC_FEATURES", "KC_FEATURES_DISABLED")


def burst_ok(limit):
    """Traefik forgets an idle bucket after 1 + 1/rate seconds (2 s at 1/s or more), and a
    client that waits that long gets a full burst back: a burst above rate x that time
    raises the real limit. Period in s or m."""
    period = limit["period"]
    seconds = int(period[:-1]) * (60 if period.endswith("m") else 1)
    rate = limit["average"] / seconds
    ttl = 2 if rate >= 1 else 1 + int(1 / rate)
    return limit["burst"] <= max(1, round(rate * ttl))


class Image(unittest.TestCase):
    def test_pinned_and_optimized(self):
        self.assertRegex(DOCKERFILE, r"quay\.io/keycloak/keycloak:26\.8\.2@sha256:[0-9a-f]{64}")
        self.assertIn("kc.sh build", DOCKERFILE)
        self.assertIn('CMD ["start", "--optimized"]', DOCKERFILE)
        self.assertIn("USER 1000", DOCKERFILE)
        for opt in ("KC_DB=postgres", "KC_HEALTH_ENABLED=true", "KC_METRICS_ENABLED=true"):
            self.assertIn(opt, DOCKERFILE)

    def test_same_upstream_image_everywhere(self):
        digest = re.search(r"keycloak:26\.8\.2@(sha256:[0-9a-f]{64})", DOCKERFILE).group(1)
        self.assertIn(digest, DEV["services"]["keycloak"]["image"])
        self.assertIn(digest, (ROOT / ".github" / "workflows" / "apps.yml").read_text())


class Service(unittest.TestCase):
    def test_runs_our_build_optimized(self):
        self.assertEqual(KC["image"], "${IHR_KEYCLOAK_IMAGE:?set IHR_KEYCLOAK_IMAGE}")
        self.assertEqual(KC["command"], ["start", "--optimized"])
        # a build-time option at run time would differ from the image and stop the start
        for key in BUILD_TIME:
            self.assertNotIn(key, ENV)

    def test_single_node_cache(self):
        self.assertEqual(ENV["KC_CACHE"], "local")
        self.assertNotIn("KC_CACHE_STACK", ENV)

    def test_forwarded_headers_only_from_traefik(self):
        net = COMPOSE["networks"]["kcproxy"]
        self.assertTrue(net["internal"])
        self.assertEqual(ENV["KC_PROXY_TRUSTED_ADDRESSES"], net["ipam"]["config"][0]["subnet"])
        self.assertEqual(ENV["KC_PROXY_HEADERS"], "xforwarded")
        self.assertEqual(KC["networks"]["kcproxy"]["aliases"], ["keycloak-proxy"])
        self.assertIn("kcproxy", TRAEFIK["networks"])
        # Only Traefik and Keycloak on kcproxy, in every compose file of the stack
        for f in (ROOT / "deploy" / "staging").glob("compose*.yaml"):
            # (compose.local-journal.yaml uses Compose's !override tag: read as a plain value)
            text = re.sub(r"!(override|reset)\b", "", f.read_text())
            for name, svc in (yaml.safe_load(text).get("services") or {}).items():
                if "kcproxy" in (svc.get("networks") or []):
                    self.assertIn(name, ("traefik", "keycloak"), f.name)
        # Traefik reaches Keycloak by the kcproxy-only name, never "keycloak" (another network)
        servers = ROUTES["http"]["services"]["keycloak"]["loadBalancer"]["servers"]
        self.assertEqual(servers, [{"url": "http://keycloak-proxy:8080"}])
        # Traefik must keep dropping what clients send in X-Forwarded-*, also with the
        # certificate overlay (it replaces the whole command)
        acme = yaml.safe_load((ROOT / "deploy" / "staging" / "compose.acme.yaml").read_text())
        for cmd in (TRAEFIK["command"], acme["services"]["traefik"]["command"]):
            for ep in ("web", "websecure"):
                self.assertIn(f"--entrypoints.{ep}.http.aliasHeadersStrategy=delete", cmd)
            self.assertFalse([c for c in cmd if "forwardedHeaders" in c])
        self.assertNotIn("ipv4_address", str(TRAEFIK["networks"]))  # one-off runs share its settings

    def test_ready_means_http_200(self):
        for hc in (KC["healthcheck"]["test"][1], DEV["services"]["keycloak"]["healthcheck"]["test"][1]):
            self.assertIn("/health/ready", hc)
            self.assertIn("grep -q '^HTTP/1.[01] 200'", hc)
            self.assertNotIn('"UP"', hc)

    def test_events_shutdown_and_queue(self):
        self.assertEqual(ENV["KC_SPI_EVENTS_LISTENER__JBOSS_LOGGING__SUCCESS_LEVEL"], "info")
        self.assertEqual(ENV["KC_SPI_EVENTS_LISTENER__JBOSS_LOGGING__ERROR_LEVEL"], "warn")
        self.assertEqual(KC["stop_grace_period"], "30s")
        self.assertGreater(int(ENV["KC_HTTP_MAX_QUEUED_REQUESTS"]), 0)

    def test_realms_log_their_events(self):
        realm = (ROOT / "plugins/ihr_platform/ihr_platform/keycloak/realm.py").read_text()
        self.assertIn('"eventsListeners": ["jboss-logging"]', realm)


class Limits(unittest.TestCase):
    routers = ROUTES["http"]["routers"]
    middlewares = ROUTES["http"]["middlewares"]

    def test_sign_in_submissions_are_limited(self):
        login, reset = self.routers["auth-login"], self.routers["auth-reset"]
        for r in (login, reset):
            self.assertIn("!Method(`GET`) && !Method(`HEAD`)", r["rule"])
            self.assertIn("login-inflight", r["middlewares"])
            self.assertGreater(r["priority"], self.routers["closed"]["priority"])
        self.assertGreater(reset["priority"], login["priority"])
        self.assertIn("login-actions|protocol/openid-connect/(token|auth)", login["rule"])
        self.assertIn("login-ratelimit", login["middlewares"])
        self.assertIn("reset-ratelimit", reset["middlewares"])
        self.assertIn("auth-ratelimit", self.routers["auth"]["middlewares"])
        hosp = self.routers["hospital-auth"]
        # only the sign-in start: a refused sign-out would leave a shared computer signed in
        self.assertIn("PathPrefix(`/api/ihr/auth/login/`)", hosp["rule"])
        self.assertGreater(hosp["priority"], self.routers["hospitals"]["priority"])
        # its host pattern also matches console./auth./files., which must stay closed
        self.assertLess(hosp["priority"], self.routers["closed"]["priority"])
        self.assertLess(hosp["priority"], self.routers["files-closed"]["priority"])
        # static sign-in page files are never limited
        self.assertNotIn("/resources/", self.routers["auth"]["rule"])
        self.assertNotIn("middlewares", self.routers["auth-resources"])

    def test_status_and_ops_hosts_never_reach_the_web_app(self):
        # P1-T9a: "status" and "ops" match the hospitals' host pattern; the closed router (above
        # the hospitals and the hospital sign-in) takes them, status/healthz sits above it
        closed, status = self.routers["closed"], self.routers["status"]
        for host in ("status", "ops"):
            self.assertIn(f'Host(`{host}.{{{{ env "IHR_TENANT_DOMAIN_SUFFIX" }}}}`)', closed["rule"])
        self.assertGreater(closed["priority"], self.routers["hospital-auth"]["priority"])
        self.assertGreater(closed["priority"], self.routers["hospitals"]["priority"])
        self.assertGreater(status["priority"], closed["priority"])
        self.assertIn("status-ratelimit", status["middlewares"])
        # Keycloak's metrics stay off the public address (management port, monitoring network)
        self.assertNotIn("/metrics", self.routers["auth"]["rule"])

    def test_smoke_checks_the_limit(self):
        smoke = (ROOT / "scripts" / "staging.sh").read_text().split("smoke() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("sign-in submissions limited per address", smoke)

    def test_counted_per_client_address(self):
        for name, mw in self.middlewares.items():
            for kind in ("rateLimit", "inFlightReq"):
                if kind not in mw:
                    continue
                src = mw[kind]["sourceCriterion"]
                # never X-Forwarded-For (a client could forge it) or a header or the host
                self.assertEqual(list(src), ["ipStrategy"], name)
                self.assertNotIn("depth", src["ipStrategy"], name)
                self.assertNotIn("excludedIPs", src["ipStrategy"], name)

    def test_bursts_do_not_come_back_full(self):
        for name, mw in self.middlewares.items():
            if "rateLimit" in mw:
                self.assertTrue(burst_ok(mw["rateLimit"]), name)


if __name__ == "__main__":
    unittest.main()
