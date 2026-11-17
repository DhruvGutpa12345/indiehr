#!/usr/bin/env python3
"""How much memory one database connection holds after touching k hospitals (P1-T23, L1).

Postgres keeps the table and index definitions of every relation a connection has used, and
never frees them (P1-T1c: about 80 MB per connection at 100 hospitals). This opens one
connection, touches the tables a request typically reads in the first k hospitals (the same
list as ihr_bench_scale), and after each k prints the backend's own memory contexts
(pg_backend_memory_contexts, PG 14+): the total and the cache part. Run it inside a container
that has psycopg and can reach the database, e.g. the backend image:

    DATABASE_URL=postgres://... python scripts/pg-conn-memory.py 1 5 10 25 50

Prints one JSON line per k. Reads only; "bench" or dev data only. Nothing is written.
"""

import json
import os
import sys

import psycopg

TABLES = (
    "emr_patient", "emr_encounter", "emr_observation", "emr_medicationrequest",
    "emr_chargeitem", "emr_invoice", "facility_facility", "users_user",
    "emr_facilitylocation", "emr_questionnaireresponse",
)


def memory(cur):
    cur.execute(
        "SELECT sum(total_bytes), "
        "sum(total_bytes) FILTER (WHERE name IN ('CacheMemoryContext', 'RelCache by OID', "
        "'CatCacheContext', 'index info')) FROM pg_backend_memory_contexts"
    )
    total, cache = cur.fetchone()
    cur.execute("SELECT total_bytes FROM pg_backend_memory_contexts WHERE name = 'CacheMemoryContext'")
    (cache_ctx,) = cur.fetchone()
    return int(total), int(cache_ctx)


def main(levels):
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT schema_name FROM ihr_platform_hospital WHERE schema_name <> 'public' "
            "ORDER BY created_on"
        )
        schemas = [row[0] for row in cur.fetchall()]
        base_total, base_cache = memory(cur)
        print(json.dumps({"k": 0, "hospitals_available": len(schemas), "total_mb": round(base_total / 2**20, 2),
                          "cache_mb": round(base_cache / 2**20, 2)}), flush=True)
        done = 0
        for level in sorted(int(x) for x in levels):
            for schema in schemas[done:level]:
                for table in TABLES:
                    try:
                        cur.execute(f'SELECT * FROM "{schema}"."{table}" LIMIT 1')
                        cur.fetchall()
                    except psycopg.errors.UndefinedTable:
                        pass
            done = min(level, len(schemas))
            total, cache = memory(cur)
            print(json.dumps({
                "k": done, "total_mb": round(total / 2**20, 2), "cache_mb": round(cache / 2**20, 2),
                "mb_per_hospital": round((total - base_total) / 2**20 / done, 2) if done else None,
            }), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:] or ["1", "2"])
