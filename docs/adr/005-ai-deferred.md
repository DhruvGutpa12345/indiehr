# ADR-005: AI features deferred

- **Status:** Accepted
- **Date:** 2026-11-03
- **Decided by:** product owner (Q-D6: "No AI for now")

## Decision

The blueprint's whole AI layer (Phase 7) is **deferred**:

- MCP server;
- LLM inference;
- ambient scribe;
- coding assist;
- radiology AI.

No patient data is sent to any AI service.

## Still in scope (they are not AI)

- **Deterministic clinical rules:** allergy checks, the curated drug-interaction list (Q-F3), duplicate-order checks, critical-value alerts, delta checks, and early-warning scores (NEWS2, qSOFA) as fixed formulas.
- **Clean, FHIR-shaped data and APIs**, so that AI can be added later without rework.

## Revisit when

- the MVP is live with pilot hospitals, **and**
- the user approves a data-handling model:
  - Claude on AWS Bedrock with India-only routing; or
  - self-hosted models on GPUs.

Check the CDSCO medical-device-software guidance (July 2026) at that time.
