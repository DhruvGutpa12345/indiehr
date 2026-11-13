# P1-T18a independent code review (2026-11-13)

Two fresh Opus reviewers who did not write the code (correctness and tests; security, tenancy and
operations), then a third agent tried to refute each finding. Of 20 findings: 0 refuted. A fixer
agent fixed them, and a fourth agent checked each fix against its failure scenario.

| # | Finding | Verdict | Fix (verified) |
|---|---|---|---|
| S2 | A hospital held by a start-up role refusal is never released, even by the alert's own remedy (--resume) | confirmed, high | as C4 (two mutation rows) |
| C1 | Fleet-wide checks run only after hospitals are migrated, and any fault after migrate leaves every hospital held | confirmed, medium | a preflight checks the public schema and the packs before anything is held; a pack fault after migrating still serves the migrated hospitals |
| C2 | Startup check faults the whole fleet for one hospital whose release migration already failed | confirmed, medium | a hospital this release already left failed and held no longer stops the start |
| C3 | --resume finds nothing to resume after a killed or interrupted run | confirmed, medium | runs start "running"; interrupts close the run; `--resume` takes up killed runs; leftover children and their sessions are stopped |
| C4 | A hospital held for a refused role sync is never released by --resume or a later start, but is reported as succeeded | confirmed, medium | holds carry a reason; a roles hold is released once roles pass; an unserved hospital is never reported as done |
| C5 | The migration being applied is never recorded: django-tenants' per-line signal never fires under redirect_stdout, and the test fakes it | confirmed, medium | the migration being applied is recorded by wrapping the executor (real failing migration tested) |
| C6 | The wall-clock limit kills the client, not the migration, and --parallel 1 has no limit at all | confirmed, medium | the child reports its database session; on a time limit or crash it is terminated before anything else; `--parallel 1` also forks |
| S1 | Celery tasks never check servable(): queued CARE tasks run in a held or half-migrated hospital | partly, medium | queued Celery tasks never run in a held or behind hospital: re-sent 60 s later (our code, no upstream patch; mutation row) |
| S3 | Manual and resumed fleet runs never reach the HospitalNotServed alert; the alert also self-resolves after 24 h while the hospital is still down | partly, medium | `staging.sh fleet` runs in a logged container; the worker repeats the line every minute for each live unserved hospital (window holds left to the window budget alert, by Claude after the verify); the rule looks back 10 min |
| S4 | The start-up one-shot's --record writes "" for behind hospitals while the old gated backend is still serving: a fleet-wide outage on any `up` of a build with new migrations | confirmed, medium | `--record` never writes a fingerprint for a schema that is not current; the start refuses instead |
| S7 | safe_error keeps the first line of non-database exception messages, so row values reach the console, logs and the 400-day archive; roles errors skip it entirely | partly, medium | error records keep no message text from non-database exceptions; roles errors go through the same helper |
| C10 | Tests for the signal path and the plan comparison are near-vacuous | partly, low | partly: signal path and plan tests made meaningful; the squashed-migration row case is a documented limit (rare) |
| C7 | Every start writes a critical 'held' alert line for non-live hospitals whose schema is not this build's | confirmed, low | no alert line for hospitals not served by design (provisioning, offboarding) |
| C8 | No guard against two fleet runs at once, or resuming a run that is still running | partly, low | one fleet run at a time (fleet-wide lock); a stale running row is closed |
| C9 | The subprocess fallback rebuilds DATABASE_URL and drops the database OPTIONS; manage.py is resolved from the cwd | partly, low | the fallback keeps `DATABASE_URL` whole and finds manage.py from the settings |
| S10 | The console hides upgrade records and hold state, and still reads a held hospital's schema with no lock bound | partly, low | the console shows holds, upgrade records and fleet runs, and never reads a held hospital's schema |
| S5 | Fleet-wide faults are checked after every hospital has been migrated, and a fault then leaves the whole fleet held | partly, low | as C1 |
| S6 | Operator commands skip servable(): ihr_pack_apply (the packs alert's remedy) and the other --all commands write into held or behind schemas | partly, low | operator commands (`ihr_pack_apply`, geography, profile, staff link) refuse or skip a held hospital |
| S8 | Critical HospitalNotServed fires for provisioning or offboarding hospitals that are not served by design (false positives on every start) | confirmed, low | as C7 |
| S9 | The wall-clock limit kills the forked child but not its Postgres session, so the migration and the hospital's advisory lock outlive the limit | partly, low | as C6 |

Design attack: design.md ("After the attack": 24 findings, 0 refuted, none fatal).
