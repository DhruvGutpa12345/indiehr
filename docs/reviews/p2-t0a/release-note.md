# P2-T0a release note: CARE backend 67a46185e → ddfd840db (2026-11-23)

For the `scripts/staging.sh release` of this build and its release log entry (data review F5).

**This release cannot be undone without `--restore`.** CARE's `users 0028_multiple_changes`
removes, with no copy, the staff fields `doctor_medical_council_registration`, `qualification`,
`doctor_experience_commenced_on`, `home_facility`, `skills`, `verified`, `video_connect_link` and
`weekly_working_hours`, and drops the `Skill` and `UserSkill` tables. Its reverse migration only
re-creates empty columns. A plain `staging.sh rollback` is refused anyway, because the hospitals'
schema fingerprint changes (`scripts/release_log.py`); only `rollback --restore` to the release's
restore point brings the values back, and it loses whatever was written after that point. Today every hospital holds synthetic
data only, so nothing real is lost, and no seed, Keycloak sync or plugin code writes the dropped
fields. From now on a doctor's NMC or state medical council registration lives in
`User.qualifications[].registration_number` (with `issuer` and `degree`, CARE's
`UserQualificationSpec`); P2-T1 must use that field and must not add its own.

**Expect a longer release window and an audit burst.** CARE's `emr 0084_multiple_changes` rewrites
rows in every hospital as the fleet migrates it: it computes `questions_hash` for every
questionnaire row (one save each) and back-fills `has_children`, `has_sub_locations`, `has_beds` and
`cached_parent_json` on every location row (one UPDATE over the table), and it converts every
specimen definition's `collection`. Each changed row writes an `ihr_audit` entry under the
migrating process's name, so every hospital's trail grows by about one entry per questionnaire,
location and specimen definition, and the next seals take longer. Our own `ihr_identity 0005`
fills CARE's new `User.role_org_ids` for people who already had a role-organisation membership (the
hospital root's admins included), and `ihr_audit 0008` re-creates each hospital's triggers so that
`role_org_ids` is left out of the trail like `cached_role_orgs`, and a location's
`cached_parent_json` like an organisation's (so the refill after `0084` is not recorded against
the first reader). Allow for this in the release window
of a hospital with many forms or locations. `0084`'s `questions_hash` save passes the answered-form
guard on hospitals with answered forms: the column is free in both the old guard (it compares only
its listed frozen columns) and the new one (`ihr_refdata 0006`, `forms_guard.FREE`).

**Fingerprint change: `scripts/deploy.sh` needs `--release`.** New migrations: `ihr_refdata
0006_forms_guard_care31` (the answered-form guard now covers CARE's two new answer tables and its
new form columns) and `0007_forms_guard_revisions` (an answer's revision moves only to its form's
current revision, and the archive copy holding an answered revision's questions stays frozen;
every hospital needs the fleet migrate for it to take effect), `ihr_audit 0008`, `ihr_identity 0005` and `ihr_access 0003` (the placement
guard's new fingerprint). Restoring a single-hospital dump taken before this release reports
`functions_differ` for `ihr_questionnaire_guard`, as designed (`restore/engine.py`).

**Reference pack `care-valuesets` 2 (36 sets) replaces 1.** On the next start each hospital's
unedited rows move to 2; a row the hospital edited waits for its admin on the pack updates page.
Clinical change, accepted by the owner on 2026-11-24: CARE narrowed `system-condition-code` to diseases (`<< 64572001`)
and moved symptoms to the new `system-symptom-code` (`< 404684003 MINUS << 64572001`, Q-K167), and
added four procedure sets.

**Behaviour changes staff may notice** (owner decisions Q-K163…Q-K165): CARE's new referral pages
and its configured actions are closed to restricted roles, and "referrals assigned to me" is empty
for anyone whose roles here do not include reading referrals; editing a form that already has
answers is refused (make a new version); a hospital admin can no longer narrow which departments
see a pack form (CARE 3.1 allows only superusers; our own setting comes later).

Also switched off on every instance by this build: CARE's anonymous Prometheus `/metrics/`
(settings, `secure.py`, `blocked.py`) and CARE's test action instruction `logging` and test field
`TestContext1` (`ihr_platform/actions.py`, check `ihr_platform.E028`).

**Known, not blocked:** a stale CARE form submit can still record an answer at the previous
revision when an edit of the not-yet-answered form commits in between (upstream's race); that
revision's archive copy is now frozen, so its questions stay readable.
