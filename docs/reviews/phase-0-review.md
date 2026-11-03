# Phase 0 review: independent second opinion

- **Reviewer:** independent Opus review agent (did not do the work)
- **Date:** 2026-11-03
- **Scope:** PLAN.md, TRACKER.md, QUESTIONS.md, ADR-000 to ADR-006, GAP_ANALYSIS.md, the UPSTREAM docs, VERSIONS.md, LOCAL_DEV.md, CI workflows, pre-commit, the Trivy and gitleaks config, scripts, deploy/, the running local stack, git history, GitHub run history

## Verdict

Phase 0 is mostly done, and done with better discipline than usual: there are ADRs, a real CI gate that runs the full upstream test suite, Trivy at HIGH, localhost-only ports, rotated fixture passwords, and prompt-injection awareness. **It is not cleanly closed**, for three reasons:

- The P0-T5 deliverables are not committed, and its acceptance criteria are not met.
- A hard-coded third-party telemetry path (OHC's Sentry) ships in every production frontend build. This contradicts the claim that the app makes no external calls.
- The central architectural bet, schema-per-tenant on CARE, has a concrete blocker in CARE's `User` model that the plan has not yet recognised.

**Grade: B−.** The work is good, but these items must be fixed before P1-T1 starts.

## Findings

| ID | Sev | Area | Finding | Evidence | Recommended action | Task |
|---|---|---|---|---|---|---|
| R0-01 | **High** | Privacy / egress | Every production frontend build turns on Sentry with **OHC's hard-coded DSN** (sentry.io). Any JS error, with its URLs (patient IDs) and messages, goes to a third party outside India. The TRACKER says "OHC URLs removed". | `apps/frontend/src/index.tsx:34-39`; `care.config.ts:97-101` (default DSN, env "staging"); `ErrorBoundary.tsx:26`; DSN found in `deploy/dev/frontend-build/assets/index-*.js` | Patch upstream so Sentry is off unless our DSN is set, and log the patch. Add a CI bundle deny-list check (`sentry.io`, `ohc.network`, `google.com/recaptcha`, `openstreetmap`). | new, before P1-T8 |
| R0-02 | Medium | Privacy / egress | Other external calls: a `cdn.ohc.network` logo on `/2fa`, Google reCAPTCHA after failed logins (the site key is empty, so login may break), and OSM Nominatim/tiles in the facility form. The CSP is Report-Only. | `AuthHero.tsx:74`, `Login.tsx:6`, `GeoLocationPicker.tsx`, `deploy/docker/frontend-nginx.conf:14` | List every egress path. Enforce a CSP with `connect-src` limited to our API. | new (same task as R0-01) |
| R0-03 | **High** | Tenancy | CARE's `User` has FKs to `emr.Organization` and `facility.Facility`. `ValueSet`, `ProductKnowledge` and `Organization` live in the large `emr` app. django-tenants splits shared from tenant data **by app**, so ADR-002's "User and reference masters in `public`" (GAP §2 option a) cannot work without upstream model patches or duplicated tables. P1-T1 is sized as a single task. | `care/users/models.py:117-143`; GAP_ANALYSIS §2; ADR-002 "Shared public schema" | Add a design spike before the prototype: a SHARED/TENANT app map, a list of every cross-boundary FK, and a user-model decision, recorded as an ADR-002 amendment. | P1-T1 split (new P1-T0) |
| R0-04 | Medium | Process | The P0-T5 work is **staged but not committed**: `scripts/care-dev.sh`, `care-local.override.yaml`, `LOCAL_DEV.md`, and the TRACKER and CLAUDE.md edits. `origin/main` = `f40049d70` does not have them, yet the TRACKER cites them as evidence. | `git status` / `git diff --cached --stat` (8 files, +159) | Commit with the P0-T5 ID once the owner approves. | P0-T5 |
| R0-05 | Medium | Acceptance | P0-T5's "Done when" is not met: the owner has not clicked through OPD, IPD, lab, pharmacy and billing; no billing or pharmacist users exist; the GAP_ANALYSIS §8 verification list is not done (gap-free invoice numbers, FEFO, reference ranges, dates); the Postgres 18 check promised in ADR-003 was not done (the stack runs `postgres:17-alpine`). | PLAN §6 P0-T5; TRACKER P0-T5 row; ADR-003 rule 5 | Run the §8 checklist and the owner click-through. Record the results in GAP_ANALYSIS. | P0-T5 |
| R0-06 | Medium | Versions | The claim that we use the latest versions is **false**. On 2026-11-03 we pinned Django 6.0.8, but 6.0.9 (released 2026-10-06, a Django security-release day) and 6.1.2 exist. Other pins also lag: DRF 3.18.2 (3.18.3 exists), PyJWT 2.14.0 (2.15.1), cryptography 50.0.0 (50.0.2), Authlib 1.9.1 (1.9.2). Each bump went to the *minimum Trivy-fixed* version. The setup script installs Docker unpinned. | PyPI JSON; `Pipfile.lock`; `deploy/dev/setup-workstation-root.sh` | Bump to the latest patch releases. Add a weekly "outdated" report job (`pip list --outdated` / `npm outdated`). | new (small) |
| R0-07 | Medium | Supply chain | Actions are pinned by mutable **tags**, including the third-party `dorny/paths-filter` and `aquasecurity/trivy-action`. The trivy-action tags were reportedly hijacked in a March 2026 incident; please verify. The repo allows all actions, and `sha_pinning_required=false`. The good news: the default token is read-only and no secrets are used. | `.github/workflows/apps.yml:30-31,160,172`; `gh api …/actions/permissions` | Pin every action to a commit SHA, with the version in a comment. Restrict allowed actions. | new (small) |
| R0-08 | Medium | Secrets | The CI gitleaks check **does nothing**. The hook runs `gitleaks git --pre-commit --staged` with `pass_filenames:false`, so on a clean CI checkout nothing is staged and nothing is scanned. Trivy's secret scan reports but cannot fail the build (`exit-code: "0"`). A free private repo has no GitHub secret scanning or branch protection (API returns 403). My full-history scan found no real leaks: 5 hits on `main`, all upstream false positives. | `.pre-commit-config.yaml:20-22`; hook definition; `apps.yml:159-167`; gitleaks over `main` | Add a CI step `gitleaks git --log-opts=<range>` (or `gitleaks dir`). Make the Trivy secret scan blocking, with a scoped ignore list. | new (small) |
| R0-09 | Medium | Prod config | Upstream `base.py` ships a hard-coded `SECRET_KEY` default and `ALLOWED_HOSTS=["*"]`. `deployment.py` does not refuse to start without them, and JWKS falls back to a file. | `config/settings/base.py:39-44`; `deployment.py:130-132` | Add our own settings layer that refuses to start in staging or production without the secret key, JWKS and allowed hosts. | P1-T8 |
| R0-10 | Medium | Docs accuracy | Stale or contradictory text (listed in §2). | §2 | One doc-sync commit. | new (Sonnet, low) |
| R0-11 | Medium | Reproducibility | Test logins cannot be recreated from the docs. `care-dev.sh seed` reloads the **public** fixture passwords, and the rotation step is manual and undocumented (LOCAL_DEV points to the TRACKER, which has no procedure). The bundle artifact expires after 7 days. `up` never rebuilds the image after a lockfile change. README setup omits uv, pre-commit, `gh auth` and the bundle download. | `scripts/care-dev.sh:14-15,22`; LOCAL_DEV "Start" | Make `seed` rotate passwords and write `TEST_USERS.md`. Rebuild the image when the lockfile hash changes. Make README a complete new-developer path. | new (Sonnet) |
| R0-12 | Medium | Testing | **None of our own code is tested.** `plugins/` holds only a README. The scripts have no shellcheck or tests. `care-dev.sh` and its override never run in CI. Upstream's Playwright e2e suite never runs. The production backend image (`prod.Dockerfile`, unpinned `python:3.13-slim-bookworm`) is never built or image-scanned, so the claim that it is "already non-root" is unverified. Trivy only scans the filesystem, not OS packages. | `apps.yml`; `plugins/`; `.pre-commit-config.yaml` | Add a shellcheck hook, bats tests for the sync script, and a CI job that builds the production image and runs `trivy image`. Add a Playwright smoke test against the compose stack. | new; P1-T8 |
| R0-13 | Low | Upstream sync | Problems in `sync-upstream.sh`: (a) our patched `Pipfile.lock` and `package-lock.json` will conflict on most syncs, and no relock step is documented; (b) on a conflict, `set -e` exits mid-merge with no recovery notes; (c) a short SHA fails `git fetch`; (d) the commit count assumes the pin is an ancestor of the new commit; (e) its commits lack task IDs, against CLAUDE.md; (f) `make sync-upstream` is documented, but `make` is not installed; (g) no tests run after the merge. | `scripts/sync-upstream.sh:83-118`; UPSTREAM.md | Add a relock step and a conflict runbook to UPSTREAM.md. Take a `--task` argument. Require full SHAs. | new (small) |
| R0-14 | Low | CI ops | Commits go straight to `main` with no protection, and `cancel-in-progress` is on for `main`, so intermediate commits can go unverified. Path-filter gaps: the frontend job ignores `plugins/`, and Trivy ignores `deploy/` and `scripts/`. One full run costs about 17 job-minutes; the free plan's 2,000 min/month allows about 115 runs, which Phase 2 will approach. | `apps.yml:18-20,35-42` | Set `cancel-in-progress: false` on `main`. Widen the filters. Track minutes monthly. | new (small) |
| R0-15 | Low | Workstation | Unrelated containers on the dev host listen on all interfaces: MariaDB 10.11.4 on 3306, Nextcloud 27.0.0 (EOL) on 10081/10443, Syncthing, and Samba on 139/445. The same host holds a `gh` token with repo write access, and those services use RAM on a 6.7 GB machine. | `ss -ltn`; `docker ps` | Owner's call: bind them to localhost or stop them. | — |
| R0-16 | Medium | Strategy | PLAN §1.1 makes ABDM M3 MVP-critical because of DHIS, but DHIS Corrigendum 7 ran only from April to September 2026, and an extension is unknown. There are no pilots (Q-B2), and pricing and timeline (Q-B6/B7) are open, while about 50 tasks are committed before a first sale. | research report line 101; QUESTIONS | Re-check the DHIS status. Add a customer-discovery / pilot-LOI task before Phase 2. | new |
| R0-17 | Medium | Strategy | The online-only decision (ADR-000) conflicts with the 49% of buyers who name poor internet or power as a barrier (PLAN §1.1). The only mitigation is a 4G router. | ADR-000 §5 | Plan a "downtime kit": printable forms, a local token printer, and back-entry. | new (P2) |
| R0-18 | Low | Hindi | Hindi covers 795 of 6,856 keys (11.2%; I verified this). P1-T7 is one Sonnet/medium task for about 6,000 strings, and clinical Hindi needs a clinical reviewer. | `public/locale/*.json` | Split P1-T7 into locale work and translation batches. | P1-T7 |
| R0-19 | Low | Blueprint | Blueprint items dropped without an ADR: ICU bedside-monitor telemetry, the integration engine/event spine (OIE/Camel), Keycloak LDAP/SAML federation and ABAC, and HA (Patroni/Galera) with 6-hourly encrypted off-site backups (RPO/RTO is pushed to P4-T7). | blueprint §1 and §"High-Availability"; PLAN §2 #11 | ADR-007, "Blueprint items deferred or dropped". Set RPO/RTO before the first pilot. | new |
| R0-20 | Low | AppSec | JWTs are stored in `localStorage`, and the CSP is report-only, so any XSS means token theft. | `AuthUserProvider.tsx:89-90` | Decide token storage in P1-T4 (BFF or httpOnly cookies). | P1-T4 |
| R0-21 | Low | Tenancy × ABDM | ABDM uses one bridge URL, with each tenant's HFR ID as its HIP ID. Tenant routing for ABDM callbacks is not in ADR-002. | research report line 95 | Add a HIP-to-tenant routing table to the P1-T1 design. | P1-T1 |

## 1. Strategy

CARE, schema-per-tenant and Keycloak is a **defensible** choice for 10–100 bed hospitals:

- it has an MIT licence and one main stack;
- its billing, inventory, scheduling and lab models are strong (GAP §3);
- it has no raw SQL.

Option C (Marley) would fix tenancy but trade away clinical depth. The plan underweights three risks:

1. **CARE's data model vs. hard tenancy (R0-03).** `User`, `Patient`, `Organization` and `ValueSet` are global by design, and upstream FKs cross the boundary we want to draw. "Upstream untouched" and "schema-per-tenant" may not both hold. Budget 3–5 tasks, not 1, and expect a standing upstream patch set.
2. **Build volume vs. team, with an unvalidated market (R0-16).** The plan needs: insurance/TPA models from scratch, packages, a PO→GRN chain, MIS, a Tally export and ERPNext connector, ABDM M1–M3 with a WASA audit, NHCX, PM-JAY (no TMS API), and about 6,000 Hindi strings. This is a solo founder with agents, no pilot hospital and an uncertain DHIS lever. A billing-first wedge (registration + OPD/IPD billing + pharmacy + TPA desk) should ship to one pilot before ABDM depth.
3. **Upstream posture.** OHC has taken ABDM, HCX, Scribe and TeleICU private; there are no releases since v3.0.32; development happens on `develop`. Our security bumps already diverge from upstream lockfiles (R0-13). Assume we will own the security cadence for the fork.

**Silent drops:** see R0-19. The online-only decision is recorded, but its risk is under-mitigated (R0-17).

## 2. Decisions: contradictions and stale text

- **PLAN:**
  - **§3** (line 125): "P0-T5 runs both CARE and Marley". Marley was dropped.
  - **§4:**
    - says P0-T5 waits for the RAM upgrade;
    - lists Docker as 26.1.3;
    - says "Folder is not a repo".
  - **§8:** RAM risk "before P0-T5".
  - **Decision log:** ADR-006 is missing.
  - **P1-T1** still compares schema vs. DB vs. RLS, which ADR-002 already decided. The real comparison is django-tenants vs. a `search_path` middleware.
  - **P0-T4** "Done when" uses `make`, which is not installed.
  - **P0-T8** "Done when" asks for a "green pipeline on PR", Playwright and image builds. None exist, and there are no PRs (ADR-000).
  - **✅ marks** in PLAN conflict with the TRACKER legend (✅ = owner-approved; all P0 tasks are 🟡).
- **ADRs:**
  - **ADR-001** still says "fork" and was not amended after ADR-006.
  - **ADR-003** consequences say "P0-T5 is blocked".
  - **ADR-003** rule 5 (Postgres 18 check in P0-T5) was not done.
- **QUESTIONS:**
  - **Q-C4:** "P3-T11/T12 move…". In PLAN they are P3-T9/T10.
  - **Q-E1:** "P0-T5 waits".
- **GAP_ANALYSIS:**
  - says "Phase 1: 11 tasks" (now 12);
  - says "Nothing was run (needs 32 GB)";
  - its "verify in P0-T5" items are still open.
- **LOCAL_DEV:** "reset as described… (see TRACKER)". There is no procedure, and the ports table omits 9100.
- **Commit messages:** `a214b3b2b` and the two subtree squash commits have no task ID.
- **TRACKER task counts are correct:** P0 8/8, P1 1/12, P2 20, P3 12, P4 7, P5 8, P9 7.

## 3. Factual claims checked

| Claim | Result |
|---|---|
| CI green | **True.** Run 41583635960: backend 11m02s, frontend 5m04s, Trivy 36s, all ✓. Later runs skipped heavy jobs (no `apps/` changes). |
| Trivy clean | **True today.** A local Trivy 0.76.1 filesystem scan of `apps/` at HIGH/CRITICAL (fixable only) finds 0. Container images are never scanned (R0-12). |
| Ports localhost-only | **True for IndieHR.** `ss -ltn` shows 127.0.0.1 for 8080/9000/9001/9100/5433/6380. Other host services are exposed (R0-15). |
| Passwords not in git | **True.** `git log -S <local pw> --all` returns 0. `TEST_USERS.md` is git-ignored. gitleaks on `main` gives 5 upstream false positives. |
| Fixture passwords replaced | **Not verified by me** (it needs a POST). It is not reproducible (R0-11). |
| Latest versions | **Partly false** (R0-06). Actions and tools are latest (checkout v7.0.1, setup-node v7.1.0, Trivy 0.76.1, gitleaks 8.30.1, Keycloak 26.8.1 exists). |
| No external network calls | **False as built** (R0-01, R0-02). True only for the click path that was tested. |
| Pins = upstream head | **True.** `develop` heads are 67a46185eb98 and 57205b897804. |
| Running image has the bumped dependencies | **True.** The container has Django 6.0.8, DRF 3.18.2 and PyJWT 2.14.0. The image was built at 11:20 IST, after the lockfile commit at 10:47. |
| Hindi coverage at 11.5% | **True** (11.2% now). |

## 4–6. Security, hygiene, process (details in the table)

- **Security:**
  - The upstream `apps/frontend/.env` holds OHC's API, CDN and app-store URLs and a reCAPTCHA *site* key, which is public and not a secret. The risk is that builds default to OHC, so override every `REACT_*` setting and fail the build if an OHC host appears (R0-01).
  - `.trivyignore` is properly scoped.
  - The CI secret gate is a no-op (R0-08).
  - Treat P4-T2 (breach runbook), P4-T4 (encryption and secrets), P4-T6 (DPA) and an RPO/RTO as **hard gates before the first pilot with real data**.
- **Hygiene:**
  - The subtree approach is sound. Lockfile patches are its main friction (R0-13).
  - Our own code has no tests (R0-12).
  - A new developer cannot reach a working login from the docs alone (R0-04, R0-11).
- **Process:**
  - Tracker counts are accurate, but P0-T5 is marked done while uncommitted and unmet.
  - Effort advice is present.
  - One-click testing is not yet met: the local URL needs 3 commands plus an artifact download, and there is no click-script in the TRACKER.
  - "Latest versions" is not met (R0-06).
  - Not done:
    - P0-T5 billing/pharmacist users and the click-through;
    - the GAP §8 checks;
    - the Postgres 18 check;
    - Playwright (P0-T8).

## 7. Phase 1 readiness

**Decide before P1-T1:**

1. the user model (shared vs. per-tenant copies vs. patching `User`'s FKs);
2. the exact shared/tenant model list, including the reference masters;
3. whether owner-approved upstream model patches are acceptable, since ADR-001 says "untouched";
4. Postgres 17 vs. 18;
5. the cloud provider (Q-E4), so that P1-T8 is not blocked.

**Proposed order:**

| Order | Task |
|---|---|
| 1 | P1-T0 tenancy design spike (R0-03, R0-21) |
| 2 | P1-T1 prototype + isolation suite |
| 3 | P1-T3 Keycloak, then P1-T4 OIDC (the user model depends on it) |
| 4 | P1-T2 routing/branding |
| 5 | New "egress + secure settings" task (R0-01, R0-02, R0-09) |
| 6 | P1-T8 staging (earlier than now, so the owner gets real one-click URLs) |
| 7 | P1-T5 provisioning |
| 8 | P1-T6 audit |
| 9 | P1-T10 terminology |
| 10 | P1-T11 roles |
| 11 | P1-T7 i18n (split) |
| 12 | P1-T9 observability |

## Top 5 actions before Phase 1

1. **Fix the third-party egress** (R0-01, R0-02): disable or replace the OHC Sentry DSN, remove the OHC CDN, reCAPTCHA and OSM defaults, add a CI deny-list check on the bundle, and enforce the CSP.
2. **Run a tenancy design spike** (R0-03): map SHARED vs. TENANT apps, list the cross-schema FKs (`User`→`Facility`/`Organization`), decide the user model, and amend ADR-002. Split P1-T1.
3. **Close P0-T5 properly** (R0-04, R0-05, R0-11): commit it, script the password rotation and `TEST_USERS.md`, run the GAP §8 checks and the owner click-through, and check Postgres 18.
4. **Harden CI** (R0-07, R0-08, R0-12, R0-14): pin actions by SHA, make the gitleaks and Trivy secret scans really gate, build and image-scan the production backend image, add shellcheck, and turn off cancel-in-progress on `main`.
5. **Sync docs and versions** (R0-06, R0-10, R0-19): one doc-consistency pass, bump to the latest patch releases (Django 6.0.9 or 6.1.x after testing), and write ADR-007 for the deferred blueprint items. Re-check the DHIS status before ABDM keeps MVP priority.
