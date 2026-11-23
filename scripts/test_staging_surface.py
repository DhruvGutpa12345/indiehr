"""The operator surface (P1-T30; docs/reviews/p1-t30/design.md, binding where they differ: section 10
"Attack and orchestrator decision"; Q-K140, Q-K141, Q-K142).

Three parts, none needing Docker (``docker`` is a stub where staging.sh runs for real):

- the freeze: deploy/staging/subcommands.txt is the reviewed list of staging.sh's subcommands and
  equals the case labels of ``main``; the README states the rule;
- ``scripts/staging.sh due`` and ``scripts/ops_due.py``: read-only, no operator session, ships with
  deploy.sh; rows, states, exit codes, IST dates, no personal data in the output;
- the alert tiers: every rule has a severity of critical, warning or info (Watchdog's ``none`` is
  exempt by name); the rendered Alertmanager tree sends critical to ``page``, immediate alerts
  (info, notify="anytime") to ``email``, warnings to the ``digest`` in two windows; the fixed matrix
  is also run through amtool by scripts/check-monitoring.sh (Docker; its hook filter covers the log
  alerts).

Run: python3 -m unittest scripts/test_staging_surface.py
"""

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import test_staging_operator as op  # noqa: E402  (a module, so its test classes are not collected here)

STAGING_SH = (ROOT / "scripts" / "staging.sh").read_text()
DEPLOY_SH = (ROOT / "scripts" / "deploy.sh").read_text()
README = (ROOT / "deploy" / "staging" / "README.md").read_text()
CHECK_MON = (ROOT / "scripts" / "check-monitoring.sh").read_text()
PRECOMMIT = (ROOT / ".pre-commit-config.yaml").read_text()
SUBCOMMANDS = ROOT / "deploy" / "staging" / "subcommands.txt"
OPS_DUE = ROOT / "scripts" / "ops_due.py"
RULE_DIRS = (ROOT / "deploy" / "monitoring" / "prometheus" / "rules", ROOT / "deploy" / "logs" / "alerts")

func = op.func


def rules():
    for d in RULE_DIRS:
        for f in sorted(d.glob("*.yml")):
            for group in yaml.safe_load(f.read_text())["groups"]:
                for rule in group["rules"]:
                    if "alert" in rule:
                        yield f, rule


# ---- (a) the freeze ------------------------------------------------------------------------


LABEL_LINE = re.compile(r'(?m)^  ("?[^\s()#;\]][^()\n]*?)\)(?=\s|$)')
LABEL_OK = re.compile(r"[a-z][a-z0-9-]*( *\| *[a-z][a-z0-9-]*)*|\*")


def labels_of(body):
    """The case labels of a dispatcher body, read loosely so an odd spelling cannot hide from the freeze (R5):
    any two-space-indented `label)`, quoted or with spaces around `|`; a label that is not a plain
    lower-case name (or `*`) is an error, and so is a body whose `;;` count is not its label count."""
    labels = []
    for raw in LABEL_LINE.findall(body):
        if not LABEL_OK.fullmatch(raw):
            raise AssertionError(f"a case label in an unreviewable spelling: {raw!r} (plain lower-case names only)")
        labels += [x.strip() for x in raw.split("|")]
    arms = len(LABEL_LINE.findall(body))
    if body.count(";;") != arms:
        raise AssertionError(f"{arms} case labels but {body.count(';;')} ';;' terminators in the dispatcher")
    return [x for x in labels if x != "*"]


def dispatcher_labels():
    return labels_of(func("main"))


class Freeze(unittest.TestCase):
    def listed(self):
        return [ln.strip() for ln in SUBCOMMANDS.read_text().splitlines() if ln.strip() and not ln.startswith("#")]

    def test_dispatcher_labels_are_the_reviewed_list(self):
        labels = dispatcher_labels()
        self.assertEqual(len(labels), len(set(labels)), "a label twice in main")
        listed = self.listed()
        self.assertEqual(len(listed), len(set(listed)), "a name twice in subcommands.txt")
        self.assertEqual(sorted(set(labels) ^ set(listed)), [],
                         "main's case labels and deploy/staging/subcommands.txt differ: a new subcommand needs the owner's OK (README, Operator surface)")

    def test_the_label_reader_cannot_be_dodged_by_an_odd_spelling(self):
        """R5: every odd way to write a new subcommand is caught, not skipped."""
        self.assertEqual(labels_of("  a-b|c) x ;;\n  *) y ;;\n"), ["a-b", "c"])
        self.assertEqual(labels_of("  dumps-now2 | dn2) x ;;\n"), ["dumps-now2", "dn2"])
        for body in ('  "new-cmd") y ;;\n', "  9lives) z ;;\n", "  Upper) z ;;\n",
                     "  ok) x ;;\n  bad_name) y ;;\n"):
            with self.subTest(body=body), self.assertRaises(AssertionError):
                labels_of(body)
        # a branch the label reader cannot see (odd indent) leaves a stray ';;'
        with self.assertRaises(AssertionError):
            labels_of("  ok) x ;;\n    sneaky) y ;;\n")

    def test_the_list_is_names_only_and_says_when_the_freeze_lifts(self):
        for ln in self.listed():
            self.assertRegex(ln, r"^[a-z][a-z0-9-]*$")
        head = "\n".join(ln for ln in SUBCOMMANDS.read_text().splitlines() if ln.startswith("#"))
        for word in ("PR1-1", "P1-T26", "owner", "commit message"):
            self.assertIn(word, head)

    def test_the_new_and_the_late_subcommands_are_listed(self):
        for name in ("due", "audit-verify"):
            self.assertIn(name, self.listed())

    def test_every_command_the_usage_and_ops_due_show_is_a_listed_subcommand(self):
        listed = set(self.listed())
        for m in re.findall(r"(?m)^#   scripts/staging\.sh ([a-z][a-z0-9-]*)", STAGING_SH):
            self.assertIn(m, listed)

    def test_readme_states_the_rule(self):
        section = README.split("## Operator surface (P1-T30)", 1)
        self.assertEqual(len(section), 2, "README has no section 'Operator surface (P1-T30)'")
        text = section[1]
        for word in ("deploy/staging/subcommands.txt", "PR1-1", "P1-T26", "freeze"):
            self.assertIn(word, text)


# ---- (b) due ---------------------------------------------------------------------------------

NOW = 1795428900  # 23-11-2026 15:45 IST


def metric(name, value, **labels):
    return {"metric": {"__name__": name, **labels}, "value": [NOW, str(value)]}


def prom(*results):
    return {"status": "success", "data": {"resultType": "vector", "result": list(results)}}


def firing(name, **labels):
    return {"metric": {"__name__": "ALERTS", "alertname": name, "alertstate": "firing", **labels}, "value": [NOW, "1"]}


def am_alert(name, **labels):
    return {"labels": {"alertname": name, **labels}, "status": {"state": "active"}}


def gauges_ok():
    return prom(
        metric("ihr_dump_oldest_object_timestamp_seconds", NOW - 20 * 86400),
        metric("ihr_dump_last_drill_timestamp_seconds", NOW - 10 * 86400),
        metric("ihr_logs_archive_oldest_day_timestamp_seconds", NOW - 100 * 86400),
        metric("ihr_client_keys_checked_timestamp_seconds", NOW - 600),
        metric("ihr_client_key_active_age_days", 29, purpose="backend"),
        metric("ihr_client_key_active_age_days", 29, purpose="onboarding"),
        metric("ihr_client_key_active_age_days", 29, purpose="staff"),
        metric("probe_ssl_earliest_cert_expiry", NOW + 60 * 86400, job="probe-public", endpoint="auth"),
        metric("probe_ssl_earliest_cert_expiry", NOW + 60 * 86400, job="probe-public", endpoint="status"),
        *live_ok(),
    )


def live_ok():
    """The `up` series of the targets whose gauges a row reads (dumper, log archiver, the public probe)."""
    return [metric("up", 1, job="dumps"), metric("up", 1, job="logs-archive"),
            metric("up", 1, job="probe-public", endpoint="auth"), metric("up", 1, job="probe-public", endpoint="status")]


class Fixture:
    """A directory of the answers due_cmd saves (one <id>.json per answered question, <id>.err otherwise)."""

    def __init__(self, tmp, alerts=None, gauges="ok", am=None, drop=()):
        self.dir = Path(tempfile.mkdtemp(prefix="answers.", dir=tmp))
        files = {"alerts-prom": prom(*(alerts or [])),
                 "gauges": gauges_ok() if gauges == "ok" else gauges,
                 "alerts-am": list(am or [])}
        for name, body in files.items():
            if name in drop:
                (self.dir / f"{name}.err").write_text("wget: server returned error")
            else:
                (self.dir / f"{name}.json").write_text(json.dumps(body))

    def render(self, features="dumps,logs-archive", now=NOW):
        args = ["python3", "-I", str(OPS_DUE), "render", str(self.dir), "--now", str(now)]
        if features is not None:
            args += ["--features", features]
        return subprocess.run(args, capture_output=True, text=True, timeout=30)


def rows_of(out):
    """item -> (state, line) from the table (the line starts with the kind)."""
    found = {}
    for ln in out.splitlines():
        m = re.match(r"^(chore|drill|key)\s+(.+?)\s{2,}(OVERDUE|UNREADABLE|ok|off)\s{2,}", ln)
        if m:
            found[m.group(2)] = (m.group(3), ln)
    return found


class OpsDue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def fx(self, **kw):
        return Fixture(self.tmp.name, **kw)

    def test_all_quiet_exits_0_and_lists_every_row_in_ist(self):
        out = self.fx().render()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("IndieHR due list at 23-11-2026 15:45 IST", out.stdout)
        rows = rows_of(out.stdout)
        self.assertEqual(sorted(rows), sorted([
            "dumps-expire", "hospital-restore-expire", "logs-archive-expire", "hospital-restore-drill",
            "staff-directory-check", "client key backend", "client key onboarding", "client key staff",
            "TLS certificate auth", "TLS certificate status"]))
        self.assertEqual({s for s, _ in rows.values()}, {"ok"})
        # the plain gauge the alert reads, as a date in IST (oldest dump 20 days before: 03-11-2026)
        self.assertIn("03-11-2026", rows["dumps-expire"][1])
        self.assertIn("13-11-2026", rows["hospital-restore-drill"][1])
        self.assertIn("29 days", rows["client key backend"][1])
        self.assertIn("22-01-2027", rows["TLS certificate auth"][1])

    def test_every_row_prints_the_command_to_run(self):
        rows = rows_of(self.fx().render().stdout)
        self.assertIn("scripts/staging.sh dumps-expire", rows["dumps-expire"][1])
        self.assertIn("scripts/staging.sh hospital-restore-expire", rows["hospital-restore-expire"][1])
        self.assertIn("scripts/staging.sh logs-archive-expire", rows["logs-archive-expire"][1])
        self.assertIn("scripts/staging.sh hospital-restore-drill", rows["hospital-restore-drill"][1])
        self.assertIn("scripts/staging.sh keys rotate onboarding", rows["client key onboarding"][1])
        self.assertIn("scripts/staging.sh cert", rows["TLS certificate status"][1])
        self.assertIn("ihr_staff_directory --check", rows["staff-directory-check"][1])

    def test_a_firing_alert_makes_its_row_overdue_and_exit_1(self):
        out = self.fx(alerts=[firing("DumpErasureOverdue", service="dumper")]).render()
        self.assertEqual(out.returncode, 1, out.stdout)
        rows = rows_of(out.stdout)
        self.assertEqual(rows["dumps-expire"][0], "OVERDUE")
        self.assertEqual(rows["logs-archive-expire"][0], "ok")
        self.assertIn("1 overdue", out.stdout)
        # overdue rows come first
        self.assertLess(out.stdout.index("dumps-expire"), out.stdout.index("hospital-restore-drill"))

    def test_each_row_follows_its_own_alert(self):
        cases = {
            "dumps-expire": firing("DumpErasureOverdue"),
            "logs-archive-expire": firing("LogArchiveErasureOverdue"),
            "hospital-restore-drill": firing("DumpDrillOverdue"),
            "client key staff": firing("ClientKeyAge", purpose="staff", severity="warning"),
            "TLS certificate auth": firing("TlsCertExpiry", endpoint="auth", severity="warning"),
        }
        for item, alert in cases.items():
            with self.subTest(item=item):
                rows = rows_of(self.fx(alerts=[alert]).render().stdout)
                self.assertEqual([i for i, (s, _) in rows.items() if s == "OVERDUE"], [item])

    def test_the_parked_schema_alert_comes_from_alertmanager(self):
        out = self.fx(am=[am_alert("ParkedSchemaOverdue", schema="ihr_parked_h_abcdef0123_20261101T101010")]).render()
        self.assertEqual(out.returncode, 1)
        rows = rows_of(out.stdout)
        self.assertEqual(rows["hospital-restore-expire"][0], "OVERDUE")
        # the schema name never reaches the output (nothing personal)
        self.assertNotIn("ihr_parked", out.stdout)
        self.assertNotIn("abcdef0123", out.stdout)

    def test_the_staff_directory_check_follows_its_log_alert(self):
        """P1-T31b: the weekly directory check's row follows StaffDirectoryDrift (Alertmanager's
        list, as the other log alert); the hospital never reaches the output."""
        rows = rows_of(self.fx().render().stdout)
        self.assertEqual(rows["staff-directory-check"][0], "ok")
        self.assertIn("weekly", rows["staff-directory-check"][1])
        out = self.fx(am=[am_alert("StaffDirectoryDrift", hospital="alpha")]).render()
        self.assertEqual(out.returncode, 1)
        rows = rows_of(out.stdout)
        self.assertEqual([i for i, (s, _) in rows.items() if s == "OVERDUE"], ["staff-directory-check"])
        self.assertIn("--repair", rows["staff-directory-check"][1])
        self.assertNotIn("alpha", out.stdout)

    def test_the_staff_directory_row_does_not_claim_a_drift_signal_it_cannot_get(self):
        """C2/C4: the weekly check runs in the unlogged admin one-off, so only a restore's
        failed rebuild reaches StaffDirectoryDrift: the row says so, and its quiet text never
        says that no drift was reported."""
        rows = rows_of(self.fx().render().stdout)
        quiet = rows["staff-directory-check"][1]
        self.assertNotIn("no drift", quiet)
        self.assertIn("by hand", quiet)
        fired = rows_of(
            self.fx(am=[am_alert("StaffDirectoryDrift", hospital="alpha")]).render().stdout
        )["staff-directory-check"][1]
        self.assertIn("restore", fired)
        self.assertNotIn("drift", fired)

    def test_an_unreadable_source_exits_2_and_wins_over_overdue(self):
        out = self.fx(alerts=[firing("DumpErasureOverdue")], drop=("gauges",)).render()
        self.assertEqual(out.returncode, 2, out.stdout)
        rows = rows_of(out.stdout)
        self.assertEqual(rows["dumps-expire"][0], "UNREADABLE")
        self.assertIn("unreadable", out.stdout.lower())
        self.assertEqual(rows["hospital-restore-expire"][0], "ok")  # its own source answered
        # overdue rows are still printed when 2 wins
        out = self.fx(alerts=[firing("LogArchiveErasureOverdue")], am=None, drop=("alerts-am",)).render()
        self.assertEqual(out.returncode, 2)
        rows = rows_of(out.stdout)
        self.assertEqual(rows["logs-archive-expire"][0], "OVERDUE")
        self.assertEqual(rows["hospital-restore-expire"][0], "UNREADABLE")

    def test_no_answer_at_all_is_unreadable(self):
        shutil_dir = Path(self.tmp.name) / "empty"
        shutil_dir.mkdir()
        out = subprocess.run(["python3", "-I", str(OPS_DUE), "render", str(shutil_dir), "--now", str(NOW),
                              "--features", "dumps"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertEqual({s for s, _ in rows_of(out.stdout).values()} - {"off"}, {"UNREADABLE"})

    def test_a_malformed_answer_is_unreadable(self):
        fx = self.fx()
        (fx.dir / "gauges.json").write_text('{"status": "error"}')
        out = fx.render()
        self.assertEqual(out.returncode, 2)
        self.assertEqual(rows_of(out.stdout)["dumps-expire"][0], "UNREADABLE")

    def test_off_rows_for_features_that_are_not_on(self):
        out = self.fx().render(features="")
        self.assertEqual(out.returncode, 0, out.stdout)
        rows = rows_of(out.stdout)
        self.assertEqual(rows["dumps-expire"][0], "off")
        self.assertEqual(rows["hospital-restore-drill"][0], "off")
        self.assertEqual(rows["logs-archive-expire"][0], "off")
        self.assertEqual(rows["client key backend"][0], "ok")
        # an off row never turns an overdue or unreadable answer into anything
        out = self.fx(alerts=[firing("DumpErasureOverdue")], drop=("gauges",)).render(features="logs-archive")
        self.assertEqual(rows_of(out.stdout)["dumps-expire"][0], "off")

    def test_a_series_that_is_absent_while_the_source_answers_is_ok(self):
        g = gauges_ok()
        g["data"]["result"] = [r for r in g["data"]["result"]
                               if r["metric"]["__name__"] not in ("ihr_dump_oldest_object_timestamp_seconds",
                                                                  "ihr_dump_last_drill_timestamp_seconds")]
        out = self.fx(gauges=g).render()
        self.assertEqual(out.returncode, 0, out.stdout)
        rows = rows_of(out.stdout)
        self.assertEqual(rows["dumps-expire"][0], "ok")
        self.assertEqual(rows["hospital-restore-drill"][0], "ok")

    def test_a_dead_target_is_unreadable_not_ok(self):
        """R2: the dumper, the log archiver or the public probe is down, so their gauges are gone; the alerts
        that read them cannot fire, but the list must not say ok (DumperDown fires meanwhile)."""
        gone = ("ihr_dump_oldest_object_timestamp_seconds", "ihr_dump_last_drill_timestamp_seconds",
                "ihr_logs_archive_oldest_day_timestamp_seconds", "probe_ssl_earliest_cert_expiry")
        for job, items in (("dumps", ("dumps-expire", "hospital-restore-drill")),
                           ("logs-archive", ("logs-archive-expire",)),
                           ("probe-public", ("TLS certificate auth", "TLS certificate status"))):
            for how in ("absent", "down"):
                with self.subTest(job=job, how=how):
                    g = gauges_ok()
                    g["data"]["result"] = [r for r in g["data"]["result"]
                                           if r["metric"]["__name__"] not in gone
                                           and not (r["metric"]["__name__"] == "up" and r["metric"]["job"] == job)]
                    if how == "down":
                        g["data"]["result"].append(metric("up", 0, job=job, endpoint="auth") if job == "probe-public"
                                                   else metric("up", 0, job=job))
                    out = self.fx(gauges=g).render()
                    self.assertEqual(out.returncode, 2, out.stdout)
                    rows = rows_of(out.stdout)
                    for item in items:
                        self.assertEqual(rows[item][0], "UNREADABLE", out.stdout)

    def test_a_down_target_is_unreadable_even_while_its_old_gauge_lingers(self):
        g = gauges_ok()
        for r in g["data"]["result"]:
            if r["metric"]["__name__"] == "up" and r["metric"]["job"] == "dumps":
                r["value"][1] = "0"
        out = self.fx(gauges=g).render()
        self.assertEqual(out.returncode, 2, out.stdout)
        self.assertEqual(rows_of(out.stdout)["dumps-expire"][0], "UNREADABLE")

    def test_a_dead_target_does_not_matter_for_a_feature_that_is_off(self):
        g = gauges_ok()
        g["data"]["result"] = [r for r in g["data"]["result"] if not (r["metric"]["__name__"] == "up"
                                                                       and r["metric"]["job"] in ("dumps", "logs-archive"))]
        out = self.fx(gauges=g).render(features="")
        self.assertEqual(out.returncode, 0, out.stdout)

    def test_a_purpose_with_no_active_key_is_overdue(self):
        """R3: ClientKeyMissing fires for the staff purpose; the age gauge is gone with the key."""
        g = gauges_ok()
        g["data"]["result"] = [r for r in g["data"]["result"]
                               if not (r["metric"]["__name__"] == "ihr_client_key_active_age_days"
                                       and r["metric"].get("purpose") == "staff")]
        out = self.fx(gauges=g, alerts=[firing("ClientKeyMissing", purpose="staff")]).render()
        self.assertEqual(out.returncode, 1, out.stdout)
        rows = rows_of(out.stdout)
        self.assertEqual(rows["client key staff"][0], "OVERDUE")
        self.assertIn("no active key", rows["client key staff"][1])
        self.assertEqual(rows["client key backend"][0], "ok")

    def test_a_missing_key_for_one_purpose_marks_only_that_keys_row(self):
        """N1: ClientKeyMissing{purpose=staff} with every target up must not touch the chores or the drill."""
        g = gauges_ok()
        g["data"]["result"] = [r for r in g["data"]["result"]
                               if not (r["metric"]["__name__"] == "ihr_client_key_active_age_days"
                                       and r["metric"].get("purpose") == "staff")]
        out = self.fx(gauges=g, alerts=[firing("ClientKeyMissing", purpose="staff")]).render()
        self.assertEqual(out.returncode, 1, out.stdout)
        for item, (state, _) in rows_of(out.stdout).items():
            self.assertEqual(state, "OVERDUE" if item == "client key staff" else "ok", f"{item}\n{out.stdout}")

    def test_a_drill_never_run_is_said_so(self):
        g = gauges_ok()
        for r in g["data"]["result"]:
            if r["metric"]["__name__"] == "ihr_dump_last_drill_timestamp_seconds":
                r["value"][1] = "0"
        rows = rows_of(self.fx(gauges=g).render().stdout)
        self.assertIn("never", rows["hospital-restore-drill"][1])

    def test_a_stale_or_absent_client_key_check_makes_the_keys_unreadable(self):
        for label, drop_name, stale in (("stale", None, NOW - 4 * 3600), ("absent", "ihr_client_keys_checked_timestamp_seconds", None)):
            with self.subTest(label):
                g = gauges_ok()
                for r in list(g["data"]["result"]):
                    if r["metric"]["__name__"] == "ihr_client_keys_checked_timestamp_seconds":
                        if drop_name:
                            g["data"]["result"].remove(r)
                        else:
                            r["value"][1] = str(stale)
                out = self.fx(gauges=g).render()
                self.assertEqual(out.returncode, 2, out.stdout)
                rows = rows_of(out.stdout)
                for purpose in ("backend", "onboarding", "staff"):
                    self.assertEqual(rows[f"client key {purpose}"][0], "UNREADABLE")
                self.assertEqual(rows["TLS certificate auth"][0], "ok")

    def test_the_stale_check_alert_itself_also_means_unreadable(self):
        out = self.fx(alerts=[firing("ClientKeyCheckStale", severity="warning")]).render()
        self.assertEqual(out.returncode, 2)

    def test_dates_are_ist_even_across_midnight(self):
        # 23-11-2026 23:50 UTC is 24-11-2026 05:20 IST
        now = 1795477800
        out = self.fx().render(now=now)
        self.assertIn("IndieHR due list at 24-11-2026 05:20 IST", out.stdout)

    def test_plan_lists_the_questions_with_encoded_paths(self):
        out = subprocess.run(["python3", "-I", str(OPS_DUE), "plan"], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        ids = []
        for ln in out.stdout.splitlines():
            ident, target, path = ln.split("\t")
            ids.append(ident)
            self.assertIn(target, ("prom", "am"))
            self.assertTrue(path.startswith("/api/"), path)
            self.assertNotRegex(path, r"""[ "{}]""")
        self.assertEqual(sorted(ids), ["alerts-am", "alerts-prom", "gauges"])

    def test_usage_errors_exit_2(self):
        for args in ([], ["frob"], ["render"], ["render", self.tmp.name], ["render", self.tmp.name, "--now", "x"]):
            with self.subTest(args=args):
                out = subprocess.run(["python3", "-I", str(OPS_DUE), *args], capture_output=True, text=True)
                self.assertEqual(out.returncode, 2)

    def test_output_holds_nothing_personal(self):
        out = self.fx(alerts=[firing("DumpErasureOverdue", hospital="alpha"), firing("ClientKeyAge", purpose="staff")],
                      am=[am_alert("ParkedSchemaOverdue", schema="ihr_parked_h_abcdef0123_20261101T101010",
                                   hospital="alpha")]).render()
        for ln in out.stdout.splitlines():
            self.assertNotIn("@", ln)
            self.assertNotIn("alpha", ln)
            self.assertNotRegex(ln, r"\b[0-9a-f]{8}-[0-9a-f]{4}-")
        self.assertTrue(out.stdout.isascii())

    def test_thresholds_named_in_the_rows_are_the_rules_own(self):
        """A row says which alert it follows; that alert must exist in the rules."""
        names = {r["alert"] for _, r in rules()}
        src = OPS_DUE.read_text()
        for alert in ("DumpErasureOverdue", "ParkedSchemaOverdue", "LogArchiveErasureOverdue", "DumpDrillOverdue",
                      "ClientKeyAge", "ClientKeyCheckStale", "TlsCertExpiry", "StaffDirectoryDrift"):
            self.assertIn(alert, names)
            self.assertIn(alert, src)

    def test_every_command_shown_is_a_listed_subcommand(self):
        listed = {ln.strip() for ln in SUBCOMMANDS.read_text().splitlines() if ln.strip() and not ln.startswith("#")}
        rows = rows_of(Fixture(self.tmp.name).render().stdout)
        for item, (_, ln) in rows.items():
            for m in re.findall(r"scripts/staging\.sh ([a-z][a-z0-9-]*)", ln):
                self.assertIn(m, listed, item)


BANNED_IN_DUE = ("op_need", "admin_run", "admin_manage", "migrate_run", "app_exec", "pg_su",
                 "audit_supersede_run", "restore_manage", "kt ", "vl_get", "logs_exec", "dumps_exec", "archive_exec")


class DueStatic(unittest.TestCase):
    def test_due_reaches_no_wrapper_and_no_operator_function(self):
        body = func("due_cmd")
        for word in BANNED_IN_DUE:
            self.assertNotIn(word, body)
        self.assertIn("prom_get", body)
        self.assertIn("ops_due.py", body)

    def test_due_is_a_dispatcher_label_and_a_job_tier(self):
        self.assertRegex(func("main"), r"(?m)^  due\) due_cmd ")
        self.assertIn("due", dispatcher_labels())
        tier = func("op_tier")
        self.assertNotRegex(tier.split("*)")[0], r"\bdue\b")  # not in a data-tier list: a job at most, never reached

    def test_due_has_a_usage_line_and_the_usage_printer_covers_the_whole_header(self):
        self.assertRegex(STAGING_SH, r"(?m)^#   scripts/staging\.sh due\b")
        printer = [ln for ln in STAGING_SH.splitlines() if ln.lstrip().startswith("*) sed -n")]
        self.assertEqual(len(printer), 1)
        self.assertNotRegex(printer[0], r"sed -n '5,[0-9]+p'")
        out = subprocess.run(["bash", str(ROOT / "scripts" / "staging.sh")], capture_output=True, text=True,
                             env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/"),
                                  "IHR_STAGING_DIR": tempfile.gettempdir()})
        self.assertEqual(out.returncode, 1)
        header = []
        for ln in STAGING_SH.splitlines()[4:]:
            if not ln.startswith("#"):
                break
            header.append(ln)
        self.assertEqual(out.stdout.splitlines(), header)

    def test_every_script_staging_sh_runs_is_shipped_by_deploy_sh(self):
        used = set(re.findall(r'python3 (?:-I )?"\$ROOT/scripts/([A-Za-z0-9_.-]+\.py)"', STAGING_SH))
        self.assertIn("ops_due.py", used)
        ship = DEPLOY_SH.split("git ls-files -z deploy/staging", 1)[1].split("| tar", 1)[0]
        shipped = set(re.findall(r"scripts/([A-Za-z0-9_.-]+\.py)", ship))
        self.assertEqual(sorted(used - shipped), [])

    def test_ci_may_not_need_a_session_for_due(self):
        # (the CI test of test_staging_operator lists the only commands CI calls)
        for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            self.assertNotRegex(wf.read_text(), r"scripts/staging\.sh due\b")


# A docker stub that answers wget by the URL it is given: fixtures by substring.
DOCKER_DUE_STUB = r'''#!/usr/bin/env bash
printf '%s\x1f' "$@" >> "$STUB_LOG"; printf '\n' >> "$STUB_LOG"
args=" $* "
if [[ "$args" == *" wget "* ]]; then
  url="${*: -1}"
  case "$url" in
    *":9093/api/v2/alerts"*) f=alerts-am ;;
    *"api/v1/query?query=ALERTS"*) f=alerts-prom ;;
    *"api/v1/query?query=%7B__name__"*) f=gauges ;;
    *) f=none ;;
  esac
  [[ -z "${DUE_FAIL:-}" || "$DUE_FAIL" != *"$f"* ]] || { echo "wget: error" >&2; exit 1; }
  [[ -f "$DUE_FIXTURES/$f.json" ]] || { echo "wget: no fixture for $url" >&2; exit 1; }
  cat "$DUE_FIXTURES/$f.json"; exit 0
fi
exit 0
'''


class DueStack(unittest.TestCase):
    def stack(self, **kw):
        stack = op.Stack(env_lines=("IHR_BACKEND_IMAGE=indiehr-backend:test", "IHR_MONITORING=on", "IHR_DUMPS=s3",
                                    *kw.get("extra_env", ())))
        self.addCleanup(stack.close)
        d = stack.bin / "docker"
        d.write_text(DOCKER_DUE_STUB)
        d.chmod(d.stat().st_mode | stat.S_IEXEC)
        return stack

    def run_due(self, stack, fixture, env=None):
        base = {"DUE_FIXTURES": str(fixture.dir), "IHR_OPERATOR": "alice"}
        base.update(env or {})
        return stack.run("due", env=base)

    def test_due_runs_end_to_end_and_writes_no_operator_line(self):
        with tempfile.TemporaryDirectory() as t:
            fx = Fixture(t)
            stack = self.stack()
            out = self.run_due(stack, fx)
            self.assertEqual(out.returncode, 0, out.stderr + out.stdout)
            self.assertIn("IndieHR due list at", out.stdout)
            self.assertIn("dumps-expire", out.stdout)
            self.assertEqual(stack.lines(), [])
            self.assertNotIn("ihr-operator", out.stderr)
            self.assertFalse([c for c in stack.calls() if "ihr_operator_open" in c])
            # every question went through Prometheus's own container, one wget each, no operator exec
            wgets = [c for c in stack.calls() if "wget" in c]
            self.assertEqual(len(wgets), 3)
            for c in wgets:
                self.assertIn("prometheus", c)

    def test_due_exit_codes_through_the_script(self):
        with tempfile.TemporaryDirectory() as t:
            stack = self.stack()
            fx = Fixture(t, alerts=[firing("DumpErasureOverdue")])
            self.assertEqual(self.run_due(stack, fx).returncode, 1)
            self.assertEqual(self.run_due(stack, fx, env={"DUE_FAIL": "gauges"}).returncode, 2)
            self.assertEqual(stack.lines(), [])

    def test_due_features_follow_the_env_flags(self):
        with tempfile.TemporaryDirectory() as t:
            fx = Fixture(t)
            out = self.run_due(self.stack(), fx)
            rows = rows_of(out.stdout)
            self.assertEqual(rows["dumps-expire"][0], "ok")
            self.assertEqual(rows["logs-archive-expire"][0], "off")

    def test_due_without_monitoring_exits_2_and_changes_nothing(self):
        stack = op.Stack(env_lines=("IHR_BACKEND_IMAGE=indiehr-backend:test",))
        self.addCleanup(stack.close)
        out = stack.run("due", env={"IHR_OPERATOR": "alice"})
        self.assertEqual(out.returncode, 2)
        self.assertIn("no monitoring", out.stderr)
        self.assertEqual(stack.lines(), [])

    def test_due_takes_no_arguments_and_leaves_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as t:
            stack = self.stack()
            fx = Fixture(t)
            before = set(os.listdir(stack.tmp.name))
            out = self.run_due(stack, fx)
            self.assertEqual(out.returncode, 0)
            self.assertEqual(set(os.listdir(stack.tmp.name)) - before, set())
            bad = stack.run("due", "--json", env={"DUE_FIXTURES": str(fx.dir)})
            self.assertEqual(bad.returncode, 2, bad.stderr)  # R4: exit 1 means "something is overdue"
            self.assertIn("usage", bad.stderr)


# ---- (c) alert tiers --------------------------------------------------------------------------


class Severity(unittest.TestCase):
    def test_every_rule_has_a_severity_of_critical_warning_or_info(self):
        bad = []
        for f, rule in rules():
            sev = (rule.get("labels") or {}).get("severity")
            if rule["alert"] == "Watchdog":
                if sev != "none":
                    bad.append((f.name, rule["alert"], sev))
            elif sev not in ("critical", "warning", "info"):
                bad.append((f.name, rule["alert"], sev))
        self.assertEqual(bad, [])

    def test_every_warning_log_alert_is_immediate(self):
        """Q-K143 C: a log alert is a rare event, so a warning among them is mailed at once (notify: anytime);
        only metric warnings wait for the digest. Critical and info rules need no label."""
        bad = [(f.name, rule["alert"]) for f, rule in rules()
               if f.parent == RULE_DIRS[1] and rule["labels"].get("severity") == "warning"
               and rule["labels"].get("notify") != "anytime"]
        self.assertEqual(bad, [])

    def test_the_rules_have_log_alerts_too(self):
        files = {f.parent.name for f, _ in rules()}
        self.assertEqual(files, {"rules", "alerts"})


def render_config(deadman="on", monitoring="on"):
    with tempfile.TemporaryDirectory() as d:
        Path(d, ".env").write_text("\n".join([
            "IHR_TENANT_DOMAIN_SUFFIX=app.example.in", f"IHR_MONITORING={monitoring}",
            "IHR_ALERT_TO=ops@example.in", "IHR_ALERT_FROM=alerts@example.in",
            "IHR_ALERT_SMTP_HOST=smtp.example.in", "IHR_ALERT_SMTP_PORT=587",
            "IHR_ALERT_SMTP_USER=emailapikey", f"IHR_ALERT_DEADMAN={deadman}", ""]))
        subprocess.run([str(ROOT / "scripts" / "staging.sh"), "monitoring-render"], check=True,
                       env={**os.environ, "IHR_STAGING_DIR": d}, capture_output=True)
        return yaml.safe_load(Path(d, "generated", "monitoring", "alertmanager.yml").read_text())


def matches(matcher, labels):
    m = re.fullmatch(r'(\w+)(=~|!~|!=|=)"(.*)"', matcher)
    name, how, val = m.groups()
    have = labels.get(name, "")
    if how == "=":
        return have == val
    if how == "!=":
        return have != val
    if how == "=~":
        return re.fullmatch(val, have) is not None
    return re.fullmatch(val, have) is None


def route_receivers(cfg, labels):
    """Alertmanager's route walk, first match with continue (the root matches everything)."""
    root = cfg["route"]
    got = []

    def walk(route):
        hit = False
        for sub in route.get("routes") or []:
            if all(matches(m, labels) for m in sub.get("matchers") or []):
                hit = True
                inner = walk(sub)
                if not inner:
                    got.append(sub["receiver"])
                if not sub.get("continue"):
                    return True
        return hit

    if not walk(root):
        got.append(root["receiver"])
    return got


# The fixed matrix (design section 10.4): labels -> receivers, with the dead-man switch on
MATRIX = [
    ({"alertname": "SomeCritical", "severity": "critical"}, ["page"]),
    ({"alertname": "SomeWarning", "severity": "warning"}, ["digest"]),
    ({"alertname": "SomeInfo", "severity": "info"}, ["email"]),
    ({"alertname": "FailedSignInsFromAddress", "severity": "warning", "notify": "anytime"}, ["email"]),
    ({"alertname": "Watchdog", "severity": "none"}, ["deadman"]),
    ({"alertname": "DailyHeartbeat", "severity": "info"}, ["email-heartbeat"]),
    ({"alertname": "AlertmanagerNotificationsFailing", "severity": "critical"}, ["deadman-fail", "page"]),
    ({"alertname": "MrdRecordsOffHours", "severity": "info"}, ["email"]),
    ({"alertname": "IhrAlertTest", "severity": "info"}, ["email"]),
]


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = render_config()
        cls.cfg_nodeadman = render_config(deadman="off")
        cls.cfg_test = render_config(monitoring="test")

    def test_the_fixed_matrix(self):
        for labels, want in MATRIX:
            with self.subTest(labels=labels):
                self.assertEqual(route_receivers(self.cfg, labels), want)

    def test_without_the_deadman_switch_its_alerts_go_to_the_blackhole(self):
        self.assertEqual(route_receivers(self.cfg_nodeadman, {"alertname": "Watchdog", "severity": "none"}), ["blackhole"])
        self.assertEqual(route_receivers(self.cfg_nodeadman, {"alertname": "AlertmanagerNotificationsFailing",
                                                              "severity": "critical"}), ["blackhole", "page"])

    def test_every_rule_lands_where_its_severity_says(self):
        special = {"Watchdog", "DailyHeartbeat", "AlertmanagerNotificationsFailing"}
        for f, rule in rules():
            if rule["alert"] in special:
                continue
            labels = {"alertname": rule["alert"], **(rule.get("labels") or {})}
            sev = labels["severity"]
            if sev == "critical":
                want = ["page"]
            elif labels.get("notify") == "anytime" or sev == "info":
                want = ["email"]
            else:
                want = ["digest"]
            with self.subTest(alert=rule["alert"], file=f.name):
                self.assertEqual(route_receivers(self.cfg, labels), want)

    def test_an_unknown_severity_is_loud_not_silent(self):
        got = route_receivers(self.cfg, {"alertname": "X", "severity": "bogus"})
        self.assertEqual(got, [self.cfg["route"]["receiver"]])
        self.assertNotIn(got[0], ("blackhole", "digest"))

    def test_the_route_order_is_fixed(self):
        names = [(r["receiver"], r["matchers"]) for r in self.cfg["route"]["routes"]]
        self.assertEqual(names, [
            ("deadman", ['alertname="Watchdog"']),
            ("email-heartbeat", ['alertname="DailyHeartbeat"']),
            ("deadman-fail", ['alertname="AlertmanagerNotificationsFailing"']),
            ("page", ['severity="critical"']),
            ("email", ['notify="anytime"']),
            ("digest", ['severity="warning"']),
        ])

    def test_the_page_receiver(self):
        page = next(r for r in self.cfg["route"]["routes"] if r["receiver"] == "page")
        self.assertEqual(page["repeat_interval"], "4h")
        self.assertNotIn("active_time_intervals", page)
        rcv = next(r for r in self.cfg["receivers"] if r["name"] == "page")
        mail = rcv["email_configs"][0]
        self.assertTrue(mail["headers"]["Subject"].startswith("[IndieHR PAGE]"))
        self.assertTrue(mail["send_resolved"])
        self.assertEqual(mail["to"], "ops@example.in")

    def test_the_immediate_routes_have_no_window(self):
        for r in self.cfg["route"]["routes"]:
            if r["receiver"] in ("page", "email"):
                self.assertNotIn("active_time_intervals", r)
        self.assertNotIn("active_time_intervals", self.cfg["route"])
        email = next(r for r in self.cfg["receivers"] if r["name"] == "email")
        self.assertTrue(email["email_configs"][0]["send_resolved"])
        self.assertEqual(self.cfg["receivers"][0]["name"], "email")

    def test_the_digest_timing_and_windows(self):
        digest = next(r for r in self.cfg["route"]["routes"] if r["receiver"] == "digest")
        self.assertEqual(digest["group_by"], ["severity"])
        self.assertEqual(digest["group_wait"], "30s")
        self.assertEqual(digest["group_interval"], "15m")
        self.assertEqual(digest["repeat_interval"], "23h")
        self.assertEqual(digest["active_time_intervals"], ["digest-ist"])
        self.assertNotIn("continue", digest)
        intervals = {t["name"]: t["time_intervals"] for t in self.cfg["time_intervals"]}
        self.assertEqual(list(intervals), ["digest-ist"])
        windows = intervals["digest-ist"]
        self.assertEqual({w["location"] for w in windows}, {"Asia/Kolkata"})
        times = sorted((t["start_time"], t["end_time"]) for w in windows for t in w["times"])
        self.assertEqual(times, [("09:00", "09:30"), ("17:00", "17:30")])
        rcv = next(r for r in self.cfg["receivers"] if r["name"] == "digest")
        mail = rcv["email_configs"][0]
        self.assertFalse(mail["send_resolved"])
        self.assertTrue(mail["headers"]["Subject"].startswith("[IndieHR digest]"))
        self.assertEqual(mail["to"], "ops@example.in")

    def test_the_old_warning_route_is_gone(self):
        text = op.func("monitoring_config")
        self.assertNotIn("daytime-ist", text)
        self.assertNotIn('"08:00"', text)

    def test_every_variant_keeps_the_same_tree_but_for_the_deadman(self):
        def shape(cfg):
            return [(r["matchers"], r.get("group_by"), r.get("repeat_interval"), r.get("active_time_intervals"))
                    for r in cfg["route"]["routes"]]
        self.assertEqual(shape(self.cfg), shape(self.cfg_nodeadman))
        self.assertEqual(shape(self.cfg), shape(self.cfg_test))

    def test_the_inhibit_rules_are_unchanged(self):
        self.assertEqual(self.cfg["inhibit_rules"][0]["source_matchers"], ['severity="critical"'])
        self.assertEqual(self.cfg["inhibit_rules"][0]["target_matchers"], ['severity="warning"'])
        self.assertEqual(len(self.cfg["inhibit_rules"]), 2)

    def test_the_unit_test_matrix_in_check_monitoring_matches_this_one(self):
        """scripts/check-monitoring.sh runs the same fixed matrix through amtool."""
        self.assertIn("amtool config routes test", CHECK_MON)
        self.assertIn("--verify.receivers", CHECK_MON)
        for labels, _ in MATRIX:
            self.assertIn(f"alertname={labels['alertname']}", CHECK_MON)
        for name in ("page", "digest", "deadman-fail", "email-heartbeat"):
            self.assertIn(name, CHECK_MON)


    def test_the_matrix_runs_in_one_container_per_variant(self):
        """R7: 27 `docker run` starts made every pre-commit run slow; one `sh` run per variant is enough."""
        block = CHECK_MON.split("Alert routing: the fixed matrix", 1)[1].split("blackbox_exporter's probes", 1)[0]
        self.assertEqual(block.count("amtool config routes test"), 1)
        self.assertEqual(block.count("run \"$AM\""), 1, "one container start per variant")
        self.assertIn("routes.sh", block)
        # the row loop only writes lines; it starts no container itself
        loop = block.split("for row in", 1)[1].split("done", 1)[0]
        self.assertNotIn("docker", loop)
        self.assertNotIn("run ", loop)


class Hooks(unittest.TestCase):
    def hook(self, ident):
        cfg = yaml.safe_load(PRECOMMIT)
        for repo in cfg["repos"]:
            for h in repo.get("hooks", []):
                if h["id"] == ident:
                    return h
        self.fail(f"no hook {ident}")

    def test_the_monitoring_hook_also_runs_for_the_log_alerts(self):
        files = self.hook("monitoring-promtool")["files"]
        self.assertRegex("deploy/logs/alerts/restore.yml", files)
        self.assertRegex("deploy/monitoring/prometheus/rules/dumps.yml", files)

    def test_the_surface_hook(self):
        h = self.hook("staging-surface-static")
        self.assertIn("scripts/test_staging_surface.py", h["entry"])
        for path in ("scripts/staging.sh", "scripts/ops_due.py", "deploy/staging/subcommands.txt",
                     "scripts/test_staging_surface.py", "deploy/logs/alerts/restore.yml",
                     "deploy/monitoring/prometheus/rules/dumps.yml", "scripts/deploy.sh",
                     "deploy/staging/README.md"):
            self.assertRegex(path, h["files"])


class Readme(unittest.TestCase):
    SECTION = README.split("## Operator surface (P1-T30)", 1)[-1]

    def test_section_and_its_parts(self):
        for word in ("scripts/staging.sh due", "Exit codes", "[IndieHR PAGE]", "digest", "09:00", "17:00",
                     "no rotation policy", "never mailed", "about 16 h"):
            self.assertIn(word, self.SECTION, word)

    def test_it_names_the_secrets_without_a_rotation_policy(self):
        for name in ("alertmanager-smtp-password", "dumps.env", "logs-archive.env", "anchor-events.env",
                     "keycloak.env", "s3-gateway.env"):
            self.assertIn(name, self.SECTION, name)

    def test_the_window_behaviour_is_cited_to_alertmanager(self):
        self.assertRegex(self.SECTION, r"https://github\.com/prometheus/alertmanager/blob/v0\.35\.0/notify/mute\.go")
        self.assertRegex(self.SECTION, r"https://github\.com/prometheus/alertmanager/blob/v0\.35\.0/dispatch/dispatch\.go")

    def test_no_text_still_describes_the_old_warning_route(self):
        self.assertNotIn("daytime-ist", README)
        self.assertNotRegex(README, r"mailed between 08:00 and\s+22:00")
        self.assertNotRegex(README, r"wait for 08:00.22:00")
        for path in list(RULE_DIRS[0].glob("*.yml")) + list(RULE_DIRS[1].glob("*.yml")):
            head = "\n".join(path.read_text().splitlines()[:6])
            self.assertNotRegex(head, r"08:00.{0,4}22:00", path.name)

    def test_warnings_that_must_not_wait_use_notify_anytime(self):
        self.assertIn('notify: anytime', self.SECTION)


class AlertTest(unittest.TestCase):
    def test_alert_test_is_still_an_immediate_info_alert(self):
        """alert-test sends severity=info: it must keep reaching the immediate receiver."""
        self.assertIn("severity=info", op.func("alert_test"))
        self.assertEqual(route_receivers(render_config(), {"alertname": "IhrAlertTest", "severity": "info"}), ["email"])


if __name__ == "__main__":
    unittest.main()
