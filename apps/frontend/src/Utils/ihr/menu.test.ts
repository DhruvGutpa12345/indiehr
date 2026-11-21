import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

import { type IhrAccess, type IhrSlugSets, ihrAccess } from "./access";
import {
  type LinkLike,
  type OurItem,
  ihrFacilityLinks,
  ihrLocationLinks,
  ihrOurItems,
  ihrPlacePage,
  ihrShowsServices,
} from "./menu";

// CI runs this file from apps/frontend (see .github/workflows/apps.yml).
const SLUGS = JSON.parse(
  readFileSync(
    join(process.cwd(), "src", "Utils", "ihr", "ihrSlugs.json"),
    "utf-8",
  ),
) as IhrSlugSets & { restricted: string[]; bedForBilling: string[] };

const BASE = "/facility/f1";
const LOC = "/facility/f1/locations/l1";
const CLASSES = ["imp", "amb", "obsenc", "emer", "vr", "hh"];

interface Link extends LinkLike {
  children?: Link[];
}

// The same list facility-nav.tsx builds (names are the keys; icons are not needed here)
function careFacilityLinks(perms: ReadonlySet<string>): Link[] {
  const has = (s: string) => perms.has(s);
  return [
    { name: "overview", url: `${BASE}/overview` },
    {
      name: "appointments",
      url: `${BASE}/appointments`,
      visibility: has("can_list_booking"),
    },
    {
      name: "queues",
      url: `${BASE}/queues`,
      visibility: has("can_list_booking"),
    },
    {
      name: "patients",
      url: `${BASE}/patients`,
      visibility:
        has("can_write_booking") ||
        has("can_list_encounter") ||
        has("can_create_encounter"),
      children: [
        { name: "search_patients", url: `${BASE}/patients` },
        {
          name: "all_encounters",
          url: `${BASE}/encounters/patients/all`,
          visibility: CLASSES.length > 1,
        },
        ...CLASSES.map((c) => ({
          name: `class_${c}`,
          url: `${BASE}/encounters/patients/${c}`,
        })),
        { name: "locations", url: `${BASE}/encounters/locations` },
      ],
    },
    { name: "services", url: `${BASE}/services` },
    { name: "resource", url: `${BASE}/resource` },
    { name: "users", url: `${BASE}/users` },
    {
      name: "billing",
      url: `${BASE}/billing`,
      children: [
        { name: "accounts", url: `${BASE}/billing/account` },
        { name: "invoices", url: `${BASE}/billing/invoices` },
        { name: "payments", url: `${BASE}/billing/payments` },
      ],
    },
    {
      name: "settings",
      url: `${BASE}/settings/general`,
      children: [
        ...[
          "general",
          "departments",
          "locations",
          "devices",
          "specimen_definitions",
          "observation_definitions",
          "activity_definitions",
          "billing",
          "charge_item_definitions",
          "healthcare_services",
          "product_knowledge",
          "product",
        ].map((n) => ({ name: n, url: `${BASE}/settings/${n}` })),
        {
          name: "token_category",
          url: `${BASE}/settings/token_category`,
          visibility: has("can_list_token_category"),
        },
        {
          name: "patient_identifier_config",
          url: `${BASE}/settings/patient_identifier_config`,
          visibility: has("can_read_patient_identifier_config"),
        },
        { name: "tag_config", url: `${BASE}/settings/tag_config` },
        {
          name: "templates",
          url: `${BASE}/template`,
          visibility: has("can_read_template"),
        },
      ],
    },
  ];
}

function careLocationLinks(): Link[] {
  return [
    { name: "beds", url: `${LOC}/beds` },
    {
      name: "laboratory",
      url: `${LOC}/laboratory`,
      children: [{ name: "service_requests", url: `${LOC}/service_requests` }],
    },
    {
      name: "pharmacy",
      url: `${LOC}/pharmacy`,
      children: [
        { name: "prescription_queue", url: `${LOC}/medication_requests` },
        { name: "dispense", url: `${LOC}/medication_dispense` },
        { name: "medication_return", url: `${LOC}/medication_return` },
      ],
    },
    {
      name: "inventory",
      url: `${LOC}/inventory/summary`,
      children: [
        { name: "items", url: `${LOC}/inventory/summary` },
        { name: "to_receive", url: `${LOC}/inventory/internal/receive/` },
        { name: "to_dispatch", url: `${LOC}/inventory/internal/dispatch/` },
        {
          name: "purchase_orders",
          url: `${LOC}/inventory/external/orders/outgoing`,
        },
        {
          name: "purchase_deliveries",
          url: `${LOC}/inventory/external/deliveries/incoming`,
        },
      ],
    },
    { name: "schedule", url: `${LOC}/schedule` },
    { name: "appointments", url: `${LOC}/appointments` },
    { name: "queues", url: `${LOC}/queues` },
  ];
}

/** What NavMain draws: hidden links dropped, parents with their visible children. */
function shown(links: Link[]): string[] {
  const out: string[] = [];
  for (const link of links.filter((l) => l.visibility !== false)) {
    if (link.children) {
      for (const child of link.children.filter((c) => c.visibility !== false)) {
        out.push(`${link.name} > ${child.name}`);
      }
    } else {
      out.push(link.name);
    }
  }
  return out;
}

/** A parent shows as its own line when it has visible children: list those lines too. */
function topLevel(links: Link[]): string[] {
  return links.filter((l) => l.visibility !== false).map((l) => l.name);
}

const makeOurs = (item: OurItem): Link => ({
  name: `ours_${item.key}`,
  url: item.url,
});

function roleAccess(role: string, extra: string[] = []): IhrAccess {
  return ihrAccess(
    {
      facilityPerms: [...SLUGS.grants[role], ...extra],
      myAccess: {
        restricted: SLUGS.restricted.includes(role),
        bills_here: SLUGS.bedForBilling.includes(role),
        hospital_admin: false,
      },
      isSuperuser: false,
    },
    SLUGS,
  );
}

function facilityFor(role: string, extra: string[] = []): Link[] {
  const access = roleAccess(role, extra);
  const perms = new Set([...SLUGS.grants[role], ...extra]);
  return ihrFacilityLinks(careFacilityLinks(perms), access, BASE, makeOurs);
}

const ENCOUNTERS = ["all_encounters", ...CLASSES.map((c) => `class_${c}`)].map(
  (n) => `patients > ${n}`,
);

describe("facility menu: pending and care", () => {
  const pendingAccess = ihrAccess(
    { facilityPerms: undefined, myAccess: undefined, isSuperuser: false },
    SLUGS,
  );

  it("shows only Overview and Users while pending, and no location item (fails closed, §13.8)", () => {
    // every slug CARE's menu keys on: nothing beyond the two items may flash
    const all = new Set(Object.values(SLUGS.grants).flat());
    const links = careFacilityLinks(all);
    const before = structuredClone(links);
    assert.deepEqual(
      shown(ihrFacilityLinks(links, pendingAccess, BASE, makeOurs)),
      ["overview", "users"],
    );
    assert.deepEqual(links, before, "CARE's list is not changed in place");
    assert.deepEqual(
      shown(ihrLocationLinks(careLocationLinks(), pendingAccess, LOC)),
      [],
    );
    assert.deepEqual(ihrOurItems(pendingAccess, BASE), []);
  });

  it("treats a failed my-access with nothing cached as pending, not as CARE's menu (C1-3)", () => {
    const failed = ihrAccess(
      {
        facilityPerms: SLUGS.grants["Lab Technician"],
        myAccess: null,
        isSuperuser: false,
      },
      SLUGS,
    );
    assert.equal(failed.mode, "pending");
    const all = new Set(Object.values(SLUGS.grants).flat());
    assert.deepEqual(
      shown(ihrFacilityLinks(careFacilityLinks(all), failed, BASE, makeOurs)),
      ["overview", "users"],
    );
    assert.equal(ihrShowsServices(failed), false);
  });

  it("returns CARE's links deep-equal in care mode, for CARE's own roles", () => {
    // Doctor, Nurse and Facility Admin hold every slug CARE's menu keys on
    const careRole = new Set([
      "can_list_booking",
      "can_write_booking",
      "can_list_encounter",
      "can_create_encounter",
      "can_list_token_category",
      "can_read_patient_identifier_config",
      "can_read_template",
    ]);
    for (const perms of [careRole, new Set<string>()]) {
      const access = ihrAccess(
        {
          facilityPerms: [...perms],
          myAccess: {
            restricted: false,
            bills_here: false,
            hospital_admin: false,
          },
          isSuperuser: false,
        },
        SLUGS,
      );
      const links = careFacilityLinks(perms);
      const copy = structuredClone(links);
      assert.deepEqual(ihrFacilityLinks(links, access, BASE, makeOurs), copy);
      const loc = careLocationLinks();
      assert.deepEqual(ihrLocationLinks(loc, access, LOC), careLocationLinks());
    }
  });

  it("returns CARE's links unchanged for a superuser even when restricted", () => {
    const access = ihrAccess(
      {
        facilityPerms: SLUGS.grants["Owner"],
        myAccess: {
          restricted: true,
          bills_here: false,
          hospital_admin: false,
        },
        isSuperuser: true,
      },
      SLUGS,
    );
    const links = careFacilityLinks(new Set());
    assert.deepEqual(
      ihrFacilityLinks(links, access, BASE, makeOurs),
      careFacilityLinks(new Set()),
    );
  });

  it("adds only the items held in care mode, after CARE's links", () => {
    const radiologist = facilityFor("Radiologist");
    assert.ok(!topLevel(radiologist).some((n) => n.startsWith("ours_")));
    assert.ok(topLevel(radiologist).includes("settings"));
    // an MRD who is also clinical: CARE's links and Records access
    const withClinical = ihrFacilityLinks(
      careFacilityLinks(new Set()),
      ihrAccess(
        {
          facilityPerms: [...SLUGS.grants["MRD"], "can_view_clinical_data"],
          myAccess: {
            restricted: false,
            bills_here: false,
            hospital_admin: false,
          },
          isSuperuser: false,
        },
        SLUGS,
      ),
      BASE,
      makeOurs,
    );
    assert.deepEqual(withClinical.at(-1), {
      name: "ours_records",
      url: `${BASE}/ihr/records`,
    });
    assert.equal(withClinical.length, careFacilityLinks(new Set()).length + 1);
  });
});

describe("facility menu: restricted roles (design §3.1)", () => {
  it("Receptionist", () => {
    const links = facilityFor("Receptionist");
    assert.deepEqual(topLevel(links), [
      "overview",
      "appointments",
      "queues",
      "patients",
      "users",
      "billing",
    ]);
    assert.deepEqual(shown(links), [
      "overview",
      "appointments",
      "queues",
      "patients > search_patients",
      ...ENCOUNTERS,
      "users",
      "billing > accounts",
    ]);
  });

  it("Cashier and TPA Desk", () => {
    for (const role of ["Cashier", "TPA Desk"]) {
      assert.deepEqual(
        shown(facilityFor(role)),
        [
          "overview",
          "patients > search_patients",
          ...ENCOUNTERS,
          "users",
          "billing > accounts",
          "billing > invoices",
          "billing > payments",
        ],
        role,
      );
    }
  });

  it("MRD", () => {
    assert.deepEqual(shown(facilityFor("MRD")), [
      "overview",
      "patients > search_patients",
      ...ENCOUNTERS,
      "users",
      "ours_records",
    ]);
  });

  it("Owner, Lab Technician and Store Keeper", () => {
    assert.deepEqual(shown(facilityFor("Owner")), [
      "overview",
      "users",
      "ours_totals",
    ]);
    for (const role of ["Lab Technician", "Store Keeper"]) {
      assert.deepEqual(
        shown(facilityFor(role)),
        ["overview", "users", "ours_places"],
        role,
      );
    }
  });

  it("never offers the bed board, services, resource, settings or templates", () => {
    for (const role of SLUGS.restricted) {
      const flat = shown(facilityFor(role));
      for (const name of [
        "patients > locations",
        "services",
        "resource",
        "templates",
      ]) {
        assert.ok(!flat.includes(name), `${role} ${name}`);
      }
      assert.ok(!flat.some((n) => n.startsWith("settings")), role);
    }
  });

  it("points a parent at its first visible child and hides empty parents", () => {
    const owner = facilityFor("Owner");
    const patients = owner.find((l) => l.name === "patients");
    assert.equal(patients?.visibility, false);
    const cashier = facilityFor("Cashier");
    assert.equal(
      cashier.find((l) => l.name === "billing")?.url,
      `${BASE}/billing/account`,
    );
  });

  it("keeps CARE's own hiding of an item (single encounter class)", () => {
    const perms = new Set(SLUGS.grants["Receptionist"]);
    const links = careFacilityLinks(perms);
    const patients = links.find((l) => l.name === "patients");
    const all = patients?.children?.find((c) => c.name === "all_encounters");
    if (all) all.visibility = false;
    const out = ihrFacilityLinks(
      links,
      roleAccess("Receptionist"),
      BASE,
      makeOurs,
    );
    assert.ok(!shown(out).includes("patients > all_encounters"));
  });

  it("leaves a link it does not know as CARE has it (fails toward CARE)", () => {
    const links = careFacilityLinks(new Set());
    links.push({ name: "plugin", url: `${BASE}/some-plugin` });
    const out = ihrFacilityLinks(links, roleAccess("Owner"), BASE, makeOurs);
    assert.ok(shown(out).includes("plugin"));
  });

  it("the settings menu has 15 /settings/ items and Templates at /template", () => {
    const settings = careFacilityLinks(new Set()).find(
      (l) => l.name === "settings",
    );
    const urls = settings?.children?.map((c) => c.url) ?? [];
    assert.equal(
      urls.filter((u) => u.startsWith(`${BASE}/settings/`)).length,
      15,
    );
    assert.equal(urls.filter((u) => u === `${BASE}/template`).length, 1);
    assert.equal(urls.length, 16);
  });

  it("does not change the list it was given", () => {
    const perms = new Set(SLUGS.grants["Cashier"]);
    const links = careFacilityLinks(perms);
    const before = structuredClone(links);
    ihrFacilityLinks(links, roleAccess("Cashier"), BASE, makeOurs);
    assert.deepEqual(links, before);
  });
});

describe("our items and the overview shortcut", () => {
  it("offers My places only to restricted roles that can open a place", () => {
    const keys = (role: string) =>
      ihrOurItems(roleAccess(role), BASE).map((i) => i.key);
    assert.deepEqual(keys("Lab Technician"), ["places"]);
    assert.deepEqual(keys("Store Keeper"), ["places"]);
    assert.deepEqual(keys("Radiologist"), []);
    assert.deepEqual(keys("Receptionist"), []);
    assert.deepEqual(keys("Cashier"), []);
    assert.deepEqual(keys("MRD"), ["records"]);
    assert.deepEqual(keys("Owner"), ["totals"]);
  });

  it("offers the audit trail only to a hospital-wide admin in care mode (P1-T24a)", () => {
    const admin = (
      over: Partial<{ restricted: boolean; hospital_admin: boolean }>,
      isSuperuser = false,
      loading = false,
    ) =>
      ihrAccess(
        {
          facilityPerms: loading ? undefined : [],
          myAccess: {
            restricted: false,
            bills_here: false,
            hospital_admin: true,
            ...over,
          },
          isSuperuser,
        },
        SLUGS,
      );
    assert.deepEqual(
      ihrOurItems(admin({}), BASE).map((i) => i.key),
      ["audit"],
    );
    assert.equal(ihrOurItems(admin({}), BASE)[0].url, `${BASE}/ihr/audit`);
    // not for a non-admin, a restricted answer, a superuser, or while pending
    assert.deepEqual(ihrOurItems(admin({ hospital_admin: false }), BASE), []);
    assert.deepEqual(ihrOurItems(admin({ restricted: true }), BASE), []);
    assert.deepEqual(ihrOurItems(admin({}, true), BASE), []);
    assert.deepEqual(ihrOurItems(admin({}, false, true), BASE), []);
    // none of the eight roles, the Radiologist included
    for (const role of Object.keys(SLUGS.grants)) {
      assert.ok(
        !ihrOurItems(roleAccess(role), BASE).some((i) => i.key === "audit"),
        role,
      );
    }
    // and it is added after CARE's links, as the last item, with its own url
    const links = ihrFacilityLinks(
      careFacilityLinks(new Set()),
      admin({}),
      BASE,
      makeOurs,
    );
    assert.deepEqual(links.at(-1), {
      name: "ours_audit",
      url: `${BASE}/ihr/audit`,
    });
  });

  it("shows the Services shortcut only in care mode (hidden restricted and pending)", () => {
    assert.equal(ihrShowsServices(roleAccess("Receptionist")), false);
    assert.equal(ihrShowsServices(roleAccess("Radiologist")), true);
    assert.equal(
      ihrShowsServices(
        ihrAccess(
          { facilityPerms: undefined, myAccess: undefined, isSuperuser: false },
          SLUGS,
        ),
      ),
      false,
    );
  });
});

describe("location menu: Lab Technician and Store Keeper (design §13.5)", () => {
  const locLinks = (role: string) =>
    shown(
      ihrLocationLinks(careLocationLinks(), roleAccess(role), LOC) as Link[],
    );

  it("Lab Technician sees laboratory requests and inventory items", () => {
    assert.deepEqual(locLinks("Lab Technician"), [
      "laboratory > service_requests",
      "inventory > items",
    ]);
  });

  it("Store Keeper sees every inventory page", () => {
    assert.deepEqual(locLinks("Store Keeper"), [
      "inventory > items",
      "inventory > to_receive",
      "inventory > to_dispatch",
      "inventory > purchase_orders",
      "inventory > purchase_deliveries",
    ]);
  });

  it("no restricted role sees beds or pharmacy; schedules follow CARE's slugs", () => {
    for (const role of SLUGS.restricted) {
      const names = locLinks(role);
      assert.ok(!names.includes("beds"), role);
      assert.ok(!names.some((n) => n.startsWith("pharmacy")), role);
    }
    assert.deepEqual(locLinks("Receptionist"), [
      "schedule",
      "appointments",
      "queues",
    ]);
    assert.deepEqual(locLinks("Owner"), []);
  });

  it("links a place to the first page the role may open", () => {
    assert.equal(
      ihrPlacePage(roleAccess("Lab Technician")),
      "/service_requests",
    );
    assert.equal(
      ihrPlacePage(roleAccess("Store Keeper")),
      "/inventory/summary",
    );
    assert.equal(ihrPlacePage(roleAccess("Owner")), undefined);
  });

  it("returns CARE's list in care mode", () => {
    const loc = careLocationLinks();
    assert.equal(ihrLocationLinks(loc, roleAccess("Radiologist"), LOC), loc);
  });
});
