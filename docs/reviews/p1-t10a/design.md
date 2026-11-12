# P1-T10a design: the terminology service engine (draft for attack, 2026-11-12)

P1-T10 is split by Claude (2026-11-12, awaiting owner confirmation):

- **P1-T10a (this): the engine and the free content.** CARE's terminology calls are answered from
  our own tables, in the same process, with nothing sent outside. The service ships with the code
  systems that are free to redistribute.
- **P1-T10b (blocked on the owner): licensed content.**
  - SNOMED CT India: the affiliate licence through MLDS India and NRCeS, plus written permission
    for SaaS use.
  - LOINC: the owner's account and acceptance of its terms.
  - The ICD-10 or ICD-11 choice (WHO licence).
  - A drug list (the India drug extension, or CDSCO).
  - Hindi designations, which go to clinical review.

## What CARE asks for (apps/backend/care/emr/fhir/)

`ResourceManger._fhir_client` (a class attribute: `FHIRClient(SNOWSTORM_DEPLOYMENT_URL)`) is
called with `query(method, resource, parameters)`. It receives exactly four operations:

| Call | Used by | Returns |
|---|---|---|
| `POST ValueSet/$expand`: Parameters `filter`, `count`, `displayLanguage`, `includeDesignations`, `valueSet` with inline `compose.include/exclude` for one system | `ValueSet.search`, `CareValueset.search`, the `/expand` and `/preview_search` APIs | `expansion.contains[]` of `system, code, display, designation[]` |
| `POST ValueSet/$validate-code`: `valueSet` (inline compose), `coding` | `ValueSet.lookup`, so every coded field CARE validates on write (`validate_valueset`) | `parameter[name=result].valueBoolean` |
| `GET CodeSystem/$lookup`: `system`, `code`, `property=*` | the `/lookup_code` API | FHIR Parameters (name, display, designation parts, property parts) |
| `GET CodeSystem`: `name`, `url` | `CodeSystemResource` (no caller in CARE today) | a Bundle |

What the composes contain, from CARE's 17 system value sets (counted 2026-11-12):

- **SNOMED (32 includes):**
  - `filter: concept is-a <id>` (27);
  - `constraint = "< id"` or `"<< id |term|"` (ECL descendants, descendants-or-self; 2);
  - enumerated `concept` lists.
- **HL7 code systems:** v2-0074, v2-0487, v2-0493, v2-0916, v3-ObservationInterpretation,
  service-type and container-cap.
- **UCUM and LOINC:** whole-system includes.

Today staging points `SNOWSTORM_DEPLOYMENT_URL` at a black hole (`127.0.0.1:9`), so clinical
search fails and coded writes are refused.

## Choice (§5 rubric, docs/reviews/p1-t10a/scorecard.md)

The choice is our own engine, a plugin over Postgres, answering in-process.

- **Snowstorm.** It needs Elasticsearch, which does not fit the 8 GB server.
- **Snowstorm Lite.** Apache-2.0, about 500 MB, a good engine. But it is SNOMED-only, so the
  HL7, UCUM and LOINC systems would still need something else. It is the runner-up for SNOMED
  if our engine turns out too slow at full SNOMED size.
- **HAPI FHIR JPA.** A 1–2 GB JVM, and slow terminology loads.
- **FHIRsmith.** BSD-3, covers every system, but v1.0.1 was released today, it has few
  maintainers, and its RAM use is unknown.

Our own engine adds no process, no network hop and no new image. Tenancy, backups and the audit
setup stay as they are, and it holds only what CARE needs.

## Design

1. **App `ihr_terminology`**, a shared app in the `public` schema next to the PIN directory.
   - Terminology is the same for every hospital.
   - No hospital table has a foreign key into it: CARE stores codes as JSON.
   - It is read-only at run time. Only the loader command writes, run by the platform operator.
2. **Models:**
   - **`CodeSystemVersion`:** url, version, name, title, licence note, source file and its
     sha256, loaded_at, concept count, status (`loading` / `active` / `retired`). Only one
     version per url is `active`.
   - **`Concept`:** system version (foreign key), code, display, active, `ancestors bigint[]`
     (the ids of every is-a ancestor, transitive, with a GIN index), and `properties` (JSONB).
   - **`Designation`:** concept (foreign key), language, use (system/code), value, and `search`
     (lowercased, unaccented, with a `pg_trgm` GIN index).
   - **Why an ancestor array and not a closure table:** SNOMED has about 370k concepts with
     about 15 ancestors each, so the arrays hold about 6M ids inside 370k rows, against a
     closure table of 6M rows. `is-a X` becomes `id = X OR ancestors @> {X}`, which uses the GIN
     index.
3. **The local client.**
   - `ihr_terminology.client.LocalTerminologyClient.query(method, resource, parameters)` is
     installed in `AppConfig.ready()` as `ResourceManger._fhir_client` and as
     `care.emr.fhir.resources.base.default_fhir_client`. No edit in `apps/`.
   - It answers the four operations with FHIR-shaped JSON. Anything else gets an
     `OperationOutcome`, never a network call.
   - `FHIRClient.query` itself is wrapped to raise, so a later CARE change that creates its own
     client also cannot call out. The startup check refuses any `SNOWSTORM_DEPLOYMENT_URL`
     except the black hole.
4. **`$expand`:**
   - **Compose:** includes and excludes, each with a concept list or filters:
     - `concept is-a | descendent-of | = | in | not-in`;
     - the ECL subset `<`, `<<`, `>`, `>>`, `*`, with an optional `|term|`;
     - `regex` refused.
   - **Search:** each word of `filter` must prefix-match a word of a designation, in any
     order. Ranking: exact, then starts-with, then preferred term, then shortest.
   - **Results:**
     - `count` is capped at 100.
     - `displayLanguage` picks the designation in that language when one exists (`hi`),
       otherwise the display.
     - `includeDesignations` returns them all.
   - **A system that is not loaded:**
     - An enumerated concept list is expanded from the compose itself (its own displays).
     - A whole-system include returns nothing and logs a "content missing" warning once per
       system. No error is raised, so CARE pages still work.
5. **`$validate-code`** is true when the code is in the expansion of the compose (computed
   with the same query, no search text). A system that is not loaded means false, so CARE
   refuses the write. This is safer than accepting unknown codes, but it changes behaviour.
   Today every coded write fails, so this is strictly better.
6. **`$lookup`** returns name, display, designations and properties (`parent` and `child` for
   is-a, and the inactive flag) in CARE's `parse_fhir_parameter_output` shape.
7. **Loader: `manage.py ihr_terminology_load`.**
   - **Formats:**
     - `--fhir <CodeSystem.json | ValueSet.json>`: a ValueSet with an enumerated compose
       becomes a fragment code system, used for UCUM's common units;
     - `--rf2 <SNOMED RF2 Snapshot dir> --module-filter … --language-refset …`: concepts,
       descriptions, is-a (116680003) relationships, and the preferred term from the language
       refset;
     - `--bundled`: everything under `ihr_terminology/content/`.
   - **How a load works:**
     - It loads into a new `loading` version.
     - It computes the ancestors with a recursive CTE, checks the counts, then switches
       `active` atomically. The previous version stays `retired` until `--prune`.
     - It refuses a file whose sha256 differs from the one given or recorded.
8. **Bundled content (free to redistribute), with a MANIFEST recording each source package and
   its sha256:**
   - **HL7 Terminology (THO) 7.5.0, CC0-1.0.** The seven code systems CARE uses, copied from
     `hl7.terminology.r4` 7.5.0 (package sha256 `1da30045…6e60`).
   - **UCUM common units, 1,364 codes.** From the FHIR R4 core package 4.0.1 (CC0-1.0,
     `ValueSet-ucum-common`, package sha256 `ebd7731d…9d44`), with UCUM's copyright notice
     (Regenstrief, royalty-free terms).
9. **Tenancy and audit.**
   - The tables are shared and read-only for hospitals.
   - The isolation suite's crawl has nothing new to reach (no new routes).
   - CARE's 10 s `fhir_resource:` cache is already per hospital through the key function, which
     is harmless.
   - The audit trail covers hospital tables only. Terminology loads are recorded in
     `CodeSystemVersion` (who, when, which file and sha256) and in the log.
10. **Tests:**
    - Each operation against small synthetic code systems: a made-up hierarchy with SNOMED-like
      ids, never real SNOMED content.
    - The ECL subset; the ranking; unloaded systems; `$validate-code` true and false.
    - An RF2 loader fixture made by hand.
    - No network: a socket guard fails any connection.
    - CARE's own valueset API tests run through our client.
    - A tenant-mode test that two hospitals see the same terminology and cannot write to it.

## Decisions to log (auto-accepted, awaiting the owner)

- **Q-F6:** our own in-process engine, not a terminology server; Snowstorm Lite is the runner-up
  for SNOMED.
- **Q-F7:** unknown codes are refused by `$validate-code` (a system without content means
  "no"), not accepted.
- **Q-F8:** bundled content is THO and UCUM common units only. SNOMED, LOINC, ICD and drugs wait
  for P1-T10b.

---

## After the design attack (2026-11-12): 18 of 23 findings survived, all folded in (built as ADR-016)

| Finding | Change |
|---|---|
| CARE pins HL7 at 2.0.0, which THO 7.5.0 lacks (high) | A pinned version that is not loaded answers from the active one, logged once. Tested with the real bundled content |
| T10a too big (8–15 h) | Split into T10a1 (this), T10a2 (ECL, RF2, benchmark) and T10b (licensed) |
| No cost limits (DoS across hospitals) | Per-call limits (100 results, 20 includes, 2,000 codes, 100 characters / 8 words), 30 calls per request (middleware), 2 s statement timeout, a bounded "warned once" set |
| Unsupported filters or ECL could widen a value set | Allow-list of (property, op); anchored ECL grammar; anything else refuses the whole compose |
| $validate-code truncated at 100; system and display ignored | Point query; system must match; display must be one of the concept's terms |
| Enumerated lists of unloaded systems: offered but refused, and SNOMED shown before its licence | One rule: licence-gated systems answer nothing until loaded; free systems' listed codes are offered and accepted |
| Not read-only in fact | Statement triggers refuse writes unless the loader opened the tables (public only); tested from a hospital's context with ORM, bulk and raw SQL |
| Load record weak | `--operator` required; host and sha256 recorded; one log line per load; history kept after prune |
| UCUM is a grammar | Common units expanded; validation accepts units well formed from them (`{capsule}`, `mg/kg/d`); duplicate codes merged |
| CARE's tests never reach the client | Our tests go through CARE's ValueSet, CodeConceptResource, the real validator and the API, with nothing mocked |
| $lookup parent/child | Direct parents stored; children found through a GIN index on parents |
| Code-only concept list could raise a 500 | Display falls back to the code |
| displayLanguage underspecified | The preferred designation for an exact non-English language; otherwise the concept's display |
| Search semantics (tags, codes) | Words of letters and digits, every word a word prefix, an exact code ranks first; empty search takes a cheap path |
| TENANCY_DESIGN and PLAN contradicted | ADR-016; TENANCY_DESIGN §5 amended; PLAN rows for the split |

Refuted: audit volume (unchanged from today's failing calls; an `ihr_audit` question), SNOMED-size
loading (P1-T10a2/T10b), loader sha256 (already per file).
