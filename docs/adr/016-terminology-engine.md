# ADR-016: Terminology answered in-process from shared, read-only tables

- **Status:** Proposed (auto-accepted by Claude in an unattended run, 2026-11-12; awaiting
  owner confirmation: Q-F6, Q-F7, Q-F8, Q-F9)
- **Date:** 2026-11-12
- **Decided by:** Claude. The evidence: the §5 rubric scorecard (docs/reviews/p1-t10a/scorecard.md),
  a design attack by two reviewers (security and tenancy; correctness against CARE's code), with
  18 of 23 findings confirmed and folded in, and CARE's terminology code read in full
- **Relates to:** ADR-002 (tenancy), ADR-010 (audit), Q-F1/Q-F1b (curated service now,
  Snowstorm later), TENANCY_DESIGN §5 (amended)

## Context

CARE sends every terminology question to a FHIR server: value-set searches, checks of every coded
field it writes, and code lookups. By default that server is a third party's IP address over
plain HTTP (`SNOWSTORM_DEPLOYMENT_URL`). The query text would leave India unencrypted. Staging
points it at a black hole (`127.0.0.1:9`), so today clinical search fails and coded writes are
refused.

## Decision

1. **Our own engine, in-process** (Q-F6): the shared app `ihr_terminology`.
   - `install()` points CARE's `ResourceManger._fhir_client` at `LocalTerminologyClient`, which
     answers the four calls CARE makes: `ValueSet/$expand`, `ValueSet/$validate-code`,
     `CodeSystem/$lookup` and `CodeSystem` search.
   - `FHIRClient.query` itself refuses, so no CARE code path can still send a query out.
   - The startup check (`settings/secure.py`) now accepts only the black hole.
   - Nothing in `apps/` is edited.
2. **Shared, read-only tables in `public`:** `CodeSystemVersion`, `Concept` (with direct
   parents and every ancestor as arrays) and `Designation`.
   - No hospital table points at them.
   - A statement trigger refuses INSERT, UPDATE and DELETE unless the loader has opened them
     for its own transaction. The loader does that only in `public`.
   - Each load is a new version, switched to active atomically. It records the file's sha256,
     the operator and the host. Older versions are retired, and their rows stay as history after
     `--prune`.
3. **Rules for an answer** (Q-F7), all in `engine.py` and each with a test:
   - **Supported composes:** only the filters CARE's own value sets use (concept is-a,
     descendent-of, =, in, not-in) and the ECL subset `<<`, `<`, `>>`, `>`, a single focus and
     `*`. Anything else is refused as a whole, never widened.
   - **Licence-gated systems:** SNOMED CT, LOINC and ICD answer nothing until their content is
     loaded.
   - **Free systems with no content:** their value sets' own concept lists are offered and
     accepted.
   - **Pinned versions:** a pinned version that is not loaded answers from the active one. CARE
     pins HL7 code systems at 2.0.0, which no current THO carries.
   - **A valid coding** has the value set's system, an active code, and a display that is one of
     the concept's terms, if it has a display at all.
   - **UCUM:** expansion offers the common units; validation accepts any unit well formed from
     them (`{capsule}`, `mg/kg/d`).
   - **Limits:**
     - per call: 100 results, 20 includes, 2,000 listed codes, 100 characters or 8 words of
       search;
     - per web request: 30 calls;
     - per query: a 2 s statement timeout.
4. **Content** (Q-F8). HL7 Terminology 7.5.0's seven code systems that CARE uses, and UCUM's
   common units from FHIR R4 4.0.1, both CC0. They ship in the code with a manifest of sha256s
   and are loaded by `up` in staging and dev (a no-op when unchanged). Licensed content is
   P1-T10b.
5. **Split** (Q-F9).
   - **P1-T10a1:** this engine.
   - **P1-T10a2:** ECL, RF2 loader and a synthetic SNOMED-scale benchmark, which decides Q-F6
     against Snowstorm Lite before SNOMED arrives.
   - **P1-T10b:** licensed content (owner gates).

## Consequences

- Clinical search and coded writes work for the HL7 and UCUM value sets now. SNOMED-based ones
  (conditions, medications, body sites, allergies) answer empty until P1-T10b.
- **Load records.** There is no hospital audit for terminology loads: they are platform data.
  The version rows hold who, when and what, and the load prints one log line. On staging that
  line reaches only the operator's terminal, because `manage` runs without a logging driver.
- **CARE's own tests never reach the engine.** They patch the terminology path and turn
  coded-field validation off for the whole process. Our tests call CARE's classes and API with no
  mock, and call the real validator directly.
- **Read-only POSTs are audited.** CARE's read-only terminology endpoints are POSTs, so the audit
  trail records each one. That was already so with the black hole: it is an `ihr_audit` question,
  not one for this ADR.

## Alternatives

Rejected:

- Snowstorm: it needs Elasticsearch, which does not fit the server.
- Ontoserver: commercial.
- HAPI FHIR: 1–2 GB of RAM.
- FHIRsmith: too young (v1.0.1).

Snowstorm Lite is the runner-up for SNOMED.

## Amendment for P1-T10a2 (2026-11-12, Proposed, awaiting owner confirmation)

Added by the unattended run that built the SNOMED-ready loader and the scale benchmark
(docs/reviews/p1-t10a2/).

- **RF2 releases** (`rf2.py`, `ihr_terminology_load --rf2`):
  - several packages (an edition plus its extensions) load as **one version**;
  - for each id, the row with the latest effectiveTime wins; two different rows with the same id
    and time are refused, and so are relationships or descriptions that point at missing
    concepts;
  - outside throwaway databases a zip, its sha256 and a licence note are required (folders only
    on `bench_*`/`test_*` databases with `IHR_TERMINOLOGY_THROWAWAY_DB` on, which
    `settings/secure.py` refuses in deployment).
- **Languages:** `--language-refset lang:refsetId` maps each language reference set to a
  language (GB English by default). Display and preferred terms come from those sets; the FSN is
  never a preferred term.
- **Search** uses a generated `search_vector` column (`to_tsvector('simple', search_text)`)
  with a GIN index. Each word typed is a prefix of a word of the term. The candidates (at most
  `MAX_CANDIDATES`, one per concept) are chosen in SQL by the same leading keys as the final
  ranking, so the cap never drops a better match. Postgres's 'simple' parser reads both the terms
  and the search, so characters it drops (some marks and punctuation) are dropped on both sides.
- **Statistics:** every load and prune ends with `ANALYZE`. Without it the planner had no
  statistics and run 1 of the benchmark timed out (docs/reviews/p1-t10a2/bench-results.md).
- **One transaction per load.** A SNOMED-sized load takes about 20 minutes, writes about 3 GB of
  WAL and as much in temp files, and holds the version's tables. Hospitals keep answering from
  the active version meanwhile; run it in an off-hours window and check disk first (runbook
  `deploy/staging/README.md`).
- **The benchmark result decides Q-F6** (our engine, or Snowstorm Lite for SNOMED): see
  bench-results.md for the verdict and its preconditions, which the owner must accept.
