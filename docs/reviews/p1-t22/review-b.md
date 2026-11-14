# P1-T22b independent code review (2026-11-14)

Reviewers who did not write the code read the single-hospital restore (engine, package loader,
files, drill), the quarantine, the superseded-segment archive, the restore hold and its uses, and
the staging commands. Each reported finding was then challenged by a second agent that tried to
refute it. Of 18 findings: 1 refuted (S3), 4 confirmed in part (S7, S8, C7, C8), 13 confirmed. A
fixer agent fixed the 17 that stood. A verifier then checked each fix against its failure scenario:
it closed 12, found 5 only partly closed (S2, S7, C4, C7, C8) and 5 new problems. A second round
closed all of them.

| # | Finding | Verdict | Fix (verified) |
|---|---|---|---|
| S1 | A dump's DEFAULT and CHECK expressions reach the live schema, and calls into `pg_catalog` are never checked: a crafted dump could copy another hospital's rows into this trail or reset its sequences | confirmed, high | the quarantine and the load read every stored expression and allow only immutable built-ins plus a short list (`nextval`, `now`, `gen_random_uuid` and similar) on the schema's own relations; CHECKs, exclusion constraints and expression indexes are built on empty tables and checked before any row loads; the trail's defaults come from code (`ensure_defaults`); two residuals (a runtime `regclass` cast, operator classes) closed in round two |
| S2 | The package's statement check anchors only the start, and statements run as multi-statement queries | confirmed, medium | statement forms matched whole after a small lexer; each statement sent alone through the extended protocol; the quarantine prints the package sha256 and the restore checks it again; round two: a constraint is matched as exactly one constraint (no `, ALTER COLUMN ... USING (...)`, `OWNER TO` or `DISABLE TRIGGER` tail); mutation row `restore-constraint-form` |
| S3 | A restored `awaiting_setup` flag re-activates a user the parked copy had deactivated | refuted | none needed: the security step only switches users off and never clears the flag, and the restore finishes with the sign-in links reset |
| S4 | The strict merge leaves out direct patient grants (`emr_patientuser`), so revoked patient access returns | confirmed, medium | grants and patient shares are merged; the affected patients' access caches are rebuilt; mutation row `restore-patient-grants` |
| S5 | Live audit entries not yet sealed at park time are neither archived nor refused, and are dropped after 7 days | confirmed, medium | a final seal inside the load transaction up to every finished transaction; any entry left unsealed rolls the park back; mutation row `restore-final-seal` |
| S6 | Sequences are advanced only after migrate has written rows and audit entries with the dump's sequence values | confirmed, medium | sequences advanced right after the load, in the same transaction; mutation row `restore-sequences-before-migrate` |
| S7 | The restored chain is checked only after the point of no return, and a dump that claims seal 0 skips the prefix check | partly, low | the restored chain is recomputed and the dump's unsealed entries compared with the parked ones before commit; round two: two forged-package tests end to end; mutation row `restore-chain-recomputed` |
| S8 | Queued protective staff requests made after the dump are cancelled even when the user still exists | partly, low | sign-in resets and invitation withdrawals for a user the restored copy still has stay queued and are named in the output |
| S9 | The decrypted dump stays on the server's disk when the run stops after fetching it | confirmed, low | disk checked from the manifest before anything is fetched; an EXIT trap removes the decrypted dump; the drill writes no password file |
| C1 | A release during an unfinished restore leaves the hospital held with no way out | confirmed, medium | each step after migrate re-migrates a schema that is behind this build; `release` and `rollback` refuse while a restore is unfinished |
| C2 | A lock conflict records a fake `lock` step that makes the restore impossible to abort | confirmed, medium | a busy hospital is refused before any operation exists; a lock failure is not a step; abort ignores names that are not steps |
| C3 | Sequences advanced only after migrate, roles and packs, so ids are reused and the strict merge compares unrelated rows | confirmed, medium | as S6 (the strict merge now runs in the same transaction, before migrate) |
| C4 | The drain does not stop Celery or worker jobs already running, so their later writes land in the restored schema | confirmed, low | every Celery task and staff-worker job holds a per-hospital shared lock while it runs (`release.job`); the drain waits for it and stops naming the job; round two: the lock is a try-lock so no job queues behind a waiting drain; mutation rows `restore-drain-jobs`, `restore-job-lock-celery`, `restore-job-lock-staff-worker`, `restore-job-lock-no-wait` |
| C5 | The "rolling restart" restarts every app container at once | confirmed, medium | no app restart by default; `--restart` opts in (exit 3) |
| C6 | Every resume re-reads and re-hashes the whole package, even when the load is done | confirmed, low | a resume after the load never reads the package |
| C7 | The drill's "others unchanged" hash skips schema shape and public functions, and scans the whole cluster twice | partly, low | the hash covers every schema's shape and public's functions and triggers; round two: other hospitals' rows are stood for by per-table write counters from the statistics (a rolled-back write counts), not a full scan |
| C8 | The tests never exercise resume or crash recovery, and one sequence assertion is always true | partly, low | resume and crash-state tests (including "only parked"), a changed-archived-entry test, a lost-schema test; the assertion fixed; trail-defaults test with mutation row `restore-trail-defaults` |
| C9 | A failure in the Keycloak step raises no HospitalRestoreFailed alert | confirmed, low | the admin one-off marks its failures as unlogged; the next logged run writes the alert line (`--report-failure`, every `--resume`) |

## Second round

The verifier found five fixes only partly closed and five new problems. All were closed by a
second fixer, each with a regression test, and the new security and lock controls each have a
mutation row whose named test must fail (7 new rows; the check lists 77 in all).

- **S2 residual (security):** a constraint statement could carry more `ALTER TABLE` subcommands
  after a comma, so a `USING (...)` expression ran once per loaded row. Constraints are now matched
  as exactly one constraint of their kind, parentheses emptied, with a foreign key's schema taken
  from the form.
- **S1 residual:** a `nextval` over a runtime `name` or `oid` cast of a sequence of another hospital
  left no dependency record. `nextval` is accepted only over a regclass constant; operators of
  exclusion and foreign keys go through the function check; an index with an operator class is built
  on the empty table and inspected first.
- **S7, C4, C7, C8 gaps:** covered as in the table.
- **New: the "load changed other schemas" comparison** failed a restore whenever the provisioner
  onboarded a hospital meanwhile. It is now a set-based catalogue version per schema (32 ms for 8
  hospitals plus public), ignoring new schemas and another restore's hospital.
- **New: a dump from an older build** lacking a column the strict merge names blocked the restore.
  The precheck refuses it up front; columns the older build lacks are skipped and named.
- **New: a held hospital's Celery tasks queued behind a waiting drain** and took worker slots. Fixed
  by the try-lock (C4).

## Final results

- App-role mode: Ran 754 OK (skipped=45); roles off: Ran 720 OK (skipped=49).
- Dump and release script tests 169 OK; quarantine static tests 20 OK; live dumper tests 91 OK;
  live quarantine tests 20 OK.
- check-logs OK; isolation-mutation check (77 rows) OK; pre-commit (43 hooks) OK.
- Mutations applied by hand were reverted and the files compared byte for byte.

Left open, all recorded under "Known limits" in design.md (section "P1-T22b as built"): the app role
cannot verify the trail after a restore (the release pre-check warns); the archive in `public` keeps
the replaced entries' values, and its DPDP erasure and offboarding are not built (P4-T1); queued
Celery tasks are not purged; Keycloak members added after the dump are reported, not removed; the
live compare sees DDL, not rows, and a fleet-wide DDL during a restore stops the load (resume after
it); the drill trusts the statistics' write counters; the quarterly live drill is documented only;
the staging proof was not repeated after the review fixes.

Design attack: design.md (P1-T22). Proof: proof-b.md.
