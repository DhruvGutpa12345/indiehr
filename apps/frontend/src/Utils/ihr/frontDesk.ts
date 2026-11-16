// IndieHR P1-T11c2 (docs/reviews/p1-t11/design-c.md §3.1, §3.3, §5.1, §13.8): the rules of the
// front-desk and billing-desk screens. Pure (no React, no "@/" imports), so
// `node --import tsx --test` runs frontDesk.test.ts; the small hooks in CARE's own files
// (marked "IndieHR P1-T11c") only call these functions, the components hold no rule of their own.
//
// The views (access.ts `ihrPatientView`):
// - "care": CARE's own screens, unchanged (CARE's roles, and MRD under a live grant).
// - "demographics": our reduced screens (Receptionist, Cashier, TPA Desk, MRD without a grant).
// - "none" and "pending": nothing clinical. Every rule answers "show nothing, ask nothing" for
//   them, so no CARE clinical request is made while the backend's answer (`my-access`) or the
//   patient is still loading, or when it failed with nothing cached (fails closed).
//
// A field the backend's projection (ihr_access/fields/*.txt) removes is never shown as empty:
// its row is removed (design-c.md §3.3).
import type { IhrAccess, IhrPatientView } from "./access";
import { DEMOGRAPHICS_TABS } from "./routeKeys";

// ---------------------------------------------------------------------------------------------
// Patient page tabs (PatientProfile.tsx)

/** The facility slug (or our slug) each tab of the demographics view needs (design §3.1). */
export function demographicsTabShown(
  route: string,
  access: IhrAccess,
): boolean {
  switch (route) {
    case "demography":
      return access.demographics;
    case "appointments":
      return access.has("can_list_booking");
    case "encounters":
      return access.encounterStatus;
    case "accounts":
      return access.has("can_read_account");
    default:
      return false;
  }
}

/**
 * The patient page's tabs. "care": CARE's own list (its patient-permission rules). "demographics":
 * exactly Demography, Appointments, Encounters, Accounts, each only with the slug it needs, in
 * CARE's order. "none" and "pending": no tab, so no tab component runs.
 */
export function ihrPatientTabs<T extends { route: string }>(
  careTabs: T[],
  allTabs: T[],
  view: IhrPatientView,
  access: IhrAccess,
): T[] {
  if (view === "care") return careTabs;
  if (view !== "demographics") return [];
  const allowed: readonly string[] = DEMOGRAPHICS_TABS;
  return allTabs.filter(
    (tab) =>
      allowed.includes(tab.route) && demographicsTabShown(tab.route, access),
  );
}

/**
 * The appointments tab is built by CARE with no facility (F9), but a restricted user must name
 * the facility (CARE's booking check answers 403 otherwise): the demographics view sends it.
 * Anything else is CARE's own value (undefined).
 */
export function ihrAppointmentsFacilityId(
  view: IhrPatientView,
  facilityId: string | undefined,
): string | undefined {
  return view === "demographics" ? facilityId : undefined;
}

// ---------------------------------------------------------------------------------------------
// Demography tab (Demography.tsx)

/** Sections removed in the demographics view: plugin data and tags (design §3.3). */
export const DEMOGRAPHY_HIDDEN_SECTIONS: readonly string[] = [
  "additional-details",
  "tags",
];
/** The key of the plugin slot `PatientDetailsTabDemographyGeneralInfo` in the general section. */
export const DEMOGRAPHY_PLUGIN_SLOT_KEY =
  "patient_details_tab__demography__general_info";

export interface DemographySection {
  id: string;
  allowEdit?: boolean;
  hidden?: boolean;
  details: unknown[];
}

function isBloodGroupRow(detail: unknown, bloodGroupLabel: string): boolean {
  return (
    typeof detail === "object" &&
    detail !== null &&
    "label" in detail &&
    (detail as { label: unknown }).label === bloodGroupLabel
  );
}

function isPluginSlot(detail: unknown): boolean {
  return (
    typeof detail === "object" &&
    detail !== null &&
    "key" in detail &&
    (detail as { key: unknown }).key === DEMOGRAPHY_PLUGIN_SLOT_KEY
  );
}

/**
 * The Demography tab's sections. "care": unchanged. "demographics": without the plugin and tag
 * sections, the plugin slot and the blood group row of the general section (the guard resets
 * them, so they would read as empty); edit buttons only on the general section and only with
 * `ihr_edit_patient_demographics` (CARE's own edit rule would never grant it to them).
 * "none" and "pending": no section.
 */
export function ihrDemographySections<T extends DemographySection>(
  sections: T[],
  view: IhrPatientView,
  access: IhrAccess,
  hasFacility: boolean,
  bloodGroupLabel: string,
): T[] {
  if (view === "care") return sections;
  if (view !== "demographics") return [];
  return sections
    .filter((section) => !DEMOGRAPHY_HIDDEN_SECTIONS.includes(section.id))
    .map((section) => {
      if (section.id !== "general-info")
        return { ...section, allowEdit: false };
      return {
        ...section,
        allowEdit: hasFacility && access.editDemographics,
        details: section.details.filter(
          (detail) =>
            !isBloodGroupRow(detail, bloodGroupLabel) && !isPluginSlot(detail),
        ),
      };
    });
}

/**
 * The edit form (update only) drops the blood group field, the plugin extension fields and the
 * "Mark as deceased" block unless the view is CARE's: the backend ignores the first two from this
 * role and answers 400 when the date of death changes (patient_edit.txt `deceased_datetime
 * visible`). Create keeps them (the registration records blood group, a known limit of P1-T11b2).
 */
export function ihrHidesClinicalFormFields(
  view: IhrPatientView,
  isUpdate: boolean,
): boolean {
  return isUpdate && view !== "care";
}

// ---------------------------------------------------------------------------------------------
// Encounters: history tab, cards, patient home, create form

/** What the encounter history tab does: list (yes), leave with a message (no), wait (pending). */
export type ListAnswer = "yes" | "no" | "pending";

/**
 * Encounter history (EncounterHistory.tsx): CARE's `can_list_patients` in "care"; the encounter
 * status slug in "demographics"; nothing, and no redirect, while pending.
 */
export function ihrEncounterHistoryRule(
  careAllowed: boolean,
  view: IhrPatientView,
  access: IhrAccess,
): ListAnswer {
  if (view === "pending") return "pending";
  if (view === "care") return careAllowed ? "yes" : "no";
  if (view === "demographics") return access.encounterStatus ? "yes" : "no";
  return "no";
}

/**
 * "View encounter" on a card (EncounterCard.tsx) is enabled by CARE's own rule, or in the
 * restricted mode by the encounter status slug (the link lands on the status page).
 */
export function ihrCanOpenEncounter(
  careAllowed: boolean,
  access: IhrAccess,
): boolean {
  if (access.mode === "restricted")
    return careAllowed || access.encounterStatus;
  return careAllowed;
}

/**
 * Patient home's encounter tab (PatientHome.tsx) lists with CARE's `can_list_encounter` (the
 * facility's list), or with the status slug in the demographics view; nothing while pending.
 */
export function ihrCanListEncounters(
  careAllowed: boolean,
  view: IhrPatientView,
  access: IhrAccess,
): boolean {
  if (view === "care") return careAllowed;
  if (view === "demographics") return access.encounterStatus;
  return false;
}

/** Patient home's "View accounts" (ungated in CARE): `can_read_account` for the demographics view. */
export function ihrShowsViewAccounts(
  view: IhrPatientView,
  access: IhrAccess,
): boolean {
  if (view === "care") return true;
  if (view === "demographics") return access.has("can_read_account");
  return false;
}

/** Patient home hides the tag card unless the view is CARE's (tags are projected away). */
export function ihrHidesPatientTags(view: IhrPatientView): boolean {
  return view !== "care";
}

/**
 * The restart-encounter menu on a completed visit needs `can_write_encounter`, which no
 * restricted role holds: only CARE's mode offers it (and never while pending).
 */
export function ihrShowsEncounterActions(access: IhrAccess): boolean {
  return access.mode === "care";
}

/** The create form's tag picker: tags are projected away for every mode but CARE's. */
export function ihrShowsEncounterTags(access: IhrAccess): boolean {
  return access.mode === "care";
}

// ---------------------------------------------------------------------------------------------
// Encounter list (EncounterList.tsx)

/**
 * The list's filters the backend refuses (fields/encounter_filters.txt `refuse`): the query
 * parameters, and the filter keys of the multi-filter that would send them.
 */
export const REFUSED_ENCOUNTER_PARAMS: readonly string[] = [
  "location",
  "organization",
  "care_team_user",
  "tags",
  "tags_behavior",
];
export const REFUSED_ENCOUNTER_FILTER_KEYS: readonly string[] = [
  "organization",
  "care_team",
  "tags",
];

/** The list may ask the API (never while pending: CARE's own roles wait one my-access answer). */
export function ihrEncounterListEnabled(access: IhrAccess): boolean {
  return access.mode !== "pending";
}

/**
 * The filter chips (department, care team, tags) are not offered outside CARE's mode: they would
 * find patients by department, doctor or tag.
 */
export function ihrEncounterFilters<T extends { key: string }>(
  filters: T[],
  access: IhrAccess,
): T[] {
  if (access.mode === "care") return filters;
  return filters.filter(
    (filter) => !REFUSED_ENCOUNTER_FILTER_KEYS.includes(filter.key),
  );
}

/**
 * The address's query parameters as the list reads them: outside CARE's mode the refused
 * filters are dropped even when they come from the address or from a filter saved in the
 * browser's session (F18), so none is sent (the backend answers 400 to them).
 */
export function ihrEncounterParams<T extends Record<string, unknown>>(
  params: T,
  access: IhrAccess,
): T {
  if (access.mode === "care") return params;
  const kept: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(params)) {
    if (!REFUSED_ENCOUNTER_PARAMS.includes(key) && key !== "care_team") {
      kept[key] = value;
    }
  }
  return kept as T;
}

// ---------------------------------------------------------------------------------------------
// Account page (AccountShow.tsx)

export interface TabLike {
  visible?: boolean;
}

/** The slug each account tab needs in the restricted mode; `reports` is never shown (Q-K41). */
const ACCOUNT_TAB_SLUGS: Readonly<Record<string, string | null>> = {
  invoices: "can_read_invoice",
  charge_items: "can_read_charge_item",
  payments: "can_read_payment_reconciliation",
  reports: null, // account reports are refused to restricted users (Q-K41)
  bed_charge_items: "can_read_charge_item", // and `billingBed` (Q-K37a B)
};

/**
 * The account page's tabs, with `visible` set (NavTabs hides a tab with visible false; the key
 * stays, since NavTabs reads `tabs[currentTab]`). "care": unchanged. Restricted: a tab only with
 * its read slug, Reports never, Bed charges also only where the role sees the bed. Pending: none.
 */
export function ihrAccountTabs<T extends Record<string, object>>(
  tabs: T,
  access: IhrAccess,
): T {
  if (access.mode === "care") return tabs;
  const out: Record<string, TabLike> = {};
  for (const [key, tab] of Object.entries(tabs) as [string, TabLike][]) {
    out[key] = {
      ...tab,
      visible: accountTabShown(key, access) && tab.visible !== false,
    };
  }
  return out as T;
}

export function accountTabShown(key: string, access: IhrAccess): boolean {
  if (access.mode !== "restricted") return false;
  const slug = ACCOUNT_TAB_SLUGS[key];
  if (slug === undefined) return false; // an account tab upstream adds: fails closed
  if (slug === null) return false;
  if (key === "bed_charge_items" && !access.billingBed) return false;
  return access.has(slug);
}

/** Whether any tab would show: NavTabs cannot render (it navigates to a first tab) with none. */
export function ihrAnyTabVisible(tabs: Record<string, object>): boolean {
  return (Object.values(tabs) as TabLike[]).some(
    (tab) => tab.visible !== false,
  );
}

/**
 * "Add charge items" (and "Quick add", and the room-rent button on a bed stay) is offered when
 * CARE offers it and, outside CARE's mode, only with `can_create_charge_item`: the Cashier adds
 * room rent, the TPA Desk (read only) never sees a button the backend would refuse.
 */
export function ihrCanAddChargeItems(
  careAllowed: boolean,
  access: IhrAccess,
): boolean {
  if (access.mode === "care") return careAllowed;
  if (access.mode === "pending") return false;
  return careAllowed && access.has("can_create_charge_item");
}

/** Which of the account page's write buttons are offered (each one a request the backend decides). */
export interface AccountActions {
  /** "Create invoice" (`can_write_invoice`) */
  invoice: boolean;
  /** "Add credit payment", "Record credit note", "Transfer payment" (`can_write_payment_reconciliation`) */
  payment: boolean;
  /** "Settle / close" and "Manage tags" (`can_update_account`) */
  account: boolean;
  /** a charge item's row menu (edit, change account, mark as) and the bulk select (`can_update_charge_item`) */
  chargeItems: boolean;
}

/**
 * A button is shown when the request behind it can succeed (design §3.3): outside CARE's mode the
 * account page's write buttons follow the role's own slugs (Receptionist: none; TPA Desk: close
 * and tags only; Cashier: all). CARE's mode is CARE's own page (it never gated these); pending
 * offers none.
 */
export function ihrAccountActions(access: IhrAccess): AccountActions {
  if (access.mode === "care") {
    return { invoice: true, payment: true, account: true, chargeItems: true };
  }
  if (access.mode === "pending") {
    return {
      invoice: false,
      payment: false,
      account: false,
      chargeItems: false,
    };
  }
  return {
    invoice: access.has("can_write_invoice"),
    payment: access.has("can_write_payment_reconciliation"),
    account: access.has("can_update_account"),
    chargeItems: access.has("can_update_charge_item"),
  };
}

// ---------------------------------------------------------------------------------------------
// The visit status page (IhrEncounterStatus.tsx)

/**
 * What the status page shows beyond the visit's own fields: the current bed, the bed stays
 * and the account link, only for the Cashier and the TPA Desk (the backend's `bills_here`,
 * Q-K37a B); everyone else gets neither (the fields are projected away, never "none").
 */
export function ihrStatusBlocks(access: IhrAccess): {
  bed: boolean;
  bedStays: boolean;
  accountLink: boolean;
} {
  const on = access.mode === "restricted" && access.billingBed;
  return { bed: on, bedStays: on, accountLink: on };
}

/** The account list query that finds a visit's account (accounts filter `encounter`). */
export function ihrAccountOfVisitQuery(encounterId: string): {
  encounter: string;
  limit: number;
} {
  return { encounter: encounterId, limit: 1 };
}
