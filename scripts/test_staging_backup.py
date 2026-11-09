"""Static checks of the database backups (P1-T8c): postgres.Dockerfile, compose.backup.yaml,
the database service, the spool and schedule scripts and staging.sh. The live proof is
local-proof + `scripts/staging.sh restore-drill`. Run: python3 -m unittest scripts/test_staging_backup.py
"""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCKER = ROOT / "deploy" / "docker"
STAGING = ROOT / "deploy" / "staging"
DOCKERFILE = (DOCKER / "postgres.Dockerfile").read_text()
SPOOL = (DOCKER / "backup" / "wal-spool.sh").read_text()
SCHEDULE = (DOCKER / "backup" / "schedule.sh").read_text()
BASE = yaml.safe_load((STAGING / "compose.yaml").read_text())
BACKUP = yaml.safe_load((STAGING / "compose.backup.yaml").read_text())
STAGING_SH = (ROOT / "scripts" / "staging.sh").read_text()


def function(name):
    return STAGING_SH.split(f"{name}() {{", 1)[1].split("\n}\n", 1)[0]


class Image(unittest.TestCase):
    def test_pinned(self):
        for f in re.findall(r"^FROM (\S+)", DOCKERFILE, re.M):
            self.assertRegex(f, r"@sha256:[0-9a-f]{64}$")
        self.assertRegex(DOCKERFILE, r"ADD --checksum=sha256:[0-9a-f]{64}")
        self.assertIn("ARG PGBACKREST_VERSION=2.60.0", DOCKERFILE)

    def test_runs_as_postgres_without_gosu(self):
        self.assertIn("rm -f /usr/local/bin/gosu", DOCKERFILE)
        self.assertIn("\nUSER postgres\n", DOCKERFILE)


class Database(unittest.TestCase):
    def test_database_is_unprivileged_and_offline(self):
        db = BASE["services"]["db"]
        self.assertEqual(db["user"], "70:70")
        self.assertNotIn("cap_add", db)
        self.assertEqual(db["networks"], ["internal"])  # no way out, also with backups on
        self.assertNotIn("networks", BACKUP["services"]["db"])
        self.assertNotIn("env_file", BACKUP["services"]["db"])  # never the backup keys

    def test_archiving_goes_to_the_spool(self):
        cmd = BASE["services"]["db"]["command"]
        self.assertIn("archive_mode=${IHR_PG_ARCHIVE_MODE:-off}", cmd)
        # pgBackRest's check wants its name in archive_command
        self.assertIn("archive_command=/usr/local/bin/ihr-pgbackrest-spool %p %f", cmd)
        self.assertIn("archive_timeout=60", cmd)

    def test_spool_is_atomic_durable_and_bounded(self):
        self.assertIn('cp "$src" "$dir/.$name.tmp"', SPOOL)
        self.assertLess(SPOOL.index('sync "$dir/.$name.tmp"'), SPOOL.index('mv "$dir/.$name.tmp"'))
        self.assertLess(SPOOL.index('mv "$dir/.$name.tmp"'), SPOOL.index('sync "$dir"'))
        self.assertIn('cmp -s "$src" "$dir/$name"', SPOOL)
        # past the limit it refuses, so Postgres's own archiver shows the failure
        self.assertIn('if [ "$used" -ge "$max" ]; then', SPOOL)


class BackupService(unittest.TestCase):
    def test_hardened_and_reads_data_read_only(self):
        b = BACKUP["services"]["backup"]
        self.assertEqual(b["user"], "70:70")
        self.assertTrue(b["read_only"])
        self.assertEqual(b["cap_drop"], ["ALL"])
        self.assertIn("pgdata:/var/lib/postgresql:ro", b["volumes"])
        self.assertEqual(b["env_file"], ["secrets/backup.env"])

    def test_repository_settings(self):
        conf = function("backup_config")
        self.assertIn("repo1-cipher-type=aes-256-cbc", conf)
        # owner, 09-11-2026: any moment of 4 weeks, whatever extra fulls are taken
        self.assertIn("repo1-retention-full-type=time", conf)
        self.assertIn("repo1-retention-full=28", conf)
        self.assertIn("repo1-path=/pgbackrest/$(env_get IHR_TENANT_DOMAIN_SUFFIX)", conf)

    def test_schedule_order_and_checks(self):
        self.assertLess(SCHEDULE.index("stanza-create"), SCHEDULE.index("pusher &"))
        self.assertIn('[ "$(date +%u)" = "$FULL_DAY" ] && type=full', SCHEDULE)
        self.assertIn("--stanza=\"$STANZA\" --log-level-console=warn check", SCHEDULE)
        self.assertIn('export TZ="${TZ:-Asia/Kolkata}"', SCHEDULE)


class Secrets(unittest.TestCase):
    def test_bucket_key_never_from_the_command_line(self):
        init = function("backup_init")
        self.assertIn("read -rsp", init)
        self.assertIn("IFS= read -r key", init)
        self.assertIn('pass="$(rand 48)"', init)
        self.assertIn("--existing-passphrase) existing=1", init)
        self.assertIn("password manager", init)

    def test_backup_health_is_checked(self):
        hc = BACKUP["services"]["backup"]["healthcheck"]["test"]
        self.assertEqual(hc, ["CMD", "/usr/local/bin/ihr-backup-health"])
        self.assertIn("backups healthy", function("smoke"))

    def test_real_restore_never_overwrites_and_keeps_the_timeline(self):
        live = function("restore_live")
        self.assertIn("already holds a database", live)
        self.assertIn("archive_mode=on", live)  # the new timeline's history is pushed
        self.assertIn("walspool:/var/spool/ihr-wal", live)
        self.assertIn("the stack is running", live)

    def test_targets_carry_their_offset(self):
        self.assertIn("([+-][0-9]{2}:[0-9]{2}|Z)$", STAGING_SH)
        self.assertIn('check_target "$target"', function("restore_drill"))

    def test_drill_never_archives_and_can_recover(self):
        drill = function("restore_drill")
        self.assertIn("--as-of", drill)  # fingerprints only up to the copy's own moment
        self.assertIn("trap ", drill)  # leftovers always removed
        self.assertIn("archive_mode=off", drill)
        # recovery needs at least the original's max_connections
        self.assertIn("max_connections=100", drill)
        self.assertIn("max_connections=100", BASE["services"]["db"]["command"])
        self.assertIn('--env-file "$SECRETS/backup.env"', function("pg_container"))
        self.assertIn("ihr_audit_verify --all", drill)
        # the EXIT trap runs after the function's locals are gone: no bare ((keep)) in it
        trap = drill.split("\n  trap ", 1)[1].split("\n", 1)[0]
        self.assertNotIn("((keep))", trap)
        self.assertIn("$keep", trap)

    def test_real_restore_reaches_a_local_repository(self):
        live = function("restore_live")
        self.assertIn("compose up -d --wait s3", live)
        # the spool is swept between the restore and the recovery
        self.assertLess(live.index("ihr-backup-spool-sweep"), live.index("pg_container ihr-restore-recovery"))
        self.assertIn("ihr-backup-spool-sweep", DOCKERFILE)
        sweep = (DOCKER / "backup" / "spool-sweep.sh").read_text()
        self.assertIn('"$rc" = 44', sweep)  # only another database's WAL is set aside
        self.assertIn("rejected-wal", sweep)
        self.assertIn("exit 3", sweep)  # our own WAL that cannot be pushed stops the restore
        self.assertNotIn("kept=", sweep)

    def test_an_unfinished_restore_is_marked(self):
        live = function("restore_live")
        mark, unmark = live.index("touch /var/lib/postgresql/.ihr-restore-incomplete"), live.index("rm -f /var/lib/postgresql/.ihr-restore-incomplete")
        self.assertLess(mark, live.index("pgbackrest --stanza=main"))
        self.assertGreater(unmark, live.index("docker stop -t 60 ihr-restore-recovery"))
        self.assertIn("restoring) die", function("db_guard"))
        self.assertIn(".ihr-restore-incomplete", STAGING_SH.split("PG_STATE=", 1)[1].split("\n", 1)[0])

    def test_an_empty_database_never_replaces_a_lost_one(self):
        up = function("up")
        self.assertLess(up.index("db_guard"), up.index("compose up -d --wait db"))
        self.assertIn('touch "$DIR/.db-created"', up)
        self.assertIn("$PG_STATE", function("db_guard"))
        self.assertIn("PG_VERSION", STAGING_SH.split("PG_STATE=", 1)[1].split("\n", 1)[0])
        self.assertIn('rm -f "$DIR/.db-created"', STAGING_SH)  # only down -v clears it
        self.assertIn("deploy/staging/.db-created", (ROOT / ".gitignore").read_text())

    def test_a_new_server_with_backups_is_a_restore(self):
        guard = function("db_guard")
        self.assertIn("repo_info", guard)
        self.assertIn('"type":"full"', guard)
        self.assertIn("--new-database", guard)
        deploy = (ROOT / "scripts" / "deploy.sh").read_text()
        self.assertIn("--no-start) NO_START=1", deploy)


if __name__ == "__main__":
    unittest.main()
