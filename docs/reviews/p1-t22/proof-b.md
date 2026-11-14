# P1-T22b local staging proof: single-hospital restore and the drill (14-11-2026)

A local proof on this machine with the dev stack stopped: `scripts/staging.sh init app.ihr-proof.in
--local`, `s3-init`, `mail-init --test`, `backup-init --local`, `dumps-init --local`, `up` (3 min 5 s;
14 containers, about 1.5 GB: Keycloak 505 MiB, SeaweedFS 412 MiB, Postgres 171 MiB). The backend
image was built from this working tree (`docker buildx build --build-context plugins=plugins -f
deploy/docker/backend.Dockerfile apps/backend`). Synthetic data only; no key or password appears
here. Afterwards the stack went (`down -v`), with `.env`, `.local`, `secrets/`, the restore folder,
one anonymous volume of the proof and four intermediate images of the rebuilds; `generated/` was put
back as it was before; the dev stack came back (`alpha.localhost` bootstrap: 200).

## The hospitals

| Hospital | How | Contents |
| --- | --- | --- |
| alpha (the restored one) | `ihr_create_tenant`, then a seed in a `manage shell` (the server image has no demo fixtures) | 6 staff users, 6 staff links, an organisation with 6 memberships; **200,000 generated audit entries** (one transaction, sealed); 5 Keycloak accounts made members of its organisation (the 6th link is not a member) |
| beta (the other one) | the same | 6 users, links and memberships; 769 audit entries |

Each schema has 1,101 relations (106 tables).

Then: a fingerprint, `backup-now full` (16 s), `dumps-now --slug alpha --slug beta` (4 s); two files
put under alpha's prefix in the patient bucket (CARE's own key), a second `dumps-now --slug alpha`
(3 s; `2026-11-14 r2`, 11.7 MB sealed).

## Alpha changes after its dump

A new user (id 7) and an invitation for it; user 1 switched off; user 2 renamed; user 3's membership
soft-deleted; a Redis key in alpha's and in beta's keyspace; one file replaced and one new file
uploaded; sealed (seal 5) and fingerprinted, so a stored fingerprint names a seal the restore will
supersede.

## The restore

`scripts/staging.sh hospital-restore alpha --dump latest --reason "proof: alpha back to its nightly dump"`:
**exit 0 in 42 s** (38 s from the fetch to "served again"; the rest is one-off containers starting).

| Phase / step | Time | What it did |
| --- | --- | --- |
| fetch and check (`dumper.py manifest` and `get`) | 2 s | manifest authentic and chained; the dump read back, SHA-256 and MAC checked, decrypted |
| quarantine (dumper image, `--network none`, read-only root) | 4 s (3.5 s inside) | restored as `ihr_quarantine` owning only an empty `h_…` schema; checked; manifest's audit head, sequences and migrations matched; package written: 106 tables, 202,980 rows, 37.8 MB |
| precheck | – | functions "as this build's" (definition hashes equal), dump's highest transaction id 4,128 below the cluster's 4,140, restored head = live seal 4, lock estimate 4,404 of a 6,400-entry lock table |
| hold, drain | 0.0 s | the restore hold set; no app transaction older than it |
| seal | 0.2 s | the live trail sealed (nothing new) and a fingerprint written |
| **restore** (one transaction) | **1.7 s** (load 1.6 s) | trail locked, live schema parked as `ihr_parked_h_2vcc5sst4m_20261114T103927`, package loaded: 1,501 statements, 202,980 rows, shape and row counts checked; guard, triggers and sealing from this build; **3,794 locks held** in that transaction |
| migrate (in-process, the restore's lock) | 1.6 s | migrations (none due), role sync, packs (`care-valuesets v1 unchanged`) |
| reconcile | 0.1 s | 2 of 103 sequences advanced; 1 user kept off (the one switched off after the dump); 1 membership revoked after the dump soft-deleted again; 6 staff links' sessions invalidated; 1 invitation cancelled; 1 Redis key deleted |
| keycloak (admin one-off, `--stop-after keycloak`) | 0.1 s | 5 members; the 1 active link that is not a member switched off; 0 members without a link |
| files | 0.2 s | patient bucket: 3 objects; 1 brought back to its version at the dump, 1 unchanged, 1 newer upload kept (pictures: none) |
| audit | 1.5 s | superseded seal 5 archived (generation 1), restore event written, a `restore` entry, sealed, fingerprinted, `ihr_audit_verify --slug alpha` passed |
| restart | – | backend, celery-beat, celery-worker, provisioner, staff-worker restarted (`staging.sh` between two invocations) |
| smoke | 0.2 s | schema current, triggers and grants as modelled; hold lifted; `https://alpha.app.ihr-proof.in/api/ihr/bootstrap/` 200 |

`--dry-run` beforehand: fetch, quarantine and every pre-check in 11 s, nothing held or changed, the
package volume removed.

## Checks after the restore

| Check | Before | After |
| --- | --- | --- |
| beta's schema (rows, triggers, function bodies, grants, sequences) and every `public` row not alpha's (`package.fingerprint`) | `45e7869f…`, public `28f7fc6c…` | **identical** |
| alpha's users / active | 7 / 6 | 6 / 4 (the new user gone; user 1 still off; the non-member off) |
| alpha's staff links with `sessions_valid_after` set | 0 of 6 | **6 of 6** |
| `users_user_id_seq` / `ihr_audit_entry_id_seq` | 7 / 200,778 (parked) | 7 / 200,788 (never below the parked copy) |
| Redis keys of alpha / beta | 1 / 1 | **0** / 1 (beta's marker still there) |
| the invitation for the post-dump user | failed (the worker's try) | **cancelled** |
| the replaced file / unchanged file / new file | — | "version at the dump" / "never changed" / "uploaded after the dump" (kept) |
| `ihr_audit_verify --all` as the owner with the reader key | — | **audit trail OK**; "1 fingerprint claims accounted for by restore events" |
| the audit watch (`ihr_audit_watch once --pass incremental`, its own role and reader key) | — | `ihr-audit: hospital=alpha event=restore outcome=recorded`; chain_broken 0, fingerprint_mismatch 0 |
| `ihr_audit_verify --slug alpha` as the **app** role (backend) | — | "seal 5 differs from its fingerprint": the app may not read the archive (by design; known limit) |
| the parked schema's USAGE for `ihr_app` / `ihr_audit_watch` | — | false / false (`ihr_dump`: true, through `pg_read_all_data`: it reads only) |
| UPDATE of `ihr_restore_record` as `ihr_owner` | — | refused (`ihr_restore_refuse`) |
| `hospital-restore-expire --dry-run` | — | "0 parked schema(s) past 7 days" |

## A forged record, a cut without one

As the celery worker (the add-only writer key): a forged restore event of generation 2 for alpha
(no superseded segment kept for it) and a fingerprint claiming seal 12 with a made-up hash.
`ihr_audit_verify --all` (owner): **exit 1**, "seal 12, fingerprinted at …, is missing (the trail was
cut)" (the generation-1 claim is still accounted for). The watch: exit 1,
`ihr-audit: hospital=alpha check=chain outcome=failed` and `check=fingerprint outcome=failed`, and the
new event named for a human to confirm (AuditEventRecorded).

## The drill on the drill copy

`scripts/staging.sh hospital-restore-drill --slug beta`: **passed, 22 s in all** (19 s inside):
dump fetched and checked 1 s; quarantine 2 s; the copy from the backups (pgBackRest, `cluster_name
ihr-restore-drill`) 10 s; the restore inside the copy 5 s (restore 0.9 s, migrate 1.5 s); every
outside step a reported dry run; alpha's schema and `public` (but beta's rows) hashed before and after:
unchanged. The one-off ran with `ihr_platform.settings.drill` (no `app.env`, Redis, storage,
fingerprint or Keycloak settings) on a network of its own holding only the copy; `dumps-drill-mark`
recorded it. Nothing was left behind (containers, networks, volumes, the work folder).

Found by the drill and fixed: the web app's system checks (Keycloak addresses, a shared cache) refused
the drill's settings, so the command skips them in drill mode only (`requires_system_checks = []`,
then `self.check()` unless `IHR_RESTORE_DRILL`); CARE's role-sync lock calls django-redis's
`set(nx=True)`, so the drill's local cache (`restore/drill_cache.py`) implements it as `add()`.

## `--from-pitr`

`scripts/staging.sh hospital-restore beta --from-pitr "2026-11-14 16:10:00+05:30" --reason … --dry-run`:
**exit 0 in 19 s**: the copy at that moment 11 s (pgBackRest), `pg_dump` of beta's schema, quarantine
2 s, pre-checks. A first try with a target after the last transaction the archive held failed
("recovery ended before configured recovery target was reached"); it now cleans the copy up and says
why.

## Found and fixed during the proof

1. **The package was unreadable by the app image's user** (the quarantine runs as the dumper image's
   postgres, uid 70; the app as django, uid 100) and the host could not delete it. The package now
   lives in a Docker volume of its own (`ihr-restore-<slug>`, filled from the image's `/state`), made
   world-readable inside it by the quarantine, mounted read-only at `/restore`, removed afterwards
   (`restore_done`); never on the host's disk.
2. **The size line read 1.0 K** (`du` on a compressed ZFS file): `stat -c %s` now.
3. **A stray empty volume** appeared when the plan ran with the volume's mount before it existed:
   mounted only when it exists.
4. **The Keycloak step ran the following steps in the unlogged admin one-off**: `--stop-after
   keycloak` now, the rest in the logged one-off.
5. **A failed copy for `--from-pitr` or the drill left the copy behind** (`restore_drill` exits from
   inside): it runs in a subshell and is cleaned up.
6. **`hospital-restore-status` showed UTC**: IST now.

## max_locks_per_transaction

The load transaction held **3,794 locks** for a 1,101-relation hospital schema (3.45 per relation;
the precheck estimates 4 per relation). The default lock table is 64 × (100 connections) = 6,400
entries, so one restore uses about 60 % of it while the rest of the platform runs. A dev cluster with
40 connections (2,560 entries) still loaded 3,777 locks (Postgres has slack in shared memory), but that
is not to be relied on. **For P1-T23:** `max_locks_per_transaction = 256` (25,600 entries at 100
connections: a restore with room for a release's migrations beside it); the precheck warns when its
estimate exceeds the lock table, and a load that runs out fails cleanly and rolls back.

## Not proven here

- The quarterly live drill on a synthetic `drill` hospital (the runbook names it; this proof is its
  script).
- Prometheus, Alertmanager and the log alerts were not running (memory): `HospitalRestoreFailed` and
  `ParkedSchemaOverdue` are covered by `scripts/check-logs.py` (fixtures fire and forged lines do
  not) and the static tests.
- E2E's and AWS's buckets: versioning on the file buckets is in `infra/aws` (35 days, `tofu test`
  assertion) and in `setup_buckets.py` for the stand-in; E2E's at go-live (README).
- A busy hospital at 3 years (44 GB): the restore's load is COPY in one transaction; at the proof's
  rate (200,000 entries and 37.8 MB of package in 1.6 s) a 44 GB package would take on the order of
  half an hour, plus the quarantine's own restore (P1-T23 measures it on generated volume).
