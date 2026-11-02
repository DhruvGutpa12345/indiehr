# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

IndieHR is a planned **multi-tenant SaaS Hospital Information System for India**, built from open-source components.

**No application code exists yet.** The repo currently holds planning material only. Before doing anything, read these four files:

- `docs/PLAN.md` — the master plan: architecture options, blueprint corrections, phased task list with IDs `P<phase>-T<n>`
- `docs/TRACKER.md` — what is done or pending; **update it at the end of every task**
- `docs/QUESTIONS.md` — open decisions; do not assume an answer the user has not given
- `docs/blueprint/Open Source Hospital Architecture Blueprint.md` — the user's original master document (it is a single-hospital, on-prem design; PLAN.md §2 lists where it is wrong or outdated)

Further reading:

- Research evidence (versions, licences, India regulation as of Nov 2026): `reports/India hospital SaaS stack research.md` and `research_notes/`
- Decisions go in `docs/adr/NNN-title.md`

## Commands

- `pre-commit run --all-files`: all repo checks. Hooks: whitespace/EOF fixes, YAML/JSON/TOML checks, large-file guard, private-key detection, **gitleaks** secret scan, **actionlint**. CI (`.github/workflows/ci.yml`) runs the same on every push to `main`.
- **gitleaks false positives:** add the exact fingerprint to `.gitleaksignore` with a dated comment. Never disable the hook.
- **sudo:** commands that need sudo must be run by the user in Ubuntu's own terminal (Ctrl+Alt+T). The app's terminal and `!` prompt have "no new privileges" set.
- **uv / pre-commit:** live in `~/.local/bin`. Prepend it to PATH in non-login shells.
- App build/test commands arrive with the CARE fork (P0-T4/P0-T5). Do not invent them before then.
- **Git:**
  - commit straight to `main`, with the task ID in each message;
  - repo-local identity is `DhruvGutpa12345` with the GitHub noreply email;
  - remote: private `github.com/DhruvGutpa12345/indiehr`.

## How the user wants work done

- **One small task at a time.**
  - Take the next task from TRACKER.md.
  - Implement it, run its tests, and report the real results.
  - Update TRACKER.md, then stop and wait for the user's "proceed".
  - Never start the next task unasked.
- **Planning tasks:** research and plan only. No code changes unless the task is an implementation task.
- **Testing for the user:** whenever the user must test something, give them all three of:
  - a one-click URL;
  - seeded test logins (synthetic, recorded in `deploy/seed/TEST_USERS.md` and the TRACKER "Test credentials" section);
  - a 3–5 step click-script.
- **Effort advice:** end **every reply** with the recommended **model + effort level** for the next task (e.g. "Next: P0-T2 — Sonnet, low"). The guide is in PLAN.md §5.
- **Versions:** use only the latest stable versions compatible with the stack. Re-check current releases before installing anything, pin exact versions, and record them in `docs/VERSIONS.md`.
- **Subagents:** use them sized to the job:
  - Haiku or Sonnet for lookups and boilerplate;
  - Opus for architecture, security, tenancy, ABDM crypto and billing maths.

## Key constraints

- **India context throughout:**
  - INR with lakh/crore formatting, IST, DD-MM-YYYY;
  - Indian addresses (state/district/PIN);
  - English and Hindi first;
  - GST rules for hospitals (clinical services are exempt; non-ICU room rent above ₹5,000/day is 5% GST with no input tax credit).
- **Data:**
  - All data stays in Indian cloud regions.
  - Never store full Aadhaar numbers.
  - No real patient data outside production.
- **Compliance targets:**
  - ABDM M1–M3 (API v3, NRCeS FHIR IG 6.5.0)
  - DPDP Act + Rules 2025 (core duties from about May 2027)
  - CERT-In: 6-hour incident reporting, 180-day logs kept in India, NTP synced to NIC/NPL
  - NABH 6th edition
  - CDSCO software-as-medical-device guidance: AI only drafts; a clinician must sign off
- **Licences:**
  - Our own code may stay proprietary on top of MIT/Apache components.
  - AGPL components (Orthanc plugins, Grafana, Synapse, etc.) are used **unmodified** and reached over the network only.
  - Do not use "ERPNext", "Frappe" or "CARE" in product branding.
- **Dev machine:** 6.7 GB RAM, no GPU. Run one heavy service stack at a time. The user is in the `docker` group. A RAM upgrade to 32 GB is planned; until then, do not run the CARE stack.

## Architecture (decided — see `docs/adr/`)

The product is a fork of **CARE** (ohcnetwork: Django backend + React frontend, MIT, FHIR-shaped models). Upstream code stays as untouched as possible, with our features added as CARE plugins (`ihr_*`).

Around that core, the plan adds:

- schema-per-tenant multi-tenancy;
- Keycloak 26.8 (one Organization per hospital);
- Orthanc + OHIF for imaging, with per-tenant labels;
- our own ABDM integration;
- an analyzer bridge;

Accounting events go through a per-tenant outbox to **both** a Tally XML export and an ERPNext v16 connector (ADR-004). AI features are **deferred** (ADR-005): build no LLM/MCP features unless asked. Commit directly to `main`, with the task ID in each message.

See PLAN.md §3 for the diagram and the alternatives.
