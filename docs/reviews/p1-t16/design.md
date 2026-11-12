# P1-T16 design: isolation coverage (draft for attack, 2026-11-13)

PLAN row P1-T16 (from the Phase 1 review, R1-04 and R1-06, and its challenge):
- seed the models behind the 34 skipped crawl routes;
- fill Django path-converter routes;
- add mutation rows for audit-trigger coverage, the terminology remote-client refusal, the
  guarded role sync and the global-edit refusal.

Research with file:line evidence: the run's scratchpad `t16/crawl.md` and `t16/mutations.md`.

## Split (by Claude, 2026-11-13, awaiting owner confirmation: Q-K26)

- **P1-T16a:** the crawl fails closed on path-converter routes and fills them, fixes the bug that
  filling them exposes, adds the mutation rows, and adds the guard tests they need.
- **P1-T16b:** the seeded rows behind the skipped routes. This is the bulk: 31 models, each with
  valid CARE enums.

## P1-T16a

1. **Fail closed.** `routes.fill()` returns None (counted as skipped) when a `<…>` or `(?P<…>`
   placeholder is left in the path. Today `<int:pk>` and `<str:…>` routes are requested with the
   placeholder in the URL and are never counted.
2. **Fill converters.**
   - `CONVERTER_RE` uses a `(?<!\?P)` look-behind and runs after the regex groups are filled.
   - The values:
     - `username` is the tenant's user;
     - `pk` is the hospital's first audit entry id;
     - `pincode` is a PIN from the bundled test sample, loaded into the public directory for the
       world and removed in `drop_world`.
   - Each value is checked against the converter's regex.
   - `pk` and `pincode` join `SHARED_VALUE_PARAMS`: integer ids are equal across schemas, so the
     cross-id probe must not use them.
3. **Fix the latent bug.**
   - `api/ihr/staff/<str:username>/<slug:action>/` routes GET to `StaffAccessView.get(request,
     username)`, which takes no `action` (a 500 once filled). The route becomes POST-only
     (`http_method_names`), and `get_routes` honours the view's `http_method_names`.
   - `action` stays unfilled. The route is POST-only, so the crawl's GET skips it by method,
     not as a skip-list entry.
4. **The label fallback.** A viewset without `database_model` takes `queryset.model`
   (PlugConfig).
5. **Mutation rows** (`scripts/isolation-mutation.sh`):

| Row | Target → replacement | Must FAIL |
|---|---|---|
| audit-trigger-every-table | `ihr_audit/install.py` `if table in EXEMPT:` → `… or table == "emr_patient":` | `CoverageTests.test_every_table_is_covered…` |
| audit-coverage-missing | the `problems.append(… trigger … is missing)` line → `pass` | `CoverageTests.test_a_new_table_or_secret_column_is_reported` |
| terminology-remote-refusal | `client.py` `care_client.FHIRClient.query = _refuse` → `pass` | `NothingLeaves.test_care_client_is_ours_and_the_remote_one_refuses` |
| terminology-install | `ihr_terminology/apps.py` `client.install()` → `pass` | both `NothingLeaves` tests |
| role-sync-guard | `ihr_roles/.../sync_permissions_roles.py` `if held:` → `if False:` | `GuardedSync.test_refuses_to_delete_a_held_role` |
| patient-global-edit-refused | `secure.py` `if settings.get("PATIENT_GLOBAL_EDIT_ACCESS_ENABLED"):` → `if False:` | a new dedicated test method (not one of about 30 subtests sharing one FAIL anchor) |
| packs-edited-row-kept | `ihr_refdata/engine.py`: the edited branch updates anyway | `RuleTests` edited-row test |

6. **Guard tests the mutations need:**
   - `EXEMPT` pinned to its reviewed four names (today a fifth name would silently drop a clinical
     table from the trail and from the check);
   - the global-edit case in its own test method.
   - `isolation-mutation.sh check` also verifies that each named test method exists.
7. The job and table header say "isolation and security controls". CI already runs each row as
   its own matrix job.

## P1-T16b

1. **A test-only `tests/isolation/extras.py`.**
   - It runs per tenant after `_seed` and before `_add_canaries` and `_collect_ids`, so the new
     UUIDs are collected and the cache flush covers them.
   - Rows are created with the ORM, with valid CARE enums, copying CARE's own test helpers.
     `model_bakery` random enums would break CARE's read specs.
   - Each row hangs off the **first row by pk** of its parent: `fill()` uses that row, and a
     child of another parent gives a silent 404.
   - The tenant's canary goes into a free-text field of each row.
2. **Models** (clinical and file first): ServiceRequest, Specimen, DiagnosticReport, Observation,
   Condition (one row serves symptom and diagnosis), AllergyIntolerance, Consent, the medication
   chain (prescription, request, administration, statement), QuestionnaireResponse,
   FormSubmission, NoteThread and NoteMessage, FileUpload, MetaArtifact, ReportUpload; then the
   device histories, FacilityLocationEncounter, DispenseOrder and MedicationDispense,
   AvailabilityException, Token, ResourceRequest and its comment, TagConfig,
   QuestionnaireResponseTemplate (with `created_by`, or its retrieve is a silent 404), and
   PlugConfig.
3. **File presigning** needs bucket settings. Use the same `boto3.client` patch the file-key test
   uses if the test settings lack them.
4. **Every seeded route is crawled for real.**
   - A filled route that answers 404 counts as crawled today. So the extras assert, per model,
     that its detail route answers 200 for its own tenant.
   - `crawl_skips.txt` shrinks to what is still skipped, each with its reason.
   - `MIN_ROUTES_WITH_OWN_DATA` rises to the new floor.
5. Anything that cannot be seeded validly stays listed with its reason. It is not forced.

## Decisions (auto-accepted, awaiting the owner)

- **Q-K26:** the P1-T16 split (a: crawl, bug, mutations; b: seeded rows).

## After the attack (2026-11-13): the design as it is built

One Opus reviewer covered correctness, security and operations, and a second agent tried to refute
each finding. Result: 11 findings, 3 refuted (A9, A10, A11), 2 confirmed, 6 partly, none fatal.
Where this section and the sections above disagree, this section wins.

1. **PlugConfig's detail route is already public in CARE (A1).** CARE's own viewset gives no
   authentication or permission on GET. Once the label fallback fills it, the anonymous test sees
   it. It goes on `anonymous_allowlist.txt` with its reason (the web app's plugin config is
   needed before sign-in; `meta` must never hold secrets), in the same change. Before seeding,
   run the anonymous test with only the fallback, to find any other route the fallback newly
   fills.
2. **Exact mutation targets (A2).** Every row's find text must occur exactly once as a substring,
   not merely as a whole line. The packs row:
   `packs-edited-row-kept|plugins/ihr_platform/ihr_refdata/engine.py|        elif record is not None and record.applied_sha256 == have:|        elif record is not None:|ihr_refdata.tests.test_engine.RuleTests.test_an_edited_row_waits_for_the_admin|`
3. **No assertions in the world's set-up (A3).** `extras.py` only seeds and records each seeded
   model's detail route on the tenant. A dedicated test,
   `test_every_seeded_and_converter_route_answers_its_owner`, asserts per route (subTest):
   - 200 for the owner;
   - the body holds the tenant's own ids or canary;
   - the converter routes (staff access, PIN, audit entry) answer 200 too.
   The crawl's admin is given the role the audit viewer needs, if it lacks it.
4. **Files (A4).** The bucket settings come from `apps/backend/docker/.local.env` in dev and CI,
   and presigning is computed locally. The file routes are not patched.
5. **Methods per route (A5, A6).**
   - `get_routes` reads `view_initkwargs`/`initkwargs` with `getattr` and ANDs it with the
     existing check. A viewset's `actions` stay first.
   - `staff_urls.py` sets `http_method_names` per route: GET, HEAD and OPTIONS on `access/`;
     POST and OPTIONS on `<slug:action>/`.
   - Both handlers accept `action=None`, so a stray method can never give a 500.
6. **PIN is a real probe (A7).**
   - The sample is loaded once into public in the world, then each tenant's geography is loaded
     (`geography.load_all()` in its schema) before `_collect_ids`.
   - The test asserts the PIN answer names that tenant's own organisations, never the other's.
   - The `SHARED_VALUE_PARAMS` change for `pk` and `pincode` is dropped. The cross-id probe only
     covers `external_id` routes, so it did nothing.
7. **Clean-up (A8).** `cls.addClassCleanup` is registered right after each public write (the PIN
   load, each hospital). PIN rows never outlive a failed set-up.
8. **Floor (A11).** `MIN_ROUTES_WITH_OWN_DATA` is set below the observed count with a margin,
   and the observed count is recorded in TRACKER.

## After the code review (2026-11-13)

A code review of the built work gave 9 findings with a challenge each; these were applied.

- **What the owner test proves (I3).** It proves each seeded route is reached and answers its
  owner with the owner's row (the row's own id is in the body). It does not prove where the
  canary is rendered: most bodies also nest a canaried staff or patient name. So each seeded
  route is recorded as `(path, canary_field)`. Where the CARE read spec renders the field the
  canary went into (checked in the spec and confirmed by a run), the test requires the canary in
  that top-level field. The routes whose read spec has no such field record `None`.
- **Converter routes are a reviewed list (I1/R3).** `fill()` picks a converter's value by its
  name alone, so the owner test requires the GET converter routes `fill()` fills to equal
  `CONVERTER_ROUTES`. A new `<int:pk>` route fails the suite until it is reviewed there.
- **Leftover regex syntax fails closed (I2).** `fill()` returns None when any of `()?<>[]\*+{}|`
  is left in the path (`.` excepted: `\.` becomes a literal dot).
- **The audit entry probe is one of the tenant's own (I4).** `<int:pk>` is the last
  `emr_patient` UPDATE (`tbl`/`op` columns), written by the canary step, and the test requires an
  own id or the canary in it.
- **The first facility is public (R1).** CARE's fixtures set it at random; the world fixes it, so
  `getallfacilities/<id>/` is open anonymously in every run and the anonymous test asserts it.
- **Seeding errors name the model (R2).** `extras.py` re-raises any error as
  "extras: seeding <Model> failed (CARE model drift?)". The report template is chosen by its
  report type's associating model (encounter or patient), not first by pk.
- **Mutation cost comment (R4)** in apps.yml now says one job per row (26 today).
