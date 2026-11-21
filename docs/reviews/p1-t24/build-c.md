# P1-T24c backend build (21-11-2026)

Backend builder (Sonnet, high). The web page, the locale strings, the audit download links and the
two polish items are the web builder's. Design: `design.md` "T24c as decided". Decisions: Q-K124,
Q-K125 (docs/QUESTIONS.md).

## Files

| File | Change |
|---|---|
| `plugins/ihr_platform/ihr_refdata/api.py` | `_summary` gains `facility_name` (l.130); `_refused(status, detail, code, **flags)` (l.235) puts `code` on every refusal; the engine's 400 is `{reason, code: "refused"}` (l.416); docstring |
| `plugins/ihr_platform/ihr_platform/seeding.py` | `make_waiting_pack_item` (l.268) and its constants (l.263-265) |
| `plugins/ihr_platform/ihr_platform/management/commands/ihr_seed_demo.py` | calls it (l.175, not with `--passwords-only`); JSON line gains `pack_item_waiting_added` |
| `docs/reviews/p1-t15b/design.md`, `docs/egm/2026-11-17-drift-p1-t15b.md` | list body (`facility_name`), the `code` table, rows 1-2 marked fixed |
| `docs/reviews/p1-t24/design.md` | "T24c as decided" |
| `docs/QUESTIONS.md` | Q-K124, Q-K125 |

## How each item is met

- **G6.** `facility_name` from `record.facility.name`; `_shown()` already `select_related("facility")`, so
  no query is added. Pinned: the list GET is 24 statements for two facilities' items, 22 for one
  (`test_facility.ApiTests.test_the_list_and_detail_name_the_facility_with_no_extra_query`, which also
  pins the names, the detail and that `facility` stays the external id). `test_api.ListTests` pins the
  new key (null for a hospital-wide item). Gate unchanged.
- **POST answers.** The table in design.md lists all 13 answers the code gives. Only the ones the page
  could not tell apart lacked a machine-readable kind: a 409 that was not stale/blocked/busy
  ("not live"), the two 429s, the 404 and 503 (by status only), the engine's 400. All now carry
  `code`; the `busy`/`stale`/`blocked` flags stay; a field-keyed 400 stays keyed by field; 200 has
  no `code`. No change to who may do what (the matrix rows for the pack routes are unchanged).
- **G5.** `make_waiting_pack_item`: returns False when anything is pending; else renames the item's
  value set, drops its `PackItem`, runs `engine.apply_packs(recheck=True)` under the hospital lock as
  owner, and raises if the item did not become pending. See design.md.
- **Drift rows 1-2.** Marked fixed in the drift doc; the P1-T15b design's list body now names
  `facility_name`.

## Tests (real counts)

Written first: `AnswerKindTests` (8 tests; 9 after the review) and the facility test; run against the old `api.py` they
gave 4 failures and 3 errors in `AnswerKindTests` (the eighth, the body-error test, passes by design)
and the facility test failed on the missing key. `test_seed_pack` (4 tests) was written with the
function; mutation check: with `recheck=True` removed from the seed's `apply_packs` call, 2 of its 4 tests
error (nothing becomes pending), so the test bites; restored, all 4 pass.

Command and result:

`IHR_DB_ROLES=test scripts/care-tenancy.sh test --settings=ihr_platform.settings.test --noinput ihr_refdata ihr_refdata_registry ihr_audit ihr_access ihr_identity ihr_platform`

`Ran 1171 tests in 1142.482s` / `OK (skipped=45)` (the skips are the existing ones).
Updated pins: three exact-body assertions in `test_api.py` (`UNAVAILABLE`, `NOT_FOUND`, `BUSY`) now
include `code`.

## For the web builder

- List and detail: `facility` (external id or null), `facility_name` (string or null).
- POST refusals: switch on `status` and `code`; field-keyed 400s on their keys (`reason` is the only
  one the user can cause). 401/403 come from DRF with no `code`.
- Seeded item: `scripts/care-dev.sh seed`, then the screen shows one waiting item ("Demo edit (seed)...",
  reason `unknown-origin`, no "earlier" column).

## Web (T24c web builder, 22-11-2026)

Files (all ours; no upstream file under `apps/` is edited, so nothing goes in UPSTREAM_PATCHES.md):
- `apps/frontend/src/Utils/ihr/packs.ts` (+ `packs.test.ts`, 35 tests): effect and hold words,
  facility name, `canKeep`/`canAccept`, `reasonCheck` (1 to 300, one line), `decisionBody`,
  `answerOf` (the status table, rows 1-13, by status and `code`; the 200 is "done" or "already";
  flags `stale`/`busy`/`blocked` still decide a 409 with no code), `loadErrorKey`, `checkLine`,
  `pageRange`, history (`historyBy`, `historySummary`), `sinceText` (DD-MM-YYYY, IST), `readTab`.
- `apps/frontend/src/components/ihr/IhrPackUpdates.tsx`: gate as the audit page (`useIhrAccess`
  `hospitalAdmin`, else `IhrNotForRole`, no request). Tabs Waiting / Kept as ours / History in
  `?tab=`; list as table (md+) and cards; sheet with facts, "what accepting does", why it waits,
  blocked line, stored data check, the system's note (English, in a labelled line), forms' stranded
  counts, the three-column difference (stacked on a phone), required reason box, Accept and Keep.
- `Utils/ihr/ihrApi.ts` (`ihrPacksApi`), `Utils/ihr/menu.ts` (`packs` after `audit`, same
  `hospitalAdmin` gate, so never a restricted role or pending), `ihrMenu.tsx` (PackageCheck),
  `IhrRoutes.tsx` (`/facility/:facilityId/ihr/packs`).
- Audit downloads: `Utils/ihr/audit.ts` (`checkExportRange`, `exportUrl`, `VERIFIER_URL`, 6 new
  tests) and a `Downloads` block on `IhrAuditTrail.tsx`.
- Polish: `IhrEncounterStatus.tsx` (Booking card only with `booked_on` or a slot time),
  `IhrOwnerTotals.tsx` ("By day" select and its wrapper `max-w-full`).
- Strings: 108 keys in `deploy/locale/en.json` and `hi.json`, rows in `hi-batch-6.csv` (now 236;
  README row updated); `scripts/merge-locale.py --check` passes.

How each requirement is met:
- Every POST answer is a fixed phrase: `answerOf` never returns server text; the table's 13 rows
  are one test each, plus "unknown answers are the generic failure" over many status/body pairs.
- 409 stale and blocked reload the open sheet (the typed reason stays); 404 closes it and reloads
  the list; the sheet's own read is never cached (`gcTime: 0`), so the hashes sent are the ones shown.
- "Keep ours" only on pending items: a kept item's sheet shows Accept and a note, no Keep.
- Accept is disabled while `blocked`; the reason is checked on the page and a missing one sends
  nothing (spec step).
- Downloads are plain `<a href download>` (same origin, cookie, GET needs no CSRF header; T24a
  answer). They carry the address's days and patient only; over 31 days or a bad range there is
  no link, only the message. The T24a A2 residue (`Sec-Fetch-Site: none` on the export) is not
  changed: the page does not make a fetch plus blob.
- Loading, empty and error states on lists, the sheet and history; DD-MM-YYYY, IST throughout.

Tests:
- Ran here (free memory 2.6 GB, `NODE_OPTIONS=--max-old-space-size=1800`):
  `node --import tsx --test src/Utils/ihr/*.test.ts` 338 pass, 0 fail (packs 35, audit 38, menu 23);
  `npx tsc --noEmit` and `npx tsc -p tests-ihr/tsconfig.json` clean; prettier clean; eslint on the
  changed sources: no errors (only the usual "key missing in public/locale" warnings of our own
  keys); `scripts/check-ihr-frontend.py` ok; `merge-locale.py --check` ok. packs.test.ts and the
  audit export tests were written first and failed (module and exports missing) before the code.
- Left to CI (not run here: they need the dev stack and a build): `tests-ihr/pack-updates.spec.ts`
  (new: admin.priya opens the seeded item, Keep without a reason sends nothing, Keep with a reason
  is a 200, the item is under Kept as ours without a Keep button, History shows it with the
  reason), `admin-audit.spec.ts` (two new steps: the links and a real CSV download; a range over
  31 days has no links) and `roles-menus.spec.ts` (the new menu item for the two admins; every
  other login gets "not part of your role" for `ihr/packs` and makes no `/api/ihr/packs/` call).
  The pack spec needs the seeded waiting item (`scripts/care-dev.sh seed`) and fails with a
  message saying so if there is none. Not looked at in a browser: the 360 px layout of the totals
  select (a `max-w-full` fix) and the sheet on a phone.

## Review

Reviewers P (access/packs lens) and W (web lens), each with a challenger; fixes by the Sonnet
fixer, 22-11-2026, on the Opus fixer's tree. Severity is reviewer, then challenger, then recheck.

| id | finding | severity | fix | test |
|---|---|---|---|---|
| W1 | `px-2` on the "By day" select overrides the forms plugin's right padding, so the arrow sits over the text | medium, medium, medium | `pl-2 pr-8` on the select in `IhrOwnerTotals.tsx` and `IhrAuditTrail.tsx` | `totals.test.ts` "the 'By day' select keeps room" (fails with `px-2`); `owner-totals.spec.ts` new 360x800 step: select and page `scrollWidth <= clientWidth`, padding-right >= 32 px (CI) |
| W2 | no P1-T24c row in `docs/UPSTREAM_PATCHES.md` | medium, medium, medium | one dated row: new files and edits to our ihr files | doc only |
| P1 | our session classes send no `WWW-Authenticate`, so a signed-out or expired session is a 403 "Authentication credentials were not provided.", shown as "only for hospital-wide admins"; the 401 branch was dead | medium, low-medium, medium | `packs.ts` `isSignedOut` (403 with that exact DRF text, and 401) in `answerOf` and `loadErrorKey` give `ihr_packs_error_signed_out`; design table rows 5a/5b | `packs.test.ts` rows 5a2 and the load-error case (fail with the check removed) |
| W5 | `silent: true` skips session-expiry handling; a Retry that cannot work | low, nit, low | folded into P1; `canRetryLoad(status)` hides Retry for 401 and 403 in the list and sheet errors. A shared redirect for all ihr pages stays out of scope | `packs.test.ts` "offers a retry only where one can work" (fails if always true) |
| P2 | the release gate's 503 `ihr_upgrading` showed "could not be saved" | low, low (premise wrong: it has a code), low | `answerOf` and `loadErrorKey` map it to new `ihr_packs_error_upgrading` (en, hi, CSV); design table row 14 | `packs.test.ts` row 14 and the load-error case |
| P3 | `make_waiting_pack_item` can raise (RuntimeError, ProvisioningError) and aborted the seed before the passwords | low, low, low | `ihr_seed_demo._waiting_pack_item()` catches both, writes a line to stderr and reports `pack_item_waiting_added: "failed"` | `ihr_refdata.tests.test_seed_pack.SeedCommandSurvivesAFailedPackItem` (3 tests; red before: no helper) |
| P4 | `make_waiting_pack_item` had no DEBUG guard of its own | low, nit, low | `if not settings.DEBUG: raise RuntimeError` inside; the existing class runs under `override_settings(DEBUG=True)` because Django's test runner turns DEBUG off | `test_it_refuses_outside_development` (red before: "RuntimeError not raised"); it also checks nothing was renamed |
| P5, W8 | the spec kept the only seeded item and never accepted it back | low, low, low | new step in `pack-updates.spec.ts`: Accept from the Kept tab with a reason, POST 200, notice "Accepted: ..." | CI Playwright (not run here) |
| W3 | the Booking-card rule was inline JSX, untested | low, low, low | `ihrShowBookingCard` in `frontDesk.ts`, used by `IhrEncounterStatus.tsx` | `frontDesk.test.ts` null, `{}`, `{token_slot: {}}`, booked_on, slot start (fails when the rule returns `Boolean(booking)`) |
| W4 | deciding the only item on the last page left empty rows and no Previous | low, low, low | `clampOffset(offset, count)` and an effect in `ListPanel` step back to the last page with rows | `packs.test.ts` "steps back to the last page" (fails when it returns the offset) |
| W6 | "1 patients'", "1 more are not shown" | low, low, low | `_one` and `_other` keys in `en.json` and `hi.json`, rows in `hi-batch-6.csv` (239), README count; new `ihr_packs_error_upgrading` row | `packs.test.ts` locale test: both forms, no bare key, in en and hi (4 failures before) |
| W7 | design §3 asked for `scripts/check-ihr-frontend.py` entries; it has no anchor for our own items | low, partly refuted, low | the step is dropped in design.md "Review decisions": no code gap | doc only |
| P6 | design row 12 said "same phrase as 12"; the `api.py` docstring said every non-field-keyed refusal has a `code` | nit, nit, nit | row now says 11; docstring names the non-JSON 400 as the exception | doc only |
| P7 | refuted: the pinned query counts (24 and 22) already catch a per-row lookup | | none | |

## Verification

Verifier (Opus, high), 22-11-2026. I wrote none of this build. Before starting I recorded the sha256 of
all 38 changed or new files from `git status`. After the checks and revert proofs, all 38 were
byte-identical (`sha256sum -c`) and `git status` matched, before this section was added. There is
**no LATE FIX**. Graphify calls: 9 (`explain` 8, `affected` 1). The new symbols were not in the
graph yet, so they were read directly.

| check | result |
|---|---|
| Backend: `IHR_DB_ROLES=test scripts/care-tenancy.sh test --settings=ihr_platform.settings.test --noinput ihr_refdata ihr_refdata_registry ihr_audit ihr_access ihr_identity ihr_platform` | PASS: `Ran 1175 tests in 1132.453s`, `OK (skipped=45)`, "Redis audit: 927 keys written, all tenant-prefixed." |
| `ihr_refdata.tests.test_seed_pack`, `-v 2`, on the restored tree | PASS: 8 tests, OK, 0 Redis keys |
| Web unit tests (2.0–2.2 GB free, `NODE_OPTIONS=--max-old-space-size=1800`): `node --import tsx --test src/Utils/ihr/*.test.ts` | PASS: 351 tests, 351 pass, 0 fail |
| `npx tsc --noEmit`; `npx tsc -p tests-ihr/tsconfig.json` | PASS: both exit 0 |
| `scripts/merge-locale.py --check` | PASS: en 382 of ours, hi 4927 of ours + 13 corrections. Every en key is also in hi; all 111 new pack, export and menu keys are in `hi-batch-6.csv` (239 rows, as the README says) |
| `pre-commit run --all-files` | PASS: exit 0, 45 hooks passed, no file modified |
| Playwright (`pack-updates`, `admin-audit`, `roles-menus`, `owner-totals` 360 px step), eslint | not run here: left to CI |

**Revert proofs.** I reverted each fix on its own, ran the test, then restored the file and checked
it by sha. Each named test failed for its own reason:
- P1: `isSignedOut` set to false. Failed: "row 5a2: 403 {Authentication credentials…}" and "a signed-out 403 … is 'signed out'".
- W5: `canRetryLoad` set to true. Failed: "offers a retry only where one can work".
- P2: the `ihr_upgrading` branches disabled. Failed: "row 14: 503 …ihr_upgrading" and "a release or restore hold … is 'try later'".
- W4: `clampOffset` returns the offset. Failed: "steps back to the last page that has rows…".
- W3: `ihrShowBookingCard` returns `Boolean(booking)`. Failed: "shows only when a booked time or a slot time is there".
- W1: both selects back to `px-2`. Failed: both "no select class list uses px-2" cases (IhrOwnerTotals, IhrAuditTrail).
- W6: the `_one`/`_other` pairs collapsed to a bare key in en and hi. Failed: all four locale cases.
- P3 and P4, run together:
  - The P4 guard set to `if False`. Failed: `test_it_refuses_outside_development`, with "RuntimeError not raised".
  - The P3 except narrowed to KeyError. Two tests errored: `test_a_runtime_error_is_reported_as_failed` (RuntimeError: x) and `test_a_busy_hospital_is_reported_as_failed` (ProvisioningError: busy).
  - Result: `FAILED (failures=1, errors=2)` of 8.

**Spot checks of claims (11).** Correct:
- `seeding.py` 263-265 constants and 268 `make_waiting_pack_item`, DEBUG guard at 292.
- `menu.ts:48`: `packs` after `audit`, only under `hospitalAdmin`.
- `IhrRoutes.tsx:116`: the route.
- `IhrOwnerTotals.tsx:196` and `IhrAuditTrail.tsx:750`: `pl-2 pr-8`.
- `release_gate.py:90`: the 503 `{"detail": "upgrading", "code": "ihr_upgrading"}`.
- `test_facility.py:626`: query pins 24 and 22.
- P1's premise holds: `StaffSessionAuthentication.authenticate_header` returns "" (`authentication.py:132`). Every refusal path in `authenticate` returns None, so an expired session is DRF's NotAuthenticated 403 too.
- The export link matches the backend: 31 inclusive days, `file=csv|json` (`ihr_audit/views.py:306-322`, `export.MAX_DAYS`).
- Every `code` the POST sends (`api.py` 327-418) has a row in `answerOf`.

Stale after the fixers' edits (nit, no code impact):
- The "Files" table line numbers are one behind:
  - `api.py` `facility_name` is at 130, not 129;
  - `_refused` is at 235, not 234;
  - the engine's 400 is at 416, not 415;
  - `ihr_seed_demo.py`'s call is at 175, not 174.
- `AnswerKindTests` now has 9 tests, not 8.

Orchestrator note (2026-11-22): the verifier's line-number nits above are now applied to the Files table.
