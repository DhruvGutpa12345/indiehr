# Phase 1 closing review: independent second opinion

- **Reviewer:** independent Opus review agent (high effort). It did none of the work and changed no
  file except this one.
- **Date:** 2026-11-22, after P1-T25 was committed (`550086a9d`).
- **Method:** static review only. Nothing was run: no Docker, tests or builds. It read PLAN.md
  (§2, §3.2, Phase 1 and Phase 4), TRACKER.md (header, phase status, P1 board and the completed log
  from 2026-11-12), QUESTIONS.md (the open and auto-accepted entries), ADR-000…019, the earlier
  review `docs/reviews/phase-1-review.md`, the P1-T25 run, design, attack and build notes, the
  known-limit sections of P1-T19…T25, `deploy/staging/{compose.yaml,README.md}`,
  `deploy/postgres/server-settings.txt`, `deploy/host/bootstrap.sh`, the alert rules,
  `scripts/staging.sh`, and the code of the P1-T25 gate. CI status came from `gh run list`.
- **Graphify calls:** 9: explain ×7, query ×2. Grep was used only for exact text after the graph
  named the file, and on docs.

## Verdict

**Phase 1 can close, with conditions.** The engineering that Phase 1 promised is built,
reviewed and well tested:
- tenancy, with a fail-closed crawl and mutation-proven controls;
- staff sign-in held by the backend;
- a sealed audit chain with a watcher;
- split database roles;
- reference packs;
- the restricted roles with a role × route matrix;
- releases with a per-hospital gate;
- dumps and single-hospital restores.

Every High and Medium item from the 2026-11-12 review has been built. Two exceptions remain: the
compliance gates (R1-13) and the second alert channel (R1-15), and both wait on the owner.

What is not met is not code. Four exit criteria depend on things outside the repo:
- a real server: the one-click staging URL and the 8 GB load test;
- licences: P1-T10b;
- customer interviews: P1-T14;
- the owner's click-throughs.

Every operational control has so far run only on the 6.7 GB laptop stand-in.

**Conditions to close:**
1. Apps CI is green on `550086a9d`. At the end of this review the "CI" run (45034897010) had passed and
   "Apps CI" (45034896999), the backend and Playwright suites, was still queued.
2. The owner confirms or reverses the open decisions: Q-K126…Q-K131, and Q-F6…Q-F9 and
   Q-O13/Q-O14, which have waited since 12-11.
3. The unmet criteria leave Phase 1 as a named **go-live gate list**, so they are not silently
   dropped. The list holds PR1-1…PR1-5 below, P1-T22c2b, the owner's "(you)" row and the go-live
   row.

**Phase 2:** engineering does not block it. The owner's own gate P1-T14 (Q-S2) does. P2-T4 and
P2-T5 also need P1-T10b.

## Exit criteria

PLAN.md has no separate Phase 1 exit list, so the "Done when" column of each Phase 1 row is used,
grouped by the lenses of this review.

| Criterion (PLAN Phase 1 "Done when") | Status | Evidence |
|---|---|---|
| Multi-tenancy: CARE suite in tenant mode; isolation suite green with each mutation red; Playwright on two subdomains; cell cap decided | **met** | TRACKER:168-177 (T1a/b/c/d); TRACKER:66 (T16: 1 skip, was 34; seven more mutation rows) |
| Per-hospital branding and routing, clicked through by the owner | met on paper | TRACKER:179 (T2b: "click-through waiting for you") |
| Identity: Keycloak sign-in for both tenants, MFA, back-channel logout, invitations, recovery, narrow credentials; new hospital "live in < 5 min from a form" | met on paper | TRACKER:181-191 (about 67 s locally; the click-throughs of T3, T4b, T5a/b/c/e1/e2 are still waiting); live Keycloak tests in CI |
| Hospital-wide admin gate (P1-T25) | met on paper | TRACKER:283; build.md "Review"; CI was still running at review time; Q-K126…131 await the owner |
| Audit trail: viewer, export, outside fingerprints, watch, screens | **met** | TRACKER:192-193, 267, 277; owner click-through of T24a/b/c passed 22-11 |
| Roles: "each role sees only its screens (tests)" | **met** | TRACKER:222-233 (T11a-c, role × route matrix T11b3) |
| Terminology: "diagnosis/drug search works with no external calls" | **not met** | The engine is built (TRACKER:219). Licensed content waits on P1-T10b (TRACKER:221). The 30-minute SNOMED load limit is unsettled (QUESTIONS:145-153) |
| Reference packs: apply, re-apply, leave an edited row alone | **met** | TRACKER:239-246 |
| Release and rollback: "drill on the local staging proof" | met (as worded) | TRACKER:257. A real server is untested (PR1-1) |
| Database roles: start-up refuses an owner runtime role | **met** | TRACKER:259-261 |
| Anonymous routes and the §6.5/§6.8 patches | **met** | TRACKER:264-265 |
| Backups (pgBackRest, restore drill) | met on paper | TRACKER:208. Proven on the local stack; the E2E Chennai repository has never been used |
| Dumps and single-hospital restore: "drill restores one hospital, others unchanged" | met on paper | TRACKER:269-270. Disaster-restore go-live parts deferred (P1-T22c2b, TRACKER:274) |
| Logs: 180 days in India, archive 400 days | met on paper | TRACKER:215-216. Local stack only; the archive bucket is a go-live step |
| Monitoring: "dashboard URL", alerts to the owner | met on paper | TRACKER:214, 217. SSH tunnel to the local stack; e-mail to one person (R1-15 open) |
| Staging: "**one-click staging URL** with test logins" | **not met** | TRACKER:209 (⬜ go-live, "when you choose to pay") |
| Postgres rules and an 8 GB load test: "numbers recorded" | **not met** (the rules are met) | TRACKER:276; README:1564-1567, 1581-1582 |
| i18n: "UI toggles language; PIN lookup works" | met on paper | TRACKER:195-201. The real PIN file is not loaded; Hindi review sheets wait for the owner |
| Upstream sync rehearsal and drift check | **met** | TRACKER:251. The trial branch is unmerged (PR1-9) |
| Customer discovery (5 interviews, go/no-go) | **not met** (owner gate) | TRACKER:234 |

## Items from the 2026-11-12 review

| ID | Earlier severity | Status now | Evidence |
|---|---|---|---|
| R1-01 app runs as the DB owner | High | **closed** (🟡). Residual: P4-T8 (see PR1-5) | TRACKER:60-61, 259-261 |
| R1-02 anonymous routes | Medium | closed | TRACKER:264 (P1-T20a) |
| R1-03 batch allow-list, presigned files, split keys, public bucket | Medium | closed | TRACKER:264-265 |
| R1-04 crawl skips | Low | closed (1 skip) | TRACKER:66 |
| R1-05 browser isolation, switcher | Low | partly: P1-T1d is built, but the switcher **still has no task row** | TRACKER:177; PLAN:235 |
| R1-06 four controls not mutation-proven | Low | closed | TRACKER:248 |
| R1-07 no safe upgrade or rollback | High | closed on the local proof; a real server is untested (PR1-1) | TRACKER:62, 257 |
| R1-08 whole-cluster restore only | Low | closed (T22a/b, c1, c2a); c2b deferred | TRACKER:269-274 |
| R1-09 Postgres operating rules | Low | closed | TRACKER:276; server-settings.txt |
| R1-10 capacity on 8 GB | Medium | partly: the back-channel pool is decided (ADR-019); **the load test is not done** | TRACKER:276 (PR1-1) |
| R1-11 nobody watches the audit trail | Medium | closed | TRACKER:267 |
| R1-12 `sync_valueset` and packs | Low | closed | TRACKER:239 |
| R1-13 compliance duties not gated | Medium | **open**, the owner's row is ⬜ | TRACKER:284; PLAN:315-322 (only P4-T7 is gated) → PR1-4 |
| R1-14 admin web screens | Medium | closed (owner click-through passed 22-11) | TRACKER:277-282 |
| R1-15 one alert inbox | Low | **open** (owner) | TRACKER:284 → PR1-4 |
| R1-16 7 against 10 years | refuted | — | — |
| R1-17 uncommitted work | Low | closed; P1-T25 committed, CI pending | `git log`; `gh run list` |
| R1-18 upstream drift | Low | partly: the drift check runs weekly; the trial sync is unmerged and the pins date from 01/02-11 | upstream.lock; QUESTIONS Q-K28 → PR1-9 |
| R1-19 owner backlog | Low | **open, grown**: 87 🟡 lines in the P1 board; 12 decisions unconfirmed | TRACKER:165-284; QUESTIONS:93-183, 1224-1269 → PR1-6 |
| R1-20 stale docs | Low | partly: the TRACKER phase status is stale again | TRACKER:137 → PR1-7 |
| R1-21 DHIS re-check | Low | moved into P1-T14 (open) | TRACKER:234 |
| R1-22 patient OTP in production | Low | closed (GAP row restated; P8-T1) | GAP_ANALYSIS:87 |

## Findings

Severity is the reviewer's own judgement. "Blocks" means: **Phase 2** (start of Phase 2 work),
**go-live** (the first pilot with real patient data) or **neither**.

### PR1-1 (High): nothing has run on a real server; the 8 GB budget is unproven

- **Evidence:**
  - Every operational task is "proven on the full local staging stack" on this 6.7 GB desktop:
    TRACKER:208, 214-217, 257, 261, 269-270.
  - Go-live is ⬜ (TRACKER:209).
  - The 8 GB load test is "never reported from this machine" (README:1581-1582; TRACKER:276).
  - The memory ceilings on the 8 GB values add up to about 7.1 GB for the core plus about 3 GB
    for the sidecars (README:1564-1567).
  - The terminology load limit is unsettled because of host swap (QUESTIONS:145-153).
  - `scripts/staging.sh` is 5,420 lines with 67 subcommands, almost none run against a provider's
    S3, DNS, SMTP or object lock.
  - The API has no per-hospital fairness: one backend of 2 workers × 4 threads serves every
    hospital (compose.yaml:54, 397-398), and Traefik limits only the sign-in routes
    (traefik/routes.yml:28-65).
- **Why it matters:** the first real bring-up will hit integration faults across many commands at
  once, at the worst moment, with a pilot waiting. The OOM ordering (`oom_score_adj`) is a
  mitigation, not proof that the stack fits in 8 GB.
- **Proposed task:** **P1-T26 Real-server dress rehearsal.** Rent an 8 GB India VM for a few days
  (owner pays; synthetic data only) and on it run:
  - `init`, `up` and `smoke`, plus `release` and `rollback`;
  - the backup restore drill and a `hospital-restore` drill;
  - the dumps, the log archive and the `*-probe` commands against the real provider buckets;
  - `load-test.py`, including a "one heavy hospital" case, and the terminology bench.

  Record the real memory use, then decide 8 or 16 GB, or move monitoring and logs off the box.
  About 12 agent hours plus 2 owner hours.
- **Blocks:** go-live.

### PR1-2 (Medium): the pilot environment is undefined

- **Evidence:**
  - CLAUDE.md says "No real patient data outside production".
  - ADR-003:13 says production "starts as Compose on a VM".
  - PLAN §3.2:163 says production is "managed Postgres or CloudNativePG on k3s/RKE2".
  - Everything built is named "staging", and every go-live gate (TRACKER:254-284) assumes the first
    pilot runs on that stack.
  - P9-T7 (PLAN:392) moves to k3s and CloudNativePG at about 10 tenants. That would re-platform the
    pgBackRest, dump, role and release tooling just built.
- **Why it matters:** as written, a pilot on "staging" breaks the no-real-data rule. Restore
  drills of real dumps need a defined place. The point at which Phase 1's ops layer gets rebuilt is
  not costed.
- **Proposed task:** **ADR-020 Environments for pilots.** The Compose stack becomes "production-1".
  A separate synthetic staging (or the drill copy) is defined. PLAN §3.2 is aligned with ADR-003.
  The trigger for P9-T7 is restated: a measured limit, not a tenant count. About 2 hours plus an
  owner decision.
- **Blocks:** go-live.

### PR1-3 (Medium): the operator's own access to patient data is neither logged nor bounded

- **Evidence:**
  - `staging.sh manage <args>` runs any `manage.py` command (`shell` and `dbshell` included) in the
    `admin` one-off, as the database **owner**, with `logging: driver: none`
    (staging.sh:5294, 5296; compose.yaml:282-294).
  - `pg_su` runs psql as `postgres` (staging.sh:402-407).
  - The audit trail records reads only through the request middleware (ADR-010 §4). Its "does not
    protect against" list (ADR-010:165-169) does not mention operator reads.
  - The server settings have no `log_connections` (deploy/postgres/server-settings.txt).
  - The host bootstrap has no auditd rule.
  - Writes by the operator are caught by the triggers; reads are invisible. P1-T25 itself recorded
    that operator lines never reach the log store (README:1775-1778).
- **Why it matters:**
  - Under the DPA (P4-T6) we are the hospital's processor. "Who looked at my record" (DPDP
    rights, NABH access logs) has no answer for our own staff.
  - CERT-In expects logs of privileged access.
  - A single operator with root is also the insider-threat case.
- **Proposed task:** **P1-T27 Operator access record.** Every admin one-off, `pg_su` and
  `docker exec` wrapper writes a journal line `ihr-operator: operator=… reason=… command=…
  hospital=…`, and `--reason` is required. Postgres logs connections for the owner and `postgres`
  roles. An auditd rule covers `docker exec`. A per-hospital audit event is written when a one-off
  opens a hospital schema. An alert fires on any `postgres` or owner login outside a release or
  restore. Opus, about 6-8 hours.
- **Blocks:** go-live.

### PR1-4 (Medium): the compliance gates and incident path are still not set (R1-13/R1-15 carried over)

- **Evidence:**
  - The owner row is ⬜ (TRACKER:284).
  - In the PLAN Phase 4 table only P4-T7 is a gate (PLAN:315-322).
  - No breach or incident runbook exists (P4-T2 ⬜, TRACKER:327).
  - Alerts go by e-mail to one person.
  - Nothing in our code refers to Aadhaar except the audit column classifier
    (`ihr_audit/install.py:79`). CARE's configurable identifiers would let a hospital admin store
    a full Aadhaar number today; the masking rule is planned only in P2-T2 (PLAN:274).
- **Why it matters:** the 6-hour CERT-In clock and the 72-hour Data Protection Board clock need a
  named contact, a second channel and templates before any real data. "Never store full Aadhaar"
  is a hard CLAUDE.md rule, and so far only a Phase 2 intention.
- **Proposed task:** the owner marks P4-T1, T2, T3 (minimum), T4 and T6 as gates and names a
  CERT-In point of contact. A **minimum P4-T2**: runbook, templates and a second alert channel,
  about 4 hours. P2-T2's done-when adds a refusal of Aadhaar-shaped values in identifier configs
  and values, tested.
- **Blocks:** go-live.

### PR1-5 (Medium, judged higher than recorded): one shared `ihr_app` role reaches every hospital's schema

- **Evidence:**
  - The P1-T19 review C2 says an injection through a served hospital could use rights left in a
    gated one ("mitigated, not removed").
  - The earlier review said the role split "protects schemas, triggers and the audit trail, not
    cross-hospital data (that is P4-T8)".
  - P4-T8 is ⬜ and not gated (TRACKER:333; PLAN:322).
- **Why it matters:** with two or more hospitals holding real data, one SQL-level escape (a CARE
  raw query, a future report feature) reads every hospital. This is the one tenancy control that
  is designed but neither built nor gated.
- **Proposed task:** make **P4-T8** (per-hospital role through `SET ROLE`) a gate before the
  **second** hospital with real data. Opus high, about 10-14 hours, with isolation-suite mutation
  rows.
- **Blocks:** go-live (the second real hospital).

### PR1-6 (Medium): decision and ADR debt underneath shipped code

- **Evidence:**
  - ADR-015, ADR-016 and ADR-018 are still "Proposed".
  - ADR-016's decisions (Q-F6…Q-F9) and ADR-015's (Q-O13, Q-O14) have been unconfirmed since 12-11
    (QUESTIONS:93-183; run files 2026-11-12). Later work builds on them: the P1-T10a2 loader and the
    Grafana stack.
  - ADR-018's decisions (Q-K34…Q-K46) are confirmed, but its status was never updated.
  - P1-T25 changed who is a hospital-wide admin, an access boundary, without any ADR text.
    ADR-010 §7 still says "an admin role on one of the hospital's **top-level, active**
    organisations" (adr/010:104), and CLAUDE.md says "top-level hospital admins".
  - Q-K126…Q-K131 await the owner.
- **Why it matters:** the ADRs are where a later reader (or a Phase 2 builder) learns the access
  rules. Today they describe a gate that no longer exists.
- **Proposed task:** **docs sync.** An ADR-010 amendment for the root gate (Q-K126…131).
  ADR-018 set to Accepted. ADR-015 and ADR-016 accepted or reversed after the owner answers. The
  CLAUDE.md audit line corrected. About 2 hours plus owner answers.
- **Blocks:** neither. ADR-016 must be settled before P1-T10b.

### PR1-7 (Low): the TRACKER's summary rows are stale

- **Evidence:**
  - The phase status says P1 is "built except P1-T1d, T11b/c…" (TRACKER:137), but both are 🟡.
  - Parent rows still show ⬜ while their children are 🟡: `P1-T4 CARE ↔ OIDC` (184), `P1-T8b`
    (203) and `P1-T9` (213).
  - The hospital switcher (TENANCY §9; R1-05) has no row.
- **Why it matters:** the owner decides Phase 1 acceptance from this table.
- **Proposed task:** a docs sync in the same pass as PR1-6, about 0.5 hours.
- **Blocks:** neither.

### PR1-8 (Low): the operational surface is large for one operator

- **Evidence:**
  - `staging.sh` has 67 subcommands; the README is 1,796 lines.
  - There are 153 alert rules (`deploy/monitoring/prometheus/rules`, `deploy/logs/alerts`), all
    sent to one inbox.
  - About 35 distinct secret files are named by compose and `staging.sh`.
  - Owner chores: `dumps-expire` weekly (README:419), `hospital-restore-expire` daily (README:574),
    `logs-archive-expire` monthly (README:1026), plus the monthly and quarterly drills.
  - The chores are alerted when overdue (DumpErasureOverdue, ParkedSchemaOverdue,
    LogArchiveErasureOverdue, DumpDrillOverdue), but no single command shows what is due.
- **Why it matters:** at 1-50 hospitals the first operational failure is more likely a missed
  chore or alert fatigue than a missing control.
- **Proposed task:** (a) an ops freeze: no new ops subsystem until PR1-1 is done. (b) A
  `staging.sh due` view listing chores, key ages and drills. (c) Alert tiers: page only on
  critical, with a daily digest for warnings. About 4-6 hours, Sonnet.
- **Blocks:** neither.

### PR1-9 (Low): upstream pins are three weeks old, and the trial sync is unmerged

- **Evidence:**
  - `upstream.lock`: backend 2026-11-01, frontend 2026-11-02.
  - Q-K28 kept `p1-t17-sync-trial` unmerged. It brings about 8,700 lines, including "token
    revocation, safer report rendering".
  - The drift report shows 10 and 9 commits behind (Upstream drift run 43786493820, 16-11).
- **Why it matters:** Phase 2 will add many CARE-touching patches, so every week of delay makes
  the first real sync harder, and upstream security fixes wait.
- **Proposed task:** rebase and merge the trial with the runbook before Phase 2's first
  CARE-touching task. About 3-4 hours.
- **Blocks:** Phase 2 (soft).

### Known limits checked and accepted as recorded

These were read and judged acceptable at the recorded severity. They are not re-raised:
- P1-T25: a root made invalid at run time is seen only at start-up, release or restore (A2); a
  superuser can unmark the root (Q-K131).
- P1-T19: refusals used as an availability lever (S2, S8).
- P1-T21: the bounded `measure` and checkpoint seeding.
- P1-T22b: the archive keeps replaced values until P4-T1; queued Celery tasks are not purged.
- P1-T23: the timeouts guard against accidents only.
- P1-T22c2b: deferred to go-live.

## Owner actions (one list)

1. **Confirm or reverse:** Q-K126…Q-K131 (P1-T25), Q-F6…Q-F9 (ADR-016) and Q-O13/Q-O14 (ADR-015).
   Also confirm the splits marked "awaiting owner confirmation" (P1-T15b, P1-T22c/c1/c2,
   P1-T24 a-d, the P1-T25 ID).
2. **Close Phase 1 with conditions:** approve moving the unmet criteria into a go-live gate list
   (PR1-1…PR1-5, P1-T22c2b, the go-live row).
3. **Click-throughs still waiting:** P1-T2b, T3, T4b, T5a, T5b, T5c, T5e1, T5e2, T6b, T7b, and the
   P1-T25 script (TRACKER "Test credentials").
4. **Reviews:** the Hindi sheets (T7c-T7f, batch 5); clinical terms in them.
5. **Compliance (R1-13/R1-15):**
   - mark P4-T1, T2, T3 (minimum), T4 and T6 as gates;
   - name a CERT-In point of contact;
   - add a second alert recipient or channel;
   - decide P4-T8 as a gate before the second real hospital (PR1-5).
6. **Environment (PR1-2):** decide where the first pilot's real data lives (ADR-020).
7. **Pay for and approve the rehearsal VM (PR1-1).** That VM also settles the 8 GB load test and
   the terminology 30-minute load.
8. **Go-live items already recorded:**
   - written confirmations: Zoho India-only storage (Q-E10), E2E MeitY empanelment;
   - ADR-007 RPO ≤ 15 min / RTO ≤ 4 h;
   - keys into the password manager: dump key, archive key, events key, owner DB password;
   - healthchecks.io and UptimeRobot accounts;
   - after the first release: read each hospital's `ihr-root:` line and `place` any `no-admin`.
9. **Gates of your own:**
   - P1-T14: 5-10 interviews, the DHIS re-check, Q-B4 (MVP module list);
   - P1-T10b licences: SNOMED CT India, LOINC, ICD choice, a drug list;
   - the PIN directory file from data.gov.in;
   - Q-B6/Q-B7 (pricing, timeline).

## What could not be checked

- **Behaviour:** no tests, stacks or builds were run. All test counts are as reported in the
  build notes and CI.
- **CI for P1-T25:** run 45034897010 ("CI") passed; run 45034896999 ("Apps CI") was still queued at the
  time of review.
- **Upstream code:** CARE (`apps/`) was not reviewed beyond our gate's dependence on CARE's
  role-organisation rules. Those rules are pinned by tests, per the P1-T25 notes; the pin itself
  was not re-checked.
- **Code depth:** most security code was not re-read line by line. It has been through the full
  review shape (lenses, challengers, verifier, mutation rows) per task. This review sampled
  `roots.py` (valid_root, marked, is_valid), `staff_access.py` (the gate, NotCrossSite),
  `release_gate.py`, the tenancy settings and the staging admin one-off.
- **Provider facts:** E2E and Zoho data-residency claims, and object-lock semantics on the real
  provider.
- **Hindi translations** and any clinical content (the owner's review).
- **Live proofs:** whether the P1-T18b release drill and the P1-T22b restore drill would pass on a
  server with real network, DNS and TLS (see PR1-1).
