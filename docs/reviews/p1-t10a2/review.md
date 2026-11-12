# P1-T10a2 independent code review (2026-11-12)

Two fresh Opus reviewers who did not write the code: **loader correctness** (rf2.py, the load
command, the bench script) and **search and benchmark** (engine.py, the bench tool). A second
agent then tried to refute each finding. 14 findings: 9 confirmed and 5 partly confirmed, none
refuted. Every one is fixed. The design attack is in design.md (21 of 23 confirmed).

| # | Finding | Verdict | Fix | Proof |
|---|---|---|---|---|
| L1 | No English refset given: GB members were never staged, so the GB display fallback never worked | confirmed, medium | GB English is staged and used whenever no `en` refset is given | test_rf2 (Hindi-only refset) |
| L2 | "Unchanged" ignored the language refsets and the version's status: a rollback or a new refset did nothing | confirmed, medium | the key covers digests and languages; it short-circuits only when that version is active | test_rf2 |
| L3 | sha256 could be empty and was not normalised | confirmed, low | 64 hex characters required, compared in lower case | test_rf2 refusals |
| L4 | Throwaway exemptions depended on the database name alone | confirmed, low | they need `IHR_TERMINOLOGY_THROWAWAY_DB` and a `bench_`/`test_` name; `secure.py` refuses both in deployment | test_secure_settings |
| L5 | Malformed releases gave tracebacks | confirmed, low | decode and zip errors become a clean refusal; encrypted or unusually compressed members are refused | test_rf2 zip safety |
| L6 | The FSN fallback could pick a non-English FSN | partly, low | English first | review of the SQL |
| L7 | Session settings outlived the load inside an outer transaction | partly, low | saved and restored | test_rf2 |
| L8 | The bench's disk sampler was orphaned on failure | confirmed, low | killed in cleanup; it also stops when the container is gone | script review |
| S1 | A value set that selects nothing, searched with words, crashed (500) | partly, medium | `EmptyResultSet` answers empty | test "value sets that select nothing" |
| S2 | The SQL candidate choice left out the exact-term key | partly, low | the SQL orders by every leading key of the ranking | test "candidates chosen in SQL" (cap of 1) |
| S3 | No ANALYZE after a load: run 1 measured tables without statistics (also a production gap) | confirmed, medium | every load and prune ends with ANALYZE; the bench analyses before measuring | bench run 3 |
| S4 | Verdict preconditions were relaxed after run 1 | partly, low | stated plainly in bench-results.md; the owner must accept them | bench-results.md |
| S5 | Timeouts left out of the latency figures; "matches" counted the whole release | confirmed, low | a timeout counts at the 2 s limit; matches are distinct concepts in the measured subtree; nearest-rank p95 | bench run 3 |
| S6 | The tests could not catch regressions in the SQL candidate choice | confirmed, medium | tests with a small cap, empty sets and characters the parser drops | test_terminology |
