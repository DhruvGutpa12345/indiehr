# P1-T10a1 independent review (2026-11-12)

Three Opus reviewers looked at the built engine, one lens each: correctness, security/tenancy/
operations, and test quality. None of them wrote the code. A separate Opus skeptic then tried to
refute each lens's findings, against the code and the running dev stack. Of 26 findings, 25 were
confirmed or partly confirmed (some duplicated across lenses) and 1 was refuted. All are fixed.

| Finding | Verdict | Fix |
|---|---|---|
| Smoke's terminology check always failed: its count imported Django models outside Django | confirmed, medium (3 lenses) | The manifest is read through `importlib.util.find_spec`, so no models are imported |
| A 30-calls-per-request budget refused long coded forms; CARE cached the refusals 10 s for other users; batch sub-requests shared it | confirmed, medium | Only searches are budgeted; code checks and lookups are point queries with no budget. Running out is an HTTP 429 and a slow query a 503, never an answer CARE could cache. Tested through the API (preview_search over 31 systems → 429) |
| Statement timeout surfaced as a 500 | confirmed, low | `_Timeout` turns a cancelled query into a 503 after rolling back its savepoint. Tested: the outer transaction still works and the setting is restored |
| `--bundled` on each `up` could replace a newer operator-loaded version | confirmed/partly, low | A bundled file is loaded only if that url and sha256 were never loaded. Tested |
| UCUM accepted non-ASCII digits; the grammar path skipped the display check | confirmed, low | ASCII only; a grammar-valid unit needs no display, or its own code as display |
| Display comparison stripped µ, °, symbols and non-Latin, non-Devanagari scripts | confirmed, low | A separate comparison: NFKC, casefold, ASCII punctuation as spaces; every letter and symbol counts; empty is never equal to something else |
| The value set's own displays were ignored for loaded codes | partly, low | Used as the expansion display and accepted by validation |
| Ranking: the exact-term tier never fired; a code in another case beat an exact term; "%" skipped the code match | confirmed, low | Rank: code as typed, exact term, code in another case, term starting with the words, preferred, shorter. Raw code match even when the text has no words. Words counted as typed (limit 12) |
| "Warned once" set: process-wide and, once full, logged every time with text staff chose | confirmed, low | Once full it logs nothing; arguments are cut to 120 characters; recorded as reviewed process-wide state (TENANCY_DESIGN §6.3) |
| Staging loads were not logged; the recorded host was a container id | partly, low | `staging.sh up` hands the "loaded" lines to the host's journal (logger), and so to the log store; the operator string carries user@server |
| The 2 s timeout is per statement, not per request | refuted | Searches are budgeted per request, and each query is bounded |
| Tests: ranking untested, vacuous limit tests, middleware wiring untested, prune could over-delete, `assertNotIn` on objects, UCUM enumerated case, excludes and displays of listed codes, ECL `>>`/bare/`*`, LOINC/ICD gating, positive displayLanguage, inactive in lookup, the FHIRClient refusal test that could have sent a real request | confirmed, low–medium | Each now has a test that fails when the rule is removed. The refusal test patches `requests` and uses a documentation address (192.0.2.1) |

Tests after the fixes: 31 in `ihr_terminology`, all passing. The plugin suites and the
isolation suite were run again; see TRACKER.
