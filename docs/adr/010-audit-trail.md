# ADR-010: Audit trail in each hospital's schema, recorded by the database

- **Status:** Accepted (owner's answers of 2026-11-07)
- **Date:** 2026-11-07
- **Task:** P1-T6a (recording, hash chain, verify); P1-T6b (viewer, export, outside fingerprints; §7–§9, owner's answers of 2026-11-08).
- **Builds on:** ADR-002 (schema per hospital), ADR-009 (staff sessions). Replaces CARE's own audit log, which stays off (TENANCY_DESIGN §6.12).

## Context

NABH, DPDP ("who looked at my record") and medico-legal work need to answer, per hospital: who created, changed, deleted or viewed which patient's data, when, from where and why. The trail must be complete and hard to alter quietly.

CARE's audit log is off by default. It writes only to a log file and records no reads. Its thread-local also survives GET requests, so in a multi-hospital process it could name the wrong user.

The owner decided four things:
- **Split:** T6a (this) and T6b.
- **Values:** keep old and new values, with identifiers masked.
- **Reads:** every patient shown counts, lists and searches included.
- **Purpose:** inferred now; staff are asked later, with break-glass.

## Decision

**1. One trail per hospital, in its own schema.** It has two plain-SQL tables:
- `ihr_audit_entry`;
- `ihr_audit_seal`.

The trail stays in the hospital's schema, so it never mixes hospitals. It is backed up, restored and exported with the hospital, and dropped only when the hospital's schema is dropped. Both tables refuse UPDATE, DELETE and TRUNCATE.

**2. Row changes are recorded by the database, not by Django.**
- Every hospital table has an AFTER INSERT/UPDATE/DELETE row trigger and an AFTER TRUNCATE trigger.
- The exceptions are the trail itself, `django_migrations` and Django's unused `django_session`.
- Bulk updates, raw SQL, Celery tasks and management commands are covered too.
- Each change is a `write` entry:
  - an insert keeps the new row;
  - a delete keeps the old row;
  - an update keeps each changed column as `[old, new]`.
- Each entry names the patient(s) the row belongs to, old and new.
  - Rows linked only through an encounter are looked up through it.
  - Rows linked through another record are looked up with SQL that `install.py` builds from the same links the read side follows. Examples: a payment's account, a note's thread, a file's owner.
- A save that changes only `modified_date`, or only the `temp_deleted` flag CARE's role sync flips on every start, is not recorded.
- The triggers are installed after every migration run of a schema, so tables added later are covered.
  - Only what differs is changed. Re-creating a trigger blocks writes to its table, so it runs under a 5-second lock timeout.
- `coverage()` reports, and the tests fail on, any of these:
  - a table without both triggers enabled;
  - a trigger with out-of-date settings;
  - any partitioned table, which must be reviewed before it can be audited.

**3. What is never copied.**
- **Secrets are hidden** (recorded only as "(hidden)"): passwords, TOTP secrets, MFA settings, push keys, API tokens and OTPs.
- **Patient identifiers** (e.g. ABHA) keep only their last 4 characters.
- **Not recorded:**
  - the identifier and organisation caches CARE keeps on the patient row (their source rows are audited);
  - CARE's own per-row `history`.
- **Columns with secret-sounding names** (password, token, key, otp, aadhaar, abha, session…) must be classified before a test passes, so a new upstream column is reviewed.

**4. Who and from where.**
- The middleware sets two Postgres session settings at the start of each hospital request and clears them at its end:
  - `ihr.audit_ctx`: request id, client address, forwarded-for, method, path, query and purpose;
  - `ihr.audit_actor`.
- The actor is bound right after DRF authenticates. That covers every authentication class, ours and CARE's patient-OTP one.
- Actor names:
  - staff: `user:<id>:<username>`;
  - machine accounts: `service:…`;
  - patients: `patient:***1234`;
  - Celery tasks: `task:<name>`, set in task_prerun and cleared in task_postrun;
  - anything else: `system:<process>`, the process name from `IHR_PROCESS_NAME` or e.g. `manage:ihr_create_tenant`, `celery`.
- The values are also kept per thread and put on any connection opened later in the same request or task. Celery closes the connection right after a task starts.
- A task run inside a request keeps the request's actor and gives the request back its values afterwards.
- Our own sign-in, join and sign-out views name the person as soon as they know who it is.
- **Constraint for P1-T8:** a connection pooler must run in session mode or not at all. Transaction pooling would mix up these settings.

**5. Reads: one `request` entry per request that showed patients or tried a change.**
- While a request runs, every model instance loaded that belongs to a patient is noted. That includes:
  - the patient itself;
  - anything with a patient link;
  - anything with an encounter link;
  - anything linked to a record with a patient link (a payment's account, a comment's request);
  - files, drawings and generated reports, through their type and owner id.
- One receiver handles every model, so models CARE registers late (several are imported only with its URLs) are covered.
- "Shown" errs towards more: everyone loaded while handling the request is listed. That can include a relative who matched a phone search but was not returned. Missing a patient who was shown would be the worse failure.
- The entry keeps the patient ids (up to 1,000, with the total), the HTTP status and the context.
- It is written outside CARE's per-request transaction, so refused and failed attempts are recorded too.
- If it cannot be written, the request fails (no trail, no access).
- CARE models with no patient link are listed in `unlinked_models.txt`, each with a reason. A new one fails the tests until it is reviewed.
- **Purpose** is inferred from the API: billing, admin or care. Asking staff comes later with the web app and break-glass.

**6. Hash chain.**
- A beat job seals each hospital's new entries every minute.
- Seal *n* covers every entry whose writing transaction id is in `[lower, upper)`:
  - `upper` is never above the oldest transaction still running, so an entry cannot appear in a window after it is sealed;
  - windows are contiguous from 0, so every entry is in exactly one seal.
- The hashes:
  - `entries_hash` = sha256 over the per-entry sha256 of a canonical text, which the verifier builds itself;
  - `hash` = sha256(previous hash ‖ "n|lower|upper|count" ‖ entries_hash).
- `ihr_audit_verify --slug x | --all [--seal]` recomputes everything and checks trigger coverage. It exits 1 on any problem, including finished entries left unsealed while nothing was sealed for 10 minutes.
- Entries held back by a transaction still running anywhere in the database cluster are reported as a warning, not a problem.

**7. Viewer for hospital-wide admins (P1-T6b).** `/api/ihr/audit/`:
- `entries/` lists newest first, with filters: day range (IST), patient, kind, operation, table, actor prefix. Each row shows who, when, from where, which record, which patients and which fields changed, **never the values**.
- `entries/<id>/` shows one entry with its old and new values. Identifiers are still masked.
- `status/` shows the chain head and trigger coverage.
- `export/` and `verifier.py` are covered in §8.

Who may use them:
- Only staff with an admin role on one of the hospital's **top-level, active** organisations. A team's or facility's admin may not.
- The same check now guards the staff-access actions (P1-T5e2).

What is recorded:
- Every call is recorded, refused ones included, as a `request` entry with purpose `audit`.
- Patients whose names it showed count as viewed.
- Searched values in query strings are masked when recorded (and when shown, for older entries): phone numbers keep their last 4 digits, names and other free text become `***`, ids and paging stay.

**8. Export, at most 31 days, each export recorded with every patient it shows.**
- An export showing more than 1,000 patients is recorded as several entries under one request id.
- **CSV** is for people:
  - IST times as DD-MM-YYYY;
  - which fields changed, no values;
  - cells that a spreadsheet would run as formulas are neutralised.
- **JSON Lines** is for an outside auditor, checked with `verify_export.py` (standard-library Python, also downloadable from the viewer). The export holds:
  - every sealed window holding the range's entries, plus one window on each side, each with a line per entry: exported entries carry their details but never their values; every other entry shows only its time, patient ids and a hash of its details;
  - seal headers on to the **earliest** fingerprint written after the last window, named with its object version and storage time.
- **What the script checks:**
  - every hash;
  - that no entry of the range (or patient) is a bare hash;
  - that the full windows start before the range (or at the first seal) and end after it;
  - that the fingerprint matches a seal and was written within 3 hours of the last window;
  - one header, nothing after the end line, and patient ids that line up.
  - It prints the hospital, range and patient. `--hospital/--from/--to/--patient` check them against the request.
  - **Exit 0:** proven. **Exit 3:** nothing contradicts but part is not proven, e.g. the newest hour, older formats, or no request given. **Exit 1:** altered.
- **How the export is built:**
  - The range ends when the export is recorded.
  - The windows and the patients they carry are counted then; the request entry lists every one.
  - The stream reads one database snapshot.
- **Seal format 3** makes this possible:
  - an entry's canonical text is `[at, patients, sha256(details)]`;
  - the details hold `sha256(salt ‖ values)`, where `salt` is 16 random bytes per entry that never leave the database. Without the salt, short values (a status, a blood group, the last 4 digits) could be found by trying every candidate.
  - Formats 1 and 2 (before the review) still verify, but their entries are exported as hashes only.
- **Not proven by an export:**
  - an entry whose time is in the range but which sits outside those windows (a transaction left open across the range's edge);
  - unsealed entries beyond their own hash;
  - that an empty range is empty;
  - the external patient ids next to entries (the platform's word, checked only for consistency).
- **A patient's export** carries the hashes of every entry in the windows it spans. That can be the whole range's trail: the price of proving nothing was left out.

**9. Outside fingerprints, hourly, in a write-once bucket.**
- **What is written:** Celery beat sends the job once for the platform (`PLATFORM_ONCE`, not once per hospital). It adds one JSON object per run listing each live (not provisioning) hospital's newest seal: number, hash and address. Each object is written once (`If-None-Match`). The object goes under `anchors/<platform>/YYYY/MM/DD/` in an S3 bucket with Object Lock.
- **Two credentials,** read from the environment, never Django settings:
  - Celery's may only add objects;
  - the verifier's may only read and list.
  - Neither can delete or change retention (checked live on dev MinIO).
- **What `ihr_audit_verify` checks:** every hospital against every fingerprint, or from `--since` for frequent runs. It reports:
  - a seal that differs (rewritten) or is missing (cut);
  - a hospital dropped from newer fingerprints;
  - a fingerprinted hospital no longer in the database, or whose address now belongs to another hospital;
  - a job more than 2 hours late;
  - delete markers, unreadable objects, and an object written twice;
  - an object whose stated time is more than 10 minutes from the storage's own time (the storage's time orders and dates fingerprints).
  - Claims are checked against the database in batches, so memory stays small however many fingerprints there are.
  - a bucket without a lock.
  - Outside dev it also reports retention that is not COMPLIANCE, unversioned buckets, and keeping objects for less than 7 years.
  - A storage outage is reported while the local checks still run.
- **Buckets:**
  - **Production (P1-T8):** an India-region bucket in COMPLIANCE mode for 10 years, in an account the database admins do not hold. *(Owner, 2026-11-08, ADR-011 §3: the bucket lives with the server's provider; on E2E that is Governance mode, accepted with a separate account and an explicit `IHR_AUDIT_ANCHOR_LOCK_MODE=GOVERNANCE`.)*
  - **Dev:** `scripts/care-dev.sh audit-anchors` makes a GOVERNANCE-mode bucket (30 days) in the local MinIO. Its root keys are in the dev containers, so dev does not show the guarantee; the checks say so.

## What this does not protect against (yet)

- **A database owner** can still rewrite or cut what was sealed after the newest fingerprint, up to about an hour (§9 catches everything older), and remove entries not yet sealed (about a minute).
- **Fingerprints are only as safe as the bucket's account.** Production must keep it apart from the database admins (P1-T8).
- **Reads that never build model instances** (`values()`, raw SQL) are not noted. CARE's API serialises model instances.

## Consequences

- Every write costs one more insert. Patient-linked rows written only through an encounter cost one indexed lookup.
- The trail grows with use. Its retention is decided with the retention engine (P4-T3); until then nothing deletes it.
- The trail holds patient data and gets the same protection as the records: the same schema, backups and access rules.
