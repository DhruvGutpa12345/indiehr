# Phase 1 review: independent second opinion

- **Reviewer:** independent Opus review agent (did not do the work; changed no files).
- **Date:** 2026-11-12, in the unattended run docs/runs/2026-11-12-run-to-P1-T11.md.
- **Scope:** CLAUDE.md, PLAN.md §2/§6, TRACKER.md, QUESTIONS.md, ADR-000…017, TENANCY_DESIGN.md,
  GAP_ANALYSIS.md, phase-0-review.md, the staging stack (`deploy/staging/`, `scripts/staging.sh`,
  `scripts/deploy.sh`), `plugins/ihr_platform/*`, the CI workflows, and the then-uncommitted
  P1-T10a2 and P1-T11a work. Q-F6 treated as open.
- **Challenge:** each finding was then given to separate agents told to refute it. The verdict of
  that challenge is in the "Challenge" section at the end; only findings that survived it became
  tasks.

## Verdict

1. **The security core is strong and well evidenced.** It has an isolation suite with 19 mutation
   runs, staff sessions held by the backend with MFA and `private_key_jwt`, a trigger-based audit
   trail with outside fingerprints, point-in-time backups with a restore drill that checks the
   audit trail, and a redacted log record kept 180 days with a 400-day archive.
2. **Phase 1 is built but not closed.** 52 items are 🟡 waiting for the owner. P1-T10a2 and
   P1-T11a were not committed and had not run in CI. P1-T1d, P1-T10b, P1-T11b/c, P1-T14 and the
   go-live (the "one-click staging URL") are still open.
3. **The real gaps are operational and were not recorded.** There is no safe upgrade or rollback
   path, and one database role owns everything. Four TENANCY_DESIGN rules (§6.10, §6.11, §7.2,
   §7.5) are not followed by the staging stack, and nothing records why.
4. **A first paying hospital is blocked by those gaps,** plus the Phase 4 duties that are not
   marked as gates (DPDP notice and consent, the 6-hour CERT-In / 72-hour breach runbook, the DPA)
   and the audit and staff screens, which so far exist only as APIs.
5. **Phase 2 can start** once Q-K21 is answered, the open work is committed, and a reference-data
   "pack" task exists. **Grade: B+.**

## Plan conformance

| Plan item | State | Differences recorded? |
|---|---|---|
| T0, T1a–c, T2a/b, T3, T4a/b, T5a–f, T6a/b, T7a–f, T12 | Built, mostly 🟡 | Yes (ADR-008…014, Q-K*, Q-O*) |
| T1d Playwright, two subdomains | **Not started.** Unblocked since P1-T4b | — |
| T8a–d2 | Proven locally. **Go-live not done**, so "one-click staging URL" is not met | Yes: ADR-011, cost decision with the owner |
| T9a–d | Proven locally | Yes (ADR-013/014/015; ADR-015 Proposed) |
| T10a1 / a2 / b | Done / in progress / blocked on licences | ADR-016 Proposed |
| T11a / b / c | Done, uncommitted at review time / blocked on Q-K21 / pending | ADR-017 Proposed |
| T14 customer discovery | Not started. The owner made it a pre-Phase-2 gate (Q-S2) | — |
| DB roles, upgrade gate, per-hospital dumps, Postgres limits, own back-channel pool | **Built differently from TENANCY_DESIGN / PLAN P1-T8** | **No** |

## Findings: security and tenancy isolation

| ID | Sev | Finding | Evidence | Follow-up |
|---|---|---|---|---|
| R1-01 | **High** | **The web app runs as the database owner.** One role, `ihr`, owns the database and every table and serves requests. TENANCY §6.10 requires `ihr_owner` (schema changes) and `ihr_app` (data only). So code execution or SQL injection in the app could disable the audit triggers, write to "read-only" terminology, and change schemas. The terminology guard is a session flag that any session can set. | `deploy/staging/initdb/10-roles.sh:9-10`; `scripts/staging.sh:196`; TENANCY_DESIGN.md:121; `ihr_terminology/migrations/0002_read_only_guard.py:7,23` | P1-T19 |
| R1-02 | Medium | **Anonymous routes still need hardening** (P1-T1b follow-ups, ⬜ since 06-11). `schedulable_users` shows staff usernames, last login and roles to anyone, which is enough to lock doctors out through Keycloak's brute-force lock. `govt/organization` returns every organisation, while §6.6 says government geography only. | `care/emr/api/viewsets/facility.py:187-198`; `organization.py:57-69`; `anonymous_allowlist.txt:21-23` | P1-T20 |
| R1-03 | Medium | **Three designed patches are not built:** the batch sub-request path allow-list (the batch endpoint now forwards the session cookie), presigned logo/avatar/cover URLs (Q-T3), and separate credentials for the patient and report buckets. | `care/emr/utils/batch_requests.py:11-20`; TENANCY_DESIGN.md:116,119, §10 | P1-T20 |
| R1-04 | Medium | **The crawl does not reach everything.** 34 models CARE's fixtures leave empty are never seeded, so their detail routes are never checked for cross-tenant leaks. | `crawl_skips.txt`; TRACKER | P1-T16 |
| R1-05 | Medium | **Isolation in the browser is untested.** Token isolation, logout clean-up (Q-T9) and the switcher have only been clicked through by Claude. | PLAN P1-T1d | P1-T1d |
| R1-06 | Low | **The mutations miss four controls:** audit trigger coverage, the refusal of outbound terminology queries, the guarded role sync, and the `PATIENT_GLOBAL_EDIT` refusal. | `scripts/isolation-mutation.sh list` | P1-T16 |

Sound: identity (ADR-008/009/012), egress lockdown (bundle check, terminology answered in-process
with the black-hole check), the Redis keyspace audit, CARE's login routes blocked in the URL
configuration so batch requests are covered too (`ihr_identity/blocked.py`).

## Findings: operations

| ID | Sev | Finding | Evidence | Follow-up |
|---|---|---|---|---|
| R1-07 | **High** | **No safe upgrade or rollback path.** `up` runs `migrate` for every hospital while the old backend keeps serving. There is no migration-fingerprint gate (503), no `ihr_fleet_run` and no maintenance window, although TENANCY §7.2 and ADR-002 Am.1 #6 require them. If hospital k of N fails, schemas are left mixed. `deploy.sh` deletes the previous images. No rollback runbook exists. CARE's migrations only go forward, so the only undo is a cluster-wide restore. | `compose.yaml:203-215`; `staging.sh:278-287`; `deploy.sh:253`; TENANCY_DESIGN.md §7.2 | P1-T18 |
| R1-08 | Medium | **Only the whole cluster can be restored.** The nightly per-hospital dumps, the single-hospital restore runbook and the erasure ledger (TENANCY §7.5, ADR-002) are not built. | TENANCY_DESIGN.md:159; README §backups | P1-T22 |
| R1-09 | Medium | **The P1-T1c operating rules were never applied** ("apply in P1-T8"): `max_locks_per_transaction` is the default 64 (256 needed), shared memory 256 MB (at least 1 GB needed), no `statement_timeout` (§6.16). | `compose.yaml:129-167`; `docs/reviews/p1-t1c/scale-results.md` | P1-T23 |
| R1-10 | Medium | **Capacity on the 8 GB machine is unproven.** Memory limits across the staging compose files add up to about 8.4 GB. Two gunicorn workers serve every hospital; back-channel logout shares that pool, although PLAN P1-T8 required its own pool. | `compose.yaml:41,46`; PLAN P1-T8 row | P1-T23 |
| R1-11 | Medium | **Nobody watches the audit trail's integrity.** Sealing and fingerprinting run on beat, but `ihr_audit_verify` runs only by hand, and no alert covers seal lag, a late fingerprint or a broken chain. | `ihr_audit/tasks.py:19-26`; `deploy/monitoring/prometheus/rules` | P1-T21 |
| R1-12 | Low | **`up` runs `sync_valueset` in every hospital, against §7.2.** Harmless today, but upstream value-set changes never reach existing hospitals, and the §5 pack mechanism (PackRelease) has no PLAN task; Phase 2 masters depend on it. | `compose.yaml:215`; TENANCY_DESIGN.md §5, §7.2 | P1-T15 |

Sound: pgBackRest (28 days point-in-time, WAL at least every minute, encrypted, refuses to start an
empty database), the restore drill, the host bootstrap, key rotation, `smoke`, and log and backup
alerts.

## Findings: compliance

| ID | Sev | Finding | Evidence | Follow-up |
|---|---|---|---|---|
| R1-13 | **High** (go-live) | **Duties that gate a first paying hospital are not marked as gates:** P4-T1 (DPDP notice, consent, requests), P4-T2 (6-hour CERT-In / 72-hour DPB runbook), P4-T6 (DPA and terms), P4-T4 (encryption at rest and a secret manager) and P4-T3 (minimum retention). Only P4-T7 is gated. No incident runbook exists. | PLAN §6 Phase 4; ADR-007 | Go-live gate list |
| R1-14 | Medium | **Hospital admins cannot answer "who looked at my record" without the API.** The audit viewer, invitations and staff recovery have no web screens, and no task row exists. | TRACKER header | P1-T24 |
| R1-15 | Low | **The 6-hour CERT-In clock depends on one person's inbox.** Critical alerts go only by e-mail to the owner. | `alertmanager.yml` | Go-live gate list |
| R1-16 | Low | **The fingerprint check accepts less than ADR-010 asks:** 7 years of bucket retention; ADR-010 §9 specifies 10. | `ihr_audit/anchor.py:38` | Later |

Sound: data residency, CERT-In 180-day logs with NTP to NIC/NPL, the DPDP one-year logs through the
400-day archive, and a per-hospital audit trail that records reads.

## Findings: maintainability, docs and process

| ID | Sev | Finding | Evidence | Follow-up |
|---|---|---|---|---|
| R1-17 | Medium | **31 paths were not committed** at review time (the new `ihr_roles` app, terminology migrations, `rf2.py`), so they had not run in tenant-mode CI. | `git status` | Before Phase 2 |
| R1-18 | Medium | **Upstream drift is growing and no sync has been rehearsed.** 75 upstream paths patched; pins date from 01/02-11 and have never been synced, though PLAN §8 promises monthly syncs. | `UPSTREAM_PATCHES.md`; `upstream.lock` | P1-T17 |
| R1-19 | Medium | **The owner's review backlog is large.** 52 items are 🟡; ADR-015/016/017 Proposed; 7 decisions auto-accepted. | TRACKER; QUESTIONS.md | Owner |
| R1-20 | Low | **Several docs are stale:** the TRACKER header and phase status; `deploy/staging/README.md` says "journald one year" though ADR-014 makes it a 14-day buffer; ADR-002's body still promises per-schema dumps. | as cited | Doc sync |
| R1-21 | Low | **Two Phase 0 strategy items are in no plan row:** R0-16 (re-check DHIS) and R0-17 (downtime kit). | PLAN | Doc sync |
| R1-22 | Low | **The patient OTP sign-in is public but will fail in production** (`IS_PRODUCTION` on, `USE_SMS` off). | `care/emr/api/otp_viewsets/login.py` | Later (P8) |

## Is Phase 2 ready to start?

Partly.

- **Ready now:** CARE-native work, such as P2-T6 GST-safe invoice numbering and the billing rules.
- **Needs the owner first:** P2-T2 (registration) and P2-T6 (billing) need the non-clinical roles
  (Q-K21 → P1-T11b).
- **Needs P1-T15 first:** P2-T1, T5 and T13 need a way to ship reference data to every hospital.
- **Needs licences first:** P2-T4 and P2-T5 need SNOMED and a drug list (P1-T10b).
- **The owner's own gate:** P1-T14 before Phase 2.

## Owner decisions blocking progress (reviewer's priority order)

1. **Q-K21 🔴**: what non-clinical roles may see (blocks P1-T11b; shapes P2-T2/P2-T6).
2. **P1-T14 / Q-S2**: the 5–10 hospital interviews; confirm Q-B4 (MVP module list).
3. **Phase 1 acceptance**: the 🟡 backlog and click-throughs; confirm Q-K22 (ADR-017).
4. **Q-F6** (benchmark verdict) and Q-F7–F9 (ADR-016).
5. **P1-T10b licences**: SNOMED CT India, LOINC, ICD-10 or ICD-11, a drug list.
6. **Go-live**: confirm ADR-007's RPO ≤ 15 min / RTO ≤ 4 h; decide when to pay for E2E; written
   confirmation of Zoho's India-only storage and E2E's MeitY empanelment.
7. **Q-O13 / Q-O14** (ADR-015): low urgency.
8. **Q-B6 / Q-B7**: pricing and timeline.

## Challenge (2026-11-12)

Each finding went to two separate Opus agents told to refute it. One checked the **evidence**
(every cited file and line, plus anything that already handles it); the other checked
**severity and scope** (is it recorded already, is the follow-up right). Their full answers are
kept in the run's scratchpad and summarised here. **Result: 1 refuted, 3 confirmed as written,
18 true but corrected** (mostly lower severity, already tracked, or mis-cited). The corrected
version below is what became tasks.

| ID | Verdict | Severity after challenge | Corrections that matter |
|---|---|---|---|
| R1-01 | confirmed | **High** | The audit triggers would be caught later by `ihr_audit_verify` and the hourly fingerprints, so tampering is detected, not prevented. The fix must also make the terminology tables SELECT-only for the runtime role (a role split alone leaves the session-flag bypass), give the provisioning worker the DDL role or a separate DDL job, and refuse to serve when the runtime role owns tables. It protects schemas, triggers and the audit trail, not cross-hospital data (that is P4-T8). |
| R1-02 | partly | Medium | Already open at TRACKER (P1-T1b follow-up). The leaked CARE username is only the local part of the staff e-mail; Keycloak's lock is temporary (5 failures → at most 15 min, per-IP limits, alerted), so the risk is repeated short lockouts if the domain is guessed, plus staff enumeration. |
| R1-03 | partly | Medium | Already open at TRACKER (P1-T1b follow-up); the shared credential is a recorded risk (§6.8). "Logo" is wrong (served from the database at `/api/ihr/logo/`). **New and unrecorded:** staging's facility bucket is public-read for avatars and covers, against the owner's Q-T3 answer ("presigned, no public bucket"). The batch allow-list is defence in depth (each sub-request is re-authenticated; a batch isolation test passes). |
| R1-04 | partly | Low | 34 *routes* (about 30 models, two of them nested list routes), already an open follow-up. **New:** Django path-converter routes (`ihr_audit` `entries/<int:pk>/`, `pincode/<str:pincode>/`) are never filled: `routes.py`'s `PARAM_RE` reads only `(?P<…>)`, so they are requested with the placeholder left in and never counted as skipped. That gets past the fail-closed skip list (checked by Claude: `routes.py:7`). |
| R1-05 | partly | Low | P1-T1d is already tracked. Server-side token isolation is covered by the suite. The hospital switcher is not built and has no task row. |
| R1-06 | partly | Low | The four controls have direct negative tests; what is missing is mutation proof that those tests are not vacuous. |
| R1-07 | confirmed (evidence) / partly (scope) | **High**, go-live gate | Images can be rebuilt from the deployed git SHA, but no tested "redeploy previous" exists; the only rollback text is the roles section. |
| R1-08 | partly | Low now, go-live gate | Per-schema dump and restore were proven in the P1-T1c bench; the nightly job, the command, the runbook and a drill are missing. The erasure ledger belongs with P4-T1. |
| R1-09 | partly | Low | Shared memory 256 MB already covers the measured cap of 100 hospitals; `statement_timeout` (§6.16) is the part that matters; the terminology engine has its own. |
| R1-10 | partly | Medium | The limits are ceilings (2.2 GB measured idle, README); the backend is 2 workers × 4 threads. Missing: a load test on an 8 GB target and a recorded decision for sharing the back-channel pool. |
| R1-11 | confirmed | Medium | — |
| R1-12 | partly | Low | New system value sets do reach existing hospitals; *changed* ones do not. P1-T15 (versioned reference packs) is still the right task before Phase 2 masters. |
| R1-13 | confirmed (evidence) / partly (severity) | Medium | The trigger is ADR-007's "before the first pilot with real data", not the synthetic staging go-live. Backup encryption is done; disk encryption and a secret manager remain. |
| R1-14 | partly | Medium | Invitations are sent from CARE's Add user screen and forgot-password is self-service; missing are the audit viewer, invitation resend/cancel, "reset sign-in" and the access-status screens. Leaving out the buttons was the owner's choice (P1-T5e2, "API now, buttons later"); no task row owns them. |
| R1-15 | partly | Low | E-mail-only alerting is the owner's decision (Q-E6/Q-O4), with an outside dead-man switch and UptimeRobot; the gap is a single responder and channel. |
| R1-16 | **refuted** | — | ADR-010 §9 deliberately pairs a 7-year verifier floor with a 10-year bucket target, and every provisioning path sets 10 years. Raising the floor is optional and would be the owner's call. |
| R1-17 | partly | Low | The run's own closing steps (tests, commit, CI); done at the end of this run. |
| R1-18 | partly | Low | P0-T4 rehearsed a sync in a scratch repo; the pins are 10–11 days old, so the monthly cadence is not overdue. Missing: a real trial sync and a scheduled drift check. |
| R1-19 | partly | Low | About 49 🟡 task rows; the backlog is the designed output of unattended runs. |
| R1-20 | partly | Low | ADR-002's per-schema dumps are still the design (not stale). The TRACKER header and README:306 were stale; **README:306 fixed in this run.** |
| R1-21 | partly | Low | R0-17 (downtime kit) was declined by the owner (Q-S3, ADR-000), so it needs no row. R0-16's DHIS re-check is untracked, while PLAN §1.1 and P3-T12 still assume DHIS incentives. |
| R1-22 | confirmed (evidence) / partly (scope) | Low | It fails closed (a 500, no code issued), so there is no security risk. Public booking and PIN auto-fill on the patient token depend on it too; GAP_ANALYSIS should show it as 🟡. Follow-up is P8-T1 (a DLT SMS provider). |

### Tasks added to TRACKER from this review

**Before Phase 2:** P1-T15 reference-data packs; P1-T16 isolation coverage (seed the skipped
models, fill path-converter routes, mutation rows for four controls); P1-T17 trial upstream sync and
drift check; a docs sync (TRACKER header, GAP_ANALYSIS row for SMS, the DHIS re-check).

**Before the first pilot with real data (go-live gates):** P1-T18 release and rollback; P1-T19
database roles; P1-T20 anonymous routes and the §6.5/§6.8 patches, including the public-read
facility bucket against Q-T3; P1-T21 audit watch; P1-T22 single-hospital restore; P1-T23
Postgres operating rules and capacity on 8 GB; P1-T24 admin web screens; and the compliance gates
P4-T1/T2/T3 (minimum)/T4/T6 plus a second alert channel (owner).
