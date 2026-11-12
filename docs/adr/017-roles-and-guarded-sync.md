# ADR-017: Hospital roles in code, and a role sync that never deletes a held role

- **Status:** Proposed (auto-accepted by Claude in an unattended run, 2026-11-12; awaiting
  owner confirmation: Q-K22; the roles themselves wait for Q-K21)
- **Date:** 2026-11-12
- **Decided by:** Claude. Evidence: CARE's role and permission code, read for P1-T11
  (docs/reviews/p1-t11/), and a design attack by two reviewers (security/privacy;
  correctness/feasibility), each finding challenged. 22 of 24 were confirmed.
- **Relates to:** ADR-008 (identity: job roles live only in CARE), ADR-010 (audit),
  TENANCY_DESIGN §5 (amended)

## Context

PLAN P1-T11 adds eight Indian hospital roles. CARE defines its roles in code. Each permission
lists the roles that hold it. `sync_permissions_roles` runs at every start and writes these
into every hospital's schema as system roles, which hospitals cannot edit.

The design attack found two things that shape how roles may be added at all:

1. **CARE's sync deletes any system role no longer in code.** It uses a hard QuerySet delete,
   which cascades to every membership holding the role. A rollback to an image without one of
   our roles, or a renamed role, would remove staff from their jobs in every hospital, and it
   would do so at start-up. (A PatientUser holding the role makes the delete fail instead, and
   start-up stops.)
2. **CARE ties opening a patient to clinical-data rights.** So the front-office roles need an
   authorization override, and what they may see is a decision for the owner (Q-K21).

## Decision

1. **Our roles are code-defined system roles** (`plugins/ihr_platform/ihr_roles`), synced like
   CARE's own. They are not a "pack" of editable rows (TENANCY_DESIGN §5 amended): hospitals
   cannot change them, and a change is a reviewed code change.
2. **Role names are permanent identifiers.** `registry.PERMANENT_NAMES` freezes them. A role is
   retired by keeping it registered with no grants, never by removing or renaming it. Start-up
   fails if a name ever registered is missing, if a name clashes with CARE's, or if a granted
   permission slug no longer exists upstream.
3. **Grants rebind, never append.** `registry.grant()` gives the permission a new role list.
   Several CARE permissions share one list object, so an append would grant all of them and
   change CARE's own roles.
4. **The sync is guarded.** `ihr_roles` is listed first of all apps, so its
   `sync_permissions_roles` shadows CARE's. It refuses before that hospital changes, and so stops
   the start (the safe failure), when:
   - a system role CARE would delete is still referenced by anything other than CARE's own
     RolePermission rows (every reverse relation of RoleModel, soft-deleted rows included);
   - a permission CARE would delete is still granted to a hospital's own role;
   - a hospital's own (not deleted) role has a registered role's name.

   It chooses these candidates exactly as CARE does, so soft-deleted roles never block. The check
   and CARE's sync run in one transaction, with the doomed roles locked. Start-up first runs
   `--check` for every hospital, so one hospital's problem stops the run before any hospital
   changes. Otherwise CARE's sync runs unchanged. The same protection covers CARE's own roles and
   permissions across upstream syncs.
5. **CARE's `PATIENT_GLOBAL_EDIT_ACCESS_ENABLED` must stay off** (`settings/secure.py` refuses
   it). With it on, any signed-in user can open any patient by id, and anyone holding
   `can_write_patient` anywhere can edit any patient.
6. **The eight roles and their access come in P1-T11b**, after Q-K21. The proposed default is a
   narrow demographics-and-billing override for the non-clinical roles.

## Consequences

- **Rolling back.** Once hospitals assign our roles, rolling back to an image **from P1-T11a or
  later** stops at the sync with a clear message rather than deleting memberships. The fix is to
  roll forward, or to reassign the role's holders first.
- **Never roll back past P1-T11a.** An image older than P1-T11a has no guard, and CARE's sync in
  it would delete the memberships (deploy/staging/README.md says so). The guard cannot reach back
  into older images; this is a known limit.
- **A change to a grant reaches every hospital at the next start.** Grant changes are owner
  decisions, recorded with a date, and RolePermission changes land in each hospital's audit
  trail.
- **Role count and CARE's demo loader.** CARE's demo loader reads one page of roles, so with
  more system roles CARE's own Doctor, Nurse, Staff and Administrator fall off that page. The
  seed's roles call must ask for every role (a plugin-side patch, P1-T11b).
