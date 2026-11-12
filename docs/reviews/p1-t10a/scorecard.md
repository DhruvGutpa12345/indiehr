# P1-T10a: terminology service, §5 rubric scorecard (2026-11-12)

The data was gathered on 2026-11-12 with `gh api`, Docker Hub and the projects' documentation by
a research agent (Sonnet). Raw notes are in the run's scratchpad and are summarised here.

## Hard gates

| Candidate | Licence | Telemetry / calls out | Security | Fits 8 GB server | Result |
|---|---|---|---|---|---|
| Snowstorm 11.0.0 (IHTSDO) | Apache-2.0 | none found | — | **No**: needs Elasticsearch (several GB of heap) | out |
| Ontoserver (CSIRO) | **commercial** | — | — | — | out |
| Hermes (wardle) | EPL-2.0 (weak copyleft), SNOMED only, no full FHIR API | — | — | yes | out (does not cover CARE's operations) |
| Pathling / Medplum / OCL | Apache-2.0 / Apache-2.0 / MPL-2.0 | — | — | heavy (Spark, a whole EHR, a big stack) | out (fit) |
| Snowstorm Lite 3.0.0 | Apache-2.0 | contacts MLDS only if syndication is switched on; an MCP server is on by default (must be switched off, ADR-005) | not scanned | yes (~500 MB once indexed) | in |
| FHIRsmith v1.0.1 (tx.fhir.org's server) | BSD-3-Clause | optional ecosystem lookups and report mails, off by default | not scanned | unknown RAM | in |
| HAPI FHIR JPA 8.x | Apache-2.0 | none found | not scanned | 1–2 GB JVM | in |
| Our own plugin over Postgres | ours (proprietary; on MIT CARE) | none (no network code) | ours | no new process | in |

## Scores (0–5, weight in brackets; maximum 500)

| Criterion | Own plugin | Snowstorm Lite + own for the rest | HAPI FHIR JPA | FHIRsmith |
|---|---|---|---|---|
| Maintenance (20) | 4: ours, small surface | 4: SNOMED International; 49 commits in 90 days, 7 contributors, 10 open issues | 5: 663 core commits a year, 273 contributors | 3: v1.0.1 released today, 13 contributors, mostly one lead |
| Velocity (10) | 3 | 4 | 4 | 5: 596 commits in 90 days |
| Community (10) | 1 | 3 | 5 | 2 |
| Architecture (15) | 4: tables + GIN arrays, in-process | 4: Lucene, ECL-complete | 4: generic, mature | 3: young Node.js |
| Fit (15) | 5: answers exactly CARE's four calls; no new service; tenancy, backups and audit unchanged | 3: SNOMED only, so a second piece for HL7, UCUM and LOINC anyway | 2: heavy generic server; slow SNOMED/LOINC loads | 4: covers every system CARE uses |
| Deployment (10) | 5: nothing to deploy | 4: one more pinned image | 3: JVM, its own database | 3: one more image, untested |
| Resources (10) | 5: Postgres only (SNOMED about 1–1.5 GB of disk, little RAM) | 3: about 500 MB of RAM | 1: 1–2 GB of RAM | 2: unknown |
| Cost (5) | 5 | 5 | 5 | 5 |
| India (5) | 5 | 5 | 5 | 5 |
| **Total** | **405** | **375** | **370** | **335** |

## Verdict

- **Winner: our own plugin.** It adds no process, no network hop and no new image, and it
  carries exactly the subset CARE uses: $expand with is-a and the ECL forms CARE's value sets
  contain, $validate-code, $lookup and the CodeSystem search.
- **Runner-up: Snowstorm Lite**, within 10 %, so it is a decision for the owner (Q-F6). We would
  switch to it for SNOMED if either of these happens:
  - our engine is too slow at full SNOMED size, measured in P1-T10b with the real release;
  - hospitals need ECL beyond the subset (refinements, attribute queries) for clinical rules.
- **Not chosen:**
  - FHIRsmith is promising, but at v1.0.1 it is too young to bet on yet. Look again in 2027.
  - HAPI is too heavy for the 8 GB server.

## Licensed content (for P1-T10b; owner gates)

- **SNOMED CT:** free in member countries, and India is one. The licence comes through MLDS
  India; NRCeS (C-DAC) distributes the India extensions, and the drug extension was released
  31-08-2026. SNOMED's page says web applications need permission, so get written confirmation
  for SaaS use from NRCeS.
- **ICD-10:** WHO licenses commercial software case by case. **ICD-11:** content is CC BY-ND 3.0
  IGO with no fee, plus an API and an offline container. Choosing between them is a decision for
  the owner.
- **LOINC:** free, with an account, the terms accepted, and the copyright notice shown.
- **UCUM:** royalty-free, with the copyright notice.
- **HL7 THO** (`hl7.terminology.r4` 7.5.0) and **FHIR R4 core** (4.0.1, which holds the common
  UCUM units value set): CC0-1.0. Bundled in P1-T10a.
