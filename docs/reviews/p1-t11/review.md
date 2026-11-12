# P1-T11a independent code review (2026-11-12)

Two fresh Opus reviewers who did not write the code: **correctness and operations**, and
**security**. A second agent tried to refute each finding. Of 12 findings, 6 were confirmed,
5 partly confirmed and 1 refuted. Several findings were the same issue seen through both lenses.
Every confirmed or partly confirmed finding is fixed. The design attack (24 findings, 22
confirmed) is summarised in design.md and in the run file.

| # | Finding | Verdict | Fix | Proof |
|---|---|---|---|---|
| C1 / S5 | The guard read soft-deleted roles, which CARE's sync never touches, so one deleted row could stop every start for good | confirmed, medium | candidates chosen with CARE's own default managers | test "a soft-deleted hospital role never blocks" |
| C2 / S2 | A hard-coded holder list missed RoleAssociation and any relation a CARE plug adds | confirmed, low | every reverse relation of RoleModel is counted except RolePermission | test "every reverse relation is counted" |
| C3 | CARE's hard delete of removed permissions cascades to hospitals' own roles | partly, low | refused while a hospital's own role holds the permission | test "permission removed but granted" |
| C4 / S3 | Check-then-act race with CARE's sync | confirmed, low | the check and the sync run in one transaction, with the doomed roles locked | code review |
| C5 / S1 | The ADR overstated rollback protection: images older than P1-T11a have no guard | confirmed, medium | ADR-017 and the staging runbook say "never roll back past P1-T11a" | docs |
| S4 | "Refuses before changing anything" held per hospital, not across hospitals | partly, low | start-up runs `--check` for every hospital before any sync | compose files; test "check mode" |
| S6 | The docs misdescribed what PATIENT_GLOBAL_EDIT_ACCESS_ENABLED does | partly, low | wording corrected (any signed-in user can open any patient by id; `can_write_patient` anywhere edits any patient) | ADR-017 |
| S7 | django_tenants no longer first in INSTALLED_APPS | refuted | django-tenants' check accepts it; the tenant suites pass | test runs |
