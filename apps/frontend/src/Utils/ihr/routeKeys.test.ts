// IndieHR P1-T11c1 (design-c.md §13.2, §13.8): which gate each of CARE's route keys gets, and
// what each gate shows. Run: node --import tsx --test src/Utils/ihr/routeKeys.test.ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

import {
  type IhrAccess,
  type IhrSlugSets,
  type MyAccess,
  ihrAccess,
  ihrPatientView,
} from "./access";
import {
  DEMOGRAPHICS_KEYS,
  DEMOGRAPHICS_TABS,
  ENCOUNTER_KEY,
  PATIENT_TAB_PREFIX,
  REQUIRED_KEYS,
  bedBoardOutcome,
  encounterGateOutcome,
  encounterListOutcome,
  patientGateOutcome,
  routeGate,
  watcherScope,
} from "./routeKeys";

// CI runs this file from apps/frontend (see .github/workflows/apps.yml).
const SRC = join(process.cwd(), "src");
const SLUGS = JSON.parse(
  readFileSync(join(SRC, "Utils", "ihr", "ihrSlugs.json"), "utf-8"),
) as IhrSlugSets & { restricted: string[]; bedForBilling: string[] };

/** The string-literal route keys of CARE's route tables (dynamic ones are added below). */
function upstreamKeys(): string[] {
  const keys = new Set<string>();
  for (const file of [
    "ConsultationRoutes.tsx",
    "PatientRoutes.tsx",
    "FacilityRoutes.tsx",
    "ScheduleRoutes.tsx",
  ]) {
    const text = readFileSync(join(SRC, "Routers", "routes", file), "utf-8");
    for (const match of text.matchAll(/"(\/[^"\s]+)"\s*[:,\]]/g)) {
      keys.add(match[1]);
    }
  }
  return [...keys];
}

function access(role: string, myAccess?: MyAccess | null): IhrAccess {
  return ihrAccess(
    {
      facilityPerms: SLUGS.grants[role],
      myAccess:
        myAccess === undefined
          ? {
              restricted: SLUGS.restricted.includes(role),
              bills_here: SLUGS.bedForBilling.includes(role),
              hospital_admin: false,
            }
          : myAccess,
      isSuperuser: false,
    },
    SLUGS,
  );
}

const PENDING = ihrAccess(
  { facilityPerms: undefined, myAccess: undefined, isSuperuser: false },
  SLUGS,
);

describe("routeGate: CARE's keys (C1-1, C1-2, C1-5)", () => {
  const keys = upstreamKeys();

  it("gates every encounter sub-page, not only the :tab key", () => {
    const sub = keys.filter((k) =>
      k.startsWith("/facility/:facilityId/patient/:patientId/encounter/"),
    );
    // questionnaire, questionnaire/:slug, questionnaire_response/:id, report/:reportId,
    // report/template/:templateSlug, consents/:consentId and the print pages
    assert.ok(sub.length >= 10, `found only ${sub.length}: ${sub.join(", ")}`);
    for (const key of sub) assert.equal(routeGate(key), "encounterPage", key);
    assert.equal(routeGate(ENCOUNTER_KEY), "encounter");
  });

  it("gates every facility patient key; a demographics view opens only the allow-list", () => {
    const patient = keys.filter(
      (k) =>
        k.startsWith("/facility/:facilityId/patient/") &&
        !k.includes("/encounter/"),
    );
    assert.ok(patient.length >= 10);
    for (const key of patient) {
      const gate = routeGate(key);
      assert.ok(gate === "patient" || gate === "patientClinical", key);
      assert.equal(gate === "patient", DEMOGRAPHICS_KEYS.has(key), key);
    }
    for (const key of [
      "/facility/:facilityId/patient/:patientId/history/:tab",
      "/facility/:facilityId/patient/:patientId/diagnostic_reports/:diagnosticReportId",
      "/facility/:facilityId/patient/:patientId/prescription/:prescriptionId/print",
      "/facility/:facilityId/patient/:patientId/service_request/:serviceRequestId/diagnostic_reports/print",
      "/facility/:facilityId/patient/:patientId/questionnaire",
      "/facility/:facilityId/patient/:patientId/consultation",
      "/facility/:facilityId/patient/:patientId/appointments/:appointmentId/print",
      `${PATIENT_TAB_PREFIX}notes`,
      `${PATIENT_TAB_PREFIX}clinical_history`,
      "/facility/:facilityId/patient/:patientId/some_new_upstream_page",
    ]) {
      assert.equal(routeGate(key), "patientClinical", key);
    }
  });

  it("lets a demographics view open search, home, create, the patient, its four tabs, edit and an appointment", () => {
    for (const key of [
      "/facility/:facilityId/patients",
      "/facility/:facilityId/patients/home",
      "/facility/:facilityId/patient/create",
      "/facility/:facilityId/patient/:id",
      "/facility/:facilityId/patient/:id/update",
      "/facility/:facilityId/patient/:patientId/appointments/:appointmentId",
      ...DEMOGRAPHICS_TABS.map((t) => PATIENT_TAB_PREFIX + t),
    ]) {
      assert.equal(routeGate(key), "patient", key);
    }
  });

  it("every allow-listed and required key exists in CARE's tables (tabs: as CARE builds them)", () => {
    const tabs = readFileSync(
      join(SRC, "components", "Patient", "PatientDetailsTab", "index.tsx"),
      "utf-8",
    );
    const present = new Set(keys);
    for (const key of REQUIRED_KEYS) {
      if (key === ENCOUNTER_KEY) continue; // built per identifier; check-ihr-frontend.py
      if (
        key.startsWith(PATIENT_TAB_PREFIX) &&
        key !== `${PATIENT_TAB_PREFIX}update`
      ) {
        const tab = key.slice(PATIENT_TAB_PREFIX.length);
        assert.ok(tabs.includes(`route: "${tab}"`), `patient tab ${tab}`);
        continue;
      }
      assert.ok(present.has(key), key);
    }
    for (const key of DEMOGRAPHICS_KEYS) {
      assert.ok(REQUIRED_KEYS.includes(key), `${key} is not required`);
    }
  });

  it("gates the encounter lists and the bed board; leaves other keys alone", () => {
    assert.equal(
      routeGate("/facility/:facilityId/encounters/patients/all"),
      "encounterList",
    );
    assert.equal(
      routeGate("/facility/:facilityId/encounters/patients/:encounterClass"),
      "encounterList",
    );
    assert.equal(
      routeGate("/facility/:facilityId/encounters/:tab"),
      "bedBoardTab",
    );
    assert.equal(
      routeGate("/facility/:facilityId/encounters/locations/:locationId"),
      "bedBoardLocation",
    );
    assert.equal(routeGate("/facility/:facilityId/overview"), "none");
  });
});

describe("the grant watcher's scope (review F6)", () => {
  it("wraps every facility route and CARE's facility-less patient charts", () => {
    const keys = upstreamKeys();
    const facilityLess = keys.filter((k) => watcherScope(k) === "facilityLess");
    // built in an array by CARE (the regex of upstreamKeys does not see it)
    assert.equal(watcherScope("/patient/:id/:tab"), "facilityLess");
    for (const key of [
      "/patient/:id",
      "/patient/:patientId/history/:tab",
      "/organization/organizationId/patient/:patientId/diagnostic_reports/:diagnosticReportId/print",
    ]) {
      assert.ok(keys.includes(key), `${key} is one of CARE's keys`);
      assert.equal(watcherScope(key), "facilityLess", key);
    }
    assert.ok(facilityLess.length >= 4);
    for (const key of keys.filter((k) => k.startsWith("/facility/"))) {
      assert.equal(watcherScope(key), "facility", key);
    }
  });

  it("leaves lists and other pages alone", () => {
    for (const key of [
      "/patient/home",
      "/organization/:id/patients",
      "/admin/patient_identifier_config",
      "/",
    ]) {
      assert.equal(watcherScope(key), null, key);
    }
  });
});

describe("gate outcomes", () => {
  it("encounter sub-pages: CARE's only for care; refused for demographics and none (C1-1)", () => {
    assert.equal(patientGateOutcome("pending", false), "loading");
    assert.equal(patientGateOutcome("none", false), "notForRole");
    assert.equal(patientGateOutcome("demographics", false), "notForRole");
    assert.equal(patientGateOutcome("care", false), "care");
    // as the roles see them (no patient fetched for "none")
    assert.equal(
      patientGateOutcome(ihrPatientView(access("Owner"), undefined), false),
      "notForRole",
    );
    assert.equal(
      patientGateOutcome(
        ihrPatientView(access("Lab Technician"), undefined),
        false,
      ),
      "notForRole",
    );
    assert.equal(
      patientGateOutcome(ihrPatientView(access("Receptionist"), []), false),
      "notForRole",
    );
    assert.equal(
      patientGateOutcome(ihrPatientView(access("Radiologist"), []), false),
      "care",
    );
  });

  it("patient pages: demographics opens only allow-listed keys (C1-2)", () => {
    assert.equal(patientGateOutcome("demographics", true), "care");
    for (const role of ["Receptionist", "Cashier", "TPA Desk", "MRD"]) {
      const view = ihrPatientView(access(role), []);
      assert.equal(view, "demographics", role);
      assert.equal(patientGateOutcome(view, false), "notForRole", role);
      assert.equal(patientGateOutcome(view, true), "care", role);
    }
  });

  it("the encounter page: status view for demographics", () => {
    assert.equal(encounterGateOutcome("demographics"), "status");
    assert.equal(encounterGateOutcome("none"), "notForRole");
    assert.equal(encounterGateOutcome("pending"), "loading");
    assert.equal(encounterGateOutcome("care"), "care");
  });

  it("encounter lists: refused to restricted roles without the status slug (C1-5)", () => {
    for (const role of ["Owner", "Lab Technician", "Store Keeper"]) {
      assert.equal(encounterListOutcome(access(role)), "notForRole", role);
    }
    for (const role of [
      "Receptionist",
      "Cashier",
      "TPA Desk",
      "MRD",
      "Radiologist",
    ]) {
      assert.equal(encounterListOutcome(access(role)), "care", role);
    }
    assert.equal(encounterListOutcome(PENDING), "loading");
  });

  it("every gate waits, never opens CARE's page, when my-access failed with nothing cached (C1-3)", () => {
    const failed = access("Receptionist", null);
    assert.equal(bedBoardOutcome(failed), "loading");
    assert.equal(encounterListOutcome(failed), "loading");
    assert.equal(encounterGateOutcome(ihrPatientView(failed, [])), "loading");
    assert.equal(bedBoardOutcome(access("Cashier")), "notForRole");
    assert.equal(bedBoardOutcome(access("Radiologist")), "care");
  });
});
