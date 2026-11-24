// IndieHR P1-T11c2 (design-c.md §3.1, §3.3, §5.1, §13.6, §13.8): the front-desk and billing-desk
// rules. Run: node --import tsx --test src/Utils/ihr/frontDesk.test.ts
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
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
import {
  DEMOGRAPHY_PLUGIN_SLOT_KEY,
  REFUSED_ENCOUNTER_FILTER_KEYS,
  REFUSED_ENCOUNTER_PARAMS,
  ihrAccountActions,
  ihrAccountOfVisitQuery,
  ihrAccountTabs,
  ihrAnyTabVisible,
  ihrAppointmentsFacilityId,
  ihrCanAddChargeItems,
  ihrCanListEncounters,
  ihrCanOpenEncounter,
  ihrDemographySections,
  ihrEncounterFilters,
  ihrEncounterHistoryRule,
  ihrEncounterListEnabled,
  ihrEncounterParams,
  ihrHidesClinicalFormFields,
  ihrHidesPatientTags,
  ihrPatientTabs,
  ihrShowBookingCard,
  ihrShowsEncounterActions,
  ihrShowsEncounterTags,
  ihrShowsViewAccounts,
  ihrStatusBlocks,
} from "./frontDesk";
import { APPOINTMENT_ID, DEMOGRAPHICS_TABS, routeGateOf } from "./routeGates";

// CI runs this file from apps/frontend (see .github/workflows/apps.yml).
const SLUGS = JSON.parse(
  readFileSync(
    join(process.cwd(), "src", "Utils", "ihr", "ihrSlugs.json"),
    "utf-8",
  ),
) as IhrSlugSets & { restricted: string[]; bedForBilling: string[] };

function forRole(role: string, myAccess?: MyAccess | null): IhrAccess {
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
// my-access failed with nothing cached: pending too (fails closed)
const FAILED = forRole("Receptionist", null);
const CARE_ROLE = ihrAccess(
  {
    facilityPerms: ["can_view_clinical_data", "can_list_patients"],
    myAccess: { restricted: false, bills_here: false, hospital_admin: false },
    isSuperuser: false,
  },
  SLUGS,
);
const RECEPTIONIST = forRole("Receptionist");
const CASHIER = forRole("Cashier");
const TPA = forRole("TPA Desk");
const MRD = forRole("MRD");
const OWNER = forRole("Owner");

/** What the patient page gets for a patient with no open visit (F4: an empty list). */
function view(access: IhrAccess, perms: string[] | undefined = []) {
  return ihrPatientView(access, perms);
}

const ALL_TABS = [
  "demography",
  "appointments",
  "encounters",
  "updates",
  "resource_requests",
  "users",
  "notes",
  "files",
  "accounts",
  "clinical_history",
].map((route) => ({ route }));
const CARE_TABS = [{ route: "demography" }, { route: "notes" }];
const routes = (tabs: { route: string }[]) => tabs.map((t) => t.route);

describe("ihrPatientTabs", () => {
  it("shows the Receptionist exactly Demography, Appointments, Encounters, Accounts", () => {
    const tabs = ihrPatientTabs(
      CARE_TABS,
      ALL_TABS,
      view(RECEPTIONIST),
      RECEPTIONIST,
    );
    assert.deepEqual(routes(tabs), [
      "demography",
      "appointments",
      "encounters",
      "accounts",
    ]);
  });

  it("shows the Cashier and TPA Desk Demography, Encounters, Accounts (no booking slug)", () => {
    for (const access of [CASHIER, TPA]) {
      const tabs = ihrPatientTabs(CARE_TABS, ALL_TABS, view(access), access);
      assert.deepEqual(routes(tabs), ["demography", "encounters", "accounts"]);
    }
  });

  it("shows MRD without a grant Demography and Encounters only", () => {
    const tabs = ihrPatientTabs(CARE_TABS, ALL_TABS, view(MRD), MRD);
    assert.deepEqual(routes(tabs), ["demography", "encounters"]);
  });

  it("never offers a tab outside the demographics list, whatever the slugs", () => {
    const rich = ihrAccess(
      {
        facilityPerms: Object.values(SLUGS.grants).flat(),
        myAccess: { restricted: true, bills_here: true, hospital_admin: false },
        isSuperuser: false,
      },
      SLUGS,
    );
    const tabs = ihrPatientTabs(CARE_TABS, ALL_TABS, view(rich), rich);
    for (const tab of routes(tabs)) {
      assert.ok((DEMOGRAPHICS_TABS as readonly string[]).includes(tab), tab);
    }
  });

  it("gives CARE's own list in care view (MRD under a grant, CARE's roles)", () => {
    const granted = view(MRD, ["can_list_patients", "can_view_clinical_data"]);
    assert.equal(granted, "care");
    assert.equal(ihrPatientTabs(CARE_TABS, ALL_TABS, granted, MRD), CARE_TABS);
    assert.equal(
      ihrPatientTabs(CARE_TABS, ALL_TABS, view(CARE_ROLE), CARE_ROLE),
      CARE_TABS,
    );
  });

  it("shows no tab while pending, when my-access failed, or for roles without patients", () => {
    for (const access of [PENDING, FAILED]) {
      assert.deepEqual(
        ihrPatientTabs(CARE_TABS, ALL_TABS, view(access), access),
        [],
      );
    }
    // the patient itself is still loading: demographics roles wait
    const loading = ihrPatientView(RECEPTIONIST, undefined);
    assert.equal(loading, "pending");
    assert.deepEqual(
      ihrPatientTabs(CARE_TABS, ALL_TABS, loading, RECEPTIONIST),
      [],
    );
    assert.deepEqual(
      ihrPatientTabs(CARE_TABS, ALL_TABS, view(OWNER), OWNER),
      [],
    );
  });
});

describe("ihrAppointmentsFacilityId", () => {
  it("names the facility only in the demographics view", () => {
    assert.equal(ihrAppointmentsFacilityId("demographics", "f1"), "f1");
    for (const v of ["care", "pending", "none"] as IhrPatientView[]) {
      assert.equal(ihrAppointmentsFacilityId(v, "f1"), undefined);
    }
  });
});

describe("ihrDemographySections", () => {
  const bloodRow = { label: "Blood group", value: "O+" };
  const nameRow = { label: "Full name", value: "A" };
  const pluginSlot = { key: DEMOGRAPHY_PLUGIN_SLOT_KEY, type: "x" };
  const other = { key: "emergency-contact" };
  const sections = () => [
    {
      id: "general-info",
      allowEdit: false,
      details: [pluginSlot, nameRow, bloodRow, other],
    },
    { id: "additional-details", allowEdit: true, details: [{ label: "x" }] },
    { id: "identifiers", allowEdit: false, details: [] },
    { id: "tags", allowEdit: true, details: [] },
  ];

  it("removes the plugin and tag sections, the plugin slot and the blood group row", () => {
    const out = ihrDemographySections(
      sections(),
      "demographics",
      RECEPTIONIST,
      true,
      "Blood group",
    );
    assert.deepEqual(
      out.map((s) => s.id),
      ["general-info", "identifiers"],
    );
    assert.deepEqual(out[0].details, [nameRow, other]);
  });

  it("allows edit on the general section only for the Receptionist and only in a facility", () => {
    const edit = (access: IhrAccess, facility: boolean) =>
      ihrDemographySections(
        sections(),
        view(access),
        access,
        facility,
        "Blood group",
      ).map((s) => [s.id, s.allowEdit]);
    assert.deepEqual(edit(RECEPTIONIST, true), [
      ["general-info", true],
      ["identifiers", false],
    ]);
    assert.deepEqual(edit(RECEPTIONIST, false), [
      ["general-info", false],
      ["identifiers", false],
    ]);
    for (const access of [CASHIER, TPA, MRD]) {
      assert.deepEqual(edit(access, true), [
        ["general-info", false],
        ["identifiers", false],
      ]);
    }
  });

  it("leaves CARE's sections untouched in care view and shows none while pending or for none", () => {
    const own = sections();
    assert.equal(
      ihrDemographySections(own, "care", CARE_ROLE, true, "Blood group"),
      own,
    );
    for (const v of ["pending", "none"] as IhrPatientView[]) {
      assert.deepEqual(
        ihrDemographySections(sections(), v, PENDING, true, "Blood group"),
        [],
      );
    }
  });

  it("does not change the caller's sections", () => {
    const own = sections();
    ihrDemographySections(
      own,
      "demographics",
      RECEPTIONIST,
      true,
      "Blood group",
    );
    assert.equal(own.length, 4);
    assert.equal(own[0].details.length, 4);
    assert.equal(own[1].allowEdit, true);
  });
});

describe("ihrHidesClinicalFormFields", () => {
  it("hides blood group, extensions and the death record (a 400 for this role) on update unless the view is CARE's", () => {
    assert.equal(ihrHidesClinicalFormFields("demographics", true), true);
    assert.equal(ihrHidesClinicalFormFields("pending", true), true);
    assert.equal(ihrHidesClinicalFormFields("none", true), true);
    assert.equal(ihrHidesClinicalFormFields("care", true), false);
  });
  it("keeps them on create (registration records blood group)", () => {
    for (const v of ["care", "demographics", "pending"] as IhrPatientView[]) {
      assert.equal(ihrHidesClinicalFormFields(v, false), false);
    }
  });
});

describe("encounter history, cards, patient home", () => {
  it("ihrEncounterHistoryRule: CARE's flag in care view, the status slug in demographics, waits while pending", () => {
    assert.equal(ihrEncounterHistoryRule(true, "care", CARE_ROLE), "yes");
    assert.equal(ihrEncounterHistoryRule(false, "care", CARE_ROLE), "no");
    assert.equal(
      ihrEncounterHistoryRule(false, "demographics", RECEPTIONIST),
      "yes",
    );
    assert.equal(ihrEncounterHistoryRule(false, "demographics", OWNER), "no");
    assert.equal(ihrEncounterHistoryRule(true, "none", OWNER), "no");
    // pending never redirects away and never lists
    assert.equal(ihrEncounterHistoryRule(true, "pending", PENDING), "pending");
  });

  it("ihrCanOpenEncounter: the status slug opens it in restricted mode only", () => {
    assert.equal(ihrCanOpenEncounter(false, RECEPTIONIST), true);
    assert.equal(ihrCanOpenEncounter(false, OWNER), false);
    assert.equal(ihrCanOpenEncounter(false, CARE_ROLE), false);
    assert.equal(ihrCanOpenEncounter(true, CARE_ROLE), true);
    assert.equal(ihrCanOpenEncounter(false, PENDING), false);
  });

  it("ihrCanListEncounters: lists for the demographics view with the status slug, never while pending", () => {
    assert.equal(ihrCanListEncounters(false, "demographics", MRD), true);
    assert.equal(ihrCanListEncounters(false, "demographics", OWNER), false);
    assert.equal(ihrCanListEncounters(true, "care", CARE_ROLE), true);
    assert.equal(ihrCanListEncounters(false, "care", CARE_ROLE), false);
    assert.equal(ihrCanListEncounters(true, "pending", PENDING), false);
    assert.equal(ihrCanListEncounters(true, "none", OWNER), false);
  });

  it("ihrShowsViewAccounts: can_read_account in the demographics view", () => {
    assert.equal(ihrShowsViewAccounts("demographics", RECEPTIONIST), true);
    assert.equal(ihrShowsViewAccounts("demographics", CASHIER), true);
    assert.equal(ihrShowsViewAccounts("demographics", MRD), false);
    assert.equal(ihrShowsViewAccounts("care", CARE_ROLE), true);
    assert.equal(ihrShowsViewAccounts("pending", PENDING), false);
  });

  it("hides the tag card and restart menu outside CARE's mode", () => {
    assert.equal(ihrHidesPatientTags("care"), false);
    for (const v of ["demographics", "pending", "none"] as IhrPatientView[]) {
      assert.equal(ihrHidesPatientTags(v), true);
    }
    assert.equal(ihrShowsEncounterActions(CARE_ROLE), true);
    assert.equal(ihrShowsEncounterTags(CARE_ROLE), true);
    for (const access of [RECEPTIONIST, CASHIER, MRD, PENDING, FAILED]) {
      assert.equal(ihrShowsEncounterActions(access), false);
      assert.equal(ihrShowsEncounterTags(access), false);
    }
  });
});

describe("encounter list filters and parameters", () => {
  const filters = [
    "status",
    "priority",
    "organization",
    "care_team",
    "tags",
    "created_date",
  ].map((key) => ({ key }));

  it("drops department, care team and tag filters outside CARE's mode", () => {
    for (const access of [RECEPTIONIST, CASHIER, MRD, PENDING]) {
      assert.deepEqual(
        ihrEncounterFilters(filters, access).map((f) => f.key),
        ["status", "priority", "created_date"],
      );
    }
    assert.equal(ihrEncounterFilters(filters, CARE_ROLE), filters);
  });

  it("drops every refused parameter, even one restored from the browser's session", () => {
    const params = {
      status: "in_progress",
      organization: "o",
      care_team_user: "u",
      care_team: "u",
      tags: "t1,t2",
      tags_behavior: "all",
      location: "l",
      page: 2,
      name: "Asha",
    };
    for (const access of [RECEPTIONIST, TPA, PENDING]) {
      assert.deepEqual(ihrEncounterParams(params, access), {
        status: "in_progress",
        page: 2,
        name: "Asha",
      });
    }
    assert.equal(ihrEncounterParams(params, CARE_ROLE), params);
    assert.equal(params.organization, "o"); // the caller's object is not changed
  });

  it("matches the backend's refuse list (ihr_access/fields/encounter_filters.txt)", () => {
    const file = join(
      process.cwd(),
      "..",
      "..",
      "plugins",
      "ihr_platform",
      "ihr_access",
      "fields",
      "encounter_filters.txt",
    );
    assert.ok(existsSync(file), `${file} is missing`);
    const refused = readFileSync(file, "utf-8")
      .split("\n")
      .map((line) => /^(\w+) refuse\b/.exec(line)?.[1])
      .filter((name): name is string => !!name)
      .sort();
    assert.deepEqual([...REFUSED_ENCOUNTER_PARAMS].sort(), refused);
    for (const key of REFUSED_ENCOUNTER_FILTER_KEYS) {
      assert.ok(
        refused.includes(key) ||
          (key === "care_team" && refused.includes("care_team_user")),
        key,
      );
    }
  });

  it("asks the API only once my-access has answered", () => {
    assert.equal(ihrEncounterListEnabled(PENDING), false);
    assert.equal(ihrEncounterListEnabled(FAILED), false);
    assert.equal(ihrEncounterListEnabled(RECEPTIONIST), true);
    assert.equal(ihrEncounterListEnabled(CARE_ROLE), true);
  });
});

describe("account page", () => {
  const tabs = () => ({
    invoices: { label: "i" },
    charge_items: { label: "c" },
    payments: { label: "p" },
    reports: { label: "r" },
    bed_charge_items: { label: "b" },
  });
  const visible = (access: IhrAccess) =>
    Object.entries(ihrAccountTabs(tabs(), access))
      .filter(([, tab]) => (tab as { visible?: boolean }).visible !== false)
      .map(([key]) => key);

  it("shows the Cashier everything but Reports, with Bed charges", () => {
    assert.deepEqual(visible(CASHIER), [
      "invoices",
      "charge_items",
      "payments",
      "bed_charge_items",
    ]);
  });

  it("shows the TPA Desk the same tabs (it reads charges and sees the bed)", () => {
    assert.deepEqual(visible(TPA), [
      "invoices",
      "charge_items",
      "payments",
      "bed_charge_items",
    ]);
  });

  it("hides Bed charges where the role does not see the bed (bills_here false)", () => {
    const elsewhere = forRole("Cashier", {
      restricted: true,
      bills_here: false,
      hospital_admin: false,
    });
    assert.deepEqual(visible(elsewhere), [
      "invoices",
      "charge_items",
      "payments",
    ]);
  });

  it("shows the Receptionist no tab (only can_read_account) and says so", () => {
    assert.deepEqual(visible(RECEPTIONIST), []);
    assert.equal(ihrAnyTabVisible(ihrAccountTabs(tabs(), RECEPTIONIST)), false);
    assert.equal(ihrAnyTabVisible(ihrAccountTabs(tabs(), CASHIER)), true);
  });

  it("keeps every key (NavTabs reads tabs[currentTab]) and CARE's object in care mode", () => {
    const own = tabs();
    assert.equal(ihrAccountTabs(own, CARE_ROLE), own);
    assert.deepEqual(
      Object.keys(ihrAccountTabs(tabs(), CASHIER)),
      Object.keys(tabs()),
    );
  });

  it("shows no tab while pending, and refuses an unknown upstream tab", () => {
    assert.deepEqual(visible(PENDING), []);
    assert.deepEqual(visible(FAILED), []);
    const withNew = { ...tabs(), statements: { label: "s" } };
    const out = ihrAccountTabs(withNew, CASHIER) as Record<
      string,
      { visible?: boolean }
    >;
    assert.equal(out.statements.visible, false);
  });

  it("does not turn a hidden tab visible", () => {
    const out = ihrAccountTabs({ invoices: { visible: false } }, CASHIER);
    assert.equal(out.invoices.visible, false);
  });

  it("offers Add charge items to the Cashier, never to the TPA Desk (room rent, Q-K37a B)", () => {
    assert.equal(ihrCanAddChargeItems(true, CASHIER), true);
    assert.equal(ihrCanAddChargeItems(true, TPA), false);
    assert.equal(ihrCanAddChargeItems(true, RECEPTIONIST), false);
    assert.equal(ihrCanAddChargeItems(false, CASHIER), false);
    assert.equal(ihrCanAddChargeItems(true, CARE_ROLE), true);
    assert.equal(ihrCanAddChargeItems(false, CARE_ROLE), false);
    assert.equal(ihrCanAddChargeItems(true, PENDING), false);
  });
});

describe("account page write buttons (a button is shown when its request can succeed)", () => {
  it("the Receptionist, who only reads accounts, is offered none", () => {
    assert.deepEqual(ihrAccountActions(RECEPTIONIST), {
      invoice: false,
      payment: false,
      account: false,
      chargeItems: false,
    });
  });
  it("the TPA Desk updates the account (close, tags) but writes no invoice, payment or charge item", () => {
    assert.deepEqual(ihrAccountActions(TPA), {
      invoice: false,
      payment: false,
      account: true,
      chargeItems: false,
    });
  });
  it("the Cashier is offered all of them", () => {
    assert.deepEqual(ihrAccountActions(CASHIER), {
      invoice: true,
      payment: true,
      account: true,
      chargeItems: true,
    });
  });
  it("CARE's roles keep CARE's page; pending and failed offer none", () => {
    assert.deepEqual(ihrAccountActions(CARE_ROLE), {
      invoice: true,
      payment: true,
      account: true,
      chargeItems: true,
    });
    for (const access of [PENDING, FAILED]) {
      assert.deepEqual(ihrAccountActions(access), {
        invoice: false,
        payment: false,
        account: false,
        chargeItems: false,
      });
    }
  });
  it("each flag is exactly the grant behind the request", () => {
    for (const role of ["Receptionist", "Cashier", "TPA Desk"]) {
      const actions = ihrAccountActions(forRole(role));
      const held = SLUGS.grants[role];
      assert.equal(actions.invoice, held.includes("can_write_invoice"), role);
      assert.equal(
        actions.payment,
        held.includes("can_write_payment_reconciliation"),
        role,
      );
      assert.equal(actions.account, held.includes("can_update_account"), role);
      assert.equal(
        actions.chargeItems,
        held.includes("can_update_charge_item"),
        role,
      );
    }
  });
});

describe("visit status page", () => {
  it("shows the bed, the stays and the account link to the Cashier and TPA Desk only", () => {
    for (const access of [CASHIER, TPA]) {
      assert.deepEqual(ihrStatusBlocks(access), {
        bed: true,
        bedStays: true,
        accountLink: true,
      });
    }
    for (const access of [
      RECEPTIONIST,
      MRD,
      OWNER,
      PENDING,
      FAILED,
      CARE_ROLE,
    ]) {
      assert.deepEqual(ihrStatusBlocks(access), {
        bed: false,
        bedStays: false,
        accountLink: false,
      });
    }
  });

  it("finds the visit's account by the encounter filter", () => {
    assert.deepEqual(ihrAccountOfVisitQuery("e1"), {
      encounter: "e1",
      limit: 1,
    });
  });
});

describe("pending: nothing clinical is shown or asked (design §13.6)", () => {
  it("every rule answers nothing for a pending view and a pending access", () => {
    for (const access of [PENDING, FAILED]) {
      const v = view(access);
      assert.equal(v, "pending");
      assert.deepEqual(ihrPatientTabs(CARE_TABS, ALL_TABS, v, access), []);
      assert.deepEqual(
        ihrDemographySections(
          [{ id: "general-info", details: [] }],
          v,
          access,
          true,
          "x",
        ),
        [],
      );
      assert.equal(ihrCanListEncounters(true, v, access), false);
      assert.equal(ihrShowsViewAccounts(v, access), false);
      assert.equal(ihrEncounterHistoryRule(true, v, access), "pending");
      assert.equal(ihrEncounterListEnabled(access), false);
      assert.equal(ihrAppointmentsFacilityId(v, "f"), undefined);
      assert.equal(ihrHidesPatientTags(v), true);
      assert.equal(ihrShowsEncounterTags(access), false);
      assert.equal(ihrCanAddChargeItems(true, access), false);
      assert.equal(ihrAccountActions(access).invoice, false);
      assert.equal(ihrStatusBlocks(access).bed, false);
      assert.equal(ihrCanOpenEncounter(false, access), false);
    }
  });
});

describe("the appointment print page stays refused (proposed Q-K55)", () => {
  it("is not part of the demographics view: it also asks for charge items, which the front desk may not read", () => {
    // AppointmentPrint.tsx asks GET …/charge_item/ (and invoices) with no role check; the
    // Receptionist holds no can_read_charge_item (role_matrix.txt: charge_item list is
    // billing and CARE only), so the page would meet a 403 on every print
    assert.equal(
      routeGateOf(APPOINTMENT_ID.replace(/\/$/, "/print")),
      "patientClinical",
    );
    assert.equal(routeGateOf(APPOINTMENT_ID), "patient");
    assert.equal(
      SLUGS.grants["Receptionist"].includes("can_read_charge_item"),
      false,
    );
  });
});

describe("the Booking card (P1-T24c)", () => {
  it("shows only when a booked time or a slot time is there", () => {
    assert.equal(ihrShowBookingCard(null), false);
    assert.equal(ihrShowBookingCard(undefined), false);
    assert.equal(ihrShowBookingCard({}), false);
    assert.equal(ihrShowBookingCard({ token_slot: {} }), false);
    assert.equal(
      ihrShowBookingCard({ booked_on: "2026-11-20T04:00:00Z" }),
      true,
    );
    assert.equal(
      ihrShowBookingCard({
        token_slot: { start_datetime: "2026-11-20T04:00:00Z" },
      }),
      true,
    );
  });
});
