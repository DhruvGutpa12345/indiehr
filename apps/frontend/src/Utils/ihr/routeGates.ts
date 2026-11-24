// IndieHR P2-T0c (docs/reviews/p2-t0c/design.md §3, §4.1, §4.4): which TanStack route id gets
// which gate, what each gate shows, which ids the grant watcher covers, and the reviewed lists
// (clinical ids left open, public routes). Replaces routeKeys.ts (the old router's keys).
//
// Pure: no React, no router import, no "@/" alias (relative imports only), so
// `node --import tsx --test` runs routeGates.test.ts, which reads src/routeTree.gen.ts as text.
// IhrStaffPage.tsx and IhrGates.tsx (IhrRouteGate) apply it; ihrRouteTree.tsx uses mergeChildren.
//
// Every `export const …_ID = "…"` here must be a key of FileRoutesById in src/routeTree.gen.ts
// (routeGates.test.ts; scripts/check-ihr-frontend.py checks the same for fast feedback).
import type { IhrAccess, IhrPatientView } from "./access";

// ---------------------------------------------------------------------------------------------
// Route ids (keys of FileRoutesById). Ids keep the pathless layouts; index routes end in "/".

/** The encounter page (its tabs): the front desk sees our status page instead. */
export const ENCOUNTER_ID =
  "/_app/facility/$facilityId/patient/$patientId/encounter/$encounterId/$tab";
/** Its organization counterpart: gated as an encounter sub-page (Q-K170 B, §3.4 row 3). */
export const ORG_ENCOUNTER_ID =
  "/_app/organization/$organizationId/patient/$patientId/encounter/$encounterId/$tab";
/** One encounter sub-page named so a renamed prefix fails the tests. */
export const ENCOUNTER_QUESTIONNAIRE_ID =
  "/_app/facility/$facilityId/patient/$patientId/encounter/$encounterId/questionnaire/";
/** The bed board is the tab "locations" of this route (and the one below it for one location) */
export const BED_BOARD_TAB_ID = "/_app/facility/$facilityId/encounters/$tab";
export const BED_BOARD_LOCATION_ID =
  "/_app/facility/$facilityId/encounters/locations/$locationId";
/** Every id that renders CARE's bed board (who occupies each bed): the board of one location,
 * CARE 3.1's location /beds page and the facility's bed availability (P2-T0c review F1) */
export const BED_BOARD_LOCATION_IDS: ReadonlySet<string> = new Set([
  BED_BOARD_LOCATION_ID,
  "/_app/facility/$facilityId/locations/$locationId/beds",
  "/_app/facility/$facilityId/bed-availability",
]);
/** The facility's encounter lists (the menu's Encounters item opens them, CARE 3.1) */
export const ENCOUNTER_LIST_ALL_ID =
  "/_app/facility/$facilityId/encounters/patients/all";
export const ENCOUNTER_LIST_CLASS_ID =
  "/_app/facility/$facilityId/encounters/patients/$encounterClass";
/** Patient search, home, create, the patient page and its edit form */
export const PATIENT_SEARCH_ID = "/_app/facility/$facilityId/patients/";
export const PATIENT_HOME_ID = "/_app/facility/$facilityId/patients/home";
export const PATIENT_CREATE_ID = "/_app/facility/$facilityId/patient/create";
export const PATIENT_UPDATE_ID =
  "/_app/facility/$facilityId/patient/$patientId/update";
/** The patient page: one route, its tab is an optional param. */
export const PATIENT_TAB_ID =
  "/_app/facility/$facilityId/patient/$patientId/{-$tab}";
/** An appointment of the patient */
export const APPOINTMENT_ID =
  "/_app/facility/$facilityId/patient/$patientId/appointments/$appointmentId/";

/** The tabs a demographics view may open (design-c §3.1); c2 hides the others' links too */
export const DEMOGRAPHICS_TABS = [
  "demography",
  "appointments",
  "encounters",
  "accounts",
] as const;

/** The encounter lists, with the redirect-only routes that lead to them. */
export const ENCOUNTER_LIST_IDS: ReadonlySet<string> = new Set([
  ENCOUNTER_LIST_ALL_ID,
  ENCOUNTER_LIST_CLASS_ID,
  "/_app/facility/$facilityId/encounters",
  "/_app/facility/$facilityId/encounters/",
  "/_app/facility/$facilityId/encounters/patients/",
]);

/**
 * The patient ids a demographics view (Receptionist, Cashier, TPA Desk, MRD without a grant)
 * may open. Every other id under the patient prefix (history, reports, prescriptions,
 * questionnaires, print pages, and any id upstream adds) shows IhrNotForRole to them.
 */
export const DEMOGRAPHICS_IDS: ReadonlySet<string> = new Set([
  PATIENT_SEARCH_ID,
  PATIENT_HOME_ID,
  PATIENT_CREATE_ID,
  PATIENT_UPDATE_ID,
  APPOINTMENT_ID,
]);

// Any segment where upstream has a param: an organization's literal `organizationId` segment
// and plugin ids with other param names match too (design §3.3).
const FAC = String.raw`^/_app/facility/[^/]+`;
const ORG = String.raw`^/_app/organization/[^/]+`;
export const PATIENT_PREFIX_RE = new RegExp(`(${FAC}|${ORG})/patient/`);
export const ENCOUNTER_PREFIX_RE = new RegExp(
  `(${FAC}|${ORG})/patient/[^/]+/encounter/[^/]+/`,
);

/**
 * Open ids that show patient or visit data (Q-K170 B): the backend refuses or projects them for
 * restricted roles and the menus hide them; a role-by-role review is backlog. The list must be
 * exactly the open ids matching CLINICAL_ID_RE (routeGates.test.ts), so a new such id fails CI
 * until it is gated or reviewed here.
 */
export const OPEN_CLINICAL_IDS: ReadonlySet<string> = new Set([
  "/patient/$id/{-$tab}",
  "/patient/home",
  "/referrals/$referralId/",
  "/referrals/$referralId/respond",
  "/_app/patient/$patientId/history/$tab",
  "/_app/patient/$patientId/history/questionnaire/$questionnaireId/responses/print",
  "/_app/patient/$patientId/history/questionnaire_response/$questionnaireResponseId/print",
  "/_app/patient/$patientId/questionnaire/",
  "/_app/patient/$patientId/questionnaire/$questionnaireId/",
  "/_app/patient/$patientId/questionnaire/$questionnaireId/responses/print",
  "/_app/patient/$patientId/questionnaire_response/$questionnaireResponseId/print",
  "/_app/facility/$facilityId/service_requests/$serviceRequestId",
  "/_app/facility/$facilityId/locations/$locationId/service_requests/",
  "/_app/facility/$facilityId/locations/$locationId/service_requests/$serviceRequestId",
  "/_app/facility/$facilityId/medication_dispense/redirect/$medicationDispenseId",
  "/_app/facility/$facilityId/locations/$locationId/medication_dispense/",
  "/_app/facility/$facilityId/locations/$locationId/medication_dispense/order/$dispenseOrderId/",
  "/_app/facility/$facilityId/locations/$locationId/medication_dispense/order/$dispenseOrderId/completed",
  "/_app/facility/$facilityId/locations/$locationId/medication_dispense/order/$dispenseOrderId/print",
  "/_app/facility/$facilityId/locations/$locationId/medication_requests/patient/$patientId/prescriptions/$prescriptionIds/",
  "/_app/facility/$facilityId/locations/$locationId/medication_requests/patient/$patientId/prescriptions/$prescriptionIds/print",
  "/_app/facility/$facilityId/locations/$locationId/medication_requests/patient/$patientId/bill/prescriptions/$prescriptionIds",
  "/_app/facility/$facilityId/locations/$locationId/medication_requests/patient/$patientId/bill/dispense/$encounterId",
  "/_app/facility/$facilityId/queue/$queueId/token/$tokenId",
]);

/** An id that names patient or visit data (design §3.7.4). */
export const CLINICAL_ID_RE =
  /\$patientId|\$encounterId|\/patient\/|service_request|medication_dispense|\/token\/|\/referrals\/|\/beds\b|bed-availability|bed-board/;

/** CARE's auth pages that redirect to /login (ihrRouteTree.tsx, design §4.1). */
export const IHR_AUTH_REDIRECT_IDS = [
  "/_public/2fa",
  "/_public/forgot-password",
  "/_public/password_reset/$token",
] as const;

/**
 * Every /_public id, reviewed (design §4.4). A new one fails routeGates.test.ts until listed:
 * an auth flow (password, 2FA, invite, set-password) joins IHR_AUTH_REDIRECT_IDS first.
 */
export const PUBLIC_ROUTES: Readonly<Record<string, string>> = {
  "/_public": "layout: publicOnly guard only, no component",
  "/_public/login":
    "our patched Login.tsx: staff panel = IhrStaffSignIn, no password form",
  "/_public/2fa": "ToLogin (redirect route, ihrRouteTree.tsx)",
  "/_public/forgot-password": "ToLogin (redirect route, ihrRouteTree.tsx)",
  "/_public/password_reset/$token":
    "ToLogin (redirect route, ihrRouteTree.tsx)",
  "/_public/invalid-reset":
    "harmless: static ErrorPages/InvalidReset, no form (its link goes to /forgot-password, which redirects)",
  "/_public/facilities":
    "harmless: public facility finder; backend trims it (public_routes.py, P1-T20a)",
  "/_public/facility/$id":
    "harmless: public facility page; same trimmed backend",
  "/_public/facility/$facilityId/appointments/$staffId/otp/$page":
    "patient portal OTP sign-in, not a staff credential",
};

/** Our own pages (code routes under /_app, ihrRouteTree.tsx). They gate themselves. */
export const IHR_PAGE_PATHS = [
  "/facility/$facilityId/ihr/audit",
  "/facility/$facilityId/ihr/packs",
  "/facility/$facilityId/ihr/places",
  "/facility/$facilityId/ihr/records",
  "/facility/$facilityId/ihr/totals",
] as const;
export const IHR_PAGE_IDS: readonly string[] = IHR_PAGE_PATHS.map(
  (path) => `/_app${path}`,
);

// ---------------------------------------------------------------------------------------------
// Classification

export type RouteGate =
  | "encounter" // the status page for the front desk
  | "encounterPage" // clinical encounter sub-pages: CARE's only for "care"
  | "bedBoardTab"
  | "bedBoardLocation"
  | "encounterList"
  | "patient" // a demographics view may open it
  | "patientTab" // the patient page: by tab
  | "patientClinical" // a demographics view may not
  | "open";

/** The gate of a route id; first rule wins (design §3.3). Unknown ids: prefix rule or open. */
export function routeGateOf(id: string): RouteGate {
  if (id === ENCOUNTER_ID) return "encounter";
  if (ENCOUNTER_PREFIX_RE.test(id)) return "encounterPage";
  if (id === BED_BOARD_TAB_ID) return "bedBoardTab";
  if (BED_BOARD_LOCATION_IDS.has(id)) return "bedBoardLocation";
  if (ENCOUNTER_LIST_IDS.has(id)) return "encounterList";
  if (id === PATIENT_TAB_ID) return "patientTab";
  if (DEMOGRAPHICS_IDS.has(id)) return "patient";
  if (PATIENT_PREFIX_RE.test(id)) return "patientClinical";
  return "open";
}

const PATIENT_FAMILY: ReadonlySet<RouteGate> = new Set([
  "patient",
  "patientTab",
  "patientClinical",
  "encounter",
  "encounterPage",
]);

/** The gates that show one patient or one visit. */
export function isPatientFamily(gate: RouteGate): boolean {
  return PATIENT_FAMILY.has(gate);
}

/** The ids the one grant watcher covers: every facility page and the facility-less charts. */
export function watcherScope(id: string): "facility" | "facilityLess" | null {
  if (/^\/_app\/facility\/[^/]+\//.test(id)) return "facility";
  if (
    /^\/_app\/patient\//.test(id) ||
    /^\/patient\/\$id\//.test(id) ||
    /^\/_app\/organization\/[^/]+\/patient\//.test(id)
  ) {
    return "facilityLess";
  }
  return null;
}

/** One problem per open id that names patient or visit data and is not reviewed (§3.7.4). */
export function clinicalProblems(
  ids: Iterable<string>,
  gateOf: (id: string) => RouteGate,
  openClinical: ReadonlySet<string>,
): string[] {
  const problems: string[] = [];
  for (const id of ids) {
    if (!CLINICAL_ID_RE.test(id) || gateOf(id) !== "open") continue;
    if (!openClinical.has(id)) {
      problems.push(
        `${id}: shows patient or visit data and is open; gate it or review it in OPEN_CLINICAL_IDS`,
      );
    }
  }
  return problems;
}

/** Every route id of src/routeTree.gen.ts (the keys of `export interface FileRoutesById`),
 * `__root__` included. */
export function treeIds(text: string): string[] {
  const start = text.indexOf("export interface FileRoutesById {");
  if (start === -1) return [];
  // the ids themselves contain "}" (`{-$tab}`): the block ends at a "}" on its own line
  const end = text.indexOf("\n}", start);
  const block = text.slice(start, end === -1 ? undefined : end);
  // `__root__` is the one unquoted key
  return [...block.matchAll(/^\s+(?:"([^"]+)"|(__root__)): typeof /gm)].map(
    (m) => m[1] ?? m[2],
  );
}

// ---------------------------------------------------------------------------------------------
// The leaf: the deepest committed match that did not fail (design §3.2)

/** The part of a TanStack route match the gate reads. */
export interface MatchLike {
  routeId: string;
  params?: unknown;
  pathname?: string;
  status?: string;
}

export interface Leaf {
  routeId: string;
  params: Readonly<Record<string, string | undefined>>;
  facilityId: string | undefined;
  /** a deeper match failed (not found, error): this is the deepest one that did not */
  failed: boolean;
}

/**
 * One string for the leaf, so the `useMatches` selector returns a primitive that is stable
 * between router store updates (as AreaChrome.tsx does). The cut is layoutFlags.ts's: matches
 * from the first one whose status is "notFound" or "error" are dropped.
 */
export function leafSignature(matches: readonly MatchLike[]): string {
  const failedAt = matches.findIndex(
    (m) => m.status === "notFound" || m.status === "error",
  );
  const ok = failedAt === -1 ? matches : matches.slice(0, failedAt);
  const d = ok.length > 0 ? ok[ok.length - 1] : undefined;
  return JSON.stringify([
    d?.routeId ?? "__root__",
    d?.params ?? {},
    d?.pathname ?? "/",
    ok.length < matches.length,
  ]);
}

/** CARE's rule (useCurrentFacility.tsx extractFacilityId): segment 2 after `/facility/`. */
export function facilityFromPath(pathname: string): string | undefined {
  const segments = pathname.split("/");
  if (segments[1] === "facility" && segments[2]) return segments[2];
  return undefined;
}

export function parseLeaf(signature: string): Leaf {
  const [routeId, rawParams, pathname, failed] = JSON.parse(signature) as [
    string,
    Record<string, unknown>,
    string,
    boolean,
  ];
  const params: Record<string, string | undefined> = {};
  for (const [key, value] of Object.entries(rawParams ?? {})) {
    if (typeof value === "string") params[key] = value;
  }
  return {
    routeId,
    params,
    facilityId: params.facilityId || facilityFromPath(pathname),
    failed,
  };
}

// ---------------------------------------------------------------------------------------------
// Outcomes

/** What a gate renders: CARE's loader, our refusal, our status page, or CARE's page. */
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

/** Encounter lists: restricted roles only with ihr_view_encounter_status (design-c §3.2). */
export function encounterListOutcome(access: IhrAccess): GateOutcome {
  if (access.mode === "pending") return "loading";
  if (access.mode === "restricted" && !access.encounterStatus) {
    return "notForRole";
  }
  return "care";
}

export interface GateInput {
  gate: RouteGate;
  /** the leaf's `tab` param */
  tab: string | undefined;
  /** the facility of the leaf match (never the address bar) */
  leafFacilityId: string | undefined;
  /** the facility the address bar names (useCurrentFacilitySilently), only to compare */
  currentFacilityId: string | undefined;
  /** the leaf's `patientId` param */
  patientId: string | undefined;
  /** useIhrAccess({ facilityId: leafFacilityId }) */
  access: IhrAccess;
  /** useIhrPatientView(patientId, { facilityId: leafFacilityId }) */
  view: IhrPatientView;
  /** my-access without a facility: undefined while unknown */
  restrictedAnywhere: boolean | undefined;
  isSuperuser: boolean;
}

const NEEDS_PATIENT: ReadonlySet<RouteGate> = new Set([
  "patientClinical",
  "encounterPage",
  "encounter",
  "patientTab",
]);

/** What the gate shows (design §3.4); first row wins. */
export function gateOutcome(input: GateInput): GateOutcome {
  const { gate, access, view } = input;
  // 1. the address bar already names another facility: wait for the matches to commit
  if (gate !== "open" && input.leafFacilityId !== input.currentFacilityId) {
    return "loading";
  }
  // 2.
  if (gate === "open") return "care";
  // 3. patient and visit pages outside a facility (organization addresses, Q-K170 B)
  if (isPatientFamily(gate) && !input.leafFacilityId) {
    if (input.isSuperuser) return "care";
    if (input.restrictedAnywhere === undefined) return "loading";
    return input.restrictedAnywhere ? "notForRole" : "care";
  }
  // 4. a gated page with no patient param (a plugin id under the prefix): fail closed
  if (
    NEEDS_PATIENT.has(gate) &&
    !input.patientId &&
    access.mode === "restricted"
  ) {
    return "notForRole";
  }
  switch (gate) {
    case "encounter":
      return encounterGateOutcome(view);
    case "encounterPage":
    case "patientClinical":
      return patientGateOutcome(view, false);
    case "patient":
      return patientGateOutcome(view, true);
    case "patientTab":
      return patientGateOutcome(
        view,
        input.tab === undefined ||
          (DEMOGRAPHICS_TABS as readonly string[]).includes(input.tab),
      );
    case "bedBoardTab":
      return input.tab === "locations" ? bedBoardOutcome(access) : "care";
    case "bedBoardLocation":
      return bedBoardOutcome(access);
    case "encounterList":
      return encounterListOutcome(access);
  }
}

// ---------------------------------------------------------------------------------------------
// Route-tree merge (design §4.1)

/**
 * The children of a route with ours merged in: an existing child whose id one of ours has is
 * replaced in place (CARE's auth pages by our redirect routes), the others are appended. A
 * second call with the same routes changes nothing (Vite HMR re-evaluating router.tsx, tests).
 */
export function mergeChildren<T>(
  existing: readonly T[] | undefined,
  ours: readonly T[],
  idOf: (route: T) => string,
): T[] {
  const byId = new Map(ours.map((route) => [idOf(route), route]));
  const merged = (existing ?? []).map((route) => {
    const replacement = byId.get(idOf(route));
    if (replacement === undefined) return route;
    byId.delete(idOf(route));
    return replacement;
  });
  return [...merged, ...byId.values()];
}
