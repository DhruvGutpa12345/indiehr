# P1-T11b4 independent code review (2026-11-15)

Three reviewers who did not write the code (Opus 5.5, high effort, three lenses) read the MRD
records grants and the Owner totals: `ihr_access/records.py`, `authz.py` (`RecordsGrantAuthorizer`),
`endpoints.py`, `totals.py`, `tasks.py`, the audit changes (`ihr_audit/context.py`, `middleware.py`,
`views.py`, `export.py`), the matrix rows for the grant viewer `mrdg`, the log line and its alerts.
The lenses were grant security (G), audit, logs and Owner totals (A), and correctness and
operations (C). Each finding was then challenged by another agent (Opus 5.5, medium; high for the
medium-severity one) that tried to refute it. Of 9 findings: 0 refuted, 3 confirmed in part (A3,
A4, C2), 6 confirmed. A fixer (Opus 5.5, high) fixed all 9. A verifier (Opus 5.5, medium) checked
each fix by applying its mutation by hand: it closed 8 and found A3 only partly closed (the wording
changed, the behaviour did not). A second fixer (Opus 5.5, high) closed A3 in code.

## Findings

Severity is the reviewer's, then the challenger's where it changed (it never did). "Ver 1" is the
verifier's status after the first fixer, "Ver 2" after the second.

| # | Finding | Severity | Challenge | Fix | Test | Mutation row | Ver 1 | Ver 2 |
|---|---|---|---|---|---|---|---|---|
| G1 | A new grant did not end an older grant that had expired but was still open; ending the old one later deleted the new grant's `PatientUser` row, so the new grant lost its patient-level reads while the authorizer still opened requests and reports | low | confirmed, low | `create_grant` locks the user's lapsed-but-open grants on the patient and ends each (`end_grant(.., "expired")`) before its live check; backstop: `end_grant` deletes the hidden-role row only when no other live grant of that user on that patient remains | `GrantedReads.test_a_new_grant_ends_the_lapsed_one_first`, `test_ending_a_grant_keeps_the_row_another_live_grant_needs` | `records-lapsed-first`, `records-row-still-needed` | closed | closed |
| G2 | `create_grant` saved a whole `Patient` row loaded before the transaction (through CARE's `PatientUser.save`), which could write back a stale name, phone or address over a concurrent demographics edit | low | confirmed, low | the row is inserted with `bulk_create`; then only a freshly locked patient is saved with `update_fields=["users_cache"]` (the way `end_grant` already did) | `GrantedReads.test_a_grant_never_writes_back_a_stale_patient` (failed on the old code) | none (not an access control) | closed | closed |
| A1 | Owner totals counted failed, queued and partial payments (and failed credit notes) as collected and refunded, so "collected" disagreed with CARE's own payment totals and with "outstanding now" | medium | confirmed, medium | `RECEIVED_OUTCOME = "complete"` filters collected, collected by method and refunded, as CARE does; adjustments stay out of "collected" (open decision, Q-K46) | `OwnerTotals.test_owner_totals_count_and_sum` (four rows of 999 rupees that must not count; failed with the filter removed, 3747.50 != 750.50), `test_owner_totals_by_month` | none | closed | closed |
| A2 | Owner totals answered 500 (OverflowError) for a far-future `to` date such as year 9999 | low | confirmed, low | a start before `MIN_DAY` (01-01-2000) is refused on `from`; an end more than `MAX_DAYS_AHEAD` (366) days after today (IST) is refused on `to`; both answer 400 on the right field | `OwnerTotals.test_owner_totals_range_rules` (year 9999 by day and by month, 0001, 31-12-1999, and the two edges that pass) | none | closed | closed |
| A3 | Every request by a grant holder was marked `purpose: records` with the grant ids, including reads of other patients and the front-desk search, so the viewer paired another patient's read with a grant's reason | low | confirmed in part, low | first fixer: wording only (ADR-018 item 8, design doc); second fixer: behaviour (see "The A3 second fix") | `RecordsAudit.test_only_reads_of_a_granted_patient_say_records`, `test_records_request_entry_has_purpose` | `records-annotate` (retargeted), `records-annotate-scope` (new) | partly | closed |
| A4 | The CSV audit export dropped the grant ids, so exported `records` reads could not be joined to their grant or reason | low | confirmed in part, low | a `grants` column is appended last to the CSV (ids joined with `; `), so earlier columns keep their places; `verify_export.py` checks only the JSON export and needs no change | `ihr_audit.tests.test_device_time_zone.RecordsGrantExportTests.test_the_csv_export_names_the_grants` (failed with the column blanked) | none | closed | closed |
| C1 | The matrix's `mrdg` rows could not detect a leak of another patient's records through the grant authorizer (the full world gave the other patient no request, specimen, report or prescription) | low | confirmed, low | `testing.second_patient_orders()` gives the Radiology patient an imaging request, specimen, report and prescription (markers `rad-request`, `rad-specimen`, `rad-report`, `rad-prescription`); 7 new `mrdg:deny` rows in `role_matrix.txt`, 7 variants in `list_params.txt`, four `mrdg rad-* * never` canary rows, 175 new `rad` keys | `RoleMatrix.test_role_matrix` (7 mrdg cells fail under the mutation), `test_grant_does_not_open_other_patient_reports` | `records-grant-scope` (now names both tests) | closed | closed |
| C2 | Ending an old grant deleted the `PatientUser` row that belonged to the clerk's newer grant on the same patient (the same defect as G1, seen from operations) | low | confirmed in part, low | as G1 | as G1 | as G1 | closed | closed |
| C3 | The per-request expiry held grant and patient row locks for the whole request, so one clerk's parallel requests ran one after another (and an end inside a refused request was rolled back) | low | confirmed, low | `records.on_request` now only reads, ordered by `pk`; new `ihr_access/middleware.py` (`RecordsExpiryMiddleware`, in `ihr_platform/settings/tenancy.py` after `StaffSessionMiddleware`) calls `records.end_due` after the view, outside `ATOMIC_REQUESTS`: each `end_grant` runs in its own short transaction in id order; a `DatabaseError` is logged and left to the beat; `expire_due` is also ordered by `pk` | `GrantedReads.test_a_refused_request_still_ends_its_expired_grants`, `test_the_request_hook_ends_nothing_itself` (only SELECTs, no `FOR UPDATE`), `test_the_beat_ends_each_grant_once` (rewritten) | `records-expiry` (retargeted), `records-expiry-after-view` (new) | closed | closed |

Nothing was refuted. No finding was a data leak that reached a person who should not see the data:
G1 and C2 failed closed (the grant lost reads, never gained them), G2 was a lost update of
demographics, A1 was a wrong money figure, A2 a 500, A3 and A4 were audit-trail readability, C1 a
test blind spot and C3 a latency problem.

## The A3 second fix

The verifier found that the first fix only changed the words: the mark was still put on every
request made while a grant was live. The second fixer made the code match the words. A request
entry now says `purpose: records`, with a grant id, only when the request showed that grant's
patient, and it names only those grants. A read of another patient made while a grant is live keeps
its own purpose and has no `grants` key.

- `ihr_audit/context.py`: new `annotate_patients(key, by_patient, **fields)` keeps a map of
  patient pk to grant id for the request entry only (never written to the database session
  context, so rows the request changes do not carry it); later calls add to the map, so a grant
  ended during the request still names the reads made under it. New `patient_fields(patients)`
  returns `{purpose, grants: [...]}` for the shown patients that have a grant, or `{}`. The map is
  cleared in `begin_request` and `end`.
- `ihr_audit/middleware.py`: reads every shown patient before the 1,000-patient cap, then builds
  the entry with the patient fields.
- `ihr_access/records.py`: `annotate()` calls `annotate_patients`. `ihr_access/state.py`: the
  unused `grant_ids` is removed.
- Mutation rows: `records-annotate` is retargeted to the new line and `records-annotate-scope` is
  new (every live grant's id on every entry). Each failed alone by assertion (`'care' != 'records'`,
  `Lists differ: [1, 2] != [1]`), never by error.
- The verifier's three minor notes became known limits (below); no code change.

## Final results

- Plugin suites, `IHR_DB_ROLES=test`: Ran 943 tests, OK (skipped=45); Redis audit: 992 keys
  written, all tenant-prefixed.
- Plugin suites, roles off: Ran 908 tests, OK (skipped=49); Redis audit: 989 keys written, all
  tenant-prefixed.
- Mutation check: 153 rows (143 before b4, 6 from the build, 4 from the review fixes), every target
  and named test found; each new or changed row applied alone failed its named test by assertion.
- `python3 scripts/check-logs.py`: Log pipeline OK.
- `scripts/batch-allowlist.py --check`: 37 routes, list current.
- `pre-commit run --all-files`: 43 hooks passed.
- `manage.py check` in the dev backend (backend and Celery restarted): no issues.
- No migration, no new table, no `apps/` edit, no change to `batch_allowlist.txt`.
- CARE modules in tenant mode (service request, specimen, diagnostic report, medication request
  and prescription, encounter, patient, file upload): 188 run, 183 pass. The 5 that fail are the
  same `test_file_upload_api` storage round trips that failed in b1, b2 and b3: the one-off test
  container cannot resolve the `minio` host. This is the baseline, not caused by b4.

Independent check of the second fix (Opus, medium, 15-11): the A3 change holds, with no regression in the audit middleware (the 1,000-patient cap, `patients_total` and entries without grants are unchanged), and the per-patient map is cleared on every path, exceptions included. `ihr_access.tests.test_records ihr_audit` in tenant mode passed (172 tests). It found one low gap, now fixed: on a path with a purpose of its own (audit, billing, admin), a grant holder's read was re-marked `records`. `context.patient_fields(patients, path_purpose)` now keeps the path's purpose and adds only the grant ids. New test `RecordsAudit.test_a_path_purpose_is_kept_and_a_mixed_read_names_only_its_grant` also pins a mixed read (a granted patient plus another names only that grant). With the fix removed, it FAILED 3 subtests. With the fix: `ihr_access.tests.test_records ihr_audit` 174 tests OK; mutation check 153 rows; `records-annotate` retargeted to the new line. Left as is: grants are matched against every patient shown, so on a request showing more than 1,000 patients `grants` can name a patient beyond the capped list (`patients_total` is recorded).

## Known limits

- No screens: MRD asks, lists and ends grants through the API (or the dev command) and the Owner
  reads totals as JSON, until P1-T11c (Q-K45).
- No unique index, so two simultaneous requests by the same clerk for the same patient can both
  make a grant (it would need a migration). Ending either keeps the shared row while the other is
  live (G1 backstop).
- A live grant survives the removal of the MRD role until it ends (at most 30 minutes) unless the
  clerk or a hospital-wide admin ends it. A file link issued under a grant can be used for up to 5
  minutes after it ends.
- An entry that showed a granted patient together with others (a search that matched both) is
  marked `records` with that grant's id and lists every patient it showed; the grant's reason
  applies only to the patient its id names. Grants made by the dev command are in the trail
  without `purpose: records`.
- Owner totals: per facility; discharges need a recorded period end; outstanding is a snapshot;
  amounts are tax-inclusive; "collected" leaves out adjustments (Q-K46, open).
- `totals.MIN_DAY` (01-01-2000) blocks earlier migrated history until the constant is moved. The
  fixed past ranges in `test_owner_totals_range_rules` stay valid because a past date never goes
  beyond `MAX_DAYS_AHEAD`.
- Under the `records-grant-scope` mutation most `mrdg` `rad-*` cells answer `project` rather than
  `allow`; the mutation is caught only because the reviewed outcome is `deny`. A future deliberate
  `project` there must re-review those rows.
- The per-request expiry check costs every staff request one indexed query.

## Open for the owner

- **Q-K46 (OPEN):** do adjustments count as "collected" in the Owner's totals? Built as option A
  (collected means money received; adjustments stay out but still lower "outstanding now").
  Recommendation A, with C later. Failed, queued and partial payments are never counted (A1, not a
  choice). See `docs/QUESTIONS.md`.
- **Q-K37a (OPEN, carried):** the Cashier and TPA Desk see no bed on the account (option A built,
  recommendation B).
- ADR-018 (Proposed) and Q-K34 to Q-K45 are auto-accepted by Claude, awaiting the owner.
