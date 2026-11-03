# Our edits to upstream CARE files

Prefer plugins (`plugins/ihr_*`). If a file under `apps/backend` or `apps/frontend` must change, add a row here in the same commit, so upstream syncs know what to re-check.

| Date | File | Why | Task |
|---|---|---|---|
| 2026-11-03 | `apps/backend/Pipfile`, `apps/backend/Pipfile.lock` | Security: fixable CRITICAL CVEs found by Trivy. PyJWT 2.12.0→2.14.0 (auth bypass), Pillow 12.2.0→13.0.0, Authlib 1.6.12→1.9.0, Brotli 1.2.0→1.2.1, certifi 2026.4.22→2026.10.18. Side effects: cryptography 48.0.1, pycparser 3.12, new joserfc 1.7.7 (Authlib dep), optional `types-awscrt` dropped from dev. Relocked with pipenv 2025.1.1 in `python:3.13-slim-bookworm`. On upstream sync: take upstream's versions if they are ≥ these; otherwise keep ours and re-lock | P0-T9 |
| 2026-11-03 | `apps/frontend/package-lock.json` | Security: DOMPurify 3.4.12→3.4.17 (GHSA-cg2g-xh64-v4q7). Only the dompurify entry changed (`package.json` range `^3.4.11` already allows it) | P0-T9 |
