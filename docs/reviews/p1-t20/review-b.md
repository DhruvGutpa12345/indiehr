# P1-T20b independent code review (2026-11-14)

P1-T20b covers signed picture links (24 h), removing the public-read pictures bucket, and separate
storage keys: `ihr-care-files` for patient files and `ihr-care-pictures` for pictures. The old
combined key is retired.

Two fresh reviewers who did not write the code (correctness and tests; security, tenancy and
operations) found 9 findings, then a third agent tried to refute each one. 0 were refuted. A fixer
agent fixed them, a verifier checked each fix against its failure scenario (it closed 8, partly
closed C4 and raised 5 new problems), and a second fixer closed C4 and all five.

| # | Finding | Verdict | Fix (verified) |
|---|---|---|---|
| S1 | On any server set up before P1-T20b, every `deploy.sh --release` fails at the migrate step with every hospital held: the keys were split only inside `up`, which `release` reaches after migrate | confirmed, high | the keys are split once on the deploying machine (`staging.sh s3-split-keys [--check]`); `deploy.sh` refuses unsplit keys before sending anything; `up`, `release` (before the hold), `db-roles --migrate` and `rollback` refuse them before any change |
| C1 | Rolling back to a release from before P1-T20b breaks patient files, and nothing refuses it | confirmed, high | `rel_target_pre_s3split`: such a rollback (both kinds) is refused before anything changes once the site is split; the advice never names it (fix forward) |
| S2 | CARE's storage keys are regenerated on the server at every deploy, so the deploying machine never holds the live keys | confirmed, medium | a server never makes CARE's keys; only a local proof (`.local`) splits on `up` |
| S3 | A rollback to a release from before P1-T20b cannot start object storage, and nothing guards against it | confirmed, medium | as C1 |
| S4 | The reviewed anonymous-route list still says the finder's cover is an unsigned link with the raw key | confirmed, low | list header and entries now describe signed links; a test pins the text |
| C2 | `care-dev.sh` splits the keys on every command, so a command run before `up` gives containers keys the running gateway does not know | confirmed, low | after a split it recreates the running gateway first, then the running backend and celery |
| C3 | The static test "every start of the s3 container migrates first" cannot fail for a new unguarded call site | confirmed, low | checks each function body, resolves dependent services; a self-test with negative cases (see second round for the gaps found next) |
| C4 | Docs overstate the web app's fallback and the 404-after-delete behaviour | confirmed, low | design.md corrected and pinned by a test; the same claim in a code comment closed in round two |
| C5 | A new boto3 client is built for every picture on a cache miss, so cold lists slow down | partly, low | one client per signing identity (key id, secret hash, endpoint, region), at most 8; recorded in TENANCY §6.3 |

## Second round (verifier)

The verifier closed 8 findings and found C4 only partly closed, plus 5 new problems. The second
fixer closed all six, each with a regression test that fails when the fix is undone.

| # | New problem | Fix |
|---|---|---|
| N1 | Once a server is split, the first release onto P1-T20b has no way back if it stops at stage 1 (before any migration): the only advice was "fix forward" while every hospital answers 503 | `rel_outgoing_storage` compares the key ids the running backend and gateway were started with (no key printed); at stage 1 the advice names `fleet release` then `fleet window --clear` in the outgoing build, and says it holds only until the gateway restarts. After the roles move, `fleet release` and `fleet hold` go to the outgoing build (`rel_outgoing_unmigrated`) |
| N2 | During a release, `rel_repo_info` fell back to `repo_info`, which recreated the gateway with the split keys before `up`, killing the old backend's key | `repo_info --keep-running` puts `--no-recreate` on its `compose up` calls; the release fallback uses it |
| N3 | `storage.py` still said a deleted picture's link "simply answers 404" (403 on infra/aws) | comment corrected; a test checks it against the design |
| N4 | The static check missed a compose start split over a backslash continuation or with services from an array | continuations are joined, `"${name[@]}"` is resolved to its literals, an unresolvable array counts as starting s3; 12 new self-test cases |
| N5 | `care-dev.sh` recreated the gateway but did not run the bucket set-up (pictures bucket kept its public-read policy until the next `up`), and missed a backend running with s3 stopped | the split now also starts the gateway when only the backend or celery runs, recreates them and runs `setup_buckets.py` at once; a failed set-up says so |

## Environment note

CARE's own storage tests run through `care-tenancy.sh` (`care.emr.tests.test_user_api`,
`test_facility_api`, `test_file_upload_api`, `care.utils.tests.test_image_validator`) reach
`minio:9000`, which does not resolve on this machine: Ran 66, 10 failures and errors, all on that
host name. The same labels passed against the dev SeaweedFS with the split keys (66 OK); their test
objects were removed afterwards. CI runs the full suite.

## Final results

- app-role mode: Ran 631 OK (skipped 45); off mode: Ran 598 OK (skipped 48); both with every Redis key tenant-prefixed.
- `scripts/isolation-mutation.sh check` OK (60 rows; the 5 new rows each fail their test when applied); check-logs OK; pre-commit OK.
- Static tests: s3 46, release 77, logs 36; infra/aws `tofu test` 11 passed.

Known limits are in docs/reviews/p1-t20/design.md ("P1-T20b as built"): links live 24 h; no rollback to a release from before P1-T20b; the web app's fallback after a failed link is not clicked through in a browser; role-based AWS access keeps the buckets apart by IAM policy only; the stage-1 serve-outgoing advice is proven on stand-ins only, not a live staging stack.
