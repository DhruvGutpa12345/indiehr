"""Releases and rollbacks (P1-T18b; docs/reviews/p1-t18/design.md items 11-17).

- scripts/release_log.py: the release log's lines, the stack of releases in force, the rollback's
  target and decision, which images are kept, when a plain start is enough;
- scripts/staging.sh release / rollback: the order of the window's steps, never `down -v`, the
  confirmation, and end-to-end runs with a fake `docker` on PATH (refusals before any change, the
  release's record before its first change, a restore point carried over);
- scripts/deploy.sh: release tags checked against image ids, the disk check before loading, the
  stack archive kept and free of settings (and its files readable), the release logged only
  after smoke, the operator's name, the release detached on the server.

Review findings (docs/reviews/p1-t18, review B) are named where a test covers one (D1-D10, O1-O10).

The live proof is the drill in the local staging proof (deploy/staging/README.md, Releases).
Run: python3 -m unittest scripts/test_release_scripts.py
"""

import os
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import release_log as rl  # noqa: E402

STAGING_SH = (ROOT / "scripts" / "staging.sh").read_text()
DEPLOY_SH = (ROOT / "scripts" / "deploy.sh").read_text()
SHA = "a919b17b7755e7389f31e5a59c9cb689f6410a5a"


def func(name):
    m = re.search(r"^%s\(\) \{(.*?)^\}" % re.escape(name), STAGING_SH, re.S | re.M)
    assert m, name
    return m.group(1)


def img(n):
    return "sha256:" + format(n, "x").rjust(64, "0")


def fp(n):
    return format(n, "x").rjust(64, "0")


def release(tag, tenant=1, public=2, backend=10, keycloak=20, postgres=30, rp="-", rp_time="-",
            time="2026-11-13T02:00:00+05:30"):
    return {
        "kind": "release", "tag": tag, "time": time,
        "backend": img(backend), "frontend": img(backend + 1), "postgres": img(postgres),
        "keycloak": img(keycloak), "tenant_fp": fp(tenant), "public_fp": fp(public),
        "restore_point": rp, "lsn": "0/3000090" if rp != "-" else "-", "rp_time": rp_time,
    }


def attempt(tag, time="2026-11-13T04:00:00+05:30", **kw):
    return {**release(tag, time=time, **kw), "kind": "attempt"}


def rollback(tag, frm):
    return {"kind": "rollback", "tag": tag, "from": frm, "restored": "no", "time": "2026-11-13T03:00:00+05:30"}


def point(tag, epoch=1763000000):
    return f"release-{tag}-{epoch}"


A, B, C, D = "aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc", "dddddddddddd"


class Lines(unittest.TestCase):
    def test_release_tag(self):
        self.assertEqual(rl.release_tag(SHA, "no", img(5)), SHA[:12])
        dirty = rl.release_tag(SHA, "yes", "sha256:" + "ab12cd34" + "0" * 56)
        self.assertEqual(dirty, SHA[:12] + "-dirty-ab12cd34")  # two dirty builds never share one
        for bad in (("unknown", "no", img(5)), (SHA, "maybe", img(5)), (SHA, "no", "abc")):
            with self.assertRaises(rl.LogError):
                rl.release_tag(*bad)

    def test_round_trip(self):
        entry = release(A, rp=point(A), rp_time="1763000000")
        line = rl.format_line(entry)
        self.assertTrue(line.startswith(f"kind=release tag={A} time="))
        self.assertEqual(rl.parse_words(line.split(" ")), entry)

    def test_only_safe_values(self):
        """A value can never carry a setting or a secret's shape: no '=', '/', spaces, quotes,
        no unknown kind, every image an id and every fingerprint a hash."""
        good = rl.format_line(release(A)).split(" ")
        for word, bad in (
            ("backend=", "indiehr-backend:staging"),
            ("tenant_fp=", "abc"),
            ("tag=", "v1.0"),
            ("restore_point=", "x;rm -rf /"),
            ("restore_point=", "postgres://ihr:secret@db:5432/ihr"),
            ("restore_point=", "main"),
            ("kind=", "deploy"),
            ("via=", "hand"),
            ("migrations=", "maybe"),
            ("rp_from=", "v1.0"),
        ):
            words = [w for w in good if not w.startswith(word)] + [word + bad]
            with self.assertRaises(rl.LogError, msg=word + bad):
                rl.parse_words(words)
        with self.assertRaises(rl.LogError):
            rl.parse_words([w for w in good if not w.startswith("keycloak=")])  # all four images

    def test_restore_point_names_are_unique_per_window(self):
        """D4/O9: release-<tag>-<epoch> (Postgres stops at the first point of a name); the name
        a pre-review line has still reads; staging.sh makes and restores exactly these."""
        for good in (point(A), point(A + "-dirty-ab12cd34"), "release-" + A):
            self.assertEqual(rl.check({**release(A), "restore_point": good})["restore_point"], good)
        self.assertIn('name="release-$1-$(date +%s)"', func("rel_restore_point"))
        m = re.search(r'"\$name" =~ (\S+) \]\]', func("restore_live"))
        for name, ok in ((point(A), True), ("release-" + A, True), ("release-x", False), ("release-" + A + "-1", False)):
            out = subprocess.run(["bash", "-c", f'[[ "$1" =~ {m.group(1)} ]]', "_", name])
            self.assertEqual(out.returncode == 0, ok, name)

    def test_a_restore_point_is_never_taken_twice(self):
        """D4: a name already in the log (or the attempt) is refused before the point is made."""
        entries = [release(A), release(B, rp=point(B)), rollback(A, B)]
        self.assertTrue(rl.point_used(entries, None, point(B)))
        self.assertTrue(rl.point_used([{**rollback(A, B), "restored": point(C)}], None, point(C)))
        self.assertTrue(rl.point_used([], attempt(C, rp=point(C)), point(C)))
        self.assertFalse(rl.point_used(entries, None, point(B, 1763000999)))
        body = func("rel_restore_point")
        self.assertLess(body.index("rlog point-free"), body.index("pg_create_restore_point"))

    def test_a_hand_edited_line_stops_everything(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "releases.log"
            log.write_text(rl.format_line(release(A)) + "\nkind=release tag=oops\n")
            with self.assertRaisesRegex(rl.LogError, "releases.log:2"):
                rl.read_log(log)
            log.write_text(rl.format_line({**release(A), "kind": "attempt"}) + "\n")
            with self.assertRaisesRegex(rl.LogError, "never logged"):
                rl.read_log(log)

    def test_no_setting_reaches_the_log(self):
        """The log's fields are written from image ids and fingerprints only (staging.sh
        rel_fields and release_note): no env_get of anything but the image names."""
        fields = func("rel_fields") + func("release_note")
        self.assertNotIn("SECRETS", fields)
        self.assertEqual(set(re.findall(r"env_get (\w+)", fields)), {"IHR_BACKEND_IMAGE"})
        self.assertEqual(set(re.findall(r"env_get (\w+)", func("rel_images"))),
                         {"IHR_BACKEND_IMAGE", "IHR_FRONTEND_IMAGE", "IHR_POSTGRES_IMAGE", "IHR_KEYCLOAK_IMAGE",
                          "IHR_ACME", "IHR_ACMEDNS_IMAGE", "IHR_MONITORING", "IHR_GRAFANA_IMAGE"})


class Stack(unittest.TestCase):
    def test_release_pushes_and_rollback_pops(self):
        entries = [release(A), release(B), release(C), rollback(B, C)]
        self.assertEqual([e["tag"] for e in rl.stack(entries)], [A, B])
        with self.assertRaises(rl.LogError):
            rl.stack([release(A), rollback(B, A)])


class Plan(unittest.TestCase):
    def test_target_is_the_release_below_the_running_one(self):
        entries = [release(A), release(B), release(C)]
        self.assertEqual(rl.plan(entries, None, C)["target"], B)
        # after rolling back to B, the next rollback goes to A, never forward to C
        self.assertEqual(rl.plan([*entries, rollback(B, C)], None, B)["target"], A)

    def test_an_unfinished_release_goes_back_to_the_last_logged(self):
        """C4: a release whose smoke never passed is never a target; its restore point is used."""
        entries = [release(A), release(B), rollback(A, B)]
        att = attempt(C, tenant=9, rp=point(C), rp_time="1763000000")
        plan = rl.plan(entries, att, C, restore=True)
        self.assertEqual((plan["leave"], plan["target"], plan["restore_point"]), (C, A, point(C)))

    def test_a_release_that_stopped_before_its_start_is_what_is_left(self):
        """D2/O3: the release stopped before `up`, so the last logged release still runs: the
        attempt is still what a rollback leaves, for that running release (never the one
        before it), and a restore uses the attempt's fresh point (never the older release's)."""
        entries = [release(A, rp=point(A)), release(B, tenant=5, rp=point(B), rp_time="1762000000")]
        att = attempt(C, tenant=9, public=9, rp=point(C), rp_time="1763000000")
        att["migrations"] = "started"
        plan = rl.plan(entries, att, B, restore=True)
        self.assertEqual((plan["leave"], plan["target"], plan["restore_point"], plan["rp_time"]),
                         (C, B, point(C), "1763000000"))
        self.assertIn("runs already", plan["leave_note"])
        # without a restore: the platform's migrations may have run half-way, so refused
        with self.assertRaisesRegex(rl.LogError, "platform's schema"):
            rl.plan(entries, att, B)
        # stopped before its restore point (nothing migrated): back without a restore, any schema
        held = {**attempt(C, tenant=9, public=9), "migrations": "none"}
        self.assertEqual(rl.plan(entries, held, B)["target"], B)
        with self.assertRaisesRegex(rl.LogError, "nothing was migrated: roll back without --restore"):
            rl.plan(entries, held, B, restore=True)

    def test_a_stale_attempt_is_refused_not_ignored(self):
        """O3: an attempt older than the log's last line belongs to no release in progress."""
        entries = [release(A), release(B)]
        with self.assertRaisesRegex(rl.LogError, "older than the log's last line"):
            rl.plan(entries, attempt(C, time="2026-11-12T02:00:00+05:30"), B)

    def test_a_build_logged_twice_is_one_release(self):
        """D1: [A, B, B] (B deployed again before the fix): a rollback goes to A, never from B to
        B, and a restore uses the window's point (the oldest B line holding one)."""
        entries = [release(A), release(B, tenant=5, rp=point(B), rp_time="1763000000"), release(B, tenant=5)]
        self.assertEqual(rl.plan([*entries[:1], *entries[1:]], None, B, restore=True)["restore_point"], point(B))
        self.assertEqual(rl.plan(entries, None, B, restore=True)["target"], A)
        same = [release(A), release(B), release(B)]
        self.assertEqual(rl.plan(same, None, B)["target"], A)
        # a log that already holds the no-op `rollback B from B` line: still to A
        self.assertEqual(rl.plan([*same, rollback(B, B)], None, B)["target"], A)
        with self.assertRaisesRegex(rl.LogError, "no logged release before"):
            rl.plan([release(B), release(B)], None, B)
        # a plain deploy of the logged build again is not an unfinished release
        self.assertEqual(rl.plan(same, attempt(B), B)["leave_note"], "the running release")

    def test_a_rollback_that_stopped_is_run_again(self):
        """O5: rolled back to A but stopped before it was logged (A runs): the same rollback."""
        entries = [release(A), release(B)]
        plan = rl.plan(entries, None, A)
        self.assertEqual((plan["leave"], plan["target"], plan["running"]), (B, A, A))

    def test_an_earlier_build_of_the_window_runs(self):
        """D3/O4 (the verifier's reproduction): R logged; release X took the window's restore
        point and stopped; release Y of another build carried it (rp_from=X) and stopped before
        its own start, so X still runs. A restore to the window's point is accepted (whatever
        runs is replaced); without one it is refused with the way that works (X may have
        migrated). Without a point carried and nothing migrated, only if the fingerprints allow."""
        R, X, Y = A, B, C
        y = {**attempt(Y, tenant=9, rp=point(X), rp_time="1763000000"), "rp_from": X,
             "via": "release", "migrations": "started"}
        plan = rl.plan([release(R)], y, X, restore=True)
        self.assertEqual((plan["leave"], plan["target"], plan["running"], plan["restore_point"], plan["rp_of"]),
                         (Y, R, X, point(X), X))
        self.assertIn(f"{X} runs (an earlier build of this window, whose restore point", plan["leave_note"])
        self.assertEqual(plan["target_postgres"], img(30))
        with self.assertRaisesRegex(rl.LogError, "may have migrated the database. Roll back with --restore"):
            rl.plan([release(R)], y, X)
        # any earlier build of the window (a third release over it): the restore still works
        self.assertEqual(rl.plan([release(R)], y, D, restore=True)["restore_point"], point(X))
        # no point to go back to (no backups): neither way, and it says fix forward
        bare = {**attempt(Y, tenant=9), "via": "release", "migrations": "started"}
        with self.assertRaisesRegex(rl.LogError, "no restore point to go back to: fix forward"):
            rl.plan([release(R)], bare, X, restore=True)
        with self.assertRaisesRegex(rl.LogError, "Fix forward"):
            rl.plan([release(R)], bare, X)
        # nothing of the window migrated (migrations=none, no point carried): without a restore
        # when the fingerprints, Keycloak and Postgres allow it, else refused
        none = {**attempt(Y), "via": "release", "migrations": "none"}
        self.assertEqual(rl.plan([release(R)], none, X)["target"], R)
        with self.assertRaisesRegex(rl.LogError, "hospitals' schema"):
            rl.plan([release(R)], {**none, "tenant_fp": fp(9)}, X)
        with self.assertRaisesRegex(rl.LogError, "Keycloak image"):
            rl.plan([release(R)], {**none, "keycloak": img(99)}, X)

    def test_unknown_running_build_is_refused(self):
        with self.assertRaisesRegex(rl.LogError, "is neither"):
            rl.plan([release(A), release(B)], None, D)
        with self.assertRaisesRegex(rl.LogError, "is neither"):
            rl.plan([release(A), release(B)], attempt(C), D)
        with self.assertRaisesRegex(rl.LogError, "empty"):
            rl.plan([], None, A)

    def test_no_restore_only_when_nothing_changed(self):
        """C5/O7: tenant and public fingerprints equal, and the same Keycloak and Postgres."""
        base = release(A)
        self.assertEqual(rl.plan([base, release(B, backend=40)], None, B)["target_backend"], img(10))
        for changed, reason in (
            ({"tenant": 7}, "hospitals' schema"),
            ({"public": 7}, "platform's schema"),
            ({"keycloak": 77}, "Keycloak image"),
            ({"postgres": 77}, "Postgres image"),
        ):
            with self.assertRaisesRegex(rl.LogError, reason):
                rl.plan([base, release(B, backend=40, **changed)], None, B)

    def test_restore_needs_the_left_release_restore_point(self):
        entries = [release(A), release(B, tenant=7, rp=point(B), rp_time="1763000000")]
        plan = rl.plan(entries, None, B, restore=True)
        self.assertEqual((plan["target"], plan["restore_point"], plan["rp_time"]), (A, point(B), "1763000000"))
        with self.assertRaisesRegex(rl.LogError, "took no restore point"):
            rl.plan([release(A), release(B, tenant=7)], None, B, restore=True)


class Keep(unittest.TestCase):
    def test_last_three_plus_running_and_attempt(self):
        entries = [release(t) for t in (A, B, C, D)]
        self.assertEqual(rl.keep(entries, None, 3), [D, C, B])
        self.assertEqual(rl.keep(entries, attempt("eeeeeeeeeeee"), 3, A), [D, C, B, "eeeeeeeeeeee", A])

    def test_a_rollback_keeps_the_release_it_left(self):
        entries = [release(A), release(B), rollback(A, B)]
        self.assertEqual(rl.keep(entries, None, 3), [A, B])


class NeedsRelease(unittest.TestCase):
    def test_decision(self):
        logged = [release(A, tenant=1, public=2)]
        self.assertIsNone(rl.needs_release(logged, fp(1), fp(2), True))
        self.assertIn("hospitals' schema", rl.needs_release(logged, fp(3), fp(2), True))
        self.assertIn("platform's schema", rl.needs_release(logged, fp(1), fp(3), True))
        # the first P1-T18 release on a server with a database is a full stop; a new server is not
        self.assertIn("first", rl.needs_release([], fp(1), fp(2), True))
        self.assertIsNone(rl.needs_release([], fp(1), fp(2), False))

    def test_an_unfinished_release_needs_the_window(self):
        """D3/O4: a plain start never overwrites an unfinished release's record (its restore
        point); a failed plain deploy, or the logged build deployed again, does not block one."""
        logged = [release(A, tenant=1, public=2)]
        for open_one in ({**attempt(B), "via": "release", "restore_point": "-"}, attempt(B, rp=point(B))):
            self.assertIn("unfinished", rl.needs_release(logged, fp(1), fp(2), True, open_one))
        self.assertIsNone(rl.needs_release(logged, fp(1), fp(2), True, {**attempt(B), "via": "plain"}))
        self.assertIsNone(rl.needs_release(logged, fp(1), fp(2), True, {**attempt(A), "via": "plain"}))
        with tempfile.TemporaryDirectory() as d:
            log, att = Path(d) / "releases.log", Path(d) / "attempt"
            log.write_text(rl.format_line(logged[0]) + "\n")
            att.write_text(rl.format_line(attempt(B, rp=point(B))) + "\n")
            out = subprocess.run([sys.executable, str(ROOT / "scripts" / "release_log.py"), "needs-release",
                                  str(log), str(att), fp(1), fp(2), "1"], capture_output=True, text=True)
            self.assertEqual(out.returncode, 3, out.stderr)
        self.assertIn('needs-release "$CM_DIR/releases.log" "$CM_DIR/attempt"', DEPLOY_SH)


class ReleaseScript(unittest.TestCase):
    def test_window_steps_in_order(self):
        """Item 11: the release's record first (D2), announce, the full audit check while the
        outgoing build still serves (O7), hold all (outgoing build), stop workers, drain, seal
        (outgoing build), restore point, shared then fleet migrations (logged container), start,
        smoke."""
        body = func("release")
        steps = [
            "trap 'rel_release_exit $?' EXIT",
            'release_note --release via=release "migrations=$migr"',
            "old_manage ihr_fleet_run window",
            'audit="$(rel_audit_check)"',
            'if ! old_manage ihr_fleet_run hold --operator "$op"; then',
            "    REL_STAGE=1\n    hold_at=",
            'compose stop "${REL_WORKERS[@]}"',
            'rel_workers_marker "$quiet_until"',
            'rel_drain "$hold_at"',
            'seal="$(rel_seal "$old_id")"',
            'rel_restore_point "$tag"',
            "REL_STAGE=2\n",
            'release_note --release via=release migrations=started "${carry[@]}"',
            "migrate_schemas --shared",
            "run --rm --no-deps -T migrate python manage.py ihr_fleet_run migrate check roles packs",
            "\n  up\n",
            "rel_workers_marker clear",
            "smoke || smoke_rc=$?",
            'rel_unserved "${fps[0]}"',
            'release_finish "$op" --checked',
        ]
        at = [body.index(st) for st in steps]
        self.assertEqual(at, sorted(at), steps)
        self.assertIn("REL_WORKERS=(celery-worker celery-beat provisioner staff-worker)", STAGING_SH)
        # the window and the holds stay while a hospital is not served: non-zero, never logged
        unfinished = body[body.index("if [[ -n \"$unserved\""):body.index('release_finish "$op" --checked')]
        self.assertIn("exit 1", unfinished)
        self.assertIn("pg_create_restore_point('$name')", func("rel_restore_point"))
        self.assertIn("pg_switch_wal()", func("rel_restore_point"))

    def test_the_trap_says_what_changed(self):
        """D2/D10: rollback advice only once something changed; before the hold nothing did (the
        record goes back as it was); held but not migrated: the database is unchanged."""
        trap = func("rel_release_exit")
        stage0 = trap[trap.index("0) echo"):trap.index("1) echo")]
        self.assertIn("nothing was held, stopped or migrated", stage0)
        self.assertNotIn("rollback", stage0)
        self.assertIn("rel_workers_marker clear", trap)
        self.assertIn('mv -f "$REL_PREV" "$REL_ATTEMPT"', trap)
        stage1 = trap[trap.index("1) echo"):trap.index("*) echo")]
        self.assertIn("database is unchanged", stage1)
        # D3/O4: the rollback advice of stages 1 and 2 is what `rollback` accepts, found as it
        # finds it (plan with the running build), never a fixed "rollback [--restore]"
        self.assertIn("$(rel_rollback_advice)", stage1)
        self.assertIn("$(rel_rollback_advice)", trap[trap.index("*) echo"):])
        self.assertNotIn("rollback [--restore]", trap)
        advice = func("rel_rollback_advice")
        self.assertIn('release_tag_of "$(running_backend_id)"', advice)
        self.assertIn('running="$(release_tag_of "$(running_backend_id)")"', func("rollback"))
        self.assertIn('out="$(rlog plan "$REL_LOG" "$REL_ATTEMPT" "$running" 2>/dev/null)"', advice)
        self.assertIn('rlog plan "$REL_LOG" "$REL_ATTEMPT" "$running" --restore', advice)
        self.assertIn("no --restore", advice)
        # the "no window" advice runs as the running backend's build (the old build): the fleet
        # dispatch, through old_manage (in it, or after the move of the roles a one-off of its
        # image in the owner's migrate container, P1-T19b)
        dispatch = STAGING_SH[STAGING_SH.index("  fleet)\n"):STAGING_SH.index("  release) release")]
        self.assertIn('"${2:-}" == window', dispatch)
        self.assertIn('old_manage ihr_fleet_run "${@:2}"', dispatch)
        old = func("old_manage")
        self.assertIn('docker tag "$(running_backend_id)" "$repo"', old)
        self.assertIn("compose exec -T backend python manage.py", old)

    def test_seal_only_inside_the_window(self):
        """O7: inside the window only a seal and one fingerprint (capped), by the outgoing build
        with the worker's add-only key; the full check (every chain and fingerprint) runs before
        the hold, in the running backend, capped too."""
        seal = func("rel_seal")
        self.assertIn('IHR_BACKEND_IMAGE="$repo" compose run --rm --no-deps -T', seal)
        self.assertIn("celery-worker", seal)  # the add-only key lives there (compose.yaml)
        self.assertIn('timeout "$REL_SEAL_SECONDS" python manage.py shell -c "$REL_SEAL_PY"', seal)
        self.assertNotIn("ihr_audit_verify", seal)
        self.assertIn("anchor-write.env", seal)
        self.assertIn("echo skipped", seal)
        py = re.search(r"^REL_SEAL_PY='(.*?)'$", STAGING_SH, re.S | re.M).group(1)
        self.assertIn("seal_once(h.schema_name)", py)
        self.assertIn("anchor.write()", py)
        self.assertNotIn("verify", py)
        self.assertNotIn("load_all", py)
        compile(py, "REL_SEAL_PY", "exec")
        check = func("rel_audit_check")
        self.assertIn('compose exec -T backend timeout "$REL_AUDIT_SECONDS" python manage.py ihr_audit_verify --all', check)
        self.assertNotIn("--seal", check)

    def test_workers_stopped_by_the_window_are_not_paged(self):
        """O6: the marker the release writes is the one ContainerNotRunning reads, for every
        worker the window stops; cleared when the release ends (a stopped release pages)."""
        rules = (ROOT / "deploy" / "monitoring" / "prometheus" / "rules" / "availability.yml").read_text()
        marker = func("rel_workers_marker")
        self.assertIn("ihr_release_workers_stopped_until_seconds $1", marker)
        self.assertIn('chmod 644 "$f.tmp"', marker)  # node-exporter reads it
        self.assertEqual(rules.count("unless on() (ihr_release_workers_stopped_until_seconds > time())"), 2)
        for worker in ("celery-worker|celery-beat", "provisioner|staff-worker"):
            self.assertIn(f'ihr_container_running{{service=~"{worker}"}} == 0', rules)
        tests = (ROOT / "deploy" / "monitoring" / "prometheus" / "tests" / "availability.yml").read_text()
        self.assertIn("workers stopped by a release window", tests)

    def test_logged_only_after_smoke_and_every_hospital_served(self):
        record = func("release_record")
        self.assertLess(record.index("rel_unserved"), record.index('>> "$REL_LOG"'))
        self.assertIn("the running backend is not release", record)
        self.assertLess(record.index('>> "$REL_LOG"'), record.index("release_prune"))
        prune = func("release_prune")
        self.assertIn('"$RELEASES_KEPT"', prune)
        self.assertIn("RELEASES_KEPT=3", STAGING_SH)
        # this machine's own images are never pruned by a proof
        self.assertIn('[[ -f "$DIR/.local" ]] || docker image prune -f', prune)

    def test_smoke_checks_every_active_hospital(self):
        smoke = func("smoke")
        self.assertIn("status = 'active'", smoke)
        self.assertIn('check "hospital $slug served (not held: 503 upgrading)" 200', smoke)


class RollbackScript(unittest.TestCase):
    def test_never_down_v_and_confirmed(self):
        body = func("rollback")
        self.assertNotIn("down -v", body.replace("never down -v", ""))
        self.assertNotIn("--volumes", body)
        self.assertIn('[[ "$answer" == "$target" ]] || die "not confirmed: nothing changed"', body)
        self.assertLess(body.index("read -r answer"), body.index("compose down"))
        # the copy before the restore, the restore to the named point from a backup before it
        self.assertLess(body.index("pgdata-pre-rollback"), body.index("restore_live --target-name"))
        self.assertIn('rel_backup_set "$rp_time"', body)
        # D2/O3/D7: every image moves back, Postgres and Keycloak too (deploy.sh moved their tags
        # to the build being left); with a restore and without
        self.assertIn('names="backend frontend postgres keycloak acmedns grafana"', body)
        self.assertEqual(body.count('names="'), 1)
        self.assertIn('rel_point_images "$names" "$plan"', body)
        self.assertIn('[[ -n "$target" && "$leave" != "$target" ]] || die', body)  # D1

    def test_target_images_checked_before_any_change(self):
        """D5: every target image is checked before the question and `down`; the tags are never
        half re-pointed; the target's Postgres image restores the database."""
        body = func("rollback")
        self.assertLess(body.index('rel_check_images "$names" "$plan"'), body.index("read -r answer"))
        self.assertLess(body.index('rel_check_images "$names" "$plan"'), body.index('rel_point_images "$names"'))
        self.assertLess(body.index('rel_point_images "postgres" "$plan"'), body.index("restore_live --target-name"))
        self.assertLess(body.index("restore_live --target-name"), body.index('rel_point_images "backend frontend keycloak'))
        point = func("rel_point_images")
        self.assertLess(point.index("rel_check_images"), point.index("docker tag"))

    def test_no_restore_rollback_serves_the_held_hospitals(self):
        """D7/O5: after `up`, the window's holds go, roles are synced again, the window is
        cleared, then smoke; the log line only after smoke (a stopped rollback is run again)."""
        body = func("rollback")
        plain = body[:body.index("    return 0\n")]
        steps = ['rel_point_images "$names"', 'scripts/staging.sh" up', 'fleet release --operator "$op"',
                 'fleet roles --operator "$op" || true', 'fleet window --clear --operator "$op"',
                 'scripts/staging.sh" smoke', "kind=rollback", 'rm -f "$REL_ATTEMPT"']
        at = [plain.index(st) for st in steps]
        self.assertEqual(at, sorted(at), steps)

    def test_the_kept_copy_is_new_complete_and_sized(self):
        """D6/O10: a new volume per rollback (never merged into an old one), sized from the
        volume itself (pg_wal too) with the 20 GB floor, removed if the copy fails, marked when
        complete; the only database is never replaced before the WAL to the point is shown to
        be in the repository."""
        body = func("rollback")
        self.assertIn('aside="${project}_pgdata-pre-rollback-$leave-$(date +%Y%m%d%H%M%S)"', body)
        self.assertIn('! docker volume inspect "$aside"', body)
        self.assertIn('need="$(rel_db_used_mb)"', body)
        self.assertIn("du \"$(env_get IHR_POSTGRES_IMAGE)\" -sm /v", func("rel_db_used_mb"))
        self.assertIn("floor=$(( ${IHR_DEPLOY_MIN_FREE_GB:-20} * 1024 ))", body)
        self.assertIn("((free > need + margin))", body)
        self.assertIn("touch /to/.ihr-rollback-copy-complete", body)
        self.assertIn('docker volume rm "$aside"', body)
        self.assertLess(body.index('rel_wal_archived "$lsn"'), body.index("read -r answer"))
        self.assertIn("((keep_copy)) || die", body[body.index('rel_wal_archived "$lsn"'):])

    def test_the_wal_to_the_point_is_shown_archived(self):
        """O10: the repository's newest archived WAL file reaches the point's (any timeline)."""
        base = f"set -euo pipefail\nrel_wal_archived() {{{func('rel_wal_archived')}}}\n" \
               "rel_sql() { echo 000000020000000000000005; }\n"
        for top, lsn, ok in (("000000020000000000000007", "0/5000090", True),
                             ("000000030000000000000005", "0/5000090", True),
                             ("000000020000000000000004", "0/5000090", False),
                             ("000000020000000000000009", "-", False)):
            info = '[{"archive":[{"id":"18-1","max":"%s"}]}]' % top
            out = subprocess.run(["bash", "-c", base + f"rel_repo_info() {{ echo '{info}'; }}\nrel_wal_archived '{lsn}'"],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode == 0, ok, (top, lsn, out.stderr))

    def test_restore_ends_with_a_full_backup(self):
        """D4/O9: a later restore never reaches back past this one."""
        body = func("rollback")
        self.assertLess(body.index("restore_live --target-name"), body.index('scripts/staging.sh" backup-now full'))

    def test_restore_takes_a_named_point(self):
        restore = func("restore_live")
        self.assertIn("--type=name", restore)
        self.assertIn("--replace-database", restore)
        self.assertNotIn("down -v first", restore)
        self.assertIn('rel_rollback_exit "$rc"', restore)  # the rollback's trap still speaks

    def test_stack_files_never_carry_settings(self):
        unpack = func("rel_unpack_stack")
        self.assertIn("--remove-destination", unpack)  # new inodes: the running script reads on
        self.assertNotIn(".env", unpack.replace("never .env", ""))

    def test_stack_files_readable_whatever_the_umask(self):
        """O2: unpacked under umask 077 (the caller's), the files stay 644 and 755."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "rel").mkdir()
            (d / "root" / "deploy" / "monitoring").mkdir(parents=True)  # an existing tree
            make_stack(d / "rel" / f"stack-{A}.tgz")
            script = f"set -euo pipefail\nROOT={d}/root\nREL_DIR={d}/rel\numask 077\n" \
                     f"rel_unpack_stack() {{{func('rel_unpack_stack')}}}\nrel_unpack_stack {A}\n"
            out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            check_modes(self, d / "root")


def make_stack_archive(path, compose_text, extra=None):
    """A release's kept stack archive (deploy.sh's stack-<tag>.tgz) with this compose.yaml (and
    the extra files {path: text})."""
    import io
    import tarfile
    with tarfile.open(path, "w:gz") as tar:
        for name, text in {"deploy/staging/compose.yaml": compose_text, **(extra or {})}.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def make_stack(path):
    """A stack archive as deploy.sh makes it: settings files 644, scripts 755."""
    import io
    import tarfile
    with tarfile.open(path, "w:gz") as tar:
        for name, mode in (("deploy/monitoring/prometheus/prometheus.yml", 0o644),
                           ("deploy/logs/fluent-bit/fluent-bit.yaml", 0o644),
                           ("scripts/staging.sh", 0o755)):
            info = tarfile.TarInfo(name)
            info.mode, info.size = mode, 2
            tar.addfile(info, io.BytesIO(b"x\n"))


def check_modes(test, root):
    for name, mode in (("deploy/monitoring/prometheus/prometheus.yml", 0o644),
                       ("deploy/logs/fluent-bit/fluent-bit.yaml", 0o644),
                       ("scripts/staging.sh", 0o755),
                       ("deploy/logs/fluent-bit", 0o755),
                       ("deploy/monitoring/prometheus", 0o755)):
        test.assertEqual(stat.S_IMODE((root / name).stat().st_mode), mode, name)


class DeployScript(unittest.TestCase):
    def test_tags_checked_against_ids(self):
        self.assertIn("docker tag '$id' '$repo:$TAG' && test", DEPLOY_SH)
        self.assertIn('tag_release "$img"', DEPLOY_SH)

    def test_disk_checked_before_loading(self):
        self.assertLess(DEPLOY_SH.index("IHR_DEPLOY_MIN_FREE_GB"), DEPLOY_SH.index('ship_image "$BACKEND"'))

    def test_schema_change_needs_the_window(self):
        """O2: decided before anything is sent; a plain start only without migrations."""
        self.assertLess(DEPLOY_SH.index("needs-release"), DEPLOY_SH.index('ship_image "$BACKEND"'))
        self.assertIn("deploy with --release", DEPLOY_SH)
        self.assertIn("scripts/staging.sh release --operator", DEPLOY_SH)

    def test_stack_archive_kept_and_clean(self):
        self.assertIn("stack-$TAG.tgz", DEPLOY_SH)
        self.assertIn("(umask 077 && cat > $REL_REMOTE/stack-$TAG.tgz.tmp)", DEPLOY_SH)
        self.assertIn(r"(^|/)(\.env|secrets/|generated/|\.local$|\.db-created$|releases/)", DEPLOY_SH)
        self.assertIn("scripts/release_log.py", DEPLOY_SH)

    def test_stack_files_extracted_readable(self):
        """O2: the server-side command, run under umask 077: the kept archive is 600, the stack
        files 644/755 (Prometheus and Fluent Bit, user 65534, read them)."""
        m = re.search(r'^remote "(mkdir -p -m 700 \$REL_REMOTE .*?)" < "\$CM_DIR/stack\.tgz"$', DEPLOY_SH, re.S | re.M)
        self.assertTrue(m)
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "srv" / "deploy" / "monitoring").mkdir(parents=True)
            make_stack(d / "stack.tgz")
            cmd = m.group(1).replace("\\\n", " ")
            env = {**os.environ, "REMOTE_ROOT": str(d / "srv"), "REL_REMOTE": str(d / "srv" / "releases"), "TAG": A}
            with open(d / "stack.tgz", "rb") as tgz:
                out = subprocess.run(["bash", "-c", 'umask 077; eval "$1"', "_", cmd], stdin=tgz, env=env,
                                     capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            check_modes(self, d / "srv")
            kept = d / "srv" / "releases" / f"stack-{A}.tgz"
            self.assertEqual(stat.S_IMODE(kept.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(kept.parent.stat().st_mode), 0o700)

    def test_operator_name(self):
        """O1: the name deploy.sh sends is never empty (the hyphen last in tr's set)."""
        m = re.search(r'^  op="(\$\(id -un \| tr .*?\))"$', DEPLOY_SH, re.M)
        self.assertTrue(m)
        for user, want in (("ihr", "ihr"), ("dhruv.g-1@x", "dhruv.g-1@x"), ("we!rd user", "we-rd-user")):
            out = subprocess.run(["bash", "-c", f"set -euo pipefail\nid() {{ echo '{user}'; }}\nop=\"{m.group(1)}\"\necho \"$op\""],
                                 capture_output=True, text=True)
            self.assertEqual((out.returncode, out.stdout.strip()), (0, want), out.stderr)
        self.assertIn('[[ "$op" =~ ^[A-Za-z0-9._@-]{1,64}$ ]] || die', DEPLOY_SH)

    def test_release_detached_on_the_server(self):
        """O1: a dropped SSH connection never stops a release half-way: setsid nohup, output
        and exit status kept, followed until it ends; the release itself not under umask 077."""
        part = DEPLOY_SH[DEPLOY_SH.index("elif ((RELEASE)); then"):]
        part = part[:part.index("\nelse\n")]
        self.assertIn("setsid nohup sh -c 'scripts/staging.sh release --operator $op", part)
        self.assertIn("< /dev/null &", part)
        self.assertIn("exit \\$(cat $rel_out.rc)", part)
        self.assertNotIn("&& umask 077 &&", part)

    def test_logged_after_smoke_and_no_blind_prune(self):
        self.assertIn("scripts/staging.sh smoke && scripts/staging.sh release-record", DEPLOY_SH)
        self.assertNotIn('remote "docker image prune', DEPLOY_SH)
        self.assertIn("--build-arg \"APP_VERSION=", DEPLOY_SH)

    def test_releases_are_git_ignored(self):
        self.assertIn("deploy/staging/releases/", (ROOT / ".gitignore").read_text())


# A stand-in for docker (python3): the images in FAKE_IMAGES exist (a name:tag always is the
# current build, FAKE_STAGING_ID), the services in FAKE_RUNNING run, those in FAKE_STOPPED have a
# stopped container (`docker start` runs it: FAKE_STARTED), those in FAKE_ONEOFF a stopped
# one-off of compose run; FAKE_PS_FAILS makes compose ps fail; anything else unexpected.
# `compose start` resolves dependencies as Compose v5.7.1 does (proven on a throwaway project):
# every app service depends on the one-shot migrate (compose.yaml's x-app), which never has a
# container on this stack, so it fails with "missing dependency migrate" and starts nothing
FAKE_DOCKER = textwrap.dedent("""\
    #!/usr/bin/env python3
    import os, sys
    a = sys.argv[1:]
    env = os.environ.get
    with open(env("FAKE_LOG"), "a") as f:
        f.write(" ".join(a) + "\\n")
    started_file = env("FAKE_STARTED")
    started = open(started_file).read().split() if started_file and os.path.exists(started_file) else []
    running = env("FAKE_RUNNING", "").split() + started
    stopped = [x for x in env("FAKE_STOPPED", "").split() if x not in started]
    oneoff = env("FAKE_ONEOFF", "").split()
    images = env("FAKE_IMAGES", "").split()
    staging = env("FAKE_STAGING_ID")
    APP = ("backend", "celery-worker", "celery-beat", "provisioner", "staff-worker")
    DEPS = {**{x: ["migrate"] for x in APP}, "frontend": ["backend"], "migrate": ["db", "redis"]}
    def ref_id(ref):
        if ref.startswith("sha256:"):
            return ref if ref in images or ref == staging else None
        return staging
    if a[:1] == ["compose"]:
        if "ps" in a:
            if env("FAKE_PS_FAILS"):
                sys.stderr.write("cannot connect to the Docker daemon\\n")
                sys.exit(1)
            svcs = a[a.index("ps") + 1:]
            fmt = "--format" in svcs
            every = "-a" in svcs
            svcs = [x for x in svcs if not x.startswith("-") and "{" not in x]
            for x in svcs:
                if x in running or (every and x in stopped):
                    print(x if fmt else "cid-" + x)
                if every and x in oneoff:
                    print(x if fmt else "cid-run-" + x)
            sys.exit(0)
        if "start" in a and "exec" not in a:
            svcs = a[a.index("start") + 1:]
            have = set(running) | set(stopped)
            todo, seen = list(svcs), set()
            while todo:
                x = todo.pop()
                if x in seen:
                    continue
                seen.add(x)
                for dep in DEPS.get(x, []):
                    if dep not in have:
                        sys.stderr.write("missing dependency " + dep + "\\n")
                        sys.exit(1)
                    todo.append(dep)
            sys.exit(0)  # never reached for an app service on this stack
        if "exec" in a and "backend" in a and "backend" not in running:
            sys.stderr.write("service backend is not running\\n")
            sys.exit(1)
        if "exec" in a and "ihr_fleet_run" in a and "window" in a:
            snap = env("FAKE_ATTEMPT")
            if snap and os.path.exists(snap):
                with open(env("FAKE_LOG"), "a") as f:
                    f.write("ATTEMPT " + open(snap).read())
            print(env("FAKE_WINDOW", "no window announced"))
            sys.exit(0)
        if "exec" in a and "ihr_fleet_run" in a and env("FAKE_EXEC_OK"):
            sys.exit(0)  # the running build's hold or release succeeds
        if "exec" in a and "psql" in a and "ihr_owner" in " ".join(a) and env("FAKE_ROLES"):
            print(env("FAKE_ROLES"))  # db_roles_state: split, single or none (P1-T19b)
            sys.exit(0)
        if "exec" in a and "psql" in a:
            if env("FAKE_SQL"):
                print(env("FAKE_SQL"))
            sys.exit(0)
    elif a[:1] == ["tag"]:
        sys.exit(0)
    elif a[:1] == ["inspect"] and "com.docker.compose.oneoff" in " ".join(a):
        for c in a[3:]:
            print(c, "True" if c.startswith("cid-run-") else "False")
        sys.exit(0)
    elif a[:1] == ["start"]:
        cids = a[1:]
        if not cids or any(c.removeprefix("cid-") not in stopped + running for c in cids):
            sys.exit(1)
        with open(started_file, "a") as f:
            f.write(" ".join(c.removeprefix("cid-") for c in cids) + "\\n")
        print("\\n".join(cids))
        sys.exit(0)
    elif a[:1] == ["inspect"] and a[-1].startswith("cid-"):
        print(env("FAKE_RUNNING_IMAGE"))
        sys.exit(0)
    elif a[:2] == ["image", "inspect"]:
        i = ref_id(a[-1])
        if i is None:
            sys.exit(1)
        fmt = a[a.index("-f") + 1] if "-f" in a else ""
        if ".Id" in fmt:
            print(i)
        elif "revision" in fmt:
            print(env("FAKE_RUNNING_SHA") if env("FAKE_RUNNING_SHA") and i == env("FAKE_RUNNING_IMAGE") else env("FAKE_SHA"))
        elif "git-dirty" in fmt:
            print("no")
        sys.exit(0)
    elif a[:1] == ["run"] and "code_fingerprint" in " ".join(a):
        print(env("FAKE_FPS"))
        sys.exit(0)
    sys.stderr.write("unexpected docker: " + " ".join(a) + "\\n")
    sys.exit(9)
    """)
CURRENT = img(2)  # the build this stack's tags name (FAKE_STAGING_ID)
OLD = img(5)      # the build the running backend was started from
RUNNING = SHA[:12]  # every image's commit label is SHA: its release tag


class EndToEnd(unittest.TestCase):
    """staging.sh with a fake docker: refusals with the reason before anything changes (no tag,
    no compose up/down/run/stop), and the release's own record."""

    def run_staging(self, entries, *args, attempt_line=None, running="", fake=None, split=True, stacks=None,
                    app_extra=""):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "releases").mkdir()
            (d / "releases" / "releases.log").write_text("".join(rl.format_line(e) + "\n" for e in entries))
            if attempt_line is not None:
                (d / "releases" / "attempt").write_text(rl.format_line(attempt_line) + "\n")
            (d / ".env").write_text("IHR_BACKEND_IMAGE=indiehr-backend:staging\nIHR_FRONTEND_IMAGE=indiehr-frontend:staging\n"
                                    "IHR_POSTGRES_IMAGE=indiehr-postgres:staging\nIHR_KEYCLOAK_IMAGE=indiehr-keycloak:staging\n")
            (d / "secrets").mkdir()
            if split:  # the split database roles' addresses (P1-T19b; made by db-split-secrets)
                (d / "secrets" / "app.env").write_text(f"DATABASE_URL=postgres://ihr_app:{'a' * 43}@db:5432/ihr\n"
                                                       + app_extra)
                (d / "secrets" / "db-owner.env").write_text(f"DATABASE_URL=postgres://ihr_owner:{'o' * 43}@db:5432/ihr\n")
            for tag, stack in (stacks or {}).items():  # releases' kept stack files
                compose_text, extra = stack if isinstance(stack, tuple) else (stack, None)
                make_stack_archive(d / "releases" / f"stack-{tag}.tgz", compose_text, extra)
            bindir = d / "bin"
            bindir.mkdir()
            dk = bindir / "docker"
            dk.write_text(FAKE_DOCKER)
            dk.chmod(dk.stat().st_mode | stat.S_IXUSR)
            env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "IHR_STAGING_DIR": str(d),
                   "FAKE_LOG": str(d / "calls"), "FAKE_SHA": SHA, "FAKE_STAGING_ID": CURRENT,
                   "FAKE_RUNNING": running, "FAKE_RUNNING_IMAGE": OLD, "FAKE_FPS": f"{fp(1)} {fp(2)}",
                   "FAKE_ATTEMPT": str(d / "releases" / "attempt"), "FAKE_STARTED": str(d / "started"),
                   **(fake or {})}
            out = subprocess.run(["bash", str(ROOT / "scripts" / "staging.sh"), *args], env=env,
                                 capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
            calls = (d / "calls").read_text() if (d / "calls").exists() else ""
            att = d / "releases" / "attempt"
            self.after = {"attempt": att.read_text() if att.exists() else None,
                          "log": (d / "releases" / "releases.log").read_text()}
            return out, calls

    def assertNothingChanged(self, calls):
        self.assertNotRegex(calls, r"(^|\n)tag ")
        self.assertNotRegex(calls, r"compose .* (up|down|run|stop) ")

    def test_refuses_across_a_schema_change(self):
        out, calls = self.run_staging([release(A), release(RUNNING, tenant=7)], "rollback")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("a rollback without a restore is refused", out.stderr)
        self.assertIn("hospitals' schema", out.stderr)
        self.assertNothingChanged(calls)

    def test_restore_needs_the_typed_tag(self):
        out, calls = self.run_staging([release(A), release(RUNNING, tenant=7, rp=point(RUNNING),
                                                           rp_time="1763000000")], "rollback", "--restore")
        # no backups set up here: refused before the question
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("no backups set up here", out.stderr)
        self.assertNothingChanged(calls)

    def test_a_missing_target_image_changes_nothing(self):
        """D5: the target's backend image was pruned: refused before any tag moves."""
        out, calls = self.run_staging([release(A), release(RUNNING, backend=40)], "rollback",
                                      fake={"FAKE_IMAGES": img(11)})
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("images of the target are no longer here: backend=" + img(10), out.stderr)
        self.assertIn("ROLLBACK STOPPED (exit 1): nothing was changed", out.stderr)
        self.assertNothingChanged(calls)

    def test_a_window_hold_without_a_release_is_not_guessed(self):
        """D2: hospitals held for a window and no release in progress: refused, nothing changed."""
        out, calls = self.run_staging([release(A), release(RUNNING)], "rollback", running="db",
                                      fake={"FAKE_SQL": "3"})
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("3 hospital(s) are held for a window, but no release is in progress", out.stderr)
        self.assertNothingChanged(calls)

    def test_a_no_restore_rollback_points_every_image_at_the_target(self):
        """D2/O3/D7: a release that stopped before its restore point (migrations=none) after
        deploy.sh moved the :staging tags to its build (another Postgres and Keycloak): the
        rollback without a restore points Postgres and Keycloak back at the target's images
        too, before `up`, so the database and Keycloak are never recreated on the new ones."""
        stopped = {**attempt(B, backend=50, postgres=77, keycloak=78, tenant=9), "via": "release",
                   "migrations": "none"}
        out, calls = self.run_staging([release(A), release(RUNNING)], "rollback", attempt_line=stopped,
                                      fake={"FAKE_IMAGES": " ".join(img(n) for n in (10, 11, 20, 30))})
        self.assertIn(f"Rolling back from {B}", out.stdout)
        for image, ref in ((img(10), "indiehr-backend"), (img(11), "indiehr-frontend"),
                           (img(30), "indiehr-postgres"), (img(20), "indiehr-keycloak")):
            self.assertIn(f"\ntag {image} {ref}:staging\n", calls)
        tags = [i for i, ln in enumerate(calls.splitlines()) if ln.startswith("tag ")]
        ups = [i for i, ln in enumerate(calls.splitlines()) if re.match(r"compose .* up ", ln)]
        self.assertTrue(ups and max(tags) < min(ups), calls)
        # (the fake docker stops `up`: the trap says the images moved and the database did not)
        self.assertIn(f"this stack's images point at {RUNNING}; the database was not touched", out.stderr)

    def test_a_failed_hold_claims_no_hold(self):
        """D2/O3: the stage moves to "held" only once the hold succeeded. A hold that failed and
        held nobody (one transaction) is stage 0: the record goes back, no rollback advice; one
        the database shows some hospitals held for names only what it shows."""
        from datetime import datetime, timedelta, timezone
        ist = timezone(timedelta(hours=5, minutes=30))
        start = (datetime.now(ist) - timedelta(minutes=5)).strftime("%d-%m-%Y %H:%M")
        window = {"FAKE_WINDOW": f"window: {start} IST for 120 min", "FAKE_IMAGES": OLD}
        out, calls = self.run_staging([release(A)], "release", "--operator", "tester", running="backend",
                                      fake={**window, "FAKE_SQL": "0"})
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("compose", calls)
        self.assertRegex(calls, r"exec -T backend python manage.py ihr_fleet_run hold --operator tester")
        self.assertIn("the hold failed (above): no hospital is held", out.stderr)
        self.assertIn("RELEASE DID NOT START (exit 1): nothing was held", out.stderr)
        self.assertNotIn("every hospital is held", out.stderr)
        self.assertIsNone(self.after["attempt"])
        self.assertNotRegex(calls, r"compose .* (up|down|run|stop) ")
        out, calls = self.run_staging([release(A)], "release", "--operator", "tester", running="backend",
                                      fake={**window, "FAKE_SQL": "2"})
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("RELEASE STOPPED BEFORE ANY MIGRATION (exit 1): the hold failed, and 2 hospital(s) are held",
                      out.stderr)
        self.assertIn("the workers were not stopped", out.stderr)
        self.assertNotIn("every hospital is held", out.stderr)
        self.assertNotRegex(calls, r"compose .* stop ")

    def test_the_advice_after_a_release_over_an_open_window_is_what_plan_accepts(self):
        """D3/O4 end to end (the verifier's reproduction): release A logged; B's release took the
        window's restore point and stopped, so B runs; this build's release carries B's point and
        stops while B still runs. The trap advises only the rollback plan() accepts (--restore),
        and a rollback without one is refused with that way, nothing changed."""
        b_sha = "bbbbbbbbbbbb" + SHA[12:]
        open_b = {**attempt(B, tenant=9, rp=point(B), rp_time="1763000000"), "via": "release", "migrations": "started"}
        from datetime import datetime, timedelta, timezone
        ist = timezone(timedelta(hours=5, minutes=30))
        start = (datetime.now(ist) - timedelta(minutes=5)).strftime("%d-%m-%Y %H:%M")
        fake = {"FAKE_WINDOW": f"window: {start} IST for 120 min", "FAKE_IMAGES": OLD,
                "FAKE_RUNNING_SHA": b_sha, "FAKE_SQL": "4"}
        out, calls = self.run_staging([release(A)], "release", "--operator", "tester", attempt_line=open_b,
                                      running="backend", fake=fake)
        self.assertEqual(out.returncode, 1, out.stderr)
        written = rl.parse_words(self.after["attempt"].split())
        self.assertEqual((written["tag"], written["rp_from"], written["restore_point"]), (RUNNING, B, point(B)))
        self.assertIn(f"scripts/staging.sh rollback --restore (back to the window's restore point {point(B)}",
                      out.stderr)
        self.assertNotIn("no --restore", out.stderr)
        self.assertNotIn("rollback [--restore]", out.stderr)
        # what that advice rests on: the same plan, as `rollback` runs it
        out, calls = self.run_staging([release(A)], "rollback", attempt_line=written, running="backend",
                                      fake={"FAKE_IMAGES": OLD, "FAKE_RUNNING_SHA": b_sha})
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("Roll back with --restore", out.stderr)
        self.assertNothingChanged(calls)

    # --- the split database roles (P1-T19b review: C1, C2, S2) -------------------------------

    def test_db_roles_migrate_refuses_a_running_stack(self):
        """C2: on a running server the move belongs to release (hold, seal, restore point, log):
        db-roles --migrate refuses while any app container runs, before anything changes."""
        out, calls = self.run_staging([release(A), release(RUNNING, backend=2)], "db-roles", "--migrate",
                                      "--operator", "tester", running="db backend frontend",
                                      fake={"FAKE_ROLES": "single", "FAKE_EXEC_OK": "1"})
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("backend frontend still run(s): on a running server the roles move inside a release", out.stderr)
        self.assertNothingChanged(calls)
        self.assertNotIn("ihr_fleet_run hold", calls)
        self.assertNotIn("DB-ROLES --migrate STOPPED", out.stderr)  # refused before its trap

    def test_running_app_containers_are_read_fail_closed(self):
        """P1-T19b verifier, item 3: when compose ps fails, db-roles --migrate (and release's
        "no backend, but a worker runs" check, D8) refuse, never take it for "none run"."""
        out, calls = self.run_staging([release(A), release(RUNNING, backend=2)], "db-roles", "--migrate",
                                      "--operator", "tester", fake={"FAKE_ROLES": "single", "FAKE_PS_FAILS": "1"})
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("could not tell whether any app container runs (docker compose ps, above): nothing was changed",
                      out.stderr)
        self.assertNothingChanged(calls)
        self.assertNotIn("pg_roles", calls)
        self.assertNotIn("DB-ROLES --migrate STOPPED", out.stderr)
        out, calls = self.run_staging([release(A)], "release", "--operator", "tester", fake={"FAKE_PS_FAILS": "1"})
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("could not tell whether any app container runs (docker compose ps, above): nothing changed",
                      out.stderr)
        self.assertNothingChanged(calls)
        self.assertIsNone(self.after["attempt"])

    def test_db_roles_migrate_moves_only_the_build_released_last(self):
        """C2: a stack whose backend image is not the last logged release's build (deploy.sh
        --no-start moved the tags) is refused: it would migrate a build never released, outside
        the window, with no restore point. No release logged: refused too. The released build
        goes on (here: the roles are split already)."""
        for entries, why in (([release(A), release(RUNNING, backend=40)], f"is not the build of the release logged last ({RUNNING})"),
                             ([], "no release is logged here")):
            with self.subTest(why=why):
                out, calls = self.run_staging(entries, "db-roles", "--migrate", "--operator", "tester", running="db",
                                              fake={"FAKE_ROLES": "single"})
                self.assertEqual(out.returncode, 1, out.stderr)
                self.assertIn(why, out.stderr)
                self.assertNothingChanged(calls)
                self.assertNotIn("pg_roles", calls)  # the database not even read
        out, calls = self.run_staging([release(A), release(RUNNING, backend=2)], "db-roles", "--migrate",
                                      "--operator", "tester", running="db", fake={"FAKE_ROLES": "split"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("The roles are split already: nothing to move", out.stdout)

    def test_no_rollback_while_the_database_has_its_single_role(self):
        """C1: every rollback ends with `up`, which refuses a single-role database (after the
        image tags were moved): refused before any change, naming the way that works."""
        out, calls = self.run_staging([release(A), release(RUNNING)], "rollback", "--operator", "tester",
                                      running="db", fake={"FAKE_ROLES": "single", "FAKE_SQL": "0"})
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("refused: the database still has its single role ihr", out.stderr)
        self.assertIn("scripts/staging.sh db-roles --serve-previous --operator", out.stderr)
        self.assertNothingChanged(calls)

    def test_a_release_stopped_before_the_move_names_no_rollback(self):
        """C1/S2: the first release with the move holds every hospital, then stops before the
        move (here `compose stop` of the workers fails). The database still has its single role:
        its advice names db-roles --serve-previous, never a rollback, `up` or a fleet step in
        the owner's container (all refused or unable to log in before the move). Its backend
        still runs, so fleet release and fleet window --clear (which run in it before the move)
        are named too (P1-T19b verifier, item 2)."""
        from datetime import datetime, timedelta, timezone
        ist = timezone(timedelta(hours=5, minutes=30))
        start = (datetime.now(ist) - timedelta(minutes=5)).strftime("%d-%m-%Y %H:%M")
        fake = {"FAKE_WINDOW": f"window: {start} IST for 120 min", "FAKE_IMAGES": OLD, "FAKE_SQL": "0",
                "FAKE_EXEC_OK": "1", "FAKE_ROLES": "single"}
        out, calls = self.run_staging([release(A)], "release", "--operator", "tester", running="backend db", fake=fake)
        self.assertNotEqual(out.returncode, 0, out.stderr)  # the fake's `compose stop` fails (9)
        self.assertIn("this release moves it to ihr_owner and ihr_app", out.stdout)
        self.assertRegex(calls, r"exec -T backend python manage.py ihr_fleet_run hold --operator tester")
        stop = out.stderr[out.stderr.index("RELEASE STOPPED BEFORE ANY MIGRATION"):]
        self.assertIn("Serve them again: scripts/staging.sh db-roles --serve-previous --operator tester", stop)
        self.assertIn("or, as its backend still runs, scripts/staging.sh fleet release --operator tester then "
                      "scripts/staging.sh fleet window --clear --operator tester", stop)
        for wrong in ("staging.sh rollback", "staging.sh up", "fleet migrate", "run --rm"):
            self.assertNotIn(wrong, stop)

    def test_rollback_advice_is_what_rollback_accepts_across_the_move(self):
        """C1: stage 2 before the move (single) names only db-roles --serve-previous; after the
        move, a rollback whose target predates the split (rel_roles_guard refuses it) is never
        named; one that does not predate it still is."""
        base = (f'set -euo pipefail\nREL_SERVE_PREVIOUS="scripts/staging.sh db-roles --serve-previous --operator"\n'
                f'rel_rollback_advice() {{{func("rel_rollback_advice")}}}\nrel_release_exit() {{{func("rel_release_exit")}}}\n'
                f'rel_serve_outgoing_advice() {{{func("rel_serve_outgoing_advice")}}}\n'
                'rel_outgoing_storage() { echo yes; }\n'
                'rel_workers_marker() { :; }\nrunning_backend_id() { :; }\nrelease_tag_of() { echo R1; }\n'
                'rel_target_pre_s3split() { return 1; }\n'
                'rlog() { echo target=R0; echo restore_point=release-r0-1763000000; }\n'
                'compose() { [[ -z "${BACKEND_RUNS:-}" ]] || echo cid-backend; }\n'
                'REL_WORKERS=(celery-worker celery-beat provisioner staff-worker)\n'
                'REL_LOG=x REL_ATTEMPT=y REL_PREV="" REL_OP=tester REL_MOVE=0\n')
        # (the target R0 predates the split in every case: rel_target_presplit succeeds)
        cases = (("single", ["db-roles --serve-previous --operator tester"], ["staging.sh rollback"]),
                 ("unreadable", ["db-roles --serve-previous --operator tester"], ["staging.sh rollback"]),
                 ("split", ["predates the split database roles", "Fix forward"], ["staging.sh rollback", "serve-previous"]))
        for state, want, never in cases:
            with self.subTest(state=state):
                # unreadable: the database did not answer, and this release still had the move to do
                state_fn = "db_roles_state() { return 1; }\nREL_MOVE=1\n" if state == "unreadable" \
                    else f"db_roles_state() {{ echo {state}; }}\n"
                script = base + state_fn + "rel_target_presplit() { return 0; }\nREL_STAGE=2\nrel_release_exit 1\n"
                out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
                self.assertIn("RELEASE DID NOT FINISH (exit 1)", out.stderr)
                for text in want:
                    self.assertIn(text, out.stderr)
                for text in never:
                    self.assertNotIn(text, out.stderr)
        # a target from after the split is still named (the filter is not blanket)
        # single, the backend still runs: the fleet steps (in it, before the move) are named too;
        # not when it does not run, nor when the database did not answer (the fleet dispatch
        # would then send them to the owner's container, which cannot log in before the move)
        for state_fn, backend, named in (("db_roles_state() { echo single; }\n", "1", True),
                                         ("db_roles_state() { echo single; }\n", "", False),
                                         ("db_roles_state() { return 1; }\nREL_MOVE=1\n", "1", False)):
            with self.subTest(state=state_fn, backend=backend):
                out = subprocess.run(["bash", "-c", base + state_fn + f"BACKEND_RUNS={backend}\nrel_rollback_advice\n"],
                                     capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertIn("db-roles --serve-previous --operator tester", out.stdout)
                fleet = ("scripts/staging.sh fleet release --operator tester then "
                         "scripts/staging.sh fleet window --clear --operator tester")
                (self.assertIn if named else self.assertNotIn)(fleet, out.stdout)
                self.assertNotIn("staging.sh rollback", out.stdout)
        out = subprocess.run(["bash", "-c", base + "db_roles_state() { echo split; }\nrel_target_presplit() { return 1; }\n"
                              "rlog() { [[ \"${*: -1}\" == --restore ]] || return 1; echo target=R0; echo restore_point=release-r0-1763000000; }\n"
                              "rel_rollback_advice\n"], capture_output=True, text=True)
        self.assertIn("scripts/staging.sh rollback --restore (back to the window's restore point release-r0-1763000000", out.stdout)

    def test_a_first_release_onto_split_keys_stopped_at_stage_1_names_its_backend(self):
        """Second fixer: the first release onto P1-T20b has no rollback (its target predates the
        split keys), but stopped before any migration (stage 1) its backend still runs with the
        keys it was started with: fleet release and fleet window --clear in it serve the
        hospitals again, named only while its storage key still works."""
        base = (f'set -euo pipefail\nREL_SERVE_PREVIOUS="scripts/staging.sh db-roles --serve-previous --operator"\n'
                f'rel_rollback_advice() {{{func("rel_rollback_advice")}}}\n'
                f'rel_serve_outgoing_advice() {{{func("rel_serve_outgoing_advice")}}}\n'
                'db_roles_state() { echo split; }\nrunning_backend_id() { :; }\nrelease_tag_of() { echo R1; }\n'
                'rel_target_presplit() { return 1; }\nrel_target_pre_s3split() { return 0; }\n'
                'rlog() { echo target=R0; echo restore_point=release-r0-1763000000; }\n'
                'compose() { [[ -z "${BACKEND_RUNS:-}" ]] || echo cid-backend; }\n'
                'REL_WORKERS=(celery-worker celery-beat provisioner staff-worker)\n'
                'REL_LOG=x REL_ATTEMPT=y REL_OP=tester REL_MOVE=0\n')
        fleet = ("or, as its backend still runs and the database is unchanged by this release, serve the hospitals "
                 "in it: scripts/staging.sh fleet release --operator tester then scripts/staging.sh fleet window "
                 "--clear --operator tester")
        for stage, backend, storage, want, never in (
            (1, "1", "yes", [fleet, "only while the gateway is not restarted"], []),
            (1, "1", "provider", [fleet, "only while its old storage key (BUCKET_KEY) still works at the provider"], []),
            (1, "1", "no", ["its object-storage key is no longer the gateway's"], [fleet]),
            (1, "", "yes", [], [fleet, "its object-storage key"]),  # no backend runs
            (2, "1", "yes", [], [fleet, "its object-storage key"]),  # migrations may have begun
        ):
            with self.subTest(stage=stage, backend=backend, storage=storage):
                script = (base + f"rel_outgoing_storage() {{ echo {storage}; }}\nREL_STAGE={stage}\n"
                          f"BACKEND_RUNS={backend}\nrel_rollback_advice\n")
                out = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(out.stderr, "")
                self.assertIn("predates the split object-storage keys (P1-T20b). Fix forward", out.stdout)
                self.assertNotIn("staging.sh rollback", out.stdout)
                for text in want:
                    self.assertIn(text, out.stdout)
                for text in never:
                    self.assertNotIn(text, out.stdout)

    def test_outgoing_storage_compares_the_key_ids_the_containers_were_started_with(self):
        """The backend's own key (BUCKET_KEY, or FILE_UPLOAD_KEY) against the running gateway's."""
        base = (f'set -euo pipefail\nrel_outgoing_storage() {{{func("rel_outgoing_storage")}}}\n'
                f'container_env() {{{func("container_env")}}}\n'
                'compose() { case "$*" in *"ps -q backend") echo cid-b ;; *"ps -q s3") [[ -z "${NO_S3:-}" ]] && echo cid-s || true ;; esac; }\n'
                'docker() { case "${*: -1}" in cid-b) printf "%b" "$B_ENV" ;; cid-s) printf "%b" "$S_ENV" ;; esac; }\n')
        for site, b_env, s_env, no_s3, want in (
            ("local", r"BUCKET_KEY=k1\nX=1\n", r"IHR_S3_CARE_KEY=k1\n", "", "yes"),
            ("local", r"BUCKET_KEY=k1\n", r"IHR_S3_FILES_KEY=k2\n", "", "no"),  # gateway recreated split
            ("local", r"BUCKET_KEY=k1\n", r"IHR_S3_CARE_KEY=k1\n", "1", "no"),  # no gateway runs
            ("local", r"FILE_UPLOAD_KEY=f\n", r"IHR_S3_FILES_KEY=f\n", "", "yes"),
            ("local", r"FILE_UPLOAD_KEY=f\n", r"IHR_S3_FILES_KEY=g\n", "", "no"),
            ("", r"BUCKET_KEY=k1\n", "", "", "provider"),
        ):
            with self.subTest(site=site, b=b_env, s=s_env, no_s3=no_s3):
                script = base + f'env_get() {{ echo "{site}"; }}\nrel_outgoing_storage\n'
                out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                     env={**os.environ, "B_ENV": b_env, "S_ENV": s_env, "NO_S3": no_s3})
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(out.stdout.strip(), want)

    def test_fleet_release_at_stage_1_runs_in_the_outgoing_build(self):
        """Second fixer: after the move of the roles, while a release's migrations have not
        started and an outgoing build serves, fleet release and hold run in it (old_manage), never
        the new build on the old schema (D10); once the migrations started, in the new build's
        migrate one-off as before."""
        fake = {"FAKE_ROLES": "split", "FAKE_EXEC_OK": "1"}
        for migrations, outgoing in (("none", True), ("started", False)):
            with self.subTest(migrations=migrations):
                out, calls = self.run_staging([release(A)], "fleet", "release", "--operator", "tester",
                                              running="db backend", fake=fake,
                                              attempt_line={**attempt(RUNNING), "via": "release", "migrations": migrations})
                if outgoing:  # (the stand-in docker refuses a compose run: its exit is not looked at)
                    self.assertEqual(out.returncode, 0, out.stderr)
                in_old = "exec -T backend python manage.py ihr_fleet_run release --operator tester" in calls
                in_new = "run --rm --no-deps -T migrate python manage.py ihr_fleet_run release --operator tester" in calls
                self.assertEqual((in_old, in_new), (outgoing, not outgoing), calls)
        # the running backend is this stack's build: no outgoing build, the migrate one-off
        out, calls = self.run_staging([release(A)], "fleet", "release", "--operator", "tester", running="db backend",
                                      fake={**fake, "FAKE_RUNNING_IMAGE": CURRENT},
                                      attempt_line={**attempt(RUNNING), "via": "release", "migrations": "none"})
        self.assertIn("run --rm --no-deps -T migrate python manage.py ihr_fleet_run release --operator tester", calls)

    def test_repo_info_in_a_release_recreates_nothing(self):
        """Second fixer: with backups set up but the backup service not running, the release's
        repository read (rel_repo_info) falls back to a one-off that recreates no container: a
        recreated gateway would carry only the split keys and kill the outgoing backend's key
        before `up`. A plain repo_info (the stack may be down) still creates what is missing."""
        base = (f'set -euo pipefail\nrepo_info() {{{func("repo_info")}}}\nrel_repo_info() {{{func("rel_repo_info")}}}\n'
                'compose() { [[ "$1" == ps ]] && return 0; echo "compose $*" >> "$LOG"; }\n'
                'env_get() { [[ "$1" == IHR_S3 ]] && echo local || true; }\n'
                's3_keys_current() { :; }\npg_container() { :; }\ndie() { echo "$*" >&2; exit 1; }\n'
                'docker() { :; }\n')
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "log"
            for call, keep in (("rel_repo_info", True), ("repo_info", False)):
                with self.subTest(call=call):
                    log.write_text("")
                    out = subprocess.run(["bash", "-c", base + call + "\n"], capture_output=True, text=True,
                                         env={**os.environ, "LOG": str(log)})
                    self.assertEqual(out.returncode, 0, out.stderr)
                    ups = [ln for ln in log.read_text().splitlines() if ln.startswith("compose up")]
                    self.assertEqual(len(ups), 2, ups)
                    self.assertIn("db backup", ups[0])
                    self.assertTrue(ups[1].endswith(" s3"), ups)
                    for ln in ups:
                        (self.assertIn if keep else self.assertNotIn)("--no-recreate", ln)

    def test_serve_previous_starts_the_build_that_ran(self):
        """C1: a release stopped before the move: the build that ran starts on its own
        containers, lifts the holds and clears the window in its backend (as ihr). Never `up`
        (that would recreate them with the new images and secrets) and never `compose start`:
        every app service depends on the one-shot migrate, which has no container here, so
        Compose v5.7.1 fails with "missing dependency migrate" before the holds are lifted (the
        fake docker fails the same way; P1-T19b verifier, item 1). `docker start` of their own
        containers, never a one-off left by compose run. Refused once the roles are split."""
        apps = "backend frontend celery-worker celery-beat provisioner staff-worker"
        out, calls = self.run_staging([release(A)], "db-roles", "--serve-previous", "--operator", "tester",
                                      running="db", fake={"FAKE_ROLES": "single", "FAKE_EXEC_OK": "1",
                                                          "FAKE_STOPPED": apps, "FAKE_ONEOFF": "celery-worker"})
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = calls.splitlines()
        at = [next(i for i, ln in enumerate(lines) if re.search(pat, ln)) for pat in (
            r"^start cid-backend$",
            r"exec -T backend python manage.py ihr_fleet_run release --operator tester",
            r"exec -T backend python manage.py ihr_fleet_run window --clear --operator tester",
            r"^start cid-frontend cid-celery-worker cid-celery-beat cid-provisioner cid-staff-worker$")]
        self.assertEqual(at, sorted(at), calls)
        self.assertNotRegex(calls, r"compose .* (up|down|run|stop|start) ")
        self.assertNotRegex(calls, r"^start .*cid-run-", )
        self.assertIn("Served by the build that ran", out.stdout)
        # Only the services with a container of their own are started (here: no workers left)
        out, calls = self.run_staging([release(A)], "db-roles", "--serve-previous", "--operator", "tester",
                                      running="db", fake={"FAKE_ROLES": "single", "FAKE_EXEC_OK": "1",
                                                          "FAKE_STOPPED": "backend frontend"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertRegex(calls, r"(^|\n)start cid-frontend\n")
        # No backend container left: refused before anything starts
        out, calls = self.run_staging([release(A)], "db-roles", "--serve-previous", "--operator", "tester",
                                      running="db", fake={"FAKE_ROLES": "single", "FAKE_STOPPED": "frontend"})
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("no backend container of the build that ran is left", out.stderr)
        self.assertNotRegex(calls, r"(^|\n)start |compose .* start ")
        out, calls = self.run_staging([release(A)], "db-roles", "--serve-previous", "--operator", "tester",
                                      running="db backend", fake={"FAKE_ROLES": "split"})
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("only for a database that still has its single role", out.stderr)
        self.assertNotIn("start", calls)

    def test_compose_start_is_never_used_for_an_app_service(self):
        """P1-T19b verifier, item 1: what the fake docker models is this stack's compose.yaml
        (every app service depends on the one-shot migrate, which only ever runs with compose run
        --rm), and staging.sh never runs `compose start` (only the database's own advice names
        `docker compose start db`, which has no dependency)."""
        compose = (ROOT / "deploy" / "staging" / "compose.yaml").read_text()
        anchor = compose[compose.index("x-app") if "x-app" in compose else 0:compose.index("\nservices:")]
        self.assertRegex(anchor, r"depends_on:\n\s+migrate:\n\s+condition: service_completed_successfully")
        db = compose[compose.index("\n  db:\n"):compose.index("\n  redis:\n")]
        self.assertNotIn("depends_on", db)
        code = "\n".join(ln for ln in STAGING_SH.splitlines() if not ln.lstrip().startswith("#"))
        self.assertNotRegex(code.replace("(docker compose start db)", ""), r"(^|[;&|(]\s*|\s)compose start ")
        self.assertNotIn("docker compose start backend", STAGING_SH)
        self.assertEqual(re.findall(r"docker compose start \w+", STAGING_SH), ["docker compose start db"])
        with tempfile.TemporaryDirectory() as d:  # the fake itself refuses as Compose does
            dk = Path(d) / "docker"
            dk.write_text(FAKE_DOCKER)
            env = {**os.environ, "FAKE_LOG": str(Path(d) / "calls"), "FAKE_RUNNING": "db redis",
                   "FAKE_STOPPED": "backend frontend celery-worker"}
            for svc in ("backend", "frontend", "celery-worker"):
                out = subprocess.run([sys.executable, str(dk), "compose", "-f", "x", "start", svc], env=env,
                                     capture_output=True, text=True)
                self.assertEqual((out.returncode, out.stderr), (1, "missing dependency migrate\n"), svc)
            out = subprocess.run([sys.executable, str(dk), "compose", "-f", "x", "start", "db"], env=env,
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)

    def test_start_existing(self):
        """The release's advice "start the old backend again" (and the advice after a release
        stopped before the move): scripts/staging.sh start-existing, docker start of the
        service's own containers, never Compose; app services only; refused with none left."""
        out, calls = self.run_staging([], "start-existing", "backend", running="db",
                                      fake={"FAKE_STOPPED": "backend", "FAKE_ONEOFF": "backend"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual([ln for ln in calls.splitlines() if ln.startswith("start ")], ["start cid-backend"], calls)
        self.assertNotRegex(calls, r"compose .* (up|start) ")
        for args, why in ((("traefik",), "traefik is not an app service"),
                          (("backend", "celery-beat"), "no container of celery-beat is left to start"),
                          ((), "usage: scripts/staging.sh start-existing")):
            with self.subTest(args=args):
                out, calls = self.run_staging([], "start-existing", *args, running="db", fake={"FAKE_STOPPED": "backend"})
                self.assertEqual(out.returncode, 1, out.stderr)
                self.assertIn(why, out.stderr)
                self.assertNotRegex(calls, r"(^|\n)start ")
        out, calls = self.run_staging([], "start-existing", "backend", running="db",
                                      fake={"FAKE_STOPPED": "backend", "FAKE_PS_FAILS": "1"})
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("could not list the containers of backend", out.stderr)
        self.assertNotRegex(calls, r"(^|\n)start ")

    def test_fleet_before_the_move_runs_in_the_running_backend(self):
        """C1: before the move ihr_owner does not exist, so the owner's migrate one-off cannot
        log in: fleet release and hold run in the running backend (as ihr), other steps refuse;
        after the move they run in the owner's container as before."""
        fake = {"FAKE_ROLES": "single", "FAKE_EXEC_OK": "1"}
        out, calls = self.run_staging([release(A)], "fleet", "release", "--operator", "tester", running="db backend", fake=fake)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("exec -T backend python manage.py ihr_fleet_run release --operator tester", calls)
        self.assertNotIn(" run ", calls)
        out, calls = self.run_staging([release(A)], "fleet", "migrate", "--operator", "tester", running="db backend", fake=fake)
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("only fleet window, release and hold run before it", out.stderr)
        self.assertNotIn(" run ", calls)
        out, calls = self.run_staging([release(A)], "fleet", "release", "--operator", "tester", running="db backend",
                                      fake={**fake, "FAKE_ROLES": "split"})
        self.assertIn("run --rm --no-deps -T migrate python manage.py ihr_fleet_run release --operator tester", calls)

    def test_releases_lists_the_stack(self):
        out, _ = self.run_staging([release(A), release(B), rollback(A, B)], "releases")
        self.assertEqual(out.stdout.split(), [A])

    def test_the_same_build_is_logged_once(self):
        """D1: the logged build deployed again: release-record logs nothing new."""
        logged = [release(A), release(RUNNING, backend=2)]
        out, _ = self.run_staging(logged, "release-record", attempt_line={**attempt(RUNNING, backend=2), "via": "plain"},
                                  running="backend", fake={"FAKE_RUNNING_IMAGE": CURRENT})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("already the last logged release: not logged again", out.stdout)
        self.assertEqual(self.after["log"].count(f"tag={RUNNING}"), 1)
        self.assertIsNone(self.after["attempt"])

    def test_a_plain_start_never_overwrites_an_unfinished_release(self):
        """D3/O4: release-note (deploy.sh's plain start) refuses while a release is unfinished."""
        open_one = {**attempt(B, rp=point(B), rp_time="1763000000"), "via": "release", "migrations": "started"}
        out, calls = self.run_staging([release(A)], "release-note", attempt_line=open_one)
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn(f"release {B} is unfinished", out.stderr)
        self.assertEqual(self.after["attempt"], rl.format_line(open_one) + "\n")
        self.assertNothingChanged(calls)

    def test_release_keeps_the_window_restore_point_and_its_record_first(self):
        """D2/D3/O4/D10: a release of another build over an unfinished one writes its record
        before its first change, carrying the window's restore point (rp_from: whose it was);
        refused at once with no window open, it says nothing changed and the record goes back
        as it was; the advice runs in the running backend."""
        open_one = {**attempt(B, rp=point(B), rp_time="1763000000"), "via": "release", "migrations": "started"}
        out, calls = self.run_staging([release(A)], "release", "--operator", "tester", attempt_line=open_one,
                                      running="backend")
        self.assertEqual(out.returncode, 1, out.stderr)
        snap = [ln for ln in calls.splitlines() if ln.startswith("ATTEMPT ")]
        self.assertEqual(len(snap), 1, calls)
        written = rl.parse_words(snap[0].split(" ")[1:])
        self.assertEqual((written["tag"], written["restore_point"], written["rp_from"], written["migrations"], written["via"]),
                         (RUNNING, point(B), B, "started", "release"))
        self.assertIn("no announced window is open now", out.stderr)
        self.assertIn("scripts/staging.sh fleet window --start", out.stderr)
        self.assertIn("RELEASE DID NOT START (exit 1): nothing was held, stopped or migrated", out.stderr)
        self.assertNotIn("rollback", out.stderr)
        self.assertEqual(self.after["attempt"], rl.format_line(open_one) + "\n")
        self.assertNotRegex(calls, r"ihr_fleet_run hold|compose .* (up|down|run|stop) ")

    def test_release_refuses_a_half_stopped_stack(self):
        """D8/O8: no backend but a worker runs: refused (no hold would protect the migrations)."""
        out, calls = self.run_staging([release(A)], "release", "--operator", "tester", running="celery-worker")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("the backend is not running, but celery-worker still run(s)", out.stderr)
        self.assertNothingChanged(calls)
        self.assertIsNone(self.after["attempt"])

    def test_a_release_needs_the_split_roles_secrets(self):
        """P1-T19b: this build runs as ihr_app and ihr_owner; without their addresses the release
        refuses before its record or any change."""
        out, calls = self.run_staging([release(A)], "release", "--operator", "tester", running="backend", split=False)
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("scripts/staging.sh db-split-secrets", out.stderr)
        self.assertNothingChanged(calls)
        self.assertIsNone(self.after["attempt"])

    def test_no_rollback_across_the_move_of_the_roles(self):
        """P1-T19b: a target release whose kept stack predates the split roles would start the
        app as the renamed single role: refused before anything changes (both kinds)."""
        old_stack = {A: "services:\n  backend:\n    env_file:\n      - secrets/app.env\n"}
        restorable = release(RUNNING, tenant=7, rp=point(RUNNING), rp_time="1763000000")
        for extra, running_release in (((), release(RUNNING)), (("--restore",), restorable)):
            with self.subTest(extra=extra):
                out, calls = self.run_staging([release(A), running_release], "rollback", *extra, stacks=old_stack,
                                              fake={"FAKE_IMAGES": " ".join(img(n) for n in (10, 11, 20, 30))})
                self.assertEqual(out.returncode, 1, out.stderr)
                self.assertIn(f"release {A} predates the split database roles", out.stderr)
                self.assertNothingChanged(calls)
        # a target whose stack has the owner's address is not refused for it
        new_stack = {A: "    env_file:\n      - secrets/app.env\n      - secrets/db-owner.env\n"}
        out, _ = self.run_staging([release(A), release(RUNNING)], "rollback", stacks=new_stack,
                                  fake={"FAKE_IMAGES": " ".join(img(n) for n in (10, 11, 20, 30))})
        self.assertNotIn("predates the split database roles", out.stderr)

    def test_no_rollback_across_the_split_storage_keys(self):
        """P1-T20b review C1/S3: once this site's storage keys are split, a target whose kept
        stack predates them (its start.sh knows only the combined ihr-care key) would sign
        patient files with the pictures key, fail to start its gateway and make the pictures
        bucket public again: refused before anything changes (both kinds); its own start.sh
        is not refused for it."""
        compose = "    env_file:\n      - secrets/app.env\n      - secrets/db-owner.env\n"
        old = {A: (compose, {"deploy/s3/start.sh": 'KEYS="IHR_S3_ADMIN_KEY IHR_S3_CARE_KEY IHR_S3_CARE_SECRET"\n'})}
        new = {A: (compose, {"deploy/s3/start.sh": (ROOT / "deploy" / "s3" / "start.sh").read_text()})}
        split = "FILE_UPLOAD_KEY=f\nFILE_UPLOAD_SECRET=fs\nFACILITY_S3_KEY=p\nFACILITY_S3_SECRET=ps\n"
        images = {"FAKE_IMAGES": " ".join(img(n) for n in (10, 11, 20, 30))}
        restorable = release(RUNNING, tenant=7, rp=point(RUNNING), rp_time="1763000000")
        for extra, running_release in (((), release(RUNNING)), (("--restore",), restorable)):
            with self.subTest(extra=extra):
                out, calls = self.run_staging([release(A), running_release], "rollback", *extra, stacks=old,
                                              app_extra=split, fake=images)
                self.assertEqual(out.returncode, 1, out.stderr)
                self.assertIn(f"release {A} predates the split object-storage keys (P1-T20b)", out.stderr)
                self.assertIn("make the pictures bucket public again", out.stderr)
                self.assertNothingChanged(calls)
        out, _ = self.run_staging([release(A), release(RUNNING)], "rollback", stacks=new, app_extra=split, fake=images)
        self.assertNotIn("predates the split object-storage keys", out.stderr)
        # a site whose keys are not split yet (both builds before P1-T20b): not refused for it
        out, _ = self.run_staging([release(A), release(RUNNING)], "rollback", stacks=old, fake=images)
        self.assertNotIn("predates the split object-storage keys", out.stderr)

    def test_rollback_advice_never_names_a_rollback_across_the_key_split(self):
        base = (f'set -euo pipefail\nrel_rollback_advice() {{{func("rel_rollback_advice")}}}\n'
                'db_roles_state() { echo split; }\nrunning_backend_id() { :; }\nrelease_tag_of() { echo R1; }\n'
                'rlog() { echo target=R0; echo restore_point=release-r0-1763000000; }\n'
                'rel_target_presplit() { return 1; }\nREL_LOG=x REL_ATTEMPT=y\n')
        out = subprocess.run(["bash", "-c", base + "rel_target_pre_s3split() { return 0; }\nrel_rollback_advice\n"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("predates the split object-storage keys (P1-T20b). Fix forward", out.stdout)
        self.assertNotIn("staging.sh rollback", out.stdout)
        out = subprocess.run(["bash", "-c", base + "rel_target_pre_s3split() { return 1; }\nrel_rollback_advice\n"],
                             capture_output=True, text=True)
        self.assertIn("scripts/staging.sh rollback (no --restore", out.stdout)

    def test_fleet_window_runs_in_the_running_backend(self):
        """D10: never the new build (a one-off of the moved tag) on the old schema."""
        out, calls = self.run_staging([], "fleet", "window", "--clear", "--operator", "tester", running="backend")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertRegex(calls, r"compose .* exec -T backend python manage.py ihr_fleet_run window --clear")
        self.assertNotIn(" run --rm", calls)


if __name__ == "__main__":
    unittest.main()
