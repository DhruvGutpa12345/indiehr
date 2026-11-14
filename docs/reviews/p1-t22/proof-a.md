# P1-T22a local staging proof: nightly per-hospital dumps (14-11-2026)

A local proof on this machine (`scripts/staging.sh init app.ihr-proof.in --local`, `s3-init`,
`mail-init --test`, `dumps-init --local`, `up`), with the dumper on its own `dumpsout` network to
the stack's SeaweedFS (`s3-dumpsout`). Synthetic data only. Started by the first builder at 07:40;
the machine rebooted at about 10:50 while it ran, Docker restarted the stack, and the second
builder finished it from 10:58. The first builder's logs were lost with its scratchpad: every
number below was measured again after the reboot. No key or password appears here.

## The stack and its hospitals

| Hospital | Status | Schema on disk | Notes |
| --- | --- | --- | --- |
| alpha | active | 9.6 MB tables (18 MB with the rest) | made with `ihr_create_tenant` |
| beta | active | 9.6 MB | |
| bulk | active | 358 MB | 500,745 generated audit entries (a busy hospital's mix, about 10 days at 50,000 a day) |
| gamma | active, **held** (`fleet hold --slug gamma`, reason `window`) | 9.6 MB | the held case |

Each hospital schema has 1,101 relations (CARE's tables, our plugins', indexes, sequences).

## Results

| Check | Outcome | Time |
| --- | --- | --- |
| Dump every hospital now (`dumps-now --slug alpha --slug beta --slug bulk --slug gamma`, 2 at a time): lock, one snapshot, `pg_dump -Fc --compress=zstd`, seal, upload, read back and check, manifest, then the night's check (each new dump restored schema-only in a throwaway server inside the container) | 3 dumped, 1 held, 0 failed; exit 0 | **7.3 s** wall in all (second run: 7.1 s) |
| bulk alone (manifest `seconds`: lock to sealed) | 358 MB on disk → 55.7 MB zstd dump → 74.2 MB sealed | 1.6 s |
| alpha, beta (each) | 1.3 MB sealed | 0.3 s |
| `dumps-check --deep --all`: 10 dumps of 4 hospitals, every manifest authentic and chained, objects present with their sizes, each read back, decrypted, listed (`pg_restore --list`: that schema only) and restored schema-only | 0 problem(s); bucket lock "Object Lock, at least 30 days, versioning on" | 11.5 s |
| `dumps-get <bulk> latest dump` (fetch, SHA-256, decrypt, plaintext SHA-256) | 55,674,272 bytes, matches its manifest | 1.2 s |
| `dumps-get <bulk> latest public` | the hospital's 6 public tables (hospital, domain, schema state, invitations, access requests, operations), parse | 0.6 s |
| Full restore of bulk's dump into a throwaway database `bench_proof_restore` in the proof's Postgres (`pg_restore --single-transaction --exit-on-error`) | 500,745 entries back, 352 MB database; dropped afterwards | **5.1 s** |
| `pg_restore --list` of bulk's dump | 1,101 relations' entries | 0.05 s |
| Held hospital | skipped and reported in every run: `ihr-dump: hospital=gamma outcome=held` and `dumper: gamma: held (window): skipped`; metric `ihr_dump_hospitals_held 1` | |
| A hospital whose lock another session holds (`pg_advisory_lock` on bulk's key from a superuser session, `IHR_DUMP_LOCK_WAIT=5`) | `ihr-dump: hospital=bulk outcome=failed reason=lock`; exit 1; `ihr_dump_hospitals_failed 1`; nothing uploaded | 5.5 s |
| `dumps-probe` (the dumper's bucket key) | 0 problem(s): add under `dumps/` ok, read back ok; same name again PreconditionFailed; delete the version, delete with the lock bypass, shorten its lock, hide it with a delete marker, change the bucket's lock, write under `erasure/`, write at the bucket's top: all AccessDenied | |
| `dumps-role` | "ihr_dump as modelled" | |
| The role writes nothing (psycopg as ihr_dump) | UPDATE public.ihr_platform_hospital, CREATE TABLE public.x, CREATE TEMP TABLE, DELETE from a hospital's audit trail: all InsufficientPrivilege; SELECT of bulk's 500,745 entries: allowed | |
| `dump-expire` under the retention (`expire.py --daily-days 3`; `--weekly-days 27`) | "refused: daily dumps are kept at least 14 days (owner, Q-K33)" / "...weekly... 28 days"; exit 1; refused before any key is read | |
| `dumps-expire --dry-run` (14 / 28 days) | "0 object version(s) past retention ... dry run: nothing deleted" | |
| Metrics from another container on `monitoring` and on `internal` | HTTP 403 both (only Prometheus's address and the container itself) | |
| Metrics (`:9110`, inside the container) | no labels at all; after the runs: `hospitals 4`, `hospitals_dumped 3`, `hospitals_held 1`, `hospitals_failed 0`, `stale_hospitals 0`, `bad_manifests 0`, `check_problems 0`, `bucket_locked 1`, `role_ok 1`, `last_run_bytes 76870439`, `largest_bytes 74237746`; a one-off run's and check's figures show in the service's scrape (shared status file) | |
| `scripts/staging.sh smoke`, dumps part | ok: the dumper runs (healthy); its role is ihr_dump as modelled; every dump authentic, chained, present (0 problem(s)); the bucket is locked (30 days) with versioning | |
| Memory | dumper 52 MiB of 512 MiB after the runs; SeaweedFS 660 MiB of 768 MiB | |

Manifest shape (bulk, revision 3): ids, hashes, sizes, the schema name, migration count and their
SHA-256, the schema fingerprint, the audit head (seal number and hash, highest entry id and
transaction), sequence values, relation count, schema bytes, extensions, the snapshot's time and
transaction bounds, the previous manifest's SHA-256 and the HMAC. No names, e-mail addresses or
rows: the public rows travel encrypted in `*.public.enc`.

## Found and fixed during the proof

1. **The health check timed out every minute** (the container went `unhealthy`; "BrokenPipeError
   answering a scrape" in the log). The scrape allow-list resolved `prometheus` inside the request
   when its one-minute cache had expired, and in a proof without monitoring that name takes
   seconds to fail. Now a thread of its own resolves the scrapers every minute and the container's
   own address is always allowed (`Scrapers`; test `test_scrapers_resolved_off_the_request_path`).
   After the fix: healthy, no broken pipes.
2. **A one-off run cleared the service's "running" flag.** Every process opening the shared status
   file reset `running` to 0, so `dumps-now` during the nightly run would have hidden it. Now only
   the service starting (`run`) clears a run left behind by a killed process (test updated).
3. **The disk check ignored the size model for a first dump** (`SEALED_PER_DISK_BYTE` was defined
   but unused). The hospital list now carries each schema's bytes on disk, and the need is the
   larger of 0.30 of them and the last sealed dump with a fifth more, plus 512 MiB (test
   `test_the_disk_check_follows_the_size_model_and_the_last_dump`). Live: alpha 514 MiB, bulk
   614 MiB.
4. **A stop printed a KeyboardInterrupt traceback** (the Postgres base image stops with SIGINT).
   Now one line, "dumper: stopped".
5. **SeaweedFS was killed at its default 448m** while the first builder's run uploaded bulk's
   74 MB dump (raised to 768m by hand for the proof). `dumps-init --local` now writes
   `IHR_S3_MEMORY=768m` into a proof's `.env`. A server's dumps go to their own bucket, not
   SeaweedFS.

## Not proven here (proof artefacts or later tasks)

- The smoke's two other failures are the proof's set-up, not the dumps: gamma is held on purpose
  (503), and the four hospitals were made with `ihr_create_tenant`, not provisioned, so Keycloak
  sync --check reports them "not linked to Keycloak yet".
- Prometheus, Alertmanager and the log alerts were not running in this proof (memory). The rules
  are covered by promtool tests (`deploy/monitoring/prometheus/tests/dumps.yml`) and the log alert
  by `scripts/check-logs.py` cases (`DumpHospitalFailed`).
- The same-disk SeaweedFS is not a second copy; the real bucket (another Indian region, Object
  Lock) is set up at go-live (`infra/aws` has it, tested by `tofu test`).
- Restores into the live cluster, the drill and `restore_hold` are P1-T22b.
