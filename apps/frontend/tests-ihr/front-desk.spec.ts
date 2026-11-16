// IndieHR P1-T11c2 (docs/reviews/p1-t11/design-c.md §3.1, §3.3, §4 c2, §9.1, §13.6): the
// Receptionist's screens, signed in as `reception.ravi` (alpha).
//
//   search by phone -> patient home -> patient page with exactly Demography, Appointments,
//   Encounters, Accounts -> edit the phone (saved, and shown) -> create a visit and land on its
//   status page -> the encounter list with refused filters planted in the browser session.
//
// - Dead-end listener (lib/deadEnds.ts): no /api/ answer 400, 403, 404 or 5xx on any step; a run
//   that fails lists every undeclared answer (method, path, status).
// - Label absence (design §3.3): what the backend projects away is never drawn as empty, so the
//   words Blood Group, Tags, Department, Care Team, Location and Bed appear on none of these
//   screens (the create form's department picker is the one place a department is chosen).
// - The patient is the demo seed's inpatient (ihr_seed_demo: an account whose visit is an
//   inpatient stay in a bed).
//
// Names are CARE's English strings (public/locale/en.json) and ours (deploy/locale/en.json).
import type { Page } from "@playwright/test";

import { watchDeadEnds } from "./lib/deadEnds";
import type { SeededStay } from "./lib/desk";
import { facilityIdOf, findSeededStay, openSidebarByDefault } from "./lib/desk";
import { HOSPITALS, expect, signIn, test, user } from "./lib/fixtures";

/** Words of projected fields: none may appear as a label (design §3.3). */
const PROJECTED_LABELS = [
  /^blood group$/i,
  /^patient tags$/i,
  /^tags$/i,
  /^department$/i,
  /^care team$/i,
  /^location$/i,
  /^bed$/i,
  /^current bed$/i,
];

async function expectNoProjectedLabels(page: Page, where: string) {
  for (const label of PROJECTED_LABELS) {
    await expect(
      page.getByText(label),
      `${where}: a "${label.source}" label`,
    ).toHaveCount(0);
  }
  await expect(page.getByText("No tags assigned")).toHaveCount(0);
}

function homeUrl(base: string, stay: SeededStay, extra = "") {
  const query = new URLSearchParams({
    phone_number: stay.phone,
    year_of_birth: String(stay.yearOfBirth ?? ""),
    partial_id: stay.patientId.slice(0, 5),
  });
  return `${base}/patients/home?${query.toString()}${extra}`;
}

/** +91 and ten digits starting with 9: a valid mobile number, different on every run. */
function newPhone(): string {
  const digits = String(Math.floor(Math.random() * 1e9)).padStart(9, "0");
  return `+919${digits}`;
}

test("the Receptionist's front-desk screens (reception.ravi, alpha)", async ({
  newContext,
}) => {
  const origin = HOSPITALS.alpha;
  const context = await newContext();
  await openSidebarByDefault(context, origin);
  const page = await context.newPage();
  await signIn(page, "alpha", user("reception.ravi"));

  const facilityId = await facilityIdOf(page);
  const stay = await findSeededStay(page, facilityId, { bedVisible: false });
  expect(stay.yearOfBirth, "the patient's year of birth").not.toBeNull();
  const base = `${origin}/facility/${facilityId}`;

  // From here on every /api/ answer is watched
  const dead = watchDeadEnds(page);

  await test.step("search by phone", async () => {
    const searched = page.waitForResponse(
      (response) =>
        response.url().includes("/api/v1/patient/search/") &&
        response.request().method() === "POST",
    );
    await page.goto(
      `${base}/patients?phone_number=${encodeURIComponent(stay.phone)}`,
    );
    expect((await searched).status()).toBe(200);
    dead.expectNone("on patient search");
  });

  await test.step("patient home: the front desk's actions, no tag card", async () => {
    await page.goto(homeUrl(base, stay));
    await expect(page.getByText(stay.patientName).first()).toBeVisible();
    await expect(
      page.getByRole("button", { name: /Create Encounter/ }).first(),
    ).toBeVisible();
    await expect(page.getByText(/View Accounts/).first()).toBeVisible();
    await expect(page.getByText(/Add tags|Manage tags/)).toHaveCount(0);
    await expectNoProjectedLabels(page, "patient home");
    // the encounter tab lists the visit; "View Encounter" opens the status page
    const view = page.getByRole("link", { name: "View Encounter" }).first();
    await expect(view).toBeVisible();
    await view.click();
    await expect(page.locator('[data-ihr="encounter-status"]')).toBeVisible();
    await expect(page.locator('[data-ihr="visit-bed"]')).toHaveCount(0);
    await expectNoProjectedLabels(page, "the status page");
    dead.expectNone("on patient home and the status page");
  });

  await test.step("patient page: exactly four tabs", async () => {
    await page.goto(`${base}/patient/${stay.patientId}/demography`);
    const tabs = page.getByRole("tablist").getByRole("tab");
    await expect(tabs.first()).toBeVisible();
    const names = (await tabs.allInnerTexts()).map((text) =>
      text.replace(/\s+/g, " ").trim(),
    );
    expect(names).toEqual([
      "Demography",
      "Appointments",
      "Encounters",
      "Accounts",
    ]);

    // Demography: identity, contact, address; never blood group, tags or plugin data
    await expect(page.getByText(stay.patientName).first()).toBeVisible();
    await expect(page.getByText("General Info").first()).toBeVisible();
    await expect(page.getByText("Identifiers").first()).toBeVisible();
    await expect(page.getByText("Additional Details")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Edit" })).toHaveCount(1);
    await expectNoProjectedLabels(page, "Demography");

    // the other three tabs open without a dead end
    await page.getByRole("tab", { name: "Appointments" }).click();
    await expect(page).toHaveURL(/\/appointments$/);
    await page.getByRole("tab", { name: "Encounters" }).click();
    await expect(page).toHaveURL(/\/encounters$/);
    await expect(
      page.getByRole("link", { name: "View Encounter" }).first(),
    ).toBeVisible();
    await expectNoProjectedLabels(page, "the Encounters tab");
    await page.getByRole("tab", { name: "Accounts" }).click();
    await expect(page).toHaveURL(/\/accounts$/);
    await page.waitForLoadState("networkidle");
    dead.expectNone("on the patient page's tabs");
  });

  await test.step("edit the phone: saved (200) and shown", async () => {
    const phone = newPhone();
    await page.goto(`${base}/patient/${stay.patientId}/demography`);
    await page.getByRole("button", { name: "Edit" }).click();
    await page.waitForURL(/\/update\?section=general-info/);
    // the edit form has no blood group or plugin fields in this view
    await expect(page.getByText(/^blood group$/i)).toHaveCount(0);
    // nor the death record: the backend answers 400 when the date of death changes
    await expect(page.getByText(/^mark as deceased$/i)).toHaveCount(0);
    await expect(page.getByText(/date and time of death/i)).toHaveCount(0);
    const phoneInput = page.locator('input[type="tel"]').first();
    await phoneInput.fill(phone);
    const saved = page.waitForResponse(
      (response) =>
        response.url().includes(`/api/v1/patient/${stay.patientId}/`) &&
        ["PUT", "PATCH"].includes(response.request().method()),
    );
    await page.getByRole("button", { name: /^Update/ }).click();
    expect((await saved).status()).toBe(200);
    // the patient now answers to the new number: the later steps (patient home searches by
    // phone) must use it, or CARE's search finds nobody and answers 403
    stay.phone = phone;
    await page.goto(`${base}/patient/${stay.patientId}/demography`);
    await expect
      .poll(async () =>
        ((await page.locator("main").innerText()) ?? "").replace(/\s/g, ""),
      )
      .toContain(phone);
    dead.expectNone("while editing the phone");
  });

  await test.step("create a visit and land on its status page", async () => {
    await page.goto(homeUrl(base, stay, "&action=create_encounter"));
    const sheet = page.getByRole("dialog");
    await expect(sheet).toBeVisible();
    // the tag picker is not offered (tags are projected away)
    await expect(sheet.getByText(/^Tags$/)).toHaveCount(0);
    const submit = sheet.getByRole("button", { name: /^Create Encounter/ });
    if (await submit.isDisabled()) {
      // pick a department (a preferred one may already be chosen)
      await sheet.getByRole("tab", { name: "All Organizations" }).click();
      await sheet.getByRole("combobox").click();
      const option = page.getByRole("option").first();
      await option.waitFor();
      await option.click();
      const confirm = page.getByRole("button", { name: "Confirm" });
      if (await confirm.isVisible()) await confirm.click();
      await expect(submit).toBeEnabled();
    }
    const created = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/encounter/" &&
        response.request().method() === "POST",
    );
    await submit.click();
    expect([200, 201]).toContain((await created).status());
    await page.waitForURL(/\/encounter\/[0-9a-f-]+\/updates$/);
    await expect(page.locator('[data-ihr="encounter-status"]')).toBeVisible();
    await expect(page.locator('[data-ihr="visit-status"]')).toBeVisible();
    await expectNoProjectedLabels(page, "the new visit's status page");
    dead.expectNone("while creating a visit");
  });

  await test.step("encounter list: refused filters in the session are never sent", async () => {
    // A filter saved by CARE's list in this browser session (F18): the department, a doctor and
    // tags would be answered with 400; the list must not send them
    await page.goto(`${base}/overview`);
    await page.evaluate(() =>
      sessionStorage.setItem(
        "encounter_list_filters",
        JSON.stringify({
          selectedOrg: { id: "00000000-0000-4000-8000-0000000000aa" },
          selectedCareTeamMember: { username: "someone" },
          selectedTags: [{ id: "00000000-0000-4000-8000-0000000000bb" }],
          tagsBehavior: "all",
        }),
      ),
    );
    const asked: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname === "/api/v1/encounter/") asked.push(url.search);
    });
    await page.goto(`${base}/encounters/patients/all`);
    await expect(page.getByText("Encounters").first()).toBeVisible();
    await page.waitForLoadState("networkidle");
    expect(asked.length, "the list asked for encounters").toBeGreaterThan(0);
    for (const search of asked) {
      for (const refused of [
        "organization=",
        "care_team_user=",
        "tags=",
        "tags_behavior=",
      ]) {
        expect(search, "a refused filter in the request").not.toContain(
          refused,
        );
      }
    }
    // the department, care team and tag filters are not offered
    await expect(
      page.getByRole("button", { name: /Department|Care Team|Tags/ }),
    ).toHaveCount(0);
    dead.expectNone("on the encounter list");
  });

  await test.step("the account page shows its header, no tab", async () => {
    await page.goto(`${base}/billing/account/${stay.accountId}`);
    await expect(page.getByText("Amount Due").first()).toBeVisible();
    await expect(
      page.getByRole("tab", { name: /Invoices|Reports|Payments/ }),
    ).toHaveCount(0);
    // the Receptionist holds can_read_account only: no write button on the page, and no
    // request behind one (Settle & Close lists charge items, which she cannot read)
    for (const name of [
      /Create Invoice/,
      /Advance\/Receipt/,
      /Settle & Close/,
      /^(Add|Manage) Tags$/,
      /^Edit/,
    ]) {
      await expect(
        page.getByRole("button", { name }),
        `a button ${name.source} on the account page`,
      ).toHaveCount(0);
    }
    dead.expectNone("on the account page");
  });
});
