// IndieHR P1-T11c1 (docs/reviews/p1-t11/design-c.md §2.1, §13.2): who may see which of our items
// and which of CARE's menus. Pure (no React, no "@/" imports), so `node --import tsx --test`
// runs its tests.
//
// The MODE is the backend's decision (GET /api/ihr/my-access/, its `restricted` field is
// `ihr_access.state.restricted`), never derived from permission slugs here and never from
// `hasPermission` (a superuser would pass every check). The slug sets (ihrSlugs.json, a copy of
// plugins/ihr_platform/ihr_roles/frontend_slugs.json, byte-compared by
// scripts/check-ihr-frontend.py) only decide which of our items to show inside a mode.

export type IhrMode = "pending" | "care" | "restricted";

/** The part of ihrSlugs.json the rules read. */
export interface IhrSlugSets {
  ours: readonly string[];
  clinical: readonly string[];
  patientOpening: readonly string[];
  grants: Readonly<Record<string, readonly string[]>>;
}

/** GET /api/ihr/my-access/?facility=<id> */
export interface MyAccess {
  restricted: boolean;
  bills_here: boolean;
  /**
   * An admin role on an active top-level organisation, the audit trail's own gate
   * (`IsHospitalAdmin`, P1-T24a). False for a superuser. Shows our admin pages in care mode.
   */
  hospital_admin: boolean;
}

export interface IhrAccessInput {
  /** `facility.permissions`; undefined while the facility is loading */
  facilityPerms: readonly string[] | undefined;
  /**
   * undefined while loading; null when the request failed with no answer cached (then
   * "pending": the UI fails closed and the query retries). See myAccessFrom.
   */
  myAccess: MyAccess | null | undefined;
  isSuperuser: boolean;
}

export const OUR_SLUGS = {
  demographics: "ihr_view_patient_demographics",
  editDemographics: "ihr_edit_patient_demographics",
  encounterStatus: "ihr_view_encounter_status",
  records: "ihr_request_records_access",
  totals: "ihr_view_hospital_totals",
} as const;

export interface IhrAccess {
  mode: IhrMode;
  /** ihr_view_patient_demographics (Receptionist, Cashier, TPA Desk, MRD) */
  demographics: boolean;
  /** ihr_edit_patient_demographics (Receptionist) */
  editDemographics: boolean;
  /** ihr_view_encounter_status (Receptionist, Cashier, TPA Desk, MRD) */
  encounterStatus: boolean;
  /** ihr_request_records_access (MRD) */
  records: boolean;
  /** ihr_view_hospital_totals (Owner) */
  totals: boolean;
  /** the visit's bed is shown here (Cashier, TPA Desk; the backend's `bills_here`, Q-K37a B) */
  billingBed: boolean;
  /** hospital-wide admin (the backend's `hospital_admin`): the audit trail page (P1-T24a) */
  hospitalAdmin: boolean;
  /** the facility's permission list holds this slug (false while pending, for a superuser) */
  has: (slug: string) => boolean;
  /** a patient's permission list holds a clinical or patient-opening slug (F4: MRD under a grant) */
  opensPatient: (patientPerms: readonly string[]) => boolean;
}

export function ihrAccess(input: IhrAccessInput, sets: IhrSlugSets): IhrAccess {
  const opening = new Set<string>([...sets.clinical, ...sets.patientOpening]);
  const opensPatient = (patientPerms: readonly string[]) =>
    patientPerms.some((slug) => opening.has(slug));

  const none = {
    demographics: false,
    editDemographics: false,
    encounterStatus: false,
    records: false,
    totals: false,
    billingBed: false,
    hospitalAdmin: false,
    has: () => false,
    opensPatient,
  };

  // A superuser is never in one of our modes and never sees our items: `hasPermission` answers
  // true to everything for them, and our endpoints refuse them.
  if (input.isSuperuser) return { mode: "care", ...none };

  const { facilityPerms, myAccess } = input;
  // No answer from the backend (loading, or failed with nothing cached): never guess the mode
  // from slugs (Q-K49 A'), and never fall back to CARE's menus (fail closed).
  if (
    facilityPerms === undefined ||
    myAccess === undefined ||
    myAccess === null
  ) {
    return { mode: "pending", ...none };
  }

  const held = new Set<string>(facilityPerms);
  const has = (slug: string) => held.has(slug);
  const restricted = myAccess.restricted;
  return {
    mode: restricted ? "restricted" : "care",
    demographics: has(OUR_SLUGS.demographics),
    editDemographics: has(OUR_SLUGS.editDemographics),
    encounterStatus: has(OUR_SLUGS.encounterStatus),
    records: has(OUR_SLUGS.records),
    totals: has(OUR_SLUGS.totals),
    billingBed: restricted && myAccess.bills_here,
    // restricted roles hold no admin role; a restricted answer never opens the admin pages
    hospitalAdmin: !restricted && myAccess.hospital_admin === true,
    has,
    opensPatient,
  };
}

/**
 * The my-access answer to use, from the query's state. A cached answer wins over a failed
 * refetch (TanStack keeps `data` and sets `isError`): a transient 5xx or network error never
 * flips a restricted user into CARE's menus. With nothing cached, a failure is null (pending).
 */
export function myAccessFrom(query: {
  data: MyAccess | undefined;
  isError: boolean;
}): MyAccess | null | undefined {
  if (query.data !== undefined) return query.data;
  return query.isError ? null : undefined;
}

export type IhrPatientView = "pending" | "care" | "demographics" | "none";

/**
 * What a user may be shown of one patient or visit.
 * - "care": CARE's own screens (CARE's roles, and MRD under a live grant: the patient's list
 *   then carries the hidden role's clinical slugs, F4).
 * - "demographics": our reduced screens (Receptionist, Cashier, TPA Desk, MRD without a grant).
 * - "none": no patient screens (Owner, Lab Technician, Store Keeper).
 * `patientPerms` is undefined while the patient is loading; a role without the demographics
 * slug never needs it, so it is answered at once.
 */
export function ihrPatientView(
  access: IhrAccess,
  patientPerms: readonly string[] | undefined,
): IhrPatientView {
  if (access.mode === "pending") return "pending";
  if (access.mode === "care") return "care";
  if (patientPerms === undefined) {
    return access.demographics ? "pending" : "none";
  }
  if (access.opensPatient(patientPerms)) return "care";
  return access.demographics ? "demographics" : "none";
}
