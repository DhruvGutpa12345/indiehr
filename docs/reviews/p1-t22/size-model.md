# P1-T22a size model: nightly per-hospital dumps (14-11-2026)

Design: `docs/reviews/p1-t22/design.md`, "After the attack" item 3 (finding A10). Bench:
`scripts/dump-size-bench.sh` (a throwaway Postgres 18.6 container with no network, database
`bench_dumps`, removed afterwards). Synthetic data only. Goes to P1-T23 (capacity).

## Method

- **Source:** one synthetic hospital schema (`pg_dump -Fc -n` of a proof hospital made by
  `ihr_create_tenant`: 1,101 relations, 746 audit entries).
- **Volume:** N audit entries added in a busy hospital's mix (P1-T21's 50,000 entries per busy
  hospital per day): 40 % request entries, 35 % inserts with the whole row, 20 % updates with
  changed columns as `[old, new]`, 5 % deletes. Values vary like real ones (uuids, times, codes,
  numbers, short notes). Loaded with triggers off, then `VACUUM (ANALYZE)`.
- **Two points:** 3,650,000 entries (73 busy days) and 10,950,000 (219 busy days), to check that
  sizes and speeds are linear before extrapolating to 1 and 3 years.
- **Machine:** this dev machine (Intel i3-12100, 8 threads, 6.7 GB RAM, ZFS on NVMe), the bench
  container limited to 1.5 GB, nothing else running. A server's figures will differ; the
  dumper's own metrics (`ihr_dump_largest_seconds`, `_largest_bytes`, `_last_run_seconds`)
  measure them in place.
- **Assumption:** the audit trail is the bulk of a busy hospital's schema (each entry holds the
  changed row's image). Clinical tables add to it; they are not modelled here.

## Measured

| | 3.65 M entries | 10.95 M entries |
|---|---|---|
| On disk (table + TOAST + indexes) | 2.96 GB, **810.6 B/entry** (heap 710, indexes 100) | 8.89 GB, **811.5 B/entry** |
| Average row (`pg_column_size`) / its `data` | 688 B / 353 B | 688 B / 354 B |
| `pg_dump -Fc` gzip (the default) | 476 MB in 31.8 s | 1.43 GB in 94.4 s |
| `pg_dump -Fc` **zstd** (the dumper's) | **448 MB in 9.0 s** (122.8 B/entry; 405k entries/s; 0.152 of the disk bytes) | **1.34 GB in 26.9 s** (122.6 B/entry; 407k entries/s) |
| `pg_dump -Fc` lz4 | 823 MB in 8.9 s | 2.46 GB in 32.1 s |
| `pg_restore --list` | 0.05 s | 0.06 s |
| Schema-only restore (the nightly check) | 1.5 s | 1.5 s (depends on the relation count, not the data) |
| Full restore, `--single-transaction`, zstd dump | **36.4 s** (100k entries/s) | **141.5 s** (77k entries/s: index builds grow faster than linear) |
| Full restore, gzip dump | 34.7 s | 121.8 s |
| Sealing (Fernet, 1 MiB tokens; Python, one core) | 476 MB in 3.0 s (157 MB/s); sealed/plain **1.3334** | 1.43 GB in 10.1 s (142 MB/s); 1.3334 |
| Opening (decrypt) / SHA-256 | 1.7 s / 0.34 s | 5.8 s / 1.3 s |

zstd was chosen: as small as gzip (6 % smaller) at 3.5 times its speed; lz4 is no faster here
and 1.8 times larger. Sealing runs in the pipe from `pg_dump` and keeps up with it (zstd writes
about 50 MB/s; the sealer takes 140 MB/s or more).

Live, on the staging proof (docs/reviews/p1-t22/proof-a.md): a hospital with 500,745 entries
(358 MB on disk) dumped and sealed in 1.6 s (55.7 MB zstd, 74.2 MB sealed, 0.207 of the disk
bytes) and restored in full in 5.1 s.

## Per busy hospital (50,000 entries a day)

| | 1 year (18.25 M entries) | 3 years (54.75 M entries) |
|---|---|---|
| Audit trail on disk | **14.8 GB** | **44.4 GB** |
| One dump, zstd | 2.24 GB | 6.72 GB |
| One dump, sealed (what is uploaded and read back) | **3.0 GB** | **9.0 GB** |
| `pg_dump` and sealing | about 45 s | about 2 min 15 s |
| Full restore (P1-T22b; at the slower 77k entries/s) | about 4 min | about 12 min (more as index builds grow; plan for 15) |
| Held in the dumps bucket: steady state with daily expiry (about 18 dumps) | 54 GB | 161 GB |
| Held just before the runbook's **weekly** expiry (about 25 dumps: 19 nights, 6 Sundays) | **75 GB** | **225 GB** |

Sealed size per byte on disk: 0.20 (both points). The dumper's disk check uses **0.30** of a
schema's bytes on disk, or its last sealed dump plus a fifth when that is larger, plus 512 MiB
(`dumper.py`: `SEALED_PER_DISK_BYTE`, `DISK_MARGIN`). The read-back streams (fixer, review C2):
a dump needs its sealed copy on the work volume and no second one; the night's check, after the
dumps, copies one dump at a time.

## What it sets

- **Compression:** zstd (`IHR_DUMP_COMPRESS`, the default).
- **Parallelism: 2** (`IHR_DUMP_PARALLEL`). Each dump keeps about one core of Postgres
  (`pg_dump`) and one of the dumper (sealing) busy; two at a time leaves the database's other
  cores for the 04:00 audit watch and early users. More would not help once the link to the
  bucket is the limit (below).
- **Window:** 03:30 to 06:00 IST, 2.5 hours (alert DumpRunOverBudget). The dumping itself is
  small: 100 busy hospitals at 3 years are about 3.75 hours of `pg_dump` at one at a time, under
  2 hours at two.
- **The real limit is transfer.** Each night every sealed dump crosses the link three times: the
  upload, the read-back check, and the night's schema-only check (which downloads it again). At
  an assumed 25 MB/s to the backup region, 2.5 hours move about 225 GB, so about **75 GB sealed
  a night: 25 busy hospitals at 1 year, or 8 at 3 years**. At 100 MB/s, four times that.
- **Work volume (`dumpstate`):** room for two of the largest sealed dumps at once, each with a
  fifth more and 512 MiB: about **8.2 GB** for busy hospitals at 1 year (2 × (3.6 + 0.5)),
  **22.6 GB** at 3 years (2 × (10.8 + 0.5)). The dumper exports that need
  (`ihr_dump_work_need_bytes`, from the largest newest dump in the bucket); DumpDiskLow fires
  below it. Below one dump's need a hospital is refused (`reason=disk`); between the two, dumps
  wait for each other.
- **Bucket:** 14 nights and 4 Sundays are about 18 copies per hospital with daily expiry (54 GB
  per busy hospital at 1 year, 161 GB at 3 years). The runbook's expiry is weekly (it needs the
  admin key on the owner's machine), so just before it the bucket holds nights up to 21 days and
  Sundays up to 35: about **25 copies, 75 GB at 1 year and 225 GB at 3 years**. Size for that
  peak. DumpErasureOverdue fires once the oldest dump passes 36 days (a missed weekly run), which
  bounds it; without that alert a missed month would reach about 46 copies.

## For P1-T23 (capacity)

1. **Measure the link** to the backup region from the real server (sealed MB/s up and down);
   it decides how many busy hospitals fit the window.
2. **Do not download a dump twice:** the night's schema-only check can restore from the copy
   already read back (same SHA-256), so each dump crosses the link twice, not three times.
3. **Incremental audit dumps:** the audit trail is append-only and sealed in ranges, so the
   nightly dump could carry only new seals' entries plus a weekly full one. That cuts nightly
   transfer and the bucket from linear in the trail's age to about one day's growth (about
   8 MB sealed a day per busy hospital). Needed before busy hospitals pass about a year, or when
   a run passes half the window.
4. **`max_locks_per_transaction`** for a single-schema `--single-transaction` restore: 1,101
   relations restored with the server default (64 × connections) here; P1-T22b measures it on
   the live cluster's settings.
5. **The bucket's size:** plan for the weekly-expiry peak (about 25 copies per hospital, above)
   plus headroom, not the 18 of the steady state; DumpErasureOverdue at 36 days keeps it there.
6. **Clinical data** on top of the audit trail: re-run `scripts/dump-size-bench.sh` with a real
   pilot hospital's (synthetic) shape once one exists.
