# P1-T11b3 independent code review (2026-11-15)

Three reviewers who did not write the code (Opus 5.5, high effort, three lenses) read the role ×
endpoint matrix and the clinical canary: `ihr_access/tests/matrix.py`, `test_role_matrix.py`, the
reviewed files (`role_matrix.txt`, `role_matrix_keys.txt`, `list_params.txt`,
`canary_exemptions.txt`, `spec_exemptions.txt`), the world in `ihr_access/testing.py` and the
three rules the build added (`branches.closed`, the chart rule, `Shape.also`). The lenses were
matrix coverage (M), canary leaks (K) and operations (O). Each finding was then challenged by
another agent (Opus 5.5, medium; high for the high-severity ones) that tried to refute it. Of 18
findings: 0 refuted, 4 confirmed in part (M3, M4, M8, O3), 14 confirmed. A fixer (Opus 5.5, high)
fixed all 18. A verifier (Opus 5.5, medium) checked each fix by applying its mutation by hand: it
closed 14 and found M3, M4, M8 and O3 only partly closed, and raised 5 new problems. A second
fixer dealt with all four gaps and all five problems.

## Defects the matrix itself found during the build

All four were fixed fail closed in `ihr_access`, before the review.

| # | What the matrix showed | Fix |
|---|---|---|
| D1 | The Radiologist read every resource request, each embedding the first patient's unprojected body | `branches.closed(user)` (restricted, or holding only roles of ours); the resource-request swap refuses closed users |
| D2 | The Radiologist saw the patient's medication return in the delivery-order list | `_NoPatientRows`, the dispense-order and device swaps use `closed` |
| D3 | An imaging request or report read at the Radiology room embedded the whole patient and visit of a patient with no Radiology visit | the chart rule: a user of our roles who holds a clinical one gets patient and visit projected unless CARE's `can_view_clinical_data` holds |
| D4 | CARE's medication-dispense spec embeds the visit through `EncounterSpecBase.serialize`, which the guard did not wrap (latent: no role of ours reaches a dispense) | `Shape.also` wraps it; check E022 covers it |

## Findings

Severity is the reviewer's, then the challenger's where it changed. "Ver 1" is the verifier's
status after the first fixer, "Ver 2" after the second.

| # | Finding | Severity | Challenge | Fix | Test | Mutation row | Ver 1 | Ver 2 |
|---|---|---|---|---|---|---|---|---|
| K1 | The Radiologist reads another patient's diagnoses, medicines, results, consents and notes by adding `?encounter=<a Radiology visit>` | high | confirmed, high | new `ihr_access/visits.py` wraps `APIView.initial`: for closed users a visit named in `?encounter=` must belong to the path patient (403 otherwise); without the chart the visit opens only its own rows; E022 fails if the hook is missing | `test_visits.VisitParam` (6 tests) | `access-visit-patient`, `access-visit-rows`, `access-visit-install` | closed | closed |
| M1 | Same hole seen from the matrix: no variant paired one patient's routes with another patient's visit | high | confirmed, medium | same fix; `matrix.CROSS` generates an `?encounter=encounter_b` variant for every GET patient route (27 rows), `post_reads.txt` has `analyse-cross`, 13 new `never` lines | `RoleMatrix`, the canary | as K1 | closed | closed |
| M2 | The `ListParams` static check misses query reads in helpers and on detail routes | medium | confirmed, medium | the check walks the whole MRO under `care.` / `ihr_` and includes detail routes; a runtime recorder (`matrix.QueryReads`) requires every key read to be named by a `list_params.txt` line | `RoleMatrix.test_every_query_read_has_a_params_line`, `ListParams.test_the_walk_reaches_parents_and_helpers` | `access-list-params-pharmacist` | closed | closed |
| M3 | Write routes default silently to `deny` and pass on 400; new upstream write routes need no review | medium | confirmed in part, low | `role_matrix_writes.txt` snapshots every denied write cell as refused by authorization (`auth`) or by body validation (`body`); a new or moved cell fails; second fixer added `DeskWrites` with valid bodies on desk and department routes | `RoleMatrix.test_every_method_of_denied_routes`, `DeskWrites.test_desk_writes_with_bodies` | none | partly | partly (known limit) |
| M4 | Allowed writes that name only `ExactGrants` are never sent, so scope and body are unchecked | low | confirmed in part, low | `AllowedWrites.test_department_writes_keep_to_their_own_copy` (own copy 200, other copies 403/404); `MatrixFiles` refuses `ExactGrants` alone on scoped routes; second fixer made `SCOPED` match specimen routes and added `DeskWrites` (39 writes, 32 rows now name it) | `AllowedWrites`, `DeskWrites`, `MatrixFiles` | none | partly | closed |
| M5 | An `all:skip` row hides a route `fill()` can fill; one skip reason is wrong | low | confirmed, low | `skip` now means only "`fill()` cannot fill this"; a skip on a filled route fails; `schedule_external_id` filled by the matrix's own overlay | `RoleMatrix.test_targets_are_unique_and_skip_means_unfilled` | none | closed | closed |
| M6 | The matrix's role list is hard-coded and never checked against the registry | low | confirmed, low | compared with `registry.RESTRICTED` and `registry.EIGHT` | `MatrixFiles.test_the_roles_are_the_registrys` | none | closed | closed |
| M7 | SpecWalk's "never a read model" check ignores CARE's fallback to `pydantic_model` | low | confirmed, low | resolves read models as CARE does (`get_read_pydantic_model`, `get_retrieve_pydantic_model`, fallback) for every view in every URL configuration | `SpecWalk.test_a_view_reading_with_a_write_spec_is_caught` | none | closed | closed |
| M8 | Store rows meant to prove Q-K43 are never answered with data | low | confirmed in part, low | the world seeds a request order and a supply request; new row `delivery_orders/ ?request_order=request_order`; second fixer corrected a stale row reason | `StoreRows.test_delivery_orders_of_a_request_order` | `access-delivery-patient` (re-pointed, FAILED 20) | partly | closed |
| K2 | `list_params` cannot see authorization that depends on the query; the note and thread lists say "optional filter" | medium | confirmed, medium | the runtime read check and the CROSS variant; `?encounter=` lines added for consent, symptom, diagnosis, medication lists, thread, note; reasons corrected | as M2 | as M2 | closed | closed |
| K3 | POST reads open to our roles are never crawled by the canary | low | confirmed, low | `post_reads.txt`: 11 reviewed POST reads with real bodies, through the outcome, canary, exemption, Owner and key checks. It found a second leak (see below) | `RoleMatrix.test_post_reads`, the canary | `access-slot-day` (FAILED 14) | closed | closed |
| K4 | The key snapshot leaves out the Radiologist and does not walk embedded patients and visits | low | confirmed, low | the Radiologist has its own column; the crawl records every instance the guard decided on and collapses a guarded shape only for those; snapshot rebuilt (2,743 key paths in 116 route keys) | `MatrixFiles.test_an_unhandled_patient_like_body_is_walked`, `RoleMatrix.test_response_keys_match_the_snapshot` | none | closed | closed |
| O1 | `ListParams` cannot see query keys CARE reads behind our swapped viewsets | medium | confirmed, medium | the MRO walk and the runtime recorder; lines for device `?location`, delivery `?origin`, supply delivery `?order` and `?request_order`, patient `?facility` | as M2 | as M2 | closed | closed |
| O2 | SpecWalk's `pydantic_model` fallback (same as M7) | medium | confirmed, low | as M7 | as M7 | none | closed | closed |
| O3 | Health rows and their keys depend on the live Celery queue length | low | confirmed in part, low | the matrix classes point CARE's queue check at an empty queue on the test Redis; the probe test asserts `(200, {"queue_length": 0})` | `RoleMatrix.test_the_health_probe_reads_an_empty_test_queue` | none | partly | closed (known limit: a Redis outage stays a loud 500) |
| O4 | The documented `IHR_MATRIX_DUMP=1` rebuild step does nothing through `care-tenancy.sh` or CI | low | confirmed, low | `care-tenancy.sh` passes the variable into the container; `docs/UPSTREAM.md` gives the exact command | by hand | none | closed | closed |
| O5 | The batch meta-test adds an undocumented after-sync failure | low | confirmed, low | `docs/UPSTREAM.md` says each new GET needs a `BATCH_GET_CASES` entry and a `BatchCases` test; the assertion message names that step | `BatchCases` | none | closed | closed |
| O6 | The as-built runtime figures do not add up: one route is crawled twice under one key | low | confirmed, low | crawl and write targets are de-duplicated by key; two paths under one key raise; design doc corrected | `test_targets_are_unique_and_skip_means_unfilled` | none | closed | closed |

## The leaks found

- **K1 / M1: Radiologist `?encounter=` cross-patient read.** CARE's `authorize_read_encounter`
  (and the note thread and message copies) authorizes on the visit named in `?encounter=` and never
  checks that the visit belongs to the patient in the URL. A Radiologist, who holds a clinical
  slug and is not restricted, could read patient A's notes, diagnoses, symptoms, medicine
  requests and statements and consents by naming a Radiology visit of patient B (403 without the
  parameter). Closed for every user of our roles by `ihr_access/visits.py`. The same gap for CARE's
  own roles is logged in Q-K40.
- **Slots-for-day (found under K3).** CARE's `slots/get_slots_for_day` checks no right, so every
  role of ours could read any doctor's day and schedule. Closed by `IhrSlotViewSet` (closed users
  need the booking read right first), registered in `route_swaps.SWAPS`: 11 swaps now.

## CARE-role gaps added to Q-K40

- `?encounter=` names a visit of another patient (K1), for CARE's own roles;
- `get_slots_for_day` checks no right;
- the request-order list is open to every member;
- carried from the build: the delivery-order list with neither origin nor destination answers every
  order of the hospital; a service request or diagnostic report read through its place embeds the
  patient's whole record and visit; `add_favorite` writes a favourite for an object the caller may
  not read.

## Second round

The verifier closed 14 findings and found M3, M4, M8 and O3 only partly closed. Its five new
problems and the second fixer's answers:

1. The static check skipped every patient route: now every key a patient route reads by name (other
   than `encounter` and paging keys) needs a `list_params.txt` line (13 lines added; row
   `access-list-params-patient-key`).
2. A bare `none` line accepted every key, and POST reads were not recorded: the format is now
   `none:key1,key2`; POST reads are recorded under `POST <route>` (rows
   `access-list-params-none-keys`, `access-post-read-recorded`).
3. No test showed the Lab Technician cannot update the ward copy's specimen: added (M4).
4. A stale reason in `role_matrix.txt` (the world seeds no request order): corrected.
5. A create that names `?encounter=` is refused for a closed user without the chart: kept fail
   closed (no web-app call does this) and pinned by `test_a_create_naming_a_visit_is_refused`.

`DeskWrites` also found one misclassified row: `POST account/default_account/` creates nothing and
needs only `can_read_account`; it is now `rec,billing:allow`.

## Final state

- **Fixed:** 17 of 18 findings closed, M3 closed in part (valid-body denials are proven on the
  desk and department routes only; see Known limits). All five verifier problems dealt with.
- **No owner decision was needed** and no new Q-K was raised. Q-K37a stays open, option A.
- **Nothing under `apps/` was edited**, so there is no UPSTREAM_PATCHES.md entry. No migration, no
  new table, no new log line. `batch_allowlist.txt` is unchanged.

## Known limits (full text in design-b.md "P1-T11b3 as built")

- The write crawl with valid bodies runs only on the desk and department routes (bookings, slots,
  queues, tokens, accounts, invoices, payments, specimens and reports). Elsewhere a `body` cell
  shows only that nothing was written (design item 14).
- A create that names `?encounter=` is refused (fail closed) for a closed user without the chart.
- A Redis outage turns the health rows into a loud 500 that matches no row.
- Staff cards (the user signature) always collapse in the key snapshot.
- The matrix runs on one world: a cell holds for these rows. Settings routes with no row here are
  `skip` (the isolation crawl covers them).
- CARE's own roles keep CARE's answers on the gaps in Q-K40.
- The matrix module adds about 1.4 min to CI's plugin step.

## Final results

- Plugin suites, `IHR_DB_ROLES=test`: Ran 912 tests, OK (skipped=45); Redis audit: 970 keys
  written, all tenant-prefixed.
- Plugin suites, roles off: Ran 877 tests, OK (skipped=49); Redis audit: 970 keys written, all
  tenant-prefixed.
- Matrix module alone: Ran 30 tests in about 86 s, OK.
- `scripts/isolation-mutation.sh check`: exit 0, 143 rows (11 new in b3). Each new row was applied
  alone to a scratch copy of `plugins/` and its named test FAILED by assertion (never an error);
  `access-visit-install` and `access-guard-install` stop at E022's signature.
- `python3 scripts/check-logs.py`: Log pipeline OK.
- `scripts/batch-allowlist.py --check`: 37 routes, list current.
- `pre-commit run --all-files`: 43 hooks passed.
