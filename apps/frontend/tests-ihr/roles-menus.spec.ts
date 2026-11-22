// IndieHR P1-T11c1 (docs/reviews/p1-t11/design-c.md §3.1, §9.1, §13.2): the web app's sidebar for
// each of the 13 seeded logins (our eight roles, CARE's Doctor, Nurse and Facility Admin, the
// seeded hospital-wide admin, and the seeded facility-only admin).
//
// - The sidebar's link names equal the expected list EXACTLY. CARE's roles' lists are derived by
//   hand from facility-nav.tsx (Q-K53 A), so an upstream menu change fails here until a person
//   looks at it; the same CARE list is also pinned by menu.test.ts (care mode returns CARE's
//   links unchanged).
// - Typed addresses of pages that are not part of a role show "This page is not part of your
//   role", and the page asks the API for no patient, encounter or location data.
// - Any "ihr-routes" console line (withIhrRoutes found a route key missing) fails every test of
//   every spec (the shared newContext fixture, lib/fixtures.ts).
//
// Names are CARE's English strings (public/locale/en.json) and ours (deploy/locale/en.json).
// Lines read "Top", or "Parent > Child" for the children of an expandable item.
import type { BrowserContext, Page } from "@playwright/test";

import {
  HOSPITALS,
  type Hospital,
  expect,
  signIn,
  test,
  user,
} from "./lib/fixtures";

const NOT_FOR_ROLE = "This page is not part of your role";
const AUDIT = "Audit trail";
const PACKS = "Reference list updates";
const SOME_ID = "00000000-0000-4000-8000-000000000001";

// careConfig.encounterClasses: the build sets no REACT_ALLOWED_ENCOUNTER_CLASSES, so all six
const ENCOUNTER_LISTS = [
  "All Encounters",
  "Inpatient Encounters",
  "Ambulatory Encounters",
  "Observation Encounters",
  "Emergency Encounters",
  "Virtual Encounters",
  "Home Health Encounters",
].map((name) => `Patients > ${name}`);

const SETTINGS_ALWAYS = [
  "General",
  "Departments",
  "Locations",
  "Devices",
  "Specimen Definitions",
  "Observation Definitions",
  "Activity Definitions",
  "Billing",
  "Charge Item Definitions",
  "Healthcare Services",
  "Product Knowledge",
  "Product",
];

/** CARE's menu for a user holding every slug it gates on (Doctor, Nurse, Facility Admin) */
const CARE_FULL = [
  "Overview",
  "Appointments",
  "Queues",
  "Patients > Search patients",
  ...ENCOUNTER_LISTS,
  "Patients > Locations",
  "Services",
  "Resource",
  "Users",
  "Billing > Accounts",
  "Billing > Invoices",
  "Billing > Payments",
  ...SETTINGS_ALWAYS.map((name) => `Settings > ${name}`),
  "Settings > Token Category",
  "Settings > Patient Identifier Config",
  "Settings > Tag config",
  "Settings > Templates",
];

/**
 * The Radiologist is in CARE mode (it holds clinical slugs) but has no booking, token category,
 * patient identifier or template slug: CARE hides exactly those items.
 */
const CARE_RADIOLOGIST = [
  "Overview",
  "Patients > Search patients",
  ...ENCOUNTER_LISTS,
  "Patients > Locations",
  "Services",
  "Resource",
  "Users",
  "Billing > Accounts",
  "Billing > Invoices",
  "Billing > Payments",
  ...SETTINGS_ALWAYS.map((name) => `Settings > ${name}`),
  "Settings > Tag config",
];

const PATIENTS = ["Patients > Search patients", ...ENCOUNTER_LISTS];

interface Login {
  username: string;
  hospital: Hospital;
  menu: string[];
}

const LOGINS: Login[] = [
  {
    username: "reception.ravi",
    hospital: "alpha",
    menu: [
      "Overview",
      "Appointments",
      "Queues",
      ...PATIENTS,
      "Users",
      "Billing > Accounts",
    ],
  },
  {
    username: "cashier.kavya",
    hospital: "alpha",
    menu: [
      "Overview",
      ...PATIENTS,
      "Users",
      "Billing > Accounts",
      "Billing > Invoices",
      "Billing > Payments",
    ],
  },
  {
    username: "tpa.joseph",
    hospital: "alpha",
    menu: [
      "Overview",
      ...PATIENTS,
      "Users",
      "Billing > Accounts",
      "Billing > Invoices",
      "Billing > Payments",
    ],
  },
  {
    username: "mrd.farah",
    hospital: "alpha",
    menu: ["Overview", ...PATIENTS, "Users", "Records access"],
  },
  {
    username: "owner.vikram",
    hospital: "alpha",
    menu: ["Overview", "Users", "Hospital totals"],
  },
  {
    username: "lab.imran",
    hospital: "alpha",
    menu: ["Overview", "Users", "My places"],
  },
  {
    username: "store.gopal",
    hospital: "alpha",
    menu: ["Overview", "Users", "My places"],
  },
  { username: "radiology.sunita", hospital: "alpha", menu: CARE_RADIOLOGIST },
  { username: "dr.meera", hospital: "alpha", menu: CARE_FULL },
  { username: "nurse.asha", hospital: "alpha", menu: CARE_FULL },
  // Hospital-wide admins (an admin role on the hospital's root, P1-T25) also get the audit trail,
  // then the reference list updates as the last item (P1-T24a, P1-T24c, Q-K110 A, Q-K111 A).
  // admin.priya is the seeded one on alpha (Q-K117). admin.bhavesh (beta) is not since P1-T25:
  // CARE's fixture role "Facility Admin" on a top-level organisation of its own no longer counts
  // (Q-K118's intent), so he gets CARE's full menu only.
  { username: "admin.bhavesh", hospital: "beta", menu: CARE_FULL },
  {
    username: "admin.priya",
    hospital: "alpha",
    menu: [...CARE_FULL, AUDIT, PACKS],
  },
  // Facility Admin at the facility root only (Q-K118 A): CARE's full menu, but NOT hospital-wide,
  // so no audit trail (and no sign-in reset: staff-access.spec.ts)
  { username: "admin.anil", hospital: "alpha", menu: CARE_FULL },
];

const HOSPITAL_ADMINS = new Set(["admin.priya"]);

/** Location menus after "My places" (design §13.5) */
const PLACES: Record<string, { place: string; menu: string[] }> = {
  "lab.imran": {
    place: "Sample Collection Room",
    menu: ["Laboratory > Service Requests", "Inventory > Items"],
  },
  "store.gopal": {
    place: "Main Store",
    menu: [
      "Inventory > Items",
      "Inventory > To Receive",
      "Inventory > To Dispatch",
      "Inventory > Purchase Orders",
      "Inventory > Purchase Deliveries",
    ],
  },
};

// ---------------------------------------------------------------------------------------------

/** The sidebar opens expanded (CARE keeps this in a cookie): its links are then in the page. */
async function openSidebarByDefault(context: BrowserContext, origin: string) {
  await context.addCookies([
    { name: "sidebar:state", value: "true", url: origin },
  ]);
}

async function facilityIdOf(page: Page): Promise<string> {
  const id = await page.evaluate(async () => {
    const res = await fetch("/api/v1/users/getcurrentuser/", {
      headers: { "X-IHR-CSRF": "1" },
      credentials: "same-origin",
    });
    const body = (await res.json()) as { facilities?: { id: string }[] };
    return body.facilities?.[0]?.id ?? "";
  });
  expect(id, "the user's facility").not.toEqual("");
  return id;
}

/**
 * Opens every closed expandable item of the sidebar (idempotent). CARE's items are Radix
 * collapsibles: the trigger is a button with aria-expanded; a plain link is an anchor. (The
 * data-sidebar attributes of links do not reach the DOM, so anchors are found structurally.)
 */
async function expandSidebar(page: Page) {
  await page.evaluate(() => {
    document
      .querySelectorAll<HTMLButtonElement>(
        '[data-sidebar="content"] li[data-sidebar="menu-item"] > button[aria-expanded="false"]',
      )
      .forEach((button) => button.click());
  });
}

/** The sidebar's links as drawn now: "Top" or "Parent > Child". */
async function readSidebar(page: Page): Promise<string[]> {
  await expandSidebar(page);
  await page.waitForTimeout(150);
  return page.evaluate(() => {
    const text = (element: Element | null) =>
      (element?.textContent ?? "").replace(/\s+/g, " ").trim();
    const lines: string[] = [];
    const items = document.querySelectorAll(
      '[data-sidebar="content"] ul[data-sidebar="menu"] > li[data-sidebar="menu-item"]',
    );
    items.forEach((item) => {
      const head = item.querySelector(":scope > a, :scope > button");
      const label = text(head?.querySelector("span") ?? head);
      const children = item.querySelectorAll('[data-sidebar="menu-sub"] a');
      if (children.length === 0) {
        lines.push(label);
      } else {
        children.forEach((child) => lines.push(`${label} > ${text(child)}`));
      }
    });
    return lines;
  });
}

async function expectSidebar(page: Page, expected: string[]) {
  // CARE's own links show for a moment while the facility and my-access load (pending), so the
  // list is polled until it settles; it must then equal the expected one exactly
  await expect
    .poll(() => readSidebar(page), { timeout: 30_000, intervals: [500] })
    .toEqual(expected);
}

/** Requests the page makes from now on to the API paths matching `pattern`. */
function collectApiRequests(page: Page, pattern: RegExp): string[] {
  const seen: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/v1/") && pattern.test(url.pathname)) {
      seen.push(`${request.method()} ${url.pathname}`);
    }
  });
  return seen;
}

async function expectNotForRole(page: Page) {
  await expect(page.getByText(NOT_FOR_ROLE).first()).toBeVisible();
}

// ---------------------------------------------------------------------------------------------

for (const login of LOGINS) {
  test(`sidebar and typed addresses for ${login.username} (${login.hospital})`, async ({
    newContext,
  }) => {
    const origin = HOSPITALS[login.hospital];
    const context = await newContext();
    await openSidebarByDefault(context, origin);
    const page = await context.newPage();
    await signIn(page, login.hospital, user(login.username));

    const facilityId = await facilityIdOf(page);
    await page.goto(`${origin}/facility/${facilityId}/overview`);
    await expectSidebar(page, login.menu);

    // The overview's Services shortcut follows the Services menu item
    const servicesShortcut = page.locator('main a[href$="/services"]');
    if (login.menu.includes("Services")) {
      await expect(servicesShortcut.first()).toBeVisible();
    } else {
      await expect(servicesShortcut).toHaveCount(0);
    }

    if (!HOSPITAL_ADMINS.has(login.username)) {
      // The audit trail and the reference list updates are not part of any other role: the typed
      // address says so, and the page asks its API for nothing (P1-T24a, P1-T24c)
      const adminCalls: string[] = [];
      page.on("request", (request) => {
        const url = new URL(request.url());
        if (
          url.pathname.startsWith("/api/ihr/audit/") ||
          url.pathname.startsWith("/api/ihr/packs/")
        ) {
          adminCalls.push(`${request.method()} ${url.pathname}`);
        }
      });
      for (const address of ["audit", "packs"]) {
        await page.goto(`${origin}/facility/${facilityId}/ihr/${address}`);
        await expectNotForRole(page);
      }
      expect(adminCalls, "requests for the admin pages").toEqual([]);
    }

    if (login.username === "reception.ravi") {
      // The bed board shows occupants: refused, with no request for them
      const asked = collectApiRequests(page, /encounter|location/);
      await page.goto(`${origin}/facility/${facilityId}/encounters/locations`);
      await expectNotForRole(page);
      await page.goto(
        `${origin}/facility/${facilityId}/encounters/locations/${SOME_ID}`,
      );
      await expectNotForRole(page);
      // The encounter's clinical sub-pages are not the status page (C1-1)
      const visit = `${origin}/facility/${facilityId}/patient/${SOME_ID}/encounter/${SOME_ID}`;
      for (const sub of [
        "questionnaire",
        `report/${SOME_ID}`,
        `consents/${SOME_ID}`,
      ]) {
        await page.goto(`${visit}/${sub}`);
        await expectNotForRole(page);
      }
      // Clinical patient pages are not part of the demographics view (C1-2)
      const patient = `${origin}/facility/${facilityId}/patient/${SOME_ID}`;
      for (const sub of [
        "history/allergies",
        "notes",
        `diagnostic_reports/${SOME_ID}`,
      ]) {
        await page.goto(`${patient}/${sub}`);
        await expectNotForRole(page);
      }
      // Our pages of other roles
      await page.goto(`${origin}/facility/${facilityId}/ihr/totals`);
      await expectNotForRole(page);
      await page.goto(`${origin}/facility/${facilityId}/ihr/records`);
      await expectNotForRole(page);
      expect(asked, "requests for encounters or locations").toEqual([]);
    }

    if (login.username === "lab.imran" || login.username === "owner.vikram") {
      // No patient screens for these roles: a typed visit address is refused before any
      // patient or visit is requested
      const asked = collectApiRequests(page, /patient|encounter/);
      await page.goto(
        `${origin}/facility/${facilityId}/patient/${SOME_ID}/encounter/${SOME_ID}/updates`,
      );
      await expectNotForRole(page);
      await page.goto(`${origin}/facility/${facilityId}/patients`);
      await expectNotForRole(page);
      // The visit's other pages too: CARE's would ask for the patient and the visit (C1-1)
      const visit = `${origin}/facility/${facilityId}/patient/${SOME_ID}/encounter/${SOME_ID}`;
      for (const sub of [
        "questionnaire",
        `questionnaire_response/${SOME_ID}`,
        "prescriptions/print",
      ]) {
        await page.goto(`${visit}/${sub}`);
        await expectNotForRole(page);
      }
      // Encounter lists are not part of these roles (C1-5)
      await page.goto(
        `${origin}/facility/${facilityId}/encounters/patients/all`,
      );
      await expectNotForRole(page);
      expect(asked, "requests for patients or visits").toEqual([]);
    }

    const places = PLACES[login.username];
    if (places) {
      await page.goto(`${origin}/facility/${facilityId}/overview`);
      await expectSidebar(page, login.menu);
      await page.getByRole("link", { name: "My places" }).first().click();
      await expect(page.locator('[data-ihr="my-places"]')).toBeVisible();
      await page.getByRole("link", { name: places.place }).click();
      await page.waitForURL(/\/locations\/[0-9a-f-]+\//);
      await expectSidebar(page, places.menu);
    }
  });
}
