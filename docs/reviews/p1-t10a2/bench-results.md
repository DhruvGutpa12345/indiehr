# P1-T10a2 benchmark results: our engine at SNOMED scale (decides Q-F6)

Synthetic release (`ihr_terminology_bench generate`; made-up ids and words, never SNOMED content):

- **Edition:** 520,000 concepts (370,000 active and 150,000 inactive), 1,558,529 descriptions,
  489,729 is-a rows.
- **Drug extension** (a second package, loaded with the edition as one version): 100,000
  concepts, 300,000 descriptions, 124,833 is-a rows.
- **Total:** 620,000 concepts, 1,858,529 descriptions, 614,562 is-a rows, 7.6M+ ancestor pairs,
  depth up to 17.
- **Targets, stated plainly:**
  - The 1.9M description target was set by Claude (the design attack said "about 2M").
  - The generator checked strictly and printed "not calibrated": descriptions came out 2.2 %
    short.
  - The attack's finding asked for a verdict only within "about 5 %". The generator applies that
    tolerance only from now on.
  - Run 1 therefore does **not** meet the strict preconditions the script stated: it was not
    calibrated, and the host swapped (see below). Its numbers are reported for information. A
    verdict needs the owner to accept those conditions, or a run that meets them.
- **Subtrees measured:** the large clinical branch has 122,260 concepts. The drug subtree has
  113,681 (the edition's drugs plus the extension).

**Environment:**
- Postgres 18.6 (`postgres:18.6-alpine`) with a 1.5 GB limit and shared_buffers 512 MB, as on
  staging.
- The loader and the measuring process are each limited to 1 GB.
- On this 6.7 GB machine, with the dev stack stopped.
- Single client unless noted; warm cache; 200 timed samples per shape after 5 warm-up calls
  (run 2: 50).

## Run 1 (2026-11-12, 19:00–19:45 IST), engine as built in P1-T10a2

**The load:**

| Measure | Result | Limit | |
|---|---|---|---|
| Load, end to end (two packages, one transaction) | 1,258 s (21 min) | ≤ 30 min | ✅ |
| Postgres peak memory / OOM kills | 714 MB / 0 | within 1,536 MB | ✅ |
| Loader peak memory | 161 MB | ≤ 1 GB | ✅ |
| Disk of one loaded version (tables + indexes) | 0.96 GB | ≤ 3 GB | ✅ |
| WAL written / temp files / data directory peak | 2,968 MB / 2,899 MB / 1,728 MB | informative (runbook) | |
| Host swap during load / during measure | 1,270 / 856 pages (about 5 MB / 3 MB) | none: the script header calls such a run invalid | ❌ precondition |

**Queries:**

| Query shape (what the web app sends) | p50 | p95 | max | timeouts | median matches | Limit (p95) | |
|---|---|---|---|---|---|---|---|
| 2–3 words of one real term, 3–6 letters each | 180 ms | **361 ms** | 1,417 ms | **2 of 201** | 10 | ≤ 300 ms | ❌ |
| one word, 3 letters | 295 ms | 502 ms | 524 ms | 0 | 36,981 | ≤ 1 s | ✅ |
| one letter (first keystroke) | 517 ms | 653 ms | 832 ms | 0 | 138,240 | ≤ 1 s, no timeout | ✅ |
| two letters | 484 ms | 557 ms | 667 ms | 0 | 170,973 | ≤ 1 s, no timeout | ✅ |
| picker opened (empty) | 45 ms | 54 ms | 60 ms | 0 | — | ≤ 500 ms | ✅ |
| medication picker opened (`" clinical drug"`) | 343 ms | 390 ms | 477 ms | 0 | 121,632 | ≤ 500 ms | ✅ |
| medication, first letter + " clinical drug" | 297 ms | 458 ms | 541 ms | 0 | 19,258 | ≤ 500 ms | ✅ |
| medication, a word + " clinical drug" | 193 ms | 264 ms | 268 ms | 0 | 86 | ≤ 500 ms | ✅ |
| `$validate-code` | 2.1 ms | 2.3 ms | 7.4 ms | 0 | — | ≤ 20 ms | ✅ |
| `$lookup` | 2.8 ms | 3.1 ms | 3.6 ms | 0 | — | ≤ 50 ms | ✅ |
| 4 clients at once, mixed shapes | 619 ms | 927 ms | 1,057 ms | 0 | — | (informative) | |

The backend process peaked at 168 MB and grew 6 MB during the measuring run.

**Verdict of run 1: one criterion failed.** The typical multi-word search missed its p95 (361 ms
against 300 ms), and 2 of 201 such searches hit the 2 s statement timeout, so the user would see
an error. Everything else passed, most with room to spare. By the rule fixed before measuring,
this alone would mean **Snowstorm Lite for SNOMED (Q-F6 B)**.

The slow case is narrow: few matches (median 10), but two prefixes that are each very common.
That points at the query plan, not at the data model.

**The independent review found a likelier cause first.** The loader never ran `ANALYZE`, so
run 1 measured tables without planner statistics. That is also a production gap: the loader now
analyses after every load. Two caveats about run 1's numbers:

- the timed-out calls were left out of the latency statistics;
- the "median matches" column counted matches in the whole release, not in the measured
  subtree.

**What comes next:**
1. The next measurement uses the same engine on an analysed release, before any plan tuning is
   credited. The pass/fail limits do not change.
2. Then any tuning is measured separately.

## Run 2 (2026-11-12, ending 20:17 IST): same engine, statistics arriving part-way

The loader now ran ANALYZE, but this run measured a release loaded by run 1's loader, kept with
`KEEP=1`. Autovacuum analysed the tables during the run. 50 samples per shape. The load itself was
repeated once with the new loader: 1,324 s, Postgres peak 683 MB, host swap 325 pages.

- Multi-word search: p95 **177 ms**, max 1,018 ms, no timeouts.
- One word of 3 letters: **25 timeouts**, all before autovacuum's analyse.
- Everything else as in run 1, within its limits.

Run 2 shows that statistics matter. It is not a verdict.

## Run 3 (2026-11-12, 20:20–20:28 IST): the shipped engine, analysed, with corrected accounting

Changes since run 1, all from the review (review.md):
- the release is analysed explicitly before measuring (the script prints `last_analyze`);
- a timed-out call counts at the 2 s limit;
- "matching concepts" are distinct concepts inside the measured subtree;
- p95 is nearest-rank;
- each run says whether it is **valid** (no host swap, calibrated release).

| Query shape | p50 | p95 | max | timeouts | matching concepts (median) | Limit (p95) | |
|---|---|---|---|---|---|---|---|
| 2–3 words of one real term | 187 ms | **326 ms** | 560 ms | 0 | 1 | ≤ 300 ms | ❌ |
| one word, 3 letters | 305 ms | 537 ms | 567 ms | 0 | 1,423 | ≤ 1 s | ✅ |
| one letter | 550 ms | 668 ms | 740 ms | 0 | 5,890 | ≤ 1 s, no timeout | ✅ |
| two letters | 470 ms | 566 ms | 695 ms | 0 | 15,817 | ≤ 1 s, no timeout | ✅ |
| picker opened (empty) | 44 ms | 50 ms | 59 ms | 0 | — | ≤ 500 ms | ✅ |
| medication picker opened | 344 ms | 363 ms | 422 ms | 0 | 113,681 | ≤ 500 ms | ✅ |
| medication, first letter | 300 ms | 457 ms | 547 ms | 0 | 9,633 | ≤ 500 ms | ✅ |
| medication, a word | 189 ms | 268 ms | 334 ms | 0 | 38 | ≤ 500 ms | ✅ |
| `$validate-code` | 2.2 ms | 2.6 ms | 3.1 ms | 0 | — | ≤ 20 ms | ✅ |
| `$lookup` | 2.9 ms | 3.3 ms | 3.6 ms | 0 | — | ≤ 50 ms | ✅ |
| 4 clients, mixed | 646 ms | 986 ms | 1,062 ms | 0 | — | (informative) | |

Host swap during measuring: 430 pages, so **not valid** by the script's own rule. Disk 0.96 GB;
backend peak 180 MB.

**Run 3 fails one limit:** multi-word search p95 326 ms against 300 ms. Even with one matching
concept, every search cost about 180 ms.

**Cause, found with a profile, not guessed.** The SQL candidate query took 0.4 ms (EXPLAIN
ANALYZE). The remaining time was one query: before the word search, `expand()` looks up the code
as typed in any case (`code__iexact`, the first ranking key). That is `UPPER(code) = UPPER(…)`,
which no index served. Postgres therefore read every concept of the value set: 171 ms of a 177 ms
search. A probe expression index on `(system, UPPER(code))` brought that query from 183 ms to
0.1 ms.

## Run 4 (2026-11-12, 20:30–20:35 IST): tuned, with one expression index

The only change is migration 0005 (`ihr_term_code_upper`, built in under a second at 620,000
concepts). The ranking, the SQL and the limits are unchanged.

| Query shape | p50 | p95 | max | timeouts | matching concepts (median) | Limit (p95) | |
|---|---|---|---|---|---|---|---|
| 2–3 words of one real term | 14 ms | **127 ms** | 357 ms | 0 | 3 | ≤ 300 ms | ✅ |
| one word, 3 letters | 92 ms | 325 ms | 390 ms | 0 | 8,133 | ≤ 1 s | ✅ |
| one letter | 366 ms | 485 ms | 718 ms | 0 | 7,781 | ≤ 1 s, no timeout | ✅ |
| two letters | 291 ms | 415 ms | 495 ms | 0 | 7,607 | ≤ 1 s, no timeout | ✅ |
| picker opened (empty) | 45 ms | 57 ms | 86 ms | 0 | — | ≤ 500 ms | ✅ |
| medication picker opened | 191 ms | 215 ms | 270 ms | 0 | 113,681 | ≤ 500 ms | ✅ |
| medication, first letter | 137 ms | 245 ms | 311 ms | 0 | 26,400 | ≤ 500 ms | ✅ |
| medication, a word | 30 ms | 111 ms | 179 ms | 0 | 48 | ≤ 500 ms | ✅ |
| `$validate-code` | 2.5 ms | 3.5 ms | 4.3 ms | 0 | — | ≤ 20 ms | ✅ |
| `$lookup` | 3.2 ms | 4.1 ms | 5.6 ms | 0 | — | ≤ 50 ms | ✅ |
| 4 clients, mixed | 419 ms | 976 ms | 1,255 ms | 0 | — | (informative) | |

Disk 0.99 GB; backend peak 169 MB (grew 5 MB). Host swap: **3 pages** (12 KB). Run 4 also shared
the machine with 44 read-only review agents (load average about 4–5). It is therefore **not valid**
by the script's strict rule either, though the conditions were harsher than intended, not
kinder.

(The "matching concepts" medians differ between runs because the sampled words are random per run;
they describe each run's own samples.)

## Run 5 (2026-11-12, 20:37–20:42 IST): the same, on a quiet machine

The same tuned engine and database as run 4, with nothing else running.

| Query shape | p50 | p95 | max | timeouts | Limit (p95) | |
|---|---|---|---|---|---|---|
| 2–3 words of one real term | 13 ms | **145 ms** | 364 ms | 0 | ≤ 300 ms | ✅ |
| one word, 3 letters | 112 ms | 333 ms | 363 ms | 0 | ≤ 1 s | ✅ |
| one letter | 358 ms | 461 ms | 539 ms | 0 | ≤ 1 s, no timeout | ✅ |
| two letters | 291 ms | 371 ms | 426 ms | 0 | ≤ 1 s, no timeout | ✅ |
| picker opened (empty) | 44 ms | 51 ms | 54 ms | 0 | ≤ 500 ms | ✅ |
| medication picker opened | 185 ms | 189 ms | 257 ms | 0 | ≤ 500 ms | ✅ |
| medication, first letter | 131 ms | 253 ms | 317 ms | 0 | ≤ 500 ms | ✅ |
| medication, a word | 28 ms | 108 ms | 128 ms | 0 | ≤ 500 ms | ✅ |
| `$validate-code` | 2.4 ms | 2.9 ms | 3.3 ms | 0 | ≤ 20 ms | ✅ |
| `$lookup` | 3.0 ms | 3.6 ms | 4.0 ms | 0 | ≤ 50 ms | ✅ |
| 4 clients, mixed | — | — | — | — | (informative) | ❌ harness |

Host swap: 59 pages (about 240 KB), so **not valid** by the strict rule.

**The 4-client shape failed, and the tool hid it.** One client thread died with Postgres's
"could not resize shared memory segment … No space left on device". It printed 181 samples instead
of 200, with no error in the figures. The cause: the bench container had Docker's default 64 MB
`/dev/shm`, while staging gives Postgres 256 MB (`deploy/staging/compose.yaml`, `shm_size`). A
parallel query under 4 clients needed more than 64 MB. Two fixes:

- the bench now runs Postgres with `--shm-size=256m`, as staging does;
- a client that fails now fails the whole run ("no figures from this run"), never a shorter
  sample.

Runs 1–4 had no such error (their logs were checked).
