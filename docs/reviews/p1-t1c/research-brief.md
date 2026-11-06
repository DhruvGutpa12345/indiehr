# P1-T1c research brief: scaling limits of schema-per-tenant (2026-11-06)

Produced by a 4-agent research workflow (3 web researchers + synthesis). Citations are as found; items marked unverified were not confirmed from primary sources.

**Brief: hospitals-per-cell cap and operational limits (P1-T1c), researched 2026-11-06**

**Recommendation.** Keep the cap at 100 tenants per cell for now (about 108k `pg_class` rows). Treat it as provisional until we measure it on PostgreSQL 18.6. No primary source gives a hard cap. AWS gives no number either; it says schema-per-tenant suits a "moderate number of tenants" [19]. Blog figures of 500–2,000 schemas [23], fewer than 1,000 [28] and 10–50 [27] are not backed by measurements. django-pgschemas says thousands of tenants "might not be feasible" [22].

**Evidence near 100 tenants**
- **Backend memory.** About 589 MiB of CacheMemoryContext at 187k relations, roughly 3.3 KB per relation (my calculation) [1]. Older data (PG 8.2) shows about 10 KB per table [2]. These caches never shrink [3]. A pruning patch existed but it is unconfirmed whether it was committed [4]. Estimate: a pooled backend that cycles through 100 tenants reaches about 350 MB. Cybertec saw about 221 MB in one case (search snippet only, the page returned 403) [5]. Bowen Shi raised eviction for 100k+ tables and long-lived connections (2024-12-04); the reply was not seen [2].
- **pgstat.** About 12 MB per backend at 102k tables [3]. Bossart measured about 16 MB for the hash alone [3]. The PG15 move of statistics into shared memory removed temp-file churn [6].
- **Autovacuum.** Before the PG14 fix, 10k–20k one-page tables took 69–379 s per cycle [7][8]. No data for PG15–18.
- **pg_dump.** `-n` still scans all of `pg_class` in PG18 [39][40]. In 2012, with 100k+ tables, it took 30–60 s per schema regardless of data size [17]. Improvements since then: [41][43]. Schema-only dump of 2.4M relations: 21 s on PG15, 31.6 s on PG18 [16]. That is a regression on 17 and 18, unexplained. No PG15–18 benchmark of `-n` exists.

**Contradiction to resolve.** The django-tenants notes say 108k relations is "below every problem case" (258k–5.2M rows [24][25]). The catalog notes put the measured pain at 10k–100k relations [1][7][17]. Both hold: the migration bugs start later, while backend memory and dump time start earlier. The cap should be set by memory and dump time.

**Operational limits**

1. **Connection reuse**
   - Set a finite `CONN_MAX_AGE` and limit pooler lifetime. Set `idle_session_timeout` [5].
   - PgBouncer transaction pooling is unsafe because `search_path` leaks between tenants [29][32]. Use session pooling only. The `track_extra_parameters` workaround rests on one unverified report [32].
   - Set `TENANT_LIMIT_SET_CALLS=True` [21].
   - The 2016 thread on 70k tables shows the risk of long-lived connections [33].
   - No data on what lifetime keeps the cache bounded, so we must measure it.
2. **`max_locks_per_transaction`**
   - The default is 64 and changing it needs a restart [9]. The lock table holds 64 × (max_connections + prepared) slots, about 6,400 at defaults [10].
   - A full dump takes a lock on every table [11]. 100 tenants × about 100 tables is over 10k locks, so the default is not enough. See the out-of-memory reports at 300k tables [12] and about 400 schemas [47].
   - Raise it to about 256. Since PG18, fast-path slots scale with this setting [14].
   - Managed DigitalOcean and Heroku cannot change it [10][13]. Our provider must allow it.
   - Per-tenant `pg_dump -n` needs only about 100 locks [39]. Lock batching history: [34][44][45].
3. **Migrations**
   - In 4.0.1, `has_table` scans the whole catalog about 4 times per tenant (#1269, open) [24]. Fix PR #1277 is unmerged, and its gains come from an uncontrolled comparison [25]. Patch `has_table` in `ihr_platform` with `to_regclass` [15][24].
   - Use `--executor=subprocess --parallel N` (3.12.0) or recycle multiprocessing workers (4.0.0) [29].
   - Run `check` once, then pass `--skip-checks` [24].
   - Consider the smart executor [30]. A multiprocessing bug exists, linked only by the issues list [31]. Older evidence: [26][35].
   - Provision by cloning: about 1.8 s vs about 10 s on PG12, table count not stated [36][37][38]. No data for about 1,077 relations.
   - Avoid `information_schema` [15]. `search_path` cost is minor for us [20].
4. **Backups**
   - Use physical PITR for the cell [17][19].
   - Take per-tenant `pg_dump -Fc -n` dumps, never at the same time as migrations, using `--lock-wait-timeout`. Under `-j`, a waiting DDL aborts the dump [46].
   - A tenant restore needs the shared public objects and roles first [46][48]. Use `pg_restore --transaction-size=1000` [53][54].
   - There is no supported rename on restore [49][50]. Restore into a scratch database under the original name [51][52].
   - Not verified: whether a later `ALTER SCHEMA … RENAME` is safe given schema names written into CARE's function and view text.
   - Citus can move tenants [55]. Catalog bloat warnings: [18][19][56]. No tenant-aware django-dbbackup [57].

**Unverified or weak points**
- Cybertec details, including the pg_upgrade of 3,500 schemas taking over 48 h and 128 GB, come from search snippets only [5].
- Laurenz Albe's advice to use row-level security instead is unconfirmed [18].
- The memory estimates for 100 and 1,000 tenants are extrapolated.
- Bug #1160 is not linked directly [31].

**Required spike on PG 18.6.** Create N = 25, 50, 100 and 200 CARE schemas. Measure CacheMemoryContext after cycling through all tenants, pgstat memory, autovacuum cycle time, `migrate_schemas` time (with and without the patch), and `pg_dump -n` and restore time and peak memory. Also measure `pg_catalog` size.

**Sources**
[1] https://www.postgresql.org/message-id/f97de758-b816-47db-96e0-0e6e2c2d3eef@gmail.com (2026-09-10)
[2] https://www.postgresql.org/message-id/CAM_vCufVCeDfP3T4uccK-ssY-GkgxAE8Cy8A6sDer2-fm-jF6w@mail.gmail.com (2011-09-28; 2024-12-04)
[3] https://www.postgresql.org/message-id/3nob5fjwrar3shffl5yo7im4qlnxxa475pi6fohemodsvwpl5g@l5cbmz5dqtjm (2026-04-08)
[4] https://postgrespro.com/list/id/20201119.142536.1685839779534143847.horikyota.ntt@gmail.com (2020-11)
[5] https://www.cybertec-postgresql.com/en/too-many-tables-are-bad/ ; https://curatedsql.com/2026/07/02/too-many-tables-and-postgresql/
[6] https://www.percona.com/blog/postgresql-15-stats-collector-gone-whats-new/
[7] https://www.postgresql.org/message-id/CAD21AoBCeoosAP-kixdediHVp-SxRwvgvvUCA0_1nruPFWCJEQ@mail.gmail.com (2020-11-27)
[8] https://www.postgresql.org/message-id/879b33c1-d14c-1cce-6bac-f8ef682f07c9@oss.nttdata.com (2020-12-08)
[9] https://www.postgresql.org/docs/18/runtime-config-locks.html
[10] https://help.heroku.com/EW2G2AF7/how-can-i-resolve-the-error-error-out-of-shared-memory-hint-you-might-need-to-increase-max_locks_per_transaction
[11] https://www.postgrespro.ru/list/id/14513.1088376044@sss.pgh.pa.us
[12] https://www.postgrespro.com/list/thread-id/2519937 (2020-11-30)
[13] https://www.digitalocean.com/community/questions/how-to-backup-a-managed-postgres-database-with-10000-tables
[14] https://postgres.ai/blog/20251008-postgres-marathon-2-004 (2025-10-08)
[15] https://www.postgresql.org/message-id/CABZYQRLY8MOPnpbSFqUPn0Fm2cfJwVmRZ3WGa01kU-9LxRveRg@mail.gmail.com (2017-06-28)
[16] https://www.depesz.com/?p=4362 (2025-05-22)
[17] https://postgrespro.com/list/thread-id/2065578 (2012-11-08)
[18] https://www.postgrespro.com/list/id/CAG5FJBVFp4TtEde-xn2hpYQKByFxPQChXitrLorwyiN71+cPyA@mail.gmail.com ; https://postgrespro.com/list/thread-id/2491298 (2020-05-07)
[19] https://docs.aws.amazon.com/prescriptive-guidance/latest/saas-multitenant-managed-postgresql/matrix.html (undated; also matrix.md)
[20] https://www.postgresql.org/message-id/CAFj8pRAh-dpnajb6tgs1zH2WRzSRKadUC5wnUY%3D9NCp75rkEtw%40mail.gmail.com
[21] https://django-tenants.readthedocs.io/en/latest/use.html
[22] https://django-pgschemas.readthedocs.io/en/stable/overview.html
[23] https://dev.to/software_mvp-factory/postgresql-multitenancy-without-row-level-security-526p
[24] https://github.com/django-tenants/django-tenants/issues/1269 (2026-09-29)
[25] https://github.com/django-tenants/django-tenants/pull/1277 (2026-11-05)
[26] https://github.com/bernardopires/django-tenant-schemas/issues/387 (2016)
[27] https://dev.to/dvoraj75/why-postgresql-row-level-security-is-the-right-approach-to-django-multitenancy-3e1m
[28] https://django-omnitenant.readthedocs.io/en/latest/api/backends/schema_backend/
[29] https://github.com/django-tenants/django-tenants/releases ; https://pypi.org/project/django-tenants/#history
[30] https://pypi.org/project/django-tenants-smart-executor/
[31] https://github.com/django-tenants/django-tenants/issues (#1160)
[32] https://github.com/django-tenants/django-tenants/issues/1112 (2024-12-18)
[33] https://postgrespro.com/list/thread-id/1237216 (2016)
[34] https://postgrespro.com/list/thread-id/2064050 (2012)
[35] https://postgrespro.com/list/thread-id/1491022 (2009-10)
[36] https://dev.to/redhap/tenant-schema-turbocharge-35bf (2020-10-28)
[37] https://github.com/django-tenants/django-tenants/issues/322 (2019-11-25)
[38] https://django-pgschemas.readthedocs.io/en/stable/advanced.html
[39] https://github.com/postgres/postgres/blob/REL_18_STABLE/src/bin/pg_dump/pg_dump.c (commit 9b71ffa061d7)
[40] https://postgrespro.com/list/thread-id/1228292 ; https://www.postgrespro.com/list/id/21114.1370873065@sss.pgh.pa.us (2013-06)
[41] https://postgrespro.com/list/thread-id/1800137 ; https://postgresql.org/message-id/21078.1333211753%40sss.pgh.pa.us (2012-03-31)
[42] https://www.postgrespro.com/list/thread-id/1538179 (about 2013; 248 schemas pushed a backend over 6 GB, cause unknown)
[43] https://www.postgresql.org/docs/release/15.0/ ; /16.0/ ; /17.0/ ; /18.0/
[44] https://www.postgresql.org/message-id/20120612.175425.2118167759512265051.t-ishii%40sraoss.co.jp (2012-06-12)
[45] https://hackorum.dev/topics/30957
[46] https://www.postgresql.org/docs/18/app-pgdump.html
[47] https://postgrespro.ru/list/thread-id/1484507 (2009)
[48] https://folio-org.atlassian.net/wiki/spaces/FOLIJET/pages/1400942
[49] https://postgresql.org/message-id/A4ACB0CF-36A7-4A89-A34E-F3EDA3D3C718%40gmail.com (2018-03-13)
[50] https://hackorum.dev/topics/52780 (2025-11-30)
[51] https://gist.github.com/kuczmama/6c9a76c223e5f73c1c40ab2a782e6b5e
[52] https://www.amazonaws.cn/en/blog-selection/managed-database-backup-and-recovery-in-a-multi-tenant-saas-application/
[53] https://postgresql.org/message-id/3614137.1689443773%40sss.pgh.pa.us (2023-07)
[54] https://postgrespro.com/list/thread-id/2656434 (2023-07)
[55] https://www.citusdata.com/blog/2023/07/31/schema-based-sharding-comes-to-postgres-with-citus/ ; https://learn.microsoft.com/postgresql/citus/api-udf
[56] https://hackorum.dev/topics/129483
[57] https://github.com/jazzband/django-dbbackup/issues/466
