// IndieHR P2-T0c (docs/reviews/p2-t0c/design.md §3.7): which gate each route id of the TanStack
// route tree gets, what each gate shows, the watcher scope, the reviewed lists and the route-tree
// merge. Replaces routeKeys.test.ts (the old router's keys).
// Run (from apps/frontend): node --import tsx --test src/Utils/ihr/routeGates.test.ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

import {
  type IhrAccess,
  type IhrPatientView,
  type IhrSlugSets,
  type MyAccess,
  ihrAccess,
  ihrPatientView,
} from "./access";
import * as Gates from "./routeGates";
import {
  APPOINTMENT_ID,
  BED_BOARD_LOCATION_ID,
  BED_BOARD_TAB_ID,
  CLINICAL_ID_RE,
  DEMOGRAPHICS_IDS,
  DEMOGRAPHICS_TABS,
  ENCOUNTER_ID,
  ENCOUNTER_LIST_IDS,
  ENCOUNTER_PREFIX_RE,
  type GateInput,
  IHR_AUTH_REDIRECT_IDS,
  IHR_PAGE_IDS,
  OPEN_CLINICAL_IDS,
  ORG_ENCOUNTER_ID,
  PATIENT_CREATE_ID,
  PATIENT_HOME_ID,
  PATIENT_PREFIX_RE,
  PATIENT_SEARCH_ID,
  PATIENT_TAB_ID,
  PATIENT_UPDATE_ID,
  PUBLIC_ROUTES,
  type RouteGate,
  bedBoardOutcome,
  clinicalProblems,
  encounterGateOutcome,
  encounterListOutcome,
  facilityFromPath,
  gateOutcome,
  leafSignature,
  mergeChildren,
  parseLeaf,
  patientGateOutcome,
  routeGateOf,
  treeIds,
  watcherScope,
} from "./routeGates";

// CI runs this file from apps/frontend (see .github/workflows/apps.yml).
const SRC = join(process.cwd(), "src");
const SLUGS = JSON.parse(
  readFileSync(join(SRC, "Utils", "ihr", "ihrSlugs.json"), "utf-8"),
) as IhrSlugSets & { restricted: string[]; bedForBilling: string[] };
const IDS = treeIds(readFileSync(join(SRC, "routeTree.gen.ts"), "utf-8"));
const ID_SET = new Set(IDS);

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
const SUPER = ihrAccess(
  { facilityPerms: [], myAccess: undefined, isSuperuser: true },
  SLUGS,
);

const ORG_PREFIX = "/_app/organization/$organizationId";
const FAC_PREFIX = "/_app/facility/$facilityId";

describe("treeIds: the parser (§3.7.1)", () => {
  it("reads every id of FileRoutesById", () => {
    assert.ok(IDS.length >= 300, `${IDS.length} ids`);
    for (const id of [
      "__root__",
      "/_app",
      ENCOUNTER_ID,
      "/patient/$id/{-$tab}",
    ]) {
      assert.ok(ID_SET.has(id), id);
    }
    assert.equal(ID_SET.size, IDS.length, "no id twice");
  });
});

describe("named ids exist in the tree (§3.7.2)", () => {
  it("every exported *_ID and every reviewed list names a tree id", () => {
    const named: string[] = [];
    for (const [name, value] of Object.entries(Gates)) {
      if (name.endsWith("_ID") && typeof value === "string") named.push(value);
    }
    assert.ok(named.length >= 10, `${named.length} *_ID exports`);
    named.push(
      ...DEMOGRAPHICS_IDS,
      ...ENCOUNTER_LIST_IDS,
      ...OPEN_CLINICAL_IDS,
      ...Object.keys(PUBLIC_ROUTES),
      ...IHR_AUTH_REDIRECT_IDS,
    );
    for (const id of named) assert.ok(ID_SET.has(id), `${id} is a tree id`);
  });

  it("our pages are not upstream ids, get no gate and are watched as facility pages", () => {
    assert.equal(IHR_PAGE_IDS.length, 5);
    for (const id of IHR_PAGE_IDS) {
      assert.ok(!ID_SET.has(id), `${id} collides with an upstream id`);
      assert.equal(routeGateOf(id), "open", id);
      assert.equal(watcherScope(id), "facility", id);
    }
  });
});

describe("gated prefixes never open (§3.7.3)", () => {
  it("every patient-prefix id (facility and organization) is gated", () => {
    const under = IDS.filter((id) => PATIENT_PREFIX_RE.test(id));
    assert.ok(under.length >= 30, `${under.length} patient-prefix ids`);
    assert.ok(under.some((id) => id.startsWith("/_app/organization/")));
    for (const id of under) assert.notEqual(routeGateOf(id), "open", id);
  });

  it("every encounter-prefix id except ENCOUNTER_ID is encounterPage", () => {
    const under = IDS.filter((id) => ENCOUNTER_PREFIX_RE.test(id));
    assert.ok(under.length >= 15, `${under.length} encounter-prefix ids`);
    for (const id of under) {
      assert.equal(
        routeGateOf(id),
        id === ENCOUNTER_ID ? "encounter" : "encounterPage",
        id,
      );
    }
    assert.equal(routeGateOf(ORG_ENCOUNTER_ID), "encounterPage");
    assert.equal(
      routeGateOf(
        "/_app/organization/organizationId/patient/$patientId/encounter/$encounterId/consents/$consentId",
      ),
      "encounterPage",
    );
  });

  it("every other facility patient-prefix id is patientClinical", () => {
    for (const id of IDS) {
      if (!id.startsWith(`${FAC_PREFIX}/patient/`)) continue;
      if (DEMOGRAPHICS_IDS.has(id) || id === PATIENT_TAB_ID) continue;
      if (ENCOUNTER_PREFIX_RE.test(id)) continue;
      assert.equal(routeGateOf(id), "patientClinical", id);
    }
  });

  it("DEMOGRAPHICS_IDS is exactly the five reviewed ids", () => {
    assert.deepEqual(
      [...DEMOGRAPHICS_IDS].sort(),
      [
        PATIENT_SEARCH_ID,
        PATIENT_HOME_ID,
        PATIENT_CREATE_ID,
        PATIENT_UPDATE_ID,
        APPOINTMENT_ID,
      ].sort(),
    );
    for (const id of DEMOGRAPHICS_IDS) assert.equal(routeGateOf(id), "patient");
  });

  it("the other named rules", () => {
    assert.equal(routeGateOf(ENCOUNTER_ID), "encounter");
    assert.equal(routeGateOf(BED_BOARD_TAB_ID), "bedBoardTab");
    assert.equal(routeGateOf(BED_BOARD_LOCATION_ID), "bedBoardLocation");
    // CARE 3.1 serves the same bed board at the location's /beds and the facility's
    // bed-availability page (P2-T0c review F1): gated like the board
    for (const id of [
      "/_app/facility/$facilityId/locations/$locationId/beds",
      "/_app/facility/$facilityId/bed-availability",
    ]) {
      assert.ok(IDS.includes(id), `${id} is one of CARE's route ids`);
      assert.equal(routeGateOf(id), "bedBoardLocation", id);
    }
    for (const id of ENCOUNTER_LIST_IDS) {
      assert.equal(routeGateOf(id), "encounterList", id);
    }
    assert.equal(routeGateOf(PATIENT_TAB_ID), "patientTab");
    // the appointment's print page asks for charge items: not part of the demographics view
    assert.equal(
      routeGateOf(APPOINTMENT_ID.replace(/\/$/, "/print")),
      "patientClinical",
    );
    for (const id of ["/_app", "__root__", "/", "/_app/$", "/plugin/x"]) {
      assert.equal(routeGateOf(id), "open", id);
    }
  });
});

describe("clinical invariant (§3.7.4)", () => {
  it("no open id with patient or visit data outside OPEN_CLINICAL_IDS, and none stale", () => {
    assert.deepEqual(clinicalProblems(IDS, routeGateOf, OPEN_CLINICAL_IDS), []);
    const openClinical = IDS.filter(
      (id) => CLINICAL_ID_RE.test(id) && routeGateOf(id) === "open",
    );
    assert.deepEqual([...OPEN_CLINICAL_IDS].sort(), openClinical.sort());
    assert.equal(OPEN_CLINICAL_IDS.size, 24);
  });

  it("each pattern element catches a new open id (mutations)", () => {
    for (const id of [
      `${FAC_PREFIX}/visit/$encounterId`,
      `${FAC_PREFIX}/x/$patientId`,
      `${FAC_PREFIX}/x/patient/y`,
      `${FAC_PREFIX}/service_requests/x/new`,
      `${FAC_PREFIX}/medication_dispense/x`,
      `${FAC_PREFIX}/queue/$q/token/x`,
      "/referrals/$referralId/x",
      `${FAC_PREFIX}/x/beds`,
      `${FAC_PREFIX}/bed-availability/x`,
    ]) {
      assert.equal(
        clinicalProblems([...IDS, id], routeGateOf, OPEN_CLINICAL_IDS).length,
        1,
        id,
      );
    }
  });

  it("an id dropped from OPEN_CLINICAL_IDS is a problem", () => {
    for (const dropped of OPEN_CLINICAL_IDS) {
      const fewer = new Set(OPEN_CLINICAL_IDS);
      fewer.delete(dropped);
      assert.deepEqual(clinicalProblems(IDS, routeGateOf, fewer).length, 1);
    }
  });

  it("an organization id with a literal segment is gated (any-segment rule)", () => {
    assert.equal(
      routeGateOf("/_app/organization/organizationId/patient/$patientId/x"),
      "patientClinical",
    );
    assert.equal(
      routeGateOf(`${FAC_PREFIX}/patient/$otherId/plugin-page`),
      "patientClinical",
    );
  });
});

describe("public routes (§3.7.5)", () => {
  it("every /_public id is reviewed in PUBLIC_ROUTES", () => {
    assert.deepEqual(
      Object.keys(PUBLIC_ROUTES).sort(),
      IDS.filter(
        (id) => id === "/_public" || id.startsWith("/_public/"),
      ).sort(),
    );
    for (const id of IHR_AUTH_REDIRECT_IDS) {
      assert.match(PUBLIC_ROUTES[id], /ToLogin/, id);
    }
  });
});

describe("organization counterparts (§3.7.6)", () => {
  it("no organization address of a demographics page or of the patient tabs exists", () => {
    for (const id of [...DEMOGRAPHICS_IDS, PATIENT_TAB_ID]) {
      const org = id.replace(FAC_PREFIX, ORG_PREFIX);
      assert.ok(
        !ID_SET.has(org),
        `${org}: decide a facility-less demographics view`,
      );
      const literal = id.replace(
        FAC_PREFIX,
        "/_app/organization/organizationId",
      );
      assert.ok(!ID_SET.has(literal), literal);
    }
  });
});

describe("params the gates read (§3.7.7)", () => {
  const needs: Partial<Record<RouteGate, RegExp[]>> = {
    encounter: [/\$patientId/, /\$encounterId/, /\$tab/],
    encounterPage: [/\$patientId/],
    patientClinical: [/\$patientId/],
    patientTab: [/\$patientId/, /\{-\$tab\}/],
    bedBoardTab: [/\$tab/],
  };
  it("every gated id carries the params its gate reads", () => {
    for (const id of IDS) {
      if (id.startsWith("/_app/organization/")) continue;
      for (const re of needs[routeGateOf(id)] ?? []) {
        assert.match(id, re, id);
      }
    }
  });
});

describe("gateOutcome (§3.4, §3.7.8)", () => {
  const ALL: RouteGate[] = [
    "encounter",
    "encounterPage",
    "bedBoardTab",
    "bedBoardLocation",
    "encounterList",
    "patient",
    "patientTab",
    "patientClinical",
  ];
  function input(over: Partial<GateInput>): GateInput {
    return {
      gate: "open",
      tab: undefined,
      leafFacilityId: "A",
      currentFacilityId: "A",
      patientId: "p",
      access: access("Doctor"),
      view: "care",
      restrictedAnywhere: false,
      isSuperuser: false,
      ...over,
    };
  }
  const viewOf = (a: IhrAccess): IhrPatientView => ihrPatientView(a, []);

  it("row 1: another facility in the address bar waits, for every gate but open", () => {
    for (const gate of ALL) {
      assert.equal(
        gateOutcome(input({ gate, currentFacilityId: "B" })),
        "loading",
        gate,
      );
      assert.equal(
        gateOutcome(input({ gate, currentFacilityId: undefined })),
        "loading",
        gate,
      );
    }
    assert.equal(gateOutcome(input({ currentFacilityId: "B" })), "care");
  });

  it("row 2: open is CARE's page, whatever the role", () => {
    for (const role of ["Receptionist", "Owner", "Lab Technician"]) {
      const a = access(role);
      assert.equal(gateOutcome(input({ access: a, view: viewOf(a) })), "care");
    }
  });

  it("row 3: organization patient and visit pages follow the person's own restriction", () => {
    const org = (gate: RouteGate, over: Partial<GateInput>) =>
      gateOutcome(
        input({
          gate,
          leafFacilityId: undefined,
          currentFacilityId: undefined,
          access: access("Doctor"),
          ...over,
        }),
      );
    for (const id of [
      ORG_ENCOUNTER_ID,
      `${ORG_PREFIX}/patient/$patientId/diagnostic_reports/$diagnosticReportId/`,
    ]) {
      const gate = routeGateOf(id);
      assert.equal(org(gate, { restrictedAnywhere: true }), "notForRole", id);
      assert.equal(org(gate, { restrictedAnywhere: undefined }), "loading", id);
      assert.equal(org(gate, { restrictedAnywhere: false }), "care", id);
      assert.equal(
        org(gate, { restrictedAnywhere: true, isSuperuser: true }),
        "care",
        id,
      );
      // a records grant is a facility's: MRD with a grant view is still refused here
      assert.equal(
        org(gate, { restrictedAnywhere: true, view: "care" }),
        "notForRole",
        id,
      );
    }
    for (const gate of ["patient", "patientTab", "encounter"] as const) {
      assert.equal(org(gate, { restrictedAnywhere: true }), "notForRole", gate);
    }
  });

  it("row 4: a gated facility id without patientId fails closed for restricted users", () => {
    for (const gate of [
      "patientClinical",
      "encounterPage",
      "encounter",
      "patientTab",
    ] as const) {
      const a = access("Receptionist");
      assert.equal(
        gateOutcome(
          input({ gate, access: a, view: "care", patientId: undefined }),
        ),
        "notForRole",
        gate,
      );
      assert.equal(
        gateOutcome(
          input({ gate, access: access("Doctor"), patientId: undefined }),
        ),
        "care",
        gate,
      );
    }
  });

  it("rows 5-8: patient and encounter pages by access mode and tab", () => {
    const cases: [string, IhrAccess, IhrPatientView][] = [
      ["pending", PENDING, "pending"],
      ["receptionist (demographics)", access("Receptionist"), "demographics"],
      ["owner (none)", access("Owner"), "none"],
      ["MRD without a grant", access("MRD"), "demographics"],
      ["MRD with a grant", access("MRD"), "care"],
      ["doctor (care)", access("Doctor"), "care"],
      ["superuser", SUPER, "care"],
    ];
    for (const [label, a, view] of cases) {
      const at = (gate: RouteGate, tab?: string) =>
        gateOutcome(input({ gate, tab, access: a, view }));
      assert.equal(at("encounter"), encounterGateOutcome(view), label);
      assert.equal(at("encounterPage"), patientGateOutcome(view, false), label);
      assert.equal(
        at("patientClinical"),
        patientGateOutcome(view, false),
        label,
      );
      assert.equal(at("patient"), patientGateOutcome(view, true), label);
      assert.equal(at("patientTab"), patientGateOutcome(view, true), label);
      for (const tab of DEMOGRAPHICS_TABS) {
        assert.equal(
          at("patientTab", tab),
          patientGateOutcome(view, true),
          tab,
        );
      }
      for (const tab of ["history", "files", "devices"]) {
        assert.equal(
          at("patientTab", tab),
          patientGateOutcome(view, false),
          tab,
        );
      }
    }
    // the demographics view by name
    const r = access("Receptionist");
    assert.equal(
      gateOutcome(
        input({ gate: "encounter", access: r, view: "demographics" }),
      ),
      "status",
    );
    assert.equal(
      gateOutcome(
        input({
          gate: "patientTab",
          tab: "history",
          access: r,
          view: "demographics",
        }),
      ),
      "notForRole",
    );
    assert.equal(
      gateOutcome(
        input({ gate: "patientClinical", access: r, view: "demographics" }),
      ),
      "notForRole",
    );
  });

  it("rows 9-11: bed board and encounter lists", () => {
    for (const [role, a] of [
      ["pending", PENDING],
      ["Cashier", access("Cashier")],
      ["Radiologist", access("Radiologist")],
      ["Owner", access("Owner")],
      ["Receptionist", access("Receptionist")],
    ] as const) {
      assert.equal(
        gateOutcome(
          input({ gate: "bedBoardTab", tab: "locations", access: a }),
        ),
        bedBoardOutcome(a),
        role,
      );
      assert.equal(
        gateOutcome(input({ gate: "bedBoardTab", tab: "patients", access: a })),
        "care",
        role,
      );
      assert.equal(
        gateOutcome(input({ gate: "bedBoardLocation", access: a })),
        bedBoardOutcome(a),
        role,
      );
      assert.equal(
        gateOutcome(input({ gate: "encounterList", access: a })),
        encounterListOutcome(a),
        role,
      );
    }
    assert.equal(bedBoardOutcome(access("Cashier")), "notForRole");
    assert.equal(encounterListOutcome(access("Owner")), "notForRole");
  });

  it("outcome helpers keep today's answers", () => {
    assert.equal(patientGateOutcome("pending", false), "loading");
    assert.equal(patientGateOutcome("none", true), "notForRole");
    assert.equal(patientGateOutcome("demographics", false), "notForRole");
    assert.equal(patientGateOutcome("demographics", true), "care");
    assert.equal(encounterGateOutcome("demographics"), "status");
    const failed = access("Receptionist", null);
    assert.equal(bedBoardOutcome(failed), "loading");
    assert.equal(encounterListOutcome(failed), "loading");
  });
});

describe("leafSignature: the deepest match that did not fail (§3.2, §3.7.8)", () => {
  const m = (
    routeId: string,
    status = "success",
    params = {},
    pathname = "/",
  ) => ({
    routeId,
    status,
    params,
    pathname,
  });

  it("takes the deepest match and the facility from its params", () => {
    const leaf = parseLeaf(
      leafSignature([
        m("__root__"),
        m("/_app"),
        m(
          ENCOUNTER_ID,
          "success",
          {
            facilityId: "F1",
            patientId: "P",
            encounterId: "E",
            tab: "updates",
          },
          "/facility/F1/patient/P/encounter/E/updates",
        ),
      ]),
    );
    assert.equal(leaf.routeId, ENCOUNTER_ID);
    assert.equal(leaf.facilityId, "F1");
    assert.equal(leaf.params.patientId, "P");
    assert.equal(leaf.failed, false);
  });

  it("a failed child falls back to the ancestor's gate", () => {
    const leaf = parseLeaf(
      leafSignature([m("__root__"), m("/_app"), m(ENCOUNTER_ID, "notFound")]),
    );
    assert.equal(leaf.routeId, "/_app");
    assert.equal(leaf.failed, true);
    assert.equal(routeGateOf(leaf.routeId), "open");
  });

  it("a failed child under a patient layout keeps the layout's gate", () => {
    const layout = `${FAC_PREFIX}/patient/$patientId/layout-x`;
    const leaf = parseLeaf(
      leafSignature([
        m("__root__"),
        m("/_app"),
        m(
          layout,
          "success",
          { facilityId: "F", patientId: "P" },
          "/facility/F/patient/P/layout-x",
        ),
        m(`${layout}/child`, "error"),
      ]),
    );
    assert.equal(leaf.routeId, layout);
    assert.equal(leaf.failed, true);
    const gate = routeGateOf(leaf.routeId);
    assert.equal(gate, "patientClinical");
    const r = access("Receptionist");
    assert.equal(
      gateOutcome({
        gate,
        tab: undefined,
        leafFacilityId: leaf.facilityId,
        currentFacilityId: "F",
        patientId: leaf.params.patientId,
        access: r,
        view: "demographics",
        restrictedAnywhere: true,
        isSuperuser: false,
      }),
      "notForRole",
    );
  });

  it("no match at all is the root; the facility falls back to the path", () => {
    assert.equal(parseLeaf(leafSignature([])).routeId, "__root__");
    const leaf = parseLeaf(
      leafSignature([
        m("/_app/plugin", "success", { fid: "F" }, "/facility/F9/plugin"),
      ]),
    );
    assert.equal(leaf.facilityId, "F9");
    assert.equal(facilityFromPath("/facility/F/x"), "F");
    assert.equal(facilityFromPath("/facility/"), undefined);
    assert.equal(facilityFromPath("/organization/x/patient/y"), undefined);
  });

  it("is stable for equal matches (one primitive for the selector)", () => {
    const a = [
      m("/_app"),
      m(ENCOUNTER_ID, "success", { facilityId: "F" }, "/x"),
    ];
    const b = [
      m("/_app"),
      m(ENCOUNTER_ID, "success", { facilityId: "F" }, "/x"),
    ];
    assert.equal(leafSignature(a), leafSignature(b));
  });
});

describe("watcherScope (§3.6, §3.7.9)", () => {
  it("facility pages, facility-less charts, nothing else", () => {
    for (const id of IDS) {
      const expected = /^\/_app\/facility\/[^/]+\//.test(id)
        ? "facility"
        : /^\/_app\/patient\//.test(id) ||
            /^\/patient\/\$id\//.test(id) ||
            /^\/_app\/organization\/[^/]+\/patient\//.test(id)
          ? "facilityLess"
          : null;
      assert.equal(watcherScope(id), expected, id);
    }
    assert.equal(watcherScope("/patient/$id/{-$tab}"), "facilityLess");
    assert.equal(watcherScope(ORG_ENCOUNTER_ID), "facilityLess");
    assert.equal(
      watcherScope("/_app/patient/$patientId/history/$tab"),
      "facilityLess",
    );
    assert.equal(watcherScope(ENCOUNTER_ID), "facility");
    for (const id of [
      "/patient/home",
      "/_app/organization/$id/patients",
      "/_app",
      "__root__",
    ]) {
      assert.equal(watcherScope(id), null, id);
    }
  });
});

describe("mergeChildren (§4.1, §3.7.10)", () => {
  interface R {
    id: string;
    tag: string;
  }
  const idOf = (r: R) => r.id;
  const existing: R[] = [
    { id: "/2fa", tag: "file" },
    { id: "/login", tag: "file" },
    { id: "/forgot-password", tag: "file" },
  ];
  const ours: R[] = [
    { id: "/2fa", tag: "ours" },
    { id: "/forgot-password", tag: "ours" },
    { id: "/facility/$facilityId/ihr/audit", tag: "ours" },
  ];

  it("replaces in place and appends the new ones", () => {
    const once = mergeChildren(existing, ours, idOf);
    assert.deepEqual(
      once.map((r) => `${r.id}:${r.tag}`),
      [
        "/2fa:ours",
        "/login:file",
        "/forgot-password:ours",
        "/facility/$facilityId/ihr/audit:ours",
      ],
    );
    assert.deepEqual(
      existing.map((r) => r.tag),
      ["file", "file", "file"],
    );
  });

  it("is idempotent: a second call adds nothing", () => {
    const once = mergeChildren(existing, ours, idOf);
    const twice = mergeChildren(once, ours, idOf);
    assert.deepEqual(twice, once);
    assert.equal(new Set(twice.map(idOf)).size, twice.length);
  });

  it("takes no children as an empty list", () => {
    assert.deepEqual(mergeChildren(undefined, ours, idOf), ours);
  });
});
