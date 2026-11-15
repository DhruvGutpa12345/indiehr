import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

import {
  type IhrAccessInput,
  type IhrSlugSets,
  type MyAccess,
  ihrAccess,
  ihrPatientView,
  myAccessFrom,
} from "./access";

// CI runs this file from apps/frontend (see .github/workflows/apps.yml).
const SLUGS = JSON.parse(
  readFileSync(
    join(process.cwd(), "src", "Utils", "ihr", "ihrSlugs.json"),
    "utf-8",
  ),
) as IhrSlugSets & { restricted: string[]; bedForBilling: string[] };

const CARE: MyAccess = { restricted: false, bills_here: false };
const RESTRICTED: MyAccess = { restricted: true, bills_here: false };

function forRole(role: string, extra: string[] = []) {
  const perms = [...SLUGS.grants[role], ...extra];
  const restricted = SLUGS.restricted.includes(role);
  const myAccess: MyAccess = {
    restricted,
    bills_here: SLUGS.bedForBilling.includes(role),
  };
  const input: IhrAccessInput = {
    facilityPerms: perms,
    myAccess,
    isSuperuser: false,
  };
  return ihrAccess(input, SLUGS);
}

describe("ihrAccess: mode (P1-T11c1)", () => {
  it("is pending until the facility and my-access have both answered", () => {
    for (const input of [
      { facilityPerms: undefined, myAccess: undefined },
      { facilityPerms: [], myAccess: undefined },
      { facilityPerms: undefined, myAccess: RESTRICTED },
    ]) {
      const a = ihrAccess({ ...input, isSuperuser: false }, SLUGS);
      assert.equal(a.mode, "pending");
      assert.equal(a.records, false);
      assert.equal(a.has("can_read_account"), false);
    }
  });

  it("takes the mode from my-access, not from the slugs", () => {
    // A Receptionist who also holds a clinical role is not restricted (the backend decides)
    const a = ihrAccess(
      {
        facilityPerms: [
          ...SLUGS.grants["Receptionist"],
          "can_view_clinical_data",
        ],
        myAccess: CARE,
        isSuperuser: false,
      },
      SLUGS,
    );
    assert.equal(a.mode, "care");
    assert.equal(a.demographics, true);
    // ... and a restricted answer wins even for an empty slug list
    assert.equal(
      ihrAccess(
        { facilityPerms: [], myAccess: RESTRICTED, isSuperuser: false },
        SLUGS,
      ).mode,
      "restricted",
    );
  });

  it("is pending (fails closed), never care, when my-access failed with nothing cached", () => {
    const a = ihrAccess(
      {
        facilityPerms: SLUGS.grants["Owner"],
        myAccess: null,
        isSuperuser: false,
      },
      SLUGS,
    );
    assert.equal(a.mode, "pending");
    assert.equal(a.billingBed, false);
    assert.equal(a.totals, false);
  });

  it("never puts a superuser in a mode or shows them our items", () => {
    const a = ihrAccess(
      {
        facilityPerms: Object.values(SLUGS.grants).flat(),
        myAccess: RESTRICTED,
        isSuperuser: true,
      },
      SLUGS,
    );
    assert.equal(a.mode, "care");
    for (const flag of [
      a.demographics,
      a.editDemographics,
      a.encounterStatus,
      a.records,
      a.totals,
      a.billingBed,
      a.has("ihr_view_hospital_totals"),
    ]) {
      assert.equal(flag, false);
    }
    // not even pending while the data is still loading
    assert.equal(
      ihrAccess(
        { facilityPerms: undefined, myAccess: undefined, isSuperuser: true },
        SLUGS,
      ).mode,
      "care",
    );
  });
});

describe("ihrAccess: items per role (design §3.1)", () => {
  const expected: Record<
    string,
    {
      mode: string;
      demographics?: boolean;
      editDemographics?: boolean;
      encounterStatus?: boolean;
      records?: boolean;
      totals?: boolean;
      billingBed?: boolean;
    }
  > = {
    Receptionist: {
      mode: "restricted",
      demographics: true,
      editDemographics: true,
      encounterStatus: true,
    },
    Cashier: {
      mode: "restricted",
      demographics: true,
      encounterStatus: true,
      billingBed: true,
    },
    "TPA Desk": {
      mode: "restricted",
      demographics: true,
      encounterStatus: true,
      billingBed: true,
    },
    MRD: {
      mode: "restricted",
      demographics: true,
      encounterStatus: true,
      records: true,
    },
    Owner: { mode: "restricted", totals: true },
    "Lab Technician": { mode: "restricted" },
    "Store Keeper": { mode: "restricted" },
    Radiologist: { mode: "care" },
  };

  for (const [role, want] of Object.entries(expected)) {
    it(`${role}`, () => {
      const a = forRole(role);
      assert.equal(a.mode, want.mode);
      assert.equal(a.demographics, want.demographics ?? false);
      assert.equal(a.editDemographics, want.editDemographics ?? false);
      assert.equal(a.encounterStatus, want.encounterStatus ?? false);
      assert.equal(a.records, want.records ?? false);
      assert.equal(a.totals, want.totals ?? false);
      assert.equal(a.billingBed, want.billingBed ?? false);
    });
  }

  it("covers every role in the slug file except the hidden one", () => {
    const names = Object.keys(SLUGS.grants).filter(
      (n) => n !== "MRD Records Access",
    );
    assert.deepEqual(names.sort(), Object.keys(expected).sort());
  });

  it("keeps our items for a doctor-owner (care mode, holds our slug)", () => {
    const a = forRole("Owner", ["can_view_clinical_data"]);
    // the file says Owner is restricted; the backend would say care for this mix
    const b = ihrAccess(
      {
        facilityPerms: [...SLUGS.grants["Owner"], "can_view_clinical_data"],
        myAccess: CARE,
        isSuperuser: false,
      },
      SLUGS,
    );
    assert.equal(a.totals, true);
    assert.equal(b.mode, "care");
    assert.equal(b.totals, true);
  });
});

describe("ihrPatientView", () => {
  it("is pending while the access is pending", () => {
    const a = ihrAccess(
      { facilityPerms: undefined, myAccess: undefined, isSuperuser: false },
      SLUGS,
    );
    assert.equal(ihrPatientView(a, []), "pending");
  });

  it("is CARE's for care mode whatever the patient's list holds", () => {
    const a = forRole("Radiologist");
    assert.equal(ihrPatientView(a, undefined), "care");
    assert.equal(ihrPatientView(a, []), "care");
  });

  it("shows the demographics view to the front desk, waiting for the patient", () => {
    const a = forRole("Receptionist");
    assert.equal(ihrPatientView(a, undefined), "pending");
    assert.equal(ihrPatientView(a, []), "demographics");
    // a returning patient with no open visit gives an empty list (F4)
    assert.equal(
      ihrPatientView(a, ["ihr_view_patient_demographics"]),
      "demographics",
    );
  });

  it("gives MRD CARE's screens only for a patient whose list carries the grant's slugs", () => {
    const a = forRole("MRD");
    assert.equal(
      ihrPatientView(a, ["ihr_view_patient_demographics"]),
      "demographics",
    );
    assert.equal(ihrPatientView(a, SLUGS.grants["MRD Records Access"]), "care");
    for (const slug of [...SLUGS.clinical, ...SLUGS.patientOpening]) {
      assert.equal(ihrPatientView(a, [slug]), "care", slug);
    }
  });

  it("is none for roles without patient screens, without waiting for a patient", () => {
    for (const role of ["Owner", "Lab Technician", "Store Keeper"]) {
      const a = forRole(role);
      assert.equal(ihrPatientView(a, undefined), "none", role);
      assert.equal(ihrPatientView(a, []), "none", role);
    }
  });
});

describe("myAccessFrom: the query's state (C1-3)", () => {
  it("keeps a cached answer when a refetch fails", () => {
    // TanStack v5 keeps `data` and sets `isError` after a failed refetch
    assert.equal(myAccessFrom({ data: RESTRICTED, isError: true }), RESTRICTED);
    const a = ihrAccess(
      {
        facilityPerms: SLUGS.grants["Receptionist"],
        myAccess: myAccessFrom({ data: RESTRICTED, isError: true }),
        isSuperuser: false,
      },
      SLUGS,
    );
    assert.equal(a.mode, "restricted");
  });

  it("is null (pending) on a failure with nothing cached, undefined while loading", () => {
    assert.equal(myAccessFrom({ data: undefined, isError: true }), null);
    assert.equal(myAccessFrom({ data: undefined, isError: false }), undefined);
    assert.equal(myAccessFrom({ data: CARE, isError: false }), CARE);
    for (const isError of [true, false]) {
      const a = ihrAccess(
        {
          facilityPerms: SLUGS.grants["Receptionist"],
          myAccess: myAccessFrom({ data: undefined, isError }),
          isSuperuser: false,
        },
        SLUGS,
      );
      assert.equal(a.mode, "pending");
    }
  });
});
