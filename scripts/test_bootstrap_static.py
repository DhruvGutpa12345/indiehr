"""Static checks of the host kit (P1-T8b1): a careless edit to deploy/host/bootstrap.sh that
weakens a required control fails pre-commit. The behaviour itself is proven on a local VM
(scripts/local-vm.sh); this guards the settings, and runs a few of the script's own helper
functions (with stubbed commands) where a regression would lock the operator out.

Run: python3 -m unittest scripts/test_bootstrap_static.py
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = (ROOT / "deploy/host/bootstrap.sh").read_text()
DEPLOY = (ROOT / "scripts/deploy.sh").read_text()
KIT_FILES = [
    ROOT / "deploy/host/bootstrap.sh",
    ROOT / "deploy/host/README.md",
    ROOT / "scripts/deploy.sh",
    ROOT / "scripts/local-vm.sh",
]


def heredoc(function: str) -> str:
    """The body of the first heredoc inside a shell function of bootstrap.sh."""
    start = re.search(rf"^{re.escape(function)}\(\) \{{[^\n]*\n", BOOTSTRAP, re.M)
    assert start, f"function {function} not found"
    doc = re.search(r"<<'?EOF'?\n(.*?)^EOF$", BOOTSTRAP[start.end():], re.S | re.M)
    assert doc, f"no heredoc in {function}"
    return doc.group(1)


def function(name: str) -> str:
    """The full definition of a shell function of bootstrap.sh (one-line or multi-line)."""
    m = re.search(rf"^{re.escape(name)}\(\) \{{.*$", BOOTSTRAP, re.M)
    assert m, f"function {name} not found"
    if m.group(0).rstrip().endswith("}") and not m.group(0).rstrip().endswith("{"):
        line = m.group(0)
        if line.count("{") == line.count("}"):
            return line
    end = re.compile(r"^\}$", re.M).search(BOOTSTRAP, m.end())
    assert end, f"end of function {name} not found"
    return BOOTSTRAP[m.start():end.end()]


def run_bash(script: str, stdin: str = "") -> subprocess.CompletedProcess:
    prelude = ('set -euo pipefail\n'
               'die() { printf "DIE %s\\n" "$*"; exit 1; }\n'
               f"USER_RE='{assignment('USER_RE', quote=chr(39))}'\n"
               'DEPLOY_USER=ihr SSH_GROUP=ihr-ssh\n')
    return subprocess.run(["bash", "-c", prelude + script], input=stdin, text=True,
                          capture_output=True, timeout=30)


def assignment(name: str, quote: str = '"') -> str:
    m = re.search(rf'^{name}={quote}([^{quote}]*){quote}', BOOTSTRAP, re.M)
    assert m, f"{name} not set"
    return m.group(1)


def array(name: str) -> list[str]:
    m = re.search(rf"^{name}=\(([^)]*)\)", BOOTSTRAP, re.M | re.S)
    assert m, f"{name} not set"
    return re.findall(r'"([^"]+)"', m.group(1)) or m.group(1).split()


def settings(text: str, sep: str = " ") -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "[", "//")):
            continue
        key, _, value = line.partition(sep)
        out.setdefault(key.strip(), value.strip())  # sshd: the first value wins
    return out


SECONDS = {"s": 1, "min": 60, "h": 3600, "d": 86400, "day": 86400, "days": 86400,
           "week": 604800, "w": 604800, "month": 2629800, "year": 31557600,
           "years": 31557600, "y": 31557600}


def timespan(value: str) -> int:
    total = 0
    for number, unit in re.findall(r"(\d+)\s*([a-z]+)", value):
        assert unit in SECONDS, f"unknown unit {unit}"
        total += int(number) * SECONDS[unit]
    return total


class TimeTests(unittest.TestCase):
    def test_ntp_servers_are_nic_and_npl_only(self):
        self.assertEqual(
            array("NTP_SERVERS"), ["samay1.nic.in", "samay2.nic.in", "time.nplindia.org"]
        )

    def test_chrony_config_has_no_other_sources(self):
        body = re.search(r"^chrony_conf\(\) \{\n(.*?)^\}", BOOTSTRAP, re.S | re.M).group(1)
        self.assertIn('echo "server $s iburst"', body)
        for word in ("pool ", "sourcedir", "ntp.ubuntu.com", "\nallow"):
            self.assertNotIn(word, body)

    def test_chrony_started_with_our_config_and_timesyncd_removed(self):
        self.assertIn('-f /etc/chrony/indiehr.conf', BOOTSTRAP)
        self.assertIn("purge systemd-timesyncd", BOOTSTRAP)
        self.assertIn("ubuntu-ntp-pools.sources", BOOTSTRAP)

    def test_timezone_ist(self):
        self.assertEqual(assignment("TIMEZONE"), "Asia/Kolkata")

    def test_ntp_allowed_out(self):
        self.assertIn('ufw_rule "allow out 123/udp"', BOOTSTRAP)


class LogTests(unittest.TestCase):
    def test_journald_persistent_and_a_14_day_buffer(self):
        """The journal is a buffer (P1-T9c, owner Q-O3): at least 14 full days with one file a
        day, never much more (the log store and the archive are the 180/400-day record)."""
        conf = settings(heredoc("journald_conf"), "=")
        self.assertEqual(conf["Storage"], "persistent")
        retention = conf["MaxRetentionSec"].replace("$JOURNAL_MAX_RETENTION",
                                                    assignment("JOURNAL_MAX_RETENTION"))
        self.assertGreaterEqual(timespan(retention), 15 * 86400)
        self.assertLessEqual(timespan(retention), 31 * 86400)
        per_file = conf["MaxFileSec"].replace("$JOURNAL_MAX_FILE", assignment("JOURNAL_MAX_FILE"))
        self.assertLessEqual(timespan(per_file), 86400)
        self.assertIn("journalctl --rotate", BOOTSTRAP)
        self.assertIn("SystemMaxUse", conf)
        self.assertIn("SystemKeepFree", conf)

    def test_docker_logs_to_journald(self):
        daemon = heredoc("daemon_json")
        self.assertIn('"log-driver": "journald"', daemon)
        self.assertIn('"live-restore": true', daemon)
        self.assertIn('"iptables": true', daemon)


class SshTests(unittest.TestCase):
    REQUIRED = {
        "PasswordAuthentication": "no",
        "KbdInteractiveAuthentication": "no",
        "PermitRootLogin": "no",
        "PubkeyAuthentication": "yes",
        "MaxAuthTries": "3",
        "LoginGraceTime": "30",
        "X11Forwarding": "no",
        "AllowGroups": "$SSH_GROUP",
    }

    def test_sshd_dropin(self):
        conf = settings(heredoc("sshd_conf"))
        for key, value in self.REQUIRED.items():
            self.assertEqual(conf.get(key), value, key)
        self.assertEqual(assignment("SSH_GROUP"), "ihr-ssh")
        self.assertIn('SSHD_DROPIN="/etc/ssh/sshd_config.d/10-indiehr.conf"', BOOTSTRAP)

    def test_lockout_guards_come_before_the_sshd_change(self):
        guard = BOOTSTRAP.index('die "no SSH key for $DEPLOY_USER')
        write = BOOTSTRAP.index('write_file "$SSHD_DROPIN"')
        test = BOOTSTRAP.index("if ! sshd -t; then")
        reload_ = BOOTSTRAP.index("systemctl reload ssh.service")
        self.assertLess(guard, write)
        self.assertLess(write, test)
        self.assertLess(test, reload_)

    def test_effective_settings_checked_for_both_users_before_reload(self):
        # sshd -t is syntax only: AllowUsers elsewhere or a Match block can still lock out
        test = BOOTSTRAP.index("if ! sshd -t; then")
        effective = BOOTSTRAP.index('done < <(sshd_login_problems "$u" "$a")')
        reload_ = BOOTSTRAP.index("systemctl reload ssh.service")
        self.assertLess(test, effective)
        self.assertLess(effective, reload_)
        self.assertIn('for u in "$DEPLOY_USER" "$ADMIN_USER"; do', BOOTSTRAP)
        body = BOOTSTRAP[test:reload_]
        self.assertIn("restore_sshd", body[body.index("sshd_login_problems"):])
        # --check runs it too, for both users, and checks root separately
        check = function("run_check")
        self.assertIn('for u in "$DEPLOY_USER" "$admin"; do', check)
        self.assertIn("sshd_login_problems", check)
        self.assertIn("user=root,", check)

    def test_sshd_login_problems_flags_other_user_filters(self):
        stub = ("sshd() { printf '%s\\n' passwordauthentication\\ no kbdinteractiveauthentication\\ no "
                "permitrootlogin\\ no pubkeyauthentication\\ yes maxauthtries\\ 3 "
                "logingracetime\\ 30 x11forwarding\\ no permitemptypasswords\\ no "
                "allowgroups\\ ihr-ssh authenticationmethods\\ publickey \"${EXTRA[@]}\"; }\n")
        expect = re.search(r"^declare -A SSHD_EXPECT=\(.*?^\)", BOOTSTRAP, re.S | re.M).group(0)
        common = (stub + expect + "\n" + function("sshd_value") + "\n"
                  + function("sshd_login_problems") + "\n")
        clean = run_bash("EXTRA=()\n" + common + 'sshd_login_problems ihr 192.0.2.1')
        self.assertEqual((clean.returncode, clean.stdout), (0, ""), clean.stderr)
        for extra, word in (("allowusers root", "allowusers"), ("denygroups ihr-ssh", "denygroups"),
                            ("allowgroups wheel", "allowgroups"),
                            ("passwordauthentication yes", "passwordauthentication")):
            out = run_bash(f"EXTRA=('{extra}')\n" + common + 'sshd_login_problems ihr 192.0.2.1')
            self.assertIn(word, out.stdout, extra)

    def test_check_mode_expects_the_same_values(self):
        for key, value in self.REQUIRED.items():
            self.assertRegex(BOOTSTRAP, rf"\[{key.lower()}\]=\"?{re.escape(value)}\"?")

    def test_fail2ban_sshd_jail(self):
        conf = settings(heredoc("fail2ban_conf"), "=")
        self.assertEqual(conf["enabled"], "true")
        self.assertEqual(conf["backend"], "systemd")
        # the ports sshd really uses, not "ssh" (= 22 only); --check compares the same text
        self.assertEqual(conf["port"], "$1")
        self.assertIn('fail2ban_conf "${SSH_PORTS//', BOOTSTRAP)
        self.assertIn('"$(fail2ban_conf "${ports//', function("run_check"))


class KeyTests(unittest.TestCase):
    """Keys with options (cloud-init's forced command on root) must not count as a login."""

    @unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen not installed")
    def test_keys_with_options_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", f"{tmp}/k"],
                           check=True)
            key = Path(f"{tmp}/k.pub").read_text().strip()
        forced = ('no-port-forwarding,no-agent-forwarding,command="echo \'Please login as the '
                  'user \\"ubuntu\\"\';echo;sleep 10;exit 142" ' + key)
        out = run_bash(function("valid_keys") + "\nvalid_keys",
                       stdin=f"{forced}\nrestrict {key}\n# comment\nnot a key\n{key}\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.splitlines(), [key])
        self.assertIn("ignored a key with options", out.stderr)
        out = run_bash(function("valid_keys") + "\nvalid_keys", stdin=forced + "\n")
        self.assertEqual(out.stdout, "")

    def test_lockout_guard_counts_usable_keys_only(self):
        self.assertIn('valid_keys "${2:-quiet}" < "$f"', function("keys_of"))
        self.assertIn("keys_of", function("key_count"))
        self.assertIn('die "no SSH key for $ADMIN_USER', BOOTSTRAP)


class AdminTests(unittest.TestCase):
    """The admin chosen on the first run is kept: the every-boot run has no SUDO_USER."""

    def pick(self, saved: str, given: str = "", sudo_user: str = "") -> str:
        with tempfile.TemporaryDirectory() as tmp:
            if saved:
                Path(f"{tmp}/admin-user").write_text(saved + "\n")
            out = run_bash(f"ADMIN_FILE={tmp}/admin-user ADMIN_USER='{given}' SUDO_USER='{sudo_user}'\n"
                           "id() { return 1; }\n"  # no 'ubuntu' user
                           + function("saved_admin") + "\n" + function("pick_admin_user")
                           + "\npick_admin_user")
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_saved_admin_wins_over_sudo_user_and_defaults(self):
        self.assertEqual(self.pick(saved="ops"), "ops")
        self.assertEqual(self.pick(saved="ops", sudo_user="dhruv"), "ops")
        self.assertEqual(self.pick(saved="", sudo_user="dhruv"), "dhruv")
        self.assertEqual(self.pick(saved="", sudo_user="ihr"), "ihradmin")
        self.assertEqual(self.pick(saved=""), "ihradmin")
        self.assertEqual(self.pick(saved="ops", given="ops2"), "ops2")

    def test_admin_saved_and_change_needs_a_flag(self):
        self.assertIn('write_file "$ADMIN_FILE" 644', BOOTSTRAP)
        self.assertIn("--change-admin", BOOTSTRAP)
        conflict = BOOTSTRAP.index('"$ADMIN_USER" != "$SAVED_ADMIN" ]] && ((CHANGE_ADMIN == 0))')
        self.assertLess(conflict, BOOTSTRAP.index('if [[ "$MODE" == check ]]; then'))
        # one sudoers file per admin, so a different pick adds sudo and never takes it away
        self.assertIn('write_file "$(sudoers_file "$ADMIN_USER")" 440', BOOTSTRAP)
        self.assertNotIn("write_file /etc/sudoers.d/90-indiehr-admin ", BOOTSTRAP)
        self.assertIn('[ "$(saved_admin)" == "$admin" ]', function("run_check"))

    def test_admin_user_cannot_be_root_or_the_deploy_user(self):
        self.assertIn('"$ADMIN_USER" != root && "$ADMIN_USER" != "$DEPLOY_USER"', BOOTSTRAP)
        self.assertIn('"$ADMIN_USER" =~ $USER_RE', BOOTSTRAP)


class FirewallTests(unittest.TestCase):
    def test_ufw_rules(self):
        self.assertIn('for p in $SSH_PORTS; do ufw_rule "limit $p/tcp"', BOOTSTRAP)
        self.assertIn('ufw_rule "allow 80/tcp"', BOOTSTRAP)
        self.assertIn('ufw_rule "allow 443/tcp"', BOOTSTRAP)
        self.assertIn('ufw_rule "allow 53"', BOOTSTRAP)
        self.assertIn("ufw default deny incoming", BOOTSTRAP)
        # the SSH rule is added before ufw is switched on
        self.assertLess(BOOTSTRAP.index('ufw_rule "limit'), BOOTSTRAP.index("ufw --force enable"))

    def test_every_ssh_port_and_no_silent_fallback_to_22(self):
        ports = function("sshd_ports")
        self.assertNotIn("exit", ports)
        self.assertNotIn(":-22", ports)
        self.assertIn('[[ -n "$SSH_PORTS" ]] || die', BOOTSTRAP)

    def test_ipv6_switched_on_before_the_rules(self):
        ipv6 = BOOTSTRAP.index("s/^IPV6=.*/IPV6=yes/")
        self.assertLess(ipv6, BOOTSTRAP.index('ufw_rule "limit'))
        self.assertLess(ipv6, BOOTSTRAP.index("ufw --force enable"))
        self.assertIn("IPV6=yes", function("run_check"))

    def test_plain_allow_for_ssh_removed_after_the_limit_rule(self):
        delete = BOOTSTRAP.index('act "ufw delete $r')
        self.assertLess(BOOTSTRAP.index('ufw_rule "limit'), delete)
        self.assertIn("ssh_bypass_re", function("run_check"))
        regex = run_bash(function("ssh_bypass_re") + "\nssh_bypass_re 22").stdout.strip()
        bypass = ["ufw allow 22/tcp", "ufw allow 22", "ufw allow OpenSSH",
                  "ufw allow in 22/tcp comment 'x'", "ufw allow ssh"]
        fine = ["ufw limit 22/tcp comment 'SSH'", "ufw allow 2222/tcp", "ufw allow 220/tcp",
                "ufw allow from 10.0.0.5 to any port 22", "ufw allow 443/tcp"]
        for rule in bypass:
            self.assertRegex(rule, regex, rule)
        for rule in fine:
            self.assertNotRegex(rule, regex, rule)

    def test_docker_user_lets_only_80_443_and_53_in(self):
        rules = [l for l in heredoc("docker_user_block").splitlines() if l.startswith("-A")]
        ports = re.findall(r"-p (tcp|udp) .*--ctorigdstport (\d+)", "\n".join(rules))
        self.assertEqual(sorted(ports), [("tcp", "443"), ("tcp", "53"), ("tcp", "80"), ("udp", "53")])
        self.assertEqual(rules[-1], "-A DOCKER-USER -j DROP")
        for rule in rules[:-1]:
            self.assertTrue(rule.endswith(("-j RETURN", '"[IHR DOCKER-USER BLOCK] "')), rule)


class DockerTests(unittest.TestCase):
    def test_versions_pinned_exactly(self):
        for name in ("DOCKER_CE_VERSION", "CONTAINERD_VERSION", "BUILDX_VERSION", "COMPOSE_VERSION"):
            self.assertRegex(assignment(name), r"^(\d+:)?\d+\.\d+\.\d+-\d+$", name)

    def test_apt_key_checked_by_fingerprint(self):
        self.assertEqual(assignment("DOCKER_KEY_FINGERPRINT"),
                         "9DC858229FC7DD38854AE2D88D81803C0EBFCD88")
        self.assertIn("Signed-By: /etc/apt/keyrings/docker.asc", BOOTSTRAP)
        self.assertIn("key_fpr /etc/apt/keyrings/docker.asc", function("run_check"))

    def test_key_file_with_an_extra_primary_key_is_refused(self):
        # apt trusts every key in a Signed-By file: one primary key, Docker's, and nothing else
        fpr = assignment("DOCKER_KEY_FINGERPRINT")
        docker = f"pub:-:4096:1:8D81803C0EBFCD88:1:::-:::scESA::::::23::0:\nfpr:::::::::{fpr}:\n" \
                 "sub:-:4096:1:7EA0A9C3F273FCD8:1:::::s::::::23:\nfpr:::::::::SUBKEYFPR:\n"
        extra = "pub:-:255:22:AAAA:1:::-:::scESC::::::23::0:\nfpr:::::::::EVILFPR:\n"
        for text, want in ((docker, fpr), (docker + extra, f"{fpr}\nEVILFPR")):
            out = run_bash(f"gpg() {{ printf '%s' '{text}'; }}\n" + function("key_fpr") + "\nkey_fpr x")
            self.assertEqual(out.stdout.strip(), want)

    def test_docker_restarted_only_for_a_new_daemon_json(self):
        # at boot Docker may still be starting: a restart would bounce the restored containers
        self.assertIn('if ((daemon_changed)); then\n  act "restarted docker', BOOTSTRAP)
        self.assertIn('act "started docker" systemctl start docker', BOOTSTRAP)
        self.assertNotIn("((daemon_changed)) || ! systemctl is-active", BOOTSTRAP)
        self.assertNotIn("((restart_chrony)) || ! systemctl is-active", BOOTSTRAP)


class KernelTests(unittest.TestCase):
    def test_missing_sysctl_keys_do_not_stop_the_run(self):
        # ipv6.disable=1 removes net.ipv6.*: sysctl -p would fail and stop the first run
        self.assertIn("sysctl -q -e -p /etc/sysctl.d/60-indiehr.conf", BOOTSTRAP)
        self.assertIn('/proc/sys/${name//.//}', function("run_check"))


class RetentionTests(unittest.TestCase):
    def test_check_fails_when_logs_younger_than_14_days_are_gone(self):
        self.assertIn("check_log_retention", function("run_check"))
        body = function("check_log_retention")
        self.assertIn("log_days < 14", body)
        self.assertIn("$INSTALLED_FILE", body)
        self.assertIn('write_file "$INSTALLED_FILE"', BOOTSTRAP)


class DeployTests(unittest.TestCase):
    def test_image_built_from_committed_files_only(self):
        # git-ignored files (the dev JWKS key, .env files) must never be baked into the image
        self.assertIn("git archive --format=tar HEAD apps/backend plugins", DEPLOY)
        self.assertIn("--exclude-standard", DEPLOY)
        self.assertIn('-t "$BACKEND" "$CTX/apps/backend"', DEPLOY)
        self.assertNotIn("--build-context plugins=plugins", DEPLOY)

    def test_real_hosts_need_a_known_host_key(self):
        self.assertIn("-o StrictHostKeyChecking=yes", DEPLOY)

    def test_provenance_from_image_labels(self):
        self.assertIn('--label "$REV_LABEL=$SHA"', DEPLOY)
        self.assertIn('echo "git_sha=$(label_of "$BACKEND" "$REV_LABEL")"', DEPLOY)
        self.assertNotIn('echo "git_sha=$SHA"', DEPLOY)

    def test_unfinished_deploy_is_marked_and_old_images_pruned(self):
        self.assertLess(DEPLOY.index("/DEPLOYING"), DEPLOY.index('ship_image "$BACKEND"'))
        self.assertIn("rm -f $REMOTE_ROOT/DEPLOYING", DEPLOY)
        self.assertIn("docker image prune -f", DEPLOY)


class OsAndNeutralityTests(unittest.TestCase):
    def test_supported_releases(self):
        self.assertEqual(array("SUPPORTED_RELEASES"), ["26.04", "24.04"])

    def test_no_provider_named_in_the_kit(self):
        # ADR-011: provider names live only in infra/<provider>/ and .env
        pattern = re.compile(r"\b(aws|amazon|e2e ?networks|e2e|azure|gcp|google cloud|"
                             r"digitalocean|hetzner|linode|oracle|ec2|mumbai)\b", re.I)
        for path in KIT_FILES:
            hits = pattern.findall(path.read_text())
            self.assertEqual(hits, [], f"{path.relative_to(ROOT)} names a provider: {hits}")



class HostDnsTests(unittest.TestCase):
    def test_resolved_stub_off_for_acme_dns(self):
        self.assertIn("DNSStubListener=no", heredoc("resolved_conf"))
        self.assertIn('ln -sfn "$RESOLV_TARGET" /etc/resolv.conf', BOOTSTRAP)
        self.assertIn('RESOLV_TARGET="/run/systemd/resolve/resolv.conf"', BOOTSTRAP)
        check = function("run_check")
        self.assertIn("port 53 free on the host", check)
        self.assertIn("the host still resolves names", check)

if __name__ == "__main__":
    unittest.main()


class HostMetricsTests(unittest.TestCase):
    """P1-T9a: the timer that feeds node_exporter's textfile collector (no Docker socket in any
    container)."""

    def test_service_runs_as_the_deploy_user_every_minute(self):
        service, timer = heredoc("metrics_service"), heredoc("metrics_timer")
        self.assertIn("User=$DEPLOY_USER", service)
        self.assertIn("SupplementaryGroups=systemd-journal", service)
        self.assertIn("ExecStart=$SRV_DIR/scripts/staging.sh monitoring-textfile", service)
        self.assertIn("ExecCondition=/usr/bin/test -x $SRV_DIR/scripts/staging.sh", service)
        self.assertIn("NoNewPrivileges=yes", service)
        self.assertNotIn("User=root", service)
        self.assertIn("OnUnitActiveSec=1min", timer)
        self.assertIn("WantedBy=timers.target", timer)
        self.assertEqual(assignment("TEXTFILE_DIR"), "/var/lib/indiehr/textfile")

    def test_apply_and_check_agree(self):
        self.assertIn('install -d -m 755 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$TEXTFILE_DIR"', BOOTSTRAP)
        self.assertIn('systemctl enable --now "$METRICS_UNIT.timer"', BOOTSTRAP)
        check = function("run_check")
        self.assertIn('same_content "/etc/systemd/system/$METRICS_UNIT.service" "$(metrics_service)"', check)
        self.assertIn('same_content "/etc/systemd/system/$METRICS_UNIT.timer" "$(metrics_timer)"', check)
        self.assertIn('systemctl is-active --quiet "$METRICS_UNIT.timer"', check)
        self.assertIn("ihr_host.prom", check)
        # the root step's output is checked too (the deploy user cannot read chrony)
        self.assertIn('$CHRONY_READ_DIR/sources.csv" "$CHRONY_READ_DIR/tracking.csv" -mmin -5', check)
        self.assertIn("ihr_ntp_reading_ok 1", check)
        self.assertIn('"root 755"', check)

    def test_chrony_read_as_root_by_a_fixed_line_only(self):
        """chronyd answers root only (cmdport 0): one root step of the unit reads it, writing into
        a root-owned folder; nothing the deploy user can change runs as root."""
        service = heredoc("metrics_service")
        self.assertIn("cmdport 0", BOOTSTRAP)
        pre = [line for line in service.splitlines() if line.startswith("ExecStartPre=")]
        self.assertEqual(len(pre), 1, pre)
        line = pre[0]
        self.assertTrue(line.startswith("ExecStartPre=+/bin/sh -c '") and line.endswith("'"), line)
        for part in ("install -d -m 755 -o root -g root $CHRONY_READ_DIR", "cd $CHRONY_READ_DIR",
                     "/usr/bin/chronyc -N -c sources > sources.new", "/usr/bin/chronyc -c tracking > tracking.new",
                     "mv -f sources.new sources.csv", "mv -f tracking.new tracking.csv",
                     # a failed read leaves no stale file: the metrics say "unknown"
                     "else rm -f sources.new tracking.new sources.csv tracking.csv"):
            self.assertIn(part, line)
        for banned in ("$SRV_DIR", "$TEXTFILE_DIR", "$DEPLOY_USER", "staging.sh", "%", "$$"):
            self.assertNotIn(banned, line)
        self.assertEqual(assignment("CHRONY_READ_DIR"), "/run/indiehr-chrony")
        host_metrics = (ROOT / "deploy/monitoring/host-metrics.sh").read_text()
        self.assertIn('CHRONY_DIR="/run/indiehr-chrony"', host_metrics)
        # the script itself never runs chronyc (it would fail as the deploy user, silently)
        code = "\n".join(line for line in host_metrics.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotRegex(code, r"(\$\(|^\s*|\|\s*|&&\s*)chronyc\s")
        # the deploy user never joins chrony's group (its socket takes every control command)
        self.assertNotRegex(BOOTSTRAP, r"(usermod|gpasswd|adduser)[^\n]*chrony")
        self.assertNotIn("chrony", service.split("SupplementaryGroups=", 1)[1].splitlines()[0])
