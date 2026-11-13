# P1-T18b independent code review (2026-11-13)

Two fresh Opus reviewers who did not write the code (data safety and design fit; operations and
deploy), then a third agent tried to refute each finding. Of 20 findings: 1 refuted, 5 confirmed
in part and 14 confirmed. A fixer agent fixed them, a verifier agent checked each fix against its
failure scenario, and a second fixer closed three gaps the verifier left. The script tests
(`scripts/test_release_scripts.py`) pass: 59 OK. The fixes below are marked "verified" where the
current code in `scripts/staging.sh`, `scripts/release_log.py` and `scripts/deploy.sh` shows
them and a test covers them.

| # | Finding | Verdict | Fix (verified) |
|---|---|---|---|
| D1 | Deploying the same build again logs a duplicate release, so `rollback` goes from B back to B and reports success | confirmed, high | the log counts lines of one tag as one release, with the oldest line's restore point; `rollback` refuses to roll back to itself; tests for [A, B, B] |
| D2 | The attempt file is written only at step 6, so a release that fails earlier makes `rollback` roll back the healthy running release | confirmed, high | the release's record is written before its first change and restored if nothing was held; rollback advice is given only once something changed (stage 0, 1, 2); `rollback` refuses when hospitals are held and no release is in progress |
| D4 | Restore point names repeat, so recovery stops at the first `release-<tag>` and can drop days of writes | confirmed, high | names are `release-<tag>-<epoch>`; a name already in the log or the attempt is refused before the point is made |
| O1 | `deploy.sh --release` can never succeed: the `tr` set is an invalid range, so the operator is empty | confirmed, high | the hyphen is last in the set and the name is checked against the operator pattern; tests run the snippet; the remote release runs under `setsid nohup` with an output file that deploy.sh follows |
| O2 | Stack files extracted under umask 077 become 0600, so services running as uid 65534 cannot read their config | confirmed, high | only the kept archive is written under umask 077; extraction runs under umask 022; tests check modes 644 and 755 under a caller's umask 077 |
| O3 | After a release that fails before `up`, rollback targets the release before the one still running, and `--restore` picks a point days old | confirmed, high | an open attempt is what a rollback leaves, whether or not its build runs; the target is the release logged last; `--restore` uses the attempt's own point |
| O4 | Fixing forward with a new build replaces the attempt and takes a new restore point after partial migrations | confirmed, high | the window's restore point is carried into the next attempt whatever the build (`rp_from` names its release); a plain start refuses while an attempt is open |
| D3 | An unfinished release's restore point is overwritten when a different build is released or plain-deployed | confirmed, medium | as O4 (the same carry-over; `needs-release` and the plain start respect an open attempt) |
| D5 | `rollback --restore` replaces the database before checking that the target's images exist, and restores with the outgoing Postgres image | confirmed, medium | every target image is checked before the question and before `down`; tags move only after all are checked; the Postgres image is pointed back before the restore |
| D6 | The pre-rollback copy volume name is reused, so the copy merges into an old one; the disk estimate leaves out `pg_wal` | partly, medium | the volume name carries a timestamp and an existing volume is refused; the size is measured with `du` on the real volume (`pg_wal` included); a failed copy is removed |
| D7 | A rollback without restore from an unfinished release leaves every hospital held and the window announced | confirmed, medium | after `up`, the rollback runs `fleet release`, `fleet roles` and `fleet window --clear` before smoke |
| O5 | Rollback without a restore never lifts window holds or clears the window | confirmed, medium | as D7, on both the restore and the non-restore path |
| O7 | The audit seal inside the window recomputes every hospital's full hash chain and reads every anchor while all hospitals are held | confirmed, medium | the full audit check runs before the hold, capped (`IHR_RELEASE_AUDIT_SECONDS`); inside the window only a seal and one fingerprint are written, capped (`IHR_RELEASE_SEAL_SECONDS`) |
| O9 | Restore-point and kept-copy names repeat across retries of the same tag | confirmed, medium | as D4 and D6 (unique point name, timestamped copy volume); a full backup of the restored database follows every restore rollback |
| D8 | `release` with the backend container down skips the hold and leaves the workers running during migrations | partly, low | `release` refuses when the backend is down but the workers or the web app still run; it tells you to stop the stack or start the old backend |
| O8 | Same as D8, seen from operations: the workers may write while migrations run | partly, low | as D8 |
| O6 | A hospital that fails in the window alerts once, then nothing; every normal window pages ContainerNotRunning | partly, low | a window-held hospital whose newest upgrade record failed counts as down (`failed_in_window`, `tasks.py`); the release writes a workers-stopped marker and the container alert holds back until the window's end |
| O10 | `rollback --restore` keeps a copy with too little headroom, ignores the 20 GB floor, and wipes the volume before proving the restore can work | partly, low | the copy is kept only with room for its size plus the larger of 20% and the 20 GB floor; before anything is replaced the repository must show the WAL up to the point (`rel_wal_archived`), else the rollback refuses unless a copy is kept |
| D10 | The "no window open" refusal advises `staging.sh fleet window`, which runs the new image against the old schema | confirmed, low | the refusal says the command runs in the running backend; the window is announced through the outgoing build (`old_manage`); no rollback advice when nothing was held |
| D9 | The backup-set choice uses a strict `<` on whole seconds, so a rollback right after the forced first backup can find no backup | refuted | none needed: the challenger showed the restore point is always stamped well after pgBackRest's stop time (WAL archive wait, manifest write, a second `psql` exec) |

Design attack: design.md (the P1-T18b design items 11 to 16).

The sources hold the findings and the challenger's verdicts but no written report from the
fixers or the verifier for P1-T18b. The "Fix (verified)" column therefore rests on a reading of
the current code and the 59 passing script tests, not on a copy of the verifier's output.

## Known limits

- Re-releasing the logged build while another window is open may make `rollback --restore` pick
  an older point.
- The remote `deploy.sh --release` path (the `setsid nohup` run and its output file) and pruning
  with more than 3 releases are checked by unit tests and static reading only, not on a real
  server.
- A rollback with `--restore` loses what hospitals wrote after the window's restore point (the
  prompt says so); Keycloak changes since then are lost too.
- Where the disk has no room for a copy, the current database is replaced, and only after the
  repository has shown the WAL up to the restore point.
