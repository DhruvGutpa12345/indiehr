# P1-T20 design: anonymous routes and the §6.5/§6.8 patches (draft for attack, 2026-11-13)

PLAN P1-T20 (Phase 1 review R1-02, R1-03 and its challenge; owner answer Q-T3: presigned URLs,
no public bucket). Research with file:line evidence: the run's scratchpad `t20/research.md`.

## What is wrong today

1. **The public doctor list leaks staff details.**
   - `GET /api/v1/facility/<id>/schedulable_users/` (no sign-in) returns each scheduled staff
     member's username, **phone number**, last sign-in, gender and roles. It does so for **any**
     facility, public or not.
   - Computing the roles also writes to `users_user`, so an anonymous GET leaves an audited
     write.
   - The web app shows only the name, the title and the photo.
2. **Organisations:** `GET /api/v1/govt/organization/` lists every organisation type to anyone
   (`?org_type=team|role|…`). The web app only ever asks for government geography.
3. **Batch requests** (`/api/v1/batch_requests/`) run any route with any method inside one
   request. Each sub-request is authenticated again, so this is defence in depth, not an open
   hole.
4. **Pictures** (staff photos, facility covers) are served from a **public-read** bucket with
   unsigned addresses, against your Q-T3 answer.
5. **One storage credential** reads and writes both the patient-files bucket and the pictures
   bucket. That is an upstream bug: CARE ignores its own `FILE_UPLOAD_*` settings.

## Split (by Claude, 2026-11-13, awaiting owner confirmation: Q-K31)

- **P1-T20a: routes** (plugin overrides, no upstream patch): the doctor list, organisations and
  the batch allow-list.
- **P1-T20b: files** (small logged upstream patches plus storage set-up): signed picture links,
  the public bucket removed, separate credentials.

## P1-T20a

1. **Doctor list** (`ihr_platform/public_routes.py`, routed in `urls_tenant.py` before CARE's
   routes, so batch sub-requests get it too). The list and the detail return only id, first and
   last name, prefix, suffix and the photo link. They:
   - list only **public** facilities;
   - list only active, not deleted staff;
   - never compute roles (no anonymous write).
   Both stay anonymous: the web app shows doctors before the patient signs in.
2. **Organisations:** the public route answers government organisations only. A team or role
   organisation's detail answers 404.
3. **Batch allow-list:** our batch view checks every sub-request **before** running any:
   - a relative `/api/v1/…` path;
   - a route and method on a reviewed `batch_allowlist.txt` (the routes the web app batches:
     questionnaire structured answers, locations, pharmacy dispensing, deliveries, payments,
     tokens, appointments, diagnostic reports, specimens);
   - no nested batch.
   One refused sub-request refuses the whole batch (403s in the shape the web app already
   handles), and nothing runs.
4. **Tests:**
   - the exact fields returned;
   - non-public facilities hidden;
   - no audit write on an anonymous GET;
   - organisation types refused;
   - each batch refusal, and the database unchanged after it;
   - every allow-list entry still resolves (catches stale entries after an upstream sync);
   - the existing batch isolation test moved to allowed routes;
   - mutation rows for the field trim, the government filter and the batch check;
   - the allow-lists updated with reasons.

## P1-T20b

1. **Signed picture links.**
   - Two logged one-line patches (`facility.py`, `users/models.py`) and a helper in the
     already-patched `cover_image.py` call our hook, `ihr_platform/storage.py:facility_image_url`.
   - The hook refuses a key without this hospital's prefix, or any key from `public`. Otherwise
     it returns a **24-hour signed link**, cached for 12 hours under a hospital-prefixed key, so
     the browser's image cache still works. Every upload makes a new key, so a cached link is
     never stale.
   - The web app falls back to initials if a link fails.
2. **The public bucket goes.**
   - `setup_buckets.py` removes the public-read policy.
   - The live check flips: anonymous reads are denied, and a signed link works.
   - The staging smoke expects 403.
   - The README is updated.
   - `secure.py` refuses `FACILITY_CDN` (unsigned) and `BUCKET_HAS_FINE_ACL`.
3. **Separate credentials.**
   - A logged patch in `care/utils/csp/config.py` makes the patient and report buckets use
     `FILE_UPLOAD_*`. These default to the old key, so upstream behaviour is unchanged when they
     are unset.
   - The storage identity is split into `ihr-care-files` (patient bucket) and
     `ihr-care-pictures` (pictures bucket).
   - `s3-init` and `care-dev.sh` write both.
   - The live check proves neither key can touch the other bucket.
   - `secure.py` refuses equal keys.
   - On AWS with role-based access, one process has one role, so separation there relies on
     bucket and IAM policy. That is noted, and is the provider module's job.
4. **Tests:**
   - an isolation test: a hospital's own key gives a signed link, another hospital's key gives
     none;
   - a mutation row: an unsigned link fails a test;
   - a unit test of the credentials per bucket;
   - the `secure.py` rules;
   - the live S3 check and smoke in the dev stack and the local staging proof.
   TENANCY §6.5, §6.6, §6.8 and §10 are updated.

## Decisions (auto-accepted, awaiting the owner)

- **Q-K31:**
  - the split;
  - the doctor list stays anonymous but trimmed to name, title and photo, public facilities
    only;
  - signed picture links last 24 h (anyone holding a current link can view the photo);
  - the batch allow-list is enforced at once;
  - on AWS role-based access, bucket separation relies on policy.

## After the attack (2026-11-13): the design as it is built

One Opus reviewer covered correctness and security, and a second agent tried to refute each
finding. Result: 10 findings, 0 refuted, 5 confirmed, 5 partly. Where this section and the
sections above disagree, this section wins.

1. **The patient-OTP appointment routes leak the same staff details (A1, High).**
   `/api/v1/otp/slots/…` (create, list and cancel appointments; open to anyone who can receive
   an OTP) returns the doctor's full staff record and the front-desk staff who booked. P1-T20a
   overrides these routes too.
   - **Fields kept:** id, slot, booked on, status, note, resource type, a minimal facility, the
     patient (OTP view), dates; the doctor as the same public projection as the doctor list
     (id, names, prefix, suffix, photo); the token trimmed to number, status and category.
   - **Fields dropped:** booked by, created by, updated by, charge item and tags.
   - **Tests:** the exact keys at every level, including a booking staff made.
   - A mutation row restores CARE's spec.
2. **The batch allow-list comes from the web app itself (A2, High).**
   - `batch_allowlist.txt` is built by extracting every route and method used in a batch from
     the 19 call sites. Each entry names its call site.
   - It includes the staff appointment route, the organisation links (encounter, location,
     device), prescriptions and dispense lookups.
   - A check (pre-commit, CI and the after-sync checklist) fails when the web app batches a route
     that is not reviewed.
   - Every refusal is logged with its route and method, and alertable.
   - Patient-token (`otp/*`) routes never belong in a staff batch.
   - The browser tests gain a department link.
3. **The batch check judges what will actually run (A3):** after conversion to a request, the
   decoded `path_info` and the uppercase method are checked, and exactly those request objects
   are executed. No `X-HTTP-Method-Override` is honoured.
4. **How overrides are installed (A4):** the router's class is swapped from `AppConfig.ready()`,
   before the URL configuration is imported. That covers every path and format variant, batch
   sub-requests included. The doctor list uses plain list and detail routes, so the crawl can
   fill them.
5. **Only doctors with a public, current schedule (A5):** list and detail share one queryset:
   - a public facility;
   - practitioner resources with a public schedule still valid today;
   - active, not deleted staff.
6. **No audited writes on anonymous GETs (A6):** CARE's cached parent field on organisations is
   added to the audit's omitted cache columns (like the role cache). A cache-only save then
   writes no entry. A test proves that anonymous GETs write nothing.
7. **Signed-link cache (A7, A8):**
   - The cache key also hashes the key id, endpoint and lifetime, so a rotation or move simply
     misses.
   - Role-based (temporary) credentials are never cached beyond their own expiry.
   - The public schema returns no link without raising.
   - The prefix check also matches the expected key shape.
8. **Credential split replaces the old identity (A9):** the combined `ihr-care` identity and its
   key are removed. Older key files are migrated once, and the live check proves the old key is
   dead.
9. **Anonymous facility list (A10):** our override also serves `getallfacilities` without the
   raw storage key (only the signed link). TENANCY §6.8 records which identifiers stay public
   (the hospital's id, object ids, the pictures key id).

### P1-T20a as built (2026-11-13)

What runs (all plugin code, no edit inside `apps/`, nothing new in UPSTREAM_PATCHES.md):

- **Route swap** (`ihr_platform/route_swaps.py`, called first in `IhrPlatformConfig.ready()`;
  `route_swaps.require()` at the end of `urls_tenant.py` and the system check
  `ihr_platform.E020` refuse to serve or start when any swap did not take, review C4):
  `BaseRouter.register` is wrapped so CARE's own registrations of six viewsets register our
  subclasses instead: the doctor list, the patient-OTP slots, government organisations, the
  facility finder, the batch view, and users (`InvitingUserViewSet`, which before covered only
  the list route). Classes are named by dotted path, not imported in `ready()`: importing CARE's
  viewset modules there hits an import cycle in CARE (`resources.permissions`), so ours are
  imported when CARE registers, after `config/api_router.py` has loaded everything in its own
  order. `install()` refuses to start if a URL configuration was imported first; `check()` and
  the tests confirm no router entry still names a CARE class (DEBUG's DefaultRouter rebuilt from
  the registry included).
- **Public routes** (`ihr_platform/public_routes.py`):
  - doctor list and detail share `public_practitioners()`: public, not deleted facility;
    practitioner resource with a public, not deleted schedule that has not ended by IST
    calendar day (`valid_to__date >= localdate()`, as CARE's slot generator: the web app stores
    `valid_to` as 00:00 IST of the last day, which is still bookable; fix review C1) (A5: "valid
    today" means not yet ended, so a schedule starting next week shows the doctor; `valid_from`
    is not checked); active, not deleted staff. `PublicPractitionerSpec` returns
    exactly id, first and last name, prefix, suffix and the picture address, and never touches
    the cached roles;
  - patient-OTP `create_appointment`, `cancel_appointment`, `get_appointments`: CARE's action
    bodies (pinned by sha256 in `test_public_routes.UpstreamDrift`) with CARE's booking serialiser
    replaced by `PublicTokenBookingSpec`, which builds each booking from the model (fix review
    S1): CARE's serialiser put a location's current encounter (another patient, the care team as
    staff records) and its parent's copy into the booking, and on a cold copy saved it (an
    audited write from the OTP GET). A location is id, name, description, location_type, form,
    status, mode; a healthcare service id, name, service_type, internal_type; a doctor the public
    projection; any other type `{}`. `public_booking()` then keeps the same allow-list at every
    level as a second layer (booking, slot, availability, schedule, resource by type, facility
    id and name, token number, status and category id/name/shorthand; the patient's own OTP
    record unchanged);
  - government organisations only (`org_type="govt"` on the shared queryset, so a team or role
    detail is a 404);
  - facility finder without `cover_image_url` (the duplicate raw-key field; the key itself is
    still inside the unsigned `read_cover_image_url` until P1-T20b signs it, review C5).
- **Audit** (A6): `emr_organization.cached_parent_json` and
  `emr_facilityorganization.cached_parent_json` are in `ihr_audit/install.py` OMIT. Only
  post_migrate re-creates a hospital's triggers, and the fleet migrates a hospital only when a
  migration is new to it, so the change ships with an empty migration
  `ihr_audit/0005_omit_cached_parent_json` (fix review C2): every hospital is behind once, and
  the held migrate in the release window brings its triggers to this build. The migration
  states `AUDIT_TRIGGERS_SHA256` (`install.config_fingerprint()`: function SQL, column lists,
  sealing version); a test fails whenever the live fingerprint differs from the latest
  migration's, so a later trigger change cannot ship without one.
- **Batch** (`ihr_platform/batch.py`, `batch_allowlist.txt`, `scripts/batch-allowlist.py`):
  - the list is generated from the 22 web-app files that use the batch helpers: 37 entries
    (method and path template, each naming its call sites). One reviewed dynamic site
    (LinkDepartmentsSheet's `api: route`, the three "add" routes only; the removes are not
    batched), pinned to the file as reviewed (sha256 of its code without comments or spacing)
    and to one use of the variable, so any change to the file fails until re-reviewed (review C3). It fails on a variable it cannot resolve, a patient-OTP route, a nested batch, or
    code posting to the batch endpoint without the helpers. `--check` runs in pre-commit
    (always), Apps CI (frontend job, no new action) and the after-sync checklist
    (docs/UPSTREAM.md);
  - every sub-request is checked before any runs, on the objects that then run: method exactly
    one of GET/POST/PUT/PATCH/DELETE (anything else is refused before CARE's request factory
    sees it); no method-override header; raw URL relative, starting `/api/v1/`, without `%`,
    `;`, `\`, `?`, `#`, `//`, dot segments, whitespace, and equal to the decoded `path_info`; a
    GET has no body (it would become a query); not a batch; and (method, `path_info`) matches an
    entry's template (parameters `[A-Za-z0-9_-]+`) **and** resolves to the same route as the
    entry's own sample, so a list action behind a detail template (`dispense/summary/`) is
    refused;
  - one refusal refuses the batch: 400 with a 403 result per refused sub-request (CARE's batch
    error shape), and the fixed line
    `ihr-batch: refused hospital=<slug> reason=<code> method=<METHOD|other> route=<URL name>`
    on standard error; alert `BatchRequestRefused` (deploy/logs/alerts/batch.yml, warning, web
    backend only, whole line) with check-logs cases (3 real, 7 forged) and the staging README;
  - the run is CARE's `create` loop copied into `_run()` on the checked objects;
    `test_public_routes.UpstreamDrift` pins the sha256 of CARE's `create` and of its executor
    functions so an upstream change is reviewed.

Tests: isolation suite (new: doctor list incl. private-only, a schedule's last day at 15:00 IST
(listed) and one that ended yesterday (hidden), deleted schedule, inactive and deleted staff,
non-public facility; OTP list/create/cancel with a booking staff made; OTP list of bookings staff
made on a location (it and its parent holding another patient's encounter, the parent copy cold)
and a healthcare service: exact keys, nothing of the other patient or staff, no row write and the
copy still cold; govt only; facility finder keys; no audit entry on 9 anonymous GETs with every
parent copy expired; route swap; batch refusals, 17 cases, each with the database unchanged and
the write beside it not made; the old batch test moved to the batched dispense GET),
`ihr_platform.tests.test_public_routes` (route swap, the URL configuration and system check
refusing a missed swap, the system check run through Django's check registry, the outer
booking allow-list per resource type, every entry resolves to a view taking its method, check unit cases, drift pins incl. CARE's three OTP booking actions),
`ihr_audit` (the latest migration states the trigger fingerprint), `scripts/test_batch_allowlist.py`
(11, incl. a changed or doubly used DYNAMIC site). Mutation rows: public-doctor-fields,
public-doctor-public-schedule, public-doctor-last-day, public-otp-booking-trim (the outer
allow-list, unit test), public-otp-booking-from-model (CARE's serialiser back: the OTP GET
writes), public-govt-only, public-facility-raw-key-field, public-route-swap, route-swap-system-check
(the registration in apps.py), route-swap-required,
batch-allow-list, audit-omit-organisation-cache, audit-triggers-ship-with-migration.

Deviations from the sections above:

- Refusals answer 400 overall (CARE's own status for a batch with a failed sub-request), with
  403 for each refused sub-request; only the refused ones are listed.
- The refusal line names the route by its URL name, not its regex (the alert takes a plain
  token; no value from the request either way).
- The batch "database unchanged" check counts row-write audit entries (`kind = 'write'`): the
  audit middleware still writes its one `request` entry for the refused POST, by design.
- Three of our own P1-T4a tests batched `/api/v1/users/getcurrentuser/`, which the web app never
  batches: they now batch the dispense read of a missing id (a signed-in sub-request gets 404, an
  unsigned one 403), and a blocked CARE route inside a batch is now refused by the allow-list
  (403) before it would reach `Blocked` (404).
- The route swap also covers `/api/v1/users/` (A4 named its `.json` twin); the old
  `users-list` route string in `urls_tenant.py` stays, now redundant.
- The A3 mutation (judge the raw path) was not added as a row: with the strict parameter
  pattern, the URL-shape rule and the `path_info` equality each refuse the encoded slash on
  their own, so no single edit lets it through; the bypass cases are tests instead.
- Not done here (left open): the browser test with a department link and a questionnaire
  (Playwright, needs the frontend build); log-only period on staging (enforced at once, Q-K31);
  the same parent-cache column on locations, tag configs and resource categories (staff GETs
  only, still audited when refreshed; the patient-OTP booking routes no longer serialise
  locations through CARE, so they never refresh it).

Results (2026-11-13, this machine):

- plugin suites + isolation, app role (`IHR_DB_ROLES=test`): **Ran 607, OK (45 skipped)**; off
  mode: **Ran 574, OK (48 skipped)**; Redis audit 562 keys, all tenant-prefixed. Isolation crawl
  135-136 routes with own data per crawl, 1 skipped (as before); both public routes
  (facility and doctor detail) open anonymously in every run.
- CARE in tenant mode (`test_booking_api`, `test_facility_api`, `test_organization_api`,
  `test_user_api`, `test_questionnaire_response_api`): Ran 171, 5 errors, all
  `UserProfilePictureTestCase` reaching `http://minio:9000`, which the throwaway test container
  cannot resolve here (no S3 by that name in the dev stack; CI's tenant-mode job has it). No CARE
  test asserts a trimmed field; nothing added to `care_tests_excluded.txt`.
- `scripts/check-logs.py`: Log pipeline OK (BatchRequestRefused fires for alpha and kappa only;
  the 7 forged lines do not). `isolation-mutation.sh check`: 50 rows, every target and test
  found. All 8 new rows applied in a scratch copy and run locally: each named test FAILS.
  `pre-commit run --all-files`: all hooks pass (the Grafana server dashboard regenerated: 11 log
  rules).
- Click-through on the dev app (synthetic data): the doctor list and detail show the six keys;
  `org_type=team|role` 0 results, a role organisation's detail 404; the facility finder without
  `cover_image_url`; audit entries 1882 → 1882 over the anonymous GETs with every parent copy
  expired; a batch with GET `/api/v1/users/` refused (400, 403 inside, one `ihr-batch: refused`
  line), the encoded-slash PUT refused as `bad_url`; the patient-OTP list without any staff
  record.
