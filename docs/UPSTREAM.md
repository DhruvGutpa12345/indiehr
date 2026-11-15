# Upstream code (CARE)

How CARE is brought in and kept current: [ADR-006](adr/006-upstream-fork-via-subtree.md). The exact pins are in [`upstream.lock`](../upstream.lock).

| App | Folder | Upstream | Pinned commit | Commit date |
|---|---|---|---|---|
| Backend (Django API) | `apps/backend` | ohcnetwork/care `develop` | `67a46185eb98` | 2026-11-01 |
| Frontend (React) | `apps/frontend` | ohcnetwork/care_fe `develop` | `57205b897804` | 2026-11-02 |

## Check for upstream changes (changes nothing)

```bash
make sync-upstream-check
```

## Pull upstream changes

```bash
scripts/sync-upstream.sh all --task P2-T4     # or: make sync-upstream TASK=P2-T4
```

Merges the latest `develop` into both apps, keeping our edits, then updates `upstream.lock` and commits. Update the table above by hand. Run the full test suite before pushing.

For one app or a specific commit:

```bash
scripts/sync-upstream.sh backend <full-40-char-sha> --task P2-T4
```

Every edit we make inside `apps/` must be listed in [UPSTREAM_PATCHES.md](UPSTREAM_PATCHES.md).

## After a frontend sync: translations (P1-T7f)

New upstream strings arrive in English only. After merging, list the strings the app now
uses that have no Hindi (`apps/frontend/public/locale/en.json` minus upstream `hi.json`,
`deploy/locale/hi.json` and `docs/i18n/unused-strings.csv`, checked against the code as in
P1-T7f), translate them with the glossary (`docs/i18n/hi-glossary.md`) and add a review
sheet. A string listed as unused that the new code now shows moves out of that list.

## After every sync, even a clean merge (P1-T17)

A clean merge can still change what one of our patches means. In the P1-T17 trial,
upstream changed a hook's signature next to our patch and git merged it without a word.
After each sync, before pushing:

1. **Patched files.** For each file the dry run flagged ("upstream also changed our patched
   file"), check that our patch is still there and still right against the new code. Update its
   UPSTREAM_PATCHES.md row.
2. **Lock files.** Did upstream change `Pipfile`, `Pipfile.lock`, `package.json`,
   `package-lock.json` or `.node-version`? If it did, compare versions with our security bumps
   even without a conflict.
3. **Our plugins' assumptions.** Look through upstream's diff for:
   - new CARE test modules that use routes we block on hospital addresses → add them to
     `plugins/ihr_platform/ihr_platform/care_tests_excluded.txt`, with the reason;
   - new `emr` models → classify them in `ihr_audit/unlinked_models.txt`, or check they have a
     patient link;
   - new routes → `ihr_identity/blocked.py`, the isolation crawl's `crawl_skips.txt` or
     `anonymous_allowlist.txt` (the suite fails until they are reviewed);
   - changed system value sets → rebuild the pack: `scripts/pack-build.sh care-valuesets` (the
     drift test fails until you do);
   - new raw cache, lock or Redis keys → TENANCY_DESIGN §6 (the test runner fails on any key
     without the hospital prefix);
   - new settings → `ihr_platform/settings/secure.py` if deployment must pin them;
   - INSTALLED_APPS changes → `ihr_platform/settings/tenancy.py`;
   - permission or role changes → the guarded role sync and `ihr_roles`.
     `ihr_roles.tests.test_grants.CarePermissionsReviewed` fails on any new, removed or changed
     CARE permission (slug, context or CARE's role names) until its line in
     `plugins/ihr_platform/ihr_roles/care_permissions.txt` is reviewed: classify a new slug
     (`clinical` goes into `registry.CLINICAL_SLUGS`, `opening` into `PATIENT_OPENING`), then
     re-check our roles' grants (`registry.GRANTS`) against the change. The other grant tests
     prove only that our grants left CARE's lists alone (`registry.ORIGINAL` is rebuilt from
     the new code) and fail when a CARE permission names one of our roles;
   - new or changed fields on the patient, encounter, location, device, booking or token specs
     (`care/emr/resources/{patient,encounter,location,device}/spec.py`,
     `scheduling/{slot,token}/spec.py`) → classify each in
     `plugins/ihr_platform/ihr_access/fields/<shape>.txt` (`ihr_access.tests.test_fields`
     fails until you do; meanwhile the field is hidden from the restricted roles), and new
     `PatientUpdateSpec` fields in `fields/patient_edit.txt`. A moved spec, or a subclass with
     its own `serialize`, fails `manage.py check` (`ihr_access.E022`). Changes to
     `PatientAccess.find_roles_on_patient` / `get_filtered_patients`
     (`care/security/authorization/patient.py`) → re-read `ihr_access/authz.py` (P1-T11b1);
   - changes to the patient, encounter, location-association, resource-request, device,
     report-upload, delivery-order, supply-delivery or dispense-order viewsets (P1-T11b2's
     route swaps, `plugins/ihr_platform/ihr_access/views.py`) → re-read the swap; a new patient
     or encounter action fails `ihr_access.tests.test_{patient,encounter}_swap` until
     classified in `ihr_access/fields/{patient,encounter}_actions.txt` (unclassified it keeps
     CARE's check); a new encounter filter, or a new field of CARE's encounter create spec,
     fails `manage.py check` (`ihr_access.E024`) until classified in
     `fields/encounter_filters.txt` or `fields/encounter_create.txt` (meanwhile restricted
     users get 400 for it); a change to a CARE method our swaps copy
     (`EMRRetrieveMixin.retrieve`, `EMRUpdateMixin.handle_update`) fails
     `ihr_access.tests.test_encounter_swap.CareCopies` until our copy is re-read and the digest
     in `ihr_access/care_copies.txt` re-pinned; a viewset that newly reads organisation
     memberships fails `ihr_access.tests.test_further_swaps.MembershipOnlyLists` until
     reviewed in `ihr_access/membership_only.txt`, and one that newly authorizes through the
     store's supply-delivery permissions fails
     `test_further_swaps.PatientReturns.test_every_supply_delivery_route_is_swapped` until
     swapped;
   - any new, removed or changed route, or a changed answer to one of our roles (P1-T11b3's
     role x endpoint matrix) → `ihr_access.tests.test_role_matrix` fails until the route is
     classified in `plugins/ihr_platform/ihr_access/tests/role_matrix.txt` (and, in the same
     folder: a route whose view reads the query string, in its own or any inherited code, or
     on which the crawl saw a query key read, in `list_params.txt`; a new key in a body one of
     our roles sees, the Radiologist included, in `role_matrix_keys.txt`; a write route that
     appears, goes or moves between "auth" and "body" in `role_matrix_writes.txt`; a read CARE
     makes by POST in `post_reads.txt`; a new Patient or Encounter spec in
     `spec_exemptions.txt` or under the guard). A new view under a patient that overrides
     `initial` stops the run at `ihr_access.E022` (the visit check, `ihr_access/visits.py`).
     Print every answer with
     `IHR_MATRIX_DUMP=1 scripts/care-tenancy.sh test --settings=ihr_platform.settings.test --noinput ihr_access.tests.test_role_matrix 2>&1 | grep '^@@'`
     (`@@M` GET and `@@P` POST-read answers: key, role, outcome, status, markers, projected
     shapes; `@@K` key paths; `@@R` query keys read; `@@S` write-snapshot lines; `@@U`/`@@WU`
     unfilled routes) and read every changed cell: one of our roles reaching clinical data
     (the Radiologist outside Q-K44 A included) is a defect to fix in `ihr_access`, never a
     row;
   - new Playwright specs → check them against our India date formats;
   - new browser storage keys (localStorage, sessionStorage, `atomWithStorage`) → classify them
     in `apps/frontend/tests-ihr/storage-keys.json` or clear them in `clearStaffStorage`
     (`scripts/check-storage-keys.py` fails until you do, P1-T1d).
   - new or changed batch call sites in the web app (`useBatchRequest`, `batchQuery`) → run
     `scripts/batch-allowlist.py`, review the diff of
     `plugins/ihr_platform/ihr_platform/batch_allowlist.txt` (every new line is a route a staff
     batch may run) and commit it (`--check` fails in pre-commit and Apps CI until you do,
     P1-T20a). A call site whose `api:` is a variable fails until it is reviewed into the
     script's `DYNAMIC` table, and a reviewed one fails again whenever its file changes: re-read
     which routes the variable can name, then set the `sha256` the check prints;
     - each new GET line also needs a case: an entry in `BATCH_GET_CASES` and a `BatchCases`
       test in `plugins/ihr_platform/ihr_access/tests/test_role_matrix.py` that runs it in a
       batch as the restricted roles and asserts no canary and no patient field (a removed GET
       line: drop its entry). `BatchCases.test_every_listed_get_has_a_batch_case` fails in the
       plugin suite (Apps CI) until you do, even though `--check` and pre-commit pass;
   - changes to CARE's batch code (`care/emr/api/viewsets/batch_request.py`,
     `care/emr/utils/batch_requests.py`) → carry them into `ihr_platform/batch.py` and update the
     fingerprints in `ihr_platform/tests/test_public_routes.py` (the test fails until you do);
   - changes to CARE's patient-OTP booking actions (`care/emr/api/otp_viewsets/slot.py`:
     `create_appointment`, `cancel_appointment`, `get_appointments`) → carry them into
     `PublicOTPSlotViewSet` (`ihr_platform/public_routes.py`, which repeats their bodies with its
     own booking serialiser) and update `CARE_OTP_ACTIONS_SHA256` in the same test file;
   - changes to the viewsets we swap in CARE's router (`ihr_platform/route_swaps.py`: the doctor
     list, the patient-OTP slots, government organisations, the facility finder, users, batch) or
     to the user, booking and facility read specs → check that `public_routes.py` still shows only
     what it should (the isolation suite's key-set tests fail on a new field). A swapped class
     that moved or was renamed stops every process at its URL configuration and fails
     `manage.py check` (`ihr_platform.E020`): add its new dotted path to `SWAPS`.
4. **Translations.** Diff the English *values* in `apps/frontend/public/locale/en.json`, not just
   the keys. A reworded English string leaves our Hindi out of date, and
   `merge-locale.py --check` does not notice.
5. **Tests in tenant mode** before pushing: the plugin suites and the isolation suite
   (`scripts/care-tenancy.sh test …`), then Apps CI.

**To rehearse a sync without touching `main`:** use a worktree on a branch of its own:

```bash
git worktree add -b sync-trial ~/indiehr-sync-trial origin/main
```

Run the script there. The `upstream-*` remotes it adds live in the shared `.git` config. To run the tests in the worktree, first copy two git-ignored items into it: the dev key `apps/backend/jwks.b64.txt` (the test settings read it) and the collected `apps/backend/staticfiles/` (error pages render a static favicon, and without the manifest every 404 becomes a 500). The test container mounts the code read-only, so neither can be created there.
`IHR_COMMIT_TRAILER` adds a trailer, such as an attribution line, to the script's own commits.
The weekly **Upstream drift** workflow reports how far behind we are: it warns at 30 days and
fails after 60.

## Conflict runbook

A sync stops with "MERGE CONFLICT" when upstream changed lines we also changed. `upstream.lock` is left untouched until the merge is finished.

1. `git status` lists the conflicted files.
2. **Lock files** (`apps/backend/Pipfile`, `Pipfile.lock`, `apps/frontend/package-lock.json`) conflict whenever upstream changes them, because we carry security bumps (UPSTREAM_PATCHES.md). The P1-T17 trial touched none. When they do conflict:
   - take **upstream's** version: `git checkout --theirs <file>`;
   - backend: re-apply any of our versions that are still newer than upstream's, using the same container method as P0-T9/P1-T12 (`pipenv upgrade "pkg==x.y.z"` inside `python:3.13-slim-bookworm` with pipenv 2025.1.1);
   - frontend: re-apply only the changed entries (transplant method from P0-T9/P1-T12);
   - if upstream's versions are already ≥ ours, drop those rows from UPSTREAM_PATCHES.md.
3. **Code files:** keep upstream's change and re-apply our patch as described in UPSTREAM_PATCHES.md; if upstream fixed the same thing, drop our patch and its row.
4. `git add` the files, then `git commit` with the task ID, and set the new SHA in `upstream.lock` (`<app>.sha` and `<app>.date`) in a second commit.
5. Run `scripts/sync-upstream.sh all --dry-run` (should say up to date), push, and confirm **Apps CI** is fully green.
6. To give up instead: `git merge --abort`.
