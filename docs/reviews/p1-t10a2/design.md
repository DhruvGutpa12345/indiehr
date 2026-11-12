# P1-T10a2 design: SNOMED-ready terminology engine and the benchmark that decides Q-F6
(draft, then the attack's changes; 2026-11-12)

## What is already there (P1-T10a1, ADR-016)

The data model already holds direct parents and transitive ancestors as arrays (GIN). The ECL
subset CARE uses (`<<`, `<`, `>>`, `>`, a single focus, `*`) is implemented and tested. The FHIR
loader reads whole JSON files, up to 64 MB. Search filters designations with
`LIKE 'w%' OR LIKE '% w%'` per word, which has no index: fine for 2,400 codes, untested at
SNOMED scale. P1-T10a2 makes the engine SNOMED-ready and measures it **before** any licensed
content exists (P1-T10b).

## Pass/fail criteria for Q-F6, fixed before measuring

The benchmark runs a synthetic SNOMED-sized release on a server-shaped Postgres: 18.6, 1.5 GB
memory limit, shared_buffers 384 MB (as staging, `IHR_PG_MEMORY`). Our engine stays (Q-F6
confirmed by the numbers) only if **all** of these hold. Otherwise the run recommends
Snowstorm Lite for SNOMED (Q-F6 B) and records why.

| Measure | Limit |
|---|---|
| Load of 370k concepts / 1.5M descriptions / 600k is-a rows, end to end | ≤ 30 min, peak Postgres memory within its limit (no OOM), loader process ≤ 1 GB RSS |
| Disk for one loaded version (tables + indexes) | ≤ 3 GB |
| `$expand` under `is-a` of a 120k-concept subtree, 2+ words of 3+ letters | p95 ≤ 300 ms |
| … one word of 1–2 letters (the picker's first keystrokes) | p95 ≤ 1 s, never a timeout |
| … empty search (picker opened) | p95 ≤ 500 ms |
| … with CARE's medication suffix `<word> clinical drug` under `<< root` | p95 ≤ 500 ms |
| `$validate-code` (point query) | p95 ≤ 20 ms |
| `$lookup` | p95 ≤ 50 ms |

Each query shape is measured 200 times with random words from the synthetic vocabulary, warm
cache, single client, and once under 4 concurrent clients. The numbers go to
`docs/reviews/p1-t10a2/bench-results.md`.

## Changes

1. **Word-prefix search with an index.**
   - A new column `Designation.search_vector`: `to_tsvector('simple', search_text)`, with a GIN
     index.
   - A search becomes `search_vector @@ to_tsquery('simple', 'w1:* & w2:*')`.
     - Every word must prefix-match a word of the same designation, as today.
     - The words are already letters, digits and marks only (`text.normalise`), so they need no
       tsquery escaping. They are still passed as a bound parameter.
   - Ranking stays in Python over at most 5,000 candidates. These are fetched ordered by
     `preferred DESC, length(search_text)`, so the best ones are among them.
   - Migration 0003 adds the column and index, and fills the column for content already loaded.
     Inside its own transaction, it sets the loader's guard flag for that.
2. **RF2 loader: `ihr_terminology_load --rf2 <release.zip | snapshot dir> [--sha256 …]
   --operator … [--language-refset 900000000000508004] [--module …]`.** It reads the Snapshot
   files only:
   - Concept, for codes and the active flag;
   - Description, for FSNs and synonyms;
   - Relationship, for the active is-a rows (`typeId 116680003`);
   - the Language refset, for preferred and acceptable terms.

   How it handles a release:
   - **Zip:** a release zip is read member by member with `zipfile`, never extracted. Absolute
     paths, `..`, symlinks, members over 2 GB and more than 10,000 members are refused. The zip's
     sha256 is checked first when given.
   - **Streaming:** files are streamed line by line in Python. Columns are checked against RF2's
     headers. `search_text` is computed in Python with the same `normalise` that answers searches.
   - **Staging tables:** rows go into temporary staging tables by `COPY`. Concepts, designations
     and parents are written by set-based SQL.
   - **Ancestors** are computed level by level in SQL: a frontier joined to direct parents,
     about 20 rounds for SNOMED's depth. Never one recursive CTE, never a Python closure of the
     whole graph. A cycle is detected by a round limit (100).
   - **Display** is the preferred synonym in the chosen language refset (GB English by
     default), falling back to the FSN.
   - **Inactive concepts** are loaded inactive, so `$lookup` still finds them. Inactive
     descriptions and relationships are skipped.
   - As before, one transaction loads a `loading` version, checks the counts, then switches it
     to `active`. `work_mem` and `maintenance_work_mem` are set explicitly for the loader's
     session.
3. **Synthetic release generator** (`ihr_terminology_bench generate --concepts 370000 --out
   <dir>`).
   - It writes RF2-shaped files with made-up ids and words: never SNOMED content.
   - The hierarchy is a DAG with SNOMED-like shape: one root and about 19 top-level branches,
     depth up to about 18, 1–3 parents each (80/17/3 %).
   - Each concept gets an FSN with a semantic tag in brackets, plus 1–4 synonyms (about 4 per
     concept), and a GB language refset.
4. **Benchmark** (`scripts/terminology-scale-bench.sh`).
   - It uses a throwaway Postgres 18.6 container (`ihr-bench-tx`) with staging's memory limit,
     and a database that must be named `bench_*`. The command refuses any other.
   - It migrates the shared schema, then generates, loads and measures, and finally removes the
     container (`KEEP=1` keeps it).
   - It reports peak memory from the container's cgroup, and peak loader RSS.
5. **Tests** (no licensed content):
   - A hand-made RF2 fixture of about 30 concepts, covering: an inactive concept, a concept with
     two parents, an inactive is-a row, a preferred synonym in GB, an FSN-only concept, and a
     description with Devanagari text.
   - Loader refusals: a zip with `../x`, a wrong sha256, a missing file, wrong headers, a cycle.
   - Search semantics unchanged: all 31 P1-T10a1 tests still pass on the tsquery search.

## Out of scope

- Real SNOMED, LOINC or ICD content (P1-T10b).
- ECL beyond the subset CARE uses: refinements, AND/OR/MINUS, member-of.
- Delta releases. A new release is loaded as a new version.

---

## After the design attack (2026-11-12): 21 of 23 findings confirmed or partly, all folded in

| Finding | Change |
|---|---|
| Opening the medication picker (`" clinical drug"` with an empty box) is the heaviest real query, unmeasured | New shapes `medication_opened`, `medication_1_letter` and `medication_word`, under a drug subtree the size of the edition's drugs plus the India Drug Extension (about 110k) |
| Two-word queries mostly matched nothing | Searches are now built from 2–3 words of one real preferred term, each cut to 3–6 letters. Every shape reports its median match count |
| The synthetic release was smaller and simpler than the real one | Calibrated to 370k active + 150k inactive concepts in the edition and 100k drugs in a second package. Long drug names have strengths and units. The target is 600k+ is-a rows and about 2M designations, and the run gives no verdict if the targets are not met |
| India's content ships as separate packages | `--rf2` repeats. All packages are staged together; the latest row per id wins; same-time conflicts are refused; descriptions or is-a rows pointing at missing concepts are refused (an extension needs its edition); counts are reported per module |
| One language refset only | `--language-refset <lang>:<id>`, repeated, in priority order. A designation is preferred only by a refset of its own language. The display is taken from the English refsets in order |
| Candidate order differed from ranking; FSNs marked preferred | SQL picks one designation per concept by the ranking's leading keys (`DISTINCT ON`), at most 1,000. FSNs are never preferred |
| Memory not readable from the measures | The bench now records cgroup OOM kills, the loader's peak RSS (1 GB container limit), the backend's RSS growth, WAL and temp bytes, the data directory's peak size, and host swap (a run where the host swapped is invalid). Timeouts are counted per shape |
| The load's effect on production | Concepts are written once, with ids assigned first. WAL, temp and peak disk are reported. ADR-016 records that one load holds a transaction (audit sealing and vacuum wait), so loads go in an off-hours IST window |
| `prune` loaded every retired concept into Python | `prune` now uses plain SQL deletes |
| Round limit did not detect cycles | A cycle is detected by a concept being its own ancestor; the round limit is only a backstop |
| The bench could not run cleanly next to the dev stack | The bench refuses to start while the dev stack runs. It uses its own network and keeps the release on disk (`~/.cache/ihr-bench`), not tmpfs |
| Synthetic release indistinguishable from SNOMED | It carries a made-up module id. Outside bench and test databases the loader refuses that module, refuses folders, and needs each zip's sha256 and a licence reference |
| Line cap applied after reading the line | Lines are read in chunks of at most 64 KiB + 1 |
| `search_vector` filled by three writers, and migration 0003 opened the guard | It is now a generated stored column (`GeneratedField`). Postgres keeps it; nothing writes it, and the migration needs no guard opening |
| Words the parser cannot read alone | Query words now come from Postgres's own `to_tsvector('simple', …)`, the same parser that built the vectors. ADR-016 notes that tokenisation depends on the image's parser |
| No designed way to load the licensed release on staging | Moved to P1-T10b: `scripts/staging.sh terminology-load <zip> --sha256 … --licence …`, with a read-only mount from outside the repo, a memory limit, journal lines, a free-disk check and an off-hours window |

Refuted: 2 (staging-shape differences beyond memory, and the bench_* guard on the load step,
since the loader now refuses the synthetic module itself).
