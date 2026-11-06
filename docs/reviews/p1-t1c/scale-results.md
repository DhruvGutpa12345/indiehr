# P1-T1c: scale and operations results (2026-11-06)

**Question:** how many hospitals can one database cell hold before operations get hard? (Q-T6, default 100.)

## Method

- **Tool:** `scripts/tenancy-scale-bench.sh` (with the `ihr_bench_scale` command) on a throwaway PostgreSQL 18.6 container.
  - Hardware: the dev machine (i3-12100, 4 cores / 8 threads, 6.7 GB RAM, NVMe).
  - Postgres settings: defaults, so `shared_buffers` is 128 MB.
- **Hospitals:**
  - The first hospital is provisioned for real and loaded with CARE's demo data: 2 facilities, 10 staff, 10 patients, 10 encounters, plus billing, inventory and scheduling.
  - Fillers are copies of it made with django-tenants `clone_schema`, at 2.3–3.6 s each (mean per batch). Provisioning 200 hospitals for real would take about 4 hours here.
  - At each level one more hospital is provisioned for real and timed.
- **Container shared memory:**
  - Levels 1–100 ran on Docker's default 64 MB `/dev/shm`.
  - Growing past about 158 hospitals failed on it (finding 6). Postgres was then restarted on the **same data** with `--shm-size=1g` to finish the 200 level.
  - The dump-time table below was re-measured on that one restarted instance, stepping down from 200, so the comparison across sizes is fair.
- **Single runs, fixed order:** each figure is one run, in a fixed order: no-op, rollback, release, rollback, multiprocessing release. Run-to-run noise looks like about ±5 s (onboarding at 200 came out faster than at 100).
- **Files in this folder:**
  - raw data: `bench-results.jsonl` (levels 1–100 are from the first version of the script, which wrote `dump_mb`) and `dump-same-instance.jsonl`;
  - research context: `research-brief.md`.
- **Not covered:**
  - **Data volume.** Every hospital is demo-sized (an 820 KB dump). These numbers show the cost of the **number** of hospitals, not of a large hospital. Real data sizes come from the P4-T7 restore drill and the pilot.
  - **Real schema history.** Copied schemas are cleaner than real ones: they lack the dropped-column and dead catalogue entries left by about 700 migrations. Catalogue size, dump time and memory per hospital are therefore probably a little **under**-estimated.

## Results

| Hospitals in the cell | 1 | 25 | 50 | 100 | 200 |
|---|---|---|---|---|---|
| Onboarding one new hospital (migrate + roles + value sets) | 65 s | 72 s | 73 s | 82 s | 80 s |
| Catalogue rows: `pg_class` | 1.7 k | 32 k | 63 k | 126 k | 251 k |
| Catalogue size | 16 MB | 103 MB | 193 MB | 367 MB | 723 MB |
| Database size (demo data) | 30 MB | 0.4 GB | 0.8 GB | 1.6 GB | 3.3 GB |
| `migrate_schemas`, nothing to apply | 0.3 s | 7 s | 15 s | 35 s | 89 s |
| One-migration release (`emr` 0083), standard executor | 0.4 s | 13 s | 27 s | 58 s | 136 s |
| Same, multiprocessing executor (2 processes) | 0.5 s | 7 s | 15 s | 32 s | 76 s |
| Rollback of that migration | 0.8 s | 20 s | 42 s | 89 s | 198 s |
| Restore one hospital into an empty database | 1.4 s | 1.4 s | 1.4 s | 1.4 s | 1.4 s |
| Restore in place under a side name: final step only (full path ≈ this + the line above) | 1.4 s | 1.5 s | 1.5 s | 1.6 s | 2.0 s |
| Restored copy identical to the source; source and neighbour untouched | ✅ | ✅ | ✅ | ✅ | ✅ |

### Per-hospital dump (`pg_dump -n`), same instance

| Hospitals | 50 | 100 | 125 | 150 | 175 | 200 |
|---|---|---|---|---|---|---|
| One hospital's dump | 0.9 s | 1.4 s | 1.7 s | **11.8 s** | 13.9 s | 16.0 s |
| All hospitals, one after another (estimate) | 45 s | 2.3 min | 3.5 min | 30 min | 41 min | 53 min |

The dump time steps up between 125 and 150 hospitals. Running ANALYZE, turning off parallel query, raising `work_mem` and turning off JIT all leave it unchanged.

The likely cause is the catalogue outgrowing memory (`shared_buffers` 128 MB and the machine's page cache), so `pg_dump`'s whole-catalogue queries start reading from disk. This is not proven. The step may sit at a different size on production hardware with more memory, so it must be re-measured on staging (P1-T8).

### Memory per database connection

PostgreSQL caches the definition of every table a connection has used and **never frees it** while the connection lives. With schema-per-tenant every hospital has its own copy of every table, so a long-lived app connection grows with every hospital it serves.

| Hospitals one connection has visited | 1 | 10 | 20 | 50 | 100 | 200 |
|---|---|---|---|---|---|---|
| Light use: `count(*)` on 10 main tables (a proxy, not real ORM traffic) | 2.3 MB | 12.8 MB | — | 44 MB | 78 MB | 153 MB |
| Worst case: every table of each hospital | 8 MB | 62 MB | 115 MB | ~270 MB* | ~530 MB* | ~1.06 GB* |

\*Measured up to 20 hospitals, then extrapolated at the last measured step, 5.2 MB per hospital. The step shrank from 6.2 to 5.9 to 5.2 MB as the count grew, so these figures are upper-end estimates.

Real requests sit between the two rows.

## Findings

1. **Onboarding stays about 1–1.5 minutes per hospital** up to 200 hospitals. It is migration-bound, about 700 CARE migrations per new schema. That is fine for the "< 5 min" goal of P1-T5.
2. **Upgrades grow a little faster than the number of hospitals, at every size.**
   - Each doubling makes a release about 2.1–2.3× slower, and a no-op migrate 2.3–2.6×. Per hospital, a release costs 0.50 s at 25 and 0.68 s at 200.
   - This matches the open django-tenants issue #1269: each tenant scans the catalogue several times.
   - At 100 hospitals a release takes about 30–60 s, well inside a maintenance window (Q-T1). Rollbacks measured slower than forward migrations; with one run in a fixed order that comparison is indicative only.
3. **Per-hospital backups hit a step between 125 and 150 hospitals** on this hardware: from 1.7 s to 11.8 s per hospital.
   - Run one after another, nightly dumps of every hospital go from minutes to half an hour or more.
   - Running several dumps in parallel would cut the wall-clock time. Each dump would still read the whole catalogue, so the database load is the same.
4. **Restores work and are exact** at every size, on both paths:
   - into an empty database (for moves and hand-over);
   - in place under a side name (the single-hospital restore runbook).

   "Identical" means a hash of every row of every table, every sequence position, and every index and constraint definition. A negative control confirmed the hash catches a moved sequence, a dropped index and an edited row. The neighbouring hospital, a seeded copy, is unchanged after the in-place restore.

   CARE has no SQL views, functions or triggers that would carry a schema name, so restoring under a renamed schema is safe today. Recheck this if a CARE upgrade adds any.
5. **Connection memory must be managed, at any cap.**
   - At 100 hospitals, one connection holds about 80 MB after light use of every hospital, and up to about 0.5 GB in the worst case.
   - Twenty to thirty such connections would need several GB unless connections are recycled.
   - This does not set the cap, but it sets an operating rule (below).
6. **Two Postgres limits fail at this scale with defaults:**
   - Docker's default 64 MB `/dev/shm` broke parallel catalogue queries at about 158 hospitals ("could not resize shared memory segment"). Cloning and `pg_dump` both failed.
   - The default `max_locks_per_transaction = 64` (about 6,400 lock slots at 100 connections) ran out when one transaction read every table of a few hospitals. In normal operation each request touches one hospital. Bulk jobs that do touch many hospitals in one transaction would hit it: a whole-database logical dump, maintenance scripts, reports across hospitals.

## Recommendation (for your decision)

**Keep the cell cap at 100 hospitals.**
- At 100, every operation measured stays comfortable: release about 30–60 s, nightly per-hospital dumps about 2–3 min, about 1.6 GB of catalogue and demo data.
- The dump step at 125–150 is the nearest cliff. A cap of 100 leaves about 25% headroom below it on this hardware.
- A cell may also close earlier on data volume, which the pilot will tell us.
- **Review the cap** after re-measuring on staging hardware (P1-T8), and again with real data.

Operating rules that come with the cap (to build in P1-T8 staging and record in TENANCY_DESIGN §6.11):
- **Connections:** recycle app and worker database connections with a finite `CONN_MAX_AGE` (a few minutes) and worker recycling. Watch per-backend memory in P1-T9.
- **Postgres settings:**
  - Raise `max_locks_per_transaction` to 256 (research brief §2; it needs a restart).
  - When containerised, give Postgres at least 1 GB of shared memory (`shm_size`).
  - Size `shared_buffers` for a catalogue of roughly 3.6 MB per hospital.
  - Confirm the hosting provider allows all three.
- **Releases:** run `migrate_schemas` with the multiprocessing executor (about 1.8× faster with 2 processes); use more processes on the release machine. Follow django-tenants #1269.
- **Backups:**
  - Run per-hospital dumps as designed, with `--lock-wait-timeout`, and never at the same time as migrations.
  - Re-measure them at every cap review.
