# P1-T22a independent code review (2026-11-14)

Reviewers who did not write the code read the dumper, the shared sealer, chain and locked bucket,
dump-expire, the `ihr_dump` role, the alerts and the staging commands, and each reported finding
was then challenged by a second agent that tried to refute it. Of 14 findings: 0 refuted, 12
confirmed, 2 confirmed in part (S2, C3). A fixer agent fixed all 14. A verifier then checked each
fix against its failure scenario: it closed 13, found 1 only partly closed (S2) and 4 new problems.
A second round closed those.

| # | Finding | Verdict | Fix (verified) |
|---|---|---|---|
| S1 | A dump can be replaced without detection: write-once is enforced only by the client, and every reader takes the current version | confirmed, high | `Holdings` lists object versions and reports any `dumps/` name with more than one version or a delete marker (`DumpNotAuthentic`); manifests record the dump's and public rows' VersionIds and every reader uses them; `expire` marks such names SUSPECT; the AWS policy denies a put without `If-None-Match`; the probe tests a rewrite. Where a store accepts a write without the header (the stand-in, possibly E2E) a rewrite is detected, not refused: known limit |
| S2 | The server can derive the erasure-record key, so a record's authenticity rests on the bucket's prefix policy | confirmed in part, low | an authentic erasure inside the retention is a problem, records dated in the future are a problem, README go-live step 4 blocks on any probe FAIL; bypass found by the verifier closed in the second round (below). Ed25519 signing not built: known limit |
| S3 | The retention floor can be bypassed on a real bucket (`--allow-short`), and an early erasure raises no alert | confirmed, low | `--allow-short` accepted only on the local stand-in endpoint and refused by the wrappers; gauge `ihr_dump_early_erasures` and alert `DumpErasedEarly` |
| S4 | A lost reply to the manifest upload forks the chain, raising a lasting critical alarm | confirmed, low | on a 412 the bucket layer compares the stored object's size and SHA-256 with ours and counts a match as done (`same_as`), for single, multipart and bytes puts |
| C1 | The dumper keeps the hospital's lock through upload and read-back, so the fleet runner holds that hospital or defers its migration | confirmed, medium | the lock session closes as soon as pg_dump exits; `up` and `release` stop the dumper before migrating; dumper alerts silenced during the release marker; manual fleet runs covered in the second round |
| C2 | The disk check and size model leave out the read-back copy, and a second worker fails at once (reason=disk) instead of waiting | confirmed, medium | the nightly read-back streams with no disk copy; `Reserve` waits for room and fails only if the dump could not fit an idle volume; `DumpDiskLow` follows the bucket's largest sealed dump |
| C3 | Read-back and night checks never read past the table of contents: later tokens and the plaintext SHA-256 are never checked | confirmed in part (low), medium as filed | `read_through` drains what the tool leaves unread, so every token and the SHA-256 are checked; an early stop raises except for `--list` and `--schema-only`; `check --deep` runs `pg_restore -f /dev/null`; a real-`pg_restore` test |
| C4 | `DumpTooOld` fires falsely in any run longer than about 2 hours: its metric is refreshed only when the run ends | confirmed, medium | the oldest-good map is updated after each successful dump |
| C5 | `DumpDrillOverdue` cannot fire in steady state: its second condition uses the oldest dump still held, which weekly expiry keeps young | confirmed, medium | new gauge `first_dump_ever_timestamp_seconds` that never rises (held manifests, erasure records, previous value); rule rewritten; promtool cases |
| C6 | A manifest PUT retried after a lost response forks the chain | confirmed, low | same bug and fix as S4 |
| C7 | `CONNECTION LIMIT 6` is exactly two workers' connections, so role checks and smoke fail during a run and parallel 3-4 breaks | confirmed, low | snapshot taken on the lock's own session (2 sessions per dump); limit 10 tied to the dumper's constants by a test; parallel above 4 refused; `role` exits 2 on "cannot connect" instead of "wrong role" |
| C8 | Off the server, `dumps-get` and `dumps-check --deep` copy the whole sealed dump into a tmpfs (RAM) | confirmed, low | the one-off container uses an anonymous Docker volume (removed by `--rm`); `need_room` checked first |
| C9 | The size model's 18 dumps per hospital assumes daily expiry; weekly expiry holds about 23-25 | confirmed, low | size-model.md gives the weekly-expiry peak (about 25 copies: 75 GB at 1 year, 225 GB at 3 years) for P1-T23; `DumpErasureOverdue` lowered from 45 to 36 days |
| C10 | The lock test is tautological: nothing ties the dumper's lock key to `runner.exclusive()` | confirmed, low | `runner.lock_key()` shared by `exclusive()`; a pinned value in both the plugin suite and `test_dumps` |

Every fix has a regression test, and each was shown red with its fix reverted (27 of 27 in the first
round, 15 of 15 in the second; promtool and `tofu test` also failed against the old rules).

## Second round

The verifier found one fix only partly closed and four new problems. A second fixer closed all of them.

- **S2 bypass (low).** `Holdings` skipped the retention check for any record with `allow_short=true`
  and counted an early erasure only when `status != "completed"`, so a server that derived the erasure
  key could write one record that bridged the chain. Now the retention and future-date checks run on
  every record unless it is provably a proof's (`allow_short` and a stand-in endpoint); an early
  erasure counts whatever its status; one that names no manifest, or a name the dumper cannot date,
  is a problem and never bridges the chain; `expire.py` leaves names that are not the dumper's own
  alone.
- **Log-archive `--allow-short` abbreviation (low).** The wrapper refused only the exact string, so
  `--allow` got through. `allow_abbrev=False` in both `expire.py` files, the archive's `expire.py`
  now checks its endpoint, and the wrappers refuse any argument starting `--a`.
- **Quiet window after the new wait (minor).** `take()` could return inside the 01:45-03:25 quiet
  window. The window is re-checked after the wait for room and after the lock is acquired.
- **C1 leftover (minor).** Manual `staging.sh fleet migrate|roles|packs|--resume` and
  `db-roles --migrate` did not stop the dumper. Now the dumper is stopped first and its own container
  started again afterwards, whatever the outcome, with the fleet's exit code kept.

Final results: dump and archive tests Ran 124 OK (skipped=6); dumper-image tests with real Postgres
Ran 90 OK; app-role mode Ran 680 OK (skipped=45); off mode Ran 646 OK (skipped=49); isolation
mutation check, check-logs and pre-commit (42 hooks) OK. `tofu test` was run by the build (13
passed) but not after the second round, which touched nothing in `infra/`.

Left open, all recorded under "Known limits" in design.md (section "P1-T22a as built"): erasure
records say what their writer says (the prefix ACL and the lock are the real control); the log
archive's erasure records are checked less and it still reads current versions; a dump cut short by
an operator's stop leaves no log line and is redone at the next start; the new bucket checks have
not run against real E2E or AWS; the staging proof was not repeated after the review fixes.

## Process note

The first builder was interrupted when the machine rebooted on 14-11 at about 10:50, while it was in
the local staging proof. Its scratchpad (research notes, proof logs) was lost; the designs and the
working tree survived. A second builder finished the build and the proof from the first one's saved
trail, and the review followed. Afterwards Claude removed six anonymous Docker volumes (about 21 GB)
that the size bench had left behind, and fixed `scripts/dump-size-bench.sh` to remove its
container's volume (`docker rm -f -v`).

Design attack: design.md (P1-T22). Proof: proof-a.md. Size model: size-model.md.
