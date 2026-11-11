"""Static checks of the wildcard-certificate pieces (P1-T8b2): compose.acme.yaml,
compose.pebble.yaml, routes.yml, deploy/docker/acme-dns.Dockerfile and staging.sh.

The live proof is `scripts/staging.sh acme-proof` (CI job "Certificate proof"); these tests
catch the settings a later edit could quietly undo. Run: python3 -m unittest scripts/test_staging_acme.py
"""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "deploy" / "staging"


class _Loader(yaml.SafeLoader):
    pass


def load(path):
    return yaml.load((STAGING / path).read_text(), Loader=_Loader)


BASE = load("compose.yaml")
ACME = load("compose.acme.yaml")
PEBBLE = load("compose.pebble.yaml")
ROUTES = (STAGING / "traefik" / "routes.yml").read_text()
DOCKERFILE = (ROOT / "deploy" / "docker" / "acme-dns.Dockerfile").read_text()
STAGING_SH = (ROOT / "scripts" / "staging.sh").read_text()
DEPLOY_SH = (ROOT / "scripts" / "deploy.sh").read_text()


class TraefikTests(unittest.TestCase):
    def test_acme_command_is_the_base_command_plus_the_resolver(self):
        # Compose replaces a command list: the overlay must repeat the base one exactly
        base = BASE["services"]["traefik"]["command"]
        acme = ACME["services"]["traefik"]["command"]
        self.assertEqual(acme[: len(base)], base)
        extra = acme[len(base):]
        self.assertTrue(extra and all(a.startswith("--certificatesresolvers.le.acme.") for a in extra), extra)

    def test_metrics_entry_point_kept_with_the_certificate(self):
        # P1-T9a: the overlay replaces the whole list, so Prometheus's metrics port must be in it
        acme = ACME["services"]["traefik"]["command"]
        for flag in ("--entrypoints.metrics.address=:8083", "--metrics.prometheus=true",
                     "--metrics.prometheus.manualrouting=true"):
            self.assertIn(flag, acme)
        # The DNS probe reaches acme-dns on `dnsprobe` (acme-dns and blackbox-exporter alone), never
        # on the shared `monitoring` network: an internet-facing container must not reach the
        # backend, Keycloak, Prometheus or Alertmanager directly (P1-T9a review)
        nets = ACME["services"]["acme-dns"]["networks"]
        self.assertEqual(sorted(nets), ["acme", "dns", "dnsprobe"])
        self.assertTrue(BASE["networks"]["dnsprobe"]["internal"])
        # the API still answers only on the acme network's fixed address
        self.assertFalse(nets["dnsprobe"])

    def test_resolver_settings(self):
        cmd = "\n".join(ACME["services"]["traefik"]["command"])
        self.assertIn("dnschallenge.provider=acmedns", cmd)
        self.assertIn("storage=/data/acme.json", cmd)
        # Propagation pre-checks stay on (they protect Let's Encrypt's failure limits)
        self.assertNotIn("propagation", cmd)
        self.assertNotIn("disablePropagationCheck", cmd)
        self.assertIn("certificatesduration=${IHR_ACME_CERT_HOURS:-2160}", cmd)

    def test_traefik_stays_unprivileged(self):
        t = ACME["services"]["traefik"]
        self.assertNotIn("user", t)  # the base's 65534 stays
        self.assertNotIn("cap_add", t)
        self.assertEqual(BASE["services"]["traefik"]["user"], "65534:65534")

    def test_default_certificate_only_when_acme_is_on(self):
        self.assertIn('{{ if env "IHR_ACME_RESOLVER" }}', ROUTES)
        block = ROUTES.split('{{ if env "IHR_ACME_RESOLVER" }}', 1)[1]
        self.assertIn("defaultGeneratedCert", block)
        self.assertIn("main: '{{ env \"IHR_TENANT_DOMAIN_SUFFIX\" }}'", block)
        self.assertIn("sans: ['*.{{ env \"IHR_TENANT_DOMAIN_SUFFIX\" }}']", block)
        self.assertEqual(ACME["services"]["traefik"]["environment"]["IHR_ACME_RESOLVER"], "le")

    def test_data_volume_owner_set_with_two_capabilities_only(self):
        init = ACME["services"]["traefik-data"]
        self.assertEqual(sorted(init["cap_add"]), ["CHOWN", "FOWNER"])
        self.assertEqual(init["cap_drop"], ["ALL"])
        self.assertEqual(init["network_mode"], "none")
        dep = ACME["services"]["traefik"]["depends_on"]["traefik-data"]
        self.assertEqual(dep["condition"], "service_completed_successfully")


class AcmeDnsTests(unittest.TestCase):
    def test_hardened(self):
        a = ACME["services"]["acme-dns"]
        self.assertEqual(a["cap_drop"], ["ALL"])
        self.assertNotIn("cap_add", a)
        self.assertTrue(a["read_only"])
        self.assertIn("no-new-privileges:true", a["security_opt"])
        self.assertNotIn("user", a)  # the image's nonroot user
        self.assertIn("USER 65532:65532", DOCKERFILE)

    def test_only_dns_is_published(self):
        ports = ACME["services"]["acme-dns"]["ports"]
        self.assertEqual(len(ports), 2)
        for p in ports:
            self.assertRegex(p, r":53/(udp|tcp)$")
        self.assertNotIn("8053", " ".join(ports))

    def test_api_name_only_on_the_internal_network(self):
        nets = ACME["services"]["acme-dns"]["networks"]
        self.assertEqual(nets["acme"]["aliases"], ["acme-dns-api"])
        # the API binds to that one address, not 0.0.0.0 (the dns bridge reaches the host)
        self.assertEqual(nets["acme"]["ipv4_address"], "${IHR_ACME_API_IP:-172.30.53.5}")
        self.assertIn('ip = "$(acme_api_ip)"', STAGING_SH)
        self.assertNotIn('ip = "0.0.0.0"', STAGING_SH)
        self.assertTrue(ACME["networks"]["acme"]["internal"])
        self.assertEqual(ACME["services"]["traefik"]["environment"]["ACME_DNS_API_BASE"], "http://acme-dns-api:8053")

    def test_registration_closed_by_default_and_reclosed_on_failure(self):
        self.assertIn('disable_registration = $([[ "$1" == open ]] && echo false || echo true)', STAGING_SH)
        register = STAGING_SH.split("acme_register() {", 1)[1].split("\n}\n", 1)[0]
        self.assertLess(register.index("acme_dns_config open"), register.index("trap acme_close_registration EXIT"))
        # closed again before the failure check, and on any exit while open
        self.assertLess(register.index("acme_close_registration\n"), register.index("registration failed"))
        self.assertIn("acme_dns_config closed", STAGING_SH.split("acme_close_registration() {", 1)[1].split("\n}\n", 1)[0])
        # acme-dns must still know Traefik's key
        self.assertIn("acme-dns refuses Traefik's key", register)
        self.assertIn("allowfrom", register)
        up = STAGING_SH.split("acme_up() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("acme_dns_config closed", up)

    def test_account_key_never_printed(self):
        # traefik_sh output reaches the log driver (journald, 180 days): the register response
        # goes to a file, only the account's name is ever printed
        register = STAGING_SH.split("acme_register() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("-O /data/.acme-register.json", register)
        self.assertNotIn("-O -", register)
        self.assertIn("umask 077", register)


class PinningTests(unittest.TestCase):
    def test_dockerfile_pins_everything(self):
        froms = re.findall(r"^FROM (\S+)", DOCKERFILE, re.M)
        self.assertEqual(len(froms), 2)
        for f in froms:
            self.assertRegex(f, r"@sha256:[0-9a-f]{64}$")
        self.assertRegex(DOCKERFILE, r"ADD --checksum=sha256:[0-9a-f]{64}")
        self.assertRegex(DOCKERFILE, r"ARG ACMEDNS_COMMIT=[0-9a-f]{40}\n")
        self.assertIn("GOTOOLCHAIN=local", DOCKERFILE)
        self.assertIn("go mod verify", DOCKERFILE)

    def test_proof_images_pinned(self):
        for name in ("pebble", "coredns"):
            self.assertRegex(PEBBLE["services"][name]["image"], r":[\w.]+@sha256:[0-9a-f]{64}$")
            self.assertEqual(PEBBLE["services"][name]["cap_drop"], ["ALL"])

    def test_go_overlay_only_raises_versions(self):
        mod = (ROOT / "deploy" / "docker" / "acme-dns" / "go.mod").read_text()
        self.assertIn("module github.com/joohoi/acme-dns", mod)
        self.assertNotIn("replace ", mod)


class DeployTests(unittest.TestCase):
    def test_deploy_ships_acme_dns_only_when_enabled(self):
        self.assertIn('ACMEDNS="$(env_get IHR_ACMEDNS_IMAGE)"', DEPLOY_SH)
        self.assertIn('[[ -z "$ACMEDNS" ]] || ship_image "$ACMEDNS"', DEPLOY_SH)

    def test_overlays_follow_env(self):
        compose = STAGING_SH.split("compose() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('files+=(-f "$DIR/compose.acme.yaml")', compose)
        self.assertIn('[[ "$acme" == pebble ]] && files+=(-f "$DIR/compose.pebble.yaml")', compose)


if __name__ == "__main__":
    unittest.main()
