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
