// IndieHR P1-T11c1 (design-c.md §2.3, §3.1, §13.2, §13.8): which of CARE's route keys get which
// gate, and what each gate shows for a view. Pure (no React, no "@/" imports), so
// `node --import tsx --test` runs its tests (routeKeys.test.ts); IhrRoutes.tsx and IhrGates.tsx
// apply it.
//
// Every `export const …_KEY = "…"` here is checked against CARE's route tables by
// scripts/check-ihr-frontend.py (KEY_FILES there names the file of each).
import type { IhrAccess, IhrPatientView } from "./access";

/** Every encounter page: `/${facility|organization}/:id/patient/:patientId/encounter/:encounterId/:tab` */
export const ENCOUNTER_KEY =
  "/facility/:id/patient/:patientId/encounter/:encounterId/:tab";
/**
 * The encounter's other pages (questionnaires, responses, reports, consents, print pages) start
 * with this prefix; raviger matches them before (or instead of) ENCOUNTER_KEY. One of them is
 * pinned below so a renamed prefix fails the anchor check.
 */
export const ENCOUNTER_PAGE_PREFIX =
  "/facility/:facilityId/patient/:patientId/encounter/:encounterId/";
export const ENCOUNTER_QUESTIONNAIRE_KEY =
  "/facility/:facilityId/patient/:patientId/encounter/:encounterId/questionnaire";
/** The bed board is the tab "locations" of this route (and the one below it for one location) */
export const BED_BOARD_TAB_KEY = "/facility/:facilityId/encounters/:tab";
export const BED_BOARD_LOCATION_KEY =
  "/facility/:facilityId/encounters/locations/:locationId";
/** The facility's encounter lists (the menu's Patients > … Encounters) */
export const ENCOUNTER_LIST_ALL_KEY =
  "/facility/:facilityId/encounters/patients/all";
export const ENCOUNTER_LIST_CLASS_KEY =
  "/facility/:facilityId/encounters/patients/:encounterClass";
/** Patient search, home, create, the patient page and its edit form */
export const PATIENT_SEARCH_KEY = "/facility/:facilityId/patients";
export const PATIENT_HOME_KEY = "/facility/:facilityId/patients/home";
export const PATIENT_CREATE_KEY = "/facility/:facilityId/patient/create";
export const PATIENT_KEY = "/facility/:facilityId/patient/:id";
export const PATIENT_UPDATE_KEY = "/facility/:facilityId/patient/:id/update";
/** An appointment of the patient (ScheduleRoutes.tsx) */
export const APPOINTMENT_KEY =
  "/facility/:facilityId/patient/:patientId/appointments/:appointmentId";
/** CARE builds one key per patient tab: `PATIENT_TAB_PREFIX + tab.route` (PatientRoutes.tsx) */
export const PATIENT_TAB_PREFIX = "/facility/:facilityId/patient/:id/";
/** The tabs a demographics view may open (design §3.1); c2 hides the others' links too */
export const DEMOGRAPHICS_TABS = [
  "demography",
  "appointments",
  "encounters",
  "accounts",
] as const;

const FACILITY_PATIENT_PREFIX = "/facility/:facilityId/patient/";

/** Keys withIhrRoutes reports when CARE no longer defines them. */
export const REQUIRED_KEYS = [
  ENCOUNTER_KEY,
  ENCOUNTER_QUESTIONNAIRE_KEY,
  BED_BOARD_TAB_KEY,
  BED_BOARD_LOCATION_KEY,
  ENCOUNTER_LIST_ALL_KEY,
  ENCOUNTER_LIST_CLASS_KEY,
  PATIENT_SEARCH_KEY,
  PATIENT_HOME_KEY,
  PATIENT_CREATE_KEY,
  PATIENT_KEY,
  PATIENT_UPDATE_KEY,
  APPOINTMENT_KEY,
  ...DEMOGRAPHICS_TABS.map((tab) => PATIENT_TAB_PREFIX + tab),
];

/**
 * The patient keys a demographics view (Receptionist, Cashier, TPA Desk, MRD without a grant)
 * may open. Every other facility patient key (history, reports, prescriptions, questionnaires,
 * other tabs, and any key upstream adds) shows IhrNotForRole to them: fails closed.
 */
export const DEMOGRAPHICS_KEYS: ReadonlySet<string> = new Set([
  PATIENT_SEARCH_KEY,
  PATIENT_HOME_KEY,
  PATIENT_CREATE_KEY,
  PATIENT_KEY,
  PATIENT_UPDATE_KEY,
  APPOINTMENT_KEY,
  ...DEMOGRAPHICS_TABS.map((tab) => PATIENT_TAB_PREFIX + tab),
]);

export type RouteGate =
  | "encounter" // the status page for the front desk (IhrEncounterGate)
  | "encounterPage" // clinical encounter sub-pages: CARE's only for "care"
  | "bedBoardTab"
  | "bedBoardLocation"
  | "encounterList"
  | "patient" // a demographics view may open it
  | "patientClinical" // a demographics view may not
  | "none";

/** The gate a CARE route key gets (by key, so a new upstream key under a prefix is gated). */
export function routeGate(key: string): RouteGate {
  if (key === ENCOUNTER_KEY) return "encounter";
  if (key.startsWith(ENCOUNTER_PAGE_PREFIX)) return "encounterPage";
  if (key === BED_BOARD_TAB_KEY) return "bedBoardTab";
  if (key === BED_BOARD_LOCATION_KEY) return "bedBoardLocation";
  if (key === ENCOUNTER_LIST_ALL_KEY || key === ENCOUNTER_LIST_CLASS_KEY) {
    return "encounterList";
  }
  if (DEMOGRAPHICS_KEYS.has(key)) return "patient";
  if (key.startsWith(FACILITY_PATIENT_PREFIX)) return "patientClinical";
  return "none";
}

/** What a gate renders: CARE's loader, our refusal, our status page, or CARE's element. */
export type GateOutcome = "loading" | "notForRole" | "status" | "care";

/** Patient pages and the encounter's sub-pages, by the user's view of that patient. */
export function patientGateOutcome(
  view: IhrPatientView,
  demographicsAllowed: boolean,
): GateOutcome {
  if (view === "pending") return "loading";
  if (view === "none") return "notForRole";
  if (view === "demographics")
    return demographicsAllowed ? "care" : "notForRole";
  return "care";
}

/** The encounter page: the status view for a demographics view. */
export function encounterGateOutcome(view: IhrPatientView): GateOutcome {
  if (view === "demographics") return "status";
  return patientGateOutcome(view, false);
}

/** The bed board shows who occupies each bed: never for restricted roles (Q-K37). */
export function bedBoardOutcome(access: IhrAccess): GateOutcome {
  if (access.mode === "pending") return "loading";
  return access.mode === "restricted" ? "notForRole" : "care";
}

/** Encounter lists: restricted roles only with ihr_view_encounter_status (design §3.2). */
export function encounterListOutcome(access: IhrAccess): GateOutcome {
  if (access.mode === "pending") return "loading";
  if (access.mode === "restricted" && !access.encounterStatus) {
    return "notForRole";
  }
  return "care";
}
